"""Paden van trackerberichten (1.3): welke repeaters een kanaalbericht doorgaven.

CHANNEL_MSG_RECV van de companion geeft enkel path_len. Het pad zelf komt uit:
- de companion: openHop duwt elk ontvangen pakket rauw door (PUSH_CODE_LOG_RX_DATA 0x88,
  meshcore-py EventType.RX_LOG_DATA, met SNR en RSSI) -> bron "companion";
- de pakketdatabase van openHop (packets, alleen-lezen): elke kopie die een van zijn antennes
  hoorde, met rx_radio_id (dak/bureau) -> bron "openhop".
Beide ontcijferen we zelf met de sleutels van onze kanalen.

Pakket: [header][4 transportcodes als route 0 of 3][padbyte: laag 6 bits = hops, hoog 2 bits =
hashgrootte - 1][pad][payload]. GRP_TXT (type 5): [kanaalhash 1][MAC 2][AES-128-ECB, sleutel =
kanaalgeheim]; MAC = HMAC-SHA256(geheim, cijfertekst)[:2]; klaartekst = [ts LE4][vlaggen 1]["naam: tekst"].
Het pad staat in volgorde van doorgeven: eerste hop = repeater het dichtst bij de tracker,
laatste hop = de repeater die onze antenne hoorde.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import sqlite3
import time
from pathlib import Path
from typing import Any, Optional

from Crypto.Cipher import AES

log = logging.getLogger("meshtrack.paths")

PAYLOAD_GRP_TXT = 5


def parse_raw(raw: bytes) -> Optional[dict[str, Any]]:
    """Rauw meshpakket -> route, payloadtype, hashgrootte, pad (lijst hex) en payload. None als te kort."""
    try:
        header = raw[0]
        route, ptype = header & 0x03, (header >> 2) & 0x0F
        i = 1 + (4 if route in (0, 3) else 0)
        pb = raw[i]
        size, hops = ((pb >> 6) & 0x03) + 1, pb & 0x3F
        i += 1
        path_b = raw[i:i + hops * size]
        if len(path_b) != hops * size:
            return None
        return {"route": route, "type": ptype, "hash_size": size, "hops": hops,
                "path": [path_b[k:k + size].hex() for k in range(0, len(path_b), size)],
                "payload": raw[i + hops * size:]}
    except IndexError:
        return None


def channel_hash(secret: bytes) -> int:
    return hashlib.sha256(secret).digest()[0]


def decrypt_grp(payload: bytes, channels: list[dict[str, Any]]) -> Optional[tuple[dict[str, Any], str]]:
    """GRP_TXT-payload ontcijferen met een van onze kanalen -> (kanaal, "naam: tekst") of None."""
    if len(payload) < 3 + 16 or (len(payload) - 3) % 16:
        return None
    ct = payload[3:]
    for ch in channels:
        key = ch["_key"]
        if payload[0] != ch["_hash"]:
            continue
        if not hmac.compare_digest(hmac.new(key, ct, hashlib.sha256).digest()[:2], payload[1:3]):
            continue
        pt = AES.new(key, AES.MODE_ECB).decrypt(ct)
        return ch, pt[5:].rstrip(b"\0").decode("utf-8", "replace")
    return None


def prepare_channels(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for c in rows:
        try:
            key = bytes.fromhex(c["secret"])
        except (ValueError, TypeError):
            continue
        if len(key) == 16 and c.get("active", True):
            out.append({**c, "_key": key, "_hash": channel_hash(key)})
    return out


def t1c(text: str) -> Optional[dict[str, Any]]:
    """"naam: T1C|pk8|tag|seq|state|..." -> velden, of None als het geen trackerbericht is."""
    body = text.split(": ", 1)[1] if ": " in text else text
    if not body.startswith("T1C|"):
        return None
    parts = body.split("|", 3)
    if len(parts) < 4 or len(parts[1]) != 8:
        return None
    rest = parts[3]
    f = rest.split("|")
    if len(f) < 2 or not f[0].isdigit():
        return None
    return {"pk": parts[1].lower(), "tag": parts[2].lower(), "rest": rest, "seq": int(f[0]), "state": f[1]}


def match(db, raw: bytes, channels: list[dict[str, Any]], sign) -> Optional[dict[str, Any]]:
    """Rauw pakket -> {tracker_id, seq, state, path, hash_size, hops} voor een geldig ondertekend
    trackerbericht op een van onze kanalen; anders None. `sign(authkey, body)` = channel_tag."""
    p = parse_raw(raw)
    if p is None or p["type"] != PAYLOAD_GRP_TXT:
        return None
    dec = decrypt_grp(p["payload"], channels)
    if dec is None:
        return None
    ch, text = dec
    m = t1c(text)
    if m is None:
        return None
    t = db.tracker_by_prefix(m["pk"])
    if not t or t.get("kind") != "real":
        return None
    if m["tag"] == "-":
        if ch.get("require_sig", True):
            return None
    elif not t.get("authkey") or not hmac.compare_digest(sign(t["authkey"], f"{m['pk']}|{m['rest']}"), m["tag"]):
        return None
    return {"tracker_id": t["id"], "seq": m["seq"], "state": m["state"], "path": p["path"],
            "hash_size": p["hash_size"], "hops": p["hops"], "channel_id": ch["id"]}


# ---- bron B: de pakketdatabase van openHop -----------------------------------------------

CURSOR_KEY = "_openhop_paths_cursor"
BATCH = 5000


def poll_openhop(db, openhop_db: str, sign, since_ts: int) -> int:
    """Nieuwe GRP_TXT-pakketten (id > cursor) uit openHop lezen en hun paden opslaan. Alleen lezen
    (mode=ro). Eerste keer: vanaf since_ts. Geeft het aantal opgeslagen paden terug."""
    p = Path(openhop_db)
    if not p.exists():
        return 0
    cursor = int(db.settings().get(CURSOR_KEY) or 0)
    channels = prepare_channels(db.channels())
    con = sqlite3.connect(f"file:{p}?mode=ro", uri=True, timeout=2)
    stored = 0
    try:
        top = con.execute("SELECT MAX(id) FROM packets").fetchone()[0] or 0
        if cursor > top:                   # database van openHop opnieuw begonnen
            cursor = 0
        while True:
            rows = con.execute(
                "SELECT id, timestamp, rssi, snr, rx_radio_id, raw_packet, original_path FROM packets "
                "WHERE id > ? AND type = 5 AND timestamp >= ? ORDER BY id LIMIT ?", (cursor, since_ts, BATCH)).fetchall()
            if not rows:
                break
            batch = []
            for pid, ts, rssi, snr, radio, raw_hex, opath in rows:
                cursor = pid
                try:
                    raw = bytes.fromhex(raw_hex or "")
                except ValueError:
                    continue
                m = match(db, raw, channels, sign) if channels else None
                if m is None:
                    continue
                if not m["path"] and opath:     # pad ontbreekt in raw_packet: dan original_path
                    try:
                        m["path"] = [h.lower() for h in json.loads(opath)]
                        m["hops"] = len(m["path"])
                    except (ValueError, TypeError):
                        pass
                batch.append({**m, "rx_ts": int(ts), "snr": snr, "rssi": rssi, "radio": radio, "source": "openhop"})
            db.add_paths(batch)
            stored += len(batch)
            if len(rows) < BATCH:
                break
        cursor = max(cursor, top)              # alles tot top is bekeken (ook andere types)
    finally:
        con.close()
    db.set_setting(CURSOR_KEY, cursor)
    return stored


# ---- namen van repeaters --------------------------------------------------------------------

_names: dict[str, Any] = {"at": 0.0, "nodes": []}


def known_nodes(openhop_db: str, companion: list[dict[str, Any]], max_age_s: int = 300) -> list[dict[str, Any]]:
    """[{key (volledige of lange hex-pubkey), name, repeater}] uit de adverts van openHop en de
    contacten van de companion (alleen lezen, gecachet)."""
    if time.time() - _names["at"] < max_age_s and _names["nodes"]:
        return _names["nodes"]
    out: dict[str, dict[str, Any]] = {}
    p = Path(openhop_db)
    if p.exists():
        try:
            con = sqlite3.connect(f"file:{p}?mode=ro", uri=True, timeout=2)
            try:
                for pk, name, ctype, is_rep in con.execute(
                        "SELECT pubkey, node_name, contact_type, is_repeater FROM adverts"):
                    k = (pk.hex() if isinstance(pk, (bytes, bytearray)) else (pk or "")).lower()
                    if k:
                        out[k] = {"key": k, "name": name or "", "repeater": bool(is_rep) or ctype == "Repeater"}
            finally:
                con.close()
        except sqlite3.Error as e:
            log.debug("adverts van openHop niet gelezen: %s", e)
    for c in companion:
        k = (c.get("public_key") or "").lower()
        if k and k not in out:
            out[k] = {"key": k, "name": c.get("name") or "", "repeater": c.get("type") in (2, 3)}
    nodes = list(out.values())
    _names.update(at=time.time(), nodes=nodes)
    return nodes


def resolve(h: str, nodes: list[dict[str, Any]]) -> dict[str, Any]:
    """Padhash -> naam. Repeaters gaan voor; meerdere kandidaten: "a3 (2 kandidaten)"."""
    cands = [n for n in nodes if n["key"].startswith(h)]
    reps = [n for n in cands if n["repeater"]]
    cands = reps or cands
    if len(cands) == 1:
        return {"name": cands[0]["name"] or None, "candidates": 1}
    if not cands:
        return {"name": None, "candidates": 0}
    return {"name": f"{h} ({len(cands)} kandidaten)", "candidates": len(cands)}
