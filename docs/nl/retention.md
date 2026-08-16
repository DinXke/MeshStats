# Bewaren en schijfruimte

*[English](../retention.md)*

Hoe lang de site iets bewaart, wat garandeert dat de schijf niet volloopt, en
waarom de beheerpagina hardop zegt wanneer de ingestelde termijn niet gehaald
wordt.

Het snoeien zelf is `db.prune()`, waar de verbinding en het slot wonen. Alles
eromheen — wanneer het gebeurt, wat het opleverde, en hoe de beheerpagina daar een
eerlijk verhaal van maakt — is `server/app/retention.py`. Dezelfde scheiding die
`clocksync` aanhoudt, en om dezelfde reden: een module die de opslag bezit hoort
geen planner te zijn.

## Drie grenzen, in deze volgorde

| # | Grens | Instelling | Geldt voor |
|---|---|---|---|
| 1 | **Leeftijd** | `packet_retention_days` (7), `retention_days` (180) | Alles ouder dan zijn bewaartermijn gaat weg |
| 2 | **Rijen** | `packet_max_rows` (200 000) | Erboven gaan de oudste pakketten, hoe oud ze ook zijn |
| 3 | **Bytes** | `db_max_mb` (512) | Erboven op schijf gaan er nog meer van de oudste pakketten |

**Leeftijd is wat we willen; rijen en bytes zijn wat we beloven.**

Een termijn alleen is geen garantie. "Bewaar 30 dagen" zegt niets over hoeveel
schijf dat is — het is een belofte over tijd, gemaakt in de hoop dat het verkeer
blijft wat het was. Eén node die elk frame dat hij hoort begint door te spiegelen,
en 30 dagen is opeens gigabytes. Botsen de drie, dan gaan de oudste pakketten het
eerst.

Het verschil doet ertoe op de beheerpagina: op het ogenblik dat grens 2 of 3
snijdt, is de ingestelde termijn **niet** gehaald. Iemand die 30 dagen instelde
kijkt in werkelijkheid naar 12, en dat hoort op het scherm te staan in plaats van
in een logboek. Een gat in een grafiek zonder uitleg is precies de storingsvorm
waar dit project telkens omheen probeert te bouwen.

Buren worden apart opgeruimd op een vaste **7 dagen**: een buur die een week niet
gehoord is, is geen buur.

## FIFO loopt op `id`, niet op `ts`

Twee redenen, en de tweede is degene die bijt.

Het id **is** de invoegvolgorde, en dat is wat "wie het eerst binnenkwam gaat het
eerst weg" betekent, en het is de primaire sleutel — dus de ronde is een
indexopzoeking plus een bereikte DELETE in plaats van een scan.
`db._trim_oldest_packets()` doet precies dat: één `OFFSET`-opzoeking voor het id
van het *keep*-ste nieuwste pakket, en dan één bereikte delete eronder. De kosten
zijn onafhankelijk van hoe ver we over de grens zaten, en dat telt, want de ronde
met het meeste te verwijderen is de ronde die draait terwijl de machine al onder
druk staat.

En een tijdstempel zou **verkeerd** zijn in het ene geval waarin de twee het
oneens zijn: een node met een foute klok stuurt pakketten die nu bewaard worden
maar vorig jaar gedateerd zijn, en verwijderen op `ts` zou juist die het eerst
weggooien terwijl ze het verste zijn wat we hebben. (Wat meteen de reden is dat
[`clocksync.md`](clocksync.md) bestaat.)

`PACKET_FIFO_FLOOR` = 1000 is de ondergrens waar het bytemaximum nooit onder mag
snijden. Een bovengrens die alleen gehaald kan worden door de tabel helemaal leeg
te maken, is een verkeerd ingestelde bovengrens, en het eerlijke antwoord is dat
op de beheerpagina zeggen in plaats van een site zonder pakketten achter te
laten.

## De bytes meten

`db.db_bytes()` telt het hoofdbestand, `-wal` en `-shm` op. De WAL telt mee omdat
het echte schijf is: in WAL-modus draagt een drukke databank daar megabytes die
het hoofdbestand nog niet toont, en een bovengrens die dat negeert is een
bovengrens die stilzwijgend overschreden wordt.

Hoeveel bytes één pakketrij kost, wordt via **`dbstat`** gemeten over `packets`
en zijn drie indexen, want een schatting die er een factor twee naast zit
betekent een bytegrens die of veel te hard bijt of nooit convergeert. `dbstat` is
een compileeroptie (`SQLITE_ENABLE_DBSTAT_VTAB`) en ontbreekt echt op sommige
builds, dus `PACKET_BYTES_FALLBACK` = 400 byte staat in de plaats — een licht
afwijkende schatting die over een uur opnieuw draait is beter dan een ronde die
helemaal weigert te werken.

De gemeten referentie op de live server: 7 477 pakketten nemen ongeveer 2,5 MB in
beslag, indexen inbegrepen — ruwweg 335 byte per rij, waarvan zo'n 134 het ruwe
hexframe is — bij een instroom van ongeveer 3 738 pakketten per dag. 200 000
rijen is dus zo'n 53 dagen van het huidige verkeer in ongeveer 80 MB: acht keer
het standaardvenster van 7 dagen, dus het rijmaximum is een rem op een explosie
en geen tweede bewaartermijn die stilletjes de eerste overrulet.

## VACUUM

SQLite verkleint een bestand nooit bij een DELETE; de pagina's komen op een
vrije lijst en worden hergebruikt. Op zichzelf is dat prima — een tabel die in een
gelijkmatig tempo gesnoeid en weer gevuld wordt, bereikt een evenwicht en stopt
met groeien. Het houdt op prima te zijn zodra iemand een bewaartermijn
**verlaagt** of de bytegrens snijdt: dan is een groot stuk van het bestand voor
altijd vrije lijst, en de gebruiker die schijfruimte wilde terugwinnen ziet het
bestand niet bewegen.

`db.maybe_vacuum()` draait een volledige VACUUM wanneer **beide** drempels
gehaald zijn:

| Drempel | Waarde | Waarom |
|---|---|---|
| `VACUUM_MIN_FREE_BYTES` | 16 MB | Genoeg absolute verspilling om een herschrijving waard te zijn |
| `VACUUM_MIN_FREE_RATIO` | 0,20 | Genoeg relatieve verspilling dat het bestand merkbaar groter is dan zijn inhoud |
| `VACUUM_MIN_DISK_FACTOR` | 3,0 | VACUUM bouwt een volledige tweede kopie voor het wisselt, dus de schijf moet plaats hebben voor beide |

De vrije ruimte wordt uit de eigen boekhouding van SQLite gelezen
(`PRAGMA freelist_count`, `page_count`, `page_size`) en niet uit de
bestandsgrootte: een bestand van 200 MB waarvan 150 MB vrije lijst is, is een
heel ander geval dan 200 MB pakketten, en alleen het eerste is een herschrijving
waard.

De schijfcontrole **weigert liever dan dat ze risico neemt** met precies de schijf
die deze functie moet beschermen.

Er draait eerst een `PRAGMA wal_checkpoint(TRUNCATE)`, voordat er iets gemeten
wordt. In WAL-modus is het write-ahead log echte schijf die `db_bytes()` meetelt,
en een VACUUM laat er een grote achter — dus zonder dat checkpoint komt het
eerlijke antwoord "we hebben 40 MB teruggegeven" eruit als "de databank groeide",
en dat is het soort getal dat een gebruiker het hele paneel doet wantrouwen.

**`PRAGMA auto_vacuum=INCREMENTAL` is overwogen en twee keer verworpen.** Het
aanzetten voor een bestaande databank vraagt sowieso een volledige VACUUM — juist
de operatie die het moest vermijden — en eenmaal aan draagt elke paginaschrijving
voorgoed onderhoud van de pointermap mee, op de ingestweg, om een operatie te
besparen die op deze omvang seconden duurt en een paar keer per jaar draait.

VACUUM neemt voor zijn duur een schrijfslot: hier is dat het moduleslot waar elke
andere query toch al doorheen gaat, dus niets ziet ooit een half herschreven
databank.

## Wanneer het draait

| Aanleiding | Wat er draait |
|---|---|
| `main.bootstrap()` | `db.prune()` één keer, bij het opstarten |
| `retention.start()` | De lus: eerste ronde na `FIRST_RUN_DELAY_S` (600 s), daarna elke `INTERVAL_MIN` (60) minuten |
| `POST /admin/settings` | `retention.run_once()`, zodat een verlaagde termijn meteen geldt |
| `routes_api.ingest()` | `db.prune()` bij ongeveer elke 500e HTTP-ingest |
| `mqtt_ingest._handle_rx()` | `db.prune()` per `PRUNE_EVERY_PACKETS` (2000) ontvangen pakketten |

**Waarom periodiek en niet alleen bij het opstarten.** Tot deze lus bestond werd
er precies twee keer gesnoeid: bij het starten van de container en bij het
opslaan van de instellingen. Voor een site die om de paar dagen opnieuw uitgerold
wordt is dat toevallig genoeg. Voor een server die maanden aan één stuk draait —
en dat is precies wat deze doet zodra hij af is — betekent het dat er na de
eerste minuut nooit meer iets weggaat. De bewaartermijn is dan geen termijn maar
een opstartritueel, en de eerste keer dat iemand het merkt is als de schijf vol
is.

Een uur tussen twee rondes is ruim bemeten. Bij de gemeten instroom van ongeveer
3 738 pakketten per dag komen er per ronde zo'n 156 rijen bij; de FIFO-grens kan
er dus hooguit een uur overheen zitten, en dat is bij 200 000 rijen minder dan een
tiende procent. Vaker draaien zou dezelfde drie indexopzoekingen vaker doen zonder
dat er iets aan verandert.

`retention.run_once()` snoeit **eerst** en beslist pas daarna of het bestand
herschreven wordt. De volgorde is niet vrijblijvend: VACUUM geeft alleen ruimte
terug die al vrijgekomen is, dus ervoor draaien zou een dure herschrijving zijn
van precies de rijen die er een seconde later uit gaan.

## Wat een ronde rapporteert

`db.prune()` geeft een rapport terug in plaats van niets, want de beheerpagina
moet kunnen zeggen wanneer er voor het laatst gesnoeid is en hoeveel er weg
ging. Een snoeibeurt die stilzwijgend gebeurt, is de reden dat een gat in een
grafiek een avond debuggen wordt.

| Sleutel | Betekenis |
|---|---|
| `at` | Wanneer de ronde draaide |
| `samples`, `neighbors` | Rijen verwijderd uit die tabellen |
| `packets_age` | Pakketten verwijderd door regel 1 |
| `packets_rows` | Pakketten verwijderd door regel 2 |
| `packets_bytes` | Pakketten verwijderd door regel 3 |
| `limit_hit` | `""`, `rows` of `bytes` — welke bovengrens sneed |
| `over_by_bytes` | Hoe ver het bestand na de delete nog over de grens zat |
| `packets_left` | Overgebleven rijen |
| `oldest`, `newest` | Het tijdvenster dat de tabel nu dekt |
| `effective_days` | Dat venster in dagen — het getal om tegen `days` te leggen |
| `db_bytes` | Het bestand, WAL inbegrepen |
| `days`, `sample_days`, `max_rows`, `max_mb` | De grenzen die werkelijk gelden |

Het rapport wordt in `settings` bewaard onder `prune_last`, en niet alleen in het
geheugen: na een herstart is "wanneer is er voor het laatst gesnoeid, en hoeveel
ging er weg" nog steeds de vraag die de beheerpagina moet kunnen beantwoorden, en
een herstart is juist het moment waarop iemand kijkt.

`limit_hit` wordt op **WARNING** gelogd, de rest op INFO. Dat is het geval waarin
de ingestelde bewaartermijn niet gehaald wordt, en stil doorgaan zou betekenen
dat iemand pas maanden later ontdekt dat zijn "30 dagen" er in werkelijkheid 12
waren.

## De beheerpagina

`retention.overview()` legt de huidige meting (`db.storage_overview()` — één set
query's, zodat de pagina geen pakkettelling en tijdvenster kan citeren die een
seconde uit elkaar gemeten zijn) naast het laatst bewaarde rapport, plus het
oordeel dat daaruit volgt:

| Veld | Betekenis |
|---|---|
| `limit_hit` | Uit de **laatste ronde**, niet uit de huidige rijtelling |
| `falls_short` | Waar als een bovengrens sneed *én* het venster meer dan een halve dag te kort is |
| `over_ceiling` | Het bestand zit op dit ogenblik boven `db_max_mb` |

Het oordeel wordt hier geveld en niet in de sjabloon: de vraag "wordt de
ingestelde termijn gehaald" heeft drie antwoorden, en een sjabloon met drie
takken erin is een sjabloon waar het vierde geval uit valt.

`falls_short` wordt met opzet aan de laatste ronde afgelezen en niet aan de
huidige telling: dat het er op dit ogenblik net onder zit, wil niet zeggen dat er
een uur geleden niets is weggegooid dat er volgens de termijn nog had moeten
staan.

### Het instellingenformulier

`POST /admin/settings` schrijft, alles begrensd:

| Veld | Bereik | Opmerking |
|---|---|---|
| `heartbeat_min` | 1–1440 | |
| `retention_days` | 1–3650 | Metingen |
| `packet_retention_days` | 1–365 | Langer dan een jaar is een tijdreeksdatabank en geen pakkettenlog |
| `packet_max_rows` | `PACKET_FIFO_FLOOR`–50 000 000 | Lager dan de ondergrens kan de FIFO toch niet honoreren |
| `db_max_mb` | 16–1 000 000 | |

De drie pakketvelden hebben `0` als standaard in plaats van verplicht te zijn, en
dat is geen slordigheid: **0 betekent "dit formulier ging er niet over"** en laat
de bestaande waarde staan. Zonder dat zou een oudere pagina die nog in een
tabblad openstond, of een script dat alleen het hartslaginterval wil zetten, de
bewaargrenzen op nul zetten — en dat is precies de instelling waarvan het
verkeerd zetten data kost.

Opslaan gaat via `retention.run_once()` en niet rechtstreeks naar `db.prune()`,
zodat een verlaagde termijn hetzelfde pad aflegt als de uurlijkse ronde —
inclusief de VACUUM-afweging, want juist het verlagen van een termijn is het
geval waarin het bestand anders groot blijft terwijl de inhoud gesnoeid is — en
het resultaat staat meteen op de pagina waar de gebruiker net op klikte.

## Wat de instellingen bereiken

`db.retention_settings()` wordt **bij elke ronde** gelezen in plaats van bij het
importeren vastgelegd. Het hele punt van deze waarden naar de tabel `settings`
verhuizen, is dat een bewaartermijn verhogen werkt zonder herstart van de
container, en alles wat ze cachet voert precies de herstart weer in die dit moest
vervangen.

De heatmap is het zichtbare gevolg. `routes_api._heatmap_window_h()` is precies
daarom een **functie** en geen moduleconstante: een lezer die de bewaartermijn
naar 30 dagen zet, verwacht dat de heatmap bij de volgende ronde 30 dagen dekt en
niet pas na een herstart. Het venster maakt bovendien deel uit van de
cachesleutel — anders zou de instelling wijzigen tot vijf minuten van een gecachte
laag opleveren die stilzwijgend nog de oude periode dekt, terwijl het antwoord een
`window_h` meldt die niet meer bij de instelling past.

## Over `samples`

Die tabel is de grootste van de databank in rijen (214 709 tegen 7 477 pakketten
op de referentieserver) en dat is geen groeiprobleem meer, maar een erfenis.

Sinds de metingen naar VictoriaMetrics gaan, schrijft `db.ingest()` niets meer in
`samples`: die tak wordt overgeslagen zodra `tsdb.enabled()` waar is. Wat er nog
wél in komt is de uitwijk (`db.spill_samples`) wanneer de tijdreeksdatabank een
batch weigert of wegvalt — een vangnet dat per definitie alleen vult als er iets
stuk is.

De tabel valt onder dezelfde opruiming, met de lange bewaartermijn
(`retention_days`, standaard 180 dagen). Ze slinkt dus vanzelf: de bestaande
rijen zijn ouder dan de overstap en verdwijnen naarmate die 180 dagen verstrijken,
en er komt niets structureels voor terug.

Ze heeft met opzet **geen eigen FIFO-bovengrens**: metingen zijn het product van
deze site en pakketten zijn werkmateriaal. Als de bytegrens niet gehaald wordt
terwijl de pakketten al op hun ondergrens staan, dan zegt de beheerpagina dat —
liever een luide waarschuwing dan stilletjes de historiek weggooien waar iedereen
naar kijkt.

## Configuratie

| Variabele | Standaard | Instellingssleutel | Betekenis |
|---|---|---|---|
| `MM_RETENTION_DAYS` | 180 | `retention_days` | Bewaartermijn voor metingen |
| `MM_PACKET_RETENTION_DAYS` | 7 | `packet_retention_days` | Bewaartermijn voor pakketten, en het venster van de heatmap |
| `MM_PACKET_MAX_ROWS` | 200000 | `packet_max_rows` | FIFO-bovengrens op de rijen |
| `MM_DB_MAX_MB` | 512 | `db_max_mb` | FIFO-bovengrens op het bestand, WAL inbegrepen |
| `MM_PRUNE_MINUTES` | 60 | *(geen)* | Minuten tussen twee rondes; gelezen bij het importeren |

De eerste vier zijn alleen de **standaard voor een verse installatie**; de
bewaarde instelling wint.

## Tests

`server/tests/test_retention.py` dekt de drie regels en hun volgorde, de
FIFO-ondergrens, de byteschatting met en zonder `dbstat`, en de VACUUM-drempels.

## Verwante documenten

| Vraag | Document |
|---|---|
| De tabellen die gesnoeid worden | [`database.md`](database.md) |
| Waar de metingen in plaats daarvan heen gingen | [`server.md`](server.md#waar-de-metingen-wonen) |
| Back-ups en schijfbeheer | [`deployment.md`](deployment.md#beheer) |
| De beheerpagina eromheen | [`admin.md`](admin.md) |
