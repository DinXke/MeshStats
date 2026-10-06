# MeshTrack

APRS-achtige tracking over MeshCore. Trackers (Seeed T1000-E met eigen firmware)
sturen hun positie als versleutelde DM naar een companion die openHop host; de
MeshTrack-server toont ze live op een offline kaart.

```
T1000-E (firmware/)  --DM over de mesh-->  openHop-companion  --TCP-->  server/ (kaart, beheer, simulator)
```

- **firmware/**: overlay op stock MeshCore `companion_radio` v1.17.1 voor de T1000-E.
  Volledige companion met USB, trackermodus op batterij (dubbelklik wisselt), serieel menu,
  backup van de opslag. De sleutel en contacten blijven bij elke flash behouden.
- **server/**: FastAPI + meshcore-py. SQLite, live kaart (MapLibre + eigen pmtiles, geen
  externe diensten), trackerbeheer met alias en iconen, geofences, Web Serial-instellingen,
  en een simulator die virtuele trackers 24/7 over echte wegen laat rijden (offline routering
  over de wegenlaag van de kaarttegels).
- **deploy/**: systemd-unit en `deploy.sh` (draait nu op de openHop-LXC, poort 8090).
- **PLAN.md**: ontwerp, berichtprotocol (`T1|...`), bewegingsregels en fasen.

## Server lokaal

```bash
python -m venv .venv && .venv/bin/pip install -r server/requirements.txt pytest
cd server && ../.venv/bin/python -m pytest -q
python -m meshtrack.auth          # wachtwoordhash + sessiesleutel voor config.yaml
MESHTRACK_CONFIG=config.yaml python -m meshtrack.main
```

Let op: openHop laat **één** client per companion toe; een tweede verbinding (lokale test,
meshcore-cli) gooit de draaiende server eruit.

## Kaarttegels

`tiles_dir` bevat `basemap.pmtiles` (Protomaps-schema), `fonts/` en `sprites/`. Een
Benelux-uitsnede tot z14 is ~1,2 GB:

```bash
pmtiles extract <bron>.pmtiles basemap.pmtiles --bbox=2.5,49.4,7.3,53.6 --maxzoom=14
```

## Firmware bouwen

Zie `firmware/platformio.local.ini`: MeshCore v1.17.1 (d929643) naast deze map, env
`t1000e_meshtrack`, bouwen op een ASCII-pad. Flashen altijd app-only via DFU, eerst `backup`.
