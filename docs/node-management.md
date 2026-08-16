# Managing nodes from the site

*[Nederlands](nl/node-management.md)*

A walkthrough of everything the site can do to a node, in the order you will
need it: recognising what a node is, bringing it under management, reading and
changing its settings, setting its clock, giving it new firmware — and what to do
on the day it does not come back. Plus, throughout, the part that takes the most
words: what the site deliberately will not do.

Reading a node is settled and has worked for a long time. Of the **writing**
side, some is built and some is designed and explicitly not built yet; each
section says which. The distinction matters more here than in most
documentation, because the failure mode is not a broken page. It is a repeater
on a roof that nobody can reach any more.

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
[over MQTT?](#can-settings-be-written-over-the-same-mqtt-route) ·
[which may be written](#which-settings-may-be-written) ·
[confirm-or-revert](#confirm-or-revert-examined-and-deliberately-not-built) ·
[reading it back](#after-a-write-read-it-back)

**The device** — [the clock](#setting-the-clock) ·
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
| **Write CLI settings** | no | designed, not built | **built** — full surface, risk-classed |
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

## Can settings be written over the same MQTT route?

The short answer, because it was asked directly: **technically yes, and the
recommended design does not do it that way.**

### Why not the `cmd` topic

The topic accepts `settings`, `status` and `time <epoch>` — a strict allow-list,
never a pipe into the CLI. The reasoning is in `MeshManagerNet.h` and it is about
who can reach that topic: **anyone holding broker credentials**, shared, leaked
or mistyped. One `reboot` in a loop costs you the roof repeater.

That argument does not weaken for writing. It gets stronger, because reading
cannot make a node unreachable and writing can. A wrong `freq`, a wrong `radio`,
`tx 0`, `repeat off` or a wrong WiFi setting on a node you reach only over LoRa
is not a mistake you correct — the setting takes effect, and with it the only way
back disappears. On a roof that is the end of the node.

### The route that is recommended instead

**Writes go over HTTP, to a node the server can reach, behind that node's own
login.** Which node that is depends on the level:

| Target | Write path |
|---|---|
| `full_managed` with an IP path | HTTP to the node itself |
| `semi_managed` (JessaZH) | HTTP to its **monitor** (DinX-Home), which issues `set …` over LoRa |
| `full_managed` without an IP path | not offered — see below |
| `unmanaged` | not offered, there is nothing to authenticate with |

This is worth dwelling on: the relayed case works. JessaZH is written by talking
to DinX-Home, and DinX-Home is on the LAN. **The reference case is covered by the
route that does not touch MQTT at all.**

What it buys:

- Broker credentials stay unable to change anything. The MQTT allow-list stays
  exactly three words, and nothing about the threat model of that topic changes.
- A write is authenticated against the node's own admin login, which is a
  credential a person holds, not a service account a container holds.
- The transport already exists and is already used for firmware, including its
  address validation and its error reporting.

What it costs: a `full_managed` node with no IP path cannot be written to at all,
even though its `cmd` topic works. That case does not exist in this installation
today. If it appears, the answer is not to widen the MQTT allow-list quietly —
it is to reopen this decision on purpose, with the risk classes below as the
thing that has to hold.

### If it were ever done over MQTT anyway

Then the broker configuration becomes load-bearing rather than hygienic, and
`mosquitto/acl.example` has to say so:

- The site's account is the **only** one with `write meshmanager/+/cmd`. Node
  accounts get `write meshmanager/<own-id>/#` and nothing else, so a compromised
  node cannot command its neighbours.
- Every node account is per node, never shared. A shared account means a leak
  cannot be contained without re-provisioning every node.
- A shared secret or signature on the command would help, and it is the honest
  thing to say that this is not enough on its own: the node would have to keep
  that secret in the same flash a backup hands out, and it protects the *content*
  of a command, not the fact that whoever holds broker credentials can replay
  yesterday's.

That is a lot of machinery to make a route safe that we do not need. Hence HTTP.

---

## Which settings may be written

**All of them, bar three.** The list is the full `handleSetCmd()` surface of
`src/helpers/CommonCLI.cpp` — twenty-eight parameters — not a curated safe
corner.

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
| `radio` | **Four** number fields — frequency, bandwidth, spreading factor, coding rate — each with its own range. One text box in which you must type `869.525 250 11 5` is exactly the box a typo turns into a lost node |
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

For a `semi_managed` target — the path that is designed but not built — the
transmitting node would be the monitor, running our firmware, so the table and
its validation apply before anything is radiated.

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

`get <key>` is the same read the daily settings sweep uses, so there is no second
code path that could disagree with the first.

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
| Writing settings to a `semi_managed` node over LoRa | **designed, not built.** Needs a state machine beside the settings sweep, and the node it exists for is the roof repeater — so it gets built against something touchable first |
| Writing to a node's WiFi and MQTT settings | **not offered here.** Those are ours, not MeshCore's, and they already have their own forms on the node's own admin page and the `wifi` CLI |
| Confirm-or-revert | **examined and rejected**, with the reasoning above |
| Automatic rights discovery | **rejected**, point-and-test-once instead |
| Cross-repeater comparison table | **built** — `/admin/compare`, chosen columns, deviations from the majority marked |
| Editing from that table | **built** — a pencil per editable cell opens one editor below the table, pre-filled. One editor rather than an input in every cell: twenty nodes by six columns is a hundred and twenty forms each with its own confirmation, and the confirmation is exactly what becomes unreadable. It calls the same `nodeconfig.write()` as the node page, so the risk classes, the bounds and the read-back apply unchanged |
| Bulk edit across several nodes | **not built, and gated by design**: plain-class parameters only, never the two heavier classes. Ten nodes in one click is also ten nodes lost in one click |
| Forced mesh transport for a node that has an IP path | **not built.** Needs the LoRa write path first, and that needs a relay that monitors the target |
| Telemetry polling without credentials | **researched, not built.** It works and yields more than expected — see above |

> While this is being developed, **JessaZH is not written to at all** — not a
> test `set`, not anything. It is reached only over LoRa, so a mistake there is
> not correctable, and it is also the reference case the design exists to serve.
> Write paths are tested against a node somebody can physically touch.

---

## See also

- [`admin.md`](admin.md) — the pages themselves: every field, every form, the
  ordering by irreversibility
- [`commanding.md`](commanding.md) — how the route and the level are computed,
  and every blocker value
- [`clocksync.md`](clocksync.md) — whether this machine may tell the mesh what
  time it is
- [`firmware-upgrade.md`](firmware-upgrade.md) — the upgrade mechanism end to end
- [`mqtt.md`](mqtt.md) — the topics, and the three words the site may publish
- [`firmware.md`](firmware.md) — building and flashing the node firmware
