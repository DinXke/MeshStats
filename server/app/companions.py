"""MeshCore-companions: de beheerde handzenders (T1000-E e.d.) over het mesh.

Waar dit past
-------------
Een companion is geen node die publiceert of uitgevraagd wordt -- hij is een
BESTEMMING. Deze module kent drie dingen en niets daarbuiten:

1.  De **commandotaal** van de companion (de T1000-E-DM-commando's). Die staat
    hier geïsoleerd, op één plek, precies zoals de room-API-vorm in ``rooms.py``
    geïsoleerd staat: wijkt de firmware ooit af, dan is dit het enige bestand dat
    mee hoeft.
2.  De **weg naar buiten**. Een commando is een DM naar de pubkey van de
    companion, en die DM vertrekt bij een AFZENDER-node via ``rooms.bot_sendto``
    (``POST /bot/sendto`` op die node). Het lokale mesh routeert de DM daarna
    vanzelf verder via de repeater -- daar hoeft deze module niets voor te doen.
3.  De **locatie**, de andere kant op: geen commando naar de companion maar een
    periodieke GET van ``/companions.json`` op de afzender-node, die vertelt
    waar elke companion die hij kent laatst gezien is. Zie ``poll_locations``
    onderaan dit bestand voor de aannames over dat endpoint.
4.  De **valescalatie**. Dezelfde ronde leest ook ``fall_ts``/``fall_kind`` uit
    ``/companions.json`` en stuurt bij een NIEUWE val een DM naar elke
    toegewezen ontvanger van die companion (``companion_alerts``, beheerd op de
    companion-pagina). Ontdubbeld op ``fall_ts``, zodat één val nooit twee keer
    alarmeert -- zie ``_escalate_fall``.

Waarom het vervoer pluggable blijft
-----------------------------------
De enige plek die weet HOE een DM de deur uit gaat, is ``send_dm``. Nu is dat de
bot van een afzender-node; een latere oorsprong (bijvoorbeeld MQTT rechtstreeks
naar de repeater) hoeft alleen deze ene functie een tweede tak te geven. De
routes, de commandobouwer en de UI weten van dat vervoer niets -- ze geven een
afzender-node en een tekst, en krijgen ``{"ok","error"}`` terug.

Serieel versus mesh
-------------------
Over het mesh draagt elk commando een ``!`` vooraan (zo onderscheidt de firmware
een commando van een gewone chatregel). Over de SERIËLE CLI -- de browser-Web-
Serial-weg, volledig client-side -- vervalt die ``!``. Deze module bouwt de
MESH-vorm (mét ``!``); de seriële kant strippt hem in de browser. De lijst met
commando's (``COMMANDS``) is de gedeelde bron voor beide, zodat ze niet uiteen
kunnen lopen.
"""
from __future__ import annotations

import logging
import re
import threading
import time

from fastapi import APIRouter, Header, HTTPException, Request

from . import audit, config, db, nodeconfig, rooms, sensornode, sensorpush

log = logging.getLogger("meshmanager.companions")

# --- de commandotaal, op één plek ---------------------------------------------
#
# Elk commando: de sleutel (zoals de route/UI hem noemt), een label voor het
# scherm, en de argumentvorm. De bouwer hieronder is de enige die deze vorm tot
# een DM-tekst maakt; de UI toont ``label`` en levert de argumenten aan.
# De SLOTS waarop een beltoon en een volume gezet worden: de ernstniveaus. De
# firmware kent per ernst een eigen beltoon en volume, dus ``!tune`` en ``!vol``
# nemen allebei een slot -- dat is precies waar de gebruiker de knip wil ("harde
# waarschuwing luid, gewoon bericht zacht").
SEVERITIES = ("H", "M", "L", "find", "msg")
GPS_MODES = ("on", "off", "ondemand")
ONOFF = ("on", "off")
VOL_LEVELS = ("0", "1", "2", "3")

# De ingebouwde beltoon-bibliotheek van de firmware. LETTERLIJK deze namen -- ze
# staan zo in de firmware en zijn geen tekst om te vertalen. Ze voeden zowel de
# keuzelijst als de validatie: een beltoon die de firmware niet kent, hoort niet
# de band op gestuurd te worden.
TUNE_LIBRARY = ("mario-main", "mario-die", "mario-1up", "coin", "powerup",
                "warning", "chime", "alert", "beep")

# De actie bij een stille periode: dempen, een vast volume (0-3), of uit.
QUIET_ACTIONS = ("mute", "0", "1", "2", "3", "off")

# De gevoeligheidsniveaus van de valdetectie (``!fall sens <niveau>``).
FALL_SENS_LEVELS = ("low", "med", "high")

# De ontvangststand van de radio (``!rxps <stand>``): uit, of een van twee
# vaste afwegingen tussen ontvangstkans en stroomverbruik. LETTERLIJK de
# firmwarewoorden, net als TUNE_LIBRARY hierboven.
RXPS_MODES = ("off", "conservative", "balanced")

# De radioparameters die met een expliciete ``confirm`` gezet mogen worden --
# zie ``build`` en de RADIO-waarschuwing in companions.js/companion.html. Een
# verkeerde waarde op één van deze vijf kan de companion van het mesh laten
# vallen (een andere frequentie/bandbreedte/spreidingsfactor/coderatio/
# zendvermogen dan de rest van het mesh spreekt niemand meer aan zonder een
# fysieke, seriële herstelsessie) -- vandaar de aparte, opzichtige weg in
# plaats van gewoon nog een regel in ``COMMANDS``.
RADIO_FIELDS = ("freq", "bw", "sf", "cr", "tx-power")
# Elk van de vijf velden is in de firmware-CLI een kaal getal (Hz/kHz al naar
# gelang het veld, geen eenheid erbij) -- deze validatie weert alleen tekst die
# duidelijk geen getal is (spaties, letters, een tweede ``confirm``); ze kent
# geen bereik per veld, want dat hoort bij de firmware en niet hier verzonnen
# te worden.
_RADIO_VALUE_RE = re.compile(r"^-?\d+(\.\d+)?$")

COMMANDS = {
    "find":     {"label": "Find-me (laten piepen/knipperen)", "args": []},
    "findstop": {"label": "Find-me stoppen", "args": []},
    "mute":     {"label": "Dempen", "args": ["state", "followapp"]},  # on|off [followapp]
    "vol":      {"label": "Volume (per ernst of globaal)", "args": ["slot", "level"]},  # <slot|leeg> <0-3>
    "tune":     {"label": "Beltoon per ernst", "args": ["slot", "name"]},  # <slot> preset <name>
    "tunes":    {"label": "Beltoon-bibliotheek opvragen", "args": []},
    "play":     {"label": "Beltoon afspelen (preview)", "args": ["name"]},  # <name>
    "quiet":    {"label": "Stille periode", "args": ["range", "action"]},   # <sH>-<eH> mute|<0-3>|off
    "gps":      {"label": "GPS", "args": ["mode"]},              # on|off|ondemand
    "loc":      {"label": "Locatie opvragen", "args": []},
    "locpush":  {"label": "Auto-locatie-push interval (min, 0=uit)", "args": ["min"]},
    "cfg":      {"label": "Configuratie opvragen", "args": []},
    "status":   {"label": "Status opvragen", "args": []},
    "allow":    {"label": "Toegestane-lijst", "args": ["sub", "value"]},
    "preset":   {"label": "Snelbericht-preset", "args": ["slot", "text"]},
    # ``sub`` kiest de valdetectie-subopdracht; ``state``/``level``/``action``/
    # ``value`` zijn er naar gelang die keuze bij nodig -- zie ``build``.
    # Zonder ``sub`` (leeg) blijft de OUDE vorm werken: alleen ``state``
    # (on/off), zodat bestaande aanroepers dit commando ongewijzigd gebruiken.
    "fall":     {"label": "Valdetectie", "args": ["sub", "state", "level", "action", "value"]},
    "rxps":     {"label": "Radio-ontvangststand (rxps)", "args": ["mode"]},
    # De WAARSCHUWINGS-commando's: zie RADIO_FIELDS hierboven.
    "radio":     {"label": "Radioparameter zetten (WAARSCHUWING)", "args": ["field", "value"]},
    "radioshow": {"label": "Radioparameters tonen (alleen-lezen)", "args": []},
    "ping":     {"label": "Ping", "args": []},
}

_HEX64 = re.compile(r"^[0-9a-fA-F]{64}$")
_HEXPREFIX = re.compile(r"^[0-9a-fA-F]+$")
_QUIET = re.compile(r"^\d{1,2}-\d{1,2}$")

# --- de tijdvensters van het locatie-spoor ------------------------------------
#
# Op één plek, want ze voeden ZOWEL de publieke deel-link (/loc/<token> in
# routes_public) ALS de beheerkaart (routes_companions): sleutel -> venster in
# seconden, plus een leesbaar label voor de knoppen. Twee kopieën zouden vroeg
# of laat uiteenlopen (de ene "7d", de andere "week"), en dan tekent de
# publieke kant een ander spoor dan de beheerkant voor dezelfde keuze. De
# volgorde is de knopvolgorde (kort -> lang); ``TRACK_WINDOW_DEFAULT`` is wat
# een pagina zonder (of met een onbekende) keuze toont.
TRACK_WINDOWS = (
    ("1h", 3600, "1 u"),
    ("6h", 6 * 3600, "6 u"),
    ("24h", 24 * 3600, "24 u"),
    ("7d", 7 * 24 * 3600, "7 d"),
)
TRACK_WINDOW_DEFAULT = "24h"


def track_window_seconds(key: str) -> int:
    """Het venster in seconden voor een venstersleutel; een onbekende of lege
    sleutel valt terug op ``TRACK_WINDOW_DEFAULT`` -- een verzonnen querystring
    hoort een nette standaard te geven, geen fout."""
    for k, secs, _label in TRACK_WINDOWS:
        if k == str(key or "").strip():
            return secs
    return track_window_seconds(TRACK_WINDOW_DEFAULT)


def track_windows_for_ui() -> list:
    """De vensters als lijst van ``{"key","label"}`` voor de knoppen in de
    sjablonen -- de seconden blijven serverzijde."""
    return [{"key": k, "label": label} for k, _secs, label in TRACK_WINDOWS]


def valid_pubkey(pubkey: str) -> bool:
    """Een volledige companion-sleutel: precies 64 hex-tekens. Een DM richt zich op
    de sleutel, dus een halve sleutel is geen bestemming -- dezelfde regel als bij
    de bot-ontvangers en de ACL in ``rooms.py``."""
    return bool(_HEX64.match(str(pubkey or "").strip()))


def _add_del_list(prefix: str, sub: str, value: str) -> tuple[str, str]:
    """De gedeelde add/del/list-vorm: ``allow`` en ``fall target`` delen hem
    letterlijk in de firmware-CLI, en een tweede kopie van dezelfde drie
    regels zou op den duur uiteen kunnen lopen. Geeft ``(body, error)`` terug;
    bij een fout is ``body`` leeg en hoort de aanroeper te stoppen.
    """
    if sub == "list":
        return f"!{prefix} list", ""
    if sub == "add":
        if not _HEX64.match(value):
            return "", f"{prefix} add verwacht een volledige pubkey van 64 hex-tekens"
        return f"!{prefix} add {value}", ""
    if sub == "del":
        if len(value) < 6 or not _HEXPREFIX.match(value):
            return "", f"{prefix} del verwacht een prefix van minstens 6 hex-tekens"
        return f"!{prefix} del {value}", ""
    return "", f"{prefix} verwacht add, list of del"


def build(cmd: str, args: dict | None = None) -> dict:
    """Een companion-commando tot een MESH-DM-tekst maken (mét ``!``).

    ``{"ok","error","body"}``. Valideert de argumenten hier en niet in de route,
    zodat de seriële weg (die dezelfde COMMANDS deelt) op dezelfde grenzen kan
    leunen. De teksten zijn Nederlands en voor het scherm.
    """
    out = {"ok": False, "error": "", "body": ""}
    a = args or {}
    spec = COMMANDS.get(cmd)
    if spec is None:
        out["error"] = f"onbekend commando: {cmd}"
        return out

    def val(name: str) -> str:
        return str(a.get(name) or "").strip()

    if cmd in ("find", "findstop", "loc", "cfg", "status", "tunes", "ping"):
        body = f"!{cmd}"
    elif cmd == "mute":
        state = val("state").lower()
        if state not in ONOFF:
            out["error"] = "mute verwacht 'on' of 'off'"
            return out
        # AANNAME: "followapp" is een optionele derde token die het dempen aan
        # de stand van de companion-APP koppelt in plaats van een vaste
        # on/off -- er was geen levende firmware met deze optie beschikbaar om
        # tegen te toetsen, dit is de meest voor de hand liggende lezing van de
        # opdracht ("mute[ followapp]"). Wijkt de firmware af, dan is dit de
        # ene plek die mee moet.
        followapp = val("followapp").lower() in ("1", "true", "on", "yes")
        body = f"!mute {state} followapp" if followapp else f"!mute {state}"
    elif cmd == "fall":
        sub = val("sub").lower()
        if not sub:
            # De OUDE, eenvoudige vorm blijft werken (alleen state): bestaande
            # aanroepers (en hun tests) geven geen ``sub`` mee.
            state = val("state").lower()
            if state not in ONOFF:
                out["error"] = "fall verwacht 'on' of 'off'"
                return out
            body = f"!fall {state}"
        elif sub in ONOFF:
            body = f"!fall {sub}"
        elif sub == "sens":
            level = val("level").lower()
            if level not in FALL_SENS_LEVELS:
                out["error"] = f"fall sens verwacht {', '.join(FALL_SENS_LEVELS)}"
                return out
            body = f"!fall sens {level}"
        elif sub in ("nomotion", "prealarm", "mm"):
            state = val("state").lower()
            if state not in ONOFF:
                out["error"] = f"fall {sub} verwacht 'on' of 'off'"
                return out
            body = f"!fall {sub} {state}"
        elif sub == "target":
            tbody, err = _add_del_list("fall target", val("action").lower(), val("value"))
            if err:
                out["error"] = err
                return out
            body = tbody
        elif sub in ("test", "status"):
            body = f"!fall {sub}"
        else:
            out["error"] = ("fall verwacht (leeg voor) on/off, of sens, "
                            "nomotion, prealarm, target, mm, test of status")
            return out
    elif cmd == "vol":
        slot = val("slot")
        lvl = val("level")
        if lvl not in VOL_LEVELS:
            out["error"] = "volume is 0 t/m 3"
            return out
        if not slot or slot.lower() == "global":
            # Globaal volume: één niveau voor alle ernsten, geen slot in de body.
            body = f"!vol {lvl}"
        elif slot in SEVERITIES:
            body = f"!vol {slot} {lvl}"
        else:
            out["error"] = (f"slot moet een van {', '.join(SEVERITIES)} zijn, "
                            "of leeg/'global' voor het globale volume")
            return out
    elif cmd == "rxps":
        mode = val("mode").lower()
        if mode not in RXPS_MODES:
            out["error"] = f"rxps verwacht {', '.join(RXPS_MODES)}"
            return out
        body = f"!rxps {mode}"
    elif cmd == "radioshow":
        body = "!radio show"
    elif cmd == "radio":
        field = val("field").lower()
        value = val("value")
        if field not in RADIO_FIELDS:
            out["error"] = f"radio verwacht een van {', '.join(RADIO_FIELDS)}"
            return out
        if not _RADIO_VALUE_RE.match(value):
            out["error"] = "radio verwacht een numerieke waarde"
            return out
        # ``confirm`` staat vast mee: de firmware eist het kennelijk zelf al
        # (vandaar de eis in de opdracht), en deze server voegt hem hoe dan ook
        # toe zodat een operator hem niet kan vergeten -- de WAARSCHUWING in de
        # UI is de échte rem, niet dit token.
        body = f"!radio {field} {value} confirm"
    elif cmd == "gps":
        mode = val("mode").lower()
        if mode not in GPS_MODES:
            out["error"] = "gps verwacht on, off of ondemand"
            return out
        body = f"!gps {mode}"
    elif cmd == "tune":
        slot = val("slot")
        name = val("name")
        if slot not in SEVERITIES:
            out["error"] = f"slot moet een van {', '.join(SEVERITIES)} zijn"
            return out
        if name not in TUNE_LIBRARY:
            out["error"] = f"onbekende beltoon (kies uit {', '.join(TUNE_LIBRARY)})"
            return out
        body = f"!tune {slot} preset {name}"
    elif cmd == "play":
        name = val("name")
        if name not in TUNE_LIBRARY:
            out["error"] = f"onbekende beltoon (kies uit {', '.join(TUNE_LIBRARY)})"
            return out
        body = f"!play {name}"
    elif cmd == "quiet":
        rng = val("range").lower()
        action = val("action").lower()
        # ``off`` schakelt de hele stille periode uit; dan is er geen bereik en
        # geen actie -- ``!quiet off``. Anders horen bereik én actie er allebei bij.
        if rng in ("", "off") or action == "off":
            body = "!quiet off"
        elif not _QUIET.match(rng):
            out["error"] = "stille periode als <startuur>-<einduur> (bv. 22-7) of 'off'"
            return out
        elif action not in QUIET_ACTIONS:
            out["error"] = "actie is mute, 0 t/m 3, of off"
            return out
        else:
            body = f"!quiet {rng} {action}"
    elif cmd == "allow":
        body, err = _add_del_list("allow", val("sub").lower(), val("value"))
        if err:
            out["error"] = err
            return out
    elif cmd == "preset":
        slot = val("slot")
        text = val("text")
        if slot not in ("1", "2", "3"):
            out["error"] = "preset-slot is 1, 2 of 3"
            return out
        if not text:
            out["error"] = "een preset-tekst mag niet leeg zijn"
            return out
        body = f"!preset {slot} {text}"
    elif cmd == "locpush":
        # Stelt de frequentie van de auto-locatie-push in (`!locpush <min>`); het
        # doel is al op de companion gezet. 0/off = uit. De companion houdt het
        # bestaande doel wanneer je enkel het interval meegeeft.
        m = val("min").lower()
        if m in ("off", "uit"):
            body = "!locpush off"
        elif not m.isdigit():
            out["error"] = "locpush verwacht minuten (0=uit) of 'off'"
            return out
        elif int(m) > 1440:
            out["error"] = "locpush: minuten 0 t/m 1440"
            return out
        else:
            body = "!locpush off" if int(m) == 0 else f"!locpush {int(m)}"
    else:  # pragma: no cover -- COMMANDS en deze takken lopen synchroon
        out["error"] = f"commando {cmd} nog niet ondersteund"
        return out

    out["body"] = body
    out["ok"] = True
    return out


# --- de weg naar buiten (het enige dat weet HOE) ------------------------------

def send_dm(sender_rep, dest_pubkey: str, msg: str, bot=None) -> dict:
    """Een DM van een afzender-node naar een companion-pubkey sturen.

    De ene plek die het vervoer kent. Nu: de bot van de afzender-node
    (``rooms.bot_sendto`` -> ``POST /bot/sendto``), waarna het mesh de DM via de
    lokale repeater verder routeert. Een toekomstige oorsprong (MQTT ->
    repeater) hoort HIER een tweede tak te krijgen en nergens anders.

    ``bot`` kiest WELKE bot-identiteit van de afzender-node de DM verstuurt op
    een node met meerdere bots (zie ``resolve_bot``/``default_bot_for``
    hieronder) -- leeg laat de node zijn standaardbot gebruiken.
    """
    if not valid_pubkey(dest_pubkey):
        return {"ok": False, "error": "een volledige pubkey van 64 hex-tekens is vereist"}
    if not str(msg or "").strip():
        return {"ok": False, "error": "een bericht mag niet leeg zijn"}
    return rooms.bot_sendto(sender_rep, dest_pubkey, msg, bot=bot)


def send_command(sender_rep, dest_pubkey: str, cmd: str,
                 args: dict | None = None, bot=None) -> dict:
    """Een companion-commando bouwen én versturen. ``{"ok","error","body"}``.

    De bouwvalidatie eerst, zodat een fout commando nooit als lege of halve DM de
    band op gaat. De ``body`` komt mee terug zodat de pagina kan tonen wat er
    precies verstuurd is -- op een gedeelde band is "wat ging er de deur uit" geen
    detail. ``bot`` gaat ongewijzigd door naar ``send_dm``.
    """
    gebouwd = build(cmd, args)
    if not gebouwd["ok"]:
        return {"ok": False, "error": gebouwd["error"], "body": ""}
    res = send_dm(sender_rep, dest_pubkey, gebouwd["body"], bot=bot)
    return {"ok": res["ok"], "error": res["error"], "body": gebouwd["body"]}


# --- welke BOT-IDENTITEIT van de afzender-node verstuurt ----------------------
#
# Een afzender-node kan meer dan één bot hosten (``rooms.bots``): de ALARM-bot
# voor zijn eigen sensoren, en -- sinds de MGMT-uitbreiding van de firmware --
# een aparte BEHEER-bot die companion-commando's verstuurt (bv.
# "BE-HSS-DinX-MGMT"). Companion-verkeer hoort NIET over de alarm-bot te lopen
# als er een aparte beheer-bot bestaat: dat mengt twee soorten verkeer op één
# identiteit, en de ontvanger kan niet meer zien welke van de twee een DM stuurde.
#
# Kort gecached en niet in de databank: dit is ontdekte informatie van het mesh
# (net als rooms.contacts), niet iets deze server invoert of beheert. Eén regel
# per node, met een korte levensduur zodat een nieuw geadverteerde bot niet
# urenlang onzichtbaar blijft, terwijl een pagina die na elkaar meerdere
# commando's verstuurt niet bij elke knop opnieuw hoeft te pollen.
_BOTS_CACHE_TTL_S = 30
_bots_cache: dict[int, dict] = {}   # rep_id -> {"at": monotonic, "data": rooms.bots()}


def reset_bots_cache() -> None:
    """De bot-cache leegmaken. Alleen voor de tests (zelfde soort functie als
    ``sensorpush.reset``): elke test krijgt een verse databank met IDs die
    weer bij 1 beginnen, en zonder deze reset zou een node-id uit een VORIGE
    test binnen ``_BOTS_CACHE_TTL_S`` de bots van een heel andere node uit de
    HUIDIGE test kunnen serveren."""
    _bots_cache.clear()


def cached_bots(rep, timeout: int | None = None,
                max_age: float = _BOTS_CACHE_TTL_S) -> dict:
    """``rooms.bots(rep)``, met een korte cache per node-id. ``max_age=0``
    forceert een verse ophaling."""
    rid = int(rep["id"])
    hit = _bots_cache.get(rid)
    now = time.monotonic()
    if hit is not None and now - hit["at"] < max_age:
        return hit["data"]
    data = rooms.bots(rep, timeout)
    _bots_cache[rid] = {"at": now, "data": data}
    return data


def default_bot_for(rep, timeout: int | None = None) -> str | None:
    """Welke bot een companion-commando standaard verstuurt vanaf ``rep``: de
    NIET-alarm-bot als de node er een heeft (de beheer-bot), anders de
    alarm-bot, anders ``None`` -- geen ``/bots.json`` (oudere firmware met
    maar één, naamloze bot) betekent geen ``bot=`` meesturen, en dan gebruikt
    de node vanzelf zijn ene bot, precies het gedrag van vóór deze uitbreiding.

    Geeft de NAAM terug als de node er een meldt (leesbaarder in het
    audittrail en de UI), anders de index als tekst.
    """
    data = cached_bots(rep, timeout)
    if not data["ok"] or not data["bots"]:
        return None
    niet_alarm = [b for b in data["bots"] if not b["alert"]]
    keuze = niet_alarm[0] if niet_alarm else data["bots"][0]
    return keuze["name"] or str(keuze["idx"])


def resolve_bot(rep, override: str | None = None, preferred: str | None = None,
                timeout: int | None = None) -> str | None:
    """Welke bot-waarde een verzending vanaf ``rep`` moet dragen: een expliciete
    keuze op het formulier (``override``) wint, dan de bewaarde voorkeur van de
    companion (``preferred``, ``companions.preferred_bot``), dan de
    MGMT-standaard van de node (``default_bot_for``)."""
    for keuze in (override, preferred):
        if keuze and str(keuze).strip():
            return str(keuze).strip()
    return default_bot_for(rep, timeout)


# --- welke nodes een DM kunnen versturen --------------------------------------

def can_send_from(rep) -> bool:
    """Of deze node een DM kan versturen: heeft ze een bereikbare eigen API (waar
    de bot op draait)? ``nodeconfig._route_sensor`` leest alleen velden -- geen
    netwerk -- en zegt of er een adres is waar al eens iets van terugkwam."""
    return bool(nodeconfig._route_sensor(rep)["can"])


def sender_candidates(reps) -> list:
    """De nodes die als afzender in de keuzelijst horen, uit een reeks repeaters.

    Alleen nodes met een bereikbare eigen API: een afzender die geen ``/bot/sendto``
    heeft, is geen afzender en hoort niet als kandidaat op het scherm te staan --
    dezelfde eerlijkheid als bij de monitor-keuzelijst. De volgorde blijft die van
    de aanroeper (op naam), zodat tientallen kandidaten voorspelbaar staan.
    """
    return [r for r in reps if can_send_from(r)]


# --- de locatie van companions, uit /companions.json van hun afzender --------
#
# Waarom dit hier staat en niet in sensornode.py: die module kent de NODE-kant
# van dit soort endpoints (adres, weblogin, foutafhandeling over HTTP) en
# levert de kale GET (``sensornode._json``); WELKE pubkeys daarna companions
# zijn en welke rij in DEZE tabel ze bijwerken is companion-kennis, en die
# hoort bij de rest van de companion-laag. Dezelfde scheiding als bij
# ``send_dm``: sensornode.py weet HOE er een verzoek de deur uit gaat, deze
# module weet WAT ermee gebeurt.
#
# ``/companions.json`` is een derde route op dezelfde eigen API als
# ``/status.json``, ``/cfg.json`` en ``/acl.json`` -- vandaar dat het adres
# hetzelfde veld is (``repeaters.sensor_host``) en geen nieuw veld: het is de
# node die zijn HTTP-API aanbiedt, niet een derde soort adres.
#
# AANNAME over de vorm van het antwoord (er was geen levende node met dit
# endpoint beschikbaar tijdens het bouwen hiervan):
#   {"companions": [{"name", "pubkey" (64 hex), "lat", "lon", "seen",
#                     "fall_ts", "fall_kind", "batt" (0-100)}, ...]}
# ``batt`` is optioneel op dezelfde manier als ``fall_ts``/``fall_kind``: een
# companion die zijn batterij niet meldt, laat het veld weg, en dan blijft de
# kolom NULL en toont de UI niets (zie ``_valid_batt``).
# ``fall_ts``/``fall_kind`` zijn optioneel: een companion die nooit gevallen
# is, hoort ze gewoon niet te hebben, en dat degradeert naar "geen val om te
# verwerken" -- zie ``_secs_to_epoch`` en de valafhandeling in
# ``poll_locations``. Ontbreekt het endpoint zelf (oudere firmware) of geeft de
# node een lege lijst terug, dan degradeert dit naar niets bijwerken -- geen
# fout die de rest van de pollronde raakt.

def location_nodes(only_rep_id: int | None = None) -> list:
    """De nodes waar minstens één companion zijn afzender op heeft staan.

    Dat zijn de enige nodes waarvoor ``/companions.json`` zinvol is: een node
    zonder companion hoeft dat endpoint niet te hebben, en bij elke sensornode
    aankloppen zou voor niets 404's opleveren op nodes die dit onderdeel niet
    kennen. ``sender_repeater_id`` is hier de aanwijzing WELKE node de
    companion aan het mesh hangt, niet alleen een voorkeur voor het versturen.

    ``only_rep_id`` beperkt de ronde tot precies die ene node -- gebruikt door
    de pagina-gedreven ONDEMAND-poll (``poll_now``) op de companion-detailpagina,
    waar maar één node relevant is en de andere niet gestoord hoeven te worden
    voor een klik op één pagina.
    """
    ids = sorted({int(c["sender_repeater_id"]) for c in db.list_companions()
                  if c["sender_repeater_id"]})
    if only_rep_id is not None:
        ids = [i for i in ids if i == int(only_rep_id)]
    if not ids:
        return []
    marks = ",".join("?" * len(ids))
    return db.q(f"SELECT * FROM repeaters WHERE id IN ({marks})", tuple(ids))


# Een waarde vanaf deze grens lezen we als een absoluut tijdstip (epoch-
# seconden na 1 januari 2001) en niet als een ouderdom in seconden. De reden om
# hier een gok te MOETEN doen staat in de kolomtoelichting bij
# ``companions.last_seen`` in db.py: dit nodetype heeft na een herstart een
# RTC die op 15 mei 2024 staat, dus zo'n veld kan zowel een epoch als een
# ouderdom betekenen en er is geen vlag die het zegt. Dezelfde heuristiek geldt
# voor ``fall_ts``, en om een andere maar even dwingende reden: een ouderdom
# die bij elke ronde OPLOOPT (zoals bij "seen") zou als RUWE waarde nooit twee
# keer hetzelfde getal geven, en dan zou de ontdubbeling hieronder een val bij
# elke volgende ronde als "nieuw" blijven zien -- de escalatie zou nooit
# stoppen. Omgerekend naar een epoch met déze servers klok blijft de waarde
# stabiel zolang het om dezelfde gebeurtenis gaat, of de node hem nu als
# ouderdom of als absolute tijd meestuurt.
_EPOCH_2001 = 978307200


def _secs_to_epoch(secs, now: float) -> int | None:
    """Een secondenveld uit /companions.json (``seen`` of ``fall_ts``) naar een
    epoch met DEZE servers klok.

    Zie ``_EPOCH_2001`` hierboven voor de heuristiek. Alles wat geen bruikbaar
    getal is (ontbrekend, negatief, een string) levert None op -- geen gok
    beter dan een verkeerde gok die er hetzelfde uitziet als een echte tijd.
    """
    if not isinstance(secs, (int, float)) or isinstance(secs, bool) or secs < 0:
        return None
    secs = int(secs)
    return secs if secs >= _EPOCH_2001 else int(now) - secs


def _valid_batt(value) -> int | None:
    """Een batterijveld uit /companions.json of de instant-push naar een heel
    percentage (0-100), of None.

    Optioneel overal: een companion die zijn batterij niet meldt, laat dit veld
    weg, en dat degradeert naar "geen batterij om bij te werken" -- de kolom
    blijft dan NULL en de UI toont niets. Alles wat geen bruikbaar getal in het
    bereik 0-100 is (ontbrekend, een string, een bool, buiten bereik) levert
    None op, net als ``_secs_to_epoch``: geen gok beter dan een verkeerde gok
    die er als een echte stand uitziet. De keuring staat HIER, op één plek,
    zodat db.set_companion_batt en beide ingestwegen erop kunnen leunen.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    pct = int(value)
    return pct if 0 <= pct <= 100 else None


# De ronde kende de kind-woorden nog niet toen ``fall`` als commando bijkwam
# (zie COMMANDS hierboven, dat de companion vertelt of hij valdetectie AANZET);
# dit zijn de kinds die de node meldt ALS hij een val ziet. Geen validatie die
# een onbekend woord weggooit: een firmware-update die een vierde soort
# toevoegt, hoort een alarm te blijven opleveren met dat woord erin -- gewoon
# niet vertaald -- in plaats van stilzwijgend te verdwijnen.

def _fall_text(name: str, kind: str, lat, lon) -> str:
    """De tekst van een valalarm. ``kind`` gaat ONVERTAALD mee (``val``,
    ``nomotion``, ``sos``, of wat de node ook meldt) -- een ontvanger die de
    firmwaretekst al kent, hoeft geen tweede vertaling te krijgen die net iets
    anders zegt. De OSM-link staat er alleen bij als er een positie is: een val
    zonder bekende locatie levert dan geen kapotte link op.
    """
    kind_txt = str(kind or "").strip() or "onbekend"
    if lat is not None and lon is not None:
        plek = f"{lat},{lon}"
        link = (f" — https://www.openstreetmap.org/?mlat={lat}&mlon={lon}"
               f"#map=17/{lat}/{lon}")
    else:
        plek = "positie onbekend"
        link = ""
    return f"⚠️ VAL ({kind_txt}): {name} @ {plek}{link}"


def _escalate_fall(comp_row, kind: str, lat, lon) -> dict:
    """Naar elke toegewezen ontvanger van deze companion (``companion_alerts``)
    een valalarm sturen. ``{"sent", "failed"}``.

    Geen toegewezen ontvangers is geen fout: de aanroeper (``poll_locations``)
    legt de val hoe dan ook vast, zodat er later -- als iemand alsnog een
    ontvanger toevoegt -- geen OUDE val alsnog afgaat. Deze functie doet dan
    gewoon niets en meldt ``{"sent": 0, "failed": 0}``.

    Eén ontvanger die niet te bereiken is (geen afzender-node, of het versturen
    zelf mislukt) mag de andere ontvangers niet raken -- dezelfde lijn als
    ``poll_locations`` voor nodes en ``sensornode.run_once`` voor nodes:
    een lus over onafhankelijke bestemmingen stopt niet bij de eerste die stuk
    is.
    """
    out = {"sent": 0, "failed": 0}
    tekst = _fall_text(comp_row["name"], kind, lat, lon)
    actor = "systeem (valdetectie)"
    for ontv in db.list_companion_alerts(comp_row["id"]):
        rid = ontv["sender_repeater_id"] or comp_row["sender_repeater_id"]
        rep = db.qone("SELECT * FROM repeaters WHERE id=?", (rid,)) if rid else None
        kort = ontv["recipient_pubkey"][:12]
        if rep is None:
            out["failed"] += 1
            audit.log(actor, "node.instelling.merkbaar", outcome=audit.MISLUKT,
                      detail=f"valalarm {comp_row['name']} niet verstuurd naar "
                             f"{kort}: geen afzender-node ingesteld")
            continue
        # Dezelfde bot-keuze als een gewoon commando naar deze companion: de
        # bewaarde voorkeur wint, anders de MGMT-standaard van de afzender.
        bot = resolve_bot(rep, preferred=comp_row["preferred_bot"])
        res = send_dm(rep, ontv["recipient_pubkey"], tekst, bot=bot)
        if res["ok"]:
            out["sent"] += 1
        else:
            out["failed"] += 1
        audit.log(actor, "node.instelling.merkbaar", rep=rep,
                  outcome=audit.OK if res["ok"] else audit.MISLUKT,
                  detail=(f"valalarm ({kind}) van {comp_row['name']} naar {kort}"
                          if res["ok"] else
                          f"valalarm ({kind}) van {comp_row['name']} mislukt naar "
                          f"{kort}: {res['error']}"))
    return out


def _handle_fall_report(comp_row, fall_ts_raw, fall_kind, lat, lon, now: float) -> dict:
    """Eén valmelding verwerken: ontdubbelen op ``fall_ts``, escaleren als hij
    ECHT nieuw is, en de laatst-verwerkte-val bijwerken. ``{"is_fall","sent",
    "failed"}``.

    GEDEELD door de achtergrondpoll (``poll_locations``, uit
    ``/companions.json``) en de instant-push (``companion_push`` hieronder,
    uit ``POST /api/companion``) -- allebei geven ze hetzelfde soort
    ruwe ``fall_ts``/``fall_kind`` door, en een tweede kopie van deze
    ontdubbeling zou op den duur een andere grens kunnen gaan hanteren (bv. de
    ene ``>=`` en de andere ``>``) zonder dat iemand het merkt.

    ``fall_ts_raw`` van ``0`` of afwezig betekent "geen val" -- dat is het
    PUSH-contract expliciet (zie de moduletekst bij ``companion_push``), en
    het voorkomt bovendien dat ``_secs_to_epoch(0, now)`` een letterlijke 0
    als "ouderdom nul seconden" zou lezen en zo een valse escalatie op NU zou
    bouwen. Dezelfde regel geldt hier voor de pollronde: een node die ooit
    ``fall_ts: 0`` zou sturen voor "geen val" wordt nu op dezelfde manier
    gelezen, in plaats van op de vorige, striktere ``_secs_to_epoch``-uitkomst
    te vertrouwen die dat geval niet apart ving.
    """
    out = {"is_fall": False, "sent": 0, "failed": 0}
    if not fall_ts_raw:
        return out
    fall_epoch = _secs_to_epoch(fall_ts_raw, now)
    if fall_epoch is None:
        return out
    vorige = comp_row["last_escalated_fall_ts"]
    if vorige is not None and fall_epoch <= int(vorige):
        return out  # deze val is al verwerkt -- geen tweede alarm.
    kind = str(fall_kind or "").strip().lower()
    esc = _escalate_fall(comp_row, kind, lat, lon)
    db.set_companion_fall(comp_row["id"], fall_epoch, kind)
    out.update(is_fall=True, sent=esc["sent"], failed=esc["failed"])
    return out


def poll_locations(timeout: int | None = None, only_rep_id: int | None = None) -> dict:
    """Elke afzender-node om ``/companions.json`` vragen: de locaties van de
    beheerde companions bijwerken én een NIEUWE val escaleren.
    ``{"nodes", "updated", "errors", "falls", "fall_alerts_sent",
    "fall_alerts_failed"}``.

    ``only_rep_id`` -- zie ``location_nodes`` -- beperkt de ronde tot één node.
    Standaard (``None``) is dit de volledige achtergrondronde.

    Matcht op PUBKEY en niet op de node waarachter het antwoord vandaan kwam:
    ``sender_repeater_id`` is een VOORKEUR voor het versturen (zie companions.py
    hierboven) en mag veranderen zonder dat een companion zijn geschiedenis
    verliest, dus een companion die zijn locatie via een andere node meldt dan
    de ingestelde voorkeur wordt hier gewoon bijgewerkt.

    Eén node die niet antwoordt (oude firmware zonder dit endpoint, of gewoon
    onbereikbaar) mag de ronde voor de andere nodes niet breken -- dezelfde
    lijn als ``sensornode.run_once``.

    De valherkenning ONTDUBBELT STRIKT op ``fall_ts``: alleen een epoch die
    ECHT nieuwer is dan ``companions.last_escalated_fall_ts`` telt als een
    nieuwe val. Dat tijdstip wordt hoe dan ook bijgewerkt (``db.set_companion_fall``),
    ook als er geen ontvanger toegewezen is -- zie ``_escalate_fall``.
    """
    now = time.time()
    out = {"nodes": 0, "updated": 0, "errors": [],
           "falls": 0, "fall_alerts_sent": 0, "fall_alerts_failed": 0}
    for rep in location_nodes(only_rep_id):
        host = str(rep["sensor_host"] or "").strip()
        if not host:
            continue
        out["nodes"] += 1
        got = sensornode._json(host, "/companions.json", timeout)
        if not got["ok"]:
            out["errors"].append(f"{rep['name']}: {got['error']}")
            continue
        for c in (got["data"].get("companions") or []):
            if not isinstance(c, dict):
                continue
            pubkey = str(c.get("pubkey") or "").strip()
            if not valid_pubkey(pubkey):
                continue
            # Een node kan companions kennen die WIJ nog niet beheren (nog niet
            # toegevoegd op de companions-pagina); daar valt niets bij te
            # werken en niets te escaleren, dus overslaan zonder fout.
            comp_row = db.companion_by_pubkey(pubkey)
            if comp_row is None:
                continue

            lat, lon = c.get("lat"), c.get("lon")
            lat_f = lon_f = None
            if lat is not None and lon is not None:
                try:
                    lat_f, lon_f = float(lat), float(lon)
                except (TypeError, ValueError):
                    lat_f = lon_f = None
            if lat_f is not None:
                seen_epoch = _secs_to_epoch(c.get("seen"), now)
                if db.set_companion_location(pubkey, lat_f, lon_f, seen_epoch):
                    out["updated"] += 1

            # De batterij, los van de locatie: een companion kan zijn batterij
            # melden zonder GPS-fix, en een rapport zonder batterij mag een
            # bekende stand niet wissen -- vandaar alleen bijwerken als er een
            # gekeurde waarde is (_valid_batt), nooit met None. Zie
            # db.set_companion_batt.
            batt = _valid_batt(c.get("batt"))
            if batt is not None:
                db.set_companion_batt(pubkey, batt)

            val = _handle_fall_report(comp_row, c.get("fall_ts"), c.get("fall_kind"),
                                      lat_f, lon_f, now)
            if val["is_fall"]:
                out["falls"] += 1
                out["fall_alerts_sent"] += val["sent"]
                out["fall_alerts_failed"] += val["failed"]
    return out


# --- de achtergrondronde -------------------------------------------------------
#
# Een eigen klein schema en geen haakje in sensornode._run: die module kent
# companions niet (en zou dat ook niet moeten -- zie de toelichting hierboven),
# en dit bestand hangt al van sensornode af voor de kale GET. Andersom zou een
# kringverwijzing zijn. Dus: hetzelfde patroon (thread, interval, eerste-ronde-
# vertraging, aan/uit via de omgeving) maar in het klein, want er is hier geen
# zendtijdbudget te bewaken zoals bij sweepsched -- dit is een gewoon
# HTTP-verzoek over het lokale net, net als sensornode.poll.

# Was 300s (5 minuten). Een companion die zijn locatie meteen aan zijn afzender-
# node meldt, verscheen daardoor tot vijf minuten later pas hier -- vandaar de
# ONDEMAND-poll (``poll_now`` hieronder) voor het moment dat iemand ECHT naar
# een companion-pagina kijkt. Zestig seconden is de achtergrondbodem voor
# wanneer er NIEMAND kijkt (geen open pagina die de ondemand-weg raakt): ruim
# genoeg boven de vloer van 30s om geen node te bestoken, kort genoeg om een
# val binnen een minuut zonder open pagina alsnog te escaleren.
LOC_INTERVAL_S = max(30, int(config.env("COMPANION_LOC_POLL_S", "60") or 60))
LOC_ENABLED = config.env("COMPANION_LOC_POLL_ENABLED", "1").strip().lower() not in (
    "0", "false", "no", "nee", "off", "")
LOC_FIRST_RUN_DELAY_S = 25

_loc_thread = None
_loc_state = {"last_run": None, "last_result": "nog niet gedraaid"}

# --- de ONDEMAND-poll: een pagina die ECHT bekeken wordt, niet wachten -------
#
# De achtergrondronde hierboven is er voor als er niemand kijkt. Zodra iemand
# wél kijkt -- de companions-lijst, één companion-pagina, of de kaart opent, of
# de auto-ververs-timer van die pagina's tikt -- wil die persoon de ECHTE
# actuele locatie zien en niet wachten tot de volgende achtergrondronde. Deze
# functie is de ene plek die dat doet: een korte, ongeblokkeerde extra
# ``poll_locations``-ronde, met een eigen hamerbescherming zodat een pagina die
# elke 15-20s ververst (of meerdere open tabbladen tegelijk) niet bij elke tik
# opnieuw alle afzender-nodes lastigvalt. ``_ondemand_lock`` met
# ``blocking=False`` zorgt dat twee gelijktijdige verzoeken (twee tabbladen die
# toevallig samenvallen) niet allebei tegelijk pollen; de tweede ziet gewoon de
# cooldown en slaat over -- de eerste ronde werkt toch voor allebei.
ONDEMAND_MIN_GAP_S = 8
_ondemand_lock = threading.Lock()
_ondemand_last_ts = 0.0


def poll_now(timeout: int = 4, only_rep_id: int | None = None) -> dict | None:
    """Een ONDEMAND-ronde, uitgelokt door een pagina die net bekeken wordt.

    ``None`` als er te kort geleden al gepolld is (achtergrond of ondemand,
    zie ``ONDEMAND_MIN_GAP_S``) of als er al een ondemand-ronde bezig is --
    geen fout, gewoon "de data die er al is, is recent genoeg". De aanroeper
    (de JSON-route) leest daarna sowieso de databank opnieuw, dus een
    overgeslagen poll levert nooit een lege of foute pagina op, hooguit een
    paar seconden minder vers dan het zou kunnen zijn.

    ``timeout`` staat hier standaard KORTER dan de achtergrondronde
    (``sensornode.TIMEOUT_S``, doorgaans 8s): dit loopt in de request van een
    pagina die iemand open heeft staan, en een trage of onbereikbare node mag
    dat verzoek niet nodeloos lang laten hangen. ``only_rep_id`` beperkt de
    ronde tot de ene node die voor de bekeken pagina relevant is (zie
    ``location_nodes``); zonder waarde (kaart, lijst) gaat de ronde langs alle
    afzender-nodes, net als de achtergrondronde.
    """
    global _ondemand_last_ts
    if time.time() - _ondemand_last_ts < ONDEMAND_MIN_GAP_S:
        return None
    if not _ondemand_lock.acquire(blocking=False):
        return None
    try:
        if time.time() - _ondemand_last_ts < ONDEMAND_MIN_GAP_S:
            return None  # een andere thread was net vóór ons
        uit = poll_locations(timeout=timeout, only_rep_id=only_rep_id)
        _ondemand_last_ts = time.time()
        if uit["nodes"]:
            _loc_state["last_run"] = db.utcnow()
            _loc_state["last_result"] = (
                f"{uit['updated']} locatie(s) bijgewerkt van {uit['nodes']} node(s) "
                "(ondemand, pagina bekeken)")
        return uit
    except Exception:                       # noqa: BLE001 -- zie _loc_run
        log.exception("Ondemand companion-poll afgebroken")
        return None
    finally:
        _ondemand_lock.release()


def location_status() -> dict:
    """Wat de serverpagina over de laatste locatieronde te melden heeft."""
    return {"enabled": LOC_ENABLED, "interval_s": LOC_INTERVAL_S,
            "last_run": _loc_state["last_run"], "last_result": _loc_state["last_result"]}


def _loc_run() -> None:
    time.sleep(LOC_FIRST_RUN_DELAY_S)
    while True:
        try:
            uit = poll_locations()
            _loc_state["last_run"] = db.utcnow()
            if not uit["nodes"]:
                _loc_state["last_result"] = "geen afzender-node met companions"
            else:
                _loc_state["last_result"] = (
                    f"{uit['updated']} locatie(s) bijgewerkt van {uit['nodes']} node(s)")
                if uit["falls"]:
                    _loc_state["last_result"] += (
                        f"; {uit['falls']} val(len) gemeld, "
                        f"{uit['fall_alerts_sent']} alarm(en) verstuurd")
                    if uit["fall_alerts_failed"]:
                        _loc_state["last_result"] += (
                            f" ({uit['fall_alerts_failed']} mislukt)")
                if uit["errors"]:
                    _loc_state["last_result"] += f"; {len(uit['errors'])} node(s) gaven een fout"
        except Exception:                   # noqa: BLE001 -- zie sensornode._run
            log.exception("Companion-locatieronde afgebroken")
            _loc_state["last_result"] = "onverwachte fout"
        time.sleep(LOC_INTERVAL_S)


def start_location_poll() -> None:
    """De locatieronde starten, tenzij ze uitstaat.

    Uit zetten is een geldige keuze: geen companion met GPS aan, of een
    installatie die deze weg simpelweg niet gebruikt, hoort er geen periodiek
    HTTP-verzoek bij te krijgen dat nooit iets oplevert.
    """
    global _loc_thread
    if not LOC_ENABLED:
        log.info("Companion-locatiepoll staat uit (MM_COMPANION_LOC_POLL_ENABLED)")
        _loc_state["last_result"] = "uitgeschakeld"
        return
    if _loc_thread is not None:
        return
    _loc_thread = threading.Thread(target=_loc_run, name="companion-loc", daemon=True)
    _loc_thread.start()


# --- de instant-push: POST /api/companion --------------------------------------
#
# De achtergrondronde hierboven (``poll_locations``) is een POLL: een val kan
# tot ``LOC_INTERVAL_S`` (standaard 60s) blijven liggen voordat een DRAAIENDE
# achtergrondronde hem ziet, en zelfs de ONDEMAND-poll wacht op iemand die
# toevallig een companion-pagina open heeft staan. Dit endpoint is de andere
# kant op: de MeshUptime-node PUSHT zelf, zodra hij een locatie of een val
# ziet, in plaats van te wachten tot hij ernaar gevraagd wordt. De 60s-poll
# BLIJFT bestaan als FALLBACK/reconciliatie -- een node die deze weg niet
# (meer) haalt (geen WiFi op dat moment, een oude firmware zonder deze push)
# wordt hoe dan ook binnen het pollinterval alsnog gezien.
#
# Authenticatie: LETTERLIJK dezelfde deur als ``POST /api/sensorpush``
# (``sensorpush.require_push_token``/``check_rate``) en niet een tweede
# kopie ervan. Dat is inhoudelijk juist en niet alleen gemakzuchtig: de
# afzender is dezelfde vertrouwde node (de MeshUptime-bewakingsnode), dus
# verdient hij hetzelfde ene token (``MM_PUSH_TOKEN``) en dezelfde
# begrenzing, niet een eigen ``MM_COMPANION_PUSH_TOKEN`` die er in de praktijk
# toch aan gelijk gezet zou worden. Wijkt de node-push-kant hier ooit van af
# (een ander toestel, een ander token), dan is dit de ene regel die moet
# veranderen (``require_push_token`` aanroepen met een ander token) -- zie de
# opdracht-toelichting voor deze keuze.
#
# Het contract::
#
#     POST /api/companion
#     Authorization: Bearer {MM_PUSH_TOKEN}
#     {"companions": [{"pubkey": "<64 hex>", "lat": <float>, "lon": <float>,
#                       "seen": <uint>, "fall_ts": <uint>, "fall_kind": "val|nomotion|sos|",
#                       "batt": <0-100>}, ...]}
#
#     200: {"ok": true, "updated": <n>, "falls": <n>, "fall_alerts_sent": <n>,
#           "fall_alerts_failed": <n>, "skipped": <n>}
#     401 bij een fout of ontbrekend token; 503 zolang MM_PUSH_TOKEN leeg is;
#     429 bij te veel pushes; 400 bij een vormfout in de BODY zelf (geen
#     JSON, of geen niet-lege ``companions``-lijst).
#
# ``lat``/``lon`` mogen ontbreken (een companion zonder GPS-fix meldt dan
# alleen zijn val en/of ``batt``); ``batt`` (0-100) is eveneens optioneel en
# wordt alleen bijgewerkt als hij meekomt -- afwezig laat een bekende stand
# staan; ``fall_ts`` van 0 of afwezig is "geen val" (zie
# ``_handle_fall_report``). Net als ``poll_locations`` is een ENKELE foute
# rij in de lijst geen reden om de hele push te weigeren: een companion-
# pubkey die hier nog niet beheerd wordt (nog niet toegevoegd op de
# companions-pagina), een halve pubkey, of een niet-numerieke lat/lon wordt
# overgeslagen (geteld in ``skipped``) en de rest van de push gaat gewoon door
# -- dezelfde eerlijkheid als de rest van deze module.
router = APIRouter()

# Ruim boven wat één bewakingsnode ooit eerlijk in één push meestuurt (hij
# kent een handvol companions), ver onder wat iemand met een gestolen token de
# companions-tabel mee zou willen bestoken. Dezelfde soort grens als
# sensorpush.MAX_EVENTS, om dezelfde reden.
MAX_COMPANIONS_PUSH = 64


@router.post("/api/companion")
async def companion_push(request: Request,
                         authorization: str | None = Header(default=None)):
    """Eén push van de bewakingsnode: locaties/vallen erin, een korte telling
    eruit. Zie de moduletekst hierboven voor het volledige contract."""
    sensorpush.require_push_token(authorization)
    sensorpush.check_rate(request)

    try:
        body = await request.json()
    except ValueError:
        raise HTTPException(400, "Geen geldige JSON")
    if not isinstance(body, dict):
        raise HTTPException(400, "de body moet een JSON-object zijn")
    items = body.get("companions")
    if not isinstance(items, list) or not items:
        raise HTTPException(400, "companions moet een niet-lege lijst zijn")
    if len(items) > MAX_COMPANIONS_PUSH:
        raise HTTPException(400, f"companions: hoogstens {MAX_COMPANIONS_PUSH} per push")

    now = time.time()
    out = {"ok": True, "updated": 0, "falls": 0, "fall_alerts_sent": 0,
           "fall_alerts_failed": 0, "skipped": 0}
    for item in items:
        if not isinstance(item, dict):
            out["skipped"] += 1
            continue
        pubkey = str(item.get("pubkey") or "").strip().lower()
        if not valid_pubkey(pubkey):
            out["skipped"] += 1
            continue
        # Dezelfde overslag-zonder-fout als poll_locations: een companion die
        # de node kent maar wij hier nog niet beheren, valt niets bij te
        # werken en niets te escaleren.
        comp_row = db.companion_by_pubkey(pubkey)
        if comp_row is None:
            out["skipped"] += 1
            continue

        lat, lon = item.get("lat"), item.get("lon")
        lat_f = lon_f = None
        if lat is not None and lon is not None:
            try:
                lat_f, lon_f = float(lat), float(lon)
            except (TypeError, ValueError):
                lat_f = lon_f = None
            if lat_f is not None:
                seen_epoch = _secs_to_epoch(item.get("seen"), now)
                if db.set_companion_location(pubkey, lat_f, lon_f, seen_epoch):
                    out["updated"] += 1

        # De batterij, los van de locatie (zie poll_locations): een push kan een
        # batterij dragen zonder positie, en een push zonder batterij mag een
        # bekende stand niet wissen -- alleen bijwerken met een gekeurde waarde.
        batt = _valid_batt(item.get("batt"))
        if batt is not None:
            db.set_companion_batt(pubkey, batt)

        # De ESCALATIE, meteen en niet pas bij de volgende pollronde -- dat is
        # het hele punt van deze push. Gedeelde functie met poll_locations
        # (zie ``_handle_fall_report``): dezelfde ontdubbeling op
        # ``last_escalated_fall_ts``, dezelfde ontvangerslijst, dezelfde DM.
        val = _handle_fall_report(comp_row, item.get("fall_ts"), item.get("fall_kind"),
                                  lat_f, lon_f, now)
        if val["is_fall"]:
            out["falls"] += 1
            out["fall_alerts_sent"] += val["sent"]
            out["fall_alerts_failed"] += val["failed"]
    return out
