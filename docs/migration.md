# Migrating from MeshStats to MeshManager

*[Nederlands](nl/migration.md)*

The project was called MeshStats until 2026-08-16. Everything it owns has been
renamed: the site, the server's environment variables, the MQTT topic prefix,
the firmware module, the Home Assistant integration domain, the container names
and the release images.

**If you have nothing running, ignore this document.** A fresh install is
already MeshManager everywhere. This page is for an installation that is
currently working, and its whole subject is a single question: in which order do
you update so the data never stops flowing?

---

## Why order matters

Nodes and server never update at the same moment. There is always a window in
which one side speaks the new names and the other the old ones, and only one
side of the chain can be taught to understand both.

| | Understands both | Why |
|---|---|---|
| **Server** | yes | It subscribes to `meshmanager/+/stats` **and** `meshcore/+/stats`, reads both `MM_*` and `MCS_*`, opens an existing `mcs.sqlite3`, and accepts both `fw_meshmanager` and `fw_meshstats` in a payload |
| **Node** | no | It publishes on exactly one prefix. Which one is a setting, not a negotiation |

So: **server first, then the nodes.** The other way round, a flashed node
publishes to `meshmanager/…` while nobody is subscribed, and nothing anywhere
reports an error — the node's publish succeeds, the broker accepts it, and the
site simply goes quiet. That is the failure mode this whole project was built
to make impossible, so do not create it during the rename.

---

## The order

### 1. Broker (before the server, and it takes a minute)

If you run an ACL — and you should — the broker must allow both prefixes before
anything else moves. A node whose publish is refused by the ACL is told nothing:
it reports a successful publish, the message is dropped, and the site waits for
numbers that never come.

`mosquitto/acl.example`, `mosquitto/init-passwd.sh` and
`mosquitto/add-node-user.sh` already generate both sets of rules. For an
existing `mosquitto/acl`, add the `meshmanager/…` line next to every
`meshcore/…` line you already have, then reload the broker.

### 2. Server

```bash
git pull
docker compose build
docker compose up -d --remove-orphans
```

`--remove-orphans` is not optional: the compose services were renamed, so
without it the old `meshstats` container keeps running next to the new one,
holds port 8080, and the new one never starts.

Nothing else is required. Specifically, you do **not** need to touch:

- **your `.env`** — every `MCS_*` name is still read. Rename them at your
  leisure; the new `MM_*` name wins if both are set.
- **your database** — an existing `mcs.sqlite3` is opened where it is and is
  never renamed. See [why](#things-that-deliberately-keep-their-old-name).
- **your Docker volumes** — the compose project name is now pinned to
  `meshstats`, so the volumes keep the names they have.

Check afterwards on `/admin`: the MQTT block lists how many nodes arrive on
which prefix. All of them will still say `meshcore` — that is correct, you have
not flashed anything yet.

### 3. Nodes

Flash firmware **2.0.0** or later. Over the air via `/admin/firmware`, or over
USB.

The site translates the old build-environment name to the new one exactly once,
so a node still running 1.12.0 is offered the right image even though the
release is now built as `heltec_v4_repeater_meshmanager`. Without that
translation, 2.0.0 would only be installable with a cable.

Building it yourself? Rename `-D MESHSTATS_NET` to `-D MESHMANAGER_NET` in your
own `platformio.local.ini`. Forget it and you get a build that compiles, flashes
and boots — as a plain MeshCore repeater, with no management page and no MQTT,
and without a single error message.

**Confirming a node came across**, two independent ways:

- `ver` on any CLI must answer `MeshManager (by DinX) v2.0.0`. Anything else,
  including a stock MeshCore answer, means the module is not running.
- `/admin` shows the node under prefix `meshmanager` within one publish
  interval, and its module version on the node's own page.

What the node does by itself, and what it does not:

- Its **configuration survives**. `/msnet.json`, `/mspwr.json`, `/msmon.json`
  and `/adverts.dat` on the data partition keep their names, and an OTA does not
  touch that partition. WiFi credentials, broker settings and the monitor list
  are all still there.
- Its **topic prefix moves itself**, once, from `meshcore` to `meshmanager` —
  but only if it was literally on the old default. If you deliberately chose
  something else, nothing happens. And if you deliberately set it back to
  `meshcore` after this upgrade, it stays there: the move is recorded with a
  `cfg_ver` in the config file and is not repeated.
- A **companion node does not move by itself**. Set it on its own management
  page, or with `wifi mqtt prefix meshmanager`.

### 4. Home Assistant, if you use the integration

A domain rename is a new integration to Home Assistant, and there is no
migration hook across domains — `async_migrate_entry` works inside one domain,
not between two. So this part is manual:

1. Settings → Devices & Services → remove the **MC Repeater Stats** entry.
2. Delete `custom_components/mc_repeater_stats/`, copy
   `custom_components/meshmanager/` in its place.
3. Restart Home Assistant.
4. Add **MeshManager** and enter the same base URL and token.

Automations that call `mc_repeater_stats.*` services must be changed to
`meshmanager.*`. The integration creates no entities of its own — it reads
MeshCore entities and pushes them — so nothing else moves, and no history is
lost.

### 5. Cleaning up, later and only when you can see it is safe

None of this is urgent, and every fallback carries a note in the code saying
when it may go.

| Remove | When |
|---|---|
| `meshcore/…` lines from `mosquitto/acl` | `/admin` no longer lists any node on the `meshcore` prefix |
| `MCS_*` from your `.env` | after one successful restart on the new names |
| `LEGACY_PREFIX` in `mqtt_ingest.py` | same condition as the ACL lines |
| `ENV_ALIAS` in `firmware.py` | no node reports the old build environment any more |
| the old asset name in `ASSET_RE` | no release in the list carries `meshstats-*.bin` any more |
| `/opt/mc-repeater-stats` (systemd install) | after the new service has run for a while |

---

## Things that deliberately keep their old name

Four names did not move, and each for the same reason: they are not in the code,
they are **in the data**. Renaming them does not change a label — it makes
existing data unreachable while everything appears to work.

| Name | What it is | What renaming would cost |
|---|---|---|
| `mcs.sqlite3` | your database file | Nothing, if you rename it while the site is stopped. But the site does not rename it for you: rolling back to the previous version would then find no database, create an empty one, and greet you with a site without repeaters, without history and without an admin password |
| `meshstats-data`, project name `meshstats` | the Docker volume the database lives in | A new, empty volume beside a full one nobody opens. The compose project name used to follow the directory name, which means renaming your clone would have done this silently — and the auto-update runs unattended every five minutes. It is pinned now |
| `meshstats` in VictoriaMetrics | the series name of every measurement ever recorded | Every chart starts at zero on the day of the upgrade, with the history still there but only findable by typing a different series name by hand. `MM_TSDB_MEASUREMENT` exists for whoever wants to move it after renaming the series in VictoriaMetrics itself |
| `/msnet.json` and friends | the node's config on its data partition | A node that comes back from an OTA without WiFi credentials, without broker settings and without its monitor list — as its own access point, on a roof |

You can still do all four by hand later. They are one-way moves, which is why
they are not done for you.

---

## Renaming the GitHub repository

Not something the repository can do to itself, and not something that needs to
happen at the same time as any of the above. See
[`contributing.md`](contributing.md#renaming-the-repository) for the steps and
for what has to be adjusted afterwards.
