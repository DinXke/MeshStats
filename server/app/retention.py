"""Opruimen: hoeveel bewaren we, en wat garandeert dat de schijf niet volloopt?

Waarom dit een eigen module is
------------------------------
Het snoeien zelf staat in ``db.prune()``, waar de verbinding en het slot wonen.
Wat hier staat is alles eromheen: wanneer het gebeurt, wat het opleverde, en hoe
de beheerpagina daar een eerlijk verhaal van maakt. Dezelfde scheiding die
``clocksync`` aanhoudt, en om dezelfde reden -- een module die de opslag bezit
hoort geen planner te zijn.

Waarom periodiek en niet alleen bij het opstarten
-------------------------------------------------
Tot nu toe werd er precies twee keer gesnoeid: bij het opstarten van de
container en bij het opslaan van de instellingen. Voor een site die om de paar
dagen opnieuw uitgerold wordt is dat toevallig genoeg. Voor een server die
maanden aan één stuk draait -- en dat is precies wat deze doet zodra hij af is
-- betekent het dat er na de eerste minuut nooit meer iets weggaat. De
bewaartermijn is dan geen termijn maar een opstartritueel, en de eerste keer dat
iemand het merkt is als de schijf vol is.

Een uur tussen twee rondes is ruim bemeten. Bij de gemeten instroom van ongeveer
3 738 pakketten per dag komen er per ronde zo'n 156 rijen bij; de FIFO-grens kan
er dus hooguit een uur overheen zitten, en dat is bij 200 000 rijen minder dan
een tiende procent. Vaker draaien zou dezelfde drie indexopzoekingen vaker doen
zonder dat er iets aan verandert.

De drie grenzen
---------------
Uitgelegd bij ``db.prune()``, want daar staat de volgorde die ze uitvoert. Kort:
de termijn is wat we willen, het rij- en het bytemaximum zijn wat we beloven, en
als die botsen gaat de oudste eruit. Wie het eerst binnenkwam gaat het eerst
weg.

Wat dat betekent voor de beheerpagina staat hieronder in ``overview()``, en het
is de helft van de feature: op het ogenblik dat een van de twee bovengrenzen
snijdt, is de ingestelde termijn niet gehaald. Iemand die 30 dagen instelde
kijkt dan naar 12, en dat hoort op het scherm te staan en niet in een logboek.

Over ``samples``
----------------
Die tabel is de grootste van de databank in rijen (214 709 tegen 7 477
pakketten op de referentieserver) en dat is geen groeiprobleem meer, maar een
erfenis. Sinds de metingen naar VictoriaMetrics gaan schrijft ``db.ingest()``
niets meer in ``samples``: die tak wordt overgeslagen zodra ``tsdb.enabled()``
waar is. Wat er nog wél in komt is de uitwijk (``db.spill_samples``) wanneer de
tijdreeksdatabank een batch weigert of wegvalt -- een vangnet dat per definitie
alleen vult als er iets stuk is.

De tabel valt onder dezelfde opruiming, met de lange bewaartermijn
(``retention_days``, standaard 180 dagen). Ze slinkt dus vanzelf: de bestaande
rijen zijn ouder dan de overstap en verdwijnen naarmate die 180 dagen verstrijken,
en er komt niets structureels voor terug. Een eigen FIFO-grens heeft ze niet
gekregen, met opzet: metingen zijn het product van deze site en pakketten zijn
werkmateriaal. Als de byte-bovengrens niet gehaald wordt terwijl de pakketten al
op hun ondergrens staan, dan zegt de beheerpagina dat -- liever een luide
waarschuwing dan stilletjes de historiek weggooien waar iedereen naar kijkt.
"""
import json
import logging
import threading
import time

from . import config, db

log = logging.getLogger("meshstats.retention")

# Minuten tussen twee rondes. Zie hierboven waarom een uur ruim is.
INTERVAL_MIN = max(1, int(config.PRUNE_MINUTES))

# Hoe lang na het opstarten de eerste periodieke ronde volgt. Het opstarten zelf
# snoeit al één keer (main.bootstrap), dus deze wachttijd hoeft niets in te
# halen; hij houdt alleen de eerste minuten na een herstart vrij, wanneer de
# MQTT-verbinding en de tsdb-schrijver hun plek nog aan het vinden zijn.
FIRST_RUN_DELAY_S = 600

# Sleutel waaronder de laatste ronde bewaard blijft. In de instellingentabel en
# niet alleen in het geheugen: na een herstart is "wanneer is er voor het laatst
# gesnoeid, en hoeveel ging er weg" nog steeds de vraag die de beheerpagina moet
# kunnen beantwoorden, en een herstart is juist het moment waarop iemand kijkt.
_LAST_KEY = "prune_last"

_state = {
    "interval_min": INTERVAL_MIN,
    "runs": 0,
    "last_run": None,        # ISO-tijdstip van de laatste ronde
    "last_error": "",
}


def last_report() -> dict | None:
    """De laatste ronde zoals ze bewaard is, of None als er nog nooit een was."""
    try:
        data = json.loads(db.get_setting(_LAST_KEY) or "null")
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


def _store(report: dict) -> None:
    try:
        db.set_setting(_LAST_KEY, json.dumps(report))
    except Exception as err:  # noqa: BLE001 - een volle schijf mag het snoeien niet ongedaan maken
        log.debug("snoeirapport niet bewaard: %s", err)


def run_once() -> dict:
    """Eén ronde: snoeien, en dan pas beslissen of het bestand herschreven wordt.

    De volgorde is niet vrijblijvend. VACUUM geeft alleen ruimte terug die al
    vrijgekomen is, dus ervoor draaien zou een dure herschrijving zijn van
    precies de rijen die er een seconde later uit gaan.
    """
    report = db.prune()
    report["vacuum"] = db.maybe_vacuum()
    _state["runs"] += 1
    _state["last_run"] = report["at"]
    _state["last_error"] = ""
    _store(report)

    gone = report["packets_age"] + report["packets_rows"] + report["packets_bytes"]
    if report["limit_hit"]:
        # WARNING, want dit is het geval waarin de ingestelde bewaartermijn niet
        # gehaald wordt. Stil doorgaan zou betekenen dat iemand pas maanden later
        # ontdekt dat zijn "30 dagen" er in werkelijkheid 12 waren.
        log.warning(
            "Opruiming: %d pakketten weg, waarvan %d op de bovengrens (%s). "
            "Er staat nu %s dagen aan pakketten in de databank, tegen %d ingesteld.",
            gone, report["packets_rows"] + report["packets_bytes"],
            "rijen" if report["limit_hit"] == "rows" else "bestandsgrootte",
            report["effective_days"], report["days"])
    elif gone or report["samples"]:
        log.info("Opruiming: %d pakketten en %d metingen weg", gone, report["samples"])
    if report["vacuum"]["ran"]:
        log.info("Databank herschreven: %s", report["vacuum"]["reason"])
    return report


def overview() -> dict:
    """Alles wat de beheerpagina over de opslag moet kunnen zeggen.

    De actuele meting (hoe groot, hoeveel pakketten, welk tijdvenster) en de
    laatste ronde (wanneer, hoeveel weg) naast elkaar, plus het oordeel dat
    daaruit volgt. Dat oordeel wordt hier geveld en niet in de sjabloon: de vraag
    "wordt de ingestelde termijn gehaald" heeft drie antwoorden en een sjabloon
    met drie takken erin is een sjabloon waar het vierde geval uit valt.
    """
    now = db.storage_overview()
    last = last_report()
    # Snijdt een bovengrens, dan is de termijn een wens en geen feit. Dat wordt
    # afgelezen aan de laatste ronde en niet aan de huidige rijtelling: dat het
    # er op dit ogenblik net onder zit, wil niet zeggen dat er een uur geleden
    # niets is weggegooid dat er volgens de termijn nog had moeten staan.
    hit = (last or {}).get("limit_hit") or ""
    short = bool(hit and now["effective_days"] is not None
                 and now["effective_days"] < now["days"] - 0.5)
    return {
        **now,
        "last": last,
        "state": dict(_state),
        "limit_hit": hit,
        # Waar of niet: haalt deze server de termijn die erop staat?
        "falls_short": short,
        "over_ceiling": now["db_bytes"] > now["ceiling_bytes"],
    }


def _run() -> None:
    time.sleep(FIRST_RUN_DELAY_S)
    while True:
        try:
            run_once()
        except Exception as err:  # noqa: BLE001 - een ronde mag de thread niet doden
            log.exception("Opruiming mislukte onverwacht: %s", err)
            _state["last_error"] = str(err)
        time.sleep(INTERVAL_MIN * 60)


_thread = None


def start() -> None:
    """Start de opruimlus. Idempotent, zoals de andere planners in deze app."""
    global _thread
    if _thread is not None:
        return
    _thread = threading.Thread(target=_run, name="retention", daemon=True)
    _thread.start()
