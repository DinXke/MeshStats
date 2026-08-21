# Managing the rooms of a room-server node

*[Nederlands](nl/rooms.md)*

A MeshUptime room-server node hosts several virtual *rooms* — shared channels
that clients can join and that sensors can post their alarms into. The site
reads and fully manages those rooms over the node's own HTTP API, on the node
page under **Beheer over IP → Rooms**. Everything here runs over the node's WiFi,
the same path as the rest of that block: when the WiFi is gone, this block is
gone with it.

## What a room-server node is

It is a node that answers `GET /rooms.json` behind the same Basic-auth login as
the rest of its API. Room 0 is the node's own identity; rooms 1 and up each have
their own keypair. If a node does not answer `/rooms.json`, the site says so and
draws nothing — it is not a room-server node.

## Reading the rooms

The Rooms section shows, per room: the name, the `idx`, whether it is stealth or
allows guests, the number of posts, and the short public key. The list is read
fresh from the node every time you open the page, and once per polling round in
the background.

## Adding, editing and deleting

Adding takes a name; the node bakes the key. Editing changes only the fields you
give — leave a name, room password or guest password blank and it is left
untouched. Clearing the guest password is a separate switch ("gast wissen"), so a
partial edit with an empty guest field never wipes an existing guest password;
the stealth flag is a checkbox and is always sent. Deleting asks for a typed
confirmation, because the room's key goes with it. All three need the
`node.instelling.merkbaar` right, the same class as setting a region.

## The join link and QR

Each room carries a join URI. The site renders a QR for it server-side, as
inline SVG, with no external library and no CDN — the strict Content-Security-
Policy of the site would block those anyway. The plain link sits next to the QR
so it works without a camera.

## Per-sensor alarm routing

Each monitored sensor has an alarm route: direct message, into a room, or both
(`am` = 1/2/3), plus which rooms it targets (`rm`, a bitmask) and which virtual
sensor-nodes its telemetry goes to (`sn`, a bitmask). The form shows a route
dropdown, a checkbox per room and a checkbox per sensor-node; the server builds
the bitmasks and sends `am`/`rm`/`sn` together. The current state is read from
`/status.json`, which the site fetches anyway, so there is no extra request. Below
each room you also see which sensors post their alarm into it.

## Virtual sensor-nodes

The same node also hosts virtual *sensor-nodes*: separate contact identities under
which telemetry shows up in the MeshCore app. They are read from the same
`/rooms.json` call (`snode_max`/`snode_active`/`snodes`) and managed symmetrically
with the rooms — add, edit (name and stealth), delete, and a contact QR + link
from the `uri` field. Each sensor-node lists the channels bound to it; the node
sends only the channel numbers and the site fills in their names from its own
channel-name data. The alarm routing above decides which sensor's reading goes to
which sensor-node.

## SNMP monitors

A node can also monitor an external device over SNMP: it polls an OID on a host
at an interval. The site adds one through `POST /monitor/snmp` (fields
`name`/`host`/`int`/`community`/`oid`/`interp`/`snmparg`), with a **preset OID
library** so nobody types raw OIDs — interface counters (ifHCInOctets,
ifHCOutOctets), ifOperStatus, and UPS-MIB (battery status, estimated minutes
remaining, load, input/output voltage). A manual OID plus interpretation
(`numeric`/`rate`/`status`) is there for anything the presets do not cover, and
an index field fills `snmparg` for per-interface or per-UPS-line OIDs. The
**community is a secret**: it is sent to the node but write-only on the site —
never shown back and never logged, and `/status.json` reports only `knd`/`itp`/
`oid` for an SNMP monitor. A new SNMP channel can be coupled to a room or
sensor-node in the same step, and afterwards routes like any other channel.

**Discovery** removes the OID typing entirely. The **server** (which has a rich
SNMP stack) walks the device and offers a checklist of what is monitorable; on
confirm it creates the matching node monitors, which the node then polls. It
walks the system group (sysName/sysDescr/sysUpTime/sysObjectID), the interface
tables (per interface: in/out traffic as a rate from the 64-bit HC octet
counters, and oper-status up/down), and UPS-MIB when present (battery status,
minutes remaining, charge %, load, input/output voltage); a generic
`snmpwalk <subtree>` is there for power users. Each checked item becomes a node
monitor with the right OID + interpretation and is coupled to a chosen
room/sensor-node in the same step. The **community is re-entered** on the confirm
step rather than carried in the page — write-only, never rendered or logged.

The SNMP stack is **net-snmp** (`snmpget`/`snmpbulkwalk`), a small OS package in
the image (`snmp`) rather than a Python dependency — see `server/app/snmp.py` for
the reasoning; if it is missing, discovery says so instead of failing. Discovery
runs from the **server**, so the server must reach the device over UDP/161 (fine
on a LAN). A device reachable only from the node is a known limitation —
node-side discovery is a later option. The preset-OID library above stays as the
quick manual path next to discovery.

## The notifier bot

A node can run a *notifier bot*: an identity that sends alerts as a direct
message to a recipient list, or posts them on its own channel. The site shows the
bot's name, public key and a contact QR + link (`GET /bot.json`), and manages the
recipient list — add a full public key or remove one on a prefix
(`POST /bot/recipient`). Buttons send an advert (`POST /bot/advert`), a test DM to
one key (`POST /bot/sendto`), or a post to the whole list (`POST /bot/post`).
Recipients are public keys; there is no secret in the list. Message bodies are not
written to the audit trail — only who a DM went to.

## Access control (ACL)

Each room and each sensor-node slot carries a per-key access list: which public
key may enter, at which level — `read` (join and read: a room's posts, a
sensor-node's telemetry), `readwrite` (also write), or `admin` (manage that
slot). It is **password-free**: a key on the list gets in at its level without a
password. The site reads the list from the `acl` array of `/rooms.json` (never a
secret, only public keys and levels) and shows it per room/sensor-node. Adding
requires the full 64-hex public key; removing works on a prefix of at least 12
hex; changing a level re-adds the key at the new level. All of it runs through
`POST /room/acl` and `POST /snode/acl`. Everywhere a public key is entered — the
ACL grants and the bot recipient list — the node's **discovered contacts**
(`GET /contacts.json`) feed a name→key chooser next to the manual field, so you
pick a contact by name and the full key is filled in.

## Backup and restore

A backup contains the rooms' **private keys**. The server keeps backups as a
store with version history, obfuscated with the same layer as the per-node
passwords (see per-node-credentials.md): not readable in a database dump, and
never sent anywhere. Making a backup, downloading one, and restoring are limited
to a server administrator. A restore overwrites the current rooms and asks for a
typed confirmation; it can take a stored backup or pasted JSON.

## Grouping: many entities, one node

Because rooms, sensor-nodes and the notifier bot each advertise their own keys,
they appear on the mesh as separate node entries. The site persists which key
belongs to which physical node, with its kind (room, sensor or bot) — learned from
`/rooms.json` and `/bot.json` — so the node list marks a loose entry as "room on
node X", "sensor-node on node X" or "bot on node X" and marks its owner as "host
of N rooms + M sensor-nodes + a bot", instead of leaving them floating as
anonymous unmanaged nodes. The mapping is pruned when an entity is removed from
the node.

## The node contract and its assumptions

The site talks to `GET /rooms.json`, `POST /room/add|edit|del`, `POST
/snode/add|edit|del`, `POST /room/acl` and `POST /snode/acl` (form `idx`/`pubkey`/
`level`, or `del=1` with a prefix), `POST /mon/alarm`, `POST /monitor/snmp`,
`GET /bot.json`, `POST /bot/recipient|advert|sendto|post`, `GET /contacts.json`,
`GET /rooms/backup` and `POST /rooms/restore`. The alarm route is set
channel-based via `POST /mon/alarm` (form `ch`/`am`/`rm`, optionally `sn`, where
`ch` is the monitor's channel from `mon[].ch` and wins on the node). The
node-centric channel panel reuses that same setter — checking a channel on a
room/sensor-node only flips that entity's `rm` resp. `sn` bit, leaving the other
masks alone. Creating a channel goes through the existing `POST /monitor` (form
`name`/`host`/`int`), and an SNMP channel through `POST /monitor/snmp`; both reply
in plain text (`ok <name> -> kanaal <N>`) — the site parses the channel number out
and couples it. Adverts use `POST /room/advert` / `POST /snode/advert` (form
`idx`/`flood`). All of these live isolated behind the `MON_ALARM_*`/
`MONITOR_ADD_PATH`/`SNMP_MONITOR_PATH`/`*_ADVERT_PATH`/`BOT_*`/`CONTACTS_PATH`
constants and their functions in `server/app/rooms.py`, so a differing contract is
a small change. The network boundary itself stays in `sensornode.py`, behind the
same target check and fleet/per-node credential as every other call to a node.

SNMP **discovery** is the one part that does not talk to the node: it talks to the
target device directly over SNMP from the server (`server/app/snmp.py`, net-snmp),
and then feeds its results back into `POST /monitor/snmp` — so it adds no new node
contract.
