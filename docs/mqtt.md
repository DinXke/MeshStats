# MQTT

MQTT is the recommended way for a node to reach the server. One connection stays
open; each measurement is a short publish on it. See
[`architecture.md`](architecture.md#why-mqtt) for why this replaced HTTP.

## Topics

Topic structure is `<prefix>/<node>/<leaf>`, built by
`StatsPublisher::topicFor()`:

```c
snprintf(out, max, "%s/%s/%s", _cfg.prefix,
         _node_hex[0] ? _node_hex : "node", leaf);
```

| Part | Value |
|---|---|
| `<prefix>` | Configured on the node, default `meshcore` |
| `<node>` | 12 hex chars: the first **6 bytes** of the node's Ed25519 public key, lowercase |
| `<leaf>` | `stats` or `rx` |

Example: `meshcore/e3d3f4d7ed01/stats`.

If the node has not resolved its own key yet, `<node>` falls back to the literal
string `node`. Seeing `meshcore/node/stats` means the publisher started before
the mesh identity was available.

### Server subscriptions

| Env var | Default | Purpose |
|---|---|---|
| `MCS_MQTT_TOPIC` | `meshcore/+/stats` | Periodic statistics |
| `MCS_MQTT_RX_TOPIC` | `meshcore/+/rx` | Raw received packets (**in development**) |

Both are subscribed at **QoS 0**.

**The `+` wildcard segment is not parsed for the stats topic.** Repeater identity
comes exclusively from `repeater.pubkey_prefix` inside the JSON body. You can
therefore publish stats on any topic that matches the filter and it will still be
attributed correctly — and, less comfortably, a node can claim to be a different
repeater by putting a different prefix in the payload. See
[`security.md`](security.md#mqtt-has-no-application-level-authentication).

The `rx` topic *is* parsed for the node prefix, because a raw frame carries no
identity of the receiver.

## Payload: `stats`

Published by `StatsPublisher::publishStats()`, built by `MyMesh::fillStatsJson()`.
Same schema as the HTTP `POST /api/v1/ingest` body — that is deliberate, so both
paths share one server-side handler.

```json
{
  "repeater": {
    "pubkey_prefix": "e3d3f4d7ed01",
    "name": "BE-HSS-JessaZH.VIR"
  },
  "metrics": {
    "online": true,
    "bat": 4.152,
    "uptime": 3.41205,
    "noise_floor": -108,
    "last_rssi": -94,
    "last_snr": -4.25,
    "airtime": 128.4,
    "rx_airtime": 902.7,
    "nb_recv": 18422,
    "nb_sent": 3310,
    "sent_flood": 2011,
    "sent_direct": 1299,
    "recv_flood": 12004,
    "recv_direct": 6418,
    "recv_errors": 37,
    "tx_queue_len": 0,
    "freq": 869.5250,
    "sf": 8,
    "cr": 8,
    "tx": 22
  }
}
```

### Fields the node sends

| Key | Type | Unit | Source |
|---|---|---|---|
| `repeater.pubkey_prefix` | string | — | first 6 bytes of the public key, hex |
| `repeater.name` | string | — | `_prefs.node_name` |
| `online` | bool | — | always `true`; it is a liveness marker |
| `bat` | float | V | `board.getBattMilliVolts() / 1000` |
| `uptime` | float | **days** | `millis()/1000 / 86400`, 5 decimals |
| `noise_floor` | int | dBm | `_radio->getNoiseFloor()` |
| `last_rssi` | int | dBm | `radio_driver.getLastRSSI()` |
| `last_snr` | float | dB | `radio_driver.getLastSNR()` |
| `airtime` | float | **minutes** | `getTotalAirTime()/1000 / 60` (TX) |
| `rx_airtime` | float | **minutes** | `getReceiveAirTime()/1000 / 60` |
| `nb_recv` / `nb_sent` | int | count | radio-level packet counters |
| `sent_flood` / `sent_direct` | int | count | `Dispatcher` counters |
| `recv_flood` / `recv_direct` | int | count | `Dispatcher` counters |
| `recv_errors` | int | count | `getPacketsRecvErrors()` |
| `tx_queue_len` | int | count | `_mgr->getOutboundTotal()` |
| `freq` | float | MHz | `_prefs.freq` |
| `sf` / `cr` | int | — | spreading factor, coding rate |
| `tx` | int | dBm | `_prefs.tx_power_dbm` |

Watch the units. `uptime` is **days**, not seconds; `airtime` is **minutes**, not
milliseconds. Both are pre-divided on the node so the server stores what it
displays.

### Fields the server also accepts

The HTTP ingest path and the Home Assistant pusher send more. All of it is valid
over MQTT too, since both go through the same handler:

| Key | Meaning |
|---|---|
| `ts` | ISO timestamp `YYYY-MM-DDTHH:MM:SSZ`; defaults to server receive time |
| `force` | bool; bypass the heartbeat dedup and always write a sample |
| `neighbors` | array of `{prefix, name, snr, seen_min}` |

Each neighbour also becomes its own time series under the metric key
`neighbor_<prefix>`.

### What the server does with it

`db.ingest()`:

1. Look up or create the repeater by `pubkey_prefix`. **Unknown repeaters are
   created automatically and are public by default** — hide them in `/admin`.
2. Coerce each metric value: `bool` → `1.0`/`0.0`, numeric → `float`, anything
   else → stored as a string in `latest.value_str` (truncated to 255 chars) with
   no sample row.
3. Upsert `latest`.
4. Write a `samples` row **only if** the value changed, or the newest stored
   sample is older than `heartbeat_min` minutes (default 5), or `force` is set.
5. Upsert neighbour rows and their per-link SNR series.

Metric keys are stored verbatim — no normalisation, no allowlist. A key the
server does not recognise renders under the "Overig" / other section with the
underscores turned into spaces.

## Payload: `rx` — raw packet forwarding

> **In development.** The firmware side exists in the MeshCore working tree and
> the server-side decoder (`server/app/packets.py`) is being written as this is
> documented. Field names and behaviour may change. Do not build against it yet.

The intent: the node does not parse anything. It hex-encodes each frame exactly
as it came off the radio and lets the server decode it using
[`protocol.md`](protocol.md).

```json
{"t": 1284511, "snr": -4.25, "rssi": -94, "len": 129, "raw": "1100ab..."}
```

| Key | Meaning |
|---|---|
| `t` | node `millis()` at receipt — **uptime, not wall clock** |
| `snr` | dB, reconstructed from the radio's SNR×4 integer |
| `rssi` | dBm |
| `len` | frame length in bytes |
| `raw` | lowercase hex, `len * 2` characters |

Design constraints, all visible in `StatsPublisher::queueRawPacket()` and
`drainRxQueue()`:

- The receive callback only copies into an 8-slot ring buffer. Publishing from
  inside the receive loop would hold up reception.
- Queue full → the packet is **dropped** and `_drop_count` increments. Losing a
  packet is better than stalling the mesh.
- No broker connection → the whole queue is flushed and counted as drops, rather
  than sending a burst of stale packets on reconnect.
- At most 4 publishes per `loop()` pass.
- `STATS_RX_MAX_LEN` is 255 (`MAX_TRANS_UNIT`), so the JSON can reach roughly
  600 bytes — which is why `setBufferSize(255 * 2 + 128)` is called at startup.
  PubSubClient's 256-byte default would silently refuse these publishes.

## Retention and QoS

**Nothing is retained.** Every publish passes `false` for the retain flag:

```c
_mqtt.publish(topic, (const uint8_t *)body, n, false);
```

That is the right call for both topics. A retained `stats` message would hand a
stale snapshot to every new subscriber, and the server would ingest it as if it
were current. A retained `rx` frame would be worse.

QoS is 0 in both directions. A publish that fails is counted in `_fail_count` and
otherwise forgotten; the next interval brings a fresh snapshot anyway. For `rx`,
a failed publish leaves the item in the queue for one retry, then it ages out.

Consequence: **MQTT gives you no delivery guarantee.** Gaps in a graph after a
WiFi hiccup are expected. If you need guaranteed delivery, use the HTTP ingest
path, which returns a status code.

## Node-side configuration

Set on the node's own management page at `http://<node-ip>/`, stored in
`/stats_cfg.json` on SPIFFS.

| Field | Default | Notes |
|---|---|---|
| `host` | *(empty)* | Broker address. Empty = publisher does nothing. |
| `port` | `1883` | Falls back to 1883 if 0 or out of range |
| `user` | *(empty)* | Empty = connect anonymously |
| `pass` | *(empty)* | Blank on save = keep existing |
| `prefix` | `meshcore` | Topic prefix; empty resets to `meshcore` |
| `interval` | `300` | Seconds, **clamped to a minimum of 30** |
| `enabled` | `false` | Master switch |
| `forward_rx` | `true` | Mirror received packets (in development) |

Client id is `meshcore-<node_hex>` — derived from the public key, so two nodes
never collide.

Reconnect behaviour: on failure the node waits **15 seconds** before retrying
(`_last_connect_try`). The comment explains why: hammering an unreachable broker
previously cost the whole node its responsiveness.

`TLS is not supported on the node.` `PubSubClient` runs over a plain
`WiFiClient`.

## Server-side configuration

| Env var | Default | Notes |
|---|---|---|
| `MCS_MQTT_HOST` | *(empty)* | **Empty disables MQTT ingest entirely** |
| `MCS_MQTT_PORT` | `1883` | |
| `MCS_MQTT_USER` | *(empty)* | Empty = connect anonymously |
| `MCS_MQTT_PASS` | *(empty)* | |
| `MCS_MQTT_TOPIC` | `meshcore/+/stats` | |
| `MCS_MQTT_RX_TOPIC` | `meshcore/+/rx` | in development |

Note the Docker Compose file supplies different effective defaults:
`MCS_MQTT_HOST=mosquitto` and `MCS_MQTT_USER=meshstats`.

The subscriber runs on a daemon thread with client id `meshstats-ingest`,
keepalive 60 s, and paho's own reconnect backoff (2 s to 60 s).

> The client id is **hardcoded**. Two server instances against one broker will
> fight over it and repeatedly disconnect each other. Run one.

Ingest status is visible in `/admin`: connection state, message count, error
count, and the last error string.

**The server does not support TLS to the broker either.** There is no
`tls_set()` call and no CA configuration. Broker credentials travel in
plaintext. Keep the broker on a trusted network, or terminate elsewhere.

## Configuring Mosquitto

The shipped config is `mosquitto/mosquitto.conf`:

```
listener 1883
protocol mqtt

allow_anonymous false
password_file /mosquitto/config/passwd

persistence true
persistence_location /mosquitto/data/
log_dest stdout
log_type warning
log_type error

message_size_limit 8192
max_keepalive 300
```

Two settings deserve attention:

- **`message_size_limit 8192`.** A stats payload is well under 1 kB, but a raw
  `rx` frame can approach 600 bytes and future additions could grow. 8 kB leaves
  room. If you raise the node's `STATS_RX_MAX_LEN` or add metrics, check this.
- **`allow_anonymous false`.** This is the only authentication anywhere on the
  MQTT path. Do not turn it off.

### Creating the broker user

```bash
cp .env.example .env
# edit .env: set MCS_MQTT_USER and MCS_MQTT_PASS
./mosquitto/init-passwd.sh
```

The script runs `mosquitto_passwd -c -b` inside the `eclipse-mosquitto:2` image
against `mosquitto/passwd`.

Two caveats:

- `-c` **truncates** the file. Running it again wipes any other users you added.
  To add a second user, drop the `-c` and run `mosquitto_passwd -b` yourself.
- `-b` puts the password on the command line, so it lands in your shell history
  and in the process list while it runs. On a shared machine, use interactive
  `mosquitto_passwd` instead.

Use the same username and password on the node's management page.

### Per-topic ACLs (recommended, not shipped)

The shipped config gives every authenticated client full access to every topic.
Any node can publish stats claiming to be any repeater. If that matters, add an
ACL file:

```
# /mosquitto/config/acl
user meshstats
topic read meshcore/#

user node-e3d3f4d7ed01
topic write meshcore/e3d3f4d7ed01/#
```

and reference it with `acl_file /mosquitto/config/acl`. Give each node its own
broker account. This is untested in this repository — verify it against your
Mosquitto version before relying on it.

## Troubleshooting

| Symptom | Where to look |
|---|---|
| `/admin` shows MQTT disabled | `MCS_MQTT_HOST` is empty |
| Connected, zero messages | Node `enabled` off, or `host` empty on the node |
| Node page says "not connected" | Broker credentials; the node retries every 15 s |
| Messages counted, no repeater appears | Errors counter and `last_error` in `/admin`; usually a missing `pubkey_prefix` |
| Repeater appears with a wrong name | Name comes from the payload, not the topic — check `repeater.name` on the node |
| Graph has gaps | Expected with QoS 0. Check WiFi stability, and `heartbeat_min` |
| Two servers keep disconnecting | Both use client id `meshstats-ingest`. Run one. |

To watch the traffic directly:

```bash
mosquitto_sub -h <broker> -u <user> -P <pass> -t 'meshcore/#' -v
```
