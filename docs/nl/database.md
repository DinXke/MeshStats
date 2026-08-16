# Het datamodel

*[English](../database.md)*

Elke tabel in `server/app/db.py`, elke kolom, wat erin staat en waarom. Het
schema staat in de constante `SCHEMA` bovenaan dat bestand; de later toegevoegde
kolommen staan er vlak onder in `COLUMN_MIGRATIONS`.

## Waarom kaal sqlite3

Met opzet `sqlite3` met één verbinding op moduleniveau en een mutex in plaats van
een ORM. De belasting is een handvol kleine schrijfacties per minuut plus
paginaleesacties, dus een ORM zou een afhankelijkheid en een migratieverhaal
toevoegen die niets opleveren. Het schema wordt bij elke verbinding met
`CREATE TABLE IF NOT EXISTS` toegepast, wat meteen het migratiemechanisme voor
nieuwe tabellen is.

Verbindingsinstellingen, één keer gezet in `db.get_conn()`:

```sql
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;
```

WAL is de reden dat een back-up via `.backup` moet lopen en niet via een
bestandskopie — zie [`deployment.md`](deployment.md#back-up).

## Migraties

SQLite kent geen `ADD COLUMN IF NOT EXISTS`, dus een nieuwe kolom op een
bestaande tabel vraagt de expliciete controle in `db._migrate()`: lees
`PRAGMA table_info(<tabel>)` en voeg toe wat ontbreekt. Een levende databank
weggooien is hier geen optie.

`COLUMN_MIGRATIONS` is een lijst van `(tabel, kolom, declaratie)` en is
**uitsluitend aanvullend**. Er wordt niets van type veranderd of verwijderd;
een kolom die verkeerd bleek, wordt vervangen door een nieuwe in plaats van
gewijzigd. Zo blijft de lijst afspeelbaar vanaf elke ouderdom van databank, op
volgorde, zonder versienummer om bij te houden.

| Tabel | Kolom | Type | Toegevoegd voor |
|---|---|---|---|
| `repeaters` | `source_prefix` | TEXT | Welke node deze statistieken publiceerde |
| `repeaters` | `source_seen` | TEXT | Wanneer die dat het laatst deed |
| `repeaters` | `fw` | TEXT | MeshCore-versie van het laatste bericht |
| `repeaters` | `fw_meshmanager` | TEXT | De versie van onze eigen module op die node |
| `repeaters` | `topic_prefix` | TEXT | Op welk MQTT-topicvoorvoegsel deze node zich meldt |
| `repeaters` | `show_position` | INTEGER NOT NULL DEFAULT 1 | Of bezoekers de positie van deze node zien |
| `repeaters` | `show_name` | INTEGER NOT NULL DEFAULT 1 | Of bezoekers de naam van deze node zien |
| `packets` | `path` | TEXT | Hophashes, komma-gescheiden |
| `packets` | `raw` | TEXT | Het frame zoals het van de radio kwam, hex |
| `contacts` | `country` | TEXT | ISO 3166-1 alpha-2, of NULL |
| `packets` | `scope` | TEXT | `unscoped` / `scoped` / `share` |
| `packets` | `scope_codes` | TEXT | De twee transportcodes, komma-gescheiden |
| `packets` | `src_hash` | TEXT | Afzenderhash van 1 byte, twee hextekens |
| `packets` | `dest_hash` | TEXT | Bestemmingshash van 1 byte, twee hextekens |

**De twee zichtbaarheidskolommen staan standaard op 1, en die standaard is het
ontwerp.** `ALTER TABLE ADD COLUMN` geeft bestaande rijen de opgegeven
standaardwaarde, dus een databank die deze kolommen er bij een upgrade bij
krijgt, toont precies wat ze de dag ervoor toonde. Een privacykolom die een
repeater 's nachts stilzwijgend van de kaart haalt, is een ergere fout dan de
ontbrekende kolom die ze oploste. `is_public` blijft ongemoeid: die beantwoordt
een andere vraag ("staat deze node überhaupt op de site") en de drie samen zijn
één keuze met drie antwoorden. Zie [`privacy.md`](privacy.md).

`fw` en `fw_meshmanager` worden bewaard en niet alleen getoond, want ze bepalen
of de site een node überhaupt iets mag vragen: opdrachten aannemen op het
MQTT-`cmd`-topic begint bij nodefirmware 1.8.0, en een knop die op iets ouders
in het niets publiceert is precies de oneerlijkheid waarvoor die kolommen
bestaan. Zie [`commanding.md`](commanding.md).

### De ene hernoeming

`COLUMN_RENAMES` is de uitzondering op "uitsluitend aanvullend", en er staat
precies één regel in: `repeaters.fw_meshstats` werd `fw_meshmanager`, met een
echte `ALTER TABLE ... RENAME COLUMN`. Hernoemen en niet twee kolommen met
dezelfde betekenis naast elkaar laten bestaan, want die worden vroeg of laat
allebei half gevuld — en het kan *hier* veilig omdat deze kolom bij **elk**
statistiekbericht opnieuw geschreven wordt (`record_firmware()`). Zelfs wie na
deze migratie terugrolt naar de vorige versie van de site, krijgt de oude kolom
weer aangemaakt en bij de eerstvolgende publicatie van elke node weer gevuld.
Voor een kolom met geschiedenis erin zou het niet mogen.

`_migrate()` hernoemt **vóór** het toevoegt. Andersom maakt de aanvullende ronde
eerst een lege `fw_meshmanager` aan, stuit de hernoeming dan op een naam die al
bestaat, en blijven de oude waarden liggen.

De payloadsleutel wordt in beide spellingen aanvaard
(`db.payload_module_version()`), om dezelfde reden als de omgevingsvariabelen:
de server en de nodes gaan nooit op dezelfde dag om.

### De ene view

`db.VIEWS` bevat één view, `visible_contacts`, aangemaakt door `_migrate()`
**nadat** de kolommen bestaan:

```sql
CREATE VIEW visible_contacts AS
SELECT c.prefix, c.prefix6, c.node_type, c.updated,
       CASE WHEN v.show_name = 0
            THEN '0x' || upper(substr(c.prefix6, 1, 2)) ELSE c.name END AS name,
       CASE WHEN v.show_position = 0 THEN NULL ELSE c.lat END AS lat,
       …
FROM contacts c LEFT JOIN <zichtbaarheid per sleutelprefix> v ON v.p6 = c.prefix6;
```

Het is `contacts` met de naam, de positie en het land door de zichtbaarheid van
de gevolgde repeater achter die sleutelprefix. **Elke publieke leesweg
selecteert uit de view; elke ingestweg (`upsert_advert()`, `upsert_contacts()`,
`set_country()`) schrijft nog steeds naar de tabel.** Wat de site weet verandert
niet — alleen wat ze vertelt.

Een verborgen positie wordt NULL, en dat is met opzet dezelfde waarde als "nooit
een positie van deze node gehoord". Die toestand was overal al afgehandeld, dus
er staat geen tweede mechanisme naast het eerste dat ermee gelijk moet blijven.
Een verborgen naam wordt de adreshash en geen NULL, want NULL zou de bestaande
terugval op `prefix.upper()` in gang zetten en dan stond er alsnog een
identiteit.

De zichtbaarheidskant is een gegroepeerde subquery en geen rechtstreekse join op
`repeaters`: `pubkey_prefix` is uniek, maar twee sleutels kunnen in hun eerste
zes hextekens samenvallen, en dan zou een rechtstreekse join elke contactrij
verdubbelen — dezelfde node kreeg twee bolletjes in `located_nodes()`. `MIN()`
kiest bij zo'n botsing de striktste keuze, en dat is de enige richting waarin
het fout mag gaan.

De view wordt bij elke migratie weggegooid en opnieuw gemaakt in plaats van
afgeschermd met `IF NOT EXISTS`: SQLite bewaart de tekst waarmee een view
gemaakt is, dus een databank die al een oudere versie draaide zou anders voor
altijd de oude definitie houden. Er zitten geen gegevens in, dus opnieuw
aanmaken kost niets.

De twee wegen die een view niet kan bereiken, zijn `repeaters.name` (staat
helemaal niet in `contacts`) en `neighbors.name` (die er normaal van wint). Ze
worden afgehandeld door respectievelijk `db.public_name()` en
`db.NEIGHBOR_NAME_SQL`. Zie [`privacy.md`](privacy.md).


## `packets.raw` is de grondwaarheid

Al de rest in een pakketrij is een samenvatting met verlies. `raw` is het
volledige verslag, en de regel die daaruit volgt is:

> De afgeleide kolommen zijn cache. `raw` beslist.

Twee gevolgen.

**Bijvullen bij het opstarten.** `db._backfill_from_raw()` draait binnen
`get_conn()` en decodeert elke rij opnieuw waar `scope IS NULL OR src_hash IS
NULL` en `raw IS NOT NULL`. Zonder dat zou een nieuw toegevoegde kolom leeg
blijven tot de hele tabel ververst was, en zou het archief een week streepjes
tonen naast rijen waarvan het antwoord er pal naast lag.

Het is zelfbegrenzend: elke rij die het aanraakt krijgt een **niet-NULL**
`src_hash` — de lege string als het pakkettype er geen draagt — dus de tweede
start vindt niets meer te doen. Die lege string als markering is de hele reden
dat de ronde niet bij elke boot elke ACK en advert opnieuw decodeert, op zoek naar
een hash die er nooit was. Rijen ouder dan de kolom `raw` houden voorgoed NULL,
en dat is het eerlijke antwoord voor een pakket waarvan niemand de bytes bewaard
heeft.

**Het detailendpoint decodeert opnieuw in plaats van te lezen.**
`GET /api/v1/packets/{id}` haalt de *huidige* decoder over `raw` voor de
advertvelden, de scope en `path_hash_size`, en valt alleen terug op de bewaarde
kolommen voor rijen waarvan het frame niet bewaard is. Een verbetering in
`packets.py` verbetert dus ook pakketten die er al staan, niet alleen nieuwe.

`MAX_RAW_HEX_STORED` = 600 tekens begrenst wat er weggeschreven wordt. Een
MeshCore-frame is hoogstens 255 bytes, dus 510 hextekens plus speling is al
royaal; de grens bestaat alleen zodat een onzinnige payload geen megabyte per rij
kan opslaan.

## Tabel voor tabel

### `repeaters`

De gevolgde repeaters — die met een pagina op `/r/<slug>`.

| Kolom | Type | Inhoud |
|---|---|---|
| `id` | INTEGER PK | Interne id, waarnaar `latest`, `samples`, `neighbors` en `repeater_cli` verwijzen |
| `slug` | TEXT UNIQUE | URL-deel, uit `db.slugify(name)` met `-2`, `-3`… bij botsing |
| `pubkey_prefix` | TEXT UNIQUE | Publieke-sleutelprefix, kleine hex. Groeit mee naar de langste ooit geziene lengte |
| `name` | TEXT | Weergavenaam. Overgenomen uit een binnenkomend bericht als hij verandert |
| `is_public` | INTEGER | 1 = zichtbaar op de site en in de publieke API. Om te zetten in `/admin`. Wie vanzelf ontstaat krijgt 0; de kolomstandaard blijft 1 voor rijen die anders ontstaan |
| `show_position` | INTEGER | 1 = bezoekers zien de positie van deze node. 0 laat de site zich gedragen alsof ze er nooit een gehoord heeft |
| `show_name` | INTEGER | 1 = bezoekers zien `name`. 0 vervangt hem overal publiek door de adreshash `0xNN` |
| `sort_order` | INTEGER | Volgorde op de startpagina en in `/admin` |
| `last_seen` | TEXT | Tijdstip van de laatste momentopname, geschreven door `db.ingest()` |
| `created_at` | TEXT | Wanneer de rij is aangemaakt |
| `source_prefix` | TEXT | Sleutel van de node die de laatste statistieken publiceerde, of letterlijk `api` voor de HTTP-weg |
| `source_seen` | TEXT | Wanneer die node het laatst publiceerde |
| `fw` | TEXT | MeshCore-firmwareversie |
| `fw_meshmanager` | TEXT | De versie van onze eigen firmwaremodule |

**Sleutels vergelijken is geen stringgelijkheid.** Bronnen zijn het oneens over
hoeveel van de sleutel ze sturen — Home Assistant 5 bytes, de eigen firmware van
een node 6 — en matchen op de string alleen registreerde ooit één node twee keer
en spleet zijn historiek middendoor. `db._find_by_prefix()` aanvaardt daarom een
match in beide richtingen zolang de kortste sleutel een prefix is van de langste
**en minstens `MIN_PREFIX_MATCH` (8) hextekens telt**; daaronder kunnen twee
verschillende sleutels toevallig samenvallen. Is de binnenkomende sleutel langer
dan de opgeslagene, dan vervangt hij die, want de langste geziene sleutel is de
minst dubbelzinnige.

`db.find_repeater()` is de publieke deur naar die match, voor bellers die willen
vragen "zijn deze twee sleutels dezelfde node?" in plaats van "geef me een rij".

**`record_source()` en `record_firmware()`** zijn met opzet aparte schrijfacties.
`record_firmware()` overschrijft alleen wat het bericht werkelijk noemde: Home
Assistant leest de MeshCore-versie van een repeater van het mesh af en weet niet
of onze eigen module erop staat, dus het mag de andere niet kunnen wissen door
erover te zwijgen.

### `latest`

Eén rij per `(repeater, metriek)`, de huidige waarde. Waaruit de tegels
gerenderd worden, en de reden dat een startpagina zonder netwerk geserveerd kan
worden.

| Kolom | Type | Inhoud |
|---|---|---|
| `repeater_id` | INTEGER | FK naar `repeaters`, `ON DELETE CASCADE` |
| `metric` | TEXT | Metrieknaam zoals de node hem stuurde |
| `ts` | TEXT | Tijdstip van de meting |
| `value` | REAL | Numerieke waarde, of NULL |
| `value_str` | TEXT | Tekstwaarde, voor metrieken die geen getal zijn |

Primaire sleutel `(repeater_id, metric)`. Een boolean komt binnen als
`1.0`/`0.0`; alles wat niet naar een float om te zetten is, wordt als tekst
bewaard, afgekapt op 255 tekens.

### `samples`

De tijdreeks in SQLite. `WITHOUT ROWID`, primaire sleutel
`(repeater_id, metric, ts)`.

| Kolom | Type | Inhoud |
|---|---|---|
| `repeater_id` | INTEGER | Welke repeater |
| `metric` | TEXT | Metrieknaam, of `neighbor_<prefix>` voor een SNR-reeks per link |
| `ts` | TEXT | Tijdstip |
| `value` | REAL | De meting |

**Met VictoriaMetrics ingesteld komt hier niets meer in**, behalve tijdens een
storing — zie
[`server.md`](server.md#twee-regels-waarvoor-de-module-bestaat). Het is het
vangnet, geen dood gewicht, en het is wat de overstap omkeerbaar maakt.

Zonder tijdreeksdatabank geldt de oude regel, in `db.ingest()`: een waarde komt
in `samples` als ze **veranderde**, of als de laatste **bewaarde** meting ouder is
dan `heartbeat_min`. Beoordeeld op de laatste bewaarde meting en niet op de
laatste ingest, zodat een stabiele metriek zijn hartslagpunt op tijd krijgt in
plaats van nooit. `force=True` — een handmatige statusverversing — schrijft
altijd.

Uitgeweken punten worden op **volledige resolutie** geschreven
(`db.spill_samples()`). Juist de punten uitdunnen die alleen bestaan omdat de
primaire opslag onbereikbaar was, zou het nut van een vangnet tenietdoen.

### `neighbors`

De eigen burentabel van één repeater, zoals de repeater hem meldde.

| Kolom | Type | Inhoud |
|---|---|---|
| `repeater_id` | INTEGER | FK naar `repeaters`, `ON DELETE CASCADE` |
| `prefix` | TEXT | De sleutelprefix van de buur, 6 hextekens |
| `name` | TEXT | Naam zoals de repeater die kent, mag NULL zijn |
| `snr` | REAL | Signaal-ruisverhouding van de link |
| `last_seen` | TEXT | Absoluut tijdstip |

Primaire sleutel `(repeater_id, prefix)`. Wordt na een vaste 7 dagen opgeruimd.

De node meldt `seen_min` — minuten sinds hij die buur voor het laatst hoorde — en
`db.ingest()` rekent dat om naar een absoluut tijdstip tegen de `ts` van de
momentopname zelf. Bij de upsert worden `name` en `snr` ge-`COALESCE`d, zodat een
melding die er een weglaat hem niet wist.

Dit is de enige relatie op de hele site die een **meting door een node** is en
geen gevolgtrekking van de server: de repeater zette de regel in zijn eigen
tabel, sleutel en SNR inbegrepen.

### `contacts`

Alles wat de site weet over de identiteit van een node: naam, positie, type.
Gevoed door adverts (`db.upsert_advert()`, aangeroepen vanuit
`db.insert_packet()`) en door `POST /api/v1/contacts`.

| Kolom | Type | Inhoud |
|---|---|---|
| `prefix` | TEXT PK | De sleutelprefix zoals de bron hem stuurde — 10 of 12 hextekens |
| `prefix6` | TEXT | De eerste 6 tekens daarvan. **De koppelsleutel overal elders** |
| `name` | TEXT | Nodenaam |
| `lat`, `lon` | REAL | Positie in graden, of NULL |
| `node_type` | TEXT | `chat`, `repeater`, `room` of `sensor` |
| `updated` | TEXT | Wanneer deze rij het laatst geschreven is |
| `country` | TEXT | ISO 3166-1 alpha-2, of NULL |

Index: `idx_contacts_p6` op `prefix6`.

**Eén node kan meerdere rijen bezitten.** De primaire sleutel is de letterlijke
prefix, en twee bronnen sturen prefixen van verschillende lengte, dus dezelfde
node komt twee keer binnen. Daarom koppelt elke andere tabel op `prefix6`,
daarom geeft `db.node_contacts()` een *lijst* terug met de langste sleutel eerst,
en daarom voegt `routes_api._node_identity()` die rijen veld voor veld samen in
plaats van er een te kiezen — de ene bron kan de naam kennen en de andere de
positie.

Elk veld wordt bij de upsert ge-`COALESCE`d, want adverts komen veel vaker binnen
dan ze veranderen, en een node kan zijn naam adverteren zonder positie of
omgekeerd. Een naamloze advert mag een bekende naam niet wissen.

`upsert_advert()` hergebruikt bovendien de `prefix` van een bestaande rij als er
al een is onder dezelfde `prefix6`, zodat beide bronnen naar één rij blijven
convergeren in plaats van naar twee die elkaar overschaduwen.

### `repeater_cli`

De CLI-configuratie van een repeater, zoals uitgelezen over LoRa of over MQTT.

| Kolom | Type | Inhoud |
|---|---|---|
| `repeater_id` | INTEGER | FK naar `repeaters`, `ON DELETE CASCADE` |
| `param` | TEXT | Parameternaam, hoogstens 64 tekens |
| `value` | TEXT | Het antwoord, hoogstens 4000 tekens, of **NULL voor "gevraagd, geen antwoord"** |
| `updated` | TEXT | Wanneer deze rij geschreven is |

Primaire sleutel `(repeater_id, param)`.

`db.upsert_cli_settings()` neemt een `prune`-vlag, en het verschil is niet
academisch:

- **`prune=True`** (een volledige heruitlezing via Home Assistant): rijen die
  deze push niet noemde *én* die de ingestelde lijst niet noemt, worden
  verwijderd, zodat een parameter die niet meer bestaat verdwijnt.
- **`prune=False`** (de eigen dagelijkse ronde van een node, en de ronde van een
  monitor over LoRa): een ontbrekende parameter betekent "deze keer geen
  antwoord", niet "weg". De ingestelde lijst noemt de regioparameter
  `cmd:region` — hij wordt als letterlijk CLI-commando opgehaald — terwijl hij
  onder `region` bewaard wordt, dus snoeien zou hem wissen bij de eerste ronde
  die hem mist.

`DEFAULT_CLI_PARAMS` is de lijst waar de *pollende* weg om vraagt. Een node die
zijn eigen CLI leest, werkt uit zijn eigen tabel (`SET_PARAMS` in de firmware) en
ziet deze nooit; houd de twee gelijk, of een parameter bestaat voor de ene soort
node en ontbreekt voor de andere. Een `cmd:`-voorvoegsel betekent "stuur dit als
letterlijk CLI-commando" in plaats van er `get ` voor te zetten.

NULL wordt met opzet bewaard en als "(geen antwoord)" getoond. Ja, dat
overschrijft een waarde die een eerdere ronde wél kreeg — en het alternatief is
afgewezen om een reden: een repeater waarvan de monitor alleen-lezen inlogt,
beantwoordt helemaal geen CLI-commando, en met waarden uit maart nog op het
scherm en alleen een tijdstempel dat opschoof, zou niemand dat ooit vinden.

### `packets`

Eén rij per ontvangst. Geschreven door `db.insert_packet()` vanuit de
MQTT-`rx`-weg.

| Kolom | Type | Inhoud |
|---|---|---|
| `id` | INTEGER PK | Oplopend; de `since_id`-cursor van de live feed |
| `ts` | TEXT | Ontvangsttijd van de server. Het veld `t` van de node is een uptimeteller, geen wandklok |
| `observer` | TEXT | Sleutelprefix van de node die het hoorde, kleine letters, ≤ 16 tekens |
| `snr` | REAL | Signaal-ruisverhouding zoals de radio hem meldde |
| `rssi` | REAL | Ontvangen signaalsterkte |
| `len` | INTEGER | Framelengte in bytes |
| `route` | TEXT | `TRANSPORT_FLOOD`, `FLOOD`, `DIRECT` of `TRANSPORT_DIRECT` |
| `payload_type` | INTEGER | 0–15, uit de headerbyte |
| `payload_name` | TEXT | `ADVERT`, `TXT_MSG`, `ACK`… of `TYPE<n>` voor een onbekende |
| `path_len` | INTEGER | Aantal hophashes in het pad |
| `sender` | TEXT | Sleutelprefix van 6 hextekens — **alleen ooit gevuld vanuit een ADVERT** |
| `phash` | TEXT | Payloadhash van 16 hextekens, voor ontdubbeling |
| `path` | TEXT | De hophashes, komma-gescheiden |
| `raw` | TEXT | Het volledige frame, hex |
| `scope` | TEXT | `unscoped`, `scoped` of `share` |
| `scope_codes` | TEXT | De twee transportcodes als decimale getallen, komma-gescheiden. NULL bij een ongescoopt pakket |
| `src_hash` | TEXT | Afzenderhash van 1 byte. `''` = gedecodeerd, dit type heeft er geen |
| `dest_hash` | TEXT | Bestemmingshash van 1 byte. `''` = gedecodeerd, dit type heeft er geen |

Indexen:

| Index | Kolommen | Bedient |
|---|---|---|
| `idx_packets_ts` | `ts` | Opruimrondes en zoekopdrachten in een tijdvenster |
| `idx_packets_dup` | `observer, phash, ts` | Dubbelenopzoeking, opruiming, en de waarnemerskant van het nodepaneel |
| `idx_packets_sender` | `sender` | "Alles wat deze node stuurde", per geopende node |

`idx_packets_sender` is goedkoop om mee te dragen: de kolom is zes hextekens en
NULL op de meeste rijen, want alleen een advert noemt zijn afzender voluit. De
waarnemerskant heeft geen eigen index nodig — `idx_packets_dup` begint al met die
kolom, en dat is de reden dat `node_reception_summary()` een *bereik* op
`observer` vraagt (`>= p6 AND < p6 + 'g'`; hex loopt 0–9a–f, dus `g` sorteert na
elke sleutel die met die zes tekens begint) in plaats van het in `substr()` te
wikkelen, een expressie die geen index kan bedienen.

**Ontdubbelen.** Een geflood pakket wordt door elke node in bereik herhaald, dus
dezelfde waarnemer hoort dezelfde payload binnen seconden meerdere keren.
`insert_packet()` weigert een rij waarvan `(observer, phash)` al binnen
`PACKET_DUP_WINDOW_S` (60 s) voorkwam. De hash gaat alleen over de payload en
niet over het hele frame, want een geflood pakket krijgt er bij elke hop
padhashes en transportcodes bij — zie [`decoder.md`](decoder.md).

**`sender` is de strengste kolom in het schema.** Hij wordt alleen gevuld als de
decoder een ADVERT vond, want een advert is de enige payload die zijn oorsprong
met een volledige sleutelprefix noemt. Al het andere dat deze node ooit uitzond,
draagt een afzenderhash van één byte die honderden bekende nodes delen. Die
meetellen zou een groter, vriendelijker getal opleveren dat deels het verkeer van
iemand anders is, en de API zegt dat in plaats van een totaal te presenteren als
"al zijn pakketten".

### `settings`

Sleutel-waardeopslag voor alles wat de beheerpagina kan wijzigen, plus wat
boekhouding. `db.get_setting()` / `db.set_setting()` / `db.setting_int()`.

| Sleutel | Gezet door | Inhoud |
|---|---|---|
| `heartbeat_min` | Instellingenformulier in `/admin` | Minuten; begrensd op 1–1440 |
| `retention_days` | Instellingenformulier in `/admin` | Bewaartermijn voor metingen; begrensd op 1–3650 |
| `packet_retention_days` | Instellingenformulier in `/admin` | Bewaartermijn voor pakketten, 1–365; ook het venster van de heatmap |
| `packet_max_rows` | Instellingenformulier in `/admin` | FIFO-bovengrens op de pakkettentabel |
| `db_max_mb` | Instellingenformulier in `/admin` | FIFO-bovengrens op het databankbestand, WAL inbegrepen |
| `prune_last` | `retention.run_once()` | JSON: de laatste opruimronde voluit — zie [`retention.md`](retention.md#wat-een-ronde-rapporteert) |
| `history_ranges` | Instellingenformulier in `/admin` | Komma-gescheiden uurwaarden voor de bereikkiezer van de grafieken |
| `layout` | Indelingsformulier in `/admin` | JSON: blokvolgorde en zichtbaarheid op een repeaterpagina |
| `cli_params` | Instellingenpagina van een repeater | Komma-gescheiden lijst CLI-parameters |
| `refresh_requests` | `db.request_refresh()` | JSON `{prefix: ts}` — statusverzoeken die op een poller wachten |
| `settings_requests` | `db.request_settings()` | JSON `{prefix: {ts, params}}` — CLI-opvragingen die op een poller wachten |
| `settings_delivered` | `db.pop_settings_requests()` | JSON `{prefix: ts}` — wanneer een opvraging uitgereikt is. Begrensd op 200 sleutels |
| `poller_seen` | `GET /api/v1/commands` | Wanneer een poller de wachtrij het laatst leegde |
| `clocksync_high_water` | `clocksync._backwards_check()` | Hoogste wandkloktijd die deze site ooit zag |
| `clocksync_sent` | `clocksync._record_sent()` | JSON `{node: epoch}` — laatste tijdssynchronisatie per node, begrensd op 50 sleutels |

Een instelling die hier staat **gaat vóór de omgevingsvariabele** met dezelfde
betekenis, en dat is de reden dat een bewaartermijn verhogen in `/admin` geen
herstart vraagt.

De twee wachtrijen worden **bij het lezen gewist**, en die vorm is de reden dat
`settings_delivered` überhaupt bestaat. Zodra een poller een verzoek heeft
meegenomen, is er geen spoor meer van, dus "de poller nam het mee en de repeater
zweeg" en "er is nooit iemand komen halen" zouden er identiek uitzien.
`pending_settings_request()` beantwoordt de tweede, `settings_delivered_at()`
samen met de ouderdom van de bewaarde waarden de eerste, en `poller_last_seen()`
onderscheidt het derde geval: er stond niemand klaar om het op te halen.

### `tokens`

API-tokens voor de HTTP-ingestweg.

| Kolom | Type | Inhoud |
|---|---|---|
| `id` | INTEGER PK | |
| `name` | TEXT | Label in `/admin` |
| `token_hash` | TEXT UNIQUE | SHA-256 van het token. Het token zelf wordt nooit bewaard |
| `created_at` | TEXT | |
| `last_used` | TEXT | Geschreven bij elke geslaagde `check_token()` |
| `revoked` | INTEGER | 1 verbergt de rij en weigert het token |

Het token is `mcs_` + `secrets.token_urlsafe(32)` en wordt één keer getoond, via
een cookie van 60 seconden in plaats van via een URL. Intrekken is een vlag en
geen verwijdering, zodat `last_used` de intrekking overleeft.

### `admins`

| Kolom | Type | Inhoud |
|---|---|---|
| `id` | INTEGER PK | |
| `username` | TEXT UNIQUE | |
| `pw_hash` | TEXT | `pbkdf2$<salt hex>$<afgeleide sleutel hex>`, SHA-256, 200 000 rondes |

Er is geen sessietabel. De kolom `pw_hash` *is* de intrekkingslijst: elke
sessiecookie draagt een korte HMAC-vingerafdruk ervan, zodat een
wachtwoordwijziging elke cookie ongeldig maakt die onder het oude wachtwoord
geslagen is. Zie [`admin.md`](admin.md#sessies).

## De queryhulpjes die het waard zijn te kennen

### `GROUP BY p.id`, overal waar pakketten contacten ontmoeten

`recent_packets()`, `packets_with_paths()`, `packet_by_id()` en
`search_packets()` koppelen `contacts` twee keer — een keer voor de afzender, een
keer voor de waarnemer — en sluiten alle vier af met `GROUP BY p.id`. Zonder dat
komt één pakket één keer per passende contactrij terug, want één node kan er
meerdere bezitten. Bij een tellende query zou dat de getallen stilzwijgend
verdubbelen, en dat is de reden dat `node_sent_by_observer()` de naam van de
waarnemer met een gecorreleerde subquery ophaalt in plaats van met een derde
join.

### De twee regimes van `recent_packets()`

Eén oplopende afspraak, twee gedragingen:

- `since_id > 0` — alles nieuwer dan die id, oudste eerst, zodat de poller in
  volgorde van aankomst aanvult.
- `since_id = 0` — de **nieuwste** `limit` pakketten, ook oudste eerst
  teruggegeven. Vroeger gaf dit de oudste bewaarde pakketten ("alles na id 0"),
  waardoor een herladen pagina opende op verkeer van uren geleden en per poll een
  pagina richting nu kroop. Een eerste blik op een live feed hoort te tonen wat er
  gebeurt, niet wat er het eerst gebeurde.

De nieuwste-eerst-ophaling wordt in Python omgekeerd in plaats van via een
geneste SELECT die twee keer sorteert: hoogstens `limit` rijen omkeren die al in
het geheugen staan kost niets.

### Hopaantallen alleen uit FLOOD

`observer_receptions()` en `node_sent_by_observer()` beperken hun hopstatistiek
allebei tot `route LIKE '%FLOOD'`. Bij een FLOOD is `path_len` de reeds afgelegde
route, wat de afstand van die node tot de waarnemer is; bij een DIRECT is het de
route die nog komt. De twee in één `MIN()` mengen zou een node als buur melden op
grond van een pakket dat alleen maar bijna klaar was. Zie
[`protocol.md`](protocol.md#14-het-path-veld) §1.4.

`observer_receptions()` gebruikt bovendien **alleen adverts** — rijen waar
`sender` niet NULL is — want een advert noemt zijn afzender met een volledige
sleutelprefix. Het is de bewijstabel waartegen [`candidates.md`](candidates.md)
weegt, en dubbelzinnige gegevens voeren aan wat dubbelzinnigheid moet oplossen,
zou een cirkel zijn.

### Het plafond dat zegt dat het een plafond is

`node_hop_appearances()` telt hoe vaak de sleutelprefix van een node als hop in
het pad van iemand anders opduikt. Een padvermelding is 1, 2 of 3 bytes van een
sleutel, dus alle drie de breedtes worden geprobeerd, en de kortste ervan noemt
één byte die honderden nodes onvermijdelijk delen. `node_hash_siblings()` telt
hoeveel bekende nodes die eerste byte delen, zodat het paneel de
dubbelzinnigheid *naast* het getal kan zetten in plaats van erachter. Bij
`siblings == 1` is de telling exact.

Het is onvermijdelijk een volledige scan — de hoplijst is één komma-gescheiden
kolom en geen index kan een lidmaatschapstest daarop beantwoorden — begrensd door
de bewaartermijn van pakketten en uitgevoerd per geopende node. `path` in een
eigen tabel splitsen is overwogen en afgewezen: dat zou een insert per hop bij
elke ontvangst kosten, op het hete pad, om een klik te versnellen. De komma's aan
beide kanten van de `LIKE` maken het een match op de hele vermelding; zonder die
zou de hop `2a` ook op de vermelding `2ae7` passen.

### Waarom het archief geen extra indexen heeft

`search_packets()` legt een meting vast, geen vermoeden. De twee LEFT JOINs en de
`GROUP BY` dwingen de query al naar een tijdelijke B-boom voor zijn `ORDER BY`,
zelfs bij de standaardvolgorde op de geïndexeerde kolom `ts` — dus een index op
`path_len` of `snr` zou daar helemaal niet gebruikt kunnen worden. Gemeten op
50 000 pakketten (ongeveer zeven keer een drukke week) kost één pagina 43–70 ms
op welke kolom er ook gesorteerd wordt, tegen 52 ms voor de volgorde die er al
was. Vier extra indexen zouden elke insert op de ingestweg vertragen voor een
verschil dat er niet is.

## Verwante documenten

| Vraag | Document |
|---|---|
| Hoe de delen samenhangen | [`server.md`](server.md) |
| Welk endpoint welke tabel leest | [`api.md`](api.md) |
| De zoektaal over `packets` | [`search.md`](search.md) |
| Wat de decoderkolommen van `packets` vult | [`decoder.md`](decoder.md) |
