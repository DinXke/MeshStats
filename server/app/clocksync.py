"""Mag deze machine de rest van het mesh vertellen hoe laat het is?

Een MeshCore-node zet zijn eigen klok nooit uit zichzelf goed. Een ESP32 zonder
gebufferde RTC begint bij wat de firmware erin gebakken heeft -- MeshCore's
``clkreboot`` zet hem letterlijk op 15 mei 2024 -- en loopt daarna langzaam weg.
De repeater op het dak herstart uit zichzelf: lege accu, watchdog, een
stroomonderbreking in het onweerseizoen. Elke keer komt hij terug met een klok
die niets met vandaag te maken heeft, en alles wat hij daarna zegt draagt die
tijd mee.

Niemand op het mesh kan dat corrigeren, want niemand op het mesh weet het beter.
Deze machine wel, want die staat op een netwerk waar een NTP-cliënt draait. Dat
is de hele reden dat deze module bestaat, en meteen ook haar enige zwakke plek:
de bewering "wij weten hoe laat het is" moet waar zijn voordat we hem uitsturen.

Waarom dat zo streng is
-----------------------
De correctie gaat één kant op en is niet terug te draaien. De firmware zet een
klok alleen vooruit, en dat is geen eigenzinnigheid van ons: een advert draagt
de klok van de node die hem uitzendt, en iedere node die de afzender al kent
gooit een advert weg waarvan de tijdstempel niet gestegen is (``onAdvertRecv``
in MyMesh.cpp). Een klok een uur terugzetten is dus een uur onzichtbaarheid voor
een repeater op een dak. Daarom corrigeert de firmware nooit terug -- en daarom
is een tijd die te ver in de TOEKOMST ligt hier een fout die je niet meer
goedmaakt zonder ter plaatse te gaan.

Eén foute publicatie van deze module smeert dus een foute klok uit over elke
node die eraan hangt, en de weg terug loopt over een dak. Vandaar: bij twijfel
niet publiceren, en luid zeggen waarom niet.

Wat we hier feitelijk kúnnen vaststellen
----------------------------------------
Eerlijk zijn over de reikwijdte is hier belangrijker dan een groen vinkje.

*De hoofdcontrole* leest de tijddiscipline van de kernel via ``adjtimex(2)``.
Dat is precies waar ``timedatectl`` zijn ``NTPSynchronized`` vandaan haalt: de
vlag ``STA_UNSYNC`` in het statusveld, plus de foutmarge die de kernel zelf
bijhoudt. Het vraagt geen rechten, geen extra pakket en geen ``timedatectl`` in
de container -- die er in een slim Python-image ook niet is.

Maar: deze app draait in een container in een LXC-container op een Proxmox-host.
Een LXC deelt de klok van zijn host en mag hem niet zetten; ``timedatectl`` in
de LXC meldt dan ook ``NTP=no`` (geen NTP-cliënt hier) naast
``NTPSynchronized=yes`` (de kernel is wél gedisciplineerd). Wat wij lezen is dus
het oordeel van de HOST-kernel, doorgegeven. Dat is het beste signaal dat vanaf
deze plek bestaat, maar het is een doorgegeven bewering en geen eigen meting:
"de host zegt dat hij gelijkloopt" is iets anders dan "de tijd is aantoonbaar
juist". De beheerpagina zegt het in die woorden, en niet in geruststellender.

Praktisch gevolg, en het hoort in het rapport en niet alleen hier: de juistheid
van elke klok in dit mesh hangt uiteindelijk aan de NTP-instelling van de
Proxmox-host. Loopt die fout, dan loopt dit alles keurig, meetbaar en volledig
verkeerd mee.

*De tweede controle* kost niets en gelooft de kernel niet op zijn woord: de
wandklok wordt vergeleken met ``time.monotonic()``. Die twee horen even snel te
lopen. Springt de wandklok terwijl de monotone klok dat niet doet, dan is er
iets met de tijd gebeurd wat we niet vertrouwen, hoe tevreden de kernel ook is.
Daarnaast wordt de hoogste tijd die we ooit gezien hebben bewaard, zodat een
klok die na een herstart plots ver in het verleden staat opvalt.

*Verworpen: kruiscontrole tegen het mesh.* De suggestie lag voor de hand -- er
komen tijdstempels binnen van nodes -- maar de redenering is rond: de nodes
waartegen we zouden controleren zijn precies de nodes die hun tijd van ons
krijgen. Vinden we dat ze gelijklopen, dan hebben we bewezen dat ons eigen
bericht is aangekomen. Bovendien draagt het ``rx``-bericht ``t`` als
uptime-teller en niet als wandklok (zie mqtt_ingest.py), dus de bruikbare bron
is er niet eens.

*Verworpen: navragen bij een externe tijdbron.* Deze server zit achter VPN/LAN
en heeft geen uitgaande weg naar een NTP-server of een HTTP-``Date``-header die
iets bewijst. Een controle die in de ontwikkelomgeving werkt en op de echte
machine altijd "onbereikbaar" zegt, is een controle die na een week wordt
uitgezet.

Waarom dagelijks
----------------
Klokdrift op een ESP32 is traag: enkele seconden per dag, tientallen bij een
slechte oscillator of een hete zolder. De firmware corrigeert een gemonitorde
node pas vanaf twee minuten afwijking, dus dagelijks vragen is ruim een orde van
grootte vaker dan nodig om binnen die drempel te blijven -- en zendtijd is het
schaarse goed, niet rekenkracht.

Wat het interval wél bepaalt is iets anders: hoe lang een node die zojuist
herstart is met een klok uit 2024 mag rondlopen voor iemand hem bijzet. Een dag
is daarvoor de bovengrens die we accepteren. Korter zou die vensters verkleinen
zonder dat het meetbaar iets aan de drift doet, en het kost elke keer opnieuw
zendtijd op het dak. De node bewaakt zijn eigen kant trouwens ook: hij doet de
LoRa-helft hoogstens één keer per uur, wat er ook binnenkomt.
"""
import ctypes
import logging
import threading
import time
from datetime import datetime, timezone

from . import commanding, config, db, mqtt_ingest

log = logging.getLogger("meshmanager.clocksync")

# Uit zetten is een geldige keuze: wie zijn nodes met de hand bijzet, of wie
# deze server niet vertrouwt genoeg om er een mesh op te ijken, hoort dat te
# kunnen zeggen zonder de firmware terug te draaien.
ENABLED = config.env("CLOCKSYNC_ENABLED", "1").strip().lower() not in (
    "0", "false", "no", "nee", "off", "")

# Uren tussen twee rondes. Zie de motivering hierboven.
INTERVAL_HOURS = max(1, int(config.env("CLOCKSYNC_HOURS", "24")))

# Hoeveel onzekerheid de kernel over zijn eigen klok mag hebben voor wij hem nog
# geloven, in seconden.
#
# De kernel houdt ``maxerror`` bij en laat die tussen twee NTP-correcties door
# groeien met 500 ppm. Bij 16 s geeft hij het op en zet zelf STA_UNSYNC. Tien
# seconden is dus ruwweg "de host is in de laatste vijfeneenhalf uur nog
# bijgestuurd" -- streng genoeg om een NTP-cliënt te betrappen die vanmiddag
# gestopt is, ruim genoeg om een normale pollcyclus (tot 1024 s) nooit te raken.
MAX_ERROR_S = float(config.env("CLOCKSYNC_MAX_ERROR_S", "10"))

# Hoeveel de wandklok en de monotone klok tussen twee rondes uit elkaar mogen
# lopen voor we het een sprong noemen, in seconden. Ruim, want een NTP-cliënt
# MAG bijsturen -- daar is hij voor -- en een dagelijkse correctie van een halve
# seconde is gezond gedrag en geen alarm.
MAX_JUMP_S = float(config.env("CLOCKSYNC_MAX_JUMP_S", "30"))

# Vanaf welke nodefirmware een node 'time <epoch>' aanneemt. Ouder
# betekent niet "misschien": zo'n node weigert het woord en telt het als
# geweigerd, en niemand ziet dat hier.
MIN_TIME_VERSION = (1, 10, 0)

# Hoe lang na het laatste bericht van een node we het nog zinvol vinden om hem
# iets te vragen. Ruimer dan commanding.NODE_STALE_SECS, want dit is een
# weigering en geen waarschuwing: publiceren naar een node die al een dag stil
# is kost niets, maar het vult de logboeken met beloftes.
NODE_STALE_SECS = 6 * 3600

# Sleutel waaronder de hoogst geziene wandklok bewaard blijft, zodat een klok
# die na een herstart plots in het verleden staat opvalt.
_SEEN_KEY = "clocksync_high_water"

# Sleutel waaronder bijgehouden wordt wanneer we voor het laatst een tijd naar
# een node stuurden: {node_hex: epoch}. Zonder dat zou de handmatige knop
# "verstuurd" melden voor een ronde waarvan de node de dure helft overslaat.
_SENT_KEY = "clocksync_sent"
# Zoveel nodes onthouden we. Ruim boven elk denkbaar aantal publicerende nodes,
# en een bovengrens zodat dit instellingenveld niet ongemerkt blijft groeien met
# sleutels die nooit meer terugkomen.
_SENT_MAX = 50

# Minimum tussen twee handmatige synchronisaties naar dezelfde node.
#
# Spiegelt MON_CLK_MIN_GAP_MS in de firmware, met opzet hetzelfde getal. Wat dit
# wel en niet is, want dat scheelt: het is GEEN veiligheidsmaatregel. Die staat
# in de firmware, bij de code die de radio bezit, en ze is absoluut -- honderd
# keer klikken levert daar hoogstens één LoRa-ronde per uur op, wat er ook op het
# cmd-topic binnenkomt. De band valt met deze knop dus niet te bezetten, ook niet
# als deze regel er niet stond.
#
# Wat het wél is: eerlijkheid in de knop. Binnen het uur zou publiceren de node
# alleen zijn eigen klok laten zetten -- en die is dan net gezet door het vorige
# bericht -- terwijl de ronde langs de gemonitorde repeaters overgeslagen wordt
# zonder dat de pagina daar iets van ziet. "Verstuurd" melden terwijl de helft
# die ertoe doet niet gebeurt, is precies de belofte die commanding.py ooit
# moest wegwerken.
MANUAL_MIN_GAP_S = 3600

_TS = "%Y-%m-%dT%H:%M:%SZ"

# --- adjtimex(2) --------------------------------------------------------------

# Waarden uit <sys/timex.h>. Overgeschreven en niet geïmporteerd omdat Python
# geen binding voor deze struct heeft; de volgorde van de velden is ABI en
# verandert niet, de padding aan het eind vangt op wat een nieuwere kernel er
# eventueel bijzet.
STA_UNSYNC = 0x0040
TIME_ERROR = 5


class _Timex(ctypes.Structure):
    _fields_ = [
        ("modes", ctypes.c_int),
        ("offset", ctypes.c_long),
        ("freq", ctypes.c_long),
        ("maxerror", ctypes.c_long),
        ("esterror", ctypes.c_long),
        ("status", ctypes.c_int),
        ("constant", ctypes.c_long),
        ("precision", ctypes.c_long),
        ("tolerance", ctypes.c_long),
        ("time_sec", ctypes.c_long),      # struct timeval
        ("time_usec", ctypes.c_long),
        ("tick", ctypes.c_long),
        ("ppsfreq", ctypes.c_long),
        ("jitter", ctypes.c_long),
        ("shift", ctypes.c_int),
        ("stabil", ctypes.c_long),
        ("jitcnt", ctypes.c_long),
        ("calcnt", ctypes.c_long),
        ("errcnt", ctypes.c_long),
        ("stbcnt", ctypes.c_long),
        ("tai", ctypes.c_int),
        ("_padding", ctypes.c_int * 11),
    ]


def kernel_clock() -> dict:
    """Wat de kernel over zijn eigen klok zegt. Nooit een uitzondering.

    ``modes=0`` maakt dit een leesoproep: adjtimex mét modes zou de klok
    bijsturen en daar hebben we geen rechten voor en geen reden toe.

    ``ok`` is bewust alleen waar als beide signalen schoon zijn. Ze zeggen niet
    hetzelfde: STA_UNSYNC is het late oordeel (de kernel heeft het opgegeven),
    ``maxerror`` is het vroege (de foutmarge loopt op omdat er al een tijd niets
    bijgestuurd is). Wachten op het late oordeel betekent uren doorgaan met een
    klok waarvan de kernel zelf al niet meer zeker is.
    """
    out = {"available": False, "synchronised": False, "max_error_s": None,
           "detail": "", "ok": False}
    try:
        libc = ctypes.CDLL(None, use_errno=True)
        tx = _Timex()
        tx.modes = 0
        rc = libc.adjtimex(ctypes.byref(tx))
    except (OSError, AttributeError, ValueError, TypeError) as err:
        # Geen Linux, geen libc, of een kernel die dit niet aanbiedt. TypeError
        # staat erbij omdat ctypes op Windows dát opwerpt voor CDLL(None) -- daar
        # draait dit nooit in productie, maar wél als iemand de tests of de app
        # lokaal start, en dan hoort het antwoord "niet beschikbaar" te zijn en
        # geen stacktrace.
        #
        # Dat is meteen de hele houding van deze functie: geen antwoord is hier
        # geen "waarschijnlijk wel goed", het is een weigering.
        out["detail"] = f"adjtimex niet beschikbaar ({err})"
        return out

    if rc < 0:
        out["detail"] = "adjtimex gaf een fout terug"
        return out

    out["available"] = True
    out["synchronised"] = rc != TIME_ERROR and not (tx.status & STA_UNSYNC)
    out["max_error_s"] = tx.maxerror / 1_000_000.0

    if not out["synchronised"]:
        out["detail"] = ("de kernel meldt zijn klok als NIET gesynchroniseerd "
                         f"(status 0x{tx.status & 0xffff:04x}, rc {rc})")
        return out
    if out["max_error_s"] > MAX_ERROR_S:
        out["detail"] = (f"de kernel houdt {out['max_error_s']:.1f} s "
                         f"onzekerheid aan, meer dan de toegestane {MAX_ERROR_S:.0f} s")
        return out

    out["ok"] = True
    out["detail"] = (f"de kernel meldt gesynchroniseerd, onzekerheid "
                     f"{out['max_error_s']:.1f} s")
    return out


# --- de tweede controle: springt de wandklok? ---------------------------------

# Referentiepaar, gezet bij de eerste ronde van dit proces. Per proces en niet
# op schijf, en dat is een bewuste beperking: een monotone klok betekent niets
# meer na een herstart, dus bewaren zou een vergelijking opleveren die alleen
# maar overtuigend lijkt. Wat wél de herstart overleeft is de hoogste tijd die
# we ooit zagen, hieronder.
_ref_wall: float | None = None
_ref_mono: float | None = None


def _jump_check(now: float) -> tuple[bool, str]:
    """Loopt de wandklok even snel als de monotone klok?

    Verschuift de wandklok terwijl de monotone dat niet doet, dan is de tijd
    gezet in plaats van verlopen. Dat mag -- een NTP-cliënt hoort bij te sturen
    -- maar een correctie hoort klein te zijn. Een sprong van een uur is iets
    anders, en dan is de vraag welke van de twee kanten de juiste was; die vraag
    kunnen wij niet beantwoorden, dus publiceren we niet.
    """
    global _ref_wall, _ref_mono
    mono = time.monotonic()
    if _ref_wall is None:
        _ref_wall, _ref_mono = now, mono
        return True, "eerste ronde van dit proces; nog niets om mee te vergelijken"

    drift = (now - _ref_wall) - (mono - _ref_mono)
    # Het referentiepaar schuift altijd mee, ook na een afkeuring: anders
    # rapporteert elke volgende ronde dezelfde sprong opnieuw en blijft de
    # feature voorgoed uit na één correctie.
    _ref_wall, _ref_mono = now, mono
    if abs(drift) > MAX_JUMP_S:
        return False, (f"de wandklok verschoof {drift:+.0f} s ten opzichte van de "
                       f"monotone klok sinds de vorige ronde")
    return True, f"wandklok en monotone klok lopen gelijk ({drift:+.1f} s)"


def _backwards_check(now: float) -> tuple[bool, str]:
    """Is de tijd sinds de vorige ronde vooruitgegaan?

    Overleeft een herstart, in tegenstelling tot de controle hierboven. Vangt
    het geval waarin de host opstart zonder netwerk, de klok op zijn
    RTC-waarde of op de bouwdatum zet, en NTP nog niet is langsgeweest -- terwijl
    ``adjtimex`` op zo'n moment best tevreden kan zijn.

    De marge is er omdat dit een grens is en geen meting: een paar seconden
    achteruit is een NTP-correctie, een dag achteruit is een klok die opnieuw
    begonnen is.
    """
    try:
        seen = float(db.get_setting(_SEEN_KEY) or 0)
    except (TypeError, ValueError):
        seen = 0.0
    if seen and now < seen - MAX_JUMP_S:
        gap = seen - now
        return False, (f"de klok staat {gap / 3600:.1f} uur vroeger dan de hoogste "
                       "tijd die deze site ooit zag")
    if now > seen:
        try:
            db.set_setting(_SEEN_KEY, f"{now:.0f}")
        except Exception as err:  # noqa: BLE001 - een volle schijf mag dit niet fataal maken
            log.debug("hoogste tijd niet bewaard: %s", err)
    return True, "de klok is niet achteruitgelopen"


def check_clock(now: float | None = None) -> dict:
    """Alle controles samen. ``ok`` is de enige die telt voor publiceren."""
    now = time.time() if now is None else now
    kernel = kernel_clock()
    jump_ok, jump_detail = _jump_check(now)
    back_ok, back_detail = _backwards_check(now)

    ok = kernel["ok"] and jump_ok and back_ok
    if kernel["ok"]:
        reason = "" if ok else (jump_detail if not jump_ok else back_detail)
    else:
        reason = kernel["detail"]
    return {
        "ok": ok,
        "reason": reason,
        "kernel": kernel,
        "jump": {"ok": jump_ok, "detail": jump_detail},
        "backwards": {"ok": back_ok, "detail": back_detail},
        "epoch": int(now),
    }


# --- wie krijgt het bericht ---------------------------------------------------

def _fresh(ts, seconds: int, now: datetime) -> bool:
    if not ts:
        return False
    try:
        dt = datetime.strptime(str(ts), _TS).replace(tzinfo=timezone.utc)
    except ValueError:
        return False
    return (now - dt).total_seconds() <= seconds


# --- wanneer stuurden we deze node voor het laatst iets ----------------------

def _sent_map() -> dict:
    """{node_hex: epoch} van onze laatste tijdbericht per node."""
    import json
    try:
        raw = db.get_setting(_SENT_KEY) or "{}"
        data = json.loads(raw)
    except Exception:  # noqa: BLE001 - stuk veld is geen reden om de knop te breken
        return {}
    if not isinstance(data, dict):
        return {}
    out = {}
    for key, value in data.items():
        try:
            out[str(key)] = float(value)
        except (TypeError, ValueError):
            continue
    return out


def last_sent(node: str) -> float | None:
    """Wanneer deze site voor het laatst een tijd naar die node stuurde."""
    return _sent_map().get((node or "").lower().strip())


def last_sent_iso(node: str) -> str | None:
    """Hetzelfde, in de ISO-vorm die de pagina in een 'x minuten geleden' omzet."""
    when = last_sent(node)
    if when is None:
        return None
    return datetime.fromtimestamp(when, timezone.utc).strftime(_TS)


def _record_sent(node: str, when: float) -> None:
    import json
    node = (node or "").lower().strip()
    if not node:
        return
    data = _sent_map()
    data[node] = when
    if len(data) > _SENT_MAX:
        # Oudste eruit. Een node die er ooit was en nooit meer terugkomt hoort
        # dit veld niet eeuwig te laten groeien.
        for key in sorted(data, key=data.get)[:len(data) - _SENT_MAX]:
            data.pop(key, None)
    try:
        db.set_setting(_SENT_KEY, json.dumps(data))
    except Exception as err:  # noqa: BLE001 - een volle schijf mag de publicatie niet ongedaan maken
        log.debug("laatste verzending niet bewaard: %s", err)


def note_sent(node: str, when: float | None = None) -> None:
    """Leg vast dat er een tijd naar die node gegaan is, langs welke weg ook.

    Bestaat omdat er sinds de eigen API van een sensornode een tweede weg is
    waarlangs deze site een klok zet (``sensornode.set_clock``), en één grootboek
    beter is dan twee: "wanneer heeft deze site deze node voor het laatst de tijd
    gestuurd" hoort één antwoord te hebben. Wat hier NIET bij zit is het oordeel
    of dat mocht -- dat blijft ``check_clock``, en de andere weg roept die zelf
    aan.
    """
    _record_sent(node, time.time() if when is None else when)


def _rebooted_since(node: str, seconds_ago: float, now: float) -> bool:
    """Of die node herstart is sinds wij hem voor het laatst de tijd stuurden.

    Dit bestaat om één valse weigering te vermijden, en net die zou de feature
    op haar zwakste moment tegenhouden. Een node die zojuist herstartte staat op
    de datum uit zijn firmware -- dat is de toestand waar dit alles voor gebouwd
    is -- terwijl onze eigen administratie zegt dat we hem twintig minuten
    geleden nog de tijd stuurden. De knop zou dan "wacht nog veertig minuten"
    melden, precies wanneer wachten het slechtste antwoord is.

    De uptime komt uit het laatste statistiekbericht en is dus zelf al even oud;
    daarom wordt er bij geteld hoe lang geleden dat bericht binnenkwam. Zonder
    die correctie zou een node die tien minuten stil was er tien minuten jonger
    uitzien dan hij is, en dat is de kant die valse toestemming geeft.
    """
    row = db.find_repeater(node)
    if row is None:
        return False
    try:
        latest = db.latest_for(row["id"])
    except Exception:  # noqa: BLE001
        return False
    up = latest.get("uptime")
    if up is None or up["value"] is None:
        return False
    try:
        # De metriek staat in dagen (zie metrics.py), het bericht in ISO-tijd.
        uptime_s = float(up["value"]) * 86400.0
    except (TypeError, ValueError):
        return False
    measured = _parse_epoch(up["ts"])
    if measured is not None and now > measured:
        uptime_s += now - measured
    return uptime_s < seconds_ago


def _parse_epoch(ts) -> float | None:
    if not ts:
        return None
    try:
        return (datetime.strptime(str(ts), _TS)
                .replace(tzinfo=timezone.utc).timestamp())
    except ValueError:
        return None


# --- welke node kan de klok van deze repeater zetten --------------------------

def time_route(rep, relay=None, now=None, allow_monitor: bool = True) -> dict:
    """Wie krijgt 'time <epoch>' als het om deze repeater gaat, en kan dat nu.

    Twee gevallen, en het verschil is de hele reden dat dit een eigen functie is
    naast ``commanding.route_for``:

    - De repeater publiceert zelf. Het bericht gaat naar hemzelf; hij zet zijn
      eigen klok en loopt daarna zijn monitorlijst af.
    - De repeater wordt doorgestuurd (de dakrepeater). Het bericht gaat naar zijn
      monitor. Die zet zijn eigen klok en controleert de klokken van ALLE nodes
      die hij monitort -- niet alleen deze. Er is geen argument om dat toe te
      spitsen, en dat is geen omissie: de firmware loopt bij een klokronde de
      hele lijst af, want de ronde is per node goedkoop en per gemonitorde node
      één heen-en-weer. De pagina hoort dat te zeggen in plaats van te doen alsof
      de knop deze ene repeater aanwijst.

    ``allow_monitor=False`` sluit het tweede geval uit. Dat is wat de dagelijkse
    ronde nodig heeft: die loopt over álle repeaters, en als twee doorgestuurde
    repeaters dezelfde monitor hebben zou hij hem twee keer hetzelfde bericht
    sturen.

    ``commanding.route_for`` beantwoordt een naburige maar andere vraag -- kan ik
    deze repeater naar zijn INSTELLINGEN vragen -- met een versiegrens die van de
    weg afhangt (1.8.0 rechtstreeks, 1.9.0 via een monitor). Voor 'time' is die
    grens 1.10.0 langs beide wegen, want het is dezelfde ontvanger die hetzelfde
    woord moet kennen. Die twee in één functie proppen zou betekenen dat
    route_for per commando een andere versie gaat uitrekenen, en dat is precies
    de soort vertakking waar een verkeerde knop uit rolt.
    """
    now = now or datetime.now(timezone.utc)
    prefix = (commanding._field(rep, "pubkey_prefix") or "").lower().strip()
    source = (commanding._field(rep, "source_prefix") or "").lower().strip()
    via_monitor = commanding.is_relayed(rep)

    out = {"id": commanding._field(rep, "id"), "prefix": prefix,
           "name": commanding._field(rep, "name") or prefix,
           "node": None, "via_monitor": via_monitor, "ok": False,
           "blocker": "", "why": "", "fw_meshmanager": None}

    if via_monitor and not allow_monitor:
        out["blocker"] = "relayed"
        out["why"] = "krijgt zijn tijd van zijn monitor, over LoRa"
        return out
    if not source:
        out["blocker"] = "no_source"
        out["why"] = "publiceert niet over MQTT"
        return out
    if source == "api":
        out["blocker"] = "http_source"
        out["why"] = "komt binnen via de HTTP-API, niet over MQTT"
        return out

    # De ONTVANGER telt, en bij een doorgestuurde repeater is dat de monitor.
    # Diens firmware moet het woord kennen; die van het onderwerp zegt hier
    # niets -- een node die zelf niet publiceert meldt nergens een versie.
    if via_monitor and relay is None:
        relay = db.find_repeater(source)
    carrier = relay if via_monitor else rep
    if via_monitor and carrier is None:
        out["node"] = source
        out["blocker"] = "relay_unknown"
        out["why"] = "de doorstuurder is hier zelf geen bekende repeater"
        return out

    out["node"] = source
    fw = commanding._field(carrier, "fw_meshmanager")
    out["fw_meshmanager"] = fw
    version = commanding.parse_version(fw)
    if version is None:
        out["blocker"] = "no_fw"
        out["why"] = "firmwareversie onbekend"
        return out
    if version < MIN_TIME_VERSION:
        out["blocker"] = "old_fw"
        out["why"] = ("nodefirmware " + ".".join(str(n) for n in MIN_TIME_VERSION)
                      + " of nieuwer nodig")
        return out
    if not _fresh(commanding._field(carrier, "source_seen"), NODE_STALE_SECS, now):
        out["blocker"] = "stale"
        out["why"] = "al te lang niets van die node gehoord"
        return out

    out["ok"] = True
    out["why"] = ("gaat naar zijn monitor, die de klokken van zijn gemonitorde "
                  "repeaters nakijkt" if via_monitor else "krijgt de tijd rechtstreeks")
    return out


def targets(rows=None, now=None) -> list[dict]:
    """Nodes die 'time <epoch>' zelf kunnen aannemen.

    Alleen nodes die RECHTSTREEKS publiceren. Een doorgestuurde repeater krijgt
    zijn tijd niet van hier maar van zijn monitor, over LoRa -- dat is precies
    wat de firmware met dat commando doet, en dat is de enige weg erheen. Hem
    hier óók proberen zou betekenen publiceren op het cmd-topic van een node die
    er niet op luistert.

    Teruggegeven wordt een lijst met de sleutel, de naam en de reden waarom een
    node wel of niet meedoet, zodat de beheerpagina kan uitleggen waarom een
    repeater ontbreekt in plaats van hem stilletjes weg te laten.
    """
    now = now or datetime.now(timezone.utc)
    if rows is None:
        rows = db.q("SELECT * FROM repeaters ORDER BY sort_order, name")
    # Eén regel, op één plek: dezelfde functie die de knop gebruikt, met de
    # monitorweg dicht. Toen dit hier zijn eigen kopie van de redenering had,
    # kon de pagina van een repeater iets anders beweren dan de dagelijkse ronde
    # deed -- en dat verschil valt pas op als iemand de logboeken naast de
    # beheerpagina legt.
    return [time_route(rep, now=now, allow_monitor=False) for rep in rows]


# --- de ronde -----------------------------------------------------------------

_state = {
    "enabled": ENABLED,
    "interval_hours": INTERVAL_HOURS,
    "last_run": None,        # ISO-tijdstip van de laatste poging
    "last_ok": None,         # ISO-tijdstip van de laatste GESLAAGDE publicatie
    "last_result": "nog niet gedraaid",
    "last_reason": "",       # waarom er niets vertrok, als er niets vertrok
    "published": 0,          # nodes die deze ronde een bericht kregen
    "skipped": 0,
    "runs": 0,
    "refusals": 0,           # rondes die op de klokcontrole strandden
    "clock": None,           # laatste uitslag van check_clock()
    # Apart van last_ok/last_run, want het is een andere gebeurtenis: die twee
    # gaan over de planner, dit over iemand die op een knop drukte. Ze samen in
    # één veld tellen zou een beheerpagina opleveren waarop niet te zien is of
    # de dagelijkse ronde nog draait.
    "last_manual": None,     # ISO-tijdstip van de laatste handmatige synchronisatie
    "manual_node": None,     # naar welke node die ging
}


def status() -> dict:
    """Voor de beheerpagina."""
    return dict(_state)


def _publish_time(node: str, when: float) -> bool:
    """Publiceer 'time <epoch>' naar één node en onthoud dat we dat deden.

    ``when`` komt van de beller en wordt voor allebei gebruikt: het is de tijd
    die verstuurd wordt én de tijd die we noteren. Dat lijkt een detail en was
    het niet -- toen deze functie zelf ``time.time()`` las, stond er in de
    administratie een ander ogenblik dan er verstuurd was. Onzichtbaar in
    productie, want daar schelen ze microseconden, maar het betekende ook dat de
    wachttijdberekening in ``sync_now`` over een andere klok redeneerde dan
    degene die de notitie schreef. Eén ogenblik, één waarde.

    Alleen bij succes onthouden. Een mislukte publicatie mag de knop geen uur
    lang laten zeggen dat er net gesynchroniseerd is.
    """
    if not mqtt_ingest.publish_command(node, "time", epoch=int(when)):
        return False
    _record_sent(node, when)
    return True


def sync_now(rep, now: float | None = None) -> dict:
    """Eén synchronisatie, nu, voor de node die deze repeater kan bereiken.

    Geen tweede code-pad naast de dagelijkse ronde, en dat is het punt. De
    klokcontrole hieronder is letterlijk dezelfde ``check_clock`` die de planner
    aanroept, en het versturen loopt door dezelfde ``publish_command`` met
    dezelfde venstercontrole op de epoch. Een knop die zijn eigen weg naar de
    broker had gehad, zou een achterdeur om die controles heen zijn geweest --
    en de enige zichtbare aanwijzing daarvoor zou een verkeerde klok op een dak
    zijn geweest, weken later.

    Wat de knop NIET overdoet is de driftdrempel en de weigering voor een node
    die voorloopt. Die staan in de firmware, bij de code die meet en zendt, en
    ze gelden hier dus vanzelf: dit bericht is hetzelfde bericht.

    Teruggegeven wordt een ``outcome`` die de pagina in een zin omzet. Elk geval
    apart, want "er is niets gebeurd" heeft hier zes verschillende oorzaken en
    vijf ervan kan de gebruiker zelf verhelpen.
    """
    now = time.time() if now is None else now
    out = {"outcome": "", "node": None, "via_monitor": False, "reason": "",
           "wait_min": 0, "blocker": ""}

    if not ENABLED:
        out["outcome"] = "disabled"
        return out

    route = time_route(rep)
    out["node"] = route["node"]
    out["via_monitor"] = route["via_monitor"]
    out["blocker"] = route["blocker"]
    if not route["ok"]:
        out["outcome"] = "no_route"
        out["reason"] = route["why"]
        return out

    if not mqtt_ingest.can_publish():
        out["outcome"] = "no_route"
        out["blocker"] = "broker_down"
        out["reason"] = "de site hangt op dit ogenblik niet aan de broker"
        return out

    # De klokcontrole staat vóór de wachttijd, niet erna. Een server die niet
    # weet hoe laat het is, hoort dat te zeggen -- ook, en juist, als het
    # antwoord anders "wacht nog even" was geweest. Andersom zou iemand een uur
    # wachten om dan pas te horen dat het sowieso niet kon.
    check = check_clock(now)
    _state["clock"] = check
    if not check["ok"]:
        out["outcome"] = "no_clock"
        out["reason"] = check["reason"]
        log.warning("Handmatige kloksynchronisatie geweigerd: %s", check["reason"])
        return out

    previous = last_sent(route["node"])
    if previous is not None and now - previous < MANUAL_MIN_GAP_S:
        waited = now - previous
        # De ene uitzondering, en ze is de moeite: een node die intussen
        # herstartte staat op de datum uit zijn firmware. Dat is precies de
        # toestand waarvoor dit bestaat, en dan is wachten het slechtste
        # antwoord dat een knop kan geven.
        if _rebooted_since(route["node"], waited, now):
            log.info("Node %s herstartte sinds de vorige tijd; wachttijd vervalt",
                     route["node"])
        else:
            out["outcome"] = "too_soon"
            out["wait_min"] = max(1, int((MANUAL_MIN_GAP_S - waited) // 60) + 1)
            return out

    if not _publish_time(route["node"], now):
        out["outcome"] = "failed"
        out["reason"] = "de opdracht is niet van deze machine vertrokken"
        return out

    _state["last_manual"] = db.utcnow()
    _state["manual_node"] = route["node"]
    log.info("Handmatige kloksynchronisatie verstuurd naar node %s", route["node"])
    out["outcome"] = "sent"
    return out


def run_once(now: float | None = None) -> dict:
    """Eén ronde: klok controleren, en pas dan publiceren.

    De volgorde is de hele functie. Controleren gebeurt vóór er ook maar één
    node uitgezocht wordt, zodat er geen pad bestaat waarlangs een bericht
    vertrekt terwijl de controle nog moest komen.
    """
    now = time.time() if now is None else now
    check = check_clock(now)
    _state["runs"] += 1
    _state["last_run"] = db.utcnow()
    _state["clock"] = check

    if not check["ok"]:
        _state["refusals"] += 1
        _state["published"] = 0
        _state["last_result"] = "geweigerd"
        _state["last_reason"] = check["reason"]
        # WARNING en niet DEBUG: dit is de toestand waarin de feature stilvalt,
        # en stil stilvallen is precies wat dit project niet doet.
        log.warning("Kloksynchronisatie overgeslagen: %s", check["reason"])
        return dict(_state)

    if not mqtt_ingest.can_publish():
        _state["published"] = 0
        _state["last_result"] = "geen brokerverbinding"
        _state["last_reason"] = "de site hangt op dit ogenblik niet aan de broker"
        log.warning("Kloksynchronisatie overgeslagen: geen brokerverbinding")
        return dict(_state)

    rows = targets()
    sent = 0
    for entry in rows:
        if not entry["ok"]:
            continue
        # De epoch wordt per node opnieuw gelezen. Een ronde over een handvol
        # nodes duurt milliseconden, dus het verschil is verwaarloosbaar -- maar
        # één waarde hergebruiken zou betekenen dat de laatste node een tijd
        # krijgt die ouder is dan het bericht zelf, en dat is precies het soort
        # detail waar dit bestand over gaat.
        if _publish_time(entry["node"], time.time()):
            sent += 1
        else:
            entry["ok"] = False
            entry["why"] = "publicatie mislukt"

    _state["published"] = sent
    _state["skipped"] = sum(1 for e in rows if not e["ok"])
    _state["last_reason"] = ""
    if sent:
        _state["last_ok"] = db.utcnow()
        _state["last_result"] = f"{sent} node(s) bijgezet"
        log.info("Kloksynchronisatie: %d node(s) kregen de tijd", sent)
    else:
        _state["last_result"] = "geen enkele node kon bereikt worden"
        _state["last_reason"] = ("geen node publiceert rechtstreeks met de nodefirmware "
                                 + ".".join(str(n) for n in MIN_TIME_VERSION) + " of nieuwer")
        log.info("Kloksynchronisatie: geen enkele node kwam in aanmerking")
    return dict(_state)


# Hoe lang na het opstarten de eerste ronde volgt. Kort, maar niet meteen: de
# MQTT-verbinding moet er zijn en de nodes moeten zich gemeld hebben, anders
# strandt de eerste ronde altijd op "geen brokerverbinding". Ook nuttig na een
# herstart van de site die op een stroomstoring volgde -- dan is er een goede
# kans dat de nodes óók net herstart zijn, met een klok uit 2024.
FIRST_RUN_DELAY_S = 300


def _run() -> None:
    time.sleep(FIRST_RUN_DELAY_S)
    while True:
        try:
            run_once()
        except Exception as err:  # noqa: BLE001 - een ronde mag de thread niet doden
            log.exception("Kloksynchronisatie mislukte onverwacht: %s", err)
            _state["last_result"] = "onverwachte fout"
            _state["last_reason"] = str(err)
        time.sleep(INTERVAL_HOURS * 3600)


_thread = None


def start() -> None:
    """Start de planner. Doet niets als de feature uit staat."""
    global _thread
    if not ENABLED:
        log.info("Kloksynchronisatie staat uit (MM_CLOCKSYNC_ENABLED)")
        _state["last_result"] = "uitgeschakeld"
        return
    if _thread is not None:
        return
    # Meteen bij het opstarten één keer meten en loggen, zonder te publiceren.
    # Zo staat er in het logboek van dag één of deze machine überhaupt in
    # aanmerking komt, in plaats van pas over vijf minuten -- of nooit, als de
    # eerste ronde op iets anders strandt.
    check = check_clock()
    _state["clock"] = check
    log.info("Klok van deze server: %s", check["reason"] or check["kernel"]["detail"])
    _thread = threading.Thread(target=_run, name="clocksync", daemon=True)
    _thread.start()
