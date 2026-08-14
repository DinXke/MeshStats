# MeshStats

**A public statistics site for [MeshCore](https://meshcore.co.uk) repeaters, fed
by the node itself.**

A MeshCore companion node already tracks a lot about itself and about the
repeaters it hears. MeshStats turns that into a public site: live figures,
history, a link map, and an admin area.

```
  Heltec / ESP32 node ──MQTT──▶ Mosquitto ──▶ MeshStats ──▶ public site
   (or Home Assistant) ──HTTP──────────────▶  (SQLite)      + map
```

The node pushes its own data. Home Assistant is optional, not required.

---

## Components

| Directory | What |
|---|---|
| [`server/`](server/) | The site: FastAPI + SQLite, public pages, admin, ingest API, MQTT subscriber |
| [`firmware/`](firmware/) | MeshCore firmware changes: multiple companions at once, a management page, stats publishing |
| [`homeassistant/`](homeassistant/) | Optional HA integration that pushes repeater data to the site |
| [`proxy/`](proxy/) | Optional TCP fan-out proxy, for when you cannot flash modified firmware |

## Quick start (Docker)

```bash
git clone https://github.com/DinXke/MeshStats.git
cd MeshStats
cp .env.example .env          # edit passwords
./mosquitto/init-passwd.sh    # creates the MQTT user
docker compose up -d
```

The site runs on port **8080**. An admin account is created on first start and
the password is printed once:

```bash
docker compose logs meshstats | grep -i password
```

Log in at `/admin`, change the password, and create an API token if you also want
to push over HTTP.

Without Docker: `sudo bash deploy/install.sh` (Debian/Ubuntu, systemd, port 8080).

Full instructions, reverse proxies and operations: [`docs/deployment.md`](docs/deployment.md).

## Documentation

| Document | What is in it |
|---|---|
| [`docs/architecture.md`](docs/architecture.md) | How the pieces fit together, and why MQTT replaced HTTP |
| [`docs/protocol.md`](docs/protocol.md) | The MeshCore over-the-air packet format and the companion TCP protocol, fully specified |
| [`docs/mqtt.md`](docs/mqtt.md) | Topics, payload schemas, retention, broker setup |
| [`docs/firmware.md`](docs/firmware.md) | What was changed in the firmware, why, and how to build and flash it |
| [`docs/deployment.md`](docs/deployment.md) | Docker Compose, environment variables, running behind a reverse proxy |
| [`docs/security.md`](docs/security.md) | Threat model, what is protected how, and what is not |

[`docs/protocol.md`](docs/protocol.md) is the one worth reading even if you never
run this project. The MeshCore wire format is documented nowhere else; that
document is a byte-level specification with worked examples, reconstructed from
the firmware source.

## What the site shows

**Public** — per repeater: status, battery and solar (with gauges and a
thermometer), message counters, airtime, neighbours with SNR, and a link map.
Every tile and every neighbour link opens its history (4 hours to 90 days).
Blocks collapse, and the preference is remembered per visitor. Light and dark
themes.

**Admin** (`/admin`) — show, hide and rename repeaters; API tokens; retention and
sample interval; drag the public page layout into order; a read-only view of each
repeater's CLI settings.

## How data gets in

**MQTT (recommended for nodes).** The node keeps one connection open and
publishes to `meshcore/<prefix>/stats`. Much lighter than HTTP: no TLS stack and
no new session per measurement — which is what an ESP32 running mesh, WiFi and
BLE can actually sustain. See [`docs/mqtt.md`](docs/mqtt.md).

**HTTP.** `POST /api/v1/ingest` with `Authorization: Bearer <token>`:

```json
{
  "repeater": {"pubkey_prefix": "e3d3f4d7ed", "name": "BE-HSS-JessaZH.VIR"},
  "metrics": {"bat": 4.15, "battery_percentage": 96.4, "online": true},
  "neighbors": [{"prefix": "2ae7af", "name": "BE-LUM-Lummen C-ESP", "snr": -4.25}]
}
```

Both paths share the same handler. Unknown repeaters appear automatically
(public by default — hide them in `/admin`) and unknown metrics land in the
"other" section rather than being rejected.

## API

| Endpoint | Auth | Description |
|---|---|---|
| `GET /api/v1/ping` | Bearer | Connectivity test |
| `POST /api/v1/ingest` | Bearer | Snapshot of one repeater |
| `POST /api/v1/contacts` | Bearer | Locations from adverts, for the map |
| `POST /api/v1/repeater_settings` | Bearer | A repeater's CLI settings |
| `GET /api/v1/commands` | Bearer | Pending requests (clear-on-read) |
| `GET /api/v1/repeaters` | — | Public repeaters with headline figures |
| `GET /api/v1/repeaters/{slug}` | — | All current values plus neighbours |
| `GET /api/v1/repeaters/{slug}/history?metric=bat&hours=24` | — | History |
| `GET /api/v1/repeaters/{slug}/map` | — | Map data |

## Settings

| Variable | Default | Meaning |
|---|---|---|
| `MCS_DATA_DIR` | `/data` | Where SQLite and the secret key live |
| `MCS_SITE_NAME` | MeshCore Repeater Stats | Name in the header |
| `MCS_RETENTION_DAYS` | 180 | History retention |
| `MCS_HEARTBEAT_MIN` | 5 | Force a graph point at least every X minutes |
| `MCS_MQTT_HOST` | *(empty)* | Broker; empty disables MQTT |
| `MCS_MQTT_PORT` / `_USER` / `_PASS` | 1883 | Broker connection |
| `MCS_MQTT_TOPIC` | `meshcore/+/stats` | What the site listens to |

Most of these are also editable in `/admin`, where the database value wins.
Full list: [`docs/deployment.md`](docs/deployment.md#environment-variables).

## Security in one paragraph

Passwords are PBKDF2-SHA256 (200k iterations); API tokens are stored only as
SHA-256 hashes; sessions are HMAC-signed, `HttpOnly` and `Secure` behind a proxy;
every admin action is CSRF-checked; CSP and the usual headers are set. **The site
knows no address and no password of your mesh** — data only ever flows towards
it, so even a fully compromised site cannot drive your nodes. Two things deserve
your attention before going public: there is no rate limiting, and a node
filesystem backup contains that node's **private key**. Read
[`docs/security.md`](docs/security.md).

## Status

Working and in use. In development or planned, and not yet usable:

- Raw-packet forwarding over MQTT (**in development**)
- A live map fed from forwarded packets (**planned**)
- A full web client on the companion node (**planned**)

## Licence

[MIT](LICENSE). Not affiliated with the MeshCore project.
