# Testing

*[Nederlands](nl/testing.md)*

The suite under `server/tests/`: how to run it, how it is built, and — more
usefully — what it is *for*. These tests are not coverage. They are a written
record of what the system is allowed to claim, and where it must refuse.

---

## Contents

- [Running the tests](#running-the-tests)
- [Configuration](#configuration)
- [The two hard rules](#the-two-hard-rules)
- [How a test database is made](#how-a-test-database-is-made)
- [`frames.py`: packets built from the spec](#framespy-packets-built-from-the-spec)
- [The test modules](#the-test-modules)
- [Tests as statements of refusal](#tests-as-statements-of-refusal)
- [What is deliberately not tested](#what-is-deliberately-not-tested)
- [Writing a new test](#writing-a-new-test)

---

## Running the tests

From `server/`:

```bash
pip install -r requirements-dev.txt
python -m pytest
```

`requirements-dev.txt` is the runtime requirements plus `pytest>=8`. Nothing
else is needed: no database, no broker, no network, no fixtures to seed.

Useful invocations:

```bash
python -m pytest tests/test_packets.py          # one module
python -m pytest -k backfill                    # by name
python -m pytest -x -q                          # stop at the first failure
```

Roughly 400 tests across a dozen modules; a full run takes seconds.

---

## Configuration

`server/pytest.ini`, in full:

```ini
[pytest]
testpaths = tests
pythonpath = .
```

That is the entire configuration. There is no `conftest` plugin stack, no
`tox.ini`, no coverage threshold and no CI file. See
[`contributing.md` §9](contributing.md#9-tooling-or-the-lack-of-it) — the absence
is the convention.

### `conftest.py` does exactly one thing

```python
os.environ.setdefault("MM_DATA_DIR",
                      tempfile.mkdtemp(prefix="meshmanager-test-data-"))
```

Importing `app.config` **creates the data directory and writes a secret key into
it**. Without this redirect, the first test run would leave a `server/data/` with
a `secret.key` in your working copy. It has to happen at module level, because
pytest loads `conftest.py` before any test module imports `app`.

There are no fixtures in `conftest.py`. Test databases are built per module — see
below.

---

## The two hard rules

### 1. Nothing real is touched

No network, no MQTT, no real database. Everything runs against temporary SQLite
files, and dependencies are faked with `monkeypatch.setattr` on module
attributes rather than with a mocking library. `test_clocksync.py` leans on this
heavily.

A test run is safe to do on the machine that also runs the site.

### 2. No captured packets

Every test vector is **hand-built from
[`protocol.md` §1](protocol.md#1-the-over-the-air-packet-format)** by
`tests/frames.py`. There is not a single real, captured packet in the directory.

This is not squeamishness about binary fixtures. It means a failing decoder test
sends you to the specification, where the disagreement can be resolved, instead
of to a blob whose provenance nobody remembers.

---

## How a test database is made

Per module, not shared. The canonical pattern (`test_db.py`, repeated in
`test_candidates.py`):

```python
@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "test.sqlite3")
    db_module._conn = None
    yield db_module
    ...  # close the connection and null it again
```

Both halves matter, and the fixture's own docstring says why: *otherwise tests
leak into each other, and Windows cannot clean up the temporary file.* The
module-level connection in `db.py` is a global; a fixture that forgets to reset
it hands the next test the previous test's database.

Schema creation is implicit. `get_conn()` runs `SCHEMA`, `_migrate()` and
`_backfill_from_raw()` on first use, so a fresh temporary file arrives fully
migrated — which also means the migration path is exercised by every test that
touches storage.

---

## `frames.py`: packets built from the spec

87 lines, the counterpart to the decoder in `server/app/packets.py`.

| Piece | What it builds |
|---|---|
| Route and type constants | `ROUTE_TRANSPORT_FLOOD` … `TYPE_PATH`, mirroring the wire format |
| `frame(route, ptype, *, version, codes, hops, hash_size, payload)` | A complete frame: header byte, optional transport codes, path descriptor, hops, payload |
| `advert_payload(...)` | `pubkey(32) + timestamp(4 LE) + signature(64) + app_data`, with the flag-byte ordering (lat/lon, feat1, feat2, name) |
| `peer_payload(dest, src, blob)` | `dest_hash(1) + src_hash(1) + MAC(2) + ciphertext`, for REQ / RESPONSE / TXT_MSG / PATH |

Two details worth knowing before you use it:

**The key and signature are deliberately impossible.** `PUBKEY = bytes(range(1,
33))` and `SIGNATURE = b"\xab" * 64`. No real key has that shape, and the decoder
does not verify signatures anyway — so a fixture that *looked* real would only
invite someone to believe it was.

**`frame()` does not validate its own arguments.** It will happily attach
transport codes to a route type that must not have them. That is required: the
truncation and malformed-frame tests exist precisely to build frames that break
the protocol's promises, and a helpful builder would make them unwritable.

With no optional fields, `advert_payload()` returns a bare 100-byte advert, which
the decoder must accept.

---

## The test modules

All in Dutch, including the test function names. Each file opens with a docstring
stating **why the file exists** — read those first; they are the design notes for
the area under test.

| Module | Tests | Area | The question it settles |
|---|---|---|---|
| `test_packets.py` | 23 | `app/packets.py` | Scope classification, address hashes per payload type, ADVERT fields, path parsing, and what happens at a truncation |
| `test_search.py` | 27 | `app/search.py` | Query syntax, LIKE escaping, and the promise that unparseable input is an error and never silence |
| `test_search_sort.py` | 11 | `app/search.py` + the search endpoint | That rows really arrive in the requested order, and that paging does not disturb it |
| `test_db.py` | 11 | `app/db.py` | Decoder columns on insert, `COLUMN_MIGRATIONS`, and `_backfill_from_raw` |
| `test_candidates.py` | 23 | `app/candidates.py` | Not "which node is it" but **when are we allowed to say** |
| `test_clocksync.py` | 25 | `app/clocksync.py` | The clock the site pushes to the mesh. Almost all refusals — correction is one-directional |
| `test_commanding.py` | 19 | `app/commanding.py` | "Can this button do anything?", answered from four independent sources |
| `test_mqtt_command.py` | 17 | site → broker → node | That publishing says nothing about arrival, and what must therefore *not* happen |
| `test_mqtt_ingest.py` | 7 | `app/mqtt_ingest.py` | Unreadable messages. Regression for a node name containing a quote |
| `test_nodes.py` | 16 | `/api/v1/nodes/{prefix}` | A panel assembled almost entirely from things no column holds |
| `test_retention.py` | 15 | Pruning | Not "something is deleted" but the **order** in which it is |
| `test_settings_chain.py` | 25 | button → queue → poller → storage | The clear-on-read queue, which fails without producing any error |
| `test_zichtbaarheid.py` | 18 | `show_position` / `show_name` across every public route | Not "the switch flips" but **that no route leaks past it**, plus that the defaults change nothing |
| `test_rechten.py` | 30 | `app/rbac.py`, `app/audit.py`, the migration | Three ways a permission model breaks without raising anything: too wide, too narrow (the owner locked out), and forgotten on a route |
| `test_beheerpaginas_renderen.py` | 10 | The admin templates, end to end | That the branches saying *why* a button is off actually render — a typo there is a blank admin page, not a test failure |

### Why several of these have a file to themselves

The choices are not arbitrary, and the docstrings explain them:

- **`test_rechten.py`** — a permission model can break in three ways that raise
  nothing. *Too wide*: someone may do something they should not, which you notice
  only after it happened — and with firmware, "it happened" is a node off a roof.
  *Too narrow*: the migration runs on a database where one administrator could do
  everything, and getting one column wrong locks out the only person who can fix
  it. *Forgotten*: a route without a check works — it just works for everybody.
  The last section therefore walks the router itself rather than testing
  behaviour.

- **`test_beheerpaginas_renderen.py`** — the rest of the suite calls route
  functions and inspects what they return, which for a template is not enough.
  Nearly everything that can go wrong on these pages sits in the branches that
  say *why* something is absent, and those only run when the page renders. Same
  reason `test_firmware.py` puts the firmware page through the real Jinja
  environment.

- **`test_commanding.py`** — the answer to "can this button do anything?" comes
  from four separate sources: who publishes for this repeater, which firmware it
  runs, whether the broker is attached, and whether a poller polled recently.
  Getting it wrong costs no error message, only a page promising something nobody
  will do.
- **`test_mqtt_command.py`** — the chain has one property that decides
  everything: publishing says nothing about arrival. The broker keeps nothing for
  an offline node and the node acknowledges nothing. So what is pinned down is
  mostly what must **not** happen: no retained publish, no publish without a
  connection, nothing on any other topic.
- **`test_mqtt_ingest.py`** — traceable to a specific firmware note: a node name
  containing a quote made the payload invalid JSON, the message was discarded,
  and the node vanished from the statistics while every counter on the firmware
  side went on reporting "published".
- **`test_nodes.py`** — the node-detail response is almost entirely derived:
  how much traffic is attributable to a node, who hears it, how often it appears
  as a hop. Every one of those derivations carries a caveat, and the caveats are
  the test subject.
- **`test_retention.py`** — the promise is the *order*: too old goes first, and
  only then, if there are still too many, the oldest goes until it fits. The
  second half is invisible if you only count rows.
- **`test_settings_chain.py`** — the chain is fragile at exactly one place that
  produces no error: the site's queue is clear-on-read, so once the poller has
  taken a request it exists nowhere. If key matching then fails, the request is
  gone and the admin page hangs on its promise.
- **`test_clocksync.py`** — the firmware only ever moves a clock **forwards**,
  because a node that sets its clock back invalidates its own adverts for
  everyone who already knows it. An error that leaves here cannot be taken back
  from the other side.

---

## Tests as statements of refusal

The naming convention follows directly from
[`contributing.md` §1](contributing.md#1-honesty-about-uncertainty). Test names
are sentences about what the system will not claim:

```
test_zonder_adjtimex_wordt_er_niets_beweerd
test_een_klok_die_ver_achteruit_sprong_wordt_geweigerd
test_backfill_herstelt_geleegde_kolommen
```

*Without adjtimex, nothing is asserted. A clock that jumped far backwards is
refused. Backfill restores emptied columns.*

`test_candidates.py` states it outright in its docstring: the tests are written
so that they mostly pin down the **refusal** — no winner on a tie, no name when
everything has been excluded, and no exclusion on a field the frame does not
bound.

This is what makes the suite worth having. A test that a correct input produces a
correct output guards against typos. A test that an *ambiguous* input produces an
ambiguous answer guards against the whole class of change where someone makes the
output tidier by making it dishonest.

---

## What is deliberately not tested

`server/tests/README.md` keeps an explicit list, and the reasoning is worth
repeating: behaviour that is still moving is not nailed down, because tests on it
would break at the next intended change and teach nothing when they did.

Currently excluded on those grounds:

- The heatmap's scale and window
- The meaning of `since_id=0` in `recent_packets`
- The archive page's frontend

Declared stable, and therefore anchored: the decoder, the query language, and the
backfill.

If you are about to add a test, check whether the thing you are testing is on
that list. If it is, either the list is out of date — say so in the commit — or
the test should wait.

---

## Writing a new test

1. **Start with the docstring.** Say why the file or the case exists, and what
   would go unnoticed without it. That is the convention every existing module
   follows, and it is the most useful part of the file.
2. **Build packets with `frames.py`.** If you need a shape it cannot build, extend
   `frames.py` from [`protocol.md`](protocol.md) and cite the section. Do not
   paste a captured frame.
3. **Write the refusal, not just the success.** Name the state the system is
   allowed to be in when it does not know, and assert that it says so.
4. **Reset globals.** `db_module._conn`, module attributes patched with
   `monkeypatch` — anything module-level leaks into the next test if you leave
   it.
5. **Dutch names, sentence style.** `test_<what happens>_<under what
   condition>`.
6. **Keep it offline.** If your test needs a broker or a network, the thing you
   are testing needs a seam.

---

## See also

| | |
|---|---|
| Why the tests look like this | [`contributing.md`](contributing.md) |
| The spec the fixtures are built from | [`protocol.md`](protocol.md) |
| What the modules under test do | [`architecture.md`](architecture.md) |
