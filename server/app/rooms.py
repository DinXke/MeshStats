"""De rooms van een MeshUptime-room-server-node: lezen, beheren, back-uppen.

Waar dit past
-------------
Dit is de serverkant van het room-beheer. De netwerkgrens ligt niet hier maar in
``sensornode`` -- ``_json`` om te lezen en ``post_form``/``post_json`` om te
schrijven, allebei langs ``firmware.open_node`` en dus langs de doelcontrole en
de vlootsleutel/per-node-login. Deze module kent de room-API van de node en zet
de antwoorden om in iets dat de route en het sjabloon kunnen tonen; hij opent
zelf geen socket en hardcodeert geen credential. Zie sensornode.py.

Het contract met de node (aannames, op één plek)
------------------------------------------------
De node (parallel gebouwd) biedt aan, achter dezelfde Basic-auth:

* ``GET  /rooms.json``   -> ``{"max","active","rooms":[{idx,name,stealth,guest,posts,pub,uri}]}``
* ``POST /room/add``     (form ``name``)                       -> ``{"ok",... ,"idx","uri"}``
* ``POST /room/edit``    (form ``idx`` [,``name``,``pass``,``guest``,``guest_clear``,``stealth``]) -> ``{"ok"}``
* ``POST /room/del``     (form ``idx``)                        -> ``{"ok"}``
* ``POST /mon/alarm``    (form ``ch``,``am``,``rm``)           -> ``{"ok",ch,am,rm}``
* ``GET  /rooms/backup`` -> volledige room-config incl. sleutels (GEVOELIG)
* ``POST /rooms/restore``(JSON-body)                           -> ``{"ok"}``

Twee bijzonderheden van het contract, elk om per ongeluk wissen te voorkomen:
een leeg of afwezig ``guest``-veld op ``/room/edit`` laat het gastwachtwoord
ONGEWIJZIGD; wissen gaat expliciet via ``guest_clear=1``. En ``/mon/alarm`` is
KANAAL-gebaseerd (``ch`` uit ``mon[].ch``, de node laat ``ch`` winnen), met
``am`` (1=dm, 2=room, 3=both) en ``rm`` (roombitmasker). Die vorm staat
geïsoleerd in de ``MON_ALARM_*``-constanten en ``set_alarm`` hieronder.

Gevoelige sleutels
------------------
De backup bevat de privésleutels van de rooms. Hij wordt serverzijde bewaard als
BEWAARPLAATS (versiehistoriek), maar geobfusceerd met ``nodecred`` -- dezelfde
laag als de per-node-wachtwoorden -- zodat een databankdump of een doorgemailde
back-up geen platte sleutels prijsgeeft. Dat is bewust geen echte versleuteling
(zie nodecred.py): het haalbare is "niet leesbaar bij een terloopse blik", en de
sleutels gaan nooit in een log en nooit naar buiten.
"""
from __future__ import annotations

import json
import logging

from . import db, nodecred, sensornode

log = logging.getLogger("meshmanager.rooms")

# De adapterlaag voor de per-sensor alarmroute. De node heeft een dedicated
# ``POST /mon/alarm`` met formvelden ``ch`` (kanaalnummer -- de node laat ch
# winnen), ``am`` (1/2/3), ``rm`` (roombitmasker) en optioneel ``sn``
# (sensor-nodes-bitmasker; afwezig = ongewijzigd). Deze constanten zijn de enige
# plek die mee hoeft als die vorm ooit afwijkt.
MON_ALARM_PATH = "/mon/alarm"
MON_ALARM_CH = "ch"
MON_ALARM_AM = "am"
MON_ALARM_RM = "rm"
MON_ALARM_SN = "sn"

# De adapterlaag voor de overige node-endpoints die de UI aanspreekt. Ze staan
# hier bij elkaar zodat een afwijkend contract op één plek aangepast wordt.
#   - een nieuwe monitor (kanaal) aanmaken op de node;
#   - een room of sensor-node zich laten melden op het mesh (flood of zerohop).
# ``MONITOR_ADD_PATH`` is een AANNAME zolang het node-contract niet vaststaat:
# een node die dit pad niet kent, antwoordt 404 en de fout komt netjes op het
# scherm -- de rest van het panel blijft werken.
MONITOR_ADD_PATH = "/mon/add"
ROOM_ADVERT_PATH = "/room/advert"
SNODE_ADVERT_PATH = "/snode/advert"
ROOM_ACL_PATH = "/room/acl"
SNODE_ACL_PATH = "/snode/acl"

# De ACL-niveaus van een room/sensor-node-slot. Een pubkey in de lijst mag
# WACHTWOORDLOOS binnen op zijn niveau: read = joinen/lezen (room: posts,
# sensor-node: telemetrie), readwrite = +schrijven, admin = +beheer van dat slot.
ACL_LEVELS = {1: "read", 2: "readwrite", 3: "admin"}
ACL_LEVEL_NUM = {woord: getal for getal, woord in ACL_LEVELS.items()}
ACL_LEVEL_GLOSS = {
    "read": "lezen/joinen",
    "readwrite": "lezen + schrijven",
    "admin": "volledig beheer van dit slot",
}

# De geldige alarmmodi (1=dm, 2=room, 3=both). Voor de validatie en voor de
# keuzelijst in de UI; ``am=0`` ("uit", zie AM_LABELS) is een toestand die
# /status.json kan melden maar die deze setter niet aanbiedt.
AM_MODES = {1: "dm", 2: "room", 3: "both"}

# Wat mon[].am betekent, voor het scherm. am=0 ("uit") is een toestand die
# /status.json kan melden maar die deze CLI niet als modus zet -- zie AM_MODES.
AM_LABELS = {0: "uit", 1: "alleen dm", 2: "alleen room", 3: "dm + room"}


def _host(rep) -> str:
    return sensornode._host(rep)


# --- lezen --------------------------------------------------------------------

def _acl_uit(ruw: dict) -> list[dict]:
    """De per-sleutel-ACL van een room/sensor-node normaliseren.

    ``[{pub, level, level_name}]``. Nooit een geheim -- alleen de publieke sleutel
    en het niveau. Een onbekend niveau krijgt "?" zodat een node die ooit een
    vierde niveau meldt de pagina niet laat struikelen.
    """
    return [
        {"pub": str(a.get("pub") or ""),
         "level": int(a.get("level") or 0),
         "level_name": ACL_LEVELS.get(int(a.get("level") or 0), "?")}
        for a in (ruw.get("acl") or []) if isinstance(a, dict)
    ]


def _room_uit(ruw: dict) -> dict:
    """Eén roomrij uit ``/rooms.json`` normaliseren tot vaste, veilige velden.

    Defensief, want dit komt van een node die parallel gebouwd wordt: een veld
    dat ontbreekt of een ander type heeft mag de pagina niet laten struikelen.
    """
    return {
        "idx": int(ruw.get("idx") or 0),
        "name": str(ruw.get("name") or ""),
        "stealth": bool(ruw.get("stealth")),
        "guest": bool(ruw.get("guest")),
        "posts": int(ruw.get("posts") or 0),
        "pub": str(ruw.get("pub") or ""),
        "uri": str(ruw.get("uri") or ""),
        "acl": _acl_uit(ruw),
    }


def _snode_uit(ruw: dict) -> dict:
    """Eén virtuele sensor-node uit ``/rooms.json`` normaliseren.

    Dezelfde defensieve lijn als ``_room_uit``. ``channels`` is een lijst
    KANAALNUMMERS die aan deze virtuele node hangen; de namen erbij zoekt de
    server zelf op (channel_names) -- de node stuurt bewust alleen de nummers.
    """
    kanalen = ruw.get("channels")
    return {
        "idx": int(ruw.get("idx") or 0),
        "name": str(ruw.get("name") or ""),
        "stealth": bool(ruw.get("stealth")),
        "pub": str(ruw.get("pub") or ""),
        "uri": str(ruw.get("uri") or ""),
        "channels": [int(c) for c in kanalen if isinstance(c, (int, float))]
                    if isinstance(kanalen, list) else [],
        "acl": _acl_uit(ruw),
    }


def _fetch(rep, timeout: int | None = None) -> dict:
    """De ruwe ``/rooms.json`` van de node -- rooms én sensor-nodes in één call."""
    return sensornode._json(_host(rep), "/rooms.json", timeout)


def _rooms_uit(data: dict) -> dict:
    return {
        "max": int(data.get("max") or 0),
        "active": int(data.get("active") or 0),
        "rooms": [_room_uit(r) for r in (data.get("rooms") or [])
                  if isinstance(r, dict)],
    }


def _snodes_uit(data: dict) -> dict:
    return {
        "max": int(data.get("snode_max") or 0),
        "active": int(data.get("snode_active") or 0),
        "snodes": [_snode_uit(s) for s in (data.get("snodes") or [])
                   if isinstance(s, dict)],
    }


def list_rooms(rep, timeout: int | None = None) -> dict:
    """``GET /rooms.json`` -> de rooms, of een reden waarom niet.

    ``{"ok", "error", "max", "active", "rooms": [...]}``. De fouttekst komt uit
    ``sensornode._json`` en is Nederlands en voor het scherm bedoeld.
    """
    out = {"ok": False, "error": "", "max": 0, "active": 0, "rooms": []}
    got = _fetch(rep, timeout)
    if not got["ok"]:
        out["error"] = got["error"]
        return out
    data = got["data"] if isinstance(got["data"], dict) else {}
    out.update(_rooms_uit(data), ok=True)
    return out


def list_snodes(rep, timeout: int | None = None) -> dict:
    """``GET /rooms.json`` -> de virtuele sensor-nodes, of een reden waarom niet.

    De spiegel van ``list_rooms``, uit dezelfde call: ``/rooms.json`` draagt naast
    de rooms ook ``snode_max``/``snode_active``/``snodes``.
    ``{"ok", "error", "max", "active", "snodes": [...]}``.
    """
    out = {"ok": False, "error": "", "max": 0, "active": 0, "snodes": []}
    got = _fetch(rep, timeout)
    if not got["ok"]:
        out["error"] = got["error"]
        return out
    data = got["data"] if isinstance(got["data"], dict) else {}
    out.update(_snodes_uit(data), ok=True)
    return out


def overview(rep, timeout: int | None = None) -> dict:
    """Rooms én sensor-nodes uit ÉÉN ``/rooms.json``-call.

    De weg die de pagina en de pollronde gebruiken: twee soorten entiteit uit één
    verzoek, in plaats van ``list_rooms`` en ``list_snodes`` los aan te roepen en
    de node twee keer te bevragen. ``{"ok","error","rooms":{...},"snodes":{...}}``.
    """
    out = {"ok": False, "error": "",
           "rooms": {"max": 0, "active": 0, "rooms": []},
           "snodes": {"max": 0, "active": 0, "snodes": []}}
    got = _fetch(rep, timeout)
    if not got["ok"]:
        out["error"] = got["error"]
        return out
    data = got["data"] if isinstance(got["data"], dict) else {}
    out["rooms"] = _rooms_uit(data)
    out["snodes"] = _snodes_uit(data)
    out["ok"] = True
    return out


# --- beheren ------------------------------------------------------------------

def add_room(rep, name: str) -> dict:
    """``POST /room/add`` met een naam. Geeft de nieuwe ``idx`` en ``uri`` terug."""
    out = {"ok": False, "error": "", "idx": None, "uri": ""}
    naam = str(name or "").strip()
    if not naam:
        out["error"] = "een room heeft een naam nodig"
        return out
    if len(naam) > 40:
        out["error"] = "de naam is te lang (hooguit 40 tekens)"
        return out
    ant = sensornode.post_form(_host(rep), "/room/add", {"name": naam})
    if not ant["ok"]:
        out["error"] = ant["error"]
        return out
    data = ant["data"] if isinstance(ant["data"], dict) else {}
    out["idx"] = data.get("idx")
    out["uri"] = str(data.get("uri") or "")
    out["ok"] = True
    return out


def edit_room(rep, idx: int, name: str | None = None, password: str | None = None,
              guest: str | None = None, guest_clear: bool = False,
              stealth: bool | None = None) -> dict:
    """``POST /room/edit``. Alleen de meegegeven velden veranderen.

    Een veld dat ``None`` blijft, gaat niet mee de deur uit (zie
    ``sensornode.post_form``): zo overschrijft "alleen de stealth-vlag omzetten"
    geen naam of wachtwoord. Zowel ``pass`` (het roomwachtwoord) als ``guest``
    (het gastwachtwoord) worden door de node zelf tot een sleutel gebakken; deze
    server bewaart ze niet.

    Het gastwachtwoord heeft een aparte WIS-weg, en dat is met opzet: de node laat
    een leeg of afwezig ``guest``-veld ONGEWIJZIGD, en wissen gaat expliciet met
    ``guest_clear=1``. Zonder dat onderscheid zou een deelbewerking (bv. alleen de
    naam) met een leeg gastveld het gastwachtwoord per ongeluk wissen. Vandaar:
    ``guest_clear`` heeft voorrang op ``guest``, en een leeg ``guest`` laat het
    gastwachtwoord staan.
    """
    out = {"ok": False, "error": ""}
    velden: dict = {"idx": int(idx)}
    if name is not None:
        naam = str(name).strip()
        if not naam or len(naam) > 40:
            out["error"] = "de naam is leeg of te lang (hooguit 40 tekens)"
            return out
        velden["name"] = naam
    if password is not None:
        velden["pass"] = str(password)
    if guest_clear:
        # Expliciet wissen -- en dan gaat er geen gastwachtwoord mee, anders zou de
        # node niet weten of het om wissen of om zetten gaat.
        velden["guest_clear"] = "1"
    elif guest is not None:
        velden["guest"] = str(guest)
    if stealth is not None:
        velden["stealth"] = "1" if stealth else "0"
    ant = sensornode.post_form(_host(rep), "/room/edit", velden)
    out["ok"] = ant["ok"]
    out["error"] = ant["error"]
    return out


def del_room(rep, idx: int) -> dict:
    """``POST /room/del`` op één room-index."""
    ant = sensornode.post_form(_host(rep), "/room/del", {"idx": int(idx)})
    return {"ok": ant["ok"], "error": ant["error"]}


# --- virtuele sensor-nodes beheren --------------------------------------------
#
# De spiegel van de room-functies hierboven: dezelfde vorm, dezelfde
# foutafhandeling, alleen ``/snode/*`` in plaats van ``/room/*``. Een sensor-node
# kent geen gast- of roomwachtwoord, dus ``edit_snode`` heeft alleen ``name`` en
# ``stealth``.

def add_snode(rep, name: str) -> dict:
    """``POST /snode/add`` met een naam. Geeft de nieuwe ``idx`` en ``uri`` terug."""
    out = {"ok": False, "error": "", "idx": None, "uri": ""}
    naam = str(name or "").strip()
    if not naam:
        out["error"] = "een sensor-node heeft een naam nodig"
        return out
    if len(naam) > 40:
        out["error"] = "de naam is te lang (hooguit 40 tekens)"
        return out
    ant = sensornode.post_form(_host(rep), "/snode/add", {"name": naam})
    if not ant["ok"]:
        out["error"] = ant["error"]
        return out
    data = ant["data"] if isinstance(ant["data"], dict) else {}
    out["idx"] = data.get("idx")
    out["uri"] = str(data.get("uri") or "")
    out["ok"] = True
    return out


def edit_snode(rep, idx: int, name: str | None = None,
               stealth: bool | None = None) -> dict:
    """``POST /snode/edit``. Alleen de meegegeven velden veranderen.

    Een veld dat ``None`` blijft, gaat niet mee de deur uit -- zelfde regel als
    ``edit_room``. De stealth-vlag gaat als ``0``/``1`` mee wanneer opgegeven.
    """
    out = {"ok": False, "error": ""}
    velden: dict = {"idx": int(idx)}
    if name is not None:
        naam = str(name).strip()
        if not naam or len(naam) > 40:
            out["error"] = "de naam is leeg of te lang (hooguit 40 tekens)"
            return out
        velden["name"] = naam
    if stealth is not None:
        velden["stealth"] = "1" if stealth else "0"
    ant = sensornode.post_form(_host(rep), "/snode/edit", velden)
    out["ok"] = ant["ok"]
    out["error"] = ant["error"]
    return out


def del_snode(rep, idx: int) -> dict:
    """``POST /snode/del`` op één sensor-node-index."""
    ant = sensornode.post_form(_host(rep), "/snode/del", {"idx": int(idx)})
    return {"ok": ant["ok"], "error": ant["error"]}


def set_alarm(rep, ch: int, am: int | None = None,
              rm: int | None = None, sn: int | None = None) -> dict:
    """De alarmroute van sensor ``ch`` zetten: ``am`` (1/2/3), ``rm`` en/of ``sn``.

    De adapterlaag. De node heeft een dedicated ``POST /mon/alarm`` met formvelden
    ``ch``, ``am``, ``rm`` en optioneel ``sn``; die staan geïsoleerd in de
    ``MON_ALARM_*``-constanten bovenaan. ``ch`` is het KANAALnummer uit
    ``/status.json`` (``mon[].ch``), niet de positie in de ``mon[]``-lijst -- de
    node laat ``ch`` winnen. ``rm`` (rooms) en ``sn`` (sensor-nodes) gaan als
    bitmasker de deur uit; de omzetting van aangevinkte vakjes naar die maskers
    gebeurt in de route. Een masker dat ``None`` blijft, gaat niet mee -- dan blijft
    het op de node ongewijzigd.

    De node antwoordt ``{"ok",ch,am,rm}`` bij succes, of 4xx/500 bij een fout; die
    fout komt als leesbare tekst terug uit ``sensornode.post_form``.
    """
    out = {"ok": False, "error": "", "data": {}}
    if am is None and rm is None and sn is None:
        out["error"] = "niets te zetten: geef am, rm of sn"
        return out
    if am is not None and int(am) not in AM_MODES:
        out["error"] = (f"alarmroute {am} kan niet gezet worden "
                        f"(kies dm/room/both = 1/2/3)")
        return out
    if rm is not None and int(rm) < 0:
        out["error"] = "een roombitmasker kan niet negatief zijn"
        return out
    if sn is not None and int(sn) < 0:
        out["error"] = "een sensor-nodes-bitmasker kan niet negatief zijn"
        return out

    velden: dict = {MON_ALARM_CH: int(ch)}
    if am is not None:
        velden[MON_ALARM_AM] = int(am)
    if rm is not None:
        velden[MON_ALARM_RM] = int(rm)
    if sn is not None:
        velden[MON_ALARM_SN] = int(sn)
    ant = sensornode.post_form(_host(rep), MON_ALARM_PATH, velden)
    out["ok"] = ant["ok"]
    out["error"] = ant["error"]
    out["data"] = ant["data"]
    return out


# --- node-centrisch kanaalbeheer: kanalen aan/af een room of sensor-node ------
#
# Dezelfde onderliggende maskers als ``set_alarm`` (``rm`` voor rooms, ``sn`` voor
# sensor-nodes), maar node-centrisch gepresenteerd: "welke kanalen horen bij deze
# room/sensor-node". Aan-/afvinken zet ALLEEN de bit van DEZE room/sensor-node op
# elke monitor; ``am`` en het andere masker blijven ongemoeid (``set_alarm``
# stuurt alleen wat het meekrijgt). Er verandert alleen wat verandert -- een
# monitor die al goed staat wordt niet aangeraakt.

def _apply_channel_bit(rep, veld: str, ent_idx: int, checked,
                       timeout: int | None = None) -> dict:
    """De bit ``ent_idx`` in ``veld`` (``rm``/``sn``) op elke monitor gelijkzetten
    aan de vinkjes. Leest de huidige stand uit ``/status.json`` zodat de andere
    bits behouden blijven."""
    out = {"ok": False, "error": "", "changed": 0}
    st = sensornode.status(_host(rep), timeout)
    if not st["ok"]:
        out["error"] = st["error"]
        return out
    mon = st["data"].get("mon") or []
    bit = 1 << int(ent_idx)
    gewenst = {int(c) for c in checked}
    fouten = []
    for m in mon:
        if not isinstance(m, dict):
            continue
        ch = int(m.get("ch") or 0)
        huidig = int(m.get(veld) or 0)
        moet = ch in gewenst
        heeft = bool(huidig & bit)
        if moet == heeft:
            continue
        nieuw = (huidig | bit) if moet else (huidig & ~bit)
        res = (set_alarm(rep, ch, rm=nieuw) if veld == "rm"
               else set_alarm(rep, ch, sn=nieuw))
        if res["ok"]:
            out["changed"] += 1
        else:
            fouten.append(f"kanaal {ch}: {res['error']}")
    if fouten:
        out["error"] = "; ".join(fouten)
        out["ok"] = out["changed"] > 0   # deels gelukt telt als gelukt-met-melding
    else:
        out["ok"] = True
    return out


def apply_room_channels(rep, room_idx: int, checked,
                        timeout: int | None = None) -> dict:
    """De kanalen die bij room ``room_idx`` horen gelijkzetten aan de vinkjes."""
    return _apply_channel_bit(rep, "rm", room_idx, checked, timeout)


def apply_snode_channels(rep, snode_idx: int, checked,
                         timeout: int | None = None) -> dict:
    """De kanalen die bij sensor-node ``snode_idx`` horen gelijkzetten."""
    return _apply_channel_bit(rep, "sn", snode_idx, checked, timeout)


def add_monitor(rep, name: str, host: str = "", kind: str = "",
                interval: int | None = None) -> dict:
    """Een nieuwe monitor (kanaal) aanmaken op de node. Adapterlaag.

    Het node-contract hiervoor staat nog niet vast; deze functie pos't naar
    ``MONITOR_ADD_PATH`` en gaat ervan uit dat de node het nieuwe kanaalnummer
    teruggeeft als ``{"ok",ch}`` zodat de aanroeper het meteen aan een room of
    sensor-node kan koppelen. Kent de node dit pad niet, dan komt er een 404 en
    een leesbare fout -- de rest van het panel blijft werken.
    """
    out = {"ok": False, "error": "", "ch": None}
    naam = str(name or "").strip()
    if not naam:
        out["error"] = "een kanaal heeft een naam nodig"
        return out
    velden: dict = {"name": naam}
    if host:
        velden["host"] = str(host).strip()
    if kind:
        velden["kind"] = str(kind).strip()
    if interval is not None:
        velden["interval"] = int(interval)
    ant = sensornode.post_form(_host(rep), MONITOR_ADD_PATH, velden)
    if not ant["ok"]:
        out["error"] = ant["error"]
        return out
    data = ant["data"] if isinstance(ant["data"], dict) else {}
    out["ch"] = data.get("ch")
    out["ok"] = True
    return out


def room_advert(rep, idx: int, flood: bool = True) -> dict:
    """Een room zich laten melden op het mesh: flood (mesh-breed) of zerohop.

    Adapterlaag over ``ROOM_ADVERT_PATH`` (form ``idx``, ``flood``=0/1). Symmetrisch
    met ``snode_advert``. Kent de node het pad nog niet, dan komt er een leesbare
    fout op het scherm.
    """
    ant = sensornode.post_form(_host(rep), ROOM_ADVERT_PATH,
                               {"idx": int(idx), "flood": "1" if flood else "0"})
    return {"ok": ant["ok"], "error": ant["error"]}


def snode_advert(rep, idx: int, flood: bool = True) -> dict:
    """Een sensor-node zich laten melden op het mesh: flood of zerohop.

    Adapterlaag over ``SNODE_ADVERT_PATH`` (form ``idx``, ``flood``=0/1).
    """
    ant = sensornode.post_form(_host(rep), SNODE_ADVERT_PATH,
                               {"idx": int(idx), "flood": "1" if flood else "0"})
    return {"ok": ant["ok"], "error": ant["error"]}


# --- de per-sleutel-ACL van een room / sensor-node ----------------------------
#
# Een room- en een sensor-node-slot dragen elk een toegangslijst: welke pubkey
# mag binnen, op welk niveau (read/readwrite/admin). WACHTWOORDLOOS -- een sleutel
# in de lijst krijgt toegang zonder wachtwoord. De bestaande grants komen uit de
# ``acl``-array van /rooms.json (zie ``_acl_uit``); zetten en verwijderen gaat via
# ``POST /room/acl`` resp. ``POST /snode/acl``, en die staan achter de
# ``*_ACL_PATH``-constanten zodat een afwijkend contract op één plek meegaat.

def _is_hex(tekst: str) -> bool:
    t = str(tekst or "")
    return bool(t) and all(c in "0123456789abcdefABCDEF" for c in t)


def _set_acl(rep, path: str, idx: int, pubkey: str, level: str) -> dict:
    out = {"ok": False, "error": "", "level": ""}
    sleutel = str(pubkey or "").strip()
    # Toevoegen/wijzigen vraagt de VOLLEDIGE sleutel: op een prefix zou je niet
    # weten wie je binnenlaat, en de node bakt er de identiteit van.
    if len(sleutel) != 64 or not _is_hex(sleutel):
        out["error"] = "een volledige pubkey van 64 hex-tekens is vereist"
        return out
    woord = str(level or "").strip().lower()
    if woord not in ACL_LEVEL_NUM:
        out["error"] = "kies een niveau: read, readwrite of admin"
        return out
    ant = sensornode.post_form(_host(rep), path,
                               {"idx": int(idx), "pubkey": sleutel, "level": woord})
    out["ok"] = ant["ok"]
    out["error"] = ant["error"]
    if ant["ok"]:
        data = ant["data"] if isinstance(ant["data"], dict) else {}
        out["level"] = str(data.get("level") or woord)
    return out


def _del_acl(rep, path: str, idx: int, prefix: str) -> dict:
    out = {"ok": False, "error": ""}
    korte = str(prefix or "").strip()
    # Verwijderen mag op een PREFIX, maar niet op een handvol tekens: onder de
    # twaalf hex raak je te makkelijk de verkeerde sleutel.
    if len(korte) < 12 or not _is_hex(korte):
        out["error"] = "een prefix van minstens 12 hex-tekens is vereist om te verwijderen"
        return out
    ant = sensornode.post_form(_host(rep), path,
                               {"idx": int(idx), "pubkey": korte, "del": "1"})
    out["ok"] = ant["ok"]
    out["error"] = ant["error"]
    return out


def set_room_acl(rep, idx: int, pubkey: str, level: str) -> dict:
    """Een pubkey op een niveau (read/readwrite/admin) in de room-ACL zetten."""
    return _set_acl(rep, ROOM_ACL_PATH, idx, pubkey, level)


def del_room_acl(rep, idx: int, prefix: str) -> dict:
    """Een pubkey (op prefix) uit de room-ACL verwijderen."""
    return _del_acl(rep, ROOM_ACL_PATH, idx, prefix)


def set_snode_acl(rep, idx: int, pubkey: str, level: str) -> dict:
    """Een pubkey op een niveau in de sensor-node-ACL zetten."""
    return _set_acl(rep, SNODE_ACL_PATH, idx, pubkey, level)


def del_snode_acl(rep, idx: int, prefix: str) -> dict:
    """Een pubkey (op prefix) uit de sensor-node-ACL verwijderen."""
    return _del_acl(rep, SNODE_ACL_PATH, idx, prefix)


# --- de koppeling room <-> sensoren -------------------------------------------

def sensor_in_room(mon: dict, room_idx: int) -> bool:
    """Of deze monitor zijn alarm (ook) naar room ``room_idx`` stuurt.

    Twee voorwaarden samen: het roembitmasker ``rm`` heeft het bit voor deze room
    aan, én de route ``am`` bevat de room-kant (2=room, 3=both). Een monitor die
    op ``am=1`` (alleen dm) staat hoort bij geen enkele room, ook al staat er nog
    een oud bit in ``rm``.
    """
    am = int(mon.get("am") or 0)
    rm = int(mon.get("rm") or 0)
    if am not in (2, 3):
        return False
    return bool(rm & (1 << int(room_idx)))


def couple(rooms: list[dict], mon: list[dict]) -> list[dict]:
    """Elke room met de sensoren die eraan hangen (uit het ``rm``-masker).

    Zo toont de pagina per room welke monitoren erin binnenkomen en met welke
    toestand -- het antwoord op "room X bevat sensoren A, B, C". De sensoren komen
    uit ``/status.json`` (``mon[]``), die de server toch al ophaalt; er gaat geen
    extra verzoek de deur uit.
    """
    uit = []
    for room in rooms:
        sensoren = [m for m in (mon or []) if sensor_in_room(m, room["idx"])]
        uit.append(dict(room, sensoren=sensoren))
    return uit


# --- welke rooms op welke fysieke node draaien --------------------------------

def record_owners(rep, room_lijst: list[dict] | None = None,
                  snode_lijst: list[dict] | None = None) -> int:
    """De koppeling pubkey -> deze node persisteren, uit ``/rooms.json``.

    Een room-server-node host meerdere virtuele entiteiten met elk een eigen
    sleutel -- rooms én sensor-nodes; op het mesh zijn dat losse entries. Dit legt
    vast dat ze bij één toestel horen (met hun ``kind``), zodat de nodelijst ze kan
    groeperen in plaats van als anonieme unmanaged nodes te tonen. De node zelf is
    de schone bron -- ``/rooms.json`` noemt al zijn pubkeys -- dus dit wordt
    bijgewerkt telkens die lijst opgehaald is, en entries die eruit verdwenen zijn
    worden opgeruimd (``prune_room_owners``, over de vereniging van beide soorten).

    Geeft het aantal vastgelegde entiteiten terug. Faalt de databank, dan is dat
    geen reden om de pagina of de pollronde te laten stranden: de mapping is een
    extra, niet de kern.
    """
    rid = int(sensornode.firmware._field(rep, "id") or 0)
    if not rid:
        return 0
    behouden = []
    try:
        for kind, lijst in (("room", room_lijst or []),
                            ("sensor", snode_lijst or [])):
            for ent in lijst:
                pub = str(ent.get("pub") or "").strip()
                if not pub:
                    continue
                db.set_room_owner(db.node_key(pub), rid, pub,
                                  ent.get("idx"), ent.get("name") or "", kind=kind)
                behouden.append(pub)
        db.prune_room_owners(rid, behouden)
    except Exception:  # noqa: BLE001 -- de mapping mag de rest nooit breken
        log.warning("Eigenaarschap niet bijgewerkt voor node %s", rid,
                    exc_info=True)
    return len(behouden)


def owners_for(rep) -> list:
    """De rooms en sensor-nodes die volgens de mapping op deze node draaien."""
    rid = int(sensornode.firmware._field(rep, "id") or 0)
    return db.room_owners_for(rid) if rid else []


# --- backup: ophalen, serverzijde bewaren, terugzetten ------------------------

def fetch_backup(rep, timeout: int | None = None) -> dict:
    """``GET /rooms/backup`` -> de volledige room-config (GEVOELIG).

    De inhoud bevat sleutels. Ze wordt hier niet gelogd en gaat nooit naar buiten;
    de aanroeper beslist of ze naar de browser gaat (achter serverbeheerder) of
    geobfusceerd de bewaarplaats in.
    """
    return sensornode._json(_host(rep), "/rooms/backup", timeout)


def store_backup(rep, data, actor: str = "") -> dict:
    """Een opgehaalde backup geobfusceerd bijschrijven in de bewaarplaats.

    De blob gaat door ``nodecred.obfuscate`` vóór hij de databank in gaat -- geen
    platte sleutels op schijf. Het aantal rooms wordt apart onthouden zodat de
    lijst iets kan tonen zonder de blob te openen.
    """
    out = {"ok": False, "error": "", "id": None}
    rid = int(sensornode.firmware._field(rep, "id") or 0)
    try:
        tekst = json.dumps(data)
    except (TypeError, ValueError) as exc:
        out["error"] = f"backup niet op te slaan: {type(exc).__name__}"
        return out
    aantal = None
    if isinstance(data, dict) and isinstance(data.get("rooms"), list):
        aantal = len(data["rooms"])
    blob = nodecred.obfuscate(tekst)
    out["id"] = db.save_room_backup(rid, blob, actor=actor, rooms=aantal)
    out["ok"] = True
    return out


def backup_and_store(rep, actor: str = "") -> dict:
    """De backup ophalen bij de node én bewaren, in één handeling.

    De gewone weg voor de knop "backup maken": de server als bewaarplaats. Faalt
    het ophalen, dan wordt er niets bewaard -- een lege of halve rij zou later een
    restore-bron zijn die niet klopt.
    """
    out = {"ok": False, "error": "", "id": None, "rooms": None}
    got = fetch_backup(rep)
    if not got["ok"]:
        out["error"] = got["error"]
        return out
    opgeslagen = store_backup(rep, got["data"], actor=actor)
    if not opgeslagen["ok"]:
        out["error"] = opgeslagen["error"]
        return out
    out["ok"] = True
    out["id"] = opgeslagen["id"]
    if isinstance(got["data"], dict) and isinstance(got["data"].get("rooms"), list):
        out["rooms"] = len(got["data"]["rooms"])
    return out


def list_stored(rep, limit: int = 50) -> list:
    """De bewaarde backups van deze node, nieuwste eerst, zonder de blob."""
    rid = int(sensornode.firmware._field(rep, "id") or 0)
    return db.room_backups_for(rid, limit)


def load_stored(rep, backup_id: int) -> dict:
    """Eén bewaarde backup terug naar leesbare data, of een reden waarom niet."""
    out = {"ok": False, "error": "", "data": None, "created": "", "actor": ""}
    rid = int(sensornode.firmware._field(rep, "id") or 0)
    rij = db.room_backup(int(backup_id), rid)
    if not rij:
        out["error"] = "onbekende backup voor deze node"
        return out
    plat = nodecred.deobfuscate(rij["blob"])
    if plat is None:
        out["error"] = ("de backup is niet te openen -- een andere secret.key dan "
                        "waarmee hij bewaard is, of een beschadigde rij")
        return out
    try:
        out["data"] = json.loads(plat)
    except (TypeError, ValueError):
        out["error"] = "de backup bevat geen geldige JSON meer"
        return out
    out["created"] = rij["created"]
    out["actor"] = rij["actor"] or ""
    out["ok"] = True
    return out


def restore(rep, data, timeout: int | None = None) -> dict:
    """``POST /rooms/restore`` met een volledige room-config als JSON-body."""
    out = {"ok": False, "error": ""}
    if not isinstance(data, (dict, list)):
        out["error"] = "een restore verwacht een JSON-object"
        return out
    ant = sensornode.post_json(_host(rep), "/rooms/restore", data, timeout)
    out["ok"] = ant["ok"]
    out["error"] = ant["error"]
    return out


def restore_stored(rep, backup_id: int) -> dict:
    """Een bewaarde backup terugzetten naar de node.

    De bewaarplaats als bron: eerst de blob openen (server), dan naar de node
    (``restore``). Zo hoeft niemand de gevoelige JSON door de browser te halen om
    een vorige toestand terug te zetten.
    """
    geladen = load_stored(rep, backup_id)
    if not geladen["ok"]:
        return {"ok": False, "error": geladen["error"]}
    return restore(rep, geladen["data"])
