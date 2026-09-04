# Beheer

*[English](../admin.md)*

Accounts, tokens, sessies en de pagina's achter `/admin`.
[`security.md`](../security.md) behandelt het dreigingsmodel en de redenering
achter de mechanismen; dit document is het beheerdersperspectief erop.

## Het eerste account

Bij de eerste start maakt `main.bootstrap()` een `admin`-account aan **als
serverbeheerder**, met een wachtwoord uit `secrets.token_urlsafe(12)`, en
schrijft dat **één keer** naar stdout:

```
[meshmanager] Eerste start: admin-account aangemaakt.
[meshmanager] Gebruikersnaam: admin  Wachtwoord: <…>
[meshmanager] Wijzig dit meteen via /admin.
```

```bash
docker compose logs meshmanager | grep -i wachtwoord     # Docker
journalctl -u meshmanager | grep -i wachtwoord   # systemd
```

Het wordt alleen aangemaakt als de tabel `admins` **leeg** is, dus het komt nooit
terug nadat je het account verwijderd of hernoemd hebt.

### Een wachtwoord zetten vanaf de opdrachtregel

```bash
docker compose exec meshmanager python -m app.main set-password admin
```

Leest het wachtwoord van stdin, minstens 8 tekens. Een account dat hij zelf moet
*aanmaken* wordt meteen serverbeheerder — een herstelweg die een account zonder
rechten oplevert is geen herstelweg. Een account dat al bestaat houdt de rechten
die het had: een wachtwoord zetten is geen reden om iemand te promoveren.

```bash
docker compose exec meshstats python -m app.main promote admin
```

De tweede weg terug, voor het geval er wél accounts zijn maar geen enkele meer
serverbeheerder is. Dan is de gebruikerspagina onbereikbaar en helpt een
wachtwoord zetten niet. `promote` maakt het genoemde account serverbeheerder en
zet het weer aan.

## Wachtwoorden

`auth.hash_password()`: PBKDF2-HMAC-SHA256, **200 000 rondes**, een willekeurige
salt van 16 byte, bewaard als `pbkdf2$<salt hex>$<sleutel hex>`.

Elke vergelijking van iets geheims in deze toepassing — token, handtekening,
digest — loopt via `auth.eq()`, dat `hmac.compare_digest` is. Een gewone `==`
lekt via zijn looptijd hoeveel voorste tekens juist waren, waardoor raden een
wandeling teken voor teken wordt.

`auth.verify_dummy()` is de andere helft van die discipline. Bestaat de
gebruikersnaam niet, dan draait het inlogpad alsnog een volledige verificatie van
200 000 rondes tegen een weggooihash, zodat een verkeerde gebruikersnaam en een
verkeerd wachtwoord even duur zijn. Zonder dat verraadt de responstijd alleen al
welke accounts de moeite van het aanvallen waard zijn.

## Sessies

De sessiecookie `mm_session` is `base64(payload).hmac`, ondertekend met de
sleutel in `<data>/secret.key`. De payload bevat:

| Veld | Inhoud |
|---|---|
| `u` | Gebruikersnaam |
| `exp` | Verloop, `SESSION_TTL` = 12 uur na uitgifte |
| `v` | `password_stamp(username)` — 16 hextekens |

Er is **geen sessietabel**. `password_stamp()` is een HMAC over de huidige
`pw_hash` van het account, waardoor de `admins`-rij die toch al gelezen wordt de
intrekkingslijst is: wijzig een wachtwoord en elke cookie die onder het oude
geslagen is, valideert niet meer, want de stempel past niet meer. De hash zelf
verlaat de server nooit — alleen zijn HMAC wordt gepubliceerd.

`read_session()` controleert, in volgorde: handtekening, verloop, aanwezige
gebruikersnaam, kloppende stempel. Sessies van vóór deze controle dragen geen
stempel en worden geweigerd, wat de bedoelde eenmalige uitlogactie is.

De cookie is `HttpOnly`, `SameSite=Lax`, en `Secure` wanneer het verzoek over
HTTPS binnenkwam — afgelezen aan `X-Forwarded-Proto` als die er is, wat het
inloggen achter een tunnel juist laat werken.

**Je eigen wachtwoord wijzigen logt je niet uit.** `POST /admin/password` geeft
deze browser een nieuwe cookie onder de nieuwe stempel, zodat wie net het
wachtwoord wijzigde zijn beheerpagina houdt terwijl elke *andere* sessie
ongeldig wordt.

`secret.key` verwijderen maakt elke sessie **en** elk CSRF-token ongeldig, en is
het botte middel om iedereen eruit te zetten. Het maakt geen API-tokens ongeldig;
die worden met kale SHA-256 gehasht.

## CSRF

`auth.csrf_token(anchor)` is een HMAC over een cookiewaarde, afgekapt op 32
hextekens. Elke `POST` onder `/admin` draagt hem als formulierveld en
`routes_admin.check_csrf()` vergelijkt hem met het token dat uit de sessiecookie
afgeleid wordt.

Het inlogformulier heeft nog geen sessie, dus zijn token hangt aan een
**kortlevende inlognonce** in de cookie `mm_login` (`LOGIN_TTL` = 30 minuten),
vers uitgegeven bij elke weergave van de pagina. Het token is waardeloos voor een
aanvaller die de cookie waaruit het afgeleid is niet ook kan lezen.

Een formulier dat langer dan dat halfuur openstond, komt op dezelfde controle
terecht, en daarom luidt de melding *"Sessie verlopen — probeer opnieuw."* en
niet iets beschuldigends.

## Inlogbegrenzing

`ratelimit.py` houdt twee **onafhankelijke** emmers bij, want elk sluit een gat
dat de andere laat:

| Emmer | Stopt |
|---|---|
| `ip:<adres>` | Eén host die veel gebruikersnamen afgaat |
| `user:<naam>` | Een botnet dat pogingen over duizenden adressen spreidt |

De emmer op gebruikersnaam is degene die de linie werkelijk houdt, want het
clientadres is maar zo eerlijk als de proxyketen, terwijl de gebruikersnaam
rechtstreeks uit het formulier gelezen wordt.

| Constante | Waarde | Betekenis |
|---|---|---|
| `WINDOW_S` | 15 min | Mislukkingen ouder dan dit vallen uit de telling |
| `FREE_ATTEMPTS` | 5 | Pogingen zonder enige straf |
| `BASE_LOCK_S` | 2 s | Blokkade is `BASIS × 2^(n-1)` seconden na de vrije pogingen |
| `MAX_LOCK_IP_S` | 15 min | Plafond voor de adresemmer |
| `MAX_LOCK_USER_S` | 5 min | Plafond voor de gebruikersnaamemmer |
| `MAX_ENTRIES` | 4096 | Harde grens op het aantal bijgehouden sleutels; die het dichtst bij verval gaan eerst |

Blokkeren op gebruikersnaam betekent dat iedereen het admin-account met opzet kan
laten blokkeren, en daarom is dat plafond minuten en geen uren — overlast is beter
dan een onbegrensd raadbudget, en de beheerder kan de service herstarten om het
te wissen.

De toestand leeft **alleen in dit proces**. De installatie is één
uvicorn-proces, en een tabel in SQLite zou elke inlogpoging vanaf het internet in
een schrijfactie veranderen. Een herstart vergeet de tellers, wat het ene geval
is waarin een aanvaller iets wint — en herstarts kan hij niet uitlokken.

Een geslaagde inlog wist beide emmers: wie het wachtwoord bewees, is niet de
aanvaller.

### Welk adres geteld wordt

`ratelimit.client_ip()` leest `X-Forwarded-For` zelf en telt
`MM_TRUSTED_PROXY_HOPS` vermeldingen **van rechts naar binnen**.
`request.client.host` kan niet gebruikt worden: uvicorn draait met
`--forwarded-allow-ips "*"` en neemt dan de *eerste* vermelding, die elke client
er zelf in kan zetten. Proxy's voegen het adres toe dat ze zagen, dus
vermeldingen zijn betrouwbaar vanaf rechts, en alleen zo ver terug als er proxy's
zijn die je werkelijk draait.

Een te hoge waarde geeft een aanvaller een vervalsbare emmersleutel; een te lage
gooit elke bezoeker op één proxyadres. Verhoog hem alleen als je er echt een hop
bij zet.

## API-tokens

Aangemaakt in `/admin`, gebruikt als `Authorization: Bearer <token>` op de
ingest-endpoints.

- Het token is `mm_` + `secrets.token_urlsafe(32)`.
- Alleen zijn SHA-256 wordt bewaard. **De site kan hem niet opnieuw tonen.**
- Hij wordt één keer aan de browser gegeven via een `HttpOnly`-cookie van 60
  seconden (`mm_new_token`) en niet via de URL, zodat hij niet in een proxylogboek
  of een browsergeschiedenis belandt.
- Intrekken zet een vlag in plaats van de rij te verwijderen, zodat `last_used`
  blijft bestaan.
- `last_used` wordt bij elke geslaagde controle geschreven, en zo wordt een token
  dat niemand meer gebruikt zichtbaar.

Kale SHA-256 in plaats van PBKDF2 is met opzet: een willekeurig token van 32 byte
heeft geen raadbare structuur, dus de trage hash die een door mensen gekozen
wachtwoord beschermt levert niets op en zou 200 000 rondes kosten bij elk
ingestverzoek.

## Gebruikers, rollen en groepen

Toegang was alles-of-niets: wie kon inloggen, kon alles. Dat viel te verdedigen
zolang deze site alleen *toonde*. Ze doet inmiddels dingen — ze vraagt nodes uit
over LoRa (wat zendtijd kost), zet klokken, schrijft firmware en bepaalt wat de
wereld van een node te zien krijgt. Bij die handelingen hoort de vraag wie ze mag
doen.

Het model gaat over **handelingen** en niet over tabellen. `rbac.py` is de ene
plek die antwoordt op "mag deze gebruiker dit met déze node".

### De risicoklassen

Elke handeling draagt een risicoklasse. Die indeling is niet voor het
rechtenmodel verzonnen: de instellingenschrijver deelt zijn parameters al in
*gewoon*, *schrijft merkbaar* en *kan de bereikbaarheid afsnijden*, en dat is
precies de grens waarop je rechten wil knippen — wel de klok mogen zetten, geen
firmware mogen flashen. Er is er één vóórgezet, omdat "mag hier rondkijken" een
echte rol is die niets in gang zet.

| Klasse | Betekenis | Handelingen |
|---|---|---|
| `kijken` | Verandert niets | Een nodepagina openen, de opgeslagen instellingen lezen |
| `gewoon` | Gevolgen die vanzelf overgaan | Uitvragen (zendtijd), hernoemen, gewone instellingen schrijven |
| `merkbaar` | Verandert iets blijvends | Klok, zichtbaarheid en privacy, beheeradres, merkbare instellingen |
| `ingrijpend` | Kan de node onbereikbaar maken, of vernietigt gegevens | Firmware, risicovolle instellingen, verwijderen |

### Rollen zijn plafonds

Een rol is niets anders dan een plafond op die klasse. Vier rollen, vier klassen,
één op één — zo is "mag deze rol dit?" een vergelijking van twee getallen in
plaats van een lijst die per rol onderhouden moet worden, en dus onmogelijk om
half bij te werken wanneer er een handeling bij komt.

| Rol | Mag alles tot en met |
|---|---|
| `lezer` | `kijken` |
| `bediener` | `gewoon` |
| `technicus` | `merkbaar` |
| `beheerder` | `ingrijpend` |

Verworpen alternatief: losse rechten per handeling, aan te vinken. Flexibeler, en
in de praktijk onleesbaar — een matrix van veertien vinkjes maal elke node maal
elke groep is een matrix waarin niemand nog ziet wie wat mag, en "wie mocht deze
node flashen" is nu juist de vraag die beantwoordbaar moet blijven.

### Serverbeheerders

Naast die rollen staat één vlag, `admins.is_superuser`. Een serverbeheerder mag
alles op elke node, plus alles op **Server en site**: instellingen, bewaartermijn,
tokens, gebruikers, het volledige audittrail.

Serverhandelingen zijn niet per groep toe te kennen, en dat is een keuze en geen
gat. Ze zijn met z'n vijven, en drie ervan (tokens, gebruikers, instellingen)
zijn genoeg om zichzelf al het andere te geven. Ze opsplitsen zou een scheiding
suggereren die er niet is.

**De laatste actieve serverbeheerder kan zichzelf niet degraderen, uitzetten of
verwijderen.** Zonder die grendel is één verkeerd vinkje een installatie die
niemand meer kan beheren, en loopt de weg terug langs de opdrachtregel op de
server zelf.

### Toekenningen

Een toekenning bindt een **onderwerp** (een gebruiker of een gebruikersgroep) aan
een **voorwerp** (één node, een nodegroep, of alle nodes), met een rol en een
effect `allow` of `deny`.

| Kolom | Waarden |
|---|---|
| `subject_type` | `user`, `group` |
| `object_type` | `node`, `nodegroup`, `all` |
| `role` | `lezer`, `bediener`, `technicus`, `beheerder` (NULL bij een weigering) |
| `effect` | `allow`, `deny` |

### Botsende toekenningen

Eén regel, op één plek (`rbac.resolve()`), want twee regels op twee plaatsen
geven vroeg of laat een ander antwoord.

**Weigeren wint van toestaan.** Altijd, en ongeacht hoe specifiek de toestemming
was. Een weigering op "alle nodes" verslaat dus ook een toestemming die
rechtstreeks op één node gegeven is. Dat is de minst verrassende kant om fout te
gaan: wie een uitzondering intrekt, wil dat die intrekking het laatste woord
heeft, en niet dat er ergens nog een oudere, specifiekere rij ligt die hem
overstemt.

**Onder de toestemmingen wint de ruimste.** Wie via zijn groep lezer is en
rechtstreeks technicus, is technicus. Anders zou iemand aan een groep toevoegen
zijn rechten kunnen *verkleinen*, en dat is precies het soort verrassing waar dit
model vanaf moet.

**Geen toekenning is geen toegang.** Er is geen impliciete rol voor nodes waar
niemand iets over gezegd heeft. Zo'n node is voor een gewone gebruiker
onzichtbaar tot een serverbeheerder er iets over zegt.

Een weigering draagt geen rol: ze weigert alles op dat voorwerp. Een weigering
die zelf weer graduaties heeft ("mag hier hooguit lezer zijn") is niet te
overzien op een pagina, en het geval waarvoor je een weigering nodig hebt — deze
ene node niet, hoe dan ook — is een geval zonder graduaties.

### Nodes in geen enkele groep

Een repeater verschijnt vanzelf in de databank zodra er een bericht over hem
binnenkomt (`db.get_or_create_repeater`), en zit dan in geen enkele nodegroep.
Voor een gewone gebruiker is hij onzichtbaar tot een toekenning hem dekt —
rechtstreeks, of via een toekenning op *alle nodes*, wat de bedoelde ontsnapping
is zodat je niet elke nieuwe node in een groep hoeft te stoppen.

Stil onzichtbaar is hetzelfde probleem als stil verborgen, dus beide pagina's
tellen ze: **Nodes en repeaters** zegt hoeveel nodes er níét getoond worden, en
**Server en site** noemt de nodes die in geen enkele nodegroep zitten.

### Waar de controle gebeurt

`rbac.decide(user, action, rep)` is de enige functie die ja of nee zegt. Elke
schrijvende beheerroute komt erlangs via `routes_admin.require_perm()`, en
`test_rechten.py` loopt de router af om dat af te dwingen: een controle die je
per route overschrijft, is een controle die bij de volgende route vergeten wordt.
Routes die er terecht geen hebben, staan met hun reden in
`routes_admin.ROUTES_ZONDER_RECHTENCONTROLE`.

Dit is de tegenhanger van `commanding.route_for()`, dat zegt wat een node *kan*.
**Een knop werkt pas als ze allebei ja zeggen.** Zegt er één nee, dan verdwijnt de
knop niet — hij staat uitgeschakeld met de reden in zijn tooltip, en dat is de
lijn die deze site overal aanhoudt.

Sjablonen redeneren hier nooit zelf over. De route geeft `rechten` mee, een
woordenboek van handeling naar besluit, en het sjabloon vraagt
`rechten['node.firmware']`. Een sjabloon dat zelf redeneert is een tweede plek
waar het antwoord vandaan komt, en de eerste keer dat die twee het oneens zijn
belooft een knop iets wat de route weigert.

### API-tokens en dit model

**Een token is geen gebruiker.** Het geeft toegang tot de invoerwegen van de
HTTP-API (statistieken binnenbrengen, de opdrachtwachtrij ophalen, contacten
doorgeven) en tot niets onder `/admin`. Er is dus geen rol op te zetten en geen
node aan te koppelen, want er is geen handeling waar dat over zou gaan.

Waarom niet alsnog: een token dat rollen kan dragen is een tweede weg naar
dezelfde bevoegdheden, met een eigen intrekking en een eigen audittrail. Twee
wegen naar "mag deze firmware schrijven" is er één te veel — dat was het hele
punt van `rbac.py`. Tokens leggen wél vast wie ze aanmaakte
(`tokens.created_by`), want een token zonder eigenaar is een sleutel die niemand
durft in te trekken.

## Het audittrail

Zolang er één beheerder was, was "wie heeft deze node geflasht" geen vraag. Met
meerdere gebruikers is het er een, gesteld op een avond waarop iemand op een dak
moet klimmen.

Het past ook bij de lijn die de rest van dit project aanhoudt: een knop die
belooft wat hij niet waarmaakt is oneerlijk, en een handeling op afstand die geen
spoor achterlaat is dezelfde oneerlijkheid één stap later.

| Kolom | Inhoud |
|---|---|
| `ts` | UTC, ISO |
| `actor` | Gebruikersnaam, als tekst — zo overleeft hij het verwijderen van het account |
| `action` | De handelingsnaam uit `rbac.ACTIONS`, of `login` / `eigen.wachtwoord` |
| `object_type`, `object_id`, `object_name` | De node, mét naam — zo overleeft hij het verwijderen van de node |
| `outcome` | `ok`, `geweigerd`, `mislukt`, `deels` |
| `detail` | Een leesbare samenvatting van wat er gebeurde |
| `ip` | `ratelimit.client_ip()`, of leeg |

**Geweigerde pogingen staan er ook in**, met `outcome='geweigerd'`, en naast de
geslaagde in plaats van in een apart logboek: twee logboeken zijn twee plaatsen
om te kijken, en de tweede wordt vergeten. De weigering wordt door
`require_perm()` zelf geschreven, dus ze hangt niet af van wie eraan dacht.

`deels` is voor de opdrachten die langs twee wegen tegelijk vertrekken en er één
halen (`routes_admin._dispatch`); `mislukt` dekt "mocht wel, ging mis",
opvragingen zonder enige weg inbegrepen.

**Wat er nooit in gaat:** wachtwoorden, tokens, beheeradressen en de inhoud van
instellingen die een geheim kunnen zijn. `detail` vat samen *wát* er gebeurde
("naar 1.10.0", "via de monitor"), niet de nuttige lading. Deze repository is
publiek en het trail is exporteerbaar.

`audit.log()` slikt zijn eigen fouten: een volle schijf of een gelockte databank
mag een firmware-upgrade die al onderweg is niet doen ontploffen. Er gaat wel een
regel naar het gewone logboek, zodat een trail dat stiekem niets meer bijhoudt
niet stil blijft.

Regels blijven `audit_retention_days` staan, standaard **730 dagen** — veel
langer dan pakketten (7) of metingen (180), want dit is de enige tabel waarvan de
waarde juist in de ouderdom zit. Het snoeien gebeurt in `retention.run_once()` en
niet in `db.prune()`: die functie gaat over meetgegevens, en het trail is geen
meetgegeven maar het geheugen van wie wat deed.

Waar je het ziet:

| Pagina | Toont |
|---|---|
| `GET /admin/repeaters/{rid}` | De laatste 15 regels voor **deze node** — de vraag wordt gesteld terwijl je naar de node kijkt |
| `GET /admin/server` `#trail` | De laatste 40 regels van de hele installatie |
| `GET /admin/audit` | Het volledige trail, alleen voor serverbeheerders |
| `GET /admin/account` | Je eigen laatste 20 regels |

## Een bestaande installatie migreren

De upgrade is additief en gebouwd rond één harde eis: **hij mag de eigenaar niet
buitensluiten.**

`is_superuser` komt erbij met `DEFAULT 0`, met opzet, want een kolom die standaard
"volledige rechten" zegt faalt de verkeerde kant op — een `INSERT` die de kolom
vergeet levert dan stilzwijgend een serverbeheerder op. `ALTER TABLE ADD COLUMN`
vult bestaande rijen met die standaard, wat op zichzelf elke bestaande beheerder
van al zijn rechten zou ontdoen. `db.POST_MIGRATIONS` zet dat recht op het moment
dat de kolom aangemaakt wordt, en alleen dan:

```sql
UPDATE admins SET is_superuser=1
```

Gebonden aan het aanmaken van de kolom en niet aan "is er al een
serverbeheerder", want dat laatste zou bij elke start opnieuw kijken — en dan
zet een beheerder die zichzelf bewust degradeert zichzelf bij de volgende
herstart weer terug.

Dus: wie gisteren alles mocht, mag dat vandaag nog, met hetzelfde wachtwoord en
dezelfde sessie. Twee tests bewaken dat, waarvan er één de hele keten aflegt op
een databank die alleen de oude `admins`-tabel met twee kolommen kent.

Verder verandert er bij de upgrade niets. Er zijn nog geen groepen en geen
toekenningen, wat betekent dat een gewone gebruiker — en die zijn er ook nog niet
— niets zou zien. Dat is precies de toestand van vóór dit model.

## Twee werelden

De beheerpagina was één lange lijst secties geworden, in de volgorde waarin ze
ooit toegevoegd zijn. Een knop die een node over de radio uitvraagt stond naast
het invoerveld voor de bewaartermijn van de databank. Die twee horen niet in
dezelfde visuele rang: de ene kost zendtijd op een gedeelde band en raakt een
apparaat op een dak, de andere zet je met een tweede klik weer terug.

Sinds de splitsing zijn er twee werelden, met een tabbalk ertussen:

| URL | Wereld |
|---|---|
| `GET /admin` | **Nodes en repeaters** — alles wat een handeling op of informatie over een fysiek apparaat is |
| `GET /admin/repeaters/{rid}` | Eén node: identiteit en versies, zichtbaarheid, uitvragen, klok, firmware, verwijderen |
| `GET /admin/firmware` | **Firmware** — welke release waar draait, welke er zijn, en wie er een image kan krijgen |
| `GET /admin/server` | **Server en site** (in het menu: *Server, gebruikers en site*) — alles wat deze installatie configureert en geen apparaat raakt: gebruikers, groepen, toekenningen, tokens, opslag, weergave |
| `GET /admin/account` | **Mijn account** — je eigen wachtwoord, je rollen, en je eigen auditregels |
| `GET /admin/audit` | Het volledige audittrail (alleen serverbeheerders) |

De tab heet in het menu *Server, gebruikers en site*, en binnen die wereld staat
een sub-balk met de secties als ankers. Dat is er gekomen omdat de eigenaar van
een installatie het gebruikersbeheer niet kon vinden achter het kale label
*Server en site*; de tab *Monitors* (welke node welke node uitvraagt) heette
toen nog *Beheerders*. De indeling van de beheerpagina's zelf staat in
[`beheer-ux.md`](beheer-ux.md).

**Server en site** is de ene tab die deze site *verbergt* in plaats van
uitschakelt. Erachter zit geen enkele handeling die een gewone gebruiker mag, en
een tab die altijd 403 geeft is een gesloten deur met een bordje erop in plaats
van een uitleg. Binnen een pagina waar je wél iets mag, geldt de regel omgekeerd:
knoppen blijven staan, uit, met de reden erbij.

`GET /admin/account` bestaat omdat het wachtwoordformulier op **Server en site**
stond. Omdat die pagina alleen voor serverbeheerders is, zou een gebruiker met
rechten op twee nodes anders zijn eigen wachtwoord niet meer kunnen wijzigen.

De POST-routes zijn gebleven waar ze stonden, zodat een beheerpagina die al in
een tabblad openstond bij de volgende klik geen 404 oplevert.
`GET /admin/repeaters/{rid}/settings` is de enige URL die verhuisd is; hij leidt
om naar `/admin/repeaters/{rid}` en neemt zijn querystring mee, zodat een melding
onderweg niet verloren gaat.

### Taal

De beheerpagina's zijn eentalig Nederlands, en dat is een keuze en geen
achterstand. De publieke site is tweetalig doordat elke vertaalbare knoop een
`data-i18n`-sleutel draagt; beheer heeft er geen enkele, en de teksten daar zijn
geen labels maar alinea's die uitleggen wat de site wel en niet weet over een
node op een dak. Dat vertalen is honderden sleutels en een tweede plek waar
dezelfde nuance juist moet blijven — en één verkeerd vertaalde zin over een klok
die niet terug te draaien is, kost een ritje naar dat dak. Als het gebeurt, hoort
het in één stap te gebeuren die alle beheerteksten tegelijk van sleutels
voorziet, niet knop voor knop.

Dus ontbreekt de taalknop op de beheerpagina's, en zet
`<html data-lang-lock="nl">` ook de door JavaScript gebouwde teksten (relatieve
tijden) op Nederlands vast. Dat repareert meteen een bestaand euvel: wie op de
publieke site ooit Engels koos, kreeg Engelse relatieve tijden en een Engels
`lang`-attribuut boven Nederlandse tekst.

## Nodes en repeaters — `GET /admin`

De lijst is gegroepeerd op **beheerniveau**, omdat wat je met een node kunt doen
per groep verschilt en dat hier de enige indeling is die de knoppen eronder
verklaart. Het niveau is een *waarneming*, nooit een instelling — er is nergens
een knop om het te zetten, het volgt uit wat er binnenkomt, en de zin achter elke
node zegt waaraan we het zien. Het wordt op één plek afgeleid,
`commanding._level()`, naast `describe()`, zodat er geen tweede definitie kan
ontstaan die van de eerste wegloopt.

| Niveau | Betekenis | Wat werkt |
|---|---|---|
| `full_managed` | Onze firmware met MQTT-koppeling: de node publiceert zijn eigen cijfers en meldt een firmwareversie | Uitvragen, instellingen, klok, en — als er een IP-pad is — een firmware-upgrade |
| `semi_managed` | Geen firmware van ons, wél rechten op zijn CLI: een monitorende node vraagt hem over LoRa uit, of de poller logt in met zijn wachtwoord | Instellingen lezen, begrensd schrijven, de klok zetten |
| `unmanaged` | Alleen telemetrie: waargenomen in het verkeer en verder niets | Niets — de knoppen staan er, uitgeschakeld, elk met zijn reden |

Het niveau kijkt bewust **niet** naar `broker_connected`. Wat er nu openstaat
staat in `route["mqtt"]`; wat een node *is* staat in `route["level"]`. Een full
managed node achter een weggevallen broker blijft full managed — er is alleen op
dit ogenblik geen weg. Die twee door elkaar halen zou het niveau laten meebewegen
met het netwerk van de server in plaats van met de node.

Of een firmware-upgrade mogelijk is, is **niet** uit het niveau af te leiden: een
full managed node zonder IP-pad neemt commando's aan maar geen image van een
megabyte. Dat is een apart veld uit de firmwareweg.

Per node toont de lijst het sleutelprefix, via welke node zijn cijfers
binnenkomen, de firmware, wanneer hij het laatst gezien is, de weg die nu
openstaat, en de publiek/verborgen-schakelaar. Hernoemen en verwijderen staan er
*niet*: die horen op de eigen pagina van die node, waar zijn naam en sleutel
bovenaan staan. Een prullenbakje in een dichte tabelrij is precies hoe je de
verkeerde node wist, en dat is de duurste fout die deze site toelaat.

## Eén node — `GET /admin/repeaters/{rid}`

Alles over één apparaat, in oplopende onomkeerbaarheid: identiteit en versies,
zichtbaarheid, uitvragen (leest), klok (schrijft één getal), firmware (schrijft
het hele apparaat), verwijderen.

| Veld | Betekenis |
|---|---|
| `route` | `commanding.describe(rep)` — niveau, reden van dat niveau, of een knop iets kan en zo niet, welke blocker |
| `settings_rows` | De bewaarde CLI-parameters en hun `updated`-tijdstempels. Een NULL-waarde toont als "(geen antwoord)" |
| `queued_since` | Een opvraging die er **nog steeds** staat: er heeft niets gepolld sinds de klik |
| `delivered_since` | Wanneer de wachtrij het laatst een opvraging uitreikte |
| `delivery_unanswered` | Waar als het nieuwste bewaarde antwoord ouder is dan die uitreiking |
| `requested`, `status` | `mqtt`, `queued`, `both` of `none` van de laatste opvraag- of statusklik |
| `clock_route` | `clocksync.time_route(rep)` — welke node de tijd zou krijgen |
| `clock_sent` | Wanneer deze site die node het laatst een tijd stuurde |
| `clock`, `clock_wait` | De uitkomst van de laatste klik, en de wachttijd in minuten |
| `clocksync_reason` | De reden uit de laatste klokcontrole, zodat een weigering hier meteen zegt wát er mis was |
| `broker` | `mqtt_ingest.can_publish()` — zie hieronder |

`queued_since` en `delivery_unanswered` bestaan omdat de wachtrij bij het lezen
gewist wordt: staat het verzoek er nog, dan heeft er niets gepolld sinds de knop
ingedrukt werd, en is het weg, dan nam de poller het mee en is de stilte die
volgt de zijne. Zonder dat onderscheid zien beide er identiek uit — een pagina
die "opvraging gestart" zegt en nooit verandert.

De knoppen worden uit `route` getekend, en een knop die niets kan doen is
uitgeschakeld **en zegt waarom**. De vereiste firmwareversie komt uit die route
in plaats van hier apart berekend te worden: welke versie nodig is hangt af van
de weg (1.8.0 voor de node zelf, 1.9.0 voor een monitor). Zie
[`commanding.md`](commanding.md).

De klokknop kijkt daarnaast naar `broker`. `clocksync.time_route()` doet dat
bewust niet — die vraag hoort bij het versturen en niet bij de weg — maar de knop
hoort het wel te weten: zonder verbinding eindigde een klik op "er is niets
verstuurd", terwijl de pagina dat vooraf kon zeggen.

Handelingen dragen hun prijs in hun vorm en niet alleen in hun tekst: een blauwe
linkerrand met het etiket "kost zendtijd" voor wat leest maar zendtijd kost,
oranje voor wat op het apparaat schrijft, rood voor wat onomkeerbaar is. De klok-
en verwijderknop vragen een bevestiging die de node bij naam en sleutelprefix
noemt, omdat de vraag over *die* node moet gaan en niet over "deze".

Het firmwareblok verwijst naar `/admin/firmware` in plaats van het te herhalen.
Welke release waar draait is een vraag die je over alle nodes tegelijk stelt, dus
heeft die een eigen pagina; de knop per node staat daar ook. De versie die nu op
deze node staat wordt evenmin herhaald — die staat hierboven bij *Identiteit en
versies*, en twee plaatsen die hetzelfde getal tonen zijn twee plaatsen die het
een keer oneens worden. Of een upgrade mogelijk is volgt **niet** uit
`route["level"]`: dat oordeel is `firmware.ota_route()`.

### Namen bij de kanalen — `#kanalen`

De telemetrie van een sensornode komt binnen als CayenneLPP, en dat formaat is een
reeks drietallen: kanaalnummer, type, waarde. Er is **geen naamveld**, niet in het
formaat en niet in MeshCore, dat alleen een oplopende kanaalteller kent. Wat een node
stuurt is dus letterlijk "kanaal 6, switch, 1" en nooit "google is bereikbaar". Dit
formulier is waar die koppeling ingevuld wordt; het is geen gemak, het is de enige
weg.

Het formulier toont alleen de kanalen die de node werkelijk gestuurd heeft — gelezen
uit `latest` en niet uit een lijst die iemand eerst moest invullen, want welke
kanalen een node heeft weet alleen die node. Een kanaal verschijnt zodra er één
meting van binnen is. Elke rij neemt een naam en, alleen bij een generic sensor, een
eenheid: `LPP_GENERIC_SENSOR` is vier unsigned byte met vermenigvuldiger 1 en belooft
niets over wát er gemeten wordt, dus `12` zonder `ms` erachter is een getal zonder
betekenis. Een spanning en een temperatuur hebben hun eenheid al uit het LPP-type, en
een toestand hoort er geen te hebben.

Het kanaalnummer reist **in de veldnaam** (`ch_naam_<N>`), nooit als rangnummer, en
een nummer dat de node niet gestuurd heeft wordt geweigerd. Naam en eenheid worden
per kanaal in één actie geschreven, want een rij bewaart beide en los na elkaar
schrijven zou de tweede de eerste laten wissen.

Hij post naar `/admin/repeaters/{rid}/channels` en vraagt `node.hernoemen` — dit is
naamgeving en niets anders: er gaat geen pakket de lucht in, de node merkt er niets
van, en het is dezelfde soort ingreep als het hernoemen van de node zelf, een laag
dieper.

> **Waarom kanaalnummers nooit mogen verschuiven.** De bewaarde naam hangt aan het
> nummer, want dat is het enige wat het pakket draagt. Laat de zendende kant een
> dienst vallen en schuift de rest op, dan wijst elke naam hier stil naar de verkeerde
> dienst: geen foutmelding, alleen verkeerde cijfers. Een gat in de nummering is dus
> geen rommel die opgeruimd hoort te worden — het is het bewijs dat er niets
> verschoven is. De pagina zegt dat met zoveel woorden, zodat een latere lezer niet op
> het idee komt die gaten weg te werken.

Een leeg naamveld wist de naam. Het kanaal staat dan als "kanaal N" op de publieke
pagina en verdwijnt **niet**: een naamloze meting is nog steeds een meting.

### Beheer over IP — `#eigen-api`

Voor een node die zijn eigen HTTP-API aanbiedt: een MeshUptime-sensornode. Hier
gaat het adres in, en vanaf dan leest de server elke vijf minuten
`/status.json` en werken de knoppen in dit blok — advert (flood of zerohop), de
klok zetten, de regio zetten, herstarten, en de instellingen uit `/cfg.json`.

**Het blok zegt ronduit dat dit over IP gaat en niet over het mesh**, en wat dat
betekent: valt de WiFi weg, dan valt dit hele blok weg. Dat is gemeten en niet
theoretisch, en de mesh-weg die voor dat geval bedoeld is werkt nog niet. De
pagina zegt beide, in plaats van het tweede te verzwijgen.

Een apart veld naast het *beheeradres* op de firmwarepagina, met opzet: dat veld
betekent "daar staat onze repeaterfirmware", met een firmware-upgrade erachter, en
deze node draait die firmware niet. Eén veld voor beide zou een image aanbieden
aan een bord waarvan wij de bouwomgeving niet kennen.

**Het adres invullen mag alleen een serverbeheerder.** Wissen niet. De server
stuurt de inloggegevens die elke node openen naar dat adres, en `node.beheeradres`
is per node delegeerbaar — zie
[`security.md`](security.md#waar-de-vlootinloggegevens-heen-mogen).

Het blok toont ook de **toegangslijst** van de node, alleen om te lezen. Dat is
waar de mesh-weg op stukloopt: een monitor die inlogt en geen antwoord krijgt,
staat meestal niet in die lijst en heeft ook het adminwachtwoord niet. Dan is
"geen antwoord" een weigering en geen storing, en dat is een ander probleem.

### Alarmen — `#alarmen`

**Telemetrie is polling; een alarm is een trap.** De cijfers en grafieken van een
node komen van een uitvraagronde: regelmatig, volledig, en blind voor wat er
tussen twee rondes gebeurde. Een alarm komt op het moment dat er iets gebeurt,
draagt één feit, en is er misschien niet — de sensornode stuurt het naar de
repeaters die in zijn toegangslijst alarmrecht hebben, en die publiceren het
meteen.

Per alarm: de tijd, de tekst, de ernst waar die uit de tekst af te leiden is, en
de bron (`mesh`, `ip` of `test`). Niet-bevestigde alarmen worden ook als badge op
de nodelijst geteld, want het punt van een trap is dat je hem ziet zonder ernaar
te zoeken.

**Bevestigen haalt een alarm niet weg** — het legt vast dat iemand het gezien
heeft. Er is met opzet geen knop die er een verwijdert: een melding die je zonder
spoor kunt wegklikken, is een melding die achteraf niet meer na te vertellen is.
Opruimen doet de bewaartermijn, samen met de rest van de historiek. Er is één knop
voor één alarm en één voor alle openstaande van een node, want een node die een
uur onbereikbaar was levert tientallen regels op, en die één voor één wegklikken
betekent dat niemand het doet.

### Zichtbaarheid op de site — `#zichtbaarheid`

Drie schakelaars in één blok, in afnemende zwaarte: `is_public` haalt de hele
node van de site, `show_position` haalt er één ding uit, `show_name` een ander.
Eén blok en geen drie secties, want het is één vraag — wat ziet een bezoeker van
deze node — met drie antwoorden.

Alle drie posten naar `/admin/repeaters/{rid}/toggle` met `what=public` (de
standaard, zodat een pagina die nog in een tabblad openstaat blijft werken),
`position` of `name`. De kolom wordt in een vaste tabel opgezocht en niet uit het
verzoek overgenomen; een kolomnaam die van buiten komt en rechtstreeks in een
`UPDATE` belandt, is een openstaande deur naar elke andere kolom van die tabel.

Elke schakelaar staat naast wat hij werkelijk doet, en de alinea die zegt wat
**geen** enkele schakelaar verbergt hoort bij het blok en niet bij de voetnoten:
de sleutelprefix zit in elke advert die de node uitzendt, en de slug in
`/r/<slug>` is uit de naam gemaakt toen de rij ontstond en verandert niet mee bij
een hernoeming. Het volledige verhaal — wat verdwijnt, wat blijft, en hoe het
gehandhaafd wordt over de zeven publieke routes die het anders zouden lekken —
staat in [`privacy.md`](privacy.md).

De nodelijst op `/admin` toont bij een node die publiek is maar niet helemaal,
een tweede pil die doorklikt, want "publiek" alleen belooft daar meer dan het
waarmaakt.

### Het pakketfilter — `#pakketfilter`

Welke van *andermans* pakketten deze repeater nog doorstuurt. Het blok staat er
altijd, ook bij een node die de server niet over IP bereikt, want zijn eerste
taak is de vraag "draait hier een filter" beantwoorden — en dat antwoord mag niet
afhangen van of de node nu toevallig online is.

Drie bronnen, drie vragen, met opzet niet samengevoegd:

| Contextsleutel | Komt uit | Beantwoordt |
|---|---|---|
| `filter_seen` | `repeater_filter`, gevuld uit het laatste statistiekenbericht | Staat er een filter aan, en wat gooide het weg, per reden |
| `filter_live` | `GET /api/filter` op de node zelf | Wat zijn de regels nu — de tabellen die te groot zijn om in elk bericht mee te reizen |
| `filter_route` | `pktfilter.filter_route(rep)` | Mag en kan deze site ze wijzigen |

Ze door elkaar halen levert precies één soort fout op: een pagina die beweert dat
er geen filter aanstaat omdat de node net niet antwoordde.

`filter_seen` heeft **drie** toestanden en geen twee. "Nooit iets gemeld" —
meestal firmware ouder dan 2.3.0 — is geen bewering dat er geen filter aanstaat.
Een node die `uit (veilige modus)` meldt is een derde: die is herhaaldelijk
opnieuw opgestart en liet zijn eigen filter deze keer uit, terwijl de regels
gewoon bewaard blijven.

Schrijven gaat naar `/admin/repeaters/{rid}/filter`, met één formulier per
handeling. De commandoregel wordt samengesteld uit een verborgen veld plus
getalvelden met hun eigen minimum en maximum — er is met opzet geen tekstvak
waarin je een hele regel kunt typen, want dan zou de risicoweging afhangen van
hoe iemand het toevallig spelt.

De risicoklasse volgt wat de regel *blokkeert*, niet hoe het formulier eruitziet.
`hops 05 4` en `hops 05 0` zijn hetzelfde invoerveld en twee verschillende
bevoegdheden; de tweede zet groepstekst helemaal stil en vraagt de naam van de
node. `filter on` weegt zwaarder als zo'n regel al klaarstaat, want dán is dat de
klik die het verkeer werkelijk stilzet.

De weg terug is de *goedkoopste* handeling: `off` en `reset` vallen onder
`node.filter.gewoon`, lichter dan aanzetten. Een rol die een filter niet aan mag
zetten, mag er wel een uitzetten. En het echte vangnet loopt helemaal niet langs
deze pagina — `filter off` over de mesh-CLI heeft geen WiFi, geen beheerpagina en
geen server nodig. Zie [`packet-filter.md`](packet-filter.md).

Het audittrail legt de zin vast en niet de commandoregel: "GRP_TXT (05) helemaal
niet meer doorsturen" is over een half jaar nog te lezen, `hops 05 0` niet.

## Server en site — `GET /admin/server`

| Anker | Blok | Inhoud |
|---|---|---|
| `#toegang` | Toegang | Als wie je bent ingelogd, en een link naar **Mijn account** voor het wachtwoord |
| `#gebruikers` | Gebruikers | Accounts, de vlaggen serverbeheerder en uit, een wachtwoord zetten voor iemand anders, verwijderen |
| `#groepen` | Groepen | Gebruikersgroepen en nodegroepen, hun leden, en het aantal nodes in geen enkele groep |
| `#toekenningen` | Toekenningen | Wie wat mag op welke nodes, met de conflictregel erbij |
| `#trail` | Audittrail | De laatste 40 regels, met een link naar het volledige trail |
| `#tokens` | API-tokens | Actieve tokens met `created_at`, `created_by` en `last_used`; aanmaken en intrekken |
| `#opslag` | Bewaartermijn en opslag | De bewaartermijn- en FIFO-velden samen met `retention.overview()`: bestandsgrootte tegenover de bovengrens, aantal pakketten, de werkelijk gedekte periode, en de laatste opruimronde |
| `#weergave` | Weergave | `heartbeat_min`, `history_ranges`, en de blokvolgorde van de publieke pagina |
| `#cli-params` | Op te vragen parameters | `cli_params` — één lijst voor alle repeaters |
| `#kloksync` | Kloksynchronisatie | `clocksync.status()` plus `clocksync.targets()` — per repeater of hij bereikt kan worden en zo niet, waarom |
| `#invoer` | Gegevensinvoer | `mqtt_ingest.status()`: verbonden, broker, topics, nodes per topicvoorvoegsel, berichten, pakketten, fouten |
| `#tsdb` | Metingen | `tsdb.status()`: bereikbaar, geschreven punten, batches, wachtrijdiepte, uitgeweken naar SQLite, laatste fout |

Instellen en uitkomst staan in één blok en niet in twee secties ver uit elkaar:
het getal dat je invult en het gevolg dat het heeft zijn dezelfde vraag, en wie
de termijn verlaagt hoort meteen te zien dat de bovengrens hem misschien toch
eerder afsnijdt.

`cli_params` stond op de pagina van één repeater terwijl hij voor allemaal geldt;
wie hem daar wijzigde, wijzigde hem stilletjes ook voor de andere nodes.

`clock_targets` wordt berekend met **dezelfde** `time_route()` die de knop
gebruikt, met de monitorweg dicht. Toen die redenering hier zijn eigen kopie had,
kon de pagina van een repeater iets anders beweren dan de dagelijkse ronde deed —
en dat verschil valt pas op als iemand de logboeken naast de beheerpagina legt.
De knop om het *nu* te doen staat waar hij hoort: op de pagina van die ene node,
met zijn bevestigingsstap.

### Instellingen, en wat ze overrulen

| Veld | Begrensd op | Effect |
|---|---|---|
| `heartbeat_min` | 1–1440 | Minuten; dwingt een meetpunt af ook als de waarde niet veranderde |
| `retention_days` | 1–3650 | Bewaartermijn voor metingen |
| `packet_retention_days` | 1–365 | Bewaartermijn voor pakketten, en het venster van de heatmap |
| `packet_max_rows` | 1000–50 000 000 | FIFO-bovengrens op de pakkettentabel |
| `db_max_mb` | 16–1 000 000 | FIFO-bovengrens op het databankbestand, WAL inbegrepen |
| `history_ranges` | 1–8760 per waarde | De uurknoppen bij de grafieken op een repeaterpagina |

Een instelling die hier staat **gaat vóór de omgevingsvariabele** met dezelfde
betekenis, dus een bewaartermijn verhogen vraagt geen herstart van de container.

Elk veld op `POST /admin/settings` is optioneel, en dat is de kern van de zaak en
geen slordigheid: ze staan nu over twee formulieren verdeeld. Met verplichte
velden zou het ene formulier de waarden van het andere als verborgen velden
moeten meesturen, en dan overschrijft een pagina die even openstond stilletjes
een instelling die intussen elders gewijzigd is. Ontbreken betekent "dit
formulier ging er niet over". De sentinel is `None` en niet `0`, want `0` is voor
deze velden geen geldige waarde en "niet ingevuld" is iets anders dan "op nul
gezet". Is er wél een termijn of grens gewijzigd, dan draait meteen
`retention.run_once()`, zodat het resultaat op de pagina staat waar je net op
klikte; het weergaveformulier lokt geen opruimronde uit. Details in
[`retention.md`](retention.md#het-instellingenformulier).

De indeling is een JSON-lijst van `{key, visible}`, gevalideerd door
`metrics.parse_layout()`: onbekende sleutels vallen weg, dubbele worden
genegeerd, en elk blok dat in de bewaarde waarde ontbreekt wordt in zijn
standaardvolgorde achteraan toegevoegd. Een blok zonder iets te tonen wordt bij
het renderen overgeslagen, dus een blok verbergen en er geen gegevens voor hebben
zien er voor een bezoeker hetzelfde uit.

## Een repeater publiek maken

`is_public` bepaalt of de node überhaupt op de site staat: de startpagina,
`/r/<slug>`, en elke publieke API-route. `_public_repeater()` in `routes_api.py`
antwoordt 404 voor een niet-publieke slug, zodat een repeater die uitgezet is
onzichtbaar is en niet alleen niet-gelinkt.

`show_position` en `show_name` zijn het fijnmazige paar ernaast, voor de node die
wel op de site mag staan maar niet met alles erop. Ze staan standaard op 1, dus
een databank die de kolommen er bij een upgrade bij krijgt, toont precies wat ze
de dag ervoor toonde. Zie [`privacy.md`](privacy.md).

**Een repeater die vanzelf verschijnt komt verborgen binnen.** Alles wat uit een
binnengekomen MQTT- of HTTP-bericht ontstaat, krijgt `is_public = 0`: dit is een
publieke site, en een repeater zichtbaar maken is jouw besluit en geen bijwerking
van het feit dat er een bericht binnenkwam. Repeaters die al bestonden houden wat
ze hadden. Bovenaan **Nodes en repeaters** staat hoeveel er wachten — verborgen
binnenkomen mag, ongemerkt binnenkomen niet — en de pil *verborgen* bij de node
zelf is hoe je er een vrijgeeft.

Een repeater verwijderen wist zijn rijen in `samples`, `latest` en `neighbors`
expliciet, en daarna de rij zelf. Zijn pakketten blijven staan:
`packets.observer` is een sleutelprefix en geen foreign key, en een ontvangst is
een feit over het mesh en niet over de rij die net verwijderd is.

## Wat het beheerdeel *niet* doet

- **Geen zelfbediening.** Geen registratie, geen wachtwoordherstel per e-mail en
  geen "wachtwoord vergeten"-link. Een serverbeheerder zet een wachtwoord voor
  iemand anders zonder het te kunnen teruglezen; de weg naar binnen als *niemand*
  meer kan inloggen is de opdrachtregel.
- **Geen rechten per handeling.** Rollen zijn plafonds op een risicoklasse en
  geen matrix van vinkjes. Zie [Rollen zijn plafonds](#rollen-zijn-plafonds).
- **Geen API-tokens per node.** Een token is geen gebruiker; zie [API-tokens en
  dit model](#api-tokens-en-dit-model).
- **Geen auditregels verwijderen.** Niet vanaf de site. Ze verdwijnen op hun
  eigen bewaartermijn.
- **Geen `/health`-endpoint.** De containercontrole haalt `/` op.

## Een publieke installatie harden

De toepassing begrenst `POST /admin/login` en zet haar eigen beveiligingsheaders,
maar de toestand van die begrenzing leeft in één proces en wordt bij een herstart
vergeten. Is de site vanaf het internet bereikbaar, dan is een tweede slot op
`/admin*` alsnog de moeite: Cloudflare Access, een IP-witte lijst, of een
snelheidsbegrenzing op de proxy. De rest van de site en `/api/v1/*` mogen open
blijven.

De volledige checklist staat in
[`security.md`](../security.md#checklist-for-a-public-deployment).

## Verwante documenten

| Vraag | Document |
|---|---|
| Dreigingsmodel en mechanismen | [`security.md`](../security.md) |
| De routes achter deze pagina's | [`api.md`](api.md#beheerroutes) |
| Wat de knoppen wel en niet kunnen bereiken | [`commanding.md`](commanding.md) |
| De klokknop | [`clocksync.md`](clocksync.md#de-knop) |
| Waar de instellingen bewaard worden | [`database.md`](database.md#settings) |
