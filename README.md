# MeshStats

**Publieke statistiekensite voor [MeshCore](https://meshcore.co.uk)-repeaters, gevoed door de node zelf.**

Een MeshCore-companion node verzamelt al voortdurend gegevens over zichzelf en
over de repeaters die hij hoort. MeshStats maakt daar een publieke site van:
live cijfers, historiek, een kaart met alle verbindingen en een beheerdersdeel.

```
   Heltec/ESP32 companion ──MQTT──▶ Mosquitto ──▶ MeshStats ──▶ publieke site
     (of Home Assistant) ──HTTP───────────────────▶   (SQLite)      + kaart
```

De node stuurt zijn gegevens rechtstreeks door — Home Assistant is niet nodig,
maar kan wel (zie [`homeassistant/`](homeassistant/)).

---

## Onderdelen

| Map | Wat |
|---|---|
| [`server/`](server/) | De website: FastAPI + SQLite, publieke pagina's, beheer, ingest-API en MQTT-abonnee |
| [`firmware/`](firmware/) | Aanpassingen aan de MeshCore-firmware: meerdere companions tegelijk, eigen beheerpagina en het doorsturen van statistieken |
| [`homeassistant/`](homeassistant/) | Optionele HA-integratie die repeaterdata naar de site pusht |
| [`proxy/`](proxy/) | Optionele TCP-proxy voor wie de firmware niet kan aanpassen |

## Snel starten (Docker)

```bash
git clone https://github.com/DinXke/MeshStats.git
cd MeshStats
cp .env.example .env          # pas wachtwoorden aan
./mosquitto/init-passwd.sh    # maakt de MQTT-gebruiker aan
docker compose up -d
```

De site draait daarna op poort **8080**. Bij de eerste start wordt een
admin-account aangemaakt; het wachtwoord staat in de log:

```bash
docker compose logs meshstats | grep Wachtwoord
```

Log in op `/admin`, wijzig het wachtwoord en maak een **API-token** aan als je
ook via HTTP wil pushen.

### Zonder Docker

```bash
sudo bash deploy/install.sh     # Debian/Ubuntu, systemd-service op poort 8080
```

## Wat de site toont

**Publiek** — per repeater: status, batterij en solar (met meters en een
thermometer), berichten, airtime, buren met SNR en een linkkaart. Elke tegel en
elke buurlink is aanklikbaar voor historiek (4 u tot 90 d). Blokken zijn
inklapbaar en die voorkeur wordt per bezoeker onthouden; er is een licht en een
donker thema.

**Beheer** (`/admin`) — repeaters tonen/verbergen/hernoemen, API-tokens,
bewaartermijn en meetinterval, de indeling van de publieke pagina verslepen, en
per repeater een readonly overzicht van de CLI-instellingen.

## Hoe gegevens binnenkomen

**MQTT (aanbevolen voor nodes).** De node houdt één verbinding open en
publiceert naar `meshcore/<prefix>/stats`. Veel lichter dan HTTP: geen TLS-stack
en geen nieuwe sessie per meting — precies wat een ESP32 aankan.

**HTTP.** `POST /api/v1/ingest` met `Authorization: Bearer <token>`:

```json
{
  "repeater": {"pubkey_prefix": "e3d3f4d7ed", "name": "BE-HSS-JessaZH.VIR"},
  "metrics": {"bat": 4.15, "battery_percentage": 96.4, "online": true},
  "neighbors": [{"prefix": "2ae7af", "name": "BE-LUM-Lummen C-ESP", "snr": -4.25}]
}
```

Beide wegen delen dezelfde verwerking. Onbekende repeaters verschijnen
automatisch (standaard publiek, via `/admin` te verbergen); onbekende metrics
komen in de sectie "Overig".

## API

| Endpoint | Auth | Beschrijving |
|---|---|---|
| `GET /api/v1/ping` | Bearer | Verbindingstest |
| `POST /api/v1/ingest` | Bearer | Snapshot van één repeater |
| `POST /api/v1/contacts` | Bearer | Locaties uit de adverts (voor de kaart) |
| `POST /api/v1/repeater_settings` | Bearer | CLI-instellingen van een repeater |
| `GET /api/v1/commands` | Bearer | Openstaande opdrachten (clear-on-read) |
| `GET /api/v1/repeaters` | — | Publieke repeaters met kerncijfers |
| `GET /api/v1/repeaters/{slug}` | — | Alle actuele waarden + buren |
| `GET /api/v1/repeaters/{slug}/history?metric=bat&hours=24` | — | Historiek |
| `GET /api/v1/repeaters/{slug}/map` | — | Kaartgegevens |

## Instellingen

| Variabele | Standaard | Betekenis |
|---|---|---|
| `MCS_DATA_DIR` | `/data` | Waar SQLite en de sleutel staan |
| `MCS_SITE_NAME` | MeshCore Repeater Stats | Naam in de kop |
| `MCS_RETENTION_DAYS` | 180 | Bewaartermijn historiek |
| `MCS_HEARTBEAT_MIN` | 5 | Minstens elke X minuten een grafiekpunt |
| `MCS_MQTT_HOST` | *(leeg)* | Broker; leeg = MQTT uit |
| `MCS_MQTT_PORT` / `_USER` / `_PASS` | 1883 | Verbinding met de broker |
| `MCS_MQTT_TOPIC` | `meshcore/+/stats` | Waarop de site luistert |

De meeste hiervan zijn ook via `/admin` aan te passen.

## Achter cloudflared of een reverse proxy

De app draait met `--proxy-headers` en herkent `X-Forwarded-Proto`, dus cookies
werken correct achter een tunnel. Wijs de tunnel naar `http://localhost:8080`.

Zet bij publieke ontsluiting een extra slot op `/admin*` (bijvoorbeeld
Cloudflare Access of een rate-limit) — de rest van de site en `/api/v1/*` mogen
open.

## Beveiliging

- Wachtwoorden met PBKDF2-SHA256 (200k iteraties), tokens alleen als SHA-256-hash opgeslagen
- Sessies HMAC-getekend, httponly, Secure achter een proxy; CSRF-controle op elke beheeractie
- CSP, `X-Frame-Options: DENY`, nosniff, Referrer-Policy en een limiet op de payloadgrootte
- De site kent geen enkel adres of wachtwoord van je mesh: gegevens stromen er
  alleen naartoe. Zelfs bij volledige compromittering van de site kan niemand
  daarlangs je nodes bedienen.

## Licentie

[MIT](LICENSE). Geen band met het MeshCore-project.
