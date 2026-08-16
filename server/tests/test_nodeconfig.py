"""Instellingen schrijven: wie het mag, wat er geweigerd wordt, en teruglezen.

De rode draad is dezelfde als bij de firmware-upgrade en staat er niet toevallig
twee keer: een node die "OK" antwoordt heeft niet noodzakelijk gedaan wat je
vroeg. MeshCore's ``set lat`` is een kale atof() die van een tikfout 0.0 maakt,
en ``advert.interval`` wordt bewaard als minuten/2, zodat 61 als 60 terugkomt.
De helft van de tests hieronder gaat daarover.
"""
import json
import urllib.error

import pytest

from app import firmware, nodeconfig


@pytest.fixture
def db(tmp_path, monkeypatch):
    from app import config
    from app import db as db_module
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "test.sqlite3")
    db_module._conn = None
    yield db_module
    if db_module._conn is not None:
        db_module._conn.close()
        db_module._conn = None


@pytest.fixture(autouse=True)
def _schoon(monkeypatch):
    monkeypatch.setattr(firmware, "NODE_USER", "admin")
    monkeypatch.setattr(firmware, "NODE_PASS", "x")
    nodeconfig._params.clear()


def rep(**overrides):
    row = {
        "id": 1, "name": "DinX-Home", "pubkey_prefix": "55d9a320a4e3",
        "fw": "v1.17.0", "fw_meshmanager": "1.13.0",
        "source_prefix": "55d9a320a4e3", "ota_host": "http://node.invalid",
    }
    row.update(overrides)
    return row


LIJST = {"params": [
    {"key": "name", "kind": "text", "lo": 0, "hi": 0, "tier": 1},
    {"key": "lat", "kind": "float", "lo": -90, "hi": 90, "tier": 1},
    {"key": "advert.interval", "kind": "int", "lo": 60, "hi": 240, "tier": 1},
]}


def _lijst_van_de_node(monkeypatch):
    monkeypatch.setattr(nodeconfig, "params",
                        lambda host, force=False: {"ok": True, "error": "",
                                                   "params": LIJST["params"], "at": 0})


# --- wie mag schrijven --------------------------------------------------------

def test_zonder_inloggegevens_geen_schrijfweg(monkeypatch):
    monkeypatch.setattr(firmware, "NODE_USER", "")
    assert nodeconfig.cfg_route(rep())["blocker"] == "no_credentials"


def test_doorgestuurde_node_krijgt_een_blijvende_reden(monkeypatch):
    """De dakrepeater. Geen ontbrekende instelling maar een blijvende toestand:
    stock MeshCore, geen IP-pad, en de weg via zijn monitor bestaat nog niet."""
    route = nodeconfig.cfg_route(rep(pubkey_prefix="e3d3f4d7edd0",
                                     source_prefix="55d9a320a4e3", ota_host=""))
    assert route["blocker"] == "relayed_only" and route["can"] is False


def test_eigen_node_zonder_adres(monkeypatch):
    assert nodeconfig.cfg_route(rep(ota_host=""))["blocker"] == "no_host"


def test_firmware_van_voor_1_13_kan_het_endpoint_niet(monkeypatch):
    route = nodeconfig.cfg_route(rep(fw_meshmanager="1.12.0"))
    assert route["blocker"] == "old_fw" and route["min_fw"] == "1.13.0"


def test_node_zonder_onze_firmware(monkeypatch):
    assert nodeconfig.cfg_route(rep(fw_meshmanager=""))["blocker"] == "no_fw"


def test_volledig_ingerichte_node_mag(monkeypatch):
    assert nodeconfig.cfg_route(rep())["can"] is True


# --- de grenzen van de node, hier alvast toegepast ---------------------------

@pytest.mark.parametrize("waarde,fout", [
    ("", "leeg"),
    ("naam\nmet regeleinde", "stuurtekens"),
    ("naam[met]haken", "tekens"),
    ("prima naam", ""),
])
def test_naamcontrole(waarde, fout):
    spec = {"key": "name", "kind": "text", "lo": 0, "hi": 0}
    uit = nodeconfig._check(spec, waarde)
    assert (fout in uit) if fout else (uit == "")


@pytest.mark.parametrize("waarde,goed", [
    ("50.5", True), ("-90", True), ("90", True),
    ("91", False), ("-91", False),
    ("noord", False),      # atof() aan de overkant zou hier 0.0 van maken
    ("12abc", False),      # strtof() leest 12 en meldt succes -- dat willen we niet
    ("50,5", True),        # een Nederlandse komma is een tikfout waard om te vergeven
])
def test_getalcontrole(waarde, goed):
    spec = {"key": "lat", "kind": "float", "lo": -90, "hi": 90}
    assert (nodeconfig._check(spec, waarde) == "") is goed


def test_geheel_getal_wordt_afgedwongen():
    spec = {"key": "advert.interval", "kind": "int", "lo": 60, "hi": 240}
    assert "geheel" in nodeconfig._check(spec, "90.5")
    assert nodeconfig._check(spec, "90") == ""


# --- schrijven ----------------------------------------------------------------

def test_een_geweigerde_route_raakt_het_netwerk_niet(db, monkeypatch):
    monkeypatch.setattr(nodeconfig, "_open",
                        lambda *a, **k: pytest.fail("mocht de node niet benaderen"))
    uit = nodeconfig.write(rep(ota_host=""), "name", "X")
    assert uit["ok"] is False and uit["step"] == "route"


def test_een_parameter_die_de_node_niet_aanbiedt_wordt_niet_verstuurd(db, monkeypatch):
    """De server heeft geen eigen lijst; hij vraagt het de node. Staat 'freq' er
    niet bij, dan gaat er niets de deur uit -- ook niet als iemand het formulier
    met de hand verbouwt."""
    _lijst_van_de_node(monkeypatch)
    monkeypatch.setattr(nodeconfig, "_open",
                        lambda *a, **k: pytest.fail("mocht de node niet benaderen"))
    uit = nodeconfig.write(rep(), "freq", "869.525")
    assert uit["ok"] is False and uit["step"] == "sleutel"


def test_een_waarde_buiten_de_grenzen_wordt_niet_verstuurd(db, monkeypatch):
    _lijst_van_de_node(monkeypatch)
    monkeypatch.setattr(nodeconfig, "_open",
                        lambda *a, **k: pytest.fail("mocht de node niet benaderen"))
    uit = nodeconfig.write(rep(), "lat", "999")
    assert uit["ok"] is False and uit["step"] == "waarde"


class _Antwoord:
    def __init__(self, payload):
        self._b = json.dumps(payload).encode()

    def read(self):
        return self._b

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_gelukt_en_precies(db, monkeypatch):
    _lijst_van_de_node(monkeypatch)
    verstuurd = {}

    def nep(host, path, data=None, timeout=10):
        verstuurd["data"] = data
        return _Antwoord({"ok": 1, "step": "", "key": "lat", "asked": "50.5",
                          "applied": "50.5", "exact": 1, "reply": "OK"})

    monkeypatch.setattr(nodeconfig, "_open", nep)
    uit = nodeconfig.write(rep(), "lat", "50.5")
    assert uit["ok"] is True and uit["exact"] is True and uit["applied"] == "50.5"
    assert b"key=lat" in verstuurd["data"]


def test_gelukt_maar_niet_precies_wordt_als_zodanig_gemeld(db, monkeypatch):
    """Het geval waar het teruglezen voor bestaat. advert.interval wordt bewaard
    als minuten/2 in één byte, dus 61 komt terug als 60 -- met "OK" ernaast. Wie
    hier alleen 'gelukt' zou tonen, vertelt dezelfde onwaarheid als de oude
    OTA-weg deed."""
    _lijst_van_de_node(monkeypatch)
    monkeypatch.setattr(nodeconfig, "_open", lambda *a, **k: _Antwoord(
        {"ok": 1, "step": "", "key": "advert.interval", "asked": "61",
         "applied": "60", "exact": 0, "reply": "OK"}))
    uit = nodeconfig.write(rep(), "advert.interval", "61")
    assert uit["ok"] is True
    assert uit["exact"] is False
    assert uit["asked"] == "61" and uit["applied"] == "60"


def test_de_node_weigert_en_die_reden_blijft_staan(db, monkeypatch):
    """Ook bij een fout antwoordt de node met JSON, en juist dan staat erin welke
    stap faalde. Die tekst inslikken en "HTTP 400" tonen zou de fout herhalen die
    dit ontwerp wegneemt."""
    _lijst_van_de_node(monkeypatch)

    def weiger(*a, **k):
        raise urllib.error.HTTPError(
            "u", 400, "Bad Request", {},
            _Fake(json.dumps({"ok": 0, "step": "waarde",
                              "msg": "lat moet een getal tussen -90 en 90 zijn"}).encode()))

    monkeypatch.setattr(nodeconfig, "_open", weiger)
    uit = nodeconfig.write(rep(), "lat", "50")
    assert uit["ok"] is False and uit["step"] == "waarde"
    assert "tussen -90 en 90" in uit["msg"]


class _Fake:
    """Het 'bestand' in een HTTPError. Heeft een close() omdat urllib het als een
    tijdelijk bestand opruimt en anders bij het afbreken gaat klagen."""

    def __init__(self, b):
        self._b = b

    def read(self):
        return self._b

    def close(self):
        pass


def test_een_node_die_niet_antwoordt(db, monkeypatch):
    _lijst_van_de_node(monkeypatch)

    def weg(*a, **k):
        raise OSError("niets thuis")

    monkeypatch.setattr(nodeconfig, "_open", weg)
    uit = nodeconfig.write(rep(), "lat", "50")
    assert uit["ok"] is False and uit["step"] == "verbinding"


def test_een_nieuwe_naam_komt_ook_in_onze_eigen_tabel(db, monkeypatch):
    """Anders toont de pagina de oude naam naast de melding dat het gelukt is,
    tot het volgende statistiekbericht binnenkomt -- in zuinige modus een uur."""
    _lijst_van_de_node(monkeypatch)
    monkeypatch.setattr(nodeconfig, "_open", lambda *a, **k: _Antwoord(
        {"ok": 1, "step": "", "key": "name", "asked": "Dak-Noord",
         "applied": "Dak-Noord", "exact": 1, "reply": "OK"}))
    row = db.get_or_create_repeater("55d9a320a4e3", "DinX-Home")
    db.execute("UPDATE repeaters SET fw_meshmanager='1.13.0', ota_host='http://x', "
               "source_prefix='55d9a320a4e3' WHERE id=?", (row["id"],))
    row = db.qone("SELECT * FROM repeaters WHERE id=?", (row["id"],))
    nodeconfig.write(dict(row), "name", "Dak-Noord")
    assert db.qone("SELECT name FROM repeaters WHERE id=?", (row["id"],))["name"] == "Dak-Noord"


# --- de lijst van de node -----------------------------------------------------

def test_oude_firmware_geeft_een_versie_en_geen_storing(monkeypatch):
    """404 op /api/cfg betekent iets heel bepaalds: de node leeft en praat HTTP,
    maar draait firmware van voor 1.13.0. Dat is een versie, geen storing, en de
    pagina hoort dat anders te zeggen dan 'onbereikbaar'."""
    def oud(*a, **k):
        raise urllib.error.HTTPError("u", 404, "Not Found", {}, _Fake(b""))

    monkeypatch.setattr(nodeconfig, "_open", oud)
    uit = nodeconfig.params("http://node.invalid")
    assert uit["ok"] is False and "1.13.0" in uit["error"]


def test_de_lijst_wordt_gecachet(monkeypatch):
    keer = []
    monkeypatch.setattr(nodeconfig, "_open",
                        lambda *a, **k: keer.append(1) or _Antwoord(LIJST))
    nodeconfig.params("http://node.invalid")
    nodeconfig.params("http://node.invalid")
    assert len(keer) == 1
    nodeconfig.params("http://node.invalid", force=True)
    assert len(keer) == 2


# --- de pagina ----------------------------------------------------------------

def _render(**over):
    """De echte nodepagina door de echte Jinja-omgeving.

    Zelfde reden als bij de firmwarepagina: bijna alles wat hier mis kan gaan zit
    in de takken die uitleggen waaróm er niet geschreven kan worden, en die
    branden pas bij het renderen. Een tikfout daar is geen testfout maar een lege
    beheerpagina.
    """
    from app.templating import templates
    ctx = {
        "site_name": "MeshManager", "user": "u", "world": "nodes",
        "rep": {"id": 1, "name": "DinX-Home", "pubkey_prefix": "55d9",
                "source_prefix": "55d9", "fw": "v1.17.0", "fw_meshmanager": "1.13.0",
                "ota_host": "http://x", "is_critical": 0, "slug": "dinx",
                "is_public": 1, "show_position": 1, "show_name": 1, "last_seen": None,
                "created_at": "2026-01-01", "topic_prefix": "", "pio_env": "",
                "sort_order": 0},
        "settings_rows": [], "status": "", "delivered_since": None,
        "delivery_unanswered": False, "csrf": "x", "requested": "",
        "queued_since": None,
        "clock_route": {"ok": False, "blocker": "", "node": None,
                        "via_monitor": False, "fw_meshmanager": ""},
        "clock_sent": None, "clock_gap_min": 10, "clock_min_fw": "1.10.0",
        "clock": "", "clocksync_reason": "", "clock_wait": "",
        "clock_enabled": True, "broker": True,
        "route": {"mqtt": True, "level": "full_managed", "level_why": "publiceert zelf",
                  "commands": ("settings", "status"), "via_monitor": False,
                  "blocker": "", "node": "55d9", "subject": "55d9",
                  "fw_meshmanager": "1.13.0", "min_fw": "1.8.0", "node_seen": None,
                  "node_stale": False, "ha": False, "poller_seen": None},
        "cfg_route": {"can": True, "blocker": "", "host": "http://x",
                      "fw": "1.13.0", "min_fw": "1.13.0", "relayed": False},
        "cfg_params": {"ok": True, "error": "", "params": [
            {"key": "name", "kind": "text", "lo": 0, "hi": 0, "tier": 1},
            {"key": "lat", "kind": "float", "lo": -90, "hi": 90, "tier": 1}]},
        "cfg_result": None,
    }
    ctx.update(over)
    return templates.env.get_template("admin/node.html").render(ctx)


def test_pagina_toont_het_schrijfformulier():
    html = _render()
    assert "Instellingen schrijven" in html
    assert 'action="/admin/repeaters/1/config"' in html
    assert "advert.interval" not in html      # niet aangeboden door deze node


@pytest.mark.parametrize("blocker,zin", [
    ("no_credentials", "geen inloggegevens"),
    ("relayed_only", "blijvende toestand"),
    ("no_host", "geen beheeradres"),
    ("old_fw", "bestaat pas vanaf"),
    ("no_fw", "meldt geen versie"),
])
def test_elke_reden_om_niet_te_kunnen_schrijven_krijgt_zijn_eigen_zin(blocker, zin):
    html = _render(cfg_route={"can": False, "blocker": blocker, "host": "",
                              "fw": "1.12.0", "min_fw": "1.13.0", "relayed": False})
    assert zin in html
    assert 'action="/admin/repeaters/1/config"' not in html


def test_afgeronde_waarde_wordt_niet_als_gewoon_gelukt_getoond():
    """De hele reden dat er teruggelezen wordt. 'Gezet' naast een waarde die
    afwijkt van wat er gevraagd is, zou hetzelfde soort halve waarheid zijn als
    de oude OTA-weg vertelde."""
    html = _render(cfg_result={"ok": True, "exact": False, "key": "advert.interval",
                               "asked": "61", "applied": "60", "step": "", "msg": ""})
    assert "niet precies" in html
    assert "61" in html and "60" in html


def test_een_weigering_toont_de_reden_van_de_node():
    html = _render(cfg_result={"ok": False, "exact": False, "key": "lat",
                               "asked": "999", "applied": "", "step": "waarde",
                               "msg": "lat moet een getal tussen -90 en 90 zijn"})
    assert "Niet gezet" in html
    assert "tussen -90 en 90" in html


def test_zonder_lijst_van_de_node_geen_formulier():
    """Een formulier uit een tabel op de server zou een parameter kunnen tonen
    die deze node weigert. Dan liever geen formulier."""
    html = _render(cfg_params={"ok": False, "error": "niet bereikbaar (URLError)",
                               "params": []})
    assert "niet bereikbaar" in html
    assert 'action="/admin/repeaters/1/config"' not in html
