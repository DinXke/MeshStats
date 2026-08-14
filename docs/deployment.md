# Deployment

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
docker compose logs meshstats | grep -i password
```

Log in at `/admin`, change the password, and create an API token if you also want
to push over HTTP.

### Services

| Service | Image | Port | Volumes |
|---|---|---|---|
| `meshstats` | built from `./server` | `${MESHSTATS_PORT:-8080}` → 8080 | `meshstats-data:/data` |
| `mosquitto` | `eclipse-mosquitto:2` | `${MQTT_PORT:-1883}` → 1883 | config (ro), `mosquitto-data`, `mosquitto-log` |

`meshstats` declares `depends_on: [mosquitto]`. That controls start order only,
not readiness — the ingest thread retries on its own, so a broker that is not up
yet is not a problem.

The application container has a healthcheck that fetches `/` every 30 s. There is
**no dedicated `/health` endpoint**; the check uses the public index page.

The container runs as **root** (no `USER` directive in the Dockerfile). If that
matters in your environment, add `user:` to the compose service and make sure the
`/data` volume is writable by that uid.

### Persistence

Everything the server needs lives in `/data`:

| Path | What |
|---|---|
| `/data/mcs.sqlite3` | The database (plus WAL files) |
| `/data/secret.key` | 32 random bytes, generated on first start, `chmod 0600` |

Back up both. **Losing `secret.key` invalidates every session cookie and every
CSRF token** — everyone is logged out. It does not invalidate API tokens, which
are hashed with plain SHA-256.

## Without Docker

```bash
sudo bash deploy/install.sh
```

On Debian/Ubuntu this:

1. installs `python3`, `python3-venv`, `rsync`
2. creates a system user `mcstats`
3. rsyncs `server/` to `/opt/mc-repeater-stats/server`
4. builds a venv and installs `requirements.txt`
5. installs and starts `mc-repeater-stats.service`

Data lives in `/var/lib/mc-repeater-stats`. The unit runs with
`NoNewPrivileges`, `ProtectSystem=strict`, `ProtectHome`, `PrivateTmp`, and
`ReadWritePaths` limited to the data directory.

The first-start password goes to the journal:

```bash
journalctl -u mc-repeater-stats | grep -i password
```

> **The systemd unit sets no `MCS_MQTT_*` variables, so MQTT ingest is off in
> this deployment.** Add them with a drop-in and bring your own broker:
>
> ```bash
> sudo systemctl edit mc-repeater-stats
> ```
> ```ini
> [Service]
> Environment=MCS_MQTT_HOST=127.0.0.1
> Environment=MCS_MQTT_USER=meshstats
> Environment=MCS_MQTT_PASS=...
> ```
>
> Prefer an `EnvironmentFile=` with mode `0600` over inline `Environment=` lines,
> which are readable via `systemctl show`.

Re-running `install.sh` upgrades in place. It uses `rsync --delete`, so anything
you added under `/opt/mc-repeater-stats/server` is removed.

## Environment variables

### Application

| Variable | Default | Meaning |
|---|---|---|
| `MCS_DATA_DIR` | `server/data` | Where the database and secret key live. Docker sets `/data`; systemd sets `/var/lib/mc-repeater-stats`. |
| `MCS_SITE_NAME` | `MeshCore Repeater Stats` | Title in the header |
| `MCS_RETENTION_DAYS` | `180` | Sample retention. Overridden by the DB setting if changed in `/admin`. |
| `MCS_HEARTBEAT_MIN` | `5` | Minutes; force a graph point even when the value has not changed. Also overridable in `/admin`. |

### MQTT

| Variable | Default (code) | Default (compose) |
|---|---|---|
| `MCS_MQTT_HOST` | *(empty — MQTT off)* | `mosquitto` |
| `MCS_MQTT_PORT` | `1883` | `1883` |
| `MCS_MQTT_USER` | *(empty)* | `meshstats` |
| `MCS_MQTT_PASS` | *(empty)* | from `.env` |
| `MCS_MQTT_TOPIC` | `meshcore/+/stats` | same |
| `MCS_MQTT_RX_TOPIC` | `meshcore/+/rx` | same |

The code defaults and the compose defaults differ. If you run the container
outside compose, set `MCS_MQTT_HOST` explicitly or ingest stays off.

Details in [`mqtt.md`](mqtt.md).

### Compose only

| Variable | Default | Meaning |
|---|---|---|
| `MESHSTATS_PORT` | `8080` | Host port for the site |
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

If the site is public, put a second lock on `/admin*` — Cloudflare Access, an
IP allowlist, or a rate limit. There is **no rate limiting in the application**;
the only brute-force defence on `/admin/login` is a one-second sleep on failure.
The rest of the site and `/api/v1/*` can stay open. See
[`security.md`](security.md).

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

## Operations

### Backup

```bash
docker compose exec meshstats \
  sqlite3 /data/mcs.sqlite3 ".backup '/data/backup.sqlite3'"
docker compose cp meshstats:/data/backup.sqlite3 ./backup.sqlite3
docker compose cp meshstats:/data/secret.key ./secret.key
```

Use `.backup` rather than copying the file — the database runs in WAL mode and a
plain copy can be inconsistent.

### Reset the admin password

```bash
docker compose exec meshstats python -m app.main set-password admin
```

Reads the new password from stdin; minimum 8 characters.

> Changing the password does **not** invalidate existing sessions. Cookies are
> stateless and stay valid until they expire (12 hours). To force everyone out,
> delete `/data/secret.key` and restart — that also invalidates CSRF tokens.

### Disk usage

Samples dominate. Retention defaults to 180 days and pruning runs at startup,
when settings are saved, and on roughly every 500th HTTP ingest.

> `db.prune()` is **not** called on the MQTT path. On an MQTT-only deployment
> nothing triggers pruning except restarts and admin settings saves. If you run
> MQTT-only and never touch `/admin`, restart the container periodically or save
> the settings form occasionally.

`latest`, `contacts` and `repeater_cli` are never pruned. They are bounded by the
number of repeaters and contacts, so this is not usually a problem.

### Logs

```bash
docker compose logs -f meshstats
docker compose logs -f mosquitto
journalctl -u mc-repeater-stats -f     # systemd
```

MQTT ingest logs under the logger name `meshstats.mqtt`. Connection state,
message count and last error are also shown in `/admin`.

## Home Assistant components

Neither the HA integration nor the TCP proxy is part of the compose stack.

**`mc_repeater_stats`** — copy
`homeassistant/custom_components/mc_repeater_stats/` into your HA
`config/custom_components/`, restart, and add the integration. It asks for the
site URL and an API token created in `/admin`. It requires the `meshcore`
integration to be present, since it reads its entities and calls
`meshcore.execute_command`.

**`mc-proxy`** — a Home Assistant add-on, not a Docker service. Add
`https://github.com/DinXke/MeshCore-Proxy` as an add-on repository, or point HA
at the `proxy/` directory. Required option: `node_host`. It listens on 5000 and
exposes a health endpoint on 5001. Only use it if you cannot flash modified
firmware; see [`firmware.md`](firmware.md#if-you-cannot-flash).
