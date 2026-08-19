# Tests

Draaien vanuit `server/`:

```bash
pip install -r requirements-dev.txt
python -m pytest
```

`pytest.ini` zet de testmap en het importpad; er is verder niets te
configureren. De tests raken geen netwerk, geen MQTT en geen echte database:
alles loopt tegen tijdelijke SQLite-files, en `conftest.py` wijst de datamap
van de app naar een wegwerpmap zodat een testrun nooit `server/data/` in de
werkkopie aanmaakt.

## Wat hier ligt

| Bestand | Dekt |
|---|---|
| `test_packets.py` | De rauwe-pakketdecoder (`app/packets.py`): scope-indeling, adreshashes per payloadtype, ADVERT-velden, padparsing, truncaties |
| `test_search.py` | De zoektaal (`app/search.py`): syntaxis, LIKE-escaping, en de belofte dat onbegrijpelijke invoer een fout is en nooit stilte |
| `test_search_sort.py` | Het sorteren van archiefresultaten: `parse_sort` maakt er een ORDER BY van, en het endpoint past die op de rijen toe en op niets anders |
| `test_db.py` | Opslag en herstel (`app/db.py`): decoderkolommen bij insert, `_backfill_from_raw`, en de `COLUMN_MIGRATIONS`-aanpak |
| `test_candidates.py` | De kandidaatweging (`app/candidates.py`): niet "welke node is het", maar wanneer we dat mogen zeggen |
| `test_clocksync.py` | De klok die de site naar het mesh stuurt (`app/clocksync.py`); bijna allemaal weigeringen, want de correctie gaat één kant op |
| `test_commanding.py` | "Kan deze knop iets doen?" (`app/commanding.py`), beantwoord uit vier losse bronnen |
| `test_mqtt_command.py` | De keten site → broker → node, en de eigenschap dat publiceren niets zegt over aankomen |
| `test_mqtt_ingest.py` | Berichten die niet te lezen vallen; regressie voor een nodenaam met een aanhalingsteken erin |
| `test_nodes.py` | Het nodedetail achter een bolletje op de live kaart, dat bijna helemaal uit afleidingen bestaat |
| `test_kanalen.py` | Kanaalmetingen van een sensornode: het wireformaat van CayenneLPP, twee LPP-types op één kanaal, en de namen die de site er per node bij bewaart |
| `test_retention.py` | Het opruimen: de bewaartermijn, de twee bovengrenzen, en vooral de volgorde waarin ze bijten |
| `test_settings_chain.py` | De instellingenketen knop → wachtrij → poller → opslag, met zijn clear-on-read-wachtrij |
| `frames.py` | Bouwstenen voor zelfgemaakte MeshCore-frames, naar `docs/protocol.md` §1 |

De testvectoren zijn allemaal zelfgemaakt uit de protocolkennis in
`docs/protocol.md`; er staat geen enkel echt, opgevangen pakket in deze map.

Een uitgebreidere beschrijving per module — en waarom die een eigen bestand
verdient — staat in [`docs/nl/testing.md`](../../docs/nl/testing.md).

## Wat hier bewust niet ligt

Gedrag dat nog in beweging is, wordt niet vastgeklonken: de schaal en het
venster van de heatmap, de betekenis van `since_id=0` in `recent_packets`, en
de frontend van de archiefpagina. Tests daarop zouden bij de eerstvolgende
bedoelde wijziging breken en leren dan niets. Decoder, zoektaal en backfill
zijn stabiel terrein; daar liggen de ankers.
