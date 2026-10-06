"""Meshnodes voor de kaart, uitsluitend uit wat via de mesh binnenkwam (offline).

Bronnen, samengevoegd per pubkey (de recentste advert wint):
1. openHop `adverts`: alles wat openHop als observer hoorde sinds hij draait,
   met RSSI/SNR, aantal adverts en of het een directe buur is.
2. openHop `companion_contacts`: de contactlijsten van de companions die
   openHop host (ook nodes die alleen via een companion bekend zijn).
3. openHop `packets`: het kleinste aantal hops waarmee een advert van die node
   binnenkwam (lengte van het pad), handig om het bereik in te schatten.
4. de live contacten van de MeshTrack-companion (via meshcore-py).
Alles wordt alleen gelezen.
"""
from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from typing import Any

TYPES = {"Chat Node": 1, "Repeater": 2, "Room Server": 3, "Sensor": 4}

_cache: dict[str, Any] = {"at": 0.0, "nodes": []}


def _key(v: Any) -> str:
    if isinstance(v, (bytes, bytearray)):
        v = v.hex()
    return (v or "")[:12].lower()


def _has_pos(lat: Any, lon: Any) -> bool:
    return lat is not None and lon is not None and (abs(lat) > 0.001 or abs(lon) > 0.001)


def from_openhop(db_path: str) -> dict[str, dict[str, Any]]:
    p = Path(db_path)
    if not p.exists():
        return {}
    con = sqlite3.connect(f"file:{p}?mode=ro", uri=True, timeout=2)
    out: dict[str, dict[str, Any]] = {}
    try:
        for pk, name, ctype, is_rep, lat, lon, seen, rssi, snr, zero, cnt in con.execute(
                "SELECT pubkey, node_name, contact_type, is_repeater, latitude, longitude, last_seen, "
                "rssi, snr, zero_hop, advert_count FROM adverts"):
            if not _has_pos(lat, lon):
                continue
            out[_key(pk)] = {"key": _key(pk), "name": name or "", "type": TYPES.get(ctype, 2 if is_rep else 1),
                             "lat": lat, "lon": lon, "last_advert": int(seen or 0), "rssi": rssi, "snr": snr,
                             "direct": bool(zero), "adverts": cnt, "hops": None, "src": "observer"}
        for pk, name, adv_type, lat, lon, adv_ts, lastmod in con.execute(
                "SELECT pubkey, name, adv_type, gps_lat, gps_lon, last_advert_timestamp, lastmod FROM companion_contacts"):
            k = _key(pk)
            if not _has_pos(lat, lon):
                continue
            ts = int(max(adv_ts or 0, lastmod or 0))
            cur = out.get(k)
            if cur is None:
                out[k] = {"key": k, "name": name or "", "type": int(adv_type or 1), "lat": lat, "lon": lon,
                          "last_advert": ts, "direct": False, "hops": None, "src": "contact"}
            elif ts > cur["last_advert"]:
                cur.update(lat=lat, lon=lon, last_advert=ts)
        # kleinste aantal hops per node uit de opgevangen adverts (type 4)
        for payload, path in con.execute(
                "SELECT payload, original_path FROM packets WHERE type = 4 AND payload IS NOT NULL"):
            n = out.get(_key(payload))
            if n is None:
                continue
            try:
                hops = len(json.loads(path)) if path else 0
            except (ValueError, TypeError):
                continue
            if n["hops"] is None or hops < n["hops"]:
                n["hops"] = hops
    finally:
        con.close()
    return out


def merged(openhop_db: str, companion: list[dict[str, Any]], max_age_s: int = 120) -> list[dict[str, Any]]:
    if time.time() - _cache["at"] < max_age_s and _cache["nodes"]:
        return _cache["nodes"]
    try:
        base = from_openhop(openhop_db)
    except sqlite3.Error:
        base = {}
    for c in companion:
        k = c["key"].lower()
        if k not in base:
            base[k] = {**c, "hops": None, "direct": False, "src": "companion"}
    nodes = list(base.values())
    _cache.update(at=time.time(), nodes=nodes)
    return nodes
