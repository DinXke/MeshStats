# Managing nodes from the site

*[Nederlands](nl/node-management.md)*

What the site can do to a node, which nodes it can do it to, and — the part that
takes the most words — what it deliberately will not do.

Reading a node is settled and has worked for a long time. This page is mostly
about **writing**: changing a setting from the site rather than from a serial
cable. Some of it is built, some of it is designed and explicitly not built yet.
Each section says which.

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

**Firmware upgrade is deliberately not part of the level.** A `full_managed`
node can accept a twenty-byte command over MQTT and still be unable to accept a
1.3 MB image, because those do not travel over the same thing. The site keeps it
in a separate key, `firmware.ota_route(rep)`, which returns `can` plus a
`blocker` in the style of `commanding.route_for`. Reasoning and the blocker
values are in [`firmware-upgrade.md`](firmware-upgrade.md).

**A capability that does not apply is shown disabled with its reason, never
hidden.** A button that vanishes leaves "why can I not do this here" unanswered,
and that question is exactly what somebody has at the moment they need the
button.

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
| Levels as an explicit concept in code and UI | **being built** — `level` / `level_why` on `commanding.describe()` |
| Firmware upgrade over HTTP, with checksum and rollback | **built**, see [`firmware-upgrade.md`](firmware-upgrade.md) |
| `ota_route()` as a separate capability key | **built** |
| Writing settings to a `full_managed` node with an IP path | **built** — firmware 1.14.0 `POST /api/cfg`: the whole CLI surface bar three, typed controls, risk-driven confirmation, read-back. Requires the node's management address to be filled in |
| Writing settings to a `semi_managed` node over LoRa | **designed, not built.** Needs a state machine beside the settings sweep, and the node it exists for is the roof repeater — so it gets built against something touchable first |
| Writing to a node's WiFi and MQTT settings | **not offered here.** Those are ours, not MeshCore's, and they already have their own forms on the node's own admin page and the `wifi` CLI |
| Confirm-or-revert | **examined and rejected**, with the reasoning above |
| Automatic rights discovery | **rejected**, point-and-test-once instead |

> While this is being developed, **JessaZH is not written to at all** — not a
> test `set`, not anything. It is reached only over LoRa, so a mistake there is
> not correctable, and it is also the reference case the design exists to serve.
> Write paths are tested against a node somebody can physically touch.
