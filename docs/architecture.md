# Architecture

*[Nederlands](nl/architecture.md)*

MeshManager turns a MeshCore node's own view of the mesh into a public statistics
site. This document explains what the pieces are, how data moves between them,
and why the transport is MQTT rather than HTTP.

## The parts

| Directory | What it is |
|---|---|
| `server/` | FastAPI + SQLite. Public pages, admin, ingest API, MQTT subscriber. |
| `server/tools/` | Build scripts for generated data files, kept next to what they generate. |
| `firmware/` | Modifications to the MeshCore firmware: multi-client WiFi, `MeshManagerNet`, the stats publisher. |
| `homeassistant/` | Optional HA integration that pushes repeater data over HTTP. |
| `proxy/` | Optional TCP fan-out proxy for people who cannot flash modified firmware. |
| `mosquitto/` | Broker configuration for the Docker deployment. |

## Data paths

There are two independent ways for data to reach the server, and one optional
helper that is not a data path at all.

### Path A — node to MQTT to site (recommended)

```
  Heltec / ESP32 node                Broker              Server
  +-------------------+          +-----------+       +--------------+
  | mesh + WiFi + BLE |          |           |       | mqtt_ingest  |
  |                   |  MQTT    |           |  sub  |    |         |
  | StatsPublisher    |--------->| Mosquitto |------>|    v         |
  |  meshmanager/<id>/stats         |           |       |  db.ingest   |
  |  meshmanager/<id>/rx (dev)      |           |       |    |         |
  +-------------------+          +-----------+       |    v         |
                                                     |  SQLite      |
                                                     +--------------+
```

The node holds one MQTT connection open and publishes a JSON snapshot of itself
every `interval` seconds (default 300). No Home Assistant, no HTTP client, no
TLS stack on the node.

The server subscribes with a wildcard (`meshmanager/+/stats`). The topic segment
names the node that **published** the message; the JSON body names the repeater
the message is **about**. Usually the same node reporting on itself, but a node
may also relay statistics for repeaters it monitors, so the publisher is stored
alongside the subject rather than assumed equal to it. See [`mqtt.md`](mqtt.md).

The same connection carries one message the other way. The admin page can ask a
node to read its CLI settings now, or to publish immediately, by putting a single
word on `meshmanager/<node>/cmd`. The answer comes back on the ordinary `stats`
topic, so this is a trigger and not a second data path. The firmware accepts
those two words and nothing else — see
[`mqtt.md`](mqtt.md#asking-a-node-for-something) for why that restriction is the
point rather than an omission.

### Path B — a poller to HTTP to site (optional)

```
  Repeater  --LoRa-->  Companion node  --TCP-->  HA `meshcore`
                                                      |
                                                 entities in HA
                                                      |
                                          meshmanager (Pusher)
                                                      |
                                             HTTPS POST + Bearer
                                                      v
                                              /api/v1/ingest
```

Home Assistant already runs the `meshcore` integration for many people, and that
integration already holds sensor entities for every repeater it hears. The
`meshmanager` custom component scrapes those entities out of the state
machine, builds the same JSON body, and POSTs it.

It can do something no node can: talk *to* repeaters that are not its own. It
issues `send_statusreq`, `send_telemetry_req`, `send_login` and `send_cmd`
through `meshcore.execute_command` and parses the replies, which is how the CLI
settings of a repeater running stock firmware reach `/admin`. A node publishing
over MQTT only ever reports on itself.

This path is optional, and no longer the only way to fill that view. A node
running the MeshManager firmware reads its own CLI once a day and can be asked to
do it now over the `cmd` topic above; a repeater that only a poller can reach
still needs this path. `commanding.py` works out per repeater which of the two is
available, and the admin page disables the button and says why when neither is —
because for a while it did the opposite, queueing look-ups for a poller that had
been switched off, and reporting each one as started.

Both paths converge on the same `db.ingest()` call and produce identical rows.
You can run both at once; whichever arrives most recently wins.

### Not a data path — the TCP proxy

`proxy/` solves a different problem entirely. Stock MeshCore firmware accepts
**one** companion TCP client. If Home Assistant is connected, your phone is not.
`mc-proxy` holds the single upstream connection and fans it out to many clients.

It carries no statistics and never talks to the MeshManager server. Use it when
you cannot flash modified firmware; if you can, the firmware's own 4-slot
`SerialWifiInterface` does the same job with better reply routing. See
[`protocol.md`](protocol.md#23-the-single-client-problem).

## Why MQTT

The first implementation was HTTP. The node built a JSON body and POSTed it to
`/api/v1/ingest` with a Bearer token. It worked on the bench and crashed in the
field.

The node is a Heltec V3 (ESP32-S3) running, simultaneously:

- the LoRa mesh stack, with its own packet pool and timing-sensitive receive loop
- WiFi, serving companion clients on TCP and a management page on port 80
- BLE, for the phone app

Adding `HTTPClient` on top of that was too much. Two specific costs:

1. **Memory.** `WiFiClientSecure` pulls in the TLS stack. Certificate parsing and
   the TLS record buffers need several tens of kB of heap, allocated in a burst,
   at exactly the moment WiFi and BLE are also holding buffers. The original
   `StatsPublisher::pushNow()` carried a guard against this
   (`if (ESP.getFreeHeap() < 40000) { skip this round; }`) which is a workaround,
   not a fix — it converts a crash into silently missing data.
2. **Per-measurement setup.** Every push meant a fresh TCP connection, a fresh
   TLS handshake, request, response, teardown. All of that heap churn, every
   five minutes, forever, for a payload under a kilobyte.

MQTT inverts the cost. `PubSubClient` over a plain `WiFiClient` keeps one socket
open. Publishing is: build the payload, write a short header and the bytes to an
already-established socket. There is no handshake, no certificate chain, no
per-message session. The library's fixed buffer is sized once at startup:

```c
_mqtt.setBufferSize(STATS_RX_MAX_LEN * 2 + 128);
_mqtt.setSocketTimeout(4);
_mqtt.setKeepAlive(60);
```

The trade-offs we accepted:

| | HTTP | MQTT |
|---|---|---|
| Heap per message | tens of kB, bursty | fixed buffer, allocated once |
| Setup cost | full TCP + TLS per push | one connection, kept alive |
| Transport security | TLS available (but `setInsecure()` was used anyway) | none in this deployment |
| Auth | Bearer token per request | broker username/password at connect |
| Delivery | synchronous status code | QoS 0, fire and forget |
| Needs a broker | no | yes |

Note the security column honestly: the HTTP path did not actually get
authenticated TLS. It called `secure.setInsecure()`, because public stats sites
often sit behind a tunnel with a certificate the node cannot validate. So the
real loss in moving to MQTT is smaller than it looks — we traded unvalidated TLS
for no TLS, and gained a node that stays up. See
[`security.md`](security.md#the-transport-between-node-and-server).

The other MQTT consequence is a good one. A persistent connection makes it cheap
to send *more* messages, which is what makes raw-packet forwarding practical at
all: the node can mirror every frame it hears without paying a session setup per
frame.

## Blocking is the other constraint

Heap was not the only failure mode. Two comments in the firmware record the same
lesson from different angles:

- `StatsPublisher.cpp`: the management page used to be assembled in pieces with
  `sendContent()`. Each piece is a separate blocking write, and with ESP32 WiFi
  modem-sleep latency spikes the main loop stalled inside them — taking the mesh
  down with it. It is now a single `send_P` of a static page that fetches its
  data as JSON afterwards.
- `MeshManagerNet.h`: the repeater's web server is `AsyncWebServer` specifically
  because "a blocking server holds up the main loop, and with it the mesh — we
  have already seen that behaviour on the companion node."

The same reasoning shapes raw-packet forwarding. `MyMesh::logRxRaw()` runs in the
middle of the receive loop, so it does nothing but copy into a ring buffer;
publishing happens later in `loop()`, a few packets at a time, and drops on
overflow rather than blocking.

**The rule:** anything on the node that touches the network must be non-blocking
and must have a bounded, pre-allocated memory cost. Data loss is an acceptable
failure; stalling the mesh is not.

## Where the measurements live

Measurements are in **VictoriaMetrics**. Everything else is in SQLite.

| | |
|---|---|
| VictoriaMetrics | the measurements only: history, and the charts drawn from it |
| SQLite | repeaters, `latest`, contacts, neighbours, packets, tokens, admin |

`latest` stays in SQLite deliberately. It feeds the cards on the home page, which
have to render fast and without touching the network, and "the one current value"
is not a shape a time-series database is good at.

**Why move.** Nodes are going from a reading every five minutes to one every ten
seconds. In SQLite that means throwing raw points away to keep the file
manageable. VictoriaMetrics compresses to roughly a byte per point, so keeping
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

Metric names come from nodes, so only `[A-Za-z0-9_]` survives into a field name —
`tsdb.safe_metric()` drops the rest rather than substituting, matching what the
migration did. The per-neighbour SNR series (`neighbor_<prefix>`, dozens of them)
go the same way as everything else.

### Writing never blocks ingest

`db.ingest()` hands its numeric values to a bounded queue and returns; a
background thread does the HTTP. Measured at 1.5 ms per snapshot of ~100 metrics,
against a network round trip that could be anything.

Batching sits on top of that: a node publishing every ten seconds would otherwise
mean a request per node per ten seconds, each with its own connection setup. The
writer collects up to two seconds or 2000 points, whichever comes first. The cost
is that a point can be up to two seconds late to become queryable, which no chart
can see.

### Nothing is lost when it is away

Three things can go wrong, and they all end in the same place:

| | |
|---|---|
| `MM_TSDB_URL` empty | points go straight to SQLite `samples` |
| write fails (twice) | the batch is spilled to `samples` |
| queue full (20 000 points) | the point is spilled to `samples` |

Reads mirror it. `tsdb.history()` returns `None` for everything the caller cannot
act on — not configured, unreachable, bad answer — and `db.metric_history()` then
reads `samples`. A metric that merely has no data returns an empty list instead,
so "no history yet" and "database unavailable" stay distinguishable.

This is why **`samples` is not dead weight and must not be dropped.** It is the
safety net, and it is what makes the move reversible: set `MM_TSDB_URL` empty and
the site is back to its old behaviour without losing a day.

The airtime utilisation tiles follow the same path. They are computed from the
first and last reading of the `airtime` counter over a window, and that window has
to come from wherever the measurements are — otherwise moving the history would
have quietly emptied two tiles.

### Choosing a step

PromQL wants a step, and a 90-day chart at full resolution is millions of points
nobody can see. `tsdb.step_for()` picks from a fixed ladder aiming at ~600 points:

| Range | Step | Points |
|---|---|---|
| 4 h | 30 s | 480 |
| 24 h | 5 min | 288 |
| 7 d | 30 min | 336 |
| 90 d | 6 h | 360 |

24 h landing on 288 points is a coincidence worth keeping: that is exactly the
density the charts had when nodes published every five minutes, so the move does
not change how a chart looks. The query is `avg_over_time(...[step])` rather than a
bare selector, so each bucket summarises the points inside it instead of sampling
whichever one sits nearest the boundary — over 90 days that is what keeps a spike
from vanishing between buckets.

Fixed rungs rather than dividing the range exactly, so two charts of the same
range agree on where their buckets start.

## Storage model

SQLite, one file, WAL mode, one process-wide connection behind a lock.

Two shapes of the same data:

- `latest` — one row per `(repeater, metric)`, the current value. What the tiles
  render from.
- `samples` — the time series, `WITHOUT ROWID`, primary key
  `(repeater_id, metric, ts)`.

With a time-series database configured, `samples` receives nothing except during
an outage — the rule below is what governs the fallback path, and what the site
does when it runs SQLite-only.

A sample is written only when the value changed, or when the last stored point is
older than `heartbeat_min` (default 5 minutes). That keeps flat metrics like
`online` from writing 288 identical rows a day while still guaranteeing a graph
has points.

History reads switch strategy at 48 hours: raw rows below that, hourly averages
above (`GROUP BY substr(ts,1,13)`). Retention defaults to 180 days for samples;
neighbour rows are pruned at a hardcoded 7 days.

### Packets

`packets` is the third shape and behaves differently from the other two. One row
per reception, written by the MQTT `rx` path, holding the decoded summary plus
two fields the live map needs:

- `path` — the hop hashes, comma-separated, denormalised out of the frame so the
  detail view and the feed can resolve a route without re-decoding.
- `raw` — the frame as it came off the radio, hex. The only complete record of a
  packet; everything else in the row is a lossy summary. The packet detail view
  re-decodes it on request rather than reading stored advert columns, so a fix to
  the decoder immediately improves packets already stored.

Storing the frame roughly doubles a packet row, which is affordable only because
packets carry their own retention: `MM_PACKET_RETENTION_DAYS`, 7 by default,
against 180 for samples. Both columns were added through `COLUMN_MIGRATIONS`, so
an existing database keeps its rows and simply has them empty — which the UI
reports as "not stored" instead of as "no hops".

### Retention: one aim, two promises

The retention lives in the `settings` table, with the environment variable as
the default for a fresh install only. That is deliberate: raising it is a
decision someone makes while looking at the admin page, and needing a container
restart to apply it is how a setting ends up never being touched. `routes_api`
reads it per request for the same reason — the heat map's window *is* the
retention, so raising one raises the other on the next pass, and the window is
part of that endpoint's cache key so a change cannot linger for a TTL.

A period on its own is not a guarantee, though. "Keep 30 days" says nothing
about how much disk that is; one node that starts mirroring every frame it hears
turns it into gigabytes. So `app/retention.py` applies three limits, in order:

1. **Age** — everything past `packet_retention_days` goes. The aim.
2. **Rows** — above `packet_max_rows`, the oldest packets go until it fits.
3. **Bytes** — above `db_max_mb` on disk, more of the oldest go, sized with a
   `dbstat` measurement of what a packet row really costs (with a measured
   constant as the fallback, since `dbstat` is a compile-time option).

2 and 3 are the promise, and they are FIFO on `id` rather than on `ts` — `id` is
the insertion order, which is what "first in, first out" means, and a node with
a broken clock would otherwise have its packets deleted first for being dated
last year. Whenever 2 or 3 does the cutting, the configured period was *not*
achieved, and both the admin page and the archive's own hint say so with the
real number next to the configured one. A retention that quietly under-delivers
is how a gap in a graph becomes an evening of debugging.

The pass runs at startup **and** every `MM_PRUNE_MINUTES`, in a thread of its
own, the same shape as `clocksync.py`. Pruning only at startup made the
retention an act rather than a rule: a container that ran for months never threw
anything away, and the first sign of that is a full disk.

SQLite does not shrink a file on DELETE — the pages go on a free list and get
reused, which is fine at a steady intake and stops being fine the moment someone
lowers a retention or a ceiling bites. So the same pass runs a `VACUUM` when at
least 16 MB *and* a fifth of the file is free list, and only when the disk has
room for the temporary second copy it builds. `auto_vacuum=INCREMENTAL` was
rejected: turning it on for an existing database needs exactly the full `VACUUM`
it was meant to avoid, and then charges every write forever to save an operation
that takes seconds a handful of times a year.

`samples` falls under the same sweep, on the much longer `retention_days`. It
does not grow structurally any more — with a time-series database configured,
`db.ingest` writes measurements straight to it and only `db.spill_samples` still
lands rows in SQLite, which by definition only happens while something is
broken. It has no FIFO ceiling of its own on purpose: measurements are this
site's product and packets are working material, so if the byte ceiling cannot
be met with packets already at their floor, the admin page says so rather than
silently deleting history.

Hop resolution is a lookup of contacts by key prefix, and its answers are
memoised for a minute: the live feed resolves the path of every packet it hands
out, and those answers only change when a new node advertises itself. What the
lookup can honestly conclude is bounded by the protocol, not by the code — see
[`protocol.md`](protocol.md#what-a-path-can-and-cannot-tell-you).

### Country of a node

`contacts.country` holds an ISO 3166-1 alpha-2 code, or NULL for "we cannot
tell". It is what the live map's country filter runs on.

**Where the borders come from.** `server/app/data/borders.json`, built by
`server/tools/build_borders.py` from **Natural Earth 1:50m Admin 0 – Countries**
(<https://www.naturalearthdata.com/>, via the project's GeoJSON distribution at
[nvkelso/natural-earth-vector](https://github.com/nvkelso/natural-earth-vector)).
Natural Earth is released into the **public domain**; no attribution is required,
and it is given here because a data file with no stated origin is a liability.
The build script is the reproduction recipe — read its module docstring before
changing anything about the file. The shipped artefact is 66 kB: western and
central Europe, clipped to the region, simplified to 0.004° and stored as
delta-encoded integers.

**Why not 1:110m.** A quarter of the size, and wrong exactly where this mesh
lives: it places Maastricht in Belgium, Maaseik in the Netherlands and Aachen in
Belgium. The build script carries reference points through the Meuse corridor and
`--verify` fails on any miss, so that mistake cannot return unnoticed.

**Computed once per node**, when a position is first stored or when it changes —
never per packet. Adverts repeat a position we already hold, and those cost
nothing. Contacts predating the column are filled in at startup by
`db.classify_countries()`, since a node that never moves would otherwise never be
classified.

**Keyed on `prefix6`, applied to every row sharing it.** Home Assistant sends
five key bytes where a node's own firmware sends six, so one node can hold two
contact rows under keys of different length — the same trap `_find_by_prefix()`
exists for on the repeaters table. Matching on the literal key would give one node
two countries, or none.

**Nothing at runtime touches the network**, and the feature is optional: if
`borders.json` is missing or unreadable, `countries.available()` is False, the API
omits its country list, and the filter does not appear. Nothing else on the page
notices.

NULL is a real answer rather than a failure — at sea, outside the covered region,
or within a few hundred metres of a coastline the source draws coarsely — and the
UI offers it as its own filter choice instead of guessing at the nearest country.
Countries are shown as a flag plus the ISO code, so there is no second dictionary
of country names to keep translated.

Unknown metrics are never rejected. A key the server has no catalog entry for
gets section `other`, label = key with underscores replaced by spaces, and shows
up on the page. That is deliberate: firmware can add a metric without a server
change.

## The live map filter

One filter — free text plus a country choice — governs everything the home page
shows: the packet list, the flashes, the travelling dots, the node markers and
the "last 5 minutes" counter. A filter reaching only some of those is actively
misleading, which is exactly what an unfiltered marker layer under a filtered
list turned out to be.

Four decisions worth keeping:

**Non-matching nodes are dimmed, not hidden.** Hiding reads more cleanly, but the
mesh is the point of this map: a Dutch node means little without the Belgian ones
around it, and a route crossing the filter would end at markers that are not
there. Faint keeps the geography while letting the matches carry the eye, and
tooltips stay attached so a ghost can still be identified on hover.

**The open packet's path is exempt.** Every node on a displayed route is shown at
full strength while the detail panel is open, including hops the filter excludes.
A gap in a drawn path already means something precise — "we cannot tell which
node this was" — and the filter must not be able to imitate that.

**The text filter only touches the markers when it names a node.** Payload types
are not node properties, so a visitor typing `advert` is filtering traffic, not
geography; treating it as geography would dim every node and announce that
nothing matches. The test is simply whether the text matches any node at all.

**The view follows only when it has to.** If a matching node is already on
screen, the map stays where the visitor put it; if none is, it refits, because
filtering to Great Britain while parked over Belgium otherwise shows an empty
map. With the detail panel open the view is never moved — its path was framed
deliberately when the packet was opened.

Markers are restyled in place, never rebuilt: each remembers the style it is
wearing, so a keystroke touches only those that actually changed. Rebuilding the
layer per keystroke is the one thing on this page that would genuinely feel slow
at mesh scale.

## The packet list and the detail panel

The sender leads the list, because that is what a reader is looking for. Then, on
a wide screen: heard by, type, SNR, RSSI, hops, length, country, and the time at
the far right. Under 700 px the row folds into two compact lines — sender and
time on the first, type, SNR, hops and country on the second — and RSSI and
length drop out rather than being squeezed. They are one tap away in the detail
panel, and a list that scrolls sideways on a phone is worse than one showing
less. Below 360 px the observer prefix goes too.

Four things about it read as bugs if you do not know why:

**"Heard by" comes and goes.** While a single node forwards everything, the
column is the same name on every row, so it hides itself. It returns the moment
the packets on screen come from more than one observer — which is exactly when it
becomes one of the most interesting columns, because then it says who heard what.
No setting to find, no migration; it resolves itself as the mesh grows.

**The observer is a name on a wide screen and a key prefix on a phone.** Both
are in the DOM and CSS picks one; one long node name would otherwise push every
row onto a third line.

**The sender cell is `flex: 1 1 0`, not `auto`.** A wrapping flex container
assigns items to lines using their *untruncated* widths and only shrinks them
afterwards, so with `auto` a long node name pushes the timestamp onto a line of
its own before any ellipsis applies. From a zero basis it can never cause a wrap.

**No sorting, deliberately.** The neighbour table on a repeater page sorts, and
should: it is a fixed set you compare. This is a feed — rows arrive every few
seconds and age off the bottom. Sorting by SNR would put the newest packet
anywhere, or nowhere visible, and the order would churn under the reader on every
poll. Newest first is the only stable order a live feed has; narrowing is what
the filter is for.

### The detail panel is a drawer on a desktop and a sheet on a phone

Wide: a full-height drawer beside the map, so the picture stays intact. Narrow:
a bottom sheet that **opens at a peek height** showing time, sender, observer and
payload type, with a grip to drag it up for the path list and the raw bytes. It
always reopens at peek and never remembers being raised — you open one of these
to see something on the map, and a sheet that remembered "fully up" would hide
the map every time.

That peek height is not cosmetic. A sheet opening at full height covered the very
route it was explaining: the path was drawn correctly and two thirds of it sat
behind the panel. The other half of that fix is `mapPadding()`, which frames the
route into the part of the map that is actually visible — Leaflet knows nothing
about a panel lying over its container, and without the padding it centres the
path in a rectangle half of which cannot be seen. The same computation covers a
landscape phone, where the map runs off the top of a short viewport.

### Naming a node from one byte

A packet's sender, its destination and every hop in its path are named by the
first byte or two of a public key. One byte has 256 values and a site of this
kind knows several hundred nodes, so more than one node answering to the same
value is routine, not a fault (see [`protocol.md`](protocol.md) §1.4). The panel
used to list every match as "N mogelijk" — honest, but needlessly wide: it
compared against every node ever heard anywhere, including nodes hundreds of
kilometres away that had only ever arrived after a dozen hops.

`server/app/candidates.py` narrows that list on evidence the database already
holds, in two steps and never a third.

**Exclusion, only where the frame supports it.** The route type decides which end
of a packet its hop count constrains, and getting that backwards would rule out
the innocent. A FLOOD carries the route already travelled, so it bounds where the
packet *came from*: heard at zero hops means the sender was inside radio range,
full stop. A DIRECT carries the route still to go, so it bounds where the packet
is *headed*. Neither bounds the other — a flooded packet's destination may sit
anywhere in the mesh however few hops it has travelled so far, and that is why
the case this was built for keeps all four of its candidates and merely orders
them. Where a bound does exist, a candidate further away than `MAX_RADIO_HOP_KM`
per remaining link is dropped, and the panel says how many went and on what
ground. A node this observer has really heard at that hop count is never dropped:
the threshold stands in for a measurement and loses to one.

**Ranking, on three coarse signals in a fixed order.** How few hops away this
observer has actually heard the node — from ADVERTs, the one payload that names
its sender in full — then distance, then how recently it was seen. Bands rather
than a score, compared in order rather than summed: a weighted number would
separate every pair of candidates including the pairs the evidence does not
separate, and nobody could retell why the winner won.

**And a refusal.** When nothing separates the top two, the answer is still "N
mogelijk". The list is sorted, so something is always first — but first by
alphabet is not evidence, and printing it as "most likely" would be a coin toss
dressed as a conclusion. Four states come back (`known`, `likely`, `ambiguous`,
`unknown`) and the front end reads each differently. `likely` sits deliberately
on the *uncertain* side of the map's line: rings on every candidate, not a route
through the leader. A ranking is a sentence with its reasons attached, and a line
on a map carries no sentence.

| Question | Document |
|---|---|
| What is in a packet? | [`protocol.md`](protocol.md) |
| Topics, payloads, broker setup | [`mqtt.md`](mqtt.md) |
| What was changed in the firmware and why | [`firmware.md`](firmware.md) |
| Running it | [`deployment.md`](deployment.md) |
| What is protected, and what is not | [`security.md`](security.md) |
