# Weblogin per node

*[English](../per-node-credentials.md)*

MeshManager benadert de eigen HTTP-API van een sensornode over IP --
`/status.json`, `/cfg.json`, `/acl.json`, `POST /cli` -- achter HTTP Basic-auth.
Deze pagina beschrijft hoe elke node zijn **eigen** weblogin kan hebben die de
server kent en gebruikt, in plaats van één gedeelde login voor de hele vloot.

## De zwakte die dit weghaalt

Tot deze functie meldde elke node zich met hetzelfde paar,
`MM_FW_NODE_USER`/`MM_FW_NODE_PASS` (de "vloot"-credential). Dat is precies
dezelfde credential waarmee de server firmware en instellingen naar **elke** node
schrijft. Eén gelekte node -- een afgeluisterde HTTP-sessie, een node die in
verkeerde handen valt, een back-up die wegloopt -- gaf daarmee de sleutel van de
hele vloot weg.

Een eigen login per node maakt geen enkele node moeilijker af te luisteren. Wat
hij wél doet is de schade indammen: een lek van node A opent niet langer de nodes
B en C.

## Het model

Twee kolommen die leeg mogen zijn staan bij de noderij (`repeaters`): `web_user`
(platte tekst -- een gebruikersnaam is geen geheim, en de kolom moet doorzoekbaar
zijn om bij een adres de juiste rij te vinden) en `web_pass_enc` (het wachtwoord,
geobfusceerd -- zie onder).

`NULL` in `web_user` betekent precies "deze node heeft nog geen eigen login,
gebruik de vlootsleutel". Dat is de terugval die bestaande nodes na de update
laat werken, tot elke node een keer geroteerd is. Heeft een node wél een eigen
login, dan gebruikt de server die voor elke IP-verbinding naar de `sensor_host`
of `ota_host` van die node; de keuze valt op één plek, `firmware._auth_header`,
waar elk uitgaand verzoek naar een node langskomt.

## Hoe rotatie werkt

Roteren staat achter dezelfde grens als het invullen van een beheeradres: alleen
een **serverbeheerder** mag het (dezelfde niet-delegeerbare controle als bij het
invullen van `sensor_host`), plus het gewone recht `node.beheeradres` op die
node. De knop staat naast de credential-status op de nodepagina, achter CSRF.

De volgorde is de hele veiligheid van de handeling:

1. Genereer een sterke, willekeurige `user` + `pass`.
2. Roep de node aan op `POST /web/cred` met zijn **huidige** credential, met body
   `{"user": "<nieuw>", "pass": "<nieuw>"}`. De node antwoordt `200 {"ok":1}` en
   gebruikt de nieuwe credential vanaf zijn volgende verzoek; een leeg wachtwoord
   wordt geweigerd met `400`.
3. Bewaar de nieuwe login **pas na** een `200`.

Faalt de node-aanroep -- adres geweigerd, geen antwoord, een HTTP-fout, of een
antwoord dat geen `{"ok":1}` is -- dan verandert er niets aan de opgeslagen
credential, en wordt de fout gemeld. Die volgorde is met opzet: eerst opslaan en
dan proberen zou je buitensluiten op het moment dat de node niet meebeweegt, want
dan klopt de server met een wachtwoord dat de node nooit aannam. Elke rotatie,
geslaagd of niet, schrijft een auditregel (nooit het wachtwoord zelf).

## Bootstrap

De allereerste rotatie heeft nog geen eigen login om zich mee aan te melden. De
"huidige" credential is daarom de eigen login als die er is, en anders de
vlootsleutel. Dat valt vanzelf goed: `firmware._auth_header` kijkt naar wat er nu
opgeslagen is, en dat is de oude waarde tot stap 3 hierboven gedaan is. De eerste
rotatie meldt zich dus met de vlootsleutel aan en vervangt die bij succes door de
eigen login van de node. Is er geen eigen login én geen vlootsleutel, dan is er
niets om de wijziging mee aan te melden, en dan zegt de rotatie dat, in plaats
van bij de node op een 401 te stuiten.

## Wat er bewaard wordt, en hoe het geobfusceerd is

Het wachtwoord wordt omgezet in een blob met een sleutel uit het
installatiegeheim (`config.SECRET`, het bestand `secret.key` naast de databank),
met HMAC-SHA256 als sleutelstroom, een verse willekeurige nonce per waarde en een
korte echtheidsmarkering. Dit is stdlib alleen, dezelfde lijn die het project al
voor sessies en CSRF-tokens aanhoudt; er is geen extra pakket voor nodig.

Wees eerlijk over wat dit is: het is **obfuscatie, geen versleuteling**. Wie de
databank kan lezen, kan meestal ook `secret.key` lezen -- ze staan in dezelfde
datamap -- en kan de blob dan terugdraaien. Wat het oplevert is dat het wachtwoord
niet in platte tekst in een back-up staat die je doormailt of plakt. Het komt
nooit in `/status.json`, bereikt nooit de UI, en komt nooit in het audittrail. De
verse nonce zorgt dat twee nodes met hetzelfde wachtwoord niet dezelfde blob
krijgen, zodat de databank niet verraadt welke nodes gelijk zijn ingesteld.

## De eerlijke grens: Basic-auth over HTTP

Een per-node-credential reist nog steeds **leesbaar over het LAN** bij elk
verzoek, want Basic-auth over kale HTTP stuurt `user:pass` base64-gecodeerd, niet
versleuteld. Deze functie beperkt dus de schade van één lek (één node in plaats
van de vloot); ze vervangt geen transportbeveiliging. Wie de draad tussen de
server en een node passief kan meekijken, leest nog steeds de login van die node.
De echte verdediging daartegen is TLS op de webserver van de node, of een apart
beheer-VLAN waar het verkeer niet vanaf te bekijken is -- allebei buiten het bereik
hier, en allebei nog steeds de moeite waard. Deze functie en die twee zijn een
aanvulling op elkaar, geen vervanging.

## Terugval en veiligheid bij terugrollen

De migratie is additief: twee kolommen die leeg mogen zijn, geen herschrijving
van gegevens. Dat is van belang voor de deploy-gate, die oude code kan
terugzetten op een al-gemigreerde databank -- oude code die deze kolommen niet
kent, laat ze gewoon liggen, en nodes vallen terug op de vlootsleutel, precies
zoals eerst. De login van een node wissen (`clear_node_web_cred`) zet die node
ook terug op de vlootsleutel, voor het geval een node opnieuw opgezet is en zijn
weblogin weer op de gedeelde waarde staat: een opgeslagen credential die niet meer
bij de node past is erger dan geen, want dan blijft de server met een dood
wachtwoord kloppen.
