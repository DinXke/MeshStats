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

from .protocol import HISTORY_STATES

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
CREATE TABLE IF NOT EXISTS groups (
  id            INTEGER PRIMARY KEY,
  name          TEXT NOT NULL UNIQUE,
  description   TEXT NOT NULL DEFAULT '',
  perms         TEXT NOT NULL DEFAULT '[]',     -- JSON lijst rechten
  all_trackers  INTEGER NOT NULL DEFAULT 1,
  trackers      TEXT NOT NULL DEFAULT '[]',     -- JSON tracker-id's als all_trackers = 0
  history_hours INTEGER NOT NULL DEFAULT 0,     -- 0 = onbeperkt
  created       INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS users (
  id            INTEGER PRIMARY KEY,
  username      TEXT NOT NULL UNIQUE COLLATE NOCASE,
  display_name  TEXT NOT NULL DEFAULT '',
  password_hash TEXT NOT NULL,
  group_id      INTEGER NOT NULL REFERENCES groups(id),
  active        INTEGER NOT NULL DEFAULT 1,
  created       INTEGER NOT NULL,
  last_login    INTEGER,
  session_gen   INTEGER NOT NULL DEFAULT 0      -- verhogen = alle sessies ongeldig
);
CREATE TABLE IF NOT EXISTS shares (
  id        INTEGER PRIMARY KEY,
  token     TEXT NOT NULL UNIQUE,
  name      TEXT NOT NULL,
  trackers  TEXT NOT NULL,                      -- JSON tracker-id's
  hours     INTEGER NOT NULL DEFAULT 12,        -- spoor tot zoveel uur terug
  sidebar   INTEGER NOT NULL DEFAULT 0,
  expires   INTEGER,                            -- NULL = nooit
  created_by TEXT NOT NULL,
  created   INTEGER NOT NULL,
  last_used INTEGER
);
CREATE TABLE IF NOT EXISTS audit (
  id     INTEGER PRIMARY KEY,
  ts     INTEGER NOT NULL,
  who    TEXT NOT NULL,
  action TEXT NOT NULL,
  detail TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS audit_ts ON audit(ts);
CREATE TABLE IF NOT EXISTS settings (
  key   TEXT PRIMARY KEY,
  value TEXT NOT NULL                         -- JSON
);
CREATE TABLE IF NOT EXISTS alert_rules (
  id         INTEGER PRIMARY KEY,
  name       TEXT NOT NULL,
  active     INTEGER NOT NULL DEFAULT 1,
  events     TEXT NOT NULL DEFAULT '[]',      -- JSON: W, S, E, N, P, B, zone_in, zone_out, bat_low, silent
  trackers   TEXT NOT NULL DEFAULT '[]',      -- JSON tracker-id's; leeg = alle
  recipients TEXT NOT NULL DEFAULT '[]',      -- JSON [{"pubkey":..., "name":...}]
  cooldown_s INTEGER NOT NULL DEFAULT 900,    -- zelfde tracker + gebeurtenis niet vaker dan dit
  created    INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS alert_log (
  id        INTEGER PRIMARY KEY,
  ts        INTEGER NOT NULL,
  rule      TEXT NOT NULL,
  tracker   TEXT NOT NULL,
  event     TEXT NOT NULL,
  recipient TEXT NOT NULL,
  text      TEXT NOT NULL,
  status    TEXT NOT NULL,                    -- wacht | ok | mislukt | overgeslagen
  attempts  INTEGER NOT NULL DEFAULT 0,
  done_ts   INTEGER
);
CREATE INDEX IF NOT EXISTS alert_log_ts ON alert_log(ts);
CREATE TABLE IF NOT EXISTS channels (             -- 0.7: kanalen waarop de server meeluistert
  id          INTEGER PRIMARY KEY,
  name        TEXT NOT NULL UNIQUE COLLATE NOCASE,
  secret      TEXT NOT NULL,                   -- 32 hex (128 bit), zoals de MeshCore-app
  slot        INTEGER NOT NULL UNIQUE,         -- kanaalnummer op de server-companion
  require_sig INTEGER NOT NULL DEFAULT 1,      -- alleen ondertekende MeshTrack-berichten
  active      INTEGER NOT NULL DEFAULT 1,
  tracker_group_id INTEGER,                    -- automatische trackergroep van dit kanaal
  created     INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS tracker_groups (       -- 0.5: groepen van trackers
  id          INTEGER PRIMARY KEY,
  name        TEXT NOT NULL UNIQUE COLLATE NOCASE,
  color       TEXT NOT NULL DEFAULT '#64748b',
  description TEXT NOT NULL DEFAULT '',
  created     INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS tracker_group_members (
  group_id    INTEGER NOT NULL,
  tracker_id  INTEGER NOT NULL,
  PRIMARY KEY (group_id, tracker_id)
);
CREATE TABLE IF NOT EXISTS user_groups (          -- 0.5: een gebruiker in meerdere groepen
  user_id     INTEGER NOT NULL,
  group_id    INTEGER NOT NULL,
  PRIMARY KEY (user_id, group_id)
);
CREATE TABLE IF NOT EXISTS tracker_keys (
  id          INTEGER PRIMARY KEY,
  tracker_id  INTEGER NOT NULL REFERENCES trackers(id) ON DELETE CASCADE,
  ts          INTEGER NOT NULL,
  kind        TEXT NOT NULL,                 -- generated | backup | import
  who         TEXT NOT NULL DEFAULT '',
  pubkey      TEXT NOT NULL,
  note        TEXT NOT NULL DEFAULT '',
  summary     TEXT NOT NULL DEFAULT '{}',    -- zonder geheimen, voor de lijst
  blob        TEXT NOT NULL                  -- versleuteld (AES-GCM), zie keys.py
);
CREATE INDEX IF NOT EXISTS tracker_keys_t ON tracker_keys(tracker_id, ts);
CREATE TABLE IF NOT EXISTS unknown_msgs (
  id INTEGER PRIMARY KEY,
  rx_ts INTEGER NOT NULL,
  pubkey_prefix TEXT NOT NULL,
  reason TEXT NOT NULL,
  text TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS stats_events (         -- 1.3: tellers voor wat niet in positions staat
  ts         INTEGER NOT NULL,
  kind       TEXT NOT NULL,                   -- zie STAT_KINDS
  tracker_id INTEGER,
  channel_id INTEGER,
  n          INTEGER NOT NULL DEFAULT 1
);
CREATE INDEX IF NOT EXISTS stats_events_ts ON stats_events(ts);
CREATE TABLE IF NOT EXISTS message_paths (        -- 1.3: pad van elke gehoorde kopie van een trackerbericht
  id         INTEGER PRIMARY KEY,
  tracker_id INTEGER NOT NULL,
  seq        INTEGER NOT NULL,
  state      TEXT NOT NULL,
  rx_ts      INTEGER NOT NULL,
  path       TEXT NOT NULL DEFAULT '',        -- hex-hashes met komma's, eerste hop (bij de tracker) eerst
  hash_size  INTEGER,
  hops       INTEGER NOT NULL DEFAULT 0,
  snr REAL, rssi INTEGER,
  radio      TEXT,                            -- antenne van openHop (dak/bureau); NULL bij de companion
  source     TEXT NOT NULL,                   -- companion | openhop
  channel_id INTEGER
);
CREATE INDEX IF NOT EXISTS message_paths_rx ON message_paths(rx_ts);
CREATE INDEX IF NOT EXISTS message_paths_tracker_rx ON message_paths(tracker_id, rx_ts);
"""

# 1.3: soorten in stats_events
STAT_KINDS = ("t1a_sent", "t1f_sent", "t1f_msg", "dup_points", "dup_msg", "invalid", "unknown", "old_fw_dm")

TRACKER_EDITABLE = ("alias", "color", "icon", "notes", "active", "lost", "lost_since", "last_via", "channel_id")


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
        for table in ("geofences", "alert_rules"):   # 0.3.1: persoonlijke zones en regels
            if "owner" not in {r["name"] for r in self._q(f"PRAGMA table_info({table})")}:
                self._x(f"ALTER TABLE {table} ADD COLUMN owner INTEGER")
        if "prefs" not in {r["name"] for r in self._q("PRAGMA table_info(users)")}:
            self._x("ALTER TABLE users ADD COLUMN prefs TEXT NOT NULL DEFAULT '{}'")
        if "power" not in {r["name"] for r in self._q("PRAGMA table_info(positions)")}:   # 0.3.1: voeding
            self._x("ALTER TABLE positions ADD COLUMN power TEXT")
        if "last_power" not in {r["name"] for r in self._q("PRAGMA table_info(trackers)")}:
            self._x("ALTER TABLE trackers ADD COLUMN last_power TEXT")
        if "region" not in {r["name"] for r in self._q("PRAGMA table_info(channels)")}:   # 0.7: regio per kanaal
            self._x("ALTER TABLE channels ADD COLUMN region TEXT NOT NULL DEFAULT 'be'")
        if "authkey" not in {r["name"] for r in self._q("PRAGMA table_info(trackers)")}:   # 0.7
            self._x("ALTER TABLE trackers ADD COLUMN authkey TEXT")
            self._x("ALTER TABLE trackers ADD COLUMN last_via TEXT")
        for table in ("alert_rules", "shares"):   # 0.5.1: trackergroepen in regels en deellinks
            if "tracker_groups" not in {r["name"] for r in self._q(f"PRAGMA table_info({table})")}:
                self._x(f"ALTER TABLE {table} ADD COLUMN tracker_groups TEXT NOT NULL DEFAULT '[]'")
        if "includes" not in {r["name"] for r in self._q("PRAGMA table_info(tracker_groups)")}:   # 0.8.1: groep van groepen
            self._x("ALTER TABLE tracker_groups ADD COLUMN includes TEXT NOT NULL DEFAULT '[]'")
        if "tracker_groups" not in {r["name"] for r in self._q("PRAGMA table_info(groups)")}:   # 0.5
            self._x("ALTER TABLE groups ADD COLUMN tracker_groups TEXT NOT NULL DEFAULT '[]'")
        if not self._q("SELECT 1 FROM user_groups LIMIT 1"):    # 0.5: groep van elke gebruiker overnemen
            self._x("INSERT OR IGNORE INTO user_groups(user_id, group_id) SELECT id, group_id FROM users")
        if "lost" not in {r["name"] for r in self._q("PRAGMA table_info(trackers)")}:   # 0.4: verloren
            self._x("ALTER TABLE trackers ADD COLUMN lost INTEGER NOT NULL DEFAULT 0")
            self._x("ALTER TABLE trackers ADD COLUMN lost_since INTEGER")
            self._x("ALTER TABLE trackers ADD COLUMN lost_seen INTEGER")
        if "last_slow_rx" not in {r["name"] for r in self._q("PRAGMA table_info(trackers)")}:   # 1.1: SlowTrack
            self._x("ALTER TABLE trackers ADD COLUMN last_slow_rx INTEGER")   # ontvangst laatste SlowTrack-bericht
            self._x("ALTER TABLE trackers ADD COLUMN last_slow_ts INTEGER")   # tijd van het nieuwste gelogde punt
        if "last_fifo_rx" not in {r["name"] for r in self._q("PRAGMA table_info(trackers)")}:   # 1.2: FIFO
            self._x("ALTER TABLE trackers ADD COLUMN last_fifo_rx INTEGER")   # ontvangst laatste FIFO-bericht (Q)
            self._x("ALTER TABLE trackers ADD COLUMN last_fifo_ts INTEGER")   # tijd van het nieuwste ingehaalde punt
        scols = {r["name"] for r in self._q("PRAGMA table_info(sims)")}
        if "drive" not in scols:  # 0.2.2: rijgedrag (snelheden, ritlengte, zwerven)
            self._x("ALTER TABLE sims ADD COLUMN drive TEXT NOT NULL DEFAULT '{}'")
        if "extra" not in {r["name"] for r in self._q("PRAGMA table_info(positions)")}:   # 1.3: statistieken
            # 1 = eerder punt uit een bericht (geen hoofdpunt); bestaande rijen herkennen aan hun raw-tekst
            self._x("ALTER TABLE positions ADD COLUMN extra INTEGER NOT NULL DEFAULT 0")
            self._x("UPDATE positions SET extra=1 WHERE raw LIKE '(eerder punt%'")
        # dekkende index voor de statistieken (bereik op ontvangsttijd, zonder de tabel zelf te lezen)
        self._x("CREATE INDEX IF NOT EXISTS positions_stats ON positions(rx_ts, tracker_id, state, extra, ts, seq, "
                "snr, path_len, bat, lat)")
        self._migrate_channels()

    def _migrate_channels(self) -> None:
        """1.0: alles via kanalen. Elke tracker heeft één trackingkanaal; groepen krijgen per
        kanaal 'kaart' of 'sleutel'; deellinks en meldingsregels kiezen kanalen. De oude
        trackergroepen worden eenmalig omgezet (de tabellen blijven, ongebruikt, bestaan)."""
        if "channel_id" in {r["name"] for r in self._q("PRAGMA table_info(trackers)")}:
            return
        self._x("ALTER TABLE trackers ADD COLUMN channel_id INTEGER")
        self._x("ALTER TABLE groups ADD COLUMN channels TEXT NOT NULL DEFAULT '{}'")
        for table in ("alert_rules", "shares"):
            self._x(f"ALTER TABLE {table} ADD COLUMN channels TEXT NOT NULL DEFAULT '[]'")
        chans = self._q("SELECT id, name, tracker_group_id FROM channels")
        by_group = {c["tracker_group_id"]: c["id"] for c in chans if c["tracker_group_id"]}
        includes = {r["id"]: json.loads(r.get("includes") or "[]") for r in self._q("SELECT id, includes FROM tracker_groups")}

        def chans_of(tgs: list[int]) -> set[int]:
            """Kanalen achter trackergroepen (ook via een groep van groepen, één niveau)."""
            out: set[int] = set()
            for tg in tgs:
                for g in [tg] + list(includes.get(tg, [])):
                    if g in by_group:
                        out.add(by_group[g])
            return out

        # trackers: kanaal van hun kanaalgroep, anders uit "kanaal <naam>" (laatst gebruikt)
        for r in self._q("SELECT group_id, tracker_id FROM tracker_group_members"):
            if r["group_id"] in by_group:
                self._x("UPDATE trackers SET channel_id=? WHERE id=? AND channel_id IS NULL", (by_group[r["group_id"]], r["tracker_id"]))
        names = {c["name"].lower(): c["id"] for c in chans}
        for t in self._q("SELECT id, last_via FROM trackers WHERE channel_id IS NULL AND last_via LIKE 'kanaal %'"):
            cid = names.get(t["last_via"][7:].lower())
            if cid:
                self._x("UPDATE trackers SET channel_id=? WHERE id=?", (cid, t["id"]))
        # groepen: kanaalgroep -> 'kaart', of 'sleutel' als de groep toestellen mag instellen
        for g in self._q("SELECT id, perms, tracker_groups FROM groups"):
            perms = set(json.loads(g["perms"] or "[]"))
            level = "sleutel" if perms & {"trackers.serial", "keys.manage", "system.manage"} else "kaart"
            cs = chans_of(json.loads(g.get("tracker_groups") or "[]"))
            if cs:
                self._x("UPDATE groups SET channels=? WHERE id=?", (json.dumps({str(c): level for c in sorted(cs)}), g["id"]))
        for table in ("alert_rules", "shares"):
            for r in self._q(f"SELECT id, tracker_groups FROM {table}"):
                cs = chans_of(json.loads(r.get("tracker_groups") or "[]"))
                if cs:
                    self._x(f"UPDATE {table} SET channels=? WHERE id=?", (json.dumps(sorted(cs)), r["id"]))

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
        self._x(sql, tuple(int(v) if k in ("active", "lost") else v for k, v in sets) + (tid,))

    def set_lost(self, tid: int, lost: bool) -> None:
        if lost:
            self._x("UPDATE trackers SET lost=1, lost_since=?, lost_seen=NULL WHERE id=? AND lost=0",
                    (int(time.time()), tid))
        else:
            self._x("UPDATE trackers SET lost=0, lost_since=NULL, lost_seen=NULL WHERE id=?", (tid,))

    def mark_lost_seen(self, tid: int, ts: int) -> None:
        self._x("UPDATE trackers SET lost_seen=? WHERE id=?", (ts, tid))

    # ---- sleutels en backups -------------------------------------------------

    def add_key(self, tid: int, kind: str, who: str, pubkey: str, note: str, summary: dict, blob: str) -> int:
        cur = self._x("INSERT INTO tracker_keys(tracker_id, ts, kind, who, pubkey, note, summary, blob) VALUES(?,?,?,?,?,?,?,?)",
                      (tid, int(time.time()), kind, who, pubkey.lower(), note, json.dumps(summary, ensure_ascii=False), blob))
        return cur.lastrowid

    def keys(self, tid: int) -> list[dict[str, Any]]:
        rows = self._q("SELECT id, tracker_id, ts, kind, who, pubkey, note, summary FROM tracker_keys "
                       "WHERE tracker_id=? ORDER BY ts DESC, id DESC", (tid,))
        for r in rows:
            r["summary"] = json.loads(r["summary"] or "{}")
        return rows

    def key(self, kid: int) -> Optional[dict[str, Any]]:
        r = self._q("SELECT * FROM tracker_keys WHERE id=?", (kid,))
        return r[0] if r else None

    def key_counts(self) -> dict[int, int]:
        return {r["tracker_id"]: r["n"] for r in self._q("SELECT tracker_id, COUNT(*) n FROM tracker_keys GROUP BY tracker_id")}

    def delete_key(self, kid: int) -> None:
        self._x("DELETE FROM tracker_keys WHERE id=?", (kid,))

    def delete_tracker(self, tid: int) -> None:
        self._x("DELETE FROM tracker_keys WHERE tracker_id=?", (tid,))
        self._x("DELETE FROM tracker_group_members WHERE tracker_id=?", (tid,))
        self._x("DELETE FROM trackers WHERE id=?", (tid,))

    # ---- posities -----------------------------------------------------------

    def position_at(self, tid: int, ts: int) -> bool:
        return bool(self._q("SELECT 1 FROM positions WHERE tracker_id=? AND ts=? AND lat IS NOT NULL LIMIT 1", (tid, ts)))

    def is_duplicate(self, tid: int, seq: int, since: int, state: str = "M") -> bool:
        """Zelfde seq binnen het venster = herhaling. SlowTrack (L), FIFO (Q) en de gewone
        tracking worden elk apart vergeleken: valt een seq samen met die van een andere soort
        (aparte teller in de firmware), dan is dat geen herhaling."""
        if state in HISTORY_STATES:
            st, args = "state=?", (tid, seq, since, state)
        else:
            st, args = "state NOT IN ('L','Q')", (tid, seq, since)
        return bool(self._q(
            f"SELECT 1 FROM positions WHERE tracker_id=? AND seq=? AND rx_ts>=? AND {st} LIMIT 1", args))

    def touch_slow(self, tid: int, rx: int, state: str = "L") -> None:
        """SlowTrack- of FIFO-bericht ontvangen waarvan alle punten al bekend waren."""
        col = "last_fifo_rx" if state == "Q" else "last_slow_rx"
        self._x(f"UPDATE trackers SET last_rx=?, {col}=? WHERE id=?", (rx, rx, tid))

    def add_position(self, tid: int, p: dict[str, Any]) -> int:
        cols = ("tracker_id", "ts", "rx_ts", "seq", "state", "lat", "lon", "alt", "spd", "crs", "bat",
                "hdop", "fix_age", "mode", "suspect", "snr", "path_len", "raw", "power", "extra")
        vals = (tid,) + tuple(p.get(c) for c in cols[1:-1]) + (int(p.get("extra") or 0),)
        cur = self._x(f"INSERT INTO positions({','.join(cols)}) VALUES({','.join('?' * len(cols))})", vals)
        if p["state"] in HISTORY_STATES:
            self._slow_summary(tid, p)
            return cur.lastrowid
        # tracker-samenvatting bijwerken
        upd = {"last_rx": p["rx_ts"], "last_state": p["state"], "last_seq": p["seq"],
               "last_snr": p.get("snr"), "last_path_len": p.get("path_len")}
        if p.get("bat") is not None:
            upd["last_bat"] = p["bat"]
        if p.get("mode"):
            upd["last_mode"] = p["mode"]
        if p.get("power"):
            upd["last_power"] = p["power"]
        if p.get("lat") is not None and not p.get("suspect"):
            # Een ouder bericht dat pas later binnenkomt (herhaalpogingen, wachtrij)
            # komt wel in het spoor, maar is niet de laatste positie.
            cur_ts = (self._q("SELECT last_ts FROM trackers WHERE id=?", (tid,)) or [{}])[0].get("last_ts")
            if cur_ts is None or p["ts"] >= cur_ts:
                upd.update(last_ts=p["ts"], last_lat=p["lat"], last_lon=p["lon"],
                           last_spd=p.get("spd"), last_crs=p.get("crs"))
        sql = "UPDATE trackers SET " + ", ".join(f"{k}=?" for k in upd) + " WHERE id=?"
        self._x(sql, tuple(upd.values()) + (tid,))
        return cur.lastrowid

    def _slow_summary(self, tid: int, p: dict[str, Any]) -> None:
        """Samenvatting na een SlowTrack- (L) of FIFO-punt (Q). Zo'n punt is meestal ouder dan de
        live-positie: dan alleen ontvangst en verbinding bijwerken. Is het nieuwer, dan wordt het de
        laatste positie (met batterij, modus, voeding), maar last_state blijft de toestand van de
        gewone tracking (stilgevallen, SOS ...); alleen zonder eerdere toestand wordt het L of Q."""
        kind = "fifo" if p["state"] == "Q" else "slow"
        cur = self._q(f"SELECT last_ts, last_state, last_{kind}_ts AS kind_ts FROM trackers WHERE id=?", (tid,))[0]
        upd: dict[str, Any] = {"last_rx": p["rx_ts"], "last_snr": p.get("snr"), "last_path_len": p.get("path_len"),
                               f"last_{kind}_rx": p["rx_ts"]}
        if cur["kind_ts"] is None or p["ts"] > cur["kind_ts"]:
            upd[f"last_{kind}_ts"] = p["ts"]
        if p.get("lat") is not None and not p.get("suspect") and (cur["last_ts"] is None or p["ts"] > cur["last_ts"]):
            upd.update(last_ts=p["ts"], last_lat=p["lat"], last_lon=p["lon"], last_spd=p.get("spd"),
                       last_crs=p.get("crs"))
            if p.get("bat") is not None:
                upd["last_bat"] = p["bat"]
            if p.get("mode"):
                upd["last_mode"] = p["mode"]
            if p.get("power"):
                upd["last_power"] = p["power"]
            if cur["last_state"] is None:
                upd["last_state"] = p["state"]
        sql = "UPDATE trackers SET " + ", ".join(f"{k}=?" for k in upd) + " WHERE id=?"
        self._x(sql, tuple(upd.values()) + (tid,))

    def track(self, tid: int, since: int, limit: int = 5000) -> list[dict[str, Any]]:
        return self._q(
            "SELECT ts, lat, lon, alt, spd, crs, bat, state, suspect, seq, snr, path_len FROM positions "
            "WHERE tracker_id=? AND ts>=? AND lat IS NOT NULL ORDER BY ts LIMIT ?", (tid, since, limit))

    def purge_positions(self, tid: int, older_than: Optional[int] = None) -> int:
        """Posities van één tracker wissen; met older_than alleen die vóór dat tijdstip."""
        if older_than is None:
            return self._x("DELETE FROM positions WHERE tracker_id=?", (tid,)).rowcount
        return self._x("DELETE FROM positions WHERE tracker_id=? AND ts<?", (tid, older_than)).rowcount

    def position_stats(self, tid: int) -> dict[str, Any]:
        return self._q("SELECT COUNT(*) AS n, MIN(ts) AS first, MAX(ts) AS last FROM positions WHERE tracker_id=?",
                       (tid,))[0]

    def prune(self, older_than: int) -> int:
        n = self._x("DELETE FROM positions WHERE rx_ts<?", (older_than,)).rowcount
        self._x("DELETE FROM geofence_events WHERE ts<?", (older_than,))
        self._x("DELETE FROM audit WHERE ts<?", (older_than - 275 * 86400,))   # audit: ~1 jaar
        self._x("DELETE FROM alert_log WHERE ts<?", (older_than,))
        self._x("DELETE FROM unknown_msgs WHERE rx_ts<?", (older_than,))
        self._x("DELETE FROM stats_events WHERE ts<?", (older_than,))
        self._x("DELETE FROM message_paths WHERE rx_ts<?", (older_than,))
        return n

    def add_paths(self, rows: list[dict[str, Any]]) -> None:
        """Paden van trackerberichten (zie paths.py), in één transactie."""
        if not rows:
            return
        with self._lock:
            self._c.execute("BEGIN")
            try:
                self._c.executemany(
                    "INSERT INTO message_paths(tracker_id, seq, state, rx_ts, path, hash_size, hops, snr, rssi, radio, "
                    "source, channel_id) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                    [(r["tracker_id"], r["seq"], r["state"], int(r["rx_ts"]), ",".join(r["path"]), r.get("hash_size"),
                      int(r.get("hops") or len(r["path"])), r.get("snr"), r.get("rssi"), r.get("radio"), r["source"],
                      r.get("channel_id")) for r in rows])
                self._c.execute("COMMIT")
            except Exception:
                self._c.execute("ROLLBACK")
                raise

    # ---- statistieken ----------------------------------------------------------

    def stat(self, kind: str, tracker_id: Optional[int] = None, channel_id: Optional[int] = None,
             n: int = 1, ts: Optional[int] = None) -> None:
        """Eén teller in stats_events (één INSERT, geen lezen)."""
        if n > 0:
            self._x("INSERT INTO stats_events(ts, kind, tracker_id, channel_id, n) VALUES(?,?,?,?,?)",
                    (int(ts if ts is not None else time.time()), kind, tracker_id, channel_id, int(n)))

    def rows(self, sql: str, args: tuple = ()) -> list[tuple]:
        """Ruwe tuples (sneller dan dicts) voor de statistiek-aggregaten."""
        with self._lock:
            return self._c.execute(sql, args).fetchall()

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

    def add_geofence(self, g: dict[str, Any], owner: Optional[int] = None) -> int:
        cur = self._x(
            "INSERT INTO geofences(name, kind, geom, color, trackers, on_enter, on_exit, notify_pubkey, active, created, owner) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (g["name"], g["kind"], json.dumps(g["geom"]), g["color"], json.dumps(g["trackers"]),
             int(g["on_enter"]), int(g["on_exit"]), g["notify_pubkey"], int(g["active"]), int(time.time()), owner))
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
            "SELECT e.*, g.name AS geofence, g.owner AS owner, t.alias AS tracker FROM geofence_events e "
            "JOIN geofences g ON g.id=e.geofence_id JOIN trackers t ON t.id=e.tracker_id "
            "ORDER BY e.id DESC LIMIT ?", (limit,))

    # ---- instellingen en meldingsregels ------------------------------------------

    def settings(self) -> dict[str, Any]:
        return {r["key"]: json.loads(r["value"]) for r in self._q("SELECT key, value FROM settings")}

    def set_setting(self, key: str, value: Any) -> None:
        self._x("INSERT INTO settings(key, value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, json.dumps(value)))

    def alert_rules(self) -> list[dict[str, Any]]:
        rows = self._q("SELECT * FROM alert_rules ORDER BY name COLLATE NOCASE")
        for r in rows:
            for k in ("events", "trackers", "recipients"):
                r[k] = json.loads(r[k])
            r["channels"] = json.loads(r.get("channels") or "[]")
            r["active"] = bool(r["active"])
        return rows

    def save_alert_rule(self, rid: Optional[int], r: dict[str, Any], owner: Optional[int] = None) -> int:
        vals = (r["name"], int(r["active"]), json.dumps(r["events"]), json.dumps(r["trackers"]),
                json.dumps(r["recipients"]), int(r["cooldown_s"]), json.dumps(r.get("channels") or []))
        if rid is None:
            return self._x("INSERT INTO alert_rules(name, active, events, trackers, recipients, cooldown_s, channels, "
                           "created, owner) VALUES(?,?,?,?,?,?,?,?,?)", vals + (int(time.time()), owner)).lastrowid
        self._x("UPDATE alert_rules SET name=?, active=?, events=?, trackers=?, recipients=?, cooldown_s=?, channels=? "
                "WHERE id=?", vals + (rid,))
        return rid

    def delete_alert_rule(self, rid: int) -> None:
        self._x("DELETE FROM alert_rules WHERE id=?", (rid,))

    def add_alert_log(self, rule: str, tracker: str, event: str, recipient: str, text: str) -> int:
        return self._x("INSERT INTO alert_log(ts, rule, tracker, event, recipient, text, status) VALUES(?,?,?,?,?,?,?)",
                       (int(time.time()), rule, tracker, event, recipient, text, "wacht")).lastrowid

    def finish_alert_log(self, lid: int, status: str, attempts: int) -> None:
        self._x("UPDATE alert_log SET status=?, attempts=?, done_ts=? WHERE id=?", (status, attempts, int(time.time()), lid))

    def alert_log(self, limit: int = 200) -> list[dict[str, Any]]:
        return self._q("SELECT * FROM alert_log ORDER BY id DESC LIMIT ?", (limit,))

    # ---- logboek ---------------------------------------------------------------

    def events(self, tracker_ids: Optional[list[int]], states: Optional[list[str]], since: int, until: int,
               zones: bool, bat_low: bool, suspect: bool, limit: int,
               power: bool = False) -> list[dict[str, Any]]:
        """Gebeurtenissen voor het logboek: posities (per toestand), zone-
        meldingen, batterij onder 20 % (afgeleid) en verdachte posities."""
        out: list[dict[str, Any]] = []
        tf = ""
        args: list[Any] = [since, until]
        if tracker_ids is not None:
            if not tracker_ids:
                return []
            tf = f" AND p.tracker_id IN ({','.join('?' * len(tracker_ids))})"
            args += tracker_ids
        if states or bat_low or suspect or power:
            rows = self._q(
                "SELECT p.id, p.tracker_id, t.alias, t.color, t.icon, p.ts, p.rx_ts, p.state, p.lat, p.lon, p.spd, "
                "p.bat, p.mode, p.suspect, p.hdop, p.snr, p.path_len, p.power FROM positions p JOIN trackers t ON t.id=p.tracker_id "
                "WHERE p.rx_ts BETWEEN ? AND ?" + tf + " ORDER BY p.tracker_id, p.ts, p.id", tuple(args))
            prev_bat: dict[int, Optional[int]] = {}
            prev_pow: dict[int, Optional[str]] = {}
            for r in rows:
                pb = prev_bat.get(r["tracker_id"])
                pp = prev_pow.get(r["tracker_id"])
                if power and r["power"] and pp and r["power"] != pp:
                    out.append({**r, "type": "usb_on" if r["power"] == "u" else "usb_off"})
                if r["power"]:
                    prev_pow[r["tracker_id"]] = r["power"]
                if states and r["state"] in states:
                    out.append({**r, "type": r["state"]})
                if bat_low and r["bat"] is not None and r["bat"] < 20 and (pb is None or pb >= 20):
                    out.append({**r, "type": "bat_low"})
                if suspect and r["suspect"]:
                    out.append({**r, "type": "suspect"})
                if r["bat"] is not None:
                    prev_bat[r["tracker_id"]] = r["bat"]
        if zones:
            zf = tf.replace("p.tracker_id", "e.tracker_id")
            for r in self._q(
                    "SELECT e.id, e.tracker_id, t.alias, t.color, t.icon, e.ts, e.ts AS rx_ts, e.event, e.lat, e.lon, "
                    "g.name AS zone FROM geofence_events e JOIN trackers t ON t.id=e.tracker_id "
                    "JOIN geofences g ON g.id=e.geofence_id WHERE e.ts BETWEEN ? AND ?" + zf, tuple(args)):
                out.append({**r, "type": "zone_in" if r["event"] == "enter" else "zone_out"})
        out.sort(key=lambda e: (e["ts"], e.get("id") or 0), reverse=True)
        return out[:limit]

    # ---- groepen en gebruikers -------------------------------------------------

    def groups(self) -> list[dict[str, Any]]:
        rows = self._q("SELECT g.*, (SELECT COUNT(*) FROM user_groups ug WHERE ug.group_id=g.id) AS members "
                       "FROM groups g ORDER BY g.id")
        for r in rows:
            r["perms"] = json.loads(r["perms"])
            r["trackers"] = json.loads(r["trackers"])
            r["channels"] = {int(k): v for k, v in json.loads(r.get("channels") or "{}").items()}
            r["all_trackers"] = bool(r["all_trackers"])
        return rows

    def group(self, gid: int) -> Optional[dict[str, Any]]:
        return next((g for g in self.groups() if g["id"] == gid), None)

    def save_group(self, gid: Optional[int], g: dict[str, Any]) -> int:
        vals = (g["name"], g.get("description", ""), json.dumps(g["perms"]), int(g["all_trackers"]),
                json.dumps(g.get("trackers", [])), int(g.get("history_hours", 0)),
                json.dumps({str(k): v for k, v in (g.get("channels") or {}).items() if v in ("kaart", "sleutel")}))
        if gid is None:
            return self._x("INSERT INTO groups(name, description, perms, all_trackers, trackers, history_hours, channels, "
                           "created) VALUES(?,?,?,?,?,?,?,?)", vals + (int(time.time()),)).lastrowid
        self._x("UPDATE groups SET name=?, description=?, perms=?, all_trackers=?, trackers=?, history_hours=?, "
                "channels=? WHERE id=?", vals + (gid,))
        return gid

    def delete_group(self, gid: int) -> None:
        self._x("DELETE FROM groups WHERE id=?", (gid,))

    def users(self) -> list[dict[str, Any]]:
        rows = self._q("SELECT id, username, display_name, group_id, active, created, last_login FROM users "
                       "ORDER BY username COLLATE NOCASE")
        names = {g["id"]: g["name"] for g in self._q("SELECT id, name FROM groups")}
        member: dict[int, list[int]] = {}
        for r in self._q("SELECT user_id, group_id FROM user_groups ORDER BY group_id"):
            member.setdefault(r["user_id"], []).append(r["group_id"])
        for u in rows:
            u["group_ids"] = member.get(u["id"], [])
            u["group_name"] = ", ".join(names.get(g, "?") for g in u["group_ids"])
        return rows

    def user_group_ids(self, uid: int) -> list[int]:
        return [r["group_id"] for r in self._q("SELECT group_id FROM user_groups WHERE user_id=? ORDER BY group_id", (uid,))]

    def set_user_groups(self, uid: int, group_ids: list[int]) -> None:
        self._x("DELETE FROM user_groups WHERE user_id=?", (uid,))
        for g in dict.fromkeys(group_ids):
            self._x("INSERT INTO user_groups(user_id, group_id) VALUES(?,?)", (uid, g))
        if group_ids:   # oude kolom blijft de eerste groep (NOT NULL)
            self._x("UPDATE users SET group_id=? WHERE id=?", (group_ids[0], uid))

    # ---- kanalen -------------------------------------------------------------------

    def channels(self) -> list[dict[str, Any]]:
        rows = self._q("SELECT * FROM channels ORDER BY slot")
        for r in rows:
            r["require_sig"], r["active"] = bool(r["require_sig"]), bool(r["active"])
        return rows

    def channel(self, cid: int) -> Optional[dict[str, Any]]:
        return next((c for c in self.channels() if c["id"] == cid), None)

    def channel_by_slot(self, slot: int) -> Optional[dict[str, Any]]:
        return next((c for c in self.channels() if c["slot"] == slot and c["active"]), None)

    def save_channel(self, cid: Optional[int], c: dict[str, Any]) -> int:
        vals = (c["name"], c["secret"], int(c["slot"]), int(c["require_sig"]), int(c["active"]), c.get("tracker_group_id"),
                c.get("region", "be") or "")
        if cid is None:
            return self._x("INSERT INTO channels(name, secret, slot, require_sig, active, tracker_group_id, region, created) "
                           "VALUES(?,?,?,?,?,?,?,?)", vals + (int(time.time()),)).lastrowid
        self._x("UPDATE channels SET name=?, secret=?, slot=?, require_sig=?, active=?, tracker_group_id=?, region=? WHERE id=?",
                vals + (cid,))
        return cid

    def delete_channel(self, cid: int) -> None:
        self._x("DELETE FROM channels WHERE id=?", (cid,))
        self._x("UPDATE trackers SET channel_id=NULL WHERE channel_id=?", (cid,))
        for g in self.groups():
            if cid in g["channels"]:
                self.save_group(g["id"], {**g, "channels": {k: v for k, v in g["channels"].items() if k != cid}})
        for r in self.alert_rules():
            if cid in r["channels"]:
                self.save_alert_rule(r["id"], {**r, "channels": [c for c in r["channels"] if c != cid]})
        for sh in self.shares():
            if cid in sh["channels"]:
                self._x("UPDATE shares SET channels=? WHERE id=?", (json.dumps([c for c in sh["channels"] if c != cid]), sh["id"]))

    def set_authkey(self, tid: int, key: Optional[str]) -> None:
        self._x("UPDATE trackers SET authkey=? WHERE id=?", (key, tid))

    def set_tracker_channel(self, tid: int, cid: Optional[int]) -> bool:
        """Trackingkanaal van een tracker zetten. True als het veranderde."""
        r = self._q("SELECT channel_id FROM trackers WHERE id=?", (tid,))
        if not r or r[0]["channel_id"] == cid:
            return False
        self._x("UPDATE trackers SET channel_id=? WHERE id=?", (cid, tid))
        return True

    def channel_members(self) -> dict[int, set[int]]:
        """Trackers per kanaal (hun trackingkanaal)."""
        out: dict[int, set[int]] = {}
        for r in self._q("SELECT id, channel_id FROM trackers WHERE channel_id IS NOT NULL"):
            out.setdefault(r["channel_id"], set()).add(r["id"])
        return out

    def user_by_name(self, username: str) -> Optional[dict[str, Any]]:
        r = self._q("SELECT * FROM users WHERE username=?", (username,))
        return r[0] if r else None

    def user(self, uid: int) -> Optional[dict[str, Any]]:
        r = self._q("SELECT * FROM users WHERE id=?", (uid,))
        return r[0] if r else None

    def add_user(self, username: str, display: str, password_hash: str, group_id, active: bool = True) -> int:
        ids = list(group_id) if isinstance(group_id, (list, tuple)) else [group_id]
        uid = self._x("INSERT INTO users(username, display_name, password_hash, group_id, active, created) "
                      "VALUES(?,?,?,?,?,?)", (username, display, password_hash, ids[0], int(active),
                                              int(time.time()))).lastrowid
        self.set_user_groups(uid, ids)
        return uid

    def user_prefs(self, uid: int) -> dict[str, Any]:
        r = self._q("SELECT prefs FROM users WHERE id=?", (uid,))
        return json.loads(r[0]["prefs"]) if r else {}

    def set_user_prefs(self, uid: int, prefs: dict[str, Any]) -> None:
        self._x("UPDATE users SET prefs=? WHERE id=?", (json.dumps(prefs), uid))

    def update_user(self, uid: int, **f: Any) -> None:
        allowed = ("display_name", "group_id", "active", "password_hash", "last_login")
        sets = [(k, v) for k, v in f.items() if k in allowed and v is not None]
        if "password_hash" in f or f.get("active") is False or f.get("active") == 0:
            sets.append(("session_gen", None))
        if not sets:
            return
        parts = [f"{k}=?" if k != "session_gen" else "session_gen=session_gen+1" for k, _ in sets]
        args = tuple(int(v) if k == "active" else v for k, v in sets if k != "session_gen")
        self._x("UPDATE users SET " + ", ".join(parts) + " WHERE id=?", args + (uid,))

    def delete_user(self, uid: int) -> None:
        self._x("DELETE FROM user_groups WHERE user_id=?", (uid,))
        self._x("DELETE FROM users WHERE id=?", (uid,))

    def count_users(self) -> int:
        return self._q("SELECT COUNT(*) AS n FROM users")[0]["n"]

    # ---- deellinks -------------------------------------------------------------

    def shares(self) -> list[dict[str, Any]]:
        rows = self._q("SELECT * FROM shares ORDER BY id DESC")
        for r in rows:
            r["trackers"] = json.loads(r["trackers"])
            r["channels"] = json.loads(r.get("channels") or "[]")
        return rows

    def share_by_token(self, token: str) -> Optional[dict[str, Any]]:
        r = self._q("SELECT * FROM shares WHERE token=?", (token,))
        if not r:
            return None
        r[0]["trackers"] = json.loads(r[0]["trackers"])
        r[0]["channels"] = json.loads(r[0].get("channels") or "[]")
        return r[0]

    def add_share(self, token: str, name: str, trackers: list[int], hours: int, sidebar: bool,
                  expires: Optional[int], created_by: str, channels: Optional[list[int]] = None) -> int:
        return self._x("INSERT INTO shares(token, name, trackers, hours, sidebar, expires, created_by, created, channels) "
                       "VALUES(?,?,?,?,?,?,?,?,?)", (token, name, json.dumps(trackers), hours, int(sidebar), expires,
                                                     created_by, int(time.time()), json.dumps(channels or []))).lastrowid

    def delete_share(self, sid: int) -> None:
        self._x("DELETE FROM shares WHERE id=?", (sid,))

    def touch_share(self, sid: int) -> None:
        self._x("UPDATE shares SET last_used=? WHERE id=?", (int(time.time()), sid))

    # ---- auditlog ----------------------------------------------------------------

    def audit(self, who: str, action: str, detail: str = "") -> None:
        self._x("INSERT INTO audit(ts, who, action, detail) VALUES(?,?,?,?)",
                (int(time.time()), who, action, detail[:300]))

    def audit_log(self, limit: int = 200) -> list[dict[str, Any]]:
        return self._q("SELECT * FROM audit ORDER BY id DESC LIMIT ?", (limit,))

    # ---- onbekende berichten ------------------------------------------------

    def log_unknown(self, prefix: str, reason: str, text: str) -> None:
        self._x("INSERT INTO unknown_msgs(rx_ts, pubkey_prefix, reason, text) VALUES(?,?,?,?)",
                (int(time.time()), prefix, reason, text[:200]))

    def unknown(self, limit: int = 50) -> list[dict[str, Any]]:
        return self._q("SELECT * FROM unknown_msgs ORDER BY id DESC LIMIT ?", (limit,))
