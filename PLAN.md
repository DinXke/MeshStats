# MeshTrack: APRS-achtige tracking over MeshCore

Projectplan, fase per fase uit te werken. Items met **VERIFIËREN** zijn aannames
die eerst tegen broncode of hardware gecontroleerd worden voor er op gebouwd wordt.

Versie 2 (2026-10-06): vervangt het oorspronkelijke plan. Belangrijkste
wijzigingen: firmware vertrekt van stock `companion_radio` met twee modi,
uitgebreidere bewegingsregels, trackerbeheer met alias, volledig offline
werkende server met eigen Benelux-kaart, hosting op de openHop-LXC.

## 1. Doel

Objecten volgen over het bestaande MeshCore-netwerk (EU Narrow, 869,618 MHz,
SF8, BW 62,5 kHz, CR 4/8):

- Een **T1000-E** draait een eigen firmware op basis van de stock MeshCore
  companion. Met USB-voeding (of op verzoek) is hij een **volledige companion**
  (MeshCore-app, regio's, contacten, kanalen). Zonder USB kan hij in
  **trackermodus**: BLE uit, radio slaapt, alleen zenden bij beweging.
- Een **openHop**-server ontvangt de posities via een gehoste companion-identiteit.
- **meshtrack-server** (Python) op dezelfde host toont de trackers live op een
  kaart, **volledig offline bruikbaar** (Pi, NUC of LXC, zonder internet).

## 2. Architectuur

```
T1000-E (MeshTrack-fw)
  | versleutelde DM met ACK; eerst flood, daarna direct pad
  v
MeshCore-repeaters (bestaand netwerk)
  v
openHop Repeater  (LXC 10.10.10.178)
  ├─ repeaterrol (ongewijzigd)
  └─ companion "BE-HSS-DinX-Track"  TCP 127.0.0.1:5051 (companion frame protocol)
        | localhost TCP
        v
meshtrack-server (Python, :8090)
  ├─ meshcore-py client (ontvangt DM's)
  ├─ parser + validatie
  ├─ SQLite (trackers, posities)
  ├─ FastAPI REST + WebSocket
  └─ MapLibre-frontend + eigen vectortegels (Benelux .pmtiles), geen CDN
        ^
        | cloudflared (bestaat al op de LXC) -> eigen hostnaam
```

Ontwerpkeuzes:

- Push via DM naar een companion, geen room server (geen nutteloze fan-out).
- Geen periodieke flood-adverts vanaf de tracker; de tracker wordt in de server
  geregistreerd (pubkey) en de server-companion voegt hem als contact toe.
- Tekstformaat in de DM: leesbaar tijdens debuggen.

## 3. Berichtprotocol (v1)

Eén DM per positie, ASCII, velden gescheiden door `|`:

```
T1|<seq>|<state>|<lat>|<lon>|<alt_m>|<spd_kmh>|<crs_deg>|<bat_pct>|<hdop>|<fix_age_s>[|<mode>|<power>|<fix_ts>]
```

| Veld | Type | Toelichting |
|---|---|---|
| `T1` | literal | Protocol + versie. Server negeert onbekende versies en logt ze. |
| `seq` | uint16 | Oplopend per bericht, wrap op 65535. Deduplicatie bij retries. |
| `state` | char | `M` bewegend, `S` net stilgevallen, `H` heartbeat, `N` geen fix, `E` SOS, `B` modus/boot-melding, `P` handmatig (enkele klik) |
| `lat`,`lon` | 5 decimalen | WGS84. Leeg bij `N`. |
| `alt_m` | int | Mag leeg. |
| `spd_kmh` | int | GNSS-snelheid. Mag leeg. |
| `crs_deg` | int 0..359 | Koers. Mag leeg. |
| `bat_pct` | 0..100 | Via echte LiPo-curve (niet lineair, zie T1000-E-batterijbug). |
| `hdop` | 1 decimaal | Mag leeg. |
| `fix_age_s` | int | Seconden sinds de fix. |
| `mode` | `c`/`t` | Companion of tracker (optioneel). |
| `power` | `u`/`b` | USB of batterij (optioneel). |
| `fix_ts` | unix-s | GPS-tijd van de fix (optioneel, fw 0.4.0+). Wint van alle andere tijden. |

Voorbeeld: `T1|412|M|50.93012|5.33781|42|37|184|87|1.2|0` (ca. 45 bytes).

Server-regels: dedup op `(tracker_pubkey, seq)` binnen 24 u; positie buiten
Benelux+buffer of hdop > 5 wordt opgeslagen maar `suspect`; tijdstempel =
`fix_ts` als plausibel, anders sender-timestamp min `fix_age_s`, anders ontvangsttijd. Een ouder bericht dat
later binnenkomt gaat in het spoor maar overschrijft de laatste positie niet. Documenteren in
`docs/protocol.md`.

Later (fase 3b): server -> tracker configcommando's (`!set <param> <waarde>`,
`!cfg`), alleen van de server-companion-pubkey, uitgevoerd in het korte
RX-venster na een eigen bericht.

## 4. Firmware (T1000-E)

### 4.1 Basis

- **Stock MeshCore `companion_radio` (BLE) v1.17.1, commit d929643**: exact de
  build van `t1000e_companion_radio_ble-v1.17.1-d929643.zip` en dezelfde basis
  als MU-companion. Overlay-env in `platformio.local.ini` (zie MeshStats
  `docs/firmware.md`), eigen code in `firmware/meshtrack/`, slechts enkele hooks
  in de stock-bestanden.
- **Opslagindeling identiek houden** (`nrf52840_s140_v7_extrafs.ld`,
  `MAX_CONTACTS=350`, `MAX_GROUP_CHANNELS=40`, stock `DataStore`). Dan behoudt
  een app-only DFU-flash identiteit, prefs (regio's), contacten en kanalen.
  Eigen instellingen in een apart bestand `/mt_cfg.dat` op InternalFS: atomair
  (`.tmp`+rename), met versie, downgrade-veilig, bootmelding
  `geladen|gemigreerd|hersteld|DEFAULTS (reden)`.
- **Harde eis: de bestaande identiteit blijft.** De T1000-E houdt na elke
  flash zijn huidige sleutelpaar (pubkey `2CB0C5EB473757805EAB00F9DD0594C2
  29D6E50B2ACEC6B57403B6259C9E126F`, in `/_main.id`). Procedure: pubkey
  uitlezen -> `backup` -> app-only DFU -> pubkey opnieuw uitlezen en
  vergelijken; bij verschil meteen `restore` van de backup. Nooit een nieuwe
  identiteit laten genereren, nooit full-chip-erase, `DataStore`/identiteits-
  bestand niet wijzigen. Volledige backup van 2026-10-06 (sha256 2f73b2b3...):
  `C:\Users\BjörnScheepers\t1000e-backup\` (bevat de private key: niet in git).
  Deze sleutel nooit hergebruiken op een ander toestel (bv. de openHop-companion).
- **Backup/restore** ingebouwd: serieel `backup` (ruwe dump 0xD4000-0xF4000 +
  crc32, zoals MU-companion 2.3.1) en `restore`. Alleen serieel, nooit via DM.
- Bouwen op een ASCII-pad (`C:\Users\Public\...`).

### 4.2 Modi

| | Volledige companion | Tracker |
|---|---|---|
| BLE / MeshCore-app | aan | uit |
| Radio | continu RX (stock) | slaapt; RX alleen kort na eigen TX (ACK) |
| Tracking | optioneel op de achtergrond (`track_in_companion`, standaard aan) | aan |
| Serieel menu | ja (als USB) | ja (als USB, maar dan staat hij in companion) |

- **USB-voeding (VBUS) = altijd volledige companion.**
- Zonder USB: de laatst gekozen modus (bewaard; standaard **tracker**).
- **Dubbelklik** wisselt van modus:
  - naar tracker: één korte **biep**
  - naar companion: **biieep biieep** (twee lange)
  - deze biepjes klinken ook als de buzzer stil staat.
- Bij een moduswissel stuurt de tracker een `B`-bericht naar de server, zodat
  de kaart de modus kent.

Knopindeling (stock-functies zoveel mogelijk behouden):

| Klik | Stock | MeshTrack |
|---|---|---|
| 1x | – (geen scherm) | **positie nu versturen** (modusbiep, GPS-fix tot fix_timeout_hb, state `P`; 2 hoge biepjes = ACK, lage toon = mislukt; max 1x per 10 s, negeert min_interval) |
| 2x | advert sturen | **modus wisselen** (advert blijft via de app) |
| 3x | buzzer aan/uit | ongewijzigd |
| 4x | GPS aan/uit | ongewijzigd in companion; **open punt**: SOS in tracker |
| lang | uitschakelen; < 8 s na boot: rescue-CLI | ongewijzigd |

Tracking in companionmodus: de tracker neemt de GPS over zolang hij volgt (de
GPS-schakelaar van de app/4x regelt dan alleen de locatie in adverts).
T1-DM's worden door de firmware verstuurd en verschijnen niet als chat in de
app; ACK's van de server mogen de ACK-boekhouding van de app niet verstoren.
**VERIFIËREN** in `MyMesh.cpp` (expected-ACK-tabel, `sendMessage`).

### 4.3 Bewegingsregels

Een positie wordt verstuurd wanneer **alle** voorwaarden van een trigger kloppen
**en** de rate limit het toelaat:

```
rate_ok   = (nu - laatste_tx) >= min_interval
afstand   = afstand(laatste_tx_pos, pos) >= min_dist
snel      = spd_kmh >= min_speed
bocht     = |koers - laatste_tx_koers| >= turn_min  EN  spd_kmh >= turn_min_speed
forceer   = bewegend EN (nu - laatste_tx) >= max_interval

zend als rate_ok EN ( (snel EN afstand) OF bocht OF forceer )
```

State machine:

```
SLEEP ──(accel-interrupt)──> ACQUIRE
ACQUIRE ──(fix)──> MOVING (evalueer regels)
ACQUIRE ──(fix_timeout)──> SEND(N) ──> MOVING
MOVING ──(regels)──> SEND(M)
MOVING ──(geen beweging >= still_timeout)──> SEND(S) ──> SLEEP
SLEEP ──(heartbeat_interval)──> ACQUIRE (korte timeout) ──> SEND(H) ──> SLEEP
```

| Parameter | Start | Omschrijving |
|---|---|---|
| `min_speed` | 10 km/u | onder deze snelheid geen afstand-trigger (0 = uit) |
| `min_dist` | 100 m | minimale verplaatsing sinds laatste verzonden positie |
| `turn_min` | 30° | koerswijziging die een bocht-trigger geeft (0 = uit) |
| `turn_min_speed` | 5 km/u | onder deze snelheid is koers ruis: geen bocht-trigger |
| `min_interval` | 60 s | **nooit vaker dan 1x per** (rate limit, in s of min) |
| `max_interval` | 10 min | in beweging toch minstens 1x per (0 = uit) |
| `still_timeout` | 5 min | geen beweging -> `S` en slapen |
| `heartbeat_interval` | 12 u | teken van leven in rust (0 = uit) |
| `fix_timeout` | 90 s / 30 s | cold / heartbeat |
| `ack_retries` | 2 | daarna pad resetten en flood |
| `track_in_companion` | aan | ook volgen in companionmodus |
| `target` | – | pubkey van de server-companion |
| `accel_sens` | med | wake-on-motion-gevoeligheid |

### 4.4 Configuratie

- **Serieel menu** (zelfde UX als MU-companion: lege regel = menu, anders
  losse commando's `cfg`, `status`, `set <param> <waarde>`). Kan ook via Web
  Serial vanuit de meshtrack-webinterface.
- Fase 3b: draadloos via de server (configwachtrij, uitgevoerd na het volgende
  bericht van de tracker).

### 4.4b Batterij

Overnemen uit MU-companion (`MuBattery.cpp/.h`, zie `battery_fix_t1000e.md`):

- `battery_percent_from_mv()`: echte LiPo-ontlaadcurve i.p.v. lineair (de
  T1000-E-bug "blijft op 50-60 % hangen en valt dan plots uit"). Dit is het
  `bat_pct`-veld in elk T1-bericht en de waarde in `status`.
- **App-spoofing** `battery_app_mv()`: de MeshCore-app rekent lineair
  `(mv-3000)/12`; we geven in de twee app-frames (self-info/battery-reply in
  `MyMesh.cpp`) een virtueel mV `3000 + pct*12` door, zodat de app het juiste %
  toont. Alleen de waarde verandert, niet de framestructuur. Vergt een kopie van
  `MyMesh.cpp` met 2 kleine hooks (zoals MU-companion).
- Eigen uitvoer (T1, serieel) toont de ECHTE spanning en het echte %.

### 4.5 Energie

Tracker/SLEEP: GNSS uit (backup-voeding aan voor hot start), radio sleep, BLE
uit, nRF52840 system-on idle, wake op QMA6100P-interrupt (P1.2). I²C alleen bij
beweging en met timeouts (Wire-hang-les uit MU-companion v1.0.1).
**VERIFIËREN**: wake-on-motion-registers QMA6100P, GNSS-backupstroom.

Schatting (te meten met PPK2): rust ~0,05-0,15 mA, beweging ~15-18 mA,
companion ~5-7 mA. Verbruik per modus/toestand meten en documenteren in
`firmware/README.md`.

## 5. Server

### 5.1 Hosting

- Openhop-LXC **10.10.10.178**, poort **8090** (vrij; in gebruik: 22, 25,
  5050, 8000, 20241). Publieke hostnaam via de bestaande cloudflared (token-
  tunnel: Public Hostname in het Cloudflare-dashboard -> `http://localhost:8090`).
- openHop-companion `BE-HSS-DinX-Track` op **127.0.0.1:5051** (niet naar buiten:
  het companion-protocol heeft geen authenticatie).
- systemd `meshtrack.service`, `After=openhop-repeater.service`.
- Moet even goed op een Pi of NUC draaien zonder internet: geen enkele
  externe afhankelijkheid tijdens runtime (JS/CSS/fonts/tegels lokaal).

### 5.2 Trackerbeheer

Webinterface (achter login) om trackers **toe te voegen, te bewerken en te
verwijderen**:

- pubkey (64 hex, of prefix + kiezen uit de contacten van de companion)
- **alias** (weergavenaam op de kaart), optioneel icoon en kleur
- actief/inactief, notities
- bij toevoegen: contact aanmaken op de openHop-companion (meshcore-py
  `add_contact`/`import_contact`), bij verwijderen: contact weghalen
  (posities optioneel bewaren of wissen)
- per tracker: laatste positie, modus, batterij, laatst gezien, pad/hops
- fase 3b: parameters wijzigen (configwachtrij)

### 5.3 Kaart (offline)

- **MapLibre GL** + vectortegels in één `.pmtiles`-bestand, zelfde stack als
  MeshStats/MeshChat (zie memory "MeshStats vector-basemap").
- Benelux uitsnijden uit het bestaande West-Europa-archief van meshmanager.net
  met `pmtiles extract --bbox=2.5,49.4,7.3,53.6 --maxzoom=14`.
  Grootte volgens MeshChat `tile_sizes.json` (BE+NL+LU): t/m z12 0,32 GB,
  z13 0,68 GB, **z14 1,39 GB** (aanbevolen), z15 3,23 GB.
- Stijl, glyphs (fonts) en sprites ook lokaal; `basemap.js` hergebruiken.
- Live markers via WebSocket, spoor laatste N uur, popup (alias, snelheid,
  batterij, modus, laatst gezien), grijs als langer dan 2 heartbeat-intervallen stil.

### 5.4 Overig

Authenticatie (nooit open: locaties zijn persoonsgegevens), retentie, logging,
`/api/health` met status van de meshverbinding, optioneel MQTT naar Home
Assistant (`meshtrack/<alias>/pos`).

## 6. Repo-structuur

```
meshtrack/
├── PLAN.md
├── docs/            protocol.md, openhop-setup.md, offline-maps.md
├── server/
│   ├── pyproject.toml, config.example.yaml
│   ├── meshtrack/   main.py config.py mesh_client.py protocol.py db.py
│   │                models.py api.py ws.py admin.py
│   ├── static/      index.html app.js admin.html + vendored maplibre/pmtiles
│   └── tests/
├── tiles/           (niet in git) benelux.pmtiles, glyphs, sprites
├── tools/           simulate_tracker.py, extract_tiles.sh
└── firmware/
    ├── README.md
    ├── platformio.local.ini (overlay-env)
    └── meshtrack/   eigen bronnen (MT*.cpp)
```

## 7. Fasen

**Fase 0: verkennen (geen code).** openHop: companion-frame-server, meerdere
gelijktijdige clients?, contactbeheer en persistentie, berichtwachtrij bij
geen client. Stock firmware: knophandlers (`ui-orig/UITask.cpp`), GPS-sturing
(`sensors`), ACK-boekhouding, VBUS-detectie T1000-E. Oplevering
`docs/openhop-setup.md`.

**Fase 1: server-skelet + simulator.** meshcore-py-client met reconnect,
`protocol.py` + tests, SQLite, REST + WebSocket, trackerbeheer met alias,
kaart met lokale tegels, `tools/simulate_tracker.py` via een tweede companion.
Acceptatie: gesimuleerde tracker beweegt live, herstart verliest niets, dubbele
seq genegeerd, werkt zonder internet.

**Fase 2: hardening + uitrol op de LXC.** config.yaml, auth, retentie,
systemd, cloudflared-hostnaam, Benelux-tegels, optioneel MQTT.

**Fase 3: firmware.** Overlay op stock v1.17.1; eerst modus/knop/biepjes +
serieel menu + backup/restore, dan bewegingsregels + T1-verzending, dan
energie (trackermodus) + meten. Acceptatie: sleutel/contacten/regio's
blijven na flash; dubbelklik wisselt met juiste biep; USB = companion; tracker
slaapt in rust en levert binnen ~1 min een positie bij beweging.

**Fase 3b: configuratie via de server.**

**Fase 4: veldtest** (Hasselt e.o.): aankomstratio, latentie, airtime,
batterij; parameters bijstellen.

## 8. Randvoorwaarden

- Duty cycle 869,4-869,65 MHz: 10 %. Een T1-bericht is ~0,6-0,8 s airtime
  (meten). Netwerk is gedeeld: totaal trackerverkeer laag houden;
  `min_interval` is de harde rem.
- Privacy: kaart achter login, beperkte retentie.
- openHop-repeaterconfiguratie niet wijzigen, behalve de companion toevoegen.
- Flashen: altijd app-only DFU (1200-baud-touch -> bootloader-COM ->
  adafruit-nrfutil), eerst `backup`. Nooit full-chip-erase.

## 9. Open vragen

- Welke objecten en hoeveel trackers? (intervallen, airtimebudget)
- Wie mag de kaart via de publieke hostnaam zien?
- SOS-knop in trackermodus: welke klik (4x?) en naar wie?
- Pull op aanvraag (alleen mogelijk in companionmodus of in het RX-venster)?
- Val-detectie later toevoegen (alleen zenden, past in trackermodus)?
