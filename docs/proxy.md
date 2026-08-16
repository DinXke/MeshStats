# The TCP proxy

*[Nederlands](nl/proxy.md)*

`proxy/mc-proxy/` — a fan-out proxy that lets several clients share one MeshCore
WiFi node. It is a Home Assistant add-on, but the program underneath is a single
dependency-free Python file that runs anywhere.

**It carries no statistics and never talks to a MeshStats server.** It is a
transport helper, and it is in this repository only because the problem it solves
sits directly in front of everything else here.

---

## Contents

- [The problem](#the-problem)
- [Do you need it?](#do-you-need-it)
- [Installation](#installation)
- [Options](#options)
- [Environment variables](#environment-variables)
- [Status page](#status-page)
- [How it forwards](#how-it-forwards)
- [Keeping the node connection alive](#keeping-the-node-connection-alive)
- [Client management](#client-management)
- [Access control](#access-control)
- [What it does not solve](#what-it-does-not-solve)
- [Troubleshooting](#troubleshooting)
- [Version history in brief](#version-history-in-brief)

---

## The problem

Stock MeshCore companion firmware accepts **one TCP client at a time**. That is
one client, total — not one per app. So the Home Assistant `meshcore`
integration, the MeshCore phone app and `meshcore-cli` cannot coexist against the
same node; whichever connects last takes the slot, and the others fight it for
the socket.

The proxy holds that single connection itself and shares it:

```
                        +---------------------------+
   WiFi node  <-------> |        mc-proxy           | <---> meshcore HA integration
   (one TCP slot)       |  holds the single socket  | <---> MeshCore app
                        |  fans out to N clients    | <---> meshcore-cli
                        +---------------------------+
                                    |
                                    +--> status page (JSON)
```

---

## Do you need it?

| Situation | Answer |
|---|---|
| You can flash the MeshStats firmware | **No.** `SerialWifiInterface` handles four simultaneous clients on the node itself, with targeted reply routing. See [`firmware.md`](firmware.md#1-multiple-companions-on-one-node) |
| You cannot flash firmware, and want more than one client | **Yes** |
| You have exactly one client and always will | No |

The two solutions differ in more than location. The firmware knows which client
asked what and routes replies to the asker; the proxy broadcasts every node frame
to every client and lets clients sort it out. Both work, for reasons explained in
[`protocol.md` §2.3](protocol.md#23-the-single-client-problem).

---

## Installation

### As a Home Assistant add-on

Add `https://github.com/DinXke/MeshCore-Proxy` as an add-on repository, or point
Home Assistant at this repository's `proxy/` directory. Then:

1. Set **`node_host`** to the address of your MeshCore WiFi node and start the
   add-on. It is the one required option; `run.sh` aborts with a fatal log line
   if it is empty.
2. Point the **`meshcore` integration** at the proxy on TCP port `5000` on the
   Home Assistant host itself.
3. Point the **MeshCore app** at the Home Assistant machine, port `5000`.
4. Make sure **nothing connects to the node directly any more**. The node still
   has only one slot, and a direct client will fight the proxy over it.

`build.yaml` builds on the Home Assistant base images for `amd64`, `aarch64` and
`armv7`. The container installs nothing but `python3`.

### Standalone

`mc_proxy.py` is plain Python 3 with only standard-library imports. Set the
environment variables below and run it:

```bash
MCP_NODE_HOST=<node-address> python3 proxy/mc-proxy/mc_proxy.py
```

The add-on layer is `run.sh`, which does nothing but translate add-on options
into those environment variables.

---

## Options

Exposed in the add-on UI, from `config.yaml`:

| Option | Default | Meaning |
|---|---|---|
| `node_host` | — (**required**) | Address of the MeshCore WiFi node |
| `node_port` | `5000` | TCP port of the node |
| `allowed_ips` | `[]` | Allow-list of client addresses or CIDRs. Empty means everyone. **Set it.** |
| `max_clients` | `8` | Maximum simultaneous clients (schema allows 1–64) |
| `log_level` | `info` | `debug` / `info` / `warning` |

Ports published by the add-on: `5000/tcp` for clients, `5001/tcp` for the status
page. Changing the port in the add-on's **Network** panel remaps the host side
only; inside the container the proxy always listens on 5000.

The UI exposes a deliberate subset. Everything else keeps its built-in default
and is not reachable from Home Assistant.

---

## Environment variables

The full set the program understands, from the module docstring and the constants
at the top of `mc_proxy.py`:

| Variable | Default | Meaning |
|---|---|---|
| `MCP_NODE_HOST` | — (required) | Address of the node |
| `MCP_NODE_PORT` | `5000` | Node TCP port |
| `MCP_LISTEN_HOST` | `0.0.0.0` | Interface to listen on |
| `MCP_LISTEN_PORT` | `5000` | Client port |
| `MCP_HEALTH_PORT` | `5001` | Status page port |
| `MCP_ALLOWED_IPS` | *(empty)* | Comma- or semicolon-separated addresses/CIDRs |
| `MCP_MAX_CLIENTS` | `32` | Maximum simultaneous clients (the add-on sets 8) |
| `MCP_RECONNECT_S` | `1` | Delay between node reconnect attempts |
| `MCP_MAX_RECONNECT_S` | `15` | Ceiling for that delay |
| `MCP_KEEPALIVE_S` | `30` | Keepalive interval towards the node |
| `MCP_HANDSHAKE_TIMEOUT_S` | `30` | Patience for a handshake answer (one retry at half time) |
| `MCP_MAX_SILENT_ROUNDS` | `3` | Unanswered keepalives before the connection is rebuilt |
| `MCP_IDLE_EVICT_S` | `60` | Idle time after which a client slot may be reused |
| `MCP_NODE_DOWN_GRACE_S` | `60` | How long the node may be gone before clients are dropped |
| `MCP_MIN_CMD_GAP_S` | `0.25` | Minimum spacing between two commands to the node |
| `MCP_LOG_LEVEL` | `info` | `debug` / `info` / `warning` |

The generous defaults are a correction, recorded in the code: on a weak WiFi link
a proxy that disconnects and reconnects quickly makes things **worse**, not
better.

---

## Status page

`http://<host>:5001/` returns JSON. `health_server()` in `mc_proxy.py`.

| Field | Meaning |
|---|---|
| `node_host` | The upstream node and port |
| `node_connected` | Whether the upstream socket is open |
| `node_answering` | Whether the node is *replying*, not merely accepting TCP |
| `seconds_since_node_data` | Age of the last byte received from the node |
| `silent_keepalive_rounds` | Consecutive keepalives with no answer |
| `clients` / `client_count` / `max_clients` | Connected clients |

The pair worth understanding: **`node_connected` true with `node_answering`
false** is the exact failure this proxy was built to detect. Companion firmware
can reach a state where it still accepts TCP and answers nothing — a plain
reachability check calls that healthy. The proxy tears the socket down and
rebuilds it, which usually revives the node.

It is a hand-rolled HTTP responder, not a framework: read one request line,
write one JSON response, close. That keeps the add-on's dependency list at
`python3` and nothing else.

---

## How it forwards

The companion TCP link is framed, contrary to what the earliest versions of this
proxy assumed:

| Direction | Marker | Then |
|---|---|---|
| client → node | `0x3C` (`<`) | 16-bit little-endian length, payload |
| node → client | `0x3E` (`>`) | 16-bit little-endian length, payload |

Both loops buffer, parse complete frames, and **resynchronise** on anything they
do not understand — scanning forward for the next marker and forwarding the
unrecognised bytes rather than dropping the connection. Full specification:
[`protocol.md` §2.1](protocol.md#21-framing).

Two exceptions to plain forwarding:

### `CMD_APP_START` is answered by the proxy

The node answers `APP_START` **once per TCP session**, and the proxy uses that up
during its own handshake. Every client's registration would therefore be ignored
by the node, leaving clients stuck on "connecting" or "failed to fetch device
info". This was the root cause of nearly all connection problems before 1.8.1.

`dispatch()` caches the node's `SELF_INFO` reply (packet type `0x05`) and
`handle_client()` answers each client's `APP_START` from that cache. The cached
frame survives a reconnect, so clients can still register while the node
connection is hiccuping.

### Commands are paced

`_exchange()` enforces `MIN_CMD_GAP_S` (0.25 s by default) between two commands
to the node. Directly against the node only one client fit; through the proxy
they all get through at once, and several clients together can overwhelm a small
radio device. Pacing gives the node the same calm stream it saw from a single
client.

### Everything else is broadcast

Since 1.8.0, **every frame from the node goes to every client**, and clients
match replies to their own commands themselves — exactly as they would when
connected directly. The earlier "reply only to the asker" routing could deliver
an answer to the wrong client, or lose it entirely, when several clients were
active.

The lock in `_exchange()` therefore no longer serialises whole exchanges. It is
short, and does one thing: stop frames from two clients interleaving mid-frame on
the wire. Nothing waits for a reply, so a busy client can never block the line.

---

## Keeping the node connection alive

`upstream_loop()` and `keepalive_loop()` between them implement four behaviours,
each a response to an observed failure:

| Behaviour | Why |
|---|---|
| `APP_START` immediately on connect | A node closes a connection that never registers. A "silent" proxy fails |
| `GET_DEVICE_TIME` keepalive every 30 s | A node closes a connection that stays quiet |
| Handshake watchdog: one retry at half time, rebuild after the full timeout | A node that accepts TCP and answers nothing is a stalled firmware; a fresh session usually revives it |
| Reconnect backoff up to `MAX_RECONNECT_S` | A node with a stalled network stack should not be hammered every second |

The watchdog carries a scar worth naming. Each `_handshake_watchdog()` guards
**precisely the connection it was started for** (`self.up_writer is not writer`
aborts it otherwise). Before 1.8.3, watchdogs from earlier attempts piled up on a
slow link and tore down new, healthy connections — the "node answering /
connection lost" loop every few seconds in the logs.

Replies to the proxy's own handshake and keepalive frames are sent through
`_send_internal()`, which does not wait for or claim the response; the reply is
simply broadcast like any other node frame.

---

## Client management

| Situation | Behaviour |
|---|---|
| A new client arrives with slots free | Accepted, registered with its address and last-send time |
| A new client arrives with all slots full | An **idle** session (nothing sent for `IDLE_EVICT_S`) is evicted for it. If every session is active, the newcomer is refused with a log line |
| A client has sent nothing for `IDLE_EVICT_S × 3` | Cleaned up during the keepalive round |
| A write to a client fails | That client is dropped from the broadcast set |
| The node connection drops | Clients **stay connected** while the node is within `NODE_DOWN_GRACE_S`; they see no data and carry on afterwards |
| The node has been gone longer than the grace period | All clients are dropped, with the reason logged. They reconnect by themselves |

The grace period is the point. On a flaky link, dropping clients on every
interruption causes a reconnect storm that is worse than the interruption.

---

## Access control

`allowed_ips` is a list of addresses or CIDRs. An invalid entry is **rejected
loudly at startup** — `parse_allowed()` logs the offending value and exits rather
than silently running with a half-parsed list.

`ALWAYS_ALLOWED` covers localhost and the container's default gateway, read out
of `/proc/net/route`. Connections from the Home Assistant host arrive through the
Docker port mapping with the internal gateway as their source address, so without
this they would be blocked by their own operator's allow-list.

**The MeshCore TCP protocol has no authentication and no encryption.** Anyone who
can reach this port controls your radio: send messages as you, read your traffic,
change settings. Keep it inside a trusted network, set `allowed_ips`, and never
port-forward it to the internet — use a VPN. See [`security.md`](security.md).

---

## What it does not solve

- **Message sync is destructive in the companion protocol.** With several clients
  connected, a chat message is consumed by whichever client syncs first. It will
  appear in one client, not all. Telemetry, management and statistics are
  unaffected — which is why this is acceptable for MeshStats' purposes and not
  for a chat-first setup.
- **The node still has one slot.** The proxy occupies it. Any client that
  connects to the node directly will fight the proxy for it, and both will lose.
- **No statistics, no MQTT, no MeshStats server.** Transport only.

---

## Troubleshooting

| Symptom | Where to look |
|---|---|
| Clients stuck on "connecting" / "failed to fetch device info" | Status page: is `node_answering` true? If the cached `self_info` was never captured, the proxy cannot answer client handshakes |
| Log loops between "node answering" and "connection lost" | The pre-1.8.3 watchdog bug. Upgrade |
| A client is refused | `max_clients` reached with every session active. Raise it or find the stuck client in `clients` |
| Nothing connects at all | `allowed_ips` — check the source address the connection actually arrives with, not the one you expect |
| The node reboots and clients hang | Expected for up to `NODE_DOWN_GRACE_S`; after that clients are dropped and reconnect |
| Chat messages appear in only one client | Working as designed. See above |

`log_level: debug` adds per-frame detail, including cached `APP_START` answers and
resynchronisation events.

---

## Version history in brief

Full detail in `proxy/mc-proxy/CHANGELOG.md`. The turning points:

| Version | Change |
|---|---|
| 1.0.0 | First release: fan-out with automatic reconnection |
| 1.1.x | Allow-list, client cap, host/gateway always allowed |
| 1.2.0–1.3.0 | Smart routing and exchange serialisation — both later reversed |
| 1.4.0 | **Real frame parsing.** The transport does use framing after all; earlier versions routed nearly everything wrongly |
| 1.5.0 | Own handshake and keepalive towards the node |
| 1.6.0 | Self-healing against a stalled node |
| 1.7.0 | Status page on 5001 |
| 1.8.0 | **Broadcast everything**; routing by asker removed |
| 1.8.1 | **The proxy answers `APP_START` itself** — root cause of nearly all connection problems |
| 1.8.2 | Patience on weak links: longer timeouts, cached `self_info`, grace period |
| 1.8.3 | Watchdogs guard only their own connection |
| 1.8.4 | Command pacing |

The pattern is worth reading as a whole: three of these releases undo a
cleverness from an earlier one. Routing by asker, serialising exchanges and
aggressive reconnection all sounded correct and all made things worse in the
field.

---

## See also

| | |
|---|---|
| The frame format it parses | [`protocol.md` §2](protocol.md#2-the-companion-protocol-tcp-and-serial) |
| The firmware alternative | [`firmware.md`](firmware.md#1-multiple-companions-on-one-node) |
| Why an open TCP port matters | [`security.md`](security.md) |
| Installing it beside the site | [`deployment.md`](deployment.md#home-assistant-components) |
| The other Home Assistant component | [`homeassistant.md`](homeassistant.md) |
