# Firmware modifications

MeshStats ships a set of changes to the [MeshCore](https://github.com/meshcore-dev/MeshCore)
firmware. They fall into two groups:

- **Companion node** (`examples/companion_radio`) — multiple simultaneous WiFi
  clients, a channel-counter fix, and the stats publisher with its web chat
  client.
- **Repeater** (`examples/simple_repeater`) — `MeshStatsNet`: WiFi with AP
  fallback, a management page, OTA over your normal network, a telnet console on
  the MeshCore CLI, and filesystem backup/restore.

Everything is opt-in at build time. Without the flags, you get stock MeshCore.

| File | What it changes |
|---|---|
| `src/helpers/esp32/SerialWifiInterface.{h,cpp}` | Multiple simultaneous WiFi companions |
| `src/helpers/BaseChatMesh.cpp` | Channel-slot reuse |
| `examples/companion_radio/StatsPublisher.{h,cpp}` | MQTT publisher + management page |
| `examples/companion_radio/MyMesh.{h,cpp}` | `fillStatsJson()`, `fillNodeIdHex()`, raw-packet hook |
| `examples/companion_radio/main.cpp` | Wires the publisher in |
| `examples/simple_repeater/MeshStatsNet.{h,cpp}` | The repeater network module |
| `repeater-hooks.patch` | The three small edits in `simple_repeater` |
| `meshstats.patch` | Everything, as one patch |

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
| `fillStatsJson(out, max)` | Builds the ingest JSON body |
| `fillNodeIdHex(out, max)` | First 6 bytes of the public key as 12 hex chars |
| `logRxRaw(snr, rssi, raw, len)` | Overrides the `Dispatcher` hook; forwards to the publisher |

Wiring in `main.cpp` is guarded by `#ifdef WIFI_SSID` on ESP32:

```cpp
#include "StatsPublisher.h"
StatsPublisher stats_publisher;
...
stats_publisher.begin(SPIFFS, &the_mesh);   // in setup()
...
stats_publisher.loop();                     // in loop()
```

`MyMesh` never includes `StatsPublisher.h`. It calls a free function declared at
the top of `MyMesh.cpp`:

```cpp
void meshstats_on_raw_packet(float snr, float rssi, const uint8_t raw[], int len);
```

which the publisher defines and points at itself in `begin()`. If the publisher
never started, the call is a no-op. That keeps the include graph acyclic and
makes the module genuinely optional.

The publisher serves a web client on port 80. Everything is JSON unless noted:

| Route | Method | Purpose |
|---|---|---|
| `/` | GET | The page itself (HTML, gzipped, single `send_P`) |
| `/config.json` | GET | Node identity, MQTT settings, status — for the page to render |
| `/stats.json` | GET | The same JSON that goes to MQTT |
| `/save` | POST | Save broker settings; reconnects |
| `/test` | POST | Publish a stats message now |
| `/messages.json` | GET | Recent messages; `?since=<seq>` polls incrementally |
| `/send` | POST | Send a message; `to=c<idx>` (channel) or `k<hex>` (contact) |
| `/channels.json` | GET | Configured group channels, paged with `?off=<n>` |
| `/channel/add` | POST | Join or create a channel (`name`, `psk`; empty psk = new) |
| `/channel/del` | POST | Forget a channel (`idx`) |
| `/contacts.json` | GET | One page of contacts, `?off=<n>` |
| `/contact/save` | POST | Per repeater: publish flag and password (`key`, `publish`, `pass`) |
| `/contact/login` | POST | Log in to a repeater with the stored password (`key`) |

The page is served as one static blob and fetches its data afterwards. It used to
be assembled in pieces with `sendContent()`; each piece is a separate blocking
write, and under ESP32 modem-sleep latency the main loop stalled inside them,
taking the mesh with it. The blob is stored gzipped
(`page.html` → `gen_page.py` → `StatsPage.h`) so the whole response fits inside
lwip's socket send buffer, and every list endpoint is paged for the same reason:
no response may outgrow a single write. The full war story is at the top of
`StatsPublisher.h`.

Configuration lives in `/stats_cfg.json` on SPIFFS and is documented in
[`mqtt.md`](mqtt.md#node-side-configuration).

> The page has **no authentication**. It exposes the broker password field
> (write-only — the page never renders the stored value) and lets anyone on your
> LAN change where stats go, read the recent messages, **send messages under the
> node's identity**, and store repeater passwords. Treat it as trusted-network
> only. The *repeater* module does have a login; see below.

### The web client

The page is a chat client in the classic three-pane layout: conversations on the
left (channels and contacts together, with unread counters and a filter), the
conversation itself in the middle, its details on the right — members of a
channel, or the key, type and last-heard of a contact, with the forward-to-site
checkbox and login button for repeaters. On a phone both side panes become
drawers. The MQTT settings and the status tables live behind a button on the
same page.

Conversations exist only in the browser. The node keeps a flat ring of the last
`STATS_MSG_RING` (8) messages and does not know what a conversation is; the page
polls `/messages.json?since=<seq>` and files each message into a conversation by
the name in its `s` field — the channel name for a channel message, the sender
for a direct message, the destination for one you sent yourself. That mapping
has two sharp edges, documented at the source in `StatsPublisher.h`:

- **Names in the ring are truncated** to 15 characters (`copyTrim()`,
  `STATS_MSG_SRC_MAX`), while `/contacts.json` returns full names. The page
  therefore matches conversations on the first 15 characters only. Widen
  `STATS_MSG_SRC_MAX` and that comparison must move with it.
- **A sent message (`STATS_MSG_SENT`) does not say whether it went to a channel
  or to a contact.** The page tries channels first, so a channel and a contact
  with the same name file your own messages under the channel. A discriminator
  field in the response would fix it and was judged not worth the bytes.

Who spoke in a channel is not in `s` either: `sendGroupMessage()` prefixes the
text with `<sender>: `, and the page strips that prefix again to show the name
in its own column.

The truncation — of names and of the message texts in the ring — is display-only.
The companion app over BLE or TCP still receives every message in full.

---

## 4. `MeshStatsNet` — the repeater module

`examples/simple_repeater/MeshStatsNet.{h,cpp}`, enabled with `-D MESHSTATS_NET=1`.

The premise, stated in the header comment: this repeater is on a roof. It must
never become unreachable. Every design choice follows from that.

Three entry points, called from `simple_repeater` via `repeater-hooks.patch`:

```c
void msnet_begin(FS &fs, MyMesh *mesh);                       // in setup()
void msnet_loop();                                            // in loop()
bool msnet_handle_command(const char *command, char *reply);  // from any CLI
```

### WiFi with AP fallback

State machine in `msnet_loop()`:

| State | Behaviour |
|---|---|
| `WIFI_TRYING` | Connecting. After `STA_TIMEOUT_MS` (30 s) → start AP. |
| `WIFI_OK` | Connected. Loss of connection → back to `WIFI_TRYING`. |
| `WIFI_FALLBACK_AP` | Broadcasting `MeshCore-<node_hex>`. Retries your network every `STA_RETRY_MS` (5 min). Success → drop the AP, go to `WIFI_OK`. |

`startAP()` uses `WIFI_AP_STA`, so the AP stays up *while* station mode keeps
retrying. You are never locked out during a retry window, and recovery is
automatic — no visit to the roof.

AP SSID is `MeshCore-<first 6 bytes of pubkey in hex>`. Default AP password is
`meshcore`.

### Management page

Port 80, `AsyncWebServer`. Asynchronous on purpose: a blocking server holds up
the main loop and therefore the mesh, which had already been observed on the
companion node.

| Route | Method | Auth | Purpose |
|---|---|---|---|
| `/` | GET | none | Static page |
| `/api/status` | GET | basic | Name, node id, board, firmware, SSID, safe-mode flag, and a status table |
| `/api/wifi` | POST | basic | Set SSID / password / AP password |
| `/api/backup` | GET | basic | Download the whole filesystem |
| `/api/restore` | POST | basic | Upload a backup, then reboot |
| `/update` | GET/POST | basic | `AsyncElegantOTA` firmware upload |

Authentication is **HTTP basic**, credentials shared with the console
(`_cfg.user` / `_cfg.console_pass`, default `admin` / `meshcore`). Note that `/`
itself is unauthenticated — it is a static shell that renders nothing until
`/api/status` succeeds.

Blank password fields on the WiFi form mean "leave unchanged". Without that,
opening the page and saving would wipe your WiFi password. The AP password is
only accepted at 8 characters or more, because WPA2 requires it.

Saving does not write from the web handler. It sets a flag:

```cpp
_apply_wifi = true;      // saving and reconnecting happens in loop()
```

The web server runs in its own task; filesystem writes and `WiFi.begin()` happen
on the main loop instead.

### OTA over your normal network

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

If the module has disabled itself after repeated crashes (see below),
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
`ms` in `GET /api/status`, tells you what is actually running.

### Why an OTA does not lose your keys

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

### Filesystem backup and restore

`/api/backup` produces a line-based text format, deliberately so that neither
side ever holds a whole file in RAM:

```
MESHSTATS-BACKUP 1
FILE /identity 64
<up to 64 bytes per line, lowercase hex>
FILE /repeater_prefs 128
<hex>
END
```

`HEX_PER_LINE` is 64, so each line is at most 128 hex characters. Restore reads
line by line with `readBytesUntil('\n')` into a fixed buffer. The backup file
itself, the restore file, and the boot counter are excluded.

Restore writes as it parses and only reboots on success, so a corrupt upload
leaves the node as it was. The response goes out first and the reboot happens
1.5 s later in `msnet_loop()`, so the browser actually receives it.

> **A backup contains your private key.** Anyone holding the file holds your
> node's identity. That is why `/api/backup` is behind the login, why the page
> says so in plain text, and why you should change the default password before
> putting the node on a network. See [`security.md`](security.md#the-node-management-endpoints).

### Telnet console

Port 23, plain telnet, same credentials. Login is a two-step prompt; three failed
password attempts drop the connection.

Once authenticated, lines go to `msnet_handle_command()` first and then to
`MyMesh::handleCommand()` — so you get the full MeshCore CLI over WiFi, plus the
`wifi` commands.

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

### `wifi` commands on the mesh CLI

`repeater-hooks.patch` inserts `msnet_handle_command()` into
`MyMesh::handleCommand()`, so these work over LoRa, over serial, and over the
console alike:

| Command | Effect |
|---|---|
| `wifi` | State, SSID, IP, RSSI |
| `wifi ssid <name>` | Set SSID (saved immediately) |
| `wifi pass <password>` | Set password (saved immediately) |
| `wifi connect` | Reconnect with the current settings |
| `wifi ap` | Start the fallback AP now |
| `wifi console <user> <pass>` | Change the console/web login |

This is the escape hatch. A wrong SSID cannot strand the repeater, because you
can still fix it over the mesh.

> `wifi pass` and `wifi console` send secrets in cleartext over LoRa if used from
> the mesh CLI. Prefer the console or the web page for those two.

### The three safety nets

`MeshStatsNet` is custom code running on a node that must not die. It therefore
assumes it might be the thing that is broken.

A boot counter lives in `/msboot` on SPIFFS. Each start increments it. After
`STABLE_UPTIME_MS` (5 minutes) of continuous running, the boot is declared
successful and the counter is reset to zero.

| Condition | Result |
|---|---|
| 3 boots without 5 minutes stable (`SAFE_MODE_BOOTS`) | **Safe mode**: AP + management page only. No console. |
| 6 boots (`DISABLE_BOOTS`) | **Module does not start at all.** A plain MeshCore repeater remains, with its mesh CLI and stock `start ota`. |
| `radio_init()` fails | No infinite `halt()`. The network side starts anyway so you can reflash. |

Two thresholds rather than one, because the bug could be in the safe-mode path
itself — safe mode still starts an AP and a web server, so it can still crash.
Level 6 removes every line of this module from the boot path.

The counter is cleared even when the module is disabled, so the next boot after a
fix tries everything again.

The radio case is worth spelling out. Stock `simple_repeater` calls `halt()` when
the radio fails to initialise, which on a rooftop node means a brick. With
`MESHSTATS_NET`:

```cpp
if (!radio_ok) {
    msnet_begin(*fs, &the_mesh);
    while (1) { msnet_loop(); delay(5); }
}
```

No mesh — there is no radio — but WiFi, the management page and OTA are up, so
you can reflash from the ground.

---

## 5. Building and flashing

### Apply the changes

```bash
git clone https://github.com/meshcore-dev/MeshCore.git
cd MeshCore
git checkout companion-v1.17.0

# copy the files over
cp -r /path/to/MeshStats/firmware/src/*      src/
cp -r /path/to/MeshStats/firmware/examples/* examples/

# or apply as a patch
git apply /path/to/MeshStats/firmware/meshstats.patch
```

`repeater-hooks.patch` contains only the `simple_repeater` edits, if you want
those without the rest.

### Configure the build

Create `platformio.local.ini` from `platformio.local.ini.example`.

**This file holds your WiFi credentials and admin password. It is gitignored.
Never commit it.**

A companion environment needs, at minimum:

```ini
[env:my_companion]
extends = Heltec_lora32_v3
build_flags =
  ${Heltec_lora32_v3.build_flags}
  -D DISPLAY_CLASS=SSD1306Display
  -D TCP_PORT=5000
  -D WIFI_SSID='"YOUR_SSID"'
  -D WIFI_PWD='"YOUR_PASSWORD"'
  -D BLE_PIN_CODE=000000
build_src_filter = ${Heltec_lora32_v3.build_src_filter}
  +<helpers/esp32/*.cpp>
  +<../examples/companion_radio/*.cpp>
lib_deps =
  ${Heltec_lora32_v3.lib_deps}
  WebServer
  knolleary/PubSubClient @ ^2.8
```

A repeater environment:

```ini
[env:my_repeater]
extends = heltec_v4_oled
build_flags =
  ${heltec_v4_oled.build_flags}
  -D DISPLAY_CLASS=SSD1306Display
  -D ADVERT_NAME='"My Repeater"'
  -D ADMIN_PASSWORD='"CHANGE_ME"'
  -D MESHSTATS_NET=1
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

Do **not** set `DISABLE_WIFI_OTA`. While `MeshStatsNet` runs it intercepts
`start ota`; if it disables itself after repeated crashes, stock OTA is your
fallback.

The compiled-in `WIFI_SSID` / `WIFI_PWD` are defaults only. `MeshStatsNet`
overrides them from `/msnet.json` once you set anything through the page or CLI.
They exist so the very first flash comes up on the network.

### Build flags

| Flag | Default | Meaning |
|---|---|---|
| `WIFI_MAX_CLIENTS` | 4 | Simultaneous companion clients (~2–3 kB RAM each) |
| `TCP_PORT` | 5000 | Companion TCP port |
| `MESHSTATS_NET` | unset | Enable the repeater network module |
| `MAX_GROUP_CHANNELS` | — | Channel slots; needed for the channel fix to matter |
| `WIFI_SSID` / `WIFI_PWD` | — | Built-in network defaults |
| `WIFI_DEBUG_LOGGING` | 0 | Verbose interface logging |

### Build and flash

```bash
pip install platformio
python -m platformio run -e my_repeater -t upload --upload-port COM4
```

Serial upload writes the app partition only. Keys survive — see
[the partition table](#why-an-ota-does-not-lose-your-keys). Take a backup
anyway.

### After flashing

1. Watch the serial log for the assigned IP, or connect to `MeshCore-<id>` if the
   node fell back to AP mode.
2. Open `http://<node-ip>/`.
3. **Change the default login immediately** — `admin` / `meshcore` on the
   repeater. Behind it sit both your private key and firmware upload.
4. Companion node: set the MQTT broker, prefix, interval, and enable publishing.
5. Confirm messages are arriving in `/admin` on the server.

---

## Status

Built and tested on a Heltec V3 (ESP32-S3) companion and a Heltec V4 repeater.

| | |
|---|---|
| Multiple companions with targeted replies | working |
| Channel-counter fix | working |
| Management page and `/stats.json` | working |
| MQTT stats publishing | working |
| `MeshStatsNet` on the repeater | working |
| Forwarding over **HTTP** | abandoned — crashed the node; see [`architecture.md`](architecture.md#why-mqtt) |
| Raw-packet forwarding over MQTT | **in development** |
| Full web client on the companion node | working |
