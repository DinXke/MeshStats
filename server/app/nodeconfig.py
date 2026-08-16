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
MIN_CFG_VERSION = (1, 13, 0)

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

    if not firmware.NODE_USER:
        out["blocker"] = "no_credentials"
    elif relayed:
        # Voor de dakrepeater is dit de blijvende toestand en geen ontbrekende
        # instelling: hij draait geen firmware van ons en heeft geen IP-pad. De
        # weg die voor hem ontworpen is loopt via zijn monitor en bestaat nog
        # niet, en dat hoort er te staan in plaats van een leeg adresveld.
        out["blocker"] = "relayed_only"
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
    404 draait er firmware van voor 1.13.0; dat is een versie en geen storing, en
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
                            "(ouder dan 1.13.0)")
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


def _check(spec: dict, value: str) -> str:
    """De grenzen van de node hier alvast toepassen. Lege string = in orde.

    Dit is de beleefdheid, niet de beveiliging: het scheelt een netwerkronde en
    het geeft een fout die naast het invoerveld past. De controle die telt staat
    in de firmware, en die draait hoe dan ook.
    """
    kind = str(spec.get("kind") or "")
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


def write(rep, key: str, value: str) -> dict:
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
           "asked": value, "applied": "", "exact": False}

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
    )

    # De naam staat ook in onze eigen tabel; die zou anders tot het volgende
    # statistiekbericht de oude blijven tonen naast een melding dat het gelukt is.
    if out["ok"] and key == "name" and out["applied"]:
        rid = int(_field(rep, "id") or 0)
        if rid:
            db.execute("UPDATE repeaters SET name=? WHERE id=?", (out["applied"][:64], rid))
    return out
