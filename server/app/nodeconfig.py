"""Eén CLI-instelling van een node zetten vanaf de beheerpagina.

De weg is die uit ``docs/node-management.md``: over HTTP naar een node die de
server kan bereiken, achter de eigen login van die node. Nadrukkelijk NIET over
het ``cmd``-topic van MQTT. Dat topic is bereikbaar voor iedereen met
brokergegevens en aanvaardt daarom precies een handvol vaste woorden; lezen kan
een node niet onbereikbaar maken en schrijven wel, dus het argument om die lijst
kort te houden wordt bij schrijven sterker in plaats van zwakker.

Twee dingen die dit bestand met opzet NIET doet.

**Geen eigen parameterlijst.** De firmware heeft er een, ingebakken, en die is
wat er werkelijk tussen een klik en de radio staat. Een tweede lijst hier zou
vroeg of laat afwijken, en de dag dat dat gebeurt biedt de pagina een parameter
aan die de node weigert -- of erger, ze zijn het eens over de naam en oneens over
de grenzen. Dus haalt de server de lijst op bij de node zelf (``GET /api/cfg``)
en gebruikt die om het formulier te bouwen én om een tikfout alvast te weigeren.
Dat blijft "controleren aan beide kanten": hier voor een snelle, leesbare fout,
daar omdat dat de controle is die telt.

**Geen schrijfweg naar een node die alleen over LoRa bereikbaar is.** Die staat
ontworpen in de documentatie en is bewust nog niet gebouwd: hij vraagt een
toestandsmachine naast de bestaande sweep, en de node waarvoor hij bestaat is
een stock MeshCore-repeater op een dak die op geen andere manier te bereiken is.
Zoiets bouw je tegen een node die iemand fysiek kan aanraken, en niet eerder.
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

def cfg_route(rep, relay=None) -> dict:
    """Mag en kan de site een instelling van deze repeater zetten?

    Bewust een eigen sleutel naast ``commanding.route_for`` en naast
    ``firmware.ota_route``, om dezelfde reden als daar: de drie reizen over
    verschillende dingen. Een node kan opdrachten over MQTT aannemen zonder
    IP-pad (dan geen schrijfweg), en een node kan een image aannemen terwijl zijn
    firmware nog geen /api/cfg kent (dan ook niet). Ze door elkaar halen levert
    precies één soort fout op, en dat is de knop die belooft wat hij niet kan.
    """
    host = (_field(rep, "ota_host") or "").strip()
    fw = _field(rep, "fw_meshmanager") or ""
    version = commanding.parse_version(fw)
    relayed = commanding.is_relayed(rep)

    out = {"can": False, "blocker": "", "host": host, "fw": fw,
           "min_fw": ".".join(str(n) for n in MIN_CFG_VERSION), "relayed": relayed}

    # Volgorde omgedraaid ten opzichte van de eerste opzet, en dat is de correctie
    # van een ontwerpfout. 'De server heeft geen inloggegevens' stond bovenaan en
    # kreeg daardoor ook de doorgestuurde nodes te pakken -- terwijl juist voor
    # die nodes de server nooit inloggegevens hoeft te hebben. Hun rechten horen
    # bij de monitor. De blijvende toestand hoort dus eerst, en de ontbrekende
    # weblogin geldt alleen voor de nodes waarvoor die weg überhaupt bestaat.
    if relayed:
        # Voor de dakrepeater is dit de blijvende toestand en geen ontbrekende
        # instelling: hij draait geen firmware van ons en heeft geen IP-pad. De
        # weg die voor hem ontworpen is loopt via zijn monitor en bestaat nog
        # niet, en dat hoort er te staan in plaats van een leeg adresveld.
        out["blocker"] = "relayed_only"
    elif not firmware.NODE_USER:
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


def write(rep, key: str, value: str, confirm: str = "") -> dict:
    """Eén parameter zetten en teruggeven wat er ná afloop in de node staat.

    Het antwoord van de node draagt ``asked`` en ``applied`` apart, en dat is
    geen omslachtigheid maar de kern van deze functie. MeshCore antwoordt "OK" op
    dingen die het niet werkelijk heeft overgenomen: ``set lat`` is een kale
    atof() die van een tikfout 0.0 maakt, en ``advert.interval`` wordt bewaard
    als minuten/2 in één byte, zodat 61 als 60 terugkomt. Wie hier "OK" zou
    teruggeven, zou dezelfde onwaarheid vertellen als de oude OTA-weg deed.
    """
    route = cfg_route(rep)
    out = {"ok": False, "step": "", "msg": "", "key": key,
           "asked": value, "applied": "", "exact": False, "reboot": False}

    if not route["can"]:
        out.update(step="route", msg=f"deze node kan geen instelling ontvangen "
                                     f"({route['blocker']})")
        return out

    listing = params(route["host"])
    if not listing["ok"]:
        out.update(step="lijst", msg=listing["error"])
        return out

    spec = next((p for p in listing["params"] if p.get("key") == key), None)
    if spec is None:
        out.update(step="sleutel",
                   msg="deze node biedt die parameter niet aan om van afstand te zetten")
        return out

    problem = _check(spec, value)
    if problem:
        out.update(step="waarde", msg=f"{key} {problem}")
        return out

    problem = confirmation_for(spec, rep, confirm)
    if problem:
        out.update(step="bevestiging", msg=problem)
        return out

    body = urllib.parse.urlencode({"key": key, "value": value}).encode()
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
            return out
    except (urllib.error.URLError, OSError, ValueError, TimeoutError) as exc:
        out.update(step="verbinding",
                   msg=f"geen antwoord van de node ({type(exc).__name__})")
        return out

    out.update(
        ok=bool(answer.get("ok")),
        step=str(answer.get("step") or ""),
        msg=str(answer.get("msg") or ""),
        applied=str(answer.get("applied") or ""),
        exact=bool(answer.get("exact")),
        # 'radio' wordt bewaard maar pas bij een herstart actief. Het teruglezen
        # toont dus de nieuwe waarden terwijl de radio nog op de oude staat, en
        # pas bij die herstart blijkt of ze kloppen -- precies het geval waarin
        # een node niet terugkomt. De pagina hoort dat te zeggen.
        reboot=bool(spec.get("reboot")),
    )

    # De naam staat ook in onze eigen tabel; die zou anders tot het volgende
    # statistiekbericht de oude blijven tonen naast een melding dat het gelukt is.
    if out["ok"] and key == "name" and out["applied"]:
        rid = int(_field(rep, "id") or 0)
        if rid:
            db.execute("UPDATE repeaters SET name=? WHERE id=?", (out["applied"][:64], rid))
    return out


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
