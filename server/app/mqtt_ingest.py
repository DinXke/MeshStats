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

A single word -- ``settings``, ``status`` or ``time`` -- asking that node to read
its CLI parameters now, to publish a statistics message now, or to set its clock.
It exists because the
admin page's "fetch settings" button used to write into a queue that only the
Home Assistant integration ever emptied, so pulling Home Assistant out of the
chain left a button that did nothing while the page kept showing values from the
node's last daily sweep. The node answers on the ordinary ``stats`` topic, so
nothing else in this file changes.

Since MeshStats 1.9.0 ``settings`` may carry one argument: the public key of a
repeater that node *monitors*. It then fetches that repeater's CLI settings
over LoRa and publishes them under that repeater's name -- the only path there
is to a repeater which does not publish to MQTT itself, which is exactly the
one on the roof this whole project was built to watch.

Since MeshStats 1.10.0 there is ``time <epoch>``: UNIX seconds in UTC, the format
MeshCore's own CLI parses. The node sets its own clock from it and then checks
the clocks of the repeaters it monitors over LoRa -- again the only path to a
repeater that does not publish here itself. Which node is asked, and what has to
be true about this machine's clock first, lives in ``clocksync.py``; this file
only knows how to put the message on the wire.

That the firmware accepts only those three words is not a detail: this topic is
reachable by anyone holding broker credentials, and the repeaters this serves
hang on roofs. The arguments do not widen that. The one on ``settings`` is never
text that reaches a CLI -- it selects one entry from a monitor list only the
node's operator can write. The one on ``time`` is a number, bounded at both ends
here and again on the node, and it can only ever move a clock forward. That last
part is not a nicety: a node's adverts carry its clock, and a clock moved
backwards makes it invisible to everyone who already knows it for exactly as
long as the step. See ``publish_command`` for why nothing is retained here, and
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
COMMANDS = ("settings", "status", "time")

# Alleen deze mag een onderwerp meekrijgen, want alleen deze betekent iets voor
# een ander dan de node zelf. 'status' met een sleutel erbij zou vragen om
# cijfers die de monitor toch al uit zichzelf doorstuurt.
COMMANDS_WITH_SUBJECT = ("settings",)

# En alleen deze krijgt een tijd mee, die hij ook nodig HEEFT: 'time' zonder
# getal wordt aan de overkant geweigerd en geteld. Apart gehouden van
# COMMANDS_WITH_SUBJECT omdat het een ander soort argument is met een andere
# controle -- een sleutel is hex en selecteert iets, een epoch is een getal en
# verandert iets. Ze door één parameter laten lopen zou betekenen dat één
# vergissing in de aanroep een sleutel als tijd laat vertrekken.
COMMANDS_WITH_EPOCH = ("time",)

# Het venster waarbinnen een tijd geloofwaardig is, in UNIX-epochseconden UTC:
# 2025-01-01 tot 2100-01-01. Dezelfde grenzen als CLOCK_MIN_EPOCH/CLOCK_MAX_EPOCH
# in MeshStatsNet.cpp, en met opzet aan beide kanten gecontroleerd.
#
# Dat is hier geen dubbel werk maar de goedkoopste plaats van de twee: een node
# zet zijn klok alleen VOORUIT (zijn adverts worden geweigerd als de tijdstempel
# niet stijgt), dus een tijd die te ver in de toekomst ligt is aan de overkant
# niet meer terug te draaien zonder er met een kabel bij te gaan staan. Een
# vergissing hier hoort hier te stranden.
MIN_EPOCH = 1_735_689_600
MAX_EPOCH = 4_102_444_800

# Kortste sleutel die we als onderwerp durven meesturen. Zelfde grens als
# MIN_PREFIX_MATCH hier en als monKeyArg() in de firmware: korter dan dit kan
# toevallig op twee nodes passen, en de firmware weigert dan alsnog -- alleen
# een halve minuut later en zonder dat iemand het ziet.
MIN_SUBJECT_HEX = 8

# A MeshCore frame tops out around 255 bytes; anything far beyond that is not a
# packet and should not be turned into a multi-megabyte bytes object.
MAX_RAW_HEX = 1024

# Hoeveel tekens van een onbruikbaar bericht mee het logboek in gaan. Genoeg om
# de plek te zien die json aanwijst ("column 87"), te weinig om een logbestand
# vol te schrijven met een node die in een lus onzin publiceert.
MAX_LOG_EXCERPT = 240

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


def publish_command(node: str, command: str, subject: str | None = None,
                    epoch: int | None = None) -> bool:
    """Ask one node to do something now. False when nothing was sent.

    ``subject`` is the public key of a repeater that ``node`` monitors, and
    turns ``settings`` into ``settings <key>``: fetch *their* CLI settings over
    LoRa instead of your own. Passed separately rather than baked into
    ``command`` so the word itself keeps being checked against COMMANDS as an
    exact string -- the same discipline the firmware applies at the far end, and
    for the same reason.

    ``epoch`` is UNIX time in UTC seconds and turns ``time`` into
    ``time <epoch>``: set your clock to this, and then check the clocks of the
    repeaters you monitor. Which format that is was not a choice -- it is what
    MeshCore's CLI parses in the ``time `` branch of ``CommonCLI::handleCommand``
    (``_atoi`` of the rest of the line, straight into ``setCurrentTime``). See
    ``clocksync.py`` for what has to be true about this machine's own clock
    before anything is allowed to leave over this route.

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

    payload = command
    if subject is not None:
        if command not in COMMANDS_WITH_SUBJECT:
            raise ValueError(f"command {command!r} takes no subject")
        subject = re.sub(r"[^0-9a-f]", "", str(subject or "").lower())
        if len(subject) < MIN_SUBJECT_HEX:
            # Niet publiceren maar teruggeven dat er niets vertrok: de pagina
            # meldt dan "niets verstuurd" in plaats van een opdracht die aan de
            # overkant geweigerd wordt zonder dat hier iets van te zien is.
            log.warning("Onderwerp %r te kort voor een opdracht aan %s", subject, node)
            return False
        payload = f"{command} {subject}"

    if command in COMMANDS_WITH_EPOCH:
        # Ontbreken is een programmeerfout en geen bedrijfsongeval: 'time'
        # zonder tijd betekent niets. Een uitzondering, zoals bij een onbekend
        # commando, zodat het bij het schrijven stukgaat en niet in productie.
        if epoch is None:
            raise ValueError(f"command {command!r} needs an epoch")
        try:
            epoch = int(epoch)
        except (TypeError, ValueError):
            raise ValueError(f"epoch {epoch!r} is not a number") from None
        if not (MIN_EPOCH <= epoch < MAX_EPOCH):
            # Wél teruggeven in plaats van opwerpen: dit is de weg waarlangs een
            # kapotte serverklok binnenkomt, en dat is een toestand van de
            # machine en geen fout in de aanroep. De beller ziet "niets
            # vertrokken" en kan dat melden.
            log.error("Tijd %s valt buiten het toegestane venster; niets verstuurd "
                      "naar %s", epoch, node)
            return False
        payload = f"{command} {epoch}"
    elif epoch is not None:
        raise ValueError(f"command {command!r} takes no epoch")

    if not can_publish():
        return False
    topic = MQTT_CMD_TOPIC.format(node=node)
    try:
        info = _client.publish(topic, payload.encode(), qos=0, retain=False)
    except Exception as err:  # noqa: BLE001 - a dead socket must not 500 the page
        log.warning("Opdracht %s naar %s niet verstuurd: %s", payload, node, err)
        return False
    if info.rc != 0:
        log.warning("Opdracht %s naar %s geweigerd door de client (rc %s)",
                    payload, node, info.rc)
        return False
    _state["commands"] += 1
    log.info("Opdracht '%s' gepubliceerd voor node %s", payload, node)
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
    # Wie er vóór dit bericht voor deze repeater publiceerde. Nu vastgehouden,
    # want record_source hieronder overschrijft het met de huidige publisher --
    # en _handle_settings moet juist weten of die twee al aan elkaar gekoppeld
    # waren voordat dit bericht binnenkwam.
    prior_source = _field(row, "source_prefix")
    ts = body.get("ts") or db.utcnow()
    db.ingest(row["id"], ts, metrics, body.get("neighbors"), force=bool(body.get("force")))
    db.record_source(row["id"], publisher)
    db.record_firmware(row["id"], rep.get("fw"), rep.get("fw_meshstats"))
    if subject != publisher:
        log.info("stats for %s relayed by node %s", subject, publisher)
    settings = body.get("settings")
    if isinstance(settings, dict):
        _handle_settings(row, publisher, settings, prior_source)
    _state["messages"] += 1
    _state["last_msg"] = ts


def _field(row, name):
    """row is een sqlite3.Row of een dict; allebei komen hier binnen."""
    try:
        return row[name]
    except (KeyError, IndexError):
        return None


# A node has around fifteen CLI parameters. The cap is not about them; it is so
# that a publisher cannot turn one message into thousands of rows.
MAX_SETTINGS = 64


def _clean_settings(values: dict) -> dict:
    """Drop the parameters that carry nothing, keep the ones that say "no answer".

    An empty string is dropped: a node omits a parameter it could not read
    rather than sending it blank, so a blank one that does arrive carries no
    information, and writing it would replace a value we know with nothing.
    Omission is safe by itself -- upsert_cli_settings only touches the keys it
    is given -- so this only has to catch the empty ones.

    ``None`` is deliberately kept, and that is not the same thing. Since
    MeshStats 1.9.0 a node also sweeps the CLI of a repeater it *monitors*, over
    LoRa, and there a parameter can be asked and stay silent. It sends null for
    those, the column stores NULL, and the page renders "(geen antwoord)" -- the
    same phrase, from the same column, that a Home Assistant sweep has always
    produced for the same fact (see ``_fetch_settings`` in the integration,
    which posts None for exactly this).

    Yes, that overwrites a value an earlier sweep did get. Rejected alternative:
    only write null where nothing is stored yet, so a good value is never lost.
    It hides precisely what has to be visible. A repeater whose monitor logs in
    read-only answers no CLI command at all, and the whole sweep then comes back
    silent; with values from March still on screen and only a timestamp moved,
    nobody would ever find that. The firmware makes the matching promise from
    its side: it publishes nothing at all when the login itself failed, because
    then it asked nothing and learned nothing.
    """
    out = {}
    for key, value in values.items():
        name = str(key).strip()
        if not name:
            continue
        if isinstance(value, str) and not value.strip():
            continue
        out[name] = value
        if len(out) >= MAX_SETTINGS:
            break
    return out


def _handle_settings(row, publisher: str, values: dict, prior_source=None) -> None:
    """Store CLI settings that rode along with a statistics message.

    Two publishers may write these, and no others:

    - the repeater **itself**, which is the ordinary case;
    - the node that **already relays this repeater's statistics**, which is new
      in MeshStats 1.9.0. Such a monitor logs in over LoRa, walks the far side's
      CLI and publishes the answers under that repeater's name, because a node
      that does not publish to MQTT itself has no other path at all.

    Until 1.9.0 the rule was "only its own", on the stated grounds that the
    firmware never sent anything else. That has stopped being true, so the rule
    had to move -- and it is worth being exact about what moved with it. Any
    client holding the shared broker credentials could already publish
    *statistics* for any repeater; that is the hole this file's header already
    admits to and points at the per-node broker ACL to close. What was extra
    here is that settings were harder to forge than metrics. They now cost one
    extra step: you must first become the node this repeater's statistics
    arrive through, and that is a visible change on the admin page
    (``source_prefix``) rather than an invisible one.

    ``prior_source`` is who that was *before* this message, because
    ``record_source`` has already overwritten the row by the time we get here.
    Reading it afterwards would compare the publisher against itself and let
    anybody through on the first try.

    Identity is compared through the repeater row rather than by string: the
    topic and the payload may spell the same key at different lengths.
    """
    owner = db.find_repeater(publisher)
    if owner is None:
        log.info("settings for %s published by unknown node %s ignored",
                 row["slug"], publisher)
        return
    if owner["id"] != row["id"]:
        relay = db.find_repeater(prior_source) if prior_source else None
        if relay is None or relay["id"] != owner["id"]:
            log.info("settings for %s published by %s ignored: not its own and "
                     "not the node that relays it", row["slug"], publisher)
            return
        log.info("settings for %s accepted from its monitor %s", row["slug"], publisher)

    clean = _clean_settings(values)
    if not clean:
        return
    # prune=False: this sweep leaves out what it could not read, and dropping a
    # parameter because one sweep missed it is the same as overwriting it with
    # nothing.
    db.upsert_cli_settings(row["id"], clean, prune=False)
    answered = sum(1 for v in clean.values() if v is not None)
    log.info("CLI settings updated for %s (%d parameters, %d answered)",
             row["slug"], len(clean), answered)


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


def _excerpt(raw: bytes) -> str:
    """Een begrensd, altijd afdrukbaar stuk van een payload, voor in het logboek.

    ``backslashreplace`` en niet ``replace``: een van de manieren waarop een
    bericht onbruikbaar wordt, is een nodenaam die halverwege een UTF-8-teken
    afgekapt is. Met vraagtekens erin ziet zo'n logregel er precies hetzelfde
    uit als een aanhalingsteken op de verkeerde plek, terwijl ``\\xc3`` meteen
    zegt welke van de twee het is.

    Mag zelf nooit stukgaan: dit draait in de foutafhandeling, en een
    uitzondering hier zou de melding wissen die ze moest opleveren.
    """
    try:
        text = bytes(raw or b"").decode("utf-8", "backslashreplace")
    except Exception:  # noqa: BLE001 - zie hierboven
        return "<niet weer te geven>"
    # Op een regel houden, anders leest een logboek met tien van deze meldingen
    # als een berg losse fragmenten zonder herkenbaar begin.
    text = text.replace("\r", " ").replace("\n", " ")
    if len(text) > MAX_LOG_EXCERPT:
        return f"{text[:MAX_LOG_EXCERPT]}... ({len(raw)} bytes)"
    return text


def handle_message(topic: str, payload: bytes) -> bool:
    """Verwerkt een binnengekomen bericht. False als het overgeslagen is.

    Staat hier op moduleniveau en niet als sluiting in ``_run()`` omdat het
    gedrag dat hier vastligt getest hoort te zijn: dat een node uit de
    statistieken verdwijnt zonder dat er ergens iets van te zien is, is precies
    het soort storing waar dit project omheen gebouwd is.

    Waarom de payload zelf mee het logboek in gaat, en niet enkel de melding van
    json: die melding is "Expecting ',' delimiter: line 1 column 87". Daar staat
    niet in wat er op kolom 87 stond, dus je weet dat een bericht van deze node
    onleesbaar was en verder niets. De aanleiding is een stuk tekst dat iemand
    ooit gekozen heeft -- een nodenaam met een aanhalingsteken erin, zie de
    1.9.1-noot in MeshStatsNet.cpp -- en die staat in de payload. Zonder dat
    fragment is de enige weg naar de oorzaak een sniffer op de broker, en
    daarmee blijft zo'n fout jaren liggen.

    Overwogen en verworpen: het bericht wegschrijven naar een tabel of een
    aparte map, zodat het compleet bewaard blijft. Dat is opslag die groeit
    zonder plafond aan de kant van wie hem publiceert, en de eerste tweehonderd
    tekens zijn in de praktijk het hele antwoord.

    Fouten worden hier bewust breed gevangen: één slecht bericht -- van welke
    node dan ook, en iedereen met brokerreferenties kan er een sturen -- mag de
    ingest-lus niet stilleggen voor alle andere.
    """
    try:
        if topic.rsplit("/", 1)[-1] == "rx":
            _handle_rx(topic, payload)
        else:
            _handle_payload(topic, payload)
        return True
    except Exception as err:  # noqa: BLE001 - zie hierboven
        _state["errors"] += 1
        _state["last_error"] = f"{type(err).__name__}: {err}"
        log.warning("MQTT-bericht op %s overgeslagen: %s: %s | payload: %s",
                    topic, type(err).__name__, err, _excerpt(payload))
        return False


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
        handle_message(msg.topic, msg.payload)

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
