# Changelog — MeshManager (site)

De versie van de site staat in `server/app/version.py` en in de footer van elke
pagina als `v<versie> · <commit> · <bouwdatum>`. Dit bestand krijgt een regel bij
elke ophoging; de commit-hash in de footer zegt wélke build van die versie het
is. Firmware heeft zijn eigen versies (`firmware/`, tags `fw-v…`); de
MeshUptime-node en de T1000-E-companion staan in de MeshUptime-repository.

Schema: MAJOR bij een breuk in de API of de databank, MINOR bij een merkbare
functie, PATCH bij een fix. Begonnen op 2.10.0 — zie de toelichting in
`version.py` voor waarom niet 1.0.0.

## 2.10.0 — 2026-09-04

Eerste versie met een stempel. Wat er die dag in zat, bovenop alles van de
voorgaande drie weken (200 commits sinds 2026-08-14):

- **Versiestempel**: footer, `/api/v1/ping` (`app_version`, `build`) en de
  eerste regel van het containerjournal. Commit en bouwdatum worden bij de
  Docker-build ingebakken (`deploy/autoupdate.sh`); zonder toont de site `dev`.
- **Poller is niet langer Home Assistant**: `route["poller"]` / `poller_name`
  (was `"ha"`); de wachtrij legt vast wíe er pollde. `/api/v1/commands` en
  `/api/v1/repeater_settings` aanvaarden naast een beheer-token ook het
  vloot-pushtoken, zodat de MeshUptime-node de wachtrij kan bedienen.
- **Filterstatistieken van stock-repeaters met filterpatch** (`pfstock`): het
  antwoord op `cmd:filter count` wordt dezelfde filterstand en dezelfde metrics
  als bij een node met MeshManager-firmware. Per-variant uitleg bij elke tegel
  (`pfhelp`), meetbare filterbewaking (`pfguard`), sweep-interval in minuten.
- Docs EN+NL bijgewerkt: commanding, api, architecture, homeassistant,
  deployment.
