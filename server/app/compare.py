"""Alle repeaters naast elkaar: versies en instellingen in één tabel.

Waarom dit bestaat en waarom het een eigen weergave is en geen kolom erbij op de
nodepagina: de vraag die hier beantwoord wordt is niet "hoe staat deze node
ervoor" maar "welke node loopt uit de pas". Dat is een vraag over de verzameling,
en je kunt hem alleen stellen als je de waarden naast elkaar ziet. Bij drie nodes
kun je dat nog in je hoofd; bij twintig niet meer, en dan is een node met een
afwijkende instelling iets wat je pas ontdekt als er iets misgaat.

Vandaar dat de nadruk hier ligt op **afwijkingen markeren** en niet op het netjes
tonen van waarden. Een tabel met twintig kolommen die allemaal hetzelfde zeggen
is een tabel waarin niemand het ene vakje ziet dat iets anders zegt.

De regel voor 'wijkt af' staat hieronder bij ``_verdeling`` en is met opzet
terughoudend: alleen markeren als er een echte meerderheid is om van af te
wijken. Twee nodes met twee verschillende waarden hebben geen afwijker, ze zijn
het gewoon oneens, en beide vakjes rood kleuren zegt niets.
"""

from __future__ import annotations

from collections import Counter

from . import commanding, db, firmware, nodeconfig

# Kolommen die niet uit de CLI-sweep komen maar uit de repeatertabel zelf. Ze
# staan vooraan omdat ze de vraag beantwoorden die je meestal het eerst stelt --
# draait iedereen hetzelfde? -- en omdat ze er altijd zijn, ook voor een node
# waarvan nog nooit een instelling gelezen is.
BUILTIN = [
    ("fw_meshmanager", "Nodefirmware"),
    ("fw", "MeshCore"),
    ("level", "Beheerniveau"),
    ("pio_env", "Bouwomgeving"),
    # Het uitvraagschema hoort hier thuis om dezelfde reden als de rest: bij
    # twintig nodes is "welke staan er eigenlijk op nooit" een vraag die je over
    # de verzameling stelt, niet over één node. Een node die als enige geen
    # schema heeft is precies de node waarvan de waarden stilletjes verouderen.
    ("sweep_hours", "Uitvraagschema"),
]
BUILTIN_KEYS = [k for k, _ in BUILTIN]

# Wat er standaard in beeld staat als niemand iets gekozen heeft. Bewust kort:
# een tabel die bij het openen al twintig kolommen breed is, is een tabel waarin
# je gaat scrollen in plaats van kijken. Dit zijn de vier waar Björn naar vroeg
# plus de versie, en de rest kiest de gebruiker erbij.
DEFAULT_COLUMNS = [
    "fw_meshmanager",
    "level",
    "region.home",
    "region.default",
    "flood.max.unscoped",
    "flood.max",
]

SETTING_KEY = "compare_columns"


def available(settings_by_node: dict) -> list[tuple[str, str]]:
    """Alle kolommen die gekozen kunnen worden, met hun label.

    De ingebouwde eerst, daarna elke parameter die van minstens één node ooit
    gelezen is. Niet de volledige lijst die de firmware zou toelaten: een kolom
    aanbieden waar nergens een waarde voor bestaat levert een lege kolom op, en
    een lege kolom lijkt op een probleem terwijl er alleen nooit iets uitgelezen
    is.
    """
    params: set[str] = set()
    for waarden in settings_by_node.values():
        params.update(waarden)
    return BUILTIN + [(p, p) for p in sorted(params)]


def parse_columns(raw: str, keuzes: list[str]) -> list[str]:
    """De kolomkeuze uit een opgeslagen tekenreeks, ontdaan van wat niet bestaat.

    Onbekende sleutels vallen weg in plaats van een fout op te leveren: de keuze
    overleeft in de instellingen, en een parameter die tijdelijk niet uitgelezen
    is (of die van een node kwam die verwijderd is) mag geen beheerpagina breken.
    Blijft er niets over, dan de standaard -- een tabel zonder kolommen is geen
    tabel.
    """
    gekozen = [k.strip() for k in (raw or "").split(",") if k.strip()]
    gefilterd = [k for k in gekozen if k in keuzes]
    return gefilterd or [k for k in DEFAULT_COLUMNS if k in keuzes] or keuzes[:4]


# Aparte schildwacht voor "deze parameter is nooit uitgevraagd". None is al
# bezet door "gevraagd, geen antwoord", en een derde toestand vraagt om een derde
# waarde in plaats van om een tweede betekenis voor dezelfde.
class _Missing:
    def __repr__(self):
        return "MISSING"


MISSING = _Missing()


def _telt_mee(waarde) -> bool:
    """Telt deze waarde mee bij het bepalen van de norm?

    Alleen een echt antwoord. Stilte en 'nooit gevraagd' zijn geen mening over de
    instelling, en als de helft van de nodes nog nooit uitgevraagd is, hoort de
    andere helft nog steeds een meerderheid te kunnen vormen.
    """
    return waarde is not None and waarde is not MISSING and waarde != ""


def _verdeling(waarden: list) -> str | None:
    """De waarde waar de meerderheid het over eens is, of None.

    Strikte meerderheid: meer dan de helft van de nodes die überhaupt een waarde
    hebben. Zonder die eis zou bij vier nodes met vier verschillende waarden de
    toevallige eerste tot norm gebombardeerd worden en zouden de andere drie als
    afwijking oplichten -- vier rode vakjes die samen niets betekenen.

    Nodes zonder waarde tellen niet mee in de noemer. 'Niet uitgelezen' is geen
    mening over de instelling, en als de helft van de nodes nog nooit uitgevraagd
    is, hoort de andere helft nog steeds een meerderheid te kunnen vormen.
    """
    echte = [w for w in waarden if _telt_mee(w)]
    if len(echte) < 3:
        # Bij twee is 'de meerderheid' één node, en dan markeer je de ander als
        # afwijker op gezag van niets. Vanaf drie begint het iets te zeggen.
        return None
    waarde, aantal = Counter(echte).most_common(1)[0]
    return waarde if aantal * 2 > len(echte) else None


def build(repeaters, columns: list[str] | None = None, broker_connected: bool = False) -> dict:
    """De hele tabel: rijen, kolommen, waarden en welke daarvan afwijken.

    Geeft alles in één keer terug zodat het sjabloon niets hoeft uit te rekenen.
    Een template dat zelf gaat tellen wie de meerderheid is, is een template dat
    je niet kunt testen zonder het te renderen.
    """
    ruw = db.cli_settings_all()
    # Drie toestanden, geen twee. Een rij die er niet is betekent "nooit
    # gevraagd"; een rij met NULL betekent "gevraagd, geen antwoord" -- dat is de
    # afspraak die mqtt_ingest._clean_settings vastlegt en die de nodepagina al
    # als "(geen antwoord)" toont. Ze hier tot één leeg vakje platslaan zou het
    # verschil wegpoetsen tussen een node die niet antwoordt en een parameter die
    # nooit op de lijst stond, en dat zijn twee heel verschillende problemen.
    per_node: dict[int, dict[str, str | None]] = {}
    ouderdom: dict[int, dict[str, str]] = {}
    for r in ruw:
        per_node.setdefault(r["repeater_id"], {})[r["param"]] = r["value"]
        ouderdom.setdefault(r["repeater_id"], {})[r["param"]] = r["updated"]

    keuzes = available(per_node)
    keys = [k for k, _ in keuzes]
    kolommen = columns if columns else list(DEFAULT_COLUMNS)
    kolommen = [k for k in kolommen if k in keys] or [k for k in DEFAULT_COLUMNS if k in keys]

    labels = dict(keuzes)

    rijen = []
    for rep in repeaters:
        rid = rep["id"]
        route = commanding.describe(rep, broker_connected=broker_connected)
        cfg = nodeconfig.cfg_route(rep)
        waarden = {}
        for k in kolommen:
            if k == "level":
                waarden[k] = route.get("level", "")
            elif k in BUILTIN_KEYS:
                if k == "sweep_hours":
                    # 0 en NULL betekenen allebei 'uit', en dat is een antwoord
                    # en geen leegte -- anders zou een node zonder schema er
                    # uitzien als een node waarover we niets weten.
                    uren = int(firmware._field(rep, k) or 0)
                    waarden[k] = f"{uren}u" if uren else "uit"
                    continue
                waarde = str(firmware._field(rep, k) or "")
                # De MeshCore-kolom is de enige vaste kolom die ook uit de sweep
                # gevuld kan worden: een repeater die zelf publiceert stuurt zijn
                # versie mee, een doorgestuurde repeater niet, en voor die tweede
                # komt hij van 'cmd:ver' over LoRa. Staat er niets, dan hangt de
                # toestand van dat vakje dus af van of die vraag gesteld is --
                # anders zou een node die nog nooit uitgevraagd is er hetzelfde
                # uitzien als een die weigerde te antwoorden.
                if waarde or k != "fw":
                    waarden[k] = waarde
                elif "cmd:ver" in per_node.get(rid, {}):
                    # Gevraagd. Of de node zweeg, of hij antwoordde iets waar
                    # geen versie uit te halen viel -- van hieraf hetzelfde:
                    # we hebben het gevraagd en we weten het niet.
                    waarden[k] = None
                else:
                    waarden[k] = MISSING
            else:
                gelezen = per_node.get(rid, {})
                # MISSING = nooit gevraagd, None = gevraagd zonder antwoord.
                waarden[k] = gelezen[k] if k in gelezen else MISSING
        rijen.append({
            "rep": rep,
            "waarden": waarden,
            "ouderdom": ouderdom.get(rid, {}),
            "route": route,
            "cfg": cfg,
        })

    # Per kolom bepalen wat de norm is, en dan pas per vakje of het afwijkt.
    # Andersom kan niet: 'afwijken' is geen eigenschap van een waarde maar van
    # een waarde tussen de andere.
    norm = {}
    for k in kolommen:
        norm[k] = _verdeling([r["waarden"][k] for r in rijen])

    for rij in rijen:
        rij["afwijkend"] = {
            k: bool(norm[k]) and _telt_mee(rij["waarden"][k]) and rij["waarden"][k] != norm[k]
            for k in kolommen
        }
        # 'Niets uitgelezen' en 'geen antwoord' zijn eigen toestanden en geen
        # afwijking. Ze zeggen iets over ons -- we hebben het nooit gevraagd, of
        # nooit antwoord gehad -- en niet over de instelling van de node. Die door
        # elkaar halen levert een tabel op die een uitleesprobleem toont als een
        # configuratieprobleem, en dan ga je de verkeerde node repareren.
        rij["stil"] = {k: rij["waarden"][k] is None for k in kolommen}
        rij["onbekend"] = {k: rij["waarden"][k] is MISSING for k in kolommen}

    return {
        "rijen": rijen,
        "kolommen": kolommen,
        "labels": labels,
        "keuzes": keuzes,
        "norm": norm,
        "afwijkers": sum(1 for r in rijen for k in kolommen if r["afwijkend"][k]),
    }
