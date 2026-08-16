# Het pakketfilter

*[English](../packet-filter.md)*

Een repeater stuurt andermans pakketten door. Dat is zijn werk, en meestal wil je
dat hij daar gewoon mee doorgaat. Maar een gedeelde band heeft slechte dagen: één
verkeerd ingestelde node overspoelt een kanaal, een client probeert een bericht
vierhonderd keer per uur opnieuw, een advertentiestorm eet de zendtijd op waar een
zonnepaneel voor betaald heeft. Op zulke dagen wil je kunnen zeggen *dat niet,
niet hiervandaan* zonder de repeater uit de lucht te halen.

Dat is het pakketfilter. Het zit in de doorstuurbeslissing van een
`simple_repeater` en gooit pakketten weg die een regel overtreden die jij gezet
hebt, en het telt elke weggegooide pakket met de reden erbij, zodat de site kan
laten zien wat er weg is.

**Het staat standaard uit, en het blijft uit tot iemand het aanzet.**

## Waar het idee vandaan komt

Het ontwerp van dit filter volgt het gedrag dat de fork
[Dutch MeshCore](https://github.com/Dutch-MeshCore/MeshCore) beschrijft in
[`docs/packet_filter_reference.md`](https://github.com/Dutch-MeshCore/MeshCore/blob/dmc-dev/docs/packet_filter_reference.md):
dezelfde zes soorten regel, dezelfde `filter …`-commandofamilie, hetzelfde
uitgangspunt "standaard uit, direct gerouteerde pakketten gaan eromheen".

**Er is geen code uit dat project overgenomen.** Deze uitvoering is geschreven op
basis van het beschreven gedrag, niet op basis van hun broncode. Waar hun
beschrijving iets veronderstelt wat een gewone repeater niet kan, zegt deze
uitvoering dat en doet iets anders — zie *Waar dit afwijkt van de referentie*
hieronder. De licentieafweging staat in
[`contributing.md`](contributing.md#code-van-derden).

## Wat het filtert, en wat het nooit aanraakt

Het filter krijgt precies één vraag, op precies één plek:
`MyMesh::allowPacketForward()`, de functie die MeshCore aanroept vóór het
andermans pakket opnieuw uitzendt. Dus:

- **Pakketten die aan deze node gericht zijn worden nooit gefilterd.** Een login,
  een CLI-commando, een statusverzoek — geen daarvan is een doorgestuurd pakket,
  dus geen daarvan komt langs het filter. Je kunt jezelf niet buitensluiten met
  een filterregel.
- **Direct gerouteerde pakketten worden nooit gefilterd.** Een pakket dat een pad
  volgt waarin deze node al genoemd staat, hoort bij iemands gevestigde route;
  die weggooien breekt werkende gesprekken in plaats van een stortvloed te
  beteugelen. Alleen floodpakketten — die deze node uit zichzelf verspreidt —
  worden gefilterd.
- **Pakketten waar een node uit de toegangslijst bij betrokken is, worden nooit
  gefilterd.** Komt de bestemmings- of afzenderhash van een pakket overeen met
  een client die deze repeater kent (`setperm`), dan gaat het pakket door wat de
  regels ook zeggen. Wie deze node mag beheren blijft werken terwijl er een
  filter aan staat.

## De zes regels

Elke regel geldt per pakkettype waar dat zin heeft, want de types hebben niets
met elkaar gemeen: een advertentie om de paar uur en een ACK per bericht zijn
niet hetzelfde verkeer en kunnen geen limiet delen.

| ID | Type | ID | Type |
|---|---|---|---|
| `00` | `REQ` | `06` | `GRP_DATA` |
| `01` | `RESPONSE` | `07` | `ANON_REQ` |
| `02` | `TXT_MSG` | `08` | `PATH` |
| `03` | `ACK` | `09` | `TRACE` |
| `04` | `ADVERT` | `10` | `MULTIPART` |
| `05` | `GRP_TXT` | `11` | `CONTROL` |

**Aantal hops.** `filter hops <type> <max>` — een pakket dat al zoveel padhashes
draagt, wordt niet opnieuw doorgestuurd. Standaard 8 voor alles behalve
`GRP_TXT`, dat er 32 krijgt. `0` betekent *stuur niets van dit type door* en
geldt als een categorale blokkade; zie de risicoklassen.

**Snelheidslimiet.** `filter rate <type> <limiet> <seconden>` — hoogstens
`limiet` pakketten van dit type per `seconden` doorgestuurd. Per type, **niet**
per afzender: een repeater kan afzenders voor de meeste types niet uit elkaar
houden zonder te ontsleutelen, en anders beweren zou een leugen in een
statusregel zijn. `0` zet de limiet voor dat type uit. Standaard: 5/60s voor de
meeste, 20/60s voor `TXT_MSG` en `GRP_TXT`, 10/60s voor `ADVERT`.

**Minimale padhashgrootte.** `filter hash <1|2|3>` — pakketten met kleinere
padhashes worden weggegooid. Standaard `1`, wat alles doorlaat. Naar `2` gaan is
een botte bijl: het blokkeert elk pakket van een node die nog niet op meerbyte
paden zit, en dat zijn er vandaag de meeste.

**Geblokkeerde kanalen.** `filter channel add <label> <psk|hash>` — groepstekst
op deze kanalen wordt niet doorgestuurd. Alleen `GRP_TXT` wordt geraakt, tot 16
regels. Lees *Een kanaal blokkeren* hieronder eerst; het werkt niet zoals je zou
denken.

**Misvormde groepsberichten.** `filter malformed on` — `GRP_TXT`-pakketten
waarvan de structuur niet kán kloppen worden weggegooid. Standaard uit.

**Pakkettype.** `filter type <type> off` — stuur dit type helemaal niet door. Dit
is de grootste hamer in de kist en is navenant ingedeeld.

## Een kanaal blokkeren

Een repeater heeft geen idee welke kanalen er bestaan. Alles wat hij in een
groepsbericht ziet is **één byte**: de kanaalhash, die MeshCore berekent als de
eerste byte van `sha256(kanaalsleutel)`. Niet van de naam — van de sleutel.

"Blokkeer kanaal *X*" is dus niet te beantwoorden vanuit een naam alleen, en deze
uitvoering doet niet alsof dat wel kan. Je geeft het:

- de **vooraf gedeelde sleutel** van het kanaal (base64, zoals je hem in een
  client zou plakken), waaruit de node de hash op dezelfde manier berekent als
  MeshCore; of
- de **hashbyte zelf**, als twee hexcijfers met een `#` ervoor, voor als je hem
  uit een pakket in het archief hebt gelezen en de sleutel niet hebt.

Het label dat je ernaast typt is voor jou en voor de site. De node vergelijkt op
de hash.

**Eén byte hash botst.** Ruwweg één kanaal op 256 deelt zijn hashbyte met een
ander. Een kanaal blokkeren blokkeert dus gemiddeld ook een klein deel
niet-verwant groepsverkeer, en een repeater kan het verschil niet zien zonder de
sleutel. Dat is een echte prijs van deze regel en de reden dat het niet de eerste
is waar je naar grijpt.

## Waar dit afwijkt van de referentie

Twee plaatsen, allebei omdat het beschreven gedrag iets veronderstelt wat een
repeater niet heeft.

**Kanalen gaan op sleutel of hash, niet op naam** — om de reden hierboven.

**"Misvormd" betekent structureel onmogelijk, niet inhoudelijk verkeerd.** De
referentie beschrijft het controleren van de tijdstempel, de tekst en de
UTF-8-codering van een groepsbericht. Alle drie hebben de leesbare tekst nodig,
en die heeft de kanaalsleutel nodig, die een repeater niet heeft. Wat deze
uitvoering controleert, is wat zónder sleutel te controleren valt:

- de payload is lang genoeg voor een kanaalhash, een MAC en één cipherblok;
- de lengte van de versleutelde tekst is een heel aantal blokken van 16 byte.

Een pakket dat op een van beide zakt, was nooit een geldig groepsbericht van wie
dan ook. Een pakket dat er doorheen komt kan nog steeds onzin zijn — dat kunnen we
niet weten, en de statusregel zegt daarom `structureel` en niet `geldig`, zodat
niemand er meer in leest dan er staat.

## De commando's

Alles hieronder werkt over de seriële console, de telnetconsole **en de
mesh-CLI**, net als elk ander commando in deze firmware. Dat is met opzet: zie
*De weg terug* hieronder.

```
filter                       stand, en hoeveel er weggegooid is
filter on                    aanzetten
filter off                   uitzetten, regels blijven staan
filter reset                 terug naar de standaardwaarden en uit
filter types                 de typetabel hierboven
filter hops                  de hoplimieten
filter hops <type> <max>     er één zetten
filter rate                  de snelheidslimieten
filter rate <type> <n> <s>   er één zetten
filter hash                  de minimale padhashgrootte
filter hash <1|2|3>          hem zetten
filter malformed [on|off]    de structuurcontrole op groepstekst
filter type <type> [on|off]  dit type überhaupt doorsturen, of niet
filter channel list          de geblokkeerde kanalen
filter channel add <label> <psk|#hh>
filter channel remove <label|#hh>
filter count                 weggegooid per reden en per type
```

De instellingen staan in `/filter_prefs` op het bestandssysteem van de node zelf
en overleven een herstart. Ze worden lui weggeschreven — een reeks wijzigingen
kost één schrijfronde en geen tien, want SPIFFS slijt.

## De weg terug

Een filter is precies het soort instelling dat een node nutteloos maakt zonder
hem onbereikbaar te maken. Hij antwoordt nog, hij adverteert nog, hij staat op
elke pagina nog groen — en hij stuurt stilletjes niets meer door. Je merkt het
als iemand klaagt.

Drie dingen staan daartussen.

**`filter off` en `filter reset` zijn altijd bereikbaar over de mesh-CLI.** Ze
hebben geen WiFi nodig, geen beheerpagina, geen server. Een node waarvan het
filter vanaf de site verkeerd gezet is, is met één commando over LoRa weer goed.
Het zijn ook de *goedkoopste* handelingen in het rechtenmodel — een rol die een
filter niet aan mag zetten, mag er wel een uitzetten. Herstel mag nooit strakker
afgeschermd zijn dan de fout die het terugdraait.

**De site toont een actief filter overal waar de node voorkomt.** Niet weggestopt
op een instellingenpagina: het nodepaneel, de vergelijkingstabel en het
API-antwoord van de node dragen allemaal de filterstand, zodat "op deze node
staat een filter aan" zichtbaar is voor wie er niet naar zocht.

**Elke weggegooide pakket wordt geteld, per reden en per type**, en die tellers
reizen mee met het gewone statistiekenbericht. De site legt ze vast als metriek
zoals elke andere, wat betekent dat ze in grafieken komen, dat ze met dezelfde
bewaartermijn verouderen, en dat een filter dat echt verkeer begint op te eten
zichtbaar wordt als een lijn die omhoog loopt in plaats van als een afwezigheid
die je moet opmerken.

## Beheren vanaf de site

Zie [`node-management.md`](node-management.md#het-pakketfilter) voor het
doorloopje en [`admin.md`](admin.md#pakketfilter) voor elk veld. Kort:

- Het filterpaneel op de beheerpagina van een node leest de actuele stand bij de
  node zelf (`GET /api/filter`) en schrijft via één endpoint
  (`POST /api/filter`).
- **De firmware is de baas over de regels.** De server stuurt de commandoregel en
  toont wat er terugkwam; hij houdt er geen eigen idee op na over wat een geldige
  limiet is, behalve om een duidelijke tikfout te weigeren vóór die een
  netwerkronde kost. Dezelfde verdeling als bij de schrijver van
  CLI-instellingen — zie [`admin.md`](admin.md).
- Drie risicoklassen, dezelfde drie die de rest van deze site gebruikt:

| Klasse | Handelingen | Bevestiging |
|---|---|---|
| `gewoon` | `off`, `reset` | geen |
| `merkbaar` | `on`, en elke regel die verkeer versmalt zonder een categorie helemaal te blokkeren | `ja` typen |
| `ingrijpend` | `hops … 0`, `type … off`, `hash 3`, en aanzetten terwijl zo'n regel al staat | de naam van de node typen |

De klasse wordt bepaald uit de handeling en zijn argumenten, op de server, vóór
er iets verstuurd wordt — en nog een keer bij de bevestigingscontrole, zodat een
zelfgebouwd formulier hem niet kan overslaan.
