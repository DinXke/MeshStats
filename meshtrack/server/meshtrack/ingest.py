"""Inkomend DM -> gevalideerde positie in de database."""
from __future__ import annotations

import time
from typing import Any, Optional

from .config import Config
from .db import DB
from .protocol import ProtocolError, UnknownVersion, is_meshtrack, is_suspect, parse

MAX_PAST_S = 7 * 24 * 3600   # sender-tijd ouder dan dit = klok fout
MAX_FUTURE_S = 300


def pick_ts(sender_ts: Optional[int], rx_ts: int, fix_age: Optional[int], fix_ts: Optional[int] = None) -> int:
    """Positietijd. Voorkeur: de GPS-tijd van de fix (fix_ts) uit het bericht. Anders
    de sender-tijd als die plausibel is (anders ontvangsttijd), min fix_age."""
    if fix_ts and rx_ts - MAX_PAST_S <= fix_ts <= rx_ts + MAX_FUTURE_S:
        return fix_ts
    base = rx_ts
    if sender_ts and rx_ts - MAX_PAST_S <= sender_ts <= rx_ts + MAX_FUTURE_S:
        base = sender_ts
    if fix_age:
        base -= fix_age
    return base


def handle(db: DB, cfg: Config, pubkey_prefix: str, text: str, sender_ts: Optional[int] = None,
           snr: Optional[float] = None, path_len: Optional[int] = None,
           now: Optional[int] = None) -> Optional[dict[str, Any]]:
    """Verwerk één DM. Geeft de opgeslagen positie (voor de live kaart) terug,
    of None als het bericht genegeerd werd (onbekend, ongeldig, dubbel)."""
    rx = int(now if now is not None else time.time())
    tracker = db.tracker_by_prefix(pubkey_prefix)
    if tracker is None:
        if is_meshtrack(text):
            db.log_unknown(pubkey_prefix, "onbekende tracker", text)
        return None
    try:
        r = parse(text)
    except UnknownVersion as e:
        db.log_unknown(pubkey_prefix, str(e), text)
        return None
    except ProtocolError as e:
        if is_meshtrack(text):
            db.log_unknown(pubkey_prefix, f"ongeldig: {e}", text)
        return None

    if db.is_duplicate(tracker["id"], r.seq, rx - cfg.dedup_window_s):
        return None

    p = {
        "ts": pick_ts(sender_ts, rx, r.fix_age_s, r.fix_ts) if r.has_fix else rx,
        "rx_ts": rx, "seq": r.seq, "state": r.state,
        "lat": r.lat, "lon": r.lon, "alt": r.alt_m, "spd": r.spd_kmh, "crs": r.crs_deg,
        "bat": r.bat_pct, "hdop": r.hdop, "fix_age": r.fix_age_s, "mode": r.mode, "power": r.power,
        "suspect": int(is_suspect(r, cfg.region_bbox, cfg.max_hdop)),
        "snr": snr, "path_len": path_len, "raw": text.strip(),
    }
    # Eerdere punten uit hetzelfde bericht, chronologisch. Een punt dat al binnen is (het
    # vorige bericht kwam toch aan, alleen de ACK niet) wordt overgeslagen.
    extras = []
    if r.extra and r.has_fix:
        for dt, lat, lon, spd in sorted(r.extra, key=lambda e: -e[0]):
            ts = p["ts"] - dt
            if db.position_at(tracker["id"], ts):
                continue
            ep = {"ts": ts, "rx_ts": rx, "seq": r.seq, "state": "M", "lat": lat, "lon": lon, "alt": None,
                  "spd": spd, "crs": None, "bat": None, "hdop": None, "fix_age": None, "mode": None, "power": None,
                  "suspect": int(not _in_bbox(lat, lon, cfg.region_bbox)), "snr": snr, "path_len": path_len,
                  "raw": f"(eerder punt uit bericht {r.seq})", "extra": 1}
            db.add_position(tracker["id"], ep)
            extras.append({"tracker_id": tracker["id"], **ep})
    db.add_position(tracker["id"], p)
    return {"tracker_id": tracker["id"], **p, "extras": extras}


def _in_bbox(lat: float, lon: float, bbox) -> bool:
    w, s, e, n = bbox
    return w <= lon <= e and s <= lat <= n
