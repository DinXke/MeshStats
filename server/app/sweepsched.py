"""Planner voor het uitvragen van CLI-instellingen, per node instelbaar.

Waarom dit er is, en waarom het er niet meteen was. Een node die zelf publiceert
leest zijn eigen instellingen één keer per dag uit; dat kost niets, want het is
een functieaanroep in zijn eigen firmware. Een node die alleen over LoRa te
bereiken is, wordt uitgevraagd door zijn monitor, en dan is één ronde twintig
vragen en twintig antwoorden over een gedeelde band -- betaald door een repeater
op een dak. Vandaar de oorspronkelijke keuze: voor gemonitorde nodes alleen op
verzoek, nooit vanzelf.

Die keuze was verdedigbaar en in de praktijk verkeerd. Wat ze opleverde was dat
niemand ooit verse waarden zag tenzij hij eraan dacht te klikken -- gemeten:
instellingen van twaalf uur oud, en een regiotabel van zeven dagen oud, op een
node die perfect antwoordde zodra iemand het vroeg. Een pagina die stille
veroudering toont als gegeven is precies het soort halve waarheid dat de rest van
dit project probeert te vermijden.

Dus: wél een schema, en de zendtijd blijft de begrenzing in plaats van te
verdwijnen. Drie grenzen, en ze stapelen:

1. **Per node een interval, standaard uit.** Wie een node toevoegt krijgt geen
   terugkerende kosten cadeau.
2. **Eén ronde tegelijk, met een minimumafstand ertussen.** Niet tien timers die
   toevallig samenvallen: één wachtrij, en de meest achterstallige node wint. Tien
   nodes op hetzelfde uur zou de band een uur bezet houden.
3. **Een bovengrens per etmaal over álle nodes.** Dat vangt het geval dat geen
   van de eerste twee vangt: iemand die twintig nodes op dagelijks zet zonder de
   optelsom te maken. Het interval per node is dan een wens, en dit is wat er
   werkelijk gebeurt.

Wat hier NIET gebeurt is de sweep zelf. Die draait in de firmware van de monitor,
met zijn eigen grenzen (MON_SET_MIN_GAP_MS tussen twee rondes, een harde cap per
ronde). Deze planner besluit alleen wanneer er gevraagd wordt, en houdt zich aan
diezelfde afspraken in plaats van eromheen te werken -- dezelfde regel als bij de
handmatige kloksyncknop, die ook door ``publish_command`` loopt en niet zijn eigen
weg naar de broker heeft.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from datetime import datetime, timezone

from . import commanding, config, db, monitors, mqtt_ingest, nodeconfig

log = logging.getLogger(__name__)

ENABLED = config.env("SWEEP_ENABLED", "1") not in ("0", "false", "no", "")

# Minimumafstand tussen twee rondes, welke node het ook betreft. Standaard gelijk
# aan MON_SET_MIN_GAP_MS in de firmware (600 s): korter heeft geen zin, want dan
# weigert de monitor toch. Iets ruimer is verdedigbaar en iets krapper niet, dus
# de ondergrens hieronder is hard.
MIN_GAP_MIN = max(10, int(config.env("SWEEP_MIN_GAP_MIN", "15") or 15))

# Hoeveel rondes er per etmaal in totaal mogen starten. Bij de standaardafstand
# van 15 minuten passen er 96 in een dag; 48 laat de helft van de band vrij voor
# alles wat er verder gebeurt en is ruim genoeg voor twintig nodes op dagelijks.
MAX_PER_DAY = max(1, int(config.env("SWEEP_MAX_PER_DAY", "48") or 48))

# Hoe vaak de planner kijkt of er iets moet. Kort genoeg om de minimumafstand
# nauwkeurig aan te houden, lang genoeg om niets te kosten.
TICK_S = 60

# Wachten na het opstarten, om dezelfde reden als bij de kloksynchronisatie: de
# brokerverbinding moet er zijn en de nodes moeten zich gemeld hebben, anders
# strandt de eerste ronde altijd op "geen verbinding" en staat dat in het logboek
# als iets wat het niet is.
FIRST_RUN_DELAY_S = 300

_LEDGER_KEY = "sweep_ledger"
_LEDGER_MAX = 200

_lock = threading.Lock()
_thread = None
_state = {"last_result": "nog niet gedraaid", "last_run": None}


# --- het grootboek ------------------------------------------------------------
#
# In de databank en niet in het geheugen: een schema dat een herstart niet
# overleeft is geen schema maar een gewoonte van dit proces. Bij een herstart
# tijdens een stroomstoring is dat precies het moment waarop je hem wél wilt.

def _ledger() -> dict:
    try:
        return json.loads(db.get_setting(_LEDGER_KEY, "{}")) or {}
    except ValueError:
        return {}


def _save(data: dict) -> None:
    if len(data) > _LEDGER_MAX:
        data = dict(sorted(data.items(), key=lambda kv: kv[1].get("asked", ""))[-_LEDGER_MAX:])
    db.set_setting(_LEDGER_KEY, json.dumps(data))


# Hoe lang een gevraagde ronde de tijd krijgt voordat de stilte als uitkomst
# geldt. Een sweep is nominaal 286 s en de node mag hem uitstellen als er net een
# andere liep, dus tien minuten is ruim -- en korter dan de minimumafstand, zodat
# het oordeel altijd valt vóór de volgende ronde vertrekt.
VERIFY_AFTER_S = 600

RESULT_ASKED = "gevraagd"
RESULT_ANSWERED = "gevraagd, antwoord binnen"
RESULT_SILENT = "gevraagd, geen antwoord"
RESULT_EXHAUSTED = "alle monitors geprobeerd, geen antwoord"
RESULT_REFUSED = "monitor kan het niet"


def record(prefix: str, when: float, result: str, seen: str | None = None,
           cursor: int = 0) -> None:
    """Eén regel in het grootboek.

    ``seen`` is de jongste tijdstempel die we van deze node hadden op het moment
    van vragen. Dat is de meetlat voor later: is er na de verificatietermijn geen
    verser tijdstempel, dan heeft de ronde niets opgeleverd. Zonder die
    nulmeting is 'geslaagd' niet vast te stellen -- alleen 'er staat iets', en dat
    stond er gisteren ook.
    """
    with _lock:
        data = _ledger()
        data[prefix.lower()] = {"asked": datetime.fromtimestamp(when, timezone.utc)
                                .isoformat(timespec="seconds"),
                                "at": when, "result": result, "seen_before": seen,
                                "cursor": int(cursor or 0)}
        _save(data)


def _newest_value_ts(rep_id: int) -> str | None:
    """De jongste ``updated`` van de uitgelezen parameters van deze node."""
    rij = db.qone("SELECT MAX(updated) AS m FROM repeater_cli WHERE repeater_id=?",
                  (rep_id,))
    return (rij["m"] if rij else None) or None


def verify_pending(now: float | None = None) -> list:
    """Beoordeel gevraagde rondes waarvan de termijn verstreken is.

    Dit bestaat omdat 'gevraagd' te optimistisch was. Publiceren lukt zodra de
    broker de bytes aanneemt, en dat zegt niets over of de monitor de ronde
    gelopen heeft: hij kan hem weigeren omdat er net een andere liep, hij kan
    slapen op zijn zonnebudget, of het onderwerp staat niet in zijn monitorlijst.
    In alle drie de gevallen zag het grootboek er identiek uit aan een geslaagde
    ronde -- en dat is de reden dat twaalf uur stilte onopgemerkt bleef.

    Nu is er een derde uitkomst. Dezelfde vier die de LoRa-schrijfweg al
    onderscheidt, teruggebracht tot wat hier te meten valt: verstuurd of niet,
    en daarna antwoord of stilte.
    """
    now = time.time() if now is None else now
    veranderd = []
    for prefix, regel in _ledger().items():
        if regel.get("result") != RESULT_ASKED:
            continue
        if now - float(regel.get("at") or 0) < VERIFY_AFTER_S:
            continue
        rij = db.find_repeater(prefix)
        if rij is None:
            continue
        nu_gezien = _newest_value_ts(rij["id"])
        eerder = regel.get("seen_before")
        beter = bool(nu_gezien) and (not eerder or nu_gezien > eerder)
        if beter:
            # Gelukt: de cursor terug naar de eerste kandidaat. Anders zou de
            # volgende ronde bij nummer twee beginnen terwijl nummer één werkt.
            record(prefix, float(regel["at"]), RESULT_ANSWERED, seen=eerder, cursor=0)
            veranderd.append((prefix, True))
            continue

        uitkomst, volgende = _na_stilte(rij, int(regel.get("cursor") or 0))
        record(prefix, float(regel["at"]), uitkomst, seen=eerder, cursor=volgende)
        veranderd.append((prefix, False))
        log.warning("Uitvraagronde voor %s leverde niets op: %s", rij["slug"], uitkomst)
    return veranderd


def _na_stilte(rep, cursor: int) -> tuple:
    """Wat betekent stilte van kandidaat ``cursor``, en wat is de volgende stap?

    Hier zit de hele terugvalredenering, en die is met opzet niet "probeer de
    volgende". Twee dingen zien er van hieraf identiek uit en verdienen een
    verschillende reactie:

    - **De monitor zweeg.** Hij sliep op zijn zonnebudget, zijn wifi was weg, of
      hij weigerde omdat er net een andere ronde liep. Dat is tijdelijk en zegt
      niets over de relatie met het doelwit, dus dan is de volgende kandidaat
      precies waar hij voor bestaat.
    - **De monitor kan het niet.** Het doelwit staat niet in zijn monitorlijst, of
      hij is er alleen als lezer binnen. Dat is blijvend, het was bij de eerste
      poging al vast te stellen, en terugvallen zou het verbergen: de volgende
      ronde loopt dan tegen precies dezelfde muur, alleen bij een andere node, en
      niemand ziet ooit dat de eerste kandidaat verkeerd ingesteld staat.

    Dus: terugvallen na stilte, niet na een inhoudelijke weigering. Wat de monitor
    kan is te lezen uit zijn eigen monitorlijst -- zie ``nodeconfig.rights_for``,
    dat login, pogingen en antwoorden combineert. Is die niet te lezen (geen
    beheeradres), dan telt het als stilte: onbekend is geen weigering, en de
    kandidaat die daarna komt kost één sweep om uit te sluiten.

    Terugvallen gebeurt over RONDEN en niet binnen één ronde, en dat is de rem.
    Een sweep is nominaal 286 s; drie kandidaten achter elkaar aflopen is een
    kwartier band voor één node z'n instellingen, en die veranderen ongeveer nooit.
    Door de cursor te verzetten en de node meteen weer opeisbaar te maken, pakt de
    wachtrij hem op na de gewone minimumafstand -- één sweep per tussenruimte,
    zoals altijd, alleen met een andere afzender.
    """
    lijst = monitors.candidates(rep)["monitors"]
    huidige = lijst[cursor] if cursor < len(lijst) else None

    if huidige is not None:
        oordeel = monitors.rights_note(rep, huidige)
        if oordeel["known"] and oordeel["info"]["diagnosis"] in ("alleen_lezen",
                                                                "niet_gemonitord"):
            # Blijvend, en al bekend. Niet terugvallen: de cursor blijft staan,
            # zodat de melding over déze kandidaat blijft gaan.
            return RESULT_REFUSED, cursor

    volgende = cursor + 1
    if volgende >= len(lijst):
        # Lijst op. Terug naar het begin, maar pas bij het volgende interval:
        # due_at() wacht alleen niet bij RESULT_SILENT, en dit is het niet.
        #
        # Bij één kandidaat luidt de uitkomst gewoon 'geen antwoord'. "Alle
        # monitors geprobeerd" is waar maar misleidend als er nooit meer dan één
        # was: het suggereert een lijst die is afgelopen, en dan gaat iemand
        # zoeken naar de tweede kandidaat die er niet is.
        return (RESULT_SILENT if len(lijst) <= 1 else RESULT_EXHAUSTED), 0
    return RESULT_SILENT, volgende


def entry(prefix: str) -> dict:
    return _ledger().get((prefix or "").lower(), {})


# --- wanneer is een node aan de beurt -----------------------------------------

def interval_minutes(rep) -> int:
    """Het ingestelde interval in MINUTEN, of 0 voor uit.

    Dit is de enige plek die de twee kolommen kent. ``sweep_minutes`` wint;
    staat die leeg, dan geldt het oude ``sweep_hours`` maal zestig. Zo blijft een
    bestaande installatie draaien zoals hij stond, zonder migratiescript en
    zonder dat een node die op twaalf uur staat plots op twaalf minuten gaat.

    Wat hier NIET gebeurt is de wens begrenzen. Een interval van vijf minuten
    mag ingesteld worden en betekent "zo vaak als mag": de wachtrij houdt
    MIN_GAP_MIN aan en MAX_PER_DAY is het dak over alle nodes samen. Die twee
    grenzen hier nog eens overdoen zou de bovengrens op twee plaatsen zetten, en
    dan is het een kwestie van tijd voor ze verschillen.
    """
    for veld, factor in (("sweep_minutes", 1), ("sweep_hours", 60)):
        try:
            waarde = int(rep[veld] or 0)
        except (KeyError, IndexError, TypeError, ValueError):
            continue
        if waarde > 0:
            return waarde * factor
    return 0


def interval_hours(rep) -> int:
    """Het interval in hele uren, afgerond naar boven. 0 blijft 0.

    Blijft bestaan omdat de beheerpagina en oudere aanroepers in uren denken.
    Naar BOVEN afronden zodat een interval van tien minuten niet als "0 uur"
    leest -- dat zou als 'uit' overkomen terwijl er een schema staat.
    """
    minuten = interval_minutes(rep)
    if not minuten:
        return 0
    return max(1, -(-minuten // 60))


def due_at(rep, now: float | None = None) -> float | None:
    """Wanneer deze node aan de beurt is, of None als er geen schema staat.

    Een node die nog nooit uitgevraagd is, is meteen aan de beurt. Dat is met
    opzet: het alternatief -- een vol interval wachten na het instellen -- laat
    iemand die net een schema aanzette een dag in het ongewisse over of het werkt.
    """
    minuten = interval_minutes(rep)
    if not minuten:
        return None
    regel = entry(rep["pubkey_prefix"])
    laatst = regel.get("at")
    if not laatst:
        return now if now is not None else time.time()
    # Meteen weer opeisbaar, maar alleen als er werkelijk een ONGEPROBEERDE
    # kandidaat klaarstaat. Dat is precies wat een cursor boven nul betekent: de
    # vorige zweeg en de volgende heeft zijn kans nog niet gehad. Die verdient
    # hem zonder een heel interval te wachten, en het kost geen extra zendtijd --
    # de wachtrij houdt de minimumafstand aan, er staat alleen een andere
    # afzender in het volgende venster.
    #
    # Staat de cursor weer op nul, dan is de lijst rond (of was er maar één) en
    # wacht deze node zijn interval uit. Zonder die voorwaarde zou een node met
    # één monitor die niet antwoordt elke minimumafstand opnieuw bevraagd worden
    # in plaats van elke twaalf uur -- van een schema een sirene maken, op een
    # band die van iedereen is.
    if regel.get("result") == RESULT_SILENT and int(regel.get("cursor") or 0) > 0:
        return now if now is not None else time.time()
    return float(laatst) + minuten * 60


def next_due_secs(rep, now: float | None = None) -> int | None:
    now = time.time() if now is None else now
    wanneer = due_at(rep, now)
    if wanneer is None:
        return None
    return max(0, int(wanneer - now))


def _sweeps_last_day(now: float) -> int:
    return sum(1 for v in _ledger().values()
               if float(v.get("at") or 0) > now - 86400)


def _last_any(now: float) -> float:
    waarden = [float(v.get("at") or 0) for v in _ledger().values()]
    return max(waarden) if waarden else 0.0


# --- één ronde ----------------------------------------------------------------

def run_once(now: float | None = None) -> dict:
    """Kijk of er een node aan de beurt is, en vraag er hoogstens één uit.

    Hoogstens één, en dat is de kern. Twintig nodes die tegelijk aan de beurt
    zijn leveren hier twintig rondes ná elkaar op met de minimumafstand ertussen,
    niet twintig tegelijk -- en als dat langer duurt dan hun interval, dan is dat
    het eerlijke antwoord op te veel schema voor te weinig band.
    """
    now = time.time() if now is None else now
    uit = {"gestart": None, "reden": "", "wachtend": 0}

    # Eerst de oogst van eerdere rondes beoordelen, en dan pas een nieuwe
    # sturen. Andersom zou een node die nooit antwoordt elke ronde opnieuw als
    # 'gevraagd' in het grootboek belanden en zou de stilte nooit opvallen.
    verify_pending(now)

    if not ENABLED:
        uit["reden"] = "uitgeschakeld"
        return uit

    verstreken = now - _last_any(now)
    if verstreken < MIN_GAP_MIN * 60:
        uit["reden"] = "minimumafstand"
        return uit

    if _sweeps_last_day(now) >= MAX_PER_DAY:
        # Bewust geen fout: dit is de bovengrens die doet wat hij moet doen. Wel
        # zichtbaar, want een schema dat structureel tegen deze grens aanloopt is
        # een schema dat niet waarmaakt wat het belooft.
        uit["reden"] = "dagbudget op"
        return uit

    kandidaten = []
    for rep in db.q("SELECT * FROM repeaters ORDER BY sort_order, name"):
        wanneer = due_at(rep, now)
        if wanneer is None or wanneer > now:
            continue
        kandidaten.append((wanneer, rep))
    uit["wachtend"] = len(kandidaten)
    if not kandidaten:
        uit["reden"] = "niemand aan de beurt"
        return uit

    # De meest achterstallige eerst. Bij gelijke stand wint de node die het
    # langst niet aan bod kwam, wat vanzelf gebeurt omdat 'wanneer' dan lager is.
    kandidaten.sort(key=lambda kv: kv[0])
    _, rep = kandidaten[0]

    broker = mqtt_ingest.can_publish()
    if not broker:
        uit["reden"] = "geen brokerverbinding"
        return uit

    # Wie de vraag stelt komt uit de ingestelde lijst, op de plek waar de cursor
    # staat. Staat er niets ingesteld, dan valt candidates() terug op de
    # waarneming -- wie de cijfers feitelijk doorstuurt -- wat precies is wat er
    # vóór deze tabellen gebeurde.
    cursor = int(entry(rep["pubkey_prefix"]).get("cursor") or 0)
    lijst = monitors.candidates(rep)["monitors"]
    if not lijst:
        record(rep["pubkey_prefix"], now, "geen monitor bekend")
        uit["reden"] = "geen monitor"
        return uit
    if cursor >= len(lijst):
        cursor = 0
    afzender = lijst[cursor]

    zelf = int(afzender["id"]) == int(rep["id"])
    route = commanding.describe(afzender, broker_connected=broker)
    if not route["mqtt"]:
        # De AFZENDER is niet aanspreekbaar, niet het doelwit. Opschrijven zodat
        # deze node niet elke minuut opnieuw de meest achterstallige is -- en de
        # cursor verzetten, want een volgende kandidaat is precies waarvoor de
        # lijst bestaat.
        volgende = cursor + 1
        record(rep["pubkey_prefix"], now,
               f"{afzender['name']} niet aanspreekbaar ({route['blocker'] or 'onbekend'})",
               cursor=volgende if volgende < len(lijst) else 0)
        uit["reden"] = "afzender niet aanspreekbaar"
        return uit

    # Dezelfde aanroep als achter de knop op de nodepagina. Een eigen weg naar de
    # broker zou een achterdeur zijn om de controles heen die daar staan. Het
    # onderwerp gaat mee tenzij de afzender het doelwit zelf is: dan leest hij
    # zijn eigen CLI en zou een onderwerp hem naar iemand anders laten kijken.
    ok = mqtt_ingest.publish_command(
        afzender["pubkey_prefix"], "settings",
        subject=None if zelf else rep["pubkey_prefix"])
    # De nulmeting mee, zodat verify_pending() straks kan zien of er iets
    # verscher is geworden in plaats van alleen dat er iets staat.
    record(rep["pubkey_prefix"], now,
           RESULT_ASKED if ok else "versturen mislukt",
           seen=_newest_value_ts(rep["id"]), cursor=cursor)
    uit["gestart"] = rep["pubkey_prefix"]
    uit["via"] = afzender["pubkey_prefix"]
    uit["reden"] = RESULT_ASKED if ok else "versturen mislukt"
    log.info("Uitvraagronde gepland voor %s via %s (kandidaat %d): %s",
             rep["slug"], afzender["name"], cursor + 1, uit["reden"])
    return uit


def status() -> dict:
    return {
        "enabled": ENABLED,
        "min_gap_min": MIN_GAP_MIN,
        "max_per_day": MAX_PER_DAY,
        "today": _sweeps_last_day(time.time()),
        "last_result": _state["last_result"],
        "last_run": _state["last_run"],
    }


def _run() -> None:
    time.sleep(FIRST_RUN_DELAY_S)
    while True:
        try:
            uit = run_once()
            _state["last_result"] = uit["reden"]
            _state["last_run"] = time.time()
        except Exception as err:                      # noqa: BLE001
            log.exception("Uitvraagplanner struikelde: %s", err)
            _state["last_result"] = f"onverwachte fout: {err}"
        time.sleep(TICK_S)


def start() -> None:
    global _thread
    if not ENABLED:
        log.info("Uitvraagplanner staat uit (MM_SWEEP_ENABLED)")
        _state["last_result"] = "uitgeschakeld"
        return
    if _thread is not None:
        return
    _thread = threading.Thread(target=_run, name="sweepsched", daemon=True)
    _thread.start()
