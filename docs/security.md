# Security

*[Nederlands](nl/security.md)*

What this system protects, how, and — as importantly — what it does not protect.
Everything here was read out of the code. Where a control is weaker than it looks,
it says so.

## Threat model

MeshStats publishes statistics about a radio network. The data itself is not
secret; anyone with a LoRa radio can hear the same adverts. So the assets worth
protecting are not the readings.

| Asset | Where | Why it matters |
|---|---|---|
| **A node's private key** | SPIFFS on the node; inside any filesystem backup | Holding it means *being* that node. Adverts are Ed25519-signed, so identity is the key. |
| Node administration | Node management page, telnet console | Firmware upload, WiFi settings, key export |
| Site administration | `/admin` | Hide/rename repeaters, mint API tokens, change retention |
| API tokens | Server database, HA config, node config | Write access to the ingest API |
| Data integrity | Ingest paths | Someone injecting fake readings |

The structural property worth stating first:

**The server holds no credentials for your mesh.** There is no stored node
password and no way for the site to configure a radio. Full compromise of the
website does not give an attacker control of a single node.

Data used to flow strictly one way, and that is no longer literally true. Two
narrow return paths exist, and both are worth understanding before trusting the
sentence above.

**1. The MQTT command topic.** The server publishes on `meshcore/<node>/cmd`, and
the firmware accepts exactly three words there: `settings` (read my own CLI
parameters now), `status` (publish a statistics message now) and `time <epoch>`
(set my clock). It is an exact match against that list — not a prefix test, and
explicitly *not* a
fallthrough to the node's CLI, even though the node's telnet console does exactly
that. That console sits behind a password on a link you control; this topic is
reachable by anyone holding broker credentials, and these repeaters hang on roofs
where one `reboot` in a loop is a lost node.

Two of the three only make the node say what it would have said by itself. The
third does not: `time` writes to the device. It is bounded by the firmware's own
rules rather than by the topic — a clock may only move forward, and a node that
already runs ahead is left alone — so the worst an attacker with broker
credentials can do is push a node's clock into the future, which is not
recoverable over the air and needs a reboot to undo. That is the real ceiling on
this path, and it is higher than "make a node publish a statistics message".
Bound it further with an ACL that gives each node read permission on
its own `cmd` topic only, and the server write permission on `meshcore/+/cmd`
only — see `mosquitto/acl.example`.

**2. The polling queue.** The HA integration polls `GET /api/v1/commands` and
acts on what it finds — a list of repeater prefixes to refresh and CLI parameters
to fetch. A compromised server could therefore ask Home Assistant to run
`send_cmd` against repeaters *whose passwords Home Assistant already holds*.
Requests are clamped (params truncated to 64 chars, at most 40 per request), but
a param may be `cmd:<literal>`, which is sent verbatim. This is the wider of the
two paths by some distance — and it exists only if you run the HA integration
with repeater passwords configured.

---

## Server

### Password storage

`server/app/auth.py`:

```python
salt = secrets.token_bytes(16)
dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 200_000)
return f"pbkdf2${salt.hex()}${dk.hex()}"
```

- **PBKDF2-HMAC-SHA256, 200 000 iterations**, 16-byte random salt per password.
- Stored as `pbkdf2$<salt_hex>$<dk_hex>` in `admins.pw_hash`.
- Verification recomputes and compares with `hmac.compare_digest()` — constant
  time.

200k iterations is above the OWASP floor for PBKDF2-SHA256. A memory-hard KDF
(argon2, scrypt) would be stronger, but PBKDF2 is in the standard library and
this deployment has exactly one admin account, so there is no password database
worth cracking at scale.

The first password is generated at startup with `secrets.token_urlsafe(12)` —
about 96 bits — and printed to stdout once. It is never stored in cleartext.

Change it via `/admin` or:

```bash
docker compose exec meshstats python -m app.main set-password admin
```

Minimum length is 8 characters, enforced in both paths.

### API tokens

- Generated as `"mcs_" + secrets.token_urlsafe(32)` — 256 bits.
- Stored **only** as `hashlib.sha256(token).hexdigest()`. The plaintext is never
  written to the database.
- Shown to the admin exactly once, through a 60-second `httponly` cookie rather
  than a URL — so it does not end up in logs, history or a referrer header.
- Revocation sets `revoked=1`; lookups filter on it.

Plain SHA-256 rather than a slow KDF is the right choice here: the token is 256
bits of randomness, not a human-chosen password, so there is nothing to brute
force. A stolen database gives an attacker hashes they cannot invert.

Two limitations:

- **Lookup is a SQL equality match on the digest, not a constant-time
  comparison.** Timing a hash-table lookup to recover 256 bits is not a practical
  attack, but it is not constant time.
- **Tokens do not expire.** Revoke them by hand.

### Sessions

Stateless, HMAC-signed cookies. No server-side session store.

```
cookie value = base64url(json({"u": username, "exp": ...})) + "." + HMAC-SHA256(payload)
```

The key is the 32 random bytes in `secret.key`, generated on first start with
mode `0600`.

| Property | Value |
|---|---|
| Cookie name | `mcs_session` |
| Lifetime | 12 hours (`SESSION_TTL`) |
| `HttpOnly` | yes |
| `SameSite` | `lax` |
| `Secure` | set when the request is HTTPS, including via `X-Forwarded-Proto` |
| `Path` | `/` (Starlette default) |

Verification uses `hmac.compare_digest()` and then checks `exp`.

#### Revocation without a session table

The payload carries a third field, `v`: an HMAC over the account's current
password hash, truncated to 16 hex chars.

```python
hmac.new(SECRET, b"pwstamp|" + pw_hash, hashlib.sha256).hexdigest()[:16]
```

`read_session()` recomputes it from the `admins` row and rejects the cookie when
it no longer matches. Changing a password rewrites `pw_hash`, so **every session
minted under the old password stops working immediately** — the account row is
the revocation list, and no session store is needed. The hash itself never
leaves the server; only its HMAC is published.

`POST /admin/password` issues a fresh cookie in the same response, so the admin
who changed the password is not logged out of their own page.

| Situation | Effect |
|---|---|
| Password changed in `/admin` | All other sessions invalid, this browser reissued |
| Password changed via `python -m app.main set-password` | All sessions invalid |
| Account deleted | Its sessions invalid (`password_stamp` returns `None`) |
| `secret.key` deleted and restarted | All sessions and CSRF tokens invalid |

The cost is one indexed `SELECT` on `admins` per admin request. Sessions created
before this change carry no `v` and are rejected — a one-off logout.

`SameSite=lax` blocks cross-site POSTs, which is the main CSRF vector, while
still allowing normal top-level navigation to `/admin`.

### CSRF

```python
hmac.new(SECRET, b"csrf|" + anchor, hashlib.sha256).hexdigest()[:32]
```

The anchor is a cookie value, so the token is per-browser and cannot be forged
without the secret. Rendered as a hidden `csrf` field in every admin form and on
the public repeater page when an admin is logged in. Every state-changing admin
POST validates it.

| Form | Anchor cookie | Lifetime |
|---|---|---|
| Every logged-in admin form | `mcs_session` | with the session (12 h) |
| `POST /admin/login` | `mcs_login` | 30 min (`LOGIN_TTL`) |

**The login form has its own anchor** because the visitor has no session yet:
`GET /admin/login` mints a random nonce, sets it as a `HttpOnly` cookie, and
renders the token derived from it. A cross-site login POST cannot read that
cookie, so it cannot produce a matching token. A form left open past `LOGIN_TTL`
lands on the same check and gets *"Sessie verlopen — probeer opnieuw"* with a
fresh nonce.

Validation goes through `auth.eq()`, which is `hmac.compare_digest`. Every
comparison of a token, signature or digest in the application uses it —
`check_csrf`, the login token, the session signature, the session stamp and the
password check. The one remaining equality on a secret is the SQL lookup on the
API token digest, described under [API tokens](#api-tokens); it is a hash-table
lookup on 256 bits, not a byte-by-byte walk.

### Security headers

Set as defaults in middleware, so a route can override:

| Header | Value |
|---|---|
| `X-Content-Type-Options` | `nosniff` |
| `X-Frame-Options` | `DENY` |
| `Referrer-Policy` | `strict-origin-when-cross-origin` |
| `Permissions-Policy` | `geolocation=(), microphone=(), camera=()` |

Content Security Policy:

```
default-src 'self';
script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net https://unpkg.com;
style-src 'self' 'unsafe-inline' https://fonts.googleapis.com https://unpkg.com;
font-src https://fonts.gstatic.com;
img-src 'self' data: https://unpkg.com https://*.basemaps.cartocdn.com;
connect-src 'self';
frame-ancestors 'none';
base-uri 'self';
form-action 'self';
object-src 'none'
```

`frame-ancestors 'none'`, `base-uri 'self'`, `form-action 'self'` and
`object-src 'none'` are the parts doing real work.

`'unsafe-inline'` on scripts and styles is a genuine weakening — it is what the
inline chart and map bootstrapping need, and it means the CSP would not stop an
injected inline script. The mitigation is that the pages render no
user-controlled HTML: metric labels and repeater names go through Jinja
autoescaping.

The CDN allowances (`cdn.jsdelivr.net`, `unpkg.com`, CartoDB basemaps) mean the
public pages fetch code and tiles from third parties. That is a supply-chain
dependency and it tells those CDNs who is viewing your site. Self-hosting those
assets would let you drop the allowances entirely.

**`Strict-Transport-Security` is not set.** Add it at your reverse proxy.

### API authentication

Write endpoints require `Authorization: Bearer <token>`:

| Endpoint | Auth |
|---|---|
| `POST /api/v1/ingest` | Bearer |
| `POST /api/v1/contacts` | Bearer |
| `POST /api/v1/repeater_settings` | Bearer |
| `GET /api/v1/commands` | Bearer |
| `GET /api/v1/ping` | Bearer |
| `GET /api/v1/repeaters` | **none** |
| `GET /api/v1/repeaters/{slug}` | **none** |
| `GET /api/v1/repeaters/{slug}/history` | **none** |
| `GET /api/v1/repeaters/{slug}/map` | **none** |

The read endpoints are deliberately open — the site is public — but they are
gated on `is_public=1`, so repeaters hidden in `/admin` return 404 there too.

OpenAPI, Swagger and ReDoc are disabled (`docs_url=None, redoc_url=None,
openapi_url=None`).

### Request limits

`BodySizeLimitMiddleware` (`app/limits.py`) caps every request body at
`MCS_MAX_BODY_BYTES`, default 2 MB, on every route and method.

It works in two steps:

1. A declared `Content-Length` over the limit is refused before a byte is read.
2. Otherwise bytes are **counted as they arrive** through a wrapped ASGI
   `receive`. This is what catches a chunked request, which sends no
   `Content-Length` at all — the old check read that as `0` and let anything
   through.

When the limit trips, the reply is `413` regardless of what the endpoint was
going to say. That last part matters: FastAPI catches everything its form and
JSON parsers raise and rewrites it as its own `400`, so the middleware also
overrides the outgoing response rather than relying on the exception reaching it.

`limit_body()` in `routes_api.py` survives as a courtesy fast path on the JSON
endpoints. It no longer demands a `Content-Length` — the header is optional, and
requiring it rejected legitimate streaming clients without stopping anything.

Cap request size at your reverse proxy as well (`client_max_body_size` in nginx).

### Rate limiting

`POST /admin/login` is throttled in memory by `app/ratelimit.py`. Two independent
buckets, each closing a hole the other leaves:

| Bucket | Stops | Max lockout |
|---|---|---|
| `ip:<address>` | one host trying many usernames | 15 min |
| `user:<name>` | a botnet spreading attempts over many addresses | 5 min |

Five failures inside a 15-minute window are free, so a mistyped password costs
nothing. Each further failure doubles a lockout from 2 s upward, capped per
bucket. A correct password clears both buckets. Blocked attempts answer `429`
with `Retry-After` and never reach the password check.

The username bucket is the one that actually holds the line, because the client
address is only as honest as the proxy chain while the username comes straight
out of the form. Its price is that anyone can lock the admin account out on
purpose — hence a ceiling of minutes rather than hours, and a restart clears it.

#### Which address gets counted

`request.client.host` is **not** used. Uvicorn runs with
`--forwarded-allow-ips "*"`, and in that mode it takes the *first*
`X-Forwarded-For` entry — the one a client writes itself. Keying on that would
let an attacker mint a fresh bucket per request.

Proxies *append* the address they saw, so the header is trustworthy from the
right. `ratelimit.client_ip()` counts `MCS_TRUSTED_PROXY_HOPS` entries in from
the right (default `1`, matching cloudflared straight to the app), validates the
result parses as an IP address, and falls back to the transport address
otherwise.

Set `MCS_TRUSTED_PROXY_HOPS` to the number of proxies you actually run. Too high
and you start reading client-supplied entries again; too low and every visitor
shares one proxy address in a single bucket.

State lives in the process, deliberately: the deployment is one uvicorn process,
and a SQLite table would turn every login attempt from the internet into a write.
A restart forgets the counters.

The old `time.sleep(1)` is gone — it did not slow a parallel attacker and it tied
up a threadpool worker per attempt. Instead, a login for an unknown username runs
`auth.verify_dummy()`, so a wrong username and a wrong password cost the same
200 000 PBKDF2 rounds and the response time reveals nothing.

An access gate in front of `/admin*` at the proxy is still worth having; see
[`deployment.md`](deployment.md#what-to-protect).

### Input handling

- Metric keys are stored verbatim. There is no allowlist. Unknown keys render in
  the "other" section. Values are coerced to `float`, or stored as strings
  truncated to 255 characters.
- `repeater_cli.param` truncated to 64 chars, `value` to 4000.
- All SQL uses bound parameters, including the dynamically built `NOT IN` clause
  in `upsert_cli_settings` — the placeholder count varies, the values are still
  bound.
- Templates use Jinja autoescaping.

**Unknown repeaters are created automatically, but no longer public.** Anyone
with a valid token, or publish access to the broker, can still make a repeater
row appear — bounded by `db.MAX_REPEATERS` — but it arrives with
`is_public = 0` and stays off the public page until you approve it in `/admin`,
which says how many are waiting.

---

## The transport between node and server

Be clear about this: **the MQTT path has no transport encryption.**

- The node's `PubSubClient` runs over a plain `WiFiClient`. No TLS support.
- The server's paho client has no `tls_set()` call and no CA configuration.
- Broker credentials therefore cross the network in plaintext.

The honest comparison with the HTTP path it replaced: that path used TLS, but
called `secure.setInsecure()` — no certificate validation, so it stopped passive
eavesdropping and nothing else. Moving to MQTT traded unvalidated TLS for none.

What is actually at risk on that link: broker credentials, and statistics that
are public on the website anyway. Not node private keys, which never leave the
node except through a filesystem backup.

Keep the broker on a network you trust. If you need to cross an untrusted one,
terminate TLS at a broker that supports it and put a tunnel between node and
broker; do not expose port 1883 to the internet.

### MQTT has no application-level authentication

Broker auth (`allow_anonymous false`) and the ACL file are the **only** gates on
the MQTT path. There is no token check on ingested messages.

#### Publisher versus subject

`_handle_payload()` used to read the repeater prefix out of the JSON body and
never look at the topic, so any client allowed to publish could claim to be any
repeater. It now parses both, and keeps them apart:

- **The topic names the publisher.** `meshcore/<node_hex>/stats` — the node that
  sent the message.
- **The payload names the subject.** `repeater.pubkey_prefix` — the repeater the
  numbers are about. Absent means "myself", and the topic supplies it.

They are deliberately allowed to differ, because a node also forwards statistics
for other repeaters it monitors. Rejecting a mismatch would break that feature
the day it ships. Instead the publisher is stored on the repeater row as
`repeaters.source_prefix` (with `source_seen`) and shown in the **Bron** column
on `/admin`: *zichzelf*, *via `<prefix>`*, or *HTTP-API*. A repeater that starts
arriving through an unfamiliar node is then something you can see.

**This bounds the damage; it does not end it.** With one shared broker account,
anyone holding those credentials can publish under any node's topic, so the topic
is exactly as trustworthy as the account behind it. Recording the route makes
impersonation visible, not impossible.

#### The actual fix: one broker account per node

Give every node its own MQTT user and restrict it by ACL to its own topic prefix.
Then the broker enforces the topic, and `source_prefix` becomes a fact rather
than a claim.

```bash
./mosquitto/init-passwd.sh                 # server account + ACL skeleton
./mosquitto/add-node-user.sh e3d3f4d7ed01  # one account per node
docker compose restart mosquitto
```

`mosquitto.conf` references `acl_file /mosquitto/config/acl`; the file is
generated by `init-passwd.sh` and appended to by `add-node-user.sh`. The shared
account keeps `topic write meshcore/#` so nothing breaks mid-migration — remove
that line once every node has its own account, and only then is the topic
actually enforced. Details and caveats in
[`mqtt.md`](mqtt.md#per-node-accounts-and-acls).

Do not turn off `allow_anonymous false`.

Also note the MQTT path bypasses the 2 MB HTTP body limit entirely. Mosquitto's
`message_size_limit 8192` is the only cap there.

---

## The node management endpoints

### The repeater: `MeshStatsNet`

Everything sensitive is behind **HTTP basic auth**, with credentials shared
between the web page and the telnet console (default `admin` / `meshcore`):

| Endpoint | Auth | Why it matters |
|---|---|---|
| `/` | none | Static shell, renders nothing until `/api/status` succeeds |
| `/api/status` | basic | |
| `/api/wifi` | basic | Changes network settings |
| `/api/backup` | basic | **Contains the private key** |
| `/api/restore` | basic | Overwrites the identity |
| `/update` | basic | Firmware upload |
| Console (port 23) | login prompt | Full MeshCore CLI |

**A filesystem backup contains the node's private key.** That is the single most
sensitive thing in this project. The backup is a dump of everything on SPIFFS —
the Ed25519 keypair, repeater preferences, the ACL and the network config.
Whoever holds that file can impersonate your node: sign adverts as it, take over
its identity in the mesh, and decrypt traffic addressed to it.

This is exactly why `/api/backup` sits behind the login, why the page says so in
plain text, and why the default password must be changed before the node goes on
a network. The same login also guards firmware upload, so a weak password there
means arbitrary code on the node.

Change it on first boot, three ways:

```
wifi console <user> <password>        # serial, mesh CLI or console
```

Basic auth over plain HTTP sends credentials base64-encoded — that is encoding,
not encryption. Telnet is plaintext too. Both are LAN-or-VPN only. There is no
HTTPS on the node and no realistic way to add one alongside mesh, WiFi and BLE
on this hardware — that memory pressure is the same reason MQTT replaced HTTPS
for stats.

`wifi pass` and `wifi console` over the **mesh** CLI put secrets into LoRa
traffic in cleartext. Use the console or the web page for those two.

### The companion node: `StatsPublisher`

**No authentication at all.** Anyone on your LAN can read `/config.json`, change
the broker settings, and redirect your statistics.

The stored broker password is never rendered back to the page — the field is
write-only, and blank means "keep existing" — so the page does not leak it. But
the settings themselves are open.

Treat the companion node's management page as trusted-network only.

### The repeater's own safety nets

Not security controls exactly, but they close an availability failure mode: three
crash-loops drop the node into safe mode (AP + management page only), six disable
the module entirely, and a radio-init failure no longer halts the board. A
rooftop node stays reachable for reflashing rather than becoming a brick. See
[`firmware.md`](firmware.md#the-three-safety-nets).

---

## Secrets and this repository

Never commit:

- `platformio.local.ini` — WiFi credentials and `ADMIN_PASSWORD`. Gitignored.
- `.env` — MQTT credentials. Gitignored.
- `mosquitto/passwd` — broker password hashes.
- `mosquitto/acl` — broker account names. Gitignored; `acl.example` documents the
  format.
- `/data/secret.key` and the database.

`mosquitto/init-passwd.sh` and `add-node-user.sh` run `mosquitto_passwd -b`,
which puts the password on the command line — visible in shell history and in the
process list. On a shared machine, run `mosquitto_passwd` interactively instead.
`init-passwd.sh` also uses `-c`, which **truncates** `passwd`, and rewrites `acl`
outright: re-running it wipes every node account `add-node-user.sh` created.

All examples in this documentation use placeholders. If a credential has ever
been committed, rotate it — rewriting history does not un-publish it.

---

## Audit notes (2026-08)

A defensive review of the whole tree. Most of what it found was already written
down above or in the code's own comments; this section records the gaps that were
not, ranked by severity, plus what was checked and found sound.

### Fixed here

**The MQTT ingest path honoured an attacker-supplied `force`.**
`mqtt_ingest._handle_payload()` passed `force=bool(body.get("force"))` into
`db.ingest()`. `force` means "store this point even if it did not change", which
skips the heartbeat de-duplication that keeps the `samples` table small. It
belongs to the manual refresh on the token-authenticated HTTP path (Home
Assistant, `pusher.py::push_repeater(force=True)`); the node firmware never puts
it in its MQTT JSON. On the `stats` topic the input is device data that is not all
the owner's — anyone with broker credentials can publish under it — so a `force`
arriving there was not a refresh but a way to defeat the de-duplication and fill
`samples`. The path now always ingests with `force=False`. Real nodes are
unaffected because they never send the field.

**Unbounded growth in tables retention never pruned.** `db.prune()` bounded
`packets` (age, row cap, byte ceiling), `samples` (age) and `neighbors` (age),
but not `latest`, `repeater_cli` or `repeaters` — one row each per distinct
`(repeater_id, metric)`, per CLI parameter and per public-key subject, with
nothing ageing them out. An untrusted MQTT publisher reached all three from the
`stats` topic: `_handle_payload()` stored every key in `metrics` verbatim and
every `neighbors[].prefix` without a cap or a hex check, and the payload
`pubkey_prefix` was not validated before `get_or_create_repeater()` created a
**public** row for it (`repeaters.is_public` defaulted to 1) — so junk subjects
both polluted `latest` and appeared on the public home page.

Mosquitto's `message_size_limit 8192` made this a drip rather than a flood, and
the honest likelihood on this deployment — a LAN behind a VPN, one broker,
per-node ACLs — was low: it needed a credentialled or compromised node. It was
fixed anyway because the byte ceiling could not save you here. It only ever
deletes from `packets`, so bloat in `latest` would have had it trimming packets
down to the FIFO floor while the file stayed exactly as large: a disk guard that
reads correctly and empties the wrong table.

Three measures, described in full in [`retention.md`](retention.md#the-tables-somebody-else-can-grow):

- **Refused at the boundary.** `db.check_snapshot()` vets one message before
  anything is written — the subject as bounded hex (`db.key_prefix`, 2–64
  characters), at most 128 metric names of at most 64 characters, at most 512
  neighbour entries — and both ingest paths go through it. `_topic_parts()`
  checks the node named in the topic the same way. Counts refuse the whole
  message; a single malformed neighbour entry refuses only itself and is counted
  in a warning. The topic *prefix* is deliberately not checked against a list, so
  `meshmanager` and `meshcore` get identical treatment during the rename.
- **`latest` and `repeater_cli` are pruned**: orphans, entries not refreshed
  within the sample retention *for repeaters that are still reporting* (a
  long-silent repeater keeps its last known values rather than having its card
  blanked), and a ceiling of 1000 metrics / 200 parameters per repeater — which
  is the rule that matters under abuse, since inside the retention window the
  staleness rule is powerless.
- **`repeaters` is bounded by refusal, not by pruning.** Deleting a repeater
  deletes its history through `ON DELETE CASCADE`, so above `db.MAX_REPEATERS`
  (500) a new subject is simply not created and the message is rejected.

**New repeaters no longer arrive public.** `get_or_create_repeater()` now
inserts with `is_public = 0`. Making a repeater visible on a public site is the
administrator's decision, not a side effect of a message arriving; until this
change, anyone allowed to publish on the topic could publish to the front page.
Existing repeaters are untouched — the INSERT only runs for a key never seen
before — and the admin page shows how many are waiting hidden, because arriving
hidden is fine and arriving unnoticed is not. The deployment checklist item
"review new repeaters in `/admin`; they appear public by default" is therefore
superseded: they now appear hidden by default and must be approved.

### Open: firmware image authenticity

The node's `/update` (and `start ota`) route uses the ESP32 `Update` library
(`firmware/examples/simple_repeater/MeshStatsNet.cpp`). That verifies an image is a
structurally valid app partition of the right size — integrity, not authenticity.
There is no code signing and no secure boot, so anyone who can reach the OTA
endpoint (behind the node's HTTP basic auth, LAN/VPN only) can flash arbitrary
firmware. When the server-side firmware-upgrade path now under construction lands,
a checksum on the downloaded image will prove the download arrived intact — not
that it came from the expected maker. Authenticity needs a signature the node
checks against a built-in public key (or ESP32 Secure Boot); a hash alone does
not provide it. Worth stating in the threat model before that path ships.

### Checked and found sound

- **Search and sort SQL** (`search.py`): column names come only from the fixed
  `FIELDS`/`SORTS` tables, values are always bound parameters, `REGION_SQL` is a
  constant, and `_escape_like()` neutralises `%`/`_`/`\`. The sort key is looked
  up in `SORTS` and an unknown one raises rather than being interpolated. No
  injection route found.
- **Packet decoder** (`packets.py`): `decode()` never raises (broad catch around
  `_decode_into`), every offset is bounds-checked before use, and the five
  admission rules mirror the firmware's `tryParsePacket()`/`isValidPathLen()`. A
  crafted frame yields a partial dict with an `error`, not an exception.
- **Auth, sessions, CSRF, throttle** (`auth.py`, `routes_admin.py`,
  `ratelimit.py`): PBKDF2 200k, `hmac.compare_digest` on every secret, password
  hash stamped into the session so a change revokes old cookies, per-form CSRF
  bound to a cookie the attacker cannot read, and a two-bucket login throttle.
  API-token lookup is a SQL equality on a SHA-256 of 256 random bits, not
  constant-time — noted above and not practically attackable.
- **Frontend** (`static/app.js`, templates): third-party node names and packet
  contents reach the DOM through `textContent` and attribute assignment, never as
  HTML; the two `innerHTML` uses write static markup and fill it via `textContent`
  afterwards. Jinja autoescaping is on. No XSS sink found for mesh-supplied data.
- **Secrets in history**: 74 commits scanned; `.env.example` carries only the
  placeholder `verander-dit-wachtwoord`, `platformio.local.ini.example` only
  `JOUW_WIFI`/`password`, and no `passwd`, `*.key`, `acl` or `.env` file was ever
  committed. History is clean.
- **Privacy** *(addressed after the review)*: the public map and pages show other
  operators' node names and positions, which come from adverts anyone with a
  radio can hear. At the time of the review the only owner control was the
  per-repeater `is_public` toggle — all or nothing, and public by default, so
  there was no way to express that a position is more sensitive than a battery
  reading. Two things changed since. A new repeater now arrives hidden rather
  than public; and the choice is no longer all or nothing:
  `repeaters.show_position` and `repeaters.show_name`, both `INTEGER NOT NULL
  DEFAULT 1` so an upgrade changes nothing that was visible before. Enforcement
  is a single SQL view (`visible_contacts`) that every public read path selects
  from, plus `db.public_name()` and `db.NEIGHBOR_NAME_SQL` for the two name
  sources a view cannot reach, and `tests/test_zichtbaarheid.py` carries one test
  per endpoint. What remains unchanged, and deliberately so: nothing can be
  switched off for a third-party node, because there is no owner here to grant
  that switch to and a form that let any visitor hide any node would be a way to
  blank out somebody else's repeater. See [`privacy.md`](privacy.md).

### Firmware cross-check

A documentation pass (commits `097efd8`, `f603aa5`) flagged several code/doc
mismatches. Reviewed here for security impact; several turn out to be sound, and
saying so is part of the point.

- **The `"via"` field is safe because the server ignores it.** Relayed messages
  carry a top-level `"via":"<node_hex>"`
  (`MeshStatsNet.cpp` ~line 2588). `mqtt_ingest.py` never reads it: publisher
  identity comes only from the topic (`_topic_node()`), which a per-node ACL
  binds. So a node cannot impersonate another through `via`. The risk is latent,
  not present — anyone who later starts trusting `via` for identity reintroduces
  exactly the payload-versus-topic hole the current code avoids. Leave it unread.
- **`createGroupDatagram()`'s 168-byte guard versus `MAX_GROUP_DATA_LENGTH`
  (165): not attacker-reachable here.** `createGroupDatagram()` is upstream
  MeshCore, not vendored in this repo, so its internal bound cannot be audited or
  changed from here. The in-repo callers are all *send* paths
  (`BaseChatMesh::sendGroupData`/`sendGroupMessage`) fed by local application
  input, not by received frames: `sendGroupData` guards `data_len <=
  MAX_GROUP_DATA_LENGTH` and sizes its buffer at `3 + MAX_GROUP_DATA_LENGTH` (168)
  before writing `3 + data_len` (<=168), which fits. Received traffic reaches the
  decoder, never these builders. No over-the-air overflow found; the three-byte
  slack is worth tidying upstream but is not a vector on this deployment.
- **`STATS_TRACE=1` ("TEMPORARY") leaks only to the serial console.** The `TRACE`
  macros in `StatsPublisher.cpp` expand to `Serial.printf`, printing the request
  URI and free heap over USB — never onto the network or MQTT. Reading it needs a
  cable on the device, i.e. the physical access that already owns the node. So it
  is left-on debug (noise, a little airtime for the print), not remote
  disclosure. Turn it off in a production build anyway.
- **`gen_page.py` writes `StatsPage.h` before it checks the size.** On an oversize
  page it leaves the bad artifact on disk *and* exits 1 (lines 68 vs 73-76). Not
  an attack path, but a failed build that a later build could pick up — check
  before writing, or write to a temp file and rename on success. Endorsed as
  build hygiene.
- **`MAX_CONTACTS` is a hard cap, and the contradiction is only in the numbers.**
  Default 100 (`MyMesh.h`), 260 in `platformio.local.ini.example`, 350 in a
  comment. The cap itself is the availability protection — third-party adverts
  cannot grow the table past it. The disagreement is a config-clarity problem to
  reconcile, not an exhaustion vector.
- **`DualSerialInterface.h` is inert, not compiled.** Nothing includes it; both
  `AbstractUITask.h` and `main.cpp` include `MultiSerialInterface.h` instead. So
  it is dead code on disk, not a live surface. Remove it for hygiene; it changes
  no attack surface today.

## Checklist for a public deployment

- [ ] Change the admin password immediately after first start
- [ ] Set `MCS_TRUSTED_PROXY_HOPS` to the number of proxies actually in front of
      the app (default `1`) — the login throttle keys on it
- [ ] Put an access gate in front of `/admin*` as well; the built-in throttle is
      per-process and forgets on restart
- [ ] Bind the container to loopback and let the reverse proxy reach it
- [ ] Add `Strict-Transport-Security` at the proxy
- [ ] Cap request body size at the proxy too
- [ ] Do not expose the MQTT port to the internet
- [ ] One broker account per node (`mosquitto/add-node-user.sh`), then drop
      `topic write meshcore/#` from the shared account in `mosquitto/acl`
- [ ] Check the **Bron** column in `/admin` — statistics arriving via an
      unexpected node are worth a look
- [ ] Change the node's default `admin` / `meshcore` login before it joins a network
- [ ] Keep node management pages off any untrusted network
- [ ] Back up `/data/secret.key` and the database; store node backups as secrets
- [ ] Review new repeaters in `/admin` — they now arrive **hidden** and stay off
      the public site until you approve them; the page says how many are waiting
- [ ] Revoke API tokens you are no longer using; they never expire
