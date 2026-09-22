# Changelog — MeshManager (site)

De versie van de site staat in `server/app/version.py` en in de footer van elke
pagina als `v<versie> · <commit> · <bouwdatum>`. Dit bestand krijgt een regel bij
elke ophoging; de commit-hash in de footer zegt wélke build van die versie het
is. Firmware heeft zijn eigen versies (`firmware/`, tags `fw-v…`); de
MeshUptime-node en de T1000-E-companion staan in de MeshUptime-repository.

Schema: MAJOR bij een breuk in de API of de databank, MINOR bij een merkbare
functie, PATCH bij een fix. Begonnen op 2.10.0 — zie de toelichting in
`version.py` voor waarom niet 1.0.0.

## 2.27.2 - 2026-09-22

- **Tropo-veld over het hele ICON-EU-gebied** (23° W tot 62° O, 29° tot 70° N) in plaats van
  alleen het kaartgebied van de tegels: 56.925 punten op 0,25°, zodat de laag bij uitzoomen
  geen rechte rand meer heeft door Ierland en Polen. Gradiënten als gehele N/km; ~230 kB
  per uur vóór compressie.

## 2.27.1 - 2026-09-22

- **Tropo-veld gekalibreerd** tegen de Hepburn-kaarten (dxinfocentre.com, 00 UTC 22-09).
  Superrefractie wordt nu alleen over lagen van minstens 500 m genomen (alle paren van de
  vijf niveaus die dik genoeg zijn); een dunne laag telt alleen mee als ze echt vangt
  (onder -157 N/km, een duct). De dunne laag 1000→950 hPa pikte 's nachts boven land de
  grondinversie op en kleurde Nederland en het Ruhrgebied "sterk" waar Hepburn marginaal
  tot redelijk gaf, terwijl de echte ducts boven de Golf van Biskaje en de Noordzee dun
  zijn en zonder die uitzondering zouden verdwijnen. Voor MeshCore (868 MHz, nodes op
  daken) telt die grondinversie wél voor de helft mee: -140 N/km in een dunne laag wordt
  niveau 3 (matig) in plaats van 6.

## 2.27.0 - 2026-09-22

- **Tropo-veld uit ICON-EU** (DWD Open Data, `app/icon.py`). De server kijkt elk half uur
  of er een nieuwe complete ICON-EU-run staat (om de 3 uur) en haalt dan T, RELHUM en FI
  op 1000/950/925/900/850 hPa voor de tijdstappen 0..39 uur (195 GRIB2-bestanden, ~200 MB),
  decodeert ze met eccodes en rekent de refractiviteitsgradiënt uit op een raster van
  0,25° (6279 punten, was 0,5° met 1610). Geen aanvraaglimiet meer; vijf lagen van
  ~400 m laten meer ducting zien dan drie van ~700 m. Open-Meteo blijft terugval als
  DWD onbereikbaar is (`MM_TROPO_SOURCE=openmeteo` dwingt dat af). `/api/tropo` geeft
  nu ook `source` en `run` mee en aanvaardt `h` tot 39. Nieuw in requirements: eccodes, numpy.

## 2.26.1 - 2026-09-22

- **Tropo-veld om de 6 uur** in plaats van elk uur: Open-Meteo telt per rasterpunt met
  een daglimiet van 10.000 per IP, en 1610 punten per uur gaf op de server zelf 429.
  Het antwoord bevat 48 uur voorspelling, dus het veld blijft tussen twee beurten
  bruikbaar. Herkansing na een fout na 15 minuten.

## 2.26.0 - 2026-09-22

- **Tropo-veld voor MeshChat** (`GET /api/tropo?h=0..36`, `app/tropo.py`). De server
  haalt één keer per uur de drukniveaus 1000/925/850 hPa van Open-Meteo op voor heel
  West-Europa (raster 0,5°, 1610 punten, 17 aanvragen) en serveert de
  refractiviteitsgradiënt als één JSON van ~10 kB, met CORS en 10 minuten cache, ook
  op de chat-hostnaam. Reden: MeshChat vroeg dit eerst per client en per kaartbeeld
  aan Open-Meteo (tot 220 punten per keer) en liep tegen de limiet per IP (429).
  `MM_TROPO=0` zet de ophaal-lus uit. MeshChat 0.3.4 gebruikt dit veld en valt
  terug op Open-Meteo zelf als de server onbereikbaar is.

## 2.25.0 - 2026-09-21

- **MeshChat op chat.meshmanager.net.** Op die hostnaam (instelbaar met
  `MM_CHAT_HOST`) serveert de site de chat op `/`, met `/sw.js`,
  `/manifest.webmanifest` en `/tiles` erbij; `/chat/...` stuurt daar door naar `/`.
  Alles anders op die hostnaam is 404. `meshmanager.net/chat/` blijft bestaan
  voor wie de app al geïnstalleerd heeft. Reden voor een eigen origin: een PWA,
  zijn service worker en zijn opslag hangen aan de origin, en een korte hostnaam
  is wat mensen elkaar doorgeven. De cloudflared-tunnel krijgt die hostnaam als
  extra publieke hostnaam naar dezelfde poort 8080.

## 2.24.0 - 2026-09-21

- **Kaarttiles ook voor MeshChat buiten deze site.** `/tiles` krijgt
  `Access-Control-Allow-Origin: *` (met `Range` als toegestane kop en de
  `Content-Range`-koppen zichtbaar) en een 204 op de OPTIONS-preflight. Reden:
  MeshChat draait ook als los HTML-bestand en als PWA op een andere host, en
  toont daar dezelfde offline kaart met de vector-tiles, glyphs en sprites van
  deze server. Een Range-request is geen "simple request", dus zonder preflight
  en zonder deze koppen weigert de browser hem. Alleen `/tiles`: openbare
  OSM-afgeleiden zonder iets persoonlijks; de rest van de site blijft
  same-origin.

## 2.23.0 - 2026-09-21

- **MeshChat op `/chat`.** De IRC-achtige webclient voor companion-radio's
  (repo DinXke/MeshChat) staat nu op meshmanager.net/chat: één zelfstandig
  HTML-bestand dat via Web Serial of Web Bluetooth rechtstreeks met de node van
  de bezoeker praat. De server serveert alleen de pagina en ziet geen bericht,
  geen sleutel en geen contact -- alles blijft in de browser. Reden om het hier
  te zetten en niet op een losse host: Web Serial en Web Bluetooth eisen https,
  en die is er hier al.
- **Bijwerken is een kopie.** `server/app/static/chat/index.html` is de
  gebouwde `meshchat.html` uit die repo; bij een MeshChat-release wordt ze met
  de hand vervangen. Geen submodule en geen build-stap, om dezelfde reden als
  bij de rest van deze site: er is geen build-stap, en dat blijft zo.
- **Twee koppen aangepast voor die ene map.** `/chat` krijgt dezelfde
  `Cache-Control: no-cache` als `/static` (anders serveert de browser na een
  release nog dagen de oude client), en mag als enige pad de browserlocatie
  vragen (`Permissions-Policy: geolocation=(self)`), want de gebruiker zet er
  zijn eigen node-positie mee. De CSP blijft ongewijzigd: de client gebruikt
  inline script en stijl, en `'unsafe-inline'` stond er al voor de kaarten.
- **Installeerbaar.** MeshChat is een PWA: `static/chat/manifest.webmanifest`
  en een service worker (`static/chat/sw.js`) die altijd eerst het netwerk
  haalt en pas bij een storing de laatst gecachte pagina geeft -- een nieuwe
  release komt dus direct door, ook op een geïnstalleerde app. Daarvoor is
  `'self'` toegevoegd aan `worker-src` in de CSP; `blob:` blijft voor de
  kaart-workers.

## 2.22.0 - 2026-09-15

- **Accubewaking.** Op 14 september zakte de zonnerepeater DinX-Home van 3,55 V
  naar 2,81 V en viel stil — hij nam de statistieken van drie andere nodes mee,
  want hij was ook hun koerier. In `alerts` stond over die hele afloop **geen
  enkele regel**. De cijfers waren er wel; ze werden door niemand gelezen. Een
  lus kijkt nu elke vijf minuten naar de laatst gemeten accuspanning van elke
  node en schrijft een alarm als die onder een grens zakt. Standaard 3,50 V
  (waarschuwing) en 3,30 V (kritiek), in te stellen op de beheerpagina.
- **Op de spanning, niet op het percentage.** Dat laatste is afgeleid, en een
  drempel op een afgeleide is twee keer raden.
- **Vier regels tegen ruis**: alleen een verse meting (ouder dan zes uur zegt
  niets over nu — een node die niet meer meldt heeft geen lage accu, die heeft
  geen meting); alleen een bruikbare meting (onder 2 V meet een bord geen cel);
  alleen bij een overgang (laag blijven is geen nieuwe gebeurtenis, verder zakken
  naar kritiek wel); en terug omhoog pas met een marge, zodat een zonnecel die bij
  elke wolk rond de grens dobbert geen alarmenmolen wordt.
- **De trede staat in `settings`**, niet in het geheugen van de lus: een herstart
  van de site is geen gebeurtenis aan de accu.
- Geen `kind` op deze alarmen, met opzet: die dient om dezelfde gebeurtenis langs
  twee wegen te ontdubbelen, en omdat al deze teksten met "accu" beginnen zou die
  regel juist de escalatie van laag naar kritiek opeten.

## 2.21.0 - 2026-09-15

- **Instellingen zetten over de mesh, via de opdrachtwachtrij.** Er waren vier
  schrijfwegen, en voor een node zonder IP-pad was er maar één: `mesh`, over de
  MONITOR van die node. Viel die monitor weg, dan viel het schrijven weg —
  terwijl de node zelf bereikbaar bleef en de poller zijn instellingen nog elke
  ronde ophaalde. De pagina zei dan "de doorstuurder is hier zelf niet bekend",
  wat klopte maar niet het hele verhaal was. De vijfde weg gebruikt wat er dan wél
  is: de site legt `cmd:set <param> <waarde>` in de wachtrij en MeshUptime voert
  het over LoRa uit.
- **De set en de teruglezing in één sessie.** Er gaan twee opdrachten de wachtrij
  in — `cmd:set tx 20` en `tx` — zodat de poller met één login zet én terugleest.
  De teruggelezen waarde verschijnt in de instellingentabel waar de pagina hem toch
  al toont.
- **`applied` blijft leeg, en dat staat er ook.** Dit is de enige weg zonder
  teruglezing in hetzelfde verzoek: het commando vertrekt in een wachtrij die pas
  bij de volgende poll geleegd wordt. De melding zegt dat het in de wachtrij staat
  en niet dat het gelukt is — een gevraagde waarde als gemeten waarde vastleggen is
  precies waar deze module tegen gebouwd is.
- **Alle drempels gelden onverkort**: dezelfde ingang (`write()`), dus dezelfde
  parameterlijst, grenzen, risicoklassen, bevestiging en rechten. Het plafond is
  hetzelfde als bij de monitor-weg, en de firmware van de poller weigert bovendien
  zelf wat een node op een dak onbereikbaar maakt (`clkreboot`, `reboot`, `erase`,
  `set radio`, `set freq`, `ota`).
- **Geen poller ooit gezien, geen kandidaat.** Anders staat op elke nodepagina een
  afgevallen weg die deze installatie niet gebruikt — dezelfde regel als bij de
  eigen API van een node.

## 2.20.0 - 2026-09-15

- **De burenlijst komt weer binnen, nu uit de CLI.** De dakrepeater vroeg de
  buren van elke node die hij bewaakte op met het binaire `REQ_TYPE_GET_NEIGHBOURS`
  en publiceerde ze mee. Sinds hij wegviel stond `neighbor_count` op de kaarten
  zoals hij dagen eerder was. De weg terug hoeft geen tweede protocolimplementatie
  te zijn: stock firmware heeft een `neighbors`-commando in de gewone CLI, en over
  die weg praat MeshUptime toch al met deze repeaters. `nbstock.apply_cli_neighbors`
  vertaalt dat antwoord naar dezelfde bestemming als de MQTT-weg had: de
  `neighbors`-tabel plus de meting `neighbor_count`.
- **Zes hextekens, niet acht.** De CLI geeft vier byte sleutel; overal in deze
  databank is een buurprefix er drie (`contacts.prefix6`, `packets.sender`,
  `neighbors.prefix`), en de naamkoppeling zoekt letterlijk op `prefix6`. Acht
  bewaren zou elke buur een tweede rij geven náást zijn eigen historie, en hem
  tegelijk naamloos maken.
- **`-none-` is een uitkomst, `Unknown command` niet.** Een repeater die het
  commando niet kent mag geen buurloze node worden: onleesbaar levert None en
  verandert niets. SNR komt als SNR×4 over de lijn en wordt gedeeld.

## 2.19.1 - 2026-09-15

- **De batterij was niet weg, het percentage was afgeleid.** Een
  MeshCore-repeater meldt in zijn statusantwoord alleen de spanning
  (`batt_milli_volts`); het percentage dat tot nu toe in de databank stond kwam
  van de dakrepeater, die het zelf uit diezelfde spanning rekende. Nu MeshUptime
  de statussen ophaalt kwam er alleen nog `bat` binnen en stond de batterij op
  elke kaart leeg terwijl de spanning gewoon bekend was. `db.ingest()` leidt
  `battery_percentage` nu af als de bron er zelf geen meestuurt — één plek, dus
  voor beide ingest-wegen en voor elke poller die er later bij komt.
- **Dezelfde curve als de node had** (3000 mV = 0 %, 4200 mV = 100 %, onder 2 V
  geen bruikbare meting), letterlijk overgenomen uit `meshmanager_batt_percent()`
  en niet vervangen door een eigen betere: 3,89 V geeft hier 74 %, precies wat er
  historisch voor die node in stond. Een nieuwe curve zou een sprong in de grafiek
  tekenen op een dag dat er niets aan de batterij veranderde.
- **Nooit overschrijven.** Stuurt een bron wél een percentage mee (de
  dakrepeater, een sensornode met een echte brandstofmeter), dan blijft dat staan:
  die weet meer van die cel dan onze curve.

## 2.19.0 - 2026-09-09

- **Een kaart groot openen.** De knop `⛶` in de kaartbalk zet de kaart over de
  hele pagina (met een marge; op een telefoon helemaal beeldvullend), Escape of
  een klik naast de kaart brengt hem terug. Bewust een eigen overlay en niet de
  Fullscreen API: die haalt het element uit de opmaakstroom en dan verliest de
  kaart alles wat ernaast hoort — het pakketpaneel dat het pad tekent, de
  filters, de modals. Dit blijft dezelfde Leaflet-instantie in dezelfde DOM, dus
  de live-animatie, de pacman-modus, de druktelaag en het filter lopen door
  zonder iets te herbouwen; alleen het kader wordt groter. De pakketlijst blijft
  staan met een eigen schuifbalk, want die is de helft van deze weergave — hem
  verbergen zou de knop een verlies maken in plaats van een winst. Kaart en
  lijst verdelen de vrije ruimte 3:1 (4:1 op een telefoon) en de kaart heeft een
  ondergrens van 420 px — de hoogte die hij in de gewone weergave heeft — want
  met vaste maten kwam hij op een laag venster LAGER uit dan zonder de knop.
  Gemeten: op 1600×1000 gaat de kaart van 1080×420 naar 1515×589 (bijna twee
  keer het oppervlak), op een telefoon van 315×420 naar 354×505. Daar gaat de
  uitleg boven de kaart weg zodra hij groot staat: die loopt op dat formaat over
  vier regels.
  Geen code per kaart: elke kaartkaart krijgt de knop, dus de linkkaart op een
  nodepagina kan het ook.
- Vangnettest op de vertaalsleutels: elke sleutel die het script zelf opbouwt
  (`t("...")`) moet in **beide** talen bestaan, en de twee tabellen moeten
  dezelfde sleutels dekken. Waarom dat nodig is: een sjabloon draagt zijn
  Nederlandse tekst als inhoud en valt dus terug op leesbaar Nederlands, maar
  tekst die JavaScript maakt levert bij een ontbrekende sleutel de ruwe
  sleutelnaam op de pagina op — en dat ziet niemand tot een bezoeker op Engels
  staat.

## 2.18.2 - 2026-09-09

- **"None" is een antwoord, geen stilte.** Op de lucht gemeten (JessaZH.VIR02):
  op een lege kanaallijst antwoordt de stock-firmware met de tekst `None`. Dat
  woord stond in onze ruiswoordenlijst, dus de parser gaf `None` terug -- niet te
  onderscheiden van een node die zwijgt -- en de pagina zei "niet bekend" terwijl
  de node net had gezegd dat er niets in de lijst staat. Nu levert zo'n antwoord
  een LEGE lijst, en de filterkaart heeft drie standen in plaats van twee:
  kanalen, leeg-en-beantwoord, of onbekend (niet gevraagd / geen antwoord).
- **De echo van het commando eet de lijst niet meer op.** Deze firmware prefixt
  al haar antwoorden met `> ` -- de statusregel komt binnen als
  `> Filter on: ...`. Een build die `> filter channel list` echoot met het
  antwoord eronder verloor voorheen zijn hele lijst, omdat elke tekst die zo
  begon als onleesbaar gold. De usage-regel wordt nu herkend op haar
  syntaxtekens (`[ ] | <`) in plaats van op haar eerste woorden, zodat `Public`
  uit dat voorbeeld niet als "geblokkeerd kanaal" op de pagina belandt -- de
  bestaande test daarover ving die regressie.

## 2.18.1 - 2026-09-08

- De afleiding uit 2.18.0 werkte maar voor de helft van de tegels: de rates
  staan niet in `TILE_METRICS` en komen dus langs de tweede tegellus, waar hij
  niet stond -- het fossiel bleef staan. Nu op één plek (`_afgeleid`) en in
  beide lussen, met een test die de echte pagina rendert in plaats van alleen de
  rekenfunctie te controleren.

## 2.18.0 - 2026-09-08

- **Negen tegels toonden een cijfer van 13 augustus als "net nu".** De
  berichtenrates (ontvangst, verzending, flood, direct, dubbelen, RX-fouten)
  werden nooit door een node gemeten: de oude Home Assistant-weg rekende ze uit
  en stuurde ze mee. Sinds die weg uit de keten is, stond in `latest` het cijfer
  van de dag waarop hij afgesloten werd -- en de nodepagina zette dat onder
  "laatste update: net nu". De bijbehorende grafiek was leeg, want de reeks werd
  door niemand meer geschreven. Beide kanten komen nu uit de teller die de node
  wél stuurt: de tegel via `db.computed_rate` (hetzelfde venster als de
  benutting) en de grafiek via `db._RATE_BASIS` in `metric_history`. De historie
  is daarmee ook meteen volledig tot waar de tellers reiken, en niet pas vanaf
  vandaag.
- **Een tegel die niet meer meegemeten wordt, zegt dat nu.** Staat een meting
  meer dan 90 minuten voor de laatste melding van de node, dan komt de datum
  eronder ("gemeten 26 dagen geleden"). Dat vangt de resterende fossielen die
  met dezelfde weg verdwenen -- `Verzoeken gelukt`, `Verzoeken mislukt`,
  `Uitgaand pad`, `Volle wachtrij-events` -- zonder ze te verbergen. Bij een node
  die zelf stil is verschijnt er niets: daar is alles even oud en zegt de kop van
  de pagina dat al.
- Vangnettest: elke metric in de catalogus met eenheid `msg/min` moet een teller
  als bron hebben. Een nieuwe rate zonder bron valt nu om in de tests en niet op
  de site.

## 2.17.1 - 2026-09-04

- **"0 geweigerd" kon een oud cijfer zijn zonder dat het opviel.** Een
  doorgestuurde repeater publiceert zijn filterstand nergens: wat op de pagina
  staat is zo oud als de laatste keer dat iemand het vroeg. Na een herstart
  stonden de tellers op nul, en wie daarna berichten stuurde zag die nul staan --
  wat leest als "het filter werkt niet" terwijl het "we hebben het sindsdien
  niet gevraagd" betekende. Twee wijzigingen: de ouderdom staat er nu als
  RELATIEVE tijd ("gemeten 3 uur geleden") in plaats van als ISO-tijdstempel, en
  er is een knop **Filterstand nu ophalen** die de vijf leescommando's in één
  LoRa-sessie in de wachtrij zet. Een leesactie (`node.uitvragen`): hij verandert
  niets aan de repeater.

## 2.17.0 - 2026-09-04

- **De klok van een repeater die VOORLOOPT is nu vanaf de site recht te zetten**
  ("Klok rechtzetten" in de kloksectie). Dat kon niet: zijn firmware weigert een
  klok achteruit (`ERR: clock cannot go backwards`), dus kost het een
  `clkreboot` -- een HERSTART -- en tussen die herstart en het gezette uur
  negeren andere nodes zijn adverts. Vandaag met de hand gedaan op JessaZH (18
  minuten voor, in één poging goed), en dat is precies waarom de site het niet
  zelf uitvoert: tussen de twee stappen mag geen netwerkronde zitten. De node
  doet de hele reeks als één job; MeshManager zet `cmd:clockfix` in de wachtrij.
- **Een eigen recht in de zwaarste klasse** (`node.klokherstel`), met de naam van
  de node overtypen als drempel -- zelfde niveau als firmware schrijven en
  verwijderen. Wie de klok mag bijstellen (`node.klok`) mag daarmee niet ook een
  repeater op een dak herstarten.
- **De knop verschijnt alleen als de poller zegt dat hij het kan**
  (`?caps=…,clockfix`), en `clockfix` staat MET OPZET niet bij de capaciteiten
  die een zwijgende poller krijgt: `settings` en `refresh` deed de Home
  Assistant-integratie, een node herstarten deed hij nooit.
- De pagina zegt wat het kost: de herstart, de filtertellers op nul, en dat
  andere nodes zijn adverts blijven negeren zolang zijn oude tijdstempel in de
  toekomst lag (liep hij 18 minuten voor, dan nog ~18 minuten).

## 2.16.0 - 2026-09-04

- **`/api/v1/ingest` aanvaardt nu ook het vloot-pushtoken.** De MeshUptime-node
  levert daar sinds nodefirmware 2.7.0 de STATUS van een andere repeater af, die
  hij over LoRa is gaan vragen omdat die zelf niets publiceert. Dat is precies
  wat een monitor vandaag al over MQTT doet (Home publiceert de cijfers van
  JessaZH), dus geeft dit token er geen bevoegdheid die het toestel niet al had.
  Zonder deze regel kwam elke statusronde binnen met een 403 die alleen in het
  serverlog stond -- de node meldde "gelukt" (de LoRa-ronde wás gelukt) en de
  pagina bleef leeg. Een test bewaakt nu dat alle drie de pollerendpoints
  dezelfde sleutel aanvaarden.

## 2.15.3 - 2026-09-04

- **De rollen op een echte pagina getest, per variant en per rol.** De ratel uit
  2.15.0 telt of er een poort OP een formulier staat; dat is niet hetzelfde als
  de JUISTE poort (een pagina waar alles voor iedereen uitstaat haalt die ratel
  ook). `tests/test_rollen_op_de_nodepagina.py` bouwt vier gebruikers met elk een
  rol op een echte node, rendert de echte route, en legt twee dingen vast: de
  ORDENING (ruimere rol = nooit meer uitgeschakeld) en het PLAFOND (elk formulier
  dat aanstaat hoort bij een handeling onder het plafond van die rol). Een
  formulier zonder vermelding in de rechtentabel van die test laat de laatste
  test falen, zodat een nieuw formulier niet stil buiten de controle valt.
  Gemeten op de vijf nodevarianten van `tools/demo_data.py`: lezer 11 van 12
  knoppen uit, bediener 8, technicus 1, beheerder 0 -- en elke knop die aanstaat
  valt binnen het plafond.
- **`tools/demo_data.py` maakte een admin zonder serverbeheerdersrecht**, dus gaf
  de demo-installatie 403 op elke nodepagina en op de serverpagina. Sinds het
  rechtenmodel mag een gewone gebruiker niets tot hem per node iets toegekend is;
  een demo waarin de beheerder nergens in mag toont het verkeerde.

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
