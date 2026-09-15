"""Accubewaking: een alarm vóór de node stil valt, niet erna.

WAAROM DIT ER IS. Op 14 september 2026 trok de zonnerepeater BE-HSS-DinX-Home
zijn accu leeg: 3,545 V op de 13e, 3,406 V rond het middaguur van de 14e, 2,812 V
om 23:22, en daarna niets meer. Hij nam de statistieken van drie andere nodes met
zich mee, want hij was ook hun koerier. In de alerts-tabel stond er over die hele
afloop **geen enkele regel** -- de enige bewaking die bestond was de
stiltebewaking van de pushende node, en die gaat over een ander toestel.

De cijfers waren er wel. Ze werden alleen door niemand gelezen.

WAT DEZE LUS DOET, en met opzet niet meer: hij kijkt periodiek naar de laatste
gemeten accuspanning van elke node en schrijft een regel in ``alerts`` als die
onder een grens zakt. Wie dat alarm verder bezorgt is niet zijn zaak -- webpush
pollt diezelfde tabel, en dat is precies waarom die tabel het koppelvlak is.

DE SPANNING EN NIET HET PERCENTAGE. Het percentage is afgeleid (zie
``metrics.battery_percentage``) en een afgeleide drempel op een afgeleid getal
is twee keer raden. De spanning is wat de node werkelijk meet.

VIER REGELS DIE RUIS VOORKOMEN
------------------------------
1. **Alleen een VERSE meting.** Een node die niet meer meldt heeft geen lage
   accu -- hij heeft geen meting. Over een waarde van gisteren valt vandaag niets
   te beweren, en er is niets zo snel genegeerd als een alarm dat elke ronde
   opnieuw over hetzelfde oude getal gaat. (Dat een node stilvalt is óók het
   melden waard, maar dat is een andere bewaking en een ander alarm.)
2. **Alleen een BRUIKBARE meting.** Onder de twee volt meet een bord geen cel
   meer: geen accu, of een kapotte deler. Dezelfde ondergrens als de curve in
   ``metrics``, want het is dezelfde vraag.
3. **Alleen bij een OVERGANG.** Zakken is de gebeurtenis. Zolang hij laag blijft
   komt er niets bij; pas als hij een trede verder zakt (laag -> kritiek) is er
   weer nieuws. De toestand staat in ``settings``, dus een herstart van de site
   maakt er geen nieuwe gebeurtenis van.
4. **Terug omhoog met een marge.** Een cel die rond de grens dobbert -- en dat
   doet een zonnecel bij elke wolk -- zou anders om het uur een alarm en een
   herstelmelding geven. Pas ``BATT_HYST_V`` boven de grens heet het hersteld.

De grenzen staan in ``settings`` en zijn per installatie te zetten; de
standaardwaarden hieronder zijn die van een enkele LiPo-cel, wat elke node in
deze vloot heeft.
"""
from __future__ import annotations

import json
import logging
import threading
import time
from datetime import datetime, timedelta, timezone

from . import db

log = logging.getLogger(__name__)

# De grenzen, in volt. Een LiPo van 3,5 V heeft nog ruim een dag te gaan op een
# repeater die enkele milliampères trekt -- tijd genoeg om een paneel schoon te
# vegen of de node te halen. Onder 3,3 V is het einde in uren te tellen; dan is
# het geen waarschuwing meer maar een laatste kans.
BATT_WARN_V_DEFAULT = 3.50
BATT_CRIT_V_DEFAULT = 3.30
# Zoveel boven de grens heet het hersteld. Een zonnecel wipt bij elke wolk over
# een kale drempel heen en terug.
BATT_HYST_V = 0.08
# Onder deze spanning meet het bord geen bruikbare cel meer (zelfde ondergrens
# als metrics.battery_percentage).
BATT_MIN_USABLE_V = 2.0
# Ouder dan dit en de meting zegt niets meer over nu.
BATT_STALE_H = 6

INTERVAL_MIN = 5
# Niet meteen bij het opstarten: de eerste pollerronde mag eerst binnen zijn,
# anders wordt er geoordeeld over metingen van vóór de herstart.
FIRST_RUN_DELAY_S = 120

# Waar de onthouden trede per node staat. In settings en niet in het geheugen:
# een herstart van de site is geen gebeurtenis aan de accu.
STATE_KEY = "battwatch_state"

NIVEAU_OK = "ok"
NIVEAU_LAAG = "laag"
NIVEAU_KRITIEK = "kritiek"

_state = {"last_run": None, "checked": 0, "alerts": 0, "last_error": ""}


def grenzen() -> tuple[float, float]:
    """De twee drempels, met de ingestelde waarden als die er zijn."""
    def _f(sleutel: str, standaard: float) -> float:
        ruw = db.get_setting(sleutel, None)
        if ruw is None:
            return standaard
        try:
            v = float(str(ruw).replace(",", "."))
        except (TypeError, ValueError):
            return standaard
        return v if 0.5 <= v <= 30.0 else standaard

    warn = _f("batt_warn_v", BATT_WARN_V_DEFAULT)
    crit = _f("batt_crit_v", BATT_CRIT_V_DEFAULT)
    # Een kritieke grens boven de waarschuwingsgrens zou betekenen dat een node
    # eerst kritiek wordt en daarna pas laag. Dat is geen instelling maar een
    # tikfout, en hem stil omdraaien is beter dan er twee alarmen van maken.
    if crit > warn:
        warn, crit = crit, warn
    return warn, crit


def _niveau(volt: float, vorig: str, warn: float, crit: float) -> str:
    """De trede waar deze spanning bij hoort, met de marge omhoog.

    ``vorig`` doet er alleen toe bij het STIJGEN: omlaag geldt de kale grens
    (een storing hoort meteen te melden), omhoog moet hij de marge halen.
    """
    if volt <= crit:
        return NIVEAU_KRITIEK
    if volt <= warn:
        # Van kritiek komend is dit alleen een verbetering als hij de marge haalt;
        # anders blijft hij kritiek heten en komt er dus geen herstelregel.
        if vorig == NIVEAU_KRITIEK and volt < crit + BATT_HYST_V:
            return NIVEAU_KRITIEK
        return NIVEAU_LAAG
    if vorig in (NIVEAU_LAAG, NIVEAU_KRITIEK) and volt < warn + BATT_HYST_V:
        return vorig
    return NIVEAU_OK


def _lees_state() -> dict:
    try:
        d = json.loads(db.get_setting(STATE_KEY, "{}") or "{}")
    except (ValueError, TypeError):
        return {}
    return d if isinstance(d, dict) else {}


def _schrijf_state(d: dict) -> None:
    db.set_setting(STATE_KEY, json.dumps(d))


def _vers(ts: str | None, nu: datetime) -> bool:
    if not ts:
        return False
    try:
        t = datetime.strptime(str(ts), "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError:
        return False
    return (nu - t) <= timedelta(hours=BATT_STALE_H)


def run_once() -> int:
    """Eén ronde. Geeft het aantal geschreven alarmen terug."""
    nu = datetime.now(timezone.utc)
    warn, crit = grenzen()
    state = _lees_state()
    geschreven = 0
    gekeken = 0

    for rep in db.q("SELECT id, name, slug FROM repeaters ORDER BY id"):
        rid = int(rep["id"])
        rij = db.qone("SELECT ts, value FROM latest WHERE repeater_id=? AND metric='bat'",
                      (rid,))
        if rij is None or rij["value"] is None:
            continue
        volt = float(rij["value"])
        if volt < BATT_MIN_USABLE_V:
            continue                      # geen bruikbare cel; zie de kop
        if not _vers(rij["ts"], nu):
            continue                      # geen verse meting; zie de kop
        gekeken += 1

        vorig = str(state.get(str(rid), NIVEAU_OK))
        nieuw = _niveau(volt, vorig, warn, crit)
        if nieuw == vorig:
            continue

        naam = rep["name"] or rep["slug"] or ("node %d" % rid)
        if nieuw == NIVEAU_KRITIEK:
            tekst = ("accu KRITIEK: %.2f V (grens %.2f V) -- deze node valt "
                     "binnenkort stil" % (volt, crit))
            ernst = "hoog"
        elif nieuw == NIVEAU_LAAG:
            tekst = "accu laag: %.2f V (grens %.2f V)" % (volt, warn)
            ernst = "hoog"
        else:
            tekst = "accu weer op peil: %.2f V" % volt
            ernst = "laag"

        # BEWUST ZONDER ``kind``. Die is er voor het geval dat dezelfde
        # gebeurtenis langs twee wegen binnenkomt (mesh en IP), en dan wordt er
        # ontdubbeld op node + soort + het eerste woord van de tekst. Deze
        # alarmen hebben maar EEN producent -- deze lus -- dus er valt niets te
        # beschermen, en omdat ze allemaal met "accu" beginnen zou die regel
        # juist de escalatie van laag naar KRITIEK opeten: precies de melding
        # waar het om gaat. Herhaling wordt hier al voorkomen door de trede zelf.
        if db.add_alert(rid, tekst, source="accu", severity=ernst):
            geschreven += 1
            log.warning("Accubewaking %s: %s -> %s (%.2f V)", naam, vorig, nieuw, volt)
        # De trede onthouden we OOK als add_alert ontdubbelde: de gebeurtenis is
        # dan al gemeld, en hem opnieuw willen melden zou de volgende ronde weer
        # dezelfde regel proberen.
        state[str(rid)] = nieuw

    _schrijf_state(state)
    _state.update(last_run=db.utcnow(), checked=gekeken, alerts=geschreven, last_error="")
    return geschreven


def status() -> dict:
    """Voor de beheerpagina: wat deed de lus het laatst, en met welke grenzen."""
    warn, crit = grenzen()
    return dict(_state, warn_v=warn, crit_v=crit, stale_h=BATT_STALE_H,
                interval_min=INTERVAL_MIN)


def _run() -> None:
    time.sleep(FIRST_RUN_DELAY_S)
    while True:
        try:
            run_once()
        except Exception as err:  # noqa: BLE001 - een ronde mag de thread niet doden
            log.exception("Accubewaking mislukte onverwacht: %s", err)
            _state["last_error"] = str(err)
        time.sleep(INTERVAL_MIN * 60)


_thread = None


def start() -> None:
    """Start de accubewaking. Idempotent, zoals de andere planners in deze app."""
    global _thread
    if _thread is not None:
        return
    _thread = threading.Thread(target=_run, name="battwatch", daemon=True)
    _thread.start()
