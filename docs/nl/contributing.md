# Bijdragen en werkwijzeconventies

*[English](../contributing.md)*

Dit is geen stijlgids. Het is de verzameling beslissingen die verklaart waarom de
code in deze repo eruitziet zoals hij eruitziet — waarom er geen buildstap is,
waarom het commentaar langer is dan gebruikelijk, waarom sommige functies
weigeren te antwoorden, en waarom de commitboodschappen in het Nederlands zijn.

Lees het vóór een eerste wijziging. Het meeste van wat volgt lijkt extra werk, en
elke regel staat er omdat zijn afwezigheid iets gekost heeft.

---

## Inhoud

- [De korte versie](#de-korte-versie)
- [1. Eerlijkheid over onzekerheid](#1-eerlijkheid-over-onzekerheid)
- [2. Commentaar draagt het waarom](#2-commentaar-draagt-het-waarom)
- [3. Commitboodschappen](#3-commitboodschappen)
- [4. Taal](#4-taal)
- [5. Vanilla JavaScript, geen buildstap](#5-vanilla-javascript-geen-buildstap)
- [6. Additieve SQLite-migraties](#6-additieve-sqlite-migraties)
- [7. `packets.raw` is de grondwaarheid](#7-packetsraw-is-de-grondwaarheid)
- [8. Tests leggen de weigeringen vast](#8-tests-leggen-de-weigeringen-vast)
- [9. Gereedschap, of het gebrek eraan](#9-gereedschap-of-het-gebrek-eraan)
- [10. Documentatieconventies](#10-documentatieconventies)
- [Een wijziging indienen](#een-wijziging-indienen)

---

## De korte versie

| Regel | In één zin |
|---|---|
| Nooit gokken in het openbaar | Als de data twee antwoorden niet scheidt, zeg dat, in plaats van er één te kiezen |
| Nooit stil falen | Een verloren verzoek, een genegeerde clausule, een overgeslagen uitsluiting: het wordt allemaal hardop gezegd |
| Commentaar legt het waarom uit | Wát de code doet is leesbaar; waarom hij dat doet en niet het voor de hand liggende alternatief, niet |
| Commits zijn Nederlands en dragen de redenering | Het onderwerp is de zichtbare verandering; de body is het onderzoek |
| Geen buildstap | Vanilla ES5, Jinja2-templates, bibliotheken van een CDN |
| Migraties zijn additief | `CREATE TABLE IF NOT EXISTS` plus een bewaakte `ADD COLUMN`. Er wordt nooit iets weggegooid |
| `raw` is de waarheid | Afgeleide kolommen zijn een cache; het frame is het archiefstuk |
| Noem je bron | Firmwaregedrag krijgt een bestand en een regelnummer in het commentaar |

---

## 1. Eerlijkheid over onzekerheid

Dit is de kernregel van het project, en hij wordt in code afgedwongen in plaats
van verondersteld. Elke andere conventie hier volgt eruit.

Het mesh geeft geen eenduidige data. Een hop-ingang is één byte — 256 mogelijke
waarden over honderden nodes. Een afgekapt frame kan een bug zijn of een echt
radio-artefact. De regio van een pakket valt niet uit zijn bytes terug te halen.
De verleiding is in elk van die gevallen het waarschijnlijkste antwoord te kiezen
en af te drukken. Dit project doet dat niet.

### De vier toestanden

`server/app/candidates.py` is de referentie-implementatie. `weigh()` geeft één
van vier toestanden terug, en de moduledocstring benoemt de derde als iets wat
hij *weigert* samen te vouwen:

| Toestand | Betekenis | Weergave |
|---|---|---|
| `known` | Precies één kandidaat | De node, bij naam |
| `likely` | Een gerangschikte koploper, met reden | Bij naam **in woorden**, met de reden erbij |
| `ambiguous` | Meerdere kandidaten die het bewijs niet scheidt | Allemaal, geen voorkeur |
| `unknown` | Niets kwam overeen | De ruwe byte, als `0xNN` |

> Een winnaar aanwijzen terwijl het bewijs de bovenste twee niet scheidt. […] Een
> muntje opgooien en de uitkomst als "meest waarschijnlijk" afdrukken is het ene
> ding dat dit project niet doet.
>
> — `server/app/candidates.py`, moduledocstring

Het geval `unknown` is leerzaam. Een vroege versie drukte alleen het woord
"onbekend" af, en gooide daarmee het ene feit weg dát het frame wél gaf: de byte.
Nu staat er `0xNN`. Niet weten welke node een hash aanduidt is iets anders dan de
hash niet weten.

### Banden, geen score

`weigh()` vergelijkt kandidaten in grove banden in een vaste volgorde, in plaats
van ze op te tellen tot een getal. De docstring zegt waarom: een gewogen score
met decimalen scheidt *elk* paar kandidaten, inclusief de paren die het bewijs
niet werkelijk scheidt. Precisie die de invoer niet draagt is een leugen met een
komma erin.

Ontbrekende waarden krijgen hun eigen band, voorbij de laatste — *niet weten*
waar iets is, is een slechtere reden om het bovenaan te zetten dan *weten* dat
het ver weg is.

### Waar een rangschikking wel en niet gebruikt mag worden

`routes_api.py` trekt de grens expliciet. Een `likely`-hop wordt in woorden
genoemd, naast zijn reden. Hij wordt **niet** op de kaart getekend:

> Een rangschikking is goed genoeg om een waarschijnlijke node in woorden te
> noemen naast de reden waarom hij waarschijnlijk is; hij is niet goed genoeg om
> een lijn op een kaart te trekken, waar de reden niet meereist.
>
> — `_hop_waypoint()`, `server/app/routes_api.py`

Een hop met meerdere kandidaten krijgt op elk van hen een holle ring in plaats
van een lijn (`server/app/static/app.js`). De mogelijkheden tonen is eerlijk; er
één kiezen niet.

### Weigeren te decoderen

`server/app/packets.py` stopt bij het eerste wat hij niet kan vertrouwen en meldt
wat daarvóór zeker was, met een `error`-veld. Hij loopt niet door voorbij een
foute waarde, want een verkeerde hashgrootte verschuift elke byte na de
descriptor — doorgaan betekent in één keer een pad, een payloadgrens én een
adreshash verzinnen. Een padlengte gelezen uit een byte die we niet vertrouwen
is, in de woorden van de module zelf, *een gok in de kleren van een getal*.

Verwante weigeringen in hetzelfde bestand: een afgekapt transportcodeveld laat
`scope` weg in plaats van te kiezen tussen `scoped` en `share`; de regio van een
scoped pakket wordt nooit geraden; een firmwarestandaardpositie van 0/0 geldt als
onbekend in plaats van uitgezet te worden in de Atlantische Oceaan.

`decode()` gooit nooit. Een kapot pakket is **data, geen bug**.

### Weigeren te negeren

De spiegelregel, in `server/app/search.py`:

> Er valt nooit stilletjes iets weg. Een onbekende veldnaam, een vergelijking op
> een tekstkolom, een misvormd bereik: elk daarvan is een fout die de pagina
> toont, nooit een clausule die stil overgeslagen wordt. Een zoekopdracht die de
> helft van wat je vroeg negeert en tegelijk een zelfverzekerd aantal treffers
> meldt, is erger dan een die weigert te draaien.

Dezelfde reflex buiten de server: de Home Assistant-integratie logt een luide
waarschuwing als hij een opgehaald commando niet kan uitvoeren, omdat de wachtrij
op de site clear-on-read is en een stil verloren verzoek nergens meer bestaat
([`homeassistant.md`](homeassistant.md#luid-falen-is-bewust)). De heatmap zet een
`capped`-vlag in plaats van een afgekapte week als een volledige te tonen.
Uitgesloten kandidaten worden geteld en toegelicht, nooit zomaar weggelaten.

### Wat dit van een bijdrager vraagt

Vraag je vóór het toevoegen van een waarde aan een pagina af wat er gebeurt als
de invoer die waarde niet draagt. Is het antwoord "we tonen de
waarschijnlijkste", dan is de wijziging niet klaar. De opties zijn: toon ze
allemaal, toon het in woorden met de reden erbij, of toon het ruwe feit en zeg
wat onbekend is.

---

## 2. Commentaar draagt het waarom

De commentaardichtheid in deze repo ligt tussen 13 en 24 % van de regels, en
moduledocstrings lopen op plaatsen door tot over de honderd regels
(`server/app/packets.py` opent met 154 regels vóór de eerste import). Dat is met
opzet.

De regel is niet "becommentarieer alles". Hij is:

- **Wat de code doet heeft geen commentaar nodig.** Dat is leesbaar.
- **Waarom hij dat doet en niet het voor de hand liggende alternatief, wel.**
  Zeker als dat alternatief eerst geprobeerd is.
- **Een constante met een getal erin krijgt haar redenering.**
  `server/app/config.py` legt niet alleen vast wat een bewaartermijn is, maar
  waar hij een belofte over is, en wat er gebeurt als die belofte met de
  werkelijkheid botst.
- **Een index krijgt een rechtvaardiging, en de zusterindex die er níet is
  ook.**
- **Firmwaregedrag krijgt een citaat**: bestand en regelnummer in de
  MeshCore-boom, zodat een latere lezer het tegen zijn eigen versie kan
  hercontroleren. `packets.py` citeert `src/Dispatcher.cpp` en `src/Mesh.cpp` op
  regel; meerdere modules verwijzen naar `docs/protocol.md` per paragraaf.

De toets of een commentaar het schrijven waard is: zou iemand die dit over een
jaar leest zich afvragen waarom het niet eenvoudiger gedaan is? Zo ja, dan hoort
het antwoord in het bestand — niet in de commithistorie en niet in iemands hoofd.

Commentaar dat een **terugdraaiing** vastlegt is het waardevolst. Drie releases
van de TCP-proxy bestaan uitsluitend om slimmigheid uit eerdere releases ongedaan
te maken ([`proxy.md`](proxy.md#versiegeschiedenis-in-het-kort)); de code zegt
dat, en daarom heeft niemand ze opnieuw toegevoegd.

---

## 3. Commitboodschappen

Commits zijn in het **Nederlands**, en ze zijn geschreven voor een mens, niet
voor een changeloggenerator.

**Onderwerpregel**: een zin die de zichtbare verandering beschrijft, niet het
mechanisme. Echte voorbeelden uit `git log`:

```
Een afzender die we niet kunnen benoemen krijgt zijn byte terug
De heatmap laat nu zien welke schakels het mesh dragen
Adreshashes: kandidaten wegen op bewijs in plaats van alles opsommen
Een nodenaam met een aanhalingsteken laat een node niet meer verdwijnen
```

Niet `fix: candidate resolution` en niet `refactor packets.py`. Wat er veranderde
voor wie het ding gebruikt.

**Body**: het onderzoek. Wat er waargenomen werd, wat de oorzaak bleek, waarom
deze oplossing en niet een andere. Een bugfix-commit opent doorgaans met het
symptoom zoals de gebruiker het zag — inclusief de misleidende delen, zoals een
teller die een gezond getal meldde terwijl de kaart leeg bleef — dan de stack
trace of de meting, dan het mechanisme, dan de beslissing.

De body is waar de redenering heen gaat die niet in een commentaar paste. Tussen
die twee hoort geen enkele beslissing in dit project onverklaard te blijven.

---

## 4. Taal

De verdeling is systematisch. Ze is niet netjes, en het is goed dat te weten
voordat je je op de verkeerde buurman spiegelt.

| Waar | Taal |
|---|---|
| Commitboodschappen | **Nederlands** |
| Gebruikersteksten (fouten, labels, paginatekst) | **Nederlands** — Engels komt via `i18n.js` |
| Codecommentaar en docstrings in `server/app/` | **Engels** |
| Het beheer-, commando- en kloksubsysteem (`routes_admin.py`, `commanding.py`, `clocksync.py`, `retention.py`) | **Nederlands** commentaar |
| `homeassistant/`, `proxy/`, `mosquitto/`, `deploy/` | **Nederlands** commentaar |
| Tests, inclusief testfunctienamen | **Nederlands** |
| `docs/` | **Beide**, gespiegeld — zie [§10](#10-documentatieconventies) |

Spiegel je op het bestand dat je bewerkt. Vertaal een bestaand bestand niet als
bijvangst van een andere wijziging.

---

## 5. Vanilla JavaScript, geen buildstap

Er is nergens in deze repo een `package.json`, geen `node_modules`, en geen
bundler. `server/app/static/app.js` is 3500 regels ES5 in één IIFE met `"use
strict"`: `var`, geen arrow functions, geen classes, geen modules.

De geen-buildstapregel staat in de code die er anders een nodig had gehad.
`server/app/main.py`, over cache headers:

> Bestandsnamen met een hash erin zijn afgewezen: die vragen een buildstap, en
> deze site heeft er bewust geen.

Cachebusting gebeurt in plaats daarvan met een queryparameter die bij de start
gezet wordt: `templating.py` zet `asset_v` in de Jinja-globals, en templates
vragen `/static/app.js?v={{ asset_v }}`.

Wat je daarmee overhoudt:

| In plaats van | Gebruik |
|---|---|
| Componenten | Jinja2-partials, gevuld door één functie. `_packet_detail.html` wordt door zowel de live pagina als het archief ingevoegd en door `fillPacketDetail` gevuld |
| Een virtuele DOM | `createElement` / `textContent`. `innerHTML` komt twee keer voor in 3500 regels, en dat is bewust — zie hieronder |
| Een state store | `window.MCS`, een inline `{{ ... \| tojson }}`-overdracht vanaf de server |
| npm-pakketten | `<script>`-tags naar een CDN. Leaflet 1.9.4 en Chart.js 4.4.9 zijn de enige twee |
| De escaping van een framework | `textContent`, 70 keer tegenover 2 keer `innerHTML` |

**Templates worden op de server gerenderd.** Jinja2 via FastAPI, `{% block %}`-
overerving vanuit `base.html`. De pagina werkt voordat er ook maar één regel
JavaScript gedraaid heeft.

### i18n

`server/app/static/i18n.js` is volledig client-side: geen sessies, geen URL's per
taal, geen bemoeienis van de server.

- Templates renderen **Nederlands als de letterlijke tekst** en labelen knopen
  met `data-i18n`. De pagina is daarmee leesbaar en indexeerbaar zonder
  JavaScript.
- `apply(root)` loopt vier attributen af: `data-i18n` → `textContent`,
  `data-i18n-title` → `title`, `data-i18n-ph` → `placeholder`, `data-i18n-aria`
  → `aria-label`. Interpolatiewaarden reizen mee in `data-i18n-vars`.
- De keuze staat in `localStorage`, met een vangnet, want `localStorage` kan
  geblokkeerd zijn.
- Een ontbrekende sleutel valt terug op de Nederlandse formulering, nooit op een
  ruwe sleutel op het scherm.
- `i18n.js` laadt **vóór** elk paginascript, zodat ook gegenereerde tekst
  `MCSI18N.t` kan gebruiken. **Elke string die `app.js` bouwt moet erdoorheen.**

Een UI-string toevoegen betekent hem aan zowel het `nl`- als het `en`-blok van
`DICT` toevoegen. Een string die maar in één taal bestaat is een bug, geen
gedeeltelijke vertaling.

---

## 6. Additieve SQLite-migraties

Gewone `sqlite3` met een verbinding op moduleniveau en een mutex. Geen ORM: het
werk is een handvol kleine schrijfacties per minuut plus paginaleesacties, en een
ORM zou alleen een afhankelijkheid en een migratieverhaal toevoegen die dit
project niet nodig heeft.

Twee mechanismen, allebei idempotent, allebei bij elke processtart:

**Nieuwe tabellen en indexen** — de `SCHEMA`-string in `server/app/db.py` is één
script van `CREATE TABLE IF NOT EXISTS` / `CREATE INDEX IF NOT EXISTS`, uitgevoerd
bij het verbinden.

**Nieuwe kolommen** — SQLite kent geen `ADD COLUMN IF NOT EXISTS`, dus bestaande
tabellen hebben een expliciete controle nodig:

```python
def _migrate(conn: sqlite3.Connection) -> None:
    for table, column, decl in COLUMN_MIGRATIONS:
        names = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}
        if column not in names:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")
```

`COLUMN_MIGRATIONS` is een lijst van `(tabel, kolom, declaratie)`-drietallen, en
**elke ingang draagt een commentaar dat uitlegt waarom die kolom bestaat**. Dat
commentaar is niet optioneel; het is de enige vastlegging van de beslissing.

Volgorde bij het starten, in `get_conn()`: verbinden → `journal_mode=WAL` →
`foreign_keys=ON` → `SCHEMA` → `_migrate()` → `_backfill_from_raw()` → commit.

### De regels

- **Er wordt nooit iets weggegooid.** Er staat nergens in `server/app/` een
  `DROP TABLE` of `DROP COLUMN`. `DELETE FROM` bestaat alleen voor het opruimen
  van rijen. Een draaiende database weggooien is geen optie, en een migratie die
  niet twee keer kan draaien evenmin.
- **Er is geen versietabel**, geen `PRAGMA user_version`, geen genummerde
  migratiebestanden en geen down-migraties. Additieve wijzigingen hebben ze niet
  nodig; wat ze wél nodig heeft, is een wijziging die heroverwogen moet worden.
- **Een nieuwe kolom begint als NULL voor oude rijen.** Is hij te herberekenen,
  neem hem dan op in de backfill (zie de volgende paragraaf). Is dat niet zo, dan
  is NULL de eerlijke waarde en moet de UI hem als zodanig kunnen tonen.

---

## 7. `packets.raw` is de grondwaarheid

`packets.raw` bewaart het frame precies zoals het van de radio kwam, in hex. Elke
andere kolom in die tabel is er een lossy samenvatting van.

De leer, uit het commentaar bij de migratie-ingang zelf:

> Het is het enige volledige archiefstuk van een pakket — al het andere in deze
> tabel is een lossy samenvatting — en het is wat een latere lezer toestaat een
> pakket te herlezen dat de decoder van toen verkeerd las.

Het verdubbelt de omvang van een pakketrij ongeveer, en dat is alleen te betalen
omdat pakketten hun eigen korte bewaartermijn hebben.

Drie gevolgen die bepalen hoe functies gebouwd worden:

1. **De detailweergave decodeert opnieuw, op verzoek.** `packet_detail()` in
   `routes_api.py` draait de *huidige* decoder over de bewaarde bytes in plaats
   van advertvelden uit kolommen te lezen. Een decoderfix verbetert daarmee
   meteen oude pakketten, niet alleen nieuwe.
2. **Eerst het frame, de kolom als terugval.** Waar allebei bestaan wint het
   gedecodeerde frame, en antwoordt de bewaarde kolom alleen voor rijen waarvan
   de bytes nooit bewaard zijn. `path_hash_size` is `None` voor die rijen —
   *"None is dat antwoord, en niet een plausibel ogende 1"*.
3. **Nieuwe afgeleide kolommen worden ingevuld.** `_backfill_from_raw()` leest
   bewaarde frames opnieuw door dezelfde decoder waar nieuwe pakketten doorheen
   gaan. Rijen van vóór de `raw`-kolom houden hun NULLs voor altijd, en dat is
   het eerlijke antwoord voor een pakket waarvan niemand de bytes bewaard heeft.

Denormalisatie is toegestaan als expliciete, beargumenteerde uitzondering —
`path` en `scope` zijn kolommen omdat de detailweergave elke hop oplost en frames
daarvoor opnieuw decoderen werk herhaalt dat de ingest al deed. De
rechtvaardiging staat in het commentaar bij de migratie-ingang. "Omdat het
sneller is" zonder meting is er geen.

Kosten tellen hier: `raw` wordt bewust weggelaten uit de lijst- en
zoek-endpoints. Hij blijft op het detail-endpoint, voor het ene pakket dat iemand
werkelijk geopend heeft.

---

## 8. Tests leggen de weigeringen vast

Volledige uitwerking in [`testing.md`](testing.md). De conventie die hier hoort:
tests in dit project zijn overwegend uitspraken over **wat het systeem weigert te
beweren**, en dat volgt rechtstreeks uit
[§1](#1-eerlijkheid-over-onzekerheid).

Testnamen lezen als zinnen die dat zeggen — *"zonder adjtimex wordt er niets
beweerd"*, *"een klok die ver achteruit sprong wordt geweigerd"*, *"backfill
herstelt geleegde kolommen"*. `test_candidates.py` beschrijft zichzelf in zijn
docstring als een antwoord niet op "welke node is het" maar op "wanneer mogen we
dat zeggen".

Twee harde regels:

- **Geen opgevangen pakketten.** Elke testvector is met de hand gebouwd uit
  [`protocol.md` §1](protocol.md#1-the-over-the-air-packet-format) door
  `server/tests/frames.py`. Een falende test hoort je naar de specificatie te
  sturen, niet naar een binair bestand dat niemand kan lezen.
- **Geen netwerk, geen MQTT, geen echte database.** `conftest.py` wijst de
  datamap om vóór er iets `app` importeert, zodat een testrun nooit
  `server/data/` in je werkkopie aanmaakt.

Een functie toevoegen die op een interessante manier fout kan zijn, betekent de
test toevoegen die zegt hoe hij fout mag zijn.

---

## 9. Gereedschap, of het gebrek eraan

| Ding | Status |
|---|---|
| Linter- of formatterconfiguratie | **Geen.** Geen `.flake8`, `ruff.toml`, `pyproject.toml`, `.eslintrc`, `.editorconfig` |
| Typechecker | **Draait niet.** Annotaties zijn documentatie |
| CI | **Niet ingecheckt** |
| Python | **3.12** (vastgelegd door `server/Dockerfile`); syntaxisondergrens 3.10 |
| Runtime-afhankelijkheden | 5, in `server/requirements.txt`, met ondergrenzen, zonder lockfile |
| Ontwikkelafhankelijkheden | `server/requirements-dev.txt` — de runtimeset plus `pytest` |
| Packaging | Geen. De app wordt gedraaid, niet geïnstalleerd: `uvicorn app.main:app` |

Typehints worden pragmatisch gebruikt: ongeveer zeven op de tien functies dragen
een returnannotatie, containers blijven vaak kaal (`-> dict`, `-> list[dict]`),
en er zijn **geen `from typing`-imports** — alleen ingebouwde generics en
PEP 604-unions. Runtimevalidatie is die van FastAPI, bijvoorbeeld
`Query(..., ge=1, le=2160)`.

De stijl wordt bij het nalezen bewaakt. Spiegel je op het bestand waar je in
zit.

---

## 10. Documentatieconventies

`docs/` is tweetalig en gespiegeld. De regels bestaan zodat een lezer in beide
talen bij dezelfde inhoud uitkomt, en zodat een scheefgegroeid paar opvalt.

| Regel | Detail |
|---|---|
| Engels staat in | `docs/<onderwerp>.md` |
| Nederlands staat in | `docs/nl/<onderwerp>.md` — **dezelfde bestandsnaam** |
| Koppen | Dezelfde structuur in beide, in dezelfde volgorde |
| Taalwissel | Bovenaan elk Engels bestand: `*[Nederlands](nl/<naam>.md)*`. Bovenaan elk Nederlands bestand: `*[English](../<naam>.md)*` |
| Vertaaldiepte | Het Nederlands is een volwaardige vertaling, geen samenvatting. Heeft een tabel acht rijen in het Engels, dan heeft ze er acht in het Nederlands |
| Indexingangen | Elk document staat in zowel `docs/README.md` als `docs/nl/README.md`, met één zin die zegt wat een lezer er vindt |

Ankers zijn het ene wat legitiem verschilt: een Nederlandse kop levert een
Nederlands anker. Links naar de andere taal (`protocol.md#...` vanuit een
Nederlands bestand) wijzen naar de eigen ankers van het Nederlandse bestand waar
het doel vertaald is, en naar het Engelse anker waar dat bewust niet zo is.

Stijl: zakelijk en concreet. Tabellen waar een tabel helpt. Verwijzingen naar
bestand en functie, zodat een bewering na te trekken valt. Komt gedrag uit de
MeshCore-firmware, citeer dan het bestand en de regel waar het vandaan komt.

**Een nieuw document is niet af tot beide helften bestaan en beide indexen het
vermelden.** Een bestand zonder tegenhanger is een fout in de documentatie, net
zoals een UI-string in maar één taal een fout in de site is.

---

## Een wijziging indienen

1. Werk op `main` tenzij er reden is dat niet te doen; dit is een klein project.
2. Draai de tests vanuit `server/`:
   ```bash
   pip install -r requirements-dev.txt
   python -m pytest
   ```
3. Houd de wijziging en haar uitleg bij elkaar: het commentaar voor het
   mechanisme, de commitbody voor het onderzoek.
4. Commit in het Nederlands, met het waarom in de body.
5. Documentatiewijziging? Beide talen, beide indexen.
6. Gedrag dat onzeker kan zijn? Zeg dat in de UI, en voeg de test toe die de
   weigering vastlegt.

### Niet committen

- Adressen, hostnames, wachtwoorden, tokens of andere infrastructuurdetails.
  **Deze repo is publiek.** `mosquitto/acl` staat in `.gitignore` omdat er
  accountnamen in staan, `.env` omdat er geheimen in staan. Voorbeelden gaan in
  `.example`-bestanden met plaatshouders.
- Echte opgevangen pakketten als testfixture. Bouw ze uit de specificatie.
- Een buildstap.

---

## Zie ook

| | |
|---|---|
| De testsuite in detail | [`testing.md`](testing.md) |
| Het vocabulaire dat deze documenten gebruiken | [`glossary.md`](glossary.md) |
| Hoe de onderdelen samenhangen | [`architecture.md`](architecture.md) |
| Wat beschermd is, en wat niet | [`security.md`](security.md) |
