# Beveiliging

*[English](../security.md)*

Wat dit systeem beschermt, hoe, en — even belangrijk — wat het níét beschermt.
Alles hier is uit de code gelezen. Waar een maatregel zwakker is dan ze lijkt,
staat dat er.

## Dreigingsmodel

MeshManager publiceert statistieken over een radionetwerk. De gegevens zelf zijn
niet geheim; iedereen met een LoRa-radio hoort dezelfde adverts. De zaken die
het beschermen waard zijn, zijn dus niet de metingen.

| Bezit | Waar | Waarom het ertoe doet |
|---|---|---|
| **De privésleutel van een node** | SPIFFS op de node; in elke back-up van het bestandssysteem | Wie hem heeft, *ís* die node. Adverts zijn Ed25519-ondertekend, dus identiteit is de sleutel. |
| Nodebeheer | Beheerpagina van de node, telnetconsole | Firmware uploaden, WiFi-instellingen, sleutelexport |
| Sitebeheer | `/admin` | Repeaters verbergen/hernoemen, API-tokens aanmaken, bewaartermijn wijzigen, klokken zetten, firmware schrijven |
| De toekenningen zelf | `admins.is_superuser`, `grants` | Sinds toegang niet meer alles-of-niets is, *zijn* deze rijen de bevoegdheid — wie ze mag wijzigen, kan zichzelf al de rest geven |
| API-tokens | Serverdatabank, HA-configuratie, nodeconfiguratie | Schrijftoegang tot de ingest-API |
| Gegevensintegriteit | De ingestwegen | Iemand die valse metingen injecteert |

De structurele eigenschap die als eerste gezegd moet worden — en die opnieuw
gezegd moet worden, want ze was sterker dan ze nu is:

**De server bewaart geen wachtwoorden van andermans nodes.** Er is geen bewaard
nodewachtwoord voor een repeater die niet van jou is, en er is geen manier
waarop de site er een bereikt anders dan via een monitor die er al rechten op
had.

Dat is een smallere belofte dan deze pagina deed tot firmware 2.1.0, en die
verandering is een keuze en geen verzuim. Er stond dat de site helemaal geen
radio kon instellen, en dat een volledige compromittering van de website een
aanvaller over geen enkele node macht gaf. **Allebei is nu onwaar**, en wie zijn
brokerconfiguratie daarop gebaseerd heeft, hoort de volgende drie alinea's te
lezen.

### Wat er precies veranderd is

**De site kan firmware en instellingen schrijven naar nodes die van jou zijn.**
`POST /api/fw` installeert een image; `POST /api/cfg` zet een CLI-parameter,
zendvermogen en radioparameters inbegrepen. Allebei lopen ze over HTTP naar de
beheerpagina van de node zelf, en allebei vragen ze de weblogin van die node —
die de server bijhoudt in `MM_FW_NODE_USER` / `MM_FW_NODE_PASS`, in de omgeving.

De eerlijke zin luidt dus: **zet je die twee variabelen, dan geeft een volledige
compromittering van de website een aanvaller alles wat die inloggegevens
toelaten — en dat is firmware schrijven naar elke node die de server over IP
bereikt.** Zet je ze niet, dan zijn die wegen simpelweg dicht, staan de knoppen
uitgegrijsd met die reden erbij, en werkt de rest gewoon.

Dat is het afwegen waard in plaats van het stilzwijgend aan te zetten, en het
weegt anders nu de site vanaf het publieke internet bereikbaar is. Twee
maatregelen, en geen van beide is schijnbeweging:

- **Laat de variabelen leeg** tenzij je bezig bent met upgraden. De functie is
  dan uit, niet halfslachtig.
- **Houd het beheernetwerk van de nodes onbereikbaar vanaf de publieke kant van
  de site.** De server heeft een route naar de nodes nodig; het internet heeft
  geen route nodig naar de nodekant van de server.

### Wat er *niet* veranderd is

**Inloggegevens van andermans nodes staan hier niet, en dat is structureel en
geen beleid.** Een repeater die niet van jou is bereik je over LoRa, vanaf een
monitor, en de rechten daarvoor horen bij die monitor:

- Bij voorkeur staat zijn publieke sleutel in de toegangslijst van de overkant
  (`setperm <monitor-pubkey> 3`). Dan is er **nergens een wachtwoord** — niet op
  de server, niet op de monitor. De eigenaar van de overkant kan het alleen
  intrekken, en niemand heeft ooit een geheim uit handen gegeven.
- Anders houdt de monitor het adminwachtwoord van die node, in zijn eigen
  monitorlijst, op het apparaat — waar het toch al moest staan om überhaupt te
  kunnen inloggen.

De site kan dat wachtwoord wel *zetten*, en hij **geeft het door zonder het te
houden**: het gaat naar de monitor en wordt niet naar de databank, niet naar een
instelling en niet naar een logregel geschreven. Wat dat kost staat hier in
plaats van verstopt — de site kan je niet tonen wat er ingesteld staat en kan het
niet opnieuw versturen zonder dat jij het opnieuw intikt. Wat het oplevert is dat
een inbraak hier geen sleutelbos is voor andermans apparatuur.

### Wat een aanvaller krijgt bij een volledige compromittering

| | Vóór firmware 2.1.0 | Nu |
|---|---|---|
| Alle statistieken en pakkethistorie lezen | ja | ja |
| De drie vaste woorden op het `cmd`-topic publiceren | ja | ja |
| Wachtwoorden van repeaters die niet van jou zijn | nee | **nee** — nog steeds |
| Een radio instellen die niet van jou is | nee | **nee** — daarvoor zijn rechten nodig die de monitor houdt |
| Firmware schrijven naar je eigen nodes | nee | **ja, als `MM_FW_NODE_*` gezet is** |
| CLI-instellingen wijzigen op je eigen nodes | nee | **ja, als `MM_FW_NODE_*` gezet is** |

Die laatste twee regels zijn de verandering. Ze zijn de prijs van een repeater op
een dak kunnen upgraden zonder ladder, en de schakelaar die hem betaalt is een
paar omgevingsvariabelen die jij beheert.

Gegevens stroomden vroeger strikt één kant op. Naast de twee HTTP-wegen hierboven
bestaan er twee *smalle* terugwegen over MQTT, en beide zijn het waard te
begrijpen: ze staan open voor iedereen met brokergegevens, en dat is een ruimere
groep dan wie de weblogin van de node heeft.

**1. Het MQTT-commandotopic.** De server publiceert op `meshmanager/<node>/cmd`, en
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
`cmd`-topic, en de server enkel schrijfrecht op `meshmanager/+/cmd` — zie
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
docker compose exec meshmanager python -m app.main set-password admin
```

De minimumlengte is 8 tekens, afgedwongen in beide wegen.

### API-tokens

- Aangemaakt als `"mm_" + secrets.token_urlsafe(32)` — 256 bit.
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

Een token is **geen** account en draagt geen rol. Het opent de invoerwegen van de
HTTP-API en niets onder `/admin`, dus het rechtenmodel uit
[`admin.md`](admin.md#gebruikers-rollen-en-groepen) is er niet op van toepassing
— er is geen handeling waar het over zou gaan. Tokens rollen geven zou een tweede
weg naar dezelfde bevoegdheden maken, met een eigen intrekking en een eigen
audittrail, en twee wegen naar "mag deze firmware schrijven" is er één te veel.
`tokens.created_by` legt vast wie er een aanmaakte, want een token zonder
eigenaar is een sleutel die niemand durft in te trekken.

### Autorisatie

Authenticatie beantwoordt *wie*; dit beantwoordt *wat diegene mag*. Allebei doen
ze ertoe nu de site een klok kan zetten en firmware kan schrijven.

`rbac.decide(user, action, rep)` is het enige beslispunt, en elke schrijvende
beheerroute komt er via `routes_admin.require_perm()` uit. Een test loopt de
router af en faalt zodra een `POST`-route dat niet doet, want een rechtencontrole
die per route overgeschreven wordt, is er een die bij de volgende route vergeten
wordt. Het volledige model — risicoklassen, rollen als plafonds, toekenningen, en
de conflictregel (weigeren wint van toestaan; onder de toestemmingen wint de
ruimste; geen toekenning is geen toegang) — staat in
[`admin.md`](admin.md#gebruikers-rollen-en-groepen).

Drie eigenschappen die hier genoemd horen te worden:

- **Het faalt gesloten.** Een onbekende handelingsnaam, een onbekende gebruiker,
  een uitgezet account en een node zonder toekenning weigeren allemaal. Een
  tikfout in een route is een dichte deur en geen open.
- **Serverhandelingen vragen `is_superuser` en zijn niet te delegeren.** Tokens,
  gebruikers en instellingen zijn elk genoeg om jezelf de rest te geven, dus ze
  opsplitsen zou een scheiding suggereren die er niet is.
- **Een weigering wordt vastgelegd.** `require_perm()` schrijft een `audit`-rij
  met `outcome='geweigerd'` vóór de 403, zodat een poging tot iets wat niet mocht
  zichtbaar is in plaats van stil.

De interface is niet de grendel. Knoppen die een gebruiker niet mag indrukken
worden uitgeschakeld getekend met de reden erbij, maar de toekenning wordt in de
route afgedwongen; het sjabloon is de beleefdheid.

### Het audittrail

`audit` legt vast wie wat deed, met welke node, wanneer en hoe het afliep —
weigeringen inbegrepen. Vanuit de applicatie is het append-only: niets in de site
verwijdert een rij, en snoeien gebeurt alleen op `audit_retention_days`
(standaard 730).

Er staan met opzet **geen** wachtwoorden, tokens, beheeradressen of
instellingswaarden in. Deze repository is publiek en het trail is exporteerbaar;
wat het moet beantwoorden is wie iets aanraakte, niet wat het geheim was.

`audit.log()` slikt zijn eigen schrijffouten, zodat een falend trail een
firmware-upgrade die al onderweg is niet kan afbreken. Dat is een bewuste
afweging — de beschikbaarheid van de handeling boven de volledigheid van het
verslag — en er gaat een waarschuwing naar het gewone logboek, zodat een trail
dat niets meer bijhoudt niet stil blijft. Wie een manipulatiebestendig trail
nodig heeft, stuurt de rijen van de machine af: een tabel in hetzelfde
SQLite-bestand als de gegevens die ze beschrijft is niet betrouwbaarder dan het
proces dat erin schrijft.

### Sessies

Toestandsloze, met HMAC ondertekende cookies. Geen sessieopslag aan serverzijde.

```
cookie value = base64url(json({"u": username, "exp": ...})) + "." + HMAC-SHA256(payload)
```

De sleutel is de 32 willekeurige bytes in `secret.key`, bij de eerste start
aangemaakt met modus `0600`.

| Eigenschap | Waarde |
|---|---|
| Cookienaam | `mm_session` |
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
| Elk beheerformulier na inloggen | `mm_session` | met de sessie (12 u) |
| `POST /admin/login` | `mm_login` | 30 min (`LOGIN_TTL`) |

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
`MM_MAX_BODY_BYTES`, standaard 2 MB, op elke route en elke methode.

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
`MM_TRUSTED_PROXY_HOPS` vermeldingen van rechts naar binnen (standaard `1`, wat
overeenkomt met cloudflared rechtstreeks naar de toepassing), controleert of het
resultaat als IP-adres te lezen is, en valt anders terug op het transportadres.

Zet `MM_TRUSTED_PROXY_HOPS` op het aantal proxy's dat je werkelijk draait. Te
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

**Onbekende repeaters worden automatisch aangemaakt, maar niet meer publiek.**
Iedereen met een geldig token, of met publiceerrechten op de broker, kan nog
steeds een repeaterrij laten ontstaan — begrensd door `db.MAX_REPEATERS` — maar
die komt binnen met `is_public = 0` en blijft van de publieke pagina tot je hem
in `/admin` vrijgeeft, waar staat hoeveel er wachten.

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

- **Het topic noemt de publicist.** `meshmanager/<node_hex>/stats` — de node die het
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
gedeelde account behoudt `topic write meshmanager/#` zodat er halverwege de migratie
niets stukgaat — haal die regel weg zodra elke node zijn eigen account heeft, en
pas dan wordt het topic werkelijk afgedwongen. Details en voorbehouden in
[`mqtt.md`](mqtt.md#accounts-en-acls-per-node).

Zet `allow_anonymous false` niet uit.

Merk ook op dat de MQTT-weg de HTTP-lichaamsgrens van 2 MB volledig omzeilt.
Mosquitto's `message_size_limit 8192` is daar de enige bovengrens.

---

## De beheerendpoints op de node

### De repeater: `MeshManagerNet`

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

**Ongebreidelde groei in tabellen die de bewaartermijn nooit snoeide.**
`db.prune()` begrensde `packets` (leeftijd, rijmaximum, bytemaximum), `samples`
(leeftijd) en `neighbors` (leeftijd), maar niet `latest`, `repeater_cli` en
`repeaters` — telkens één rij per verschillende `(repeater_id, metric)`, per
CLI-parameter en per onderwerp met een publieke sleutel, en niets liet ze
verouderen. Een onvertrouwde MQTT-publicist bereikte alle drie vanaf het
`stats`-topic: `_handle_payload()` bewaarde elke sleutel in `metrics` woordelijk
en elke `neighbors[].prefix` zonder bovengrens en zonder hexcontrole, en de
`pubkey_prefix` uit de payload werd niet gevalideerd voordat
`get_or_create_repeater()` er een **publieke** rij voor aanmaakte
(`repeaters.is_public` stond standaard op 1) — dus rommelonderwerpen vervuilden
zowel `latest` als de publieke startpagina.

Mosquitto's `message_size_limit 8192` maakte hier een druppel van en geen
vloedgolf, en de eerlijke waarschijnlijkheid op deze installatie — een LAN achter
een VPN, één broker, ACL's per node — was klein: het vergde een node met geldige
of gelekte inloggegevens. Het is toch gerepareerd omdat het bytemaximum je hier
niet kon redden. Dat verwijdert enkel ooit uit `packets`, dus opzwelling in
`latest` zou het pakketten tot aan de FIFO-bodem hebben laten wegsnoeien terwijl
het bestand precies even groot bleef: een schijfbewaking die correct leest en de
verkeerde tabel leegt.

Drie maatregelen, volledig beschreven in
[`retention.md`](retention.md#de-tabellen-die-iemand-anders-kan-laten-groeien):

- **Geweigerd aan de grens.** `db.check_snapshot()` keurt één bericht voordat er
  iets geschreven wordt — het onderwerp als begrensde hex (`db.key_prefix`, 2-64
  tekens), hoogstens 128 metrieknamen van hoogstens 64 tekens, hoogstens 512
  burenregels — en beide ingest-wegen lopen erlangs. `_topic_parts()` keurt de
  node in het topic op dezelfde manier. Aantallen keuren het hele bericht af; een
  losse misvormde burenregel keurt alleen zichzelf af en wordt geteld in een
  waarschuwing. Het topic*voorvoegsel* wordt bewust niet tegen een lijst
  gehouden, zodat `meshmanager` en `meshcore` tijdens de hernoeming even ver
  komen.
- **`latest` en `repeater_cli` worden gesnoeid**: wezen, regels die binnen de
  bewaartermijn van de metingen niet ververst zijn *bij repeaters die zelf nog
  rapporteren* (een lang stille repeater houdt zijn laatst bekende waarden in
  plaats van een lege kaart te krijgen), en een plafond van 1000 metrieken / 200
  parameters per repeater — de regel die er bij misbruik werkelijk toe doet,
  want binnen de bewaartermijn staat de verouderingsregel machteloos.
- **`repeaters` wordt begrensd door te weigeren, niet door te snoeien.** Een
  repeater verwijderen verwijdert zijn historiek via `ON DELETE CASCADE`, dus
  boven `db.MAX_REPEATERS` (500) wordt een nieuw onderwerp simpelweg niet
  aangemaakt en het bericht afgewezen.

**Nieuwe repeaters komen niet langer publiek binnen.**
`get_or_create_repeater()` voegt nu in met `is_public = 0`. Een repeater
zichtbaar maken op een publieke site is een besluit van de beheerder en geen
bijwerking van een binnengekomen bericht; tot deze wijziging kon wie op het topic
mocht publiceren, publiceren op de voorpagina. Bestaande repeaters blijven
ongemoeid — de INSERT draait alleen voor een sleutel die nog nooit gezien is — en
de beheerpagina toont hoeveel er verborgen wachten, want verborgen binnenkomen
mag en ongemerkt binnenkomen niet. Het vinkje "bekijk nieuwe repeaters in
`/admin`; ze verschijnen standaard publiek" is daarmee achterhaald: ze komen nu
verborgen binnen en moeten worden vrijgegeven.

### Open: authenticiteit van het firmware-image

De route `/update` (en `start ota`) van de node gebruikt de ESP32-bibliotheek
`Update` (`firmware/examples/simple_repeater/MeshManagerNet.cpp`). Die controleert
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
- **Privacy** *(na de beoordeling opgelost)*: de publieke kaart en pagina's tonen
  nodenamen en posities van andere operatoren, die uit adverts komen die iedereen
  met een radio kan horen. Ten tijde van de beoordeling was de schakelaar
  `is_public` per repeater de enige knop die de eigenaar had — alles of niets, en
  standaard publiek, dus er viel niet uit te drukken dat een positie gevoeliger
  is dan een batterijmeting. Sindsdien zijn er twee dingen veranderd. Een nieuwe
  repeater komt nu verborgen binnen in plaats van publiek; en de keuze is niet
  langer alles of niets: `repeaters.show_position` en `repeaters.show_name`,
  allebei `INTEGER NOT NULL DEFAULT 1` zodat een upgrade niets verandert aan wat
  er eerder zichtbaar was. De handhaving is één SQL-view (`visible_contacts`)
  waaruit elke publieke leesweg selecteert, plus `db.public_name()` en
  `db.NEIGHBOR_NAME_SQL` voor de twee naambronnen die een view niet kan bereiken,
  en `tests/test_zichtbaarheid.py` draagt één test per endpoint. Wat ongewijzigd
  blijft, en met opzet: voor een node van derden valt er niets uit te zetten,
  want er is hier geen eigenaar om die schakelaar aan te geven en een formulier
  waarmee elke bezoeker elke node kon verbergen, zou een manier zijn om andermans
  repeater weg te poetsen. Zie [`privacy.md`](privacy.md).

### Kruiscontrole met de firmware

Een documentatieronde (commits `097efd8`, `f603aa5`) signaleerde verschillende
verschillen tussen code en documentatie. Hier beoordeeld op impact op de
beveiliging; verscheidene blijken in orde te zijn, en dat zeggen hoort erbij.

- **Het veld `"via"` is veilig omdat de server het negeert.** Doorgestuurde
  berichten dragen op het hoogste niveau `"via":"<node_hex>"`
  (`MeshManagerNet.cpp`, rond regel 2588). `mqtt_ingest.py` leest het nooit: de
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
- [ ] Zet `MM_TRUSTED_PROXY_HOPS` op het aantal proxy's dat werkelijk voor de
      toepassing staat (standaard `1`) — de inlogrem sleutelt erop
- [ ] Zet ook een toegangsslot vóór `/admin*`; de ingebouwde rem geldt per proces
      en vergeet bij een herstart
- [ ] Bind de container aan loopback en laat de reverse proxy erbij
- [ ] Voeg `Strict-Transport-Security` toe op de proxy
- [ ] Begrens de grootte van het requestlichaam ook op de proxy
- [ ] Stel de MQTT-poort niet bloot aan het internet
- [ ] Eén brokeraccount per node (`mosquitto/add-node-user.sh`), en haal daarna
      `topic write meshmanager/#` weg bij het gedeelde account in `mosquitto/acl`
- [ ] Controleer de kolom **Bron** in `/admin` — statistieken die via een
      onverwachte node binnenkomen zijn een blik waard
- [ ] Wijzig de standaardlogin `admin` / `meshcore` van de node voor hij op een netwerk komt
- [ ] Houd de beheerpagina's van nodes weg van elk onvertrouwd netwerk
- [ ] Maak een back-up van `/data/secret.key` en de databank; bewaar node-back-ups als geheimen
- [ ] Bekijk nieuwe repeaters in `/admin` — ze komen nu **verborgen** binnen en
      blijven van de publieke site tot je ze vrijgeeft; de pagina zegt hoeveel
      er wachten
- [ ] Trek API-tokens in die je niet meer gebruikt; ze verlopen nooit
- [ ] Houd minstens **twee** serverbeheerders, zodat één kwijtgeraakt wachtwoord
      geen ritje naar de opdrachtregel is
- [ ] Geef mensen de smalste rol waarmee ze kunnen werken — `technicus` dekt de
      klok en de zichtbaarheid zonder firmware open te zetten
- [ ] Kijk af en toe in `/admin/audit` naar regels met `geweigerd`; dat zijn
      pogingen tot iets wat niet mocht
- [ ] Stuur de auditregels van de machine af als je ze manipulatiebestendig wilt
