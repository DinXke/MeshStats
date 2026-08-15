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

### Who is speaking, and who they are speaking about

Both topics are parsed for the `+` segment. It answers a different question from
the payload, and the two are kept apart on purpose:

| | Answers | Comes from |
|---|---|---|
| **Publisher** | which node sent this message | the topic, `meshcore/<node>/…` |
| **Subject** | which repeater the numbers describe | `repeater.pubkey_prefix` in the payload |

Usually the same node reporting on itself. They are **allowed to differ**,
because a node also forwards statistics for repeaters it monitors — the topic
stays its own, while the payload names the repeater it is relaying. Blocking a
mismatch would break that.

So, for `stats`:

- No `repeater.pubkey_prefix` in the payload → the node is talking about itself
  and the topic supplies the subject.
- Both present → the payload picks the subject, and the topic prefix is stored on
  the repeater row as `source_prefix` / `source_seen`, shown in the **Bron**
  column on `/admin` (*zichzelf*, *via `<prefix>`*, or *HTTP-API*). A relay is
  also logged at INFO.
- A topic with no node segment at all is refused.

For `rx` the topic is the only identity there is: a raw frame carries nothing
about who received it.

What this does *not* fix: with one shared broker account, any client holding the
credentials can publish under any node's topic. Recording the route makes
impersonation visible, not impossible — see
[per-node accounts](#per-node-accounts-and-acls) below and
[`security.md`](security.md#mqtt-has-no-application-level-authentication).

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
| `repeater.pubkey_prefix` | string | — | first 6 bytes of the public key, hex. Optional: left out, the server reads it from the topic |
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
| `settings` | object of CLI parameters; see below |

Each neighbour also becomes its own time series under the metric key
`neighbor_<prefix>`.

### `settings` — the node's own CLI configuration

Swept by the node once a day and carried along with an ordinary statistics
message:

```json
"settings": {
  "name": "BE-HSS-JessaZH.VIR", "role": "repeater",
  "radio": "868.0,250,10,8", "freq": "869.525", "tx": "22", "af": "1",
  "repeat": "on", "advert.interval": "240",
  "flood.advert.interval": "1440", "flood.max": "3", "flood.max.unscoped": "5",
  "allow.read.only": "off", "rxdelay": "0", "txdelay": "0",
  "lat": "50.92", "lon": "5.352", "region.home": "be", "region.default": "be"
}
```

The keys are whatever the sweep table in the firmware names them — the server
stores and shows every key it receives, known or not. The parameter list on the
admin settings page steers the Home Assistant look-up only; a parameter added
there does **not** reach nodes publishing over MQTT until the firmware's own
table (`SET_PARAMS` in `MeshStatsNet.cpp`) asks for it too.

It fills the same admin page as `POST /api/v1/repeater_settings`, so a node can
populate it with no Home Assistant in the picture.

**Both paths match the key the same way.** A repeater that reports over MQTT
*and* is monitored through Home Assistant arrives under two spellings of one
key — six key bytes from its own firmware, five from Home Assistant — and the
stored key grows to the longest one seen. Both settings paths therefore resolve
the repeater through `find_repeater()`. The HTTP endpoint compared strings until
it was found doing so: a repeater picked up over MQTT would silently start
answering 404 to Home Assistant, throwing away a look-up that costs one to two
minutes of LoRa airtime — and the admin page would keep showing the last sweep
that did land, with nothing to say that anything had failed since.

**Why it is not on a topic of its own.** It was going to be
`meshcore/<node>/settings`, until a check of `mqtt_ingest.py` showed this
subscriber listens to exactly two patterns. A third topic would have been
accepted by the broker and then dropped unread — the same failure that lost the
monitored repeaters once before. Adding a topic means adding it to `MCS_MQTT_*`
**and** to the subscribe calls in `on_connect`; until both happen, the messages
go nowhere.

Two rules on the server side, both in `_handle_settings`:

- **Only a node's own settings are stored.** Statistics may legitimately be
  relayed — a node forwards figures about repeaters it monitors — but settings
  describe the publisher's own configuration, and the topic is the only part of a
  message a broker can be made to enforce. Settings arriving for a *different*
  repeater are logged and dropped, which costs nothing because the firmware only
  ever sends its own. Identity is compared through the repeater row, not the
  string, since topic and payload may spell the same key at different lengths.
- **An omitted parameter is not a deleted one.** The firmware leaves out what it
  could not read, so this path calls `upsert_cli_settings(..., prune=False)` and
  discards empty values before the call. A sweep that manages only half the
  parameters leaves the other half standing.

### What the server does with it

`db.ingest()`:

0. Determine the subject: `repeater.pubkey_prefix` if present, otherwise the node
   segment of the topic. The node segment is recorded separately as the
   publisher.
1. Look up or create the repeater by that prefix. **Unknown repeaters are
   created automatically and are public by default** — hide them in `/admin`.
2. Coerce each metric value: `bool` → `1.0`/`0.0`, numeric → `float`, anything
   else → stored as a string in `latest.value_str` (truncated to 255 chars) with
   no sample row.
3. Upsert `latest`.
4. Write a `samples` row **only if** the value changed, or the newest stored
   sample is older than `heartbeat_min` minutes (default 5), or `force` is set.
5. Upsert neighbour rows and their per-link SNR series.
6. Record which node delivered it (`db.record_source`). The HTTP ingest path
   writes `api` there, so a repeater that moved to HTTP does not keep showing a
   stale node prefix.

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
acl_file /mosquitto/config/acl

persistence true
persistence_location /mosquitto/data/
log_dest stdout
log_type warning
log_type error

message_size_limit 8192
max_keepalive 300
```

Three settings deserve attention:

- **`message_size_limit 8192`.** A stats payload is well under 1 kB, but a raw
  `rx` frame can approach 600 bytes and future additions could grow. 8 kB leaves
  room. If you raise the node's `STATS_RX_MAX_LEN` or add metrics, check this.
- **`allow_anonymous false`.** Without it there is no authentication at all on
  the MQTT path. Do not turn it off.
- **`acl_file`.** Decides who may publish where. **The broker will not start if
  the file is missing**, so run `init-passwd.sh` before `docker compose up`.

### Creating the broker user

```bash
cp .env.example .env
# edit .env: set MCS_MQTT_USER and MCS_MQTT_PASS
./mosquitto/init-passwd.sh
```

The script runs `mosquitto_passwd -c -b` inside the `eclipse-mosquitto:2` image
against `mosquitto/passwd`, and writes `mosquitto/acl` with the server account.
Both files end up owned by uid 1883 with mode `0400`, because the broker runs as
that user and refuses to start if it cannot read them.

Three caveats:

- `-c` **truncates** `passwd`, and the ACL is rewritten outright. Running the
  script again wipes every node account added with `add-node-user.sh`.
- `-b` puts the password on the command line, so it lands in your shell history
  and in the process list while it runs. On a shared machine, use interactive
  `mosquitto_passwd` instead.
- `mosquitto/acl` is gitignored (it lists account names). `acl.example` is the
  documented format.

Use the same username and password on the node's management page.

### Per-node accounts and ACLs

The shared account is the reason the topic cannot be trusted: every node signs in
as the same user, so the broker has no way to tell them apart and any of them can
publish under any prefix. Recording the publisher (above) makes that visible;
only a per-node account makes it impossible.

```bash
./mosquitto/add-node-user.sh e3d3f4d7ed01
```

The script:

1. Refuses anything that is not 6–32 lowercase hex characters — the same shape as
   the topic segment.
2. Adds `node-e3d3f4d7ed01` to `mosquitto/passwd` (without `-c`, so existing
   accounts survive), generating a random password unless you pass one.
3. Appends an ACL block restricting that account to its own two topics.
4. Restores ownership and permissions on both files.
5. Prints the password **once** — it is not stored anywhere else.

```
user node-e3d3f4d7ed01
topic write meshcore/e3d3f4d7ed01/stats
topic write meshcore/e3d3f4d7ed01/rx
```

`stats` and `rx` are listed separately rather than `meshcore/<prefix>/#`, so a
node cannot create topics the server may later use for something else.

Put the printed credentials on the node's management page and restart the broker:

```bash
docker compose restart mosquitto
```

#### Finishing the migration

`init-passwd.sh` leaves the shared account with `topic write meshcore/#` so
nothing breaks while nodes are still on it. That line is also what keeps
impersonation possible. Once every node has its own account:

```
user meshstats
topic read meshcore/#
# topic write meshcore/#   <- delete this line
```

Restart the broker. From then on the broker enforces that a node can only publish
under its own prefix, and the **Bron** column in `/admin` reflects reality rather
than a claim.

Two things to know about Mosquitto ACL files:

- **Topic lines before the first `user` line apply to every client**, anonymous
  ones included. A single stray global line makes the rest of the file
  meaningless. The generated file starts with a `user` block for that reason.
- A user with no matching block gets **no** access. Adding an account to `passwd`
  without an ACL block leaves it unable to publish anything.

Verify with the account you just created:

```bash
# allowed
mosquitto_pub -h <broker> -u node-e3d3f4d7ed01 -P <pass> \
  -t meshcore/e3d3f4d7ed01/stats -m '{"metrics":{"online":true}}'

# refused by the broker
mosquitto_pub -h <broker> -u node-e3d3f4d7ed01 -P <pass> \
  -t meshcore/aabbccddeeff/stats -m '{"metrics":{"online":true}}'
```

## Troubleshooting

| Symptom | Where to look |
|---|---|
| `/admin` shows MQTT disabled | `MCS_MQTT_HOST` is empty |
| Connected, zero messages | Node `enabled` off, or `host` empty on the node |
| Node page says "not connected" | Broker credentials; the node retries every 15 s |
| Messages counted, no repeater appears | Errors counter and `last_error` in `/admin`; usually a missing `pubkey_prefix` |
| Repeater appears with a wrong name | Name comes from the payload, not the topic — check `repeater.name` on the node |
| A repeater shows "via `<prefix>`" in /admin | Another node published its stats. Expected for relayed repeaters; unexpected otherwise |
| Node connects but nothing is published | ACL: the account has no `topic write` block, or the topic prefix does not match it. Check the broker log |
| Broker refuses to start | `mosquitto/acl` is missing or unreadable — run `init-passwd.sh` |
| Graph has gaps | Expected with QoS 0. Check WiFi stability, and `heartbeat_min` |
| Two servers keep disconnecting | Both use client id `meshstats-ingest`. Run one. |

To watch the traffic directly:

```bash
mosquitto_sub -h <broker> -u <user> -P <pass> -t 'meshcore/#' -v
```
