# MQTT

*[Nederlands](nl/mqtt.md)*

MQTT is the recommended way for a node to reach the server. One connection stays
open; each measurement is a short publish on it. See
[`architecture.md`](architecture.md#why-mqtt) for why this replaced HTTP.

## Topics

Topic structure is `<prefix>/<node>/<leaf>`. Both firmwares build it identically
— `StatsPublisher::topicFor()` on the companion, `mqttTopic()` on the repeater:

```c
snprintf(out, max, "%s/%s/%s", _cfg.prefix,
         _node_hex[0] ? _node_hex : "node", leaf);
```

| Part | Value |
|---|---|
| `<prefix>` | Configured on the node, default `meshmanager` |
| `<node>` | 12 hex chars: the first **6 bytes** of the node's Ed25519 public key, lowercase |
| `<leaf>` | `stats` or `rx` from the node, `cmd` towards it |

Example: `meshmanager/e3d3f4d7ed01/stats`.

If the node has not resolved its own key yet, `<node>` falls back to the literal
string `node`. Seeing `meshmanager/node/stats` means the publisher started before
the mesh identity was available.

### Two prefixes at once

The server subscribes to **both** `meshmanager/…` and `meshcore/…`, and handles
them identically. That is not politeness — it is what makes the rename
survivable. Nodes and server are never upgraded at the same moment, and a node
can only publish on one prefix, so the side that can be taught to understand
both has to do it.

A command goes out on the prefix that node **reports itself on**, remembered on
arrival and stored in `repeaters.topic_prefix` so it survives a restart of the
site. A node never heard from gets it on both — two eight-byte messages are
cheaper than a button that does nothing.

`/admin` lists how many nodes arrive on which prefix. That is the number that
answers "may the fallback go?", and it is why it is on the page rather than in
someone's head. See [`migration.md`](migration.md).

### Server subscriptions

| Env var | Default | Purpose |
|---|---|---|
| `MM_MQTT_PREFIX` | `meshmanager` | The prefix this project owns. `meshcore` is always subscribed to as well |
| `MM_MQTT_TOPIC` | *(empty)* | An **extra** pattern for periodic statistics, on top of the prefixes above |
| `MM_MQTT_RX_TOPIC` | *(empty)* | An extra pattern for raw received packets |

All of them are subscribed at **QoS 0**.

The last two used to hold the full topic and are now empty by default: the
prefixes cover it. A value set there is added to the subscriptions rather
than replacing them, so an installation running under its own branch on a
shared broker keeps working across this rename instead of going deaf at the
moment it updates.

### The one topic the server publishes on

| Env var | Default | Purpose |
|---|---|---|
| `MM_MQTT_CMD_TOPIC` | `{prefix}/{node}/cmd` | One command for one node |

`{node}` is filled in with that node's own pubkey prefix, so a broker ACL can
bind a node's *read* permission to the same prefix as its *write* permission.
`{prefix}` is filled in with the prefix **that node** reports itself on. A
pattern without `{prefix}` is used exactly as written — whoever sets a fixed
topic means it.
See [Asking a node for something](#asking-a-node-for-something).

The clock synchronisation publishes on this same topic and has its own settings,
because the question it answers is not "which topic" but "may we speak at all":

| Env var | Default | Purpose |
|---|---|---|
| `MM_CLOCKSYNC_ENABLED` | `1` | Send the time to the nodes at all |
| `MM_CLOCKSYNC_HOURS` | `24` | Hours between two rounds |
| `MM_CLOCKSYNC_MAX_ERROR_S` | `10` | Uncertainty the kernel may have about its own clock before we stop believing it |
| `MM_CLOCKSYNC_MAX_JUMP_S` | `30` | Wall-clock jump against the monotonic clock that counts as a jump rather than a correction |

See [Setting the clock](#setting-the-clock).

### Who is speaking, and who they are speaking about

Both topics are parsed for the `+` segment. It answers a different question from
the payload, and the two are kept apart on purpose:

| | Answers | Comes from |
|---|---|---|
| **Publisher** | which node sent this message | the topic, `meshmanager/<node>/…` |
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

### Three producers, one schema

The same shape is built in three places, and they do **not** send the same set of
fields. Knowing which one you are looking at is the difference between "this node
does not report duplicates" and "this node is not the kind of node that reports
duplicates".

| Producer | Function | Subject |
|---|---|---|
| **Companion** | `MyMesh::fillStatsJson()` in `examples/companion_radio` | itself |
| **Repeater** | `MyMesh::fillStatsJson()` added by `repeater-hooks.patch` | itself |
| **Monitor relaying a repeater** | `publishMonitorRound()` in `MeshManagerNet.cpp` | *another* repeater |

All three publish on `<prefix>/<node>/stats`. The first two describe the node in
the topic; the third describes somebody else and says so in
`repeater.pubkey_prefix`.

### Fields the node sends

| Key | Type | Unit | Companion | Repeater | Relayed | Source |
|---|---|---|---|---|---|---|
| `repeater.pubkey_prefix` | string | — | ✓ | ✓ | ✓ | first 6 bytes of the public key, hex. Optional on the first two: left out, the server reads it from the topic |
| `repeater.name` | string | — | ✓ | ✓ | ✓ | `_prefs.node_name`, or the monitor's name for that entry. JSON-escaped since 1.9.1 |
| `repeater.fw` | string | — | | ✓ | | `FIRMWARE_VERSION` — MeshCore's version |
| `repeater.fw_meshmanager` | string | — | | ✓ | | `MESHMANAGER_VERSION`, empty when the module is not compiled in |
| `online` | bool | — | ✓ | ✓ | ✓ | always `true`; a liveness marker |
| `bat` | float | V | ✓ | ✓ | ✓ | cell voltage |
| `battery_percentage` | int | % | | ✓ | ✓ | shared curve, see `meshmanager_batt_percent()` |
| `ch1_voltage` | float | V | | ✓ | | the same cell voltage under a telemetry channel name |
| `uptime` | float | **days** | ✓ | ✓ | ✓ | 5 decimals |
| `noise_floor` | int | dBm | ✓ | ✓ | ✓ | omitted unless negative |
| `last_rssi` | int | dBm | ✓ | ✓ | ✓ | omitted unless negative |
| `last_snr` | float | dB | ✓ | ✓ | ✓ | omitted when the node has received nothing |
| `airtime` / `rx_airtime` | float | **minutes** | ✓ | ✓ | ✓ | TX and RX |
| `nb_recv` / `nb_sent` | int | count | ✓ | ✓ | ✓ | radio-level packet counters |
| `sent_flood` / `sent_direct` | int | count | ✓ | ✓ | ✓ | `Dispatcher` counters |
| `recv_flood` / `recv_direct` | int | count | ✓ | ✓ | ✓ | `Dispatcher` counters |
| `recv_errors` | int | count | ✓ | ✓ | ✓* | `getPacketsRecvErrors()` |
| `direct_dups` / `flood_dups` | int | count | | ✓ | ✓* | duplicates suppressed by the dedup table |
| `err_events` | int | count | | ✓ | ✓* | `_err_flags` |
| `tx_queue_len` | int | count | ✓ | ✓ | ✓ | `getOutboundTotal()` |
| `neighbor_count` | int | count | | ✓ | ✓ | how many neighbours the node knows, which is not the same as how many it reported |
| `mcu_temperature` | float | °C | | ✓ | | ESP32 die temperature |
| `ch<N>_temperature` / `ch<N>_voltage` | float | °C / V | | | ✓ | decoded from the monitored node's CayenneLPP telemetry |
| `freq` | float | MHz | ✓ | ✓ | | `_prefs.freq` |
| `sf` / `cr` | int | — | ✓ | ✓ | | spreading factor, coding rate |
| `tx` | int | dBm | ✓ | ✓ | | `_prefs.tx_power_dbm` |
| `neighbors` | array | — | | ✓ | ✓ | see below |
| `via` | string | — | | | ✓ | 12 hex chars: the node that relayed this. **Currently ignored by the server** — see below |

`✓*` on the relayed column means "only when the monitored node's firmware is new
enough to have sent those bytes" — see the struct-length rule below.

Watch the units. `uptime` is **days**, not seconds; `airtime` is **minutes**, not
milliseconds. Both are pre-divided on the node so the server stores what it
displays.

#### Why a field can be missing, and why that is deliberate

**A metric that is not available is left out, never sent as `0`.** JessaZH
reported `noise_floor 0`, which drew a line diving to zero on a graph where a gap
belonged — and cost somebody an afternoon working out which. The tests are about
physics, not tidiness:

- a noise floor or an RSSI in dBm is always negative; `0` means the radio driver
  never filled it in;
- an SNR of 0.0 dB is a perfectly real reading, so it is suppressed only when the
  node has received nothing at all to have an SNR of;
- a board reporting no cell voltage gets neither `bat` nor `battery_percentage`.

**Counters are never filtered.** Zero packets sent is a fact, not a gap.

For a *relayed* reading there is a second reason a field can be absent, and it is
worth understanding because it looks identical from the outside. `RepeaterStats`
grew over MeshCore releases, and an older node answers with a shorter struct. The
monitor therefore checks how many bytes actually arrived before emitting each
field (`ST_HAS()` in `publishMonitorRound()`). So a missing `flood_dups` on a
relayed repeater means either "that firmware does not have it" or "it was never
filled in" — and both are better than a confident zero.

#### `mcu_temperature` is not `ch1_temperature`

They are not the same measurement and must never be merged again. `mcu_temperature`
is the ESP32 die, which with WiFi running sits 20–30 degrees above its
surroundings — a node reported 51 °C while it was about 25 °C outside. Under a
`ch1_temperature` name a reader takes that for an ambient reading and concludes
the roof is on fire. `ch1_temperature` stays reserved for an actual sensor.

For a relayed reading the channel numbers are the far side's own, not ours. On a
MeshCore repeater channel 1 is its own board, so `ch1_temperature` there is the
die again — but that is the far side's naming to make, and reinterpreting it here
would be inventing data. See
[`protocol.md`](protocol.md#110-telemetry-and-cayenne-lpp).

#### `neighbors`

```json
"neighbors": [
  {"prefix": "a1b2c3", "snr": -7.25, "seen_min": 12}
]
```

Each entry becomes its own time series under the metric key
`neighbor_<prefix>`. A relayed neighbour entry carries **no name field**: the
monitored repeater's reply contains only key, age and SNR, and leaving the name
out means the server keeps whatever name it already had rather than overwriting
it with a blank.

`neighbor_count` and the length of `neighbors` are allowed to differ. The count
is what the node knows; the array is what fitted in the message. The array
truncates rather than failing — a partial neighbour list is useful, a dropped
stats message is not.

#### `via`, and why the server does not read it

Both relayed message types — `publishMonitorRound()` and
`publishMonitorSettings()` — append a top-level `"via"` holding the relaying
node's own 12-hex-character id. It is redundant with the topic, which already
names the publisher, and **`mqtt_ingest.py` does not read it**: the server
derives the relay from the `+` segment and records it as `source_prefix`.

That is not a bug on either side. The topic is the authority for "who spoke",
because it is the thing a per-node broker ACL can actually bind. A payload field
claiming a relay would be a claim, not a fact. `via` is there for anyone reading
the raw stream — a sniffer, a second subscriber, a log — where the topic may
already have been stripped.

Do not build a server-side feature on `via` without first deciding what happens
when it disagrees with the topic.

#### Message size

The repeater sets `PubSubClient`'s buffer to `MQTT_PUB_MAX` = **5120 bytes** at
startup, because the neighbour array can be long and a raw packet is over 500
hex characters. The 256-byte default would make `publish()` **silently refuse**
these messages — success on this side, nothing at the broker. Anything longer
than the buffer is truncated at the source (fewer neighbours) rather than being
refused here.

### Fields the server also accepts

The HTTP ingest path and the Home Assistant pusher send more. All of it is valid
over MQTT too, since both go through the same handler:

| Key | Meaning |
|---|---|
| `ts` | ISO timestamp `YYYY-MM-DDTHH:MM:SSZ`; defaults to server receive time |
| `force` | bool; bypass the heartbeat dedup and always write a sample |
| `neighbors` | array of `{prefix, name, snr, seen_min}` |
| `settings` | object of CLI parameters; see below |
| `filter` | the packet filter's state and drop counters; see below |

Each neighbour also becomes its own time series under the metric key
`neighbor_<prefix>`.

### `filter` — the packet filter

Rides along with **every** statistics message, not with the daily sweep:

```json
"filter": {
  "on": true, "disarmed": false, "hash": 1, "malformed": true,
  "channels": 1, "blocked_types": 0, "passed": 91422, "exempt": 12,
  "drop": {"type": 0, "hops": 41, "rate": 308, "hash": 0,
           "kanaal": 77, "misvormd": 4}
}
```

About 160 bytes, and the frequency is the point. A filter makes a node useless
without making it unreachable — it still answers, still advertises, still shows
green — so a state that only travelled once a day would be a day late. The rule
tables (twelve hop limits, twelve rate limits, the channel list) are **not** in
here: two kilobytes that change once a month belong behind a request from
somebody about to change them, which is `GET /api/filter` on the node.

The counters go through the ordinary metric machinery as `filter_dropped`,
`filter_passed`, `filter_exempt`, `filter_on` and `filter_drop_<reason>`, so they
graph and age like everything else.

The server takes this object **only when the message is about the publisher
itself**. A node may legitimately relay figures about a repeater it monitors, but
not its filter state: a monitored repeater never reports its filter over the
radio, so a block claiming otherwise cannot be true. Refused, and logged.

See [`packet-filter.md`](packet-filter.md).

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
  "lat": "50.92", "lon": "5.352", "region.home": "be", "region.default": "be",
  "cmd:region": "*\n eu F\n  bx F\n   be^ F\n    be-vbr F"
}
```

Nineteen keys, one per entry in `SET_PARAMS` in `MeshManagerNet.cpp`. Eighteen of
them are one-line values; `cmd:region` is a tree and is the reason `\n` survives
JSON escaping at all — see below and
[`firmware.md`](firmware.md#set_params--the-parameter-table).

The keys are whatever the sweep table in the firmware names them — the server
stores and shows every key it receives, known or not. The parameter list on the
admin settings page steers the Home Assistant look-up only; a parameter added
there does **not** reach nodes publishing over MQTT until the firmware's own
table (`SET_PARAMS` in `MeshManagerNet.cpp`) asks for it too.

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

### `cfgspec` — the node's writable parameter table

Rides along with the settings sweep above, and only with it: this table changes
only when different firmware goes onto the node, so paying for it in every
message would be paying monthly for something that changes yearly.

```json
"cfgspec": {
  "name": "text,0,0,1,0,0",
  "flood.max": "int,0,64,2,0,0",
  "loop.detect": "enum,0,0,2,0,0,off|minimal|moderate|strict",
  "tx": "int,0,30,3,0,0"
}
```

One string per parameter, in a fixed order:
`<kind>,<lo>,<hi>,<risk>,<reboot>,<secret>[,<choices>]`. Compact on purpose —
twenty-seven objects with seven keys each are two kilobytes against nine hundred
bytes this way, and it travels in a message with a fixed buffer.

**Why this exists at all.** The server builds its write form from the node's own
list and deliberately keeps no parameter table of its own; until 2.8.0 that list
came only from `GET /api/cfg`, which is exactly what a node the server has no web
login for cannot answer. Without `cfgspec` the MQTT write path would exist and be
unusable: the site would not know a parameter's risk class, would assume the
heaviest (`nodeconfig.risk_of`), and would block everything.

It is still the node's list and not a table invented here — only a second
*source* for the same list. It is accepted only when the message is about the
publisher itself: a parameter table is the compiled-in list of the publishing
firmware, so a block claiming to hold another node's cannot be true. That matters
more than it sounds, because the site hangs its confirmations and its permissions
on those risk classes.

### `cfgset` — the outcome of the last write over `cmd`

Rides along with every statistics message once there has been one, because it is
a few hundred bytes and a missed publication should not lose it:

```json
"cfgset": {
  "seq": 3, "ok": 1, "param": "flood.max", "asked": "12",
  "applied": "12", "exact": 1, "reboot": 0, "msg": ""
}
```

`applied` is what the node read back afterwards, not what was asked — the same
discipline as `POST /api/cfg`, and for the same measured reasons (`set lat abc`
is a bare `atof()`, `advert.interval 61` stores 60). `exact` says whether those
two agree. `seq` counts writes since boot, so the server can tell this outcome
from the previous one.

A **refusal is reported here too**, with `ok: 0` and the reason in `msg`. Silence
would be indistinguishable from a node asleep on its solar budget, and then a
typo looks exactly like a flat battery. Same publisher rule as `cfgspec`.

**Why it is not on a topic of its own.** It was going to be
`meshmanager/<node>/settings`, until a check of `mqtt_ingest.py` showed this
subscriber listens to exactly two patterns. A third topic would have been
accepted by the broker and then dropped unread — the same failure that lost the
monitored repeaters once before. Adding a topic means adding it to `MM_MQTT_*`
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

So the server publishes one word on `meshmanager/<node>/cmd`:

| Word | The node does |
|---|---|
| `settings` | reads its CLI parameters now, and publishes them with the statistics message it sends as soon as the sweep finishes |
| `settings <key>` | logs in to a repeater it *monitors*, reads **that** repeater's CLI parameters over LoRa, and publishes them under that repeater's name (nodefirmware 1.9.0) |
| `status` | publishes a statistics message immediately |
| `time <epoch>` | sets its own clock to that UNIX time in UTC seconds, then checks the clocks of the repeaters it monitors over LoRa (nodefirmware 1.10.0) |
| `set <param> <value>` | sets one of **its own** CLI parameters, reads it back, and reports the outcome with the statistics message it publishes immediately after (nodefirmware 2.8.0) |

The answer comes back on the ordinary `stats` topic. Nothing else in the ingest
path changes, and a receiver that knows nothing about `cmd` still works. `time`
is the exception: it produces no message at all, only a change on the node. What
happened is readable with `wifi clock` on the node and on the site's admin page.

**The firmware accepts those four words and nothing else.** Not a prefix test,
not a fallthrough to `handleCommand()` — an exact match against a list of four.
The telnet console on the node does hand its input to the CLI, but that console
asks for a password over a link the operator controls, while this topic is
reachable by anyone holding broker credentials. These repeaters hang on roofs and
run off solar panels; one `reboot` in a loop is enough to lose one. The first two
words only make the node say what it would have said by itself, so the worst an
attacker on the broker achieves with them is a statistics message, at most one
every 30 seconds (`MQTT_CMD_MIN_GAP_MS`).

The arguments do not widen that, and they are worth separating because they are
different kinds of thing.

The one on `settings` never becomes text that reaches a CLI: it selects a single
entry from the node's monitor list, and the commands then sent are the
compiled-in parameter table. That list is writable only from the admin page and
the mesh CLI, both password-protected, so the most a broker account can do with
it is read out a repeater the operator already chose to monitor — at most once
every ten minutes.

The one on `time` is a number, checked against a window of years at both ends
(2025–2100, in `mqtt_ingest.py` and again in `MeshManagerNet.cpp`) and applied by
code that only ever moves a clock **forward**. So this word does grant a real
capability that the other two do not: it changes state. Named plainly, because
it is the one that deserves an ACL: an attacker on the broker can push a node's
clock to any time between now and 2100, and that cannot be walked back over the
air. The reason is in the next section.

The one on `set` is the word that genuinely raises the ceiling of this topic, and
it is worth being exact about how far. It is a **larger allowlist, not a
passthrough**, and the node does the checking:

- the parameter must be one of the twenty-eight names compiled into `CFG_PARAMS`.
  The command is then built from *the table's* key, so no text out of the message
  ever becomes a command — only the value travels, and it is always the last
  word, so there is no separator a second command could start after;
- the value must pass `cfgCheckValue()`, the same sieve `POST /api/cfg` and
  `POST /api/moncfg` use. One sieve, not three that can drift apart;
- the risk class must not exceed `CFG_MQTT_MAX_RISK`, which stands at *changes
  behaviour noticeably* and not at *can cut this node off*. Radio parameters are not on
  that table at all since 2.6.0, so this path cannot offer them either.

A node that gets an unknown parameter or an out-of-bounds value refuses it,
counts it, and **says so** — see `cfgset` above. Silence would be
indistinguishable from a node asleep on its solar budget, which is the one thing
this reply may not be.

Why the ceiling sits lower here than on the two HTTP write paths comes down to
who stands on the other side: those have an authenticated counterparty, this one
has whoever the broker let in. On a broker with one shared account that is every
node speaking to it. The full weighing, and what it means for your broker setup,
is in [`security.md`](security.md#changing-a-setting-three-transports).

### The format of a `cmd` payload

The payload is the bare word, optionally followed by one argument, as plain
text. No JSON, no envelope, no trailing structure.

| Rule | Value | Consequence |
|---|---|---|
| Maximum length | 96 bytes (`MQTT_CMD_MAX`) | Longer than the longest legal command, so a payload that does not fit is recognisable as *too long* rather than truncated into something that happens to match. Over-length payloads are refused and counted |
| Leading/trailing whitespace | trimmed | A publisher that appends a newline is not punished for it |
| Argument separator | one space or tab | `settings a1b2c3d4`, `time 1786665600` |
| Arity | checked per word | `status <anything>` is **refused**, not run as `status`. A publisher sending an argument to a command that takes none has misunderstood something, and running it anyway hides that from both ends |
| Minimum gap | 30 s (`MQTT_CMD_MIN_GAP_MS`) | Commands arriving inside the gap are **dropped, not queued** — "do it now" loses its meaning if it waits |
| Retain | must be `false` | See below |
| QoS | 0 | See below |
| Concurrency | one word at a time | If a word is already waiting to be processed, the next one is dropped |

Examples, exactly as they go on the wire:

```
settings
status
settings e3d3f4d7ed01
time 1786665600
set flood.max 12
set name Dak Noord
```

`set` is the one word with two arguments: the parameter name, then the value.
The value is everything after the parameter, spaces included — `name` and
`owner.info` consist of little else.

`time` takes UNIX epoch **seconds in UTC**, parsed with `strtoul` (not `atol` —
the epoch passes 2³¹ in 2038 and these nodes may well still be on their roofs). A
trailing non-digit means the argument was not a bare number, and a number with
something stuck to it is a mistake upstream, not a time: it is refused and
counted.

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
(`MM_CLOCKSYNC_HOURS`, default 24). The format was not chosen: it is what
`CommonCLI::handleCommand` parses in its `time ` branch — `_atoi` of the rest of
the line, straight into `setCurrentTime`, UNIX seconds in UTC.

Only nodes that **publish here directly** are addressed by the schedule. A
relayed repeater gets its time from its monitor over LoRa, which is exactly what
the command makes that monitor do, and is the only path there is to it. The
schedule deliberately does not walk relayed repeaters: two of them behind one
monitor would send that monitor the same message twice, and it would pay for two
clock rounds where one was asked for.

### The button

Every repeater's admin page has **"Klok nu synchroniseren"**, for when waiting a
day is not an option — a node that just came back from a power cut stamps
everything it says with a date from 2024 until the next round.

It is not a second path to the broker. The button calls the same `sync_now` in
`clocksync.py` that the scheduler goes through, so the clock guard below, the
epoch window and the firmware version gate all apply unchanged. The drift
threshold and the refusal for a node running fast are not re-implemented either:
they live in the firmware, and this is the same message.

Two things are worth knowing before pressing it:

- **On a relayed repeater it does not target that repeater alone.** The message
  goes to the node that monitors it, and that node checks the clocks of *every*
  repeater it monitors. There is no argument to narrow that down, and that is not
  an omission — a clock round costs one command and one reply per monitored
  repeater, so walking the whole list is about a fifth of one ordinary poll
  round. The page says so rather than implying the button is aimed.
- **It refuses inside the hour**, reporting when the next one is possible. Not
  for safety: `MON_CLK_MIN_GAP_MS` in the firmware already makes it impossible to
  occupy the band by clicking, however often you click. For honesty — within the
  hour the node would set only its own clock, which the previous message already
  did, and skip the round that actually matters, while the page said "sent". The
  one exception is a node that rebooted since, detected from its `uptime` metric,
  because that is precisely when waiting is the worst possible answer.

What the site cannot show is the measured drift. The node measures how far each
monitored repeater is off, and corrects only past two minutes, but it publishes
that nowhere; it is readable with `wifi clock` on the node itself. Getting it
onto this page would mean putting it in the stats message — a firmware change.
The button itself needs none beyond the 1.10.0 that `time` already requires.

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
> time is demonstrably correct". **If you run the server in a container, the
> correctness of every clock in this mesh ultimately rests on the NTP
> configuration of the machine underneath it** — if that is wrong, all of this
> runs neatly, measurably and completely wrong along with it.

Two checks were considered and rejected. Cross-checking against timestamps from
the mesh is circular: the nodes we would check against are the nodes we set, so
agreement only proves our own message arrived — and the `rx` message carries `t`
as an uptime counter, not a wall clock, so the usable source is not even there.
Querying an external time source does not work either: this server sits behind
VPN/LAN with no outbound reference, so that check would pass in development and
report "unreachable" forever on the real machine, which is a check that gets
switched off within a week.

What the node does with it is in `MeshManagerNet.cpp` above `MON_CLK_FIRST_MS`: it
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
were deliberately not, sits above `MON_SET_FIRST_MS` in `MeshManagerNet.cpp`.

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
- **`cmd:region` is a tree, not a value** (nodefirmware 1.11.0). It is the one
  parameter whose answer spans lines, and whose line breaks *and indentation*
  carry the meaning: indentation is parent/child nesting, `^` marks the home
  region, a trailing ` F` means flooding is allowed there and its absence means
  it is denied. It arrives as **one** text message — MeshCore caps the tree at
  160 bytes itself (`handleRegionCmd` calls `exportTo(reply, 160)`) and sends
  the whole reply in a single datagram — so there is no multi-packet collection
  on this path. A tree larger than that is cut on the far side, not here.
  The key is `cmd:region` and not `region`: `cmd:<x>` is this site's notation
  for "run `<x>` literally instead of `get <x>`", and the row in `repeater_cli`
  is named after the configured parameter. Publishing it as `region` would have
  created a second row beside the existing one and left the original ageing.

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


> **During the rename**, every rule below needs its `meshcore/…` twin as
> well: a node publishes on the old prefix until it is flashed and on the new
> one after. An ACL that knows only one of the two lets exactly one of those
> two states die in silence — the node reports a successful publish and the
> broker drops the message. `init-passwd.sh` and `add-node-user.sh` already
> generate both. See [`migration.md`](migration.md).
```
user meshmanager
topic read meshmanager/#
topic write meshmanager/+/cmd

user node-e3d3f4d7ed01
topic write meshmanager/e3d3f4d7ed01/stats
topic write meshmanager/e3d3f4d7ed01/rx
topic read  meshmanager/e3d3f4d7ed01/cmd
```

Since nodefirmware 2.8.0 the `cmd` topic can also *change a setting*
(`set <param> <value>`), which makes "who may publish on
`<prefix>/<node>/cmd`" the deciding question rather than a tidiness one. With one
account per node and the rules above, the answer is "the site, and nothing else".
With a **single shared account** — which is what a default `init-passwd.sh`
deployment runs on — every node holds credentials that may publish on every
other node's `cmd` topic. Read
[`security.md`](security.md#the-broker-is-now-the-deciding-question) before
relying on that write path.

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

> **Still settling.** The firmware side is in service and the server-side
> decoder `server/app/packets.py` now exists and is called from
> `mqtt_ingest.py`. Field names in the *decoded* result may still change; the
> five keys of the message itself, below, have not.

The intent: the node does not parse anything. It hex-encodes each frame exactly
as it came off the radio and lets the server decode it using
[`protocol.md`](protocol.md).

```json
{"t": 1284511, "snr": -4.25, "rssi": -94, "len": 129, "fwd": 0, "why": "hops",
 "raw": "1100ab..."}
```

| Key | Meaning |
|---|---|
| `t` | node `millis()` at receipt — **uptime, not wall clock** |
| `snr` | dB, reconstructed from the radio's SNR×4 integer |
| `rssi` | dBm |
| `len` | frame length in bytes |
| `fwd` | what this node's packet filter did: `1` forwarded, `0` refused. **Absent when the filter did not judge this packet** (2.7.0+, repeater only) |
| `why` | the reason it was refused — `type`, `hops`, `rate`, `hash`, `kanaal`, `misvormd`. Only present with `"fwd": 0` (2.7.0+) |
| `raw` | lowercase hex, `len * 2` characters |

`fwd` is absent far more often than it is present, and that absence is a value in
its own right: the filter only ever judges *flood* packets it is asked to
forward. A packet addressed to this node, a direct-routed one, or a frame that
never parsed never reaches `allowPacketForward()` at all. Reading a missing `fwd` as
"forwarded" would turn "nobody looked" into a claim.

The verdict rides inside the packet's own message rather than arriving as a
second one keyed by a packet hash, and that is worth stating because the obvious
design is the other one. Reception and the forwarding decision happen inside the
same processing of the same packet, while the packet is still sitting in the rx
ring waiting to be published — so the verdict catches up with its own packet
before it leaves. That removes a hash both sides must compute identically, an
ordering problem in both directions, and verdicts about packets the server never
received. See `meshmanager_on_forward_verdict()`.

The design constraints are the same on both firmwares, but **the numbers are
not**, and conflating them is easy:

| | Companion (`StatsPublisher`) | Repeater (`MeshManagerNet`) |
|---|---|---|
| Ring size | `STATS_RX_QUEUE` = **4** slots | `MQTT_RX_QUEUE` = **8** slots |
| Slot size | `STATS_RX_MAX_LEN` = 255 (a full MTU, 264 B with padding) | `MQTT_RX_MAX_LEN` = 255 |
| Static RAM | ~1 kB | ~2 kB |
| Publishes per `loop()` pass | **1** | `MQTT_DRAIN_MAX` = **4** |
| Publish buffer | `setBufferSize(255 * 2 + 128)` | `setBufferSize(MQTT_PUB_MAX)` = 5120 |

Four slots on the companion is a deliberate trade recorded at the source:
forwarding is best-effort — the queue is thrown away in its entirety as soon as
the broker is unreachable — and RAM is worth more there than absorbing peaks.
Going back to eight would cost 1056 bytes of static RAM. On the repeater eight
slots buys roughly one burst of traffic; beyond that it would rather lose packets
than memory.

The shared rules:

- **The receive callback only copies.** Publishing from inside the receive loop
  would hold up reception. The same discipline as the `cmd` callback and the web
  server's apply flags.
- **Queue full → the packet is dropped** and a counter increments. Losing a
  packet is better than stalling the mesh.
- **No broker connection → the whole queue is flushed** and counted as drops,
  rather than sending a burst of stale packets on reconnect.
- **`setBufferSize()` is not optional.** A raw frame becomes over 500 hex
  characters, and PubSubClient's 256-byte default would make `publish()`
  *silently refuse* — the same shape of failure as the 1.3.0 wrong-topic bug.

On the repeater, forwarding is additionally gated on the battery: above
`bat_live` percent a received packet goes out immediately, below it the node
waits. See [`firmware.md`](firmware.md#43-power-management).

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

<a id="node-side-configuration"></a>
## Node-side configuration (companion)

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

## Node-side configuration (repeater)

The repeater keeps its settings in `/msnet.json` on SPIFFS, not
`/stats_cfg.json`, and they are set in three interchangeable ways: the admin page
at `http://<node-ip>/` (`POST /api/mqtt`), the mesh/serial/telnet CLI, or a
restored backup.

| Field | CLI | Default | Notes |
|---|---|---|---|
| `mqtt_host` | `wifi mqtt host <name>` | *(empty)* | Empty = publisher does nothing. A hostname costs a DNS wait on every connect attempt |
| `mqtt_port` | `wifi mqtt port <n>` | `1883` | 1–65535 |
| `mqtt_user` | `wifi mqtt user <name>` | *(empty)* | Empty = connect anonymously |
| `mqtt_pass` | `wifi mqtt pass <word>` | *(empty)* | |
| `mqtt_prefix` | `wifi mqtt prefix <p>` | `meshcore` | Empty resets to `meshcore` |
| `mqtt_enabled` | `wifi mqtt on` / `off` | off | Master switch |
| `mqtt_rx` | `wifi mqtt rx on` / `off` | — | Forward every received packet |

The publish **interval is not a single number here.** It follows the battery
through a rule table and the clock through a night window; see
[`firmware.md`](firmware.md#43-power-management). In power-save mode the interval
also decides how often the radio wakes, which is why it has a higher floor there
(60 s) than in always-reachable mode (10 s).

`wifi mqtt` with no argument prints the status line that answers most questions
at once:

```
verbonden, broker=<host>:1883, prefix=meshcore, rx=aan,
stats=412 pkt=9021 drop=3 cmd=7/2
```

`cmd=<accepted>/<refused>` is the counter that separates "the site never asked"
from "the broker refused my subscribe" from "it ran and nothing changed".

Client id is `meshcore-<node_hex>` on both firmwares — derived from the public
key, so two nodes never collide.

Reconnect behaviour on the repeater: `MQTT_RETRY_MS` = **15 s** between attempts.
A broker that does not answer costs a full socket timeout per attempt, and that
time comes straight out of the mesh. `setSocketTimeout(4)` and
`setKeepAlive(60)` bound it further.

**TLS is not supported here either.**

## Server-side configuration

| Env var | Default | Notes |
|---|---|---|
| `MM_MQTT_HOST` | *(empty)* | **Empty disables MQTT ingest entirely** |
| `MM_MQTT_PORT` | `1883` | |
| `MM_MQTT_USER` | *(empty)* | Empty = connect anonymously |
| `MM_MQTT_PASS` | *(empty)* | |
| `MM_MQTT_PREFIX` | `meshmanager` | `meshcore` is subscribed to as well |
| `MM_MQTT_TOPIC` | *(empty)* | Extra pattern, on top of the prefixes |
| `MM_MQTT_RX_TOPIC` | *(empty)* | Extra pattern; raw packets are in development |

Note the Docker Compose file supplies different effective defaults:
`MM_MQTT_HOST=mosquitto` and `MM_MQTT_USER=meshmanager`.

The subscriber runs on a daemon thread with client id `meshmanager-ingest`,
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
# edit .env: set MM_MQTT_USER and MM_MQTT_PASS
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
topic write meshmanager/e3d3f4d7ed01/stats
topic write meshmanager/e3d3f4d7ed01/rx
```

`stats` and `rx` are listed separately rather than `meshmanager/<prefix>/#`, so a
node cannot create topics the server may later use for something else.

Put the printed credentials on the node's management page and restart the broker:

```bash
docker compose restart mosquitto
```

#### Finishing the migration

`init-passwd.sh` leaves the shared account with `topic write meshmanager/#` so
nothing breaks while nodes are still on it. That line is also what keeps
impersonation possible. Once every node has its own account:

```
user meshmanager
topic read meshmanager/#
# topic write meshmanager/#   <- delete this line
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
  -t meshmanager/e3d3f4d7ed01/stats -m '{"metrics":{"online":true}}'

# refused by the broker
mosquitto_pub -h <broker> -u node-e3d3f4d7ed01 -P <pass> \
  -t meshmanager/aabbccddeeff/stats -m '{"metrics":{"online":true}}'
```

## Troubleshooting

| Symptom | Where to look |
|---|---|
| `/admin` shows MQTT disabled | `MM_MQTT_HOST` is empty |
| Connected, zero messages | Node `enabled` off, or `host` empty on the node |
| Node page says "not connected" | Broker credentials; the node retries every 15 s |
| Messages counted, no repeater appears | Errors counter and `last_error` in `/admin`; usually a missing `pubkey_prefix` |
| Repeater appears with a wrong name | Name comes from the payload, not the topic — check `repeater.name` on the node |
| A repeater shows "via `<prefix>`" in /admin | Another node published its stats. Expected for relayed repeaters; unexpected otherwise |
| Node connects but nothing is published | ACL: the account has no `topic write` block, or the topic prefix does not match it. Check the broker log |
| Broker refuses to start | `mosquitto/acl` is missing or unreadable — run `init-passwd.sh` |
| Graph has gaps | Expected with QoS 0. Check WiFi stability, and `heartbeat_min` |
| Two servers keep disconnecting | Both use client id `meshmanager-ingest`. Run one. |

To watch the traffic directly:

```bash
mosquitto_sub -h <broker> -u <user> -P <pass> -t 'meshmanager/#' -v
```
