"""Het pakketfilter van een repeater lezen en zetten vanaf de beheerpagina.

Waarom dit náást ``nodeconfig`` staat en er niet in
---------------------------------------------------
``nodeconfig`` schrijft één CLI-instelling: een sleutel, een waarde, een type,
grenzen, een risicoklasse. Dat model past op de achtentwintig parameters die de
firmware aanbiedt, en het past goed -- de node levert de lijst, de pagina bouwt
er een formulier van, en er is geen tweede lijst die kan gaan afwijken.

Een filterstand is geen sleutel/waardepaar. Het zijn drie tabellen van twaalf
(hoplimiet, snelheidslimiet, aan/uit per pakkettype), een lijst van maximaal
zestien geblokkeerde kanalen, en twee losse instellingen. Dat door de bestaande
schrijfweg persen zou sleutels opleveren als ``filter.rate.05.limit``. Dan staat
de grammatica van het filter op drie plaatsen -- hier, in de firmware, en in de
parser die de firmware er toch omheen moet bouwen -- en op de dag dat iemand een
regel toevoegt, denken die drie niet meer hetzelfde.

Dus een eigen weg, met de rest van het patroon intact:

- de **firmware is de baas**. De node krijgt een commandoregel en antwoordt met
  wat er ná afloop in staat. Deze module verzint geen grenzen; hij weigert
  hoogstens iets wat overduidelijk geen commando is, om een netwerkronde te
  besparen.
- **teruglezen in plaats van "OK" geloven**, om precies de reden die in
  ``nodeconfig.write`` staat uitgeschreven.
- **drie risicoklassen**, dezelfde drie, met dezelfde bevestigingen.

En één ding dat hier zwaarder weegt dan bij een gewone instelling: **de weg
terug is de goedkoopste handeling.** ``off`` en ``reset`` vallen in de lichtste
klasse, lichter dan ``on``. Een rol die een filter niet aan mag zetten mag er
wel een uitzetten. Dat is geen slordigheid in de indeling maar het punt ervan:
herstel mag nooit strakker afgeschermd zijn dan de fout die het terugdraait, en
een filter is de instelling waarbij die fout er van buitenaf uitziet als een
gezonde node.

De echte terugvalweg loopt trouwens niet langs deze module. ``filter off`` en
``filter reset`` werken over de mesh-CLI, zonder WiFi, zonder deze site. Zie
``docs/packet-filter.md``. Wat hier staat is het gemak; dat daar is het vangnet.
"""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.parse

from . import commanding, firmware, nodeconfig

# De firmware die /api/filter kent. Lager en het endpoint bestaat niet -- dan
# antwoordt de node met 404, en dat hoort de pagina te zeggen in plaats van een
# knop aan te bieden die niets doet.
MIN_FILTER_VERSION = (2, 3, 0)

FILTER_TIMEOUT_S = 10

# De pagina wacht korter dan de schrijfweg, en dat is geen inconsistentie maar
# het verschil tussen een gemak en een besluit. Bij het openen van een nodepagina
# is de actuele stand een extraatje naast wat er al uit het laatste
# statistiekenbericht bekend is; een node die niet antwoordt mag die pagina niet
# tien seconden ophouden. Vóór een schrijfactie is diezelfde stand de basis
# waarop de risicoklasse gewogen wordt, en dan is wachten het goedkoopste wat er
# is.
FILTER_PEEK_TIMEOUT_S = 3

# Dezelfde drie klassen als bij de CLI-instellingen, en met opzet dezelfde
# getallen: de bevestiging die eraan hangt is dezelfde functie.
RISK_PLAIN = nodeconfig.RISK_PLAIN
RISK_WRITES = nodeconfig.RISK_WRITES
RISK_CUTOFF = nodeconfig.RISK_CUTOFF

# De pakkettypes zoals de firmware ze nummert. Hier alleen om een formulier te
# kunnen tekenen en een commando leesbaar te maken -- de node stuurt zijn eigen
# lijst mee in GET /api/filter, en dat is de lijst die telt.
TYPE_NAMES = ("REQ", "RESPONSE", "TXT_MSG", "ACK", "ADVERT", "GRP_TXT",
              "GRP_DATA", "ANON_REQ", "PATH", "TRACE", "MULTIPART", "CONTROL")

DROP_LABELS = {
    "type": "type helemaal dicht",
    "hops": "te veel hops",
    "rate": "over de snelheidslimiet",
    "hash": "padhash te klein",
    "kanaal": "geblokkeerd kanaal",
    "misvormd": "misvormde groepstekst",
}


def _field(row, key, default=None):
    return firmware._field(row, key, default)


# --- kan er naar dit filter geschreven worden --------------------------------

def filter_route(rep) -> dict:
    """Mag en kan de site het filter van deze repeater lezen en zetten?

    Eigen sleutel naast ``nodeconfig.cfg_route``, en om dezelfde reden als die
    naast ``commanding.route_for`` staat: ze reizen over verschillende dingen.
    Een node kan /api/cfg kennen en /api/filter niet -- dat is precies het geval
    tussen 2.1.0 en 2.3.0 -- en dan is 'u kunt instellingen schrijven' waar en
    'u kunt het filter zetten' onwaar. Eén gedeelde sleutel zou een van die twee
    knoppen laten liegen.
    """
    host = (_field(rep, "ota_host") or "").strip()
    fw = _field(rep, "fw_meshmanager") or ""
    version = commanding.parse_version(fw)
    relayed = commanding.is_relayed(rep)

    out = {"can": False, "blocker": "", "host": host, "fw": fw,
           "min_fw": ".".join(str(n) for n in MIN_FILTER_VERSION), "relayed": relayed}

    if relayed:
        # Blijvende toestand, geen ontbrekende instelling: deze node draait geen
        # firmware van ons en heeft geen IP-pad. Voor hem is de mesh-CLI de weg,
        # en die loopt niet langs deze module.
        out["blocker"] = "relayed_only"
    elif not firmware.NODE_USER:
        out["blocker"] = "no_credentials"
    elif not host:
        out["blocker"] = "no_host"
    elif version is None:
        out["blocker"] = "no_fw"
    elif version < MIN_FILTER_VERSION:
        out["blocker"] = "old_fw"
    else:
        out["can"] = True
    return out


# --- de node ------------------------------------------------------------------

def state(host: str, timeout: int = FILTER_TIMEOUT_S) -> dict:
    """De volledige filterstand zoals de node hem nu kent.

    Rechtstreeks van de node en niet uit onze eigen tabel, want die tabel houdt
    een momentopname uit het laatste statistiekenbericht en dat kan minuten oud
    zijn. Wie op het punt staat een regel te wijzigen, hoort te zien wat er nu
    staat -- en of de node überhaupt antwoordt.
    """
    out = {"ok": False, "error": "", "filter": {}}
    if not (host or "").strip():
        out["error"] = "geen beheeradres"
        return out
    try:
        with nodeconfig._open(host, "/api/filter", timeout=timeout) as resp:
            out["filter"] = json.loads(resp.read())
        out["ok"] = True
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            out["error"] = ("deze node draait firmware zonder pakketfilter "
                            "(ouder dan 2.3.0)")
        elif exc.code == 401:
            out["error"] = "aanmelden geweigerd door de node"
        else:
            out["error"] = f"node antwoordde HTTP {exc.code}"
    except (urllib.error.URLError, OSError, ValueError, TimeoutError) as exc:
        out["error"] = f"niet bereikbaar ({type(exc).__name__})"
    return out


# --- wat een commando aanricht ------------------------------------------------

def normalise(cmd: str) -> str:
    """De commandoregel opgeschoond: één spatie tussen woorden, geen rommel.

    Bestaat zodat de risicoweging, de bevestiging, het audittrail en wat er
    werkelijk verstuurd wordt allemaal naar dezelfde tekenreeks kijken. Een
    regel die onderweg nog verandert, is een regel waarvan de weging ergens
    anders over ging dan de handeling.
    """
    schoon = " ".join((cmd or "").split())
    # Stuurtekens horen niet in een CLI-regel en zouden in een logregel of een
    # HTTP-verzoek een tweede betekenis kunnen krijgen.
    return "".join(c for c in schoon if ord(c) >= 0x20 and ord(c) != 0x7F)[:160]


# Commando's die niets veranderen. Ze mogen langs deze weg omdat de pagina de
# stand van één onderdeel wil kunnen verversen zonder het hele blok op te halen.
LEESCOMMANDOS = {"", "types", "count", "count types", "hops", "rate", "hash",
                 "malformed", "channel list", "channel"}


def is_blanket(cmd: str, current: dict | None = None) -> bool:
    """Blokkeert deze regel een hele categorie verkeer in één klap?

    Dit is de grens tussen 'merkbaar' en 'ingrijpend', en hij ligt niet bij de
    hoeveelheid maar bij de soort. Een snelheidslimiet van vijf per minuut laat
    verkeer door; ``hops 05 0`` laat van dat type niets meer door, en dat is een
    andere handeling dan hem bijstellen -- ook al zien de twee formulieren er
    identiek uit.

    Drie vormen, en de derde is de venijnigste: ``filter on`` terwijl er al een
    categorale regel klaarstaat. Op zichzelf is aanzetten een merkbare
    handeling, maar als er ``type 05 off`` in de node staat is dit de klik die
    groepstekst stilzet. Dat is waarom hier de huidige stand van de node in mee
    mag: de zwaarte van een handeling hangt af van waar hij bovenop komt.
    """
    delen = normalise(cmd).split()
    if not delen:
        return False
    kop = delen[0]
    if kop == "hops" and len(delen) >= 3:
        return delen[2] == "0"
    if kop == "type" and len(delen) >= 3:
        return delen[2] == "off"
    if kop == "hash" and len(delen) >= 2:
        # 3 byte padhash blokkeert vandaag vrijwel al het verkeer. 2 is streng
        # maar laat de nodes door die al meerbytepaden gebruiken.
        return delen[1] == "3"
    if kop == "on":
        return _heeft_categorale_regel(current or {})
    return False


def _heeft_categorale_regel(current: dict) -> bool:
    """Staat er in deze filterstand al een regel die een categorie dichtzet?"""
    if not isinstance(current, dict):
        return False
    if int(current.get("hash") or 1) >= 3:
        return True
    for t in current.get("types") or []:
        if not isinstance(t, dict):
            continue
        if t.get("on") is False or int(t.get("hops") or 1) == 0:
            return True
    # De korte vorm uit het statistiekenbericht draagt geen typetabel maar wel
    # de telling. Beide vormen komen hier langs, dus beide worden gelezen.
    return int(current.get("blocked_types") or 0) > 0


def risk_of(cmd: str, current: dict | None = None) -> int:
    """De risicoklasse van deze commandoregel.

    Alles wat het filter ruimer maakt is licht, alles wat het smaller maakt is
    minstens merkbaar, en wat een categorie dichtzet is ingrijpend. Bij twijfel
    de zwaarste klasse -- een regel die deze functie niet herkent, is een regel
    waarvan we niet weten wat hij doet, en dat is geen reden om hem als
    ongevaarlijk te behandelen.
    """
    schoon = normalise(cmd)
    delen = schoon.split()
    if schoon in LEESCOMMANDOS:
        return RISK_PLAIN
    if not delen:
        return RISK_PLAIN
    if is_blanket(schoon, current):
        return RISK_CUTOFF

    kop = delen[0]
    # De weg terug. Bewust de lichtste klasse, lichter dan 'on' -- zie de
    # moduletekst.
    if kop in ("off", "reset"):
        return RISK_PLAIN
    if kop == "on":
        return RISK_WRITES
    if kop == "hops":
        return RISK_WRITES
    if kop == "rate" and len(delen) >= 3:
        # Limiet 0 is 'geen snelheidsregel meer voor dit type': dat laat verkeer
        # toe in plaats van weg te nemen.
        return RISK_PLAIN if delen[2] == "0" else RISK_WRITES
    if kop == "hash" and len(delen) >= 2:
        return RISK_PLAIN if delen[1] == "1" else RISK_WRITES
    if kop == "malformed" and len(delen) >= 2:
        return RISK_WRITES if delen[1] == "on" else RISK_PLAIN
    if kop == "type" and len(delen) >= 3:
        return RISK_PLAIN if delen[2] == "on" else RISK_CUTOFF
    if kop == "channel" and len(delen) >= 2:
        return RISK_WRITES if delen[1] == "add" else RISK_PLAIN
    return RISK_CUTOFF


def describe(cmd: str) -> str:
    """De commandoregel als Nederlandse zin, voor de bevestiging en het log.

    Een bevestigingsvenster dat ``hops 05 0`` toont, vraagt om een ja op iets wat
    de lezer eerst moet ontcijferen -- en dat is precies het moment waarop
    iemand op ja klikt zonder het gelezen te hebben.
    """
    delen = normalise(cmd).split()
    if not delen:
        return "de filterstand opvragen"
    kop = delen[0]

    def typenaam(idx: str) -> str:
        try:
            return f"{TYPE_NAMES[int(idx)]} ({int(idx):02d})"
        except (ValueError, IndexError):
            return f"type {idx}"

    if kop == "on":
        return "het pakketfilter AANZETTEN"
    if kop == "off":
        return "het pakketfilter uitzetten (de regels blijven staan)"
    if kop == "reset":
        return "alle filterregels terugzetten op de standaard en het filter uitzetten"
    if kop == "hops" and len(delen) >= 3:
        if delen[2] == "0":
            return f"{typenaam(delen[1])} helemaal niet meer doorsturen (0 hops)"
        return f"{typenaam(delen[1])} nog hoogstens {delen[2]} hops laten reizen"
    if kop == "rate" and len(delen) >= 4:
        if delen[2] == "0":
            return f"de snelheidslimiet op {typenaam(delen[1])} opheffen"
        return (f"{typenaam(delen[1])} beperken tot {delen[2]} pakketten "
                f"per {delen[3]} seconden")
    if kop == "hash" and len(delen) >= 2:
        return f"alleen pakketten met een padhash van minstens {delen[1]} byte doorsturen"
    if kop == "malformed" and len(delen) >= 2:
        aan = delen[1] == "on"
        return ("groepstekst met een onmogelijke structuur weggooien" if aan
                else "de structuurcontrole op groepstekst uitzetten")
    if kop == "type" and len(delen) >= 3:
        aan = delen[2] == "on"
        return (f"{typenaam(delen[1])} weer doorsturen" if aan
                else f"{typenaam(delen[1])} helemaal niet meer doorsturen")
    if kop == "channel" and len(delen) >= 3:
        if delen[1] == "add":
            return f"het kanaal '{delen[2]}' blokkeren voor groepstekst"
        if delen[1] == "remove":
            return f"het kanaal '{delen[2]}' weer doorlaten"
    return f"filter {' '.join(delen)}"


def confirmation_for(cmd: str, rep, confirm: str, current: dict | None = None) -> str:
    """Is de bevestiging zwaar genoeg? Lege string = ja.

    Hier en niet alleen in het sjabloon, om dezelfde reden als bij
    ``nodeconfig``: een bevestiging die je met een zelfgebouwd formulier kunt
    overslaan is een opmaakkeuze en geen drempel.
    """
    return nodeconfig.confirmation_for({"risk": risk_of(cmd, current)}, rep, confirm)


# --- schrijven ----------------------------------------------------------------

def write(rep, cmd: str, confirm: str = "", current: dict | None = None) -> dict:
    """Eén filtercommando uitvoeren en teruggeven wat er ná afloop in staat.

    ``current`` is de stand waarop deze wijziging bovenop komt; de risicoweging
    gebruikt hem voor het ene geval waarin dat uitmaakt (aanzetten terwijl er al
    een categorale regel klaarstaat). Niet opgehaald maar meegegeven, zodat de
    weging vóór de schrijfactie op dezelfde stand weegt als de bevestiging erna
    -- dat opnieuw ophalen zou een raceconditie zijn op de instelling waar je er
    het minst een wil.
    """
    schoon = normalise(cmd)
    route = filter_route(rep)
    out = {"ok": False, "step": "", "msg": "", "cmd": schoon,
           "wat": describe(schoon), "risk": risk_of(schoon, current), "state": {}}

    if not route["can"]:
        out.update(step="route",
                   msg=f"het filter van deze node is niet te zetten ({route['blocker']})")
        return out

    if not schoon:
        out.update(step="commando", msg="geen filtercommando opgegeven")
        return out

    probleem = confirmation_for(schoon, rep, confirm, current)
    if probleem:
        out.update(step="bevestiging", msg=probleem)
        return out

    body = urllib.parse.urlencode({"cmd": schoon}).encode()
    try:
        with nodeconfig._open(route["host"], "/api/filter", data=body,
                              timeout=FILTER_TIMEOUT_S) as resp:
            antwoord = json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        # Ook bij een fout antwoordt de node met JSON, en juist dan staat erin
        # wat er mis was. Die tekst inslikken en "HTTP 400" tonen zou de fout
        # herhalen die dit hele ontwerp probeert weg te nemen.
        try:
            antwoord = json.loads(exc.read())
        except (ValueError, OSError):
            out.update(step=f"http_{exc.code}", msg=f"node antwoordde HTTP {exc.code}")
            return out
    except (urllib.error.URLError, OSError, ValueError, TimeoutError) as exc:
        out.update(step="verbinding",
                   msg=f"geen antwoord van de node ({type(exc).__name__})")
        return out

    out.update(ok=bool(antwoord.get("ok")),
               msg=str(antwoord.get("msg") or ""),
               state=antwoord.get("state") or {})
    if not out["ok"] and not out["step"]:
        out["step"] = "node"
    return out


# --- de tweede schrijfweg: via de pollerwachtrij ------------------------------
#
# Een stock-repeater met filterpatch (JessaZH) heeft geen IP-pad en geen
# /api/filter. Zijn filter is wél te zetten: over de mesh-CLI, en sinds de
# MeshUptime-node de opdrachtwachtrij bedient (routes_api /api/v1/commands) kan
# deze site daar een `cmd:filter ...` in leggen die de node als beheerder op de
# repeater uitvoert. Dezelfde risicoweging, dezelfde bevestiging en dezelfde
# meting (pfguard) als bij de IP-weg -- alleen komt het antwoord niet meteen
# terug maar na de LoRa-sessie, als `cmd:filter` / `cmd:filter count` die we
# meteen achter de wijziging in de wachtrij zetten.

# Wat de stock-variant kent (`filter help` op JessaZH, 2026-09-04). Bewust GEEN
# `type`: die regel bestaat daar niet, en een commando dat de tegenkant niet kent
# komt terug als "command error" na een LoRa-sessie die niets deed.
STOCK_COMMANDS = ("on", "off", "reset", "hash", "hops", "rate", "malformed", "channel")

# Een kanaalnaam zoals die firmware ze aanvaardt: ``#naam`` of ``Public``.
# Dezelfde vorm als pfstock._CHAN_NAAM; daar om te LEZEN, hier om te weigeren
# voor er zendtijd aan opgaat.
_CHANNEL_NAAM = re.compile(r"^#?[A-Za-z0-9][A-Za-z0-9._\-]{0,31}$")


def queue_route(rep) -> dict:
    """Kan het filter van deze repeater via de pollerwachtrij gezet worden?

    Drie voorwaarden, in de volgorde waarin ze getoetst worden: hij wordt
    doorgestuurd (geen IP-pad, anders is er een betere weg), hij draait de
    filterpatch (anders valt er niets te zetten), en er is een verse poller die
    de wachtrij komt leegmaken (anders ligt de opdracht er tot sint-juttemis).
    """
    from . import pfstock
    out = {"can": False, "blocker": "", "poller_name": None,
           "variant": pfstock.variant(rep)}
    if not commanding.is_relayed(rep):
        out["blocker"] = "not_relayed"
        return out
    if out["variant"] != "meshcore_filter":
        out["blocker"] = "no_filter_patch"
        return out
    croute = commanding.describe(rep)
    if not croute.get("poller"):
        out["blocker"] = "no_poller"
        return out
    out["can"] = True
    out["poller_name"] = croute.get("poller_name")
    return out


def queue_write(rep, cmd: str, confirm: str = "", current: dict | None = None) -> dict:
    """Eén filtercommando in de wachtrij zetten voor de poller, plus het
    teruglezen van de stand erachter.

    Dezelfde vorm als ``write()`` zodat de pagina één soort antwoord kent. Het
    verschil zit in ``step="queued"`` en een lege ``state``: er is nog niets
    gebeurd, en dat hoort er ook te staan. ``current`` is hier de laatst
    GEMELDE stand (db.filter_state_for), niet een verse -- een verse bestaat
    voor deze node niet.
    """
    from . import db
    schoon = normalise(cmd)
    route = queue_route(rep)
    out = {"ok": False, "step": "", "msg": "", "cmd": schoon,
           "wat": describe(schoon), "risk": risk_of(schoon, current),
           "state": {}, "queued": True}

    if not route["can"]:
        out.update(step="route",
                   msg=f"het filter van deze node is niet via de wachtrij te zetten ({route['blocker']})")
        return out
    if not schoon:
        out.update(step="commando", msg="geen filtercommando opgegeven")
        return out
    delen = schoon.split()
    kop = delen[0]
    # ``channel add|remove <naam>`` is de enige regel met een vrije tekst erin, en
    # de firmware leest daar precies EEN woord. Een naam met een spatie zou
    # stilletjes op het eerste woord geblokkeerd worden -- dus hier weigeren, niet
    # daar. ``channel list`` heeft geen naam nodig.
    if kop == "channel":
        if len(delen) < 2 or delen[1] not in ("add", "remove", "list"):
            out.update(step="commando",
                       msg="channel kan alleen add, remove of list")
            return out
        if delen[1] == "list" and len(delen) != 2:
            out.update(step="commando", msg="channel list neemt geen naam")
            return out
        if delen[1] in ("add", "remove"):
            if len(delen) != 3:
                out.update(step="commando",
                           msg="channel %s vraagt EEN kanaalnaam zonder spaties "
                               "(bv. #dutch of Public)" % delen[1])
                return out
            if not _CHANNEL_NAAM.match(delen[2]):
                out.update(step="commando",
                           msg="'%s' is geen kanaalnaam die deze firmware "
                               "aanvaardt (#naam of Public, letters, cijfers, "
                               ". _ -)" % delen[2])
                return out
    if kop not in STOCK_COMMANDS:
        out.update(step="commando",
                   msg=f"deze firmware kent geen filterregel '{kop}' "
                       f"(wel: {', '.join(STOCK_COMMANDS)})")
        return out
    probleem = confirmation_for(schoon, rep, confirm, current)
    if probleem:
        out.update(step="bevestiging", msg=probleem)
        return out

    prefix = str(_field(rep, "pubkey_prefix") or "").lower()
    # De wijziging, en meteen erachter de twee leescommando's waaruit
    # pfstock.apply_cli_filter de stand samenstelt -- in deze volgorde, één
    # LoRa-sessie. Zo staat de nieuwe stand op de pagina zonder tweede klik.
    db.request_settings(prefix, ["cmd:filter " + schoon, "cmd:filter", "cmd:filter count"])
    wie = route["poller_name"] or "de poller"
    out.update(ok=True, step="queued",
               msg=f"in de wachtrij voor {wie}: de node logt over LoRa in op de repeater, "
                   f"voert `filter {schoon}` uit en leest daarna de stand terug "
                   f"(reken op één tot twee minuten; ververs dan deze pagina)")
    return out


# --- tonen --------------------------------------------------------------------

def summarise(state_dict: dict | None) -> dict:
    """De filterstand als iets wat een pagina of een tabel kan tonen.

    Drie toestanden en niet twee, en dat verschil is de helft van de waarde van
    dit hele scherm:

    ``onbekend``  deze node heeft nog nooit iets over een filter gezegd. Meestal
                  draait er firmware zonder filter; het is in elk geval geen
                  bewering dat er geen filter is.
    ``uit``       de node meldt dat er geen filter aanstaat.
    ``aan``       er staat er een aan, met hoeveel hij weggooit erbij.

    'Onbekend' en 'uit' als hetzelfde lege vakje tonen zou de vraag "staat er
    ergens een filter aan dat ik vergeten ben" onbeantwoordbaar maken, en dat is
    nu juist de vraag waarvoor dit gebouwd is.
    """
    if not state_dict:
        return {"bekend": False, "aan": False, "tekst": "onbekend", "weg": 0,
                "door": 0, "regels": 0, "redenen": []}

    drops = state_dict.get("drop") or {}
    weg = sum(int(v or 0) for v in drops.values() if isinstance(v, (int, float)))
    redenen = sorted(
        ((DROP_LABELS.get(k, k), int(v or 0)) for k, v in drops.items()
         if isinstance(v, (int, float)) and v),
        key=lambda p: -p[1])
    regels = int(state_dict.get("channels") or 0) + int(state_dict.get("blocked_types") or 0)
    if int(state_dict.get("hash") or 1) > 1:
        regels += 1
    if state_dict.get("malformed"):
        regels += 1

    aan = bool(state_dict.get("on"))
    if aan:
        tekst = f"aan ({weg} weg)" if weg else "aan"
    elif state_dict.get("disarmed"):
        # De node heeft het filter zelf uit gelaten na herhaalde herstarts. Dat
        # is een andere toestand dan 'uit gezet', en de pagina hoort dat te
        # zeggen: de regels staan er nog en komen bij een schone start terug.
        tekst = "uit (veilige modus)"
    else:
        tekst = "uit"

    return {
        "bekend": True,
        "aan": aan,
        "disarmed": bool(state_dict.get("disarmed")),
        "tekst": tekst,
        "weg": weg,
        "door": int(state_dict.get("passed") or 0),
        "vrij": int(state_dict.get("exempt") or 0),
        "regels": regels,
        "redenen": redenen,
        "updated": state_dict.get("_updated") or "",
    }


def breakdown(state_dict: dict | None, admin: bool = False) -> dict:
    """De uitsplitsing van wat het filter weggooide (firmware 2.6.0+).

    Wat hier NIET in zit, en waarom dat een keuze is en geen omissie. De site
    staat publiek. Weggegooide pakketten zijn andermans verkeer, en de grens die
    dit project daarin al trekt -- zie ``docs/privacy.md`` -- is: geaggregeerde
    tellers over het GEDRAG VAN DEZE NODE zijn openbaar, want wie merkt dat zijn
    bericht niet aankomt heeft daar recht op; de REGELTABELLEN zijn beheerders-
    gereedschap en staan achter een login.

    Deze uitsplitsing valt aan beide kanten van die grens, en wordt daarom
    gesplitst in plaats van als geheel de ene of de andere kant op geduwd:

    ``xr`` en ``ex``    tellingen per pakkettype. Openbaar. Het pakkettype staat
                        al op de publieke pakkettenpagina van elk bericht, en er
                        zit geen identiteit in -- 'ADVERT sneuvelde 40 keer op de
                        hoplimiet' zegt iets over deze repeater, niet over wie
                        die adverts uitzond.
    ``rate``            de druk op de limiet. Openbaar, behalve ``lim``: dat is
                        de ingestelde waarde, en dus een regel.
    ``chan``            de hash en het aantal treffers zijn openbaar, het LABEL
                        niet. Die knip loopt niet tussen 'kanaal' en 'geen
                        kanaal' maar tussen een meting en een oordeel.
                        De hash is één byte van sha256(kanaalsleutel), en die
                        byte staat onversleuteld in élk groepsbericht dat door de
                        lucht gaat -- iedereen met een ontvanger leest hem mee.
                        Hem hier verzwijgen beschermt dus niemand, terwijl 'dit
                        kanaal wordt hier geweerd, 900 keer' precies is wat
                        iemand nodig heeft die zich afvraagt waarom zijn verkeer
                        niet aankomt.
                        Het label is van een andere soort: geen waarneming maar
                        de naam die ONZE beheerder aan het kanaal van iemand
                        anders gaf. Het draagt geen informatie die de hash niet
                        al draagt, en publiceren zou de site een oordeel over een
                        derde laten herhalen ('spam') waar ze een gedraging van
                        deze node hoort te melden. Dat hoort achter de login.
    """
    leeg = {"bekend": False, "xr": [], "rate": [], "ex": [], "chan": [],
            "trunc": False}
    if not state_dict:
        return leeg
    stats = state_dict.get("stats")
    if not isinstance(stats, dict) or not stats:
        return leeg

    kruis = []
    for naam, redenen in (stats.get("xr") or {}).items():
        if not isinstance(redenen, dict):
            continue
        totaal = sum(int(v or 0) for v in redenen.values() if isinstance(v, int))
        if not totaal:
            continue
        kruis.append({
            "type": naam,
            "totaal": totaal,
            "redenen": sorted(((DROP_LABELS.get(k, k), int(v)) for k, v in redenen.items()
                               if isinstance(v, int) and v), key=lambda p: -p[1]),
        })
    kruis.sort(key=lambda d: -d["totaal"])

    tempo = []
    for naam, regel in (stats.get("rate") or {}).items():
        if not isinstance(regel, dict):
            continue
        vensters = int(regel.get("seen") or 0)
        if not vensters:
            continue
        geraakt = int(regel.get("cap") or 0)
        rij = {"type": naam, "vensters": vensters, "geraakt": geraakt,
               "piek": int(regel.get("peak") or 0),
               # Het aandeel is het getal waar het om gaat: 12 op 4000 is een
               # ruime limiet, 12 op 14 een knellende, en het aantal weggegooide
               # pakketten kan in beide gevallen gelijk zijn.
               "aandeel": round(100.0 * geraakt / vensters, 1)}
        if admin:
            rij["limiet"] = int(regel.get("lim") or 0)
        tempo.append(rij)
    tempo.sort(key=lambda d: -d["aandeel"])

    vrij = sorted(((naam, int(aantal)) for naam, aantal in (stats.get("ex") or {}).items()
                   if isinstance(aantal, int) and aantal), key=lambda p: -p[1])

    kanalen = []
    for item in stats.get("chan") or []:
        if not isinstance(item, dict):
            continue
        rij = {"hash": item.get("hash") or "", "hits": int(item.get("hits") or 0)}
        if admin:
            rij["label"] = item.get("label") or ""
        kanalen.append(rij)
    kanalen.sort(key=lambda d: -d["hits"])

    return {"bekend": True, "xr": kruis, "rate": tempo, "ex": vrij,
            "chan": kanalen, "trunc": bool(stats.get("trunc"))}


def mesh_totals(states: dict, alle_repeater_ids) -> dict:
    """De filtercijfers van alle nodes samen, voor de voorpagina.

    Een optelsom over nodes is de minst gevoelige vorm die er is: geen node
    aanwijsbaar, geen pakket aanwijsbaar, alleen 'zoveel verkeer wordt in dit
    mesh geweerd, en hierom'. Daarom staat dit publiek. Zie ``docs/privacy.md``.

    WAAR DIT MAKKELIJK ONEERLIJK WORDT, en dat is de reden dat deze functie
    meer teruggeeft dan een paar sommen. Een totaal over *de nodes die
    rapporteren* is geen totaal over *het mesh*. Vandaag rapporteert er in de
    praktijk één repeater; een kaal '412 geweerd' is dan het cijfer van één node
    in de kleren van een groep. Er komt dus altijd bij hoeveel nodes meetellen
    en hoeveel er zijn, en die tellingen houden dezelfde drie toestanden aan die
    de rest van dit bestand ook aanhoudt:

    ``met_filter``    meldt een filter dat aanstaat
    ``zonder_filter`` meldt uitdrukkelijk dat er geen filter aanstaat
    ``onbekend``      heeft nog nooit iets over een filter gezegd -- meestal
                      firmware zonder filter, in elk geval geen bewering

    Die laatste twee op één hoop gooien zou 'wij weten het niet' laten doorgaan
    voor 'daar staat niets aan', en dat is precies het onderscheid waarvoor het
    hele scherm bestaat.

    EN DE PERIODE. De tellers van een node lopen sinds zijn laatste herstart en
    overleven een reboot niet. Een som over nodes met verschillende uptimes is
    dus geen som over een gelijke periode, en er is hier geen venster te kiezen
    dat dat repareert: de node levert standen, geen reeksen. Het wordt daarom
    niet weggepoetst maar benoemd -- ``periode`` is de tekst die de pagina
    erbij zet, en ``sinds`` is de oudste meting waarop deze som steunt.
    """
    met_filter = zonder_filter = 0
    weg = door = vrij = 0
    redenen: dict = {}
    sinds = ""
    gemeten = 0

    for rid in alle_repeater_ids:
        stand = states.get(rid)
        if not stand:
            continue
        gemeten += 1
        if stand.get("on"):
            met_filter += 1
        else:
            zonder_filter += 1
        door += int(stand.get("passed") or 0)
        vrij += int(stand.get("exempt") or 0)
        for sleutel, aantal in (stand.get("drop") or {}).items():
            if not isinstance(aantal, (int, float)) or aantal < 0:
                continue
            weg += int(aantal)
            redenen[sleutel] = redenen.get(sleutel, 0) + int(aantal)
        # De oudste meting waarop deze som steunt. Niet de nieuwste: die zou
        # suggereren dat het hele beeld van zopas is, terwijl één node dat
        # gisteren voor het laatst meldde er even hard in meetelt.
        gemeld = str(stand.get("_updated") or "")
        if gemeld and (not sinds or gemeld < sinds):
            sinds = gemeld

    totaal = len(list(alle_repeater_ids))
    return {
        "gemeten": gemeten,
        "totaal": totaal,
        "onbekend": totaal - gemeten,
        "met_filter": met_filter,
        "zonder_filter": zonder_filter,
        "weg": weg,
        "door": door,
        "vrij": vrij,
        "redenen": sorted(((DROP_LABELS.get(k, k), v) for k, v in redenen.items() if v),
                          key=lambda p: -p[1]),
        "sinds": sinds,
        # Wordt er iets geweerd? Zo niet, dan hoort de voorpagina geen kader te
        # tonen dat vooral zegt dat er niets te melden is.
        "iets_te_melden": gemeten > 0 and (weg > 0 or met_filter > 0),
    }
