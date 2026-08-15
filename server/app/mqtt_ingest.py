"""MQTT subscriber: nodes publish, we write it down.

A MeshCore node keeps a single MQTT connection open and publishes over it, which
is far cheaper for an ESP32 than setting up an HTTP request every time. Two kinds
of message arrive, on two topic patterns:

``meshcore/<node_hex>/stats``
    Periodic statistics, same JSON as POST /api/v1/ingest::

        {"repeater": {"pubkey_prefix": "...", "name": "..."},
         "metrics": {...}, "neighbors": [...], "settings": {...}}

    ``settings`` is the node's own CLI configuration (name, role, radio, freq,
    tx, advert intervals, lat/lon, region...), swept once a day. It rides
    along here rather than on a topic of its own on purpose: this subscriber
    listens to exactly two patterns, so a third topic would have been accepted
    by the broker and then dropped on the floor unread -- which is precisely how
    the monitored repeaters went missing once before. See ``_handle_settings``
    for why only a node's own settings are taken.

``meshcore/<node_hex>/rx``
    One message per LoRa packet the node overheard::

        {"t": 123456, "snr": 5.25, "rssi": -92, "len": 57, "raw": "<hex frame>"}

    ``t`` is the node's own uptime counter, not a wall clock, so reception time
    is taken from the server instead.

``<node_hex>`` is the pubkey prefix of the *observing* node. The firmware sends
it uppercase; everything downstream keys on lowercase hex, so it is normalised
here.

One topic goes the other way::

    meshcore/<node_hex>/cmd

A single word -- ``settings`` or ``status`` -- asking that node to read its CLI
parameters now, or to publish a statistics message now. It exists because the
admin page's "fetch settings" button used to write into a queue that only the
Home Assistant integration ever emptied, so pulling Home Assistant out of the
chain left a button that did nothing while the page kept showing values from the
node's last daily sweep. The node answers on the ordinary ``stats`` topic, so
nothing else in this file changes.

That the firmware accepts only those two words is not a detail: this topic is
reachable by anyone holding broker credentials, and the repeaters this serves
hang on roofs. See ``publish_command`` for why nothing is retained here, and
``MeshStatsNet.cpp`` for why nothing else is accepted there.

Identity: topic versus payload
------------------------------
The topic names the node that **published** the message. The payload names the
repeater the message is **about**. Those are usually the same node reporting on
itself, but they are allowed to differ, because a node also forwards statistics
for other repeaters it monitors -- rejecting a mismatch would break that the day
it ships.

So the rule here is: never take the publisher's identity from the payload, and
never silently fold the two together.

- No ``repeater.pubkey_prefix`` in the payload means the node is talking about
  itself, and the topic supplies the subject.
- When both are present the payload picks the subject, and the topic prefix is
  stored on the repeater row as ``source_prefix``. A repeater that starts
  arriving through an unfamiliar node is then a visible fact on the admin page
  rather than an invisible one.

This bounds the damage but does not end it: with one shared broker account, any
client holding those credentials can still publish under any node's topic, so
the topic is only as trustworthy as the account behind it. The fix belongs on
the broker -- one MQTT user per node, each restricted by ACL to its own topic
prefix, which turns the topic into something the broker enforces. See
``mosquitto/acl`` and ``docs/mqtt.md``.
"""
import json
import logging
import os
import re
import threading

from . import db, packets

log = logging.getLogger("meshstats.mqtt")

MQTT_HOST = os.environ.get("MCS_MQTT_HOST", "")
MQTT_PORT = int(os.environ.get("MCS_MQTT_PORT", "1883"))
MQTT_USER = os.environ.get("MCS_MQTT_USER", "")
MQTT_PASS = os.environ.get("MCS_MQTT_PASS", "")
MQTT_TOPIC = os.environ.get("MCS_MQTT_TOPIC", "meshcore/+/stats")
MQTT_RX_TOPIC = os.environ.get("MCS_MQTT_RX_TOPIC", "meshcore/+/rx")
# The one topic this side publishes on. ``{node}`` is the publishing node's own
# pubkey prefix, exactly as it appears in the two topics above, so a broker ACL
# can bind a node's read permission to the same prefix as its write permission.
MQTT_CMD_TOPIC = os.environ.get("MCS_MQTT_CMD_TOPIC", "meshcore/{node}/cmd")

# Everything the firmware accepts, and nothing more. Kept here as well so a
# typo on this side is refused before it costs a round trip, and so the list is
# readable next to the code that sends it -- MeshStatsNet.cpp, mqttRunCommand().
COMMANDS = ("settings", "status")

# A MeshCore frame tops out around 255 bytes; anything far beyond that is not a
# packet and should not be turned into a multi-megabyte bytes object.
MAX_RAW_HEX = 1024

# Retention has no scheduler of its own, so the packet firehose drives it.
PRUNE_EVERY_PACKETS = 2000

_state = {"connected": False, "messages": 0, "packets": 0, "errors": 0,
          "last_error": "", "last_msg": None, "last_packet": None,
          "commands": 0}

# The live client, so the request handlers can publish over the connection the
# subscriber already holds. None until the background thread has built one.
_client = None


def status() -> dict:
    """State for the admin page."""
    return {
        "enabled": bool(MQTT_HOST),
        "broker": f"{MQTT_HOST}:{MQTT_PORT}" if MQTT_HOST else None,
        "topic": MQTT_TOPIC,
        "rx_topic": MQTT_RX_TOPIC,
        "cmd_topic": MQTT_CMD_TOPIC,
        **_state,
    }


def can_publish() -> bool:
    """Whether a command sent right now would actually leave this machine."""
    return bool(MQTT_HOST) and _client is not None and bool(_state["connected"])


def publish_command(node: str, command: str) -> bool:
    """Ask one node to do something now. False when nothing was sent.

    Returns whether the message left, never whether it arrived. Deliberately
    QoS 0 and ``retain=False``:

    - QoS 0 because there is nothing to gain from the alternative. The client
      connects with a clean session, so the broker queues nothing for a node
      that is offline; a higher QoS would only confirm delivery to the broker,
      which is not the question anyone is asking. A node asleep on its solar
      budget simply misses the message, and the page has to say so rather than
      pretend the command is on its way.
    - retain=False because a retained command is redelivered on every reconnect.
      The node would sweep its CLI on every boot and after every WiFi drop, for
      as long as the message sat on the broker, and nobody would connect that to
      a button pressed once, weeks earlier.
    """
    if command not in COMMANDS:
        raise ValueError(f"unknown command {command!r}")
    node = re.sub(r"[^0-9a-f]", "", str(node or "").lower())
    if not node:
        return False
    if not can_publish():
        return False
    topic = MQTT_CMD_TOPIC.format(node=node)
    try:
        info = _client.publish(topic, command.encode(), qos=0, retain=False)
    except Exception as err:  # noqa: BLE001 - a dead socket must not 500 the page
        log.warning("Opdracht %s naar %s niet verstuurd: %s", command, node, err)
        return False
    if info.rc != 0:
        log.warning("Opdracht %s naar %s geweigerd door de client (rc %s)",
                    command, node, info.rc)
        return False
    _state["commands"] += 1
    log.info("Opdracht '%s' gepubliceerd voor node %s", command, node)
    return True


def _topic_node(topic: str) -> str:
    """Publishing node from ``meshcore/<node_hex>/<kind>``."""
    parts = topic.split("/")
    node = parts[1].lower().strip() if len(parts) >= 3 else ""
    if not node:
        raise ValueError(f"no node prefix in topic {topic!r}")
    return node


def _handle_payload(topic: str, raw: bytes) -> None:
    publisher = _topic_node(topic)
    body = json.loads(raw.decode("utf-8"))
    rep = body.get("repeater") or {}
    # Subject defaults to the publisher: a node reporting on itself does not
    # have to repeat its own prefix in the payload.
    subject = str(rep.get("pubkey_prefix", "")).lower().strip() or publisher
    metrics = body.get("metrics")
    if not isinstance(metrics, dict):
        raise ValueError("metrics missing")

    row = db.get_or_create_repeater(subject, rep.get("name"))
    ts = body.get("ts") or db.utcnow()
    db.ingest(row["id"], ts, metrics, body.get("neighbors"), force=bool(body.get("force")))
    db.record_source(row["id"], publisher)
    db.record_firmware(row["id"], rep.get("fw"), rep.get("fw_meshstats"))
    if subject != publisher:
        log.info("stats for %s relayed by node %s", subject, publisher)
    settings = body.get("settings")
    if isinstance(settings, dict):
        _handle_settings(row, publisher, settings)
    _state["messages"] += 1
    _state["last_msg"] = ts


# A node has around fifteen CLI parameters. The cap is not about them; it is so
# that a publisher cannot turn one message into thousands of rows.
MAX_SETTINGS = 64


def _clean_settings(values: dict) -> dict:
    """Drop the parameters the node could not read.

    Firmware omits a parameter it failed to fetch rather than sending it empty,
    so an empty value that does arrive carries no information -- and writing it
    would replace a value we already know with nothing. Omission is safe by
    itself (upsert_cli_settings only touches the keys it is given), so this only
    has to catch the empty ones.
    """
    out = {}
    for key, value in values.items():
        name = str(key).strip()
        if not name or value is None:
            continue
        if isinstance(value, str) and not value.strip():
            continue
        out[name] = value
        if len(out) >= MAX_SETTINGS:
            break
    return out


def _handle_settings(row, publisher: str, values: dict) -> None:
    """Store CLI settings that rode along with a statistics message.

    Only from a node reporting on **itself**, which is the one place this
    departs from how statistics are treated. Statistics may legitimately be
    relayed -- a node forwards figures about repeaters it monitors -- but
    settings describe the publisher's own configuration, and the topic is the
    only part of a message the broker can be made to enforce. Taking relayed
    settings would let any client holding the shared broker credentials rewrite
    another repeater's settings page, and the firmware only ever sends its own,
    so refusing the rest costs nothing real.

    Identity is compared through the repeater row rather than by string: the
    topic and the payload may spell the same key at different lengths.
    """
    owner = db.find_repeater(publisher)
    if owner is None or owner["id"] != row["id"]:
        log.info("settings for %s published by %s ignored: not its own",
                 row["slug"], publisher)
        return
    clean = _clean_settings(values)
    if not clean:
        return
    # prune=False: this sweep leaves out what it could not read, and dropping a
    # parameter because one sweep missed it is the same as overwriting it with
    # nothing.
    db.upsert_cli_settings(row["id"], clean, prune=False)
    log.info("CLI settings updated for %s (%d parameters)", row["slug"], len(clean))


def _handle_rx(topic: str, raw: bytes) -> None:
    """Decode one overheard LoRa frame and store the reception."""
    observer = _topic_node(topic)
    body = json.loads(raw.decode("utf-8"))
    hex_frame = str(body.get("raw", "")).strip()
    if not hex_frame:
        raise ValueError("raw missing")
    if len(hex_frame) > MAX_RAW_HEX:
        raise ValueError(f"raw too long ({len(hex_frame)} hex chars)")
    frame = bytes.fromhex(hex_frame)

    pkt = packets.decode(frame)
    db.insert_packet(observer, pkt, snr=body.get("snr"), rssi=body.get("rssi"),
                     length=body.get("len") or len(frame), raw=hex_frame)
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
                _handle_payload(msg.topic, msg.payload)
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
    # One connection for both directions. A second client for publishing would
    # need its own credentials, its own reconnect loop and its own client id --
    # and paho's publish() is thread-safe, so the request handlers can use this
    # one from their own threads.
    global _client
    _client = client

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
