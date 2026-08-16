# Managing nodes from the site

*[Nederlands](nl/node-management.md)*

A walkthrough of everything the site can do to a node, in the order you will
need it: recognising what a node is, bringing it under management, reading and
changing its settings, setting its clock, giving it new firmware — and what to do
on the day it does not come back. Plus, throughout, the part that takes the most
words: what the site deliberately will not do.

Reading a node is settled and has worked for a long time. **Writing** is now
built along both routes — over IP to a node the server reaches itself, and over
LoRa through a monitor to one it cannot — and a few things around it are still
designed and explicitly not built; each section says which. The distinction
matters more here than in most documentation, because the failure mode is not a
broken page. It is a repeater on a roof that nobody can reach any more.

The screenshots come from a throwaway instance filled with invented nodes, never
from a running installation — see
[`contributing.md` §10](contributing.md#10-documentation-conventions) for how to
remake them. The admin pages are Dutch only, deliberately, so the screenshots are
Dutch even here; [`admin.md`](admin.md) explains that choice.

---

## Contents

**Knowing what you have** — [the nodes page](#start-at-the-nodes-page) ·
[the three levels](#the-three-levels) ·
[what is possible per level](#what-is-possible-per-level) ·
[bringing a node under management](#bringing-a-node-under-management)

**Settings** — [reading](#reading-settings) ·
[three transports](#three-transports-for-one-write-path) ·
[which may be written](#which-settings-may-be-written) ·
[writing over LoRa](#writing-over-lora-through-the-monitor) ·
[confirm-or-revert](#confirm-or-revert-examined-and-deliberately-not-built) ·
[reading it back](#after-a-write-read-it-back)

**The device** — [the packet filter](#the-packet-filter) ·
[the clock](#setting-the-clock) ·
[firmware and rollback](#upgrading-firmware-and-going-back) ·
[when a node does not come back](#when-a-node-does-not-come-back)

**When it does not work** — [rights](#rights-are-the-hinge-and-their-failure-mode-is-confusing) ·
[telemetry without credentials](#telemetry-without-credentials) ·
[without internet](#working-without-internet) ·
[discovery](#discovery-point-then-test-once) ·
[built and not built](#what-is-built-and-what-is-not)

---

## Start at the nodes page

`/admin` is the only page that answers "what have I got, and what can I do with
it" in one screen. It does not sort by name or by last seen. It groups by
**management level**, because what you can do with a node differs per group and
nothing else on the page explains the buttons underneath.

![The admin page 'Nodes en repeaters' with five invented nodes in three groups. Full managed — 2 holds Voorbeeld-Thuisnode and Voorbeeld-Zendmast, both with source 'zichzelf', firmware v1.16.0 + 1.10.0 and route 'MQTT'. Semi-managed — 1 holds Voorbeeld-Dakrepeater, source 'via bb11bb11bb11', route 'via monitor'. Unmanaged — 2 holds Voorbeeld-Buurnode and Voorbeeld-Veldpost, both with route 'geen'. Each group carries a paragraph explaining what that level means, and each node a sentence saying how its level was observed.](images/beheer-nodes-overzicht.png)

Three things on that page are worth naming before anything else.

**The sentence under each node is the evidence.** "publiceert zelf over MQTT met
nodefirmware 1.10.0", "bereikbaar via Voorbeeld-Thuisnode over LoRa", "alleen
waargenomen in het verkeer". That is `level_why`, and it names the node that
makes the level possible. Without that name, "semi-managed" is a label nobody
can act on.

**"Weg nu" is not the level.** It says what can leave this machine at this
instant. A full managed node behind a broker that just dropped is still full
managed; there is only no route right now. The two are deliberately separate
keys, and [`commanding.md`](commanding.md) explains why the level ignores the
broker connection entirely.

**A hidden node still counts.** A repeater that appears by itself out of an
incoming message arrives hidden — publishing rights on a topic are not
publishing rights on the front page — and the banner at the top says how many
are waiting for that decision.

---

## The three levels

A node's **management level** is an observation, not a setting. There is no
button to raise it, because the level is a statement about what is currently
true, and a button would let it say something that is not.

`commanding.describe()` reports it as `level` plus a Dutch `level_why`.

| Level | What is true | Reference node |
|---|---|---|
| `unmanaged` | Only ever seen in the traffic: adverts, packets, SNR, sometimes a position. No credentials anywhere, no actions. | most of the mesh |
| `semi_managed` | Not our firmware. Reachable over LoRa through a monitoring repeater that holds CLI rights on it. | **JessaZH** (`e3d3f4…`), and it will stay that way for months |
| `full_managed` | Our firmware, publishing to MQTT itself. Own statistics, its own `cmd` topic, clock, and — when there is also an IP path — firmware. | **DinX-Home** (`55d9a3…`) |

Derivation order, which is also the order in which the evidence is strongest:

1. The node publishes statistics itself **and** reports a `fw_meshmanager`
   version → `full_managed`. Its `cmd` topic is reachable, so it can be steered
   directly.
2. It does not publish itself, but a monitor with CLI rights reaches it →
   `semi_managed`.
3. Neither → `unmanaged`.

**Stock MeshCore is the normal case, not the exception.** Nodes running our
firmware are the variant that can do extra. It is worth reading the table that
way round, because designing for "our nodes, plus some others" is how the roof
repeater ends up as an edge case — and it is the node this whole project was
built around.

### Seeing the level of one node

Open a node and the level is the first thing on the page, above the key prefix,
because it answers the question you have before you scroll: what can I do here?

![The admin page of Voorbeeld-Dakrepeater. Next to the title a badge reads SEMI-MANAGED, and under the heading 'Identiteit en versies' an amber-bordered card repeats the badge with 'waargenomen: bereikbaar via Voorbeeld-Thuisnode over LoRa' and a paragraph explaining what semi-managed allows. Below it a table lists key prefix aa00aa00aa00, slug, 'Bron van de cijfers: doorgestuurd door node bb11bb11bb11', last seen, MeshCore firmware v1.16.0, and an empty nodefirmware field noting that without that version the site sends this node nothing.](images/beheer-node-semi-managed.png)

The empty **Nodefirmware (MeshManager)** row on that screenshot is not cosmetic.
That field decides whether the buttons further down may send anything at all —
commands from 1.8.0, sweeping a monitored repeater from 1.9.0, the clock from
1.10.0. Whoever wonders why a button is off looks here first.

### Transitions

A `semi_managed` node becomes `full_managed` by getting our firmware, which needs
an IP path (see [`firmware-upgrade.md`](firmware-upgrade.md)). A `full_managed`
node falls back to `semi_managed` when its network connection goes away and
somebody still monitors it. Neither transition is something the site performs;
both are things it notices.

---

## What is possible per level

| | `unmanaged` | `semi_managed` | `full_managed` |
|---|---|---|---|
| Read from traffic (adverts, SNR, path, position) | yes | yes | yes |
| Own statistics (uptime, airtime, counters) | no | no | yes |
| Read CLI settings | no | yes, over LoRa | yes |
| **Write CLI settings** | no | **built** — over LoRa through the monitor, same surface | **built** — full surface, risk-classed |
| Set the clock | no | yes, through the monitor | yes |
| Firmware upgrade | no | **no** | only with an IP path |
| Telemetry polling without credentials | usually **yes** — a property beside the level, not part of it | usually yes | usually yes |

**Firmware upgrade is deliberately not part of the level.** A `full_managed`
node can accept a twenty-byte command over MQTT and still be unable to accept a
1.3 MB image, because those do not travel over the same thing. The site keeps it
in a separate key, `firmware.ota_route(rep)`, which returns `can` plus a
`blocker` in the style of `commanding.route_for`. Reasoning and the blocker
values are in [`firmware-upgrade.md`](firmware-upgrade.md).

**A capability that does not apply is shown disabled with its reason, never
hidden.** A button that vanishes leaves "why can I not do this here" unanswered,
and that question is exactly what somebody has at the moment they need the
button. On an `unmanaged` node every action is therefore still on the page, off,
each with its own sentence:

![The 'Uitvragen' and 'Klok' sections of the unmanaged node Voorbeeld-Buurnode. The settings table is empty. Three buttons are greyed out — 'Opvragen kan nu niet', 'Status opvragen kan nu niet' and 'Synchroniseren kan nu niet' — and each carries its reason: 'Geen van beide wegen staat open', 'de node meldt geen firmwareversie, dus valt niet vast te stellen of hij opdrachten aanneemt' and the same for the clock. A line notes that nobody has ever fetched /api/v1/commands.](images/beheer-node-unmanaged.png)

Note that the reasons differ from each other. "No route is open" and "the node
reports no firmware version" are different problems with different fixes, and a
single greyed-out button saying nothing would have hidden both.

---

## Bringing a node under management

You do not raise a level. You change the world, and the level follows at the next
message. There are exactly two things to change.

### To `semi_managed`: rights on its CLI

A MeshCore repeater runs a CLI command only for a client it considers an
**admin**. Whoever operates that repeater grants it, on their side:

```
setperm <our-public-key> 3
```

`1` is read-only and is not enough — see
[Rights are the hinge](#rights-are-the-hinge-and-their-failure-mode-is-confusing)
for why that particular half-measure is the most confusing state in this whole
area. The alternative is handing over the repeater's admin password, which works
and is worse: a password is shared, a permission is revocable by the other
operator alone.

On our side the monitoring node needs to know about it. The monitor list lives on
the node, not on the site (`wifi mon add <key>`), and the monitor must run
nodefirmware **1.9.0** or later, because that is where `settings <key>` — fetch
*their* CLI over LoRa — was added. An older monitor knows the `cmd` topic but
refuses the argument and counts the command as rejected, which is why the site
refuses to guess and shows `old_fw` instead.

### To `full_managed`: our firmware plus MQTT

Two conditions, both necessary:

1. The node runs the MeshManager firmware and publishes its own statistics —
   see [`firmware.md`](firmware.md) for building and flashing, and
   [`mqtt.md`](mqtt.md) for the topics and the per-node broker account.
2. It reports a `fw_meshmanager` version in those messages. Without it the site
   cannot establish that the `cmd` topic exists on that node, and a command
   published into the void is exactly the dishonesty the level exists to prevent.

Getting our firmware onto a node that is only reachable over LoRa is not possible
from here and never will be — 1.3 MB against the duty cycle is days of airtime.
Such a node is flashed over USB, in person. That is the whole reason
`semi_managed` is a level and not a waiting room.

### Checking that it worked

Reload `/admin`. The node has moved to another group, and the sentence under it
names the new evidence. That is the confirmation — not a success message, but the
site's own reading of what is now true.

If it has not moved, the sentence tells you what is still missing, and it is
almost always one of three things: no firmware version reported, a monitor below
1.9.0, or nothing published since the change.

---

## Reading settings

Reading is settled, works today, and is the same mechanism at both managed
levels. It differs only in who is asked.

![The 'Uitvragen' section of Voorbeeld-Dakrepeater. A table lists fifteen CLI parameters with their values and how long ago each was fetched — advert.interval 240, af 1.0, allow.read.only off, cmd:region showing '(geen antwoord)' in grey, flood.max 3, freq 869.525, radio 869.525,250,11,5, repeat on, role repeater, tx 22 and more. Below it a blue-bordered block 'Instellingen nu opvragen' tagged 'kost zendtijd' explains that node bb11bb11bb11 monitors this repeater and can query it over LoRa, with an active button. A second block for a fresh status is greyed out because a relayed repeater cannot be asked to publish.](images/beheer-node-instellingen.png)

Three things this screenshot is showing.

**`cmd:region` reads "(geen antwoord)", not a stale value.** A sweep that gets no
answer for one parameter says so. Showing the last known value would make an
unanswered question look like a fresh fact, and on a radio link the difference is
routine rather than exceptional.

**The button is tagged "kost zendtijd".** A sweep is fifteen or so commands and
fifteen answers over a shared band, one at a time with breathing room between
them. It is a read — nothing on the device changes — but it is not free, and the
page prices it accordingly. Expect **2 to 5 minutes** through a monitor, under
half a minute direct.

**"Status opvragen" is off, and for a reason that is not a fault.** A relayed
repeater does not publish; its figures arrive on the monitor's own schedule.
Asking it for a fresh status is not a thing that exists, so the button says so
rather than pretending.

Which parameters get swept is one list for all repeaters, on
`/admin/server#cli-params` — not per node, because a per-node list invites the
idea that you can ask one node something special.

---

## Three transports for one write path

The short answer, because it was asked directly: **yes, over MQTT too — since
nodefirmware 2.8.0, and deliberately not by widening the `cmd` topic into a
CLI.**

There is one write path. Everything that can refuse a write — the node's own
parameter list, its bounds, the risk classes, the confirmation, the RBAC
permission, the read-back — happens in `nodeconfig.write()` no matter which
transport is used. Only *how the command travels* differs. Per node, the site
picks the first of these that is actually available, and the node page says which
one it picked and why:

| # | Transport | Available when | Counterparty | Risk classes |
|---|---|---|---|---|
| 1 | HTTP to the node (`POST /api/cfg`) | it has an IP path and `MM_FW_NODE_USER`/`MM_FW_NODE_PASS` are set | that node's web login | 1, 2 and 3 |
| 2 | MQTT `cmd` topic (`set <param> <value>`) | the node publishes to MQTT itself, runs nodefirmware 2.8.0, and the broker is connected | whoever the broker let in | 1 and 2 |
| 3 | Mesh CLI via its monitor (`POST /api/moncfg`) | the node is relayed and its monitor has an IP path, a web login and nodefirmware 2.4.0 | the monitor's login, then its own rights on the far node | 1, 2 and 3 |

The ordering is a ranking: strongest counterparty and fastest, most complete
read-back first; most expensive last. A relayed node only ever has row 3 — it
does not publish, so it has no `cmd` topic of its own. A node that publishes for
itself has rows 1 and 2.

If none of the three is available, that is still the answer — with the reason per
transport, so "not possible" is something you can act on rather than a dead end.

### Why the `cmd` topic was reopened

The earlier design said writes never go over MQTT, and named the case that would
force the decision back open: *a `full_managed` node with no IP path cannot be
written to at all, even though its `cmd` topic works.* It then said the answer
would not be to widen the allow-list quietly, but to reopen the decision on
purpose.

That case turned up in a slightly different shape. A node that publishes to MQTT
and runs our firmware — full managed by every definition — reported "cannot be
changed" as soon as `MM_FW_NODE_USER` was left empty, while an open, working
connection to it lay unused. A full managed node has an MQTT connection by
definition, so "cannot" was factually wrong there.

So the decision was reopened, and this is what held.

**It is a larger allow-list, not a pipe into the CLI.** The topic still matches
exact words; there is now a fourth one. The node itself validates:

- the parameter must be one of the twenty-eight names compiled into `CFG_PARAMS`,
  and the command is built from *the table's* key — no text out of the message
  ever becomes a command. Only the value travels, and it is always the last word,
  so there is no separator a second command could start after;
- the value must pass `cfgCheckValue()`, the same sieve both HTTP write paths
  use. One sieve, so the three cannot drift apart;
- the risk class must not exceed `CFG_MQTT_MAX_RISK`.

A node that gets an unknown parameter or an out-of-bounds value refuses it,
counts it, and reports it — the refusal comes back in the statistics message it
publishes immediately after. Silence would be indistinguishable from a node
asleep on its solar budget, which is the one thing this answer may not be.

### The ceiling on that transport, and why it sits where it does

**Classes 1 and 2 go over the `cmd` topic. Class 3 does not.**

The difference between the transports is not speed but who stands on the other
side. Rows 1 and 3 have an authenticated counterparty: a password on a link you
control, or a monitor logging in over LoRa with rights the far side's operator
granted and can revoke. Row 2 has whoever holds broker credentials — and
`mosquitto/acl.example` supports one account per node, while a default deployment
runs on one shared account. On a shared account that is every node speaking to
the broker.

On top of that, there is no read-back inside the same request. The node reports
the outcome in its next statistics message, so a mistake is not visible at the
moment it is made — which is precisely what you want when the mistake can take a
node off the air.

"Everything everywhere" and "nothing anywhere" would both be wrong. The settings
you adjust on an ordinary day — name, position, advert interval, flood limits,
duty cycle — are classes 1 and 2, and they go through. The handful that can cut a
node off keep their two authenticated roads. If you want those from here, fill in
`MM_FW_NODE_USER` and `MM_FW_NODE_PASS`; the page says so where it refuses.

The ceiling is enforced twice: `nodeconfig.MQTT_MAX_RISK` on the server, so
nothing leaves that would be refused anyway, and `CFG_MQTT_MAX_RISK` in the
firmware, because the node is the one that carries the consequence.

### Radio parameters are refused on all three

`radio` — frequency, bandwidth, spreading factor, coding rate, which MeshCore
sets as one parameter — is **not written from a distance at all**. Not over IP,
not over the `cmd` topic, not over a monitor's mesh CLI. `tx` (transmit power) is
allowed and keeps its own risk class.

The asymmetry is the argument. A wrong `tx` makes a node weaker and leaves it
reachable: you still hear it, it still hears you, and you put it back. A wrong
frequency, spreading factor, coding rate or bandwidth takes it off the air — it
hears nobody and nobody hears it — and there is no way back that is not physical.
On a roof that is the end of the node. No confirmation repairs that: a threshold
protects against hesitation and against clicking the wrong row, not against a
number that puts a transmitter on a band the antenna is not cut for. `radio` also
only takes effect on reboot, so the mistake first becomes visible at the moment
it is already irreversible.

It is a refusal at the source, not a missing form field. A parameter merely left
out of a *form* can still be written with a hand-made request; the threshold
belongs where the request is actually carried out. So it lives in two places:

- **in the firmware**, where `radio` is gone from `CFG_PARAMS` since nodefirmware
  2.6.0. That one table is what `GET /api/cfg` publishes, what
  `POST /api/moncfg` accepts and what the `cmd` topic lets through, so one row
  removed closes all three entrances at once. The comment where it stood keeps
  the line written out, so putting it back is one line;
- **on the server**, as `nodeconfig.NO_REMOTE`, checked in `write()` before a
  transport is even chosen. Not redundant: it refuses before anything leaves,
  with a sentence saying why, and a node still on firmware older than 2.6.0 does
  have `radio` in its table and would accept it.

### What this asks of your broker

With transport 2 in use, "who may publish on `<prefix>/<node>/cmd`" stops being a
tidiness question. `mosquitto/acl.example` supports an account per node — each
node writes only under its own prefix and reads only its own `cmd` topic, and the
site writes only on `<prefix>/+/cmd`. That is the recommended arrangement.

**This installation currently runs on one shared account.** Then every node holds
credentials that may publish on every other node's `cmd` topic, and since 2.8.0
that means classes 1 and 2 on every node running it. Read
[`security.md`](security.md#the-broker-is-now-the-deciding-question) before
relying on this path — that section states the ceiling plainly and lists what
bounds it.

### The parameter list, for a node reached only over MQTT

The server builds its form from the node's own list and deliberately keeps no
table of its own. That list used to come only from `GET /api/cfg` — exactly what a
node the server has no web login for cannot answer.

Since 2.8.0 the node also publishes it (`cfgspec`) alongside its settings sweep.
So the action that opens this path is the one that was already there: press
**fetch settings** once, and the site knows both the values and the bounds. Until
that has happened the page says so and offers no form, because a form built from
a table invented on the server is precisely what this design refuses.

---

## Which settings may be written

**All of them, bar four.** The list is the full `handleSetCmd()` surface of
`src/helpers/CommonCLI.cpp` — twenty-seven parameters — not a curated safe
corner. Three of the four were never on it (below); the fourth is `radio`, which
was removed in nodefirmware 2.6.0 — see
[radio parameters](#radio-parameters-are-refused-on-all-three) above.

That is a deliberate reversal of the first design, which admitted only
parameters that could not cut off reachability. Safe, and beside the point: the
settings you most need at a distance *are* the dangerous ones — transmit power,
radio parameters — and omitting them does not remove the risk. It only means
somebody fetches a ladder, or a serial cable, and does the same thing with less
care and no record.

So the risk moved from **omission** to **handling**. Every parameter carries a
risk class, and the class decides how much friction a change costs.

### The three that are still absent

| Not offered | Why |
|---|---|
| `prv.key` | Replaces the node's identity. That is not a setting, it is a different node: every contact list, ACL and monitor entry elsewhere in the mesh then points at somebody who no longer exists. There is no confirmation that makes this a good idea from a web page |
| `bridge.secret` | A shared secret that comes straight back out on the read-back. A password that has been in a log line or a screenshot is gone |
| `freq` | MeshCore accepts `set freq` **only from the serial cable** (`sender_timestamp == 0`), and this path deliberately passes something else. Frequency belongs with the other three radio values and goes through `radio`, which *is* validated — `set freq` is not |

### Risk classes

| Class | Meaning | Confirmation |
|---|---|---|
| **Plain** | A value you can just set back | Save is enough |
| **Writes noticeably** | Changes how the node behaves on the mesh, but cannot put it out of reach | An explicit tick |
| **Can cut off reachability** | Touches the radio or who may log in | Type the node's name |

**Plain** (9) — `name`, `lat`, `lon`, `owner.info`, `advert.interval`,
`flood.advert.interval`, `rxdelay`, `txdelay`, `direct.txdelay`

**Writes noticeably** (12) — `dutycycle`, `af`, `flood.max`,
`flood.max.unscoped`, `flood.max.advert`, `int.thresh`, `agc.reset.interval`,
`multi.acks`, `path.hash.mode`, `loop.detect`, `cad`, `adc.multiplier`

**Can cut off reachability** (7) — `tx`, `repeat`, `allow.read.only`,
`radio.rxgain`, `radio.fem.rxgain`, `guest.password`, `radio`

The line that matters is the one between the second and third class, and it is a
single question: *if this goes wrong, can the node still be reached along the
route you steer it with?* On a node of ours there are two independent ways in —
break the WiFi and the mesh CLI answers, break the radio settings and the admin
page answers — so a mistake is annoying. On a stock repeater reachable only over
LoRa there is one way in, and a wrong frequency is the end of it.

Typing the node's name is the same device the firmware page uses for critical
nodes, and it is there for the same reason: the failure it catches is not doubt,
it is a click on the wrong row, and a yes/no question does not help against that.

**The confirmation is enforced on the server, not only in the page.** A
threshold you can skip with a hand-edited form is a styling choice, not a
threshold.

### Type drives the control

An input you *can* type an invalid value into is an input that can break a node,
so the control follows the declared type:

| Type | Control |
|---|---|
| `enum` | Dropdown containing exactly the allowed words (`loop.detect` → off / minimal / moderate / strict) |
| `bool` | Dropdown of `on` / `off` — not a free field, because MeshCore compares with `memcmp(…, "on", 2)`, so upstream `onzin` means *on* |
| `int` / `float` | Number field carrying that parameter's own `min` and `max` |
| `radio` | **Not offered.** Firmware 2.6.0 and up do not list it, so the row does not appear; a node on older firmware still does, and there the page shows the reason instead of four fields. It used to be four number fields with their own ranges, on the argument that one text box for `869.525 250 11 5` is the box a typo turns into a lost node. True, and not far enough: a correctly filled form can put a transmitter on a band the antenna is not cut for just as finally |
| `text` | Free text, only where it really is free text |
| `text` + secret | Password field, never pre-filled — see below |

### Where the list actually lives

**In the firmware**, compiled in (`CFG_PARAMS` in `MeshManagerNet.cpp`). Not in the
server, because the server is editable by whoever runs the site, and this list is
what stands between a click and the radio.

The server keeps **no second copy**. It asks the node (`GET /api/cfg`) which keys
it allows, of what type, between which bounds and in which risk class, builds the
form from that answer, and validates against it before sending. That still
satisfies "validate on both sides" — the server check gives a fast error next to
the input field, the node check is the one that counts — but there is only ever
one list, so the two cannot drift into offering a parameter the node refuses.

Bounds are ours, and in several places they are the **only** ones there are:
`lat`, `lon`, `af`, `tx`, `int.thresh`, `multi.acks` and `adc.multiplier` are
read upstream with a bare `atof()`/`atoi()` and no check whatsoever.
`atof("noord")` is `0.0`, so a typo puts the node in the Gulf of Guinea and the
CLI answers `OK`. **A node that accepts a nonsense value is more dangerous than
one that refuses**, and stock MeshCore is the accepting kind.

Elsewhere ours are stricter on purpose: MeshCore accepts `0` for both advert
intervals, meaning "stop advertising" — that does not make a node unreachable,
but it does let it sink out of everyone's list, which on a roof feels the same.

### One setting is a secret

`guest.password` is marked secret. It is read back and compared like everything
else — the verification is the whole point of this endpoint — but **the value
read is not returned**, and the page shows `(verborgen)` instead. The input is a
password field and the current value is never pre-filled.

Otherwise the password you just set would sit in the admin page's HTML, in the
browser history and in every screenshot of it, and a password that has been there
is gone. That is the same reason `bridge.secret` is absent altogether; the
difference is that `guest.password` is a setting you genuinely want to change
from a distance, so it is handled rather than dropped.

### One setting only takes effect on reboot

`radio` answers `OK - reboot to apply`. So the read-back shows the new values
while the radio is still running on the old ones, and whether the new ones work
is only discovered at the restart. That is precisely the situation in which a
node does not come back, and the page says so rather than reporting a plain
success.

### The endpoints

Both behind the node's own HTTP login, the same one that guards `/api/fw` and
`/api/backup`.

**`GET /api/cfg`** — what this image allows, so the page never offers a key the
firmware does not have:

```json
{"params":[{"key":"loop.detect","kind":"enum","lo":0,"hi":0,
            "choices":"off|minimal|moderate|strict","risk":2,"reboot":0},
           {"key":"radio","kind":"radio","lo":0,"hi":0,
            "choices":"","risk":3,"reboot":1}]}
```

**`POST /api/cfg`** with form fields `key` and `value`:

```json
{"ok":1,"step":"","key":"advert.interval","asked":"61",
 "applied":"60","exact":0,"reply":"OK"}
```

`step` on failure is one of `sleutel` (not on the list), `waarde` (outside the
bounds), `bevestiging` (confirmation too light) or `node` (the CLI refused), and
never merely `error`.

The key is never taken from the request when the command is built — it is looked
up in the compiled-in table and the table's own spelling is used — so there is no
string from the caller in the command except the value, which is always the last
word. The CLI call also passes a **non-zero sender timestamp**: `0` means "this
came from the serial cable" in MeshCore and unlocks commands that belong only
there (`erase`, `get prv.key`, and `set freq`). This path needs none of them, so
if the table ever turns out to have a hole, the hole is smaller.

### Validation happens on both sides, and they are not the same check

- **The server** validates type and range before sending, so a typo produces a
  refusal next to the input field instead of a packet. It validates against the
  bounds the *node* reported, not against a list of its own.
- **The node** validates again before it issues `set`. This is the check that
  actually protects the mesh, because the server is editable by whoever runs the
  site and the firmware's table is compiled in.

That second check matters most for the dangerous class, and for a reason worth
being exact about: a frequency outside the band is not *risky*, it is simply
**wrong**, and no number of confirmations should let it reach the radio. The
confirmation governs whether a legal value may be set; the bounds govern whether
a value is legal at all. They are different questions and they are answered in
different places.

For a `semi_managed` target the transmitting node is the monitor, running our
firmware, so the table and its validation apply before anything is radiated. The
next section is about that route.

---

## Writing over LoRa, through the monitor

This is the route the whole project exists for, and until firmware 2.4.0 it was
the one thing in this document that was designed and not built. The node it
serves is JessaZH: stock MeshCore on a roof, no IP path, and none coming.

**One write path, three transports.** Everything above still applies without
exception — the same parameter table, the same bounds, the same three risk
classes, the same confirmations, the same permissions, the same read-back. Only
the last step differs. `nodeconfig.write()` runs every check first and picks a
transport afterwards; a second function for the radio route would have been a
second place where a threshold can go missing, and that is the kind of fault you
discover when a node has gone quiet.

| Target | Where the server knocks | What happens there |
|---|---|---|
| IP path of its own | the node, `POST /api/cfg` | one `handleCommand()` call, tenths of a second |
| LoRa only | its **monitor**, `POST /api/moncfg` | two packets over a shared band, tens of seconds |

**The monitor needs the new firmware. The target needs nothing.** This is the
point of the design rather than a detail of it. JessaZH learns nothing, receives
nothing and notices nothing: two ordinary CLI commands arrive, exactly as they
have since the settings sweep was built. A node that will not get new firmware
for months does not need any.

### What actually goes on the air

Two commands, and the second is not optional:

```
set <parameter> <value>
get <parameter>
```

The reported outcome is what the **second** one returns, with the request beside
it. Never what the node answered to the first.

That is the same discipline the IP route follows, and here it carries more
weight for two reasons that compound. The target has no second way in, so a
wrong value is not correctable from a chair. And a round takes long enough that
nobody checks by hand afterwards — over IP you notice within seconds that
`advert.interval 61` became 60, over LoRa you would not notice for a month.

The comparison — is what came back the same *value* as what was asked, allowing
for `869.525 250 11 5` versus `869.525,250,11,5` and for `50.0%` versus `50` —
happens in one place in the firmware, shared with `POST /api/cfg`. Two copies
would eventually disagree, and then a warning would appear beside a radio that is
perfectly fine. An alarm that goes off too often is as useless as one that never
goes off.

### Test it first without changing anything

There is one test of this route that walks the entire path and changes nothing:
**write a parameter to the value it already has.**

`set tx 17` on a node already at 17 exercises transmitting, receiving, parsing
the reply and reading back. If it fails, nothing is broken. The node page
pre-fills every input with what the last sweep read, so this is one click.

Do that before anything else on a node you cannot physically reach, and do it
again whenever a monitor link is new or has been rebuilt. It is the difference
between testing and hoping.

It is also the only thing this project does to JessaZH. Until somebody has
watched a no-op complete there, no real value is written to that repeater.

### A third outcome that only exists on radio

Over IP a write succeeded or it did not. Over LoRa there is a third state, and
flattening it into "failed" would be a lie in the more dangerous direction.

| `step` | What is certain | What to do |
|---|---|---|
| *(empty)*, `ok` | the parameter was read back; `applied` is what stands in the node | nothing, unless `exact` is false |
| `niet_verstuurd` | **nothing went on the air** — the login was unanswered, or the monitor's packet pool was full. Certainly nothing changed | try again |
| `geen_antwoord` | the `set` **left**, and no answer came. Whether the node executed it cannot be seen from here | a fresh settings sweep is the only way to find out. Do not simply repeat the write |
| `geen_teruglezing` | the `set` was answered, the `get` was not. Something may have been stored and it was not established what | read it back with a sweep |
| `node` | the node refused the command and said so | fix the value |
| `bezig` | still running on the monitor | reload the page; the outcome is kept there |
| `monitor` | the monitor refused to start — not in its list, a sweep is running, too soon after the last | the message says which |

`geen_antwoord` deserves its own word precisely because "failed" would make
somebody assume nothing happened, and on a node you cannot inspect, that is the
assumption you must not make. The server also refuses to record such a write in
its own settings table: a guess in the column is worse than an empty cell.

The failure worth knowing in advance is the read-only monitor — logged in
perfectly, every command met with silence. That produces `geen_antwoord` for a
write, and the fix is `setperm <monitor-pubkey> 3` on the far side. The node page
shows that diagnosis above the form, from the monitor's own counters, so it is
answerable before the button is pressed rather than after. See
[Telling the three silences apart](#telling-the-three-silences-apart).

### The endpoints on the monitor

Both behind the monitor's own HTTP login — `MM_FW_NODE_USER` /
`MM_FW_NODE_PASS`, the same credentials `/api/fw` and `/api/cfg` use. That login
belongs to a node of ours. **The server never needs a secret of the target**;
whatever gets the monitor in lives on the monitor, or does not exist because the
far side put our public key in its access list.

**`POST /api/moncfg`** with `key` (the target's public key), `param` and
`value`. Answers **202**, not 200, because nothing has happened yet:

```json
{"ok":1,"step":"","busy":1,"msg":"gevraagd; twee commando's over LoRa, …"}
```

**`GET /api/moncfg`** — the running or last-finished write. The fields line up
with `POST /api/cfg` on purpose, so the server produces one shape of answer and
the page need not know which route it came by:

```json
{"seq":3,"busy":0,"ok":1,"step":"","key":"e3d3f4d7edd0","param":"tx",
 "asked":"17","applied":"17","exact":1,"reboot":0,"reply":"OK",
 "end":"klaar","age":31}
```

**The result lives on the monitor, not on the server.** That is why the browser
does not have to keep waiting: the server gives up after 40 seconds and says the
write is still running, and a reload finds the outcome. It also avoids a job list
and a background thread in the server for an action of half a minute — and it is
the more honest place, since the node that did the work is the only one that
knows how it went.

The same thing from a serial cable, the telnet console or the mesh CLI:
`wifi mon set <hex> <param> <value>`, and `wifi mon set` on its own for how the
last one ended. Not only for diagnosis: the mesh CLI is the route that fails
last, so a setting on the roof repeater can still be corrected from a phone when
the WiFi, the site and the broker are all gone.

### What it costs, and why the pause is shorter here

Two commands and two replies — roughly a tenth of a settings sweep. The waits
are the sweep's, because they were measured on the same band over the same hops
and there is no reason a `set` would come back faster than a `get`: 20 s for the
first command after a login, 12 s for each one after that, 2 s between them, and
a hard 90 s ceiling on the whole thing.

One at a time, on request only, and **no retries anywhere**. Repeating a `set`
that stayed silent would run it a second time on a node that may already have
taken it.

Between two writes there is **one minute**, where the sweep has ten. That gap is
deliberately the smaller of the two, and it is the most considered number in this
section: *the action you want to take right after a mistake is the opposite one.*
Whoever set `tx 5` where `tx 20` belonged must be able to put it back within a
minute, not within ten. Recovery must never be throttled harder than the mistake
it undoes — the same rule that makes `filter off` cheaper than `filter on`. What
the gap does stop is a script filling the band, and a minute is ample for that:
it is longer than a whole round takes.

Unlike the sweep, this route needs **no working broker**. The sweep publishes its
result over MQTT and has nothing to do without one; this answers over HTTP to
whoever asked. An installation with no internet, or with a broker that is
momentarily gone, should still be able to put a radio setting right.

## Confirm-or-revert: examined, and deliberately not built

The obvious safety net for risky configuration changes is the one network
equipment uses: apply the change, and undo it automatically unless a confirmation
arrives within N minutes. It was considered seriously here and rejected, for a
reason that is worth writing down because it inverts the intuition.

**The nodes that need it cannot have it. The nodes that could have it do not need
it.**

- A `semi_managed` node runs **stock MeshCore**. There is no pending-change
  mechanism in it and no way to add one without replacing the firmware — which is
  the very thing that cannot be done to those nodes. So for JessaZH, the node
  where a bad setting is unrecoverable, confirm-or-revert is not available. Not
  hard, not expensive: unavailable.
- A `full_managed` node could implement it, and does not need it, because it
  already has two independent ways in. Break the WiFi and the mesh CLI still
  answers. Break the radio settings and the admin page still answers. The node
  that could hold a revert timer is the node that already has a second door.

So the effort goes into the risk classes and the bounds instead, which stop the
change rather than undo it. A prevention that works on stock firmware beats a
rollback that only works where it is not needed. It is also why the heaviest
class asks for the node's name rather than promising to put things back: nothing
here can promise that.

---

## Scheduling the settings sweep

A node that publishes to MQTT reads its own CLI settings once a day. That costs
nothing — it is a function call inside its own firmware. A node reachable only
over LoRa is read out by its monitor, and then one round is twenty questions and
twenty answers over a shared band, paid for by a repeater on a roof.

So the original design gave monitored nodes **no schedule at all**: on request
only. That was defensible on airtime grounds and wrong in practice. What it
produced was measured: settings twelve hours old, and a region tree seven days
old, on a node that answered perfectly the moment somebody asked. A page that
presents silent staleness as fact is exactly the half-truth the rest of this
project tries to avoid.

There is now a schedule, and airtime remains the constraint rather than
disappearing.

### Three limits, and they stack

**1. An interval per node, off by default.** Not one global figure, because the
cost differs per node: a repeater at the edge of your range pays each round with
power from a solar panel and with packets that may not arrive, while a node two
streets away is nearly free. One interval for all of them means either setting it
by the most expensive node or neglecting the cheapest. Adding a node grants no
recurring cost.

**2. One round at a time, with a minimum gap between them.** Not ten timers that
happen to coincide: one queue, and the most overdue node wins. Ten rounds in the
same hour would occupy the band for an hour. The gap is global — across all
nodes, not per node — because the band is shared whether or not two monitors are.

**3. A ceiling per day across all nodes.** This catches what neither of the
others catches: somebody setting twenty nodes to daily without doing the
arithmetic. When it bites, schedules slip, and the page says so. **The per-node
interval is a wish; this is what actually happens.**

| Setting | Default | Meaning |
|---|---|---|
| per node, on the node's page | off | hours between rounds for this node |
| `MM_SWEEP_ENABLED` | `1` | the scheduler as a whole |
| `MM_SWEEP_MIN_GAP_MIN` | `15` | minutes between any two rounds; never below 10 |
| `MM_SWEEP_MAX_PER_DAY` | `48` | rounds per 24 hours across everything |

The minimum gap cannot go below 10 minutes, and that floor is not arbitrary: it
is `MON_SET_MIN_GAP_MS` in the firmware. Asking more often is pointless because
the monitor refuses anyway, and it would let the site promise something that does
not happen.

### It is not a back door around the firmware's own limits

The sweep itself does not run here. It runs in the monitor's firmware, with its
own budget: a minimum gap between rounds, a cap per round, a stop after three
consecutive silences. This scheduler decides only *when to ask*, and it goes out
through the same `publish_command` as the manual button — no separate path to the
broker.

That is the same rule the manual clock-sync button follows, and for the same
reason: a second route to the broker would be a back door around the checks on
the first, and the only visible symptom would be too much traffic on somebody
else's band.

### A node that has no route does not block the queue

If the scheduler picks a node it cannot currently reach, it **writes that down**
rather than retrying every minute. Without that, the unreachable node stays the
most overdue for ever and nobody else gets a turn — and the page would keep
promising a round that never comes.

### What the page shows

Per node: the interval, when the next round falls, and what the previous one
produced. Without that last part a schedule is a promise you cannot check.

It also completes the three cell states in the comparison table. Until now an
empty cell meant "never asked" or "asked, no answer"; **"the schedule is off"** is
a third thing, and it is the one that explains why a value has been sitting there
for a week. The comparison table therefore carries the schedule as a column of
its own — with twenty nodes, "which ones are set to never" is a question about the
set, and a node that is the only one without a schedule is precisely the node
whose values quietly go stale.

### Rights

Changing a schedule is `node.schema`, one class heavier than the button that
starts a single round (`node.uitvragen`). That is not strictness for its own
sake: the button costs airtime once, this costs it every day, on a band that
belongs to everybody. Whoever switches it on imposes a recurring load on somebody
else's mesh.

---

## After a write, read it back

A write is never reported as a success on the strength of having been sent. The
node re-reads the parameter with `get <key>` immediately after the `set`, in the
same request, and the answer carries **`asked` and `applied` separately** plus an
`exact` flag.

This is not defensive habit. It is two measured behaviours in MeshCore that both
answer `OK` while storing something else:

- **`set lat abc`** → `atof()` yields `0.0`. Answer: `OK`. The node now claims a
  position it was never given.
- **`set advert.interval 61`** → stored as `minutes / 2` in a single byte, so
  `30`; `get advert.interval` multiplies by two again and returns `60`. Answer:
  `OK`. **Odd minute values always come back rounded down to even**, and this is
  the normal case rather than an error.

So the admin page has three outcomes, not two: *set*, *set but not exactly* (with
both numbers shown, and a note that this is not a fault), and *not set* with the
node's own reason. Anything that collapsed the middle one into "success" would be
telling the same kind of half-truth the old OTA path told.

Over LoRa there are two more, because the read-back can fail on its own — see
[A third outcome that only exists on radio](#a-third-outcome-that-only-exists-on-radio).

`get <key>` is the same read the daily settings sweep uses, so there is no second
code path that could disagree with the first.

Because that read is what the page reports, the server also writes it into its
own settings table. Without that the "now" column keeps showing the old value
next to a message saying the write succeeded, until the next sweep — which over
LoRa costs airtime on somebody else's band and happens at most daily. What is
recorded is what came *back*, never what was asked, and nothing is recorded when
the read-back did not happen.

---

## Setting the clock

The clock has its own section on the node page and its own confirmation naming
the node, because it writes one number that cannot be corrected from here.

![The 'Klok' section of Voorbeeld-Dakrepeater. A table shows 'Laatst tijd gestuurd: nog nooit door deze site' and 'Automatisch: ja, de site doet dit uit zichzelf'. An amber-bordered block 'Klok nu synchroniseren', tagged 'schrijft op het apparaat', explains that this repeater has no route of its own, that its clock comes from node bb11bb11bb11 which monitors it, and in bold that the button therefore does not target this repeater alone — the site sends the time to that node, which then checks the clocks of all repeaters it monitors. Below the active button a paragraph explains what the page does and does not know about whether the clock is actually right.](images/beheer-node-klok.png)

Four things worth knowing before pressing it.

**The button is wider than the node it sits under.** For a relayed repeater the
time goes to its monitor, and that monitor then checks the clocks of *every*
repeater it monitors. The firmware has no way to narrow that down, and that is
not an omission — a clock round costs one question and one answer per monitored
repeater, roughly a fifth of an ordinary poll round. The page says so instead of
pretending the button points at one device.

**`time` needs nodefirmware 1.10.0**, along both routes — unlike settings, where
the boundary depends on the route (1.8.0 direct, 1.9.0 via a monitor). It is the
same recipient having to know the same word.

**A clock can only go forward.** An advert carries the clock of the node that
sent it, and any node that already knows the sender throws away an advert whose
timestamp has not increased. Setting a clock back an hour makes that repeater
invisible for an hour, so the firmware never corrects backwards — which means a
time set too far into the future is a mistake you repair in person.

**The site refuses when it does not trust its own clock.** Three checks —
`adjtimex(2)`, a wall-versus-monotonic jump check, and a persisted high-water
mark — and if any of them is unhappy nothing goes out. The page says which.

The site also does this by itself, once a day; the button exists so you do not
have to wait. Full reasoning in [`clocksync.md`](clocksync.md).

---

## Upgrading firmware, and going back

Firmware lives on its own page, because "which release runs where" is a question
you ask across all nodes at once. [`firmware-upgrade.md`](firmware-upgrade.md)
has the full mechanism — the checksum verified twice, why only success reboots,
what a checksum does *not* prove. What belongs here is which nodes can receive an
image at all, and what the page does when one does not come back.

![The 'Nodes' part of the firmware page with three invented nodes. Voorbeeld-Thuisnode shows nodefirmware 1.10.0, MeshCore v1.16.0, build environment heltec_v3, management address http://192.0.2.11, a 'kritiek' pill, and an upgrade form with a version dropdown plus a field to confirm by typing the node name. Voorbeeld-Zendmast shows a notice in bold — 'Node niet teruggekomen na upgrade naar 1.10.0', with the step herstart beside it, explaining the image was written and verified, that this is about the restart, and that falling back is possible with 'wifi fw rollback' over the mesh CLI, with a 'Wegklikken' button. Voorbeeld-Dakrepeater has an empty address field, a disabled 'Node uitvragen' button and the note 'Geen upgrade mogelijk' because it is relayed over LoRa.](images/beheer-firmware.png)

`firmware.ota_route()` decides, and returns a blocker the page turns into a
sentence. In the order they are tested:

| Blocker | Means | What to do |
|---|---|---|
| `no_credentials` | The server has no login for the nodes' own admin pages | Set `MM_FW_NODE_USER` and `MM_FW_NODE_PASS`. Until then every upgrade button stays off |
| `relayed_only` | No management address, and this node's figures arrive through another node | Nothing. This is a **permanent state**, not a forgotten setting: 1.3 MB over LoRa against the duty cycle is days. Flash it over USB |
| `no_host` | No management address, and the node is not relayed | Fill one in, if there is one |
| `no_fw` | The node reports no MeshManager version | It is probably not running our firmware, and an image from this project does not belong on it |

Two further refusals happen after `can` is true. A node that reports no **build
environment** gets no upgrade button but a "Node uitvragen" button instead — the
env comes from the node itself and from nowhere else, because a wrong image on a
node you cannot touch is not repairable. And a node marked **kritiek** requires
its name typed out in full, the same device the heaviest settings class uses, for
the same reason: it catches a click on the wrong row, which a yes/no question
does not.

**Going back is one write and a restart**, because the partition table holds two
application slots and an OTA never erases the one it is not writing. It is
deliberately *not* automatic: a solar repeater browns out for reasons that have
nothing to do with firmware, and "three failed boots, roll back" would quietly
undo good upgrades forever. Three restarts already drop a node into safe mode,
which keeps it reachable; rollback is then a decision somebody makes.

---

## When a node does not come back

This is its own state, and that is the entire point. `niet_teruggekomen` is
neither a failure nor a success: the image was written, the digest matched, the
node restarted, and then it stopped answering. The job stays on the page until
somebody clicks it away — deliberately the only way it disappears, because a node
that quietly vanishes after an upgrade is the event this whole design exists for.

The site waits **150 seconds**, polling every five, before it says so.

| What the site knows | What it does not know |
|---|---|
| The image reached the node and its SHA-256 matched, twice | Whether the node booted |
| `otadata` was written, so the node intended to start the new image | Whether it joined the network |
| It has not answered on its management address since | Whether it is running, in safe mode, or dead |

What to try, cheapest first:

1. **Wait a little longer.** 150 seconds is a timeout, not a verdict. A node that
   re-associates slowly can still turn up.
2. **`wifi fw rollback` over the mesh CLI.** This is the important one: it works
   even when the node is invisible on the network, because the mesh path and the
   IP path are independent. If the node is on the air at all, this reaches it.
3. **Safe mode.** Three failed restarts and the node raises its own access point
   with its admin page. Reachable without the network it just lost.
4. **`start ota` over the mesh CLI**, then the soft AP, if the module has
   disabled itself after six restarts.
5. **USB.** In person, and the reason a critical node is one you can physically
   reach.

If the node comes back on the **old** version instead, that is `mislukt` with
step `terug_op_oud`, not this state — the rollback happened by itself and the
node is fine.

The same shape of problem exists one size down, after a `radio` change: that
setting answers `OK - reboot to apply`, so a wrong value is only discovered at
the restart. Steps 2 to 5 are the way back from that too.

---

## Two kinds of credentials, and they live in different places

This is the part the first design got wrong, and the difference matters enough to
write out.

| Path | Who authenticates | Where the secret lives |
|---|---|---|
| Server → node, over HTTP (`/api/fw`, `/api/cfg`, `/api/mon`) | the server presents that node's **web login** | on the server, in `MM_FW_NODE_USER` / `MM_FW_NODE_PASS` |
| Monitor → target, over LoRa (the CLI sweep, and writing) | the **monitor** logs in to the target | on the monitor: either nothing at all, or the target's admin password in its own monitor list |

The first design asked the server for credentials in the *second* case, which is
backwards. **The server never needs to know a target node's password.** It needs
to reach its own monitor; the monitor holds — or does not need — whatever the
target requires.

That mistake was visible in the interface: a relayed node showed *"the server has
no credentials for the nodes' management pages"*, which is both true and
irrelevant. No credential on the server would ever have helped that node.

### The two ways a monitor gets in, and which to prefer

**Access list — recommended.** The far side's operator runs
`setperm <monitor-pubkey> 3` once. There is then **no password anywhere**: the
monitor logs in with an empty string and the far side looks our public key up in
its own access list. Nobody hands a secret over, and the other operator can
revoke it from their side alone without asking us anything.

The `3` matters. `1` is read-only and is enough for status polling, but **not**
for the settings sweep: a repeater only runs a CLI command for a client it
considers an admin. A read-only monitor logs in perfectly and is then met with
silence, command after command.

**Password — second choice.** The monitor holds the target's admin password in
its own monitor list. The site can *set* that password and **passes it through
without keeping it**: it goes to the monitor and is not written to the database,
to a setting, or to a log.

The cost is stated rather than hidden: the site cannot show you what is
configured — only *that* something is — and cannot re-send it without you typing
it again. The benefit is that a break-in on the website is not a keyring for
other people's equipment. See [`security.md`](security.md), where that promise is
now stated in its narrower, true form.

### Telling the three silences apart

A sweep that ends in silence has three causes that look identical from a
distance, and this is where somebody loses half an hour. The monitor already
knows enough to separate them, so the site reports which one it is:

| What the monitor reports | Diagnosis | What fixes it |
|---|---|---|
| Login never answered, and we do **not** hear the node's adverts | Out of range | Nothing here — it is a radio problem |
| Login never answered, but we **do** hear its adverts | Not allowed in: our key is not in its access list, or the password is wrong | `setperm <our-pubkey> 3` on the far side, or the right password |
| Login succeeded, every command silent | **Read-only.** In as a reader, not as an administrator | `setperm <our-pubkey> 3`, or the admin password |
| Commands answered | Fine | — |

The third row is the treacherous one: everything looks healthy and nothing comes
back. The heard list is the only thing that separates row one from row two —
if its adverts are arriving, "cannot reach" stops being the explanation and
"may not" becomes it.

---

## Rights are the hinge, and their failure mode is confusing

A MeshCore repeater runs a CLI command only for a client it considers an **admin**
(`handleCommand` is reached from `onPeerDataRecv` only under `client->isAdmin()`),
and it says nothing at all to a client it does not.

So a read-only monitor logs in **perfectly**, sends eighteen commands, and hears
eighteen silences — which looks exactly like a node that is out of range. That is
the single most confusing failure in this whole area, and the site should name it
rather than reporting "no answer" and leaving the operator to guess.

Three states worth distinguishing on the page:

| State | How it looks | How to fix it |
|---|---|---|
| No rights | login gets no reply at all, exactly like out of range | the far side adds `setperm <our-pubkey> 1` |
| Read-only | login succeeds, every command is met with silence | `setperm <our-pubkey> 3`, or the admin password |
| Admin | commands answer | — |

The heard list is what separates "no rights" from "out of range": if we are
hearing its adverts, we can reach it.

An empty password in the monitor list is a choice and not an omission — it makes
the far side skip the password check and look our public key up in its access
list instead. That is the tidier arrangement: nobody hands out a password, and
the other operator can revoke us on their side alone.

---

## Telemetry without credentials

Can the site read anything useful from a repeater it holds no password for? This
was researched before building anything, because the answer decides whether the
feature is worth having. **It is: rather more than expected.**

### What the source says

A login is always required — `handleLoginReq()` in `examples/simple_repeater/MyMesh.cpp`
returns `0` (no reply at all) for anything it does not accept. But there are
three ways to be accepted, and the third is the interesting one:

1. **Blank password + the sender is in the ACL.** The operator put you there with
   `setperm <pubkey> 1`. This is the tidy arrangement the monitor list already
   uses.
2. **The admin password.**
3. **The guest password** — and `guest_password[0] = 0` in `CommonCLI.h:189`, so
   it is **empty by default**. A blank password therefore matches it.

That third path is the answer. On a stock repeater whose operator never ran
`set guest.password`, an unknown node logging in with a blank password is
accepted as `PERM_ACL_GUEST`.

### What a guest may then ask for

| Request | Guest gets it | What it contains |
|---|---|---|
| `REQ_TYPE_GET_STATUS` `0x01` | **yes**, no permission check at all — the source even says so: *"guests can also access this now"* | The whole `RepeaterStats`: uptime, air time, TX/RX counters, flood/direct split, duplicates, error flags, battery millivolts, noise floor, last RSSI and SNR, queue length |
| `REQ_TYPE_GET_TELEMETRY_DATA` `0x03` | **yes, but reduced** — `perm_mask` is forced to `0x00` for a guest | Battery voltage and MCU temperature. External sensors are withheld |
| `REQ_TYPE_GET_NEIGHBOURS` `0x06` | **yes**, no permission check | The neighbour list |
| `REQ_TYPE_GET_ACCESS_LIST` `0x05` | no — `&& sender->isAdmin()` | — |
| CLI commands (`get`/`set`) | no — `handleCommand` is reached only under `client->isAdmin()` | — |

So a credential-free poll yields roughly what this site already shows for a
monitored repeater, minus the CLI settings. That is a real feature, not a
consolation prize.

### Two findings worth writing down

**`allow.read.only` does nothing on a repeater.** It is stored, it is readable and
writable over the CLI, and the only code that consults it is
`examples/simple_room_server/MyMesh.cpp:351`. On a repeater it is an inert
setting. It is still classified as a dangerous parameter here, because the same
module can be built for a room server where it *does* gate access — but nobody
should expect toggling it on a repeater to change who may poll it.

**A refusal is silent.** `handleLoginReq` returns `0` and sends nothing. So a
repeater whose operator *has* set a guest password is indistinguishable, from
here, from one that is out of range or switched off. That ambiguity is real and
cannot be resolved by trying harder; it can only be reported honestly.

### What this means for the design

Three states, and the page must not merge them:

| Result | Meaning |
|---|---|
| Answered | Telemetry is available without credentials |
| No answer | **Any of**: out of range, guest password set, firmware too old. Not distinguishable |
| Answered before, silent now | Something changed — worth showing differently from never-answered |

The heard list is the one thing that narrows the middle row: if the node's
adverts are still arriving, "out of range" becomes unlikely and "not allowed"
becomes probable. That is a hint, not proof, and should be worded as one.

### Restraint, deliberately

This is polling **other people's equipment** on a shared band, so:

- **No automatic sweep of everything heard.** A discovery that knocks on every
  node it has ever heard is exactly the behaviour a shared mesh does not need.
  The operator picks who is asked.
- **The air-time cost is shown before the button is pressed**, not after.
- **Monitoring this way is telemetry only.** A node polled without credentials
  gets no management controls, because there are none to give — the CLI is
  closed to guests. That is enforced by the far side, which is the best kind of
  enforcement.

### Level, or a property beside it?

**A property beside the level, not a fourth level.** `unmanaged` /
`semi_managed` / `full_managed` answers "what may we *do* to this node", and
polling telemetry is not doing anything to it — it asks, and the node decides.
A node we poll for telemetry is still `unmanaged`: we cannot change one setting
on it, we cannot write firmware, and we hold no credentials.

Making it a fourth level would also break the ordering the levels currently
have, which is one of increasing capability. Telemetry-polling is not "more than
unmanaged and less than semi-managed" — a `full_managed` node can be polled this
way too. It is a different axis, so it gets a different field.

---

## Working without internet

This project exists partly for emergency communication, so "does it still work
when the internet is gone" is not a curiosity — it is a requirement somebody has
to be able to check *before* they need it. The honest answer has three layers,
and they are not the same question.

### Layer 1 — no internet, local network intact

**Almost everything works.** The server, the broker, the database and the mesh
are all local; none of them reach out to the internet to do their job.

| Works | Does not work |
|---|---|
| Statistics ingest over MQTT | **Fetching firmware releases from GitHub** |
| Reading CLI settings, over MQTT and over LoRa | Map tiles, if the map provider is remote |
| **Writing CLI settings**, both transports | |
| Setting the clock | |
| The whole admin interface, the comparison table, the packet archive | |
| Pushing a firmware image the server **already downloaded** | |

The one real casualty is the firmware page. It lists releases by asking
`api.github.com`, and without internet that call fails. The page keeps working —
it shows the last list it managed to fetch, with the reason beside it — but a
release published while you were offline is not visible, and an image that was
never downloaded cannot be installed.

That is a reason to **fetch before you need it**, not a reason to distrust the
rest of the site. Nothing else on this list touches a remote host.

### Layer 2 — no local network either

The site cannot reach *any* node, by any transport. This is worth stating plainly
because "forced over the mesh" sounds like it should help here, and it does not:

> **Choosing the mesh transport does not remove the site's own need for one
> IP-reachable node.** It removes the *target's* need to be reachable. The server
> still has to hand the command to some node over MQTT or HTTP, and that node
> then carries it onward over LoRa.

So with the LAN down, the route back in is not this site at all — it is the mesh
CLI from a companion app on a phone, over Bluetooth or over LoRa. That path never
involved the server and is unaffected by anything here.

### Layer 3 — what the mesh transport is actually for

Given layer 2, why offer it at all? Because the interesting failure is not "the
network is gone" but "**the target is not on the network**":

- A repeater on a roof with no WiFi in range — the permanent state for the roof
  repeater in this installation.
- A node whose WiFi credentials were changed, or whose access point died, while
  its radio is perfectly healthy.
- A node in power-save mode with its WiFi asleep, which still hears LoRa.

In all three the site is online, the broker is up, and one node is reachable —
and the target is not. That is the case the mesh transport exists for, and it is
the common one.

It is also why forcing the mesh for a node that *is* IP-reachable is worth
having: it is the only way to exercise the LoRa write path against a node you can
still fix with a browser if it goes wrong.

---

## Discovery: point, then test once

The site sees every node in the traffic, so it could try each of them to find out
where it has rights. **It should not**, and this is a policy choice rather than a
technical limit.

- It costs airtime on a shared band, for a question nobody asked.
- It knocks on other people's equipment. A login attempt against a stranger's
  repeater is at best impolite and at worst indistinguishable from someone
  probing it.
- The answer goes stale anyway, because rights are granted on the other side.

Instead: **the operator points at a node** — from the heard list or by pasting a
public key — supplies whatever credential applies, and the site tests **once**,
then remembers the outcome. One deliberate knock, at a moment a human chose,
against a node a human named.

---

## What is built and what is not

| | State |
|---|---|
| Reading CLI settings over LoRa through a monitor | **built**, and has been for a while (`wifi mon settings <key>`, `settings <key>` on the `cmd` topic) |
| Levels as an explicit concept in code and UI | **built** — `level` / `level_why` on `commanding.describe()`, and `/admin` groups by them |
| Setting the clock, manually and daily | **built**, see [`clocksync.md`](clocksync.md) |
| Firmware upgrade over HTTP, with checksum and rollback | **built**, see [`firmware-upgrade.md`](firmware-upgrade.md) |
| `ota_route()` as a separate capability key | **built** |
| `niet_teruggekomen` as a state that stays until acknowledged | **built** |
| Writing settings to a `full_managed` node with an IP path | **built** — firmware 2.1.0 `POST /api/cfg`: the whole CLI surface bar three, typed controls, risk-driven confirmation, read-back. Requires the node's management address to be filled in |
| Writing settings to a `full_managed` node with no web login | **built** — firmware 2.8.0 `set <param> <value>` on the `cmd` topic, with the read-back reported in the next statistics message. Risk classes 1 and 2 only, and the node validates against its own table |
| Writing radio settings from a distance | **refused on every transport**, in the server and again in the firmware. `tx` is allowed; frequency, bandwidth, spreading factor and coding rate are not |
| Writing settings to a `semi_managed` node over LoRa | **built** — firmware 2.4.0 `POST`/`GET /api/moncfg` on the **monitor**, plus `wifi mon set` on any CLI. `set` then `get`, one at a time, budgeted, with the read-back as the reported outcome. Same table, bounds, risk classes and permissions as the IP route |
| Writing to a node's WiFi and MQTT settings | **not offered here.** Those are ours, not MeshCore's, and they already have their own forms on the node's own admin page and the `wifi` CLI |
| Confirm-or-revert | **examined and rejected**, with the reasoning above |
| Automatic rights discovery | **rejected**, point-and-test-once instead |
| Cross-repeater comparison table | **built** — `/admin/compare`, chosen columns, deviations from the majority marked |
| Editing from that table | **built** — a pencil per editable cell opens one editor below the table, pre-filled. One editor rather than an input in every cell: twenty nodes by six columns is a hundred and twenty forms each with its own confirmation, and the confirmation is exactly what becomes unreadable. It calls the same `nodeconfig.write()` as the node page, so the risk classes, the bounds and the read-back apply unchanged |
| Bulk edit across several nodes | **not built, and gated by design**: plain-class parameters only, never the two heavier classes. Ten nodes in one click is also ten nodes lost in one click |
| Forced mesh transport for a node that has an IP path | **not built.** The LoRa write path now exists, so what is missing is only the choice: the route is derived from the node rather than picked. It stays out until there is a node that is both monitored and IP-reachable to exercise it on |
| Telemetry polling without credentials | **researched, not built.** It works and yields more than expected — see above |
| MeshCore version for relayed nodes | **built** — `ver` joins the sweep, and one answer fills both version columns |
| A sweep schedule per node | **built** — off by default, one round at a time with a global minimum gap, and a daily ceiling across all nodes |
| Showing which right a monitor uses per target | **built** — access list or password, read from the monitor's own list, which reports *that* a password is set and never *which* |
| Telling the three silences apart | **built** — out of range / not allowed in / read-only, from login state plus the heard list |
| Setting a target's password from the site | **built as pass-through** — `nodeconfig.push_monitor_password()` sends it to the monitor and stores nothing. Not yet on a page: the form is the remaining piece |

> **JessaZH gets no-ops only.** The path is built and tested against a simulated
> monitor in the test suite; on the real repeater the first and for now the only
> thing written is a parameter set to the value it already holds. It is reached
> only over LoRa, so a mistake there is not correctable, and it is also the
> reference case the design exists to serve. Real changes go there after a no-op
> has been watched to complete.

## The packet filter

A packet filter decides which of *other people's* packets a repeater still
forwards. It is off by default, it is per node, and it is the only setting on
this page whose failure mode is a node that looks completely healthy.

That is the whole reason it gets its own section here rather than a row in the
settings table. Set a frequency wrong and the node goes silent — unpleasant, but
you find out within the hour. Set a filter wrong and the node keeps answering,
keeps advertising, keeps showing green on every page, and quietly relays nothing.
You find out when somebody complains that their messages stopped arriving, which
can be days.

**Before you switch one on, know how to switch it off.** Three ways, in order of
how much has to still be working:

1. `filter off` or `filter reset` **over the mesh CLI**. No WiFi, no admin page,
   no server — LoRa is up before any of them. This is the one that works when the
   others do not.
2. The buttons in the *Pakketfilter* block on the node's admin page.
3. `POST /api/filter` with `cmd=off` on the node itself.

On the site, those two are also the *cheapest* actions in the permission model —
`node.filter.gewoon`, lighter than switching a filter on. A role that may not
enable a filter may still disable one. Recovery must never be gated harder than
the mistake it undoes.

### What you can set, and what it costs

The six rule kinds, what they block and the price of each, are in
[`packet-filter.md`](packet-filter.md). Two of them surprise people, so they are
worth repeating here:

- **Blocking a channel needs the channel key, not its name.** All a repeater sees
  is one byte — `sha256(channel_key)[0]`. And one byte collides: roughly one
  channel in 256 shares it, and that traffic goes with it.
- **"Malformed" means structurally impossible**, not "the text looks wrong". The
  content is encrypted with a key a repeater does not hold.

### The three risk tiers, applied to filters

Same three as for settings, and the tier follows what a rule *blocks* rather than
what the form looks like. `hops 05 4` and `hops 05 0` are the same input box:
the first shortens the reach of group text, the second stops it dead. So the
second asks you to type the node's name, and the first only asks for a `ja`.

`filter on` moves up a tier when such a rule is already stored — because then
that click is the one that actually silences the traffic, not the click that
wrote the rule while the filter was off.

### Seeing that one is running

Look for it in three places, none of which you have to go hunting for:

- the **Pakketfilter** block on the node page, which shows what the node reported
  in its last statistics message, including what it dropped and why;
- the **Pakketfilter** column in the comparison table, in view by default —
  "which node has something on" is a question about the set;
- the `filter` object in `GET /api/v1/repeaters/{slug}`, which is public, because
  the people who notice missing traffic are not the ones with a login.

All three keep "never reported anything" (usually firmware older than 2.3.0) and
"reports that nothing is on" as separate states. Flattening those into one empty
cell would make the only question this exists for unanswerable.

---

## See also

- [`admin.md`](admin.md) — the pages themselves: every field, every form, the
  ordering by irreversibility
- [`commanding.md`](commanding.md) — how the route and the level are computed,
  and every blocker value
- [`clocksync.md`](clocksync.md) — whether this machine may tell the mesh what
  time it is
- [`firmware-upgrade.md`](firmware-upgrade.md) — the upgrade mechanism end to end
- [`mqtt.md`](mqtt.md) — the topics, and the four words the site may publish
- [`firmware.md`](firmware.md) — building and flashing the node firmware
