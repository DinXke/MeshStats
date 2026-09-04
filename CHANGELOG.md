# Changelog — MeshManager (site)

De versie van de site staat in `server/app/version.py` en in de footer van elke
pagina als `v<versie> · <commit> · <bouwdatum>`. Dit bestand krijgt een regel bij
elke ophoging; de commit-hash in de footer zegt wélke build van die versie het
is. Firmware heeft zijn eigen versies (`firmware/`, tags `fw-v…`); de
MeshUptime-node en de T1000-E-companion staan in de MeshUptime-repository.

Schema: MAJOR bij een breuk in de API of de databank, MINOR bij een merkbare
functie, PATCH bij een fix. Begonnen op 2.10.0 — zie de toelichting in
`version.py` voor waarom niet 1.0.0.

## 2.15.2 - 2026-09-04

- **"Er is geen weg naar deze repeater" was onwaar bij JessaZH.** Zijn
  instellingen zijn prima op te vragen (de MeshUptime-node doet dat over LoRa);
  alleen een statusbericht niet, want dat gaat over een protocol dat de poller
  nog niet kent. De server bouwt nu de echte zin (`route["refresh_why"]`), die
  de poller bij naam noemt, zegt wat er wél werkt en niet beweert dat er geen
  weg is. Zowel op de nodepagina als op de publieke repeaterpagina. Een reden
  die niet klopt is erger dan een uitgeschakelde knop: hij stuurt de lezer naar
  de netwerkkabel in plaats van naar de knop ernaast die het wel doet.

## 2.15.1 - 2026-09-04

- **De nodepagina en `/api/v1/repeaters/<slug>` gaven 500 zodra er werkelijk een
  kanaal geblokkeerd was.** `pktfilter.summarise` deed `int()` op `channels`,
  en dat veld is in het statistiekenbericht van onze eigen firmware een LIJST
  van geblokkeerde kanalen (`{label, hash}`) en elders een geteld aantal. Een
  sluimerende fout van maanden: zolang er nergens een kanaal geblokkeerd was,
  deed `int(None or 0)` gewoon zijn werk. Nu één plek (`_aantal`) die beide
  vormen aankan, voor `channels`, `blocked_types` en `hash`. De nieuwe tests
  leggen niet één vorm vast maar bewijzen dat geen enkele vorm nog een pagina
  kan neerhalen -- de blob komt van een node, dus alles kan erin staan.

## 2.15.0 - 2026-09-04

- **Beheerpagina's herverdeeld op één stramien.** De nodepagina is opgesplitst
  in secties met een plakkende inhoudsopgave (`admin/node/_*.html`), in
  oplopende onomkeerbaarheid; firmware, verwijderen en het audittrail staan
  ingeklapt achteraan. Eén formulierrij (`.frm`: label, veld met hulptekst,
  knop rechts), feitenlijsten (`.kv`), tabellen die op 375 px kaartjes worden
  (`.stack`), en de drie risicoklassen uit `nodeconfig`/`rbac` als kleur en
  etiket. Lange toelichtingen zijn uitklappers; de tekst zelf is niet weg.
  Zie `docs/nl/beheer-ux.md`.
- **Elk beheerformulier staat zichtbaar uit voor wie het niet mag.** Een
  `<fieldset>` met de rechtenpoort schakelt velden én knop uit, met de reden
  in de tooltip; de nodepagina zegt bovenaan welke rol je hebt en wat die
  betekent. De grendel op de server was al dicht (`require_perm` in elke
  route); de poort in het sjabloon ontbrak op 86 formulieren.
  `tests/test_rechtenpoorten.py` is de ratel die dat aantal alleen laat dalen.
- **Vindbaarheid.** De tab *Beheerders* heet *Monitors* (dat is wat hij is),
  de servertab *Server, gebruikers en site*, met een sub-balk naar zijn
  secties; het aanmaken van een gebruiker is een echt formulier bovenaan, met
  bij het vinkje serverbeheerder wat die rol mag en wat een gewone gebruiker
  standaard mag (niets).
- Routes, veldnamen, `csrf`- en `confirm`-velden zijn ongewijzigd; de
  publieke pagina's zijn niet aangeraakt.

## 2.14.1 - 2026-09-04

- **Een grafiek open je door op de grafiek te klikken**, niet op een knopje
  eronder -- dezelfde handeling als op een tegel erboven. De hele kaart is de
  knop, met rol, tabstop en Enter/spatie erbij; die worden door app.js gezet en
  niet in het sjabloon, want zonder JavaScript doet een klik niets en dan mag er
  ook niets staan wat zich als knop voordoet. (De tegels hebben die
  toetsenbordtoegang nog niet; dit is de vorm waar ze naartoe moeten.)
- **`filter_total` teruggerekend over de historie die er al lag**
  (`server/tools/backfill_filter_total.py`). De reeks bestond pas vanaf de
  uitrol, terwijl de drie componenten zeven dagen aan punten hadden -- dus stond
  het totaal niet op de grafiek. Het script vult alleen tijdstippen waar ALLE
  DRIE de componenten een punt hebben: een som van twee van de drie zou een
  lager totaal en dus een hoger weigeringspercentage suggereren dan er was.
  Draai hem per periode (`--uren 168` en `--uren 24`), want de tijdreeksdatabank
  antwoordt per periode op een eigen stap-raster.

## 2.14.0 - 2026-09-04

- **Een gezamenlijke filterstatistiek.** Naast de losse tegels nu een frame met
  de VERHOUDING: beoordeeld totaal, doorgelaten, weggegooid. Dat is een andere
  vraag dan "hoeveel gooide hij weg" -- 200 geweigerd op 220 is een repeater die
  niets meer doorlaat, 200 op 20.000 is een filter dat zijn werk doet. Daaronder
  een tweede frame met de zes redenen naast elkaar; apart, omdat die reeksen
  ordes van grootte lager liggen en in een gedeelde as allemaal op de nullijn
  zouden vallen.
- **De noemer is een echte meetreeks** (`filter_total` = doorgelaten +
  weggegooid + vrijgesteld via de ACL), en alleen waar de node een
  doorlaatteller meldt. Zonder die teller zou de som van de weigeringen de
  noemer worden en las elke grafiek als "100% geweigerd" -- een cijfer dat klopt
  met de opgeslagen data en toch onwaar is. Een stock-repeater meldt geen
  `passed`, dus daar blijft die lijn gewoon weg.
- **Elke vaste grafiek is nu open te klikken** ("Groter met meer periodes"): het
  bestaande grote frame nam maar één reeks aan en kan er nu meerdere tekenen,
  met de knoppen voor een langere periode. Een reeks die deze firmware niet
  meldt, valt daar uit de legenda in plaats van de rest mee te trekken.

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
