# MeshManager

*[English](README.md)*

**Een publieke statistieken- en analysesite voor
[MeshCore](https://meshcore.co.uk)-repeaters, gevoed door de nodes zelf — met de
firmware om dat te doen.**

Een MeshCore-node weet al heel wat: over zichzelf, over de repeaters die hij
hoort, en over elk pakket dat langs zijn antenne komt. MeshManager maakt daar een
publieke site van — live cijfers, een live kaart, een doorzoekbaar
pakketarchief, historiek en een linkkaart — en levert de
firmwareaanpassingen mee waarmee een node dat kan publiceren zonder iets
ertussen.

```
  Heltec / ESP32-node ──MQTT──▶ Mosquitto ──▶ MeshManager ──▶ publieke site
   (of Home Assistant) ──HTTP──────────────▶  (SQLite)      + live kaart
```

De node pusht zijn eigen data. Home Assistant is optioneel, en niet langer de
aanbevolen weg.

---

## Wat de site kan

**Live kaart.** Elke node met een geadverteerde positie, op een kaart, met de
pakketten die op dit moment tussen die nodes bewegen. Een heatmap over de paden
laat zien welke schakels het mesh werkelijk dragen — niet welke verbindingen
bestaan, maar welke gebruikt worden. Klik een bolletje aan en een paneel vertelt
alles wat we van die node weten: hoeveel verkeer aan hem toe te schrijven valt,
wie hem hoort, hoe vaak hij als hop in andermans pad opduikt.

**Pakketarchief.** Elk pakket dat de waarnemers van het mesh gehoord hebben,
doorzoekbaar met een Kibana-achtige zoekbalk:

```
type:ADVERT scope:scoped           snr:>5  len:20..40
sender:2ae7*  -type:ACK            type:(ADVERT OR TXT_MSG)
name:circuit*  name:*circuit       name:*circuit*
```

Sorteerbaar op elke zichtbare kolom, met een leesbaar detailvenster per pakket en
plus/min-filterknopjes bij elke waarde erin. Onbegrijpelijke invoer is een fout
met uitleg, nooit een clausule die stilletjes wegvalt.

**Pakketdetail.** Het gedecodeerde frame, het opgeloste pad, en de ruwe bytes.
Het benoemt wélke hash het toont — adreshash of padhash — en hoe groot die is,
want dat zijn twee verschillende dingen die voortdurend verward worden.

**Repeaterstatistieken.** Per repeater: status, batterij en zon met meters en een
thermometer, berichtentellers, zendtijd, buren met SNR, en een linkkaart. Elke
tegel en elke burenlink opent zijn eigen historiek, van vier uur tot negentig
dagen. Blokken klappen dicht en die voorkeur wordt per bezoeker onthouden.

**Instellingen over LoRa.** Vraag een repeater zijn eigen CLI-instellingen terug
te lezen — regio, hash mode, zendvermogen — en zie ze op de site. Alleen-lezen:
de site kan waarden opvragen, nooit schrijven.

**Pakketfilter.** Een repeater met onze firmware kan weigeren verkeer door te
sturen, op zes soorten regel — aantal hops, een snelheidslimiet per pakkettype,
minimale padhashgrootte, geblokkeerde kanalen, structureel onmogelijke
groepstekst, en hele pakkettypes. Standaard uit, vanaf de site te beheren met
drie risicoklassen, en elk weggegooid pakket wordt geteld per reden en in een
grafiek gezet. `filter off` blijft bereikbaar over de mesh-CLI, want een filter
is de ene instelling die een node nutteloos maakt zonder hem onbereikbaar te
maken. Zie [`docs/nl/packet-filter.md`](docs/nl/packet-filter.md).

**Room-server-nodes.** Een MeshUptime-room-server-node — een node met een eigen
HTTP-API — wordt vanaf de nodepagina gelezen en volledig beheerd: zijn rooms en
virtuele sensor-nodes (toevoegen, bewerken, verwijderen, met een join-/contact-QR
en link), per-sleutel-toegangsbeheer op niveaus read/readwrite/admin,
node-centrisch kanaalbeheer, handmatige adverts, en serverzijde room-backups met
versiehistoriek. Losse room- en sensor-node-entries op het mesh worden
teruggekoppeld aan de fysieke node die ze host. Zie [`docs/rooms.md`](docs/rooms.md).

**Kloksynchronisatie.** De site kan de klok van een node over MQTT zetten, en een
monitorende node kan over LoRa de klok zetten van de repeaters waar hij naar
omkijkt. Alleen vooruit, nooit achteruit, want een node die zijn klok terugzet
maakt zijn eigen adverts ongeldig voor iedereen die hem al kent.

**Twee talen, twee thema's.** Nederlands en Engels, licht en donker, gekozen in
de browser zonder dat de server eraan te pas komt.

**Beheer.** Repeaters tonen, verbergen, hernoemen en herordenen; API-tokens;
bewaartermijn en meetinterval; een alleen-lezen weergave van de CLI-instellingen
van elke repeater.

---

## Onderdelen

| Map | Wat het is |
|---|---|
| [`server/`](server/) | De site: FastAPI + SQLite. Publieke pagina's, beheer, ingest-API, MQTT-abonnee, pakketdecoder, zoeken |
| [`firmware/`](firmware/) | Aanpassingen aan de MeshCore-firmware: meerdere companions tegelijk op één node, de statspublisher, en de netwerkmodule van de repeater met beheerpagina en OTA |
| [`mosquitto/`](mosquitto/) | Brokerconfiguratie voor de Docker-deploy, met één account per node en een ACL die afdwingt wie waar mag publiceren |
| [`deploy/`](deploy/) | Installatie zonder Docker (venv + systemd), en een autoupdate-timer voor de Compose-deploy |
| [`homeassistant/`](homeassistant/) | Optionele HA-integratie. Nu nodes zelf over MQTT publiceren is hij niet meer nodig — hij levert nog wel kaartposities uit adverts en haalt CLI-instellingen van repeaters over LoRa op |
| [`proxy/`](proxy/) | Optionele TCP-fan-outproxy, voor wie geen aangepaste firmware kan flashen en toch meer dan één client op een node wil |

---

## Snelstart

```bash
git clone https://github.com/DinXke/MeshStats.git
cd MeshStats
cp .env.example .env          # wachtwoorden aanpassen
./mosquitto/init-passwd.sh    # maakt de MQTT-gebruiker aan
docker compose up -d
```

De site draait op poort **8080**. Bij de eerste start wordt een beheerdersaccount
aangemaakt en het wachtwoord één keer afgedrukt:

```bash
docker compose logs meshmanager | grep -i password
```

Log in op `/admin`, wijzig het wachtwoord, en maak een API-token aan als je ook
over HTTP wilt pushen. Dat eerste account is **serverbeheerder**; extra
gebruikers, groepen en rollen per node staan onder *Server en site* — zie
[`docs/admin.md`](docs/nl/admin.md#gebruikers-rollen-en-groepen). Wijs daarna een
node naar de broker — zie [`docs/mqtt.md`](docs/nl/mqtt.md) — of flash de
MeshManager-firmware uit
[`docs/firmware.md`](docs/nl/firmware.md).

Zonder Docker: `sudo bash deploy/install.sh` (Debian/Ubuntu, systemd, poort
8080). Volledige instructies, reverse proxies, back-ups en automatische upgrades:
[`docs/deployment.md`](docs/nl/deployment.md).

---

## Documentatie

**[`docs/nl/README.md`](docs/nl/README.md) is de inhoudsopgave** — elk document,
gegroepeerd naar wat je probeert te doen, met bij elk een zin die zegt wat erin
staat. Alles bestaat in het [Nederlands](docs/nl/README.md) en in het
[Engels](docs/README.md).

Om mee te beginnen:

| Document | Wat erin staat |
|---|---|
| [`docs/nl/migration.md`](docs/nl/migration.md) | **Kom je van MeshStats?** De volgorde waarin je een draaiende installatie bijwerkt |
| [`docs/nl/architecture.md`](docs/nl/architecture.md) | Hoe de onderdelen samenhangen, en waarom MQTT het van HTTP overnam |
| [`docs/nl/glossary.md`](docs/nl/glossary.md) | Het MeshCore-vocabulaire dat deze documenten veronderstellen |
| [`docs/nl/protocol.md`](docs/nl/protocol.md) | Het pakketformaat in de ether en het companion-protocol, volledig gespecificeerd |
| [`docs/nl/deployment.md`](docs/nl/deployment.md) | De site installeren, instellen, bijwerken en draaien |
| [`docs/nl/contributing.md`](docs/nl/contributing.md) | Waarom de code eruitziet zoals hij eruitziet |

[`docs/nl/protocol.md`](docs/nl/protocol.md) is het lezen waard, ook als je dit
project nooit draait. Het MeshCore-wireformaat staat nergens anders beschreven;
dat document is een specificatie op byteniveau met uitgewerkte voorbeelden,
gereconstrueerd uit de firmwarebroncode en regel voor regel geciteerd.

---

## Hoe data binnenkomt

**MQTT (aanbevolen).** De node houdt één verbinding open en publiceert naar
`meshmanager/<node>/stats`. Veel lichter dan HTTP: geen TLS-stack en geen nieuwe
sessie per meting — en dat is wat een ESP32 die mesh, WiFi en BLE tegelijk
draait daadwerkelijk volhoudt. Ruwe pakketten gaan over
`meshmanager/<node>/rx`, en dat is wat de live kaart en het archief voedt. Er
wordt ook nog naar het oudere voorvoegsel `meshcore/` geluisterd, zodat nodes en
server nooit op dezelfde dag om hoeven.

Over diezelfde verbinding kan de site een node om iets vragen — drie korte
opdrachten op `meshmanager/<node>/cmd` (`settings`, `status`, `time <epoch>`), en
verder wordt daar niets geaccepteerd. Zie [`docs/mqtt.md`](docs/nl/mqtt.md).

**HTTP.** `POST /api/v1/ingest` met `Authorization: Bearer <token>`:

```json
{
  "repeater": {"pubkey_prefix": "e3d3f4d7ed", "name": "BE-HSS-JessaZH.VIR"},
  "metrics": {"bat": 4.15, "battery_percentage": 96.4, "online": true},
  "neighbors": [{"prefix": "2ae7af", "name": "BE-LUM-Lummen C-ESP", "snr": -4.25}]
}
```

Beide wegen delen dezelfde afhandeling. Onbekende repeaters verschijnen vanzelf —
standaard publiek, verberg ze in `/admin` — en onbekende metrics belanden in de
sectie "overig" in plaats van geweigerd te worden.

Volledig routeoverzicht: [`docs/api.md`](docs/nl/api.md).

---

## Instellingen

| Variabele | Standaard | Betekenis |
|---|---|---|
| `MM_DATA_DIR` | `server/data` | Waar SQLite en de geheime sleutel staan. Docker zet `/data` |
| `MM_MQTT_PREFIX` | `meshmanager` | Het MQTT-topicvoorvoegsel dat deze installatie in eigendom heeft |
| `MM_SITE_NAME` | MeshManager | Naam in de kop |
| `MM_RETENTION_DAYS` | 180 | Bewaartermijn van de historiek |
| `MM_PACKET_RETENTION_DAYS` | 7 | Bewaartermijn van het pakketarchief, en tevens het venster van de heatmap |
| `MM_PACKET_MAX_ROWS` | 200000 | Bovengrens aan rijen in het archief; de oudste gaan eerst |
| `MM_DB_MAX_MB` | 512 | Bovengrens aan de omvang van de database, WAL inbegrepen |
| `MM_HEARTBEAT_MIN` | 5 | Dwing minstens elke X minuten een grafiekpunt af |
| `MM_MAX_BODY_BYTES` | 2000000 | Grootste verzoekbody die geaccepteerd wordt |
| `MM_TRUSTED_PROXY_HOPS` | 1 | Aantal proxies vóór de app; de inlogrem gebruikt het om het clientadres te vinden |
| `MM_MQTT_HOST` | *(leeg)* | Broker; leeg schakelt MQTT uit |
| `MM_MQTT_CMD_TOPIC` | `{prefix}/{node}/cmd` | Het enige topic waarop de site publiceert |

Elke variabele heet `MM_<NAAM>`. De oude schrijfwijze `MM_<NAAM>` wordt **nog
steeds gelezen** als terugval, dus een bestaande `.env` blijft gewoon werken. De
meeste hiervan zijn ook in `/admin` te bewerken, waar de databasewaarde wint.
Volledige lijst:
[`docs/deployment.md`](docs/nl/deployment.md#omgevingsvariabelen).

---

## Beveiliging in één alinea

Wachtwoorden zijn PBKDF2-SHA256 (200k iteraties); API-tokens worden alleen als
SHA-256-hash bewaard; sessies zijn HMAC-ondertekend, `HttpOnly` en `Secure`
achter een proxy, en een wachtwoordwijziging maakt ze allemaal ongeldig; de
inlog is CSRF-gecontroleerd en afgeremd per adres en per gebruikersnaam;
verzoekbodies worden al tijdens het lezen begrensd; CSP en de gebruikelijke
headers staan aan. **De site kent geen enkel wachtwoord van jouw mesh** — het
enige dat hij naar een node kan sturen is één van drie korte opdrachten,
`settings`, `status` en `time <epoch>`: twee ervan maken alleen maar dat de node
publiceert wat hij toch al publiceert, en de derde zet een klok. Geen van drie
stelt een radio in, dus zelfs een volledig gecompromitteerde site kan jouw mesh
niet herconfigureren. Twee dingen verdienen nog aandacht vóór je publiek gaat:
de inlogrem leeft in één proces en vergeet bij een herstart, dus een
toegangspoort bij de proxy is de moeite waard, en een filesystem-back-up van een
node bevat de
**privésleutel** van die node. Lees [`docs/security.md`](docs/nl/security.md).

---

## Bijdragen

Commits zijn in het Nederlands en dragen de redenering in de body. Commentaar
legt het waarom uit, niet het wat. Er is geen buildstap, migraties zijn
additief, en de site weigert in het openbaar te gokken — als het bewijs twee
antwoorden niet scheidt, zegt hij dat, in plaats van er één te kiezen.

Dat staat allemaal, met de redenen erbij, in
[`docs/nl/contributing.md`](docs/nl/contributing.md). Lees het vóór een eerste
wijziging.

Tests: `cd server && pip install -r requirements-dev.txt && python -m pytest`.
Zie [`docs/nl/testing.md`](docs/nl/testing.md).

---

## Licentie

[MIT](LICENSE). Niet gelieerd aan het MeshCore-project.
