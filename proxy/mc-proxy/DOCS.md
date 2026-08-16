# MeshCore Proxy

MeshCore companion firmware accepts only **one TCP client at a time**. This
add-on holds that single connection to your WiFi node and shares it with as
many clients as you like:

```
WiFi node  <-->  MeshCore Proxy (this add-on)  <-->  meshcore-ha integration (127.0.0.1:5000)
                                               <-->  MeshCore app (your-HA-IP:5000)
                                               <-->  meshcore-cli
```

If you are able to flash modified firmware, you do not need this: the MeshManager
firmware's `SerialWifiInterface` handles four simultaneous clients on the node
itself, with proper reply routing. See
[`docs/firmware.md`](../../docs/firmware.md#1-multiple-companions-on-one-node).
Use the proxy when changing firmware is not an option.

## Setup

1. Set `node_host` to the IP of your MeshCore WiFi node and start the add-on.
2. Point the **meshcore integration** at TCP host `127.0.0.1`, port `5000`.
3. Connect the **MeshCore app** to your Home Assistant machine's IP, port `5000`.
4. Make sure nothing connects to the node directly anymore — the node has
   only one slot, and a direct client will fight the proxy over it.

## Options

| Option | Default | Description |
|---|---|---|
| `node_host` | — (required) | IP/hostname of the MeshCore WiFi node |
| `node_port` | `5000` | TCP port of the node |
| `allowed_ips` | `[]` | Allow-list of client IPs/CIDRs (e.g. `192.168.1.0/24`). Empty = every client allowed. **Recommended to set.** |
| `max_clients` | `8` | Maximum simultaneous clients |
| `log_level` | `info` | `debug` / `info` / `warning` |

Connections from the Home Assistant host itself (localhost and the container
gateway address) are always allowed, even with `allowed_ips` set — the port
mapping makes them arrive with the internal gateway as source address.

The add-on listens on **5000** and serves a JSON status page on **5001**.
Changing the port in the add-on's **Network** section remaps the host-side port;
the proxy itself always listens on 5000 inside the container.

Options exposed in the add-on UI map onto a subset of the environment variables
the proxy understands. The rest keep their built-in defaults and are not
reachable from Home Assistant.

## Status page

`http://<your-HA-IP>:5001/` returns JSON:

| Field | Meaning |
|---|---|
| `node_host` | The upstream node |
| `node_connected` | Whether the upstream socket is open |
| `node_answering` | Whether the node is replying, not just accepting TCP |
| `seconds_since_node_data` | Age of the last byte from the node |
| `silent_keepalive_rounds` | Consecutive keepalives with no answer |
| `clients` / `client_count` / `max_clients` | Connected clients |

`node_connected` true with `node_answering` false is the specific failure this
proxy was built to detect: companion firmware can reach a state where it still
accepts TCP but answers nothing. The proxy tears the socket down and rebuilds it,
which usually revives the node.

## How it forwards

The companion TCP link is framed: a marker byte (`0x3C` client→node, `0x3E`
node→client), a 16-bit little-endian length, then the payload. The proxy parses
complete frames in both directions and resynchronises on the next marker if it
sees anything it does not understand, rather than dropping the connection. Full
specification: [`docs/protocol.md`](../../docs/protocol.md#2-the-companion-protocol-tcp-and-serial).

Since 1.8.0 **every frame from the node goes to every client.** Clients match
replies to their own commands themselves. The earlier "reply only to the asker"
routing could deliver an answer to the wrong client or lose it entirely when
several clients were active.

Two exceptions to plain forwarding:

- **`CMD_APP_START` is answered by the proxy.** The node answers it only once per
  TCP session — the proxy uses that up during its own handshake. It caches the
  node's `SELF_INFO` reply and answers every client's handshake from the cache.
- **Commands are paced**, at least 0.25 s apart, so several clients cannot
  overwhelm a small radio device.

## Security

The MeshCore TCP protocol has **no authentication or encryption** — anyone
who can reach this port controls your radio. Keep it inside your trusted
network, set `allowed_ips`, and **never** port-forward it to the internet
(use a VPN for remote access).

## Good to know

- Message sync is destructive in the companion protocol: with several clients
  connected, a chat message is consumed by whichever client syncs first. It
  will show up in one client, not all. Telemetry and management are unaffected.
- If the node reboots, the proxy reconnects automatically within seconds;
  clients stay connected to the proxy throughout. Clients are only dropped when
  the node has been unreachable for longer than the grace period.
- The proxy carries no statistics and never talks to a MeshManager server. It is a
  transport helper only.

Full documentation: https://github.com/DinXke/MeshCore-Proxy
