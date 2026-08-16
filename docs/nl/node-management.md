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
| **CLI-instellingen schrijven** | nee | ontworpen, begrensd | ontworpen, begrensd |
| De klok zetten | nee | ja, via de monitor | ja |
| Firmware-upgrade | nee | **nee** | alleen met een IP-pad |

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
de categorietabel hieronder als datgene wat overeind moet blijven.

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

Geen toelatingslijst omdat toelatingslijsten in de mode zijn, maar omdat de
manier waarop het hier misgaat is dat je een node blijvend kwijtraakt. Drie
categorieën.

### Categorie 1 — veilig. **Gebouwd, en dit is wat er uitgeleverd wordt.**

Kan langs geen enkele route de bereikbaarheid afsnijden. Wordt aangeboden op elke
node waarnaar de site kan schrijven, zonder extra bevestiging.

| Sleutel | Type | Onze grenzen | MeshCore's eigen validatie |
|---|---|---|---|
| `name` | tekst | niet leeg, geen `[ ] \ : , ? *`, geen stuurtekens | weigert dezelfde leestekens (`isValidName`), staat stuurtekens toe |
| `lat` | float | −90 … 90 | **helemaal geen** — kale `atof()` |
| `lon` | float | −180 … 180 | **helemaal geen** — kale `atof()` |
| `advert.interval` | int | 60 … 240 minuten | 0, of 60 … 240 |
| `flood.advert.interval` | int | 3 … 168 uur | 0, of 3 … 168 |
| `rxdelay` | float | 0 … 20 | 0 … 20 |
| `txdelay` | float | 0 … 2 | 0 … 2 |

De slechtste afloop in deze categorie is een node die minder vaak adverteert of
anders vertraagt. Allebei zichtbaar in de statistieken, en allebei te corrigeren
via dezelfde route die ze gezet heeft.

Twee van onze grenzen zijn met opzet strenger dan die van MeshCore. Dat
accepteert `0` voor allebei de advert-intervallen, wat "stop met adverteren"
betekent — daarmee wordt een node niet onbereikbaar, maar hij zakt er wel mee uit
ieders lijst weg, en op een dak voelt dat hetzelfde. En `af` (airtime factor) is
tijdens het bouwen uit deze categorie geschrapt: MeshCore valideert die ook niet,
en een hoge waarde knijpt het zenden zo ver af dat een repeater stilvalt zonder
ooit onbereikbaar te worden — precies het soort halfkapot dat deze categorie
hoort uit te sluiten.

Bij `lat` en `lon` is het de moeite waard even stil te staan, want zij laten het
duidelijkst zien waarom die grenzen hier überhaupt bestaan. De handler van
MeshCore is `_prefs->node_lat = atof(&config[4]);` — geen bereikcontrole, geen
parsecontrole. `atof("noord")` is `0.0`, dus een typfout zet de node in de Golf
van Guinee en de CLI antwoordt `OK`. **Een node die een onzinnige waarde aanneemt
is gevaarlijker dan een die weigert**, en MeshCore is van het aannemende soort.

### Waar de lijst werkelijk staat

**In de firmware**, meegecompileerd (`CFG_PARAMS` in `MeshManagerNet.cpp`). Niet in
de server, want de server is aan te passen door wie de site draait, en deze lijst
is wat er tussen een klik en de radio staat.

De server houdt er **geen tweede kopie** van bij. Hij vraagt aan de node
(`GET /api/cfg`) welke sleutels die toestaat en tussen welke grenzen, bouwt het
formulier op uit dat antwoord, en valideert daartegen voordat er iets vertrekt.
Dat voldoet nog steeds aan "valideer aan beide kanten" — de controle van de
server geeft snel een fout naast het invoerveld, de controle van de node is
degene die telt — maar er is altijd maar één lijst, zodat de twee niet uit elkaar
kunnen groeien en een parameter gaan aanbieden die de node weigert.

### Categorie 2 — riskant, achter een expliciete bevestiging die het risico benoemt

`flood.max`, `flood.max.unscoped`, `repeat`, `allow.read.only`

Deze veranderen hoe de node aan het mesh deelneemt. Ze verbreken de beheerroute
niet uit zichzelf, maar ze kunnen het beeld van het mesh genoeg veranderen dat
het volgende probleem lastiger te diagnosticeren wordt. `allow.read.only`
verandert daarbij wie er mag inloggen — mogelijk ook wij.

### Categorie 3 — nooit op afstand op een node die alleen over LoRa bereikt wordt

`freq`, `radio` (bandbreedte / spreading factor / coding rate), `tx`, `role`,
`region.*`, en alles wat met WiFi te maken heeft.

Elk van deze kan van kracht worden en op datzelfde moment de enige weg terug
wegnemen. Er is geen bevestiging die daarbij helpt: die bevestiging zou moeten
reizen over de verbinding die de wijziging net kapotmaakte.

Ze mogen aangeboden worden op een node met **twee onafhankelijke paden** — onze
firmware met zowel een IP-route als een mesh-route, waar het breken van de ene de
andere overlaat — en zelfs dan achter dezelfde bevestiging als categorie 2. Op
een `semi_managed` node worden ze helemaal niet aangeboden.

### De endpoints

Allebei achter de eigen HTTP-login van de node, dezelfde die `/api/fw` en
`/api/backup` bewaakt.

**`GET /api/cfg`** — wat deze image toestaat, zodat de pagina nooit een sleutel
aanbiedt die de firmware niet heeft:

```json
{"params":[{"key":"name","kind":"text","lo":0,"hi":0,"tier":1},
           {"key":"lat","kind":"float","lo":-90,"hi":90,"tier":1}]}
```

**`POST /api/cfg`** met formuliervelden `key` en `value`:

```json
{"ok":1,"step":"","key":"advert.interval","asked":"61",
 "applied":"60","exact":0,"reply":"OK"}
```

`step` is bij een mislukking `sleutel` (staat niet op de lijst), `waarde` (buiten
de grenzen) of `node` (de CLI weigerde), en nooit alleen maar `error`.

De sleutel wordt bij het opbouwen van het commando nooit uit het verzoek
overgenomen — hij wordt opgezocht in de meegecompileerde tabel en de spelling van
die tabel wordt gebruikt — zodat er behalve de waarde geen enkele tekst van de
aanroeper in het commando zit, en die waarde is altijd het laatste woord. De
CLI-aanroep geeft ook een **sender-timestamp ongelijk aan nul** mee: `0` betekent
in MeshCore "dit kwam van de seriële kabel" en ontgrendelt commando's die alleen
daar thuishoren (`erase`, `get prv.key`). Dit pad heeft er geen enkele van nodig,
dus mocht de tabel ooit een gat blijken te hebben, dan is dat gat kleiner.

### Validatie gebeurt aan beide kanten, en het is niet dezelfde controle

- **De server** valideert type en bereik voordat er iets vertrekt, zodat een
  typfout een weigering naast het invoerveld oplevert in plaats van een pakket.
  Hij valideert tegen de grenzen die de *node* opgaf, niet tegen een eigen lijst.
- **De node** valideert nog een keer voordat hij `set` uitvoert. Dit is de
  controle die het mesh werkelijk beschermt, want de server is aan te passen door
  wie de site draait, en de tabel van de firmware zit meegecompileerd.

Bij een `semi_managed` doel — het pad dat ontworpen is maar niet gebouwd — zou de
uitzendende node de monitor zijn, met onze firmware, zodat de tabel en de
validatie ervan gelden voordat er iets de lucht in gaat. Het doel is daar
standaard MeshCore en valideert bijna niets: `set` parseert met `atof`/`atoi` en
neemt wat het krijgt. **Een node die een onzinnige waarde aanneemt is gevaarlijker
dan een die weigert**, en de standaardfirmware is van het aannemende soort.
Precies daarom moet de weigering vóór het uitzenden gebeuren en niet aan de
overkant.

---

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

De moeite gaat dus naar de categorietabel, die de wijziging voorkómt in plaats
van hem terug te draaien. Een preventie die op standaardfirmware werkt is beter
dan een rollback die alleen werkt waar hij niet nodig is.

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
| Instellingen schrijven naar een `full_managed` node met een IP-pad | **gebouwd** — firmware 1.13.0 `POST /api/cfg`, alleen categorie 1, met teruglezen. Vereist dat het beheeradres van de node ingevuld is |
| Instellingen schrijven naar een `semi_managed` node over LoRa | **ontworpen, niet gebouwd.** Vraagt om een toestandsmachine naast de instellingenronde, en de node waarvoor het bestaat is de dakrepeater — dus het wordt eerst gebouwd tegen iets wat iemand kan aanraken |
| Parameters uit categorie 2 (`flood.max`, `repeat`, `allow.read.only`, …) | **niet gebouwd.** Het `tier`-veld staat in de firmwaretabel, dus ze toevoegen is een tabelregel plus een bevestigingsstap, geen verbouwing |
| Bevestigen-of-terugdraaien | **onderzocht en verworpen**, met de redenering hierboven |
| Automatisch rechten ontdekken | **verworpen**, in plaats daarvan aanwijzen-en-één-keer-proberen |

> Zolang hieraan gewerkt wordt, wordt er **helemaal niet naar JessaZH
> geschreven** — geen test-`set`, niets. Hij wordt alleen over LoRa bereikt, dus
> een vergissing daar is niet te herstellen, en hij is bovendien het
> referentiegeval waarvoor dit ontwerp bestaat. Schrijfpaden worden getest tegen
> een node die iemand fysiek kan aanraken.
