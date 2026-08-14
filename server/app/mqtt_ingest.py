"""MQTT subscriber: nodes publish, we write it down.

A MeshCore node keeps a single MQTT connection open and publishes over it, which
is far cheaper for an ESP32 than setting up an HTTP request every time. Two kinds
of message arrive, on two topic patterns:

``meshcore/<node_hex>/stats``
    Periodic statistics, same JSON as POST /api/v1/ingest::

        {"repeater": {"pubkey_prefix": "...", "name": "..."},
         "metrics": {...}, "neighbors": [...]}

``meshcore/<node_hex>/rx``
    One message per LoRa packet the node overheard::

        {"t": 123456, "snr": 5.25, "rssi": -92, "len": 57, "raw": "<hex frame>"}

    ``t`` is the node's own uptime counter, not a wall clock, so reception time
    is taken from the server instead.

``<node_hex>`` is the pubkey prefix of the *observing* node. The firmware sends
it uppercase; everything downstream keys on lowercase hex, so it is normalised
here.

Runs as a background task of the web application; without a broker configured it
does nothing at all.
"""
import json
import logging
import os
import threading

from . import db, packets

log = logging.getLogger("meshstats.mqtt")

MQTT_HOST = os.environ.get("MCS_MQTT_HOST", "")
MQTT_PORT = int(os.environ.get("MCS_MQTT_PORT", "1883"))
MQTT_USER = os.environ.get("MCS_MQTT_USER", "")
MQTT_PASS = os.environ.get("MCS_MQTT_PASS", "")
MQTT_TOPIC = os.environ.get("MCS_MQTT_TOPIC", "meshcore/+/stats")
MQTT_RX_TOPIC = os.environ.get("MCS_MQTT_RX_TOPIC", "meshcore/+/rx")

# A MeshCore frame tops out around 255 bytes; anything far beyond that is not a
# packet and should not be turned into a multi-megabyte bytes object.
MAX_RAW_HEX = 1024

# Retention has no scheduler of its own, so the packet firehose drives it.
PRUNE_EVERY_PACKETS = 2000

_state = {"connected": False, "messages": 0, "packets": 0, "errors": 0,
          "last_error": "", "last_msg": None, "last_packet": None}


def status() -> dict:
    """State for the admin page."""
    return {
        "enabled": bool(MQTT_HOST),
        "broker": f"{MQTT_HOST}:{MQTT_PORT}" if MQTT_HOST else None,
        "topic": MQTT_TOPIC,
        "rx_topic": MQTT_RX_TOPIC,
        **_state,
    }


def _handle_payload(raw: bytes) -> None:
    body = json.loads(raw.decode("utf-8"))
    rep = body.get("repeater") or {}
    prefix = str(rep.get("pubkey_prefix", "")).lower().strip()
    metrics = body.get("metrics")
    if not prefix or not isinstance(metrics, dict):
        raise ValueError("repeater.pubkey_prefix or metrics missing")

    row = db.get_or_create_repeater(prefix, rep.get("name"))
    ts = body.get("ts") or db.utcnow()
    db.ingest(row["id"], ts, metrics, body.get("neighbors"), force=bool(body.get("force")))
    _state["messages"] += 1
    _state["last_msg"] = ts


def _handle_rx(topic: str, raw: bytes) -> None:
    """Decode one overheard LoRa frame and store the reception."""
    parts = topic.split("/")
    observer = parts[1].lower().strip() if len(parts) >= 3 else ""
    if not observer:
        raise ValueError(f"no node prefix in topic {topic!r}")

    body = json.loads(raw.decode("utf-8"))
    hex_frame = str(body.get("raw", "")).strip()
    if not hex_frame:
        raise ValueError("raw missing")
    if len(hex_frame) > MAX_RAW_HEX:
        raise ValueError(f"raw too long ({len(hex_frame)} hex chars)")
    frame = bytes.fromhex(hex_frame)

    pkt = packets.decode(frame)
    db.insert_packet(observer, pkt, snr=body.get("snr"), rssi=body.get("rssi"),
                     length=body.get("len") or len(frame))
    _state["packets"] += 1
    _state["last_packet"] = db.utcnow()
    if _state["packets"] % PRUNE_EVERY_PACKETS == 0:
        db.prune()


def _run() -> None:
    import paho.mqtt.client as mqtt

    def on_connect(client, userdata, flags, rc, properties=None):
        if rc == 0:
            _state["connected"] = True
            _state["last_error"] = ""
            client.subscribe(MQTT_TOPIC, qos=0)
            client.subscribe(MQTT_RX_TOPIC, qos=0)
            log.info("MQTT connected to %s:%s, subscribed to %s and %s",
                     MQTT_HOST, MQTT_PORT, MQTT_TOPIC, MQTT_RX_TOPIC)
        else:
            _state["connected"] = False
            _state["last_error"] = f"connection refused (code {rc})"
            log.warning("MQTT connection refused: %s", rc)

    def on_disconnect(client, userdata, rc, properties=None, reason=None):
        _state["connected"] = False
        log.info("MQTT disconnected (%s); paho reconnects on its own", rc)

    def on_message(client, userdata, msg):
        try:
            if msg.topic.rsplit("/", 1)[-1] == "rx":
                _handle_rx(msg.topic, msg.payload)
            else:
                _handle_payload(msg.payload)
        except Exception as err:  # noqa: BLE001 - one bad message must break nothing
            _state["errors"] += 1
            _state["last_error"] = f"{type(err).__name__}: {err}"
            log.warning("MQTT message on %s skipped: %s", msg.topic, err)

    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="meshstats-ingest")
    if MQTT_USER:
        client.username_pw_set(MQTT_USER, MQTT_PASS)
    client.on_connect = on_connect
    client.on_disconnect = on_disconnect
    client.on_message = on_message
    client.reconnect_delay_set(min_delay=2, max_delay=60)

    while True:
        try:
            client.connect(MQTT_HOST, MQTT_PORT, keepalive=60)
            client.loop_forever(retry_first_connection=True)
        except Exception as err:  # noqa: BLE001
            _state["connected"] = False
            _state["last_error"] = f"{type(err).__name__}: {err}"
            log.warning("MQTT loop stopped (%s); retrying", err)
            import time
            time.sleep(10)


def start() -> None:
    """Start the subscriber in a background thread (a no-op without a broker)."""
    if not MQTT_HOST:
        log.info("No MCS_MQTT_HOST configured; MQTT ingest is off")
        return
    t = threading.Thread(target=_run, name="mqtt-ingest", daemon=True)
    t.start()
