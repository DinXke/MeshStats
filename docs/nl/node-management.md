# Nodes beheren vanaf de site

*[English](../node-management.md)*

Wat de site met een node kan doen, met welke nodes dat kan, en — het deel dat de
meeste woorden kost — wat hij met opzet níét doet.

Een node uitlezen is uitgekristalliseerd en werkt al lang. Deze pagina gaat
vooral over **schrijven**: een instelling wijzigen vanaf de site in plaats van
via een seriële kabel. Een deel daarvan is gebouwd, een deel is ontworpen en
uitdrukkelijk nog niet gebouwd. Bij elke sectie staat welke van de twee.

---

## De drie niveaus

Het **beheerniveau** van een node is een waarneming, geen instelling. Er is geen
knop om het te verhogen, want het niveau is een uitspraak over wat er op dit
moment waar is, en met een knop zou het iets kunnen zeggen dat dat niet is.

`commanding.describe()` meldt het als `level`, met een Nederlandse `level_why`
erbij.

| Niveau | Wat er waar is | Referentienode |
|---|---|---|
| `unmanaged` | Alleen ooit in het verkeer gezien: adverts, pakketten, SNR, soms een positie. Nergens inloggegevens, geen acties. | het grootste deel van het mesh |
| `semi_managed` | Niet onze firmware. Bereikbaar over LoRa via een monitorende repeater die CLI-rechten op hem heeft. | **JessaZH** (`e3d3f4…`), en dat blijft nog maanden zo |
| `full_managed` | Onze firmware, publiceert zelf naar MQTT. Eigen statistieken, een eigen `cmd`-topic, klok, en — als er ook een IP-pad is — firmware. | **DinX-Home** (`55d9a3…`) |

De afleidingsvolgorde, die tegelijk de volgorde is waarin het bewijs het sterkst
is:

1. De node publiceert zelf statistieken **en** meldt een
   `fw_meshmanager`-versie → `full_managed`. Zijn `cmd`-topic is bereikbaar, dus
   hij kan rechtstreeks aangestuurd worden.
2. Hij publiceert niet zelf, maar een monitor met CLI-rechten bereikt hem →
   `semi_managed`.
3. Geen van beide → `unmanaged`.

**Standaard MeshCore is het normale geval, niet de uitzondering.** Nodes met
onze firmware zijn de variant die extra dingen kan. Het is de moeite waard om de
tabel zo om te draaien, want ontwerpen voor "onze nodes, plus wat anderen" is
precies hoe de dakrepeater een randgeval wordt — en dat is de node waar dit hele
project omheen gebouwd is.

### Overgangen

Een `semi_managed` node wordt `full_managed` door onze firmware te krijgen, en
daarvoor is een IP-pad nodig (zie
[`firmware-upgrade.md`](firmware-upgrade.md)). Een `full_managed` node valt terug
naar `semi_managed` wanneer zijn netwerkverbinding wegvalt en iemand hem nog
monitort. Geen van beide overgangen voert de site uit; het zijn allebei dingen
die hij opmerkt.

---

## Wat er per niveau mogelijk is

| | `unmanaged` | `semi_managed` | `full_managed` |
|---|---|---|---|
| Uitlezen uit het verkeer (adverts, SNR, pad, positie) | ja | ja | ja |
| Eigen statistieken (uptime, zendtijd, tellers) | nee | nee | ja |
| CLI-instellingen lezen | nee | ja, over LoRa | ja |
| **CLI-instellingen schrijven** | nee | ontworpen, niet gebouwd | **gebouwd** — het hele oppervlak, met risicoklassen |
| De klok zetten | nee | ja, via de monitor | ja |
| Firmware-upgrade | nee | **nee** | alleen met een IP-pad |
| Telemetrie opvragen zonder inloggegevens | meestal **ja** — een eigenschap naast het niveau, geen onderdeel ervan | meestal ja | meestal ja |

**Firmware-upgrade is met opzet geen onderdeel van het niveau.** Een
`full_managed` node kan een commando van twintig bytes over MQTT aannemen en toch
geen image van 1,3 MB kunnen aannemen, want die twee reizen niet over hetzelfde.
De site houdt het in een aparte sleutel, `firmware.ota_route(rep)`, die `can`
teruggeeft plus een `blocker` in de stijl van `commanding.route_for`. De
redenering en de blocker-waarden staan in
[`firmware-upgrade.md`](firmware-upgrade.md).

**Een mogelijkheid die niet van toepassing is, wordt uitgeschakeld getoond met
de reden erbij, en nooit verborgen.** Een knop die verdwijnt laat "waarom kan ik
dit hier niet" onbeantwoord, en dat is nou juist de vraag die iemand heeft op het
moment dat hij die knop nodig heeft.

---

## Kunnen instellingen over diezelfde MQTT-route geschreven worden?

Het korte antwoord, omdat het rechtstreeks gevraagd is: **technisch gezien ja, en
het aanbevolen ontwerp doet het niet zo.**

### Waarom niet via het `cmd`-topic

Het topic accepteert `settings`, `status` en `time <epoch>` — een strikte
toelatingslijst, nooit een doorgeefluik naar de CLI. De redenering staat in
`MeshManagerNet.h` en gaat over wie dat topic kan bereiken: **iedereen met
broker-inloggegevens**, gedeeld, gelekt of vertypt. Eén `reboot` in een lus kost
je de dakrepeater.

Dat argument wordt niet zwakker bij schrijven. Het wordt sterker, want lezen kan
een node niet onbereikbaar maken en schrijven wel. Een verkeerde `freq`, een
verkeerde `radio`, `tx 0`, `repeat off` of een verkeerde WiFi-instelling op een
node die je alleen over LoRa bereikt, is geen vergissing die je herstelt — de
instelling wordt van kracht, en daarmee verdwijnt de enige weg terug. Op een dak
is dat het einde van de node.

### De route die in plaats daarvan aanbevolen wordt

**Schrijfacties gaan over HTTP, naar een node die de server kan bereiken, achter
de eigen login van die node.** Welke node dat is, hangt af van het niveau:

| Doel | Schrijfpad |
|---|---|
| `full_managed` met een IP-pad | HTTP naar de node zelf |
| `semi_managed` (JessaZH) | HTTP naar zijn **monitor** (DinX-Home), die `set …` over LoRa uitstuurt |
| `full_managed` zonder IP-pad | wordt niet aangeboden — zie hieronder |
| `unmanaged` | wordt niet aangeboden, er is niets om mee te authenticeren |

Hier is het de moeite waard even bij stil te staan: het doorgegeven geval werkt.
JessaZH wordt geschreven door met DinX-Home te praten, en DinX-Home hangt aan het
LAN. **Het referentiegeval wordt gedekt door de route die MQTT helemaal niet
aanraakt.**

Wat het oplevert:

- Broker-inloggegevens blijven niets kunnen veranderen. De MQTT-toelatingslijst
  blijft precies drie woorden, en aan het dreigingsmodel van dat topic verandert
  niets.
- Een schrijfactie wordt geauthenticeerd tegen de eigen beheerderslogin van de
  node, en dat is een inloggegeven dat een mens in handen heeft, geen
  serviceaccount dat een container in handen heeft.
- Het transport bestaat al en wordt al gebruikt voor firmware, inclusief de
  adresvalidatie en de foutmeldingen.

Wat het kost: een `full_managed` node zonder IP-pad kan helemaal niet geschreven
worden, ook al werkt zijn `cmd`-topic. Dat geval bestaat vandaag niet in deze
installatie. Duikt het op, dan is het antwoord niet om de MQTT-toelatingslijst
stilletjes te verbreden — het is om deze beslissing bewust opnieuw te openen, met
de risicoklassen hieronder als datgene wat overeind moet blijven.

### Als het ooit tóch over MQTT zou gebeuren

Dan wordt de brokerconfiguratie dragend in plaats van hygiënisch, en moet
`mosquitto/acl.example` dat ook zeggen:

- Het account van de site is het **enige** met `write meshmanager/+/cmd`.
  Node-accounts krijgen `write meshmanager/<own-id>/#` en verder niets, zodat een
  gecompromitteerde node zijn buren niet kan commanderen.
- Elk node-account is per node, nooit gedeeld. Een gedeeld account betekent dat
  een lek niet in te dammen is zonder elke node opnieuw in te richten.
- Een gedeeld geheim of een handtekening op het commando zou helpen, en het is
  eerlijk om erbij te zeggen dat dat op zichzelf niet genoeg is: de node zou dat
  geheim moeten bewaren in dezelfde flash die een back-up uitdeelt, en het
  beschermt de *inhoud* van een commando, niet het feit dat wie
  broker-inloggegevens heeft die van gisteren opnieuw kan afspelen.

Dat is een hoop machinerie om een route veilig te maken die we niet nodig hebben.
Vandaar HTTP.

---

## Welke instellingen geschreven mogen worden

**Allemaal, op drie na.** De lijst is het volledige `handleSetCmd()`-oppervlak
van `src/helpers/CommonCLI.cpp` — achtentwintig parameters — en niet een
zorgvuldig uitgekozen veilig hoekje.

Dat is een bewuste omkering van het eerste ontwerp, dat alleen parameters toeliet
die de bereikbaarheid niet konden afsnijden. Veilig, en naast de kwestie: de
instellingen die je op afstand het hardst nodig hebt *zijn* juist de gevaarlijke
— zendvermogen, radioparameters — en ze weglaten neemt het risico niet weg. Het
betekent alleen dat iemand een ladder haalt, of een seriële kabel, en hetzelfde
doet met minder zorg en zonder dat het ergens vastligt.

Het risico verschoof dus van **weglaten** naar **afhandelen**. Elke parameter
draagt een risicoklasse, en die klasse bepaalt hoeveel wrijving een wijziging
kost.

### De drie die er nog steeds niet bij zitten

| Wordt niet aangeboden | Waarom |
|---|---|
| `prv.key` | Vervangt de identiteit van de node. Dat is geen instelling, dat is een andere node: elke contactenlijst, ACL en monitorregel elders in het mesh wijst dan naar iemand die niet meer bestaat. Er is geen bevestiging die dit vanaf een webpagina een goed idee maakt |
| `bridge.secret` | Een gedeeld geheim dat bij het teruglezen meteen weer naar buiten komt. Een wachtwoord dat in een logregel of een schermafbeelding heeft gestaan, is weg |
| `freq` | MeshCore accepteert `set freq` **alleen vanaf de seriële kabel** (`sender_timestamp == 0`), en dit pad geeft met opzet iets anders mee. Frequentie hoort bij de andere drie radiowaarden en gaat via `radio`, dat *wel* gevalideerd wordt — `set freq` niet |

### Risicoklassen

| Klasse | Wat het betekent | Bevestiging |
|---|---|---|
| **Gewoon** | Een waarde die je zo weer terug kunt zetten | Opslaan is genoeg |
| **Schrijft merkbaar** | Verandert hoe de node zich op het mesh gedraagt, maar kan hem niet buiten bereik brengen | Een expliciet vinkje |
| **Kan de bereikbaarheid afsnijden** | Raakt de radio, of wie er mag inloggen | Typ de naam van de node |

**Gewoon** (9) — `name`, `lat`, `lon`, `owner.info`, `advert.interval`,
`flood.advert.interval`, `rxdelay`, `txdelay`, `direct.txdelay`

**Schrijft merkbaar** (12) — `dutycycle`, `af`, `flood.max`,
`flood.max.unscoped`, `flood.max.advert`, `int.thresh`, `agc.reset.interval`,
`multi.acks`, `path.hash.mode`, `loop.detect`, `cad`, `adc.multiplier`

**Kan de bereikbaarheid afsnijden** (7) — `tx`, `repeat`, `allow.read.only`,
`radio.rxgain`, `radio.fem.rxgain`, `guest.password`, `radio`

De grens die ertoe doet is die tussen de tweede en de derde klasse, en het is één
enkele vraag: *als dit misgaat, is de node dan nog te bereiken via de route
waarmee je hem aanstuurt?* Op een node van ons zijn er twee onafhankelijke wegen
naar binnen — sloop de WiFi en de mesh-CLI antwoordt, sloop de radio-instellingen
en de beheerpagina antwoordt — dus een vergissing is vervelend. Op een
standaardrepeater die je alleen over LoRa bereikt is er één weg naar binnen, en
een verkeerde frequentie is het einde ervan.

De naam van de node typen is hetzelfde middel dat de firmwarepagina voor kritieke
nodes gebruikt, en het staat er om dezelfde reden: de fout die het opvangt is
geen twijfel, het is een klik op de verkeerde regel, en daar helpt een
ja/nee-vraag niet tegen.

**De bevestiging wordt op de server afgedwongen, niet alleen in de pagina.** Een
drempel die je met een met de hand aangepast formulier kunt overslaan is een
opmaakkeuze, geen drempel.

### Het type bepaalt de invoer

Een veld waarin je een ongeldige waarde *kunt* typen is een veld dat een node
kapot kan maken, dus de invoer volgt het opgegeven type:

| Type | Invoer |
|---|---|
| `enum` | Keuzelijst met precies de toegestane woorden (`loop.detect` → off / minimal / moderate / strict) |
| `bool` | Keuzelijst met `on` / `off` — geen vrij veld, want MeshCore vergelijkt met `memcmp(…, "on", 2)`, zodat `onzin` daar *on* betekent |
| `int` / `float` | Getalveld met de eigen `min` en `max` van die parameter |
| `radio` | **Vier** getalvelden — frequentie, bandbreedte, spreading factor, coding rate — elk met een eigen bereik. Eén tekstvak waarin je `869.525 250 11 5` moet typen is precies het vak waar een typfout een verloren node van maakt |
| `text` | Vrije tekst, alleen daar waar het ook echt vrije tekst is |
| `text` + geheim | Wachtwoordveld, nooit voorgevuld — zie hieronder |

### Waar de lijst werkelijk staat

**In de firmware**, meegecompileerd (`CFG_PARAMS` in `MeshManagerNet.cpp`). Niet in
de server, want de server is aan te passen door wie de site draait, en deze lijst
is wat er tussen een klik en de radio staat.

De server houdt er **geen tweede kopie** van bij. Hij vraagt aan de node
(`GET /api/cfg`) welke sleutels die toestaat, van welk type, tussen welke grenzen
en in welke risicoklasse, bouwt het formulier op uit dat antwoord, en valideert
daartegen voordat er iets vertrekt. Dat voldoet nog steeds aan "valideer aan
beide kanten" — de controle van de server geeft snel een fout naast het
invoerveld, de controle van de node is degene die telt — maar er is altijd maar
één lijst, zodat de twee niet uit elkaar kunnen groeien en een parameter gaan
aanbieden die de node weigert.

De grenzen zijn van ons, en op verschillende plekken zijn het de **enige** die er
zijn: `lat`, `lon`, `af`, `tx`, `int.thresh`, `multi.acks` en `adc.multiplier`
worden bij MeshCore zelf ingelezen met een kale `atof()`/`atoi()` en zonder ook
maar enige controle. `atof("noord")` is `0.0`, dus een typfout zet de node in de
Golf van Guinee en de CLI antwoordt `OK`. **Een node die een onzinnige waarde
aanneemt is gevaarlijker dan een die weigert**, en standaard MeshCore is van het
aannemende soort.

Elders zijn die van ons met opzet strenger: MeshCore accepteert `0` voor allebei
de advert-intervallen, wat "stop met adverteren" betekent — daarmee wordt een
node niet onbereikbaar, maar hij zakt er wel mee uit ieders lijst weg, en op een
dak voelt dat hetzelfde.

### Eén instelling is een geheim

`guest.password` staat gemarkeerd als geheim. Hij wordt teruggelezen en
vergeleken zoals al het andere — dat teruglezen is de hele reden dat dit endpoint
bestaat — maar **de gelezen waarde gaat niet mee terug**, en de pagina toont
`(verborgen)`. Het invoerveld is een wachtwoordveld en de huidige waarde wordt
nooit voorgevuld.

Anders zou het wachtwoord dat je net zette in de HTML van de beheerpagina staan,
in de browsergeschiedenis en in elke schermafdruk daarvan — en een wachtwoord dat
daar geweest is, is weg. Dat is dezelfde reden waarom `bridge.secret` helemaal
niet aangeboden wordt; het verschil is dat `guest.password` een instelling is die
je werkelijk van afstand wilt kunnen zetten, dus die wordt afgevangen in plaats
van geschrapt.

### Eén instelling wordt pas na een herstart van kracht

`radio` antwoordt `OK - reboot to apply`. Het teruglezen laat dus de nieuwe
waarden zien terwijl de radio nog op de oude draait, en of die nieuwe werken
blijkt pas bij de herstart. Dat is precies de situatie waarin een node niet
terugkomt, en de pagina zegt dat erbij in plaats van gewoon succes te melden.

### De endpoints

Allebei achter de eigen HTTP-login van de node, dezelfde die `/api/fw` en
`/api/backup` bewaakt.

**`GET /api/cfg`** — wat deze image toestaat, zodat de pagina nooit een sleutel
aanbiedt die de firmware niet heeft:

```json
{"params":[{"key":"loop.detect","kind":"enum","lo":0,"hi":0,
            "choices":"off|minimal|moderate|strict","risk":2,"reboot":0},
           {"key":"radio","kind":"radio","lo":0,"hi":0,
            "choices":"","risk":3,"reboot":1}]}
```

**`POST /api/cfg`** met formuliervelden `key` en `value`:

```json
{"ok":1,"step":"","key":"advert.interval","asked":"61",
 "applied":"60","exact":0,"reply":"OK"}
```

`step` is bij een mislukking `sleutel` (staat niet op de lijst), `waarde` (buiten
de grenzen), `bevestiging` (de bevestiging was te licht) of `node` (de CLI
weigerde), en nooit alleen maar `error`.

De sleutel wordt bij het opbouwen van het commando nooit uit het verzoek
overgenomen — hij wordt opgezocht in de meegecompileerde tabel en de spelling van
die tabel wordt gebruikt — zodat er behalve de waarde geen enkele tekst van de
aanroeper in het commando zit, en die waarde is altijd het laatste woord. De
CLI-aanroep geeft ook een **sender-timestamp ongelijk aan nul** mee: `0` betekent
in MeshCore "dit kwam van de seriële kabel" en ontgrendelt commando's die alleen
daar thuishoren (`erase`, `get prv.key` en `set freq`). Dit pad heeft er geen
enkele van nodig, dus mocht de tabel ooit een gat blijken te hebben, dan is dat
gat kleiner.

### Validatie gebeurt aan beide kanten, en het is niet dezelfde controle

- **De server** valideert type en bereik voordat er iets vertrekt, zodat een
  typfout een weigering naast het invoerveld oplevert in plaats van een pakket.
  Hij valideert tegen de grenzen die de *node* opgaf, niet tegen een eigen lijst.
- **De node** valideert nog een keer voordat hij `set` uitvoert. Dit is de
  controle die het mesh werkelijk beschermt, want de server is aan te passen door
  wie de site draait, en de tabel van de firmware zit meegecompileerd.

Die tweede controle telt het zwaarst voor de gevaarlijke klasse, en om een reden
die het waard is om precies te benoemen: een frequentie buiten de band is niet
*riskant*, die is gewoon **fout**, en hoeveel bevestigingen je er ook voor zet,
hij hoort de radio nooit te bereiken. De bevestiging gaat erover of een
toegestane waarde gezet mag worden; de grenzen gaan erover of een waarde
überhaupt toegestaan is. Dat zijn verschillende vragen, en ze worden op
verschillende plaatsen beantwoord.

Bij een `semi_managed` doel — het pad dat ontworpen is maar niet gebouwd — zou de
uitzendende node de monitor zijn, met onze firmware, zodat de tabel en de
validatie ervan gelden voordat er iets de lucht in gaat.

## Bevestigen-of-terugdraaien: onderzocht, en met opzet niet gebouwd

Het voor de hand liggende vangnet voor riskante configuratiewijzigingen is dat
wat netwerkapparatuur gebruikt: voer de wijziging door, en draai hem automatisch
terug tenzij er binnen N minuten een bevestiging binnenkomt. Het is hier serieus
overwogen en verworpen, om een reden die het opschrijven waard is omdat hij de
intuïtie omkeert.

**De nodes die het nodig hebben kunnen het niet krijgen. De nodes die het zouden
kunnen hebben, hebben het niet nodig.**

- Een `semi_managed` node draait **standaard MeshCore**. Daar zit geen mechanisme
  voor uitgestelde wijzigingen in, en er is geen manier om er een toe te voegen
  zonder de firmware te vervangen — precies datgene wat bij die nodes niet kan.
  Voor JessaZH, de node waar een verkeerde instelling onherstelbaar is, is
  bevestigen-of-terugdraaien dus niet beschikbaar. Niet lastig, niet duur:
  onbeschikbaar.
- Een `full_managed` node zou het kunnen bouwen, en heeft het niet nodig, want
  hij heeft al twee onafhankelijke wegen naar binnen. Sloop de WiFi en de
  mesh-CLI antwoordt nog. Sloop de radio-instellingen en de beheerpagina
  antwoordt nog. De node die een terugdraaitimer zou kunnen bijhouden is de node
  die al een tweede deur heeft.

De moeite gaat dus naar de risicoklassen en de grenzen, die de wijziging
tegenhouden in plaats van hem terug te draaien. Een preventie die op
standaardfirmware werkt is beter dan een rollback die alleen werkt waar hij niet
nodig is. Het is ook waarom de zwaarste klasse om de naam van de node vraagt in
plaats van te beloven dat het teruggezet wordt: dat kan hier niets beloven.

---

## Lees na een schrijfactie terug

Een schrijfactie wordt nooit als geslaagd gemeld op grond van het feit dat hij
verstuurd is. De node leest de parameter meteen na de `set` opnieuw met
`get <key>`, binnen hetzelfde verzoek, en het antwoord draagt **`asked` en
`applied` apart** mee, plus een `exact`-vlag.

Dat is geen defensieve gewoonte. Het zijn twee gemeten gedragingen in MeshCore
die allebei `OK` antwoorden terwijl ze iets anders opslaan:

- **`set lat abc`** → `atof()` levert `0.0` op. Antwoord: `OK`. De node claimt nu
  een positie die hem nooit gegeven is.
- **`set advert.interval 61`** → wordt als `minutes / 2` in één byte opgeslagen,
  dus `30`; `get advert.interval` vermenigvuldigt weer met twee en geeft `60`
  terug. Antwoord: `OK`. **Oneven minuutwaarden komen altijd naar beneden
  afgerond op even terug**, en dat is het normale geval en geen fout.

De beheerpagina heeft dus drie uitkomsten en niet twee: *gezet*, *gezet maar niet
precies* (met allebei de getallen erbij, en een notitie dat dit geen mankement
is), en *niet gezet* met de reden van de node zelf. Alles wat de middelste tot
"geslaagd" zou platslaan, zou dezelfde soort halve waarheid vertellen als het
oude OTA-pad deed.

`get <key>` is dezelfde uitlezing die de dagelijkse instellingenronde gebruikt,
zodat er geen tweede codepad is dat het met het eerste oneens zou kunnen zijn.

---

## Rechten zijn het scharnier, en ze falen op een verwarrende manier

Een MeshCore-repeater voert een CLI-commando alleen uit voor een client die hij
als **admin** beschouwt (`handleCommand` wordt vanuit `onPeerDataRecv` alleen
bereikt onder `client->isAdmin()`), en tegen een client die dat niet is zegt hij
helemaal niets.

Dus een alleen-lezen monitor logt **perfect** in, stuurt achttien commando's, en
hoort achttien stiltes — wat er precies uitziet als een node die buiten bereik
is. Dat is de meest verwarrende manier waarop het in dit hele gebied misgaat, en
de site hoort het bij naam te noemen in plaats van "geen antwoord" te melden en
de operator te laten gissen.

Drie toestanden die het onderscheiden waard zijn op de pagina:

| Toestand | Hoe het eruitziet | Hoe je het oplost |
|---|---|---|
| Geen rechten | de login krijgt helemaal geen antwoord, precies als buiten bereik | de overkant voegt `setperm <our-pubkey> 1` toe |
| Alleen-lezen | de login lukt, elk commando wordt met stilte beantwoord | `setperm <our-pubkey> 3`, of het beheerderswachtwoord |
| Admin | commando's antwoorden | — |

De gehoorde lijst is wat "geen rechten" van "buiten bereik" scheidt: horen we
zijn adverts, dan kunnen we hem bereiken.

Een leeg wachtwoord in de monitorlijst is een keuze en geen weglating — het zorgt
ervoor dat de overkant de wachtwoordcontrole overslaat en in plaats daarvan onze
publieke sleutel opzoekt in zijn toegangslijst. Dat is de nettere afspraak:
niemand deelt een wachtwoord uit, en de andere operator kan ons in zijn eentje
intrekken.

---

## Telemetrie zonder inloggegevens

Kan de site iets bruikbaars uitlezen van een repeater waarvoor hij geen
wachtwoord heeft? Dat is uitgezocht voordat er ook maar iets gebouwd werd, want
het antwoord bepaalt of de functie de moeite waard is. **Dat is hij: er komt
nogal wat meer uit dan verwacht.**

### Wat de broncode zegt

Inloggen is altijd verplicht — `handleLoginReq()` in
`examples/simple_repeater/MyMesh.cpp` geeft `0` terug (helemaal geen antwoord)
voor alles wat hij niet accepteert. Maar er zijn drie manieren om geaccepteerd te
worden, en de derde is de interessante:

1. **Een leeg wachtwoord + de afzender staat in de ACL.** De operator heeft je
   daar met `setperm <pubkey> 1` in gezet. Dat is de nette afspraak die de
   monitorlijst al gebruikt.
2. **Het beheerderswachtwoord.**
3. **Het gastwachtwoord** — en `guest_password[0] = 0` in `CommonCLI.h:189`, dus
   het is **standaard leeg**. Een leeg wachtwoord komt daar dus mee overeen.

Dat derde pad is het antwoord. Op een standaardrepeater waarvan de operator nooit
`set guest.password` gedraaid heeft, wordt een onbekende node die met een leeg
wachtwoord inlogt geaccepteerd als `PERM_ACL_GUEST`.

### Wat een gast dan mag opvragen

| Verzoek | Krijgt een gast het | Wat erin zit |
|---|---|---|
| `REQ_TYPE_GET_STATUS` `0x01` | **ja**, helemaal geen rechtencontrole — de broncode zegt het zelfs met zoveel woorden: *"guests can also access this now"* (gasten kunnen hier nu ook bij) | De volledige `RepeaterStats`: uptime, zendtijd, TX/RX-tellers, verdeling flood/direct, duplicaten, foutvlaggen, batterijspanning in millivolt, ruisvloer, laatste RSSI en SNR, lengte van de wachtrij |
| `REQ_TYPE_GET_TELEMETRY_DATA` `0x03` | **ja, maar beperkt** — `perm_mask` wordt voor een gast op `0x00` gedwongen | Batterijspanning en MCU-temperatuur. Externe sensoren worden achtergehouden |
| `REQ_TYPE_GET_NEIGHBOURS` `0x06` | **ja**, geen rechtencontrole | De burenlijst |
| `REQ_TYPE_GET_ACCESS_LIST` `0x05` | nee — `&& sender->isAdmin()` | — |
| CLI-commando's (`get`/`set`) | nee — `handleCommand` wordt alleen bereikt onder `client->isAdmin()` | — |

Een uitvraag zonder inloggegevens levert dus ongeveer op wat deze site voor een
gemonitorde repeater al laat zien, min de CLI-instellingen. Dat is een echte
functie en geen troostprijs.

### Twee bevindingen die het opschrijven waard zijn

**`allow.read.only` doet niets op een repeater.** Hij wordt opgeslagen, hij is
via de CLI te lezen en te schrijven, en de enige code die hem raadpleegt is
`examples/simple_room_server/MyMesh.cpp:351`. Op een repeater is het een inerte
instelling. Hij is hier toch ingedeeld als gevaarlijke parameter, want dezelfde
module kan gebouwd worden voor een room server waar hij de toegang *wel* regelt —
maar niemand moet verwachten dat hem omzetten op een repeater verandert wie hem
mag bevragen.

**Een weigering is stil.** `handleLoginReq` geeft `0` terug en stuurt niets. Een
repeater waarvan de operator *wel* een gastwachtwoord gezet heeft, is vanaf hier
dus niet te onderscheiden van een die buiten bereik of uitgeschakeld is. Die
dubbelzinnigheid is echt en valt niet op te lossen door het harder te proberen;
ze valt alleen eerlijk te melden.

### Wat dit betekent voor het ontwerp

Drie toestanden, en de pagina mag ze niet op één hoop gooien:

| Uitkomst | Wat het betekent |
|---|---|
| Antwoord gekregen | Telemetrie is zonder inloggegevens beschikbaar |
| Geen antwoord | **Een van deze**: buiten bereik, gastwachtwoord gezet, firmware te oud. Niet te onderscheiden |
| Eerder wel geantwoord, nu stil | Er is iets veranderd — dat is het waard om anders te tonen dan nooit-geantwoord |

De gehoorde lijst is het enige wat de middelste regel versmalt: komen de adverts
van de node nog binnen, dan wordt "buiten bereik" onwaarschijnlijk en "niet
toegestaan" waarschijnlijk. Dat is een aanwijzing en geen bewijs, en het hoort
ook zo geformuleerd te worden.

### Terughoudendheid, met opzet

Dit is het bevragen van **andermans apparatuur** op een gedeelde band, dus:

- **Geen automatische ronde langs alles wat gehoord is.** Een ontdekking die
  aanklopt bij elke node die hij ooit gehoord heeft, is precies het gedrag dat
  een gedeeld mesh niet nodig heeft. De operator kiest wie er bevraagd wordt.
- **De kosten aan zendtijd staan er vóór de knop ingedrukt wordt**, niet erna.
- **Op deze manier monitoren is alleen telemetrie.** Een node die zonder
  inloggegevens bevraagd wordt, krijgt geen beheerknoppen, want die zijn er niet
  te geven — de CLI is dicht voor gasten. Dat wordt door de overkant afgedwongen,
  en dat is de beste soort afdwinging.

### Niveau, of een eigenschap ernaast?

**Een eigenschap naast het niveau, geen vierde niveau.** `unmanaged` /
`semi_managed` / `full_managed` beantwoordt de vraag "wat mogen we met deze node
*doen*", en telemetrie opvragen is niets met hem doen — je vraagt het, en de node
beslist. Een node die we om telemetrie vragen is nog steeds `unmanaged`: we
kunnen er geen enkele instelling op wijzigen, we kunnen er geen firmware naartoe
schrijven, en we hebben geen inloggegevens.

Er een vierde niveau van maken zou bovendien de volgorde breken die de niveaus nu
hebben, en dat is er een van oplopende mogelijkheden. Telemetrie opvragen is niet
"meer dan unmanaged en minder dan semi-managed" — een `full_managed` node kun je
op dezelfde manier bevragen. Het is een andere as, dus krijgt het een ander veld.

---

## Werken zonder internet

Dit project bestaat mede voor noodcommunicatie, dus "werkt het nog als het
internet weg is" is geen nieuwsgierigheid — het is een eis die iemand moet kunnen
controleren *voordat* hij hem nodig heeft. Het eerlijke antwoord heeft drie
lagen, en dat is niet drie keer dezelfde vraag.

### Laag 1 — geen internet, lokaal netwerk intact

**Bijna alles werkt.** De server, de broker, de database en het mesh zijn
allemaal lokaal; geen van alle heeft het internet nodig om zijn werk te doen.

| Werkt | Werkt niet |
|---|---|
| Statistieken binnenhalen over MQTT | **Firmware-releases ophalen bij GitHub** |
| CLI-instellingen lezen, over MQTT en over LoRa | Kaarttegels, als de kaartaanbieder extern is |
| **CLI-instellingen schrijven**, over allebei de transporten | |
| De klok zetten | |
| De hele beheerinterface, de vergelijkingstabel, het pakketarchief | |
| Een firmware-image doorzetten die de server **al gedownload heeft** | |

Het enige echte slachtoffer is de firmwarepagina. Hij somt releases op door het
aan `api.github.com` te vragen, en zonder internet mislukt die aanroep. De pagina
blijft werken — hij toont de laatste lijst die hij nog wist op te halen, met de
reden ernaast — maar een release die verscheen terwijl je offline was, is niet
zichtbaar, en een image dat nooit gedownload is, kun je niet installeren.

Dat is een reden om **op te halen vóór je het nodig hebt**, geen reden om de rest
van de site te wantrouwen. Niets anders op deze lijst raakt een externe host aan.

### Laag 2 — ook geen lokaal netwerk

De site bereikt *geen enkele* node meer, via welk transport dan ook. Dat is het
waard om ronduit te zeggen, want "geforceerd over het mesh" klinkt alsof het hier
zou moeten helpen, en dat doet het niet:

> **Het mesh-transport kiezen neemt niet weg dat de site zelf één node nodig
> heeft die over IP bereikbaar is.** Het neemt weg dat het *doel* bereikbaar moet
> zijn. De server moet het commando nog steeds aan de een of andere node
> overhandigen, over MQTT of over HTTP, en die node draagt het daarna over LoRa
> verder.

Met het LAN plat is de weg terug naar binnen dus helemaal deze site niet — het is
de mesh-CLI vanuit een companion-app op een telefoon, over bluetooth of over
LoRa. Dat pad liep nooit via de server en heeft nergens hier last van.

### Laag 3 — waar het mesh-transport eigenlijk voor is

Gezien laag 2, waarom het dan überhaupt aanbieden? Omdat het interessante geval
niet "het netwerk is weg" is, maar "**het doel hangt niet aan het netwerk**":

- Een repeater op een dak zonder WiFi in de buurt — de blijvende toestand van de
  dakrepeater in deze installatie.
- Een node waarvan de WiFi-gegevens gewijzigd zijn, of waarvan het accesspoint
  het begeven heeft, terwijl de radio kerngezond is.
- Een node in energiebesparingsstand met zijn WiFi in slaap, die LoRa nog wel
  hoort.

In alle drie is de site online, draait de broker en is er één node bereikbaar —
en het doel niet. Dat is het geval waarvoor het mesh-transport bestaat, en het is
het gewone geval.

Het is ook waarom het mesh forceren voor een node die over IP *wel* bereikbaar is
de moeite waard is: het is de enige manier om het LoRa-schrijfpad te beproeven
tegen een node die je nog met een browser kunt herstellen als het misgaat.

---

## Ontdekken: eerst aanwijzen, dan één keer proberen

De site ziet elke node in het verkeer, dus hij zou ze allemaal kunnen proberen om
uit te vinden waar hij rechten heeft. **Dat hoort hij niet te doen**, en dat is
een beleidskeuze en geen technische grens.

- Het kost zendtijd op een gedeelde band, voor een vraag die niemand stelde.
- Het klopt aan bij andermans apparatuur. Een inlogpoging op de repeater van een
  vreemde is op zijn best onbeleefd en op zijn slechtst niet te onderscheiden van
  iemand die hem staat af te tasten.
- Het antwoord veroudert toch, want rechten worden aan de overkant gegeven.

In plaats daarvan: **de operator wijst een node aan** — uit de gehoorde lijst of
door een publieke sleutel te plakken — geeft het inloggegeven dat van toepassing
is, en de site probeert het **één keer**, en onthoudt daarna de uitkomst. Eén
bewuste klop, op een moment dat een mens koos, op een node die een mens noemde.

---

## Wat gebouwd is en wat niet

| | Toestand |
|---|---|
| CLI-instellingen lezen over LoRa via een monitor | **gebouwd**, en dat al een tijd (`wifi mon settings <key>`, `settings <key>` op het `cmd`-topic) |
| Niveaus als expliciet begrip in code en UI | **wordt gebouwd** — `level` / `level_why` op `commanding.describe()` |
| Firmware-upgrade over HTTP, met checksum en rollback | **gebouwd**, zie [`firmware-upgrade.md`](firmware-upgrade.md) |
| `ota_route()` als aparte sleutel voor wat er kan | **gebouwd** |
| Instellingen schrijven naar een `full_managed` node met een IP-pad | **gebouwd** — firmware 2.1.0 `POST /api/cfg`: het hele CLI-oppervlak op drie na, bediening per type, bevestiging per risicoklasse, met teruglezen. Vereist dat het beheeradres van de node ingevuld is |
| Instellingen schrijven naar een `semi_managed` node over LoRa | **ontworpen, niet gebouwd.** Vraagt om een toestandsmachine naast de instellingenronde, en de node waarvoor het bestaat is de dakrepeater — dus het wordt eerst gebouwd tegen iets wat iemand kan aanraken |
| Schrijven naar de WiFi- en MQTT-instellingen van een node | **wordt hier niet aangeboden.** Dat zijn de onze en niet die van MeshCore, en ze hebben al hun eigen formulieren op de beheerpagina van de node zelf en in de `wifi`-CLI |
| Bevestigen-of-terugdraaien | **onderzocht en verworpen**, met de redenering hierboven |
| Automatisch rechten ontdekken | **verworpen**, in plaats daarvan aanwijzen-en-één-keer-proberen |
| Vergelijkingstabel over repeaters heen | **gebouwd** — `/admin/compare`, gekozen kolommen, afwijkingen van de meerderheid gemarkeerd |
| Bewerken vanuit die tabel | **niet gebouwd.** Het ontwerp is één bewerkveld dat door de tabel aangestuurd wordt, in plaats van een invoerveld in elke cel; zie hieronder |
| Meerdere nodes tegelijk bewerken | **niet gebouwd, en in het ontwerp al ingeperkt**: alleen parameters uit de klasse Gewoon, nooit de twee zwaardere klassen. Tien nodes in één klik is ook tien nodes kwijt in één klik |
| Mesh-transport forceren voor een node die een IP-pad heeft | **niet gebouwd.** Vraagt eerst om het LoRa-schrijfpad, en dat vraagt om een relais dat het doel monitort |
| Telemetrie opvragen zonder inloggegevens | **onderzocht, niet gebouwd.** Het werkt en levert meer op dan verwacht — zie hierboven |

> Zolang hieraan gewerkt wordt, wordt er **helemaal niet naar JessaZH
> geschreven** — geen test-`set`, niets. Hij wordt alleen over LoRa bereikt, dus
> een vergissing daar is niet te herstellen, en hij is bovendien het
> referentiegeval waarvoor dit ontwerp bestaat. Schrijfpaden worden getest tegen
> een node die iemand fysiek kan aanraken.
