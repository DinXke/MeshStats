# Wat de site toont, en waarvan je kunt zeggen dat ze het niet moet doen

*[English](../privacy.md)*

Deze site publiceert dingen over radio's die van anderen zijn. Dat verdient het
om uitgeschreven te worden, want er zitten twee verschillende vragen onder: wat
mag er überhaupt getoond worden, en wat kan een beheerder uitzetten. Deze pagina
beantwoordt allebei, en noemt ook de plaatsen waar het antwoord "hier valt niets
uit te zetten, en dit is waarom" luidt.

---

## 1. Twee soorten node

**Gevolgde repeaters** zijn rijen in de tabel `repeaters`. Iemand heeft ze
bewust aan deze installatie toegevoegd: ze hebben een pagina op `/r/<slug>`, een
batterijgrafiek, een burentabel, en — als de firmware het toelaat — knoppen die
het apparaat bereiken. Elke schakelaar op deze pagina gaat over hen.

**Nodes van derden** zijn al de rest op de kaart: de honderden bolletjes die er
staan omdat hun adverts opgevangen zijn. Ze hebben geen rij, geen pagina en geen
eigenaar die bij deze installatie bekend is.

Van zo'n node toont de site zijn sleutelprefix, zijn naam, zijn nodetype en, als
zijn advert er een droeg, zijn positie. Alle vier komen uit de **advert** — een
pakket dat de node op een open, vergunningsvrije band uitzendt, onversleuteld,
om de zoveel uur, naar iedereen binnen radiobereik. Er wordt hier niets
verkregen door te vragen, te porren of te combineren; de site schrijft op wat
haar toegezonden is, net als elke andere ontvanger in de buurt.

Dat is de eerlijke rechtvaardiging en meteen ook haar grens. Ze verklaart waarom
deze gegevens niet geheim zijn; ze maakt het verzamelen ervan op één doorzoekbare
plek daarmee nog niet onschuldig. Daar volgen twee dingen uit, allebei met
opzet:

- **Voor een node van derden is er geen schakelaar per node**, want er is niemand
  om hem aan te geven. Een formulier waarmee een willekeurige bezoeker elke node
  kon verbergen, zou een manier zijn om andermans repeater weg te poetsen, geen
  privacyknop.
- **De uitweg loopt over de radio, niet over deze site.** Een MeshCore-node
  zendt zijn positie alleen mee als hij zo ingesteld is. Een node die geen
  coördinaten meer uitzendt, voedt ze hier niet meer — en tegelijk ook bij elke
  andere ontvanger in de buurt niet meer, en dat is de enige plek waar die keuze
  werkelijk standhoudt. Zie [`protocol.md`](protocol.md) §1.3 voor het optionele
  positieveld in een advert.

Draai je deze installatie en vraagt iemand zijn node te verwijderen, dan is het
eerlijke antwoord: dat kan (de contactrijen wissen), dat hij terugkomt zodra ze
weer adverteren, en dat de blijvende oplossing op hun apparaat zit.

---

## 2. De drie schakelaars van een gevolgde repeater

Op `/admin/repeaters/{id}`, sectie **Zichtbaarheid op de site**. Alle drie per
node, alle drie omkeerbaar met dezelfde klik, en geen ervan raakt het apparaat of
de opgeslagen gegevens — ze bepalen alleen wat de server uitlevert.

| Schakelaar | Kolom | Uit betekent |
|---|---|---|
| Publiek | `is_public` | De node staat niet op de startpagina, `/r/<slug>` geeft 404, en geen enkele publieke API-route noemt hem |
| Positie tonen | `show_position` | De site gedraagt zich alsof ze van deze node nooit een positie gehoord heeft |
| Naam tonen | `show_name` | De node heet overal waar een bezoeker kan kijken naar zijn adreshash |

`show_position` en `show_name` zijn `INTEGER NOT NULL DEFAULT 1`, toegevoegd via
`db.COLUMN_MIGRATIONS`. Die standaard is geen formaliteit: `ALTER TABLE ADD
COLUMN` geeft bestaande rijen de standaardwaarde, dus een databank die deze
kolommen er bij een upgrade bij krijgt, toont precies wat ze daarvoor toonde.
Een privacykolom die de ochtend na een upgrade stilzwijgend een repeater van de
kaart haalt, is een ergere fout dan de ontbrekende kolom die ze oploste.

### De positie verbergen

De toestand die een verborgen positie oplevert, is dezelfde als "van deze node
nooit een advert met locatie gehoord", en dat is met opzet. Die toestand was
overal al afgehandeld: de linkkaart telt zulke buren in plaats van ze weg te
laten, een hop zonder positie laat een gat in een getekende route, en de
drukte-kaart weigert eroverheen te bruggen. Door hem te hergebruiken staat er
geen tweede mechanisme naast het eerste dat ermee gelijk gehouden moet worden.

Concreet, met `show_position = 0`:

- geen bolletje op de live kaart, op geen enkel zoomniveau;
- geen plek op de linkkaart van een repeater die hem als buur heeft;
- geen `sender_lat` / `observer_lat` bij een pakket, niet in de feed, niet in het
  archief en niet op de pagina van het pakket zelf;
- geen coördinaten en **geen afstand in kilometers** bij een kandidaat achter een
  adreshash — een afstand is uit twee posities berekend en zou hetzelfde gegeven
  in een andere eenheid alsnog overhandigen;
- **geen eindpunt van een heatmapsegment.** De node breekt de keten precies zoals
  een dubbelzinnige hop dat doet, zodat verkeer dat werkelijk over hem liep niet
  tot een lijn geteld wordt;
- geen land. Het land is uit de coördinaten afgeleid (`db.set_country()`) en is
  niets anders dan een grove vorm ervan.

Wat blijft: de naam, alle cijfers, de burenlijst, de SNR's, de pagina van de
node, en de sleutelprefix.

### De naam verbergen

De node heet dan `0xNN` — `0x` plus de eerste byte van zijn sleutel, in
hoofdletters. Die tekst is geen vondst voor deze functie: het is wat de
pakkettenlijst altijd al print voor een afzender die ze niet kan noemen
(`static/app.js`), zodat een lezer niet hoeft te leren dat er twee soorten
naamloos bestaan.

Dit dekt de naam overal waar hij uitgeleverd wordt, en daar zijn drie
afzonderlijke bronnen voor: `repeaters.name`, `contacts.name` en
`neighbors.name` — die laatste is de naam die een ándere repeater voor hem
doorgeeft, en die wint normaal. Het zoekveld `name:` in het archief loopt over
dezelfde gemaskeerde kolom, dus de echte naam werkt ook als zoekterm niet meer;
een naam bevestigen aan wie hem al vermoedde is nog steeds vertellen.

De naam die de beheerder ingetypt heeft, blijft in de databank staan en blijft
zichtbaar in `/admin`. Hem ook voor de beheerder verbergen zou betekenen dat hij
hem niet meer kan terugzetten, en niet meer kan zien bij welke fysieke node een
pagina vol knoppen hoort.

### Wat geen enkele schakelaar verbergt

- **De sleutelprefix.** Die zit in elke advert die de node uitzendt. Doen alsof
  deze site hem geheim kan houden, zou een belofte zijn die het apparaat zelf
  tegenspreekt.
- **De slug in `/r/<slug>`.** Die is uit de naam gemaakt toen de rij ontstond en
  verandert niet mee bij een hernoeming. De node verwijderen en zich opnieuw
  laten aanmelden onder een andere naam is de enige manier om hem te wijzigen.
- **Dat de node bestaat.** Een verborgen positie en een verborgen naam laten nog
  altijd een publieke pagina met cijfers staan. Wil je de node helemaal van de
  site af, gebruik dan `is_public`.

---

## 3. Wat het pakketfilter zegt over andermans verkeer

Een repeater met een pakketfilter weigert een deel van wat hij hoort door te
sturen. Hij telt wat hij weigerde, en sinds firmware 2.6.0 telt hij dat vrij
gedetailleerd. Die uitsplitsing verdient een eigen paragraaf, want anders dan
alles in §1 komt ze niet uit een advert. Een advert is identiteit die een node
zelf uitzendt; een weigering is iets wat er met het pakket van iemand anders
gebeurd is.

De grens loopt tussen **een meting van het gedrag van deze repeater** en **een
verslag van het verkeer van een bepaald iemand**.

| Gegeven | Waar | Waarom |
|---|---|---|
| Totalen: weggegooid, doorgelaten, vrijgesteld via de ACL, filter aan/uit | **openbaar** | Was voor 2.6.0 al openbaar. Een repeater met een filter aan stuurt andermans verkeer niet meer door, en wie dat merkt is juist degene die niet kan inloggen |
| Weggegooid per reden | **openbaar** | Idem: het beschrijft de repeater |
| Weggegooid per pakkettype, en per type × reden | **openbaar** | Het pakkettype van elk bericht staat al openbaar op de pakkettenpagina. 'ADVERT sneuvelde 40 keer op de hoplimiet' zegt iets over deze repeater, niet over wie die adverts uitzond |
| Druk op de snelheidslimiet: vensters met verkeer, vensters waarin hij beet, piek | **openbaar** | Een weggooiteller zonder noemer is geen meting. 12 op 4000 vensters is een limiet die ruim staat, 12 op 14 er een die in gewoon verkeer snijdt — en het aantal weggegooide pakketten kan gelijk zijn |
| De ingestelde limiet zelf | **beheerder** | Dat is een regel en geen waarneming. Regeltabellen staan sinds 2.3.0 achter de login |
| Geblokkeerd kanaal: hash en aantal treffers | **openbaar** | De hash is één byte van `sha256(kanaalsleutel)`, en die byte reist onversleuteld mee in elk groepsbericht op de lucht. Verzwijgen beschermt niemand, terwijl 'dit kanaal wordt hier geweerd, 900 keer' precies is wat iemand nodig heeft die zich afvraagt waarom zijn verkeer niet aankomt |
| Geblokkeerd kanaal: het label | **beheerder** | Geen waarneming maar de naam die *onze* beheerder aan het kanaal van *iemand anders* gaf. Het draagt niets wat de hash niet al draagt, en publiceren zou de site een oordeel over een derde laten herhalen waar ze een gedraging van deze node hoort te melden |
| Welk pakket weggegooid is: afzender, tijdstip, de losse gebeurtenis | **bestaat niet** | Zie hieronder |

**Er wordt nergens een verslag per pakket bijgehouden, op geen enkel
toegangsniveau.** De firmware telt; hij logt niet. Er is geen tabel met
geweigerde pakketten, geen afzender bij een weigering, geen tijdstip per
gebeurtenis. Dat is geen gat dat nog ingevuld moet worden: een repeater die
opschrijft wie hij weigerde en wanneer, houdt een logboek bij van andermans
communicatie, en dat is iets anders van soort dan het tellen van zijn eigen
gedrag. De tellers overleven bovendien geen herstart, dus ze zeggen 'sinds deze
node voor het laatst startte' en nooit 'ooit'.

Dat begrenst ook wat een beheerder van zijn eigen node leert. Het beheerdersbeeld
voegt de regelwaarden en de kanaallabels toe — zijn eigen configuratie — en
verder niets over personen.

---

## 4. Het hardop zeggen

Een node die door een zichtbaarheidskeuze uit beeld valt, wordt geteld en
gemeld. Deze site beschouwt een stille weglating als een langzaam vertelde
leugen, en die regel houdt niet op te gelden zodra de weglating er een is
waarom een beheerder gevraagd heeft.

| Waar | Veld | Getoond als |
|---|---|---|
| `/api/v1/repeaters/{slug}/map` | `hidden`, `hidden_names` | Een tweede regel onder de kaart, los van `unlocated` |
| `/api/v1/packets` (eerste ronde) | `hidden_nodes` | "N nodes tonen hun positie niet", naast de kaarthint |
| `/api/v1/packets/heatmap` | `hidden_nodes` | Een voetnoot bij de tooltip van de laag, naast `capped` |

`unlocated` en `hidden` worden apart geteld en nooit samengevoegd. "Nog geen
advert met locatie ontvangen" is een uitspraak over het mesh; "deze node toont
zijn positie niet" is een beslissing van een mens. Eén getal dat allebei dekt,
zou de eerste zin onwaar maken.

---

## 5. Hoe het gehandhaafd wordt

Eén view, `visible_contacts`, aangemaakt in `db._migrate()` nadat de kolommen
bestaan. Het is `contacts` met de naam, de breedte, de lengte en het land door
een `CASE` op de zichtbaarheid van de gevolgde repeater achter die sleutelprefix.
**Elke publieke leesweg selecteert uit de view; elke ingestweg
(`upsert_advert()`, `upsert_contacts()`, `set_country()`) schrijft nog steeds
naar de tabel.** Wat de site weet verandert niet — alleen wat ze vertelt.

Een view en geen filter per endpoint, omdat een positie langs zes wegen naar
buiten komt en een naam langs meer, en zes losse filters zijn zes plaatsen waar
de zevende weg vergeten wordt. De twee wegen die een view niet kan bereiken,
worden elk expliciet en op één plek afgehandeld: `db.public_name()` voor
`repeaters.name`, en `db.NEIGHBOR_NAME_SQL` voor `neighbors.name`.

`tests/test_zichtbaarheid.py` bevat één test per endpoint die aantoont dat een
verborgen positie er niet uit komt, plus `test_standaard_verandert_niets`, die
aantoont dat de standaardwaarden elk van die endpoints laten zoals ze waren.

---

## 6. Verder lezen

- [`security.md`](security.md) — het dreigingsmodel waar dit binnen valt
- [`database.md`](database.md) — de kolommen en de view
- [`api.md`](api.md) — de endpoints en hun velden
- [`admin.md`](admin.md) — de rest van `/admin`
- [`candidates.md`](candidates.md) — waarom een verborgen positie ook een afstand
  wegneemt, en waarom de node wél in de kandidatenlijst blijft
