"""Tests voor de weg die de knop vandaag aflegt: site -> broker -> node.

De keten heeft één eigenschap die alles bepaalt: publiceren zegt niets over
aankomen. De broker bewaart niets voor een node die offline is en de node
bevestigt niets terug. Wat hier vastligt is dus vooral wat er NIET gebeurt --
niet retained publiceren, niet publiceren zonder verbinding, en niets anders
versturen dan de twee woorden die de firmware aanneemt.
"""
import pytest

from app import mqtt_ingest


class FakeInfo:
    def __init__(self, rc=0):
        self.rc = rc


class FakeClient:
    """Genoeg van paho om vast te leggen wat er de deur uit gaat."""

    def __init__(self, rc=0, boom=False):
        self.published = []
        self.rc = rc
        self.boom = boom

    def publish(self, topic, payload, qos=0, retain=False):
        if self.boom:
            raise OSError("socket dicht")
        self.published.append({"topic": topic, "payload": payload,
                               "qos": qos, "retain": retain})
        return FakeInfo(self.rc)


@pytest.fixture
def broker(monkeypatch):
    """Een verbonden client, zoals na een geslaagde connect."""
    client = FakeClient()
    monkeypatch.setattr(mqtt_ingest, "_client", client)
    monkeypatch.setattr(mqtt_ingest, "MQTT_HOST", "broker.invalid")
    # Schoon geheugen per test: welk voorvoegsel een node gebruikt, wordt
    # onthouden zodra er een bericht van hem binnenkomt, en een test die dat
    # van een vorige test erft, meet iets anders dan ze denkt.
    monkeypatch.setattr(mqtt_ingest, "_seen_prefix", {})
    mqtt_ingest._state["connected"] = True
    yield client
    mqtt_ingest._state["connected"] = False


@pytest.fixture
def gehoord(monkeypatch):
    """Doet alsof een node zich op een bepaald voorvoegsel gemeld heeft."""
    def zet(node, prefix):
        mqtt_ingest._seen_prefix[node] = prefix
    return zet


def _topics(broker):
    return [m["topic"] for m in broker.published]


def test_opdracht_gaat_naar_het_cmd_topic_van_de_node(broker, gehoord):
    gehoord("e3d3f4d7edd0", "meshmanager")
    assert mqtt_ingest.publish_command("E3D3F4D7EDD0", "settings") is True
    (msg,) = broker.published
    assert msg["topic"] == "meshmanager/e3d3f4d7edd0/cmd"
    assert msg["payload"] == b"settings"


# --- twee werelden tegelijk, tijdens de hernoeming --------------------------

def test_een_node_op_het_oude_voorvoegsel_wordt_daar_aangesproken(broker, gehoord):
    # Dit is de hele reden dat de opdracht niet gewoon naar het nieuwe topic
    # gaat. Een node die nog niet geflasht is, luistert op meshcore/, en een
    # knop die "verstuurd" meldt terwijl er niemand meeleest is precies de
    # stilte waar dit project omheen gebouwd is.
    gehoord("e3d3f4d7edd0", "meshcore")
    assert mqtt_ingest.publish_command("e3d3f4d7edd0", "status") is True
    assert _topics(broker) == ["meshcore/e3d3f4d7edd0/cmd"]


def test_een_onbekende_node_krijgt_het_op_allebei(broker):
    # Nooit iets van gehoord: dan is er niets om uit te kiezen. Twee berichtjes
    # van acht bytes zijn goedkoper dan een knop die niets doet.
    assert mqtt_ingest.publish_command("aabbccddeeff", "status") is True
    assert _topics(broker) == ["meshmanager/aabbccddeeff/cmd",
                               "meshcore/aabbccddeeff/cmd"]


def test_een_vast_topic_uit_de_omgeving_blijft_vast(broker, monkeypatch):
    # Wie een eigen patroon zet, draait op een gedeelde broker onder een eigen
    # tak. Dat overrulen met onze voorvoegsels zou zo'n installatie stilleggen.
    monkeypatch.setattr(mqtt_ingest, "MQTT_CMD_TOPIC", "eigen/tak/{node}/cmd")
    assert mqtt_ingest.publish_command("e3d3f4d7edd0", "status") is True
    assert _topics(broker) == ["eigen/tak/e3d3f4d7edd0/cmd"]


def test_het_voorvoegsel_wordt_onthouden_uit_het_topic():
    # _topic_node is de enige plek waar langskomt waar een node zit; als het
    # daar niet blijft hangen, is elke opdracht daarna een gok.
    mqtt_ingest._seen_prefix.pop("55d9a320a4e3", None)
    assert mqtt_ingest._topic_node("meshcore/55d9a320a4e3/rx") == "55d9a320a4e3"
    assert mqtt_ingest._seen_prefix["55d9a320a4e3"] == "meshcore"
    assert mqtt_ingest._topic_node("meshmanager/55d9a320a4e3/stats") == "55d9a320a4e3"
    assert mqtt_ingest._seen_prefix["55d9a320a4e3"] == "meshmanager"


def test_er_wordt_naar_allebei_de_voorvoegsels_geluisterd():
    # Ging alleen de server om, dan was hij doof voor elke node die nog niet
    # geflasht is.
    assert "meshmanager/+/stats" in mqtt_ingest.MQTT_TOPICS
    assert "meshcore/+/stats" in mqtt_ingest.MQTT_TOPICS
    assert "meshmanager/+/rx" in mqtt_ingest.MQTT_RX_TOPICS
    assert "meshcore/+/rx" in mqtt_ingest.MQTT_RX_TOPICS


def test_niets_wordt_retained(broker):
    # Een retained opdracht wordt bij elke herverbinding opnieuw bezorgd: de
    # node zou dan bij elke boot en elke WiFi-hik zijn CLI uitlezen, wekenlang,
    # zonder dat iemand dat nog aan één klik koppelt.
    mqtt_ingest.publish_command("e3d3f4d7edd0", "status")
    assert broker.published[0]["retain"] is False


def test_zonder_verbinding_wordt_er_niet_gepubliceerd(broker):
    # Belangrijker dan het lijkt: de knop mag geen "verstuurd" melden omdat
    # paho de boodschap in een wachtrij stopte die bij een clean session toch
    # nooit vertrekt.
    mqtt_ingest._state["connected"] = False
    assert mqtt_ingest.publish_command("e3d3f4d7edd0", "settings") is False
    assert broker.published == []


def test_onbekende_opdracht_wordt_hier_al_geweigerd(broker):
    # De firmware weigert ze ook, maar dan is het een ronde over de radio en
    # een teller op de node. Een typefout hoort hier te stranden.
    with pytest.raises(ValueError):
        mqtt_ingest.publish_command("e3d3f4d7edd0", "reboot")
    assert broker.published == []


def test_sleutel_wordt_tot_hex_teruggebracht(broker, gehoord):
    # Het topic komt uit de database en de database uit berichten van buiten.
    # Een '+' of een '#' erin zou een topic maken dat iets heel anders raakt.
    gehoord("e3d3f4d7", "meshmanager")
    assert mqtt_ingest.publish_command("e3d3f4/#+d7", "status") is True
    assert broker.published[0]["topic"] == "meshmanager/e3d3f4d7/cmd"


def test_lege_sleutel_levert_geen_publicatie_op(broker):
    assert mqtt_ingest.publish_command("", "status") is False
    assert mqtt_ingest.publish_command(None, "status") is False
    assert broker.published == []


def test_een_stukke_socket_geeft_false_in_plaats_van_een_500(monkeypatch):
    client = FakeClient(boom=True)
    monkeypatch.setattr(mqtt_ingest, "_client", client)
    monkeypatch.setattr(mqtt_ingest, "MQTT_HOST", "broker.invalid")
    mqtt_ingest._state["connected"] = True
    try:
        assert mqtt_ingest.publish_command("e3d3f4d7edd0", "status") is False
    finally:
        mqtt_ingest._state["connected"] = False


def test_weigering_door_de_client_telt_als_niet_verstuurd(monkeypatch):
    client = FakeClient(rc=4)
    monkeypatch.setattr(mqtt_ingest, "_client", client)
    monkeypatch.setattr(mqtt_ingest, "MQTT_HOST", "broker.invalid")
    mqtt_ingest._state["connected"] = True
    try:
        assert mqtt_ingest.publish_command("e3d3f4d7edd0", "status") is False
    finally:
        mqtt_ingest._state["connected"] = False


def test_zonder_broker_is_publiceren_uitgeschakeld(monkeypatch):
    monkeypatch.setattr(mqtt_ingest, "_client", None)
    assert mqtt_ingest.can_publish() is False
    assert mqtt_ingest.publish_command("e3d3f4d7edd0", "status") is False


# --- een opdracht mét onderwerp, voor een gemonitorde repeater --------------

def test_onderwerp_reist_mee_in_de_opdracht(broker, gehoord):
    # De opdracht gaat naar de monitor, het onderwerp staat erin. Zonder dat
    # argument leest die node zijn eigen CLI uit en publiceert hij die onder de
    # naam van een ander -- precies de verwarring die dit moest oplossen.
    gehoord("55d9a320a4e3", "meshmanager")
    assert mqtt_ingest.publish_command("55d9a320a4e3", "settings",
                                       subject="E3D3F4D7EDD0") is True
    (msg,) = broker.published
    assert msg["topic"] == "meshmanager/55d9a320a4e3/cmd"
    assert msg["payload"] == b"settings e3d3f4d7edd0"


# --- de tijd zetten ---------------------------------------------------------

def test_de_tijd_gaat_als_epoch_in_seconden_mee(broker, gehoord):
    # Het formaat is niet gekozen maar overgenomen: CommonCLI::handleCommand
    # doet _atoi() op de rest van de regel en zet dat rechtstreeks in
    # setCurrentTime(). UNIX-seconden in UTC, geen datumtekst, geen
    # milliseconden.
    gehoord("e3d3f4d7edd0", "meshmanager")
    assert mqtt_ingest.publish_command("e3d3f4d7edd0", "time", epoch=1_800_000_000) is True
    (msg,) = broker.published
    assert msg["topic"] == "meshmanager/e3d3f4d7edd0/cmd"
    assert msg["payload"] == b"time 1800000000"
    assert msg["retain"] is False


def test_time_zonder_tijd_is_een_programmeerfout(broker):
    # 'time' zonder getal betekent niets en wordt aan de overkant geweigerd en
    # geteld. Hier stukgaan, bij het schrijven, en niet daar.
    with pytest.raises(ValueError):
        mqtt_ingest.publish_command("e3d3f4d7edd0", "time")
    assert broker.published == []


def test_een_commando_zonder_tijd_neemt_er_ook_geen_aan(broker):
    with pytest.raises(ValueError):
        mqtt_ingest.publish_command("e3d3f4d7edd0", "status", epoch=1_800_000_000)
    assert broker.published == []


@pytest.mark.parametrize("epoch", [0, 1, 1_600_000_000, 5_000_000_000])
def test_een_tijd_buiten_het_venster_vertrekt_niet(broker, epoch):
    # Belangrijker dan het lijkt, en de reden dat deze grens twee keer bestaat
    # (hier én in MeshManagerNet.cpp). Een node zet zijn klok alleen VOORUIT --
    # zijn adverts worden geweigerd als de tijdstempel niet stijgt -- dus een
    # tijd in 2128 is aan de overkant niet meer terug te draaien zonder er met
    # een kabel bij te gaan staan. En die overkant hangt op een dak.
    assert mqtt_ingest.publish_command("e3d3f4d7edd0", "time", epoch=epoch) is False
    assert broker.published == []


def test_een_tijd_buiten_het_venster_werpt_niet_op(broker):
    # Bewust False en geen uitzondering, in tegenstelling tot een onbekend
    # commando: dit is de weg waarlangs een kapotte SERVERKLOK binnenkomt. Dat
    # is een toestand van de machine en geen fout in de aanroep, en de beller
    # hoort hem te kunnen melden in plaats van eraan te sterven. De echte
    # bewaking staat in clocksync.py; dit is het vangnet eronder.
    assert mqtt_ingest.publish_command("e3d3f4d7edd0", "time", epoch=0) is False


def test_status_neemt_geen_onderwerp_aan(broker):
    # Een monitor kan geen statusbericht namens een ander sturen. Hier
    # stranden in plaats van aan de overkant, waar het een teller wordt die
    # niemand leest.
    with pytest.raises(ValueError):
        mqtt_ingest.publish_command("55d9a320a4e3", "status", subject="e3d3f4d7edd0")
    assert broker.published == []


def test_te_kort_onderwerp_vertrekt_niet(broker):
    # Onder de acht hextekens kunnen twee sleutels toevallig samenvallen. De
    # firmware weigert zo'n opdracht ook, maar dan is het een ronde over de
    # radio verder en ziet de pagina alleen stilte.
    assert mqtt_ingest.publish_command("55d9a320a4e3", "settings",
                                       subject="e3d3f4") is False
    assert broker.published == []


# --- 'set <param> <waarde>', sinds nodefirmware 2.5.0 ------------------------
#
# Het vierde woord, en het eerste dat een instelling verandert. Wat hier
# vastligt is uitsluitend wat er de deur uit gaat: of het gezet MÁG worden is
# een vraag van nodeconfig.write() en van de node zelf, en die hebben hun eigen
# tests. Deze laag is de postbode.


def test_een_instelling_vertrekt_als_een_commando_met_twee_woorden(broker, gehoord):
    gehoord("e3d3f4d7edd0", "meshmanager")
    assert mqtt_ingest.publish_command("e3d3f4d7edd0", "set",
                                       setting=("flood.max", "12")) is True
    (msg,) = broker.published
    assert msg["topic"] == "meshmanager/e3d3f4d7edd0/cmd"
    assert msg["payload"] == b"set flood.max 12"
    # Retained zou dit bij elke herverbinding opnieuw uitvoeren, en dan zet
    # iemand een maand later een instelling terug die hij ooit één keer koos.
    assert msg["retain"] is False


def test_een_waarde_met_spaties_blijft_heel(broker, gehoord):
    # 'name' en 'owner.info' bestaan uit weinig anders. De waarde is aan de
    # overkant alles na de parameter, dus er valt niets in te smokkelen: er is
    # geen scheider waarmee een tweede commando begint.
    gehoord("e3d3f4d7edd0", "meshmanager")
    assert mqtt_ingest.publish_command("e3d3f4d7edd0", "set",
                                       setting=("name", "Dak Noord")) is True
    assert broker.published[0]["payload"] == b"set name Dak Noord"


def test_set_zonder_instelling_werpt_op(broker):
    # Een programmeerfout en geen bedrijfsongeval: 'set' zonder parameter
    # betekent niets. Stukgaan bij het schrijven, niet in productie.
    with pytest.raises(ValueError):
        mqtt_ingest.publish_command("e3d3f4d7edd0", "set")
    assert broker.published == []


def test_een_commando_zonder_instelling_neemt_er_ook_geen_aan(broker):
    with pytest.raises(ValueError):
        mqtt_ingest.publish_command("e3d3f4d7edd0", "status", setting=("tx", "22"))
    assert broker.published == []


@pytest.mark.parametrize("param,waarde", [
    ("", "12"),                       # geen parameter
    ("flood.max", ""),                # geen waarde; geen enkele parameter neemt niets
    ("flood max", "12"),              # een spatie zou er twee argumenten van maken
    ("Flood.Max", "12"),              # de tabel aan de overkant is hoofdlettergevoelig
    ("x" * 40, "12"),                 # langer dan CFG_KEY_MAX
    ("flood.max", "y" * 60),          # langer dan CFG_VALUE_MAX
])
def test_wat_aan_de_overkant_afgekapt_zou_worden_vertrekt_hier_niet(broker, param, waarde):
    # MQTT_CMD_MAX is daar 96 byte en de tabel kapt op 28 resp. 40. Een
    # afgekapte waarde is een ÁNDERE waarde, en die hoort niet stilletjes gezet
    # te worden. Geen uitzondering maar False: dit komt uit een formulier.
    assert mqtt_ingest.publish_command("e3d3f4d7edd0", "set",
                                       setting=(param, waarde)) is False
    assert broker.published == []


def test_de_firmware_kent_hetzelfde_woord():
    """Crosscheck op de bron aan de overkant.

    COMMANDS hier en de allowlist in mqttRunCommand() moeten dezelfde vier
    woorden zijn. Ze staan op twee plaatsen omdat een tikfout hier anders een
    ronde over het netwerk kost voordat iemand hem ziet -- en twee lijsten die
    uit elkaar lopen zijn erger dan één lijst op de verkeerde plek.
    """
    from pathlib import Path
    bron = (Path(__file__).resolve().parents[2]
            / "firmware" / "examples" / "simple_repeater" / "MeshManagerNet.cpp")
    if not bron.exists():          # de server draait ook zonder de firmwareboom
        pytest.skip("firmwarebron niet aanwezig")
    tekst = bron.read_text(encoding="utf-8", errors="replace")
    for woord in mqtt_ingest.COMMANDS:
        assert f'strcmp(w, "{woord}")' in tekst, woord
