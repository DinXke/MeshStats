# HTTP-API

*[English](../api.md)*

Elke route die de server bedient: de JSON-API in `server/app/routes_api.py`, de
publieke pagina's in `routes_public.py`, en de beheerformulieren in
`routes_admin.py`.

Tijdstempels zijn altijd `JJJJ-MM-DDTUU:MM:SSZ`, UTC, als tekst.
Sleutelprefixen zijn hex in kleine letters. `null` betekent "niet bekend", nooit
"nul" — `routes_api._round()` bestaat uitsluitend zodat een ontbrekende SNR niet
als `0.0` aankomt, wat een volstrekt geloofwaardige en volstrekt verkeerde meting
is.

## Authenticatie

| Groep | Hoe |
|---|---|
| `/api/v1/ping`, `/contacts`, `/commands`, `/repeater_settings`, `/ingest` | `Authorization: Bearer <token>`, gecontroleerd door `routes_api.require_token()` |
| Elke andere `/api/v1/*`-route | Geen. Publiek, alleen-lezen, beperkt tot repeaters met `is_public=1`, en verder gevormd door `show_position` / `show_name` — zie [`privacy.md`](privacy.md) |
| `/admin/*` | Ondertekende sessiecookie, plus een CSRF-token bij elke POST |

`require_token()` antwoordt **401** zonder bearerheader en **403** bij een
onbekend of ingetrokken token, zodat een client "je stuurde niets" kan
onderscheiden van "wat je stuurde deugt niet".

Elke methode en route wordt bovendien begrensd op `MM_MAX_BODY_BYTES` (2 MB)
door `limits.BodySizeLimitMiddleware`, geteld tijdens het lezen.

## Ingest-endpoints

### `GET /api/v1/ping`

Verbindingstest voor de Home Assistant-integratie.

```json
{"ok": true, "app": "meshmanager", "version": 1}
```

### `POST /api/v1/ingest`

Eén momentopname van één repeater. Hetzelfde lichaam dat het MQTT-topic `stats`
draagt.

```json
{
  "repeater": {"pubkey_prefix": "e3d3f4d7ed", "name": "BE-XXX-Example.VIR",
               "fw": "v1.7.2", "fw_meshmanager": "1.10.0"},
  "ts": "2026-08-15T12:00:00Z",
  "metrics": {"bat": 4.15, "online": true, "uptime": 3.5},
  "neighbors": [{"prefix": "2ae7af", "name": "…", "snr": -4.25, "seen_min": 12}],
  "force": false
}
```

| Veld | Verplicht | Opmerking |
|---|---|---|
| `repeater.pubkey_prefix` | ja | Begrensde hex in kleine letters, 2–64 tekens; 422 bij al de rest |
| `repeater.name` | nee | Overgenomen als hij van de bewaarde naam verschilt |
| `repeater.fw`, `repeater.fw_meshmanager` | nee | Alleen wat aanwezig is, wordt geschreven |
| `ts` | nee | Servertijd als het ontbreekt |
| `metrics` | ja | Moet een object zijn; hoogstens 128 namen van hoogstens 64 tekens; anders 422 |
| `neighbors` | nee | Hoogstens 512 regels (daarboven 422); `seen_min` wordt omgerekend naar een absoluut tijdstip. Een regel waarvan `prefix` geen sleutel is, valt eruit en wordt gelogd; de rest van het bericht blijft |
| `force` | nee | Altijd een meting bewaren, ook onveranderd |

Antwoord: `{"ok": true, "repeater": "<slug>"}`. De rij wordt aangemaakt als de
sleutel onbekend is (`db.get_or_create_repeater()`), `source_prefix` wordt op
letterlijk `api` gezet, en ongeveer elke 500e aanroep zet `db.prune()` in gang.

Een nieuw aangemaakte repeater komt **verborgen** binnen (`is_public = 0`) en
blijft van de publieke site tot een beheerder hem in `/admin` vrijgeeft; 429
zodra het repeaterplafond (`db.MAX_REPEATERS`, 500) bereikt is, dat weigert in
plaats van verwijdert. Beide controles komen uit `db.check_snapshot()`, dezelfde
functie die de MQTT-weg gebruikt — zie
[`retention.md`](retention.md#de-tabellen-die-iemand-anders-kan-laten-groeien).

### `POST /api/v1/contacts`

Contactposities die uit adverts geoogst zijn.

```json
{"contacts": [{"prefix": "2ae7c1d40f", "name": "…", "lat": 50.9, "lon": 5.3,
               "type": "repeater"}]}
```

Rijen met een prefix korter dan 6 tekens, of zonder numerieke coördinaten,
worden stilzwijgend overgeslagen — dit is een bulkpush en één slechte vermelding
mag de rest niet kosten. Antwoord: `{"ok": true, "count": <bewaard>}`.

### `POST /api/v1/repeater_settings`

CLI-instellingen van één repeater, gepusht door een poller.

```json
{"repeater": {"pubkey_prefix": "e3d3f4d7ed"},
 "settings": {"name": "…", "role": "repeater", "freq": "869.525", "lat": null}}
```

`null` betekent "gevraagd, geen antwoord" en wordt als zodanig bewaard. De
opzoeking loopt via `db.find_repeater()` en niet via een gelijkheidstest, want
een strikte match begint 404 te antwoorden aan Home Assistant zodra dezelfde
node ook over MQTT rapporteert onder een langere sleutel — en gooit daarmee een
instellingenronde weg die een tot twee minuten LoRa-zendtijd kost om te maken.
404 als er geen repeater past; 422 zonder prefix of instellingenobject.

Bewaard met `prune=True`: dit is een volledige heruitlezing, dus een parameter
die niet meer bestaat verdwijnt.

### `GET /api/v1/commands`

De wachtrij voor een pollende client, die bij het lezen gewist wordt.

```json
{"refresh": ["e3d3f4d7ed"],
 "settings": [{"prefix": "e3d3f4d7ed", "params": ["name", "role", "cmd:region"]}]}
```

Elke poll — de lege inbegrepen — noteert `poller_seen`. Dat is wat de
beheerpagina vertelt of er überhaupt iemand is om een verzoek aan uit te reiken:
een niet-gepollde wachtrij ziet er precies uit als een die een seconde geleden
geleegd is, en zolang er niets pollde, bleef de pagina het tweede beloven. Een
niet-lege uitreiking wordt bovendien gelogd, want na deze aanroep staat het
verzoek nergens anders meer.

### `POST /api/sensorpush`

Gebeurtenis-push van een sensornode: de node meldt zijn eigen overgangen op het
moment dat ze gebeuren, in plaats van te wachten tot de IP-poll ze een ronde
later opmerkt. Een machine-endpoint met een eigen bearer-token
(`MM_PUSH_TOKEN`, leeg = de route antwoordt 503 met die reden), met opzet
buiten elk sessie-/CSRF-mechanisme — de aanroeper is een microcontroller. Let
op het pad: **niet** onder `/api/v1`, en het token is geen API-token van de
beheerpagina.

```json
{"node": "aabbccddeeff", "seq": 17, "boot": 3, "hb_s": 60,
 "events": [{"ch": 6, "kind": "neer", "text": "hoas gemeld als neer",
             "sev": "hoog", "sim": 0}],
 "acked": [5]}
```

* `node` is de 12-hex `pubkey_prefix` van een repeaterrij; een onbekende node
  is een 404 (deze route maakt nooit rijen aan). Vormfouten zijn een 400 met de
  veldnaam in het antwoord; een fout of ontbrekend token is een 401.
* `events` worden alarmen met `source='push'`, over wegen heen ontdubbeld met
  hetzelfde (node, soort, kanaal)-venster dat het mesh en de IP-poll al met
  elkaar verzoent. `sim: 1` markeert een oefening exact zoals de IP-afleiding
  dat doet: "(simulatie)" in de tekst, `kind` NULL.
* `acked` bevestigt de eigen alarmen van de node per kanaal — hetzelfde effect
  als de ack-knop, met een auditregel waarvan de actor de node is.
* Het `200`-antwoord is `{"ok": 1, "ack": [<kanalen>]}`: de kanalen waarvan
  alarmen **aan de serverkant** bevestigd zijn sinds de node er voor het laatst
  van hoorde. Eenmalig geleverd; de afleverstand staat in de databank
  (`alerts.ack_pushed`) en overleeft dus een herstart, en een herhaling van
  dezelfde push (zelfde `boot` en `seq`) krijgt het identieke antwoord terug.
* `hb_s` is de hartslag die de node belooft. Blijft een pushende node langer
  dan 3× die belofte stil (ondergrens 90 s), dan maakt de server een alarm
  "node stil (push)" (soort `stil`, ernst `hoog` — een stille melder betekent
  dat we niets weten) en een herstelmelding zodra hij terug is. Een
  serverherstart ijkt opnieuw in plaats van te alarmeren. Een veranderde
  `boot`-teller is een herstart van de node: geen alarm, wel zichtbaar op de
  nodepagina.

## Publieke data-endpoints

Alles hieronder is beperkt tot repeaters met `is_public=1`, en wordt daarnaast
gevormd door twee zichtbaarheidsschakelaars per node. Met `show_position = 0`
levert geen enkele route hieronder de coördinaten van die node uit, noch zijn
land, noch een afstand die eruit berekend is; met `show_name = 0` wordt zijn
naam overal vervangen door de adreshash `0xNN`. De handhaving is één SQL-view
plus twee benoemde uitdrukkingen in plaats van een filter per endpoint, en de
redenering, de precieze lijst van wat verdwijnt en wat blijft, en wat geen
enkele schakelaar verbergt staan in [`privacy.md`](privacy.md).

### `GET /api/v1/repeaters`

Elke publieke repeater, geordend op `sort_order, name`.

```json
[{"slug": "example", "name": "…", "pubkey_prefix": "e3d3f4d7ed",
  "last_seen": "2026-08-15T12:00:00Z", "online": true,
  "battery_percentage": 96.0, "uptime": 3.5, "neighbor_count": 14}]
```

### `GET /api/v1/repeaters/{slug}`

Eén repeater met elke metriek die hij ooit gemeld heeft, elk met zijn presentatie
uit `metrics.metric_info()`.

```json
{"slug": "example", "name": "…", "pubkey_prefix": "…", "last_seen": "…",
 "metrics": {"bat": {"value": 4.15, "ts": "…", "label": "Batterijspanning",
                     "unit": "V", "section": "battery", "sort": 1}},
 "neighbors": [{"prefix": "2ae7af", "name": "…", "snr": -4.25, "last_seen": "…"}],
 "filter": {"known": true, "on": false, "text": "uit", "dropped": 0,
            "passed": 91422, "reasons": [], "updated": "…"}}
```

`filter` is het pakketfilter van de repeater, zoals hij het in zijn laatste
statistiekenbericht meldde. Het staat met opzet in het publieke antwoord: een
repeater met een filter aan stuurt andermans verkeer niet meer door, en de mensen
die dat merken zijn de lezers van deze API en niet de beheerder van de node.
Zonder dit is "waarom komt mijn bericht niet aan" alleen te beantwoorden door
iemand met een inlog.

`known` false betekent dat de node nooit iets over een filter gezegd heeft —
meestal firmware ouder dan 2.3.0. Dat is **geen** bewering dat er geen filter
aanstaat. `reasons` bevat alleen de redenen die niet nul zijn, grootste eerst. De
regeltabellen (hoplimieten per type, snelheidslimieten, geblokkeerde kanalen)
staan er niet in: dat is beheerdersgereedschap en dat zit achter een login. Zie
[`packet-filter.md`](packet-filter.md).

Een metriek die de catalogus niet kent, wordt nooit geweigerd: die belandt in
sectie `other` met zijn sleutel als label, zodat firmware een metriek kan
toevoegen zonder serverwijziging.

Een buur waarvan de gemelde naam ontbreekt, of enkel zijn eigen prefix is, valt
terug op de naam uit `contacts`.

404 bij een onbekende of niet-publieke slug.

### `GET /api/v1/repeaters/{slug}/map`

Kaartgegevens voor de linkkaart op een repeaterpagina.

```json
{"repeater": {"name": "…", "lat": 50.9, "lon": 5.3},
 "links": [{"prefix": "2ae7af", "name": "…", "snr": -4.25, "last_seen": "…",
            "lat": 50.8, "lon": 5.4, "node_type": "repeater"}],
 "unlocated": 3, "unlocated_names": ["…"],
 "hidden": 1, "hidden_names": ["…"]}
```

`repeater` is `null` als de eigen positie van de repeater onbekend is — ook als
ze bekend is maar niet getoond wordt. Buren zonder positie worden **geteld en
benoemd** in plaats van weggelaten, zodat de kaart nooit stilletjes beweert de
hele buurt te tonen.

`hidden` telt de buren die om een andere reden ontbreken: hun beheerder heeft
gekozen hun positie niet te tonen. Dat wordt met opzet **niet** bij `unlocated`
opgeteld. "Nog geen advert met locatie ontvangen" is een uitspraak over het
mesh; "deze node toont zijn positie niet" is een beslissing van een mens, en één
getal dat allebei dekt zou de eerste zin onwaar maken. De namen rijden wel mee:
de twee schakelaars staan los van elkaar, dus een buur die alleen zijn plek
verbergt, wordt gewoon bij naam genoemd.

### `GET /api/v1/repeaters/{slug}/history`

| Parameter | Type | Standaard | Bereik |
|---|---|---|---|
| `metric` | tekst | *(verplicht)* | ≤ 64 tekens |
| `hours` | int | 24 | 1–2160 |

```json
{"metric": "bat", "hours": 24, "points": [["2026-08-15T12:00:00Z", 4.15]]}
```

Geleverd uit VictoriaMetrics als die antwoordt, en uit de SQLite-tabel `samples`
als hij dat niet doet. De uitwijk is met opzet stil: een bezoeker die naar een
grafiek kijkt, kan niets met de vraag welke databank hem leverde, en de
beheerpagina meldt de gezondheid.

### `GET /api/v1/packets`

De live feed achter de kaart op de startpagina.

| Parameter | Type | Standaard | Bereik |
|---|---|---|---|
| `since_id` | int | 0 | ≥ 0 |
| `limit` | int | 200 | 1–500 |

Gepolld en niet gepusht: enkele seconden vertraging kost hier niets, en gewoon
pollen overleeft proxy's, slapende laptops en herstarts waar SSE of websockets
elk hun eigen herverbindingslogica voor nodig zouden hebben.

Pakketten komen altijd **oplopend op id** binnen, en `last_id` is het hoogste id
in het antwoord, zodat de volgende poll precies verdergaat waar deze eindigde. De
eerste aanroep (`since_id=0`) geeft de *nieuwste* `limit` pakketten in plaats van
de oudste bewaarde, zodat een vers geladen pagina op het heden opent.

```json
{"last_id": 84213,
 "packets": [{
   "id": 84213, "ts": "2026-08-15T12:00:00Z",
   "observer": "2ae7c1d40f93", "observer_name": "…",
   "snr": 6.25, "rssi": -92, "len": 57,
   "route": "FLOOD", "type": "ADVERT",
   "scope": "unscoped", "scope_region": null,
   "path_len": 2,
   "sender": "2ae7c1", "sender_name": "…",
   "src": null,
   "lat": 50.9, "lon": 5.3, "origin": "sender",
   "sender_lat": 50.9, "sender_lon": 5.3,
   "observer_lat": 50.8, "observer_lon": 5.4,
   "path": [{"hash": "2a", "state": "known", "lat": 50.9, "lon": 5.3}],
   "country": "BE"
 }],
 "nodes": [{"prefix": "2ae7c1", "name": "…", "lat": 50.9, "lon": 5.3,
            "node_type": "repeater", "country": "BE"}],
 "countries": ["BE", "NL"],
 "hidden_nodes": 2}
```

`nodes`, `countries` en `hidden_nodes` staan er **alleen bij de eerste
aanroep**, zodat de kaart zijn basislaag uit hetzelfde verzoek kan tekenen.
`countries` ontbreekt helemaal als `borders.json` er niet is, wat voor de client
het teken is om het landfilter uit de pagina te laten.

`hidden_nodes` zegt hoeveel nodes er **niet** in `nodes` staan omdat hun
beheerder gekozen heeft hun positie niet te tonen. Ontbreekt als het er geen
zijn — een nul melden is ruis. Iets anders dan de eigen telling "N nodes buiten
beeld" van de kaart: die lost een klik op, deze niet.

`lat`/`lon`/`origin` is de positie waar de ontvangst getekend wordt: die van de
afzender als een advert hem noemde, anders die van de waarnemer, en `origin` zegt
welke van de twee. `country` volgt diezelfde keuze, zodat filteren op land
overeenkomt met het bolletje dat de bezoeker ziet.

De vermeldingen in `path` zijn teruggebracht tot wat een bewegend bolletje nodig
heeft. **Een positie wordt alleen uitgereikt voor een hop die precies één
geplaatste node oplevert** — toestand `known`. Al de rest houdt zijn toestand en
geen coördinaten, zodat de client dat stuk tekent als het gatenvrije gat dat het
is. `likely` staat met opzet aan de verkeerde kant van die lijn: een rangschikking
is goed genoeg om een waarschijnlijke node in woorden te noemen naast de reden
waarom hij waarschijnlijk is, en niet goed genoeg om een lijn op een kaart te
tekenen, waar die reden niet meereist. Zie [`candidates.md`](candidates.md).

`src` lost de afzenderhash van één byte op voor pakketten die geen advert zijn;
hij is `null` als het pakket zijn afzender al noemt, of als het payloadtype
helemaal geen afzenderhash draagt.

### `GET /api/v1/packets/search`

Het pakketarchief achter `/pakketten`. Rijen, totaal, histogram en facetten in
één aanroep — want ze beantwoorden alle dezelfde vraag, en een pagina die vier
verzoeken per aanslag afvuurde zou ze op verschillende ogenblikken zien
binnenkomen, wat leest als een kapotte zoekfunctie.

| Parameter | Type | Standaard | Opmerking |
|---|---|---|---|
| `q` | tekst | `""` | De zoektaal; ≤ 500 tekens. Zie [`search.md`](search.md) |
| `since` | tekst | nu − 24 u | `JJJJ-MM-DDTUU:MM[:SS][Z]`; al het andere wordt genegeerd |
| `until` | tekst | open einde | Zelfde vorm |
| `limit` | int | 100 | 1–500 |
| `offset` | int | 0 | 0–100 000 |
| `facets` | tekst | `""` | Komma-gescheiden veldnamen, hoogstens 6 |
| `sort` | tekst | `""` | `veld` of `veld:asc\|desc` |

```json
{
  "total": 1843,
  "offset": 0,
  "sort": "time:desc",
  "bucket_s": 1440,
  "histogram": [{"t": 1755172800, "n": 37}],
  "facets": {"type": [{"value": "ADVERT", "count": 812}]},
  "packets": [{
    "id": 84213, "ts": "…",
    "observer": "2ae7c1d40f93", "observer_name": "…",
    "snr": 6.25, "rssi": -92, "len": 57,
    "route": "FLOOD", "type": "TXT_MSG",
    "scope": "unscoped", "scope_region": null,
    "path_len": 2, "path": "2a,e7",
    "sender": null, "sender_name": null,
    "src": {"hash": "e3", "state": "likely", "lead": "hops", "total": 3,
            "matches": [{"prefix": "e3d3f4", "name": "…", "hops": 0, "km": 2.1}],
            "dropped": [{"prefix": "e3aa01", "name": "…", "km": 210.4,
                         "why": "range"}],
            "dropped_total": 1},
    "src_hash": "e3",
    "dest_hash": "c3", "dest": {"…": "zelfde vorm als src"},
    "phash": "9f2c1ab30de44571",
    "country": "BE"
  }]
}
```

Vier dingen over die vorm die het waard zijn te weten:

**`sort` komt genormaliseerd terug.** De pagina leest de volgorde die ze
werkelijk kreeg in plaats van te vertrouwen op wat ze vroeg, zodat het pijltje in
een kolomkop en de rijen eronder het nooit oneens kunnen zijn over de richting.

**Sorteren raakt alleen de rijen.** Het totaal, het histogram en de facetten
beschrijven de hele resultaatverzameling, en een verzameling verandert niet
doordat je haar in een andere volgorde opsomt — op een kop klikken mag de
staafgrafiek niet laten flikkeren of de tellingen laten verspringen. Sorteren
raakt wél `offset`: pagina 5 van de ene volgorde heeft niets te maken met pagina
5 van de andere, dus de pagina zet hem terug.

**Elke rij draagt elk veld dat de tabel in een kolom kan zetten**, of de lezer
die kolom nu aan heeft staan of niet. Een antwoord dat zich naar de huidige keuze
voegde, zou elk vinkje in een rondgang veranderen, met een tabel die knippert en
een wachtindicator voor gegevens die de browser al had. Het ene werkelijk zware
veld blijft er met opzet uit: `raw` verdubbelt een pakketrij ruwweg en heeft geen
kolom om in te verschijnen, dus dat blijft op het detailendpoint voor het ene
pakket dat iemand daadwerkelijk opende.

**Een geweigerde zoekopdracht is een 200 met `error`**, geen 4xx:

```json
{"error": "Onbekend veld 'foo'. Bekende velden: country, dest, hash, hops, …",
 "fields": [{"name": "type", "label": "Payloadtype", "kind": "text",
             "hint": "ADVERT", "facet": true}]}
```

Voor dit endpoint is een typfout in de zoekopdracht een gewone uitkomst om naast
het invoervak te tonen, geen uitzonderlijke die ruis in een proxylogboek waard
is. Een onmogelijke sortering gaat dezelfde weg — meestal is het een oude link
die een kolom noemt die sindsdien geschrapt is.

`bucket_s` volgt het venster zodat de grafiek altijd in de orde van zestig staven
heeft: per minuut over een uur, per uur over dagen. Een facet dat een onbekend of
niet-facetbaar veld noemt, wordt overgeslagen en niet geweigerd, want een oude
bladwijzer kan een veld noemen dat sindsdien hernoemd is.

### `GET /api/v1/packets/heatmap`

Linkgebruik over het **volledige bewaarvenster van pakketten**, samengevat voor
de heatmap-laag. Geen parameters.

```json
{"window_h": 168, "packets": 23117, "capped": false, "hidden_nodes": 0, "max": 812,
 "segments": [{"a": {"prefix": "2ae7c1", "name": "…", "lat": 50.9, "lon": 5.3},
               "b": {"prefix": "e3d3f4", "name": "…", "lat": 50.8, "lon": 5.4},
               "n": 41}]}
```

Wat er precies geteld wordt: **één segment per paar opeenvolgend plaatsbare
stops** langs het pad van elk pakket, `afzender → hops → waarnemer`, één keer per
doorgang. `packets` telt de ontvangsten die minstens één segment bijdroegen, niet
de gelezen rijen.

Vier eigenschappen van die samenvatting, elk dragend:

- **Een onzekere hop breekt de keten in plaats van overbrugd te worden.**
  Dezelfde eerlijkheidsregel als de getekende route: een hop die niet precies één
  geplaatste node oplevert, heeft geen positie waar wij recht op hebben. De route
  van één pakket kan zich een gestreepte gok over zo'n gat veroorloven; hier zou
  die gok geteld en hérteld worden tot een massieve, gezaghebbend ogende lijn —
  precies de leugen die een heatmap niet mag vertellen. De hop wordt met opzet
  zonder waarnemer of route opgelost: alleen een `known`-resolutie wordt gebruikt,
  dus een rangschikking zou niets veranderen.
- **Segmenten zijn ongericht.** De belasting van een link is het verkeer erover,
  welke kant het ook opging, dus de sleutel is het gesorteerde paar prefixen.
- **Een stop gelijk aan zijn buur wordt overgeslagen zonder de keten te breken.**
  De waarnemer is vaak de laatste hop, en dat paar tellen zou een link van lengte
  nul opleveren.
- **Gesorteerd, lichtste eerst.** Een client die ze op volgorde tekent, legt de
  zware bovenop, en de oplopende volgorde is dragend voorbij de tekenvolgorde: de
  rangschaal van de client leest de positie van een segment in deze lijst als zijn
  rang.

Het venster is de volledige bewaartermijn (168 u standaard) omdat de laag de
vraag "welke schakels dragen dit mesh" beantwoordt, en een schakel die om de dag
gebruikt wordt hoort bij dat antwoord, ook als de laatste 24 uur hem toevallig
misten. Het eerdere venster van een dag verborg stelselmatig precies die tragere
schakels.

`_heatmap_window_h()` is een **functie** en geen moduleconstante, en dat is er
het punt van: de bewaartermijn is een instelling op de beheerpagina, dus een
lezer die hem naar 30 dagen zet, verwacht dat de heatmap bij de volgende ronde 30
dagen dekt en niet pas na een herstart van de container. Het venster maakt om
dezelfde reden deel uit van de **cachesleutel** — anders zou de instelling
wijzigen tot vijf minuten van een gecachte laag opleveren die stilzwijgend nog de
oude periode dekt en een `window_h` meldt die niet meer bij de instelling past.

Het hele antwoord wordt `_HEATMAP_TTL_S` = 300 s onthouden. Incrementeel
samenvatten is overwogen en afgewezen: tellingen zouden ook moeten *krimpen*
naarmate pakketten voorbij de bewaartermijn verouderen, wat een tijdstempel per
doorgang per segment vraagt — en dan kost de boekhouding meer dan opnieuw een
ronde doen die in seconden klaar is.

`capped` is waar als de query precies `_HEATMAP_MAX_PACKETS` (200 000) rijen
teruggaf, wat betekent dat oudere pakketten in het venster niet meegeteld zijn.
Precies de bovengrens zonder afkapping is mogelijk maar niet te onderscheiden, en
één keer te vaak waarschuwen is de eerlijke kant om aan te zitten.

`hidden_nodes` is dezelfde soort voetnoot om een andere reden. Een node wiens
positie niet getoond wordt, kan geen eindpunt van een segment zijn en **breekt
dus de keten**, precies zoals een dubbelzinnige hop dat doet — verkeer dat
werkelijk over hem liep, wordt niet tot een lijn geteld. Het getal is wat de
laag in staat stelt dat te zeggen; zonder dat zou een ontbrekende drukke lijn
lezen als een stil stuk mesh.

### `GET /api/v1/packets/{packet_id}`

Alles wat er over één ontvangst bekend is.

```json
{"id": 84213, "ts": "…",
 "observer": "2ae7c1d40f93", "observer_name": "…",
 "observer_lat": 50.8, "observer_lon": 5.4, "observer_country": "BE",
 "snr": 6.25, "rssi": -92, "len": 57,
 "route": "FLOOD", "payload_type": 4, "type": "ADVERT",
 "scope": "unscoped", "scope_codes": null, "scope_region": null,
 "path_len": 2, "path_hash_size": 1,
 "sender": "2ae7c1", "sender_name": "…",
 "sender_lat": 50.9, "sender_lon": 5.3, "sender_country": "BE",
 "src": null, "dest": null,
 "raw": "01…",
 "path": [{"hash": "2a", "state": "ambiguous", "lead": null,
           "matches": [{"prefix": "2ae7c1", "name": "…", "lat": 50.9,
                        "lon": 5.3, "node_type": "repeater", "hops": 0,
                        "km": 2.1, "seen": "…"}],
           "dropped": []}],
 "path_stored": true,
 "error": null,
 "advert": {"name": "…", "lat": 50.9, "lon": 5.3, "node_type": "repeater",
            "ts": 1755172800, "pubkey": "2ae7c1…"}}
```

Verschillen met de lijst-endpoints, alle in dezelfde richting — het frame krijgt
het laatste woord:

- `scope`, `scope_codes` en het advertblok worden op verzoek uit `raw`
  gedecodeerd, met de bewaarde kolommen als terugval voor rijen waarvan het frame
  niet bewaard is.
- `path_hash_size` komt **uitsluitend** uit het frame. Het zijn de bovenste twee
  bits van de padbeschrijver, gekozen door wie het pakket het eerst uitzond, dus
  een rij zonder `raw` heeft geen antwoord en `null` is dat antwoord in plaats van
  een geloofwaardig ogende `1`. De client heeft het nodig omdat één hop van twee
  bytes en twee hops van één byte als dezelfde vier hextekens afdrukken.
- De vermeldingen in `path` zijn de **volledige** resolutie en niet de
  ingekorte: coördinaten, `node_type`, `seen`, en de afgevallen kandidaten met hun
  reden.
- `path_stored` onderscheidt "dit pakket nam geen hops" van "we hebben het pad
  niet bewaard", zodat de client dat kan zeggen in plaats van te doen alsof een
  pakket geen hops nam.
- `error` is wat de decoder niet voorbij kwam, of `null`.

404 bij een onbekend id.

### `GET /api/v1/nodes/{prefix}`

Alles wat de site over één node bezit — het paneel achter een bolletje op de live
kaart. `prefix` is 6 tot 64 hextekens en wordt teruggebracht tot zes; een
beheerder met een volledige sleutel in de hand hoort niet te moeten uitzoeken
welke zes tekens de API wil. 422 bij alles wat geen hex is.

In één verzoek beantwoord in plaats van in de vijf waaruit het is samengesteld,
want het paneel opent op een klik, en half gevulde panelen worden gelezen als
"deze node heeft geen buren" lang voordat het laatste antwoord binnen is.

```json
{
  "prefix": "e3d3f4",
  "key_prefix": "e3d3f4d7ed12", "name": "…", "node_type": "repeater",
  "country": "BE", "lat": 50.9, "lon": 5.3, "updated": "…",
  "window": {"days": 7, "oldest": "2026-08-08T09:12:00Z"},
  "repeater": {"slug": "example", "name": "…", "pubkey_prefix": "…",
               "last_seen": "…", "url": "/r/example", "online": true,
               "battery_percentage": 96.0, "uptime": 3.5,
               "neighbor_count": 14,
               "neighbors": [{"prefix": "2ae7af", "name": "…", "snr": -4.25,
                              "last_seen": "…"}],
               "neighbors_capped": true},
  "sent": {"total": 412, "first": "…", "last": "…", "hops_min": 0,
           "observers": [{"prefix": "2ae7c1", "observer": "2ae7c1d40f93",
                          "name": "…", "count": 412, "first": "…", "last": "…",
                          "snr_avg": 5.11, "snr_best": 9.5,
                          "rssi_avg": -91.2, "rssi_best": -72.0,
                          "hops_min": 0, "hops_avg": 0.42}],
           "types": [{"type": "ADVERT", "count": 412}],
           "scopes": [{"scope": "unscoped", "count": 412}]},
  "heard": {"total": 23117, "first": "…", "last": "…", "senders": 84},
  "as_hop": {"packets": 1902, "first": "…", "last": "…", "siblings": 12},
  "neighbor_of": [{"slug": "example", "name": "…", "snr": -4.25,
                   "last_seen": "…", "url": "/r/example"}]
}
```

Lees het met de voorbehouden waar het omheen gebouwd is:

**`window`** is het venster waarin elk cijfer eronder leeft. Beide helften zijn
nodig: de ingestelde bewaartermijn is de belofte, het oudste nog bewaarde pakket
is wat die belofte tot nu toe werkelijk geleverd heeft, en op een server die
gisteren herstartte zijn dat heel verschillende getallen.

**`sent` is een ondergrens, geen totaal.** Het telt alleen wat een ADVERT met een
volledige sleutelprefix aan deze node toeschreef. Al het andere dat hij uitzond
draagt een afzenderhash van één byte die honderden nodes delen. Een boven- en een
ondergrens naast elkaar is overwogen en afgewezen: twee getallen waarvan het
verschil pure dubbelzinnigheid is, nodigen de lezer uit ze te middelen.
`hops_min` en `hops_avg` zijn alleen uit FLOOD, om de reden in
[`database.md`](database.md#hopaantallen-alleen-uit-flood).

**`as_hop` is een plafond, en `siblings` zegt hoeveel van een.** Bij
`siblings == 1` is de telling exact, ook voor het geval van één byte; bij
`siblings == 12` is het een bovengrens, en het paneel heeft het getal dat dat
zegt.

**`heard` ontbreekt, staat niet op nul, als de node geen waarnemer is.** Bijna
geen enkele node is dat, en een regel "0 pakketten gehoord" onder elk bolletje op
de kaart zou lezen als een mesh waarin niets iets hoort.

**`repeater` staat er alleen voor de paar gevolgde repeaters**, en blijft
kerncijfers plus een link: `/r/<slug>` is een volledige pagina met grafieken,
burenhistoriek en instellingen, en een paneel dat er een deel van overdeed zou
een tweede versie van die cijfers zijn om gelijk te houden. `neighbors` is
begrensd op 12 en `neighbors_capped` zegt dat de grens sneed, zodat het paneel
"de beste 12" kan zeggen in plaats van een afgekapte lijst als de hele buurt te
presenteren.

**Velden worden samengevoegd, niet gekozen.** Een node kan meerdere contactrijen
bezitten onder sleutels van verschillende lengte; `_node_identity()` neemt per
veld de eerste niet-lege waarde met de langste sleutel voorop, en de **nieuwste**
`updated` van allemaal.

404 betekent "er is helemaal niets bekend", en dat is iets anders dan "deze node
heeft geen verkeer": een node die alleen ooit zichzelf adverteerde is een prima
antwoord met een leeg `sent`-blok.

## Hopresolutie en haar caches

Een hop oplossen kost een databankopzoeking, en de live feed lost het pad van
elk pakket op dat ze uitreikt — makkelijk een paar honderd opzoekingen per poll
per bezoeker, voor antwoorden die alleen veranderen als een node die we nog nooit
gehoord hebben zich adverteert. `routes_api` houdt daarom twee kortlevende
geheugentjes bij, beide met een TTL van `_HOP_CACHE_TTL_S` = 60 s:

| Geheugen | Sleutel | Bevat |
|---|---|---|
| `_hop_cache` | `(hash, waarnemer, grens)` | De afgewerkte resolutie |
| `_observer_cache` | `waarnemer` | De positie van die waarnemer en zijn ontvangsttabel |

De hopsleutel draagt de waarnemer en de hopgrens naast de hash, want dezelfde
byte lost anders op naargelang wie het pakket hoorde en wat het frame zegt over
hoe ver de node kan liggen. Allebei zijn kleine, herhalende waarden, dus het
geheugen vouwt een feed vol pakketten nog steeds terug tot een handvol
vermeldingen.

`_expire_caches()` laat **beide samen** vervallen, met opzet: een resolutie is
een functie van de waarnemerscontext waaruit ze berekend is, en de een laten
vervallen zonder de ander zou rangschikkingen opdienen die op al ververst bewijs
gebouwd zijn.

`_resolve_src()` en `_resolve_dest()` zijn aparte functies en geen enkele aanroep
met de rol van buitenaf meegegeven. De rol is wat de oplosser vertelt welke kant
het frame de afstand begrenst — een flood begrenst waar het pakket *vandaan*
kwam, een direct waar het *heen* gaat — en die twee omdraaien zou de onschuldigen
uitsluiten. Daarom mag geen enkele beller kiezen.

## Publieke pagina's

| Route | Sjabloon | Inhoud |
|---|---|---|
| `GET /` | `index.html` | Repeaterkaartjes plus de live kaart. Het kaartblok (en Leaflet ermee) blijft volledig uit de pagina als geen enkele node een positie heeft |
| `GET /pakketten` | `packets.html` | Het pakketarchief: zoekbalk, histogram, facetten, sorteerbare tabel, kolomkiezer |
| `GET /r/{slug}` | `repeater.html` | Eén repeater: tegels, grafieken, linkkaart, burentabel, en voor een beheerder de verversknop |

`/r/{slug}` stelt zijn blokken samen uit de indeling in `settings.layout` en
slaat elk blok over dat niets te tonen heeft. Twee benuttingstegels
(`airtime_utilization`, `rx_airtime_utilization`) worden door
`db.computed_utilization()` **berekend** uit de airtimetellers over een venster
van 90 minuten in plaats van van de node gelezen, want het cijfer aan nodezijde
valt terug naar nul bij elke herstart van Home Assistant. Het geeft `null` terug
als het venster korter is dan tien minuten of de teller achteruitliep — een
reset, geen meting.

De verversknop en de weg erachter worden **alleen voor een beheerder** berekend,
en de weg wordt bepaald vóór de knop getekend wordt: een knop die niets kan doen
hoort uitgeschakeld te zijn en te zeggen waarom. Zie
[`commanding.md`](commanding.md).

## Beheerroutes

Alle vragen ze een sessie en controleren ze een CSRF-token. Elke schrijvende
route komt bovendien langs `routes_admin.require_perm()`, dat bij een weigering
403 geeft met de reden in het Nederlands en de poging in het audittrail zet. De
mechanismen staan in [`admin.md`](admin.md); welk recht een route vraagt is de
`action` in zijn `require_perm()`-aanroep.

| Route | Methode | Wat het doet |
|---|---|---|
| `/admin/login` | GET, POST | Inlogformulier en verzending. Begrensd per adres en per gebruikersnaam |
| `/admin/logout` | GET | Wist de sessiecookie |
| `/admin` | GET | Nodes en repeaters, gegroepeerd op beheerniveau |
| `/admin/repeaters/{rid}` | GET | Eén node: identiteit, zichtbaarheid, uitvragen, klok, firmware, verwijderen |
| `/admin/server` | GET | Server en site: toegang, tokens, bewaring, weergave, parameters, kloksync, status |
| `/admin/settings` | POST | Bewaargrenzen en `history_ranges`, elk veld optioneel. Draait een volledige opruimronde zodra er een grens wijzigde — zie [`retention.md`](retention.md#het-instellingenformulier) |
| `/admin/layout` | POST | Blokvolgorde en zichtbaarheid op een repeaterpagina |
| `/admin/cli_params` | POST | De lijst CLI-parameters waar een poller om vraagt |
| `/admin/repeaters/{rid}/refresh` | POST | Nu om een verse status vragen. Met `back=node` terug naar de nodepagina in plaats van de publieke |
| `/admin/repeaters/{rid}/settings` | GET | Omleiding naar `/admin/repeaters/{rid}`, querystring inbegrepen. Blijft bestaan voor bladwijzers |
| `/admin/repeaters/{rid}/settings/refresh` | POST | Nu om een CLI-instellingenronde vragen |
| `/admin/repeaters/{rid}/clocksync` | POST | De klok van deze repeater nu zetten. Zie [`clocksync.md`](clocksync.md) |
| `/admin/repeaters/{rid}/filter` | POST | Eén regel van het pakketfilter. `cmd` plus eventueel `arg1`/`arg2` worden samengevoegd tot de commandoregel die de CLI van de node zelf aanneemt; het gevraagde recht volgt wat de regel blokkeert (`node.filter.gewoon` / `.merkbaar` / `.ingrijpend`). Zie [`packet-filter.md`](packet-filter.md) |
| `/admin/repeaters/{rid}/toggle` | POST | Eén zichtbaarheidsknop omzetten: `what=public` (standaard), `position` of `name`. Met `back=node` terug naar de nodepagina |
| `/admin/repeaters/{rid}/rename` | POST | De weergavenaam wijzigen |
| `/admin/repeaters/{rid}/delete` | POST | De repeater en zijn metingen, actuele waarden en buren verwijderen |
| `/admin/tokens` | POST | Een API-token aanmaken; eenmalig getoond via een cookie van 60 seconden |
| `/admin/tokens/{tid}/revoke` | POST | Een token intrekken |
| `/admin/password` | POST | Je eigen wachtwoord wijzigen. Geeft deze browser een nieuwe cookie |
| `/admin/account` | GET | Mijn account: eigen wachtwoord, je rollen, je eigen auditregels |
| `/admin/audit` | GET | Het volledige audittrail. `?n=` zet het aantal regels |
| `/admin/users` | POST | Een account aanmaken. Het wachtwoord gaat gehasht binnen en is nooit terug te lezen |
| `/admin/users/{uid}/password` | POST | Een wachtwoord zetten voor iemand anders, zonder het oude te kennen |
| `/admin/users/{uid}/flags` | POST | Serverbeheerder ja/nee, uit ja/nee. Weigert bij de laatste actieve serverbeheerder |
| `/admin/users/{uid}/delete` | POST | Een account verwijderen. Zijn auditregels blijven |
| `/admin/groups` | POST | Een gebruikersgroep (`soort=user`) of nodegroep (`soort=node`) aanmaken |
| `/admin/groups/{soort}/{gid}/delete` | POST | Een groep verwijderen, met de toekenningen die erop stonden |
| `/admin/groups/{soort}/{gid}/members` | POST | Eén lid toevoegen of weghalen |
| `/admin/grants` | POST | Een rol toekennen, of weigeren. Weigert een combinatie die niet in het model past |
| `/admin/grants/{grant_id}/delete` | POST | Een toekenning intrekken |

De twee "vraag nu iets"-routes lopen allebei via
`routes_admin._dispatch()`, die **elke openstaande weg bewandelt in plaats van de
eerste de beste**: de MQTT-weg bereikt de node zelf en alleen zolang die aan de
broker hangt, de pollerwachtrij bereikt een client die de repeater over LoRa
uitvraagt en werkt ook met de WiFi van de node uit. Ze geeft `mqtt`, `queued`,
`both` of `none` terug, en de pagina zegt welke — niet wat we hoopten dat er zou
gebeuren. De uitkomst reist mee in de querystring van de omleiding.

## Verwante documenten

| Vraag | Document |
|---|---|
| Wat er in de tabellen achter deze antwoorden staat | [`database.md`](database.md) |
| De volledige zoektaal | [`search.md`](search.md) |
| De resolutietoestanden in `src`, `dest` en `path` | [`candidates.md`](candidates.md) |
| Wat de decoder wel en niet kan concluderen | [`decoder.md`](decoder.md) |
