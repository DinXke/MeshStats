# MeshCore-protocollen

*[English](../protocol.md)*

Hier worden twee verschillende protocollen beschreven. Ze hebben niets met
elkaar te maken en zijn makkelijk te verwarren:

| | Waar het draait | Wat het draagt |
|---|---|---|
| [Over-the-air pakketformaat](#1-het-over-the-air-pakketformaat) | LoRa-radio, node naar node | ruwe LoRa-frames |
| [Companion TCP/serieel-protocol](#2-het-companion-protocol-tcp-en-serieel) | node naar app | TCP, USB-serieel, BLE |

Beide zijn gereconstrueerd door de broncode van de MeshCore-firmware te lezen.
Geen van beide is upstream gedocumenteerd. Alles hieronder is voorzien van een
verwijzing naar het bestand en de functie waar het vandaan komt, zodat je het
opnieuw kunt nakijken tegen je eigen firmwareversie.

**Referentieversie.** Alle regelverwijzingen gelden voor de MeshCore-werkboom in
`C:\Users\Public\MeshCore` (companion-firmware v1.17.0-lijn). Het wire-formaat is
stabiel over de hele v1.1x-reeks, maar de companion-commandonummers zijn dat
niet — zie [Versiedrift](#versiedrift).

---

# 1. Het over-the-air pakketformaat

Referentiebron:

| Onderwerp | Bestand |
|---|---|
| Een ontvangen frame parsen | `src/Dispatcher.cpp`, `Dispatcher::tryParsePacket()` |
| Een frame serialiseren voor TX | `src/Dispatcher.cpp`, `Dispatcher::checkSend()` |
| Blob-vorm (zelfde layout) | `src/Packet.cpp`, `Packet::writeTo()` / `readFrom()` |
| Veld-accessors, constanten | `src/Packet.h` |
| Grootteconstanten | `src/MeshCore.h` |
| Payload-semantiek per type | `src/Mesh.cpp`, `Mesh::onRecvPacket()` |
| Advert-`app_data` | `src/helpers/AdvertDataHelpers.{h,cpp}` |

## 1.1 Frame-layout

```
+--------+------------------+----------+---------------+----------------------+
| header | transport codes  | path_len | path          | payload              |
| 1 byte | 0 or 4 bytes     | 1 byte   | 0..64 bytes   | 0..184 bytes         |
+--------+------------------+----------+---------------+----------------------+
```

Op deze laag is er geen preamble, geen magic number, geen lengteprefix en geen
CRC. De LoRa-PHY levert al een lengte en een CRC, dus een MeshCore-frame begint
meteen met de headerbyte. `payload` is simpelweg "alles wat overblijft" —
`tryParsePacket()` berekent `payload_len = len - i` nadat het vaste deel is
verwerkt.

Het maximale on-air frame is `MAX_TRANS_UNIT` = **255** bytes.

### Wat een node accepteert, en wat hij toch doorspiegelt

Een frame van de radio is nog geen geldig frame. `Dispatcher::checkRecv()` roept
**eerst** de raw-logging-hook aan (`src/Dispatcher.cpp` regel 199) en pas daarna
`tryParsePacket()` (regel 205), waarbij het pakket weer wordt vrijgegeven als dat
mislukt. MeshManager ontvangt zijn pakketten via precies die hook
(`MyMesh::logRxRaw()` → `meshmanager_on_raw_packet()`), dus **de ruwe MQTT-feed
bevat frames die geen enkele MeshCore-node ooit heeft geaccepteerd** — ruis die
de PHY-CRC heeft overleefd, en frames van protocolvarianten die deze firmware
weigert.

Alles wat die bytes leest, moet dezelfde toelatingsregels toepassen, anders
presenteert het een geweigerd frame als een feit over het mesh. Er zijn er vijf,
allemaal uit `tryParsePacket()` en `Packet::isValidPathLen()`:

| Regel | Bron |
|---|---|
| payload-versie moet `PAYLOAD_VER_1` (0) zijn | `Dispatcher.cpp` 153–156 |
| path-hashgrootte 4 (descriptorbits 6–7 = 3) is gereserveerd | `Dispatcher.cpp` 167–170, `Packet.cpp` 16 |
| `count × size` ≤ `MAX_PATH_SIZE` (64) | `Packet.cpp` 17, `Dispatcher.cpp` 173 |
| het pad moet binnen de ontvangen lengte passen | `Dispatcher.cpp` 173 |
| payload ≤ `MAX_PACKET_PAYLOAD` (184) | `Dispatcher.cpp` 181–184 |

De eerste vier gaan allemaal over bytes die alles wat erna komt *positioneren*,
en dat is precies waarom een te toegeeflijke parser niet gewoon één veld
verkeerd leest: een verkeerd geïnterpreteerde descriptor verschuift het pad, de
payloadgrens en de adres-hashes tegelijk, en elk daarvan ziet er nog steeds uit
als een plausibele waarde. `server/app/packets.py` handhaaft alle vijf en meldt
welke regel gefaald heeft.

## 1.2 De headerbyte

Eén byte, drie bitvelden (`src/Packet.h`, regels 8–12):

```
 bit  7   6   5   4   3   2   1   0
     +---+---+---+---+---+---+---+---+
     |  ver  |   payload type    | route |
     +---+---+---+---+---+---+---+---+
       ^       ^                   ^
       |       |                   +-- bits 0-1, PH_ROUTE_MASK 0x03
       |       +---------------------- bits 2-5, PH_TYPE_SHIFT 2, PH_TYPE_MASK 0x0F
       +------------------------------ bits 6-7, PH_VER_SHIFT 6, PH_VER_MASK 0x03
```

`header == 0xFF` is geen geldige wire-waarde. Het is een in-memory sentinel met
de betekenis "verstuur dit pakket niet opnieuw" (`Packet::markDoNotRetransmit()`),
gezet wanneer een node vaststelt dat een pakket aan hemzelf gericht was.

### Route-type (bits 0–1)

| Waarde | Naam | Betekenis |
|---|---|---|
| `0x00` | `ROUTE_TYPE_TRANSPORT_FLOOD` | Flood, **met** 4 bytes transportcodes |
| `0x01` | `ROUTE_TYPE_FLOOD` | Flood; elke forwarder voegt zijn hash toe aan `path` |
| `0x02` | `ROUTE_TYPE_DIRECT` | Source-routed; `path` is de resterende hoplijst |
| `0x03` | `ROUTE_TYPE_TRANSPORT_DIRECT` | Direct, **met** 4 bytes transportcodes |

Het transportcodeveld is aanwezig dan en slechts dan als het route-type `0x00` of
`0x03` is (`Packet::hasTransportCodes()`). Dit is het enige veld met variabele
aanwezigheid in het vaste deel van het frame, en het is veruit de meest
voorkomende manier om een MeshCore-pakket verkeerd te parsen: een naïeve parser
die `path_len` altijd op offset 1 leest, zit bij transport-scoped pakketten vier
bytes uit de pas.

### Payload-type (bits 2–5)

| Waarde | Naam | Payload begint met | Opmerkingen |
|---|---|---|---|
| `0x00` | `PAYLOAD_TYPE_REQ` | dest-hash, src-hash, MAC | versleuteld: timestamp + blob |
| `0x01` | `PAYLOAD_TYPE_RESPONSE` | dest-hash, src-hash, MAC | antwoord op REQ / ANON_REQ |
| `0x02` | `PAYLOAD_TYPE_TXT_MSG` | dest-hash, src-hash, MAC | versleuteld: timestamp + tekst |
| `0x03` | `PAYLOAD_TYPE_ACK` | 4-byte CRC | platte tekst, niet versleuteld |
| `0x04` | `PAYLOAD_TYPE_ADVERT` | publieke sleutel, timestamp, handtekening | **ondertekend, niet versleuteld** |
| `0x05` | `PAYLOAD_TYPE_GRP_TXT` | kanaalhash, MAC | groepstekst, niet-geverifieerde afzender |
| `0x06` | `PAYLOAD_TYPE_GRP_DATA` | kanaalhash, MAC | groepsdatagram |
| `0x07` | `PAYLOAD_TYPE_ANON_REQ` | dest-hash, efemere publieke sleutel, MAC | |
| `0x08` | `PAYLOAD_TYPE_PATH` | dest-hash, src-hash, MAC | versleuteld: een pad + extra |
| `0x09` | `PAYLOAD_TYPE_TRACE` | tag, authcode, flags | verzamelt SNR per hop |
| `0x0A` | `PAYLOAD_TYPE_MULTIPART` | 1 packing-byte | één uit een reeks |
| `0x0B` | `PAYLOAD_TYPE_CONTROL` | 1 flagsbyte | control/discovery |
| `0x0C`–`0x0E` | — | — | niet toegewezen |
| `0x0F` | `PAYLOAD_TYPE_RAW_CUSTOM` | applicatiegedefinieerd | eigen versleuteling/formaat |

Onbekende payload-types worden weggegooid en **niet** flood-doorgestuurd
(default-tak van `Mesh::onRecvPacket()`, `src/Mesh.cpp` 326–329). Een nieuw
payload-type propageert dus niet als *flood* door een mesh van oudere nodes.

#### De structurele poort die aan die switch voorafgaat

De zin hierboven klopt en is niet het hele verhaal, en dat verschil is van belang
voor alles wat redeneert over wat een node zal doorsturen.

Om de switch op payload-type (`src/Mesh.cpp` regel 116) überhaupt te bereiken,
moet een pakket **ofwel flood-routed zijn, ofwel direct-routed met
`getPathHashCount() == 0`**. Elk direct pakket met nog resterende hops wordt
volledig afgehandeld door het forwarding-blok op `src/Mesh.cpp` 78–110 en bereikt
de switch **nooit**.

Een direct multi-hop pakket van *eender welk* payload-type — inclusief een
onbekend type, een `CONTROL` en een `RAW_CUSTOM` — wordt dus doorgestuurd door
`src/Mesh.cpp` 89–107 zonder dat het type ooit wordt bekeken.
Payload-typesemantiek is een eigenschap van pakketten die zijn aangekomen, niet
van pakketten onderweg. Twee beweringen verderop in dit document steunen op deze
regel; zie [CONTROL](#control-0x0b) en [RAW_CUSTOM](#raw_custom-0x0f).

### Payload-versie (bits 6–7)

| Waarde | Naam | Status |
|---|---|---|
| `0x00` | `PAYLOAD_VER_1` | De enige versie die in gebruik is. 1-byte hashes, 2-byte MAC. |
| `0x01`–`0x03` | `PAYLOAD_VER_2..4` | Gereserveerd. Niets implementeert ze. |

`tryParsePacket()` verwerpt elk frame met `getPayloadVer() > PAYLOAD_VER_1` nog
voor het iets anders doet. De versie wordt als eerste gecontroleerd, dus een
toekomstig v2-frame kost een oude node één byte parsewerk en verder niets.

## 1.3 Transportcodes

Alleen aanwezig bij route-types `0x00` en `0x03`. Vier bytes: twee `uint16`'s,
little-endian, letterlijk gekopieerd met `memcpy` bij zowel parsen als
serialiseren (`Dispatcher.cpp` regels 158–163 en 313–316).

Ze worden gezet door de aanroeper via het `transport_codes`-argument van
`Mesh::sendFlood()` / `Mesh::sendZeroHop()` (`src/Mesh.cpp` regels 664–677 en
728–732). De kernbibliotheek draagt ze alleen maar; ze worden nooit
geïnspecteerd. De commentaarregel in `src/helpers/BaseSerialInterface.h`
beschrijft hun doel als regio-scoping.

**Alleen de originator zet ze.** Doorsturen voegt geen transportcode toe,
herschrijft er geen en strijkt er geen weg. `Mesh::routeRecvPacket()`
(`src/Mesh.cpp` regels 349–352) voegt de eigen hash van de forwarder toe aan
`path` en raakt niets anders aan, en `Dispatcher::checkSend()` (regels 313–316)
kopieert `transport_codes` byte voor byte terug op de wire zodra
`hasTransportCodes()` geldt. De codes op een frame horen dus bij wie het heeft
*uitgezonden*, hoeveel hops geleden dat ook was — een scope reist mee met het
pakket en wordt onderweg nooit opnieuw toegepast.

> **Niet geverifieerd:** de toewijzing van specifieke codewaarden aan specifieke
> regio's of scopes is nergens gedefinieerd in de kernbronnen die voor dit
> document zijn gelezen. Heb je die waarden nodig, lees dan de applicatielaag die
> ze zet, niet `src/`.

### Wat de applicatielaag ermee doet

Het lezen van die applicatielaag maakt duidelijk wat een ontvanger wel en niet
uit de twee codes kan afleiden. `MyMesh::sendFloodScoped()` in
`examples/simple_repeater` (`MyMesh.cpp` 1274–1283) vult ze in:

```c
uint16_t codes[2];
codes[0] = scope.calcTransportCode(pkt);
codes[1] = 0;  // REVISIT: set to 'home' Region, for sender/return region?
```

De companion heeft zijn eigen versie van dezelfde functie
(`examples/companion_radio/MyMesh.cpp` 502–510) met een andere signatuur — die
heeft geen `path_hash_size`-parameter en geeft zelf `_prefs.path_hash_mode + 1`
door.

| Code | Wat het is | Kan het een regio benoemen? |
|---|---|---|
| `codes[0]` | `TransportKey::calcTransportCode(pkt)` — berekend uit de 16-byte scope-sleutel **en het pakket** | Op zichzelf niet. Het verschilt per pakket. Maar het *is* reproduceerbaar voor iedereen die de regionaam kan raden — zie hieronder |
| `codes[1]` | Gereserveerd voor de thuisregio van de zender | In principe ja, in de praktijk niet: de firmware schrijft er een letterlijke nul. `filterRecvFloodPacket()` draagt een bijbehorende `REVISIT` over het terugleren ervan |

#### Hoe de code wordt afgeleid, en wat dat betekent voor een archief

Dit is het waard om uit te spellen, want de voor de hand liggende conclusie — "een
scoped pakket kan zonder de sleutels niet aan een regio worden toegeschreven" — is
**fout voor het standaardgeval**, en een eerdere versie van dit document beweerde
dat wel.

```
code = first 2 bytes of HMAC-SHA256(key, payload_type_byte || payload)
```

(`TransportKey::calcTransportCode()`, `src/helpers/TransportKeyStore.cpp` 4–18.)
Daaruit volgen rechtstreeks twee eigenschappen:

- **De code wordt uitsluitend berekend over `payload_type || payload`** — dezelfde
  invoer als de dedup-hash. Ze verandert dus niet van hop tot hop; ze is identiek
  bij elke hop van één pakket. Precies daarom kan `RegionMap::findMatch()`
  (`src/helpers/RegionMap.cpp` 190–205) de code voor elke bekende regio opnieuw
  berekenen op een *ontvangen* frame en vergelijken.
- **Waar de sleutel vandaan komt, bepaalt of een buitenstaander hetzelfde kan.**
  `RegionMap::getTransportKeysFor()` (`src/helpers/RegionMap.cpp` 173–188) splitst
  regio's in twee soorten:

| Regionaam | Sleutelbron | Herleidbaar door een waarnemer? |
|---|---|---|
| begint met `$` | de transport key store — een echt gedeeld geheim | **Nee.** Dit is het private geval |
| begint met `#`, **of heeft helemaal geen prefix** (de impliciete auto-hashtag-tak, regels 180–185) | `TransportKeyStore::getAutoKeyFor()` = **gewoon `SHA256(name)`** over de naam met `#`-prefix, zonder salt (`TransportKeyStore.cpp` 37–50, sleutel op 45–47) | **Ja** |

Voor elke regio die geen `$`-prefix heeft — en dat is de standaard, en wat
regionamen als `be` of `eu` opleveren — is de sleutel dus een publieke functie van
de naam. Elke waarnemer die de naam kan raden, kan de sleutel berekenen en dus
`codes[0]` herkennen. **Een archief van ruwe pakketten kan de regio van een scoped
pakket benoemen door een lijst kandidaat-regionamen af te toetsen**, zonder ook
maar enig geheim te bezitten.

De eerlijke samenvatting luidt daarom: de aanwezigheid van de codes vertelt je dat
een pakket scoped was; `codes[0]` benoemt de regio voor hashtag-regio's en
impliciete regio's als je een kandidatenlijst hebt, en benoemt niets voor private
regio's met `$`-prefix.

Voor de volledigheid: `codes[1]` is in elk huidig firmwarepad een letterlijke nul,
maar is niet nul *by design*. `RegionEntry.id` is een `uint16` die sequentieel
vanaf 1 wordt toegekend (`src/helpers/RegionMap.cpp` 45, 166), en de regiovlaggen
zijn `REGION_DENY_FLOOD` `0x01` en `REGION_DENY_DIRECT` `0x02`
(`src/helpers/RegionMap.h` 11–12). `MAX_REGION_ENTRIES` is 32, `MAX_TKS_ENTRIES`
is 16, en een transportsleutel is 16 bytes.

Eén waarde is bijzonder en is **helemaal geen** regio. `isShare()` in de repeater
leest de codes `{0, 0}` als "stuur naar nergens":

```c
static bool isShare(const mesh::Packet *packet) {
  if (packet->hasTransportCodes()) {
    return packet->transport_codes[0] == 0 && packet->transport_codes[1] == 0;
  }
  ...
```

Dat is de vorm van een advert die via de Share-functie van de app is
geïmporteerd in plaats van uit de lucht gehoord, en de repeater houdt zo'n advert
bewust uit zijn buurtabel: een zero-hop advert betekent normaal "deze node is
binnen bereik", en een gedeelde niet. Alles wat scoped verkeer classificeert, moet
het om dezelfde reden als een apart geval behandelen — zie `server/app/packets.py`,
dat op exact deze grondslag `unscoped` / `scoped` / `share` rapporteert.

`{0, 0}` is beschikbaar als marker omdat `calcTransportCode()` beide eindwaarden
reserveert (`src/helpers/TransportKeyStore.cpp` regels 12–16: een berekende code
van `0x0000` wordt opgehoogd naar 1 en `0xFFFF` verlaagd naar `0xFFFE`). Een echte
scope-sleutel kan dus nooit `codes[0] == 0` opleveren, en dat is wat nul
ondubbelzinnig maakt in plaats van louter onwaarschijnlijk.

### Waarom zoveel verkeer unscoped is

Dit is de vraag die het archief als eerste oproept, en het antwoord ligt in de
applicatielaag en niet in een of andere decoder. De ingestelde **standaardregio**
van een repeater bereikt maar twee soorten pakketten:

| Pakket | Scoped? | Bron |
|---|---|---|
| pakketten die de repeater zelf originator van is (eigen adverts, zelfgegenereerde floods) | ja, met `default_scope` | `examples/simple_repeater/MyMesh.cpp` 204, 1312, 1786 |
| antwoorden op een request die zelf scoped was | ja, met de regio van de *request* | `MyMesh::sendFloodReply()`, regel 642 |
| antwoorden op een unscoped request | **nee** — `sendFlood()` zonder codes | dezelfde functie, regels 648 en 651 |
| alles wat hij voor anderen doorstuurt | **helemaal geen wijziging** | `Mesh::routeRecvPacket()` |

De regio-instelling van één node zegt dus niets over het verkeer dat er doorheen
gaat. Een mesh waarin de meeste originators niet scopen, leest als overwegend
`unscoped`, hoeveel van zijn repeaters ook een regio ingesteld hebben, en dat is
een meting en geen parsefout.

Nog een kanttekening bij het woord. `unscoped` betekent "geen transportcodes op
de wire", en dat is de volle waarheid voor een FLOOD maar slechts de helft ervan
voor een DIRECT-pakket: een direct pakket wordt source-routed langs een expliciete
hoplijst, niet geflood, en de firmware vraagt nooit tot welke regio het behoort —
`MyMesh::onRecvPacket()` zet `recv_pkt_region = NULL` voor elke niet-flood-route
(`examples/simple_repeater/MyMesh.cpp` regel 794), en `allowPacketForward()`
raadpleegt de regio alleen bij floods (regel 662; de functie zelf begint op 655).
Lees `unscoped` op een DIRECT-rij als "niet van toepassing", niet als "vrij
rondzwervend".

`allowPacketForward()` doet nog twee dingen die bepalen wat een archief te zien
krijgt:

- **`flood_max_unscoped` geldt alleen voor gewone `ROUTE_TYPE_FLOOD`**
  (`examples/simple_repeater/MyMesh.cpp` 657–660). Een repeater kan unscoped
  floods dus een krapper hopbudget geven dan scoped floods, wat zich in een archief
  vertaalt naar unscoped verkeer dat eerder uitdooft — een configuratiekeuze, geen
  propagatie-anomalie.
- **Lusdetectie** via `isLooped()`, dat telt hoe vaak de eigen hash van deze node
  al in het pad voorkomt (`630–639`, toegepast op `666–679`), in de modi
  `LOOP_DETECT_OFF` / `MINIMAL` / `MODERATE` / strikt.

## 1.4 Het path-veld

`path_len` is één byte, maar het is *geen* byte-telling. Het verpakt twee getallen
(`src/Packet.h` regels 79–83):

```
 bit  7   6   5   4   3   2   1   0
     +-------+-----------------------+
     | sz-1  |      hash count       |
     +-------+-----------------------+
```

| Expressie | Betekenis |
|---|---|
| `getPathHashSize()` = `(path_len >> 6) + 1` | Bytes per hop-entry: 1, 2, 3 of 4 |
| `getPathHashCount()` = `path_len & 63` | Aantal hop-entries, 0–63 |
| `getPathByteLen()` = count × size | Werkelijk aantal bytes op de wire |

`path_len == 0x00` betekent dus "hashgrootte 1, nul hops" — het gebruikelijke
geval voor een net uitgezonden flood-pakket.

### Wie de hashgrootte bepaalt

Per pakket, door wie het als eerste verstuurde. `Mesh::sendFlood()` neemt een
`path_hash_size`-argument aan en stempelt het in de descriptor
(`setPathHashSizeAndCount(path_hash_size, 0)`, `src/Mesh.cpp` regels 649 en 678);
de repeater geeft `_prefs.path_hash_mode + 1` door, zijn eigen CLI-instelling
`hash_mode` (`src/helpers/CommonCLI.h` regel 68, gebruikt op
`examples/simple_repeater/MyMesh.cpp` 204, 1312, 1777). Forwarders behouden ze:
`routeRecvPacket()` schrijft zijn hash met `getPathHashSize()` bytes, en
antwoorden spiegelen de grootte van de request
(`sendFloodReply(..., packet->getPathHashSize())`).

Drie gevolgen die het waard zijn om te benoemen, omdat ze gemakkelijk omgekeerd
begrepen worden:

- Het is **geen** mesh-brede eigenschap en ook geen protocolversie-eigenschap. De
  groottes 1, 2 en 3 reizen naast elkaar door dezelfde lucht; in een steekproef van
  400 pakketten live verkeer zag MeshManager 312 × 1-byte, 76 × 2-byte en 9 ×
  3-byte.
- Het **is** afleesbaar uit het frame, dus er hoeft niets aangenomen te worden.
  MeshManager rapporteert het als `path_hash_size`.
- Het zegt niets over de **adres-hashes in de payload**. Die liggen vast op één
  byte via `PATH_HASH_SIZE` (`src/Mesh.cpp` 462, `src/Identity.h` 19–26) en via
  `PAYLOAD_VER_1`, waarvan de hele definitie luidt "1-byte src/dest-hashes, 2-byte
  MAC" (`src/Packet.h` 34). Een node die op `hash_mode 2` staat, zet hops van twee
  bytes in het pad *en adresseert zijn peers nog steeds met één byte*. Alleen een
  `PAYLOAD_VER_2`-frame zou dat veranderen, en `tryParsePacket()` verwerpt die —
  niets implementeert het.

Beperkingen, afgedwongen in `Packet::isValidPathLen()` en nogmaals in
`tryParsePacket()`:

- Bovenste bits `= 3` (hashgrootte 4) is **gereserveerd en wordt geweigerd**.
  `tryParsePacket()` weigert het frame; `isValidPathLen()` geeft false terug.
- `count × size` moet ≤ `MAX_PATH_SIZE` (**64**) zijn.
- Het pad moet binnen het ontvangen frame passen, anders wordt het pakket
  weggegooid als afgekapt.

Een hop-entry is een **prefix van de Ed25519 publieke sleutel van de forwarder**,
geen digest. `Identity::copyHashTo()` is een gewone `memcpy` van de eerste `n`
bytes van `pub_key` (`src/Identity.h` regels 19–26). De vergelijking is een
`memcmp` op diezelfde prefix. Een "hash" van 1 byte geeft dus 256 buckets, en
botsingen zijn verwacht en worden afgehandeld — zie `Mesh::searchPeersByHash()`,
dat gedocumenteerd staat als ondersteunend tot 4 gelijktijdige matches en
eenvoudigweg tegen elke kandidaat probeert te ontsleutelen.

### Hoe het pad evolueert

**Flood** (`Mesh::routeRecvPacket()`): elke forwarder voegt zijn eigen hash toe op
`path[count * size]` en verhoogt de teller, op voorwaarde dat
`(count + 1) * size <= MAX_PATH_SIZE`. Het pad groeit vanaf de bron naar buiten,
dus bij aankomst is het de route *terug*.

**Direct** (`Mesh::onRecvPacket()`, `Mesh::removeSelfFromPath()`): `path[0]` is de
volgende hop. Een node stuurt alleen door als `path[0]` overeenkomt met zijn eigen
sleutelprefix, en verwijdert die entry vervolgens door het hele pad één entry op te
schuiven en de teller te verlagen. Het pad krimpt naarmate het pakket reist.

### Wat een pad je wel en niet kan vertellen

Dit is van belang voor alles wat een route probeert te *tonen*, en MeshManager doet
precies dat op zijn live kaart, dus de beperking is het waard om onomwonden te
stellen.

Een hop-entry is een sleutelprefix, geen identifier. Met `PATH_HASH_SIZE` = 1 zijn
er **256** mogelijke waarden. Een mesh van een paar honderd nodes heeft dus
hopwaarden waarop meerdere nodes reageren — volgens de birthday bound wordt een
botsing bij 256 buckets waarschijnlijk rond de 20 nodes, en MeshManager volgt er al
meer dan 200. Ambiguïteit is het normale geval, geen datafout.

Gevolgen voor wie het pad leest:

| Kandidaten die op een hop matchen | Wat je mag besluiten |
|---|---|
| precies één | die node heeft het pakket doorgestuurd — zo zeker als dit protocol wordt |
| meerdere | een van hen heeft het doorgestuurd; **welke, is niet te achterhalen** |
| geen | een node waarvan je nooit een advert hebt gehoord, heeft het doorgestuurd |

De firmware werkt zelf ook zo: `Mesh::searchPeersByHash()` geeft tot 4 kandidaten
terug en probeert er simpelweg elk. Elke renderer die één "beste" kandidaat kiest,
verzint een zekerheid die het wire-formaat niet draagt. MeshManager lost elke
kandidaat op (`_resolve_hop()` in `server/app/routes_api.py`) en tekent
onopgeloste en ambigue hops als onderbroken gaten in plaats van als lijnen naar een
gok.

Nog twee beperkingen bij het lezen van een opgeslagen pad:

- **De richting hangt af van het route-type.** Bij flood groeide het pad achter het
  pakket aan, dus is het de route die het heeft afgelegd. Bij direct is `path` de
  route die *nog afgelegd moet worden* — de reeds gepasseerde hops zijn verwijderd.
- **Een pad benoemt de afzender niet.** Alleen ADVERT draagt een identiteit, dus
  voor elk ander payload-type is de oorsprong van een ontvangen pakket onbekend,
  hoe volledig het pad ook is.

**Trace is de uitzondering.** Bij `PAYLOAD_TYPE_TRACE` draagt `path` helemaal geen
hashes — elke hop voegt zijn gemeten SNR toe als signed byte:

```c
pkt->path[pkt->path_len++] = (int8_t) (pkt->getSNR()*4);   // Mesh.cpp:61
```

Merk op dat deze regel `path_len` als ruwe teller verhoogt en zo de
grootte/aantal-verpakking omzeilt. TRACE gedraagt zich daarom alleen correct bij
hashgrootte 1.

In de praktijk begrenst de code het wel degelijk: de verhoging op `src/Mesh.cpp` 61
staat binnen `if (pkt->path_len < MAX_PATH_SIZE)` (regel 43), dus `path_len` kan
nooit boven 63 uitkomen en bit 6 wordt langs dat pad nooit gezet. De waarschuwing
blijft staan als waarschuwing over de *codering*, niet als een actuele overflow.

## 1.5 Grootteconstanten

Uit `src/MeshCore.h` tenzij anders vermeld:

| Constante | Waarde | Betekenis |
|---|---|---|
| `MAX_TRANS_UNIT` | 255 | Grootste on-air frame |
| `MAX_PACKET_PAYLOAD` | 184 | Grootste `payload` |
| `MAX_PATH_SIZE` | 64 | Grootste `path` in bytes |
| `MAX_HASH_SIZE` | 8 | Lengte van de pakkethash (dedup-tabel) |
| `PATH_HASH_SIZE` | 1 | Standaard aantal bytes per hop-entry (V1) |
| `PUB_KEY_SIZE` | 32 | Ed25519 publieke sleutel |
| `PRV_KEY_SIZE` | 64 | Ed25519 private sleutel |
| `SEED_SIZE` | 32 | |
| `SIGNATURE_SIZE` | 64 | Ed25519-handtekening |
| `MAX_ADVERT_DATA_SIZE` | 32 | Grootste `app_data` in een advert |
| `CIPHER_KEY_SIZE` | 16 | AES-128 |
| `CIPHER_BLOCK_SIZE` | 16 | |
| `CIPHER_MAC_SIZE` | 2 | Afgekapte HMAC (V1) |
| `MAX_GROUP_DATA_LENGTH` | 165 | `MAX_PACKET_PAYLOAD - CIPHER_BLOCK_SIZE - 3` |
| `MAX_FRAME_SIZE` | 176 | Bovengrens companion-frame (`helpers/BaseSerialInterface.h`) |

Het gat tussen 255 en 184 is 71 bytes: 1 header + 1 path_len + tot 64 path + 4
transportcodes = 70, plus één byte speling.

### Afgeleide limieten — wat er werkelijk in een payload past

De constanten hierboven zijn de plafonds op het frame. Wat een *builder*
accepteert ligt lager, en elk type heeft zijn eigen rekenwerk. Een decoder die
aanneemt dat 184 het plaintextbudget is, overschat elk van deze waarden:

| Builder | Bewaking | Grootste plaintext | Bron |
|---|---|---|---|
| `createDatagram()` | `data_len + CIPHER_MAC_SIZE + 15 > 184` | **167** | `src/Mesh.cpp` 490 |
| `createAnonDatagram()` | `data_len + 1 + 32 + 15 > 184` | **136** | `src/Mesh.cpp` 514 |
| `createGroupDatagram()` | `data_len + 1 + 15 > 184` | **168** | `src/Mesh.cpp` 542 |
| gecombineerd pad in een PATH-retour | `MAX_COMBINED_PATH` = `184 - 2 - 16` | **166** | `src/Mesh.cpp` 440, afgedwongen op 452 |
| chattekst | `MAX_TEXT_LEN` = `10 * CIPHER_BLOCK_SIZE` | **160** | `src/helpers/BaseChatMesh.h` 8 |
| companion-kanaaldata | `MAX_CHANNEL_DATA_LENGTH` = `MAX_FRAME_SIZE - 9` | **167** | `examples/companion_radio/MyMesh.cpp` 109 |

Merk op dat de eigen bewaking van `createGroupDatagram()` (168) **ruimer** is dan
`MAX_GROUP_DATA_LENGTH` (165). De twee zijn het oneens, en de constante is de
conservatieve van de twee; beschouw geen van beide als de definitieve limiet
zonder na te gaan welk pad het pakket heeft opgebouwd.

### Twee onafhankelijke lengtegrenzen, aan elke kant

Bij ontvangst: `Dispatcher::checkRecv()` leest in `uint8_t raw[MAX_TRANS_UNIT+1]`
maar roept `recvRaw(raw, MAX_TRANS_UNIT)` aan (`src/Dispatcher.cpp` 196–197), dus
255 is een harde bovengrens op wat er überhaupt gelezen wordt.

Bij verzenden zijn er twee afzonderlijke weigeringen:

- `sendPacket()` weigert `payload_len > MAX_PACKET_PAYLOAD` of een ongeldige
  `path_len` nog voor het pakket in de wachtrij komt (`src/Dispatcher.cpp`
  372–374);
- `checkSend()` gooit het pakket alsnog weg als `len + payload_len >
  MAX_TRANS_UNIT` tijdens het serialiseren (`src/Dispatcher.cpp` 320–323) — het
  geval waarin een legale payload plus een lang pad samen niet meer passen.

En `Packet::writePath()` geeft 0 terug en schrijft niets wanneer
`count * size > MAX_PATH_SIZE` (`src/Packet.cpp` 20–30): een foute descriptor bij
TX kapt het frame stilzwijgend af in plaats van een fout te geven.

## 1.6 Payload-layouts per type

### Versleuteld peer-to-peer: REQ, RESPONSE, TXT_MSG, PATH (`0x00`, `0x01`, `0x02`, `0x08`)

```
+-----------+----------+--------+---------------------------+
| dest_hash | src_hash | MAC    | ciphertext                |
| 1 byte    | 1 byte   | 2 byte | multiple of 16 bytes      |
+-----------+----------+--------+---------------------------+
```

Uit `Mesh::onRecvPacket()` regels 133–140. Beide hashes zijn sleutelprefixen van
1 byte onder `PAYLOAD_VER_1`.

De MAC en de ciphertext worden geproduceerd door `Utils::encryptThenMAC()`
(`src/Utils.cpp` regels 135–155) en geverifieerd door `Utils::MACThenDecrypt()`:

- Cipher: **AES-128 in ECB-modus**, met nullen opgevuld tot een veelvoud van 16
  bytes. Er is geen IV en geen chaining. Identieke plaintextblokken onder dezelfde
  sleutel leveren identieke ciphertextblokken op.
- **De AES-sleutel is alleen de eerste 16 bytes van het 32-byte gedeelde geheim**
  (`setKey(shared_secret, CIPHER_KEY_SIZE)`, `src/Utils.cpp` 81 en 119). De MAC
  gebruikt daarentegen alle 32. Een herimplementatie die het volledige geheim aan
  AES voert, ontsleutelt niets, en de fout ziet eruit als een sleutelmismatch.
- MAC: **HMAC-SHA256 over de ciphertext**, gesleuteld met het volledige 32-byte
  gedeelde geheim, afgekapt tot de eerste `CIPHER_MAC_SIZE` = **2 bytes**.
- De volgorde is encrypt-then-MAC. Ontsleuteling wordt geweigerd tenzij de 2-byte
  MAC klopt, en `MACThenDecrypt()` verwerpt `src_len <= CIPHER_MAC_SIZE`
  regelrecht (`src/Utils.cpp` 158). Bij ontvangst is de lengtebewaking
  `i + CIPHER_MAC_SIZE >= payload_len` (`src/Mesh.cpp` 139).

Twee bytes MAC betekent een kans van 1 op 65536 dat een willekeurige vervalsing
per poging wordt aanvaard. Dat is een bewuste afweging rond zendtijd en geen
vergissing — maar het betekent wel dat de MAC een corruptiecontrole is die
toevallig ook gesleuteld is, en geen authenticatiegarantie. Echte
afzenderauthenticatie in MeshCore komt van de Ed25519-handtekening op adverts, niet
van deze MAC.

Het gedeelde geheim is ECDH op Curve25519, met de Ed25519 publieke sleutel
omgezet naar X25519 (`LocalIdentity::calcSharedSecret()`, `src/Identity.h` regels
70–81).

#### Ontsleutelde TXT_MSG-plaintext

```
+-----------+-------------------------------+---------------------------+
| timestamp | flags                         | text                      |
| uint32 LE | (attempt & 3) | (txt_type<<2) | to the end, zero-padded    |
+-----------+-------------------------------+---------------------------+
```

Byte 4 verpakt twee velden. De laagste twee bits zijn de retry-poging
(`src/helpers/BaseChatMesh.cpp` 427); de rest is het tekstsubtype, teruggelezen
als `data[4] >> 2` (regel 232).

| Waarde | Naam |
|---|---|
| 0 | `TXT_TYPE_PLAIN` |
| 1 | `TXT_TYPE_CLI_DATA` |
| 2 | `TXT_TYPE_SIGNED_PLAIN` |

(`src/helpers/TxtDataHelpers.h` 6–8.) Groepstekst weigert alles behalve 0
(`BaseChatMesh.cpp` 386–388), en pogingnummers boven 3 worden verborgen als een
extra afsluitende byte (`434–436`).

`TXT_TYPE_CLI_DATA` is degene waar MeshManager van afhangt: zo komt een CLI-antwoord
van een bewaakte repeater via de lucht terug — zie
[`firmware.md`](firmware.md).

#### Ontsleutelde PATH-plaintext

De plaintext van `PAYLOAD_TYPE_PATH` heeft zijn eigen structuur
(`Mesh::onRecvPacket()` regels 161–172):

```
+----------+-----------------+------------+------------------+
| path_len | path            | extra_type | extra            |
| 1 byte   | count*size      | 1 byte     | remainder        |
+----------+-----------------+------------+------------------+
```

`extra_type` gebruikt alleen de lage nibble (`data[k++] & 0x0F`); de hoge nibble
is gereserveerd. `extra` loopt door tot het einde van het ontsleutelde blok en
**kan met nullen opgevuld zijn**, omdat de AES-ECB-padding niet wordt verwijderd —
de ontvanger kan afsluitende nulpadding niet onderscheiden van afsluitende
nuldata. De lengte moet uit de eigen codering van `extra_type` komen.

> **`extra_type == 0x0F` betekent "geen extra".** De builderkant
> (`src/Mesh.cpp` 465–481) schrijft `extra_type = 0xFF` plus vier willekeurige
> bytes wanneer er niets bij te voegen valt, puur zodat de pakkethash uniek blijft
> (`476–477`). Omdat de lezer maskeert met `0x0F`, komt dat aan als `0x0F`. Een
> decoder die `0x0F` als een betekenisvol extra-type behandelt, leest **elke**
> path-retour zonder extra verkeerd, en de vier willekeurige bytes zien eruit als
> payload.

### ANON_REQ (`0x07`)

```
+-----------+-------------------+--------+---------------------------+
| dest_hash | sender pub key    | MAC    | ciphertext                |
| 1 byte    | 32 bytes          | 2 byte | multiple of 16 bytes      |
+-----------+-------------------+--------+---------------------------+
```

`Mesh::onRecvPacket()` regels 197–219. De zender stuurt zijn volledige publieke
sleutel mee, zodat een node zonder eerder contactrecord het gedeelde geheim toch
kan afleiden.

### Groep: GRP_TXT (`0x05`), GRP_DATA (`0x06`)

```
+--------------+--------+---------------------------+
| channel_hash | MAC    | ciphertext                |
| 1 byte       | 2 byte | multiple of 16 bytes      |
+--------------+--------+---------------------------+
```

`Mesh::onRecvPacket()` regels 225–247. De sleutel is de kanaal-PSK, dus iedereen
die de PSK bezit kan een bericht vervalsen — daarom noemt `Packet.h` deze
"(unverified)". Ontsleutelde GRP_TXT is `timestamp, "name: msg"`; ontsleutelde
GRP_DATA is `data_type (uint16), data_len, blob`.

Kanalen worden op hash opgezocht met tot 4 kandidaat-matches, met dezelfde
botsingsafhandeling als bij peers.

### ACK (`0x03`)

```
+----------+
| ack_crc  |
| 4 bytes  |
+----------+
```

`Mesh::onRecvPacket()` regels 117–128. Een kale 32-bitswaarde, little-endian, in
platte tekst. ACK's worden afgehandeld vóór de normale direct-routecontrole, zodat
een node een ACK "vroeg" kan opmerken — terwijl hij hem nog voor iemand anders aan
het doorsturen is (regels 78–87).

### ADVERT (`0x04`)

```
+---------------+-----------+-------------+---------------------+
| pub_key       | timestamp | signature   | app_data            |
| 32 bytes      | 4 bytes   | 64 bytes    | 0..32 bytes         |
+---------------+-----------+-------------+---------------------+
```

`Mesh::createAdvert()` regels 404–438, geparset op regels 252–291.

- `timestamp` is een `uint32` little-endian, UNIX-epochseconden, uit de RTC van de
  node.
- `signature` is Ed25519 over de aaneenschakeling
  `pub_key || timestamp || app_data` — dus over de eigen velden van het pakket
  *met uitzondering van* de handtekening zelf. Zowel de builder als de verifier
  stelt dat bericht identiek samen in een buffer van
  `PUB_KEY_SIZE + 4 + MAX_ADVERT_DATA_SIZE`.
- Een node die zijn eigen advert terugkrijgt, logt hem en gooit hem weg
  (`self_id.matches(id.pub_key)`).
- Een mislukte handtekeningcontrole gooit het pakket weg en **stopt de flood** —
  hij wordt nooit doorgestuurd.
- `app_data` die langer is dan `MAX_ADVERT_DATA_SIZE` wordt vóór de verificatie
  afgekapt tot 32 bytes, zodat een te grote advert zakt op zijn
  handtekeningcontrole in plaats van te overlopen.

Dit is het enige pakkettype in het protocol met echte afzenderauthenticatie, en
daarom is het de basis voor identiteit in het mesh.

### Advert-`app_data` — flaggestuurde codering

`src/helpers/AdvertDataHelpers.{h,cpp}`. Byte 0 is een flagsbyte; elk volgend veld
is alleen aanwezig als zijn flag gezet is, in een vaste volgorde.

```
 bit  7   6   5   4   3   2   1   0
     +---+---+---+---+---+---+---+---+
     |NAM|FT2|FT1|LAT|    type       |
     +---+---+---+---+---+---+---+---+
```

| Bit | Constante | Waarde | Voegt toe indien gezet |
|---|---|---|---|
| 0–3 | nodetype (lage nibble) | — | niets; het is het type zelf |
| 4 | `ADV_LATLON_MASK` | `0x10` | `int32` lat + `int32` lon, LE, 8 bytes |
| 5 | `ADV_FEAT1_MASK` | `0x20` | `uint16` extra1, LE, 2 bytes |
| 6 | `ADV_FEAT2_MASK` | `0x40` | `uint16` extra2, LE, 2 bytes |
| 7 | `ADV_NAME_MASK` | `0x80` | UTF-8-naam, **de rest van `app_data`** |

Nodetypes (lage nibble, `getType()` = `_flags & 0x0F`):

| Waarde | Constante |
|---|---|
| 0 | `ADV_TYPE_NONE` |
| 1 | `ADV_TYPE_CHAT` |
| 2 | `ADV_TYPE_REPEATER` |
| 3 | `ADV_TYPE_ROOM` |
| 4 | `ADV_TYPE_SENSOR` |
| 5–15 | gereserveerd |

De veldvolgorde in de codering ligt **vast**: lat/lon, dan feat1, dan feat2, dan
de naam. Omdat de naam geen lengteprefix heeft en doorloopt tot het einde van
`app_data`, moet hij als laatste komen, en een parser moet de totale
`app_data_len` kennen om te vinden waar de naam eindigt. Op de wire staat er geen
null-terminator; `AdvertDataParser` voegt er één toe bij het kopiëren naar zijn
eigen buffer.

Coördinaten zijn micrograden in vastekommanotatie: `_lat = lat * 1E6` bij het coderen,
`_lat / 1000000.0` bij het decoderen. Het bereik is daarmee ±2147 graden — veel
meer dan nodig — en de precisie is ongeveer 11 cm.

Twee eigenaardigheden van de encoder zijn het kennen waard, beide in
`AdvertDataBuilder::encodeTo()`:

1. `extra1` / `extra2` worden alleen geschreven `if (_extra1)` — een
   **featurewaarde van nul is niet te onderscheiden van een afwezige feature**. Je
   kunt in geen van beide featurevelden een expliciete nul versturen.
2. De naam wordt afgekapt met `mesh::validUtf8PrefixLength(_name, 32 - i)`, dus
   hij wordt op een UTF-8-tekengrens gesneden en niet middenin een reeks. Een lange
   naam verliest stilzwijgend zijn staart; hij levert nooit ongeldige UTF-8 op.

Aan de decodeerkant zet `AdvertDataParser` `_valid` alleen wanneer
`app_data_len >= i` nadat de geflagde velden zijn doorlopen. Het controleert
**niet** onderweg elk veld tegen `app_data_len`, dus het leest eerst de geflagde
velden en valideert achteraf. Voed het uitsluitend buffers van minstens
`MAX_ADVERT_DATA_SIZE`.

### TRACE (`0x09`)

```
+-----------+-----------+-------+------------------------+
| trace_tag | auth_code | flags | hop list               |
| 4 bytes   | 4 bytes   | 1 byte| appended by hops       |
+-----------+-----------+-------+------------------------+
```

`Mesh::createTrace()` zet `payload_len = 9` en de hoplijst groeit daarachter aan.
`Mesh::onRecvPacket()` regels 41–68 handelt het af.

- De lage 2 bits van `flags` zijn de exponent van de path-hashgrootte:
  `path_sz = flags & 0x03`, en de entry-grootte is `1 << path_sz` (v1.11+).
- De route is vooraf berekend: de bedoelde hop-hashes staan in de *payload* na de
  flagsbyte, terwijl de gemeten SNR-waarden zich opstapelen in *`path`*.
- Een node stuurt alleen door als zijn sleutelprefix overeenkomt op
  `payload[9 + (path_len << path_sz)]`.
- Wanneer `offset >= len` heeft de trace het einde van zijn route bereikt en wordt
  hij afgeleverd via `onTraceRecv()`.

De offset wordt berekend als `uint16_t`, met een expliciet commentaar dat uitlegt
waarom: `path_len` tot 63 maal een entry-grootte tot 8 overschrijdt 255, en een
`uint8_t` zou omslaan en de vergelijking op de verkeerde bytes richten.

`Mesh::sendDirect()` bouwt de uitgaande trace anders op dan elk ander type: het
hangt de vooraf berekende hoplijst aan de **payload**, zet `path_len` op nul, en
verzendt op prioriteit 5 (`src/Mesh.cpp` 698–704).

TRACE krijgt ook een uitzonderingsbehandeling in
`Packet::calculatePacketHash()` — het is het enige type waarvan `path_len` mee in
de dedup-hash wordt gemengd, omdat een trace legitiem dezelfde node opnieuw kan
aandoen op de terugweg en niet als duplicaat onderdrukt mag worden.

`Mesh::sendFlood()` weigert TRACE-pakketten expliciet.

### MULTIPART (`0x0A`)

```
+---------------+---------------------------+
| packing byte  | inner payload             |
| 1 byte        | remainder                 |
+---------------+---------------------------+
```

De packing-byte splitst in `remaining = payload[0] >> 4` (pakketten die nog moeten
komen) en `type = payload[0] & 0x0F` (het ingepakte payload-type)
(`Mesh::onRecvPacket()` regels 300–304).

Bewakingen: de handler vereist `payload_len > 2` (regel 301), en voor het
ACK-geval `payload_len >= 5` (regel 305).

Alleen `type == PAYLOAD_TYPE_ACK` is geïmplementeerd. De handler bouwt een
synthetisch `Packet` opnieuw op met de packing-byte weggehaald en verwerkt het als
een gewone ACK. Al de rest valt in een tak met `// FUTURE: other multipart
types??` en wordt weggegooid.

> **Een multipart-ACK dedupliceert *niet* tegen een gewone ACK.** Het synthetische
> pakket kopieert de header letterlijk (`tmp.header = pkt->header`,
> `src/Mesh.cpp` 307), dus `getPayloadType()` daarop geeft nog altijd
> `PAYLOAD_TYPE_MULTIPART` (0x0A) terug. De dedup-hash is
> `SHA256(0x0A || ack_payload)`, terwijl een gewone ACK
> `SHA256(0x03 || ack_payload)` hasht. De twee verschillen, dus multipart-ACK's
> dedupliceren alleen tegen andere multipart-ACK's. (Een eerdere versie van dit
> document had dit omgekeerd.)

`Mesh::createMultiAck()` bouwt het omgekeerde: `payload[0] = (remaining << 4) | PAYLOAD_TYPE_ACK`.

Er is een parallel direct pad, `forwardMultipartDirect()`
(`src/Mesh.cpp` 359–377), bereikt vanaf regels 90–91, dat
multi-ACK-hertransmissies spreidt op `(remaining + 1) * 300` ms.

### CONTROL (`0x0B`)

```
+-------------+---------------------------+
| flags byte  | application data          |
| 1 byte      | remainder                 |
+-------------+---------------------------+
```

Afgehandeld op `Mesh::onRecvPacket()` regels 70–76. Een CONTROL-pakket wordt
alleen aan `onControlDataRecv()` afgeleverd wanneer het **direct-routed is, bit 7
van `payload[0]` gezet heeft, en een hopteller van exact nul heeft**. Commentaar
in de broncode: "just zero-hop control packets allowed (for this subset of
payloads)".

> **Het klopt niet dat CONTROL nooit wordt doorgestuurd**, en een eerdere versie
> van dit document beweerde dat wel. Dat blok onderschept alleen CONTROL-pakketten
> met **bit 7 gezet**. Een direct-routed CONTROL-pakket met bit 7 *leeg* en nog
> resterende hops bereikt de switch op payload-type helemaal niet — het valt in het
> generieke direct-forwardingblok (`src/Mesh.cpp` 78–110) en **wordt doorgestuurd**
> zoals elk ander direct verkeer. Zie
> [de structurele poort](#de-structurele-poort-die-aan-die-switch-voorafgaat).
> Flood-routed CONTROL bereikt de `default:`-tak wel en wordt weggegooid
> (`326–329`).

De betekenis van de overige 7 flagbits en van de data erachter wordt bepaald door
de applicatie (`CMD_SEND_CONTROL_DATA` aan de companion-kant), niet door `src/`.

### RAW_CUSTOM (`0x0F`)

De payload is volledig applicatiegedefinieerd. De kern dedupliceert hem en roept
`onRawDataRecv()` aan, en hij wordt bewust niet flood-routed — de broncode draagt
het commentaar `// don't flood route these (yet)` (`src/Mesh.cpp` 296).

"Vereist dat hij direct-routed is" is een onderschatting van de regel. De `case`
is alleen bereikbaar voor **zero-hop** directe pakketten; een direct RAW_CUSTOM
met meerdere hops wordt doorgestuurd op `src/Mesh.cpp` 89–107 zonder dat
`onRawDataRecv()` ooit wordt aangeroepen. Het type wordt dus alleen *afgeleverd*
bij hopteller nul, terwijl het er tussenin *doorgegeven* wordt als eender wat.

## 1.7 Pakkethash en deduplicatie

`Packet::calculatePacketHash()` (`src/Packet.cpp` regels 41–50):

```
SHA256( payload_type_byte || [path_len if TRACE] || payload )  -> first 8 bytes
```

Merk op wat er **uitgesloten** is: het route-type, de payload-versie en het pad.
Dat is precies de bedoeling. Hetzelfde logische pakket dat via twee verschillende
routes aankomt, of in flood- en in directe vorm, hasht identiek en wordt door
`wasSeen()` / `markSeen()` onderdrukt als duplicaat.

Drie details die een herimplementatie exact goed moet krijgen:

- De uitvoer is `MAX_HASH_SIZE` = **8** bytes, de eerste 8 van de SHA-256.
- Voor TRACE mengt `sha.update(&path_len, sizeof(path_len))` er **2 bytes** in,
  omdat `path_len` in het in-memory `Packet` als `uint16_t` gedeclareerd staat
  (`src/Packet.h` 47), ook al beslaat het op de wire één byte. Daar één byte
  hashen levert geen match op.
- **Een zender markeert zijn eigen pakketten vooraf als gezien** (`src/Mesh.cpp`
  651, 680, 713, 723, 736), zodat een node de echo van zijn eigen uitzending nooit
  verwerkt.

## 1.8 Uitgewerkt voorbeeld — een repeater-advert

Een repeater met de naam `BE-HSS-JessaZH.VIR` op 50.930000 N, 5.338000 O zendt op
epoch 1786665600 een flood-advert uit.

**Header.** Flood-route, advert-type, versie 1:

```
route = ROUTE_TYPE_FLOOD          = 0x01
type  = PAYLOAD_TYPE_ADVERT       = 0x04
ver   = PAYLOAD_VER_1             = 0x00

header = (0x00 << 6) | (0x04 << 2) | 0x01
       = 0x00 | 0x10 | 0x01
       = 0x11
```

**Transportcodes.** Het route-type is `0x01` en niet `0x00`/`0x03`, dus dit veld
is afwezig. Nul bytes.

**path_len.** Net uitgezonden, nog geen hops, standaard hashgrootte 1:

```
path_len = ((1 - 1) << 6) | 0 = 0x00
```

**app_data.** Repeater, heeft locatie, heeft naam:

```
flags = ADV_TYPE_REPEATER | ADV_LATLON_MASK | ADV_NAME_MASK
      = 0x02 | 0x10 | 0x80
      = 0x92

lat = 50.930000 * 1e6 = 50930000 = 0x030948D0  -> LE: D0 48 09 03
lon =  5.338000 * 1e6 =  5338000 = 0x00517390  -> LE: 90 73 51 00
name = "BE-HSS-JessaZH.VIR" (18 bytes, no terminator)
```

```
92                                      flags
D0 48 09 03                             lat
90 73 51 00                             lon
42 45 2D 48 53 53 2D 4A 65 73 73 61     "BE-HSS-Jessa"
5A 48 2E 56 49 52                       "ZH.VIR"
```

Lengte van `app_data` = 1 + 4 + 4 + 18 = **27** bytes (≤ 32, dus geldig).

**Volledig frame.**

```
offset  bytes                                   field
------  --------------------------------------  ---------------------------
0x00    11                                      header
0x01    00                                      path_len (size 1, count 0)
0x02    <32 bytes>                              pub_key
0x22    80 5A 7E 6A                             timestamp (1786665600 LE)
0x26    <64 bytes>                              Ed25519 signature
0x66    92 D0 48 09 03 90 73 51 00 42 45 ...    app_data (27 bytes)
0x81    (end)
```

**Lengtes.**

```
payload_len = 32 + 4 + 64 + 27          = 127
raw length  = 1 + 0 + 1 + 0 + 127       = 129 bytes
```

Controle tegen `Packet::getRawLength()`:

```
2 + getPathByteLen() + payload_len + (hasTransportCodes() ? 4 : 0)
= 2 + 0 + 127 + 0
= 129   ✓
```

**De invoer voor de handtekening** is het 63-byte bericht
`pub_key || 80 5A 7E 6A || app_data`, dus 32 + 4 + 27 bytes — *niet* de bytes van
het pakket, en zonder de header of het pad.

### Hetzelfde advert na twee flood-hops

Twee repeaters sturen het door. Elk voegt zijn eigen sleutelprefix van 1 byte toe.
Stel `A7` en daarna `3F`:

```
offset  bytes         field
------  ------------  -------------------------------
0x00    11            header (unchanged)
0x01    02            path_len: size 1, count 2
0x02    A7 3F         path
0x04    <127 bytes>   payload (unchanged)
0x83    (end)
```

`path_len` ging van `0x00` naar `0x02`, en het frame groeide met exact 2 bytes tot
131. De payload — en dus ook de handtekening en de dedup-hash — is onaangeroerd.

## 1.9 Het admin-/serverrequestprotocol

Alles hierboven beschrijft de envelop. Binnen een ontsleutelde `REQ`-payload zit
een tweede protocol op applicatieniveau, en dat is wat MeshManager werkelijk spreekt
wanneer een monitoringnode een repeater bevraagt. Het maakt geen deel uit van
`src/`: elke voorbeeldfirmware definieert zijn eigen requestnummers, en daarom
zijn de tabellen hieronder per rol opgesteld.

**Repeater** — `examples/simple_repeater/MyMesh.cpp` 50–61:

| Waarde | Naam | Opmerkingen |
|---|---|---|
| `0x01` | `REQ_TYPE_GET_STATUS` | Antwoordt met `RepeaterStats` |
| `0x02` | `REQ_TYPE_KEEP_ALIVE` | |
| `0x03` | `REQ_TYPE_GET_TELEMETRY_DATA` | Antwoordt met Cayenne LPP; zie §1.10 |
| `0x05` | `REQ_TYPE_GET_ACCESS_LIST` | |
| `0x06` | `REQ_TYPE_GET_NEIGHBOURS` | |
| `0x07` | `REQ_TYPE_GET_OWNER_INFO` | Vereist `FIRMWARE_VER_LEVEL >= 2` |

`RESP_SERVER_LOGIN_OK` is `0` (regel 57). De sensorfirmware voegt
`REQ_TYPE_LOGIN` `0x00` en `REQ_TYPE_GET_AVG_MIN_MAX` `0x04` toe
(`examples/simple_sensor/SensorMesh.cpp` 51–58); de room server heeft zijn eigen
set (`examples/simple_room_server/MyMesh.cpp` 15–20), en de gedeelde definities
aan de chatkant staan in `src/helpers/BaseChatMesh.h` 18–21.

Binnen een **ANON_REQ** is de selector een andere opsomming
(`examples/simple_repeater/MyMesh.cpp` 59–61):

| Waarde | Naam |
|---|---|
| `0x01` | `ANON_REQ_TYPE_REGIONS` |
| `0x02` | `ANON_REQ_TYPE_OWNER` |
| `0x03` | `ANON_REQ_TYPE_BASIC` |

De repeater onderscheidt die van een login door één byte te bekijken: in de
ontsleutelde body `uint32 timestamp | data[4]` betekent een `data[4]` die `0` of
`>= ' '` is een login-/wachtwoordrequest, en al de rest is een
`ANON_REQ_TYPE_*`-selector (`examples/simple_repeater/MyMesh.cpp` 803–814).

### Toegangscontrolerollen

`src/helpers/ClientACL.h` 7–11:

| Waarde | Naam |
|---|---|
| — | `PERM_ACL_ROLE_MASK` = 3 |
| 0 | `PERM_ACL_GUEST` |
| 1 | `PERM_ACL_READ_ONLY` |
| 2 | `PERM_ACL_READ_WRITE` |
| 3 | `PERM_ACL_ADMIN` |

Dit zijn de getallen achter `setperm <pubkey> <n>` op de CLI van een repeater, en
het onderscheid dat bepaalt of een monitoringnode de instellingen van een andere
repeater kan uitlezen: **een repeater voert een CLI-commando alleen uit voor een
client die hij als admin beschouwt, en zegt helemaal niets tegen een client die
dat niet is.** Een read-only monitor logt dus perfect in en wordt daarna genegeerd
— op de lucht niet te onderscheiden van een node buiten bereik. Zie
[`firmware.md`](firmware.md).

## 1.10 Telemetrie en Cayenne LPP

Een `REQ_TYPE_GET_TELEMETRY_DATA`-request antwoordt in **Cayenne LPP**, de enige
codering in dit systeem die de eigen conventies van MeshCore niet volgt.

Permissiebits (`src/helpers/SensorManager.h` 6–10):

| Waarde | Naam |
|---|---|
| `0x01` | `TELEM_PERM_BASE` |
| `0x02` | `TELEM_PERM_LOCATION` |
| `0x04` | `TELEM_PERM_ENVIRONMENT` |
| 1 | `TELEM_CHANNEL_SELF` |

> **`payload[1]` van de request is een *omgekeerd* permissiemasker.** De
> antwoordende node berekent `~payload[1]`
> (`examples/simple_repeater/MyMesh.cpp` 244–265). Een rechttoe rechtaan lezing van
> die byte levert precies de verkeerde set permissies op.

Kanaalnummering: GPS is altijd kanaal 1, en elke andere sensor krijgt sequentieel
een kanaal toegewezen vanaf `TELEM_CHANNEL_SELF + 1`
(`src/helpers/sensors/EnvironmentSensorManager.cpp` 668, 671). Wat een kanaal
betekent, is dus een eigenschap van de antwoordende node en niet van het protocol
— en daarom slaat MeshManager telemetrie op als `ch<N>_temperature` /
`ch<N>_voltage`, onder het kanaal dat de bron zelf gebruikte, in plaats van het te
hernoemen naar iets waarvan het aanneemt dat het dat betekent. Op een
MeshCore-repeater is kanaal 1 zijn eigen board, dus `ch1_temperature` is daar de
MCU-die en niet de buitenlucht.

### Recordframing

```
channel (1 byte) | type (1 byte) | value (type-dependent)
```

`channel == 0` beëindigt de stroom (`src/helpers/sensors/LPPDataHelpers.h`
95–103); de overslaglengtes per type staan op 140–172.

> **LPP is big-endian.** `LPPWriter::write()` schrijft de meest significante byte
> eerst (regels 180–183) en `LPPReader::getFloat()` schuift naar links (71–86).
> Elk ander multi-byteveld in MeshCore — timestamps, transportcodes, ACK-CRC's,
> advertcoördinaten — is little-endian. Dit is met voorsprong de makkelijkste
> plek in het hele systeem om de byte-volgorde verkeerd te doen, omdat het pakket
> eromheen je het omgekeerde heeft aangeleerd.

### Typetabel

`src/helpers/sensors/LPPDataHelpers.h` 5–31:

| Waarde | Naam | Codering |
|---|---|---|
| 0 | `LPP_DIGITAL_INPUT` | 1 B |
| 1 | `LPP_DIGITAL_OUTPUT` | 1 B |
| 2 | `LPP_ANALOG_INPUT` | 2 B, ×100 signed |
| 3 | `LPP_ANALOG_OUTPUT` | 2 B, ×100 signed |
| 100 | `LPP_GENERIC_SENSOR` | 4 B unsigned |
| 101 | `LPP_LUMINOSITY` | 2 B, 1 lux |
| 102 | `LPP_PRESENCE` | 1 B bool |
| 103 | `LPP_TEMPERATURE` | 2 B, ×10 signed |
| 104 | `LPP_RELATIVE_HUMIDITY` | 1 B, ×2 unsigned |
| 113 | `LPP_ACCELEROMETER` | 2 B per as, ×1000 |
| 115 | `LPP_BAROMETRIC_PRESSURE` | 2 B, ×10 unsigned |
| 116 | `LPP_VOLTAGE` | 2 B, ×100 unsigned |
| 117 | `LPP_CURRENT` | 2 B, ×1000 |
| 118 | `LPP_FREQUENCY` | 4 B, 1 Hz |
| 120 | `LPP_PERCENTAGE` | 1 B |
| 121 | `LPP_ALTITUDE` | 2 B, 1 m signed |
| 125 | `LPP_CONCENTRATION` | 2 B, 1 ppm |
| 128 | `LPP_POWER` | 2 B, 1 W |
| 130 | `LPP_DISTANCE` | 4 B, ×1000 |
| 131 | `LPP_ENERGY` | 4 B, ×1000 kWh |
| 132 | `LPP_DIRECTION` | 2 B, 1 graad |
| 133 | `LPP_UNIXTIME` | 4 B unsigned |
| 134 | `LPP_GYROMETER` | 2 B per as, ×100 |
| 135 | `LPP_COLOUR` | 3 B RGB |
| 136 | `LPP_GPS` | 3 B lat + 3 B lon (×10000) + 3 B alt (×100) |
| 142 | `LPP_SWITCH` | 1 B |
| 240 | `LPP_POLYLINE` | variabel, minimaal 8 B |

De vermenigvuldigers staan op regels 34–60; de foutcodes zijn `LPP_ERROR_OK` 0,
`LPP_ERROR_OVERFLOW` 1 en `LPP_ERROR_UNKOWN_TYPE` 2 (spelling zoals in de
broncode), regels 62–64.

MeshManager decodeert er slechts twee van — `LPP_TEMPERATURE` en `LPP_VOLTAGE` — naar
`ch<N>_temperature` en `ch<N>_voltage`, via `helpers/sensors/LPPDataHelpers.h` dat
door `MeshManagerNet.cpp` wordt geïncludeerd. De rest staat hier vermeld zodat een
uitbreiding de tabel niet opnieuw hoeft te ontdekken.

---

# 2. Het companion-protocol (TCP en serieel)

Dit is de verbinding tussen een node en een clientapplicatie: de
MeshCore-telefoonapp, `meshcore-cli`, de `meshcore`-integratie van Home Assistant,
of MeshManager' eigen gereedschap. Het is geen mesh-protocol; het verlaat de lokale
link nooit.

Referentiebron:

| Onderwerp | Bestand |
|---|---|
| TCP-framing, meerdere clients | `MeshManager/firmware/src/helpers/esp32/SerialWifiInterface.cpp` |
| Bovengrens framegrootte | `MeshCore/src/helpers/BaseSerialInterface.h` |
| Commando-/responscodes | `MeshCore/examples/companion_radio/MyMesh.cpp` |
| Een onafhankelijke implementatie | `MeshManager/proxy/mc-proxy/mc_proxy.py` |

## 2.1 Framing

Elk frame, in beide richtingen:

```
+--------+-------------------+---------------------+
| marker | length            | payload             |
| 1 byte | 2 bytes, LE       | `length` bytes      |
+--------+-------------------+---------------------+
```

| Marker | Hex | Richting |
|---|---|---|
| `<` | `0x3C` | client → node |
| `>` | `0x3E` | node → client |

De lengte is little-endian en dekt alleen de payload, niet de 3-byte header.
Schrijfkant, `SerialWifiInterface::checkRecvFrame()`:

```c
pkt[0] = '>';
pkt[1] = (len & 0xFF);  // LSB
pkt[2] = (len >> 8);    // MSB
```

Leeskant, `SerialWifiInterface::readFromSlot()`, leest de marker en daarna de
2-byte lengte rechtstreeks in een `uint16_t` — wat alleen correct is omdat de
ESP32 little-endian is. Een portable client moet die expliciet samenstellen, zoals
`mc_proxy.py` doet:

```python
ln = buf[1] | (buf[2] << 8)
```

De maximale payload is `MAX_FRAME_SIZE` = **176** bytes. Frames die langer zijn,
worden byte per byte gelezen en weggegooid, net als frames waarvan de marker niet
de verwachte is — de parser synchroniseert opnieuw in plaats van de verbinding te
verbreken.

Het 16-bits lengteveld laat 65535 toe, maar niets mag meer dan 176 versturen.
Dimensioneer buffers niet op het lengteveld alleen.

## 2.2 Payloadstructuur

Byte 0 van de payload is de commando-, respons- of pushcode. De rest is
codespecifiek.

Omdat de header 3 bytes is, **staat de codebyte op offset 3 van het frame**. Zo
inspecteert `mc_proxy.py` frames zonder ze te decoderen:

```python
if len(frame) >= 4 and frame[3] == PKT_SELF_INFO:
```

Commandocodes, client → node (`examples/companion_radio/MyMesh.cpp` regels 14–72):

| Code | Naam | | Code | Naam |
|---|---|---|---|---|
| 1 | `CMD_APP_START` | | 33 | `CMD_SIGN_START` |
| 2 | `CMD_SEND_TXT_MSG` | | 34 | `CMD_SIGN_DATA` |
| 3 | `CMD_SEND_CHANNEL_TXT_MSG` | | 35 | `CMD_SIGN_FINISH` |
| 4 | `CMD_GET_CONTACTS` | | 36 | `CMD_SEND_TRACE_PATH` |
| 5 | `CMD_GET_DEVICE_TIME` | | 37 | `CMD_SET_DEVICE_PIN` |
| 6 | `CMD_SET_DEVICE_TIME` | | 38 | `CMD_SET_OTHER_PARAMS` |
| 7 | `CMD_SEND_SELF_ADVERT` | | 39 | `CMD_SEND_TELEMETRY_REQ` |
| 8 | `CMD_SET_ADVERT_NAME` | | 40 | `CMD_GET_CUSTOM_VARS` |
| 9 | `CMD_ADD_UPDATE_CONTACT` | | 41 | `CMD_SET_CUSTOM_VAR` |
| 10 | `CMD_SYNC_NEXT_MESSAGE` | | 42 | `CMD_GET_ADVERT_PATH` |
| 11 | `CMD_SET_RADIO_PARAMS` | | 43 | `CMD_GET_TUNING_PARAMS` |
| 12 | `CMD_SET_RADIO_TX_POWER` | | 50 | `CMD_SEND_BINARY_REQ` |
| 13 | `CMD_RESET_PATH` | | 51 | `CMD_FACTORY_RESET` |
| 14 | `CMD_SET_ADVERT_LATLON` | | 52 | `CMD_SEND_PATH_DISCOVERY_REQ` |
| 15 | `CMD_REMOVE_CONTACT` | | 54 | `CMD_SET_FLOOD_SCOPE_KEY` (v8+) |
| 16 | `CMD_SHARE_CONTACT` | | 55 | `CMD_SEND_CONTROL_DATA` (v8+) |
| 17 | `CMD_EXPORT_CONTACT` | | 56 | `CMD_GET_STATS` (v8+) |
| 18 | `CMD_IMPORT_CONTACT` | | 57 | `CMD_SEND_ANON_REQ` |
| 19 | `CMD_REBOOT` | | 58 | `CMD_SET_AUTOADD_CONFIG` |
| 20 | `CMD_GET_BATT_AND_STORAGE` | | 59 | `CMD_GET_AUTOADD_CONFIG` |
| 21 | `CMD_SET_TUNING_PARAMS` | | 60 | `CMD_GET_ALLOWED_REPEAT_FREQ` |
| 22 | `CMD_DEVICE_QUERY` | | 61 | `CMD_SET_PATH_HASH_MODE` |
| 23 | `CMD_EXPORT_PRIVATE_KEY` | | 62 | `CMD_SEND_CHANNEL_DATA` |
| 24 | `CMD_IMPORT_PRIVATE_KEY` | | 63 | `CMD_SET_DEFAULT_FLOOD_SCOPE` |
| 25 | `CMD_SEND_RAW_DATA` | | 64 | `CMD_GET_DEFAULT_FLOOD_SCOPE` |
| 26 | `CMD_SEND_LOGIN` | | 65 | `CMD_SEND_RAW_PACKET` |
| 27 | `CMD_SEND_STATUS_REQ` | | | |
| 28 | `CMD_HAS_CONNECTION` | | | |
| 29 | `CMD_LOGOUT` | | | |
| 30 | `CMD_GET_CONTACT_BY_KEY` | | | |
| 31 | `CMD_GET_CHANNEL` | | | |
| 32 | `CMD_SET_CHANNEL` | | | |

De codes 44–49 en 53 zijn in deze versie niet toegewezen.

`CMD_GET_STATS` (56) neemt een subtype in zijn tweede byte:

| Waarde | Naam |
|---|---|
| 0 | `STATS_TYPE_CORE` |
| 1 | `STATS_TYPE_RADIO` |
| 2 | `STATS_TYPE_PACKETS` |

Responscodes, node → client, verstuurd als antwoord op een commando:

| Code | Naam | Antwoord op |
|---|---|---|
| 0 | `RESP_CODE_OK` | eender wat |
| 1 | `RESP_CODE_ERR` | eender wat |
| 2 | `RESP_CODE_CONTACTS_START` | `CMD_GET_CONTACTS` (eerste) |
| 3 | `RESP_CODE_CONTACT` | `CMD_GET_CONTACTS` (herhaald) |
| 4 | `RESP_CODE_END_OF_CONTACTS` | `CMD_GET_CONTACTS` (laatste) |
| 5 | `RESP_CODE_SELF_INFO` | `CMD_APP_START` |
| 6 | `RESP_CODE_SENT` | `CMD_SEND_TXT_MSG` |
| 7 | `RESP_CODE_CONTACT_MSG_RECV` | `CMD_SYNC_NEXT_MESSAGE` (ver < 3) |
| 8 | `RESP_CODE_CHANNEL_MSG_RECV` | `CMD_SYNC_NEXT_MESSAGE` (ver < 3) |
| 9 | `RESP_CODE_CURR_TIME` | `CMD_GET_DEVICE_TIME` |
| 10 | `RESP_CODE_NO_MORE_MESSAGES` | `CMD_SYNC_NEXT_MESSAGE` |
| 11 | `RESP_CODE_EXPORT_CONTACT` | `CMD_EXPORT_CONTACT` |
| 12 | `RESP_CODE_BATT_AND_STORAGE` | `CMD_GET_BATT_AND_STORAGE` |
| 13 | `RESP_CODE_DEVICE_INFO` | `CMD_DEVICE_QUERY` |
| 14 | `RESP_CODE_PRIVATE_KEY` | `CMD_EXPORT_PRIVATE_KEY` |
| 15 | `RESP_CODE_DISABLED` | |
| 16 | `RESP_CODE_CONTACT_MSG_RECV_V3` | `CMD_SYNC_NEXT_MESSAGE` (ver ≥ 3) |
| 17 | `RESP_CODE_CHANNEL_MSG_RECV_V3` | `CMD_SYNC_NEXT_MESSAGE` (ver ≥ 3) |
| 18 | `RESP_CODE_CHANNEL_INFO` | `CMD_GET_CHANNEL` |
| 19 | `RESP_CODE_SIGN_START` | `CMD_SIGN_START` |
| 20 | `RESP_CODE_SIGNATURE` | `CMD_SIGN_FINISH` |
| 21 | `RESP_CODE_CUSTOM_VARS` | `CMD_GET_CUSTOM_VARS` |
| 22 | `RESP_CODE_ADVERT_PATH` | `CMD_GET_ADVERT_PATH` |
| 23 | `RESP_CODE_TUNING_PARAMS` | `CMD_GET_TUNING_PARAMS` |
| 24 | `RESP_CODE_STATS` | `CMD_GET_STATS`; byte 2 is het statstype |
| 25 | `RESP_CODE_AUTOADD_CONFIG` | `CMD_GET_AUTOADD_CONFIG` |
| 26 | `RESP_ALLOWED_REPEAT_FREQ` | `CMD_GET_ALLOWED_REPEAT_FREQ` |
| 27 | `RESP_CODE_CHANNEL_DATA_RECV` | |
| 28 | `RESP_CODE_DEFAULT_FLOOD_SCOPE` | `CMD_GET_DEFAULT_FLOOD_SCOPE` |

Pushcodes, node → client, **ongevraagd en op elk moment**. Ze beginnen bij `0x80`,
dus het hoogste bit onderscheidt een push van een respons:

| Code | Naam |
|---|---|
| `0x80` | `PUSH_CODE_ADVERT` |
| `0x81` | `PUSH_CODE_PATH_UPDATED` |
| `0x82` | `PUSH_CODE_SEND_CONFIRMED` |
| `0x83` | `PUSH_CODE_MSG_WAITING` |
| `0x84` | `PUSH_CODE_RAW_DATA` |
| `0x85` | `PUSH_CODE_LOGIN_SUCCESS` |
| `0x86` | `PUSH_CODE_LOGIN_FAIL` |
| `0x87` | `PUSH_CODE_STATUS_RESPONSE` |
| `0x88` | **`PUSH_CODE_LOG_RX_DATA`** |
| `0x89` | `PUSH_CODE_TRACE_DATA` |
| `0x8A` | `PUSH_CODE_NEW_ADVERT` |
| `0x8B` | `PUSH_CODE_TELEMETRY_RESPONSE` |
| `0x8C` | `PUSH_CODE_BINARY_RESPONSE` |
| `0x8D` | `PUSH_CODE_PATH_DISCOVERY_RESPONSE` |
| `0x8E` | `PUSH_CODE_CONTROL_DATA` (v8+) |
| `0x8F` | `PUSH_CODE_CONTACT_DELETED` |
| `0x90` | `PUSH_CODE_CONTACTS_FULL` |

(`examples/companion_radio/MyMesh.cpp` 120–136.) `PUSH_CODE_LOG_RX_DATA`
(`0x88`) is degene die door `MyMesh::logRxRaw()` wordt uitgezonden — dezelfde hook
die MeshManager aftapt voor zijn ruwe feed.

`PUSH_CODE_LOGIN_SUCCESS` / `PUSH_CODE_LOGIN_FAIL` zijn het vermelden waard naast
[het zwijgen van de repeater bij een geweigerde login](firmware.md): over de
*companion*-link krijgt een client het wél te horen, omdat de node waarmee hij
praat de zijne is. Via de *lucht* zegt een repeater die een login weigert
helemaal niets.

Foutcodes, teruggegeven in de body van een `RESP_CODE_ERR`
(`examples/companion_radio/MyMesh.cpp` 138–143):

| Code | Naam |
|---|---|
| 1 | `ERR_CODE_UNSUPPORTED_CMD` |
| 2 | `ERR_CODE_NOT_FOUND` |
| 3 | `ERR_CODE_TABLE_FULL` |
| 4 | `ERR_CODE_BAD_STATE` |
| 5 | `ERR_CODE_FILE_IO_ERROR` |
| 6 | `ERR_CODE_ILLEGAL_ARG` |

Andere constanten uit hetzelfde headerblok:

| Constante | Waarde |
|---|---|
| `MAX_CHANNEL_DATA_LENGTH` | `MAX_FRAME_SIZE - 9` = 167 |
| `PUBLIC_GROUP_PSK` | `izOH6cXN6mrJ5e26oRXNcg==` (de algemeen bekende sleutel van het publieke kanaal) |

## 2.3 Het probleem van de enkele client

Standaard MeshCore accepteert precies één TCP-client. De oorspronkelijke lus was:

```cpp
auto newClient = server.available();
if (newClient) {
    client.stop();      // the existing companion is kicked off
    client = newClient;
}
```

Home Assistant en je telefoon konden dus niet allebei verbonden zijn. MeshManager
lost dit twee keer op, op twee verschillende plaatsen, en de twee oplossingen
verschillen op een belangrijk punt.

### Firmware: `SerialWifiInterface`, 4 slots met gerichte antwoorden

`WIFI_MAX_CLIENTS` (standaard 4) slots, elk met zijn eigen `WiFiClient` **en zijn
eigen gedeeltelijke frameheader**. Headerstatus per slot is niet optioneel: twee
clients midden in een frame zouden anders elkaars lengtevelden corrumperen.

Nieuwe verbindingen krijgen een vrij slot. Alleen wanneer alle slots bezet zijn,
wordt het oudste weggegooid (`slot = next_poll % WIFI_MAX_CLIENTS`).

Inkomende frames worden round-robin gepolld vanaf `next_poll`, zodat één
spraakzame client de andere niet kan uithongeren.

Uitgaande frames worden **gerouteerd**:

```cpp
send_queue[send_queue_len].dest_slot = reply_slot;
```

`reply_slot` wordt bovenaan elke `checkRecvFrame()` op `-1` gezet en krijgt de
slotindex zodra een commando aan het mesh wordt doorgegeven. Alles wat het mesh
onmiddellijk na een commando schrijft, wordt dus als het antwoord op dat commando
beschouwd en gaat alleen naar die client; alles wat op een ander moment wordt
geschreven (adverts, binnenkomende berichten, ACK's) heeft `dest_slot == -1` en
wordt naar elke verbonden client gebroadcast.

Dit werkt omdat de companion-firmware single-threaded en synchroon is: een
commando wordt volledig afgehandeld, inclusief zijn schrijfacties, voor het
volgende frame wordt gelezen. Zonder dat onderscheid zien clients elkaars
antwoorden en raken hun request-/responstoestandsmachines uit de pas.

De diepte van de zendwachtrij is `FRAME_QUEUE_SIZE` = 4 frames; bij een volle
wachtrij wordt de schrijfactie weggegooid en 0 teruggegeven.

### Proxy: `mc_proxy.py`, alles broadcasten

De proxy staat vóór een **ongewijzigde** node, dus hij houdt de enige
upstream-socket vast en waaiert uit. Hij kan de `reply_slot`-truc niet gebruiken —
hij heeft geen zicht op de interne volgorde van de node — en sinds versie 1.8.0
broadcast hij bewust elk frame van de node naar elke client, omdat de eerdere
poging tot antwoordroutering een drukke client het antwoord van iemand anders liet
wegkapen.

Hij compenseert dat met twee gedragingen:

- **Caching van `RESP_CODE_SELF_INFO`.** De node beantwoordt `CMD_APP_START`
  slechts één keer per TCP-sessie. De proxy bewaart het laatste SELF_INFO-frame en
  beantwoordt de `CMD_APP_START` van elke client lokaal, zonder hem door te sturen.
- **Spreiding van commando's.** `MCP_MIN_CMD_GAP_S` (standaard 0,25 s) tussen
  upstream-schrijfacties.

Gebruik de firmwareslots wanneer je de firmware kunt aanpassen; gebruik de proxy
wanneer dat niet kan. Zie [`deployment.md`](deployment.md).

## 2.4 Uitgewerkt voorbeeld — een companion-frame

`CMD_GET_DEVICE_TIME` (5) zonder argumenten, client → node:

```
3C 01 00 05
^  ^^^^^ ^
|  |     +-- payload: CMD_GET_DEVICE_TIME
|  +-------- length = 1, little-endian
+----------- marker '<' (0x3C)
```

Het antwoord van de node draagt `RESP_CODE_CURR_TIME` (9) en een
`uint32`-timestamp:

```
3E 05 00 09 80 5A 7E 6A
^  ^^^^^ ^  ^^^^^^^^^^^
|  |     |  +----------- uint32 LE timestamp
|  |     +-------------- payload: RESP_CODE_CURR_TIME
|  +-------------------- length = 5
+----------------------- marker '>' (0x3E)
```

En de handshake die de proxy bij het verbinden verstuurt. `mc_proxy.py` bouwt hem
als `frame(bytes([CMD_APP_START, 0x03]) + b"      " + b"mcproxy")` — commandobyte,
`0x03`, zes spaties, dan de naam van de app. Dat zijn 15 payloadbytes:

```
3C 0F 00 01 03 20 20 20 20 20 20 6D 63 70 72 6F 78 79
^  ^^^^^ ^  ^  ^^^^^^^^^^^^^^^^^ ^^^^^^^^^^^^^^^^^^^
|  |     |  |  6 spaces           "mcproxy"
|  |     |  +-- 0x03
|  |     +----- CMD_APP_START
|  +----------- length = 15
+-------------- marker '<'
```

> De **betekenis** van de argumentlayout — `0x03` als protocolversie, het veld van
> zes spaties, de app-naam achteraan — is afgeleid uit deze ene aanroepplaats. Ze
> is niet uit de `CMD_APP_START`-handler van `MyMesh.cpp` gelezen, dus beschouw de
> veldsemantiek als **niet geverifieerd**. De bytereeks zelf is exact wat de proxy
> verstuurt en werkt aantoonbaar.

---

## Versiedrift

Het wire-formaat in deel 1 is stabiel gebleven over de hele v1.1x-reeks. De
companion-codes in deel 2 niet: de lijst hierboven bevat gaten (44–49, 53) waar
codes verwijderd zijn, items gemarkeerd als "v8+" die oudere firmware weigert,
`_V3`-responsvarianten die naast hun voorgangers zijn toegevoegd, en minstens één
hernoemde constante (`CMD_GET_BATT_AND_STORAGE`, voorheen
`CMD_GET_BATTERY_VOLTAGE`).

Voor je op een specifieke code vertrouwt, controleer je die tegen
`examples/companion_radio/MyMesh.cpp` in de firmwareversie die je werkelijk
draait. `CMD_APP_START` draagt precies daarom een protocolversiebyte.

## Ruwe pakketten doorsturen

**Ruwe pakketten doorsturen over MQTT** wordt meegeleverd: de node codeert elk
ontvangen over-the-air frame in hex en publiceert het, zodat een server deel 1 van
dit document kan toepassen zonder dat de firmware iets hoeft te parsen. De
archiefpagina en de heatmap van de paden zijn erop gebouwd. Zie
[`mqtt.md`](mqtt.md).
