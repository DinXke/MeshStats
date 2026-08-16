# Van MeshStats naar MeshManager

*[English](../migration.md)*

Dit project heette MeshStats tot 16 augustus 2026. Alles wat van het project
zelf is, is hernoemd: de site, de omgevingsvariabelen van de server, het
MQTT-topicvoorvoegsel, de firmwaremodule, het domein van de Home
Assistant-integratie, de containernamen en de release-images.

**Draait er nog niets? Sla dit document over.** Een verse installatie is overal
al MeshManager. Deze pagina is voor een installatie die vandaag werkt, en gaat
over één vraag: in welke volgorde werk je bij zodat de datastroom niet stilvalt?

---

## Waarom de volgorde uitmaakt

Nodes en server gaan nooit op hetzelfde moment om. Er is altijd een periode
waarin de ene kant de nieuwe namen spreekt en de andere de oude — en maar één
van de twee is te leren allebei te verstaan.

| | Verstaat allebei | Waarom |
|---|---|---|
| **Server** | ja | Hij schrijft zich in op `meshmanager/+/stats` **en** `meshcore/+/stats`, leest zowel `MM_*` als `MCS_*`, opent een bestaand `mcs.sqlite3`, en aanvaardt zowel `fw_meshmanager` als `fw_meshstats` in een payload |
| **Node** | nee | Hij publiceert op precies één voorvoegsel. Welk, is een instelling en geen onderhandeling |

Dus: **server eerst, dan pas de nodes.** Andersom publiceert een geflashte node
naar `meshmanager/…` terwijl daar niemand luistert, en nergens staat een fout:
de publish van de node slaagt, de broker aanvaardt hem, en de site wordt gewoon
stil. Dat is precies de storing waar dit hele project omheen gebouwd is — maak
hem dan niet zelf tijdens een hernoeming.

---

## De volgorde

### 1. Broker (vóór de server, en het kost een minuut)

Draai je een ACL — en dat hoor je te doen — dan moet de broker allebei de
voorvoegsels toestaan voor er iets anders beweegt. Een node wiens publish op de
ACL wordt geweigerd, hoort daar niets over: hij meldt een geslaagde publish, het
bericht valt op de grond, en de site wacht op cijfers die nooit komen.

`mosquitto/acl.example`, `mosquitto/init-passwd.sh` en
`mosquitto/add-node-user.sh` genereren allebei de regelsets al. Voor een
bestaande `mosquitto/acl`: zet naast elke `meshcore/…`-regel die je al hebt de
bijbehorende `meshmanager/…`-regel, en herlaad de broker.

### 2. Server

```bash
git pull
docker compose build
docker compose up -d --remove-orphans
```

`--remove-orphans` is niet vrijblijvend: de compose-services zijn hernoemd, dus
zonder dat blijft de oude container `meshstats` naast de nieuwe draaien, houdt
hij poort 8080 bezet, en start de nieuwe nooit.

Verder is er niets nodig. In het bijzonder hoef je **niet** aan te raken:

- **je `.env`** — elke `MCS_*`-naam wordt nog gelezen. Hernoem ze wanneer het je
  uitkomt; zet je beide, dan wint de nieuwe.
- **je databank** — een bestaand `mcs.sqlite3` wordt geopend waar het staat en
  nooit hernoemd. Zie [waarom](#namen-die-met-opzet-blijven-staan).
- **je Docker-volumes** — de projectnaam van compose staat nu vast op
  `meshstats`, dus de volumes houden de namen die ze hebben.

Kijk daarna op `/admin`: het MQTT-blok toont per voorvoegsel hoeveel nodes er
binnenkomen. Ze staan allemaal nog op `meshcore` — dat klopt, je hebt nog niets
geflasht.

### 3. Nodes

Flash firmware **2.0.0** of hoger. Over de lucht via `/admin/firmware`, of over
USB.

De site vertaalt de oude naam van de bouwomgeving precies één keer naar de
nieuwe, zodat een node die nog 1.12.0 draait het juiste image krijgt aangeboden
ook al heet de release nu `heltec_v4_repeater_meshmanager`. Zonder die vertaling
zou 2.0.0 alleen met een kabel te installeren zijn.

Bouw je zelf? Hernoem `-D MESHSTATS_NET` naar `-D MESHMANAGER_NET` in je eigen
`platformio.local.ini`. Vergeet je dat, dan krijg je een build die compileert,
flasht en start — als een gewone MeshCore-repeater, zonder beheerpagina en
zonder MQTT, en zonder één foutmelding.

**Bevestigen dat een node om is**, op twee onafhankelijke manieren:

- `ver` op eender welke CLI hoort `MeshManager (by DinX) v2.0.0` te antwoorden.
  Iets anders, ook een gewoon MeshCore-antwoord, betekent dat de module niet
  draait.
- `/admin` toont de node binnen één publicatieronde onder voorvoegsel
  `meshmanager`, en zijn moduleversie op zijn eigen pagina.

Wat de node zelf doet, en wat niet:

- Zijn **configuratie blijft staan**. `/msnet.json`, `/mspwr.json`,
  `/msmon.json` en `/adverts.dat` op de datapartitie houden hun namen, en een
  OTA raakt die partitie niet aan. WiFi-gegevens, brokerinstellingen en de
  monitorlijst staan er na het flashen nog.
- Zijn **topicvoorvoegsel verhuist vanzelf**, één keer, van `meshcore` naar
  `meshmanager` — maar alleen als er letterlijk de oude standaard stond. Koos je
  bewust iets anders, dan gebeurt er niets. En zet je het na deze upgrade met
  opzet terug op `meshcore`, dan blijft dat staan: de verhuizing wordt met een
  `cfg_ver` in het configuratiebestand vastgelegd en gebeurt niet opnieuw.
- Een **companion verhuist niet vanzelf**. Zet dat op zijn eigen beheerpagina,
  of met `wifi mqtt prefix meshmanager`.

### 4. Home Assistant, als je de integratie gebruikt

Een domeinhernoeming is voor Home Assistant een nieuwe integratie, en daar
bestaat geen migratiehaak voor — `async_migrate_entry` werkt binnen één domein,
niet ertussen. Dit deel gaat dus met de hand:

1. Instellingen → Apparaten en diensten → verwijder de invoer **MC Repeater
   Stats**.
2. Verwijder `custom_components/mc_repeater_stats/` en zet
   `custom_components/meshmanager/` ervoor in de plaats.
3. Herstart Home Assistant.
4. Voeg **MeshManager** toe en vul dezelfde basis-URL en hetzelfde token in.

Automatiseringen die `mc_repeater_stats.*`-diensten aanroepen, moeten naar
`meshmanager.*`. De integratie maakt zelf geen entiteiten aan — ze leest
MeshCore-entiteiten en duwt die door — dus er verhuist verder niets en er gaat
geen historiek verloren.

### 5. Opruimen, later, en alleen als je kunt zien dat het mag

Niets hiervan heeft haast, en bij elk terugvalpad staat in de code een noot
wanneer het weg mag.

| Weghalen | Wanneer |
|---|---|
| de `meshcore/…`-regels uit `mosquitto/acl` | `/admin` toont geen enkele node meer op het voorvoegsel `meshcore` |
| `MCS_*` uit je `.env` | na één geslaagde herstart op de nieuwe namen |
| `LEGACY_PREFIX` in `mqtt_ingest.py` | zelfde voorwaarde als de ACL-regels |
| `ENV_ALIAS` in `firmware.py` | geen enkele node meldt nog de oude bouwomgeving |
| de oude assetnaam in `ASSET_RE` | geen release in de lijst draagt nog `meshstats-*.bin` |
| `/opt/mc-repeater-stats` (installatie met systemd) | als de nieuwe service een tijd gedraaid heeft |

---

## Namen die met opzet blijven staan

Vier namen zijn niet meegegaan, en telkens om dezelfde reden: ze staan niet in
de code maar **in de data**. Ze hernoemen verandert geen etiket — het maakt
bestaande gegevens onbereikbaar terwijl alles er goed uitziet.

| Naam | Wat het is | Wat hernoemen zou kosten |
|---|---|---|
| `mcs.sqlite3` | je databankbestand | Niets, als je het hernoemt terwijl de site stilstaat. Maar de site doet het niet voor je: wie daarna terugrolt naar de vorige versie vindt dan geen databank, krijgt een lege, en kijkt naar een site zonder repeaters, zonder historiek en zonder beheerderswachtwoord |
| `meshstats-data`, projectnaam `meshstats` | het Docker-volume waar de databank in leeft | Een nieuw, leeg volume naast een vol volume dat niemand meer opent. De projectnaam volgde vroeger de mapnaam, dus je kloon hernoemen zou dit in stilte gedaan hebben — en de autoupdate draait elke vijf minuten zonder toezicht. Hij staat nu vast |
| `meshstats` in VictoriaMetrics | de reeksnaam van elke meting die er ooit in ging | Elke grafiek begint op de dag van de upgrade bij nul, met de historiek er nog wél, alleen te vinden door met de hand een andere reeksnaam in te tikken. `MM_TSDB_MEASUREMENT` bestaat voor wie het toch wil, ná de reeksen in VictoriaMetrics zelf hernoemd te hebben |
| `/msnet.json` en de rest | de configuratie van de node op zijn datapartitie | Een node die uit een OTA terugkomt zonder WiFi-gegevens, zonder brokerinstellingen en zonder monitorlijst — als eigen accesspoint, op een dak |

Alle vier kun je later alsnog met de hand doen. Het zijn eenrichtingsstappen, en
daarom doet dit project ze niet voor je.

---

## De GitHub-repo hernoemen

Niet iets wat de repo met zichzelf kan doen, en niet iets wat tegelijk met het
bovenstaande hoeft te gebeuren. Zie
[`contributing.md`](contributing.md#de-repository-hernoemen) voor de stappen en
voor wat er daarna nog aangepast moet worden.
