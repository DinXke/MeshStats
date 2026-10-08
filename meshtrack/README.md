# MeshTrack

APRS-achtige tracking via MeshCore, volledig offline. Trackers (Seeed T1000-E met eigen firmware) sturen hun
positie op een versleuteld MeshCore-kanaal. De MeshTrack-server luistert mee via een companion die openHop host en
toont de trackers live op een kaart; wie de kanaalsleutel heeft, kan ook zonder server meekijken met de offline-app.

```
T1000-E (firmware/)  --kanaalbericht (flood)-->  openHop-companion  --TCP-->  server/ (kaart, beheer, simulator)
                                             \-->  eigen companion  --Bluetooth-->  /offline (PWA, zonder internet)
```

## Onderdelen

- **firmware/**: overlay op stock MeshCore `companion_radio` v1.17.1 voor de T1000-E (huidige versie 0.7.0).
  - Volledige companion aan USB, trackermodus op batterij. Dubbelklik wisselt de modus (tot 0,8 s tussen de klikken),
    één klik stuurt meteen een positie, 2 tot 8 s vasthouden stuurt een SOS, langer dan 8 s schakelt uit.
  - Verzenden alleen via één **trackingkanaal** (`chan`), met de naam van de tracker als afzender en een handtekening
    met de authsleutel (`authkey`). Sinds 0.7.0 geen DM, geen doel (server-pubkey), geen ACK-herhalingen en geen ritme
    volgens de ontvangst meer: een kanaalbericht krijgt geen bevestiging.
  - Bewegingsregels (snelheid, afstand, bochten, ritme), stilstand en heartbeat, wakker worden via de
    bewegingssensor, radio en led uit in trackermodus. Zelfde regels als `server/meshtrack/rules.py` (simulator).
  - In beweging elke `sample` seconden een punt bewaren; elk bericht neemt zoveel punten mee als erin passen.
  - In companionmodus komt een kopie van elke eigen positie in de berichtenwachtrij, zodat een app via Bluetooth ook
    de eigen posities ziet (een companion hoort zijn eigen kanaalberichten anders niet).
  - Genummerd serieel menu en `backup` van de opslag. De sleutel, contacten, kanalen en regio's blijven bij elke
    app-only flash behouden; de opgeslagen instellingen houden hetzelfde formaat (oude velden blijven ongebruikt staan).
  - Klaarmaken via USB: `key import/export`, `chan list/set`, `set name|radio|tx|path_bytes|scope|chan|authkey`.
    Paden altijd 2 bytes per hop.
- **server/**: FastAPI + meshcore-py (versie 1.0.0).
  - Live kaart met MapLibre en eigen pmtiles (geen externe diensten), filters (ook per kanaal, `?kanaal=<id>`),
    favorieten, volgen, sporen per snelheid, meshnodes uit de observer-database van openHop.
  - **Rechten via kanalen**: elke tracker heeft één trackingkanaal (`trackers.channel_id`, ook bijgewerkt uit zijn
    laatste geldige bericht). Gebruikers zitten in een of meer groepen; een groep krijgt per kanaal het niveau
    `kaart` (de trackers van dat kanaal zien) of `sleutel` (ook naam, sleutel en QR-code), of "alle kanalen". Rechten
    en zichtbare trackers worden opgeteld; *Effectieve rechten* toont per recht, kanaal en tracker de herkomst
    (`/api/users/{id}/effective`). Beheerders (`system.manage`) hebben `sleutel` op alles.
  - Pagina *Kanalen* (`/kanalen`, `/api/channels/mine`): de kanalen die je mag lezen; bij `sleutel` met QR-code
    (`meshcore://channel/add`) om het kanaal op een eigen companion te zetten.
  - Gebruikers, groepen, deellinks (kanalen of losse trackers, nooit meer dan de maker ziet), auditlog, logboek met
    filters, GPX/CSV-export.
  - Zones (gedeeld of persoonlijk) en meldingsregels (per kanaal of tracker) die DM's via de mesh naar personen
    sturen, met een wachtrij. Dat zijn de enige DM's die de server nog gebruikt.
  - Simulator: virtuele trackers rijden 24/7 over echte wegen (offline routering over de wegenlaag van de
    kaarttegels), met een historiek in versnelde tijd. Profielen auto, fiets, voet en reiziger; ook zij krijgen een kanaal.
  - Pagina's: Kaart, Logboek, Kanalen, Trackers (lijst met zoeken, filters en kanaal, genegeerde berichten; formulieren
    in een zijpaneel), Toestellen (alles via USB: naam, trackingkanaal, instellingen met voorinstellingen, firmware,
    klaarmaken en backups, terminal), Gebruikers, Systeem (meldingen, kanalen, instellingen, companion-QR), Offline, Help.
  - Firmware flashen in de browser (Web Serial-DFU, `static/dfu.js`): eerst een backup, alleen de app, daarna
    controle van de pubkey.
  - Nieuw toestel klaarmaken: de server maakt het sleutelpaar en zet sleutel, naam, radio, regio en kanalen via USB op
    het toestel. Backups (met privésleutel, formaat van de MeshCore-app) staan versleuteld (AES-GCM) op de server;
    recht `keys.manage`.
  - Offline-app `/offline` (PWA): verbindt via Web Bluetooth met een MeshCore-companion, haalt de kanaalberichten op
    die de companion ontcijferde, leest `T1C|…` volledig offline (met de extra punten), bewaart alles in IndexedDB en
    tekent sporen zoals de online kaart. Werkt zonder account en gebruikt niets uit de database: van de server komen
    alleen kaarten, lettertypes en sprites (openbaar). Kaarten (Limburg, België, Benelux, Frankrijk, Duitsland) staan
    als pmtiles in OPFS en worden samen getoond (van grof naar gedetailleerd gestapeld). Namen uit de contacten van de
    companion; kanaalkeuze uit de kanalen van de companion; kanalen toevoegen via QR (camera of foto) of met naam en
    sleutel. Chat voor alle andere berichten, zelf sturen met instelbare scope (standaard `be`), herhalingen van eigen
    berichten en eigen trackerposities via de ruwe ontvangstlog (push 0x88). Versleepbaar onderpaneel op de gsm.
  - Verloren trackers: een bericht van een verloren tracker geeft de gebeurtenis `lost_seen`; de status blijft.
- **deploy/**: systemd-unit en `deploy.sh` (draait op de openHop-LXC, poort 8090).
- **tools/publish_firmware.py**: zet een firmwarebuild (zip + uf2 + `firmware.json`) in `server/static/firmware/`
  voor de downloads en de webflasher. De binaire bestanden staan niet in git.
- **tools/build_display_tiles.py**: bouwt de weergavekaart (z0–13 voor heel het bronarchief, z14 voor de Benelux)
  zonder veel geheugen.
- **docs/handleiding/**: handleiding voor gebruikers (HTML-bron, screenshots en de PDF, ook te downloaden vanaf de
  helppagina van de site) en de snelstart op één pagina.
- **PLAN.md**: ontwerp, berichtprotocol en bewegingsregels.

## Berichtprotocol

Een tracker stuurt op zijn trackingkanaal een groepsbericht `"<naam>: T1C|<pubkey 8 hex>|<tag 8 hex>|<rest>"`, met

```
<rest> = <seq>|<state>|<lat>|<lon>|<alt_m>|<spd_kmh>|<crs_deg>|<bat_pct>|<hdop>|<fix_age_s>|<mode c|t>|<power u|b>|<fix_ts>[|<extra>]
```

`tag` = eerste 4 bytes HMAC-SHA256(authsleutel, `<pubkey8>|<rest>`); de authsleutel (16 bytes) maakt de server per
tracker en gaat via USB naar de tracker. De server luistert op de kanalen uit *Systeem → Kanalen*, controleert de tag
(of aanvaardt per kanaal ook `-`) en zet het trackingkanaal van de tracker. De afzendernaam is vrij (de nodenaam) en
wordt genegeerd: een kanaalbericht bevat geen publieke sleutel, alleen `pubkey8` en de tag identificeren de tracker.

Extra punten (`<extra>`): `~<interval>;dlat,dlon[@s];...`, nieuwste eerst, elk punt als verschil met het vorige in
1e-5 graden; de server berekent de snelheid. Het oudere formaat `dt,dlat,dlon,spd;...` wordt nog gelezen.

`fix_ts` is de GPS-tijd van de fix in unix-seconden; de server gebruikt die als tijdstip van de positie. Zonder
`fix_ts`: sender-tijd min `fix_age_s`, of de ontvangsttijd als de klok van de tracker niet klopt.

Statussen: `M` beweging, `W` wakker door beweging, `S` stilgevallen, `H` heartbeat, `N` geen fix, `P` handmatig,
`E` SOS, `B` moduswissel of voeding gewijzigd.

Tot firmware 0.6 konden trackers ook `T1|<rest>` als DM naar de server sturen; de server leest dat sinds 1.0 niet
meer en noteert het bij de genegeerde berichten als "oude firmware".

## Server lokaal

```bash
python -m venv .venv && .venv/bin/pip install -r server/requirements.txt pytest httpx
cd server && ../.venv/bin/python -m pytest -q
python -m meshtrack.auth          # wachtwoordhash + sessiesleutel voor config.yaml
MESHTRACK_CONFIG=config.yaml python -m meshtrack.main
```

Sleutels en backups worden versleuteld met `auth.keystore_secret` uit `config.yaml`, of anders met
`auth.session_secret`. Wijzig je dat geheim, dan zijn bestaande backups niet meer leesbaar.

Let op: openHop laat **één** client per companion toe; een tweede verbinding (lokale test, meshcore-cli) gooit de
draaiende server eruit. Bij de eerste start maakt de server de standaardgroepen aan en een beheerder uit
`config.yaml` (`auth.user` / `auth.password_hash`).

## Kaarttegels

In `tiles_dir`:
- `basemap.pmtiles`: de weergavekaart (Protomaps-schema). Bouwen met
  `python tools/build_display_tiles.py bron.pmtiles basemap.pmtiles` (zet `TMPDIR` op een schijf, niet op tmpfs).
- `roads.pmtiles`: een Benelux-uitsnede tot z14 voor de routering van de simulator
  (`pmtiles extract bron.pmtiles roads.pmtiles --bbox=2.5,49.4,7.3,53.6 --maxzoom=14`).
- `offline/*.pmtiles`: de kaarten voor de offline-app, uitgesneden met `bash tools/build_offline_maps.sh`
  (Limburg z14 84 MB, België z13 286 MB, Benelux z10/z12 44/276 MB, Frankrijk z10/z12 216 MB/1,1 GB,
  Duitsland z10/z12 157/884 MB).
- `fonts/` en `sprites/`.

## Firmware bouwen en flashen

Zie `firmware/platformio.local.ini`: MeshCore v1.17.1 (d929643) naast deze map, env `t1000e_meshtrack`, bouwen op
een ASCII-pad. Flashen altijd app-only via DFU, en eerst een `backup` maken. Daarna
`python tools/publish_firmware.py <versie> "<wijzigingen>"` en deployen; de webinterface biedt de nieuwe versie
dan aan.
