"""Het pakketfilter van een gewone MeshCore-repeater met filterpatch lezen.

Waarom hier een tweede parser staat
-----------------------------------
Er lopen twee filterimplementaties in hetzelfde mesh, en dat gaat niet meer weg:

``meshmanager``       onze eigen node-firmware (kolom ``fw_meshmanager``). Die
                      publiceert zijn filterstand als JSON in het
                      statistiekenbericht -- zie ``mqtt_ingest._handle_filter``
                      -- en kent daarnaast ``/api/filter`` over IP.
``meshcore_filter``   een stock MeshCore-repeater waar iemand een filterpatch in
                      heeft gezet (kolom ``fw``, bv. ``v1.17.1-PS+filter``).
                      Die publiceert helemaal niets: je krijgt zijn stand alleen
                      door ``filter count`` over de CLI te vragen en de TEKST te
                      lezen die eruit komt.

Andere commando's, andere tekst, en -- dat is het punt van deze module -- een
KLEINERE set cijfers. De stock-variant heeft geen ``passed``, geen ``exempt`` en
geen dropteller per pakkettype.

Dus twee parsers, maar één opslagvorm. Wat hier uitkomt heeft dezelfde sleutels
als de blob die ``_handle_filter`` wegschrijft (``db.upsert_filter_state``),
zodat ``pktfilter.summarise`` en ``pktfilter.breakdown`` er zonder tweede
codepad iets van kunnen maken. Een aparte tabel of een tweede blobvorm zou
betekenen dat elke pagina die een filterstand toont beide vormen moet kennen, en
dan staat de grammatica van een filterstand op drie plaatsen in plaats van één.

De regel die de rest van dit bestand verklaart: ONTBREKEND IS NIET NUL
---------------------------------------------------------------------
De verleiding is groot om ``passed: 0`` in de blob te zetten zodat elk veld
gevuld is. Dat is precies de stille leugen die dit project niet wil. Een nul die
een node publiceert is een METING -- 'dit filter liet vandaag niets door' -- en
die hoort in een grafiek te staan. Een nul die wij verzinnen omdat de firmware
het cijfer niet kent, ziet er in diezelfde grafiek identiek uit en betekent iets
volstrekt anders. Vandaar dat een reeks die de stock-variant niet meldt hier
gewoon ONTBREEKT, en dat ``capabilities()`` bestaat: daarmee kan de pagina een
leeg vak uitleggen als "deze firmware meldt dit niet" in plaats van het als een
gat te tonen.

En dezelfde houding als in ``mqtt_ingest``: dit is tekst van een node die niet
de onze is, dus vaste sleutelnamen, vaste grenzen, vaste maxima. Wat er niet in
past wordt genegeerd of geweigerd, nooit doorgegeven.
"""

from __future__ import annotations

import re

from . import commanding

# De versie van onze eigen module die een filter heeft. Zelfde getal als
# ``pktfilter.MIN_FILTER_VERSION`` en met opzet niet daaruit geïmporteerd: deze
# module is een tekstparser en hoort niet de hele schrijfweg (nodeconfig,
# firmware, urllib) mee te trekken voor één tupel. Wijkt het ooit af, dan is dat
# een fout in één van de twee -- test_pfstock.py houdt ze gelijk.
MIN_MESHMANAGER_FILTER = (2, 3, 0)

# Het merkteken in de firmwarestring van de gepatchte stock-repeater.
# ``v1.17.1-PS+filter+rollback`` -- de patches staan als plusdelen achter de
# versie, dus we zoeken het deel en niet een positie.
STOCK_MARKER = "+filter"

# De pakkettypes zoals de firmware ze nummert. Hier herhaald en niet uit het
# antwoord van de node overgenomen, om dezelfde reden als bij
# ``mqtt_ingest.PF_TYPE_NAMES``: de node levert getallen, wij bepalen hoe ze
# heten. ``parse_filter_types`` leest de nummering die de node zelf toont, en
# wie die wil laten meewegen geeft hem mee aan ``parse_filter_count(names=...)``.
TYPE_NAMES = ("REQ", "RESPONSE", "TXT_MSG", "ACK", "ADVERT", "GRP_TXT",
              "GRP_DATA", "ANON_REQ", "PATH", "TRACE", "MULTIPART", "CONTROL")

# ``filter types`` toont twaalf types (t/m 11 = CONTROL), maar ``filter hops``
# en ``filter rate`` aanvaarden alleen 0-10. Een limietregel voor 11 kan dus
# nooit ergens vandaan komen; hij zou een parser zijn die iets accepteert wat de
# node niet kan hebben gezet. Twee grenzen dus, en niet één.
MAX_TYPE_NUMBER = len(TYPE_NAMES) - 1        # voor `filter types`
MAX_LIMIT_TYPE_NUMBER = len(TYPE_NAMES) - 2  # voor de limiettabel

# De categorieën in het Blocked-blok, met de sleutel die ze in de blob krijgen.
# Let op de twee Nederlandse sleutels: die vorm ligt vast in
# ``mqtt_ingest.FILTER_DROP_METRICS`` en in ``pktfilter.DROP_LABELS``, en één
# afwijkende naam hier zou een reden zijn die nergens meer een label heeft.
#
# Wat er NIET bij staat is ``type`` -- de zesde reden uit die lijst. De
# stock-variant kan een pakkettype niet helemaal dichtzetten en telt er dus ook
# niets voor. Een sleutel met een nul zou beweren dat hij die regel heeft.
STOCK_DROP_KEYS = {
    "hops": "hops",
    "rate": "rate",
    "channel": "kanaal",
    "hash": "hash",
    "malformed": "misvormd",
}

# Dezelfde bovengrens als ``mqtt_ingest._num``: hierboven is het geen teller
# meer maar rommel op de lijn.
MAX_COUNTER = 4_000_000_000

# Een hoplimiet is in MeshCore een byte, een snelheidslimiet een aantal
# pakketten per venster. Strenger dan MAX_COUNTER en met opzet: dit is
# CONFIGURATIE die wij straks in een formulier terugleggen, en een hoplimiet van
# een miljard is geen limiet maar een leesfout.
MAX_HOP_LIMIT = 255
MAX_RATE_LIMIT = 65535

# ``> Filter off: Blocked [ ... ]`` -- de prompt-echo ervoor mag er staan of niet.
_HEADER = re.compile(r"filter\s+(on|off)\s*:", re.IGNORECASE)
# ``Hops: 12`` binnen het Blocked-blok. Het minteken wordt MEEGELEZEN zodat een
# negatief getal een geweigerde waarde is en niet een half gelukte match.
_COUNTER = re.compile(r"([A-Za-z]+)\s*:\s*(-?\d+)")
# ``04: 3,20`` -- de limiettabel, één regel per pakkettype.
_LIMIT_LINE = re.compile(r"^\s*(\d{1,3})\s*:\s*(-?\d+)\s*,\s*(-?\d+)\s*$")
# ``00=REQ`` uit ``filter types``, dat als één lange regel of als twaalf regels
# kan aankomen.
_TYPE_PAIR = re.compile(r"(\d{1,3})\s*=\s*([A-Za-z0-9_]{1,23})")


def _counter(text: str) -> int | None:
    """Een teller uit de tekst, of None als het er geen kan zijn."""
    try:
        waarde = int(text)
    except (TypeError, ValueError):
        return None
    if waarde < 0 or waarde > MAX_COUNTER:
        return None
    return waarde


def _limit(text: str, maximum: int) -> int | None:
    """Een geconfigureerde limiet, of None. 0 is een geldige waarde: geen limiet."""
    try:
        waarde = int(text)
    except (TypeError, ValueError):
        return None
    if waarde < 0 or waarde > maximum:
        return None
    return waarde


def _type_name(nummer: int, names: dict | None) -> str:
    """De naam van een pakkettype, uit de meegegeven nummering of uit de onze.

    De nummering van de node krijgt voorrang omdat een patch er een type bij kan
    zetten, maar hij wordt wel eerst schoongemaakt: het is tekst van een node.
    """
    if isinstance(names, dict):
        naam = names.get(nummer)
        if isinstance(naam, str):
            schoon = "".join(c for c in naam if c.isalnum() or c == "_")[:23]
            if schoon:
                return schoon.upper()
    if 0 <= nummer < len(TYPE_NAMES):
        return TYPE_NAMES[nummer]
    return ""


def parse_filter_count(text, *, names: dict | None = None) -> dict | None:
    """Het antwoord op ``filter count`` als filterstand-blob, of None.

    De tekst heeft twee blokken die niets met elkaar te maken hebben, en die
    verwarren is de fout waar deze functie tegen beschermt::

        > Filter off: Blocked [ Hops: 0 | Rate: 0 | Channel: 0 | Hash: 0 | Malformed: 0 ]
        [TYPE: HOPS,RATE]
        00: 0,0
        01: 3,20

    De eerste regel is de hoofdschakelaar plus WEGGEGOOIDE AANTALLEN per reden.
    Het tweede blok is de INGESTELDE hop- en snelheidslimiet per pakkettype --
    configuratie, geen tellers, en ``0,0`` betekent 'geen limiet'. Ze staan
    daarom in twee sleutels: ``drop`` (tellers, gaat als metric verder) en
    ``limits`` (regels, hoort bij de regeltabellen). In ``drop`` gezet zouden ze
    grafieken vullen met een getal dat nooit oploopt.

    Twee valkuilen die uit die scheiding volgen, want de blob heeft namen die
    hier iets ANDERS betekenen dan in de tekst:

    - ``Hash: 0`` en ``Malformed: 0`` zijn hier TELLERS, terwijl de blob een
      ``hash`` (de ingestelde minimale padhash) en een ``malformed`` (aan/uit)
      op het hoogste niveau heeft. Ze gaan dus in ``drop`` en nergens anders.
    - ``Channel: 0`` is een teller, en de blob-sleutel ``channels`` is het
      AANTAL geblokkeerde kanalen. Die laatste blijft leeg: de stock-variant
      vertelt in dit antwoord niet hoeveel kanaalregels er staan.

    Alles wat de stock-variant niet meldt ontbreekt, inclusief ``passed`` en
    ``exempt``. Een blok dat er niet in stond levert geen lege dict maar geen
    sleutel: ``pktfilter.summarise`` leest een ontbrekende ``drop`` als 'niets
    weggegooid gemeld' en dat is wat het is.

    None komt eruit bij tekst die geen filterantwoord is -- een foutmelding van
    een node die het commando niet kent, een leeg antwoord, een stuk van een
    ander commando. Dat onderscheid is de reden dat de hoofdschakelaar verplicht
    is: zonder ``Filter on:`` of ``Filter off:`` weten we niet eens of we naar
    het goede antwoord kijken, en dan is een half gevulde blob erger dan niets.
    """
    if not isinstance(text, str) or not text.strip():
        return None

    regels = text.replace("\r", "\n").split("\n")

    kop = None
    kopregel = ""
    for regel in regels:
        m = _HEADER.search(regel)
        if m:
            kop = m
            kopregel = regel
            break
    if kop is None:
        return None

    uit: dict = {"on": kop.group(1).lower() == "on", "variant": "meshcore_filter"}

    # Alleen de KOPREGEL wordt op tellers afgezocht, en het liefst alleen wat
    # tussen de blokhaken staat. Zou dit over de hele tekst lopen, dan zou een
    # limietregel als ``05: 3,20`` of een losse zin uit een ander commando er
    # tellers bij verzinnen.
    haakjes = re.search(r"\[([^\]]*)\]", kopregel[kop.end():])
    blok = haakjes.group(1) if haakjes else kopregel[kop.end():]
    drop: dict = {}
    for naam, waarde in _COUNTER.findall(blok):
        sleutel = STOCK_DROP_KEYS.get(naam.lower())
        if sleutel is None:
            # Een categorie die wij niet kennen. Negeren en niet doorgeven: de
            # namen in de blob zijn de namen waar labels en metrics aan hangen.
            continue
        getal = _counter(waarde)
        if getal is None:
            # Eén onzinnige waarde maakt de andere vier niet onbetrouwbaar; die
            # zijn gemeten en staan er los van. Alleen deze reden valt af, en
            # die is dan afwezig in plaats van nul -- zie de kop van dit bestand.
            continue
        drop[sleutel] = getal
    if drop:
        uit["drop"] = drop

    limieten: dict = {}
    for regel in regels:
        m = _LIMIT_LINE.match(regel)
        if not m:
            # Ook de afgekapte laatste regel van een ingekort antwoord komt hier
            # terecht (``10: 0,``), en dat is de bedoeling: een half getal is
            # geen limiet.
            continue
        nummer = int(m.group(1))
        if nummer > MAX_LIMIT_TYPE_NUMBER:
            continue
        hops = _limit(m.group(2), MAX_HOP_LIMIT)
        rate = _limit(m.group(3), MAX_RATE_LIMIT)
        if hops is None or rate is None:
            # Hier wél de hele regel weigeren en niet één veld: de twee getallen
            # komen uit hetzelfde paar, en 'hoplimiet 3, snelheidslimiet
            # onbekend' is geen toestand die de node kan hebben.
            continue
        naam = _type_name(nummer, names)
        if not naam:
            continue
        # ``0,0`` blijft staan. Het is de gemelde configuratie 'voor dit type
        # geldt geen limiet', en dat is iets anders dan een type dat niet in het
        # antwoord voorkwam -- precies het onderscheid dat een formulier straks
        # nodig heeft om te weten of het een veld leeg mag laten.
        limieten[naam] = {"hops": hops, "rate": rate}
    if limieten:
        uit["limits"] = limieten

    return uit


def parse_filter_types(text) -> dict | None:
    """Het antwoord op ``filter types`` als nummer -> naam, of None.

    ``00=REQ 01=RESPONSE 02=TXT_MSG ...``, of dezelfde paren over meerdere
    regels -- beide vormen komen langs, dus er wordt op paren gezocht en niet op
    regels.

    Waarom dit een eigen commando en een eigen functie is: de nummering hoort bij
    de firmware, niet bij ons. Wij hebben een eigen lijst (``TYPE_NAMES``) om een
    formulier te kunnen tekenen als er niets gevraagd is, maar zodra de node zijn
    eigen lijst geeft, is die de waarheid over welk nummer welk type is. Een
    patch die er een type bij zet, mag geen labels laten verschuiven.

    Integer-sleutels en geen strings, want dit is een nummering waarop
    vergeleken en gesorteerd wordt; ``"10" < "9"`` is precies de fout die
    ``commanding.parse_version`` ook moest wegwerken.
    """
    if not isinstance(text, str) or not text.strip():
        return None
    uit: dict = {}
    for nummer, naam in _TYPE_PAIR.findall(text):
        num = int(nummer)
        if num > MAX_TYPE_NUMBER:
            # Buiten de nummering die de firmware kent. Meelezen zou betekenen
            # dat een node de lijst zo lang kan maken als hij wil.
            continue
        uit[num] = naam.upper()
    return uit or None


def variant(rep) -> str:
    """Welke filterimplementatie er op deze node praat.

    ``meshmanager``       onze module, nieuw genoeg om een filter te hebben
    ``meshcore_filter``   stock MeshCore met de filterpatch (``+filter`` in fw)
    ``geen``              we weten welke firmware er draait, en die heeft er geen
    ``onbekend``          we weten nog niet welke firmware er draait

    De laatste twee zijn met opzet niet hetzelfde, en om dezelfde reden als de
    drie toestanden in ``pktfilter.summarise``: 'deze node heeft geen filter' is
    een bewering, 'we hebben deze node nog nooit gehoord' is er geen. Alleen bij
    de eerste mag een pagina zeggen dat er niets te configureren valt.

    De volgorde is niet willekeurig. Onze eigen module wint, want een node die
    hem draait heeft een stock-firmwarestring waar ``+filter`` in KAN staan
    (onze module draait bovenop MeshCore) -- en dan is de rijkere weg de juiste.
    Een ``fw_meshmanager`` die te oud is voor een filter valt door naar de
    stock-vraag en daarna naar ``geen``: die node heeft echt geen filter.

    ``rep`` is een sqlite3.Row, een dict of None. Alle drie komen hier binnen,
    dus hetzelfde ``_field`` als in ``commanding``.
    """
    mm = commanding._field(rep, "fw_meshmanager") or ""
    fw = commanding._field(rep, "fw") or ""

    versie = commanding.parse_version(mm)
    if versie is not None and versie >= MIN_MESHMANAGER_FILTER:
        return "meshmanager"
    if STOCK_MARKER in str(fw).lower():
        return "meshcore_filter"
    if str(fw).strip() or str(mm).strip():
        return "geen"
    return "onbekend"


# Wat elke variant kan leveren. Eén tabel en geen reeks ifs, zodat de pagina en
# de ingest naar hetzelfde antwoord kijken -- en zodat er bij een derde variant
# één regel bij komt in plaats van een vertakking in elke template.
#
# De sleutels zijn secties en reeksen zoals een scherm ze toont, niet velden uit
# de blob: 'kan deze node mij snelheidsdruk vertellen' is de vraag die een lege
# grafiek moet kunnen uitleggen. Een False hier betekent "deze firmware meldt dit
# niet" en is dus een ZIN op het scherm; een ontbrekende sleutel in de blob bij
# een True betekent dat de node het wel kan maar nog niets zei.
_CAPABILITIES: dict = {
    "meshmanager": {
        "naam": "MeshManager-firmware",
        "aan_uit": True,
        "veilige_modus": True,       # 'disarmed' na herhaalde herstarts
        "regeltabellen": True,       # hash/malformed/channels/blocked_types
        "limieten": True,            # hop- en snelheidslimiet per type
        "drop_per_reden": True,      # drop{}: zes redenen
        "drop_per_type": True,       # stats.xr: type x reden
        "snelheidsdruk": True,       # stats.rate: seen/cap/peak/lim
        "passed": True,
        "exempt": True,
        "kanalen": True,             # stats.chan: label, hash, treffers
        "drop_redenen": ("type", "hops", "rate", "hash", "kanaal", "misvormd"),
    },
    "meshcore_filter": {
        "naam": "MeshCore met filterpatch",
        "aan_uit": True,
        # Geen veilige modus: de patch heeft geen herstarttelling die het filter
        # zelf uitzet, dus 'uit' is daar altijd 'uitgezet'.
        "veilige_modus": False,
        # De regeltabellen zelf komen niet uit `filter count`. Ze zijn er wel --
        # `filter hops`/`filter rate` zetten ze -- maar dit antwoord vertelt niet
        # hoeveel kanaalregels of dichtgezette types er staan.
        "regeltabellen": False,
        "limieten": True,
        "drop_per_reden": True,
        # Geen dropteller per pakkettype: de patch telt per REDEN en houdt geen
        # kruistabel bij. Dit is de sectie die op de pagina het vaakst als 'gat'
        # gelezen zou worden, en dat is precies waarom deze tabel bestaat.
        "drop_per_type": False,
        "snelheidsdruk": False,
        "passed": False,
        "exempt": False,
        "kanalen": False,
        # Vijf en geen zes: 'type' ontbreekt, zie STOCK_DROP_KEYS.
        "drop_redenen": ("hops", "rate", "hash", "kanaal", "misvormd"),
    },
}

_CAPABILITIES_LEEG: dict = {
    "naam": "",
    "aan_uit": False,
    "veilige_modus": False,
    "regeltabellen": False,
    "limieten": False,
    "drop_per_reden": False,
    "drop_per_type": False,
    "snelheidsdruk": False,
    "passed": False,
    "exempt": False,
    "kanalen": False,
    "drop_redenen": (),
}


def capabilities(variant_naam) -> dict:
    """Welke secties en reeksen deze variant kan leveren.

    Bedoeld om een leeg vak te kunnen UITLEGGEN. Zonder dit kan een pagina niet
    kiezen tussen "deze repeater gooide niets weg", "deze repeater heeft nog
    niets gezegd" en "deze firmware kent dit cijfer niet", en de eerste twee
    zijn meldingen terwijl de derde een eigenschap is.

    Een onbekende naam levert de lege set en niet een KeyError: dit wordt uit een
    template gelezen, en een pagina die niet rendert omdat er een nieuwe
    firmwarevariant in het mesh hangt is erger dan een pagina die van niets weet.
    Een KOPIE, zodat een aanroeper die er iets in zet de tabel niet vergiftigt.
    """
    return dict(_CAPABILITIES.get(variant_naam, _CAPABILITIES_LEEG))


# --- de brug naar de rest van de site ----------------------------------------

def apply_cli_filter(repeater_id: int, values: dict, source: str = "") -> bool:
    """Een `cmd:filter ...`-antwoord uit een CLI-sweep als filterstand opslaan.

    Waarom dit hier staat en niet in de ingest-route: het is dezelfde vertaalslag
    als ``mqtt_ingest._handle_filter`` doet voor nodes die hun filterstand zelf
    meepubliceren, alleen komt de tekst hier langs de trage weg binnen -- een
    sweep over LoRa. De rest van de site hoort daarna niet te kunnen zien welke
    van de twee wegen het was: dezelfde blobvorm, dezelfde metricnamen, dezelfde
    tegels.

    Zonder deze brug bleef de parser een parser: `cmd:filter count` landde als
    tekst in ``repeater_cli`` en er gebeurde niets zichtbaars. Dat was precies de
    klacht -- "ik zie geen verschil" -- en het lag niet aan de radio.

    Geeft True terug als er werkelijk iets weggeschreven is, zodat de aanroeper
    het kan loggen zonder zelf te hoeven raden of er een filterantwoord in zat.
    """
    from . import db

    ruw = None
    for sleutel, waarde in (values or {}).items():
        if str(sleutel).strip().lower().startswith("cmd:filter") and waarde:
            ruw = str(waarde)
            break
    if not ruw:
        return False

    blob = parse_filter_count(ruw)
    if not blob:
        return False

    db.upsert_filter_state(repeater_id, blob, source)

    # Dezelfde tellers ook als gewone metrics, want dat is wat de tegels en de
    # grafieken lezen. De import staat in de functie: mqtt_ingest trekt de halve
    # app mee en dit bestand hoort een tabel te blijven die je los kunt draaien.
    try:
        from .mqtt_ingest import _filter_metrics
        metrics = _filter_metrics(blob)
    except Exception:
        metrics = {}
    if metrics:
        # force=True: een sweep komt hooguit een paar keer per dag langs, en dan
        # is "de waarde veranderde niet" geen reden om het punt weg te laten --
        # anders staat er een gat in de grafiek waar wel gemeten is.
        db.ingest(repeater_id, db.utcnow(), metrics, None, force=True)
    return True
