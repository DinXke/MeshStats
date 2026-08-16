# Administration

*[Nederlands](nl/admin.md)*

Accounts, tokens, sessions and the pages behind `/admin`.
[`security.md`](security.md) covers the threat model and the reasoning behind the
mechanisms; this document is the operator's view of them.

## The first account

On first start, `main.bootstrap()` creates an `admin` account with a
`secrets.token_urlsafe(12)` password and prints it to stdout **once**:

```
[mc-repeater-stats] Eerste start: admin-account aangemaakt.
[mc-repeater-stats] Gebruikersnaam: admin  Wachtwoord: <…>
[mc-repeater-stats] Wijzig dit meteen via /admin.
```

```bash
docker compose logs meshstats | grep -i wachtwoord     # Docker
journalctl -u mc-repeater-stats | grep -i wachtwoord   # systemd
```

It is only created when the `admins` table is **empty**, so it never reappears
after you have deleted or renamed the account.

### Setting a password from the command line

```bash
docker compose exec meshstats python -m app.main set-password admin
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

The session cookie `mcs_session` is `base64(payload).hmac`, signed with the key
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
nonce** in the `mcs_login` cookie (`LOGIN_TTL` = 30 minutes), issued fresh on
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

- The token is `mcs_` + `secrets.token_urlsafe(32)`.
- Only its SHA-256 is stored. **The site cannot show it again.**
- It is handed to the browser once through a 60-second `HttpOnly` cookie
  (`mcs_new_token`) rather than through the URL, so it does not land in a proxy
  log or a browser history.
- Revoking sets a flag rather than deleting the row, so `last_used` survives.
- `last_used` is written on every successful check, which is how a token nobody
  uses any more becomes visible.

Plain SHA-256 rather than PBKDF2 is deliberate: a 32-byte random token has no
guessable structure, so the slow hash that protects a human-chosen password buys
nothing and would cost 200 000 rounds on every ingest request.

## The dashboard — `GET /admin`

| Block | Contents |
|---|---|
| Repeaters | Every repeater, public or not, with rename, publish/unpublish, delete, and a link to its settings page |
| Tokens | Active tokens with `created_at` and `last_used`; create and revoke |
| Settings | `heartbeat_min`, `retention_days`, `packet_retention_days`, `packet_max_rows`, `db_max_mb`, `history_ranges` |
| Storage | `retention.overview()`: file size against the ceiling, packets held, the period actually covered, and the last pruning pass |
| Layout | Block order and visibility on a repeater page |
| MQTT | `mqtt_ingest.status()`: connected, broker, topics, messages, packets, errors, last error, commands sent |
| Measurements | `tsdb.status()`: reachable, points written, batches, queue depth, spilled to SQLite, last error, last write |
| Clock | `clocksync.status()` plus `clocksync.targets()` — per repeater whether it can be reached and, if not, why |

`clock_targets` is computed with the **same** `time_route()` the button uses,
with the monitor route closed. When that reasoning had its own copy here, a
repeater's page could claim something different from what the daily round did —
and that difference only shows up if somebody puts the logs next to the admin
page.

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
same meaning, so raising a retention does not need a container restart. Saving
runs `retention.run_once()` immediately, so a lowered retention is applied — and
its result shown — on the page you just clicked on.

The three packet fields default to `0` rather than being required, and `0` means
"this form was not about that": it leaves the existing value alone. Without
that, an older page still open in a tab would set the retention limits to zero,
which is precisely the setting where getting it wrong costs data. Details in
[`retention.md`](retention.md#the-settings-form).

The layout is a JSON list of `{key, visible}` validated by
`metrics.parse_layout()`: unknown keys are dropped, duplicates ignored, and any
block missing from the stored value is appended in its default order. A block
with nothing to show is skipped when the page is rendered, so hiding a block and
having no data for it look the same to a visitor.

## The repeater settings page — `GET /admin/repeaters/{rid}/settings`

A read-only view of `repeater_cli`, plus the two buttons and everything needed to
say honestly what they can do.

| Field | Meaning |
|---|---|
| `settings_rows` | The stored CLI parameters and their `updated` timestamps. A NULL value renders as "(geen antwoord)" |
| `cli_params` | The parameter list a poller is asked for. Editable, `,`/`;` separated, capped at 40 |
| `route` | `commanding.describe(rep)` — whether the button can do anything and, if not, which blocker |
| `queued_since` | A queued look-up that is **still there**: nothing has polled since the click |
| `delivered_since` | When the queue last handed a look-up out |
| `delivery_unanswered` | True when the newest stored answer is older than that hand-out |
| `requested` | `mqtt`, `queued`, `both` or `none` from the last click |
| `clock_route` | `clocksync.time_route(rep)` — which node would get the time |
| `clock_sent` | When this site last sent that node a time |
| `clock`, `clock_wait` | The outcome of the last click, and the wait in minutes |
| `clocksync_reason` | The reason from the last clock check, so a refusal says what was wrong here instead of pointing at `/admin` |

`queued_since` and `delivery_unanswered` exist because the queue is
clear-on-read: a request still sitting there means nothing has polled since the
button was pressed, and one that is gone means the poller took it and the silence
that follows is its own. Without that distinction both look identical — a page
that says "look-up started" and never changes.

The button is drawn from `route`, and a button that cannot do anything is
disabled **and says why**. The required firmware version comes out of that route
rather than being computed separately here: which version is needed depends on
the route (1.8.0 for the node itself, 1.9.0 for a monitor), and two places both
working that out is one too many. See [`commanding.md`](commanding.md).

## Making a repeater public

`is_public` governs everything: the home page, `/r/<slug>`, and every public API
route. `_public_repeater()` in `routes_api.py` answers 404 for a non-public slug,
so a repeater that is switched off is invisible rather than merely unlinked.

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
