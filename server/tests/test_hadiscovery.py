"""Tests voor de Home Assistant MQTT-discovery-publisher.

Wat hier het bewaken waard is:

* **uit is uit, met de reden** -- zonder HA-broker (of met host maar zonder de
  schakelaar) hoort dit uit te staan met een zin die zegt wat er ontbreekt,
  precies zoals webpush zonder VAPID-sleutels. De app mag er niet op vallen;
* **de entiteiten kloppen** -- een sensornode levert de spanning als
  voltage-sensor, de netvoeding/wifi als binary_sensor met het juiste
  device_class, en elke ping-monitor als binary_sensor MET de kanaalnaam plus
  een responstijd-sensor. De kanaalnaam is het hele punt;
* **er wordt niets van een ander overschreven** -- alles draagt het voorvoegsel
  ``meshmanager_``;
* **een weggevallen entiteit laat geen spook achter** -- een object-id dat niet
  meer bij een node hoort, krijgt een retained "" op zijn config-topic;
* **beschikbaarheid volgt de stilte** -- een node die te lang zweeg, gaat in HA
  op offline;
* **de haak in db.ingest werkt** -- een verse meting zet het node-id in de
  wachtrij, zonder het ingest-pad op te houden.

Er wordt nergens echt naar een broker gepubliceerd: ``_client`` wordt vervangen
door een nepper die opschrijft wat hij kreeg.
"""
import json

import pytest

from app import config


@pytest.fixture
def db(tmp_path, monkeypatch):
    """Verse database per test, zoals test_webpush.py."""
    from app import db as db_module
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "test.sqlite3")
    db_module._conn = None
    yield db_module
    if db_module._conn is not None:
        db_module._conn.close()
        db_module._conn = None


class FakeClient:
    """Onthoudt elk gepubliceerd bericht als (topic, payload, retain)."""

    def __init__(self):
        self.published = []

    def publish(self, topic, payload, qos=0, retain=False):
        self.published.append((topic, payload, retain))

    def by_topic(self, topic):
        # De LAATSTE publicatie op dit topic: dat is de retained toestand die
        # HA zou zien, en de enige die zin heeft om na te rekenen.
        for t, p, r in reversed(self.published):
            if t == topic:
                return p, r
        return None


@pytest.fixture
def hd(monkeypatch):
    """De module 'aan', met een nepclient en verse tellers."""
    from app import hadiscovery
    monkeypatch.setattr(hadiscovery, "HA_MQTT_HOST", "10.10.10.100")
    monkeypatch.setattr(hadiscovery, "HA_DISCOVERY_ENABLED", True)
    hadiscovery._config_sig.clear()
    hadiscovery._companion_config_sig.clear()
    fake = FakeClient()
    monkeypatch.setattr(hadiscovery, "_client", fake)
    return hadiscovery, fake


def _sensornode(db):
    """Een sensornode met spanning, netvoeding, batterij, wifi en één monitor."""
    rep = db.get_or_create_repeater("48d7aade232b", "Sensornode")
    db.set_sensor_host(rep["id"], "10.10.30.50")
    db.ingest(rep["id"], db.utcnow(), {
        "ch1_voltage": 4.05,
        "ch2_switch": 1,     # netvoeding aanwezig
        "ch3_switch": 0,     # niet op batterij
        "ch4_switch": 1,     # wifi verbonden
        "ch5_switch": 1,     # monitor 'google' bereikbaar
        "ch5_generic": 42,   # responstijd
    }, None)
    db.set_channel_name(rep["id"], 2, "Netvoeding")
    db.set_channel_name(rep["id"], 3, "Batterijvoeding")
    db.set_channel_name(rep["id"], 4, "WiFi")
    db.set_channel_name(rep["id"], 5, "google", unit="ms")
    return db.qone("SELECT * FROM repeaters WHERE id=?", (rep["id"],))


def _repeater(db):
    """Een gewone repeater die telemetrie meldt (geen sensor_host)."""
    rep = db.get_or_create_repeater("e3d3f4d7edd0", "Dakrepeater")
    db.ingest(rep["id"], db.utcnow(), {
        "bat": 3.92, "airtime_utilization": 1.3, "noise_floor": -108,
    }, None)
    return db.qone("SELECT * FROM repeaters WHERE id=?", (rep["id"],))


# --- uit is uit, met de reden ------------------------------------------------

def test_zonder_broker_staat_het_uit_en_zegt_waarom(monkeypatch):
    from app import hadiscovery
    monkeypatch.setattr(hadiscovery, "HA_MQTT_HOST", "")
    monkeypatch.setattr(hadiscovery, "HA_DISCOVERY_ENABLED", True)
    st = hadiscovery.status()
    assert st["enabled"] is False
    assert "MM_HA_MQTT_HOST" in st["reason"]


def test_met_host_maar_zonder_schakelaar_staat_het_uit(monkeypatch):
    from app import hadiscovery
    monkeypatch.setattr(hadiscovery, "HA_MQTT_HOST", "10.10.10.100")
    monkeypatch.setattr(hadiscovery, "HA_DISCOVERY_ENABLED", False)
    st = hadiscovery.status()
    assert st["enabled"] is False
    assert "MM_HA_DISCOVERY_ENABLED" in st["reason"]


def test_met_beide_gezet_staat_het_aan(monkeypatch):
    from app import hadiscovery
    monkeypatch.setattr(hadiscovery, "HA_MQTT_HOST", "10.10.10.100")
    monkeypatch.setattr(hadiscovery, "HA_DISCOVERY_ENABLED", True)
    assert hadiscovery.status()["enabled"] is True


# --- de entiteiten -----------------------------------------------------------

def test_sensornode_levert_de_verwachte_entiteiten(db, hd):
    hadiscovery, _ = hd
    rep = _sensornode(db)
    ents = {key: (comp, cfg) for key, comp, cfg, _ in
            hadiscovery._entities_for(rep, db.latest_for(rep["id"]))}

    # Spanning -> voltage-sensor in V.
    comp, cfg = ents["ch1_voltage"]
    assert comp == "sensor"
    assert cfg["device_class"] == "voltage"
    assert cfg["unit_of_measurement"] == "V"

    # Netvoeding -> binary_sensor power; batterij -> geen device_class; wifi ->
    # connectivity.
    assert ents["ch2_switch"][0] == "binary_sensor"
    assert ents["ch2_switch"][1]["device_class"] == "power"
    assert "device_class" not in ents["ch3_switch"][1]
    assert ents["ch4_switch"][1]["device_class"] == "connectivity"

    # De ping-monitor draagt de kanaalnaam en is connectivity; de responstijd is
    # een aparte sensor in ms.
    assert ents["ch5_switch"][1]["name"] == "google"
    assert ents["ch5_switch"][1]["device_class"] == "connectivity"
    assert ents["ch5_generic"][0] == "sensor"
    assert ents["ch5_generic"][1]["unit_of_measurement"] == "ms"

    # Node online en actieve storing bestaan altijd.
    assert ents["online"][0] == "binary_sensor"
    assert ents["alert"][1]["device_class"] == "problem"


def test_elke_unique_id_draagt_het_meshmanager_voorvoegsel(db, hd):
    hadiscovery, _ = hd
    rep = _sensornode(db)
    for _key, _comp, cfg, _state in hadiscovery._entities_for(rep, db.latest_for(rep["id"])):
        assert cfg["unique_id"].startswith("meshmanager_48d7aade232b_")
        assert cfg["object_id"] == cfg["unique_id"]


def test_repeater_telemetrie_wordt_een_sensor_met_catalogus_label(db, hd):
    hadiscovery, _ = hd
    rep = _repeater(db)
    ents = {key: (comp, cfg) for key, comp, cfg, _ in
            hadiscovery._entities_for(rep, db.latest_for(rep["id"]))}
    assert ents["bat"][0] == "sensor"
    assert ents["bat"][1]["device_class"] == "voltage"
    # 'bat' heet in HA "Batterijspanning" (uit de catalogus), niet "bat".
    assert ents["bat"][1]["name"] == "Batterijspanning"
    assert ents["noise_floor"][1]["device_class"] == "signal_strength"


# --- publiceren en opruimen --------------------------------------------------

def test_publiceren_zet_config_retained_state_en_online(db, hd):
    hadiscovery, fake = hd
    rep = _sensornode(db)
    hadiscovery._publish_node(rep, db.latest_for(rep["id"]))

    node = "48d7aade232b"
    # Config-topic van de spanning: retained en geldige JSON.
    cfg = fake.by_topic(f"homeassistant/sensor/meshmanager_{node}_ch1_voltage/config")
    assert cfg is not None and cfg[1] is True
    assert json.loads(cfg[0])["device_class"] == "voltage"

    # State van de spanning.
    st = fake.by_topic(f"meshmanager/ha/{node}/ch1_voltage")
    assert st is not None and st[0] == "4.05"

    # Beschikbaarheid: online.
    av = fake.by_topic(f"meshmanager/ha/{node}/availability")
    assert av is not None and av[0] == "online"


def test_config_wordt_niet_elke_ronde_opnieuw_geschreven(db, hd):
    hadiscovery, fake = hd
    rep = _sensornode(db)
    hadiscovery._publish_node(rep, db.latest_for(rep["id"]))
    na_eerste = hadiscovery._state["config_msgs"]
    assert na_eerste > 0
    # Tweede ronde met dezelfde vorm: geen nieuwe config-berichten.
    hadiscovery._publish_node(rep, db.latest_for(rep["id"]))
    assert hadiscovery._state["config_msgs"] == na_eerste


def test_een_verdwenen_entiteit_wordt_geleegd(db, hd):
    hadiscovery, fake = hd
    rep = _sensornode(db)
    hadiscovery._publish_node(rep, db.latest_for(rep["id"]))
    node = "48d7aade232b"

    # Wis de monitor: geen ch5-metingen meer in latest.
    db.execute("DELETE FROM latest WHERE metric LIKE 'ch5_%'")
    hadiscovery._config_sig.clear()   # forceer een verse configvergelijking
    hadiscovery._publish_node(rep, db.latest_for(rep["id"]))

    leeg = fake.by_topic(f"homeassistant/binary_sensor/meshmanager_{node}_ch5_switch/config")
    assert leeg is not None
    assert leeg[0] == "" and leeg[1] is True   # retained lege payload


def test_forget_node_leegt_alles_en_zet_offline(db, hd):
    hadiscovery, fake = hd
    rep = _sensornode(db)
    hadiscovery._publish_node(rep, db.latest_for(rep["id"]))
    node = "48d7aade232b"
    fake.published.clear()

    hadiscovery.forget_node(node)

    # Minstens één config-topic geleegd, en de node op offline.
    assert any(t.endswith("/config") and p == "" and r
               for t, p, r in fake.published)
    av = fake.by_topic(f"meshmanager/ha/{node}/availability")
    assert av is not None and av[0] == "offline"


# --- beschikbaarheid en alarmen ----------------------------------------------

def test_actieve_storing_volgt_de_openstaande_alarmen(db, hd):
    hadiscovery, _ = hd
    rep = _sensornode(db)
    from app import webpush
    webpush.ensure_schema()
    db.execute("INSERT INTO alerts(repeater_id, channel, text, severity, ts, source) "
               "VALUES(?,?,?,?,?,?)", (rep["id"], 5, "google stil", "warning",
                                       db.utcnow(), "test"))
    ents = {key: state for key, _comp, _cfg, state in
            hadiscovery._entities_for(rep, db.latest_for(rep["id"]))}
    assert ents["alert"] == "1"


def test_een_stil_gevallen_node_gaat_offline(db, hd, monkeypatch):
    hadiscovery, fake = hd
    rep = _sensornode(db)
    # Zet de node ver in het verleden zodat de stiltedrempel bijt.
    db.execute("UPDATE repeaters SET last_seen=? WHERE id=?",
               ("2000-01-01T00:00:00Z", rep["id"]))
    monkeypatch.setattr(hadiscovery, "SCOPE", "sensors")
    hadiscovery._sweep()
    av = fake.by_topic("meshmanager/ha/48d7aade232b/availability")
    assert av is not None and av[0] == "offline"


# --- de haak in db.ingest ----------------------------------------------------

def test_on_ingest_zet_het_id_in_de_wachtrij(hd):
    hadiscovery, _ = hd
    # Trek de wachtrij leeg voor de zekerheid.
    while not hadiscovery._queue.empty():
        hadiscovery._queue.get_nowait()
    hadiscovery.on_ingest(7)
    assert hadiscovery._queue.get_nowait() == 7


def test_db_ingest_roept_de_geregistreerde_haak(db):
    gezien = []
    db.register_ingest_hook(gezien.append)
    try:
        rep = db.get_or_create_repeater("aabbccddeeff", "Node")
        db.ingest(rep["id"], db.utcnow(), {"bat": 4.0}, None)
        assert rep["id"] in gezien
    finally:
        db._ingest_hooks.remove(gezien.append)


# --- scope -------------------------------------------------------------------

def test_scope_sensors_neemt_alleen_sensornodes(db, hd, monkeypatch):
    hadiscovery, _ = hd
    monkeypatch.setattr(hadiscovery, "SCOPE", "sensors")
    sensor = _sensornode(db)
    _repeater(db)
    prefixes = {rep["pubkey_prefix"] for rep, _ in hadiscovery._in_scope_nodes()}
    assert prefixes == {sensor["pubkey_prefix"]}


def test_scope_monitored_neemt_ook_meldende_repeaters(db, hd, monkeypatch):
    hadiscovery, _ = hd
    monkeypatch.setattr(hadiscovery, "SCOPE", "monitored")
    _sensornode(db)
    _repeater(db)
    prefixes = {rep["pubkey_prefix"] for rep, _ in hadiscovery._in_scope_nodes()}
    assert prefixes == {"48d7aade232b", "e3d3f4d7edd0"}


# --- companions als device_tracker -------------------------------------------
#
# Dezelfde brug, maar nu een companion (opt-in via companions.ha_publish) die
# als device_tracker op de HA-kaart hoort te verschijnen. Het bewaken waard:
# alleen wie de opt-in aan heeft ÉN een positie kent wordt gepubliceerd; de
# positie reist via de json-attributen (het MQTT-device_tracker-contract); en
# een opt-in die uit gaat (of een companion die verdwijnt) laat geen spook
# achter -- retained "" op zijn config-topic.

COMP_KEY = "aabbccddeeff" + "0" * 52  # 64 hex; prefix = aabbccddeeff


def _opt_in_companion(db, key=COMP_KEY, lat=51.2, lon=5.4, batt=77):
    """Een companion met de HA-opt-in aan en een bekende positie."""
    import time as _t
    cid = db.add_companion("Björn T1000", key, "T1000-E", "", None)
    db.set_companion_location(key, lat, lon, int(_t.time()))
    if batt is not None:
        db.set_companion_batt(key, batt)
    db.set_companion_ha_publish(cid, True)
    return cid


def test_opt_in_companion_wordt_een_device_tracker(db, hd):
    hadiscovery, fake = hd
    _opt_in_companion(db)
    hadiscovery._sweep()

    oid = "meshmanager_companion_aabbccddeeff"
    cfg = fake.by_topic(f"homeassistant/device_tracker/{oid}/config")
    assert cfg is not None and cfg[1] is True          # retained
    conf = json.loads(cfg[0])
    assert conf["unique_id"] == oid and conf["object_id"] == oid
    assert conf["source_type"] == "gps"
    assert conf["device"]["identifiers"] == [oid]
    assert conf["device"]["name"] == "Björn T1000"
    assert conf["device"]["model"] == "T1000-E"
    # De positie reist via de attributen, niet de kale state.
    attrs = fake.by_topic(conf["json_attributes_topic"])
    assert attrs is not None and attrs[1] is True
    payload = json.loads(attrs[0])
    assert payload["latitude"] == 51.2 and payload["longitude"] == 5.4
    assert payload["gps_accuracy"] == hadiscovery.COMPANION_GPS_ACCURACY
    assert payload["battery_level"] == 77
    assert payload["last_seen"]                          # ISO, niet leeg
    # De kale state en de beschikbaarheid.
    st = fake.by_topic(conf["state_topic"])
    assert st is not None and st[0] == hadiscovery.COMPANION_STATE
    av = fake.by_topic("meshmanager/ha/companion_aabbccddeeff/availability")
    assert av is not None and av[0] == "online"
    assert hadiscovery._state["published_companions"] == 1


def test_zonder_batterij_geen_battery_level_attribuut(db, hd):
    hadiscovery, fake = hd
    _opt_in_companion(db, batt=None)
    hadiscovery._sweep()
    oid = "meshmanager_companion_aabbccddeeff"
    conf = json.loads(fake.by_topic(f"homeassistant/device_tracker/{oid}/config")[0])
    payload = json.loads(fake.by_topic(conf["json_attributes_topic"])[0])
    assert "battery_level" not in payload
    assert payload["latitude"] == 51.2


def test_alleen_opt_in_companions_worden_gepubliceerd(db, hd):
    """Een companion zonder opt-in, en een met opt-in maar zonder positie:
    allebei GEEN device_tracker -- de kern van de per-companion-toestemming."""
    import time as _t
    hadiscovery, fake = hd
    # Wel opt-in, wel positie: publiceren.
    _opt_in_companion(db, key="a" * 64)
    # Opt-in maar GEEN positie: overslaan (geen kapotte tracker).
    cid2 = db.add_companion("Geen fix", "b" * 64, "", "", None)
    db.set_companion_ha_publish(cid2, True)
    # Positie maar GEEN opt-in: overslaan (persoonlijk, niet vanzelf op de kaart).
    cid3 = db.add_companion("Geen opt-in", "c" * 64, "", "", None)
    db.set_companion_location("c" * 64, 1.0, 2.0, int(_t.time()))

    hadiscovery._sweep()
    assert hadiscovery._state["published_companions"] == 1
    # Alleen de eerste heeft een config-topic gekregen.
    assert fake.by_topic("homeassistant/device_tracker/meshmanager_companion_"
                         + "a" * 12 + "/config") is not None
    assert fake.by_topic("homeassistant/device_tracker/meshmanager_companion_"
                         + "b" * 12 + "/config") is None
    assert fake.by_topic("homeassistant/device_tracker/meshmanager_companion_"
                         + "c" * 12 + "/config") is None


def test_opt_in_uit_ruimt_de_tracker_op(db, hd):
    """Opt-in uit (of positie/companion weg): de eerstvolgende ronde legt een
    retained "" op het config-topic zodat HA de entiteit weggooit."""
    hadiscovery, fake = hd
    cid = _opt_in_companion(db)
    oid = "meshmanager_companion_aabbccddeeff"
    hadiscovery._sweep()
    assert fake.by_topic(f"homeassistant/device_tracker/{oid}/config")[0] != ""

    # Opt-in uit -> volgende ronde ruimt op.
    db.set_companion_ha_publish(cid, False)
    hadiscovery._sweep()
    leeg = fake.by_topic(f"homeassistant/device_tracker/{oid}/config")
    assert leeg is not None and leeg[0] == "" and leeg[1] is True
    av = fake.by_topic("meshmanager/ha/companion_aabbccddeeff/availability")
    assert av is not None and av[0] == "offline"
    assert hadiscovery._state["published_companions"] == 0


def test_verwijderde_companion_wordt_opgeruimd(db, hd):
    """Een companion die uit de lijst verdwijnt (dus ook uit de gewenste set),
    laat geen spook-tracker achter."""
    hadiscovery, fake = hd
    cid = _opt_in_companion(db)
    oid = "meshmanager_companion_aabbccddeeff"
    hadiscovery._sweep()
    db.delete_companion(cid)
    hadiscovery._sweep()
    leeg = fake.by_topic(f"homeassistant/device_tracker/{oid}/config")
    assert leeg is not None and leeg[0] == ""


def test_companion_config_niet_elke_ronde_opnieuw(db, hd):
    hadiscovery, fake = hd
    _opt_in_companion(db)
    hadiscovery._sweep()
    na_eerste = hadiscovery._state["config_msgs"]
    assert na_eerste > 0
    hadiscovery._sweep()
    assert hadiscovery._state["config_msgs"] == na_eerste
