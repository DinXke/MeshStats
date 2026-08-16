"""Tests voor de indeling van de beheerpagina's.

Waarom dit een eigen bestand verdient. De beheerpagina is in twee werelden
gesplitst -- handelingen op een apparaat tegenover instellingen van deze
installatie -- en drie dingen daarin kunnen stuk zonder dat er ooit een fout
verschijnt:

1. Het **beheerniveau** waarop de nodelijst gegroepeerd is. Dat is een
   waarneming, afgeleid uit vier losse gegevens, en een verkeerde afleiding
   levert geen exception op maar een node die in de verkeerde groep staat --
   met knoppen die iets beloven wat er niet is, of andersom.
2. De **omleiding** van de oude instellingen-URL. Die staat in documentatie en
   in bladwijzers; valt hij weg, dan denkt de lezer dat de knop stuk is.
3. Het **deelsgewijs opslaan** van instellingen. De velden staan sinds de
   herindeling over twee formulieren verdeeld. Zou een ontbrekend veld als
   "zet op nul" gelezen worden, dan gooit het weergaveformulier de
   bewaartermijn weg -- stil, en pas maanden later zichtbaar als een gat in
   een grafiek.

De routefuncties worden rechtstreeks aangeroepen in plaats van via een
HTTP-client: er hangt geen middleware tussen die deze antwoorden verandert, en
een testclient zou hier httpx als afhankelijkheid binnenhalen voor niets.
"""
import pytest
from starlette.requests import Request

from app import commanding, config


@pytest.fixture
def db(tmp_path, monkeypatch):
    """De db-module tegen een verse, tijdelijke database.

    Zelfde opzet als test_nodes.py: de moduleverbinding leeft op moduleniveau en
    moet per test weggegooid en na afloop gesloten worden, anders lekken tests
    in elkaar en kan Windows de tijdelijke file niet opruimen.
    """
    from app import db as db_module
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "test.sqlite3")
    db_module._conn = None
    yield db_module
    if db_module._conn is not None:
        db_module._conn.close()
        db_module._conn = None


def scope_request(path: str, query: str = "") -> Request:
    """Het kleinste ding dat een Starlette-Request is.

    Genoeg voor de routes hieronder: die lezen alleen het pad, de query en de
    koekjes. Een echte ASGI-server erbij halen zou niets extra's bewaken.
    """
    return Request({
        "type": "http", "http_version": "1.1", "method": "GET",
        "scheme": "http", "server": ("test", 80), "path": path,
        "query_string": query.encode(), "headers": [],
    })


# --- het beheerniveau --------------------------------------------------------
#
# De vier gevallen die er zijn, elk met het bewijs waarop het niveau berust.

def rep(**over) -> dict:
    base = {"pubkey_prefix": "e3d3f4d7edd0", "source_prefix": "e3d3f4d7edd0",
            "source_seen": "2026-08-16T12:00:00Z", "fw_meshmanager": "1.10.0",
            "fw": "v1.16.0", "name": "Dakrepeater"}
    base.update(over)
    return base


def route(rep_row, **kw) -> dict:
    kw.setdefault("broker_connected", True)
    return commanding.route_for(rep_row, **kw)


def test_eigen_firmware_die_zelf_publiceert_is_full_managed():
    r = route(rep())
    assert r["level"] == commanding.LEVEL_FULL
    assert "1.10.0" in r["level_why"]


def test_zonder_firmwareversie_geen_full_managed():
    """Een node zonder gemelde versie kan van alles zijn, en dus niet 'alles kan'."""
    r = route(rep(fw_meshmanager=None))
    assert r["level"] == commanding.LEVEL_UNMANAGED


def test_doorgestuurde_repeater_met_monitor_is_semi_managed():
    """De dakrepeater: publiceert zelf niet, maar zijn monitor kan hem uitvragen."""
    monitor = rep(pubkey_prefix="55d9a320a4e3", name="Thuisnode", fw_meshmanager="1.9.0")
    r = route(rep(source_prefix="55d9a320a4e3", fw_meshmanager=None), relay=monitor)
    assert r["level"] == commanding.LEVEL_SEMI
    # De reden noemt de node die het mogelijk maakt, want zonder die naam is
    # "semi-managed" een etiket waar niemand iets mee kan.
    assert "Thuisnode" in r["level_why"]


def test_monitor_met_te_oude_firmware_is_geen_semi_managed():
    monitor = rep(pubkey_prefix="55d9a320a4e3", fw_meshmanager="1.8.0")
    r = route(rep(source_prefix="55d9a320a4e3", fw_meshmanager=None), relay=monitor)
    assert r["level"] == commanding.LEVEL_UNMANAGED


def test_alleen_een_poller_maakt_semi_managed():
    """De poller logt met het repeaterwachtwoord in op dezelfde CLI.

    Hem niet meetellen zou een repeater die alleen zo binnenkomt 'unmanaged'
    noemen terwijl de opvraagknop ernaast werkt.
    """
    r = route(rep(source_prefix="api", fw_meshmanager=None),
              poller_seen="2026-08-16T12:00:00Z",
              now=commanding.datetime(2026, 8, 16, 12, 1,
                                      tzinfo=commanding.timezone.utc))
    assert r["level"] == commanding.LEVEL_SEMI
    assert "poller" in r["level_why"]


def test_niveau_hangt_niet_aan_de_brokerverbinding():
    """Een full managed node achter een weggevallen broker blijft full managed.

    Wat er nu niet kan staat in ``mqtt``; wat deze node is staat in ``level``.
    Die twee door elkaar halen zou het niveau laten meebewegen met de
    netwerkverbinding van de server in plaats van met de node.
    """
    r = route(rep(), broker_connected=False)
    assert r["level"] == commanding.LEVEL_FULL
    assert r["mqtt"] is False


def test_niveaus_staan_in_aflopende_volgorde_op_de_pagina():
    from app import routes_admin
    assert routes_admin.LEVEL_ORDER == (commanding.LEVEL_FULL,
                                        commanding.LEVEL_SEMI,
                                        commanding.LEVEL_UNMANAGED)


# --- de oude URL -------------------------------------------------------------

def test_oude_instellingen_url_leidt_om_naar_de_nodepagina(db, monkeypatch):
    from app import routes_admin
    monkeypatch.setattr(routes_admin, "require_login", lambda request: "admin")
    resp = routes_admin.repeater_settings_redirect(scope_request("/admin/repeaters/7/settings"), 7)
    assert resp.status_code == 303
    assert resp.headers["location"] == "/admin/repeaters/7"


def test_oude_url_neemt_de_melding_mee(db, monkeypatch):
    """?clock=sent hoort de omleiding te overleven.

    Anders verliest een POST die op de oude pagina uitkwam zijn uitslag onderweg
    en ziet de gebruiker een pagina zonder enige melding -- precies het gedrag
    waar de meldingsblokken tegen gebouwd zijn.
    """
    from app import routes_admin
    monkeypatch.setattr(routes_admin, "require_login", lambda request: "admin")
    resp = routes_admin.repeater_settings_redirect(
        scope_request("/admin/repeaters/7/settings", "clock=sent&wait=12"), 7)
    assert resp.headers["location"] == "/admin/repeaters/7?clock=sent&wait=12"


# --- deelsgewijs opslaan -----------------------------------------------------

def test_weergaveformulier_laat_de_bewaartermijn_staan(db, monkeypatch):
    """Het ene formulier mag de velden van het andere niet op nul zetten."""
    from app import retention, routes_admin
    monkeypatch.setattr(routes_admin, "require_login", lambda request: "admin")
    monkeypatch.setattr(routes_admin, "check_csrf", lambda request, csrf: None)
    monkeypatch.setattr(retention, "run_once", lambda *a, **k: {})
    db.set_setting("retention_days", "42")

    # Alle parameters expliciet: deze functie wordt hier zonder FastAPI
    # aangeroepen, dus een weggelaten argument houdt zijn Form()-object in
    # plaats van de None die de server erin zou zetten.
    resp = routes_admin.save_settings(scope_request("/admin/settings"), csrf="x",
                                      heartbeat_min=9, retention_days=None,
                                      history_ranges="4,24",
                                      packet_retention_days=None,
                                      packet_max_rows=None, db_max_mb=None)
    assert resp.headers["location"] == "/admin/server"
    assert db.setting_int("retention_days", 0) == 42
    assert db.setting_int("heartbeat_min", 0) == 9


def test_bewaarformulier_laat_het_puntinterval_staan(db, monkeypatch):
    from app import retention, routes_admin
    monkeypatch.setattr(routes_admin, "require_login", lambda request: "admin")
    monkeypatch.setattr(routes_admin, "check_csrf", lambda request, csrf: None)
    gedraaid = []
    monkeypatch.setattr(retention, "run_once", lambda *a, **k: gedraaid.append(True))
    db.set_setting("heartbeat_min", "7")

    routes_admin.save_settings(scope_request("/admin/settings"), csrf="x",
                               heartbeat_min=None, retention_days=30,
                               history_ranges=None, packet_retention_days=5,
                               packet_max_rows=100000, db_max_mb=64)
    assert db.setting_int("heartbeat_min", 0) == 7
    assert db.setting_int("retention_days", 0) == 30
    assert db.setting_int("packet_retention_days", 0) == 5
    # Een gewijzigde termijn hoort meteen toegepast te worden, langs dezelfde
    # weg als de uurlijkse ronde.
    assert gedraaid == [True]


def test_weergave_lokt_geen_opruimronde_uit(db, monkeypatch):
    """VACUUM is duur; het punt-interval wijzigen is geen reden ervoor."""
    from app import retention, routes_admin
    monkeypatch.setattr(routes_admin, "require_login", lambda request: "admin")
    monkeypatch.setattr(routes_admin, "check_csrf", lambda request, csrf: None)
    gedraaid = []
    monkeypatch.setattr(retention, "run_once", lambda *a, **k: gedraaid.append(True))

    routes_admin.save_settings(scope_request("/admin/settings"), csrf="x",
                               heartbeat_min=10, retention_days=None,
                               history_ranges=None, packet_retention_days=None,
                               packet_max_rows=None, db_max_mb=None)
    assert gedraaid == []


# --- geen open redirect ------------------------------------------------------

def test_back_veld_kan_alleen_de_twee_eigen_bestemmingen_aanwijzen(db, monkeypatch):
    """``back`` is een woord, geen URL.

    Zou het een URL zijn, dan is dit formulier -- dat achter een login staat en
    dus de moeite waard is -- een open redirect. Deze test bewaakt dat een
    vreemde waarde op de veilige bestemming uitkomt in plaats van erbuiten.
    """
    from app import routes_admin
    monkeypatch.setattr(routes_admin, "require_login", lambda request: "admin")
    monkeypatch.setattr(routes_admin, "check_csrf", lambda request, csrf: None)
    db.execute("INSERT INTO repeaters(slug, pubkey_prefix, name, created_at)"
               " VALUES('r','aabbccddeeff','R', '2026-01-01T00:00:00Z')")
    rid = db.qone("SELECT id FROM repeaters")["id"]

    naar_node = routes_admin.toggle_repeater(scope_request("/x"), rid, csrf="x", back="node")
    assert naar_node.headers["location"] == f"/admin/repeaters/{rid}"

    kwaadaardig = routes_admin.toggle_repeater(scope_request("/x"), rid, csrf="x",
                                               back="https://elders.example/")
    assert kwaadaardig.headers["location"] == "/admin"
