# What the site shows, and what it can be told not to

*[Nederlands](nl/privacy.md)*

This site publishes things about radios that other people own. That is worth
being explicit about, because two different questions hide behind it: what may
be shown at all, and what an operator can switch off. This page answers both,
and names the places where the answer is "nothing can be switched off here, and
here is why".

---

## 1. Two kinds of node

**Tracked repeaters** are rows in the `repeaters` table. Somebody added them to
this installation on purpose: they have a page at `/r/<slug>`, a battery graph,
a neighbour table, and — when the firmware allows it — buttons that reach the
device. Every switch on this page is about them.

**Third-party nodes** are everything else on the map: the several hundred dots
that appear because their adverts were overheard. They have no row, no page and
no owner known to this installation.

What is shown about a third-party node is its key prefix, its name, its node
type and, when its advert carried one, its position. All four come out of the
**advert** — a packet the node broadcasts on an open, licence-free band,
unencrypted, roughly every few hours, to anyone within radio range. Nothing here
is obtained by asking, probing or correlating; the site writes down what was
transmitted to it, the same as any other receiver in the area.

That is the honest justification and also its limit. It explains why this
information is not secret; it does not by itself make collecting it in one
searchable place harmless. Two things follow from that, and both are
deliberate:

- **There is no per-node switch for a third-party node**, because there is
  nobody to grant it to. A form that let an anonymous visitor hide any node they
  liked would be a way to blank out somebody else's repeater, not a privacy
  control.
- **The way out is the radio, not this site.** A MeshCore node advertises its
  position only when it is configured to. A node that stops broadcasting
  coordinates stops feeding them here, and to every other receiver in range at
  the same time — which is the only place the choice actually holds. See
  [`protocol.md`](protocol.md) §1.3 for the advert's optional position field.

If you run this installation and someone asks you to remove their node, the
honest answer is that you can (delete the contact rows), that it will come back
the next time they advertise, and that the durable fix is on their device.

---

## 2. The three switches on a tracked repeater

On `/admin/repeaters/{id}`, section **Zichtbaarheid op de site**. All three are
per node, all three flip back with the same click, and none of them touch the
device or the stored data — they decide only what leaves the server.

| Switch | Column | Off means |
|---|---|---|
| Publiek | `is_public` | The node is not on the home page, `/r/<slug>` is a 404, and no public API route mentions it |
| Positie tonen | `show_position` | The site behaves as though it had never heard a position for this node |
| Naam tonen | `show_name` | The node is called by its address hash everywhere a visitor can look |

`show_position` and `show_name` are `INTEGER NOT NULL DEFAULT 1`, added through
`db.COLUMN_MIGRATIONS`. The default is not a formality: `ALTER TABLE ADD COLUMN`
gives existing rows the default, so a database that gains these columns on
upgrade shows exactly what it showed before. A privacy column that silently
took a repeater off the map on the morning after an upgrade would be a worse
fault than the missing column it fixed.

### Hiding the position

The state a hidden position produces is the same state as "this node has never
advertised a position", and that is on purpose. That state was already handled
everywhere: the link map counts such neighbours instead of dropping them, a hop
without a position leaves a gap in a drawn route, and the heat map refuses to
bridge over it. Reusing it means there is no second mechanism to keep in step
with the first.

Concretely, with `show_position = 0`:

- no dot on the live map, at any zoom level;
- no place on the link map of a repeater that lists it as a neighbour;
- no `sender_lat` / `observer_lat` on a packet, in the feed, in the archive or
  on a packet's own page;
- no coordinates and **no distance in kilometres** on a candidate behind an
  address hash — a distance is computed from two positions and would hand the
  same fact over in another unit;
- **no endpoint of a heat-map segment.** The node breaks the chain exactly as an
  ambiguous hop does, so traffic that really travelled over it is not counted
  into a line;
- no country. The country is derived from the coordinates (`db.set_country()`)
  and is nothing but a coarse form of them.

What stays: the name, every metric, the neighbour list, the SNR figures, the
node's page, and the key prefix.

### Hiding the name

The node is then called `0xNN` — `0x` plus the first byte of its key, upper
case. That string is not an invention for this feature: it is what the packet
list has always printed for a sender it cannot name (`static/app.js`), so a
reader does not have to learn that there are two kinds of nameless.

This covers the name wherever it is served, and there are three separate
sources for it: `repeaters.name`, `contacts.name`, and `neighbors.name` — the
last one being the name another repeater reports for it, which normally wins.
The archive's `name:` search field runs over the same masked column, so the real
name does not match as a query either; confirming a name to somebody who already
suspected it is still telling them.

The name the administrator typed stays in the database and stays visible in
`/admin`. Hiding it from the operator would mean they could not switch it back,
and could not tell which physical node a page full of buttons belongs to.

### What no switch hides

- **The key prefix.** It is in every advert the node transmits. Pretending this
  site could keep it secret would be a promise the device itself contradicts.
- **The slug in `/r/<slug>`.** It was derived from the name when the row was
  created and does not follow a rename. Deleting the node and letting it
  re-register under a different name is the only way to change it.
- **The fact that the node exists.** A hidden position and a hidden name still
  leave a public page with figures on it. To remove the node from the site
  entirely, use `is_public`.

---

## 3. What the packet filter says about other people's traffic

A repeater with a packet filter refuses to forward some of what it hears. It
counts what it refused, and since firmware 2.6.0 it counts it in some detail.
That detail needs its own section, because unlike everything in §1 it is not
derived from an advert. An advert is identity a node broadcast about itself; a
drop is something that happened to somebody else's packet.

The line runs between **a measurement of this repeater's behaviour** and **a
record of a particular person's traffic**.

| Data | Where | Why |
|---|---|---|
| Totals: dropped, passed, ACL-exempt, filter on/off | **public** | Public before 2.6.0 already. A repeater with a filter on stops forwarding other people's traffic, and the people who notice are precisely the ones who cannot log in |
| Drops per reason | **public** | Same: it describes the repeater |
| Drops per packet type, and per type × reason | **public** | The packet type of every message is already public on the packets page. "ADVERT was dropped 40 times on the hop limit" says something about this repeater, not about who sent those adverts |
| Rate-limit pressure: windows with traffic, windows where the limit bit, peak | **public** | A drop counter without a denominator is not a measurement. 12 in 4000 windows is a limit set generously; 12 in 14 is one cutting into ordinary traffic — and the number of dropped packets can be identical |
| The configured limit itself | **admin** | A rule, not an observation. Rule tables have sat behind the login since 2.3.0 |
| Blocked channel: hash and hit count | **public** | The hash is one byte of `sha256(channel_key)`, and that byte travels unencrypted in every group message on the air. Withholding it protects nobody, while "this channel is refused here, 900 times" is exactly what somebody needs who wonders why their traffic does not arrive |
| Blocked channel: the label | **admin** | Not an observation but the name *our* operator gave to *someone else's* channel. It carries nothing the hash does not, and publishing it would have the site repeat a judgement about a third party where it should report a behaviour of this node |
| Which packet was dropped: sender, timestamp, the individual event | **does not exist** | See below |

**No per-packet drop record is kept, at any access level.** The firmware counts;
it does not log. There is no table of refused packets, no sender attached to a
drop, no timestamp per event. That is not a gap waiting to be filled: a repeater
recording who it refused and when would be keeping a log of other people's
communications, which is a different kind of thing from counting its own
behaviour. The counters also do not survive a restart, so they say "since this
node last started" and never "ever".

That bounds what an operator learns from their own node too. The admin view adds
the rule values and the channel labels — their own configuration — and nothing
about individuals.

---

## 4. Saying so out loud

A node that disappears from a view because of a visibility choice is counted and
reported. This site treats a silent omission as a lie told slowly, and the rule
does not stop being true when the omission is one an operator asked for.

| Where | Field | Shown as |
|---|---|---|
| `/api/v1/repeaters/{slug}/map` | `hidden`, `hidden_names` | A second line under the map, separate from `unlocated` |
| `/api/v1/packets` (first call) | `hidden_nodes` | "N nodes do not show their position", next to the map hint |
| `/api/v1/packets/heatmap` | `hidden_nodes` | A footnote on the layer's tooltip, next to `capped` |

`unlocated` and `hidden` are counted apart and never merged. "No advert with a
location received yet" is a statement about the mesh; "this node does not show
its position" is a decision by a person. A single number covering both would
make the first sentence untrue.

---

## 5. How it is enforced

One view, `visible_contacts`, created in `db._migrate()` after the columns
exist. It is `contacts` with the name, latitude, longitude and country passed
through a `CASE` on the visibility of the tracked repeater behind that key
prefix. **Every public read path selects from the view; every ingest path
(`upsert_advert()`, `upsert_contacts()`, `set_country()`) still writes to the
table.** What the site knows does not change — only what it tells.

A view rather than a filter per endpoint, because a position reaches the outside
along six routes and a name along more, and six separate filters are six places
where the seventh route gets forgotten. The two paths a view cannot reach are
handled explicitly and in one place each: `db.public_name()` for
`repeaters.name`, and `db.NEIGHBOR_NAME_SQL` for `neighbors.name`.

`tests/test_zichtbaarheid.py` holds one test per endpoint proving a hidden
position does not come out, plus `test_standaard_verandert_niets`, which proves
the defaults leave every one of those endpoints exactly as it was.

---

## 6. Related reading

- [`security.md`](security.md) — the threat model this sits inside
- [`database.md`](database.md) — the columns and the view
- [`api.md`](api.md) — the endpoints and their fields
- [`admin.md`](admin.md) — the rest of `/admin`
- [`candidates.md`](candidates.md) — why a hidden position also removes a
  distance, and why the node stays in the candidate list
