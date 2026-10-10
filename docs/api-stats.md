# Statistiek-API (server 1.3.0)

Alle endpoints: `GET`, JSON, login vereist (sessiecookie). Recht: `map.view`; deellinks krijgen `403`, niet ingelogd `401`.

## Gemeenschappelijk

| Parameter | Type | Standaard | Betekenis |
|---|---|---|---|
| `from` | unix-seconden | `to` − 7 dagen | begin van het bereik (inclusief) |
| `to` | unix-seconden | nu | einde van het bereik (inclusief) |
| `tracker` | tracker-id | – | alleen deze tracker; `404` als hij niet bestaat of niet zichtbaar is |
| `channel` | kanaal-id | – | alleen trackers met dit trackingkanaal; `404` als het kanaal niet bestaat |

- `from` > `to` → `400`. `from` wordt opgeschoven tot de terugblik (`history_hours`) van de gebruiker; valt alles
  daarbuiten, dan is het antwoord leeg (nullen).
- **Zichtbaarheid**: een gebruiker ziet de trackers van zijn kanalen plus losse trackers (zoals op de kaart); wie
  "alle kanalen" heeft (beheerders) ziet alles. Tellers in `stats_events` zonder tracker (bv. `unknown`, `t1f_msg`)
  tellen alleen mee voor wie hun kanaal mag zien; zonder kanaal (bv. `old_fw_dm` van een onbekende afzender) alleen
  voor wie alles ziet.
- **Bereik op ontvangsttijd**: berichten, punten en tellers vallen in het bereik op hun ontvangsttijd (`rx_ts`). Alleen
  de gaten van `/api/stats/fifo` gaan op positietijd (`ts`).
- **Cache**: elk antwoord blijft 30 s bewaard per gebruiker en per exacte query-string. Zonder `to` bevriest het
  bereik dus tot 30 s.
- Gemiddelden zijn afgerond op 2 decimalen (batterij op 1). Ontbrekende waarden zijn `null`.

### Begrippen

- **bericht** (message): één ontvangen T1-bericht dat iets opsloeg. Gewone toestanden (M, W, S, H, N, E, P, B): de
  hoofdrij. SlowTrack `L` en FIFO `Q`: één per (tracker, toestand, seq, ontvangsttijd), ook als alleen extra punten
  nieuw waren. Een volledig dubbel bericht slaat niets op: het telt niet als bericht maar in `dup_msg` (zelfde seq) of
  `dup_points` (alle punten al bekend).
- **punt** (point): opgeslagen positie met coördinaten. Soorten: `live` = hoofdpunt van een niet-L/Q-bericht,
  `extra` = eerder punt uit een niet-L/Q-bericht, `slow` = L-punt, `fifo` = Q-punt. Een N-bericht (geen fix) is een
  bericht zonder punt.
- **SNR / hops** van een bericht: zoals de companion ze meldde; hops buiten 0..63 (bv. 255) gelden als onbekend.
- **vertraging** (delay) = `rx_ts − ts` per punt (s). `recovered_late` = punten met vertraging > 300 s.
- **emmers**: `hour` = UTC-uur; `day` = lokale middernacht in `tz` (standaard Europe/Brussels, `MESHTRACK_TZ`).
  `bucket=auto` (standaard): `hour` als `to − from` ≤ 3 dagen, anders `day`. Hoogstens 2000 emmers (anders `400`).
  `t` = begin van de emmer; de eerste emmer begint op of vóór `from`. Lege emmers staan erin met nullen/`null`.

---

## 1. `GET /api/stats/summary`

```json
{
  "range": {"from": 1799395200, "to": 1800000000},
  "path_source": "openhop",
  "totals": {
    "messages": 5,
    "messages_by_state": {"M": 2, "W": 0, "S": 1, "H": 0, "N": 1, "E": 0, "P": 0, "V": 0, "B": 0, "L": 0, "Q": 1},
    "points": 8,
    "points_by_kind": {"live": 3, "extra": 2, "slow": 0, "fifo": 3},
    "recovered_late": 3,
    "dup_points": 3, "dup_msg": 1, "invalid": 0, "unknown": 0,
    "t1a_sent": 0, "t1f_sent": 1, "t1f_msg": 1, "old_fw_dm": 0,
    "avg_snr": 2.4, "avg_hops": 1.5, "trackers_active": 2
  },
  "trackers": [
    {"id": 1, "alias": "Björn", "channel": "MeshTracker-01", "channel_id": 1,
     "messages": 4, "points": 7, "q_messages": 1, "q_points": 3, "l_points": 0,
     "last_rx": 1799999920, "avg_snr": 2.5, "min_snr": -3.0, "max_snr": 7.0, "avg_hops": 1.0,
     "bat_last": 80, "fw_mode": "t", "delivery_delay_p50": 40, "delivery_delay_p90": 3020,
     "first_hop": {"hash": "48d7", "name": "Heuvel", "count": 2}}
  ]
}
```

| Veld | Betekenis |
|---|---|
| `totals.messages_by_state` | berichten per toestand; alle gekende toestanden staan erin (ook 0) |
| `totals.points_by_kind` | zie *Begrippen* |
| `totals.dup_points` … `old_fw_dm` | som uit `stats_events` in het bereik (zie §5) |
| `totals.avg_snr`, `avg_hops` | over berichten |
| `totals.trackers_active` | trackers met minstens één bericht of punt |
| `trackers[]` | **alle** zichtbare trackers in de selectie, ook zonder verkeer (dan nullen), gesorteerd op alias |
| `channel` / `channel_id` | naam (alleen als de gebruiker het kanaal mag zien, anders `null`) / id van het trackingkanaal |
| `q_messages`, `q_points`, `l_points` | FIFO-berichten, Q-punten, L-punten |
| `last_rx` | laatste ontvangst van de tracker (niet begrensd door het bereik) |
| `bat_last`, `fw_mode` | laatst gemelde batterij (%) en modus (`t` tracker, `c` companion, of `null`) |
| `delivery_delay_p50/p90` | mediaan / 90e percentiel van de vertraging van de punten (s, nearest-rank) |
| `first_hop` | meest gebruikte eerste hop (repeater het dichtst bij de tracker) over alle gehoorde kopieën, of `null` |
| `path_source` | bron van de paden voor `first_hop` (zie §6) |

## 2. `GET /api/stats/timeseries?bucket=hour|day|auto`

```json
{
  "bucket": "hour", "tz": "Europe/Brussels", "range": {"from": 1799989200, "to": 1800000000},
  "series": [
    {"t": 1799989200, "messages": 0, "points": 0, "live": 0, "extra": 0, "slow": 0, "fifo": 0,
     "avg_snr": null, "min_snr": null, "max_snr": null, "avg_bat": null, "trackers": 0},
    {"t": 1799996400, "messages": 5, "points": 8, "live": 3, "extra": 2, "slow": 0, "fifo": 3,
     "avg_snr": 2.4, "min_snr": -3.0, "max_snr": 7.0, "avg_bat": 72.0, "trackers": 2}
  ]
}
```

- `messages`, `points`, `live`/`extra`/`slow`/`fifo`: zoals in de samenvatting, per emmer.
- `avg_snr`/`min_snr`/`max_snr`: over de berichten van de emmer. `avg_bat`: over de berichten met een batterijwaarde.
- `trackers`: aantal verschillende trackers met verkeer in die emmer.
- Ongeldige `bucket` → `400`.

## 3. `GET /api/stats/distributions`

```json
{
  "range": {"from": 1799913600, "to": 1800000000}, "tz": "Europe/Brussels",
  "snr": [{"lo": -20, "hi": -18, "count": 0}, "…", {"lo": 14, "hi": 16, "count": 0}],
  "hops": [{"hops": 0, "count": 1}, {"hops": 1, "count": 1}, {"hops": 2, "count": 1}, {"hops": 3, "count": 1}],
  "delay": [{"lo": 0, "hi": 10, "count": 2}, {"lo": 10, "hi": 30, "count": 2}, {"lo": 30, "hi": 60, "count": 1},
            {"lo": 60, "hi": 300, "count": 0}, {"lo": 300, "hi": 1800, "count": 0}, {"lo": 1800, "hi": 7200, "count": 3},
            {"lo": 7200, "hi": 43200, "count": 0}, {"lo": 43200, "hi": null, "count": 0}],
  "states": {"M": 2, "W": 0, "S": 1, "H": 0, "N": 1, "E": 0, "P": 0, "V": 0, "B": 0, "L": 0, "Q": 1},
  "hour_of_day": [0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 5, 0, 0]
}
```

- `snr`: berichten per 2 dB, 18 emmers `[lo, hi)` van −20 tot +16; waarden daarbuiten tellen in de randemmer.
- `hops`: berichten per aantal hops, van 0 tot het hoogste voorkomende (zonder gaten; leeg als er niets is).
- `delay`: punten per vertraging in s, emmers `[lo, hi)`; `hi: null` = open (> 12 u); negatief (klokfout) telt in de eerste.
- `states`: berichten per toestand. `hour_of_day`: 24 tellers, berichten per lokaal uur (in `tz`) van ontvangst.

## 4. `GET /api/stats/fifo`

```json
{
  "range": {"from": 1799913600, "to": 1800000000},
  "trackers": [
    {"id": 1, "alias": "Björn", "q_messages": 1, "q_points": 3, "t1f_sent": 1, "avg_points_per_q": 3.0,
     "max_gap_filled_s": 3000,
     "gaps": [{"from": 1799996000, "to": 1799999000, "filled_points": 3}]}
  ]
}
```

- Alleen trackers met Q-berichten, Q-punten, verstuurde T1F of een gevuld gat.
- `t1f_sent`: aantal keer dat deze tracker in een T1F-bevestiging stond. `avg_points_per_q` = `q_points / q_messages`.
- **Gat**: meer dan 10 min (`> 600 s`) tussen twee opeenvolgende live/extra punten van de tracker (op positietijd
  `ts`, beide binnen het bereik) waarin later L- of Q-punten aankwamen (`filled_points` = L/Q-punten strikt
  daartussen). Gaten zonder ingehaalde punten staan er niet in. Hoogstens de 20 grootste, langste eerst.
  `max_gap_filled_s` = lengte van het grootste gevulde gat, of `null`.

## 5. `GET /api/stats/events?kind=…&bucket=hour|day|auto`

```json
{
  "bucket": "hour", "tz": "Europe/Brussels", "range": {"from": 1799992800, "to": 1800000060},
  "kinds": ["invalid", "unknown"],
  "totals": {"invalid": 2, "unknown": 1},
  "series": [{"t": 1799992800, "invalid": 0, "unknown": 0}, {"t": 1799996400, "invalid": 2, "unknown": 1}]
}
```

- `kind`: één soort of een kommalijst; leeg = alle soorten. Onbekende soort → `400`. Emmers zoals §2.
- Soorten (tabel `stats_events`):

| `kind` | Wanneer | tracker / kanaal |
|---|---|---|
| `t1a_sent` | SOS-bevestiging `T1A` verstuurd | tracker + kanaal |
| `t1f_sent` | per tracker in een verstuurde `T1F`-bundel | tracker + kanaal |
| `t1f_msg` | per verstuurd `T1F`-bericht | kanaal |
| `dup_points` | punten overgeslagen omdat hun fix-tijd al bestond (`n` = aantal) | tracker (+ kanaal) |
| `dup_msg` | bericht met een seq die al binnen was | tracker (+ kanaal) |
| `invalid` | ongeldig kanaalbericht, foute of ontbrekende handtekening, onleesbaar T1-bericht | tracker als gekend + kanaal |
| `unknown` | MeshTrack-bericht van een onbekende tracker | kanaal |
| `old_fw_dm` | T1-bericht via DM (oude firmware) | tracker als gekend |
| `loc_request` | (1.3.2) verzoek om een positie `T1R\|<*\|pk8>\|<nonce>` op een van onze kanalen | doeltracker als gekend (`*` = NULL) + kanaal |

## 6. `GET /api/stats/repeaters?source=auto|openhop|companion`

```json
{
  "range": {"from": 1799395200, "to": 1800000000},
  "source": "openhop",
  "repeaters": [
    {"hash": "e3d3", "name": "Dak-e3d3", "candidates": 1, "count": 3, "as_first_hop": 0, "as_last_hop": 3,
     "avg_snr": 0.33, "trackers": 1},
    {"hash": "48d7", "name": "Heuvel", "candidates": 1, "count": 2, "as_first_hop": 2, "as_last_hop": 0,
     "avg_snr": null, "trackers": 1},
    {"hash": "5c39", "name": "5c39 (2 kandidaten)", "candidates": 2, "count": 1, "as_first_hop": 1, "as_last_hop": 0,
     "avg_snr": null, "trackers": 1}
  ],
  "top_paths": [
    {"path": "48d7,e3d3", "hops": 2, "count": 2, "avg_snr": 2.5},
    {"path": "", "hops": 0, "count": 1, "avg_snr": 10.0},
    {"path": "5c39,e3d3", "hops": 2, "count": 1, "avg_snr": -4.0}
  ],
  "hops_by_tracker": [{"id": 1, "alias": "Björn", "messages": 2, "avg_hops": 1.0, "direct_pct": 50.0}]
}
```

Paden komen uit de tabel `message_paths`: één rij per **gehoorde kopie** van een geldig ondertekend trackerbericht
(`T1C`) op een van onze kanalen. Twee bronnen:

- `openhop`: de pakketdatabase van openHop (alleen lezen, elke 30 s bijgewerkt): alle kopieën die de antennes
  (`radio`: dak/bureau) hoorden, met SNR/RSSI per kopie.
- `companion`: de rauwe pakketten die de companion doorduwt (`PUSH_CODE_LOG_RX_DATA`), met SNR/RSSI.

Beide worden opgeslagen; één antwoord gebruikt er één (anders telt elke kopie dubbel). `source=auto` (standaard):
`openhop` als die bron in het bereik iets heeft, anders `companion`. Ongeldige `source` → `400`.

| Veld | Betekenis |
|---|---|
| `hash` | padhash van de repeater (hex, 1–3 bytes, zoals in het pakket) |
| `name`, `candidates` | naam via de adverts van openHop en de contacten van de companion (repeaters gaan voor). Eén kandidaat: zijn naam; meerdere: `"<hash> (<n> kandidaten)"`; geen: `null` en 0 |
| `count` | aantal kopieën met deze repeater in het pad |
| `as_first_hop` | … als eerste hop (het dichtst bij de tracker) |
| `as_last_hop` | … als laatste hop (het dichtst bij onze antenne) |
| `avg_snr` | gemiddelde SNR aan onze antenne over de kopieën waarin hij de **laatste** hop was (anders `null`) |
| `trackers` | aantal verschillende trackers |
| `top_paths` | de 20 meest gehoorde paden (`path` met komma's, eerste hop eerst; `""` = rechtstreeks), met gemiddelde SNR |
| `hops_by_tracker` | per tracker met paden: `messages` = berichten (tracker, toestand, seq) met minstens één kopie; `avg_hops` = gemiddelde van het kortste pad per bericht; `direct_pct` = % berichten met een rechtstreeks gehoorde kopie |

`repeaters` is gesorteerd op `count` (aflopend).

---

## Schema (migratie 1.3, automatisch en idempotent)

- `positions.extra` (0/1): eerder punt uit een bericht. Bestaande rijen: 1 als `raw` met `(eerder punt` begint.
- Index `positions_stats(rx_ts, tracker_id, state, extra, ts, seq, snr, path_len, bat, lat)` (dekkend).
- Tabel `stats_events(ts, kind, tracker_id, channel_id, n)` + index op `ts`.
- Tabel `message_paths(id, tracker_id, seq, state, rx_ts, path, hash_size, hops, snr, rssi, radio, source, channel_id)`
  + indexen op `rx_ts` en `(tracker_id, rx_ts)`.
- Beide nieuwe tabellen volgen de bewaartermijn van de posities (`retention_days`).
- De leescursor in de pakketdatabase van openHop staat in `settings` onder `_openhop_paths_cursor`.
