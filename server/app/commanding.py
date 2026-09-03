"""Welke weg heeft een opvraging vandaag nog, en wat mag de pagina beloven?

Een repeater bijwerken op verzoek kan langs twee wegen, en geen van beide is er
altijd:

**Rechtstreeks over MQTT.** De site publiceert één woord op ``<voorvoegsel>/<node>/cmd``
en de node leest daarop zijn eigen CLI uit of stuurt meteen een statusbericht.
Dat werkt alleen als de node zelf publiceert, als zijn firmware dat topic kent
(nodefirmware 1.8.0 en hoger) en als de broker op dit ogenblik verbonden is.

**Over MQTT naar de node die hem monitort.** Een repeater die zelf niet
publiceert, maar wiens cijfers doorgestuurd worden door een node die hem
uitleest, is niet onbereikbaar -- hij is alleen niet rechtstreeks bereikbaar.
De monitor logt al bij hem in en pollt hem al; sinds nodefirmware 1.9.0 kan die
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

# Vanaf welke nodefirmware een node opdrachten op het cmd-topic aanneemt.
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

# Hoe lang na de laatste geslaagde polling de eigen API van een sensornode nog
# als aanwezig geldt. De pollronde loopt elke 300 s (sensornode.INTERVAL_S), dus
# dit is ruwweg "twee rondes overgeslagen". Hier hardcoded en niet uit die module
# gehaald, om dezelfde reden als bij POLLER_STALE_SECS hierboven: dit bestand is
# met opzet te lezen en te testen zonder de rest van de app erbij, en het geeft
# alleen een oordeel -- het polt zelf niets. test_sensornode.py houdt de twee
# getallen tegen elkaar aan zodat een korter interval hier niet stil blijft staan.
#
# Waarom een grens en niet "de laatste keer telt altijd": deze weg loopt over
# WiFi, en op batterij is dat een pad dat komt en gaat. Zonder grens zou de
# pagina van een node die vanmorgen nog antwoordde en sindsdien van het net is,
# nog steeds zeggen dat de knoppen werken.
IP_API_STALE_SECS = 600

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
              relay=None, poller_name=None) -> dict:
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
    ``fw_meshmanager`` de firmware van de node die de opdracht krijgt -- dus van
                     de monitor als het langs een monitor gaat. Dat is de versie
                     waar de bewering over gaat, en de pagina toont hem.
    ``level``        het beheerniveau van deze node: 'unmanaged', 'semi_managed'
                     of 'full_managed'. Een waarneming, geen instelling -- zie
                     ``_level`` hieronder.
    ``level_why``    waarom dat niveau, in het Nederlands en met de node erbij
                     die het mogelijk maakt.
    """
    now = now or datetime.now(timezone.utc)
    node = (_field(rep, "source_prefix") or "").lower().strip()
    via_monitor = is_relayed(rep)

    # Bij een doorgestuurde repeater telt de firmware van de dóórstuurder: die
    # node krijgt de opdracht, en die moet ze kennen. De versie van het onderwerp
    # zegt hier niets -- vaak staat er niet eens een, want een node die zelf niet
    # publiceert meldt zijn firmwareversie nergens.
    fw = _field(relay, "fw_meshmanager") if via_monitor else _field(rep, "fw_meshmanager")
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
    poller_fresh = _fresh(poller_seen, POLLER_STALE_SECS, now)
    level, level_why = _level(rep, via_monitor=via_monitor, relay=relay,
                              poller_fresh=poller_fresh)
    return {
        "mqtt": open_,
        # Wat deze node IS, naast wat er nu open staat. Zie _level hierboven voor
        # waarom die twee uit elkaar gehouden worden.
        "level": level,
        "level_why": level_why,
        "commands": ("settings",) if via_monitor else ("settings", "status"),
        "via_monitor": via_monitor,
        "blocker": blocker,
        "node": node if node and node != "api" else None,
        "subject": (_field(rep, "pubkey_prefix") or "").lower().strip() or None,
        "fw_meshmanager": fw,
        "min_fw": ".".join(str(n) for n in needed),
        "node_seen": _field(rep, "source_seen"),
        "node_stale": not _fresh(_field(rep, "source_seen"), NODE_STALE_SECS, now),
        # Heette "ha", naar de enige poller die er toen was. Nu MeshUptime de
        # wachtrij ook kan bedienen zegt die naam iets wat niet meer klopt; de
        # eigenschap is "er is een verse poller", welke dan ook.
        "poller": poller_fresh,
        "poller_name": poller_name,
        "poller_seen": poller_seen,
        # De eigen API van de node, als waarneming en naast het niveau. Het
        # niveau zegt wat deze node IS; dit zegt of die weg NU nog draagt, en dat
        # is een ander antwoord -- precies de tweedeling die ``mqtt`` naast
        # ``level`` ook maakt. ``stale`` weegt op het pollinterval en niet op een
        # eigen getal: valt er meer dan twee rondes niets binnen, dan is dat geen
        # vertraging maar een afwezigheid, en over WiFi op batterij is dat een
        # gemeten en geen theoretisch geval.
        "ip_api": _ip_api(rep, now),
    }


def _ip_api(rep, now: datetime) -> dict:
    """Wat de eigen API van deze node laatst deed. Leeg als hij er geen heeft.

    Als eigen sleutel en niet verwerkt in ``blocker``, want die gaat over de
    MQTT-weg en deze weg is de tegenovergestelde: hij werkt juist als de broker
    weg is, en hij valt weg als de WiFi weg is. Ze in één veld persen zou van
    twee onafhankelijke wegen één toestand maken.
    """
    host = (_field(rep, "sensor_host") or "").strip()
    seen = _field(rep, "sensor_seen")
    return {
        "host": host,
        "seen": seen,
        "fw": _field(rep, "sensor_fw") or "",
        "ever": bool(host and seen),
        "fresh": _fresh(seen, IP_API_STALE_SECS, now),
        "stale_after_s": IP_API_STALE_SECS,
    }


# --- beheerniveau ------------------------------------------------------------
#
# Drie niveaus, en ze zijn een WAARNEMING en geen instelling. Nergens staat een
# knop om ze te zetten: ze volgen uit wat er binnenkomt, en ze verschuiven vanzelf
# zodra een node zijn netwerkverbinding verliest of andere firmware krijgt.
#
#   unmanaged  alleen telemetrie. We zien hem in het verkeer en verder niets:
#              geen wachtwoord, geen weg om iets te vragen. Er is dus ook geen
#              handeling die op zo'n node kan slagen.
#   semi       geen eigen firmware van ons, maar wél rechten op zijn CLI --
#              bereikt over LoRa door een node die hem monitort, of door de
#              poller die met het repeaterwachtwoord inlogt. Instellingen lezen
#              en begrensd schrijven kan, de klok zetten kan; firmware schrijven
#              niet, en eigen statistieken stuurt hij niet.
#   full       onze firmware met MQTT-koppeling. Alles kan, firmware-upgrade
#              inbegrepen.
#
# Waarom dit náást ``mqtt``/``ha`` staat en die niet vervangt: die twee zeggen
# wat er op DIT OGENBLIK openstaat, en dat is iets anders dan wat deze node is.
# Een full-managed node achter een weggevallen broker blijft full managed -- er
# is alleen nu geen weg. Precies daarom kijkt de berekening hieronder niet naar
# ``broker_connected``: anders zou het niveau van een node op en neer springen
# met de netwerkverbinding van de server, en zou "semi-managed" iets over ons
# gaan zeggen in plaats van over hem.
#
# Verworpen alternatief: het niveau als kolom in de repeaters-tabel, door de
# beheerder in te stellen. Dat leest prettig -- je zegt wat een node is -- maar
# het loopt gegarandeerd uit de pas met de werkelijkheid, en dan staat er een
# knop die "kan" zegt over een node die zijn firmware kwijt is.
# De waarden staan voluit en niet afgekort: ze reizen mee in JSON-antwoorden en
# "semi" alleen zegt daar niets.
LEVEL_UNMANAGED = "unmanaged"
LEVEL_SEMI = "semi_managed"
LEVEL_FULL = "full_managed"


def _level(rep, *, via_monitor: bool, relay, poller_fresh: bool) -> tuple[str, str]:
    """(niveau, waargenomen reden). De reden is Nederlandse tekst voor op het scherm.

    Toetsvolgorde zoals afgesproken: eerst full managed, dan semi, dan de rest.
    ``level_why`` is bewust geen code die een template in een zin omzet -- anders
    dan ``blocker`` hierboven -- omdat de reden hier de node bij naam noemt en
    dat op elke plek waar het niveau opduikt hetzelfde hoort te luiden.

    Wat hier NIET in zit: of er een firmware-upgrade mogelijk is. Dat is een
    eigen eigenschap en geen vierde niveau -- een full managed node zonder
    IP-pad neemt commando's aan maar geen image van ruim een megabyte, en een
    node waarvan we de bouwomgeving niet kennen mag er sowieso geen krijgen
    (verkeerd board = kapotte node). Die sleutel komt uit de firmwareweg.
    """
    source = (_field(rep, "source_prefix") or "").lower().strip()
    fw = _field(relay, "fw_meshmanager") if via_monitor else _field(rep, "fw_meshmanager")
    version = parse_version(fw)

    # Full managed: de node publiceert zijn eigen cijfers over MQTT en meldt een
    # firmwareversie. Dan bestaat zijn cmd-topic en kan de site hem
    # rechtstreeks aansturen. Een doorgestuurde repeater kan dit per definitie
    # niet zijn: de versie die we dan kennen is die van de doorstuurder.
    if source and source != "api" and not via_monitor and version is not None:
        return LEVEL_FULL, f"publiceert zelf over MQTT met nodefirmware {fw}"

    # Full managed, tweede vorm: de node biedt zijn EIGEN API aan over IP, en die
    # heeft ook geantwoord. Andere firmware, hetzelfde niveau -- en dat laatste is
    # een uitspraak die uitleg verdient.
    #
    # Waarom dit niet 'semi' is. Het niveau weegt drie dingen: is er een weg,
    # staat er een geauthenticeerde tegenpartij tegenover, en valt er te
    # controleren wat er gebeurd is. Deze weg scoort op alle drie minstens zo hoog
    # als de MQTT-weg: de weblogin van de node zelf in plaats van wie de broker
    # binnenliet, een antwoord in hetzelfde verzoek in plaats van een bericht dat
    # later langskomt, en tienden van seconden in plaats van tientallen. Hem
    # 'semi' noemen zou het woord laten betekenen "draait onze firmware niet" in
    # plaats van "beperkt te beheren", en dan gaat het niveau over ONS in plaats
    # van over hem.
    #
    # Waarom er niet naar de VERSHEID van sensor_seen gekeken wordt: om dezelfde
    # reden dat deze berekening niet naar broker_connected kijkt. Het niveau zegt
    # wat deze node IS en niet wat er nu openstaat. Een node waarvan de WiFi net
    # wegviel is nog steeds een node met een eigen API -- er is alleen nu geen
    # weg, en dat hoort de pagina te tonen in plaats van stil in het niveau te
    # verdwijnen. Anders springt het niveau op en neer met een netwerkverbinding.
    #
    # De prijs staat erbij: een node die deze API ooit had en nu voorgoed van het
    # net is, blijft full managed heten. Dat is dezelfde prijs die de MQTT-weg al
    # betaalt (``fw_meshmanager`` blijft ook staan als een node nooit meer
    # publiceert) en het is de goedkoopste van de twee fouten.
    sensor_host = (_field(rep, "sensor_host") or "").strip()
    sensor_seen = _field(rep, "sensor_seen")
    if sensor_host and sensor_seen:
        return LEVEL_FULL, (f"biedt zijn eigen API aan over IP op {sensor_host}; "
                            f"laatst geantwoord {sensor_seen}")

    # Semi managed: geen eigen firmware van ons, maar wel rechten op zijn CLI.
    if via_monitor and version is not None and version >= MIN_MON_CMD_VERSION:
        who = _field(relay, "name") or _field(rep, "source_prefix")
        return LEVEL_SEMI, f"bereikbaar via {who} over LoRa"

    # De poller staat niet met zoveel woorden in de afgesproken regel, en hij
    # hoort er toch bij: de Home Assistant-integratie logt met het
    # repeaterwachtwoord in en leest en schrijft dezelfde CLI. Hem hier weglaten
    # zou een repeater die alleen zo binnenkomt "unmanaged -- alleen waargenomen
    # in het verkeer" noemen terwijl de knop ernaast werkt, en dat is precies de
    # oneerlijkheid die commanding.py bestaat om te voorkomen. Het bewijs is wel
    # brozer dan een monitor -- het vervalt zodra de poller een kwartier zwijgt --
    # en dat staat er daarom bij.
    if poller_fresh:
        return LEVEL_SEMI, "bereikbaar via de poller over LoRa, zolang die pollt"

    if not source:
        return LEVEL_UNMANAGED, "nog geen enkel bericht van deze node binnengekomen"
    if source == "api":
        return LEVEL_UNMANAGED, "cijfers komen via de HTTP-API; geen weg met rechten"
    if via_monitor and relay is None:
        return LEVEL_UNMANAGED, "doorgestuurd door een node die hier zelf niet bekend is"
    return LEVEL_UNMANAGED, "alleen waargenomen in het verkeer"


def describe(rep, **kwargs) -> dict:
    """route_for met de brokerstatus en de doorstuurder er zelf bij gehaald.

    De functie hierboven krijgt die als argument, zodat ze te testen is zonder
    een MQTT-client of een database in de buurt.
    """
    from . import db, mqtt_ingest
    kwargs.setdefault("broker_connected", mqtt_ingest.can_publish())
    kwargs.setdefault("poller_seen", db.poller_last_seen())
    kwargs.setdefault("poller_name", db.poller_last_name())
    if "relay" not in kwargs:
        # Alleen opzoeken als het écht een andere node is. find_repeater matcht
        # sleutels van verschillende lengte tegen elkaar, dus een node die
        # zichzelf publiceert zou anders zijn eigen rij als 'doorstuurder'
        # terugkrijgen -- klopt toevallig, maar het is een ander verhaal.
        kwargs["relay"] = (db.find_repeater(_field(rep, "source_prefix"))
                           if is_relayed(rep) else None)
    return route_for(rep, **kwargs)
