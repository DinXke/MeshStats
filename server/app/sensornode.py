"""De derde weg: een node die zijn eigen API over IP aanbiedt.

MeshManager kende tot nu toe twee soorten nodes en beide zijn een soort MQTT.

**Onze repeaterfirmware.** Hij publiceert zelf op de broker, leest zijn eigen
CLI uit, en neemt opdrachten aan op zijn ``cmd``-topic. Dat is de node waar de
site rond gebouwd is, en hij heet full managed.

**Een node achter een monitor.** Hij publiceert niets; een repeater met onze
firmware logt op hem in over LoRa en stuurt zijn cijfers door. Dat kost zendtijd
op andermans band en het duurt tientallen seconden per handeling, maar het werkt
voor een node die nooit iets van ons krijgt.

Hier komt een derde bij, en hij is geen variant op de eerste twee: **een node
die zelf een HTTP-API aanbiedt over IP.** Een MeshUptime-sensornode doet dat --
``/status.json``, ``/cfg.json``, ``/acl.json`` en ``POST /cli`` achter dezelfde
HTTP-basislogin die de firmware-schrijfweg al gebruikt.

Waarom dat een eigen weg is en niet ``ota_host`` erbij
------------------------------------------------------
``ota_host`` betekent één ding in dit project: daar staat de beheerpagina van
ONZE repeaterfirmware, met ``/api/fw``, ``/api/cfg`` en ``/api/mon`` erachter.
Wie het adres van een sensornode in dat veld zet, krijgt een site die een
firmware-image aanbiedt aan een node waarvan wij de bouwomgeving niet beheren
(verkeerd board = kapotte node) en die ``GET /api/cfg`` probeert op een pad dat
er niet is. Vandaar ``repeaters.sensor_host``: een tweede adres met een tweede
betekenis, in plaats van één veld met twee.

Waarom de node die dit aanbiedt volledig beheerd is
---------------------------------------------------
De niveaus in ``commanding.py`` zijn een WAARNEMING: ze volgen uit wat er
binnenkomt en er is nergens een knop om ze te zetten. Deze weg past daar zonder
uitzondering in, en hij hoort bij het hoogste niveau -- niet uit vriendelijkheid
maar omdat hij op elk punt dat het niveau meet minstens zo sterk is als de
MQTT-weg: een geauthenticeerde tegenpartij, synchroon, met de teruglezing in
hetzelfde antwoord, in tienden van seconden, en zonder een derde partij die
ervoor betaalt.

Wat deze weg NIET is
--------------------
Hij is **niet het mesh**. Hij loopt over WiFi, en dat is geen theoretische
kwetsbaarheid: op batterij is de node in de meting van 19 augustus 2026
(``docs/meting-voeding-2026-08-19.log`` in MeshUptime) eerst met tussenpozen en
daarna veertien pollingen op rij onbereikbaar geweest over precies dit pad,
terwijl hij over LoRa gewoon bestond. Het mesh is de weg die daarvoor bedoeld
is, en die werkt op dit ogenblik niet: de uitvraagronde begint met een login die
``LOGIN_NOANSWER`` krijgt, en de ronde is bovendien repeater-vormig terwijl een
sensornode ``REQ_TYPE_GET_STATUS`` niet implementeert en op een burenverzoek
letterlijk "not supported" antwoordt.

Dat staat met zoveel woorden op de nodepagina en het staat hier, omdat het de
enige eerlijke lezing is van wat deze module oplevert: volledig beheer, zolang
de WiFi er is.

Wat hier NIET in staat
----------------------
**Geen tweede schrijfweg.** ``nodeconfig.write()`` blijft de enige plek waar een
instelling gezet wordt, met al zijn drempels: de weigeringslijst, de
parameterlijst, de grenzen, de risicoklassen, de bevestiging, de rechten en de
terugleescontrole. Deze module levert er een vierde VERVOERMIDDEL bij
(``sensor``) en geen tweede ingang. Zie ``_write_sensor`` in ``nodeconfig.py``.

**Geen tweede radioregel.** Wat er van afstand nooit gezet wordt staat in
``nodeconfig.NO_REMOTE``, en de weigering hieronder is dezelfde lijst en niet een
kopie ervan. Zie ``radio_refusal()``.

**Geen tweede klokoordeel.** Of deze machine mag zeggen hoe laat het is, beslist
``clocksync.check_clock()`` -- dezelfde functie die de dagelijkse ronde
gebruikt. Deze module stuurt het commando, ze velt het oordeel niet.
"""

from __future__ import annotations

import json
import logging
import threading
import time
import urllib.error
import urllib.parse

from . import clocksync, config, db, firmware, nodeconfig

log = logging.getLogger("meshmanager.sensornode")

# --- wanneer er gepolst wordt -------------------------------------------------
#
# Hetzelfde tempo als de bestaande polling: de Home Assistant-integratie stuurt
# elke 300 s een volledige snapshot (FULL_PUSH_INTERVAL in
# homeassistant/custom_components/meshmanager/const.py), en dat is ook de
# hartslag waarop deze server rekent -- ``HEARTBEAT_MIN`` staat op 5 minuten, dus
# een reeks die trager binnenkomt krijgt gaten in zijn grafiek en een reeks die
# sneller binnenkomt levert punten op die niemand tekent.
#
# Sneller is technisch mogelijk (het is één HTTP-verzoek over het lokale net en
# er is geen zendtijd bij betrokken), en toch staat het er niet: een node die
# ondertussen een radio bedient krijgt elk verzoek door dezelfde loop() als het
# meshwerk, en de eigen pagina van de node haalt zijn status al elke vijf
# seconden op zolang iemand meekijkt. Wie een fijnere resolutie wil, zet dit
# lager én de hartslag mee -- twee getallen die bij elkaar horen.
INTERVAL_S = max(30, int(config.env("SENSOR_POLL_S", "300") or 300))

ENABLED = config.env("SENSOR_POLL_ENABLED", "1").strip().lower() not in (
    "0", "false", "no", "nee", "off", "")

# Kort, want dit is het lokale net en er hangt een webverzoek achter te wachten
# als de pagina de status ophaalt. Een node die er is antwoordt in tienden van
# seconden; een node die weg is, is weg.
TIMEOUT_S = max(2, int(config.env("SENSOR_TIMEOUT_S", "8") or 8))

# Wachten na het opstarten, om dezelfde reden als bij clocksync en sweepsched:
# tijdens het opstarten is het netwerk in een container nog niet
# noodzakelijkerwijs klaar, en een eerste ronde die daarop strandt zet een fout
# in het logboek die geen fout is.
FIRST_RUN_DELAY_S = 20

# --- de woordenschat van /status.json ----------------------------------------
#
# Deze woorden komen uit WebTask::appendMonitors() in de MeshUptime-firmware en
# staan hier bij elkaar omdat ze een AFSPRAAK zijn en geen tekst om te tonen. Ze
# vertalen naar precies twee dingen: is dit kanaal op, en heeft het een tijd.
#
# 'op' is de enige toestand die "op" betekent. Dat is niet strengheid: de
# firmware van de sensornode stuurt over LoRa ``LPP_SWITCH 1`` uitsluitend als
# ``seeded && up``, en 'pauze', 'stil' en '?' zijn precies de gevallen waarin dat
# niet geldt. Ze hier als 0 lezen is dus geen verlies van nuance -- het is wat de
# andere weg ook doet, en die twee moeten hetzelfde getal opleveren of de
# metricnaam betekent twee dingen.
ST_UP = "op"

# Een vast kanaal meldt geen op/neer maar een toestand in woorden, en die twee
# paren zijn de enige die er voorkomen: netvoeding/batterijvoeding (aan|uit) en
# wifi (online|weg).
ST_TRUE = ("aan", "online")
ST_FALSE = ("uit", "weg")

KIND_FIXED = "vast"
KIND_PING = "ping"
KIND_PUSH = "gemeld"


# --- de netwerkgrens ----------------------------------------------------------
#
# Eén plek waar er een socket opengaat, zodat een test hem in zijn geheel kan
# vervangen -- dezelfde afspraak als bij ``firmware.push`` en
# ``mqtt_ingest.publish_command``, en om dezelfde reden.

def _json(host: str, path: str, timeout: int | None = None) -> dict:
    """Eén GET naar de node, met zijn antwoord of met een reden waarom niet.

    ``{"ok": bool, "error": str, "data": dict}``. De fouttekst is Nederlands en
    voor het scherm bedoeld: dit is de enige plek die weet of het een 401, een
    404 of een dode socket was, en dat verschil is precies wat iemand een half
    uur kost als de pagina het samenvat als "onbereikbaar".
    """
    out = {"ok": False, "error": "", "data": {}}
    if not (host or "").strip():
        out["error"] = "geen adres voor de eigen API van deze node"
        return out
    if not firmware.NODE_USER:
        out["error"] = ("geen weblogin voor de nodes (MM_FW_NODE_USER/"
                        "MM_FW_NODE_PASS)")
        return out
    try:
        with nodeconfig._open(host, path,
                              timeout=timeout or TIMEOUT_S) as resp:
            out["data"] = json.loads(resp.read())
        out["ok"] = True
    except firmware.TargetRefused as exc:
        # Vóór de andere gevallen, want dit is geen storing: er is niets
        # geprobeerd. "Niet bereikbaar" zou iemand naar de node laten kijken voor
        # een grens die op deze server staat. Zie firmware.check_target.
        out["error"] = str(exc)
    except urllib.error.HTTPError as exc:
        if exc.code == 401:
            out["error"] = "aanmelden geweigerd door de node"
        elif exc.code == 404:
            out["error"] = (f"deze node kent {path} niet; dit is geen node met "
                            f"een eigen sensor-API")
        elif exc.code == 503:
            # De node leeft en zegt zelf wat er ontbreekt. Die tekst is bruikbaar
            # ("meshlaag niet gekoppeld: voeg in main.cpp ... toe") en hem
            # inslikken voor "HTTP 503" zou de fout herhalen die dit project
            # elders al een paar keer heeft opgeruimd.
            try:
                out["error"] = exc.read().decode("utf-8", "replace").strip()[:200]
            except OSError:
                out["error"] = "de node meldt dat een laag niet gekoppeld is"
        else:
            out["error"] = f"node antwoordde HTTP {exc.code}"
    except (urllib.error.URLError, OSError, ValueError, TimeoutError) as exc:
        out["error"] = f"niet bereikbaar ({type(exc).__name__})"
    return out


def status(host: str, timeout: int | None = None) -> dict:
    """``GET /status.json``: de toestand plus de volledige kanaalkaart."""
    return _json(host, "/status.json", timeout)


def cfg(host: str, timeout: int | None = None) -> dict:
    """``GET /cfg.json``: de hele stand van NodePrefs plus de gebakken radiowaarden."""
    return _json(host, "/cfg.json", timeout)


def acl(host: str, timeout: int | None = None) -> dict:
    """``GET /acl.json``: de toegangslijst en de gehoorde buren.

    Hoort hier omdat het het antwoord bevat op de vraag waarom de MESH-weg naar
    deze node niet werkt. Een monitor die inlogt en ``LOGIN_NOANSWER`` krijgt,
    krijgt dat omdat zijn sleutel niet in deze lijst staat of omdat hij het
    wachtwoord niet heeft -- en dat valt van hieraf niet te zien, tenzij je de
    lijst opvraagt.
    """
    return _json(host, "/acl.json", timeout)


def cli(host: str, cmd: str, timeout: int | None = None) -> dict:
    """``POST /cli``: één CLI-opdracht, en de tekst die de node teruggeeft.

    **Er gaat nooit een ``confirm`` mee.** De node kent er twee -- ``confirm=radio``
    voor ``set radio``/``set freq`` en ``confirm=erase`` voor ``erase`` -- en
    beide zijn er om te voorkomen dat een losse fetch, een bookmarklet of een
    voorgeladen link erlangs komt. Die parameter meesturen zou dat slot van
    binnenuit openen, en dat is precies wat deze functie niet mag kunnen. Vandaar
    dat ze geen parameter heeft om hem in te zetten: een weglating die je niet
    kunt vergeten is beter dan een voorwaarde die je kunt omzeilen.

    De opdracht wordt ONGEWIJZIGD doorgegeven, op één weigering na (zie
    ``radio_refusal``). Er wordt niets aangevuld of "verbeterd": wat er verstuurd
    is, is wat er in het antwoord staat, en dat is de enige manier waarop het
    logboek een eerlijke weergave is van wat er gebeurd is.
    """
    out = {"ok": False, "error": "", "reply": "", "cmd": cmd}
    refusal = radio_refusal(cmd)
    if refusal:
        out["error"] = refusal
        return out
    if not (host or "").strip():
        out["error"] = "geen adres voor de eigen API van deze node"
        return out
    if not firmware.NODE_USER:
        out["error"] = "geen weblogin voor de nodes (MM_FW_NODE_USER/MM_FW_NODE_PASS)"
        return out

    body = urllib.parse.urlencode({"cmd": cmd}).encode()
    try:
        with nodeconfig._open(host, "/cli", data=body,
                              timeout=timeout or TIMEOUT_S) as resp:
            out["reply"] = resp.read().decode("utf-8", "replace").strip()
        out["ok"] = True
    except firmware.TargetRefused as exc:
        out["error"] = str(exc)
    except urllib.error.HTTPError as exc:
        # De node antwoordt bij een weigering met platte tekst waarin de REDEN
        # staat -- 403 voor de privésleutel, 409 voor een ontbrekende
        # bevestiging, 400 voor een te lange opdracht. Die tekst is het enige
        # bruikbare aan zo'n antwoord.
        try:
            out["reply"] = exc.read().decode("utf-8", "replace").strip()
        except OSError:
            out["reply"] = ""
        out["error"] = out["reply"] or f"node antwoordde HTTP {exc.code}"
    except (urllib.error.URLError, OSError, ValueError, TimeoutError) as exc:
        out["error"] = f"geen antwoord van de node ({type(exc).__name__})"
    return out


# --- de radioregel ------------------------------------------------------------

def radio_refusal(cmd: str) -> str:
    """De weigering uit ``nodeconfig.NO_REMOTE``, toegepast op een CLI-regel.

    Dezelfde lijst en geen tweede. ``nodeconfig.write()`` toetst op de SLEUTEL en
    komt daar niet langs; deze functie bestaat omdat er langs deze weg ook losse
    opdrachten vertrekken (een advert, de klok, een regio) en een van die
    opdrachten zou in theorie ``set freq 868.0`` kunnen zijn. Dan hoort de
    weigering hier te vallen en niet aan de overkant, waar hij een 409 wordt met
    de uitleg dat je ``confirm=radio`` mee moet sturen -- en dat is de laatste
    aanwijzing die iemand hier nodig heeft.

    Op de EXACTE sleutel en niet op een voorvoegsel, en dat is het hele werk van
    deze functie. ``set radio.rxgain on`` is geen frequentie: het zet de
    ontvangstversterking, en dat maakt een node hooguit dover terwijl hij op
    hetzelfde kanaal blijft -- dezelfde asymmetrie die 'tx' wél toestaat. Een
    voorvoegseltoets zou hem als "radio" lezen en weigeren, en dan zou de regel
    iets anders gaan betekenen dan ze zegt. De node zelf toetst met exact
    dezelfde grens (``cmdIs(cmd, "set radio ")``, met een spatie).
    """
    parts = str(cmd or "").strip().split()
    if len(parts) < 2 or parts[0] != "set":
        return ""
    if parts[1] not in nodeconfig.NO_REMOTE:
        return ""
    return (f"'{parts[1]}' wordt van afstand niet gezet. "
            + nodeconfig.NO_REMOTE_REASON)


# --- welke instellingen deze node laat zetten ---------------------------------
#
# WAAROM HIER EEN TABEL STAAT, terwijl nodeconfig.py bovenaan met zoveel woorden
# zegt dat er geen parameterlijst in de server hoort. Die regel is er om te
# voorkomen dat de site iets aanbiedt wat de node weigert, en ze staat hier nog
# steeds -- alleen kan de bron ervan hier niet de node zijn.
#
# Onze repeaterfirmware PUBLICEERT zijn tabel: ``GET /api/cfg`` geeft precies
# CFG_PARAMS terug, met de grenzen en de risicoklassen erin, en dat is dan ook
# waar het formulier voor zo'n node uit opgebouwd wordt. Een sensornode doet dat
# niet: ``/cfg.json`` geeft WAARDEN en geen keuring, en de koppeling van veld
# naar CLI-opdracht zit in de JavaScript van zijn eigen pagina (de tabel ``GRP``
# in WebTask.cpp). Er is dus geen machineleesbare lijst om op te halen.
#
# Wat er wél is, is dat beide nodes DEZELFDE CLI draaien: MeshCore's CommonCLI,
# met dezelfde sleutels en dezelfde grenzen. De tabel hieronder is daarom geen
# nieuwe lijst maar een letterlijke spiegel van CFG_PARAMS in
# firmware/examples/simple_repeater/MeshManagerNet.cpp -- en
# test_sensornode.py leest die C-tabel uit de broncode en vergelijkt hem regel
# voor regel met deze. Twee plaatsen die het eens moeten zijn, met een test die
# het merkt zodra ze het niet meer zijn; dat is het beste dat hier te krijgen is.
#
# Twee sleutels uit CFG_PARAMS staan er met opzet NIET in, en de reden is
# dezelfde voor beide: er is geen veld in ``/cfg.json`` om ze in terug te lezen.
# Zonder teruglezing zou deze module "gelukt" moeten melden op het woord van de
# node, en dat is precies wat nodeconfig.py bestaat om niet te doen -- MeshCore
# antwoordt "OK" op dingen die het niet werkelijk heeft overgenomen. Zie
# ``NO_READBACK`` hieronder.
#
# 'radio', 'freq', 'bw', 'sf' en 'cr' staan er niet in en zouden er ook niet in
# mogen: ze staan in NO_REMOTE en ``nodeconfig.write()`` weigert ze vóór er een
# weg gekozen wordt.

# CLI-sleutel -> sleutel in /cfg.json. De enige tabel hier die niet uit de
# firmware te spiegelen valt: de namen in /cfg.json zijn afgekort voor de
# JSON-buffer van de node ("advint" voor "advert.interval") en die afkortingen
# bestaan nergens anders.
CFG_KEYS = {
    "name": "name",
    "lat": "lat",
    "lon": "lon",
    "owner.info": "owner",
    "advert.interval": "advint",
    "flood.advert.interval": "fadvint",
    "rxdelay": "rxdelay",
    "txdelay": "txdelay",
    "direct.txdelay": "dtxdelay",
    "af": "af",
    "flood.max": "fmax",
    "flood.max.unscoped": "fmaxuns",
    "flood.max.advert": "fmaxadv",
    "int.thresh": "intthr",
    "agc.reset.interval": "agc",
    "multi.acks": "multiack",
    "path.hash.mode": "hashmode",
    "loop.detect": "loopd",
    "cad": "cad",
    "adc.multiplier": "adcmult",
    "tx": "tx",
    "repeat": "repeat",
    "allow.read.only": "rdonly",
    "radio.rxgain": "rxgain",
    "radio.fem.rxgain": "femrx",
}

# Sleutels die in CFG_PARAMS staan en hier met opzet niet aangeboden worden,
# met de reden. De test eist dat elke sleutel uit de firmwaretabel óf in
# CFG_KEYS óf hier staat -- zodat een nieuwe parameter in de firmware niet
# stilzwijgend uit deze weg wegblijft.
NO_READBACK = {
    "dutycycle": ("/cfg.json meldt het zendtijdbudget niet, dus er valt niets "
                  "terug te lezen"),
    "guest.password": ("een wachtwoord over HTTP zonder TLS, en /cfg.json meldt "
                       "alleen of het leeg of nog de gebakken waarde is"),
}

# De spiegel van CFG_PARAMS, in dezelfde volgorde en met dezelfde velden:
#   sleutel, soort, lo, hi, keuzes, risico
# 'reboot' en 'secret' staan er niet in: geen van deze sleutels heeft ze aan, en
# een kolom met alleen nullen erin is een kolom die stil verkeerd komt te staan.
SPEC = (
    # --- zo weer terug te zetten ---------------------------------------------
    ("name",                  "text",     0,    0, "", nodeconfig.RISK_PLAIN),
    ("lat",                   "float",  -90,   90, "", nodeconfig.RISK_PLAIN),
    ("lon",                   "float", -180,  180, "", nodeconfig.RISK_PLAIN),
    ("owner.info",            "text",     0,    0, "", nodeconfig.RISK_PLAIN),
    ("advert.interval",       "int",     60,  240, "", nodeconfig.RISK_PLAIN),
    ("flood.advert.interval", "int",      3,  168, "", nodeconfig.RISK_PLAIN),
    ("rxdelay",               "float",    0,   20, "", nodeconfig.RISK_PLAIN),
    ("txdelay",               "float",    0,    2, "", nodeconfig.RISK_PLAIN),
    ("direct.txdelay",        "float",    0,    2, "", nodeconfig.RISK_PLAIN),
    # --- verandert merkbaar hoe de node zich gedraagt ------------------------
    ("af",                    "float",    0,  100, "", nodeconfig.RISK_WRITES),
    ("flood.max",             "int",      0,   64, "", nodeconfig.RISK_WRITES),
    ("flood.max.unscoped",    "int",      0,   64, "", nodeconfig.RISK_WRITES),
    ("flood.max.advert",      "int",      0,   64, "", nodeconfig.RISK_WRITES),
    ("int.thresh",            "int",      0,  255, "", nodeconfig.RISK_WRITES),
    ("agc.reset.interval",    "int",      0, 1020, "", nodeconfig.RISK_WRITES),
    ("multi.acks",            "int",      0,    3, "", nodeconfig.RISK_WRITES),
    ("path.hash.mode",        "int",      0,    2, "", nodeconfig.RISK_WRITES),
    ("loop.detect",           "enum",     0,    0,
     "off|minimal|moderate|strict", nodeconfig.RISK_WRITES),
    ("cad",                   "bool",     0,    0, "", nodeconfig.RISK_WRITES),
    ("adc.multiplier",        "float",    0,   10, "", nodeconfig.RISK_WRITES),
    # --- kan de bereikbaarheid afsnijden -------------------------------------
    ("tx",                    "int",      0,   30, "", nodeconfig.RISK_CUTOFF),
    ("repeat",                "bool",     0,    0, "", nodeconfig.RISK_CUTOFF),
    ("allow.read.only",       "bool",     0,    0, "", nodeconfig.RISK_CUTOFF),
    ("radio.rxgain",          "bool",     0,    0, "", nodeconfig.RISK_CUTOFF),
    ("radio.fem.rxgain",      "bool",     0,    0, "", nodeconfig.RISK_CUTOFF),
)


def spec() -> dict:
    """De parameterlijst in dezelfde vorm als ``nodeconfig.params()``.

    Zodat de rest van ``nodeconfig`` en het sjabloon niet hoeven te weten waar de
    lijst vandaan komt: over deze weg uit de spiegel hierboven, over de andere
    twee van de node zelf.
    """
    return {"ok": True, "error": "", "at": 0.0, "params": [
        {"key": key, "kind": kind, "lo": float(lo), "hi": float(hi),
         "choices": keuzes, "risk": risk, "reboot": 0, "secret": 0}
        for key, kind, lo, hi, keuzes, risk in SPEC
        if key in CFG_KEYS
    ]}


def values(host: str, timeout: int | None = None) -> dict:
    """De huidige waarde van elke aangeboden parameter, op CLI-sleutel.

    ``{"ok": bool, "error": str, "values": {sleutel: tekst}, "raw": dict}``.
    ``raw`` is het hele antwoord, want de pagina toont er meer uit dan alleen de
    schrijfbare velden -- de gebakken radiowaarden, de wachtwoordvlaggen en het
    kanaalbudget staan er ook in, en die zijn juist interessant omdat ze NIET te
    zetten zijn.
    """
    got = cfg(host, timeout)
    out = {"ok": got["ok"], "error": got["error"], "values": {}, "raw": got["data"]}
    if not got["ok"]:
        return out
    for cli_key, json_key in CFG_KEYS.items():
        if json_key in got["data"]:
            out["values"][cli_key] = str(got["data"][json_key])
    return out


def same_value(kind: str, asked: str, applied: str) -> bool:
    """Of de teruggelezen waarde is wat er gevraagd werd.

    Een spiegel van ``cfgSameValue()`` in de repeaterfirmware, inclusief de
    marge: een getal dat als 'minuten/2' in één byte bewaard wordt komt niet
    bit-voor-bit terug, en een float die door een tekstveld en terug is geweest
    wijkt in de vijfde decimaal af. Beide zijn geen mislukking. Een string
    vergelijkt wel exact -- daar is elk verschil er een.
    """
    if kind in ("int", "float"):
        try:
            return abs(float(str(asked).replace(",", ".").strip())
                       - float(str(applied).replace(",", ".").strip().rstrip("%"))) <= 0.0005
        except (TypeError, ValueError):
            return False
    return str(asked) == str(applied)


def is_error(reply: str) -> bool:
    """Of de CLI met een fout antwoordde.

    Beide spellingen waarmee MeshCore weigert, voluit -- zoals ``cfgIsError()``
    in de repeaterfirmware, en om dezelfde reden: een node die 'Erratic' heet
    moet zijn eigen naam nog kunnen terugkrijgen.
    """
    text = str(reply or "").lstrip()
    if text.startswith("> "):
        text = text[2:]
    return text.startswith("Error") or text.startswith("Err - ")


# --- de kanalen uit /status.json ----------------------------------------------

def metrics_from_status(data: dict) -> dict:
    """De metingen uit één ``/status.json``, onder de namen die al bestaan.

    **Dezelfde metricnamen als de repeaterfirmware publiceert**, en dat is de
    hele opzet van deze functie. Een sensornode die over LoRa uitgevraagd wordt
    levert ``ch<N>_switch``, ``ch<N>_generic``, ``ch<N>_voltage`` en
    ``ch<N>_temperature`` op (zie monDecodeTelemetry en de publicatie in
    MeshManagerNet.cpp). Dezelfde node over IP moet dezelfde namen opleveren,
    anders staat er per dienst twee keer een reeks in de databank en tekent de
    pagina twee grafieken van hetzelfde -- met een knik op het moment dat er van
    weg gewisseld werd.

    Twee regels die uit de firmware overgenomen zijn en waar het op aankomt:

    **De tijd gaat alleen mee als het kanaal op staat.** ``querySensors()`` in
    MonitorSensors.cpp voegt ``LPP_GENERIC_SENSOR`` uitsluitend toe wanneer
    ``seeded && up``. Een tijd bij een dode dienst is geen meting maar een oude
    waarde, en wie hem toch tekent krijgt een grafiek die tijdens een storing
    gewoon doorloopt. Hier dus hetzelfde: geen ``ch<N>_generic`` bij een kanaal
    dat niet 'op' staat.

    **De schakelaar gaat wél altijd mee.** ``LPP_SWITCH`` kent geen "onbekend",
    en een kanaal dat komt en gaat is voor een dashboard erger dan een kanaal dat
    even 0 staat.

    Naast de kanalen komen er drie dingen mee die geen kanaal zijn:
    ``online`` (de bekende levensteken-vlag), ``uptime`` in DAGEN zoals de rest
    van dit project (``/status.json`` meldt seconden) en ``wifi_rssi``. Die
    laatste heeft geen tegenhanger over LoRa en staat er toch, omdat hij de
    gezondheid meet van precies het pad waar dit hele bestand op leunt: een
    signaal dat wegzakt is de vroegste aanwijzing dat deze beheerweg gaat
    verdwijnen. Bewust NIET ``last_rssi``: dat is de LoRa-radio, en twee
    verschillende grootheden onder één naam is de fout die deze functie juist
    vermijdt.
    """
    out: dict = {}
    if not isinstance(data, dict):
        return out
    out["online"] = True

    uptime = data.get("uptime")
    if isinstance(uptime, (int, float)) and not isinstance(uptime, bool):
        out["uptime"] = round(float(uptime) / 86400.0, 5)

    rssi = data.get("rssi")
    # Een RSSI in dBm is altijd negatief; 0 betekent dat de driver hem nooit
    # ingevuld heeft. Dezelfde toets als de firmware op noise_floor en last_rssi
    # doet, en om dezelfde reden: een lijn die naar nul duikt op een grafiek waar
    # een gat hoort heeft hier eerder een middag gekost.
    if isinstance(rssi, (int, float)) and not isinstance(rssi, bool) and rssi < 0:
        out["wifi_rssi"] = int(rssi)

    for entry in data.get("mon") or []:
        if not isinstance(entry, dict):
            continue
        try:
            channel = int(entry.get("ch"))
        except (TypeError, ValueError):
            continue
        if channel <= 0:
            continue
        soort = str(entry.get("k") or "")
        st = str(entry.get("st") or "").strip()

        if soort == KIND_FIXED:
            # Een vast kanaal meldt óf een spanning in woorden ("4.139 V") óf een
            # toestand. Welke van de twee valt aan de tekst te zien en niet aan
            # het kanaalnummer: CH_MAINS en CH_WIFI zijn constanten in de
            # firmware van de node, en die hier hardcoderen zou betekenen dat
            # een nieuwe versie stil het verkeerde kanaal vult.
            spanning = _volts(st)
            if spanning is not None:
                out[f"ch{channel}_voltage"] = spanning
            elif st in ST_TRUE:
                out[f"ch{channel}_switch"] = 1
            elif st in ST_FALSE:
                out[f"ch{channel}_switch"] = 0
            continue

        if soort not in (KIND_PING, KIND_PUSH):
            continue
        up = st == ST_UP
        out[f"ch{channel}_switch"] = 1 if up else 0
        if up:
            ms = entry.get("ms")
            if isinstance(ms, (int, float)) and not isinstance(ms, bool):
                out[f"ch{channel}_generic"] = int(ms)
    return out


def _volts(text: str) -> float | None:
    """"4.139 V" -> 4.139. None als het geen spanning is."""
    parts = str(text or "").split()
    if len(parts) != 2 or parts[1] != "V":
        return None
    try:
        return float(parts[0])
    except ValueError:
        return None


def channel_names_from_status(data: dict) -> dict[int, dict]:
    """De namen die de node zelf bij zijn kanalen meldt.

    ``{kanaal: {"name": ..., "unit": ...}}``.

    Dit is de enige plek in de hele keten waar deze namen bestaan. CayenneLPP
    draagt geen naamveld -- niet in het formaat en niet in MeshCore, dat alleen
    een oplopende kanaalteller kent -- dus over de radio komt er letterlijk
    "kanaal 6, switch, 1" binnen en niets meer. Wie de node over IP kan bereiken,
    kan de koppeling wél vragen, en dan hoeft niemand hem meer over te typen.

    De naam wordt ``n``, en ``h`` komt er tussen haakjes achter waar hij iets
    toevoegt: "google (google.com)" zegt meer dan "google", en
    "netvoeding (klemspanning)" zegt waar die toestand gemeten wordt. Waar ``h``
    hetzelfde zegt als ``n`` of waar hij "(gemeld)" is -- dat staat al in het
    soort van het kanaal -- blijft hij weg.

    De eenheid wordt "ms" op elk kanaal dat een responstijd kan dragen, en dat
    is nadrukkelijk een uitspraak die alleen deze weg kan doen.
    ``LPP_GENERIC_SENSOR`` is vier byte met vermenigvuldiger 1 en belooft niets
    over wát er gemeten is; dat het milliseconden zijn, weet ``/status.json``
    (het veld heet ``ms``) en het telemetriepakket niet. Precies daarom is de
    eenheid op een gemonitorde node een veld dat een mens moet invullen, en
    precies daarom hoeft dat hier niet.
    """
    uit: dict[int, dict] = {}
    if not isinstance(data, dict):
        return uit
    for entry in data.get("mon") or []:
        if not isinstance(entry, dict):
            continue
        try:
            channel = int(entry.get("ch"))
        except (TypeError, ValueError):
            continue
        if channel <= 0:
            continue
        naam = str(entry.get("n") or "").strip()
        waar = str(entry.get("h") or "").strip()
        if not naam:
            continue
        if waar and waar != naam and not waar.startswith("("):
            naam = f"{naam} ({waar})"
        soort = str(entry.get("k") or "")
        uit[channel] = {
            "name": naam[:64],
            "unit": "ms" if soort in (KIND_PING, KIND_PUSH) else "",
        }
    return uit


def neighbors_from_acl(data: dict) -> list[dict]:
    """De gehoorde buren uit ``/acl.json``, in de vorm die ``db.ingest`` verwacht.

    ``a`` is een ouderdom in seconden en niet een tijdstempel, en dat is hier
    een geluk: de klok van een sensornode staat na elke herstart op 15 mei 2024,
    dus een absolute tijd van die node zou onbruikbaar zijn. Een VERSCHIL binnen
    diezelfde verkeerde klok is dat niet -- ``ageOf()`` rekent beide kanten in
    dezelfde RTC-seconden -- dus de ouderdom klopt ook als de datum niet klopt.
    Daarom rekent deze functie hem om naar minuten en laat ``db.ingest`` er de
    tijd van DEZE server bij optellen.

    ``db.node_key`` en niet ``db.key_prefix``, en dat is hier geen detail.
    ``/acl.json`` meldt de VOLLE publieke sleutel (64 hextekens, want daar hangt
    de gedeelde geheime sleutel aan), en de burentabel van deze site draait op
    de eerste zes byte -- daar hangen de burenlijst, de linkkaart en de
    naamopzoeking op. Een rij van 64 tekens zou naast dezelfde node van 12 komen
    te staan en met niets meer matchen: twee buren waar er één is.
    """
    uit = []
    if not isinstance(data, dict):
        return uit
    for entry in data.get("nb") or []:
        if not isinstance(entry, dict):
            continue
        prefix = db.node_key(entry.get("k"))
        if not prefix:
            continue
        rij = {"prefix": prefix, "name": str(entry.get("n") or "") or None}
        try:
            rij["snr"] = float(entry.get("s"))
        except (TypeError, ValueError):
            pass
        age = entry.get("a")
        if isinstance(age, (int, float)) and not isinstance(age, bool) and age >= 0:
            rij["seen_min"] = float(age) / 60.0
        uit.append(rij)
    return uit


# --- één ronde ----------------------------------------------------------------
#
# De uitslag van de laatste ronde staat in het geheugen en niet in de databank.
# Wat dat kost: na een herstart van de site is hij weg. Wat het oplevert: geen
# tabel, geen opruiming en geen bewaartermijn voor iets dat over vijf minuten
# vervangen wordt -- en wat er WEL blijvend toe doet (de metingen, de namen, de
# waarneming dat de API antwoordde) gaat langs deze weg gewoon de databank in.
# Dezelfde afweging als bij ``nodeconfig._cfgset``.

_lock = threading.Lock()
_last: dict[int, dict] = {}
_state = {"last_run": None, "last_result": "nog niet gedraaid"}

# --- alarmen uit de poll afleiden ----------------------------------------------
#
# WAAROM DE POLL ALARMEN MAAKT, terwijl de node ze zelf al stuurt. De node stuurt
# zijn alarmen als DM over het mesh naar een repeater, en die RF-richting
# (node -> repeater) is defect bevestigd: heen werkt, terug niet, en zes
# softwareverdenkingen zijn weerlegd (MeshUptime, docs/openstaand.md). Zolang dat
# ter plekke niet gerepareerd is, komt er over het mesh dus NIETS binnen -- en
# een alertketen die op een kapotte schakel wacht, is geen alertketen. De poll
# ziet elke ronde de volledige toestand, en een OVERGANG daarin is dezelfde
# gebeurtenis als het alarm dat de node zou hebben gestuurd.
#
# Wat deze weg niet kan en de mesh-weg wel, en dat staat overal waar het telt:
# de LATENTIE. Een mesh-alarm is er seconden na het feit; deze afleiding pas bij
# de volgende ronde, dus tot MM_SENSOR_POLL_S (standaard 300 s) later. Vandaar
# de bronvermelding op /meshmoni: wie een melding leest, hoort te weten hoe oud
# ze kan zijn.
#
# DE VORIGE TOESTAND leeft hier, in het geheugen van dit proces, en nergens
# anders. Dat is een keuze met een gedocumenteerde prijs: een herstart van de
# server wist de vergelijkingsbasis. De eerste ronde na een start is daarom
# ALLEEN IJKEN -- ze legt vast wat er is en meldt niets. Zonder die regel zou
# elke herstart een golf "nieuwe" alarmen geven voor toestanden die al dagen zo
# waren, en een alarmkanaal dat bij elke deploy blaft is er binnen een week een
# dat niemand meer leest. Wat het kost: een storing die al bestond toen de
# server startte, wordt pas gemeld bij haar eerstvolgende overgang. Dat is de
# goedkopere fout, en de toestand zelf staat intussen gewoon op de pagina's.
#
# Per kanaal wordt de laatst BEKENDE toestand onthouden, en '?' en 'pauze'
# overschrijven die niet. Dat is meer dan netheid: na een herstart van de NODE
# staan alle kanalen even op '?', en 'op -> ? -> neer' zou anders in twee stille
# stappen uiteenvallen. Nu vergelijkt de afleiding neer met op -- de laatste
# toestand waarvan we iets wisten -- en meldt ze de storing alsnog.
_toestand: dict[int, dict] = {}

# Toestanden waar een uitspraak in zit. 'pauze' en '?' zeggen niet dat er iets
# mis is maar dat WIJ het niet weten, en op niet-weten hoort geen alarm te
# volgen -- dezelfde lijn als de firmware, die 'pauze' en 'stil' amber kleurt en
# rood reserveert voor wat is vastgesteld. 'stil' staat er wél bij: dat is een
# vaststelling over de MELDER, en de firmware alarmeert er zelf ook op.
_DEFINITE = ("op", "neer", "stil")


def _state_from_status(data: dict) -> dict:
    """De toestand van één ronde, teruggebracht tot wat de vergelijking nodig heeft.

    ``{"mains": 0|1|None, "mains_sim": bool, "mon": {kanaal: {"st","naam",
    "host","soort","sim"}}}``. Alleen de monitorkanalen (ping/gemeld): de vaste
    kanalen herhalen mains en wifi, en wifi is over deze weg per definitie
    'online' -- wij praten erover met de node. Mains komt uit het veld zelf; -1
    (geen sensorlaag) wordt None.

    ``sim`` is het simulatieveld van de node (``sm``: off/up/down). Het reist
    mee omdat de afleiding uit de TOESTAND werkt en daarmee de tekstmarkering
    zou omzeilen die het mesh-pad wél heeft: de firmware zet TEST/SIMULATIE in
    het bericht zelf, maar een geforceerd vakje ziet er in ``st`` precies zo uit
    als een echte storing. Zonder deze vlag maakt de oefenknop dus een kale
    echte melding -- en dat is bij de eerste end-to-end-test ook gebeurd.

    ``mains_sim`` komt uit de vaste kanalen (netvoeding en batterijvoeding
    dragen hetzelfde sensornummer en dezelfde ``sm``): aan het kale
    ``mains``-veld is niet te zien of de waarde geforceerd is.
    """
    uit: dict = {"mains": None, "mains_sim": False, "mon": {}}
    if not isinstance(data, dict):
        return uit
    mains = data.get("mains")
    if mains in (0, 1):
        uit["mains"] = int(mains)
    for entry in data.get("mon") or []:
        if not isinstance(entry, dict):
            continue
        soort = str(entry.get("k") or "")
        sim = str(entry.get("sm") or "off").strip().lower() not in ("", "off")
        if soort == KIND_FIXED:
            if sim and str(entry.get("st") or "").strip() in ("aan", "uit"):
                uit["mains_sim"] = True
            continue
        if soort not in (KIND_PING, KIND_PUSH):
            continue
        try:
            channel = int(entry.get("ch"))
        except (TypeError, ValueError):
            continue
        uit["mon"][channel] = {
            "st": str(entry.get("st") or "").strip(),
            "naam": str(entry.get("n") or "").strip(),
            "host": str(entry.get("h") or "").strip(),
            "soort": soort,
            "sim": sim,
        }
    return uit


def mark_simulation(alert: dict) -> dict:
    """Eén alarm als oefening aanmerken: "(simulatie)" in de tekst, soort NULL.

    DE ENIGE SPELLING. De IP-afleiding hieronder gebruikt hem, en de
    gebeurtenis-push (sensorpush.py) gebruikt exact dezelfde functie -- niet een
    kopie -- want deze twee woorden zijn de helft van twee gedragingen die over
    alle wegen gelijk moeten zijn:

    * de TEKST, omdat wie op een pushmelding kijkt zonder nadenken moet zien of
      zijn router echt uit staat. De ernst blijft wél staan: de gebruiker test
      juist of een hoge melding doorkomt;
    * ``kind=None``, omdat een oefening buiten de kruisontdubbeling hoort te
      vallen -- dezelfde keuze die mqtt_ingest.alert_kind voor de TEST-teksten
      van het mesh maakt, en om dezelfde reden: een gesimuleerde 'neer' mag een
      ECHTE 'neer' die er kort na komt nooit onderdrukken, en andersom ook niet.

    Een tweede spelling ("(test)", "(oefening)") zou de ontdubbeling niet raken
    maar wel de lezer: twee wegen die hetzelfde feit anders aankleden, lezen als
    twee verschillende feiten.
    """
    alert["text"] += " (simulatie)"
    alert["kind"] = None
    return alert


def _transition_alert(prev: dict, cur: dict, channel: int) -> dict | None:
    """Het alarm bij één kanaalovergang, of None als er niets te melden is.

    De teksten spiegelen de vormen van de firmware (monitorAlertText en
    recoverAlertText), en dat is geen stijlkeuze maar de helft van de
    ontdubbeling: komt hetzelfde feit ooit alsnog over het mesh binnen, dan
    begint dat bericht met dezelfde dienstnaam -- en (node, soort, naam) is de
    sleutel waarop db.add_alert de tweede melding herkent.

    EEN GEFORCEERDE TOESTAND IS EEN OEFENING, en dat hoort de melding te zeggen.
    De afleiding werkt uit de toestand en zou anders de tekstmarkering omzeilen
    die het mesh-pad heeft (de firmware zet TEST/SIMULATIE in het bericht zelf).
    Dus: raakt een simulatie aan de overgang -- aan de kant waar hij heen gaat óf
    waar hij vandaan komt -- dan krijgt de tekst "(simulatie)" en de rij
    ``kind=None``. Die twee zijn elk de helft van het gedrag:

    * de TEKST, omdat wie op een pushmelding kijkt zonder nadenken moet zien of
      zijn router echt uit staat. De ernst blijft wél staan: de gebruiker test
      juist of een hoge melding doorkomt, en een oefening die als 'laag' binnen
      zou komen test iets anders dan het echte geval;
    * ``kind=None``, omdat een oefening buiten de kruisontdubbeling hoort te
      vallen -- precies de keuze die mqtt_ingest.alert_kind voor de
      TEST-teksten van het mesh ook maakt, en om dezelfde reden: een
      gesimuleerde 'neer' mag een ECHTE 'neer' die er kort na komt nooit
      onderdrukken, en andersom ook niet. De prijs is dat twee oefeningen kort
      na elkaar twee rijen geven; dat is bij oefenen eerder informatie dan ruis.

    De prev-kant telt mee omdat het einde van een simulatie anders een kale
    echte melding wordt: valt de forcering af en blijkt de dienst gewoon op, dan
    is dat "herstel" een artefact van de oefening en geen dienst die terugkwam.

    En de spiegel dáárvan: valt de forcering af en is de dienst ÉCHT neer, dan
    verandert ``st`` niet ('neer' blijft 'neer') maar verandert de herkomst wel
    -- van beweerd naar gemeten. Dat is het moment waarop we leren dat het geen
    oefening meer is, en dat is een echte melding waard; de simulatie heeft de
    werkelijke storing tot dan gemaskeerd.
    """
    was, nu = prev["st"], cur["st"]
    if was not in _DEFINITE or nu not in _DEFINITE:
        return None
    sim_was = bool(prev.get("sim"))
    sim_nu = bool(cur.get("sim"))
    naam = cur["naam"] or f"kanaal {channel}"
    push = cur["soort"] == KIND_PUSH

    if nu == was:
        # Geen overgang in de toestand -- maar mogelijk wel in de HERKOMST.
        if sim_was and not sim_nu and nu == "neer":
            return {"kind": "neer", "severity": "hoog", "channel": channel,
                    "text": (f"{naam} gemeld als neer" if push
                             else f"{naam} onbereikbaar ({cur['host']})")}
        return None

    if nu == "neer":
        alert = {"kind": "neer", "severity": "hoog", "channel": channel,
                 "text": (f"{naam} gemeld als neer" if push
                          else f"{naam} onbereikbaar ({cur['host']})")}
    elif nu == "stil":
        # Een heel andere boodschap dan 'neer', en het verschil is voor de
        # lezer het halve bericht: hier is de MELDER stil en weten wij niets.
        alert = {"kind": "stil", "severity": "hoog", "channel": channel,
                 "text": f"{naam}: geen melding meer"}
    # nu == "op": het herstel. Lagere ernst, zoals de firmware dat ook doet --
    # en na een stilte een andere zin, want dan is er geen dienst hersteld
    # waarvan we weten dat hij plat lag.
    elif was == "stil":
        alert = {"kind": "op", "severity": "laag", "channel": channel,
                 "text": f"{naam} meldt weer"}
    else:
        alert = {"kind": "op", "severity": "laag", "channel": channel,
                 "text": (f"{naam} weer op gemeld" if push
                          else f"{naam} weer bereikbaar")}

    if sim_was or sim_nu:
        mark_simulation(alert)
    return alert


def _derive_alerts(rid: int, data: dict) -> int:
    """Vergelijk deze ronde met de vorige en leg overgangen vast als alarm.

    Geeft het aantal NIEUWE rijen terug (ontdubbelde tellen niet mee). De
    eerste ronde per node -- na een serverherstart dus ook -- ijkt alleen; zie
    het blok boven ``_toestand`` voor waarom dat geen gemakzucht is.
    """
    nieuw = _state_from_status(data)
    with _lock:
        vorig = _toestand.get(rid)
        if vorig is None:
            _toestand[rid] = nieuw
            return 0
        # De vorige toestand bijwerken zonder de laatst bekende uitspraak te
        # verliezen: een onbepaalde 'st' laat de oude staan (zie _DEFINITE), en
        # een kanaal dat uit de kaart verdwijnt, verdwijnt ook hier -- een
        # verwijderde monitor is geen storing.
        bewaard: dict = {"mains": nieuw["mains"] if nieuw["mains"] is not None
                                  else vorig.get("mains"),
                         "mains_sim": nieuw.get("mains_sim", False),
                         "mon": {}}
        overgangen = []
        for channel, cur in nieuw["mon"].items():
            prev = vorig["mon"].get(channel)
            if prev is None:
                # Nieuw kanaal: eerst ijken, net als een nieuwe node. Een
                # monitor die net is aangemaakt en meteen 'neer' meet, kan een
                # dienst zijn die nooit bestaan heeft -- een tikfout in een
                # adres -- en dat is geen storing om iemand voor te wekken.
                bewaard["mon"][channel] = cur
                continue
            alert = _transition_alert(prev, cur, channel)
            if alert is not None:
                overgangen.append(alert)
            if cur["st"] in _DEFINITE:
                bewaard["mon"][channel] = cur
            else:
                bewaard["mon"][channel] = prev
        # De netvoeding, met dezelfde simulatieregels als de monitorkanalen:
        # raakt de forcering aan de overgang, dan "(simulatie)" en kind=None;
        # valt de forcering af terwijl de voeding echt weg blijkt, dan is dát de
        # echte melding die de oefening tot nu toe maskeerde.
        mains_sim_was = bool(vorig.get("mains_sim"))
        mains_sim_nu = bool(nieuw.get("mains_sim"))
        if vorig.get("mains") is not None and nieuw["mains"] is not None:
            if nieuw["mains"] != vorig["mains"]:
                if nieuw["mains"] == 0:
                    alert = {"kind": "neer", "severity": "hoog", "channel": None,
                             "text": "netvoeding weg, node op batterij"}
                else:
                    alert = {"kind": "op", "severity": "laag", "channel": None,
                             "text": "netvoeding terug"}
                if mains_sim_was or mains_sim_nu:
                    mark_simulation(alert)
                overgangen.append(alert)
            elif mains_sim_was and not mains_sim_nu and nieuw["mains"] == 0:
                overgangen.append({"kind": "neer", "severity": "hoog",
                                   "channel": None,
                                   "text": "netvoeding weg, node op batterij"})
        _toestand[rid] = bewaard

    aantal = 0
    for alert in overgangen:
        if db.add_alert(rid, alert["text"], source="ip",
                        channel=alert["channel"], severity=alert["severity"],
                        kind=alert["kind"]):
            aantal += 1
    return aantal


def poll(rep, timeout: int | None = None) -> dict:
    """Eén node uitlezen en alles wat eruit komt wegschrijven.

    Vier dingen, in deze volgorde, en de volgorde is geen toeval: eerst de
    waarneming dat de API antwoordde (want daar hangt het beheerniveau aan en dat
    hoort te kloppen ook als het wegschrijven hierna misgaat), dan de namen bij
    de kanalen (want de pagina die de metingen toont leest ze meteen daarna), dan
    de metingen, en als laatste de buren -- die komen uit een tweede verzoek en
    mogen de eerste drie niet in gevaar brengen.
    """
    rid = int(firmware._field(rep, "id") or 0)
    host = str(firmware._field(rep, "sensor_host") or "").strip()
    out = {"ok": False, "error": "", "at": db.utcnow(), "metrics": 0,
           "channels": 0, "neighbors": 0, "alerts": 0, "host": host}

    got = status(host, timeout)
    if not got["ok"]:
        out["error"] = got["error"]
        _note(rid, out)
        return out

    data = got["data"]
    db.record_sensor_seen(rid, str(data.get("fw") or ""))

    for channel, naam in channel_names_from_status(data).items():
        if db.set_channel_name(rid, channel, naam["name"], naam["unit"],
                               source=db.SOURCE_AUTO):
            out["channels"] += 1

    gemeten = metrics_from_status(data)
    if gemeten:
        db.ingest(rid, out["at"], gemeten, None)
        out["metrics"] = len(gemeten)

    # De overgangen sinds de vorige ronde, als alarm. Ná de metingen, zodat wie
    # op een pushmelding de pagina opent daar al de cijfers vindt die erbij
    # horen. Zolang de mesh-schakel node->repeater defect is, is dit de enige
    # weg waarlangs een storing van deze node iemands telefoon haalt -- met de
    # latentie van het pollinterval, en dat staat erbij waar de melding staat.
    out["alerts"] = _derive_alerts(rid, data)

    buren = acl(host, timeout)
    if buren["ok"]:
        rijen = neighbors_from_acl(buren["data"])
        if rijen:
            db.ingest(rid, out["at"], {}, rijen)
            out["neighbors"] = len(rijen)
    else:
        # Geen fout van de ronde: de metingen zijn binnen. Wel iets om te
        # vermelden, want een lege burenlijst en een burenlijst die niet
        # opgehaald kon worden zien er op de kaart identiek uit.
        out["error"] = f"buren niet opgehaald: {buren['error']}"

    out["ok"] = True
    _note(rid, out)
    return out


def _note(rid: int, uitslag: dict) -> None:
    with _lock:
        _last[rid] = dict(uitslag)


def last(rid) -> dict:
    """De uitslag van de laatste ronde voor deze node, of een leeg antwoord."""
    with _lock:
        return dict(_last.get(int(rid or 0)) or
                    {"ok": False, "error": "", "at": None, "metrics": 0,
                     "channels": 0, "neighbors": 0, "alerts": 0, "host": ""})


def run_once() -> dict:
    """Alle nodes met een eigen API, één keer."""
    rows = db.sensor_nodes()
    gelukt = mislukt = 0
    for rep in rows:
        try:
            uitslag = poll(rep)
        except Exception:               # noqa: BLE001 -- zie hieronder
            # Eén node mag de ronde niet meenemen. Dit is een lus over apparaten
            # die los van elkaar stuk kunnen zijn, en een uitzondering op de
            # tweede zou de derde tot na het volgende interval laten wachten --
            # zonder dat er ergens staat waarom.
            log.exception("Sensornode %s: onverwachte fout tijdens de ronde",
                          firmware._field(rep, "pubkey_prefix"))
            mislukt += 1
            continue
        if uitslag["ok"]:
            gelukt += 1
        else:
            mislukt += 1
            log.info("Sensornode %s: %s",
                     firmware._field(rep, "pubkey_prefix"), uitslag["error"])
    uit = {"nodes": len(rows), "ok": gelukt, "failed": mislukt}
    _state["last_run"] = db.utcnow()
    _state["last_result"] = (
        "geen nodes met een eigen API" if not rows
        else f"{gelukt} van {len(rows)} geantwoord")
    return uit


def status_summary() -> dict:
    """Wat de serverpagina over deze ronde te melden heeft."""
    return {"enabled": ENABLED, "interval_s": INTERVAL_S,
            "last_run": _state["last_run"], "last_result": _state["last_result"]}


_thread = None


def _run() -> None:
    time.sleep(FIRST_RUN_DELAY_S)
    while True:
        try:
            run_once()
        except Exception:               # noqa: BLE001
            log.exception("Sensornode-ronde afgebroken")
        time.sleep(INTERVAL_S)


def start() -> None:
    """De pollronde starten, tenzij ze uitstaat.

    Uit zetten is een geldige keuze: wie zijn sensornodes niet over IP wil laten
    uitlezen -- omdat de server op een netwerk staat waar dat niet hoort, of
    omdat het mesh de bedoelde weg is -- hoort dat te kunnen zeggen zonder de
    adressen weg te gooien.
    """
    global _thread
    if not ENABLED:
        log.info("Sensornode-polling staat uit (MM_SENSOR_POLL_ENABLED)")
        return
    if _thread is not None:
        return
    _thread = threading.Thread(target=_run, daemon=True,
                              name="meshmanager-sensornode")
    _thread.start()


# --- de losse handelingen -----------------------------------------------------
#
# Vier knoppen die over deze weg werkelijk iets kunnen, en per knop staat er
# hieronder waarom hij hier hoort en niet in nodeconfig.write(). Kort: dit zijn
# geen instellingen. Een advert is een pakket, een herstart is een gebeurtenis,
# de klok is een oordeel van deze server, en een regio is een boom met een eigen
# opslagcommando. Ze door de instellingenweg persen zou van elk van de vier een
# nep-parameter maken.

def send_advert(rep, zerohop: bool = False) -> dict:
    """De node zich laten melden op het mesh.

    ``advert.zerohop`` bestaat ernaast en niet in plaats van: een gewone advert
    gaat als flood het hele mesh over, een zerohop-advert alleen naar wie hem
    direct hoort. Wie wil weten of zijn buren hem nog horen, heeft de tweede
    nodig en betaalt daarvoor geen mesh-brede flood.
    """
    return cli(_host(rep), "advert.zerohop" if zerohop else "advert")


def reboot(rep) -> dict:
    """De node herstarten.

    De node antwoordt hier vóór hij het doet (hij zet de opdracht een halve
    seconde uit), dus een antwoord op deze aanroep betekent "aangevraagd" en niet
    "gebeurd". Dat is met opzet zo gebouwd aan de overkant en het is precies wat
    je wil weten op een bewakingsnode: de verbinding afbreken vóór het antwoord
    verstuurd is, maakt van een geslaagde herstart en een omgevallen node
    hetzelfde beeld.

    Wat een herstart hier kost, en het hoort op de pagina te staan: de gemeten
    toestanden beginnen weer op '?' en de klok staat weer op 15 mei 2024.
    """
    return cli(_host(rep), "reboot")


def set_clock(rep) -> dict:
    """De klok van deze node op de tijd van deze server zetten.

    **Geen tweede klokoordeel.** Of deze machine mag zeggen hoe laat het is,
    beslist ``clocksync.check_clock()`` -- letterlijk dezelfde functie die de
    dagelijkse ronde over MQTT aanroept. Een knop met zijn eigen oordeel zou een
    achterdeur om die controle heen zijn, en het enige zichtbare gevolg zou weken
    later een verkeerde klok op een dak zijn.

    Wat hier WEL anders is dan bij de MQTT-weg, en waarom:

    *   Er is geen brokerverbinding nodig. Deze weg is de node zelf.
    *   Er is geen minimumafstand tussen twee keer. Bij de MQTT-weg staat die er
        omdat elke correctie zendtijd kost op een gedeelde band; hier kost ze
        één HTTP-verzoek op het lokale net en betaalt niemand anders mee. En de
        node heeft die correctie vaker nodig dan een repeater: hij heeft geen
        batterijgevoede klok, dus na élke herstart staat hij op 15 mei 2024. Een
        wachttijd zou precies dan het slechtste antwoord geven.

    Het moment wordt wél in hetzelfde grootboek gezet als de MQTT-weg
    (``clocksync.note_sent``), zodat "wanneer heeft deze site deze node voor het
    laatst de tijd gestuurd" één antwoord heeft en niet twee.
    """
    out = {"ok": False, "outcome": "", "reason": "", "reply": "", "epoch": 0}
    if not clocksync.ENABLED:
        out["outcome"] = "disabled"
        out["reason"] = "kloksynchronisatie staat uit op deze server"
        return out

    check = clocksync.check_clock()
    if not check["ok"]:
        out["outcome"] = "no_clock"
        out["reason"] = check["reason"]
        log.warning("Klok van een sensornode geweigerd: %s", check["reason"])
        return out

    now = int(time.time())
    out["epoch"] = now
    antwoord = cli(_host(rep), f"time {now}")
    out["reply"] = antwoord["reply"]
    if not antwoord["ok"] or is_error(antwoord["reply"]):
        out["outcome"] = "failed"
        out["reason"] = antwoord["error"] or antwoord["reply"]
        return out

    key = str(firmware._field(rep, "pubkey_prefix") or "").lower()
    if key:
        clocksync.note_sent(key, now)
    out["ok"] = True
    out["outcome"] = "sent"
    return out


def clock_of(rep) -> dict:
    """Wat de node zelf zegt dat het is. Lezen, dus zonder gevolgen."""
    return cli(_host(rep), "clock")


def region_tree(rep) -> dict:
    """De regioboom van de node, zoals hij hem zelf toont."""
    return cli(_host(rep), "region")


# Welke regio-instellingen van hieraf te zetten zijn, met het CLI-woord erbij.
# Twee, en niet de hele regio-taal: 'region def <...>' definieert nieuwe takken
# en 'region list' leest, en beide horen op de pagina van de node zelf waar de
# boom naast het invoerveld staat. Hier staan de twee die je op afstand wil
# kunnen zetten -- waar deze node staat, en welke scope hij op uitgaand verkeer
# zet.
REGION_FIELDS = {
    "home": "waar deze node staat",
    "default": "de scope op uitgaande pakketten",
}


def set_region(rep, veld: str, naam: str) -> dict:
    """Eén regioveld zetten en het vastleggen op flash.

    Twee opdrachten en niet één, want zo werkt de regiotaal van MeshCore:
    ``region home eu be`` zet het in het geheugen en ``region save`` legt het
    vast. Zonder dat tweede is de instelling weg bij de eerstvolgende herstart --
    en dat is precies de instelling waarvan je pas na die herstart merkt dat hij
    er niet meer is. Beide antwoorden gaan mee terug: een geslaagde ``set`` met
    een mislukte ``save`` is een derde uitkomst en niet "gelukt".

    **Dit is niet de radio en het is ook geen kleinigheid.** Een scope bepaalt
    wie dit verkeer doorstuurt, dus een verkeerde waarde kan een node stil buiten
    het bereik van zijn buren zetten. Het verschil met de frequentie is dat die
    fout van hieraf terug te draaien is: de node blijft over WiFi bereikbaar en
    dit commando kan opnieuw. Vandaar een bevestiging en geen weigering.
    """
    out = {"ok": False, "error": "", "set": "", "saved": "", "veld": veld,
           "naam": naam}
    if veld not in REGION_FIELDS:
        out["error"] = f"onbekend regioveld: {veld}"
        return out
    naam = str(naam or "").strip()
    # Alleen wat een regionaam kan zijn. Zonder deze zeef is dit veld een manier
    # om er een tweede opdracht achter te plakken -- de CLI leest tot het einde
    # van de regel, dus de waarde is altijd het laatste woord en er hoort dan ook
    # geen scheider in te kunnen.
    #
    # De spatie hoort er WEL bij en de puntkomma niet, en dat is precies het
    # verschil dat deze zeef moet maken. Een regio is een pad ("eu be"), dus
    # spaties zijn de gewone vorm; ';', '&' en een regeleinde zijn de tekens
    # waarmee je van één regel twee opdrachten maakt.
    if not naam or len(naam) > 40 or any(
            c not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"
                     "0123456789.-_| " for c in naam):
        out["error"] = ("een regionaam bestaat uit letters, cijfers, punt, "
                        "streepje, liggend streepje, '|' en spaties")
        return out

    host = _host(rep)
    gezet = cli(host, f"region {veld} {naam}")
    out["set"] = gezet["reply"] or gezet["error"]
    if not gezet["ok"] or is_error(gezet["reply"]):
        out["error"] = gezet["error"] or gezet["reply"]
        return out

    bewaard = cli(host, "region save")
    out["saved"] = bewaard["reply"] or bewaard["error"]
    if not bewaard["ok"] or is_error(bewaard["reply"]):
        out["error"] = ("de regio is gezet maar niet vastgelegd; hij is weg bij "
                        "de eerstvolgende herstart: "
                        + (bewaard["error"] or bewaard["reply"]))
        return out
    out["ok"] = True
    return out


def _host(rep) -> str:
    return str(firmware._field(rep, "sensor_host") or "").strip()


def rotate_cred(rep) -> dict:
    """Deze node een verse, eigen weblogin geven, los van de vlootsleutel.

    De volgorde is de hele veiligheid van deze functie:

    1.  Genereer een sterke ``user`` + ``pass`` (nodecred.generate).
    2.  Roep de node aan met de HUIDIGE credential -- dat is de per-node-login als
        die er al is, en anders de vlootsleutel (de bootstrap). Die keuze valt
        vanzelf goed: firmware._auth_header kijkt naar wat er NU opgeslagen is, en
        dat is de oude waarde zolang stap 3 niet gedaan is.
    3.  Bewaar de nieuwe login PAS na een 200 van de node.

    Faalt de aanroep -- adres geweigerd, geen antwoord, een HTTP-fout, of een
    antwoord dat geen ``{"ok":1}`` is -- dan verandert er NIETS aan de opslag. Dat
    is geen nette bijkomstigheid maar het punt: eerst opslaan en dan proberen zou
    je buitensluiten zodra de node niet meebeweegt, want dan klopt de server met
    een wachtwoord dat de node nooit aangenomen heeft.

    Het nieuwe wachtwoord komt NERGENS terug uit deze functie en in geen enkel log
    -- alleen dát het gelukt is, en de (niet-geheime) gebruikersnaam. Dezelfde
    regel als in audit.py: een geheim hoort niet in een log en niet in een URL.
    """
    from . import nodecred

    out = {"ok": False, "error": "", "user": ""}
    rid = int(firmware._field(rep, "id") or 0)
    host = _host(rep)
    if not host:
        out["error"] = "geen adres voor de eigen API van deze node"
        return out
    # Is er een credential om de wijziging mee AAN TE MELDEN? Bij de bootstrap is
    # dat de vlootsleutel; is die er niet en is er ook nog geen per-node-login,
    # dan valt er niets aan te melden en zou de aanroep sowieso op een 401 stuiten.
    if nodecred.for_host(host) is None and not firmware.NODE_USER:
        out["error"] = ("geen huidige weblogin om de wijziging mee aan te melden "
                        "(MM_FW_NODE_USER/MM_FW_NODE_PASS)")
        return out

    new_user, new_pass = nodecred.generate()
    # Form-urlencoded, NIET json: de node parseert /web/cred met dezelfde
    # form-arg-lezer als al zijn andere routes (/hook, /wifi, /sim, /cli) en ziet
    # een json-body als "geen user". Gemeten: json gaf 400 "user ontbreekt". De
    # node is de eenvoudigste, gedeelde conventie; de server past zich aan.
    from urllib.parse import urlencode
    body = urlencode({"user": new_user, "pass": new_pass}).encode()
    try:
        with firmware.open_node(host, "/web/cred", data=body,
                                timeout=TIMEOUT_S,
                                content_type="application/x-www-form-urlencoded") as resp:
            antwoord = json.loads(resp.read() or b"{}")
    except firmware.TargetRefused as exc:
        out["error"] = str(exc)
        return out
    except urllib.error.HTTPError as exc:
        # De node antwoordt bij een weigering met tekst; die is bruikbaarder dan
        # "HTTP 400". 400 hoort hier niet voor te komen -- we sturen nooit een
        # leeg wachtwoord -- maar als het toch gebeurt, zeg dan wat de node zei.
        try:
            melding = exc.read().decode("utf-8", "replace").strip()[:200]
        except OSError:
            melding = ""
        out["error"] = melding or f"node antwoordde HTTP {exc.code}"
        return out
    except (urllib.error.URLError, OSError, ValueError, TimeoutError) as exc:
        out["error"] = f"niet bereikbaar ({type(exc).__name__})"
        return out

    if not (isinstance(antwoord, dict) and antwoord.get("ok")):
        # Geen 200-met-ok: de node heeft de nieuwe login NIET aangenomen. Niets
        # opslaan -- zie de docstring.
        out["error"] = "de node bevestigde de nieuwe weblogin niet"
        return out

    # Pas hier bewaren, want pas hier is het waar. Vanaf het volgende verzoek
    # gebruikt firmware._auth_header deze nieuwe login voor dit adres.
    nodecred.store(rid, new_user, new_pass)
    out["ok"] = True
    out["user"] = new_user
    return out
