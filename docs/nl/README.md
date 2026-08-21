# MeshManager-documentatie

*[English](../README.md)*

Alles wat over dit project is opgeschreven, gegroepeerd naar wat je probeert te
doen. Bij elk item staat in één zin wat je er vindt, zodat je niet vijf
bestanden hoeft te openen om het juiste te vinden.

Elk document bestaat in het Engels als `docs/<naam>.md` en in het Nederlands als
`docs/nl/<naam>.md`, met dezelfde koppen. De link bovenaan elke pagina wisselt
van taal.

---

## Begin hier

| Document | Wat je er vindt |
|---|---|
| [README van de repo](../../README.nl.md) | Wat dit project is, wat de site kan, en een snelstart van vijf commando's |
| [`architecture.md`](architecture.md) | Hoe de onderdelen samenhangen, welke wegen data van een radio naar de site kan afleggen, en waarom het transport MQTT is en geen HTTP |
| [`glossary.md`](glossary.md) | Advert, flood, direct, scoped, hop, adreshash, padhash, transportcodes, companion, repeater, monitor — het vocabulaire dat de rest van deze documenten veronderstelt |
| [`migration.md`](migration.md) | Kom je van MeshStats: in welke volgorde je een draaiende installatie bijwerkt zodat de datastroom niet stilvalt, en welke vier namen met opzet blijven staan |
| [`deployment.md`](deployment.md) | De site installeren en draaien: Docker Compose, systemd zonder Docker, omgevingsvariabelen, reverse proxies, back-ups, upgrades |

Nieuw in MeshCore zelf? Lees eerst de [woordenlijst](glossary.md), daarna
[`architecture.md`](architecture.md). Nieuw in deze repo als ontwikkelaar? Lees
[`contributing.md`](contributing.md) vóór je eerste wijziging.

---

## Nodes beheren

Het hart van deze documentatie. De rest beschrijft een onderdeel; dit beschrijft
het werk.

| Document | Wat je er vindt |
|---|---|
| **[`node-management.md`](node-management.md)** | **De handleiding.** De drie beheerniveaus en hoe je ziet welk niveau een node heeft, een node onder beheer brengen, zijn instellingen lezen en schrijven met de drie risicocategorieën, de klok zetten, firmware upgraden en terugrollen, en wat je doet als een node niet terugkomt — met schermafbeeldingen van de beheerpagina's |

Lees die eerst. Hij verwijst door naar de pagina's die op één stap dieper ingaan:
[`admin.md`](admin.md) voor elk veld en elk formulier,
[`commanding.md`](commanding.md) voor hoe een weg berekend wordt,
[`clocksync.md`](clocksync.md) voor de klok, en
[`firmware-upgrade.md`](firmware-upgrade.md) voor het upgrademechanisme van begin
tot eind.

---

## De site en zijn API

| Document | Wat je er vindt |
|---|---|
| [`server.md`](server.md) | Wat er binnen `server/` draait: de FastAPI-applicatie, haar modules, achtergrondtaken, en hoe de delen samenhangen |
| [`api.md`](api.md) | Elke route die de server bedient — de JSON-API, de publieke pagina's, de beheerformulieren — met parameters, antwoorden en authenticatie |
| [`search.md`](search.md) | De Kibana-achtige zoektaal van het pakketarchief: syntaxis, velden, sorteren, en de belofte dat er nooit stilletjes iets wegvalt |
| [`commanding.md`](commanding.md) | Hoe de server bepaalt wat er op dit moment aan een repeater gevraagd kan worden, en wat een knop op de pagina dus mag beloven |
| [`clocksync.md`](clocksync.md) | Of deze machine het mesh mag vertellen hoe laat het is, en langs welke weg dat antwoord reist |

---

## De data

| Document | Wat je er vindt |
|---|---|
| [`database.md`](database.md) | Elke tabel en kolom in het SQLite-schema, wat erin gaat en waarom, plus hoe additieve migraties werken |
| [`decoder.md`](decoder.md) | Wat `server/app/packets.py` uit een ruw frame haalt, wat hij weigert te decoderen, en waarom weigeren het juiste antwoord is |
| [`candidates.md`](candidates.md) | Hoe een node uit één byte sleutel benoemd wordt, wanneer de site mag zeggen wélke node het was, en wanneer hij in plaats daarvan alle mogelijkheden moet tonen |

---

## Het MeshCore-protocol

| Document | Wat je er vindt |
|---|---|
| [`protocol.md`](protocol.md) | Het pakketformaat in de ether en het companion-protocol over TCP/serieel, byte voor byte gespecificeerd met uitgewerkte voorbeelden, gereconstrueerd uit de firmwarebroncode |
| [`mqtt.md`](mqtt.md) | Topics, payloadschema's, de twee commando's die de site mag sturen, bewaartermijnen, en brokeropzet met een account per node |

[`protocol.md`](protocol.md) is het lezen waard, ook als je dit project nooit
draait. Het MeshCore-wireformaat staat nergens anders beschreven.

---

## De firmware

| Document | Wat je er vindt |
|---|---|
| [`firmware.md`](firmware.md) | Elke wijziging die MeshManager in MeshCore aanbrengt — meerdere companions tegelijk, de statspublisher, de netwerkmodule van de repeater — en hoe je het bouwt en flasht |
| [`firmware-upgrade.md`](firmware-upgrade.md) | Hoe een node aan een nieuw image komt: GitHub-releases, de checksum die twee keer gecontroleerd wordt, waarom alleen succes herstart, hoe je teruggaat, en wat een checksum **niet** bewijst |
| [`packet-filter.md`](packet-filter.md) | Het pakketfilter van de repeater: welke doorgestuurde pakketten het mag weggooien en op welke grond, wat het nooit aanraakt, waarom een kanaal blokkeren een sleutel vraagt in plaats van een naam, en de weg terug als een filter verkeerd gezet is |

---

## Optionele onderdelen

Geen van beide is nodig. Ze bestaan voor situaties die de hoofdweg niet dekt.

| Document | Wat je er vindt |
|---|---|
| [`homeassistant.md`](homeassistant.md) | De HA-integratie: wat hij nog doet nu nodes zelf over MQTT publiceren, wanneer je hem wilt, hoe hij repeaters ontdekt, en hoe hij CLI-instellingen over LoRa ophaalt |
| [`proxy.md`](proxy.md) | De TCP-fan-outproxy waarmee meerdere clients één node kunnen delen als je geen aangepaste firmware kunt flashen |

---

## Draaien en onderhouden

| Document | Wat je er vindt |
|---|---|
| [`deployment.md`](deployment.md) | Omgevingsvariabelen, reverse proxies, automatische upgrades, back-ups, schijfgebruik, logs, en de tijdreeksdatabase |
| [`backup.md`](backup.md) | Het back-upscript: een consistente SQLite-kopie plus een VictoriaMetrics-snapshot, de cronregel, de rotatie, het terugzetten, en de eerlijke noot dat offsite de stap van de beheerder is |
| [`admin.md`](admin.md) | Het beheerdersperspectief op `/admin`: accounts, API-tokens, sessies, en elk formulier achter de inlog |
| [`retention.md`](retention.md) | Hoelang de site dingen bewaart, wat verhindert dat de schijf volloopt, en waarom de beheerpagina het hardop zegt als de ingestelde termijn niet gehaald wordt |
| [`security.md`](security.md) | Het dreigingsmodel, wat er hoe beschermd wordt, en — minstens zo belangrijk — wat niet |
| [`per-node-credentials.md`](per-node-credentials.md) | Elke sensornode zijn eigen weblogin geven in plaats van de gedeelde vlootsleutel: het model, hoe rotatie werkt, de bootstrap, en de eerlijke grens dat Basic-auth over HTTP nog leesbaar over het LAN reist |
| [`privacy.md`](privacy.md) | Wat de site toont over nodes van anderen en waarom dat mag, de drie zichtbaarheidsschakelaars per node, en wat geen enkele schakelaar verbergt |

---

## Ontwikkelen

| Document | Wat je er vindt |
|---|---|
| [`contributing.md`](contributing.md) | De conventies die verklaren waarom de code eruitziet zoals hij eruitziet: eerlijkheid over onzekerheid, commentaar dat het waarom draagt, Nederlandse commitboodschappen, vanilla JS zonder buildstap, additieve migraties, `packets.raw` als grondwaarheid |
| [`testing.md`](testing.md) | Hoe je de testsuite draait, hoe testpakketten uit de specificatie gebouwd worden in plaats van opgevangen, en waarom de meeste tests een weigering vastleggen |

---

## Zoeken op vraag

| Vraag | Ga naar |
|---|---|
| **Wat kan ik eigenlijk met mijn nodes?** | **[`node-management.md`](node-management.md)** |
| Hoe breng ik een node onder beheer? | [`node-management.md`](node-management.md) |
| Welke instellingen mag ik op afstand wijzigen? | [`node-management.md`](node-management.md) |
| Hoe wijzig ik een instelling op een repeater die ik alleen over LoRa bereik? | [`node-management.md`](node-management.md) |
| Mijn node kwam niet terug na een upgrade | [`node-management.md`](node-management.md) |
| Mijn opdrachten krijgen helemaal geen antwoord | [`node-management.md`](node-management.md) |
| Wat betekent dit woord? | [`glossary.md`](glossary.md) |
| Hoe krijg ik dit draaiend? | [`deployment.md`](deployment.md) |
| Wat geeft dit API-endpoint terug? | [`api.md`](api.md) |
| Hoe doorzoek ik het pakketarchief? | [`search.md`](search.md) |
| Wat staat er in deze databasekolom? | [`database.md`](database.md) |
| Wat betekenen deze bytes? | [`protocol.md`](protocol.md) |
| Waarom zegt de site hier "onbekend"? | [`candidates.md`](candidates.md), [`decoder.md`](decoder.md) |
| Waarom staat deze knop uit? | [`commanding.md`](commanding.md) |
| Waar zijn mijn oude pakketten gebleven? | [`retention.md`](retention.md) |
| Hoe maak ik back-ups? | [`backup.md`](backup.md) |
| Hoe beheer ik accounts en tokens? | [`admin.md`](admin.md) |
| Hoe krijg ik mijn node aan het publiceren? | [`mqtt.md`](mqtt.md), [`firmware.md`](firmware.md) |
| Hoe upgrade ik een node vanaf de site? | [`firmware-upgrade.md`](firmware-upgrade.md) |
| Waarom stuurt deze repeater niets meer door? | [`packet-filter.md`](packet-filter.md) |
| Waarom is deze knop grijs bij deze node? | [`node-management.md`](node-management.md), [`commanding.md`](commanding.md) |
| Is het veilig dit op het internet te zetten? | [`security.md`](security.md) |
| Kan ik de positie van een node verbergen maar zijn cijfers houden? | [`privacy.md`](privacy.md) |
| Mijn data komt uit Home Assistant | [`homeassistant.md`](homeassistant.md) |
| Ik kan geen firmware flashen | [`proxy.md`](proxy.md) |
| Hoe draag ik een wijziging bij? | [`contributing.md`](contributing.md) |
| Hoe draai ik de tests? | [`testing.md`](testing.md) |

---

## Conventies in deze documenten

- **Beweringen zijn na te trekken.** Gedrag wordt toegeschreven aan een bestand
  en, waar dat uitmaakt, aan een functie of regelnummer. Firmwaregedrag wordt
  geciteerd tegen de MeshCore-broncode, zodat een latere lezer het tegen zijn
  eigen versie kan hercontroleren.
- **Onzekerheid wordt benoemd.** Waar de documentatie iets niet weet, of waar het
  systeem bewust weigert het te weten, staat dat er — in plaats van dat het glad
  gestreken wordt.
- **Beide talen zijn volledig.** Het Nederlands is een volwaardige vertaling, geen
  samenvatting. Een document met maar één helft is een fout — zie
  [`contributing.md` §10](contributing.md#10-documentatieconventies).
