# Security

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

**The server holds no credentials for your mesh.** Data flows one way, from node
to site. There is no stored node password, no return channel, no command path
from the site to a node. Full compromise of the website does not give an attacker
control of a single radio.

The one qualification: the HA integration polls `GET /api/v1/commands` and acts
on what it finds — a list of repeater prefixes to refresh and CLI parameters to
fetch. A compromised server could therefore ask Home Assistant to run
`send_cmd` against repeaters *whose passwords Home Assistant already holds*.
Requests are clamped (params truncated to 64 chars, at most 40 per request), but
a param may be `cmd:<literal>`, which is sent verbatim. This is a real, if
narrow, path — and it exists only if you use the HA integration with repeater
passwords configured.

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

Consequences of statelessness, worth knowing before you rely on it:

- **Sessions cannot be revoked.** Changing the admin password does not log anyone
  out. A stolen cookie is valid for up to 12 hours.
- The only global logout is deleting `/data/secret.key` and restarting. That also
  invalidates every CSRF token.

`SameSite=lax` blocks cross-site POSTs, which is the main CSRF vector, while
still allowing normal top-level navigation to `/admin`.

### CSRF

```python
hmac.new(SECRET, b"csrf|" + session_cookie, hashlib.sha256).hexdigest()[:32]
```

Derived from the session cookie, so it is per-session and cannot be forged
without the secret. Rendered as a hidden `csrf` field in every admin form and on
the public repeater page when an admin is logged in. Every state-changing admin
POST validates it.

Two gaps:

- **`POST /admin/login` has no CSRF token.** Login CSRF (forcing a victim into
  *your* session) is possible. Low impact here, but it is a gap.
- Validation uses `!=` rather than `hmac.compare_digest()`. Not constant time.
  The token is truncated to 32 hex chars (128 bits), which is still far too much
  to guess.

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

`limit_body` rejects bodies over 2 MB on `/ingest`, `/contacts` and
`/repeater_settings`.

Two holes worth knowing:

- It reads `Content-Length`. A request without one (chunked transfer) evaluates
  to `0` and passes.
- It is not applied to admin form POSTs.

Cap request size at your reverse proxy as well (`client_max_body_size` in nginx).

### Rate limiting

**There is none.** The only brute-force mitigation is `time.sleep(1)` on a failed
`/admin/login`, and because the endpoint is a synchronous `def`, each attempt
occupies a threadpool worker — so it is also a small self-inflicted DoS surface.

If the site is public, put rate limiting or an access gate in front of `/admin*`.
See [`deployment.md`](deployment.md#what-to-protect).

### Input handling

- Metric keys are stored verbatim. There is no allowlist. Unknown keys render in
  the "other" section. Values are coerced to `float`, or stored as strings
  truncated to 255 characters.
- `repeater_cli.param` truncated to 64 chars, `value` to 4000.
- All SQL uses bound parameters, including the dynamically built `NOT IN` clause
  in `upsert_cli_settings` — the placeholder count varies, the values are still
  bound.
- Templates use Jinja autoescaping.

**Unknown repeaters are created automatically and are public by default.**
Anyone with a valid token, or publish access to the broker, can make a new
repeater appear on the public page. Hide it in `/admin`.

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

Broker auth (`allow_anonymous false`) is the **only** gate on the MQTT path.
There is no token check on ingested messages.

More importantly, **repeater identity comes from the JSON body, not the topic**.
The `+` in `meshcore/+/stats` is never parsed. So any client allowed to publish
can claim to be any repeater, on any topic that matches the filter.

Fix it at the broker with per-topic ACLs and one account per node — see
[`mqtt.md`](mqtt.md#per-topic-acls-recommended-not-shipped). Do not turn off
`allow_anonymous false`.

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
- `/data/secret.key` and the database.

`mosquitto/init-passwd.sh` runs `mosquitto_passwd -b`, which puts the password on
the command line — visible in shell history and in the process list. On a shared
machine, run `mosquitto_passwd` interactively instead. The script also uses
`-c`, which **truncates** the file: adding a second user means dropping that
flag.

All examples in this documentation use placeholders. If a credential has ever
been committed, rotate it — rewriting history does not un-publish it.

---

## Checklist for a public deployment

- [ ] Change the admin password immediately after first start
- [ ] Put an access gate or rate limit on `/admin*`
- [ ] Bind the container to loopback and let the reverse proxy reach it
- [ ] Add `Strict-Transport-Security` at the proxy
- [ ] Cap request body size at the proxy
- [ ] Do not expose the MQTT port to the internet
- [ ] One broker account per node, with per-topic ACLs
- [ ] Change the node's default `admin` / `meshcore` login before it joins a network
- [ ] Keep node management pages off any untrusted network
- [ ] Back up `/data/secret.key` and the database; store node backups as secrets
- [ ] Review new repeaters in `/admin` — they appear public by default
- [ ] Revoke API tokens you are no longer using; they never expire
