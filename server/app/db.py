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

from . import config, countries, packets, tsdb

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
  updated TEXT NOT NULL,
  country TEXT
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
  phash TEXT,
  path TEXT,
  raw TEXT
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
    # The hop hashes of the packet's path, comma-separated. Denormalised out of
    # ``raw`` on purpose: the packet detail view resolves every hop against the
    # contacts table, and re-decoding frames for that is work the ingest path has
    # already done once.
    ("packets", "path", "TEXT"),
    # The frame exactly as it came off the radio, hex. It is the only complete
    # record of a packet -- everything else in this table is a lossy summary --
    # and it is what lets a later reader re-parse a packet the decoder of the day
    # got wrong. It roughly doubles the size of a packet row, which is affordable
    # only because packets have their own short retention (PACKET_RETENTION_DAYS,
    # 7 by default) rather than the 180 days that metric samples get.
    ("packets", "raw", "TEXT"),
    # ISO 3166-1 alpha-2 for the contact's position, NULL when we cannot tell.
    # Written once, when a position becomes known -- see set_country.
    ("contacts", "country", "TEXT"),
    # Whether the sender restricted this packet to a region: 'unscoped', 'scoped'
    # or 'share'. See the Scoping section in packets.py for what each means and
    # why the region itself is not one of them. Stored rather than derived on
    # read because the packet list shows it per row, and re-decoding the frame
    # for a column is work the ingest path has already done once -- the same
    # reasoning as ``path`` above.
    ("packets", "scope", "TEXT"),
    # The two transport codes, comma-separated, exactly as they were on the wire.
    # NULL on an unscoped packet, where the wire has no room for them at all.
    ("packets", "scope_codes", "TEXT"),
    # The 1-byte source and destination hashes of REQ/RESPONSE/TXT_MSG/PATH
    # payloads (dest only for ANON_REQ), two hex characters each. One byte names
    # nobody by itself, but resolved against the contacts table it usually
    # answers "who sent this" on a mesh of realistic size -- the same resolution,
    # with the same honesty about ambiguity, that path hops already get.
    # Empty string means "decoded, and this packet type has none": without that
    # sentinel the backfill would re-decode every ACK and advert on every start,
    # looking for a hash that was never there.
    ("packets", "src_hash", "TEXT"),
    ("packets", "dest_hash", "TEXT"),
]


def _migrate(conn: sqlite3.Connection) -> None:
    for table, column, decl in COLUMN_MIGRATIONS:
        names = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}
        if column not in names:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")


def _backfill_from_raw(conn: sqlite3.Connection) -> None:
    """Fill decoder-derived columns on packets stored before those columns existed.

    The frame is kept in ``raw``, so this is a re-read of what was already there
    rather than an invention: the same bytes through the same decoder that new
    packets go through. Without it a new column would stay empty until the whole
    table had rolled over, and the list would show a week of dashes on rows whose
    answer is sitting right next to them.

    Self-limiting. Every row it touches gets a non-NULL src_hash (the empty
    string when the packet type carries none), so the second start finds nothing
    to do -- cheap enough at the once-per-process this runs. Rows older than the
    ``raw`` column keep NULLs forever, which is the honest answer for a packet
    whose bytes nobody kept.
    """
    rows = conn.execute(
        "SELECT id, raw FROM packets "
        "WHERE (scope IS NULL OR src_hash IS NULL) AND raw IS NOT NULL"
    ).fetchall()
    for row in rows:
        try:
            pkt = packets.decode(bytes.fromhex(row["raw"]))
        except ValueError:
            continue        # stored hex that is not hex: nothing to re-read
        conn.execute(
            "UPDATE packets SET scope=COALESCE(?, scope), "
            "scope_codes=COALESCE(?, scope_codes), src_hash=?, dest_hash=? "
            "WHERE id=?",
            (pkt.get("scope"), _scope_codes(pkt),
             pkt.get("src_hash", ""), pkt.get("dest_hash", ""), row["id"]),
        )


def _scope_codes(pkt: dict) -> str | None:
    """The transport codes as they go into the ``scope_codes`` column."""
    codes = pkt.get("transport_codes")
    return ",".join(str(int(c)) for c in codes) if codes else None


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
        _backfill_from_raw(_conn)
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


def set_country(prefix6: str, lat, lon) -> None:
    """Work out which country a node sits in and store it on every row it owns.

    Keyed on prefix6, and applied with a single UPDATE across all rows sharing
    it, because one node can hold more than one contact row: Home Assistant sends
    five key bytes where a node's own firmware sends six, so the same node
    arrives under keys of different length. That is the trap _find_by_prefix
    exists for on the repeaters table. Matching on the literal key here would
    give one node two countries, or none.

    Called only when a position is written that differs from the stored one, so
    an ordinary advert -- which repeats a position we already have -- costs
    nothing. A node that never moves is classified exactly once.
    """
    if lat is None or lon is None or not countries.available():
        return
    execute("UPDATE contacts SET country=? WHERE prefix6=?",
            (countries.lookup(lat, lon), prefix6.lower()))


def _position_changed(prev, lat, lon) -> bool:
    """True when this position is new information about where a node is."""
    return prev is None or prev["lat"] != lat or prev["lon"] != lon


def upsert_contacts(contacts: list[dict]) -> int:
    """Refresh contact positions (advert data); returns how many were stored."""
    now = utcnow()
    n = 0
    moved = []
    with _lock:
        conn = get_conn()
        for c in contacts:
            prefix = str(c.get("prefix", "")).lower().strip()
            lat, lon = c.get("lat"), c.get("lon")
            if len(prefix) < 6 or not isinstance(lat, (int, float)) or not isinstance(lon, (int, float)):
                continue
            lat, lon = float(lat), float(lon)
            prev = conn.execute(
                "SELECT lat, lon FROM contacts WHERE prefix6=? LIMIT 1", (prefix[:6],)
            ).fetchone()
            conn.execute(
                "INSERT INTO contacts(prefix, prefix6, name, lat, lon, node_type, updated) "
                "VALUES(?,?,?,?,?,?,?) ON CONFLICT(prefix) DO UPDATE SET "
                "name=COALESCE(excluded.name, name), lat=excluded.lat, lon=excluded.lon, "
                "node_type=COALESCE(excluded.node_type, node_type), updated=excluded.updated",
                (prefix, prefix[:6], c.get("name"), lat, lon, c.get("type"), now),
            )
            n += 1
            if _position_changed(prev, lat, lon):
                moved.append((prefix[:6], lat, lon))
        conn.commit()
    # Outside the lock: set_country takes it itself, and threading.Lock is not
    # reentrant.
    for prefix6, lat, lon in moved:
        set_country(prefix6, lat, lon)
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
    row = qone("SELECT prefix, lat, lon FROM contacts WHERE prefix6=?", (prefix6,))
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
    # A positionless advert keeps whatever position we already had (COALESCE
    # above), so the effective position -- not the advert's own -- decides
    # whether anything needs classifying.
    now_lat = lat if lat is not None else (row["lat"] if row else None)
    now_lon = lon if lon is not None else (row["lon"] if row else None)
    if _position_changed(row, now_lat, now_lon):
        set_country(prefix6, now_lat, now_lon)


# A MeshCore frame is at most 255 bytes, so 510 hex characters plus slack is
# already generous; the cap only exists so a nonsense payload cannot store a
# megabyte per row.
MAX_RAW_HEX_STORED = 600


def insert_packet(observer: str, pkt: dict, snr=None, rssi=None,
                  length: int | None = None, ts: str | None = None,
                  raw: str | None = None) -> int | None:
    """Store one packet reception. Returns the row id, or None if skipped.

    ``pkt`` is the dict from packets.decode(); ``raw`` the hex frame it was
    decoded from. An advert also refreshes the contacts table, which is what
    later lets the live map place a packet.
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

    raw_hex = str(raw or "").strip().lower()[:MAX_RAW_HEX_STORED] or None
    return execute(
        "INSERT INTO packets(ts, observer, snr, rssi, len, route, payload_type, "
        "payload_name, path_len, sender, phash, path, raw, scope, scope_codes, "
        "src_hash, dest_hash) "
        "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (ts, observer,
         float(snr) if isinstance(snr, (int, float)) else None,
         float(rssi) if isinstance(rssi, (int, float)) else None,
         int(length) if isinstance(length, int) else pkt.get("len"),
         pkt.get("route_name"), pkt.get("payload_type"), pkt.get("payload_name"),
         pkt.get("path_len"), pkt.get("sender"), phash,
         ",".join(pkt.get("path") or []) or None, raw_hex,
         pkt.get("scope"), _scope_codes(pkt),
         pkt.get("src_hash", ""), pkt.get("dest_hash", "")),
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
        "c.country AS sender_country, "
        "o.name AS observer_name, o.lat AS observer_lat, o.lon AS observer_lon, "
        "o.country AS observer_country "
        "FROM packets p "
        "LEFT JOIN contacts c ON c.prefix6 = p.sender "
        "LEFT JOIN contacts o ON o.prefix6 = substr(p.observer, 1, 6) "
        "WHERE p.id > ? GROUP BY p.id ORDER BY p.id LIMIT ?",
        (since_id, limit),
    )


def packets_with_paths(since: str, limit: int = 20000) -> list[sqlite3.Row]:
    """Packets since ``since``, reduced to what the heat map aggregation needs.

    A lean cousin of recent_packets: no raw frame, no radio figures, no
    countries -- the aggregation only places stops along each path. Newest
    first, so when the cap bites it is the oldest packets that fall off the
    heat map rather than the freshest. Same GROUP BY p.id as recent_packets,
    for the same reason: two sources can register one node under prefixes of
    different length.
    """
    return q(
        "SELECT p.sender, p.path, c.name AS sender_name, "
        "c.lat AS sender_lat, c.lon AS sender_lon, "
        "substr(p.observer, 1, 6) AS observer6, o.name AS observer_name, "
        "o.lat AS observer_lat, o.lon AS observer_lon "
        "FROM packets p "
        "LEFT JOIN contacts c ON c.prefix6 = p.sender "
        "LEFT JOIN contacts o ON o.prefix6 = substr(p.observer, 1, 6) "
        "WHERE p.ts >= ? GROUP BY p.id ORDER BY p.id DESC LIMIT ?",
        (since, limit),
    )


def packet_by_id(packet_id: int) -> sqlite3.Row | None:
    """One packet with its sender and observer contact rows joined in.

    Same GROUP BY as recent_packets, and for the same reason: two sources can
    register one node under prefixes of different length, and without it a single
    packet comes back once per matching contact row.
    """
    return qone(
        "SELECT p.*, c.name AS sender_name, c.lat AS sender_lat, c.lon AS sender_lon, "
        "c.country AS sender_country, "
        "o.name AS observer_name, o.lat AS observer_lat, o.lon AS observer_lon, "
        "o.country AS observer_country "
        "FROM packets p "
        "LEFT JOIN contacts c ON c.prefix6 = p.sender "
        "LEFT JOIN contacts o ON o.prefix6 = substr(p.observer, 1, 6) "
        "WHERE p.id = ? GROUP BY p.id",
        (packet_id,),
    )


def contacts_by_key_prefix(key_prefix: str) -> list[sqlite3.Row]:
    """Every known node whose public key starts with these hex characters.

    Returns a list, never a single row, because a path hop identifies a node by
    only its first one or two key bytes -- so several nodes can legitimately
    answer to the same hop. Callers must present that as the ambiguity it is.
    """
    h = (key_prefix or "").lower().strip()
    if not h or len(h) > 6 or not re.fullmatch(r"[0-9a-f]+", h):
        return []
    return q(
        "SELECT prefix6, name, lat, lon, node_type FROM contacts "
        "WHERE substr(prefix6, 1, ?) = ? GROUP BY prefix6 ORDER BY prefix6",
        (len(h), h),
    )


# The archive page asks three questions about one query -- the rows, the total,
# and the shape over time -- and a fourth per field it breaks down. They share
# this FROM clause, so the joins the search fields assume live in one place.
# search.FIELDS refers to these aliases by name; keep the two in step.
_SEARCH_FROM = (
    "FROM packets p "
    "LEFT JOIN contacts c ON c.prefix6 = p.sender "
    "LEFT JOIN contacts o ON o.prefix6 = substr(p.observer, 1, 6) "
)


def _search_where(query, since: str, until: str) -> tuple[str, list]:
    """The WHERE for a parsed query inside a time window."""
    sql = "WHERE p.ts >= ? AND p.ts <= ?"
    params: list = [since, until]
    if query.sql:
        sql += f" AND ({query.sql})"
        params.extend(query.params)
    return sql, params


def search_packets(query, since: str, until: str, limit: int = 100,
                   offset: int = 0) -> list[sqlite3.Row]:
    """One page of matching packets, newest first.

    Newest first, unlike the live feed: the archive is read by someone looking
    for something that already happened, and the most recent match is the one
    they most often mean.
    """
    where, params = _search_where(query, since, until)
    return q(
        "SELECT p.*, c.name AS sender_name, c.lat AS sender_lat, c.lon AS sender_lon, "
        "c.country AS sender_country, o.name AS observer_name, "
        "o.country AS observer_country "
        f"{_SEARCH_FROM}{where} GROUP BY p.id ORDER BY p.ts DESC, p.id DESC "
        "LIMIT ? OFFSET ?",
        (*params, limit, offset),
    )


def count_packets(query, since: str, until: str) -> int:
    """How many packets match, in total.

    Counted rather than inferred from the page: "1-100 of many" is the kind of
    half-answer that makes a search box untrustworthy, and over one week of
    packets on one SQLite file this is a cheap query.
    """
    where, params = _search_where(query, since, until)
    row = qone(f"SELECT COUNT(DISTINCT p.id) AS n {_SEARCH_FROM}{where}", tuple(params))
    return (row["n"] or 0) if row else 0


def packet_histogram(query, since: str, until: str, bucket_s: int) -> list[dict]:
    """Match counts per time bucket, for the bar chart above the results.

    Bucketed in SQL on the epoch second: pulling every matching timestamp into
    Python to group it there would mean transferring the whole result set to draw
    sixty bars.
    """
    where, params = _search_where(query, since, until)
    rows = q(
        f"SELECT CAST(strftime('%s', p.ts) AS INTEGER) / {int(bucket_s)} AS b, "
        f"COUNT(DISTINCT p.id) AS n {_SEARCH_FROM}{where} GROUP BY b ORDER BY b",
        tuple(params),
    )
    return [{"t": r["b"] * int(bucket_s), "n": r["n"]} for r in rows]


def packet_facets(query, since: str, until: str, column: str,
                  limit: int = 8) -> list[dict]:
    """The most common values of one field among the matches.

    ``column`` is a SQL expression from search.FIELDS, never anything a visitor
    typed -- the field name is looked up in that table first, so an unknown one
    never reaches here.
    """
    where, params = _search_where(query, since, until)
    rows = q(
        f"SELECT {column} AS v, COUNT(DISTINCT p.id) AS n {_SEARCH_FROM}{where} "
        f"AND {column} IS NOT NULL AND {column} != '' "
        "GROUP BY v ORDER BY n DESC, v LIMIT ?",
        (*params, limit),
    )
    return [{"value": str(r["v"]), "count": r["n"]} for r in rows]


def packet_span() -> dict:
    """Oldest and newest packet held, so the page can bound its time picker."""
    row = qone("SELECT MIN(ts) AS lo, MAX(ts) AS hi, COUNT(*) AS n FROM packets")
    if not row or not row["hi"]:
        return {"oldest": None, "newest": None, "total": 0}
    return {"oldest": row["lo"], "newest": row["hi"], "total": row["n"] or 0}


def last_packet_id() -> int:
    row = qone("SELECT MAX(id) AS id FROM packets")
    return (row["id"] or 0) if row else 0


def located_nodes() -> list[sqlite3.Row]:
    """Every node we know a position for; the base layer of the live map."""
    return q(
        "SELECT prefix6, name, lat, lon, node_type, country FROM contacts "
        "WHERE lat IS NOT NULL AND lon IS NOT NULL GROUP BY prefix6"
    )


def known_countries() -> list[str]:
    """Countries actually represented on the map, for the filter's choices.

    Only countries we have placed a node in: offering a visitor a filter that can
    only ever return nothing is worse than not offering it.
    """
    return [r["country"] for r in q(
        "SELECT country FROM contacts WHERE country IS NOT NULL "
        "AND lat IS NOT NULL AND lon IS NOT NULL "
        "GROUP BY country ORDER BY country"
    )]


def classify_countries(force: bool = False) -> int:
    """Give every located contact a country. Returns how many rows changed.

    Existing databases were filled long before this column existed, and a node
    that never moves would otherwise never be classified. Run at startup, so the
    first request after a deploy already has countries; ``force`` recomputes
    everything, which is what to use after rebuilding borders.json.
    """
    if not countries.available():
        return 0
    rows = q("SELECT prefix6, lat, lon FROM contacts "
             "WHERE lat IS NOT NULL AND lon IS NOT NULL"
             + ("" if force else " AND country IS NULL") + " GROUP BY prefix6")
    for r in rows:
        set_country(r["prefix6"], r["lat"], r["lon"])
    return len(rows)


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


def upsert_cli_settings(repeater_id: int, values: dict, prune: bool = True) -> None:
    """Store a node's CLI parameters.

    ``prune`` drops rows this push did not mention and the configured list does
    not name, which is what a full re-read through Home Assistant wants: a
    parameter that no longer exists should disappear.

    Pass prune=False when the source omits what it could not read, as the node's
    own six-hourly sweep does. There, an absent parameter means "no answer this
    time", not "gone" -- and the two are indistinguishable from here. The
    difference is not academic: the configured list names the region parameter
    ``cmd:region`` (it is fetched as a literal CLI command) while it is stored
    under ``region``, so pruning erases it on the first sweep that misses it.
    """
    now = utcnow()
    # Prune against the configured parameter list rather than this push: a
    # partial re-read must not wipe rows it simply did not ask about.
    configured = {p.strip() for p in
                  (get_setting("cli_params", DEFAULT_CLI_PARAMS) or "").replace(";", ",").split(",")
                  if p.strip()}
    keep = [str(p)[:64] for p in ({str(k)[:64] for k in values} | configured)]
    with _lock:
        conn = get_conn()
        if prune:
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


def find_repeater(pubkey_prefix: str) -> sqlite3.Row | None:
    """Look up a repeater by public key, without creating one.

    Public door to the prefix-tolerant match, for callers that need to ask "are
    these two keys the same node?" rather than "give me a row". Comparing the
    strings instead would answer no whenever the two sources disagree on key
    length -- Home Assistant sends five bytes where a node's own firmware sends
    six -- which is the trap _find_by_prefix exists for.
    """
    return _find_by_prefix(str(pubkey_prefix or "").lower().strip())


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


def spill_samples(items) -> None:
    """Write measurements into ``samples`` that VictoriaMetrics could not take.

    Registered with the tsdb module, which calls it from its writer thread when
    a batch fails, when the queue is full, or when no time-series database is
    configured at all. Full resolution goes in here: this is a safety net, and
    thinning the very points that only exist because the primary store was
    unavailable would defeat it.
    """
    with _lock:
        conn = get_conn()
        for repeater_id, _slug, metric, value, _ts_ns, ts in items:
            conn.execute(
                "INSERT OR REPLACE INTO samples(repeater_id, metric, ts, value) "
                "VALUES(?,?,?,?)",
                (repeater_id, metric, ts, float(value)),
            )
        conn.commit()


tsdb.register_spill(spill_samples)


def ingest(repeater_id: int, ts: str, metrics: dict, neighbors: list | None,
           force: bool = False):
    """Store a snapshot.

    ``latest`` always gets the new value: it feeds the home page and has to be
    readable without touching the network.

    Where the *history* goes depends on whether a time-series database is
    configured. With one, every numeric value is handed to it at full
    resolution, which is the whole point of the move -- nodes are going to
    publish every ten seconds. Without one, the old rule applies: a value only
    enters ``samples`` when it changed, or when the last stored point is older
    than the heartbeat interval, because otherwise a stable metric would fill
    the table with identical rows while its chart still needs points to keep
    running. force=True (manual status update) always writes.
    """
    # Read the setting before taking the lock; get_setting takes it itself
    heartbeat = timedelta(minutes=setting_int("heartbeat_min", config.HEARTBEAT_MIN))
    to_tsdb: dict = {}
    slug = None
    with _lock:
        conn = get_conn()
        row = conn.execute("SELECT slug FROM repeaters WHERE id=?",
                           (repeater_id,)).fetchone()
        slug = row["slug"] if row else None
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
            to_tsdb[name] = value
            if tsdb.enabled():
                continue    # VictoriaMetrics keeps this one, at full resolution
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
                # Per-link history: SNR trend of one individual neighbour link.
                # There are a lot of these -- one per heard node -- and they go
                # to the same place as everything else.
                if isinstance(snr, (int, float)):
                    to_tsdb[f"neighbor_{prefix}"] = float(snr)
                    if tsdb.enabled():
                        continue
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

    # Outside the lock, and non-blocking: record() only queues. Nothing in the
    # ingest path waits on a socket, and if the queue cannot take the points
    # they come straight back to spill_samples above.
    if slug and to_tsdb:
        tsdb.record(repeater_id, slug, ts, to_tsdb)


def history(repeater_id: int, metric: str, hours: int) -> list[tuple[str, float]]:
    """History straight from SQLite. The fallback path -- callers should go
    through metric_history(), which prefers VictoriaMetrics."""
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


def metric_history(repeater, metric: str, hours: int) -> list[tuple[str, float]]:
    """History for a chart, from wherever it actually lives.

    VictoriaMetrics when it answers, SQLite when it does not. The fallback is
    silent on purpose: a visitor looking at a chart cannot act on which database
    served it, and the admin page reports the health.
    """
    points = tsdb.history(repeater["slug"], metric, hours)
    if points is None:
        return history(repeater["id"], metric, hours)
    return points


def computed_utilization(repeater, total_metric: str, window_min: int = 90) -> float | None:
    """Utilisation (%) derived from the airtime totals: delta airtime / delta time.

    Computed here instead of read from the node because the meshcore-side figure
    resets on every Home Assistant restart.
    """
    # This reads the same measurements the charts do, so it has to follow them
    # to VictoriaMetrics -- otherwise moving the history would quietly empty
    # these two tiles, since `samples` stops being written once the move is on.
    series = tsdb.window_values(repeater["slug"], total_metric, window_min)
    if series is None:
        since = (datetime.now(timezone.utc)
                 - timedelta(minutes=window_min)).strftime("%Y-%m-%dT%H:%M:%SZ")
        rows = q(
            "SELECT ts, value FROM samples WHERE repeater_id=? AND metric=? AND ts>=? "
            "ORDER BY ts",
            (repeater["id"], total_metric, since),
        )
        series = []
        for r in rows:
            try:
                dt = datetime.strptime(r["ts"], "%Y-%m-%dT%H:%M:%SZ").replace(
                    tzinfo=timezone.utc)
            except (TypeError, ValueError):
                continue
            series.append((dt.timestamp(), r["value"]))
    if len(series) < 2:
        return None
    dt_min = (series[-1][0] - series[0][0]) / 60
    dv_min = series[-1][1] - series[0][1]   # airtime is in minutes
    if dt_min < 10 or dv_min < 0:           # window too short, or counter reset
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
