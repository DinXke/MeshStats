# MeshTrack

APRS-achtige tracking via MeshCore, volledig offline. Trackers (Seeed T1000-E met eigen firmware) sturen hun
positie op een versleuteld MeshCore-kanaal. De MeshTrack-server luistert mee via een companion die openHop host en
toont de trackers live op een kaart; wie de kanaalsleutel heeft, kan ook zonder server meekijken met de offline-app.

```
T1000-E (firmware/)  --kanaalbericht (flood)-->  openHop-companion  --TCP-->  server/ (kaart, beheer, simulator)
                                             \-->  eigen companion  --Bluetooth-->  /offline (PWA, zonder internet)
```

## Onderdelen

- **firmware/**: overlay op stock MeshCore `companion_radio` v1.17.1 (huidige versie 0.9.11; heeft server 1.3.1 of nieuwer nodig voor de FIFO-bevestiging `T1F`, 1.3.2 voor toestand `V`, 1.4.0 voor prioritair).
  - **Nooit op een openbaar kanaal** (0.9.10): geen posities, SOS, verzoekantwoorden of inhaalberichten op Public of een
    #hashtag-kanaal waarvan de sleutel uit de naam volgt; een klik geeft de lage toon; `status` `kanaal_fout=openbaar` met
    een LET OP-regel (aanleiding: een niet-klaargemaakte WisMesh Tag stuurde posities en SOS ongetekend op Public). Een
    nieuwe of gereset tracker heeft geen trackingkanaal (`chan=-`, `kanaal_fout=geen`) en stuurt niets tot hij klaargemaakt
    is; bestaande trackers met Public als trackingkanaal zijn eenmalig op geen gezet. `set chan -` = geen; `set chan <nr>`
    weigert openbare kanalen. Rechtzetten zonder nieuwe firmware: USB → Toestellen → Verbinden → Instellingen →
    Trackingkanaal → MeshTrack-kanaal → Opslaan (kanaal op een vrij nummer, Public blijft, authsleutel gemaakt en gezet,
    regio en trackingkanaal ingesteld; controle: `authkey=ja`, `chan_naam=…`). Niet via Bluetooth (de authsleutel kan alleen via USB); *Klaarmaken* is niet nodig als de tracker zijn eigen sleutel houdt.
  - **Beheercode** (0.9.11): `set ble_beheer <code>` (6–32 tekens, geen spaties) of `uit`, alleen via USB (ook in
    Toestellen; menu *Modus en knop* → 12), bewaard als hash. Zonder beheercode blijft Bluetooth alleen-lezen voor de
    MeshTrack-instellingen. Status `ble_beheer=ingesteld|niet ble_schrijven=ja|nee`. Ontgrendelen in Tracker live →
    Instellingen met uitdaging-antwoord (HMAC; de code gaat nooit leesbaar door de lucht); daarna 15 min schrijven, tot
    *Vergrendelen* of tot de verbinding wegvalt; na 5 foute pogingen 10 min op slot. Wijzigbaar na ontgrendelen: alle
    gewone instellingen (FastTrack, SlowTrack, FIFO, knop en biepjes, locatieverzoeken, modus, regio, radio, zendvermogen,
    nummer van het trackingkanaal en privékanalen, `gps zoek`, FIFO wissen); radio, modus en trackingkanaal met extra
    bevestiging. Nooit via Bluetooth: authsleutel, beheercode, koppelcode (alleen companioncommando/USB), sleutels en
    back-ups, formatteren, defaults. Advies: beheercode ≠ koppelcode, en verander 123456. De tekst "trackingkanaal 255"
    is nu "geen trackingkanaal: maak het toestel klaar via Toestellen".
  - Bluetooth-koppelcode (0.9.9): `set blepin <6 cijfers>|standaard` (werkt na een herstart), `blepin toon` (alleen USB),
    status `blepin=eigen|standaard`, menu *Modus en knop* → 11 *Bluetooth-koppelcode*; in Toestellen het veld
    *Bluetooth-code* (oudere firmware: via Tracker live → Instellingen). De code zit nooit in een back-up. RAK3401 met
    OLED en standaardcode: MeshCore kiest per sessie een willekeurige code; `blepin toon` geeft de actieve.
  - `/tracker` tabblad *Instellingen* (Bluetooth, companionmodus): leest bij het openen alle huidige instellingen
    (MeshTrack-status + MeshCore: naam, radio, zendvermogen, Bluetooth-code ingesteld of niet, kanalen) en toont ze in
    groepen zoals Toestellen (accordeon), met *Vernieuwen*; de rest alleen-lezen met "Wijzigen via USB (Toestellen)";
    firmware < 0.9.1: alleen de MeshCore-waarden. Wijzigen (alleen standaard MeshCore-companioncommando's, dus ook voor
    firmware ouder dan 0.9.1): Bluetooth-code, naam, herstarten, kanalenlijst met vingerafdruk (nooit de sleutel;
    trackingkanaal gemarkeerd), trackingkanaal vervangen via QR (`meshcore://channel/add?name=…&secret=…`, camera met
    zaklamp of foto; iPhone zonder QR: overtikken) of met de hand (naam + 32 hex), met bevestiging en terugleescontrole;
    het kanaal moet in /kanalen bestaan, de authsleutel blijft. Oudere firmware (< 0.9.1): kanaalnummer zelf kiezen (via
    USB `chan=`). MeshTrack-instellingen (FIFO, SlowTrack, biepjes, modus, regio, authsleutel) voorlopig alleen via USB.
    Wie de koppelcode kent, kan dit wijzigen: verander 123456.
  - **Drie borden**, elke release voor alle drie: Seeed T1000-E (`board=t1000e`, getest); RAK WisMesh Tag (`wismesh_tag`:
    knop, buzzer, LIS2DH-bewegingssensor, AT6558R-GPS); RAK3401 + RAK13302 1 W (`rak3401_1w`: voertuigtracker op een
    RAK19007 met losse UART-GPS, geen buzzer, geen bewegingssensor, terugmelding met de led, max. ≈ 27 dBm na de
    versterker = TX 18 dBm + ≈ 9 dB). **De Tag en de RAK3401 zijn nog niet op een echt toestel getest.** In Toestellen →
    Firmware kies je het bord, of het wordt herkend uit de status. Verkeerd pakket: de bootloader beschermt alleen tussen
    de T1000-E en de RAK-borden (andere SoftDevice); tussen Tag en RAK3401 beschermt alleen de website. Bootloader:
    1200-baud touch (de website doet dat zelf) of dubbelklik op reset voor het UF2-station.
  - Mogelijkhedenregel in `status`: `board= knop= buzzer= accel= accel_type= gps= [gps_pinnen=] tx_max= tx_versterking=`.
    Toestellen toont de kaart *Herkend toestel* (herkenning bij het verbinden via het USB-type, de MeshTrack-status of het
    model van stock MeshCore; vóór het flashen vanaf stock eerst een back-up van de privésleutel), verbergt of grijst
    instellingen die het bord niet heeft, en slaat bij het terugzetten van een back-up niet-passende instellingen over.
  - Rust via de GPS zonder bewegingssensor: `rust_gps_check <tijd>|uit` (standaard 5m): na `still_timeout` zonder
    GPS-beweging gaat de GPS uit en kijkt hij elke x kort of hij verplaatst is (> 50 m of ≥ 3 km/u); `uit` = GPS blijft aan.
  - Alleen RAK3401: GPS-autodetectie (pinnen 15/16 of 8/6, beide richtingen, 9600/38400/115200; bij de eerste start en na
    10 min stilte), `gps zoek`, `set gps_pinnen 15/16@9600`, status `gps_pinnen=`. `pin31 uit|knop|prio`: `knop` =
    analoge knop met bescherming tegen een zwevende pin; `prio` = ingang voor de blauwe lichten (`prio_niveau laag|hoog`,
    standaard laag met interne pull-up; `prio_houd` 1–60 min, standaard 5m, `0` = volgt de ingang: prioritair precies zolang die actief is, meteen voorbij met een bericht; `prio_interval`, standaard 30s, 0 = gewone
    instellingen). Zolang actief draagt elk bericht vlag `p`; bij het aangaan meteen een positie. Status `prio=aan|uit|fout`
    (fout = zwevende ingang). Aansluiten via een PC817-optocoupler (12/24 V → serieweerstand → led; transistorkant → pin 31
    en GND, `prio_niveau laag`); **nooit 12/24 V rechtstreeks, max. 3,3 V op de pin**. Drie bedradingsvarianten met dezelfde firmware: (1) voeding van de blauwe lichten via de
    optocoupler (`prio_houd` 5m bij knipperen), (2) eigen schakelaar op het bedieningspaneel die 12/24 V in hetzelfde
    optocouplercircuit schakelt (`prio_houd 0`), (3) potentiaalvrij contact rechtstreeks tussen pin 31 en GND (`prio_niveau
    laag`, interne pull-up; alleen als er geen voertuigspanning op kan komen; bij lange draden RC-filter 1 kΩ + 100 nF of
    toch een optocoupler; `prio_houd 0`). Lichtsensor kan maar is minder
    betrouwbaar (RGB-sensor zoals TCS34725 met knipperdetectie; LDR of BPW34 af te raden wegens zonlicht). Schema in de
    handleiding (hoofdstuk 13).
  - Volledige companion aan USB, trackermodus op batterij. Dubbelklik wisselt de modus (tot 0,8 s tussen de klikken),
    één klik stuurt meteen een positie, 2 tot 8 s vasthouden stuurt een SOS, langer dan 8 s schakelt uit.
    Met `sos uit` (0.8.0, standaard aan) doet 2 tot 8 s vasthouden niets (geen SOS, geen wapenbiep), tegen een SOS per
    ongeluk in een zak of houder; uitschakelen door lang vasthouden blijft.
  - Verzenden alleen via één **trackingkanaal** (`chan`), met de naam van de tracker als afzender en een handtekening
    met de authsleutel (`authkey`). Sinds 0.7.0 stuurt de tracker zijn posities nooit meer als DM (privébericht) naar de
    server: geen doel (server-pubkey), geen ACK-herhalingen, geen ritme volgens de ontvangst en geen trackergroepen meer.
    Een kanaalbericht krijgt geen bevestiging; alleen op SOS (`T1A`) en FIFO-punten (`T1F`) antwoordt de server op het
    kanaal. DM's bestaan nog wel voor de meldingen van de server aan personen en als privéberichten in companionmodus
    (`msg_beep`).
  - Bewegingsregels (snelheid, afstand, bochten, minimum- en maximuminterval), stilstand en heartbeat, wakker worden via de
    bewegingssensor, radio en led uit in trackermodus. Zelfde bewegingsregels als `server/meshtrack/rules.py` (simulator).
  - In beweging elke `sample` seconden een punt bewaren; elk bericht neemt zoveel punten mee als erin passen.
  - In companionmodus komt een kopie van elke eigen positie in de berichtenwachtrij, zodat een app via Bluetooth ook
    de eigen posities ziet (een companion hoort zijn eigen kanaalberichten anders niet).
  - Terugmelding (0.7.3): na een klik of SOS twee hoge biepjes zodra de tracker zijn eigen bericht via een repeater terug
    hoort (eerste cipherblok vergelijken in de ruwe ontvangst), anders na 12 s een lage toon. De server bevestigt elke
    SOS op het kanaal met `T1A|<pk8>|<tag>|<seq>` (tag = HMAC(authsleutel, `<pk8>|A|<seq>`)); de tracker speelt dan drie
    stijgende tonen. `status`: `gehoord=` en `sos_bevestigd=`.
  - `msg_beep prive|alles|uit` (0.7.1): biep bij berichten als companion zonder app; standaard alleen privéberichten.
  - Biepjes bij automatische berichten (0.8.0, als tracker en als companion, standaard uit): `tx_beep aan` = korte biep
    zodra de radio een automatisch positiebericht verzonden heeft; `heard_beep aan` = twee hoge biepjes als een repeater
    het herhaalt (de radio blijft daarvoor ~12 s na elke zending wakker, dus wat meer verbruik). Klik en SOS houden hun
    eigen terugmelding.
  - **SlowTrack** (0.8.0), naast de gewone tracking (FastTrack): elke `slow_log` een GPS-punt loggen zolang de tracker
    beweegt (stilstaand en < 5 m van het vorige punt = overgeslagen). Sinds 0.9.1 logt hij niet in rust: langer dan
    `still_timeout` (standaard 5 min) geen beweging (bewegingssensor of een GPS-fix ≥ 1,5 km/u) = geen GPS voor
    SlowTrack; de eerste beweging logt meteen een punt. Uitzondering: staat FastTrack uit wegens `fast_min_batt`, dan logt
    hij door en telt een SlowTrack-fix met snelheid als beweging; elke `slow_send` precies één bericht met state `L`.
    Eén bericht draagt zo'n 6 à 11 punten (naargelang de afstanden); zijn er meer, dan wordt gelijkmatig uitgedund
    (oudste en nieuwste blijven). Vuistregel: `slow_log` ≈ 1/8 van `slow_send` (bv. 30m → 4m). Standaard
    `slow_log=uit`, `slow_send=30m`. Mislukt de zending, dan blijft de buffer (64 punten) voor de volgende keer.
  - `fast_min_batt <0..100>` (0.8.0, 0 = uit): onder dit batterijpercentage geen FastTrack; SlowTrack, heartbeat, klik
    en SOS blijven. Weer aan vanaf +3 %, nooit actief aan USB.
  - `status` (0.8.0): ook `slow_log`, `slow_send`, `fast_min_batt`, `sos`, `tx_beep`, `heard_beep`, `slow_buffer`,
    `fasttrack=aan|uit(batterij)` en `slow_per_bericht`. Menu: 5 SlowTrack, 6 Stilstand, 7 GPS, 8 Nu sturen,
    9 Onderhoud; *Modus en knop* kreeg 5 SOS, 6 tx_beep, 7 heard_beep. Back-ups (Toestellen) bewaren de nieuwe sleutels;
    het terugzetten van `uit` als `0` is verholpen.
  - **Trackmodus** (0.9.0): `set track_mode classic|fifo`, standaard `classic` (FastTrack + SlowTrack zoals in 0.8).
    `fifo` = store-and-forward voor posities die de mesh niet haalden:
    - Na elk FastTrack-bericht ~12 s luisteren naar een herhaling. Niet gehoord: de punten liften eerst mee met de
      volgende FastTrack-berichten. Punten die die buffer verlaten zonder ooit gehoord te zijn (buffer vol of 10 min oud)
      gaan de FIFO-wachtrij in, met hun echte GPS-tijd. `fifo_punten alle|hoofd` (0.9.7, standaard `alle`; status
      `fifo_punten=`, FIFO-menu, Toestellen *Wat gaat in de wachtrij*): `alle` = hoofdpunt én sample-tussenpunten van het
      gemiste bericht (rechte stukken uitgedund door `fifo_dun`; dode zone in detail, bv. 5 min zonder bereik ≈ 30 punten
      vóór het uitdunnen i.p.v. 3), `hoofd` = alleen het hoofdpunt (oud gedrag, minder berichten). Meer punten = meer
      Q-berichten, nog altijd begrensd door `fifo_per_uur`, het pogingenplafond en de niet-vol-regel. SlowTrack logt in
      fifo-modus rechtstreeks in de wachtrij (`slow_log`); `slow_send` wordt niet gebruikt.
    - Wachtrij: `fifo_max` 20..500 (standaard 500; met `hoofd` ~8 à 16 u rijden zonder bereik, met `alle` sneller vol: ~2 u bij een punt per 15 s,
      langer op rechte wegen), in flash (`/mt_fifo.dat`, overleeft
      een herstart). Vol = het punt dat het minst vorm toevoegt (dichtst bij het segment tussen zijn buren) valt weg,
      niet het oudste. `fifo_dun` (standaard 10 m) dunt rechte stukken uit; punten rond stops > 10 min blijven.
    - Leegmaken alleen bij stabiele dekking: SNR ≥ `fifo_snr` (standaard −5 dB), twee keer dekking binnen 60 s, of
      een T1F. Dekking = herhaling van een eigen bericht of een flood-pakket met ≥ 1 hop. Vanaf `fifo_min` punten
      (standaard 5; na een onderbreking vanaf 1), oudste eerst, één Q-bericht per `fifo_gap` (standaard 30 s,
      15 s..5 min), hoogstens `fifo_per_uur` herhaalde Q-berichten per uur (standaard 20, 1..60). Sinds 0.9.2 telt
      `fifo_per_uur` alleen berichten die een repeater herhaalde (gehoord) of die de server met een T1F bevestigde;
      pogingen zonder gehoorde herhaling tellen niet. Vast plafond: hoogstens 2 × `fifo_per_uur` pogingen per uur in
      totaal, om de eigen zendtijd te beschermen (10 % duty cycle in 869,4–869,65 MHz; standaard 40 pogingen ≈ 1 min
      zendtijd). `fifo` toont "N getelde berichten (max P) en M pogingen (max 2P)", `/tracker` "Doorgegeven x / 20" en
      "Pogingen y / 40"; status `gepauzeerd (fifo_per_uur bereikt)` of `gepauzeerd (2x fifo_per_uur pogingen bereikt)`.
      Menulabel: *Herhaalde leegmaakberichten per uur (1-60)*. Binaire extra punten, ~1,5× zoveel als in
      tekst: zo'n 7 à 10 punten per Q-bericht, afhankelijk van de lengte van de trackernaam. Een punt verlaat de wachtrij pas als zijn bericht herhaald gehoord is of de server het
      bevestigt (T1F).
    - Pogingen: niet herhaald = wachttijd van 1, 5, 15 en daarna 60 min. Na `fifo_pogingen` (standaard 3) geparkeerd:
      lagere voorrang en 60 min wachttijd, maar sinds 0.9.4 nooit opgegeven. Een punt verlaat de wachtrij alleen als zijn
      bericht herhaald gehoord is, als de server precies dat bericht bevestigt (T1F per volgnummer), of bij overloop (het
      punt dat het minst vorm toevoegt). In rust of in companionmodus met sterke dekking (SNR ≥ `fifo_snr`) gaat de teller
      van geparkeerde punten terug op 0, hooguit eens per 30 min (meestal thuis).
    - Bevestiging (0.9.4 + server 1.3.1): elk Q-bericht draagt vlag `g`; `f` vraagt een bevestiging voor verstuurde maar
      niet gehoorde punten. De server bevestigt de seqs van de Q-berichten die hij echt kreeg en de tracker wist precies
      die punten. 0.9.0–0.9.3 ("ontvangen tot tijdstip X") wiste ook oudere punten die nooit aankwamen; die firmware krijgt
      geen T1F meer: bijwerken naar 0.9.4. `/tracker` toont het totaal in de FIFO als "nog niet verstuurd · verstuurd,
      onbevestigd · geparkeerd" (geparkeerd in het oranje).
    - Vol en niet-vol (0.9.3): een vol Q-bericht heeft minstens `fifo_per_bericht` punten (≈ 9 met een korte naam) en
      volgt de gewone regels. Een niet-vol bericht (minder punten; neemt alles mee wat wacht) gaat alleen weg als de
      vorige niet-volle poging (geslaagd of niet) én het laatste geslaagde Q-bericht (herhaald of met T1F bevestigd)
      minstens `fifo_wacht` geleden zijn, bij sterke dekking (SNR ≥ `fifo_snr` of een T1F; twee keer dekking binnen 60 s
      volstaat niet) en alleen als de radio toch al wakker is: de tracker wekt of luistert er nooit apart voor. Dat
      voorkomt een stroom berichten met 1 punt (in de praktijk 7 berichten in 6 u met 1 à 5 punten).
    - `fifo_wacht` (`set fifo_wacht <duur>|uit`, 1m..4h, standaard 30m; menu *Wachttijd voor een niet-vol bericht*;
      Toestellen: 15 min, 30 min, 60 min, nooit of anders): niet-vol Q-bericht hooguit 1× per fifo_wacht; `uit` = nooit
      een niet-vol bericht. De oude betekenis (oudste punt ouder dan…, 0.9.1–0.9.2) bestaat niet meer.
    - In rust (langer dan `still_timeout` geen beweging) luistert de tracker alleen 60 s naar repeaters als er minstens
      één vol bericht klaarstaat; geen dekking: opnieuw na 10, 20, 40 en daarna elke 60 min.
    - `fifo` toont ook "laatste geslaagde inhaalbericht: X min geleden; niet-vol bericht weer mogelijk over Y min" en
      "een vol bericht = minstens N punten …"; `dump` (flush) heeft `vol=`, `deel_na=` en `deel_snr=`; `/tracker` toont
      of er een vol bericht klaarstaat en wanneer een niet-vol bericht weer mag.
    - Punten per Q-bericht hangen af van de nodenaam: `"<naam>: "` zit in elk bericht, elke byte telt (emoji tellen voor
      meerdere bytes, 🇧🇪 = 8). De kanaalnaam gaat nooit mee (alleen een hash van 1 byte). Naam van 6 à 11 bytes ≈ 9
      punten per bericht, volle wachtrij (500) ≈ 56 berichten, met de standaardwaarden (20/u) in ~2,1 u leeg; naam van
      24 bytes ≈ 7 punten, ≈ 72 berichten, ~3,1 u. Een korte naam zonder emoji geeft meer punten per bericht.
    - Meshbelasting: elk bericht wordt door meerdere repeaters herhaald, dus hou `fifo_gap` ruim. `fifo` en elke `set` van
      `fifo_max`/`fifo_gap`/`fifo_per_uur`/`fifo_wacht` tonen een berekende samenvatting met het exacte aantal punten per
      bericht voor deze tracker (ook de webinterface).
    - Commando's `fifo` (wachtrij tonen) en `fifo wis ja`. `status`: `track_mode`, `fifo_max`, `fifo_min`, `fifo_gap`,
      `fifo_per_uur`, `fifo`, `fifo_dekking`, `fifo_pogingen`, `fifo_geparkeerd`, `fifo_dun`, `fifo_snr`,
      `fifo_bevestigd`, `fifo_wacht`, `fifo_per_bericht` (0.9.1). Menu: 5 *SlowTrack en trackmodus (FIFO)* → 4 *Trackmodus en FIFO-wachtrij*.
    - Configuratie v6: flashen over 0.8.x behoudt alle instellingen; de nieuwe krijgen hun standaardwaarde.
  - MeshTrack bewaart zijn configuratie en volgnummer op ExtraFS (0.7.1): InternalFS (7 blokken) was vol, waardoor
    0.7.0 niets meer kon bewaren. Oude bestanden worden bij het opstarten verhuisd; `status` toont
    `opslag_intern`/`opslag_extra` in blokken.
  - Genummerd serieel menu en `backup` van de opslag. De sleutel, contacten, kanalen en regio's blijven bij elke
    app-only flash behouden; de opgeslagen instellingen houden hetzelfde formaat (oude velden blijven ongebruikt staan).
  - Klaarmaken via USB: `key import/export`, `chan list/set`, `set name|radio|tx|path_bytes|scope|chan|authkey`.
    Paden altijd 2 bytes per hop.
  - Diagnose (0.7.5): `rxlog aan|uit` toont live in de seriële console elk ontvangen kanaalpakket
    (`rx kanaalpakket hash xx, N hops, N bytes`) en elk ontcijferd kanaalbericht (`rx kanaalbericht op kanaal N: …`),
    tot een herstart. Handig als een SOS-bevestiging (`T1A`) niet aankomt. De tracker bewaart geen log: de regels
    verschijnen alleen live en alleen met het menu dicht (`q`). Er is geen commando `log`.
  - `dump` (0.9.1): de interne toestand machineleesbaar (`mtdump 1` …), voor de webapp `/tracker` (*Tracker live*).
    Sinds 0.9.5 met `req last= answered= ignored=`, sinds 0.9.6 met `lastfix <ts> <lat5> <lon5>`.
  - Laatst gekende fix (0.9.6): bewaard in flash bij de eerste fix, bij het ingaan van rust, voor een herstart of
    uitschakelen, en onderweg hooguit elke 10 min bij > 20 m verplaatsing; overleeft herstart en flashen. `status`:
    `laatste_fix=<leeftijd>` (bv. 2u15m, `-` = nooit). `/tracker`: zonder actuele fix "Laatst gekende positie: X geleden"
    met een grijze gestippelde marker. Tip: na het flashen één keer buiten tot er een fix is.
  - **Locatieverzoeken** (0.9.5): /offline (tab Trackers) en /tracker (Overzicht, alleen via Bluetooth) sturen met
    *Positie vragen* via de verbonden companion op het trackingkanaal `T1R|<*|pk8>|<nonce>` (alle trackers of één).
    Trackers antwoorden met toestand `V` (op verzoek): meteen met een verse fix, anders GPS even aan (hooguit
    `fix_timeout_hb`); geen fix = de laatst gekende positie met het echte tijdstip van die fix (0.9.6), zonder positie alleen
    als er nooit een fix was. Apps en site tonen dat als "laatst gekend, X geleden" met een holle, gestippelde amberkleurige
    ring; de live-marker springt niet terug, het punt komt op zijn echte tijd in het spoor. "Alle" = antwoord willekeurig gespreid over 2–20 s, gericht = 1–3 s. Grenzen: "alle" hooguit 1× per 2 min,
    gericht 1× per 30 s per tracker (de apps houden zich eraan), elke nonce maar één keer. Wie het hoort: companionmodus
    altijd; trackermodus alleen in beweging met `rx_beweging aan` (standaard aan; radio luistert dan onderweg, ~15–20 %
    extra verbruik tijdens het rijden); in rust slaapt de radio. Instellingen `verzoek aan|uit`, `rx_beweging aan|uit` en
    `verzoek_beep aan|uit` (standaard uit: kort herkenbaar deuntje als de tracker een verzoek aanvaardt, ook met de buzzer
    uit, zodat de drager weet dat iemand zijn positie vroeg) (Toestellen, groep *Locatieverzoeken*; menu *Modus en knop* 8, 9 en 10 *Deuntje bij een locatieverzoek*:
    twee snelle stijgende tonen, pauze, één toon). Ook op de kaart van de site, in de kanaalweergave (`?kanaal=`):
    de server stuurt het verzoek via zijn eigen companion op dat kanaal; mag voor wie het kanaal op de kaart ziet
    (`kaart` of `sleutel`); grenzen per kanaal voor alle gebruikers samen ("alle" 1× per 2 min, één tracker 1× per
    30 s); elk verzoek in het auditlog; andere gebruikers met die kaart open krijgen een korte melding; antwoorden als
    live posities met het amberkleurige icoon en een antwoordpaneel. Op de kaarten (/offline, /tracker, site) een
    amberkleurig doelwit met legende *Op verzoek*. Meshbelasting: "alle" op een kanaal met N trackers = N antwoorden.
    Via Bluetooth (companionmodus) aanvaardt de tracker alleen `status`, `fifo` en `dump`: alleen lezen.
- **server/**: FastAPI + meshcore-py (versie 1.4.1).
  - 1.4.1: een gekende tracker die op het kanaal **Public** stuurt (niet klaargemaakt) wordt herkend in de rauwe
    pakketten (companion en openHop): `trackers.public_leak_ts`, `op_public` in de trackerlijst (laatste 24 u), auditregel
    (hoogstens 1 per 6 u per tracker) en meldingsgebeurtenis `op_public`. Die posities worden nooit opgeslagen.
  - 1.4.0: **prioritair** (blauwe lichten): vlag `p` in veld 16 van elk bericht. Een live bericht met `p` maakt de tracker
    prioritair tot ontvangst + `prio_hold_s` (Systeem, standaard 300 s); een live bericht zonder `p` beëindigt het meteen.
    L/Q-punten bewaren de vlag per punt (`positions.prio`) zonder de live-toestand te wijzigen. Trackers krijgen `prio` en
    `prio_until`, sporen `prio` per punt; meldingsgebeurtenissen `prio_start`/`prio_end`; simulators kunnen prioritair
    rijden (`POST /api/sims/{id}/prio`).
  - 1.3.2: toestand `V` (positie op verzoek, firmware 0.9.5), verwerkt zoals `P`: live, ook zonder fix toegelaten, eigen
    meldingsgebeurtenis. Verzoeken `T1R|<*|pk8>|<nonce>` van companions/telefoons op het trackingkanaal negeert de
    server stil (geen onbekend/ongeldig) en telt ze als `loc_request` in de statistieken.
    Vanaf de website: `POST /api/channels/{id}/request` (`{"target": "*" | tracker-id}`) stuurt zelf een `T1R`, met
    limieten per kanaal (alle trackers 1x per 120 s, een tracker 1x per 30 s), auditlog en live-bericht `loc_request`.
  - 1.3.1: **dataverlies-fix FIFO**: exacte bevestiging `T1F|<pk8>:<tag8>:s<seqs>` per ontvangen Q-seq, alleen voor
    trackers met vlag `g` (firmware 0.9.4+). Firmware 0.9.0–0.9.3 krijgt geen T1F meer (de oude "tot ts"-bevestiging
    deed die trackers nooit ontvangen punten wissen). Zie *Berichtprotocol*.
  - 1.3.0: statistiek-API `/api/stats/*` (samenvatting, tijdreeks, verdelingen, FIFO-inhaalwerk, tellers, repeaters;
    zie `docs/api-stats.md`). Nieuwe tabellen `stats_events` (dubbele punten/berichten, ongeldig, onbekend, verstuurde
    `T1A`/`T1F`, DM's van oude firmware) en `message_paths` (pad van elke gehoorde kopie van een trackerbericht, uit de
    rauwe pakketten van de companion en uit de pakketdatabase van openHop, alleen lezen). Kolom `positions.extra` en
    een dekkende index voor de statistieken; de migratie is automatisch en verliest niets.
  - 1.2.0: status `Q` (ingehaalde FIFO-punten, verwerkt zoals `L`), binaire extra punten (`B` + base64url) op `L` en
    `Q`, FIFO-bevestiging `T1F` en de trackervelden `last_fifo_rx`/`last_fifo_ts` (zie *Berichtprotocol*). Volledig
    achterwaarts compatibel: getest met echte berichten van firmware 0.6 tot 0.8.
  - Bij elke verbinding zet de server de openHop-companion op 2-byte padhashes (`path_hash_mode=1`), zoals de
    trackers. Sommige repeaters (bij ons e3d3) sturen pakketten met 1-byte padhashes niet door; daardoor bereikte de
    SOS-bevestiging wel de mesh, maar nooit de tracker. Log: "padhashes van de companion op 2 bytes gezet".
  - Live kaart met MapLibre en eigen pmtiles (geen externe diensten), filters (ook per kanaal, `?kanaal=<id>`),
    favorieten, volgen, sporen per snelheid, meshnodes uit de observer-database van openHop.
  - **Rechten via kanalen**: elke tracker heeft één trackingkanaal (`trackers.channel_id`, ook bijgewerkt uit zijn
    laatste geldige bericht). Gebruikers zitten in een of meer groepen; een groep krijgt per kanaal het niveau
    `kaart` (de trackers van dat kanaal zien) of `sleutel` (ook naam, sleutel en QR-code), of "alle kanalen". Rechten
    en zichtbare trackers worden opgeteld; *Effectieve rechten* toont per recht, kanaal en tracker de herkomst
    (`/api/users/{id}/effective`). Beheerders (`system.manage`) hebben `sleutel` op alles.
  - Pagina *Kanalen* (`/kanalen`, `/api/channels/mine`): de kanalen die je mag lezen; bij `sleutel` met QR-code
    (`meshcore://channel/add`) om het kanaal op een eigen companion te zetten.
  - Gebruikers, groepen, deellinks (kanalen of losse trackers, nooit meer dan de maker ziet), auditlog, logboek met
    filters, GPX/CSV-export.
  - Zones (gedeeld of persoonlijk) en meldingsregels (per kanaal of tracker) die meldingen als DM (privébericht) via de
    mesh naar personen sturen (bv. de gsm van de wachtdienst), met een wachtrij. Dat zijn de enige DM's die de server
    nog verstuurt; trackers sturen nooit DM's naar de server.
  - Simulator: virtuele trackers rijden 24/7 over echte wegen (offline routering over de wegenlaag van de
    kaarttegels), met een historiek in versnelde tijd. Profielen auto, fiets, voet en reiziger; ook zij krijgen een kanaal.
  - Pagina's: Kaart, Logboek, Kanalen, Trackers (lijst met zoeken, filters en kanaal, genegeerde berichten; formulieren
    in een zijpaneel), Toestellen (alles via USB: naam, trackingkanaal, instellingen met voorinstellingen en de groep
    Trackmodus met een live berekening, firmware, klaarmaken en back-ups, terminal, en het tabblad Simulatie: een
    versnelde animatie van classic tegenover fifo met de instellingen van de tracker), Gebruikers, Systeem (meldingen, kanalen, instellingen, companion-QR), Help, en onder *Apps*
    de Offline-kaart (`/offline`) en Tracker live (`/tracker`: PWA die via USB of Bluetooth `status`, `fifo` en `dump` van
    één tracker leest en zijn buffers, wachtrij, tijdlijn en kaart live toont).
  - Firmware flashen in de browser (Web Serial-DFU, `static/dfu.js`): eerst een back-up, alleen de app, daarna
    controle van de pubkey.
  - Nieuw toestel klaarmaken: de server maakt het sleutelpaar en zet sleutel, naam, radio, regio en kanalen via USB op
    het toestel. Back-ups (met privésleutel, formaat van de MeshCore-app) staan versleuteld (AES-GCM) op de server;
    recht `keys.manage`.
  - Offline-app `/offline` (PWA): verbindt via Web Bluetooth met een MeshCore-companion, haalt de kanaalberichten op
    die de companion ontcijferde, leest `T1C|…` volledig offline (met de extra punten), bewaart alles in IndexedDB en
    tekent sporen zoals de online kaart. Werkt zonder account en gebruikt niets uit de database: van de server komen
    alleen kaarten, lettertypes en sprites (openbaar). Kaarten (Limburg, België, Benelux, Frankrijk, Duitsland) staan
    als pmtiles in OPFS en worden samen getoond (van grof naar gedetailleerd gestapeld). Namen uit de contacten van de
    companion; kanaalkeuze uit de kanalen van de companion; kanalen toevoegen via QR (camera of foto) of met naam en
    sleutel. Chat voor alle andere berichten, zelf sturen met instelbare scope (standaard `be`), herhalingen van eigen
    berichten en eigen trackerposities via de ruwe ontvangstlog (push 0x88). Versleepbaar onderpaneel op de gsm.
  - Verloren trackers: een bericht van een verloren tracker geeft de gebeurtenis `lost_seen`; de status blijft.
- **deploy/**: systemd-unit en `deploy.sh` (draait op de openHop-LXC, poort 8090).
- **tools/publish_firmware.py**: zet een firmwarebuild (zip + uf2 + `firmware.json`) in `server/static/firmware/`
  voor de downloads en de webflasher. De binaire bestanden staan niet in git.
- **tools/build_display_tiles.py**: bouwt de weergavekaart (z0–13 voor heel het bronarchief, z14 voor de Benelux)
  zonder veel geheugen.
- **docs/handleiding/**: handleiding voor gebruikers (HTML-bron, schermafbeeldingen en de PDF, ook te downloaden vanaf de
  helppagina van de site) en de snelstart op één pagina.
- **PLAN.md**: ontwerp, berichtprotocol en bewegingsregels.

## Berichtprotocol

Een tracker stuurt op zijn trackingkanaal een groepsbericht `"<naam>: T1C|<pubkey 8 hex>|<tag 8 hex>|<rest>"`, met

```
<rest> = <seq>|<state>|<lat>|<lon>|<alt_m>|<spd_kmh>|<crs_deg>|<bat_pct>|<hdop>|<fix_age_s>|<mode c|t>|<power u|b>|<fix_ts>[|<extra>]
```

`tag` = eerste 4 bytes HMAC-SHA256(authsleutel, `<pubkey8>|<rest>`); de authsleutel (16 bytes) maakt de server per
tracker en gaat via USB naar de tracker. De server luistert op de kanalen uit *Systeem → Kanalen*, controleert de tag
(of aanvaardt per kanaal ook `-`) en zet het trackingkanaal van de tracker. De afzendernaam is vrij (de nodenaam) en
wordt genegeerd: een kanaalbericht bevat geen publieke sleutel, alleen `pubkey8` en de tag identificeren de tracker.

Extra punten (`<extra>`): `~<interval>;dlat,dlon[@s];...`, nieuwste eerst, elk punt als verschil met het vorige in
1e-5 graden; de server berekent de snelheid. Het oudere formaat `dt,dlat,dlon,spd;...` wordt nog gelezen.

`fix_ts` is de GPS-tijd van de fix in unix-seconden; de server gebruikt die als tijdstip van de positie. Zonder
`fix_ts`: de verzendtijd min `fix_age_s`, of de ontvangsttijd als de klok van de tracker niet klopt.

Statussen: `M` beweging, `W` wakker door beweging, `S` stilgevallen, `H` heartbeat, `N` geen fix, `P` handmatig,
`E` SOS, `B` moduswissel of voeding gewijzigd, `L` gelogd punt (SlowTrack, firmware 0.8.0+), `Q` ingehaald punt (FIFO, firmware 0.9.0+),
`V` positie op verzoek (antwoord op `T1R`, firmware 0.9.5+, server 1.3.2+).

SlowTrack (`L`, server 1.1.0+): naast de gewone tracking logt de tracker elke `slow_log` een punt en stuurt die elke
`slow_send` samen in één `L`-bericht (firmware 0.8.0; de server aanvaardt ook meerdere `L`-berichten na elkaar, elk
met eigen seq). Het hoofdpunt is het nieuwste punt van dat bericht, de extra punten zijn oudere (zelfde formaat als
bij `M`). De server zet alles op fix-tijd in hetzelfde spoor en slaat een punt met een fix-tijd
die al bekend is over. Een `L`-punt verplaatst de live-positie alleen als het nieuwer is dan die positie, en
verandert de laatste toestand niet. Geen meldingen voor `L` zelf; zones, batterij en voeding worden alleen getoetst
voor punten die nieuwer zijn dan de live-positie (wel `lost_seen`: het bericht bewijst dat de tracker leeft). Op de
kaart zijn `L`-punten kleinere, lichtere stippen; popup en lijst tonen "SlowTrack: laatste burst <tijd>". Seq-herhalingen worden voor `L` en de gewone tracking apart
gecontroleerd. De trackerlijst toont `last_slow_rx` (ontvangst laatste SlowTrack-bericht) en `last_slow_ts`
(nieuwste gelogde punt).

FIFO (`Q`, server 1.2.0+): gemiste posities die de tracker later alsnog stuurt (store-and-forward); verwerkt zoals
`L` (`last_fifo_rx`/`last_fifo_ts` in de trackerlijst). Extra punten kunnen binair: `B<base64url zonder padding>`,
punten nieuwste eerst, elk drie LEB128-varints t.o.v. het vorige punt: dt (s), dlat en dlon (1e-5 graden, zigzag);
tot 40 punten. Optioneel veld 16 = vlaggen; `f` = bevestiging gevraagd, `g` = exacte bevestiging (firmware 0.9.4
zet `g` op elk Q-bericht). Server 1.3.1+: alleen voor een Q-bericht met `g` antwoordt de server op hetzelfde kanaal
met `T1F|<pk8>:<tag8>:s<seqs>|...` (tot 4 trackers per bericht). `<seqs>` = de seqs van de Q-berichten die de server
van die tracker echt ontving sinds de vorige bevestiging (ook dubbel ontvangen), oplopend, met reeksen `a-b`, bv.
`s12-15,18`; tag8 = HMAC-SHA256(authsleutel van die tracker, `<pk8>|F|s<seqs>`)[:4] hex. De tracker wist enkel die
berichten. Past een lijst niet in de kanaaltekst (140 tekens), dan gaan de laatst ontvangen seqs mee en volgt de rest
in een volgende T1F. **Trackers zonder `g` (firmware 0.9.0–0.9.3) krijgen geen T1F meer**: de oude bevestiging
`<upto_ts>` ("alles tot ts") liet die firmware ook verloren punten wissen. Zij wissen alleen nog wat een repeater
hoorbaar herhaalde. Limieten: per tracker ~20 s na zijn laatste Q-bericht en daarna hoogstens 1 per 10 min, per
kanaal 1 T1F per 60 s, in totaal 20 per uur. Eigen `T1A`/`T1F`-berichten die terugkomen (echo) negeert de server.

Tot firmware 0.6 konden trackers ook `T1|<rest>` als DM naar de server sturen; de server leest dat sinds 1.0 niet
meer en noteert het bij de genegeerde berichten als "oude firmware".

## Server lokaal

```bash
python -m venv .venv && .venv/bin/pip install -r server/requirements.txt pytest httpx
cd server && ../.venv/bin/python -m pytest -q
python -m meshtrack.auth          # wachtwoordhash + sessiesleutel voor config.yaml
MESHTRACK_CONFIG=config.yaml python -m meshtrack.main
```

Sleutels en back-ups worden versleuteld met `auth.keystore_secret` uit `config.yaml`, of anders met
`auth.session_secret`. Wijzig je dat geheim, dan zijn bestaande back-ups niet meer leesbaar.

Let op: openHop laat **één** client per companion toe; een tweede verbinding (lokale test, meshcore-cli) gooit de
draaiende server eruit. Bij de eerste start maakt de server de standaardgroepen aan en een beheerder uit
`config.yaml` (`auth.user` / `auth.password_hash`).

## Kaarttegels

In `tiles_dir`:
- `basemap.pmtiles`: de weergavekaart (Protomaps-schema). Bouwen met
  `python tools/build_display_tiles.py bron.pmtiles basemap.pmtiles` (zet `TMPDIR` op een schijf, niet op tmpfs).
- `roads.pmtiles`: een Benelux-uitsnede tot z14 voor de routering van de simulator
  (`pmtiles extract bron.pmtiles roads.pmtiles --bbox=2.5,49.4,7.3,53.6 --maxzoom=14`).
- `offline/*.pmtiles`: de kaarten voor de offline-app, uitgesneden met `bash tools/build_offline_maps.sh`
  (Limburg z14 84 MB, België z13 286 MB, Benelux z10/z12 44/276 MB, Frankrijk z10/z12 216 MB/1,1 GB,
  Duitsland z10/z12 157/884 MB).
- `fonts/` en `sprites/`.

## Firmware bouwen en flashen

Zie `firmware/platformio.local.ini`: MeshCore v1.17.1 (d929643) naast deze map, env `t1000e_meshtrack`, bouwen op
een ASCII-pad. Flashen altijd app-only via DFU, en eerst een `backup` maken. Daarna
`python tools/publish_firmware.py <versie> "<wijzigingen>"` en uitrollen; de webinterface biedt de nieuwe versie
dan aan.
