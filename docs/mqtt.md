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
| `<leaf>` | `stats` or `rx` from the node, `cmd` towards it |

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

### The one topic the server publishes on

| Env var | Default | Purpose |
|---|---|---|
| `MCS_MQTT_CMD_TOPIC` | `meshcore/{node}/cmd` | One command for one node |

`{node}` is filled in with that node's own pubkey prefix, so a broker ACL can
bind a node's *read* permission to the same prefix as its *write* permission.
See [Asking a node for something](#asking-a-node-for-something).

The clock synchronisation publishes on this same topic and has its own settings,
because the question it answers is not "which topic" but "may we speak at all":

| Env var | Default | Purpose |
|---|---|---|
| `MCS_CLOCKSYNC_ENABLED` | `1` | Send the time to the nodes at all |
| `MCS_CLOCKSYNC_HOURS` | `24` | Hours between two rounds |
| `MCS_CLOCKSYNC_MAX_ERROR_S` | `10` | Uncertainty the kernel may have about its own clock before we stop believing it |
| `MCS_CLOCKSYNC_MAX_JUMP_S` | `30` | Wall-clock jump against the monotonic clock that counts as a jump rather than a correction |

See [Setting the clock](#setting-the-clock).

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
go nowhere. That rule is about *publishing*, and says nothing about the
direction the `cmd` topic below runs in.

## Asking a node for something

The daily sweep answers "what is this node configured as" once a day. The admin
page needs to answer it *now*, and used to do that by writing into a queue that
only the Home Assistant integration ever emptied. Take Home Assistant out of the
chain — which is exactly what a node publishing straight to MQTT is for — and
the button wrote into a queue nobody read, while the page promised a look-up
that had already started.

So the server publishes one word on `meshcore/<node>/cmd`:

| Word | The node does |
|---|---|
| `settings` | reads its CLI parameters now, and publishes them with the statistics message it sends as soon as the sweep finishes |
| `settings <key>` | logs in to a repeater it *monitors*, reads **that** repeater's CLI parameters over LoRa, and publishes them under that repeater's name (MeshStats 1.9.0) |
| `status` | publishes a statistics message immediately |
| `time <epoch>` | sets its own clock to that UNIX time in UTC seconds, then checks the clocks of the repeaters it monitors over LoRa (MeshStats 1.10.0) |

The answer comes back on the ordinary `stats` topic. Nothing else in the ingest
path changes, and a receiver that knows nothing about `cmd` still works. `time`
is the exception: it produces no message at all, only a change on the node. What
happened is readable with `wifi clock` on the node and on the site's admin page.

**The firmware accepts those three words and nothing else.** Not a prefix test,
not a fallthrough to `handleCommand()` — an exact match against a list of three.
The telnet console on the node does hand its input to the CLI, but that console
asks for a password over a link the operator controls, while this topic is
reachable by anyone holding broker credentials. These repeaters hang on roofs and
run off solar panels; one `reboot` in a loop is enough to lose one. The first two
words only make the node say what it would have said by itself, so the worst an
attacker on the broker achieves with them is a statistics message, at most one
every 30 seconds (`MQTT_CMD_MIN_GAP_MS`).

The arguments do not widen that, and the two are worth separating because they
are different kinds of thing.

The one on `settings` never becomes text that reaches a CLI: it selects a single
entry from the node's monitor list, and the commands then sent are the
compiled-in parameter table. That list is writable only from the admin page and
the mesh CLI, both password-protected, so the most a broker account can do with
it is read out a repeater the operator already chose to monitor — at most once
every ten minutes.

The one on `time` is a number, checked against a window of years at both ends
(2025–2100, in `mqtt_ingest.py` and again in `MeshStatsNet.cpp`) and applied by
code that only ever moves a clock **forward**. So this word does grant a real
capability that the other two do not: it changes state. Named plainly, because
it is the one that deserves an ACL: an attacker on the broker can push a node's
clock to any time between now and 2100, and that cannot be walked back over the
air. The reason is in the next section.

### Why a clock only ever goes forward

This governs the whole feature and it is not a MeshCore quirk to route around.

An advert carries the clock of the node that emitted it, and every node that
already knows the sender **drops an advert whose timestamp did not increase**
(`onAdvertRecv` in `MyMesh.cpp`, the `timestamp > client->last_timestamp` test).
Move a repeater's clock back by an hour and it is invisible to everyone who
knows it for an hour — a maintenance command that takes a roof repeater off the
mesh. MeshCore's own `time` and `clock sync` refuse to go backwards; this
firmware refuses for that reason specifically, and so does the server.

Two consequences follow, and both are visible rather than hidden:

- A node found running **fast** is reported and left alone. There is no way to
  correct it over the air; only `clkreboot` on that node helps, and that reboots
  it.
- A time published in error cannot be undone. Hence `clocksync.py`, which
  refuses to publish at all unless this machine's own clock can be established
  as trustworthy — see [Setting the clock](#setting-the-clock) below.

### Setting the clock

A MeshCore node never gets its clock right on its own. An ESP32 without a
battery-backed RTC starts at whatever the firmware carries — `clkreboot` sets it
literally to 15 May 2024 — and drifts from there. A roof repeater reboots by
itself: flat battery, watchdog, a power cut in thunderstorm season. Each time it
comes back stamping everything it says with a date that has nothing to do with
today, and nothing on the mesh corrects it, because nothing on the mesh knows
better either.

The server does, so it publishes `time <epoch>` on a schedule
(`MCS_CLOCKSYNC_HOURS`, default 24). The format was not chosen: it is what
`CommonCLI::handleCommand` parses in its `time ` branch — `_atoi` of the rest of
the line, straight into `setCurrentTime`, UNIX seconds in UTC.

Only nodes that **publish here directly** are addressed. A relayed repeater gets
its time from its monitor over LoRa, which is exactly what the command makes
that monitor do, and is the only path there is to it.

Before anything leaves, `clocksync.py` has to establish that this machine's own
clock is trustworthy, and refuses loudly — logs and admin page — when it cannot:

- **Kernel clock discipline** via `adjtimex(2)`: the `STA_UNSYNC` flag and the
  kernel's own `maxerror`. Same source `timedatectl` reports as
  `NTPSynchronized`, and it needs no package, no privileges and no
  `timedatectl` inside the container — which a slim Python image does not have.
- **Wall clock against monotonic clock** between rounds, so a clock that was
  *set* rather than *elapsed* is caught however satisfied the kernel is.
- **Never backwards**, across restarts, via a high-water mark in the settings
  table. This catches a host that booted without a network and fell back to an
  RTC value or a build date, which `adjtimex` can be perfectly happy about.

> **Scope, honestly.** In an LXC the container shares the host's clock and may
> not set it: `timedatectl` there reports `NTP=no` alongside
> `NTPSynchronized=yes`. What we read is therefore the **host kernel's** claim,
> passed through. "The host says it is synchronised" is not the same as "the
> time is demonstrably correct". **The correctness of every clock in this mesh
> ultimately rests on the NTP configuration of the Proxmox host** — if that is
> wrong, all of this runs neatly, measurably and completely wrong along with it.

Two checks were considered and rejected. Cross-checking against timestamps from
the mesh is circular: the nodes we would check against are the nodes we set, so
agreement only proves our own message arrived — and the `rx` message carries `t`
as an uptime counter, not a wall clock, so the usable source is not even there.
Querying an external time source does not work either: this server sits behind
VPN/LAN with no outbound reference, so that check would pass in development and
report "unreachable" forever on the real machine, which is a check that gets
switched off within a week.

What the node does with it is in `MeshStatsNet.cpp` above `MON_CLK_FIRST_MS`: it
sets its own clock, then walks its monitor list asking each repeater `clock` (one
round trip), and only sends `clock sync` to one whose reading is more than two
minutes behind. Reading first costs the same as syncing blind — one command, one
reply — so the argument for it is not thrift but evidence: it turns "this
repeater was four minutes behind" into something the site can show, and it means
the node never transmits a clock-changing command on a guess. The threshold is
two minutes because `clock` answers to the minute and the reply arrives seconds
after the far side read it; the firmware computes the drift as a *range* and acts
only when the whole range lies beyond the threshold.

Airtime: one command and one reply per monitored node per day, against the three
of each that an ordinary poll round already spends every fifteen minutes. That is
why this may run on a schedule where the settings sweep may not. The node caps
its own LoRa half at once an hour whatever arrives on the topic.

### Reaching a repeater that does not publish

The third form exists for the case this project was built around: a repeater on
a roof that talks only over LoRa. Its statistics reach the site because another
node polls it and forwards them, but there was no command path *to* it at all —
the site could show its numbers and nothing about its configuration. The button
on its settings page said "relayed, only the node itself can read its own CLI",
which was true and useless in equal measure.

A monitor already logs in to that repeater and polls it every round, so it can
just as well walk its CLI. Since 1.9.0 it does, on request: the same eighteen
`get` commands over the air, one at a time, and one message at the end carrying
what came back.

That message is the expensive thing in this design, and the firmware bounds it
accordingly — on request only and never on a schedule, at most one sweep every
ten minutes, two seconds between commands, twelve per answer, and a stop after
three consecutive silences. The reasoning behind each of those numbers,
including which of the Home Assistant integration's values were copied and which
were deliberately not, sits above `MON_SET_FIRST_MS` in `MeshStatsNet.cpp`.

Two things about the result are worth knowing before reading the page:

- **A parameter that was asked and stayed silent is published as `null`** and
  shows up as "(geen antwoord)", overwriting whatever stood there. On purpose:
  the common failure is invisible otherwise. A repeater only runs a CLI command
  for a client with **admin** rights, so a monitor that logs in read-only —
  which is enough for everything else here, and is what the firmware header
  recommends — gets a login that succeeds and then eighteen silences. Grant
  `setperm <monitor-pubkey> 3` on the monitored repeater, or give the monitor
  its admin password, if those settings are meant to be readable here.
- **A sweep whose login never answered publishes nothing at all**, because it
  asked nothing and learned nothing. Throwing away values an earlier sweep did
  get would be the wrong kind of honest.

**Nothing is retained, and QoS stays 0.** A retained command is redelivered on
every reconnect, so the node would sweep its CLI on every boot and after every
WiFi drop for as long as the message sat on the broker — and nobody would connect
that to a button pressed once, weeks earlier. QoS 0 because the alternative buys
nothing: the node connects with a clean session, so the broker queues nothing
while it is offline. A node asleep on its power budget simply misses the message.

**Which is why the page checks before it promises.** `commanding.py` decides
whether a command can go out at all, and which one. It picks the node that will
receive it — the repeater itself, or the node that relays its statistics —
checks *that* node's firmware against the version the chosen route needs
(1.8.0 for a node reading its own CLI, 1.9.0 for a monitor reading somebody
else's), checks that the server is connected to the broker, and — for the
fallback route — that a poller fetched `/api/v1/commands` in the last 15
minutes. With no route open the button is disabled and the page says which of
those is missing. An older firmware does not subscribe to `cmd`, or subscribes
and refuses the argument, and in both cases publishing succeeds and vanishes;
that is the failure this check exists to prevent.

The route also carries *which* commands it can take. A monitor can be asked to
read another repeater's settings but not to publish a status message on its
behalf — it already forwards those figures every round — so the status button on
a relayed repeater stays on the poller route or stays grey.

### ACL

Add the read side to the node's account and the write side to the server's:

```
user meshstats
topic read meshcore/#
topic write meshcore/+/cmd

user node-e3d3f4d7ed01
topic write meshcore/e3d3f4d7ed01/stats
topic write meshcore/e3d3f4d7ed01/rx
topic read  meshcore/e3d3f4d7ed01/cmd
```

Without the read rule the node connects, subscribes, is refused by the broker,
and reports nothing about it — the button then looks exactly as dead as it did
before any of this existed. `wifi mqtt` on the node prints a `cmd=<accepted>/<refused>`
counter for precisely that reason.

Two rules on the server side, both in `_handle_settings`:

- **Settings come from the repeater itself, or from the node that already
  relays its statistics.** Until 1.9.0 this was "its own settings, full stop",
  on the grounds that the firmware never sent anything else — and that stopped
  being true the moment a monitor could sweep somebody else's CLI. What it costs
  is worth stating plainly: a client holding the shared broker credentials could
  already publish *statistics* for any repeater (see the note on identity at the
  top of `mqtt_ingest.py`, and the per-node ACL that closes it). Settings now
  cost that client one extra step — it must first become the node this
  repeater's statistics arrive through, and that shows up on the admin page as a
  changed `source_prefix`. Identity is compared through the repeater row, not
  the string, since topic and payload may spell the same key at different
  lengths, and the *previous* relay is read before `record_source` overwrites
  it — comparing afterwards would compare a publisher against itself.
- **An omitted parameter is not a deleted one, but an explicit `null` is a
  fact.** The firmware leaves out what it could not read, so this path calls
  `upsert_cli_settings(..., prune=False)`, and empty strings are discarded
  before the call. `null` is kept and stored as NULL: it means "asked, no
  answer", which is what the monitored sweep sends and what the page renders as
  "(geen antwoord)".

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
