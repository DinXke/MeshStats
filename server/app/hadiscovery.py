"""Home Assistant MQTT-discovery: onze nodes verschijnen vanzelf als entiteiten.

Wat dit doet, en waarom zo
--------------------------
Home Assistant kan entiteiten aanmaken zonder dat iemand ze in zijn
configuratie zet: publiceer een *config*-bericht op een topic onder
``homeassistant/...`` en HA maakt de sensor, de knop of het binaire signaal aan.
Uptime Kuma doet het al op deze broker, en wij doen precies hetzelfde -- geen
custom component, geen HA-herstart, geen YAML. Zie docs/ha-integratie.md.

De broker is een ANDERE dan die van ``mqtt_ingest``. Daar publiceren de nodes
hun statistieken naartoe (onze eigen Mosquitto); hier publiceren WIJ naar de
broker waar Home Assistant aan hangt (een EMQX op het LAN). Twee brokers, dus
twee verbindingen: deze module bouwt een eigen paho-client met eigen client-id,
eigen inloggegevens en een eigen herverbindlus. paho zit al in de requirements
voor de ingest, dus er komt geen afhankelijkheid bij.

Uit tot het gezet is
--------------------
Zoals webpush uit is tot de VAPID-sleutels gezet zijn, staat dit uit tot
``MM_HA_MQTT_HOST`` én ``MM_HA_DISCOVERY_ENABLED`` gezet zijn. De reden staat in
:func:`status` (op de serverpagina) en in het opstartlog. Een broker die
onbereikbaar is mag de app niet ophouden of laten vallen: de verbinding en al
het publiceren gebeuren in een achtergronddraad met automatische herverbinding
(``loop_start`` + ``reconnect_delay_set``), net als bij ``mqtt_ingest``.

Hoe de state binnenkomt
-----------------------
Niet pollen: we hangen een haak in ``db.ingest`` (zie ``register_ingest_hook``).
Elke verse meting -- of ze nu via MQTT, via de sensornode-poll of via de
HTTP-API binnenkwam -- zet meteen het id van de node in een wachtrij, en de
achtergronddraad werkt de HA-state-topics van die node bij. De haak zelf doet
niets meer dan ``put_nowait``: het ingest-pad staat nooit op de HA-broker te
wachten. Daarnaast loopt er een trage ronde (:data:`SWEEP_SECS`) die voor elke
node in scope de beschikbaarheid herijkt (stil gevallen -> "offline" in HA) en
de state opnieuw zet, zodat ook een alarm dat zonder nieuwe meting binnenkomt
binnen een minuut in HA staat.

Beschikbaarheid
---------------
Elke entiteit hangt aan TWEE availability-topics met ``availability_mode: all``:
een brug-topic (met een last-will, zodat álles "niet beschikbaar" wordt als
MeshManager wegvalt) en een node-topic (dat op "offline" gaat als díe node
langer dan :data:`STALE_MIN` niets meer stuurde). Zo toont HA een node grijs
zodra hij stil valt, zonder de andere nodes mee te trekken.

Opruimen
--------
Config-topics zijn *retained*: HA onthoudt ze over een herstart. Dat betekent
dat een entiteit die weg moet, actief opgeruimd moet worden -- een retained ""
op zijn config-topic. We onthouden per node welke object-id's we publiceerden
(in ``settings``, dus over een herstart heen) en ruimen bij elke ronde op wat er
niet meer bij hoort: een ping-monitor die van de node verdwijnt (en wiens
``latest``-rij door de bewaring gesnoeid wordt) laat zo geen spookentiteit
achter. Een node die uit scope valt of verwijderd wordt, idem.

Botsingen
---------
Alles krijgt het voorvoegsel ``meshmanager_`` in zijn object-id en unique-id,
zodat we niets van de bestaande MeshCore-scripts of de Uptime-Kuma-entiteiten
overschrijven.
"""
import json
import logging
import queue
import threading

from . import config, metrics

log = logging.getLogger("meshmanager.hadiscovery")

# --- configuratie ------------------------------------------------------------
HA_MQTT_HOST = config.env("HA_MQTT_HOST", "").strip()
HA_MQTT_PORT = int(config.env("HA_MQTT_PORT", "1883") or 1883)
HA_MQTT_USER = config.env("HA_MQTT_USER", "").strip()
HA_MQTT_PASS = config.env("HA_MQTT_PASS", "")
# Bewust een aparte schakelaar naast de host: een host invullen is nog niet
# hetzelfde als "zet dit nu aan". Zo kan iemand de broker klaarzetten in .env en
# de knop pas omzetten als de EMQX-gebruiker en de ACL er staan.
HA_DISCOVERY_ENABLED = config.env("HA_DISCOVERY_ENABLED", "").strip().lower() in (
    "1", "true", "yes", "ja", "on", "aan")

# Waar HA naar config-berichten luistert. 'homeassistant' is de standaard van HA
# zelf; alleen aanpassen als de HA-instantie een ander discovery-voorvoegsel
# heeft (mqtt: discovery_prefix in configuration.yaml).
DISCOVERY_PREFIX = (config.env("HA_DISCOVERY_PREFIX", "homeassistant").strip().strip("/")
                    or "homeassistant")
# Onder welk voorvoegsel ONZE state- en availability-topics wonen. Los van het
# ingest-voorvoegsel en met een eigen ``/ha`` erin, zodat een broker-ACL
# 'meshmanager alleen onder meshmanager/ha/#' kan afdwingen.
STATE_PREFIX = (config.env("HA_STATE_PREFIX", "meshmanager/ha").strip().strip("/")
                or "meshmanager/ha")

# Welke nodes we publiceren. Zie :func:`_in_scope`.
#   sensors    alleen de sensornodes (eigen API / sensor_host)
#   monitored  sensornodes + repeaters die telemetrie melden  (standaard)
#   all        elke rij in de repeaters-tabel
SCOPE = config.env("HA_SCOPE", "monitored").strip().lower() or "monitored"

# Na hoeveel minuten stilte een node in HA "niet beschikbaar" wordt. Ruim, om
# dezelfde reden als MQTT_QUIET_MIN: een node in zuinige modus meldt zich soms
# maar eens per uur, en een entiteit die elke nacht op grijs springt leert
# iedereen om beschikbaarheid te negeren.
STALE_MIN = int(config.env("HA_STALE_MIN", "20") or 20)

# Seconden tussen twee trage rondes (beschikbaarheid herijken + state en alarmen
# opnieuw zetten). Kort genoeg dat een alarm zonder verse meting toch snel in HA
# staat, lang genoeg dat het niets kost.
SWEEP_SECS = 60

CLIENT_ID = "meshmanager-hadiscovery"

# --- companion-device_trackers -----------------------------------------------
# De vaste GPS-nauwkeurigheid (meter) die we op elke companion-tracker meesturen.
# De companions (T1000-E e.d.) melden hun fix zonder een nauwkeurigheidscijfer,
# dus HA krijgt hier een verstandige, eerlijke schatting in plaats van een
# verzonnen-precieze 0: HA tekent er een cirkeltje mee op de kaart en dat hoort
# niet te suggereren dat de positie op de meter klopt.
COMPANION_GPS_ACCURACY = 20
# Wat er op het STATE-topic van een companion-tracker staat. Een device_tracker
# in HA houdt een korte tekst-state; de POSITIE komt niet daaruit maar uit de
# json-attributen (lat/lon, ``source_type: gps``). Deze retained, niet-lege
# tekst houdt de entiteit "aanwezig" terwijl het bolletje op de kaart volledig
# door de attributen gestuurd wordt -- "see" (gezien) en niet "home", want we
# rekenen hier geen HA-zones uit en willen niet liegen dat de handzender in de
# thuiszone staat.
COMPANION_STATE = "see"
# Het model dat HA onder het companion-device toont als de companion zelf geen
# type meldt. De beheerde handzenders zijn doorgaans T1000-E's.
COMPANION_DEFAULT_MODEL = "T1000-E"

# Telemetrie die een gewone (niet-sensor) repeater tot een HA-entiteit maakt, en
# meteen wat het voor entiteit wordt. Bewust een KORTE, betekenisvolle selectie
# en niet de hele catalogus: HA volstoppen met tientallen tellers die niemand in
# een automatisering gebruikt, is precies wat de opdracht niet wil.
#
# Per metriek: (component, device_class, unit, state_class). None waar HA er
# geen moet krijgen.
REPEATER_METRICS = {
    "bat":                    ("sensor", "voltage", "V", "measurement"),
    "battery_percentage":     ("sensor", "battery", "%", "measurement"),
    "airtime_utilization":    ("sensor", None, "%", "measurement"),
    "rx_airtime_utilization": ("sensor", None, "%", "measurement"),
    "noise_floor":            ("sensor", "signal_strength", "dBm", "measurement"),
    "mcu_temperature":        ("sensor", "temperature", "°C", "measurement"),
    "neighbor_count":         ("sensor", None, None, "measurement"),
}

# Deze metrieken maken van een repeater een 'gemonitorde' node: staat er één van
# in ``latest``, dan hoort hij (bij scope=monitored) in HA. Een repeater die
# alleen als buur van een ander gezien is, heeft deze niet en blijft eruit.
TELEMETRY_MARKERS = ("bat", "battery_percentage", "airtime_utilization",
                     "rx_airtime_utilization", "noise_floor")


# --- leefstatus van de module ------------------------------------------------
_state = {
    "connected": False, "connects": 0, "refusals": 0,
    "published_entities": 0, "published_nodes": 0, "published_companions": 0,
    "state_msgs": 0, "config_msgs": 0, "removed": 0,
    "last_error": "", "last_publish": None, "started": None,
}
_client = None
_queue: "queue.Queue[int]" = queue.Queue()
_thread = None
# Per node de handtekening van de laatst gepubliceerde config-set, zodat we
# retained config-topics niet elke ronde opnieuw schrijven maar alleen als er
# een entiteit bij komt, weg gaat of van vorm verandert.
_config_sig: dict = {}
# Idem voor de companion-device_trackers, apart gehouden van de node-set zodat
# de twee elkaars handtekeningen niet kunnen overschrijven (ze delen dezelfde
# publiceer-cadans maar niet dezelfde sleutels).
_companion_config_sig: dict = {}


# --- status voor de serverpagina ---------------------------------------------
def status() -> dict:
    """Aan of uit, en zo nee waarom -- plus wat er gepubliceerd is.

    De reden is een zin voor op het scherm, geen foutcode: de beheerder moet
    eruit kunnen aflezen wat hem te doen staat.
    """
    if not HA_MQTT_HOST:
        reason = ("HA-broker niet ingesteld (MM_HA_MQTT_HOST); "
                  "zie docs/ha-integratie.md")
    elif not HA_DISCOVERY_ENABLED:
        reason = ("staat uit tot MM_HA_DISCOVERY_ENABLED=1 gezet is "
                  "(host is al ingesteld)")
    else:
        reason = None
    return {
        "enabled": reason is None,
        "reason": reason,
        "broker": f"{HA_MQTT_HOST}:{HA_MQTT_PORT}" if HA_MQTT_HOST else None,
        "discovery_prefix": DISCOVERY_PREFIX,
        "state_prefix": STATE_PREFIX,
        "scope": SCOPE,
        "stale_min": STALE_MIN,
        **_state,
    }


def enabled() -> bool:
    return status()["enabled"]


# --- topics ------------------------------------------------------------------
def _bridge_availability_topic() -> str:
    return f"{STATE_PREFIX}/bridge/availability"


def _node_availability_topic(node: str) -> str:
    return f"{STATE_PREFIX}/{node}/availability"


def _state_topic(node: str, key: str) -> str:
    return f"{STATE_PREFIX}/{node}/{key}"


def _config_topic(component: str, object_id: str) -> str:
    return f"{DISCOVERY_PREFIX}/{component}/{object_id}/config"


def _object_id(node: str, key: str) -> str:
    return f"meshmanager_{node}_{key}"


# --- companion-topics/-ids ---------------------------------------------------
# Alles hangt aan de PUBKEY-PREFIX van de companion en niet aan zijn rij-id: het
# id in de databank verandert als iemand een companion weggooit en opnieuw
# toevoegt, en dan zou HA een tweede tracker aanmaken naast een spook van de
# eerste. De pubkey is de stabiele identiteit van de handzender -- dezelfde
# keuze als ``pubkey_prefix`` bij de nodes.
def _companion_prefix(comp) -> str:
    return str(comp["pubkey"] or "").strip().lower()[:12]


def _companion_node(comp) -> str:
    """De 'node'-naam waaronder de state-/availability-topics van een companion
    wonen (``meshmanager/ha/companion_<prefix>/...``)."""
    return f"companion_{_companion_prefix(comp)}"


def _companion_object_id(comp) -> str:
    """Het HA object-/unique-id van de companion-tracker. Het ``meshmanager_``
    voorvoegsel houdt hem uit de buurt van andere entiteiten, ``companion_``
    onderscheidt hem van de node-entiteiten met dezelfde prefix."""
    return f"meshmanager_companion_{_companion_prefix(comp)}"


# --- scope -------------------------------------------------------------------
def _is_sensor_node(rep) -> bool:
    host = (rep["sensor_host"] if "sensor_host" in rep.keys() else None) or ""
    return bool(str(host).strip())


def _in_scope(rep, latest: dict) -> bool:
    """Hoort deze node in HA? Zie :data:`SCOPE`.

    De sensornodes horen er altijd bij (die dragen de ping-monitors, en dat is
    het hele punt). Een gewone repeater telt als 'gemonitord' zodra hij
    telemetrie meldt -- anders is hij een node die we alleen als buur zagen, en
    daar valt in HA niets zinnigs over te tonen.
    """
    if _is_sensor_node(rep):
        return True
    if SCOPE == "sensors":
        return False
    if SCOPE == "all":
        return True
    return any(m in latest for m in TELEMETRY_MARKERS)


def _in_scope_nodes():
    """Elke node die in HA hoort, met zijn ``latest``-tabel erbij."""
    from . import db
    out = []
    for rep in db.q("SELECT * FROM repeaters"):
        latest = db.latest_for(rep["id"])
        if _in_scope(rep, latest):
            out.append((rep, latest))
    return out


# --- entiteiten samenstellen -------------------------------------------------
def _num_str(value) -> str:
    """Een getal netjes als string: gehele getallen zonder ``.0``."""
    try:
        f = float(value)
    except (TypeError, ValueError):
        return str(value)
    return str(int(f)) if f == int(f) else repr(f)


def _switch_device_class(name: str):
    """Het HA device_class bij een schakelkanaal, afgeleid van de naam.

    De naam is door de gebruiker gezet (``channel_names``) en draagt de betekenis
    die het LPP-type niet heeft: een switch is voor de node een kale bool. De
    heuristiek is bewust simpel en gedocumenteerd; wat er niet uitkomt, wordt een
    connectiviteitssignaal -- want dat is wat de meeste kanalen zijn (een
    ping-monitor: bereikbaar ja/nee).

    'batterij' eerst, want 'batterijvoeding' bevat óók 'voeding'. Een
    accu/batterij-kanaal krijgt geen device_class: "aan = op batterij" past op
    geen enkele HA-klasse netjes, en een verkeerde klasse liegt over de polariteit.
    """
    n = (name or "").lower()
    if "wifi" in n:
        return "connectivity"
    if "batterij" in n or "accu" in n or "battery" in n:
        return None
    if "netvoeding" in n or "mains" in n or ("net" in n and "voeding" in n):
        return "power"
    return "connectivity"


def _device_block(rep) -> dict:
    """Het HA-device waaronder alle entiteiten van deze node hangen."""
    node = rep["pubkey_prefix"]
    model = "MeshUptime" if _is_sensor_node(rep) else "MeshCore repeater"
    return {
        "identifiers": [f"meshmanager_{node}"],
        "name": rep["name"],
        "manufacturer": "MeshManager",
        "model": model,
        # Alle nodes hangen onder één brug-device, zodat ze in HA bij elkaar staan.
        "via_device": "meshmanager_bridge",
    }


def _availability_block(node: str) -> list:
    return [
        {"topic": _bridge_availability_topic(),
         "payload_available": "online", "payload_not_available": "offline"},
        {"topic": _node_availability_topic(node),
         "payload_available": "online", "payload_not_available": "offline"},
    ]


def _entities_for(rep, latest: dict) -> list:
    """De entiteiten van één node: ``(key, component, config, state)`` per stuk.

    ``config`` is het discovery-bericht (retained), ``state`` de waarde voor het
    state-topic (of ``None`` als er nu niets te melden valt).
    """
    from . import db
    node = rep["pubkey_prefix"]
    device = _device_block(rep)
    avail = _availability_block(node)
    names = db.channel_names_for(rep["id"])
    ents = []

    def add(key, component, config, state):
        object_id = _object_id(node, key)
        base = {
            "unique_id": object_id,
            "object_id": object_id,
            "device": device,
            "availability": avail,
            "availability_mode": "all",
            "state_topic": _state_topic(node, key),
        }
        base.update(config)
        ents.append((key, component, base, state))

    # 1) Node online/heartbeat: bestaat altijd, los van welke metriek er is.
    #    De state volgt de beschikbaarheid (online zolang de node niet stil is).
    add("online", "binary_sensor",
        {"name": "Online", "device_class": "connectivity",
         "payload_on": "online", "payload_off": "offline",
         # Deze entiteit leest zijn eigen beschikbaarheidstopic als state: is de
         # node beschikbaar, dan staat hij "aan". Eén topic voor twee doelen.
         "state_topic": _node_availability_topic(node)},
        None)  # state komt via het availability-topic, niet apart

    # 2) Actieve storing: één binary_sensor per node op basis van de openstaande
    #    alarmen. Gekozen boven een tekstsensor met de laatste alerttekst omdat
    #    hij precies één ding robuust doet -- "is er iets mis, ja/nee" -- en dat
    #    is wat een HA-automatisering nodig heeft om te notificeren. De tekst zelf
    #    staat al op de alertenlijst van de site; die hier spiegelen zou een
    #    tweede bron zijn die kan gaan afwijken.
    open_alerts = db.alerts_open_count(rep["id"])
    add("alert", "binary_sensor",
        {"name": "Actieve storing", "device_class": "problem",
         "payload_on": "1", "payload_off": "0"},
        "1" if open_alerts else "0")

    # 3) Kanaalmetingen van een sensornode (ch<N>_...): de ping-monitors en de
    #    spanning/temperatuur. De NAAM komt uit channel_names -- dat is het punt.
    for name, row in sorted(latest.items()):
        ch = metrics.channel_metric(name)
        if ch is None:
            continue
        channel, kind = ch
        gezet = names.get(channel)
        human = ((gezet["name"] if gezet is not None else "") or "").strip()
        unit_set = ((gezet["unit"] if gezet is not None else "") or "").strip()
        label = human or f"kanaal {channel}"
        value = row["value"]
        if kind == "voltage":
            add(f"ch{channel}_voltage", "sensor",
                {"name": f"{label} spanning", "device_class": "voltage",
                 "unit_of_measurement": "V", "state_class": "measurement"},
                None if value is None else _num_str(value))
        elif kind == "temperature":
            add(f"ch{channel}_temperature", "sensor",
                {"name": f"{label} temperatuur", "device_class": "temperature",
                 "unit_of_measurement": "°C", "state_class": "measurement"},
                None if value is None else _num_str(value))
        elif kind == "switch":
            cfg = {"name": label, "payload_on": "1", "payload_off": "0"}
            dc = _switch_device_class(human)
            if dc:
                cfg["device_class"] = dc
            add(f"ch{channel}_switch", "binary_sensor", cfg,
                None if value is None else ("1" if value else "0"))
        elif kind == "generic":
            # De responstijd van een ping-monitor. Eenheid komt van de beheerder
            # (channel_names.unit, bv. "ms"); is die er, dan mag het een
            # duur-sensor zijn met een meetklasse.
            cfg = {"name": f"{label} meetwaarde", "state_class": "measurement"}
            if unit_set:
                cfg["unit_of_measurement"] = unit_set
                if unit_set.lower() in ("ms", "s"):
                    cfg["device_class"] = "duration"
            add(f"ch{channel}_generic", "sensor", cfg,
                None if value is None else _num_str(value))

    # 4) Telemetrie van een gewone repeater (bat/airtime/ruisvloer/...).
    for metric, (component, dc, unit, state_class) in REPEATER_METRICS.items():
        row = latest.get(metric)
        if row is None or row["value"] is None:
            continue
        # De catalogus levert het label; zo heet 'bat' in HA "Batterijspanning"
        # en niet "bat".
        section_label = metrics.CATALOG.get(metric)
        label = section_label[1] if section_label else metric.replace("_", " ")
        cfg = {"name": label, "state_class": state_class}
        if dc:
            cfg["device_class"] = dc
        if unit:
            cfg["unit_of_measurement"] = unit
        add(metric, component, cfg, _num_str(row["value"]))

    return ents


# --- publiceren --------------------------------------------------------------
def _publish(topic: str, payload: str, retain: bool = False) -> bool:
    cli = _client
    if cli is None:
        return False
    try:
        cli.publish(topic, payload, qos=0, retain=retain)
        return True
    except Exception as err:  # noqa: BLE001 - de broker mag wegvallen
        _state["last_error"] = f"{type(err).__name__}: {err}"
        return False


def _pub_key(node: str) -> str:
    return f"ha_pub:{node}"


def _publish_node(rep, latest: dict) -> int:
    """Publiceer config (als die veranderde), state en beschikbaarheid van één node.

    Geeft het aantal entiteiten van deze node terug.
    """
    from . import db
    node = rep["pubkey_prefix"]
    ents = _entities_for(rep, latest)

    # Config alleen herschrijven als de set of de vorm veranderde. Retained, dus
    # HA houdt hem hoe dan ook; elke ronde opnieuw schrijven zou alleen verkeer
    # kosten.
    desired = {_object_id(node, key): (component, cfg)
               for key, component, cfg, _ in ents}
    sig = json.dumps({oid: [component, cfg] for oid, (component, cfg)
                      in desired.items()}, sort_keys=True)
    if _config_sig.get(node) != sig:
        for oid, (component, cfg) in desired.items():
            if _publish(_config_topic(component, oid), json.dumps(cfg), retain=True):
                _state["config_msgs"] += 1
        _config_sig[node] = sig
        # Opruimen: object-id's die we eerder publiceerden maar nu niet meer
        # willen, krijgen een retained "" zodat HA de entiteit weggooit.
        _forget_removed(node, desired)

    # State: altijd, want dat is de goedkope kant en de reden dat we hier zijn.
    for key, component, cfg, state in ents:
        if state is None:
            continue
        if _publish(_state_topic(node, key), state, retain=True):
            _state["state_msgs"] += 1
    # Beschikbaarheid: als we hier zijn is de node zojuist gehoord -> online.
    _publish(_node_availability_topic(node), "online", retain=True)
    _state["last_publish"] = db.utcnow()
    return len(ents)


def _forget_removed(node: str, desired: dict) -> None:
    """Ruim config-topics op van entiteiten die niet meer bij deze node horen.

    ``desired`` is ``{object_id: (component, cfg)}``. De vorige set staat in
    ``settings`` (over een herstart heen); het verschil krijgt een retained "".
    """
    from . import db
    key = _pub_key(node)
    try:
        prev = json.loads(db.get_setting(key, "") or "{}")
    except (ValueError, TypeError):
        prev = {}
    for oid, component in prev.items():
        if oid not in desired:
            if _publish(_config_topic(component, oid), "", retain=True):
                _state["removed"] += 1
    db.set_setting(key, json.dumps({oid: comp for oid, (comp, _) in desired.items()}))


def forget_node(node: str) -> None:
    """Verwijder elke HA-entiteit van één node (bv. na scope-verandering).

    Publiceert een retained "" op elk onthouden config-topic en zet de node
    daarna op "offline". Gebruikt door de ronde voor nodes die uit scope vielen.
    """
    from . import db
    key = _pub_key(node)
    try:
        prev = json.loads(db.get_setting(key, "") or "{}")
    except (ValueError, TypeError):
        prev = {}
    for oid, component in prev.items():
        if _publish(_config_topic(component, oid), "", retain=True):
            _state["removed"] += 1
    _publish(_node_availability_topic(node), "offline", retain=True)
    db.set_setting(key, "{}")
    _config_sig.pop(node, None)


# --- companions als device_tracker -------------------------------------------
#
# Zelfde brug, zelfde conventies als de nodes hierboven -- alleen is de entiteit
# nu een ``device_tracker`` in plaats van een sensor, en hangt hij aan een
# companion (opt-in via ``companions.ha_publish``) in plaats van aan een node.
# De POSITIE reist via een json-attributen-topic (lat/lon/nauwkeurigheid/
# batterij), niet via de kale state: dat is het MQTT-device_tracker-contract van
# HA (``source_type: gps`` + ``json_attributes_topic``). De kale state draagt
# alleen een korte, retained tekst (:data:`COMPANION_STATE`) die de entiteit
# aanwezig houdt.
def _companion_attr_topic(node: str) -> str:
    return _state_topic(node, "attributes")


def _companion_state_topic(node: str) -> str:
    return _state_topic(node, "state")


def _companion_pub_key(object_id: str) -> str:
    return f"ha_pub_companion:{object_id}"


def _companion_config(comp) -> dict:
    """Het discovery-bericht (retained) van één companion-tracker."""
    object_id = _companion_object_id(comp)
    node = _companion_node(comp)
    model = (str(comp["type"] or "").strip() or COMPANION_DEFAULT_MODEL)
    return {
        "unique_id": object_id,
        "object_id": object_id,
        "name": comp["name"],
        "source_type": "gps",
        "state_topic": _companion_state_topic(node),
        "json_attributes_topic": _companion_attr_topic(node),
        "device": {
            "identifiers": [object_id],
            "name": comp["name"],
            "manufacturer": "MeshManager",
            "model": model,
            # Onder dezelfde brug als de nodes, zodat companions en nodes in HA
            # bij elkaar onder MeshManager staan.
            "via_device": "meshmanager_bridge",
        },
        "availability": _availability_block(node),
        "availability_mode": "all",
    }


def _companion_attributes(comp) -> dict:
    """De json-attributen die HA op de kaart zetten. ``battery_level`` alleen als
    de companion zijn batterij meldt -- een onbekende stand hoort niet als 0 op
    de kaart te verschijnen (zie companions._valid_batt)."""
    from . import db
    attrs = {
        "latitude": comp["last_lat"],
        "longitude": comp["last_lon"],
        "gps_accuracy": COMPANION_GPS_ACCURACY,
        "last_seen": db.iso_from_epoch(comp["last_seen"]),
    }
    if comp["batt"] is not None:
        attrs["battery_level"] = comp["batt"]
    return attrs


def _publish_companion(comp, now=None) -> None:
    """Config (als die veranderde), attributen, state en beschikbaarheid van één
    companion-tracker publiceren. Onthoudt in ``settings`` dat deze tracker
    bestaat (over een herstart heen), zodat de ronde hem later kan opruimen als
    de opt-in uit gaat of de companion verdwijnt."""
    from . import db
    object_id = _companion_object_id(comp)
    node = _companion_node(comp)
    cfg = _companion_config(comp)

    # Config alleen (her)schrijven als de vorm veranderde -- retained, net als bij
    # de nodes; elke ronde opnieuw schrijven zou alleen verkeer kosten.
    sig = json.dumps(cfg, sort_keys=True)
    if _companion_config_sig.get(object_id) != sig:
        if _publish(_config_topic("device_tracker", object_id), json.dumps(cfg),
                    retain=True):
            _state["config_msgs"] += 1
        _companion_config_sig[object_id] = sig
        db.set_setting(_companion_pub_key(object_id), node)

    # Attributen (de positie) en de state: retained, zodat HA ze na een
    # herverbinding meteen terugziet.
    if _publish(_companion_attr_topic(node), json.dumps(_companion_attributes(comp)),
                retain=True):
        _state["state_msgs"] += 1
    _publish(_companion_state_topic(node), COMPANION_STATE, retain=True)

    # Beschikbaarheid: online zolang de fix niet te oud is, anders offline --
    # dezelfde stiltedrempel als de nodes, zodat een companion die dagen geleden
    # voor het laatst gezien werd in HA grijs staat in plaats van vers te lijken.
    online = True
    if now is not None:
        mins = _minutes_since(db.iso_from_epoch(comp["last_seen"]), now)
        if mins is not None and mins >= STALE_MIN:
            online = False
    _publish(_node_availability_topic(node), "online" if online else "offline",
             retain=True)
    _state["last_publish"] = db.utcnow()


def _forget_companion(object_id: str, node: str) -> None:
    """Eén companion-tracker uit HA verwijderen: retained "" op zijn config-topic
    (de standaard MQTT-discovery-opruiming) en de beschikbaarheid op offline.
    Gebruikt door de ronde voor companions waarvan de opt-in uit ging, die geen
    positie meer hebben, of die uit de lijst verdwenen."""
    from . import db
    if _publish(_config_topic("device_tracker", object_id), "", retain=True):
        _state["removed"] += 1
    if node:
        _publish(_node_availability_topic(node), "offline", retain=True)
    # De onthoud-rij weg (en niet leeggemaakt zoals forget_node bij de nodes):
    # een companion-tracker is een op zichzelf staande entiteit, dus zodra hij
    # opgeruimd is hoeft de ronde er niet elke keer opnieuw een retained "" op te
    # blijven schrijven -- weghalen maakt de opruiming eenmalig.
    db.execute("DELETE FROM settings WHERE key=?", (_companion_pub_key(object_id),))
    _companion_config_sig.pop(object_id, None)


def _sweep_companions(now) -> int:
    """De companion-kant van de ronde: elke opt-in companion met een positie als
    device_tracker publiceren, en trackers opruimen die niet meer horen te
    bestaan (opt-in uit, positie weg, of companion verwijderd). Geeft het aantal
    gepubliceerde companion-trackers terug.

    De opruiming loopt via ``settings`` (net als ``forget_node`` voor de nodes):
    wat we ooit publiceerden onthouden we daar, en wat nu niet meer in de
    gewenste set zit, krijgt een retained "". Zo wordt een opt-in die uit gaat
    vanzelf bij de eerstvolgende ronde opgeruimd -- de toggle-route hoeft niets
    naar de broker te doen en blijft een gewone databankmutatie, precies zoals
    de node-kant scope-veranderingen in de ronde afhandelt en niet op de plek
    van de mutatie."""
    from . import db
    gewenst = {}
    published = 0
    for comp in db.companions_for_ha_publish():
        object_id = _companion_object_id(comp)
        gewenst[object_id] = _companion_node(comp)
        _publish_companion(comp, now)
        published += 1
    # Trackers die eerder gepubliceerd werden maar nu niet meer gewenst zijn.
    for row in db.q("SELECT DISTINCT key FROM settings "
                    "WHERE key LIKE 'ha_pub_companion:%'"):
        object_id = row["key"].split(":", 1)[1]
        if object_id not in gewenst:
            # De 'node' (voor het availability-topic) staat als waarde bewaard;
            # is die er niet, dan kan de config-opruiming nog steeds door.
            node = db.get_setting(row["key"], "") or ""
            _forget_companion(object_id, node)
    _state["published_companions"] = published
    return published


def _minutes_since(ts, now) -> float | None:
    from datetime import datetime
    try:
        a = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
        b = datetime.fromisoformat(str(now).replace("Z", "+00:00"))
        return (b - a).total_seconds() / 60.0
    except (TypeError, ValueError):
        return None


def _sweep() -> None:
    """Trage ronde: beschikbaarheid herijken en state opnieuw zetten.

    Publiceert voor elke node in scope, zet stil gevallen nodes op "offline" en
    ruimt nodes op die niet meer in scope zitten maar wel ooit gepubliceerd zijn.
    """
    from . import db
    now = db.utcnow()
    scope = _in_scope_nodes()
    in_scope_prefixes = {rep["pubkey_prefix"] for rep, _ in scope}
    published = 0
    entities = 0
    for rep, latest in scope:
        node = rep["pubkey_prefix"]
        entities += _publish_node(rep, latest)
        published += 1
        # Stil te lang? Dan alsnog op offline -- _publish_node zette hem net op
        # online omdat er ooit iets was, dus dit moet erná.
        mins = _minutes_since(rep["last_seen"], now)
        if mins is not None and mins >= STALE_MIN:
            _publish(_node_availability_topic(node), "offline", retain=True)
    # Nodes die eerder gepubliceerd werden maar nu buiten scope vallen.
    for row in db.q("SELECT DISTINCT key FROM settings WHERE key LIKE 'ha_pub:%'"):
        node = row["key"].split(":", 1)[1]
        if node not in in_scope_prefixes:
            forget_node(node)
    _state["published_nodes"] = published
    _state["published_entities"] = entities
    # De companions als device_tracker: opt-in met een bekende positie erop,
    # opt-in die uit ging (of positie/companion weg) eraf. Dezelfde ronde en
    # dezelfde retained-config-/opruim-conventies als de nodes hierboven.
    _sweep_companions(now)


# --- achtergronddraad --------------------------------------------------------
def on_ingest(repeater_id: int) -> None:
    """Ingest-haak: zet het node-id in de wachtrij. Niet-blokkerend, nooit werpen."""
    try:
        _queue.put_nowait(repeater_id)
    except Exception:  # noqa: BLE001 - een volle wachtrij mag de ingest niet raken
        pass


def _publish_one(repeater_id: int) -> None:
    from . import db
    rep = db.qone("SELECT * FROM repeaters WHERE id=?", (repeater_id,))
    if rep is None:
        return
    latest = db.latest_for(repeater_id)
    if _in_scope(rep, latest):
        _publish_node(rep, latest)


def _run() -> None:
    import paho.mqtt.client as mqtt

    def on_connect(client, userdata, flags, rc, properties=None):
        if rc == 0:
            _state["connected"] = True
            _state["connects"] += 1
            _state["last_error"] = ""
            # Brug online, en meteen een volledige ronde: HA moet na een
            # herverbinding weer weten wat er is.
            client.publish(_bridge_availability_topic(), "online", qos=0, retain=True)
            log.info("HA-discovery verbonden met %s:%s", HA_MQTT_HOST, HA_MQTT_PORT)
            try:
                _sweep()
            except Exception:  # noqa: BLE001
                log.exception("HA-discovery: eerste ronde na verbinden mislukt")
        else:
            _state["connected"] = False
            _state["refusals"] += 1
            _state["last_error"] = f"verbinding geweigerd (code {rc})"
            log.error("HA-discovery: verbinding geweigerd (code %s)", rc)

    def on_disconnect(client, userdata, rc, properties=None, reason=None):
        _state["connected"] = False
        log.info("HA-discovery losgekoppeld (%s); paho verbindt zelf opnieuw", rc)

    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=CLIENT_ID)
    if HA_MQTT_USER:
        client.username_pw_set(HA_MQTT_USER, HA_MQTT_PASS)
    client.on_connect = on_connect
    client.on_disconnect = on_disconnect
    # Last will: valt MeshManager (of de verbinding) weg, dan zet de broker de
    # brug op "offline" en worden alle entiteiten in HA "niet beschikbaar".
    client.will_set(_bridge_availability_topic(), "offline", qos=0, retain=True)
    client.reconnect_delay_set(min_delay=2, max_delay=60)
    global _client
    _client = client
    client.loop_start()

    # Verbinden mag mislukken zolang de broker weg is: paho probeert het dan zelf
    # opnieuw. De app-start hangt hier niet aan (deze functie draait al in een
    # eigen draad).
    while True:
        try:
            client.connect(HA_MQTT_HOST, HA_MQTT_PORT, keepalive=60)
            break
        except Exception as err:  # noqa: BLE001
            _state["last_error"] = f"{type(err).__name__}: {err}"
            log.warning("HA-discovery: verbinden mislukt (%s); nieuwe poging", err)
            import time
            time.sleep(10)

    # De werklus: verse metingen uit de wachtrij, en bij stilte een trage ronde.
    while True:
        try:
            rid = _queue.get(timeout=SWEEP_SECS)
        except queue.Empty:
            try:
                _sweep()
            except Exception:  # noqa: BLE001 - de lus mag nooit sterven
                log.exception("HA-discovery-ronde mislukt")
            continue
        try:
            # Even leegtrekken zodat een node die tien metingen per seconde
            # stuurt niet tien keer achter elkaar gepubliceerd wordt.
            ids = {rid}
            while True:
                try:
                    ids.add(_queue.get_nowait())
                except queue.Empty:
                    break
            for one in ids:
                _publish_one(one)
        except Exception:  # noqa: BLE001
            log.exception("HA-discovery: publiceren van een node mislukt")


def start() -> None:
    """Start de publisher, of leg uit waarom niet. Zelfde vorm als webpush.start."""
    global _thread
    st = status()
    if not st["enabled"]:
        print(f"[meshmanager] HA-discovery staat uit: {st['reason']}", flush=True)
        return
    from . import db
    db.register_ingest_hook(on_ingest)
    _state["started"] = db.utcnow()
    if _thread is not None:
        return
    _thread = threading.Thread(target=_run, name="hadiscovery", daemon=True)
    _thread.start()
