# Architecture

MeshStats turns a MeshCore node's own view of the mesh into a public statistics
site. This document explains what the pieces are, how data moves between them,
and why the transport is MQTT rather than HTTP.

## The parts

| Directory | What it is |
|---|---|
| `server/` | FastAPI + SQLite. Public pages, admin, ingest API, MQTT subscriber. |
| `firmware/` | Modifications to the MeshCore firmware: multi-client WiFi, `MeshStatsNet`, the stats publisher. |
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
  |  meshcore/<id>/stats         |           |       |  db.ingest   |
  |  meshcore/<id>/rx (dev)      |           |       |    |         |
  +-------------------+          +-----------+       |    v         |
                                                     |  SQLite      |
                                                     +--------------+
```

The node holds one MQTT connection open and publishes a JSON snapshot of itself
every `interval` seconds (default 300). No Home Assistant, no HTTP client, no
TLS stack on the node.

The server subscribes with a wildcard (`meshcore/+/stats`). The topic segment
names the node that **published** the message; the JSON body names the repeater
the message is **about**. Usually the same node reporting on itself, but a node
may also relay statistics for repeaters it monitors, so the publisher is stored
alongside the subject rather than assumed equal to it. See [`mqtt.md`](mqtt.md).

### Path B — Home Assistant to HTTP to site

```
  Repeater  --LoRa-->  Companion node  --TCP-->  HA `meshcore`
                                                      |
                                                 entities in HA
                                                      |
                                          mc_repeater_stats (Pusher)
                                                      |
                                             HTTPS POST + Bearer
                                                      v
                                              /api/v1/ingest
```

Home Assistant already runs the `meshcore` integration for many people, and that
integration already holds sensor entities for every repeater it hears. The
`mc_repeater_stats` custom component scrapes those entities out of the state
machine, builds the same JSON body, and POSTs it.

It can do more than the node can, because it can talk *to* repeaters: it issues
`send_statusreq`, `send_telemetry_req`, `send_login` and `send_cmd` through
`meshcore.execute_command` and parses the replies. That is how the read-only CLI
settings view in `/admin` gets filled. A node publishing over MQTT reports only
on itself.

Both paths converge on the same `db.ingest()` call and produce identical rows.
You can run both at once; whichever arrives most recently wins.

### Not a data path — the TCP proxy

`proxy/` solves a different problem entirely. Stock MeshCore firmware accepts
**one** companion TCP client. If Home Assistant is connected, your phone is not.
`mc-proxy` holds the single upstream connection and fans it out to many clients.

It carries no statistics and never talks to the MeshStats server. Use it when
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
- `MeshStatsNet.h`: the repeater's web server is `AsyncWebServer` specifically
  because "a blocking server holds up the main loop, and with it the mesh — we
  have already seen that behaviour on the companion node."

The same reasoning shapes raw-packet forwarding. `MyMesh::logRxRaw()` runs in the
middle of the receive loop, so it does nothing but copy into a ring buffer;
publishing happens later in `loop()`, a few packets at a time, and drops on
overflow rather than blocking.

**The rule:** anything on the node that touches the network must be non-blocking
and must have a bounded, pre-allocated memory cost. Data loss is an acceptable
failure; stalling the mesh is not.

## Storage model

SQLite, one file, WAL mode, one process-wide connection behind a lock.

Two shapes of the same data:

- `latest` — one row per `(repeater, metric)`, the current value. What the tiles
  render from.
- `samples` — the time series, `WITHOUT ROWID`, primary key
  `(repeater_id, metric, ts)`.

A sample is written only when the value changed, or when the last stored point is
older than `heartbeat_min` (default 5 minutes). That keeps flat metrics like
`online` from writing 288 identical rows a day while still guaranteeing a graph
has points.

History reads switch strategy at 48 hours: raw rows below that, hourly averages
above (`GROUP BY substr(ts,1,13)`). Retention defaults to 180 days for samples;
neighbour rows are pruned at a hardcoded 7 days.

Unknown metrics are never rejected. A key the server has no catalog entry for
gets section `other`, label = key with underscores replaced by spaces, and shows
up on the page. That is deliberate: firmware can add a metric without a server
change.

## Where to go next

| Question | Document |
|---|---|
| What is in a packet? | [`protocol.md`](protocol.md) |
| Topics, payloads, broker setup | [`mqtt.md`](mqtt.md) |
| What was changed in the firmware and why | [`firmware.md`](firmware.md) |
| Running it | [`deployment.md`](deployment.md) |
| What is protected, and what is not | [`security.md`](security.md) |
