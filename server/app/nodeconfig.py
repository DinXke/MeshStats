"""Eén CLI-instelling van een node zetten vanaf de beheerpagina.

**Eén schrijfweg, drie vervoermiddelen.** Alles wat een schrijfactie kan
tegenhouden -- de parameterlijst van de node, zijn grenzen, de risicoklassen, de
bevestiging, de rechten, de terugleescontrole -- staat in ``write()`` en gebeurt
onverkort. Pas als er niets meer te weigeren valt, wordt er gekozen hóé het
commando er komt. Per node de eerste van deze drie die werkelijk beschikbaar is:

    1. ``ip``    HTTP naar de node zelf (``POST /api/cfg``). Vraagt een IP-pad en
                 ``MM_FW_NODE_USER``/``MM_FW_NODE_PASS``. Tienden van seconden,
                 want het is één CLI-aanroep, en het teruglezen zit in hetzelfde
                 antwoord.
    2. ``mqtt``  het ``cmd``-topic (``set <param> <waarde>``). Beschikbaar voor
                 elke node die zelf publiceert, want die HEEFT per definitie een
                 verbinding met deze broker. Seconden; het teruglezen komt terug
                 in het eerstvolgende statistiekenbericht.
    3. ``mesh``  HTTP naar de MONITOR van deze node (``POST /api/moncfg``), die
                 het over LoRa doorgeeft. Voor een node zonder IP-pad, en dat is
                 de node waar dit project omheen gebouwd is. Tientallen seconden.

De volgorde is niet willekeurig. Bovenaan staat de weg met de sterkste
tegenpartij en de snelste, meest volledige teruglezing; onderaan de duurste.
Wat er per node gekozen is en waaróm staat in ``why`` en op de nodepagina, want
"het is gelukt" zonder te zeggen waarlangs, is bij drie wegen te weinig.

**Waarom het cmd-topic erbij is gekomen.** Tot 2.5.0 stond hier dat dit
nadrukkelijk NIET over MQTT ging. Dat was houdbaar zolang dat topic alleen kon
vragen om te práten. Maar het gevolg ervan was een node die zelf publiceert,
onze firmware draait en volledig beheerd heet, en waarover de pagina "kan niet"
zei zodra er geen weblogin ingevuld was -- terwijl er een open, werkende
verbinding naartoe lag. Dat was feitelijk onjuist, en dat is wat er veranderd is.

Wat er NIET veranderd is, is waarom die lijst kort moest blijven. Het topic is
bereikbaar voor iedereen met brokergegevens, en er is één nauw omschreven woord
bij gekomen waarbij de NODE ZELF valideert: alleen parameters uit zijn eigen
ingebakken tabel, alleen waarden binnen zijn eigen grenzen, en alleen de
risicoklassen die zijn firmware laag genoeg vindt voor dit kanaal. Een grotere
whitelist, geen doorgeefluik. Zie ``MQTT_MAX_RISK`` hieronder voor de afweging
die daarbij hoort, en ``docs/security.md`` voor wat dit voor de broker betekent.

Twee dingen die dit bestand met opzet NIET doet.

**Geen eigen parameterlijst.** De firmware heeft er een, ingebakken, en die is
wat er werkelijk tussen een klik en de radio staat. Een tweede lijst hier zou
vroeg of laat afwijken, en de dag dat dat gebeurt biedt de pagina een parameter
aan die de node weigert -- of erger, ze zijn het eens over de naam en oneens over
de grenzen. Dus haalt de server de lijst op bij de node zelf (``GET /api/cfg``)
en gebruikt die om het formulier te bouwen én om een tikfout alvast te weigeren.
Dat blijft "controleren aan beide kanten": hier voor een snelle, leesbare fout,
daar omdat dat de controle is die telt.

**Geen tweede schrijfweg naast deze.** Er is één ``write()`` en er zijn drie
vervoermiddelen. Een tweede functie voor een van de drie zou een tweede plek
zijn waar een drempel kan ontbreken, en dat is het soort fout dat je pas ontdekt
als er een node stil is. Zelfde parameterlijst, zelfde grenzen, zelfde
risicoklassen, zelfde bevestigingen, zelfde rechten, zelfde terugleescontrole --
alleen het transport verschilt.

Twee dingen die aan de LoRa-weg anders zijn en die hieronder terugkomen.

De MONITOR heeft de nieuwe firmware nodig, niet het doel. De dakrepeater leert
niets, krijgt niets en merkt niets: hij ontvangt twee doodgewone CLI-commando's.
Dat is de kracht van deze weg -- een node die maandenlang geen nieuwe firmware
krijgt, hoeft er ook geen.

En er is een derde uitkomst bij gekomen. Over IP is een schrijfactie gelukt of
niet. Over LoRa kan het antwoord op de `set` uitblijven, en dan is het eerlijke
antwoord dat we het NIET WETEN: het commando is de lucht in gegaan en of het is
aangekomen valt van hieraf niet te zien. Dat heet hier ``geen_antwoord`` en het
is met opzet geen mislukking, want "mislukt" laat iemand denken dat er niets
gebeurd is.
"""

from __future__ import annotations

import json
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

from . import commanding, db, firmware

# De firmware die POST /api/cfg kent. Lager en het endpoint bestaat niet: dan
# antwoordt de node met 404 en hoort de pagina dat te zeggen in plaats van een
# knop aan te bieden die een foutmelding oplevert.
MIN_CFG_VERSION = (2, 1, 0)

# En de firmware die /api/moncfg kent: schrijven over LoRa naar een repeater die
# deze node monitort. Die eis geldt voor de MONITOR en niet voor het doel -- het
# doel draait stock MeshCore en krijgt gewoon twee CLI-commando's binnen. Dat is
# precies waarom deze weg werkt voor een node die nooit nieuwe firmware krijgt.
MIN_MESH_CFG_VERSION = (2, 4, 0)

# En de firmware die 'set <param> <waarde>' op zijn cmd-topic aanneemt. Ouder
# betekent hier niet "misschien": zo'n node kent het woord niet, weigert het en
# telt het als geweigerd -- en dat gebeurt aan de overkant, zonder dat er hier
# iets van te zien is. Vandaar dat de knop niet getekend wordt in plaats van een
# opdracht te versturen die de lucht in verdwijnt.
MIN_MQTT_CFG_VERSION = (2, 8, 0)

# Hoe lang de server op de uitslag van een schrijfactie over MQTT wacht.
#
# De node zet en leest terug in dezelfde lus en publiceert daarna meteen (het
# 'set'-commando zet dezelfde vlag als 'status'), dus dit is een kwestie van
# seconden en niet van een halve minuut zoals over LoRa. Blijft het uit, dan is
# dat geen mislukking maar onzekerheid, en die heeft hier dezelfde naam als daar:
# ``geen_antwoord``.
MQTT_WAIT_S = 20
MQTT_POLL_S = 0.5

# Het plafond van het cmd-topic: klasse 1 en 2 mogen erlangs, klasse 3 niet.
#
# Dit is de afweging die de drie vervoermiddelen van elkaar onderscheidt, en ze
# gaat over wie er aan de overkant staat.
#
#   ip    de weblogin van de node zelf, uit de omgeving van deze server.
#   mesh  de weblogin van de monitor, die vervolgens met zijn EIGEN rechten
#         inlogt op het doel -- rechten die de eigenaar aan de overkant heeft
#         uitgegeven en zelf kan intrekken.
#   mqtt  wie de broker heeft binnengelaten. In de aanbevolen opstelling is dat
#         een account per node met een ACL eromheen; in de opstelling die hier
#         draait is het één gedeeld account, en dan is het elke node die met deze
#         broker praat.
#
# Bij de eerste twee staat een geauthenticeerde tegenpartij; bij de derde staat
# de brokerinrichting, en die is een instelling van iemand anders. Daar komt bij
# dat de teruglezing over MQTT asynchroon is: er is geen antwoord in hetzelfde
# verzoek dat een fout meteen zichtbaar maakt, en dat is precies wat je wél wilt
# bij een handeling die een node van de lucht kan halen.
#
# "Overal alles" en "nergens iets" zijn allebei het verkeerde antwoord. De
# instellingen die je op een gewone dag bijstelt -- naam, positie,
# advertentie-interval, floodgrenzen, zendtijdbudget -- zijn klasse 1 en 2, en
# die mogen hier. De handvol die de bereikbaarheid kan afsnijden houdt zijn twee
# wegen met een wachtwoord ervoor. De firmware handhaaft dezelfde grens
# (CFG_MQTT_MAX_RISK), zodat deze regel niet alleen in de server leeft.
MQTT_MAX_RISK = 2

# Wat er van afstand NOOIT gezet wordt, langs geen enkel vervoermiddel.
#
# 'radio' is in MeshCore één parameter die vier getallen tegelijk zet:
# frequentie, bandbreedte, spreidingsfactor en coderingssnelheid. De asymmetrie
# die deze streep rechtvaardigt: een verkeerde 'tx' maakt een node zwakker maar
# laat hem bereikbaar -- je hoort hem nog, hij hoort jou nog, en je zet het
# terug. Een verkeerde frequentie, spreidingsfactor, coderingssnelheid of
# bandbreedte haalt hem van de lucht: hij hoort niemand meer en niemand hoort
# hem, en er is geen weg terug die niet fysiek is. Op een dak is dat het einde.
#
# Geen bevestiging repareert dat. Een drempel beschermt tegen twijfel en tegen
# de klik op de verkeerde regel; ze beschermt niet tegen een getal dat de zender
# op een band zet waar de antenne niet op staat. Daarom is dit een weigering en
# geen zwaardere drempel, en daarom staat 'tx' er niet bij.
#
# Twee plaatsen, en de tweede is niet overbodig.
#
# In de FIRMWARE is 'radio' sinds 2.6.0 helemaal uit CFG_PARAMS verdwenen. Die
# ene lijst is tegelijk wat /api/cfg publiceert, wat /api/moncfg aanvaardt en wat
# het cmd-topic doorlaat, dus één regel weghalen sluit alle drie de ingangen op
# de node zelf. Dat is de helft die telt: de server is niet het enige dat een
# node kan bereiken, en de node draagt het gevolg.
#
# Hier staat de weigering nog een keer, onder eigen naam, en daar zijn twee
# redenen voor. Ten eerste weigert de server dan vóórdat er iets vertrekt, met
# een zin die zegt waarom -- in plaats van een node die "staat niet op de lijst"
# terugstuurt, wat ook het antwoord is op een tikfout. Ten tweede, en dat is de
# harde reden: een node die nog firmware van vóór 2.6.0 draait HÉÉFT 'radio' nog
# in zijn tabel staan en zou hem gewoon aannemen. De regel hangt aan de handeling
# en niet aan de firmwareversie van de node die hem toevallig krijgt.
#
# En als weigering, niet als ontbrekend invoerveld: dit staat in ``write()``,
# waar het verzoek werkelijk begint. Een parameter die alleen uit het formulier
# weg is, valt met een aangepast verzoek alsnog te schrijven.
NO_REMOTE = ("radio",)

NO_REMOTE_REASON = (
    "de radio-instellingen worden van afstand niet gezet. Een verkeerde "
    "frequentie, spreidingsfactor, coderingssnelheid of bandbreedte haalt deze "
    "node van de lucht, en dat is van hieraf niet terug te draaien -- er is dan "
    "geen weg meer naartoe die niet fysiek is. Het zendvermogen (tx) kan wel: "
    "een node die te zwak zendt blijft bereikbaar."
)

# Hoe lang de server op de monitor wacht voordat hij de pagina teruggeeft.
#
# De handeling zelf duurt op de node hoogstens 90 seconden (login, `set`,
# adempauze, `get`), en dat is te lang om een browser op te laten wachten: een
# omgekeerde proxy kapt zoiets af en dan staat er een foutpagina over een
# schrijfactie die gewoon doorloopt. Dus wacht de server een stuk korter en zegt
# eerlijk "loopt nog" als het niet klaar is.
#
# Dat kan omdat de uitslag niet hier bewaard wordt maar op de MONITOR: die houdt
# de laatste opdracht vast tot de volgende. Wie de pagina herlaadt, ziet hem
# alsnog. Er hoeft dus geen opdrachtenlijst en geen achtergronddraad in de server
# te staan om een handeling van een halve minuut te overleven -- de node die het
# werk doet, is ook de plek waar het antwoord ligt.
MESH_WAIT_S = 40
MESH_POLL_S = 2
MESH_START_TIMEOUT_S = 10

# Risicoklassen zoals de firmware ze meegeeft. Ze sturen hier één ding: hoeveel
# moeite het kost om een waarde te zetten. Sluit aan bij wat de beheerpagina al
# doet met kleur en gewicht -- blauw kost zendtijd, oranje schrijft op het
# apparaat, rood is onomkeerbaar.
RISK_PLAIN = 1      # zo weer terug te zetten; opslaan volstaat
RISK_WRITES = 2     # bevestiging die node en sleutel noemt
RISK_CUTOFF = 3     # naam van de node overtypen

RISK_NAMES = {RISK_PLAIN: "gewoon", RISK_WRITES: "schrijft", RISK_CUTOFF: "afsnijden"}

# De lijst van een node verandert alleen als er andere firmware op gaat, dus een
# korte cache is ruim voldoende en scheelt een netwerkronde per paginaweergave.
PARAMS_TTL_S = 300
CFG_TIMEOUT_S = 10

_lock = threading.Lock()
_params: dict[str, dict] = {}


def _field(row, key, default=None):
    return firmware._field(row, key, default)


# --- kan er naar deze node geschreven worden ---------------------------------

def _route_ip(rep) -> dict:
    """Kandidaat 1: HTTP naar de node zelf.

    De snelste en de volledigste. Eén CLI-aanroep over het lokale net, met het
    teruglezen in hetzelfde antwoord, en met de weblogin van de node ertussen.
    Daarom staat hij bovenaan: waar deze kan, is er geen reden voor een andere.
    """
    host = (_field(rep, "ota_host") or "").strip()
    fw = _field(rep, "fw_meshmanager") or ""
    version = commanding.parse_version(fw)
    out = {"transport": "ip", "can": False, "blocker": "", "host": host, "fw": fw,
           "min_fw": ".".join(str(n) for n in MIN_CFG_VERSION),
           "max_risk": RISK_CUTOFF, "target": "", "monitor": ""}
    if not firmware.NODE_USER:
        out["blocker"] = "no_credentials"
    elif not host:
        out["blocker"] = "no_host"
    elif version is None:
        out["blocker"] = "no_fw"
    elif version < MIN_CFG_VERSION:
        out["blocker"] = "old_fw"
    else:
        out["can"] = True
    return out


def _route_mqtt(rep, broker_connected: bool) -> dict:
    """Kandidaat 2: het cmd-topic.

    Beschikbaar voor elke node die zelf publiceert. Dat is de kern van waarom
    deze kandidaat bestaat: zo'n node HEEFT een verbinding met deze broker, dus
    "er is geen weg naartoe" was er altijd al naast.

    Wat hier NIET gevraagd wordt is een weblogin, en wat er wél gevraagd wordt is
    een levende brokerverbinding. Dat laatste als laatste getoetst, om dezelfde
    reden als in ``commanding.route_for``: een broker die even weg is, hoort de
    blijvende reden niet te overschaduwen.

    ``max_risk`` ligt hier lager dan bij de andere twee. Zie ``MQTT_MAX_RISK``.
    """
    node = (_field(rep, "source_prefix") or "").lower().strip()
    fw = _field(rep, "fw_meshmanager") or ""
    version = commanding.parse_version(fw)
    out = {"transport": "mqtt", "can": False, "blocker": "", "host": "", "fw": fw,
           "min_fw": ".".join(str(n) for n in MIN_MQTT_CFG_VERSION),
           "max_risk": MQTT_MAX_RISK, "target": "", "monitor": "", "node": node}
    if not node:
        out["blocker"] = "no_source"
    elif node == "api":
        out["blocker"] = "http_source"
    elif commanding.is_relayed(rep):
        # Een doorgestuurde node heeft geen eigen cmd-topic: hij publiceert niet
        # en leest dus ook niets. Het topic van zijn monitor bestaat wel, maar
        # daarop een schrijfactie voor een DERDE node aanbieden zou een tweede
        # soort commando zijn met een tweede soort doel erin, over het kanaal met
        # de zwakste tegenpartij. Die weg heet hier 'mesh' en loopt over HTTP.
        out["blocker"] = "relayed"
    elif version is None:
        out["blocker"] = "no_fw"
    elif version < MIN_MQTT_CFG_VERSION:
        out["blocker"] = "old_fw"
    elif not broker_connected:
        out["blocker"] = "broker_down"
    else:
        out["can"] = True
    return out


def _route_mesh(rep, relay) -> dict:
    """Kandidaat 3: HTTP naar de MONITOR, die het over LoRa doorgeeft.

    Elke eis hieronder gaat over de MONITOR en geen enkele over het doel -- dat
    is niet een bijzonderheid maar de kern van deze weg. De dakrepeater draait
    stock MeshCore, krijgt nooit iets van ons, en dat hoeft ook niet: hij
    ontvangt twee doodgewone CLI-commando's.
    """
    host = (_field(relay, "ota_host") or "").strip()
    fw = _field(relay, "fw_meshmanager") or ""
    version = commanding.parse_version(fw)
    out = {"transport": "mesh", "can": False, "blocker": "", "host": host, "fw": fw,
           "min_fw": ".".join(str(n) for n in MIN_MESH_CFG_VERSION),
           "max_risk": RISK_CUTOFF,
           "target": (_field(rep, "pubkey_prefix") or "").lower().strip(),
           "monitor": str(_field(relay, "name") or
                          _field(rep, "source_prefix") or "").strip()}
    if relay is None:
        # De doorstuurder is hier zelf geen bekende repeater. Dan is er geen
        # beheeradres om aan te kloppen en geen firmwareversie om op te toetsen,
        # en gokken kost een verzoek dat nergens aankomt.
        out["blocker"] = "relay_unknown"
    elif not firmware.NODE_USER:
        # Hier geldt de weblogin wél, en het is een andere login dan waar de
        # eerste opzet naar zocht: die van de MONITOR, een node van onszelf. Wat
        # de server nooit hoeft te kennen is een geheim van het DOEL -- dat hoort
        # bij de monitor, in zijn eigen monitorlijst, of het bestaat niet omdat
        # de overkant ons in zijn toegangslijst zette.
        out["blocker"] = "no_credentials"
    elif not host:
        out["blocker"] = "no_relay_host"
    elif version is None:
        out["blocker"] = "relay_no_fw"
    elif version < MIN_MESH_CFG_VERSION:
        out["blocker"] = "relay_old_fw"
    elif not out["target"]:
        out["blocker"] = "no_target"
    else:
        out["can"] = True
    return out


# Waarom een kandidaat afvalt, in het Nederlands, zodat de reden in ``why``
# dezelfde is als die op de pagina en in het logboek. Een zin die op drie
# plaatsen anders luidt, is een zin waar niemand meer op vertrouwt.
BLOCKER_TEXT = {
    "no_credentials": "geen weblogin voor de beheerpagina (MM_FW_NODE_USER/PASS)",
    "no_host": "geen beheeradres ingevuld",
    "no_fw": "meldt geen versie van onze firmware",
    "old_fw": "de firmware is er te oud voor",
    "no_source": "er is nog geen bericht van deze node binnengekomen",
    "http_source": "de cijfers komen via de HTTP-API; er is geen cmd-topic",
    "relayed": "publiceert zelf niet, dus er is geen eigen cmd-topic",
    "broker_down": "de broker is nu niet verbonden",
    "relay_unknown": "de doorstuurder is hier zelf niet bekend",
    "no_relay_host": "de monitor heeft geen beheeradres ingevuld",
    "relay_no_fw": "de monitor meldt geen versie van onze firmware",
    "relay_old_fw": "de firmware van de monitor is er te oud voor",
    "no_target": "van deze repeater is geen publieke sleutel bekend",
}

TRANSPORT_TEXT = {
    "ip": "over HTTP naar de node zelf",
    "mqtt": "over het MQTT-cmd-topic",
    "mesh": "over LoRa via zijn monitor",
}


def cfg_route(rep, relay=None, broker_connected=None) -> dict:
    """Mag en kan de site een instelling van deze repeater zetten, en waarlangs?

    Bewust een eigen sleutel naast ``commanding.route_for`` en naast
    ``firmware.ota_route``, om dezelfde reden als daar: de drie reizen over
    verschillende dingen. Een node kan opdrachten over MQTT aannemen zonder
    IP-pad, en een node kan een image aannemen terwijl zijn firmware nog geen
    /api/cfg kent. Ze door elkaar halen levert precies één soort fout op, en dat
    is de knop die belooft wat hij niet kan.

    Drie kandidaten worden alle drie doorgerekend en niet alleen de eerste die
    past. Dat kost niets -- het zijn drie vergelijkingen op rijen die er al zijn
    -- en het levert het antwoord op de vraag die iemand werkelijk stelt als er
    niets kan: niet "het gaat niet", maar wat er per weg aan ontbreekt.
    ``options`` draagt die drie, ``why`` vat ze samen in één zin.

    De gekozen weg staat in ``transport``, met ``host``, ``target``,
    ``monitor``, ``fw``, ``min_fw`` en ``max_risk`` erbij die daarbij horen.
    Kan er niets, dan draagt de sleutel de kandidaat die het dichtst bij was --
    de eerste van de drie die überhaupt van toepassing is -- zodat de pagina één
    reden kan tonen in plaats van drie.

    ``relay`` is de repeaterrij van de node die voor deze repeater publiceert.
    Als argument zodat deze functie te testen is zonder databank; wordt hij niet
    meegegeven, dan zoekt hij hem zelf op. Hetzelfde geldt voor
    ``broker_connected``.
    """
    relayed = commanding.is_relayed(rep)
    if relayed and relay is None:
        relay = db.find_repeater(_field(rep, "source_prefix"))
    if broker_connected is None:
        from . import mqtt_ingest
        broker_connected = mqtt_ingest.can_publish()

    ip = _route_ip(rep)
    mqtt = _route_mqtt(rep, broker_connected)
    mesh = _route_mesh(rep, relay) if relayed else None

    # De volgorde is de rangschikking: sterkste tegenpartij en beste teruglezing
    # eerst, duurste laatst. Een doorgestuurde node heeft alleen de derde; een
    # node die zelf publiceert heeft de eerste twee. Dat de lijst per node
    # verschilt is geen uitzondering maar wat deze functie te zeggen heeft.
    kandidaten = [ip, mqtt] if not relayed else [mesh]
    gekozen = next((k for k in kandidaten if k["can"]), None)

    out = {"can": False, "blocker": "", "host": "", "fw": "", "relayed": relayed,
           "transport": "mesh" if relayed else "ip", "target": "", "monitor": "",
           "min_fw": "", "max_risk": RISK_CUTOFF,
           "options": kandidaten, "why": ""}

    if gekozen is not None:
        out.update({k: v for k, v in gekozen.items() if k != "can"}, can=True)
        # Alles vóór de gekozene is per definitie afgevallen -- dat is wat "de
        # eerste die werkelijk beschikbaar is" betekent -- en juist die redenen
        # horen erbij. Wie ziet dat er over MQTT geschreven wordt, hoort te weten
        # dat dat komt doordat er geen weblogin staat.
        afgevallen = kandidaten[:kandidaten.index(gekozen)]
        reden = "; ".join(f"{TRANSPORT_TEXT[k['transport']]} kan niet: "
                          f"{BLOCKER_TEXT.get(k['blocker'], k['blocker'])}"
                          for k in afgevallen)
        out["why"] = (f"{TRANSPORT_TEXT[gekozen['transport']]}"
                      + (f" -- {reden}" if reden else
                         " -- de eerste en snelste weg is beschikbaar"))
        return out

    eerste = kandidaten[0]
    out.update({k: v for k, v in eerste.items() if k != "can"})
    out["why"] = "geen enkele weg: " + "; ".join(
        f"{TRANSPORT_TEXT[k['transport']]} -- {BLOCKER_TEXT.get(k['blocker'], k['blocker'])}"
        for k in kandidaten)
    return out


# --- de node ------------------------------------------------------------------

def _open(host: str, path: str, data: bytes | None = None, timeout: int = CFG_TIMEOUT_S):
    url = firmware._url(host, path)
    headers = dict(firmware._auth_header())
    if data is not None:
        headers["Content-Type"] = "application/x-www-form-urlencoded"
    req = urllib.request.Request(url, data=data, headers=headers,
                                 method="POST" if data is not None else "GET")
    return urllib.request.urlopen(req, timeout=timeout)


def params(host: str, force: bool = False) -> dict:
    """Welke parameters deze node laat zetten, met hun grenzen.

    Rechtstreeks van de node, want de firmware is de baas over die lijst. Bij een
    404 draait er firmware van voor 2.1.0; dat is een versie en geen storing, en
    de pagina hoort dat anders te zeggen dan "onbereikbaar".
    """
    key = (host or "").strip()
    out = {"ok": False, "error": "", "params": [], "at": 0.0}
    if not key:
        out["error"] = "geen beheeradres"
        return out

    with _lock:
        cached = _params.get(key)
        if cached and not force and (time.time() - cached["at"]) < PARAMS_TTL_S:
            return dict(cached)

    try:
        with _open(key, "/api/cfg") as resp:
            data = json.loads(resp.read())
        out.update(ok=True, params=list(data.get("params") or []), at=time.time())
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            out["error"] = ("deze node draait firmware zonder /api/cfg "
                            "(ouder dan 2.1.0)")
        elif exc.code == 401:
            out["error"] = "aanmelden geweigerd door de node"
        else:
            out["error"] = f"node antwoordde HTTP {exc.code}"
    except (urllib.error.URLError, OSError, ValueError, TimeoutError) as exc:
        out["error"] = f"niet bereikbaar ({type(exc).__name__})"

    if out["ok"]:
        with _lock:
            _params[key] = dict(out)
    return out


# De volgorde van de velden in een regel van ``cfgspec``, zoals de firmware ze
# schrijft (cfgSpecJson). Compact omdat die tabel in een bericht met een vaste
# buffer meereist: achtentwintig objecten met acht sleutels elk zijn twee
# kilobyte, deze vorm is negenhonderd byte.
#
#   <soort>,<lo>,<hi>,<risico>,<herstart>,<geheim>[,<keuzes>]
#
# De ontleding staat hier, naast de vorm, en de vorm staat in de firmware naast
# het schrijven. Twee plaatsen die het over hetzelfde eens moeten zijn is er één
# meer dan ideaal; het alternatief was een tweede parametertabel in deze server,
# en dat is precies wat dit bestand niet wil.
SPEC_FIELDS = ("kind", "lo", "hi", "risk", "reboot", "secret")


def spec_from_node(rep) -> dict:
    """De parameterlijst zoals deze node hem zelf over MQTT meldde.

    Hetzelfde soort antwoord als ``params()``, zodat de rest van dit bestand en
    het sjabloon niet hoeven te weten waar de lijst vandaan komt. Wat er niet is,
    is er niet: een node die zijn tabel nog nooit meestuurde levert hier een
    lege lijst met een zin die zegt wat je eraan doet -- één instellingenronde.
    """
    out = {"ok": False, "error": "", "params": [], "at": 0.0}
    ruw = str(_field(rep, "cfg_spec") or "").strip()
    if not ruw:
        out["error"] = ("deze node heeft zijn parameterlijst nog niet gemeld. "
                        "Vraag eenmalig een instellingenronde op; die stuurt de "
                        "lijst mee, en daarna staat het formulier er")
        return out
    try:
        rauw = json.loads(ruw)
    except (ValueError, TypeError):
        out["error"] = "de gemelde parameterlijst is onleesbaar"
        return out
    if not isinstance(rauw, dict):
        out["error"] = "de gemelde parameterlijst heeft niet de verwachte vorm"
        return out

    lijst = []
    for key, regel in rauw.items():
        delen = str(regel).split(",")
        if len(delen) < len(SPEC_FIELDS):
            continue
        spec = {"key": str(key), "choices": ",".join(delen[len(SPEC_FIELDS):])}
        for naam, waarde in zip(SPEC_FIELDS, delen):
            if naam in ("lo", "hi"):
                try:
                    spec[naam] = float(waarde)
                except ValueError:
                    spec[naam] = 0.0
            elif naam == "kind":
                spec[naam] = waarde
            else:
                try:
                    spec[naam] = int(waarde)
                except ValueError:
                    # Bij twijfel de veilige kant: een risicoklasse die niet te
                    # lezen is, is de zwaarste.
                    spec[naam] = RISK_CUTOFF if naam == "risk" else 0
        lijst.append(spec)

    if not lijst:
        out["error"] = "de gemelde parameterlijst is leeg"
        return out
    out.update(ok=True, params=lijst)
    return out


def params_for(rep, route=None) -> dict:
    """De parameterlijst van deze node, langs de weg die er is.

    Nog steeds de lijst van de node zelf en nooit een tabel van hier -- dat is de
    regel bovenaan dit bestand en die blijft. Wat erbij gekomen is, is een tweede
    BRON voor diezelfde lijst, voor precies het geval waarin de eerste niet
    bestaat: een node die de server niet over IP bereikt of waarvoor hij geen
    weblogin heeft, kan ``GET /api/cfg`` niet beantwoorden en stuurt zijn tabel
    daarom mee met zijn instellingenronde.

    Zonder dit zou de schrijfweg over MQTT bestaan zonder bruikbaar te zijn: de
    site zou de risicoklasse van een parameter niet kennen, bij twijfel de
    zwaarste aannemen (zie ``risk_of``) en dus alles blokkeren.
    """
    route = route or cfg_route(rep)
    if not route["can"]:
        return {"ok": False, "error": "", "params": []}
    if route["transport"] == "mqtt":
        return spec_from_node(rep)
    return params(route["host"])


def choices(spec: dict) -> list[str]:
    """De toegestane woorden van een enum, of een lege lijst.

    Zodat het sjabloon een keuzelijst kan tekenen met precies die woorden. Een
    invoerveld waarin je een ongeldige waarde kúnt typen is een invoerveld dat
    een node kan breken, dus waar de firmware een lijst geeft hoort er ook een
    lijst te staan.
    """
    raw = str(spec.get("choices") or "")
    return [c for c in raw.split("|") if c]


def _check(spec: dict, value: str) -> str:
    """De grenzen van de node hier alvast toepassen. Lege string = in orde.

    Dit is de beleefdheid, niet de beveiliging: het scheelt een netwerkronde en
    het geeft een fout die naast het invoerveld past. De controle die telt staat
    in de firmware, en die draait hoe dan ook.
    """
    kind = str(spec.get("kind") or "")
    if kind == "bool":
        return "" if value in ("on", "off") else "moet on of off zijn"
    if kind == "enum":
        allowed = choices(spec)
        return "" if value in allowed else f"moet een van deze zijn: {', '.join(allowed)}"
    if kind == "radio":
        parts = value.replace(",", " ").split()
        if len(parts) != 4:
            return "moet vier waarden zijn: freq bw sf cr"
        try:
            freq, bw, sf, cr = (float(p) for p in parts)
        except ValueError:
            return "alle vier de waarden moeten getallen zijn"
        if not 150 <= freq <= 2500:
            return f"frequentie {freq:g} ligt buiten 150-2500 MHz"
        if not 7 <= bw <= 500:
            return f"bandbreedte {bw:g} ligt buiten 7-500 kHz"
        if not (5 <= sf <= 12 and sf == int(sf)):
            return "spreading factor moet een geheel getal 5-12 zijn"
        if not (5 <= cr <= 8 and cr == int(cr)):
            return "coding rate moet een geheel getal 5-8 zijn"
        return ""
    if kind == "text":
        if not value:
            return "mag niet leeg zijn"
        if any(ord(c) < 0x20 for c in value):
            return "mag geen stuurtekens bevatten"
        bad = [c for c in "[]\\:,?*" if c in value]
        if bad:
            return f"mag deze tekens niet bevatten: {' '.join(bad)}"
        return ""

    try:
        num = float(value.replace(",", ".").strip())
    except ValueError:
        return "moet een getal zijn"
    lo, hi = float(spec.get("lo", 0)), float(spec.get("hi", 0))
    if not (lo <= num <= hi):
        return f"moet tussen {lo:g} en {hi:g} liggen"
    if kind == "int" and num != int(num):
        return "moet een geheel getal zijn"
    return ""


def confirmation_for(spec: dict, rep, confirm: str) -> str:
    """Is de bevestiging die erbij zit zwaar genoeg? Lege string = ja.

    Hier en niet alleen in het sjabloon, want een bevestiging die je met een
    aangepast formulier kunt overslaan is geen bevestiging maar een opmaakkeuze.
    De drempel hoort te staan op de plek die het verzoek werkelijk uitvoert.

    Drie zwaartes, oplopend met wat er misgaat als je de verkeerde regel raakt:
    niets, een uitdrukkelijk 'ja', en de naam van de node overtypen. Die laatste
    vangt een andere fout dan twijfel -- hij vangt de klik op de verkeerde node,
    en daar helpt een ja/nee-vraag niet tegen. Dezelfde afweging als bij de
    firmwarepagina, en om dezelfde reden.
    """
    risk = int(spec.get("risk") or RISK_PLAIN)
    if risk <= RISK_PLAIN:
        return ""
    if risk == RISK_WRITES:
        return "" if confirm.strip() == "ja" else "deze wijziging moet bevestigd worden"
    naam = str(_field(rep, "name") or "")
    if confirm.strip() == naam:
        return ""
    return (f"deze instelling kan de node onbereikbaar maken; typ de naam "
            f"({naam}) precies over om te bevestigen")


def risk_of(rep, key: str) -> int:
    """De risicoklasse van één parameter op déze node.

    Bestaat zodat de rechtencontrole vóór de schrijfactie op dezelfde klasse kan
    wegen als de bevestiging erna. Zonder dit zou het recht "mag schrijven" zijn
    en niet "mag dit soort wijziging", en dan is "wel de zendtijd bijstellen,
    niet aan de radio komen" een uitzondering in plaats van een rol.

    Bij twijfel de zwaarste klasse: kan de lijst niet opgehaald worden of kent
    de node de parameter niet, dan is dat geen reden om hem als ongevaarlijk te
    behandelen. Wie de sleutel niet herkent, weet ook niet wat hij aanricht.
    """
    route = cfg_route(rep)
    if not route["can"]:
        return RISK_CUTOFF
    listing = params_for(rep, route)
    if not listing["ok"]:
        return RISK_CUTOFF
    spec = next((p for p in listing["params"] if p.get("key") == key), None)
    if spec is None:
        return RISK_CUTOFF
    return int(spec.get("risk") or RISK_PLAIN)


def write(rep, key: str, value: str, confirm: str = "") -> dict:
    """Eén parameter zetten en teruggeven wat er ná afloop in de node staat.

    Het antwoord draagt ``asked`` en ``applied`` apart, en dat is geen
    omslachtigheid maar de kern van deze functie. MeshCore antwoordt "OK" op
    dingen die het niet werkelijk heeft overgenomen: ``set lat`` is een kale
    atof() die van een tikfout 0.0 maakt, en ``advert.interval`` wordt bewaard
    als minuten/2 in één byte, zodat 61 als 60 terugkomt. Wie hier "OK" zou
    teruggeven, zou dezelfde onwaarheid vertellen als de oude OTA-weg deed.

    Eén ingang voor alle drie de vervoermiddelen, en dat is met opzet. Alles wat
    een schrijfactie tegenhoudt -- de route, de lijst van de node, de grenzen, de
    bevestiging -- staat hierboven en gebeurt dus onverkort, of het commando nu
    over een netwerkkabel, over de broker of over de lucht verdergaat. Pas als er
    niets meer te weigeren valt, gaan de drie wegen uiteen. Een tweede functie
    voor een van de drie zou een tweede plek zijn waar een drempel kan ontbreken,
    en dat is precies het soort fout dat je pas ontdekt als er een node stil is.
    """
    route = cfg_route(rep)
    out = {"ok": False, "step": "", "msg": "", "key": key, "asked": value,
           "applied": "", "exact": False, "reboot": False,
           "transport": route["transport"], "busy": False,
           "why": route.get("why", "")}

    # Vóór alles, en met opzet vóór de route: dit is geen eigenschap van de weg
    # maar van de handeling. Deze parameter wordt van afstand niet gezet, en dan
    # doet het er niet toe of er een weg naartoe is. Zie NO_REMOTE.
    if key in NO_REMOTE:
        out.update(step="afstand", msg=NO_REMOTE_REASON)
        return out

    if not route["can"]:
        out.update(step="route", msg=f"deze node kan geen instelling ontvangen "
                                     f"({route['blocker']})")
        return out

    listing = params_for(rep, route)
    if not listing["ok"]:
        out.update(step="lijst", msg=listing["error"])
        return out

    spec = next((p for p in listing["params"] if p.get("key") == key), None)
    if spec is None:
        out.update(step="sleutel",
                   msg="deze node biedt die parameter niet aan om van afstand te zetten")
        return out

    # Het plafond van dit vervoermiddel. Alleen het cmd-topic heeft er een dat
    # lager ligt dan de zwaarste klasse; zie MQTT_MAX_RISK voor waarom, en let
    # erop dat de firmware dezelfde grens nog eens handhaaft. Deze weigering
    # staat hier zodat er niets vertrekt dat aan de overkant toch geweigerd
    # wordt -- en zodat de pagina kan zeggen wat er dan wél kan.
    risk = int(spec.get("risk") or RISK_PLAIN)
    if risk > int(route.get("max_risk") or RISK_CUTOFF):
        out.update(step="plafond", msg=(
            f"deze instelling kan de bereikbaarheid van de node afsnijden, en de "
            f"weg die nu beschikbaar is ({TRANSPORT_TEXT[route['transport']]}) "
            f"neemt zulke wijzigingen niet aan. Daar staat geen wachtwoord "
            f"tegenover en er is geen teruglezing in hetzelfde verzoek. Vul "
            f"MM_FW_NODE_USER/MM_FW_NODE_PASS in, of doe het op de beheerpagina "
            f"van de node zelf"))
        return out

    problem = _check(spec, value)
    if problem:
        out.update(step="waarde", msg=f"{key} {problem}")
        return out

    problem = confirmation_for(spec, rep, confirm)
    if problem:
        out.update(step="bevestiging", msg=problem)
        return out

    # 'radio' wordt bewaard maar pas bij een herstart actief. Het teruglezen toont
    # dus de nieuwe waarden terwijl de radio nog op de oude staat, en pas bij die
    # herstart blijkt of ze kloppen -- precies het geval waarin een node niet
    # terugkomt. De pagina hoort dat te zeggen, langs welke weg dan ook.
    out["reboot"] = bool(spec.get("reboot"))

    if route["transport"] == "mesh":
        _write_mesh(route, out)
    elif route["transport"] == "mqtt":
        _write_mqtt(rep, route, out)
    else:
        _write_ip(route, out)

    _remember(rep, spec, out)
    return out


def _write_ip(route: dict, out: dict) -> None:
    """De weg naar een node die de server zelf over IP bereikt.

    Synchroon, want aan de overkant is dit één aanroep van ``handleCommand()``
    en die is in tienden van seconden klaar.
    """
    body = urllib.parse.urlencode({"key": out["key"], "value": out["asked"]}).encode()
    try:
        with _open(route["host"], "/api/cfg", data=body) as resp:
            answer = json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        # Ook bij een fout antwoordt de node met JSON, en juist dan staat erin
        # welke stap faalde. Die tekst inslikken en "HTTP 400" tonen zou de fout
        # herhalen die dit hele ontwerp probeert weg te nemen.
        try:
            answer = json.loads(exc.read())
        except (ValueError, OSError):
            out.update(step=f"http_{exc.code}", msg=f"node antwoordde HTTP {exc.code}")
            return
    except (urllib.error.URLError, OSError, ValueError, TimeoutError) as exc:
        out.update(step="verbinding",
                   msg=f"geen antwoord van de node ({type(exc).__name__})")
        return

    out.update(
        ok=bool(answer.get("ok")),
        step=str(answer.get("step") or ""),
        msg=str(answer.get("msg") or ""),
        applied=str(answer.get("applied") or ""),
        exact=bool(answer.get("exact")),
    )


# --- de weg over het cmd-topic ------------------------------------------------
#
# De uitslag komt hier niet terug in het antwoord op het verzoek -- er IS geen
# antwoord op een MQTT-publicatie -- maar in het eerstvolgende
# statistiekenbericht van de node, dat hij meteen verstuurt. Die berichten komen
# binnen op de ingest-draad, dus is er één plek nodig waar die draad zijn vondst
# neerlegt en waar de webverzoekdraad hem ophaalt.
#
# In het geheugen en niet in de databank, en dat is een keuze met een prijs. Wat
# het kost: na een herstart van de site is de laatste uitslag weg. Wat het
# oplevert: geen tabel, geen opruiming, geen bewaartermijn voor iets dat hooguit
# een halve minuut interessant is. En wat er WEL bewaard blijft is het enige dat
# er blijvend toe doet -- de teruggelezen waarde gaat via ``_remember`` de
# gewone instellingentabel in, precies zoals bij de andere twee wegen.
_cfgset_lock = threading.Lock()
_cfgset: dict[str, dict] = {}

# Zoveel nodes onthouden we een uitslag van. Ruim boven elk mesh dat deze site
# bedient, en het is er alleen om te voorkomen dat een broker vol vreemde nodes
# dit geheugen laat groeien: dit vult zich van het stats-topic, en dat is geen
# invoer van de eigenaar.
CFGSET_MAX_NODES = 200


def note_cfgset(node: str, job: dict) -> None:
    """De uitslag van een schrijfactie die met een statistiekenbericht meekwam.

    Aangeroepen vanuit de ingest en niet andersom, zodat deze module niets van
    het bericht hoeft te weten behalve welke node het stuurde.
    """
    sleutel = (node or "").lower().strip()
    if not sleutel or not isinstance(job, dict):
        return
    with _cfgset_lock:
        if sleutel not in _cfgset and len(_cfgset) >= CFGSET_MAX_NODES:
            # Oudste eruit. Een dict houdt invoegvolgorde, dus dit is de node
            # die het langst niets nieuws meldde.
            _cfgset.pop(next(iter(_cfgset)))
        _cfgset[sleutel] = dict(job)


def cfgset_state(node: str) -> dict:
    """De laatst gemelde schrijfactie van deze node, of een leeg antwoord."""
    with _cfgset_lock:
        return dict(_cfgset.get((node or "").lower().strip()) or {})


def _write_mqtt(rep, route: dict, out: dict) -> None:
    """De weg over het cmd-topic van de node zelf.

    Publiceren en dan wachten. Het wachten is kort en dat mag: de node zet en
    leest terug in dezelfde lus en publiceert daarna meteen, dus dit is een
    kwestie van seconden. Blijft het uit, dan is het antwoord ``geen_antwoord``
    -- dezelfde naam als bij de LoRa-weg, en om dezelfde reden. Een publicatie
    die vertrokken is, kan gewoon zijn uitgevoerd; "mislukt" zou iemand laten
    denken dat er niets gebeurd is.

    Herkend aan ``seq`` en niet aan "er staat nu iets": de node telt zijn
    schrijfacties door en houdt de laatste vast, dus zonder dat nummer zou de
    uitslag van vorige week doorgaan voor de uitslag van nu.
    """
    from . import mqtt_ingest

    node = str(route.get("node") or _field(rep, "source_prefix") or "").lower().strip()
    vorige = int(cfgset_state(node).get("seq") or 0)

    if not mqtt_ingest.publish_command(node, "set",
                                       setting=(out["key"], out["asked"])):
        # Met zekerheid niets veranderd: het bericht heeft deze machine niet
        # verlaten. Zelfde onderscheid als bij de LoRa-weg, waar dit
        # 'niet_verstuurd' heet -- de geruststellende helft van 'er ging iets mis'.
        out.update(step="niet_verstuurd", msg=(
            "er is niets vertrokken -- de brokerverbinding weigerde de publicatie. "
            "Op de node is dus met zekerheid niets veranderd; probeer het opnieuw"))
        return

    deadline = time.monotonic() + MQTT_WAIT_S
    job = {}
    while True:
        time.sleep(MQTT_POLL_S)
        job = cfgset_state(node)
        if int(job.get("seq") or 0) > vorige:
            break
        if time.monotonic() >= deadline:
            out.update(step="geen_antwoord", msg=(
                "het commando is naar de broker vertrokken, maar de node heeft er "
                "nog geen uitslag over gepubliceerd. Of hij het heeft uitgevoerd is "
                "van hieraf niet te zien; hij kan slapen op zijn stroombudget. "
                "Herlaad deze pagina, of vraag een statusbericht op"))
            return

    # De node meldt 'ok' als de 'set' niet geweigerd werd, en 'applied' als wat
    # er ná afloop werkelijk in staat -- dezelfde betekenis en dezelfde velden
    # als /api/cfg, zodat er hier niets te vertalen valt.
    out.update(
        ok=bool(job.get("ok")),
        step="" if job.get("ok") else "node",
        msg=str(job.get("msg") or ""),
        applied=str(job.get("applied") or ""),
        exact=bool(job.get("exact")),
    )


# Wat een afgeronde LoRa-schrijfactie kan opleveren, en wat die uitkomst betekent
# voor iemand die ernaar kijkt. Ze staan hier en niet in het sjabloon omdat ze
# ook in het serverlogboek en in een API-antwoord terechtkomen, en een zin die op
# twee plaatsen anders luidt is een zin waar niemand meer op vertrouwt.
MESH_STEPS = {
    "": "",
    "bezig": "de monitor is er nog mee bezig",
    # De gunstigste manier waarop deze weg kan falen, en die verdient een eigen
    # zin: er is niets vertrokken, dus er is met zekerheid niets veranderd. Dat
    # op één hoop gooien met 'geen antwoord' zou de rustgevende uitkomst laten
    # klinken als de verontrustende, of andersom.
    "niet_verstuurd": ("er is niets de lucht in gegaan -- de login bleef "
                       "onbeantwoord of de pakketpool van de monitor zat vol. "
                       "Op de node is dus met zekerheid niets veranderd; "
                       "probeer het opnieuw"),
    # De nieuwe uitkomst, en de reden dat deze tabel bestaat. Niet 'mislukt':
    # het commando IS verstuurd en of het is aangekomen weten we niet.
    "geen_antwoord": ("het commando is over LoRa vertrokken, maar de node heeft er "
                      "niet op geantwoord. Of hij het heeft uitgevoerd is van hieraf "
                      "niet te zien; een nieuwe uitleesronde is de enige manier om "
                      "erachter te komen"),
    "geen_teruglezing": ("de node antwoordde op het zetten, maar op het teruglezen "
                         "niet. Wat er nu werkelijk in staat is dus niet vastgesteld"),
    "node": "de node weigerde het commando",
}


def mesh_state(monitor_host: str) -> dict:
    """De lopende of laatst afgeronde schrijfactie van deze monitor.

    Bestaat omdat de uitslag niet hier bewaard wordt maar op de monitor. Dat
    scheelt niet alleen een opdrachtenlijst in de server -- het is ook de
    eerlijkere plaats: de node die het werk deed is de enige die weet hoe het
    afliep, en een kopie hier zou na een herstart van de site verdwenen zijn
    terwijl de handeling wel degelijk plaatsvond.
    """
    uit = {"ok": False, "error": "", "job": {}}
    if not (monitor_host or "").strip():
        uit["error"] = "geen beheeradres voor de monitor"
        return uit
    try:
        with _open(monitor_host, "/api/moncfg") as resp:
            uit["job"] = json.loads(resp.read())
        uit["ok"] = True
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            uit["error"] = ("deze monitor draait firmware zonder /api/moncfg "
                            "(ouder dan 2.4.0)")
        elif exc.code == 401:
            uit["error"] = "aanmelden geweigerd door de monitor"
        else:
            uit["error"] = f"monitor antwoordde HTTP {exc.code}"
    except (urllib.error.URLError, OSError, ValueError, TimeoutError) as exc:
        uit["error"] = f"monitor niet bereikbaar ({type(exc).__name__})"
    return uit


def _write_mesh(route: dict, out: dict) -> None:
    """De weg naar een node die alleen over LoRa te bereiken is.

    Vragen aan de monitor, en dan wachten. Het wachten gebeurt hier en niet in de
    browser met een verversende pagina, omdat een schrijfactie meestal binnen
    tien seconden klaar is en het antwoord dan meteen op het scherm hoort te
    staan. Duurt het langer dan ``MESH_WAIT_S``, dan zegt de pagina dat het nog
    loopt in plaats van een halve minuut te blijven hangen -- en omdat de uitslag
    op de monitor blijft staan, laat een herlading hem alsnog zien.

    Wat er níét gebeurt: opnieuw proberen. Een schrijfactie die stil bleef, is
    misschien wel uitgevoerd, en hem herhalen is dan de tweede keer hetzelfde
    doen op een node die je niet kunt nakijken.
    """
    body = urllib.parse.urlencode({"key": route["target"], "param": out["key"],
                                   "value": out["asked"]}).encode()
    try:
        with _open(route["host"], "/api/moncfg", data=body,
                   timeout=MESH_START_TIMEOUT_S) as resp:
            resp.read()
    except urllib.error.HTTPError as exc:
        # De monitor antwoordt ook bij een weigering met JSON, en juist dan staat
        # erin waarom: een sleutel die hij niet monitort, een uitleesronde die
        # loopt, te kort na de vorige schrijfactie. Dat is de nuttigste zin die
        # er te geven valt, dus die blijft staan zoals hij is.
        try:
            answer = json.loads(exc.read())
        except (ValueError, OSError):
            out.update(step=f"http_{exc.code}", msg=f"monitor antwoordde HTTP {exc.code}")
            return
        out.update(step=str(answer.get("step") or "monitor"),
                   msg=str(answer.get("msg") or f"monitor weigerde (HTTP {exc.code})"))
        return
    except (urllib.error.URLError, OSError, ValueError, TimeoutError) as exc:
        out.update(step="verbinding",
                   msg=f"geen antwoord van de monitor ({type(exc).__name__})")
        return

    deadline = time.monotonic() + MESH_WAIT_S
    job = {}
    while True:
        time.sleep(MESH_POLL_S)
        state = mesh_state(route["host"])
        if not state["ok"]:
            out.update(step="verbinding", msg=state["error"])
            return
        job = state["job"] or {}
        if not job.get("busy"):
            break
        if time.monotonic() >= deadline:
            out.update(busy=True, step="bezig", msg=(
                f"de monitor is er nog mee bezig. Twee commando's over LoRa duren "
                f"tot anderhalve minuut; herlaad deze pagina voor de uitslag"))
            return

    step = str(job.get("step") or "")
    out.update(
        ok=bool(job.get("ok")),
        step=step,
        msg=MESH_STEPS.get(step, str(job.get("end") or "")),
        applied=str(job.get("applied") or ""),
        exact=bool(job.get("exact")),
    )


def _remember(rep, spec: dict, out: dict) -> None:
    """Wat er nu in de node staat ook hier vastleggen.

    Zonder dit blijft de kolom 'Nu' op de beheerpagina de oude waarde tonen
    naast een melding dat het gelukt is, tot de volgende uitleesronde. Over IP is
    dat hooguit verwarrend; over LoRa is het erger, want daar kost zo'n ronde
    zendtijd op andermans band en gebeurt hij hooguit dagelijks.

    Wat teruggelezen is en niet wat gevraagd is -- dat is het hele punt van deze
    module. En een geheim wordt niet bewaard: de node geeft er '(verborgen)' voor
    terug, en die tekst in de tabel zetten zou een waarde suggereren.
    """
    if not (out["ok"] and out["applied"]) or spec.get("secret"):
        return
    rid = int(_field(rep, "id") or 0)
    if not rid:
        return
    db.upsert_cli_settings(rid, {out["key"]: out["applied"]}, prune=False)
    # De naam staat ook in onze eigen repeatertabel; die zou anders tot het
    # volgende statistiekbericht de oude blijven tonen.
    if out["key"] == "name":
        db.execute("UPDATE repeaters SET name=? WHERE id=?", (out["applied"][:64], rid))


# --- rechten van de monitor op zijn doelnode ---------------------------------
#
# Dit is het stuk waar de eerste opzet de plank missloeg, en het verschil is
# wezenlijk genoeg om het uit te schrijven.
#
# Er zijn TWEE soorten inloggegevens in dit project en ze horen op verschillende
# plaatsen:
#
#   server -> node, over HTTP      de beheerpagina van een node van onszelf
#                                  (/api/fw, /api/cfg, /api/mon). Die login houdt
#                                  de server, in de omgeving. Zonder is die weg
#                                  dicht -- en dat is het enige wat er dan dicht
#                                  is.
#   monitor -> doelnode, over LoRa  de CLI van een node van iemand anders. Die
#                                  rechten horen bij de MONITOR en niet bij de
#                                  server: die logt in, die voert het commando
#                                  uit, en die heeft er dus de rechten voor
#                                  nodig.
#
# De eerste opzet vroeg de server om inloggegevens voor het tweede geval, en dat
# is verkeerd om. De server hoeft de doelnode nooit te kennen; hij hoeft zijn
# eigen monitor te kunnen bereiken, en die monitor houdt (of heeft niet nodig)
# wat er voor de doelnode geldt.
#
# Twee manieren waarop een monitor binnenkomt, en de eerste verdient de voorkeur:
#
#   ACL         de eigenaar van de doelnode zette `setperm <monitor-pubkey> 3`.
#               Er is dan HELEMAAL GEEN wachtwoord: de monitor logt in met een
#               lege string en de overkant zoekt zijn sleutel op in de eigen
#               toegangslijst. Niemand geeft een wachtwoord uit handen, en de
#               andere eigenaar kan het aan zijn kant intrekken zonder ons iets
#               te vragen.
#   wachtwoord  de monitor kent het adminwachtwoord van de doelnode en bewaart
#               het in zijn eigen monitorlijst.
#
# Wat deze module met dat wachtwoord doet is doorgeven en vergeten. Het komt één
# keer binnen, gaat naar de monitor, en wordt hier niet bewaard -- niet in de
# databank, niet in een instelling, nergens. Dat kost iets (de site kan het niet
# tonen en niet opnieuw doorgeven zonder dat iemand het opnieuw intikt) en het
# levert het enige op wat hier telt: een gecompromitteerde website is geen
# sleutelbos. Zie docs/security.md, waar die afweging staat en waar ook staat wat
# er sinds de firmware-upgradeweg NIET meer waar is aan de oude belofte.

MON_MODE_ACL = "acl"
MON_MODE_PASSWORD = "password"
MON_MODE_UNKNOWN = "unknown"


def monitors(host: str) -> dict:
    """De monitorlijst van een node, zoals hij hem zelf rapporteert.

    Alleen wat de node vrijgeeft, en dat is met opzet niet het wachtwoord: de
    firmware meldt per regel ``pw`` als 0 of 1 -- of er een wachtwoord staat, niet
    welk. Precies genoeg om de modus te tonen en te weinig om er iets mee te
    kunnen, wat de juiste hoeveelheid is.
    """
    out = {"ok": False, "error": "", "entries": [], "heard": []}
    if not (host or "").strip():
        out["error"] = "geen beheeradres"
        return out
    try:
        with _open(host, "/api/mon") as resp:
            data = json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        out["error"] = ("aanmelden geweigerd door de node" if exc.code == 401
                        else f"node antwoordde HTTP {exc.code}")
        return out
    except (urllib.error.URLError, OSError, ValueError, TimeoutError) as exc:
        out["error"] = f"niet bereikbaar ({type(exc).__name__})"
        return out
    out.update(ok=True, entries=list(data.get("mon") or data.get("entries") or []),
               heard=list(data.get("heard") or []))
    return out


def rights_for(monitor_host: str, target_key: str) -> dict:
    """Hoe komt deze monitor binnen bij deze doelnode, en werkt dat?

    Het antwoord op de vraag waar iemand anders een half uur aan kwijt is. Een
    sweep die op stilte uitloopt heeft drie verschillende oorzaken die er van
    hieraf identiek uitzien, en de monitor weet genoeg om ze uit elkaar te
    houden:

    - de login werd nooit beantwoord én we horen de node niet -> buiten bereik;
    - de login werd nooit beantwoord maar we horen hem wel -> onze sleutel staat
      niet in zijn toegangslijst, of het wachtwoord klopt niet;
    - de login lukte en de commando's zwijgen -> we zijn binnen als lezer maar
      niet als beheerder. Dat is het verraderlijke geval: alles ziet er goed uit
      en er komt niets. `setperm <onze-pubkey> 3` of het adminwachtwoord.

    Die laatste staat zo uitgebreid in de firmware beschreven omdat hij daar
    gemeten is; hier wordt hij alleen doorverteld.
    """
    uit = {"ok": False, "error": "", "mode": MON_MODE_UNKNOWN, "known": False,
           "login_ok": False, "polls": 0, "oks": 0, "heard": False, "diagnosis": ""}
    lijst = monitors(monitor_host)
    if not lijst["ok"]:
        uit["error"] = lijst["error"]
        return uit

    sleutel = (target_key or "").lower()
    regel = next((e for e in lijst["entries"]
                  if str(e.get("k", "")).lower().startswith(sleutel[:12])), None)
    uit["heard"] = any(str(h.get("k", "")).lower().startswith(sleutel[:12])
                       for h in lijst["heard"])
    if regel is None:
        uit.update(ok=True, diagnosis="niet_gemonitord")
        return uit

    uit.update(ok=True, known=True,
               mode=MON_MODE_PASSWORD if regel.get("pw") else MON_MODE_ACL,
               login_ok=bool(regel.get("lr")),
               polls=int(regel.get("polls") or 0), oks=int(regel.get("oks") or 0))

    if uit["login_ok"] and uit["oks"] == 0 and uit["polls"] > 0:
        uit["diagnosis"] = "alleen_lezen"
    elif not uit["login_ok"] and uit["polls"] > 0:
        uit["diagnosis"] = "geen_toegang" if uit["heard"] else "buiten_bereik"
    elif uit["oks"] > 0:
        uit["diagnosis"] = "goed"
    else:
        uit["diagnosis"] = "nog_niet_geprobeerd"
    return uit


def push_monitor_password(monitor_host: str, target_key: str, password: str) -> dict:
    """Het wachtwoord van een doelnode aan de monitor geven. En dan vergeten.

    De server bewaart het niet -- niet hier, niet in de databank, nergens. Wat
    dat kost staat in de moduletekst hierboven; wat het oplevert is dat een
    inbraak op deze website geen wachtwoorden van andermans nodes oplevert.

    Een lege waarde is geen 'niets doen' maar een geldige opdracht: die wist het
    wachtwoord en zet de monitor terug op de ACL-weg, wat de aanbevolen manier
    is. Vandaar dat hier niet op leegte gecontroleerd wordt.
    """
    uit = {"ok": False, "error": ""}
    body = urllib.parse.urlencode({"act": "pass", "key": target_key,
                                   "pass": password}).encode()
    try:
        with _open(monitor_host, "/api/mon", data=body) as resp:
            resp.read()
        uit["ok"] = True
    except urllib.error.HTTPError as exc:
        uit["error"] = ("aanmelden geweigerd door de monitor" if exc.code == 401
                        else f"monitor antwoordde HTTP {exc.code}")
    except (urllib.error.URLError, OSError, ValueError, TimeoutError) as exc:
        uit["error"] = f"monitor niet bereikbaar ({type(exc).__name__})"
    return uit
