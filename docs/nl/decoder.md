# De pakketdecoder

*[English](../decoder.md)*

`server/app/packets.py` maakt van een ruw MeshCore-frame een dict. Het is een
pure functie zonder I/O, en het bevat de enige opgeschreven kopie van het
draadformaat die dit project heeft — teruggehaald uit de firmware, en dure kennis
om opnieuw te verwerven.

[`protocol.md`](../protocol.md) specificeert het formaat zelf. Dit document gaat
over wat de *decoder* eruit haalt, wat hij weigert, en waarom hij dat weigert.

## `decode()` werpt nooit een uitzondering

```python
pkt = packets.decode(frame_bytes)
```

Radioruis en firmwareverschillen leveren regelmatig afgekapte of onzinnige
frames op, en één slecht pakket mag de MQTT-abonnee niet kunnen platleggen. Dus:
wat **met zekerheid** te lezen viel wordt teruggegeven, de rest is simpelweg
afwezig, en `error` bevat een notitie over wat het tegenhield. De dict heeft
altijd minstens `len` en `ok`.

| Sleutel | Aanwezig wanneer | Inhoud |
|---|---|---|
| `len` | altijd | Framelengte in bytes |
| `ok` | altijd | Waar zodra header, pad en payloadgrens alle drie zeker zijn |
| `error` | bij elke weigering | Waarom het parsen stopte |
| `route`, `route_name` | header gelezen | 0–3 / `TRANSPORT_FLOOD`, `FLOOD`, `DIRECT`, `TRANSPORT_DIRECT` |
| `payload_type`, `payload_name` | header gelezen | 0–15 / `ADVERT`, `TXT_MSG`, … of `TYPE<n>` |
| `version` | header gelezen | Protocolversie, 0–3 |
| `transport_codes` | gescoopte routes | `[code0, code1]`, elk uint16 LE |
| `scope` | routetype bekend | `unscoped`, `scoped` of `share` |
| `scope_region` | `codes[1] != 0` | Het regionummer, kaal |
| `path_len` | beschrijver aanvaard | Aantal hophashes |
| `path_hash_size` | beschrijver aanvaard | 1, 2 of 3 bytes per hop |
| `path` | beschrijver aanvaard | Lijst hexteksten |
| `payload_len` | payload aanvaard | Bytes na het pad |
| `hash` | payload aanvaard | 16 hextekens, zie onder |
| `dest_hash`, `src_hash` | payloadtype draagt ze | Elk twee hextekens |
| `pubkey`, `sender`, `advert_ts` | ADVERT | Volledige sleutel, eerste 3 bytes, nodetijdstempel |
| `node_type`, `lat`, `lon`, `feat1`, `feat2`, `name` | ADVERT, per vlag | Advert-app_data |

`payload_name` valt terug op `TYPE<n>` in plaats van weggelaten te worden: een
payloadtype dat deze firmware niet kent, is nog steeds een feit over het mesh.

## De vijf toelatingsregels

Deze bytes komen **niet** voorgevalideerd binnen. De node spiegelt ze uit
`logRxRaw()`, die `Dispatcher::checkRecv()` aanroept op *alles* wat de radio
aanreikt (`src/Dispatcher.cpp` regel 199) — en pas daarna draait hij
`tryParsePacket()` (regel 205) en gooit het frame weg als dat mislukt.

Deze stroom bevat dus frames die geen enkele MeshCore-node ooit aanvaard heeft,
en een decoder die toegeeflijker is dan de firmware zou ze als feit presenteren.
Onderstaande regels zijn daarom die van de firmware zelf, uit `tryParsePacket()`
en `Packet::isValidPathLen()`:

| # | Weigering | `error` | Bron |
|---|---|---|---|
| 1 | Payloadversie hoger dan `PAYLOAD_VER_1` (0) | `unsupported protocol version <n>` | Geweigerd vóór al de rest |
| 2 | Padhashgrootte 4 (beschrijverbits 6–7 = 3) | `reserved path hash size 4` | `isValidPathLen()`: "if (hash_size == 4) return false" |
| 3 | `aantal × grootte` boven `MAX_PATH_SIZE` (64) | `path of <n> bytes exceeds MAX_PATH_SIZE` | `MeshCore.h` |
| 4 | Een pad dat niet in het frame past | `truncated path` | |
| 5 | Een payload boven `MAX_PACKET_PAYLOAD` (184) | `payload of <n> bytes exceeds MAX_PACKET_PAYLOAD` | `MeshCore.h` |

Elke weigering wordt gemeld met wat er **daarvoor** zeker was, en er wordt nooit
voorbij geraden. Een verkeerde hashgrootte verschuift elke byte na de
beschrijver, dus doorgaan zou een pad, een payloadgrens en een adreshash tegelijk
verzinnen.

Regel 2 heeft een tweede gevolg dat het waard is uit te spellen: als de
beschrijver geweigerd wordt, wordt **noch** `path_len` **noch** `path_hash_size`
gepubliceerd. Een padlengte gelezen uit een byte die we niet vertrouwen, is een
gok in de kleren van een getal.

Twee andere afkapweigeringen staan buiten die tabel, om dezelfde reden:
`truncated transport codes` (het routetype belooft codes en het frame is te kort
— waarbij `scope` afwezig blijft in plaats van tussen `scoped` en `share` te
kiezen) en `missing path descriptor`.

## Draadindeling, zoals de decoder hem afloopt

```
byte 0   header
           bits 0-1  routetype    0 = TRANSPORT_FLOOD
                                  1 = FLOOD
                                  2 = DIRECT
                                  3 = TRANSPORT_DIRECT
           bits 2-5  payloadtype
           bits 6-7  protocolversie (alleen 0 bestaat vandaag)

als routetype 0 of 3 is:
  bytes 1-4   twee transportcodes, elk uint16 little-endian

volgende byte  padbeschrijver
           bits 6-7  hashgrootte - 1, dus grootte = (byte >> 6) + 1
           bits 0-5  aantal hashes = byte & 63
           gevolgd door (aantal * grootte) bytes padhashes

de rest   payload, per payloadtype geïnterpreteerd
```

## Hashgroottes: twee verschillende velden

Het frame draagt twee dingen die een hash heten, en ze worden door verschillende
regels bepaald. Ze verwarren is de reden dat een node "1 byte" kan lijken op een
mesh waarvan de repeaters op 2 ingesteld staan.

**Padhopvermeldingen** zijn 1, 2 of 3 bytes, **per pakket**, uit de bovenste twee
bits van de beschrijver. De *afzender* kiest de grootte uit zijn eigen
voorkeur `hash_mode` (`path_hash_mode`, `src/helpers/CommonCLI.h` regel 68) en
elke doorstuurder houdt hem aan — `Mesh::routeRecvPacket()` voegt zijn eigen hash
toe op precies `getPathHashSize()` bytes. Het is dus een eigenschap van de node
die het pakket **verzond**, niet van het mesh, en het is uit het frame af te
lezen als `path_hash_size`.

**`dest_hash` en `src_hash` in de payload** zijn altijd **één** byte, wat het pad
ook gebruikt. Ze worden geschreven met de compileerconstante `PATH_HASH_SIZE`
= 1 (`src/Mesh.cpp` regel 462, `src/Identity.h` regels 19–26). De payloadversie
in de header is wat dat ooit zou veranderen: `PAYLOAD_VER_1` staat gedocumenteerd
als "1-byte src/dest hashes, 2-byte MAC" en `PAYLOAD_VER_2` als een toekomstige
"eg. 2-byte hashes, 4-byte MAC" (`src/Packet.h` regels 34–37). Geen enkele
firmware implementeert versie 2, en regel 1 hierboven weigert hem. Een repeater
die op padhashes van 2 bytes staat, adresseert zijn buren dus nog steeds met één
byte, en dat zeggen is niet de decoder die pessimistisch doet — het is het
draadformaat.

`path_hash_size` wordt gemeld en niet stilzwijgend verbruikt, omdat het het
verschil is tussen "deze hop noemt een van 256 nodes" en "een van 65 536", en een
lezer die een kale hop van twee hextekens ziet, kan onmogelijk weten naar welke
van de twee hij kijkt. Het is de reden dat `GET /api/v1/packets/{id}` hem
teruggeeft, en `null` teruggeeft als het frame niet bewaard is.

## Adreshashes per payloadtype

| Payloadtypes | Wat er genomen wordt |
|---|---|
| `REQ` (0), `RESPONSE` (1), `TXT_MSG` (2), `PATH` (8) | `dest_hash` = byte 0, `src_hash` = byte 1 |
| `ANON_REQ` (7) | Alleen `dest_hash` — de bron is een efemere sleutel die met opzet op geen enkel contact past |
| `ADVERT` (4) | Geen van beide; de payload noemt zijn afzender voluit |
| `GRP_TXT` (5), `GRP_DATA` (6) | Geen van beide; die beginnen met een kanaalhash |
| `ACK` (3) | Geen van beide; die begint met een CRC |
| al de rest | Geen van beide |

`_HASHED_PEER_TYPES = (0, 1, 2, 8)` is de lijst, en die bestaat omdat die vier
beginnen met `dest_hash(1) + src_hash(1) + MAC(2)` — zie
[`protocol.md`](../protocol.md#16-payload-layouts-by-type) §1.6.

Eén byte identificeert op zichzelf niemand: 256 bakjes tegen een heel mesh.
Vergeleken met een bekende contactenlijst is het meestal genoeg, en dat
vergelijken is de taak van de lezer en niet van de decoder — zie
[`candidates.md`](candidates.md).

## Scoping

De twee transportcodes zijn hoe MeshCore floodverkeer binnen een regio houdt, dus
**hun loutere aanwezigheid** beantwoordt al de vraag "was dit pakket gescoopt?".
Dat is wat `scope` meldt, en het wordt alleen door het routetype bepaald:

| `scope` | Wanneer | Wat het betekent |
|---|---|---|
| `unscoped` | Routetype FLOOD of DIRECT | Helemaal geen transportcodes op de draad |
| `scoped` | TRANSPORT_FLOOD / TRANSPORT_DIRECT, minstens één code niet nul | De afzender beperkte het tot een regio |
| `share` | Beide codes nul | Geen regio maar een markering |

Bij een FLOOD betekent `unscoped` wat er staat: niets houdt het pakket binnen een
regio. Bij een DIRECT betekent het alleen dat het veld ontbreekt — een direct
pakket wordt langs een expliciete hoplijst gestuurd in plaats van geflood, en de
firmware vraagt niet eens tot welke regio het behoort (`MyMesh::onRecvPacket()`
zet `recv_pkt_region = NULL` voor elke niet-floodroute,
`examples/simple_repeater/MyMesh.cpp` regel 794).

`share` is `isShare()` in de repeaterfirmware: de codes `{0, 0}` lezen als
"stuur naar nergens", de vorm die een advert heeft wanneer die via de
Share-functie van de app geïmporteerd is in plaats van uit de lucht gehoord. De
repeater houdt zo'n advert daarom uit zijn burentabel, en hem hier op één hoop
gooien met echt gescoopt verkeer zou het ene geval verbergen waarin een advert
met nul hops **niet** betekent "deze node is in bereik".

### Welke regio — en waarom de decoder niet raadt

Staat niet in het frame, en deze module verzint hem niet:

- `transport_codes[0]` is `TransportKey::calcTransportCode(pkt)` — een MAC over
  het pakket, berekend met de scopesleutel van 16 byte, dus hij verschilt voor
  elk pakket dat onder diezelfde sleutel verstuurd wordt. Hij duidt op zichzelf
  geen regio aan; hij is alleen herkenbaar voor een node die de sleutel bezit en
  hem herberekent.
- `transport_codes[1]` is gereserveerd voor de thuisregio van de afzender en is
  het enige veld dat er een zou kunnen noemen. De companionfirmware schrijft daar
  een letterlijke nul (`codes[1] = 0;  // REVISIT: set to 'home' Region`), dus in
  de praktijk is hij vrijwel altijd afwezig. Hij wordt als `scope_region` gemeld
  wanneer hij niet nul is, als het kale getal dat hij is — de tabel van nummer
  naar naam woont in de regiokaart van het mesh, niet op de draad.

De regio van een gescoopt pakket benoemen moet dus gebeuren door een node die de
scopesleutels heeft, en naast het frame gepubliceerd worden. Zie
[`protocol.md`](../protocol.md#13-transport-codes) §1.3.

### Een grotendeels ongescoopt mesh is de verwachte lezing

Twee firmwarefeiten beslechten dat, en ze zijn het waard te bewaren, want "waarom
is alles ongescoopt?" is de eerste vraag die iemand over de scopekolom stelt.

**Doorsturen voegt nooit een scope toe.** `Mesh::routeRecvPacket()` voegt een
padhash toe en niets anders, en `Dispatcher::checkSend()` zendt
`transport_codes` byte voor byte opnieuw uit. De codes op een frame zijn die van
de *oorspronkelijke afzender*, hoeveel hops terug dat ook was.

**De eigen standaardregio van een repeater bereikt alleen pakketten die hij zelf
begint** (`sendFloodScoped(default_scope, …)`) en antwoorden waarvan het verzoek
al gescoopt was (`MyMesh::sendFloodReply()`,
`examples/simple_repeater/MyMesh.cpp` regel 642).

Een regio instellen op één node kan dus onmogelijk de ongescoopte floods van
iemand anders gescoopt laten lijken, en het aandeel `unscoped` in het archief is
een meting van het mesh en niet van deze module.

## De payloadhash

```python
out["hash"] = hashlib.sha1(bytes([payload_type]) + payload).hexdigest()[:16]
```

De **payload**, niet het hele frame — want een geflood pakket krijgt er bij elke
hop padhashes en transportcodes bij, dus alleen de payload blijft stabiel over
herhalingen van wat eigenlijk hetzelfde bericht is. Het payloadtype gaat ervoor,
zodat twee verschillende types niet op identieke bytes kunnen botsen.

Die stabiliteit is wat het ontdubbelen in `db.insert_packet()` laat werken:
dezelfde `(observer, phash)` binnen `PACKET_DUP_WINDOW_S` (60 s) is één ontvangst
en geen meerdere. Zonder dat zou de live kaart een geflood advert één keer per
node in bereik tonen.

## ADVERT

```
pubkey(32) + tijdstempel(uint32 LE) + handtekening(64) + app_data
```

`_ADVERT_APP_DATA_OFFSET` is dus 100. `sender` is de eerste
`_SENDER_PREFIX_BYTES` = 3 bytes van de sleutel, in hex — zes tekens, want
contacten zijn overal in de app op die vorm gesleuteld.

`app_data` begint met een vlaggenbyte; de optionele velden die volgen verschijnen
in precies deze volgorde, alleen als hun bit gezet is:

| Bit | Veld |
|---|---|
| `flags & 0x0f` | Nodetype: 1 = chat, 2 = repeater, 3 = room, 4 = sensor |
| `flags & 0x10` | `lat` en `lon`, elk int32 LE, in micrograden |
| `flags & 0x20` | `feat1`, uint16 LE |
| `flags & 0x40` | `feat2`, uint16 LE |
| `flags & 0x80` | `name`: alle resterende bytes, UTF-8 |

Drie details die niet vanzelf spreken:

**Alleen de eerste `MAX_ADVERT_DATA_SIZE` (32) bytes van `app_data` bestaan** wat
het mesh betreft. `Mesh::onRecvPacket()` begrenst `app_data_len` tot 32 voordat
hij de Ed25519-handtekening controleert, en `AdvertDataParser` ziet er nooit
meer. Alles daarvoorbij valt **buiten de handtekening**, dus daar een naam uit
lezen zou tekst tonen die geen enkele node gecontroleerd heeft en die elke
doorstuurder had kunnen toevoegen.

**0/0 is geen positie.** Firmware stuurt dat als er geen ingesteld is, en dat is
de Atlantische Oceaan op een kaart, dus het geldt als onbekend in plaats van
uitgetekend te worden. Coördinaten buiten ±90 / ±180 vallen op dezelfde manier
af.

**De naam wordt afgekapt op 64 tekens** en bij de eerste NUL afgesneden,
gedecodeerd met `errors="replace"`. Een kale advert zonder `app_data` is geldig,
alleen weinigzeggend, en keert vroegtijdig terug.

## Wat de ingestweg met het resultaat doet

`db.insert_packet()` schrijft de gedecodeerde velden in `packets`, en doet nog
één ding: als het pakket een ADVERT met een `pubkey` is, roept hij
`db.upsert_advert()` aan om de tabel `contacts` te verversen. Dat is wat de live
kaart later in staat stelt een pakket überhaupt te plaatsen — de decoder voedt de
identiteitsopslag, en die opslag voedt elke resolutie die de site uitvoert.

## Tests

`server/tests/test_packets.py` dekt deze module: scope-indeling, adreshashes per
payloadtype, ADVERT-velden, padparsing en elke afkapping.
`server/tests/frames.py` bouwt MeshCore-frames uit de protocolkennis in
`docs/protocol.md` §1; **er staat geen enkel echt, opgevangen pakket in de
testmap**.

## Verwante documenten

| Vraag | Document |
|---|---|
| Het draadformaat zelf, byte voor byte | [`protocol.md`](../protocol.md) |
| Bij welke node een hash hoort | [`candidates.md`](candidates.md) |
| Waar de gedecodeerde velden bewaard worden | [`database.md`](database.md#packets) |
| Hoe een gedecodeerd pakket geserveerd wordt | [`api.md`](api.md#get-apiv1packetspacket_id) |
