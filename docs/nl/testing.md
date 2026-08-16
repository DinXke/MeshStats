# Testen

*[English](../testing.md)*

De suite onder `server/tests/`: hoe je hem draait, hoe hij opgebouwd is, en —
nuttiger — waar hij *voor* is. Deze tests zijn geen dekkingscijfer. Ze zijn een
opgeschreven vastlegging van wat het systeem mag beweren, en waar het moet
weigeren.

---

## Inhoud

- [De tests draaien](#de-tests-draaien)
- [Configuratie](#configuratie)
- [De twee harde regels](#de-twee-harde-regels)
- [Hoe een testdatabase gemaakt wordt](#hoe-een-testdatabase-gemaakt-wordt)
- [`frames.py`: pakketten uit de specificatie](#framespy-pakketten-uit-de-specificatie)
- [De testmodules](#de-testmodules)
- [Tests als uitspraken van weigering](#tests-als-uitspraken-van-weigering)
- [Wat er bewust niet getest wordt](#wat-er-bewust-niet-getest-wordt)
- [Een nieuwe test schrijven](#een-nieuwe-test-schrijven)

---

## De tests draaien

Vanuit `server/`:

```bash
pip install -r requirements-dev.txt
python -m pytest
```

`requirements-dev.txt` is de runtime-requirements plus `pytest>=8`. Meer is er
niet nodig: geen database, geen broker, geen netwerk, geen fixtures om te vullen.

Handige aanroepen:

```bash
python -m pytest tests/test_packets.py          # één module
python -m pytest -k backfill                    # op naam
python -m pytest -x -q                          # stop bij de eerste fout
```

Ruwweg 220 tests over een tiental modules; een volledige run duurt seconden.

---

## Configuratie

`server/pytest.ini`, volledig:

```ini
[pytest]
testpaths = tests
pythonpath = .
```

Dat is de hele configuratie. Er is geen plug-instapel in `conftest`, geen
`tox.ini`, geen dekkingsdrempel en geen CI-bestand. Zie
[`contributing.md` §9](contributing.md#9-gereedschap-of-het-gebrek-eraan) — de
afwezigheid is de conventie.

### `conftest.py` doet precies één ding

```python
os.environ.setdefault("MCS_DATA_DIR",
                      tempfile.mkdtemp(prefix="meshstats-test-data-"))
```

Het importeren van `app.config` **maakt de datamap aan en schrijft er een geheime
sleutel in**. Zonder deze omleiding zou de eerste testrun dus een `server/data/`
met een `secret.key` in je werkkopie achterlaten. Het moet op moduleniveau
gebeuren, want pytest laadt `conftest.py` voordat enige testmodule `app`
importeert.

Er staan geen fixtures in `conftest.py`. Testdatabases worden per module gebouwd
— zie hieronder.

---

## De twee harde regels

### 1. Er wordt niets echts aangeraakt

Geen netwerk, geen MQTT, geen echte database. Alles loopt tegen tijdelijke
SQLite-bestanden, en afhankelijkheden worden nagebootst met
`monkeypatch.setattr` op moduleattributen in plaats van met een mockbibliotheek.
`test_clocksync.py` leunt daar zwaar op.

Een testrun is veilig te draaien op de machine waar ook de site op staat.

### 2. Geen opgevangen pakketten

Elke testvector is **met de hand gebouwd uit
[`protocol.md` §1](protocol.md#1-the-over-the-air-packet-format)** door
`tests/frames.py`. Er staat geen enkel echt, opgevangen pakket in de map.

Dat is geen preutsheid over binaire fixtures. Het betekent dat een falende
decodertest je naar de specificatie stuurt, waar het meningsverschil op te lossen
valt, in plaats van naar een blob waarvan niemand de herkomst nog weet.

---

## Hoe een testdatabase gemaakt wordt

Per module, niet gedeeld. Het standaardpatroon (`test_db.py`, herhaald in
`test_candidates.py`):

```python
@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "test.sqlite3")
    db_module._conn = None
    yield db_module
    ...  # verbinding sluiten en weer op None zetten
```

Beide helften doen ertoe, en de docstring van de fixture zegt waarom: *anders
lekken tests in elkaar en kan Windows de tijdelijke file niet opruimen.* De
verbinding op moduleniveau in `db.py` is een global; een fixture die vergeet hem
te resetten geeft de volgende test de database van de vorige.

Het aanmaken van het schema gebeurt impliciet. `get_conn()` draait `SCHEMA`,
`_migrate()` en `_backfill_from_raw()` bij het eerste gebruik, zodat een vers
tijdelijk bestand volledig gemigreerd aankomt — wat ook betekent dat het
migratiepad geoefend wordt door elke test die opslag raakt.

---

## `frames.py`: pakketten uit de specificatie

87 regels, de tegenhanger van de decoder in `server/app/packets.py`.

| Onderdeel | Wat het bouwt |
|---|---|
| Route- en typeconstanten | `ROUTE_TRANSPORT_FLOOD` … `TYPE_PATH`, gespiegeld aan het wireformaat |
| `frame(route, ptype, *, version, codes, hops, hash_size, payload)` | Een compleet frame: headerbyte, optionele transportcodes, paddescriptor, hops, payload |
| `advert_payload(...)` | `pubkey(32) + timestamp(4 LE) + signature(64) + app_data`, met de volgorde van de vlaggenbyte (lat/lon, feat1, feat2, naam) |
| `peer_payload(dest, src, blob)` | `dest_hash(1) + src_hash(1) + MAC(2) + ciphertext`, voor REQ / RESPONSE / TXT_MSG / PATH |

Twee details om te kennen voor je hem gebruikt:

**De sleutel en de handtekening zijn bewust onmogelijk.** `PUBKEY =
bytes(range(1, 33))` en `SIGNATURE = b"\xab" * 64`. Geen echte sleutel heeft die
vorm, en de decoder controleert handtekeningen toch niet — dus een fixture die er
écht uitzag zou alleen maar uitnodigen om te geloven dat hij het was.

**`frame()` valideert zijn eigen argumenten niet.** Hij hangt met plezier
transportcodes aan een routetype dat die niet mag hebben. Dat is nodig: de tests
voor truncatie en misvormde frames bestaan juist om frames te bouwen die de
beloften van het protocol breken, en een behulpzame bouwer zou ze onschrijfbaar
maken.

Zonder optionele velden geeft `advert_payload()` een kaal advert van 100 bytes
terug, dat de decoder moet accepteren.

---

## De testmodules

Allemaal in het Nederlands, inclusief de testfunctienamen. Elk bestand opent met
een docstring die zegt **waarom het bestaat** — lees die eerst; het zijn de
ontwerpnotities bij het gebied dat getest wordt.

| Module | Tests | Gebied | De vraag die het beslecht |
|---|---|---|---|
| `test_packets.py` | 23 | `app/packets.py` | Scope-indeling, adreshashes per payloadtype, ADVERT-velden, padparsing, en wat er bij een truncatie gebeurt |
| `test_search.py` | 27 | `app/search.py` | Zoeksyntaxis, LIKE-escaping, en de belofte dat onbegrijpelijke invoer een fout is en nooit stilte |
| `test_search_sort.py` | 11 | `app/search.py` + het zoek-endpoint | Dat rijen echt in de gevraagde volgorde aankomen, en dat pagineren dat niet verstoort |
| `test_db.py` | 11 | `app/db.py` | Decoderkolommen bij insert, `COLUMN_MIGRATIONS`, en `_backfill_from_raw` |
| `test_candidates.py` | 23 | `app/candidates.py` | Niet "welke node is het" maar **wanneer mogen we dat zeggen** |
| `test_clocksync.py` | 25 | `app/clocksync.py` | De klok die de site naar het mesh stuurt. Bijna allemaal weigeringen — de correctie gaat één kant op |
| `test_commanding.py` | 19 | `app/commanding.py` | "Kan deze knop iets doen?", beantwoord uit vier losse bronnen |
| `test_mqtt_command.py` | 17 | site → broker → node | Dat publiceren niets zegt over aankomen, en wat er dus **niet** mag gebeuren |
| `test_mqtt_ingest.py` | 7 | `app/mqtt_ingest.py` | Onleesbare berichten. Regressie voor een nodenaam met een aanhalingsteken |
| `test_nodes.py` | 16 | `/api/v1/nodes/{prefix}` | Een paneel dat bijna helemaal samengesteld is uit dingen die in geen enkele kolom staan |
| `test_retention.py` | 15 | Opruimen | Niet "er wordt iets verwijderd" maar de **volgorde** waarin dat gebeurt |
| `test_settings_chain.py` | 25 | knop → wachtrij → poller → opslag | De clear-on-read-wachtrij, die faalt zonder ook maar één foutmelding op te leveren |

### Waarom een aantal hiervan een eigen bestand heeft

De keuzes zijn niet willekeurig, en de docstrings leggen ze uit:

- **`test_commanding.py`** — het antwoord op "kan deze knop iets doen?" komt uit
  vier losse bronnen: wie er voor deze repeater publiceert, welke firmware die
  draait, of de broker eraan hangt, en of er recent gepold is. Fout antwoorden
  kost geen foutmelding, maar een pagina die iets belooft wat niemand gaat doen.
- **`test_mqtt_command.py`** — de keten heeft één eigenschap die alles bepaalt:
  publiceren zegt niets over aankomen. De broker bewaart niets voor een offline
  node en de node bevestigt niets terug. Wat er vastligt is dus vooral wat er
  **niet** mag gebeuren: niet retained publiceren, niet publiceren zonder
  verbinding, niets op enig ander topic.
- **`test_mqtt_ingest.py`** — herleidbaar tot een specifieke firmwarenoot: een
  nodenaam met een aanhalingsteken maakte de payload ongeldige JSON, het bericht
  werd weggegooid, en de node verdween uit de statistieken terwijl elke teller
  aan de firmwarekant "gepubliceerd" bleef melden.
- **`test_nodes.py`** — het antwoord van het nodedetail is bijna helemaal
  afgeleid: hoeveel verkeer aan een node toe te schrijven valt, wie hem hoort,
  hoe vaak hij als hop opduikt. Elk van die afleidingen draagt een voorbehoud, en
  die voorbehouden zijn het onderwerp van de test.
- **`test_retention.py`** — de belofte is de *volgorde*: eerst gaat weg wat te oud
  is, en pas daarna, als het er dan nog te veel zijn, gaat de oudste weg tot het
  past. Die tweede helft is onzichtbaar als je alleen rijen telt.
- **`test_settings_chain.py`** — de keten is fragiel op precies één plek die geen
  foutmelding oplevert: de wachtrij op de site is clear-on-read, dus zodra de
  poller een verzoek heeft opgehaald bestaat het nergens meer. Gaat het herkennen
  van de sleutel daarna mis, dan is het verzoek weg en blijft de beheerpagina op
  zijn belofte hangen.
- **`test_clocksync.py`** — de firmware zet een klok alleen ooit **vooruit**,
  omdat een node die zijn klok terugzet zijn eigen adverts ongeldig maakt voor
  iedereen die hem al kent. Een fout die hier vertrekt is aan de overkant niet
  meer terug te halen.

---

## Tests als uitspraken van weigering

De naamgevingsconventie volgt rechtstreeks uit
[`contributing.md` §1](contributing.md#1-eerlijkheid-over-onzekerheid).
Testnamen zijn zinnen over wat het systeem niet zal beweren:

```
test_zonder_adjtimex_wordt_er_niets_beweerd
test_een_klok_die_ver_achteruit_sprong_wordt_geweigerd
test_backfill_herstelt_geleegde_kolommen
```

`test_candidates.py` zegt het ronduit in zijn docstring: de tests zijn zo
geschreven dat ze vooral de **weigering** vastleggen — geen winnaar bij
gelijkspel, geen naam als alles is afgevallen, en geen uitsluiting op een veld
dat het frame niet begrenst.

Dat is wat de suite de moeite waard maakt. Een test dat correcte invoer correcte
uitvoer geeft, beschermt tegen typefouten. Een test dat *dubbelzinnige* invoer een
dubbelzinnig antwoord geeft, beschermt tegen de hele klasse wijzigingen waarin
iemand de uitvoer netter maakt door hem oneerlijk te maken.

---

## Wat er bewust niet getest wordt

`server/tests/README.md` houdt een expliciete lijst bij, en de redenering is het
herhalen waard: gedrag dat nog in beweging is wordt niet vastgeklonken, want
tests daarop zouden bij de eerstvolgende bedoelde wijziging breken en leren dan
niets.

Op die grond momenteel uitgesloten:

- De schaal en het venster van de heatmap
- De betekenis van `since_id=0` in `recent_packets`
- De frontend van de archiefpagina

Stabiel verklaard, en dus verankerd: de decoder, de zoektaal en de backfill.

Sta je op het punt een test toe te voegen, kijk dan of wat je test op die lijst
staat. Staat het er, dan is de lijst verouderd — zeg dat in de commit — of moet
de test wachten.

---

## Een nieuwe test schrijven

1. **Begin met de docstring.** Zeg waarom het bestand of het geval bestaat, en wat
   er zonder onopgemerkt zou blijven. Dat is de conventie die elke bestaande
   module volgt, en het nuttigste deel van het bestand.
2. **Bouw pakketten met `frames.py`.** Heb je een vorm nodig die hij niet kan
   bouwen, breid `frames.py` dan uit vanuit [`protocol.md`](protocol.md) en
   noem de paragraaf. Plak geen opgevangen frame.
3. **Schrijf de weigering, niet alleen het succes.** Benoem de toestand waarin het
   systeem mag verkeren als het iets niet weet, en leg vast dat het dat zegt.
4. **Reset globals.** `db_module._conn`, moduleattributen die met `monkeypatch`
   gezet zijn — alles op moduleniveau lekt naar de volgende test als je het laat
   staan.
5. **Nederlandse namen, zinsvorm.** `test_<wat er gebeurt>_<onder welke
   voorwaarde>`.
6. **Houd het offline.** Heeft je test een broker of een netwerk nodig, dan heeft
   het ding dat je test een naad nodig.

---

## Zie ook

| | |
|---|---|
| Waarom de tests er zo uitzien | [`contributing.md`](contributing.md) |
| De specificatie waar de fixtures uit gebouwd zijn | [`protocol.md`](protocol.md) |
| Wat de geteste modules doen | [`architecture.md`](architecture.md) |
