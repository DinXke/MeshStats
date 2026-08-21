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
give — leave the name or password blank and it is left untouched; the guest and
stealth flags are checkboxes and are always sent. Deleting asks for a typed
confirmation, because the room's key goes with it. All three need the
`node.instelling.merkbaar` right, the same class as setting a region.

## The join link and QR

Each room carries a join URI. The site renders a QR for it server-side, as
inline SVG, with no external library and no CDN — the strict Content-Security-
Policy of the site would block those anyway. The plain link sits next to the QR
so it works without a camera.

## Per-sensor alarm routing

Each monitored sensor has an alarm route: direct message, into a room, or both
(`am` = 1/2/3), plus which rooms it targets (`rm`, a bitmask). The form shows a
route dropdown and a checkbox per room; the server builds the bitmask. The
current state is read from `/status.json`, which the site fetches anyway, so
there is no extra request. Below each room you also see which sensors post their
alarm into it.

## Backup and restore

A backup contains the rooms' **private keys**. The server keeps backups as a
store with version history, obfuscated with the same layer as the per-node
passwords (see per-node-credentials.md): not readable in a database dump, and
never sent anywhere. Making a backup, downloading one, and restoring are limited
to a server administrator. A restore overwrites the current rooms and asks for a
typed confirmation; it can take a stored backup or pasted JSON.

## Grouping: many rooms, one node

Because rooms 1+ advertise their own keys, they appear on the mesh as separate
node entries. The site persists which room key belongs to which physical node —
learned from `/rooms.json` — so the node list marks a loose room entry as "room
on node X" and marks its owner as "host of N rooms", instead of leaving the rooms
floating as anonymous unmanaged nodes. The mapping is pruned when a room is
removed from the node.

## The node contract and its assumptions

The site talks to `GET /rooms.json`, `POST /room/add|edit|del`, `GET
/rooms/backup` and `POST /rooms/restore`. Setting a sensor's alarm route has no
fixed setter URL in the contract yet; that one assumption lives isolated behind
`MON_ALARM_PATH` and `set_alarm` in `server/app/rooms.py`, so a differing
contract is a one-line change. The network boundary itself stays in
`sensornode.py`, behind the same target check and fleet/per-node credential as
every other call to a node.
