# Woordenlijst

*[English](../glossary.md)*

MeshCore-vocabulaire, zoals MeshStats het gebruikt. Elk lemma zegt wat het woord
betekent, waar het vandaan komt, en — waar dat uitmaakt — wat het **niet**
betekent. De definities op byteniveau staan in [`protocol.md`](protocol.md);
deze pagina is de korte versie die je naast de site open kunt houden.

De woorden staan gegroepeerd naar wat ze beschrijven en niet op alfabet, omdat
een aantal ervan alleen naast elkaar betekenis heeft. Onderaan staat een
[alfabetisch register](#alfabetisch-register).

---

## Nodes en rollen

### Node

Elk MeshCore-apparaat op het mesh. Zijn identiteit is een **Ed25519-sleutelpaar**;
de publieke sleutel is in elke technische zin de naam van de node. Al het andere
— de weergavenaam, de locatie, de rol — wordt geadverteerd en kan veranderd of
vervalst worden. De sleutel niet.

### Repeater

Een node die het verkeer van anderen doorgeeft. Firmware
`examples/simple_repeater`. Hij heeft geen chat-interface, houdt tellers bij van
wat hij doorstuurde, en beantwoordt over LoRa een kleine CLI voor wie inlogt met
zijn beheerderswachtwoord. Repeaters zijn datgene waarover MeshStats een
statistiekensite *is*.

### Companion

Een node die aan een app of computer gekoppeld is, via USB-serieel, BLE of
TCP/WiFi. Firmware `examples/companion_radio`. Dit is de node die het mesh
*hoort* én een weg naar het internet heeft, en dus in MeshStats de node die
publiceert. Het protocol dat hij met zijn app spreekt staat in
[`protocol.md` §2](protocol.md#2-the-companion-protocol-tcp-and-serial).

Standaard companion-firmware accepteert **één** TCP-client tegelijk. Precies die
beperking is de reden dat zowel [`proxy.md`](proxy.md) als de
`SerialWifiInterface`-aanpassing in [`firmware.md`](firmware.md) bestaan.

### Monitor

Een node die de inloggegevens van één of meer repeaters heeft gekregen en er
namens het mesh naar omkijkt: hij logt over LoRa in, vraagt hun instellingen op
en controleert of zet hun klok. Een repeater met een monitor heet **doorgestuurd**
(*relayed*) — de site praat er nooit rechtstreeks mee, maar vraagt het aan de
monitor.

Het onderscheid is in de code zichtbaar als `commanding.is_relayed(rep)` en
bepaalt het antwoord van `clocksync.time_route()` in `server/app/clocksync.py`:
een doorgestuurde repeater krijgt zijn tijd van zijn monitor over LoRa, een
niet-doorgestuurde rechtstreeks van de site over MQTT.

### Waarnemer (observer)

De node wiens radio een bepaald pakket daadwerkelijk gehoord heeft. Elke rij in
het pakketarchief heeft er één. Het is niet de afzender en niet de bestemming —
het is de getuige. Twee waarnemers die hetzelfde pakket horen leveren twee rijen
op met dezelfde pakkethash en een verschillende SNR, en precies dat maakt een
linkkaart mogelijk.

---

## Pakketten en routering

### Advert

Payloadtype `ADVERT` (`0x04`). Een node die zichzelf aankondigt: publieke
sleutel, tijdstempel, handtekening, en optionele extra's (naam, locatie,
nodetype). Het is **Ed25519-ondertekend**, waarmee een advert het enige
pakkettype is waarvan het auteurschap te verifiëren valt zonder enig gedeeld
geheim.

Adverts zijn de bron van de nodenamen en kaartposities in MeshStats. Volledige
byte-indeling: [`protocol.md` §1.6](protocol.md#advert-0x04).

### Flood

Routetype `FLOOD`. Het pakket wordt heruitgezonden door elke repeater die het
hoort en nog niet eerder zag. Elke doorgever **plakt** zijn eigen sleutelprefix
achter het pad, zodat het pad groeit naarmate het pakket naar buiten reist; bij
aankomst leest het als de route *terug* naar de afzender.

### Direct

Routetype `DIRECT`. Het pakket draagt een expliciete lijst hops en wordt daar
langs gestuurd (*source routing*). `path[0]` is de volgende hop; een node stuurt
alleen door als die ingang met zijn eigen sleutelprefix overeenkomt, en
**verwijdert** hem dan. Het pad krimpt terwijl het pakket reist.

Praktisch gevolg: een floodpad van links naar rechts gelezen is verleden tijd,
een directpad van links naar rechts gelezen is toekomst.

### Hop

Eén doorgever op het pad van een pakket. Op de draad is een hop **geen**
identificatie en **geen** digest — het zijn de eerste *n* bytes van de
Ed25519-publieke sleutel van die doorgever, letterlijk gekopieerd
(`Identity::copyHashTo()`, een gewone `memcpy`).

Bij de gebruikelijke grootte van 1 byte bestaan er 256 mogelijke hopwaarden. In
een mesh van enkele honderden nodes luisteren meerdere nodes naar dezelfde
waarde. De site behandelt "welke node is deze hop?" daarom als een vraag met een
*verzameling* antwoorden, gewogen op bewijs — zie
[`contributing.md`](contributing.md#1-eerlijkheid-over-onzekerheid) en
`server/app/candidates.py`.

### Padhash-grootte

Hoeveel bytes elke hop-ingang inneemt: 1, 2 of 3 (4 is gereserveerd en wordt
geweigerd). Dat wordt **per pakket bepaald door wie het als eerste verstuurde**,
uit diens `hash_mode`-CLI-instelling, en elke doorgever laat het staan. Het is
dus geen eigenschap van het mesh of van een firmwareversie: grootte 1, 2 en 3
reizen naast elkaar door dezelfde ether.

MeshStats leest het van het frame af en rapporteert het als `path_hash_size`.
Details en de bitindeling: [`protocol.md` §1.4](protocol.md#14-the-path-field).

### Adreshash

De bron- en bestemmingshash van 1 byte **binnen een versleutelde payload** — niet
in het pad. Vastgelegd op één byte door `PATH_HASH_SIZE` en door de definitie van
`PAYLOAD_VER_1`.

Dit is het lemma dat het vaakst omgedraaid wordt: **de padhash-grootte en de
adreshash-grootte staan los van elkaar.** Een node die op `hash_mode 2` staat zet
hops van twee bytes in het pad en adresseert zijn peers *nog steeds* met één
byte. Precies daarom benoemt de pakketdetailpagina welke van de twee hij toont,
en hoe groot die is.

### Transportcodes

Vier bytes die aanwezig zijn **dan en slechts dan als** het routetype
`TRANSPORT_FLOOD` (`0x00`) of `TRANSPORT_DIRECT` (`0x03`) is. Dit is het enige
veld in de header dat er soms wel en soms niet staat, dus een decoder die na
byte 1 een vaste offset aanneemt, staat bij elk scoped pakket vier bytes uit de
maat.

- `codes[0]` wordt berekend uit een scopesleutel van 16 byte **en het pakket**,
  en verschilt dus voor elk pakket dat onder één en dezelfde sleutel verstuurd
  wordt. Alleen een node die de sleutel heeft, herkent hem — door hem opnieuw te
  berekenen.
- `codes[1]` is gereserveerd voor de thuisregio van de afzender; de firmware
  schrijft er een letterlijke nul in.

De aanwezigheid van de codes bewijst dus dat een pakket scoped was. De codes zelf
noemen de regio **niet** — die valt niet uit de bytes in de ether terug te
halen.

### Scoped / unscoped / share

De drie waarden die MeshStats in de kolom `scope` rapporteert
(`server/app/packets.py`):

| Waarde | Betekenis |
|---|---|
| `scoped` | Transportcodes aanwezig — verstuurd onder een of andere scopesleutel |
| `unscoped` | Geen transportcodes op de draad |
| `share` | Transportcodes aanwezig en allebei nul |

`share` is een bewuste markering, geen ontaard geval: `{0, 0}` is de vorm van een
advert dat via de Share-functie van de app geïmporteerd werd in plaats van uit de
ether gehoord. `calcTransportCode()` reserveert beide eindwaarden, dus een echte
scopesleutel kan nooit `codes[0] == 0` opleveren.

Twee waarschuwingen bij het lezen hiervan:

- **Een mesh leest als overwegend `unscoped`, ook als zijn repeaters een regio
  ingesteld hebben.** Een repeater scopet alleen wat hij zelf uitzendt en zijn
  antwoorden op scoped verzoeken; alles wat hij voor anderen doorstuurt gaat
  ongewijzigd door. De regio-instelling van één node zegt niets over het verkeer
  dat er langskomt.
- **Op een DIRECT-rij betekent `unscoped` "niet van toepassing", niet "los in het
  wild".** Een direct pakket wordt langs een expliciet pad gestuurd, en de
  firmware vraagt nooit bij welke regio het hoort.

### Pakkethash

De identiteit van een pakket voor ontdubbeling, afgeleid uit zijn inhoud. Twee
waarnemers die dezelfde uitzending horen, rapporteren dezelfde hash — en dat is
wat de site toelaat ze samen te vouwen tot één pakket met twee getuigen in plaats
van twee pakketten.

---

## Woorden van de site

| Term | Betekenis |
|---|---|
| **Prefix** / `pubkey_prefix` | De eerste hextekens van de publieke sleutel van een node, gebruikt als korte identiteit. Verschillende bronnen spellen hem verschillend lang — de `meshcore`-HA-integratie vijf bytes, de eigen firmware van een node zes — en daarom is het matchen prefix-tolerant tot 8 hextekens en niet verder (`MIN_PREFIX_MATCH`). |
| **Slug** | De URL-veilige naam van een repeater op de site: `/r/<slug>`. |
| **Snapshot** | Eén `POST /api/v1/ingest`-body: één repeater, zijn huidige metrics, zijn buren. |
| **Buur (neighbour)** | Een andere node die deze repeater rechtstreeks gehoord heeft, met de SNR waarop dat gebeurde. De grondstof van de linkkaart. |
| **Hartslag (heartbeat)** | Een afgedwongen grafiekpunt dat ook geschreven wordt als er niets veranderde, zodat een vlakke lijn zichtbaar vlak is in plaats van afwezig (`MCS_HEARTBEAT_MIN`). |
| **Kloksynchronisatie** | De site die een node vertelt hoe laat het is — rechtstreeks over MQTT, of via de monitor van die node over LoRa. `server/app/clocksync.py`. |
| **Instellingenopvraging** | Een repeater vragen zijn eigen CLI-instellingen terug te lezen. Alleen-lezen: de site kan waarden opvragen, nooit schrijven. |
| **Facet** | Een "meestvoorkomende waarden"-uitsplitsing voor een doorzoekbaar veld in het pakketarchief. |

---

## Radiometingen

| Term | Betekenis |
|---|---|
| **SNR** | Signaal-ruisverhouding in dB, zoals de ontvangende radio hem meldde. LoRa decodeert ruim onder 0 dB, dus een negatieve SNR is normaal en geen storing. |
| **RSSI** | Ontvangen signaalsterkte in dBm. Altijd negatief; dichter bij nul is sterker. |
| **Ruisvloer (noise floor)** | Het achtergrondniveau dat de radio meet als er niets uitgezonden wordt. |
| **Zendtijd (airtime)** | Hoelang de radio werkelijk zond of ontving. De grootheid die telt voor duty-cycle-limieten, en de reden dat een spraakzame node een probleem van het hele mesh is en niet alleen van zichzelf. |

---

## Alfabetisch register

[Adreshash](#adreshash) ·
[Advert](#advert) ·
[Buur](#woorden-van-de-site) ·
[Companion](#companion) ·
[Direct](#direct) ·
[Facet](#woorden-van-de-site) ·
[Flood](#flood) ·
[Hartslag](#woorden-van-de-site) ·
[Hop](#hop) ·
[Instellingenopvraging](#woorden-van-de-site) ·
[Kloksynchronisatie](#woorden-van-de-site) ·
[Monitor](#monitor) ·
[Node](#node) ·
[Padhash-grootte](#padhash-grootte) ·
[Pakkethash](#pakkethash) ·
[Prefix](#woorden-van-de-site) ·
[Repeater](#repeater) ·
[RSSI](#radiometingen) ·
[Ruisvloer](#radiometingen) ·
[Scoped](#scoped--unscoped--share) ·
[Share](#scoped--unscoped--share) ·
[Slug](#woorden-van-de-site) ·
[Snapshot](#woorden-van-de-site) ·
[SNR](#radiometingen) ·
[Transportcodes](#transportcodes) ·
[Unscoped](#scoped--unscoped--share) ·
[Waarnemer](#waarnemer-observer) ·
[Zendtijd](#radiometingen)

---

## Waar nu heen

| Je wilt | Lees |
|---|---|
| De bytes, exact | [`protocol.md`](protocol.md) |
| Hoe de onderdelen samenhangen | [`architecture.md`](architecture.md) |
| Waarom de code eruitziet zoals hij eruitziet | [`contributing.md`](contributing.md) |
| Al het andere | [`README.md`](README.md) |
