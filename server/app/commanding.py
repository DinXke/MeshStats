"""Welke weg heeft een opvraging vandaag nog, en wat mag de pagina beloven?

Een repeater bijwerken op verzoek kan langs twee wegen, en geen van beide is er
altijd:

**Rechtstreeks over MQTT.** De site publiceert één woord op ``meshcore/<node>/cmd``
en de node leest daarop zijn eigen CLI uit of stuurt meteen een statusbericht.
Dat werkt alleen als de node zelf publiceert, als zijn firmware dat topic kent
(MeshStats 1.8.0 en hoger) en als de broker op dit ogenblik verbonden is.

**Over MQTT naar de node die hem monitort.** Een repeater die zelf niet
publiceert, maar wiens cijfers doorgestuurd worden door een node die hem
uitleest, is niet onbereikbaar -- hij is alleen niet rechtstreeks bereikbaar.
De monitor logt al bij hem in en pollt hem al; sinds MeshStats 1.9.0 kan die
monitor op verzoek ook zijn CLI-instellingen over LoRa ophalen en publiceren.
De opdracht gaat dan naar de monitor (``settings <sleutel>``) en niet naar het
onderwerp.

Dat is precies het geval waarvoor dit project bestaat: de dakrepeater die dit
alles moet meten publiceert zelf niets. Tot 1.9.0 zei de knop over hem
"doorgestuurd, alleen de node zelf kan zijn eigen CLI uitlezen" -- waar en
onbruikbaar tegelijk.

**Via een poller.** De Home Assistant-integratie haalt ``GET /api/v1/commands``
op, vraagt de repeater over LoRa uit en POST het antwoord terug. Die weg blijft
bestaan, maar is nu de laatste keuze in plaats van de enige.

Dit bestand bestaat omdat het antwoord op "kan dit überhaupt?" uit vijf losse
plaatsen komt -- de repeaterrij, wie ervoor publiceert, de firmwareversie van
díe node, de brokerverbinding en het tijdstip waarop een poller voor het laatst
iets ophaalde -- en omdat de knop op de beheerpagina anders belooft wat niemand
kan waarmaken. Dat is precies wat er gebeurde toen Home Assistant uit de keten
verdween: de pagina bleef melden "Opvraging gestart -- Home Assistant logt in op
de repeater", terwijl het verzoek in een wachtrij lag die niemand nog leegde.

De functies hier raken niets aan. Ze beschrijven alleen wat mogelijk is, zodat
de route bepaald wordt vóór de knop getekend wordt en niet pas nadat erop
geklikt is.
"""
from datetime import datetime, timedelta, timezone

# Vanaf welke MeshStats-firmware een node opdrachten op het cmd-topic aanneemt.
# Ouder betekent niet "misschien": zo'n node schrijft zich niet in op het topic,
# dus de broker gooit het bericht weg zonder dat iemand het merkt.
MIN_CMD_VERSION = (1, 8, 0)

# Vanaf welke versie een monitorende node 'settings <sleutel>' aanneemt en die
# sweep over LoRa naar een gemonitorde repeater stuurt. Een 1.8.0-node kent het
# topic wél, maar weigert het argument en telt de opdracht als geweigerd -- dus
# ook hier is "misschien" geen optie.
MIN_MON_CMD_VERSION = (1, 9, 0)

# Hoe lang na de laatste poll we een poller nog als aanwezig beschouwen. De
# HA-integratie pollt om de 30 seconden; een kwartier stilte is dus geen
# vertraging maar een afwezigheid.
POLLER_STALE_SECS = 900

# Hoe lang na het laatste bericht van een node een opdracht een gok wordt. Het
# publicatie-interval loopt met de batterij mee en kan in zuinige modus oplopen,
# dus dit staat ruim: het is een waarschuwing op de pagina, geen weigering.
NODE_STALE_SECS = 3600

# Onder dit aantal hextekens kunnen twee sleutels toevallig samenvallen. Zelfde
# grens als db.MIN_PREFIX_MATCH, hier herhaald zodat dit bestand zonder database
# te testen is.
MIN_PREFIX_MATCH = 8

_TS = "%Y-%m-%dT%H:%M:%SZ"


def parse_version(text) -> tuple | None:
    """'1.8.0' -> (1, 8, 0). None als er geen versie in staat.

    Vergelijken gebeurt op getallen en niet op de string, want "1.10.0" komt
    alfabetisch vóór "1.8.0" en dat is net de firmware die het wél kan.
    """
    if not text:
        return None
    parts = str(text).strip().split(".")
    out = []
    for part in parts:
        digits = "".join(c for c in part if c.isdigit())
        if not digits:
            break
        out.append(int(digits))
    return tuple(out) or None


def same_key(a, b) -> bool:
    """Of twee sleutelprefixen dezelfde node aanduiden.

    Bronnen sturen verschillende lengtes -- Home Assistant vijf bytes, de eigen
    firmware zes -- dus de kortste moet een prefix zijn van de langste. Zelfde
    regel als db._find_by_prefix, want anders zou de pagina een node die zichzelf
    publiceert aanzien voor een die doorgestuurd wordt.
    """
    a = (a or "").lower().strip()
    b = (b or "").lower().strip()
    if not a or not b:
        return False
    if a == b:
        return True
    short, long = (a, b) if len(a) <= len(b) else (b, a)
    return len(short) >= MIN_PREFIX_MATCH and long.startswith(short)


def _parse(ts) -> datetime | None:
    if not ts:
        return None
    try:
        return datetime.strptime(str(ts), _TS).replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _fresh(ts, seconds: int, now: datetime) -> bool:
    dt = _parse(ts)
    return dt is not None and now - dt <= timedelta(seconds=seconds)


def _field(rep, name):
    """rep is een sqlite3.Row, een dict of None; alle drie komen hier binnen."""
    if rep is None:
        return None
    try:
        return rep[name]
    except (KeyError, IndexError):
        return None


def is_relayed(rep) -> bool:
    """Of de cijfers van deze repeater door een ándere node binnenkomen."""
    source = (_field(rep, "source_prefix") or "").lower().strip()
    if not source or source == "api":
        return False
    return not same_key(source, _field(rep, "pubkey_prefix"))


def route_for(rep, *, broker_connected: bool, poller_seen=None, now=None,
              relay=None) -> dict:
    """Wat kan er met deze repeater, nu meteen.

    ``blocker`` zegt waarom de MQTT-weg dicht is; de beheerpagina zet dat om in
    een zin. Leeg betekent dat ze open is.

    ``relay`` is de repeaterrij van de node die voor deze repeater publiceert,
    en hoort er alleen te staan als dat een ándere node is. Als argument en niet
    zelf opgezocht, om dezelfde reden als ``broker_connected``: zo blijft deze
    functie te testen zonder database. ``describe`` haalt hem erbij.

    Wat er teruggegeven wordt is bewust meer dan één ja/nee, want de twee wegen
    over MQTT kunnen niet hetzelfde:

    ``mqtt``         er kan nu iets vertrekken over MQTT
    ``commands``     wélke opdrachten die weg aankan. Een monitor kan voor een
                     ander gevraagd worden zijn instellingen op te halen, maar
                     niet om zijn statistieken te publiceren -- die stuurt hij
                     al vanzelf, op de rondes die hij zelf plant. Een knop die
                     'status' aanbiedt langs een weg die dat niet kent, is de
                     soort belofte die dit bestand moest wegwerken.
    ``via_monitor``  de opdracht gaat naar een andere node dan het onderwerp
    ``node``         de node die de opdracht krijgt
    ``subject``      de sleutel die in die opdracht meegaat, of None
    ``fw_meshstats`` de firmware van de node die de opdracht krijgt -- dus van
                     de monitor als het langs een monitor gaat. Dat is de versie
                     waar de bewering over gaat, en de pagina toont hem.
    """
    now = now or datetime.now(timezone.utc)
    node = (_field(rep, "source_prefix") or "").lower().strip()
    via_monitor = is_relayed(rep)

    # Bij een doorgestuurde repeater telt de firmware van de dóórstuurder: die
    # node krijgt de opdracht, en die moet ze kennen. De versie van het onderwerp
    # zegt hier niets -- vaak staat er niet eens een, want een node die zelf niet
    # publiceert meldt zijn MeshStats-versie nergens.
    fw = _field(relay, "fw_meshstats") if via_monitor else _field(rep, "fw_meshstats")
    version = parse_version(fw)
    needed = MIN_MON_CMD_VERSION if via_monitor else MIN_CMD_VERSION

    blocker = ""
    if not node:
        blocker = "no_source"
    elif node == "api":
        blocker = "http_source"
    elif via_monitor and relay is None:
        # De doorstuurder is hier zelf geen bekende repeater, dus van zijn
        # firmware weten we niets. Gokken kost een opdracht die stilletjes
        # geweigerd wordt aan de overkant.
        blocker = "relay_unknown"
    elif version is None:
        blocker = "no_fw"
    elif version < needed:
        blocker = "old_fw"
    elif not broker_connected:
        # Als laatste, zodat een tijdelijk wegvallende broker niet de blijvende
        # reden overschaduwt: 'firmware te oud' lost zichzelf niet op.
        blocker = "broker_down"

    open_ = blocker == ""
    return {
        "mqtt": open_,
        "commands": ("settings",) if via_monitor else ("settings", "status"),
        "via_monitor": via_monitor,
        "blocker": blocker,
        "node": node if node and node != "api" else None,
        "subject": (_field(rep, "pubkey_prefix") or "").lower().strip() or None,
        "fw_meshstats": fw,
        "min_fw": ".".join(str(n) for n in needed),
        "node_seen": _field(rep, "source_seen"),
        "node_stale": not _fresh(_field(rep, "source_seen"), NODE_STALE_SECS, now),
        "ha": _fresh(poller_seen, POLLER_STALE_SECS, now),
        "poller_seen": poller_seen,
    }


def describe(rep, **kwargs) -> dict:
    """route_for met de brokerstatus en de doorstuurder er zelf bij gehaald.

    De functie hierboven krijgt die als argument, zodat ze te testen is zonder
    een MQTT-client of een database in de buurt.
    """
    from . import db, mqtt_ingest
    kwargs.setdefault("broker_connected", mqtt_ingest.can_publish())
    kwargs.setdefault("poller_seen", db.poller_last_seen())
    if "relay" not in kwargs:
        # Alleen opzoeken als het écht een andere node is. find_repeater matcht
        # sleutels van verschillende lengte tegen elkaar, dus een node die
        # zichzelf publiceert zou anders zijn eigen rij als 'doorstuurder'
        # terugkrijgen -- klopt toevallig, maar het is een ander verhaal.
        kwargs["relay"] = (db.find_repeater(_field(rep, "source_prefix"))
                           if is_relayed(rep) else None)
    return route_for(rep, **kwargs)
