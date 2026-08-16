#pragma once

/* StatsPublisher - de ingebouwde webclient van de node, plus het MQTT-kanaal
 * naar een MeshManager-site.
 *
 * Ondanks de naam doet deze module twee samenhangende dingen:
 *
 *  1. Doorsturen via MQTT. Twee soorten berichten:
 *       <prefix>/<node>/stats   periodiek de eigen statistieken (JSON)
 *       <prefix>/<node>/rx      elk ontvangen pakket, ruw en volledig (hex)
 *     De node ontleedt die pakketten bewust niet zelf: hij stuurt de bytes door
 *     zoals ze uit de lucht komen en laat het ontleden aan de site over. Dat
 *     scheelt geheugen hier, en de site kan er later meer uit halen zonder dat
 *     de firmware mee moet.
 *
 *     MQTT in plaats van HTTP: een verbinding blijft openstaan, terwijl elke
 *     HTTP-push een nieuwe (TLS-)sessie opzet. Die stack past niet naast mesh,
 *     WiFi en BLE - dat liet deze node crashen.
 *
 *  2. Een volwaardige chatclient op poort 80: kanalen, contacten, recente
 *     berichten en versturen. Zo is de node bruikbaar vanuit elke browser op
 *     het LAN, zonder de companion-app over BLE of TCP.
 *
 * Zonder ingestelde broker doet de MQTT-helft niets; de webclient blijft werken.
 *
 * === Waarom elk antwoord in een enkele schrijfactie moet passen ===
 *
 * Twee keer heeft dit ons een node gekost, en beide keren om dezelfde reden.
 *
 * Eerst bouwde de pagina zichzelf op in stukjes met sendContent(), met de
 * actuele waarden er al in. Elk stukje is een aparte blokkerende
 * TCP-schrijfactie, en met de latentiepieken van ESP32-wifi bleef de hoofdlus
 * daarin hangen - waarmee ook de mesh stilviel, tot een harde reset.
 *
 * Daarna bleek de tweede helft: WiFiClient::write() stuurt met MSG_DONTWAIT,
 * probeert tien keer met telkens een select() van een seconde, en geeft dan een
 * gedeeltelijk aantal bytes terug. WebServer kijkt daar niet naar. Past een
 * antwoord dus niet in de socket-verzendbuffer van lwip (5760 bytes), dan
 * belooft de kop een Content-Length die nooit gehaald wordt en blijft de client
 * wachten - terwijl de hoofdlus tot tien seconden vastzit.
 *
 * Daarom:
 *  - de pagina is een onveranderlijk blok dat in een keer de deur uit gaat, en
 *    wordt gzip-verstuurd zodat ze ook echt onder die 5760 bytes blijft
 *    (page.html -> gen_page.py -> StatsPage.h);
 *  - alle gegevens komen via kleine JSON-endpoints, nooit in de HTML gebakken;
 *  - lijsten gaan per pagina (STATS_CONTACT_PAGE / STATS_CHANNEL_PAGE) zodat
 *    geen antwoord boven de gedeelde buffer uitkomt;
 *  - elke handler schrijft in een vaste buffer in plaats van een String;
 *  - en CountingWebServer hieronder controleert of het er echt allemaal uit is.
 *
 * === Taken en volgorde ===
 *
 * Alles hier draait vanuit loop(). Mesh-callbacks kopieren enkel naar de
 * ringbuffers hieronder; netwerk-I/O vanuit een radio-callback zou de ontvangst
 * ophouden. Andersom mag wel: de HTTP-handlers mogen de mesh aanroepen, want
 * versturen zet enkel een pakket in de wachtrij.
 *
 * Endpoints (alles JSON, tenzij anders vermeld):
 *   GET  /                de pagina zelf (HTML, gzip, een send_P)
 *   GET  /config.json     identiteit van de node, MQTT-instellingen, status
 *   GET  /stats.json      de eigen radiostatistieken
 *   POST /save            MQTT-instellingen bewaren
 *   POST /test            nu meteen een statistiekenbericht publiceren
 *   GET  /messages.json   recente berichten, ?since=<seq> om incrementeel te pollen
 *   POST /send            bericht sturen; to=c<idx> (kanaal) of k<hex> (contact)
 *   GET  /channels.json   ingestelde groepskanalen, ?off=<n>
 *   POST /channel/add     kanaal joinen of aanmaken (name, psk; lege psk = nieuw)
 *   POST /channel/del     kanaal vergeten (idx)
 *   GET  /contacts.json   een pagina contacten, ?off=<n>
 *   POST /contact/save    per repeater: doorsturen-vinkje en wachtwoord (key, publish, pass)
 *   POST /contact/login   inloggen op een repeater met het bewaarde wachtwoord (key)
 *
 * === De webclient ===
 *
 * page.html is opgezet als een gewone chatclient: links een lijst gesprekken
 * (kanalen en contacten door elkaar), in het midden het gesprek zelf, rechts de
 * details ervan. Die gesprekken bestaan alleen in de browser. Wij houden hier
 * een platte ring van de laatste berichten bij (STATS_MSG_RING) en weten niet
 * eens dat er zoiets als een gesprek is - dat scheelt RAM, en het is de browser
 * die toch al de kanalen- en contactenlijst in handen heeft.
 *
 * De koppeling loopt daarom over de naam in "s": de kanaalnaam bij een
 * kanaalbericht, de naam van de afzender bij een prive-bericht, en bij een
 * eigen bericht de naam van de bestemming. Twee dingen om te weten als je aan
 * een van beide kanten sleutelt:
 *
 *  - copyTrim() kapt "s" af op STATS_MSG_SRC_MAX-1 tekens terwijl
 *    /contacts.json de volledige naam geeft, dus de pagina vergelijkt alleen
 *    het begin van de naam. Wordt STATS_MSG_SRC_MAX ooit ruimer, dan mag die
 *    vergelijking mee;
 *  - een eigen bericht (STATS_MSG_SENT) zegt niet of het naar een kanaal of
 *    naar een contact ging. De pagina zoekt de naam eerst bij de kanalen en
 *    daarna pas bij de contacten. Een kanaal en een contact met dezelfde naam
 *    laten hun eigen berichten dus bij het kanaal belanden; een veld erbij in
 *    het antwoord was dat niet waard.
 *
 * Wie er in een kanaal sprak staat niet in "s" maar voor de tekst zelf:
 * sendGroupMessage() zet "<afzender>: " voor het bericht. De pagina haalt die
 * er weer af om de naam apart te kunnen tonen.
 *
 * De pagina bewaart daarnaast de laatste 300 berichten zelf, in localStorage
 * (sleutel "mh"), zodat een volgend bezoek ook toont wat de kleine ring hier
 * al kwijt is - na een herstart van de node is die zelfs helemaal leeg, en
 * SPIFFS-persistentie is hierboven bewust afgewezen. Drie keuzes daarbij:
 *
 *  - samenvoegen van cache en ring kan niet op "q": die teller begint na een
 *    herstart opnieuw, dus hetzelfde bericht kan onder twee nummers
 *    langskomen. De pagina dedupliceert daarom op
 *    tijdstip+soort+bron+spreker+tekst ('\n' als scheider; jsonStr() laat
 *    stuurtekens vallen, dus die kan nooit in een veld zitten). Echte
 *    dubbelposts overleven dat: afzenders stempelen met
 *    getCurrentTimeUnique(), dus twee keer "ja" draagt twee tijdstippen. Wat
 *    overblijft: twee gelijknamige sprekers die in dezelfde seconde hetzelfde
 *    zeggen in hetzelfde kanaal versmelten tot een bericht - met de velden
 *    die de ring biedt niet te onderscheiden, en dat aanvaarden we. Omdat "q"
 *    geen identiteit meer is, hernummert de pagina lokaal (anders zouden
 *    gecachete nummers van voor een herstart de ongelezen-telling boven de
 *    verse, lage nummers uit tillen); gecachete berichten tellen daarbij
 *    nooit als ongelezen, want die stonden al eens op een scherm;
 *  - 300 is gekozen op teken- en parsewerk, niet op opslag: dat is zo'n 30 kB
 *    JSON waar localStorage 5 MB per origin toelaat, maar de pagina hertekent
 *    de hele lijst per update, dus veel meer bewaren maakt vooral het tekenen
 *    op een telefoon traag;
 *  - geschreven wordt gebundeld: bij het verbergen of verlaten van de pagina
 *    en verder elke halve minuut, nooit per bericht - op een druk kanaal zou
 *    elke regel anders een volledige serialisatie kosten. Alleen het nieuwe
 *    bericht bijschrijven kan niet: localStorage is alles-of-niets per
 *    sleutel. Een crash van de browser kost dus hoogstens een halve minuut
 *    cache, en die berichten staan meestal toch nog in de ring.
 *
 * Restje eerlijkheid: berichten die geen enkele open browser zag en ook uit
 * de ring gevallen zijn, zijn echt weg; en op de naad tussen cache en ring
 * kan de volgorde iets afwijken, want de lijst staat op volgorde van aankomst
 * en sorteren op afzenderklokken zou het erger maken.
 */

#include <Arduino.h>
#include <FS.h>
#include <WebServer.h>
#include <WiFiClient.h>
#include <PubSubClient.h>

#define STATS_CFG_FILE      "/stats_cfg.json"
#define STATS_HOST_MAX      64
#define STATS_USER_MAX      32
#define STATS_PASS_MAX      64
#define STATS_PREFIX_MAX    32

/* Ontvangen pakketten wachten hier tot loop() ze kan versturen; publiceren
 * vanuit de radio-callback zelf zou de ontvangst ophouden.
 *
 * Elke plaats kost een volle MTU (264 bytes met opvulling), wat deze wachtrij
 * tot een van de grootste dingen in de module maakt. Vier plaatsen is een
 * bewuste afweging: doorsturen is toch al "zo goed als het gaat" (de wachtrij
 * wordt in haar geheel weggegooid zodra de broker onbereikbaar is), en op een
 * node die zo krap zit is het RAM meer waard dan het opvangen van pieken.
 * Terug naar 8 kost 1056 bytes statisch RAM. */
#define STATS_RX_QUEUE      4
#define STATS_RX_MAX_LEN    255   // MAX_TRANS_UNIT

/* Elke JSON-handler, de MQTT-payload met statistieken en die met ruwe pakketten
 * schrijven in een gedeelde buffer. Ze kunnen elkaar niet overlappen: de
 * webserver behandelt een verzoek tegelijk, en beide MQTT-schrijvers draaien
 * vanuit loop() buiten handleClient(). Een aparte buffer per gebruiker kostte
 * 4230 bytes; dit kost er 896. De grootte volgt uit de grootste gebruiker, een
 * hex-gecodeerd MTU-pakket (2*255 + kop) - alle lijsten gaan per pagina zodat
 * ze hierin passen. */
#define STATS_IO_BUF        896

/* Recente chatberichten, voor de webclient. Vaste breedte: het afkappen is
 * enkel voor de weergave - de companion-app over BLE krijgt elk bericht nog
 * altijd volledig. Elke plaats kost 76 bytes.
 *
 * De ring is meteen ook de achterstand voor een browser die pas later opent:
 * de pagina haalt bij het laden alles op met since=0. Acht plaatsen bleek
 * daarvoor te krap - op een druk kanaal was een avond aan berichten al
 * overschreven voordat iemand keek. 32 plaatsen kost 2432 bytes statisch RAM
 * (was 608); met de vorige build op 55% RAM is dat ruimschoots te dragen.
 * /messages.json blijft er klein bij: het antwoord pagineert zichzelf al met
 * "more" zodra de gedeelde buffer vol raakt, de pagina haalt de rest meteen op.
 *
 * Bekende beperking: de ring leeft alleen in RAM, dus na een herstart van de
 * node is hij leeg. Hem op SPIFFS bewaren zou dat oplossen, maar dan is elk
 * binnenkomend bericht een flash-schrijfactie - op een node die dag en nacht
 * meshverkeer ziet slijt dat de flash sneller dan de backlog waard is. Bewust
 * niet gebouwd. */
#define STATS_MSG_RING      32
#define STATS_MSG_SRC_MAX   16
#define STATS_MSG_TEXT_MAX  48

// Soorten bericht, zoals ze in het veld "k" naar de browser gaan. Gewone
// defines en geen klasselid, omdat de globale haken hieronder ze ook invullen.
#define STATS_MSG_CHANNEL   0
#define STATS_MSG_DIRECT    1
#define STATS_MSG_SENT      2   // zelf verstuurd, teruggekaatst zodat de pagina beide kanten toont

// Keuzes per repeater, gemaakt in de browser en bewaard op SPIFFS. De contacten
// zelf zitten in de opslag van de mesh; dit onthoudt enkel wat de webclient er
// bovenop zet, voor de handvol repeaters die iemand echt beheert.
#define STATS_REPEATER_FILE "/repeaters.json"
#define STATS_REPEATER_MAX  8
#define STATS_REPEATER_PASS 16   // sendLogin() kapt af op 15 tekens; dat is dus de echte grens

/* Lijsten gaan per pagina, zodat geen enkel antwoord - en dus geen enkele
 * blokkerende TCP-schrijfactie - boven de gedeelde buffer uitkomt. Bewust klein
 * gehouden: MAX_CONTACTS staat hier op 350, en een pagina per verzoek kost veel
 * minder dan een lange schrijfactie die de mesh-lus ophoudt. */
#define STATS_CONTACT_PAGE  5
#define STATS_CHANNEL_PAGE  8

class MyMesh;   // vooruitverwijzing; vermijdt include-cyclus

/* WebServer met een teller erop, want de gewone kijkt niet naar zijn eigen
 * schrijfacties.
 *
 * WiFiClient::write() stuurt met MSG_DONTWAIT, probeert tien keer en geeft dan
 * terug hoeveel bytes het wel geworden zijn. WebServer negeert die waarde: de
 * kop belooft dan een Content-Length die nooit gehaald wordt en de client wacht
 * tot zijn tijdslimiet - precies waarmee deze node ons twee keer voor raadsels
 * zette. _currentClientWrite is protected en virtual, dus we kunnen meekijken
 * zonder de webserver zelf aan te passen. */
class CountingWebServer : public WebServer {
public:
  CountingWebServer(int port) : WebServer(port), _asked(0), _done(0) {}

  void beginWriteTracking() { _asked = _done = 0; }
  bool shortWrite() const { return _done < _asked; }
  size_t written() const { return _done; }

protected:
  size_t _currentClientWrite(const char* b, size_t l) override {
    size_t w = WebServer::_currentClientWrite(b, l);
    _asked += l; _done += w;
    return w;
  }
  size_t _currentClientWrite_P(PGM_P b, size_t l) override {
    size_t w = WebServer::_currentClientWrite_P(b, l);
    _asked += l; _done += w;
    return w;
  }

private:
  size_t _asked, _done;
};

/* Het MQTT-topicvoorvoegsel, sinds de hernoeming naar MeshManager.
 * 'meshcore' was de naam van het protocol en van een ander project; dit is
 * er een die dit project zelf bezit. De server luistert tijdens de overgang
 * naar allebei, dus een companion die nog niet om is blijft binnenkomen.
 *
 * Anders dan op de repeater verhuist dit hier NIET vanzelf. Een companion
 * hangt niet op een dak maar op een bureau, hij wordt met de hand ingesteld
 * en zijn beheerpagina staat een klik verderop; een stille wijziging is daar
 * meer verrassing dan winst. Op de repeater is de afweging omgekeerd, en
 * daar staat ze uitgeschreven in loadConfig() van MeshManagerNet.cpp. */
#define STATS_PREFIX_DEFAULT  "meshmanager"

class StatsPublisher {
public:
  struct Config {
    char host[STATS_HOST_MAX];      // MQTT-broker, bv. 10.0.0.5
    uint16_t port;                  // 1883
    char user[STATS_USER_MAX];
    char pass[STATS_PASS_MAX];
    char prefix[STATS_PREFIX_MAX];  // topicprefix, standaard "meshmanager"
    uint32_t interval_secs;         // hoe vaak statistieken sturen
    bool enabled;
    bool forward_rx;                // ook elk ontvangen pakket doorsturen
  };

  StatsPublisher() : _server(80), _mqtt(_net), _fs(nullptr), _mesh(nullptr),
                     _last_push(0), _last_connect_try(0), _push_count(0),
                     _rx_count(0), _drop_count(0), _fail_count(0),
                     _rx_head(0), _rx_tail(0), _msg_head(0), _msg_seq(0),
                     _num_repeaters(0), _started(false) {
    memset(&_cfg, 0, sizeof(_cfg));
    _cfg.port = 1883;
    _cfg.interval_secs = 300;
    _cfg.enabled = false;
    _cfg.forward_rx = true;
    strcpy(_cfg.prefix, STATS_PREFIX_DEFAULT);
    _last_error[0] = 0;
    _node_hex[0] = 0;
    memset(_msgs, 0, sizeof(_msgs));
    memset(_repeaters, 0, sizeof(_repeaters));
  }

  void begin(FS& fs, MyMesh* mesh);
  void loop();

  /* Aangeroepen vanuit de ontvangstlus voor elk binnengekomen pakket. Kopieert
   * alleen naar de wachtrij - versturen gebeurt later in loop(). */
  void queueRawPacket(float snr, float rssi, const uint8_t raw[], int len);

  /* Aangeroepen vanuit de berichtcallbacks van de mesh. "kind" onderscheidt een
   * binnengekomen kanaalbericht van een prive-bericht en van wat we zelf
   * stuurden, zodat de browser ze anders kan tonen. Enkel kopieren; geen I/O. */
  void noteMessage(uint8_t kind, const char* src, uint32_t timestamp, const char* text);

  const Config& getConfig() const { return _cfg; }

private:
  struct RxItem {
    uint32_t ms;
    int16_t snr4;     // SNR maal 4, zoals de radio hem geeft
    int16_t rssi;
    uint8_t len;
    uint8_t data[STATS_RX_MAX_LEN];
  };

  struct MsgItem {
    uint32_t seq;             // loopt op; laat de browser incrementeel pollen
    uint32_t timestamp;       // unixtijd, volgens de klok van de afzender
    uint8_t kind;
    char src[STATS_MSG_SRC_MAX];
    char text[STATS_MSG_TEXT_MAX];
  };

  // Een repeater wordt herkend aan de eerste 6 bytes van zijn publieke sleutel,
  // dezelfde prefix waarmee de mesh contacten opzoekt.
  struct RepeaterOpt {
    uint8_t key[6];
    bool publish;                     // gegevens van deze repeater naar de site sturen
    char pass[STATS_REPEATER_PASS];   // admin- of read/write-wachtwoord
  };

  CountingWebServer _server;
  WiFiClient _net;
  PubSubClient _mqtt;
  FS* _fs;
  MyMesh* _mesh;
  Config _cfg;
  unsigned long _last_push;
  unsigned long _last_connect_try;
  uint32_t _push_count;
  uint32_t _rx_count;      // doorgestuurde pakketten
  uint32_t _drop_count;    // pakketten die de wachtrij niet haalden
  uint32_t _fail_count;
  RxItem _rx_queue[STATS_RX_QUEUE];
  volatile uint8_t _rx_head, _rx_tail;
  MsgItem _msgs[STATS_MSG_RING];
  uint8_t _msg_head;       // volgende plaats om te overschrijven; de ring loopt nooit leeg
  uint32_t _msg_seq;
  RepeaterOpt _repeaters[STATS_REPEATER_MAX];
  uint8_t _num_repeaters;
  bool _started;
  char _last_error[64];
  char _node_hex[13];      // pubkey-prefix, gebruikt in de topics

  void loadConfig();
  void saveConfig();
  void loadRepeaters();
  void saveRepeaters();
  RepeaterOpt* findRepeater(const uint8_t key[6]);
  RepeaterOpt* findOrAddRepeater(const uint8_t key[6]);

  bool ensureConnected();
  bool publishStats();
  void drainRxQueue();
  void topicFor(const char* leaf, char* out, size_t max);

  // Leest de "k<hex>"-notatie die de pagina voor een contact gebruikt.
  bool parseKeyArg(const char* name, uint8_t out[6]);

  /* De enige uitgang voor JSON-antwoorden. Gebruikt send_P met een expliciete
   * lengte, want de gewone send() giet de body eerst in een String - een
   * heap-kopie van het hele antwoord, bovenop de buffer die we al hebben. Op een
   * node waar de heap het knelpunt is, is dat net wat we niet willen; de
   * framework-code waarschuwt er zelf voor ("Use send_P for long arrays").
   * send_P werkt op ESP32 ook gewoon op RAM. */
  void sendJson(const char* body, size_t len);
  void sendJson(const char* body) { sendJson(body, strlen(body)); }

  // Sluit de verbinding als het antwoord er niet volledig uit is gekomen.
  void finishResponse(unsigned long t0, size_t len);

  void handleRoot();
  void handleConfigJson();
  void handleSave();
  void handleTest();
  void handleStatsJson();
  void handleMessagesJson();
  void handleSend();
  void handleChannelsJson();
  void handleChannelAdd();
  void handleChannelDel();
  void handleContactsJson();
  void handleContactSave();
  void handleContactLogin();
};

/* Globale haken zodat MyMesh niets van deze klasse hoeft te weten. Worden in
 * begin() ingevuld; zolang die niet liep, doet aanroepen niets. */
void meshmanager_on_raw_packet(float snr, float rssi, const uint8_t raw[], int len);
void meshmanager_on_channel_msg(const char* channel_name, uint32_t timestamp, const char* text);
void meshmanager_on_direct_msg(const char* sender_name, uint32_t timestamp, const char* text);
