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
    mqtt_ingest._state["connected"] = True
    yield client
    mqtt_ingest._state["connected"] = False


def test_opdracht_gaat_naar_het_cmd_topic_van_de_node(broker):
    assert mqtt_ingest.publish_command("E3D3F4D7EDD0", "settings") is True
    (msg,) = broker.published
    assert msg["topic"] == "meshcore/e3d3f4d7edd0/cmd"
    assert msg["payload"] == b"settings"


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


def test_sleutel_wordt_tot_hex_teruggebracht(broker):
    # Het topic komt uit de database en de database uit berichten van buiten.
    # Een '+' of een '#' erin zou een topic maken dat iets heel anders raakt.
    assert mqtt_ingest.publish_command("e3d3f4/#+d7", "status") is True
    assert broker.published[0]["topic"] == "meshcore/e3d3f4d7/cmd"


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

def test_onderwerp_reist_mee_in_de_opdracht(broker):
    # De opdracht gaat naar de monitor, het onderwerp staat erin. Zonder dat
    # argument leest die node zijn eigen CLI uit en publiceert hij die onder de
    # naam van een ander -- precies de verwarring die dit moest oplossen.
    assert mqtt_ingest.publish_command("55d9a320a4e3", "settings",
                                       subject="E3D3F4D7EDD0") is True
    (msg,) = broker.published
    assert msg["topic"] == "meshcore/55d9a320a4e3/cmd"
    assert msg["payload"] == b"settings e3d3f4d7edd0"


# --- de tijd zetten ---------------------------------------------------------

def test_de_tijd_gaat_als_epoch_in_seconden_mee(broker):
    # Het formaat is niet gekozen maar overgenomen: CommonCLI::handleCommand
    # doet _atoi() op de rest van de regel en zet dat rechtstreeks in
    # setCurrentTime(). UNIX-seconden in UTC, geen datumtekst, geen
    # milliseconden.
    assert mqtt_ingest.publish_command("e3d3f4d7edd0", "time", epoch=1_800_000_000) is True
    (msg,) = broker.published
    assert msg["topic"] == "meshcore/e3d3f4d7edd0/cmd"
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
    # (hier én in MeshStatsNet.cpp). Een node zet zijn klok alleen VOORUIT --
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
