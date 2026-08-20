# Administration

*[Nederlands](nl/admin.md)*

Accounts, tokens, sessions and the pages behind `/admin`.
[`security.md`](security.md) covers the threat model and the reasoning behind the
mechanisms; this document is the operator's view of them.

## The first account

On first start, `main.bootstrap()` creates an `admin` account **as a server
administrator**, with a `secrets.token_urlsafe(12)` password printed to stdout
**once**:

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

Reads the password from stdin, minimum 8 characters. An account it has to
*create* is made a server administrator — a recovery path that leaves you with an
account that may do nothing is not a recovery path. An account that already
exists keeps the rights it had: setting a password is no reason to promote
someone.

```bash
docker compose exec meshstats python -m app.main promote admin
```

The second way back in, for the case where accounts exist but none of them is a
server administrator any more. Then the users page is unreachable and setting a
password does not help. `promote` makes the named account a server administrator
and clears its disabled flag.

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

## Users, roles and groups

Access used to be all-or-nothing: whoever could log in could do everything. That
was defensible while this site only *showed* things. It now does things — it asks
nodes over LoRa (which costs airtime), sets clocks, writes firmware, and decides
what the world sees of a node. Those actions deserve the question of who may
perform them.

The model is built on **actions**, not on tables. `rbac.py` is the one place that
answers "may this user do this to this node".

### Risk classes

Every action carries a risk class. The classification is not invented for the
permission model — the settings writer already sorts its parameters into
*ordinary*, *writes noticeably* and *can cut off reachability*, and that is
exactly where an operator wants to cut: someone who may set a clock but may not
flash firmware. One class was prepended, because "may look around" is a real role
that starts nothing.

| Class | Meaning | Actions |
|---|---|---|
| `kijken` | Changes nothing | Open a node page, read its stored settings |
| `gewoon` | Consequences that pass by themselves | Look-ups (airtime), rename, ordinary setting writes |
| `merkbaar` | Changes something lasting | Clock, visibility and privacy, management address, noticeable setting writes |
| `ingrijpend` | Can make the node unreachable, or destroys data | Firmware, risky setting writes, delete |

### Roles are ceilings

A role is nothing but a ceiling on that class. Four roles, four classes, one to
one — so "may this role do this?" is a comparison of two numbers rather than a
list that has to be maintained per role, and therefore impossible to half-update
when an action is added.

| Role | May do everything up to |
|---|---|
| `lezer` | `kijken` |
| `bediener` | `gewoon` |
| `technicus` | `merkbaar` |
| `beheerder` | `ingrijpend` |

Rejected alternative: individual permissions per action, ticked off. More
flexible, and unreadable in practice — a matrix of fourteen checkboxes times
every node times every group is a matrix in which nobody can still see who may do
what, and "who was allowed to flash this node" is precisely the question that has
to stay answerable.

### Server administrators

Beside those roles sits one flag, `admins.is_superuser`. A server administrator
may do everything on every node, plus everything on **Server and site**:
settings, retention, tokens, users, the full audit trail.

Server-scoped actions cannot be granted per group, and that is a choice rather
than a gap. There are five of them, and three (tokens, users, settings) are
enough to grant yourself all the rest. Splitting them would suggest a separation
that is not there.

**The last active server administrator cannot demote, disable or delete
themselves.** Without that latch one wrong checkbox is an installation nobody can
administer any more, and the way back runs over the command line on the server
itself.

### Grants

A grant binds a **subject** (a user or a user group) to an **object** (one node,
a node group, or all nodes) with a role, and an effect of `allow` or `deny`.

| Column | Values |
|---|---|
| `subject_type` | `user`, `group` |
| `object_type` | `node`, `nodegroup`, `all` |
| `role` | `lezer`, `bediener`, `technicus`, `beheerder` (NULL on a deny) |
| `effect` | `allow`, `deny` |

### Conflicting grants

One rule, in one place (`rbac.resolve()`), because two rules in two places
eventually give different answers.

**Deny beats allow.** Always, and regardless of how specific the allow was. A
deny on "all nodes" therefore beats an allow granted directly on one node. That
is the least surprising direction to be wrong in: whoever revokes an exception
wants that revocation to have the last word, not to find an older, more specific
row that overrules it.

**Among allows, the widest wins.** A user who is `lezer` through their group and
`technicus` directly is `technicus`. Otherwise adding someone to a group could
*shrink* their rights, which is exactly the kind of surprise this model has to be
free of.

**No grant is no access.** There is no implicit role for nodes nobody has said
anything about. Such a node is invisible to an ordinary user until a server
administrator says something about it.

A deny carries no role: it denies everything on that object. A deny that itself
has gradations ("may be at most `lezer` here") cannot be surveyed on a page, and
the case you need a deny for — not this one node, whatever else is true — has no
gradations.

### Nodes in no group

A repeater appears in the database by itself as soon as a message about it comes
in (`db.get_or_create_repeater`), and it is then in no node group. For an
ordinary user it is invisible until a grant covers it — directly, or through a
grant on *all nodes*, which is the intended escape hatch so you do not have to
put every new node in a group.

Silently invisible is the same problem as silently hidden, so both pages count
them: **Nodes and repeaters** says how many nodes are not shown to you, and
**Server and site** lists the nodes that are in no node group at all.

### Where the check happens

`rbac.decide(user, action, rep)` is the only function that says yes or no. Every
writing admin route goes through `routes_admin.require_perm()`, and
`test_rechten.py` walks the router to require it: a check copied out per route is
a check that gets forgotten at the next route. Routes that legitimately have none
are listed with their reason in `routes_admin.ROUTES_ZONDER_RECHTENCONTROLE`.

This is the counterpart of `commanding.route_for()`, which says what a node
*can* do. **A button works only when both say yes.** When either says no the
button does not disappear — it is disabled with the reason in its tooltip, which
is the line this site holds everywhere. The reasons are Dutch sentences, because
they end up on the screen.

Templates never reason about this themselves. The route passes `rechten`, a dict
of action to decision, and the template asks `rechten['node.firmware']`. A
template that reasons is a second place the answer comes from, and the first time
those two disagree there is a button promising something the route refuses.

### API tokens and this model

**A token is not a user.** It gives access to the HTTP API's intake paths
(bringing in statistics, fetching the command queue, submitting contacts) and to
nothing under `/admin`. There is therefore no role to set on it and no node to
attach it to, because there is no action that would be about.

Why not anyway: a token that can carry roles is a second path to the same powers,
with its own revocation and its own audit trail. Two paths to "may write this
firmware" is one too many — which was the whole point of `rbac.py`. Tokens do
record who minted them (`tokens.created_by`), because a token without an owner is
a key nobody dares revoke.

## The audit trail

While there was one administrator, "who flashed this node" was not a question.
With several users it is one, asked on an evening when somebody has to climb onto
a roof.

It also fits the line the rest of this project holds: a button that promises what
it cannot deliver is dishonest, and a remote action that leaves no trace is the
same dishonesty one step later.

| Column | Contents |
|---|---|
| `ts` | UTC, ISO |
| `actor` | Username, as text — so it survives the account being deleted |
| `action` | The action name from `rbac.ACTIONS`, or `login` / `eigen.wachtwoord` |
| `object_type`, `object_id`, `object_name` | The node, name included — so it survives the node being deleted |
| `outcome` | `ok`, `geweigerd`, `mislukt`, `deels` |
| `detail` | A readable summary of what happened |
| `ip` | `ratelimit.client_ip()`, or empty |

**Refused attempts are recorded too**, with `outcome='geweigerd'`, and beside the
successful ones rather than in a separate log: two logs are two places to look,
and the second one gets forgotten. The refusal is written by `require_perm()`
itself, so it does not depend on anyone remembering to log it.

`deels` is for the commands that leave along two routes at once and reach one of
them (`routes_admin._dispatch`); `mislukt` covers "was allowed, went wrong",
including a look-up that found no route at all.

**What never goes in:** passwords, tokens, management addresses, and the contents
of settings that could be a secret. `detail` summarises *what* happened ("to
1.10.0", "via the monitor"), not the useful payload. This repository is public
and the trail is exportable.

`audit.log()` swallows its own errors: a full disk or a locked database must not
blow up a firmware upgrade that is already under way. It does write a line to the
ordinary log, so a trail that has quietly stopped recording does not stay quiet.

Rows are kept for `audit_retention_days`, default **730 days** — far longer than
packets (7) or measurements (180), because this is the one table whose value is
in its age. Pruning happens in `retention.run_once()` rather than in `db.prune()`:
that function is about measurements, and the trail is not a measurement but the
memory of who did what.

Where you see it:

| Page | Shows |
|---|---|
| `GET /admin/repeaters/{rid}` | The last 15 lines for **this node** — the question is asked while you are looking at the node |
| `GET /admin/server` `#trail` | The last 40 lines for the whole installation |
| `GET /admin/audit` | The full trail, server administrators only |
| `GET /admin/account` | Your own last 20 lines |

## Migrating an existing installation

The upgrade is additive and designed around one hard requirement: **it must not
lock the owner out.**

`is_superuser` is added with `DEFAULT 0`, deliberately, because a column that
defaults to "full rights" fails the wrong way — an `INSERT` that forgets the
column would silently produce a server administrator. `ALTER TABLE ADD COLUMN`
fills existing rows with that default, which on its own would strip every
existing administrator of everything. `db.POST_MIGRATIONS` corrects that at the
moment the column is created, and only then:

```sql
UPDATE admins SET is_superuser=1
```

Bound to the creation of the column rather than to "is there a server
administrator yet", because the latter would look again on every start — and then
an administrator who deliberately demotes themselves is promoted back on the next
restart.

So: whoever could do everything yesterday can do everything today, with the same
password and the same session. Two tests guard it, one of which walks the whole
chain on a database that knows only the old two-column `admins` table.

Nothing else changes on upgrade. There are no groups and no grants yet, which
means an ordinary user — and there are none yet either — would see nothing. That
is exactly the state before this model.

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
| `GET /admin/account` | **My account** — your own password, the roles you hold, and your own audit lines |
| `GET /admin/audit` | The full audit trail (server administrators only) |

**Server and site** is the one tab this site *hides* rather than disabling.
Behind it there is not a single action an ordinary user may perform, and a tab
that always answers 403 is a closed door with a sign on it rather than an
explanation. Inside a page where you *may* do something the rule is the other way
round: buttons stay, disabled, with the reason.

`GET /admin/account` exists because the password form used to live on **Server
and site**. Since that page is for server administrators only, a user with rights
on two nodes could otherwise no longer change their own password.

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

### Naming the channels — `#kanalen`

A sensor node's telemetry arrives as CayenneLPP, and that format is a run of
triples: channel number, type, value. There is **no name field**, not in the format
and not in MeshCore, which only has an incrementing channel counter. So what a node
sends is literally "channel 6, switch, 1" and never "google is reachable". This form
is where that mapping is supplied; it is not a convenience, it is the only route.

The form lists only the channels the node has genuinely reported — read from
`latest`, not from a list somebody had to fill in first, because which channels a
node has is known only to that node. A channel appears as soon as one measurement
of it has arrived. Each row takes a name and, for a generic sensor only, a unit:
`LPP_GENERIC_SENSOR` is four unsigned bytes with multiplier 1 and promises nothing
about *what* it measures, so `12` without `ms` after it is a number without a
meaning. A voltage and a temperature already have their unit from the LPP type, and
a switch should not have one.

The channel number travels **in the field name** (`ch_naam_<N>`), never as a row
index, and a number the node has not reported is refused. Names and units are
written per channel in one action, because a row holds both and writing them
separately would let the second erase the first.

It posts to `/admin/repeaters/{rid}/channels` and needs `node.hernoemen` — this is
naming and nothing else: no packet goes out, the node never notices, and it is the
same kind of act as renaming the node itself, one layer down.

> **Why channel numbers must never shift.** The stored name hangs off the number,
> because that is the only thing the packet carries. If the sending side drops a
> service and the rest move up, every name here silently points at the wrong
> service: no error, just wrong figures. A gap in the numbering is therefore not
> untidiness to be cleaned up — it is the evidence that nothing moved. The page says
> this in as many words, so that a later reader does not get the idea of tidying the
> gaps away.

An empty name clears it. The channel then shows as "kanaal N" on the public page
and does **not** disappear: an unnamed measurement is still a measurement.

### Management over IP — `#eigen-api`

For a node that offers its own HTTP API: a MeshUptime sensor node. The address
goes in here, and from then on the server reads `/status.json` every five minutes
and the buttons in this block work — advert (flood or zerohop), set the clock,
set the region, restart, and the settings from `/cfg.json`.

**The block says out loud that this is IP and not the mesh**, and what that means:
if the WiFi drops, this whole block drops with it. That is measured and not
theoretical, and the mesh route meant for that case does not work yet. The page
states both instead of hiding the second.

A separate field beside the *management address* on the firmware page, on purpose:
that field means "our repeater firmware lives there", with a firmware upgrade
behind it, and this node does not run that firmware. One field for both would
offer an image to a board whose build environment we do not know.

**Filling in the address requires a server administrator.** Clearing it does not.
The server sends the credentials that open every node to that address, and
`node.beheeradres` is delegatable per node — see
[`security.md`](security.md#where-the-fleet-credentials-may-go).

The block also shows the node's **access list**, read-only. That is where the mesh
route breaks: a monitor that logs in and gets no answer is usually not in that
list and has no admin password either. Then "no answer" is a refusal and not an
outage, and that is a different problem.

### Alerts — `#alarmen`

**Telemetry is polling; an alert is a trap.** The figures and graphs of a node come
from a poll round: regular, complete, and blind to what happened between two
rounds. An alert arrives the moment something happens, carries one fact, and may
not arrive at all — the sensor node sends it to the repeaters that hold alert
rights in its access list, and they publish it immediately.

Per alert: the time, the text, the severity where it can be derived from the text,
and the source (`mesh`, `ip` or `test`). Unacknowledged alerts are counted in a
badge on the node list as well, because the point of a trap is that you see it
without looking for it.

**Acknowledging does not remove an alert** — it records that somebody saw it. There
is deliberately no button that deletes one: a message you can click away without a
trace is a message that cannot be recounted afterwards. Removal is the retention's
job, together with the rest of the history. There is one button for a single alert
and one for all open alerts of a node, because a node that was unreachable for an
hour produces dozens of rows, and clicking those away one by one means nobody does
it.

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

### The packet filter — `#pakketfilter`

Which of *other people's* packets this repeater still forwards. The block is
always there, even for a node the server cannot reach over IP, because its first
job is to answer "is a filter running here" — and that answer must not depend on
whether the node happens to be online right now.

Three sources, three questions, deliberately not merged:

| Context key | Comes from | Answers |
|---|---|---|
| `filter_seen` | `repeater_filter`, filled from the last statistics message | Is a filter on, and what has it thrown away, by reason |
| `filter_live` | `GET /api/filter` on the node itself | What are the rules right now — the tables that are too large to ride along in every message |
| `filter_route` | `pktfilter.filter_route(rep)` | May and can this site change them |

Merging them produces exactly one kind of bug: a page claiming no filter is
running because the node did not answer this second.

`filter_seen` has **three** states and not two. "Never reported anything" —
usually firmware older than 2.3.0 — is not a claim that no filter is on. A node
that reports `uit (veilige modus)` is a third: it restarted repeatedly, so it
left its own filter off for this boot, and the rules are still stored.

Writes go to `/admin/repeaters/{rid}/filter`, one form per action. The command is
assembled from a hidden field plus numeric inputs with their own min and max —
there is deliberately no text box you can type a whole command line into, because
then the risk weighting would depend on how somebody happened to spell it.

The risk tier follows what the rule *blocks*, not what the form looks like.
`hops 05 4` and `hops 05 0` are the same input box and two different permissions;
the second stops group text entirely and asks for the node's name. `filter on`
weighs heavier when such a rule is already stored, because that click is the one
that actually silences the traffic.

The way back is the *cheapest* action: `off` and `reset` are `node.filter.gewoon`,
lighter than switching on. A role that may not enable a filter may still disable
one. And the real fallback does not run through this page at all — `filter off`
over the mesh CLI needs no WiFi, no admin page and no server. See
[`packet-filter.md`](packet-filter.md).

The audit trail records the sentence, not the command line: "GRP_TXT (05)
helemaal niet meer doorsturen" is still readable in six months; `hops 05 0` is
not.

## Server and site — `GET /admin/server`

| Anchor | Block | Contents |
|---|---|---|
| `#toegang` | Access | Who you are signed in as, and a link to **My account** for the password |
| `#gebruikers` | Users | Accounts, the server-administrator and disabled flags, setting a password for someone else, deleting |
| `#groepen` | Groups | User groups and node groups, their members, and the count of nodes in no group |
| `#toekenningen` | Grants | Who may do what on which nodes, and the conflict rule spelled out |
| `#trail` | Audit trail | The last 40 lines, with a link to the full trail |
| `#tokens` | API tokens | Active tokens with `created_at`, `created_by` and `last_used`; create and revoke |
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

- **No self-service.** There is no sign-up, no password reset by e-mail and no
  "forgot my password" link. A server administrator sets a password for someone
  else without being able to read it back; the way in when *nobody* can log in is
  the command line.
- **No per-action permissions.** Roles are ceilings on a risk class, not a matrix
  of checkboxes. See [Roles are ceilings](#roles-are-ceilings).
- **No node-scoped API tokens.** A token is not a user; see [API tokens and this
  model](#api-tokens-and-this-model).
- **No deleting audit lines.** Not from the site. They age out on their own
  retention.
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
