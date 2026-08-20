# Nodes beheren vanaf de site

*[English](../node-management.md)*

Een doorlopende handleiding bij alles wat de site met een node kan doen, in de
volgorde waarin je het nodig hebt: herkennen wat een node is, hem onder beheer
brengen, zijn instellingen lezen en wijzigen, zijn klok zetten, hem nieuwe
firmware geven — en wat je doet op de dag dat hij niet terugkomt. En overal
doorheen het deel dat de meeste woorden kost: wat de site met opzet níét doet.

Een node uitlezen is uitgekristalliseerd en werkt al lang. **Schrijven** is nu
langs beide wegen gebouwd — over IP naar een node die de server zelf bereikt, en
over LoRa via een monitor naar een node die hij niet bereikt — en een handvol
dingen eromheen is nog ontworpen en uitdrukkelijk niet gebouwd; bij elke sectie
staat welke van de twee. Dat onderscheid weegt hier zwaarder dan in de meeste
documentatie, want de faalmodus is geen kapotte pagina. Het is een repeater op
een dak die niemand nog kan bereiken.

De schermafbeeldingen komen van een wegwerpinstantie met verzonnen nodes, nooit
van een draaiende installatie — zie
[`contributing.md` §10](contributing.md#10-documentatieconventies) voor hoe je ze
opnieuw maakt.

---

## Inhoud

**Weten wat je hebt** — [de nodepagina](#begin-bij-de-nodepagina) ·
[de drie niveaus](#de-drie-niveaus) ·
[wat er per niveau mogelijk is](#wat-er-per-niveau-mogelijk-is) ·
[een node onder beheer brengen](#een-node-onder-beheer-brengen)

**Instellingen** — [uitlezen](#instellingen-uitlezen) ·
[drie vervoermiddelen](#drie-vervoermiddelen-voor-één-schrijfweg) ·
[welke geschreven mogen worden](#welke-instellingen-geschreven-mogen-worden) ·
[schrijven over LoRa](#schrijven-over-lora-via-de-monitor) ·
[bevestigen-of-terugdraaien](#bevestigen-of-terugdraaien-onderzocht-en-met-opzet-niet-gebouwd) ·
[teruglezen](#lees-na-een-schrijfactie-terug)

**Het apparaat** — [het pakketfilter](#het-pakketfilter) ·
[de klok](#de-klok-zetten) ·
[firmware en terugrollen](#firmware-upgraden-en-terugrollen) ·
[als een node niet terugkomt](#als-een-node-niet-terugkomt)

**Als het niet werkt** — [rechten](#rechten-zijn-het-scharnier-en-ze-falen-op-een-verwarrende-manier) ·
[telemetrie zonder inloggegevens](#telemetrie-zonder-inloggegevens) ·
[zonder internet](#werken-zonder-internet) ·
[ontdekken](#ontdekken-eerst-aanwijzen-dan-één-keer-proberen) ·
[gebouwd en niet gebouwd](#wat-gebouwd-is-en-wat-niet)

---

## Begin bij de nodepagina

`/admin` is de enige pagina die in één scherm antwoordt op "wat heb ik, en wat
kan ik ermee". Hij sorteert niet op naam of op laatst gezien. Hij groepeert op
**beheerniveau**, omdat wat je met een node kúnt doen per groep verschilt en
niets anders op die pagina de knoppen eronder verklaart.

![De beheerpagina 'Nodes en repeaters' met vijf verzonnen nodes in drie groepen. Full managed — 2 bevat Voorbeeld-Thuisnode en Voorbeeld-Zendmast, allebei met bron 'zichzelf', firmware v1.16.0 + 1.10.0 en weg 'MQTT'. Semi-managed — 1 bevat Voorbeeld-Dakrepeater, bron 'via bb11bb11bb11', weg 'via monitor'. Unmanaged — 2 bevat Voorbeeld-Buurnode en Voorbeeld-Veldpost, allebei met weg 'geen'. Bij elke groep staat een alinea die uitlegt wat dat niveau betekent, en bij elke node een zin die zegt waaraan het niveau waargenomen is.](../images/beheer-nodes-overzicht.png)

Drie dingen op die pagina verdienen het om vooraf benoemd te worden.

**De zin onder elke node is het bewijs.** "publiceert zelf over MQTT met
nodefirmware 1.10.0", "bereikbaar via Voorbeeld-Thuisnode over LoRa", "alleen
waargenomen in het verkeer". Dat is `level_why`, en het noemt de node die het
niveau mogelijk maakt. Zonder die naam is "semi-managed" een etiket waar niemand
iets mee kan.

**"Weg nu" is niet het niveau.** Dat zegt wat er op dit ogenblik van deze machine
kan vertrekken. Een full managed node achter een net weggevallen broker blijft
full managed; er is alleen nu geen weg. Het zijn met opzet twee losse sleutels,
en [`commanding.md`](commanding.md) legt uit waarom het niveau de
brokerverbinding volledig negeert.

**Een verborgen node telt ook mee.** Een repeater die vanzelf uit een
binnengekomen bericht ontstaat komt verborgen binnen — publiceerrechten op een
topic zijn geen publiceerrechten op de voorpagina — en de melding bovenaan zegt
hoeveel er op die beslissing staan te wachten.

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

### Het niveau van één node zien

Open een node en het niveau staat bovenaan, boven de sleutelprefix, omdat het de
vraag beantwoordt die je hebt vóór je naar beneden scrolt: wat kan ik hier
eigenlijk mee?

![De beheerpagina van Voorbeeld-Dakrepeater. Naast de titel staat een label SEMI-MANAGED, en onder de kop 'Identiteit en versies' herhaalt een amberkleurig omrande kaart dat label met 'waargenomen: bereikbaar via Voorbeeld-Thuisnode over LoRa' en een alinea die uitlegt wat semi-managed toelaat. Daaronder een tabel met sleutelprefix aa00aa00aa00, slug, 'Bron van de cijfers: doorgestuurd door node bb11bb11bb11', laatst gezien, MeshCore-firmware v1.16.0, en een leeg veld voor de nodefirmware met de opmerking dat de site zonder die versie niets naar deze node stuurt.](../images/beheer-node-semi-managed.png)

De lege rij **Nodefirmware (MeshManager)** op die afbeelding is geen
schoonheidsfoutje. Dat veld beslist of de knoppen verderop überhaupt iets mogen
versturen — opdrachten vanaf 1.8.0, een gemonitorde repeater uitvragen vanaf
1.9.0, de klok vanaf 1.10.0. Wie zich afvraagt waarom een knop uit staat, kijkt
hier eerst.

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
| **CLI-instellingen schrijven** | nee | **gebouwd** — over LoRa via de monitor, hetzelfde oppervlak | **gebouwd** — het hele oppervlak, met risicoklassen |
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
moment dat hij die knop nodig heeft. Op een `unmanaged` node staat elke handeling
dus nog op de pagina, uit, elk met een eigen zin:

![De secties 'Uitvragen' en 'Klok' van de unmanaged node Voorbeeld-Buurnode. De instellingentabel is leeg. Drie knoppen staan grijs — 'Opvragen kan nu niet', 'Status opvragen kan nu niet' en 'Synchroniseren kan nu niet' — en bij elk staat de reden: 'Geen van beide wegen staat open', 'de node meldt geen firmwareversie, dus valt niet vast te stellen of hij opdrachten aanneemt' en hetzelfde voor de klok. Een regel meldt dat nog nooit iemand /api/v1/commands opgehaald heeft.](../images/beheer-node-unmanaged.png)

Merk op dat de redenen van elkaar verschillen. "Geen van beide wegen staat open"
en "de node meldt geen firmwareversie" zijn verschillende problemen met
verschillende oplossingen, en één grijze knop die niets zegt had ze allebei
verborgen.

---

## Een node onder beheer brengen

Je verhoogt geen niveau. Je verandert de wereld, en het niveau volgt bij het
volgende bericht. Er zijn precies twee dingen te veranderen.

### Naar `semi_managed`: rechten op zijn CLI

Een MeshCore-repeater voert een CLI-commando alleen uit voor een cliënt die hij
als **admin** beschouwt. Wie die repeater beheert, geeft dat aan zijn kant:

```
setperm <onze-publieke-sleutel> 3
```

`1` is alleen-lezen en is niet genoeg — zie
[Rechten zijn het scharnier](#rechten-zijn-het-scharnier-en-ze-falen-op-een-verwarrende-manier)
waarom juist die halve maatregel de meest verwarrende toestand in dit hele gebied
is. Het alternatief is het adminwachtwoord van de repeater overhandigen; dat
werkt en is slechter: een wachtwoord wordt gedeeld, een recht kan de andere
beheerder in zijn eentje intrekken.

Aan onze kant moet de monitorende node ervan weten. De monitorlijst staat op de
node en niet op de site (`wifi mon add <sleutel>`), en de monitor moet
nodefirmware **1.9.0** of hoger draaien, want daar is `settings <sleutel>` — hún
CLI over LoRa ophalen — bijgekomen. Een oudere monitor kent het `cmd`-topic wel
maar weigert het argument en telt de opdracht als geweigerd, en daarom weigert de
site te gokken en toont hij `old_fw`.

### Naar `full_managed`: onze firmware plus MQTT

Twee voorwaarden, allebei noodzakelijk:

1. De node draait de MeshManager-firmware en publiceert zijn eigen statistieken —
   zie [`firmware.md`](firmware.md) voor bouwen en flashen, en
   [`mqtt.md`](mqtt.md) voor de topics en het brokeraccount per node.
2. Hij meldt in die berichten een `fw_meshmanager`-versie. Zonder die versie kan
   de site niet vaststellen dat het `cmd`-topic op die node bestaat, en een
   opdracht die het luchtledige in gepubliceerd wordt is precies de oneerlijkheid
   waar het niveau tegen bestaat.

Onze firmware op een node krijgen die alleen over LoRa bereikbaar is, kan van
hieruit niet en zal nooit kunnen — 1,3 MB tegen de duty-cycle-limiet is dágen
zendtijd. Zo'n node wordt over USB geflasht, ter plaatse. Dat is de hele reden
dat `semi_managed` een niveau is en geen wachtkamer.

### Controleren of het gelukt is

Herlaad `/admin`. De node staat in een andere groep, en de zin eronder noemt het
nieuwe bewijs. Dát is de bevestiging — geen succesmelding, maar de eigen lezing
van de site van wat er nu waar is.

Staat hij er nog, dan zegt die zin wat er nog ontbreekt, en dat is bijna altijd
één van drie dingen: geen gemelde firmwareversie, een monitor onder 1.9.0, of
sinds de wijziging is er niets gepubliceerd.

---

## Instellingen uitlezen

Lezen is uitgekristalliseerd, werkt vandaag, en is op beide beheerde niveaus
hetzelfde mechanisme. Het verschilt alleen in wie er gevraagd wordt.

![De sectie 'Uitvragen' van Voorbeeld-Dakrepeater. Een tabel toont vijftien CLI-parameters met hun waarde en hoelang geleden elk opgehaald is — advert.interval 240, af 1.0, allow.read.only off, cmd:region met '(geen antwoord)' in het grijs, flood.max 3, freq 869.525, radio 869.525,250,11,5, repeat on, role repeater, tx 22 en meer. Daaronder een blauw omrand blok 'Instellingen nu opvragen' met het label 'kost zendtijd', dat uitlegt dat node bb11bb11bb11 deze repeater monitort en hem over LoRa kan uitvragen, met een werkende knop. Een tweede blok voor een verse status staat grijs, omdat een doorgestuurde repeater niet gevraagd kan worden zelf te publiceren.](../images/beheer-node-instellingen.png)

Drie dingen die deze afbeelding laat zien.

**`cmd:region` toont "(geen antwoord)" en geen verouderde waarde.** Een sweep die
voor één parameter geen antwoord krijgt, zegt dat. De laatst bekende waarde tonen
zou een onbeantwoorde vraag op een vers feit laten lijken, en over een radiolink
is dat verschil eerder regel dan uitzondering.

**Bij de knop staat "kost zendtijd".** Een sweep is een stuk of vijftien
commando's en vijftien antwoorden over een gedeelde band, één voor één met
ademruimte ertussen. Het is een leesactie — er verandert niets op het apparaat —
maar gratis is hij niet, en de pagina beprijst hem daarnaar. Reken op **2 à 5
minuten** via een monitor, en op minder dan een halve minuut rechtstreeks.

**"Status opvragen" staat uit, om een reden die geen storing is.** Een
doorgestuurde repeater publiceert niet; zijn cijfers komen op de rondes van de
monitor binnen. Hem om een verse status vragen is geen ding dat bestaat, dus zegt
de knop dat in plaats van te doen alsof.

Wélke parameters er opgehaald worden is één lijst voor alle repeaters, op
`/admin/server#cli-params` — niet per node, want een lijst per node wekt de
indruk dat je aan één node iets bijzonders kunt vragen.

---

## Drie vervoermiddelen voor één schrijfweg

Het korte antwoord, omdat het rechtstreeks gevraagd is: **ja, ook over MQTT —
sinds nodefirmware 2.8.0, en met opzet niet door het `cmd`-topic tot een CLI te
verbreden.**

Er is één schrijfweg. Alles wat een schrijfactie kan weigeren — de parameterlijst
van de node zelf, zijn grenzen, de risicoklassen, de bevestiging, het recht uit
het RBAC-model, de terugleescontrole — gebeurt in `nodeconfig.write()`, ongeacht
welk vervoermiddel gebruikt wordt. Alleen *hoe het commando reist* verschilt. Per
node kiest de site de eerste van deze drie die werkelijk beschikbaar is, en de
nodepagina zegt welke het geworden is en waarom:

| # | Vervoermiddel | Beschikbaar als | Tegenpartij | Risicoklassen |
|---|---|---|---|---|
| 0 | De **eigen API** van de node (`POST /cli`) | er een `sensor_host` staat die een serverbeheerder heeft vastgelegd, `MM_FW_NODE_USER`/`MM_FW_NODE_PASS` gezet zijn, en `/status.json` minstens één keer geantwoord heeft | de weblogin van die node | 1, 2 en 3 |
| 1 | HTTP naar de node (`POST /api/cfg`) | hij een IP-pad heeft en `MM_FW_NODE_USER`/`MM_FW_NODE_PASS` gezet zijn | de weblogin van die node | 1, 2 en 3 |
| 2 | MQTT-`cmd`-topic (`set <param> <waarde>`) | de node zelf op MQTT publiceert, nodefirmware 2.8.0 draait, en de broker verbonden is | wie de broker heeft binnengelaten | 1 en 2 |
| 3 | Mesh-CLI via zijn monitor (`POST /api/moncfg`) | de node doorgestuurd wordt en zijn monitor een IP-pad, een weblogin en nodefirmware 2.4.0 heeft | de login van de monitor, daarna zijn eigen rechten op de doelnode | 1, 2 en 3 |

De volgorde is een rangschikking: sterkste tegenpartij en snelste, volledigste
teruglezing eerst; duurste laatst. Een doorgestuurde node heeft alleen regel 3 —
hij publiceert niet, dus hij heeft geen eigen `cmd`-topic. Een node die voor
zichzelf publiceert heeft regel 1 en 2. Een node met een eigen API heeft regel 0
en geen van de andere: hij draait onze firmware niet, publiceert niet op de broker,
en zolang de mesh-weg naar hem niet werkt stuurt geen monitor iets voor hem door.
Regel 0 staat daarom **vóór** regel 1 en niet uit voorkeur — hem achteraan zetten
zou de pagina eerst laten opsommen wat er allemaal niet is.

Is geen van de wegen beschikbaar, dan blijft dát het antwoord — met de reden per
vervoermiddel, zodat "niet mogelijk" iets is waar je mee verder kunt in plaats van
een doodlopende weg.

### Een node die zichzelf over IP laat beheren

De derde soort node, en hij is geen variant op de andere twee. Een
**MeshUptime-sensornode** biedt `/status.json`, `/cfg.json`, `/acl.json` en
`POST /cli` aan over HTTP, achter dezelfde basislogin die de firmwareweg al
gebruikt. Dat maakt hem *full managed*: de niveaus in `commanding.py` zijn een
waarneming, en deze weg scoort op elk punt dat het niveau weegt minstens zo hoog
als de MQTT-weg — een geauthenticeerde tegenpartij, synchroon, teruglezing in
hetzelfde verzoek, tienden van seconden, en geen derde partij die ervoor betaalt.

Wat de server ermee doet:

- **uitlezen**: `/status.json` elke `MM_SENSOR_POLL_S` (300 s, hetzelfde tempo als
  de bestaande polling en als `MM_HEARTBEAT_MIN`), en de kanalen bewaren onder de
  metricnamen die de repeaterfirmware al publiceert — `ch<N>_switch`,
  `ch<N>_generic`, `ch<N>_voltage`. Één naamruimte en niet twee: dezelfde node kan
  ook over LoRa binnenkomen, en twee namen voor één dienst zouden twee grafieken
  van hetzelfde tekenen met een knik op het moment dat er van weg gewisseld werd.
  De regel die daarbij het stilst misgaat reist mee: de responstijd gaat alleen
  mee als het kanaal *op* staat, precies zoals `querySensors()` doet, want een
  tijd bij een dode dienst is geen meting maar een oude waarde — en een grafiek
  die tijdens een storing gewoon doorloopt, wordt gelezen op precies het moment
  dat je de waarheid wilt;
- **de kanaalnamen vullen** uit wat de node meldt (`mon[].n` en `mon[].h`), met de
  eenheid `ms` waar een kanaal een responstijd kan dragen. Die eenheid is een
  uitspraak die alleen deze weg kan doen: `LPP_GENERIC_SENSOR` belooft vier byte
  en niets over betekenis. Een naam die een mens getypt heeft, wint altijd — zie
  [`database.md`](database.md);
- **de buren lezen** uit `/acl.json`, zodat de linkkaart en de burenlijst werken
  zonder de mesh-weg;
- **beheren**: advert (flood of zerohop), de klok, de regio, een herstart, en de
  instellingen uit `/cfg.json`. Instellingen lopen door `nodeconfig.write()` zoals
  elk ander vervoermiddel, met alle drempels onverkort.

**En hij zegt ronduit wat hij is.** Dit is WiFi en niet het mesh. Valt die
verbinding weg, dan valt deze hele weg weg — gemeten en niet theoretisch: in de
voedingsmeting van 19 augustus 2026 kreeg de node op batterij eerst met
tussenpozen geen antwoord (vier van de eerste vijftien pollingen) en daarna
veertien pollingen op rij niets, terwijl hij over LoRa gewoon bestond. Het mesh is
de weg die voor dat geval bedoeld is en die werkt nog niet: de ronde begint met
een login die geen antwoord krijgt, en de ronde is op een repeater afgestemd
terwijl een sensornode het statusverzoek niet implementeert en op een burenverzoek
letterlijk "not supported" antwoordt. De nodepagina zegt dat, in plaats van het te
verzwijgen.

**Eén parametertabel, gespiegeld.** Een sensornode publiceert geen parametertabel
— `/cfg.json` geeft *waarden* en geen keuring — en hij draait dezelfde MeshCore
CommonCLI als onze firmware. Wat er aangeboden wordt is daarom de `CFG_PARAMS` van
die firmware, gespiegeld in `sensornode.SPEC`, met een test die de C-broncode
uitleest en er regel voor regel tegenaan houdt. Twee plaatsen die het eens moeten
zijn is er één te veel; een test die het merkt zodra ze het niet meer zijn, is het
beste dat hier te krijgen is. Sleutels zonder terugleesbaar spiegelbeeld in
`/cfg.json` worden met opzet niet aangeboden (`dutycycle`, `guest.password`), met
de reden op de pagina erbij: zonder teruglezing zou de site "gelukt" moeten melden
op het woord van de node, en MeshCore antwoordt "OK" op dingen die het niet
werkelijk heeft overgenomen.

### Welke adressen de server mag benaderen

`ota_host` en `sensor_host` bepalen waarheen deze server een verbinding opent, en
hij stuurt `MM_FW_NODE_USER`/`MM_FW_NODE_PASS` mee in de `Authorization`-header.
Die inloggegevens openen **elke** node. Twee problemen in één veld, en ze zijn
beide ernstig: een doel dat de gebruiker koos (SSRF) en een geheim dat daarheen
gaat.

`node.beheeradres` is een *delegeerbaar* recht — een beheerder kan het per node
toekennen. Wie het had, kon tot deze reparatie het adres van zijn eigen server
invullen en de sleutel van de vloot ontvangen.

De gebruikelijke reparatie is "weiger private adressen": 127/8, 10/8, 172.16/12,
192.168/16, 169.254/16, ::1, fc00::/7. Dat werkt hier niet, want de nodes van dit
project *staan* op 192.168.x — dat ís wat een beheeradres is. Zo'n lijst zou de
functie afschaffen in plaats van beveiligen.

**Het onderscheid zit dus niet in het adres maar in wie het vastlegde.** Een
LAN-adres opgeven is inherent een beheerdersdaad: de server moet het kunnen
bereiken, en wie weet welk adres dat is, kent het netwerk waar de server op staat.
Dus:

- een adres **invullen** mag alleen een *serverbeheerder*. **Wissen** vraagt alleen
  `node.beheeradres`: een adres weghalen sluit een weg en kan er nooit een openen;
- bij het **verbinden** toetst `firmware.check_target` of dit adres werkelijk zo
  vastgelegd is (`repeaters.host_admin`). Dat is de toegestane-lijst, en de
  databank is de enige plek waar ze kan staan — ze moet ook kloppen voor een rij
  die er al stond, en na een herstart;
- de weigering geldt óók voor een **publiek** adres. Het lek is niet dat de server
  een LAN aanraakt; het lek is het wachtwoord in de header. Naar de server van een
  aanvaller op een publiek adres is dat precies zo erg, en alleen private bereiken
  toetsen zou het gat openlaten aan de kant waar het makkelijkst te misbruiken is;
- er wordt geoordeeld over het **opgeloste** adres en niet over de tekst, want een
  naam die naar 127.0.0.1 wijst is geen publiek adres. Dat beslist niet meer over
  toelating — de toegestane-lijst doet dat — maar het bepaalt wat de melding zegt,
  en het weigert de bereiken die nooit een node kunnen zijn (0.0.0.0, multicast).

De toets staat op de plek waar de verbinding werkelijk gemaakt wordt en niet
alleen in het formulier, om dezelfde reden als bij `nodeconfig.NO_REMOTE`: een
controle die in het formulier zit, is met een aangepast verzoek te omzeilen. Ze
staat *naast* de eerste en niet in de plaats ervan — twee sloten voor één regel,
waarvan de tweede degene is die telt.

> **Geef `node.beheeradres` aan niemand behalve serverbeheerders.** De code dwingt
> de grens nu af, maar het recht gaat nog steeds over dat veld, en dat veld is
> waar de vlootinloggegevens op gericht staan.

### Waarom het `cmd`-topic heropend is

Het eerdere ontwerp zei dat schrijfacties nooit over MQTT gaan, en benoemde het
geval dat die beslissing zou heropenen: *een `full_managed` node zonder IP-pad
kan helemaal niet geschreven worden, ook al werkt zijn `cmd`-topic.* Het zei er
meteen bij dat het antwoord dan niet zou zijn om de toelatingslijst stilletjes te
verbreden, maar om de beslissing bewust opnieuw te openen.

Dat geval dook op in een iets andere vorm. Een node die op MQTT publiceert en
onze firmware draait — full managed volgens elke definitie — meldde "kan niet
gewijzigd worden" zodra `MM_FW_NODE_USER` leeg gelaten was, terwijl er een open,
werkende verbinding naartoe ongebruikt lag. Een full managed node heeft per
definitie een MQTT-verbinding, dus "kan niet" was daar feitelijk onjuist.

De beslissing is dus heropend, en dit is wat overeind bleef.

**Het is een grotere toelatingslijst, geen doorgeefluik naar de CLI.** Het topic
vergelijkt nog steeds exacte woorden; er is er nu een vierde. De node valideert
zelf:

- de parameter moet een van de achtentwintig namen zijn die in `CFG_PARAMS`
  ingecompileerd staan, en het commando wordt opgebouwd met de sleutel *uit die
  tabel* — er wordt nooit tekst uit het bericht een commando. Alleen de waarde
  reist mee, en die is altijd het laatste woord, dus er is geen scheider waar een
  tweede commando na kan beginnen;
- de waarde moet door `cfgCheckValue()`, dezelfde zeef die beide
  HTTP-schrijfwegen gebruiken. Eén zeef, zodat de drie niet uit elkaar kunnen
  lopen;
- de risicoklasse mag `CFG_MQTT_MAX_RISK` niet overschrijden.

Een node die een onbekende parameter of een waarde buiten de grenzen krijgt,
weigert hem, telt hem, en meldt hem — de weigering komt terug in het
statistiekbericht dat hij er meteen na publiceert. Stilte zou niet te
onderscheiden zijn van een node die slaapt op zijn zonnebudget, en dat is het
enige wat dit antwoord niet mag zijn.

### Het plafond van dat vervoermiddel, en waarom het daar ligt

**Klasse 1 en 2 gaan over het `cmd`-topic. Klasse 3 niet.**

Het verschil tussen de vervoermiddelen is niet snelheid maar wie er aan de
overkant staat. Regel 1 en 3 hebben een geauthenticeerde tegenpartij: een
wachtwoord op een verbinding die jij beheert, of een monitor die over LoRa inlogt
met rechten die de eigenaar aan de overkant uitgaf en kan intrekken. Regel 2
heeft wie er brokergegevens houdt — en `mosquitto/acl.example` ondersteunt een
account per node, terwijl een standaardopstelling op één gedeeld account draait.
Met een gedeeld account is dat elke node die met de broker praat.

Daar komt bij dat er geen teruglezing in hetzelfde verzoek zit. De node meldt de
uitslag in zijn eerstvolgende statistiekbericht, dus een vergissing is niet
zichtbaar op het moment dat ze gemaakt wordt — en dat is precies wat je wél wilt
als die vergissing een node van de lucht kan halen.

"Overal alles" en "nergens iets" zouden allebei verkeerd zijn. De instellingen
die je op een gewone dag bijstelt — naam, positie, advertentie-interval,
floodgrenzen, zendtijdbudget — zijn klasse 1 en 2, en die gaan erlangs. De
handvol die een node kan afsnijden houdt zijn twee geauthenticeerde wegen. Wil je
die van hieraf kunnen zetten, vul dan `MM_FW_NODE_USER` en `MM_FW_NODE_PASS` in;
de pagina zegt dat waar hij weigert.

Het plafond wordt twee keer gehandhaafd: `nodeconfig.MQTT_MAX_RISK` in de server,
zodat er niets vertrekt dat toch geweigerd zou worden, en `CFG_MQTT_MAX_RISK` in
de firmware, omdat de node degene is die het gevolg draagt.

### Radio-instellingen worden op alle drie geweigerd

`radio` — frequentie, bandbreedte, spreidingsfactor en coderingssnelheid, die
MeshCore als één parameter zet — wordt **van afstand helemaal niet geschreven**.
Niet over IP, niet over het `cmd`-topic, niet over de mesh-CLI van een monitor.
`tx` (zendvermogen) mag wel en houdt zijn eigen risicoklasse.

De asymmetrie is het argument. Een verkeerde `tx` maakt een node zwakker en laat
hem bereikbaar: je hoort hem nog, hij hoort jou nog, en je zet het terug. Een
verkeerde frequentie, spreidingsfactor, coderingssnelheid of bandbreedte haalt
hem van de lucht — hij hoort niemand meer en niemand hoort hem — en er is geen
weg terug die niet fysiek is. Op een dak is dat het einde van die node. Geen
bevestiging repareert dat: een drempel beschermt tegen twijfel en tegen de klik
op de verkeerde regel, niet tegen een getal dat een zender op een band zet waar
de antenne niet voor gemaakt is. `radio` wordt bovendien pas na een herstart van
kracht, dus de fout wordt pas zichtbaar op het moment dat hij al onomkeerbaar is.

Het is een weigering aan de bron en geen ontbrekend invoerveld. Een parameter die
alleen uit een *formulier* weggelaten is, valt met een met de hand gemaakt
verzoek alsnog te schrijven; de drempel hoort te staan waar het verzoek werkelijk
uitgevoerd wordt. Hij staat dus op twee plaatsen:

- **in de firmware**, waar `radio` sinds nodefirmware 2.6.0 uit `CFG_PARAMS`
  verdwenen is. Die ene tabel is wat `GET /api/cfg` publiceert, wat
  `POST /api/moncfg` aanvaardt en wat het `cmd`-topic doorlaat, dus één regel
  weghalen sluit alle drie de ingangen tegelijk. In het commentaar op de plek
  waar hij stond staat de regel nog uitgeschreven, zodat terugzetten één regel is;
- **op de server**, als `nodeconfig.NO_REMOTE`, getoetst in `write()` nog vóór er
  een vervoermiddel gekozen wordt. Niet overbodig: het weigert voordat er iets
  vertrekt, met een zin die zegt waarom, en een node die nog firmware van vóór
  2.6.0 draait hééft `radio` in zijn tabel en zou hem aannemen.

### Wat dit van je broker vraagt

Met vervoermiddel 2 in gebruik houdt "wie mag er publiceren op
`<voorvoegsel>/<node>/cmd`" op een kwestie van netheid te zijn.
`mosquitto/acl.example` ondersteunt een account per node — elke node schrijft
alleen onder zijn eigen voorvoegsel en leest alleen zijn eigen `cmd`-topic, en de
site schrijft alleen op `<voorvoegsel>/+/cmd`. Dat is de aanbevolen inrichting.

**Deze installatie draait op dit moment op één gedeeld account.** Dan houdt elke
node inloggegevens waarmee op het `cmd`-topic van elke andere node gepubliceerd
kan worden, en sinds 2.8.0 betekent dat klasse 1 en 2 op elke node die het
draait. Lees [`security.md`](security.md#de-broker-is-nu-de-bepalende-vraag)
voordat je op deze weg gaat leunen — die sectie zet het plafond onomwonden neer
en somt op wat het begrenst.

### De parameterlijst, voor een node die alleen over MQTT bereikbaar is

De server bouwt zijn formulier uit de lijst van de node zelf en houdt met opzet
geen eigen tabel. Die lijst kwam tot nu toe alleen van `GET /api/cfg` — precies
wat een node waarvoor de server geen weblogin heeft niet kan beantwoorden.

Sinds 2.8.0 publiceert de node hem ook (`cfgspec`), samen met zijn
instellingenronde. De handeling die deze weg opent is dus de handeling die er al
was: druk één keer op **instellingen opvragen**, en de site kent zowel de waarden
als de grenzen. Tot dat gebeurd is zegt de pagina dat en biedt ze geen formulier,
want een formulier gebouwd uit een tabel die op de server verzonnen is, is
precies wat dit ontwerp weigert.

---

## Welke instellingen geschreven mogen worden

**Allemaal, op vier na.** De lijst is het volledige `handleSetCmd()`-oppervlak
van `src/helpers/CommonCLI.cpp` — zevenentwintig parameters — en niet een
zorgvuldig uitgekozen veilig hoekje. Drie van de vier stonden er nooit op
(hieronder); de vierde is `radio`, die in nodefirmware 2.6.0 verwijderd is — zie
[radio-instellingen](#radio-instellingen-worden-op-alle-drie-geweigerd)
hierboven.

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
| `radio` | **Wordt niet aangeboden.** Firmware 2.6.0 en hoger noemt hem niet, dus de regel verschijnt niet; een node op oudere firmware noemt hem wél, en daar toont de pagina de reden in plaats van vier velden. Het waren vier getalvelden met elk een eigen bereik, op het argument dat één tekstvak voor `869.525 250 11 5` precies het vak is waar een typfout een verloren node van maakt. Dat klopte en ging niet ver genoeg: ook een correct ingevuld formulier zet een zender net zo definitief op een band waar de antenne niet voor gemaakt is |
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

Bij een `semi_managed` doel is de uitzendende node de monitor, met onze firmware,
zodat de tabel en de validatie ervan gelden voordat er iets de lucht in gaat. De
volgende sectie gaat over die weg.

---

## Schrijven over LoRa, via de monitor

Dit is de weg waarvoor het hele project bestaat, en tot firmware 2.4.0 was het
het enige in dit document dat ontworpen was en niet gebouwd. De node waar het om
gaat is JessaZH: stock MeshCore op een dak, geen IP-pad, en dat komt er ook niet.

**Eén schrijfweg, drie vervoermiddelen.** Alles hierboven blijft zonder
uitzondering gelden — dezelfde parameterlijst, dezelfde grenzen, dezelfde drie
risicoklassen, dezelfde bevestigingen, dezelfde rechten, hetzelfde teruglezen.
Alleen de laatste stap verschilt. `nodeconfig.write()` doorloopt eerst elke
controle en kiest daarna pas een vervoermiddel; een tweede functie voor de
radioweg zou een tweede plek zijn waar een drempel kan ontbreken, en dat is het
soort fout dat je pas ontdekt als er een node stil is.

| Doel | Waar de server aanklopt | Wat daar gebeurt |
|---|---|---|
| eigen IP-pad | de node, `POST /api/cfg` | één `handleCommand()`-aanroep, tienden van seconden |
| alleen over LoRa | zijn **monitor**, `POST /api/moncfg` | twee pakketten over een gedeelde band, tientallen seconden |

**De monitor heeft de nieuwe firmware nodig. Het doel niets.** Dat is het punt
van dit ontwerp en geen bijkomstigheid. JessaZH leert niets, krijgt niets en
merkt niets: er komen twee doodgewone CLI-commando's binnen, precies zoals sinds
de instellingenronde gebouwd werd. Een node die maandenlang geen nieuwe firmware
krijgt, hoeft er ook geen.

### Wat er werkelijk de lucht in gaat

Twee commando's, en het tweede is niet optioneel:

```
set <parameter> <waarde>
get <parameter>
```

De gemelde uitslag is wat het **tweede** teruggeeft, met de vraag ernaast. Nooit
wat de node op het eerste antwoordde.

Dat is dezelfde discipline die de weg over IP volgt, en hier weegt ze zwaarder om
twee redenen die elkaar versterken. Het doel heeft geen tweede weg naar binnen,
dus een verkeerde waarde is niet vanuit een stoel te herstellen. En een ronde
duurt lang genoeg dat niemand het achteraf met de hand natrekt — over IP zie je
binnen seconden dat `advert.interval 61` een 60 werd, over LoRa zou je het een
maand niet merken.

De vergelijking — is wat er terugkwam dezelfde *waarde* als wat er gevraagd is,
met `869.525 250 11 5` tegenover `869.525,250,11,5` en `50.0%` tegenover `50` —
gebeurt op één plek in de firmware, gedeeld met `POST /api/cfg`. Twee kopieën
zouden vroeg of laat van elkaar gaan verschillen, en dan staat er een
waarschuwing bij een radio die volkomen in orde is. Een melding die te vaak
afgaat is net zo onbruikbaar als een die nooit afgaat.

### Beproef hem eerst zonder iets te veranderen

Er is één toets van deze weg die het hele pad aflegt en niets verandert:
**schrijf een parameter naar de waarde die hij al heeft.**

`set tx 17` op een node die al op 17 staat oefent verzenden, ontvangen, het
antwoord verwerken en het teruglezen. Mislukt het, dan is er niets stuk. De
nodepagina vult elk invoerveld voor met wat de laatste uitleesronde vond, dus dit
is één klik.

Doe dat als eerste op een node die je niet kunt aanraken, en opnieuw telkens als
een monitorverbinding nieuw is of opnieuw is opgezet. Het is het verschil tussen
toetsen en hopen.

Het is ook het enige wat dit project met JessaZH doet. Tot iemand daar een
lege schrijfactie heeft zien slagen, gaat er geen echte waarde naar die repeater.

### Een derde uitkomst die alleen over de radio bestaat

Over IP is een schrijfactie geslaagd of niet. Over LoRa is er een derde toestand,
en die tot "mislukt" platslaan zou een leugen zijn in de gevaarlijkste richting.

| `step` | Wat vaststaat | Wat te doen |
|---|---|---|
| *(leeg)*, `ok` | de parameter is teruggelezen; `applied` is wat er in de node staat | niets, tenzij `exact` onwaar is |
| `niet_verstuurd` | **er is niets de lucht in gegaan** — de login bleef onbeantwoord, of de pakketpool van de monitor zat vol. Er is met zekerheid niets veranderd | opnieuw proberen |
| `geen_antwoord` | de `set` **is vertrokken** en er kwam geen antwoord. Of de node hem uitgevoerd heeft, is van hieraf niet te zien | een nieuwe uitleesronde is de enige manier om erachter te komen. Herhaal de schrijfactie niet zomaar |
| `geen_teruglezing` | de `set` werd beantwoord, de `get` niet. Er is misschien iets opgeslagen en het is niet vastgesteld wat | lees terug met een uitleesronde |
| `node` | de node weigerde het commando en zei waarom | verbeter de waarde |
| `bezig` | loopt nog op de monitor | herlaad de pagina; de uitslag wordt daar bewaard |
| `monitor` | de monitor wilde niet beginnen — niet in zijn lijst, er loopt een ronde, te kort na de vorige | de melding zegt welke |

`geen_antwoord` verdient een eigen woord juist omdat "mislukt" iemand zou laten
aannemen dat er niets gebeurd is, en op een node die je niet kunt nakijken is dat
de aanname die je niet mag maken. De server legt zo'n schrijfactie ook niet vast
in zijn eigen instellingentabel: een gok in de kolom is erger dan een leeg vakje.

De faalwijze die je van tevoren wilt kennen is de alleen-lezende monitor —
keurig ingelogd, en op elk commando stilte. Dat levert bij schrijven
`geen_antwoord` op, en de oplossing is `setperm <pubkey van de monitor> 3` aan de
overkant. De nodepagina toont die diagnose boven het formulier, uit de tellers
van de monitor zelf, zodat het te beantwoorden is vóór de knop ingedrukt wordt in
plaats van erna. Zie
[De drie stiltes uit elkaar houden](#de-drie-stiltes-uit-elkaar-houden).

### De endpoints op de monitor

Allebei achter de eigen HTTP-login van de monitor — `MM_FW_NODE_USER` /
`MM_FW_NODE_PASS`, dezelfde gegevens die `/api/fw` en `/api/cfg` gebruiken. Die
login hoort bij een node van onszelf. **De server heeft nooit een geheim van het
doel nodig**; wat de monitor binnenlaat staat op de monitor, of bestaat niet
omdat de overkant onze publieke sleutel in zijn toegangslijst zette.

**`POST /api/moncfg`** met `key` (de publieke sleutel van het doel), `param` en
`value`. Antwoordt **202** en geen 200, want er is nog niets gebeurd:

```json
{"ok":1,"step":"","busy":1,"msg":"gevraagd; twee commando's over LoRa, …"}
```

**`GET /api/moncfg`** — de lopende of laatst afgeronde schrijfactie. De velden
lopen met opzet gelijk met die van `POST /api/cfg`, zodat de server er één soort
antwoord van maakt en de pagina niet hoeft te weten langs welke weg het ging:

```json
{"seq":3,"busy":0,"ok":1,"step":"","key":"e3d3f4d7edd0","param":"tx",
 "asked":"17","applied":"17","exact":1,"reboot":0,"reply":"OK",
 "end":"klaar","age":31}
```

**De uitslag staat op de monitor en niet op de server.** Daarom hoeft de browser
niet te blijven wachten: de server geeft het na 40 seconden op en meldt dat de
schrijfactie nog loopt, en een herlading vindt de uitslag alsnog. Het scheelt ook
een opdrachtenlijst en een achtergronddraad in de server voor een handeling van
een halve minuut — en het is de eerlijkere plaats, want de node die het werk deed
is de enige die weet hoe het afliep.

Hetzelfde vanaf een seriële kabel, de telnetconsole of de mesh-CLI:
`wifi mon set <hex> <param> <waarde>`, en `wifi mon set` zonder meer voor hoe de
laatste afliep. Niet alleen om te diagnosticeren: de mesh-CLI is de weg die als
laatste wegvalt, dus een instelling op de dakrepeater is nog te corrigeren vanaf
een telefoon terwijl de WiFi, de site en de broker alle drie weg zijn.

### Wat het kost, en waarom de pauze hier korter is

Twee commando's en twee antwoorden — ruwweg een tiende van een uitleesronde. De
wachttijden zijn die van die ronde, want ze zijn gemeten op dezelfde band over
dezelfde hops en er is geen reden waarom een `set` sneller terug zou komen dan
een `get`: 20 s voor het eerste commando na een login, 12 s voor elk volgend, 2 s
ertussen, en een hard plafond van 90 s op het geheel.

Eén tegelijk, alleen op verzoek, en **nergens herhalingen**. Een `set` die stil
bleef nog eens versturen, zou hem een tweede keer uitvoeren op een node die hem
misschien al aangenomen heeft.

Tussen twee schrijfacties zit **één minuut**, waar de uitleesronde er tien heeft.
Die grens is met opzet de kleinste van de twee, en het is het meest doordachte
getal van deze sectie: *de handeling die je na een misser meteen wilt doen, is de
omgekeerde.* Wie `tx 5` zette waar `tx 20` hoorde, moet dat binnen een minuut
kunnen rechtzetten en niet binnen tien. Herstel mag nooit zwaarder afgeremd
worden dan de fout die het terugdraait — dezelfde regel die `filter off`
goedkoper maakt dan `filter on`. Wat de grens wél tegenhoudt is een script dat de
band vol zet, en daar is een minuut ruim genoeg voor: hij ligt boven de duur van
een hele ronde.

Anders dan de uitleesronde heeft deze weg **geen werkende broker nodig**. Die
ronde publiceert haar uitslag over MQTT en heeft zonder broker niets te doen;
deze antwoordt over HTTP aan wie het vroeg. Een installatie zonder internet, of
met een broker die er even niet is, hoort een radio-instelling nog te kunnen
rechtzetten.

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

## De instellingensweep inplannen

Een node die naar MQTT publiceert leest zijn eigen CLI-instellingen één keer per
dag uit. Dat kost niets — het is een functieaanroep binnen zijn eigen firmware.
Een node die alleen over LoRa bereikbaar is wordt door zijn monitor uitgelezen,
en dan is één ronde twintig vragen en twintig antwoorden over een gedeelde band,
betaald door een repeater op een dak.

Het oorspronkelijke ontwerp gaf gemonitorde nodes daarom **helemaal geen
schema**: alleen op verzoek. Dat was te verdedigen op grond van zendtijd, en in
de praktijk verkeerd. Wat het opleverde is gemeten: instellingen van twaalf uur
oud, en een regioboom van zeven dagen oud, op een node die perfect antwoordde
zodra iemand het vroeg. Een pagina die stille veroudering als feit presenteert is
precies de halve waarheid die de rest van dit project probeert te vermijden.

Er is nu een schema, en zendtijd blijft de beperking in plaats van te verdwijnen.

### Drie grenzen, en ze stapelen

**1. Een interval per node, standaard uit.** Niet één globaal getal, want de
kosten verschillen per node: een repeater aan de rand van je bereik betaalt elke
ronde met stroom uit een zonnepaneel en met pakketten die misschien niet
aankomen, terwijl een node twee straten verderop bijna gratis is. Eén interval
voor allemaal betekent ofwel instellen op de duurste node, ofwel de goedkoopste
verwaarlozen. Een node toevoegen levert geen terugkerende kosten op.

**2. Eén ronde tegelijk, met een minimale tussenruimte.** Niet tien timers die
toevallig samenvallen: één wachtrij, en de node die het langst over tijd is wint.
Tien rondes in hetzelfde uur zouden de band een uur lang bezetten. De
tussenruimte geldt globaal — over alle nodes heen, niet per node — want de band
is gedeeld, of twee monitors dat nu zijn of niet.

**3. Een plafond per dag over alle nodes heen.** Dit vangt wat geen van de andere
twee vangt: iemand die twintig nodes op dagelijks zet zonder het rekenwerk te
doen. Als het knelt, schuiven schema's op, en de pagina zegt dat. **Het interval
per node is een wens; dit is wat er werkelijk gebeurt.**

| Instelling | Standaard | Betekenis |
|---|---|---|
| per node, op de pagina van de node | uit | uren tussen rondes voor deze node |
| `MM_SWEEP_ENABLED` | `1` | de planner als geheel |
| `MM_SWEEP_MIN_GAP_MIN` | `15` | minuten tussen twee rondes; nooit onder 10 |
| `MM_SWEEP_MAX_PER_DAY` | `48` | rondes per 24 uur over alles heen |

De minimale tussenruimte kan niet onder 10 minuten, en die ondergrens is niet
willekeurig: het is `MON_SET_MIN_GAP_MS` in de firmware. Vaker vragen heeft geen
zin omdat de monitor het toch weigert, en het zou de site iets laten beloven wat
niet gebeurt.

### Het is geen achterdeur om de grenzen van de firmware heen

De sweep zelf draait hier niet. Hij draait in de firmware van de monitor, met een
eigen budget: een minimale tussenruimte tussen rondes, een maximum per ronde, en
stoppen na drie stiltes op rij. Deze planner bepaalt alleen *wanneer er gevraagd
wordt*, en hij gaat naar buiten via dezelfde `publish_command` als de handmatige
knop — geen apart pad naar de broker.

Dat is dezelfde regel die de handmatige knop voor het zetten van de klok volgt,
en om dezelfde reden: een tweede route naar de broker zou een achterdeur zijn om
de controles op de eerste heen, en het enige zichtbare symptoom zou te veel
verkeer op de band van iemand anders zijn.

### Een node zonder route blokkeert de wachtrij niet

Kiest de planner een node die hij op dat moment niet kan bereiken, dan **schrijft
hij dat op** in plaats van het elke minuut opnieuw te proberen. Zonder dat blijft
de onbereikbare node voor altijd degene die het langst over tijd is en komt
niemand anders aan de beurt — en zou de pagina een ronde blijven beloven die er
nooit komt.

### Wat de pagina toont

Per node: het interval, wanneer de volgende ronde valt, en wat de vorige
opleverde. Zonder dat laatste is een schema een belofte die je niet kunt
nakijken.

Het maakt ook de drie celtoestanden in de vergelijkingstabel compleet. Tot nu toe
betekende een lege cel "nooit gevraagd" of "gevraagd, geen antwoord"; **"het
schema staat uit"** is een derde ding, en het is degene die verklaart waarom een
waarde daar al een week staat. De vergelijkingstabel draagt het schema daarom als
een eigen kolom — bij twintig nodes is "welke staan er op nooit" een vraag over
de verzameling, en een node die als enige geen schema heeft is precies de node
waarvan de waarden stilletjes verouderen.

### Rechten

Een schema wijzigen is `node.schema`, één klasse zwaarder dan de knop die één
losse ronde start (`node.uitvragen`). Dat is geen strengheid om de strengheid:
de knop kost eenmalig zendtijd, dit kost het elke dag, op een band die van
iedereen is. Wie hem aanzet legt een terugkerende belasting op het mesh van
iemand anders.

### Topics zijn hoofdlettergevoelig, en dat kostte twaalf uur

De planner liep, schreef `gevraagd` in zijn aantekeningen, en leverde niets af.
De oorzaak was niet de planner.

MeshCore bouwt zijn MQTT-topics met `Utils::toHex()`, en de tabel van die functie
is `"0123456789ABCDEF"` (`src/Utils.cpp:186`). Een node **publiceert dus op hex in
hoofdletters en abonneert zich daar ook op**: `meshmanager/55D9A320A4E3/cmd`. De
site normaliseerde de nodesleutel bij binnenkomst naar kleine letters — terecht,
het is een databasesleutel — en bouwde het opdrachttopic daarna uit diezelfde
genormaliseerde waarde. Elke opdracht ging naar
`meshmanager/55d9a320a4e3/cmd`, waar niemand op geabonneerd was.

`publish()` slaagde, de broker nam de bytes aan, en er was geen luisteraar. Dat
is dezelfde soort fout als de verkeerdetopicbug in firmware 1.3.0, en om dezelfde
reden onzichtbaar: **succes op de transportlaag zegt niets over aankomst.**

Het raakte elke opdracht die de site ooit op dit topic verstuurd heeft — de knop
voor instellingen, het zetten van de klok, statusverzoeken, het schrijven van de
MQTT-instellingen — en niet alleen de planner. De planner maakte het enkel
zichtbaar, doordat hij het eerste was dat opdrachten verstuurde waar niemand op
dat moment naar keek.

**De oplossing is onthouden in plaats van uitrekenen.** Het nodedeel wordt nu
precies zo bewaard als het aankwam (`repeaters.topic_node`), naast
`topic_prefix`, dat één veld verderop om exact dezelfde reden bestaat. Een
opdracht gaat naar het topic dat de node werkelijk gebruikt. Voor een node die we
nog nooit hoorden worden beide schrijfwijzen geprobeerd — hoofdletters eerst,
want dat is wat MeshCore doet — volgens dezelfde redenering die al voor een
onbekend voorvoegsel geldt: één extra berichtje van acht bytes is goedkoper dan
een knop die stilzwijgend niets doet.

Twee tegengestelde regels reizen in één bericht mee, dus ze worden beide door
tests vastgelegd:

| Deel | Schrijfwijze | Waarom |
|---|---|---|
| de node in het **topic** | zoals de node hem schrijft, normaal in **HOOFDLETTERS** | MQTT-topics zijn hoofdlettergevoelig en MeshCore gebruikt hoofdletters |
| het onderwerp in de **payload** | in **kleine letters** | `monKeyArg()` zet elke sleutel om naar kleine letters voordat die in de monitorlijst komt *én* voordat een binnenkomend onderwerp ermee vergeleken wordt, en `sameNode()` vergelijkt met `memcmp` — een onderwerp in hoofdletters zou dus *niet* overeenkomen |

Uitrekenen wat het topic zou moeten zijn is precies hoe dit misging; de tweede
rij is waarom "zet alles maar in hoofdletters" de andere helft gebroken zou
hebben.

### "Gevraagd" is geen uitkomst

De aantekening zei `gevraagd` zodra het publiceren gelukt was, en dat is een
uitspraak over de broker en niet over de node. Een geslaagde ronde en een
verdwenen ronde zagen er identiek uit, en daarom viel twaalf uur stilte niet op.

Er zijn nu drie uitkomsten. Wanneer een ronde aangevraagd wordt, wordt de
nieuwste tijdstempel onder de bewaarde waarden van die node als vertrekpunt
opgeschreven. Tien minuten later — langer dan de nominale 286 s van een sweep,
korter dan de minimale tussenruimte, zodat het oordeel altijd valt voordat de
volgende ronde vertrekt — kijkt de planner opnieuw:

| Aantekening | Betekenis |
|---|---|
| `versturen mislukt` | er is niets van de server vertrokken |
| `gevraagd` | verstuurd, het oordeel moet nog vallen |
| `gevraagd, antwoord binnen` | de waarden zijn nieuwer dan daarvoor |
| `gevraagd, geen antwoord` | verstuurd, en er kwam niets terug |

De laatste wordt als waarschuwing gelogd en op de pagina van de node getoond. De
verificatie loopt *voordat* er een nieuwe ronde verstuurd wordt, want in de
andere volgorde zou een node die nooit antwoordt zijn eigen aantekening elke
cyclus overschrijven en zou de stilte nooit bovenkomen.

### Een ontbrekend beheeradres is nu vooraf zichtbaar

Een leeg `ota_host` sluit het schrijfpad over HTTP, het pad voor
firmware-upgrades, en de rechtencontrole die bij de monitor van een node
aanklopt — en niets daarvan is op de node zelf te zien. Tot nu toe werd het pas
gemeld wanneer iemand op een knop drukte die dan niets deed.

De nodelijst noemt full managed nodes zonder adres daarom bovenaan de pagina, op
dezelfde plek en om dezelfde reden als het blok over de gezondheid van de broker.

Doorgestuurde nodes vallen erbuiten: voor een node die alleen over LoRa
bereikbaar is, is een leeg adres de normale toestand, en ze meenemen zou de
waarschuwing waardeloos maken.

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

Over LoRa komen er nog twee bij, want daar kan het teruglezen op zichzelf
misgaan — zie
[Een derde uitkomst die alleen over de radio bestaat](#een-derde-uitkomst-die-alleen-over-de-radio-bestaat).

`get <key>` is dezelfde uitlezing die de dagelijkse instellingenronde gebruikt,
zodat er geen tweede codepad is dat het met het eerste oneens zou kunnen zijn.

Omdat die uitlezing is wat de pagina meldt, schrijft de server hem ook in zijn
eigen instellingentabel. Zonder dat blijft de kolom 'nu' de oude waarde tonen
naast een melding dat het gelukt is, tot de volgende uitleesronde — en die kost
over LoRa zendtijd op andermans band en gebeurt hooguit dagelijks. Wat er
vastgelegd wordt is wat er *terugkwam*, nooit wat er gevraagd is, en er wordt
niets vastgelegd als het teruglezen niet gebeurd is.

---

## De klok zetten

De klok heeft een eigen sectie op de nodepagina en een eigen bevestiging die de
node bij naam noemt, want hij schrijft één getal dat van hier niet meer te
corrigeren is.

![De sectie 'Klok' van Voorbeeld-Dakrepeater. Een tabel toont 'Laatst tijd gestuurd: nog nooit door deze site' en 'Automatisch: ja, de site doet dit uit zichzelf'. Een amberkleurig omrand blok 'Klok nu synchroniseren', met het label 'schrijft op het apparaat', legt uit dat deze repeater geen eigen weg naar de site heeft, dat zijn klok van node bb11bb11bb11 komt die hem monitort, en vetgedrukt dat de knop zich daarom niet op deze repeater alleen richt — de site stuurt de tijd naar die node, en die kijkt de klokken na van alle repeaters die hij monitort. Onder de werkende knop staat een alinea over wat de pagina wel en niet weet over of de klok daarna echt goed stond.](../images/beheer-node-klok.png)

Vier dingen om te weten voor je erop drukt.

**De knop is breder dan de node waaronder hij staat.** Bij een doorgestuurde
repeater gaat de tijd naar zijn monitor, en die kijkt daarna de klokken na van
*elke* repeater die hij monitort. De firmware kent geen manier om dat toe te
spitsen, en dat is geen omissie — een klokronde kost per gemonitorde repeater één
vraag en één antwoord, ongeveer een vijfde van een gewone pollronde. De pagina
zegt dat in plaats van te doen alsof de knop op één apparaat wijst.

**`time` vereist nodefirmware 1.10.0**, langs beide wegen — anders dan bij
instellingen, waar de grens van de weg afhangt (1.8.0 rechtstreeks, 1.9.0 via een
monitor). Het is dezelfde ontvanger die hetzelfde woord moet kennen.

**Een klok kan alleen vooruit.** Een advert draagt de klok van zijn afzender mee,
en elke node die die afzender al kent gooit een advert weg waarvan de tijdstempel
niet gestegen is. Een klok een uur terugzetten maakt die repeater een uur
onzichtbaar, dus corrigeert de firmware nooit achteruit — wat betekent dat een
tijd die te ver vooruit gezet is een fout is die je ter plaatse herstelt.

**De site weigert als hij zijn eigen klok niet vertrouwt.** Drie controles —
`adjtimex(2)`, een controle op wandklok tegenover monotone klok, en een bewaarde
hoogwatermarkering — en als één daarvan ontevreden is vertrekt er niets. De
pagina zegt welke.

De site doet dit ook uit zichzelf, één keer per dag; de knop bestaat om niet te
hoeven wachten. De volledige redenering staat in [`clocksync.md`](clocksync.md).

---

## Firmware upgraden, en terugrollen

Firmware staat op een eigen pagina, want "welke release draait waar" is een vraag
die je over alle nodes tegelijk stelt.
[`firmware-upgrade.md`](firmware-upgrade.md) bevat het volledige mechanisme — de
checksum die twee keer gecontroleerd wordt, waarom alleen succes herstart, wat
een checksum *niet* bewijst. Wat hier hoort is welke nodes überhaupt een image
kunnen ontvangen, en wat de pagina doet als er één niet terugkomt.

![Het deel 'Nodes' van de firmwarepagina met drie verzonnen nodes. Voorbeeld-Thuisnode toont nodefirmware 1.10.0, MeshCore v1.16.0, bouwomgeving heltec_v3, beheeradres http://192.0.2.11, een pil 'kritiek', en een upgradeformulier met een versiekeuzelijst plus een veld om te bevestigen door de nodenaam over te typen. Voorbeeld-Zendmast toont vetgedrukt een melding — 'Node niet teruggekomen na upgrade naar 1.10.0', met de stap herstart ernaast, met de uitleg dat het image geschreven en gecontroleerd is, dat dit over de herstart gaat, en dat terugvallen kan met 'wifi fw rollback' over de mesh-CLI, met een knop 'Wegklikken'. Voorbeeld-Dakrepeater heeft een leeg adresveld, een uitgeschakelde knop 'Node uitvragen' en de melding 'Geen upgrade mogelijk' omdat hij over LoRa doorgestuurd wordt.](../images/beheer-firmware.png)

`firmware.ota_route()` beslist, en geeft een blokkade terug die de pagina in een
zin omzet. In de volgorde waarin ze getoetst worden:

| Blokkade | Betekent | Wat je eraan doet |
|---|---|---|
| `no_credentials` | De server heeft geen login voor de beheerpagina's van de nodes | Zet `MM_FW_NODE_USER` en `MM_FW_NODE_PASS`. Tot dan blijft elke upgradeknop uit |
| `relayed_only` | Geen beheeradres, en de cijfers van deze node komen via een andere node binnen | Niets. Dit is een **blijvende toestand**, geen vergeten instelling: 1,3 MB over LoRa tegen de duty-cycle-limiet is dágen. Flash hem over USB |
| `no_host` | Geen beheeradres, en de node wordt niet doorgestuurd | Vul er een in, als die er is |
| `no_fw` | De node meldt geen MeshManager-versie | Hij draait waarschijnlijk onze firmware niet, en een image van dit project hoort daar niet heen |

Twee verdere weigeringen volgen nadat `can` waar is. Een node die geen
**bouwomgeving** meldt krijgt geen upgradeknop maar een knop "Node uitvragen" —
die omgeving komt van de node zelf en nergens anders vandaan, want een verkeerd
image op een node die je niet kunt aanraken is niet meer recht te zetten. En een
node die als **kritiek** gemarkeerd staat vraagt zijn naam voluit overgetypt,
hetzelfde middel als de zwaarste risicoklasse hierboven, en om dezelfde reden:
het vangt een klik op de verkeerde regel, en daar helpt een ja/nee-vraag niet
tegen.

**Teruggaan is één schrijfactie en een herstart**, want de partitietabel heeft
twee applicatiesleuven en een OTA wist nooit die waar hij niet in schrijft. Het
is met opzet *niet* automatisch: een zonnerepeater valt uit om redenen die niets
met firmware te maken hebben, en "drie mislukte starts, dan terugrollen" zou
goede upgrades eeuwig stilletjes ongedaan maken. Drie herstarts brengen een node
al in veilige modus, waardoor hij bereikbaar blijft; terugrollen is dan een
beslissing die iemand neemt.

---

## Als een node niet terugkomt

Dit is een eigen toestand, en dat is de hele kern. `niet_teruggekomen` is geen
mislukking en geen succes: het image is geschreven, de checksum klopte, de node
herstartte, en daarna antwoordde hij niet meer. De opdracht blijft op de pagina
staan tot iemand hem wegklikt — met opzet de enige manier waarop hij verdwijnt,
want een node die na een upgrade stilletjes uit beeld verdwijnt is precies de
gebeurtenis waar dit hele ontwerp voor bestaat.

De site wacht **150 seconden**, om de vijf pollend, voor hij dat zegt.

| Wat de site weet | Wat hij niet weet |
|---|---|
| Het image bereikte de node en zijn SHA-256 klopte, twee keer | Of de node opgestart is |
| `otadata` is geschreven, dus de node wílde het nieuwe image starten | Of hij op het netwerk gekomen is |
| Hij antwoordt sindsdien niet op zijn beheeradres | Of hij draait, in veilige modus staat, of dood is |

Wat je probeert, van goedkoop naar duur:

1. **Nog even wachten.** 150 seconden is een tijdslimiet, geen oordeel. Een node
   die traag opnieuw associeert kan alsnog opduiken.
2. **`wifi fw rollback` over de mesh-CLI.** Dit is de belangrijke: het werkt ook
   als de node op het netwerk onzichtbaar is, want het meshpad en het IP-pad zijn
   onafhankelijk. Is de node überhaupt in de lucht, dan bereikt dit hem.
3. **Veilige modus.** Drie mislukte herstarts en de node zet zijn eigen
   accesspoint met zijn beheerpagina op. Bereikbaar zonder het netwerk dat hij
   net kwijtraakte.
4. **`start ota` over de mesh-CLI**, en dan de soft-AP, als de module zichzelf na
   zes herstarts uitgeschakeld heeft.
5. **USB.** Ter plaatse, en de reden dat een kritieke node er een is die je
   fysiek kunt bereiken.

Komt de node terug op de **oude** versie, dan is dat `mislukt` met stap
`terug_op_oud` en niet deze toestand — de terugval is vanzelf gebeurd en de node
is in orde.

Dezelfde soort probleem bestaat een maat kleiner, na een wijziging van `radio`:
die instelling antwoordt `OK - reboot to apply`, dus een verkeerde waarde komt
pas bij de herstart aan het licht. Stap 2 tot 5 zijn ook daar de weg terug.

---

### Het vanaf de site toewijzen

`/admin/monitors`. Één pagina in plaats van een blok per node, om dezelfde reden
als bij de vergelijkingstabel: *"welke node heeft nog geen monitor"* en *"welke
lijst komt nooit voorbij zijn eerste kandidaat"* zijn vragen over de verzameling.

Per node een geordende lijst van ten hoogste `MAX_CANDIDATES` (drie). Per
nodegroep hetzelfde, zodat twintig nodes geen twintig bezoeken kosten. **Een
lijst op een node gaat voor die van zijn groep** — ze worden niet samengevoegd,
want een lijst die half uit een groep en half uit een node komt is een lijst
waarvan de volgorde niet meer na te tellen is, en juist die volgorde is het hele
punt.

### De controle hoort in de keuzelijst, niet in de eerste poging

Een kandidaat die de node nooit kan bereiken staat er **uitgeschakeld bij, met de
reden in zijn tooltip**. Dat was vanaf het begin de eis: een lijst met een
monitor erin die het nooit kan, is een lijst die liegt, en die leugen kost een
sweep die in een time-out afloopt op een gedeelde band, elke ronde.

Bij het opslaan wordt hij **geweigerd en niet stilletjes weggelaten**. Een lijst
die korter blijkt dan wat iemand koos, zonder dat er staat waarom, is dezelfde
leugen in een ander jasje.

`rights_note()` — die de monitor zelf vraagt of hij binnenkomt — zit in plaats
daarvan achter een knop. Die gaat het netwerk op, en op een pagina met twintig
nodes zou dat twintig HTTP-verzoeken per keer bekijken zijn.

### Waarneming naast configuratie

Allebei worden ze altijd getoond:

> **Waargenomen:** stuurt zijn cijfers via X
> **Ingesteld:** mag beheerd worden via X, daarna Y

Ze kunnen verschillen, en dat verschil is informatie en geen storing: X hoort hem
en Y kan hem niet bereiken, of iemand veranderde de instelling en de volgende
ronde moet het nog laten zien. De uitslag van de laatste ingeplande poging staat
eronder — met daarbij, als een kandidaat een inhoudelijke weigering gaf, de
melding dat de lijst **niet** doorgeschoven is en waarom niet.

### Deze lijst en de lijst op de node zelf

Er zijn twee lijsten en ze zeggen verschillende dingen. **Deze** zegt wie de site
*kiest*. **Die op de repeater** (`wifi mon`, of zijn eigen beheerpagina) zegt wie
hij werkelijk kan *uitvragen* — daar staan de sleutels, en het wachtwoord als er
een nodig is.

Ze moeten overeenkomen, en **deze pagina vult de lijst op de node niet
automatisch aan.** Dat is een beslissing en geen omissie: een node aan die lijst
toevoegen laat de monitor hem elke ronde uitvragen — terugkerende zendtijd — en
het dwingt een keuze over de inloggegevens (leeg, via hun toegangslijst, of een
wachtwoord). Zulke gevolgen horen niet als bijwerking van een keuzelijst te
ontstaan.

Twee wegen vullen die lijst wél, en deze pagina gaat met opzet langs geen van
beide heen: [ontdekken](#telemetrie-zonder-inloggegevens) voegt een node zonder
wachtwoord toe (de gastweg), en de eigen beheerpagina van de repeater blijft de
baas over zijn eigen lijst. Staat een node hier ingesteld maar niet in de lijst
van die monitor, dan zegt *Rechten nakijken* dat met zoveel woorden.

### Rechten

Toewijzen is `node.schema` — dezelfde klasse als het sweepschema, en om dezelfde
reden: wie dit zet, beslist wiens zendtijd er elke ronde opgaat. Een lijst per
groep is `server.instellingen`, want die reikt over nodes heen die degene die hem
zet niet allemaal zelf in beheer hoeft te hebben.

De keuzelijsten met kandidaten worden opgebouwd uit de **zichtbare** nodes. Een
monitor die je niet mag zien, hoort niet te staan in een lijst waar je een naam
uit kunt lezen — die keuzelijst was de enige plek op deze pagina waar een naam
van buiten je bereik voorbijkwam.

---

## Twee soorten inloggegevens, en ze staan op verschillende plaatsen

Dit is het deel dat het eerste ontwerp verkeerd had, en het verschil is genoeg
waard om uit te schrijven.

| Pad | Wie er inlogt | Waar het geheim staat |
|---|---|---|
| Server → node, over HTTP (`/api/fw`, `/api/cfg`, `/api/mon`) | de server toont de **weblogin** van díe node | op de server, in `MM_FW_NODE_USER` / `MM_FW_NODE_PASS` |
| Monitor → doel, over LoRa (de CLI-sweep, en het schrijven) | de **monitor** logt in bij het doel | op de monitor: ofwel helemaal niets, ofwel het beheerderswachtwoord van het doel in zijn eigen monitorlijst |

Het eerste ontwerp vroeg de server om inloggegevens in het *tweede* geval, en dat
is omgekeerd. **De server hoeft het wachtwoord van een doelnode nooit te kennen.**
Hij moet zijn eigen monitor bereiken; de monitor houdt bij — of heeft niet nodig —
wat het doel vraagt.

Die vergissing was zichtbaar in de interface: bij een doorgestuurde node stond
*"de server heeft geen inloggegevens voor de beheerpagina's van de nodes"*, wat
tegelijk waar en niet ter zake is. Geen enkel gegeven op de server had die node
ooit geholpen.

### De twee manieren waarop een monitor binnenkomt, en welke de voorkeur heeft

**Toegangslijst — aanbevolen.** De operator aan de overkant voert één keer
`setperm <monitor-pubkey> 3` uit. Er staat dan **nergens een wachtwoord**: de
monitor logt in met een lege tekenreeks en de overkant zoekt onze publieke
sleutel op in zijn eigen toegangslijst. Niemand geeft een geheim uit handen, en
de andere operator kan het in zijn eentje intrekken zonder ons iets te vragen.

Die `3` doet ertoe. `1` is alleen-lezen en is genoeg om de status te pollen, maar
**niet** voor de instellingensweep: een repeater voert een CLI-commando alleen
uit voor een client die hij als admin beschouwt. Een alleen-lezen monitor logt
perfect in en wordt daarna met stilte beantwoord, commando na commando.

**Wachtwoord — tweede keuze.** De monitor houdt het beheerderswachtwoord van het
doel in zijn eigen monitorlijst. De site kan dat wachtwoord *zetten* en **geeft
het door zonder het te bewaren**: het gaat naar de monitor en wordt niet naar de
database, niet naar een instelling en niet naar een logboek geschreven.

De prijs staat er eerlijk bij in plaats van verstopt: de site kan je niet laten
zien wát er ingesteld is — alleen *dát* er iets is — en kan het niet opnieuw
versturen zonder dat je het weer intikt. Het voordeel is dat een inbraak op de
website geen sleutelbos voor de apparatuur van anderen oplevert. Zie
[`security.md`](security.md), waar die belofte nu in zijn smallere, ware vorm
staat.

### De drie stiltes uit elkaar houden

Een sweep die in stilte eindigt heeft drie oorzaken die er van een afstand
identiek uitzien, en dit is waar iemand een half uur verliest. De monitor weet al
genoeg om ze te scheiden, dus meldt de site welke van de drie het is:

| Wat de monitor meldt | Diagnose | Wat het oplost |
|---|---|---|
| De login antwoordde nooit, en we horen de adverts van de node **niet** | Buiten bereik | Hier valt niets te doen — het is een radioprobleem |
| De login antwoordde nooit, maar we horen zijn adverts **wel** | Niet binnengelaten: onze sleutel staat niet in zijn toegangslijst, of het wachtwoord klopt niet | `setperm <our-pubkey> 3` aan de overkant, of het juiste wachtwoord |
| De login lukte, elk commando blijft stil | **Alleen-lezen.** Binnen als lezer, niet als beheerder | `setperm <our-pubkey> 3`, of het beheerderswachtwoord |
| Commando's antwoorden | In orde | — |

De derde regel is de verraderlijke: alles ziet er gezond uit en er komt niets
terug. De gehoorde lijst is het enige wat de eerste regel van de tweede scheidt —
komen zijn adverts binnen, dan houdt "kan hem niet bereiken" op de verklaring te
zijn en wordt "mag niet" het.

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

### Hoe het werkt, en wat het kost

**Er was geen firmwarewijziging voor nodig.** De monitormachinerie doet dit al:
een regel in de monitorlijst zonder wachtwoord logt in met een lege tekenreeks,
en een pollronde vraagt precies die drie dingen op. Ontdekken stuurt die
machinerie over HTTP aan en meldt de uitslag eerlijk.

Een uitvraag is twee aanroepen naar `/api/mon` van de afzender: `act=add` met de
sleutel en zonder wachtwoord, en daarna `act=poll`. Twee aanroepen en niet één,
want de firmware voert per doorloop van de lus één wijziging in de monitorlijst
door — een poll in dezelfde beweging zou op *"previous change not applied yet"*
stuiten.

**Het is geen momentopname, en de pagina zegt dat.** Uitvragen betekent dat de
node aan de monitorlijst van de afzender toegevoegd wordt, dus gaat hij mee in
elke volgende pollronde tot iemand hem verwijdert. Iets anders voorwenden zou de
firmware verkeerd voorstellen, en daarom staat er bij elke uitslag een knop
*Vergeten*.

**De kosten staan er vóór de klik**, inclusief het deel dat je makkelijk
weglaat: de firmware kent geen poll van één node. `MA_POLL` zet
`_mon_next_round` en de ronde loopt de hele lijst af, dus een uitvraag kost
`(gemonitord + 1) × 4` verzoeken. Het getal voor het slechtste geval op de
pagina is kloktijd en geen zendtijd — `MON_STEP_MS` is 30 s per antwoord over
vier stappen — en juist dat slechtste geval is waarop je toetst: een node die
antwoordt is in seconden klaar, een node die stil blijft kost de volle
wachttijd.

### Vier uitkomsten, want "geen antwoord" is drie verschillende dingen

Het oordeel valt vijf minuten na de uitvraag. De monitor weet genoeg om te
scheiden wat anders allemaal als stilte zou lezen: zijn monitorlijst meldt de
loginuitslag per regel, en zijn gehoorde lijst zegt of de adverts van de node
nog binnenkomen.

| Uitkomst | Wat het betekent | Zekerheid |
|---|---|---|
| **antwoord binnen** | er kwamen versere cijfers binnen | gemeten |
| **buiten bereik** | de login bleef onbeantwoord *en* er zijn geen adverts | een radioprobleem, geen instelling |
| **gastwachtwoord ingesteld** | de login bleef onbeantwoord terwijl we hem *wel* horen | **een vermoeden** — dat we hem horen bewijst dat hij bestaat, niet waarom hij zwijgt |
| **niet ondersteund** | de login lukte en daarna kwam er niets | ongebruikelijk; een gast hoort de status zonder rechtencontrole te krijgen |

De derde regel is op de pagina met opzet voorzichtig geformuleerd. De operator
kan `set guest.password` gedraaid hebben, of kan met een toegangslijst werken
waar wij niet in staan; van hieraf zien die twee er identiek uit, en de
formulering zegt dat in plaats van er een te kiezen.

Het oordeel wordt geveld wanneer de pagina geopend wordt en niet op een timer.
Dat is een keuze: het antwoord doet alleen ter zake wanneer iemand ernaar kijkt,
en een achtergronddraad die uitvragen beoordeelt waar niemand naar vroeg zou
machinerie om niets zijn.

### Waar het verzoek vandaan komt

Eén functie, `discovery.sender()`, en dat is vandaag het hele verhaal: de eerste
repeater met onze firmware en een beheeradres. Met één zo'n node is de keuze
geen keuze.

Het minimum is **1.4.0**, en met opzet niet het minimum van het `cmd`-topic —
ontdekken raakt dat topic nooit aan. Wat het nodig heeft is de
monitormachinerie met de loginuitslag (1.3.1) en de tellers per verzoeksoort
(1.4.0), want de drie stiltes rusten daarop. Strenger zijn dan nodig zou een
node uitsluiten die het prima kan.

Zodra er meer dan één kandidaat is wordt dit een geordende lijst met terugval,
dezelfde vorm die `monitors.candidates()` al heeft — en alleen die ene functie
verandert.

### Openstaande vraag: wat er publiek mag

**Niets, voorlopig.** Dit alles zit achter de inlog van het beheer, en dat is
niet het definitieve antwoord maar het eerlijke tussenantwoord.

Telemetrie die zonder inloggegevens van andermans node gehaald is, is een andere
categorie dan de cijfers die je eigen repeaters aan jou publiceren. Hun operator
heeft er niet voor gekozen om op deze site te staan; hij liet een gastdeur open
staan, en dat is niet hetzelfde als toestemming om te publiceren. En de site is
bereikbaar vanaf het publieke internet.

Het voorstel, voor wie erover beslist: **per node, standaard uit, met de naam
van de operator erbij en een manier om bezwaar te maken** — dezelfde vorm als de
bestaande schakelaars `show_position` / `show_name`, en geen nieuw mechanisme.
Overzichten in het groot (hoeveel repeaters er bereikbaar zijn, een
dekkingskaart) leggen een zwakkere claim op iemand anders zijn gegevens dan een
pagina met zijn batterijspanning erop, dus als er iets toegestaan wordt, zijn
dat de eerste kandidaten.

Die beslissing is makkelijker te nemen met echte gegevens op het scherm dan
vanuit een beschrijving, en daarom kwam het uitlezen eerst.

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
| Niveaus als expliciet begrip in code en UI | **gebouwd** — `level` / `level_why` op `commanding.describe()`, en `/admin` groepeert erop |
| De klok zetten, met de hand en dagelijks | **gebouwd**, zie [`clocksync.md`](clocksync.md) |
| Firmware-upgrade over HTTP, met checksum en rollback | **gebouwd**, zie [`firmware-upgrade.md`](firmware-upgrade.md) |
| `ota_route()` als aparte sleutel voor wat er kan | **gebouwd** |
| `niet_teruggekomen` als toestand die blijft tot ze gezien is | **gebouwd** |
| Instellingen schrijven naar een `full_managed` node met een IP-pad | **gebouwd** — firmware 2.1.0 `POST /api/cfg`: het hele CLI-oppervlak op drie na, bediening per type, bevestiging per risicoklasse, met teruglezen. Vereist dat het beheeradres van de node ingevuld is |
| Instellingen schrijven naar een `full_managed` node zonder weblogin | **gebouwd** — firmware 2.8.0, `set <param> <waarde>` op het `cmd`-topic, met het teruglezen gemeld in het eerstvolgende statistiekbericht. Alleen risicoklasse 1 en 2, en de node toetst tegen zijn eigen tabel |
| Radio-instellingen van afstand schrijven | **geweigerd op elk vervoermiddel**, in de server en nog eens in de firmware. `tx` mag wel; frequentie, bandbreedte, spreidingsfactor en coderingssnelheid niet |
| Instellingen schrijven naar een `semi_managed` node over LoRa | **gebouwd** — firmware 2.4.0, `POST`/`GET /api/moncfg` op de **monitor**, plus `wifi mon set` vanaf elke CLI. `set` en dan `get`, één tegelijk, begrensd, met het teruglezen als gemelde uitslag. Dezelfde tabel, grenzen, risicoklassen en rechten als de weg over IP |
| Schrijven naar de WiFi- en MQTT-instellingen van een node | **wordt hier niet aangeboden.** Dat zijn de onze en niet die van MeshCore, en ze hebben al hun eigen formulieren op de beheerpagina van de node zelf en in de `wifi`-CLI |
| Bevestigen-of-terugdraaien | **onderzocht en verworpen**, met de redenering hierboven |
| Automatisch rechten ontdekken | **verworpen**, in plaats daarvan aanwijzen-en-één-keer-proberen |
| Vergelijkingstabel over repeaters heen | **gebouwd** — `/admin/compare`, gekozen kolommen, afwijkingen van de meerderheid gemarkeerd |
| Bewerken vanuit die tabel | **gebouwd** — een potloodje per te zetten cel opent één bewerkveld onder de tabel, voorgevuld. Één bewerkveld en niet een invoerveld in elke cel: twintig nodes maal zes kolommen is honderdtwintig formulieren met elk hun eigen bevestiging, en juist die bevestiging wordt dan onleesbaar. Het loopt door dezelfde `nodeconfig.write()` als de nodepagina, dus de risicoklassen, de grenzen en het teruglezen gelden onverkort |
| Meerdere nodes tegelijk bewerken | **niet gebouwd, en in het ontwerp al ingeperkt**: alleen parameters uit de klasse Gewoon, nooit de twee zwaardere klassen. Tien nodes in één klik is ook tien nodes kwijt in één klik |
| Mesh-transport forceren voor een node die een IP-pad heeft | **niet gebouwd.** Het LoRa-schrijfpad bestaat nu wel, dus wat ontbreekt is alleen de keuze: de weg volgt uit de node in plaats van gekozen te worden. Het blijft eruit tot er een node is die zowel gemonitord als over IP bereikbaar is om het op te beproeven |
| Telemetrie opvragen zonder inloggegevens | **gebouwd** — `/admin/discovery`, aanwijzen-en-uitvragen, vier uitkomsten, de kosten in beeld vóór de klik. Geen firmwarewijziging voor nodig; de afzender heeft 1.4.0 nodig |
| Ingestelde monitortoewijzing (geordend, per node en per groep) | **gebouwd** — `/admin/monitors`, geordend per node en per groep, de controle in de keuzelijst, de waarneming naast de configuratie, en de uitslag van de laatste poging |
| Andermans telemetrie publiceren | **met opzet niet gebouwd.** Achter de inlog van het beheer tot iemand beslist; het voorstel staat hierboven |
| MeshCore-versie van doorgestuurde nodes | **gebouwd** — `ver` gaat mee in de sweep, en één antwoord vult allebei de versiekolommen |
| Een sweepschema per node | **gebouwd** — standaard uit, één ronde tegelijk met een globale minimale tussenruimte, en een plafond per dag over alle nodes heen |
| Nagaan of een ingeplande ronde iets opgeleverd heeft | **gebouwd** — `gevraagd` is geen uitkomst meer; een tijdstempel als vertrekpunt plus een oordeel na tien minuten scheidt "antwoord binnen" van "niets teruggekomen" |
| Opdrachten sturen naar het topic waar een node werkelijk op luistert | **opgelost** — het nodedeel wordt bewaard zoals het aankwam in plaats van uitgerekend uit de genormaliseerde sleutel. Raakte elke opdracht die de site verstuurde, niet alleen de planner |
| Waarschuwen voor een ontbrekend beheeradres voordat er op een knop gedrukt wordt | **gebouwd** — full managed nodes zonder adres worden bovenaan de nodelijst genoemd |
| Tonen welk recht een monitor per doelnode gebruikt | **gebouwd** — toegangslijst of wachtwoord, gelezen uit de monitorlijst van de monitor zelf, die meldt *dát* er een wachtwoord staat en nooit *welk* |
| De drie stiltes uit elkaar houden | **gebouwd** — buiten bereik / niet binnengelaten / alleen lezen, uit de loginstatus plus de gehoorde lijst |
| Het wachtwoord van een doelnode vanaf de site zetten | **gebouwd als doorgeefluik** — `nodeconfig.push_monitor_password()` stuurt het naar de monitor en bewaart niets. Nog niet op een pagina: het formulier is het resterende stuk |

> **JessaZH krijgt alleen lege schrijfacties.** De weg is gebouwd en getest tegen
> een nagebootste monitor in de testsuite; op de echte repeater is het eerste en
> voorlopig enige wat er geschreven wordt een parameter die op zijn eigen waarde
> gezet wordt. Hij wordt alleen over LoRa bereikt, dus een vergissing daar is niet
> te herstellen, en hij is bovendien het referentiegeval waarvoor dit ontwerp
> bestaat. Echte wijzigingen gaan er pas heen als iemand zo'n lege schrijfactie
> heeft zien slagen.

## Het pakketfilter

Een pakketfilter bepaalt welke van *andermans* pakketten een repeater nog
doorstuurt. Het staat standaard uit, het geldt per node, en het is de enige
instelling op deze pagina waarvan de storing eruitziet als een kerngezonde node.

Dat is meteen de reden dat het hier een eigen hoofdstuk krijgt en geen regel in
de instellingentabel. Zet een frequentie verkeerd en de node valt stil —
vervelend, maar je merkt het binnen het uur. Zet een filter verkeerd en de node
blijft antwoorden, blijft adverteren, staat op elke pagina groen, en stuurt
stilletjes niets meer door. Je merkt het als iemand klaagt dat zijn berichten
niet meer aankomen, en dat kan dagen duren.

**Weet hoe je hem uitzet vóór je hem aanzet.** Drie wegen, op volgorde van hoeveel
er nog moet werken:

1. `filter off` of `filter reset` **over de mesh-CLI**. Geen WiFi, geen
   beheerpagina, geen server — LoRa staat er eerder dan die alle drie. Dit is de
   weg die werkt als de andere het niet doen.
2. De knoppen in het blok *Pakketfilter* op de beheerpagina van de node.
3. `POST /api/filter` met `cmd=off` op de node zelf.

Op de site zijn die twee ook de *goedkoopste* handelingen in het rechtenmodel —
`node.filter.gewoon`, lichter dan een filter aanzetten. Een rol die een filter
niet aan mag zetten, mag er wel een uitzetten. Herstel mag nooit strakker
afgeschermd zijn dan de fout die het terugdraait.

### Wat je kunt zetten, en wat het kost

De zes soorten regel, wat ze blokkeren en wat elk ervan kost, staan in
[`packet-filter.md`](packet-filter.md). Twee ervan verrassen mensen, dus die
staan hier nog een keer:

- **Een kanaal blokkeren vraagt de kanaalsleutel, niet de naam.** Alles wat een
  repeater ziet is één byte: `sha256(kanaalsleutel)[0]`. En één byte botst:
  ruwweg één kanaal op 256 deelt hem, en dat verkeer gaat mee.
- **"Misvormd" betekent structureel onmogelijk**, niet "de tekst ziet er raar
  uit". De inhoud is versleuteld met een sleutel die een repeater niet heeft.

### De drie risicoklassen, toegepast op filters

Dezelfde drie als bij instellingen, en de klasse volgt wat een regel
*blokkeert* en niet hoe het formulier eruitziet. `hops 05 4` en `hops 05 0` zijn
hetzelfde invoerveld: de eerste verkort het bereik van groepstekst, de tweede
zet hem stil. Dus vraagt de tweede om de naam van de node en de eerste alleen om
een `ja`.

`filter on` gaat een klasse omhoog als zo'n regel al klaarstaat — want dán is dat
de klik die het verkeer werkelijk stilzet, en niet de klik die de regel schreef
terwijl het filter uitstond.

### Zien dat er een aanstaat

Kijk op drie plaatsen, waarvan je er geen hoeft te zoeken:

- het blok **Pakketfilter** op de nodepagina, dat toont wat de node in zijn
  laatste statistiekenbericht meldde, inclusief wat hij weggooide en waarom;
- de kolom **Pakketfilter** in de vergelijkingstabel, standaard in beeld — "op
  welke node staat er iets aan" is een vraag over de verzameling;
- het `filter`-object in `GET /api/v1/repeaters/{slug}`, dat publiek is, want de
  mensen die verkeer missen zijn niet de mensen met een inlog.

Alle drie houden "nooit iets gemeld" (meestal firmware ouder dan 2.3.0) en
"meldt dat er niets aanstaat" uit elkaar. Die tot één leeg vakje platslaan maakt
precies de vraag onbeantwoordbaar waarvoor dit bestaat.

---

## Zie ook

- [`admin.md`](admin.md) — de pagina's zelf: elk veld, elk formulier, de ordening
  op onomkeerbaarheid
- [`commanding.md`](commanding.md) — hoe de weg en het niveau berekend worden, en
  elke blokkadewaarde
- [`clocksync.md`](clocksync.md) — of deze machine de mesh mag vertellen hoe laat
  het is
- [`firmware-upgrade.md`](firmware-upgrade.md) — het upgrademechanisme van begin
  tot eind
- [`mqtt.md`](mqtt.md) — de topics, en de vier woorden die de site mag publiceren
- [`firmware.md`](firmware.md) — de nodefirmware bouwen en flashen
