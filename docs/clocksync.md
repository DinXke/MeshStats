# Clock synchronisation

*[Nederlands](nl/clocksync.md)*

`server/app/clocksync.py` answers one question before it does anything: **may
this machine tell the rest of the mesh what time it is?**

[`mqtt.md`](mqtt.md#setting-the-clock) describes the message on the wire and what
the firmware does with it. This document is about the decision on the server
side.

## Why the site is the one that knows

A MeshCore node never sets its own clock. An ESP32 without a buffered RTC starts
at whatever the firmware baked in — MeshCore's `clkreboot` puts it literally on
15 May 2024 — and drifts from there. A repeater on a roof restarts by itself:
flat battery, watchdog, a power cut in thunderstorm season. Every time it comes
back with a clock that has nothing to do with today, and everything it says
afterwards carries that time.

Nobody on the mesh can correct that, because nobody on the mesh knows better.
This machine does, because it sits on a network with an NTP client. That is the
entire reason this module exists — and immediately its only weak spot: the claim
"we know what time it is" has to be true before it is sent out.

## Why the checks are so strict

The correction goes one way and cannot be undone. The firmware only ever moves a
clock **forward**, and that is not a quirk of ours: an advert carries the clock
of the node that sent it, and every node that already knows the sender throws
away an advert whose timestamp has not increased (`onAdvertRecv` in
`MyMesh.cpp`). Setting a clock back an hour is an hour of invisibility for a
repeater on a roof. So the firmware never corrects backwards — and therefore a
time too far in the **future** is a mistake you cannot repair without going
there in person.

One bad publication from this module smears a wrong clock across every node
hanging off it, and the way back runs over a roof. Hence: when in doubt do not
publish, and say loudly why not.

## What can actually be established

Being honest about the reach of these checks matters more here than a green
tick.

### The main check: `adjtimex(2)`

`kernel_clock()` reads the kernel's time discipline through `adjtimex` with
`modes=0` — a read call; `adjtimex` *with* modes would steer the clock, which we
have neither the rights nor a reason for. This is exactly where `timedatectl`
gets its `NTPSynchronized`: the `STA_UNSYNC` flag in the status field, plus the
error margin the kernel maintains itself. It needs no privileges, no extra
package and no `timedatectl` inside the container — which a slim Python image
does not have anyway.

`ok` is true only when **both** signals are clean, because they do not say the
same thing:

| Signal | What it is |
|---|---|
| `STA_UNSYNC` clear, and `rc != TIME_ERROR` | The late verdict: the kernel has not given up |
| `maxerror ≤ MAX_ERROR_S` | The early verdict: the margin is not yet growing unchecked |

Waiting for the late verdict alone would mean carrying on for hours with a clock
the kernel itself is no longer sure of. The kernel lets `maxerror` grow at
500 ppm between NTP corrections and gives up at 16 s, so the default of **10 s**
is roughly "the host was still being corrected within the last five and a half
hours" — strict enough to catch an NTP client that stopped this afternoon, wide
enough never to touch a normal poll cycle of up to 1024 s.

**Where that verdict comes from matters.** This app runs in a container inside an
LXC container on a Proxmox host. An LXC shares its host's clock and may not set
it; `timedatectl` inside the LXC reports `NTP=no` (no NTP client here) next to
`NTPSynchronized=yes` (the kernel *is* disciplined). What is read here is
therefore the **host kernel's** judgement, passed through. That is the best
signal available from this position, but it is a relayed claim and not an own
measurement: "the host says it is in sync" is not the same as "the time is
demonstrably correct". The admin page says it in those words and not in more
reassuring ones.

The practical consequence belongs in any report about this feature, not only
here: **the correctness of every clock in this mesh ultimately hangs on the NTP
configuration of the Proxmox host.** If that runs wrong, all of this runs neatly,
measurably and completely wrong along with it.

Anything that makes `adjtimex` unavailable — not Linux, no libc, a kernel that
does not offer it — is a **refusal**, never a "probably fine". That includes
Windows, where `ctypes.CDLL(None)` raises `TypeError`; this never runs in
production there, but it does when somebody starts the tests or the app locally,
and then the answer should be "not available" rather than a stack trace.

### The second check: does the wall clock jump?

`_jump_check()` costs nothing and does not take the kernel at its word. The wall
clock and `time.monotonic()` should advance at the same rate. If the wall clock
shifts while the monotonic clock does not, time was **set** rather than elapsed.
That is allowed — an NTP client is supposed to steer — but a correction should be
small. A jump of an hour is something else, and then the question is which of the
two sides was right; we cannot answer that, so we do not publish.
`MAX_JUMP_S` defaults to 30 s, deliberately generous: a daily half-second
correction is healthy behaviour, not an alarm.

The reference pair lives **per process** and is not written to disk. A monotonic
clock means nothing after a restart, so persisting it would produce a comparison
that only looks convincing. The pair is always advanced, including after a
rejection — otherwise every following round would report the same jump again and
the feature would stay off forever after one correction.

### The third check: has time gone backwards?

`_backwards_check()` does survive a restart. The highest wall-clock time this
site has ever seen is stored in `settings` under `clocksync_high_water`. It
catches the case where the host boots without a network, sets its clock from the
RTC or the build date, and NTP has not been past yet — while `adjtimex` may well
be perfectly content. The margin is `MAX_JUMP_S`, because this is a threshold and
not a measurement: a few seconds back is an NTP correction, a day back is a clock
that started over.

### Two checks that were rejected

**Cross-checking against the mesh.** The suggestion is obvious — timestamps do
arrive from nodes — but the reasoning is circular: the nodes we would check
against are precisely the nodes that get their time from us. Finding them in
agreement would prove only that our own message arrived. On top of that the `rx`
message carries `t` as an uptime counter and not as a wall clock, so the usable
source is not even there.

**Asking an external time source.** This server sits behind VPN/LAN and has no
outbound route to an NTP server, nor an HTTP `Date` header that proves anything.
A check that works in the development environment and always says "unreachable"
on the real machine is a check that gets switched off after a week.

## Who gets the message

Two routes, and the difference is why `time_route()` exists next to
`commanding.route_for()`:

| Case | Message goes to | What that node then does |
|---|---|---|
| The repeater publishes itself | To itself | Sets its own clock, then walks its monitor list |
| The repeater is relayed (the roof repeater) | To its **monitor** | Sets its own clock and checks the clocks of **all** the nodes it monitors |

There is no argument to narrow the second case down, and that is not an
omission: the firmware walks the whole list on a clock round, because the round
is cheap per node and one round trip per monitored node. The page should say so
rather than pretend the button points at this one repeater.

`allow_monitor=False` excludes the second case. That is what the daily round
needs: it walks *all* repeaters, and two relayed repeaters sharing one monitor
would otherwise be sent the same message twice.

`time_route()` returns a `blocker` and a `why` for every refusal, so the admin
page can explain why a repeater is missing instead of quietly leaving it out:

| `blocker` | Meaning |
|---|---|
| `relayed` | Gets its time from its monitor, over LoRa (only with `allow_monitor=False`) |
| `no_source` | Does not publish over MQTT at all |
| `http_source` | Arrives through the HTTP API, not over MQTT |
| `relay_unknown` | The relaying node is not itself a known repeater here |
| `no_fw` | Module version unknown |
| `old_fw` | Needs node firmware `MIN_TIME_VERSION` (1.10.0) or newer |
| `stale` | Nothing heard from that node for more than `NODE_STALE_SECS` (6 h) |
| `broker_down` | The site is not connected to the broker right now |

**The recipient's firmware is what counts**, and for a relayed repeater that is
the monitor's. The subject's version says nothing here — a node that does not
publish reports no version anywhere. The version boundary is 1.10.0 along *both*
routes, unlike `commanding.route_for()` where it depends on the route (1.8.0
direct, 1.9.0 via a monitor): it is the same recipient having to know the same
word. Folding the two into one function would mean `route_for` computing a
different version per command, which is exactly the kind of branch a wrong
button falls out of.

`NODE_STALE_SECS` here is 6 hours, wider than `commanding.NODE_STALE_SECS` (1 h),
because this is a refusal rather than a warning: publishing to a node that has
been quiet for a day costs nothing, but it fills the logs with promises.

## The daily round

`run_once()`, in this order, and the order is the whole function:

1. `check_clock()` — **before a single node is selected**, so there is no path
   along which a message leaves while the check was still to come.
2. If it fails: count a refusal, record the reason, log at **WARNING** — this is
   the state in which the feature falls silent, and falling silent quietly is
   what this project does not do.
3. If the broker is not connected: record that and stop.
4. `targets()` — every repeater, through the same `time_route()` the button
   uses, with the monitor route closed.
5. Publish per eligible node, reading `time.time()` **per node**.

A round over a handful of nodes takes milliseconds, so reusing one epoch would
barely differ — but it would mean the last node gets a time older than the
message itself, and that is precisely the kind of detail this file is about.

### Why daily

Clock drift on an ESP32 is slow: a few seconds a day, tens of seconds with a bad
oscillator or a hot attic. The firmware only corrects a monitored node from two
minutes of deviation, so asking daily is well over an order of magnitude more
often than needed to stay inside that threshold — and airtime is the scarce good,
not compute.

What the interval actually decides is something else: **how long a node that has
just restarted, with a clock from 2024, may walk around before somebody sets it
right.** A day is the upper bound we accept. Shorter would narrow those windows
without measurably affecting the drift, and it costs airtime on the roof every
time. The node guards its own side anyway: it does the LoRa half at most once an
hour, whatever arrives.

`FIRST_RUN_DELAY_S` is 300 s after startup. Short, but not immediate: the MQTT
connection has to exist and the nodes have to have reported in, or the first
round always strands on "no broker connection". It is also useful after a site
restart that followed a power cut — there is then a good chance the nodes have
just restarted too, with a clock from 2024.

`start()` additionally measures and logs once at startup **without publishing**,
so the log of day one already says whether this machine qualifies at all,
instead of only in five minutes — or never, if the first round strands on
something else.

## The button

`POST /admin/repeaters/{rid}/clocksync` → `clocksync.sync_now()`.

**It is not a second code path**, and that is the point. The clock check is
literally the same `check_clock()` the scheduler calls, and the sending runs
through the same `publish_command()` with the same window check on the epoch. A
button with its own way to the broker would have been a back door around those
checks, and the only visible sign of it would have been a wrong clock on a roof,
weeks later.

What the button does **not** redo is the drift threshold and the refusal for a
node that is running ahead. Those live in the firmware, next to the code that
measures and transmits, so they apply here automatically: this message is the
same message.

`sync_now()` returns an `outcome` the page turns into a sentence. Each case is
separate, because "nothing happened" has six different causes here and the user
can fix five of them:

| `outcome` | Meaning |
|---|---|
| `disabled` | `MM_CLOCKSYNC_ENABLED=0` |
| `no_route` | No route to this repeater; `blocker` and `reason` say which |
| `no_clock` | This machine failed its own clock check; `reason` is the check's own wording |
| `too_soon` | Within `MANUAL_MIN_GAP_S`; `wait_min` says how long |
| `failed` | Publishing did not leave this machine |
| `sent` | Published |

Order matters inside it: **the clock check comes before the wait**, not after. A
server that does not know what time it is should say so — including, and
especially, when the answer would otherwise have been "wait a bit". The other way
round, somebody would wait an hour only to be told it was never going to work.

### The wait, and the one exception

`MANUAL_MIN_GAP_S` is 3600 s, mirroring `MON_CLK_MIN_GAP_MS` in the firmware on
purpose. What it is and is not, because that matters:

It is **not** a safety measure. That one lives in the firmware, next to the code
that owns the radio, and it is absolute — clicking a hundred times yields at most
one LoRa round an hour there, whatever arrives on the `cmd` topic. The band
cannot be occupied with this button even if this rule were absent.

What it **is**: honesty in the button. Within the hour, publishing would only
make the node set its own clock — which the previous message has just done —
while the round along the monitored repeaters is skipped without the page seeing
any of it. Reporting "sent" while the half that matters does not happen is
exactly the promise `commanding.py` had to clear away.

The one exception is worth it: `_rebooted_since()` waives the wait for a node
that has restarted in the meantime. Such a node is on the date from its firmware
— precisely the state all of this was built for — while our own bookkeeping says
we sent it the time twenty minutes ago. The button would then say "wait another
forty minutes" exactly when waiting is the worst answer.

The uptime comes from the last statistics message and is therefore itself
already old, so the age of that message is added to it. Without that correction a
node that has been quiet for ten minutes would look ten minutes younger than it
is — and that is the direction that grants false permission.

## Bookkeeping

`clocksync_sent` in `settings` holds `{node_hex: epoch}` for the last time
message per node, bounded at `_SENT_MAX` = 50 keys (oldest dropped first).
Without it the manual button would report "sent" for a round of which the node
skips the expensive half.

`_publish_time(node, when)` uses the caller's `when` for **both** the message and
the note. That looks like a detail and was not: when this function read
`time.time()` itself, the bookkeeping held a different moment than what was sent
— invisible in production, where they differ by microseconds, but it also meant
the wait calculation in `sync_now()` reasoned about a different clock than the
one that wrote the note. One moment, one value.

Only a successful publication is recorded. A failed one must not make the button
claim for an hour that a sync just happened.

`status()` feeds the admin page:

| Key | Meaning |
|---|---|
| `enabled`, `interval_hours` | Configuration |
| `last_run`, `last_ok` | Last attempt, last **successful** publication |
| `last_result`, `last_reason` | What happened, and why nothing left if nothing left |
| `published`, `skipped`, `runs`, `refusals` | Counters; `refusals` are rounds that stranded on the clock check |
| `clock` | The last `check_clock()` result in full |
| `last_manual`, `manual_node` | The last manual sync, kept **separate** from `last_run`/`last_ok` |

The manual fields are separate because they are a different event: those two are
about the scheduler, this one about somebody pressing a button. Counting them in
one field would produce an admin page on which you cannot see whether the daily
round is still running.

## The message on the wire

`mqtt_ingest.publish_command(node, "time", epoch=...)` publishes
`time <epoch>` on that node's `cmd` topic: UNIX seconds in UTC, which is what
MeshCore's own CLI parses in the `time ` branch of `CommonCLI::handleCommand`
(`_atoi` of the rest of the line, straight into `setCurrentTime`).

The epoch is bounded at both ends, in `MIN_EPOCH` (2025-01-01) and `MAX_EPOCH`
(2100-01-01), the same limits as `CLOCK_MIN_EPOCH`/`CLOCK_MAX_EPOCH` in
`MeshManagerNet.cpp`. Checking on both sides is not duplicated work but the
cheaper of the two places: a node only ever moves its clock **forward**, so a
time too far in the future cannot be undone at the far end without standing next
to it with a cable. A mistake here should strand here.

An out-of-window epoch **returns False rather than raising**: that is the road a
broken server clock arrives by, and that is a state of the machine rather than a
bug in the call. The caller sees "nothing left" and can report it. A missing
epoch on `time` *does* raise, because that is a programming error and should
break while it is being written, not in production.

Nothing is retained and QoS is 0 — see
[`commanding.md`](commanding.md#qos-0-and-retainfalse).

## Configuration

| Variable | Default | Meaning |
|---|---|---|
| `MM_CLOCKSYNC_ENABLED` | `1` | `0`, `false`, `no`, `nee`, `off` or empty switches it off |
| `MM_CLOCKSYNC_HOURS` | `24` | Hours between two rounds, minimum 1 |
| `MM_CLOCKSYNC_MAX_ERROR_S` | `10` | Kernel uncertainty still believed |
| `MM_CLOCKSYNC_MAX_JUMP_S` | `30` | Wall clock versus monotonic, and the backwards margin |

Switching it off is a valid choice: whoever sets their nodes by hand, or does not
trust this server enough to calibrate a mesh on it, should be able to say so
without rolling back the firmware.

## Tests

`server/tests/test_clocksync.py` covers the three checks separately and together,
the route selection in both directions, the wait and its reboot exception, and
the epoch window.

## Related documents

| Question | Document |
|---|---|
| The `time` command on the wire | [`mqtt.md`](mqtt.md#setting-the-clock) |
| The other commands, and their routes | [`commanding.md`](commanding.md) |
| Where `clocksync_*` is stored | [`database.md`](database.md#settings) |
