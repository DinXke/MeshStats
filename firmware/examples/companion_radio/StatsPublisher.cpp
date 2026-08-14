#include "StatsPublisher.h"
#include "MyMesh.h"
#include "StatsPage.h"    // gegenereerd uit page.html door gen_page.py
#include <WiFi.h>

/* base64.hpp staat volledig in de header maar is niet inline, dus die hier ook
 * includeren naast BaseChatMesh.cpp geeft dubbele symbolen bij het linken. We
 * hebben maar een functie nodig; die kondigen we dus zelf aan. */
extern unsigned int encode_base64(const unsigned char input[], unsigned int input_length,
                                  unsigned char output[]);

static StatsPublisher* _instance = nullptr;

void meshstats_on_raw_packet(float snr, float rssi, const uint8_t raw[], int len) {
  if (_instance) _instance->queueRawPacket(snr, rssi, raw, len);
}

void meshstats_on_channel_msg(const char* channel_name, uint32_t timestamp, const char* text) {
  if (_instance) _instance->noteMessage(STATS_MSG_CHANNEL, channel_name, timestamp, text);
}

void meshstats_on_direct_msg(const char* sender_name, uint32_t timestamp, const char* text) {
  if (_instance) _instance->noteMessage(STATS_MSG_DIRECT, sender_name, timestamp, text);
}

static const char HEXCHARS[] = "0123456789abcdef";

/* De enige buffer waarin deze module schrijft: JSON-antwoorden, de
 * MQTT-payload met statistieken en de hex-gecodeerde ruwe pakketten delen hem.
 * Zie STATS_IO_BUF in de header voor waarom dat veilig is en wat het scheelt. */
static char io_buf[STATS_IO_BUF];

/* TIJDELIJK - diagnostiek voor het vastlopen van grotere antwoorden.
 *
 * WiFiClient::write() stuurt met MSG_DONTWAIT en geeft het na tien pogingen op;
 * de webserver kijkt niet naar hoeveel er echt geschreven is. Lukt het niet in
 * een keer, dan staat er wel een Content-Length in de kop maar volgt de rest
 * nooit - en dan blijft de client precies zo hangen als we zien. Elke poging
 * wacht bovendien tot een seconde in select(), dus zo'n gedeeltelijke schrijving
 * houdt meteen ook de hoofdlus (en dus de mesh) tien seconden op.
 *
 * Deze regels tonen per verzoek de vrije heap en de lengte, en hoe lang de
 * schrijfactie duurde. Zet STATS_TRACE op 0 zodra de oorzaak vastligt. */
#define STATS_TRACE 1
#if STATS_TRACE
  #define TRACE(...)  Serial.printf(__VA_ARGS__)
  // Eerste regel van elke handler. Ontbreekt hij, dan is het verzoek nooit bij
  // ons aangekomen (verbinding weg) in plaats van dat de handler bleef hangen.
  #define TRACE_ENTER() TRACE("[stats] %s binnen heap=%u\n", \
                              _server.uri().c_str(), (unsigned)ESP.getFreeHeap())
#else
  #define TRACE(...)
  #define TRACE_ENTER()
#endif

// ------------------------------------------------------------------ hulpjes

static int hexVal(char c) {
  if (c >= '0' && c <= '9') return c - '0';
  if (c >= 'a' && c <= 'f') return c - 'a' + 10;
  if (c >= 'A' && c <= 'F') return c - 'A' + 10;
  return -1;
}

// Leest 12 hex-tekens in een pubkey-prefix van 6 bytes. Geeft false bij elk
// niet-hex teken, zodat een rare URL nooit in de contactentabel kan grijpen.
static bool hexToKey(const char* s, uint8_t out[6]) {
  for (int i = 0; i < 6; i++) {
    int hi = hexVal(s[i * 2]), lo = hexVal(s[i * 2 + 1]);
    if (hi < 0 || lo < 0) return false;
    out[i] = (uint8_t)((hi << 4) | lo);
  }
  return true;
}

static void keyToHex(const uint8_t key[6], char out[13]) {
  for (int i = 0; i < 6; i++) {
    out[i * 2] = HEXCHARS[key[i] >> 4];
    out[i * 2 + 1] = HEXCHARS[key[i] & 0x0F];
  }
  out[12] = 0;
}

/* Kopieert src naar dest als inhoud van een JSON-string. Nodenamen en
 * berichttekst komen uit de lucht en kunnen dus aanhalingstekens of
 * stuurtekens bevatten die de parser van de browser zouden breken. dest moet
 * 2*strlen(src)+1 bytes groot zijn; aanroepers doen dat, zodat hier nooit
 * midden in een teken afgekapt wordt. Bytes boven 0x7F gaan ongemoeid door,
 * zodat UTF-8 heel blijft. */
static void jsonStr(char* dest, size_t max, const char* src) {
  size_t o = 0;
  for (const char* p = src; *p && o + 2 < max; p++) {
    uint8_t c = (uint8_t)*p;
    if (c == '"' || c == '\\') { dest[o++] = '\\'; dest[o++] = (char)c; }
    else if (c >= 0x20) dest[o++] = (char)c;
  }
  dest[o] = 0;
}

/* Afkappende kopie die geen UTF-8-reeks doormidden snijdt: een half teken
 * toont de browser als vervangingsteken en kan bovendien als ongeldige byte in
 * een JSON-string belanden. */
static void copyTrim(char* dest, size_t max, const char* src) {
  size_t n = strlen(src);
  if (n > max - 1) {
    n = max - 1;
    while (n > 0 && (((uint8_t)src[n]) & 0xC0) == 0x80) n--;  // terug naar een kopbyte
  }
  memcpy(dest, src, n);
  dest[n] = 0;
}

// ---------------------------------------------------------------- config i/o

void StatsPublisher::loadConfig() {
  if (!_fs) return;
  File f = _fs->open(STATS_CFG_FILE, "r");
  if (!f) return;

  // Heel eenvoudige parser: we schrijven het bestand zelf, dus het formaat ligt
  // vast. {"host":"...","port":1883,"user":"...","pass":"...", ...}
  String s = f.readString();
  f.close();

  auto grab = [&](const char* key, char* out, size_t max) {
    String pat = String("\"") + key + "\":\"";
    int i = s.indexOf(pat);
    if (i < 0) return;                  // ontbreekt: laat de standaard staan
    i += pat.length();
    int j = s.indexOf('"', i);
    if (j < 0) return;
    String v = s.substring(i, j);
    strncpy(out, v.c_str(), max - 1);
    out[max - 1] = 0;
  };
  auto num = [&](const char* key, long fallback) -> long {
    String pat = String("\"") + key + "\":";
    int i = s.indexOf(pat);
    if (i < 0) return fallback;
    return s.substring(i + pat.length()).toInt();
  };

  grab("host", _cfg.host, STATS_HOST_MAX);
  grab("user", _cfg.user, STATS_USER_MAX);
  grab("pass", _cfg.pass, STATS_PASS_MAX);
  grab("prefix", _cfg.prefix, STATS_PREFIX_MAX);
  if (_cfg.prefix[0] == 0) strcpy(_cfg.prefix, "meshcore");

  _cfg.port = (uint16_t)num("port", 1883);
  if (_cfg.port == 0) _cfg.port = 1883;

  _cfg.interval_secs = (uint32_t)num("interval", 300);
  if (_cfg.interval_secs < 30) _cfg.interval_secs = 30;

  _cfg.enabled = num("enabled", 0) != 0;
  _cfg.forward_rx = num("forward_rx", 1) != 0;
}

void StatsPublisher::saveConfig() {
  if (!_fs) return;
  File f = _fs->open(STATS_CFG_FILE, "w");
  if (!f) return;
  f.printf("{\"host\":\"%s\",\"port\":%u,\"user\":\"%s\",\"pass\":\"%s\","
           "\"prefix\":\"%s\",\"interval\":%u,\"enabled\":%d,\"forward_rx\":%d}",
           _cfg.host, (unsigned)_cfg.port, _cfg.user, _cfg.pass, _cfg.prefix,
           (unsigned)_cfg.interval_secs, _cfg.enabled ? 1 : 0,
           _cfg.forward_rx ? 1 : 0);
  f.close();
}

// ------------------------------------------------------ repeater-voorkeuren

/* Staat in een apart bestand van de MQTT-instellingen, zodat een repeater die
 * iemand beheert een wissel van broker overleeft, en omgekeerd. Zelfde
 * handgeschreven parser als hierboven: wij zijn de enige schrijver, dus de
 * indeling ligt vast. */
void StatsPublisher::loadRepeaters() {
  _num_repeaters = 0;
  if (!_fs) return;
  File f = _fs->open(STATS_REPEATER_FILE, "r");
  if (!f) return;
  String s = f.readString();
  f.close();

  int pos = 0;
  while (_num_repeaters < STATS_REPEATER_MAX) {
    int k = s.indexOf("\"k\":\"", pos);
    if (k < 0) break;
    k += 5;
    if (k + 12 > (int)s.length()) break;

    RepeaterOpt& r = _repeaters[_num_repeaters];
    if (!hexToKey(s.c_str() + k, r.key)) break;

    int end = s.indexOf('}', k);
    if (end < 0) end = s.length();
    String rec = s.substring(k, end);

    r.publish = rec.indexOf("\"p\":1") >= 0;
    r.pass[0] = 0;
    int w = rec.indexOf("\"w\":\"");
    if (w >= 0) {
      w += 5;
      int q = rec.indexOf('"', w);
      if (q > w) {
        String v = rec.substring(w, q);
        strncpy(r.pass, v.c_str(), STATS_REPEATER_PASS - 1);
        r.pass[STATS_REPEATER_PASS - 1] = 0;
      }
    }
    _num_repeaters++;
    pos = end + 1;
  }
}

void StatsPublisher::saveRepeaters() {
  if (!_fs) return;
  File f = _fs->open(STATS_REPEATER_FILE, "w");
  if (!f) return;
  f.print("{\"r\":[");
  for (uint8_t i = 0; i < _num_repeaters; i++) {
    char hex[13];
    keyToHex(_repeaters[i].key, hex);
    f.printf("%s{\"k\":\"%s\",\"p\":%d,\"w\":\"%s\"}", i ? "," : "", hex,
             _repeaters[i].publish ? 1 : 0, _repeaters[i].pass);
  }
  f.print("]}");
  f.close();
}

StatsPublisher::RepeaterOpt* StatsPublisher::findRepeater(const uint8_t key[6]) {
  for (uint8_t i = 0; i < _num_repeaters; i++) {
    if (memcmp(_repeaters[i].key, key, 6) == 0) return &_repeaters[i];
  }
  return nullptr;
}

StatsPublisher::RepeaterOpt* StatsPublisher::findOrAddRepeater(const uint8_t key[6]) {
  RepeaterOpt* r = findRepeater(key);
  if (r) return r;
  if (_num_repeaters >= STATS_REPEATER_MAX) return nullptr;
  r = &_repeaters[_num_repeaters++];
  memset(r, 0, sizeof(*r));
  memcpy(r->key, key, 6);
  return r;
}

// ------------------------------------------------------------ berichtenring

void StatsPublisher::noteMessage(uint8_t kind, const char* src, uint32_t timestamp,
                                 const char* text) {
  if (!text) return;

  // Draait bij ontvangen berichten binnen een mesh-callback: kopieren en meteen
  // terug, hier nooit het netwerk aanraken.
  MsgItem& m = _msgs[_msg_head];
  m.seq = ++_msg_seq;
  m.timestamp = timestamp;
  m.kind = kind;
  copyTrim(m.src, sizeof(m.src), src ? src : "?");
  copyTrim(m.text, sizeof(m.text), text);
  _msg_head = (uint8_t)((_msg_head + 1) % STATS_MSG_RING);
}

// ------------------------------------------------------------------ publiceren

void StatsPublisher::topicFor(const char* leaf, char* out, size_t max) {
  snprintf(out, max, "%s/%s/%s", _cfg.prefix,
           _node_hex[0] ? _node_hex : "node", leaf);
}

bool StatsPublisher::ensureConnected() {
  if (_mqtt.connected()) return true;
  if (_cfg.host[0] == 0 || WiFi.status() != WL_CONNECTED) return false;

  // Niet blijven hameren op een broker die niet opneemt: dat kostte eerder de
  // hele node zijn responsiviteit.
  if (_last_connect_try && millis() - _last_connect_try < 15000UL) return false;
  _last_connect_try = millis();

  char client_id[32];
  snprintf(client_id, sizeof(client_id), "meshcore-%s",
           _node_hex[0] ? _node_hex : "node");

  bool ok = _cfg.user[0]
    ? _mqtt.connect(client_id, _cfg.user, _cfg.pass)
    : _mqtt.connect(client_id);

  if (ok) {
    _last_error[0] = 0;
  } else {
    _fail_count++;
    snprintf(_last_error, sizeof(_last_error), "MQTT-verbinding faalde (rc %d)",
             _mqtt.state());
  }
  return ok;
}

bool StatsPublisher::publishStats() {
  if (!_mesh || !ensureConnected()) return false;

  size_t n = _mesh->fillStatsJson(io_buf, sizeof(io_buf));
  if (n == 0) return false;

  char topic[96];
  topicFor("stats", topic, sizeof(topic));

  bool ok = _mqtt.publish(topic, (const uint8_t *)io_buf, n, false);
  if (ok) {
    _push_count++;
    _last_error[0] = 0;
  } else {
    _fail_count++;
    snprintf(_last_error, sizeof(_last_error), "publiceren van stats faalde");
  }
  return ok;
}

void StatsPublisher::queueRawPacket(float snr, float rssi, const uint8_t raw[], int len) {
  if (!_started || !_cfg.enabled || !_cfg.forward_rx) return;
  if (len <= 0 || len > STATS_RX_MAX_LEN) return;

  uint8_t next = (uint8_t)((_rx_head + 1) % STATS_RX_QUEUE);
  if (next == _rx_tail) {   // wachtrij vol: liever een pakket kwijt dan ontvangst ophouden
    _drop_count++;
    return;
  }

  RxItem& it = _rx_queue[_rx_head];
  it.ms = millis();
  it.snr4 = (int16_t)(snr * 4);
  it.rssi = (int16_t)rssi;
  it.len = (uint8_t)len;
  memcpy(it.data, raw, len);
  _rx_head = next;
}

void StatsPublisher::drainRxQueue() {
  if (_rx_head == _rx_tail) return;
  if (!ensureConnected()) {
    // Geen verbinding: gooi de wachtrij leeg, anders sturen we straks een berg
    // verouderde pakketten na.
    while (_rx_tail != _rx_head) {
      _rx_tail = (uint8_t)((_rx_tail + 1) % STATS_RX_QUEUE);
      _drop_count++;
    }
    return;
  }

  char topic[96];
  topicFor("rx", topic, sizeof(topic));

  /* Precies een pakket per ronde. Elke publish() is een blokkerende
   * TCP-schrijfactie die op een druk netwerk honderden milliseconden kan duren;
   * er vier na elkaar wegwerken hield de mesh-lus soms bijna een seconde op.
   * Een per ronde houdt het ergste geval begrensd en leegt de wachtrij nog
   * altijd snel, want loop() draait duizenden keren per seconde. */
  {
    RxItem& it = _rx_queue[_rx_tail];

    int n = snprintf(io_buf, sizeof(io_buf),
      "{\"t\":%u,\"snr\":%.2f,\"rssi\":%d,\"len\":%u,\"raw\":\"",
      (unsigned)it.ms, it.snr4 / 4.0f, (int)it.rssi, (unsigned)it.len);

    // De buffer is op een volle MTU in hex berekend, maar stop liever dan eroverheen
    // te schrijven als dat ooit niet meer klopt.
    if (n < 0 || (size_t)n + it.len * 2 + 2 > sizeof(io_buf)) {
      _rx_tail = (uint8_t)((_rx_tail + 1) % STATS_RX_QUEUE);
      _drop_count++;
      return;
    }

    for (uint8_t i = 0; i < it.len; i++) {
      io_buf[n++] = HEXCHARS[it.data[i] >> 4];
      io_buf[n++] = HEXCHARS[it.data[i] & 0x0F];
    }
    io_buf[n++] = '"';
    io_buf[n++] = '}';

    if (_mqtt.publish(topic, (const uint8_t *)io_buf, n, false)) {
      _rx_count++;
    } else {
      _fail_count++;
      snprintf(_last_error, sizeof(_last_error), "publiceren van pakket faalde");
      return;     // laat het pakket staan; volgende ronde opnieuw
    }
    _rx_tail = (uint8_t)((_rx_tail + 1) % STATS_RX_QUEUE);
  }
}

// ---------------------------------------------------------------- webserver

/* De pagina zit niet meer als string in dit bestand: ze staat in page.html en
 * wordt door gen_page.py gzip-gecomprimeerd tot StatsPage.h. Dat blijft precies
 * hetzelfde principe als voorheen - een onveranderlijk blok dat in een enkele
 * schrijfactie de deur uit gaat, met alle gegevens via JSON-endpoints - maar dan
 * klein genoeg om ook echt in een keer te passen. Wie de pagina wil aanpassen
 * bewerkt page.html en draait het script; nooit StatsPage.h met de hand.
 *
 * Nooit gegevens in de HTML bakken, nooit meer dan een schrijfactie. */

/* Sluit af na een antwoord dat er niet helemaal uit is gekomen.
 *
 * De kop belooft een Content-Length; haalt de schrijfactie die niet, dan blijft
 * de client wachten op een rest die nooit komt. Eeuwig wachten is de slechtste
 * afloop, dus dan verbreken we de verbinding: de browser krijgt meteen een
 * duidelijke fout in plaats van een tijdslimiet van een minuut. De seriële regel
 * maakt zichtbaar wat het framework stil laat passeren.
 *
 * Let op wat dit wel en niet bewijst. De teller telt de bytes die
 * WiFiClient::write() heeft aangenomen, en dat is de verzendbuffer van lwip -
 * niet de client. Valt de WiFi weg nadat lwip de bytes heeft overgenomen, dan
 * gooit de stack ze alsnog weg en krijgt de client niets, terwijl het hier
 * "volledig verstuurd" heet. Geen ONVOLLEDIG betekent dus: het is de stack in
 * gegaan, niet: het is aangekomen. */
void StatsPublisher::finishResponse(unsigned long t0, size_t len) {
  if (_server.shortWrite()) {
    TRACE(" ONVOLLEDIG %u/%u na %lums heap=%u\n", (unsigned)_server.written(),
          (unsigned)len, millis() - t0, (unsigned)ESP.getFreeHeap());
    _server.client().stop();
  } else {
    TRACE(" -> %lums heap=%u\n", millis() - t0, (unsigned)ESP.getFreeHeap());
  }
}

void StatsPublisher::sendJson(const char* body, size_t len) {
  TRACE("[stats] %s len=%u heap=%u", _server.uri().c_str(), (unsigned)len,
        (unsigned)ESP.getFreeHeap());
  unsigned long t0 = millis();
  _server.beginWriteTracking();
  _server.send_P(200, "application/json", body, len);
  finishResponse(t0, len);
}

void StatsPublisher::handleRoot() {
  TRACE_ENTER();
  /* Gzip: zie StatsPage.h. Content-Length wordt hier de gecomprimeerde lengte,
   * want dat is wat er daadwerkelijk over de lijn gaat. */
  TRACE("[stats] / gz=%u heap=%u", (unsigned)PAGE_GZ_LEN,
        (unsigned)ESP.getFreeHeap());
  unsigned long t0 = millis();
  _server.beginWriteTracking();
  _server.sendHeader("Content-Encoding", "gzip");
  _server.send_P(200, "text/html; charset=utf-8", (PGM_P)PAGE_GZ, PAGE_GZ_LEN);
  finishResponse(t0, PAGE_GZ_LEN);
}

void StatsPublisher::handleConfigJson() {
  TRACE_ENTER();
  char name[65];
  jsonStr(name, sizeof(name), _mesh ? _mesh->getNodeName() : "MeshCore node");

  int n = snprintf(io_buf, sizeof(io_buf),
    "{\"name\":\"%s\",\"ip\":\"%s\",\"node\":\"%s\","
    "\"cfg\":{\"host\":\"%s\",\"port\":%u,\"user\":\"%s\",\"prefix\":\"%s\","
    "\"interval\":%u,\"enabled\":%d,\"forward_rx\":%d},"
    "\"status\":{\"Broker\":\"%s\",\"Statistieken verstuurd\":%u,"
    "\"Pakketten doorgestuurd\":\"%u (%u niet gehaald)\","
    // Het grootste vrije blok zegt meer dan het totaal: lwip heeft aaneengesloten
    // ruimte nodig voor zijn pbufs, en bij fragmentatie faalt dat terwijl het
    // totaal nog ruim lijkt.
    "\"Fouten\":\"%u %s\",\"Vrij geheugen\":\"%u bytes\","
    "\"Grootste vrije blok\":\"%u bytes\"}}",
    name, WiFi.localIP().toString().c_str(), _node_hex,
    _cfg.host, (unsigned)_cfg.port, _cfg.user, _cfg.prefix,
    (unsigned)_cfg.interval_secs, _cfg.enabled ? 1 : 0, _cfg.forward_rx ? 1 : 0,
    _mqtt.connected() ? "verbonden" : (_cfg.host[0] ? "niet verbonden" : "niet ingesteld"),
    (unsigned)_push_count, (unsigned)_rx_count, (unsigned)_drop_count,
    (unsigned)_fail_count, _last_error, (unsigned)ESP.getFreeHeap(),
    (unsigned)ESP.getMaxAllocHeap());

  // Dit is het enige antwoord waarvan de lengte van ingetypte tekst afhangt
  // (broker, gebruiker, prefix) en niet van onze eigen paginering. Zijn die lang
  // genoeg om de gedeelde buffer te vullen, stuur dan geldige-maar-lege JSON in
  // plaats van een afgekapt object dat de browser niet kan lezen.
  if (n < 0 || (size_t)n >= sizeof(io_buf)) {
    _server.send(200, "application/json", "{\"cfg\":{},\"status\":{}}");
    return;
  }
  sendJson(io_buf, (size_t)n);
}

void StatsPublisher::handleSave() {
  auto copy_arg = [&](const char* name, char* out, size_t max) {
    if (_server.hasArg(name)) {
      strncpy(out, _server.arg(name).c_str(), max - 1);
      out[max - 1] = 0;
    }
  };
  copy_arg("host", _cfg.host, STATS_HOST_MAX);
  copy_arg("user", _cfg.user, STATS_USER_MAX);
  copy_arg("prefix", _cfg.prefix, STATS_PREFIX_MAX);
  if (_cfg.prefix[0] == 0) strcpy(_cfg.prefix, "meshcore");

  if (_server.hasArg("pass") && _server.arg("pass").length() > 0) {
    copy_arg("pass", _cfg.pass, STATS_PASS_MAX);
  }
  if (_server.hasArg("port")) {
    long v = _server.arg("port").toInt();
    _cfg.port = (v > 0 && v < 65536) ? (uint16_t)v : 1883;
  }
  if (_server.hasArg("interval")) {
    uint32_t v = (uint32_t)_server.arg("interval").toInt();
    _cfg.interval_secs = v < 30 ? 30 : v;
  }
  _cfg.enabled = _server.hasArg("enabled");
  _cfg.forward_rx = _server.hasArg("forward_rx");
  saveConfig();

  _mqtt.disconnect();          // met de nieuwe instellingen opnieuw verbinden
  _last_connect_try = 0;
  _mqtt.setServer(_cfg.host, _cfg.port);

  _server.send(200, "application/json", "{\"ok\":1}");
}

void StatsPublisher::handleTest() {
  bool ok = publishStats();
  _server.send(200, "application/json", ok ? "{\"ok\":1}" : "{\"ok\":0}");
}

void StatsPublisher::handleStatsJson() {
  TRACE_ENTER();
  size_t n = _mesh ? _mesh->fillStatsJson(io_buf, sizeof(io_buf)) : 0;
  if (n == 0) { _server.send(503, "application/json", "{}"); return; }
  sendJson(io_buf, n);
}

// -------------------------------------------------------------- chat-endpoints

/* GET /messages.json?since=<seq>
 * Geeft de berichten nieuwer dan <seq>, oudste eerst, zodat de browser
 * incrementeel kan pollen in plaats van de hele lijst te hertekenen. Stopt
 * zodra de gedeelde buffer bijna vol is en zegt dat met "more":1, waarop de
 * client meteen terugkomt voor de rest. Zo bereikt een piek aan verkeer de
 * pagina toch snel, zonder dat een enkel antwoord - en dus een enkele
 * blokkerende TCP-schrijfactie - groot wordt. */
void StatsPublisher::handleMessagesJson() {
  TRACE_ENTER();
  uint32_t since = (uint32_t)strtoul(_server.arg("since").c_str(), nullptr, 10);

  // Langste item: vaste tekst + 2 32-bitgetallen + beide strings volledig ontsnapt.
  const size_t ENTRY_MAX = 48 + STATS_MSG_SRC_MAX * 2 + STATS_MSG_TEXT_MAX * 2;
  int n = snprintf(io_buf, sizeof(io_buf), "{\"m\":[");
  bool first = true, more = false;

  for (int i = 0; i < STATS_MSG_RING; i++) {
    const MsgItem& m = _msgs[(_msg_head + i) % STATS_MSG_RING];
    if (m.seq == 0 || m.seq <= since) continue;
    if ((size_t)n + ENTRY_MAX + 16 > sizeof(io_buf)) { more = true; break; }

    char src[STATS_MSG_SRC_MAX * 2 + 1], text[STATS_MSG_TEXT_MAX * 2 + 1];
    jsonStr(src, sizeof(src), m.src);
    jsonStr(text, sizeof(text), m.text);

    n += snprintf(io_buf + n, sizeof(io_buf) - n,
                  "%s{\"q\":%u,\"k\":%u,\"s\":\"%s\",\"t\":%u,\"x\":\"%s\"}",
                  first ? "" : ",", (unsigned)m.seq, (unsigned)m.kind, src,
                  (unsigned)m.timestamp, text);
    first = false;
  }
  snprintf(io_buf + n, sizeof(io_buf) - n, "],\"more\":%d}", more ? 1 : 0);
  sendJson(io_buf, strlen(io_buf));
}

/* POST /send  (to, text)
 * "to" is c<idx> voor een groepskanaal of k<12 hex> voor een contact. Versturen
 * zet enkel een pakket in de wachtrij, dus dit mag rechtstreeks vanuit de
 * verzoekafhandeling. */
void StatsPublisher::handleSend() {
  if (!_mesh) { _server.send(503, "application/json", "{\"err\":\"geen mesh\"}"); return; }

  String to = _server.arg("to");
  String text = _server.arg("text");
  if (text.length() == 0 || to.length() < 2) {
    _server.send(200, "application/json", "{\"ok\":0,\"err\":\"Kies een bestemming en typ een bericht.\"}");
    return;
  }
  // Laat plaats voor het voorvoegsel "<afzender>: " van een groepsbericht, en
  // snijd daarbij geen UTF-8-teken doormidden.
  if (text.length() > MAX_TEXT_LEN - 40) {
    int cut = MAX_TEXT_LEN - 40;
    while (cut > 0 && ((uint8_t)text[cut] & 0xC0) == 0x80) cut--;
    text.remove(cut);
  }

  uint32_t ts = _mesh->getRTCClock()->getCurrentTimeUnique();
  bool ok = false;
  const char* err = "Versturen mislukt.";
  char label[STATS_MSG_SRC_MAX];
  label[0] = 0;

  if (to[0] == 'c') {
    ChannelDetails ch;
    int idx = atoi(to.c_str() + 1);
    if (_mesh->getChannel(idx, ch) && ch.name[0]) {
      ok = _mesh->sendGroupMessage(ts, ch.channel, _mesh->getNodeName(), text.c_str(), text.length());
      copyTrim(label, sizeof(label), ch.name);
    } else {
      err = "Onbekend kanaal.";
    }
  } else if (to[0] == 'k' && to.length() >= 13) {
    uint8_t key[6];
    ContactInfo* c = hexToKey(to.c_str() + 1, key) ? _mesh->lookupContactByPubKey(key, 6) : nullptr;
    if (c) {
      uint32_t ack, timeout;
      ok = _mesh->sendMessage(*c, ts, 0, text.c_str(), ack, timeout) != MSG_SEND_FAILED;
      copyTrim(label, sizeof(label), c->name);
    } else {
      err = "Onbekend contact.";
    }
  } else {
    err = "Ongeldige bestemming.";
  }

  // Ons eigen bericht in de ring echoen, zodat de browser het gesprek toont en
  // niet enkel de binnenkomende helft.
  if (ok) noteMessage(STATS_MSG_SENT, label, ts, text.c_str());

  if (ok) {
    _server.send(200, "application/json", "{\"ok\":1}");
  } else {
    snprintf(io_buf, sizeof(io_buf), "{\"ok\":0,\"err\":\"%s\"}", err);
    sendJson(io_buf, strlen(io_buf));
  }
}

// ---------------------------------------------------------- kanaal-endpoints

/* GET /channels.json?off=<slot> - enkel bezette plaatsen; een lege naam betekent
 * vrij. Per pagina zoals de contacten, want MAX_GROUP_CHANNELS staat hier op 40
 * en de browser heeft de hele lijst nodig voor zijn keuzelijst. */
void StatsPublisher::handleChannelsJson() {
  TRACE_ENTER();
  int off = atoi(_server.arg("off").c_str());
  if (off < 0) off = 0;

  int n = snprintf(io_buf, sizeof(io_buf), "{\"ch\":[");
  bool first = true;
  int sent = 0, slot = off;

  if (_mesh) {
    ChannelDetails ch;
    for (; sent < STATS_CHANNEL_PAGE && _mesh->getChannel(slot, ch); slot++) {
      if (ch.name[0] == 0) continue;
      if ((size_t)n + sizeof(ch.name) * 2 + 40 > sizeof(io_buf)) break;
      char name[sizeof(ch.name) * 2 + 1];
      jsonStr(name, sizeof(name), ch.name);
      n += snprintf(io_buf + n, sizeof(io_buf) - n, "%s{\"i\":%d,\"n\":\"%s\"}",
                    first ? "" : ",", slot, name);
      first = false;
      sent++;
    }
    // getChannel() faalt voorbij de laatste plaats; zo weten we dat we rond zijn.
    if (!_mesh->getChannel(slot, ch)) slot = -1;
  } else {
    slot = -1;
  }

  snprintf(io_buf + n, sizeof(io_buf) - n, "],\"next\":%d}", slot);
  sendJson(io_buf, strlen(io_buf));
}

/* POST /channel/add  (name, psk)
 * Een lege psk betekent "maak een nieuw kanaal": we maken hier een 128-bits
 * sleutel aan, zodat niemand er zelf een moet verzinnen; die is daarna via de
 * companion-app uit te lezen om te delen. */
void StatsPublisher::handleChannelAdd() {
  if (!_mesh) { _server.send(503, "application/json", "{\"err\":\"geen mesh\"}"); return; }

  String name = _server.arg("name");
  String psk = _server.arg("psk");
  if (name.length() == 0) {
    _server.send(200, "application/json", "{\"ok\":0,\"err\":\"Geef het kanaal een naam.\"}");
    return;
  }

  char psk_b64[46];
  if (psk.length() == 0) {
    uint8_t key[16];
    for (int i = 0; i < 16; i++) key[i] = (uint8_t)esp_random();
    psk_b64[encode_base64(key, sizeof(key), (unsigned char *)psk_b64)] = 0;
  } else {
    strncpy(psk_b64, psk.c_str(), sizeof(psk_b64) - 1);
    psk_b64[sizeof(psk_b64) - 1] = 0;
  }

  if (_mesh->addChannel(name.c_str(), psk_b64)) {
    _mesh->persistChannels();
    _server.send(200, "application/json", "{\"ok\":1}");
  } else {
    // addChannel faalt enkel bij een verkeerde sleutellengte of een volle tabel.
    _server.send(200, "application/json",
                 "{\"ok\":0,\"err\":\"PSK ongeldig (16 of 32 bytes base64) of geen plaats meer.\"}");
  }
}

/* POST /channel/del  (idx)
 * Er is geen removeChannel(); de plaats leegmaken is hoe de rest van de
 * firmware een kanaal als weg markeert (een lege naam betekent ongebruikt). */
void StatsPublisher::handleChannelDel() {
  if (!_mesh) { _server.send(503, "application/json", "{\"err\":\"geen mesh\"}"); return; }

  int idx = atoi(_server.arg("idx").c_str());
  ChannelDetails ch;
  if (!_mesh->getChannel(idx, ch) || ch.name[0] == 0) {
    _server.send(200, "application/json", "{\"ok\":0,\"err\":\"Onbekend kanaal.\"}");
    return;
  }

  memset(&ch, 0, sizeof(ch));
  _mesh->setChannel(idx, ch);
  _mesh->persistChannels();
  _server.send(200, "application/json", "{\"ok\":1}");
}

// --------------------------------------------------------- contact-endpoints

/* GET /contacts.json?off=<slot>
 * Per pagina: MAX_CONTACTS kan in de honderden lopen, en een groot antwoord zou
 * een grote blokkerende schrijfactie betekenen. "next" is de plaats om verder
 * te gaan, of -1 als de tabel rond is. De plaatsen zijn ruwe tabelindexen,
 * inclusief de gereserveerde anonieme vooraan; die hebben type 0 en worden
 * overgeslagen. */
void StatsPublisher::handleContactsJson() {
  TRACE_ENTER();
  int off = atoi(_server.arg("off").c_str());
  if (off < 0) off = 0;

  int n = snprintf(io_buf, sizeof(io_buf), "{\"c\":[");
  bool first = true;
  int sent = 0, slot = off;
  uint32_t now = _mesh ? _mesh->getRTCClock()->getCurrentTime() : 0;

  if (_mesh) {
    ContactInfo c;
    int total = _mesh->getTotalContactSlots();
    for (; slot < total && sent < STATS_CONTACT_PAGE; slot++) {
      if (!_mesh->getContactByIdx(slot, c)) break;
      if (c.type == ADV_TYPE_NONE || c.name[0] == 0) continue;
      if ((size_t)n + 160 > sizeof(io_buf)) break;   // plaats houden voor het langste item

      char hex[13], name[sizeof(c.name) * 2 + 1];
      keyToHex(c.id.pub_key, hex);
      jsonStr(name, sizeof(name), c.name);

      long ago = (now && c.last_advert_timestamp) ? (long)(now - c.last_advert_timestamp) : -1;
      if (ago < 0) ago = -1;

      const RepeaterOpt* r = findRepeater(c.id.pub_key);
      n += snprintf(io_buf + n, sizeof(io_buf) - n,
                    "%s{\"k\":\"%s\",\"n\":\"%s\",\"t\":%u,\"a\":%ld,\"p\":%d,\"w\":%d}",
                    first ? "" : ",", hex, name, (unsigned)c.type, ago,
                    (r && r->publish) ? 1 : 0, (r && r->pass[0]) ? 1 : 0);
      first = false;
      sent++;
    }
    if (slot >= total) slot = -1;
  } else {
    slot = -1;
  }

  snprintf(io_buf + n, sizeof(io_buf) - n, "],\"next\":%d}", slot);
  sendJson(io_buf, strlen(io_buf));
}

bool StatsPublisher::parseKeyArg(const char* name, uint8_t out[6]) {
  String v = _server.arg(name);
  return v.length() >= 12 && hexToKey(v.c_str(), out);
}

/* POST /contact/save  (key, publish, pass)
 * Bewaart de twee extra dingen die de webclient per repeater bijhoudt. Een leeg
 * wachtwoord laat staan wat er stond, zodat de pagina het wachtwoord nooit naar
 * de browser hoeft terug te sturen. Een item zonder vinkje en zonder wachtwoord
 * verdwijnt weer, zodat de kleine vaste tabel vrij blijft voor repeaters die
 * er echt toe doen. */
void StatsPublisher::handleContactSave() {
  uint8_t key[6];
  if (!parseKeyArg("key", key)) {
    _server.send(200, "application/json", "{\"ok\":0,\"err\":\"Ongeldig contact.\"}");
    return;
  }

  bool publish = _server.arg("publish") == "1";
  String pass = _server.arg("pass");

  RepeaterOpt* r = findOrAddRepeater(key);
  if (!r) {
    _server.send(200, "application/json",
                 "{\"ok\":0,\"err\":\"Te veel repeaters bewaard; wis er eerst een.\"}");
    return;
  }
  r->publish = publish;

  if (pass.length() > 0) {
    // Aanhalingstekens en stuurtekens zouden de handgeschreven parser breken die
    // dit bestand terugleest, dus die laten we gewoon vallen.
    size_t o = 0;
    for (size_t i = 0; i < pass.length() && o < STATS_REPEATER_PASS - 1; i++) {
      char ch = pass[i];
      if (ch == '"' || ch == '\\' || (uint8_t)ch < 0x20) continue;
      r->pass[o++] = ch;
    }
    r->pass[o] = 0;
  }

  if (!r->publish && r->pass[0] == 0) {   // niets om te onthouden: plaats vrijgeven
    uint8_t idx = (uint8_t)(r - _repeaters);
    for (uint8_t i = idx; i + 1 < _num_repeaters; i++) _repeaters[i] = _repeaters[i + 1];
    _num_repeaters--;
  }

  saveRepeaters();
  _server.send(200, "application/json", "{\"ok\":1}");
}

/* POST /contact/login  (key)
 * Logt in op een repeater met het bewaarde wachtwoord; die login is wat het
 * uitlezen van zijn gegevens vrijgeeft. Het antwoord komt later door de lucht
 * terug en bereikt de companion-app via het gewone berichtenpad. */
void StatsPublisher::handleContactLogin() {
  uint8_t key[6];
  if (!_mesh || !parseKeyArg("key", key)) {
    _server.send(200, "application/json", "{\"ok\":0,\"err\":\"Ongeldig contact.\"}");
    return;
  }

  const RepeaterOpt* r = findRepeater(key);
  if (!r || r->pass[0] == 0) {
    _server.send(200, "application/json",
                 "{\"ok\":0,\"err\":\"Geen wachtwoord bewaard voor deze repeater.\"}");
    return;
  }

  ContactInfo* c = _mesh->lookupContactByPubKey(key, 6);
  if (!c) {
    _server.send(200, "application/json", "{\"ok\":0,\"err\":\"Contact niet gevonden.\"}");
    return;
  }

  uint32_t timeout;
  bool ok = _mesh->sendLogin(*c, r->pass, timeout) != MSG_SEND_FAILED;
  _server.send(200, "application/json",
               ok ? "{\"ok\":1}" : "{\"ok\":0,\"err\":\"Verzenden mislukt.\"}");
}

// -------------------------------------------------------------------- publiek

void StatsPublisher::begin(FS& fs, MyMesh* mesh) {
  _fs = &fs;
  _mesh = mesh;
  _instance = this;
  loadConfig();
  loadRepeaters();

  if (_mesh) _mesh->fillNodeIdHex(_node_hex, sizeof(_node_hex));

  // Een ruw pakket wordt hex ruim 500 tekens; de standaardbuffer van 256 is te
  // klein en publish() zou stilzwijgend weigeren.
  _mqtt.setBufferSize(STATS_RX_MAX_LEN * 2 + 128);
  _mqtt.setSocketTimeout(4);
  _mqtt.setKeepAlive(60);
  _mqtt.setServer(_cfg.host, _cfg.port);

  _server.on("/", HTTP_GET, [this]() { handleRoot(); });
  _server.on("/config.json", HTTP_GET, [this]() { handleConfigJson(); });
  _server.on("/save", HTTP_POST, [this]() { handleSave(); });
  _server.on("/test", HTTP_POST, [this]() { handleTest(); });
  _server.on("/stats.json", HTTP_GET, [this]() { handleStatsJson(); });
  _server.on("/messages.json", HTTP_GET, [this]() { handleMessagesJson(); });
  _server.on("/send", HTTP_POST, [this]() { handleSend(); });
  _server.on("/channels.json", HTTP_GET, [this]() { handleChannelsJson(); });
  _server.on("/channel/add", HTTP_POST, [this]() { handleChannelAdd(); });
  _server.on("/channel/del", HTTP_POST, [this]() { handleChannelDel(); });
  _server.on("/contacts.json", HTTP_GET, [this]() { handleContactsJson(); });
  _server.on("/contact/save", HTTP_POST, [this]() { handleContactSave(); });
  _server.on("/contact/login", HTTP_POST, [this]() { handleContactLogin(); });
  _server.begin();
  _started = true;
  _last_push = millis();
}

void StatsPublisher::loop() {
  if (!_started) return;
  _server.handleClient();

  if (!_cfg.enabled || _cfg.host[0] == 0) return;

  /* Alles onder deze regel kan blokkeren: connect() en publish() van
   * PubSubClient zijn synchrone socket-schrijfacties, en connect() kan seconden
   * op zijn socket-timeout blijven zitten als de broker onbereikbaar is. Dat
   * mag geen van alle gebeuren terwijl de radio werk klaar heeft staan - de
   * mesh heeft harde timing, dit doorsturen niet. Is de mesh bezig, dan komen
   * we volgende ronde gewoon terug. */
  if (_mesh && _mesh->hasPendingWork()) return;

  if (_mqtt.connected()) _mqtt.loop();
  drainRxQueue();

  if (millis() - _last_push < (unsigned long)_cfg.interval_secs * 1000UL) return;
  _last_push = millis();
  publishStats();
}
