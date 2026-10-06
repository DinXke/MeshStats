"""SQLite-opslag: trackers en posities.

Eén verbinding, beschermd door een lock: alle queries zijn klein, en de
server heeft maar een handvol trackers.
"""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from typing import Any, Optional

SCHEMA = """
CREATE TABLE IF NOT EXISTS trackers (
  id          INTEGER PRIMARY KEY,
  pubkey      TEXT NOT NULL UNIQUE,          -- 64 hex, lowercase
  alias       TEXT NOT NULL,
  color       TEXT NOT NULL DEFAULT '#e4572e',
  icon        TEXT NOT NULL DEFAULT '',
  notes       TEXT NOT NULL DEFAULT '',
  active      INTEGER NOT NULL DEFAULT 1,
  created     INTEGER NOT NULL,
  last_rx     INTEGER,                        -- ontvangsttijd laatste bericht
  last_ts     INTEGER,                        -- tijd van de laatste positie
  last_lat    REAL, last_lon REAL,
  last_state  TEXT, last_mode TEXT,
  last_bat    INTEGER, last_spd INTEGER, last_crs INTEGER,
  last_snr    REAL, last_path_len INTEGER,
  last_seq    INTEGER
);
CREATE TABLE IF NOT EXISTS positions (
  id        INTEGER PRIMARY KEY,
  tracker_id INTEGER NOT NULL REFERENCES trackers(id) ON DELETE CASCADE,
  ts        INTEGER NOT NULL,                 -- positietijd (sender of ontvangst)
  rx_ts     INTEGER NOT NULL,
  seq       INTEGER NOT NULL,
  state     TEXT NOT NULL,
  lat REAL, lon REAL, alt INTEGER, spd INTEGER, crs INTEGER,
  bat INTEGER, hdop REAL, fix_age INTEGER, mode TEXT,
  suspect   INTEGER NOT NULL DEFAULT 0,
  snr REAL, path_len INTEGER,
  raw       TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS positions_tracker_ts ON positions(tracker_id, ts);
CREATE INDEX IF NOT EXISTS positions_dedup ON positions(tracker_id, seq, rx_ts);
CREATE TABLE IF NOT EXISTS sims (
  tracker_id INTEGER PRIMARY KEY REFERENCES trackers(id) ON DELETE CASCADE,
  profile    TEXT NOT NULL DEFAULT 'car',      -- car | bike | walk
  home_lat   REAL NOT NULL,
  home_lon   REAL NOT NULL,
  params     TEXT NOT NULL DEFAULT '{}',        -- JSON rules.Params
  loss_pct   INTEGER NOT NULL DEFAULT 5,        -- pakketverlies op de mesh
  batt_speed REAL NOT NULL DEFAULT 4,           -- batterij x sneller dan echt
  running    INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS geofences (
  id       INTEGER PRIMARY KEY,
  name     TEXT NOT NULL,
  kind     TEXT NOT NULL,                       -- circle | polygon
  geom     TEXT NOT NULL,                       -- circle: {"center":[lon,lat],"radius":m}; polygon: [[lon,lat],...]
  color    TEXT NOT NULL DEFAULT '#3b82f6',
  trackers TEXT NOT NULL DEFAULT '[]',          -- JSON tracker-id's; leeg = alle
  on_enter INTEGER NOT NULL DEFAULT 1,
  on_exit  INTEGER NOT NULL DEFAULT 1,
  notify_pubkey TEXT NOT NULL DEFAULT '',       -- optioneel: DM via de companion
  active   INTEGER NOT NULL DEFAULT 1,
  created  INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS geofence_state (
  geofence_id INTEGER NOT NULL REFERENCES geofences(id) ON DELETE CASCADE,
  tracker_id  INTEGER NOT NULL REFERENCES trackers(id) ON DELETE CASCADE,
  inside      INTEGER NOT NULL,
  PRIMARY KEY (geofence_id, tracker_id)
);
CREATE TABLE IF NOT EXISTS geofence_events (
  id          INTEGER PRIMARY KEY,
  ts          INTEGER NOT NULL,
  geofence_id INTEGER NOT NULL REFERENCES geofences(id) ON DELETE CASCADE,
  tracker_id  INTEGER NOT NULL REFERENCES trackers(id) ON DELETE CASCADE,
  event       TEXT NOT NULL,                    -- enter | exit
  lat REAL, lon REAL
);
CREATE INDEX IF NOT EXISTS geofence_events_ts ON geofence_events(ts);
CREATE TABLE IF NOT EXISTS unknown_msgs (
  id INTEGER PRIMARY KEY,
  rx_ts INTEGER NOT NULL,
  pubkey_prefix TEXT NOT NULL,
  reason TEXT NOT NULL,
  text TEXT NOT NULL
);
"""

TRACKER_EDITABLE = ("alias", "color", "icon", "notes", "active")


class DB:
    def __init__(self, path: str):
        self._lock = threading.Lock()
        self._c = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self._c.row_factory = sqlite3.Row
        self._c.execute("PRAGMA journal_mode=WAL")
        self._c.execute("PRAGMA foreign_keys=ON")
        self._c.executescript(SCHEMA)
        self._migrate()

    def _migrate(self) -> None:
        cols = {r["name"] for r in self._q("PRAGMA table_info(trackers)")}
        if "kind" not in cols:   # 0.2.1: echte of gesimuleerde tracker
            self._x("ALTER TABLE trackers ADD COLUMN kind TEXT NOT NULL DEFAULT 'real'")
        scols = {r["name"] for r in self._q("PRAGMA table_info(sims)")}
        if "drive" not in scols:  # 0.2.2: rijgedrag (snelheden, ritlengte, zwerven)
            self._x("ALTER TABLE sims ADD COLUMN drive TEXT NOT NULL DEFAULT '{}'")

    def _q(self, sql: str, args: tuple = ()) -> list[dict[str, Any]]:
        with self._lock:
            return [dict(r) for r in self._c.execute(sql, args).fetchall()]

    def _x(self, sql: str, args: tuple = ()) -> sqlite3.Cursor:
        with self._lock:
            return self._c.execute(sql, args)

    # ---- trackers -----------------------------------------------------------

    def trackers(self) -> list[dict[str, Any]]:
        return self._q("SELECT * FROM trackers ORDER BY alias COLLATE NOCASE")

    def tracker(self, tid: int) -> Optional[dict[str, Any]]:
        r = self._q("SELECT * FROM trackers WHERE id=?", (tid,))
        return r[0] if r else None

    def tracker_by_prefix(self, prefix: str) -> Optional[dict[str, Any]]:
        r = self._q("SELECT * FROM trackers WHERE pubkey LIKE ?", (prefix.lower() + "%",))
        return r[0] if len(r) == 1 else None

    def add_tracker(self, pubkey: str, alias: str, color: str = "#e4572e", icon: str = "",
                    notes: str = "", active: bool = True, kind: str = "real") -> int:
        cur = self._x(
            "INSERT INTO trackers(pubkey, alias, color, icon, notes, active, created, kind) VALUES(?,?,?,?,?,?,?,?)",
            (pubkey.lower(), alias, color, icon, notes, int(active), int(time.time()), kind),
        )
        return cur.lastrowid

    def update_tracker(self, tid: int, **fields: Any) -> None:
        sets = [(k, v) for k, v in fields.items() if k in TRACKER_EDITABLE and v is not None]
        if not sets:
            return
        sql = "UPDATE trackers SET " + ", ".join(f"{k}=?" for k, _ in sets) + " WHERE id=?"
        self._x(sql, tuple(int(v) if k == "active" else v for k, v in sets) + (tid,))

    def delete_tracker(self, tid: int) -> None:
        self._x("DELETE FROM trackers WHERE id=?", (tid,))

    # ---- posities -----------------------------------------------------------

    def is_duplicate(self, tid: int, seq: int, since: int) -> bool:
        return bool(self._q(
            "SELECT 1 FROM positions WHERE tracker_id=? AND seq=? AND rx_ts>=? LIMIT 1", (tid, seq, since)))

    def add_position(self, tid: int, p: dict[str, Any]) -> int:
        cols = ("tracker_id", "ts", "rx_ts", "seq", "state", "lat", "lon", "alt", "spd", "crs", "bat",
                "hdop", "fix_age", "mode", "suspect", "snr", "path_len", "raw")
        vals = (tid,) + tuple(p.get(c) for c in cols[1:])
        cur = self._x(f"INSERT INTO positions({','.join(cols)}) VALUES({','.join('?' * len(cols))})", vals)
        # tracker-samenvatting bijwerken
        upd = {"last_rx": p["rx_ts"], "last_state": p["state"], "last_seq": p["seq"],
               "last_snr": p.get("snr"), "last_path_len": p.get("path_len")}
        if p.get("bat") is not None:
            upd["last_bat"] = p["bat"]
        if p.get("mode"):
            upd["last_mode"] = p["mode"]
        if p.get("lat") is not None and not p.get("suspect"):
            upd.update(last_ts=p["ts"], last_lat=p["lat"], last_lon=p["lon"],
                       last_spd=p.get("spd"), last_crs=p.get("crs"))
        sql = "UPDATE trackers SET " + ", ".join(f"{k}=?" for k in upd) + " WHERE id=?"
        self._x(sql, tuple(upd.values()) + (tid,))
        return cur.lastrowid

    def track(self, tid: int, since: int, limit: int = 5000) -> list[dict[str, Any]]:
        return self._q(
            "SELECT ts, lat, lon, alt, spd, crs, bat, state, suspect, seq, snr, path_len FROM positions "
            "WHERE tracker_id=? AND ts>=? AND lat IS NOT NULL ORDER BY ts LIMIT ?", (tid, since, limit))

    def purge_positions(self, tid: int) -> None:
        self._x("DELETE FROM positions WHERE tracker_id=?", (tid,))

    def prune(self, older_than: int) -> int:
        n = self._x("DELETE FROM positions WHERE rx_ts<?", (older_than,)).rowcount
        self._x("DELETE FROM geofence_events WHERE ts<?", (older_than,))
        self._x("DELETE FROM unknown_msgs WHERE rx_ts<?", (older_than,))
        return n

    # ---- simulator ----------------------------------------------------------

    def sims(self) -> list[dict[str, Any]]:
        rows = self._q("SELECT s.*, t.alias, t.color, t.pubkey FROM sims s JOIN trackers t ON t.id=s.tracker_id")
        for r in rows:
            r["params"] = json.loads(r["params"])
            r["drive"] = json.loads(r.get("drive") or "{}")
        return rows

    def sim(self, tid: int) -> Optional[dict[str, Any]]:
        return next((s for s in self.sims() if s["tracker_id"] == tid), None)

    def save_sim(self, tid: int, profile: str, home_lat: float, home_lon: float, params: dict,
                 loss_pct: int, batt_speed: float, running: bool, drive: Optional[dict] = None) -> None:
        self._x(
            "INSERT INTO sims(tracker_id, profile, home_lat, home_lon, params, loss_pct, batt_speed, running, drive) "
            "VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(tracker_id) DO UPDATE SET profile=excluded.profile, "
            "home_lat=excluded.home_lat, home_lon=excluded.home_lon, params=excluded.params, "
            "loss_pct=excluded.loss_pct, batt_speed=excluded.batt_speed, running=excluded.running, "
            "drive=excluded.drive",
            (tid, profile, home_lat, home_lon, json.dumps(params), loss_pct, batt_speed, int(running),
             json.dumps(drive or {})))

    def set_sim_running(self, tid: int, running: bool) -> None:
        self._x("UPDATE sims SET running=? WHERE tracker_id=?", (int(running), tid))

    # ---- geofences ----------------------------------------------------------

    def geofences(self) -> list[dict[str, Any]]:
        rows = self._q("SELECT * FROM geofences ORDER BY name COLLATE NOCASE")
        for r in rows:
            r["geom"] = json.loads(r["geom"])
            r["trackers"] = json.loads(r["trackers"])
        return rows

    def geofence(self, gid: int) -> Optional[dict[str, Any]]:
        return next((g for g in self.geofences() if g["id"] == gid), None)

    def add_geofence(self, g: dict[str, Any]) -> int:
        cur = self._x(
            "INSERT INTO geofences(name, kind, geom, color, trackers, on_enter, on_exit, notify_pubkey, active, created) "
            "VALUES(?,?,?,?,?,?,?,?,?,?)",
            (g["name"], g["kind"], json.dumps(g["geom"]), g["color"], json.dumps(g["trackers"]),
             int(g["on_enter"]), int(g["on_exit"]), g["notify_pubkey"], int(g["active"]), int(time.time())))
        return cur.lastrowid

    def update_geofence(self, gid: int, g: dict[str, Any]) -> None:
        self._x(
            "UPDATE geofences SET name=?, kind=?, geom=?, color=?, trackers=?, on_enter=?, on_exit=?, "
            "notify_pubkey=?, active=? WHERE id=?",
            (g["name"], g["kind"], json.dumps(g["geom"]), g["color"], json.dumps(g["trackers"]),
             int(g["on_enter"]), int(g["on_exit"]), g["notify_pubkey"], int(g["active"]), gid))
        self._x("DELETE FROM geofence_state WHERE geofence_id=?", (gid,))   # vorm gewijzigd: opnieuw bepalen

    def delete_geofence(self, gid: int) -> None:
        self._x("DELETE FROM geofences WHERE id=?", (gid,))

    def geofence_inside(self, gid: int, tid: int) -> Optional[bool]:
        r = self._q("SELECT inside FROM geofence_state WHERE geofence_id=? AND tracker_id=?", (gid, tid))
        return bool(r[0]["inside"]) if r else None

    def set_geofence_inside(self, gid: int, tid: int, inside: bool) -> None:
        self._x("INSERT INTO geofence_state(geofence_id, tracker_id, inside) VALUES(?,?,?) "
                "ON CONFLICT(geofence_id, tracker_id) DO UPDATE SET inside=excluded.inside", (gid, tid, int(inside)))

    def add_geofence_event(self, ts: int, gid: int, tid: int, event: str, lat: float, lon: float) -> int:
        return self._x("INSERT INTO geofence_events(ts, geofence_id, tracker_id, event, lat, lon) VALUES(?,?,?,?,?,?)",
                       (ts, gid, tid, event, lat, lon)).lastrowid

    def geofence_events(self, limit: int = 50) -> list[dict[str, Any]]:
        return self._q(
            "SELECT e.*, g.name AS geofence, t.alias AS tracker FROM geofence_events e "
            "JOIN geofences g ON g.id=e.geofence_id JOIN trackers t ON t.id=e.tracker_id "
            "ORDER BY e.id DESC LIMIT ?", (limit,))

    # ---- onbekende berichten ------------------------------------------------

    def log_unknown(self, prefix: str, reason: str, text: str) -> None:
        self._x("INSERT INTO unknown_msgs(rx_ts, pubkey_prefix, reason, text) VALUES(?,?,?,?)",
                (int(time.time()), prefix, reason, text[:200]))

    def unknown(self, limit: int = 50) -> list[dict[str, Any]]:
        return self._q("SELECT * FROM unknown_msgs ORDER BY id DESC LIMIT ?", (limit,))
