# Een node iets laten doen

*[English](../commanding.md)*

`server/app/commanding.py` beantwoordt één vraag: **wat kan er met deze repeater,
nu meteen, en wat mag de pagina beloven?**

[`mqtt.md`](../mqtt.md#asking-a-node-for-something) beschrijft de woorden op de
draad en wat de firmware ermee doet. Dit document gaat over hoe de server
bepaalt welke weg er is en wat de knop zegt.

## Waarom de module bestaat

Het antwoord op "kan dit überhaupt?" komt uit vijf losse plaatsen:

1. de repeaterrij,
2. wie ervoor publiceert (`source_prefix`),
3. de firmwareversie van **die** node,
4. de brokerverbinding,
5. wanneer een poller voor het laatst iets ophaalde.

Zonder één plek die dat samenbrengt, belooft de knop op de beheerpagina wat
niemand kan waarmaken — en dat is precies wat er gebeurde toen Home Assistant uit
de keten verdween. De pagina bleef melden *"Opvraging gestart — Home Assistant
logt in op de repeater"* terwijl het verzoek in een wachtrij lag die niemand nog
leegde.

De functies hier **raken niets aan**. Ze beschrijven alleen wat mogelijk is,
zodat de route bepaald wordt *vóór* de knop getekend wordt en niet pas nadat erop
geklikt is.

## De drie wegen

### Rechtstreeks over MQTT

De site publiceert één woord op `<voorvoegsel>/<node>/cmd` en de node leest daarop
zijn eigen CLI uit of stuurt meteen een statusbericht. Dat werkt alleen als de
node zelf publiceert, als zijn firmware dat topic kent (**nodefirmware 1.8.0** en
hoger, `MIN_CMD_VERSION`) en als de broker op dit ogenblik verbonden is.

### Over MQTT naar de node die hem monitort

Een repeater die zelf niet publiceert, maar wiens cijfers doorgestuurd worden
door een node die hem uitleest, is niet onbereikbaar — hij is alleen niet
*rechtstreeks* bereikbaar. De monitor logt al bij hem in en pollt hem al; sinds
**nodefirmware 1.9.0** (`MIN_MON_CMD_VERSION`) kan die monitor op verzoek ook zijn
CLI-instellingen over LoRa ophalen en publiceren. De opdracht gaat dan naar de
monitor (`settings <sleutel>`) en niet naar het onderwerp.

Dat is precies het geval waarvoor dit project bestaat: de dakrepeater die dit
alles moet meten publiceert zelf niets. Tot 1.9.0 zei de knop over hem
*"doorgestuurd, alleen de node zelf kan zijn eigen CLI uitlezen"* — waar en
onbruikbaar tegelijk.

### Via een poller

De Home Assistant-integratie haalt `GET /api/v1/commands` op, vraagt de repeater
over LoRa uit en POST het antwoord terug. Die weg blijft bestaan, maar is nu de
laatste keuze in plaats van de enige.

## `route_for()` — wat er terugkomt

```python
commanding.describe(rep)   # route_for met brokerstatus en doorstuurder erbij
```

`describe()` haalt de brokerstatus, het pollertijdstip en de rij van de
doorsturende repeater er zelf bij; `route_for()` krijgt ze als argument, zodat
die te testen blijft zonder een MQTT-client of een databank in de buurt.

| Sleutel | Betekenis |
|---|---|
| `mqtt` | Er kan nu iets vertrekken over MQTT |
| `commands` | **Welke** opdrachten die weg aankan |
| `via_monitor` | De opdracht gaat naar een andere node dan het onderwerp |
| `blocker` | Waarom de MQTT-weg dicht is; leeg betekent open |
| `node` | De node die de opdracht krijgt |
| `subject` | De sleutel die in die opdracht meegaat, of `None` |
| `fw_meshmanager` | Firmware van de node die de opdracht krijgt |
| `min_fw` | De versie die deze weg vereist |
| `node_seen`, `node_stale` | Wanneer die node het laatst publiceerde, en of dat te lang geleden is |
| `ha`, `poller_seen` | Of er binnen `POLLER_STALE_SECS` een poller gezien is |

**`commands` is geen formaliteit.** Een monitor kan gevraagd worden de
instellingen van een ander op te halen, maar niet om diens statistieken te
publiceren — die stuurt hij al door op rondes die hij zelf plant. Een knop die
`status` aanbiedt langs een weg die dat niet kent, is precies de soort belofte die
deze module moest wegwerken:

```python
"commands": ("settings",) if via_monitor else ("settings", "status")
```

### De blockers, in de volgorde waarin ze getest worden

| `blocker` | Betekenis |
|---|---|
| `no_source` | Er heeft nog nooit iets voor deze repeater gepubliceerd |
| `http_source` | Hij komt binnen via de HTTP-API (`source_prefix == "api"`), niet over MQTT |
| `relay_unknown` | Hij wordt doorgestuurd, maar de doorstuurder is hier zelf geen bekende repeater — dus van zijn firmware weten we niets, en gokken kost een opdracht die aan de overkant stilletjes geweigerd wordt |
| `no_fw` | Geen moduleversie bekend |
| `old_fw` | Versie lager dan `min_fw` |
| `broker_down` | Niet verbonden met de broker |

`broker_down` wordt **als laatste** getest, met opzet, zodat een tijdelijk
wegvallende broker de blijvende reden niet overschaduwt: "firmware te oud" lost
zichzelf niet op.

### Welke firmware telt

Bij een doorgestuurde repeater die van de **doorsturende** node. Die node krijgt
de opdracht en moet ze kennen. De versie van het onderwerp zegt hier niets — vaak
staat er niet eens een, want een node die zelf niet publiceert meldt zijn
moduleversie nergens.

## `is_relayed()` en `same_key()`

```python
def is_relayed(rep) -> bool:
    source = (_field(rep, "source_prefix") or "").lower().strip()
    if not source or source == "api":
        return False
    return not same_key(source, _field(rep, "pubkey_prefix"))
```

`same_key()` herhaalt de regel van `db._find_by_prefix()`: bronnen sturen
verschillende lengtes — Home Assistant vijf bytes, de eigen firmware zes — dus de
kortste sleutel moet een prefix zijn van de langste, en minstens
`MIN_PREFIX_MATCH` (8) hextekens tellen. Zonder die regel zou de pagina een node
die zichzelf publiceert aanzien voor een die doorgestuurd wordt.

De constante staat hier herhaald in plaats van uit `db` geïmporteerd, zodat dit
bestand zonder databank te testen is.

`parse_version()` vergelijkt op getallen en niet op de string: `"1.10.0"` komt
alfabetisch vóór `"1.8.0"`, en dat is net de firmware die het wél kan.

## `_dispatch()` — elke openstaande weg, niet de eerste de beste

`routes_admin._dispatch(rep, command)` is wat de twee knoppen aanroepen.

**Beide wegen worden bewandeld en niet de eerste de beste**, want ze zijn niet
uitwisselbaar. De MQTT-weg bereikt de node zelf en alleen zolang die aan de
broker hangt; de wachtrij bereikt een poller die de repeater over LoRa uitvraagt
en werkt ook als de node zijn WiFi uit heeft staan. Wie er allebei heeft, heeft
er allebei iets aan; wie er geen heeft, hoort dat te **zien** in plaats van
"gestart" te lezen.

```
mqtt    alleen de rechtstreekse publicatie vertrok
queued  alleen de pollerwachtrij is gevuld
both    allebei
none    er is niets gebeurd
```

De formulering van de pagina hangt aan die terugwaarde en niet aan wat we
hoopten dat er zou gebeuren. Ze reist mee in de querystring van de omleiding, en
de oude vorm `?refresh=1` wordt nog steeds als `both` gelezen zodat een pagina die
nog in een tabblad openstaat er niet op stukvalt.

Bij `settings` wordt de wachtrij **alleen gevuld als er werkelijk een poller
gezien is**. Toch in de wachtrij zetten zou een verzoek achterlaten dat een net
geïnstalleerde Home Assistant maanden later oppikt, en zou
`pending_settings_request()` laten ophouden te betekenen wat het zegt.

## `publish_command()` — het enige dat de site publiceert

`mqtt_ingest.publish_command(node, command, subject=None, epoch=None)` geeft
terug of het bericht **vertrokken** is, nooit of het aangekomen is.

Het topic is `MM_MQTT_CMD_TOPIC`, standaard `{prefix}/{node}/cmd`.
`command_prefix()` vult `{prefix}` in met het voorvoegsel waarop deze ene node
zich het laatst meldde — onthouden bij binnenkomst in `repeaters.topic_prefix` en
niet gekozen bij vertrek, want tijdens de hernoeming luistert een node die nog
niet geflasht is op het oude, en geen enkele instelling kan zeggen welke van de
twee. Een node die we nog nooit hoorden, krijgt de opdracht op **elk**
voorvoegsel (`command_topics()`): twee berichtjes van acht bytes zijn goedkoper
dan een knop die niets doet. Een patroon dat zonder `{prefix}` ingesteld is,
wordt gerespecteerd zoals het er staat — wie een vast topic opgeeft, bedoelt
dat.

```python
COMMANDS = ("settings", "status", "time")
COMMANDS_WITH_SUBJECT = ("settings",)
COMMANDS_WITH_EPOCH = ("time",)
```

Dat de firmware alleen die drie woorden aanneemt is geen detail: dit topic is
bereikbaar voor iedereen met brokerreferenties, en de repeaters die dit bedient
hangen op daken. De lijst staat aan deze kant herhaald zodat een typfout geweigerd
wordt voordat hij een rondgang kost, en zodat ze leesbaar is naast de code die
hem verstuurt.

De argumenten verbreden dat niet:

- **`subject`** is nooit tekst die een CLI bereikt. Het selecteert één vermelding
  uit een monitorlijst die alleen de beheerder van de node kan schrijven, het
  wordt tot hex gestript, en het moet minstens `MIN_SUBJECT_HEX` (8) tekens
  tellen. Korter en er wordt helemaal niets gepubliceerd — met False terug, zodat
  de pagina "niets verstuurd" meldt in plaats van een opdracht te sturen die aan
  de overkant geweigerd wordt zonder dat hier iets van te zien is.
- **`epoch`** is een getal, aan beide kanten begrensd — hier en nog eens op de
  node — en het kan een klok alleen vooruit zetten. Zie
  [`clocksync.md`](clocksync.md#het-bericht-op-de-draad).

Ze staan in **aparte lijsten** in plaats van in één parameter, want het zijn
verschillende soorten argument met verschillende controles — een sleutel is hex
en *selecteert* iets, een epoch is een getal en *verandert* iets. Ze door één
parameter laten lopen zou betekenen dat één vergissing in de aanroep een sleutel
als tijd laat vertrekken.

Een verkeerd commandowoord, een onderwerp bij een commando dat er geen neemt, of
een epoch bij een commando dat er geen neemt, **werpen alle drie op**: dat zijn
programmeerfouten en die horen stuk te gaan bij het schrijven. Een epoch buiten
het venster en een te kort onderwerp **geven False terug**: dat zijn toestanden
van de wereld, en de beller moet ze kunnen melden.

### QoS 0 en `retain=False`

Allebei met opzet.

**QoS 0** omdat er niets te winnen valt bij het alternatief. De client verbindt
met een schone sessie, dus de broker bewaart niets voor een node die offline is;
een hogere QoS zou alleen de aflevering *aan de broker* bevestigen, en dat is niet
de vraag die iemand stelt. Een node die op zijn zonnebudget slaapt, mist het
bericht gewoon, en de pagina hoort dat te zeggen in plaats van te doen alsof de
opdracht onderweg is.

**`retain=False`** omdat een bewaarde opdracht bij elke herverbinding opnieuw
afgeleverd wordt. De node zou zijn CLI bij elke boot en na elke WiFi-onderbreking
uitlezen, zolang het bericht op de broker stond, en niemand zou dat in verband
brengen met een knop die weken eerder één keer ingedrukt werd.

### Eén verbinding, beide richtingen

`publish_command()` gebruikt de client die de abonneethread al bezit. Een tweede
client om te publiceren zou eigen referenties, een eigen herverbindingslus en een
eigen client-id nodig hebben — en `publish()` van paho is thread-veilig, dus de
requesthandlers kunnen deze vanuit hun eigen threads gebruiken. `can_publish()`
is de controle of een nu verstuurde opdracht deze machine werkelijk zou verlaten:
een host ingesteld, een client gebouwd, en de verbinding staat.

## De pollerwachtrij, en drie soorten stilte uit elkaar houden

De wachtrij staat in `settings` en wordt **bij het lezen gewist**, en daarom
bestaan er drie stukjes boekhouding omheen. Zonder die zou de beheerpagina in
drie verschillende situaties hetzelfde moeten beloven:

| Vraag | Beantwoord door |
|---|---|
| Staat het verzoek er nog, dus heeft er niets gepolld sinds de klik? | `db.pending_settings_request(prefix)` |
| Heeft een poller het meegenomen, en is er sindsdien iets teruggekomen? | `db.settings_delivered_at(prefix)` tegenover de nieuwste `repeater_cli.updated` |
| Is er überhaupt iemand om het op te halen? | `db.poller_last_seen()`, geschreven bij **elke** poll, de lege inbegrepen |

`settings_delivered` is begrensd op 200 sleutels, waarbij de nieuwste blijven,
want anders dan de verzoekwachtrij wordt die niet bij het lezen gewist.

De instellingenpagina van een repeater maakt van die drie `queued_since`,
`delivered_since` en `delivery_unanswered`, zodat een opvraging die opgehaald
werd en nooit beantwoord, zichtbaar is in plaats van er precies uit te zien als
een die nooit opgehaald is.

## Versiegrenzen in één tabel

| Weg | Commando | Minimum | Constante |
|---|---|---|---|
| Rechtstreeks | `settings`, `status` | nodefirmware 1.8.0 | `commanding.MIN_CMD_VERSION` |
| Via een monitor | `settings <sleutel>` | nodefirmware 1.9.0 | `commanding.MIN_MON_CMD_VERSION` |
| Beide | `time <epoch>` | nodefirmware 1.10.0 | `clocksync.MIN_TIME_VERSION` |

"Ouder" betekent niet "misschien". Een node onder 1.8.0 schrijft zich helemaal
niet in op het topic, dus de broker gooit het bericht weg zonder dat iemand het
merkt; een 1.8.0-node kent het topic wél maar weigert het argument en telt de
opdracht als geweigerd. Daarom staat de versie op de repeaterrij
(`fw_meshmanager`) en daarom is een knop die niet kan werken uitgeschakeld in
plaats van hoopvol.

## Verlooptijden

| Constante | Waarde | Betekenis |
|---|---|---|
| `POLLER_STALE_SECS` | 900 s | De HA-integratie pollt om de 30 s, dus een kwartier stilte is een afwezigheid en geen vertraging |
| `NODE_STALE_SECS` | 3600 s | Een **waarschuwing** op de pagina, geen weigering: het publicatie-interval loopt met de batterij mee en kan in zuinige modus oplopen |
| `clocksync.NODE_STALE_SECS` | 6 u | Een weigering, en dus ruimer |

## Tests

`server/tests/test_commanding.py` dekt de wegkeuze en de blockers;
`test_mqtt_command.py` dekt `publish_command()` inclusief de controle op
onderwerp en epoch; `test_settings_chain.py` dekt de hele keten van knop tot
bewaarde instelling.

## Verwante documenten

| Vraag | Document |
|---|---|
| De woorden op de draad en wat de firmware ermee doet | [`mqtt.md`](../mqtt.md#asking-a-node-for-something) |
| Het `time`-commando voluit | [`clocksync.md`](clocksync.md) |
| Waar de wachtrij en haar boekhouding wonen | [`database.md`](database.md#settings) |
| De beheerpagina's die deze knoppen tekenen | [`admin.md`](admin.md) |
