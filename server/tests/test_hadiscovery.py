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
