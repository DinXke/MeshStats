# Deployment

*[Nederlands](nl/deployment.md)*

Two supported ways to run the server: Docker Compose (recommended, brings its own
broker) and a systemd service on Debian/Ubuntu.

## Docker Compose

```bash
git clone https://github.com/DinXke/MeshStats.git
cd MeshStats
cp .env.example .env          # edit passwords
./mosquitto/init-passwd.sh    # creates the MQTT user
docker compose up -d
```

The site is on port **8080**. On first start an admin account is created and the
password is printed once:

```bash
docker compose logs meshmanager | grep -i password
```

Log in at `/admin`, change the password, and create an API token if you also want
to push over HTTP.

### Services

| Service | Image | Port | Volumes |
|---|---|---|---|
| `meshmanager` | built from `./server` | `${MESHMANAGER_PORT:-8080}` → 8080 | `meshstats-data:/data` |
| `mosquitto` | `eclipse-mosquitto:2` | `${MQTT_PORT:-1883}` → 1883 | config (ro), `mosquitto-data`, `mosquitto-log` |
| `victoria` | `victoriametrics/victoria-metrics` | none (internal only) | `victoria-data:/victoria-metrics-data` |

`meshmanager` declares `depends_on: [mosquitto, victoria]`. That controls start
order only, not readiness — both clients retry on their own, so a dependency that
is not up yet is not a problem.

`victoria` publishes **no host port**, deliberately: only the application talks
to it, over the compose network. It has no authentication of its own, so
publishing it would hand anyone on the network the write endpoint.

The application container has a healthcheck that fetches `/` every 30 s. There is
**no dedicated `/health` endpoint**; the check uses the public index page.

The container runs as **root** (no `USER` directive in the Dockerfile). If that
matters in your environment, add `user:` to the compose service and make sure the
`/data` volume is writable by that uid.

### Persistence

Everything the server needs lives in `/data`:

| Path | What |
|---|---|
| `/data/meshmanager.sqlite3` | The database (plus WAL files). An existing `mcs.sqlite3` is used **where it is** and never renamed — renaming is one-way traffic, and somebody rolling back to the previous version would find no database and an empty site |
| `/data/secret.key` | 32 random bytes, generated on first start, `chmod 0600` |

Back up both. **Losing `secret.key` invalidates every session cookie and every
CSRF token** — everyone is logged out. It does not invalidate API tokens, which
are hashed with plain SHA-256.

The measurements are **not** in there. They live in the `victoria-data` volume,
which needs backing up separately; see [Time-series database](#time-series-database).

## Without Docker

```bash
sudo bash deploy/install.sh
```

On Debian/Ubuntu this:

1. installs `python3`, `python3-venv`, `rsync`
2. creates a system user `mcstats`
3. rsyncs `server/` to `/opt/meshmanager/server`
4. builds a venv and installs `requirements.txt`
5. installs and starts `meshmanager.service`

Data lives in `/var/lib/meshmanager`. The unit runs with
`NoNewPrivileges`, `ProtectSystem=strict`, `ProtectHome`, `PrivateTmp`, and
`ReadWritePaths` limited to the data directory.

The first-start password goes to the journal:

```bash
journalctl -u meshmanager | grep -i password
```

> **The systemd unit sets no `MM_MQTT_*` variables, so MQTT ingest is off in
> this deployment.** Add them with a drop-in and bring your own broker:
>
> ```bash
> sudo systemctl edit meshmanager
> ```
> ```ini
> [Service]
> Environment=MM_MQTT_HOST=127.0.0.1
> Environment=MM_MQTT_USER=meshmanager
> Environment=MM_MQTT_PASS=...
> ```
>
> Prefer an `EnvironmentFile=` with mode `0600` over inline `Environment=` lines,
> which are readable via `systemctl show`.

Re-running `install.sh` upgrades in place. It uses `rsync --delete`, so anything
you added under `/opt/meshmanager/server` is removed.

## Environment variables

Every variable is `MM_<NAME>`. The old `MM_<NAME>` spelling is **still read**
as a fallback (`config.env()`), so an existing `.env` keeps working across the
rename: whoever has an installation running should not have to rewrite a
configuration file before the site comes back up. When both are set the new name
wins, and an explicitly empty value counts as an answer rather than as silence —
`MM_TSDB_URL=` means "no time-series database", and the old name must not
quietly override it.

`MM_` rather than `MESHMANAGER_` because the old prefix was an initialism too
(MCS = MeshCore Stats), and an eleven-character prefix pushes the lines in
`.env` past the eighty columns the rest of this project keeps to.

The fallback may go once every installation using this repository has restarted
at least once with the new names — practically, leave it until the next major
version.

### Application

| Variable | Default | Meaning |
|---|---|---|
| `MM_DATA_DIR` | `server/data` | Where the database and secret key live. Docker sets `/data`; systemd sets `/var/lib/meshmanager`. |
| `MM_MQTT_PREFIX` | `meshmanager` | The MQTT topic prefix this installation owns. The old `meshcore` prefix is subscribed to as well, for as long as unflashed nodes still publish under it. |
| `MM_MQTT_QUIET_MIN` | `90` | Minutes of silence after which `/admin` reports the ingest as connected but receiving nothing. Deliberately generous: a node on solar publishes at most hourly at night, and a warning that fires every night is one everybody learns to ignore. |
| `MM_SITE_NAME` | `MeshManager` | Title in the header |
| `MM_RETENTION_DAYS` | `180` | Sample retention. Overridden by the DB setting if changed in `/admin`. |
| `MM_HEARTBEAT_MIN` | `5` | Minutes; force a graph point even when the value has not changed. Also overridable in `/admin`. |
| `MM_PACKET_RETENTION_DAYS` | `7` | Raw packet retention; they arrive far faster than samples. Overridable in `/admin`, and it is also the heat map's window. |
| `MM_PACKET_MAX_ROWS` | `200000` | FIFO ceiling on the packets table: above it the oldest packets go, whatever the retention says. Overridable in `/admin`. |
| `MM_DB_MAX_MB` | `512` | FIFO ceiling on the database file, WAL included. Above it, more of the oldest packets go. Overridable in `/admin`. |
| `MM_PRUNE_MINUTES` | `60` | Minutes between retention passes. Pruning also happens at startup, but a server that runs for months has to prune in between. |
| `MM_MAX_BODY_BYTES` | `2000000` | Largest request body accepted, on every route and method. Enforced while reading, so a chunked request cannot skip it. |
| `MM_TRUSTED_PROXY_HOPS` | `1` | How many proxies sit in front of the app. The login throttle counts this many `X-Forwarded-For` entries in from the right to find the client address. Raise it only when you really add a hop — see [`security.md`](security.md#which-address-gets-counted). |
| `MM_BUILD_SHA`, `MM_BUILD_DATE` | *(empty)* | The git commit and build date baked into the image (Docker `ARG` → `ENV`). Not something you set in `.env`: `deploy/autoupdate.sh` passes them as build args, a manual build may pass them too, and an image built without them shows `dev`. See *Which version is running?* below. |

### Which version is running?

Every page carries a stamp in its footer — `v2.10.0 · 399de16 · 2026-09-04` —
and `GET /api/v1/ping` returns the same as `app_version` and `build`. The
container's journal opens with it as well. Two numbers, on purpose
(`server/app/version.py`):

- **`VERSION`** is the site's semantic version, bumped by hand for a change a
  user notices (MINOR), a fix (PATCH) or a break in the API or database
  (MAJOR), each with a line in [`CHANGELOG.md`](../CHANGELOG.md). It started at
  2.10.0 rather than 1.0.0: the site and the node firmware in this repository
  are one generation ("2" = MeshManager, since the rename from MeshStats) and
  were at the same number when the stamp was introduced. It is the number for
  people.
- **commit · date** is what the image was actually built from. Two sites at the
  same `VERSION` can run different commits, and then the commit is the only thing
  that tells them apart. It is the number for finding a bug.

The commit is baked in at build time: `deploy/autoupdate.sh` exports
`MM_BUILD_SHA=$(git rev-parse --short HEAD)` and `MM_BUILD_DATE=$(date -u +%F)`
before `docker compose build`, and `docker-compose.yml` hands them to the
`Dockerfile` as build args. Building by hand:

```bash
MM_BUILD_SHA=$(git rev-parse --short HEAD) MM_BUILD_DATE=$(date -u +%F) docker compose build
docker compose up -d
```

An image built without the arguments shows `dev` — true, and deliberately not
a made-up value. Outside Docker the module asks `git` itself, so a development
checkout shows its real commit.

### MQTT

| Variable | Default (code) | Default (compose) |
|---|---|---|
| `MM_MQTT_HOST` | *(empty — MQTT off)* | `mosquitto` |
| `MM_MQTT_PORT` | `1883` | `1883` |
| `MM_MQTT_USER` | *(empty)* | `meshmanager` |
| `MM_MQTT_PASS` | *(empty)* | from `.env` |
| `MM_MQTT_TOPIC` | *(empty — `<prefix>/+/stats` for every prefix)* | same |
| `MM_MQTT_RX_TOPIC` | *(empty — `<prefix>/+/rx` for every prefix)* | same |
| `MM_MQTT_CMD_TOPIC` | `{prefix}/{node}/cmd` | same |

The site subscribes to `meshmanager/+/stats` and `meshmanager/+/rx`, and to the
same two patterns under the legacy `meshcore` prefix. A pattern set in
`MM_MQTT_TOPIC` or `MM_MQTT_RX_TOPIC` is added **on top of** those rather than
replacing them — that is the way to run under your own branch on a shared broker,
and silently dropping the defaults would deafen an installation that just
upgraded.

`MM_MQTT_CMD_TOPIC` is the only topic the site publishes on. It carries exactly
three words — `settings`, `status` and `time <epoch>` — asking a node to read its
CLI settings now, to publish a status message now, or to set its clock. It needs
a broker ACL that lets each node read its own `cmd` topic — without that the
node's subscribe is refused and nothing anywhere reports it. See
[`mqtt.md`](mqtt.md#asking-a-node-for-something) and
[`commanding.md`](commanding.md).

The code defaults and the compose defaults differ. If you run the container
outside compose, set `MM_MQTT_HOST` explicitly or ingest stays off.

Details in [`mqtt.md`](mqtt.md).

### Clock synchronisation

The site periodically publishes `time <epoch>` to every node that publishes
directly, because a MeshCore node never sets its own clock. All four variables,
and the checks the server performs on its own clock before anything leaves, are
in [`clocksync.md`](clocksync.md#configuration).

| Variable | Default | Meaning |
|---|---|---|
| `MM_CLOCKSYNC_ENABLED` | `1` | `0`, `false`, `no`, `nee`, `off` or empty switches it off |
| `MM_CLOCKSYNC_HOURS` | `24` | Hours between two rounds, minimum 1 |
| `MM_CLOCKSYNC_MAX_ERROR_S` | `10` | How much uncertainty the kernel may have about its own clock and still be believed |
| `MM_CLOCKSYNC_MAX_JUMP_S` | `30` | How far the wall clock may shift against the monotonic clock before it counts as a jump |

Requires node firmware 1.10.0 (the module this repository ships). In an LXC the clock check reads
the **host** kernel's discipline, so the correctness of every clock in the mesh
ultimately hangs on the NTP configuration of that host.

### Time-series database

| Variable | Default (code) | Default (compose) | Meaning |
|---|---|---|---|
| `MM_TSDB_URL` | *(empty — everything stays in SQLite)* | `http://victoria:8428` | Base URL of VictoriaMetrics |
| `MM_TSDB_RETENTION` | — | `180d` | Compose only; passed to the container as `-retentionPeriod` |

Same trap as MQTT: **empty is a supported configuration**, not a broken one. Run
the container outside compose without setting `MM_TSDB_URL` and the site keeps
every measurement in SQLite exactly as it did before — thinned by the heartbeat
rule, but working.

See [Time-series database](#time-series-database-1) under Operations for what to
check when it misbehaves.

### Compose only

| Variable | Default | Meaning |
|---|---|---|
| `MESHMANAGER_PORT` | `8080` | Host port for the site |
| `MQTT_PORT` | `1883` | Host port for the broker |

Most application settings are also editable in `/admin`, where they are stored in
the database and take precedence over the environment.

## Behind cloudflared or a reverse proxy

The app runs with `--proxy-headers --forwarded-allow-ips "*"`, so it honours
`X-Forwarded-Proto`. The session cookie's `Secure` flag is set from that header,
which is what makes login work correctly behind a tunnel.

Point the tunnel at `http://localhost:8080`.

> `--forwarded-allow-ips "*"` trusts `X-Forwarded-*` from **any** source. That is
> fine when only your proxy can reach port 8080. If the port is reachable
> directly, a client can spoof those headers. Bind the container to loopback
> (`127.0.0.1:8080:8080`) or firewall the port.

### cloudflared

```yaml
tunnel: <tunnel-id>
credentials-file: /etc/cloudflared/<tunnel-id>.json
ingress:
  - hostname: stats.example.com
    service: http://localhost:8080
  - service: http_status:404
```

### nginx

```nginx
server {
    listen 443 ssl http2;
    server_name stats.example.com;

    location / {
        proxy_pass http://127.0.0.1:8080;
        proxy_set_header Host              $host;
        proxy_set_header X-Real-IP         $remote_addr;
        proxy_set_header X-Forwarded-For   $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

### Caddy

```
stats.example.com {
    reverse_proxy 127.0.0.1:8080
}
```

Caddy sets the forwarded headers itself.

### What to protect

The server sets `Content-Security-Policy`, `X-Frame-Options: DENY`,
`X-Content-Type-Options: nosniff`, `Referrer-Policy` and `Permissions-Policy`
itself. It does **not** set `Strict-Transport-Security`; add it at the proxy if
you terminate TLS there.

`POST /admin/login` is throttled in the application, per client address and per
username, with an escalating lockout — see
[`security.md`](security.md#rate-limiting). That state lives in the uvicorn
process and is forgotten on restart, so if the site is public a second lock on
`/admin*` is still worth having: Cloudflare Access, an IP allowlist, or a rate
limit at the proxy. The rest of the site and `/api/v1/*` can stay open.

The throttle needs to know which address is the client's. Set
`MM_TRUSTED_PROXY_HOPS` to the number of proxies you actually put in front of
the app (default `1`); the reasoning is in
[`security.md`](security.md#which-address-gets-counted).

The MQTT port does not need to be exposed to the internet at all. If every node
is on your own network, drop the `ports:` mapping from the `mosquitto` service
and let it be reachable only on the compose network.

## Upgrading

```bash
git pull
docker compose build
docker compose up -d
```

The schema is applied with `CREATE TABLE IF NOT EXISTS` on startup; there is no
migration framework. Back up `/data` before upgrading across a schema change.

### Automatic upgrades

For a compose deployment that should track `main` on its own:

```bash
sudo bash deploy/install-autoupdate.sh
```

This installs `meshmanager-autoupdate.timer`, which runs `deploy/autoupdate.sh`
every five minutes from the clone you ran the installer in. The script fetches
`main`, exits silently when there is nothing new, and otherwise performs
exactly the manual sequence above: `git pull --ff-only`,
`docker compose build`, `docker compose up -d`.

It **polls** rather than listening for a webhook, deliberately: the server can
sit behind LAN or VPN with no inbound port at all, and a delay of at most five
minutes is not worth keeping a tunnel open for.

Failure behaviour:

- A failed build never touches the running site. `up -d` is only reached after
  `build` succeeds, so the containers keep running the previous image and the
  error lands in the journal.
- The last *successfully deployed* commit is recorded in
  `.git/autoupdate-deployed`, so a run that failed after the pull is retried on
  the next tick instead of being mistaken for done.
- The pull is `--ff-only`. A deploy clone should never have local commits; if
  it somehow does, failing loudly beats fabricating a merge on the server.
- Overlap cannot happen: the service is `Type=oneshot`, and systemd does not
  start a timer's unit again while the previous activation is still running.
  There is no lock file because none is needed.

The timer is quiet by design — only runs that found work, or hit an error,
write anything:

```bash
journalctl -u meshmanager-autoupdate -f
systemctl list-timers meshmanager-autoupdate.timer
```

The unit runs as root (docker needs it); clone the repository as root too, or
git will refuse to touch it ("dubious ownership").

This is for the compose deployment only. The systemd/venv deployment from
`install.sh` copies the code out of the repository and has no compose stack to
rebuild, so the timer does not apply there.

## Operations

### Backup

```bash
docker compose exec meshmanager \
  sqlite3 /data/meshmanager.sqlite3 ".backup '/data/backup.sqlite3'"
docker compose cp meshmanager:/data/backup.sqlite3 ./backup.sqlite3
docker compose cp meshmanager:/data/secret.key ./secret.key
```

Use `.backup` rather than copying the file — the database runs in WAL mode and a
plain copy can be inconsistent.

### Reset the admin password

```bash
docker compose exec meshmanager python -m app.main set-password admin
```

Reads the new password from stdin; minimum 8 characters.

> Changing a password **does** invalidate every session minted under the old
> one. Each cookie carries an HMAC fingerprint of the account's password hash,
> so the `admins` row is the revocation list and no session table is needed. The
> one exception is the browser that performed the change through `/admin`, which
> is handed a fresh cookie so the person who just changed the password is not
> logged out of their own admin page. Deleting `/data/secret.key` and restarting
> remains the blunt instrument: it invalidates every session **and** every CSRF
> token. Details in [`admin.md`](admin.md#sessions).

### Time-series database

The measurements live in VictoriaMetrics; SQLite keeps everything else,
including `latest`. Background and the reasoning are in
[`architecture.md`](architecture.md#where-the-measurements-live).

**Check the state** in `/admin` → *Metingen (tijdreeksen)*: reachable yes/no,
points written, queue depth, how many had to fall back to SQLite, and the last
error. Same information in the log under `meshmanager.tsdb`.

**By hand**, from the application container (the database has no host port):

```bash
# is it up?
docker compose exec meshmanager python -c \
  "import urllib.request;print(urllib.request.urlopen('http://victoria:8428/health').read())"

# which series exist -- note the explicit start/end: without them the label API
# only looks at the last few hours and you will think data is missing
docker compose exec meshmanager python -c \
  "import json,urllib.request,time;e=int(time.time());print(len(json.load(urllib.request.urlopen(
   f'http://victoria:8428/api/v1/label/__name__/values?start={e-400*86400}&end={e}'))['data']))"
```

**When it is unreachable**, nothing breaks and nothing is lost: measurements are
written to the SQLite `samples` table instead and the charts read from there
again. You lose resolution for the duration, not data. The admin page counts
those points under *Uitgeweken naar SQLite*.

**Freshly written points are not queryable instantly.** VictoriaMetrics indexes
them over the next few seconds — measured at up to about 8 s for a burst on a
cold instance. This is invisible on a chart covering hours, but it will confuse
you when testing by hand.

**Backups** are a separate job from the SQLite one:

```bash
docker compose stop victoria
docker run --rm -v meshstats_victoria-data:/from -v "$PWD":/to alpine \
  tar czf /to/victoria-backup.tgz -C /from .
docker compose start victoria
```

For a live backup without stopping, use VictoriaMetrics' own
`/snapshot/create` endpoint and copy the snapshot directory.

**Rolling back to SQLite-only** takes one variable: set `MM_TSDB_URL=` (empty)
and restart. The site returns to reading and writing `samples`. History written
to VictoriaMetrics in the meantime is not merged back, so charts will show a gap
for that period until it is switched on again.

### Disk usage

In SQLite, `samples` dominates by row count — but only as an inheritance. With a
time-series database configured it receives nothing except during an outage, so
it shrinks as its 180-day retention passes, and `packets` becomes the table that
actually grows.

Three limits hold the packets table down, applied in this order: the retention
period, a row ceiling (`MM_PACKET_MAX_ROWS`), and a ceiling on the whole
database file including its WAL (`MM_DB_MAX_MB`). Age is the aim, the two
ceilings are the promise, and when they collide the oldest packets go first. A
pruning pass runs hourly (`MM_PRUNE_MINUTES`), at startup, when the settings
are saved, on roughly every 500th HTTP ingest and every 2000 received MQTT
packets. The full reasoning, and when the file is rewritten with `VACUUM` to
actually give the space back, is in [`retention.md`](retention.md).

Whenever one of the two ceilings does the cutting, the configured period was not
met — and `/admin` says so rather than absorbing it, because a retention that is
quietly not honoured is only discovered when somebody wonders where a week of
graph went.

VictoriaMetrics keeps its own retention (`MM_TSDB_RETENTION`, 180 d) and
compresses to roughly a byte per point. A node publishing every 10 s with 100
metrics is about 315 M points a year, on the order of a few hundred MB — which is
why full resolution is affordable there and was not in SQLite.

`latest`, `contacts` and `repeater_cli` are never pruned. They are bounded by the
number of repeaters and contacts, so this is not usually a problem.

### Logs

```bash
docker compose logs -f meshmanager
docker compose logs -f mosquitto
journalctl -u meshmanager -f     # systemd
```

Logger names, so a filter can pick one out: `meshmanager.mqtt` (ingest and the one
publish topic), `meshmanager.tsdb` (the time-series writer), `meshmanager.clocksync`
(the clock rounds and their refusals), `meshmanager.retention` (pruning and
VACUUM) and `meshmanager.countries` (the border file at startup). Connection
state, counters and last error for each are also shown in `/admin`.

Two things are logged loudly on purpose, because they are the states in which a
feature stops working: a clock round refused by the clock check, and a pruning
pass in which a ceiling rather than the retention did the cutting. Both are
WARNING.

### Running the tests

```bash
cd server
pip install -r requirements-dev.txt
python -m pytest
```

`pytest.ini` sets the test directory and the import path; there is nothing else
to configure. The tests touch no network, no MQTT and no real database:
everything runs against temporary SQLite files, and `tests/conftest.py` points
the application's data directory at a throwaway directory so a test run never
creates `server/data/` in your working copy — which it otherwise would, complete
with a `secret.key`, on the first import of `app.config`.

All test vectors are built from the protocol knowledge in
[`protocol.md`](protocol.md); there is not one real, captured packet in the test
directory.

## Home Assistant components

Neither the HA integration nor the TCP proxy is part of the compose stack.

**`meshmanager`** — copy
`homeassistant/custom_components/meshmanager/` into your HA
`config/custom_components/`, restart, and add the integration. It asks for the
site URL and an API token created in `/admin`. It requires the `meshcore`
integration to be present, since it reads its entities and calls
`meshcore.execute_command`.

**`mc-proxy`** — a Home Assistant add-on, not a Docker service. Add
`https://github.com/DinXke/MeshCore-Proxy` as an add-on repository, or point HA
at the `proxy/` directory. Required option: `node_host`. It listens on 5000 and
exposes a health endpoint on 5001. Only use it if you cannot flash modified
firmware; see [`firmware.md`](firmware.md#if-you-cannot-flash).
