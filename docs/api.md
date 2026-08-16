# HTTP API

*[Nederlands](nl/api.md)*

Every route the server serves: the JSON API in `server/app/routes_api.py`, the
public pages in `routes_public.py`, and the admin forms in `routes_admin.py`.

Timestamps are always `YYYY-MM-DDTHH:MM:SSZ`, UTC, as strings. Key prefixes are
lower-case hex. `null` means "not known", never "zero" — `routes_api._round()`
exists solely so a missing SNR does not arrive as `0.0`, which is a perfectly
plausible and completely wrong reading.

## Authentication

| Group | How |
|---|---|
| `/api/v1/ping`, `/contacts`, `/commands`, `/repeater_settings`, `/ingest` | `Authorization: Bearer <token>`, checked by `routes_api.require_token()` |
| Every other `/api/v1/*` route | None. Public, read-only, limited to repeaters with `is_public=1`, and further shaped by `show_position` / `show_name` — see [`privacy.md`](privacy.md) |
| `/admin/*` | Signed session cookie, plus a CSRF token on every POST |

`require_token()` answers **401** without a bearer header and **403** for an
unknown or revoked token, so a client can tell "you sent nothing" from "what you
sent is no good".

Every method and route is additionally capped at `MM_MAX_BODY_BYTES` (2 MB) by
`limits.BodySizeLimitMiddleware`, counted while reading.

## Ingest endpoints

### `GET /api/v1/ping`

Connection test for the Home Assistant integration.

```json
{"ok": true, "app": "meshmanager", "version": 1}
```

### `POST /api/v1/ingest`

One snapshot of one repeater. The same body the MQTT `stats` topic carries.

```json
{
  "repeater": {"pubkey_prefix": "e3d3f4d7ed", "name": "BE-XXX-Example.VIR",
               "fw": "v1.7.2", "fw_meshmanager": "1.10.0"},
  "ts": "2026-08-15T12:00:00Z",
  "metrics": {"bat": 4.15, "online": true, "uptime": 3.5},
  "neighbors": [{"prefix": "2ae7af", "name": "…", "snr": -4.25, "seen_min": 12}],
  "force": false
}
```

| Field | Required | Notes |
|---|---|---|
| `repeater.pubkey_prefix` | yes | Bounded lowercase hex, 2–64 characters; 422 for anything else |
| `repeater.name` | no | Adopted when it differs from the stored name |
| `repeater.fw`, `repeater.fw_meshmanager` | no | Only the one that is present is written |
| `ts` | no | Server time when absent |
| `metrics` | yes | Must be an object; at most 128 names of at most 64 characters; 422 otherwise |
| `neighbors` | no | At most 512 entries (422 above that); `seen_min` is converted to an absolute timestamp. An entry whose `prefix` is not a key is dropped and logged, the rest of the message is kept |
| `force` | no | Always store a sample, even unchanged |

Response: `{"ok": true, "repeater": "<slug>"}`. The row is created if the key is
unknown (`db.get_or_create_repeater()`), `source_prefix` is set to the literal
`api`, and roughly every 500th call triggers `db.prune()`.

A newly created repeater arrives **hidden** (`is_public = 0`) and stays off the
public site until an administrator approves it in `/admin`; 429 when the
repeater ceiling (`db.MAX_REPEATERS`, 500) is reached, which refuses rather than
deletes. Both checks come from `db.check_snapshot()`, the same function the MQTT
path uses — see [`retention.md`](retention.md#the-tables-somebody-else-can-grow).

### `POST /api/v1/contacts`

Contact positions harvested from adverts.

```json
{"contacts": [{"prefix": "2ae7c1d40f", "name": "…", "lat": 50.9, "lon": 5.3,
               "type": "repeater"}]}
```

Rows with a prefix shorter than 6 characters, or without numeric coordinates,
are skipped silently — this is a bulk push and one bad entry must not lose the
rest. Response: `{"ok": true, "count": <stored>}`.

### `POST /api/v1/repeater_settings`

CLI settings of one repeater, pushed by a poller.

```json
{"repeater": {"pubkey_prefix": "e3d3f4d7ed"},
 "settings": {"name": "…", "role": "repeater", "freq": "869.525", "lat": null}}
```

`null` means "asked, no answer" and is stored as such. The look-up goes through
`db.find_repeater()` rather than an equality test, because a strict match starts
answering 404 to Home Assistant the moment the same node also reports over MQTT
under a longer key — throwing away a settings sweep that costs one to two
minutes of LoRa airtime to produce. 404 when no repeater matches; 422 without a
prefix or a settings object.

Stored with `prune=True`: this is a full re-read, so a parameter that no longer
exists disappears.

### `GET /api/v1/commands`

The clear-on-read work queue for a polling client.

```json
{"refresh": ["e3d3f4d7ed"],
 "settings": [{"prefix": "e3d3f4d7ed", "params": ["name", "role", "cmd:region"]}]}
```

Every poll — the empty ones included — records `poller_seen`. That is what tells
the admin page whether there is anyone out there to hand a request to at all: an
unpolled queue looks exactly like one that was emptied a second ago, and while
nothing was polling, the page kept promising the second. A non-empty hand-out is
additionally logged, because after this call there is no record of the request
anywhere else.

## Public data endpoints

Everything below is limited to repeaters with `is_public=1`, and additionally
shaped by two per-node visibility switches. With `show_position = 0` no route
below hands out that node's coordinates, its country, or a distance derived from
them; with `show_name = 0` its name is replaced everywhere by the address hash
`0xNN`. The enforcement is one SQL view plus two named expressions rather than a
filter per endpoint, and the reasoning, the exact list of what disappears and
what stays, and what no switch hides are in [`privacy.md`](privacy.md).

### `GET /api/v1/repeaters`

Every public repeater, ordered by `sort_order, name`.

```json
[{"slug": "example", "name": "…", "pubkey_prefix": "e3d3f4d7ed",
  "last_seen": "2026-08-15T12:00:00Z", "online": true,
  "battery_percentage": 96.0, "uptime": 3.5, "neighbor_count": 14}]
```

### `GET /api/v1/repeaters/{slug}`

One repeater with every metric it has ever reported, each carrying its
presentation from `metrics.metric_info()`.

```json
{"slug": "example", "name": "…", "pubkey_prefix": "…", "last_seen": "…",
 "metrics": {"bat": {"value": 4.15, "ts": "…", "label": "Batterijspanning",
                     "unit": "V", "section": "battery", "sort": 1}},
 "neighbors": [{"prefix": "2ae7af", "name": "…", "snr": -4.25, "last_seen": "…"}]}
```

A metric the catalogue does not know is never rejected: it lands in section
`other` with its key as a label, so firmware can add a metric without a server
change.

A neighbour whose reported name is missing, or is merely its own prefix, falls
back to the name from `contacts`.

404 for an unknown or non-public slug.

### `GET /api/v1/repeaters/{slug}/map`

Map data for the link map on a repeater page.

```json
{"repeater": {"name": "…", "lat": 50.9, "lon": 5.3},
 "links": [{"prefix": "2ae7af", "name": "…", "snr": -4.25, "last_seen": "…",
            "lat": 50.8, "lon": 5.4, "node_type": "repeater"}],
 "unlocated": 3, "unlocated_names": ["…"],
 "hidden": 1, "hidden_names": ["…"]}
```

`repeater` is `null` when the repeater's own position is unknown — including
when it is known but withheld. Neighbours without a position are **counted and
named** rather than dropped, so the map never quietly claims to show the whole
neighbourhood.

`hidden` counts the neighbours left off for a different reason: their operator
chose not to show their position. It is deliberately **not** merged into
`unlocated`. "No advert with a location received yet" is a statement about the
mesh; "this node does not show its position" is a decision by a person, and one
number covering both would make the first sentence untrue. Names still travel
with it: the two switches are independent, so a neighbour who hides only their
place is still named.

### `GET /api/v1/repeaters/{slug}/history`

| Parameter | Type | Default | Range |
|---|---|---|---|
| `metric` | string | *(required)* | ≤ 64 characters |
| `hours` | int | 24 | 1–2160 |

```json
{"metric": "bat", "hours": 24, "points": [["2026-08-15T12:00:00Z", 4.15]]}
```

Served from VictoriaMetrics when it answers, from the SQLite `samples` table
when it does not. The fallback is silent on purpose: a visitor looking at a
chart cannot act on which database served it, and the admin page reports the
health.

### `GET /api/v1/packets`

The live feed behind the map on the home page.

| Parameter | Type | Default | Range |
|---|---|---|---|
| `since_id` | int | 0 | ≥ 0 |
| `limit` | int | 200 | 1–500 |

Polled rather than pushed: a few seconds of latency costs nothing here, and
plain polling survives proxies, sleeping laptops and restarts that SSE or
websockets would each need their own reconnect handling for.

Packets always arrive **ascending by id**, and `last_id` is the highest id in
the response, so the next poll picks up exactly where this one ended. The first
call (`since_id=0`) returns the *newest* `limit` packets rather than the oldest
stored ones, so a freshly loaded page opens on the present.

```json
{"last_id": 84213,
 "packets": [{
   "id": 84213, "ts": "2026-08-15T12:00:00Z",
   "observer": "2ae7c1d40f93", "observer_name": "…",
   "snr": 6.25, "rssi": -92, "len": 57,
   "route": "FLOOD", "type": "ADVERT",
   "scope": "unscoped", "scope_region": null,
   "path_len": 2,
   "sender": "2ae7c1", "sender_name": "…",
   "src": null,
   "lat": 50.9, "lon": 5.3, "origin": "sender",
   "sender_lat": 50.9, "sender_lon": 5.3,
   "observer_lat": 50.8, "observer_lon": 5.4,
   "path": [{"hash": "2a", "state": "known", "lat": 50.9, "lon": 5.3}],
   "country": "BE"
 }],
 "nodes": [{"prefix": "2ae7c1", "name": "…", "lat": 50.9, "lon": 5.3,
            "node_type": "repeater", "country": "BE"}],
 "countries": ["BE", "NL"],
 "hidden_nodes": 2}
```

`nodes`, `countries` and `hidden_nodes` are present **only on the first call**,
so the map can draw its base layer from the same request. `countries` is absent
entirely when `borders.json` is missing, which is the client's cue to leave the
country filter out of the page.

`hidden_nodes` says how many nodes are **not** in `nodes` because their operator
chose not to show their position. Absent when there are none — reporting a zero
is noise. It is a different thing from the map's own "N nodes outside the view":
that one a click solves, this one does not.

`lat`/`lon`/`origin` are the position the reception is drawn at: the sender's
when an advert named it, the observer's otherwise, and `origin` says which.
`country` follows that same choice, so filtering by country matches the dot the
visitor sees.

`path` entries are trimmed to what a moving dot needs. **A position is handed
out only for a hop that resolves to exactly one located node** — `state`
`known`. Everything else keeps its state and no coordinates, so the client draws
that stretch as the guess-free gap it is. `likely` is deliberately on the wrong
side of that line: a ranking is good enough to name a probable node in words
next to the reason it is probable, and not good enough to draw a line on a map,
where the reason does not travel with it. See [`candidates.md`](candidates.md).

`src` resolves the 1-byte source hash for packets that are not adverts; it is
`null` when the packet already names its sender, or when the payload type
carries no source hash at all.

### `GET /api/v1/packets/search`

The packet archive behind `/pakketten`. Rows, total, histogram and facets in one
call — because they all answer the same query, and a page that fired four
requests per keystroke would show them resolving at different moments, which
reads as a broken search.

| Parameter | Type | Default | Notes |
|---|---|---|---|
| `q` | string | `""` | The query language; ≤ 500 characters. See [`search.md`](search.md) |
| `since` | string | now − 24 h | `YYYY-MM-DDTHH:MM[:SS][Z]`; anything else is ignored |
| `until` | string | open end | Same format |
| `limit` | int | 100 | 1–500 |
| `offset` | int | 0 | 0–100 000 |
| `facets` | string | `""` | Comma-separated field names, at most 6 |
| `sort` | string | `""` | `field` or `field:asc\|desc` |

```json
{
  "total": 1843,
  "offset": 0,
  "sort": "time:desc",
  "bucket_s": 1440,
  "histogram": [{"t": 1755172800, "n": 37}],
  "facets": {"type": [{"value": "ADVERT", "count": 812}]},
  "packets": [{
    "id": 84213, "ts": "…",
    "observer": "2ae7c1d40f93", "observer_name": "…",
    "snr": 6.25, "rssi": -92, "len": 57,
    "route": "FLOOD", "type": "TXT_MSG",
    "scope": "unscoped", "scope_region": null,
    "path_len": 2, "path": "2a,e7",
    "sender": null, "sender_name": null,
    "src": {"hash": "e3", "state": "likely", "lead": "hops", "total": 3,
            "matches": [{"prefix": "e3d3f4", "name": "…", "hops": 0, "km": 2.1}],
            "dropped": [{"prefix": "e3aa01", "name": "…", "km": 210.4,
                         "why": "range"}],
            "dropped_total": 1},
    "src_hash": "e3",
    "dest_hash": "c3", "dest": {"…": "same shape as src"},
    "phash": "9f2c1ab30de44571",
    "country": "BE"
  }]
}
```

Four things worth knowing about the shape:

**`sort` comes back normalised.** The page reads the ordering it actually got
rather than trusting what it asked for, so the arrow in a column heading and the
rows underneath it can never disagree about the direction.

**Ordering touches the rows only.** The total, the histogram and the facets
describe the whole result set, and a set does not change by being listed in
another order — clicking a heading must not make the bar chart flicker or the
counts move. Ordering *does* bear on `offset`: page 5 of one order has nothing
to do with page 5 of another, so the page resets it.

**Every row carries every field the table can put in a column**, whether or not
the reader has that column switched on. A response shaped by the current choice
would turn each tick of a checkbox into a round trip, with a table that blinks
and a spinner for data the browser already had. The one genuinely heavy field is
left out on purpose: `raw` roughly doubles a packet row and has no column to
appear in, so it stays on the detail endpoint for the one packet somebody
actually opened.

**A refused query is a 200 with `error` set**, not a 4xx:

```json
{"error": "Onbekend veld 'foo'. Bekende velden: country, dest, hash, hops, …",
 "fields": [{"name": "type", "label": "Payloadtype", "kind": "text",
             "hint": "ADVERT", "facet": true}]}
```

For this endpoint a typo in the query is a normal outcome to render next to the
box, not an exceptional one worth noise in a proxy log. An impossible sort
travels the same road — most often it is an old link naming a column that has
since been dropped.

`bucket_s` follows the window so the chart always has on the order of sixty
bars: per-minute over an hour, per-hour over days. A facet naming an unknown or
non-facetable field is skipped rather than refused, because an old bookmark may
name a field that was since renamed.

### `GET /api/v1/packets/heatmap`

Link usage over the **full packet retention window**, aggregated for the
heat-map overlay. No parameters.

```json
{"window_h": 168, "packets": 23117, "capped": false, "hidden_nodes": 0, "max": 812,
 "segments": [{"a": {"prefix": "2ae7c1", "name": "…", "lat": 50.9, "lon": 5.3},
               "b": {"prefix": "e3d3f4", "name": "…", "lat": 50.8, "lon": 5.4},
               "n": 41}]}
```

What is counted, exactly: **one segment per pair of consecutively placeable
stops** along each packet's path, `sender → hops → observer`, once per
traversal. `packets` counts the receptions that contributed at least one
segment, not the rows read.

Four properties of that aggregation, each of them load-bearing:

- **An uncertain hop breaks the chain rather than being bridged.** The same
  honesty rule as the drawn route: a hop that does not resolve to exactly one
  located node has no position we are entitled to use. A single packet's route
  can afford a dashed guess across such a gap; here the guess would be counted
  and recounted into a solid, authoritative-looking line — exactly the lie a heat
  map must not tell. The hop is resolved without an observer or a route,
  deliberately: only a `known` resolution is used, so a ranking would change
  nothing.
- **Segments are undirected.** A link's load is the traffic over it, whichever
  way it went, so the key is the two prefixes sorted.
- **A stop equal to its neighbour is skipped without breaking the chain.** The
  observer is often the last hop, and counting that pair would add a
  zero-length link.
- **Sorted lightest first.** A client drawing them in order puts the heavy ones
  on top, and the ascending order is load-bearing beyond draw order: the
  client's rank scale reads a segment's position in this list as its rank.

The window is the full retention (168 h by default) because the overlay answers
"which links carry this mesh", and a link exercised every other day is part of
that answer even when the last 24 hours happened to miss it. The earlier
day-long window systematically hid exactly those slower links.

`_heatmap_window_h()` is a **function**, not a module constant, and that is the
point of it: the retention is an admin-page setting, so a reader who raises it
to 30 days expects the heat map to cover 30 days on the next pass rather than
after a container restart. The window is part of the **cache key** for the same
reason — otherwise changing the setting would leave up to five minutes of a
cached overlay quietly covering the old period while stating a `window_h` that
no longer matches.

The whole response is memoised for `_HEATMAP_TTL_S` = 300 s. Incremental
aggregation was considered and rejected: counts would also have to *shrink* as
packets age past retention, which needs a timestamp per traversal per segment —
at which point the bookkeeping costs more than redoing a pass that finishes in
seconds.

`capped` is true when the query returned exactly `_HEATMAP_MAX_PACKETS`
(200 000) rows, meaning older packets in the window went uncounted. A result of
exactly the cap without truncation is possible but indistinguishable, and
warning one time too often is the honest side to err on.

`hidden_nodes` is the same kind of footnote for a different reason. A node whose
position is withheld cannot be an endpoint of a segment, so it **breaks the
chain** exactly as an ambiguous hop does — traffic that really travelled over it
is not counted into a line. The count is what lets the overlay say so; without
it a missing busy link would read as a quiet stretch of mesh.

### `GET /api/v1/packets/{packet_id}`

Everything known about one reception.

```json
{"id": 84213, "ts": "…",
 "observer": "2ae7c1d40f93", "observer_name": "…",
 "observer_lat": 50.8, "observer_lon": 5.4, "observer_country": "BE",
 "snr": 6.25, "rssi": -92, "len": 57,
 "route": "FLOOD", "payload_type": 4, "type": "ADVERT",
 "scope": "unscoped", "scope_codes": null, "scope_region": null,
 "path_len": 2, "path_hash_size": 1,
 "sender": "2ae7c1", "sender_name": "…",
 "sender_lat": 50.9, "sender_lon": 5.3, "sender_country": "BE",
 "src": null, "dest": null,
 "raw": "01…",
 "path": [{"hash": "2a", "state": "ambiguous", "lead": null,
           "matches": [{"prefix": "2ae7c1", "name": "…", "lat": 50.9,
                        "lon": 5.3, "node_type": "repeater", "hops": 0,
                        "km": 2.1, "seen": "…"}],
           "dropped": []}],
 "path_stored": true,
 "error": null,
 "advert": {"name": "…", "lat": 50.9, "lon": 5.3, "node_type": "repeater",
            "ts": 1755172800, "pubkey": "2ae7c1…"}}
```

Differences from the list endpoints, all in the same direction — the frame gets
the last word:

- `scope`, `scope_codes` and the advert block are decoded from `raw` on
  request, with the stored columns as the fallback for rows whose frame was
  never kept.
- `path_hash_size` **only** comes from the frame. It is the top two bits of the
  path descriptor, chosen by whoever first sent the packet, so a row without
  `raw` has no answer and `null` is that answer rather than a plausible-looking
  `1`. The client needs it because one hop of two bytes and two hops of one byte
  print as the same four hex characters.
- `path` entries are the **full** resolution, not the trimmed one: coordinates,
  `node_type`, `seen`, and the dropped candidates with their reason.
- `path_stored` distinguishes "this packet took no hops" from "we did not keep
  the path", so the client can say so instead of pretending a packet took no
  hops.
- `error` is whatever the decoder could not get past, or `null`.

404 for an unknown id.

### `GET /api/v1/nodes/{prefix}`

Everything the site holds about one node — the panel behind a dot on the live
map. `prefix` is 6 to 64 hex characters and is cut down to six; an operator with
a full key in hand should not have to work out which six characters the API
wants. 422 for anything that is not hex.

Answered in one request rather than the five it is assembled from, because the
panel opens on a click and half-filled panels are read as "this node has no
neighbours" long before the last response lands.

```json
{
  "prefix": "e3d3f4",
  "key_prefix": "e3d3f4d7ed12", "name": "…", "node_type": "repeater",
  "country": "BE", "lat": 50.9, "lon": 5.3, "updated": "…",
  "window": {"days": 7, "oldest": "2026-08-08T09:12:00Z"},
  "repeater": {"slug": "example", "name": "…", "pubkey_prefix": "…",
               "last_seen": "…", "url": "/r/example", "online": true,
               "battery_percentage": 96.0, "uptime": 3.5,
               "neighbor_count": 14,
               "neighbors": [{"prefix": "2ae7af", "name": "…", "snr": -4.25,
                              "last_seen": "…"}],
               "neighbors_capped": true},
  "sent": {"total": 412, "first": "…", "last": "…", "hops_min": 0,
           "observers": [{"prefix": "2ae7c1", "observer": "2ae7c1d40f93",
                          "name": "…", "count": 412, "first": "…", "last": "…",
                          "snr_avg": 5.11, "snr_best": 9.5,
                          "rssi_avg": -91.2, "rssi_best": -72.0,
                          "hops_min": 0, "hops_avg": 0.42}],
           "types": [{"type": "ADVERT", "count": 412}],
           "scopes": [{"scope": "unscoped", "count": 412}]},
  "heard": {"total": 23117, "first": "…", "last": "…", "senders": 84},
  "as_hop": {"packets": 1902, "first": "…", "last": "…", "siblings": 12},
  "neighbor_of": [{"slug": "example", "name": "…", "snr": -4.25,
                   "last_seen": "…", "url": "/r/example"}]
}
```

Read it with the caveats it is built around:

**`window`** is the window every figure below it lives in. Both halves are
needed: the configured retention is the promise, the oldest packet still held is
what that promise has actually delivered so far, and on a server that restarted
yesterday those are very different numbers.

**`sent` is a floor, not a total.** It counts only what an ADVERT attributed to
this node by full key prefix. Everything else it transmitted carries a one-byte
source hash that several hundred nodes share. A ceiling and a floor side by side
was considered and rejected: two numbers whose difference is pure ambiguity
invite the reader to average them. `hops_min` and `hops_avg` are FLOOD-only, for
the reason in [`database.md`](database.md#flood-only-hop-counts).

**`as_hop` is a ceiling, and `siblings` says how much of one.** With
`siblings == 1` the count is exact even for the one-byte case; with
`siblings == 12` it is an upper bound, and the panel has the number that says
so.

**`heard` is absent, not zeroed, when the node is not an observer.** Almost no
node is one, and a "0 packets heard" line under every dot on the map would read
as a mesh where nothing hears anything.

**`repeater` is present for the few tracked repeaters only**, and stays
headline figures plus a link: `/r/<slug>` is a full page of charts, neighbour
history and settings, and a panel reproducing part of it would be a second
version of those numbers to keep in step. `neighbors` is capped at 12 and
`neighbors_capped` says the cap bit, so the panel can say "the best 12" rather
than presenting a truncated list as the whole neighbourhood.

**`null` fields are merged, not picked.** A node can own several contact rows
under keys of different length; `_node_identity()` takes the first non-empty
value per field with the longest key leading, and the **newest** `updated` of
all of them.

404 means "nothing at all is known", which is a different thing from "this node
has no traffic": a node that only ever advertised itself is a perfectly good
answer with an empty `sent` block.

## Hop resolution and its caches

Resolving a hop costs a database lookup, and the live feed resolves the path of
every packet it hands out — easily a few hundred lookups per poll per visitor,
for answers that only change when a node we have never heard of advertises
itself. `routes_api` therefore keeps two short-lived memos, both with a TTL of
`_HOP_CACHE_TTL_S` = 60 s:

| Memo | Key | Holds |
|---|---|---|
| `_hop_cache` | `(hash, observer, bound)` | The finished resolution |
| `_observer_cache` | `observer` | That observer's position and its reception table |

The hop key carries the observer and the hop bound as well as the hash, because
the same byte resolves differently depending on who heard the packet and what
the frame says about how far away the node can be. Both are small, repeating
values, so the memo still collapses a feed's worth of packets onto a handful of
entries.

`_expire_caches()` drops **both together**, on purpose: a resolution is a
function of the observer context it was computed from, and letting one expire
without the other would serve rankings built on evidence that had already been
refreshed.

`_resolve_src()` and `_resolve_dest()` are separate functions rather than one
call with the role passed in from outside. The role is what tells the resolver
which way the frame bounds the distance — a flood bounds where the packet came
*from*, a direct bounds where it is *going* — and getting those the wrong way
round would exclude the innocent. Which is why neither caller may choose.

## Public pages

| Route | Template | Contents |
|---|---|---|
| `GET /` | `index.html` | Repeater cards plus the live map. The map block (and Leaflet with it) is left out entirely when no node has a position |
| `GET /pakketten` | `packets.html` | The packet archive: query bar, histogram, facets, sortable table, column picker |
| `GET /r/{slug}` | `repeater.html` | One repeater: tiles, charts, link map, neighbour table, and for an admin the refresh button |

`/r/{slug}` assembles its blocks from the layout stored in `settings.layout`,
skipping any block with nothing to show. Two utilisation tiles
(`airtime_utilization`, `rx_airtime_utilization`) are **computed** by
`db.computed_utilization()` from the airtime counters over a 90-minute window
rather than read from the node, because the node-side figure resets on every
Home Assistant restart. It returns `null` when the window is shorter than ten
minutes or the counter went backwards — a reset, not a measurement.

The refresh button and the route behind it are computed **only for an admin**,
and the route is worked out before the button is drawn: a button that cannot do
anything should be disabled and say why. See [`commanding.md`](commanding.md).

## Admin routes

All of them require a session and check a CSRF token. Details of the mechanisms
are in [`admin.md`](admin.md).

| Route | Method | What it does |
|---|---|---|
| `/admin/login` | GET, POST | Login form and submission. Throttled per address and per username |
| `/admin/logout` | GET | Clears the session cookie |
| `/admin` | GET | Nodes and repeaters, grouped by management level |
| `/admin/repeaters/{rid}` | GET | One node: identity, visibility, look-ups, clock, firmware, delete |
| `/admin/server` | GET | Server and site: access, tokens, retention, display, parameters, clock sync, status |
| `/admin/settings` | POST | Retention limits and `history_ranges`, every field optional. Runs a full retention pass when a limit changed — see [`retention.md`](retention.md#the-settings-form) |
| `/admin/layout` | POST | Block order and visibility on a repeater page |
| `/admin/cli_params` | POST | The CLI parameter list a poller asks for |
| `/admin/repeaters/{rid}/refresh` | POST | Ask for a fresh status now. `back=node` returns to the node page instead of the public one |
| `/admin/repeaters/{rid}/settings` | GET | Redirect to `/admin/repeaters/{rid}`, query string included. Kept for bookmarks |
| `/admin/repeaters/{rid}/settings/refresh` | POST | Ask for a CLI settings sweep now |
| `/admin/repeaters/{rid}/clocksync` | POST | Set this repeater's clock now. See [`clocksync.md`](clocksync.md) |
| `/admin/repeaters/{rid}/toggle` | POST | Flip one visibility switch: `what=public` (default), `position` or `name`. `back=node` returns to the node page |
| `/admin/repeaters/{rid}/rename` | POST | Change the display name |
| `/admin/repeaters/{rid}/delete` | POST | Delete the repeater and its samples, latest and neighbours |
| `/admin/tokens` | POST | Create an API token; shown once through a 60-second cookie |
| `/admin/tokens/{tid}/revoke` | POST | Revoke a token |
| `/admin/password` | POST | Change the password. Re-issues this browser's cookie |

The two "ask for something now" routes both go through
`routes_admin._dispatch()`, which walks **every open route rather than the first
one**: the MQTT route reaches the node itself and only while it is connected to
the broker, the poller queue reaches a client that asks the repeater over LoRa
and works even with the node's WiFi off. It returns `mqtt`, `queued`, `both` or
`none`, and the page says which — not what we hoped would happen. The outcome
travels in the redirect's query string.

## Related documents

| Question | Document |
|---|---|
| What is in the tables behind these responses | [`database.md`](database.md) |
| The full query language | [`search.md`](search.md) |
| The resolution states in `src`, `dest` and `path` | [`candidates.md`](candidates.md) |
| What the decoder can and cannot conclude | [`decoder.md`](decoder.md) |
