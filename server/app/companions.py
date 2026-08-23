"""MeshCore-companions: de beheerde handzenders (T1000-E e.d.) over het mesh.

Waar dit past
-------------
Een companion is geen node die publiceert of uitgevraagd wordt -- hij is een
BESTEMMING. Deze module kent twee dingen en niets daarbuiten:

1.  De **commandotaal** van de companion (de T1000-E-DM-commando's). Die staat
    hier geïsoleerd, op één plek, precies zoals de room-API-vorm in ``rooms.py``
    geïsoleerd staat: wijkt de firmware ooit af, dan is dit het enige bestand dat
    mee hoeft.
2.  De **weg naar buiten**. Een commando is een DM naar de pubkey van de
    companion, en die DM vertrekt bij een AFZENDER-node via ``rooms.bot_sendto``
    (``POST /bot/sendto`` op die node). Het lokale mesh routeert de DM daarna
    vanzelf verder via de repeater -- daar hoeft deze module niets voor te doen.

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

import re

from . import db, nodeconfig, rooms

# --- de commandotaal, op één plek ---------------------------------------------
#
# Elk commando: de sleutel (zoals de route/UI hem noemt), een label voor het
# scherm, en de argumentvorm. De bouwer hieronder is de enige die deze vorm tot
# een DM-tekst maakt; de UI toont ``label`` en levert de argumenten aan.
SEVERITIES = ("H", "M", "L", "find", "msg")
GPS_MODES = ("on", "off", "ondemand")
ONOFF = ("on", "off")

COMMANDS = {
    "find":     {"label": "Find-me (laten piepen/knipperen)", "args": []},
    "findstop": {"label": "Find-me stoppen", "args": []},
    "mute":     {"label": "Dempen", "args": ["state"]},          # on|off
    "vol":      {"label": "Volume", "args": ["level"]},          # 0..3
    "tune":     {"label": "Beltoon per ernst", "args": ["sev", "rtttl"]},
    "quiet":    {"label": "Stille uren", "args": ["range"]},     # "sH-eH" of "off"
    "gps":      {"label": "GPS", "args": ["mode"]},              # on|off|ondemand
    "loc":      {"label": "Locatie opvragen", "args": []},
    "cfg":      {"label": "Configuratie opvragen", "args": []},
    "allow":    {"label": "Toegestane-lijst", "args": ["sub", "value"]},
    "preset":   {"label": "Snelbericht-preset", "args": ["slot", "text"]},
    "fall":     {"label": "Valdetectie", "args": ["state"]},     # on|off
    "ping":     {"label": "Ping", "args": []},
}

_HEX64 = re.compile(r"^[0-9a-fA-F]{64}$")
_HEXPREFIX = re.compile(r"^[0-9a-fA-F]+$")
_QUIET = re.compile(r"^\d{1,2}-\d{1,2}$")


def valid_pubkey(pubkey: str) -> bool:
    """Een volledige companion-sleutel: precies 64 hex-tekens. Een DM richt zich op
    de sleutel, dus een halve sleutel is geen bestemming -- dezelfde regel als bij
    de bot-ontvangers en de ACL in ``rooms.py``."""
    return bool(_HEX64.match(str(pubkey or "").strip()))


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

    if cmd in ("find", "findstop", "loc", "cfg", "ping"):
        body = f"!{cmd}"
    elif cmd in ("mute", "fall"):
        state = val("state").lower()
        if state not in ONOFF:
            out["error"] = f"{cmd} verwacht 'on' of 'off'"
            return out
        body = f"!{cmd} {state}"
    elif cmd == "vol":
        lvl = val("level")
        if lvl not in ("0", "1", "2", "3"):
            out["error"] = "volume is 0 t/m 3"
            return out
        body = f"!vol {lvl}"
    elif cmd == "gps":
        mode = val("mode").lower()
        if mode not in GPS_MODES:
            out["error"] = "gps verwacht on, off of ondemand"
            return out
        body = f"!gps {mode}"
    elif cmd == "tune":
        sev = val("sev")
        rtttl = val("rtttl")
        if sev not in SEVERITIES:
            out["error"] = f"ernst moet een van {', '.join(SEVERITIES)} zijn"
            return out
        if not rtttl:
            out["error"] = "een RTTTL-melodie mag niet leeg zijn"
            return out
        body = f"!tune {sev} {rtttl}"
    elif cmd == "quiet":
        rng = val("range").lower()
        if rng != "off" and not _QUIET.match(rng):
            out["error"] = "stille uren als <startuur>-<einduur> (bv. 22-7) of 'off'"
            return out
        body = f"!quiet {rng}"
    elif cmd == "allow":
        sub = val("sub").lower()
        value = val("value")
        if sub == "list":
            body = "!allow list"
        elif sub == "add":
            if not _HEX64.match(value):
                out["error"] = "allow add verwacht een volledige pubkey van 64 hex-tekens"
                return out
            body = f"!allow add {value}"
        elif sub == "del":
            if len(value) < 6 or not _HEXPREFIX.match(value):
                out["error"] = "allow del verwacht een prefix van minstens 6 hex-tekens"
                return out
            body = f"!allow del {value}"
        else:
            out["error"] = "allow verwacht add, list of del"
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
    else:  # pragma: no cover -- COMMANDS en deze takken lopen synchroon
        out["error"] = f"commando {cmd} nog niet ondersteund"
        return out

    out["body"] = body
    out["ok"] = True
    return out


# --- de weg naar buiten (het enige dat weet HOE) ------------------------------

def send_dm(sender_rep, dest_pubkey: str, msg: str) -> dict:
    """Een DM van een afzender-node naar een companion-pubkey sturen.

    De ene plek die het vervoer kent. Nu: de bot van de afzender-node
    (``rooms.bot_sendto`` -> ``POST /bot/sendto``), waarna het mesh de DM via de
    lokale repeater verder routeert. Een toekomstige oorsprong (MQTT ->
    repeater) hoort HIER een tweede tak te krijgen en nergens anders.
    """
    if not valid_pubkey(dest_pubkey):
        return {"ok": False, "error": "een volledige pubkey van 64 hex-tekens is vereist"}
    if not str(msg or "").strip():
        return {"ok": False, "error": "een bericht mag niet leeg zijn"}
    return rooms.bot_sendto(sender_rep, dest_pubkey, msg)


def send_command(sender_rep, dest_pubkey: str, cmd: str,
                 args: dict | None = None) -> dict:
    """Een companion-commando bouwen én versturen. ``{"ok","error","body"}``.

    De bouwvalidatie eerst, zodat een fout commando nooit als lege of halve DM de
    band op gaat. De ``body`` komt mee terug zodat de pagina kan tonen wat er
    precies verstuurd is -- op een gedeelde band is "wat ging er de deur uit" geen
    detail.
    """
    gebouwd = build(cmd, args)
    if not gebouwd["ok"]:
        return {"ok": False, "error": gebouwd["error"], "body": ""}
    res = send_dm(sender_rep, dest_pubkey, gebouwd["body"])
    return {"ok": res["ok"], "error": res["error"], "body": gebouwd["body"]}


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
