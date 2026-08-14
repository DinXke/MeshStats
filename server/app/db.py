"""SQLite layer: schema, helpers and ingest logic.

Deliberately plain sqlite3 with a module-level connection and a mutex instead of
an ORM: the workload is a handful of small writes per minute plus page reads, so
an ORM would only add a dependency and a migration story we do not need. The
schema is applied with CREATE TABLE IF NOT EXISTS on every connect, which
doubles as the migration mechanism for additive changes.
"""
import re
import sqlite3
import threading
from datetime import datetime, timedelta, timezone

from . import config

_lock = threading.Lock()
_conn: sqlite3.Connection | None = None

SCHEMA = """
CREATE TABLE IF NOT EXISTS repeaters(
  id INTEGER PRIMARY KEY,
  slug TEXT UNIQUE NOT NULL,
  pubkey_prefix TEXT UNIQUE NOT NULL,
  name TEXT NOT NULL,
  is_public INTEGER NOT NULL DEFAULT 1,
  sort_order INTEGER NOT NULL DEFAULT 0,
  last_seen TEXT,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS latest(
  repeater_id INTEGER NOT NULL REFERENCES repeaters(id) ON DELETE CASCADE,
  metric TEXT NOT NULL,
  ts TEXT NOT NULL,
  value REAL,
  value_str TEXT,
  PRIMARY KEY(repeater_id, metric)
);
CREATE TABLE IF NOT EXISTS samples(
  repeater_id INTEGER NOT NULL,
  metric TEXT NOT NULL,
  ts TEXT NOT NULL,
  value REAL NOT NULL,
  PRIMARY KEY(repeater_id, metric, ts)
) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS neighbors(
  repeater_id INTEGER NOT NULL REFERENCES repeaters(id) ON DELETE CASCADE,
  prefix TEXT NOT NULL,
  name TEXT,
  snr REAL,
  last_seen TEXT NOT NULL,
  PRIMARY KEY(repeater_id, prefix)
);
CREATE TABLE IF NOT EXISTS tokens(
  id INTEGER PRIMARY KEY,
  name TEXT NOT NULL,
  token_hash TEXT UNIQUE NOT NULL,
  created_at TEXT NOT NULL,
  last_used TEXT,
  revoked INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS admins(
  id INTEGER PRIMARY KEY,
  username TEXT UNIQUE NOT NULL,
  pw_hash TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS settings(
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS contacts(
  prefix TEXT PRIMARY KEY,
  prefix6 TEXT NOT NULL,
  name TEXT,
  lat REAL,
  lon REAL,
  node_type TEXT,
  updated TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_contacts_p6 ON contacts(prefix6);
CREATE TABLE IF NOT EXISTS repeater_cli(
  repeater_id INTEGER NOT NULL REFERENCES repeaters(id) ON DELETE CASCADE,
  param TEXT NOT NULL,
  value TEXT,
  updated TEXT NOT NULL,
  PRIMARY KEY(repeater_id, param)
);
CREATE TABLE IF NOT EXISTS packets(
  id INTEGER PRIMARY KEY,
  ts TEXT NOT NULL,
  observer TEXT NOT NULL,
  snr REAL,
  rssi REAL,
  len INTEGER,
  route TEXT,
  payload_type INTEGER,
  payload_name TEXT,
  path_len INTEGER,
  sender TEXT,
  phash TEXT
);
CREATE INDEX IF NOT EXISTS idx_packets_ts ON packets(ts);
-- Duplicate lookup and retention sweeps both scan on (observer, hash, time).
CREATE INDEX IF NOT EXISTS idx_packets_dup ON packets(observer, phash, ts);
"""

# Additive column migrations. CREATE TABLE IF NOT EXISTS covers new tables, but
# SQLite has no ADD COLUMN IF NOT EXISTS, so existing tables need the explicit
# check below. Dropping a live database is not an option here.
COLUMN_MIGRATIONS = [
    # Which node published this repeater's last statistics, and when. A node
    # relaying figures about a repeater it monitors is legitimate, so the two
    # identities differ on purpose -- see mqtt_ingest for the reasoning.
    ("repeaters", "source_prefix", "TEXT"),
    ("repeaters", "source_seen", "TEXT"),
]


def _migrate(conn: sqlite3.Connection) -> None:
    for table, column, decl in COLUMN_MIGRATIONS:
        names = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}
        if column not in names:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")


# A flooded packet is repeated by every node in range, so the same observer
# hears the same payload several times within seconds. Collapsing those keeps
# the table and the live map readable without losing distinct traffic.
PACKET_DUP_WINDOW_S = 60


def utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def get_conn() -> sqlite3.Connection:
    global _conn
    if _conn is None:
        _conn = sqlite3.connect(config.DB_PATH, check_same_thread=False)
        _conn.row_factory = sqlite3.Row
        _conn.execute("PRAGMA journal_mode=WAL")
        _conn.execute("PRAGMA foreign_keys=ON")
        _conn.executescript(SCHEMA)
        _migrate(_conn)
        _conn.commit()
    return _conn


def q(sql: str, params=()) -> list[sqlite3.Row]:
    with _lock:
        return get_conn().execute(sql, params).fetchall()


def qone(sql: str, params=()) -> sqlite3.Row | None:
    rows = q(sql, params)
    return rows[0] if rows else None


def execute(sql: str, params=()) -> int:
    with _lock:
        conn = get_conn()
        cur = conn.execute(sql, params)
        conn.commit()
        return cur.lastrowid


def get_setting(key: str, default: str | None = None) -> str | None:
    row = qone("SELECT value FROM settings WHERE key=?", (key,))
    return row["value"] if row else default


def setting_int(key: str, default: int) -> int:
    try:
        return int(get_setting(key, str(default)))
    except (TypeError, ValueError):
        return default


def set_setting(key: str, value: str) -> None:
    execute(
        "INSERT INTO settings(key, value) VALUES(?,?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, value),
    )


def upsert_contacts(contacts: list[dict]) -> int:
    """Refresh contact positions (advert data); returns how many were stored."""
    now = utcnow()
    n = 0
    with _lock:
        conn = get_conn()
        for c in contacts:
            prefix = str(c.get("prefix", "")).lower().strip()
            lat, lon = c.get("lat"), c.get("lon")
            if len(prefix) < 6 or not isinstance(lat, (int, float)) or not isinstance(lon, (int, float)):
                continue
            conn.execute(
                "INSERT INTO contacts(prefix, prefix6, name, lat, lon, node_type, updated) "
                "VALUES(?,?,?,?,?,?,?) ON CONFLICT(prefix) DO UPDATE SET "
                "name=COALESCE(excluded.name, name), lat=excluded.lat, lon=excluded.lon, "
                "node_type=COALESCE(excluded.node_type, node_type), updated=excluded.updated",
                (prefix, prefix[:6], c.get("name"), float(lat), float(lon), c.get("type"), now),
            )
            n += 1
        conn.commit()
    return n


def contact_location(prefix6: str):
    """Position of a contact, or None. Adverts may register a node by name
    before it ever reports coordinates, so rows without a position exist and
    must not be handed to callers that are about to plot them."""
    return qone(
        "SELECT * FROM contacts WHERE prefix6=? AND lat IS NOT NULL AND lon IS NOT NULL",
        (prefix6.lower(),),
    )


def upsert_advert(pubkey: str, name: str | None = None, lat: float | None = None,
                  lon: float | None = None, node_type: str | None = None) -> None:
    """Record the identity carried by an advert in the shared contacts table.

    Adverts arrive far more often than they change, and a node may advertise its
    name without a position (or the other way round), so every field is only
    overwritten when the advert actually carries it -- otherwise a nameless
    advert would erase a known name.
    """
    pk = (pubkey or "").lower().strip()
    if len(pk) < 6:
        return
    prefix6 = pk[:6]
    # Contacts pushed by Home Assistant use a shorter pubkey prefix than the
    # 32-byte key in an advert. Reuse the existing row's key so both sources
    # keep converging on one row per node instead of two that shadow each other.
    row = qone("SELECT prefix FROM contacts WHERE prefix6=?", (prefix6,))
    prefix = row["prefix"] if row else pk[:12]
    if lat is None or lon is None:
        lat = lon = None
    execute(
        "INSERT INTO contacts(prefix, prefix6, name, lat, lon, node_type, updated) "
        "VALUES(?,?,?,?,?,?,?) ON CONFLICT(prefix) DO UPDATE SET "
        "name=COALESCE(excluded.name, name), "
        "lat=COALESCE(excluded.lat, lat), lon=COALESCE(excluded.lon, lon), "
        "node_type=COALESCE(excluded.node_type, node_type), updated=excluded.updated",
        (prefix, prefix6, name, lat, lon, node_type, utcnow()),
    )


def insert_packet(observer: str, pkt: dict, snr=None, rssi=None,
                  length: int | None = None, ts: str | None = None) -> int | None:
    """Store one packet reception. Returns the row id, or None if skipped.

    ``pkt`` is the dict from packets.decode(). An advert also refreshes the
    contacts table, which is what later lets the live map place a packet.
    """
    observer = str(observer or "").lower().strip()[:16]
    if not observer:
        return None
    ts = ts or utcnow()
    phash = pkt.get("hash")

    if phash:
        cutoff = (datetime.now(timezone.utc)
                  - timedelta(seconds=PACKET_DUP_WINDOW_S)).strftime("%Y-%m-%dT%H:%M:%SZ")
        if qone("SELECT 1 FROM packets WHERE observer=? AND phash=? AND ts>=? LIMIT 1",
                (observer, phash, cutoff)):
            return None

    if pkt.get("pubkey"):
        upsert_advert(pkt["pubkey"], pkt.get("name"), pkt.get("lat"), pkt.get("lon"),
                      pkt.get("node_type"))

    return execute(
        "INSERT INTO packets(ts, observer, snr, rssi, len, route, payload_type, "
        "payload_name, path_len, sender, phash) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
        (ts, observer,
         float(snr) if isinstance(snr, (int, float)) else None,
         float(rssi) if isinstance(rssi, (int, float)) else None,
         int(length) if isinstance(length, int) else pkt.get("len"),
         pkt.get("route_name"), pkt.get("payload_type"), pkt.get("payload_name"),
         pkt.get("path_len"), pkt.get("sender"), phash),
    )


def recent_packets(since_id: int = 0, limit: int = 200) -> list[sqlite3.Row]:
    """Packets newer than ``since_id``, oldest first, with the sender's name and
    position joined in so the caller can plot them without a second query."""
    # GROUP BY p.id keeps one row per packet: contacts is keyed on the full
    # pubkey prefix, and two sources (adverts, Home Assistant) can register the
    # same node under prefixes of different length, which would otherwise
    # multiply every packet by the number of matching contact rows.
    return q(
        "SELECT p.*, c.name AS sender_name, c.lat AS sender_lat, c.lon AS sender_lon, "
        "o.name AS observer_name, o.lat AS observer_lat, o.lon AS observer_lon "
        "FROM packets p "
        "LEFT JOIN contacts c ON c.prefix6 = p.sender "
        "LEFT JOIN contacts o ON o.prefix6 = substr(p.observer, 1, 6) "
        "WHERE p.id > ? GROUP BY p.id ORDER BY p.id LIMIT ?",
        (since_id, limit),
    )


def last_packet_id() -> int:
    row = qone("SELECT MAX(id) AS id FROM packets")
    return (row["id"] or 0) if row else 0


def located_nodes() -> list[sqlite3.Row]:
    """Every node we know a position for; the base layer of the live map."""
    return q(
        "SELECT prefix6, name, lat, lon, node_type FROM contacts "
        "WHERE lat IS NOT NULL AND lon IS NOT NULL GROUP BY prefix6"
    )


# 'cmd:' prefix = literal CLI command (not prefixed with 'get ')
DEFAULT_CLI_PARAMS = ("name,role,radio,freq,tx,af,repeat,advert.interval,"
                      "flood.advert.interval,flood.max,allow.read.only,"
                      "rxdelay,txdelay,lat,lon,cmd:region")


def request_settings(prefix: str, params: list[str]) -> None:
    """Queue a CLI settings request for the Home Assistant integration."""
    import json
    try:
        d = json.loads(get_setting("settings_requests", "{}"))
    except ValueError:
        d = {}
    d[prefix] = {"ts": utcnow(), "params": params}
    set_setting("settings_requests", json.dumps(d))


def pop_settings_requests() -> list[dict]:
    import json
    try:
        d = json.loads(get_setting("settings_requests", "{}"))
    except ValueError:
        d = {}
    if d:
        set_setting("settings_requests", "{}")
    return [{"prefix": p, "params": v.get("params", [])} for p, v in d.items()]


def upsert_cli_settings(repeater_id: int, values: dict) -> None:
    now = utcnow()
    # Prune against the configured parameter list rather than this push: a
    # partial re-read must not wipe rows it simply did not ask about.
    configured = {p.strip() for p in
                  (get_setting("cli_params", DEFAULT_CLI_PARAMS) or "").replace(";", ",").split(",")
                  if p.strip()}
    keep = [str(p)[:64] for p in ({str(k)[:64] for k in values} | configured)]
    with _lock:
        conn = get_conn()
        placeholders = ",".join("?" for _ in keep) or "''"
        conn.execute(
            f"DELETE FROM repeater_cli WHERE repeater_id=? AND param NOT IN ({placeholders})",
            [repeater_id, *keep],
        )
        for param, value in values.items():
            conn.execute(
                "INSERT INTO repeater_cli(repeater_id, param, value, updated) VALUES(?,?,?,?) "
                "ON CONFLICT(repeater_id, param) DO UPDATE SET "
                "value=excluded.value, updated=excluded.updated",
                (repeater_id, str(param)[:64],
                 None if value is None else str(value)[:4000], now),
            )
        conn.commit()


def cli_settings_for(repeater_id: int) -> list:
    return q("SELECT * FROM repeater_cli WHERE repeater_id=? ORDER BY param", (repeater_id,))


def request_refresh(prefix: str) -> None:
    """Queue a manual status request for the Home Assistant integration."""
    import json
    d = {}
    try:
        d = json.loads(get_setting("refresh_requests", "{}"))
    except ValueError:
        pass
    d[prefix] = utcnow()
    set_setting("refresh_requests", json.dumps(d))


def pop_refresh_requests() -> list[str]:
    """Fetch pending requests and clear them (delivered to Home Assistant)."""
    import json
    try:
        d = json.loads(get_setting("refresh_requests", "{}"))
    except ValueError:
        d = {}
    if d:
        set_setting("refresh_requests", "{}")
    return list(d)


def slugify(name: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return s or "repeater"


# Below this many hex characters two different keys could collide by chance, so
# we refuse to treat one as a shortening of the other.
MIN_PREFIX_MATCH = 8


def _find_by_prefix(pubkey_prefix: str) -> sqlite3.Row | None:
    """Find a repeater by public key, tolerating differing prefix lengths.

    Sources disagree on how much of the key they send: Home Assistant reports
    5 bytes, a node's own firmware 6. Matching on the string alone registered
    one node twice and split its history down the middle. For this to be the
    same node, the shorter key must be a prefix of the longer one.
    """
    row = qone("SELECT * FROM repeaters WHERE pubkey_prefix=?", (pubkey_prefix,))
    if row or len(pubkey_prefix) < MIN_PREFIX_MATCH:
        return row

    # Stored key is shorter: 'aabbccddee' matches an incoming 'aabbccddeeff'.
    row = qone(
        "SELECT * FROM repeaters WHERE ?1 LIKE pubkey_prefix || '%'"
        " AND length(pubkey_prefix) >= ?2"
        " ORDER BY length(pubkey_prefix) DESC LIMIT 1",
        (pubkey_prefix, MIN_PREFIX_MATCH),
    )
    if row:
        # Keep the longest key seen; it is the least ambiguous.
        if len(pubkey_prefix) > len(row["pubkey_prefix"]):
            execute("UPDATE repeaters SET pubkey_prefix=? WHERE id=?",
                    (pubkey_prefix, row["id"]))
            row = qone("SELECT * FROM repeaters WHERE id=?", (row["id"],))
        return row

    # Stored key is longer: an incoming 'aabbccddee' matches 'aabbccddeeff'.
    return qone(
        "SELECT * FROM repeaters WHERE pubkey_prefix LIKE ?1 || '%'"
        " ORDER BY length(pubkey_prefix) DESC LIMIT 1",
        (pubkey_prefix,),
    )


def get_or_create_repeater(pubkey_prefix: str, name: str | None) -> sqlite3.Row:
    row = _find_by_prefix(pubkey_prefix)
    if row:
        # Adopt the name whenever Home Assistant sends a new one
        if name and name != row["name"]:
            execute("UPDATE repeaters SET name=? WHERE id=?", (name, row["id"]))
            row = qone("SELECT * FROM repeaters WHERE id=?", (row["id"],))
        return row
    base = slugify(name or pubkey_prefix)
    slug = base
    i = 2
    while qone("SELECT 1 FROM repeaters WHERE slug=?", (slug,)):
        slug = f"{base}-{i}"
        i += 1
    execute(
        "INSERT INTO repeaters(slug, pubkey_prefix, name, created_at) VALUES(?,?,?,?)",
        (slug, pubkey_prefix, name or pubkey_prefix, utcnow()),
    )
    return qone("SELECT * FROM repeaters WHERE pubkey_prefix=?", (pubkey_prefix,))


def record_source(repeater_id: int, source: str) -> None:
    """Note who delivered this repeater's statistics.

    Kept because the deliverer and the subject need not be the same node: a node
    may report on repeaters it monitors. Recording the route makes that visible
    instead of invisible, so a repeater suddenly arriving via an unexpected node
    is something the admin page can show rather than something nobody notices.
    """
    execute("UPDATE repeaters SET source_prefix=?, source_seen=? WHERE id=?",
            (str(source or "")[:32] or None, utcnow(), repeater_id))


def ingest(repeater_id: int, ts: str, metrics: dict, neighbors: list | None,
           force: bool = False):
    """Store a snapshot.

    Numeric values only enter the history when they changed, or when the last
    stored point is older than the heartbeat interval -- otherwise a stable
    metric would fill the table with identical rows, while its chart would still
    need points to keep running. force=True (manual status update) always writes.
    """
    # Read the setting before taking the lock; get_setting takes it itself
    heartbeat = timedelta(minutes=setting_int("heartbeat_min", config.HEARTBEAT_MIN))
    with _lock:
        conn = get_conn()
        for name, raw in metrics.items():
            value = value_str = None
            if isinstance(raw, bool):
                value = 1.0 if raw else 0.0
            elif isinstance(raw, (int, float)):
                value = float(raw)
            else:
                try:
                    value = float(raw)
                except (TypeError, ValueError):
                    value_str = None if raw is None else str(raw)[:255]
            prev = conn.execute(
                "SELECT ts, value, value_str FROM latest WHERE repeater_id=? AND metric=?",
                (repeater_id, name),
            ).fetchone()
            conn.execute(
                "INSERT INTO latest(repeater_id, metric, ts, value, value_str) VALUES(?,?,?,?,?) "
                "ON CONFLICT(repeater_id, metric) DO UPDATE SET ts=excluded.ts, "
                "value=excluded.value, value_str=excluded.value_str",
                (repeater_id, name, ts, value, value_str),
            )
            if value is None:
                continue
            store = True
            if not force and prev is not None and prev["value"] == value:
                # Unchanged: only a heartbeat point, and judged on the last
                # STORED sample rather than the last ingest.
                last_sample = conn.execute(
                    "SELECT MAX(ts) AS ts FROM samples WHERE repeater_id=? AND metric=?",
                    (repeater_id, name),
                ).fetchone()
                try:
                    prev_dt = datetime.strptime(last_sample["ts"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
                    now_dt = datetime.strptime(ts, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
                    store = (now_dt - prev_dt) >= heartbeat
                except (TypeError, ValueError):
                    store = True
            if store:
                conn.execute(
                    "INSERT OR REPLACE INTO samples(repeater_id, metric, ts, value) VALUES(?,?,?,?)",
                    (repeater_id, name, ts, value),
                )
        if neighbors is not None:
            for nb in neighbors:
                prefix = str(nb.get("prefix", "")).lower()
                if not prefix:
                    continue
                # 'seen_min' = minutes since last heard -> absolute timestamp
                last = ts
                seen_min = nb.get("seen_min")
                if isinstance(seen_min, (int, float)):
                    try:
                        ts_dt = datetime.strptime(ts, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
                        last = (ts_dt - timedelta(minutes=seen_min)).strftime("%Y-%m-%dT%H:%M:%SZ")
                    except ValueError:
                        pass
                snr = nb.get("snr")
                prev_nb = conn.execute(
                    "SELECT snr FROM neighbors WHERE repeater_id=? AND prefix=?",
                    (repeater_id, prefix),
                ).fetchone()
                conn.execute(
                    "INSERT INTO neighbors(repeater_id, prefix, name, snr, last_seen) VALUES(?,?,?,?,?) "
                    "ON CONFLICT(repeater_id, prefix) DO UPDATE SET "
                    "name=COALESCE(excluded.name, name), snr=COALESCE(excluded.snr, snr), "
                    "last_seen=excluded.last_seen",
                    (repeater_id, prefix, nb.get("name"), snr, last),
                )
                # Per-link history: SNR trend of one individual neighbour link
                if isinstance(snr, (int, float)):
                    store_link = force or prev_nb is None or prev_nb["snr"] != snr
                    if not store_link:
                        last_sample = conn.execute(
                            "SELECT MAX(ts) AS ts FROM samples WHERE repeater_id=? AND metric=?",
                            (repeater_id, f"neighbor_{prefix}"),
                        ).fetchone()
                        try:
                            prev_dt = datetime.strptime(last_sample["ts"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
                            now_dt = datetime.strptime(ts, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
                            store_link = (now_dt - prev_dt) >= heartbeat
                        except (TypeError, ValueError):
                            store_link = True
                    if store_link:
                        conn.execute(
                            "INSERT OR REPLACE INTO samples(repeater_id, metric, ts, value) VALUES(?,?,?,?)",
                            (repeater_id, f"neighbor_{prefix}", ts, float(snr)),
                        )
        conn.execute("UPDATE repeaters SET last_seen=? WHERE id=?", (ts, repeater_id))
        conn.commit()


def history(repeater_id: int, metric: str, hours: int) -> list[tuple[str, float]]:
    since = (datetime.now(timezone.utc) - timedelta(hours=hours)).strftime("%Y-%m-%dT%H:%M:%SZ")
    if hours <= 48:
        rows = q(
            "SELECT ts, value FROM samples WHERE repeater_id=? AND metric=? AND ts>=? ORDER BY ts",
            (repeater_id, metric, since),
        )
        return [(r["ts"], r["value"]) for r in rows]
    # Longer windows: average per hour to keep the response small
    rows = q(
        "SELECT substr(ts,1,13)||':00:00Z' AS bucket, AVG(value) AS value "
        "FROM samples WHERE repeater_id=? AND metric=? AND ts>=? GROUP BY bucket ORDER BY bucket",
        (repeater_id, metric, since),
    )
    return [(r["bucket"], round(r["value"], 3)) for r in rows]


def computed_utilization(repeater_id: int, total_metric: str, window_min: int = 90) -> float | None:
    """Utilisation (%) derived from the airtime totals: delta airtime / delta time.

    Computed here instead of read from the node because the meshcore-side figure
    resets on every Home Assistant restart.
    """
    since = (datetime.now(timezone.utc) - timedelta(minutes=window_min)).strftime("%Y-%m-%dT%H:%M:%SZ")
    rows = q(
        "SELECT ts, value FROM samples WHERE repeater_id=? AND metric=? AND ts>=? ORDER BY ts",
        (repeater_id, total_metric, since),
    )
    if len(rows) < 2:
        return None
    try:
        t0 = datetime.strptime(rows[0]["ts"], "%Y-%m-%dT%H:%M:%SZ")
        t1 = datetime.strptime(rows[-1]["ts"], "%Y-%m-%dT%H:%M:%SZ")
    except ValueError:
        return None
    dt_min = (t1 - t0).total_seconds() / 60
    dv_min = rows[-1]["value"] - rows[0]["value"]  # airtime is in minutes
    if dt_min < 10 or dv_min < 0:  # window too short, or counter reset
        return None
    return round(dv_min / dt_min * 100, 2)


def latest_for(repeater_id: int) -> dict[str, sqlite3.Row]:
    return {r["metric"]: r for r in q("SELECT * FROM latest WHERE repeater_id=?", (repeater_id,))}


def prune():
    retention = setting_int("retention_days", config.RETENTION_DAYS)
    cutoff = (datetime.now(timezone.utc) - timedelta(days=retention)).strftime("%Y-%m-%dT%H:%M:%SZ")
    execute("DELETE FROM samples WHERE ts<?", (cutoff,))
    # Neighbours unheard for 7 days drop off the list
    nb_cutoff = (datetime.now(timezone.utc) - timedelta(days=7)).strftime("%Y-%m-%dT%H:%M:%SZ")
    execute("DELETE FROM neighbors WHERE last_seen<?", (nb_cutoff,))
    # Packets arrive orders of magnitude faster than metric samples and are only
    # interesting while recent, hence their own much shorter retention.
    pkt_days = setting_int("packet_retention_days", config.PACKET_RETENTION_DAYS)
    pkt_cutoff = (datetime.now(timezone.utc) - timedelta(days=pkt_days)).strftime("%Y-%m-%dT%H:%M:%SZ")
    execute("DELETE FROM packets WHERE ts<?", (pkt_cutoff,))
