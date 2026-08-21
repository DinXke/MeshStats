# De rooms van een room-server-node beheren

*[English](../rooms.md)*

Een MeshUptime-room-server-node host meerdere virtuele *rooms* — gedeelde
kanalen waar clients aan kunnen deelnemen en waar sensoren hun alarm in kunnen
posten. De site leest en beheert die rooms volledig over de eigen HTTP-API van
de node, op de nodepagina onder **Beheer over IP → Rooms**. Alles hier loopt
over de WiFi van de node, dezelfde weg als de rest van dat blok: valt de WiFi
weg, dan valt dit blok mee weg.

## Wat een room-server-node is

Het is een node die `GET /rooms.json` beantwoordt, achter dezelfde
Basic-auth-login als de rest van zijn API. Room 0 is de eigen identiteit van de
node; rooms 1 en verder hebben elk een eigen keypair. Antwoordt een node niet op
`/rooms.json`, dan zegt de site dat en tekent ze niets — het is dan geen
room-server-node.

## De rooms lezen

De Rooms-sectie toont per room: de naam, de `idx`, of hij stealth is of gasten
toelaat, het aantal posts, en de korte publieke sleutel. De lijst wordt bij elke
paginaweergave vers van de node gelezen, en één keer per pollronde op de
achtergrond.

## Toevoegen, bewerken en verwijderen

Toevoegen vraagt een naam; de node bakt de sleutel. Bewerken verandert alleen de
velden die je opgeeft — laat een naam, roomwachtwoord of gastwachtwoord leeg en
die blijven staan. Het gastwachtwoord wissen is een aparte schakelaar ("gast
wissen"), zodat een deelbewerking met een leeg gastveld een bestaand
gastwachtwoord nooit wegveegt; de stealth-vlag is een aankruisvakje en gaat altijd
mee. Verwijderen vraagt een getypte bevestiging, want de sleutel van de room gaat
mee weg. Alle drie vragen het recht `node.instelling.merkbaar`, dezelfde klasse
als het zetten
van een regio.

## De join-link en QR

Elke room draagt een join-URI. De site rendert er serverzijde een QR van, als
inline-SVG, zonder externe bibliotheek en zonder CDN — de strakke
Content-Security-Policy van de site zou die toch blokkeren. De platte link staat
naast de QR zodat het ook zonder camera werkt.

## De alarmroute per sensor

Elke bewaakte sensor heeft een alarmroute: direct bericht, in een room, of beide
(`am` = 1/2/3), plus welke rooms hij aanspreekt (`rm`, een bitmasker) en naar
welke virtuele sensor-nodes zijn telemetrie gaat (`sn`, een bitmasker). Het
formulier toont een keuzelijst voor de route, een aankruisvakje per room en een
per sensor-node; de server bouwt de bitmaskers en stuurt `am`/`rm`/`sn` samen. De
huidige stand komt uit `/status.json`, die de site toch al ophaalt, dus er gaat
geen extra verzoek de deur uit. Onder elke room zie je bovendien welke sensoren
hun alarm erin posten.

## Virtuele sensor-nodes

Dezelfde node host ook virtuele *sensor-nodes*: aparte contact-identiteiten
waaronder telemetrie in de MeshCore-app verschijnt. Ze komen uit dezelfde
`/rooms.json`-call (`snode_max`/`snode_active`/`snodes`) en worden symmetrisch met
de rooms beheerd — toevoegen, bewerken (naam en stealth), verwijderen, en een
contact-QR + link uit het `uri`-veld. Elke sensor-node toont de kanalen die eraan
gekoppeld zijn; de node stuurt alleen de kanaalnummers en de site vult de namen
aan uit zijn eigen kanaalnaam-gegevens. De alarmroute hierboven bepaalt welke
meting van welke sensor naar welke sensor-node gaat.

## SNMP-monitors

Een node kan ook een extern apparaat over SNMP bewaken: hij vraagt periodiek een
OID op bij een host. De site voegt er een toe via `POST /monitor/snmp` (velden
`name`/`host`/`int`/`community`/`oid`/`interp`/`snmparg`), met een
**preset-OID-bibliotheek** zodat niemand OIDs hoeft over te typen —
interface-tellers (ifHCInOctets, ifHCOutOctets), ifOperStatus, en UPS-MIB
(batterijstatus, resterende minuten, belasting, in-/uitgangsspanning). Een
handmatige OID plus interpretatie (`numeric`/`rate`/`status`) staat er voor wat de
presets niet dekken, en een indexveld vult `snmparg` voor per-interface- of
per-UPS-lijn-OIDs. De **community is een geheim**: hij gaat naar de node maar is
op de site write-only — nooit teruggetoond en nooit gelogd, en `/status.json`
meldt per SNMP-monitor alleen `knd`/`itp`/`oid`. Een nieuw SNMP-kanaal kan in
dezelfde stap aan een room/sensor-node gekoppeld worden en routeert daarna als elk
ander kanaal.

## De notifier-bot

Een node kan een *notifier-bot* draaien: een identiteit die meldingen als direct
bericht naar een ontvangerslijst stuurt, of ze op zijn eigen kanaal post. De site
toont de naam, publieke sleutel en een contact-QR + link van de bot
(`GET /bot.json`) en beheert de ontvangerslijst — een volledige pubkey toevoegen
of er een op prefix verwijderen (`POST /bot/recipient`). Knoppen sturen een advert
(`POST /bot/advert`), een test-DM naar één sleutel (`POST /bot/sendto`) of een
post naar de hele lijst (`POST /bot/post`). Ontvangers zijn publieke sleutels; er
zit geen geheim in de lijst. De inhoud van berichten komt niet in het audittrail —
alleen naar wie een DM ging.

## Toegang (ACL)

Elk room- en elk sensor-node-slot draagt een per-sleutel-toegangslijst: welke
publieke sleutel binnen mag, op welk niveau — `read` (joinen en lezen: de posts
van een room, de telemetrie van een sensor-node), `readwrite` (ook schrijven) of
`admin` (beheer van dat slot). Het is **wachtwoordloos**: een sleutel in de lijst
komt op zijn niveau binnen zonder wachtwoord. De site leest de lijst uit de
`acl`-array van `/rooms.json` (nooit een geheim, alleen publieke sleutels en
niveaus) en toont hem per room/sensor-node. Toevoegen vereist de volledige
64-hex-sleutel; verwijderen mag op een prefix van minstens 12 hex; een niveau
wijzigen zet de sleutel opnieuw op het nieuwe niveau. Dit loopt via `POST
/room/acl` en `POST /snode/acl`. Overal waar je een publieke sleutel invult — de
ACL-grants en de bot-ontvangerslijst — voeden de door de node **ontdekte
contacten** (`GET /contacts.json`) een naam→sleutel-kiezer naast het handmatige
veld: je kiest een contact op naam en de volledige sleutel wordt ingevuld.

## Backup en terugzetten

Een backup bevat de **privésleutels** van de rooms. De server bewaart backups als
bewaarplaats met versiehistoriek, geobfusceerd met dezelfde laag als de
per-node-wachtwoorden (zie per-node-credentials.md): niet leesbaar in een
databankdump, en nooit ergens heen gestuurd. Een backup maken, er een downloaden,
en terugzetten kan alleen een serverbeheerder. Een restore overschrijft de
huidige rooms en vraagt een getypte bevestiging; hij neemt een bewaarde backup of
geplakte JSON.

## Groeperen: veel entiteiten, één node

Omdat rooms, sensor-nodes én de notifier-bot elk hun eigen sleutel adverteren,
verschijnen ze op het mesh als losse node-entries. De site legt vast welke sleutel
bij welke fysieke node hoort, met de soort (room, sensor of bot) — geleerd uit
`/rooms.json` en `/bot.json` — zodat de nodelijst een losse entry markeert als
"room op node X", "sensor-node op node X" of "bot op node X" en zijn eigenaar als
"host van N rooms + M sensor-nodes + een bot", in plaats van ze als anonieme
unmanaged nodes te laten rondzweven. De koppeling wordt gesnoeid zodra een
entiteit van de node verdwijnt.

## Het nodecontract en de aannames

De site spreekt `GET /rooms.json`, `POST /room/add|edit|del`, `POST
/snode/add|edit|del`, `POST /room/acl` en `POST /snode/acl` (form `idx`/`pubkey`/
`level`, of `del=1` met een prefix), `POST /mon/alarm`, `POST /monitor/snmp`,
`GET /bot.json`, `POST /bot/recipient|advert|sendto|post`, `GET /contacts.json`,
`GET /rooms/backup` en `POST /rooms/restore` aan. De alarmroute wordt
kanaal-gebaseerd gezet via `POST /mon/alarm` (formvelden `ch`/`am`/`rm`, optioneel
`sn`, waarbij `ch` het kanaalnummer uit `mon[].ch` is en op de node wint). Het
node-centrische kanaalpanel gebruikt diezelfde setter — een kanaal aan-/afvinken
op een room/sensor-node zet alleen de `rm`- resp. `sn`-bit van die entiteit en
laat de andere maskers met rust. Een kanaal aanmaken loopt via het bestaande `POST
/monitor` (formvelden `name`/`host`/`int`), en een SNMP-kanaal via `POST
/monitor/snmp`; beide geven platte tekst terug (`ok <naam> -> kanaal <N>`) — de
site plukt het kanaalnummer eruit en koppelt het. Adverts gaan via `POST
/room/advert` / `POST /snode/advert` (formvelden `idx`/`flood`). Dit alles staat
geïsoleerd achter de `MON_ALARM_*`/`MONITOR_ADD_PATH`/`SNMP_MONITOR_PATH`/
`*_ADVERT_PATH`/`BOT_*`/`CONTACTS_PATH`-constanten en hun functies in
`server/app/rooms.py`, zodat een afwijkend contract een kleine wijziging is. De
netwerkgrens zelf blijft in `sensornode.py`, achter dezelfde doelcontrole en
vloot-/per-node-credential als elke andere aanroep naar een node.
