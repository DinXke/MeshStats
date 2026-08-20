"""Tests voor webpush: de verzendlus, het opruimen en het eerlijke uit-staan.

Wat hier het bewaken waard is:

* **uit is uit, met de reden** -- zonder VAPID-sleutels (of zonder pywebpush)
  hoort webpush uit te staan met een zin die zegt wat er ontbreekt, en de site
  hoort daar verder niets van te merken. Een pushfeature die de site laat
  crashen omdat een optionele sleutel leeg is, is erger dan geen pushfeature;
* **een alert wordt één keer aangekondigd** -- het watermerk schuift ook op
  als het versturen mislukt. Zonder die regel herhaalt de lus elke vijftien
  seconden dezelfde melding tot in de eeuwigheid, en dat merkt niemand tot de
  eerste echte storing;
* **abonnementen ruimen zichzelf op** -- 404/410 van de pushdienst betekent
  "deze telefoon is weg" en de rij hoort meteen te verdwijnen; aanhoudend
  falen ook, maar pas na een aantal pogingen, want een nacht zonder bereik is
  geen opzegging.

Er wordt nergens echt verstuurd: _pywebpush wordt vervangen door een nepper
die opschrijft wat hij kreeg. De echte bibliotheek versleutelen laten we aan
pywebpush zelf over -- dat contract test upstream al.
"""
import pytest

from app import config


@pytest.fixture
def db(tmp_path, monkeypatch):
    """Zelfde opzet als test_kanalen.py: verse database per test."""
    from app import db as db_module
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "test.sqlite3")
    db_module._conn = None
    yield db_module
    if db_module._conn is not None:
        db_module._conn.close()
        db_module._conn = None


@pytest.fixture
def aan(monkeypatch):
    """Webpush 'aan': sleutels gezet en een nepverzender die alles noteert."""
    from app import webpush
    verstuurd = []
    monkeypatch.setattr(webpush, "VAPID_PUBLIC", "pub")
    monkeypatch.setattr(webpush, "VAPID_PRIVATE", "priv")
    monkeypatch.setattr(webpush, "_pywebpush",
                        lambda **kw: verstuurd.append(kw))
    return verstuurd


def _abonnee(db, endpoint="https://push.example/a"):
    from app import webpush
    assert webpush.subscribe({"endpoint": endpoint,
                              "keys": {"p256dh": "pk", "auth": "ak"}}, "beheerder")
    return db.qone("SELECT * FROM push_subscriptions WHERE endpoint=?", (endpoint,))


def _alert(db, rep_id=None, kanaal=None, tekst="dienst antwoordt niet"):
    from app import webpush
    webpush.ensure_schema()
    db.execute(
        "INSERT INTO alerts(repeater_id, channel, text, severity, ts, source) "
        "VALUES(?,?,?,?,?,?)", (rep_id, kanaal, tekst, "warning", db.utcnow(), "test"))


# --- uit is uit, met de reden ------------------------------------------------

def test_zonder_sleutels_staat_push_uit_en_zegt_de_status_waarom(db, monkeypatch):
    from app import webpush
    monkeypatch.setattr(webpush, "VAPID_PUBLIC", "")
    monkeypatch.setattr(webpush, "VAPID_PRIVATE", "")
    # De bibliotheek 'aanwezig' maken, wat er lokaal ook geïnstalleerd staat:
    # deze test gaat over de sleutels, niet over de installatie.
    monkeypatch.setattr(webpush, "_pywebpush", lambda **kw: None)
    st = webpush.status()
    assert st["enabled"] is False
    assert "MM_VAPID_PUBLIC" in st["reason"]
    assert st["public_key"] is None


def test_zonder_pywebpush_staat_push_uit_met_die_reden(db, monkeypatch):
    from app import webpush
    monkeypatch.setattr(webpush, "VAPID_PUBLIC", "pub")
    monkeypatch.setattr(webpush, "VAPID_PRIVATE", "priv")
    monkeypatch.setattr(webpush, "_pywebpush", None)
    st = webpush.status()
    assert st["enabled"] is False
    assert "pywebpush" in st["reason"]


def test_start_zonder_sleutels_start_geen_draad(db, monkeypatch, capsys):
    from app import webpush
    monkeypatch.setattr(webpush, "VAPID_PUBLIC", "")
    monkeypatch.setattr(webpush, "VAPID_PRIVATE", "")
    monkeypatch.setattr(webpush, "_thread", None)
    webpush.start()
    assert webpush._thread is None
    # De reden staat in de opstartlog: daar kijkt een beheerder die zich
    # afvraagt waarom zijn telefoon zwijgt.
    assert "Webpush staat uit" in capsys.readouterr().out


# --- abonnementen --------------------------------------------------------------

def test_opnieuw_abonneren_vervangt_de_rij_in_plaats_van_te_verdubbelen(db):
    """Een browser die zich na een herinstallatie opnieuw aanmeldt zou anders
    elke melding dubbel krijgen."""
    from app import webpush
    _abonnee(db)
    assert webpush.subscribe({"endpoint": "https://push.example/a",
                              "keys": {"p256dh": "nieuw", "auth": "nieuw"}}, "ander")
    rijen = db.q("SELECT * FROM push_subscriptions")
    assert len(rijen) == 1
    assert rijen[0]["p256dh"] == "nieuw"


def test_een_abonnement_zonder_https_of_sleutels_wordt_geweigerd(db):
    from app import webpush
    webpush.ensure_schema()
    assert not webpush.subscribe({"endpoint": "http://push.example/a",
                                  "keys": {"p256dh": "pk", "auth": "ak"}}, "x")
    assert not webpush.subscribe({"endpoint": "https://push.example/a",
                                  "keys": {}}, "x")
    assert db.qone("SELECT * FROM push_subscriptions") is None


# --- de verzendlus --------------------------------------------------------------

def test_een_nieuwe_alert_gaat_naar_elk_abonnement(db, aan):
    from app import webpush
    _abonnee(db, "https://push.example/a")
    _abonnee(db, "https://push.example/b")
    _alert(db)
    assert webpush.ronde() == 2
    assert len(aan) == 2
    assert {kw["subscription_info"]["endpoint"] for kw in aan} == {
        "https://push.example/a", "https://push.example/b"}


def test_een_alert_wordt_maar_een_keer_aangekondigd(db, aan):
    from app import webpush
    _abonnee(db)
    _alert(db)
    assert webpush.ronde() == 1
    assert webpush.ronde() == 0, "het watermerk hoort opgeschoven te zijn"


def test_de_melding_draagt_de_nodenaam_en_de_kanaalnaam(db, aan):
    import json

    from app import webpush
    rep = db.get_or_create_repeater("aabbccddeeff", "Uptimenode")
    db.set_channel_name(rep["id"], 6, "google", "ms")
    _abonnee(db)
    _alert(db, rep_id=rep["id"], kanaal=6)
    webpush.ronde()
    payload = json.loads(aan[0]["data"])
    assert payload["title"] == "Uptimenode — google"
    assert payload["body"] == "dienst antwoordt niet"
    # Zonder naam blijft het kanaal onder zijn nummer zichtbaar.
    db.execute("DELETE FROM channel_names")
    _alert(db, rep_id=rep["id"], kanaal=6)
    webpush.ronde()
    assert json.loads(aan[-1]["data"])["title"] == "Uptimenode — kanaal 6"


def test_het_watermerk_schuift_ook_op_als_versturen_mislukt(db, monkeypatch):
    """Anders herhaalt de lus dezelfde melding elke ronde opnieuw; wie hem
    miste vindt hem in de alertenlijst, die blijft staan tot hij bevestigd is."""
    from app import webpush
    monkeypatch.setattr(webpush, "VAPID_PUBLIC", "pub")
    monkeypatch.setattr(webpush, "VAPID_PRIVATE", "priv")

    def kapot(**kw):
        raise OSError("netwerk weg")

    monkeypatch.setattr(webpush, "_pywebpush", kapot)
    _abonnee(db)
    _alert(db)
    assert webpush.ronde() == 0
    assert webpush.ronde() == 0
    rij = db.qone("SELECT failures FROM push_subscriptions")
    assert rij["failures"] == 1, "één mislukking, want de alert is niet herhaald"


def test_410_van_de_pushdienst_ruimt_het_abonnement_meteen_op(db, monkeypatch):
    from types import SimpleNamespace

    from app import webpush
    monkeypatch.setattr(webpush, "VAPID_PUBLIC", "pub")
    monkeypatch.setattr(webpush, "VAPID_PRIVATE", "priv")

    def weg(**kw):
        raise webpush.WebPushException(
            "gone", response=SimpleNamespace(status_code=410))

    monkeypatch.setattr(webpush, "_pywebpush", weg)
    _abonnee(db)
    _alert(db)
    webpush.ronde()
    assert db.qone("SELECT * FROM push_subscriptions") is None


def test_aanhoudend_falen_ruimt_het_abonnement_uiteindelijk_op(db, monkeypatch):
    from app import webpush
    monkeypatch.setattr(webpush, "VAPID_PUBLIC", "pub")
    monkeypatch.setattr(webpush, "VAPID_PRIVATE", "priv")

    def kapot(**kw):
        raise OSError("netwerk weg")

    monkeypatch.setattr(webpush, "_pywebpush", kapot)
    _abonnee(db)
    for _ in range(webpush.MAX_FAILURES):
        _alert(db)
        webpush.ronde()
    assert db.qone("SELECT * FROM push_subscriptions") is None


def test_eerste_start_begint_bij_het_heden(db, aan, monkeypatch):
    """Een verse installatie met een gevulde alerts-tabel mag de historiek niet
    alsnog naar elke telefoon duwen; start() zet het watermerk op het heden.
    De draad zelf starten we hier niet echt."""
    import threading

    from app import webpush
    _alert(db)
    _alert(db)
    gestart = []
    monkeypatch.setattr(webpush, "_thread", None)
    monkeypatch.setattr(threading, "Thread",
                        lambda **kw: SimpleThread(gestart, kw))
    webpush.start()
    assert gestart, "met sleutels hoort de draad te starten"
    _abonnee(db)
    assert webpush.ronde() == 0, "de bestaande alerts zijn geschiedenis"


class SimpleThread:
    def __init__(self, log, kw):
        self.log = log
        self.kw = kw

    def start(self):
        self.log.append(self.kw)
