# Architectuur

*[English](../architecture.md)*

MeshManager maakt van het beeld dat een MeshCore-node zelf van het mesh heeft een
publieke statistiekensite. Dit document legt uit wat de onderdelen zijn, hoe data
zich ertussen beweegt, en waarom het transport MQTT is en geen HTTP.

## De onderdelen

| Map | Wat het is |
|---|---|
| `server/` | FastAPI + SQLite. Publieke pagina's, beheer, ingest-API, MQTT-abonnee. |
| `server/tools/` | Buildscripts voor gegenereerde databestanden, bewaard naast wat ze genereren. |
| `firmware/` | Aanpassingen aan de MeshCore-firmware: WiFi met meerdere clients, `MeshManagerNet`, de statistiekenpublicist. |
| `homeassistant/` | Optionele HA-integratie die repeaterdata over HTTP pusht. |
| `proxy/` | Optionele TCP-fan-outproxy voor wie geen aangepaste firmware kan flashen. |
| `mosquitto/` | Brokerconfiguratie voor de Docker-uitrol. |

## Datawegen

Er zijn twee onafhankelijke manieren waarop data de server bereikt, en één
optionele hulp die helemaal geen dataweg is.

### Weg A — node naar MQTT naar site (aanbevolen)

```
  Heltec / ESP32 node                Broker              Server
  +-------------------+          +-----------+       +--------------+
  | mesh + WiFi + BLE |          |           |       | mqtt_ingest  |
  |                   |  MQTT    |           |  sub  |    |         |
  | StatsPublisher    |--------->| Mosquitto |------>|    v         |
  |  meshmanager/<id>/stats         |           |       |  db.ingest   |
  |  meshmanager/<id>/rx (dev)      |           |       |    |         |
  +-------------------+          +-----------+       |    v         |
                                                     |  SQLite      |
                                                     +--------------+
```

De node houdt één MQTT-verbinding open en publiceert elke `interval` seconden
(standaard 300) een JSON-momentopname van zichzelf. Geen Home Assistant, geen
HTTP-client, geen TLS-stack op de node.

De server schrijft zich met een jokerteken in (`meshmanager/+/stats`). Het
topicsegment noemt de node die het bericht **publiceerde**; het JSON-lichaam
noemt de repeater waarover het bericht **gaat**. Meestal is dat dezelfde node die
over zichzelf rapporteert, maar een node kan ook statistieken doorsturen voor
repeaters die hij monitort, dus de publicist wordt naast het onderwerp bewaard in
plaats van er gelijk aan verondersteld. Zie [`mqtt.md`](mqtt.md).

Dezelfde verbinding draagt één bericht de andere kant op. De beheerpagina kan een
node vragen zijn CLI-instellingen nu te lezen, of meteen te publiceren, door één
woord op `meshmanager/<node>/cmd` te zetten. Het antwoord komt terug op het gewone
`stats`-topic, dus dit is een aanleiding en geen tweede dataweg. De firmware
aanvaardt die twee woorden en niets anders — zie
[`mqtt.md`](mqtt.md#iets-vragen-aan-een-node) voor waarom die beperking de
bedoeling is en geen omissie.

### Weg B — een poller naar HTTP naar site (optioneel)

```
  Repeater  --LoRa-->  Companion node  --TCP-->  HA `meshcore`
                                                      |
                                                 entities in HA
                                                      |
                                          meshmanager (Pusher)
                                                      |
                                             HTTPS POST + Bearer
                                                      v
                                              /api/v1/ingest
```

Home Assistant draait bij veel mensen al de `meshcore`-integratie, en die
integratie bezit al sensorentiteiten voor elke repeater die ze hoort. Het
aangepaste onderdeel `meshmanager` schraapt die entiteiten uit de
toestandsmachine, bouwt hetzelfde JSON-lichaam en POSTt het.

Het kan iets wat geen node kan: praten *tegen* repeaters die niet de zijne zijn.
Het geeft `send_statusreq`, `send_telemetry_req`, `send_login` en `send_cmd` uit
via `meshcore.execute_command` en ontleedt de antwoorden, en zo bereiken de
CLI-instellingen van een repeater op fabrieksfirmware `/admin`. Een node die over
MQTT publiceert rapporteert altijd alleen over zichzelf.

Deze weg is optioneel en niet langer de enige manier om dat overzicht te vullen.
Een node met de MeshManager-firmware leest zijn eigen CLI eens per dag en kan
gevraagd worden het nu te doen over het `cmd`-topic hierboven; een repeater die
alleen een poller kan bereiken heeft deze weg nog steeds nodig. `commanding.py`
zoekt per repeater uit welke van de twee beschikbaar is, en de beheerpagina zet de
knop uit en zegt waarom wanneer geen van beide dat is — want een tijdlang deed ze
het omgekeerde: opvragingen in een wachtrij zetten voor een poller die
uitgeschakeld was, en elk ervan als gestart melden.

Beide wegen komen samen bij dezelfde aanroep van `db.ingest()` en leveren
identieke rijen op. Je mag ze allebei tegelijk draaien; wat het laatst binnenkomt
wint.

### Geen dataweg — de TCP-proxy

`proxy/` lost een heel ander probleem op. Fabrieksfirmware van MeshCore aanvaardt
**één** companion-TCP-client. Is Home Assistant verbonden, dan is je telefoon dat
niet. `mc-proxy` houdt de ene verbinding stroomopwaarts vast en waaiert ze uit
naar veel clients.

Hij draagt geen statistieken en praat nooit met de MeshManager-server. Gebruik hem
wanneer je geen aangepaste firmware kunt flashen; kun je dat wel, dan doet de
eigen `SerialWifiInterface` met 4 slots dezelfde klus met betere
antwoordroutering. Zie
[`protocol.md`](protocol.md#23-het-probleem-van-de-enkele-client).

## Waarom MQTT

De eerste implementatie was HTTP. De node bouwde een JSON-lichaam en POSTte dat
naar `/api/v1/ingest` met een Bearer-token. Op de werkbank werkte het; in het veld
crashte het.

De node is een Heltec V3 (ESP32-S3) die tegelijk draait:

- de LoRa-meshstack, met een eigen pakketpool en een timinggevoelige ontvangstlus
- WiFi, dat companionclients over TCP bedient en een beheerpagina op poort 80
- BLE, voor de telefoonapp

Daar `HTTPClient` bovenop leggen was te veel. Twee concrete kosten:

1. **Geheugen.** `WiFiClientSecure` trekt de TLS-stack mee. Certificaten ontleden
   en de TLS-recordbuffers vragen enkele tientallen kB heap, in één uitbarsting
   toegewezen, precies op het ogenblik dat WiFi en BLE ook buffers vasthouden. De
   oorspronkelijke `StatsPublisher::pushNow()` droeg daar een bewaking tegen
   (`if (ESP.getFreeHeap() < 40000) { skip this round; }`), en dat is een omweg,
   geen oplossing — het zet een crash om in stilzwijgend ontbrekende data.
2. **Opzet per meting.** Elke push betekende een verse TCP-verbinding, een verse
   TLS-handshake, request, response, afbraak. Al die heapchurn, om de vijf
   minuten, voor altijd, voor een payload onder een kilobyte.

MQTT keert die kosten om. `PubSubClient` over een gewone `WiFiClient` houdt één
socket open. Publiceren is: de payload bouwen, een korte header en de bytes naar
een reeds bestaande socket schrijven. Er is geen handshake, geen certificaatketen,
geen sessie per bericht. De vaste buffer van de bibliotheek wordt één keer bij het
opstarten bemeten:

```c
_mqtt.setBufferSize(STATS_RX_MAX_LEN * 2 + 128);
_mqtt.setSocketTimeout(4);
_mqtt.setKeepAlive(60);
```

De afwegingen die we aanvaard hebben:

| | HTTP | MQTT |
|---|---|---|
| Heap per bericht | tientallen kB, in uitbarstingen | vaste buffer, één keer toegewezen |
| Opzetkosten | volledige TCP + TLS per push | één verbinding, in leven gehouden |
| Transportbeveiliging | TLS beschikbaar (maar `setInsecure()` werd toch gebruikt) | geen, in deze uitrol |
| Authenticatie | Bearer-token per request | brokergebruikersnaam/wachtwoord bij het verbinden |
| Aflevering | synchrone statuscode | QoS 0, versturen en vergeten |
| Vraagt een broker | nee | ja |

Lees de beveiligingskolom eerlijk: de HTTP-weg kreeg in werkelijkheid geen
geauthenticeerde TLS. Hij riep `secure.setInsecure()` aan, want publieke
statistiekensites zitten vaak achter een tunnel met een certificaat dat de node
niet kan valideren. Het echte verlies van de overstap naar MQTT is dus kleiner dan
het lijkt — we ruilden ongevalideerde TLS voor geen TLS, en kregen er een node
voor terug die blijft draaien. Zie
[`security.md`](security.md#het-transport-tussen-node-en-server).

Het andere gevolg van MQTT is een goed gevolg. Een blijvende verbinding maakt het
goedkoop om *meer* berichten te sturen, en dat is wat het doorsturen van ruwe
pakketten überhaupt haalbaar maakt: de node kan elk frame dat hij hoort
doorspiegelen zonder per frame een sessieopzet te betalen.

## Blokkeren is de andere randvoorwaarde

Heap was niet de enige storingsvorm. Twee opmerkingen in de firmware noteren
dezelfde les vanuit een andere hoek:

- `StatsPublisher.cpp`: de beheerpagina werd vroeger in stukken samengesteld met
  `sendContent()`. Elk stuk is een aparte blokkerende schrijfactie, en met de
  latentiepieken van de modem-sleep van ESP32-WiFi liep de hoofdlus daarin vast —
  en nam het mesh mee. Het is nu één `send_P` van een statische pagina die haar
  gegevens achteraf als JSON ophaalt.
- `MeshManagerNet.h`: de webserver van de repeater is `AsyncWebServer` juist omdat
  "een blokkerende server de hoofdlus ophoudt, en daarmee het mesh — dat gedrag
  hebben we al gezien op de companionnode."

Dezelfde redenering vormt het doorsturen van ruwe pakketten. `MyMesh::logRxRaw()`
draait midden in de ontvangstlus en doet dus niets anders dan kopiëren naar een
ringbuffer; publiceren gebeurt later in `loop()`, een paar pakketten per keer, en
laat bij overloop vallen in plaats van te blokkeren.

**De regel:** alles op de node dat het netwerk raakt moet niet-blokkerend zijn en
een begrensde, vooraf toegewezen geheugenkost hebben. Dataverlies is een
aanvaardbare storing; het mesh laten vastlopen niet.

## Waar de metingen wonen

De metingen staan in **VictoriaMetrics**. Al de rest staat in SQLite.

| | |
|---|---|
| VictoriaMetrics | enkel de metingen: historiek, en de grafieken die eruit getekend worden |
| SQLite | repeaters, `latest`, contacten, buren, pakketten, tokens, beheer |

`latest` blijft met opzet in SQLite. Het voedt de kaartjes op de startpagina, die
snel en zonder netwerk moeten renderen, en "de ene huidige waarde" is niet een
vorm waar een tijdreeksdatabank goed in is.

**Waarom verhuizen.** Nodes gaan van een meting per vijf minuten naar een per tien
seconden. In SQLite betekent dat ruwe punten weggooien om het bestand hanteerbaar
te houden. VictoriaMetrics comprimeert tot ruwweg een byte per punt, dus volledige
resolutie bewaren is daar goedkoper dan hier uitdunnen.

### De naamgeving ligt vast

De bestaande historiek is onder deze namen gemigreerd, en elke afwijking splitst
een reeks stilzwijgend in tweeën:

```
schrijven (influx line protocol, POST /write):
    meshstats,repeater=<slug> <metric>=<value> <nanoseconden>
lezen (PromQL):
    meshstats_<metric>{repeater="<slug>"}
```

Metricnamen komen uit nodes, dus alleen `[A-Za-z0-9_]` overleeft in een veldnaam —
`tsdb.safe_metric()` laat de rest vallen in plaats van te vervangen, net zoals de
migratie deed. De SNR-reeksen per buur (`neighbor_<prefix>`, tientallen ervan)
gaan dezelfde weg als al de rest.

### Schrijven houdt de ingest nooit op

`db.ingest()` geeft zijn numerieke waarden aan een begrensde wachtrij en keert
terug; een achtergrondthread doet de HTTP. Gemeten op 1,5 ms per momentopname van
~100 metrics, tegenover een netwerkretour die van alles kan zijn.

Batchen zit daar bovenop: een node die om de tien seconden publiceert zou anders
een request per node per tien seconden betekenen, elk met een eigen
verbindingsopzet. De schrijver verzamelt tot twee seconden of 2000 punten, wat het
eerst komt. De kost is dat een punt tot twee seconden later bevraagbaar wordt, wat
geen enkele grafiek kan zien.

### Er gaat niets verloren wanneer ze weg is

Er kunnen drie dingen misgaan, en ze eindigen alle drie op dezelfde plek:

| | |
|---|---|
| `MM_TSDB_URL` leeg | punten gaan rechtstreeks naar SQLite `samples` |
| schrijven mislukt (twee keer) | de batch wijkt uit naar `samples` |
| wachtrij vol (20 000 punten) | het punt wijkt uit naar `samples` |

Het lezen spiegelt dat. `tsdb.history()` geeft `None` terug voor alles waar de
beller niets aan kan doen — niet ingesteld, onbereikbaar, een fout antwoord — en
`db.metric_history()` leest dan `samples`. Een metriek die simpelweg geen gegevens
heeft, geeft in de plaats een lege lijst terug, zodat "nog geen historiek" en
"databank niet beschikbaar" onderscheidbaar blijven.

Daarom is **`samples` geen dood gewicht en mag het niet weg.** Het is het vangnet,
en het is wat de overstap omkeerbaar maakt: maak `MM_TSDB_URL` leeg en de site
doet weer wat ze deed, zonder één dag te verliezen.

De kaartjes met de zendtijdbenutting volgen dezelfde weg. Ze worden berekend uit
de eerste en de laatste meting van de teller `airtime` over een venster, en dat
venster moet komen van waar de metingen ook staan — anders had het verhuizen van
de historiek stilzwijgend twee kaartjes leeggemaakt.

### Een stap kiezen

PromQL wil een stap, en een grafiek van 90 dagen op volle resolutie is miljoenen
punten die niemand kan zien. `tsdb.step_for()` kiest uit een vaste ladder die op
~600 punten mikt:

| Bereik | Stap | Punten |
|---|---|---|
| 4 u | 30 s | 480 |
| 24 u | 5 min | 288 |
| 7 d | 30 min | 336 |
| 90 d | 6 u | 360 |

Dat 24 u op 288 punten uitkomt, is een toevalligheid die het waard is te behouden:
precies de dichtheid die de grafieken hadden toen nodes om de vijf minuten
publiceerden, dus de overstap verandert niets aan hoe een grafiek eruit ziet. De
query is `avg_over_time(...[step])` en geen kale selector, zodat elke emmer de
punten erin samenvat in plaats van er willekeurig eentje uit te nemen die het
dichtst bij de grens ligt — over 90 dagen is dat wat een piek voor het verdwijnen
behoedt.

Vaste sporten in plaats van het bereik exact delen, zodat twee grafieken van
hetzelfde bereik het eens zijn over waar hun emmers beginnen.

## Opslagmodel

SQLite, één bestand, WAL-modus, één verbinding voor het hele proces achter een
slot.

Twee vormen van dezelfde data:

- `latest` — één rij per `(repeater, metric)`, de huidige waarde. Waar de kaartjes
  uit renderen.
- `samples` — de tijdreeks, `WITHOUT ROWID`, primaire sleutel
  `(repeater_id, metric, ts)`.

Met een tijdreeksdatabank ingesteld ontvangt `samples` niets behalve tijdens een
storing — de regel hieronder is wat de uitwijkroute bestuurt, en wat de site doet
wanneer ze alleen op SQLite draait.

Een sample wordt alleen geschreven wanneer de waarde veranderd is, of wanneer het
laatst bewaarde punt ouder is dan `heartbeat_min` (standaard 5 minuten). Dat houdt
vlakke metrics zoals `online` ervan af 288 identieke rijen per dag te schrijven, en
garandeert tegelijk dat een grafiek punten heeft.

Historiekleesacties wisselen van strategie op 48 uur: ruwe rijen daaronder,
uurgemiddelden daarboven (`GROUP BY substr(ts,1,13)`). De bewaartermijn staat
standaard op 180 dagen voor samples; burenrijen worden op een vast ingebakken
7 dagen opgeruimd.

### Pakketten

`packets` is de derde vorm en gedraagt zich anders dan de andere twee. Eén rij per
ontvangst, geschreven door de MQTT-`rx`-weg, met de gedecodeerde samenvatting plus
twee velden die de live kaart nodig heeft:

- `path` — de hophashes, komma-gescheiden, gedenormaliseerd uit het frame zodat
  het detailoverzicht en de feed een route kunnen oplossen zonder opnieuw te
  decoderen.
- `raw` — het frame zoals het van de radio kwam, hex. Het enige volledige verslag
  van een pakket; al de rest in de rij is een lossy samenvatting. Het
  detailoverzicht van een pakket decodeert het op verzoek opnieuw in plaats van
  bewaarde advertkolommen te lezen, zodat een verbetering aan de decoder meteen al
  bewaarde pakketten verbetert.

Het frame bewaren verdubbelt een pakketrij ruwweg, en dat is alleen betaalbaar
omdat pakketten hun eigen bewaartermijn dragen: `MM_PACKET_RETENTION_DAYS`,
standaard 7, tegenover 180 voor samples. Beide kolommen zijn via
`COLUMN_MIGRATIONS` toegevoegd, dus een bestaande databank houdt haar rijen en
heeft ze simpelweg leeg — wat de interface als "niet bewaard" meldt in plaats van
als "geen hops".

### Bewaartermijn: één doel, twee beloften

De bewaartermijn woont in de tabel `settings`, met de omgevingsvariabele als
standaard voor uitsluitend een verse installatie. Dat is bewust: hem verhogen is
een beslissing die iemand neemt terwijl hij naar de beheerpagina kijkt, en een
containerherstart nodig hebben om dat toe te passen is hoe een instelling nooit
meer aangeraakt wordt. `routes_api` leest hem per request om dezelfde reden — het
venster van de heatmap *is* de bewaartermijn, dus het ene verhogen verhoogt bij de
volgende ronde het andere, en het venster is deel van de cachesleutel van dat
endpoint zodat een wijziging niet een TTL lang kan blijven hangen.

Een termijn alleen is echter geen garantie. "Bewaar 30 dagen" zegt niets over
hoeveel schijf dat is; één node die elk frame dat hij hoort begint door te
spiegelen, maakt er gigabytes van. Dus past `app/retention.py` drie grenzen toe,
op volgorde:

1. **Leeftijd** — alles voorbij `packet_retention_days` gaat weg. Het doel.
2. **Rijen** — boven `packet_max_rows` gaan de oudste pakketten weg tot het past.
3. **Bytes** — boven `db_max_mb` op schijf gaan er meer van de oudste weg,
   bemeten met een `dbstat`-meting van wat een pakketrij werkelijk kost (met een
   gemeten constante als terugval, want `dbstat` is een compileeroptie).

2 en 3 zijn de belofte, en ze zijn FIFO op `id` en niet op `ts` — `id` is de
invoegvolgorde, en dat is wat "wie het eerst binnenkwam gaat het eerst weg"
betekent, en een node met een kapotte klok zou anders zijn pakketten het eerst
zien verdwijnen omdat ze vorig jaar gedateerd zijn. Telkens wanneer 2 of 3 snijdt,
is de ingestelde termijn *niet* gehaald, en zowel de beheerpagina als de hint van
het archief zelf zegt dat, met het echte getal naast het ingestelde. Een
bewaartermijn die stilzwijgend te weinig levert, is hoe een gat in een grafiek een
avond debuggen wordt.

De ronde draait bij het opstarten **en** elke `MM_PRUNE_MINUTES`, in een eigen
thread, dezelfde vorm als `clocksync.py`. Alleen bij het opstarten snoeien maakte
van de bewaartermijn een handeling in plaats van een regel: een container die
maanden draaide gooide nooit iets weg, en het eerste teken daarvan is een volle
schijf.

SQLite krimpt een bestand niet bij een DELETE — de pagina's komen op een vrije
lijst en worden hergebruikt, wat prima is bij een gestage instroom en ophoudt
prima te zijn zodra iemand een bewaartermijn verlaagt of een bovengrens bijt. Dus
draait dezelfde ronde een `VACUUM` wanneer minstens 16 MB *en* een vijfde van het
bestand vrije lijst is, en alleen wanneer de schijf plaats heeft voor de tijdelijke
tweede kopie die hij bouwt. `auto_vacuum=INCREMENTAL` is afgewezen: dat aanzetten
voor een bestaande databank vraagt precies de volledige `VACUUM` die het moest
vermijden, en belast daarna elke schrijfactie voor altijd om een handeling te
besparen die een handvol keer per jaar seconden duurt.

`samples` valt onder dezelfde ronde, op de veel langere `retention_days`. Het
groeit structureel niet meer — met een tijdreeksdatabank ingesteld schrijft
`db.ingest` de metingen daar rechtstreeks naartoe en zet alleen `db.spill_samples`
nog rijen in SQLite, wat per definitie alleen gebeurt terwijl er iets stuk is. Het
heeft met opzet geen eigen FIFO-bovengrens: metingen zijn het product van deze
site en pakketten zijn werkmateriaal, dus als de bytegrens niet gehaald kan worden
terwijl de pakketten al op hun ondergrens staan, zegt de beheerpagina dat in
plaats van stilzwijgend historiek te verwijderen.

Hopresolutie is een opzoeking van contacten op sleutelprefix, en haar antwoorden
worden een minuut lang gememoïseerd: de live feed lost het pad op van elk pakket
dat hij uitreikt, en die antwoorden veranderen alleen wanneer een nieuwe node zich
adverteert. Wat de opzoeking eerlijk kan concluderen wordt begrensd door het
protocol, niet door de code — zie
[`protocol.md`](protocol.md#wat-een-pad-je-wel-en-niet-kan-vertellen).

### Het land van een node

`contacts.country` bevat een ISO 3166-1 alpha-2-code, of NULL voor "we kunnen het
niet zeggen". Het is waar het landfilter van de live kaart op draait.

**Waar de grenzen vandaan komen.** `server/app/data/borders.json`, gebouwd door
`server/tools/build_borders.py` uit **Natural Earth 1:50m Admin 0 – Countries**
(<https://www.naturalearthdata.com/>, via de GeoJSON-distributie van het project
op [nvkelso/natural-earth-vector](https://github.com/nvkelso/natural-earth-vector)).
Natural Earth is vrijgegeven in het **publieke domein**; naamsvermelding is niet
vereist, en ze staat hier omdat een databestand zonder vermelde herkomst een
risico is. Het buildscript is het reproductierecept — lees de moduledocstring
ervan voordat je iets aan het bestand verandert. Het meegeleverde artefact is
66 kB: West- en Midden-Europa, bijgesneden tot de regio, vereenvoudigd tot 0,004°
en bewaard als delta-gecodeerde gehele getallen.

**Waarom niet 1:110m.** Een kwart van de grootte, en fout precies daar waar dit
mesh leeft: het legt Maastricht in België, Maaseik in Nederland en Aken in België.
Het buildscript draagt referentiepunten door de Maascorridor mee en `--verify`
faalt bij elke misser, dus die fout kan niet onopgemerkt terugkomen.

**Eén keer per node berekend**, wanneer een positie voor het eerst bewaard wordt
of wanneer ze verandert — nooit per pakket. Adverts herhalen een positie die we al
hebben, en die kosten niets. Contacten van vóór de kolom worden bij het opstarten
bijgevuld door `db.classify_countries()`, want een node die nooit verhuist zou
anders nooit geclassificeerd worden.

**Gesleuteld op `prefix6`, toegepast op elke rij die die deelt.** Home Assistant
stuurt vijf sleutelbytes waar de eigen firmware van een node er zes stuurt, dus
één node kan twee contactrijen bezitten onder sleutels van verschillende lengte —
dezelfde val waarvoor `_find_by_prefix()` op de repeaterstabel bestaat. Matchen op
de letterlijke sleutel zou één node twee landen geven, of geen.

**Niets raakt tijdens het draaien het netwerk**, en de functie is optioneel:
ontbreekt `borders.json` of is het onleesbaar, dan is `countries.available()`
False, laat de API zijn landenlijst weg en verschijnt het filter niet. Niets
anders op de pagina merkt het.

NULL is een echt antwoord en geen storing — op zee, buiten het gedekte gebied, of
binnen enkele honderden meters van een kust die de bron grof tekent — en de
interface biedt het als eigen filterkeuze aan in plaats van naar het
dichtstbijzijnde land te raden. Landen worden getoond als een vlag plus de
ISO-code, zodat er geen tweede woordenboek van landnamen vertaald moet blijven.

Onbekende metrics worden nooit geweigerd. Een sleutel waarvoor de server geen
catalogusvermelding heeft, krijgt sectie `other`, label = de sleutel met
onderstrepingstekens vervangen door spaties, en verschijnt op de pagina. Dat is
bewust: firmware kan een metriek toevoegen zonder dat de server verandert.

## Het filter op de live kaart

Eén filter — vrije tekst plus een landkeuze — bestuurt alles wat de startpagina
toont: de pakkettenlijst, de flitsen, de reizende bolletjes, de nodemarkers en de
teller "laatste 5 minuten". Een filter dat maar een deel daarvan bereikt is actief
misleidend, en dat is precies wat een ongefilterde markerlaag onder een gefilterde
lijst bleek te zijn.

Vier beslissingen die het waard zijn te behouden:

**Nodes die niet matchen worden gedimd, niet verborgen.** Verbergen leest netter,
maar het mesh is de bedoeling van deze kaart: een Nederlandse node zegt weinig
zonder de Belgische eromheen, en een route die het filter kruist zou eindigen bij
markers die er niet zijn. Vaag houdt de geografie overeind en laat de matches het
oog leiden, en tooltips blijven aangehecht zodat een schim bij het zweven nog
altijd te identificeren is.

**Het pad van het geopende pakket is vrijgesteld.** Elke node op een getoonde
route wordt op volle sterkte getoond zolang het detailpaneel openstaat, ook hops
die het filter uitsluit. Een gat in een getekend pad betekent al iets precies —
"we kunnen niet zeggen welke node dit was" — en het filter mag dat niet kunnen
nabootsen.

**Het tekstfilter raakt de markers alleen wanneer het een node benoemt.**
Payloadtypes zijn geen eigenschappen van een node, dus een bezoeker die `advert`
typt filtert verkeer, geen geografie; het als geografie behandelen zou elke node
dimmen en melden dat niets matcht. De toets is simpelweg of de tekst überhaupt op
een node past.

**Het beeld volgt alleen wanneer het moet.** Staat er al een matchende node op het
scherm, dan blijft de kaart waar de bezoeker haar zette; is er geen, dan past ze
zich opnieuw aan, want filteren op Groot-Brittannië terwijl je boven België
geparkeerd staat toont anders een lege kaart. Met het detailpaneel open wordt het
beeld nooit verplaatst — het pad daarvan is bewust in beeld gebracht toen het
pakket geopend werd.

Markers worden ter plaatse hergestileerd, nooit herbouwd: elk onthoudt de stijl
die het draagt, zodat een toetsaanslag alleen die raakt die werkelijk veranderden.
De laag per toetsaanslag herbouwen is het ene ding op deze pagina dat op
meshschaal werkelijk traag zou aanvoelen.

## De pakkettenlijst en het detailpaneel

De afzender leidt de lijst, want dat is waar een lezer naar zoekt. Daarna, op een
breed scherm: gehoord door, type, SNR, RSSI, hops, lengte, land, en de tijd
helemaal rechts. Onder 700 px vouwt de rij zich tot twee compacte regels —
afzender en tijd op de eerste, type, SNR, hops en land op de tweede — en vallen
RSSI en lengte weg in plaats van geperst te worden. Ze liggen één tik verderop in
het detailpaneel, en een lijst die op een telefoon zijwaarts schuift is erger dan
een die minder toont. Onder 360 px gaat ook de waarnemersprefix weg.

Vier dingen eraan lezen als bugs als je niet weet waarom:

**"Gehoord door" komt en gaat.** Zolang één node alles doorstuurt, is de kolom op
elke rij dezelfde naam, dus verbergt ze zichzelf. Ze keert terug zodra de pakketten
op het scherm van meer dan één waarnemer komen — wat precies het ogenblik is
waarop ze een van de interessantste kolommen wordt, want dan zegt ze wie wat
hoorde. Geen instelling om te vinden, geen migratie; het lost zichzelf op naarmate
het mesh groeit.

**De waarnemer is een naam op een breed scherm en een sleutelprefix op een
telefoon.** Beide staan in de DOM en CSS kiest er een; één lange nodenaam zou
anders elke rij op een derde regel duwen.

**De afzendercel is `flex: 1 1 0`, niet `auto`.** Een flexcontainer die afbreekt
verdeelt items over regels op hun *onafgekapte* breedte en krimpt ze pas daarna,
dus met `auto` duwt een lange nodenaam de tijdstempel op een eigen regel voordat
er enige ellips toegepast wordt. Vanuit een basis van nul kan ze nooit een
afbreking veroorzaken.

**Geen sortering, met opzet.** De burentabel op een repeaterpagina sorteert, en
hoort dat te doen: dat is een vaste verzameling die je vergelijkt. Dit is een feed
— rijen komen om de paar seconden binnen en verouderen er onderaan uit. Sorteren
op SNR zou het nieuwste pakket overal kunnen zetten, of nergens zichtbaar, en de
volgorde zou bij elke poll onder de lezer omwoelen. Nieuwste eerst is de enige
stabiele volgorde die een live feed heeft; versmallen is waar het filter voor is.

### Het detailpaneel is een lade op een desktop en een blad op een telefoon

Breed: een lade over de volle hoogte naast de kaart, zodat het beeld intact blijft.
Smal: een blad aan de onderkant dat **op kijkhoogte opent** en tijd, afzender,
waarnemer en payloadtype toont, met een greep om het omhoog te slepen voor de
padlijst en de ruwe bytes. Het heropent altijd op kijkhoogte en onthoudt nooit dat
het omhoog stond — je opent zoiets om iets op de kaart te zien, en een blad dat
"helemaal omhoog" onthield zou de kaart elke keer verbergen.

Die kijkhoogte is geen cosmetica. Een blad dat op volle hoogte opende bedekte juist
de route die het uitlegde: het pad was correct getekend en tweederde ervan zat
achter het paneel. De andere helft van die oplossing is `mapPadding()`, dat de
route in beeld brengt binnen het deel van de kaart dat werkelijk zichtbaar is —
Leaflet weet niets van een paneel dat over zijn container ligt, en zonder de
opvulling centreert het het pad in een rechthoek waarvan de helft niet te zien is.
Dezelfde berekening dekt een telefoon in liggende stand, waar de kaart boven een
korte viewport uitloopt.

### Een node benoemen uit één byte

De afzender van een pakket, zijn bestemming en elke hop in zijn pad worden benoemd
met de eerste byte of twee van een publieke sleutel. Eén byte heeft 256 waarden en
een site van dit soort kent enkele honderden nodes, dus dat meer dan één node op
dezelfde waarde antwoordt is het normale geval, geen fout (zie
[`protocol.md`](protocol.md) §1.4). Het paneel somde vroeger elke match op als
"N mogelijk" — eerlijk, maar nodeloos breed: het vergeleek met elke node die ooit
ergens gehoord is, ook nodes op honderden kilometers die alleen ooit na een dozijn
hops aankwamen.

`server/app/candidates.py` versmalt die lijst op bewijs dat de databank al bezit,
in twee stappen en nooit een derde.

**Uitsluiten, alleen waar het frame het draagt.** Het routetype bepaalt welk
uiteinde van een pakket zijn hopaantal begrenst, en dat omdraaien zou de
onschuldigen uitsluiten. Een FLOOD draagt de reeds afgelegde route, dus begrenst
hij waar het pakket *vandaan kwam*: op nul hops gehoord betekent dat de afzender
binnen radiobereik lag, punt. Een DIRECT draagt de nog af te leggen route, dus
begrenst hij waar het pakket *heen gaat*. Geen van beide begrenst het andere — de
bestemming van een geflood pakket mag overal in het mesh liggen, hoe weinig hops
het tot dan ook afgelegd heeft, en daarom houdt het geval waarvoor dit gebouwd is
al zijn vier kandidaten en ordent het ze alleen maar. Waar er wél een grens
bestaat, valt een kandidaat af die verder ligt dan `MAX_RADIO_HOP_KM` per
resterende schakel, en het paneel zegt hoeveel er wegvielen en op welke grond. Een
node die deze waarnemer werkelijk op dat hopaantal gehoord heeft, valt nooit af:
de drempel staat in de plaats van een meting en verliest van een meting.

**Rangschikken, op drie grove signalen in een vaste volgorde.** Op hoe weinig hops
deze waarnemer de node werkelijk gehoord heeft — uit ADVERTs, de ene payload die
zijn afzender voluit benoemt — dan afstand, dan hoe recent hij gezien is. Banden
in plaats van een score, op volgorde vergeleken in plaats van opgeteld: een gewogen
getal zou elk paar kandidaten scheiden, ook de paren die het bewijs niet scheidt,
en niemand zou kunnen navertellen waarom de winnaar won.

**En een weigering.** Wanneer niets de bovenste twee scheidt, is het antwoord nog
steeds "N mogelijk". De lijst is gesorteerd, dus er staat altijd iets vooraan —
maar eerst op alfabet is geen bewijs, en dat afdrukken als "meest waarschijnlijk"
zou een muntworp zijn die zich als conclusie voordoet. Er komen vier toestanden
terug (`known`, `likely`, `ambiguous`, `unknown`) en de frontend leest elk ervan
anders. `likely` staat met opzet aan de *onzekere* kant van de lijn op de kaart:
ringen op elke kandidaat, geen route door de koploper. Een rangschikking is een zin
met haar redenen eraan vast, en een lijn op een kaart draagt geen zin.

| Vraag | Document |
|---|---|
| Wat staat er in een pakket? | [`protocol.md`](protocol.md) |
| Topics, payloads, de broker opzetten | [`mqtt.md`](mqtt.md) |
| Wat er in de firmware veranderd is en waarom | [`firmware.md`](firmware.md) |
| Draaien | [`deployment.md`](deployment.md) |
| Wat beschermd is, en wat niet | [`security.md`](security.md) |
