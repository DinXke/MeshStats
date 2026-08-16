# Kloksynchronisatie

*[English](../clocksync.md)*

`server/app/clocksync.py` beantwoordt één vraag voordat het iets doet: **mag deze
machine de rest van het mesh vertellen hoe laat het is?**

[`mqtt.md`](../mqtt.md#setting-the-clock) beschrijft het bericht op de draad en
wat de firmware ermee doet. Dit document gaat over de beslissing aan de
serverkant.

## Waarom de site degene is die het weet

Een MeshCore-node zet zijn eigen klok nooit goed. Een ESP32 zonder gebufferde
RTC begint bij wat de firmware erin gebakken heeft — MeshCore's `clkreboot` zet
hem letterlijk op 15 mei 2024 — en loopt daarna langzaam weg. Een repeater op een
dak herstart uit zichzelf: lege accu, watchdog, een stroomonderbreking in het
onweerseizoen. Elke keer komt hij terug met een klok die niets met vandaag te
maken heeft, en alles wat hij daarna zegt draagt die tijd mee.

Niemand op het mesh kan dat corrigeren, want niemand op het mesh weet het beter.
Deze machine wel, want die staat op een netwerk waar een NTP-cliënt draait. Dat
is de hele reden dat deze module bestaat, en meteen ook haar enige zwakke plek:
de bewering "wij weten hoe laat het is" moet waar zijn voordat we hem uitsturen.

## Waarom de controles zo streng zijn

De correctie gaat één kant op en is niet terug te draaien. De firmware zet een
klok alleen **vooruit**, en dat is geen eigenzinnigheid van ons: een advert
draagt de klok van de node die hem uitzendt, en iedere node die de afzender al
kent gooit een advert weg waarvan de tijdstempel niet gestegen is
(`onAdvertRecv` in `MyMesh.cpp`). Een klok een uur terugzetten is dus een uur
onzichtbaarheid voor een repeater op een dak. Daarom corrigeert de firmware nooit
terug — en daarom is een tijd die te ver in de **toekomst** ligt hier een fout
die je niet meer goedmaakt zonder ter plaatse te gaan.

Eén foute publicatie van deze module smeert dus een foute klok uit over elke node
die eraan hangt, en de weg terug loopt over een dak. Vandaar: bij twijfel niet
publiceren, en luid zeggen waarom niet.

## Wat we hier feitelijk kunnen vaststellen

Eerlijk zijn over de reikwijdte van deze controles is hier belangrijker dan een
groen vinkje.

### De hoofdcontrole: `adjtimex(2)`

`kernel_clock()` leest de tijddiscipline van de kernel via `adjtimex` met
`modes=0` — een leesoproep; `adjtimex` mét modes zou de klok bijsturen en daar
hebben we geen rechten voor en geen reden toe. Dat is precies waar `timedatectl`
zijn `NTPSynchronized` vandaan haalt: de vlag `STA_UNSYNC` in het statusveld,
plus de foutmarge die de kernel zelf bijhoudt. Het vraagt geen rechten, geen
extra pakket en geen `timedatectl` in de container — die er in een slim
Python-image ook niet is.

`ok` is alleen waar als **beide** signalen schoon zijn, want ze zeggen niet
hetzelfde:

| Signaal | Wat het is |
|---|---|
| `STA_UNSYNC` niet gezet, en `rc != TIME_ERROR` | Het late oordeel: de kernel heeft het niet opgegeven |
| `maxerror ≤ MAX_ERROR_S` | Het vroege oordeel: de marge loopt nog niet ongecontroleerd op |

Wachten op alleen het late oordeel zou betekenen uren doorgaan met een klok
waarvan de kernel zelf al niet meer zeker is. De kernel laat `maxerror` tussen
twee NTP-correcties groeien met 500 ppm en geeft het bij 16 s op, dus de
standaard van **10 s** is ruwweg "de host is in de laatste vijfeneenhalf uur nog
bijgestuurd" — streng genoeg om een NTP-cliënt te betrappen die vanmiddag
gestopt is, ruim genoeg om een normale pollcyclus tot 1024 s nooit te raken.

**Waar dat oordeel vandaan komt, doet ertoe.** Deze app draait in een container
in een LXC-container op een Proxmox-host. Een LXC deelt de klok van zijn host en
mag hem niet zetten; `timedatectl` in de LXC meldt dan ook `NTP=no` (geen
NTP-cliënt hier) naast `NTPSynchronized=yes` (de kernel is wél gedisciplineerd).
Wat hier gelezen wordt is dus het oordeel van de **host-kernel**, doorgegeven. Dat
is het beste signaal dat vanaf deze plek bestaat, maar het is een doorgegeven
bewering en geen eigen meting: "de host zegt dat hij gelijkloopt" is iets anders
dan "de tijd is aantoonbaar juist". De beheerpagina zegt het in die woorden, en
niet in geruststellender.

Het praktische gevolg hoort in elk rapport over deze functie en niet alleen hier:
**de juistheid van elke klok in dit mesh hangt uiteindelijk aan de NTP-instelling
van de Proxmox-host.** Loopt die fout, dan loopt dit alles keurig, meetbaar en
volledig verkeerd mee.

Alles wat `adjtimex` onbeschikbaar maakt — geen Linux, geen libc, een kernel die
het niet aanbiedt — is een **weigering**, nooit een "waarschijnlijk wel goed".
Daaronder valt ook Windows, waar `ctypes.CDLL(None)` een `TypeError` opwerpt; daar
draait dit nooit in productie, maar wél als iemand de tests of de app lokaal
start, en dan hoort het antwoord "niet beschikbaar" te zijn en geen stacktrace.

### De tweede controle: springt de wandklok?

`_jump_check()` kost niets en gelooft de kernel niet op zijn woord. De wandklok
en `time.monotonic()` horen even snel te lopen. Verschuift de wandklok terwijl de
monotone klok dat niet doet, dan is de tijd **gezet** in plaats van verlopen. Dat
mag — een NTP-cliënt hoort bij te sturen — maar een correctie hoort klein te
zijn. Een sprong van een uur is iets anders, en dan is de vraag welke van de twee
kanten de juiste was; die vraag kunnen wij niet beantwoorden, dus publiceren we
niet. `MAX_JUMP_S` staat standaard op 30 s, met opzet ruim: een dagelijkse
correctie van een halve seconde is gezond gedrag en geen alarm.

Het referentiepaar leeft **per proces** en gaat niet naar schijf. Een monotone
klok betekent niets meer na een herstart, dus bewaren zou een vergelijking
opleveren die alleen maar overtuigend lijkt. Het paar schuift altijd mee, ook na
een afkeuring — anders rapporteert elke volgende ronde dezelfde sprong opnieuw en
blijft de functie voorgoed uit na één correctie.

### De derde controle: is de tijd achteruitgegaan?

`_backwards_check()` overleeft wél een herstart. De hoogste wandkloktijd die deze
site ooit zag, staat in `settings` onder `clocksync_high_water`. Het vangt het
geval waarin de host opstart zonder netwerk, de klok op zijn RTC-waarde of de
bouwdatum zet, en NTP nog niet is langsgeweest — terwijl `adjtimex` op zo'n
moment best tevreden kan zijn. De marge is `MAX_JUMP_S`, omdat dit een grens is
en geen meting: een paar seconden achteruit is een NTP-correctie, een dag
achteruit is een klok die opnieuw begonnen is.

### Twee controles die verworpen zijn

**Kruiscontrole tegen het mesh.** De suggestie ligt voor de hand — er komen
tijdstempels binnen van nodes — maar de redenering is rond: de nodes waartegen we
zouden controleren zijn precies de nodes die hun tijd van ons krijgen. Vinden we
dat ze gelijklopen, dan hebben we bewezen dat ons eigen bericht is aangekomen.
Bovendien draagt het `rx`-bericht `t` als uptime-teller en niet als wandklok, dus
de bruikbare bron is er niet eens.

**Navragen bij een externe tijdbron.** Deze server zit achter VPN/LAN en heeft
geen uitgaande weg naar een NTP-server of een HTTP-`Date`-header die iets
bewijst. Een controle die in de ontwikkelomgeving werkt en op de echte machine
altijd "onbereikbaar" zegt, is een controle die na een week wordt uitgezet.

## Wie het bericht krijgt

Twee wegen, en het verschil is de reden dat `time_route()` naast
`commanding.route_for()` bestaat:

| Geval | Bericht gaat naar | Wat die node dan doet |
|---|---|---|
| De repeater publiceert zelf | Zichzelf | Zet zijn eigen klok en loopt daarna zijn monitorlijst af |
| De repeater wordt doorgestuurd (de dakrepeater) | Zijn **monitor** | Zet zijn eigen klok en controleert de klokken van **alle** nodes die hij monitort |

Er is geen argument om het tweede geval toe te spitsen, en dat is geen omissie:
de firmware loopt bij een klokronde de hele lijst af, want de ronde is per node
goedkoop en per gemonitorde node één heen-en-weer. De pagina hoort dat te zeggen
in plaats van te doen alsof de knop deze ene repeater aanwijst.

`allow_monitor=False` sluit het tweede geval uit. Dat is wat de dagelijkse ronde
nodig heeft: die loopt over *alle* repeaters, en twee doorgestuurde repeaters met
dezelfde monitor zouden hem anders twee keer hetzelfde bericht sturen.

`time_route()` geeft bij elke weigering een `blocker` en een `why` terug, zodat
de beheerpagina kan uitleggen waarom een repeater ontbreekt in plaats van hem
stilletjes weg te laten:

| `blocker` | Betekenis |
|---|---|
| `relayed` | Krijgt zijn tijd van zijn monitor, over LoRa (alleen bij `allow_monitor=False`) |
| `no_source` | Publiceert helemaal niet over MQTT |
| `http_source` | Komt binnen via de HTTP-API, niet over MQTT |
| `relay_unknown` | De doorstuurder is hier zelf geen bekende repeater |
| `no_fw` | Moduleversie onbekend |
| `old_fw` | Vraagt nodefirmware `MIN_TIME_VERSION` (1.10.0) of nieuwer |
| `stale` | Al langer dan `NODE_STALE_SECS` (6 u) niets van die node gehoord |
| `broker_down` | De site hangt op dit ogenblik niet aan de broker |

**De firmware van de ontvanger telt**, en bij een doorgestuurde repeater is dat
die van de monitor. De versie van het onderwerp zegt hier niets — een node die
zelf niet publiceert meldt nergens een versie. De versiegrens is 1.10.0 langs
*beide* wegen, anders dan bij `commanding.route_for()` waar ze van de weg afhangt
(1.8.0 rechtstreeks, 1.9.0 via een monitor): het is dezelfde ontvanger die
hetzelfde woord moet kennen. Die twee in één functie proppen zou betekenen dat
`route_for` per commando een andere versie gaat uitrekenen, en dat is precies de
soort vertakking waar een verkeerde knop uit rolt.

`NODE_STALE_SECS` is hier 6 uur, ruimer dan `commanding.NODE_STALE_SECS` (1 u),
want dit is een weigering en geen waarschuwing: publiceren naar een node die al
een dag stil is kost niets, maar het vult de logboeken met beloftes.

## De dagelijkse ronde

`run_once()`, in deze volgorde, en de volgorde is de hele functie:

1. `check_clock()` — **vóór er ook maar één node uitgezocht wordt**, zodat er geen
   pad bestaat waarlangs een bericht vertrekt terwijl de controle nog moest
   komen.
2. Faalt die: een weigering tellen, de reden noteren, loggen op **WARNING** — dit
   is de toestand waarin de functie stilvalt, en stil stilvallen is wat dit
   project niet doet.
3. Hangt de broker er niet: dat noteren en stoppen.
4. `targets()` — elke repeater, via dezelfde `time_route()` die de knop gebruikt,
   met de monitorweg dicht.
5. Publiceren per node die in aanmerking komt, met `time.time()` **per node**
   gelezen.

Een ronde over een handvol nodes duurt milliseconden, dus één epoch hergebruiken
zou nauwelijks schelen — maar het zou betekenen dat de laatste node een tijd
krijgt die ouder is dan het bericht zelf, en dat is precies het soort detail waar
dit bestand over gaat.

### Waarom dagelijks

Klokdrift op een ESP32 is traag: enkele seconden per dag, tientallen bij een
slechte oscillator of een hete zolder. De firmware corrigeert een gemonitorde
node pas vanaf twee minuten afwijking, dus dagelijks vragen is ruim een orde van
grootte vaker dan nodig om binnen die drempel te blijven — en zendtijd is het
schaarse goed, niet rekenkracht.

Wat het interval wél bepaalt is iets anders: **hoe lang een node die zojuist
herstart is, met een klok uit 2024, mag rondlopen voor iemand hem bijzet.** Een
dag is daarvoor de bovengrens die we accepteren. Korter zou die vensters
verkleinen zonder dat het meetbaar iets aan de drift doet, en het kost elke keer
opnieuw zendtijd op het dak. De node bewaakt zijn eigen kant trouwens ook: hij
doet de LoRa-helft hoogstens één keer per uur, wat er ook binnenkomt.

`FIRST_RUN_DELAY_S` is 300 s na het opstarten. Kort, maar niet meteen: de
MQTT-verbinding moet er zijn en de nodes moeten zich gemeld hebben, anders
strandt de eerste ronde altijd op "geen brokerverbinding". Het is ook nuttig na
een herstart van de site die op een stroomstoring volgde — dan is er een goede
kans dat de nodes óók net herstart zijn, met een klok uit 2024.

`start()` meet en logt bovendien één keer bij het opstarten **zonder te
publiceren**, zodat in het logboek van dag één staat of deze machine überhaupt in
aanmerking komt, in plaats van pas over vijf minuten — of nooit, als de eerste
ronde op iets anders strandt.

## De knop

`POST /admin/repeaters/{rid}/clocksync` → `clocksync.sync_now()`.

**Het is geen tweede code-pad**, en dat is het punt. De klokcontrole is letterlijk
dezelfde `check_clock()` die de planner aanroept, en het versturen loopt door
dezelfde `publish_command()` met dezelfde venstercontrole op de epoch. Een knop
die zijn eigen weg naar de broker had gehad, zou een achterdeur om die controles
heen zijn geweest — en de enige zichtbare aanwijzing daarvoor zou een verkeerde
klok op een dak zijn geweest, weken later.

Wat de knop **niet** overdoet, is de driftdrempel en de weigering voor een node
die voorloopt. Die staan in de firmware, bij de code die meet en zendt, en ze
gelden hier dus vanzelf: dit bericht is hetzelfde bericht.

`sync_now()` geeft een `outcome` terug die de pagina in een zin omzet. Elk geval
apart, want "er is niets gebeurd" heeft hier zes verschillende oorzaken en vijf
ervan kan de gebruiker zelf verhelpen:

| `outcome` | Betekenis |
|---|---|
| `disabled` | `MM_CLOCKSYNC_ENABLED=0` |
| `no_route` | Geen weg naar deze repeater; `blocker` en `reason` zeggen welke |
| `no_clock` | Deze machine zakte voor haar eigen klokcontrole; `reason` is de formulering van de controle zelf |
| `too_soon` | Binnen `MANUAL_MIN_GAP_S`; `wait_min` zegt hoe lang |
| `failed` | De publicatie is niet van deze machine vertrokken |
| `sent` | Gepubliceerd |

De volgorde erin doet ertoe: **de klokcontrole staat vóór de wachttijd**, niet
erna. Een server die niet weet hoe laat het is, hoort dat te zeggen — ook, en
juist, als het antwoord anders "wacht nog even" was geweest. Andersom zou iemand
een uur wachten om dan pas te horen dat het sowieso niet kon.

### De wachttijd, en de ene uitzondering

`MANUAL_MIN_GAP_S` is 3600 s, met opzet hetzelfde getal als `MON_CLK_MIN_GAP_MS`
in de firmware. Wat het wel en niet is, want dat scheelt:

Het is **geen** veiligheidsmaatregel. Die staat in de firmware, bij de code die
de radio bezit, en ze is absoluut — honderd keer klikken levert daar hoogstens
één LoRa-ronde per uur op, wat er ook op het `cmd`-topic binnenkomt. De band valt
met deze knop dus niet te bezetten, ook niet als deze regel er niet stond.

Wat het wél is: eerlijkheid in de knop. Binnen het uur zou publiceren de node
alleen zijn eigen klok laten zetten — en die is dan net gezet door het vorige
bericht — terwijl de ronde langs de gemonitorde repeaters overgeslagen wordt
zonder dat de pagina daar iets van ziet. "Verstuurd" melden terwijl de helft die
ertoe doet niet gebeurt, is precies de belofte die `commanding.py` ooit moest
wegwerken.

De ene uitzondering is de moeite: `_rebooted_since()` laat de wachttijd vervallen
voor een node die intussen herstart is. Zo'n node staat op de datum uit zijn
firmware — precies de toestand waarvoor dit alles bestaat — terwijl onze eigen
administratie zegt dat we hem twintig minuten geleden nog de tijd stuurden. De
knop zou dan "wacht nog veertig minuten" melden, precies wanneer wachten het
slechtste antwoord is.

De uptime komt uit het laatste statistiekbericht en is dus zelf al even oud;
daarom wordt de ouderdom van dat bericht erbij geteld. Zonder die correctie zou
een node die tien minuten stil was er tien minuten jonger uitzien dan hij is, en
dat is de kant die valse toestemming geeft.

## Boekhouding

`clocksync_sent` in `settings` bevat `{node_hex: epoch}` voor het laatste
tijdbericht per node, begrensd op `_SENT_MAX` = 50 sleutels (oudste eerst eruit).
Zonder dat zou de handmatige knop "verstuurd" melden voor een ronde waarvan de
node de dure helft overslaat.

`_publish_time(node, when)` gebruikt de `when` van de beller voor **allebei**: het
bericht én de notitie. Dat lijkt een detail en was het niet: toen deze functie
zelf `time.time()` las, stond er in de administratie een ander ogenblik dan er
verstuurd was — onzichtbaar in productie, waar ze microseconden schelen, maar het
betekende ook dat de wachttijdberekening in `sync_now()` over een andere klok
redeneerde dan degene die de notitie schreef. Eén ogenblik, één waarde.

Alleen bij succes wordt er onthouden. Een mislukte publicatie mag de knop geen
uur lang laten zeggen dat er net gesynchroniseerd is.

`status()` voedt de beheerpagina:

| Sleutel | Betekenis |
|---|---|
| `enabled`, `interval_hours` | Configuratie |
| `last_run`, `last_ok` | Laatste poging, laatste **geslaagde** publicatie |
| `last_result`, `last_reason` | Wat er gebeurde, en waarom er niets vertrok als er niets vertrok |
| `published`, `skipped`, `runs`, `refusals` | Tellers; `refusals` zijn rondes die op de klokcontrole strandden |
| `clock` | De laatste uitslag van `check_clock()` voluit |
| `last_manual`, `manual_node` | De laatste handmatige synchronisatie, **apart** van `last_run`/`last_ok` |

De handmatige velden staan apart omdat het een andere gebeurtenis is: die twee
gaan over de planner, dit over iemand die op een knop drukte. Ze samen in één
veld tellen zou een beheerpagina opleveren waarop niet te zien is of de
dagelijkse ronde nog draait.

## Het bericht op de draad

`mqtt_ingest.publish_command(node, "time", epoch=...)` publiceert
`time <epoch>` op het `cmd`-topic van die node: UNIX-seconden in UTC, wat MeshCore's
eigen CLI parseert in de `time `-tak van `CommonCLI::handleCommand` (`_atoi` van
de rest van de regel, rechtstreeks naar `setCurrentTime`).

De epoch is aan beide kanten begrensd, met `MIN_EPOCH` (2025-01-01) en
`MAX_EPOCH` (2100-01-01), dezelfde grenzen als
`CLOCK_MIN_EPOCH`/`CLOCK_MAX_EPOCH` in `MeshManagerNet.cpp`. Aan beide kanten
controleren is geen dubbel werk maar de goedkoopste plaats van de twee: een node
zet zijn klok alleen **vooruit**, dus een tijd die te ver in de toekomst ligt is
aan de overkant niet meer terug te draaien zonder er met een kabel bij te gaan
staan. Een vergissing hier hoort hier te stranden.

Een epoch buiten het venster **geeft False terug in plaats van op te werpen**:
dat is de weg waarlangs een kapotte serverklok binnenkomt, en dat is een toestand
van de machine en geen fout in de aanroep. De beller ziet "niets vertrokken" en
kan dat melden. Een ontbrekende epoch bij `time` werpt *wel* op, want dat is een
programmeerfout en hoort stuk te gaan bij het schrijven, niet in productie.

Er wordt niets bewaard en QoS is 0 — zie
[`commanding.md`](commanding.md#qos-0-en-retainfalse).

## Configuratie

| Variabele | Standaard | Betekenis |
|---|---|---|
| `MM_CLOCKSYNC_ENABLED` | `1` | `0`, `false`, `no`, `nee`, `off` of leeg zet het uit |
| `MM_CLOCKSYNC_HOURS` | `24` | Uren tussen twee rondes, minimaal 1 |
| `MM_CLOCKSYNC_MAX_ERROR_S` | `10` | Onzekerheid van de kernel die nog geloofd wordt |
| `MM_CLOCKSYNC_MAX_JUMP_S` | `30` | Wandklok tegenover monotone klok, en de marge voor achteruitlopen |

Uit zetten is een geldige keuze: wie zijn nodes met de hand bijzet, of wie deze
server niet genoeg vertrouwt om er een mesh op te ijken, hoort dat te kunnen
zeggen zonder de firmware terug te draaien.

## Tests

`server/tests/test_clocksync.py` dekt de drie controles apart en samen, de
wegkeuze in beide richtingen, de wachttijd en haar herstartuitzondering, en het
epochvenster.

## Verwante documenten

| Vraag | Document |
|---|---|
| Het `time`-commando op de draad | [`mqtt.md`](../mqtt.md#setting-the-clock) |
| De andere commando's, en hun wegen | [`commanding.md`](commanding.md) |
| Waar `clocksync_*` bewaard wordt | [`database.md`](database.md#settings) |
