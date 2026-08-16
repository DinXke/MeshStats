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
        "fw": "v1.17.0", "fw_meshmanager": "2.1.0",
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
    route = nodeconfig.cfg_route(rep(fw_meshmanager="2.0.0"))
    assert route["blocker"] == "old_fw" and route["min_fw"] == "2.1.0"


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
    db.execute("UPDATE repeaters SET fw_meshmanager='2.1.0', ota_host='http://x', "
               "source_prefix='55d9a320a4e3' WHERE id=?", (row["id"],))
    row = db.qone("SELECT * FROM repeaters WHERE id=?", (row["id"],))
    nodeconfig.write(dict(row), "name", "Dak-Noord")
    assert db.qone("SELECT name FROM repeaters WHERE id=?", (row["id"],))["name"] == "Dak-Noord"


# --- de lijst van de node -----------------------------------------------------

def test_oude_firmware_geeft_een_versie_en_geen_storing(monkeypatch):
    """404 op /api/cfg betekent iets heel bepaalds: de node leeft en praat HTTP,
    maar draait firmware van voor 2.1.0. Dat is een versie, geen storing, en de
    pagina hoort dat anders te zeggen dan 'onbereikbaar'."""
    def oud(*a, **k):
        raise urllib.error.HTTPError("u", 404, "Not Found", {}, _Fake(b""))

    monkeypatch.setattr(nodeconfig, "_open", oud)
    uit = nodeconfig.params("http://node.invalid")
    assert uit["ok"] is False and "2.1.0" in uit["error"]


def test_de_lijst_wordt_gecachet(monkeypatch):
    keer = []
    monkeypatch.setattr(nodeconfig, "_open",
                        lambda *a, **k: keer.append(1) or _Antwoord(LIJST))
    nodeconfig.params("http://node.invalid")
    nodeconfig.params("http://node.invalid")
    assert len(keer) == 1
    nodeconfig.params("http://node.invalid", force=True)
    assert len(keer) == 2


# --- de bevestiging, en dat die op de server staat ---------------------------

VOLLE_LIJST = [
    {"key": "name", "kind": "text", "lo": 0, "hi": 0, "choices": "", "risk": 1, "reboot": 0},
    {"key": "flood.max", "kind": "int", "lo": 0, "hi": 64, "choices": "", "risk": 2, "reboot": 0},
    {"key": "loop.detect", "kind": "enum", "lo": 0, "hi": 0,
     "choices": "off|minimal|moderate|strict", "risk": 2, "reboot": 0},
    {"key": "cad", "kind": "bool", "lo": 0, "hi": 0, "choices": "", "risk": 2, "reboot": 0},
    {"key": "tx", "kind": "int", "lo": 0, "hi": 30, "choices": "", "risk": 3, "reboot": 0},
    {"key": "radio", "kind": "radio", "lo": 0, "hi": 0, "choices": "", "risk": 3, "reboot": 1},
]


def _volle_lijst(monkeypatch):
    monkeypatch.setattr(nodeconfig, "params",
                        lambda host, force=False: {"ok": True, "error": "",
                                                   "params": VOLLE_LIJST, "at": 0})


def test_gewone_waarde_heeft_geen_bevestiging_nodig(db, monkeypatch):
    _volle_lijst(monkeypatch)
    monkeypatch.setattr(nodeconfig, "_open", lambda *a, **k: _Antwoord(
        {"ok": 1, "step": "", "key": "name", "asked": "X", "applied": "X",
         "exact": 1, "reply": "OK"}))
    assert nodeconfig.write(rep(), "name", "X")["ok"] is True


def test_merkbare_waarde_zonder_bevestiging_gaat_niet_de_deur_uit(db, monkeypatch):
    """De drempel staat op de server en niet alleen in het sjabloon: een
    bevestiging die je met een aangepast formulier kunt overslaan is geen
    bevestiging maar een opmaakkeuze."""
    _volle_lijst(monkeypatch)
    monkeypatch.setattr(nodeconfig, "_open",
                        lambda *a, **k: pytest.fail("mocht de node niet benaderen"))
    uit = nodeconfig.write(rep(), "flood.max", "32")
    assert uit["ok"] is False and uit["step"] == "bevestiging"


def test_merkbare_waarde_met_bevestiging_mag(db, monkeypatch):
    _volle_lijst(monkeypatch)
    monkeypatch.setattr(nodeconfig, "_open", lambda *a, **k: _Antwoord(
        {"ok": 1, "step": "", "key": "flood.max", "asked": "32", "applied": "32",
         "exact": 1, "reply": "OK"}))
    assert nodeconfig.write(rep(), "flood.max", "32", confirm="ja")["ok"] is True


def test_afsnijdende_waarde_eist_de_naam_van_de_node(db, monkeypatch):
    """De fout die dit vangt is niet twijfel maar de klik op de verkeerde node,
    en daar helpt een ja/nee-vraag niet tegen."""
    _volle_lijst(monkeypatch)
    monkeypatch.setattr(nodeconfig, "_open",
                        lambda *a, **k: pytest.fail("mocht de node niet benaderen"))
    for poging in ("", "ja", "DinX-Thuis"):
        uit = nodeconfig.write(rep(), "tx", "10", confirm=poging)
        assert uit["ok"] is False and uit["step"] == "bevestiging"
        assert "DinX-Home" in uit["msg"]


def test_afsnijdende_waarde_met_de_juiste_naam_mag(db, monkeypatch):
    _volle_lijst(monkeypatch)
    monkeypatch.setattr(nodeconfig, "_open", lambda *a, **k: _Antwoord(
        {"ok": 1, "step": "", "key": "tx", "asked": "10", "applied": "10",
         "exact": 1, "reply": "OK"}))
    assert nodeconfig.write(rep(), "tx", "10", confirm="DinX-Home")["ok"] is True


# --- de nieuwe soorten --------------------------------------------------------

@pytest.mark.parametrize("waarde,goed", [
    ("on", True), ("off", True), ("onzin", False), ("aan", False), ("", False),
])
def test_booleaanse_waarde_alleen_precies_on_of_off(waarde, goed):
    """MeshCore vergelijkt met memcmp(..., "on", 2), dus "onzin" zet het daar aan.
    Dit is de enige plek waar die tikfout nog geweigerd kan worden."""
    spec = {"key": "cad", "kind": "bool"}
    assert (nodeconfig._check(spec, waarde) == "") is goed


@pytest.mark.parametrize("waarde,goed", [
    ("off", True), ("strict", True), ("Strict", False), ("uit", False), ("", False),
])
def test_opsomming_alleen_uit_de_lijst(waarde, goed):
    spec = {"key": "loop.detect", "kind": "enum", "choices": "off|minimal|moderate|strict"}
    assert (nodeconfig._check(spec, waarde) == "") is goed


@pytest.mark.parametrize("waarde,fout", [
    ("869.525 250 11 5", ""),
    ("869.525,250,11,5", ""),            # komma's mogen ook: 'get radio' geeft die vorm
    ("869.525 250 11", "vier waarden"),
    ("50 250 11 5", "buiten 150-2500"),
    ("869.525 900 11 5", "buiten 7-500"),
    ("869.525 250 13 5", "spreading factor"),
    ("869.525 250 11 9", "coding rate"),
    ("abc def ghi jkl", "getallen"),
])
def test_radiowaarden_worden_stuk_voor_stuk_gecontroleerd(waarde, fout):
    spec = {"key": "radio", "kind": "radio"}
    uit = nodeconfig._check(spec, waarde)
    assert (fout in uit) if fout else (uit == "")


def test_keuzes_worden_uitgesplitst():
    assert nodeconfig.choices({"choices": "off|minimal"}) == ["off", "minimal"]
    assert nodeconfig.choices({"choices": ""}) == []
    assert nodeconfig.choices({}) == []


# --- de pagina ----------------------------------------------------------------

PARAMS = [
    {"key": "name", "kind": "text", "lo": 0, "hi": 0, "choices": "", "risk": 1, "reboot": 0},
    {"key": "flood.max", "kind": "int", "lo": 0, "hi": 64, "choices": "", "risk": 2, "reboot": 0},
    {"key": "loop.detect", "kind": "enum", "lo": 0, "hi": 0,
     "choices": "off|minimal|moderate|strict", "risk": 2, "reboot": 0},
    {"key": "cad", "kind": "bool", "lo": 0, "hi": 0, "choices": "", "risk": 2, "reboot": 0},
    {"key": "tx", "kind": "int", "lo": 0, "hi": 30, "choices": "", "risk": 3, "reboot": 0},
    {"key": "radio", "kind": "radio", "lo": 0, "hi": 0, "choices": "", "risk": 3, "reboot": 1},
    {"key": "guest.password", "kind": "text", "lo": 0, "hi": 0, "choices": "",
     "risk": 3, "reboot": 0, "secret": 1},
]


class _AllesMag:
    """Een rechtenwoordenboek dat op elke sleutel 'ja' antwoordt.

    De sjabloon doet ``rechten[sleutel]`` en verwacht een besluit dat waar is en
    een reden kan geven. Een gewone dict zou elke onbekende sleutel laten
    struikelen, en dan test je of de test alle sleutels kent in plaats van wat
    de pagina toont.
    """

    def __getitem__(self, sleutel):
        return self

    def __bool__(self):
        return True

    @property
    def reden(self):
        return ""


def _render(**over):
    """De echte nodepagina door de echte Jinja-omgeving.

    Zelfde reden als bij de firmwarepagina: bijna alles wat hier mis kan gaan zit
    in de takken die uitleggen waaróm er niet geschreven kan worden, en die
    branden pas bij het renderen. Een tikfout daar is geen testfout maar een lege
    beheerpagina.
    """
    from app import nodeconfig as nc
    from app.templating import templates
    params = over.pop("params", PARAMS)
    ctx = {
        "site_name": "MeshManager", "user": "u", "world": "nodes",
        "rep": {"id": 1, "name": "DinX-Home", "pubkey_prefix": "55d9",
                "source_prefix": "55d9", "fw": "v1.17.0", "fw_meshmanager": "2.1.0",
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
                  "fw_meshmanager": "2.1.0", "min_fw": "1.8.0", "node_seen": None,
                  "node_stale": False, "ha": False, "poller_seen": None},
        "cfg_route": {"can": True, "blocker": "", "host": "http://x",
                      "fw": "2.1.0", "min_fw": "2.1.0", "relayed": False},
        "cfg_params": {"ok": True, "error": "", "params": params},
        "cfg_groups": [(r, [q for q in params if q["risk"] == r])
                       for r in (nc.RISK_PLAIN, nc.RISK_WRITES, nc.RISK_CUTOFF)],
        "cfg_now": {"name": "DinX-Home", "tx": "22"},
        "cfg_result": None, "rights": None, "relay": None,
        "sweep_hours": 0, "sweep_next": None, "sweep_last": None,
        "sweep_status": {"enabled": True, "min_gap_min": 15,
                         "max_per_day": 48, "today": 0},
        # Sinds het rechtenmodel vraagt de sjabloon wat er mág. Hier alles
        # toegestaan: deze tests gaan over de bediening van instellingen, niet
        # over de rechten -- die hebben hun eigen tests in test_rechten.py. Zou
        # dit meebeslissen, dan zou een test rood worden om een reden die niets
        # met zijn onderwerp te maken heeft.
        "rechten": _AllesMag(),
        "mijn_rol": "beheerder",
        "serverrechten": _AllesMag(),
        "audit": [],
    }
    ctx.update(over)
    return templates.env.get_template("admin/node.html").render(ctx)


def test_pagina_toont_alle_drie_de_risicoklassen():
    html = _render()
    assert "Gewoon" in html
    assert "Schrijft merkbaar" in html
    assert "Kan de bereikbaarheid afsnijden" in html
    assert 'action="/admin/repeaters/1/config"' in html


def test_een_opsomming_wordt_een_keuzelijst_en_geen_tekstveld():
    """Een invoerveld waarin je een ongeldige waarde kúnt typen is een invoerveld
    dat een node kan breken."""
    html = _render()
    for woord in ("off", "minimal", "moderate", "strict"):
        assert f'<option value="{woord}">' in html


def test_een_getal_krijgt_de_grenzen_van_de_node_mee():
    html = _render()
    assert 'min="0" max="64"' in html      # flood.max
    assert 'min="0" max="30"' in html      # tx


def test_radio_krijgt_vier_velden_met_elk_eigen_grenzen():
    """Eén tekstveld waarin je "869.525 250 11 5" moet typen is precies het soort
    veld waarin een tikfout een node van de lucht haalt."""
    html = _render()
    for naam in ("rf", "rb", "rs", "rc"):
        assert f'name="{naam}"' in html
    assert 'min="150" max="2500"' in html
    assert 'min="5" max="12"' in html


def test_de_zwaarste_klasse_vraagt_om_de_naam_van_de_node():
    html = _render()
    assert 'placeholder="DinX-Home"' in html
    assert "de naam van de node overtypen" in html


def test_de_middelste_klasse_vraagt_een_uitdrukkelijke_bevestiging():
    html = _render()
    assert 'name="confirm" value="ja"' in html


@pytest.mark.parametrize("blocker,zin", [
    ("no_credentials", "geen weblogin voor de beheerpagina"),
    ("relayed_only", "blijvende toestand"),
    ("no_host", "geen beheeradres"),
    ("old_fw", "bestaat pas vanaf"),
    ("no_fw", "meldt geen versie"),
])
def test_elke_reden_om_niet_te_kunnen_schrijven_krijgt_zijn_eigen_zin(blocker, zin):
    html = _render(cfg_route={"can": False, "blocker": blocker, "host": "",
                              "fw": "2.0.0", "min_fw": "2.1.0", "relayed": False})
    assert zin in html
    assert 'action="/admin/repeaters/1/config"' not in html


def test_afgeronde_waarde_wordt_niet_als_gewoon_gelukt_getoond():
    """De hele reden dat er teruggelezen wordt."""
    html = _render(cfg_result={"ok": True, "exact": False, "key": "advert.interval",
                               "asked": "61", "applied": "60", "step": "", "msg": "",
                               "reboot": False})
    assert "niet precies" in html
    assert "61" in html and "60" in html


def test_een_wijziging_die_pas_na_herstart_geldt_zegt_dat():
    """'radio' wordt bewaard maar pas bij een herstart actief -- en pas dan blijkt
    of de nieuwe waarden kloppen. Dat is het geval waarin een node wegblijft."""
    html = _render(cfg_result={"ok": True, "exact": True, "key": "radio",
                               "asked": "869.525 250 11 5", "applied": "869.525,250,11,5",
                               "step": "", "msg": "", "reboot": True})
    assert "Nog niet actief" in html


def test_een_weigering_toont_de_reden_van_de_node():
    html = _render(cfg_result={"ok": False, "exact": False, "key": "lat",
                               "asked": "999", "applied": "", "step": "waarde",
                               "msg": "lat moet een getal tussen -90 en 90 zijn",
                               "reboot": False})
    assert "Niet gezet" in html
    assert "tussen -90 en 90" in html


def test_zonder_lijst_van_de_node_geen_formulier():
    html = _render(cfg_params={"ok": False, "error": "niet bereikbaar (URLError)",
                               "params": []}, cfg_groups=[], params=[])
    assert "niet bereikbaar" in html
    assert 'action="/admin/repeaters/1/config"' not in html


def test_een_geheim_wordt_niet_getoond_en_niet_voorgevuld():
    """Wel vergeleken, niet verklapt. Een wachtwoord dat in de HTML van de
    beheerpagina, de browsergeschiedenis of een schermafdruk beland is, is weg --
    dezelfde reden waarom bridge.secret helemaal niet aangeboden wordt."""
    html = _render()
    assert 'type="password" name="value"' in html
    assert "•••" in html


def test_een_geheim_krijgt_zijn_waarde_niet_terug(db, monkeypatch):
    _volle_lijst_met_geheim(monkeypatch)
    monkeypatch.setattr(nodeconfig, "_open", lambda *a, **k: _Antwoord(
        {"ok": 1, "step": "", "key": "guest.password", "asked": "hunter2",
         "applied": "(verborgen)", "exact": 1, "reply": "OK"}))
    uit = nodeconfig.write(rep(), "guest.password", "hunter2", confirm="DinX-Home")
    assert uit["ok"] is True and uit["exact"] is True
    assert uit["applied"] == "(verborgen)"


def _volle_lijst_met_geheim(monkeypatch):
    lijst = VOLLE_LIJST + [{"key": "guest.password", "kind": "text", "lo": 0, "hi": 0,
                            "choices": "", "risk": 3, "reboot": 0, "secret": 1}]
    monkeypatch.setattr(nodeconfig, "params",
                        lambda host, force=False: {"ok": True, "error": "",
                                                   "params": lijst, "at": 0})


# --- rechten van de monitor op zijn doelnode ---------------------------------

def _monlijst(monkeypatch, entries, heard=()):
    monkeypatch.setattr(nodeconfig, "monitors",
                        lambda host: {"ok": True, "error": "",
                                      "entries": list(entries), "heard": list(heard)})


DOEL = "e3d3f4d7edd0"


def test_geen_wachtwoord_betekent_de_acl_weg(monkeypatch):
    """Een lege wachtwoordkolom is een keuze en geen omissie: de overkant slaat
    de wachtwoordcontrole over en zoekt onze sleutel op in zijn toegangslijst."""
    _monlijst(monkeypatch, [{"k": DOEL, "pw": 0, "lr": 1, "polls": 3, "oks": 3}])
    r = nodeconfig.rights_for("http://x", DOEL)
    assert r["mode"] == nodeconfig.MON_MODE_ACL
    assert r["diagnosis"] == "goed"


def test_met_wachtwoord_wordt_dat_ook_zo_gemeld(monkeypatch):
    _monlijst(monkeypatch, [{"k": DOEL, "pw": 1, "lr": 1, "polls": 2, "oks": 2}])
    assert nodeconfig.rights_for("http://x", DOEL)["mode"] == nodeconfig.MON_MODE_PASSWORD


def test_ingelogd_maar_alles_zwijgt_is_alleen_lezen(monkeypatch):
    """Het verraderlijke geval: de login lukt perfect en achttien commando's
    krijgen niets terug, omdat de overkant een CLI-commando alleen uitvoert voor
    een client met adminrechten. Van een afstand ziet dat eruit als een node die
    niet bereikbaar is."""
    _monlijst(monkeypatch, [{"k": DOEL, "pw": 0, "lr": 1, "polls": 4, "oks": 0}])
    assert nodeconfig.rights_for("http://x", DOEL)["diagnosis"] == "alleen_lezen"


def test_login_zonder_antwoord_terwijl_we_hem_horen_is_geen_toegang(monkeypatch):
    _monlijst(monkeypatch, [{"k": DOEL, "pw": 0, "lr": 0, "polls": 4, "oks": 0}],
              heard=[{"k": DOEL}])
    assert nodeconfig.rights_for("http://x", DOEL)["diagnosis"] == "geen_toegang"


def test_login_zonder_antwoord_en_niet_gehoord_is_buiten_bereik(monkeypatch):
    """Het onderscheid waar iemand anders een half uur op verliest. De heardlijst
    is het enige wat 'mag niet' van 'kan niet' scheidt."""
    _monlijst(monkeypatch, [{"k": DOEL, "pw": 0, "lr": 0, "polls": 4, "oks": 0}])
    assert nodeconfig.rights_for("http://x", DOEL)["diagnosis"] == "buiten_bereik"


def test_een_node_die_niet_gemonitord_wordt(monkeypatch):
    _monlijst(monkeypatch, [{"k": "aaaaaaaaaaaa", "pw": 0, "lr": 1, "polls": 1, "oks": 1}])
    r = nodeconfig.rights_for("http://x", DOEL)
    assert r["known"] is False and r["diagnosis"] == "niet_gemonitord"


def test_het_wachtwoord_gaat_naar_de_monitor_en_wordt_niet_bewaard(db, monkeypatch):
    """Doorgeven en vergeten. Een inbraak op deze website mag geen sleutelbos
    opleveren voor de nodes van anderen."""
    gezien = {}

    class _Resp:
        def read(self):
            return b"{}"

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def nep(host, path, data=None, timeout=10):
        gezien["path"] = path
        gezien["data"] = data
        return _Resp()

    monkeypatch.setattr(nodeconfig, "_open", nep)
    uit = nodeconfig.push_monitor_password("http://x", DOEL, "geheim")
    assert uit["ok"] is True
    assert gezien["path"] == "/api/mon"
    assert b"act=pass" in gezien["data"] and b"geheim" in gezien["data"]

    # ...en nergens blijft het hangen.
    alles = " ".join(str(r) for r in db.q("SELECT key, value FROM settings"))
    assert "geheim" not in alles


def test_een_leeg_wachtwoord_zet_terug_op_de_acl_weg(db, monkeypatch):
    """Geen 'niets doen' maar een geldige opdracht: wissen en terug naar de
    aanbevolen manier."""
    gezien = {}

    class _Resp:
        def read(self):
            return b"{}"

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(nodeconfig, "_open",
                        lambda host, path, data=None, timeout=10:
                        gezien.update(data=data) or _Resp())
    assert nodeconfig.push_monitor_password("http://x", DOEL, "")["ok"] is True
    assert b"pass=" in gezien["data"]


def test_een_doorgestuurde_node_klaagt_nooit_over_serverinloggegevens(monkeypatch):
    """De ontwerpfout die dit vangt. 'De server heeft geen inloggegevens' stond
    bovenaan in de volgorde en kreeg daardoor ook de doorgestuurde nodes te
    pakken -- terwijl juist voor die nodes de server nooit inloggegevens hoeft te
    hebben. Hun rechten horen bij de monitor."""
    monkeypatch.setattr(firmware, "NODE_USER", "")
    route = nodeconfig.cfg_route(rep(pubkey_prefix="e3d3f4d7edd0",
                                     source_prefix="55d9a320a4e3", ota_host=""))
    assert route["blocker"] == "relayed_only"


def test_een_eigen_node_zonder_weblogin_klaagt_daar_wel_over(monkeypatch):
    monkeypatch.setattr(firmware, "NODE_USER", "")
    assert nodeconfig.cfg_route(rep())["blocker"] == "no_credentials"


@pytest.mark.parametrize("diagnose,zin", [
    ("goed", "Ja —"),
    ("alleen_lezen", "Ingelogd, maar alles zwijgt"),
    ("geen_toegang", "het ligt niet aan het bereik"),
    ("buiten_bereik", "we horen hem ook niet"),
])
def test_elke_stilte_krijgt_zijn_eigen_diagnose_op_de_pagina(diagnose, zin):
    """De drie stiltes zien er van een afstand identiek uit, en dat is waar
    iemand een half uur op verliest."""
    html = _render(
        cfg_route={"can": False, "blocker": "relayed_only", "host": "",
                   "fw": "", "min_fw": "2.1.0", "relayed": True},
        relay={"name": "DinX-Home", "id": 2},
        rights={"ok": True, "known": True, "mode": "acl", "diagnosis": diagnose,
                "polls": 4, "oks": 4 if diagnose == "goed" else 0,
                "heard": True, "error": ""})
    assert zin in html


def test_de_toegangslijst_wordt_als_de_betere_weg_gepresenteerd():
    html = _render(
        cfg_route={"can": False, "blocker": "relayed_only", "host": "",
                   "fw": "", "min_fw": "2.1.0", "relayed": True},
        relay={"name": "DinX-Home", "id": 2},
        rights={"ok": True, "known": True, "mode": "password", "diagnosis": "goed",
                "polls": 2, "oks": 2, "heard": True, "error": ""})
    assert "toegangslijst verdient de voorkeur" in html
    assert "setperm" in html


def test_een_doorgestuurde_node_vraagt_niet_om_serverinloggegevens_op_de_pagina():
    """De ontwerpfout zoals Björn hem zag: 'de server heeft geen inloggegevens'
    onder een node waarvoor de server die nooit hoeft te hebben."""
    html = _render(cfg_route={"can": False, "blocker": "relayed_only", "host": "",
                              "fw": "", "min_fw": "2.1.0", "relayed": True},
                   relay=None, rights=None)
    assert "MM_FW_NODE_USER" not in html
    assert "horen bij zijn monitor" in html
