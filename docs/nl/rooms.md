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
velden die je opgeeft — laat de naam of het wachtwoord leeg en die blijven staan;
de gast- en stealth-vlag zijn aankruisvakjes en gaan altijd mee. Verwijderen
vraagt een getypte bevestiging, want de sleutel van de room gaat mee weg. Alle
drie vragen het recht `node.instelling.merkbaar`, dezelfde klasse als het zetten
van een regio.

## De join-link en QR

Elke room draagt een join-URI. De site rendert er serverzijde een QR van, als
inline-SVG, zonder externe bibliotheek en zonder CDN — de strakke
Content-Security-Policy van de site zou die toch blokkeren. De platte link staat
naast de QR zodat het ook zonder camera werkt.

## De alarmroute per sensor

Elke bewaakte sensor heeft een alarmroute: direct bericht, in een room, of beide
(`am` = 1/2/3), plus welke rooms hij aanspreekt (`rm`, een bitmasker). Het
formulier toont een keuzelijst voor de route en een aankruisvakje per room; de
server bouwt het bitmasker. De huidige stand komt uit `/status.json`, die de
site toch al ophaalt, dus er gaat geen extra verzoek de deur uit. Onder elke room
zie je bovendien welke sensoren hun alarm erin posten.

## Backup en terugzetten

Een backup bevat de **privésleutels** van de rooms. De server bewaart backups als
bewaarplaats met versiehistoriek, geobfusceerd met dezelfde laag als de
per-node-wachtwoorden (zie per-node-credentials.md): niet leesbaar in een
databankdump, en nooit ergens heen gestuurd. Een backup maken, er een downloaden,
en terugzetten kan alleen een serverbeheerder. Een restore overschrijft de
huidige rooms en vraagt een getypte bevestiging; hij neemt een bewaarde backup of
geplakte JSON.

## Groeperen: veel rooms, één node

Omdat rooms 1+ hun eigen sleutel adverteren, verschijnen ze op het mesh als losse
node-entries. De site legt vast welke room-sleutel bij welke fysieke node hoort —
geleerd uit `/rooms.json` — zodat de nodelijst een losse room-entry markeert als
"room op node X" en zijn eigenaar als "host van N rooms", in plaats van de rooms
als anonieme unmanaged nodes te laten rondzweven. De koppeling wordt gesnoeid
zodra een room van de node verdwijnt.

## Het nodecontract en de aannames

De site spreekt `GET /rooms.json`, `POST /room/add|edit|del`, `GET /rooms/backup`
en `POST /rooms/restore` aan. Het zetten van de alarmroute van een sensor heeft
nog geen vaste setter-URL in het contract; die ene aanname staat geïsoleerd
achter `MON_ALARM_PATH` en `set_alarm` in `server/app/rooms.py`, zodat een
afwijkend contract een wijziging van één regel is. De netwerkgrens zelf blijft in
`sensornode.py`, achter dezelfde doelcontrole en vloot-/per-node-credential als
elke andere aanroep naar een node.
