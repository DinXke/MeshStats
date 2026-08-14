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
| `test_db.py` | Opslag en herstel (`app/db.py`): decoderkolommen bij insert, `_backfill_from_raw`, en de `COLUMN_MIGRATIONS`-aanpak |
| `frames.py` | Bouwstenen voor zelfgemaakte MeshCore-frames, naar `docs/protocol.md` §1 |

De testvectoren zijn allemaal zelfgemaakt uit de protocolkennis in
`docs/protocol.md`; er staat geen enkel echt, opgevangen pakket in deze map.

## Wat hier bewust niet ligt

Gedrag dat nog in beweging is, wordt niet vastgeklonken: de schaal en het
venster van de heatmap, de betekenis van `since_id=0` in `recent_packets`, en
de frontend van de archiefpagina. Tests daarop zouden bij de eerstvolgende
bedoelde wijziging breken en leren dan niets. Decoder, zoektaal en backfill
zijn stabiel terrein; daar liggen de ankers.
