# Een node benoemen uit één byte

*[English](../candidates.md)*

De bron van een pakket, zijn bestemming en elke hop in zijn pad worden benoemd
met de eerste byte of twee van een publieke sleutel. Eén byte heeft 256 waarden
terwijl deze site al honderden nodes kent, dus dat meerdere nodes op dezelfde
hash passen is het **normale geval, geen datafout** (zie
[`protocol.md`](../protocol.md#14-the-path-field) §1.4).

`server/app/candidates.py` beslist wat daarover eerlijk gezegd mag worden. Het is
een pure module: geen I/O, geen databankverbinding, een injecteerbare klok.

## Drie dingen die het doet, en één dat het weigert

**Uitsluiten.** Een kandidaat die het frame zelf buiten elk aannemelijk
radiobereik plaatst, valt af. Dit is de enige manier waarop een kandidaat
verdwijnt, het vraagt een grens die het frame werkelijk draagt, en de beller
krijgt de afgevallen kandidaten terug zodat de lezer verteld kan worden hoeveel
er wegvielen en op welke grond.

**Rangschikken.** De overblijvers worden op drie grove, benoembare signalen in
een vaste voorrang van beste naar slechtste geordend: op hoeveel hops deze
waarnemer de node werkelijk gehoord heeft, hoe ver hij ligt, en hoe recent hij
gezien is.

**De koploper benoemen — maar alleen als het bewijs hem scheidt.**

**Nooit:** een winnaar benoemen wanneer het bewijs de bovenste twee niet
scheidt. Een rangschikking die niet kan rangschikken wordt als gelijkspel gemeld
en de beller valt terug op "N mogelijk". Een munt opgooien en de uitkomst als
"meest waarschijnlijk" afdrukken is het ene dat dit project niet doet.

## De vier toestanden

`weigh()` geeft `{"state", "matches", "dropped", "lead"}` terug.

| `state` | Betekenis | Hoe de site het tekent |
|---|---|---|
| `known` | Eén kandidaat blijft staan — de enige match, of de laatste na een uitsluiting | Een naam, en een positie op de kaart |
| `likely` | Meerdere blijven staan, en het bewijs zet er een boven de rest; `lead` noemt het signaal dat dat deed | De naam in woorden, met zijn reden. **Geen lijn op de kaart** |
| `ambiguous` | Meerdere blijven staan en niets scheidt ze | "N mogelijk", alle opgesomd |
| `unknown` | Niets blijft staan: geen contact past, of alles wat paste is uitgesloten | Een gat |

`known` is nog steeds een afleiding uit één byte, en de formulering stelt het
nooit als een identiteit.

`likely` staat met opzet aan de **onzekere** kant van de lijn op de kaart. Een
rangschikking is goed genoeg om een waarschijnlijke node in woorden te noemen
naast de reden waarom hij waarschijnlijk is; ze is niet goed genoeg om een lijn
op een kaart te tekenen, waar die reden niet meereist.
`routes_api._hop_waypoint()` is waar die lijn getrokken wordt: coördinaten worden
uitgereikt voor `known` en voor niets anders.

## De grens: welke kant van een pakket zijn hopaantal begrenst

Alles draait om één asymmetrie die je makkelijk omdraait.

> Bij een **FLOOD** is het pad de **reeds afgelegde** route: elke doorstuurder
> voegde zijn eigen hash toe, dus het frame telt terug naar de oorsprong.
> Bij een **DIRECT** is het pad de **nog af te leggen** route: het frame telt
> vooruit naar de bestemming.

Een flood begrenst dus waar een pakket **vandaan kwam** en een direct waar het
**heen gaat**, en geen van beide begrenst het andere.
`radio_hop_bound(role, route, path_len, index)`:

| `role` | FLOOD | DIRECT | Redenering |
|---|---|---|---|
| `src` | `path_len + 1` | *(geen)* | De oorsprong ligt `path_len` doorstuurders terug, dus zoveel schakels plus één vanaf hier. Nul hops betekent dat we de eigen uitzending van de afzender hoorden: hij ligt binnen ons radiobereik, punt |
| `dest` | *(geen)* | `path_len + 2` | `path_len` doorstuurders te gaan en dan de bestemming, en de node die we net hoorden uitzenden ligt zelf één schakel van ons |
| `hop` op `index` | `path_len - index` | `index + 2` | Bij een flood kwamen er `path_len - 1 - index` doorstuurders na hem. Bij een direct moet hij nog doorsturen: `index + 1` schakels voorbij de node die we hoorden |

Let op wat het floodgeval **niet** dekt: de bestemming van een geflood pakket
mag overal in het mesh liggen, hoe weinig hops het ook afgelegd heeft. Een flood
met nul hops zegt waar het begon, niet waar het heen gaat. Daarom houdt het geval
waarvoor dit gebouwd is — de bestemming van een geflood pakket — al zijn
kandidaten en ordent het ze alleen maar.

`None` betekent "geen grens", en geen grens betekent **geen uitsluiting**. Dat is
de veilige kant: de rangschikking draait nog steeds.

Omdat de twee rollen omdraaien de onschuldigen zou uitsluiten, heeft `routes_api`
een aparte `_resolve_src()` en `_resolve_dest()` in plaats van één aanroep met de
rol van buitenaf meegegeven. Geen enkele beller mag kiezen.

## Uitsluiting, en de meting die haar overstemt

```python
if (bound and km is not None and km > bound * MAX_RADIO_HOP_KM
        and (hops is None or hops > bound)):
    dropped.append({**entry, "why": "range", "bound": bound})
```

`MAX_RADIO_HOP_KM` is **120 km**, en het is een verwerpingsdrempel en geen model
van dekking — ver boven alles wat een normaal mesh oplevert gezet in plaats van
in het midden van de verdeling. Op de installatie waartegen dit gemeten is, lag
de verste node ooit op nul hops gehoord 24 km weg en de verste op één hop 51 km.
120 km laat een factor vijf ruimte boven de eerste en sluit nog steeds de
buurlanden uit die als kandidaat opdoken voor lokaal gehoord verkeer.

Terrestrische LoRa reikt over water of vanaf een heuveltop wél verder, en juist
daarom vervalt de uitsluiting **voor elke node die deze waarnemer werkelijk op
dat hopaantal gehoord heeft**. Dekking die de drempel verslaat, is een feit over
de wereld; de drempel is slechts een plaatsvervanger daarvoor, en een meting wint
altijd.

Afgevallen kandidaten reizen met `why` en `bound` terug naar de beller, en de API
geeft een telling door (`dropped_total`), zodat een regel kan zeggen hoeveel er
wegvielen in plaats van een versmalde lijst als de hele waarheid te presenteren.

## Rangschikken: banden, in een vaste volgorde

De sorteersleutel is een viertal, van links naar rechts vergeleken:

```python
(_heard_tier(hops), _band(km, DISTANCE_BANDS_KM), _age_band(seen, now),
 (name or prefix).lower())
```

### Hoplagen

| Laag | Voorwaarde |
|---|---|
| 0 | Door deze waarnemer gehoord op minder dan `HEARD_TIER_LOCAL` (2) hops |
| 1 | Gehoord op minder dan `HEARD_TIER_NEARBY` (4) hops |
| 2 | Gehoord, maar verder weg |
| 3 | `TIER_UNHEARD` — nooit door deze waarnemer gehoord |

Nul en één hop vormen met opzet één laag: een node waarvan de advert ons
rechtstreeks bereikte en een waarvan de advert via één repeater kwam, zitten
allebei "in deze hoek van het mesh", en ze splitsen zou rangschikken op het
toeval welke kopie van een geflood advert het eerst aankwam.

### Afstand en recentheid

`DISTANCE_BANDS_KM = (25, 75, 200)` en `RECENCY_BANDS_S` = vandaag, deze week,
deze maand, ouder. Brede banden, want het doel is "in de buurt" van "in een ander
land" te scheiden, niet twee nodes op 30 en 34 km te rangschikken.

`_band()` geeft een ontbrekende waarde een eigen band **één voorbij de laatste**,
zodat een niet-geplaatste of nooit-geziene node onder elke node valt die we wél
kunnen plaatsen of dateren. Niet weten waar iets is, is een slechtere reden om
het vooraan te zetten dan weten dat het ver weg is.

### De waarnemer is geen kandidaat die overgehoord moet zijn

```python
if observer6 and prefix[:6] == observer6:
    hops, km = 0, 0.0
```

Hij is de node die luistert. Nul hops en nul afstand zijn feiten over hem, en de
bewijstabel zou dat alleen zeggen door het toeval dat een van zijn eigen adverts
via het mesh terugkwam.

### Waarom banden en geen score

Een gewogen score met decimalen zou elk paar kandidaten scheiden — ook de paren
die het bewijs niet werkelijk scheidt. En juist het gelijkspel is hier het
interessante geval, want dat is het geval waarin het eerlijke antwoord nog steeds
"meerdere mogelijk" is.

Drie in banden ingedeelde signalen in een vaste volgorde kan je bovendien
hardop lezen: *dichterbij gehoord, dan dichterbij, dan recenter.* Een formule kan
niemand navertellen.

### Waar de koploper vandaan komt

```python
top, runner = scored[0][0], scored[1][0]
lead = next((name for i, name in enumerate(LEAD_SIGNALS)
             if top[i] != runner[i]), None)
state = "likely" if lead else "ambiguous"
```

De lijst is gesorteerd, dus er staat altijd iets vooraan — maar als de bovenste
twee op geen van de drie signalen verschillen, is het enige wat ze scheidt de
alfabetische gelijkspelbreker in de sortering. Dat is geen bewijs en mag niet als
rangschikking verkleed worden. `LEAD_SIGNALS` is `("hops", "distance",
"recency")` en die namen zijn de woordenschat die de voorkant vertaalt; houd ze
gelijk met de i18n-sleutels.

## Wat de signalen waard zijn

`hops` is de sterke, en het is het enige signaal dat **bewijs is en geen
geografie**: een ADVERT noemt zijn afzender met een volledige sleutelprefix, dus
"deze exacte node is door deze exacte waarnemer gehoord, op zoveel hops" is een
meting. Het komt uit `db.observer_receptions()`, die precies daarom beperkt is
tot adverts en tot FLOOD-pakketten — dubbelzinnige gegevens voeren aan wat
dubbelzinnigheid moet oplossen, zou een cirkel zijn.

Afstand en recentheid zijn **vooronderstellingen**, en ze worden alleen gebruikt
om te ordenen wat het hopbewijs gelijk laat.

`seen` valt terug op `contacts.updated` wanneer deze waarnemer de node nooit zelf
gehoord heeft: een contact dat Home Assistant pushte, heeft een datum maar geen
ontvangst.

## Hoe de server het aanroept

`routes_api._resolve_hop()` levert de waarnemerscontext en de grens:

```python
weighed = candidates.weigh(
    [...db.contacts_by_key_prefix(hop_hash)...],
    evidence=ctx["evidence"],      # db.observer_receptions(observer)
    observer6=ctx["prefix6"],
    observer_pos=ctx["pos"],       # db.contact_location(prefix6)
    bound=candidates.radio_hop_bound(role, route, path_len, index),
)
```

`db.contacts_by_key_prefix()` geeft een **lijst** terug, nooit één rij, want een
padhop identificeert een node met slechts zijn eerste een of twee sleutelbytes.
Bellers moeten dat presenteren als de dubbelzinnigheid die het is. De functie
weigert alles wat geen 1 tot 6 hextekens is.

Beide contextopzoekingen worden een minuut lang onthouden, samen — zie
[`api.md`](api.md#hopresolutie-en-haar-caches).

`_trim()` brengt een resolutie terug tot wat een lijstregel nodig heeft: namen,
`hops`, `km`, de `lead`, en de tellingen die de lezer verteld moeten worden.
Coördinaten en tijdstempels vallen weg; `total` overleeft de inkorting zodat een
regel nog kan zeggen hoeveel kandidaten er waren, ook als ze er maar zes
afdrukt.

## Waar de eerlijkheidsregel elders toegepast wordt

**De getekende route** (`GET /api/v1/packets`, `path[]`): coördinaten alleen voor
`known`. Al de rest is een gatenvrij gat.

**De heatmap** (`GET /api/v1/packets/heatmap`): een onzekere hop breekt de keten
in plaats van overbrugd te worden. De route van één pakket kan zich een
gestreepte gok over zo'n gat veroorloven; op een heatmap zou die gok geteld en
hérteld worden tot een massieve, gezaghebbend ogende lijn. De heatmap lost hops
**zonder** waarnemer of route op, met opzet — hij gebruikt alleen ooit een
`known`-resolutie, dus een rangschikking zou daar niets veranderen.

**Het nodepaneel** (`GET /api/v1/nodes/{prefix}`): `as_hop.packets` is een
plafond en `as_hop.siblings` zegt hoeveel van een — hoeveel bekende nodes de
eerste sleutelbyte van deze node delen. Geteld over `contacts` en niet aangenomen
als 256: wat telt is hoeveel nodes deze site werkelijk met elkaar zou kunnen
verwarren.

**Het filter op de live kaart**: elke node op een getoonde route wordt op volle
sterkte getoond zolang het detailpaneel openstaat, ook hops die het filter
uitsluit. Een gat in een getekend pad betekent al iets precies — "we kunnen niet
zeggen welke node dit was" — en het filter mag dat niet kunnen nabootsen.

## Tests

`server/tests/test_candidates.py` dekt de grens in beide routerichtingen, de
uitsluiting en haar meetvrijstelling, de laag- en bandgrenzen, en het gelijkspel
dat `ambiguous` moet blijven.

## Verwante documenten

| Vraag | Document |
|---|---|
| Waar de hashes vandaan komen | [`decoder.md`](decoder.md#adreshashes-per-payloadtype) |
| De bewijstabel achter `hops` | [`database.md`](database.md#hopaantallen-alleen-uit-flood) |
| Welke endpoints deze toestanden teruggeven | [`api.md`](api.md) |
| De protocolregel waar de grens op rust | [`protocol.md`](../protocol.md#14-the-path-field) |
