# Home Assistant via MQTT discovery

*[Nederlands](nl/ha-integratie.md)*

MeshManager can make its telemetry appear in Home Assistant **by itself**, as
regular HA entities, without a custom component and without editing any YAML.
It publishes MQTT *discovery* messages to the same broker Home Assistant is
connected to — exactly the way Uptime Kuma already publishes its entities there.

This is the reverse direction of [`homeassistant.md`](homeassistant.md): that
integration reads data *out of* Home Assistant and pushes it *to* MeshManager.
This one pushes *from* MeshManager *into* Home Assistant.

## What it does

A node running MeshManager already publishes its statistics to MeshManager's own
MQTT broker (`MM_MQTT_*`). Home Assistant, in our setup, is connected to a
*different* broker — an EMQX on the LAN. This feature opens a **second, separate
MQTT connection** to that HA broker and publishes discovery + state there. The
two brokers never mix: separate client, separate credentials, separate reconnect
loop, in a background thread that never blocks or crashes the app if the HA
broker is away.

Each node becomes one HA **device**. Under it hang the entities: battery
voltage, mains/battery/wifi state, one connectivity sensor per ping monitor with
its response time, a node-online sensor, and an "active fault" sensor. All
object-ids and unique-ids carry a `meshmanager_` prefix, so nothing collides
with your existing MeshCore scripts or the Uptime Kuma entities.

## Turning it on

It stays **off** until both the broker host and the switch are set — the same
rule as the VAPID keys for web push. Set these environment variables (see
`.env.example`, and in Docker they are already wired into `docker-compose.yml`
with the usual `MM_`/`MCS_` fallback):

```
MM_HA_MQTT_HOST=10.10.10.100
MM_HA_MQTT_PORT=1883
MM_HA_MQTT_USER=meshmanager
MM_HA_MQTT_PASS=your-password
MM_HA_DISCOVERY_ENABLED=1
```

Optional: `MM_HA_DISCOVERY_PREFIX` (default `homeassistant`),
`MM_HA_STATE_PREFIX` (default `meshmanager/ha`), `MM_HA_SCOPE` (default
`monitored`) and `MM_HA_STALE_MIN` (default `20`). The current state, the reason
it is off, and the number of published entities are shown on the server page
under **Home Assistant (MQTT-discovery)**, and the reason is printed to the
startup log.

State is published the moment a measurement arrives — the module hooks into the
ingest path, it does not poll — so an entity in HA updates as fast as the node
reports. A slow background pass (every 60 s) re-checks availability and pushes
fault state even when no new measurement came in.

## Creating an EMQX user

MeshManager needs its own account on the EMQX broker. In the EMQX dashboard:

1. Go to **Access Control → Authentication** and add a user, e.g. `meshmanager`
   with a long password. Put that same password in `MM_HA_MQTT_PASS`.
2. Go to **Access Control → Authorization** and add ACL rules that allow this
   user to publish only under its own topics (see the ACL section below).
3. Restart MeshManager (or the container). The entities appear in Home Assistant
   within a minute, the same way the Uptime Kuma entities did.

## The entities

Per node (device), depending on what the node reports:

| Source | HA entity | Type |
|---|---|---|
| Battery voltage (`ch1_voltage`, `bat`) | sensor, `voltage`, V | `sensor` |
| Mains present | binary, `power` | `binary_sensor` |
| Running on battery | binary (on = on battery) | `binary_sensor` |
| WiFi | binary, `connectivity` | `binary_sensor` |
| Ping monitor up/down | binary, `connectivity`, **named after the channel** | `binary_sensor` |
| Ping monitor response time | sensor (ms) | `sensor` |
| Node online / heartbeat | binary, `connectivity` | `binary_sensor` |
| Active fault | binary, `problem` | `binary_sensor` |
| Repeater telemetry (airtime, noise floor, …) | sensor | `sensor` |

The ping-monitor entity takes its name from the channel name you set in
MeshManager (`channel_names`) — that is the whole point: "google" reads better
in HA than "channel 6". A switch channel's device_class is derived from that
name (wifi → connectivity, mains → power, battery → none, everything else →
connectivity), which keeps the common case (a ping monitor) correct.

For the "active fault" we chose a single `binary_sensor` with device_class
`problem` per node, driven by the count of open alerts, rather than a text
sensor with the last alert. It does one robust thing — is something wrong,
yes/no — which is exactly what an HA automation needs to notify on. The alert
text itself already lives on the site's alert list; mirroring it here would be a
second source that can drift.

## Availability and cleanup

Every entity is tied to two availability topics with `availability_mode: all`: a
bridge topic (with an MQTT last-will, so *everything* goes unavailable if
MeshManager falls away) and a per-node topic (which goes `offline` when that
node has been silent longer than `MM_HA_STALE_MIN` minutes). So a single node
that goes quiet shows grey in HA without dragging the others down.

Discovery config topics are **retained**, so an entity that must go away is
cleaned up actively: MeshManager remembers per node which entities it published
(in the `settings` table, so it survives a restart) and clears the config topic
(a retained empty message) for anything no longer wanted. A ping monitor that
disappears from a node — and whose `latest` row is eventually pruned by
retention — leaves no ghost entity. A node that falls out of scope is cleared
the same way.

## Scope

`MM_HA_SCOPE` decides which nodes are published:

- `sensors` — only the sensor nodes (those with a `sensor_host` / own API).
- `monitored` — the sensor nodes plus repeaters that actually report telemetry
  (battery, airtime, noise floor). **This is the default.** A repeater that was
  only ever *heard* as a neighbour has none of those and stays out, so HA is not
  filled with dozens of meaningless entities.
- `all` — every tracked repeater in the database.

## Security (ACL)

This publishes to a broker on your LAN. Give the MeshManager EMQX user an ACL
that lets it publish **only** under its own topics, nothing else:

```
# allow publish
homeassistant/#            (discovery config — HA requires this prefix)
meshmanager/ha/#           (state + availability)
```

Deny everything else for this user. That way a leaked MeshManager credential
cannot publish arbitrary discovery messages that would create rogue entities
elsewhere in Home Assistant.

## Troubleshooting

- **Server page says "off".** The reason is on the line itself: host not set, or
  the switch not set. Both are required.
- **"on, but not connected".** Wrong host/port, or the broker refuses the login
  — the last error is shown. Check `MM_HA_MQTT_USER` / `MM_HA_MQTT_PASS` and the
  EMQX authentication.
- **Entities appear but stay "unavailable".** Check that HA's MQTT integration
  is pointed at the *same* EMQX broker, and that the ACL allows
  `meshmanager/ha/#` — the availability topics live there.
- **An old entity will not go away.** It is a retained config topic. MeshManager
  clears the ones it knows about; a manually created one can be cleared by
  publishing an empty retained message to its `.../config` topic.
