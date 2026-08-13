"""MQTT-abonnee: nodes publiceren hun statistieken, wij schrijven ze weg.

Een MeshCore-node houdt één MQTT-verbinding open en publiceert daarop zijn
statistieken. Dat is voor een ESP32 veel lichter dan telkens een HTTP-verzoek
opzetten. De payload is dezelfde JSON als bij POST /api/v1/ingest:

    {"repeater": {"pubkey_prefix": "...", "name": "..."},
     "metrics": {...}, "neighbors": [...]}

Draait als achtergrondtaak van de webapplicatie; zonder broker doet hij niets.
"""
import json
import logging
import os
import threading

from . import db

log = logging.getLogger("meshstats.mqtt")

MQTT_HOST = os.environ.get("MCS_MQTT_HOST", "")
MQTT_PORT = int(os.environ.get("MCS_MQTT_PORT", "1883"))
MQTT_USER = os.environ.get("MCS_MQTT_USER", "")
MQTT_PASS = os.environ.get("MCS_MQTT_PASS", "")
MQTT_TOPIC = os.environ.get("MCS_MQTT_TOPIC", "meshcore/+/stats")

_state = {"connected": False, "messages": 0, "errors": 0, "last_error": "", "last_msg": None}


def status() -> dict:
    """Toestand voor de beheerpagina."""
    return {
        "enabled": bool(MQTT_HOST),
        "broker": f"{MQTT_HOST}:{MQTT_PORT}" if MQTT_HOST else None,
        "topic": MQTT_TOPIC,
        **_state,
    }


def _handle_payload(raw: bytes) -> None:
    body = json.loads(raw.decode("utf-8"))
    rep = body.get("repeater") or {}
    prefix = str(rep.get("pubkey_prefix", "")).lower().strip()
    metrics = body.get("metrics")
    if not prefix or not isinstance(metrics, dict):
        raise ValueError("repeater.pubkey_prefix of metrics ontbreekt")

    row = db.get_or_create_repeater(prefix, rep.get("name"))
    ts = body.get("ts") or db.utcnow()
    db.ingest(row["id"], ts, metrics, body.get("neighbors"), force=bool(body.get("force")))
    _state["messages"] += 1
    _state["last_msg"] = ts


def _run() -> None:
    import paho.mqtt.client as mqtt

    def on_connect(client, userdata, flags, rc, properties=None):
        if rc == 0:
            _state["connected"] = True
            _state["last_error"] = ""
            client.subscribe(MQTT_TOPIC, qos=0)
            log.info("MQTT verbonden met %s:%s, geabonneerd op %s",
                     MQTT_HOST, MQTT_PORT, MQTT_TOPIC)
        else:
            _state["connected"] = False
            _state["last_error"] = f"verbinden geweigerd (code {rc})"
            log.warning("MQTT verbinden geweigerd: %s", rc)

    def on_disconnect(client, userdata, rc, properties=None, reason=None):
        _state["connected"] = False
        log.info("MQTT-verbinding verbroken (%s); paho verbindt zelf opnieuw", rc)

    def on_message(client, userdata, msg):
        try:
            _handle_payload(msg.payload)
        except Exception as err:  # noqa: BLE001 - één slecht bericht mag niets breken
            _state["errors"] += 1
            _state["last_error"] = f"{type(err).__name__}: {err}"
            log.warning("MQTT-bericht op %s overgeslagen: %s", msg.topic, err)

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
            log.warning("MQTT-lus gestopt (%s); opnieuw proberen", err)
            import time
            time.sleep(10)


def start() -> None:
    """Start de abonnee in een achtergrondthread (doet niets zonder broker)."""
    if not MQTT_HOST:
        log.info("Geen MCS_MQTT_HOST ingesteld; MQTT-ingest staat uit")
        return
    t = threading.Thread(target=_run, name="mqtt-ingest", daemon=True)
    t.start()
