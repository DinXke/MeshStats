# Firmware-aanpassingen

*[English](../firmware.md)*

MeshManager levert een reeks aanpassingen op de [MeshCore](https://github.com/meshcore-dev/MeshCore)-
firmware. Ze vallen uiteen in twee groepen:

- **Companion-node** (`examples/companion_radio`) — meerdere gelijktijdige
  WiFi-clients, een fix voor de kanaalteller, en de stats-publisher met zijn
  webchatclient.
- **Repeater** (`examples/simple_repeater`) — `MeshManagerNet`: WiFi met
  AP-fallback, een beheerpagina, OTA over het gewone netwerk, een telnetconsole
  op de MeshCore-CLI, MQTT-publicatie, monitoring van andere repeaters,
  kloksynchronisatie, en back-up/herstel van het bestandssysteem.

Alles is opt-in bij het bouwen. Zonder de vlaggen krijgt u standaard MeshCore.

| Bestand | Wat het verandert |
|---|---|
| `src/helpers/esp32/SerialWifiInterface.{h,cpp}` | Meerdere gelijktijdige WiFi-companions |
| `src/helpers/BaseChatMesh.cpp` | Hergebruik van kanaalslots |
| `examples/companion_radio/StatsPublisher.{h,cpp}` | MQTT-publisher + beheerpagina |
| `examples/companion_radio/page.html`, `gen_page.py`, `StatsPage.h` | De webclient, zijn buildstap en zijn gegenereerde uitvoer |
| `examples/companion_radio/MyMesh.{h,cpp}` | `fillStatsJson()`, `fillNodeIdHex()`, raw-packet-hook |
| `examples/companion_radio/main.cpp` | Koppelt de publisher in |
| `examples/simple_repeater/MeshManagerNet.{h,cpp}` | De netwerkmodule van de repeater |
| `examples/simple_repeater/PacketFilter.{h,cpp}` | Het pakketfilter — welke doorgestuurde pakketten de repeater nog doorlaat. Zie [`packet-filter.md`](packet-filter.md) |
| `repeater-hooks.patch` | De aanpassingen in `simple_repeater` (inclusief zijn `fillStatsJson()`) — **verplicht**, ze zijn wat de module inkoppelt |
| `meshmanager.patch` | De aanpassingen in beide voorbeelden, als één patch |
| `tools/verify_image.py` | Bewijst dat een gebouwd `.bin` de module werkelijk bevat |

Twee versienummers reizen samen mee en mogen niet verward worden.
`FIRMWARE_VERSION` is die van MeshCore; `MESHMANAGER_VERSION`
(`MeshManagerNet.h:158`) is die van deze module, en de twee bewegen onafhankelijk
van elkaar. `ver` drukt beide af, en beide verschijnen in elke stats-payload als
`repeater.fw` en `repeater.fw_meshmanager` — want wanneer er iets misloopt, is de
eerste vraag naar welke van de twee men kijkt.

---

## 1. Meerdere companions op één node

### Het probleem

De standaard `SerialWifiInterface` houdt precies één client bij:

```cpp
auto newClient = server.available();
if (newClient) {
    client.stop();      // the existing companion is kicked off
    client = newClient;
}
```

Verbind de telefoon en Home Assistant valt weg. Verbind Home Assistant en de
telefoon valt weg.

### De aanpassing

`WIFI_MAX_CLIENTS` slots (standaard 4). Elk slot bevat een `WiFiClient` **en zijn
eigen `FrameHeader`**:

```cpp
struct ClientSlot {
    WiFiClient client;
    FrameHeader header;
};
```

Headerstatus per slot is geen detail. Een companion-frame is een header van
3 bytes plus een payload die over meerdere TCP-reads kan binnenkomen. Met één
gedeelde header overschrijven twee clients midden in een frame elkaars lengte en
lopen beide verbindingen uit de pas.

Nieuwe verbindingen nemen een vrij slot in. Alleen wanneer elk slot bezet is,
wordt er één afgebroken, en dan is dat het slot waar `next_poll` naar wijst.

Inkomende frames worden round-robin gepolld zodat een spraakzame client de rest
niet kan uithongeren:

```cpp
for (int n = 0; n < WIFI_MAX_CLIENTS; n++) {
    int i = (next_poll + n) % WIFI_MAX_CLIENTS;
    ...
}
```

### Gerichte antwoorden — het deel dat ertoe doet

Alles naar iedereen uitzenden werkt niet. Companion-clients draaien
request/response-toestandsmachines; voed één client de `RESP_CODE_CONTACT`-stroom
van een andere en ze loopt uit synchronisatie.

Daarom worden uitgaande frames gerouteerd. `reply_slot` wordt bovenaan elke
`checkRecvFrame()` gereset en gezet zodra een commando aan de mesh wordt
doorgegeven:

```cpp
// checkRecvFrame(), before accepting anything:
reply_slot = -1;
...
// after reading a frame from slot i:
reply_slot = (int8_t)i;
```

en elke write legt dat vast:

```cpp
send_queue[send_queue_len].dest_slot = reply_slot;
```

`dest_slot >= 0` → uitsluitend die client. `dest_slot == -1` → elke verbonden
client.

Dit werkt omdat de companion-firmware single-threaded en synchroon is: een
commando wordt volledig afgehandeld, writes inbegrepen, voor het volgende frame
gelezen wordt. Alles wat de mesh meteen na een commando schrijft, *is* het
antwoord op dat commando. Alles wat op om het even welk ander moment geschreven
wordt — adverts, inkomende berichten, ACK's — is ongevraagd en gaat naar
iedereen.

De diepte van de verzendwachtrij is `FRAME_QUEUE_SIZE` = 4. Een volle wachtrij
laat de write vallen en geeft 0 terug.

Kostprijs: ruwweg 2–3 kB RAM per slot.

### Als flashen niet mogelijk is

`proxy/mc-proxy` doet de fan-out buiten de node, tegen ongewijzigde firmware. Het
kan geen antwoordroutering doen — het heeft geen zicht op de interne volgorde van
de node — dus het broadcast alles en compenseert door het `SELF_INFO`-frame te
cachen en de `CMD_APP_START` van elke client lokaal te beantwoorden. Zie
[`protocol.md`](protocol.md).

---

## 2. De fix voor de kanaalteller

`src/helpers/BaseChatMesh.cpp`.

`setChannel(idx, ...)` schrijft een kanaal op een willekeurige index en raakt
`num_channels` **niet** aan. `addChannel()` vertrouwde op `num_channels` als "het
volgende vrije slot". Apps gebruiken `setChannel()`.

Het gevolg: `num_channels` kon `MAX_GROUP_CHANNELS` bereiken terwijl er lege
slots onder lagen, en een kanaal toevoegen faalde met "channel limit reached" op
een node die grotendeels leeg was.

De fix hergebruikt lege slots binnen het gebruikte bereik voor dat bereik wordt
uitgebreid:

```cpp
int slot = -1;
for (int i = 0; i < num_channels && i < MAX_GROUP_CHANNELS; i++) {
    if (channels[i].name[0] == 0) { slot = i; break; }
}
if (slot < 0 && num_channels < MAX_GROUP_CHANNELS) slot = num_channels;
...
if (slot == num_channels) num_channels++;
```

Een leeg slot is er een met een lege `name`. `num_channels` groeit nog steeds
alleen maar, dus dit is een compatibele wijziging: ze maakt `addChannel()`
tolerant voor wat `setChannel()` doet, zonder het contract van een van beide
functies te wijzigen.

---

## 3. De stats-publisher (companion)

`examples/companion_radio/StatsPublisher.{h,cpp}`, plus drie toevoegingen aan
`MyMesh`:

| Methode | Wat ze doet |
|---|---|
| `fillStatsJson(out, max)` | Bouwt de JSON-body voor ingest (`MyMesh.cpp:1045`) |
| `fillNodeIdHex(out, max)` | Eerste 6 bytes van de publieke sleutel als 12 hextekens |
| `logRxRaw(snr, rssi, raw, len)` | Overschrijft de `Dispatcher`-hook; stuurt door naar de publisher (`MyMesh.cpp:295`) |

De inkoppeling in `main.cpp` staat achter `#if defined(ESP32) && defined(WIFI_SSID)`:

```cpp
#include "StatsPublisher.h"
StatsPublisher stats_publisher;
...
stats_publisher.begin(SPIFFS, &the_mesh);   // last statement in setup()
...
stats_publisher.loop();                     // in loop()
```

`begin()` is bewust het **laatste** wat `setup()` doet (`main.cpp:277-284`): het
leest twee bestanden en start een webserver, en mocht dat ooit blijven hangen of
falen, dan draaien de mesh en de companion-interface op `TCP_PORT` — waar Home
Assistant zit — al. Het is het ene onderdeel van `setup()` dat de node niet mee
in zijn val mag sleuren.

`MyMesh` includeert nooit `StatsPublisher.h`. Het roept vrije functies aan die
bovenaan `MyMesh.cpp` gedeclareerd zijn:

```cpp
void meshmanager_on_raw_packet(float snr, float rssi, const uint8_t raw[], int len);
```

die de publisher definieert (`StatsPublisher.cpp:14-24`) en in `begin()` naar
zichzelf laat wijzen. Een null-instantie maakt elke hook een no-op, dus een
aanroep vóór `begin()` is onschadelijk. Dat houdt de include-graaf acyclisch en
maakt de module werkelijk optioneel.

### De regel waarrond de hele module is gebouwd

Uit de headercommentaar (`StatsPublisher.h:26-50`), en het is de meest dragende
alinea in de companion-firmware:

> **Elk antwoord moet in één write passen.** Dit heeft ons tweemaal een node
> gekost, beide keren om dezelfde reden.

Eerst stelde de pagina zichzelf stuk voor stuk samen met `sendContent()`, met de
waarden al ingebakken. Elk stuk is een aparte blokkerende TCP-write, en met de
latentiepieken van ESP32-WiFi bleef de hoofdlus daarbinnen hangen — wat de mesh
mee stillegde, tot aan een harde reset.

Daarna kwam de tweede helft boven: `WiFiClient::write()` verzendt met
`MSG_DONTWAIT`, probeert het tien keer opnieuw met telkens een `select()` van één
seconde, en geeft dan een *gedeeltelijk* bytegetal terug. `WebServer` kijkt niet
naar die waarde. Een antwoord dat groter is dan de socket-verzendbuffer van lwip
(**5760 bytes**) belooft dus een `Content-Length` die nooit geleverd wordt, de
client wacht tot zijn eigen time-out, en de hoofdlus zit tot tien seconden vast.

Daaruit volgen vijf regels, en al de rest in deze sectie is een toepassing van
een ervan:

1. de pagina is een onveranderlijke blob, gzipped, in één keer verzonden;
2. alle data komt uit kleine JSON-endpoints, nooit ingebakken in de HTML;
3. lijsten worden gepagineerd (`STATS_CONTACT_PAGE`, `STATS_CHANNEL_PAGE`);
4. elke handler schrijft in een vaste buffer, nooit in een `String`;
5. `CountingWebServer` verifieert dat wat beloofd werd ook effectief buiten is
   geraakt.

Die laatste is een subklasse van `WebServer` (`StatsPublisher.h:214-245`) die de
protected virtuals `_currentClientWrite` / `_currentClientWrite_P` overschrijft
om gevraagde bytes tegen verwerkte bytes af te zetten. Bij een korte write
verbreekt `finishResponse()` (`StatsPublisher.cpp:416-424`) de verbinding, zodat
de browser meteen een fout krijgt in plaats van een time-out van een minuut. De
eerlijke kanttekening staat bij de bron: *geen korte write betekent dat de bytes
in de stack terechtkwamen, niet dat ze aangekomen zijn*.

Eén gedeelde I/O-buffer van 896 bytes bedient elke JSON-handler en beide
MQTT-payloads (`StatsPublisher.h:162-168`). Ze kunnen niet overlappen — de
webserver behandelt één verzoek tegelijk en beide MQTT-schrijvers draaien vanuit
`loop()`, buiten `handleClient()`. Een buffer per gebruiker zou 4230 bytes gekost
hebben. De omvang volgt uit de grootste gebruiker, een hex-gecodeerd MTU-pakket
(`2*255` + header), en elke lijst wordt gepagineerd om erin te passen.

### HTTP-routes

Poort 80, vast (`StatsPublisher.h:260`). Geregistreerd in `begin()`
(`StatsPublisher.cpp:866-878`). Alles is JSON tenzij anders vermeld:

| Route | Methode | Doel |
|---|---|---|
| `/` | GET | De pagina zelf: HTML, gzipped, één `send_P` |
| `/config.json` | GET | Identiteit van de node, MQTT-instellingen, statustabel |
| `/stats.json` | GET | Dezelfde JSON die naar MQTT gaat |
| `/save` | POST | Brokerinstellingen bewaren; verbindt opnieuw |
| `/test` | POST | Nu meteen een statsbericht publiceren |
| `/messages.json` | GET | Recente berichten; `?since=<seq>` pollt incrementeel |
| `/send` | POST | Een bericht verzenden; `to=c<idx>` (kanaal) of `k<hex>` (contact) |
| `/channels.json` | GET | Groepskanalen, gepagineerd met `?off=<n>` |
| `/channel/add` | POST | Een kanaal joinen of aanmaken (`name`, `psk`; lege psk = nieuw) |
| `/channel/del` | POST | Een kanaal vergeten (`idx`) |
| `/contacts.json` | GET | Eén pagina contacten, `?off=<n>` |
| `/contact/save` | POST | Per repeater: publicatievlag en wachtwoord (`key`, `publish`, `pass`) |
| `/contact/login` | POST | Aanmelden bij een repeater met het bewaarde wachtwoord (`key`) |

Er is **geen `/update`-route, geen `onNotFound`, geen authenticatie en geen
CORS-header** in deze server te vinden. Antwoordvormen:

| Endpoint | Vorm |
|---|---|
| `/config.json` | `{"name","ip","node","cfg":{host,port,user,prefix,interval,enabled,forward_rx},"status":{…}}` |
| `/stats.json` | `{"repeater":{pubkey_prefix,name},"metrics":{…}}`; HTTP 503 en `{}` wanneer de mesh ontbreekt |
| `/messages.json` | `{"m":[{"q":seq,"k":kind,"s":src,"t":ts,"x":text}],"more":1}` |
| `/channels.json` | `{"ch":[{"i":slot,"n":name}],"next":slot\|-1}` |
| `/contacts.json` | `{"c":[{"k":hex12,"n":name,"t":type,"a":secs_ago,"p":publish,"w":has_password}],"next":slot\|-1}` |
| de POST-routes | `{"ok":1}` of `{"ok":0,"err":"…"}` |

`/config.json` heeft één bewuste eigenaardigheid: het is het enige antwoord
waarvan de lengte afhangt van tekst die iemand heeft ingetypt in plaats van van
zijn eigen paginering, dus wanneer die strings de gedeelde buffer doen overlopen,
verzendt het een geldige maar **lege** `{"cfg":{},"status":{}}` in plaats van
afgeknotte JSON (`StatsPublisher.cpp:476-478`).

De configuratie staat in `/stats_cfg.json` op SPIFFS en is gedocumenteerd in
[`mqtt.md`](mqtt.md).

> De pagina heeft **geen authenticatie**. Ze legt het wachtwoordveld van de
> broker bloot (write-only — de pagina toont de bewaarde waarde nooit) en laat
> iedereen op het LAN toe te wijzigen waar de statistieken naartoe gaan, de
> recente berichten te lezen, **berichten te verzenden onder de identiteit van de
> node**, en repeaterwachtwoorden op te slaan. Beschouw ze als uitsluitend voor
> een vertrouwd netwerk. De *repeater*-module heeft wel een login; zie §4.

### `StatsPage.h` wordt gegenereerd — nooit met de hand bewerken

`page.html` is de bron. `gen_page.py` gzipt het en genereert `StatsPage.h` als
een `PROGMEM`-bytearray. De waarschuwing staat op drie plaatsen, en ze meent wat
ze zegt:

| Waar | Bewoording |
|---|---|
| `StatsPage.h:3` | GEGENEREERD BESTAND — niet met de hand bewerken |
| `gen_page.py:16-17` | Wijzig altijd `page.html` en draai daarna dit script |
| `StatsPublisher.cpp:397-398` | Wie de pagina wil wijzigen, bewerkt `page.html` en draait het script; nooit `StatsPage.h` met de hand |

```bash
python examples/companion_radio/gen_page.py     # page.html -> StatsPage.h
```

Twee zaken over de generator die makkelijk fout lopen:

- **Hij minifyt niet.** Hij leest de ruwe bytes en gzipt ze ongewijzigd
  (`gen_page.py:28-35`). `page.html` is in de bron met de hand geminifyd — daarom
  is het geschreven als dichte eenregelige JavaScript. Wordt daar ruime HTML
  geschreven, dan betaalt het budget hieronder voor elke spatie.
- **De uitvoer is met opzet reproduceerbaar.** `compresslevel=9, mtime=0`, dus
  dezelfde `page.html` levert altijd dezelfde blob op en een regeneratie zonder
  inhoudelijke wijziging geeft geen diff.

Gegenereerde symbolen: `static const uint8_t PAGE_GZ[] PROGMEM` en
`static const size_t PAGE_GZ_LEN = sizeof(PAGE_GZ);`, 16 bytes per regel.

#### Het gzip-budget

| | |
|---|---|
| Budget (`SND_BUF`, `gen_page.py:26`) | **5760 bytes** — `CONFIG_LWIP_TCP_SND_BUF_DEFAULT` in deze build |
| `page.html`, ongecomprimeerd | 15278 bytes |
| **Huidige gzipte omvang** | **5702 bytes** (37 % van het origineel), vastgelegd in `StatsPage.h:14-15` |
| Speling | **58 bytes**, dus 99,0 % van het budget is opgebruikt |
| Bij overschrijding | drukt een waarschuwing af en eindigt met **1** |

Het budget is geen stijlvoorkeur: het is de omvang van de socket-verzendbuffer,
en een pagina die hem overschrijdt, herintroduceert precies de hierboven
beschreven blokkering.

Eén scherpe rand die de moeite is om te kennen voor de pagina bewerkt wordt: de
groottecontrole gebeurt **nadat** `StatsPage.h` al geschreven is
(`gen_page.py:68` schrijft, `:73` controleert), en de vergelijking is `>=`. Een
mislukte run laat dus een te grote `StatsPage.h` in de werkboom achter met
exitstatus 1. Commit dat bestand niet zonder de uitvoer van het script gelezen te
hebben. Met 58 bytes speling moet een functie die aan `page.html` wordt
toegevoegd zichzelf bijna altijd terugverdienen door iets anders te verwijderen.

De pagina wordt geserveerd met de gecomprimeerde lengte als `Content-Length`,
want dat is wat er effectief over de lijn gaat (`StatsPublisher.cpp:435-446`):

```cpp
_server.sendHeader("Content-Encoding", "gzip");
_server.send_P(200, "text/html; charset=utf-8", (PGM_P)PAGE_GZ, PAGE_GZ_LEN);
```

JSON-antwoorden gaan om dezelfde reden via `send_P` naar buiten: de gewone
`send()` kopieert de volledige body naar een `String` op de heap bovenop de
buffer die al aangehouden wordt, en het framework waarschuwt zelf *"Use send_P
for long arrays"*.

### De webclient

Een chatclient in de klassieke driepaneelindeling (`page.html:151-218`):
gesprekken links (kanalen en contacten samen, met ongelezen-tellers en een
filter), het gesprek in het midden, de details ervan rechts — wie in een kanaal
gesproken heeft, of de sleutel, het type en het laatst gehoord van een contact,
met het selectievakje voor doorsturen-naar-site, het wachtwoordveld en de
loginknop voor repeaters. Op een telefoon worden beide zijpanelen lades. De
MQTT-instellingen, de statustabel en een live statistiektabel zitten achter de
instellingenknop op dezelfde pagina.

Pollcadans (`page.html:306`): berichten om de 3 s zolang de chatweergave open
staat, `/config.json` om de 10 s zolang de instellingenweergave open staat, en
een cacheflush om de 30 s.

### De berichtenring

| Constante | Waarde | |
|---|---|---|
| `STATS_MSG_RING` | **32** | slots, 76 bytes elk = 2432 bytes statisch RAM |
| `STATS_MSG_SRC_MAX` | 16 | dus **15** tekens afzendernaam |
| `STATS_MSG_TEXT_MAX` | 48 | tekens berichttekst |
| `STATS_MSG_CHANNEL` / `_DIRECT` / `_SENT` | 0 / 1 / 2 | soort; `_SENT` is het eigen bericht dat teruggekaatst wordt zodat de pagina beide kanten kan tonen |

De ring is een platte lijst van `MsgItem` (`StatsPublisher.h:300-306`), elk met
`seq`, `timestamp` (de klok van de *afzender*, niet die van ons), `kind`, `src`
en `text`. Uitwerpen gebeurt door het oudste slot onvoorwaardelijk te
overschrijven — er is geen vol- of leegtest, en `_msg_head` is gedocumenteerd als
*"next slot to overwrite; the ring never runs empty"*. Lezers lopen vooruit vanaf
`_msg_head` en slaan slots met `seq == 0` (nooit geschreven) of `seq <= since`
over. `copyTrim()` knot af zonder een UTF-8-sequentie te splitsen.

Oorspronkelijk waren het **8 slots**, en de reden dat het er 32 zijn is het
onthouden waard: de ring doet ook dienst als achterstand voor een browser die
later opent, aangezien de pagina alles laadt met `since=0`. Op een druk kanaal
betekenden acht slots dat een avond aan berichten al overschreven was voor iemand
keek. Tweeëndertig kost 2432 bytes in plaats van 608, wat een build die op 55 %
RAM zit nog kan dragen. `/messages.json` groeit niet mee: het antwoord pagineert
zichzelf met `"more"` zodra de gedeelde buffer vol raakt, en de pagina haalt de
rest meteen op.

**Bekende beperking, vermeld bij de bron:** de ring leeft alleen in RAM, dus een
herstart van de node maakt hem leeg. Hem naar SPIFFS wegschrijven zou dat
oplossen en is **bewust niet gebouwd** — elk binnenkomend bericht zou een
flashwrite worden, en op een node die dag en nacht meshverkeer ziet, slijt dat de
flash sneller uit dan de achterstand waard is.

### De browsercache van de historiek

De pagina bewaart zelf de laatste **300** berichten, in `localStorage` onder de
sleutel `mh` (`page.html:230-231`, `:276`, `:303-304`). Dat is wat een gesprek
zowel de kleine ring als een herstart van de node doet overleven.

| Aspect | Gedrag |
|---|---|
| Identiteit | `mid(m)` = timestamp + soort + bron + spreker + tekst, samengevoegd met newlines |
| Samenvoegen | een bericht waarvan de id al bekend is, wordt weggelaten; alles wat nieuw is, wordt lokaal hernummerd |
| Plafond | 300 berichten, oudste eerst weggegooid |
| Schrijfmomenten | bij het verbergen van de pagina, bij een zichtbaarheidswissel, en om de 30 s — nooit per bericht |

Elk daarvan is een beslissing met een reden (`StatsPublisher.h:102-135`):

- **Samenvoegen op `q` is onmogelijk.** De volgordeteller begint opnieuw na een
  herstart van de node, dus hetzelfde bericht kan onder twee nummers opduiken.
  Vandaar de inhoudshash. Echte herhaalde berichten overleven die wel, omdat
  afzenders stempelen met `getCurrentTimeUnique()`. Het aanvaarde verlies wordt
  expliciet benoemd: twee sprekers met dezelfde naam die in dezelfde seconde in
  hetzelfde kanaal hetzelfde zeggen, versmelten tot één bericht, en met de velden
  die de ring biedt is dat niet te onderscheiden.
- **Berichten uit de cache worden bij het laden hernummerd** en tellen nooit als
  ongelezen, anders zouden hoge nummers van voor de herstart voorrang krijgen op
  verse lage in de ongelezen-telling.
- **300 is een renderbudget, geen opslagbudget.** Het gaat om zo'n 30 kB JSON
  waar `localStorage` 5 MB per origin toestaat — maar de pagina hertekent de hele
  lijst bij elke update, dus veel meer bijhouden zou vooral het tekenen op een
  telefoon traag maken.
- **Schrijfacties worden gebundeld** omdat `localStorage` alles-of-niets is per
  sleutel: één bericht toevoegen kan niet. Een browsercrash kost daardoor
  hoogstens een halve minuut cache.

Gevolgen, zonder omwegen gesteld: sitegegevens wissen wist de historiek, een
tweede browser begint leeg en vult zich vanaf dat moment, en een bericht dat uit
de ring viel terwijl geen enkele browser aan het pollen was, is werkelijk weg. Op
de naad tussen cache en ring kan de volgorde licht afwijken — de lijst staat in
volgorde van aankomst, en sorteren op afzenderklokken zou het erger maken.

### Twee scherpe randen in de gespreksmapping

Beide gedocumenteerd bij de bron (`StatsPublisher.h:88-96`):

- **Namen in de ring worden afgeknot op 15 tekens** terwijl `/contacts.json` de
  volledige naam teruggeeft, dus de pagina vergelijkt alleen de eerste 15
  (`page.html:227-228`, `function eq(a,b){return a.slice(0,15)==b.slice(0,15)}`).
  Wordt `STATS_MSG_SRC_MAX` verbreed, dan moet die vergelijking mee.
- **Een verzonden bericht (`STATS_MSG_SENT`) zegt niet of het naar een kanaal dan
  wel naar een contact ging.** De pagina zoekt de naam eerst bij de kanalen, dus
  een kanaal en een contact met dezelfde naam doen de eigen berichten onder het
  kanaal belanden. Een onderscheidend veld in het antwoord zou dat oplossen en
  werd de bytes niet waard geacht.

Wie in een kanaal gesproken heeft, staat evenmin in `s`: `sendGroupMessage()`
plaatst `<sender>: ` voor de tekst, en de pagina strijkt dat voorvoegsel er weer
af om de naam in een eigen kolom te tonen. Al dit afknotten is uitsluitend voor
de weergave — de companion-app over BLE of TCP ontvangt nog steeds elk bericht
volledig.

### Geen OTA op de companion

De companion heeft **geen enkele vorm van firmware-upload**. Een zoekactie over
de hele repository naar `ArduinoOTA`, `ElegantOTA`, `AsyncElegantOTA`,
`Update.begin`, `httpUpdate` en `/update` raakt `examples/simple_repeater/` en
niets anders. De routetabel van de companion heeft geen `/update`, zijn includes
zijn enkel `Arduino.h`, `FS.h`, `WebServer.h`, `WiFiClient.h` en
`PubSubClient.h`, en zijn omgeving trekt `${esp32_ota.lib_deps}` niet binnen.

Het is niet louter niet-aangesloten, het is in zijn huidige vorm structureel
onverenigbaar: de companion gebruikt de **synchrone** `WebServer`, terwijl
`AsyncElegantOTA` `ESPAsyncWebServer` vereist.

Dus: **de companion wordt over USB/serieel geflasht, de repeater kan over het
netwerk geflasht worden.** De asymmetrie is bedoeld. Een repeater staat op een
dak; een companion staat op een bureau naast de persoon die hem wil herflashen,
en de flash en het RAM die een uploader kost, zijn beter besteed aan contacten en
kanalen. Houd daar rekening mee bij het uitrollen — een companion-node op een
lastige plek is een node die met een kabel opgehaald zal moeten worden.

### Timingregels in `loop()`

Drie, elk met een gemeten reden:

- **Er wordt precies één raw packet per doorloop van `loop()` gepubliceerd**
  (`StatsPublisher.cpp:353-357`). Er vier na elkaar leegtrekken hield de meshlus
  bijna een seconde op.
- **Alles onder `_server.handleClient()` wordt overgeslagen zolang
  `_mesh->hasPendingWork()`** (`StatsPublisher.cpp:890-896`). De `connect()` en
  `publish()` van `PubSubClient` zijn synchroon en kunnen blokkeren; de mesh heeft
  harde timing en doorsturen niet.
- **15 seconden backoff na een mislukte brokerverbinding**
  (`StatsPublisher.cpp:276-278`). Een broker die niet antwoordt blijven bestoken,
  kostte de hele node eerder zijn responsiviteit.

De meshcallbacks zelf kopiëren alleen maar naar een ringbuffer, ze verzenden
nooit: netwerk-I/O vanuit een radiocallback zou de ontvangst ophouden. De
omgekeerde richting is wel toegestaan — een HTTP-handler mag de mesh aanroepen,
omdat verzenden slechts een pakket in de wachtrij zet.

### Wat u moet weten voor u dit bouwt

- **`STATS_TRACE` staat nog op `1`** (`StatsPublisher.cpp:33-44`). Het blok is
  gemarkeerd als *"TEMPORARY — diagnostics"* met de instructie het op 0 te zetten
  zodra de oorzaak vastgepind is, en dat is niet gebeurd. Elk verzoek wordt naar
  `Serial` afgedrukt.
- **`base64.hpp` wordt manueel geïncludeerd.** Het is header-only maar niet
  `inline`, dus het hier includeren naast in `BaseChatMesh.cpp` levert dubbele
  symbolen op bij het linken; enkel `encode_base64` wordt met de hand
  gedeclareerd (`StatsPublisher.cpp:6-10`).
- **Een repeaterwachtwoord wordt afgeknot op 15 tekens** door `sendLogin()`, en
  dat is de echte limiet achter `STATS_REPEATER_PASS 16`. Er worden tot
  `STATS_REPEATER_MAX` = 8 repeaterrecords op SPIFFS bewaard; de contacten zelf
  staan in de meshopslag.
- **`MAX_CONTACTS` spreekt zichzelf tegen.** De commentaar bij
  `StatsPublisher.h:205-210` zegt 350; het meegeleverde voorbeeld zet **260**, met
  zijn eigen uitleg: bij 350 bleef er nog maar 22 kB heap over, te weinig voor
  lwip om zijn buffers te schikken, waardoor de webserver halve antwoorden
  schreef en clients bleven hangen. 260 laat ongeveer 50 kB over. Geloof het
  voorbeeld.
- **`DualSerialInterface.h` is dode code.** Het draait BLE en WiFi naast elkaar
  en routeert antwoorden naar wie het laatst een frame afleverde, maar niets
  includeert of instantieert het — `main.cpp` gebruikt `MultiSerialInterface` en
  registreert elk transport apart. Het is achterhaald, niet in gebruik.
- **`WiFi.setSleep(false)` wordt alleen toegepast wanneer BLE niet meegecompileerd
  is** (`main.cpp:201-220`). De ESP32-IDF vereist modem sleep wanneer WiFi en
  Bluetooth naast elkaar bestaan; het geforceerd uitschakelen leverde een
  `abort()` op core 0 en een herstartlus op. Het wordt opnieuw toegepast bij elke
  `ARDUINO_EVENT_WIFI_STA_GOT_IP`, omdat een herverbinding de energiemodus reset.

---

## 4. `MeshManagerNet` — de repeatermodule

`examples/simple_repeater/MeshManagerNet.{h,cpp}`, ingeschakeld met
`-D MESHMANAGER_NET=1`.

Het uitgangspunt, gesteld in de headercommentaar (`MeshManagerNet.h:94-124`): deze
repeater staat op een dak en draait op een zonnepaneel. **Hij mag nooit
onbereikbaar worden, en hij mag nooit meer energie verbruiken dan het paneel
binnenbrengt.** Elke ontwerpkeuze hieronder volgt uit die twee zinnen, en waar
een keuze overbodig lijkt, is dat meestal omdat ze een gat dicht dat de andere
niet kunnen bereiken.

Drie ingangspunten, aangeroepen vanuit `simple_repeater` via
`repeater-hooks.patch`:

```c
void msnet_begin(FS &fs, MyMesh *mesh);                       // in setup()
void msnet_loop();                                            // in loop()
bool msnet_handle_command(const char *command, char *reply);  // from any CLI
```

Vier bijkomende hooks voeden hem vanaf de meshzijde (`MeshManagerNet.h:148-180`):
`meshmanager_on_raw_packet()`, `meshmanager_on_monitor_response()`,
`meshmanager_on_advert()` en `meshmanager_advert_name()`, plus
`meshmanager_batt_percent()`, dat bestaat opdat de beheerpagina, het energiebeheer
en de gepubliceerde statistieken alle drie *hetzelfde* batterijcijfer citeren —
twee curves die enkele procenten van elkaar verschillen, zijn een bugmelding in
wording.

### 4.1 Versiegeschiedenis

De gezaghebbende changelog is het blokcommentaar bovenaan `MeshManagerNet.cpp`
(regels 1–615). De huidige versie staat in `MeshManagerNet.h:158`. Deze tabel is
een leeshulp, geen vervanging: het commentaar legt de *redenering* vast, en dat
is het deel dat telt wanneer beslist moet worden of er iets gewijzigd wordt.

| Versie | Wat ze bracht | Waarom |
|---|---|---|
| **1.0.0** | MQTT-publicatie (eigen statistieken + elk raw packet); publicatie-interval dat rekening houdt met batterij en klok, met hysterese; energiebesparende WiFi met een noodluik om ze geforceerd aan te zetten; beheerpagina in de stijl van de publieke site, licht/donker, NL/EN; eigen versie gemeld door `ver`, op de pagina en in de payload | Een repeater op een paneel moet kunnen rapporteren zonder zichzelf leeg te trekken |
| **1.1.0** | Task watchdog: een vastgelopen `loop()` wordt een herstart | De drie vangnetten op de boottellers reageren allemaal op *herstarts*, en een vastloper levert er geen — zie §4.14 |
| **1.2.0** | Andere repeaters monitoren: kiezen uit de gehoorde lijst of een publieke sleutel plakken, aanmelden met een wachtwoord of via hun toegangslijst, `GET_STATUS` pollen over de mesh | Een repeater die enkel LoRa spreekt, heeft geen andere manier om de site te bereiken |
| **1.3.0** | Gemonitorde metingen verhuisd naar het gewone `stats`-topic, met het onderwerp in de payload; buurlijst verhuisd naar binnen de stats-payload als `neighbors`; aparte `polls` / `oks` / `pubs`-tellers; chiptemperatuur hernoemd naar `mcu_temperature` | Ze waren gepubliceerd naar `<prefix>/<node>/mon`, waar niets op geabonneerd is: `publish()` slaagde en de broker gooide de data ongelezen weg |
| **1.3.1** | Trace van de pollvolgorde (pagina, `wifi mon trace`, serieel); één floodherhaling per stap | Een poll die na een geslaagde login vastliep, zag er precies uit als een poll waarvan het verzoek nooit vertrok — beide laten `polls=1, oks=0, lr=1` achter |
| **1.4.0** | Een gemonitorde repeater wordt met drie verzoeken uitgelezen in plaats van één: status, telemetrie (CayenneLPP → `ch<N>_temperature` / `ch<N>_voltage`) en buren, gepubliceerd als één bericht; tellers per type | Elk van de drie mag falen zonder de andere te verliezen |
| **1.5.0** | Adverts gecacht op het bestandssysteem (sleutel, naam, type, laatst gehoord, coördinaten); een metriek die niet beschikbaar is, wordt weggelaten in plaats van als `0` gepubliceerd | Namen overleefden een herstart als kale hex tot aan de volgende advert, uren later; en `noise_floor 0` tekende een lijn die naar nul dook waar een gat hoorde |
| **1.6.0** | De koppeling batterij-naar-interval werd een door de gebruiker bewerkbare regeltabel met hysterese en een ondergrens per modus; de pagina toont wat een instelling *kost* (berichten/dag, LoRa-pakketten/uur); de node leest zijn eigen CLI-parameters uit en verstuurt ze als een `settings`-object | Vijf vaste niveaus passen niet bij elk paneel, elke cel en elk seizoen |
| **1.7.0** | De instellingensweep draait eenmaal per dag in plaats van om de zes uur, en werd waarneembaar: hoeveel er antwoordden, wanneer de laatste liep, wanneer de volgende verwacht wordt, de waarden zelf, en een manier om er een af te dwingen | Daarvoor verschenen de waarden alleen in het ene bericht na een sweep — één op 1440 — waardoor een mislukte sweep en een sweep die nooit liep er identiek uitzagen |
| **1.7.1** | De automatische monitorronde startte helemaal nooit — opgelost; `region` gesplitst in `region.home` en `region.default` | `passed()` leest `0` als "niet ingepland" en `_mon_next_round` begon op `0`. Aanwezig sinds 1.2.0, verborgen omdat elke test met een manuele poll begon |
| **1.7.2** | De sweep vraagt ook `flood.max.unscoped` op | De parameterlijst op de site stuurt enkel het Home Assistant-pad aan; deze sweep heeft zijn eigen tabel, dus een parameter die daar toegevoegd werd, bereikte MQTT-nodes nooit |
| **1.8.0** | De node abonneert zich op `<prefix>/<node>/cmd` en aanvaardt precies twee woorden: `settings` en `status`. Al de rest wordt geweigerd en geteld | De knop "instellingen ophalen" van de site schreef in een wachtrij die alleen Home Assistant ooit leegmaakte — haal Home Assistant uit de keten en de knop deed helemaal niets |
| **1.9.0** | `settings <key>`: een monitorende node leest de CLI-instellingen van een repeater die hij *monitort*, over LoRa, en publiceert ze onder de naam van die repeater | 1.8.0 bereikte alleen nodes die zelf naar MQTT publiceren — en dat is niet de repeater waarrond dit project gebouwd is |
| **1.9.1** | Nodenamen en de zes stukken ingetypte tekst in `/api/status` worden JSON-ge-escaped; `jsonEsc()` kreeg UTF-8-veilig afknotten | Een naam met een aanhalingsteken komt niet raar ogend aan, hij komt helemaal niet aan: het bericht is geen JSON meer, de ingest gooit het volledig weg, en `publish()` meldt nog steeds succes |
| **1.10.0** | De site kan de klok van deze node zetten (`time <epoch>` op `cmd`), waarna de node de klokken controleert van de repeaters die hij monitort, over LoRa; `wifi clock` leest terug wat er gebeurd is | Een ESP32 zonder batterijgevoede RTC komt terug uit een herstart en stempelt alles met mei 2024, en niets op de mesh weet beter. De site wel |
| **1.11.0** | De sweep verzamelt de regioboom opnieuw, als `cmd:region`; `SET_VALUE_MAX` 32 → 176; `jsonEsc()` schrijft `\n`, `\r`, `\t`; `Err - …` wordt naast `Error…` als weigering herkend; `MON_SET_TOTAL_MS` 300 s → 360 s | 1.7.1 stopte terecht met het publiceren van een boom in een instellingenkolom, maar liet ten onrechte de boom volledig vallen — waardoor één rij op "7 dagen" stond te verouderen naast achttien op "32 minuten" |

Twee patronen lopen door die lijst heen en verdienen het benoemd te worden, want
ze zijn de reden dat verschillende van de regels hieronder bestaan:

- **Een publicatie die slaagt en daarna weggegooid wordt, is de ergste
  faalmodus in dit systeem.** Het gebeurde met een topic waarop niets
  geabonneerd was (1.3.0) en met een payload die geen geldige JSON was (1.9.1).
  Beide keren zei elke teller op de node dat alles in orde was. Vandaar: twee
  uitgaande topics en niet meer, en één escaping-helper die overal gebruikt wordt.
- **Een stilte moet gepubliceerd worden, niet verborgen.** Een parameter die
  gevraagd werd en niet antwoordde, gaat als `null` naar buiten (1.9.0), een
  planner die niet ingepland is, zegt dat (1.7.1), en de pollvolgorde houdt een
  trace bij (1.3.1). Een node op een dak diagnosticeren mag geen seriële kabel
  vereisen.

### 4.2 WiFi met AP-fallback

Toestandsmachine in `msnet_loop()`:

| Toestand | Gedrag |
|---|---|
| `WIFI_TRYING` | Verbinden. Na `STA_TIMEOUT_MS` (30 s) → AP starten. |
| `WIFI_OK` | Verbonden. Verlies van verbinding → terug naar `WIFI_TRYING`. |
| `WIFI_FALLBACK_AP` | Zendt `MeshCore-<node_hex>` uit. Probeert het eigen netwerk opnieuw om de `STA_RETRY_MS` (5 min). Succes → AP afbreken, naar `WIFI_OK`. |

`startAP()` gebruikt `WIFI_AP_STA`, zodat de AP in de lucht blijft *terwijl*
stationmodus blijft proberen. Er is nooit uitsluiting tijdens een
herprobeervenster, en het herstel is automatisch — geen bezoek aan het dak nodig.

De AP-SSID is `MeshCore-<eerste 6 bytes van de pubkey in hex>`. Het standaard
AP-wachtwoord is `meshcore`.

Eén uitzondering, in `WIFI_TRYING`: in energiebesparende modus is een AP
optrekken waar niemand op wacht het duurste wat deze node zou kunnen doen, dus
gaat hij terug slapen en probeert hij het de volgende ronde opnieuw. Tenzij het
venster geforceerd werd met `wifi on` — dan staat er iemand naast op zoek naar
een netwerk.

### 4.3 Energiebeheer

Alles hier is instelbaar in plaats van ingecompileerd, omdat de juiste getallen
afhangen van het paneel, de cel en het seizoen, en ze zonder herflashen over de
mesh wijzigbaar moeten zijn.

| Modus | Betekenis | Ondergrens interval |
|---|---|---|
| `PWR_ALWAYS` (0) | Altijd bereikbaar; WiFi blijft geassocieerd | `PWR_MIN_ALWAYS` = 10 s |
| `PWR_SAVE` (1) | WiFi meestal uit; wordt wakker, publiceert, slaapt | `PWR_MIN_SAVE` = 60 s |

In energiebesparende modus bepaalt het publicatie-interval ook hoe vaak de radio
ontwaakt, en daarom ligt de ondergrens ervan zes keer hoger. Radiostilte bespaart
veel meer dan een trager interval ooit zal doen — dat is de hele reden waarom de
modus bestaat.

De koppeling batterij-naar-interval is een regeltabel (tot `PWR_RULES_MAX` = 8
regels, bewaard in `/mspwr.json`), met hysterese (`bat_hyst`) zodat een cel die
op een grens blijft hangen niet tussen twee intervallen oscilleert. Een
nachtvenster (`night_from` / `night_to`, uren UTC) vermenigvuldigt het interval
met `night_factor`, en geldt alleen wanneer de klok werkelijk plausibel is —
niets in deze firmware mag stoppen met werken omdat de klok fout staat.

Twee drempels gaan over gedrag in plaats van over timing:

- `bat_live` — boven dit percentage worden ontvangen raw packets onmiddellijk
  naar MQTT doorgestuurd; eronder wachten ze.
- `bat_mon` — onder dit percentage stopt het pollen van andere repeaters
  helemaal. Een lege batterij aan iemand anders zijn statistieken besteden is de
  verkeerde afweging.

Een bord dat geen bruikbare celspanning meldt, wordt als *onbekend* behandeld, en
onbekend wordt als netstroom behandeld: een node die zijn cel niet kan meten, mag
niet op basis van een gok afgeknepen worden.

Het noodluik is `wifi on [minutes]` (standaard `FORCE_DEFAULT_MIN` = 30). Het
forceert WiFi omhoog en houdt het daar, wat de modus ook is en wat de batterij
ook zegt. Dit is de weg terug in een node die slaapt, en het werkt vanaf de
mesh-CLI — dus het werkt wanneer de node per definitie onbereikbaar is over IP.

### 4.4 Beheerpagina en de `/api/*`-endpoints

Poort 80, `AsyncWebServer`. Asynchroon met opzet: een blokkerende server houdt de
hoofdlus op en daarmee de mesh, wat op de companion-node al was vastgesteld.

| Route | Methode | Auth | Doel |
|---|---|---|---|
| `/` | GET | geen | De pagina zelf, vanuit flash gestreamd met `send_P` |
| `/api/status` | GET | basic | Alles wat de pagina rendert: identiteit, bord, beide firmwareversies, WiFi-toestand, batterij, MQTT-tellers, energietoestand, instellingensweep, safe-modevlag |
| `/api/wifi` | POST | basic | SSID / wachtwoord / AP-wachtwoord |
| `/api/power` | POST | basic | Energiemodus, venster, slaap, TX-vermogen, batterijgrenzen, intervallen, nachtvenster |
| `/api/mqtt` | POST | basic | Brokerhost, poort, gebruiker, wachtwoord, prefix, inschakelen, doorsturen van raw packets |
| `/api/settings` | POST | basic | Interval van de instellingensweep, nu een sweep afdwingen |
| `/api/mon` | GET | basic | Monitorlijst met tellers per record, plannertoestand, seconden tot de volgende ronde, gehoorde lijst, trace |
| `/api/mon` | POST | basic | `add`, `del`, `pass`, `en`, `iv`, `poll` |
| `/api/backup` | GET | basic | Het volledige bestandssysteem downloaden |
| `/api/restore` | POST | basic | Een back-up uploaden, daarna herstarten |
| `/api/cfg` | GET | basic | Welke CLI-parameters van afstand gezet mogen worden, met hun type, grenzen, toegestane woorden en risicoklasse (2.1.0+). `?values=1` zet er achter elke parameter bij wat er nú in staat, en levert de vier deelgrenzen van `radio` in `choices` (2.5.0+) |
| `/api/cfg` | POST | basic | Er één zetten en meteen teruglezen — zie [`node-management.md`](node-management.md) (2.1.0+) |
| `/api/moncfg` | GET | basic | De lopende of laatst afgeronde schrijfactie naar een **gemonitorde** repeater over LoRa: wat er gevraagd is, wat er teruggelezen is, en hoe het afliep (2.4.0+) |
| `/api/moncfg` | POST | basic | Één parameter zetten op een repeater die deze node monitort, over LoRa, en hem daarna teruglezen. Antwoordt `202` — er is nog niets gebeurd; twee pakketten over een gedeelde band duren tientallen seconden (2.4.0+) |
| `/api/fw` | GET | basic | Geïnstalleerde versie, bouwomgeving, welke partitie draait en wat er in de andere staat (1.12.0+) |
| `/api/fw` | POST | basic | Firmware-image als kale body; de digest wordt gecontroleerd vóór de bootpartitie omgezet wordt — zie [`firmware-upgrade.md`](firmware-upgrade.md) (1.12.0+) |
| `/api/fw/rollback` | POST | basic | De andere applicatiepartitie weer opstarten (1.12.0+) |
| `/update` | GET/POST | basic | Firmware-upload via `AsyncElegantOTA`. Blijft bestaan als terugval voor wanneer de weg hierboven juist het kapotte onderdeel is |

De authenticatie is **HTTP basic**, met dezelfde inloggegevens als de console
(`_cfg.user` / `_cfg.console_pass`, standaard `admin` / `meshcore`). `/` zelf is
niet geauthenticeerd: het is een statische schil die niets rendert tot
`/api/status` slaagt. `send_P` streamt de pagina rechtstreeks uit flash, omdat
`send()` eerst alle 45 kB naar een `String` op de heap zou kopiëren op een node
die ook nog een mesh draaiende moet houden.

Anders dan bij de companion is er hier **geen gzip-budget**. De pagina van de
companion wordt in één keer in de socketbuffer gelegd en zit daarom klem onder
`CONFIG_LWIP_TCP_SND_BUF_DEFAULT` (5760 byte, §"Het gzip-budget"); deze gaat
ongecomprimeerd naar buiten en `AsyncWebServer` voert hem in stukken af naarmate
het venster het toelaat. De grens is hier de applicatiepartitie, niet de
verzendbuffer.

`/api/status` antwoordt met **waarden en codes, nooit met afgewerkte zinnen** —
de pagina rendert ze in de taal van de lezer, wat ook de reden is waarom de
batterij als millivolt, percentage en niveau binnenkomt in plaats van als een
opgemaakte string. Zes velden erin zijn tekst die iemand gekozen heeft (nodenaam,
SSID, brokerhost, enzovoort) en gaan sinds 1.9.1 door `jsonEsc()`; een
MCU-temperatuur die `NaN` is, wordt als `-999` verzonden, want `"%.1f"` van een
NaN drukt `nan` af, wat geen JSON is en de hele pagina blanco zou maken.

Lege wachtwoordvelden op het WiFi-formulier betekenen "ongewijzigd laten".
Zonder dat zou de pagina openen en bewaren het WiFi-wachtwoord wissen. Het
AP-wachtwoord wordt alleen aanvaard vanaf 8 tekens, omdat WPA2 dat vereist.

**Er wordt vanuit een webhandler niets weggeschreven.** De server draait in een
eigen taak; de handlers zetten een vlag en keren terug:

```cpp
_apply_wifi = true;      // saving and reconnecting happens in loop()
```

`_apply_wifi`, `_apply_mqtt`, `_apply_power` en `_apply_rules` worden bovenaan
`msnet_loop()` opgepikt, en daar horen schrijfacties naar het bestandssysteem en
`WiFi.begin()` thuis. De monitor-endpoints volgen dezelfde discipline via
`_mon_action`, en een POST die binnenkomt terwijl een vorige actie nog in
behandeling is, wordt beantwoord met `{"ok":0,"err":"busy"}` in plaats van in een
wachtrij gezet.

#### Wat er op de pagina staat (2.5.0+)

Tien inklapbare secties: toestand, WiFi, energie, MQTT, monitoren, instellingen
van deze node (de uitslag van de sweep), **instellingen wijzigen**,
**pakketfilter**, firmware en back-up. Die twee vetgedrukte zijn wat 2.5.0
toevoegde — tot dan kon de pagina je alles tónen en vrijwel niets veranderen.

Het zijn `<details>`/`<summary>` en geen tabbladen. Open- en dichtklappen, het
toetsenbord, de schermlezer en het zoeken-op-deze-pagina van de browser kosten dan
niets om te bouwen; tabbladen zijn dezelfde functie in ruil voor een omschakelaar
in JS, een toestand in CSS en `aria-*`-attributen om bruikbaar te blijven. Alleen
het *onthouden* kost JavaScript — vier regels, onder dezelfde
localStorage-sleutel `mcs-collapse:<naam>` die de publieke site gebruikt. Het
verschil: de site bewaart alleen "dicht" en beschouwt afwezig als open, want daar
staat alles standaard open. Hier verschilt de standaard per sectie — toestand
open, de rest dicht — dus wordt de stand voluit bewaard en betekent "niets
bewaard" juist "gebruik wat de firmware koos".

**Instellingen wijzigen** wordt volledig getekend uit `GET /api/cfg?values=1`. Er
staat geen tweede parameterlijst in de pagina: een parameter die deze firmware
niet kent kan niet aangeboden worden, en een grens die hier afweek zou de losse
van de twee zijn — en dat is precies waar iemand op een knop drukt. Elk `kind`
wordt de bediening waarin een ongeldige waarde niet uit te drukken is:

| `kind` | Bediening |
|---|---|
| `bool` | een keuzelijst met twee opties, `on` / `off` — letterlijk de woorden waarmee MeshCore vergelijkt |
| `enum` | een keuzelijst uit `choices`, met de huidige waarde geselecteerd |
| `int` / `float` | `<input type=number>` met de eigen `min`/`max` van die parameter, `step=1` of `step=any` |
| `radio` | *wordt niet meer aangeboden* — zie de noot hieronder. De weergave met vier velden en hun grenzen zit nog in de pagina en in `choices`, dus terugzetten kost geen werk aan de bediening |
| tekst, `secret=1` | `type=password`, nooit voorgevuld |
| tekst | gewoon invoerveld, `maxlength=39` |

> **`radio` is er sinds 2.6.0 uit, en dat is een regel en geen omissie.** Van
> afstand mag het zendvermogen gewijzigd worden en verder niets aan de radio:
> geen frequentie, geen spreidingsfactor, geen coderingssnelheid, geen
> bandbreedte. Een verkeerde `tx` maakt een node zwakker maar bereikbaar; een
> verkeerde frequentie of modulatie haalt hem van de lucht — hij hoort niemand
> meer en niemand hoort hem, en geen enkel commando draait dat terug omdat er
> geen weg meer naar binnen is. Dat koop je niet af met een zwaardere
> bevestiging. De regel wordt afgedwongen door de regel uit `CFG_PARAMS` te
> halen, wat deze pagina, de schrijfweg van de server én de weg over LoRa naar
> een gemonitorde repeater in één keer sluit, in plaats van door drie schermen
> die er elk zelf iets van vinden. Wat het kost, hardop gezegd: deze pagina
> blijft over wifi bereikbaar als de radio verkeerd staat (wifi en LoRa zijn
> onafhankelijk), dus dit was de laatste weg die een verkeerde bandbreedte nog
> zonder ladder kon rechtzetten. Dat is nu de seriële kabel of de mesh-CLI.

Elk veld is voorgevuld met wat er nu in de node staat, zodat "zet dit op wat het
al is" met één klik een proefrit is over de hele schrijfweg. De vier radiovelden
bestaan omdat `get radio` met komma's antwoordt en `set radio` spaties wil: één
vak waarin `869.525 250 11 5` overgetypt moet worden, is het vak waarin een
tikfout een node kwijtmaakt.

De drie **risicoklassen staan gegroepeerd en uitgelegd boven de bediening** — dus
vóór de keuze, en niet pas in het venster dat om een bevestiging vraagt. Klasse 1
slaat meteen op, klasse 2 vraagt een bevestiging die de parameter en de waarde
noemt, klasse 3 laat de naam van de node overtypen en zegt het gevolg erbij. Die
drempel zit in de browser, en dat is een bewuste grens: `POST /api/cfg` kent geen
bevestigingsveld, want de server schrijft langs dezelfde weg en zou erover
struikelen. Wat de node wél doet — de waarde toetsen aan de tabel en hem
teruglezen — is de controle die telt, en die draait ongeacht wie er aanklopte.

De uitslag meldt **wat de node antwoordt, niet wat er gevraagd is**, met dezelfde
`cfgSameValue()` die de server gebruikt. `advert.interval 61` zegt dus gewoon dat
er 60 staat, en dat wordt getoond als een eigen uitkomst en niet als een fout,
want het is het gewone geval. Bij een `reboot`-parameter komt de regel erbij dat
het bewaard is maar nog niet actief.

**Pakketfilter** wordt getekend uit `GET /api/filter` en schrijft via
`POST /api/filter`. Er is met opzet **geen tekstvak voor een commandoregel** — dat
zou een CLI op een webpagina zijn, en dan hangt de zwaarte van een handeling af
van hoe iemand toevallig spelt. Elke knop draagt een vast werkwoord en leest zijn
getallen uit begrensde invoervelden op het moment dat erop gedrukt wordt. De
zwaarte volgt de *richting* van de wijziging en waar ze bovenop komt, gelijk aan
`pktfilter.risk_of()` op de server: `off` en `reset` vragen helemaal niets, en
aanzetten vraagt de naam van de node zodra er al een regel staat die een hele
categorie dichtzet. Zie
[`packet-filter.md`](packet-filter.md#beheren-vanaf-de-eigen-pagina-van-de-node).

Wat het kostte: de pagina ging van 25.839 naar 46.086 byte flash (+20.247).
Daarvan is 980 byte de inklapbare indeling zelf (714 markup en CSS, 266 het
onthouden), 10.263 byte de woordenlijsten voor twee talen, en 9.004 byte de twee
formulieren. De statische buffer van `handleCfgList()` ging van 3000 naar 5600
byte, berekend op het slechtste geval in plaats van op het gewone: de oude maat
paste met 122 byte over, en één parameter erbij had de lus stilletjes laten
stoppen met een antwoord dat geldige JSON is en onvolledig.

Wat er met opzet **niet** in zit: `prv.key`, `bridge.secret` en `set freq`
ontbreken nog steeds, om de redenen uit §4.11 en de changelogregel bij 2.1.0 — het
oppervlak is niet ruimer geworden, alleen bedienbaar. En de weg terug is
onaangeroerd: `filter off` en `filter reset` werken nog altijd over de mesh-CLI,
zonder WiFi, zonder deze pagina en zonder server.

### 4.5 OTA over het gewone netwerk

`AsyncElegantOTA.begin(&_server, _cfg.user, _cfg.console_pass)` hangt de uploader
aan dezelfde server, achter dezelfde login. Firmware-upgrades gaan over het
gewone WiFi — geen `start ota` hoeven af te vuren, op een soft-AP inloggen en van
daaruit uploaden.

`start ota` wordt onderschept, want beide uploaders willen poort 80. Het drukt
**niet** louter de `/update`-URL af: het legt onze eigen server stil en geeft het
over aan de standaard soft-AP-updater. Een eerdere versie drukte wel de URL af,
in de veronderstelling dat een upload over het gewone netwerk altijd werkt — en
verwijderde daarmee de enige fallback die dat wel deed. Een herstelpad mag nooit
afhangen van datgene waarvan hersteld wordt.

Als de module zichzelf uitgeschakeld heeft na herhaalde crashes (§4.14), geeft
`msnet_handle_command()` meteen false terug en werkt de standaard `start ota`
sowieso. `DISABLE_WIFI_OTA` wordt om die reden bewust niet gezet.

#### Uploaden met curl: schakel `Expect: 100-continue` uit

Dit heeft uren gekost, dus het wordt opgeschreven. curl voegt een
`Expect: 100-continue`-header toe aan elke `-F`-upload van enige omvang.
AsyncWebServer beantwoordt die niet op de manier waarop curl wacht, en de upload
faalt dan op een manier die op succes lijkt:

- curl meldt HTTP-status **100** als eindresultaat, nooit 200 of 400
- de node **herstart toch**, omdat `AsyncElegantOTA` `restart()` aanroept in de
  antwoordhandler, ongeacht of `Update.end()` geslaagd is
- de node komt dus terug op de *oude* firmware, en de herstart bewijst niets

Onderdruk de header en het werkt:

```bash
curl -H "Expect:" -u admin:PASSWORD \
     -F "MD5=$(md5sum firmware.bin | cut -d' ' -f1)" \
     -F "file=@firmware.bin;filename=firmware.bin" \
     http://<node-ip>/update
```

Het `MD5`-veld is **verplicht** — zonder dat antwoordt de handler
`400 MD5 parameter missing` nog voor de upload begint. De browserpagina die bij
AsyncElegantOTA geleverd wordt, berekent het aan clientzijde, en daarom werkt
uploaden vanuit een browser wanneer een naïef curl-commando dat niet doet.

**Controleer achteraf de versie, nooit de herstart.** `ver` over de console, of
het `ms`-veld in `GET /api/status`, vertelt wat er werkelijk draait.

### 4.6 Waarom een OTA uw sleutels niet verliest

Dit is de vraag die mensen voor elke flash stellen, dus het is de moeite waard om
precies te zijn.

**De applicatie en het bestandssysteem staan in verschillende
flashpartities.** Een OTA schrijft enkel de inactieve applicatiepartitie. Ze
raakt SPIFFS nooit aan, waar de identiteit staat.

Op een ESP32-S3 van 16 MB — `boards/heltec_v4.json` zet
`"partitions": "default_16MB.csv"` — ziet de tabel er zo uit:

| Partitie | Type | Offset | Grootte | Bevat |
|---|---|---|---|---|
| `nvs` | data | `0x009000` | `0x005000` (20 KB) | niet-vluchtige sleutel/waarde-opslag |
| `otadata` | data | `0x00e000` | `0x002000` (8 KB) | welk app-slot geboot wordt |
| `app0` | app, ota_0 | `0x010000` | `0x640000` (**6,25 MB**) | firmwareslot A |
| `app1` | app, ota_1 | `0x650000` | `0x640000` (**6,25 MB**) | firmwareslot B |
| `spiffs` | data | `0xc90000` | `0x360000` (**3,38 MB**) | **identiteit, prefs, ACL, configuratie** |
| `coredump` | data | `0xff0000` | `0x010000` (64 KB) | crashdumps |

De rekensom klopt exact:
`0x010000 + 0x640000 = 0x650000`, `0x650000 + 0x640000 = 0xc90000`,
`0xc90000 + 0x360000 = 0xff0000`, `0xff0000 + 0x010000 = 0x1000000` = 16 MB.

Dus:

- **OTA** schrijft het inactieve app-slot, kantelt `otadata`, herstart. `spiffs`
  onaangeroerd. Sleutels, contacten, prefs en ACL blijven behouden.
- **`pio run -t upload`** over serieel schrijft enkel de app-partitie. Zelfde
  resultaat.
- **`esptool erase_flash`** wist alles, `spiffs` inbegrepen. De private sleutel is
  weg en de node heeft een nieuwe identiteit.
- **Een volledige samengevoegde `.bin` op offset 0 flashen** overschrijft de hele
  chip, `spiffs` inbegrepen. Zelfde uitkomst.

De regel: een applicatie flashen is veilig; wissen of hele-chipimages schrijven
is dat niet.

> `default_16MB.csv` wordt meegeleverd met de Arduino-ESP32-core, niet met deze
> repository, dus de rijwaarden hierboven konden niet uit deze werkboom gelezen
> worden. Het is de standaardtabel en de rekensom is intern consistent, maar
> controleer tegen uw eigen build voor u erop vertrouwt bij een herstelactie:
> `python -m esptool --port COM4 read_flash 0x8000 0xc00 ptable.bin` en daarna
> `gen_esp32part.py ptable.bin`.

Neem sowieso een back-up. Het kost één commando:

```bash
python -m esptool --port COM4 read_flash 0 0x1000000 backup.bin
```

(Gebruik `0x800000` voor een bord van 8 MB.)

### 4.7 Back-up en herstel van het bestandssysteem

`/api/backup` levert een regelgebaseerd tekstformaat op, bewust zo opgezet dat
geen van beide zijden ooit een volledig bestand in RAM houdt:

```
MESHMANAGER-BACKUP 1
FILE /identity 64
<up to 64 bytes per line, lowercase hex>
FILE /repeater_prefs 128
<hex>
END
```

`HEX_PER_LINE` is 64, dus elke regel telt hoogstens 128 hextekens. Het herstel
leest regel per regel met `readBytesUntil('\n')` in een vaste buffer. Drie
bestanden zijn uitgesloten (`skipInBackup()`): het back-upbestand zelf, het
herstelbestand, en de boot-teller `/msboot` — een boot-teller herstellen zou een
node rechtstreeks terug in safe mode zetten.

Het herstel schrijft terwijl het parseert en herstart alleen bij succes, dus een
beschadigde upload laat de node achter zoals hij was. Het antwoord gaat eerst
buiten en de herstart gebeurt 1,5 s later vanuit `msnet_loop()`, zodat de browser
het ook werkelijk ontvangt.

> **Een back-up bevat uw private sleutel.** Wie het bestand heeft, heeft de
> identiteit van uw node. Daarom staat `/api/backup` achter de login, daarom zegt
> de pagina dat in duidelijke taal, en daarom hoort het standaardwachtwoord
> gewijzigd te worden voor de node op een netwerk gezet wordt. Zie
> [`security.md`](security.md).

### 4.8 Telnetconsole

Poort 23, gewone telnet, dezelfde inloggegevens. Aanmelden is een prompt in twee
stappen; drie mislukte wachtwoordpogingen verbreken de verbinding.

Eenmaal geauthenticeerd gaan regels eerst naar `msnet_handle_command()` en
daarna naar `MyMesh::handleCommand()` — zo krijgt men de volledige MeshCore-CLI
over WiFi, plus de `wifi`-commando's. `quit` of `exit` sluit de sessie.

Er bestaan twee time-outs omdat een dode TCP-sessie op een ESP32 nog lang
`connected()` blijft melden. Zonder die time-outs zou één verbroken verbinding het
debugkanaal permanent sluiten dat men net nodig heeft wanneer er iets misloopt:

| Constante | Waarde | Effect |
|---|---|---|
| `CON_IDLE_MS` | 5 min | Een stille sessie wordt door de node gesloten |
| `CON_TAKEOVER_MS` | 1 min | Een sessie die zo lang stil is, kan door een nieuwe verbinding overgenomen worden |

De console is uitgeschakeld in safe mode.

> De console is **platte tekst**: inloggegevens en alles wat ingetypt wordt, gaan
> onversleuteld over het netwerk. Enkel LAN of VPN.

### 4.9 `wifi`-commando's op de CLI

`repeater-hooks.patch` voegt `msnet_handle_command()` in
`MyMesh::handleCommand()` in, zodat elk commando hieronder **evengoed over LoRa,
over serieel als over de telnetconsole** werkt. Dat is de bedoeling: een kapotte
WiFi-configuratie moet vanaf de mesh te herstellen zijn, want een node met een
verkeerde SSID heeft geen andere weg naar binnen.

| Commando | Effect |
|---|---|
| `ver` | De versie van deze module plus de MeshCore-versie waarop ze gebouwd is. **Geen MeshManager-naam in het antwoord betekent dat deze module niet draait** |
| `wifi` | Toestand, IP, signaal, batterij, publicatie-interval |
| `wifi ssid <name>` | Het netwerk instellen; leeg herstelt de ingecompileerde standaardwaarde |
| `wifi pass <word>` | Het WiFi-wachtwoord instellen |
| `wifi connect` | Opnieuw verbinden met de bewaarde inloggegevens |
| `wifi ap` | Nu meteen ons eigen netwerk uitzenden |
| `wifi on [minutes]` | WiFi geforceerd omhoog brengen en daar houden (standaard 30 min), wat de modus en de batterij ook zeggen |
| `wifi off` | Terug naar automatisch energiebeheer |
| `wifi console <user> <pass>` | De console- en weblogin wijzigen |
| `wifi mqtt …` | Brokerinstellingen; zie hieronder |
| `wifi power …` | Energiebeheer; `wifi power` alleen drukt de subhelp af |
| `wifi mon …` | Gemonitorde repeaters; zie §4.11 |
| `wifi settings …` | De eigen CLI-instellingensweep van de node |
| `wifi clock` | Klokstatus; **bewust alleen-lezen**, zie §4.12 |
| `wifi fw` | Welke versie vanuit welke applicatiepartitie draait, voor welke bouwomgeving dit image gemaakt is, wat er in de andere partitie staat en hoe de laatste upload afliep (1.12.0+) |
| `wifi fw rollback` | De andere applicatiepartitie weer opstarten — de firmware van vóór de laatste upgrade (1.12.0+) |
| `wifi wdt` | Doelbewust de lus blokkeren en nagaan of de watchdog afgaat |

Van deze lijst is `wifi fw rollback` degene die over de **mesh** het meeste
uitmaakt. Elke andere weg naar binnen loopt over IP, dus een upgrade waarvan de
enige fout is dat hij de wifi niet meer haalt, neemt ze in één klap allemaal mee
— en LoRa komt op vanuit de radiodriver, nog vóór al die andere. Zie
[`firmware-upgrade.md`](firmware-upgrade.md).

Subcommando's van `wifi mqtt`: `host`, `port`, `user`, `pass`, `prefix`,
`rx <on/off>`, `on`/`off`. Zonder argument drukt het een statusregel af:

```
verbonden, broker=<host>:1883, prefix=meshcore, rx=aan,
stats=412 pkt=9021 drop=3 cmd=7/2
```

Die teller `cmd=<aanvaard>/<geweigerd>` is het ene ding dat drie storingen uit
elkaar houdt die er anders identiek uitzien: de site heeft nooit iets gevraagd,
de broker heeft de subscribe geweigerd, of het commando is uitgevoerd en heeft
niets veranderd.

Subcommando's van `wifi settings`: kaal (status van de sweep), `now` (er een
afdwingen), `list <n>` (één parameter per aanroep — een CLI-antwoord is 160 bytes
en dit moet over de mesh werken), `iv <minutes>` (5 … 65535).

`wifi wdt` verdient een noot, want het oogt roekeloos en is het tegendeel. Het
blokkeert de lus gedurende `WDT_TIMEOUT_S + 10` seconden — *begrensd*. Een
oneindige lus zou de node onherstelbaar doen vastlopen mocht de watchdog niet
blijken te werken, en deze node staat op een dak. Gaat de watchdog af, dan
herstart de node halverwege en is de hele keten (vastloper → watchdog → herstart
→ boot-teller) bewezen. Gaat hij niet af, dan komt de node gewoon terug en is
geleerd dat het net niet gespannen is, zonder schade.

> `wifi pass` en `wifi console` sturen geheimen onversleuteld over LoRa wanneer
> ze vanaf de mesh-CLI gebruikt worden. Gebruik voor die twee bij voorkeur de
> console of de webpagina.

### 4.10 Commando's over MQTT — het `cmd`-topic

De node abonneert zich op `<prefix>/<node>/cmd` bij **elke geslaagde
brokerverbinding**, binnen `mqttEnsure()`. Een abonnement leeft binnen één sessie
en deze client verbindt met een clean session, dus één keer abonneren bij het
opstarten zou werken tot de eerste WiFi-hapering en daarna stilzwijgend stoppen —
precies het soort storing dat zich alleen ooit toont als *"die knop deed vroeger
iets"*.

| Woord | Effect | Sinds |
|---|---|---|
| `settings` | Een sweep van **de eigen CLI van deze node** afdwingen en die publiceren zodra ze klaar is | 1.8.0 |
| `settings <key>` | De CLI sweepen van een repeater die deze node *monitort*, over LoRa, en die publiceren onder de naam van die repeater | 1.9.0 |
| `status` | Onmiddellijk een statistiekbericht publiceren | 1.8.0 |
| `time <epoch>` | De klok van deze node op die UNIX-tijd in UTC-seconden zetten, en daarna de klokken van de gemonitorde repeaters over LoRa controleren | 1.10.0 |

Dat is de volledige woordenschat.

#### Waarom het een whitelist is en geen doorgeefluik naar de CLI

De verleidelijke implementatie was één regel: de payload aan `handleCommand()`
geven en klaar, precies zoals de telnetconsole al doet. Ze werd verworpen, en de
reden verdient het volledig uitgeschreven te worden, want ze is het
beveiligingsmodel van deze functie.

**De console vraagt om een wachtwoord over een verbinding die de beheerder
controleert. Het `cmd`-topic is bereikbaar voor iedereen met
brokerinloggegevens** — gedeeld, gelekt, of gewoon verkeerd ingetypt in een
tweede client. En de CLI erachter bevat `reboot`, `set`, en de wifi-commando's.
Eén `reboot` in een lus is een verloren repeater op een dak, zonder foutmelding
ergens en zonder iets dat het verlies verbindt met een script dat iemand heeft
laten draaien.

Daarom wordt het woord getoetst aan een lijst van precies drie, en de twee
argumenten die bestaan verbreden die lijst niet:

- **Het argument bij `settings` wordt nooit tekst die een CLI bereikt**, hier
  noch aan de overkant. Het selecteert enkel één record uit de monitorlijst, en
  wat er dan de lucht in gaat, is de ingecompileerde `SET_PARAMS`-tabel en niets
  anders. Die lijst is uitsluitend beschrijfbaar vanaf de beheerpagina en de
  mesh-CLI, die beide om een wachtwoord vragen. Een sleutel die op geen enkel
  record past, of op meer dan één, wordt geweigerd en geteld.
- **Het argument bij `time` wordt hier als getal geparseerd**, begrensd door een
  venster van jaren, en toegepast door `clockApplyOwn()`, dat een klok nooit
  anders dan *vooruit* zal zetten.

Het ergste wat een aanvaller op de broker daardoor kan bereiken, is: de node laten
publiceren wat hij toch al uit zichzelf publiceert, een repeater uitlezen die zijn
beheerder al gekozen heeft om te monitoren, of de klok van deze node vooruitzetten
binnen dat venster — hoogstens eens per 30 s, en voor de twee die zendtijd kosten
respectievelijk hoogstens eens per tien minuten en eens per uur.

**De mogelijkheid om de klok te zetten is reëel en wordt benoemd in plaats van
weggemoffeld.** Een klok die ver in de toekomst geduwd is, kan niet over de lucht
teruggezet worden, want niets in dit systeem mag een klok achteruit zetten
(§4.12). Daarvan herstellen vereist `clkreboot` op de node en een hersynchronisatie.
Het blijft een veel kleinere mogelijkheid dan de `reboot` die een doorgeefluik zou
hebben weggegeven, en de functie is zinloos zonder haar.

#### Formaat en afhandeling

| Regel | Waarde | Waarom |
|---|---|---|
| Maximale payload | `MQTT_CMD_MAX` = 96 bytes | Langer dan het langste aanvaarde commando (`settings ` + een sleutel van 64 tekens), zodat een payload die niet past herkenbaar *te lang* is in plaats van stilzwijgend afgeknot tot iets dat toevallig past |
| Minimale tussenruimte | `MQTT_CMD_MIN_GAP_MS` = 30 s | Een energiebudget, geen beveiligingsmaatregel: elk aanvaard commando eindigt in een publicatie, en een node op een paneel kan er geen per seconde betalen omdat iemand een script heeft laten draaien |
| Te vroeg | weggegooid, niet in de wachtrij | "Doe het nu" verliest zijn betekenis als het staat te wachten |
| Retained | moet `false` zijn bij de publisher | Een retained commando wordt bij elke herverbinding opnieuw afgeleverd, dus de node zou sweepen bij elke boot en na elke WiFi-onderbreking, eeuwig |
| Witruimte | weggeknipt | Een publisher die er een newline aan toevoegt, wordt daar niet voor gestraft |
| Verkeerde ariteit | geweigerd | `status <om het even wat>` wordt geweigerd in plaats van stilletjes als `status` uitgevoerd: een publisher die een argument meestuurt bij een commando dat er geen heeft, heeft iets verkeerd begrepen, en het toch uitvoeren verbergt dat voor beide kanten |

De callback (`mqttOnMessage`) draait binnen `_mqtt.loop()` en doet precies één
ding: het woord naar een slot kopiëren en terugkeren. `PubSubClient` is daar
midden in het lezen van zijn socket, en publiceren van binnenuit zijn eigen read
is hoe men een antwoord door een binnenkomend bericht heen geweven krijgt.
`mqttRunCommand()` handelt er enkele instructies later naar, vanuit de gewone lus
— dezelfde discipline als bij de raw-packetwachtrij en de apply-vlaggen van de
webserver. Staat er al een woord te wachten, dan wordt het nieuwe weggegooid.

Aanvaarde en geweigerde commando's worden apart geteld en door `wifi mqtt`
afgedrukt.

Hiervoor is een ACL-record bij de broker nodig: het account van de node moet
`<prefix>/<eigen node-id>/cmd` mogen **lezen**. Zonder dat wordt de subscribe
geweigerd, en ziet de knop op de site er precies even dood uit als voor dit
allemaal bestond. Zie [`mqtt.md`](mqtt.md).

### 4.11 Andere repeaters monitoren

#### De pollronde

Per peer is de volgorde die welke een chatclient uitvoert: een `ANON_REQ` met het
wachtwoord erin, daarna — zodra aanvaard — een `REQ` van het type `GET_STATUS`,
en sinds 1.4.0 ook telemetrie en buren. Een `RESPONSE` brengt `RepeaterStats`
terug.

Het is een toestandsmachine aangestuurd vanuit `msnet_loop()`, **één peer per
keer**. Niet omdat dat eenvoudiger is, maar omdat deze node een repeater is: een
uitbarsting van logins vanuit precies die node die andermans verkeer moet
doorgeven, is asociaal, en elke gefloode login kost de hele mesh zendtijd.

| Constante | Waarde | Waarom |
|---|---|---|
| `MON_STEP_MS` | 30 s | Een eerste login wordt geflood en het antwoord komt terug over een onbekend aantal hops; 20 s bleek krap |
| `MON_GAP_MS` | 3 s | Ademruimte tussen peers |
| `MON_FIRST_MS` | 60 s | Eerste automatische ronde na het opstarten, laat genoeg om niet met de opstart te vechten |
| `MON_BACKOFF_AFTER` / `MON_BACKOFF_EVERY` | 3 / 4 | Drie verzoeken die elk 30 s uitzitten, is 90 s besteed aan een peer die er gewoon niet is; na drie onvruchtbare rondes wordt een record nog maar elke vierde keer geprobeerd. Elk antwoord wist dat |
| `MON_MIN_HEX` | 12 | Kortste sleutel die aanvaard wordt bij het *toevoegen* van een node: 6 bytes, wat deze firmware zelf gebruikt om een node te benoemen. Daaronder houden botsingen op theoretisch te zijn en monitort men de verkeerde repeater |
| `MON_PASS_MAX` | 16 | Het protocol knot af op 15 tekens |
| `_mon_interval` | 900 s standaard | Tussen twee rondes |

Drie tellers per record, niet twee: `polls` (pogingen), `oks` (antwoorden
ontvangen en geparseerd) en `pubs` (werkelijk gepubliceerd). `oks` werd vroeger
alleen verhoogd bij een geslaagde publicatie, wat betekende dat een meting die
opgehaald maar nooit afgeleverd werd er precies uitzag als een die nooit
opgehaald werd — en dat uitzoeken vergde een sniffer op de broker. Elk verschil
tussen de drie is nu zichtbaar op de pagina.

Een trace van 12 regels (`wifi mon trace`, de beheerpagina, serieel) bestaat om
dezelfde reden: een statusverzoek dat nooit verzonden werd (packetpool leeg) en
een dat wel verzonden maar nooit beantwoord werd, laten beide `polls=1, oks=0,
lr=1` achter.

#### Aanmelden zonder wachtwoord

Aanmelden kan met het admin- of lees/schrijfwachtwoord van de gemonitorde
repeater, maar er is een nettere weg die helemaal geen wachtwoord nodig heeft:
**een leeg wachtwoord doet de overkant de wachtwoordcontrole overslaan en in de
plaats daarvan uw publieke sleutel opzoeken in zijn toegangslijst**
(`handleLoginReq()` in `MyMesh.cpp`). De beheerder daar voegt u eenmalig toe:

```
setperm <your-pubkey-hex> 1
```

waarbij 1 alleen-lezen is, 2 lezen/schrijven en 3 admin. Niemand hoeft een
wachtwoord uit te delen, en de toegang kan aan hun kant alleen ingetrokken
worden. In de monitorlijst is een leeg wachtwoord daarom **een keuze, geen
weglating**, en de beheerpagina behandelt het niet als een ontbrekend veld.

> Een geweigerde login levert **helemaal geen antwoord** op, precies zoals een
> repeater die buiten bereik is. Vandaar de toestand `LOGIN_NOANSWER` in plaats
> van te doen alsof men weet welke van de twee zich voordeed. "Geen antwoord"
> betekent ofwel dat uw sleutel nog niet in hun lijst staat, ofwel dat u ze
> gewoon niet kunt bereiken — de gehoorde lijst op de beheerpagina is wat de twee
> uit elkaar houdt.

#### De instellingensweep over LoRa (1.9.0)

De dagelijkse sweep van de eigen instellingen van *deze* node kost geen enkele
zendtijd: `handleCommand()` is een functieaanroep. De variant met een sleutel
leest die van iemand anders, over de radio, en dat is een ander beest:
**negentien verzoeken en tot negentien antwoorden op een gedeelde band**, de
helft ervan betaald door een zonnerepeater op een dak.

Ze bestaat omdat een repeater die zelf niet naar MQTT publiceert helemaal geen
commandopad had. Zijn statistieken komen doorgegeven door de node die hem
monitort; zijn configuratie kwam nergens aan, en de knop op zijn instellingenpagina
zei "doorgegeven, alleen de node zelf kan zijn eigen CLI lezen" — wat even waar
als nutteloos was. De monitor meldt zich al aan en pollt hem, en aanvaardt sinds
1.4.0 `TXT_MSG`-antwoorden van hem. Er werd alleen nooit iets *gevraagd*.

De volgorde, aangestuurd vanuit dezelfde toestandsmachine als de gewone poll (één
radio, één antwoordslot, één sessie per peer):

1. de login van een eerdere poll hergebruiken, of er een sturen en `MON_STEP_MS`
   wachten;
2. het commando van de parameter versturen, `MON_SET_FIRST_MS` wachten op het
   eerste antwoord en `MON_SET_STEP_MS` op de rest;
3. `MON_SET_GAP_MS` wachten, dan de volgende parameter;
4. eenmaal publiceren, op het einde, op het gewone `stats`-topic met de
   gemonitorde repeater in `repeater.pubkey_prefix`, een leeg `metrics`-object, en
   `via` ingesteld op deze node.

| Limiet | Waarde | Waarom |
|---|---|---|
| — | **enkel op verzoek** | Een schema zou die zendtijd eeuwig blijven uitgeven, voor waarden die eens per jaar wijzigen. Iemand drukt op een knop, of er gebeurt niets |
| `MON_SET_MIN_GAP_MS` | 10 min | Een herladen pagina of een browsertabblad dat op vernieuwen blijft staan, mag de band niet bezet kunnen houden. Veel langer dan een sweep nodig heeft, zodat een legitieme tweede poging nooit geblokkeerd wordt, en het plafonneert de functie op ruwweg 1 % van het uur wat men stroomopwaarts ook doet |
| `MON_SET_GAP_MS` | 2 s | Spreidt negentien heen-en-weerreizen over minuten in plaats van zo snel te vuren als de overkant antwoordt. Een uitbarsting vanuit de relay zelf is de minst vergeeflijke congestie die er bestaat. Twee seconden is waar de Home Assistant-implementatie op uitkwam voor dezelfde volgorde op dezelfde band |
| `MON_SET_STEP_MS` | 12 s | De gemeten waarde van Home Assistant, zelfde band, zelfde hops |
| `MON_SET_FIRST_MS` | 20 s | Het eerste pakket na een login is het pakket dat afhangt van het net geleerde pad — de pollvolgorde is daar in 1.3.1 op de harde manier achter gekomen |
| `MON_SET_SILENT_MAX` | 3 | Wie de derde parameter negeert, zal ook de negentiende negeren; doorgaan betekent zestien keer extra in een gat zenden |
| `MON_SET_TOTAL_MS` | 6 min | Terwijl een sweep loopt, wachten de gewone pollrondes, want ze delen deze toestandsmachine; het plafond is wat verhindert dat "wachten" "tot aan de volgende herstart" gaat betekenen |

Er is **geen herhaling per parameter**, met opzet. Home Assistant draait een
tweede ronde voor de parameters die stil bleven; de wachttijd van 12 s was het
kopiëren waard, de extra ronde niet. Home Assistant draait op netstroom via een
node aan een USB-kabel, terwijl hier een tweede ronde de kostprijs van de hele
sweep verdubbelt om precies de parameters na te jagen die het minst waarschijnlijk
zullen antwoorden. Een stilte wordt in de plaats daarvan als een stilte
gepubliceerd.

Het openstaande verzoek wordt als **sleutel** bijgehouden, niet als index in de
monitorlijst, want die lijst kan bewerkt worden tussen het verzoek en het moment
waarop de toestandsmachine er vrij voor is — `monDelete()` schuift alles achter
het gat één plaats op, en een index zou dan stilletjes de verkeerde repeater
aanspreken. Dat is de ene storing die deze functie niet mag hebben: ze meldt zich
ergens aan en voert er commando's uit. Een verzoek dat nooit aan de beurt kwam,
verloopt na `MON_SET_TOTAL_MS`, zodat één verschaald verzoek niet elk later
verzoek kan beantwoorden met "er loopt er al een" tot aan de volgende herstart.

Het sleutelargument zelf wordt genormaliseerd met een **lagere ondergrens dan bij
het toevoegen van een node**: acht hextekens in plaats van twaalf, oneven lengtes
toegestaan, en **regelrecht geweigerd wanneer het op meer dan één record past**.
Een node toevoegen met een te korte sleutel betekent de verkeerde repeater
monitoren; kiezen uit een lijst die al bestaat is een andere vraag, en acht is
wat de site beschouwt als de kortste sleutel die ze dezelfde node durft te noemen
— kortere weigeren zou betekend hebben dat precies de Home Assistant-sleutels van
vijf bytes geweigerd werden waarvoor deze functie bestaat.

#### `SET_PARAMS` — de parametertabel

Negentien records, in `MeshManagerNet.cpp:1352`. Dezelfde tabel wordt gebruikt voor
de eigen dagelijkse sweep van deze node en voor de LoRa-sweep van een gemonitorde
node, omdat beide in dezelfde kolom van dezelfde tabel op de site belanden — en
een regel die voor de ene wel en voor de andere niet gold, is precies hoe
`cmd:temp = Unknown command` in een database terechtkomt.

| Sleutel | CLI-commando | Opmerkingen |
|---|---|---|
| `name` | `get name` | |
| `role` | `get role` | |
| `radio` | `get radio` | |
| `freq` | `get freq` | |
| `tx` | `get tx` | |
| `af` | `get af` | |
| `repeat` | `get repeat` | |
| `advert.interval` | `get advert.interval` | |
| `flood.advert.interval` | `get flood.advert.interval` | |
| `flood.max` | `get flood.max` | |
| `flood.max.unscoped` | `get flood.max.unscoped` | Nieuwere firmwares splitsen het floodbudget in tweeën; op een firmware die dat niet doet, wordt het `??`-antwoord geweigerd en is de parameter gewoon een misser |
| `allow.read.only` | `get allow.read.only` | |
| `rxdelay` | `get rxdelay` | |
| `txdelay` | `get txdelay` | |
| `lat` | `get lat` | |
| `lon` | `get lon` | |
| `region.home` | `region home` | houdt wat na `" is "` volgt |
| `region.default` | `region default` | houdt wat na `" is "` volgt |
| `cmd:region` | `region` | **antwoord over meerdere regels verwacht**; met opzet als laatste |

Drie eigenschappen van die tabel dragen hun eigen redenering:

- **`after`** (het scheidingsteken `" is "`) wordt per parameter opgegeven in
  plaats van als algemene regel, omdat een nodenaam zelf `" is "` zou kunnen
  bevatten.
- **`list`** geeft aan dat een antwoord over meerdere regels verwacht wordt en
  geen fout is. Precies één parameter zet het, en dat is het punt: elk ander
  record houdt de regel aan dat een antwoord over meerdere regels een tabel is die
  niets te zoeken heeft in een instellingenkolom, en wordt daarvoor nog steeds
  geweigerd.
- **`cmd:region` staat met opzet als laatste.** Het is het langste antwoord en het
  minst dringende in de tabel — de regiotopologie wijzigt ongeveer even vaak als
  iemand de node herflasht, terwijl alles erboven is waar men naar kijkt wanneer
  er iets misloopt. Mocht een sweep ooit door zijn tijdbudget heen gaan, dan is
  dit het record dat erin zou moeten ontbreken.

De regioboom ziet er zo uit, en het lezen van `printChildRegions()` maakt uit wat
de markeringen betekenen:

```
*
 eu F
  bx F
   be^ F
    be-vbr F
```

`*` is gewoon de naam van de wildcard-hoofdregio — **geen** markering voor de
actieve regio; `^` markeert de thuisregio; een afsluitende ` F` betekent dat
flooding daar toegestaan is en de afwezigheid ervan betekent `DENY_FLOOD`;
inspringing is nesting van ouder/kind. MeshCore plafonneert de boom zelf op
160 bytes (`handleRegionCmd()` roept `exportTo(reply, 160)` aan) en verzendt het
volledige antwoord als **één** tekstbericht, dus op dit pad is er geen
verzamelen-tot-het-stil-is en geen verruimde time-out: één commando, één
antwoord, precies zoals bij elk ander record. Een boom die te groot is voor
160 bytes wordt aan de overkant afgeknipt, niet hier.

Twee gevolgen van de toevoeging in 1.11.0, die beide moesten wijken:

- `SET_VALUE_MAX` ging van 32 naar **176**, want 160 is het plafond dat MeshCore
  oplegt. Een tweede, grotere buffer voor die ene lange parameter werd verworpen:
  het bespaart zo'n vijf kilobyte op een onderdeel van twee megabyte en betaalt
  daarvoor met twee opslagpaden, twee dimensioneringsregels en een speciaal geval
  in elke lus die de tabel doorloopt — en de dag dat iemand een tweede lange
  parameter toevoegt, is de goedkope versie degene die breekt.
- `jsonEsc()` schrijft nu `\n`, `\r` en `\t` in plaats van ze weg te laten.
  Stuurtekens werden met opzet weggelaten, en voor een nodenaam is dat juist, maar
  hier **zijn** de regeleindes en de inspringing de waarde. Ze weglaten zou
  veertien betekenisvolle regels tot één aaneenschakeling van regionamen hebben
  gemaakt, succesvol gepubliceerd, en fout.

Eveneens sinds 1.11.0 worden beide schrijfwijzen van een weigering in MeshCore —
`Error…` en `Err - …` — herkend. Alleen de eerste werd dat, dus
`Err - unknown region` werd opgeslagen alsof het een instelling was, met een verse
tijdstempel ernaast: een antwoord dat er gezaghebbender uitziet dan
"(geen antwoord)" terwijl het strikt minder betekent. Ze worden voluit geschreven
vergeleken in plaats van als `Err`-voorvoegsel, zodat een node genaamd *Erratic*
het overleeft.

#### Adminrechten zijn vereist, en een alleen-lezen monitor faalt stilzwijgend

Dit is het allerbelangrijkste operationele feit over de sweep.

**Een repeater voert een CLI-commando alleen uit voor een client die hij als
admin beschouwt** (`handleCommand` wordt vanuit `onPeerDataRecv` alleen bereikt
onder `client->isAdmin()`), en hij zegt **helemaal niets** tegen een client die
dat niet is.

Een alleen-lezen monitor meldt zich dus perfect aan, stuurt negentien commando's,
en hoort negentien stiltes — wat op de lucht er precies uitziet als een node die
buiten bereik is geraakt. Alleen-lezen volstaat voor al de rest in deze module en
is wat de header aanbeveelt; het volstaat niet hiervoor.

```
setperm <monitor-pubkey-hex> 3
```

op de gemonitorde repeater, of geef de monitor het adminwachtwoord van die
repeater.

De sweep **publiceert zijn stiltes in plaats van ze te verbergen**, precies opdat
dit vanaf de site diagnosticeerbaar is in plaats van vanaf een seriële kabel.
Twee storingen die er van op afstand hetzelfde uitzien, worden met opzet uit
elkaar gehouden:

| Storing | Wat er gepubliceerd wordt |
|---|---|
| De login heeft nooit geantwoord | **Helemaal niets.** Er is niets gevraagd, dus er is niets geleerd. De site blijft de waarden tonen die ze had, met hun oude tijdstempels — en zo ziet "we hebben niets geleerd" er eerlijk uit. Negentien nulls publiceren zou waarden weggooien die een eerdere sweep wel binnenhaalde, voor een storing die niets zegt over enige individuele parameter |
| Aangemeld, parameters bleven stil | **Gepubliceerd, met `null` voor elke parameter die niet antwoordde.** De site rendert dat als "(geen antwoord)". Het overschrijft wat we wisten, en dat is de bedoeling: hier hebben we wel gevraagd, en "ze wilden het ons niet zeggen" is een verser feit dan een waarde uit maart |

Startbaar vanaf elke CLI evengoed als vanaf MQTT: `wifi mon settings <hex>` start
er een en rapporteert over de vorige, en `wifi mon trace` toont de volgorde. Dat
telt hier zwaarder dan waar ook elders in deze module, omdat deze faalmodus van
nature stil is.

Dezelfde tabel loopt sinds 2.4.0 ook de andere kant op: `wifi mon set <hex>
<param> <waarde>`, of `POST /api/moncfg`, schrijft **één** parameter naar een
gemonitorde repeater over LoRa en leest die parameter daarna terug — en het is
dat teruglezen dat gemeld wordt, nooit wat de node op de `set` antwoordde. Twee
commando's met een pauze ertussen, één tegelijk, een minuut tussen twee
schrijfacties, en een hard plafond van negentig seconden. De faalwijze die hier
nieuw is, is stilte *nadat* de `set` vertrokken is: het commando is de lucht in
gegaan en of het is aangekomen valt van deze kant niet te zien, wat dan ook
precies zo gemeld wordt en niet als mislukking. De volledige redenering staat in
[`node-management.md`](node-management.md#schrijven-over-lora-via-de-monitor).

Let op welke node deze firmware nodig heeft: **de monitor**. De repeater waarnaar
geschreven wordt, krijgt twee doodgewone CLI-commando's binnen en hoeft niets te
weten — en dat is de hele reden dat deze weg bestaat voor een node die maandenlang
niet opnieuw geflasht wordt.

Sinds 2.8.0 is er een derde ingang voor diezelfde schrijfweg, en die is voor de
node zelf: `set <param> <waarde>` op het `cmd`-topic. Hij bestaat omdat een node
die op MQTT publiceert een werkende verbinding met de broker heeft, zodat "er is
geen weg naartoe" onjuist was over precies de node die het makkelijkst te
bereiken is. De node toetst tegen zijn eigen `CFG_PARAMS` — de parameter moet op
de tabel staan, de waarde binnen zijn grenzen vallen, en de risicoklasse mag niet
hoger zijn dan `CFG_MQTT_MAX_RISK` — en meldt de teruglezing, of de weigering, in
het statistiekbericht dat hij er meteen na publiceert (`cfgset`). Zijn
parametertabel reist mee met de instellingenronde (`cfgspec`), zodat een server
zonder weblogin voor deze node toch een formulier kan bouwen uit de lijst van de
node zelf.

`radio` wordt op deze weg net zomin aangeboden, en niet door een controle hier:
sinds 2.6.0 staat hij helemaal niet meer in `CFG_PARAMS`, en die ene tabel is
waar alle drie de ingangen van afstand uit lezen. Een verkeerde `tx` laat een
node bereikbaar; een verkeerde frequentie, spreidingsfactor, coderingssnelheid of
bandbreedte niet, en er is geen weg terug die niet fysiek is.

### 4.12 Kloksynchronisatie

#### Waarom de mesh dit überhaupt nodig heeft

Een MeshCore-node zet een tijdstempel op de berichten die hij verzendt en op de
adverts die hij uitzendt, en dat vanuit zijn eigen klok, en een ESP32 zonder
batterijgevoede RTC start op wat er ingecompileerd of met `clkreboot` gezet werd.
Een repeater op een dak herstart uit zichzelf — lege batterij, watchdog, een
stroomonderbreking in het onweerseizoen — en komt terug met een klok die mei 2024
aangeeft. Alles wat hij daarna zegt, is verkeerd gestempeld, en niets op de mesh
corrigeert dat, want niets op de mesh weet het ook beter.

De site wel: die draait op een machine waarvan de klok tegen de echte tijd
gedisciplineerd wordt.

#### De commando's en het tijdformaat

| Commando | Waar | Formaat |
|---|---|---|
| `time <epoch>` | MeshCore-CLI, en het `cmd`-topic | **UNIX-epochseconden, UTC.** `CommonCLI.cpp` doet `_atoi` van de rest van de regel, rechtstreeks naar `setCurrentTime` |
| `clock` | MeshCore-CLI | Antwoordt `HH:MM - D/M/YYYY UTC` |
| `clock sync` | MeshCore-CLI | Zet de klok vanuit de **tijdstempel van het verzoekpakket** in plaats van vanuit tekst |

**Alle drie weigeren achteruit te gaan.**

De overkant van een monitorronde krijgt `clock sync`, niet `time <epoch>`, en de
reden is rekenwerk rond zendtijd: tien tekens tegenover vijftien, wat met vijf
bytes berichtheader neerkomt op één cipherblok van 16 bytes in plaats van twee —
een derde van de zendtijd van het pakket, voor een waarde die we sowieso uit onze
eigen klok zouden hebben gehaald. Er is ook geen getal om verkeerd op te maken.

Voor de eigen klok van deze node:

| Constante | Waarde | Betekenis |
|---|---|---|
| `CLOCK_MIN_EPOCH` | 1735689600 (2025-01-01 UTC) | Ondergrens. Geen versiering: `clkreboot` zet de klok op 15 mei 2024 en een ongezet bord start rond zijn builddatum, beide meer dan een jaar in het verleden — zodat één vergelijking "nooit gezet" van "afgedreven" scheidt, en die twee verdienen verschillende woorden op een pagina |
| `CLOCK_MAX_EPOCH` | 4102444800 (2100-01-01 UTC) | Bovengrens. Vangt milliseconden die tot 32 bits afgeknot zijn, een parse die het verkeerde veld las, een typfout met een cijfer te veel |
| `CLOCK_OWN_MIN_STEP_S` | 5 | Kleinste verschil dat een stap waard is. De site publiceert dagelijks en het bericht doet er even over om aan te komen, dus een seconde of twee is de meting en niet de afwijking |

`clockApplyOwn()` weigert drie zaken, elk beschermt tegen een ander ongeluk: een
tijd buiten het venster, een tijd *achter* de onze, en een verschil dat te klein
is om een stap waard te zijn. Een ongezette klok is vrijgesteld van die laatste
regel — een sprong van anderhalf jaar is daar precies wat er hoort te gebeuren.

#### Waarom alles enkel vooruit gaat

Dit is de regel die de hele functie beheerst, en het is geen eigenaardigheid van
MeshCore om omheen te werken.

**Een advert draagt de klok van de uitzendende node, en elke node die de
afzender al kent, gooit een advert weg waarvan de tijdstempel niet gestegen is**
(`onAdvertRecv` in `MyMesh.cpp`). Zet de klok van een repeater een uur terug en
hij is onzichtbaar voor iedereen die hem kent, gedurende een uur. Op een
dakrepeater is dat erger dan om het even welke verkeerde tijdstempel — een
onderhoudscommando dat de node van de mesh haalt, en dat is het ene wat deze
firmware niet mag doen.

Een node waarvan blijkt dat hij **voorloopt**, wordt dus geteld, gerapporteerd, en
precies gelaten zoals hij is. Het is de minste van de twee fouten en de enige
omkeerbare.

#### De ronde langs de gemonitorde nodes

| Constante | Waarde | Waarom |
|---|---|---|
| `MON_CLK_FIRST_MS` | 20 s | Het eerste commando na een login hangt af van een net geleerd pad |
| `MON_CLK_STEP_MS` | 12 s | Per commando daarna |
| `MON_CLK_GAP_MS` | 3 s | Tussen twee commando's, en tussen nodes |
| `MON_CLK_SILENT_MAX` | 3 | Na drie stille nodes na elkaar is wat er misloopt niet de vierde node |
| `MON_CLK_TOTAL_MS` | 5 min | Hard plafond op één doorloop; pollrondes wachten zolang hij loopt |
| `MON_CLK_MIN_GAP_MS` | 1 u | De site vraagt het eens per dag; dit plafonneert wat een brokeraccount daarmee kan doen op eens per uur — nog altijd 24× het bedoelde tempo en nog altijd goedkoper dan één pollronde |
| `MON_CLK_SKEW_S` | 120 | Kleinste afwijking waarop gehandeld wordt |

Er zijn **nergens herhalingen**. Een node die niet op `clock` antwoordt, wordt
overgeslagen tot morgen; een klok is niet dringend genoeg om voor te flooden, en
morgen ligt één dag afwijking verderop.

**Waarom de klok gelezen wordt voor ze gezet wordt.** Blind `clock sync` sturen
kost precies evenveel als `clock` lezen — één commando, één antwoord, elke node,
elke dag. Zuinigheid is dus *niet* het argument, en het tegendeel voorwenden zou
het soort redenering zijn dat in een commentaar blijft voortleven lang nadat het
niet meer klopte. Het argument is dat een leesactie een meting oplevert — "deze
repeater liep vier minuten achter" — waar een blinde sync niets oplevert dat
iemand kan zien, en dat een node die in orde is dan nooit een commando krijgt dat
zijn klok überhaupt wijzigt. De tweede heen-en-weerreis wordt alleen uitgegeven
waar de eerste bewees dat ze nodig was.

**Waarom twee minuten.** Het is de resolutie van de interface, geen smaakkwestie.
`clock` antwoordt op de minuut, dus een aflezing van `09:05` betekent dat de
overkant zich ergens in een venster van zestig seconden bevindt, en het antwoord
bereikt ons seconden nadat het gelezen werd. De afwijking wordt daarom berekend
als een **bereik**, en er gaat pas een correctie uit wanneer het volledige bereik
voorbij de drempel ligt. Twee minuten is het kleinste getal waarvoor dat
überhaupt waar kan zijn.

**Waarom dit op een schema mag draaien terwijl de instellingensweep dat niet
mag.** Eén commando en één antwoord per gemonitorde node per dag, tegenover
negentien en negentien. Dat is ruwweg een vijfde van één pollronde, die deze node
sowieso al om de vijftien minuten betaalt.

`wifi clock` leest alles terug: de klok van deze node, wanneer de site ze het
laatst gezet heeft, de vier tellers (gezet / al juist / geweigerd-achteruit /
geweigerd-buiten-venster), en de samenvatting van de laatste ronde — gevraagd,
geantwoord, gesynchroniseerd, voorlopend, en de grootste geziene afwijking.

Het is **met opzet alleen-lezen**. Er is geen manier om daar een tijd in te
typen, want de hele bedoeling van de functie is dat de tijd komt van een machine
die een reden heeft om te weten hoe laat het is. Iemand aan een seriële kabel
heeft die niet, en deze node evenmin.

### 4.13 De advert-cache

Elke advert die deze node hoort, werkt een kleine cache bij: sleutel, naam, type,
wanneer laatst gehoord, en coördinaten wanneer de advert die meedraagt.

| Constante | Waarde |
|---|---|
| `ADV_CACHE_MAX` | 48 records |
| `ADV_NAME_MAX` | 24 tekens |
| `ADV_WRITE_DELAY` | 120 s stilte voor er geschreven wordt |
| Bestand | `/adverts.dat`, magic `AVS1` |

Ze bestaat omdat zonder haar een herstart de monitorlijst en de gehoorde lijst
achterlaat met kale hexsleutels tot aan de volgende advert, en die kunnen uren
uit elkaar liggen.

De hook kopieert enkel naar RAM. **Het bestand wordt lui weggeschreven vanuit
`msnet_loop()`, nooit vanuit de hook**: adverts komen in bursts binnen op een
drukke mesh, en SPIFFS slijt. Eén schrijfactie zodra de burst gaan liggen is, niet
één schrijfactie per advert.

Namen die op `ADV_NAME_MAX` afgeknot zijn, zijn de reden waarom `jsonEsc()`
UTF-8-veilig moet zijn: een naam waarvan de laatste byte midden in een teken van
twee bytes belandt, is al een half teken voor hij de publisher bereikt.

<a id="the-three-safety-nets"></a>
### 4.14 De vangnetten

`MeshManagerNet` is maatwerkcode die draait op een node die niet mag sterven. Ze
gaat er daarom van uit dat zij zelf het defecte onderdeel zou kunnen zijn. Er
zijn **vier** netten, en elk dekt een storing af die de andere niet kunnen zien.

Een boot-teller staat in `/msboot` op SPIFFS. `checkSafeMode()` leest hem, beslist,
en schrijft hem meteen verhoogd terug. Na `STABLE_UPTIME_MS` (5 minuten)
ononderbroken draaien wordt de boot als geslaagd verklaard en gaat de teller terug
op nul.

| # | Net | Trigger | Resultaat |
|---|---|---|---|
| 1 | **Safe mode** | 3 boots zonder 5 minuten stabiliteit (`SAFE_MODE_BOOTS`) | Enkel AP + beheerpagina. Geen console, geen MQTT, geen monitoring, geen instellingensweep |
| 2 | **Module uit** | 6 boots (`DISABLE_BOOTS`) | Geen enkele regel van deze module start. Wat overblijft is een gewone MeshCore-repeater met zijn mesh-CLI en de standaard `start ota` |
| 3 | **Geen `halt()` bij radiofalen** | `radio_init()` faalt | De netwerkzijde start toch, zodat er vanaf de grond geherflasht kan worden |
| 4 | **Task watchdog** | `loop()` geblokkeerd gedurende `WDT_TIMEOUT_S` (30 s) | Panic, backtrace, herstart — waardoor een vastloper een gebeurtenis wordt die de drie netten hierboven wél kunnen zien |

**Waarom twee bootdrempels in plaats van één.** De fout zou in het safe-modepad
zelf kunnen zitten. Safe mode start nog steeds een AP en een webserver, dus het
kan nog steeds crashen. Niveau 6 verwijdert elke regel van deze module uit het
bootpad. De teller wordt ook gewist wanneer de module uitgeschakeld is, zodat de
volgende boot na een oplossing alles opnieuw probeert.

**Waarom het radiogeval telt.** De standaard `simple_repeater` roept `halt()` aan
wanneer de radio niet initialiseert, wat op een daknode een baksteen betekent. Met
`MESHMANAGER_NET`:

```cpp
if (!radio_ok) {
    msnet_begin(*fs, &the_mesh);
    while (1) { msnet_loop(); delay(5); }
}
```

Geen mesh — er is geen radio — maar WiFi, de beheerpagina en OTA staan wel
overeind.

**Waarom de watchdog niet overbodig is.** De drie netten hierboven reageren
allemaal op *herstarts*. Een zusternode faalde op een manier die er geen
produceert: na een flash antwoordde hij op geen enkele TCP-poort terwijl ping
afwisselend bleef werken. Dat is de handtekening van een geblokkeerde `loop()`.
WiFi en lwip leven in hun eigen FreeRTOS-taken en blijven pings beantwoorden
terwijl de applicatietaak stilstaat — geen crash, geen backtrace, geen herstart,
dus de boot-teller loopt nooit op en safe mode komt er nooit. Op een dak is dat een
dode node.

`panic=true` is bewust gekozen: de panic-handler drukt een backtrace af voor hij
herstart (het framework is gebouwd met `PANIC_PRINT_REBOOT`), zodat de volgende
persoon de diagnose krijgt die de zusternode nooit heeft opgeleverd.

**Waarom 30 s en niet de standaard van 5 s uit het framework.** Verschillende
zaken blokkeren deze taak legitiem veel langer dan vijf seconden, en een valse
herstartlus op een dak is erger dan de kwaal. De langste paal is een
MQTT-verbinding met een broker die als hostnaam opgegeven is — de DNS-wachttijd
van lwip plus de sockettime-out is ruwweg 15–20 s geblokkeerde `loop()` zonder dat
er iets mis is. SPIFFS-schrijfacties en een flash erase voegen er nog enkele aan
toe. Dertig seconden dekt dat ergste realistische geval ruim en haalt een
vastgelopen node nog steeds binnen het halve minuut terug. Merk op dat dit ook de
idle-task-watchdog die de core op 5 s installeert naar diezelfde 30 s versoepelt;
dat is de bedoeling, aangezien dezelfde operaties de idle-taak om dezelfde redenen
uithongeren.

**De watchdog gaat opzij tijdens een OTA.** Een upload schrijft en wist flash
vanuit de async-taak, en die stukken leggen de wereld langer stil dan om het even
welke normale operatie. Een watchdog-herstart halverwege een firmwareschrijfactie
is de ene herstart die dit nooit mag veroorzaken, dus meldt `wdtFeed()` zich voor
de duur af in plaats van te proberen een time-out te raden die dat dekt.

Dat opent een eigen gat, dat ook gedicht wordt: een afgebroken upload (browser
halverwege gesloten) bereikt `Update.end()` nooit, dus zou `Update.isRunning()`
eeuwig waar blijven en de node stilletjes zonder watchdog achterlaten — precies de
stille storing die dit hele geval moet voorkomen. Vandaar `WDT_OTA_MAX_MS`
(5 min): de grens aan hoe lang we bereid zijn te geloven dat een upload nog bezig
is.

`wdtBegin()` wordt **voor** de `_disabled`-return in `msnet_begin()` aangeroepen,
met opzet: een node die deze module uitgeschakeld heeft, is precies de node die
zichzelf nog uit een vastloper moet kunnen herstarten. En `wdtFeed()` is de eerste
instructie in `msnet_loop()`, voor elke vroege return, omdat het bereiken van die
regel het bewijs is dat de lus nog draait.

Bewijs de hele keten met `wifi wdt` (§4.9).

---

## 5. Bouwen en flashen

### 5.1 De aanpassingen toepassen

Deze repository is een **overlay** op upstream MeshCore, geen fork: er staat hier
geen `platformio.ini`, en de boom bevat alleen de bestanden die verschillen.

```bash
git clone https://github.com/meshcore-dev/MeshCore.git
cd MeshCore
git checkout companion-v1.17.0

# 1. de bestanden erover kopiëren
cp -r /path/to/MeshManager/firmware/src/*      src/
cp -r /path/to/MeshManager/firmware/examples/* examples/

# 2. en de repeater-hooks, aanpassingen bínnen upstreams eigen bestanden
git apply /path/to/MeshManager/firmware/repeater-hooks.patch
```

**Allebei, niet het een of het ander.** Het zijn geen twee schrijfwijzen van
hetzelfde, en stap 2 is de valkuil: `examples/simple_repeater/` levert hier
alleen `MeshManagerNet.{cpp,h}`, want `MyMesh.{cpp,h}` en `main.cpp` zijn
upstreambestanden die we enkel bewerken. Die bewerkingen zijn de aanroepen
achter `#ifdef MESHMANAGER_NET`, en ze zijn het enige dat de module aan de
repeater knoopt. Sla de patch over en `MeshManagerNet.cpp` compileert niet eens
— hij roept methodes op `MyMesh` aan die de patch toevoegt.
`repeater-hooks.patch` draagt ook de eigen `fillStatsJson()` van dat voorbeeld,
en daar wordt de stats-payload van de repeater gebouwd — een andere en rijkere
payload dan die van de companion, zie [`mqtt.md`](mqtt.md).

`meshmanager.patch` is dezelfde soort aanpassingen voor *beide* voorbeelden in
één bestand. Ook dat vervangt stap 1 niet: er zitten geen nieuwe bestanden in,
dus `MeshManagerNet.{cpp,h}`, `StatsPublisher.{cpp,h}` en de rest moeten nog
steeds gekopieerd worden.

Controleer na het bouwen of de module werkelijk in het image beland is:

```bash
python firmware/tools/verify_image.py \
  .pio/build/<jouw_env>/firmware.bin --env <jouw_env>
```

Die controle bestaat omdat de fout die ze vangt stil is: zonder
`-D MESHMANAGER_NET` of zonder de hooks compileert alles, gooit de linker de
module weg waar niemand naar verwijst, en rolt er een doodgewone
MeshCore-repeater uit die dat nergens zegt. De releaseworkflow draait hetzelfde
script.

### 5.2 De build configureren

Maak `platformio.local.ini` op basis van `platformio.local.ini.example`.

> **Dit bestand bevat uw WiFi-inloggegevens en adminwachtwoord. Het staat in
> gitignore. Commit het nooit.**

Een companion-omgeving heeft minimaal het volgende nodig:

```ini
[env:my_companion]
extends = Heltec_lora32_v3
build_flags =
  ${Heltec_lora32_v3.build_flags}
  -D DISPLAY_CLASS=SSD1306Display
  -D MAX_CONTACTS=260
  -D MAX_GROUP_CHANNELS=40
  -D TCP_PORT=5000
  -D WIFI_SSID='"YOUR_SSID"'
  -D WIFI_PWD='"YOUR_PASSWORD"'
  -D BLE_PIN_CODE=000000
build_src_filter = ${Heltec_lora32_v3.build_src_filter}
  +<helpers/esp32/*.cpp>
  +<../examples/companion_radio/*.cpp>
lib_deps =
  ${Heltec_lora32_v3.lib_deps}
  densaugeo/base64 @ ~1.4.0
  WebServer
  knolleary/PubSubClient @ ^2.8
```

`MAX_CONTACTS` is het waard om expliciet te zetten en het is het waard om **laag**
te zetten. Een contactslot kost ongeveer 316 bytes statisch RAM. Bij 350 bleef er
nog maar 22 kB heap over — te weinig voor lwip om zijn buffers te schikken,
waardoor de webserver halve antwoorden schreef en clients bleven hangen. 260 laat
ruwweg 50 kB over en houdt nog steeds 40 slots speling boven het aantal nodes dat
momenteel in de lucht is.

Een repeater-omgeving:

```ini
[env:my_repeater]
extends = heltec_v4_oled
build_flags =
  ${heltec_v4_oled.build_flags}
  -D DISPLAY_CLASS=SSD1306Display
  -D ADVERT_NAME='"My Repeater"'
  -D ADVERT_LAT=0.0
  -D ADVERT_LON=0.0
  -D ADMIN_PASSWORD='"CHANGE_ME"'
  -D MAX_NEIGHBOURS=50
  -D MESHMANAGER_NET=1
  -D WIFI_SSID='"YOUR_SSID"'
  -D WIFI_PWD='"YOUR_PASSWORD"'
build_src_filter = ${heltec_v4_oled.build_src_filter}
  +<helpers/ui/SSD1306Display.cpp>
  +<../examples/simple_repeater>
lib_deps =
  ${heltec_v4_oled.lib_deps}
  ${esp32_ota.lib_deps}
  bakercp/CRC32 @ ^2.0.0
  knolleary/PubSubClient @ ^2.8
```

Zet `DISABLE_WIFI_OTA` **niet**. Zolang `MeshManagerNet` draait, onderschept het
`start ota`; schakelt het zichzelf uit na herhaalde crashes, dan is de standaard
OTA de fallback (§4.14).

De ingecompileerde `WIFI_SSID` / `WIFI_PWD` zijn enkel standaardwaarden.
`MeshManagerNet` overschrijft ze vanuit `/msnet.json` zodra er iets via de pagina of
de CLI ingesteld wordt. Ze bestaan opdat de allereerste flash op het netwerk
opkomt.

### 5.3 Buildvlaggen

| Vlag | Standaard | Betekenis |
|---|---|---|
| `MESHMANAGER_NET` | niet gezet | De netwerkmodule van de repeater inschakelen |
| `WIFI_MAX_CLIENTS` | 4 | Gelijktijdige companion-clients (~2–3 kB RAM elk) |
| `TCP_PORT` | 5000 | TCP-poort voor de companion |
| `MAX_CONTACTS` | — | Contactslots; ~316 bytes elk. Zie hierboven |
| `MAX_GROUP_CHANNELS` | — | Kanaalslots; nodig opdat de kanaalfix zin heeft |
| `MAX_NEIGHBOURS` | — | Buurslots op een repeater; bepaalt de omvang van de `neighbors`-array in de payload |
| `WIFI_SSID` / `WIFI_PWD` | — | Ingebouwde netwerkstandaarden |
| `ADMIN_PASSWORD` | — | Het eigen adminwachtwoord van MeshCore voor de mesh-CLI |
| `BLE_PIN_CODE` | — | BLE-koppelcode voor de companion |
| `RADIO_FEM_RXGAIN_DEFAULT` | bord | Zet op 0 waar de FEM-versterker oversturing veroorzaakt bij een sterk signaal — een node die hoog staat met vrij zicht hoort er meer zonder |
| `WIFI_DEBUG_LOGGING` | 0 | Uitvoerige interfacelogging |
| `DISABLE_WIFI_OTA` | niet gezet | **Laat ongezet**; zie hierboven |

### 5.4 De companion-pagina opnieuw genereren (enkel companion)

Alleen als `page.html` gewijzigd werd:

```bash
python examples/companion_radio/gen_page.py
```

Controleer de uitvoer. Het script eindigt met 1 wanneer de gzipte pagina groter is
dan 5760 bytes — en tegen dan heeft het `StatsPage.h` al overschreven. Zie §3.

### 5.5 Bouwen en flashen

```bash
pip install platformio
python -m platformio run -e my_repeater -t upload --upload-port COM4
```

Een seriële upload schrijft enkel de app-partitie. Sleutels blijven behouden — zie
[de partitietabel](#46-waarom-een-ota-uw-sleutels-niet-verliest). Neem sowieso een
back-up.

| Doel | Eerste flash | Latere flashes |
|---|---|---|
| **Repeater** | USB/serieel | **OTA op `http://<node-ip>/update`**, achter de adminlogin. Of serieel |
| **Companion** | USB/serieel | **USB/serieel. Er is geen OTA** (§3) |

Voor de repeater is de volledige OTA-procedure:

1. `python -m platformio run -e my_repeater` — enkel bouwen.
2. Zoek het binaire bestand in `.pio/build/my_repeater/firmware.bin`.
3. Open `http://<node-ip>/update`, meld u aan, upload. De meegeleverde pagina
   berekent de MD5 voor u.
4. Of vanaf de commandoregel, **met de `Expect:`-header onderdrukt**:

```bash
curl -H "Expect:" -u admin:PASSWORD \
     -F "MD5=$(md5sum firmware.bin | cut -d' ' -f1)" \
     -F "file=@firmware.bin;filename=firmware.bin" \
     http://<node-ip>/update
```

   Zonder `-H "Expect:"` meldt curl status 100, herstart de node toch, en komt
   hij terug op de oude firmware. Zonder het `MD5`-veld antwoordt de handler
   `400 MD5 parameter missing`. Zie §4.5.
5. **Controleer `ver`, niet de herstart.**

Is de node onbereikbaar over IP: sluit aan op zijn eigen netwerk
`MeshCore-<node id>` (standaardwachtwoord `meshcore`) en gebruik dezelfde pagina,
of bereik de mesh-CLI over LoRa en gebruik `wifi on 30` om WiFi geforceerd omhoog
te brengen (§4.3).

### 5.6 Na het flashen

1. Volg de seriële log voor het toegekende IP, of maak verbinding met
   `MeshCore-<id>` als de node teruggevallen is op AP-modus.
2. Open `http://<node-ip>/`.
3. **Wijzig de standaardlogin onmiddellijk** — `admin` / `meshcore` op de
   repeater. Daarachter zitten zowel uw private sleutel als de firmware-upload.
4. Stel de MQTT-broker, prefix en interval in, en schakel publiceren in.
5. Op een monitorende node: voeg de te monitoren repeaters toe, en zorg dat de
   overkant **admin**rechten toegekend heeft als hun instellingen leesbaar moeten
   zijn (§4.11).
6. Bevestig dat er berichten binnenkomen in `/admin` op de server.

---

## Status

Gebouwd en getest op een Heltec V3 (ESP32-S3) als companion en een Heltec V4 als
repeater.

| | |
|---|---|
| Meerdere companions met gerichte antwoorden | werkt |
| Fix voor de kanaalteller | werkt |
| Beheerpagina, chatclient en `/stats.json` op de companion | werkt |
| MQTT-publicatie van statistieken | werkt |
| `MeshManagerNet` op de repeater | werkt |
| Andere repeaters monitoren over LoRa | werkt |
| Commando's vanaf de site over MQTT (`cmd`-topic, 1.8.0) | geschreven en nagelezen, **nog op geen enkele node geflasht** |
| Instellingensweep van een gemonitorde repeater over LoRa (1.9.0) | geschreven en nagelezen, **nog op geen enkele node geflasht** — vereist 1.9.0 op de monitorende node en adminrechten op de gemonitorde |
| Kloksynchronisatie (1.10.0) en `cmd:region` (1.11.0) | geschreven en nagelezen, **nog op geen enkele node geflasht** |
| Doorsturen over **HTTP** | opgegeven — deed de node crashen; zie [`architecture.md`](architecture.md) |
| Raw packets doorsturen over MQTT | werkt |
| Volledige webclient op de companion-node | werkt |
