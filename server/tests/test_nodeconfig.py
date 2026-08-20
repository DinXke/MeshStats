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


def bestaande(db, **overrides):
    """Dezelfde node, maar dan als rij die werkelijk in de databank staat.

    Sinds ``write()`` de teruggelezen waarde ook hier vastlegt -- anders blijft de
    kolom 'Nu' de oude waarde tonen naast een melding dat het gelukt is -- is een
    verzonnen dict met een id dat nergens bestaat niet meer genoeg. De rij in
    ``repeater_cli`` hangt aan een echte repeater.
    """
    row = db.get_or_create_repeater("55d9a320a4e3", "DinX-Home")
    db.execute("UPDATE repeaters SET fw_meshmanager='2.1.0', "
               "ota_host='http://node.invalid', source_prefix='55d9a320a4e3' "
               "WHERE id=?", (row["id"],))
    out = dict(db.qone("SELECT * FROM repeaters WHERE id=?", (row["id"],)))
    out.update(overrides)
    return out


# De dakrepeater, zoals hij werkelijk in de databank staat: stock MeshCore, geen
# beheeradres, en zijn cijfers komen binnen via DinX-Home. Twee rijen, want de
# schrijfweg over LoRa is een uitspraak over de MONITOR en niet over het doel.
DAK = "e3d3f4d7edd0"
MONITOR = "55d9a320a4e3"


def doorgestuurd(db, *, monitor_fw="2.4.0", monitor_host="http://monitor.invalid"):
    """(doelrij, monitorrij). Beide echt, zodat cfg_route de monitor kan vinden."""
    mon = db.get_or_create_repeater(MONITOR, "DinX-Home")
    db.execute("UPDATE repeaters SET fw_meshmanager=?, ota_host=?, source_prefix=? "
               "WHERE id=?", (monitor_fw, monitor_host, MONITOR, mon["id"]))
    doel = db.get_or_create_repeater(DAK, "JessaZH")
    db.execute("UPDATE repeaters SET fw_meshmanager='', ota_host='', source_prefix=? "
               "WHERE id=?", (MONITOR, doel["id"]))
    return (dict(db.qone("SELECT * FROM repeaters WHERE id=?", (doel["id"],))),
            dict(db.qone("SELECT * FROM repeaters WHERE id=?", (mon["id"],))))


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


def test_doorgestuurde_node_gaat_over_de_monitor(db, monkeypatch):
    """De dakrepeater. Hij heeft geen IP-pad en krijgt er nooit een, dus de weg
    loopt over zijn monitor: dáár klopt de server aan, en die zet het over LoRa.

    Wat deze test vooral vastlegt is welke node de eisen draagt. Het adres, de
    firmwareversie en de weblogin gaan alle drie over de MONITOR. Het doel draait
    stock MeshCore en hoeft niets -- dat is de hele reden dat deze weg bestaat."""
    doel, mon = doorgestuurd(db)
    route = nodeconfig.cfg_route(doel)
    assert route["can"] is True
    assert route["transport"] == "mesh"
    assert route["host"] == mon["ota_host"]     # de monitor, niet het doel
    assert route["target"] == DAK               # en het doel reist mee als sleutel
    assert route["fw"] == "2.4.0"               # de versie van de monitor


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
    """De server heeft geen eigen lijst; hij vraagt het de node. Staat 'tx' er
    niet bij, dan gaat er niets de deur uit -- ook niet als iemand het formulier
    met de hand verbouwt.

    'tx' en niet 'freq', en dat verschil is precies de test hiernaast: een
    radiosleutel komt tot deze controle niet eens, want die valt al op NO_REMOTE.
    Met 'freq' zou deze test iets anders bewijzen dan zijn naam belooft."""
    _lijst_van_de_node(monkeypatch)
    monkeypatch.setattr(nodeconfig, "_open",
                        lambda *a, **k: pytest.fail("mocht de node niet benaderen"))
    uit = nodeconfig.write(rep(), "tx", "20")
    assert uit["ok"] is False and uit["step"] == "sleutel"


def test_elk_radiowoord_valt_op_de_weigering_en_niet_op_de_lijst(db, monkeypatch):
    """De vijf namen uit NO_REMOTE komen niet tot de parameterlijst.

    Dat onderscheid is de hele reden dat de lijst vijf namen heeft in plaats van
    één. 'radio' is de vorm waarin onze eigen firmware de vier getallen aanbiedt;
    een node met een eigen API neemt 'set freq' als apart woord aan. Wie alleen
    'radio' weigert, weigert op de ene node en niet op de andere -- en het
    verschil is een node die van de lucht valt."""
    _lijst_van_de_node(monkeypatch)
    monkeypatch.setattr(nodeconfig, "_open",
                        lambda *a, **k: pytest.fail("mocht de node niet benaderen"))
    for sleutel in nodeconfig.NO_REMOTE:
        uit = nodeconfig.write(rep(), sleutel, "869.525")
        assert uit["ok"] is False, sleutel
        assert uit["step"] == "afstand", sleutel


def test_de_ontvangstversterking_is_geen_radiowoord():
    """'radio.rxgain' begint met hetzelfde woord en is het niet.

    Hij zet de ontvangstversterking: een node wordt er hooguit dover van en
    blijft op hetzelfde kanaal, en dat is de kant van de asymmetrie waar 'tx' ook
    staat. Een weigering op voorvoegsel zou hem meenemen, en dan zou de regel
    iets anders gaan betekenen dan ze zegt."""
    assert "radio.rxgain" not in nodeconfig.NO_REMOTE
    assert "radio.fem.rxgain" not in nodeconfig.NO_REMOTE


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
    uit = nodeconfig.write(bestaande(db), "lat", "50.5")
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
    uit = nodeconfig.write(bestaande(db), "advert.interval", "61")
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
    assert nodeconfig.write(bestaande(db), "name", "X")["ok"] is True


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
    assert nodeconfig.write(bestaande(db), "flood.max", "32", confirm="ja")["ok"] is True


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
    assert nodeconfig.write(bestaande(db), "tx", "10", confirm="DinX-Home")["ok"] is True


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
    from app import pktfilter as pf
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
                  "node_stale": False, "ha": False, "poller_seen": None,
                  # De eigen API van de node. Hier: die heeft hij niet -- deze
                  # reeks gaat over een node met onze firmware op de broker.
                  "ip_api": {"host": "", "seen": None, "fw": "", "ever": False,
                             "fresh": False, "stale_after_s": 600}},
        "cfg_route": {"can": True, "blocker": "", "host": "http://x",
                      "fw": "2.1.0", "min_fw": "2.1.0", "relayed": False,
                      "transport": "ip", "target": "", "monitor": "",
                      "max_risk": nc.RISK_CUTOFF, "options": [],
                      "why": "over HTTP naar de node zelf"},
        # De vorige schrijfactie van de monitor. Hier: er is er nooit een geweest,
        # want deze reeks gaat over de weg over IP.
        "cfg_mesh": {"ok": False, "error": "", "job": {}},
        "cfg_mesh_steps": nc.MESH_STEPS,
        # En die over het cmd-topic, om dezelfde reden leeg.
        "cfg_mqtt": {},
        "cfg_no_remote": nc.NO_REMOTE,
        "cfg_no_remote_reason": nc.NO_REMOTE_REASON,
        "cfg_transport_text": nc.TRANSPORT_TEXT,
        "cfg_blocker_text": nc.BLOCKER_TEXT,
        # De derde weg heeft zijn eigen tests (test_sensornode.py). Hier de
        # toestand die niets toont: geen adres, dus geen sectie.
        "sensor_route": None,
        "sensor_last": {"ok": False, "error": "", "at": None, "metrics": 0,
                        "channels": 0, "neighbors": 0, "host": ""},
        "sensor_acl": {"ok": False, "error": "", "data": {}},
        "sensor_interval_s": 300, "sensor_enabled": True,
        "sensor_region_fields": {}, "sensor_no_readback": {},
        "sensor_result": None,
        "cfg_params": {"ok": True, "error": "", "params": params},
        "cfg_groups": [(r, [q for q in params if q["risk"] == r])
                       for r in (nc.RISK_PLAIN, nc.RISK_WRITES, nc.RISK_CUTOFF)],
        "cfg_now": {"name": "DinX-Home", "tx": "22"},
        "cfg_result": None, "rights": None, "relay": None,
        # Het pakketfilter zit op dezelfde pagina maar heeft zijn eigen tests
        # (test_pktfilter.py). Hier de toestand die niets toont: geen
        # schrijfweg, en de node heeft nooit iets over een filter gemeld. Zo
        # gaat deze reeks over de instellingenschrijver en niets anders.
        "filter_route": {"can": False, "blocker": "old_fw", "host": "http://x",
                         "fw": "2.1.0", "min_fw": "2.3.0", "relayed": False},
        "filter_live": {"ok": False, "error": "", "filter": {}},
        "filter_seen": pf.summarise(None),
        "filter_types": pf.TYPE_NAMES,
        "filter_result": None,
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
        # Zonder het sluithaakje: een keuze kan sinds het voorvullen ook
        # 'selected' dragen, en die vlag hoort deze test niet te toetsen.
        assert f'<option value="{woord}"' in html


def test_een_getal_krijgt_de_grenzen_van_de_node_mee():
    html = _render()
    assert 'min="0" max="64"' in html      # flood.max
    assert 'min="0" max="30"' in html      # tx


def test_radio_krijgt_geen_invoervelden_maar_de_reden():
    """Van afstand geen radio-instellingen buiten tx.

    Hier stonden vier invoervelden met elk hun eigen grenzen, omdat één tekstveld
    waarin je "869.525 250 11 5" moet typen het soort veld is waarin een tikfout
    een node van de lucht haalt. Die redenering klopte en ging niet ver genoeg:
    ook een keurig ingevuld formulier kan een zender op een band zetten waar de
    antenne niet op staat, en dan is er geen weg terug die niet fysiek is.

    Dus geen velden, en wél de reden. Weglaten zonder uitleg zou de vraag
    "waarom staat de radio er niet bij" onbeantwoord laten bij iedereen die de
    lijst kent."""
    html = _render()
    for naam in ("rf", "rb", "rs", "rc"):
        assert f'name="{naam}"' not in html
    assert "Niet van afstand" in html
    assert "van de lucht" in html
    # En 'tx' blijft wél te zetten: een node die te zwak zendt is zwakker maar
    # bereikbaar, en dat is precies de asymmetrie waar deze regel op rust.
    assert 'min="0" max="30"' in html


def test_de_zwaarste_klasse_vraagt_om_de_naam_van_de_node():
    html = _render()
    assert 'placeholder="DinX-Home"' in html
    assert "de naam van de node overtypen" in html


def test_de_middelste_klasse_vraagt_een_uitdrukkelijke_bevestiging():
    html = _render()
    assert 'name="confirm" value="ja"' in html


@pytest.mark.parametrize("blocker,transport,zin", [
    ("no_credentials", "ip", "geen weblogin voor de beheerpagina van <em>deze</em>"),
    ("no_host", "ip", "geen beheeradres"),
    ("old_fw", "ip", "bestaat pas vanaf"),
    ("no_fw", "ip", "meldt geen versie"),
    # En de vier van de weg over LoRa. Ze gaan alle vier over de MONITOR, en dat
    # verschil moet uit de zin blijken: wie leest dat "deze node" geen firmware
    # heeft, gaat de verkeerde node flashen -- en dat is bij de dakrepeater een
    # ladder.
    ("no_credentials", "mesh", "beheerpagina van de <em>monitor</em>"),
    ("relay_unknown", "mesh", "die node is hier zelf niet"),
    ("no_relay_host", "mesh", "loopt via zijn"),
    ("relay_no_fw", "mesh", "meldt geen versie van onze firmware"),
    ("relay_old_fw", "mesh", "de monitor die die versie nodig heeft"),
])
def test_elke_reden_om_niet_te_kunnen_schrijven_krijgt_zijn_eigen_zin(blocker, transport, zin):
    html = _render(cfg_route={"can": False, "blocker": blocker, "host": "",
                              "fw": "2.0.0", "min_fw": "2.1.0",
                              "relayed": transport == "mesh", "transport": transport,
                              "target": DAK, "monitor": "DinX-Home",
                              "max_risk": nodeconfig.RISK_CUTOFF, "options": [],
                              "why": "geen enkele weg"},
                   relay={"name": "DinX-Home", "id": 2})
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


def test_de_weblogin_van_een_doorgestuurde_node_is_die_van_de_monitor(db, monkeypatch):
    """De ontwerpfout die dit vangt, in zijn huidige vorm.

    Zonder weblogin ligt ook de LoRa-weg dicht, en dat lijkt op de oude fout maar
    is het niet: het gaat om de login van de MONITOR, een node van onszelf. Wat de
    server nooit hoeft te kennen is een geheim van het DOEL -- die rechten horen
    bij de monitor, in zijn eigen monitorlijst, of ze bestaan niet omdat de
    overkant onze sleutel in zijn toegangslijst zette. De pagina zegt dat er dan
    ook bij; zie de sjabloontest verderop."""
    doel, _ = doorgestuurd(db)
    monkeypatch.setattr(firmware, "NODE_USER", "")
    assert nodeconfig.cfg_route(doel)["blocker"] == "no_credentials"


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
        cfg_route={"can": False, "blocker": "no_relay_host", "host": "",
                   "fw": "", "min_fw": "2.4.0", "relayed": True,
                   "transport": "mesh", "target": DAK, "monitor": "DinX-Home",
                   "max_risk": nodeconfig.RISK_CUTOFF, "options": [], "why": "geen enkele weg"},
        relay={"name": "DinX-Home", "id": 2},
        rights={"ok": True, "known": True, "mode": "acl", "diagnosis": diagnose,
                "polls": 4, "oks": 4 if diagnose == "goed" else 0,
                "heard": True, "error": ""})
    assert zin in html


def test_de_toegangslijst_wordt_als_de_betere_weg_gepresenteerd():
    html = _render(
        cfg_route={"can": False, "blocker": "no_relay_host", "host": "",
                   "fw": "", "min_fw": "2.4.0", "relayed": True,
                   "transport": "mesh", "target": DAK, "monitor": "DinX-Home",
                   "max_risk": nodeconfig.RISK_CUTOFF, "options": [], "why": "geen enkele weg"},
        relay={"name": "DinX-Home", "id": 2},
        rights={"ok": True, "known": True, "mode": "password", "diagnosis": "goed",
                "polls": 2, "oks": 2, "heard": True, "error": ""})
    assert "toegangslijst verdient de voorkeur" in html
    assert "setperm" in html


def test_een_doorgestuurde_node_vraagt_niet_om_serverinloggegevens_op_de_pagina():
    """De ontwerpfout zoals Björn hem zag: 'de server heeft geen inloggegevens'
    onder een node waarvoor de server die nooit hoeft te hebben."""
    html = _render(cfg_route={"can": False, "blocker": "no_relay_host", "host": "",
                              "fw": "", "min_fw": "2.4.0", "relayed": True,
                              "transport": "mesh", "target": DAK, "monitor": "",
                              "max_risk": nodeconfig.RISK_CUTOFF, "options": [],
                              "why": "geen enkele weg"},
                   relay=None, rights=None)
    assert "MM_FW_NODE_USER" not in html
    assert "op de rij van die monitor" in html


# --- schrijven over LoRa, via de monitor --------------------------------------
#
# De weg waarvoor dit project bestaat, en de enige die niet met de hand te
# beproeven valt: het doel is een stock MeshCore-repeater op een dak waar
# nadrukkelijk niets naartoe geschreven mag worden zolang dit in aanbouw is. Dus
# staat hier een nagebootste monitor. Wat hij nabootst is precies wat de echte
# doet: de opdracht aannemen, er even mee bezig zijn, en dan melden wat hij heeft
# TERUGGELEZEN -- nooit wat de node op het zetten antwoordde.


def klus(**over):
    """Een antwoord van GET /api/moncfg, met de velden die de firmware stuurt."""
    uit = {"seq": 1, "busy": 0, "ok": 1, "step": "", "msg": "", "key": DAK,
           "param": "tx", "asked": "17", "applied": "17", "exact": 1,
           "reboot": 0, "reply": "OK", "end": "klaar", "age": 12}
    uit.update(over)
    return uit


class NepMonitor:
    """Een monitor die doet alsof hij over LoRa schrijft.

    De POST wordt aangenomen (of geweigerd, met de reden die de echte ook geeft),
    en elke GET levert de volgende toestand uit ``toestanden``. De laatste blijft
    staan, want zo gedraagt de echte zich ook: de uitslag blijft daar tot er een
    volgende opdracht komt, en dat is waarom de server hem niet zelf hoeft te
    bewaren.
    """

    def __init__(self, *toestanden, weiger=None):
        self.toestanden = list(toestanden) or [klus()]
        self.weiger = weiger
        self.posts = []
        self.gets = 0

    def __call__(self, host, path, data=None, timeout=10):
        if data is not None:
            self.posts.append((host, path, data))
            if self.weiger is not None:
                raise urllib.error.HTTPError(
                    "u", 409, "Conflict", {}, _Fake(json.dumps(self.weiger).encode()))
            return _Antwoord({"ok": 1, "busy": 1, "step": ""})
        self.gets += 1
        return _Antwoord(self.toestanden[min(self.gets - 1, len(self.toestanden) - 1)])


@pytest.fixture
def geen_wachttijd(monkeypatch):
    """De pauze tussen twee opvragingen weg. Die is er voor een echte radio."""
    monkeypatch.setattr(nodeconfig, "MESH_POLL_S", 0)


def _mesh(db, monkeypatch, monitor):
    """De dakrepeater, met een nagebootste monitor ervoor.

    De lijst is die van de MONITOR -- want die zendt -- en hij is hier de volle
    lijst plus advert.interval, de parameter waarvan MeshCore 61 als 60 opslaat.
    Juist die maakt zichtbaar waar het teruglezen voor bestaat.
    """
    lijst = VOLLE_LIJST + [{"key": "advert.interval", "kind": "int", "lo": 60,
                            "hi": 240, "choices": "", "risk": 1, "reboot": 0}]
    monkeypatch.setattr(nodeconfig, "params",
                        lambda host, force=False: {"ok": True, "error": "",
                                                   "params": lijst, "at": 0})
    monkeypatch.setattr(nodeconfig, "_open", monitor)
    doel, _ = doorgestuurd(db)
    return doel


def test_de_opdracht_gaat_naar_de_monitor_met_het_doel_erin(db, monkeypatch, geen_wachttijd):
    """Het adres is dat van de monitor en het onderwerp is de sleutel van het
    doel. Andersom -- aankloppen bij de dakrepeater -- is precies wat niet kan."""
    nep = NepMonitor(klus())
    doel = _mesh(db, monkeypatch, nep)
    nodeconfig.write(doel, "tx", "17", confirm="JessaZH")

    host, path, data = nep.posts[0]
    assert host == "http://monitor.invalid"
    assert path == "/api/moncfg"
    assert f"key={DAK}".encode() in data
    assert b"param=tx" in data and b"value=17" in data


def test_de_uitslag_is_wat_er_teruggelezen_is_en_niet_wat_de_node_antwoordde(
        db, monkeypatch, geen_wachttijd):
    """De kern van deze weg. MeshCore antwoordt "OK" op dingen die het niet
    overgenomen heeft, en over de radio duurt het lang genoeg dat niemand het uit
    zichzelf natrekt. Dus telt alleen wat 'get' teruggeeft."""
    nep = NepMonitor(klus(param="advert.interval", asked="61", applied="60",
                          exact=0, reply="OK"))
    doel = _mesh(db, monkeypatch, nep)
    uit = nodeconfig.write(doel, "advert.interval", "61")
    assert uit["ok"] is True
    assert uit["exact"] is False
    assert uit["asked"] == "61" and uit["applied"] == "60"


def test_de_beproeving_die_niets_verandert_legt_de_hele_weg_af(
        db, monkeypatch, geen_wachttijd):
    """De verstandige eerste proef, en de enige die op de dakrepeater mag.

    Een parameter zetten op de waarde die hij al heeft oefent versturen,
    ontvangen, antwoord verwerken en teruglezen -- en verandert niets. Mislukt
    het, dan is er niets stuk. Wat deze test vastlegt is dat zo'n schrijfactie
    werkelijk de lucht in gaat en niet ergens als 'geen wijziging' wordt
    weggeoptimaliseerd; dan zou de proef niets bewijzen."""
    nep = NepMonitor(klus(param="tx", asked="17", applied="17", exact=1))
    doel = _mesh(db, monkeypatch, nep)
    uit = nodeconfig.write(doel, "tx", "17", confirm="JessaZH")
    assert nep.posts, "een no-op hoort net zo goed verstuurd te worden"
    assert uit["ok"] is True and uit["exact"] is True
    assert uit["applied"] == uit["asked"] == "17"


def test_een_set_zonder_antwoord_is_geen_mislukking(db, monkeypatch, geen_wachttijd):
    """De uitkomst die alleen over de radio bestaat, en de reden dat ze een eigen
    woord heeft. Het commando IS vertrokken; of het is aangekomen weten we niet.
    'Mislukt' zou iemand laten denken dat er niets gebeurd is, en dat is precies
    de conclusie die je op een node zonder tweede weg niet mag trekken."""
    nep = NepMonitor(klus(ok=0, step="geen_antwoord", applied="", exact=0,
                          reply="", end="geen antwoord op set"))
    doel = _mesh(db, monkeypatch, nep)
    uit = nodeconfig.write(doel, "tx", "17", confirm="JessaZH")
    assert uit["ok"] is False
    assert uit["step"] == "geen_antwoord"
    assert "niet te zien" in uit["msg"] and "uitleesronde" in uit["msg"]


def test_niets_verstuurd_is_iets_anders_dan_geen_antwoord(db, monkeypatch, geen_wachttijd):
    """De twee uitkomsten die je niet mag verwarren, en de reden dat de firmware
    er een vlag voor bijhoudt.

    Een login die onbeantwoord bleef of een volle pakketpool betekent dat er
    niets vertrokken is, en dus dat er met zekerheid niets veranderd is. Dat is
    de geruststellende van de twee, en hij hoort ook zo te klinken -- terwijl
    'geen antwoord' juist zegt dat we het níét weten. Ze op één hoop gooien laat
    iemand zich door het verkeerde geval gerust stellen."""
    nep = NepMonitor(klus(ok=0, step="niet_verstuurd", applied="", exact=0,
                          reply="", end="login onbeantwoord"))
    doel = _mesh(db, monkeypatch, nep)
    uit = nodeconfig.write(doel, "tx", "17", confirm="JessaZH")
    assert uit["ok"] is False and uit["step"] == "niet_verstuurd"
    assert "met zekerheid niets veranderd" in uit["msg"]


def test_zonder_teruglezing_heet_niets_geslaagd(db, monkeypatch, geen_wachttijd):
    """De node antwoordde op het zetten en zweeg op het teruglezen. Dan is er
    misschien iets gezet en is niet vastgesteld wat -- en dat is niet hetzelfde
    als gelukt."""
    nep = NepMonitor(klus(ok=0, step="geen_teruglezing", applied="", exact=0,
                          reply="OK", end="geen antwoord op teruglezen"))
    doel = _mesh(db, monkeypatch, nep)
    uit = nodeconfig.write(doel, "tx", "17", confirm="JessaZH")
    assert uit["ok"] is False and uit["step"] == "geen_teruglezing"
    assert "niet vastgesteld" in uit["msg"]


def test_de_monitor_weigert_en_die_reden_blijft_staan(db, monkeypatch, geen_wachttijd):
    """De monitor kent redenen die de server niet kan kennen: een uitleesronde
    die loopt, een sleutel die hij niet monitort, te kort na de vorige. Die tekst
    inslikken en 'HTTP 409' tonen zou de nuttigste zin weggooien die er is."""
    nep = NepMonitor(weiger={"ok": 0, "step": "monitor",
                             "msg": "te kort na de vorige schrijfactie"})
    doel = _mesh(db, monkeypatch, nep)
    uit = nodeconfig.write(doel, "tx", "17", confirm="JessaZH")
    assert uit["ok"] is False and uit["step"] == "monitor"
    assert "te kort na de vorige" in uit["msg"]
    assert nep.gets == 0, "een geweigerde opdracht hoeft niet nagekeken te worden"


def test_een_schrijfactie_die_blijft_lopen_meldt_dat_en_liegt_niet(
        db, monkeypatch, geen_wachttijd):
    """De server wacht niet eindeloos -- een omgekeerde proxy kapt zoiets af --
    en zegt dan dat het nog loopt in plaats van 'mislukt'. Dat kan omdat de
    uitslag op de monitor blijft staan: een herlading vindt hem alsnog."""
    monkeypatch.setattr(nodeconfig, "MESH_WAIT_S", 0)
    nep = NepMonitor(klus(busy=1, ok=0, step="bezig", applied="", exact=0))
    doel = _mesh(db, monkeypatch, nep)
    uit = nodeconfig.write(doel, "tx", "17", confirm="JessaZH")
    assert uit["ok"] is False and uit["busy"] is True and uit["step"] == "bezig"
    assert "herlaad" in uit["msg"].lower()


def test_de_risicoklassen_gelden_onverkort_over_lora(db, monkeypatch, geen_wachttijd):
    """Een schrijfweg met twee vervoermiddelen, en niet twee schrijfwegen. Alles
    wat een schrijfactie tegenhoudt staat voor de splitsing, dus 'tx' vraagt hier
    net zo goed om de naam van de node -- en op de node waar dit voor bedoeld is
    weegt dat zwaarder dan waar ook."""
    nep = NepMonitor(klus())
    doel = _mesh(db, monkeypatch, nep)
    uit = nodeconfig.write(doel, "tx", "0")
    assert uit["ok"] is False and uit["step"] == "bevestiging"
    assert "JessaZH" in uit["msg"]
    assert not nep.posts, "zonder bevestiging mag er niets de lucht in"


def test_een_waarde_buiten_de_grenzen_gaat_ook_over_lora_niet_de_lucht_in(
        db, monkeypatch, geen_wachttijd):
    nep = NepMonitor(klus())
    doel = _mesh(db, monkeypatch, nep)
    uit = nodeconfig.write(doel, "tx", "99", confirm="JessaZH")
    assert uit["ok"] is False and uit["step"] == "waarde"
    assert not nep.posts


def test_de_parameterlijst_komt_van_de_monitor(db, monkeypatch):
    """Want die zendt, en zijn tabel is wat er tussen een klik en de radio staat.
    De dakrepeater heeft geen /api/cfg en zou er ook nooit een kunnen hebben."""
    gevraagd = []
    monkeypatch.setattr(nodeconfig, "params",
                        lambda host, force=False: gevraagd.append(host) or
                        {"ok": True, "error": "", "params": VOLLE_LIJST, "at": 0})
    monkeypatch.setattr(nodeconfig, "_open",
                        lambda *a, **k: pytest.fail("mocht niet zover komen"))
    doel, _ = doorgestuurd(db)
    nodeconfig.write(doel, "tx", "99", confirm="JessaZH")     # strandt op de grenzen
    assert gevraagd == ["http://monitor.invalid"]


def test_de_teruggelezen_waarde_komt_in_onze_eigen_tabel(db, monkeypatch, geen_wachttijd):
    """Zonder dit blijft de kolom 'Nu' de oude waarde tonen naast een melding dat
    het gelukt is, tot de volgende uitleesronde -- en die kost over LoRa zendtijd
    op andermans band. Wat er komt te staan is wat er TERUGGELEZEN is."""
    nep = NepMonitor(klus(param="advert.interval", asked="61", applied="60", exact=0))
    doel = _mesh(db, monkeypatch, nep)
    nodeconfig.write(doel, "advert.interval", "61")
    rij = db.qone("SELECT value FROM repeater_cli WHERE repeater_id=? AND param=?",
                  (doel["id"], "advert.interval"))
    assert rij["value"] == "60"


def test_een_onbevestigde_schrijfactie_legt_niets_vast(db, monkeypatch, geen_wachttijd):
    """De keerzijde van de test hierboven, en de belangrijkere van de twee: bij
    'geen antwoord' weten we niet wat er staat, dus mag er hier niets komen te
    staan. Een gok in de tabel is erger dan een leeg vakje."""
    nep = NepMonitor(klus(param="tx", ok=0, step="geen_antwoord", applied="", exact=0))
    doel = _mesh(db, monkeypatch, nep)
    nodeconfig.write(doel, "tx", "17", confirm="JessaZH")
    assert db.qone("SELECT value FROM repeater_cli WHERE repeater_id=? AND param=?",
                   (doel["id"], "tx")) is None


def test_de_uitslag_wordt_bij_de_monitor_opgehaald_en_niet_hier_bewaard(db, monkeypatch):
    """``mesh_state`` bestaat zodat een herlading een handeling van een halve
    minuut alsnog laat zien. Oude firmware op de monitor is daarbij een versie en
    geen storing, net als bij /api/cfg."""
    def oud(*a, **k):
        raise urllib.error.HTTPError("u", 404, "Not Found", {}, _Fake(b""))

    monkeypatch.setattr(nodeconfig, "_open", oud)
    uit = nodeconfig.mesh_state("http://monitor.invalid")
    assert uit["ok"] is False and "2.4.0" in uit["error"]


# --- wat de pagina ervan toont ------------------------------------------------


def _mesh_render(**over):
    ctx = {
        "cfg_route": {"can": True, "blocker": "", "host": "http://monitor.invalid",
                      "fw": "2.4.0", "min_fw": "2.4.0", "relayed": True,
                      "transport": "mesh", "target": DAK, "monitor": "DinX-Home",
                      "max_risk": nodeconfig.RISK_CUTOFF, "options": [],
                      "why": "over LoRa via zijn monitor"},
        "relay": {"name": "DinX-Home", "id": 2},
    }
    ctx.update(over)
    return _render(**ctx)


def test_de_pagina_legt_de_beproeving_zonder_gevolgen_uit():
    """Wie deze weg voor het eerst gebruikt op een node die hij niet kan
    aanraken, hoort te lezen hoe hij hem toetst zonder iets te veranderen."""
    html = _mesh_render()
    assert "Beproef deze weg eerst zonder iets te veranderen" in html
    assert "waarde die er al staat" in html


def test_de_pagina_zegt_welke_node_de_nieuwe_firmware_draagt():
    """De verwarring die dit voorkomt is duur: wie denkt dat de dakrepeater
    geflasht moet worden, gaat een ladder halen voor niets."""
    html = _mesh_render()
    assert "niet op deze repeater" in html


def test_een_onbevestigde_schrijfactie_heet_op_de_pagina_geen_mislukking():
    html = _mesh_render(cfg_result={
        "ok": False, "step": "geen_antwoord", "msg": "het commando is vertrokken",
        "key": "tx", "asked": "17", "applied": "", "exact": False, "reboot": False,
        "transport": "mesh", "busy": False})
    assert "Verstuurd, maar onbevestigd" in html
    assert "Niet gezet" not in html


def test_een_lopende_schrijfactie_zegt_dat_hij_loopt():
    html = _mesh_render(cfg_result={
        "ok": False, "step": "bezig", "msg": "de monitor is er nog mee bezig",
        "key": "tx", "asked": "17", "applied": "", "exact": False, "reboot": False,
        "transport": "mesh", "busy": True})
    assert "Loopt nog" in html


def test_de_vorige_schrijfactie_van_de_monitor_staat_er_na_een_herlading():
    """Het antwoord ligt op de monitor, niet hier. Dat is de reden dat de browser
    niet hoeft te blijven wachten."""
    html = _mesh_render(cfg_mesh={"ok": True, "error": "",
                                  "job": klus(param="tx", asked="17", applied="17",
                                              age=90)})
    assert "Laatste schrijfactie over LoRa" in html
    assert "90 seconden" in html


def test_het_invoerveld_is_voorgevuld_met_wat_er_nu_staat():
    """Zo is 'zet hem op de waarde die hij al heeft' een klik, en dat is de enige
    beproeving die de hele weg aflegt zonder iets te veranderen."""
    html = _render()                       # cfg_now zet tx op 22
    assert 'value="22"' in html

# --- het derde vervoermiddel: het cmd-topic ----------------------------------
#
# De aanleiding: bij een full managed node zonder weblogin meldde de pagina dat
# een instellingswijziging niet kon. Een full managed node HEEFT per definitie
# een MQTT-verbinding, dus dat was feitelijk onjuist -- er lag een open,
# werkende weg naartoe waar niets overheen ging.
#
# Wat hieronder vastligt is de keuze uit drie, in volgorde, met de reden erbij.

# Zoals cfgSpecJson() hem schrijft. 'radio' staat er met opzet in: die parameter
# is sinds nodefirmware 2.6.0 uit de tabel, maar een node die nog ouder is meldt
# hem gewoon, en dat is precies de node waarvoor de weigering op de server nog
# nodig is.
SPEC = ('{"name":"text,0,0,1,0,0",'
        '"flood.max":"int,0,64,2,0,0",'
        '"tx":"int,0,30,3,0,0",'
        '"loop.detect":"enum,0,0,2,0,0,off|minimal|moderate|strict",'
        '"radio":"radio,0,0,3,1,0"}')


def eigen(db, **overrides):
    """Een node die zelf publiceert, met zijn parametertabel al binnen."""
    row = db.get_or_create_repeater("55d9a320a4e3", "DinX-Home")
    db.execute("UPDATE repeaters SET fw_meshmanager='2.8.0', ota_host='', "
               "source_prefix='55d9a320a4e3', cfg_spec=? WHERE id=?",
               (SPEC, row["id"]))
    out = dict(db.qone("SELECT * FROM repeaters WHERE id=?", (row["id"],)))
    out.update(overrides)
    return out


@pytest.fixture
def broker_aan(monkeypatch):
    from app import mqtt_ingest
    monkeypatch.setattr(mqtt_ingest, "can_publish", lambda: True)
    return mqtt_ingest


@pytest.fixture
def geen_login(monkeypatch):
    monkeypatch.setattr(firmware, "NODE_USER", "")
    monkeypatch.setattr(firmware, "NODE_PASS", "")


def test_zonder_weblogin_loopt_het_over_het_cmd_topic(db, broker_aan, geen_login):
    """De fout die dit alles in gang zette: "kan niet" over een node met een
    open verbinding naar deze broker."""
    route = nodeconfig.cfg_route(eigen(db))
    assert route["can"] is True
    assert route["transport"] == "mqtt"
    # En waarom, want bij drie wegen is "het is gelukt" te weinig.
    assert "MQTT" in route["why"]
    assert "weblogin" in route["why"]


def test_met_weblogin_wint_de_weg_naar_de_node_zelf(db, broker_aan):
    """De volgorde is de rangschikking: sterkste tegenpartij en beste
    teruglezing eerst. Waar HTTP kan, is er geen reden voor iets anders."""
    node = eigen(db, ota_host="http://node.invalid", fw_meshmanager="2.8.0")
    route = nodeconfig.cfg_route(node)
    assert route["transport"] == "ip"
    assert route["max_risk"] == nodeconfig.RISK_CUTOFF


def test_zonder_broker_blijft_er_niets_over_en_dat_staat_erbij(db, geen_login, monkeypatch):
    from app import mqtt_ingest
    monkeypatch.setattr(mqtt_ingest, "can_publish", lambda: False)
    route = nodeconfig.cfg_route(eigen(db))
    assert route["can"] is False
    # Beide redenen, want de ene is een instelling en de andere een storing, en
    # dat zijn verschillende handelingen.
    assert "weblogin" in route["why"]
    assert "broker" in route["why"]
    assert [o["blocker"] for o in route["options"]] == ["no_credentials", "broker_down"]


def test_te_oude_firmware_kent_het_woord_niet(db, broker_aan, geen_login):
    route = nodeconfig.cfg_route(eigen(db, fw_meshmanager="2.7.0"))
    assert route["can"] is False
    assert [o["blocker"] for o in route["options"]] == ["no_credentials", "old_fw"]


def test_een_doorgestuurde_node_heeft_geen_eigen_cmd_topic(db, broker_aan):
    """Hij publiceert niet, dus hij leest ook niets. Het topic van zijn monitor
    bestaat wel, maar daarop een schrijfactie voor een DERDE node aanbieden zou
    een tweede soort commando zijn over het kanaal met de zwakste tegenpartij."""
    doel, _ = doorgestuurd(db)
    route = nodeconfig.cfg_route(doel)
    assert route["transport"] == "mesh"
    assert [o["transport"] for o in route["options"]] == ["mesh"]


# --- het plafond van dat kanaal ----------------------------------------------

def test_de_zwaarste_klasse_gaat_niet_over_de_broker(db, broker_aan, geen_login, monkeypatch):
    """De afweging die de drie wegen van elkaar onderscheidt.

    Bij HTTP staat de weblogin van de node ertegenover, bij de mesh-weg die van
    een monitor die met eigen rechten inlogt. Hier staat "wie de broker heeft
    binnengelaten" -- op een broker met een gedeeld account is dat elke node die
    eraan meepraat. En er is geen teruglezing in hetzelfde verzoek die een fout
    meteen zichtbaar maakt. Wat een node van de lucht kan halen hoort niet langs
    de zwakste van de drie."""
    from app import mqtt_ingest
    monkeypatch.setattr(mqtt_ingest, "publish_command",
                        lambda *a, **k: pytest.fail("mocht niet vertrekken"))
    uit = nodeconfig.write(eigen(db), "tx", "22", confirm="DinX-Home")
    assert uit["ok"] is False
    assert uit["step"] == "plafond"
    assert "MM_FW_NODE_USER" in uit["msg"]


def test_de_lichtere_klassen_gaan_er_wel_langs(db, broker_aan, geen_login, monkeypatch):
    """"Nergens iets" is net zo verkeerd als "overal alles". De instellingen die
    je op een gewone dag bijstelt zijn klasse 1 en 2."""
    from app import mqtt_ingest
    node = eigen(db)
    verstuurd = {}

    def publiceer(n, cmd, setting=None, **k):
        verstuurd.update(node=n, cmd=cmd, setting=setting)
        nodeconfig.note_cfgset(n, {"seq": 1, "ok": 1, "param": setting[0],
                                   "asked": setting[1], "applied": setting[1],
                                   "exact": 1, "reboot": 0, "msg": ""})
        return True

    monkeypatch.setattr(mqtt_ingest, "publish_command", publiceer)
    monkeypatch.setattr(nodeconfig, "MQTT_POLL_S", 0.01)
    uit = nodeconfig.write(node, "flood.max", "12", confirm="ja")
    assert uit["ok"] is True and uit["exact"] is True
    assert uit["transport"] == "mqtt"
    assert verstuurd["cmd"] == "set" and verstuurd["setting"] == ("flood.max", "12")
    # En wat er teruggelezen is, staat ook in onze eigen tabel -- anders blijft
    # de kolom "Nu" de oude waarde tonen naast een melding dat het gelukt is.
    rijen = {r["param"]: r["value"] for r in db.cli_settings_for(node["id"])}
    assert rijen["flood.max"] == "12"


def test_stilte_over_de_broker_heet_geen_mislukking(db, broker_aan, geen_login, monkeypatch):
    """Het commando IS vertrokken. Of het is uitgevoerd weten we niet, en
    "mislukt" laat iemand denken dat er niets gebeurd is."""
    from app import mqtt_ingest
    monkeypatch.setattr(mqtt_ingest, "publish_command", lambda *a, **k: True)
    monkeypatch.setattr(nodeconfig, "MQTT_POLL_S", 0.01)
    monkeypatch.setattr(nodeconfig, "MQTT_WAIT_S", 0.05)
    uit = nodeconfig.write(eigen(db), "flood.max", "12", confirm="ja")
    assert uit["ok"] is False
    assert uit["step"] == "geen_antwoord"
    assert "niet te zien" in uit["msg"]


def test_een_publicatie_die_niet_vertrok_is_de_geruststellende_helft(
        db, broker_aan, geen_login, monkeypatch):
    from app import mqtt_ingest
    monkeypatch.setattr(mqtt_ingest, "publish_command", lambda *a, **k: False)
    uit = nodeconfig.write(eigen(db), "flood.max", "12", confirm="ja")
    assert uit["step"] == "niet_verstuurd"
    assert "met zekerheid niets veranderd" in uit["msg"]


def test_een_weigering_van_de_node_komt_terug_met_zijn_reden(
        db, broker_aan, geen_login, monkeypatch):
    """De node valideert zelf, en een weigering hoort niet in stilte te
    eindigen: dan is een tikfout niet te onderscheiden van een node die slaapt."""
    from app import mqtt_ingest

    def publiceer(n, cmd, setting=None, **k):
        nodeconfig.note_cfgset(n, {"seq": 7, "ok": 0, "param": setting[0],
                                   "asked": setting[1], "applied": "",
                                   "exact": 0, "reboot": 0,
                                   "msg": "Err - onbekende parameter"})
        return True

    monkeypatch.setattr(mqtt_ingest, "publish_command", publiceer)
    monkeypatch.setattr(nodeconfig, "MQTT_POLL_S", 0.01)
    uit = nodeconfig.write(eigen(db), "flood.max", "12", confirm="ja")
    assert uit["ok"] is False and uit["step"] == "node"
    assert "onbekende parameter" in uit["msg"]


# --- de radio, langs geen enkele weg -----------------------------------------

@pytest.mark.parametrize("bouw", ["ip", "mqtt", "mesh"])
def test_de_radio_wordt_van_afstand_nergens_gezet(db, monkeypatch, broker_aan, bouw):
    """De regel hangt aan de HANDELING en niet aan het kanaal.

    De asymmetrie: een verkeerde tx maakt een node zwakker maar laat hem
    bereikbaar. Een verkeerde frequentie, spreidingsfactor, coderingssnelheid of
    bandbreedte haalt hem van de lucht, en er is geen weg terug die niet fysiek
    is. Op een dak is dat het einde.

    Weigeren aan de bron en niet door het invoerveld weg te laten: een verzoek
    dat het formulier omzeilt komt hier alsnog binnen."""
    from app import mqtt_ingest
    monkeypatch.setattr(nodeconfig, "_open",
                        lambda *a, **k: pytest.fail("mocht de node niet benaderen"))
    monkeypatch.setattr(mqtt_ingest, "publish_command",
                        lambda *a, **k: pytest.fail("mocht niet vertrekken"))
    if bouw == "mesh":
        node, _ = doorgestuurd(db)
    elif bouw == "mqtt":
        monkeypatch.setattr(firmware, "NODE_USER", "")
        node = eigen(db)
    else:
        node = eigen(db, ota_host="http://node.invalid")

    uit = nodeconfig.write(node, "radio", "869.525 250 11 5", confirm=node["name"])
    assert uit["ok"] is False
    assert uit["step"] == "afstand"
    assert "van de lucht" in uit["msg"]
    assert "tx" in uit["msg"]


def test_de_firmware_biedt_de_radio_helemaal_niet_aan():
    """Crosscheck op de bron aan de overkant, want dat is de helft die het
    onomkeerbaar maakt: de server is niet het enige dat een node kan bereiken,
    en de node draagt het gevolg.

    Daar is de regel anders afgedwongen dan hier, en beter: 'radio' staat sinds
    2.6.0 niet meer in CFG_PARAMS. Die ene lijst is tegelijk wat /api/cfg
    publiceert, wat /api/moncfg aanvaardt en wat het cmd-topic doorlaat, dus
    één regel weghalen sluit alle drie tegelijk."""
    from pathlib import Path
    bron = (Path(__file__).resolve().parents[2]
            / "firmware" / "examples" / "simple_repeater" / "MeshManagerNet.cpp")
    if not bron.exists():          # de server draait ook zonder de firmwareboom
        pytest.skip("firmwarebron niet aanwezig")
    tekst = bron.read_text(encoding="utf-8", errors="replace")
    tabel = tekst[tekst.index("static const CfgParam CFG_PARAMS[] = {"):
                  tekst.index("#define CFG_PARAM_COUNT")]
    # Alleen de werkelijke rijen tellen. In het commentaar op de plek waar
    # 'radio' stond staat de regel nog uitgeschreven, zodat terugzetten één regel
    # is -- en dat is documentatie, geen tabelrij.
    rijen = [r.strip() for r in tabel.splitlines() if r.strip().startswith('{ "')]
    sleutels = [r.split('"')[1] for r in rijen]
    assert 'tx' in sleutels                    # het zendvermogen mag wel
    assert 'radio' not in sleutels             # en de rest van de radio niet


# --- de parametertabel die over MQTT binnenkomt ------------------------------

def test_de_lijst_komt_van_de_node_ook_als_er_geen_http_pad_is(db, broker_aan, geen_login):
    """Nog steeds de lijst van de node en geen tabel van hier -- alleen langs een
    tweede bron, voor het geval waarin de eerste niet bestaat."""
    node = eigen(db)
    lijst = nodeconfig.params_for(node, nodeconfig.cfg_route(node))
    assert lijst["ok"] is True
    spec = {p["key"]: p for p in lijst["params"]}
    assert spec["flood.max"]["risk"] == 2
    assert spec["flood.max"]["lo"] == 0 and spec["flood.max"]["hi"] == 64
    assert spec["loop.detect"]["choices"] == "off|minimal|moderate|strict"


def test_zonder_gemelde_lijst_geen_formulier_maar_wel_de_volgende_stap(
        db, broker_aan, geen_login):
    node = eigen(db, cfg_spec="")
    lijst = nodeconfig.params_for(node, nodeconfig.cfg_route(node))
    assert lijst["ok"] is False
    assert "instellingenronde" in lijst["error"]


def test_een_onleesbare_lijst_blokkeert_in_plaats_van_te_gokken(db, broker_aan, geen_login):
    node = eigen(db, cfg_spec="{niet eens json")
    assert nodeconfig.params_for(node, nodeconfig.cfg_route(node))["ok"] is False
