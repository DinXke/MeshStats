# Naming a node from one byte

*[Nederlands](nl/candidates.md)*

A packet's source, its destination and every hop in its path are named by the
first byte or two of a public key. One byte has 256 values while this site
already knows several hundred nodes, so several nodes answering to the same hash
is the **normal case, not a data error** (see
[`protocol.md`](protocol.md#14-the-path-field) §1.4).

`server/app/candidates.py` decides what may honestly be said about that. It is a
pure module: no I/O, no database handle, an injectable clock.

## Three things it does, and one it refuses

**Exclusion.** A candidate the frame itself places outside any plausible radio
range is dropped. This is the only way a candidate disappears, it needs a bound
the frame really carries, and the caller is handed the dropped ones so the
reader can be told how many fell away and on what ground.

**Ranking.** The survivors are ordered best first on three coarse, nameable
signals in a fixed priority: how close in hops this observer has actually heard
the node, how far away it is, and how recently it was seen.

**Naming the leader — but only when the evidence separates it.**

**Never:** naming a winner when the evidence does not separate the top two. A
ranking that cannot rank is reported as a tie and the caller falls back to "N
possible". Flipping a coin and printing the result as "most likely" is the one
thing this project does not do.

## The four states

`weigh()` returns `{"state", "matches", "dropped", "lead"}`.

| `state` | Meaning | How the site draws it |
|---|---|---|
| `known` | One candidate stands — either the only match, or the last one left after an exclusion | A name, and a position on the map |
| `likely` | Several stand, and the evidence puts one above the rest; `lead` names the signal that did it | The name in words, with its reason. **No line on the map** |
| `ambiguous` | Several stand and nothing separates them | "N possible", all of them listed |
| `unknown` | Nothing stands: no contact matches, or every one that did was excluded | A gap |

`known` is still a derivation from one byte, and the wording never states it as
an identity.

`likely` sits deliberately on the **uncertain** side of the map's line. A
ranking is good enough to name a probable node in words next to the reason it is
probable; it is not good enough to draw a line on a map, where the reason does
not travel with it. `routes_api._hop_waypoint()` is where that line is drawn:
coordinates are handed out for `known` and for nothing else.

## The bound: which end of a packet its hop count constrains

Everything turns on one asymmetry that is easy to get backwards.

> On a **FLOOD** the path is the route **already travelled**: every forwarder
> appended its own hash, so the frame counts backwards to the originator.
> On a **DIRECT** the path is the route **still to travel**: the frame counts
> forwards to the destination.

So a flood bounds where a packet **came from** and a direct bounds where it is
**going**, and neither bounds the other. `radio_hop_bound(role, route,
path_len, index)`:

| `role` | FLOOD | DIRECT | Reasoning |
|---|---|---|---|
| `src` | `path_len + 1` | *(none)* | The originator sits `path_len` forwarders back, hence that many links plus one from here. Zero hops means we heard the sender's own transmission: it is inside our radio range, full stop |
| `dest` | *(none)* | `path_len + 2` | `path_len` forwarders still to go and then the destination, and the node we just heard transmit is itself one link from us |
| `hop` at `index` | `path_len - index` | `index + 2` | On a flood, `path_len - 1 - index` forwarders came after it. On a direct it has yet to forward: `index + 1` links past the node we heard |

Note what the flood case does **not** cover: a flooded packet's destination may
sit anywhere in the mesh, however few hops the packet has travelled so far. A
zero-hop flood tells you where it started, not where it is headed. That is why
the case this was built for — a flooded packet's destination — keeps all its
candidates and merely orders them.

`None` means "no bound", and no bound means **no exclusion**. That is the safe
direction: the ranking still runs.

Because getting the two roles the wrong way round would exclude the innocent,
`routes_api` has a separate `_resolve_src()` and `_resolve_dest()` rather than
one call with a role passed in from outside. Neither caller may choose.

## Exclusion, and the measurement that overrules it

```python
if (bound and km is not None and km > bound * MAX_RADIO_HOP_KM
        and (hops is None or hops > bound)):
    dropped.append({**entry, "why": "range", "bound": bound})
```

`MAX_RADIO_HOP_KM` is **120 km**, and it is a rejection threshold rather than a
model of coverage — set far above anything a normal mesh produces rather than at
the middle of the distribution. On the deployment it was measured against, the
furthest node ever heard at zero hops sat 24 km away and the furthest at one hop
51 km. 120 km leaves a factor of five over the first and still rules out the
cross-border neighbours that were turning up as candidates for locally-heard
traffic.

Terrestrial LoRa does reach further than that over water or from a hilltop,
which is precisely why the exclusion is **waived for any node this observer has
really heard at that hop count**. Coverage that beats the threshold is a fact
about the world; the threshold is only a stand-in for one, and a measurement
always wins.

Dropped candidates travel back to the caller with `why` and `bound`, and the API
passes on a count (`dropped_total`), so a row can say how many fell away rather
than presenting a narrowed list as the whole truth.

## Ranking: bands, in a fixed order

The sort key is a 4-tuple, compared left to right:

```python
(_heard_tier(hops), _band(km, DISTANCE_BANDS_KM), _age_band(seen, now),
 (name or prefix).lower())
```

### Hop tiers

| Tier | Condition |
|---|---|
| 0 | Heard by this observer at fewer than `HEARD_TIER_LOCAL` (2) hops |
| 1 | Heard at fewer than `HEARD_TIER_NEARBY` (4) hops |
| 2 | Heard, but further out |
| 3 | `TIER_UNHEARD` — never heard by this observer at all |

Zero and one hop are one tier on purpose: a node whose advert reached us direct
and one whose advert came via a single repeater are both "in this corner of the
mesh", and splitting them would rank on the accident of which copy of a flooded
advert happened to arrive first.

### Distance and recency

`DISTANCE_BANDS_KM = (25, 75, 200)` and `RECENCY_BANDS_S` = today, this week,
this month, older. Wide bands, because the point is to separate "in the
neighbourhood" from "in another country", not to rank two nodes that are 30 and
34 km out.

`_band()` gives a missing value its own band **one past the last**, so an
unplaced or never-seen node sorts below every node we can actually place or
date. Not knowing where something is is a worse reason to put it first than
knowing it is far away.

### The observer is not a candidate that has to have been overheard

```python
if observer6 and prefix[:6] == observer6:
    hops, km = 0, 0.0
```

It is the node doing the listening. Zero hops and zero distance are facts about
it, and the evidence table would only say so by the accident of one of its own
adverts coming back through the mesh.

### Why bands and not a score

A weighted score with decimals would separate every pair of candidates —
including the pairs the evidence does not actually separate. And the tie is the
interesting case here, because it is the one where the honest answer is still
"several possible".

Three bucketed signals in a fixed order can also be read out loud: *heard
closer, then nearer, then more recently.* Nobody can retell a formula.

### Where the leader comes from

```python
top, runner = scored[0][0], scored[1][0]
lead = next((name for i, name in enumerate(LEAD_SIGNALS)
             if top[i] != runner[i]), None)
state = "likely" if lead else "ambiguous"
```

The list is sorted, so something is always first — but if the top two differ
nowhere among the three signals, the only thing separating them is the
alphabetical tiebreak in the sort. That is not evidence and must not be dressed
up as a ranking. `LEAD_SIGNALS` is `("hops", "distance", "recency")` and those
names are the vocabulary the front end translates, so keep them and the i18n
keys in step.

## What the signals are worth

`hops` is the strong one, and it is the only signal that is **evidence rather
than geography**: an ADVERT names its sender by full key prefix, so "this exact
node has been heard by this exact observer, at this many hops" is a measurement.
It comes from `db.observer_receptions()`, which is restricted to adverts and to
FLOOD packets for exactly that reason — feeding ambiguous data into the thing
that resolves ambiguity would be circular.

Distance and recency are **priors**, and they are used only to order what the
hop evidence leaves level.

`seen` falls back to `contacts.updated` when this observer has never heard the
node itself: a contact pushed by Home Assistant has a date but no reception.

## How the server calls it

`routes_api._resolve_hop()` supplies the observer context and the bound:

```python
weighed = candidates.weigh(
    [...db.contacts_by_key_prefix(hop_hash)...],
    evidence=ctx["evidence"],      # db.observer_receptions(observer)
    observer6=ctx["prefix6"],
    observer_pos=ctx["pos"],       # db.contact_location(prefix6)
    bound=candidates.radio_hop_bound(role, route, path_len, index),
)
```

`db.contacts_by_key_prefix()` returns a **list**, never a single row, because a
path hop identifies a node by only its first one or two key bytes. Callers must
present that as the ambiguity it is. It refuses anything that is not 1–6 hex
characters.

Both context lookups are memoised for a minute, together — see
[`api.md`](api.md#hop-resolution-and-its-caches).

`_trim()` reduces a resolution to what a list row needs: names, `hops`, `km`,
the `lead`, and the counts the reader has to be told about. Coordinates and
timestamps go; `total` survives the trim so a row can still say how many
candidates there were even when it prints only the first six.

## Where the honesty rule is applied elsewhere

**The drawn route** (`GET /api/v1/packets`, `path[]`): coordinates only for
`known`. Anything else is a guess-free gap.

**The heat map** (`GET /api/v1/packets/heatmap`): an uncertain hop breaks the
chain rather than being bridged. A single packet's route can afford a dashed
guess across such a gap; on a heat map the guess would be counted and recounted
into a solid, authoritative-looking line. The heat map resolves hops
**without** an observer or a route, deliberately — it only ever uses a `known`
resolution, so a ranking would change nothing there.

**The node panel** (`GET /api/v1/nodes/{prefix}`): `as_hop.packets` is a ceiling
and `as_hop.siblings` says how much of one — how many known nodes share this
node's first key byte. Counted over `contacts` rather than assumed to be 256:
what matters is how many nodes this site could actually confuse with each other.

**The live map filter**: every node on a displayed route is shown at full
strength while the detail panel is open, including hops the filter excludes. A
gap in a drawn path already means something precise — "we cannot tell which node
this was" — and the filter must not be able to imitate that.

## Tests

`server/tests/test_candidates.py` covers the bound in both route directions, the
exclusion and its measurement waiver, the tier and band boundaries, and the tie
that must stay `ambiguous`.

## Related documents

| Question | Document |
|---|---|
| Where the hashes come from | [`decoder.md`](decoder.md#address-hashes-per-payload-type) |
| The evidence table behind `hops` | [`database.md`](database.md#flood-only-hop-counts) |
| Which endpoints return these states | [`api.md`](api.md) |
| The protocol rule the bound rests on | [`protocol.md`](protocol.md#14-the-path-field) |
