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

from app import firmware, nodeconfig, pfstock, pktfilter


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


# --- de pagina ----------------------------------------------------------------

def _render(**over):
    """De nodepagina renderen met een filter dat wél leeft.

    Deze tak -- schrijfweg open én de node antwoordde -- is de enige die de
    regeltabellen tekent, en hij is in geen enkele andere test te zien. Een
    sjabloonfout daarin zou dus pas op de echte beheerpagina opduiken.
    """
    from app.templating import templates

    class _AllesMag(dict):
        def __getitem__(self, key):
            return type("B", (), {"allowed": True, "reason": ""})()

        def get(self, key, default=None):
            return self[key]

    ctx = {
        "site_name": "MeshManager", "user": "u", "world": "nodes",
        "rep": {"id": 1, "name": "DinX-Home", "pubkey_prefix": "55d9",
                "source_prefix": "55d9", "fw": "v1.17.0", "fw_meshmanager": "2.3.0",
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
                  "fw_meshmanager": "2.3.0", "min_fw": "1.8.0", "node_seen": None,
                  "node_stale": False, "poller": False, "poller_name": None, "poller_seen": None,
                  "poller_refresh": False, "poller_settings": False, "poller_caps": [],
                  # De eigen API van de node. Hier: die heeft hij niet -- deze
                  # reeks gaat over een node met onze firmware op de broker.
                  "ip_api": {"host": "", "seen": None, "fw": "", "ever": False,
                             "fresh": False, "stale_after_s": 600}},
        "cfg_route": {"can": False, "blocker": "no_host", "host": "",
                      "fw": "2.3.0", "min_fw": "2.1.0", "relayed": False,
                      "transport": "ip", "target": "", "monitor": "",
                      "max_risk": nodeconfig.RISK_CUTOFF,
                      "why": "geen enkele weg", "options": []},
        "cfg_params": {"ok": False, "error": "", "params": []},
        "cfg_groups": [], "cfg_now": {}, "cfg_result": None,
        # De instellingenschrijver zit op dezelfde pagina en heeft zijn eigen
        # tests (test_nodeconfig.py). Hier de toestand die niets toont.
        "cfg_mesh": {"ok": False, "error": "", "job": {}},
        "cfg_mesh_steps": nodeconfig.MESH_STEPS,
        "cfg_mqtt": {},
        "cfg_no_remote": nodeconfig.NO_REMOTE,
        "cfg_no_remote_reason": nodeconfig.NO_REMOTE_REASON,
        "cfg_transport_text": nodeconfig.TRANSPORT_TEXT,
        "cfg_blocker_text": nodeconfig.BLOCKER_TEXT,
        # De derde weg heeft zijn eigen tests (test_sensornode.py). Hier de
        # toestand die niets toont: geen adres, dus geen sectie.
        "sensor_route": None,
        "sensor_last": {"ok": False, "error": "", "at": None, "metrics": 0,
                        "channels": 0, "neighbors": 0, "host": ""},
        "sensor_acl": {"ok": False, "error": "", "data": {}},
        "sensor_interval_s": 300, "sensor_enabled": True,
        "sensor_region_fields": {}, "sensor_no_readback": {},
        "sensor_result": None,
        "rights": None, "relay": None,
        "sweep_hours": 0, "sweep_next": None, "sweep_last": None,
        "sweep_status": {"enabled": True, "min_gap_min": 15,
                         "max_per_day": 48, "today": 0},
        "rechten": _AllesMag(), "mijn_rol": "beheerder",
        "serverrechten": _AllesMag(), "audit": [],
        "filter_route": {"can": True, "blocker": "", "host": "http://x",
                         "fw": "2.3.0", "min_fw": "2.3.0", "relayed": False},
        "filter_live": {"ok": True, "error": "", "filter": dict(
            STAND, on=True,
            channels=[{"label": "publiek", "hash": "a3"}])},
        "filter_seen": pktfilter.summarise({"on": True, "passed": 10,
                                            "drop": {"rate": 4}}),
        "filter_types": pktfilter.TYPE_NAMES,
        "filter_result": None,
        # De tweede schrijfweg (stock-repeater via de poller) staat standaard
        # uit; test_de_pollertabel_* hieronder zet hem aan.
        "filter_queue": None,
        "filter_state": {},
        "filter_defaults": pfstock.STOCK_DEFAULTS,
        "filter_presets": pfstock.STOCK_PRESETS,
    }
    ctx.update(over)
    return templates.env.get_template("admin/node.html").render(ctx)


# --- de tweede schrijfweg: de tabel voor een stock-repeater --------------------

def _pollerrender(**over):
    """De nodepagina voor een DOORGESTUURDE repeater met filterpatch: geen
    IP-weg, wel een verse poller. Dat is de tak met de tabel."""
    basis = {
        "filter_route": {"can": False, "blocker": "relayed_only", "host": "",
                         "fw": "", "min_fw": "2.3.0", "relayed": True},
        "filter_queue": {"can": True, "blocker": "", "poller_name": "node-push-token",
                         "variant": "meshcore_filter"},
        "filter_state": {"on": False, "variant": "meshcore_filter",
                         "limits": {"GRP_TXT": {"hops": 32, "rate": 20, "window": 60},
                                    "TXT_MSG": {"hops": 8}},
                         "drop_types": {"GRP_TXT": {"hops": 2, "rate": 10}}},
    }
    basis.update(over)
    return _render(**basis)


def test_de_pollertabel_vult_in_wat_de_repeater_meldde():
    html = _pollerrender()
    assert "Zetten via de poller" in html
    assert "node-push-token" in html
    # De gemelde waarden staan als waarde in de velden, niet als tekst erbij.
    assert 'value="32"' in html and 'value="60"' in html
    # De standaard van die firmware staat ernaast, en de tellers per type erbij.
    assert "32 / 20 / 60" in html          # GRP_TXT-standaard uit de gids
    assert "2 hops" in html                # weggegooid op de hoplimiet
    # De voorbeeldopstellingen, letterlijk.
    assert "filter rate 05 20 60" in html


def test_wat_niet_gemeld_is_blijft_leeg_en_wordt_geen_nul():
    """De kern van dit scherm. Een veld dat we niet weten mag geen 0 tonen: dan
    zou de tabel beweren dat er geen limiet staat."""
    html = _pollerrender(filter_state={"on": False, "variant": "meshcore_filter",
                                       "limits": {"TXT_MSG": {"hops": 8}}})
    # TXT_MSG kent alleen zijn hoplimiet; de snelheidsvelden blijven leeg.
    assert 'placeholder="onbekend"' in html
    assert "nog niet gemeld" in html


def test_zonder_poller_geen_formulier_maar_een_reden():
    html = _pollerrender(filter_queue={"can": False, "blocker": "no_poller",
                                       "poller_name": None,
                                       "variant": "meshcore_filter"})
    assert "Zetten via de poller" not in html
    assert "geen verse poller" in html
    # De weg terug blijft staan: die heeft deze site niet nodig.
    assert "mesh-CLI" in html


def test_de_regeltabellen_worden_getekend_als_de_node_antwoordt():
    html = _render()
    assert 'action="/admin/repeaters/1/filter"' in html
    # Alle twaalf types, met hun naam zoals de firmware ze noemt.
    for naam in pktfilter.TYPE_NAMES:
        assert naam in html
    assert "publiek" in html


def test_dichtzetten_vraagt_de_naam_van_de_node_in_het_formulier():
    """De drempel staat ook op de server; dit gaat over of hij zichtbaar is.

    Een formulier dat om 'ja' vraagt terwijl de server de nodenaam eist, is een
    formulier dat je laat klikken en dan afwijst -- en dan typt de volgende
    persoon 'ja' in het veld waar de naam hoort.
    """
    html = _render()
    assert "DinX-Home" in html
    assert "dichtzetten" in html


def test_de_weg_terug_staat_er_ook_als_er_geen_schrijfweg_is():
    html = _render(filter_route={"can": False, "blocker": "relayed_only",
                                 "host": "", "fw": "", "min_fw": "2.3.0",
                                 "relayed": True})
    assert "filter off" in html
    assert "mesh-CLI" in html


# --- de uitsplitsing (firmware 2.6.0+) ----------------------------------------

def _blok(**extra):
    """Een filterblok zoals de firmware het publiceert, met uitsplitsing."""
    blok = {
        "on": True, "passed": 900, "exempt": 87,
        "drop": {"hops": 5, "rate": 2},
        "xr": {"04.hops": 5, "05.rate": 2},
        "rate": {"05": {"seen": 41, "cap": 2, "peak": 20, "lim": 20}},
        "ex": {"02": 87},
        "chan": [{"l": "spam", "h": "a3", "hits": 41}],
    }
    blok.update(extra)
    return blok


def test_de_uitsplitsing_wordt_op_naam_gebracht_en_niet_op_nummer():
    """De node stuurt typenummers; de namen komen uit onze eigen tabel.

    Dezelfde regel als bij FILTER_DROP_METRICS: de afzender levert getallen, wij
    bepalen hoe ze heten. Anders bepaalt een vreemde publisher welke sleutels er
    in de databank verschijnen.
    """
    from app import mqtt_ingest
    uit = mqtt_ingest._filter_breakdown(_blok())
    assert uit["xr"] == {"ADVERT": {"hops": 5}, "GRP_TXT": {"rate": 2}}
    assert uit["ex"] == {"TXT_MSG": 87}
    assert uit["rate"]["GRP_TXT"]["cap"] == 2


@pytest.mark.parametrize("rommel", [
    {"xr": {"99.hops": 5}},                    # bestaat niet als pakkettype
    {"xr": {"04.onzin": 5}},                   # bestaat niet als reden
    {"xr": {"04.hops": -1}},                   # negatieve teller
    {"xr": {"04.hops": "veel"}},               # geen getal
    {"xr": "helemaal geen dict"},
    {"rate": {"05": "geen dict"}},
    {"ex": {"04": None}},
    {"chan": "geen lijst"},
])
def test_rommel_in_de_uitsplitsing_wordt_stil_genegeerd(rommel):
    """Iedereen met brokergegevens kan op dit topic publiceren."""
    from app import mqtt_ingest
    leeg = {"on": True, "drop": {}}
    leeg.update(rommel)
    uit = mqtt_ingest._filter_breakdown(leeg)
    for sleutel in ("xr", "rate", "ex", "chan"):
        assert not uit.get(sleutel), f"{sleutel} had leeg moeten blijven"


def test_de_uitsplitsing_kan_niet_onbeperkt_groeien():
    """Een node die duizend kanalen meldt, krijgt er zestien opgeslagen."""
    from app import mqtt_ingest
    uit = mqtt_ingest._filter_breakdown({
        "chan": [{"l": f"k{i}", "h": "a3", "hits": 1} for i in range(500)],
        "ex": {f"{i:02d}": 1 for i in range(99)},
    })
    assert len(uit["chan"]) == mqtt_ingest.PF_MAX_CHANNELS
    assert len(uit["ex"]) <= len(mqtt_ingest.PF_TYPE_NAMES)


def test_een_kanaallabel_wordt_geschoond_en_niet_vertrouwd():
    """Het label komt van een node en belandt in HTML."""
    from app import mqtt_ingest
    uit = mqtt_ingest._filter_breakdown({
        "chan": [{"l": "<script>x</script>" + "a" * 60, "h": "A3", "hits": 1}],
    })
    kanaal = uit["chan"][0]
    assert "<" not in kanaal["label"] and ">" not in kanaal["label"]
    assert len(kanaal["label"]) <= mqtt_ingest.PF_MAX_LABEL
    assert kanaal["hash"] == "a3"


def test_de_druk_op_de_snelheidslimiet_wordt_een_reeks_met_een_noemer():
    """'12 keer gebeten' zegt niets zonder 'van hoeveel vensters'.

    Twee reeksen en niet zesendertig: per type maal drie velden zou het dak van
    128 metrics per bericht en de FIFO van 1000 rijen per repeater opeten. De
    verdeling per type staat in de blob, het totaal in de grafiek.
    """
    from app import mqtt_ingest
    mets = mqtt_ingest._filter_metrics(_blok())
    assert mets["filter_rate_windows"] == 41
    assert mets["filter_rate_capped"] == 2


def test_zonder_uitsplitsing_komen_er_geen_snelheidsreeksen():
    """Oudere firmware stuurt geen 'rate', en dan hoort er geen nul te staan.

    Een nul zou beweren dat er nul vensters waren; er is simpelweg niets gemeld.
    """
    from app import mqtt_ingest
    mets = mqtt_ingest._filter_metrics({"on": True, "drop": {"hops": 1}})
    assert "filter_rate_windows" not in mets
    assert "filter_rate_capped" not in mets


def test_van_een_geblokkeerd_kanaal_is_de_hash_openbaar_en_het_label_niet():
    """De knip loopt tussen een meting en een oordeel, niet tussen wel en niet.

    De hash is een byte van sha256(kanaalsleutel), en die byte staat
    onversleuteld in elk groepsbericht dat door de lucht gaat: verzwijgen
    beschermt niemand, en 'dit kanaal wordt hier geweerd' is juist wat iemand
    nodig heeft die zich afvraagt waarom zijn verkeer niet aankomt. Het label is
    de naam die ONZE beheerder aan het kanaal van een ander gaf -- geen
    waarneming, en het draagt niets wat de hash niet al draagt.
    """
    stand = {"stats": {"xr": {"ADVERT": {"hops": 5}},
                       "chan": [{"label": "spam", "hash": "a3", "hits": 41}],
                       "rate": {"GRP_TXT": {"seen": 41, "cap": 2, "lim": 20}}}}
    publiek = pktfilter.breakdown(stand, admin=False)
    beheer = pktfilter.breakdown(stand, admin=True)

    assert publiek["chan"][0]["hash"] == "a3"
    assert publiek["chan"][0]["hits"] == 41
    assert "label" not in publiek["chan"][0]
    assert beheer["chan"][0]["label"] == "spam"
    # De ingestelde limiet is een REGEL, en regels staan achter de login.
    assert "limiet" not in publiek["rate"][0]
    assert beheer["rate"][0]["limiet"] == 20
    # Wat wel openbaar is, is voor allebei gelijk.
    assert publiek["xr"] == beheer["xr"]


def test_het_aandeel_maakt_een_ruime_limiet_zichtbaar_naast_een_knellende():
    stand = {"stats": {"rate": {
        "GRP_TXT": {"seen": 4000, "cap": 12, "peak": 20},
        "ADVERT": {"seen": 14, "cap": 12, "peak": 10},
    }}}
    uit = pktfilter.breakdown(stand)
    op_naam = {r["type"]: r for r in uit["rate"]}
    assert op_naam["GRP_TXT"]["aandeel"] == 0.3
    assert op_naam["ADVERT"]["aandeel"] == 85.7
    # De knellendste staat bovenaan, want dat is de regel die aandacht vraagt.
    assert uit["rate"][0]["type"] == "ADVERT"


def test_een_afgekapte_uitsplitsing_zegt_dat_ze_afgekapt_is():
    """Een onvolledige uitsplitsing die zich voordoet als volledige is de stille
    fout die dit project niet wil."""
    from app import mqtt_ingest
    uit = mqtt_ingest._filter_breakdown(_blok(trunc=1))
    assert uit["trunc"] is True
    assert pktfilter.breakdown({"stats": uit})["trunc"] is True


def test_een_node_zonder_uitsplitsing_geeft_een_leeg_maar_geldig_antwoord():
    """Firmware ouder dan 2.6.0 meldt geen uitsplitsing, en dat is geen fout."""
    assert pktfilter.breakdown(None)["bekend"] is False
    assert pktfilter.breakdown({"on": True, "drop": {"hops": 3}})["bekend"] is False


# --- het oordeel per pakket (firmware 2.7.0+) ---------------------------------

def _rx(**extra):
    """Een rx-bericht zoals de firmware het publiceert."""
    body = {"t": 1234, "snr": 6.5, "rssi": -92, "len": 16, "raw": "00" * 16}
    body.update(extra)
    return body


def test_een_pakket_zonder_oordeel_is_niet_doorgelaten_maar_onbekend():
    """De derde toestand is een eigen antwoord en geen nette nul.

    Een pakket dat aan de node zelf gericht was, dat direct gerouteerd werd of
    waarvan het frame de parser niet haalde, bereikt allowPacketForward() niet
    eens. Firmware ouder dan 2.7.0 stuurt het veld helemaal niet. In beide
    gevallen zou 'doorgelaten' een bewering zijn die niemand gedaan heeft.
    """
    from app import mqtt_ingest
    assert mqtt_ingest._rx_verdict(_rx()) == (None, None)


def test_een_geweerd_pakket_draagt_zijn_reden():
    from app import mqtt_ingest
    assert mqtt_ingest._rx_verdict(_rx(fwd=0, why="rate")) == ("geweerd", "rate")


def test_een_doorgelaten_pakket_heeft_geen_reden():
    from app import mqtt_ingest
    assert mqtt_ingest._rx_verdict(_rx(fwd=1)) == ("doorgelaten", None)


@pytest.mark.parametrize("reden", ["onzin", "", None, 5, "RATE"])
def test_een_onbekende_reden_wordt_weggelaten_maar_het_oordeel_blijft(reden):
    """Iedereen met brokergegevens kan op dit topic publiceren.

    Het oordeel zelf blijft staan: dat een pakket geweerd is, is gemeld -- alleen
    het woord waarop is niet te vertrouwen, en dan is geen woord beter dan een
    verzonnen woord.
    """
    from app import mqtt_ingest
    assert mqtt_ingest._rx_verdict(_rx(fwd=0, why=reden)) == ("geweerd", None)


def test_het_oordeel_belandt_in_de_kolommen(db):
    from app import mqtt_ingest, packets
    frame = bytes.fromhex("00" * 16)
    pkt = packets.decode(frame)
    rid = db.insert_packet("e3d3f4d7edd0", pkt, raw="00" * 16,
                           fwd="geweerd", fwd_reason="hops")
    rij = db.qone("SELECT fwd, fwd_reason FROM packets WHERE id=?", (rid,))
    assert rij["fwd"] == "geweerd"
    assert rij["fwd_reason"] == "hops"


def test_een_pakket_van_voor_deze_kolom_blijft_leeg(db):
    """NULL en niet 'doorgelaten'. Die rijen zijn nooit gemeten, en dat is niet
    achteraf te herstellen: het oordeel staat niet in de bytes."""
    from app import packets
    pkt = packets.decode(bytes.fromhex("00" * 16))
    rid = db.insert_packet("e3d3f4d7edd0", pkt, raw="00" * 16)
    rij = db.qone("SELECT fwd, fwd_reason FROM packets WHERE id=?", (rid,))
    assert rij["fwd"] is None and rij["fwd_reason"] is None


def test_de_zoektaal_kent_het_oordeel_en_de_reden():
    """Zonder deze regel werken de plus/min-knoppen en de kolomkiezer er niet op:
    die gaan allemaal uit van search.FIELDS."""
    from app import search
    assert "filter" in search.FIELDS
    assert "reden" in search.FIELDS
    # Sorteerbaar, dus ook als kolomkop bruikbaar.
    assert "filter" in search.SORTS and "reden" in search.SORTS
    # En de kolomlijst deelt de woordenschat van de velden.
    assert "filter" in search.COLUMNS and "reden" in search.COLUMNS


def test_geweerd_is_te_zoeken(db):
    from app import packets, search
    pkt = packets.decode(bytes.fromhex("00" * 16))
    db.insert_packet("e3d3f4d7edd0", pkt, raw="00" * 16, fwd="geweerd",
                     fwd_reason="rate")
    db.insert_packet("aabbccddeeff", pkt, raw="00" * 16, fwd="doorgelaten")
    vraag = search.parse("filter:geweerd")
    rijen = db.q(f"SELECT p.id FROM packets p WHERE {vraag.sql}", vraag.params)
    assert len(rijen) == 1


# --- de optelsom voor de voorpagina -------------------------------------------

def test_de_voorpagina_telt_alleen_de_nodes_die_iets_gemeld_hebben():
    """En zegt erbij hoeveel dat er zijn.

    Een totaal over de nodes die rapporteren is geen totaal over het mesh. Zonder
    die noemer is '412 geweerd' het cijfer van één node in de kleren van een
    groep.
    """
    standen = {
        1: {"on": True, "passed": 900, "exempt": 4,
            "drop": {"hops": 5, "rate": 2}, "_updated": "2026-08-16T10:00:00Z"},
        2: {"on": False, "passed": 100, "exempt": 0,
            "drop": {}, "_updated": "2026-08-15T10:00:00Z"},
    }
    uit = pktfilter.mesh_totals(standen, [1, 2, 3, 4])
    assert uit["gemeten"] == 2
    assert uit["totaal"] == 4
    assert uit["met_filter"] == 1
    assert uit["zonder_filter"] == 1
    # 'Nooit iets gemeld' is een eigen toestand en niet hetzelfde als 'geen filter'.
    assert uit["onbekend"] == 2
    assert uit["weg"] == 7
    assert uit["door"] == 1000
    # De oudste meting waarop de som steunt, niet de nieuwste: die zou
    # suggereren dat het hele beeld van zopas is.
    assert uit["sinds"] == "2026-08-15T10:00:00Z"


def test_zonder_enige_melding_valt_het_kader_weg():
    """Een kader dat vooral zegt dat er niets te melden is, hoort er niet."""
    uit = pktfilter.mesh_totals({}, [1, 2])
    assert uit["iets_te_melden"] is False
    assert uit["onbekend"] == 2


def test_de_redenen_staan_op_volgorde_van_zwaarte():
    standen = {1: {"on": True, "drop": {"hops": 3, "rate": 90, "kanaal": 12}}}
    uit = pktfilter.mesh_totals(standen, [1])
    assert [naam for naam, _ in uit["redenen"]][0] == "over de snelheidslimiet"
    assert uit["weg"] == 105
