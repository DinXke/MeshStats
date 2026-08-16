# Asking a node to do something

*[Nederlands](nl/commanding.md)*

`server/app/commanding.py` answers one question: **what can be done with this
repeater, right now, and what may the page promise?**

[`mqtt.md`](mqtt.md#asking-a-node-for-something) describes the words on the wire
and what the firmware does with them. This document is about how the server
decides which route exists and what the button says.

## Why the module exists

The answer to "is this possible at all?" comes from five separate places:

1. the repeater row,
2. who publishes for it (`source_prefix`),
3. the firmware version of **that** node,
4. the broker connection,
5. when a poller last collected anything.

Without one place to combine them, the button on the admin page promises what
nobody can deliver — which is exactly what happened when Home Assistant left the
chain. The page kept reporting *"Opvraging gestart — Home Assistant logt in op de
repeater"* while the request lay in a queue nobody emptied any more.

The functions here **change nothing**. They only describe what is possible, so
the route is determined *before* the button is drawn and not after it is clicked.

## The three routes

### Directly over MQTT

The site publishes one word on `<prefix>/<node>/cmd` and the node reads its own
CLI or sends a status message immediately. Works only if the node publishes
itself, if its firmware knows that topic (**node firmware 1.8.0** and up,
`MIN_CMD_VERSION`) and if the broker is connected right now.

### Over MQTT to the node that monitors it

A repeater that does not publish itself, but whose figures are relayed by a node
that reads it out, is not unreachable — it is only not *directly* reachable. The
monitor already logs in to it and polls it; since **node firmware 1.9.0**
(`MIN_MON_CMD_VERSION`) it can also fetch that repeater's CLI settings over LoRa
on request and publish them. The command then goes to the monitor
(`settings <key>`) and not to the subject.

This is exactly the case this project exists for: the roof repeater that has to
measure all of this publishes nothing itself. Until 1.9.0 the button said of it
*"relayed, only the node itself can read its own CLI"* — true and useless at the
same time.

### Via a poller

The Home Assistant integration fetches `GET /api/v1/commands`, asks the repeater
over LoRa and POSTs the answer back. That route still exists, but it is now the
last choice instead of the only one.

## `route_for()` — what comes back

```python
commanding.describe(rep)   # route_for with broker state and relay filled in
```

`describe()` looks up the broker status, the poller timestamp and the relaying
repeater row itself; `route_for()` takes them as arguments so it stays testable
without an MQTT client or a database anywhere near.

| Key | Meaning |
|---|---|
| `mqtt` | Something can leave over MQTT right now |
| `commands` | **Which** commands that route can carry |
| `via_monitor` | The command goes to a different node than the subject |
| `blocker` | Why the MQTT route is closed; empty means open |
| `node` | The node that receives the command |
| `subject` | The key travelling inside the command, or `None` |
| `fw_meshmanager` | Firmware of the node receiving the command |
| `min_fw` | The version this route requires |
| `node_seen`, `node_stale` | When that node last published, and whether that is too long ago |
| `level` | The management level of this node: `unmanaged`, `semi_managed` or `full_managed` |
| `level_why` | What that level is seen by, naming the node that makes it possible |
| `ha`, `poller_seen` | Whether a poller has been seen within `POLLER_STALE_SECS` |

**`commands` is not a formality.** A monitor can be asked to fetch somebody
else's settings, but not to publish their statistics — it already forwards those
on rounds it schedules itself. A button offering `status` along a route that does
not know it is exactly the kind of promise this module had to clear away:

```python
"commands": ("settings",) if via_monitor else ("settings", "status")
```

### The blockers, in the order they are tested

| `blocker` | Meaning |
|---|---|
| `no_source` | Nothing has ever published for this repeater |
| `http_source` | It arrives through the HTTP API (`source_prefix == "api"`), not over MQTT |
| `relay_unknown` | It is relayed, but the relaying node is not itself a known repeater here — so we know nothing of its firmware, and guessing costs a command that is silently refused at the far end |
| `no_fw` | No module version known |
| `old_fw` | Version below `min_fw` |
| `broker_down` | Not connected to the broker |

### The management level

`_level()` answers a different question from `mqtt`/`ha`: not "can something be
sent right now" but "what is this node". Three answers, in the order they are
tested:

| Level | Evidence |
|---|---|
| `full_managed` | Publishes its own figures over MQTT *and* reports a firmware version, so its `cmd` topic exists |
| `semi_managed` | No firmware of ours, but rights on its CLI: a monitor running `MIN_MON_CMD_VERSION` or newer, or a poller that logs in with the repeater password |
| `unmanaged` | Neither — seen in the traffic and nothing more |

It is an **observation, never a setting**. There is no column for it and no
control to set one: storing it would guarantee it drifts away from reality, and
then a button says "can" about a node that has lost its firmware.

It deliberately ignores `broker_connected`. A full-managed node behind a dropped
broker is still full managed — there is just no route at this moment, which is
what `mqtt` is for. Letting the level swing with the server's network would make
it a statement about us rather than about the node.

The poller is not in the level names, and it counts anyway: the Home Assistant
integration logs in with the repeater password and reads and writes the same CLI.
Leaving it out would call a repeater that only arrives that way "unmanaged" while
the button next to it works. That evidence is more brittle than a monitor — it
lapses once the poller has been quiet for fifteen minutes — and `level_why` says
so.

Whether a **firmware upgrade** is possible does not follow from the level and is
a separate field: a full-managed node without an IP path takes commands but not a
megabyte image, and a node whose build environment is unknown may not receive one
at all.

`broker_down` is tested **last**, on purpose, so a temporarily absent broker does
not overshadow the permanent reason: "firmware too old" does not fix itself.

### Which firmware counts

For a relayed repeater, the **relaying** node's version. That node receives the
command and has to know it. The subject's version says nothing here — often there
is not even one, because a node that does not publish reports its module
version nowhere.

## `is_relayed()` and `same_key()`

```python
def is_relayed(rep) -> bool:
    source = (_field(rep, "source_prefix") or "").lower().strip()
    if not source or source == "api":
        return False
    return not same_key(source, _field(rep, "pubkey_prefix"))
```

`same_key()` repeats `db._find_by_prefix()`'s rule: sources send different
lengths — Home Assistant five bytes, the node's own firmware six — so the shorter
key must be a prefix of the longer one, and must be at least `MIN_PREFIX_MATCH`
(8) hex characters. Without that rule the page would mistake a node publishing
about itself for one being relayed.

The constant is repeated here rather than imported from `db` so this file can be
tested without a database.

`parse_version()` compares on numbers, not on the string: `"1.10.0"` sorts before
`"1.8.0"` alphabetically, and that is exactly the firmware that *can* do it.

## `_dispatch()` — every open route, not the first one

`routes_admin._dispatch(rep, command)` is what the two buttons call.

**Both routes are walked, and not the first available one**, because they are not
interchangeable. The MQTT route reaches the node itself and only while it is
hanging on the broker; the queue reaches a poller that asks the repeater over
LoRa and also works when the node's WiFi is off. Whoever has both benefits from
both; whoever has neither should **see** that rather than read "started".

```
mqtt    only the direct publication left
queued  only the poller queue was filled
both    both
none    nothing happened
```

The page's wording hangs off that return value, not off what we hoped would
happen. It travels in the redirect's query string, and the old form `?refresh=1`
is still read as `both` so a page still open in a tab does not break on it.

For `settings` the queue is filled **only when a poller has actually been seen**.
Queueing anyway would leave a request that a freshly installed Home Assistant
picks up months later, and would make `pending_settings_request()` stop meaning
what it says.

## `publish_command()` — the only thing the site publishes

`mqtt_ingest.publish_command(node, command, subject=None, epoch=None)` returns
whether the message **left**, never whether it arrived.

The topic is `MM_MQTT_CMD_TOPIC`, `{prefix}/{node}/cmd` by default.
`command_prefix()` fills `{prefix}` with the prefix this particular node was
last heard reporting on — remembered on arrival in `repeaters.topic_prefix`
rather than chosen at departure, because during the rename a node that has not
been reflashed still listens on the old one and no setting can say which. A node
never heard from gets the command on **every** prefix (`command_topics()`): two
eight-byte messages are cheaper than a button that does nothing. A pattern
configured without `{prefix}` in it is respected exactly as written — whoever
pins a fixed topic means it.

```python
COMMANDS = ("settings", "status", "time")
COMMANDS_WITH_SUBJECT = ("settings",)
COMMANDS_WITH_EPOCH = ("time",)
```

That the firmware accepts only those three words is not a detail: this topic is
reachable by anyone holding broker credentials, and the repeaters this serves
hang on roofs. The list is repeated on this side so a typo is refused before it
costs a round trip, and so it is readable next to the code that sends it.

The arguments do not widen that surface:

- **`subject`** is never text reaching a CLI. It selects one entry from a monitor
  list only the node's operator can write, it is stripped to hex, and it must be
  at least `MIN_SUBJECT_HEX` (8) characters. Shorter and nothing is published at
  all — returning False so the page says "nothing sent", rather than sending a
  command that is refused at the far end without anything showing here.
- **`epoch`** is a number, bounded at both ends here and again on the node, and
  it can only ever move a clock forward. See
  [`clocksync.md`](clocksync.md#the-message-on-the-wire).

They are kept in **separate lists** rather than one parameter because they are
different kinds of argument with different checks — a key is hex and *selects*
something, an epoch is a number and *changes* something. Running them through one
parameter would mean a single slip in the call could send a key as a time.

A wrong command word, a subject on a command that takes none, or an epoch on a
command that takes none, all **raise**: those are programming errors and should
break while being written. An out-of-window epoch and a too-short subject
**return False**: those are states of the world, and the caller has to be able to
report them.

### QoS 0 and `retain=False`

Deliberate, both of them.

**QoS 0** because there is nothing to gain from the alternative. The client
connects with a clean session, so the broker queues nothing for a node that is
offline; a higher QoS would only confirm delivery *to the broker*, which is not
the question anybody is asking. A node asleep on its solar budget simply misses
the message, and the page has to say so rather than pretend the command is on its
way.

**`retain=False`** because a retained command is redelivered on every reconnect.
The node would sweep its CLI on every boot and after every WiFi drop, for as long
as the message sat on the broker, and nobody would connect that to a button
pressed once, weeks earlier.

### One connection, both directions

`publish_command()` uses the client the subscriber thread already holds. A second
client for publishing would need its own credentials, its own reconnect loop and
its own client id — and paho's `publish()` is thread-safe, so the request
handlers can use this one from their own threads. `can_publish()` is the check
that a command sent right now would actually leave this machine: a host
configured, a client built, and the connection up.

## The poller queue, and telling three silences apart

The queue lives in `settings` and is **clear-on-read**, which is why three
separate pieces of bookkeeping exist around it. Without them, the admin page
would have to promise the same thing in three different situations:

| Question | Answered by |
|---|---|
| Is the request still waiting, i.e. has nothing polled since the click? | `db.pending_settings_request(prefix)` |
| Did a poller take it, and has anything come back since? | `db.settings_delivered_at(prefix)` versus the newest `repeater_cli.updated` |
| Is there anyone out there to collect it at all? | `db.poller_last_seen()`, written on **every** poll including the empty ones |

`settings_delivered` is bounded at 200 keys, keeping the newest, because unlike
the request queue it is not cleared on read.

The repeater settings page turns those three into `queued_since`,
`delivered_since` and `delivery_unanswered`, so a look-up that was fetched and
never answered is visible instead of looking exactly like one that was never
collected.

## Version boundaries in one table

| Route | Command | Minimum | Constant |
|---|---|---|---|
| Direct | `settings`, `status` | node firmware 1.8.0 | `commanding.MIN_CMD_VERSION` |
| Via a monitor | `settings <key>` | node firmware 1.9.0 | `commanding.MIN_MON_CMD_VERSION` |
| Either | `time <epoch>` | node firmware 1.10.0 | `clocksync.MIN_TIME_VERSION` |

"Older" does not mean "maybe". A node below 1.8.0 does not subscribe to the topic
at all, so the broker throws the message away without anybody noticing; a 1.8.0
node does know the topic but refuses the argument and counts the command as
refused. Which is why the version is stored on the repeater row
(`fw_meshmanager`) and why a button that cannot work is disabled rather than
hopeful.

## Timeouts

| Constant | Value | Meaning |
|---|---|---|
| `POLLER_STALE_SECS` | 900 s | The HA integration polls every 30 s, so a quarter of an hour of silence is an absence, not a delay |
| `NODE_STALE_SECS` | 3600 s | A **warning** on the page, not a refusal: the publication interval follows the battery and can stretch in power-saving mode |
| `clocksync.NODE_STALE_SECS` | 6 h | A refusal, and therefore wider |

## Tests

`server/tests/test_commanding.py` covers the route selection and the blockers;
`test_mqtt_command.py` covers `publish_command()` including the subject and epoch
validation; `test_settings_chain.py` covers the whole chain from button to stored
setting.

## Related documents

| Question | Document |
|---|---|
| The words on the wire and what the firmware does with them | [`mqtt.md`](mqtt.md#asking-a-node-for-something) |
| The `time` command in full | [`clocksync.md`](clocksync.md) |
| Where the queue and its bookkeeping live | [`database.md`](database.md#settings) |
| The admin pages that draw these buttons | [`admin.md`](admin.md) |
