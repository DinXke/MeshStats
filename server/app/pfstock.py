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
                      door hem over de CLI te vragen en de TEKST te lezen die
                      eruit komt -- en dat zijn TWEE commando's (gemeten op
                      JessaZH, 2026-09-04): het kale ``filter`` geeft de
                      statusregel met hoofdschakelaar en tellers, ``filter count``
                      geeft alleen de limiettabel. Elk antwoord is één pakket, en
                      de node die het doorgeeft vlakt regeleindes tot spaties.

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
# De vensterlengte van een snelheidsregel, in seconden. Een dag is al absurd
# lang voor een limiet die per pakkettype geldt; alles daarboven is een leesfout.
MAX_WINDOW_SECS = 86400

# ``> Filter off: Blocked [ ... ]`` -- de prompt-echo ervoor mag er staan of niet.
_HEADER = re.compile(r"filter\s+(on|off)\s*:", re.IGNORECASE)
# ``Hops: 12`` binnen het Blocked-blok. Het minteken wordt MEEGELEZEN zodat een
# negatief getal een geweigerde waarde is en niet een half gelukte match.
_COUNTER = re.compile(r"([A-Za-z]+)\s*:\s*(-?\d+)")
# ``04: 3,20`` -- de limiettabel, één paar per pakkettype. Niet regelgebonden:
# de node die het antwoord doorgeeft maakt er één regel van, dus de paren staan
# achter elkaar achter de marker ``[TYPE: HOPS,RATE]``. Het minteken wordt
# MEEGELEZEN zodat een negatief getal een geweigerde waarde is.
# De drie tabellen die deze firmware kan geven, elk met hun eigen kopmarker:
#
#   filter count  ->  [TYPE: HOPS,RATE]    NN: <weg door hops>,<weg door rate>
#   filter hops   ->  [TYPE: MAX_HOPS]     NN: <hoplimiet>
#   filter rate   ->  [TYPE: LIMIT,SECS]   NN: <limiet>,<venster in s>
#
# LET OP -- dit is de valkuil van deze firmware, en hij is de moeite waard om
# hier uit te schrijven: de eerste tabel bevat TELLERS en niet de instellingen.
# "05: 2,10" betekent van type 05 zijn er 2 weggegooid op de hoplimiet en 10 op
# de snelheidslimiet (de DMC-filtergids zegt het met zoveel woorden). De vorm is
# identiek aan die van `filter rate`, waar dezelfde twee getallen de LIMIET en
# het VENSTER zijn. Ze uit elkaar houden kan dus alleen aan de marker.
#
# De eerste versie van deze parser las `filter count` als de limiettabel. Dat gaf
# een scherm vol nullen die eruitzagen als "geen enkele limiet gezet" terwijl het
# "nog niets weggegooid" betekende -- precies de stille leugen waar de rest van
# dit bestand tegen ontworpen is. Vandaar dat de instellingen nu alleen uit
# `filter hops` en `filter rate` komen, en nergens anders vandaan.
_LIMIT_MARKER = re.compile(r"\[TYPE:\s*(HOPS\s*,\s*RATE|MAX_HOPS|LIMIT\s*,\s*SECS)\]", re.IGNORECASE)
_LIMIT_PAIR = re.compile(r"(\d{1,3})\s*:\s*(-?\d+)\s*,\s*(-?\d+)(?=\s|$|[^\d,])")
_LIMIT_ONE = re.compile(r"(\d{1,3})\s*:\s*(-?\d+)(?=\s|$|[^\d,])")
# Zonder marker maar mét kopregel: dan telt alleen een HELE regel ``04: 3,20`` --
# de vorm waarin een repeater die regeleindes wél bewaart de tabel geeft.
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

    De twee blokken komen in de praktijk als TWEE antwoorden binnen: het kale
    ``filter`` geeft de kopregel, ``filter count`` alleen de tabel (gemeten op
    JessaZH). Elk van beide mag hier dus alleen staan; ``on`` ontbreekt dan bij
    een tabel-antwoord, en ``apply_cli_filter`` voegt de twee samen. Ook zijn ze
    niet regelgebonden: de node die het antwoord doorgeeft maakt er één regel
    van, dus de tabel wordt herkend aan zijn marker en zijn paren, niet aan
    regeleindes.

    None komt eruit bij tekst die geen van beide is -- een foutmelding van een
    node die het commando niet kent, een leeg antwoord, een stuk van een ander
    commando. Zonder kopregel én zonder limiettabel weten we niet eens of we
    naar het goede antwoord kijken, en dan is een half gevulde blob erger dan
    niets.
    """
    if not isinstance(text, str) or not text.strip():
        return None

    tekst = text.replace("\r", "\n")
    uit: dict = {"variant": "meshcore_filter"}

    # De KOPREGEL: hoofdschakelaar plus weggegooide aantallen. Alleen wat tussen
    # de blokhaken achter de schakelaar staat wordt op tellers afgezocht; zou dit
    # over de hele tekst lopen, dan zou een limietpaar als ``05: 3,20`` of een
    # losse zin uit een ander commando er tellers bij verzinnen.
    kop = _HEADER.search(tekst)
    if kop is not None:
        uit["on"] = kop.group(1).lower() == "on"
        rest = tekst[kop.end():]
        haakjes = re.search(r"\[([^\]]*)\]", rest)
        blok = haakjes.group(1) if haakjes else rest.split("\n", 1)[0]
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

    # De LIMIETTABEL: alles achter de marker tot aan een volgend blok. Zonder
    # marker geen tabel -- een los paar ``12: 3,4`` in een ander antwoord is geen
    # limiet.
    limieten: dict = {}
    drop_types: dict = {}
    marker = _LIMIT_MARKER.search(tekst)
    soort = ""
    paren: list = []
    if marker is not None:
        soort = re.sub(r"\s+", "", marker.group(1).upper())
        segment = tekst[marker.end():]
        volgend = re.search(r"\[|Filter\s+(on|off)\s*:", segment, re.IGNORECASE)
        if volgend:
            segment = segment[:volgend.start()]
        if soort == "MAX_HOPS":
            paren = [(n, h, None) for n, h in _LIMIT_ONE.findall(segment)]
        else:
            paren = [(n, a, b) for n, a, b in _LIMIT_PAIR.findall(segment)]
    elif kop is not None:
        # Geen marker, wel een kopregel: dezelfde tellertabel als losse regels,
        # zoals een repeater hem geeft die zijn regeleindes bewaart. Alleen hele
        # regels; een afgekapte (``10: 0,``) of te lange (``04: 2,20,30``) valt af.
        soort = "HOPS,RATE"
        paren = [m.groups() for m in (_LIMIT_LINE.match(r) for r in tekst.split("\n")) if m]
    for nummer_s, een_s, twee_s in paren:
        nummer = int(nummer_s)
        if nummer > MAX_LIMIT_TYPE_NUMBER:
            continue
        naam = _type_name(nummer, names)
        if not naam:
            continue
        if soort == "MAX_HOPS":
            hops = _limit(een_s, MAX_HOP_LIMIT)
            if hops is None:
                continue
            limieten[naam] = {"hops": hops}
            continue
        # Twee getallen uit hetzelfde paar: allebei of geen van beide. 'limiet 3,
        # venster onbekend' is geen toestand die de node kan hebben, en half
        # opslaan zou een formulier een verzonnen waarde laten terugschrijven.
        if soort == "LIMIT,SECS":
            rate = _limit(een_s, MAX_RATE_LIMIT)
            venster = _limit(twee_s, MAX_WINDOW_SECS)
            if rate is None or venster is None:
                continue
            limieten[naam] = {"rate": rate, "window": venster}
            continue
        # HOPS,RATE = TELLERS per type: weggegooid op de hoplimiet, weggegooid op
        # de snelheidslimiet. Geen configuratie -- zie de kop van dit bestand.
        weg_hops = _counter(een_s)
        weg_rate = _counter(twee_s)
        if weg_hops is None or weg_rate is None:
            continue
        drop_types[naam] = {"hops": weg_hops, "rate": weg_rate}
    if limieten:
        uit["limits"] = limieten
    if drop_types:
        uit["drop_types"] = drop_types

    if kop is None and not limieten and not drop_types:
        return None
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


# ``filter channel list``: één naam per geblokkeerd kanaal, mogelijk met de hash
# tussen haakjes -- de firmwarestring ``%s (%s)`` staat pal naast de kanaalcode,
# dus dat is vrijwel zeker "naam (hash)". VRIJWEL: met een lege lijst stuurt deze
# firmware helemaal geen bericht (handleCommand zet reply_len 0 en dan gaat er
# niets de lucht in), dus we hebben de niet-lege vorm nog niet op de lucht
# gezien. Vandaar een tolerante parser die op beide vormen werkt en die bij
# twijfel niets teruggeeft in plaats van een verzonnen kanaal.
_CHAN_MET_HASH = re.compile(r"([^\s()]{1,32})\s*\(\s*([0-9A-Fa-f#]{1,8})\s*\)")
# Een kanaalnaam zoals de firmware ze aanvaardt: ``#naam`` of ``Public``.
_CHAN_NAAM = re.compile(r"^#?[A-Za-z0-9][A-Za-z0-9._\-]{0,31}$")
# Woorden die in een antwoord staan maar geen kanaalnaam zijn.
_CHAN_RUIS = {"filter", "channel", "list", "add", "remove", "blocked", "channels",
              "none", "empty", "ok"}


def parse_filter_channels(text):
    """De geblokkeerde kanalen uit ``filter channel list``, of None.

    None betekent "hier valt niets uit te lezen" en NIET "geen kanalen": op deze
    firmware levert een lege lijst geen bericht op, dus stilte en een lege lijst
    zien er van hier af hetzelfde uit. Die twee mogen niet in één antwoord
    samenvallen -- de pagina moet kunnen zeggen welke van de twee het is.

    Een lijst met kanalen levert een lijst dicts ``{"label": ..., "hash": ...}``,
    dezelfde vorm die onze eigen firmware in zijn statistiekenbericht publiceert
    (mqtt_ingest), zodat de pagina er één weergave voor heeft.
    """
    if not isinstance(text, str) or not text.strip():
        return None
    # Een foutmelding of de usage-regel is geen lijst.
    if "syntax error" in text.lower() or "command error" in text.lower():
        return None
    if text.lstrip().startswith("> filter channel"):
        return None

    uit = []
    gezien = set()
    for naam, h in _CHAN_MET_HASH.findall(text):
        if naam.lower() in _CHAN_RUIS or not _CHAN_NAAM.match(naam):
            continue
        if naam not in gezien:
            gezien.add(naam)
            uit.append({"label": naam, "hash": h.lstrip("#").lower()[:2]})
    if uit:
        return uit

    # Geen haakjesvorm: dan losse namen, gescheiden door komma's of witruimte.
    schoon = text.replace(">", " ").replace(",", " ").replace(":", " ")
    for woord in schoon.split():
        if woord.lower() in _CHAN_RUIS or not _CHAN_NAAM.match(woord):
            continue
        if woord not in gezien:
            gezien.add(woord)
            uit.append({"label": woord, "hash": ""})
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
        "drop_per_type_redenen": ("type", "hops", "rate", "hash", "kanaal", "misvormd"),
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
        # Wél een dropteller per pakkettype, maar alleen voor TWEE redenen: de
        # hoplimiet en de snelheidslimiet (`filter count`). De andere drie redenen
        # (kanaal, padhash, misvormd) staan alleen in het totaal.
        "drop_per_type": True,
        "drop_per_type_redenen": ("hops", "rate"),
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
    "drop_per_type_redenen": (),
    "snelheidsdruk": False,
    "passed": False,
    "exempt": False,
    "kanalen": False,
    "drop_redenen": (),
}

# De fabrieksinstellingen van deze firmware, uit de DutchMeshCore-filtergids
# (toolbox.dutchmeshcore.nl/#/filter-guide, gelezen 2026-09-04). Hier alleen om
# naast de gemelde stand te kunnen tonen wat de standaard IS -- nooit om een
# leeg veld mee te vullen: wat de node niet meldde blijft leeg, want "we weten
# het niet" is iets anders dan "hij staat op de standaard".
STOCK_DEFAULTS = {
    "hash": 1,
    "malformed": False,
    "on": False,
    # (hoplimiet, snelheidslimiet, venster in seconden) per pakkettype.
    "types": {
        "REQ": (8, 5, 60), "RESPONSE": (8, 5, 60), "TXT_MSG": (8, 20, 60),
        "ACK": (8, 5, 60), "ADVERT": (8, 10, 60), "GRP_TXT": (32, 20, 60),
        "GRP_DATA": (8, 5, 60), "ANON_REQ": (8, 5, 60), "PATH": (8, 5, 60),
        "TRACE": (8, 5, 60), "MULTIPART": (8, 5, 60), "CONTROL": (8, 5, 60),
    },
}

# De twee voorbeeldopstellingen uit diezelfde gids, letterlijk. Als referentie op
# het scherm en niet als knop: elke regel heeft zijn eigen risicoweging, en één
# klik die er zes zet zou precies die drempel omzeilen.
STOCK_PRESETS = (
    ("Gewone publieke repeater",
     ("filter on", "filter hash 1", "filter malformed on", "filter rate 05 20 60",
      "filter rate 02 20 60", "filter hops 05 32")),
    ("Drukke of misbruikte repeater",
     ("filter on", "filter hash 2", "filter malformed on", "filter rate 05 10 60",
      "filter rate 02 5 60", "filter rate 04 5 60", "filter hops 05 16",
      "filter hops 02 16", "filter hops 04 8")),
)


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

    # ALLE filterantwoorden in deze push, niet het eerste: de statusregel komt
    # van `cmd:filter`, de limiettabel van `cmd:filter count`, en een volledige
    # ronde levert ze samen af. `cmd:filter help` en `cmd:filter types` komen
    # ook langs; die leveren geen blob en vallen er zo uit.
    nieuw: dict = {}
    for sleutel, waarde in (values or {}).items():
        naam = str(sleutel).strip().lower()
        if not naam.startswith("cmd:filter") or not isinstance(waarde, str) or not waarde.strip():
            continue
        # De kanaallijst is geen tabel met een marker maar losse namen, dus een
        # eigen parser. Hij staat vóór parse_filter_count omdat die op deze tekst
        # None geeft (geen kopregel, geen marker) en het antwoord dan stil zou
        # verdwijnen.
        if naam.startswith("cmd:filter channel"):
            kanalen = parse_filter_channels(waarde)
            if kanalen is not None:
                nieuw["channels"] = kanalen
            continue
        deel = parse_filter_count(waarde)
        if not deel:
            continue
        # De tabellen PER TYPE samenvoegen en niet overschrijven, ook binnen deze
        # ene push: `filter hops` en `filter rate` dragen elk een deel van
        # dezelfde regel, en een platte update zou de kolom van het antwoord dat
        # toevallig eerder langskwam wissen.
        for tabel in ("limits", "drop_types"):
            if tabel not in deel:
                continue
            samen = dict(nieuw.get(tabel) or {})
            for typenaam, waarden in deel.pop(tabel).items():
                rij = dict(samen.get(typenaam) or {})
                rij.update(waarden)
                samen[typenaam] = rij
            nieuw[tabel] = samen
        nieuw.update(deel)
    if not nieuw:
        return False

    # Samenvoegen met wat er al lag. De twee antwoorden komen soms in twee
    # pushes (een losse `filter` na een losse `filter count`), en geen van beide
    # mag de ander wissen: wat deze push niet noemt blijft staan zoals het was.
    # De stand is dus 'het laatst gemelde per veld' -- en `updated` zegt wanneer
    # er voor het laatst íets gemeld werd.
    oud = db.filter_state_for(repeater_id) or {}
    blob = dict(oud)
    blob.update(nieuw)
    # Dezelfde samenvoeging nog eens, nu met wat er al opgeslagen lag: de
    # antwoorden komen soms in twee pushes (een `filter rate` na een eerdere
    # `filter hops`), en geen van beide mag de kolom van de ander wissen.
    for tabel in ("limits", "drop_types"):
        if tabel not in nieuw:
            continue
        samen = dict(oud.get(tabel) or {})
        for naam, waarden in nieuw[tabel].items():
            rij = dict(samen.get(naam) or {})
            rij.update(waarden)
            samen[naam] = rij
        blob[tabel] = samen
    blob["variant"] = "meshcore_filter"
    db.upsert_filter_state(repeater_id, blob, source)

    # Dezelfde tellers ook als gewone metrics, want dat is wat de tegels en de
    # grafieken lezen -- maar alleen uit wat NU gemeld is: een tabel-antwoord
    # mag de oude tellers niet als vers meetpunt herhalen. De import staat in de
    # functie: mqtt_ingest trekt de halve app mee en dit bestand hoort een tabel
    # te blijven die je los kunt draaien.
    try:
        from .mqtt_ingest import _filter_metrics
        metrics = _filter_metrics(nieuw)
    except Exception:
        metrics = {}
    if metrics:
        # force=True: een sweep komt hooguit een paar keer per dag langs, en dan
        # is "de waarde veranderde niet" geen reden om het punt weg te laten --
        # anders staat er een gat in de grafiek waar wel gemeten is.
        db.ingest(repeater_id, db.utcnow(), metrics, None, force=True)
    return True
