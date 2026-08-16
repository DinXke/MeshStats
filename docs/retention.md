# Retention and disk space

*[Nederlands](nl/retention.md)*

How long the site keeps things, what guarantees the disk does not fill up, and
why the admin page says out loud when the configured period is not being met.

The pruning itself is `db.prune()`, where the connection and the lock live.
Everything around it — when it happens, what it produced, and how the admin page
turns that into an honest story — is `server/app/retention.py`. The same split
`clocksync` keeps, for the same reason: a module that owns the storage has no
business being a scheduler.

## Three limits, in this order

| # | Limit | Setting | Applies to |
|---|---|---|---|
| 1 | **Age** | `packet_retention_days` (7), `retention_days` (180) | Everything older than its retention goes |
| 2 | **Rows** | `packet_max_rows` (200 000) | Above it the oldest packets go, whatever their age |
| 3 | **Bytes** | `db_max_mb` (512) | Above it on disk, more of the oldest packets go |

**Age is the aim; rows and bytes are the promise.**

A period alone is not a guarantee. "Keep 30 days" says nothing about how much
disk that is — it is a promise about time, made in the hope that traffic stays
what it was. One node that starts mirroring every frame it hears and 30 days is
suddenly gigabytes. So when the three collide, the oldest packets go first.

The difference matters on the admin page: whenever limit 2 or 3 does the
cutting, the configured period was **not** achieved. Somebody who set 30 days is
actually looking at 12, and that belongs on the screen rather than in a log. A
gap in a graph with no explanation is exactly the failure mode this project
keeps trying to avoid.

Neighbours are pruned separately at a hardcoded **7 days**: a neighbour unheard
for a week is not a neighbour.

## FIFO is on `id`, not on `ts`

Two reasons, and the second is the one that bites.

The id **is** the insertion order, which is what "first in, first out" means,
and it is the primary key — so the sweep is an index seek plus a ranged DELETE
instead of a scan. `db._trim_oldest_packets()` does exactly that: one `OFFSET`
seek for the id of the *keep*-th newest packet, then one ranged delete below it.
The cost is independent of how far over the limit we were, which matters because
the pass with the most to delete is the pass running while the machine is
already under pressure.

And a timestamp would be **wrong** in the one case where the two disagree: a
node whose clock is off sends packets that are stored now but dated last year,
and deleting on `ts` would throw those away first even though they are the
freshest thing we have. (Which is also why [`clocksync.md`](clocksync.md)
exists.)

`PACKET_FIFO_FLOOR` = 1000 is the floor the byte ceiling may never cut below. A
ceiling that has to be met by emptying the table entirely is a misconfigured
ceiling, and the honest answer is to say so on the admin page rather than to
leave a site with no packets on it at all.

## Measuring the bytes

`db.db_bytes()` sums the main file, `-wal` and `-shm`. The WAL counts because it
is real disk: in WAL mode a busy database carries megabytes there that the main
file does not show yet, and a ceiling that ignores it is a ceiling that is
quietly exceeded.

How many bytes one packet row costs is measured through **`dbstat`** over
`packets` and its three indexes, because a guess wrong by a factor of two means
a byte ceiling that either bites far too hard or never converges. `dbstat` is a
compile-time option (`SQLITE_ENABLE_DBSTAT_VTAB`) and is genuinely absent on
some builds, so `PACKET_BYTES_FALLBACK` = 400 bytes stands in — a slightly-off
estimate that runs again in an hour beats a sweep that refuses to work at all.

The measured reference on the live server: 7 477 packets take about 2.5 MB
including their three indexes — roughly 335 bytes a row, of which about 134 is
the raw hex frame — at an intake of about 3 738 packets a day. 200 000 rows is
therefore some 53 days of today's traffic in about 80 MB: eight times the default
7-day window, so the row cap is a guard against an explosion rather than a second
retention that quietly overrules the first.

## VACUUM

SQLite never shrinks a file on DELETE; the pages go on a free list and get
reused. On its own that is fine — a table pruned and refilled at a steady rate
reaches an equilibrium and stops growing. It stops being fine the moment someone
**lowers** a retention or the byte ceiling bites: then a large slice of the file
is free list forever, and the user who set out to reclaim disk watches the file
not move at all.

`db.maybe_vacuum()` runs a full VACUUM when **both** thresholds are met:

| Threshold | Value | Why |
|---|---|---|
| `VACUUM_MIN_FREE_BYTES` | 16 MB | Enough absolute waste to be worth a rewrite |
| `VACUUM_MIN_FREE_RATIO` | 0.20 | Enough relative waste that the file is meaningfully bigger than its contents |
| `VACUUM_MIN_DISK_FACTOR` | 3.0 | VACUUM builds a full second copy before swapping, so the disk must have room for both |

The free space is read from SQLite's own bookkeeping (`PRAGMA freelist_count`,
`page_count`, `page_size`) rather than from the file size: a 200 MB file of
which 150 MB is free list is a very different case from 200 MB of packets, and
only the first is worth a rewrite.

The disk check **refuses rather than risks** filling the very disk this feature
exists to protect.

A `PRAGMA wal_checkpoint(TRUNCATE)` runs first, before anything is measured. In
WAL mode the write-ahead log is real disk that `db_bytes()` counts, and a VACUUM
leaves a big one behind — so without the checkpoint the honest answer "we gave
40 MB back" comes out as "the database grew", which is the sort of number that
makes a user distrust the whole panel.

**`PRAGMA auto_vacuum=INCREMENTAL` was considered and rejected, twice over.**
Turning it on for an existing database requires a full VACUUM anyway — the very
operation it was meant to avoid — and once on, every page write carries
pointer-map maintenance forever, on the ingest path, to save an operation that at
this size takes seconds and runs a handful of times a year.

VACUUM takes a write lock for its duration: here that is the module lock every
other query already goes through, so nothing ever sees a half-rewritten
database.

## When it runs

| Trigger | What runs |
|---|---|
| `main.bootstrap()` | `db.prune()` once, at startup |
| `retention.start()` | The loop: first pass after `FIRST_RUN_DELAY_S` (600 s), then every `INTERVAL_MIN` (60) minutes |
| `POST /admin/settings` | `retention.run_once()`, so a lowered retention applies immediately |
| `routes_api.ingest()` | `db.prune()` on roughly every 500th HTTP ingest |
| `mqtt_ingest._handle_rx()` | `db.prune()` every `PRUNE_EVERY_PACKETS` (2000) received packets |

**Why periodic and not only at startup.** Until this loop existed, pruning
happened exactly twice: at container start and on saving the settings. For a
site redeployed every few days that is accidentally enough. For a server running
for months on end — which is exactly what this one does once it is finished — it
means nothing is ever removed after the first minute. The retention is then not
a period but a startup ritual, and the first time anybody notices is when the
disk is full.

An hour between passes is generous. At the measured intake of about 3 738
packets a day, roughly 156 rows arrive per pass, so the FIFO ceiling can be at
most an hour over — less than a tenth of a percent at 200 000 rows. Running more
often would repeat the same three index lookups without changing anything.

`retention.run_once()` prunes **first** and only then decides whether to rewrite
the file. The order is not optional: VACUUM only returns space that has already
been freed, so running it first would be an expensive rewrite of exactly the
rows that go a second later.

## What a pass reports

`db.prune()` returns a report rather than nothing, because the admin page has to
be able to say when the last sweep ran and how much it threw away. A prune that
happens silently is the reason a hole in a graph turns into an evening of
debugging.

| Key | Meaning |
|---|---|
| `at` | When the pass ran |
| `samples`, `neighbors` | Rows removed from those tables |
| `packets_age` | Packets removed by rule 1 |
| `packets_rows` | Packets removed by rule 2 |
| `packets_bytes` | Packets removed by rule 3 |
| `limit_hit` | `""`, `rows` or `bytes` — which ceiling did the cutting |
| `over_by_bytes` | How far over the ceiling the file still was after the delete |
| `packets_left` | Rows remaining |
| `oldest`, `newest` | The time span the table now covers |
| `effective_days` | That span in days — the number to compare against `days` |
| `db_bytes` | The file, WAL included |
| `days`, `sample_days`, `max_rows`, `max_mb` | The limits actually in force |

The report is stored in `settings` under `prune_last`, not only in memory:
after a restart, "when was the last prune and how much went" is still the
question the admin page has to answer, and a restart is exactly when somebody
looks.

`limit_hit` is logged at **WARNING**, the rest at INFO. That is the case where
the configured retention is not being met, and carrying on quietly would mean
somebody discovering months later that their "30 days" were really 12.

## The admin page

`retention.overview()` combines the current measurement (`db.storage_overview()`
— one query set, so the page cannot quote a packet count and a time span
measured a second apart) with the last stored report, plus the verdict that
follows:

| Field | Meaning |
|---|---|
| `limit_hit` | From the **last pass**, not the current row count |
| `falls_short` | True when a ceiling bit *and* the span is more than half a day short of the configured period |
| `over_ceiling` | The file is over `db_max_mb` right now |

The verdict is formed here rather than in the template: "is the configured
period being met" has three answers, and a template with three branches in it is
a template the fourth case falls out of.

`falls_short` is read from the last pass rather than the current count on
purpose: the fact that it happens to sit just under the ceiling right now does
not mean nothing was thrown away an hour ago that the retention says should
still be there.

### The settings form

`POST /admin/settings` writes, all clamped:

| Field | Range | Note |
|---|---|---|
| `heartbeat_min` | 1–1440 | |
| `retention_days` | 1–3650 | Samples |
| `packet_retention_days` | 1–365 | Longer than a year is a time-series database, not a packet log |
| `packet_max_rows` | `PACKET_FIFO_FLOOR`–50 000 000 | Lower than the floor cannot be honoured anyway |
| `db_max_mb` | 16–1 000 000 | |

Every field is optional rather than required, and that is not sloppiness:
**missing means "this form was not about that"** and leaves the existing value
alone. Since the admin restructure these fields are spread over two forms
(retention and storage, and display), so a required field would force one form to
carry the other's values as hidden inputs — after which an older page still open
in a tab, or a script that only wants to set the heartbeat, quietly overwrites
the retention limits. That is precisely the setting where getting it wrong costs
data.

The sentinel is `None` and not `0`, because `0` is not a valid value for these
fields and "not submitted" is a different thing from "set to zero" — a
distinction a default of `0` could not make.

Saving goes through `retention.run_once()` and not straight to `db.prune()`, so
a lowered retention walks the same path as the hourly pass — including the VACUUM
decision, because lowering a retention is exactly the case where the file
otherwise stays large while its contents have been pruned — and the result is on
the page the user just clicked on. It only runs when a retention or ceiling
actually changed: the display form has no business provoking a pruning pass.

## What the settings reach

`db.retention_settings()` is read **on every pass** rather than captured at
import. The whole point of moving these into the `settings` table is that
raising a retention takes effect without a container restart, and anything that
caches them reintroduces exactly the restart this replaces.

The heat map is the visible consequence. `routes_api._heatmap_window_h()` is a
**function**, not a module constant, precisely for that reason: a reader who
raises the retention to 30 days expects the heat map to start covering 30 days
on the next pass, not after a restart. The window is also part of the cache key
— without that, changing the retention would leave up to five minutes of a
cached overlay quietly still covering the old period, while the response states
a `window_h` that no longer matches the setting.

## About `samples`

That table is the largest in the database by row count (214 709 against 7 477
packets on the reference server) and that is no longer a growth problem but an
inheritance.

Since the measurements went to VictoriaMetrics, `db.ingest()` writes nothing to
`samples` at all: the branch is skipped as soon as `tsdb.enabled()` is true.
What still lands there is the spill (`db.spill_samples`) when the time-series
database refuses a batch or drops out — a safety net that by definition only
fills when something is broken.

It falls under the same cleanup with the long retention (`retention_days`, 180
days by default), so it shrinks by itself: the existing rows are older than the
switch and disappear as those 180 days pass, and nothing structural replaces
them.

It deliberately has **no FIFO ceiling of its own**: measurements are this site's
product, packets are working material. If the byte ceiling cannot be met while
the packets are already at their floor, the admin page says so — a loud warning
beats quietly throwing away the history everybody is looking at.

## Configuration

| Variable | Default | Setting key | Meaning |
|---|---|---|---|
| `MM_RETENTION_DAYS` | 180 | `retention_days` | Sample retention |
| `MM_PACKET_RETENTION_DAYS` | 7 | `packet_retention_days` | Packet retention, and the heat map's window |
| `MM_PACKET_MAX_ROWS` | 200000 | `packet_max_rows` | FIFO ceiling on rows |
| `MM_DB_MAX_MB` | 512 | `db_max_mb` | FIFO ceiling on the file, WAL included |
| `MM_PRUNE_MINUTES` | 60 | *(none)* | Minutes between passes; read at import |

Each of the first four is only the **default for a fresh install**; the stored
setting wins.

## Tests

`server/tests/test_retention.py` covers the three rules and their order, the
FIFO floor, the byte estimate with and without `dbstat`, and the VACUUM
thresholds.

## Related documents

| Question | Document |
|---|---|
| The tables being pruned | [`database.md`](database.md) |
| Where the measurements went instead | [`server.md`](server.md#where-the-measurements-live) |
| Backups and disk operations | [`deployment.md`](deployment.md#operations) |
| The admin page around it | [`admin.md`](admin.md) |
