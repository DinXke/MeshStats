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

1. The node publishes statistics itself **and** reports a `fw_meshstats`
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
| **Write CLI settings** | no | designed, bounded | designed, bounded |
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
never a pipe into the CLI. The reasoning is in `MeshStatsNet.h` and it is about
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
it is to reopen this decision on purpose, with the tier table below as the thing
that has to hold.

### If it were ever done over MQTT anyway

Then the broker configuration becomes load-bearing rather than hygienic, and
`mosquitto/acl.example` has to say so:

- The site's account is the **only** one with `write meshcore/+/cmd`. Node
  accounts get `write meshcore/<own-id>/#` and nothing else, so a compromised
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

Not a whitelist because whitelists are fashionable, but because the failure mode
is losing a node permanently. Three tiers.

### Tier 1 — safe

Cannot cut off reachability by any route. Offered on every node the site can
write to, with no extra confirmation.

`name`, `lat`, `lon`, `advert.interval`, `flood.advert.interval`, `af`
(airtime factor), `rxdelay`, `txdelay`

The worst outcome in this tier is a node that advertises less often or delays
differently. Both are visible in the statistics and both are correctable by the
same route that set them.

### Tier 2 — risky, behind an explicit confirmation that names the risk

`flood.max`, `flood.max.unscoped`, `repeat`, `allow.read.only`

These change how the node participates in the mesh. They do not sever the
management route by themselves, but they can change what the mesh looks like
enough that diagnosing the next problem gets harder. `allow.read.only` in
particular changes who may log in — including, potentially, us.

### Tier 3 — never remotely on a node reached only over LoRa

`freq`, `radio` (bandwidth / spreading factor / coding rate), `tx`, `role`,
`region.*`, and anything to do with WiFi.

Every one of these can take effect and, in the same instant, remove the only path
back. There is no acknowledgement that helps: the acknowledgement would have to
travel over the link the change just broke.

They may be offered on a node that has **two independent paths** — our firmware
with both an IP route and a mesh route, where breaking one leaves the other — and
even then behind the same confirmation as tier 2. On a `semi_managed` node they
are not offered at all.

### Validation happens on both sides, and they are not the same check

- **The server** validates type and range before sending, so a typo produces a
  refusal on a page instead of a packet.
- **The node doing the transmitting** validates again before it issues `set`.
  This is the check that actually protects the mesh, because the server's list is
  editable by whoever runs the site and the node's is compiled in.

For a `semi_managed` target the transmitting node is the monitor, running our
firmware — so the tier table and its validation live in `MeshStatsNet`, applied
before anything is radiated. The target is stock MeshCore and validates almost
nothing: `set` parses with `_atoi` and takes what it gets. **A node that accepts
a nonsense value is more dangerous than one that refuses**, and the stock
firmware is the accepting kind. That is precisely why the refusal has to happen
before transmission.

---

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

So the effort goes into the tier table instead, which prevents the change rather
than undoing it. A prevention that works on stock firmware beats a rollback that
only works where it is not needed.

---

## After a write, read it back

A write is never reported as a success on the strength of having been sent. The
site re-reads the parameter and shows what the node actually made of it.

This is the same discipline as the firmware upgrade, and for the same reason:
`publish()` reporting success means the broker took the bytes, and a `set` that
was transmitted means it was transmitted. Neither says anything about the value
now in the node. The existing settings sweep is the mechanism — one parameter, or
the whole table — so there is no second code path that could disagree with the
first.

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
| Writing settings | **designed, not built.** The route (HTTP to a reachable node, LoRa onward), the tier table, the both-sides validation and the read-back are settled; the endpoints are not written |
| Confirm-or-revert | **examined and rejected**, with the reasoning above |
| Automatic rights discovery | **rejected**, point-and-test-once instead |

> While this is being developed, **JessaZH is not written to at all** — not a
> test `set`, not anything. It is reached only over LoRa, so a mistake there is
> not correctable, and it is also the reference case the design exists to serve.
> Write paths are tested against a node somebody can physically touch.
