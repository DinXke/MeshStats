# The data model

*[Nederlands](nl/database.md)*

Every table in `server/app/db.py`, every column, what goes in it and why. The
schema is in the `SCHEMA` constant at the top of that file; the columns added
later are in `COLUMN_MIGRATIONS` just below it.

## Why plain sqlite3

Deliberately `sqlite3` with a module-level connection and a mutex rather than an
ORM. The workload is a handful of small writes per minute plus page reads, so an
ORM would add a dependency and a migration story that buy nothing. The schema is
applied with `CREATE TABLE IF NOT EXISTS` on every connect, which doubles as the
migration mechanism for new tables.

Connection settings, applied once in `db.get_conn()`:

```sql
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;
```

WAL is why a backup must go through `.backup` rather than a file copy — see
[`deployment.md`](deployment.md#backup).

## Migrations

SQLite has no `ADD COLUMN IF NOT EXISTS`, so a new column on an existing table
needs the explicit check in `db._migrate()`: read `PRAGMA table_info(<table>)`
and add what is missing. Dropping a live database is not an option here.

`COLUMN_MIGRATIONS` is a list of `(table, column, declaration)` and is
**additive only**. Nothing in it retypes or removes; a column that turned out
wrong is superseded by a new one rather than altered. That keeps the list
replayable from any age of database, in order, with no version number to
track.

| Table | Column | Type | Added for |
|---|---|---|---|
| `repeaters` | `source_prefix` | TEXT | Which node published these statistics |
| `repeaters` | `source_seen` | TEXT | When it last did |
| `repeaters` | `fw` | TEXT | MeshCore version of the last message |
| `repeaters` | `fw_meshmanager` | TEXT | Our own module's version on that node |
| `repeaters` | `topic_prefix` | TEXT | Which MQTT topic prefix this node reports on |
| `repeaters` | `show_position` | INTEGER NOT NULL DEFAULT 1 | Whether visitors see this node's position |
| `repeaters` | `show_name` | INTEGER NOT NULL DEFAULT 1 | Whether visitors see this node's name |
| `packets` | `path` | TEXT | Hop hashes, comma-separated |
| `packets` | `raw` | TEXT | The frame as it came off the radio, hex |
| `contacts` | `country` | TEXT | ISO 3166-1 alpha-2, or NULL |
| `packets` | `scope` | TEXT | `unscoped` / `scoped` / `share` |
| `packets` | `scope_codes` | TEXT | The two transport codes, comma-separated |
| `packets` | `src_hash` | TEXT | 1-byte source hash, two hex characters |
| `packets` | `dest_hash` | TEXT | 1-byte destination hash, two hex characters |

**The two visibility columns default to 1, and that default is the design.**
`ALTER TABLE ADD COLUMN` gives existing rows the declared default, so a database
that gains these columns on upgrade shows exactly what it showed the day before.
A privacy column that quietly took a repeater off the map overnight would be a
worse fault than the missing column it fixed. `is_public` is left alone: it
answers a different question ("is this node on the site at all") and the three
together are one choice with three answers. See [`privacy.md`](privacy.md).

`fw` and `fw_meshmanager` are stored rather than merely shown, because they
decide whether the site may ask a node anything at all: accepting commands on
the MQTT `cmd` topic starts at node firmware 1.8.0, and a button that publishes
into the void on anything older is precisely the dishonesty those columns exist
to prevent. See [`commanding.md`](commanding.md).

### The one rename

`COLUMN_RENAMES` is the exception to "additive only", and it holds exactly one
entry: `repeaters.fw_meshstats` became `fw_meshmanager` with a real
`ALTER TABLE ... RENAME COLUMN`. Renaming rather than letting two columns
meaning the same thing coexist, because two such columns end up half-filled
each — and it is safe *here* specifically because this column is rewritten on
**every** statistics message (`record_firmware()`). Even somebody who rolls back
to the previous version of the site after this migration gets the old column
recreated and refilled on the next publication from every node. For a column
holding history it would not be allowed.

`_migrate()` renames **before** it adds. The other way round, the additive pass
would create an empty `fw_meshmanager` first, the rename would then hit a name
that already exists, and the old values would be left behind.

The payload key is accepted in both spellings (`db.payload_module_version()`), for
the same reason the environment variables are: the server and the nodes never
change name on the same day.

### The one view

`db.VIEWS` holds a single view, `visible_contacts`, created by `_migrate()`
**after** the columns exist:

```sql
CREATE VIEW visible_contacts AS
SELECT c.prefix, c.prefix6, c.node_type, c.updated,
       CASE WHEN v.show_name = 0
            THEN '0x' || upper(substr(c.prefix6, 1, 2)) ELSE c.name END AS name,
       CASE WHEN v.show_position = 0 THEN NULL ELSE c.lat END AS lat,
       …
FROM contacts c LEFT JOIN <visibility per key prefix> v ON v.p6 = c.prefix6;
```

It is `contacts` with the name, position and country passed through the
visibility of the tracked repeater behind that key prefix. **Every public read
path selects from the view; every ingest path (`upsert_advert()`,
`upsert_contacts()`, `set_country()`) still writes to the table.** What the site
knows does not change — only what it tells.

A hidden position becomes NULL, which is deliberately the same value as "never
heard a position for this node". That state was already handled everywhere, so
there is no second mechanism to keep in step with the first. A hidden name
becomes the address hash rather than NULL, because NULL would trip the existing
fallback to `prefix.upper()` and an identity would be printed anyway.

The visibility side is a grouped subquery, not a direct join on `repeaters`:
`pubkey_prefix` is unique, but two keys can agree in their first six hex
characters, and a direct join would then duplicate every contact row — the same
node would get two dots in `located_nodes()`. `MIN()` picks the stricter choice
on such a collision, which is the only direction it may fail in.

The view is dropped and recreated on every migration rather than guarded with
`IF NOT EXISTS`: SQLite stores the text a view was created with, so a database
that already ran an older version would otherwise keep the old definition
forever. There is no data in it, so recreating costs nothing.

The two paths a view cannot reach are `repeaters.name` (not in `contacts` at
all) and `neighbors.name` (which normally beats it). They are handled by
`db.public_name()` and `db.NEIGHBOR_NAME_SQL` respectively. See
[`privacy.md`](privacy.md).


## `packets.raw` is the ground truth

Everything else in a packet row is a lossy summary. `raw` is the complete
record, and the rule that follows from it is:

> The derived columns are a cache. `raw` decides.

Two consequences.

**Backfill at startup.** `db._backfill_from_raw()` runs inside `get_conn()` and
re-decodes every row where `scope IS NULL OR src_hash IS NULL` and `raw IS NOT
NULL`. Without it a newly added column would stay empty until the whole table
had rolled over, and the archive would show a week of dashes next to rows whose
answer was sitting right beside them.

It is self-limiting: every row it touches gets a **non-NULL** `src_hash` — the
empty string when the packet type carries none — so the second start finds
nothing to do. That empty-string sentinel is the whole reason the pass does not
re-decode every ACK and advert on every boot, looking for a hash that was never
there. Rows older than the `raw` column keep NULLs forever, which is the honest
answer for a packet whose bytes nobody kept.

**The detail endpoint re-decodes rather than reads.** `GET /api/v1/packets/{id}`
runs the *current* decoder over `raw` for the advert fields, the scope and
`path_hash_size`, falling back to the stored columns only for rows whose frame
was never kept. A fix to `packets.py` therefore improves packets already stored,
not just new ones.

`MAX_RAW_HEX_STORED` = 600 characters caps what is written. A MeshCore frame is
at most 255 bytes, so 510 hex characters plus slack is already generous; the cap
only exists so a nonsense payload cannot store a megabyte per row.

## Table by table

### `repeaters`

The tracked repeaters — the ones with a page at `/r/<slug>`.

| Column | Type | Contents |
|---|---|---|
| `id` | INTEGER PK | Internal id, referenced by `latest`, `samples`, `neighbors`, `repeater_cli` |
| `slug` | TEXT UNIQUE | URL segment, from `db.slugify(name)` with `-2`, `-3`… on collision |
| `pubkey_prefix` | TEXT UNIQUE | Public-key prefix, lower-case hex. Grows to the longest length ever seen |
| `name` | TEXT | Display name. Adopted from an incoming message when it changes |
| `is_public` | INTEGER | 1 = visible on the site and in the public API. Toggled in `/admin`. Auto-created repeaters get 0; the column default stays 1 for rows made any other way |
| `show_position` | INTEGER | 1 = visitors see this node's position. 0 makes the site behave as though it had never heard one |
| `show_name` | INTEGER | 1 = visitors see `name`. 0 replaces it with the address hash `0xNN` everywhere public |
| `sort_order` | INTEGER | Ordering on the home page and in `/admin` |
| `last_seen` | TEXT | Timestamp of the last snapshot, written by `db.ingest()` |
| `created_at` | TEXT | When the row was created |
| `source_prefix` | TEXT | Key of the node that published the last statistics, or the literal `api` for the HTTP path |
| `source_seen` | TEXT | When that node last published |
| `fw` | TEXT | MeshCore firmware version |
| `fw_meshmanager` | TEXT | Our own firmware module's version |

**Prefix matching is not string equality.** Sources disagree on how much of the
key they send — Home Assistant 5 bytes, a node's own firmware 6 — and matching on
the string alone once registered one node twice and split its history down the
middle. `db._find_by_prefix()` therefore accepts a match in either direction as
long as the shorter key is a prefix of the longer one **and is at least
`MIN_PREFIX_MATCH` (8) hex characters**; below that two different keys could
collide by chance. When the incoming key is longer than the stored one it
replaces it, because the longest key seen is the least ambiguous.

`db.find_repeater()` is the public door to that match, for callers that need to
ask "are these two keys the same node?" rather than "give me a row".

**`record_source()` and `record_firmware()`** are separate writes on purpose.
`record_firmware()` only overwrites what the message actually named: Home
Assistant reads a repeater's MeshCore version off the mesh and has no idea
whether our own module is on it, so it must not be able to erase the other
by staying silent about it.

### `latest`

One row per `(repeater, metric)`, the current value. What the tiles render from,
and the reason a home page can be served without touching the network.

| Column | Type | Contents |
|---|---|---|
| `repeater_id` | INTEGER | FK to `repeaters`, `ON DELETE CASCADE` |
| `metric` | TEXT | Metric name as the node sent it |
| `ts` | TEXT | Timestamp of the reading |
| `value` | REAL | Numeric value, or NULL |
| `value_str` | TEXT | Text value, for metrics that are not numbers |

Primary key `(repeater_id, metric)`. A boolean arrives as `1.0`/`0.0`; anything
that will not convert to a float is stored as text, truncated to 255 characters.

### `samples`

The time series in SQLite. `WITHOUT ROWID`, primary key
`(repeater_id, metric, ts)`.

| Column | Type | Contents |
|---|---|---|
| `repeater_id` | INTEGER | Which repeater |
| `metric` | TEXT | Metric name, or `neighbor_<prefix>` for a per-link SNR series |
| `ts` | TEXT | Timestamp |
| `value` | REAL | The measurement |

**With VictoriaMetrics configured this table receives nothing** except during an
outage — see [`server.md`](server.md#two-rules-the-module-exists-to-keep). It is
the safety net, not dead weight, and it is what makes the move reversible.

Without a time-series database the old rule applies, in `db.ingest()`: a value
enters `samples` when it **changed**, or when the last **stored sample** is
older than `heartbeat_min`. Judged on the last stored sample rather than the
last ingest, so a stable metric still gets its heartbeat point on schedule
instead of never. `force=True` — a manual status update — always writes.

Spilled points are written at **full resolution** (`db.spill_samples()`).
Thinning the very points that only exist because the primary store was
unavailable would defeat the purpose of having a net.

### `neighbors`

One repeater's own neighbour table, as the repeater reported it.

| Column | Type | Contents |
|---|---|---|
| `repeater_id` | INTEGER | FK to `repeaters`, `ON DELETE CASCADE` |
| `prefix` | TEXT | The neighbour's 6-hex key prefix |
| `name` | TEXT | Name as the repeater knows it, may be NULL |
| `snr` | REAL | Signal-to-noise of the link |
| `last_seen` | TEXT | Absolute timestamp |

Primary key `(repeater_id, prefix)`. Pruned at a hardcoded 7 days.

The node reports `seen_min` — minutes since it last heard that neighbour — and
`db.ingest()` converts it to an absolute timestamp against the snapshot's own
`ts`. On upsert, `name` and `snr` are `COALESCE`d so a report that omits one does
not erase it.

This is the one relation on the whole site that is a **measurement by a node**
rather than an inference by the server: the repeater put the entry in its own
table, key and SNR included.

### `contacts`

Everything the site knows about the identity of a node: name, position, type.
Fed by adverts (`db.upsert_advert()`, called from `db.insert_packet()`) and by
`POST /api/v1/contacts`.

| Column | Type | Contents |
|---|---|---|
| `prefix` | TEXT PK | The key prefix as the source sent it — 10 or 12 hex characters |
| `prefix6` | TEXT | First 6 characters of that key. **The join key everywhere else** |
| `name` | TEXT | Node name |
| `lat`, `lon` | REAL | Position in degrees, or NULL |
| `node_type` | TEXT | `chat`, `repeater`, `room` or `sensor` |
| `updated` | TEXT | When this row was last written |
| `country` | TEXT | ISO 3166-1 alpha-2, or NULL |

Index: `idx_contacts_p6` on `prefix6`.

**One node can own several rows.** The primary key is the literal prefix, and
two sources send prefixes of different length, so the same node arrives twice.
That is why every other table joins on `prefix6`, why `db.node_contacts()`
returns a *list* ordered longest key first, and why `routes_api._node_identity()`
merges those rows field by field rather than picking one — one source may know
the name and another the position.

Every field is `COALESCE`d on upsert, because adverts arrive far more often than
they change and a node may advertise its name without a position or the other
way round. A nameless advert must not erase a known name.

`upsert_advert()` also reuses an existing row's `prefix` when one already exists
under the same `prefix6`, so both sources keep converging on one row instead of
two that shadow each other.

### `repeater_cli`

The CLI configuration of a repeater, as read over LoRa or over MQTT.

| Column | Type | Contents |
|---|---|---|
| `repeater_id` | INTEGER | FK to `repeaters`, `ON DELETE CASCADE` |
| `param` | TEXT | Parameter name, at most 64 characters |
| `value` | TEXT | The answer, at most 4000 characters, or **NULL for "asked, no answer"** |
| `updated` | TEXT | When this row was written |

Primary key `(repeater_id, param)`.

`db.upsert_cli_settings()` takes a `prune` flag and the difference is not
academic:

- **`prune=True`** (a full re-read through Home Assistant): rows this push did
  not mention *and* the configured list does not name are deleted, so a
  parameter that no longer exists disappears.
- **`prune=False`** (a node's own daily sweep, and a monitor's sweep over LoRa):
  an absent parameter means "no answer this time", not "gone". The configured
  list names the region parameter `cmd:region` — it is fetched as a literal CLI
  command — while it is stored under `region`, so pruning would erase it on the
  first sweep that missed it.

`DEFAULT_CLI_PARAMS` is the list the *polling* path asks for. A node reading its
own CLI works from its own table (`SET_PARAMS` in the firmware) and never sees
this one; keep the two in step or a parameter exists for one kind of node and is
missing for the other. A `cmd:` prefix means "send this as a literal CLI command"
rather than prefixing it with `get `.

NULL is deliberately stored and rendered as "(geen antwoord)". Yes, that
overwrites a value an earlier sweep did get — and the alternative was rejected
for a reason: a repeater whose monitor logs in read-only answers no CLI command
at all, and with values from March still on screen and only a timestamp moved,
nobody would ever find that.

### `packets`

One row per reception. Written by `db.insert_packet()` from the MQTT `rx` path.

| Column | Type | Contents |
|---|---|---|
| `id` | INTEGER PK | Ascending; the live feed's `since_id` cursor |
| `ts` | TEXT | Server reception time. The node's `t` field is an uptime counter, not a wall clock |
| `observer` | TEXT | Key prefix of the node that heard it, lower-case, ≤ 16 characters |
| `snr` | REAL | Signal-to-noise as the radio reported it |
| `rssi` | REAL | Received signal strength |
| `len` | INTEGER | Frame length in bytes |
| `route` | TEXT | `TRANSPORT_FLOOD`, `FLOOD`, `DIRECT` or `TRANSPORT_DIRECT` |
| `payload_type` | INTEGER | 0–15, from the header byte |
| `payload_name` | TEXT | `ADVERT`, `TXT_MSG`, `ACK`… or `TYPE<n>` for an unknown one |
| `path_len` | INTEGER | Number of hop hashes in the path |
| `sender` | TEXT | 6-hex key prefix — **only ever filled from an ADVERT** |
| `phash` | TEXT | 16-hex payload hash, for de-duplication |
| `path` | TEXT | The hop hashes, comma-separated |
| `raw` | TEXT | The complete frame, hex |
| `scope` | TEXT | `unscoped`, `scoped` or `share` |
| `scope_codes` | TEXT | The two transport codes as decimal numbers, comma-separated. NULL on an unscoped packet |
| `src_hash` | TEXT | 1-byte source hash. `''` = decoded, this type has none |
| `dest_hash` | TEXT | 1-byte destination hash. `''` = decoded, this type has none |

Indexes:

| Index | Columns | Serves |
|---|---|---|
| `idx_packets_ts` | `ts` | Retention sweeps and time-window searches |
| `idx_packets_dup` | `observer, phash, ts` | Duplicate lookup, retention, and the observer side of the node panel |
| `idx_packets_sender` | `sender` | "Everything this node sent", per opened node |

`idx_packets_sender` is cheap to carry: the column is six hex characters and
NULL on the majority of rows, since only an advert names its sender in full. The
observer side needs no index of its own — `idx_packets_dup` already leads with
that column, which is why `node_reception_summary()` asks a *range* on
`observer` (`>= p6 AND < p6 + 'g'`; hex runs 0–9a–f so `g` sorts after every key
starting with those six characters) instead of wrapping it in `substr()`, an
expression no index can serve.

**De-duplication.** A flooded packet is repeated by every node in range, so the
same observer hears the same payload several times within seconds.
`insert_packet()` refuses a row whose `(observer, phash)` already appeared
within `PACKET_DUP_WINDOW_S` (60 s). The hash is over the payload only, not the
whole frame, because a flooded packet gains path hashes and transport codes at
every hop — see [`decoder.md`](decoder.md).

**`sender` is the strictest column in the schema.** It is filled only when the
decoder found an ADVERT, because an advert is the one payload that names its
origin by a full key prefix. Everything else this node ever transmitted carries
a one-byte source hash that several hundred known nodes share. Counting those in
would produce a bigger, friendlier number that is partly somebody else's
traffic, and the API says so rather than presenting a total as "all its packets".

### `settings`

Key/value store for everything the admin page can change, plus a few pieces of
bookkeeping. `db.get_setting()` / `db.set_setting()` / `db.setting_int()`.

| Key | Set by | Contents |
|---|---|---|
| `heartbeat_min` | `/admin` settings form | Minutes; clamped to 1–1440 |
| `retention_days` | `/admin` settings form | Sample retention; clamped to 1–3650 |
| `packet_retention_days` | `/admin` settings form | Packet retention, 1–365; also the heat map's window |
| `packet_max_rows` | `/admin` settings form | FIFO ceiling on the packets table |
| `db_max_mb` | `/admin` settings form | FIFO ceiling on the database file, WAL included |
| `prune_last` | `retention.run_once()` | JSON: the last pruning pass in full — see [`retention.md`](retention.md#what-a-pass-reports) |
| `history_ranges` | `/admin` settings form | Comma-separated hour values for the chart range picker |
| `layout` | `/admin` layout form | JSON: block order and visibility on a repeater page |
| `cli_params` | Repeater settings page | Comma-separated CLI parameter list |
| `refresh_requests` | `db.request_refresh()` | JSON `{prefix: ts}` — status requests waiting for a poller |
| `settings_requests` | `db.request_settings()` | JSON `{prefix: {ts, params}}` — CLI look-ups waiting for a poller |
| `settings_delivered` | `db.pop_settings_requests()` | JSON `{prefix: ts}` — when a look-up was handed out. Bounded at 200 keys |
| `poller_seen` | `GET /api/v1/commands` | When a poller last emptied the queue |
| `clocksync_high_water` | `clocksync._backwards_check()` | Highest wall-clock time this site has ever seen |
| `clocksync_sent` | `clocksync._record_sent()` | JSON `{node: epoch}` — last time sync per node, bounded at 50 keys |

A setting stored here **takes precedence over the environment variable** of the
same meaning, which is why raising a retention in `/admin` does not need a
restart.

The two queues are **clear-on-read**, and that shape is why `settings_delivered`
exists at all. Once a poller has taken a request there is no trace of it left,
so "the poller took it and the repeater stayed silent" and "nothing has ever come
to collect it" would look identical. `pending_settings_request()` answers the
second, `settings_delivered_at()` plus the age of the stored values answers the
first, and `poller_last_seen()` tells apart the third case: nothing was ever
going to come and collect it.

### `tokens`

API tokens for the HTTP ingest path.

| Column | Type | Contents |
|---|---|---|
| `id` | INTEGER PK | |
| `name` | TEXT | Label shown in `/admin` |
| `token_hash` | TEXT UNIQUE | SHA-256 of the token. The token itself is never stored |
| `created_at` | TEXT | |
| `last_used` | TEXT | Written on every successful `check_token()` |
| `revoked` | INTEGER | 1 hides the row and refuses the token |

The token is `mcs_` + `secrets.token_urlsafe(32)` and is shown once, through a
60-second cookie rather than a URL. Revoking is a flag rather than a delete, so
`last_used` survives the revocation.

### `admins`

| Column | Type | Contents |
|---|---|---|
| `id` | INTEGER PK | |
| `username` | TEXT UNIQUE | |
| `pw_hash` | TEXT | `pbkdf2$<salt hex>$<derived key hex>`, SHA-256, 200 000 rounds |

There is no session table. The `pw_hash` column *is* the revocation list: every
session cookie carries a short HMAC fingerprint of it, so changing a password
invalidates every cookie minted under the old one. See
[`admin.md`](admin.md#sessions).

## The query helpers worth knowing

### `GROUP BY p.id`, everywhere packets meet contacts

`recent_packets()`, `packets_with_paths()`, `packet_by_id()` and
`search_packets()` all join `contacts` twice — once for the sender, once for the
observer — and all of them close with `GROUP BY p.id`. Without it a single packet
comes back once per matching contact row, because one node can own several. On a
counting query that would silently double the numbers, which is why
`node_sent_by_observer()` reaches for a correlated subquery to fetch the
observer's name instead of a third join.

### The two regimes of `recent_packets()`

One ascending contract, two behaviours:

- `since_id > 0` — everything newer than that id, oldest first, so the poller
  appends in arrival order.
- `since_id = 0` — the **newest** `limit` packets, also handed back oldest
  first. It used to return the oldest stored packets ("everything after id 0"),
  which made a refreshed page open on traffic from hours ago and crawl towards
  now one page per poll. A first look at a live feed should show what is
  happening, not what happened first.

The newest-first fetch is reversed in Python rather than through a nested SELECT
ordered twice: reversing at most `limit` rows already in memory costs nothing.

### FLOOD-only hop counts

`observer_receptions()` and `node_sent_by_observer()` both restrict their hop
statistics to `route LIKE '%FLOOD'`. On a FLOOD `path_len` is the route already
travelled, which is the distance from that node to the observer; on a DIRECT it
is the route still to go. Mixing the two in one `MIN()` would report a node as a
neighbour on the strength of a packet that was merely nearly finished. See
[`protocol.md`](protocol.md#14-the-path-field) §1.4.

`observer_receptions()` additionally uses **adverts only** — rows where `sender`
is not NULL — because an advert names its sender by full key prefix. It is the
evidence table that [`candidates.md`](candidates.md) weighs against, and feeding
ambiguous data into the thing that resolves ambiguity would be circular.

### The ceiling that says it is a ceiling

`node_hop_appearances()` counts how often a node's key prefix turns up as a hop
in somebody else's path. A path entry is 1, 2 or 3 bytes of a key, so all three
widths are tried, and the shortest of them names one byte that several hundred
nodes cannot help sharing. `node_hash_siblings()` counts how many known nodes
share that first byte, so the panel can print the ambiguity *next to* the number
instead of behind it. With `siblings == 1` the count is exact.

It is a full scan — the hop list is one comma-separated column and no index can
answer a membership test on it — bounded by the packet retention and run once
per opened node. Splitting `path` into its own table was considered and
rejected: it would carry an insert per hop on every reception, the hot path, to
speed up a click. The commas around both sides of the `LIKE` make it a
whole-entry match; without them the hop `2a` would also match the entry `2ae7`.

### Why the archive has no extra indexes

`search_packets()` documents a measurement rather than a hunch. The two LEFT
JOINs and the `GROUP BY` already force the query onto a temporary B-tree for its
`ORDER BY`, even for the default order on the indexed `ts` column — so an index
on `path_len` or `snr` could not be used there at all. Measured on 50 000
packets (about seven times a busy week) one page costs 43–70 ms whichever column
it is sorted by, against 52 ms for the order that was already there. Four more
indexes would slow every insert on the ingest path down for a difference that
does not exist.

## Related documents

| Question | Document |
|---|---|
| How the pieces fit together | [`server.md`](server.md) |
| Which endpoint reads which of these tables | [`api.md`](api.md) |
| The query language over `packets` | [`search.md`](search.md) |
| What fills `packets`' decoder columns | [`decoder.md`](decoder.md) |
