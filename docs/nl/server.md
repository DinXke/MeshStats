# De server

*[English](../server.md)*

Wat er in `server/` draait, waar de gegevens vandaan komen, en hoe de delen aan
elkaar hangen. [`architecture.md`](../architecture.md) beschrijft het systeem als
geheel, firmware inbegrepen; dit document blijft aan de serverkant en gaat
dieper.

## In één alinea

`server/app/main.py` bouwt een **FastAPI**-toepassing bovenop één
**SQLite**-bestand. Nodes met de MeshStats-firmware publiceren over **MQTT**;
`mqtt_ingest.py` schrijft zich daarop in en noteert wat binnenkomt. Numerieke
historiek gaat naar **VictoriaMetrics**, met de SQLite-tabel `samples` als
vangnet dat alles opvangt wat de tijdreeksdatabank niet aankan. Home Assistant
mag nog steeds over de HTTP-API pushen, maar is nergens meer de bron van, en de
site werkt zonder.

## De modules

| Bestand | Verantwoordelijkheid |
|---|---|
| `app/main.py` | Applicatieobject, middleware, routers, opstartprocedure, `set-password`-CLI |
| `app/config.py` | Omgevingsvariabelen, datamap, de ondertekeningssleutel |
| `app/db.py` | SQLite: schema, migraties, ingest, elke query die de site draait |
| `app/packets.py` | Decoder voor ruwe MeshCore-frames. Pure functies, geen I/O |
| `app/candidates.py` | Adreshash-kandidaten wegen. Pure functies, geen I/O |
| `app/search.py` | De zoektaal van het archief, zijn sorteersleutels en zijn kolommenlijst. Pure functies, geen I/O |
| `app/mqtt_ingest.py` | MQTT-abonnee en de enige publicatie (`publish_command`) |
| `app/tsdb.py` | Schrijfthread naar VictoriaMetrics, en het lezen ervan |
| `app/clocksync.py` | Beslissen of deze machine het mesh de tijd mag vertellen, en het doen |
| `app/commanding.py` | Welke weg een opvraging naar een repeater nog heeft, en wat de pagina mag beloven |
| `app/retention.py` | De opruimlus, de VACUUM-afweging, en de opslagcijfers voor de beheerpagina |
| `app/routes_api.py` | `/api/v1/*` — ingest en de publieke JSON-endpoints |
| `app/routes_public.py` | De publieke HTML-pagina's |
| `app/routes_admin.py` | `/admin/*` — inloggen, repeaters, tokens, instellingen |
| `app/auth.py` | Tokens, wachtwoordhashing, ondertekende sessiecookies, CSRF |
| `app/ratelimit.py` | Bruteforcebegrenzing op het inloggen, in dit proces |
| `app/limits.py` | ASGI-middleware die de bytes van een request telt terwijl ze binnenkomen |
| `app/metrics.py` | Catalogus van bekende repeatermetrics: labels, eenheden, secties, meters |
| `app/countries.py` | Offline punt-in-polygoon tegen `app/data/borders.json` |
| `app/templating.py` | De Jinja2-omgeving plus de cachebuster `asset_v` |

Drie ervan — `packets.py`, `candidates.py` en `search.py` — importeren niets uit
de rest van de toepassing. Dat is opzet: zij bevatten de kennis die duur is om
opnieuw te verwerven, en een pure functie is de vorm die je kunt testen zonder
databank, broker of request.

## Waar de gegevens vandaan komen

Er zijn twee ingestwegen, en ze komen samen bij dezelfde `db.ingest()`.

### MQTT — de nodes zelf

De hoofdweg. Een node houdt één MQTT-verbinding open en publiceert; de server
schrijft zich met een jokerteken in. Twee topicpatronen komen binnen:

| Topic | Afhandeling | Wat het draagt |
|---|---|---|
| `meshcore/<node>/stats` | `mqtt_ingest._handle_payload()` | Een JSON-momentopname: `repeater`, `metrics`, optioneel `neighbors`, optioneel `settings` |
| `meshcore/<node>/rx` | `mqtt_ingest._handle_rx()` | Eén opgevangen LoRa-frame als hex, plus `snr`, `rssi`, `len` |

`<node>` is de sleutelprefix van de publicerende node. De firmware stuurt hem in
hoofdletters; `_topic_node()` zet hem om, want elke tabel verderop is gesleuteld
op kleine letters.

**Het topic noemt de publicist, de payload noemt het onderwerp.** Meestal is dat
dezelfde node die over zichzelf rapporteert, en ze mógen verschillen, want een
node stuurt ook statistieken door voor repeaters die hij monitort — dat is
precies hoe de dakrepeater waarvoor dit project gebouwd is de site überhaupt
bereikt. Dus:

- geen `repeater.pubkey_prefix` in de payload betekent dat de node over zichzelf
  praat en het topic het onderwerp levert;
- staan beide er, dan kiest de payload het onderwerp en wordt de topicprefix op
  de repeaterrij bewaard als `source_prefix` (`db.record_source()`).

Dat begrenst de schade van één gedeeld brokeraccount maar heft ze niet op. De
echte oplossing hoort op de broker: één MQTT-gebruiker per node, elk met een ACL
beperkt tot zijn eigen topicprefix. Zie [`mqtt.md`](../mqtt.md#acl) en
[`security.md`](../security.md#the-actual-fix-one-broker-account-per-node).

**Eén slecht bericht legt de lus nooit stil.** `handle_message()` vangt alles,
telt het in `_state["errors"]` en logt de fout *samen met een begrensd, altijd
afdrukbaar stuk van de payload* (`_excerpt()`, hoogstens `MAX_LOG_EXCERPT` = 240
tekens, `backslashreplace` en niet `replace`). De reden is concreet: een
nodenaam met een aanhalingsteken erin liet ooit een node uit de statistieken
verdwijnen, en `Expecting ',' delimiter: line 1 column 87` zegt niet wat er op
kolom 87 stond.

### HTTP — Home Assistant of een eigen script

`POST /api/v1/ingest` neemt hetzelfde JSON-lichaam aan en wordt met een
Bearer-token geauthenticeerd. De Home Assistant-integratie in `homeassistant/`
gebruikt hem, en die kan één ding dat geen node kan: praten *tegen* repeaters die
niet de zijne zijn, over LoRa, via `meshcore.execute_command`.

Deze weg is optioneel en is nergens meer de bron van. De site zegt dat in de
moduledocstring van `main.py` zelf, en `commanding.py` bestaat omdat de
beheerpagina dat een tijdlang niet deed: ze bleef beloven "Opvraging gestart —
Home Assistant logt in op de repeater" terwijl het verzoek in een wachtrij lag
die niemand nog leegde.

## Opstarten

`main.bootstrap()` draait op het `startup`-event van FastAPI, in deze volgorde:

1. `db.get_conn()` — SQLite openen, schema toepassen, `COLUMN_MIGRATIONS`
   draaien, decoderkolommen bijvullen uit `packets.raw`.
2. Het `admin`-account aanmaken als de tabel `admins` leeg is, en het gegenereerde
   wachtwoord **eenmalig** naar stdout schrijven.
3. `db.prune()` — de bewaartermijn meteen toepassen in plaats van bij een latere
   aanleiding.
4. `retention.start()` — de uurlijkse opruimlus, zodat de bewaartermijn een regel
   is die geldt in plaats van een handeling die bij het opstarten gebeurde.
5. `db.classify_countries()` — elk geplaatst contact een land geven. Ingest
   classificeert alleen wanneer een positie *verandert*, en de meeste nodes
   verhuizen nooit, dus zonder deze ronde zou een bestaande databank de kolom
   nooit vullen.
6. `tsdb.start()` — de schrijfthread, gestart **voordat** er een ingestweg
   opengaat, zodat de eerste meting niet zonder reden de uitwijkroute neemt.
7. `mqtt_ingest.start()` — de abonneethread.
8. `clocksync.start()` — als laatste, want die publiceert en heeft de client van
   de stap hierboven nodig.

## De threads

Het proces is één uvicorn-worker plus vier daemonthreads.

| Thread | Gestart door | Wat hij doet | Als hij sterft |
|---|---|---|---|
| `mqtt-ingest` | `mqtt_ingest.start()` | `paho`-lus: inschrijven, decoderen, wegschrijven | `_run()` vangt alles en verbindt na 10 s opnieuw; paho zelf probeert het met 2–60 s uitstel |
| `tsdb-writer` | `tsdb.start()` | Leegt de puntenwachtrij, batcht, POST | `_run()` vangt per batch en wijkt uit naar SQLite |
| `clocksync` | `clocksync.start()` | Slaapt `FIRST_RUN_DELAY_S` (300 s), daarna elke `INTERVAL_HOURS` een ronde | `_run()` vangt per ronde en noteert de fout in `_state` |
| `retention` | `retention.start()` | Slaapt 600 s, snoeit daarna elke `INTERVAL_MIN` en overweegt een VACUUM | `_run()` vangt per ronde en noteert de fout in `_state` |

Drie ervan starten niet als hun functie niet ingesteld is: geen
`MCS_MQTT_HOST`, geen abonnee; geen `MCS_TSDB_URL`, geen schrijver;
`MCS_CLOCKSYNC_ENABLED=0`, geen planner. Elk zegt dat in het logboek in plaats
van er stilzwijgend niet te zijn.

SQLite wordt bereikt via één verbinding op moduleniveau, bewaakt door een
`threading.Lock` (`db._lock`). Dat volstaat, want de belasting is een handvol
kleine schrijfacties per minuut plus paginaleesacties — en het is meteen de reden
dat de ingestweg nooit mag blokkeren: `tsdb.record()` zet alleen in de wachtrij,
en `db.ingest()` roept hem *buiten* het slot aan.

## Middleware en headers

Twee middlewares, geregistreerd in `main.py`, en de volgorde doet ertoe.

`limits.BodySizeLimitMiddleware` wordt met `add_middleware` toegevoegd, wat hem
vooraan invoegt — waardoor hij net binnen de `security_headers`-middleware komt
te staan. Een te grote body wordt dus geweigerd voordat enige route,
formulierparser of JSON-decoder hem ziet, en de 413 krijgt op weg naar buiten
alsnog de beveiligingsheaders mee.

Hij telt de bytes **terwijl ze binnenkomen** in plaats van `Content-Length` te
geloven, want een chunked request stuurt helemaal geen lengte: de oude controle
las `0` en elke te grote body zeilde erdoor, terwijl beheerformulieren nooit
gecontroleerd werden. `routes_api.limit_body()` kijkt nog wel naar de opgegeven
lengte, maar enkel als goedkope voorcontrole — voer de header niet opnieuw in als
*vereiste*.

`security_headers` zet, met `setdefault` zodat een route ze kan overschrijven:

| Header | Waarde |
|---|---|
| `X-Content-Type-Options` | `nosniff` |
| `X-Frame-Options` | `DENY` |
| `Referrer-Policy` | `strict-origin-when-cross-origin` |
| `Permissions-Policy` | `geolocation=(), microphone=(), camera=()` |
| `Cache-Control` | `no-cache`, enkel op `/static` |
| `Content-Security-Policy` | `default-src 'self'` plus de CDN-hosts waar Leaflet en de lettertypen vandaan komen |

`Cache-Control: no-cache` op `/static` verbiedt niet het bewaren; het dwingt
hervalidatie af, die `StaticFiles` met een goedkope 304 beantwoordt. Zonder dat
past een browser heuristische caching toe en draait een lezer de `app.js` van
gisteren tegen de API van vandaag. Bestandsnamen met een hash zijn afgewezen
omdat ze een buildstap vragen en deze site die met opzet niet heeft —
`templating.py` zet in plaats daarvan een `asset_v`-parameter op de URL's, die
bij elke processtart vernieuwt.

## Waar de metingen wonen

| | |
|---|---|
| VictoriaMetrics | enkel de metingen: historiek, en de grafieken die eruit getekend worden |
| SQLite | repeaters, `latest`, contacten, buren, pakketten, tokens, beheer |

`latest` blijft met opzet in SQLite: het voedt de kaartjes op de startpagina, die
snel en zonder netwerk moeten renderen, en "de ene huidige waarde" is niet een
vorm waar een tijdreeksdatabank goed in is.

**Waarom überhaupt verhuizen.** Nodes gaan van een meting per vijf minuten naar
een per tien seconden. In SQLite betekent dat ruwe punten weggooien om het
bestand hanteerbaar te houden; VictoriaMetrics comprimeert tot ruwweg een byte
per punt, dus volledige resolutie bewaren is daar goedkoper dan hier uitdunnen.

### De naamgeving ligt vast

De bestaande historiek is onder deze namen gemigreerd, en elke afwijking splitst
een reeks stilzwijgend in tweeën:

```
schrijven (influx line protocol, POST /write):
    meshstats,repeater=<slug> <metric>=<value> <nanoseconden>
lezen (PromQL):
    meshstats_<metric>{repeater="<slug>"}
```

Metricnamen komen uit firmware, en die mag verzinnen wat ze wil, dus
`tsdb.safe_metric()` laat alles buiten `[A-Za-z0-9_]` vallen in plaats van te
vervangen — vervangen zou een andere naam opleveren dan de migratie deed. De
SNR-reeksen per buur (`neighbor_<prefix>`, tientallen per repeater) gaan dezelfde
weg als al de rest.

### Twee regels waarvoor de module bestaat

1. **Schrijven mag de ingest niet ophouden.** `tsdb.record()` zet punten in een
   begrensde wachtrij (`QUEUE_MAX_POINTS` = 20 000) en keert terug. De
   schrijfthread verzamelt tot `MAX_BATCH_POINTS` (2000) of `FLUSH_INTERVAL_S`
   (2,0 s), wat het eerst komt, en doet de HTTP met een `WRITE_TIMEOUT_S` van
   5 s en `WRITE_ATTEMPTS` = 2.
2. **Een databank die weg is, mag geen metingen kosten.** Alles wat niet
   geschreven kan worden, wijkt uit naar de SQLite-tabel `samples` via de
   callback `db.spill_samples`, die `db.py` registreert met
   `tsdb.register_spill()`.

Drie manieren om in `samples` te belanden, alle met hetzelfde gevolg:

| Situatie | Wat er gebeurt |
|---|---|
| `MCS_TSDB_URL` leeg | `tsdb.record()` wijkt meteen per punt uit |
| Schrijven mislukt twee keer | `_flush()` wijkt met de hele batch uit |
| Wachtrij vol | `record()` wijkt met dat punt uit |

Het lezen spiegelt dat. `tsdb.history()` geeft `None` terug voor alles waar de
beller niets aan kan doen — niet ingesteld, onbereikbaar, een fout antwoord — en
`db.metric_history()` leest dan `samples`. Een metriek die simpelweg geen
gegevens heeft, geeft een lege lijst terug, zodat "nog geen historiek" en
"databank niet beschikbaar" onderscheidbaar blijven. De uitwijk is met opzet stil
voor de bezoeker: die kan niets met de vraag welke databank een grafiek leverde,
en de beheerpagina meldt de gezondheid.

`samples` is dus **geen dood gewicht en mag niet weg**. Het is wat de overstap
omkeerbaar maakt: `MCS_TSDB_URL` leegmaken, herstarten, en de site doet weer wat
ze deed zonder één dag te verliezen.

### Een stap kiezen

PromQL wil een stap, en een grafiek van 90 dagen op volle resolutie is miljoenen
punten die niemand kan zien. `tsdb.step_for()` kiest uit een vaste ladder met
`TARGET_POINTS` = 600 als doel:

| Bereik | Stap | Punten |
|---|---|---|
| 4 u | 30 s | 480 |
| 24 u | 5 min | 288 |
| 7 d | 30 min | 336 |
| 90 d | 6 u | 360 |

Dat 24 u op 288 punten uitkomt, is een toevalligheid die het waard is te
behouden: precies de dichtheid die de grafieken hadden toen nodes om de vijf
minuten publiceerden, dus de overstap verandert niets aan hoe een grafiek eruit
ziet. De query is `avg_over_time(...[stap])` en geen kale selector, zodat elke
emmer de punten erin samenvat in plaats van er willekeurig eentje uit te nemen
die het dichtst bij de grens ligt — over 90 dagen is dat wat een piek voor het
verdwijnen behoedt. Vaste sporten in plaats van het bereik exact delen, zodat
twee grafieken van hetzelfde bereik het eens zijn over waar hun emmers beginnen.

`tsdb.window_values()` is de uitzondering: de berekende airtime-benutting heeft
de eerste en de laatste meting in een venster nodig en geen getekende curve, dus
die bevraagt op een vlakke stap van 60 s en slaat de ladder over.

## Bewaren en opruimen

`db.prune()` past drie grenzen toe in een vaste volgorde — leeftijd, dan een
rijmaximum, dan een bytemaximum — en geeft een rapport terug van wat ze deed.
`retention.py` is de planner eromheen: een uurlijkse ronde, de VACUUM-afweging,
en de cijfers die de beheerpagina nodig heeft om te zeggen of de ingestelde
termijn werkelijk gehaald wordt.

Pakketten krijgen hun eigen, veel kortere bewaartermijn dan metingen, omdat ze
ordes van grootte sneller binnenkomen en binnen dagen hun waarde verliezen — en
omdat `packets.raw` een rij ruwweg verdubbelt. Buren worden na een vaste week
opgeruimd. `latest`, `contacts` en `repeater_cli` worden nooit gesnoeid; die
worden begrensd door het aantal repeaters en contacten.

Naast de uurlijkse lus wordt `db.prune()` ook aangeroepen bij het opstarten
(`main.bootstrap()`), bij ongeveer elke 500e HTTP-ingest (`routes_api.ingest()`)
en per `PRUNE_EVERY_PACKETS` (2000) ontvangen pakketten
(`mqtt_ingest._handle_rx()`) — de pakkettenstroom stuurt zijn eigen opruiming
aan. Het opslaan van het instellingenformulier gaat via `retention.run_once()`,
zodat een verlaagde termijn hetzelfde pad aflegt als de uurlijkse ronde.

Het hele verhaal, inclusief waarom de FIFO op `id` loopt en niet op `ts` en
wanneer een VACUUM zijn kosten waard is, staat in [`retention.md`](retention.md).

## Landbepaling

`contacts.country` bevat een ISO 3166-1 alpha-2-code, of NULL voor "we kunnen het
niet zeggen". `countries.lookup()` beantwoordt dat uit `app/data/borders.json`
met een verwerping op de omhullende rechthoek gevolgd door ray casting, gaten
inbegrepen — wat maakt dat San Marino niet Italië is.

Twee regels vormen `countries.py`. **Nooit netwerk**: een site die een
geocodeerdienst zou bellen, zou stukgaan zodra die dienst dat deed, en zou
ondertussen elke nodepositie lekken die ze bezit. **Ontbrekende gegevens zijn
geen fout**: zonder het bestand is `countries.available()` onwaar, laat de API
zijn landenlijst weg en verschijnt het filter niet; niets anders merkt het.

Het classificeren gebeurt één keer per node, in `db.set_country()`, en alleen
wanneer een positie *geschreven wordt die van de opgeslagene verschilt* — een
gewone advert herhaalt een positie die we al hebben en kost niets. Het is
gesleuteld op `prefix6` en wordt met één `UPDATE` op elke rij met diezelfde
prefix toegepast, want één node kan meerdere contactrijen bezitten onder sleutels
van verschillende lengte (Home Assistant stuurt vijf sleutelbytes waar de eigen
firmware van een node er zes stuurt). Matchen op de letterlijke sleutel zou één
node twee landen geven, of geen.

NULL is een echt antwoord — op zee, buiten het gedekte gebied, of binnen enkele
honderden meters van een kust die de bron grof tekent — en de interface biedt het
als eigen filterkeuze aan in plaats van naar het dichtstbijzijnde land te raden.

## Logboek

Loggernamen, zodat een filter er een uit kan pikken:

| Naam | Module |
|---|---|
| `meshstats.mqtt` | `mqtt_ingest.py` |
| `meshstats.tsdb` | `tsdb.py` |
| `meshstats.clocksync` | `clocksync.py` |
| `meshstats.retention` | `retention.py` |
| `meshstats.countries` | `countries.py` |
| `app.routes_api` | `routes_api.py` (module-`__name__`) |

Twee regels die het waard zijn te behouden. Een **wachtrij die bij het lezen
gewist wordt, wordt altijd gelogd als ze uitgereikt wordt**, want zodra de poller
een verzoek heeft meegenomen, staat er nergens meer een spoor van
(`GET /api/v1/commands`). En een **functie die stilvalt zegt dat op WARNING**,
nooit op DEBUG: een geweigerde klokronde is precies de toestand waarin die
functie stopt met werken, en stil stilvallen is wat dit project niet doet.

## Verwante documenten

| Vraag | Document |
|---|---|
| Wat staat er in elke tabel en kolom? | [`database.md`](database.md) |
| Wat doet elk HTTP-endpoint? | [`api.md`](api.md) |
| Hoe schrijf ik een zoekopdracht? | [`search.md`](search.md) |
| Wat komt er uit een ruw frame? | [`decoder.md`](decoder.md) |
| Wie is deze hash van één byte? | [`candidates.md`](candidates.md) |
| Hoe zet de site de klok van een node? | [`clocksync.md`](clocksync.md) |
| Hoe lang wordt iets bewaard? | [`retention.md`](retention.md) |
| Hoe vraagt de site een node iets te doen? | [`commanding.md`](commanding.md) |
| Beheeraccounts, tokens, instellingen | [`admin.md`](admin.md) |
| Draaien en installeren | [`deployment.md`](deployment.md) |
