# De Home Assistant-integratie

*[English](../homeassistant.md)*

`homeassistant/custom_components/mc_repeater_stats/` — een custom integratie die
MeshCore-repeaterdata uit de state machine van Home Assistant leest en over HTTP
naar een MeshStats-site pusht.

**Hij is optioneel, en niet langer de aanbevolen weg.** Een node met de
MeshStats-firmware publiceert zelf naar MQTT, zonder Home Assistant ertussen.
Dit document bestaat omdat de integratie nog twee dingen doet die de MQTT-weg
niet doet, en omdat er heel wat installaties op gebouwd zijn van vóór MQTT
bestond.

---

## Inhoud

- [Is dit iets voor jou?](#is-dit-iets-voor-jou)
- [Wat hij nodig heeft](#wat-hij-nodig-heeft)
- [Installatie](#installatie)
- [Configuratiestroom](#configuratiestroom)
- [Wat hij ontdekt](#wat-hij-ontdekt)
- [Wat hij pusht](#wat-hij-pusht)
- [Wat hij ophaalt](#wat-hij-ophaalt)
- [Tijdsinstellingen](#tijdsinstellingen)
- [Prefixen matchen](#prefixen-matchen)
- [Services](#services)
- [Grenzen en bekend gedrag](#grenzen-en-bekend-gedrag)
- [Problemen oplossen](#problemen-oplossen)
- [Bestandsoverzicht](#bestandsoverzicht)

---

## Is dit iets voor jou?

| Situatie | Gebruik |
|---|---|
| Je kunt de MeshStats-firmware op een companion-node flashen | **MQTT.** [`mqtt.md`](mqtt.md) — geen Home Assistant nodig |
| Je kunt geen firmware flashen, maar draait al Home Assistant met de `meshcore`-integratie | **Deze integratie** |
| Je wilt CLI-instellingen van repeaters over LoRa ophalen zonder monitornode | **Deze integratie** — het is de enige weg die vanuit HA op een repeater inlogt |
| Je wilt allebei | Prima. Beide schrijven via dezelfde ingest-afhandeling; de nieuwste waarde wint |

De eerlijke samenvatting: als de node het zelf kan publiceren, laat hem. Deze
integratie voegt een machine toe die moet blijven draaien, een token dat geldig
moet blijven, en een verzameling reguliere expressies die moeten blijven passen
op entity-ID's die de `meshcore`-integratie kiest.

Wat hij je nog steeds oplevert:

1. **Contactlocaties.** Hij leest geadverteerde posities uit de contactsensoren
   van `meshcore` en post ze naar `/api/v1/contacts`, en dat is wat nodes op de
   kaart zet. Een node die over MQTT publiceert meldt wat hij over zichzelf meet,
   niet de advertdatabase die zijn app bijhoudt.
2. **CLI-instellingen ophalen over LoRa.** Hij kan met het beheerderswachtwoord
   op een repeater inloggen en instellingen terug lezen, één `get` tegelijk. Dat
   is hetzelfde werk als een [monitor](glossary.md#monitor)-node doet, maar dan
   vanuit HA.

---

## Wat hij nodig heeft

- Home Assistant met een werkende **`meshcore`-integratie** die
  `sensor.meshcore_*`- / `binary_sensor.meshcore_*`-entiteiten produceert. Deze
  integratie leest die entiteiten en roept `meshcore.execute_command` aan; zonder
  hem valt er niets te lezen en niets te commanderen.
- Een MeshStats-site die vanaf de HA-machine over HTTP(S) bereikbaar is.
- Een **API-token** aangemaakt in `/admin`.
- Optioneel het **beheerderswachtwoord van elke repeater** waarvan je
  instellingen wilt ophalen.

Geen Python-afhankelijkheden: `manifest.json` heeft een lege `requirements`-lijst
en lege `dependencies`.

---

## Installatie

```
config/custom_components/mc_repeater_stats/     <- kopieer de map hierheen
```

Kopieer `homeassistant/custom_components/mc_repeater_stats/` uit deze repo naar
de `config/custom_components/` van je Home Assistant, herstart Home Assistant, en
ga dan naar **Instellingen → Apparaten en diensten → Integratie toevoegen → MC
Repeater Stats**.

Er is geen HACS-repository en geen release-artefact; de map is het product.

---

## Configuratiestroom

`config_flow.py`. Twee stappen bij de eerste opzet, drie als je later opties
wijzigt.

### Stap 1 — `user`: site en token

| Veld | Betekenis |
|---|---|
| `base_url` | De basis-URL van de site, zonder afsluitende schuine streep |
| `token` | Een API-token uit `/admin` |

De URL wordt gevalideerd voordat de stroom verdergaat: `validate_connection()`
doet een `GET /api/v1/ping` met het bearer-token en eist HTTP 200. Een mislukking
toont `cannot_connect`, een antwoord dat geen 200 is toont `invalid_auth`. De
basis-URL is tegelijk de unieke ID van de config entry, dus dezelfde site kan
niet twee keer toegevoegd worden.

### Stap 2 — `repeaters`: welke nodes gesynchroniseerd worden

Een multiselect opgebouwd uit `discover_repeaters()`, plus één vinkje:

| Veld | Standaard | Betekenis |
|---|---|---|
| `repeaters` | alle gevonden | Welke prefixen gepusht worden |
| `auto_add` | aan | Nieuw ontdekte repeaters automatisch overnemen |

Met `auto_add` aan vergelijkt `_interval_push()` elke vijf minuten
`discover_repeater_prefixes()` met de huidige selectie, en schrijft bij iets
nieuws een bijgewerkte optielijst weg. Die update triggert een herlaad van de
config entry, en de vers gebouwde pusher pusht meteen alles.

### Stap 3 — `passwords`: beheerderswachtwoorden (alleen in de optiestroom)

Eén optioneel tekstveld per geselecteerde repeater. Dit zijn de wachtwoorden voor
`send_login` bij het ophalen van CLI-instellingen. **Een leeggelaten veld behoudt
het opgeslagen wachtwoord** in plaats van het te wissen, zodat het opnieuw openen
van de optiestroom niet weggooit wat je de vorige keer invoerde.

Zonder wachtwoord logt `_fetch_settings_inner()` een waarschuwing en gaat toch
verder — een paar `get`-commando's antwoorden zonder inlog, de meeste niet.

---

## Wat hij ontdekt

Alles hangt aan de vorm van de entity-ID's die de `meshcore`-integratie
produceert. Ze worden gematcht door de reguliere expressies in `const.py`:

| Constante | Patroon | Matcht |
|---|---|---|
| `RE_ENTITY` | `^(?:sensor\|binary_sensor)\.meshcore_([0-9a-f]{6,12})_(.+)$` | Elke MeshCore-entiteit: prefix plus rest |
| `RE_NAME` | `MeshCore Repeater: (.+?) \([0-9a-f]+\)` | De weergavenaam van een repeater, uit `friendly_name` |
| `RE_NEIGHBOR` | `^neighbor_([0-9a-f]{6})$` | De SNR-sensor van een buur |
| `RE_NEIGHBOR_SEEN` | `^neighbor_([0-9a-f]{6})_seen$` | Minuten sinds een buur voor het laatst gehoord werd |
| `RE_NEIGHBOR_NAME` | `Neighbor (.+?) SNR$` | De naam van de buur, uit `friendly_name` |
| `RE_CONTACT` | `^binary_sensor\.meshcore_.+_([0-9a-f]{12})_contact$` | Een contact, voor zijn geadverteerde positie |

Twee ontdekfuncties met een bewust verschil:

| Functie | Geeft terug | Gebruikt voor |
|---|---|---|
| `discover_repeaters()` | Elke MeshCore-prefix → weergavenaam | De keuzelijst: toont alles, jij kiest |
| `discover_repeater_prefixes()` | Alleen prefixen waarvan `friendly_name` op `MeshCore Repeater:` past | `auto_add`: neemt **alleen** echte repeaters over |

Dat onderscheid doet ertoe. Alles automatisch overnemen wat er als een
MeshCore-entiteit uitziet, zou companions en clients een
repeaterstatistiekensite in duwen.

### Metricnamen

`extract_metric()` beeldt de rest van een entity-ID af op een bekende
metricnaam. `KNOWN_METRICS` is bewust **langste eerst** gesorteerd:

```python
KNOWN_METRICS = sorted([...], key=len, reverse=True)
```

Entity-ID's eindigen op een geslugde nodenaam (`bat_be_hss_jessazh_vir`). Kortste
eerst matchen zou `battery_percentage` als `bat` met een suffix lezen. Langste
eerst maakt de gulzige match de juiste.

Booleans krijgen hun eigen regel: een `binary_sensor`-waarde is alleen waar bij
`on` of `fresh`; al het andere telt als offline. Toestanden `unknown`,
`unavailable` of leeg worden helemaal overgeslagen in plaats van als nul gepusht.

---

## Wat hij pusht

### Snapshots — `POST /api/v1/ingest`

`_snapshot()` loopt alle MeshCore-entiteiten voor één prefix af en bouwt:

```json
{
  "repeater": {"pubkey_prefix": "<prefix>", "name": "<uit friendly_name>"},
  "metrics":  {"bat": 4.15, "online": true, "...": "..."},
  "neighbors": [{"prefix": "2ae7af", "name": "...", "snr": -4.25, "seen_min": 3.0}]
}
```

Geeft `None` terug — en pusht dus niets — als een repeater helemaal geen
bruikbare metrics heeft. Een snapshot van niets is erger dan geen snapshot: het
schrijft een rij weg die op een meting lijkt.

Versturen wordt op drie manieren getriggerd:

| Trigger | Mechanisme |
|---|---|
| Een statuswijziging bij een gesynchroniseerde repeater | `EVENT_STATE_CHANGED`-luisteraar, met debounce |
| Elke 5 minuten | `async_track_time_interval` → `_interval_push()` → `push_all()` |
| De site vroeg om een verversing | `_poll_commands()` → `_request_status()` → geforceerde push |

De debounce is per prefix en samenvouwend in plaats van herstartend: zodra er
voor een prefix een push gepland staat, worden verdere statuswijzigingen
genegeerd tot hij afgaat (`if prefix in self._debounce: return`). Een repeater
waarvan alle sensoren binnen een seconde bijwerken levert één push op, geen
dozijn.

Een geforceerde push draagt `"force": true` mee, wat de server vertelt een
datapunt te schrijven ook als er niets veranderde — precies waarom er gevraagd
werd.

### Contacten — `POST /api/v1/contacts`

`collect_contacts()` leest `adv_lat`/`adv_lon` (met terugval op
`latitude`/`longitude`) van elke contact-binary-sensor en post prefix, naam,
positie en nodetype. Contacten zonder bruikbare positie worden overgeslagen, niet
met nulls gepost.

Wordt bij het starten gepusht en bij elk vijfminuteninterval.

---

## Wat hij ophaalt

Elke 30 seconden doet `_poll_commands()` een `GET /api/v1/commands` en vindt
daar twee soorten verzoeken.

### `refresh` — een repeater om verse status vragen

`_request_status()` roept `meshcore.execute_command` twee keer aan, met
`send_statusreq <kort>` en `send_telemetry_req <kort>`, en plant daarna 35
seconden later een geforceerde push — genoeg tijd voor de LoRa-heen-en-terug om
in de state machine van HA te landen voordat de snapshot genomen wordt.

### `settings` — de CLI-instellingen van een repeater lezen

`_fetch_settings()` is geserialiseerd achter een `asyncio.Lock()`: **één
opvraging tegelijk over alle repeaters heen**, want ze delen allemaal één radio.

De volgorde in `_fetch_settings_inner()`:

1. `send_login <kort> <wachtwoord>`, daarna `SETTINGS_LOGIN_WAIT` seconden
   wachten.
2. Per parameter `send_cmd <kort> "get <param>"`. Een parameter geschreven als
   `cmd:<iets>` wordt letterlijk verstuurd, zonder `get ` ervoor.
3. Wacht tot `SETTINGS_RESPONSE_TIMEOUT` op het eerste antwoord, en blijf daarna
   verzamelen tot het `SETTINGS_QUIET_GAP` stil is — meerregelige antwoorden zoals
   de regiolijst komen als losse LoRa-pakketten binnen — met `SETTINGS_PARAM_CAP`
   als harde bovengrens per parameter.
4. Twee seconden tussen parameters, om LoRa ademruimte te geven.
5. **Eén herkansingsronde** voor elke parameter die `None` terugkwam.
6. `POST /api/v1/repeater_settings` met de hele resultaatmap, onbeantwoorde
   parameters inbegrepen als `null`.

Antwoorden komen over twee eventbussen tegelijk binnen en naar allebei wordt
geluisterd: `meshcore_cli_response`, en `meshcore_message` voor het geval waarin
de repeater als direct bericht antwoordt met `> ` ervoor. `_response_text()`
probeert de veldnamen `response`, `text`, `message`, `result`, `payload` op
volgorde en valt terug op een JSON-dump, afgekapt op 500 tekens — een tolerante
lezer, want de vorm van het event is niet de onze om te repareren.

Parameters worden defensief begrensd voor gebruik: 64 tekens elk, maximaal 40.

### Luid falen is bewust

Kan een verzoek niet uitgevoerd worden — geen passende repeater, lege
parameterlijst — dan logt de integratie een **waarschuwing die de reden en de
gesynchroniseerde prefixen noemt**:

```python
# Luid falen: de wachtrij op de site is clear-on-read, dus een
# verzoek dat we hier laten vallen bestaat nergens meer.
```

`GET /api/v1/commands` is clear-on-read. Een verzoek dat hier stil verdwijnt
bestaat nergens meer, en de beheerpagina zou "opvraging gestart" blijven melden
met niets erachter en niets dat uitlegt waarom. Zie
[`contributing.md`](contributing.md#1-eerlijkheid-over-onzekerheid).

---

## Tijdsinstellingen

Alle in `const.py`, alle in seconden.

| Constante | Waarde | Waar hij over gaat |
|---|---|---|
| `DEBOUNCE_SECONDS` | 10 | Wachttijd na een statuswijziging voordat die repeater gepusht wordt |
| `FULL_PUSH_INTERVAL` | 300 | Volledige snapshot van elke repeater, plus contacten |
| `COMMAND_POLL_INTERVAL` | 30 | Hoe vaak de commandowachtrij gepold wordt |
| `REFRESH_PUSH_DELAY` | 35 | Wachttijd na een statusverzoek vóór de geforceerde push |
| `SETTINGS_LOGIN_WAIT` | 12 | Wachttijd na `send_login` vóór de eerste `get` |
| `SETTINGS_RESPONSE_TIMEOUT` | 12 | Wachttijd op het eerste antwoord op een `get` |
| `SETTINGS_QUIET_GAP` | 5 | Stilte die een meerregelig antwoord afsluit |
| `SETTINGS_PARAM_CAP` | 45 | Harde bovengrens per parameter |
| `MIN_PREFIX_MATCH` | 8 | Kortste prefixvergelijking die nog vertrouwd wordt (hextekens) |

HTTP-timeouts: 30 s voor pushes, 15 s voor de commandopoll en voor `ping`.

---

## Prefixen matchen

`match_prefix()` in `pusher.py` — klein, en de bron van een hele klasse stille
mislukkingen als hij ontbreekt.

De site en Home Assistant spellen dezelfde sleutel niet even lang: de
`meshcore`-integratie levert hier vijf sleutelbytes, de eigen firmware van een
node zes, en de site bewaart de langste die ze ooit zag. Een gelijkheidstest zegt
dan "andere node" over twee schrijfwijzen van één node. En omdat de
commandowachtrij op de site clear-on-read is, verdwijnt zo'n verzoek daarmee
spoorloos.

De regel:

- Een exacte match wint.
- Anders mag de ene string een prefix van de andere zijn.
- **Nooit onder `MIN_PREFIX_MATCH` = 8 hextekens.** Twee werkelijk verschillende
  sleutels kunnen kort hetzelfde beginnen, en een opvraging naar de verkeerde
  node sturen is erger dan er geen sturen.
- Bij meerdere kandidaten wint de **langste** — die is het minst dubbelzinnig.

Dezelfde functie wordt op de wachtwoordmap losgelaten, om dezelfde reden: een
wachtwoord dat onder een anders gespelde prefix staat zou anders een inlog zonder
wachtwoord opleveren, en een repeater die nergens op antwoordt.

`MIN_PREFIX_MATCH` spiegelt de gelijknamige constante op de server. Wijzig je de
ene, wijzig dan de andere.

---

## Services

| Service | Effect |
|---|---|
| `mc_repeater_stats.push_now` | Meteen een volledige snapshot van elke gesynchroniseerde repeater pushen, voor elke ingestelde site |

Eén keer globaal geregistreerd, niet per config entry: hij loopt elke pusher in
`hass.data[DOMAIN]` af.

---

## Grenzen en bekend gedrag

- **De integratie leest toestand, hij pollt geen radio's.** Zijn data is alleen zo
  vers als wat de `meshcore`-integratie in de state machine gezet heeft. Een
  verversingsverzoek is de ene uitzondering, en dat werkt door `meshcore` te
  vragen iets uit te zenden.
- **Reguliere expressies zijn een koppeling.** Wijzigt de `meshcore`-integratie de
  vorm van zijn entity-ID's of `friendly_name`s, dan valt de ontdekking stil in
  plaats van te falen met een foutmelding. Symptoom: repeaters verschijnen niet
  meer in de keuzelijst.
- **Elke netwerkfout wordt geslikt en gelogd, nooit doorgegooid.** Een mislukte
  push mag de HA-eventloop niet omleggen, dus `push_repeater()`,
  `push_contacts()` en `_poll_commands()` vangen breed. Het volgende interval
  probeert het opnieuw.
- **Een instellingenopvraging bezet de radio minutenlang.** Bewust
  geserialiseerd, met bewuste tussenpozen. Veel parameters van meerdere repeaters
  ophalen is traag, en dat is de LoRa-duty-cycle die spreekt, niet de code.
- **Wachtwoorden staan in de config entry**, die in de `.storage` van Home
  Assistant leeft. Behandel die map ernaar; zie [`security.md`](security.md).

---

## Problemen oplossen

| Symptoom | Waarschijnlijke oorzaak |
|---|---|
| Geen repeaters in de keuzelijst | De `meshcore`-integratie ontbreekt, of zijn entiteitsnamen passen niet meer op `RE_ENTITY` / `RE_NAME` |
| Opzet faalt met `cannot_connect` | De HA-machine kan de site-URL niet bereiken |
| Opzet faalt met `invalid_auth` | Token fout, ingetrokken, of `/api/v1/ping` gaf geen 200 |
| "Opvraging gestart" op de site, er komt nooit iets aan | Kijk in de HA-log naar de waarschuwing die de reden noemt — meestal matchte de prefix niet, of er staat geen wachtwoord |
| Instellingen komen grotendeels als `null` terug | Geen repeaterwachtwoord ingesteld, of de LoRa-link verliest antwoorden. Eén herkansingsronde is al gedraaid |
| Site toont een repeater maar geen kaartpositie | Posities komen uit contacten, niet uit snapshots. Controleer of de contactsensoren `adv_lat` / `adv_lon` dragen |
| Waarden stoppen met bijwerken zonder foutmelding | Sensoren staan op `unknown` / `unavailable`; die toestanden worden bewust overgeslagen |

De integratie logt onder `custom_components.mc_repeater_stats`. Zet het hoger in
`configuration.yaml`:

```yaml
logger:
  logs:
    custom_components.mc_repeater_stats: debug
```

---

## Bestandsoverzicht

| Bestand | Inhoud |
|---|---|
| `__init__.py` | Opzetten en afbreken van de entry, de `push_now`-service, herladen bij optiewijziging |
| `config_flow.py` | De opzet- en optiestromen |
| `const.py` | Domein, optiesleutels, tijdsconstanten, entiteitsregexen, `KNOWN_METRICS` |
| `pusher.py` | Ontdekking, snapshots bouwen, pushen, commando's pollen, instellingen ophalen |
| `manifest.json` | Metadata van de integratie; `iot_class: cloud_push`, geen requirements |
| `services.yaml` | De beschrijving van de `push_now`-service |
| `strings.json`, `translations/{en,nl}.json` | UI-tekst |

---

## Zie ook

| | |
|---|---|
| De aanbevolen weg in plaats van deze | [`mqtt.md`](mqtt.md) |
| Waar dit in het geheel past | [`architecture.md`](architecture.md) |
| Hem naast de site installeren | [`deployment.md`](deployment.md#home-assistant-onderdelen) |
| De TCP-proxy, een ander HA-onderdeel | [`proxy.md`](proxy.md) |
| Omgaan met tokens, en wat een wachtwoord een aanvaller oplevert | [`security.md`](security.md) |
