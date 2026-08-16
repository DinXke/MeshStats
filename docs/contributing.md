# Contributing and working conventions

*[Nederlands](nl/contributing.md)*

This is not a style guide. It is the set of decisions that explain why the code
in this repository looks the way it does — why there is no build step, why the
comments are longer than usual, why some functions refuse to answer, and why the
commit messages are in Dutch.

Read it before a first change. Most of what follows will look like extra work,
and each rule is here because its absence cost something.

---

## Contents

- [The short version](#the-short-version)
- [1. Honesty about uncertainty](#1-honesty-about-uncertainty)
- [2. Comments carry the why](#2-comments-carry-the-why)
- [3. Commit messages](#3-commit-messages)
- [4. Language](#4-language)
- [5. Vanilla JavaScript, no build step](#5-vanilla-javascript-no-build-step)
- [6. Additive SQLite migrations](#6-additive-sqlite-migrations)
- [7. `packets.raw` is the ground truth](#7-packetsraw-is-the-ground-truth)
- [8. Tests pin down the refusals](#8-tests-pin-down-the-refusals)
- [9. Tooling, or the lack of it](#9-tooling-or-the-lack-of-it)
- [10. Documentation conventions](#10-documentation-conventions)
- [Submitting a change](#submitting-a-change)

---

## The short version

| Rule | In one line |
|---|---|
| Never guess in public | If the data does not separate two answers, say so instead of picking one |
| Never fail silently | A dropped request, an ignored clause, a skipped exclusion: all of them get said out loud |
| Comments explain why | What the code does is readable; why it does that and not the obvious alternative is not |
| Commits are Dutch, and carry the reasoning | The subject is the user-visible change; the body is the investigation |
| No build step | Vanilla ES5, Jinja2 templates, CDN libraries |
| Migrations are additive | `CREATE TABLE IF NOT EXISTS` plus guarded `ADD COLUMN`. Nothing is ever dropped |
| `raw` is the truth | Derived columns are a cache; the frame is the record |
| Cite your source | Firmware behaviour gets a file and a line number in the comment |

---

## 1. Honesty about uncertainty

This is the project's central rule, and it is enforced in code rather than
assumed. Every other convention here is downstream of it.

The mesh does not give unambiguous data. A hop entry is one byte — 256 possible
values across hundreds of nodes. A truncated frame may be a bug or may be a real
radio artefact. A packet's region cannot be recovered from its bytes. The
temptation in each case is to pick the likeliest answer and print it. This
project does not.

### The four states

`server/app/candidates.py` is the reference implementation. `weigh()` returns one
of four states, and the module docstring names the third as a thing it *refuses*
to collapse:

| State | Meaning | Rendering |
|---|---|---|
| `known` | Exactly one candidate | The node, named |
| `likely` | A ranked front-runner, with a reason | Named **in words**, with the reason attached |
| `ambiguous` | Several candidates the evidence does not separate | All of them, none preferred |
| `unknown` | Nothing matched | The raw byte, as `0xNN` |

> Naming a winner when the evidence does not separate the top two. […] Flipping a
> coin and printing the result as "most likely" is the one thing this project does
> not do.
>
> — `server/app/candidates.py`, module docstring

The `unknown` case is instructive. An early version printed only the word
"unknown", and that threw away the one fact the frame *did* give: the byte. It
now prints `0xNN`. Not knowing which node a hash names is different from not
knowing the hash.

### Bands, not scores

`weigh()` compares candidates in coarse bands in a fixed order rather than
summing them into a number. The docstring says why: a weighted score with
decimals separates *every* pair of candidates, including the pairs the evidence
does not actually separate. Precision that the input does not support is a lie
with a decimal point in it.

Missing values get their own band, past the last one — *not knowing* where
something is is a worse reason to rank it first than *knowing* it is far away.

### Where a ranking may and may not be used

`routes_api.py` draws the line explicitly. A `likely` hop is named in prose next
to its reason. It is **not** drawn on the map:

> A ranking is good enough to name a probable node in words next to the reason it
> is probable; it is not good enough to draw a line on a map, where the reason
> does not travel with it.
>
> — `_hop_waypoint()`, `server/app/routes_api.py`

A hop with several candidates gets a hollow ring on each of them instead of a
line (`server/app/static/app.js`). Showing the possibilities is honest; picking
one is not.

### Refusing to decode

`server/app/packets.py` stops at the first thing it cannot trust and reports what
was certain before it, with an `error` field. It does not continue past a bad
value, because a wrong hash size shifts every byte after the descriptor — carry
on and you invent a path, a payload boundary and an address hash in one go. A
path length read out of a byte we do not trust is, in the module's own words, *a
guess wearing a number's clothes*.

Related refusals in the same file: a truncated transport-code field leaves `scope`
absent rather than choosing between `scoped` and `share`; the region of a scoped
packet is never guessed at; a firmware-default position of 0/0 is treated as
unknown rather than plotted in the Atlantic.

`decode()` never raises. A corrupt packet is **data, not a bug**.

### Refusing to ignore

The mirror-image rule, in `server/app/search.py`:

> Nothing is ever silently dropped. An unknown field name, a comparison on a text
> column, a malformed range: each one is an error the page shows, never a clause
> quietly skipped. A search that ignores half of what you asked for while
> reporting a confident number of hits is worse than one that refuses to run.

The same instinct outside the server: the Home Assistant integration logs a loud
warning when it cannot execute a queued command, because the site's queue is
clear-on-read and a request dropped quietly exists nowhere any more
([`homeassistant.md`](homeassistant.md#loud-failure-is-deliberate)). The heatmap
sets a `capped` flag rather than showing a truncated week as a complete one.
Excluded candidates are counted and explained, never simply absent.

### What this asks of a contributor

Before adding a value to a page, ask what happens when the input does not support
it. If the answer is "we show the most likely one", the change is not ready. The
options are: show all of them, show it in words with its reason, or show the raw
fact and say what is unknown.

---

## 2. Comments carry the why

Comment density in this repository runs 13–24 % of lines, and module docstrings
run to over a hundred lines in places (`server/app/packets.py` opens with 154
lines before the first import). That is intentional.

The rule is not "comment everything". It is:

- **What the code does needs no comment.** It is readable.
- **Why it does that, and not the obvious alternative, does.** Especially when
  the obvious alternative was tried first.
- **A constant with a number in it gets its reasoning.** `server/app/config.py`
  records not just what a retention limit is but what it is a promise *about*,
  and what happens when the promise collides with reality.
- **An index gets a justification, and so does the sibling index that is
  absent.**
- **Firmware behaviour gets a citation**: file and line number in the MeshCore
  tree, so a later reader can re-check it against their own version. `packets.py`
  cites `src/Dispatcher.cpp` and `src/Mesh.cpp` by line; several modules
  cross-reference `docs/protocol.md` by section.

The test for whether a comment is worth writing: would someone reading this in a
year wonder why it is not done the simpler way? If yes, the answer belongs in the
file, not in the commit history and not in someone's head.

Comments that record a **reversal** are the most valuable kind. Three releases of
the TCP proxy exist purely to undo cleverness from earlier ones
([`proxy.md`](proxy.md#version-history-in-brief)); the code says so, which is why
nobody has re-added it.

---

## 3. Commit messages

Commits are in **Dutch**, and they are written for a person, not a changelog
generator.

**Subject line**: a sentence describing the user-visible change, not the
mechanism. Real examples from `git log`:

```
Een afzender die we niet kunnen benoemen krijgt zijn byte terug
De heatmap laat nu zien welke schakels het mesh dragen
Adreshashes: kandidaten wegen op bewijs in plaats van alles opsommen
Een nodenaam met een aanhalingsteken laat een node niet meer verdwijnen
```

Not `fix: candidate resolution` and not `refactor packets.py`. What changed for
whoever uses the thing.

**Body**: the investigation. What was observed, what turned out to be the cause,
why this fix and not another. A bug fix commit typically opens with the symptom
as the user saw it — including the misleading parts, like a counter that reported
a healthy number while the map stayed empty — then the stack trace or the
measurement, then the mechanism, then the decision.

The body is where the reasoning that did not fit in a comment goes. Between the
two, no decision in this project should be unexplained.

---

## 4. Language

The split is systematic. It is not tidy, and it is worth knowing before you match
the wrong neighbour.

| Where | Language |
|---|---|
| Commit messages | **Dutch** |
| User-facing strings (errors, labels, page copy) | **Dutch** — English arrives via `i18n.js` |
| Code comments and docstrings in `server/app/` | **English** |
| The admin, commanding and clock subsystem (`routes_admin.py`, `commanding.py`, `clocksync.py`, `retention.py`) | **Dutch** comments |
| `homeassistant/`, `proxy/`, `mosquitto/`, `deploy/` | **Dutch** comments |
| Tests, including test function names | **Dutch** |
| `docs/` | **Both**, mirrored — see [§10](#10-documentation-conventions) |

Match the file you are editing. Do not translate an existing file as a
side-effect of another change.

---

## 5. Vanilla JavaScript, no build step

There is no `package.json` anywhere in this repository, no `node_modules`, and no
bundler. `server/app/static/app.js` is 3500 lines of ES5 in a single IIFE with
`"use strict"`: `var`, no arrow functions, no classes, no modules.

The no-build rule is stated in the code that would otherwise have needed one.
`server/app/main.py`, on cache headers:

> Hash-versioned filenames were rejected: they need a build step, and this site
> deliberately has none.

Cache busting is a restart-time query parameter instead: `templating.py` puts
`asset_v` in the Jinja globals, and templates request `/static/app.js?v={{
asset_v }}`.

What that leaves you with:

| Instead of | Use |
|---|---|
| Components | Jinja2 partials, filled by one function. `_packet_detail.html` is included by both the live page and the archive and populated by `fillPacketDetail` |
| A virtual DOM | `createElement` / `textContent`. `innerHTML` appears twice in 3500 lines, and that is deliberate — see below |
| A state store | `window.MCS`, an inline `{{ ... \| tojson }}` handoff from the server |
| npm packages | CDN `<script>` tags. Leaflet 1.9.4 and Chart.js 4.4.9 are the only two |
| A framework's escaping | `textContent`, 70 uses against 2 of `innerHTML` |

**Templates are server-rendered.** Jinja2 via FastAPI, `{% block %}` inheritance
from `base.html`. The page works before any JavaScript runs.

### i18n

`server/app/static/i18n.js` is entirely client-side: no sessions, no
per-language URLs, no server involvement.

- Templates render **Dutch as the literal text** and tag nodes with `data-i18n`.
  The page is therefore readable and indexable without JavaScript.
- `apply(root)` walks four attributes: `data-i18n` → `textContent`,
  `data-i18n-title` → `title`, `data-i18n-ph` → `placeholder`, `data-i18n-aria`
  → `aria-label`. Interpolation values ride in `data-i18n-vars`.
- The choice is stored in `localStorage`, guarded, because `localStorage` can be
  blocked.
- A missing key falls back to the Dutch wording, never to a raw key on screen.
- `i18n.js` loads **before** any page script, so generated strings can use
  `MCSI18N.t` too. **Every string `app.js` builds must go through it.**

Adding a UI string means adding it to both `nl` and `en` blocks of `DICT`. A
string that only exists in one is a bug, not a partial translation.

---

## 6. Additive SQLite migrations

Plain `sqlite3` with a module-level connection and a mutex. No ORM: the workload
is a handful of small writes per minute plus page reads, and an ORM would add a
dependency and a migration story this project does not need.

Two mechanisms, both idempotent, both run on every process start:

**New tables and indexes** — the `SCHEMA` string in `server/app/db.py` is one
script of `CREATE TABLE IF NOT EXISTS` / `CREATE INDEX IF NOT EXISTS`, executed
on connect.

**New columns** — SQLite has no `ADD COLUMN IF NOT EXISTS`, so existing tables
need an explicit check:

```python
def _migrate(conn: sqlite3.Connection) -> None:
    for table, column, decl in COLUMN_MIGRATIONS:
        names = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}
        if column not in names:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")
```

`COLUMN_MIGRATIONS` is a list of `(table, column, decl)` triples, and **each entry
carries a comment explaining why the column exists**. That comment is not
optional; it is the only record of the decision.

Startup order in `get_conn()`: connect → `journal_mode=WAL` →
`foreign_keys=ON` → `SCHEMA` → `_migrate()` → `_backfill_from_raw()` → commit.

### The rules

- **Nothing is ever dropped.** There is no `DROP TABLE` and no `DROP COLUMN`
  anywhere in `server/app/`. `DELETE FROM` exists only for row retention.
  Dropping a live database is not an option, and neither is a migration that
  cannot be run twice.
- **There is no version table**, no `PRAGMA user_version`, no numbered migration
  files and no down-migrations. Additive changes do not need them; anything that
  does need them is a change that should be reconsidered.
- **A new column starts NULL for old rows.** If it can be recomputed, add it to
  the backfill (see next section). If it cannot, NULL is the honest value and the
  UI must be able to show it as such.

---

## 7. `packets.raw` is the ground truth

`packets.raw` holds the frame exactly as it came off the radio, in hex. Every
other column in that table is a lossy summary of it.

The doctrine, from the migration entry's own comment:

> It is the only complete record of a packet — everything else in this table is a
> lossy summary — and it is what lets a later reader re-parse a packet the decoder
> of the day got wrong.

It roughly doubles the size of a packet row, which is affordable only because
packets have their own short retention.

Three consequences that shape how features get built:

1. **The detail view re-decodes on request.** `packet_detail()` in
   `routes_api.py` runs the *current* decoder over the stored bytes rather than
   reading advert fields out of columns. A decoder fix therefore improves old
   packets immediately, not just new ones.
2. **Frame first, column as fallback.** Where both exist, the decoded frame wins
   and the stored column answers only for rows whose bytes were never kept.
   `path_hash_size` is `None` for those rows — *"None is that answer rather than
   a plausible-looking 1"*.
3. **New derived columns get backfilled.** `_backfill_from_raw()` re-reads stored
   frames through the same decoder new packets go through. Rows that predate the
   `raw` column keep their NULLs forever, which is the honest answer for a packet
   whose bytes nobody kept.

Denormalisation is allowed as an explicit, justified exception — `path` and
`scope` are columns because the detail view resolves every hop and re-decoding
frames for that repeats work ingest already did. The justification is in the
comment beside the migration entry. "Because it is faster" without a measurement
is not one.

Cost control matters here: `raw` is deliberately excluded from the list and
search endpoints. It stays on the detail endpoint, for the one packet somebody
actually opened.

---

## 8. Tests pin down the refusals

Full detail in [`testing.md`](testing.md). The convention worth stating here:
tests in this project are mostly statements about **what the system refuses to
claim**, which is the direct consequence of [§1](#1-honesty-about-uncertainty).

Test names read as sentences to that effect — *"without adjtimex nothing is
asserted"*, *"a clock that jumped far backwards is refused"*, *"backfill restores
emptied columns"*. `test_candidates.py` is described in its own docstring as
answering not "which node is it" but "when are we allowed to say".

Two hard rules:

- **No captured packets.** Every test vector is hand-built from
  [`protocol.md` §1](protocol.md#1-the-over-the-air-packet-format) by
  `server/tests/frames.py`. A test that fails should send you to the spec, not to
  a binary nobody can read.
- **No network, no MQTT, no real database.** `conftest.py` redirects the data
  directory before anything imports `app`, so a test run never creates
  `server/data/` in your working copy.

Adding a feature that can be wrong in an interesting way means adding the test
that says how it is allowed to be wrong.

---

## 9. Tooling, or the lack of it

| Thing | Status |
|---|---|
| Linter / formatter config | **None.** No `.flake8`, `ruff.toml`, `pyproject.toml`, `.eslintrc`, `.editorconfig` |
| Type checker | **None runs.** Annotations are documentation |
| CI | **None checked in** |
| Python | **3.12** (pinned by `server/Dockerfile`); syntax floor 3.10 |
| Runtime dependencies | 5, in `server/requirements.txt`, lower-bound pinned, no lockfile |
| Dev dependencies | `server/requirements-dev.txt` — the runtime set plus `pytest` |
| Packaging | None. The app is run, not installed: `uvicorn app.main:app` |

Type hints are used pragmatically: about seven in ten functions carry a return
annotation, containers are often left bare (`-> dict`, `-> list[dict]`), and
there are **no `from typing` imports** — builtin generics and PEP 604 unions
only. Runtime validation is FastAPI's, e.g. `Query(..., ge=1, le=2160)`.

Style is maintained by review. Match the file you are in.

---

## 10. Documentation conventions

`docs/` is bilingual and mirrored. The rules exist so that a reader in either
language reaches the same content, and so that a broken pair is visible.

| Rule | Detail |
|---|---|
| English lives at | `docs/<topic>.md` |
| Dutch lives at | `docs/nl/<topic>.md` — **the same filename** |
| Headings | The same structure in both, in the same order |
| Language switch | First line of every English file: `*[Nederlands](nl/<name>.md)*`. First line of every Dutch file: `*[English](../<name>.md)*` |
| Translation depth | The Dutch is a full translation, not a summary. If a table has eight rows in English it has eight rows in Dutch |
| Index entries | Every document appears in both `docs/README.md` and `docs/nl/README.md`, with one sentence saying what a reader finds there |

Anchors are the one thing that legitimately differs: a Dutch heading produces a
Dutch anchor. Cross-language links (`protocol.md#...` from a Dutch file) point at
the Dutch file's own anchors where the target has been translated, and at the
English anchor where it deliberately has not.

Style: businesslike and concrete. Tables where a table helps. References to file
and function, so a claim can be checked. When behaviour comes from the MeshCore
firmware, cite the file and line it came from.

**A new document is not finished until both halves exist and both indexes list
it.** A file with no counterpart is a bug in the documentation, the same way a
UI string in only one language is a bug in the site.

---

## Submitting a change

1. Work on `main` unless there is reason not to; this is a small project.
2. Run the tests from `server/`:
   ```bash
   pip install -r requirements-dev.txt
   python -m pytest
   ```
3. Keep the change and its explanation together: the comment for the mechanism,
   the commit body for the investigation.
4. Commit in Dutch, with the why in the body.
5. Documentation change? Both languages, both indexes.
6. Behaviour that can be uncertain? Say so in the UI, and add the test that pins
   down the refusal.

### Do not commit

- Addresses, hostnames, passwords, tokens or any other infrastructure detail.
  **This repository is public.** `mosquitto/acl` is gitignored because it
  contains account names; `.env` because it contains secrets. Examples go in
  `.example` files with placeholder values.
- Real captured packets as test fixtures. Build them from the spec.
- A build step.

---

## Renaming the repository

GitHub keeps the old name as a redirect, so existing clones, `git remote`s and
links keep working after a rename. That makes this a low-risk operation — but
not a no-op, because a few things point at the name in text rather than through
the remote.

In the GitHub settings: Settings → General → Repository name → `MeshManager`.

Afterwards, in the repository:

| What | Where |
|---|---|
| Clone URLs in the docs | `README.md`, `README.nl.md`, `docs/deployment.md`, `docs/nl/deployment.md` |
| Integration links | `homeassistant/custom_components/meshmanager/manifest.json` (`documentation`, `issue_tracker`) |
| Badges, if any are added later | `README.md` |

And on the deploy host, optionally — the redirect means it is not required:

```bash
git remote set-url origin https://github.com/DinXke/MeshManager.git
```

**Do not rename the directory of the deploy clone.** The Docker Compose project
name is pinned to `meshstats` precisely so the volumes no longer follow the
directory, but the clone path is also baked into the systemd unit by
`deploy/install-autoupdate.sh`. Renaming it means re-running that script.

See [`migration.md`](migration.md) for the rest of the rename.

---

## See also

| | |
|---|---|
| The test suite in detail | [`testing.md`](testing.md) |
| The vocabulary these documents use | [`glossary.md`](glossary.md) |
| How the pieces fit together | [`architecture.md`](architecture.md) |
| What is protected, and what is not | [`security.md`](security.md) |
