# De TCP-proxy

*[English](../proxy.md)*

`proxy/mc-proxy/` — een fan-outproxy waarmee meerdere clients één MeshCore
WiFi-node kunnen delen. Het is een Home Assistant-add-on, maar het programma
eronder is één Python-bestand zonder afhankelijkheden dat overal draait.

**Hij draagt geen statistieken en praat nooit met een MeshStats-server.** Het is
een transporthulpje, en het staat alleen in deze repo omdat het probleem dat het
oplost pal vóór al het andere hier ligt.

---

## Inhoud

- [Het probleem](#het-probleem)
- [Heb je hem nodig?](#heb-je-hem-nodig)
- [Installatie](#installatie)
- [Opties](#opties)
- [Omgevingsvariabelen](#omgevingsvariabelen)
- [Statuspagina](#statuspagina)
- [Hoe hij doorstuurt](#hoe-hij-doorstuurt)
- [De nodeverbinding in leven houden](#de-nodeverbinding-in-leven-houden)
- [Clientbeheer](#clientbeheer)
- [Toegangscontrole](#toegangscontrole)
- [Wat hij niet oplost](#wat-hij-niet-oplost)
- [Problemen oplossen](#problemen-oplossen)
- [Versiegeschiedenis in het kort](#versiegeschiedenis-in-het-kort)

---

## Het probleem

Standaard MeshCore companion-firmware accepteert **één TCP-client tegelijk**. Eén
client in totaal — niet één per app. De `meshcore`-integratie van Home Assistant,
de MeshCore-telefoonapp en `meshcore-cli` kunnen dus niet naast elkaar bestaan op
dezelfde node; wie het laatst verbindt neemt de plek, en de rest vecht erom.

De proxy houdt die ene verbinding zelf vast en deelt hem:

```
                        +---------------------------+
   WiFi-node <--------> |        mc-proxy           | <---> meshcore-HA-integratie
   (één TCP-plek)       |  houdt de ene socket vast | <---> MeshCore-app
                        |  waaiert uit naar N       | <---> meshcore-cli
                        +---------------------------+
                                    |
                                    +--> statuspagina (JSON)
```

---

## Heb je hem nodig?

| Situatie | Antwoord |
|---|---|
| Je kunt de MeshStats-firmware flashen | **Nee.** `SerialWifiInterface` bedient vier clients tegelijk op de node zelf, met gerichte antwoordroutering. Zie [`firmware.md`](firmware.md#1-multiple-companions-on-one-node) |
| Je kunt geen firmware flashen en wilt meer dan één client | **Ja** |
| Je hebt precies één client en houdt dat zo | Nee |

De twee oplossingen verschillen in meer dan plaats. De firmware weet welke client
wat vroeg en stuurt antwoorden naar de vrager; de proxy zendt elk nodeframe naar
elke client en laat de clients het uitzoeken. Allebei werkt, om redenen die in
[`protocol.md` §2.3](protocol.md#23-the-single-client-problem) staan.

---

## Installatie

### Als Home Assistant-add-on

Voeg `https://github.com/DinXke/MeshCore-Proxy` toe als add-onrepository, of wijs
Home Assistant naar de map `proxy/` in deze repo. Daarna:

1. Zet **`node_host`** op het adres van je MeshCore WiFi-node en start de add-on.
   Het is de enige verplichte optie; `run.sh` stopt met een fatale logregel als
   hij leeg is.
2. Wijs de **`meshcore`-integratie** naar de proxy op TCP-poort `5000` op de
   Home Assistant-host zelf.
3. Wijs de **MeshCore-app** naar de Home Assistant-machine, poort `5000`.
4. Zorg dat er **niets meer rechtstreeks met de node verbindt**. De node heeft nog
   steeds maar één plek, en een rechtstreekse client vecht er met de proxy om.

`build.yaml` bouwt op de Home Assistant-basisimages voor `amd64`, `aarch64` en
`armv7`. De container installeert niets dan `python3`.

### Standalone

`mc_proxy.py` is gewoon Python 3 met alleen imports uit de standaardbibliotheek.
Zet de onderstaande omgevingsvariabelen en draai het:

```bash
MCP_NODE_HOST=<node-adres> python3 proxy/mc-proxy/mc_proxy.py
```

De add-onlaag is `run.sh`, die niets anders doet dan add-onopties vertalen naar
die omgevingsvariabelen.

---

## Opties

Zichtbaar in de add-on-UI, uit `config.yaml`:

| Optie | Standaard | Betekenis |
|---|---|---|
| `node_host` | — (**verplicht**) | Adres van de MeshCore WiFi-node |
| `node_port` | `5000` | TCP-poort van de node |
| `allowed_ips` | `[]` | Toegangslijst van clientadressen of CIDR's. Leeg betekent iedereen. **Vul hem in.** |
| `max_clients` | `8` | Maximaal aantal gelijktijdige clients (schema laat 1–64 toe) |
| `log_level` | `info` | `debug` / `info` / `warning` |

Poorten die de add-on publiceert: `5000/tcp` voor clients, `5001/tcp` voor de
statuspagina. De poort wijzigen in het **Netwerk**-paneel van de add-on verlegt
alleen de hostkant; binnen de container luistert de proxy altijd op 5000.

De UI toont een bewuste deelverzameling. Al het andere houdt zijn ingebouwde
standaard en is vanuit Home Assistant niet bereikbaar.

---

## Omgevingsvariabelen

De volledige set die het programma kent, uit de moduledocstring en de constanten
bovenaan `mc_proxy.py`:

| Variabele | Standaard | Betekenis |
|---|---|---|
| `MCP_NODE_HOST` | — (verplicht) | Adres van de node |
| `MCP_NODE_PORT` | `5000` | TCP-poort van de node |
| `MCP_LISTEN_HOST` | `0.0.0.0` | Interface om op te luisteren |
| `MCP_LISTEN_PORT` | `5000` | Clientpoort |
| `MCP_HEALTH_PORT` | `5001` | Poort van de statuspagina |
| `MCP_ALLOWED_IPS` | *(leeg)* | Adressen/CIDR's, gescheiden door komma's of puntkomma's |
| `MCP_MAX_CLIENTS` | `32` | Maximaal aantal gelijktijdige clients (de add-on zet 8) |
| `MCP_RECONNECT_S` | `1` | Wachttijd tussen herverbindingspogingen naar de node |
| `MCP_MAX_RECONNECT_S` | `15` | Bovengrens voor die wachttijd |
| `MCP_KEEPALIVE_S` | `30` | Keepalive-interval richting de node |
| `MCP_HANDSHAKE_TIMEOUT_S` | `30` | Geduld met een handshake-antwoord (één herkansing halverwege) |
| `MCP_MAX_SILENT_ROUNDS` | `3` | Onbeantwoorde keepalives vóór de verbinding herbouwd wordt |
| `MCP_IDLE_EVICT_S` | `60` | Inactieve tijd waarna een clientplek hergebruikt mag worden |
| `MCP_NODE_DOWN_GRACE_S` | `60` | Hoelang de node weg mag zijn voordat clients losgekoppeld worden |
| `MCP_MIN_CMD_GAP_S` | `0.25` | Minimale tussentijd tussen twee commando's naar de node |
| `MCP_LOG_LEVEL` | `info` | `debug` / `info` / `warning` |

De ruime standaardwaarden zijn een correctie, vastgelegd in de code: op een
zwakke wifi-link maakt een proxy die snel loskoppelt en herverbindt het
**erger**, niet beter.

---

## Statuspagina

`http://<host>:5001/` geeft JSON terug. `health_server()` in `mc_proxy.py`.

| Veld | Betekenis |
|---|---|
| `node_host` | De node en poort erboven |
| `node_connected` | Of de socket naar boven openstaat |
| `node_answering` | Of de node *antwoordt*, en niet alleen TCP accepteert |
| `seconds_since_node_data` | Ouderdom van de laatste byte van de node |
| `silent_keepalive_rounds` | Opeenvolgende keepalives zonder antwoord |
| `clients` / `client_count` / `max_clients` | Verbonden clients |

Het paar dat je moet begrijpen: **`node_connected` waar met `node_answering`
onwaar** is precies de storing waarvoor deze proxy gebouwd is. Companion-firmware
kan in een toestand raken waarin hij nog TCP accepteert en nergens op antwoordt —
een gewone bereikbaarheidstest noemt dat gezond. De proxy breekt de socket af en
bouwt hem opnieuw op, wat de node meestal bijbrengt.

Het is een met de hand geschreven HTTP-antwoorder, geen framework: één
verzoekregel lezen, één JSON-antwoord schrijven, sluiten. Daarmee blijft de
afhankelijkhedenlijst van de add-on `python3` en verder niets.

---

## Hoe hij doorstuurt

De companion-TCP-link is geframed, in tegenstelling tot wat de vroegste versies
van deze proxy aannamen:

| Richting | Marker | Daarna |
|---|---|---|
| client → node | `0x3C` (`<`) | 16-bits lengte little-endian, payload |
| node → client | `0x3E` (`>`) | 16-bits lengte little-endian, payload |

Beide lussen bufferen, parsen complete frames en **hersynchroniseren** op alles
wat ze niet begrijpen — vooruitzoeken naar de volgende marker en de onbegrepen
bytes doorsturen, in plaats van de verbinding af te breken. Volledige
specificatie: [`protocol.md` §2.1](protocol.md#21-framing).

Twee uitzonderingen op eenvoudig doorsturen:

### `CMD_APP_START` wordt door de proxy beantwoord

De node beantwoordt `APP_START` **één keer per TCP-sessie**, en de proxy verbruikt
dat tijdens zijn eigen handshake. De aanmelding van elke client zou dus door de
node genegeerd worden, waardoor clients bleven hangen op "verbinden" of "kan
apparaatinfo niet ophalen". Dit was vóór 1.8.1 de grondoorzaak van bijna alle
verbindingsproblemen.

`dispatch()` bewaart het `SELF_INFO`-antwoord van de node (pakkettype `0x05`) en
`handle_client()` beantwoordt de `APP_START` van elke client uit die cache. Het
bewaarde frame overleeft een herverbinding, zodat clients zich ook kunnen
aanmelden terwijl de nodeverbinding hapert.

### Commando's worden gespreid

`_exchange()` dwingt `MIN_CMD_GAP_S` af (standaard 0,25 s) tussen twee commando's
naar de node. Rechtstreeks op de node paste er maar één client; via de proxy
komen ze allemaal tegelijk binnen, en meerdere clients samen kunnen een klein
radio-apparaat overspoelen. De spreiding geeft de node dezelfde rustige stroom
als bij één enkele client.

### De rest wordt uitgezonden

Sinds 1.8.0 gaat **elk frame van de node naar elke client**, en matchen clients
zelf welk antwoord bij hun eigen commando hoort — precies zoals ze zouden doen als
ze rechtstreeks verbonden waren. De eerdere routering "alleen naar de vrager" kon
een antwoord bij de verkeerde client bezorgen, of het helemaal verliezen, als er
meerdere clients actief waren.

De vergrendeling in `_exchange()` serialiseert daarom geen hele uitwisselingen
meer. Ze is kort en doet één ding: voorkomen dat frames van twee clients halverwege
door elkaar op de draad geschreven worden. Er wordt nergens op een antwoord
gewacht, dus een drukke client kan de lijn nooit blokkeren.

---

## De nodeverbinding in leven houden

`upstream_loop()` en `keepalive_loop()` realiseren samen vier gedragingen, elk
een antwoord op een waargenomen storing:

| Gedrag | Waarom |
|---|---|
| `APP_START` meteen bij het verbinden | Een node sluit een verbinding die zich nooit aanmeldt. Een "stille" proxy faalt |
| `GET_DEVICE_TIME`-keepalive elke 30 s | Een node sluit een verbinding die stil blijft |
| Handshake-waakhond: één herkansing halverwege, herbouw na de volle timeout | Een node die TCP accepteert en niets antwoordt is vastgelopen firmware; een verse sessie brengt hem meestal bij |
| Herverbindingsuitstel oplopend tot `MAX_RECONNECT_S` | Een node met een vastgelopen netwerkstack moet niet elke seconde bestookt worden |

De waakhond draagt een litteken dat het benoemen waard is. Elke
`_handshake_watchdog()` bewaakt **precies de verbinding waarvoor hij gestart is**
(`self.up_writer is not writer` breekt hem anders af). Vóór 1.8.3 stapelden
waakhonden van eerdere pogingen zich op een trage link op en braken ze nieuwe,
gezonde verbindingen af — de lus "node antwoordt / verbinding verloren" om de paar
seconden in de logs.

Antwoorden op de eigen handshake- en keepaliveframes van de proxy gaan via
`_send_internal()`, die niet op het antwoord wacht en het ook niet opeist; het
antwoord wordt gewoon uitgezonden als elk ander nodeframe.

---

## Clientbeheer

| Situatie | Gedrag |
|---|---|
| Een nieuwe client komt binnen met vrije plekken | Toegelaten, geregistreerd met adres en laatste zendmoment |
| Een nieuwe client komt binnen met alle plekken bezet | Een **inactieve** sessie (niets gestuurd gedurende `IDLE_EVICT_S`) wordt ervoor vervangen. Is elke sessie actief, dan wordt de nieuwkomer geweigerd met een logregel |
| Een client stuurde niets gedurende `IDLE_EVICT_S × 3` | Opgeruimd tijdens de keepaliveronde |
| Schrijven naar een client mislukt | Die client valt uit de uitzendverzameling |
| De nodeverbinding valt weg | Clients **blijven verbonden** zolang de node binnen `NODE_DOWN_GRACE_S` blijft; ze zien even geen data en gaan daarna verder |
| De node is langer weg dan de respijtperiode | Alle clients worden losgekoppeld, met de reden in de log. Ze verbinden zelf opnieuw |

Die respijtperiode is het punt. Op een haperende link veroorzaakt clients bij elke
onderbreking loskoppelen een herverbindingsstorm die erger is dan de
onderbreking.

---

## Toegangscontrole

`allowed_ips` is een lijst adressen of CIDR's. Een ongeldige ingang wordt **luid
geweigerd bij het starten** — `parse_allowed()` logt de betreffende waarde en
stopt, in plaats van stilletjes met een half geparste lijst door te draaien.

`ALWAYS_ALLOWED` dekt localhost en de default gateway van de container, gelezen
uit `/proc/net/route`. Verbindingen vanaf de Home Assistant-host komen via de
Docker-poortmapping binnen met het interne gatewayadres als bron, dus zonder dit
zouden ze door de toegangslijst van hun eigen beheerder geblokkeerd worden.

**Het MeshCore-TCP-protocol kent geen authenticatie en geen versleuteling.** Wie
deze poort kan bereiken, bestuurt je radio: berichten sturen in jouw naam, je
verkeer lezen, instellingen wijzigen. Houd hem binnen een vertrouwd netwerk, zet
`allowed_ips`, en forward hem nooit naar het internet — gebruik een VPN. Zie
[`security.md`](security.md).

---

## Wat hij niet oplost

- **Berichtensynchronisatie is destructief in het companion-protocol.** Met
  meerdere verbonden clients wordt een chatbericht opgesnoept door wie het eerst
  synchroniseert. Het verschijnt in één client, niet in alle. Telemetrie, beheer
  en statistieken hebben er geen last van — en daarom is dit voor de doelen van
  MeshStats aanvaardbaar en voor een chat-eerst-opstelling niet.
- **De node heeft nog steeds één plek.** De proxy bezet hem. Elke client die
  rechtstreeks met de node verbindt vecht er met de proxy om, en allebei
  verliezen.
- **Geen statistieken, geen MQTT, geen MeshStats-server.** Alleen transport.

---

## Problemen oplossen

| Symptoom | Waar te kijken |
|---|---|
| Clients blijven hangen op "verbinden" / "kan apparaatinfo niet ophalen" | Statuspagina: is `node_answering` waar? Werd de `self_info` nooit opgevangen, dan kan de proxy client-handshakes niet beantwoorden |
| Log wisselt tussen "node antwoordt" en "verbinding verloren" | De waakhondbug van vóór 1.8.3. Bijwerken |
| Een client wordt geweigerd | `max_clients` bereikt met elke sessie actief. Verhoog hem, of zoek de vastzittende client in `clients` |
| Er verbindt helemaal niets | `allowed_ips` — controleer het bronadres waarmee de verbinding werkelijk binnenkomt, niet het adres dat je verwacht |
| De node herstart en clients hangen | Verwacht tot `NODE_DOWN_GRACE_S`; daarna worden clients losgekoppeld en verbinden ze opnieuw |
| Chatberichten verschijnen maar in één client | Werkt zoals bedoeld. Zie hierboven |

`log_level: debug` voegt detail per frame toe, inclusief `APP_START`-antwoorden
uit de cache en hersynchronisaties.

---

## Versiegeschiedenis in het kort

Volledig detail in `proxy/mc-proxy/CHANGELOG.md`. De kantelpunten:

| Versie | Wijziging |
|---|---|
| 1.0.0 | Eerste release: fan-out met automatische herverbinding |
| 1.1.x | Toegangslijst, clientlimiet, host/gateway altijd toegelaten |
| 1.2.0–1.3.0 | Slimme routering en geserialiseerde uitwisselingen — allebei later teruggedraaid |
| 1.4.0 | **Echte frameparsing.** Het transport is toch geframed; eerdere versies routeerden bijna alles verkeerd |
| 1.5.0 | Eigen handshake en keepalive richting de node |
| 1.6.0 | Zelfherstel tegen een vastgelopen node |
| 1.7.0 | Statuspagina op 5001 |
| 1.8.0 | **Alles uitzenden**; routering naar de vrager verwijderd |
| 1.8.1 | **De proxy beantwoordt `APP_START` zelf** — grondoorzaak van bijna alle verbindingsproblemen |
| 1.8.2 | Geduld op zwakke links: langere timeouts, bewaarde `self_info`, respijtperiode |
| 1.8.3 | Waakhonden bewaken alleen hun eigen verbinding |
| 1.8.4 | Commando's spreiden |

Het patroon is als geheel het lezen waard: drie van deze releases maken
slimmigheid uit een eerdere ongedaan. Routeren naar de vrager, uitwisselingen
serialiseren en agressief herverbinden klonken alle drie correct en maakten het
in het veld alle drie erger.

---

## Zie ook

| | |
|---|---|
| Het frameformaat dat hij parseert | [`protocol.md` §2](protocol.md#2-the-companion-protocol-tcp-and-serial) |
| Het firmware-alternatief | [`firmware.md`](firmware.md#1-multiple-companions-on-one-node) |
| Waarom een open TCP-poort ertoe doet | [`security.md`](security.md) |
| Hem naast de site installeren | [`deployment.md`](deployment.md#home-assistant-components) |
| Het andere Home Assistant-onderdeel | [`homeassistant.md`](homeassistant.md) |
