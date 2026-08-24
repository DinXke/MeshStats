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

from . import config, db, nodeconfig, rooms, sensornode

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

COMMANDS = {
    "find":     {"label": "Find-me (laten piepen/knipperen)", "args": []},
    "findstop": {"label": "Find-me stoppen", "args": []},
    "mute":     {"label": "Dempen", "args": ["state"]},          # on|off
    "vol":      {"label": "Volume per ernst", "args": ["slot", "level"]},  # <slot> <0-3>
    "tune":     {"label": "Beltoon per ernst", "args": ["slot", "name"]},  # <slot> preset <name>
    "play":     {"label": "Beltoon afspelen (preview)", "args": ["name"]},  # <name>
    "quiet":    {"label": "Stille periode", "args": ["range", "action"]},   # <sH>-<eH> mute|<0-3>|off
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
        slot = val("slot")
        lvl = val("level")
        if slot not in SEVERITIES:
            out["error"] = f"slot moet een van {', '.join(SEVERITIES)} zijn"
            return out
        if lvl not in VOL_LEVELS:
            out["error"] = "volume is 0 t/m 3"
            return out
        body = f"!vol {slot} {lvl}"
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
#   {"companions": [{"name", "pubkey" (64 hex), "lat", "lon", "seen"}, ...]}
# Ontbreekt het endpoint (oudere firmware) of geeft de node een lege lijst
# terug, dan degradeert dit naar niets bijwerken -- geen fout die de rest van
# de pollronde raakt, zie ``poll_locations``.

def location_nodes() -> list:
    """De nodes waar minstens één companion zijn afzender op heeft staan.

    Dat zijn de enige nodes waarvoor ``/companions.json`` zinvol is: een node
    zonder companion hoeft dat endpoint niet te hebben, en bij elke sensornode
    aankloppen zou voor niets 404's opleveren op nodes die dit onderdeel niet
    kennen. ``sender_repeater_id`` is hier de aanwijzing WELKE node de
    companion aan het mesh hangt, niet alleen een voorkeur voor het versturen.
    """
    ids = sorted({int(c["sender_repeater_id"]) for c in db.list_companions()
                  if c["sender_repeater_id"]})
    if not ids:
        return []
    marks = ",".join("?" * len(ids))
    return db.q(f"SELECT * FROM repeaters WHERE id IN ({marks})", tuple(ids))


# Een waarde vanaf deze grens lezen we als een absoluut tijdstip (epoch-
# seconden na 1 januari 2001) en niet als een ouderdom in seconden. De reden om
# hier een gok te MOETEN doen staat in de kolomtoelichting bij
# ``companions.last_seen`` in db.py: dit nodetype heeft na een herstart een
# RTC die op 15 mei 2024 staat, dus "seen" kan zowel een epoch als een
# ouderdom betekenen en er is geen vlag die het zegt.
_EPOCH_2001 = 978307200


def _seen_to_epoch(seen, now: float) -> int | None:
    """``seen`` uit /companions.json naar een epoch met DEZE servers klok.

    Zie ``_EPOCH_2001`` hierboven voor de heuristiek. Alles wat geen bruikbaar
    getal is (ontbrekend, negatief, een string) levert None op -- geen gok
    beter dan een verkeerde gok die er hetzelfde uitziet als een echte tijd.
    """
    if not isinstance(seen, (int, float)) or isinstance(seen, bool) or seen < 0:
        return None
    seen = int(seen)
    return seen if seen >= _EPOCH_2001 else int(now) - seen


def poll_locations(timeout: int | None = None) -> dict:
    """Elke afzender-node om ``/companions.json`` vragen en de locaties van de
    beheerde companions bijwerken. ``{"nodes", "updated", "errors"}``.

    Matcht op PUBKEY en niet op de node waarachter het antwoord vandaan kwam:
    ``sender_repeater_id`` is een VOORKEUR voor het versturen (zie companions.py
    hierboven) en mag veranderen zonder dat een companion zijn geschiedenis
    verliest, dus een companion die zijn locatie via een andere node meldt dan
    de ingestelde voorkeur wordt hier gewoon bijgewerkt.

    Eén node die niet antwoordt (oude firmware zonder dit endpoint, of gewoon
    onbereikbaar) mag de ronde voor de andere nodes niet breken -- dezelfde
    lijn als ``sensornode.run_once``.
    """
    now = time.time()
    out = {"nodes": 0, "updated": 0, "errors": []}
    for rep in location_nodes():
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
            lat, lon = c.get("lat"), c.get("lon")
            if not valid_pubkey(pubkey) or lat is None or lon is None:
                continue
            try:
                lat_f, lon_f = float(lat), float(lon)
            except (TypeError, ValueError):
                continue
            seen_epoch = _seen_to_epoch(c.get("seen"), now)
            if db.set_companion_location(pubkey, lat_f, lon_f, seen_epoch):
                out["updated"] += 1
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

LOC_INTERVAL_S = max(30, int(config.env("COMPANION_LOC_POLL_S", "300") or 300))
LOC_ENABLED = config.env("COMPANION_LOC_POLL_ENABLED", "1").strip().lower() not in (
    "0", "false", "no", "nee", "off", "")
LOC_FIRST_RUN_DELAY_S = 25

_loc_thread = None
_loc_state = {"last_run": None, "last_result": "nog niet gedraaid"}


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
