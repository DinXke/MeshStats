# Firmware modifications

*[Nederlands](nl/firmware.md)*

MeshManager ships a set of changes to the [MeshCore](https://github.com/meshcore-dev/MeshCore)
firmware. They fall into two groups:

- **Companion node** (`examples/companion_radio`) — multiple simultaneous WiFi
  clients, a channel-counter fix, and the stats publisher with its web chat
  client.
- **Repeater** (`examples/simple_repeater`) — `MeshManagerNet`: WiFi with AP
  fallback, a management page, OTA over your normal network, a telnet console on
  the MeshCore CLI, MQTT publishing, monitoring of other repeaters, clock
  synchronisation, and filesystem backup/restore.

Everything is opt-in at build time. Without the flags, you get stock MeshCore.

| File | What it changes |
|---|---|
| `src/helpers/esp32/SerialWifiInterface.{h,cpp}` | Multiple simultaneous WiFi companions |
| `src/helpers/BaseChatMesh.cpp` | Channel-slot reuse |
| `examples/companion_radio/StatsPublisher.{h,cpp}` | MQTT publisher + management page |
| `examples/companion_radio/page.html`, `gen_page.py`, `StatsPage.h` | The web client, its build step and its generated output |
| `examples/companion_radio/MyMesh.{h,cpp}` | `fillStatsJson()`, `fillNodeIdHex()`, raw-packet hook |
| `examples/companion_radio/main.cpp` | Wires the publisher in |
| `examples/simple_repeater/MeshManagerNet.{h,cpp}` | The repeater network module |
| `repeater-hooks.patch` | The edits in `simple_repeater` (including its `fillStatsJson()`) |
| `meshmanager.patch` | Everything, as one patch |

Two version numbers travel together and must not be confused. `FIRMWARE_VERSION`
is MeshCore's; `MESHMANAGER_VERSION` (`MeshManagerNet.h:138`) is this module's, and
the two move independently. `ver` prints both, and both appear in every stats
payload as `repeater.fw` and `repeater.fw_meshmanager` — because when something is
wrong, the first question is which of the two you are looking at.

---

## 1. Multiple companions on one node

### The problem

Stock `SerialWifiInterface` keeps exactly one client:

```cpp
auto newClient = server.available();
if (newClient) {
    client.stop();      // the existing companion is kicked off
    client = newClient;
}
```

Connect your phone and Home Assistant drops. Connect Home Assistant and your
phone drops.

### The change

`WIFI_MAX_CLIENTS` slots (default 4). Each slot holds a `WiFiClient` **and its
own `FrameHeader`**:

```cpp
struct ClientSlot {
    WiFiClient client;
    FrameHeader header;
};
```

Per-slot header state is not a detail. A companion frame is a 3-byte header plus
a payload that may arrive across several TCP reads. With one shared header, two
clients mid-frame overwrite each other's length and both connections desync.

New connections take a free slot. Only when every slot is busy is one dropped,
and then it is the one `next_poll` points at.

Inbound frames are polled round-robin so a chatty client cannot starve the rest:

```cpp
for (int n = 0; n < WIFI_MAX_CLIENTS; n++) {
    int i = (next_poll + n) % WIFI_MAX_CLIENTS;
    ...
}
```

### Targeted replies — the part that matters

Broadcasting everything to everyone does not work. Companion clients run
request/response state machines; feed one client another's `RESP_CODE_CONTACT`
stream and it desynchronises.

So outbound frames are routed. `reply_slot` is reset at the top of every
`checkRecvFrame()` and set when a command is handed to the mesh:

```cpp
// checkRecvFrame(), before accepting anything:
reply_slot = -1;
...
// after reading a frame from slot i:
reply_slot = (int8_t)i;
```

and every write records it:

```cpp
send_queue[send_queue_len].dest_slot = reply_slot;
```

`dest_slot >= 0` → that client only. `dest_slot == -1` → every connected client.

This works because the companion firmware is single-threaded and synchronous:
a command is fully handled, writes included, before the next frame is read.
Anything the mesh writes right after a command *is* that command's reply.
Anything written at any other time — adverts, incoming messages, ACKs — is
unsolicited and goes to everyone.

Send queue depth is `FRAME_QUEUE_SIZE` = 4. A full queue drops the write and
returns 0.

Cost: roughly 2–3 kB of RAM per slot.

### If you cannot flash

`proxy/mc-proxy` does the fan-out outside the node, against unmodified firmware.
It cannot do reply routing — it has no view of the node's internal ordering — so
it broadcasts everything and compensates by caching the `SELF_INFO` frame and
answering each client's `CMD_APP_START` locally. See
[`protocol.md`](protocol.md#23-the-single-client-problem).

---

## 2. The channel-counter fix

`src/helpers/BaseChatMesh.cpp`.

`setChannel(idx, ...)` writes a channel at an arbitrary index and does **not**
touch `num_channels`. `addChannel()` relied on `num_channels` as "the next free
slot". Apps use `setChannel()`.

The result: `num_channels` could reach `MAX_GROUP_CHANNELS` while empty slots
sat below it, and adding a channel failed with "channel limit reached" on a node
that was mostly empty.

The fix reuses empty slots inside the used range before extending it:

```cpp
int slot = -1;
for (int i = 0; i < num_channels && i < MAX_GROUP_CHANNELS; i++) {
    if (channels[i].name[0] == 0) { slot = i; break; }
}
if (slot < 0 && num_channels < MAX_GROUP_CHANNELS) slot = num_channels;
...
if (slot == num_channels) num_channels++;
```

An empty slot is one with an empty `name`. `num_channels` still only ever grows,
so this is a compatible change: it makes `addChannel()` tolerant of what
`setChannel()` does, without changing either function's contract.

---

## 3. The stats publisher (companion)

`examples/companion_radio/StatsPublisher.{h,cpp}`, plus three additions to
`MyMesh`:

| Method | What it does |
|---|---|
| `fillStatsJson(out, max)` | Builds the ingest JSON body (`MyMesh.cpp:1045`) |
| `fillNodeIdHex(out, max)` | First 6 bytes of the public key as 12 hex chars |
| `logRxRaw(snr, rssi, raw, len)` | Overrides the `Dispatcher` hook; forwards to the publisher (`MyMesh.cpp:295`) |

Wiring in `main.cpp` is guarded by `#if defined(ESP32) && defined(WIFI_SSID)`:

```cpp
#include "StatsPublisher.h"
StatsPublisher stats_publisher;
...
stats_publisher.begin(SPIFFS, &the_mesh);   // last statement in setup()
...
stats_publisher.loop();                     // in loop()
```

`begin()` is deliberately the **last** thing `setup()` does (`main.cpp:277-284`):
it reads two files and starts a web server, and if that ever hangs or fails, the
mesh and the companion interface on `TCP_PORT` — where Home Assistant sits — are
already running. It is the one part of `setup()` that may not drag the node down
with it.

`MyMesh` never includes `StatsPublisher.h`. It calls free functions declared at
the top of `MyMesh.cpp`:

```cpp
void meshmanager_on_raw_packet(float snr, float rssi, const uint8_t raw[], int len);
```

which the publisher defines (`StatsPublisher.cpp:14-24`) and points at itself in
`begin()`. A null instance makes every hook a no-op, so a call before `begin()`
is harmless. That keeps the include graph acyclic and makes the module genuinely
optional.

### The rule the whole module is built around

From the header comment (`StatsPublisher.h:26-50`), and it is the single most
load-bearing paragraph in the companion firmware:

> **Every response must fit in one write.** This cost us a node twice, both times
> for the same reason.

First, the page assembled itself in pieces with `sendContent()`, values already
baked in. Each piece is a separate blocking TCP write, and with the latency
spikes of ESP32 WiFi the main loop hung inside them — which stalled the mesh
along with it, until a hard reset.

Then the second half turned up: `WiFiClient::write()` sends with `MSG_DONTWAIT`,
retries ten times with a one-second `select()` each, and then returns a *partial*
byte count. `WebServer` does not look at that value. So a response larger than
lwip's socket send buffer (**5760 bytes**) promises a `Content-Length` that is
never delivered, the client waits until its own timeout, and the main loop is
stuck for up to ten seconds.

Five rules follow, and everything else in this section is an application of one
of them:

1. the page is an immutable blob, gzipped, sent in one go;
2. all data comes from small JSON endpoints, never baked into the HTML;
3. lists are paginated (`STATS_CONTACT_PAGE`, `STATS_CHANNEL_PAGE`);
4. every handler writes into a fixed buffer, never a `String`;
5. `CountingWebServer` verifies that what was promised actually went out.

That last one is a subclass of `WebServer` (`StatsPublisher.h:214-245`) which
overrides the protected virtuals `_currentClientWrite` / `_currentClientWrite_P`
to count bytes asked against bytes done. On a short write, `finishResponse()`
(`StatsPublisher.cpp:416-424`) drops the connection so the browser gets an
immediate error instead of a minute-long timeout. Its honest caveat is recorded
at the source: *no short write means the bytes went into the stack, not that they
arrived*.

One shared I/O buffer of 896 bytes serves every JSON handler and both MQTT
payloads (`StatsPublisher.h:162-168`). They cannot overlap — the web server
handles one request at a time and both MQTT writers run from `loop()`, outside
`handleClient()`. A buffer per user would have cost 4230 bytes. Its size follows
from the largest user, a hex-encoded MTU packet (`2*255` + header), and every
list is paginated to fit inside it.

### HTTP routes

Port 80, fixed (`StatsPublisher.h:260`). Registered in `begin()`
(`StatsPublisher.cpp:866-878`). Everything is JSON unless noted:

| Route | Method | Purpose |
|---|---|---|
| `/` | GET | The page itself: HTML, gzipped, one `send_P` |
| `/config.json` | GET | Node identity, MQTT settings, status table |
| `/stats.json` | GET | The same JSON that goes to MQTT |
| `/save` | POST | Save broker settings; reconnects |
| `/test` | POST | Publish a stats message now |
| `/messages.json` | GET | Recent messages; `?since=<seq>` polls incrementally |
| `/send` | POST | Send a message; `to=c<idx>` (channel) or `k<hex>` (contact) |
| `/channels.json` | GET | Group channels, paged with `?off=<n>` |
| `/channel/add` | POST | Join or create a channel (`name`, `psk`; empty psk = new) |
| `/channel/del` | POST | Forget a channel (`idx`) |
| `/contacts.json` | GET | One page of contacts, `?off=<n>` |
| `/contact/save` | POST | Per repeater: publish flag and password (`key`, `publish`, `pass`) |
| `/contact/login` | POST | Log in to a repeater with the stored password (`key`) |

There is **no `/update` route, no `onNotFound`, no authentication and no CORS
header** anywhere in this server. Response shapes:

| Endpoint | Shape |
|---|---|
| `/config.json` | `{"name","ip","node","cfg":{host,port,user,prefix,interval,enabled,forward_rx},"status":{…}}` |
| `/stats.json` | `{"repeater":{pubkey_prefix,name},"metrics":{…}}`; HTTP 503 and `{}` when the mesh is absent |
| `/messages.json` | `{"m":[{"q":seq,"k":kind,"s":src,"t":ts,"x":text}],"more":1}` |
| `/channels.json` | `{"ch":[{"i":slot,"n":name}],"next":slot\|-1}` |
| `/contacts.json` | `{"c":[{"k":hex12,"n":name,"t":type,"a":secs_ago,"p":publish,"w":has_password}],"next":slot\|-1}` |
| the POST routes | `{"ok":1}` or `{"ok":0,"err":"…"}` |

`/config.json` has one deliberate oddity: it is the only response whose length
depends on text somebody typed rather than on its own pagination, so when those
strings overflow the shared buffer it sends a valid but **empty**
`{"cfg":{},"status":{}}` rather than truncated JSON (`StatsPublisher.cpp:476-478`).

Configuration lives in `/stats_cfg.json` on SPIFFS and is documented in
[`mqtt.md`](mqtt.md#node-side-configuration-companion).

> The page has **no authentication**. It exposes the broker password field
> (write-only — the page never renders the stored value) and lets anyone on your
> LAN change where stats go, read the recent messages, **send messages under the
> node's identity**, and store repeater passwords. Treat it as trusted-network
> only. The *repeater* module does have a login; see §4.

### `StatsPage.h` is generated — never edit it

`page.html` is the source. `gen_page.py` gzips it and emits `StatsPage.h` as a
`PROGMEM` byte array. The warning appears in three places, and it means what it
says:

| Where | Wording (translated) |
|---|---|
| `StatsPage.h:3` | GENERATED FILE — do not edit by hand |
| `gen_page.py:16-17` | Always change `page.html` and then run this script |
| `StatsPublisher.cpp:397-398` | Whoever wants to change the page edits `page.html` and runs the script; never `StatsPage.h` by hand |

```bash
python examples/companion_radio/gen_page.py     # page.html -> StatsPage.h
```

Two things about the generator that are easy to get wrong:

- **It does not minify.** It reads the raw bytes and gzips them unchanged
  (`gen_page.py:28-35`). `page.html` is hand-minified in the source — that is why
  it is written as dense one-line JavaScript. If you write roomy HTML there, the
  budget below pays for every space.
- **The output is reproducible on purpose.** `compresslevel=9, mtime=0`, so the
  same `page.html` always produces the same blob and a regeneration without a
  content change produces no diff.

Emitted symbols: `static const uint8_t PAGE_GZ[] PROGMEM` and
`static const size_t PAGE_GZ_LEN = sizeof(PAGE_GZ);`, 16 bytes per line.

#### The gzip budget

| | |
|---|---|
| Budget (`SND_BUF`, `gen_page.py:26`) | **5760 bytes** — `CONFIG_LWIP_TCP_SND_BUF_DEFAULT` on this build |
| `page.html`, uncompressed | 15278 bytes |
| **Current gzipped size** | **5702 bytes** (37 % of the original), recorded in `StatsPage.h:14-15` |
| Headroom | **58 bytes**, i.e. 99.0 % of the budget is spent |
| On overflow | prints a warning and exits **1** |

The budget is not a style preference: it is the size of the socket send buffer,
and a page that exceeds it reintroduces exactly the hang described above.

One sharp edge worth knowing before you edit the page: the size check happens
**after** `StatsPage.h` has already been written (`gen_page.py:68` writes,
`:73` checks), and the comparison is `>=`. So a failed run leaves an oversized
`StatsPage.h` in your tree with exit status 1. Do not commit that file without
reading the script's output. With 58 bytes of headroom, a feature added to
`page.html` almost always has to pay for itself by removing something else.

The page is served with the compressed length as `Content-Length`, because that
is what actually goes over the wire (`StatsPublisher.cpp:435-446`):

```cpp
_server.sendHeader("Content-Encoding", "gzip");
_server.send_P(200, "text/html; charset=utf-8", (PGM_P)PAGE_GZ, PAGE_GZ_LEN);
```

JSON responses go out through `send_P` for the same reason: the ordinary `send()`
copies the whole body into a heap `String` on top of the buffer already held, and
the framework itself warns *"Use send_P for long arrays"*.

### The web client

A chat client in the classic three-pane layout (`page.html:151-218`):
conversations on the left (channels and contacts together, with unread counters
and a filter), the conversation in the middle, its details on the right — who
spoke in a channel, or the key, type and last-heard of a contact, with the
forward-to-site checkbox, password field and login button for repeaters. On a
phone both side panes become drawers. The MQTT settings, the status table and a
live statistics table sit behind the settings button on the same page.

Polling cadence (`page.html:306`): messages every 3 s while the chat view is
open, `/config.json` every 10 s while the settings view is open, and a cache
flush every 30 s.

### The message ring

| Constant | Value | |
|---|---|---|
| `STATS_MSG_RING` | **32** | slots, 76 bytes each = 2432 bytes of static RAM |
| `STATS_MSG_SRC_MAX` | 16 | so **15** characters of sender name |
| `STATS_MSG_TEXT_MAX` | 48 | characters of message text |
| `STATS_MSG_CHANNEL` / `_DIRECT` / `_SENT` | 0 / 1 / 2 | kind; `_SENT` is your own message echoed back so the page can show both sides |

The ring is a flat list of `MsgItem` (`StatsPublisher.h:300-306`), each carrying
`seq`, `timestamp` (the *sender's* clock, not ours), `kind`, `src` and `text`.
Eviction is an unconditional overwrite of the oldest slot — there is no full or
empty test, and `_msg_head` is documented as *"next slot to overwrite; the ring
never runs empty"*. Readers walk forward from `_msg_head`, skipping slots with
`seq == 0` (never written) or `seq <= since`. `copyTrim()` truncates without
splitting a UTF-8 sequence.

It was **8 slots** originally, and the reason it is 32 is worth keeping: the ring
doubles as the backlog for a browser that opens later, since the page loads
everything with `since=0`. On a busy channel, eight slots meant an evening of
messages had already been overwritten before anybody looked. Thirty-two costs
2432 bytes instead of 608, which a build sitting at 55 % RAM can carry.
`/messages.json` does not grow with it: the response paginates itself with
`"more"` as soon as the shared buffer fills, and the page fetches the rest
immediately.

**Known limitation, stated at the source:** the ring lives in RAM only, so a node
reboot empties it. Persisting it to SPIFFS would fix that and was **deliberately
not built** — every incoming message would become a flash write, and on a node
that sees mesh traffic day and night that wears the flash out faster than the
backlog is worth.

### The browser cache of the history

The page keeps the last **300** messages itself, in `localStorage` under the key
`mh` (`page.html:230-231`, `:276`, `:303-304`). That is what makes a conversation
survive both the small ring and a node reboot.

| Aspect | Behaviour |
|---|---|
| Identity | `mid(m)` = timestamp + kind + source + speaker + text, joined by newlines |
| Merge | a message whose id is already known is dropped; anything new is renumbered locally |
| Cap | 300 messages, oldest dropped first |
| Write moments | on page hide, on visibility change, and every 30 s — never per message |

Each of those is a decision with a reason (`StatsPublisher.h:102-135`):

- **Merging on `q` is impossible.** The sequence counter restarts after a node
  reboot, so the same message can appear under two numbers. Hence the content
  hash. Genuine repeat posts still survive it, because senders stamp with
  `getCurrentTimeUnique()`. The accepted loss is named explicitly: two speakers
  with the same name saying the same thing in the same second in the same channel
  merge into one message, and with the fields the ring offers that cannot be
  told apart.
- **Cached messages are renumbered on load** and never count as unread, otherwise
  high pre-reboot numbers would outrank fresh low ones in the unread count.
- **300 is a rendering budget, not a storage budget.** It is about 30 kB of JSON
  where `localStorage` allows 5 MB per origin — but the page redraws the whole
  list on every update, so keeping much more would mainly make drawing slow on a
  phone.
- **Writes are batched** because `localStorage` is all-or-nothing per key: you
  cannot append one message. A browser crash therefore costs at most half a
  minute of cache.

Consequences, stated plainly: clearing site data clears the history, a second
browser starts empty and fills from that moment, and a message that fell out of
the ring while no browser was polling is genuinely gone. At the seam between
cache and ring the ordering can deviate slightly — the list is in arrival order,
and sorting on sender clocks would make it worse.

### Two sharp edges in the conversation mapping

Both documented at the source (`StatsPublisher.h:88-96`):

- **Names in the ring are truncated to 15 characters** while `/contacts.json`
  returns the full name, so the page compares only the first 15
  (`page.html:227-228`, `function eq(a,b){return a.slice(0,15)==b.slice(0,15)}`).
  Widen `STATS_MSG_SRC_MAX` and that comparison must move with it.
- **A sent message (`STATS_MSG_SENT`) does not say whether it went to a channel
  or to a contact.** The page looks for the name among channels first, so a
  channel and a contact with the same name file your own messages under the
  channel. A discriminator field in the response would fix it and was judged not
  worth the bytes.

Who spoke in a channel is not in `s` either: `sendGroupMessage()` prefixes the
text with `<sender>: `, and the page strips that prefix again to show the name in
its own column. All of this truncation is display-only — the companion app over
BLE or TCP still receives every message in full.

### No OTA on the companion

The companion has **no firmware upload of any kind**. A repository-wide search
for `ArduinoOTA`, `ElegantOTA`, `AsyncElegantOTA`, `Update.begin`, `httpUpdate`
and `/update` hits `examples/simple_repeater/` and nothing else. The companion's
route table has no `/update`, its includes are only `Arduino.h`, `FS.h`,
`WebServer.h`, `WiFiClient.h` and `PubSubClient.h`, and its environment does not
pull in `${esp32_ota.lib_deps}`.

It is not merely unwired, it is structurally incompatible as it stands: the
companion uses the **synchronous** `WebServer`, while `AsyncElegantOTA` requires
`ESPAsyncWebServer`.

So: **the companion is flashed over USB/serial, the repeater can be flashed over
the network.** The asymmetry is intentional. A repeater is on a roof; a companion
sits on a desk next to the person who wants to reflash it, and the flash and RAM
an uploader costs are better spent on contacts and channels. Plan for that when
you deploy — a companion node in an awkward place is a node you will be fetching
with a cable.

### Timing rules in `loop()`

Three, each with a measured reason:

- **Exactly one raw packet is published per `loop()` pass**
  (`StatsPublisher.cpp:353-357`). Draining four in a row held the mesh loop up
  for nearly a second.
- **Everything below `_server.handleClient()` is skipped while
  `_mesh->hasPendingWork()`** (`StatsPublisher.cpp:890-896`). `PubSubClient`'s
  `connect()` and `publish()` are synchronous and can block; the mesh has hard
  timing and forwarding does not.
- **15 seconds of backoff after a failed broker connection**
  (`StatsPublisher.cpp:276-278`). Hammering a broker that does not answer
  previously cost the whole node its responsiveness.

Mesh callbacks themselves only ever copy into a ring buffer, never send: network
I/O from a radio callback would hold up reception. The reverse direction is
allowed — an HTTP handler may call into the mesh, because sending merely queues a
packet.

### Things to know before you build this

- **`STATS_TRACE` is still `1`** (`StatsPublisher.cpp:33-44`). The block is
  marked *"TEMPORARY — diagnostics"* with the instruction to set it to 0 once the
  cause is pinned down, and it has not been. Every request prints to `Serial`.
- **`base64.hpp` is included manually.** It is header-only but not `inline`, so
  including it here as well as in `BaseChatMesh.cpp` produces duplicate symbols at
  link time; only `encode_base64` is declared by hand
  (`StatsPublisher.cpp:6-10`).
- **A repeater password is truncated at 15 characters** by `sendLogin()`, which
  is the real limit behind `STATS_REPEATER_PASS 16`. Up to
  `STATS_REPEATER_MAX` = 8 repeater entries are stored on SPIFFS; the contacts
  themselves live in mesh storage.
- **`MAX_CONTACTS` disagrees with itself.** The comment at
  `StatsPublisher.h:205-210` says 350; the shipped example sets **260**, with its
  own explanation: at 350 only 22 kB of heap was left, too little for lwip to
  arrange its buffers, so the web server wrote half responses and clients hung.
  260 leaves about 50 kB. Believe the example.
- **`DualSerialInterface.h` is dead code.** It runs BLE and WiFi side by side and
  routes replies to whichever last delivered a frame, but nothing includes or
  instantiates it — `main.cpp` uses `MultiSerialInterface` and registers each
  transport separately. It is superseded, not in use.
- **`WiFi.setSleep(false)` is applied only when BLE is not compiled in**
  (`main.cpp:201-220`). The ESP32 IDF requires modem sleep when WiFi and
  Bluetooth coexist; forcing it off produced an `abort()` on core 0 and a reboot
  loop. It is re-applied on every `ARDUINO_EVENT_WIFI_STA_GOT_IP`, because a
  reconnect resets the power mode.

---

## 4. `MeshManagerNet` — the repeater module

`examples/simple_repeater/MeshManagerNet.{h,cpp}`, enabled with `-D MESHMANAGER_NET=1`.

The premise, stated in the header comment (`MeshManagerNet.h:94-124`): this
repeater is on a roof and runs off a solar panel. **It may never become
unreachable, and it may never spend more energy than the panel brings in.**
Every design choice below follows from those two sentences, and where a choice
looks redundant it is usually because it closes a hole the others cannot reach.

Three entry points, called from `simple_repeater` via `repeater-hooks.patch`:

```c
void msnet_begin(FS &fs, MyMesh *mesh);                       // in setup()
void msnet_loop();                                            // in loop()
bool msnet_handle_command(const char *command, char *reply);  // from any CLI
```

Four more hooks feed it from the mesh side (`MeshManagerNet.h:148-180`):
`meshmanager_on_raw_packet()`, `meshmanager_on_monitor_response()`,
`meshmanager_on_advert()` and `meshmanager_advert_name()`, plus
`meshmanager_batt_percent()` which exists so that the admin page, the power
management and the published statistics all quote the *same* battery figure —
two curves disagreeing by a few percent is a bug report waiting to happen.

### 4.1 Version history

The authoritative changelog is the block comment at the top of
`MeshManagerNet.cpp` (lines 1–262). The current version is in
`MeshManagerNet.h:138`. This table is a reading aid, not a replacement: the
comment records the *reasoning*, which is the part that matters when you are
deciding whether to change something.

| Version | What it brought | Why |
|---|---|---|
| **1.0.0** | MQTT publishing (own stats + every raw packet); battery- and clock-aware publish interval with hysteresis; power-save WiFi with a forced-on escape hatch; admin page in the style of the public site, light/dark, NL/EN; own version reported by `ver`, on the page and in the payload | A repeater on a panel needs to report without draining itself |
| **1.1.0** | Task watchdog: a hung `loop()` becomes a reboot | The three boot-counter nets all key off *restarts*, and a hang produces none — see §4.14 |
| **1.2.0** | Monitor other repeaters: pick from the heard list or paste a public key, log in with a password or via their access list, poll `GET_STATUS` over the mesh | A repeater that talks only LoRa has no other way to reach the site |
| **1.3.0** | Monitored readings moved to the ordinary `stats` topic, with the subject in the payload; neighbour list moved inside the stats payload as `neighbors`; separate `polls` / `oks` / `pubs` counters; chip temperature renamed `mcu_temperature` | They had been published to `<prefix>/<node>/mon`, which nothing subscribes to: `publish()` succeeded and the broker dropped the data unread |
| **1.3.1** | Trace of the poll sequence (page, `wifi mon trace`, serial); one flood retry per step | A poll that stalled after a successful login looked exactly like one whose request was never sent — both leave `polls=1, oks=0, lr=1` |
| **1.4.0** | A monitored repeater is read with three requests instead of one: status, telemetry (CayenneLPP → `ch<N>_temperature` / `ch<N>_voltage`) and neighbours, published as one message; per-type counters | Any of the three may fail without losing the others |
| **1.5.0** | Adverts cached on the file system (key, name, type, last heard, coordinates); a metric that is not available is left out rather than published as `0` | Names survived a reboot as bare hex until the next advert, hours later; and `noise_floor 0` drew a line diving to zero where a gap belonged |
| **1.6.0** | Battery-to-interval became a user-editable rule table with hysteresis and a floor per mode; the page shows what a setting *costs* (messages/day, LoRa packets/hour); the node reads its own CLI parameters and ships them as a `settings` object | Five fixed levels do not fit every panel, cell and season |
| **1.7.0** | The settings sweep runs once a day instead of every six hours, and became observable: how many answered, when the last ran, when the next is due, the values themselves, and a way to force one | Before this the values appeared only in the one message after a sweep — one in 1440 — so a failed sweep and a sweep that never ran looked identical |
| **1.7.1** | The automatic monitor round never started at all — fixed; `region` split into `region.home` and `region.default` | `passed()` reads `0` as "not scheduled" and `_mon_next_round` began at `0`. Present since 1.2.0, hidden because every test began with a manual poll |
| **1.7.2** | The sweep also asks for `flood.max.unscoped` | The parameter list on the site steers only the Home Assistant path; this sweep has its own table, so a parameter added there never reached MQTT nodes |
| **1.8.0** | The node subscribes to `<prefix>/<node>/cmd` and accepts exactly two words: `settings` and `status`. Everything else is refused and counted | The site's "fetch settings" button wrote into a queue only Home Assistant ever emptied — take Home Assistant out of the chain and the button did nothing at all |
| **1.9.0** | `settings <key>`: a monitoring node reads the CLI settings of a repeater it *monitors*, over LoRa, and publishes them under that repeater's name | 1.8.0 only reached nodes that publish to MQTT themselves — which is not the repeater this project was built around |
| **1.9.1** | Node names and the six pieces of typed text in `/api/status` are JSON-escaped; `jsonEsc()` gained UTF-8-safe truncation | A name with a quote does not arrive looking odd, it does not arrive at all: the message stops being JSON, the ingest drops it whole, and `publish()` still reports success |
| **1.10.0** | The site can set this node's clock (`time <epoch>` on `cmd`), after which the node checks the clocks of the repeaters it monitors over LoRa; `wifi clock` reads back what happened | An ESP32 without a battery-backed RTC comes back from a reboot stamping everything with May 2024, and nothing on the mesh knows better. The site does |
| **1.11.0** | The sweep collects the region tree again, as `cmd:region`; `SET_VALUE_MAX` 32 → 176; `jsonEsc()` writes `\n`, `\r`, `\t`; `Err - …` recognised as a refusal alongside `Error…`; `MON_SET_TOTAL_MS` 300 s → 360 s | 1.7.1 rightly stopped publishing a tree in a settings column, but wrongly dropped the tree entirely — leaving one row ageing at "7 days" beside eighteen at "32 minutes" |

Two patterns run through that list and are worth naming, because they are the
reason several of the rules below exist:

- **A publish that succeeds and is then discarded is the worst failure mode in
  this system.** It happened with a topic nothing subscribed to (1.3.0) and with
  a payload that was not valid JSON (1.9.1). Both times every counter on the node
  said everything was fine. Hence: two outbound topics and only two, and one
  escaping helper used everywhere.
- **A silence must be published, not hidden.** A parameter that was asked and did
  not answer goes out as `null` (1.9.0), a scheduler that is not scheduled says
  so (1.7.1), and the poll sequence keeps a trace (1.3.1). Diagnosing a node on a
  roof must not require a serial cable.

### 4.2 WiFi with AP fallback

State machine in `msnet_loop()`:

| State | Behaviour |
|---|---|
| `WIFI_TRYING` | Connecting. After `STA_TIMEOUT_MS` (30 s) → start AP. |
| `WIFI_OK` | Connected. Loss of connection → back to `WIFI_TRYING`. |
| `WIFI_FALLBACK_AP` | Broadcasting `MeshCore-<node_hex>`. Retries your network every `STA_RETRY_MS` (5 min). Success → drop the AP, go to `WIFI_OK`. |

`startAP()` uses `WIFI_AP_STA`, so the AP stays up *while* station mode keeps
retrying. You are never locked out during a retry window, and recovery is
automatic — no visit to the roof.

AP SSID is `MeshCore-<first 6 bytes of pubkey in hex>`. The default AP password
is `meshcore`.

One exception, in `WIFI_TRYING`: in power-save mode, raising an AP nobody is
waiting for is the most expensive thing this node could do, so it goes back to
sleep and tries again next round instead. Unless the window was forced with
`wifi on` — then somebody is standing next to it looking for a network.

### 4.3 Power management

Everything here is tunable rather than compiled in, because the right numbers
depend on the panel, the cell and the season, and they have to be changeable
over the mesh without a reflash.

| Mode | Meaning | Interval floor |
|---|---|---|
| `PWR_ALWAYS` (0) | Always reachable; WiFi stays associated | `PWR_MIN_ALWAYS` = 10 s |
| `PWR_SAVE` (1) | WiFi off most of the time; wakes, publishes, sleeps | `PWR_MIN_SAVE` = 60 s |

In power-save mode the publish interval also decides how often the radio wakes,
which is why its floor is six times higher. Radio silence saves far more than a
slower interval ever will — that is the whole reason the mode exists.

The battery-to-interval mapping is a rule table (up to `PWR_RULES_MAX` = 8 rules,
stored in `/mspwr.json`), with hysteresis (`bat_hyst`) so a cell hovering on a
boundary does not oscillate between two intervals. A night window
(`night_from` / `night_to`, hours UTC) multiplies the interval by `night_factor`,
and applies only when the clock is actually plausible — nothing in this firmware
may stop working because the clock is wrong.

Two thresholds are about behaviour rather than timing:

- `bat_live` — above this percentage, received raw packets are forwarded to MQTT
  immediately; below it they wait.
- `bat_mon` — below this percentage, polling other repeaters stops altogether.
  Spending a flat battery on somebody else's statistics is the wrong trade.

A board that reports no usable cell voltage is treated as *unknown*, and unknown
is treated as mains power: a node that cannot measure its cell must not be
throttled by a guess.

The escape hatch is `wifi on [minutes]` (default `FORCE_DEFAULT_MIN` = 30). It
forces WiFi up and holds it there whatever the mode and whatever the battery
says. This is the way back into a node that is asleep, and it works from the mesh
CLI — so it works when the node is unreachable over IP by definition.

### 4.4 Management page and the `/api/*` endpoints

Port 80, `AsyncWebServer`. Asynchronous on purpose: a blocking server holds up
the main loop and therefore the mesh, which had already been observed on the
companion node.

| Route | Method | Auth | Purpose |
|---|---|---|---|
| `/` | GET | none | The page itself, streamed from flash with `send_P` |
| `/api/status` | GET | basic | Everything the page renders: identity, board, both firmware versions, WiFi state, battery, MQTT counters, power state, settings sweep, safe-mode flag |
| `/api/wifi` | POST | basic | SSID / password / AP password |
| `/api/power` | POST | basic | Power mode, window, sleep, TX power, battery bounds, intervals, night window |
| `/api/mqtt` | POST | basic | Broker host, port, user, password, prefix, enable, raw-packet forwarding |
| `/api/settings` | POST | basic | Settings-sweep interval, force a sweep now |
| `/api/mon` | GET | basic | Monitor list with per-entry counters, scheduler state, seconds to the next round, heard list, trace |
| `/api/mon` | POST | basic | `add`, `del`, `pass`, `en`, `iv`, `poll` |
| `/api/backup` | GET | basic | Download the whole filesystem |
| `/api/restore` | POST | basic | Upload a backup, then reboot |
| `/api/cfg` | GET | basic | Which CLI parameters may be set remotely, with their type, bounds, allowed words and risk class (2.1.0+) |
| `/api/cfg` | POST | basic | Set one of them and read it straight back — see [`node-management.md`](node-management.md) (2.1.0+) |
| `/api/fw` | GET | basic | Installed version, build environment, which partition runs, what the other one holds (1.12.0+) |
| `/api/fw` | POST | basic | Firmware image as the raw body, digest checked before the boot partition is switched — see [`firmware-upgrade.md`](firmware-upgrade.md) (1.12.0+) |
| `/api/fw/rollback` | POST | basic | Boot the other application partition again (1.12.0+) |
| `/update` | GET/POST | basic | `AsyncElegantOTA` firmware upload. Kept as the fallback for when the path above is the thing that broke |

Authentication is **HTTP basic**, credentials shared with the console
(`_cfg.user` / `_cfg.console_pass`, default `admin` / `meshcore`). `/` itself is
unauthenticated: it is a static shell that renders nothing until `/api/status`
succeeds. `send_P` streams the page straight from flash, because `send()` would
first copy all 14 kB into a heap `String` on a node that also has to keep a mesh
running.

`/api/status` answers with **values and codes, never finished sentences** — the
page renders them in the reader's language, which is also why the battery arrives
as millivolts, percentage and level rather than as a formatted string. Six fields
in it are text somebody chose (node name, SSID, broker host, and so on) and are
run through `jsonEsc()` since 1.9.1; a `NaN` MCU temperature is sent as `-999`,
because `"%.1f"` of a NaN prints `nan`, which is not JSON and would blank the
whole page.

Blank password fields on the WiFi form mean "leave unchanged". Without that,
opening the page and saving would wipe your WiFi password. The AP password is
only accepted at 8 characters or more, because WPA2 requires it.

**Nothing is written from a web handler.** The server runs in its own task; the
handlers set a flag and return:

```cpp
_apply_wifi = true;      // saving and reconnecting happens in loop()
```

`_apply_wifi`, `_apply_mqtt`, `_apply_power` and `_apply_rules` are picked up at
the top of `msnet_loop()`, which is where filesystem writes and `WiFi.begin()`
belong. The monitor endpoints use the same discipline through `_mon_action`, and
a POST arriving while a previous action is still pending is answered
`{"ok":0,"err":"busy"}` rather than queued.

### 4.5 OTA over your normal network

`AsyncElegantOTA.begin(&_server, _cfg.user, _cfg.console_pass)` mounts the
uploader on the same server, behind the same login. Firmware upgrades go over
your ordinary WiFi — no need to trigger `start ota`, join a soft-AP and upload
from there.

`start ota` is intercepted, because both uploaders want port 80. It does **not**
merely print the `/update` URL: it shuts our own server down and hands over to
the stock soft-AP updater. An earlier version did print the URL, on the
assumption that an upload over the normal network always works — and in doing so
removed the only fallback that did. A recovery path must never depend on the
thing you are recovering from.

If the module has disabled itself after repeated crashes (§4.14),
`msnet_handle_command()` returns false immediately and stock `start ota` works
regardless. `DISABLE_WIFI_OTA` is deliberately left unset for that reason.

#### Uploading with curl: disable `Expect: 100-continue`

This cost hours, so it is written down. curl adds an `Expect: 100-continue`
header to any sizeable `-F` upload. AsyncWebServer does not answer it the way
curl waits for, and the upload then fails in a way that looks like success:

- curl reports HTTP status **100** as its final result, never 200 or 400
- the node **reboots anyway**, because `AsyncElegantOTA` calls `restart()` in the
  response handler whether or not `Update.end()` succeeded
- so the node comes back on the *old* firmware, and the reboot proves nothing

Suppress the header and it works:

```bash
curl -H "Expect:" -u admin:PASSWORD \
     -F "MD5=$(md5sum firmware.bin | cut -d' ' -f1)" \
     -F "file=@firmware.bin;filename=firmware.bin" \
     http://<node-ip>/update
```

The `MD5` field is **mandatory** — without it the handler answers
`400 MD5 parameter missing` before the upload starts. The browser page bundled
with AsyncElegantOTA computes it client-side, which is why uploading from a
browser works when a naive curl command does not.

**Verify the version afterwards, never the reboot.** `ver` over the console, or
the `ms` field in `GET /api/status`, tells you what is actually running.

<a id="why-an-ota-does-not-lose-your-keys"></a>
### 4.6 Why an OTA does not lose your keys

This is the question people ask before every flash, so it is worth being precise.

**The application and the filesystem live in different flash partitions.** An
OTA writes only the inactive application partition. It never touches SPIFFS,
where your identity lives.

On a 16 MB ESP32-S3 — `boards/heltec_v4.json` sets
`"partitions": "default_16MB.csv"` — the table is:

| Partition | Type | Offset | Size | Holds |
|---|---|---|---|---|
| `nvs` | data | `0x009000` | `0x005000` (20 KB) | non-volatile key/value store |
| `otadata` | data | `0x00e000` | `0x002000` (8 KB) | which app slot to boot |
| `app0` | app, ota_0 | `0x010000` | `0x640000` (**6.25 MB**) | firmware slot A |
| `app1` | app, ota_1 | `0x650000` | `0x640000` (**6.25 MB**) | firmware slot B |
| `spiffs` | data | `0xc90000` | `0x360000` (**3.38 MB**) | **identity, prefs, ACL, config** |
| `coredump` | data | `0xff0000` | `0x010000` (64 KB) | crash dumps |

The arithmetic closes exactly:
`0x010000 + 0x640000 = 0x650000`, `0x650000 + 0x640000 = 0xc90000`,
`0xc90000 + 0x360000 = 0xff0000`, `0xff0000 + 0x010000 = 0x1000000` = 16 MB.

So:

- **OTA** writes the inactive app slot, flips `otadata`, reboots. `spiffs`
  untouched. Keys, contacts, prefs and ACL survive.
- **`pio run -t upload`** over serial writes only the app partition. Same result.
- **`esptool erase_flash`** erases everything, including `spiffs`. Your private
  key is gone and your node has a new identity.
- **Flashing a full merged `.bin` at offset 0** overwrites the whole chip,
  including `spiffs`. Same outcome.

The rule: flashing an application is safe; erasing or writing whole-chip images
is not.

> `default_16MB.csv` ships with the Arduino-ESP32 core, not with this
> repository, so the row values above could not be read from this working tree.
> They are the standard table and the arithmetic is self-consistent, but confirm
> against your own build before trusting them for a recovery operation:
> `python -m esptool --port COM4 read_flash 0x8000 0xc00 ptable.bin` and then
> `gen_esp32part.py ptable.bin`.

Back up anyway. It costs one command:

```bash
python -m esptool --port COM4 read_flash 0 0x1000000 backup.bin
```

(Use `0x800000` for an 8 MB board.)

### 4.7 Filesystem backup and restore

`/api/backup` produces a line-based text format, deliberately so that neither
side ever holds a whole file in RAM:

```
MESHMANAGER-BACKUP 1
FILE /identity 64
<up to 64 bytes per line, lowercase hex>
FILE /repeater_prefs 128
<hex>
END
```

`HEX_PER_LINE` is 64, so each line is at most 128 hex characters. Restore reads
line by line with `readBytesUntil('\n')` into a fixed buffer. Three files are
excluded (`skipInBackup()`): the backup file itself, the restore file, and the
boot counter `/msboot` — restoring a boot counter would restore a node straight
back into safe mode.

Restore writes as it parses and only reboots on success, so a corrupt upload
leaves the node as it was. The response goes out first and the reboot happens
1.5 s later from `msnet_loop()`, so the browser actually receives it.

> **A backup contains your private key.** Anyone holding the file holds your
> node's identity. That is why `/api/backup` is behind the login, why the page
> says so in plain text, and why you should change the default password before
> putting the node on a network. See [`security.md`](security.md#the-node-management-endpoints).

### 4.8 Telnet console

Port 23, plain telnet, same credentials. Login is a two-step prompt; three failed
password attempts drop the connection.

Once authenticated, lines go to `msnet_handle_command()` first and then to
`MyMesh::handleCommand()` — so you get the full MeshCore CLI over WiFi, plus the
`wifi` commands. `quit` or `exit` closes the session.

Two timeouts exist because a dead TCP session on an ESP32 keeps reporting
`connected()` for a long time. Without them, one dropped connection would
permanently close the debug channel you need precisely when something is wrong:

| Constant | Value | Effect |
|---|---|---|
| `CON_IDLE_MS` | 5 min | A silent session is closed by the node |
| `CON_TAKEOVER_MS` | 1 min | A session silent this long can be taken over by a new connection |

The console is disabled in safe mode.

> The console is **plaintext**: credentials and everything you type cross the
> network in the clear. LAN or VPN only.

### 4.9 `wifi` commands on the CLI

`repeater-hooks.patch` inserts `msnet_handle_command()` into
`MyMesh::handleCommand()`, so every command below works **over LoRa, over
serial, and over the telnet console alike**. That is the point: a broken WiFi
configuration must be fixable from the mesh, because a node with a wrong SSID has
no other way in.

| Command | Effect |
|---|---|
| `ver` | This module's version plus the MeshCore version it is built on. **No MeshManager name in the answer means this module is not running** |
| `wifi` | State, IP, signal, battery, publish interval |
| `wifi ssid <name>` | Set the network; empty restores the compiled-in default |
| `wifi pass <word>` | Set the WiFi password |
| `wifi connect` | Reconnect with the stored credentials |
| `wifi ap` | Broadcast our own network now |
| `wifi on [minutes]` | Force WiFi up and hold it there (default 30 min), whatever the mode and the battery say |
| `wifi off` | Back to automatic power management |
| `wifi console <user> <pass>` | Change the console and web login |
| `wifi mqtt …` | Broker settings; see below |
| `wifi power …` | Power management; `wifi power` alone prints the sub-help |
| `wifi mon …` | Monitored repeaters; see §4.11 |
| `wifi settings …` | The node's own CLI settings sweep |
| `wifi clock` | Clock status; **read-only on purpose**, see §4.12 |
| `wifi fw` | Which version runs from which application partition, which build environment this image was compiled for, what the other partition holds, and how the last upload ended (1.12.0+) |
| `wifi fw rollback` | Boot the other application partition again — the firmware from before the last upgrade (1.12.0+) |
| `wifi wdt` | Deliberately block the loop and see whether the watchdog fires |

`wifi fw rollback` is the one on this list that matters most over the **mesh**.
Every other way into this node runs over IP, so an upgrade whose only fault is
that it cannot join the WiFi takes all of them away at once — and LoRa comes up
from the radio driver before any of them. See
[`firmware-upgrade.md`](firmware-upgrade.md).

`wifi mqtt` sub-commands: `host`, `port`, `user`, `pass`, `prefix`, `rx <on/off>`,
`on`/`off`. With no argument it prints a status line:

```
verbonden, broker=<host>:1883, prefix=meshcore, rx=aan,
stats=412 pkt=9021 drop=3 cmd=7/2
```

That `cmd=<accepted>/<refused>` counter is the one thing that separates three
failures which otherwise look identical: the site never asked, the broker refused
the subscribe, or the command ran and changed nothing.

`wifi settings` sub-commands: bare (status of the sweep), `now` (force one),
`list <n>` (one parameter per call — a CLI reply is 160 bytes and this has to
work over the mesh), `iv <minutes>` (5 … 65535).

`wifi wdt` deserves a note, because it looks reckless and is the opposite. It
blocks the loop for `WDT_TIMEOUT_S + 10` seconds — *bounded*. An infinite loop
would hang the node irrecoverably if the watchdog turned out not to work, and
this node is on a roof. If the watchdog fires, the node reboots halfway through
and the whole chain (hang → watchdog → restart → boot counter) is proven. If it
does not fire, the node simply comes back and you have learned that the net is
not strung, without damage.

> `wifi pass` and `wifi console` send secrets in cleartext over LoRa when used
> from the mesh CLI. Prefer the console or the web page for those two.

### 4.10 Commands over MQTT — the `cmd` topic

The node subscribes to `<prefix>/<node>/cmd` on **every successful broker
connect**, inside `mqttEnsure()`. A subscription lives inside one session and this
client connects with a clean session, so subscribing once at startup would work
until the first WiFi hiccup and then silently stop — the kind of fault that only
ever shows up as *"the button used to do something"*.

| Word | Effect | Since |
|---|---|---|
| `settings` | Force a sweep of **this node's own** CLI, then publish it as soon as it finishes | 1.8.0 |
| `settings <key>` | Sweep the CLI of a repeater this node *monitors*, over LoRa, and publish it under that repeater's name | 1.9.0 |
| `status` | Publish a statistics message immediately | 1.8.0 |
| `time <epoch>` | Set this node's clock to that UNIX time in UTC seconds, then check the clocks of the monitored repeaters over LoRa | 1.10.0 |

That is the entire vocabulary.

#### Why it is a whitelist and not a passthrough to the CLI

The tempting implementation was one line: hand the payload to `handleCommand()`
and be done, exactly as the telnet console already does. It was rejected, and the
reason is worth stating in full because it is the security model of this feature.

**The console asks for a password over a link the operator controls. The `cmd`
topic is reachable by anyone holding broker credentials** — shared, leaked, or
simply mistyped into a second client. And the CLI behind it contains `reboot`,
`set`, and the wifi commands. One `reboot` in a loop is a lost repeater on a
roof, with no error anywhere and nothing to connect the loss to a script somebody
left running.

So the word is matched against a list of exactly three, and the two arguments
that exist do not widen it:

- **The argument on `settings` never becomes text that reaches a CLI**, here or
  on the far side. It only selects one entry from the monitor list, and what then
  goes out on the air is the compiled-in `SET_PARAMS` table and nothing else.
  That list is writable solely from the admin page and the mesh CLI, both of
  which ask for a password. A key matching no entry, or more than one, is refused
  and counted.
- **The argument on `time` is parsed here as a number**, bounded by a window of
  years, and applied by `clockApplyOwn()` which will only ever move a clock
  *forward*.

The worst an attacker on the broker can therefore achieve is: make the node
publish what it already publishes by itself, read out a repeater its operator
already chose to monitor, or move this node's clock forward inside that window —
at most once every 30 s, and for the two that cost airtime at most once every ten
minutes and once an hour respectively.

**The clock capability is real and is named rather than glossed over.** A clock
pushed far into the future cannot be walked back over the air, because nothing in
this system may move a clock backwards (§4.12). Recovering from that needs
`clkreboot` on the node and a resync. It is still a far smaller capability than
the `reboot` a passthrough would have handed over, and the feature is pointless
without it.

#### Format and handling

| Rule | Value | Why |
|---|---|---|
| Maximum payload | `MQTT_CMD_MAX` = 96 bytes | Longer than the longest accepted command (`settings ` + a 64-char key), so a payload that does not fit is recognisable as *too long* rather than silently truncated into something that happens to match |
| Minimum gap | `MQTT_CMD_MIN_GAP_MS` = 30 s | A power budget, not a security measure: every accepted command ends in a publish, and a node on a panel cannot afford one per second because somebody left a script running |
| Too soon | dropped, not queued | "Do it now" loses its meaning if it waits |
| Retained | must be `false` on the publisher | A retained command is redelivered on every reconnect, so the node would sweep on every boot and after every WiFi drop, forever |
| Whitespace | trimmed | A publisher that adds a newline is not punished for it |
| Wrong arity | refused | `status <anything>` is refused rather than quietly run as `status`: a publisher sending an argument to a command that has none has misunderstood something, and running it anyway hides that from both ends |

The callback (`mqttOnMessage`) runs inside `_mqtt.loop()` and does exactly one
thing: copy the word into a slot and return. `PubSubClient` is in the middle of
reading its socket there, and publishing from inside its own read is how you get
a reply interleaved with an incoming message. `mqttRunCommand()` acts on it a few
instructions later, from the ordinary loop — the same discipline as the raw
packet queue and the web server's apply flags. If a word is already waiting, the
new one is dropped.

Accepted and refused commands are counted separately and printed by `wifi mqtt`.

This needs a broker ACL entry: the node's account must be allowed to **read**
`<prefix>/<its own node id>/cmd`. Without it the subscribe is refused, and the
button on the site looks exactly as dead as it did before any of this existed.
See [`mqtt.md`](mqtt.md#asking-a-node-for-something).

### 4.11 Monitoring other repeaters

#### The poll round

Per peer the sequence is the one a chat client performs: an `ANON_REQ` carrying
the password, then — once accepted — a `REQ` of type `GET_STATUS`, and since
1.4.0 also telemetry and neighbours. A `RESPONSE` carries `RepeaterStats` back.

It is a state machine driven from `msnet_loop()`, **one peer at a time**. Not
because that is simpler, but because this node is a repeater: a burst of logins
from the very node meant to relay other people's traffic is antisocial, and every
flooded login costs the whole mesh airtime.

| Constant | Value | Why |
|---|---|---|
| `MON_STEP_MS` | 30 s | A first login is flooded and its answer comes back over an unknown number of hops; 20 s turned out to be tight |
| `MON_GAP_MS` | 3 s | Breathing space between peers |
| `MON_FIRST_MS` | 60 s | First automatic round after boot, late enough not to fight with startup |
| `MON_BACKOFF_AFTER` / `MON_BACKOFF_EVERY` | 3 / 4 | Three requests each waiting out 30 s is 90 s spent on a peer that is simply not there; after three barren rounds an entry is retried only every fourth. Any answer clears it |
| `MON_MIN_HEX` | 12 | Shortest key accepted when *adding* a node: 6 bytes, which is what this firmware itself uses to name a node. Below that, collisions stop being theoretical and you end up monitoring the wrong repeater |
| `MON_PASS_MAX` | 16 | The protocol truncates at 15 characters |
| `_mon_interval` | 900 s default | Between two rounds |

Three counters per entry, not two: `polls` (attempts), `oks` (answers received
and parsed) and `pubs` (actually published). `oks` used to be raised only on a
successful publish, which meant a reading that was fetched but never delivered
looked exactly like one that was never fetched — and finding that out took a
sniffer on the broker. Any gap between the three is now visible on the page.

A 12-line trace (`wifi mon trace`, the admin page, serial) exists for the same
reason: a status request that was never sent (packet pool empty) and one that was
sent but never answered both leave `polls=1, oks=0, lr=1`.

#### Logging in without a password

You can log in with the monitored repeater's admin or read/write password, but
there is a tidier way that needs no password at all: **a blank password makes the
far side skip the password check and look your public key up in its access list
instead** (`handleLoginReq()` in `MyMesh.cpp`). Its operator adds you once:

```
setperm <your-pubkey-hex> 1
```

where 1 is read-only, 2 read/write and 3 admin. Nobody has to hand out a
password, and access can be revoked on their side alone. In the monitor list an
empty password is therefore **a choice, not an omission**, and the admin page
does not treat it as a missing field.

> A refused login produces **no reply at all**, exactly like a repeater that is
> out of range. Hence the state `LOGIN_NOANSWER` rather than a pretence of
> knowing which of the two happened. "No answer" means either your key is not in
> their list yet, or you simply cannot reach them — the heard list on the admin
> page is what tells the two apart.

#### The settings sweep over LoRa (1.9.0)

The daily sweep of *this* node's own settings costs no airtime whatsoever:
`handleCommand()` is a function call. The variant with a key reads somebody
else's, over the radio, and that is a different animal: **nineteen requests and
up to nineteen replies on a shared band**, half of them paid for by a solar
repeater on a roof.

It exists because a repeater that does not publish to MQTT itself had no command
path at all. Its statistics arrive relayed by the node that monitors it; its
configuration arrived nowhere, and the button on its settings page said "relayed,
only the node itself can read its own CLI" — which was true and useless in equal
measure. The monitor already logs in and polls it, and has accepted `TXT_MSG`
answers from it since 1.4.0. Nothing ever *asked*.

The sequence, driven from the same state machine as the ordinary poll (one radio,
one reply slot, one session per peer):

1. reuse the login from an earlier poll, or send one and wait `MON_STEP_MS`;
2. send the parameter's command, wait `MON_SET_FIRST_MS` for the first answer and
   `MON_SET_STEP_MS` for the rest;
3. wait `MON_SET_GAP_MS`, then the next parameter;
4. publish once, at the end, on the ordinary `stats` topic with the monitored
   repeater in `repeater.pubkey_prefix`, an empty `metrics` object, and `via` set
   to this node.

| Limit | Value | Why |
|---|---|---|
| — | **on request only** | A schedule would spend that airtime forever, for values that change once a year. Somebody presses a button, or nothing happens |
| `MON_SET_MIN_GAP_MS` | 10 min | A reloaded page or a browser tab left on a refresh must not be able to keep the band busy. Far longer than a sweep needs, so a legitimate second attempt is never blocked, and it caps the feature at roughly 1 % of the hour whatever anyone does upstream |
| `MON_SET_GAP_MS` | 2 s | Spreads nineteen round trips over minutes instead of firing as fast as the far side answers. A burst from the relay itself is the least excusable congestion there is. Two seconds is what the Home Assistant implementation settled on for the same sequence on the same band |
| `MON_SET_STEP_MS` | 12 s | Home Assistant's measured value, same band, same hops |
| `MON_SET_FIRST_MS` | 20 s | The first packet after a login is the one that depends on the path just learned — the poll sequence found that out the hard way in 1.3.1 |
| `MON_SET_SILENT_MAX` | 3 | Whoever ignores the third parameter will ignore the nineteenth; continuing means transmitting sixteen more times into a hole |
| `MON_SET_TOTAL_MS` | 6 min | While a sweep runs the ordinary poll rounds wait, because they share this state machine; the cap is what keeps "wait" from meaning "until the next reboot" |

There is **no per-parameter retry**, deliberately. Home Assistant runs a second
round for the parameters that stayed silent; the 12 s wait was worth copying, the
extra round was not. Home Assistant runs on mains power through a USB-attached
node, while here a second round doubles the cost of the whole sweep to chase the
parameters least likely to answer. A silence is published as a silence instead.

The pending request is held as a **key**, not as an index into the monitor list,
because the list can be edited between the request and the moment the state
machine is free to act on it — `monDelete()` shifts everything after the gap down
by one, and an index would then quietly address the wrong repeater. That is the
one failure this feature must not have: it logs in and runs commands somewhere. A
request that never got its turn expires after `MON_SET_TOTAL_MS`, so one stale
request cannot answer every later one with "one is already running" until the
next reboot.

The key argument itself is normalised with a **lower floor than adding a node**:
eight hex characters instead of twelve, odd lengths allowed, and **refused
outright when it matches more than one entry**. Adding a node with too short a
key means monitoring the wrong repeater; selecting from a list that already
exists is a different question, and eight is what the site treats as the shortest
key it dares call the same node — refusing shorter ones would have meant refusing
the exact five-byte Home Assistant keys this feature exists for.

#### `SET_PARAMS` — the parameter table

Nineteen entries, in `MeshManagerNet.cpp:1352`. The same table is used for this
node's own daily sweep and for the LoRa sweep of a monitored node, because both
land in the same column of the same table on the site — and a rule that held for
one and not the other is exactly how `cmd:temp = Unknown command` gets into a
database.

| Key | CLI command | Notes |
|---|---|---|
| `name` | `get name` | |
| `role` | `get role` | |
| `radio` | `get radio` | |
| `freq` | `get freq` | |
| `tx` | `get tx` | |
| `af` | `get af` | |
| `repeat` | `get repeat` | |
| `advert.interval` | `get advert.interval` | |
| `flood.advert.interval` | `get flood.advert.interval` | |
| `flood.max` | `get flood.max` | |
| `flood.max.unscoped` | `get flood.max.unscoped` | Newer firmwares split the flood budget in two; on one that has not, the `??` reply is refused and the parameter is simply a miss |
| `allow.read.only` | `get allow.read.only` | |
| `rxdelay` | `get rxdelay` | |
| `txdelay` | `get txdelay` | |
| `lat` | `get lat` | |
| `lon` | `get lon` | |
| `region.home` | `region home` | keeps what follows `" is "` |
| `region.default` | `region default` | keeps what follows `" is "` |
| `cmd:region` | `region` | **multi-line answer expected**; last on purpose |

Three properties of that table carry their own reasoning:

- **`after`** (the `" is "` separator) is specified per parameter rather than as
  a blanket rule, because a node name could itself contain `" is "`.
- **`list`** says a multi-line answer is expected and not a fault. Exactly one
  parameter sets it, and that is the point: every other entry keeps the rule that
  a multi-line answer is a table which has no business in a settings column, and
  keeps being refused for it.
- **`cmd:region` is last on purpose.** It is the longest answer and the least
  urgent thing in the table — region topology changes about as often as somebody
  reflashes the node, while everything above it is what you look at when
  something is wrong. If a sweep ever runs out of its time budget, this is the
  entry that should be missing from it.

The region tree looks like this, and reading `printChildRegions()` settles what
the markers mean:

```
*
 eu F
  bx F
   be^ F
    be-vbr F
```

`*` is simply the name of the wildcard root region — **not** a marker for the
active one; `^` marks the home region; a trailing ` F` means flooding is allowed
there and its absence means `DENY_FLOOD`; indentation is parent/child nesting.
MeshCore caps the tree at 160 bytes itself (`handleRegionCmd()` calls
`exportTo(reply, 160)`) and sends the whole reply as **one** text message, so
there is no collect-until-quiet on this path and no widened timeout: one command,
one reply, exactly like every other entry. A tree too big for 160 bytes is cut on
the far side, not here.

Two consequences of adding it in 1.11.0, both of which had to give:

- `SET_VALUE_MAX` went from 32 to **176**, because 160 is the ceiling MeshCore
  imposes. A second, larger buffer for the one long parameter was rejected: it
  saves about five kilobytes of a two-megabyte part and pays for that with two
  storage paths, two sizing rules and a special case in every loop that walks the
  table — and the day somebody adds a second long parameter, the cheap version is
  the one that breaks.
- `jsonEsc()` now writes `\n`, `\r` and `\t` instead of dropping them. Control
  characters were dropped on purpose, and for a node name that is right, but here
  the line breaks and the indentation **are** the value. Dropping them would have
  turned fourteen meaningful lines into one run of region names, published
  successfully, and wrong.

Also since 1.11.0, MeshCore's two spellings of a refusal — `Error…` and
`Err - …` — are both recognised. Only the first was, so `Err - unknown region`
was stored as though it were a setting, with a fresh timestamp beside it: an
answer that looks more authoritative than "(geen antwoord)" while meaning
strictly less. They are matched spelled out rather than as a `Err` prefix, so a
node called *Erratic* survives.

#### Admin rights are required, and a read-only monitor fails silently

This is the single most important operational fact about the sweep.

**A repeater runs a CLI command only for a client it considers an admin**
(`handleCommand` is reached from `onPeerDataRecv` only under
`client->isAdmin()`), and it says **nothing at all** to one it does not.

So a read-only monitor logs in perfectly, sends nineteen commands, and hears
nineteen silences — which on the air looks exactly like a node that moved out of
range. Read-only is enough for everything else in this module and is what the
header recommends; it is not enough for this.

```
setperm <monitor-pubkey-hex> 3
```

on the monitored repeater, or give the monitor that repeater's admin password.

The sweep **publishes its silences rather than hiding them**, precisely so this
is diagnosable from the site instead of from a serial cable. Two failures that
look alike from a distance are kept apart on purpose:

| Failure | What is published |
|---|---|
| The login never answered | **Nothing at all.** Nothing was asked, so nothing was learned. The site keeps showing the values it had, with their old timestamps — which is what "we learned nothing" honestly looks like. Publishing nineteen nulls would throw away values an earlier sweep did get, for a fault that says nothing about any individual parameter |
| Logged in, parameters stayed silent | **Published, with `null` for each one that did not answer.** The site renders that as "(geen antwoord)". It overwrites what we knew, and that is intended: here we did ask, and "they would not tell us" is a fresher fact than a value from March |

Startable from any CLI as well as from MQTT: `wifi mon settings <hex>` starts one
and reports on the previous one, and `wifi mon trace` shows the sequence. That
matters more here than anywhere else in this module, because this failure mode is
silent by nature.

### 4.12 Clock synchronisation

#### Why the mesh needs it at all

A MeshCore node timestamps the messages it sends and the adverts it emits from
its own clock, and an ESP32 without a battery-backed RTC starts at whatever it
was compiled or `clkreboot`-ed with. A repeater on a roof reboots on its own —
flat battery, watchdog, a power cut in thunderstorm season — and comes back with
a clock reading May 2024. Everything it says afterwards is stamped wrong, and
nothing on the mesh corrects it, because nothing on the mesh knows better either.

The site does: it runs on a machine whose clock is disciplined against real time.

#### The commands and the time format

| Command | Where | Format |
|---|---|---|
| `time <epoch>` | MeshCore CLI, and the `cmd` topic | **UNIX epoch seconds, UTC.** `CommonCLI.cpp` does `_atoi` of the rest of the line, straight into `setCurrentTime` |
| `clock` | MeshCore CLI | Answers `HH:MM - D/M/YYYY UTC` |
| `clock sync` | MeshCore CLI | Sets the clock from the **timestamp of the request packet** rather than from text |

**All three refuse to go backwards.**

The far side of a monitor round gets `clock sync`, not `time <epoch>`, and the
reason is airtime arithmetic: ten characters against fifteen, which with five
bytes of message header is one 16-byte cipher block instead of two — a third of
the packet's airtime, for a value we would have taken from our own clock anyway.
There is also no number to format wrongly.

On this node's own clock:

| Constant | Value | Meaning |
|---|---|---|
| `CLOCK_MIN_EPOCH` | 1735689600 (2025-01-01 UTC) | Floor. Not decoration: `clkreboot` sets the clock to 15 May 2024 and an unset board starts near its build date, both more than a year in the past — so one comparison separates "never set" from "drifted", and those two deserve different words on a page |
| `CLOCK_MAX_EPOCH` | 4102444800 (2100-01-01 UTC) | Ceiling. Catches milliseconds truncated into 32 bits, a parse that read the wrong field, a typo with an extra digit |
| `CLOCK_OWN_MIN_STEP_S` | 5 | Smallest difference worth a step. The site publishes daily and the message takes a moment to arrive, so a second or two is the measurement rather than the drift |

`clockApplyOwn()` refuses three things, each protecting against a different
accident: a time outside the window, a time *behind* our own, and a difference
too small to be worth a step. An unset clock is exempt from the last rule — a
jump of a year and a half is exactly what should happen there.

#### Why everything is forward-only

This is the rule that governs the whole feature, and it is not a MeshCore quirk
to route around.

**An advert carries the emitting node's clock, and every node that already knows
the sender drops an advert whose timestamp did not increase** (`onAdvertRecv` in
`MyMesh.cpp`). Move a repeater's clock back by an hour and it is invisible to
everyone who knows it, for an hour. On a roof repeater that is worse than any
wrong timestamp — a maintenance command that takes the node off the mesh, which
is the one thing this firmware may not do.

So a node found running **fast** is counted, reported, and left exactly as it is.
It is the lesser of the two faults and the only reversible one.

#### The round along the monitored nodes

| Constant | Value | Why |
|---|---|---|
| `MON_CLK_FIRST_MS` | 20 s | First command after a login depends on a path just learned |
| `MON_CLK_STEP_MS` | 12 s | Per command after that |
| `MON_CLK_GAP_MS` | 3 s | Between two commands, and between nodes |
| `MON_CLK_SILENT_MAX` | 3 | After three silent nodes in a row, the thing that is wrong is not the fourth node |
| `MON_CLK_TOTAL_MS` | 5 min | Hard cap on one run; poll rounds wait while it runs |
| `MON_CLK_MIN_GAP_MS` | 1 h | The site asks once a day; this caps what a broker account can do with that to once an hour — still 24× the intended rate and still cheaper than one poll round |
| `MON_CLK_SKEW_S` | 120 | Smallest deviation acted on |

There are **no retries anywhere**. A node that does not answer `clock` is skipped
until tomorrow; a clock is not urgent enough to flood for, and tomorrow is one
day of drift away.

**Why the clock is read before it is set.** Sending `clock sync` blind costs
exactly the same as reading `clock` — one command, one reply, every node, every
day. So thrift is *not* the argument, and pretending otherwise would be the kind
of reasoning that survives in a comment long after it stopped being true. The
argument is that a read produces a measurement — "this repeater was four minutes
behind" — where a blind sync produces nothing anybody can see, and that a node
which is fine is then never sent a command that changes its clock at all. The
second round trip is spent only where the first one proved it was needed.

**Why two minutes.** It is the resolution of the interface, not a taste. `clock`
answers to the minute, so a reading of `09:05` means the far side is somewhere in
a sixty-second window, and the reply reaches us seconds after it read. The drift
is therefore computed as a **range**, and a correction goes out only when the
whole range sits beyond the threshold. Two minutes is the smallest number for
which that can be true at all.

**Why this may run on a schedule when the settings sweep may not.** One command
and one reply per monitored node per day, against nineteen and nineteen. That is
roughly a fifth of one poll round, which this node already pays every fifteen
minutes.

`wifi clock` reads it all back: this node's clock, when the site last set it, the
four counters (set / already right / refused-backwards / refused-out-of-window),
and the last round's summary — asked, answered, synced, running fast, and the
largest deviation seen.

It is **read-only on purpose**. There is no way to type a time in there, because
the whole point of the feature is that the time comes from a machine which has a
reason to know what time it is. A person at a serial cable does not, and neither
does this node.

### 4.13 The advert cache

Every advert this node hears updates a small cache: key, name, type, when last
heard, and coordinates when the advert carries them.

| Constant | Value |
|---|---|
| `ADV_CACHE_MAX` | 48 entries |
| `ADV_NAME_MAX` | 24 characters |
| `ADV_WRITE_DELAY` | 120 s of quiet before writing |
| File | `/adverts.dat`, magic `AVS1` |

It exists because without it a reboot leaves the monitor list and the heard list
showing bare hex keys until the next advert, and those can be hours apart.

The hook copies into RAM only. **The file is written lazily from `msnet_loop()`,
never from the hook**: adverts arrive in bursts on a busy mesh, and SPIFFS wears
out. One write once the burst has settled, not one write per advert.

Names truncated at `ADV_NAME_MAX` are why `jsonEsc()` has to be UTF-8-safe: a
name whose last byte lands in the middle of a two-byte character is already half
a character before it reaches the publisher.

<a id="the-three-safety-nets"></a>
### 4.14 The safety nets

`MeshManagerNet` is custom code running on a node that must not die. It therefore
assumes it might itself be the thing that is broken. There are **four** nets, and
each covers a failure the others cannot see.

A boot counter lives in `/msboot` on SPIFFS. `checkSafeMode()` reads it, decides,
and immediately writes it back incremented. After `STABLE_UPTIME_MS` (5 minutes)
of continuous running, the boot is declared successful and the counter is reset
to zero.

| # | Net | Trigger | Result |
|---|---|---|---|
| 1 | **Safe mode** | 3 boots without 5 minutes stable (`SAFE_MODE_BOOTS`) | AP + management page only. No console, no MQTT, no monitoring, no settings sweep |
| 2 | **Module off** | 6 boots (`DISABLE_BOOTS`) | Not one line of this module starts. What remains is a plain MeshCore repeater with its mesh CLI and stock `start ota` |
| 3 | **No `halt()` on radio failure** | `radio_init()` fails | The network side starts anyway, so you can reflash from the ground |
| 4 | **Task watchdog** | `loop()` blocked for `WDT_TIMEOUT_S` (30 s) | Panic, backtrace, reboot — which turns a hang into an event the three nets above can see |

**Why two boot thresholds rather than one.** The bug could be in the safe-mode
path itself. Safe mode still starts an AP and a web server, so it can still
crash. Level 6 removes every line of this module from the boot path. The counter
is cleared even when the module is disabled, so the next boot after a fix tries
everything again.

**Why the radio case matters.** Stock `simple_repeater` calls `halt()` when the
radio fails to initialise, which on a rooftop node means a brick. With
`MESHMANAGER_NET`:

```cpp
if (!radio_ok) {
    msnet_begin(*fs, &the_mesh);
    while (1) { msnet_loop(); delay(5); }
}
```

No mesh — there is no radio — but WiFi, the management page and OTA are up.

**Why the watchdog is not redundant.** The three nets above all key off
*restarts*. A sibling node failed in a way that produces none: after a flash it
answered on no TCP port at all while ping kept working on and off. That is the
signature of a blocked `loop()`. WiFi and lwip live in their own FreeRTOS tasks
and keep answering pings while the application task stands still — no crash, no
backtrace, no restart, so the boot counter never advances and safe mode never
arrives. On a roof that is a dead node.

`panic=true` is deliberate: the panic handler prints a backtrace before rebooting
(the framework is built with `PANIC_PRINT_REBOOT`), so the next person gets the
diagnosis the sibling node never produced.

**Why 30 s and not the framework default of 5 s.** Several things legitimately
block this task far longer than five seconds, and a spurious reboot loop on a
roof is worse than the illness. The long pole is an MQTT connect to a broker
given as a hostname — lwip's DNS wait plus the socket timeout is roughly 15–20 s
of blocked `loop()` with nothing wrong. SPIFFS writes and a flash erase add a few
more. Thirty seconds clears that worst realistic case with room to spare and
still brings a hung node back inside half a minute. Note this also relaxes the
idle-task watchdog the core installs at 5 s to the same 30 s; that is intended,
since the same operations starve the idle task for the same reasons.

**The watchdog steps aside during an OTA.** An upload writes and erases flash
from the async task, and those stretches stop the world for longer than any
normal operation. A watchdog reboot halfway through a firmware write is the one
reboot this must never cause, so `wdtFeed()` unsubscribes for the duration
instead of trying to guess a timeout that covers it.

That opens its own hole, which is closed too: an abandoned upload (browser closed
halfway) never reaches `Update.end()`, so `Update.isRunning()` would stay true
forever and quietly leave the node without a watchdog — exactly the silent
failure this whole thing exists to prevent. Hence `WDT_OTA_MAX_MS` (5 min): the
limit on how long we are willing to believe an upload is still in progress.

`wdtBegin()` is called **before** the `_disabled` return in `msnet_begin()`, on
purpose: a node that has switched this module off is exactly the one that must
still be able to reboot itself out of a hang. And `wdtFeed()` is the first
statement in `msnet_loop()`, before any early return, because reaching that line
is the proof that the loop is still turning.

Prove the whole chain with `wifi wdt` (§4.9).

---

## 5. Building and flashing

### 5.1 Apply the changes

This repository is an **overlay** on upstream MeshCore, not a fork: there is no
`platformio.ini` here, and the tree contains only the files that differ.

```bash
git clone https://github.com/meshcore-dev/MeshCore.git
cd MeshCore
git checkout companion-v1.17.0

# copy the files over
cp -r /path/to/MeshManager/firmware/src/*      src/
cp -r /path/to/MeshManager/firmware/examples/* examples/

# or apply as a patch
git apply /path/to/MeshManager/firmware/meshmanager.patch
```

`repeater-hooks.patch` contains only the `simple_repeater` edits, if you want
those without the rest. It also carries that example's own `fillStatsJson()`,
which is where the repeater's stats payload is built — a different and richer
payload than the companion's, see [`mqtt.md`](mqtt.md#payload-stats).

### 5.2 Configure the build

Create `platformio.local.ini` from `platformio.local.ini.example`.

> **This file holds your WiFi credentials and admin password. It is gitignored.
> Never commit it.**

A companion environment needs, at minimum:

```ini
[env:my_companion]
extends = Heltec_lora32_v3
build_flags =
  ${Heltec_lora32_v3.build_flags}
  -D DISPLAY_CLASS=SSD1306Display
  -D MAX_CONTACTS=260
  -D MAX_GROUP_CHANNELS=40
  -D TCP_PORT=5000
  -D WIFI_SSID='"YOUR_SSID"'
  -D WIFI_PWD='"YOUR_PASSWORD"'
  -D BLE_PIN_CODE=000000
build_src_filter = ${Heltec_lora32_v3.build_src_filter}
  +<helpers/esp32/*.cpp>
  +<../examples/companion_radio/*.cpp>
lib_deps =
  ${Heltec_lora32_v3.lib_deps}
  densaugeo/base64 @ ~1.4.0
  WebServer
  knolleary/PubSubClient @ ^2.8
```

`MAX_CONTACTS` is worth setting explicitly and worth setting **low**. A contact
slot costs about 316 bytes of static RAM. At 350 there was only 22 kB of heap
left — too little for lwip to arrange its buffers, so the web server wrote half
responses and clients hung. 260 leaves roughly 50 kB and still keeps 40 slots of
headroom above the number of nodes currently on the air.

A repeater environment:

```ini
[env:my_repeater]
extends = heltec_v4_oled
build_flags =
  ${heltec_v4_oled.build_flags}
  -D DISPLAY_CLASS=SSD1306Display
  -D ADVERT_NAME='"My Repeater"'
  -D ADVERT_LAT=0.0
  -D ADVERT_LON=0.0
  -D ADMIN_PASSWORD='"CHANGE_ME"'
  -D MAX_NEIGHBOURS=50
  -D MESHMANAGER_NET=1
  -D WIFI_SSID='"YOUR_SSID"'
  -D WIFI_PWD='"YOUR_PASSWORD"'
build_src_filter = ${heltec_v4_oled.build_src_filter}
  +<helpers/ui/SSD1306Display.cpp>
  +<../examples/simple_repeater>
lib_deps =
  ${heltec_v4_oled.lib_deps}
  ${esp32_ota.lib_deps}
  bakercp/CRC32 @ ^2.0.0
  knolleary/PubSubClient @ ^2.8
```

Do **not** set `DISABLE_WIFI_OTA`. While `MeshManagerNet` runs it intercepts
`start ota`; if it disables itself after repeated crashes, stock OTA is your
fallback (§4.14).

The compiled-in `WIFI_SSID` / `WIFI_PWD` are defaults only. `MeshManagerNet`
overrides them from `/msnet.json` once you set anything through the page or the
CLI. They exist so the very first flash comes up on the network.

### 5.3 Build flags

| Flag | Default | Meaning |
|---|---|---|
| `MESHMANAGER_NET` | unset | Enable the repeater network module |
| `WIFI_MAX_CLIENTS` | 4 | Simultaneous companion clients (~2–3 kB RAM each) |
| `TCP_PORT` | 5000 | Companion TCP port |
| `MAX_CONTACTS` | — | Contact slots; ~316 bytes each. See above |
| `MAX_GROUP_CHANNELS` | — | Channel slots; needed for the channel fix to matter |
| `MAX_NEIGHBOURS` | — | Neighbour slots on a repeater; sizes the `neighbors` array in the payload |
| `WIFI_SSID` / `WIFI_PWD` | — | Built-in network defaults |
| `ADMIN_PASSWORD` | — | MeshCore's own admin password for the mesh CLI |
| `BLE_PIN_CODE` | — | Companion BLE pairing code |
| `RADIO_FEM_RXGAIN_DEFAULT` | board | Set to 0 where the FEM amplifier overdrives on a strong signal — a node standing high with clear line of sight hears more without it |
| `WIFI_DEBUG_LOGGING` | 0 | Verbose interface logging |
| `DISABLE_WIFI_OTA` | unset | **Leave unset**; see above |

### 5.4 Regenerate the companion page (companion only)

Only if you touched `page.html`:

```bash
python examples/companion_radio/gen_page.py
```

Check the output. It exits 1 when the gzipped page exceeds 5760 bytes — and it
has already overwritten `StatsPage.h` by then. See §3.

### 5.5 Build and flash

```bash
pip install platformio
python -m platformio run -e my_repeater -t upload --upload-port COM4
```

Serial upload writes the app partition only. Keys survive — see
[the partition table](#46-why-an-ota-does-not-lose-your-keys). Take a backup
anyway.

| Target | First flash | Later flashes |
|---|---|---|
| **Repeater** | USB/serial | **OTA at `http://<node-ip>/update`**, behind the admin login. Or serial |
| **Companion** | USB/serial | **USB/serial. There is no OTA** (§3) |

For the repeater, the full OTA procedure is:

1. `python -m platformio run -e my_repeater` — build only.
2. Find the binary at `.pio/build/my_repeater/firmware.bin`.
3. Open `http://<node-ip>/update`, log in, upload. The bundled page computes the
   MD5 for you.
4. Or from the command line, **with the `Expect:` header suppressed**:

```bash
curl -H "Expect:" -u admin:PASSWORD \
     -F "MD5=$(md5sum firmware.bin | cut -d' ' -f1)" \
     -F "file=@firmware.bin;filename=firmware.bin" \
     http://<node-ip>/update
```

   Without `-H "Expect:"` curl reports status 100, the node reboots anyway, and
   comes back on the old firmware. Without the `MD5` field the handler answers
   `400 MD5 parameter missing`. See §4.5.
5. **Check `ver`, not the reboot.**

If the node is unreachable over IP: join its own network `MeshCore-<node id>`
(default password `meshcore`) and use the same page, or reach the mesh CLI over
LoRa and use `wifi on 30` to force WiFi up (§4.3).

### 5.6 After flashing

1. Watch the serial log for the assigned IP, or connect to `MeshCore-<id>` if the
   node fell back to AP mode.
2. Open `http://<node-ip>/`.
3. **Change the default login immediately** — `admin` / `meshcore` on the
   repeater. Behind it sit both your private key and firmware upload.
4. Set the MQTT broker, prefix, interval, and enable publishing.
5. On a monitoring node: add the repeaters to monitor, and make sure the far side
   has granted **admin** rights if you want their settings readable (§4.11).
6. Confirm messages are arriving in `/admin` on the server.

---

## Status

Built and tested on a Heltec V3 (ESP32-S3) companion and a Heltec V4 repeater.

| | |
|---|---|
| Multiple companions with targeted replies | working |
| Channel-counter fix | working |
| Companion management page, chat client and `/stats.json` | working |
| MQTT stats publishing | working |
| `MeshManagerNet` on the repeater | working |
| Monitoring other repeaters over LoRa | working |
| Commands from the site over MQTT (`cmd` topic, 1.8.0) | written and reviewed, **not flashed on any node yet** |
| Settings sweep of a monitored repeater over LoRa (1.9.0) | written and reviewed, **not flashed on any node yet** — needs 1.9.0 on the monitoring node and admin rights on the monitored one |
| Clock synchronisation (1.10.0) and `cmd:region` (1.11.0) | written and reviewed, **not flashed on any node yet** |
| Forwarding over **HTTP** | abandoned — crashed the node; see [`architecture.md`](architecture.md#why-mqtt) |
| Raw-packet forwarding over MQTT | working |
| Full web client on the companion node | working |
