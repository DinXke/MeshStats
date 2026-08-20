"""Tests voor de /meshmoni-subsite: de PWA voor monitoring op de telefoon.

Wat hier bewaakt wordt en waarom het een eigen bestand verdient:

* **de login is de grens** -- een PWA die op het beginscherm staat voelt als
  een eigen app, maar ze praat met dezelfde server; een data-endpoint dat
  zonder sessie antwoordt is een gat dat niemand op het scherm ziet. Pagina's
  leiden om (303), data-endpoints weigeren (401) -- dat verschil is gedrag en
  geen smaak, dus het staat hier vast;
* **kanalen heten naar hun naam** -- de namen uit ``channel_names`` horen op
  elk scherm te staan en kale metricnamen op geen enkel; de subsite bouwt haar
  eigen tegels en kan dus haar eigen versie van deze fout maken;
* **uitvragen loopt langs de bestaande weg** -- dezelfde _dispatch, hetzelfde
  recht, dezelfde auditregel als de knop in de beheer-UI. Een tweede weg naar
  de radio zou zijn eigen rechtencontrole moeten onderhouden, en dat is er
  precies één te veel;
* **data draagt no-store** -- de belofte "een meting komt nooit uit een cache"
  is een header, en een header die wegvalt is onzichtbaar tot iemand naar oude
  cijfers zit te kijken.

De routefuncties worden rechtstreeks aangeroepen, zoals overal in deze suite:
er hangt geen middleware tussen die deze antwoorden verandert.
"""
import asyncio
import json

import pytest
from fastapi import HTTPException

from app import config


@pytest.fixture
def db(tmp_path, monkeypatch):
    """De db-module tegen een verse, tijdelijke database. Zelfde opzet als
    test_kanalen.py, en om dezelfde reden (Windows en de moduleverbinding)."""
    from app import db as db_module
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "test.sqlite3")
    db_module._conn = None
    yield db_module
    if db_module._conn is not None:
        db_module._conn.close()
        db_module._conn = None


def _sessie(naam="beheerder", superuser=True):
    """Een geldige sessie van een echt account; zie test_kanalen._sessie.

    Idempotent, want sommige tests halen twee keer een sessie en het account
    hoeft er maar één keer te zijn."""
    from app import auth, rbac
    from app import db as db_module
    if not db_module.qone("SELECT 1 FROM admins WHERE username=?", (naam,)):
        rbac.maak_gebruiker(naam, auth.hash_password("wachtwoord123"),
                            is_superuser=superuser)
    return auth.make_session(naam)


class _Request:
    """Het minimum dat de meshmoni-routes van een Request aanraken."""

    def __init__(self, sessie=None, csrf=None, body=None):
        from app import auth
        self.cookies = {auth.SESSION_COOKIE: sessie} if sessie else {}
        self.headers = {"x-csrf": csrf} if csrf else {}
        self.query_params = {}
        self._body = body

    async def json(self):
        if self._body is None:
            raise ValueError("geen body")
        return self._body


def _ingelogd(db, **kw):
    sessie = _sessie()
    from app import auth
    return _Request(sessie=sessie, csrf=auth.csrf_token(sessie), **kw)


def _node(db, metrics_dict=None):
    """Een sensornode met kanaalmetingen, zichtbaar of niet maakt hier niet uit:
    de subsite staat achter de login en toont ook verborgen nodes."""
    rep = db.get_or_create_repeater("aabbccddeeff", "Uptimenode")
    db.ingest(rep["id"], db.utcnow(), metrics_dict or {
        "online": True,
        "ch5_switch": 1,
        "ch5_generic": 11,
        "ch6_switch": 0,
        "ch6_generic": 12,
    }, None)
    return db.qone("SELECT * FROM repeaters WHERE id=?", (rep["id"],))


def _data(resp) -> dict:
    return json.loads(resp.body)


# --- de grens ------------------------------------------------------------------

def test_pagina_zonder_sessie_leidt_om_naar_het_inlogscherm(db):
    from app import meshmoni
    with pytest.raises(HTTPException) as e:
        meshmoni.index(_Request())
    assert e.value.status_code == 303
    assert e.value.headers["Location"] == "/admin/login"


def test_data_endpoint_zonder_sessie_geeft_401_en_geen_omleiding(db):
    """Een fetch die een 303 naar een HTML-inlogpagina volgt leest HTML als
    data; 401 laat het script zelf de weg wijzen."""
    from app import meshmoni
    for aanroep in (lambda: meshmoni.api_nodes(_Request()),
                    lambda: meshmoni.api_alerts(_Request()),
                    lambda: meshmoni.api_push_status(_Request())):
        with pytest.raises(HTTPException) as e:
            aanroep()
        assert e.value.status_code == 401


def test_schrijvende_endpoints_eisen_het_csrf_token(db):
    from app import meshmoni
    _node(db)
    req = _Request(sessie=_sessie())  # ingelogd, maar zonder token
    with pytest.raises(HTTPException) as e:
        meshmoni.api_refresh(req, 1)
    assert e.value.status_code == 403


def test_manifest_en_service_worker_bestaan_en_zijn_publiek(db):
    """Het manifest wordt soms zonder koekjes opgehaald; achter de login zou
    de PWA willekeurig niet installeren. Er staat dan ook niets in dan naam en
    iconen."""
    from app import meshmoni
    assert (meshmoni._STATIC / "manifest.webmanifest").exists()
    assert (meshmoni._STATIC / "sw.js").exists()
    manifest = json.loads((meshmoni._STATIC / "manifest.webmanifest").read_text("utf-8"))
    assert manifest["scope"] == "/meshmoni"
    for icoon in manifest["icons"]:
        naam = icoon["src"].rsplit("/", 1)[-1]
        assert (meshmoni._STATIC / naam).exists(), f"manifest wijst naar {naam} dat er niet is"


# --- de nodes ------------------------------------------------------------------

def test_nodeslijst_toont_kanaalnamen_en_nooit_kale_metricnamen(db):
    from app import meshmoni
    rep = _node(db)
    db.set_channel_name(rep["id"], 6, "google", "ms")
    data = _data(meshmoni.api_nodes(_ingelogd(db)))
    assert len(data["nodes"]) == 1
    labels = [k["label"] for k in data["nodes"][0]["channels"]]
    assert "google — meetwaarde" in labels
    # Een naamloos kanaal blijft zichtbaar, onder zijn nummer.
    assert "kanaal 5 — meetwaarde" in labels
    # En nergens een kale metricnaam als label.
    assert not any(l.startswith("ch") for l in labels)


def test_switch_leest_als_op_of_neer(db):
    from app import meshmoni
    _node(db)
    kanalen = _data(meshmoni.api_nodes(_ingelogd(db)))["nodes"][0]["channels"]
    per_metric = {k["metric"]: k for k in kanalen}
    assert per_metric["ch5_switch"]["display"] == "op"
    assert per_metric["ch6_switch"]["display"] == "neer"


def test_node_zonder_kanaalmetingen_staat_niet_in_de_lijst(db):
    from app import meshmoni
    _node(db, {"online": True, "battery_percentage": 80})
    assert _data(meshmoni.api_nodes(_ingelogd(db)))["nodes"] == []


def test_data_antwoorden_dragen_no_store(db):
    """De belofte "een meting komt nooit uit een cache" is deze header."""
    from app import meshmoni
    _node(db)
    resp = meshmoni.api_nodes(_ingelogd(db))
    assert resp.headers["Cache-Control"] == "no-store"


# --- de historiek ----------------------------------------------------------------

def _met_historiek(db):
    rep = _node(db)
    for i, v in enumerate([10.0, 12.0, 14.0, 20.0]):
        ts = f"2199-01-01T10:0{i}:00Z"  # ver in de toekomst: altijd in het venster
        db.execute("INSERT INTO samples(repeater_id, metric, ts, value) VALUES(?,?,?,?)",
                   (rep["id"], "ch6_generic", ts, v))
    return rep


def test_historiek_geeft_punten_statistiek_en_histogram(db):
    from app import meshmoni
    rep = _met_historiek(db)
    db.set_channel_name(rep["id"], 6, "google", "ms")
    # De sampletijden staan in 2199: het venster telt terug vanaf nu, en een
    # ts>=-vergelijking neemt de toekomst altijd mee. Zo test dit de echte
    # SQLite-weg (db.metric_history valt zonder TSDB daarop terug) zonder aan
    # de klok te draaien.
    data = _data(meshmoni.api_history(_ingelogd(db), rep["id"],
                                      metric="ch6_generic", hours=48))
    assert data["label"] == "google — meetwaarde"
    assert data["unit"] == "ms"
    # Vijf punten: de ingest van _node schreef er al één (12, nu), plus de
    # vier hierboven. Gemiddelde dus (12+10+12+14+20)/5.
    assert data["stats"] == {"min": 10.0, "max": 20.0, "avg": 13.6, "last": 20.0, "n": 5}
    assert sum(b["n"] for b in data["histogram"]) == 5
    assert data["histogram"][0]["lo"] == 10.0
    assert data["histogram"][-1]["hi"] == 20.0
    assert len(data["points"]) == 5


def test_historiek_weigert_een_onbruikbare_metricnaam(db):
    from app import meshmoni
    rep = _node(db)
    with pytest.raises(HTTPException) as e:
        meshmoni.api_history(_ingelogd(db), rep["id"],
                             metric='x" or "1"="1', hours=24)
    assert e.value.status_code == 400


def test_historiek_van_een_onbekende_node_is_404(db):
    from app import meshmoni
    with pytest.raises(HTTPException) as e:
        meshmoni.api_history(_ingelogd(db), 999, metric="ch6_generic", hours=24)
    assert e.value.status_code == 404


# --- het uitvragen ---------------------------------------------------------------

def test_uitvragen_loopt_langs_de_weg_van_de_beheer_ui(db, monkeypatch):
    """Zelfde _dispatch als de opvraagknop op /admin, en de uitkomst gaat
    onvertaald door naar de app."""
    from app import meshmoni, routes_admin
    rep = _node(db)
    gezien = {}

    def nep_dispatch(row, command):
        gezien["rid"], gezien["command"] = row["id"], command
        return "queued"

    monkeypatch.setattr(routes_admin, "_dispatch", nep_dispatch)
    data = _data(meshmoni.api_refresh(_ingelogd(db), rep["id"]))
    assert data["weg"] == "queued"
    assert gezien == {"rid": rep["id"], "command": "status"}
    # En er staat een regel in het audittrail, zoals bij de knop op /admin.
    rij = db.qone("SELECT * FROM audit WHERE action='node.uitvragen'")
    assert rij is not None and "meshmoni" in rij["detail"]


def test_uitvragen_zonder_recht_is_403_en_komt_in_het_audittrail(db, monkeypatch):
    from app import auth, meshmoni, routes_admin
    rep = _node(db)
    monkeypatch.setattr(routes_admin, "_dispatch",
                        lambda row, command: pytest.fail("mag hier nooit komen"))
    sessie = _sessie("lezer", superuser=False)
    req = _Request(sessie=sessie, csrf=auth.csrf_token(sessie))
    with pytest.raises(HTTPException) as e:
        meshmoni.api_refresh(req, rep["id"])
    assert e.value.status_code == 403
    rij = db.qone("SELECT * FROM audit WHERE action='node.uitvragen'")
    assert rij is not None and rij["outcome"] == "geweigerd"


# --- de alerts -------------------------------------------------------------------

def _alert(db, rep, tekst="dienst antwoordt niet", kanaal=6):
    from app import webpush
    webpush.ensure_schema()
    db.execute(
        "INSERT INTO alerts(repeater_id, channel, text, severity, ts, source) "
        "VALUES(?,?,?,?,?,?)",
        (rep["id"], kanaal, tekst, "warning", db.utcnow(), "monitor"))
    return db.qone("SELECT * FROM alerts ORDER BY id DESC LIMIT 1")


def test_alertenlijst_draagt_namen_en_geen_nummers(db):
    from app import meshmoni
    rep = _node(db)
    db.set_channel_name(rep["id"], 6, "google", "ms")
    _alert(db, rep)
    data = _data(meshmoni.api_alerts(_ingelogd(db)))
    assert data["open"] == 1
    alert = data["alerts"][0]
    assert alert["node"] == "Uptimenode"
    assert alert["channel_name"] == "google"
    # Zonder naam blijft het kanaal zichtbaar onder zijn nummer.
    _alert(db, rep, kanaal=5)
    data = _data(meshmoni.api_alerts(_ingelogd(db)))
    assert data["alerts"][0]["channel_name"] == "kanaal 5"


def test_bevestigen_haalt_een_alert_uit_de_standaardweergave_maar_wist_niets(db):
    from app import meshmoni
    rep = _node(db)
    rij = _alert(db, rep)
    req = _ingelogd(db)
    meshmoni.api_alert_ack(req, rij["id"])
    data = _data(meshmoni.api_alerts(req))
    assert data["open"] == 0 and data["alerts"] == []
    # Met all=1 blijft de geschiedenis leesbaar.
    data = _data(meshmoni.api_alerts(req, all=1))
    assert len(data["alerts"]) == 1 and data["alerts"][0]["acked"] is True


def test_bevestigen_van_een_onbekende_alert_is_404(db):
    from app import meshmoni
    with pytest.raises(HTTPException) as e:
        meshmoni.api_alert_ack(_ingelogd(db), 12345)
    assert e.value.status_code == 404


# --- de push-inschrijfflow --------------------------------------------------------

def _abonnement(endpoint="https://push.example/abc"):
    return {"endpoint": endpoint, "keys": {"p256dh": "sleutel", "auth": "geheim"}}


def test_abonneren_slaat_de_rij_op_met_de_gebruiker_erbij(db, monkeypatch):
    from app import meshmoni, webpush
    monkeypatch.setattr(webpush, "VAPID_PUBLIC", "pub")
    monkeypatch.setattr(webpush, "VAPID_PRIVATE", "priv")
    monkeypatch.setattr(webpush, "_pywebpush", lambda **kw: None)
    req = _ingelogd(db, body=_abonnement())
    data = _data(asyncio.run(meshmoni.api_push_subscribe(req)))
    assert data["subscribed"] is True
    rij = db.qone("SELECT * FROM push_subscriptions")
    assert rij["endpoint"] == "https://push.example/abc"
    assert rij["username"] == "beheerder"


def test_abonneren_terwijl_push_uitstaat_geeft_de_reden_terug(db, monkeypatch):
    """Uit is uit, met de reden erbij -- de app toont die zin, geen kaal 409."""
    from app import meshmoni, webpush
    monkeypatch.setattr(webpush, "VAPID_PUBLIC", "")
    monkeypatch.setattr(webpush, "VAPID_PRIVATE", "")
    # Bibliotheek 'aanwezig': de test gaat over de sleutels, niet de installatie.
    monkeypatch.setattr(webpush, "_pywebpush", lambda **kw: None)
    req = _ingelogd(db, body=_abonnement())
    with pytest.raises(HTTPException) as e:
        asyncio.run(meshmoni.api_push_subscribe(req))
    assert e.value.status_code == 409
    assert "MM_VAPID_PUBLIC" in e.value.detail


def test_een_onbruikbaar_abonnement_wordt_geweigerd(db, monkeypatch):
    from app import meshmoni, webpush
    monkeypatch.setattr(webpush, "VAPID_PUBLIC", "pub")
    monkeypatch.setattr(webpush, "VAPID_PRIVATE", "priv")
    monkeypatch.setattr(webpush, "_pywebpush", lambda **kw: None)
    req = _ingelogd(db, body={"endpoint": "http://geen-https.example", "keys": {}})
    with pytest.raises(HTTPException) as e:
        asyncio.run(meshmoni.api_push_subscribe(req))
    assert e.value.status_code == 400


def test_uitschrijven_verwijdert_de_rij(db, monkeypatch):
    from app import meshmoni, webpush
    monkeypatch.setattr(webpush, "VAPID_PUBLIC", "pub")
    monkeypatch.setattr(webpush, "VAPID_PRIVATE", "priv")
    monkeypatch.setattr(webpush, "_pywebpush", lambda **kw: None)
    asyncio.run(meshmoni.api_push_subscribe(_ingelogd(db, body=_abonnement())))
    data = _data(asyncio.run(meshmoni.api_push_unsubscribe(
        _ingelogd(db, body={"endpoint": "https://push.example/abc"}))))
    assert data["removed"] is True
    assert db.qone("SELECT * FROM push_subscriptions") is None


def test_pushstatus_zegt_waarom_push_uitstaat(db, monkeypatch):
    from app import meshmoni, webpush
    monkeypatch.setattr(webpush, "VAPID_PUBLIC", "")
    monkeypatch.setattr(webpush, "VAPID_PRIVATE", "")
    # Bibliotheek 'aanwezig': de test gaat over de sleutels, niet de installatie.
    monkeypatch.setattr(webpush, "_pywebpush", lambda **kw: None)
    data = _data(meshmoni.api_push_status(_ingelogd(db)))
    assert data["enabled"] is False
    assert "MM_VAPID_PUBLIC" in data["reason"]
