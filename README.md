# MeshTrack

APRS-achtige tracking via MeshCore, volledig offline. Trackers (Seeed T1000-E met eigen firmware) sturen hun
positie als versleutelde DM naar een companion die openHop host. De MeshTrack-server toont ze live op een kaart.

```
T1000-E (firmware/)  --DM over de mesh-->  openHop-companion  --TCP-->  server/ (kaart, beheer, simulator)
```

## Onderdelen

- **firmware/**: overlay op stock MeshCore `companion_radio` v1.17.1 voor de T1000-E (huidige versie 0.4.1).
  - Volledige companion aan USB, trackermodus op batterij. Dubbelklik wisselt de modus, één klik stuurt meteen een
    positie, 2 tot 8 s vasthouden stuurt een SOS, langer dan 8 s schakelt uit.
  - Bewegingsregels (snelheid, afstand, bochten, ritme), stilstand en heartbeat, wakker worden via de
    bewegingssensor, ACK met herhaalpogingen, radio en led uit in trackermodus.
  - Ritme volgens de ontvangst (0.4.0): na een snelle ACK vaker zenden, na herhaalde missers trager; in het snelle ritme geen herhaalpogingen voor gewone posities
    (`fast_retries`), en een gewone positie wijkt altijd voor een verse; zelfde logica
    als `server/meshtrack/rules.py`, die ook de simulator gebruikt.
  - Genummerd serieel menu en `backup` van de opslag. De sleutel, contacten, kanalen en regio's blijven bij elke
    app-only flash behouden.
  - Klaarmaken via USB: `key import/export`, `chan list/set`, `set name|radio|tx|path_bytes|scope`. Paden altijd
    2 bytes per hop (1 byte wordt bij het opstarten 2).
- **server/**: FastAPI + meshcore-py.
  - Live kaart met MapLibre en eigen pmtiles (geen externe diensten), filters, favorieten, volgen, sporen per
    snelheid, meshnodes uit de observer-database van openHop.
  - Gebruikers, groepen en rechten, deellinks, auditlog, logboek met filters, GPX/CSV-export.
  - Zones (gedeeld of persoonlijk) en meldingsregels die DM's via de mesh sturen, met een wachtrij.
  - Simulator: virtuele trackers rijden 24/7 over echte wegen (offline routering over de wegenlaag van de
    kaarttegels), met een historiek in versnelde tijd. Profielen auto, fiets, voet en reiziger.
  - Instellen van een tracker via Web Serial, helppagina in de site, zes thema's.
  - Firmware flashen in de browser (Web Serial-DFU, `static/dfu.js`): eerst een backup, alleen de app, daarna
    controle van de pubkey.
  - Nieuw toestel klaarmaken: de server maakt het sleutelpaar en zet sleutel, naam, radio, regio, kanalen en doel
    via USB op het toestel. Backups (met privésleutel, formaat van de MeshCore-app) staan versleuteld (AES-GCM) op
    de server; recht `keys.manage`.
  - Verloren trackers: een bericht van een verloren tracker geeft de gebeurtenis `lost_seen`; de status blijft.
- **deploy/**: systemd-unit en `deploy.sh` (draait op de openHop-LXC, poort 8090).
- **tools/publish_firmware.py**: zet een firmwarebuild (zip + uf2 + `firmware.json`) in `server/static/firmware/`
  voor de downloads en de webflasher. De binaire bestanden staan niet in git.
- **tools/build_display_tiles.py**: bouwt de weergavekaart (z0–13 voor heel het bronarchief, z14 voor de Benelux)
  zonder veel geheugen.
- **docs/handleiding/**: handleiding voor gebruikers (HTML-bron, screenshots en de PDF, ook te downloaden vanaf de
  helppagina van de site).
- **PLAN.md**: ontwerp, berichtprotocol (`T1|…`) en bewegingsregels.

## Berichtprotocol

```
T1|<seq>|<state>|<lat>|<lon>|<alt_m>|<spd_kmh>|<crs_deg>|<bat_pct>|<hdop>|<fix_age_s>|<mode c|t>|<power u|b>|<fix_ts>
```

`fix_ts` (vanaf firmware 0.4.0) is de GPS-tijd van de fix in unix-seconden; de server gebruikt die als tijdstip van
de positie. Zonder `fix_ts`: sender-tijd min `fix_age_s`, of de ontvangsttijd als de klok van de tracker niet klopt.

Statussen: `M` beweging, `W` wakker door beweging, `S` stilgevallen, `H` heartbeat, `N` geen fix, `P` handmatig,
`E` SOS, `B` moduswissel of voeding gewijzigd.

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
- `fonts/` en `sprites/`.

## Firmware bouwen en flashen

Zie `firmware/platformio.local.ini`: MeshCore v1.17.1 (d929643) naast deze map, env `t1000e_meshtrack`, bouwen op
een ASCII-pad. Flashen altijd app-only via DFU, en eerst een `backup` maken. Daarna
`python tools/publish_firmware.py <versie> "<wijzigingen>"` en deployen; de webinterface biedt de nieuwe versie
dan aan.
