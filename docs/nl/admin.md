# Beheer

*[English](../admin.md)*

Accounts, tokens, sessies en de pagina's achter `/admin`.
[`security.md`](../security.md) behandelt het dreigingsmodel en de redenering
achter de mechanismen; dit document is het beheerdersperspectief erop.

## Het eerste account

Bij de eerste start maakt `main.bootstrap()` een `admin`-account aan met een
wachtwoord uit `secrets.token_urlsafe(12)` en schrijft dat **één keer** naar
stdout:

```
[mc-repeater-stats] Eerste start: admin-account aangemaakt.
[mc-repeater-stats] Gebruikersnaam: admin  Wachtwoord: <…>
[mc-repeater-stats] Wijzig dit meteen via /admin.
```

```bash
docker compose logs meshstats | grep -i wachtwoord     # Docker
journalctl -u mc-repeater-stats | grep -i wachtwoord   # systemd
```

Het wordt alleen aangemaakt als de tabel `admins` **leeg** is, dus het komt nooit
terug nadat je het account verwijderd of hernoemd hebt.

### Een wachtwoord zetten vanaf de opdrachtregel

```bash
docker compose exec meshstats python -m app.main set-password admin
```

Leest het wachtwoord van stdin, minstens 8 tekens, en maakt het account aan als
het niet bestaat. Dat is de weg terug naar binnen als het wachtwoord kwijt is.

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

De sessiecookie `mcs_session` is `base64(payload).hmac`, ondertekend met de
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
**kortlevende inlognonce** in de cookie `mcs_login` (`LOGIN_TTL` = 30 minuten),
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

- Het token is `mcs_` + `secrets.token_urlsafe(32)`.
- Alleen zijn SHA-256 wordt bewaard. **De site kan hem niet opnieuw tonen.**
- Hij wordt één keer aan de browser gegeven via een `HttpOnly`-cookie van 60
  seconden (`mcs_new_token`) en niet via de URL, zodat hij niet in een proxylogboek
  of een browsergeschiedenis belandt.
- Intrekken zet een vlag in plaats van de rij te verwijderen, zodat `last_used`
  blijft bestaan.
- `last_used` wordt bij elke geslaagde controle geschreven, en zo wordt een token
  dat niemand meer gebruikt zichtbaar.

Kale SHA-256 in plaats van PBKDF2 is met opzet: een willekeurig token van 32 byte
heeft geen raadbare structuur, dus de trage hash die een door mensen gekozen
wachtwoord beschermt levert niets op en zou 200 000 rondes kosten bij elk
ingestverzoek.

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
| `GET /admin/server` | **Server en site** — alles wat deze installatie configureert en geen apparaat raakt |

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

Twee blokken zijn bewust leeg gelaten in plaats van gevuld met een belofte: de
firmware-upgradeweg, en de fijnmazige zichtbaarheidskeuze (positie tonen, naam
tonen). Op beide plekken staat in commentaar wat er hoort te komen — inclusief de
zes endpoints in `routes_api.py` waar de positie van een gevolgde repeater naar
buiten komt, want een schakelaar die belooft een positie te verbergen terwijl de
heatmap hem nog uitlevert, is erger dan geen schakelaar.

## Server en site — `GET /admin/server`

| Anker | Blok | Inhoud |
|---|---|---|
| `#toegang` | Toegang | Als wie je bent ingelogd, en het wachtwoord wijzigen |
| `#tokens` | API-tokens | Actieve tokens met `created_at` en `last_used`; aanmaken en intrekken |
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

`is_public` bepaalt alles: de startpagina, `/r/<slug>`, en elke publieke
API-route. `_public_repeater()` in `routes_api.py` antwoordt 404 voor een
niet-publieke slug, zodat een repeater die uitgezet is onzichtbaar is en niet
alleen niet-gelinkt.

Een repeater verwijderen wist zijn rijen in `samples`, `latest` en `neighbors`
expliciet, en daarna de rij zelf. Zijn pakketten blijven staan:
`packets.observer` is een sleutelprefix en geen foreign key, en een ontvangst is
een feit over het mesh en niet over de rij die net verwijderd is.

## Wat het beheerdeel *niet* doet

- **Geen gebruikersbeheer.** Een of enkele accounts, aangemaakt vanaf de
  opdrachtregel.
- **Geen auditlogboek.** Handelingen belanden in het gewone applicatielogboek.
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
