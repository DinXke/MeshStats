# MQTT

*[English](../mqtt.md)*

MQTT is de aanbevolen manier voor een node om de server te bereiken. Eén
verbinding blijft open; elke meting is een korte publish daarop. Zie
[`architecture.md`](architecture.md) voor waarom dit HTTP verving.

## Topics

De topicstructuur is `<prefix>/<node>/<leaf>`. Beide firmwares bouwen ze
identiek op — `StatsPublisher::topicFor()` op de companion, `mqttTopic()` op de
repeater:

```c
snprintf(out, max, "%s/%s/%s", _cfg.prefix,
         _node_hex[0] ? _node_hex : "node", leaf);
```

| Deel | Waarde |
|---|---|
| `<prefix>` | Ingesteld op de node, standaard `meshmanager` |
| `<node>` | 12 hextekens: de eerste **6 bytes** van de Ed25519-publieke sleutel van de node, in kleine letters |
| `<leaf>` | `stats` of `rx` vanaf de node, `cmd` ernaartoe |

Voorbeeld: `meshmanager/e3d3f4d7ed01/stats`.

Als de node zijn eigen sleutel nog niet heeft kunnen bepalen, valt `<node>` terug
op de letterlijke tekst `node`. Zie je `meshmanager/node/stats`, dan is de publisher
gestart voordat de mesh-identiteit beschikbaar was.

### Twee voorvoegsels tegelijk

De server schrijft zich in op **allebei**: `meshmanager/…` en `meshcore/…`, en
behandelt ze identiek. Dat is geen vriendelijkheid maar wat de hernoeming
overleefbaar maakt. Nodes en server gaan nooit op hetzelfde moment om, en een
node kan maar op één voorvoegsel publiceren, dus de kant die te leren is allebei
te verstaan moet dat doen.

Een opdracht vertrekt op het voorvoegsel waarop die node **zich meldt** —
onthouden bij binnenkomst en vastgelegd in `repeaters.topic_prefix`, zodat het
een herstart van de site overleeft. Een node waar we nog nooit iets van hoorden,
krijgt het op allebei: twee berichtjes van acht bytes zijn goedkoper dan een
knop die niets doet.

`/admin` toont per voorvoegsel hoeveel nodes er binnenkomen. Dat is het getal
dat de vraag "mag de terugval weg?" beantwoordt, en daarom staat het op de
pagina en niet in iemands hoofd. Zie [`migration.md`](migration.md).

### Abonnementen van de server

| Env-variabele | Standaard | Doel |
|---|---|---|
| `MM_MQTT_PREFIX` | `meshmanager` | Het voorvoegsel dat dit project bezit. Er wordt altijd ook naar `meshcore` geluisterd |
| `MM_MQTT_TOPIC` | *(leeg)* | Een **extra** patroon voor periodieke statistieken, bovenop de voorvoegsels hierboven |
| `MM_MQTT_RX_TOPIC` | *(leeg)* | Een extra patroon voor ruwe ontvangen pakketten |

De laatste twee bevatten vroeger het volledige topic en zijn nu standaard
leeg: de voorvoegsels dekken het af. Een waarde die je er wél zet, komt bij
de inschrijvingen bovenop in plaats van ervoor in de plaats — zodat een
installatie die op een gedeelde broker onder een eigen tak draait deze
hernoeming doorkomt in plaats van doof te worden op het moment dat ze
bijwerkt.

Beide worden geabonneerd op **QoS 0**.

Sinds firmware 2.10.0 is er een derde blad, `<prefix>/+/alert`. Dat heeft geen
eigen omgevingsvariabele, anders dan de twee hierboven: die hebben er een omdat ze
bestonden vóór de voorvoegselregel en er installaties zijn met een eigen topic.
Dit topic is nieuw en heeft die geschiedenis niet, en een variabele ervoor zou een
instelling zijn die niemand ooit anders zet.

### Het enige topic waarop de server publiceert

| Env-variabele | Standaard | Doel |
|---|---|---|
| `MM_MQTT_CMD_TOPIC` | `{prefix}/{node}/cmd` | Eén commando voor één node |

`{prefix}` wordt ingevuld met het voorvoegsel waarop **die node** zich meldt.
Een patroon zonder `{prefix}` wordt letterlijk gebruikt: wie een vast topic
opgeeft, bedoelt dat.

`{node}` wordt ingevuld met de eigen pubkey-prefix van die node, zodat een
broker-ACL de *lees*rechten van een node aan dezelfde prefix kan binden als zijn
*schrijf*rechten. Zie [Iets vragen aan een node](#iets-vragen-aan-een-node).

De kloksynchronisatie publiceert op datzelfde topic en heeft eigen instellingen,
omdat de vraag die zij beantwoordt niet "welk topic" is maar "mogen we überhaupt
spreken":

| Env-variabele | Standaard | Doel |
|---|---|---|
| `MM_CLOCKSYNC_ENABLED` | `1` | De tijd al dan niet naar de nodes sturen |
| `MM_CLOCKSYNC_HOURS` | `24` | Uren tussen twee rondes |
| `MM_CLOCKSYNC_MAX_ERROR_S` | `10` | Onzekerheid die de kernel over zijn eigen klok mag hebben voordat we ze niet meer geloven |
| `MM_CLOCKSYNC_MAX_JUMP_S` | `30` | Sprong van de wandklok ten opzichte van de monotone klok die als sprong telt in plaats van als correctie |

Zie [De klok gelijkzetten](#de-klok-gelijkzetten).

### Wie spreekt, en over wie er gesproken wordt

Bij beide topics wordt het `+`-segment geparset. Dat beantwoordt een andere vraag
dan de payload, en de twee worden bewust gescheiden gehouden:

| | Beantwoordt | Komt uit |
|---|---|---|
| **Publisher** | welke node dit bericht verstuurde | het topic, `meshmanager/<node>/…` |
| **Onderwerp** | welke repeater de cijfers beschrijven | `repeater.pubkey_prefix` in de payload |

Meestal is dat dezelfde node die over zichzelf rapporteert. Ze **mogen
verschillen**, want een node stuurt ook statistieken door voor repeaters die ze
monitort — het topic blijft haar eigen topic, terwijl de payload de repeater
noemt die ze doorgeeft. Een verschil blokkeren zou dat kapotmaken.

Dus, voor `stats`:

- Geen `repeater.pubkey_prefix` in de payload → de node heeft het over zichzelf
  en het topic levert het onderwerp.
- Beide aanwezig → de payload bepaalt het onderwerp, en de topicprefix wordt op
  de repeaterrij opgeslagen als `source_prefix` / `source_seen`, getoond in de
  kolom **Bron** op `/admin` (*zichzelf*, *via `<prefix>`*, of *HTTP-API*). Een
  doorgifte wordt ook op INFO gelogd.
- Een topic zonder nodesegment wordt geweigerd.

Voor `rx` is het topic de enige identiteit die er is: een ruw frame draagt niets
in zich over wie het ontving.

Wat dit *niet* oplost: met één gedeeld brokeraccount kan elke client die de
inloggegevens heeft, publiceren onder het topic van eender welke node. Het
vastleggen van de route maakt impersonatie zichtbaar, niet onmogelijk — zie
[accounts per node](#accounts-en-acls-per-node) hieronder en
[`security.md`](security.md).

## Payload: `stats`

Gepubliceerd door `StatsPublisher::publishStats()`, opgebouwd door
`MyMesh::fillStatsJson()`. Hetzelfde schema als de body van de HTTP-`POST
/api/v1/ingest` — dat is bewust, zodat beide paden één handler aan serverzijde
delen.

```json
{
  "repeater": {
    "pubkey_prefix": "e3d3f4d7ed01",
    "name": "BE-HSS-JessaZH.VIR"
  },
  "metrics": {
    "online": true,
    "bat": 4.152,
    "uptime": 3.41205,
    "noise_floor": -108,
    "last_rssi": -94,
    "last_snr": -4.25,
    "airtime": 128.4,
    "rx_airtime": 902.7,
    "nb_recv": 18422,
    "nb_sent": 3310,
    "sent_flood": 2011,
    "sent_direct": 1299,
    "recv_flood": 12004,
    "recv_direct": 6418,
    "recv_errors": 37,
    "tx_queue_len": 0,
    "freq": 869.5250,
    "sf": 8,
    "cr": 8,
    "tx": 22
  }
}
```

### Drie producenten, één schema

Dezelfde vorm wordt op drie plaatsen opgebouwd, en ze sturen **niet** dezelfde
verzameling velden. Weten naar welke je kijkt, is het verschil tussen "deze node
rapporteert geen duplicaten" en "deze node is niet het soort node dat duplicaten
rapporteert".

| Producent | Functie | Onderwerp |
|---|---|---|
| **Companion** | `MyMesh::fillStatsJson()` in `examples/companion_radio` | zichzelf |
| **Repeater** | `MyMesh::fillStatsJson()` toegevoegd door `repeater-hooks.patch` | zichzelf |
| **Monitor die een repeater doorgeeft** | `publishMonitorRound()` in `MeshManagerNet.cpp` | een *andere* repeater |

Alle drie publiceren op `<prefix>/<node>/stats`. De eerste twee beschrijven de
node in het topic; de derde beschrijft iemand anders en zegt dat in
`repeater.pubkey_prefix`.

### Velden die de node verstuurt

| Sleutel | Type | Eenheid | Companion | Repeater | Doorgegeven | Bron |
|---|---|---|---|---|---|---|
| `repeater.pubkey_prefix` | string | — | ✓ | ✓ | ✓ | eerste 6 bytes van de publieke sleutel, hex. Optioneel bij de eerste twee: laat je het weg, dan leest de server het uit het topic |
| `repeater.name` | string | — | ✓ | ✓ | ✓ | `_prefs.node_name`, of de naam die de monitor voor dat item heeft. JSON-escaped sinds 1.9.1 |
| `repeater.fw` | string | — | | ✓ | | `FIRMWARE_VERSION` — de versie van MeshCore |
| `repeater.fw_meshmanager` | string | — | | ✓ | | `MESHMANAGER_VERSION`, leeg wanneer de module niet meegecompileerd is |
| `online` | bool | — | ✓ | ✓ | ✓ | altijd `true`; een levensteken |
| `bat` | float | V | ✓ | ✓ | ✓ | celspanning |
| `battery_percentage` | int | % | | ✓ | ✓ | gedeelde curve, zie `meshmanager_batt_percent()` |
| `ch1_voltage` | float | V | | ✓ | | dezelfde celspanning onder een telemetriekanaalnaam |
| `uptime` | float | **dagen** | ✓ | ✓ | ✓ | 5 decimalen |
| `noise_floor` | int | dBm | ✓ | ✓ | ✓ | weggelaten tenzij negatief |
| `last_rssi` | int | dBm | ✓ | ✓ | ✓ | weggelaten tenzij negatief |
| `last_snr` | float | dB | ✓ | ✓ | ✓ | weggelaten wanneer de node niets ontvangen heeft |
| `airtime` / `rx_airtime` | float | **minuten** | ✓ | ✓ | ✓ | TX en RX |
| `nb_recv` / `nb_sent` | int | aantal | ✓ | ✓ | ✓ | pakkettellers op radioniveau |
| `sent_flood` / `sent_direct` | int | aantal | ✓ | ✓ | ✓ | `Dispatcher`-tellers |
| `recv_flood` / `recv_direct` | int | aantal | ✓ | ✓ | ✓ | `Dispatcher`-tellers |
| `recv_errors` | int | aantal | ✓ | ✓ | ✓* | `getPacketsRecvErrors()` |
| `direct_dups` / `flood_dups` | int | aantal | | ✓ | ✓* | duplicaten onderdrukt door de dedup-tabel |
| `err_events` | int | aantal | | ✓ | ✓* | `_err_flags` |
| `tx_queue_len` | int | aantal | ✓ | ✓ | ✓ | `getOutboundTotal()` |
| `neighbor_count` | int | aantal | | ✓ | ✓ | hoeveel buren de node kent, wat niet hetzelfde is als hoeveel ze er gerapporteerd heeft |
| `mcu_temperature` | float | °C | | ✓ | | ESP32-dietemperatuur |
| `ch<N>_temperature` / `ch<N>_voltage` | float | °C / V | | | ✓ | gedecodeerd uit de CayenneLPP-telemetrie van de gemonitorde node |
| `ch<N>_switch` | int | 0/1 | | | ✓ | `LPP_SWITCH` op kanaal N — zo meldt een sensornode een dienst op of neer |
| `ch<N>_generic` | int | — | | | ✓ | `LPP_GENERIC_SENSOR` op kanaal N: een heel getal zonder eigen eenheid. Een uptimemonitor zet er een responstijd in ms in, maar het type zegt dat niet — zie de noot onder de tabel |
| `freq` | float | MHz | ✓ | ✓ | | `_prefs.freq` |
| `sf` / `cr` | int | — | ✓ | ✓ | | spreading factor, coding rate |
| `tx` | int | dBm | ✓ | ✓ | | `_prefs.tx_power_dbm` |
| `neighbors` | array | — | | ✓ | ✓ | zie hieronder |
| `via` | string | — | | | ✓ | 12 hextekens: de node die dit doorgaf. **Wordt momenteel genegeerd door de server** — zie hieronder |

`✓*` in de kolom "Doorgegeven" betekent "alleen wanneer de firmware van de
gemonitorde node nieuw genoeg is om die bytes verstuurd te hebben" — zie de
regel over de structlengte hieronder.

#### Kanaalmetingen: het type zit in de naam, de betekenis niet

De vier `ch<N>_*`-velden dragen een kanaalnummer `N` dat de *antwoordende* node
gekozen heeft, en een achtervoegsel dat het **LPP-type** noemt — nooit wat de
waarde betekent.

Twee ervan op hetzelfde kanaal is normaal en geen vergissing: een sensornode meldt
één dienst tegelijk als `ch5_switch` (bereikbaar ja/nee) en `ch5_generic`
(responstijd). Daarom zit het type in de veldnaam; een naam uit alleen het kanaal
zou de tweede de eerste laten overschrijven.

`ch6_generic` heet met opzet niet `ch6_ping_ms`. `LPP_GENERIC_SENSOR` garandeert
vier unsigned byte met vermenigvuldiger 1 en zegt niets over wát er geteld wordt,
dus een eenheid in de naam zou een verzinsel zijn. De koppeling kanaal → dienst —
"kanaal 6 is google, in ms" — wordt per node op de server bewaard, in
`channel_names`, en op de beheerpagina gezet. Zie
[`protocol.md`](protocol.md) en [`admin.md`](admin.md).

**Kanaalnummers mogen nooit verschuiven.** De bewaarde naam hangt aan het nummer,
want dat is het enige wat het pakket draagt. Laat de antwoordende kant een dienst
vallen en schuift de rest op, dan wijst elke bewaarde naam stil naar de verkeerde
dienst — geen foutmelding, alleen verkeerde cijfers. Een gat in de nummering is dus
geen rommel om op te ruimen; het is het bewijs dat er niets verschoven is.

Let op de eenheden. `uptime` staat in **dagen**, niet in seconden; `airtime` in
**minuten**, niet in milliseconden. Beide worden op de node al gedeeld, zodat de
server opslaat wat ze toont.

#### Waarom een veld kan ontbreken, en waarom dat bewust is

**Een metriek die niet beschikbaar is, wordt weggelaten, nooit als `0`
verstuurd.** JessaZH rapporteerde `noise_floor 0`, wat een lijn naar nul deed
duiken op een grafiek waar een gat hoorde — en iemand een namiddag kostte om uit
te zoeken welke. De tests gaan over fysica, niet over netheid:

- een ruisvloer of een RSSI in dBm is altijd negatief; `0` betekent dat de
  radiodriver ze nooit heeft ingevuld;
- een SNR van 0,0 dB is een volstrekt reële meting, dus die wordt alleen
  onderdrukt wanneer de node helemaal niets ontvangen heeft om een SNR van te
  hebben;
- een board dat geen celspanning rapporteert, krijgt noch `bat` noch
  `battery_percentage`.

**Tellers worden nooit gefilterd.** Nul verstuurde pakketten is een feit, geen
gat.

Bij een *doorgegeven* meting is er een tweede reden waarom een veld kan
ontbreken, en die is de moeite waard om te begrijpen omdat ze er van buitenaf
identiek uitziet. `RepeaterStats` groeide doorheen de MeshCore-releases, en een
oudere node antwoordt met een kortere struct. De monitor controleert daarom
hoeveel bytes er werkelijk aankwamen voor hij elk veld uitstuurt (`ST_HAS()` in
`publishMonitorRound()`). Een ontbrekende `flood_dups` op een doorgegeven
repeater betekent dus ofwel "die firmware heeft dat niet" ofwel "het werd nooit
ingevuld" — en beide zijn beter dan een zelfverzekerde nul.

#### `mcu_temperature` is niet `ch1_temperature`

Het zijn niet dezelfde metingen en ze mogen nooit opnieuw samengevoegd worden.
`mcu_temperature` is de ESP32-die, die met WiFi actief 20 à 30 graden boven zijn
omgeving zit — een node rapporteerde 51 °C terwijl het buiten ongeveer 25 °C
was. Onder de naam `ch1_temperature` neemt een lezer dat voor een
omgevingsmeting en concludeert hij dat het dak in brand staat.
`ch1_temperature` blijft voorbehouden aan een echte sensor.

Bij een doorgegeven meting zijn de kanaalnummers die van de overkant, niet die
van ons. Op een MeshCore-repeater is kanaal 1 zijn eigen board, dus
`ch1_temperature` is daar opnieuw de die — maar dat is een naamgeving die de
overkant moet maken, en ze hier herinterpreteren zou data verzinnen zijn. Zie
[`protocol.md`](protocol.md).

#### `neighbors`

```json
"neighbors": [
  {"prefix": "a1b2c3", "snr": -7.25, "seen_min": 12}
]
```

Elk item wordt een eigen tijdreeks onder de metrieksleutel `neighbor_<prefix>`.
Een doorgegeven buuritem draagt **geen naamveld**: het antwoord van de
gemonitorde repeater bevat enkel sleutel, ouderdom en SNR, en de naam weglaten
betekent dat de server behoudt welke naam hij al had in plaats van die te
overschrijven met een lege.

`neighbor_count` en de lengte van `neighbors` mogen verschillen. De teller is
wat de node weet; de array is wat in het bericht paste. De array kapt af in
plaats van te falen — een gedeeltelijke burenlijst is nuttig, een weggevallen
statsbericht niet.

#### `via`, en waarom de server het niet leest

Beide doorgegeven berichttypes — `publishMonitorRound()` en
`publishMonitorSettings()` — hangen er een `"via"` op het hoogste niveau aan met
de eigen id van 12 hextekens van de doorgevende node. Dat is redundant met het
topic, dat de publisher al benoemt, en **`mqtt_ingest.py` leest het niet**: de
server leidt de doorgifte af uit het `+`-segment en legt die vast als
`source_prefix`.

Dat is aan geen van beide kanten een bug. Het topic is de autoriteit voor "wie
sprak", omdat het het enige is waaraan een broker-ACL per node zich werkelijk
kan binden. Een payloadveld dat een doorgifte claimt, zou een claim zijn, geen
feit. `via` staat er voor wie de ruwe stroom leest — een sniffer, een tweede
abonnee, een log — waar het topic mogelijk al weggevallen is.

Bouw geen serverfunctie op `via` zonder eerst te beslissen wat er gebeurt
wanneer het niet overeenkomt met het topic.

#### Berichtgrootte

De repeater zet de buffer van `PubSubClient` bij het opstarten op `MQTT_PUB_MAX`
= **5120 bytes**, omdat de burenarray lang kan zijn en een ruw pakket meer
dan 500 hextekens telt. De standaardwaarde van 256 bytes zou `publish()` deze
berichten **stilzwijgend doen weigeren** — succes aan deze kant, niets bij de
broker. Alles wat langer is dan de buffer wordt bij de bron afgekapt (minder
buren) in plaats van hier geweigerd te worden.

### Velden die de server ook aanvaardt

Het HTTP-ingestpad en de Home Assistant-pusher sturen meer. Dat alles is ook
geldig over MQTT, aangezien beide door dezelfde handler gaan:

| Sleutel | Betekenis |
|---|---|
| `ts` | ISO-tijdstempel `YYYY-MM-DDTHH:MM:SSZ`; standaard het ontvangstmoment op de server |
| `force` | bool; de heartbeat-dedup omzeilen en altijd een meetpunt wegschrijven |
| `neighbors` | array van `{prefix, name, snr, seen_min}` |
| `settings` | object met CLI-parameters; zie hieronder |
| `filter` | de stand en de weggooitellers van het pakketfilter; zie hieronder |

Elke buur wordt ook een eigen tijdreeks onder de metrieksleutel
`neighbor_<prefix>`.

### `filter` — het pakketfilter

Reist mee met **elk** statistiekenbericht, niet met de dagelijkse ronde:

```json
"filter": {
  "on": true, "disarmed": false, "hash": 1, "malformed": true,
  "channels": 1, "blocked_types": 0, "passed": 91422, "exempt": 12,
  "drop": {"type": 0, "hops": 41, "rate": 308, "hash": 0,
           "kanaal": 77, "misvormd": 4}
}
```

Ongeveer 160 byte, en die frequentie is het punt. Een filter maakt een node
nutteloos zonder hem onbereikbaar te maken — hij antwoordt nog, adverteert nog,
staat nog groen — dus een stand die maar eens per dag reisde, zou een dag te laat
zijn. De regeltabellen (twaalf hoplimieten, twaalf snelheidslimieten, de
kanalenlijst) zitten er **niet** in: twee kilobyte die eens per maand verandert
hoort achter een verzoek van iemand die ze gaat wijzigen, en dat is
`GET /api/filter` op de node.

De tellers gaan door de gewone metricmolen als `filter_dropped`, `filter_passed`,
`filter_exempt`, `filter_on` en `filter_drop_<reden>`, zodat ze net als al het
andere in grafieken komen en verouderen.

De server neemt dit object **alleen aan als het bericht over de afzender zelf
gaat**. Een node mag legitiem cijfers doorgeven over een repeater die hij
monitort, maar niet diens filterstand: een gemonitorde repeater vertelt zijn
filter nergens over de radio, dus een blok dat dat beweert kan niet kloppen.
Geweigerd, en opgeschreven.

Zie [`packet-filter.md`](packet-filter.md).

### `settings` — de eigen CLI-configuratie van de node

Eén keer per dag door de node uitgelezen en meegestuurd met een gewoon
statistiekbericht:

```json
"settings": {
  "name": "BE-HSS-JessaZH.VIR", "role": "repeater",
  "radio": "868.0,250,10,8", "freq": "869.525", "tx": "22", "af": "1",
  "repeat": "on", "advert.interval": "240",
  "flood.advert.interval": "1440", "flood.max": "3", "flood.max.unscoped": "5",
  "allow.read.only": "off", "rxdelay": "0", "txdelay": "0",
  "lat": "50.92", "lon": "5.352", "region.home": "be", "region.default": "be",
  "cmd:region": "*\n eu F\n  bx F\n   be^ F\n    be-vbr F"
}
```

Negentien sleutels, één per item in `SET_PARAMS` in `MeshManagerNet.cpp`. Achttien
daarvan zijn waarden van één regel; `cmd:region` is een boom en is de reden dat
`\n` de JSON-escaping überhaupt overleeft — zie hieronder en
[`firmware.md`](firmware.md).

De sleutels zijn wat de sweep-tabel in de firmware ze noemt — de server slaat
elke sleutel die hij ontvangt op en toont ze, bekend of niet. De parameterlijst
op de instellingenpagina van de admin stuurt enkel de opzoeking via Home
Assistant aan; een parameter die daar wordt toegevoegd, bereikt **geen** nodes
die over MQTT publiceren totdat de eigen tabel van de firmware (`SET_PARAMS` in
`MeshManagerNet.cpp`) er ook om vraagt.

Het vult dezelfde adminpagina als `POST /api/v1/repeater_settings`, zodat een
node ze kan vullen zonder dat er Home Assistant aan te pas komt.

**Beide paden matchen de sleutel op dezelfde manier.** Een repeater die over
MQTT rapporteert *en* via Home Assistant gemonitord wordt, komt binnen onder
twee schrijfwijzen van één sleutel — zes sleutelbytes uit zijn eigen firmware,
vijf uit Home Assistant — en de opgeslagen sleutel groeit mee tot de langste die
gezien is. Beide instellingspaden vinden de repeater daarom via
`find_repeater()`. Het HTTP-endpoint vergeleek strings tot het daarop betrapt
werd: een repeater die over MQTT was opgepikt, begon dan stilzwijgend 404 te
antwoorden aan Home Assistant, waarmee een opzoeking van één à twee minuten
LoRa-zendtijd werd weggegooid — en de adminpagina bleef de laatste sweep tonen
die wél was aangekomen, zonder ook maar iets dat verried dat er sindsdien iets
mislukt was.

### `cfgspec` — de beschrijfbare parametertabel van de node

Reist mee met de instellingenronde hierboven, en met niets anders: deze tabel
verandert alleen als er andere firmware op de node gaat, dus hem bij elk bericht
betalen zou maandelijks betalen zijn voor iets dat jaarlijks verandert.

```json
"cfgspec": {
  "name": "text,0,0,1,0,0",
  "flood.max": "int,0,64,2,0,0",
  "loop.detect": "enum,0,0,2,0,0,off|minimal|moderate|strict",
  "tx": "int,0,30,3,0,0"
}
```

Eén string per parameter, in een vaste volgorde:
`<soort>,<lo>,<hi>,<risico>,<herstart>,<geheim>[,<keuzes>]`. Compact met opzet —
zevenentwintig objecten met zeven sleutels elk zijn twee kilobyte tegen
negenhonderd byte op deze manier, en het reist in een bericht met een vaste
buffer.

**Waarom dit er is.** De server bouwt zijn schrijfformulier uit de lijst van de
node zelf en houdt met opzet geen eigen parametertabel; tot 2.8.0 kwam die lijst
alleen van `GET /api/cfg`, en dat is precies wat een node waarvoor de server geen
weblogin heeft niet kan beantwoorden. Zonder `cfgspec` zou de MQTT-schrijfweg
bestaan en onbruikbaar zijn: de site zou de risicoklasse van een parameter niet
kennen, bij twijfel de zwaarste aannemen (`nodeconfig.risk_of`), en dus alles
blokkeren.

Het blijft de lijst van de node en geen tabel die hier verzonnen is — alleen een
tweede *bron* voor diezelfde lijst. Hij wordt alleen aangenomen als het bericht
over de publisher zelf gaat: een parametertabel is de ingecompileerde lijst van
de publicerende firmware, dus een blok dat beweert die van een ander te dragen
kan niet waar zijn. Dat weegt zwaarder dan het klinkt, want de site hangt haar
bevestigingen en haar rechten aan die risicoklassen op.

### `cfgset` — de uitslag van de laatste schrijfactie over `cmd`

Reist mee met elk statistiekbericht zodra er ooit een geweest is, want het is een
paar honderd byte en een gemiste publicatie hoort hem niet te verliezen:

```json
"cfgset": {
  "seq": 3, "ok": 1, "param": "flood.max", "asked": "12",
  "applied": "12", "exact": 1, "reboot": 0, "msg": ""
}
```

`applied` is wat de node achteraf terugleest en niet wat er gevraagd is —
dezelfde discipline als bij `POST /api/cfg`, en om dezelfde gemeten redenen
(`set lat abc` is een kale `atof()`, `advert.interval 61` legt 60 vast). `exact`
zegt of die twee gelijk zijn. `seq` telt schrijfacties sinds de start, zodat de
server deze uitslag van de vorige kan onderscheiden.

Een **weigering wordt hier ook gemeld**, met `ok: 0` en de reden in `msg`. Stilte
zou niet te onderscheiden zijn van een node die slaapt op zijn zonnebudget, en
dan lijkt een tikfout precies op een lege batterij. Dezelfde publisherregel als
bij `cfgspec`.

**Waarom het geen eigen topic heeft.** Het zou `meshmanager/<node>/settings`
worden, tot een controle van `mqtt_ingest.py` uitwees dat deze abonnee naar
precies twee patronen luistert. Een derde topic zou door de broker aanvaard zijn
en daarna ongelezen zijn weggevallen — dezelfde fout die de gemonitorde
repeaters eerder al eens deed verdwijnen. Een topic toevoegen betekent het
toevoegen aan `MM_MQTT_*` **én** aan de subscribe-oproepen in `on_connect`;
zolang niet beide gebeuren, gaan de berichten nergens heen. Die regel gaat over
*publiceren*, en zegt niets over de richting waarin het `cmd`-topic hieronder
loopt.

## Iets vragen aan een node

De dagelijkse sweep beantwoordt één keer per dag de vraag "hoe is deze node
geconfigureerd". De adminpagina moet die *nu* beantwoorden, en deed dat vroeger
door in een wachtrij te schrijven die alleen de Home Assistant-integratie ooit
leegmaakte. Haal Home Assistant uit de keten — precies waar een node die
rechtstreeks naar MQTT publiceert voor dient — en de knop schreef in een
wachtrij die niemand las, terwijl de pagina een opzoeking beloofde die al gestart
zou zijn.

Daarom publiceert de server één woord op `meshmanager/<node>/cmd`:

| Woord | De node doet |
|---|---|
| `settings` | leest nu zijn CLI-parameters, en publiceert ze met het statistiekbericht dat hij verstuurt zodra de sweep klaar is |
| `settings <key>` | logt in op een repeater die hij *monitort*, leest de CLI-parameters van **die** repeater over LoRa, en publiceert ze onder de naam van die repeater (nodefirmware 1.9.0) |
| `status` | publiceert onmiddellijk een statistiekbericht |
| `time <epoch>` | zet zijn eigen klok op die UNIX-tijd in UTC-seconden, en controleert daarna over LoRa de klokken van de repeaters die hij monitort (nodefirmware 1.10.0) |
| `set <param> <waarde>` | zet een van **zijn eigen** CLI-parameters, leest hem terug, en meldt de uitslag met het statistiekbericht dat hij er meteen na publiceert (nodefirmware 2.8.0) |

Het antwoord komt terug op het gewone `stats`-topic. Verder verandert er niets
aan het ingestpad, en een ontvanger die niets van `cmd` afweet, blijft werken.
`time` is de uitzondering: dat levert helemaal geen bericht op, alleen een
verandering op de node. Wat er gebeurd is, valt af te lezen met `wifi clock` op
de node en op de adminpagina van de site.

**De firmware aanvaardt die vier woorden en niets anders.** Geen prefixtest,
geen doorval naar `handleCommand()` — een exacte match tegen een lijst van vier.
De telnetconsole op de node geeft haar invoer wél door aan de CLI, maar die
console vraagt een wachtwoord over een verbinding die de operator beheert,
terwijl dit topic bereikbaar is voor iedereen met brokergegevens. Deze repeaters
hangen op daken en draaien op zonnepanelen; één `reboot` in een lus volstaat om
er een te verliezen. De eerste twee woorden doen de node enkel zeggen wat hij
uit zichzelf ook gezegd zou hebben, dus het ergste dat een aanvaller op de
broker ermee bereikt, is een statistiekbericht, hoogstens één per 30 seconden
(`MQTT_CMD_MIN_GAP_MS`).

De argumenten verruimen dat niet, en ze zijn het waard om te scheiden omdat het
verschillende soorten dingen zijn.

Dat bij `settings` wordt nooit tekst die een CLI bereikt: het selecteert één item
uit de monitorlijst van de node, en de commando's die dan verstuurd worden, zijn
de ingecompileerde parametertabel. Die lijst is enkel beschrijfbaar vanaf de
adminpagina en de mesh-CLI, beide met wachtwoord beveiligd, dus het meeste wat
een brokeraccount ermee kan, is een repeater uitlezen die de operator al gekozen
had om te monitoren — hoogstens één keer per tien minuten.

Dat bij `time` is een getal, gecontroleerd tegen een venster van jaren aan beide
kanten (2025–2100, in `mqtt_ingest.py` en nogmaals in `MeshManagerNet.cpp`) en
toegepast door code die een klok alleen ooit **vooruit** zet. Dit woord verleent
dus wel een echte capaciteit die de andere twee niet geven: het verandert
toestand. Onomwonden benoemd, omdat het het woord is dat een ACL verdient: een
aanvaller op de broker kan de klok van een node op om het even welk tijdstip
tussen nu en 2100 zetten, en dat valt niet meer over de lucht terug te draaien.
De reden staat in de volgende sectie.

Dat bij `set` is het woord dat het plafond van dit topic werkelijk verhoogt, en
het is de moeite om precies te zijn over hoever. Het is een **grotere whitelist,
geen doorgang**, en de node doet het keuren:

- de parameter moet een van de achtentwintig namen zijn die in `CFG_PARAMS`
  ingecompileerd staan. Het commando wordt daarna opgebouwd met de sleutel *uit
  die tabel*, dus er wordt nooit tekst uit het bericht een commando — alleen de
  waarde reist mee, en die is altijd het laatste woord, dus er is geen scheider
  waar een tweede commando na kan beginnen;
- de waarde moet door `cfgCheckValue()`, dezelfde zeef die `POST /api/cfg` en
  `POST /api/moncfg` gebruiken. Eén zeef, niet drie die uit elkaar kunnen lopen;
- de risicoklasse mag `CFG_MQTT_MAX_RISK` niet overschrijden, en die staat op
  *verandert merkbaar hoe de node zich gedraagt* en niet op *kan deze node
  afsnijden*. Radio-instellingen staan sinds 2.6.0 helemaal niet meer op die
  tabel, dus deze weg kan ze net zomin aanbieden als de andere twee.

Een node die een onbekende parameter of een waarde buiten de grenzen krijgt,
weigert hem, telt hem, en **zegt het** — zie `cfgset` hierboven. Stilte zou niet
te onderscheiden zijn van een node die slaapt op zijn zonnebudget, en dat is het
enige wat dit antwoord niet mag zijn.

Waarom het plafond hier lager ligt dan op de twee HTTP-schrijfwegen komt neer op
wie er aan de overkant staat: die hebben een geauthenticeerde tegenpartij, deze
heeft wie de broker heeft binnengelaten. Op een broker met één gedeeld account is
dat elke node die ermee praat. De volledige afweging, en wat het voor je
brokerinrichting betekent, staat in
[`security.md`](security.md#een-instelling-wijzigen-drie-vervoermiddelen).

### Het formaat van een `cmd`-payload

De payload is het kale woord, eventueel gevolgd door één argument, als platte
tekst. Geen JSON, geen envelope, geen structuur erachter.

| Regel | Waarde | Gevolg |
|---|---|---|
| Maximale lengte | 96 bytes (`MQTT_CMD_MAX`) | Langer dan het langste geldige commando, zodat een payload die er niet in past herkenbaar *te lang* is in plaats van afgekapt tot iets dat toevallig matcht. Te lange payloads worden geweigerd en geteld |
| Spaties vooraan/achteraan | weggeknipt | Een publisher die er een newline aan plakt, wordt daar niet voor gestraft |
| Argumentscheiding | één spatie of tab | `settings a1b2c3d4`, `time 1786665600` |
| Ariteit | per woord gecontroleerd | `status <wat dan ook>` wordt **geweigerd**, niet uitgevoerd als `status`. Een publisher die een argument stuurt aan een commando dat er geen neemt, heeft iets verkeerd begrepen, en het toch uitvoeren verbergt dat voor beide kanten |
| Minimale tussentijd | 30 s (`MQTT_CMD_MIN_GAP_MS`) | Commando's die binnen die tussentijd toekomen worden **verworpen, niet in de wachtrij gezet** — "doe het nu" verliest zijn betekenis als het moet wachten |
| Retain | moet `false` zijn | Zie hieronder |
| QoS | 0 | Zie hieronder |
| Gelijktijdigheid | één woord tegelijk | Staat er al een woord te wachten op verwerking, dan wordt het volgende verworpen |

Voorbeelden, precies zoals ze over de lijn gaan:

```
settings
status
settings e3d3f4d7ed01
time 1786665600
set flood.max 12
set name Dak Noord
```

`set` is het enige woord met twee argumenten: de parameternaam, dan de waarde.
De waarde is alles na de parameter, spaties inbegrepen — `name` en `owner.info`
bestaan uit weinig anders.

`time` neemt UNIX-epoch in **seconden in UTC**, geparset met `strtoul` (niet
`atol` — de epoch passeert 2³¹ in 2038 en deze nodes hangen dan misschien nog
altijd op hun dak). Een niet-cijfer aan het einde betekent dat het argument geen
kaal getal was, en een getal met iets eraan geplakt is een fout stroomopwaarts,
geen tijd: het wordt geweigerd en geteld.

### Waarom een klok alleen vooruit gaat

Dit bepaalt de hele functie en het is geen MeshCore-eigenaardigheid om omheen te
werken.

Een advert draagt de klok van de node die hem uitzond, en elke node die de
zender al kent **verwerpt een advert waarvan het tijdstempel niet toegenomen
is** (`onAdvertRecv` in `MyMesh.cpp`, de test `timestamp > client->last_timestamp`).
Zet de klok van een repeater een uur terug en hij is een uur lang onzichtbaar
voor iedereen die hem kent — een onderhoudscommando dat een dakrepeater van de
mesh haalt. MeshCore's eigen `time` en `clock sync` weigeren achteruit te gaan;
deze firmware weigert het om precies die reden, en de server ook.

Daar volgen twee gevolgen uit, en beide zijn zichtbaar in plaats van verborgen:

- Een node die te **snel** blijkt te lopen, wordt gerapporteerd en verder met
  rust gelaten. Er is geen manier om dat over de lucht te corrigeren; enkel
  `clkreboot` op die node helpt, en dat herstart hem.
- Een per vergissing gepubliceerde tijd valt niet ongedaan te maken. Vandaar
  `clocksync.py`, dat helemaal weigert te publiceren tenzij de eigen klok van
  deze machine als betrouwbaar kan worden vastgesteld — zie
  [De klok gelijkzetten](#de-klok-gelijkzetten) hieronder.

### De klok gelijkzetten

Een MeshCore-node krijgt zijn klok nooit uit zichzelf juist. Een ESP32 zonder
batterijgevoede RTC start op wat de firmware meedraagt — `clkreboot` zet ze
letterlijk op 15 mei 2024 — en drift van daaruit weg. Een dakrepeater herstart
uit zichzelf: lege batterij, watchdog, een stroomonderbreking in het
onweersseizoen. Telkens komt hij terug en stempelt hij alles wat hij zegt met een
datum die niets met vandaag te maken heeft, en niets op de mesh corrigeert dat,
omdat niets op de mesh het zelf beter weet.

De server wel, dus die publiceert `time <epoch>` volgens een schema
(`MM_CLOCKSYNC_HOURS`, standaard 24). Het formaat is niet gekozen: het is wat
`CommonCLI::handleCommand` in zijn `time `-tak parset — `_atoi` van de rest van
de regel, rechtstreeks naar `setCurrentTime`, UNIX-seconden in UTC.

Alleen nodes die **hier rechtstreeks publiceren** worden door het schema
aangesproken. Een doorgegeven repeater krijgt zijn tijd van zijn monitor over
LoRa, wat precies is wat het commando die monitor doet doen, en het is de enige
weg ernaartoe. Het schema loopt bewust niet de doorgegeven repeaters af: twee
daarvan achter één monitor zouden diezelfde monitor tweemaal hetzelfde bericht
sturen, en die zou betalen voor twee klokrondes waar er om één gevraagd was.

### De knop

De adminpagina van elke repeater heeft **"Klok nu synchroniseren"**, voor wanneer
een dag wachten geen optie is — een node die net terug is van een
stroomonderbreking stempelt alles wat hij zegt met een datum uit 2024 tot de
volgende ronde.

Het is geen tweede weg naar de broker. De knop roept dezelfde `sync_now` in
`clocksync.py` aan als waar de scheduler doorheen gaat, dus de klokbewaking
hieronder, het epoch-venster en de firmwareversiecontrole gelden onverkort. De
driftdrempel en de weigering bij een node die te snel loopt, worden evenmin
opnieuw geïmplementeerd: die zitten in de firmware, en dit is hetzelfde bericht.

Twee dingen zijn het waard om te weten voor je erop drukt:

- **Op een doorgegeven repeater richt hij zich niet op die repeater alleen.** Het
  bericht gaat naar de node die hem monitort, en die node controleert de klokken
  van *elke* repeater die hij monitort. Er is geen argument om dat te versmallen,
  en dat is geen nalatigheid — een klokronde kost één commando en één antwoord
  per gemonitorde repeater, dus de hele lijst aflopen is ongeveer een vijfde van
  één gewone pollronde. De pagina zegt dat ook, in plaats van te suggereren dat
  de knop gericht is.
- **Hij weigert binnen het uur**, en meldt wanneer de volgende mogelijk is. Niet
  uit veiligheid: `MON_CLK_MIN_GAP_MS` in de firmware maakt het al onmogelijk om
  de band te bezetten door te klikken, hoe vaak je ook klikt. Uit eerlijkheid —
  binnen het uur zou de node enkel zijn eigen klok zetten, wat het vorige bericht
  al deed, en de ronde overslaan die er wél toe doet, terwijl de pagina
  "verzonden" zei. De ene uitzondering is een node die sindsdien herstart is,
  gedetecteerd via zijn `uptime`-metriek, want dat is precies het moment waarop
  wachten het slechtst denkbare antwoord is.

Wat de site niet kan tonen, is de gemeten drift. De node meet hoever elke
gemonitorde repeater afwijkt, en corrigeert enkel voorbij twee minuten, maar hij
publiceert dat nergens; het is af te lezen met `wifi clock` op de node zelf. Dat
op deze pagina krijgen zou betekenen dat het in het statsbericht moet — een
firmwarewijziging. De knop zelf heeft er geen nodig bovenop de 1.10.0 die `time`
sowieso al vereist.

Voordat er iets vertrekt, moet `clocksync.py` vaststellen dat de eigen klok van
deze machine betrouwbaar is, en het weigert luidruchtig — logs en adminpagina —
wanneer dat niet lukt:

- **Klokdiscipline van de kernel** via `adjtimex(2)`: de vlag `STA_UNSYNC` en de
  eigen `maxerror` van de kernel. Dezelfde bron als die `timedatectl` rapporteert
  als `NTPSynchronized`, en ze vergt geen pakket, geen rechten en geen
  `timedatectl` in de container — wat een slanke Python-image niet heeft.
- **Wandklok tegen monotone klok** tussen rondes, zodat een klok die *gezet* werd
  in plaats van *verstreken* opgemerkt wordt, hoe tevreden de kernel ook is.
- **Nooit achteruit**, over herstarts heen, via een hoogwatermerk in de
  settings-tabel. Dat vangt een host op die opstartte zonder netwerk en terugviel
  op een RTC-waarde of een builddatum, waar `adjtimex` perfect gelukkig mee kan
  zijn.

> **Reikwijdte, eerlijk gezegd.** In een LXC deelt de container de klok van de
> host en mag hij ze mogelijk niet zetten: `timedatectl` rapporteert daar `NTP=no`
> naast `NTPSynchronized=yes`. Wat we lezen is dus de claim van de **kernel van
> de host**, doorgegeven. "De host zegt dat hij gesynchroniseerd is" is niet
> hetzelfde als "de tijd is aantoonbaar juist". **Draai je de server in een
> container, dan rust de correctheid van elke klok in deze mesh uiteindelijk op
> de NTP-configuratie van de machine eronder** — als die verkeerd staat, draait
> dit alles netjes, meetbaar en volledig verkeerd mee.

Twee controles zijn overwogen en verworpen. Kruiscontrole tegen tijdstempels uit
de mesh is circulair: de nodes waartegen we zouden controleren zijn de nodes die
we zetten, dus overeenstemming bewijst enkel dat ons eigen bericht aankwam — en
het `rx`-bericht draagt `t` als uptime-teller, niet als wandklok, dus de bruikbare
bron is er niet eens. Een externe tijdsbron bevragen werkt evenmin: deze server
zit achter VPN/LAN zonder uitgaande referentie, dus die controle zou slagen in
ontwikkeling en op de echte machine eeuwig "onbereikbaar" rapporteren, wat een
controle is die binnen de week wordt uitgezet.

Wat de node ermee doet, staat in `MeshManagerNet.cpp` boven `MON_CLK_FIRST_MS`: hij
zet zijn eigen klok, loopt dan zijn monitorlijst af en vraagt elke repeater
`clock` (één heen-en-terug), en stuurt enkel `clock sync` naar wie meer dan twee
minuten achterloopt. Eerst lezen kost evenveel als blind synchroniseren — één
commando, één antwoord — dus het argument ervoor is geen zuinigheid maar bewijs:
het maakt van "deze repeater liep vier minuten achter" iets dat de site kan
tonen, en het betekent dat de node nooit een klokwijzigend commando op goed geluk
uitzendt. De drempel ligt op twee minuten omdat `clock` op de minuut antwoordt en
het antwoord seconden na het uitlezen aan de overkant toekomt; de firmware
berekent de drift als een *bereik* en handelt enkel wanneer het hele bereik
voorbij de drempel ligt.

Zendtijd: één commando en één antwoord per gemonitorde node per dag, tegenover de
drie van elk die een gewone pollronde nu al om de vijftien minuten uitgeeft.
Daarom mag dit volgens een schema draaien waar de settings-sweep dat niet mag. De
node begrenst zijn eigen LoRa-helft tot één keer per uur, wat er ook op het topic
binnenkomt.

### Een repeater bereiken die niet publiceert

De derde vorm bestaat voor het geval waarrond dit project gebouwd is: een
repeater op een dak die enkel over LoRa praat. Zijn statistieken bereiken de site
omdat een andere node hem pollt en ze doorstuurt, maar er was helemaal geen
commandopad *naar* hem toe — de site kon zijn cijfers tonen en niets over zijn
configuratie. De knop op zijn instellingenpagina zei "doorgegeven, alleen de node
zelf kan zijn eigen CLI uitlezen", wat even waar als nutteloos was.

Een monitor logt sowieso al in op die repeater en pollt hem elke ronde, dus hij
kan er evengoed de CLI van aflopen. Sinds 1.9.0 doet hij dat, op verzoek:
dezelfde achttien `get`-commando's over de lucht, één per keer, en één bericht
op het eind met wat er terugkwam.

Dat bericht is het dure onderdeel van dit ontwerp, en de firmware begrenst het
navenant — enkel op verzoek en nooit volgens een schema, hoogstens één sweep per
tien minuten, twee seconden tussen commando's, twaalf per antwoord, en stoppen na
drie opeenvolgende stiltes. De redenering achter elk van die getallen, inclusief
welke waarden van de Home Assistant-integratie zijn overgenomen en welke bewust
niet, staat boven `MON_SET_FIRST_MS` in `MeshManagerNet.cpp`.

Twee dingen over het resultaat zijn het waard om te weten voor je de pagina
leest:

- **Een parameter die gevraagd werd en stil bleef, wordt gepubliceerd als `null`**
  en verschijnt als "(geen antwoord)", waarbij overschreven wordt wat er stond.
  Met opzet: het gebruikelijke faalgeval is anders onzichtbaar. Een repeater
  voert een CLI-commando enkel uit voor een client met **admin**rechten, dus een
  monitor die read-only inlogt — wat voor al de rest hier volstaat, en wat de
  firmware-header aanbeveelt — krijgt een login die slaagt en daarna achttien
  stiltes. Geef `setperm <monitor-pubkey> 3` op de gemonitorde repeater, of geef
  de monitor zijn adminwachtwoord, als die instellingen hier leesbaar horen te
  zijn.
- **Een sweep waarvan de login nooit antwoordde, publiceert helemaal niets**,
  want hij vroeg niets en leerde niets. Waarden weggooien die een eerdere sweep
  wél kreeg, zou de verkeerde soort eerlijkheid zijn.
- **`cmd:region` is een boom, geen waarde** (nodefirmware 1.11.0). Het is de ene
  parameter waarvan het antwoord over meerdere regels loopt, en waarvan de
  regeleindes *en de inspringing* de betekenis dragen: inspringing is
  ouder/kind-nesting, `^` markeert de thuisregio, een afsluitende ` F` betekent
  dat flooding daar toegestaan is en de afwezigheid ervan dat het geweigerd
  wordt. Het komt aan als **één** tekstbericht — MeshCore begrenst de boom zelf
  op 160 bytes (`handleRegionCmd` roept `exportTo(reply, 160)` aan) en verstuurt
  het hele antwoord in één datagram — dus er is op dit pad geen verzameling over
  meerdere pakketten. Een boom die groter is, wordt aan de overkant afgekapt,
  niet hier. De sleutel is `cmd:region` en niet `region`: `cmd:<x>` is de notatie
  van deze site voor "voer `<x>` letterlijk uit in plaats van `get <x>`", en de
  rij in `repeater_cli` is genoemd naar de geconfigureerde parameter. Het
  publiceren als `region` zou een tweede rij naast de bestaande hebben gemaakt en
  de oorspronkelijke hebben laten verouderen.

**Er wordt niets geretained, en QoS blijft 0.** Een geretained commando wordt bij
elke herverbinding opnieuw afgeleverd, dus de node zou zijn CLI aflopen bij elke
boot en na elke WiFi-onderbreking zolang het bericht op de broker bleef staan —
en niemand zou dat linken aan een knop die weken eerder één keer werd ingedrukt.
QoS 0 omdat het alternatief niets oplevert: de node verbindt met een clean
session, dus de broker zet niets in de wachtrij terwijl hij offline is. Een node
die slaapt op zijn energiebudget mist het bericht gewoon.

**Daarom controleert de pagina voor ze iets belooft.** `commanding.py` beslist of
er überhaupt een commando uit kan, en welk. Het kiest de node die het zal
ontvangen — de repeater zelf, of de node die zijn statistieken doorgeeft —
controleert de firmware van *die* node tegen de versie die de gekozen route nodig
heeft (1.8.0 voor een node die zijn eigen CLI leest, 1.9.0 voor een monitor die
die van iemand anders leest), controleert dat de server met de broker verbonden
is, en — voor de terugvalroute — dat een poller in de laatste 15 minuten
`/api/v1/commands` ophaalde. Is er geen route open, dan is de knop uitgeschakeld
en zegt de pagina welke van die voorwaarden ontbreekt. Een oudere firmware
abonneert zich niet op `cmd`, of abonneert zich wel en weigert het argument, en
in beide gevallen slaagt het publiceren en verdwijnt het; dat is het faalgeval
waarvoor deze controle bestaat.

De route draagt ook *welke* commando's ze aankan. Aan een monitor kan gevraagd
worden de instellingen van een andere repeater te lezen, maar niet om namens hem
een statusbericht te publiceren — hij stuurt die cijfers elke ronde toch al door
— dus de statusknop op een doorgegeven repeater blijft op de pollerroute of
blijft grijs.

### ACL

Voeg de leeskant toe aan het account van de node en de schrijfkant aan dat van de
server:


> **Tijdens de hernoeming** heeft elke regel hieronder ook zijn
> `meshcore/…`-tegenhanger nodig: een node publiceert op het oude voorvoegsel
> tot hij geflasht is en op het nieuwe daarna. Een ACL die er maar één van de
> twee kent, laat precies één van die twee toestanden in stilte doodlopen —
> de node meldt een geslaagde publish en de broker gooit het bericht weg.
> `init-passwd.sh` en `add-node-user.sh` genereren allebei al. Zie
> [`migration.md`](migration.md).
```
user meshmanager
topic read meshmanager/#
topic write meshmanager/+/cmd

user node-e3d3f4d7ed01
topic write meshmanager/e3d3f4d7ed01/stats
topic write meshmanager/e3d3f4d7ed01/rx
topic read  meshmanager/e3d3f4d7ed01/cmd
```

Sinds nodefirmware 2.8.0 kan het `cmd`-topic ook een *instelling wijzigen*
(`set <param> <waarde>`), en daarmee wordt "wie mag er publiceren op
`<voorvoegsel>/<node>/cmd`" de bepalende vraag in plaats van een kwestie van
netheid. Met een account per node en de regels hierboven luidt het antwoord "de
site, en niemand anders". Met één **gedeeld account** — en daar draait een
standaardopstelling van `init-passwd.sh` op — houdt elke node inloggegevens
waarmee op het `cmd`-topic van elke andere node gepubliceerd kan worden. Lees
[`security.md`](security.md#de-broker-is-nu-de-bepalende-vraag) voordat je op die
schrijfweg gaat leunen.

Zonder de leesregel verbindt de node, abonneert hij zich, wordt hij door de
broker geweigerd, en rapporteert hij daar niets over — de knop ziet er dan
precies even dood uit als voor dit alles bestond. `wifi mqtt` op de node drukt
een teller `cmd=<accepted>/<refused>` af om precies die reden.

Twee regels aan serverzijde, beide in `_handle_settings`:

- **Instellingen komen van de repeater zelf, of van de node die zijn statistieken
  al doorgeeft.** Tot 1.9.0 was dit "zijn eigen instellingen, punt", op grond van
  het feit dat de firmware nooit iets anders stuurde — en dat hield op te kloppen
  op het moment dat een monitor de CLI van iemand anders kon aflopen. Wat het
  kost, is het waard om onomwonden te zeggen: een client met de gedeelde
  brokergegevens kon al *statistieken* publiceren voor eender welke repeater (zie
  de noot over identiteit bovenaan `mqtt_ingest.py`, en de ACL per node die dat
  dichtzet). Instellingen kosten die client nu één extra stap — hij moet eerst de
  node worden waarlangs de statistieken van deze repeater binnenkomen, en dat is
  op de adminpagina zichtbaar als een gewijzigde `source_prefix`. Identiteit wordt
  vergeleken via de repeaterrij, niet via de string, aangezien topic en payload
  dezelfde sleutel in verschillende lengtes kunnen spellen, en de *vorige*
  doorgever wordt gelezen voordat `record_source` hem overschrijft — achteraf
  vergelijken zou een publisher met zichzelf vergelijken.
- **Een weggelaten parameter is geen verwijderde, maar een expliciete `null` is
  een feit.** De firmware laat weg wat ze niet kon lezen, dus dit pad roept
  `upsert_cli_settings(..., prune=False)` aan, en lege strings worden vóór de
  aanroep weggegooid. `null` blijft behouden en wordt als NULL opgeslagen: het
  betekent "gevraagd, geen antwoord", wat de gemonitorde sweep verstuurt en wat
  de pagina rendert als "(geen antwoord)".

### Wat de server ermee doet

`db.ingest()`:

0. Bepaal het onderwerp: `repeater.pubkey_prefix` als het aanwezig is, anders het
   nodesegment van het topic. Het nodesegment wordt apart vastgelegd als de
   publisher.
1. Zoek de repeater op bij die prefix of maak hem aan. **Onbekende repeaters
   worden automatisch aangemaakt en zijn standaard publiek** — verberg ze in
   `/admin`.
2. Zet elke metriekwaarde om: `bool` → `1.0`/`0.0`, numeriek → `float`, al de
   rest → opgeslagen als string in `latest.value_str` (afgekapt op 255 tekens)
   zonder meetpuntrij.
3. Upsert `latest`.
4. Schrijf **enkel** een `samples`-rij als de waarde veranderde, of als het
   nieuwste opgeslagen meetpunt ouder is dan `heartbeat_min` minuten (standaard
   5), of als `force` gezet is.
5. Upsert de burenrijen en hun SNR-reeksen per verbinding.
6. Leg vast welke node het afleverde (`db.record_source`). Het HTTP-ingestpad
   schrijft daar `api` neer, zodat een repeater die naar HTTP verhuisde geen
   verouderde nodeprefix blijft tonen.

Metrieksleutels worden letterlijk opgeslagen — geen normalisatie, geen
toelatingslijst. Een sleutel die de server niet herkent, wordt gerenderd onder de
sectie "Overig" met de underscores omgezet in spaties.

## Payload: `rx` — ruwe pakketten doorsturen

> **Nog niet uitgekristalliseerd.** De firmwarekant is in dienst en de decoder
> aan serverzijde `server/app/packets.py` bestaat nu en wordt aangeroepen vanuit
> `mqtt_ingest.py`. Veldnamen in het *gedecodeerde* resultaat kunnen nog
> veranderen; de vijf sleutels van het bericht zelf, hieronder, niet.

De bedoeling: de node parset niets. Hij codeert elk frame in hex precies zoals
het van de radio kwam en laat de server het decoderen met
[`protocol.md`](protocol.md).

```json
{"t": 1284511, "snr": -4.25, "rssi": -94, "len": 129, "fwd": 0, "why": "hops",
 "raw": "1100ab..."}
```

| Sleutel | Betekenis |
|---|---|
| `t` | `millis()` van de node bij ontvangst — **uptime, geen wandklok** |
| `snr` | dB, gereconstrueerd uit het SNR×4-geheel getal van de radio |
| `rssi` | dBm |
| `len` | framelengte in bytes |
| `fwd` | wat het pakketfilter van deze node deed: `1` doorgelaten, `0` geweerd. **Afwezig als het filter dit pakket niet beoordeeld heeft** (2.7.0+, alleen repeater) |
| `why` | waarop het geweerd werd — `type`, `hops`, `rate`, `hash`, `kanaal`, `misvormd`. Alleen aanwezig bij `"fwd": 0` (2.7.0+) |
| `raw` | hex in kleine letters, `len * 2` tekens |

`fwd` ontbreekt veel vaker dan het er staat, en die afwezigheid is een eigen
waarde: het filter beoordeelt uitsluitend *flood*-pakketten die het moet
doorsturen. Een pakket aan deze node zelf, een direct gerouteerd pakket, of een
frame dat nooit geparseerd is, bereikt `allowPacketForward()` niet eens. Een
ontbrekende `fwd` als 'doorgestuurd' lezen maakt van 'niemand heeft gekeken' een
bewering.

Het oordeel reist mee in het bericht van het pakket zelf en komt niet als tweede
bericht met een pakkethash als sleutel. Dat scheelt een hash die beide kanten
identiek moeten berekenen, een volgordeprobleem in twee richtingen, en oordelen
over pakketten die de server nooit ontvangen heeft. Het pakket staat nog in de
rx-ring wanneer de beslissing valt, dus het oordeel haalt het in voordat het
vertrekt.

**Hoe lang het mag wachten, en een correctie.** 2.7.0 verscheen met de bewering
dat ontvangst en doorstuurbeslissing "binnen dezelfde verwerking van hetzelfde
pakket" gebeuren. Dat klopt niet, en het is gemeten in plaats van beredeneerd:
van 72 pakketten vertrok **68% van het floodverkeer zonder oordeel** —
payloadtypes 0, 1, 4, 5 en 6, allemaal geparseerd, allemaal wel degelijk langs
`allowPacketForward()`. Er zaten twee dingen fout.

`mmnet_loop()` staat in `main.cpp` op regel 196 en `the_mesh.loop()` op 214, dus
publiceren gaat *vóór* ontvangen-en-beslissen. Een pakket dat in ronde N
binnenkomt vertrekt aan het begin van ronde N+1 en heeft dus alleen de rest van
ronde N om zijn oordeel op te halen; MeshCore stelt het doorsturen van
floodverkeer uit, dus het oordeel valt vaak later. Sinds 2.8.1 mag een nog niet
beoordeeld pakket daarom tot `RX_VERDICT_GRACE_MS` (50 ms sinds 2.8.2) wachten voordat het
alsnog vertrekt.

Dat is **niet** de afgewezen opzet waarbij publicatie wacht tot ná de
beslissing. Alles wordt nog steeds gepubliceerd: een frame dat nooit geparseerd
wordt, zit de wachttijd gewoon uit en gaat zonder oordeel, dus de ruis waarvoor
deze stroom bestaat (§"Wat een node aanvaardt, en wat hij toch doorgeeft") blijft
in het archief staan, een fractie van een seconde later. En de ring gaat voor:
loopt die vol, dan vertrekken pakketten ongestempeld in plaats van verloren te
gaan.

De tweede fout was erger dan een gemiste aantekening. Het oordeel werd gestempeld
op "de sleuf die zojuist gevuld is", en dat koppelt stil verkeerd zodra er twee
pakketten tegelijk onderweg zijn: het oordeel van het eerste pakket belandt op
het tweede. Er wordt nu op inhoud gekoppeld — `payload` is de staart van het
bewaarde frame (`protocol.md` §1.1), dus een `memcmp` op die staart wijst het
pakket exact aan.

`/api/status` meldt onder `mqtt` de tellers `vok`, `vlate`, `vforced`, `vdup`,
`vavg` en `vmax`, zodat de wachttijd op een meting gezet kan worden in plaats van
op een gevoel. En dat is gebeurd: 2.8.1 mat `vavg` 1 ms en `vmax` 2 ms, dus 2.8.2
bracht de wachttijd terug van 400 naar 50 ms — vijfentwintig keer de traagste
meting in plaats van tweehonderd. Niet naar 5 ms: die `vmax` steunt op twee
waarnemingen, en een grens strak om een steekproef van twee leggen is dezelfde
fout als 400 ms, alleen de andere kant op.

`vdup` is het ene dat de server niet zelf kan uitrekenen: hoeveel van de niet
beoordeelde pakketten herhalingen waren die MeshCore al had laten vallen. Het is
een schatting en dat staat erbij — de payloadgrens is op de node niet bekend, dus
de vingerafdruk gaat over de laatste 16 bytes van het frame. De payload is de
staart en twee ontvangsten van hetzelfde floodpakket verschillen alleen in hun
pad, dus die staart komt overeen; een payload korter dan het venster reikt in het
pad en telt niet mee, wat het getal een ondergrens maakt en nooit een
overschatting.

De ontwerpbeperkingen zijn op beide firmwares dezelfde, maar **de getallen niet**,
en ze door elkaar halen is makkelijk:

| | Companion (`StatsPublisher`) | Repeater (`MeshManagerNet`) |
|---|---|---|
| Ringgrootte | `STATS_RX_QUEUE` = **4** plaatsen | `MQTT_RX_QUEUE` = **8** plaatsen |
| Grootte per plaats | `STATS_RX_MAX_LEN` = 255 (een volledige MTU, 264 B met padding) | `MQTT_RX_MAX_LEN` = 255 |
| Statisch RAM | ~1 kB | ~2 kB |
| Publicaties per `loop()`-doorgang | **1** | `MQTT_DRAIN_MAX` = **4** |
| Publicatiebuffer | `setBufferSize(255 * 2 + 128)` | `setBufferSize(MQTT_PUB_MAX)` = 5120 |

Vier plaatsen op de companion is een bewuste afweging die bij de bron is
vastgelegd: doorsturen is best-effort — de wachtrij wordt in haar geheel
weggegooid zodra de broker onbereikbaar is — en RAM is daar meer waard dan pieken
opvangen. Terug naar acht gaan zou 1056 bytes statisch RAM kosten. Op de repeater
kopen acht plaatsen ruwweg één verkeersburst; daarbovenop verliest hij liever
pakketten dan geheugen.

De gedeelde regels:

- **De ontvangstcallback kopieert alleen.** Publiceren vanuit de ontvangstlus zou
  de ontvangst ophouden. Dezelfde discipline als bij de `cmd`-callback en de
  apply-vlaggen van de webserver.
- **Wachtrij vol → het pakket wordt verworpen** en een teller loopt op. Een pakket
  verliezen is beter dan de mesh laten stilvallen.
- **Geen brokerverbinding → de hele wachtrij wordt geleegd** en als verwerpingen
  geteld, in plaats van bij herverbinding een burst verouderde pakketten te
  versturen.
- **`setBufferSize()` is niet optioneel.** Een ruw frame wordt meer dan 500
  hextekens, en de standaardwaarde van 256 bytes bij PubSubClient zou `publish()`
  *stilzwijgend doen weigeren* — dezelfde soort fout als de verkeerdetopicbug van
  1.3.0.

Op de repeater is doorsturen bovendien afhankelijk van de batterij: boven
`bat_live` procent vertrekt een ontvangen pakket onmiddellijk, daaronder wacht de
node. Zie [`firmware.md`](firmware.md).

## Payload: `alert` — een storing, op het moment dat hij gebeurt

**Telemetrie is SNMP-polling; een alarm is een SNMP-trap.** Die ene zin is het
ontwerp. Pollen is regelmatig, betaalbaar en volledig, en weet niets van wat er
tussen twee rondes gebeurde. Een trap komt op het moment dat er iets gebeurt,
draagt één feit, en arriveert misschien niet. Je hebt ze beide nodig: de trap zegt
*wanneer*, de poll zegt *wat*.

Een sensornode stuurt zijn alarmen als DM naar de contacten met
`PERM_RECV_ALERTS_*`. Een repeater die in die lijst staat, krijgt zo'n DM binnen
als een `TXT_MSG` en publiceert hem — sinds firmware 2.10.0 — meteen op
`<prefix>/<node>/alert`:

```json
{
  "alert": {
    "pubkey_prefix": "48d7aade232b",
    "name": "MeshUptime",
    "text": "hoas onbereikbaar (hoas.scheepers.one)",
    "ts": 1755691200,
    "snr": 8.50
  },
  "via": "aabbccddeeff"
}
```

| Veld | Type | Inhoud |
|---|---|---|
| `alert.pubkey_prefix` | string | De node waar het alarm **over gaat**: 6 byte van zijn publieke sleutel, hex |
| `alert.name` | string | De naam van die node zoals de doorgever hem kent, JSON-ge-escaped |
| `alert.text` | string | De melding zoals de node hem schreef |
| `alert.ts` | int | Epochseconden **van de doorgevende repeater**, weggelaten als zijn klok niet staat |
| `alert.snr` | float | Signaal van de laatste advert van die node, weggelaten als onbekend |
| `via` | string | De doorgevende repeater — dezelfde waarde als in het topic |

**Een eigen topic, en geen veld in `stats`.** Een trap hoort niet te wachten op de
volgende ronde, en het statistiekenbericht *is* die ronde. Erin proppen zou een
alarm precies zo traag maken als het pollen dat hij moet aanvullen — en het zou een
bericht dat over *metingen* gaat een tweede betekenis geven.

**Het onderwerp staat in de payload, de doorgever in het topic.** Langs deze weg
zijn die twee per definitie verschillend: een sensornode publiceert helemaal niet.
Koppelen op het topic zou elke storing aan de repeater hangen in plaats van aan de
node die stilviel.

**De tijdstempel is die van de doorgever en nooit die van de sensornode.** Zo'n
node heeft geen batterijgevoede klok en staat na elke herstart op 15 mei 2024 —
precies het apparaat dat deze alarmen stuurt. De server weigert alles van vóór
2025 en zet zijn eigen ontvangsttijd, want anders staat de melding onder elke
andere regel in de lijst, waar niemand hem ziet.

**Herhalingen zijn normaal.** De node stuurt opnieuw tot hij een ACK krijgt, en
een monitorbericht wordt niet bevestigd, dus één storing levert een handvol
identieke DM's op. De repeater remt dat aan zijn kant af (`MON_ALERT_DEDUP_MS`) en
de server nog een keer (`db.ALERT_DEDUP_S`, 300 s) — die eerste rem leeft in RAM en
overleeft geen herstart, en twee repeaters die dezelfde node horen zouden er elk
een sturen.

**En dezelfde gebeurtenis kan langs een tweede weg binnenkomen.** Zolang de
mesh-schakel node→repeater stuk is — op dit moment een bevestigd hardwaredefect —
leidt de server alerts af uit zijn eigen IP-poll van de sensornode
(`sensornode._derive_alerts`): een overgang tussen twee rondes wordt een alertrij
met `source='ip'`, tot `MM_SENSOR_POLL_S` laat. Zodra het mesh weer werkt, komt
dezelfde storing ook hier binnen, met een tekst die nét verschilt. Beide
schrijvers stempelen daarom een `kind` (`neer`, `op`, `stil`) en `db.add_alert`
ontdubbelt over de bronnen heen op (node, soort, kanaal-of-dienstnaam) binnen
`ALERT_CROSS_DEDUP_S` (900 s — drie pollrondes, wat de pollvertraging dekt plus
een mesh-alert dat nog binnendruppelt terwijl de node herhaalt). De prijs staat
bij het venster zelf: een dienst die binnen vijftien minuten écht twee keer
dezelfde overgang maakt, levert één rij op en geen twee.

**En de reflex:** een alarm trekt op de doorgevende repeater de volgende
uitvraagronde naar voren, zodat de cijfers die bij de storing horen binnen seconden
volgen in plaats van bij het volgende interval. Begrensd door drie remmen; zie
[`firmware.md`](firmware.md).

## Retentie en QoS

**Er wordt niets geretained.** Elke publicatie geeft `false` mee voor de
retain-vlag:

```c
_mqtt.publish(topic, (const uint8_t *)body, n, false);
```

Dat is voor beide topics de juiste keuze. Een geretained `stats`-bericht zou elke
nieuwe abonnee een verouderde momentopname aanreiken, en de server zou die
ingesten alsof ze actueel was. Een geretained `rx`-frame zou nog erger zijn.

QoS is 0 in beide richtingen. Een publicatie die mislukt, wordt geteld in
`_fail_count` en verder vergeten; het volgende interval brengt sowieso een verse
momentopname. Voor `rx` blijft het item na een mislukte publicatie in de wachtrij
staan voor één nieuwe poging, waarna het verjaart.

Gevolg: **MQTT geeft je geen afleveringsgarantie.** Gaten in een grafiek na een
WiFi-hapering zijn te verwachten. Heb je gegarandeerde aflevering nodig, gebruik
dan het HTTP-ingestpad, dat een statuscode teruggeeft.

<a id="node-side-configuration"></a>
## Configuratie aan nodezijde (companion)

Ingesteld op de eigen beheerpagina van de node op `http://<node-ip>/`, opgeslagen
in `/stats_cfg.json` op SPIFFS.

| Veld | Standaard | Opmerkingen |
|---|---|---|
| `host` | *(leeg)* | Brokeradres. Leeg = de publisher doet niets. |
| `port` | `1883` | Valt terug op 1883 bij 0 of buiten bereik |
| `user` | *(leeg)* | Leeg = anoniem verbinden |
| `pass` | *(leeg)* | Leeg bij opslaan = bestaande behouden |
| `prefix` | `meshcore` | Topicprefix; leeg zet terug naar `meshcore` |
| `interval` | `300` | Seconden, **begrensd op minimaal 30** |
| `enabled` | `false` | Hoofdschakelaar |
| `forward_rx` | `true` | Ontvangen pakketten spiegelen (in ontwikkeling) |

De client-id is `meshcore-<node_hex>` — afgeleid van de publieke sleutel, zodat
twee nodes nooit botsen.

Herverbindingsgedrag: bij een mislukking wacht de node **15 seconden** voor een
nieuwe poging (`_last_connect_try`). De commentaar legt uit waarom: een
onbereikbare broker blijven bestoken kostte de node vroeger zijn hele
responsiviteit.

`TLS wordt niet ondersteund op de node.` `PubSubClient` draait over een gewone
`WiFiClient`.

## Configuratie aan nodezijde (repeater)

De repeater bewaart zijn instellingen in `/msnet.json` op SPIFFS, niet in
`/stats_cfg.json`, en ze worden op drie uitwisselbare manieren gezet: de
adminpagina op `http://<node-ip>/` (`POST /api/mqtt`), de mesh-/serieel-/telnet-CLI,
of een teruggezette back-up.

| Veld | CLI | Standaard | Opmerkingen |
|---|---|---|---|
| `mqtt_host` | `wifi mqtt host <name>` | *(leeg)* | Leeg = de publisher doet niets. Een hostnaam kost een DNS-wachttijd bij elke verbindingspoging |
| `mqtt_port` | `wifi mqtt port <n>` | `1883` | 1–65535 |
| `mqtt_user` | `wifi mqtt user <name>` | *(leeg)* | Leeg = anoniem verbinden |
| `mqtt_pass` | `wifi mqtt pass <word>` | *(leeg)* | |
| `mqtt_prefix` | `wifi mqtt prefix <p>` | `meshcore` | Leeg zet terug naar `meshcore` |
| `mqtt_enabled` | `wifi mqtt on` / `off` | off | Hoofdschakelaar |
| `mqtt_rx` | `wifi mqtt rx on` / `off` | — | Elk ontvangen pakket doorsturen |

Het publicatie-**interval is hier geen enkel getal.** Het volgt de batterij via
een regeltabel en de klok via een nachtvenster; zie
[`firmware.md`](firmware.md). In power-savemodus bepaalt het interval ook hoe
vaak de radio ontwaakt, en daarom ligt de ondergrens daar hoger (60 s) dan in de
altijd-bereikbaarmodus (10 s).

`wifi mqtt` zonder argument drukt de statusregel af die de meeste vragen in één
keer beantwoordt:

```
verbonden, broker=<host>:1883, prefix=meshcore, rx=aan,
stats=412 pkt=9021 drop=3 cmd=7/2
```

`cmd=<accepted>/<refused>` is de teller die "de site heeft nooit iets gevraagd"
onderscheidt van "de broker weigerde mijn subscribe" en van "het liep en er
veranderde niets".

De client-id is `meshcore-<node_hex>` op beide firmwares — afgeleid van de
publieke sleutel, zodat twee nodes nooit botsen.

Herverbindingsgedrag op de repeater: `MQTT_RETRY_MS` = **15 s** tussen pogingen.
Een broker die niet antwoordt, kost een volledige sockettime-out per poging, en
die tijd gaat rechtstreeks van de mesh af. `setSocketTimeout(4)` en
`setKeepAlive(60)` begrenzen dat verder.

**TLS wordt hier evenmin ondersteund.**

## Configuratie aan serverzijde

| Env-variabele | Standaard | Opmerkingen |
|---|---|---|
| `MM_MQTT_HOST` | *(leeg)* | **Leeg schakelt MQTT-ingest volledig uit** |
| `MM_MQTT_PORT` | `1883` | |
| `MM_MQTT_USER` | *(leeg)* | Leeg = anoniem verbinden |
| `MM_MQTT_PASS` | *(leeg)* | |
| `MM_MQTT_PREFIX` | `meshmanager` | Er wordt ook naar `meshcore` geluisterd |
| `MM_MQTT_TOPIC` | *(leeg)* | Extra patroon, bovenop de voorvoegsels |
| `MM_MQTT_RX_TOPIC` | *(leeg)* | Extra patroon; ruwe pakketten zijn in ontwikkeling |

Merk op dat het Docker Compose-bestand andere effectieve standaardwaarden
meegeeft: `MM_MQTT_HOST=mosquitto` en `MM_MQTT_USER=meshmanager`.

De abonnee draait op een daemonthread met client-id `meshmanager-ingest`,
keepalive 60 s, en paho's eigen reconnect-backoff (2 s tot 60 s).

> De client-id is **hardcoded**. Twee serverinstanties tegen één broker vechten
> erom en verbreken elkaars verbinding voortdurend. Draai er één.

De ingeststatus is zichtbaar in `/admin`: verbindingstoestand, aantal berichten,
aantal fouten, en de laatste foutmelding.

**De server ondersteunt evenmin TLS naar de broker.** Er is geen
`tls_set()`-aanroep en geen CA-configuratie. Brokergegevens gaan in platte tekst
over de lijn. Houd de broker op een vertrouwd netwerk, of termineer elders.

## Mosquitto configureren

De meegeleverde configuratie is `mosquitto/mosquitto.conf`:

```
listener 1883
protocol mqtt

allow_anonymous false
password_file /mosquitto/config/passwd
acl_file /mosquitto/config/acl

persistence true
persistence_location /mosquitto/data/
log_dest stdout
log_type warning
log_type error

message_size_limit 8192
max_keepalive 300
```

Drie instellingen verdienen aandacht:

- **`message_size_limit 8192`.** Een stats-payload blijft ruim onder 1 kB, maar
  een ruw `rx`-frame kan tegen de 600 bytes aanlopen en toekomstige toevoegingen
  kunnen groeien. 8 kB laat ruimte. Verhoog je `STATS_RX_MAX_LEN` op de node of
  voeg je metrieken toe, controleer dit dan.
- **`allow_anonymous false`.** Zonder dat is er helemaal geen authenticatie op het
  MQTT-pad. Zet dit niet uit.
- **`acl_file`.** Bepaalt wie waar mag publiceren. **De broker start niet op als
  het bestand ontbreekt**, dus draai `init-passwd.sh` voor `docker compose up`.

### De brokergebruiker aanmaken

```bash
cp .env.example .env
# edit .env: set MM_MQTT_USER and MM_MQTT_PASS
./mosquitto/init-passwd.sh
```

Het script draait `mosquitto_passwd -c -b` binnen de image
`eclipse-mosquitto:2` tegen `mosquitto/passwd`, en schrijft `mosquitto/acl` met
het serveraccount. Beide bestanden komen uit op eigenaar uid 1883 met modus
`0400`, omdat de broker als die gebruiker draait en weigert op te starten als hij
ze niet kan lezen.

Drie kanttekeningen:

- `-c` **kapt** `passwd` af, en de ACL wordt volledig herschreven. Het script
  opnieuw draaien wist elk node-account dat met `add-node-user.sh` was toegevoegd.
- `-b` zet het wachtwoord op de commandoregel, dus het belandt in je
  shellgeschiedenis en in de proceslijst zolang het draait. Gebruik op een
  gedeelde machine het interactieve `mosquitto_passwd`.
- `mosquitto/acl` staat in gitignore (het somt accountnamen op). `acl.example` is
  het gedocumenteerde formaat.

Gebruik dezelfde gebruikersnaam en hetzelfde wachtwoord op de beheerpagina van de
node.

### Accounts en ACL's per node

Het gedeelde account is de reden dat het topic niet te vertrouwen is: elke node
meldt zich aan als dezelfde gebruiker, dus de broker kan ze onmogelijk uit elkaar
houden en elk van hen kan onder eender welke prefix publiceren. De publisher
vastleggen (hierboven) maakt dat zichtbaar; alleen een account per node maakt het
onmogelijk.

```bash
./mosquitto/add-node-user.sh e3d3f4d7ed01
```

Het script:

1. Weigert alles wat geen 6–32 hextekens in kleine letters is — dezelfde vorm als
   het topicsegment.
2. Voegt `node-e3d3f4d7ed01` toe aan `mosquitto/passwd` (zonder `-c`, zodat
   bestaande accounts overleven) en genereert een willekeurig wachtwoord tenzij je
   er een meegeeft.
3. Voegt een ACL-blok toe dat dat account tot zijn eigen twee topics beperkt.
4. Herstelt eigenaarschap en rechten op beide bestanden.
5. Drukt het wachtwoord **één keer** af — het wordt nergens anders bewaard.

```
user node-e3d3f4d7ed01
topic write meshmanager/e3d3f4d7ed01/stats
topic write meshmanager/e3d3f4d7ed01/rx
```

`stats` en `rx` worden apart opgesomd in plaats van `meshmanager/<prefix>/#`, zodat
een node geen topics kan aanmaken die de server later voor iets anders wil
gebruiken.

Zet de afgedrukte inloggegevens op de beheerpagina van de node en herstart de
broker:

```bash
docker compose restart mosquitto
```

#### De migratie afronden

`init-passwd.sh` laat het gedeelde account achter met `topic write meshmanager/#`
zodat er niets stukgaat zolang er nog nodes op zitten. Precies die regel houdt
impersonatie ook mogelijk. Zodra elke node zijn eigen account heeft:

```
user meshmanager
topic read meshmanager/#
# topic write meshmanager/#   <- delete this line
```

Herstart de broker. Vanaf dan dwingt de broker af dat een node enkel onder zijn
eigen prefix kan publiceren, en weerspiegelt de kolom **Bron** in `/admin` de
werkelijkheid in plaats van een claim.

Twee dingen om te weten over ACL-bestanden van Mosquitto:

- **Topicregels vóór de eerste `user`-regel gelden voor elke client**, anonieme
  inbegrepen. Eén verdwaalde globale regel maakt de rest van het bestand
  betekenisloos. Het gegenereerde bestand begint om die reden met een
  `user`-blok.
- Een gebruiker zonder overeenkomend blok krijgt **geen** toegang. Een account aan
  `passwd` toevoegen zonder ACL-blok laat het niets kunnen publiceren.

Controleer met het account dat je net hebt aangemaakt:

```bash
# allowed
mosquitto_pub -h <broker> -u node-e3d3f4d7ed01 -P <pass> \
  -t meshmanager/e3d3f4d7ed01/stats -m '{"metrics":{"online":true}}'

# refused by the broker
mosquitto_pub -h <broker> -u node-e3d3f4d7ed01 -P <pass> \
  -t meshmanager/aabbccddeeff/stats -m '{"metrics":{"online":true}}'
```

## Problemen oplossen

| Symptoom | Waar te kijken |
|---|---|
| `/admin` toont MQTT als uitgeschakeld | `MM_MQTT_HOST` is leeg |
| Verbonden, nul berichten | `enabled` staat af op de node, of `host` is leeg op de node |
| Nodepagina zegt "niet verbonden" | Brokergegevens; de node probeert het elke 15 s opnieuw |
| Berichten geteld, geen repeater verschijnt | Foutenteller en `last_error` in `/admin`; meestal een ontbrekende `pubkey_prefix` |
| Repeater verschijnt met een verkeerde naam | De naam komt uit de payload, niet uit het topic — controleer `repeater.name` op de node |
| Een repeater toont "via `<prefix>`" in /admin | Een andere node publiceerde zijn stats. Te verwachten bij doorgegeven repeaters; anders onverwacht |
| Node verbindt maar er wordt niets gepubliceerd | ACL: het account heeft geen `topic write`-blok, of de topicprefix komt er niet mee overeen. Bekijk het brokerlog |
| Broker weigert op te starten | `mosquitto/acl` ontbreekt of is onleesbaar — draai `init-passwd.sh` |
| Grafiek heeft gaten | Te verwachten bij QoS 0. Controleer de WiFi-stabiliteit, en `heartbeat_min` |
| Twee servers verbreken elkaars verbinding | Beide gebruiken client-id `meshmanager-ingest`. Draai er één. |

Om het verkeer rechtstreeks mee te volgen:

```bash
mosquitto_sub -h <broker> -u <user> -P <pass> -t 'meshmanager/#' -v
```
