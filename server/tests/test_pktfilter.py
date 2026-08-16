"""Het pakketfilter: wat een regel aanricht, wie hem mag zetten, en de weg terug.

De rode draad hier is een andere dan bij ``test_nodeconfig``. Daar gaat het om
"de node zei OK maar deed iets anders". Hier gaat het om iets wat een node juist
zonder klagen doet: precies wat je vroeg, waarna hij er gezond uitziet en niets
meer doorstuurt. De tests hieronder leggen daarom vooral drie dingen vast.

1. De zwaarte van een handeling hangt af van wat hij blokkeert, niet van hoe het
   formulier eruitziet. ``hops 05 4`` en ``hops 05 0`` zijn hetzelfde veld en
   twee verschillende bevoegdheden.
2. De weg terug is de goedkoopste handeling. Uitzetten mag lichter dan aanzetten.
3. 'Nooit iets gemeld' en 'meldt dat er niets aanstaat' blijven verschillende
   toestanden, helemaal tot in de kolom van de vergelijkingstabel.
"""
import json
import urllib.error

import pytest

from app import firmware, nodeconfig, pktfilter


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


def rep(**overrides):
    row = {
        "id": 1, "name": "DinX-Home", "pubkey_prefix": "55d9a320a4e3",
        "fw": "v1.17.0", "fw_meshmanager": "2.3.0",
        "source_prefix": "55d9a320a4e3", "ota_host": "http://node.invalid",
    }
    row.update(overrides)
    return row


STAND = {
    "on": False, "disarmed": False, "hash": 1, "malformed": False,
    "passed": 100, "exempt": 2,
    "drop": {"type": 0, "hops": 0, "rate": 0, "hash": 0, "kanaal": 0, "misvormd": 0},
    "types": [{"id": i, "name": n, "on": True, "hops": 8, "limit": 5,
               "window": 60, "drop": 0}
              for i, n in enumerate(pktfilter.TYPE_NAMES)],
    "channels": [],
}


# --- kan er geschreven worden -------------------------------------------------

def test_firmware_zonder_filter_biedt_de_knop_niet_aan():
    route = pktfilter.filter_route(rep(fw_meshmanager="2.2.0"))
    assert not route["can"]
    assert route["blocker"] == "old_fw"
    assert route["min_fw"] == "2.3.0"


def test_een_doorgestuurde_node_krijgt_geen_schrijfweg_maar_wel_een_reden():
    """Voor de dakrepeater is dit een blijvende toestand, geen ontbrekend veld.

    Die node draait onze firmware niet en heeft geen IP-pad. De pagina hoort dat
    te zeggen en naar de mesh-CLI te wijzen, in plaats van een leeg adresveld te
    tonen alsof iemand vergeten is het in te vullen.
    """
    route = pktfilter.filter_route(rep(source_prefix="aabbccddeeff"))
    assert not route["can"]
    assert route["blocker"] == "relayed_only"


# --- wat een regel aanricht ---------------------------------------------------

@pytest.mark.parametrize("cmd,verwacht", [
    # De weg terug is de lichtste klasse. Met opzet lichter dan 'on': herstel
    # mag nooit strakker afgeschermd zijn dan de fout die het terugdraait.
    ("off", pktfilter.RISK_PLAIN),
    ("reset", pktfilter.RISK_PLAIN),
    # Ruimer maken is licht, smaller maken is merkbaar.
    ("hash 1", pktfilter.RISK_PLAIN),
    ("hash 2", pktfilter.RISK_WRITES),
    ("rate 02 0 60", pktfilter.RISK_PLAIN),
    ("rate 02 5 60", pktfilter.RISK_WRITES),
    ("malformed off", pktfilter.RISK_PLAIN),
    ("malformed on", pktfilter.RISK_WRITES),
    ("type 05 on", pktfilter.RISK_PLAIN),
    ("channel remove publiek", pktfilter.RISK_PLAIN),
    ("channel add publiek #a3", pktfilter.RISK_WRITES),
    ("on", pktfilter.RISK_WRITES),
    ("hops 05 4", pktfilter.RISK_WRITES),
    # En wat een hele categorie dichtzet is ingrijpend, hoe onschuldig het veld
    # er ook uitziet.
    ("hops 05 0", pktfilter.RISK_CUTOFF),
    ("type 05 off", pktfilter.RISK_CUTOFF),
    ("hash 3", pktfilter.RISK_CUTOFF),
])
def test_de_zwaarte_hangt_af_van_wat_de_regel_blokkeert(cmd, verwacht):
    assert pktfilter.risk_of(cmd) == verwacht


def test_een_onbekende_regel_krijgt_de_zwaarste_klasse():
    """Wie de regel niet herkent, weet ook niet wat hij aanricht."""
    assert pktfilter.risk_of("verzin maar wat") == pktfilter.RISK_CUTOFF


def test_aanzetten_weegt_zwaarder_als_er_al_een_categorale_regel_klaarstaat():
    """De klik die het verkeer stilzet is niet de klik die de regel maakte.

    ``type 05 off`` op een uitstaand filter verandert niets aan het verkeer --
    er wordt toch niet gefilterd. Het is ``filter on`` dat die regel scherp
    stelt, en dat is dus de handeling die de naam van de node hoort te vragen.
    """
    kaal = dict(STAND)
    assert pktfilter.risk_of("on", kaal) == pktfilter.RISK_WRITES

    met_regel = dict(STAND)
    met_regel["types"] = [dict(t) for t in STAND["types"]]
    met_regel["types"][5]["on"] = False
    assert pktfilter.risk_of("on", met_regel) == pktfilter.RISK_CUTOFF


def test_de_korte_vorm_uit_het_statistiekenbericht_telt_ook_mee():
    """Het bericht draagt geen typetabel maar wel de telling, en die telt.

    Anders zou dezelfde node twee antwoorden geven op dezelfde vraag,
    afhankelijk van of hij op dat moment over IP bereikbaar was.
    """
    kort = {"on": False, "hash": 1, "blocked_types": 1}
    assert pktfilter.risk_of("on", kort) == pktfilter.RISK_CUTOFF


# --- de bevestiging -----------------------------------------------------------

def test_een_ingrijpende_regel_vraagt_de_naam_van_de_node():
    node = rep()
    assert pktfilter.confirmation_for("type 05 off", node, "ja")
    assert pktfilter.confirmation_for("type 05 off", node, "DinX-Home") == ""


def test_uitzetten_vraagt_helemaal_niets():
    assert pktfilter.confirmation_for("off", rep(), "") == ""
    assert pktfilter.confirmation_for("reset", rep(), "") == ""


def test_de_bevestiging_wordt_op_de_server_afgedwongen(monkeypatch):
    """Een drempel die je met een zelfgebouwd formulier kunt overslaan is er geen.

    Deze test stuurt geen bevestiging mee en verwacht dat er niets de deur
    uitgaat -- niet dat de node het weigert.
    """
    def nooit(*args, **kwargs):
        raise AssertionError("er had niets verstuurd mogen worden")

    monkeypatch.setattr(nodeconfig, "_open", nooit)
    uit = pktfilter.write(rep(), "type 05 off", confirm="")
    assert not uit["ok"]
    assert uit["step"] == "bevestiging"


# --- de zin bij de regel ------------------------------------------------------

def test_een_regel_wordt_een_zin_en_geen_commandoregel():
    """'hops 05 0' is over een half jaar niet meer te lezen; de zin wel.

    Die zin staat in de bevestiging én in het audittrail, en dat is precies waar
    iemand hem nodig heeft: op het moment van klikken, en op het moment dat
    iemand terugzoekt wie het gedaan heeft.
    """
    assert "GRP_TXT" in pktfilter.describe("hops 05 0")
    assert "niet meer doorsturen" in pktfilter.describe("hops 05 0")
    assert "AANZETTEN" in pktfilter.describe("on")
    assert "standaard" in pktfilter.describe("reset")


# --- schrijven ----------------------------------------------------------------

class _Antwoord:
    def __init__(self, payload):
        self._payload = json.dumps(payload).encode()

    def read(self):
        return self._payload

    # HTTPError behandelt zijn body als een bestand en sluit hem bij het
    # opruimen; zonder dit klaagt de opruimer luid over een test die slaagde.
    def close(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def test_het_antwoord_draagt_de_stand_na_afloop(monkeypatch):
    """Niet wat er gevraagd is, maar wat er ná afloop in de node staat.

    Zelfde reden als bij de instellingenschrijver: "OK" is geen bewijs. Hier is
    dat extra concreet, want een filterregel die net iets anders uitpakt dan
    bedoeld is een regel die verkeer weggooit dat je wilde houden.
    """
    na = dict(STAND, on=True)
    monkeypatch.setattr(nodeconfig, "_open",
                        lambda *a, **k: _Antwoord({"ok": 1, "msg": "filter AAN",
                                                   "state": na}))
    uit = pktfilter.write(rep(), "on", confirm="ja")
    assert uit["ok"]
    assert uit["state"]["on"] is True
    assert uit["wat"] == "het pakketfilter AANZETTEN"


def test_de_foutmelding_van_de_node_blijft_staan(monkeypatch):
    """Bij een 400 antwoordt de node met JSON, en juist dan staat erin wat er mis was."""
    def kapot(*a, **k):
        raise urllib.error.HTTPError(
            "http://node.invalid/api/filter", 400, "Bad Request", {},
            _Antwoord({"ok": 0, "msg": "de padhash is 1, 2 of 3 byte"}))

    monkeypatch.setattr(nodeconfig, "_open", kapot)
    uit = pktfilter.write(rep(), "hash 2", confirm="ja")
    assert not uit["ok"]
    assert "1, 2 of 3 byte" in uit["msg"]


def test_zonder_schrijfweg_gaat_er_niets_de_deur_uit(monkeypatch):
    def nooit(*args, **kwargs):
        raise AssertionError("er had niets verstuurd mogen worden")

    monkeypatch.setattr(nodeconfig, "_open", nooit)
    uit = pktfilter.write(rep(fw_meshmanager="2.2.0"), "off")
    assert not uit["ok"]
    assert uit["step"] == "route"


# --- tonen --------------------------------------------------------------------

def test_nooit_gemeld_en_niets_aan_blijven_verschillende_toestanden():
    """De vraag is "staat er ergens een filter aan dat ik vergeten ben".

    Die is onbeantwoordbaar als een node zonder filterfirmware er hetzelfde
    uitziet als een node die meldt dat er niets aanstaat.
    """
    onbekend = pktfilter.summarise(None)
    assert not onbekend["bekend"]
    assert onbekend["tekst"] == "onbekend"

    uit = pktfilter.summarise({"on": False, "drop": {}})
    assert uit["bekend"]
    assert uit["tekst"] == "uit"


def test_veilige_modus_is_een_eigen_toestand():
    """De node liet het filter zelf uit; de regels staan er nog.

    Dat is iets anders dan een filter dat iemand heeft uitgezet, en de pagina
    hoort dat te zeggen -- anders zet iemand de regels opnieuw op een node die
    ze bij de volgende schone start alsnog gaat handhaven.
    """
    stand = pktfilter.summarise({"on": False, "disarmed": True, "drop": {}})
    assert "veilige modus" in stand["tekst"]


def test_de_redenen_staan_op_volgorde_van_hoeveel_ze_weggooien():
    stand = pktfilter.summarise({
        "on": True, "passed": 10,
        "drop": {"hops": 2, "rate": 40, "kanaal": 0, "misvormd": 7},
    })
    assert stand["weg"] == 49
    assert [naam for naam, _ in stand["redenen"]][0] == "over de snelheidslimiet"
    # Een reden zonder weggegooide pakketten hoort niet in de lijst: een tabel
    # met zes nullen erin verbergt de ene rij die niet nul is.
    assert all(aantal for _, aantal in stand["redenen"])


# --- opslag en de tabel -------------------------------------------------------

def test_de_stand_overleeft_en_komt_terug_zoals_hij_erin_ging(db):
    db.get_or_create_repeater("55d9a320a4e3", "DinX-Home")
    rid = db.qone("SELECT id FROM repeaters")["id"]
    db.upsert_filter_state(rid, {"on": True, "drop": {"hops": 3}}, "55d9a320a4e3")
    terug = db.filter_state_for(rid)
    assert terug["on"] is True
    assert terug["drop"]["hops"] == 3
    assert terug["_source"] == "55d9a320a4e3"


def test_een_node_zonder_filterbericht_geeft_none(db):
    db.get_or_create_repeater("55d9a320a4e3", "DinX-Home")
    rid = db.qone("SELECT id FROM repeaters")["id"]
    assert db.filter_state_for(rid) is None


# --- ingest -------------------------------------------------------------------

def test_de_tellers_gaan_als_gewone_metrics_mee(db):
    """Zo tekenen ze in de grafieken en verouderen ze met dezelfde bewaartermijn.

    De alternatieve weg -- een eigen tabel met een eigen grafiek -- zou een
    tweede stelsel zijn voor zes getallen, met een eigen bewaartermijn die op
    een dag uit de pas gaat lopen met de rest.
    """
    from app import mqtt_ingest
    mets = mqtt_ingest._filter_metrics({
        "on": True, "passed": 900, "exempt": 4,
        "drop": {"hops": 5, "rate": 2, "kanaal": 0},
    })
    assert mets["filter_drop_hops"] == 5
    assert mets["filter_dropped"] == 7
    assert mets["filter_passed"] == 900
    assert mets["filter_on"] == 1.0


def test_onzin_in_de_tellers_wordt_genegeerd():
    """Iedereen met brokergegevens kan op dit topic publiceren."""
    from app import mqtt_ingest
    mets = mqtt_ingest._filter_metrics({
        "drop": {"hops": -1, "rate": "veel", "kanaal": None, "misvormd": 3},
    })
    assert "filter_drop_hops" not in mets
    assert "filter_drop_rate" not in mets
    assert mets["filter_drop_malformed"] == 3


def test_een_node_meldt_alleen_zijn_eigen_filter(db, caplog):
    """Een filterblok over iemand anders kan niet kloppen, dus het gaat niet mee.

    De firmware publiceert alleen zijn eigen filter; een gemonitorde repeater
    vertelt zijn filterstand nergens over de radio. Voor metrics is 'niet over
    jezelf' normaal -- een node mag cijfers doorgeven over wat hij monitort --
    maar hier niet, en het gaat net over de instelling waarmee je een node
    onopvallend nutteloos maakt.
    """
    from app import mqtt_ingest
    row = db.get_or_create_repeater("aabbccddeeff", "Dakrepeater")
    mqtt_ingest._handle_filter(row, "55d9a320a4e3", "aabbccddeeff", {"on": True})
    assert db.filter_state_for(row["id"]) is None


def test_de_eigen_stand_wordt_wel_bewaard(db):
    from app import mqtt_ingest
    row = db.get_or_create_repeater("55d9a320a4e3", "DinX-Home")
    mqtt_ingest._handle_filter(row, "55d9a320a4e3", "55d9a320a4e3",
                               {"on": True, "hash": 2, "channels": 1,
                                "drop": {"hops": 9}})
    bewaard = db.filter_state_for(row["id"])
    assert bewaard["on"] is True
    assert bewaard["hash"] == 2
    assert bewaard["drop"]["hops"] == 9
