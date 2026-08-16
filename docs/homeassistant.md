# The Home Assistant integration

*[Nederlands](nl/homeassistant.md)*

`homeassistant/custom_components/mc_repeater_stats/` — a custom integration that
reads MeshCore repeater data out of the Home Assistant state machine and pushes
it to a MeshStats site over HTTP.

**It is optional, and it is no longer the recommended path.** A node running the
MeshStats firmware publishes to MQTT by itself, with no Home Assistant in the
middle. This document exists because the integration still does two things the
MQTT path does not, and because plenty of installations were built on it before
MQTT existed.

---

## Contents

- [Is this for you?](#is-this-for-you)
- [What it needs](#what-it-needs)
- [Installation](#installation)
- [Configuration flow](#configuration-flow)
- [What it discovers](#what-it-discovers)
- [What it pushes](#what-it-pushes)
- [What it fetches back](#what-it-fetches-back)
- [Timing](#timing)
- [Prefix matching](#prefix-matching)
- [Services](#services)
- [Limits and known behaviour](#limits-and-known-behaviour)
- [Troubleshooting](#troubleshooting)
- [File map](#file-map)

---

## Is this for you?

| Situation | Use |
|---|---|
| You can flash the MeshStats firmware onto a companion node | **MQTT.** [`mqtt.md`](mqtt.md) — no Home Assistant needed |
| You cannot flash firmware, but you already run Home Assistant with the `meshcore` integration | **This integration** |
| You want repeater CLI settings fetched over LoRa without a monitor node | **This integration** — it is the only path that logs in to a repeater from HA |
| You want both | Fine. Both write through the same ingest handler; the newest value wins |

The honest summary: if the node can publish for itself, let it. This integration
adds a machine that has to stay up, a token that has to stay valid, and a set of
regular expressions that have to keep matching entity IDs the `meshcore`
integration chooses.

What it still buys you:

1. **Contact locations.** It reads advertised positions out of `meshcore`'s
   contact sensors and posts them to `/api/v1/contacts`, which is what puts nodes
   on the map. A node publishing over MQTT reports what it measures about itself,
   not the advert database its app holds.
2. **CLI settings fetching over LoRa.** It can log in to a repeater with its
   admin password and read back settings, one `get` at a time. This is the same
   job a [monitor](glossary.md#monitor) node does, performed from HA instead.

---

## What it needs

- Home Assistant with the **`meshcore` integration** already working and
  producing `sensor.meshcore_*` / `binary_sensor.meshcore_*` entities. This
  integration reads those entities and calls `meshcore.execute_command`; without
  it, there is nothing to read and nothing to command.
- A MeshStats site reachable over HTTP(S) from the HA machine.
- An **API token** minted in `/admin`.
- Optionally, the **admin password of each repeater** you want settings fetched
  from.

No Python dependencies: `manifest.json` has an empty `requirements` list and
empty `dependencies`.

---

## Installation

```
config/custom_components/mc_repeater_stats/     <- copy the directory here
```

Copy `homeassistant/custom_components/mc_repeater_stats/` from this repository
into your Home Assistant `config/custom_components/`, restart Home Assistant,
then **Settings → Devices & services → Add integration → MC Repeater Stats**.

There is no HACS repository and no release artefact; the directory is the
deliverable.

---

## Configuration flow

`config_flow.py`. Two steps on first setup, three when you change options later.

### Step 1 — `user`: site and token

| Field | Meaning |
|---|---|
| `base_url` | The site's base URL, trailing slash stripped |
| `token` | An API token from `/admin` |

The URL is validated before the flow continues: `validate_connection()` performs
a `GET /api/v1/ping` with the bearer token and requires HTTP 200. A failure shows
`cannot_connect`, a non-200 shows `invalid_auth`. The base URL is also the
config entry's unique ID, so the same site cannot be added twice.

### Step 2 — `repeaters`: which nodes to sync

A multi-select built from `discover_repeaters()`, plus one checkbox:

| Field | Default | Meaning |
|---|---|---|
| `repeaters` | all discovered | Which prefixes to push |
| `auto_add` | on | Adopt newly discovered repeaters automatically |

With `auto_add` on, `_interval_push()` compares `discover_repeater_prefixes()`
against the current selection every five minutes and, on finding anything new,
writes an updated option list. That update triggers a config-entry reload, and
the freshly built pusher immediately pushes everything.

### Step 3 — `passwords`: repeater admin passwords (options flow only)

One optional text field per selected repeater. These are the passwords used for
`send_login` when fetching CLI settings. **A field left empty keeps the stored
password** rather than clearing it, so re-opening the options flow does not wipe
what you entered last time.

Without a password, `_fetch_settings_inner()` logs a warning and proceeds anyway
— a few `get` commands answer without a login, most do not.

---

## What it discovers

Everything hinges on the entity-ID shapes the `meshcore` integration produces.
They are matched by the regular expressions in `const.py`:

| Constant | Pattern | Matches |
|---|---|---|
| `RE_ENTITY` | `^(?:sensor\|binary_sensor)\.meshcore_([0-9a-f]{6,12})_(.+)$` | Any MeshCore entity: prefix + remainder |
| `RE_NAME` | `MeshCore Repeater: (.+?) \([0-9a-f]+\)` | A repeater's display name, out of `friendly_name` |
| `RE_NEIGHBOR` | `^neighbor_([0-9a-f]{6})$` | A neighbour's SNR sensor |
| `RE_NEIGHBOR_SEEN` | `^neighbor_([0-9a-f]{6})_seen$` | Minutes since a neighbour was last heard |
| `RE_NEIGHBOR_NAME` | `Neighbor (.+?) SNR$` | The neighbour's name, out of `friendly_name` |
| `RE_CONTACT` | `^binary_sensor\.meshcore_.+_([0-9a-f]{12})_contact$` | A contact, for its advertised position |

Two discovery functions with a deliberate difference:

| Function | Returns | Used for |
|---|---|---|
| `discover_repeaters()` | Every MeshCore prefix → display name | The picker: shows everything, lets you choose |
| `discover_repeater_prefixes()` | Only prefixes whose `friendly_name` matches `MeshCore Repeater:` | `auto_add`: adopts **only** genuine repeaters |

The split matters. Auto-adding anything that merely looks like a MeshCore entity
would push companions and clients into a repeater statistics site.

### Metric names

`extract_metric()` maps the remainder of an entity ID onto a known metric name.
`KNOWN_METRICS` is sorted **longest first**, deliberately:

```python
KNOWN_METRICS = sorted([...], key=len, reverse=True)
```

Entity IDs end with a slugged node name (`bat_be_hss_jessazh_vir`). Matching
shortest-first would read `battery_percentage` as `bat` with a suffix. Longest
first makes the greedy match the correct one.

Booleans get their own rule: a `binary_sensor` value is true only for `on` or
`fresh`; anything else counts as offline. States of `unknown`, `unavailable` or
empty are skipped entirely rather than pushed as zero.

---

## What it pushes

### Snapshots — `POST /api/v1/ingest`

`_snapshot()` walks all MeshCore entities for one prefix and builds:

```json
{
  "repeater": {"pubkey_prefix": "<prefix>", "name": "<from friendly_name>"},
  "metrics":  {"bat": 4.15, "online": true, "...": "..."},
  "neighbors": [{"prefix": "2ae7af", "name": "...", "snr": -4.25, "seen_min": 3.0}]
}
```

Returns `None` — and pushes nothing — when a repeater has no usable metrics at
all. A snapshot of nothing is worse than no snapshot: it writes a row that looks
like a measurement.

Sending is triggered three ways:

| Trigger | Mechanism |
|---|---|
| A state change on a synced repeater | `EVENT_STATE_CHANGED` listener, debounced |
| Every 5 minutes | `async_track_time_interval` → `_interval_push()` → `push_all()` |
| The site asked for a refresh | `_poll_commands()` → `_request_status()` → forced push |

The debounce is per prefix and coalescing rather than resetting: once a push is
scheduled for a prefix, further state changes are ignored until it fires
(`if prefix in self._debounce: return`). A repeater whose sensors all update
within a second produces one push, not a dozen.

A forced push carries `"force": true`, which tells the server to write a data
point even if nothing changed — the whole point of having asked.

### Contacts — `POST /api/v1/contacts`

`collect_contacts()` reads `adv_lat`/`adv_lon` (falling back to
`latitude`/`longitude`) off every contact binary sensor and posts prefix, name,
position and node type. Contacts without a usable position are skipped, not
posted with nulls.

Pushed at startup and on every five-minute interval.

---

## What it fetches back

Every 30 seconds `_poll_commands()` performs `GET /api/v1/commands` and finds
two kinds of request.

### `refresh` — ask a repeater for fresh status

`_request_status()` calls `meshcore.execute_command` twice, with
`send_statusreq <short>` and `send_telemetry_req <short>`, then schedules a
forced push 35 seconds later — enough time for the LoRa round trip to land in
HA's state machine before the snapshot is taken.

### `settings` — read a repeater's CLI settings

`_fetch_settings()` is serialised behind `asyncio.Lock()`: **one settings fetch
at a time across all repeaters**, because they all share one radio.

The sequence in `_fetch_settings_inner()`:

1. `send_login <short> <password>`, then wait `SETTINGS_LOGIN_WAIT` seconds.
2. For each parameter, `send_cmd <short> "get <param>"`. A parameter written as
   `cmd:<something>` is sent literally instead, without the `get ` prefix.
3. Wait up to `SETTINGS_RESPONSE_TIMEOUT` for the first reply, then keep
   collecting until it has been quiet for `SETTINGS_QUIET_GAP` — multi-line
   answers such as the region list arrive as several separate LoRa packets —
   with `SETTINGS_PARAM_CAP` as the hard ceiling per parameter.
4. Two seconds between parameters, to give LoRa room to breathe.
5. **One retry round** for every parameter that came back `None`.
6. `POST /api/v1/repeater_settings` with the whole result map, unanswered
   parameters included as `null`.

Replies arrive on two event buses at once and both are listened to:
`meshcore_cli_response`, and `meshcore_message` for the case where the repeater
answers as a direct message prefixed with `> `. `_response_text()` tries the
field names `response`, `text`, `message`, `result`, `payload` in order and falls
back to a JSON dump, capped at 500 characters — a tolerant reader, because the
event shape is not ours to fix.

Parameters are capped defensively before use: 64 characters each, 40 at most.

### Loud failure is deliberate

If a request cannot be executed — no matching repeater, empty parameter list —
the integration logs a **warning naming the reason and the synced prefixes**:

```python
# Luid falen: de wachtrij op de site is clear-on-read, dus een
# verzoek dat we hier laten vallen bestaat nergens meer.
```

`GET /api/v1/commands` is clear-on-read. A request dropped silently here exists
nowhere any more, and the admin page would go on claiming "fetch started" with
nothing behind it and nothing to explain why. See
[`contributing.md`](contributing.md#1-honesty-about-uncertainty).

---

## Timing

All in `const.py`, all in seconds.

| Constant | Value | What it governs |
|---|---|---|
| `DEBOUNCE_SECONDS` | 10 | Delay after a state change before pushing that repeater |
| `FULL_PUSH_INTERVAL` | 300 | Full snapshot of every repeater, plus contacts |
| `COMMAND_POLL_INTERVAL` | 30 | How often the command queue is polled |
| `REFRESH_PUSH_DELAY` | 35 | Wait after a status request before the forced push |
| `SETTINGS_LOGIN_WAIT` | 12 | Wait after `send_login` before the first `get` |
| `SETTINGS_RESPONSE_TIMEOUT` | 12 | Wait for the first reply to a `get` |
| `SETTINGS_QUIET_GAP` | 5 | Silence that ends a multi-line answer |
| `SETTINGS_PARAM_CAP` | 45 | Hard ceiling per parameter |
| `MIN_PREFIX_MATCH` | 8 | Shortest prefix comparison still trusted (hex characters) |

HTTP timeouts: 30 s for pushes, 15 s for the command poll and for `ping`.

---

## Prefix matching

`match_prefix()` in `pusher.py` — small, and the source of a whole class of
silent failures if it is absent.

The site and Home Assistant do not spell the same key at the same length: the
`meshcore` integration delivers five key bytes here, a node's own firmware six,
and the site stores the longest it ever saw. An equality test therefore says
"different node" about two spellings of one node. Because the site's command
queue is clear-on-read, such a request then vanishes without trace.

The rule:

- Exact match wins.
- Otherwise, either string may be a prefix of the other.
- **Never below `MIN_PREFIX_MATCH` = 8 hex characters.** Two genuinely different
  keys can share a short opening, and sending a fetch to the wrong node is worse
  than sending none.
- Among several candidates, the **longest** wins — it is the least ambiguous.

The same function is used against the password map, for the same reason: a
password stored under a differently-spelled prefix would otherwise cause a login
without a password, and a repeater that answers nothing.

`MIN_PREFIX_MATCH` mirrors the server-side constant of the same name. If you
change one, change the other.

---

## Services

| Service | Effect |
|---|---|
| `mc_repeater_stats.push_now` | Immediately push a full snapshot of every synced repeater, for every configured site |

Registered once, globally, not per config entry: it iterates every pusher in
`hass.data[DOMAIN]`.

---

## Limits and known behaviour

- **The integration reads state, it does not poll radios.** Its data is only as
  fresh as what the `meshcore` integration has put in the state machine. A
  refresh request is the one exception, and it works by asking `meshcore` to
  transmit.
- **Regular expressions are a coupling.** If the `meshcore` integration changes
  its entity-ID or `friendly_name` shapes, discovery goes quiet rather than
  erroring. Symptom: repeaters stop appearing in the picker.
- **Every network error is swallowed and logged, never raised.** A failed push
  must not take down the HA event loop, so `push_repeater()`, `push_contacts()`
  and `_poll_commands()` all catch broadly. The next interval tries again.
- **A settings fetch occupies the radio for minutes.** Serialised by design, with
  deliberate gaps. Fetching many parameters from several repeaters is slow, and
  that is the LoRa duty cycle talking, not the code.
- **Passwords are stored in the config entry**, which lives in Home Assistant's
  `.storage`. Treat that directory accordingly; see
  [`security.md`](security.md).

---

## Troubleshooting

| Symptom | Likely cause |
|---|---|
| No repeaters offered in the picker | The `meshcore` integration is missing, or its entity names no longer match `RE_ENTITY` / `RE_NAME` |
| Setup fails with `cannot_connect` | The HA machine cannot reach the site URL |
| Setup fails with `invalid_auth` | Token wrong, revoked, or `/api/v1/ping` did not return 200 |
| "Fetch started" on the site, nothing ever arrives | Check the HA log for the warning naming the reason — usually the prefix did not match, or no password is stored |
| Settings come back mostly `null` | No repeater password configured, or the LoRa link is losing replies. One retry round already ran |
| Site shows a repeater but no map position | Positions come from contacts, not snapshots. Check that the contact sensors carry `adv_lat` / `adv_lon` |
| Values stop updating but nothing errors | Sensors are in `unknown` / `unavailable`; those states are skipped by design |

The integration logs under `custom_components.mc_repeater_stats`. Turn it up in
`configuration.yaml`:

```yaml
logger:
  logs:
    custom_components.mc_repeater_stats: debug
```

---

## File map

| File | Contents |
|---|---|
| `__init__.py` | Entry setup/teardown, the `push_now` service, reload-on-options-change |
| `config_flow.py` | The setup and options flows |
| `const.py` | Domain, option keys, timing constants, entity regexes, `KNOWN_METRICS` |
| `pusher.py` | Discovery, snapshot building, pushing, command polling, settings fetching |
| `manifest.json` | Integration metadata; `iot_class: cloud_push`, no requirements |
| `services.yaml` | The `push_now` service description |
| `strings.json`, `translations/{en,nl}.json` | UI text |

---

## See also

| | |
|---|---|
| The recommended path instead of this one | [`mqtt.md`](mqtt.md) |
| Where this fits in the whole | [`architecture.md`](architecture.md) |
| Installing it alongside the site | [`deployment.md`](deployment.md#home-assistant-components) |
| The TCP proxy, a different HA component | [`proxy.md`](proxy.md) |
| Token handling and what a password buys an attacker | [`security.md`](security.md) |
