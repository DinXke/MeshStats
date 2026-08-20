"""MQTT subscriber: nodes publish, we write it down.

A MeshCore node keeps a single MQTT connection open and publishes over it, which
is far cheaper for an ESP32 than setting up an HTTP request every time. Two kinds
of message arrive, on two topic patterns:

Twee topicvoorvoegsels tegelijk
-------------------------------
Sinds de hernoeming naar MeshManager is het voorvoegsel ``meshmanager/``. Het
oude ``meshcore/`` blijft gewoon meeluisteren, en dat is niet uit
vriendelijkheid maar uit noodzaak: nodes en server worden nooit op hetzelfde
moment bijgewerkt. Ging alleen de server om, dan zou hij doof zijn voor elke
node die nog niet geflasht is; gingen alleen de nodes om, dan publiceerden ze
in het niets. Dus luistert deze kant naar allebei en behandelt ze identiek --
het voorvoegsel zegt niets over de inhoud.

Waarom het voorvoegsel überhaupt mee omgaat: ``meshcore/`` is de naam van het
protocol en van een ander project, niet van dit project. Op een broker die ook
echte MeshCore-diensten bedient, is dat een botsing die wacht om te gebeuren,
en een ACL die "van dit project" moet zeggen kan dat niet zeggen. Vandaar een
voorvoegsel dat we zelf bezitten.

Welke kant een opdracht op gaat, hangt af van waar de node zich meldt: zie
``command_prefix``. Wanneer ``LEGACY_PREFIX`` weg mag staat daar.

``<prefix>/<node_hex>/stats``
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

``<prefix>/<node_hex>/rx``
    One message per LoRa packet the node overheard::

        {"t": 123456, "snr": 5.25, "rssi": -92, "len": 57, "raw": "<hex frame>"}

    ``t`` is the node's own uptime counter, not a wall clock, so reception time
    is taken from the server instead.

``<node_hex>`` is the pubkey prefix of the *observing* node. The firmware sends
it uppercase; everything downstream keys on lowercase hex, so it is normalised
here.

One topic goes the other way::

    <prefix>/<node_hex>/cmd

A single word -- ``settings``, ``status`` or ``time`` -- asking that node to read
its CLI parameters now, to publish a statistics message now, or to set its clock.
It exists because the
admin page's "fetch settings" button used to write into a queue that only the
Home Assistant integration ever emptied, so pulling Home Assistant out of the
chain left a button that did nothing while the page kept showing values from the
node's last daily sweep. The node answers on the ordinary ``stats`` topic, so
nothing else in this file changes.

Since firmware 1.9.0 ``settings`` may carry one argument: the public key of a
repeater that node *monitors*. It then fetches that repeater's CLI settings
over LoRa and publishes them under that repeater's name -- the only path there
is to a repeater which does not publish to MQTT itself, which is exactly the
one on the roof this whole project was built to watch.

Since firmware 1.10.0 there is ``time <epoch>``: UNIX seconds in UTC, the format
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
``MeshManagerNet.cpp`` for why nothing else is accepted there.

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
import re
import threading
import time

from . import config, db, packets

log = logging.getLogger("meshmanager.mqtt")

MQTT_HOST = config.env("MQTT_HOST", "")
MQTT_PORT = int(config.env("MQTT_PORT", "1883"))
MQTT_USER = config.env("MQTT_USER", "")
MQTT_PASS = config.env("MQTT_PASS", "")

# Het voorvoegsel dat dit project bezit, en het voorvoegsel van vroeger.
#
# Wanneer LEGACY_PREFIX weg mag: als elke node die naar deze broker publiceert
# geflasht is met MeshManager-firmware 2.0.0 of hoger én ``wifi mqtt prefix``
# op de nieuwe waarde staat. De beheerpagina toont per node op welk voorvoegsel
# hij binnenkomt, dus dat is af te lezen en niet te gokken. Verwijder dan deze
# constante, de regel uit PREFIXES hieronder, en de ``meshcore``-regels uit
# mosquitto/acl.
PREFIX = config.env("MQTT_PREFIX", "meshmanager").strip().strip("/") or "meshmanager"
LEGACY_PREFIX = "meshcore"


def _prefixes() -> tuple:
    """Voorvoegsels waar we naar luisteren, het eigene eerst.

    Een lijst en geen enkele waarde, omdat een migratie nu eenmaal een periode
    is waarin beide waar zijn. Dubbele eruit, want luisteren op hetzelfde
    patroon zou elk bericht twee keer laten binnenkomen -- en dat zou pas
    opvallen als de teller op de beheerpagina verdubbelt.
    """
    out = []
    for p in (PREFIX, LEGACY_PREFIX):
        if p and p not in out:
            out.append(p)
    return tuple(out)


PREFIXES = _prefixes()


def _patterns(leaf: str) -> tuple:
    """``<prefix>/+/<leaf>`` voor elk voorvoegsel."""
    return tuple(f"{p}/+/{leaf}" for p in PREFIXES)


# Wie een eigen patroon in de omgeving zet, houdt dat. Zo'n waarde staat er niet
# per ongeluk -- het is de enige manier om op een gedeelde broker onder een
# eigen tak te draaien -- en die stilzwijgend vervangen door onze standaard zou
# zo'n installatie doof maken op precies het moment dat ze bijwerkt. Ze komt er
# dus BOVENOP de voorvoegsels hierboven, niet in de plaats ervan.
MQTT_TOPICS = tuple(dict.fromkeys(_patterns("stats") + tuple(
    t for t in (config.env("MQTT_TOPIC", "").strip(),) if t)))
MQTT_RX_TOPICS = tuple(dict.fromkeys(_patterns("rx") + tuple(
    t for t in (config.env("MQTT_RX_TOPIC", "").strip(),) if t)))

# En de alarmen. TELEMETRIE IS SNMP-POLLING, EEN ALERT IS EEN SNMP-TRAP -- en die
# vergelijking is precies de reden dat dit een derde topic is en geen veld in het
# statistiekenbericht. Een trap hoort niet te wachten op de volgende ronde, en het
# statistiekenbericht IS die ronde. Zie ``_handle_alert`` en db.add_alert.
#
# Geen eigen omgevingsvariabele, anders dan bij de twee hierboven. Die twee
# hebben er een omdat ze bestonden voordat de voorvoegselregel er was en er
# installaties zijn die een eigen topic gebruiken; dit topic is nieuw en heeft
# die geschiedenis niet. Een variabele erbij zou een instelling zijn die niemand
# ooit anders zet.
MQTT_ALERT_TOPICS = _patterns("alert")

# The one topic this side publishes on. ``{node}`` is the publishing node's own
# pubkey prefix, exactly as it appears in the two topics above, so a broker ACL
# can bind a node's read permission to the same prefix as its write permission.
# ``{prefix}`` wordt ingevuld met het voorvoegsel waarop die ene node zich
# meldt -- zie ``command_prefix``. Een patroon uit de omgeving zonder
# ``{prefix}`` erin wordt gerespecteerd zoals het er staat: wie een vast topic
# opgeeft, bedoelt dat.
MQTT_CMD_TOPIC = config.env("MQTT_CMD_TOPIC", "{prefix}/{node}/cmd")

# Everything the firmware accepts, and nothing more. Kept here as well so a
# typo on this side is refused before it costs a round trip, and so the list is
# readable next to the code that sends it -- MeshManagerNet.cpp, mqttRunCommand().
COMMANDS = ("settings", "status", "time", "set")

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

# En alleen deze krijgt een parameter met een waarde mee. Apart van de twee
# hierboven om dezelfde reden als die twee van elkaar: het is een derde soort
# argument met een derde soort controle. Een sleutel selecteert iets, een epoch
# verandert een klok, en dit verandert een instelling -- ze door één parameter
# laten lopen zou betekenen dat één vergissing in de aanroep een tijd als
# parameternaam laat vertrekken.
COMMANDS_WITH_SETTING = ("set",)

# Wat er van een parameternaam en een waarde nog een commando mag worden. De
# lengtes komen uit CFG_KEY_MAX en CFG_VALUE_MAX in de firmware, en de tekens
# uit wat daar een sleutel mag zijn.
#
# Dit is de beleefdheid en niet de beveiliging -- de node keurt de sleutel tegen
# zijn eigen tabel en de waarde tegen zijn eigen grenzen, en dat is de controle
# die telt. Wat het hier wél doet, is voorkomen dat er een payload vertrekt die
# aan de overkant in stilte wordt afgekapt: MQTT_CMD_MAX is daar 96 byte, en een
# afgekapte waarde is een andere waarde.
MAX_SETTING_KEY = 27
MAX_SETTING_VALUE = 39

# Het venster waarbinnen een tijd geloofwaardig is, in UNIX-epochseconden UTC:
# 2025-01-01 tot 2100-01-01. Dezelfde grenzen als CLOCK_MIN_EPOCH/CLOCK_MAX_EPOCH
# in MeshManagerNet.cpp, en met opzet aan beide kanten gecontroleerd.
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

# Wat de broker terugstuurt als hij een verbinding weigert, in woorden waar
# iemand iets mee kan. "connection refused (code 5)" is geen aanwijzing; "de
# broker weigert de inloggegevens" wijst naar de twee variabelen die je moet
# nakijken.
#
# Dit is niet theoretisch. Bij de hernoeming naar MeshManager startte een
# container met een leeg wachtwoord omdat docker-compose de oude .env niet meer
# las, en de enige aanwijzing was 'Not authorized' in de containerlogs -- de
# site bleef 200 antwoorden en oogde op elke pagina gezond. Dertien minuten
# zonder gegevens, en de enige manier om het te zien was de pakkettelling
# vergelijken.
_WEIGERING = {
    1: "de broker spreekt deze protocolversie niet",
    2: "de broker weigert de client-id",
    3: "de broker is er wel maar niet beschikbaar",
    4: "de broker weigert de gebruikersnaam of het wachtwoord "
       "(MM_MQTT_USER / MM_MQTT_PASS)",
    5: "de broker weigert de inloggegevens: niet geautoriseerd "
       "(MM_MQTT_USER / MM_MQTT_PASS, en de ACL op de broker)",
}

# Hoe lang de ingest stil mag zijn voor de pagina er iets van zegt. Ruim, want
# een node in zuinige modus publiceert 's nachts hooguit een keer per uur, en
# een waarschuwing die elke nacht afgaat leert iedereen hem te negeren.
QUIET_MIN = int(config.env("MQTT_QUIET_MIN", "90") or 90)

_state = {"connected": False, "messages": 0, "packets": 0, "errors": 0,
          "last_error": "", "last_msg": None, "last_packet": None,
          "commands": 0, "refusals": 0, "connects": 0, "started": None,
          # Alarmen apart geteld en niet bij 'messages': ze komen zelden en ze
          # betekenen iets anders. Een teller die op nul blijft terwijl er
          # storingen zijn, is precies de meting die zegt dat deze weg stil is.
          "alerts": 0, "last_alert": None}

# The live client, so the request handlers can publish over the connection the
# subscriber already holds. None until the background thread has built one.
_client = None


def status() -> dict:
    """State for the admin page."""
    return {
        "enabled": bool(MQTT_HOST),
        "broker": f"{MQTT_HOST}:{MQTT_PORT}" if MQTT_HOST else None,
        # Meervoud sinds de hernoeming, en als tekst met komma's zodat de
        # beheerpagina in één oogopslag laat zien dat er nog een oud
        # voorvoegsel meeluistert. Dat is namelijk precies wat je wilt zien om
        # te beslissen of je het mag weghalen.
        "topic": ", ".join(MQTT_TOPICS),
        "rx_topic": ", ".join(MQTT_RX_TOPICS),
        "cmd_topic": MQTT_CMD_TOPIC,
        "prefix": PREFIX,
        "legacy_prefix": LEGACY_PREFIX,
        "health": health(),
        "quiet_min": QUIET_MIN,
        **_state,
    }


def health() -> dict:
    """Doet de ingest zijn werk? Een oordeel, geen tellers.

    Bestaat omdat de tellers hierboven de verkeerde vraag beantwoorden. Ze
    stonden al op de beheerpagina -- "Verbonden: nee", in grijs, tussen twaalf
    andere regels -- en dat is precies zacht genoeg om over te lezen op een
    site die verder overal 200 antwoordt. Deze functie zegt in een woord wat er
    aan de hand is, en de pagina kan dat luid maken.

    Vier toestanden, en het onderscheid tussen de laatste twee is het punt:

    ``uit``        geen broker ingesteld. Geen alarm: de HTTP-ingest is een
                   geldige manier om dit te draaien.
    ``geweigerd``  de broker wijst ons af. Ondubbelzinnig kapot, en de reden
                   staat erbij -- dit is de toestand die dertien minuten
                   onopgemerkt bleef.
    ``weg``        ingesteld, maar er is geen verbinding. Netwerk, verkeerde
                   host, broker plat.
    ``stil``       verbonden, maar er komt niets binnen. Kan kloppen (een mesh
                   waar niemand publiceert) en kan de ergste storing van
                   allemaal zijn (een ACL die alles weggooit). Daarom een
                   waarschuwing met de duur erbij, en geen fout.
    ``goed``       verbonden en er komt verkeer binnen.

    Er wordt met opzet niet geraden welke van de twee ``stil`` is. Het verschil
    is van buitenaf niet te zien, en een pagina die gokt is een pagina die je
    de volgende keer niet gelooft.
    """
    now = db.utcnow()
    if not MQTT_HOST:
        return {"state": "uit", "ok": True,
                "why": "geen broker ingesteld; nodes leveren via HTTP aan"}
    if _state["refusals"] and not _state["connected"]:
        return {"state": "geweigerd", "ok": False,
                "why": _state["last_error"] or "de broker weigert de verbinding"}
    if not _state["connected"]:
        return {"state": "weg", "ok": False,
                "why": _state["last_error"] or "geen verbinding met de broker"}

    laatste = _state["last_msg"] or _state["last_packet"]
    sinds = _state["started"]
    stil_sinds = laatste or sinds
    minuten = _minuten_geleden(stil_sinds, now)
    if minuten is not None and minuten >= QUIET_MIN:
        wat = "sinds de start" if laatste is None else "sinds het laatste bericht"
        return {"state": "stil", "ok": False,
                "why": f"verbonden, maar {int(minuten)} minuten niets ontvangen "
                       f"({wat}). Controleer de ACL op de broker en of de nodes "
                       f"publiceren."}
    return {"state": "goed", "ok": True, "why": ""}


def _minuten_geleden(wanneer, nu) -> float | None:
    """Minuten tussen twee ISO-tijdstempels, of None als dat niet lukt.

    Mag nooit opwerpen: dit draait in de weergave van een beheerpagina, en een
    kapotte tijdstempel hoort geen pagina te breken die juist moet vertellen
    dat er iets mis is.
    """
    from datetime import datetime
    try:
        a = datetime.fromisoformat(str(wanneer).replace("Z", "+00:00"))
        b = datetime.fromisoformat(str(nu).replace("Z", "+00:00"))
        return (b - a).total_seconds() / 60.0
    except (TypeError, ValueError):
        return None


def can_publish() -> bool:
    """Whether a command sent right now would actually leave this machine."""
    return bool(MQTT_HOST) and _client is not None and bool(_state["connected"])


def publish_command(node: str, command: str, subject: str | None = None,
                    epoch: int | None = None,
                    setting: tuple | None = None) -> bool:
    """Ask one node to do something now. False when nothing was sent.

    ``subject`` is the public key of a repeater that ``node`` monitors, and
    turns ``settings`` into ``settings <key>``: fetch *their* CLI settings over
    LoRa instead of your own. Passed separately rather than baked into
    ``command`` so the word itself keeps being checked against COMMANDS as an
    exact string -- the same discipline the firmware applies at the far end, and
    for the same reason.

    ``setting`` is ``(parameter, waarde)`` and turns ``set`` into
    ``set <parameter> <waarde>``: change one of that node's own CLI settings.
    Passed as its own argument for the same reason as the two above -- it is a
    third kind of argument with a third kind of check.

    That word is where this topic gained the ability to *change* something other
    than a clock, so it is worth saying what this side does and does not do. It
    does not decide whether the parameter may be set: that lives in
    ``nodeconfig.write``, which has already weighed the risk class, the ceiling
    of this transport and the confirmation by the time it gets here. And the node
    weighs all of it again against its own compiled-in table, which is the check
    that counts -- this side cannot know what a given firmware accepts, and a
    server that guessed would be a second parameter list.

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

    if command in COMMANDS_WITH_SETTING:
        # Ontbreken is een programmeerfout: 'set' zonder parameter betekent
        # niets. Een uitzondering dus, zodat het bij het schrijven stukgaat.
        if setting is None:
            raise ValueError(f"command {command!r} needs a setting")
        param, waarde = (str(x) for x in setting)
        param = param.strip()
        waarde = waarde.strip()
        # Een lege waarde is hier geen 'wissen' maar een aanroepfout: elke
        # parameter in de tabel van de node heeft een vorm, en geen enkele
        # aanvaardt niets. Teruggeven en niet opwerpen -- dit komt uit een
        # formulier en de beller mag het melden.
        if not param or not waarde:
            log.warning("Instelling %r=%r onvolledig; niets verstuurd naar %s",
                        param, waarde, node)
            return False
        if (len(param) > MAX_SETTING_KEY or len(waarde) > MAX_SETTING_VALUE
                or not re.fullmatch(r"[a-z0-9._]+", param)):
            # Aan de overkant zou dit afgekapt of geweigerd worden, en een
            # afgekapte waarde is een ándere waarde. Dat hoort hier te stranden.
            log.warning("Instelling %r=%r past niet in een cmd-bericht; niets "
                        "verstuurd naar %s", param, waarde, node)
            return False
        payload = f"{command} {param} {waarde}"
    elif setting is not None:
        raise ValueError(f"command {command!r} takes no setting")

    if not can_publish():
        return False

    sent = False
    for topic in command_topics(node):
        try:
            info = _client.publish(topic, payload.encode(), qos=0, retain=False)
        except Exception as err:  # noqa: BLE001 - a dead socket must not 500 the page
            log.warning("Opdracht %s naar %s niet verstuurd: %s", payload, node, err)
            continue
        if info.rc != 0:
            log.warning("Opdracht %s naar %s geweigerd door de client (rc %s)",
                        payload, node, info.rc)
            continue
        sent = True
        log.info("Opdracht '%s' gepubliceerd op %s", payload, topic)
    if sent:
        _state["commands"] += 1
    return sent


# Waar we een node voor het laatst hoorden publiceren, per node. Alleen een
# cache: de waarheid staat in de kolom ``topic_prefix`` op de repeaterrij, want
# na een herstart van de site is dit leeg terwijl de node nog steeds daar
# luistert waar hij gisteren luisterde.
_seen_prefix = {}
# Het middenstuk zoals de node het schrijft, per genormaliseerde sleutel. Naast
# _seen_prefix en om dezelfde reden: allebei komen ze alleen hier langs, en samen
# vormen ze het antwoord op "waar gaat een opdracht voor deze node heen".
_seen_node = {}


def command_prefix(node: str) -> str | None:
    """Het voorvoegsel waarop deze node zich meldt, of None als we het niet weten.

    Een opdracht gaat naar het topic waar de node werkelijk luistert, en dat is
    tijdens een migratie niet af te leiden uit een instelling: een node die nog
    niet geflasht is, luistert op het oude voorvoegsel, en de site heeft geen
    andere manier om dat te weten dan te kijken waar zijn berichten vandaan
    komen. Vandaar dat we het onthouden bij binnenkomst in plaats van het te
    kiezen bij vertrek.
    """
    node = re.sub(r"[^0-9a-f]", "", str(node or "").lower())
    if not node:
        return None
    if node in _seen_prefix:
        return _seen_prefix[node]
    stored = db.topic_prefix_for(node)
    if stored:
        _seen_prefix[node] = stored
    return stored


def command_topics(node: str) -> tuple:
    """Topic(s) waarop een opdracht voor deze node vertrekt.

    Meestal precies één: dat waarop hij zich meldt. Voor een node die we nog
    nooit gehoord hebben zijn het er twee -- op allebei de voorvoegsels -- want
    dan is er niets om uit te kiezen en is één klein bericht extra goedkoper
    dan een knop die niets doet zonder te zeggen waarom. Zo'n node kan dat
    trouwens nauwelijks zijn: de knoppen die dit gebruiken staan op de pagina
    van een repeater die hier al binnenkomt.
    """
    # Het middenstuk in de vorm die deze node zelf gebruikt. MQTT-topics zijn
    # hoofdlettergevoelig en MeshCore schrijft ze met hoofdletters, dus met de
    # genormaliseerde sleutel zou de opdracht op een topic belanden waar niemand
    # op geabonneerd is: publish() slaagt, de broker neemt de bytes aan, en er
    # luistert niemand. Precies het patroon dat twaalf uur onopgemerkt bleef.
    #
    # Nooit gehoord? Dan allebei de vormen, met de hoofdlettervariant vooraan
    # omdat dat is wat MeshCore doet. Dezelfde afweging als bij een onbekend
    # voorvoegsel hieronder: één klein bericht extra is goedkoper dan een knop
    # die niets doet zonder te zeggen waarom.
    knopen = command_nodes(node)
    if "{prefix}" not in MQTT_CMD_TOPIC:
        # Vast patroon uit de omgeving: respecteren zoals opgegeven.
        return tuple(MQTT_CMD_TOPIC.format(node=n) for n in knopen)
    prefix = command_prefix(node)
    prefixes = (prefix,) if prefix else PREFIXES
    return tuple(MQTT_CMD_TOPIC.format(prefix=p, node=n)
                 for p in prefixes for n in knopen)


def command_nodes(node: str) -> tuple:
    """Het middenstuk van het topic voor deze node, zoals hij het zelf schrijft.

    Eén vorm als we hem ooit gehoord hebben, anders twee. Het geheugen eerst en
    de databank daarna, zodat een node die zich net gemeld heeft niet op een
    schrijfactie hoeft te wachten.
    """
    gezien = _seen_node.get(node)
    if not gezien:
        rij = db.find_repeater(node)
        if rij is not None:
            try:
                gezien = rij["topic_node"]
            except (KeyError, IndexError):
                gezien = None
    if gezien and db.key_prefix(gezien) == db.key_prefix(node):
        return (gezien,)
    boven = node.upper()
    return (boven, node) if boven != node else (node,)


def _topic_parts(topic: str) -> tuple:
    """``(voorvoegsel, node)`` uit ``<prefix>/<node_hex>/<kind>``.

    Het middenstuk wordt hier gekeurd als sleutel en niet verderop, want dit is
    de enige plek waar het binnenkomt en het gaat drie kanten op: naar
    ``_seen_prefix`` (geheugen), naar ``get_or_create_repeater`` (een rij) en
    naar ``insert_packet`` (de waarnemerskolom). We abonneren met ``+`` op die
    positie, dus wat daar staat is precies wat de publisher koos.

    Het VOORVOEGSEL wordt bewust niet tegen een lijst gehouden. Deze site
    luistert tijdens de hernoeming naar MeshManager op ``meshmanager`` én
    ``meshcore``, en wie een eigen patroon in de omgeving zet mag een derde
    naam. De begrenzing daarvan zit waar hij hoort: ``record_topic_prefix``
    knipt hem op 32 tekens af voor hij de databank in gaat.
    """
    parts = topic.split("/")
    node = db.key_prefix(parts[1]) if len(parts) >= 3 else ""
    if not node:
        raise ValueError(f"topic {topic!r} noemt geen node met een sleutel")
    # Ook het ruwe middenstuk terug, want de normalisatie hierboven gooit iets weg
    # dat we straks nodig hebben: MQTT-topics zijn hoofdlettergevoelig en MeshCore
    # schrijft ze met hoofdletters (Utils::toHex gebruikt "0123456789ABCDEF").
    # Een opdracht die met het genormaliseerde middenstuk opgebouwd wordt, komt op
    # een topic terecht waar niemand op geabonneerd is.
    return parts[0].strip(), node, parts[1].strip()


def _topic_node(topic: str) -> str:
    """Publishing node from ``<prefix>/<node_hex>/<kind>``.

    Legt meteen vast op welk voorvoegsel deze node zich meldt, want dit is de
    enige plaats waar dat langskomt -- en het is wat ``command_topics`` straks
    nodig heeft om een opdracht de goede kant op te sturen.

    Alleen in het geheugen, met opzet. Ook ``rx`` komt hier langs, en dat is de
    snelste stroom die deze server kent; naar de databank schrijven gebeurt op
    de statistiekenkant, waar het om enkele berichten per minuut gaat.
    """
    prefix, node, raw = _topic_parts(topic)
    if prefix:
        _seen_prefix[node] = prefix
    if raw:
        _seen_node[node] = raw
    return node


def _handle_payload(topic: str, raw: bytes) -> None:
    publisher = _topic_node(topic)
    body = json.loads(raw.decode("utf-8"))
    rep = body.get("repeater") or {}
    # Subject defaults to the publisher: a node reporting on itself does not
    # have to repeat its own prefix in the payload.
    metrics = body.get("metrics")
    if not isinstance(metrics, dict):
        raise ValueError("metrics missing")

    # De filterstand reist mee met elk statistiekenbericht. De tellers erin gaan
    # als gewone metrics door dezelfde molen: dan tekenen ze in de grafieken,
    # verouderen ze met dezelfde bewaartermijn en zijn ze te vergelijken met het
    # verkeer waar ze uit weggeknipt zijn -- allemaal machinerie die er al is.
    # Het zou zonde zijn er een tweede stelsel naast te zetten voor zes getallen.
    filter_state = body.get("filter")
    if isinstance(filter_state, dict):
        extra = _filter_metrics(filter_state)
        if extra:
            metrics = dict(metrics)
            metrics.update(extra)

    # Keuren vóór er iets geschreven wordt, en in één regel voor beide
    # ingest-wegen (zie db.check_snapshot). Wat hier opgeworpen wordt, komt
    # terecht bij de brede vanger in _dispatch: geteld op de beheerpagina en met
    # een fragment van de payload in het logboek, zodat "geweigerd" naspeurbaar
    # is in plaats van stil.
    subject = db.check_snapshot(rep.get("pubkey_prefix") or publisher,
                                metrics, body.get("neighbors"))

    row = db.get_or_create_repeater(subject, rep.get("name"))
    # Wie er vóór dit bericht voor deze repeater publiceerde. Nu vastgehouden,
    # want record_source hieronder overschrijft het met de huidige publisher --
    # en _handle_settings moet juist weten of die twee al aan elkaar gekoppeld
    # waren voordat dit bericht binnenkwam.
    prior_source = _field(row, "source_prefix")
    ts = body.get("ts") or db.utcnow()
    # Bewust GEEN force uit de payload. 'force' is het signaal "sla dit punt hoe
    # dan ook op, ook als het niet veranderde" en het overslaat de
    # heartbeat-ontdubbeling die de samples-tabel klein houdt. Het hoort bij een
    # handmatige verversing langs de HTTP-ingest (Home Assistant, met token) --
    # de eigen firmware zet het nooit in zijn MQTT-JSON. Op dit topic is de data
    # apparaatdata die niet van iedereen komt (iedereen met brokerreferenties kan
    # eronder publiceren), dus een force die van hier binnenkomt is geen
    # verversing maar een manier om die ontdubbeling te ontwijken en de tabel vol
    # te schrijven. We negeren hem daarom en laten db.ingest op force=False staan.
    db.ingest(row["id"], ts, metrics, body.get("neighbors"))
    db.record_source(row["id"], publisher)
    # Op de statistiekenkant wél naar de databank, want dit moet een herstart
    # van de site overleven: een opdracht aan een node die vandaag nog niets
    # publiceerde, hoort na een herstart nog steeds op het goede topic te
    # vertrekken. Eén UPDATE per bericht, en die komen per minuut in plaats van
    # per seconde. Hier en niet bovenaan, want pas na get_or_create_repeater
    # bestaat de rij van een node die zich voor het eerst meldt.
    db.record_topic_prefix(publisher, _seen_prefix.get(publisher, ""))
    db.record_topic_node(publisher, _seen_node.get(publisher, ""))
    db.record_firmware(row["id"], rep.get("fw"), db.payload_module_version(rep))
    if subject != publisher:
        log.info("stats for %s relayed by node %s", subject, publisher)
    if isinstance(filter_state, dict):
        _handle_filter(row, publisher, subject, filter_state)
    settings = body.get("settings")
    if isinstance(settings, dict):
        _handle_settings(row, publisher, settings, prior_source)
    _handle_cfg(row, publisher, subject, body)
    _state["messages"] += 1
    _state["last_msg"] = ts


# --- het pakketfilter ---------------------------------------------------------

# De tellers die als metric verdergaan, met de naam die ze op de site krijgen.
# Een vaste lijst en geen doorgeeflus: dit is een topic waar iedereen met
# brokergegevens onder kan publiceren, en een lus zou een vreemde afzender laten
# bepalen welke metricnamen er in de databank verschijnen. Zes namen die wij
# kiezen, en de rest van de payload wordt genegeerd.
FILTER_DROP_METRICS = {
    "type": "filter_drop_type",
    "hops": "filter_drop_hops",
    "rate": "filter_drop_rate",
    "hash": "filter_drop_hash",
    "kanaal": "filter_drop_channel",
    "misvormd": "filter_drop_malformed",
}

# De pakkettypes zoals de firmware ze nummert (PacketFilter.cpp, TYPE_NAMES).
# Hier herhaald en niet uit de payload overgenomen, om dezelfde reden als
# hierboven: de afzender levert getallen, wij bepalen hoe ze heten.
PF_TYPE_NAMES = [
    "REQ", "RESPONSE", "TXT_MSG", "ACK", "ADVERT", "GRP_TXT",
    "GRP_DATA", "ANON_REQ", "PATH", "TRACE", "MULTIPART", "CONTROL",
]
PF_MAX_CHANNELS = 16          # PF_CHAN_MAX in de firmware
PF_MAX_LABEL = 23             # PF_LABEL_MAX - 1


def _num(value):
    """Een teller, of None. Negatieve en onzinnige waarden vallen af."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if value < 0 or value > 4_000_000_000:
        return None
    return float(value)


def _filter_metrics(state: dict) -> dict:
    """De tellers uit een filterblok, als gewone metrics.

    Ook wanneer het filter uit staat, en dat is met opzet. Een nul die
    gepubliceerd wordt is een meting -- 'dit filter gooide vandaag niets weg' --
    en die is iets anders dan een ontbrekende reeks. Zonder die nullen zou de
    grafiek van een node waarvan het filter net uitgezet is gewoon ophouden, wat
    er precies zo uitziet als een node die niets meer meldt.
    """
    uit = {}
    drops = state.get("drop")
    totaal = 0.0
    if isinstance(drops, dict):
        for sleutel, naam in FILTER_DROP_METRICS.items():
            waarde = _num(drops.get(sleutel))
            if waarde is None:
                continue
            uit[naam] = waarde
            totaal += waarde
    if uit:
        uit["filter_dropped"] = totaal
    for sleutel, naam in (("passed", "filter_passed"), ("exempt", "filter_exempt")):
        waarde = _num(state.get(sleutel))
        if waarde is not None:
            uit[naam] = waarde
    # Aan of uit als getal, zodat 'wanneer stond dit filter aan' een reeks is en
    # geen gok op basis van wanneer er weer iets weggegooid werd.
    if isinstance(state.get("on"), bool):
        uit["filter_on"] = 1.0 if state["on"] else 0.0

    # Twee reeksen erbij, en met opzet maar twee. De snelheidslimiet is de enige
    # regel waarvan de DRUK iets anders zegt dan de dropteller: 'hij heeft in 12
    # van de 4000 vensters gebeten' betekent iets heel anders dan 'in 3900 van de
    # 4000', terwijl het aantal weggegooide pakketten hetzelfde kan zijn. De
    # verhouding tussen deze twee is het cijfer waarmee je besluit of een limiet
    # bijgesteld moet worden.
    #
    # Als reeks en niet als uitsplitsing per type, want twaalf types maal drie
    # velden zijn zesendertig namen tegen een dak van 128 metrics per bericht en
    # een FIFO van 1000 rijen per repeater. De verdeling per type staat in de
    # blob; hier staat het totaal, want dat is wat je in een grafiek wilt zien.
    rate = state.get("rate")
    if isinstance(rate, dict):
        vensters = geraakt = 0.0
        gezien = False
        for waarde in list(rate.values())[:len(PF_TYPE_NAMES)]:
            if not isinstance(waarde, dict):
                continue
            seen = _num(waarde.get("seen"))
            cap = _num(waarde.get("cap"))
            if seen is None and cap is None:
                continue
            gezien = True
            vensters += seen or 0.0
            geraakt += cap or 0.0
        if gezien:
            uit["filter_rate_windows"] = vensters
            uit["filter_rate_capped"] = geraakt
    return uit


def _pf_type(sleutel) -> int | None:
    """"04" -> 4, en alles wat geen bestaand pakkettype is -> None."""
    if not isinstance(sleutel, str) or not sleutel.isdigit():
        return None
    nummer = int(sleutel)
    return nummer if 0 <= nummer < len(PF_TYPE_NAMES) else None


def _filter_breakdown(state: dict) -> dict:
    """De uitsplitsing uit een filterblok (firmware 2.6.0+), streng nagelopen.

    Dezelfde houding als bij FILTER_DROP_METRICS, en om dezelfde reden: dit komt
    van een topic waar iedereen met brokergegevens op kan publiceren. Vaste
    sleutels, vaste grenzen, vaste maximumaantallen. Wat er niet in past wordt
    genegeerd in plaats van doorgegeven -- een node die onzin stuurt hoort niet
    te kunnen bepalen wat er in de databank belandt of hoe groot het wordt.

    Wat er NIET gebeurt: hier worden geen metrics van gemaakt. Twaalf types maal
    zes redenen zijn tweeënzeventig namen, en er is één bericht per paar minuten
    met een dak van 128 metrics en een FIFO van 1000 rijen per repeater. Dit is
    een momentopname van een verdeling, en die hoort in de bestaande JSON-blob
    van repeater_filter -- één rij per repeater, die per definitie niet groeit.
    """
    uit: dict = {}

    # type x reden: {"04.hops": 3}
    xr = state.get("xr")
    if isinstance(xr, dict):
        kruis: dict = {}
        for sleutel, waarde in list(xr.items())[:len(PF_TYPE_NAMES) * len(FILTER_DROP_METRICS)]:
            if not isinstance(sleutel, str) or "." not in sleutel:
                continue
            links, _, reden = sleutel.partition(".")
            nummer = _pf_type(links)
            getal = _num(waarde)
            if nummer is None or reden not in FILTER_DROP_METRICS or getal is None:
                continue
            kruis.setdefault(PF_TYPE_NAMES[nummer], {})[reden] = int(getal)
        if kruis:
            uit["xr"] = kruis

    # snelheidslimiet: {"05": {"seen":41,"cap":2,"peak":20,"lim":20}}
    rate = state.get("rate")
    if isinstance(rate, dict):
        tempo: dict = {}
        for sleutel, waarde in list(rate.items())[:len(PF_TYPE_NAMES)]:
            nummer = _pf_type(sleutel)
            if nummer is None or not isinstance(waarde, dict):
                continue
            regel = {}
            for veld in ("seen", "cap", "peak", "lim"):
                getal = _num(waarde.get(veld))
                if getal is not None:
                    regel[veld] = int(getal)
            if regel:
                tempo[PF_TYPE_NAMES[nummer]] = regel
        if tempo:
            uit["rate"] = tempo

    # via de ACL langs het filter, per type
    ex = state.get("ex")
    if isinstance(ex, dict):
        vrij: dict = {}
        for sleutel, waarde in list(ex.items())[:len(PF_TYPE_NAMES)]:
            nummer = _pf_type(sleutel)
            getal = _num(waarde)
            if nummer is None or getal is None:
                continue
            vrij[PF_TYPE_NAMES[nummer]] = int(getal)
        if vrij:
            uit["ex"] = vrij

    # geblokkeerde kanalen met hun treffers
    chan = state.get("chan")
    if isinstance(chan, list):
        kanalen = []
        for item in chan[:PF_MAX_CHANNELS]:
            if not isinstance(item, dict):
                continue
            label = item.get("l")
            hash_ = item.get("h")
            treffers = _num(item.get("hits"))
            if not isinstance(label, str) or not isinstance(hash_, str):
                continue
            # Hetzelfde alfabet als labelOk() in de firmware, en dezelfde lengte.
            label = "".join(c for c in label if c.isalnum() or c in "-_.")[:PF_MAX_LABEL]
            hash_ = "".join(c for c in hash_.lower() if c in "0123456789abcdef")[:2]
            if not label or len(hash_) != 2:
                continue
            kanalen.append({"label": label, "hash": hash_,
                            "hits": int(treffers or 0)})
        if kanalen:
            uit["chan"] = kanalen

    # De node zegt zelf dat er iets niet meepaste. Overnemen, want een
    # onvolledige uitsplitsing die zich voordoet als een volledige is precies de
    # stille fout die dit project niet wil.
    if state.get("trunc"):
        uit["trunc"] = True
    return uit


def _handle_filter(row, publisher: str, subject: str, state: dict) -> None:
    """Bewaar de filterstand -- maar alleen die van de afzender zelf.

    Dezelfde regel als bij de CLI-instellingen, en om een scherpere reden. Een
    node kan legitiem cijfers dóórgeven over een repeater die hij monitort, dus
    voor metrics is 'niet over jezelf' normaal. Voor een filterstand niet: de
    firmware publiceert alleen zijn eigen filter, want een gemonitorde repeater
    vertelt zijn filterstand nergens over de radio. Een blok dat over iemand
    anders beweert te gaan, kan dus niet kloppen -- en het gaat over de
    instelling waarmee je een node onopvallend nutteloos maakt. Weigeren, en het
    opschrijven.
    """
    if subject != publisher:
        log.info("filterstand voor %s meegestuurd door %s: genegeerd, "
                 "een node meldt alleen zijn eigen filter", subject, publisher)
        return
    bewaard = {
        "on": bool(state.get("on")),
        "disarmed": bool(state.get("disarmed")),
        "hash": int(_num(state.get("hash")) or 1),
        "malformed": bool(state.get("malformed")),
        "channels": int(_num(state.get("channels")) or 0),
        "blocked_types": int(_num(state.get("blocked_types")) or 0),
        "passed": int(_num(state.get("passed")) or 0),
        "exempt": int(_num(state.get("exempt")) or 0),
        "drop": {},
    }
    drops = state.get("drop")
    if isinstance(drops, dict):
        for sleutel in FILTER_DROP_METRICS:
            waarde = _num(drops.get(sleutel))
            if waarde is not None:
                bewaard["drop"][sleutel] = int(waarde)
    uitsplitsing = _filter_breakdown(state)
    if uitsplitsing:
        bewaard["stats"] = uitsplitsing
    db.upsert_filter_state(row["id"], bewaard, publisher)


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
    firmware 1.9.0 a node also sweeps the CLI of a repeater it *monitors*, over
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


# 'ver' antwoordt in twee vormen, en beide horen hier gelezen te worden.
#
#   standaard MeshCore   "v1.17.0 (Build: 12 Jan 2026)"
#                        CommonCLI.cpp:271 -- memcmp(command, "ver", 3), dat
#                        antwoordt met "%s (Build: %s)" uit getFirmwareVer() en
#                        getBuildDate().
#   met onze module      "MeshManager (by DinX) v2.1.0 - MeshCore v1.17.0 (Build: ...)"
#                        mmnet_handle_command() vangt 'ver' af vóór MeshCore en
#                        zet er de moduleversie voor.
#
# Eén vraag, twee kolommen. Dat is de reden dat 'ver' in de sweep staat en niet
# een tweede commando voor de moduleversie: over LoRa is elke vraag zendtijd van
# een repeater op een dak.
_VER_MODULE = re.compile(r"v(\d+\.\d+\.\d+)\s+-\s+MeshCore\s+(\S+)")
_VER_PLAIN = re.compile(r"^\s*(\S+)")


def parse_ver(answer: str) -> tuple[str, str]:
    """(MeshCore-versie, moduleversie) uit een antwoord op 'ver'.

    Lege strings waar niets te halen valt, want dat is wat ``record_firmware``
    als "hier weet ik niets van" leest -- en die laat een kolom dan met rust in
    plaats van hem leeg te schrijven. Een node die 'ver' niet kent antwoordt met
    iets anders of met niets, en beide moeten hier stil aflopen: dit draait in de
    ingest van elk statistiekbericht, en een parser die daar struikelt kost de
    hele boodschap.
    """
    text = (answer or "").strip()
    if not text:
        return "", ""
    m = _VER_MODULE.search(text)
    if m:
        return m.group(2), m.group(1)
    # Geen module ervoor: dan is het eerste woord de MeshCore-versie, en de rest
    # is "(Build: ...)" dat we niet bewaren -- de bouwdatum zegt niets wat het
    # versienummer niet al zegt, en hij maakt de kolom twee keer zo breed.
    m = _VER_PLAIN.match(text)
    kandidaat = m.group(1) if m else ""
    # Een antwoord dat niet op een versie lijkt is geen versie. 'Err - ...' en
    # '??' komen hier langs op firmware die het commando niet kent.
    if not re.match(r"^v?\d", kandidaat):
        return "", ""
    return kandidaat, ""


def _handle_settings(row, publisher: str, values: dict, prior_source=None) -> None:
    """Store CLI settings that rode along with a statistics message.

    Two publishers may write these, and no others:

    - the repeater **itself**, which is the ordinary case;
    - the node that **already relays this repeater's statistics**, which is new
      in firmware 1.9.0. Such a monitor logs in over LoRa, walks the far side's
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

    # 'cmd:ver' is de enige parameter in de sweep die niet alleen een instelling
    # is maar ook een eigenschap van de node zelf, en daar heeft de site een
    # kolom voor. Voor een repeater die zelf publiceert staat fw al in zijn
    # statistiekbericht; voor een doorgestuurde repeater is DIT de enige plek
    # waar hij ooit vandaan komt. Zonder deze regel blijft de MeshCore-kolom
    # leeg voor precies de nodes waarvoor hij bedoeld is.
    ver = clean.get("cmd:ver")
    if ver:
        fw, module = parse_ver(ver)
        if fw or module:
            db.record_firmware(row["id"], fw=fw, fw_module=module)

    answered = sum(1 for v in clean.values() if v is not None)
    log.info("CLI settings updated for %s (%d parameters, %d answered)",
             row["slug"], len(clean), answered)


def _rx_verdict(body: dict) -> tuple[str | None, str | None]:
    """Wat het pakketfilter van de waarnemer met dit pakket deed.

    Drie uitkomsten, en de derde is een eigen antwoord: ``fwd`` ontbreekt als het
    filter dit pakket niet beoordeeld heeft. Dat is het gewone geval voor een
    pakket dat aan de node zelf gericht was, dat direct gerouteerd werd, of
    waarvan het frame de parser niet haalde -- die bereiken allowPacketForward()
    niet eens. Ook firmware ouder dan 2.7.0 stuurt het veld niet. In al die
    gevallen is (None, None) het eerlijke antwoord en nadrukkelijk niet
    'doorgelaten'.

    De reden wordt getoetst aan dezelfde zes sleutels die de tellers en de
    labels al gebruiken. Iedereen met brokergegevens kan op dit topic
    publiceren, dus een onbekende reden wordt weggelaten in plaats van
    doorgegeven -- anders bepaalt een vreemde afzender welke woorden er in de
    kolom belanden.
    """
    if "fwd" not in body:
        return None, None
    doorgelaten = body.get("fwd")
    if not isinstance(doorgelaten, (int, bool)) or isinstance(doorgelaten, float):
        return None, None
    if doorgelaten:
        return "doorgelaten", None
    reden = body.get("why")
    if not isinstance(reden, str) or reden not in FILTER_DROP_METRICS:
        reden = None
    return "geweerd", reden


def _handle_cfg(row, publisher: str, subject: str, body: dict) -> None:
    """De parametertabel en de laatste schrijfactie, als ze meekwamen.

    Twee dingen die alleen een node over ZICHZELF kan melden, en die regel is
    hier strenger dan bij ``_handle_settings``. Daar mag ook de monitor
    publiceren, omdat hij de CLI van een ander werkelijk uitleest; hier kan dat
    niet bestaan. De parametertabel is de ingebakken lijst van de publicerende
    firmware, en de uitslag gaat over een commando dat op het cmd-topic van die
    node aankwam. Een node die deze twee onder de naam van een ander stuurt,
    beweert iets wat hij niet kan weten.

    Dat verschil is niet theoretisch. Wie de gedeelde brokergegevens heeft, kan
    onder elk topic publiceren -- zie de kop van dit bestand. Zou een vreemde
    publisher hier een parametertabel voor een andere node mogen neerleggen, dan
    zou hij de risicoklassen kiezen waarop de site haar bevestigingen en haar
    rechten baseert. Dat is precies de verkeerde kant om.
    """
    eigen = subject == publisher
    spec = body.get("cfgspec")
    if isinstance(spec, dict) and spec:
        if eigen:
            db.record_cfg_spec(row["id"], json.dumps(spec, separators=(",", ":")))
        else:
            log.info("cfgspec voor %s van %s genegeerd: een node meldt alleen "
                     "zijn eigen parametertabel", subject, publisher)

    job = body.get("cfgset")
    if isinstance(job, dict) and job:
        if eigen:
            from . import nodeconfig
            nodeconfig.note_cfgset(publisher, job)
        else:
            log.info("cfgset voor %s van %s genegeerd: een node meldt alleen "
                     "zijn eigen schrijfacties", subject, publisher)


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

    fwd, reden = _rx_verdict(body)
    pkt = packets.decode(frame)
    db.insert_packet(observer, pkt, snr=body.get("snr"), rssi=body.get("rssi"),
                     length=body.get("len") or len(frame), raw=hex_frame,
                     fwd=fwd, fwd_reason=reden)
    _state["packets"] += 1
    _state["last_packet"] = db.utcnow()
    if _state["packets"] % PRUNE_EVERY_PACKETS == 0:
        db.prune()


# Woorden waaraan de ernst van een alarm te zien is, en aan welke kant ze staan.
#
# Dit is met opzet een KLEINE lijst en geen poging tot taalbegrip. De alarmen van
# MeshUptime hebben een vaste vorm (zie monitorAlertText in MonitorSensors.cpp),
# en drie woorden daaruit zeggen genoeg: een dienst die onbereikbaar is of als
# neer gemeld werd, is 'hoog'; een test en een herstelmelding zijn 'laag'. Wat
# niet past krijgt NULL, en dat is een geldig antwoord -- de tekst staat er
# voluit naast, en een verzonnen ernst is erger dan geen.
_ALERT_HIGH = ("onbereikbaar", "gemeld als neer", "geen melding meer")
_ALERT_LOW = ("test ", "simulatie", "weer bereikbaar", "hersteld")

# Waar het kanaalnummer in de tekst staat, als het erin staat. De alarmen van
# MeshUptime noemen de NAAM van een dienst en niet zijn kanaal, dus dit vindt
# meestal niets -- en dat is waarom het veld mag ontbreken. Wat het wél vindt is
# de vorm die een mens intypt bij een testalarm ("kanaal 6: ...").
_ALERT_CHANNEL = re.compile(r"\b(?:kanaal|channel|ch)\s*([0-9]{1,3})\b", re.I)


def alert_severity(text: str) -> str | None:
    """De ernst van een alarm uit zijn tekst, of None.

    Eerst 'laag' en dan 'hoog', en die volgorde is de hele functie: een
    testalarm bevat het woord "onbereikbaar" ook, want dat is precies de
    bedoeling van een test -- hij leest als het echte bericht. Andersom toetsen
    zou elke simulatie als een storing melden, en dan is de test onbruikbaar
    geworden door de weergave ervan.
    """
    laag = str(text or "").lower()
    if any(w in laag for w in _ALERT_LOW):
        return "laag"
    if any(w in laag for w in _ALERT_HIGH):
        return "hoog"
    return None


def alert_channel(text: str):
    """Het kanaalnummer uit de tekst, of None. Zie ``_ALERT_CHANNEL``."""
    m = _ALERT_CHANNEL.search(str(text or ""))
    if not m:
        return None
    try:
        n = int(m.group(1))
    except ValueError:
        return None
    return n if 1 <= n <= 255 else None


def _handle_alert(topic: str, raw: bytes) -> None:
    """Eén alarm van een sensornode, doorgezet door de repeater die het hoorde.

    De node WAAROVER het gaat staat in de payload (``alert.pubkey_prefix``); het
    topic noemt de DOORGEVER. Die twee zijn hier per definitie verschillend -- een
    sensornode publiceert zelf niet -- en koppelen op het topic zou elke storing
    aan de repeater hangen in plaats van aan de node die stilviel.

    Een alarm van een node zonder rij hier wordt bewaard zonder node erbij en
    niet weggegooid: zie db.add_alert. Dat is de melding die je bij een onbekende
    node het hardst nodig hebt.

    De TIJD komt van de repeater als hij er een meestuurt, en anders van deze
    server. Wat er NIET gebeurt is de tijd van de sensornode gebruiken: die heeft
    geen gebufferde klok en staat na elke herstart op 15 mei 2024 -- precies het
    apparaat dat deze alarmen stuurt.
    """
    publisher = _topic_node(topic)
    body = json.loads(raw.decode("utf-8"))
    alert = body.get("alert") or {}
    text = str(alert.get("text") or "").strip()
    if not text:
        raise ValueError("alert zonder tekst")

    subject = db.key_prefix(alert.get("pubkey_prefix")) or publisher
    rij = db.find_repeater(subject)
    ts = None
    epoch = alert.get("ts")
    if isinstance(epoch, (int, float)) and not isinstance(epoch, bool):
        ts = _iso_from_epoch(epoch)

    alert_id = db.add_alert(
        rij["id"] if rij is not None else None, text, source="mesh", ts=ts,
        channel=alert_channel(text), severity=alert_severity(text))
    _state["alerts"] = _state.get("alerts", 0) + 1
    if alert_id:
        _state["last_alert"] = db.utcnow()
        # Waarschuwingsniveau, want dat is wat dit is. Een alarm dat alleen in een
        # tabel belandt, is een alarm dat niemand ziet tot hij gaat kijken.
        log.warning("ALERT van %s (via %s): %s", subject, publisher, text[:120])
    else:
        log.info("Alarm van %s was een herhaling binnen %ss; niet opnieuw bewaard",
                 subject, db.ALERT_DEDUP_S)


def _iso_from_epoch(epoch) -> str | None:
    """Een epoch van een node naar onze ISO-vorm, of None als hij onbruikbaar is.

    De ondergrens is geen netheid: een node zonder gebufferde klok staat op de
    datum uit zijn firmware, en die tijdstempel zou een alarm van vandaag jaren
    in het verleden zetten -- onder elke andere regel in de lijst, waar niemand
    hem ziet. Liever de ontvangsttijd van deze server, die aantoonbaar klopt.
    """
    from datetime import datetime, timezone
    try:
        seconds = float(epoch)
    except (TypeError, ValueError):
        return None
    # 2025-01-01 als vloer, en een dag vooruit als plafond: alles buiten dat
    # venster is geen klok maar een fabrieksinstelling of een tikfout.
    if not (1735689600 <= seconds <= time.time() + 86400):
        return None
    return datetime.fromtimestamp(seconds, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


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
    1.9.1-noot in MeshManagerNet.cpp -- en die staat in de payload. Zonder dat
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
        leaf = topic.rsplit("/", 1)[-1]
        if leaf == "rx":
            _handle_rx(topic, payload)
        elif leaf == "alert":
            _handle_alert(topic, payload)
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
            _state["connects"] += 1
            _state["last_error"] = ""
            topics = MQTT_TOPICS + MQTT_RX_TOPICS + MQTT_ALERT_TOPICS
            for topic in topics:
                client.subscribe(topic, qos=0)
            log.info("MQTT connected to %s:%s, subscribed to %s",
                     MQTT_HOST, MQTT_PORT, ", ".join(topics))
        else:
            _state["connected"] = False
            _state["refusals"] += 1
            uitleg = _WEIGERING.get(int(rc) if str(rc).isdigit() else -1, "")
            _state["last_error"] = (f"verbinding geweigerd (code {rc})"
                                    + (f": {uitleg}" if uitleg else ""))
            # Foutniveau en niet waarschuwing: een geweigerde verbinding is geen
            # hik maar een installatie die vanaf nu niets meer binnenkrijgt.
            log.error("MQTT-verbinding geweigerd (code %s)%s", rc,
                      f": {uitleg}" if uitleg else "")

    def on_disconnect(client, userdata, rc, properties=None, reason=None):
        _state["connected"] = False
        log.info("MQTT disconnected (%s); paho reconnects on its own", rc)

    def on_message(client, userdata, msg):
        handle_message(msg.topic, msg.payload)

    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="meshmanager-ingest")
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
        log.info("No MM_MQTT_HOST configured; MQTT ingest is off")
        return
    # Vanaf wanneer "er komt niets binnen" iets betekent. Zonder dit ijkpunt
    # zou een verse start er hetzelfde uitzien als een ingest die al uren stil
    # ligt.
    _state["started"] = db.utcnow()
    t = threading.Thread(target=_run, name="mqtt-ingest", daemon=True)
    t.start()
