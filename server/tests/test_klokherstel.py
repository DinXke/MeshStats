"""De klok van een repeater die VOORLOOPT rechtzetten, via de poller.

Waarom dit een eigen handeling is en geen variant van ``/clocksync``: die stuurt
een tijd naar een node die hem aanneemt. Hier weigert de firmware van de
doelrepeater een klok achteruit te zetten (``ERR: clock cannot go backwards``,
letterlijk in zijn binary), dus kost het een ``clkreboot`` -- een HERSTART -- en
tussen die herstart en het gezette uur negeren andere nodes zijn adverts, zolang
zijn oude tijdstempel in de toekomst lag.

Dat verschil is de hele reden dat dit bestand bestaat. Vier eisen:

1. De knop bestaat alleen als de poller zegt dat hij de hele reeks kan (``caps``
   met ``clockfix``). Een poller die zwijgt kan het NIET -- anders zou een oude
   Home Assistant-integratie ineens een dak-repeater kunnen herstarten.
2. Het vraagt de naam van de node, net als firmware schrijven en verwijderen.
3. Het is een eigen recht in de zwaarste klasse; wie de klok mag bijstellen mag
   daarmee niet ook een repeater herstarten.
4. Wat er in de wachtrij komt is precies ``cmd:clockfix`` -- het contract met de
   nodefirmware, die dat woord NIET naar de repeater doorstuurt.
"""
import pytest
from starlette.requests import Request

from app import commanding, config, rbac


@pytest.fixture
def db(tmp_path, monkeypatch):
    from app import db as db_module
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "test.sqlite3")
    db_module._conn = None
    yield db_module
    if db_module._conn is not None:
        db_module._conn.close()
        db_module._conn = None


VERS = "2099-01-01T00:00:00Z"


def rep(**over):
    row = {"id": 7, "name": "BE-HSS-JessaZH", "pubkey_prefix": "e3d3f4d7edd0",
           "source_prefix": "48d7aade232b", "fw_meshmanager": "",
           "fw": "v1.17.1-PS+filter", "source_seen": None}
    row.update(over)
    return row


def route(**over):
    kw = {"broker_connected": True, "poller_seen": VERS, "poller_name": "node-push-token",
          "poller_caps": ["settings", "refresh", "clockfix"]}
    kw.update(over)
    return commanding.route_for(rep(), **kw)


# --- de capaciteit ------------------------------------------------------------

def test_alleen_met_de_clockfix_capaciteit():
    assert route()["poller_clockfix"] is True
    assert route(poller_caps=["settings", "refresh"])["poller_clockfix"] is False


def test_een_zwijgende_poller_krijgt_deze_knop_niet():
    """De kern van de veiligheid hier. 'settings' en 'refresh' gelden voor een
    poller die niets zegt, want de Home Assistant-integratie deed die; een klok
    rechtzetten deed hij nooit, en het HERSTART een node."""
    assert "clockfix" not in commanding.DEFAULT_POLLER_CAPS
    assert route(poller_caps=None)["poller_clockfix"] is False


def test_zonder_verse_poller_kan_het_niet():
    assert route(poller_seen="2020-01-01T00:00:00Z")["poller_clockfix"] is False


# --- het recht ----------------------------------------------------------------

def test_het_is_een_eigen_recht_in_de_zwaarste_klasse():
    h = rbac.ACTIONS["node.klokherstel"]
    assert h.klasse == rbac.KLASSE_INGRIJPEND
    # En zwaarder dan de gewone klokhandeling: wie de klok mag bijstellen, mag
    # daarmee niet ook een repeater op een dak herstarten.
    assert rbac._rang(h.klasse) > rbac._rang(rbac.ACTIONS["node.klok"].klasse)
    assert "herstart" in h.tekst


@pytest.mark.parametrize("rol,mag", [("lezer", False), ("bediener", False),
                                     ("technicus", False), ("beheerder", True)])
def test_alleen_een_beheerder_mag_het(rol, mag):
    plafond = rbac.ROL_PLAFOND[rol]
    klasse = rbac.ACTIONS["node.klokherstel"].klasse
    assert (rbac._rang(klasse) <= rbac._rang(plafond)) is mag


# --- de route -----------------------------------------------------------------

def verzoek(cookie):
    return Request({
        "type": "http", "http_version": "1.1", "method": "POST",
        "scheme": "http", "server": ("test", 80), "path": "/admin/repeaters/1/clockfix",
        "query_string": b"", "headers": [(b"cookie", f"mm_session={cookie}".encode())],
    })


@pytest.fixture
def wereld(db, monkeypatch):
    from app import auth, firmware, mqtt_ingest, nodeconfig, pktfilter, sensornode
    from app import rbac as r
    monkeypatch.setattr(mqtt_ingest, "can_publish", lambda: False)
    monkeypatch.setattr(firmware, "releases",
                        lambda force=False: {"items": [], "error": "", "at": 0})
    monkeypatch.setattr(pktfilter, "state",
                        lambda host, timeout=None: {"ok": False, "error": "", "filter": {}})
    monkeypatch.setattr(nodeconfig, "params", lambda *a, **k: {"ok": False, "error": "", "params": []})
    monkeypatch.setattr(sensornode, "_json", lambda *a, **k: {"ok": False, "error": "", "data": {}})

    node = db.get_or_create_repeater("e3d3f4d7edd0", "BE-HSS-JessaZH")
    db.execute("UPDATE repeaters SET source_prefix=?, fw=? WHERE id=?",
               ("48d7aade232b", "v1.17.1-PS+filter", node["id"]))
    db.note_poller_seen("node-push-token", ["settings", "refresh", "clockfix"])
    uid = r.maak_gebruiker("baas", auth.hash_password("wachtwoord123"), is_superuser=True)
    return {"node": db.qone("SELECT * FROM repeaters WHERE id=?", (node["id"],)),
            "koek": auth.make_session("baas"), "uid": uid}


def _post(wereld, confirm):
    from app import auth, routes_admin
    req = verzoek(wereld["koek"])
    csrf = auth.csrf_token(auth.read_session(wereld["koek"]) or "baas")
    monkey = None
    # De CSRF-controle leest het token uit de sessie; hier geven we hem mee.
    return routes_admin.queue_clockfix(req, wereld["node"]["id"], confirm=confirm, csrf=csrf)


def test_zonder_de_naam_komt_er_niets_in_de_wachtrij(db, wereld, monkeypatch):
    from app import routes_admin
    monkeypatch.setattr(routes_admin, "check_csrf", lambda *a, **k: None)
    resp = _post(wereld, "")
    assert resp.status_code == 200
    assert db.pop_settings_requests() == []


def test_met_de_naam_gaat_cmd_clockfix_de_wachtrij_in(db, wereld, monkeypatch):
    from app import routes_admin
    monkeypatch.setattr(routes_admin, "check_csrf", lambda *a, **k: None)
    _post(wereld, "BE-HSS-JessaZH")
    wachtrij = db.pop_settings_requests()
    assert len(wachtrij) == 1
    assert wachtrij[0]["prefix"] == "e3d3f4d7edd0"
    assert wachtrij[0]["params"] == ["cmd:clockfix"]


def test_zonder_capaciteit_gaat_er_niets_de_wachtrij_in(db, wereld, monkeypatch):
    """Een poller die geen clockfix meldt, mag ook met de juiste naam geen
    herstart krijgen."""
    from app import routes_admin
    monkeypatch.setattr(routes_admin, "check_csrf", lambda *a, **k: None)
    db.note_poller_seen("node-push-token", ["settings", "refresh"])
    _post(wereld, "BE-HSS-JessaZH")
    assert db.pop_settings_requests() == []


def test_het_komt_in_het_audittrail(db, wereld, monkeypatch):
    from app import routes_admin
    monkeypatch.setattr(routes_admin, "check_csrf", lambda *a, **k: None)
    _post(wereld, "BE-HSS-JessaZH")
    regels = db.q("SELECT * FROM audit ORDER BY id DESC LIMIT 3")
    assert any("klokherstel" in str(dict(r)) for r in regels)
