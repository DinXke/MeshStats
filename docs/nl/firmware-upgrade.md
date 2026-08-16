# Firmware-upgrades

*[English](../firmware-upgrade.md)*

Hoe een repeater die `MeshManagerNet` draait aan een nieuw image komt: waar dat
image vandaan komt, naar welke node het toe mag, wat er gecontroleerd wordt
voordat er iets definitief wordt, en hoe je terugkomt als het een vergissing was.

De firmware op de node zelf staat beschreven in
[`firmware.md`](firmware.md); deze pagina gaat alleen over het vervangen
ervan.

---

## De korte versie

1. Een git-tag bouwt de firmware in GitHub Actions en publiceert één `.bin` per
   board, elk met een `.sha256`, plus de changelog als release notes.
2. De site toont die releases op zijn beheerpagina, per node, met de
   geïnstalleerde versie ernaast.
3. Op upgrade drukken laat de **server** het image downloaden, zijn digest
   controleren, en het over HTTP naar de node duwen.
4. De **node** controleert diezelfde digest nog een keer, en wisselt pas daarna
   van bootpartitie en herstart. Bij een fout blijft hij precies draaien wat hij
   draaide.
5. Gedraagt het nieuwe image zich slecht, dan staat het vorige nog in flash:
   `POST /api/fw/rollback`, of `wifi fw rollback` over het mesh.

---

## Waarom er twee upgradepaden zijn, en waarom het oude blijft

De node heeft al lange tijd een upgradepagina op `/update` (AsyncElegantOTA).
Die is er nog steeds, en is niet afgeschreven. Maar het kan niet het pad zijn
dat de site aanstuurt, om een reden die gemeten is en niet gegokt.

Op echte hardware werd een upload van **1.284.538 bytes**, verstuurd als
`-F "update=@firmware.bin"` — de veldnaam waar je van nature naar grijpt, en
zonder `MD5`-veld — volledig aangenomen, weggegooid, en gevolgd door een
herstart op de **oude** firmware. De aanroeper zag `HTTP 000`. Dezelfde binary,
verstuurd als `-F "MD5=<md5>" -F "file=@firmware.bin;filename=firmware.bin"`,
werkte wel.

Drie eigenschappen komen daarin samen:

| Eigenschap | Gevolg |
|---|---|
| Het image wordt alleen herkend onder de multipart-veldnaam `file`, met een `MD5`-veld ernaast | De verkeerde veldnaam is een stille weggooi, geen fout |
| De handler herstart de node of `Update.end()` nu slaagde of niet | "Hij is herstart" komt er bij succes en bij mislukking identiek uit |
| De herstart gebeurt voordat het HTTP-antwoord is weggeschreven | Er valt niets te lezen; `curl` meldt `000` |

Het enige waarneembare signaal draagt dus geen informatie. Op een repeater op een
dak is dat geen ruw randje — het is een upgradepad dat liegt.

**En het blijft toch.** Het is de terugvaloptie voor wanneer juist het nieuwe pad
stuk is, en een herstelroute mag nooit afhangen van datgene waarvan je aan het
herstellen bent. Dat is dezelfde regel die het mesh-commando `start ota` zijn
standaard soft-AP-gedrag teruggaf, nadat een eerdere release het had vervangen
door een bericht dat naar `/update` wees.

De volledige ladder van terugvalopties, gemakkelijkste eerst:

| Wanneer | Route | Werkt nog als |
|---|---|---|
| Normaal | `POST /api/fw` vanaf de site | — |
| Nieuw pad stuk | `POST /update` met `MD5=` en `file=`, met de hand | de module nog draait |
| Nieuw image komt niet op WiFi | `wifi fw rollback` via de mesh-CLI | LoRa werkt |
| Module heeft zichzelf uitgeschakeld (6 herstarts) | `start ota` via de mesh-CLI, daarna de soft-AP | MeshCore opstart |
| Niets werkt | USB | je bij de node kunt |

---

## De endpoints op de node

Alle drie zitten achter dezelfde HTTP-login als de rest van de beheerpagina
(`_cfg.user` / consolewachtwoord). Wie hier firmware kan schrijven, kan via
`/api/backup` ook de private sleutel downloaden, dus een lichtere laag bestaat
niet.

### `POST /api/fw?sha256=<hex>&size=<bytes>&ver=<label>`

Het image is de **rauwe request-body**. Niet multipart — de bug hierboven was een
multipart-veldnaam, en een formaat zonder veldnamen kan die niet hebben.

| Parameter | Verplicht | Betekenis |
|---|---|---|
| `sha256` | ja | 64 hextekens, de digest van het hele image |
| `size` | nee | verwacht aantal bytes; wordt geweigerd vóórdat de partitie gewist wordt als het niet overeenkomt met `Content-Length` |
| `ver` | nee | een label voor de logging, bijv. `1.12.0`. Wordt nergens voor vertrouwd |

De volgorde van handelingen is de garantie:

1. Authenticeren. Een weigering antwoordt `401` en leest geen image.
2. De parameters valideren. Een foute digest-string sneuvelt hier, voordat er
   flash aangeraakt wordt.
3. `Update.begin(size)` — die ook een image weigert dat groter is dan de partitie
   en de ESP32-image-magicbyte controleert, zodat een HTML-foutpagina die als
   `.bin` opgeslagen is meteen faalt met een leesbare reden.
4. Streamen naar de **inactieve** applicatiepartitie, terwijl er gehasht wordt.
   Dit verandert niets aan wat er opstart.
5. De digest vergelijken. Bij een verschil: `Update.abort()`, en de node draait
   gewoon verder op de firmware waarmee hij opstartte.
6. Pas nu `Update.end()`, die `otadata` schrijft. **Dit is de enige definitieve
   handeling, en hij wordt pas bereikt nadat de digest klopte.**
7. Antwoorden, en 1,5 s later herstarten — lang genoeg om het antwoord de deur
   uit te laten gaan.

**Alleen succes herstart.** Een mislukte schrijfactie laat een gezond systeem
draaien, en dat herstarten is precies de handeling die van een mislukte upgrade
een storing maakt.

Het antwoord is altijd leesbare JSON:

```json
{"ok":0,"step":"sha","msg":"checksum klopt niet na 1284538 van 1284538 bytes",
 "bytes":1284538,"total":1284538,"want":"ab12…","have":"cd34…",
 "from":"1.11.0","to":"1.12.0","env":"heltec_v4_repeater_meshmanager","reboot":0}
```

`step` is een van `auth`, `param`, `bezig`, `begin`, `write`, `sha`, `kort`,
`end`, `leeg`, of leeg bij succes.

### `GET /api/fw`

Wat er geïnstalleerd is, waar naartoe teruggegaan kan worden, en hoe de laatste
poging afliep.

```json
{"ver":"1.12.0","fw":"v1.17.0","env":"heltec_v4_repeater_meshmanager",
 "board":"Heltec V4.3 OLED","busy":0,"got":0,"total":0,
 "run":"app0","other":{"slot":"app1","valid":1,"ver":"1.11.0"},
 "last":{"any":1,"ok":1,"step":"","msg":"","bytes":1284538,"total":1284538}}
```

Met opzet achter de login: `env` plus `ver` is een boodschappenlijstje voor
iedereen die hier graag het verkeerde image zou schrijven.

### `POST /api/fw/rollback`

De andere partitie weer opstarten. Zie [hieronder](#teruggaan).

### `wifi fw` en `wifi fw rollback`

Dezelfde twee, over elke CLI — serieel, telnetconsole, **of het mesh**. De
mesh-vorm is degene die ertoe doet: een upgrade waarvan het enige mankement is
dat hij niet op de WiFi komt, neemt in één klap elke IP-route naar de node mee,
en LoRa komt op vanuit de radiodriver nog voordat een van die routes bestaat.

---

## Welk image bij welke node hoort

Dit is het deel waarmee je hardware kunt slopen. Een image dat voor een ander
board gebouwd is en naar een node op een dak geschreven wordt, valt niet over de
lucht te herstellen.

**De node meldt de PlatformIO-omgeving waaronder hij gebouwd is**, in
`MESHMANAGER_ENV`, gezet vanuit `$PIOENV` in `platformio.ci.ini`. Een release
draagt één asset per bouwomgeving, met de naam
`meshmanager-<env>-<version>.bin`. De site legt die twee **exact** naast elkaar, en
weigert wanneer dat niet lukt.

Het verworpen alternatief was matchen op de boardnaam die de node toch al meldt —
`"board":"Heltec V4.3 OLED"` in `/api/status`. Dat is vrije tekst die upstream
onderhouden wordt: hij kan verschillen tussen twee boards die dezelfde binary
aankunnen, overeenkomen tussen twee die dat niet doen, en door een
MeshCore-release geherformuleerd worden zonder dat iemand hier het merkt. De
env-naam is de exacte sleutel waaronder het image gebouwd is, dus image en node
komen wel of niet overeen, zonder oordeel daartussen.

**Een node die een lege `env` meldt, krijgt geen upgradeknop.** Dat is een image
van vóór 1.12.0, of een image dat zonder de vlag gebouwd is. De eerlijke uitkomst
is "niet vast te stellen welk image hier hoort", en niet een beste gok.

### Een board toevoegen

1. Voeg een `[env:...]`-sectie toe aan `firmware/platformio.ci.ini`. Laat
   `extends` naar de juiste variant uit MeshCore's `variants/`-boom wijzen, en
   behoud `-D MESHMANAGER_ENV='"$PIOENV"'`.
2. Dat is alles. De release-workflow leidt zijn build-matrix af uit de
   sectienamen in dat bestand.

Twee boards die dezelfde binary aankunnen krijgen alsnog hun eigen ingang zodra
hun env-namen verschillen, omdat een node de naam meldt waaronder hij gebouwd is
en niets anders.

### Boards die dit helemaal niet kunnen gebruiken

`MeshManagerNet` is een WiFi-module. Op een variant zonder WiFi wordt hij niet
meegecompileerd, is er geen HTTP van welke aard dan ook, en gaat deze pagina niet
op — die nodes worden over USB geüpgraded.

Een node mét de module maar **zonder IP-pad vanaf de server** kan evenmin
geüpgraded worden, en dat is voor minstens één node in dit project een blijvende
toestand in plaats van een tijdelijke. Zie de volgende sectie.

---

## Welke nodes geüpgraded kunnen worden

Firmware-upgrade is bewust **geen** onderdeel van het beheerniveau van een node
(`unmanaged` / `semi_managed` / `full_managed`). Een `full_managed` node kan
commando's over MQTT aannemen en toch geen image kunnen aannemen, omdat die twee
over verschillende dingen reizen. De site houdt het in een aparte sleutel.

| Node | Upgradebaar | Waarom |
|---|---|---|
| Eigen firmware, over IP bereikbaar vanaf de server, meldt een env | ja | er is een pad voor 1,3 MB en het image is geïdentificeerd |
| Eigen firmware, geen IP-pad vanaf de server | **nee** | zie hieronder |
| Eigen firmware, IP-pad, maar geen env gemeld | **nee** | image van vóór 1.12.0; niet te zeggen welk asset past |
| Standaard MeshCore, beheerd via een monitor over LoRa | **nee** | niet onze firmware, en sowieso geen pad |

**Waarom geen IP-pad blijvend "nee" betekent.** Een firmware-image is ~1,28 MB.
De enige andere route naar zo'n node is LoRa via een monitorende repeater, en bij
de radio-instellingen die deze nodes gebruiken (BW 62,5 kHz, SF 8) plus de
Europese duty-cycle-limiet komt dat neer op zendtijd in de orde van **dagen** —
volstrekt buiten alle verhouding, nog los van wat het met het mesh zou doen. Er
bestaat geen slimme codering die die orde van grootte verandert.

Een node die alleen via een monitor bereikt wordt, krijgt dus geen upgradeknop,
maar een uitleg. Dat is de situatie voor de dakrepeater waar dit project omheen
gebouwd is, en naar verwachting blijft dat maanden zo.

---

## Teruggaan

Een ESP32 met een `default_16MB`-partitietabel heeft **twee
applicatiepartities**, en een OTA wist nooit degene waar hij niet naartoe
schrijft. De firmware die de node vóór de laatste upgrade draaide staat dus nog
in flash, byte voor byte, en teruggaan is één `otadata`-schrijfactie en een
herstart. Geen download, geen radio, geen netwerk buiten het verzoek zelf.

```
POST /api/fw/rollback          # over HTTP
wifi fw rollback               # over serieel, console, of het mesh
```

De node weigert wanneer het andere slot geen geldig applicatie-image bevat — wat
het geval is op een node die alleen ooit over USB geflasht is, omdat er dan nog
niets in het tweede slot geschreven is. **De eerste upgrade via dit pad is dus
ook wat de eerste rollback mogelijk maakt.**

### Het gebeurt niet automatisch, met opzet

De verleidelijke versie is "drie mislukte boots, dan terugrollen", en deze
firmware heeft al een bootteller voor precies dat soort probleem. Het wordt
geweigerd omdat een zonnerepeater herstart om redenen die niets met firmware te
maken hebben: een lege cel in november laat het board drie keer in één nacht
brownouten, en een automatische rollback zou een goede upgrade stilletjes
terugdraaien en dat daarna blijven doen.

De bereikbaarheidsgarantie heeft het ook niet nodig. Drie herstarts laten de node
al in **veilige modus** (safe mode) vallen — zijn eigen accesspoint en de
beheerpagina, ongeacht wat het nieuwe image stukmaakte. Veilige modus is wat de
node bereikbaar houdt; rollback is wat hem repareert, en een reparatie is een
beslissing.

### Wat een rollback *niet* terugdraait

Alles buiten de applicatiepartitie. Configuratie, sleutels, de ACL, de
advert-cache en de monitorlijst staan allemaal op de datapartitie en worden door
zowel de upgrade als de rollback met rust gelaten — wat dezelfde reden is dat een
OTA je sleutels niet kwijtraakt.

Dat snijdt aan twee kanten bij een **downgrade** (een oudere release installeren
dan de draaiende). De opgeslagen bestanden zijn van nature vergevingsgezind:
`loadConfig()` vult eerst elke standaardwaarde in en laat het bestand daarna
overschrijven wat het herkent, dus een onbekende sleutel wordt genegeerd en een
ontbrekende sleutel houdt zijn standaardwaarde. Een ouder image leest een nieuwer
bestand dus zonder morren — het negeert simpelweg instellingen die het niet kent,
en die instellingen hebben geen effect meer totdat je weer upgradet.

Waar je wél op moet letten, is dat een instelling die ná de versie waar je naar
teruggaat is toegevoegd, vergeten lijkt te zijn, en uit het bestand geschreven
wordt zodra dat oudere image de volgende keer zijn configuratie opslaat. De site
waarschuwt daarom bij een stap omlaag, in plaats van hem stilzwijgend toe te
staan.

---

## Waar de images vandaan komen

Er valt niets te installeren als er niets gebouwd wordt, dus releases horen bij
deze functionaliteit en zijn geen los verhaal.

### Een release taggen

```bash
# MESHMANAGER_VERSION in MeshManagerNet.h must already say 1.12.0
git tag fw-v1.12.0
git push origin fw-v1.12.0
```

`.github/workflows/firmware-release.yml` doet dan het volgende:

1. **weigeren** als de tag en `MESHMANAGER_VERSION` niet overeenkomen — een release
   waarvan de assets een andere versie melden dan de release zelf, zou de site op
   zoek sturen naar een upgrade die iets anders installeert;
2. de build-matrix lezen uit de `[env:...]`-secties van
   `firmware/platformio.ci.ini`;
3. MeshCore uitchecken op de vastgezette `MESHCORE_REF`,
   `firmware/repeater-hooks.patch` erop toepassen, `firmware/src` en
   `firmware/examples` eroverheen kopiëren, en elke omgeving bouwen;
4. **het gebouwde `.bin` teruglezen** met `firmware/tools/verify_image.py` en
   falen als `MESHMANAGER_NAME`, `MESHMANAGER_VERSION` of `MESHMANAGER_ENV` er
   niet in staat;
5. per omgeving `meshmanager-<env>-<version>.bin` en `.sha256` publiceren;
6. het changelog-blok voor die versie, rechtstreeks uit `MeshManagerNet.cpp`,
   gebruiken als release notes. Eenmaal geschreven, tweemaal gepubliceerd.

Stap 3 en 4 zijn dezelfde les, twee keer geleerd. De hooks zijn aanpassingen
bínnen upstreams eigen `simple_repeater`-bestanden, dus een kopie van de
bestanden uit deze repository draagt ze niet mee; zonder die hooks compileert
`MeshManagerNet.cpp` niet, en dat is de luide versie van het probleem. De stille
versie is een ontbrekende `-D MESHMANAGER_NET`: alles compileert, de linker gooit
een module weg waar niemand naar verwijst, en er rolt een doodgewone
MeshCore-repeater uit met een MeshManager-bestandsnaam. De site zou die als
upgrade aanbieden, een node op een dak zou hem installeren, en er zou geen
beheerpagina meer zijn om het terug te draaien. Vandaar een controle op de bytes
in plaats van op de afloopcode van de compiler.

### De build heeft geen geheimen nodig

`platformio.local.ini` staat in gitignore omdat het WiFi-inloggegevens en een
beheerderswachtwoord bevat, dus CI kan het niet gebruiken. Dat hoeft ook niet, en
dat is een eigenschap van de firmware en geen omweg:

- `WIFI_SSID` / `WIFI_PWD` worden door `loadConfig()` gelezen **als
  standaardwaarden**, voordat `/msnet.json` op de datapartitie ze mag
  overschrijven.
- `ADMIN_PASSWORD` wordt alleen in het defaults-blok in de prefs van MeshCore
  geschreven, voor een node die nog geen prefs heeft.
- Een OTA schrijft een applicatiepartitie en laat de datapartitie ongemoeid.

Een node die één keer geconfigureerd is, houdt dus zijn netwerk en zijn wachtwoord
over elk image dat uit placeholders gebouwd is. `firmware/platformio.ci.ini`
bevat die placeholders en staat in git, juist omdat er niets in staat.

`ADMIN_PASSWORD` is een placeholder in plaats van leeg omdat het niet alleen een
waarde is: `#if defined(ADMIN_PASSWORD)` in MeshCore's `ESP32Board.cpp` is wat de
standaard soft-AP-updater überhaupt in het image meecompileert, en die updater is
de onderste sport van de terugvalladder.

> **Dit zijn upgrade-images.** Er eentje over USB op een maagdelijk board flashen
> levert een node op zonder netwerk en met een placeholderwachtwoord — op een dak
> een node waar je een ladder voor nodig hebt. Eerste installaties worden gebouwd
> uit `platformio.local.ini`, met echte waarden.

---

## Vangnetten, en hoe dit pad ze niet voor de voeten loopt

`MeshManagerNet` had er al vier, beschreven in [`firmware.md`](firmware.md). Het
upgradepad is gebouwd om erop te leunen in plaats van eromheen:

| Vangnet | Wisselwerking |
|---|---|
| Bootteller → veilige modus na 3 herstarts | De upgrade-endpoints worden **onvoorwaardelijk** geregistreerd, dus ze werken in veilige modus — precies de toestand waarin iemand er een werkend image in terug moet zetten |
| Bootteller → module uitgeschakeld na 6 herstarts | De endpoints en `wifi fw` zijn dan ook weg. Wat overblijft is standaard MeshCore en `start ota`. Dit is de bodem, en die is bewust gekozen: een commando dat het falen van zijn eigen module overleeft, zou buiten die module moeten leven |
| Task-watchdog | Stapt al opzij zolang `Update` draait, zodat hij een flash-schrijfactie niet kan afbreken. Ons pad gebruikt hetzelfde `Update`-object, en erft dat dus |
| Netwerkkant start ook bij een radiostoring | Ongewijzigd — een node die geen LoRa kan praten, is nog steeds te herflashen |

Twee schrijvers op één `Update`-object zouden hun bytes in één partitie door
elkaar heen weven, dus een tweede upload terwijl er al een loopt wordt geweigerd
in plaats van meegenomen.

---

## Dreigingsmodel

De belangrijkste zin op deze pagina: **een checksum bewijst integriteit, geen
authenticiteit.** De digest die de server controleert en die de node nog eens
controleert, bewijst dat de bytes precies zo aankwamen als ze vertrokken. Hij
bewijst helemaal niets over wie ze gemaakt heeft. Die twee door elkaar halen is
de klassieke manier om je veilig te voelen bij een updatekanaal dat dat niet is,
dus deze sectie zegt onomwonden waar de grens ligt.

### Wat er werkelijk gecontroleerd wordt

| Controle | Waar | Bewijst |
|---|---|---|
| SHA-256 tegen de gepubliceerde `.sha256` | server, vóór het versturen | de download is niet afgekapt of beschadigd |
| SHA-256 nogmaals, over de geschreven bytes | node, vóór het wisselen van partitie | er is niets verloren of verhaspeld tussen server en node |
| ESP32-imageheader en -grootte | node, binnen `Update.begin()`/`Update.end()` | dit is *een* applicatie-image, van een grootte die past |
| HTTP-login | node | de uploader heeft de beheerdersgegevens van de node |

Er is **geen code-signing en geen secure boot**. `Update` accepteert monter elk
welgevormd ESP32-applicatie-image. Dus:

> **Iedereen die poort 80 van de node kan bereiken en zijn inloggegevens heeft,
> kan willekeurige firmware flashen.** Dat is dezelfde groep mensen die via
> `/api/backup` al de private sleutel kan downloaden. Het beheernetwerk is de
> grens, en het is de enige grens.

### Waar de vertrouwensketen feitelijk op rust

1. **GitHub**, voor het bewaren van de release en zijn assets.
2. **De HTTPS-verbinding van de server met GitHub**, voor het ongewijzigd
   afleveren van zowel het image als de `.sha256`. Merk op dat de digest en het
   image langs dezelfde weg van dezelfde plek komen: wie de een kan vervangen,
   kan de ander ook vervangen, dus deze controle vangt ongelukken en geen
   tegenstanders.
3. **De workflow die het image bouwde**, en dat is de reden dat hij bouwt vanaf
   een tag in deze repository en niet vanaf iemands laptop — het maakt van "welke
   broncode draait mijn daknode" een vraag met een antwoord.
4. **Wie de beheerdersgegevens van de node heeft**, en wie het netwerk kan
   bereiken waar de node op zit.

Alles stroomafwaarts van een compromittering op een van die vier is
onbeschermd.

### Zou een handtekening helpen, en kunnen we die krijgen?

Ja op het eerste, en het lijkt haalbaar — maar hij is **vandaag niet gebouwd**,
en dat zeggen is nuttiger dan het tegendeel suggereren.

De vorm die het zou krijgen: CI ondertekent de digest van het image met een
private sleutel die als repository-secret bewaard wordt; de publieke sleutel
wordt in de firmware meegecompileerd; de node verifieert de handtekening *op het
punt waar hij de digest toch al vergelijkt*, vlak vóór `Update.end()`. Ed25519 is
de voor de hand liggende keuze omdat MeshCore al een Ed25519-implementatie
meedraagt voor node-identiteiten, dus het kost geen nieuwe afhankelijkheid en heel
weinig flash.

Dat zou het gat bij stap 1 en 2 hierboven dichten: een vervangen asset zou niet
langer geaccepteerd worden, omdat wie hem verving de handtekening niet kan
produceren. Het zou stap 4 **niet** dichten — een ondertekensleutel in een
GitHub-secret is nog steeds aan GitHub toevertrouwd, en iemand aan de USB-poort
van de node of met `start ota` kan nog altijd van alles flashen, omdat de
bootloader het zonder secure boot ook niet uitmaakt.

Twee redenen waarom het niet in deze release zit, allebei het weten waard voordat
iemand het toevoegt:

- **Een ondertekenschema is een manier om een node te verliezen.** Raakt de
  sleutel kwijt, of zit er een bug in de verificatie, dan accepteert het OTA-pad
  *geen enkel* image meer — op een repeater op een dak. Een ondertekend pad moet
  dus altijd een niet-ondertekende terugvaloptie houden (`/update`, `start ota`,
  USB), wat betekent dat het de lat hoger legt zonder ooit de enige deur te zijn.
  De moeite waard, maar de haast niet waard.
- **Sleutelbeheer is een beslissing, geen detail.** Waar de private sleutel
  woont, wie een ondertekenende build mag starten, en wat er gebeurt als hij
  geroteerd wordt op nodes die de oude publieke sleutel al dragen, zijn vragen met
  echte antwoorden, en die stilzwijgend invullen is erger dan nog niet
  ondertekenen.

Tot die tijd: **behandel het beheerderswachtwoord van de node en het netwerk
waarop hij zit als de dingen die hem beschermen**, want dat zijn ze.

### Overige aannames

- **Het MQTT-topic `cmd` maakt geen deel uit van dit pad.** Er is geen
  upgrade-werkwoord aan toegevoegd, en dat moet ook niet gebeuren: dat topic is
  bereikbaar voor iedereen met broker-inloggegevens, en het accepteert precies
  daarom een korte, vaste lijst woorden. Het enige MQTT-bericht dat deze
  functionaliteit verstuurt is `status`, na een geslaagde upgrade, om de node zijn
  nieuwe versie te laten publiceren in plaats van de site de oude te laten tonen
  tot het volgende geplande bericht.
- **De `/api/fw` van de node zit ook achter de login**, inclusief de
  alleen-lezen GET. `env` plus `ver` is een boodschappenlijstje voor iedereen die
  hier graag het verkeerde image zou schrijven.
- **Het beheeradres wordt door een operator ingetypt**, en er wordt gevalideerd
  dat het `http://` of `https://` is voordat het gebruikt wordt — een hostveld dat
  `file:///etc/...` accepteerde, zou een manier zijn om de server zijn eigen schijf
  te laten lezen.
