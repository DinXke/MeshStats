# MeshStats

*[Nederlands](README.nl.md)*

**A public statistics and analysis site for [MeshCore](https://meshcore.co.uk)
repeaters, fed by the nodes themselves — with the firmware to do it.**

A MeshCore node already knows a great deal: about itself, about the repeaters it
hears, and about every packet that passes its antenna. MeshStats turns that into
a public site — live figures, a live map, a searchable packet archive, history
and a link map — and ships the firmware changes that let a node publish it
without anything in between.

```
  Heltec / ESP32 node ──MQTT──▶ Mosquitto ──▶ MeshStats ──▶ public site
   (or Home Assistant) ──HTTP──────────────▶  (SQLite)      + live map
```

The node pushes its own data. Home Assistant is optional, and no longer the
recommended path.

---

## What the site can do

**Live map.** Every node with an advertised position, drawn on a map, with the
packets currently moving between them. A heatmap over the paths shows which
links actually carry the mesh — not which links exist, but which ones are used.
Click a dot and a panel says everything known about that node: how much traffic
is attributable to it, who hears it, how often it turns up as a hop in somebody
else's path.

**Packet archive.** Every packet the mesh's observers heard, searchable with a
Kibana-style query bar:

```
type:ADVERT scope:scoped           snr:>5  len:20..40
sender:2ae7*  -type:ACK            type:(ADVERT OR TXT_MSG)
```

Sortable on every visible column, with a readable detail panel per packet and
plus/minus filter buttons on every value in it. Unparseable input is an error
with an explanation, never a clause silently dropped.

**Packet detail.** The decoded frame, the resolved path, and the raw bytes. It
names which hash it is showing — address hash or path hash — and how big it is,
because those are two different things that are constantly confused.

**Repeater statistics.** Per repeater: status, battery and solar with gauges and
a thermometer, message counters, airtime, neighbours with SNR, and a link map.
Every tile and every neighbour link opens its own history, from four hours to
ninety days. Blocks collapse and the preference is remembered per visitor.

**Settings over LoRa.** Ask a repeater to read back its own CLI settings — region,
hash mode, transmit power — and see them on the site. Read-only: the site can
request values, never write them.

**Clock synchronisation.** The site can set a node's clock over MQTT, and a
monitoring node can set the clocks of the repeaters it looks after over LoRa.
Only forwards, never backwards, because a node that sets its clock back
invalidates its own adverts for everyone who already knows it.

**Two languages, two themes.** Dutch and English, light and dark, chosen in the
browser with no server involvement.

**Admin.** Show, hide, rename and reorder repeaters; API tokens; retention and
sample interval; a read-only view of each repeater's CLI settings.

---

## Components

| Directory | What it is |
|---|---|
| [`server/`](server/) | The site: FastAPI + SQLite. Public pages, admin, ingest API, MQTT subscriber, packet decoder, search |
| [`firmware/`](firmware/) | MeshCore firmware changes: several companions on one node at once, the stats publisher, and the repeater's network module with a management page and OTA |
| [`mosquitto/`](mosquitto/) | Broker configuration for the Docker deployment, with one account per node and an ACL that enforces who may publish where |
| [`deploy/`](deploy/) | Installation without Docker (venv + systemd), and an auto-update timer for the Compose deployment |
| [`homeassistant/`](homeassistant/) | Optional HA integration. Since nodes publish over MQTT themselves it is no longer required — it still supplies map positions from adverts and fetches repeater CLI settings over LoRa |
| [`proxy/`](proxy/) | Optional TCP fan-out proxy, for when you cannot flash modified firmware and still want more than one client on a node |

---

## Quick start

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

Log in at `/admin`, change the password, and create an API token if you also
want to push over HTTP. Then point a node at the broker — see
[`docs/mqtt.md`](docs/mqtt.md) — or flash the MeshStats firmware from
[`docs/firmware.md`](docs/firmware.md).

Without Docker: `sudo bash deploy/install.sh` (Debian/Ubuntu, systemd, port
8080). Full instructions, reverse proxies, backups and automatic upgrades:
[`docs/deployment.md`](docs/deployment.md).

---

## Documentation

**[`docs/README.md`](docs/README.md) is the index** — every document, grouped by
what you are trying to do, each with a sentence saying what is in it. All of it
exists in [English](docs/README.md) and in [Dutch](docs/nl/README.md).

The ones to start with:

| Document | What is in it |
|---|---|
| [`docs/architecture.md`](docs/architecture.md) | How the pieces fit together, and why MQTT replaced HTTP |
| [`docs/glossary.md`](docs/glossary.md) | The MeshCore vocabulary these documents assume |
| [`docs/protocol.md`](docs/protocol.md) | The over-the-air packet format and the companion protocol, fully specified |
| [`docs/deployment.md`](docs/deployment.md) | Installing, configuring, upgrading and operating the site |
| [`docs/contributing.md`](docs/contributing.md) | Why the code looks the way it does |

[`docs/protocol.md`](docs/protocol.md) is worth reading even if you never run
this project. The MeshCore wire format is documented nowhere else; that document
is a byte-level specification with worked examples, reconstructed from the
firmware source and cited line by line.

---

## How data gets in

**MQTT (recommended).** The node keeps one connection open and publishes to
`meshmanager/<node>/stats`. Much lighter than HTTP: no TLS stack and no new
session per measurement — which is what an ESP32 running mesh, WiFi and BLE can
actually sustain. Raw packets are forwarded on `meshmanager/<node>/rx`, which is
what feeds the live map and the archive. The older `meshcore/` prefix is
subscribed to as well, so nodes and server never have to be upgraded on the same
day.

Over that same connection the site can ask a node for something — three short
commands on `meshmanager/<node>/cmd` (`settings`, `status`, `time <epoch>`), and
nothing else is accepted there. See [`docs/mqtt.md`](docs/mqtt.md).

**HTTP.** `POST /api/v1/ingest` with `Authorization: Bearer <token>`:

```json
{
  "repeater": {"pubkey_prefix": "e3d3f4d7ed", "name": "BE-HSS-JessaZH.VIR"},
  "metrics": {"bat": 4.15, "battery_percentage": 96.4, "online": true},
  "neighbors": [{"prefix": "2ae7af", "name": "BE-LUM-Lummen C-ESP", "snr": -4.25}]
}
```

Both paths share the same handler. Unknown repeaters appear automatically —
public by default, hide them in `/admin` — and unknown metrics land in the
"other" section rather than being rejected.

Full route reference: [`docs/api.md`](docs/api.md).

---

## Settings

| Variable | Default | Meaning |
|---|---|---|
| `MM_DATA_DIR` | `server/data` | Where SQLite and the secret key live. Docker sets `/data` |
| `MM_MQTT_PREFIX` | `meshmanager` | The MQTT topic prefix this installation owns |
| `MM_SITE_NAME` | MeshCore Repeater Stats | Name in the header |
| `MM_RETENTION_DAYS` | 180 | History retention |
| `MM_PACKET_RETENTION_DAYS` | 7 | Packet archive retention, and the heatmap's window |
| `MM_PACKET_MAX_ROWS` | 200000 | Row ceiling for the archive; oldest go first |
| `MM_DB_MAX_MB` | 512 | Size ceiling for the database, WAL included |
| `MM_HEARTBEAT_MIN` | 5 | Force a graph point at least every X minutes |
| `MM_MAX_BODY_BYTES` | 2000000 | Largest request body accepted |
| `MM_TRUSTED_PROXY_HOPS` | 1 | Proxies in front of the app; the login throttle uses it to find the client address |
| `MM_MQTT_HOST` | *(empty)* | Broker; empty disables MQTT |
| `MM_MQTT_CMD_TOPIC` | `{prefix}/{node}/cmd` | The only topic the site publishes on |

Every variable is `MM_<NAME>`. The old `MCS_<NAME>` spelling is **still read** as
a fallback, so an existing `.env` keeps working. Most of these are also editable
in `/admin`, where the database value wins. Full list:
[`docs/deployment.md`](docs/deployment.md#environment-variables).

---

## Security in one paragraph

Passwords are PBKDF2-SHA256 (200k iterations); API tokens are stored only as
SHA-256 hashes; sessions are HMAC-signed, `HttpOnly` and `Secure` behind a proxy,
and a password change invalidates every one of them; the login is CSRF-checked
and throttled per address and per username; request bodies are capped while
being read; CSP and the usual headers are set. **The site knows no password of
your mesh** — the only thing it can send a node is one of three short commands,
`settings`, `status` and `time <epoch>`: two of them merely make the node publish
what it publishes anyway, and the third sets a clock. None of them configures a
radio, so even a fully compromised site cannot reconfigure your mesh. Two things
still deserve attention before going public: the login throttle lives in one
process and forgets on restart, so an access gate at the proxy is worth having,
and a node filesystem backup contains that node's **private key**. Read
[`docs/security.md`](docs/security.md).

---

## Contributing

Commits are in Dutch and carry the reasoning in the body. Comments explain why,
not what. There is no build step, migrations are additive, and the site refuses
to guess in public — when the evidence does not separate two answers, it says so
rather than picking one.

All of that is written down, with the reasons, in
[`docs/contributing.md`](docs/contributing.md). Read it before a first change.

Tests: `cd server && pip install -r requirements-dev.txt && python -m pytest`.
See [`docs/testing.md`](docs/testing.md).

---

## Licence

[MIT](LICENSE). Not affiliated with the MeshCore project.
