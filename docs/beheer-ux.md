# Admin pages: layout and components

*[Nederlands](nl/beheer-ux.md)*

This document is the guide for the design of everything under `/admin`, and the
record of the choices behind it. It describes *how* an admin page is built; what
is on each page and why is in [`admin.md`](admin.md).

## Why this was needed

The admin pages grew by accretion. Every block was added the moment its feature
arrived, with the explanation that was needed at that moment, and with a form of
its own (`class="rowform"`: label, field and button on one line). Per block that
produced a well-considered piece; as a whole it produced a page of fifty cards in
a row in which state, explanation and action are mixed together. On the page of a
single node an administrator scrolls past thirty screens to reach the delete
block, and the button that switches a filter on looks the same as the button that
saves a name.

What was *not* wrong: the texts. They explain what the site does and does not
know about a device on a roof, and that is the core of this project. They stay.
What changes is *where* they stand and *how much* of them is on screen at once.

## Four layout principles

1. **Heading, state, action — in that order, in every block.** A block starts
   with what it is, then shows what the site *knows* (facts, no buttons), and
   only then offers what you can *do*. The explanation that belongs to an action
   stands with that action, not before it.

2. **What you rarely need is out of sight, not gone.** Long explanations go into
   a disclosure (`<details>`) with a summary that says what they are about. Rare
   and drastic blocks (firmware, delete, the audit trail) are collapsed at the
   bottom. Nothing disappears: the text is in the DOM, a screen reader reaches
   it, and without JavaScript it simply unfolds.

3. **The risk is in the shape.** The three risk classes the code already knows
   (`nodeconfig.RISK_PLAIN/WRITES/CUTOFF`, `pktfilter.risk_of`, the roles in
   `rbac.py`) each get one fixed appearance, the same everywhere. Whoever sets the
   clock sees the same amber edge as whoever writes a noticeable setting; whoever
   does something irreversible sees red and retypes the node's name.

4. **One form shape.** Every form has its label on the left, the field with its
   help text in the middle and the button on the right, on one grid for the whole
   admin site. On a narrow screen that folds to a single column. Field names,
   routes and hidden fields stay what they were; only the wrapping changes.

## Findability: the case of the users

The concrete proof that the layout fell short: the owner of this installation —
a server administrator — could not find where to create a second administrator.
It sat on `/admin/server`, section `#gebruikers`, behind a menu item called
*Server en site*, after the table of existing accounts, as three loose
placeholders on one line. And the menu did have a tab *Beheerders*
("administrators") — which was about something else: which node polls which
other node (monitors).

Two things were wrong, and both are put right:

1. **The names in the menu did not say what was behind them.** *Beheerders* is
   now *Monitors*, which is what it is. The tab *Server en site* is now *Server,
   gebruikers en site* ("server, users and site"), and as soon as you are on it a
   second bar appears with the sections of that page — the same shape as the
   Companions sub-bar. User management can thus be reached from any admin page
   in two clicks, without scrolling.
2. **The creation form was not a form.** It becomes a `.frm` with a label per
   field, the minimum of eight characters as help text next to the password, and
   next to the *server administrator* checkbox a note of what that role may do —
   and, in the same breath, what an ordinary user may do as long as nobody gives
   him a role on a node or node group: nothing. That sentence was already there,
   in a grey paragraph nobody read at the moment of creating an account; now it
   stands next to the checkbox.

What stays: the routes (`POST /admin/users`, `/admin/users/{id}/flags`,
`/password`, `/delete`), the field names (`username`, `password`, `is_superuser`,
`csrf`) and the permission gates (`server.gebruikers`, `server.instellingen`). The
rule that the last active server administrator cannot demote, disable or delete
himself also stays visible next to the table: that is not a footnote but the
reason those buttons are sometimes disabled.

## The components

All classes live in `app/static/style.css`, under *admin: componenten*. They use
only the existing colour variables, so the light theme follows automatically.

### Section navigation — `.secnav`

A row of anchors at the top of a long page, one per `<section id=…>`. Sticks to
the top while scrolling (`position: sticky`) and works without JavaScript: they
are plain `href="#…"` links. On a narrow screen the row scrolls sideways instead
of wrapping into five lines. A small script marks the section in view; without
that script the row is still a table of contents.

### Card — `.card`, with `.card-head`

Already existed. The head gets a fixed shape: title on the left, badges (risk,
state) next to it, and an optional one-sentence `.card-desc`. The paragraphs
that used to follow it move into a `.uitleg`.

### Fact list — `.kv`

The two-column *key — value* table found on many pages (identity, clock, storage,
ingest). One class so they share column width and line height everywhere, and on
a narrow screen the key sits above the value instead of beside it in a column
four letters wide.

### Form row — `.frm`

```html
<form class="frm" method="post" action="…">
  <input type="hidden" name="csrf" value="…">
  <label class="frm-label" for="x">Name</label>
  <div class="frm-field">
    <input id="x" name="name" …>
    <p class="frm-hint">What this field does, in one sentence.</p>
  </div>
  <div class="frm-actions"><button type="submit">Save</button></div>
</form>
```

A `grid` with three columns: label (fixed width), field (stretches), buttons (to
content). Several fields on one row (region: field + name + confirmation) share a
`.frm-row` inside `.frm-field`. Below 640 px the columns become rows. A `.frm`
without a label (just a button with an explanation) leaves the first column out
with `.frm--nolabel`.

The old `rowform` remains for the places where it fits — a row of loose buttons,
or forms *inside* a table cell — but it is no longer the default.

### Permission gate — `fieldset`

```html
<form class="frm" method="post" action="…">
  <input type="hidden" name="csrf" value="…">
  <label class="frm-label" for="x">Name</label>
  <fieldset class="frm-field"{{ recht('node.hernoemen') }}>
    <input id="x" name="name" …>
  </fieldset>
  <div class="frm-actions"><button type="submit"{{ recht('node.hernoemen') }}>Save</button></div>
</form>
```

Every admin form is *visibly* disabled for whoever may not perform the action:
the fields as well as the button, with the reason in the tooltip. The lock is on
the server (`require_perm` in every route) and was already closed; the gate in
the template was missing on 86 forms, and that is what the owner saw — an
operator who could fill in fields and only got a refusal after the click. A page
that is tidy but lies about what you may do is worse than the clutter before it.

The gate is `{{ recht('<action>') }}` on a `<fieldset>` around the fields: a
disabled fieldset disables everything inside it, without JavaScript, and its
`title` is the reason from `rbac.ACTIONS`. Which action belongs to which form is
not invented in the template but follows the `require_perm` of the matching
route; where the class depends on the entered *value* (`hops 05 0`, `hash 3`)
the screen gates on the class of the ordinary value and the server weighs the
value once more. At the top of the node page it says once which role the user
has on this node and what that means (`rbac.ROL_UITLEG`), so that not every
disabled button has to explain it separately.

`tests/test_rechtenpoorten.py` is the ratchet: it counts the forms without a
gate per template and fails as soon as one is added.

### Action — `.act` with a risk class

Already existed as `.act--read/--write/--danger`. Three classes are added that
sit on the risk classes of the code, so that the word in the code and the colour
on the screen are the same:

| Class | Code | Colour | Confirmation |
|---|---|---|---|
| `.risk-gewoon` | `RISK_PLAIN`, role *bediener* | blue (cyan) | none, or "costs airtime" as a tag |
| `.risk-merkbaar` | `RISK_WRITES`, role *technicus* | amber | checkbox or "ja" |
| `.risk-ingrijpend` | `RISK_CUTOFF`, role *beheerder* | red | retyping the node's name |

The existing `--read`/`--write`/`--danger` keep working as synonyms. The tag
(`.act-tag`) names the price in words: *costs airtime*, *writes to the device*,
*irreversible*. Colour is never the only carrier.

### Explanation — `.uitleg`

`<details class="uitleg"><summary>Why this works this way</summary> … </details>`.
For every explanation of more than two sentences that is not needed to perform
the action safely. What *is* needed to act safely — "this runs over WiFi and
falls away with the WiFi", "a clock can only go forward" — stays visible, as a
`.frm-hint` or as a warning (`.waarschuwing`).

### Tables — `.tablewrap` and `.stack`

Every wide table sits in a `.tablewrap` that scrolls sideways inside its card, so
the page itself never scrolls sideways. Tables with a form per row (`.cfgtable`,
the filter rules) additionally get `.stack`: below 640 px every row becomes a
small card with the column heading in front of each cell (`data-l` on the cell).
So "Parameter · Now · New value" stays legible at 375 px without the reader
tapping a cell at random.

### Buttons

Three kinds, always in the same place: on the right of the form row, or bottom
right in an `.act`.

| Class | Use |
|---|---|
| `button` (default) | the primary action of a form |
| `button.secondary` | a second choice next to it (cancel, refresh, fetch the list again) |
| `button.danger` | only in a `.risk-ingrijpend` block, and only with a confirmation |

The pills (`.pill.on/.off`) remain the on/off switches: one click, reversible,
no confirmation.

## Per page

### `node.html` — one node

The biggest change. The page gets a `.secnav` and is split into sections in
increasing irreversibility, with the rare things at the end:

| Anchor | Section | Contents |
|---|---|---|
| `#overzicht` | Overview | level, identity and versions, name |
| `#zichtbaarheid` | Visibility | the four switches, each with one sentence; the rest in an explanation |
| `#uitvragen` | Polling | the stored parameters, the two poll buttons, the poll schedule |
| `#instellingen` | Write settings | the three risk groups as tables with their risk class |
| `#pakketfilter` | Packet filter | what the node reports, and whichever write path exists |
| `#klok` | Clock | state and the sync button |
| `#eigen-api` | Management over IP | address, state, actions; rooms, sensor nodes, SNMP and bot each as a disclosure |
| `#kanalen` | Channels | the names for the channels (sensor node only) |
| `#alarmen` | Alerts | alerts and event push |
| `#ingrijpend` | Firmware and delete | collapsed; red |
| `#trail` | What happened | collapsed |

The status messages of an action just performed stay at the top, because that is
where the page looks after a click.

### `server.html` — Server and site

Same `.secnav`. The settings forms (`settingsgrid`) become `.frm` rows with each
field's bounds as help text. The user table gets a `.tablewrap`; setting a
password and deleting per user stay in the row, with the dangerous button as the
last column.

### `nodes.html` — the list

Was already card-based and good on a narrow screen. The three warning blocks
(`.pending`) become one *Attention* list at the top, so three yellow blocks do
not read as three outages.

### `firmware.html`, `monitors.html`, `discovery.html`, `compare.html`

Forms to `.frm`; the upgrade button in a `.risk-ingrijpend` block; the tables in
a `.tablewrap`.

### `companions.html`, `companion.html`, `senddm.html`

The command grid (`.cardgrid`) stays: many small actions side by side is the
right shape here. The radio-parameter card becomes a `.risk-ingrijpend` block
instead of a card with a loose red border in `style=`. The management block at
the bottom (edit, share link, HA, delete) gets `.frm` rows and delete comes last,
in red.

### `account.html`, `audit.html`, `login.html`

Small changes: `.frm` for the password form, `.tablewrap` for the tables, `.kv`
where it fits.

## What deliberately does not change

- **No field name, route, `csrf` or `confirm` field** changes. Every form POSTs
  exactly what it POSTed; only the wrapping differs. The tests on literal texts
  (`test_nodeconfig.py`, `test_pktfilter.py`, `test_beheerpaginas_renderen.py`,
  `test_rooms.py`) guard that.
- **No JavaScript that carries.** The section navigation, the disclosures and
  the forms work without a script. The script that marks the active section is
  decoration.
- **No new i18n keys.** Admin is Dutch only; see the head of
  `admin/_layout.html`.
- **No external fonts or libraries.** Everything in `style.css`, with the
  variables that were already there.
- **The texts.** Shortened where they said the same thing twice, moved into a
  disclosure where they are long, but not thrown away. A sentence that says why
  a clock cannot go back is not decoration.
