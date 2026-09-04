# Beheerpagina's: indeling en componenten

Dit document is de leidraad voor de vormgeving van alles onder `/admin`, en de
vastlegging van de keuzes erachter. Het beschrijft *hoe* een beheerpagina is
opgebouwd; wat er op elke pagina staat en waarom, staat in
[`admin.md`](admin.md).

## Waarom dit nodig was

De beheerpagina's zijn gegroeid door aanbouw. Elk blok werd toegevoegd op het
moment dat de functie erbij kwam, met de uitleg erbij die op dat moment nodig
was, en met een eigen formuliertje (`class="rowform"`: label, veld en knop op
één regel). Dat leverde per blok een goed doordacht stukje op, en als geheel
een pagina van vijftig kaartjes achter elkaar waarin toestand, uitleg en
handeling door elkaar staan. Op de pagina van één node scrolt een beheerder
langs dertig schermen om bij het verwijderblok te komen, en de knop die een
filter aanzet ziet er hetzelfde uit als de knop die een naam opslaat.

Wat er níét mis was: de teksten. Die leggen uit wat de site wel en niet weet
over een apparaat op een dak, en dat is de kern van dit project. Ze blijven
staan. Wat verandert is *waar* ze staan en *hoeveel* ervan meteen in beeld is.

## Vier indelingsprincipes

1. **Kop, toestand, handeling — in die volgorde, in elk blok.** Een blok
   begint met wat het is, toont dan wat de site *weet* (feiten, geen knoppen),
   en biedt pas daarna aan wat je kunt *doen*. De uitleg die bij een handeling
   hoort staat bij die handeling, en niet ervoor.

2. **Wat je zelden nodig hebt staat uit het zicht, niet weg.** Lange
   toelichtingen gaan in een uitklapper (`<details>`) met een samenvatting die
   zegt waar ze over gaat. Zeldzame en ingrijpende blokken (firmware,
   verwijderen, het audittrail) staan ingeklapt onderaan. Niets verdwijnt: de
   tekst staat in de DOM, een schermlezer bereikt hem, en zonder JavaScript
   klapt hij gewoon uit.

3. **Het risico staat in de vorm.** De drie risicoklassen die de code al kent
   (`nodeconfig.RISK_PLAIN/WRITES/CUTOFF`, `pktfilter.risk_of`, de rollen in
   `rbac.py`) krijgen elk één vaste verschijning, overal dezelfde. Wie de klok
   zet ziet dezelfde oranje rand als wie een merkbare instelling schrijft; wie
   iets onomkeerbaars doet ziet rood en typt de naam van de node over.

4. **Eén formuliervorm.** Elk formulier heeft een label links, het veld met zijn
   hulptekst in het midden en de knop rechts, op één stramien voor de hele
   beheersite. Op een smal scherm klapt dat naar onder elkaar. Veldnamen,
   routes en verborgen velden blijven wat ze waren; alleen de omhulling
   verandert.

## Vindbaarheid: het geval van de gebruikers

Het concrete bewijs dat de indeling tekortschoot: de eigenaar van deze
installatie — serverbeheerder — kon niet vinden waar hij een tweede beheerder
aanmaakt. Dat zat op `/admin/server`, sectie `#gebruikers`, achter een menu-item
dat *Server en site* heette, ná de tabel met bestaande accounts, als drie
losse placeholders op één regel. En het menu had wél een tab *Beheerders* — die
over iets anders ging: welke node welke andere node uitvraagt (monitors).

Twee dingen zaten fout, en ze worden allebei rechtgezet:

1. **De namen in het menu zeiden niet wat erachter zat.** *Beheerders* heet nu
   *Monitors*, wat het is. De tab *Server en site* heet *Server, gebruikers en
   site*, en zodra je erop staat verschijnt een tweede balk met de secties van
   die pagina — dezelfde vorm als de sub-balk van Companions. Gebruikersbeheer is
   daarmee vanaf elke beheerpagina in twee klikken te vinden zonder te scrollen.
2. **Het aanmaakformulier was geen formulier.** Het wordt een `.frm` met een
   label per veld, de ondergrens van acht tekens als hulptekst bij het
   wachtwoord, en bij het vinkje *serverbeheerder* staat wat die rol mag — en,
   in dezelfde adem, wat een gewone gebruiker mag zolang niemand hem een rol op
   een node of nodegroep geeft: niets. Die zin stond er al, in een grijze alinea
   die niemand las op het moment dat hij een account aanmaakte; nu staat hij
   naast het vinkje.

Wat blijft: de routes (`POST /admin/users`, `/admin/users/{id}/flags`,
`/password`, `/delete`), de veldnamen (`username`, `password`, `is_superuser`,
`csrf`) en de rechtenpoorten (`server.gebruikers`, `server.instellingen`). Ook de
regel dat de laatste actieve serverbeheerder zichzelf niet kan degraderen,
uitzetten of verwijderen blijft zichtbaar bij de tabel: dat is geen voetnoot maar
de reden dat die knoppen soms uit staan.

## De componenten

Alle klassen staan in `app/static/style.css`, onder *admin: componenten*.
Ze gebruiken uitsluitend de bestaande kleurvariabelen, dus het lichte thema
volgt vanzelf.

### Sectienavigatie — `.secnav`

Een rij ankers bovenaan een lange pagina, één per `<section id=…>`. Plakt bovenaan
tijdens het scrollen (`position: sticky`) en werkt zonder JavaScript: het zijn
gewone `href="#…"`-links. Op een smal scherm schuift de rij horizontaal in plaats
van om te klappen naar vijf regels. Een klein script markeert de sectie die in
beeld is; zonder dat script is de rij nog steeds een inhoudsopgave.

### Kaart — `.card`, met `.card-head`

Bestond al. De kop krijgt een vaste vorm: titel links, badges (risico, toestand)
rechts ernaast, en een optionele regel `.card-desc` in één zin. De alinea's die
er eerder onder stonden gaan naar een `.uitleg`.

### Feitenlijst — `.kv`

De tweekolomstabel *sleutel — waarde* die op veel pagina's staat (identiteit,
klok, opslag, invoer). Eén klasse zodat ze overal dezelfde kolombreedte en
regelhoogte hebben, en op een smal scherm de sleutel boven de waarde komt in
plaats van ernaast in een kolom van vier letters breed.

### Formulierrij — `.frm`

```html
<form class="frm" method="post" action="…">
  <input type="hidden" name="csrf" value="…">
  <label class="frm-label" for="x">Naam</label>
  <div class="frm-field">
    <input id="x" name="name" …>
    <p class="frm-hint">Wat dit veld doet, in één zin.</p>
  </div>
  <div class="frm-actions"><button type="submit">Opslaan</button></div>
</form>
```

Een `grid` met drie kolommen: label (vaste breedte), veld (rekt), knoppen (op
inhoud). Meerdere velden in één rij (regio: veld + naam + bevestiging) staan
samen in `.frm-field` als `.frm-row`. Onder 640 px worden de kolommen rijen.
Een `.frm` zonder label (alleen een knop met uitleg) laat de eerste kolom leeg
met `.frm--noplabel`.

De oude `rowform` blijft bestaan voor de plekken waar hij past — een rij losse
knoppen, of formulieren *in* een tabelcel — maar hij is niet meer de standaard.

### Handeling — `.act` met risicoklasse

Bestond al als `.act--read/--write/--danger`. Er komen drie klassen bij die op de
risicoklassen van de code liggen, zodat het woord in de code en de kleur op het
scherm dezelfde zijn:

| Klasse | Code | Kleur | Bevestiging |
|---|---|---|---|
| `.risk-gewoon` | `RISK_PLAIN`, rol *bediener* | blauw (cyan) | geen, of "kost zendtijd" als etiket |
| `.risk-merkbaar` | `RISK_WRITES`, rol *technicus* | oranje (amber) | vinkje of "ja" |
| `.risk-ingrijpend` | `RISK_CUTOFF`, rol *beheerder* | rood | de naam van de node overtypen |

De bestaande `--read`/`--write`/`--danger` blijven als synoniemen werken. Het
etiket (`.act-tag`) noemt de prijs in woorden: *kost zendtijd*, *schrijft op het
apparaat*, *onomkeerbaar*. Kleur is nooit de enige drager.

### Uitleg — `.uitleg`

`<details class="uitleg"><summary>Waarom dit zo werkt</summary> … </details>`.
Voor elke toelichting van meer dan twee zinnen die niet nodig is om de
handeling veilig uit te voeren. Wat wél nodig is om veilig te handelen — "dit
loopt over WiFi en valt weg met de WiFi", "een klok kan alleen vooruit" — blijft
zichtbaar, als `.frm-hint` of als waarschuwing (`.warn`).

### Tabellen — `.tablewrap` en `.stack`

Elke brede tabel staat in een `.tablewrap` die zijwaarts schuift binnen zijn
kaart, zodat de pagina zelf nooit zijwaarts scrolt. Tabellen met een formulier
per rij (`.cfgtable`, de filterregels) krijgen daarbovenop `.stack`: onder
640 px wordt elke rij een kaartje met de kolomkop ervoor (`data-l` op de cel).
Zo blijft "Parameter · Nu · Nieuwe waarde" leesbaar op 375 px zonder dat de
lezer op de gok in een cel tikt.

### Knoppen

Drie soorten, altijd dezelfde plek: rechts in de formulierrij, of rechts onder
in een `.act`.

| Klasse | Gebruik |
|---|---|
| `button` (standaard) | de primaire handeling van een formulier |
| `button.secondary` | een tweede keuze ernaast (annuleren, verversen, lijst opnieuw ophalen) |
| `button.danger` | alleen in een `.risk-ingrijpend`-blok, en alleen met een bevestiging |

De pillen (`.pill.on/.off`) blijven de aan/uit-schakelaars: één klik, omkeerbaar,
geen bevestiging.

## Per pagina

### `node.html` — één node

De grootste verandering. De pagina krijgt een `.secnav` en wordt in negen
secties verdeeld, in oplopende onomkeerbaarheid en met de zeldzame dingen
achteraan:

| Anker | Sectie | Wat erin staat |
|---|---|---|
| `#overzicht` | Overzicht | niveau, identiteit en versies, naam |
| `#zichtbaarheid` | Zichtbaarheid | de vier schakelaars, elk met één zin; de rest in een uitleg |
| `#uitvragen` | Uitvragen | de bewaarde parameters, de twee opvraagknoppen, het uitvraagschema |
| `#instellingen` | Instellingen schrijven | de drie risicogroepen als tabellen met risicoklasse |
| `#pakketfilter` | Pakketfilter | wat de node meldt, en de schrijfweg die er is |
| `#klok` | Klok | toestand en de synchronisatieknop |
| `#eigen-api` | Beheer over IP | adres, toestand, handelingen; rooms, sensor-nodes, SNMP en bot elk als uitklapper |
| `#kanalen` | Kanalen | de namen bij de kanalen (alleen bij een sensornode) |
| `#meldingen` | Meldingen | alarmen en gebeurtenis-push |
| `#ingrijpend` | Firmware en verwijderen | ingeklapt; rood |
| `#trail` | Wat er gebeurd is | ingeklapt |

De statusmeldingen van een net uitgevoerde handeling blijven bovenaan, want
daar kijkt de pagina na een klik naartoe.

### `server.html` — Server en site

Zelfde `.secnav`. De instellingenformulieren (`settingsgrid`) worden `.frm`-rijen
met per veld zijn grens als hulptekst. De gebruikerstabel krijgt een
`.tablewrap`; het wachtwoord-zetten en verwijderen per gebruiker blijven in de
rij, met de gevaarlijke knop als laatste kolom.

### `nodes.html` — de lijst

Was al kaartgebaseerd en goed op een smal scherm. De drie waarschuwingsblokken
(`.pending`) worden één lijst *Aandacht* bovenaan, zodat drie gele blokken niet
lezen als drie storingen.

### `firmware.html`, `monitors.html`, `discovery.html`, `compare.html`

Formulieren naar `.frm`; de upgradeknop in een `.risk-ingrijpend`-blok; de
tabellen in `.tablewrap`.

### `companions.html`, `companion.html`, `senddm.html`

De commandoraster (`.cardgrid`) blijft: veel kleine handelingen naast elkaar is
hier de juiste vorm. De radioparameter-kaart wordt een `.risk-ingrijpend`-blok
in plaats van een kaart met een losse rode rand in `style=`. Het beheerblok
onderaan (bewerken, deel-link, HA, verwijderen) krijgt `.frm`-rijen en het
verwijderen komt als laatste, rood.

### `account.html`, `audit.html`, `login.html`

Kleine ingrepen: `.frm` voor het wachtwoordformulier, `.tablewrap` voor de
tabellen, `.kv` waar het past.

## Wat bewust niet verandert

- **Geen veldnaam, route, `csrf`- of `confirm`-veld** verandert. Elk formulier
  POST precies wat het POSTte; alleen de omhulling is anders. De tests op de
  letterlijke teksten (`test_nodeconfig.py`, `test_pktfilter.py`,
  `test_beheerpaginas_renderen.py`, `test_rooms.py`) zijn daar de bewaker van.
- **Geen JavaScript dat draagt.** De sectienavigatie, de uitklappers en de
  formulieren werken zonder script. Het script dat de actieve sectie markeert
  is versiering.
- **Geen nieuwe i18n-sleutels.** Beheer is eentalig Nederlands; zie de kop van
  `admin/_layout.html`.
- **Geen externe fonts of bibliotheken.** Alles in `style.css`, met de
  variabelen die er al stonden.
- **De teksten.** Ingekort waar ze twee keer hetzelfde zeiden, verplaatst naar
  een uitklapper waar ze lang zijn, maar niet weggegooid. Een zin die zegt
  waarom een klok niet terug kan, is geen versiering.
