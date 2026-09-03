"""Welke INSTELLING achter elk filtercijfer zit, als opzoektabel.

Waarom dit bestaat
------------------
De repeaterpagina toont tegels als ``WEG: PADHASH TE KLEIN -- 535``. Dat getal
is gemeten, klopt, en is toch onbruikbaar: de lezer weet niet welke instelling
het veroorzaakt, wat die instelling doet, en wat hij nu staat. Alle drie die
antwoorden bestaan al ergens -- in ``pktfilter`` (de schrijfweg), in ``pfstock``
(de tekstparser) en in de firmware-strings -- maar nergens naast de metricnaam
waar de lezer op klikt.

Deze module is dus geen nieuwe functionaliteit maar een BRUG: metricnaam in,
instelling + syntax + uitleg uit. Puur data en geen opmaak, met opzet. Een
``?``-ballon is een opmaakkeuze die vijf keer anders uitvalt op vijf schermen;
wat eronder staat mag maar op één plaats staan, anders gaat de tekst in de
ballon op de nodepagina iets anders beweren dan die op de voorpagina.

Twee varianten, en dat is de helft van het werk hier
----------------------------------------------------
Er lopen twee filterimplementaties in hetzelfde mesh (zie de kop van
``pfstock.py``). Ze delen de metricnamen -- die kiezen wij, in
``mqtt_ingest.FILTER_DROP_METRICS`` -- maar niet de commando's eronder. En op
twee plaatsen betekent dezelfde syntax bij de twee varianten het TEGENDEEL:

``filter hops <type> 0``   stock: geen limiet voor dit type.
                           meshmanager: dit type helemaal niet meer doorsturen
                           (``pktfilter.is_blanket``, ``describe``).
``filter channel``         meshmanager: een ZWARTE lijst -- ``channel add``
                           blokkeert juist dat kanaal (``pktfilter.describe``).
                           stock: welke kant de lijst op werkt is NIET bekend --
                           de firmware-strings van die build noemen alleen het
                           commando-oppervlak. Eerder stond hier "een witte
                           lijst"; die bewering is teruggetrokken omdat ze niet
                           te onderbouwen was.

Eén gedeelde uitlegtekst zou dus bij de helft van de repeaters de omgekeerde
handeling aanprijzen. Vandaar dat de tabel per variant is opgesplitst en niet
per metric met een uitzonderinglijstje: de uitzondering is hier de regel.

Wat hier NIET staat, en waarom dat expres is
--------------------------------------------
Niets wat niet uit de code of uit de firmware-strings te onderbouwen is. Waar
een variant een cijfer meldt zonder dat wij het bijbehorende commando kennen,
staat ``ondersteund: False`` met een reden in plaats van een aannemelijke gok.
Een ``?``-ballon die iets verzint over een instelling is erger dan geen ballon:
de lezer voert hem uit.
"""

from __future__ import annotations

# De twee varianten heten hier hetzelfde als in ``pfstock.variant()``. Met opzet
# als losse tekenreeks en niet geïmporteerd: dit bestand is een tabel en hoort
# geen parser mee te trekken. Wijken ze ooit af, dan valt dat om in
# test_pfhelp.py -- die vergelijkt de twee namen.
VARIANT_STOCK = "meshcore_filter"
VARIANT_MESHMANAGER = "meshmanager"
VARIANTS: tuple[str, ...] = (VARIANT_STOCK, VARIANT_MESHMANAGER)

# De pakkettypenummers zoals de firmware ze nummert. Hier omdat ze bij de UITLEG
# horen: ``filter hops 05 3`` is zonder deze tabel geen leesbaar commando, en
# wie een hoplimiet zet moet weten dat 00 en 01 zijn eigen beheerweg zijn.
TYPE_NUMBERS: dict[str, str] = {
    "00": "REQ", "01": "RESPONSE", "02": "TXT_MSG", "03": "ACK",
    "04": "ADVERT", "05": "GRP_TXT", "06": "GRP_DATA", "07": "ANON_REQ",
    "08": "PATH", "09": "TRACE", "10": "MULTIPART", "11": "CONTROL",
}

# De beheercommando's: ze zetten niets, ze lezen (of zetten alles terug). Apart
# van de subfilters omdat ze bij géén metric horen en bij alle uitleg wel: het
# antwoord op "wat staat er nu" is bij de stock-variant altijd `filter count`.
BEHEER: dict[str, dict[str, str]] = {
    VARIANT_STOCK: {
        "lezen": "filter count",
        "types": "filter types",
        "terug": "filter reset",
    },
    VARIANT_MESHMANAGER: {
        # Dezelfde drie, en daarnaast GET /api/filter over IP -- dat is de weg
        # die ``pktfilter.state()`` neemt en de enige die de regeltabellen zelf
        # teruggeeft.
        "lezen": "filter count",
        "types": "filter types",
        "terug": "filter reset",
    },
}

# Waarschuwingen die op meer dan één plaats thuishoren. Als constante en niet
# als herhaalde tekenreeks, want een waarschuwing die op twee plaatsen anders
# geformuleerd staat, wordt op de derde plaats vergeten.
# Deze tekst stond hier eerder als een LOCKOUT-WAARSCHUWING: "zet dit op 2 en u
# bent uw beheerweg over de radio kwijt". Die bewering is TERUGGETROKKEN, want ze
# was voor geen van beide varianten onderbouwd:
#
# - Voor ons eigen filter is ze aantoonbaar ONWAAR. docs/packet-filter.md zegt dat
#   het filter alleen in `MyMesh::allowPacketForward()` gevraagd wordt, en dat
#   pakketten AAN deze node (een login, een CLI-commando, een statusverzoek) geen
#   doorgestuurde pakketten zijn en er dus nooit langs komen -- plus dat verkeer
#   van of naar een client in de access list altijd doorgaat. Letterlijk: "You
#   cannot lock yourself out of a repeater with a filter rule."
# - Voor de EasySkyMesh/dutchmeshcore-build is ze ONBEKEND. Dat is een andere
#   implementatie (die heeft geen `type`-subcommando en een ander uitvoerformaat),
#   en de strings in die binary zeggen niets over welk verkeer buiten het filter
#   valt. Onbekend is iets anders dan gevaarlijk, en het als gevaar presenteren is
#   precies het soort halve waarheid dat dit project probeert te vermijden.
#
# Wat er WEL waar is, staat hieronder: het gaat over doorgestuurd flood-verkeer
# van anderen, en dat is een keuze over het mesh en niet over uw eigen toegang.
_FLOOD_ONLY_NL = (
    "Deze regel raakt het verkeer dat deze node voor ANDEREN zou doorsturen. Zet "
    "u hem strenger, dan verspreidt dit knooppunt minder van het mesh -- adverts "
    "en padopbouw van andere nodes vallen daar ook onder. Kijk dus niet alleen "
    "naar wat u wegknipt maar ook naar wat u niet meer doorgeeft."
)
_FLOOD_ONLY_EN = (
    "This rule affects traffic this node would forward for OTHERS. Tighten it and "
    "this hop spreads less of the mesh -- other nodes' adverts and path discovery "
    "included. So watch not only what you cut away, but also what you stop "
    "passing on."
)

# Ook deze stond er eerder scherper dan houdbaar: "blokkeert uw eigen
# adminfunctie". Voor ons eigen filter is dat onwaar -- verkeer AAN deze node en
# verkeer van of naar een client in de access list gaat nooit langs het filter
# (docs/packet-filter.md). Voor de andere build is het onbekend. Wat in beide
# gevallen wél waar is: 00/01 is beheerverkeer, en dat van ANDEREN loopt hier
# langs. Dat is de reden om voorzichtig te zijn, en die reden is genoeg.
_ADMIN_NL = (
    "Type 00 (REQ) en 01 (RESPONSE) is beheerverkeer. Een regel daarop knipt het "
    "beheerverkeer weg dat deze node voor ANDERE nodes zou doorsturen -- dan kan "
    "iemand anders zijn eigen repeater niet meer bereiken via dit knooppunt. Uw "
    "eigen toegang tot DEZE node loopt er niet langs."
)
_ADMIN_EN = (
    "Types 00 (REQ) and 01 (RESPONSE) are admin traffic. A rule on those cuts the "
    "admin traffic this node would forward for OTHER nodes -- someone else may "
    "then be unable to reach their own repeater through this hop. Your own access "
    "to THIS node does not pass through the filter."
)

_REBOOT_NL = (
    "De filterconfiguratie van deze variant overleefde in de praktijk een reboot "
    "NIET. Controleer met `filter count` ná een herstart of uw regels er nog "
    "staan."
)
_REBOOT_EN = (
    "In practice this variant's filter configuration did NOT survive a reboot. "
    "Verify with `filter count` after a restart that your rules are still there."
)


def _regel(**velden) -> dict:
    """Eén subfilterregel met alle velden gevuld.

    Bestaat zodat een template nooit op een ontbrekende sleutel stuit -- een
    pagina die niet rendert omdat één variant geen waarschuwing heeft, is een
    slechtere pagina dan één zonder waarschuwing. De verplichte velden staan
    hier dus als lege tekenreeks in de standaard en niet als afwezige sleutel;
    dat ze gevuld ZIJN is een test en geen aanname.
    """
    basis: dict = {
        # Bij welke instelling hoort dit -- de kern van deze module.
        "instelling": "",
        "instelling_en": "",
        "syntax": "",
        "bereik": "",
        # Wat de instelling doet, in één zin, in beide talen.
        "doet_nl": "",
        "doet_en": "",
        # Werkt deze regel alleen als de hoofdschakelaar aanstaat?
        "vereist": "",
        # Hoe je de HUIDIGE waarde uitleest over de CLI.
        "leescommando": "",
        # Waar de huidige waarde in de OPGESLAGEN blob staat
        # (``db.filter_state_for`` / ``mqtt_ingest._handle_filter``). Leeg tupel
        # = de blob draagt hem niet; dan is ``leescommando`` de enige weg.
        "waarde_pad": (),
        "waarde_vorm": "",      # bool | getal | per_type
        "waarde_veld": "",      # bij per_type: het veld binnen elke typerij
        # Waar hij in het LIVE antwoord van ``pktfilter.state()`` staat, als dat
        # rijker is dan de blob.
        "live_pad": (),
        "waarschuwing_nl": "",
        "waarschuwing_en": "",
        # Kennen wij een instelling met naam en syntax voor deze variant?
        "ondersteund": True,
        "niet_nl": "",
        "niet_en": "",
    }
    basis.update(velden)
    return basis


# --- de subfilters per variant ------------------------------------------------
#
# De sleutels zijn subfilters en niet metricnamen, want één subfilter draagt soms
# drie cijfers (de snelheidslimiet draagt een dropteller én twee drukreeksen) en
# één cijfer hoort altijd bij precies één subfilter. ``_METRICS`` hieronder legt
# de verbinding.

_SUBFILTERS: dict[str, dict[str, dict]] = {
    VARIANT_STOCK: {
        "hoofdschakelaar": _regel(
            instelling="hoofdschakelaar van het filter",
            instelling_en="filter master switch",
            syntax="filter on / filter off",
            doet_nl=("Zet het hele filter aan of uit. Staat hij uit, dan doet "
                     "GEEN ENKEL subfilter iets -- de regels blijven staan maar "
                     "er wordt niets weggegooid."),
            doet_en=("Turns the whole filter on or off. While off, NO subfilter "
                     "does anything -- the rules stay but nothing is dropped."),
            leescommando="filter count",
            waarde_pad=("on",),
            waarde_vorm="bool",
            waarschuwing_nl=_REBOOT_NL,
            waarschuwing_en=_REBOOT_EN,
        ),
        "hops": _regel(
            instelling="hoplimiet per pakkettype",
            instelling_en="hop limit per packet type",
            syntax="filter hops <type> <max_hops>",
            bereik="type 0-10, max_hops 0-64; 0 = geen limiet",
            doet_nl=("Gooit pakketten van dat type weg die MEER hops aflegden "
                     "dan max_hops. 0 betekent hier GEEN limiet -- niet "
                     "'alles weg'."),
            doet_en=("Drops packets of that type which travelled MORE hops than "
                     "max_hops. Here 0 means NO limit -- not 'drop everything'."),
            vereist="filter on",
            leescommando="filter count",
            # `filter count` toont onder [TYPE: HOPS,RATE] per type de INGESTELDE
            # limieten; ``pfstock.parse_filter_count`` zet die in ``limits``.
            waarde_pad=("limits",),
            waarde_vorm="per_type",
            waarde_veld="hops",
            waarschuwing_nl=_ADMIN_NL,
            waarschuwing_en=_ADMIN_EN,
        ),
        "rate": _regel(
            instelling="snelheidslimiet per pakkettype",
            instelling_en="rate limit per packet type",
            syntax="filter rate <type> <limit> <secs>",
            bereik="type 0-10; limit 0 = geen limiet",
            doet_nl=("Gooit pakketten van dat type weg boven <limit> stuks per "
                     "<secs> seconden."),
            doet_en=("Drops packets of that type above <limit> per <secs> "
                     "seconds."),
            vereist="filter on",
            leescommando="filter count",
            waarde_pad=("limits",),
            waarde_vorm="per_type",
            waarde_veld="rate",
            waarschuwing_nl=_ADMIN_NL,
            waarschuwing_en=_ADMIN_EN,
        ),
        "channel": _regel(
            instelling="kanaallijst",
            instelling_en="channel list",
            syntax="filter channel [list|add|remove] <#naam|Public>",
            # Bereik NIET overgenomen van onze eigen firmware: de strings van
            # deze build noemen geen maximum. Zestien is wat ONS filter aanhoudt
            # en dat hier neerzetten zou een gok met een getal erbij zijn.
            bereik="",
            # Hier stond eerder dat dit een WITTE lijst is (verkeer buiten de
            # lijst sneuvelt), tegenover een zwarte bij onze eigen firmware. Die
            # bewering is teruggetrokken: ze was niet te onderbouwen. Alles wat de
            # binary van deze build hierover zegt is het commando-oppervlak
            # (`channel [list|add|remove]`) en de bevestigingen "channel %s
            # added/removed" -- niets over welke kant de lijst op werkt. Wie dat
            # wil weten leest `filter channel list` op de node zelf.
            doet_nl=("Filtert groepstekst op de kanalen in deze lijst. Welke kant "
                     "de lijst op werkt -- doorlaten of blokkeren -- staat niet in "
                     "de firmware-strings van deze build; controleer het met "
                     "`filter channel list` op de node voordat u erop vertrouwt."),
            doet_en=("Filters group text on the channels in this list. Which way "
                     "the list works -- pass or block -- is not stated in this "
                     "build's firmware strings; check with `filter channel list` "
                     "on the node before relying on it."),
            vereist="filter on",
            leescommando="filter channel list",
            # `filter count` vertelt niet hoeveel kanaalregels er staan, alleen
            # hoeveel pakketten erop sneuvelden -- zie parse_filter_count.
            waarde_pad=(),
            waarschuwing_nl=("De lijst staat niet in het opgeslagen blok; alleen "
                             "`filter channel list` op de node zelf vertelt wat "
                             "er nu doorgelaten wordt."),
            waarschuwing_en=("The list is not in the stored blob; only `filter "
                             "channel list` on the node itself tells you what "
                             "currently passes."),
        ),
        "hash": _regel(
            instelling="minimale padhashlengte",
            instelling_en="minimum path hash length",
            syntax="filter hash <min_bytes>",
            bereik="1-3 (1 = alles door)",
            doet_nl=("Gooit pakketten weg met een padhash KLEINER dan "
                     "min_bytes."),
            doet_en="Drops packets with a path hash SMALLER than min_bytes.",
            vereist="filter on",
            leescommando="filter count",
            # `filter count` meldt `Hash: <teller>` -- de WEGGEGOOIDE aantallen,
            # niet de ingestelde min_bytes. De ingestelde waarde is uit dit
            # antwoord niet te halen.
            waarde_pad=(),
            waarschuwing_nl=_FLOOD_ONLY_NL,
            waarschuwing_en=_FLOOD_ONLY_EN,
        ),
        "malformed": _regel(
            instelling="controle op misvormde tekst",
            instelling_en="malformed text check",
            syntax="filter malformed <on|off>",
            doet_nl=("Gooit pakketten weg waarvan de tekstinhoud als beschadigd "
                     "herkend wordt."),
            doet_en=("Drops packets whose text content is recognised as "
                     "corrupted."),
            vereist="filter on",
            leescommando="filter count",
            # Idem: `Malformed: <teller>` is een teller, niet de aan/uit-stand.
            waarde_pad=(),
        ),
        "type": _regel(
            ondersteund=False,
            instelling="(bestaat niet in deze variant)",
            instelling_en="(does not exist in this variant)",
            niet_nl=("Deze variant kan een pakkettype niet in één klap "
                     "dichtzetten en telt er dus ook niets voor. Het strengste "
                     "wat hier kan is `filter rate <type> 1 <secs>` of een lage "
                     "hoplimiet."),
            niet_en=("This variant cannot shut a packet type completely and "
                     "therefore counts nothing for it. The strictest option "
                     "here is `filter rate <type> 1 <secs>` or a low hop "
                     "limit."),
        ),
        "acl": _regel(
            ondersteund=False,
            instelling="(geen vrijstellingslijst in deze variant)",
            instelling_en="(no exemption list in this variant)",
            niet_nl=("Deze variant kent geen vrijstellingen en meldt de teller "
                     "niet."),
            niet_en="This variant has no exemptions and does not report the counter.",
        ),
    },

    VARIANT_MESHMANAGER: {
        "hoofdschakelaar": _regel(
            instelling="hoofdschakelaar van het filter",
            instelling_en="filter master switch",
            syntax="filter on / filter off",
            doet_nl=("Zet het hele filter aan of uit. Staat hij uit, dan doet "
                     "GEEN ENKEL subfilter iets. Let op: `filter on` terwijl er "
                     "al een categorale regel klaarstaat, is de klik die dat "
                     "verkeer stilzet."),
            doet_en=("Turns the whole filter on or off. While off, NO subfilter "
                     "does anything. Note: `filter on` while a blanket rule is "
                     "already staged is the click that silences that traffic."),
            leescommando="filter count",
            waarde_pad=("on",),
            waarde_vorm="bool",
            live_pad=("on",),
            waarschuwing_nl=("Deze firmware kan het filter na herhaalde "
                             "herstarts zélf uit laten staan (veilige modus, "
                             "veld `disarmed`). De regels staan er dan nog en "
                             "komen bij een schone start terug."),
            waarschuwing_en=("This firmware may leave the filter off by itself "
                             "after repeated restarts (safe mode, field "
                             "`disarmed`). The rules remain and return after a "
                             "clean start."),
        ),
        "hops": _regel(
            instelling="hoplimiet per pakkettype",
            instelling_en="hop limit per packet type",
            syntax="filter hops <type> <max_hops>",
            # Het bereik van deze variant is niet uit deze codebase vast te
            # stellen: pktfilter weigert bewust geen grenzen ("de firmware is de
            # baas"). Leeg laten in plaats van het stock-bereik overnemen.
            bereik="",
            doet_nl=("Laat dat pakkettype nog hoogstens max_hops hops reizen. "
                     "0 betekent hier: dit type HELEMAAL niet meer doorsturen "
                     "-- omgekeerd aan de stock-variant."),
            doet_en=("Lets that packet type travel at most max_hops hops. Here "
                     "0 means: stop forwarding this type ENTIRELY -- the "
                     "opposite of the stock variant."),
            vereist="filter on",
            leescommando="filter count",
            # De hoplimiettabel gaat NIET mee in de opgeslagen blob (zie
            # ``mqtt_ingest._handle_filter``); alleen het live antwoord van
            # /api/filter draagt hem, als ``types[i]["hops"]``.
            waarde_pad=(),
            live_pad=("types",),
            waarschuwing_nl=_ADMIN_NL,
            waarschuwing_en=_ADMIN_EN,
        ),
        "rate": _regel(
            instelling="snelheidslimiet per pakkettype",
            instelling_en="rate limit per packet type",
            syntax="filter rate <type> <limit> <secs>",
            bereik="limit 0 = de limiet op dit type opheffen",
            doet_nl=("Gooit pakketten van dat type weg boven <limit> stuks per "
                     "<secs> seconden."),
            doet_en=("Drops packets of that type above <limit> per <secs> "
                     "seconds."),
            vereist="filter on",
            leescommando="filter count",
            # ``stats.rate[type]["lim"]`` is de INGESTELDE limiet; die staat wel
            # in de blob, achter een login (zie ``pktfilter.breakdown``).
            waarde_pad=("stats", "rate"),
            waarde_vorm="per_type",
            waarde_veld="lim",
            waarschuwing_nl=_ADMIN_NL,
            waarschuwing_en=_ADMIN_EN,
        ),
        "channel": _regel(
            instelling="geblokkeerde kanalen (zwarte lijst)",
            instelling_en="blocked channels (block-list)",
            syntax="filter channel [list|add|remove] <#naam|Public>",
            bereik="hoogstens 16 kanalen",
            doet_nl=("`channel add` BLOKKEERT juist dat kanaal voor "
                     "groepstekst; alles wat niet op de lijst staat gaat door. "
                     "Andersom dan bij de stock-variant."),
            doet_en=("`channel add` BLOCKS that channel for group text; "
                     "anything not listed passes. The opposite of the stock "
                     "variant."),
            vereist="filter on",
            leescommando="filter channel list",
            # De blob draagt het AANTAL kanaalregels, niet de lijst; de labels en
            # treffers staan in ``stats.chan`` en die zijn beheerdersgereedschap.
            waarde_pad=("channels",),
            waarde_vorm="getal",
            live_pad=("channels",),
        ),
        "hash": _regel(
            instelling="minimale padhashlengte",
            instelling_en="minimum path hash length",
            syntax="filter hash <min_bytes>",
            bereik="1-3 (1 = alles door)",
            doet_nl=("Stuurt alleen pakketten door met een padhash van minstens "
                     "min_bytes byte."),
            doet_en=("Only forwards packets with a path hash of at least "
                     "min_bytes bytes."),
            vereist="filter on",
            leescommando="filter count",
            waarde_pad=("hash",),
            waarde_vorm="getal",
            live_pad=("hash",),
            waarschuwing_nl=_FLOOD_ONLY_NL,
            waarschuwing_en=_FLOOD_ONLY_EN,
        ),
        "malformed": _regel(
            instelling="controle op misvormde groepstekst",
            instelling_en="malformed group text check",
            syntax="filter malformed <on|off>",
            doet_nl=("Gooit groepstekst met een onmogelijke structuur weg."),
            doet_en="Drops group text with an impossible structure.",
            vereist="filter on",
            leescommando="filter count",
            waarde_pad=("malformed",),
            waarde_vorm="bool",
            live_pad=("malformed",),
        ),
        "type": _regel(
            instelling="pakkettype helemaal dicht",
            instelling_en="packet type fully closed",
            syntax="filter type <type> <on|off>",
            doet_nl=("`off` stuurt dat pakkettype HELEMAAL niet meer door -- "
                     "geen limiet maar een dichte deur. Dit is de zwaarste "
                     "filterhandeling die deze firmware kent."),
            doet_en=("`off` stops forwarding that packet type ENTIRELY -- not a "
                     "limit but a closed door. The heaviest filter action this "
                     "firmware offers."),
            vereist="filter on",
            leescommando="filter types",
            # De blob draagt het AANTAL dichtgezette types; welke types dat zijn
            # staat alleen in het live antwoord (``types[i]["on"]``).
            waarde_pad=("blocked_types",),
            waarde_vorm="getal",
            live_pad=("types",),
            waarschuwing_nl=_ADMIN_NL,
            waarschuwing_en=_ADMIN_EN,
        ),
        "acl": _regel(
            ondersteund=False,
            instelling="vrijstellingslijst (ACL)",
            instelling_en="exemption list (ACL)",
            niet_nl=("Deze firmware MELDT de vrijstellingsteller, maar in deze "
                     "codebase staat geen filtercommando dat de "
                     "vrijstellingslijst zet. Wat de lijst vult is hier niet te "
                     "onderbouwen, dus staat er geen syntax."),
            niet_en=("This firmware DOES report the exemption counter, but this "
                     "codebase contains no filter command that sets the "
                     "exemption list. What fills the list cannot be "
                     "substantiated here, so no syntax is given."),
        ),
    },
}

# Voor een node waarvan we de variant nog niet weten. Niet hetzelfde als 'geen
# filter', om dezelfde reden als de vier toestanden in ``pfstock.variant()``:
# 'we weten het nog niet' is geen bewering.
_ONBEKENDE_VARIANT: dict = _regel(
    ondersteund=False,
    instelling="(variant onbekend)",
    instelling_en="(variant unknown)",
    niet_nl=("We weten nog niet welke filterimplementatie deze node draait, dus "
             "ook niet hoe de instelling achter dit cijfer heet."),
    niet_en=("We do not yet know which filter implementation this node runs, so "
             "we cannot name the setting behind this number either."),
)


# --- de metrics ---------------------------------------------------------------
#
# ``soort`` zegt wat voor getal het is, en dat is geen sier: een dropteller en
# een drukreeks lezen anders. Een dropteller die oploopt is verkeer dat weg is;
# een drukreeks die oploopt is een limiet die de kans kreeg om te bijten.

_METRICS: dict[str, dict] = {
    "filter_on": {
        "subfilter": "hoofdschakelaar",
        "soort": "schakelaar",
        "meting_nl": ("1 zolang het filter aanstond, 0 zolang het uitstond. Als "
                      "reeks, zodat 'wanneer stond dit filter aan' een antwoord "
                      "heeft en geen gok op basis van wanneer er weer iets "
                      "weggegooid werd."),
        "meting_en": ("1 while the filter was on, 0 while it was off. Kept as a "
                      "series so that 'when was this filter on' has an answer "
                      "instead of being guessed from when drops resumed."),
    },
    "filter_dropped": {
        "subfilter": "hoofdschakelaar",
        "soort": "totaal",
        "meting_nl": ("Alle weggegooide pakketten bij elkaar: de som van de "
                      "droptellers per reden. Het cijfer om te zien DAT er "
                      "gefilterd wordt; de reden staat in de tegels ernaast."),
        "meting_en": ("All dropped packets together: the sum of the per-reason "
                      "drop counters. The number that shows filtering IS "
                      "happening; the reason is in the neighbouring tiles."),
    },
    "filter_passed": {
        "subfilter": "hoofdschakelaar",
        "soort": "doorlaatteller",
        "meting_nl": ("Pakketten die het filter langs alle regels doorliet. "
                      "Alleen samen met `filter_dropped` bruikbaar: 500 weg is "
                      "veel bij 600 door en weinig bij 600.000 door."),
        "meting_en": ("Packets the filter let through past every rule. Only "
                      "useful next to `filter_dropped`: 500 dropped is a lot "
                      "against 600 passed and little against 600,000."),
    },
    "filter_exempt": {
        "subfilter": "acl",
        "soort": "doorlaatteller",
        "meting_nl": ("Pakketten die de regels helemaal oversloegen omdat ze "
                      "vrijgesteld waren. Loopt dit op terwijl u niets "
                      "vrijstelde, dan filtert deze node minder dan u denkt."),
        "meting_en": ("Packets that skipped the rules entirely because they were "
                      "exempt. If this climbs while you exempted nothing, this "
                      "node filters less than you think."),
    },
    "filter_drop_hops": {
        "subfilter": "hops",
        "soort": "dropteller",
        "meting_nl": "Pakketten weggegooid omdat ze te veel hops hadden afgelegd.",
        "meting_en": "Packets dropped for having travelled too many hops.",
    },
    "filter_drop_rate": {
        "subfilter": "rate",
        "soort": "dropteller",
        "meting_nl": ("Pakketten weggegooid omdat de snelheidslimiet van hun "
                      "type vol zat."),
        "meting_en": ("Packets dropped because their type's rate limit was "
                      "full."),
    },
    "filter_drop_type": {
        "subfilter": "type",
        "soort": "dropteller",
        "meting_nl": ("Pakketten weggegooid omdat hun hele pakkettype "
                      "dichtstaat. Loopt dit op, dan is er geen limiet geraakt "
                      "maar een deur dicht."),
        "meting_en": ("Packets dropped because their whole packet type is shut. "
                      "If this climbs, no limit was hit -- a door is closed."),
    },
    "filter_drop_hash": {
        "subfilter": "hash",
        "soort": "dropteller",
        "meting_nl": ("Pakketten weggegooid omdat hun padhash korter was dan de "
                      "ingestelde minimumlengte."),
        "meting_en": ("Packets dropped because their path hash was shorter than "
                      "the configured minimum."),
    },
    "filter_drop_channel": {
        "subfilter": "channel",
        "soort": "dropteller",
        "meting_nl": ("Kanaalverkeer weggegooid door de kanaallijst. Kijk goed "
                      "welke kant die lijst op werkt bij deze variant."),
        "meting_en": ("Channel traffic dropped by the channel list. Check which "
                      "way that list works on this variant."),
    },
    "filter_drop_malformed": {
        "subfilter": "malformed",
        "soort": "dropteller",
        "meting_nl": ("Pakketten weggegooid omdat hun tekstinhoud als beschadigd "
                      "herkend werd."),
        "meting_en": ("Packets dropped because their text content was "
                      "recognised as corrupted."),
    },
    "filter_rate_windows": {
        "subfilter": "rate",
        "soort": "druk",
        "verhouding_met": "filter_rate_capped",
        "meting_nl": ("GEEN dropteller. Het aantal tijdvensters waarin er "
                      "verkeer van dit soort langskwam -- dus hoe vaak de "
                      "limiet de KANS had om te bijten. De noemer van de "
                      "verhouding hieronder."),
        "meting_en": ("NOT a drop counter. The number of time windows in which "
                      "such traffic passed -- i.e. how often the limit had the "
                      "CHANCE to bite. The denominator of the ratio below."),
    },
    "filter_rate_capped": {
        "subfilter": "rate",
        "soort": "druk",
        "verhouding_met": "filter_rate_windows",
        "meting_nl": ("GEEN dropteller. Het aantal vensters waarin de limiet "
                      "werkelijk BEET. Deel dit door "
                      "`filter_rate_windows` en u hebt het cijfer waarmee u een "
                      "limiet bijstelt: 12 van 4000 vensters is een limiet die "
                      "af en toe een uitschieter afvlakt en verder niets doet; "
                      "3900 van 4000 is een limiet die de normale gang van "
                      "zaken is geworden en dus te laag staat -- terwijl het "
                      "aantal weggegooide pakketten in beide gevallen "
                      "hetzelfde kan zijn. Dáárom staan deze twee naast de "
                      "dropteller en niet in plaats van hem."),
        "meting_en": ("NOT a drop counter. The number of windows in which the "
                      "limit actually BIT. Divide this by "
                      "`filter_rate_windows` and you have the number you tune a "
                      "limit with: 12 out of 4000 windows is a limit that "
                      "occasionally shaves a spike and otherwise does nothing; "
                      "3900 out of 4000 is a limit that has become the normal "
                      "state of affairs and is therefore set too low -- while "
                      "the dropped-packet count can be identical in both cases. "
                      "That is why these two sit NEXT TO the drop counter "
                      "rather than instead of it."),
    },
}

# Wat een variant niet MELDT. Los van ``ondersteund``, want dat zijn twee
# verschillende lege vakken: 'deze firmware kan die instelling niet' is iets
# anders dan 'deze firmware kan het wel maar vertelt het cijfer niet', en een
# pagina die die twee door elkaar haalt laat de lezer naar een instelling zoeken
# die er is. Afgeleid van ``pfstock.capabilities()``.
_NIET_GEMELD: dict[str, tuple[str, ...]] = {
    VARIANT_STOCK: (
        "filter_passed",
        "filter_exempt",
        "filter_drop_type",
        "filter_rate_windows",
        "filter_rate_capped",
    ),
    VARIANT_MESHMANAGER: (),
}


# --- opvragen -----------------------------------------------------------------

def metric_names() -> tuple[str, ...]:
    """De metricnamen waar deze module iets over te zeggen heeft."""
    return tuple(_METRICS)


def subfilter_of(metric: str) -> str:
    """Bij welk subfilter hoort deze metric? Lege string bij een onbekende naam."""
    regel = _METRICS.get(metric)
    return str(regel["subfilter"]) if regel else ""


def help_for(metric: str, variant: str = VARIANT_STOCK) -> dict | None:
    """Alles wat er bij één filtercijfer te vertellen valt, als platte dict.

    ``None`` bij een metric die hier niet in staat -- dat is een echte fout in de
    aanroeper en hoort niet als lege ballon te eindigen. Een ONBEKENDE VARIANT
    levert wél een dict, met ``ondersteund: False`` en een reden: dat er een
    nieuwe firmwarevariant in het mesh hangt, mag geen pagina laten struikelen.

    Platte dict en geen geneste, omdat een template dit met puntnotatie moet
    kunnen lezen zonder te weten dat de tabel eronder per variant gesplitst is.
    Een KOPIE, zodat een aanroeper die er iets in zet de tabel niet vergiftigt.
    """
    regel = _METRICS.get(metric)
    if regel is None:
        return None

    sub = str(regel["subfilter"])
    per_variant = _SUBFILTERS.get(variant, {}).get(sub, _ONBEKENDE_VARIANT)

    uit: dict = dict(per_variant)
    uit.update({
        "metric": metric,
        "subfilter": sub,
        "soort": regel["soort"],
        "meting_nl": regel["meting_nl"],
        "meting_en": regel["meting_en"],
        "verhouding_met": regel.get("verhouding_met", ""),
        "variant": variant,
        # Meldt deze variant dit cijfer überhaupt? Bij een variant die we niet
        # kennen is het antwoord 'onbekend' en niet 'nee'; False zou beweren dat
        # een leeg vak een eigenschap is.
        "gemeld": (metric not in _NIET_GEMELD[variant]
                   if variant in _NIET_GEMELD else True),
        "beheer": dict(BEHEER.get(variant, {})),
    })
    return uit


def all_help(variant: str = VARIANT_STOCK) -> dict[str, dict]:
    """Dezelfde tabel voor alle metrics tegelijk, in de volgorde van ``_METRICS``.

    Voor het geval dat de pagina alle tegels in één keer opbouwt: twaalf losse
    aanroepen per pagina is geen probleem, maar twaalf losse aanroepen per tegel
    in een lus over repeaters wel.
    """
    return {naam: help_for(naam, variant) or {} for naam in _METRICS}


def current_value(state: dict | None, metric: str,
                  variant: str = VARIANT_STOCK) -> dict:
    """De HUIDIGE stand van de instelling achter dit cijfer, uit de blob.

    ``state`` is de opgeslagen filterstand (``db.filter_state_for``, of wat
    ``pfstock.parse_filter_count`` oplevert). Het antwoord is bewust drieledig en
    niet 'de waarde of None', want die drie zijn op een pagina drie andere
    zinnen:

    ``bekend`` én een waarde   dit staat er nu.
    ``bekend`` False met een
    reden in ``waarom``        deze variant draagt die waarde niet in de blob;
                               ``leescommando`` is dan de enige weg.
    ``bekend`` False zonder
    waarde maar met een pad    de blob KAN hem dragen, maar deze node zei nog
                               niets.

    Dat laatste onderscheid is dezelfde regel als in ``pfstock``: ontbrekend is
    niet nul. Een nul die wij hier verzinnen ziet er op het scherm precies zo uit
    als een gemeten nul en betekent iets anders.
    """
    hulp = help_for(metric, variant)
    uit: dict = {"bekend": False, "waarde": None, "pad": "", "waarom": ""}
    if hulp is None:
        uit["waarom"] = f"onbekende metric: {metric}"
        return uit

    pad = tuple(hulp.get("waarde_pad") or ())
    vorm = str(hulp.get("waarde_vorm") or "")
    uit["pad"] = ".".join(pad)
    if not pad or not vorm:
        uit["waarom"] = ("de opgeslagen filterstand draagt deze instelling niet; "
                         f"lees hem met `{hulp.get('leescommando') or 'filter count'}`")
        return uit
    if not isinstance(state, dict):
        uit["waarom"] = "geen filterstand bekend voor deze node"
        return uit

    knoop = state
    for sleutel in pad:
        if not isinstance(knoop, dict) or sleutel not in knoop:
            uit["waarom"] = "deze node heeft dit nog niet gemeld"
            return uit
        knoop = knoop[sleutel]

    if vorm == "bool":
        uit.update(bekend=True, waarde=bool(knoop))
    elif vorm == "getal":
        # Geen int() eromheen: wat er staat is wat de node meldde, en een
        # onverwachte vorm hoort zichtbaar te blijven in plaats van stil een 0 te
        # worden.
        uit.update(bekend=isinstance(knoop, (int, float)) and not isinstance(knoop, bool),
                   waarde=knoop)
        if not uit["bekend"]:
            uit["waarom"] = "de gemelde waarde is geen getal"
    elif vorm == "per_type":
        veld = str(hulp.get("waarde_veld") or "")
        if not isinstance(knoop, dict):
            uit["waarom"] = "de gemelde tabel heeft niet de verwachte vorm"
            return uit
        tabel = {naam: rij.get(veld) for naam, rij in knoop.items()
                 if isinstance(rij, dict) and veld in rij}
        uit.update(bekend=bool(tabel), waarde=tabel)
        if not tabel:
            uit["waarom"] = "deze node heeft nog geen limieten gemeld"
    return uit


def tooltip(metric: str, variant: str = VARIANT_STOCK, lang: str = "nl") -> str:
    """De tekst achter het '?' bij een filtertegel, in één samenstelling.

    Twee dingen horen hier samen en niet los. ``meting_*`` zegt wat dit GETAL
    betekent -- en dat is per metric echt anders: ``filter_rate_capped`` is geen
    dropteller maar de helft van een verhouding. ``doet_*`` zegt wat de REGEL
    doet die dat getal veroorzaakt. Wie alleen het eerste leest weet niet waar
    hij moet draaien; wie alleen het tweede leest denkt bij "vensters waarin de
    limiet beet" aan weggegooide pakketten.

    Waarom dit een functie is en geen samenvoeging in de template of in de
    i18n-generator: die twee zouden uit elkaar lopen. De pagina zet de
    Nederlandse tekst inline als terugval en de sleutel ernaast; wijkt de
    gegenereerde vertaling af van de inline tekst, dan verspringt het tooltip
    zodra i18n.js langskomt. Één bron dus, en beide kanten lezen hem hier.
    """
    hulp = help_for(metric, variant)
    if not hulp:
        return ""
    meting = (hulp.get("meting_nl" if lang == "nl" else "meting_en") or "").strip()
    doet = (hulp.get("doet_nl" if lang == "nl" else "doet_en") or "").strip()
    if not hulp.get("ondersteund"):
        # Niet-ondersteund: dan is 'wat de regel doet' een verzinsel en staat er
        # in niet_* juist waarom die er niet is. Die reden is hier het antwoord.
        niet = (hulp.get("niet_nl" if lang == "nl" else "niet_en") or "").strip()
        return " ".join(p for p in (meting, niet) if p)
    if doet and doet != meting:
        voor = "Regel: " if lang == "nl" else "Rule: "
        return f"{meting} {voor}{doet}".strip()
    return meting
