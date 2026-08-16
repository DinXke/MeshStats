# Administration

*[Nederlands](nl/admin.md)*

Accounts, tokens, sessions and the pages behind `/admin`.
[`security.md`](security.md) covers the threat model and the reasoning behind the
mechanisms; this document is the operator's view of them.

## The first account

On first start, `main.bootstrap()` creates an `admin` account with a
`secrets.token_urlsafe(12)` password and prints it to stdout **once**:

```
[meshmanager] Eerste start: admin-account aangemaakt.
[meshmanager] Gebruikersnaam: admin  Wachtwoord: <…>
[meshmanager] Wijzig dit meteen via /admin.
```

```bash
docker compose logs meshmanager | grep -i wachtwoord     # Docker
journalctl -u meshmanager | grep -i wachtwoord   # systemd
```

It is only created when the `admins` table is **empty**, so it never reappears
after you have deleted or renamed the account.

### Setting a password from the command line

```bash
docker compose exec meshmanager python -m app.main set-password admin
```

Reads the password from stdin, minimum 8 characters, and creates the account if
it does not exist. That is the way back in when the password is lost.

## Passwords

`auth.hash_password()`: PBKDF2-HMAC-SHA256, **200 000 rounds**, a 16-byte random
salt, stored as `pbkdf2$<salt hex>$<key hex>`.

Every comparison of anything secret in this application — token, signature,
digest — goes through `auth.eq()`, which is `hmac.compare_digest`. A plain `==`
leaks through its running time how many leading characters were right, turning
guessing into a character-by-character walk.

`auth.verify_dummy()` is the other half of that discipline. When the username
does not exist, the login path still runs a full 200 000-round verification
against a throwaway hash, so a wrong username and a wrong password cost the same.
Without it the response time alone tells an attacker which accounts are worth
attacking.

## Sessions

The session cookie `mm_session` is `base64(payload).hmac`, signed with the key
in `<data>/secret.key`. The payload holds:

| Field | Contents |
|---|---|
| `u` | Username |
| `exp` | Expiry, `SESSION_TTL` = 12 hours from issue |
| `v` | `password_stamp(username)` — 16 hex characters |

There is **no session table**. `password_stamp()` is an HMAC over the account's
current `pw_hash`, so the `admins` row that is read anyway becomes the revocation
list: change a password and every cookie minted under the old one stops
validating, because the stamp no longer matches. The hash itself never leaves the
server — only its HMAC is published.

`read_session()` checks, in order: signature, expiry, username present, stamp
matches. Sessions from before this check carry no stamp and are rejected, which
is the intended one-off logout.

The cookie is `HttpOnly`, `SameSite=Lax`, and `Secure` when the request arrived
over HTTPS — read from `X-Forwarded-Proto` when there is one, which is what makes
login work correctly behind a tunnel.

**Changing your own password does not log you out.** `POST /admin/password`
re-issues this browser's cookie under the new stamp, so the person who just
changed the password keeps their admin page while every *other* session is
invalidated.

Deleting `secret.key` invalidates every session **and** every CSRF token, and is
the blunt way to force everyone out. It does not invalidate API tokens, which are
hashed with plain SHA-256.

## CSRF

`auth.csrf_token(anchor)` is an HMAC over a cookie value, truncated to 32 hex
characters. Every `POST` under `/admin` carries it as a form field and
`routes_admin.check_csrf()` compares it against the token derived from the
session cookie.

The login form has no session yet, so its token hangs off a **short-lived login
nonce** in the `mm_login` cookie (`LOGIN_TTL` = 30 minutes), issued fresh on
every view of the page. The token is worthless to an attacker who cannot also
read the cookie it is derived from.

A form left open past that half hour lands on the same check, which is why its
message is *"Sessie verlopen — probeer opnieuw."* rather than an accusation.

## Login throttling

`ratelimit.py` keeps two **independent** buckets, because each closes a hole the
other leaves:

| Bucket | Stops |
|---|---|
| `ip:<address>` | One host hammering many usernames |
| `user:<name>` | A botnet spreading attempts over thousands of addresses |

The username bucket is the one that actually holds the line, because the client
address is only as honest as the proxy chain while the username is read straight
from the form.

| Constant | Value | Meaning |
|---|---|---|
| `WINDOW_S` | 15 min | Failures older than this drop out of the count |
| `FREE_ATTEMPTS` | 5 | Attempts with no penalty at all |
| `BASE_LOCK_S` | 2 s | Lockout is `BASE × 2^(n-1)` seconds after the free ones |
| `MAX_LOCK_IP_S` | 15 min | Ceiling for the address bucket |
| `MAX_LOCK_USER_S` | 5 min | Ceiling for the username bucket |
| `MAX_ENTRIES` | 4096 | Hard cap on tracked keys; the ones closest to expiry go first |

Locking on username means anyone can lock the admin account out on purpose,
which is why that ceiling is minutes rather than hours — a nuisance beats an
unbounded guessing budget, and the operator can restart the service to clear it.

State lives in **this process only**. The deployment is a single uvicorn process,
and a table in SQLite would turn every login attempt from the internet into a
write. A restart forgets the counters, which is the one case where an attacker
gains something — and they do not get to trigger restarts.

A successful login clears both buckets: whoever proved the password is not the
attacker.

### Which address gets counted

`ratelimit.client_ip()` reads `X-Forwarded-For` itself and counts
`MM_TRUSTED_PROXY_HOPS` entries **in from the right**. `request.client.host`
cannot be used: uvicorn runs with `--forwarded-allow-ips "*"` and then takes the
*first* entry, which any client can write itself. Proxies append the address they
saw, so entries are trustworthy from the right, and only as far back as there are
proxies you actually run.

Too high a value hands an attacker a spoofable bucket key; too low lumps every
visitor onto one proxy address. Raise it only when you really added a hop.

## API tokens

Created in `/admin`, used as `Authorization: Bearer <token>` on the ingest
endpoints.

- The token is `mm_` + `secrets.token_urlsafe(32)`.
- Only its SHA-256 is stored. **The site cannot show it again.**
- It is handed to the browser once through a 60-second `HttpOnly` cookie
  (`mm_new_token`) rather than through the URL, so it does not land in a proxy
  log or a browser history.
- Revoking sets a flag rather than deleting the row, so `last_used` survives.
- `last_used` is written on every successful check, which is how a token nobody
  uses any more becomes visible.

Plain SHA-256 rather than PBKDF2 is deliberate: a 32-byte random token has no
guessable structure, so the slow hash that protects a human-chosen password buys
nothing and would cost 200 000 rounds on every ingest request.

## Two worlds

The admin area had become one long list of sections, in the order they happened
to be added. A button that asks a node over the radio sat next to the input for
the database retention. Those two do not belong in the same visual rank: one
costs airtime on a shared band and touches a device on a roof, the other you put
back with a second click.

Since the split there are two worlds, with a tab bar between them:

| URL | World |
|---|---|
| `GET /admin` | **Nodes and repeaters** — everything that is an action on, or information about, a physical device |
| `GET /admin/repeaters/{rid}` | One node: identity and versions, visibility, look-ups, clock, firmware, delete |
| `GET /admin/firmware` | **Firmware** — which release runs where, which are available, and who can be written to |
| `GET /admin/server` | **Server and site** — everything that configures this installation and touches no device |

The POST routes stayed where they were, so an admin page already open in a tab
does not answer 404 on the next click. `GET /admin/repeaters/{rid}/settings` is
the one URL that moved; it redirects to `/admin/repeaters/{rid}` and carries its
query string along, so a notice does not get lost on the way.

### Language

The admin pages are Dutch only, and that is a decision rather than a backlog
item. The public site is bilingual because every translatable node carries a
`data-i18n` key; the admin pages carry none, and their text is not labels but
paragraphs explaining what the site does and does not know about a node on a
roof. Translating that is hundreds of keys and a second place where the same
nuance has to stay right — and one mistranslated sentence about a clock that
cannot be turned back costs a trip to that roof. If it happens, it should happen
in one step that keys every admin string at once, not button by button.

So the language toggle is absent on admin pages, and `<html data-lang-lock="nl">`
pins the JavaScript-built text (relative times) to Dutch as well. That also fixes
an existing wart: a visitor who once picked English on the public site used to
get English relative times and an English `lang` attribute above Dutch prose.

## Nodes and repeaters — `GET /admin`

The list is grouped by **management level**, because what you can do with a node
differs per group and that is the only grouping that explains the buttons below
it. The level is an *observation*, never a setting — there is no control to set
it, it follows from what comes in, and the sentence behind each node says what we
see it by. It is derived in one place, `commanding._level()`, next to
`describe()`, so a second definition cannot drift away from the first.

| Level | Meaning | What works |
|---|---|---|
| `full_managed` | Our firmware with an MQTT link: the node publishes its own figures and reports a firmware version | Look-ups, settings, clock, and — given an IP path — a firmware upgrade |
| `semi_managed` | No firmware of ours, but rights on its CLI: a monitoring node queries it over LoRa, or the poller logs in with its password | Reading settings, bounded writes, setting the clock |
| `unmanaged` | Telemetry only: seen in the traffic and nothing more | Nothing — the buttons are there and disabled, each with its reason |

The level deliberately does **not** look at `broker_connected`. What is open
right now lives in `route["mqtt"]`; what a node *is* lives in `route["level"]`. A
full-managed node behind a dropped broker is still full managed — there is just
no route at this moment. Mixing the two would make the level swing with the
server's network instead of with the node.

Whether a firmware upgrade is possible is **not** derivable from the level: a
full-managed node without an IP path takes commands but not a megabyte image.
That is a separate field from the firmware path.

Per node the list shows the key prefix, which node its figures come in through,
firmware, last seen, the route open right now, and the public/hidden toggle.
Rename and delete are *not* here — they live on the node's own page, where its
name and key are at the top. A trash icon in a dense table row is exactly how you
wipe the wrong node, and that is the most expensive mistake this site allows.

## One node — `GET /admin/repeaters/{rid}`

Everything about one device, in ascending order of irreversibility: identity and
versions, visibility, look-ups (read), clock (writes one number), firmware
(writes the whole device), delete.

| Field | Meaning |
|---|---|
| `route` | `commanding.describe(rep)` — level, level reason, whether a button can do anything and, if not, which blocker |
| `settings_rows` | The stored CLI parameters and their `updated` timestamps. A NULL value renders as "(geen antwoord)" |
| `queued_since` | A queued look-up that is **still there**: nothing has polled since the click |
| `delivered_since` | When the queue last handed a look-up out |
| `delivery_unanswered` | True when the newest stored answer is older than that hand-out |
| `requested`, `status` | `mqtt`, `queued`, `both` or `none` from the last look-up or status click |
| `clock_route` | `clocksync.time_route(rep)` — which node would get the time |
| `clock_sent` | When this site last sent that node a time |
| `clock`, `clock_wait` | The outcome of the last click, and the wait in minutes |
| `clocksync_reason` | The reason from the last clock check, so a refusal says what was wrong here |
| `broker` | `mqtt_ingest.can_publish()` — see below |

`queued_since` and `delivery_unanswered` exist because the queue is
clear-on-read: a request still sitting there means nothing has polled since the
button was pressed, and one that is gone means the poller took it and the silence
that follows is its own. Without that distinction both look identical — a page
that says "look-up started" and never changes.

Buttons are drawn from `route`, and a button that cannot do anything is disabled
**and says why**. The required firmware version comes out of that route rather
than being computed separately here: which version is needed depends on the route
(1.8.0 for the node itself, 1.9.0 for a monitor). See
[`commanding.md`](commanding.md).

The clock button also checks `broker`. `clocksync.time_route()` deliberately does
not — that question belongs to sending, not to the route — but the button should
know: without a connection a click ended on "nothing was sent", which the page
could have said beforehand.

Actions carry their price in their shape, not only in their text: a blue left
border and a "kost zendtijd" tag for reads that cost airtime, amber for writing
to the device, red for irreversible. The clock and delete buttons ask for
confirmation naming the node and its key prefix, because the question has to be
about *that* node rather than about "this one".

The firmware block links to `/admin/firmware` rather than repeating it. Which
release runs where is a question you ask across all nodes at once, so it has its
own page; the button per node sits there too. The version currently on this node
is not repeated either — it is in *Identity and versions* above, and two places
showing the same number are two places that eventually disagree. Whether an
upgrade is possible does **not** follow from `route["level"]`: that verdict is
`firmware.ota_route()`.

### Visibility on the site — `#zichtbaarheid`

Three switches in one block, in decreasing severity: `is_public` takes the whole
node off the site, `show_position` takes one thing off it, `show_name` another.
One block and not three sections, because it is one question — what does a
visitor see of this node — with three answers.

All three post to `/admin/repeaters/{rid}/toggle` with `what=public` (the
default, so a page still open in a tab keeps working), `position` or `name`. The
column is looked up in a fixed table rather than taken from the request; a column
name arriving from outside and going straight into an `UPDATE` is an open door to
every other column of the table.

Each switch stands next to what it actually does, and the paragraph that says
what **no** switch hides is part of the block rather than a footnote: the key
prefix is in every advert the node transmits, and the slug in `/r/<slug>` was
derived from the name when the row was created and does not follow a rename. The
full account — what disappears, what stays, and how it is enforced across the
seven public routes that could otherwise leak it — is in
[`privacy.md`](privacy.md).

The node list at `/admin` shows a second, click-through pill on a node that is
public but not fully so, because "publiek" alone would promise more than it
delivers there.

## Server and site — `GET /admin/server`

| Anchor | Block | Contents |
|---|---|---|
| `#toegang` | Access | Who you are signed in as, and the password change |
| `#tokens` | API tokens | Active tokens with `created_at` and `last_used`; create and revoke |
| `#opslag` | Retention and storage | The retention and FIFO fields together with `retention.overview()`: file size against the ceiling, packets held, the period actually covered, and the last pruning pass |
| `#weergave` | Display | `heartbeat_min`, `history_ranges`, and the block order for the public page |
| `#cli-params` | Parameters to fetch | `cli_params` — one list for all repeaters |
| `#kloksync` | Clock sync | `clocksync.status()` plus `clocksync.targets()` — per repeater whether it can be reached and, if not, why |
| `#invoer` | Data intake | `mqtt_ingest.status()`: connected, broker, topics, nodes per topic prefix, messages, packets, errors |
| `#tsdb` | Measurements | `tsdb.status()`: reachable, points written, batches, queue depth, spilled to SQLite, last error |

Setting and outcome sit in one block rather than two sections apart: the number
you type and the effect it has are the same question, and whoever lowers a
retention should see immediately that the ceiling may cut in earlier anyway.

`cli_params` used to live on one repeater's page while it applies to all of them,
so changing it there silently changed it for the other nodes too.

`clock_targets` is computed with the **same** `time_route()` the button uses,
with the monitor route closed. When that reasoning had its own copy here, a
repeater's page could claim something different from what the daily round did —
and that difference only shows up if somebody puts the logs next to the admin
page. The button to do it *now* stays where it belongs: on that one node's page,
with its confirmation step.

### Settings, and what they override

| Field | Clamped to | Effect |
|---|---|---|
| `heartbeat_min` | 1–1440 | Minutes; forces a sample point even when the value has not changed |
| `retention_days` | 1–3650 | Sample retention |
| `packet_retention_days` | 1–365 | Packet retention, and the heat map's window |
| `packet_max_rows` | 1000–50 000 000 | FIFO ceiling on the packets table |
| `db_max_mb` | 16–1 000 000 | FIFO ceiling on the database file, WAL included |
| `history_ranges` | 1–8760 per value | The hour buttons on a repeater page's charts |

A setting stored here **takes precedence over the environment variable** of the
same meaning, so raising a retention does not need a container restart.

Every field on `POST /admin/settings` is optional, and that is the point rather
than sloppiness: they are spread over two forms now. With required fields, one
form would have to carry the other's values as hidden inputs, and then a page
that sat open for a while silently overwrites a setting changed elsewhere in the
meantime. Missing means "this form was not about that". The sentinel is `None`
and not `0`, because `0` is not a valid value for these fields and "not
submitted" is a different thing from "set to zero". When a retention or ceiling
did change, `retention.run_once()` runs immediately, so the result shows on the
page you just clicked on; the display form does not provoke a pruning pass.
Details in [`retention.md`](retention.md#the-settings-form).

The layout is a JSON list of `{key, visible}` validated by
`metrics.parse_layout()`: unknown keys are dropped, duplicates ignored, and any
block missing from the stored value is appended in its default order. A block
with nothing to show is skipped when the page is rendered, so hiding a block and
having no data for it look the same to a visitor.

## Making a repeater public

`is_public` governs whether the node is on the site at all: the home page,
`/r/<slug>`, and every public API route. `_public_repeater()` in `routes_api.py`
answers 404 for a non-public slug, so a repeater that is switched off is
invisible rather than merely unlinked.

`show_position` and `show_name` are the finer-grained pair beside it, for the
node that may be on the site but not with everything. They default to 1, so a
database that gains the columns on upgrade shows exactly what it showed the day
before. See [`privacy.md`](privacy.md).

**A repeater that appears by itself arrives hidden.** Anything created out of an
incoming MQTT or HTTP message gets `is_public = 0`: this is a public site, and
making a repeater visible is your decision rather than a side effect of a message
arriving. Repeaters that already existed keep whatever they had. A line at the
top of **Nodes en repeaters** says how many are waiting — arriving hidden is fine,
arriving unnoticed is not — and the *verborgen* pill on the node itself is how you
approve one.

Deleting a repeater removes its `samples`, `latest` and `neighbors` rows
explicitly, then the row itself. Its packets stay: `packets.observer` is a key
prefix, not a foreign key, and a reception is a fact about the mesh rather than
about the row that has just been deleted.

## What the admin area does *not* do

- **No user management.** One or a few accounts, created from the command line.
- **No audit log.** Actions land in the ordinary application log.
- **No `/health` endpoint.** The container healthcheck fetches `/`.

## Hardening a public deployment

The application throttles `POST /admin/login` and sets its own security headers,
but the throttle's state lives in one process and is forgotten on restart. If the
site is reachable from the internet, a second lock on `/admin*` is still worth
having: Cloudflare Access, an IP allowlist, or a rate limit at the proxy. The
rest of the site and `/api/v1/*` can stay open.

The full checklist is in
[`security.md`](security.md#checklist-for-a-public-deployment).

## Related documents

| Question | Document |
|---|---|
| Threat model and mechanisms | [`security.md`](security.md) |
| The routes behind these pages | [`api.md`](api.md#admin-routes) |
| What the buttons can and cannot reach | [`commanding.md`](commanding.md) |
| The clock button | [`clocksync.md`](clocksync.md#the-button) |
| Where the settings are stored | [`database.md`](database.md#settings) |
