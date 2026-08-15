"""Welke weg heeft een opvraging vandaag nog, en wat mag de pagina beloven?

Een repeater bijwerken op verzoek kan langs twee wegen, en geen van beide is er
altijd:

**Rechtstreeks over MQTT.** De site publiceert één woord op ``meshcore/<node>/cmd``
en de node leest daarop zijn eigen CLI uit of stuurt meteen een statusbericht.
Dat werkt alleen als de node zelf publiceert (niet doorgestuurd door een ander),
als zijn firmware dat topic kent (MeshStats 1.8.0 en hoger) en als de broker op
dit ogenblik verbonden is.

**Via een poller.** De Home Assistant-integratie haalt ``GET /api/v1/commands``
op, vraagt de repeater over LoRa uit en POST het antwoord terug. Die weg blijft
bestaan, maar is nu de tweede keuze in plaats van de enige.

Dit bestand bestaat omdat het antwoord op "kan dit überhaupt?" uit vier losse
plaatsen komt -- de repeaterrij, de firmwareversie, de brokerverbinding en het
tijdstip waarop een poller voor het laatst iets ophaalde -- en omdat de knop op
de beheerpagina anders belooft wat niemand kan waarmaken. Dat is precies wat er
gebeurde toen Home Assistant uit de keten verdween: de pagina bleef melden
"Opvraging gestart -- Home Assistant logt in op de repeater", terwijl het
verzoek in een wachtrij lag die niemand nog leegde.

De functies hier raken niets aan. Ze beschrijven alleen wat mogelijk is, zodat
de route bepaald wordt vóór de knop getekend wordt en niet pas nadat erop
geklikt is.
"""
from datetime import datetime, timedelta, timezone

# Vanaf welke MeshStats-firmware een node opdrachten op het cmd-topic aanneemt.
# Ouder betekent niet "misschien": zo'n node schrijft zich niet in op het topic,
# dus de broker gooit het bericht weg zonder dat iemand het merkt.
MIN_CMD_VERSION = (1, 8, 0)

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
    """rep is een sqlite3.Row of een dict; allebei komen hier binnen."""
    try:
        return rep[name]
    except (KeyError, IndexError):
        return None


def route_for(rep, *, broker_connected: bool, poller_seen=None, now=None) -> dict:
    """Wat kan er met deze repeater, nu meteen.

    ``blocker`` zegt waarom de MQTT-weg dicht is; de beheerpagina zet dat om in
    een zin. Leeg betekent dat ze open is.
    """
    now = now or datetime.now(timezone.utc)
    node = (_field(rep, "source_prefix") or "").lower().strip()
    fw = _field(rep, "fw_meshstats")
    version = parse_version(fw)

    blocker = ""
    if not node:
        blocker = "no_source"
    elif node == "api":
        blocker = "http_source"
    elif not same_key(node, _field(rep, "pubkey_prefix")):
        blocker = "relayed"
    elif version is None:
        blocker = "no_fw"
    elif version < MIN_CMD_VERSION:
        blocker = "old_fw"
    elif not broker_connected:
        # Als laatste, zodat een tijdelijk wegvallende broker niet de blijvende
        # reden overschaduwt: 'firmware te oud' lost zichzelf niet op.
        blocker = "broker_down"

    return {
        "mqtt": blocker == "",
        "blocker": blocker,
        "node": node if node and node != "api" else None,
        "fw_meshstats": fw,
        "node_seen": _field(rep, "source_seen"),
        "node_stale": not _fresh(_field(rep, "source_seen"), NODE_STALE_SECS, now),
        "ha": _fresh(poller_seen, POLLER_STALE_SECS, now),
        "poller_seen": poller_seen,
    }


def describe(rep, **kwargs) -> dict:
    """route_for met de brokerstatus er zelf bij gehaald.

    De aparte functie hierboven krijgt die als argument, zodat ze te testen is
    zonder een MQTT-client in de buurt.
    """
    from . import db, mqtt_ingest
    kwargs.setdefault("broker_connected", mqtt_ingest.can_publish())
    kwargs.setdefault("poller_seen", db.poller_last_seen())
    return route_for(rep, **kwargs)
