# The packet filter

*[Nederlands](nl/packet-filter.md)*

A repeater forwards other people's packets. That is its job, and most of the
time you want it to keep doing exactly that. But a shared band has bad days: one
misconfigured node floods a channel, a client retries a message four hundred
times an hour, an advert storm eats the duty cycle a solar panel paid for. On
those days you want to be able to say *not that, not from here* without taking
the repeater off the air.

That is what the packet filter is. It sits in the forwarding decision of a
`simple_repeater` and drops packets that break a rule you set, counting every
drop by reason so the site can show you what it threw away.

**It is off by default, and it stays off until somebody turns it on.**

## Where the idea comes from

The design of this filter follows the behaviour documented by the
[Dutch MeshCore](https://github.com/Dutch-MeshCore/MeshCore) fork in
[`docs/packet_filter_reference.md`](https://github.com/Dutch-MeshCore/MeshCore/blob/dmc-dev/docs/packet_filter_reference.md):
the same six kinds of rule, the same `filter …` command family, the same
"disabled by default, direct-routed packets bypass it" starting point.

**No code was taken from that project.** This implementation was written against
the documented behaviour, not against their sources. Where their description
assumes something a stock repeater cannot do, this implementation says so and
does something else — see *Where this differs from the reference* below. The
licence reasoning is in [`contributing.md`](contributing.md#third-party-code).

## What it filters, and what it never touches

The filter is asked exactly one question, in exactly one place: `MyMesh::
allowPacketForward()`, the function MeshCore calls before it retransmits
somebody else's packet. So:

- **Packets addressed to this node are never filtered.** A login, a CLI command,
  a status request — none of those are forwarded packets, so none of them pass
  through the filter at all. You cannot lock yourself out of a repeater with a
  filter rule.
- **Direct-routed packets are never filtered.** A packet travelling along a
  path that already names this node is somebody's established route; dropping
  those breaks working conversations rather than curbing a flood. Only flood
  packets — the ones this node volunteers to spread — are filtered.
- **Packets involving a node in the access list are never filtered.** If either
  the destination or the source address hash of a packet matches a client this
  repeater knows (`setperm`), the packet goes through whatever the rules say.
  The people who are allowed to administer this node keep working while a filter
  is on.

## The six rules

Every rule is per packet type where that makes sense, because the types have
nothing in common: an advert every few hours and an ACK per message are not the
same traffic and cannot share a limit.

| ID | Type | ID | Type |
|---|---|---|---|
| `00` | `REQ` | `06` | `GRP_DATA` |
| `01` | `RESPONSE` | `07` | `ANON_REQ` |
| `02` | `TXT_MSG` | `08` | `PATH` |
| `03` | `ACK` | `09` | `TRACE` |
| `04` | `ADVERT` | `10` | `MULTIPART` |
| `05` | `GRP_TXT` | `11` | `CONTROL` |

**Hop count.** `filter hops <type> <max>` — a packet that already carries this
many path hashes is not forwarded again. Default 8 for everything except
`GRP_TXT`, which gets 32. `0` means *forward nothing of this type* and is
treated as a blanket block; see the risk tiers.

**Rate limit.** `filter rate <type> <limit> <seconds>` — at most `limit` packets
of this type forwarded per `seconds`. Per type, **not** per sender: a repeater
cannot tell senders apart for most types without decrypting, and pretending
otherwise would be a lie in a status line. `0` disables the limit for that type.
Defaults: 5/60s for most, 20/60s for `TXT_MSG` and `GRP_TXT`, 10/60s for
`ADVERT`.

The window is **fixed, not sliding**: the counter resets whole rather than
ageing out packet by packet. A burst straddling a boundary can therefore push up
to twice the limit through in a short span. That is a real inaccuracy and it is
the right trade on a node whose first duty is relaying: a sliding window needs a
timestamp per packet per type, which is memory spent on precision nobody is
using. Set the limit for the traffic you want, not for the ceiling you fear.

**Minimum path hash size.** `filter hash <1|2|3>` — packets whose path hashes
are smaller than this are dropped. Default `1`, which passes everything.
Raising it to `2` is a blunt instrument: it blocks every packet from a node that
has not moved to multibyte paths, which today is most of them.

**Blocked channels.** `filter channel add <label> <psk|hash>` — group text on
these channels is not forwarded. Only `GRP_TXT` is affected, up to 16 entries.
Read *Blocking a channel* below before using it; it does not work the way you
would guess.

**Malformed group messages.** `filter malformed on` — `GRP_TXT` packets whose
structure cannot be right are dropped. Off by default.

**Packet type.** `filter type <type> off` — do not forward this type at all.
This is the largest hammer in the box and is classified accordingly.

## Blocking a channel

A repeater has no idea what channels exist. All it sees in a group message is
**one byte**: the channel hash, which MeshCore computes as the first byte of
`sha256(channel_key)`. Not of the name — of the key.

So "block channel *X*" cannot be answered from a name alone, and this
implementation does not pretend it can. You give it either:

- the channel's **pre-shared key** (base64, as you would paste it into a client),
  from which the node computes the hash the same way MeshCore does; or
- the **hash byte itself**, as two hex digits prefixed with `#`, for when you
  have read it off a packet in the archive and do not have the key.

The label you type alongside it is for you and for the site. The node matches on
the hash.

**One byte of hash collides.** Roughly one channel in 256 shares a hash byte
with any other. Blocking a channel therefore blocks, on average, a small
fraction of unrelated group traffic as well, and there is no way for a repeater
to tell the difference without the key. That is a real cost of this rule and the
reason it is not the first one to reach for.

## Where this differs from the reference

Two places, both because the described behaviour assumes something a repeater
does not have.

**Channels are given by key or hash, not by name** — for the reason above.

**"Malformed" means structurally impossible, not semantically wrong.** The
reference describes validating a group message's timestamp, its text and its
UTF-8 encoding. All three need the plaintext, and the plaintext needs the
channel key, which a repeater does not hold. What this implementation checks is
what can be checked without a key:

- the payload is long enough to hold a channel hash, a MAC and one cipher block;
- the ciphertext length is a whole number of 16-byte cipher blocks.

A packet failing either was never a valid group message from any sender. A
packet passing both may still be nonsense — we cannot know, and the status line
says `structureel` rather than `geldig` so nobody reads more into it than that.

## The commands

Everything below works over the serial console, the telnet console **and the
mesh CLI**, like every other command in this firmware. That is deliberate: see
*The way back* below.

```
filter                       state, and how much has been dropped
filter on                    switch on
filter off                   switch off, keeping the rules
filter reset                 back to the defaults and off
filter types                 the type table above
filter hops                  the hop limits
filter hops <type> <max>     set one
filter rate                  the rate limits
filter rate <type> <n> <s>   set one
filter hash                  the minimum path hash size
filter hash <1|2|3>          set it
filter malformed [on|off]    the structural check on group text
filter type <type> [on|off]  forward this type at all, or not
filter channel list          the blocked channels
filter channel add <label> <psk|#hh>
filter channel remove <label|#hh>
filter count                 drops per reason and per type
```

Settings live in `/filter_prefs` on the node's own filesystem and survive a
restart. They are written lazily — a burst of changes costs one write, not ten,
because SPIFFS wears out.

## What it counts, and where those numbers go

Since firmware **2.6.0** the filter counts not only *how much* it dropped but
*what*: the cross of packet type × reason, the pressure on each rate limit
(windows with traffic, windows where the limit bit, the peak), ACL exemptions per
type, and hits per blocked channel.

**Which way the data travels is the design, not a detail.** All of the above goes
over **MQTT only** — WiFi or LAN, where bandwidth costs nothing. The settings
sweep and the mesh CLI stay exactly as lean as they were, because there every
byte is airtime on a shared band. The ~160-byte short form that rides in every
statistics message is unchanged; the breakdown follows it and costs about 130
bytes in practice, 2.6 kB in the fullest case imaginable. If it does not fit, the
node sets `"trunc":1` rather than dropping rows silently.

On the server the breakdown does **not** become metrics. Twelve types by six
reasons is seventy-two names, against a ceiling of 128 metrics per message and a
FIFO of 1000 rows per repeater — and it is a snapshot of a distribution, not a
series. It is stored in the existing `repeater_filter` JSON blob: one row per
repeater, which by definition cannot grow. Only the rate-limit pressure becomes a
series, as `filter_rate_windows` and `filter_rate_capped`, because there the
trend is the question.

Where to see it: the **Filter: uitsplitsing** block on a node's public page. What
is public and what sits behind the login — and why — is in
[`privacy.md`](privacy.md#3-what-the-packet-filter-says-about-other-peoples-traffic).
In short: counts and distributions describe this repeater and are public; the
configured limits and the channel labels are operator's tooling and are not; and
there is no per-packet record of who was refused, at any access level.

## Seeing it in the packet archive

A refused packet does not disappear — it was already in the archive before it was
refused. The raw feed hangs off `logRxRaw()`, which fires at *reception*, while
the filter decides at *forwarding*. What was missing until firmware **2.7.0** was
only the note saying that it was not forwarded, and why. There is no second log:
the firmware still counts and does not keep a list of packets.

Each archived packet now carries one of three states:

| State | Meaning |
|---|---|
| `geweerd` | this observer's filter refused to forward it, with the reason |
| `doorgelaten` | this observer's filter judged it and let it through |
| *blank* | the filter never judged it |

The blank is a finding, not a rounding of `doorgelaten`. The filter only ever
judges flood packets it is asked to forward, so a packet addressed to the node, a
direct-routed one, or a frame that never parsed is genuinely unjudged. Rows from
before 2.7.0 are blank permanently — the verdict is not in the bytes, so no
re-decode can recover it.

Searchable with the ordinary query language, so the plus/minus buttons and the
column picker work on it like any other field:

```
filter:geweerd              everything this mesh refused to forward
filter:geweerd reden:rate   ... because of a rate limit
-filter:geweerd             everything else
reden:kanaal observer:e3d3  one node's blocked-channel drops
```

Refused rows are coloured, and the colour is never the only carrier: the cell
spells out `geweerd` with the reason next to it, in both themes. The packet's
detail panel names the reason in full **and the node that applied it** — a packet
is refused by one repeater, not by the mesh, and without the name it reads as the
latter.

The front page carries the mesh-wide sum: refused, forwarded, and the breakdown
by reason, with how many repeaters are counted out of how many exist. See
[`privacy.md`](privacy.md#31-the-per-packet-mark-and-a-correction) for why this
is public and what is deliberately not recorded.

## The way back

A filter is the kind of setting that makes a node useless without making it
unreachable. It still answers, it still advertises, it still shows up green on
every page — and it quietly forwards nothing. You find out when somebody
complains.

Three things guard against that.

**`filter off` and `filter reset` are always reachable over the mesh CLI.** They
do not need WiFi, they do not need the admin page, they do not need the server.
A node whose filter was set wrong from the site is fixed with one command over
LoRa. They are also the *cheapest* actions in the permission model — a role that
may not switch a filter on may still switch one off. Recovery must never be
gated more tightly than the mistake it undoes.

**The site shows an active filter everywhere the node appears.** Not buried on a
settings page: the node panel, the comparison table and the node's own API
response all carry the filter state, so "this node has a filter on" is visible
to someone who was not looking for it.

**Every drop is counted, by reason and by type**, and those counters ride along
with the ordinary statistics message. The site records them as metrics like any
other, which means they graph, they age out with the same retention, and a
filter that started eating real traffic shows up as a line going up rather than
as an absence you have to notice.

## Managing it from the site

See [`node-management.md`](node-management.md#the-packet-filter) for the
walkthrough and [`admin.md`](admin.md#packet-filter) for every field. In short:

- The filter panel on a node's admin page reads the live state from the node
  (`GET /api/filter`) and writes through a single endpoint (`POST /api/filter`).
- The **firmware owns the rules**. The server sends the command string and shows
  what came back; it does not keep its own idea of what a valid limit is beyond
  refusing an obvious typo before it costs a network round trip. Same division
  as the CLI settings writer — see [`admin.md`](admin.md).
- Three risk tiers, the same three the rest of this site uses:

| Tier | Actions | Confirmation |
|---|---|---|
| `gewoon` | `off`, `reset` | none |
| `merkbaar` | `on`, and any rule that narrows traffic without blocking a category outright | type `ja` |
| `ingrijpend` | `hops … 0`, `type … off`, `hash 3`, and switching on while such a rule is already set | type the node's name |

The tier is decided from the action and its arguments, on the server, before
anything is sent — and again in the confirmation check, so a hand-built form
cannot skip it.

## Managing it from the node's own page

Since firmware **2.5.0** the whole filter is also on the node's own admin page
(port 80, behind the same login), under *Packet filter*. That matters more than
convenience: this page is the road that remains when the server, the internet or
the broker is gone, and this project exists partly for emergency communication.
The site can do all of this, but the site stands between you and the device; the
node's own page does not.

It reads `GET /api/filter` and writes `POST /api/filter` — the same two endpoints
the site uses, so there is one parser and one grammar, not a second one that will
disagree on the day it matters. The page shows the on/off state, the safe-mode
disarm, the minimum path hash, the structural check, all twelve packet types with
their hop limit, rate limit and window, the blocked channels, and the counters per
reason plus passed and ACL-exempt.

Two things are worth spelling out, because they differ from the site:

- **There is no text box for a command line.** Every button carries a fixed verb
  and reads its numbers from inputs that are bounded to what the firmware accepts
  (`0–63` hops, `0–65535` per `1–3600 s`, hash `1|2|3`). A free-text field would
  be a CLI on a web page, and then the weight of an action depends on how somebody
  happens to spell it.
- **The confirmation is in the browser, not in the node.** `POST /api/filter`
  takes no confirmation field — the server writes down the same path and would
  trip over one. The node's own protection is unchanged and unconditional: the
  parser rejects out-of-range values, and the answer carries the state *after the
  fact* so the page shows what is being enforced rather than what was typed.

The tiers are the same three, decided from the direction of the change and what it
lands on top of — the same rule as `pktfilter.risk_of()` on the server:

| Tier | Actions on the node's page | Confirmation |
|---|---|---|
| ordinary | `off`, `reset`, `type … on`, `hash 1`, `malformed off`, `rate … 0`, `channel remove` | none at all |
| noticeable | `on`, `hops … n>0`, `rate … n>0`, `hash 2`, `malformed on`, `channel add` | a confirmation naming the action |
| far-reaching | `hops … 0`, `type … off`, `hash 3`, and `on` while such a rule is already set | retype the node's name |

`off` and `reset` therefore ask **nothing at all** here either — one click. That
is not sloppiness in the classification, it is the point of it: recovery must
never be gated more tightly than the mistake it undoes.

Each confirmation states the action as a sentence rather than as a command
(*"stop forwarding GRP_TXT (05) entirely (0 hops)"*, not `hops 05 0`), for the
same reason the site does it: a dialog showing `hops 05 0` asks for a yes on
something the reader has to decode first, and that is exactly the moment somebody
clicks yes without reading.

None of this replaces the way back. `filter off` and `filter reset` over the mesh
CLI need no WiFi, no admin page and no server, and 2.5.0 changed nothing about
that.
