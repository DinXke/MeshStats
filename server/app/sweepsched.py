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

from . import commanding, config, db, mqtt_ingest

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


def record(prefix: str, when: float, result: str, seen: str | None = None) -> None:
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
                                "at": when, "result": result, "seen_before": seen}
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
        record(prefix, float(regel["at"]),
               RESULT_ANSWERED if beter else RESULT_SILENT, seen=eerder)
        veranderd.append((prefix, beter))
        if not beter:
            log.warning("Uitvraagronde voor %s leverde niets op: geen versere "
                        "waarden na %d s", rij["slug"], VERIFY_AFTER_S)
    return veranderd


def entry(prefix: str) -> dict:
    return _ledger().get((prefix or "").lower(), {})


# --- wanneer is een node aan de beurt -----------------------------------------

def interval_hours(rep) -> int:
    """Het ingestelde interval, of 0 voor uit. Nooit korter dan de minimumafstand."""
    try:
        uren = int(rep["sweep_hours"] or 0)
    except (KeyError, IndexError, TypeError, ValueError):
        uren = 0
    return max(0, uren)


def due_at(rep, now: float | None = None) -> float | None:
    """Wanneer deze node aan de beurt is, of None als er geen schema staat.

    Een node die nog nooit uitgevraagd is, is meteen aan de beurt. Dat is met
    opzet: het alternatief -- een vol interval wachten na het instellen -- laat
    iemand die net een schema aanzette een dag in het ongewisse over of het werkt.
    """
    uren = interval_hours(rep)
    if not uren:
        return None
    laatst = entry(rep["pubkey_prefix"]).get("at")
    if not laatst:
        return now if now is not None else time.time()
    return float(laatst) + uren * 3600


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
    route = commanding.describe(rep, broker_connected=broker)
    if not route["mqtt"] or "settings" not in route["commands"]:
        # Geen weg naar deze node. Wél opschrijven, anders blijft hij elke minuut
        # opnieuw de meest achterstallige en komt niemand anders meer aan de
        # beurt -- en zou de pagina blijven beloven dat er iets staat te gebeuren.
        record(rep["pubkey_prefix"], now, f"geen weg ({route['blocker'] or 'onbekend'})")
        uit["reden"] = "geen weg"
        return uit

    # Dezelfde aanroep als achter de knop op de nodepagina, met dezelfde
    # onderwerpsleutel als het langs een monitor gaat. Een eigen weg naar de
    # broker zou een achterdeur zijn om de controles heen die daar staan.
    ok = mqtt_ingest.publish_command(
        route["node"], "settings",
        subject=route["subject"] if route["via_monitor"] else None)
    # De nulmeting mee, zodat verify_pending() straks kan zien of er iets
    # verscher is geworden in plaats van alleen dat er iets staat.
    record(rep["pubkey_prefix"], now,
           RESULT_ASKED if ok else "versturen mislukt",
           seen=_newest_value_ts(rep["id"]))
    uit["gestart"] = rep["pubkey_prefix"]
    uit["reden"] = RESULT_ASKED if ok else "versturen mislukt"
    log.info("Uitvraagronde gepland voor %s: %s", rep["slug"], uit["reden"])
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
