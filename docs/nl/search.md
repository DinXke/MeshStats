# De zoektaal

*[English](../search.md)*

De zoekbalk op `/pakketten` spreekt een kleine taal in Kibana-stijl.
`server/app/search.py` is het geheel ervan: er is geen tweede, verborgen
regelset elders, en elk teken SQL dat eruit komt, komt uit de eigen tabellen van
die module.

## De ene belofte

> **Er wordt nooit stilzwijgend iets weggelaten.**

Een onbekende veldnaam, een vergelijking op een tekstkolom, een ondeugdelijk
bereik, een omgekeerd bereik, een niet-gesloten aanhalingsteken, een onmogelijke
sortering: elk daarvan is een fout die de pagina toont, nooit een clausule die
stilletjes overgeslagen wordt. Een zoekfunctie die de helft van wat je vroeg
negeert en ondertussen een stellig aantal treffers meldt, is erger dan een die
weigert te draaien.

Fouten komen binnen als een 200 met een `error`-tekst op
`GET /api/v1/packets/search` — zie
[`api.md`](api.md#get-apiv1packetssearch) voor waarom dat geen 4xx is.

## Vormen

| Vorm | Voorbeeld | Betekenis |
|---|---|---|
| `veld:waarde` | `type:ADVERT` | Exacte match, hoofdletterongevoelig |
| meerdere clausules | `type:ADVERT scope:scoped` | Verbonden met AND |
| joker, begint met | `sender:2ae7*` | Alleen tekstvelden |
| joker, eindigt op | `name:*circuit` | Alleen tekstvelden |
| joker, bevat | `name:*circuit*` | Alleen tekstvelden |
| vergelijking | `snr:>5`, `rssi:<=-100` | Alleen numerieke velden |
| bereik | `len:20..40` | Aan beide kanten inclusief, alleen numerieke velden |
| aanhalingstekens | `name:"BE-XXX-Example.VIR"` | Voor een waarde met spaties erin |
| uitsluiting | `-type:ACK`, `NOT type:ACK` | Beide schrijfwijzen |
| één veld, meerdere waarden | `type:(ADVERT OR TXT_MSG)` | OR binnen één veld |
| vrije term | `2ae7` | Bevat-zoeken over de identificerende kolommen |

Clausules gescheiden door spaties worden verbonden met **AND**, zoals Kibana
doet. **OR bestaat alleen binnen de haakjes van één veld.** Dat dekt "een van
deze types" af zonder dit tot een expressieparser te maken waarvan niemand de
voorrangsregels zou onthouden. Binnen de haakjes worden zowel `A OR B` als een
kaal `A B` als dezelfde lijst aanvaard.

Uitsluiting omvat de hele clausule: `-type:(ACK OR ADVERT)` wordt
`NOT (type = ACK OR type = ADVERT)`.

### Het sterretje

**Een sterretje staat voor "wat dan ook", waar het ook staat.** Eén regel in
plaats van drie losse vormen, want wie geleerd heeft dat een sterretje "wat dan
ook" betekent, hoeft er niet ook nog bij te leren waar het mag staan:

| Zoekopdracht | LIKE-patroon | Vraagt om |
|---|---|---|
| `sender:2ae7*` | `2ae7%` | begint met |
| `name:*circuit` | `%circuit` | eindigt op |
| `name:*circuit*` | `%circuit%` | bevat |
| `name:BE*VIR` | `BE%VIR` | beide delen, in die orde |

Het werkt binnen een OR-lijst (`type:(*MSG* OR ACK)`) en binnen een uitsluiting
(`-name:*test*`) net als elke andere waarde.

**Alleen tekstvelden.** Bevat-zoeken op een getal zegt niets — `snr:*5*` zou
vragen om een signaalsterkte met ergens een vijf in de decimale notatie — dus een
sterretje op een getalveld is een fout, en de melding gaat over het sterretje in
plaats van over een waarde die geen getal is. `Field.kind` is de hele regel; er is
geen tweede lijst veldnamen die daarvan weg zou kunnen driften. Getalvelden hebben
in de plaats daarvan vergelijkingen en bereiken, en dat is wat iemand die naar
`snr:*5*` grijpt eigenlijk wilde.

Een sterretje gaat wél boven het bevat-gedrag van `name` en `path`: zonder
sterretje is `name:BE-HSS` al `%BE-HSS%`, omdat die kolom een hooiberg is, maar
`name:BE-HSS*` ankert aan het begin. De bezoeker heeft dan zelf gezegd waar de
match moet zitten, en dat antwoord gaat voor op de standaard van de kolom.

**Bij `name` en `path` ankert een anker de hooiberg, niet één naam.** `name` is
`c.name || ' ' || o.name`, dus `name:BE-HSS*` matcht wanneer de naam van de
*afzender* zo begint, en `name:*VIR` wanneer de naam van de *waarnemer* daarop
eindigt — en heeft die waarnemer helemaal geen naam, dan eindigt de uitdrukking op
de scheidingsspatie en eindigt er niets op `VIR`. `path` doet hetzelfde rond zijn
kommagescheiden hoplijst, waar "begint met" de eerste hop betekent. Beide zijn
eerlijke lezingen van "dit veld begint hiermee", en beide zijn makkelijk aan te
zien voor "een van de twee namen begint hiermee". `name:*BE-HSS*` is de vorm die
de vraag stelt die mensen doorgaans bedoelen.

## Velden

Het soort bepaalt hoe een waarde eruit mag zien: `num` aanvaardt vergelijkingen
en bereiken, `text` aanvaardt jokers en aanhalingstekens. Facetvelden kan je om
een uitsplitsing "meest voorkomende waarden" vragen; sorteervelden mogen de
resultatenlijst ordenen.

| Veld | Kolom | Soort | Label | Voorbeeld | Facet | Sorteer |
|---|---|---|---|---|---|---|
| `type` | `p.payload_name` | text | Payloadtype | `ADVERT` | ja | ja |
| `route` | `p.route` | text | Routetype | `FLOOD` | ja | ja |
| `scope` | `p.scope` | text | Bereik | `scoped` | ja | ja |
| `region` | *(afgeleid)* | num | Regio | `7` | ja | ja |
| `sender` | `p.sender` | text | Afzender (sleutel) | `2ae7c1` | ja | ja |
| `observer` | `p.observer` | text | Waarnemer (sleutel) | `2ae7c1d40f93` | ja | ja |
| `dest` | `p.dest_hash` | text | Bestemming (hash) | `c3` | nee | ja |
| `src` | `p.src_hash` | text | Afzender (hash) | `e3` | nee | ja |
| `name` | naam afzender ‖ waarnemer | text | Naam van afzender of waarnemer | `BE-XXX` | nee | nee |
| `country` | land afzender of waarnemer | text | Land | `BE` | ja | ja |
| `snr` | `p.snr` | num | SNR | `>5` | nee | ja |
| `rssi` | `p.rssi` | num | RSSI | `<-100` | nee | ja |
| `len` | `p.len` | num | Lengte in bytes | `20..40` | nee | ja |
| `hops` | `p.path_len` | num | Aantal hops | `>3` | ja | ja |
| `path` | `p.path` | text | Hop in het pad | `2ae7` | nee | nee |
| `hash` | `p.phash` | text | Payloadhash | | nee | ja |

De joins die die expressies veronderstellen, staan in `db._SEARCH_FROM`; houd de
twee gelijk. Die joins lezen `visible_contacts` en niet `contacts`, dus `name` en
`country` zoeken in de *gepubliceerde* waarden: een repeater met `show_name = 0`
matcht niet op zijn echte naam en wél op zijn adreshash. Een naam bevestigen aan
wie hem al vermoedde, is nog steeds vertellen. Zie
[`privacy.md`](privacy.md).

### De velden die zich anders gedragen

**`sender` en `src` zijn met opzet aparte velden.** `sender` bevat de volledige
sleutelprefix die een ADVERT noemde, en die hebben de meeste pakketten gewoon
niet; `src` is de ene byte die de rest wél draagt. Ze beantwoorden verschillende
vragen — "pakketten van deze node" tegenover "pakketten van wie deze byte ook is"
— en een zoekfunctie die de eerste stilzwijgend tot de tweede verbreedde, zou
rijen teruggeven waar de bezoeker nooit om vroeg. Het zoeken matcht de bewaarde
byte, want dat is het deel dat een feit is; hem tot een node herleiden is de taak
van de API, met alle eerlijkheid die dat vraagt. Zie
[`candidates.md`](candidates.md).

**`dest` is de spiegel ervan**, en beantwoordt een vraag die het archief eerder
helemaal niet kon beantwoorden: wat was er op deze node gericht.

**`name` en `path` matchen op bevatten, niet op gelijkheid.** Het zijn
hooibergen — `name` is een samenvoeging van twee namen, `path` een
komma-gescheiden hoplijst — dus een exacte match op de hele kolom zou nooit
raken. Dat is meteen de reden dat geen van beide sorteerbaar is: hun
alfabetische volgorde zegt niemand iets.

**`region` heeft geen kolom.** De regio die een gescoopt pakket noemt, staat
binnenin `scope_codes`, dus zowel het filteren als het sorteren leidt hem af met
`search.REGION_SQL`:

```sql
CAST(NULLIF(substr(p.scope_codes, instr(p.scope_codes, ',') + 1), '0') AS INTEGER)
```

`FIELDS["region"].sql` is een plaatshouder (`p.scope_region`) die zowel
`_field_clause()` als de `SORTS`-tabel voor die expressie inwisselt. Het sorteren
moet diezelfde wissel maken, of de query zou een kolom noemen die de
pakkettentabel niet heeft.

### Wat een vrije term doorzoekt

`FREE_TEXT_FIELDS`: `p.sender`, `p.observer`, `p.payload_name`, `p.scope`,
`c.name`, `o.name`, `c.country`, `o.country`. Met opzet alleen de identificerende
kolommen — `snr` erbij zou betekenen dat `5` intypen op een signaalsterkte matcht,
en dat is nooit wat iemand met een los woord in een zoekvak bedoelt.

Een vrije term is altijd bevat-zoeken, dus een sterretje aan een van de uiteinden
voegt niets toe en wordt eraf gehaald in plaats van geweigerd: `Jessa`, `Jessa*`
en `*Jessa*` leveren hetzelfde patroon op. Een sterretje in het midden is geen
versiering en houdt zijn betekenis — `BE*VIR` wordt `%BE%VIR%`.

## Sorteren

Sorteren is een **eigen parameter**, geen clausule in de zoektekst. Een
sortering is geen filter — ze verandert niets aan de resultaatverzameling, alleen
aan de pagina ervan waar je naar kijkt — en haar in het tekstvak vouwen zou één
clausule opleveren die stilzwijgend iets anders doet dan alle andere, plus een
parser die een `sort:` uit een `NOT` en uit een `OR`-lijst moet houden.

`sort=veld` of `sort=veld:asc|desc`. Geen richting betekent **aflopend**, net
zoals de standaardvolgorde van het archief nieuwste eerst is: het interessante
uiteinde van een hopaantal, een signaalsterkte of een ogenblik in de tijd is
vrijwel altijd het bovenste. Leeg betekent de standaard, `time:desc`. Al het
overige is een fout en geen stille terugval — een link die "gesorteerd op hops"
belooft en stiekem iets anders toont, is dezelfde soort leugen als een
zoekopdracht die de helft van haar clausules laat vallen.

`SORTS` is afgeleid van `FIELDS` (elk veld met `sort=True`) plus één met de hand
toegevoegde vermelding: `time`, op `p.ts`. Tijd staat niet in `FIELDS` omdat het
archief op tijd filtert via de vensterkiezer en niet via de zoektaal, maar het is
de standaardvolgorde, dus het moet sorteerbaar zijn — en het is de enige kolom
die het schema als `NOT NULL` verklaart.

De `ORDER BY` die `search.Sort` bouwt, heeft drie delen:

```sql
<kolom> IS NULL,          -- alleen voor een kolom die NULL kan zijn
<kolom> ASC|DESC,
p.id ASC|DESC
```

**Ontbrekende waarden gaan in beide richtingen achteraan.** SQLite sorteert NULL
vooraan bij oplopend, dus "sorteer op SNR, kleinste eerst" zou anders openen op
een volle pagina streepjes — de pakketten waarvan het signaal nooit genoteerd is,
gepresenteerd alsof ze de zwakste waren. Geschreven als `x IS NULL` en niet met
de `NULLS LAST`-clausule, die SQLite 3.30 vraagt en hetzelfde kost.

**Het id is een gelijkspelbreker die de volgorde totaal maakt.** Zonder dat
zouden twee pakketten met hetzelfde hopaantal van plaats kunnen wisselen tussen
het verzoek om pagina 1 en dat om pagina 2, en zou een rij twee keer, of helemaal
niet, verschijnen zonder dat de lezer daar iets van kan zien. Het loopt mee met
de sorteerrichting zodat gelijke waarden nog chronologisch lezen.

### Waarom de kolommentabel de verdediging is

`Sort.sql` wordt binnen `search.py` samengesteld zodat elk teken ervan uit de
eigen tabellen van die module komt. De sleutel wordt in `SORTS` opgezocht, en een
sleutel die er niet in staat werpt een fout op in plaats van geïnterpoleerd te
worden. Dat is de hele verdediging tegen injectie via de sorteerparameter, en het
is de reden dat de kolom nooit als tekst uit de API-laag wordt doorgegeven: een
parameterplaatshouder kan niet in de plaats van een kolomnaam staan, dus het
enige veilige alternatief voor een vaste tabel zou een ontsnappingsroutine zijn
die elke keer opnieuw juist moet zijn. Dezelfde regel geldt voor
`db.packet_facets()`, waarvan het argument `column` eerst in `FIELDS` opgezocht
wordt.

## Kolommen

`search.COLUMNS` is de geordende reeks kolommen die de archieftabel kan tonen:

```
time, sender, src, dest, observer, type, route, scope, region,
snr, rssi, hops, len, path, hash, country
```

`DEFAULT_COLUMNS` — wat een bezoeker ziet die nog nooit iets gekozen heeft — is
`time, sender, type, scope, snr, rssi, hops, len, country`: precies de kolommen
die de pagina toonde voordat de keuze bestond, zodat een pagina die een bezoeker
kent zich niet onder hem herschikt.

Een geordende reeks en geen extra vlag op `Field`, want ze drukt iets uit wat de
veldentabel niet kan: **waar** een kolom staat. Elke naam erin is een sleutel van
`SORTS` of `FIELDS` — er is geen aparte woordenschat voor kolommen — maar de twee
lijsten zijn niet dezelfde lijst en geen van beide is een deelverzameling van de
andere: `path` is een kolom waard en nutteloos als volgorde; `name` is het
zoeken waard en al zichtbaar in de afzenderkolom, dus geen van beide.

De pagina rendert de gekozen kolommen in de volgorde van `COLUMNS` en niet in de
volgorde waarin ze aangevinkt zijn, zodat de tabel er hetzelfde uitziet welke weg
iemand er ook naartoe nam en een gedeelde link niet met het tijdstempel in het
midden kan aankomen. Kolommen met de hand herschikken is overwogen en
weggelaten: dat is een tweede, zwaardere functie (sleepdoelen, een bewaarde
volgorde, een URL die hem meedraagt) bovenop degene waar om gevraagd werd.

## De drie describe-functies

De pagina houdt nooit een tweede mening over wat de server aanvaardt:

| Functie | Voedt | Vorm |
|---|---|---|
| `describe_fields()` | Het hulppaneel en de filterknoppen | `{name, label, kind, hint, facet}` |
| `describe_sorts()` | De klikbare kolomkoppen | `{name, kind}` |
| `describe_columns()` | De kolomkiezer en de tabel | `{name, sort, default}` |

Een kolomkop die een volgorde aanbood die de server weigert, zou een knop zijn
die een foutmelding oplevert, en die zou verschijnen op het ogenblik dat iemand
de tabel bewerkt. `kind` reist mee zodat de pagina een verstandige eerste
klikrichting kan kiezen.

## Ontsnappen

`_escape_like()` maakt de eigen jokertekens van LIKE onschadelijk binnen een
waarde: `\` → `\\`, `%` → `\%`, `_` → `\_`, en elke `LIKE` wordt met
`ESCAPE '\'` geschreven. Zonder dat zou een bezoeker die een letterlijke
underscore zoekt — waar elke nodenaam vol mee staat — stilzwijgend een joker voor
één teken krijgen, en zou het resultaat eruitzien als een werkende zoekopdracht
die net iets verkeerde rijen teruggeeft.

**Het sterretje van de bezoeker wordt ná die escaping omgezet, nooit ervoor.** De
twee gebruiken hetzelfde mechanisme, dus de orde is wat ze uit elkaar houdt:
`_escape_like()` produceert enkel `\`, `%` en `_`, nooit een sterretje, dus niets
van wat die functie afgeeft kan achteraf gelezen worden als een joker die iemand
gevraagd heeft — en een getypt `%` is al onschadelijk gemaakt op het ogenblik dat
de sterretjes `%` worden. De omgekeerde orde zou een getypt `%` in een joker
veranderen, en zou `name:*_*` laten zoeken naar drie willekeurige tekens in plaats
van naar een letterlijke underscore tussen twee jokers.
`server/tests/test_search.py` test dat geval tegen echte SQLite, want het is een
afspraak met de database en niet met een string.

Al het overige is een gebonden parameter. De enige teksten die in SQL
geïnterpoleerd worden, zijn kolomexpressies uit `FIELDS`, `SORTS` en
`REGION_SQL`.

## Foutmeldingen

Ze zijn geschreven voor wie de zoekopdracht intypte, in het Nederlands, en ze
benoemen het probleem in plaats van de parser:

| Situatie | Melding |
|---|---|
| Onbekend veld | `Onbekend veld 'foo'. Bekende velden: …` |
| Veld zonder waarde | `Veld 'snr' heeft geen waarde.` |
| Kale `-` of `NOT` | `Er staat een min of NOT zonder iets erachter.` |
| Niet-gesloten aanhalingsteken | `Een aanhalingsteken is niet afgesloten.` |
| Niet-gesloten haakje | `Een haakje is niet gesloten.` |
| Haakjes zonder veld | `Haakjes horen bij een veld, zoals type:(ADVERT OR ACK).` |
| Lege lijst | `Veld 'type' heeft een lege lijst.` |
| Alleen sterretjes als waarde | `Veld 'sender' heeft alleen sterretjes als waarde.` |
| Een sterretje op een veld dat geen tekst is | `Een sterretje werkt alleen op tekstvelden, en 'snr' is een getalveld.` |
| Niet-numerieke waarde op een numeriek veld | `Veld 'snr' is een getal, en 'abc' is dat niet.` |
| Omgekeerd bereik | `Bereik voor 'len' loopt achteruit: 40..20.` |
| Onbekende sorteersleutel | `Sorteren op 'foo' kan niet. Wel mogelijk: …` |
| Onbekende sorteerrichting | `Sorteerrichting 'up' bestaat niet; kies asc of desc.` |

## Hoe de parser gebouwd is

`_tokenize()` is met de hand geschreven en niet één regex over de hele tekst:
aanhalingstekens en de lijst met haakjes bevatten allebei spaties, en één
expressie die dat aankan, is alleen-schrijfbaar. Ze loopt de tekst één keer door
en levert drietallen `(uitgesloten, veld of None, waarde)`; `_read_value()` leest
één waarde — tussen aanhalingstekens, tussen haakjes, of tot de volgende spatie.

`parse()` maakt daar een `Query` van met een SQL-fragment en zijn parameterlijst.
Een lege zoekopdracht levert een leeg fragment op, wat "match alles" betekent;
`db._search_where()` begrenst het dan alleen nog met het tijdvenster.

De module is puur: geen I/O, geen databankverbinding. Dat is wat
`server/tests/test_search.py` en `test_search_sort.py` in staat stelt om
rechtstreeks over de gegenereerde SQL te oordelen.

## Verwante documenten

| Vraag | Document |
|---|---|
| Het endpoint dat deze zoekopdrachten draait | [`api.md`](api.md#get-apiv1packetssearch) |
| De kolommen waarin gezocht wordt | [`database.md`](database.md#packets) |
| Waar `src` en `dest` naar oplossen | [`candidates.md`](candidates.md) |
