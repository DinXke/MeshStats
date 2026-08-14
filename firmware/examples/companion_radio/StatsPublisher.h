#pragma once

/* StatsPublisher - de ingebouwde webclient van de node, plus het MQTT-kanaal
 * naar een MeshStats-site.
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
 * === Waarom de pagina een statische PROGMEM-string is ===
 *
 * Een eerdere versie bouwde de HTML in stukjes op met sendContent(), met de
 * actuele waarden er al in. Elk zo'n stukje is een aparte blokkerende
 * TCP-schrijfactie, en met de latentiepieken van ESP32-wifi (modem-sleep) bleef
 * de hoofdlus daarin hangen - waarmee ook de mesh stilviel, tot een harde
 * reset. Dat heeft ons een vastgelopen node gekost.
 *
 * Dus: de pagina is een enkele onveranderlijke string die in een send_P() gaat,
 * en alle gegevens bereiken de browser via kleine JSON-endpoints. Nieuwe UI =
 * uitbreiding van die ene string plus een endpoint. Nooit gegevens in de HTML
 * bakken, nooit meer dan een sendContent().
 *
 * Om dezelfde reden zijn ook de JSON-antwoorden begrensd. Lijsten gaan per
 * pagina (STATS_CONTACT_PAGE / STATS_CHANNEL_PAGE), zodat geen enkel antwoord
 * boven de gedeelde buffer uitkomt, en elke handler schrijft in een vaste
 * buffer in plaats van een String op te bouwen.
 *
 * === Taken en volgorde ===
 *
 * Alles hier draait vanuit loop(). Mesh-callbacks kopieren enkel naar de
 * ringbuffers hieronder; netwerk-I/O vanuit een radio-callback zou de ontvangst
 * ophouden. Andersom mag wel: de HTTP-handlers mogen de mesh aanroepen, want
 * versturen zet enkel een pakket in de wachtrij.
 *
 * Endpoints (alles JSON, tenzij anders vermeld):
 *   GET  /                de pagina zelf (HTML, een send_P)
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

/* Recente chatberichten, voor de webclient. Bewust klein en met vaste breedte:
 * deze node draait mesh, WiFi en BLE naast elkaar, dus RAM is het schaarse
 * goed. Het afkappen is enkel voor de weergave - de companion-app over BLE
 * krijgt elk bericht nog altijd volledig. Elke plaats kost 76 bytes. */
#define STATS_MSG_RING      8
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

class StatsPublisher {
public:
  struct Config {
    char host[STATS_HOST_MAX];      // MQTT-broker, bv. 10.0.0.5
    uint16_t port;                  // 1883
    char user[STATS_USER_MAX];
    char pass[STATS_PASS_MAX];
    char prefix[STATS_PREFIX_MAX];  // topicprefix, standaard "meshcore"
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
    strcpy(_cfg.prefix, "meshcore");
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

  WebServer _server;
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
void meshstats_on_raw_packet(float snr, float rssi, const uint8_t raw[], int len);
void meshstats_on_channel_msg(const char* channel_name, uint32_t timestamp, const char* text);
void meshstats_on_direct_msg(const char* sender_name, uint32_t timestamp, const char* text);
