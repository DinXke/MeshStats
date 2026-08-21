# Home Assistant via MQTT-discovery

*[English](../ha-integratie.md)*

MeshManager kan zijn telemetrie **vanzelf** in Home Assistant laten verschijnen,
als gewone HA-entiteiten, zonder custom component en zonder ook maar één regel
YAML. Hij publiceert MQTT-*discovery*-berichten naar dezelfde broker waar Home
Assistant aan hangt — precies zoals Uptime Kuma daar zijn entiteiten al
publiceert.

Dit is de omgekeerde richting van [`homeassistant.md`](homeassistant.md): die
integratie leest gegevens *uit* Home Assistant en duwt ze *naar* MeshManager.
Deze duwt *vanuit* MeshManager *naar* Home Assistant.

## Wat het doet

Een node met MeshManager-firmware publiceert zijn statistieken al naar de eigen
MQTT-broker van MeshManager (`MM_MQTT_*`). Home Assistant hangt bij ons aan een
*andere* broker — een EMQX op het LAN. Deze functie opent een **tweede, aparte
MQTT-verbinding** naar die HA-broker en publiceert daar discovery + state. De
twee brokers lopen nooit door elkaar: eigen client, eigen inloggegevens, eigen
herverbindlus, in een achtergronddraad die de app niet ophoudt of laat vallen
als de HA-broker weg is.

Elke node wordt één HA-**device**. Daaronder hangen de entiteiten:
batterijspanning, netvoeding/batterij/wifi-toestand, per ping-monitor een
connectiviteitssignaal met zijn responstijd, een node-online-sensor en een
"actieve storing"-sensor. Alle object-id's en unique-id's dragen het voorvoegsel
`meshmanager_`, zodat er niets botst met je bestaande MeshCore-scripts of de
Uptime-Kuma-entiteiten.

## Aanzetten

Het staat **uit** tot zowel de brokerhost als de schakelaar gezet zijn —
dezelfde afspraak als de VAPID-sleutels voor webpush. Zet deze
omgevingsvariabelen (zie `.env.example`; in Docker staan ze al in
`docker-compose.yml` met de gebruikelijke `MM_`/`MCS_`-terugval):

```
MM_HA_MQTT_HOST=10.10.10.100
MM_HA_MQTT_PORT=1883
MM_HA_MQTT_USER=meshmanager
MM_HA_MQTT_PASS=jouw-wachtwoord
MM_HA_DISCOVERY_ENABLED=1
```

Optioneel: `MM_HA_DISCOVERY_PREFIX` (standaard `homeassistant`),
`MM_HA_STATE_PREFIX` (standaard `meshmanager/ha`), `MM_HA_SCOPE` (standaard
`monitored`) en `MM_HA_STALE_MIN` (standaard `20`). De huidige toestand, de
reden als het uit staat en het aantal gepubliceerde entiteiten staan op de
serverpagina onder **Home Assistant (MQTT-discovery)**, en de reden wordt naar
het opstartlog geschreven.

State wordt gepubliceerd op het moment dat een meting binnenkomt — de module
haakt in het ingest-pad, hij pollt niet — dus een entiteit in HA werkt net zo
snel bij als de node meldt. Een trage achtergrondronde (elke 60 s) herijkt de
beschikbaarheid en zet de storingsstatus ook als er geen nieuwe meting was.

## Een EMQX-gebruiker aanmaken

MeshManager heeft een eigen account op de EMQX-broker nodig. In het
EMQX-dashboard:

1. Ga naar **Access Control → Authentication** en voeg een gebruiker toe, bv.
   `meshmanager` met een lang wachtwoord. Zet dat wachtwoord in
   `MM_HA_MQTT_PASS`.
2. Ga naar **Access Control → Authorization** en voeg ACL-regels toe die deze
   gebruiker alleen onder zijn eigen topics laten publiceren (zie de ACL-sectie
   hieronder).
3. Herstart MeshManager (of de container). De entiteiten verschijnen binnen een
   minuut in Home Assistant, net zoals de Uptime-Kuma-entiteiten dat deden.

## De entiteiten

Per node (device), afhankelijk van wat de node meldt:

| Bron | HA-entiteit | Type |
|---|---|---|
| Batterijspanning (`ch1_voltage`, `bat`) | sensor, `voltage`, V | `sensor` |
| Netvoeding aanwezig | binair, `power` | `binary_sensor` |
| Op batterij | binair (aan = op batterij) | `binary_sensor` |
| WiFi | binair, `connectivity` | `binary_sensor` |
| Ping-monitor op/neer | binair, `connectivity`, **met de kanaalnaam** | `binary_sensor` |
| Ping-monitor responstijd | sensor (ms) | `sensor` |
| Node online / heartbeat | binair, `connectivity` | `binary_sensor` |
| Actieve storing | binair, `problem` | `binary_sensor` |
| Repeatertelemetrie (airtime, ruisvloer, …) | sensor | `sensor` |

De ping-monitor-entiteit neemt zijn naam over uit de kanaalnaam die je in
MeshManager gezet hebt (`channel_names`) — dat is het hele punt: "google" leest
in HA beter dan "kanaal 6". Het device_class van een schakelkanaal wordt uit die
naam afgeleid (wifi → connectivity, netvoeding → power, batterij → geen, de rest
→ connectivity), wat het meest voorkomende geval (een ping-monitor) juist houdt.

Voor de "actieve storing" kozen we per node één `binary_sensor` met device_class
`problem`, gevoed door het aantal openstaande alarmen, en niet een tekstsensor
met de laatste alerttekst. Hij doet één ding robuust — is er iets mis, ja/nee —
en dat is precies wat een HA-automatisering nodig heeft om te notificeren. De
alerttekst zelf staat al op de alertenlijst van de site; die hier spiegelen zou
een tweede bron zijn die kan gaan afwijken.

## Beschikbaarheid en opruimen

Elke entiteit hangt aan twee availability-topics met `availability_mode: all`:
een brug-topic (met een MQTT-last-will, zodat *alles* niet-beschikbaar wordt als
MeshManager wegvalt) en een node-topic (dat op `offline` gaat als díe node
langer dan `MM_HA_STALE_MIN` minuten stil was). Zo toont HA een node grijs zodra
hij stil valt, zonder de andere mee te trekken.

Discovery-config-topics zijn *retained*, dus een entiteit die weg moet, wordt
actief opgeruimd: MeshManager onthoudt per node welke entiteiten hij publiceerde
(in de `settings`-tabel, dus over een herstart heen) en leegt het config-topic
(een retained leeg bericht) van alles wat niet meer bijhoort. Een ping-monitor
die van een node verdwijnt — en wiens `latest`-rij uiteindelijk door de bewaring
gesnoeid wordt — laat zo geen spookentiteit achter. Een node die uit scope valt
wordt op dezelfde manier geleegd.

## Scope

`MM_HA_SCOPE` bepaalt welke nodes gepubliceerd worden:

- `sensors` — alleen de sensornodes (die met een `sensor_host` / eigen API).
- `monitored` — de sensornodes plus repeaters die werkelijk telemetrie melden
  (batterij, airtime, ruisvloer). **Dit is de standaard.** Een repeater die
  alleen ooit als buur *gehoord* is, heeft die niet en blijft eruit, zodat HA
  niet volloopt met tientallen betekenisloze entiteiten.
- `all` — elke tracked repeater in de databank.

## Veiligheid (ACL)

Dit publiceert naar een broker op je LAN. Geef de MeshManager-EMQX-gebruiker een
ACL die hem **alleen** onder zijn eigen topics laat publiceren, niets anders:

```
# toegestaan om te publiceren
homeassistant/#            (discovery-config — HA vereist dit voorvoegsel)
meshmanager/ha/#           (state + availability)
```

Weiger de rest voor deze gebruiker. Zo kan een gelekte MeshManager-inlog geen
willekeurige discovery-berichten publiceren die elders in Home Assistant valse
entiteiten zouden aanmaken.

## Problemen oplossen

- **De serverpagina zegt "uit".** De reden staat op de regel zelf: host niet
  gezet, of de schakelaar niet gezet. Beide zijn vereist.
- **"aan, maar niet verbonden".** Verkeerde host/poort, of de broker weigert de
  inlog — de laatste fout staat erbij. Controleer `MM_HA_MQTT_USER` /
  `MM_HA_MQTT_PASS` en de EMQX-authenticatie.
- **Entiteiten verschijnen maar blijven "niet beschikbaar".** Controleer of de
  MQTT-integratie van HA op *dezelfde* EMQX-broker wijst, en of de ACL
  `meshmanager/ha/#` toestaat — daar wonen de availability-topics.
- **Een oude entiteit gaat niet weg.** Dat is een retained config-topic.
  MeshManager leegt de topics die hij kent; een handmatig aangemaakte leeg je
  door een leeg retained bericht op zijn `.../config`-topic te publiceren.
