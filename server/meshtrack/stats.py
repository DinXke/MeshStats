"""Statistieken (1.3): aggregaten over positions en stats_events voor /api/stats/* (zie docs/api-stats.md).

Begrippen:
- bericht (message): één ontvangen T1-bericht dat iets opsloeg. Gewone toestanden: de hoofdrij
  (extra=0). SlowTrack (L) en FIFO (Q): één per (tracker, toestand, seq, rx_ts), ook als enkel
  extra punten nieuw waren. Een volledig dubbel L/Q-bericht slaat niets op en telt enkel in dup_points.
- punt (point): een opgeslagen rij met een positie. live = hoofdpunten van niet-L/Q-berichten,
  extra = eerdere punten uit niet-L/Q-berichten, slow = L-punten, fifo = Q-punten.
- vertraging (delay) = rx_ts - ts per punt. Bereik en emmers gaan op ontvangsttijd (rx_ts);
  de gaten van /fifo gaan op positietijd (ts).
Emmers: per uur (UTC-uur) of per dag (lokale middernacht, MESHTRACK_TZ, standaard Europe/Brussels).
"""
from __future__ import annotations

import os
from bisect import bisect_left, bisect_right
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Optional
from zoneinfo import ZoneInfo

from .db import DB, STAT_KINDS
from .protocol import STATES

try:
    TZ = ZoneInfo(os.environ.get("MESHTRACK_TZ", "Europe/Brussels"))
except Exception:  # noqa: BLE001 - geen tijdzonedatabase (bv. Windows zonder tzdata)
    TZ = datetime.now().astimezone().tzinfo

LATE_S = 300                    # recovered_late: later dan dit ontvangen
GAP_S = 600                     # gat in het live-spoor
MAX_GAPS = 20
MAX_BUCKETS = 2000
SNR_LO, SNR_HI, SNR_STEP = -20, 16, 2          # emmers [-20,-18) ... [14,16); daarbuiten in de randemmer
DELAY_BINS = [(0, 10), (10, 30), (30, 60), (60, 300), (300, 1800), (1800, 7200), (7200, 43200), (43200, None)]
HOPS_OK = "path_len BETWEEN 0 AND 63"          # 255 e.d. = onbekend / rechtstreeks zonder pad


@dataclass
class Scope:
    frm: int
    to: int
    ids: Optional[list[int]] = None             # None = alle trackers (geen filter)
    ev_chans: set[int] = field(default_factory=set)   # events zonder tracker: zichtbaar op deze kanalen


def _r(x: Optional[float], nd: int = 2) -> Optional[float]:
    return None if x is None else round(x, nd)


def _pf(sc: Scope) -> tuple[str, tuple]:
    """WHERE-deel voor positions binnen het bereik en de zichtbare trackers (ids zijn eigen ints)."""
    sql = "rx_ts BETWEEN ? AND ?"
    if sc.ids is not None:
        sql += f" AND tracker_id IN ({','.join(str(int(i)) for i in sc.ids)})" if sc.ids else " AND 0"
    return sql, (sc.frm, sc.to)


def _ef(sc: Scope) -> tuple[str, tuple]:
    sql = "ts BETWEEN ? AND ?"
    if sc.ids is not None:
        ids = ",".join(str(int(i)) for i in sc.ids)
        ch = ",".join(str(int(c)) for c in sc.ev_chans)
        parts = ([f"tracker_id IN ({ids})"] if ids else []) + \
                ([f"(tracker_id IS NULL AND channel_id IN ({ch}))"] if ch else [])
        sql += f" AND ({' OR '.join(parts)})" if parts else " AND 0"
    return sql, (sc.frm, sc.to)


def _msgs(sc: Scope) -> tuple[str, tuple]:
    """CTE m(tracker_id, state, rx_ts, ts, snr, path_len): één rij per bericht."""
    f, a = _pf(sc)
    return (f"WITH m AS (SELECT tracker_id, state, rx_ts, ts, snr, path_len FROM positions "
            f"WHERE {f} AND extra=0 AND state NOT IN ('L','Q') "
            f"UNION ALL SELECT tracker_id, state, rx_ts, MAX(ts), MAX(snr), MAX(path_len) FROM positions "
            f"WHERE {f} AND state IN ('L','Q') GROUP BY tracker_id, state, seq, rx_ts) "), a + a


# ---- emmers ----------------------------------------------------------------------

def pick_bucket(bucket: str, frm: int, to: int) -> str:
    if bucket in ("hour", "day"):
        return bucket
    return "hour" if to - frm <= 3 * 86400 else "day"


def bucket_starts(bucket: str, frm: int, to: int) -> list[int]:
    if bucket == "hour":
        return list(range(frm // 3600 * 3600, to + 1, 3600))
    out, d = [], datetime.fromtimestamp(frm, TZ).date()
    while True:
        t = int(datetime(d.year, d.month, d.day, tzinfo=TZ).timestamp())
        if t > to:
            return out or [t]
        out.append(t)
        d += timedelta(days=1)


def _idx(starts: list[int], hour: int) -> int:
    return bisect_right(starts, hour * 3600) - 1


# ---- samenvatting ----------------------------------------------------------------

def _empty_states() -> dict[str, int]:
    return {s: 0 for s in STATES}


def summary(db: DB, sc: Scope, trackers: list[dict[str, Any]], channels: dict[int, str], names=None) -> dict[str, Any]:
    f, a = _pf(sc)
    src = path_source(db, sc)
    fh = first_hops(db, sc, src)
    pts = {r[0]: r for r in db.rows(
        "SELECT tracker_id, SUM(lat IS NOT NULL), SUM(state='Q'), SUM(state='L'), "
        "SUM(extra=0 AND state NOT IN ('L','Q') AND lat IS NOT NULL), SUM(extra=1 AND state NOT IN ('L','Q')), "
        f"SUM(lat IS NOT NULL AND rx_ts-ts>{LATE_S}) FROM positions WHERE {f} GROUP BY tracker_id", a)}
    cte, ca = _msgs(sc)
    per: dict[int, dict[str, Any]] = {}
    by_state = _empty_states()
    tot = {"n": 0, "snr_s": 0.0, "snr_n": 0, "hop_s": 0, "hop_n": 0}
    for tid, st, n, ss, sn, smin, smax, hs, hn in db.rows(
            cte + "SELECT tracker_id, state, COUNT(*), SUM(snr), COUNT(snr), MIN(snr), MAX(snr), "
            f"SUM(CASE WHEN {HOPS_OK} THEN path_len END), SUM({HOPS_OK}) FROM m GROUP BY tracker_id, state", ca):
        e = per.setdefault(tid, {"n": 0, "q": 0, "snr_s": 0.0, "snr_n": 0, "min": None, "max": None, "hop_s": 0, "hop_n": 0})
        e["n"] += n
        e["q"] += n if st == "Q" else 0
        e["snr_s"] += ss or 0
        e["snr_n"] += sn
        e["hop_s"] += hs or 0
        e["hop_n"] += hn or 0
        if smin is not None:
            e["min"] = smin if e["min"] is None else min(e["min"], smin)
            e["max"] = smax if e["max"] is None else max(e["max"], smax)
        by_state[st] = by_state.get(st, 0) + n
        tot["n"] += n
        tot["snr_s"] += ss or 0
        tot["snr_n"] += sn
        tot["hop_s"] += hs or 0
        tot["hop_n"] += hn or 0
    delays: dict[int, dict[str, int]] = {}
    for tid, n, rn, d in db.rows(
            "SELECT tracker_id, n, rn, d FROM (SELECT tracker_id, rx_ts-ts AS d, "
            "ROW_NUMBER() OVER (PARTITION BY tracker_id ORDER BY rx_ts-ts) AS rn, "
            f"COUNT(*) OVER (PARTITION BY tracker_id) AS n FROM positions WHERE {f} AND lat IS NOT NULL) "
            "WHERE rn=(n*50+99)/100 OR rn=(n*90+99)/100", a):
        if rn == (n * 50 + 99) // 100:
            delays.setdefault(tid, {})["p50"] = d
        if rn == (n * 90 + 99) // 100:
            delays.setdefault(tid, {})["p90"] = d
    ev = events_totals(db, sc)
    rows = []
    for t in trackers:
        p, m, dl = pts.get(t["id"]), per.get(t["id"], {}), delays.get(t["id"], {})
        rows.append({
            "id": t["id"], "alias": t["alias"], "channel": channels.get(t.get("channel_id")),
            "channel_id": t.get("channel_id"),
            "messages": m.get("n", 0), "points": p[1] if p else 0, "q_messages": m.get("q", 0),
            "q_points": p[2] if p else 0, "l_points": p[3] if p else 0, "last_rx": t.get("last_rx"),
            "avg_snr": _r(m["snr_s"] / m["snr_n"]) if m.get("snr_n") else None,
            "min_snr": m.get("min"), "max_snr": m.get("max"),
            "avg_hops": _r(m["hop_s"] / m["hop_n"]) if m.get("hop_n") else None,
            "bat_last": t.get("last_bat"), "fw_mode": t.get("last_mode"),
            "delivery_delay_p50": dl.get("p50"), "delivery_delay_p90": dl.get("p90"),
            "first_hop": ({"hash": fh[t["id"]][0], "name": names(fh[t["id"]][0])["name"] if names else None,
                           "count": fh[t["id"]][1]} if t["id"] in fh else None),
        })
    active = {tid for tid, e in per.items() if e["n"]} | {tid for tid, r in pts.items() if r[1]}
    s = lambda i: sum((r[i] or 0) for r in pts.values())  # noqa: E731
    totals = {
        "messages": tot["n"], "messages_by_state": by_state,
        "points": s(1), "points_by_kind": {"live": s(4), "extra": s(5), "slow": s(3), "fifo": s(2)},
        "recovered_late": s(6),
        **{k: ev.get(k, 0) for k in ("dup_points", "dup_msg", "invalid", "unknown", "t1a_sent", "t1f_sent", "t1f_msg",
                                       "old_fw_dm")},
        "avg_snr": _r(tot["snr_s"] / tot["snr_n"]) if tot["snr_n"] else None,
        "avg_hops": _r(tot["hop_s"] / tot["hop_n"]) if tot["hop_n"] else None,
        "trackers_active": len(active),
    }
    return {"range": {"from": sc.frm, "to": sc.to}, "totals": totals, "trackers": rows, "path_source": src}


def events_totals(db: DB, sc: Scope) -> dict[str, int]:
    f, a = _ef(sc)
    return {k: n for k, n in db.rows(f"SELECT kind, SUM(n) FROM stats_events WHERE {f} GROUP BY kind", a)}


# ---- tijdreeks --------------------------------------------------------------------

def timeseries(db: DB, sc: Scope, bucket: str) -> dict[str, Any]:
    starts = bucket_starts(bucket, sc.frm, sc.to)
    series = [{"t": t, "messages": 0, "points": 0, "live": 0, "extra": 0, "slow": 0, "fifo": 0,
               "avg_snr": None, "min_snr": None, "max_snr": None, "avg_bat": None, "trackers": 0} for t in starts]
    acc = [{"snr_s": 0.0, "snr_n": 0, "bat_s": 0, "bat_n": 0, "trk": set()} for _ in starts]
    f, a = _pf(sc)
    for h, tid, pts, live, extra, slow, fifo, bs, bn in db.rows(
            "SELECT rx_ts/3600, tracker_id, SUM(lat IS NOT NULL), SUM(extra=0 AND state NOT IN ('L','Q') AND lat IS NOT NULL), "
            "SUM(extra=1 AND state NOT IN ('L','Q')), SUM(state='L'), SUM(state='Q'), "
            "SUM(CASE WHEN extra=0 THEN bat END), SUM(extra=0 AND bat IS NOT NULL) "
            f"FROM positions WHERE {f} GROUP BY 1, 2", a):
        i = _idx(starts, h)
        if i < 0:
            continue
        b, c = series[i], acc[i]
        b["points"] += pts
        b["live"] += live
        b["extra"] += extra
        b["slow"] += slow
        b["fifo"] += fifo
        c["bat_s"] += bs or 0
        c["bat_n"] += bn
        c["trk"].add(tid)
    cte, ca = _msgs(sc)
    for h, tid, n, ss, sn, smin, smax in db.rows(
            cte + "SELECT rx_ts/3600, tracker_id, COUNT(*), SUM(snr), COUNT(snr), MIN(snr), MAX(snr) FROM m GROUP BY 1, 2", ca):
        i = _idx(starts, h)
        if i < 0:
            continue
        b, c = series[i], acc[i]
        b["messages"] += n
        c["snr_s"] += ss or 0
        c["snr_n"] += sn
        c["trk"].add(tid)
        if smin is not None:
            b["min_snr"] = smin if b["min_snr"] is None else min(b["min_snr"], smin)
            b["max_snr"] = smax if b["max_snr"] is None else max(b["max_snr"], smax)
    for b, c in zip(series, acc):
        b["avg_snr"] = _r(c["snr_s"] / c["snr_n"]) if c["snr_n"] else None
        b["avg_bat"] = _r(c["bat_s"] / c["bat_n"], 1) if c["bat_n"] else None
        b["trackers"] = len(c["trk"])
    return {"bucket": bucket, "tz": str(TZ), "range": {"from": sc.frm, "to": sc.to}, "series": series}


def events_series(db: DB, sc: Scope, bucket: str, kinds: list[str]) -> dict[str, Any]:
    starts = bucket_starts(bucket, sc.frm, sc.to)
    series = [{"t": t, **{k: 0 for k in kinds}} for t in starts]
    totals = {k: 0 for k in kinds}
    f, a = _ef(sc)
    ks = ",".join(f"'{k}'" for k in kinds)           # alleen namen uit STAT_KINDS
    for h, kind, n in db.rows(f"SELECT ts/3600, kind, SUM(n) FROM stats_events WHERE {f} AND kind IN ({ks}) GROUP BY 1, 2", a):
        i = _idx(starts, h)
        if i >= 0:
            series[i][kind] += n
            totals[kind] += n
    return {"bucket": bucket, "tz": str(TZ), "range": {"from": sc.frm, "to": sc.to}, "kinds": kinds,
            "totals": totals, "series": series}


# ---- verdelingen ------------------------------------------------------------------

def distributions(db: DB, sc: Scope) -> dict[str, Any]:
    cte, ca = _msgs(sc)
    nbins = (SNR_HI - SNR_LO) // SNR_STEP
    snr = [{"lo": SNR_LO + i * SNR_STEP, "hi": SNR_LO + (i + 1) * SNR_STEP, "count": 0} for i in range(nbins)]
    for b, n in db.rows(cte + f"SELECT CAST((snr - ({SNR_LO})) / {float(SNR_STEP)} AS INTEGER), COUNT(*) "
                              "FROM m WHERE snr IS NOT NULL GROUP BY 1", ca):
        snr[min(max(b, 0), nbins - 1)]["count"] += n          # buiten -20..+16: in de randemmer
    hops_c = {h: n for h, n in db.rows(cte + f"SELECT path_len, COUNT(*) FROM m WHERE {HOPS_OK} GROUP BY 1", ca)}
    hops = [{"hops": h, "count": hops_c.get(h, 0)} for h in range(0, max(hops_c) + 1)] if hops_c else []
    states = _empty_states()
    for st, n in db.rows(cte + "SELECT state, COUNT(*) FROM m GROUP BY 1", ca):
        states[st] = states.get(st, 0) + n
    hod = [0] * 24
    for h, n in db.rows(cte + "SELECT rx_ts/3600, COUNT(*) FROM m GROUP BY 1", ca):
        hod[datetime.fromtimestamp(h * 3600, TZ).hour] += n
    f, a = _pf(sc)
    case = " ".join(f"WHEN rx_ts-ts < {hi} THEN {i}" for i, (_, hi) in enumerate(DELAY_BINS) if hi is not None)
    dc = {b: n for b, n in db.rows(f"SELECT CASE {case} ELSE {len(DELAY_BINS) - 1} END, COUNT(*) FROM positions "
                                   f"WHERE {f} AND lat IS NOT NULL GROUP BY 1", a)}
    delay = [{"lo": lo, "hi": hi, "count": dc.get(i, 0)} for i, (lo, hi) in enumerate(DELAY_BINS)]
    return {"range": {"from": sc.frm, "to": sc.to}, "tz": str(TZ), "snr": snr, "hops": hops, "delay": delay,
            "states": states, "hour_of_day": hod}


# ---- FIFO / inhaalwerk -----------------------------------------------------------

def fifo(db: DB, sc: Scope, trackers: list[dict[str, Any]]) -> dict[str, Any]:
    ids = [t["id"] for t in trackers]
    if not ids:
        return {"range": {"from": sc.frm, "to": sc.to}, "trackers": []}
    il = ",".join(str(int(i)) for i in ids)
    cte, ca = _msgs(sc)
    qm = {tid: n for tid, n in db.rows(cte + "SELECT tracker_id, COUNT(*) FROM m WHERE state='Q' GROUP BY 1", ca)}
    f, a = _pf(sc)
    qp = {tid: n for tid, n in db.rows(f"SELECT tracker_id, COUNT(*) FROM positions WHERE {f} AND state='Q' GROUP BY 1", a)}
    ef, ea = _ef(sc)
    t1f = {tid: n for tid, n in db.rows(f"SELECT tracker_id, SUM(n) FROM stats_events WHERE {ef} AND kind='t1f_sent' "
                                        "AND tracker_id IS NOT NULL GROUP BY 1", ea)}
    # gaten in het live-spoor (op positietijd) en de L/Q-punten die erin vallen
    gaps: dict[int, list[tuple[int, int]]] = {}
    for tid, prev, ts in db.rows(
            "SELECT tracker_id, prev, ts FROM (SELECT tracker_id, ts, LAG(ts) OVER (PARTITION BY tracker_id ORDER BY ts) AS prev "
            f"FROM positions WHERE tracker_id IN ({il}) AND ts BETWEEN ? AND ? AND lat IS NOT NULL "
            f"AND state NOT IN ('L','Q')) WHERE ts - prev > {GAP_S}", (sc.frm, sc.to)):
        gaps.setdefault(tid, []).append((prev, ts))
    hist: dict[int, list[int]] = {}
    if gaps:
        gl = ",".join(str(int(i)) for i in gaps)
        for tid, ts in db.rows(f"SELECT tracker_id, ts FROM positions WHERE tracker_id IN ({gl}) AND ts BETWEEN ? AND ? "
                               "AND state IN ('L','Q') ORDER BY tracker_id, ts", (sc.frm, sc.to)):
            hist.setdefault(tid, []).append(ts)
    out = []
    for t in trackers:
        tid = t["id"]
        h = hist.get(tid, [])
        filled = []
        for lo, hi in gaps.get(tid, []):
            n = bisect_left(h, hi) - bisect_right(h, lo)          # strikt tussen lo en hi
            if n:
                filled.append({"from": lo, "to": hi, "filled_points": n})
        filled.sort(key=lambda g: (g["to"] - g["from"], g["filled_points"]), reverse=True)
        q_m, q_p, sent = qm.get(tid, 0), qp.get(tid, 0), t1f.get(tid, 0)
        if not (q_m or q_p or sent or filled):
            continue
        out.append({"id": tid, "alias": t["alias"], "q_messages": q_m, "q_points": q_p, "t1f_sent": sent,
                    "avg_points_per_q": _r(q_p / q_m) if q_m else None,
                    "max_gap_filled_s": (filled[0]["to"] - filled[0]["from"]) if filled else None,
                    "gaps": filled[:MAX_GAPS]})
    return {"range": {"from": sc.frm, "to": sc.to}, "trackers": out}


# ---- paden en repeaters -------------------------------------------------------------

TOP_PATHS = 20


def path_source(db: DB, sc: Scope, wanted: str = "auto") -> str:
    """Eén bron per antwoord (anders telt elke kopie dubbel): openhop als die in het bereik iets heeft
    (alle kopieën van beide antennes), anders de companion."""
    if wanted in ("openhop", "companion"):
        return wanted
    f, a = _pf(sc)
    return "openhop" if db.rows(f"SELECT 1 FROM message_paths WHERE {f} AND source='openhop' LIMIT 1", a) else "companion"


def _path_rows(db: DB, sc: Scope, source: str) -> list[tuple]:
    f, a = _pf(sc)
    return db.rows(f"SELECT tracker_id, seq, state, path, hops, snr FROM message_paths WHERE {f} AND source=?", a + (source,))


def first_hops(db: DB, sc: Scope, source: str) -> dict[int, tuple[str, int]]:
    """Per tracker de meest gebruikte eerste hop (hash, aantal kopieën)."""
    f, a = _pf(sc)
    out: dict[int, tuple[str, int]] = {}
    for tid, h, n in db.rows(
            "SELECT tracker_id, CASE WHEN instr(path, ',') > 0 THEN substr(path, 1, instr(path, ',') - 1) ELSE path END AS h, "
            f"COUNT(*) AS n FROM message_paths WHERE {f} AND source=? AND path != '' GROUP BY 1, 2 ORDER BY 1, 3 DESC, 2",
            a + (source,)):
        out.setdefault(tid, (h, n))
    return out


def repeaters(db: DB, sc: Scope, trackers: list[dict[str, Any]], names, source: str) -> dict[str, Any]:
    """names(hash) -> {"name", "candidates"}. Gemiddelde SNR van een repeater = over de kopieën
    waarin hij de laatste hop was (de SNR is gemeten aan onze antenne, dus van die laatste schakel)."""
    reps: dict[str, dict[str, Any]] = {}
    top: dict[str, list] = {}
    msgs: dict[tuple, int] = {}              # (tracker, state, seq) -> kleinste aantal hops
    for tid, seq, st, path, hops, snr in _path_rows(db, sc, source):
        hs = path.split(",") if path else []
        for i, h in enumerate(hs):
            r = reps.setdefault(h, {"count": 0, "first": 0, "last": 0, "snr_s": 0.0, "snr_n": 0, "trk": set()})
            r["count"] += 1
            r["first"] += i == 0
            r["trk"].add(tid)
            if i == len(hs) - 1:
                r["last"] += 1
                if snr is not None:
                    r["snr_s"] += snr
                    r["snr_n"] += 1
        tp = top.setdefault(path, [0, 0.0, 0])
        tp[0] += 1
        if snr is not None:
            tp[1] += snr
            tp[2] += 1
        k = (tid, st, seq)
        msgs[k] = min(msgs.get(k, 99), len(hs))
    out_reps = []
    for h, r in reps.items():
        nm = names(h)
        out_reps.append({"hash": h, "name": nm["name"], "candidates": nm["candidates"], "count": r["count"],
                         "as_first_hop": r["first"], "as_last_hop": r["last"],
                         "avg_snr": _r(r["snr_s"] / r["snr_n"]) if r["snr_n"] else None, "trackers": len(r["trk"])})
    out_reps.sort(key=lambda r: (-r["count"], r["hash"]))
    tops = sorted(({"path": p, "hops": len(p.split(",")) if p else 0, "count": v[0],
                    "avg_snr": _r(v[1] / v[2]) if v[2] else None} for p, v in top.items()),
                  key=lambda x: (-x["count"], x["path"]))[:TOP_PATHS]
    per: dict[int, list[int]] = {}
    for (tid, _, _), h in msgs.items():
        per.setdefault(tid, []).append(h)
    hbt = []
    for t in trackers:
        hs = per.get(t["id"])
        if hs:
            hbt.append({"id": t["id"], "alias": t["alias"], "messages": len(hs), "avg_hops": _r(sum(hs) / len(hs)),
                        "direct_pct": _r(100.0 * sum(1 for h in hs if h == 0) / len(hs), 1)})
    return {"range": {"from": sc.frm, "to": sc.to}, "source": source, "repeaters": out_reps, "top_paths": tops,
            "hops_by_tracker": hbt}


def parse_kinds(kind: str) -> list[str]:
    """'' = alle soorten; anders een kommalijst. ValueError bij een onbekende soort."""
    if not kind:
        return list(STAT_KINDS)
    ks = [k.strip() for k in kind.split(",") if k.strip()]
    bad = [k for k in ks if k not in STAT_KINDS]
    if bad:
        raise ValueError(", ".join(bad))
    return list(dict.fromkeys(ks))
