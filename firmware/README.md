# MeshManager firmware

Changes to the [MeshCore](https://github.com/meshcore-dev/MeshCore) firmware
(companion v1.17.0 line) that give your node:

1. **multiple companions at once** — Home Assistant, the MeshCore app and a
   stats server can all be connected simultaneously
2. a **management page** on port 80, with live statistics
3. **stats publishing over MQTT** to a MeshManager site
4. on repeaters, `MeshManagerNet`: WiFi with AP fallback, OTA over your normal
   network, a telnet console on the MeshCore CLI, and filesystem backup/restore

**Full documentation: [`../docs/firmware.md`](../docs/firmware.md).** This file is
a short index; the detail — including *why* an OTA does not lose your keys — lives
there.

> **Coming from MeshStats?** The module was called `MeshStatsNet` up to and
> including 1.12.0. Version 2.0.0 renamed everything, the MQTT topic prefix
> included, so **update the server first** — it listens to both prefixes, a node
> does not. In your own `platformio.local.ini`, rename `-D MESHSTATS_NET` to
> `-D MESHMANAGER_NET`; forget it and you get a build that starts as a plain
> MeshCore repeater without saying so. After flashing, `ver` must answer
> `MeshManager (by DinX) v2.0.1` or later. The config on the data partition keeps its
> filenames and survives, and the topic prefix moves itself once. Full order of
> operations: [`../docs/migration.md`](../docs/migration.md).

## Files

| File | What |
|---|---|
| `src/helpers/esp32/SerialWifiInterface.{h,cpp}` | Multiple simultaneous WiFi companions |
| `src/helpers/BaseChatMesh.cpp` | Reuse of empty channel slots |
| `examples/companion_radio/StatsPublisher.{h,cpp}` | MQTT publishing + management page |
| `examples/companion_radio/MyMesh.{h,cpp}` | `fillStatsJson()`, `fillNodeIdHex()`, raw-packet hook |
| `examples/companion_radio/main.cpp` | Wires the module in |
| `examples/simple_repeater/MeshManagerNet.{h,cpp}` | Repeater: WiFi, management page, OTA, console, backup |
| `repeater-hooks.patch` | The edits in `simple_repeater`, **required** — see *Applying* |
| `meshmanager.patch` | The in-place edits of both examples, as one patch |
| `tools/verify_image.py` | Proves a built `.bin` really contains the module |

## Why these changes

**One client at a time.** The stock WiFi interface held exactly one companion:

```cpp
auto newClient = server.available();
if (newClient) {
    client.stop();      // the existing companion is kicked off
    client = newClient;
}
```

So you could not have Home Assistant and your phone connected at once. There are
now four slots, each with its own frame state. Replies go only to the client that
sent the command; unsolicited messages (adverts, incoming messages) go to
everyone. Without that distinction, clients desynchronise on each other's
replies.

**The channel counter.** `setChannel()` — which apps use — writes a channel
without updating `num_channels`, while `addChannel()` relied on that counter. It
could reach the maximum while empty slots sat below it, and the app then reported
"channel limit reached" on a mostly empty node. Empty slots are now reused.

**HTTP crashed the node.** `HTTPClient` plus the TLS stack needs too much heap
next to mesh, WiFi and BLE. Replaced by MQTT, which keeps one lightweight
connection open instead of building a session per measurement. See
[`../docs/architecture.md`](../docs/architecture.md#why-mqtt).

## Applying

```bash
git clone https://github.com/meshcore-dev/MeshCore.git
cd MeshCore
git checkout companion-v1.17.0

# 1. copy the files from this directory over the tree
cp -r /path/to/MeshManager/firmware/src/*      src/
cp -r /path/to/MeshManager/firmware/examples/* examples/

# 2. and the repeater hooks, which are edits inside upstream's own files
git apply /path/to/MeshManager/firmware/repeater-hooks.patch
```

> **Both steps.** `examples/simple_repeater/` here holds only
> `MeshManagerNet.{cpp,h}`; the calls that tie the module into `MyMesh.cpp` and
> `main.cpp` live in `repeater-hooks.patch`, because those are upstream files we
> only edit. Without the patch `MeshManagerNet.cpp` does not compile at all.
> `meshmanager.patch` is those in-place edits for both examples in one file —
> it contains no new files, so it does not replace step 1 either.

Create a `platformio.local.ini` with your own settings (see
`platformio.local.ini.example`) and build:

```bash
pip install platformio
python -m platformio run -e <your_env> -t upload --upload-port COM4
```

> `platformio.local.ini` holds your WiFi credentials and admin password. It is
> gitignored. Never commit it.

> Flashing with `-t upload` writes only the app partition. Your private key,
> contacts and settings are in a separate SPIFFS partition and survive — see
> [`../docs/firmware.md`](../docs/firmware.md#why-an-ota-does-not-lose-your-keys)
> for the partition table. Take a backup anyway:
> `python -m esptool --port COM4 read_flash 0 0x1000000 backup.bin`

## Repeater with network management

`MeshManagerNet` gives a repeater an IP life alongside its mesh life. Built for a
node on a roof, which must never become unreachable:

- **WiFi client**; if it cannot connect within 30 s it broadcasts its own network
  (`MeshManager-<id>`) with the same management page, and keeps retrying yours every
  5 minutes
- **Management page** on port 80 behind a login, with status, WiFi settings and
  **firmware upload at `/update`** — upgrades go over your normal network, not
  only via the OTA soft-AP
- **Backup and restore** of the whole filesystem: keypair, repeater prefs, ACL
  and network settings, in a line-based format
- **Console** on port 23 behind the same login, wired straight into
  `MyMesh::handleCommand` — the full MeshCore CLI over WiFi. A silent session is
  closed after 5 minutes and can be taken over after 1, so one dropped connection
  does not close your debug channel
- **`wifi` commands** also work over the mesh CLI, so a wrong network setting
  cannot strand the repeater

Three safety nets against a bug in this code itself:

| Situation | What happens |
|---|---|
| 3 restarts without 5 minutes stable | Safe mode: own network + management page only |
| 6 restarts | The module does not start; a plain MeshCore repeater remains, with `start ota` |
| Radio init fails | No infinite `halt()`; the network side starts anyway so you can reflash |

> **A backup contains your private key.** Set your own password immediately
> (default `admin` / `meshmanager`) — the same login also guards firmware upload.

## Management page

After flashing: **http://\<node-ip\>/**

- publishing settings (broker, credentials, topic prefix, interval)
- live node statistics
- `/stats.json` returns the same data as JSON

> The **companion** node's page has no authentication. Trusted networks only. The
> **repeater** page does have a login.

## Build-time flags

| Flag | Default | Meaning |
|---|---|---|
| `WIFI_MAX_CLIENTS` | 4 | Simultaneous companions (~2–3 kB RAM each) |
| `TCP_PORT` | 5000 | Companion port |
| `MESHMANAGER_NET` | unset | Enable the repeater network module (was `MESHSTATS_NET` before 2.0.0) |
| `MAX_GROUP_CHANNELS` | — | Number of channel slots |
| `WIFI_SSID` / `WIFI_PWD` | — | Built-in network defaults |

## Status

Working and tested on a Heltec V3 (ESP32-S3) companion and a Heltec V4 repeater.

- Multiple companions at once, with targeted replies
- Channel-counter fix
- Management page and `/stats.json`
- MQTT stats publishing
- `MeshManagerNet` on the repeater
- Forwarding over **HTTP**: abandoned, it crashed the node
- Raw-packet forwarding over MQTT: **in development**
- A full web client on the companion node: **planned**
