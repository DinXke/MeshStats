# The server

*[Nederlands](nl/server.md)*

What runs inside `server/`, where its data comes from, and how the parts hold
together. [`architecture.md`](architecture.md) describes the system as a whole,
firmware included; this document stays on the server side and goes deeper.

## In one paragraph

`server/app/main.py` builds a **FastAPI** application on top of a single
**SQLite** file. Nodes running the MeshStats firmware publish over **MQTT**;
`mqtt_ingest.py` subscribes and writes what arrives. Numeric history is handed to
**VictoriaMetrics**, with the SQLite `samples` table as the safety net that
catches everything the time-series database cannot take. Home Assistant may
still push over the HTTP API, but it is no longer the source of anything and the
site works without it.

## The modules

| File | Responsibility |
|---|---|
| `app/main.py` | Application object, middleware, router mounting, startup bootstrap, `set-password` CLI |
| `app/config.py` | Environment variables, data directory, the signing secret |
| `app/db.py` | SQLite: schema, migrations, ingest, every query the site runs |
| `app/packets.py` | Decoder for raw MeshCore frames. Pure functions, no I/O |
| `app/candidates.py` | Weighing address-hash candidates. Pure functions, no I/O |
| `app/search.py` | The archive query language, its sort keys and its column list. Pure functions, no I/O |
| `app/mqtt_ingest.py` | MQTT subscriber and the one publisher (`publish_command`) |
| `app/tsdb.py` | VictoriaMetrics writer thread and reader |
| `app/clocksync.py` | Deciding whether this machine may tell the mesh the time, and doing it |
| `app/retention.py` | The pruning loop, the VACUUM decision, and the storage figures for the admin page |
| `app/commanding.py` | Which route a request to a repeater still has, and what the page may promise |
| `app/routes_api.py` | `/api/v1/*` — ingest and the public JSON endpoints |
| `app/routes_public.py` | The public HTML pages |
| `app/routes_admin.py` | `/admin/*` — login, repeaters, tokens, settings |
| `app/auth.py` | Tokens, password hashing, signed session cookies, CSRF |
| `app/ratelimit.py` | In-process brute-force throttle on the login |
| `app/limits.py` | ASGI middleware that counts request-body bytes as they arrive |
| `app/metrics.py` | Catalogue of known repeater metrics: labels, units, sections, gauges |
| `app/countries.py` | Offline point-in-polygon lookup against `app/data/borders.json` |
| `app/templating.py` | The Jinja2 environment plus the `asset_v` cache buster |

Three of them — `packets.py`, `candidates.py` and `search.py` — have no
imports from the rest of the application at all. That is deliberate: they hold
the knowledge that is expensive to re-acquire, and a pure function is the shape
that can be tested without a database, a broker or a request.

## Where the data comes from

There are two ingest paths and they converge on the same `db.ingest()` call.

### MQTT — the nodes themselves

The primary path. A node holds one MQTT connection open and publishes; the
server subscribes with a wildcard. Two topic patterns arrive:

| Topic | Handler | What it carries |
|---|---|---|
| `meshcore/<node>/stats` | `mqtt_ingest._handle_payload()` | A JSON snapshot: `repeater`, `metrics`, optional `neighbors`, optional `settings` |
| `meshcore/<node>/rx` | `mqtt_ingest._handle_rx()` | One overheard LoRa frame as hex, plus `snr`, `rssi`, `len` |

`<node>` is the publishing node's public-key prefix. The firmware sends it in
upper case; `_topic_node()` lower-cases it, because every table downstream keys
on lower-case hex.

**The topic names the publisher, the payload names the subject.** They are
usually the same node reporting on itself, and they are allowed to differ,
because a node may relay statistics for repeaters it monitors — that is exactly
how the roof repeater this project was built for reaches the site at all. So:

- no `repeater.pubkey_prefix` in the payload means the node is talking about
  itself and the topic supplies the subject;
- when both are present the payload picks the subject, and the topic prefix is
  stored on the repeater row as `source_prefix` (`db.record_source()`).

That bounds the damage of a shared broker account without ending it. The real
fix belongs on the broker: one MQTT user per node, each restricted by ACL to its
own topic prefix. See [`mqtt.md`](mqtt.md#acl) and
[`security.md`](security.md#the-actual-fix-one-broker-account-per-node).

**One bad message never stops the loop.** `handle_message()` catches everything,
counts it in `_state["errors"]`, and logs the failure *together with a bounded,
always-printable excerpt of the payload* (`_excerpt()`, at most
`MAX_LOG_EXCERPT` = 240 characters, `backslashreplace` rather than `replace`).
The reason is concrete: a node name containing a quote character once made a
node disappear from the statistics, and `Expecting ',' delimiter: line 1 column
87` does not say what was at column 87.

### HTTP — Home Assistant or your own script

`POST /api/v1/ingest` takes the same JSON body and is authenticated with a
Bearer token. The Home Assistant integration in `homeassistant/` uses it, and it
can do one thing no node can: talk *to* repeaters that are not its own, over
LoRa, through `meshcore.execute_command`.

This path is optional and is no longer the source of anything. The site says so
in `main.py`'s own module docstring, and `commanding.py` exists because for a
while the admin page did not: it kept promising "look-up started — Home
Assistant is logging in to the repeater" while the request sat in a queue
nobody emptied any more.

## Startup

`main.bootstrap()` runs on FastAPI's `startup` event, in this order:

1. `db.get_conn()` — open SQLite, apply the schema, run `COLUMN_MIGRATIONS`,
   backfill decoder columns from `packets.raw`.
2. Create the `admin` account if the `admins` table is empty, and print the
   generated password to stdout **once**.
3. `db.prune()` — apply retention immediately rather than at some later trigger.
4. `retention.start()` — the hourly pruning loop, so retention is a rule that
   holds rather than an act that happened at startup.
5. `db.classify_countries()` — give every located contact a country. Ingest only
   classifies a position when it *changes*, and most nodes never move, so
   without this pass an existing database would never fill the column.
6. `tsdb.start()` — the writer thread, started **before** any ingest path opens,
   so the first measurement does not take the spill route for no reason.
7. `mqtt_ingest.start()` — the subscriber thread.
8. `clocksync.start()` — last, because it publishes and needs the client the
   step above created.

## The threads

The process is one uvicorn worker plus four daemon threads.

| Thread | Started by | What it does | If it dies |
|---|---|---|---|
| `mqtt-ingest` | `mqtt_ingest.start()` | `paho` loop: subscribe, decode, write | `_run()` catches everything and reconnects after 10 s; paho itself retries with a 2–60 s backoff |
| `tsdb-writer` | `tsdb.start()` | Drains the point queue, batches, POSTs | `_run()` catches per batch and spills to SQLite |
| `clocksync` | `clocksync.start()` | Sleeps `FIRST_RUN_DELAY_S` (300 s), then a round every `INTERVAL_HOURS` | `_run()` catches per round and records the failure in `_state` |
| `retention` | `retention.start()` | Sleeps 600 s, then prunes and considers a VACUUM every `INTERVAL_MIN` | `_run()` catches per round and records the failure in `_state` |

Three of them are not started when their feature is not configured: no
`MCS_MQTT_HOST`, no subscriber; no `MCS_TSDB_URL`, no writer;
`MCS_CLOCKSYNC_ENABLED=0`, no scheduler. Each says so in the log rather than
being silently absent.

SQLite is reached through one module-level connection guarded by a
`threading.Lock` (`db._lock`). That is enough because the workload is a handful
of small writes per minute plus page reads, and it is why the ingest path must
never block: `tsdb.record()` only enqueues, and `db.ingest()` calls it *outside*
the lock.

## Middleware and headers

Two middlewares, registered in `main.py`, and the order matters.

`limits.BodySizeLimitMiddleware` is added with `add_middleware`, which inserts at
the front — leaving it just inside the `security_headers` middleware. An
oversized body is therefore refused before any route, form parser or JSON
decoder sees it, and the 413 still picks up the security headers on the way out.

It counts bytes **as they arrive** rather than trusting `Content-Length`,
because a chunked request sends no length at all: the old check read `0` and
every oversized body sailed through, and admin form posts were never checked.
`routes_api.limit_body()` still looks at the declared length, but only as a
courtesy fast path — do not reintroduce a *requirement* for the header.

`security_headers` sets, with `setdefault` so a route can override:

| Header | Value |
|---|---|
| `X-Content-Type-Options` | `nosniff` |
| `X-Frame-Options` | `DENY` |
| `Referrer-Policy` | `strict-origin-when-cross-origin` |
| `Permissions-Policy` | `geolocation=(), microphone=(), camera=()` |
| `Cache-Control` | `no-cache`, on `/static` only |
| `Content-Security-Policy` | `default-src 'self'` plus the CDN hosts Leaflet and the fonts come from |

`Cache-Control: no-cache` on `/static` does not forbid storing; it forces
revalidation, which `StaticFiles` answers with a cheap 304. Without it a browser
applies heuristic caching and a reader ends up running yesterday's `app.js`
against today's API. Hash-versioned filenames were rejected because they need a
build step and this site deliberately has none — `templating.py` puts an
`asset_v` query parameter on the URLs instead, refreshed on every process start.

## Where the measurements live

| | |
|---|---|
| VictoriaMetrics | the measurements only: history, and the charts drawn from it |
| SQLite | repeaters, `latest`, contacts, neighbours, packets, tokens, admin |

`latest` stays in SQLite deliberately: it feeds the cards on the home page,
which must render fast and without touching the network, and "the one current
value" is not a shape a time-series database is good at.

**Why move at all.** Nodes are going from a reading every five minutes to one
every ten seconds. In SQLite that means throwing raw points away to keep the file
manageable; VictoriaMetrics compresses to roughly a byte per point, so keeping
full resolution there is cheaper than thinning it out here.

### The naming is fixed

The existing history was migrated under these names, and any deviation silently
splits a series in two:

```
write (influx line protocol, POST /write):
    meshstats,repeater=<slug> <metric>=<value> <nanoseconds>
read (PromQL):
    meshstats_<metric>{repeater="<slug>"}
```

Metric names come from firmware, which is free to invent them, so
`tsdb.safe_metric()` drops everything outside `[A-Za-z0-9_]` rather than
substituting — substitution would produce a different name than the migration
did. The per-neighbour SNR series (`neighbor_<prefix>`, dozens per repeater) go
the same way as everything else.

### Two rules the module exists to keep

1. **Writing may not hold up ingest.** `tsdb.record()` puts points on a bounded
   queue (`QUEUE_MAX_POINTS` = 20 000) and returns. The writer thread batches up
   to `MAX_BATCH_POINTS` (2000) or `FLUSH_INTERVAL_S` (2.0 s), whichever comes
   first, and does the HTTP with a `WRITE_TIMEOUT_S` of 5 s and
   `WRITE_ATTEMPTS` = 2.
2. **A database that is away may not lose measurements.** Everything that cannot
   be written is spilled to the SQLite `samples` table through the callback
   `db.spill_samples`, which `db.py` registers with `tsdb.register_spill()`.

Three ways to end up in `samples`, all with the same result:

| Situation | What happens |
|---|---|
| `MCS_TSDB_URL` empty | `tsdb.record()` spills every point directly |
| Write fails twice | `_flush()` spills the whole batch |
| Queue full | `record()` spills that point |

Reads mirror it. `tsdb.history()` returns `None` for everything the caller
cannot act on — not configured, unreachable, bad answer — and
`db.metric_history()` then reads `samples`. A metric that merely has no data
returns an empty list instead, so "no history yet" and "database unavailable"
stay distinguishable. The fallback is silent to the visitor on purpose: they
cannot act on which database served a chart, and the admin page reports the
health.

`samples` is therefore **not dead weight and must not be dropped**. It is what
makes the move reversible: empty `MCS_TSDB_URL`, restart, and the site is back
to its old behaviour without losing a day.

### Choosing a step

PromQL wants a step, and a 90-day chart at full resolution is millions of points
nobody can see. `tsdb.step_for()` picks from a fixed ladder aiming at
`TARGET_POINTS` = 600:

| Range | Step | Points |
|---|---|---|
| 4 h | 30 s | 480 |
| 24 h | 5 min | 288 |
| 7 d | 30 min | 336 |
| 90 d | 6 h | 360 |

24 h landing on 288 points is a coincidence worth keeping: that is exactly the
density the charts had when nodes published every five minutes, so the move does
not change how a chart looks. The query is `avg_over_time(...[step])` rather
than a bare selector, so each bucket summarises the points inside it instead of
sampling whichever one sits nearest the boundary — over 90 days that is what
keeps a spike from vanishing between buckets. Fixed rungs rather than dividing
the range exactly, so two charts of the same range agree on where their buckets
start.

`tsdb.window_values()` is the exception: the computed airtime utilisation needs
the first and last reading in a window rather than a drawn curve, so it queries
at a flat 60 s step and skips the ladder.

## Retention and pruning

`db.prune()` applies three limits in a fixed order — age, then a row ceiling,
then a byte ceiling — and returns a report of what it did. `retention.py` is the
scheduler around it: an hourly pass, the VACUUM decision, and the figures the
admin page needs to say whether the configured period is actually being met.

Packets get their own, far shorter retention than samples because they arrive
orders of magnitude faster and lose their value within days — and because
`packets.raw` roughly doubles a row. Neighbours are pruned at a hardcoded seven
days. `latest`, `contacts` and `repeater_cli` are never pruned; they are bounded
by the number of repeaters and contacts.

Besides the hourly loop, `db.prune()` is also called at startup
(`main.bootstrap()`), on roughly every 500th HTTP ingest (`routes_api.ingest()`)
and every `PRUNE_EVERY_PACKETS` (2000) received packets
(`mqtt_ingest._handle_rx()`) — the packet firehose drives its own retention.
Saving the settings form goes through `retention.run_once()` instead, so a
lowered retention walks the same path as the hourly pass.

The whole story, including why the FIFO is on `id` rather than on `ts` and when
a VACUUM is worth its cost, is in [`retention.md`](retention.md).

## Country classification

`contacts.country` holds an ISO 3166-1 alpha-2 code, or NULL for "we cannot
tell". `countries.lookup()` answers it from `app/data/borders.json` with a
bounding-box reject followed by ray casting, holes included — which is what
makes San Marino not Italy.

Two rules shape `countries.py`. **No network, ever**: a site that phoned a
geocoding service would break when that service did, and would leak every node
position it holds while it worked. **Missing data is not an error**: without the
file `countries.available()` is False, the API omits its country list and the
filter does not appear; nothing else notices.

Classification runs once per node, in `db.set_country()`, and only when a
position is *written that differs from the stored one* — an ordinary advert
repeats a position we already have and costs nothing. It is keyed on `prefix6`
and applied with a single `UPDATE` across every row sharing it, because one node
can hold several contact rows under keys of different length (Home Assistant
sends five key bytes where a node's own firmware sends six). Matching on the
literal key would give one node two countries, or none.

NULL is a real answer — at sea, outside the covered region, or within a few
hundred metres of a coast the source draws coarsely — and the UI offers it as
its own filter choice rather than guessing at the nearest country.

## Logging

Logger names, so a filter can pick one out:

| Name | Module |
|---|---|
| `meshstats.mqtt` | `mqtt_ingest.py` |
| `meshstats.tsdb` | `tsdb.py` |
| `meshstats.clocksync` | `clocksync.py` |
| `meshstats.retention` | `retention.py` |
| `meshstats.countries` | `countries.py` |
| `app.routes_api` | `routes_api.py` (module `__name__`) |

Two rules worth keeping. A **clear-on-read queue is always logged when it is
handed out**, because once the poller has taken a request there is no trace of
it anywhere (`GET /api/v1/commands`). And a **feature that falls silent says so
at WARNING**, never at DEBUG: a refused clock round is exactly the state in which
this feature stops working, and stopping quietly is what this project does not
do.

## Related documents

| Question | Document |
|---|---|
| What is in every table and column? | [`database.md`](database.md) |
| What does each HTTP endpoint do? | [`api.md`](api.md) |
| How do I write a search query? | [`search.md`](search.md) |
| What comes out of a raw frame? | [`decoder.md`](decoder.md) |
| Who is this one-byte hash? | [`candidates.md`](candidates.md) |
| How does the site set a node's clock? | [`clocksync.md`](clocksync.md) |
| How long is anything kept? | [`retention.md`](retention.md) |
| How does the site ask a node to do something? | [`commanding.md`](commanding.md) |
| Admin accounts, tokens, settings | [`admin.md`](admin.md) |
| Running and installing it | [`deployment.md`](deployment.md) |
