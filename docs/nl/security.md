# Beveiliging

*[English](../security.md)*

Wat dit systeem beschermt, hoe, en — even belangrijk — wat het níét beschermt.
Alles hier is uit de code gelezen. Waar een maatregel zwakker is dan ze lijkt,
staat dat er.

## Dreigingsmodel

MeshStats publiceert statistieken over een radionetwerk. De gegevens zelf zijn
niet geheim; iedereen met een LoRa-radio hoort dezelfde adverts. De zaken die
het beschermen waard zijn, zijn dus niet de metingen.

| Bezit | Waar | Waarom het ertoe doet |
|---|---|---|
| **De privésleutel van een node** | SPIFFS op de node; in elke back-up van het bestandssysteem | Wie hem heeft, *ís* die node. Adverts zijn Ed25519-ondertekend, dus identiteit is de sleutel. |
| Nodebeheer | Beheerpagina van de node, telnetconsole | Firmware uploaden, WiFi-instellingen, sleutelexport |
| Sitebeheer | `/admin` | Repeaters verbergen/hernoemen, API-tokens aanmaken, bewaartermijn wijzigen |
| API-tokens | Serverdatabank, HA-configuratie, nodeconfiguratie | Schrijftoegang tot de ingest-API |
| Gegevensintegriteit | De ingestwegen | Iemand die valse metingen injecteert |

De structurele eigenschap die als eerste gezegd moet worden:

**De server bewaart geen inloggegevens voor je mesh.** Er is geen bewaard
nodewachtwoord en geen manier waarop de site een radio kan instellen. Een
volledige compromittering van de website geeft een aanvaller geen controle over
ook maar één node.

Gegevens stroomden vroeger strikt één kant op, en dat is niet langer letterlijk
waar. Er bestaan twee smalle terugwegen, en beide zijn het waard te begrijpen
voor je op de zin hierboven vertrouwt.

**1. Het MQTT-commandotopic.** De server publiceert op `meshcore/<node>/cmd`, en
de firmware aanvaardt daar precies drie woorden: `settings` (lees nu mijn eigen
CLI-parameters), `status` (publiceer nu een statistiekbericht) en `time <epoch>`
(zet mijn klok). Het is een
exacte vergelijking met die lijst — geen prefixtest, en uitdrukkelijk
*geen* doorval naar de CLI van de node, ook al doet de telnetconsole van de node
precies dat. Die console zit achter een wachtwoord op een verbinding die jij
beheert; dit topic is bereikbaar voor iedereen met brokerinloggegevens, en deze
repeaters hangen op daken waar één `reboot` in een lus een verloren node is.

Twee van de drie laten de node alleen zeggen wat hij uit zichzelf ook gezegd zou
hebben. De derde niet: `time` schrijft op het apparaat. Wat hem begrenst zijn de
regels van de firmware zelf en niet het topic — een klok mag alleen vooruit, en
een node die al voorloopt wordt met rust gelaten — dus het ergste dat iemand met
brokerinloggegevens kan doen is de klok van een node de toekomst in duwen. Dat is
over de lucht niet terug te draaien en vergt een herstart om te herstellen. Dát
is het werkelijke plafond van deze weg, en het ligt hoger dan "een node een
statistiekbericht laten publiceren". Begrens het
verder met een ACL die elke node enkel leesrecht geeft op zijn eigen
`cmd`-topic, en de server enkel schrijfrecht op `meshcore/+/cmd` — zie
`mosquitto/acl.example`.

**2. De pollwachtrij.** De HA-integratie pollt `GET /api/v1/commands` en handelt
naar wat ze daar vindt — een lijst repeaterprefixen om te verversen en
CLI-parameters om op te halen. Een gecompromitteerde server kan Home Assistant
dus vragen `send_cmd` te draaien tegen repeaters *waarvan Home Assistant de
wachtwoorden al bezit*. Verzoeken worden begrensd (parameters afgekapt op 64
tekens, hoogstens 40 per verzoek), maar een parameter mag `cmd:<literal>` zijn,
en die wordt woordelijk verstuurd. Dit is met afstand de bredere van de twee
wegen — en ze bestaat alleen als je de HA-integratie draait met
repeaterwachtwoorden ingesteld.

---

## Server

### Wachtwoordopslag

`server/app/auth.py`:

```python
salt = secrets.token_bytes(16)
dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 200_000)
return f"pbkdf2${salt.hex()}${dk.hex()}"
```

- **PBKDF2-HMAC-SHA256, 200 000 rondes**, een willekeurige salt van 16 byte per
  wachtwoord.
- Bewaard als `pbkdf2$<salt_hex>$<dk_hex>` in `admins.pw_hash`.
- Verificatie herberekent en vergelijkt met `hmac.compare_digest()` — constante
  tijd.

200 000 rondes ligt boven de OWASP-ondergrens voor PBKDF2-SHA256. Een
geheugenharde KDF (argon2, scrypt) zou sterker zijn, maar PBKDF2 zit in de
standaardbibliotheek en deze installatie heeft precies één beheeraccount, dus er
is geen wachtwoorddatabank die het waard is op schaal te kraken.

Het eerste wachtwoord wordt bij het opstarten gegenereerd met
`secrets.token_urlsafe(12)` — ongeveer 96 bit — en één keer naar stdout
geschreven. Het wordt nooit in klare tekst bewaard.

Wijzig het via `/admin` of met:

```bash
docker compose exec meshstats python -m app.main set-password admin
```

De minimumlengte is 8 tekens, afgedwongen in beide wegen.

### API-tokens

- Aangemaakt als `"mcs_" + secrets.token_urlsafe(32)` — 256 bit.
- **Alleen** bewaard als `hashlib.sha256(token).hexdigest()`. De klare tekst
  wordt nooit naar de databank geschreven.
- Precies één keer aan de beheerder getoond, via een `httponly`-cookie van 60
  seconden en niet via een URL — zo belandt hij niet in logboeken, geschiedenis
  of een referrer-header.
- Intrekken zet `revoked=1`; opzoekingen filteren erop.

Kale SHA-256 in plaats van een trage KDF is hier de juiste keuze: het token is
256 bit willekeur en geen door mensen gekozen wachtwoord, dus er valt niets
bruteforce te raden. Een gestolen databank geeft een aanvaller hashes die hij
niet kan omkeren.

Twee beperkingen:

- **De opzoeking is een SQL-gelijkheidsvergelijking op de digest, geen
  vergelijking in constante tijd.** Het timen van een hashtabelopzoeking om 256
  bit te achterhalen is geen praktische aanval, maar constante tijd is het niet.
- **Tokens verlopen niet.** Trek ze met de hand in.

### Sessies

Toestandsloze, met HMAC ondertekende cookies. Geen sessieopslag aan serverzijde.

```
cookie value = base64url(json({"u": username, "exp": ...})) + "." + HMAC-SHA256(payload)
```

De sleutel is de 32 willekeurige bytes in `secret.key`, bij de eerste start
aangemaakt met modus `0600`.

| Eigenschap | Waarde |
|---|---|
| Cookienaam | `mcs_session` |
| Levensduur | 12 uur (`SESSION_TTL`) |
| `HttpOnly` | ja |
| `SameSite` | `lax` |
| `Secure` | gezet wanneer het verzoek over HTTPS binnenkomt, ook via `X-Forwarded-Proto` |
| `Path` | `/` (standaard van Starlette) |

Verificatie gebruikt `hmac.compare_digest()` en controleert daarna `exp`.

#### Intrekken zonder sessietabel

De payload draagt een derde veld, `v`: een HMAC over de huidige wachtwoordhash
van het account, afgekapt op 16 hextekens.

```python
hmac.new(SECRET, b"pwstamp|" + pw_hash, hashlib.sha256).hexdigest()[:16]
```

`read_session()` herberekent hem uit de `admins`-rij en weigert de cookie zodra
hij niet meer past. Een wachtwoord wijzigen herschrijft `pw_hash`, dus **elke
sessie die onder het oude wachtwoord geslagen is, werkt onmiddellijk niet meer**
— de accountrij is de intrekkingslijst, en een sessieopslag is niet nodig. De
hash zelf verlaat de server nooit; alleen zijn HMAC wordt gepubliceerd.

`POST /admin/password` geeft in hetzelfde antwoord een verse cookie uit, zodat de
beheerder die het wachtwoord wijzigde niet van zijn eigen pagina gegooid wordt.

| Situatie | Effect |
|---|---|
| Wachtwoord gewijzigd in `/admin` | Alle andere sessies ongeldig, deze browser krijgt een nieuwe |
| Wachtwoord gewijzigd via `python -m app.main set-password` | Alle sessies ongeldig |
| Account verwijderd | Zijn sessies ongeldig (`password_stamp` geeft `None` terug) |
| `secret.key` verwijderd en herstart | Alle sessies en CSRF-tokens ongeldig |

De prijs is één geïndexeerde `SELECT` op `admins` per beheerverzoek. Sessies van
vóór deze wijziging dragen geen `v` en worden geweigerd — een eenmalige
uitlogactie.

`SameSite=lax` blokkeert cross-site-POST's, de belangrijkste CSRF-vector, terwijl
gewone navigatie op topniveau naar `/admin` blijft werken.

### CSRF

```python
hmac.new(SECRET, b"csrf|" + anchor, hashlib.sha256).hexdigest()[:32]
```

Het anker is een cookiewaarde, dus het token is per browser en kan zonder het
geheim niet vervalst worden. Het wordt als verborgen `csrf`-veld gerenderd in elk
beheerformulier en op de publieke repeaterpagina wanneer een beheerder ingelogd
is. Elke beheer-POST die toestand wijzigt, valideert hem.

| Formulier | Ankercookie | Levensduur |
|---|---|---|
| Elk beheerformulier na inloggen | `mcs_session` | met de sessie (12 u) |
| `POST /admin/login` | `mcs_login` | 30 min (`LOGIN_TTL`) |

**Het inlogformulier heeft zijn eigen anker** omdat de bezoeker nog geen sessie
heeft: `GET /admin/login` slaat een willekeurige nonce, zet die als
`HttpOnly`-cookie en rendert het token dat eruit afgeleid is. Een
cross-site-inlog-POST kan die cookie niet lezen en kan dus geen passend token
maken. Een formulier dat langer dan `LOGIN_TTL` openstond, komt op dezelfde
controle terecht en krijgt *"Sessie verlopen — probeer opnieuw"* met een verse
nonce.

De validatie loopt via `auth.eq()`, dat `hmac.compare_digest` is. Elke
vergelijking van een token, handtekening of digest in de toepassing gebruikt hem
— `check_csrf`, het inlogtoken, de sessiehandtekening, de sessiestempel en de
wachtwoordcontrole. De enige overgebleven gelijkheidsvergelijking op iets geheims
is de SQL-opzoeking op de digest van een API-token, beschreven onder
[API-tokens](#api-tokens); dat is een hashtabelopzoeking op 256 bit, geen
wandeling byte voor byte.

### Beveiligingsheaders

Als standaardwaarden gezet in middleware, zodat een route ze kan overschrijven:

| Header | Waarde |
|---|---|
| `X-Content-Type-Options` | `nosniff` |
| `X-Frame-Options` | `DENY` |
| `Referrer-Policy` | `strict-origin-when-cross-origin` |
| `Permissions-Policy` | `geolocation=(), microphone=(), camera=()` |

Content Security Policy:

```
default-src 'self';
script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net https://unpkg.com;
style-src 'self' 'unsafe-inline' https://fonts.googleapis.com https://unpkg.com;
font-src https://fonts.gstatic.com;
img-src 'self' data: https://unpkg.com https://*.basemaps.cartocdn.com;
connect-src 'self';
frame-ancestors 'none';
base-uri 'self';
form-action 'self';
object-src 'none'
```

`frame-ancestors 'none'`, `base-uri 'self'`, `form-action 'self'` en
`object-src 'none'` zijn de delen die echt werk doen.

`'unsafe-inline'` op scripts en stijlen is een echte verzwakking — het is wat het
inline opstarten van de grafiek en de kaart nodig heeft, en het betekent dat de
CSP een geïnjecteerd inline script niet zou tegenhouden. De verzachting is dat de
pagina's geen door de gebruiker bepaalde HTML renderen: metrieklabels en
repeaternamen gaan door Jinja-autoescaping.

De CDN-toelatingen (`cdn.jsdelivr.net`, `unpkg.com`, CartoDB-basiskaarten)
betekenen dat de publieke pagina's code en tegels bij derden ophalen. Dat is een
afhankelijkheid in de toeleveringsketen, en het vertelt die CDN's wie je site
bekijkt. Die assets zelf hosten zou je toelaten de toelatingen volledig te
schrappen.

**`Strict-Transport-Security` wordt niet gezet.** Voeg hem toe op je reverse
proxy.

### API-authenticatie

Schrijfendpoints vereisen `Authorization: Bearer <token>`:

| Endpoint | Auth |
|---|---|
| `POST /api/v1/ingest` | Bearer |
| `POST /api/v1/contacts` | Bearer |
| `POST /api/v1/repeater_settings` | Bearer |
| `GET /api/v1/commands` | Bearer |
| `GET /api/v1/ping` | Bearer |
| `GET /api/v1/repeaters` | **geen** |
| `GET /api/v1/repeaters/{slug}` | **geen** |
| `GET /api/v1/repeaters/{slug}/history` | **geen** |
| `GET /api/v1/repeaters/{slug}/map` | **geen** |

De leesendpoints staan met opzet open — de site is publiek — maar ze hangen af
van `is_public=1`, dus repeaters die in `/admin` verborgen zijn, geven ook daar
404.

OpenAPI, Swagger en ReDoc staan uit (`docs_url=None, redoc_url=None,
openapi_url=None`).

### Grenzen op verzoeken

`BodySizeLimitMiddleware` (`app/limits.py`) begrenst elk requestlichaam op
`MCS_MAX_BODY_BYTES`, standaard 2 MB, op elke route en elke methode.

Het werkt in twee stappen:

1. Een opgegeven `Content-Length` boven de grens wordt geweigerd voor er één byte
   gelezen is.
2. Anders worden de bytes **geteld terwijl ze binnenkomen**, via een omhulde
   ASGI-`receive`. Dat is wat een chunked request vangt, die helemaal geen
   `Content-Length` stuurt — de oude controle las dat als `0` en liet alles door.

Slaat de grens aan, dan is het antwoord `413`, ongeacht wat het endpoint ging
zeggen. Dat laatste doet ertoe: FastAPI vangt alles wat zijn formulier- en
JSON-parsers opwerpen en herschrijft het als zijn eigen `400`, dus de middleware
overschrijft ook het uitgaande antwoord in plaats van erop te rekenen dat de
uitzondering hem bereikt.

`limit_body()` in `routes_api.py` blijft bestaan als goedkope voorcontrole op de
JSON-endpoints. Hij eist geen `Content-Length` meer — de header is optioneel, en
hem vereisen wees legitieme streamingclients af zonder iets tegen te houden.

Begrens de requestgrootte ook op je reverse proxy (`client_max_body_size` in
nginx).

### Snelheidsbegrenzing

`POST /admin/login` wordt in het geheugen afgeremd door `app/ratelimit.py`. Twee
onafhankelijke emmers, elk sluit een gat dat de andere laat:

| Emmer | Stopt | Max. blokkade |
|---|---|---|
| `ip:<adres>` | Eén host die veel gebruikersnamen afgaat | 15 min |
| `user:<naam>` | Een botnet dat pogingen over veel adressen spreidt | 5 min |

Vijf mislukkingen binnen een venster van 15 minuten zijn gratis, dus een vertypt
wachtwoord kost niets. Elke verdere mislukking verdubbelt een blokkade vanaf 2 s,
met een plafond per emmer. Een juist wachtwoord wist beide emmers. Geblokkeerde
pogingen antwoorden `429` met `Retry-After` en bereiken de wachtwoordcontrole
nooit.

De emmer op gebruikersnaam is degene die de linie werkelijk houdt, want het
clientadres is maar zo eerlijk als de proxyketen, terwijl de gebruikersnaam
rechtstreeks uit het formulier komt. De prijs ervan is dat iedereen het
beheeraccount met opzet kan laten blokkeren — vandaar een plafond in minuten en
niet in uren, en een herstart wist het.

#### Welk adres geteld wordt

`request.client.host` wordt **niet** gebruikt. Uvicorn draait met
`--forwarded-allow-ips "*"`, en in die modus neemt hij de *eerste*
`X-Forwarded-For`-vermelding — die een client er zelf in zet. Daarop sleutelen
zou een aanvaller per verzoek een verse emmer laten slaan.

Proxy's *voegen* het adres dat ze zagen achteraan *toe*, dus de header is
betrouwbaar vanaf rechts. `ratelimit.client_ip()` telt
`MCS_TRUSTED_PROXY_HOPS` vermeldingen van rechts naar binnen (standaard `1`, wat
overeenkomt met cloudflared rechtstreeks naar de toepassing), controleert of het
resultaat als IP-adres te lezen is, en valt anders terug op het transportadres.

Zet `MCS_TRUSTED_PROXY_HOPS` op het aantal proxy's dat je werkelijk draait. Te
hoog en je leest weer vermeldingen die de client zelf aanlevert; te laag en elke
bezoeker deelt één proxyadres in één emmer.

De toestand leeft met opzet in het proces: de installatie is één uvicorn-proces,
en een tabel in SQLite zou elke inlogpoging vanaf het internet in een
schrijfactie veranderen. Een herstart vergeet de tellers.

De oude `time.sleep(1)` is weg — die vertraagde een parallelle aanvaller niet en
hield per poging een threadpoolworker bezet. In plaats daarvan draait een inlog
met een onbekende gebruikersnaam `auth.verify_dummy()`, zodat een verkeerde
gebruikersnaam en een verkeerd wachtwoord dezelfde 200 000 PBKDF2-rondes kosten
en de responstijd niets verraadt.

Een toegangsslot vóór `/admin*` op de proxy blijft de moeite; zie
[`deployment.md`](deployment.md#wat-je-moet-beschermen).

### Omgaan met invoer

- Metrieksleutels worden woordelijk bewaard. Er is geen witte lijst. Onbekende
  sleutels renderen in de sectie "overig". Waarden worden naar `float` gedwongen,
  of als tekst bewaard, afgekapt op 255 tekens.
- `repeater_cli.param` wordt afgekapt op 64 tekens, `value` op 4000.
- Alle SQL gebruikt gebonden parameters, ook de dynamisch opgebouwde `NOT
  IN`-clausule in `upsert_cli_settings` — het aantal plaatshouders varieert, de
  waarden blijven gebonden.
- Templates gebruiken Jinja-autoescaping.

**Onbekende repeaters worden automatisch aangemaakt en zijn standaard publiek.**
Iedereen met een geldig token, of met publiceerrechten op de broker, kan een
nieuwe repeater op de publieke pagina laten verschijnen. Verberg hem in `/admin`.

---

## Het transport tussen node en server

Wees hier duidelijk over: **de MQTT-weg heeft geen transportversleuteling.**

- De `PubSubClient` van de node draait over een kale `WiFiClient`. Geen
  TLS-ondersteuning.
- De paho-client van de server heeft geen `tls_set()`-aanroep en geen
  CA-configuratie.
- Brokerinloggegevens gaan dus in klare tekst over het netwerk.

De eerlijke vergelijking met de HTTP-weg die hij verving: die weg gebruikte TLS,
maar riep `secure.setInsecure()` aan — geen certificaatvalidatie, dus hij hield
passief meeluisteren tegen en verder niets. De overstap naar MQTT ruilde
ongevalideerde TLS in voor helemaal geen.

Wat er op die verbinding werkelijk op het spel staat: brokerinloggegevens, en
statistieken die op de website toch al publiek zijn. Niet de privésleutels van
nodes, die de node alleen verlaten via een back-up van het bestandssysteem.

Houd de broker op een netwerk dat je vertrouwt. Moet je een onvertrouwd netwerk
over, termineer TLS dan op een broker die het ondersteunt en zet een tunnel
tussen node en broker; stel poort 1883 niet bloot aan het internet.

### MQTT heeft geen authenticatie op toepassingsniveau

Brokerauthenticatie (`allow_anonymous false`) en het ACL-bestand zijn de
**enige** sloten op de MQTT-weg. Op binnengehaalde berichten is er geen
tokencontrole.

#### Publicist versus onderwerp

`_handle_payload()` las vroeger de repeaterprefix uit het JSON-lichaam en keek
nooit naar het topic, dus elke client die mocht publiceren kon beweren eender
welke repeater te zijn. Nu leest hij beide, en houdt hij ze uit elkaar:

- **Het topic noemt de publicist.** `meshcore/<node_hex>/stats` — de node die het
  bericht stuurde.
- **De payload noemt het onderwerp.** `repeater.pubkey_prefix` — de repeater waar
  de cijfers over gaan. Ontbreekt hij, dan betekent dat "mezelf" en levert het
  topic het onderwerp.

Ze mógen met opzet verschillen, want een node stuurt ook statistieken door voor
andere repeaters die hij monitort. Een verschil weigeren zou die functie
kapotmaken op de dag dat ze uitkomt. In plaats daarvan wordt de publicist op de
repeaterrij bewaard als `repeaters.source_prefix` (met `source_seen`) en getoond
in de kolom **Bron** op `/admin`: *zichzelf*, *via `<prefix>`* of *HTTP-API*. Een
repeater die via een onbekende node begint binnen te komen, is dan iets wat je
kunt zien.

**Dit begrenst de schade; het heft ze niet op.** Met één gedeeld brokeraccount
kan iedereen met die inloggegevens onder het topic van elke node publiceren, dus
het topic is precies zo betrouwbaar als het account erachter. De weg noteren
maakt het zich voordoen als een ander zichtbaar, niet onmogelijk.

#### De echte oplossing: één brokeraccount per node

Geef elke node zijn eigen MQTT-gebruiker en beperk die met een ACL tot zijn eigen
topicprefix. Dan dwingt de broker het topic af, en wordt `source_prefix` een feit
in plaats van een bewering.

```bash
./mosquitto/init-passwd.sh                 # server account + ACL skeleton
./mosquitto/add-node-user.sh e3d3f4d7ed01  # one account per node
docker compose restart mosquitto
```

`mosquitto.conf` verwijst naar `acl_file /mosquitto/config/acl`; het bestand
wordt gegenereerd door `init-passwd.sh` en aangevuld door `add-node-user.sh`. Het
gedeelde account behoudt `topic write meshcore/#` zodat er halverwege de migratie
niets stukgaat — haal die regel weg zodra elke node zijn eigen account heeft, en
pas dan wordt het topic werkelijk afgedwongen. Details en voorbehouden in
[`mqtt.md`](mqtt.md#accounts-en-acls-per-node).

Zet `allow_anonymous false` niet uit.

Merk ook op dat de MQTT-weg de HTTP-lichaamsgrens van 2 MB volledig omzeilt.
Mosquitto's `message_size_limit 8192` is daar de enige bovengrens.

---

## De beheerendpoints op de node

### De repeater: `MeshStatsNet`

Alles wat gevoelig is zit achter **HTTP basic auth**, met inloggegevens die
gedeeld worden tussen de webpagina en de telnetconsole (standaard `admin` /
`meshcore`):

| Endpoint | Auth | Waarom het ertoe doet |
|---|---|---|
| `/` | geen | Statische schil, rendert niets tot `/api/status` lukt |
| `/api/status` | basic | |
| `/api/wifi` | basic | Wijzigt netwerkinstellingen |
| `/api/backup` | basic | **Bevat de privésleutel** |
| `/api/restore` | basic | Overschrijft de identiteit |
| `/update` | basic | Firmware uploaden |
| Console (poort 23) | inlogprompt | Volledige MeshCore-CLI |

**Een back-up van het bestandssysteem bevat de privésleutel van de node.** Dat is
het gevoeligste dat er in dit project bestaat. De back-up is een dump van alles
op SPIFFS — het Ed25519-sleutelpaar, de repeatervoorkeuren, de ACL en de
netwerkconfiguratie. Wie dat bestand heeft, kan zich voor je node uitgeven:
adverts als die node ondertekenen, zijn identiteit in het mesh overnemen, en
verkeer dat aan hem gericht is ontsleutelen.

Precies daarom zit `/api/backup` achter de login, zegt de pagina dat met zoveel
woorden, en moet het standaardwachtwoord gewijzigd worden voor de node op een
netwerk gaat. Dezelfde login bewaakt ook het uploaden van firmware, dus een zwak
wachtwoord daar betekent willekeurige code op de node.

Wijzig het bij de eerste start, op drie manieren:

```
wifi console <user> <password>        # serial, mesh CLI or console
```

Basic auth over gewone HTTP stuurt de inloggegevens base64-gecodeerd — dat is
codering, geen versleuteling. Telnet is eveneens klare tekst. Beide zijn enkel
voor LAN of VPN. Er is geen HTTPS op de node en geen realistische manier om er
een toe te voegen naast mesh, WiFi en BLE op deze hardware — diezelfde
geheugendruk is de reden dat MQTT HTTPS verving voor de statistieken.

`wifi pass` en `wifi console` over de **mesh**-CLI zetten geheimen in klare tekst
in het LoRa-verkeer. Gebruik voor die twee de console of de webpagina.

### De companion-node: `StatsPublisher`

**Helemaal geen authenticatie.** Iedereen op je LAN kan `/config.json` lezen, de
brokerinstellingen wijzigen en je statistieken omleiden.

Het bewaarde brokerwachtwoord wordt nooit terug naar de pagina gerenderd — het
veld is alleen-schrijven, en leeg betekent "laat staan wat er is" — dus de pagina
lekt het niet. Maar de instellingen zelf staan open.

Behandel de beheerpagina van de companion-node als iets voor een vertrouwd
netwerk alleen.

### De eigen vangnetten van de repeater

Niet echt beveiligingsmaatregelen, maar ze sluiten een faalwijze in
beschikbaarheid: drie crashlussen zetten de node in veilige modus (enkel AP +
beheerpagina), zes schakelen de module volledig uit, en een mislukte
radio-initialisatie legt het bord niet meer stil. Een node op een dak blijft
bereikbaar om opnieuw te flashen in plaats van een baksteen te worden. Zie
[`firmware.md`](firmware.md#the-three-safety-nets).

---

## Geheimen en deze repository

Nooit committen:

- `platformio.local.ini` — WiFi-inloggegevens en `ADMIN_PASSWORD`. Staat in
  gitignore.
- `.env` — MQTT-inloggegevens. Staat in gitignore.
- `mosquitto/passwd` — hashes van brokerwachtwoorden.
- `mosquitto/acl` — namen van brokeraccounts. Staat in gitignore; `acl.example`
  documenteert het formaat.
- `/data/secret.key` en de databank.

`mosquitto/init-passwd.sh` en `add-node-user.sh` draaien `mosquitto_passwd -b`,
wat het wachtwoord op de opdrachtregel zet — zichtbaar in de shellgeschiedenis en
in de processenlijst. Draai `mosquitto_passwd` op een gedeelde machine in plaats
daarvan interactief. `init-passwd.sh` gebruikt bovendien `-c`, wat `passwd`
**afkapt**, en herschrijft `acl` volledig: hem opnieuw draaien wist elk
nodeaccount dat `add-node-user.sh` aanmaakte.

Alle voorbeelden in deze documentatie gebruiken plaatshouders. Is een
inloggegeven ooit gecommit, roteer het dan — geschiedenis herschrijven maakt het
niet ongepubliceerd.

---

## Auditnotities (2026-08)

Een defensieve doorlichting van de hele boom. Het meeste van wat ze vond stond al
hierboven of in de commentaren van de code zelf; deze sectie noteert de gaten die
dat niet deden, geordend naar ernst, plus wat gecontroleerd en in orde bevonden
is.

### Hier opgelost

**De MQTT-ingestweg eerbiedigde een `force` die van een aanvaller kwam.**
`mqtt_ingest._handle_payload()` gaf `force=bool(body.get("force"))` door aan
`db.ingest()`. `force` betekent "bewaar dit punt ook als het niet veranderde",
wat de heartbeat-ontdubbeling overslaat die de tabel `samples` klein houdt. Het
hoort bij het handmatig verversen op de HTTP-weg met tokenauthenticatie (Home
Assistant, `pusher.py::push_repeater(force=True)`); de nodefirmware zet het nooit
in zijn MQTT-JSON. Op het `stats`-topic is de invoer apparaatgegevens die niet
allemaal van de eigenaar zijn — iedereen met brokerinloggegevens kan eronder
publiceren — dus een `force` die daar aankwam, was geen verversing maar een
manier om de ontdubbeling te omzeilen en `samples` vol te schrijven. De weg haalt
nu altijd binnen met `force=False`. Echte nodes merken er niets van, want zij
sturen het veld nooit.

### Open: ongebreidelde groei in tabellen die de bewaartermijn nooit snoeit

`db.prune()` (`server/app/db.py`) begrenst `packets` (leeftijd, rijmaximum,
bytemaximum), `samples` (leeftijd) en `neighbors` (leeftijd). Hij raakt `latest`,
`repeater_cli` en `repeaters` **niet** aan. Die groeien met één rij per
verschillende `(repeater_id, metric)`, per CLI-parameter en per onderwerp met een
publieke sleutel — en niets laat ze verouderen.

Een onvertrouwde MQTT-publicist bereikt alle drie vanaf het `stats`-topic:

- `_handle_payload()` bewaart elke sleutel in `metrics` woordelijk (`db.ingest`,
  telkens één `latest`-rij) en elke `neighbors[].prefix` (een
  `neighbor_<prefix>`-rij), zonder bovengrens op hoeveel en zonder controle of
  een burenprefix hex is. Verschillende namen die over berichten heen afgewisseld
  worden, stapelen zich eeuwig op.
- de `pubkey_prefix` uit de payload (het onderwerp) wordt niet als sleutel
  gevalideerd voor `get_or_create_repeater()` er een **publieke** rij voor
  aanmaakt (`repeaters.is_public` staat standaard op 1), dus rommelonderwerpen
  vervuilen zowel `latest` als de publieke startpagina.

Mosquitto's `message_size_limit 8192` begrenst één bericht, dus dit is een
druppel en geen vloedgolf, en de eerlijke waarschijnlijkheid op deze installatie
— een LAN achter een VPN, één broker, ACL's per node — is klein: het vergt een
node met geldige inloggegevens of een gecompromitteerde node. Maar het
bytemaximum in `prune()` kan je hier niet redden, want het verwijdert enkel ooit
uit `packets`; opzwelling in `latest` zou het pakketten tot de FIFO-bodem laten
wegsnoeien terwijl het bestand groot blijft. De kleinste afdoende maatregel:
valideer het onderwerp als begrensde hex op de vertrouwensgrens, en geef
`latest`/`repeater_cli` hun eigen snoeibeurt (rijen weggooien waarvan de repeater
of de metriek niet binnen het bewaarvenster van de metingen gezien is).
Overgelaten aan het werk dat aan `db.py`/`mqtt_ingest.py` toebehoort in plaats
van halverwege de audit gewijzigd.

### Open: authenticiteit van het firmware-image

De route `/update` (en `start ota`) van de node gebruikt de ESP32-bibliotheek
`Update` (`firmware/examples/simple_repeater/MeshStatsNet.cpp`). Die controleert
of een image een structureel geldige app-partitie van de juiste grootte is —
integriteit, geen authenticiteit. Er is geen code signing en geen secure boot,
dus iedereen die het OTA-endpoint kan bereiken (achter de HTTP basic auth van de
node, enkel LAN/VPN) kan willekeurige firmware flashen. Wanneer de
firmware-upgradeweg aan serverzijde die nu in aanbouw is er komt, zal een
checksum op het gedownloade image bewijzen dat de download intact aankwam — niet
dat hij van de verwachte maker komt. Authenticiteit vergt een handtekening die de
node controleert tegen een ingebouwde publieke sleutel (of ESP32 Secure Boot);
een hash alleen levert dat niet. De moeite om in het dreigingsmodel te zetten
voor die weg uitkomt.

### Gecontroleerd en in orde bevonden

- **SQL voor zoeken en sorteren** (`search.py`): kolomnamen komen enkel uit de
  vaste tabellen `FIELDS`/`SORTS`, waarden zijn altijd gebonden parameters,
  `REGION_SQL` is een constante, en `_escape_like()` maakt `%`/`_`/`\`
  onschadelijk. De sorteersleutel wordt in `SORTS` opgezocht en een onbekende
  werpt een fout op in plaats van geïnterpoleerd te worden. Geen injectieweg
  gevonden.
- **Pakketdecoder** (`packets.py`): `decode()` werpt nooit een fout op (een brede
  vangst rond `_decode_into`), elke offset wordt op zijn grenzen gecontroleerd
  voor gebruik, en de vijf toelatingsregels spiegelen `tryParsePacket()`/
  `isValidPathLen()` van de firmware. Een geknutseld frame levert een
  gedeeltelijke dict met een `error` op, geen uitzondering.
- **Authenticatie, sessies, CSRF, inlogrem** (`auth.py`, `routes_admin.py`,
  `ratelimit.py`): PBKDF2 met 200 000 rondes, `hmac.compare_digest` op elk
  geheim, de wachtwoordhash als stempel in de sessie zodat een wijziging oude
  cookies intrekt, CSRF per formulier gebonden aan een cookie die de aanvaller
  niet kan lezen, en een inlogrem met twee emmers. De opzoeking van een API-token
  is een SQL-gelijkheid op een SHA-256 van 256 willekeurige bits, niet in
  constante tijd — hierboven vermeld en niet praktisch aanvalbaar.
- **Frontend** (`static/app.js`, templates): nodenamen van derden en
  pakketinhoud bereiken de DOM via `textContent` en het toekennen van attributen,
  nooit als HTML; de twee plaatsen die `innerHTML` gebruiken schrijven statische
  opmaak en vullen die daarna via `textContent`. Jinja-autoescaping staat aan.
  Geen XSS-put gevonden voor gegevens die uit het mesh komen.
- **Geheimen in de historiek**: 74 commits doorzocht; `.env.example` draagt enkel
  de plaatshouder `verander-dit-wachtwoord`, `platformio.local.ini.example` enkel
  `JOUW_WIFI`/`password`, en er is nooit een `passwd`-, `*.key`-, `acl`- of
  `.env`-bestand gecommit. De historiek is schoon.
- **Privacy**: de publieke kaart en pagina's tonen nodenamen en posities van
  andere operatoren (`routes_api.py::repeater_map`), die uit adverts komen die
  iedereen met een radio kan horen, maar de enige knop die de eigenaar heeft is
  de schakelaar `is_public` per repeater — alles of niets, en standaard publiek.
  Een keuze per node in de trant van "positie verbergen maar statistieken
  behouden" bestaat niet; het overwegen waard, want een positie is gevoeliger dan
  een batterijmeting.

### Kruiscontrole met de firmware

Een documentatieronde (commits `097efd8`, `f603aa5`) signaleerde verschillende
verschillen tussen code en documentatie. Hier beoordeeld op impact op de
beveiliging; verscheidene blijken in orde te zijn, en dat zeggen hoort erbij.

- **Het veld `"via"` is veilig omdat de server het negeert.** Doorgestuurde
  berichten dragen op het hoogste niveau `"via":"<node_hex>"`
  (`MeshStatsNet.cpp`, rond regel 2588). `mqtt_ingest.py` leest het nooit: de
  identiteit van de publicist komt enkel uit het topic (`_topic_node()`), dat een
  ACL per node vastlegt. Een node kan zich via `via` dus niet voordoen als een
  andere. Het risico is latent, niet aanwezig — wie later `via` wél voor
  identiteit begint te vertrouwen, brengt precies het gat tussen payload en topic
  terug dat de huidige code vermijdt. Laat het ongelezen.
- **De grens van 168 byte in `createGroupDatagram()` tegenover
  `MAX_GROUP_DATA_LENGTH` (165): hier niet bereikbaar voor een aanvaller.**
  `createGroupDatagram()` komt van MeshCore upstream en zit niet mee in deze
  repo, dus zijn interne grens is van hieruit niet te auditen of te wijzigen. De
  aanroepers in de repo zijn allemaal *verzend*wegen
  (`BaseChatMesh::sendGroupData`/`sendGroupMessage`), gevoed door lokale
  toepassingsinvoer en niet door ontvangen frames: `sendGroupData` bewaakt
  `data_len <= MAX_GROUP_DATA_LENGTH` en geeft zijn buffer de grootte
  `3 + MAX_GROUP_DATA_LENGTH` (168) voor hij `3 + data_len` (<=168) schrijft, wat
  past. Ontvangen verkeer komt bij de decoder terecht, nooit bij deze opbouwers.
  Geen overflow via de lucht gevonden; de speling van drie byte is het waard
  upstream op te ruimen maar is op deze installatie geen vector.
- **`STATS_TRACE=1` ("TEMPORARY") lekt enkel naar de seriële console.** De
  `TRACE`-macro's in `StatsPublisher.cpp` breiden uit tot `Serial.printf` en
  drukken de request-URI en de vrije heap over USB af — nooit op het netwerk of
  op MQTT. Het lezen ervan vereist een kabel aan het apparaat, dat wil zeggen de
  fysieke toegang die de node toch al bezit. Het is dus debug die aan bleef staan
  (ruis, een beetje airtime voor het afdrukken), geen openbaarmaking op afstand.
  Zet het in een productiebuild toch uit.
- **`gen_page.py` schrijft `StatsPage.h` voor hij de grootte controleert.** Bij
  een te grote pagina laat hij het slechte artefact op schijf staan *en* stopt
  hij met exitcode 1 (regels 68 tegenover 73-76). Geen aanvalsweg, maar een
  mislukte build die een latere build zou kunnen oppikken — controleer voor het
  schrijven, of schrijf naar een tijdelijk bestand en hernoem bij succes.
  Onderschreven als buildhygiëne.
- **`MAX_CONTACTS` is een harde bovengrens, en de tegenspraak zit alleen in de
  getallen.** Standaard 100 (`MyMesh.h`), 260 in `platformio.local.ini.example`,
  350 in een commentaar. De bovengrens zelf is de bescherming van de
  beschikbaarheid — adverts van derden kunnen de tabel er niet voorbij laten
  groeien. Het meningsverschil is een probleem van configuratieduidelijkheid dat
  rechtgetrokken moet worden, geen weg om het geheugen uit te putten.
- **`DualSerialInterface.h` is inert en wordt niet gecompileerd.** Niets
  includeert het; zowel `AbstractUITask.h` als `main.cpp` includeren in plaats
  daarvan `MultiSerialInterface.h`. Het is dus dode code op schijf, geen levend
  oppervlak. Verwijder het voor de hygiëne; het verandert vandaag geen enkel
  aanvalsoppervlak.

## Checklist voor een publieke installatie

- [ ] Wijzig het adminwachtwoord meteen na de eerste start
- [ ] Zet `MCS_TRUSTED_PROXY_HOPS` op het aantal proxy's dat werkelijk voor de
      toepassing staat (standaard `1`) — de inlogrem sleutelt erop
- [ ] Zet ook een toegangsslot vóór `/admin*`; de ingebouwde rem geldt per proces
      en vergeet bij een herstart
- [ ] Bind de container aan loopback en laat de reverse proxy erbij
- [ ] Voeg `Strict-Transport-Security` toe op de proxy
- [ ] Begrens de grootte van het requestlichaam ook op de proxy
- [ ] Stel de MQTT-poort niet bloot aan het internet
- [ ] Eén brokeraccount per node (`mosquitto/add-node-user.sh`), en haal daarna
      `topic write meshcore/#` weg bij het gedeelde account in `mosquitto/acl`
- [ ] Controleer de kolom **Bron** in `/admin` — statistieken die via een
      onverwachte node binnenkomen zijn een blik waard
- [ ] Wijzig de standaardlogin `admin` / `meshcore` van de node voor hij op een netwerk komt
- [ ] Houd de beheerpagina's van nodes weg van elk onvertrouwd netwerk
- [ ] Maak een back-up van `/data/secret.key` en de databank; bewaar node-back-ups als geheimen
- [ ] Bekijk nieuwe repeaters in `/admin` — ze verschijnen standaard publiek
- [ ] Trek API-tokens in die je niet meer gebruikt; ze verlopen nooit
