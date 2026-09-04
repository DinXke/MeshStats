# Changelog — MeshManager (site)

De versie van de site staat in `server/app/version.py` en in de footer van elke
pagina als `v<versie> · <commit> · <bouwdatum>`. Dit bestand krijgt een regel bij
elke ophoging; de commit-hash in de footer zegt wélke build van die versie het
is. Firmware heeft zijn eigen versies (`firmware/`, tags `fw-v…`); de
MeshUptime-node en de T1000-E-companion staan in de MeshUptime-repository.

Schema: MAJOR bij een breuk in de API of de databank, MINOR bij een merkbare
functie, PATCH bij een fix. Begonnen op 2.10.0 — zie de toelichting in
`version.py` voor waarom niet 1.0.0.

## 2.13.0 - 2026-09-04

- **Kanaalfilter te beheren vanaf de site.** In het pollerblok op de nodepagina:
  de geblokkeerde kanalen als lijst met een knop per kanaal om ze weer door te
  laten, een formulier om er een toe te voegen, en een knop om de lijst opnieuw
  op te halen. `filter channel list` staat in de standaard parameterlijst en
  `pfstock.parse_filter_channels` leest hem.
  Een aparte aan/uit bestaat op die firmware niet: de lijst IS de stand.
  Kanaalnamen worden op de server getoetst (`#naam` of `Public`, geen spaties)
  voor er zendtijd aan opgaat -- de firmware leest er precies een woord, dus een
  naam met een spatie zou een ander kanaal blokkeren dan bedoeld.
  Eerlijk gemeld op de pagina: op een lege lijst antwoordt die firmware niet, dus
  "niets geblokkeerd" en "geen antwoord" komen als hetzelfde aan; de teller
  `Channel` in de statusregel is het onafhankelijke bewijs.
- **Live pakketten: een echte tijd in plaats van een vaste 2,8 seconde.** De stip
  beweegt nu tijdens de zendtijd van elke hop (LoRa time-on-air uit de
  pakketlengte) en WACHT bij elke repeater die hem doorstuurt -- want daar zit de
  tijd, niet in de afstand. Een pakket van een hop is daarmee merkbaar sneller,
  een pakket van acht hops eerlijk langzamer.
- **Sporen feller en ze blijven nagloeien.** Een afgeronde route verdwijnt niet
  meer meteen maar dimt een halve minuut weg. Daardoor is niet alleen het ene
  pakket van nu te zien maar het patroon: welke paden druk zijn en welke node
  alles doorgeeft.

## 2.12.0 — 2026-09-04

- **`filter count` betekende iets anders dan gedacht.** Volgens de
  [DutchMeshCore-filtergids](https://toolbox.dutchmeshcore.nl/#/filter-guide) zijn
  `[TYPE: HOPS,RATE]`-regels **tellers** per type (weggegooid op de hoplimiet,
  weggegooid op de snelheidslimiet), geen instellingen. Ze stonden als limieten
  op het scherm: een tabel vol nullen die "geen limiet gezet" leek te zeggen
  terwijl er "nog niets weggegooid" stond. De instellingen komen nu uit
  `filter hops` (`[TYPE: MAX_HOPS]`) en `filter rate` (`[TYPE: LIMIT,SECS]`),
  allebei toegevoegd aan de standaard parameterlijst.
- **Regels per pakkettype als tabel** op de nodepagina, voorgevuld met de
  gemelde waarden, met de standaard van die firmware ernaast en de weggegooide
  aantallen per type erbij. Leeg betekent "nog niet gemeld", nooit nul. Plus de
  twee voorbeeldopstellingen uit de gids, als referentie.
- **De knop "status opvragen" belooft niets meer dat niet gebeurt.** Een poller
  zegt nu met `?caps=` op `/api/v1/commands` wat hij waarmaakt; de MeshUptime-node
  meldt `settings` en laat statusverzoeken vallen, dus de knop staat uit met die
  reden erbij. Een poller die niets zegt kan alles, zoals voorheen.
- Twee sjablonen verwezen nog naar `route.ha`, dat sinds 2.10.0 `route.poller`
  heet. Daardoor stond de knop op de publieke repeaterpagina uit en meldde de
  nodelijst "geen weg" waar er een poller was.

## 2.11.0 — 2026-09-04

Het filter van een **stock-repeater met filterpatch** (JessaZH) is nu vanuit de
site te lezen én te zetten, via de MeshUptime-node als poller.

- **Lezen**: twee CLI-antwoorden, samengevoegd. Gemeten op JessaZH geeft het kale
  `filter` de statusregel met tellers en `filter count` alleen de limiettabel
  (één pakket elk; de node vlakt regeleindes tot spaties). `pfstock` herkent
  beide los en `apply_cli_filter` voegt ze cumulatief samen; `cmd:filter` staat
  nu naast `cmd:filter count` in de standaard parameterlijst.
- **Zetten**: op de nodepagina een formulier "Zetten via de poller" zodra de
  repeater doorgestuurd wordt, de filterpatch draait en er een verse poller is.
  Elke regel gaat als `cmd:filter …` de wachtrij in, met erachter `filter` en
  `filter count` zodat de nieuwe stand terugkomt; dezelfde risicoweging,
  bevestiging en meting (`pfguard`) als bij de IP-weg. Alleen de stock-syntaxis
  (`on|off|reset|hash|hops|rate|malformed|channel`; geen `type`).
- **Indeling**: een blok dat later bijkwam (pakketfilter) komt op zijn
  standaardplek — vóór "Overig" — in plaats van achteraan bij een oudere
  opgeslagen indeling.

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
