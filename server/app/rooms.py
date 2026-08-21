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
* ``POST /room/edit``    (form ``idx`` [,``name``,``pass``,``guest``,``stealth``]) -> ``{"ok"}``
* ``POST /room/del``     (form ``idx``)                        -> ``{"ok"}``
* ``GET  /rooms/backup`` -> volledige room-config incl. sleutels (GEVOELIG)
* ``POST /rooms/restore``(JSON-body)                           -> ``{"ok"}``

De per-sensor alarmroute staat in ``/status.json`` als ``mon[].am`` (1=dm, 2=room,
3=both) en ``mon[].rm`` (roombitmasker). Het ZETTEN daarvan heeft nog geen vaste
setter-URL in het contract; die aanname staat daarom geïsoleerd in
``MON_ALARM_PATH`` en ``set_alarm`` hieronder -- wijkt het contract af, dan is dat
de enige plek die aangepast hoeft te worden.

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

# De setter voor de per-sensor alarmroute. Eén constante, want dit is het enige
# stuk contract dat nog niet vaststaat -- zie de moduledocstring. De veldnamen
# staan er los naast, zodat een node die andere namen verwacht met één regel
# meegaat.
MON_ALARM_PATH = "/mon/alarm"
MON_ALARM_IDX = "idx"
MON_ALARM_AM = "am"
MON_ALARM_RM = "rm"

# Wat mon[].am betekent, voor het scherm en voor de validatie in één.
AM_LABELS = {0: "uit", 1: "alleen dm", 2: "alleen room", 3: "dm + room"}


def _host(rep) -> str:
    return sensornode._host(rep)


# --- lezen --------------------------------------------------------------------

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
    }


def list_rooms(rep, timeout: int | None = None) -> dict:
    """``GET /rooms.json`` -> de rooms, of een reden waarom niet.

    ``{"ok", "error", "max", "active", "rooms": [...]}``. De fouttekst komt uit
    ``sensornode._json`` en is Nederlands en voor het scherm bedoeld.
    """
    out = {"ok": False, "error": "", "max": 0, "active": 0, "rooms": []}
    got = sensornode._json(_host(rep), "/rooms.json", timeout)
    if not got["ok"]:
        out["error"] = got["error"]
        return out
    data = got["data"] if isinstance(got["data"], dict) else {}
    out["max"] = int(data.get("max") or 0)
    out["active"] = int(data.get("active") or 0)
    out["rooms"] = [_room_uit(r) for r in (data.get("rooms") or [])
                    if isinstance(r, dict)]
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
              guest: bool | None = None, stealth: bool | None = None) -> dict:
    """``POST /room/edit``. Alleen de meegegeven velden veranderen.

    Een veld dat ``None`` blijft, gaat niet mee de deur uit (zie
    ``sensornode.post_form``): zo overschrijft "alleen de gast-vlag omzetten" geen
    naam of wachtwoord. Het wachtwoord gaat als ``pass`` mee -- de node bakt er
    zelf de sleutel van; deze server bewaart het niet.
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
    if guest is not None:
        velden["guest"] = "1" if guest else "0"
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


def set_alarm(rep, idx: int, am: int | None = None,
              rm: int | None = None) -> dict:
    """De per-sensor alarmroute zetten: ``am`` (0-3) en/of ``rm`` (roombitmasker).

    De adapterlaag over het enige nog niet vaststaande stuk contract. Wat hier
    verandert als de node een andere setter blijkt te hebben, staat helemaal in de
    ``MON_ALARM_*``-constanten bovenaan -- de rest van de server en het sjabloon
    hoeven het niet te weten.
    """
    out = {"ok": False, "error": ""}
    velden: dict = {MON_ALARM_IDX: int(idx)}
    if am is not None:
        if int(am) not in AM_LABELS:
            out["error"] = f"onbekende alarmroute {am} (kies 0-3)"
            return out
        velden[MON_ALARM_AM] = int(am)
    if rm is not None:
        if int(rm) < 0:
            out["error"] = "een roombitmasker kan niet negatief zijn"
            return out
        velden[MON_ALARM_RM] = int(rm)
    if len(velden) == 1:
        out["error"] = "niets te zetten: geef am, rm of allebei"
        return out
    ant = sensornode.post_form(_host(rep), MON_ALARM_PATH, velden)
    out["ok"] = ant["ok"]
    out["error"] = ant["error"]
    return out


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

def record_owners(rep, room_lijst: list[dict]) -> int:
    """De koppeling room-pubkey -> deze node persisteren, uit ``/rooms.json``.

    Een room-server-node host meerdere virtuele rooms met elk een eigen sleutel;
    op het mesh zijn dat losse entries. Dit legt vast dat ze bij één toestel horen,
    zodat de nodelijst ze kan groeperen in plaats van als anonieme unmanaged nodes
    te tonen. De node zelf is de schone bron -- ``/rooms.json`` noemt al zijn
    room-pubkeys -- dus dit wordt bijgewerkt telkens die lijst opgehaald is, en
    rooms die eruit verdwenen zijn worden opgeruimd (``prune_room_owners``).

    Geeft het aantal vastgelegde rooms terug. Faalt de databank, dan is dat geen
    reden om de pagina of de pollronde te laten stranden: de mapping is een extra,
    niet de kern.
    """
    rid = int(sensornode.firmware._field(rep, "id") or 0)
    if not rid:
        return 0
    behouden = []
    try:
        for kamer in room_lijst or []:
            pub = str(kamer.get("pub") or "").strip()
            if not pub:
                continue
            db.set_room_owner(db.node_key(pub), rid, pub,
                              kamer.get("idx"), kamer.get("name") or "")
            behouden.append(pub)
        db.prune_room_owners(rid, behouden)
    except Exception:  # noqa: BLE001 -- de mapping mag de rest nooit breken
        log.warning("Room-eigenaarschap niet bijgewerkt voor node %s", rid,
                    exc_info=True)
    return len(behouden)


def owners_for(rep) -> list:
    """De rooms die volgens de vastgelegde mapping op deze node draaien."""
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
