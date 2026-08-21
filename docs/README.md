# MeshManager documentation

*[Nederlands](nl/README.md)*

Everything written down about this project, grouped by what you are trying to
do. Each entry says in one sentence what you will find there, so you do not have
to open five files to locate the right one.

Every document exists in English at `docs/<name>.md` and in Dutch at
`docs/nl/<name>.md`, with the same headings. The link at the top of each page
switches language.

---

## Start here

| Document | What you will find |
|---|---|
| [Repository README](../README.md) | What this project is, what the site can do, and a five-command quick start |
| [`architecture.md`](architecture.md) | How the pieces fit together, which paths data can take from a radio to the site, and why the transport is MQTT rather than HTTP |
| [`glossary.md`](glossary.md) | Advert, flood, direct, scoped, hop, address hash, path hash, transport codes, companion, repeater, monitor — the vocabulary the rest of these documents assume |
| [`migration.md`](migration.md) | Coming from MeshStats: in which order to update a running installation so the data never stops flowing, and which four names deliberately did not move |
| [`deployment.md`](deployment.md) | Installing and running the site: Docker Compose, systemd without Docker, environment variables, reverse proxies, backups, upgrades |

New to MeshCore itself? Read the [glossary](glossary.md) first, then
[`architecture.md`](architecture.md). New to this repository as a developer? Read
[`contributing.md`](contributing.md) before your first change.

---

## Managing nodes

The heart of this documentation. Everything else describes a part; this
describes the job.

| Document | What you will find |
|---|---|
| **[`node-management.md`](node-management.md)** | **The walkthrough.** The three management levels and how to see which one a node has, bringing a node under management, reading and writing its settings with the three risk tiers, setting the clock, upgrading and rolling back firmware, and what to do when a node does not come back — with screenshots of the admin pages |
| [`rooms.md`](rooms.md) | Managing a MeshUptime room-server node over its own HTTP API: reading and managing rooms and virtual sensor-nodes, the join/contact QR and link, per-key access control (ACL), node-centric channel management, manual adverts, server-side backups, and how loose room/sensor-node entries are grouped back onto their physical node |

Read that one first. It links onward to the pages that go deeper on a single
step: [`admin.md`](admin.md) for every field and form,
[`commanding.md`](commanding.md) for how a route is computed,
[`clocksync.md`](clocksync.md) for the clock, and
[`firmware-upgrade.md`](firmware-upgrade.md) for the upgrade mechanism end to
end.

---

## The site and its API

| Document | What you will find |
|---|---|
| [`server.md`](server.md) | What runs inside `server/`: the FastAPI application, its modules, background tasks, and how the parts hold together |
| [`api.md`](api.md) | Every route the server serves — the JSON API, the public pages, the admin forms — with parameters, responses and authentication |
| [`search.md`](search.md) | The Kibana-style query language of the packet archive: syntax, fields, sorting, and the promise that nothing is silently dropped |
| [`commanding.md`](commanding.md) | How the server decides what can be asked of a repeater right now, and what a button on the page is therefore allowed to promise |
| [`clocksync.md`](clocksync.md) | Whether this machine may tell the mesh what time it is, and by which route the answer travels |

---

## The data

| Document | What you will find |
|---|---|
| [`database.md`](database.md) | Every table and column in the SQLite schema, what goes in it and why, plus how additive migrations work |
| [`decoder.md`](decoder.md) | What `server/app/packets.py` extracts from a raw frame, what it refuses to decode, and why refusing is the right answer |
| [`candidates.md`](candidates.md) | How a node is named from one byte of key, when the site is allowed to say which node it was, and when it must show all the possibilities instead |

---

## The MeshCore protocol

| Document | What you will find |
|---|---|
| [`protocol.md`](protocol.md) | The over-the-air packet format and the companion TCP/serial protocol, specified byte by byte with worked examples, reconstructed from the firmware source |
| [`mqtt.md`](mqtt.md) | Topics, payload schemas, the two commands the site may send, retention, and broker setup with per-node accounts |

[`protocol.md`](protocol.md) is worth reading even if you never run this project.
The MeshCore wire format is documented nowhere else.

---

## The firmware

| Document | What you will find |
|---|---|
| [`firmware.md`](firmware.md) | Every change MeshManager makes to MeshCore — multiple simultaneous companions, the stats publisher, the repeater's network module — and how to build and flash it |
| [`firmware-upgrade.md`](firmware-upgrade.md) | How a node gets a new image: GitHub releases, the checksum that is verified twice, why only success reboots, how to roll back, and what a checksum does **not** prove |
| [`packet-filter.md`](packet-filter.md) | The repeater's packet filter: which forwarded packets it can drop and on what grounds, what it never touches, why blocking a channel needs a key rather than a name, and the way back when a filter was set wrong |

---

## Optional components

Neither of these is required. Both exist for situations the main path does not
cover.

| Document | What you will find |
|---|---|
| [`homeassistant.md`](homeassistant.md) | The HA integration: what it still does now that nodes publish over MQTT themselves, when you want it, how it discovers repeaters, and how it fetches CLI settings over LoRa |
| [`proxy.md`](proxy.md) | The TCP fan-out proxy that lets several clients share one node when you cannot flash modified firmware |

---

## Running and maintaining

| Document | What you will find |
|---|---|
| [`deployment.md`](deployment.md) | Environment variables, reverse proxies, automatic upgrades, backups, disk usage, logs, and the time-series database |
| [`backup.md`](backup.md) | The backup script: a consistent SQLite copy plus a VictoriaMetrics snapshot, the cron line, rotation, restoring, and the honest note that offsite is the operator's step |
| [`admin.md`](admin.md) | The operator's view of `/admin`: accounts, API tokens, sessions, and every form behind the login |
| [`retention.md`](retention.md) | How long the site keeps things, what stops the disk filling up, and why the admin page says so out loud when the configured period is not being met |
| [`security.md`](security.md) | The threat model, what is protected and how, and — as importantly — what is not |
| [`per-node-credentials.md`](per-node-credentials.md) | Giving each sensor node its own web login instead of the shared fleet credential: the model, how rotation works, the bootstrap, and the honest limit that Basic-auth over HTTP still travels readable over the LAN |
| [`privacy.md`](privacy.md) | What the site shows about other people's nodes and why it may, the three per-node visibility switches, and what no switch hides |

---

## Developing

| Document | What you will find |
|---|---|
| [`contributing.md`](contributing.md) | The conventions that explain why the code looks the way it does: honesty about uncertainty, comments that carry the why, Dutch commit messages, vanilla JS without a build step, additive migrations, `packets.raw` as ground truth |
| [`testing.md`](testing.md) | How to run the test suite, how test packets are built from the specification instead of captured, and why most tests assert a refusal |

---

## Finding things by question

| Question | Go to |
|---|---|
| **What can I actually do with my nodes?** | **[`node-management.md`](node-management.md)** |
| How do I bring a node under management? | [`node-management.md`](node-management.md) |
| Which settings am I allowed to change remotely? | [`node-management.md`](node-management.md) |
| How do I change a setting on a repeater I can only reach over LoRa? | [`node-management.md`](node-management.md) |
| My node did not come back after an upgrade | [`node-management.md`](node-management.md) |
| My commands get no answer at all | [`node-management.md`](node-management.md) |
| What does this word mean? | [`glossary.md`](glossary.md) |
| How do I get this running? | [`deployment.md`](deployment.md) |
| I am upgrading from MeshStats | [`migration.md`](migration.md) |
| What does this API endpoint return? | [`api.md`](api.md) |
| How do I search the packet archive? | [`search.md`](search.md) |
| What is in this database column? | [`database.md`](database.md) |
| What do these bytes mean? | [`protocol.md`](protocol.md) |
| Why does the site say "unknown" here? | [`candidates.md`](candidates.md), [`decoder.md`](decoder.md) |
| Why is this button greyed out? | [`commanding.md`](commanding.md) |
| Where did my old packets go? | [`retention.md`](retention.md) |
| How do I make backups? | [`backup.md`](backup.md) |
| How do I manage accounts and tokens? | [`admin.md`](admin.md) |
| How do I get my node publishing? | [`mqtt.md`](mqtt.md), [`firmware.md`](firmware.md) |
| How do I upgrade a node from the site? | [`firmware-upgrade.md`](firmware-upgrade.md) |
| Why is this repeater not forwarding anything? | [`packet-filter.md`](packet-filter.md) |
| Why is this button greyed out for this node? | [`node-management.md`](node-management.md), [`commanding.md`](commanding.md) |
| Is it safe to put this on the internet? | [`security.md`](security.md) |
| Can I hide a node's position but keep its figures? | [`privacy.md`](privacy.md) |
| My data comes from Home Assistant | [`homeassistant.md`](homeassistant.md) |
| I cannot flash firmware | [`proxy.md`](proxy.md) |
| How do I contribute a change? | [`contributing.md`](contributing.md) |
| How do I run the tests? | [`testing.md`](testing.md) |

---

## Conventions in these documents

- **Claims are citable.** Behaviour is attributed to a file and, where it matters,
  a function or line number. Firmware behaviour is cited against the MeshCore
  source tree so a later reader can re-check it against their own version.
- **Uncertainty is stated.** Where the documentation does not know something, or
  where the system deliberately refuses to know it, it says so instead of
  smoothing it over.
- **Both languages are complete.** The Dutch is a full translation, not a
  summary. A document with only one half is a bug — see
  [`contributing.md` §10](contributing.md#10-documentation-conventions).
