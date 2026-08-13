# MeshStats-firmware

Aanpassingen op de [MeshCore](https://github.com/meshcore-dev/MeshCore)-firmware
(companion, v1.17.0) waarmee je node:

1. **meerdere companions tegelijk** aankan — Home Assistant, de MeshCore-app en
   een statistiekenserver kunnen samen verbonden zijn
2. een **eigen beheerpagina** krijgt op poort 80, met live statistieken
3. zijn statistieken **zelf doorstuurt** naar een MeshStats-site

## Waarom deze aanpassingen

De originele WiFi-interface hield precies één client vast:

```cpp
auto newClient = server.available();
if (newClient) {
    client.stop();      // de bestaande companion wordt eruit gegooid
    client = newClient;
}
```

Daardoor kon je niet tegelijk met Home Assistant én je telefoon verbonden zijn.
Nu zijn er vier slots, elk met een eigen frame-status. Antwoorden gaan alleen
naar de client die het commando stuurde; ongevraagde berichten (adverts,
inkomende berichten) gaan naar iedereen. Zonder dat onderscheid raken clients in
de war van elkaars antwoorden.

Daarnaast zat er een fout in het kanalenbeheer: `setChannel()` (wat apps
gebruiken) werkt de teller `num_channels` niet bij, terwijl `addChannel()` daar
wél op vertrouwt. De teller kon zo oplopen tot het maximum terwijl er lege
plaatsen waren — de app meldde dan onterecht "channel limit reached". Lege
plaatsen worden nu hergebruikt.

## Toepassen

```bash
git clone https://github.com/meshcore-dev/MeshCore.git
cd MeshCore
git checkout companion-v1.17.0

# bestanden uit deze map eroverheen kopiëren
cp -r /pad/naar/MeshStats/firmware/src/* src/
cp -r /pad/naar/MeshStats/firmware/examples/* examples/

# of, als patch:
git apply /pad/naar/MeshStats/firmware/meshstats.patch
```

Maak een `platformio.local.ini` met je eigen instellingen (zie
`platformio.local.ini.example`) en bouw:

```bash
pip install platformio
python -m platformio run -e <jouw_env> -t upload --upload-port COM4
```

> Flashen via `-t upload` schrijft alleen de app-partitie. Je private key,
> contacten en instellingen blijven staan. Maak toch een back-up voor de
> zekerheid:
> `python -m esptool --port COM4 read_flash 0 0x800000 backup.bin`

## Bestanden

| Bestand | Wat |
|---|---|
| `src/helpers/esp32/SerialWifiInterface.{h,cpp}` | Meerdere gelijktijdige WiFi-companions |
| `src/helpers/BaseChatMesh.cpp` | Hergebruik van lege kanaalplaatsen |
| `examples/companion_radio/StatsPublisher.{h,cpp}` | Beheerpagina + doorsturen van statistieken |
| `examples/companion_radio/MyMesh.{h,cpp}` | `fillStatsJson()`: eigen statistieken als JSON |
| `examples/companion_radio/main.cpp` | Module inhaken |

## Beheerpagina

Na het flashen: **http://\<ip-van-je-node\>/**

- instellingen voor het doorsturen (broker/URL, token, interval)
- live statistieken van de node
- `/stats.json` geeft dezelfde gegevens als JSON

## Instelbaar tijdens het bouwen

| Vlag | Standaard | Betekenis |
|---|---|---|
| `WIFI_MAX_CLIENTS` | 4 | Aantal gelijktijdige companions |
| `TCP_PORT` | 5000 | Poort voor companions |
| `MAX_GROUP_CHANNELS` | — | Aantal kanaalplaatsen |

## Status

Werkt en getest op een Heltec V3 (ESP32-S3):

- ✅ meerdere companions tegelijk, met gerichte antwoorden
- ✅ kanaalteller-fix
- ✅ beheerpagina en `/stats.json`
- ⚠️ doorsturen via **HTTP** laat de node crashen (`HTTPClient` en de TLS-stack
  vragen te veel geheugen naast mesh, WiFi en BLE) — daarom vervangen door MQTT,
  dat één lichte verbinding openhoudt
