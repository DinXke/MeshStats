#include "PacketFilter.h"

/* Utils.h en niet alleen MeshCore.h: de constanten (CIPHER_BLOCK_SIZE,
 * CIPHER_MAC_SIZE) komen uit MeshCore.h, maar mesh::Utils::sha256 -- waarmee
 * de kanaalhash berekend wordt op precies de manier die de zender ook
 * gebruikt -- staat in Utils.h, dat MeshCore.h zelf meebrengt. */
#include <Utils.h>
#include <ctype.h>
#include <stdarg.h>
#include <stdlib.h>
#include <string.h>

/* Everything the reference calls a "packet type" is an index into the tables
 * below. The names are MeshCore's own, from Packet.h, so a status line and a
 * packet dump use the same word for the same thing. */
static const char *TYPE_NAMES[PF_TYPE_COUNT] = {
  "REQ", "RESPONSE", "TXT_MSG", "ACK", "ADVERT", "GRP_TXT",
  "GRP_DATA", "ANON_REQ", "PATH", "TRACE", "MULTIPART", "CONTROL",
};

static const char *REASON_NAMES[PF_REASON_COUNT] = {
  "door", "type", "hops", "rate", "hash", "kanaal", "misvormd",
};

#define PF_FILE  "/filter_prefs"

/* Types that begin with a destination hash and (mostly) a source hash. These
 * are the ones an ACL exemption can be decided for -- see the caller. Kept
 * here as documentation of the layout the caller relies on:
 *
 *   REQ, RESPONSE, TXT_MSG, PATH   payload[0] = dest hash, payload[1] = src
 *   ANON_REQ                       payload[0] = dest hash
 *   GRP_TXT, GRP_DATA              payload[0] = channel hash
 *   ACK, ADVERT, TRACE, ...        no hash at the front at all
 */

#define GRP_TXT_TYPE   5
#define GRP_DATA_TYPE  6

// --------------------------------------------------------------- the state

struct PfChannel {
  char     label[PF_LABEL_MAX];
  uint8_t  hash;
  /* Treffers op deze regel. Niet bewaard in /filter_prefs, net als elke andere
   * teller hier: het bestand beschrijft wat er GEHANDHAAFD wordt, en een teller
   * die een herstart overleeft zou zeggen dat er iets gebeurd is sinds een
   * moment dat niemand kan aanwijzen. */
  uint32_t hits;
};

static bool     _enabled = false;
static bool     _disarmed = false;      // safe mode: rules loaded, not enforced
static uint8_t  _min_hash = 1;          // 1..3; 1 passes everything
static bool     _malformed = false;
static bool     _type_on[PF_TYPE_COUNT];
static uint8_t  _max_hops[PF_TYPE_COUNT];
static uint16_t _rate_limit[PF_TYPE_COUNT];
static uint16_t _rate_window[PF_TYPE_COUNT];
static PfChannel _chans[PF_CHAN_MAX];
static int      _n_chans = 0;

// The fixed-window rate counters. Not persisted: a budget is about now.
static unsigned long _win_start[PF_TYPE_COUNT];
static uint16_t      _win_count[PF_TYPE_COUNT];

static uint32_t _drop[PF_REASON_COUNT];
static uint32_t _drop_type[PF_TYPE_COUNT];
static uint32_t _passed = 0;
static uint32_t _exempted = 0;

/* De uitgebreide boekhouding (2.6.0). Alles hieronder reist over MQTT en nooit
 * over LoRa: MQTT loopt over wifi of LAN, waar bandbreedte niets kost, en de
 * sweep en de mesh-CLI blijven zuinig omdat daar elke byte zendtijd is. Dat
 * onderscheid is de enige reden dat dit ruimhartig mag zijn.
 *
 * Waarom dit meer zegt dan de zes totalen die er al waren. 'Er is 412 keer iets
 * weggegooid' vertelt niet welke regel te streng staat. De kruising type x reden
 * doet dat wel: ADVERT dat op de hoplimiet sneuvelt is een andere diagnose dan
 * GRP_TXT dat op de snelheidslimiet sneuvelt, en met alleen een totaal zijn die
 * twee niet uit elkaar te houden.
 *
 * Kosten in RAM: 12*7*4 + 12*4*3 + 12*4 = 528 byte. */
static uint32_t _drop_xr[PF_TYPE_COUNT][PF_REASON_COUNT];  // type x reden
static uint32_t _exempt_type[PF_TYPE_COUNT];               // via de ACL langs het filter

/* De snelheidslimiet apart, want 'hoe vaak bijt hij' is een andere vraag dan
 * 'hoeveel is er weggegooid'. Een limiet die nooit bijt staat te ruim en zegt
 * niets; een limiet die in elk venster bijt staat te krap en gooit structureel
 * verkeer weg. Het verschil tussen die twee is precies wat je wilt zien voordat
 * je een getal bijstelt, en het is uit een dropteller niet af te leiden. */
static uint32_t _win_seen[PF_TYPE_COUNT];    // vensters met minstens één pakket
static uint32_t _win_capped[PF_TYPE_COUNT];  // vensters waarin de limiet geraakt werd
static uint16_t _win_peak[PF_TYPE_COUNT];    // hoogste stand die een venster haalde
static bool     _win_hit[PF_TYPE_COUNT];     // in DIT venster al geteld

static FS   *_pf_fs = nullptr;
static bool  _dirty = false;
static bool  _loading = false;        // replaying the file: do not write it back
static unsigned long _save_due = 0;

/* SPIFFS wears out and adverts arrive in bursts, so a run of changes costs one
 * write rather than one per change. Five seconds is long enough to swallow a
 * form submitted field by field and short enough that nobody reboots in
 * between on purpose. */
#define PF_SAVE_DELAY_MS  5000

// ------------------------------------------------------------- the defaults

/* Straight from the reference, and they are not arbitrary: 8 hops is roughly
 * the diameter of a healthy regional mesh, GRP_TXT gets 32 because public
 * channel traffic is the one kind you want to travel, and the rate limits are
 * per type because an advert every few hours and an ACK per message have
 * nothing to say to each other.
 *
 * These defaults are what the filter does when it is SWITCHED ON without
 * further configuration. They are chosen to be nearly invisible on a healthy
 * mesh -- which is the point: turning the filter on should not be the moment a
 * repeater stops working. */
static void pfDefaults() {
  _enabled = false;
  _min_hash = 1;
  _malformed = false;
  for (int i = 0; i < PF_TYPE_COUNT; i++) {
    _type_on[i] = true;
    _max_hops[i] = 8;
    _rate_limit[i] = 5;
    _rate_window[i] = 60;
    _win_start[i] = 0;
    _win_count[i] = 0;
  }
  _max_hops[GRP_TXT_TYPE] = 32;
  _rate_limit[2] = 20;                 // TXT_MSG
  _rate_limit[GRP_TXT_TYPE] = 20;      // GRP_TXT
  _rate_limit[4] = 10;                 // ADVERT
  _n_chans = 0;
  memset(_chans, 0, sizeof(_chans));
}

static void pfClearCounters() {
  memset(_drop, 0, sizeof(_drop));
  memset(_drop_type, 0, sizeof(_drop_type));
  memset(_drop_xr, 0, sizeof(_drop_xr));
  memset(_exempt_type, 0, sizeof(_exempt_type));
  memset(_win_seen, 0, sizeof(_win_seen));
  memset(_win_capped, 0, sizeof(_win_capped));
  memset(_win_peak, 0, sizeof(_win_peak));
  memset(_win_hit, 0, sizeof(_win_hit));
  for (int i = 0; i < PF_CHAN_MAX; i++) _chans[i].hits = 0;
  _passed = 0;
  _exempted = 0;
}

// ------------------------------------------------------------- persistence

/* The file is a list of the very commands that produce this state, one per
 * line, and loading it feeds them back through the same parser the CLI uses.
 *
 * Rejected: a struct written as bytes, like the main config. It is smaller and
 * faster and it has a failure mode this cannot have -- a field added in the
 * middle silently reinterprets every stored value, and the node that shows you
 * the damage is on a roof. Here a line the parser no longer understands is one
 * line refused, with the rest of the settings intact, and a human with a serial
 * cable can read the file and see what the node thinks it was told. For a
 * setting whose failure mode is 'forwards nothing and looks fine', that trade
 * is not close. */
static void pfSave() {
  if (!_pf_fs) return;
  File f = _pf_fs->open(PF_FILE, "w");
  if (!f) return;
  f.printf("# MeshManager pakketfilter -- regels zoals 'filter <regel>'\n");
  f.printf("hash %u\n", (unsigned)_min_hash);
  f.printf("malformed %s\n", _malformed ? "on" : "off");
  for (int i = 0; i < PF_TYPE_COUNT; i++) {
    f.printf("hops %02d %u\n", i, (unsigned)_max_hops[i]);
    f.printf("rate %02d %u %u\n", i, (unsigned)_rate_limit[i], (unsigned)_rate_window[i]);
    if (!_type_on[i]) f.printf("type %02d off\n", i);
  }
  for (int i = 0; i < _n_chans; i++) {
    f.printf("channel add %s #%02x\n", _chans[i].label, (unsigned)_chans[i].hash);
  }
  /* Last on purpose. Everything above is inert while the filter is off, so a
   * file that is truncated halfway -- a brown-out during the write, which on a
   * solar node is a Tuesday -- leaves a node with half its rules and the filter
   * OFF, rather than one enforcing half a ruleset nobody chose. */
  if (_enabled) f.printf("on\n");
  f.close();
}

void pf_loop() {
  if (!_dirty || _save_due == 0) return;
  if ((long)(millis() - _save_due) < 0) return;
  _dirty = false;
  _save_due = 0;
  pfSave();
}

static void pfTouch() {
  if (_loading) return;
  _dirty = true;
  _save_due = millis() + PF_SAVE_DELAY_MS;
  if (_save_due == 0) _save_due = 1;
}

// --------------------------------------------------------------- the rules

static int parseType(const char *s, int *out) {
  if (!s || !*s) return 0;
  char *end = NULL;
  long v = strtol(s, &end, 10);
  if (end == s || (end && *end != 0)) return 0;
  if (v < 0 || v >= PF_TYPE_COUNT) return 0;
  *out = (int)v;
  return 1;
}

/* Base64 in 30 lines rather than a library. MeshCore vendors one, but it is
 * pulled in by the chat helpers a repeater does not build, and adding a
 * dependency to a repeater image for one decode is a poor trade. */
static int b64Val(char c) {
  if (c >= 'A' && c <= 'Z') return c - 'A';
  if (c >= 'a' && c <= 'z') return c - 'a' + 26;
  if (c >= '0' && c <= '9') return c - '0' + 52;
  if (c == '+') return 62;
  if (c == '/') return 63;
  return -1;
}

static int b64Decode(const char *src, uint8_t *dest, int dest_max) {
  int acc = 0, bits = 0, n = 0;
  for (const char *p = src; *p; p++) {
    if (*p == '=' || *p == '\r' || *p == '\n' || *p == ' ') continue;
    int v = b64Val(*p);
    if (v < 0) return -1;
    acc = (acc << 6) | v;
    bits += 6;
    if (bits >= 8) {
      bits -= 8;
      if (n >= dest_max) return -1;
      dest[n++] = (uint8_t)((acc >> bits) & 0xFF);
    }
  }
  return n;
}

static int hexVal(char c) {
  if (c >= '0' && c <= '9') return c - '0';
  if (c >= 'a' && c <= 'f') return c - 'a' + 10;
  if (c >= 'A' && c <= 'F') return c - 'A' + 10;
  return -1;
}

/* Turns what the operator typed into the one byte that is actually on the air.
 *
 * Two forms, and the difference matters. A pre-shared key is the honest input:
 * we hash it exactly as MeshCore's addChannel() does -- sha256(secret), first
 * byte -- so the value we compare against is the value the sender computed. A
 * bare '#hh' is the escape hatch for when you read a hash off a packet in the
 * archive and do not have the key; it blocks the same byte without any claim
 * about which channel that is.
 *
 * And the cost, which belongs next to the code that pays it: one byte collides.
 * Roughly one channel in 256 shares a hash byte with any other, so blocking a
 * channel also blocks that fraction of unrelated group traffic. A repeater
 * cannot tell them apart -- it would need the key to try decrypting, and if it
 * had the key it would not be a repeater. This is why channel blocking is the
 * last rule to reach for and why the site labels it as such. */
static bool chanHashFrom(const char *arg, uint8_t *out, const char **why) {
  if (arg[0] == '#') {
    int hi = hexVal(arg[1]), lo = arg[2] ? hexVal(arg[2]) : -1;
    if (hi < 0 || lo < 0 || arg[3] != 0) {
      *why = "een hash is '#' plus twee hexcijfers";
      return false;
    }
    *out = (uint8_t)((hi << 4) | lo);
    return true;
  }
  uint8_t secret[40];
  int len = b64Decode(arg, secret, sizeof(secret));
  if (len != 16 && len != 32) {
    *why = "geen geldige base64-sleutel (16 of 32 byte) en geen '#hh'";
    return false;
  }
  uint8_t hash[8];
  mesh::Utils::sha256(hash, sizeof(hash), secret, len);
  *out = hash[0];
  return true;
}

/* Own case-insensitive compare rather than strcasecmp(): that one lives in
 * <strings.h>, which is POSIX and not guaranteed on every Arduino core this
 * firmware is built for. Four lines is cheaper than a build that fails on
 * somebody else's toolchain. */
static bool eqNoCase(const char *a, const char *b) {
  for (; *a && *b; a++, b++) {
    if (tolower((unsigned char)*a) != tolower((unsigned char)*b)) return false;
  }
  return *a == 0 && *b == 0;
}

/* Wat een label mag zijn, en waarom er uberhaupt een grens is.
 *
 * Dit label komt in drie dingen terecht: het prefsbestand (regelgebaseerd, dus
 * een spatie erin zou een tweede argument worden), de JSON van /api/filter (een
 * aanhalingsteken erin zou het antwoord onparseerbaar maken) en de commandoregel
 * die de site terugstuurt om het kanaal weer vrij te geven. Ontsnappingsregels
 * voor alle drie is drie plaatsen die het eens moeten blijven; een label
 * beperken tot wat overal veilig is, is er een. Het label is toch alleen voor de
 * mens -- de node vergelijkt op de hash. */
static bool labelOk(const char *s) {
  if (!s || !*s) return false;
  for (const char *p = s; *p; p++) {
    bool ok = (*p >= 'a' && *p <= 'z') || (*p >= 'A' && *p <= 'Z')
              || (*p >= '0' && *p <= '9') || *p == '-' || *p == '_' || *p == '.';
    if (!ok) return false;
  }
  return true;
}

static int chanFind(const char *label_or_hash) {
  if (label_or_hash[0] == '#') {
    int hi = hexVal(label_or_hash[1]);
    int lo = label_or_hash[1] ? hexVal(label_or_hash[2]) : -1;
    if (hi < 0 || lo < 0) return -1;
    uint8_t h = (uint8_t)((hi << 4) | lo);
    for (int i = 0; i < _n_chans; i++) if (_chans[i].hash == h) return i;
    return -1;
  }
  for (int i = 0; i < _n_chans; i++) {
    if (eqNoCase(_chans[i].label, label_or_hash)) return i;
  }
  return -1;
}

// ------------------------------------------------------------ the decision

/* Group text whose structure cannot be right, checked without the key.
 *
 * The layout is channel hash (1) + MAC (2) + ciphertext, and encryptThenMAC()
 * pads the ciphertext to whole 16-byte blocks. So a payload that is not
 * 3 + 16n bytes was never produced by any MeshCore sender. That is the entire
 * claim -- see the header for why it is not more. */
static bool grpStructurallyImpossible(int payload_len) {
  int cipher = payload_len - 1 - CIPHER_MAC_SIZE;
  if (cipher < CIPHER_BLOCK_SIZE) return true;
  return (cipher % CIPHER_BLOCK_SIZE) != 0;
}

static bool rateAllows(int type) {
  uint16_t limit = _rate_limit[type];
  if (limit == 0) return true;                 // 0 = no rate rule for this type
  uint16_t window = _rate_window[type];
  if (window == 0) return true;

  unsigned long now = millis();
  unsigned long span = (unsigned long)window * 1000UL;
  if (_win_start[type] == 0 || (now - _win_start[type]) >= span) {
    _win_start[type] = now;
    _win_count[type] = 0;
    _win_hit[type] = false;
  }
  /* Een venster telt pas mee als er verkeer in zat. Anders zou een stille nacht
   * duizenden 'ruime' vensters opleveren en zou de verhouding tussen krap en
   * ruim alleen nog zeggen hoe lang de node aan stond. */
  if (_win_count[type] == 0) _win_seen[type]++;
  if (_win_count[type] >= limit) {
    // Eén keer per venster, niet één keer per geweigerd pakket: de vraag is in
    // hoeveel vensters de limiet geraakt werd, niet hoe diep hij overschreden is.
    if (!_win_hit[type]) { _win_hit[type] = true; _win_capped[type]++; }
    return false;
  }
  _win_count[type]++;
  if (_win_count[type] > _win_peak[type]) _win_peak[type] = _win_count[type];
  return true;
}

bool pf_enabled() { return _enabled; }

bool pf_allow(uint8_t payload_type, uint8_t hash_count, uint8_t hash_size,
              const uint8_t *payload, int payload_len, bool exempt) {
  if (!_enabled) return true;
  if (payload_type >= PF_TYPE_COUNT) return true;   // 0x0F RAW_CUSTOM and friends

  int t = (int)payload_type;
  /* Ook per type geteld, en dat is het cijfer waarmee je merkt dat een filter
   * strenger staat dan je dacht: alles wat hier langskomt is verkeer dat de
   * regels WEL geraakt zou hebben en er via de ACL langs mocht. Staat dat getal
   * hoog naast een lage 'passed', dan werkt het filter vooral voor de mensen die
   * er toch al buiten vielen. */
  if (exempt) { _exempted++; _exempt_type[t]++; return true; }

  uint8_t reason = PF_PASS;
  int chan_idx = -1;

  /* Order is deliberate and differs from the order the rules are documented in.
   * The cheap, absolute tests come first, and the rate limit comes LAST -- a
   * packet that was going to be dropped for another reason must not spend
   * budget, or a burst of packets you already refuse would push the ones you
   * wanted through the limit. */
  if (!_type_on[t]) {
    reason = PF_R_TYPE;
  } else if (_max_hops[t] == 0 || hash_count >= _max_hops[t]) {
    reason = PF_R_HOPS;
  } else if (hash_size < _min_hash) {
    reason = PF_R_HASH;
  } else if (t == GRP_TXT_TYPE && _malformed && grpStructurallyImpossible(payload_len)) {
    reason = PF_R_MALFORMED;
  } else if (t == GRP_TXT_TYPE && _n_chans > 0 && payload_len >= 1) {
    for (int i = 0; i < _n_chans; i++) {
      if (_chans[i].hash == payload[0]) { reason = PF_R_CHANNEL; chan_idx = i; break; }
    }
  }
  if (reason == PF_PASS && !rateAllows(t)) reason = PF_R_RATE;

  if (reason == PF_PASS) { _passed++; return true; }
  _drop[reason]++;
  _drop_type[t]++;
  _drop_xr[t][reason]++;
  // Welke regel raakte, en niet alleen dat er een kanaalregel raakte. Met zestien
  // mogelijke regels is 'kanaal: 900' geen aanwijzing en 'spam: 900' wel.
  if (chan_idx >= 0) _chans[chan_idx].hits++;
  return false;
}

// ---------------------------------------------------------------- the JSON

size_t pf_json(char *out, size_t max) {
  int p = snprintf(out, max,
    "{\"on\":%s,\"disarmed\":%s,\"hash\":%u,\"malformed\":%s,"
    "\"passed\":%lu,\"exempt\":%lu,\"drop\":{",
    _enabled ? "true" : "false", _disarmed ? "true" : "false", (unsigned)_min_hash,
    _malformed ? "true" : "false",
    (unsigned long)_passed, (unsigned long)_exempted);
  if (p <= 0 || (size_t)p >= max) return 0;

  for (int i = 1; i < PF_REASON_COUNT; i++) {
    p += snprintf(out + p, max - p, "%s\"%s\":%lu", i > 1 ? "," : "",
                  REASON_NAMES[i], (unsigned long)_drop[i]);
    if ((size_t)p >= max) return 0;
  }
  p += snprintf(out + p, max - p, "},\"types\":[");
  if ((size_t)p >= max) return 0;

  for (int i = 0; i < PF_TYPE_COUNT; i++) {
    p += snprintf(out + p, max - p,
                  "%s{\"id\":%d,\"name\":\"%s\",\"on\":%s,\"hops\":%u,"
                  "\"limit\":%u,\"window\":%u,\"drop\":%lu}",
                  i ? "," : "", i, TYPE_NAMES[i], _type_on[i] ? "true" : "false",
                  (unsigned)_max_hops[i], (unsigned)_rate_limit[i],
                  (unsigned)_rate_window[i], (unsigned long)_drop_type[i]);
    if ((size_t)p >= max) return 0;
  }
  p += snprintf(out + p, max - p, "],\"channels\":[");
  if ((size_t)p >= max) return 0;

  for (int i = 0; i < _n_chans; i++) {
    p += snprintf(out + p, max - p, "%s{\"label\":\"%s\",\"hash\":\"%02x\"}",
                  i ? "," : "", _chans[i].label, (unsigned)_chans[i].hash);
    if ((size_t)p >= max) return 0;
  }
  p += snprintf(out + p, max - p, "]}");
  if (p <= 0 || (size_t)p >= max) return 0;
  return (size_t)p;
}

/* Bijschrijven dat nooit buiten de buffer komt, hoeveel er ook nog aangeboden
 * wordt. Zonder dit hangt de veiligheid aan de vraag of de gereserveerde staart
 * groot genoeg was voor alle sluittekens die er nog aan komen -- en die vraag
 * moet je bij élke wijziging opnieuw goed beantwoorden. Eén keer verkeerd en
 * 'p' staat voorbij 'max', waarna (max - p) als size_t een enorm getal is en de
 * volgende snprintf buiten de buffer schrijft. Hier is 'vol' gewoon een
 * toestand: er wordt niets meer bijgeschreven en p blijft op max staan.
 *
 * De standaardwaarde van 'detail' staat in de header, niet hier -- C++ laat hem
 * maar op één plek toe. */
static void pfAppend(char *out, int *p, size_t max, const char *fmt, ...) {
  if (*p < 0 || (size_t)*p >= max) { *p = (int)max; return; }
  va_list ap;
  va_start(ap, fmt);
  int w = vsnprintf(out + *p, max - *p, fmt, ap);
  va_end(ap);
  if (w < 0) { *p = (int)max; return; }
  *p += w;
  if ((size_t)*p >= max) *p = (int)max;
}

size_t pf_summary_json(char *out, size_t max, bool detail) {
  int blocked = 0;
  for (int i = 0; i < PF_TYPE_COUNT; i++) if (!_type_on[i] || _max_hops[i] == 0) blocked++;

  int p = snprintf(out, max,
    "{\"on\":%s,\"disarmed\":%s,\"hash\":%u,\"malformed\":%s,\"channels\":%d,"
    "\"blocked_types\":%d,\"passed\":%lu,\"exempt\":%lu,\"drop\":{",
    _enabled ? "true" : "false", _disarmed ? "true" : "false", (unsigned)_min_hash,
    _malformed ? "true" : "false", _n_chans, blocked,
    (unsigned long)_passed, (unsigned long)_exempted);
  if (p <= 0 || (size_t)p >= max) return 0;

  for (int i = 1; i < PF_REASON_COUNT; i++) {
    p += snprintf(out + p, max - p, "%s\"%s\":%lu", i > 1 ? "," : "",
                  REASON_NAMES[i], (unsigned long)_drop[i]);
    if (p <= 0 || (size_t)p >= max) return 0;
  }
  p += snprintf(out + p, max - p, "}");
  if (p <= 0 || (size_t)p >= max) return 0;

  /* Vanaf hier is afkappen géén reden om het hele bericht weg te gooien: de
   * korte vorm hierboven staat er al, en die is wat de site nodig heeft om te
   * weten dat er een filter aanstaat. Wat hieronder niet past, wordt gemeld met
   * "trunc":1 in plaats van stilletjes weggelaten -- een uitsplitsing die de
   * helft van haar rijen kwijt is, is erger dan een die zegt dat ze dat is.
   *
   * TAIL is de ruimte die tot het eind gereserveerd blijft: de vier sluittekens
   * van de deelobjecten, ,"trunc":1, de sluitaccolade en de NUL. ENTRY is de
   * langste regel die één lus kan schrijven -- een kanaal met een label van 23
   * tekens. Vóór elke regel wordt op ENTRY + TAIL getoetst, dus 'p' kan nooit
   * voorbij 'max' komen. Dat is niet theoretisch: zou p over max heen gaan, dan
   * is (max - p) als size_t een enorm getal en schrijft de volgende snprintf
   * vrolijk buiten de buffer. */
  if (detail && max > 240) {
    const size_t TAIL = 64;
    const size_t ENTRY = 64;
    bool cut = false, first = true;

    // type x reden, alleen wat werkelijk geteld is
    pfAppend(out, &p, max, ",\"xr\":{");
    for (int t = 0; t < PF_TYPE_COUNT && !cut; t++) {
      for (int r = 1; r < PF_REASON_COUNT; r++) {
        if (_drop_xr[t][r] == 0) continue;
        if ((size_t)p + ENTRY + TAIL > max) { cut = true; break; }
        pfAppend(out, &p, max, "%s\"%02d.%s\":%lu", first ? "" : ",",
                      t, REASON_NAMES[r], (unsigned long)_drop_xr[t][r]);
        first = false;
      }
    }
    pfAppend(out, &p, max, "}");

    /* De snelheidslimiet: hoe vaak bijt hij, en hoe ruim zat hij. Alleen types
     * met een limiet én met verkeer -- een venster zonder pakketten zegt niets
     * en zou de verhouding alleen maar verdunnen. */
    pfAppend(out, &p, max, ",\"rate\":{");
    first = true;
    for (int t = 0; t < PF_TYPE_COUNT && !cut; t++) {
      if (_rate_limit[t] == 0 || _win_seen[t] == 0) continue;
      if ((size_t)p + ENTRY + TAIL > max) { cut = true; break; }
      pfAppend(out, &p, max,
               "%s\"%02d\":{\"seen\":%lu,\"cap\":%lu,\"peak\":%u,\"lim\":%u}",
               first ? "" : ",", t, (unsigned long)_win_seen[t],
               (unsigned long)_win_capped[t], (unsigned)_win_peak[t],
               (unsigned)_rate_limit[t]);
      first = false;
    }
    pfAppend(out, &p, max, "}");

    // Langs het filter via de ACL, per type
    pfAppend(out, &p, max, ",\"ex\":{");
    first = true;
    for (int t = 0; t < PF_TYPE_COUNT && !cut; t++) {
      if (_exempt_type[t] == 0) continue;
      if ((size_t)p + ENTRY + TAIL > max) { cut = true; break; }
      pfAppend(out, &p, max, "%s\"%02d\":%lu", first ? "" : ",",
                    t, (unsigned long)_exempt_type[t]);
      first = false;
    }
    pfAppend(out, &p, max, "}");

    /* Treffers per geblokkeerd kanaal. Het label is een lokale bijnaam van de
     * beheerder en de hash is wat er werkelijk op de lucht staat; beide gaan mee,
     * zodat de ontvangende kant kan kiezen wat ze toont -- en die kiest het label
     * en de hash achter een login, want dit is het enige veld hier dat over het
     * kanaal van iemand anders gaat in plaats van over het gedrag van deze node. */
    pfAppend(out, &p, max, ",\"chan\":[");
    for (int i = 0; i < _n_chans && !cut; i++) {
      if ((size_t)p + ENTRY + TAIL > max) { cut = true; break; }
      pfAppend(out, &p, max, "%s{\"l\":\"%s\",\"h\":\"%02x\",\"hits\":%lu}",
                    i ? "," : "", _chans[i].label, (unsigned)_chans[i].hash,
                    (unsigned long)_chans[i].hits);
    }
    pfAppend(out, &p, max, "]");

    if (cut) pfAppend(out, &p, max, ",\"trunc\":1");
    if (p <= 0 || (size_t)p >= max) return 0;
  }

  p += snprintf(out + p, max - p, "}");
  if (p <= 0 || (size_t)p >= max) return 0;
  return (size_t)p;
}

// ------------------------------------------------------------ the commands

static void statusLine(char *reply, size_t max) {
  uint32_t weg = 0;
  for (int i = 1; i < PF_REASON_COUNT; i++) weg += _drop[i];
  int blocked = 0;
  for (int i = 0; i < PF_TYPE_COUNT; i++) if (!_type_on[i] || _max_hops[i] == 0) blocked++;
  snprintf(reply, max,
           "filter %s - %lu weg, %lu door, %lu vrijgesteld | hash>=%u | misvormd %s "
           "| %d kanalen | %d types dicht",
           _enabled ? "AAN" : (_disarmed ? "uit (veilige modus)" : "uit"),
           (unsigned long)weg, (unsigned long)_passed,
           (unsigned long)_exempted, (unsigned)_min_hash, _malformed ? "aan" : "uit",
           _n_chans, blocked);
}

/* Splits off the next whitespace-separated word, in place. Returns NULL when
 * there is nothing left. */
static char *nextWord(char **cursor) {
  char *s = *cursor;
  while (*s == ' ' || *s == '\t') s++;
  if (*s == 0) { *cursor = s; return NULL; }
  char *start = s;
  while (*s && *s != ' ' && *s != '\t') s++;
  if (*s) { *s = 0; s++; }
  *cursor = s;
  return start;
}

bool pf_command(const char *rest, char *reply, size_t reply_max) {
  char buf[192];
  strncpy(buf, rest ? rest : "", sizeof(buf) - 1);
  buf[sizeof(buf) - 1] = 0;
  char *cursor = buf;
  char *w = nextWord(&cursor);

  if (w == NULL) {                                   // 'filter'
    statusLine(reply, reply_max);
    return true;
  }

  if (strcmp(w, "on") == 0) {
    /* Also lifts the safe-mode disarm. Somebody typing this has decided the
     * node is well enough to enforce rules again, and that judgement beats a
     * boot counter -- which is, after all, why there is a human at a CLI. */
    _enabled = true;
    _disarmed = false;
    pfTouch();
    statusLine(reply, reply_max);
    return true;
  }
  if (strcmp(w, "off") == 0) {
    _enabled = false;
    pfTouch();
    snprintf(reply, reply_max, "OK - filter uit, regels blijven staan");
    return true;
  }
  if (strcmp(w, "reset") == 0) {
    /* The escape hatch. Deliberately also clears the counters: after a reset
     * the interesting number is 'how much has been dropped since I fixed it',
     * and a running total from the broken configuration buries that. */
    pfDefaults();
    pfClearCounters();
    pfTouch();
    snprintf(reply, reply_max, "OK - filter terug op de standaardwaarden en UIT");
    return true;
  }

  if (strcmp(w, "types") == 0) {
    int p = snprintf(reply, reply_max, "types:");
    for (int i = 0; i < PF_TYPE_COUNT && (size_t)p < reply_max; i++) {
      p += snprintf(reply + p, reply_max - p, " %02d=%s", i, TYPE_NAMES[i]);
    }
    return true;
  }

  if (strcmp(w, "hops") == 0) {
    char *a = nextWord(&cursor), *b = nextWord(&cursor);
    if (a == NULL) {
      int p = snprintf(reply, reply_max, "hops:");
      for (int i = 0; i < PF_TYPE_COUNT && (size_t)p < reply_max; i++) {
        p += snprintf(reply + p, reply_max - p, " %02d:%u", i, (unsigned)_max_hops[i]);
      }
      return true;
    }
    int t;
    if (!parseType(a, &t) || b == NULL) {
      snprintf(reply, reply_max, "Err - 'filter hops <type 00-11> <max 0-63>'");
      return true;
    }
    long v = strtol(b, NULL, 10);
    if (v < 0 || v > 63) {
      snprintf(reply, reply_max, "Err - max hops ligt tussen 0 en 63");
      return true;
    }
    _max_hops[t] = (uint8_t)v;
    pfTouch();
    snprintf(reply, reply_max, "OK - %s (%02d) hoogstens %ld hops%s",
             TYPE_NAMES[t], t, v, v == 0 ? " -- dus NIETS van dit type" : "");
    return true;
  }

  if (strcmp(w, "rate") == 0) {
    char *a = nextWord(&cursor), *b = nextWord(&cursor), *c = nextWord(&cursor);
    if (a == NULL) {
      int p = snprintf(reply, reply_max, "rate:");
      for (int i = 0; i < PF_TYPE_COUNT && (size_t)p < reply_max; i++) {
        p += snprintf(reply + p, reply_max - p, " %02d:%u/%us", i,
                      (unsigned)_rate_limit[i], (unsigned)_rate_window[i]);
      }
      return true;
    }
    int t;
    if (!parseType(a, &t) || b == NULL || c == NULL) {
      snprintf(reply, reply_max, "Err - 'filter rate <type 00-11> <aantal> <seconden>'");
      return true;
    }
    long lim = strtol(b, NULL, 10), win = strtol(c, NULL, 10);
    if (lim < 0 || lim > 65535 || win < 1 || win > 3600) {
      snprintf(reply, reply_max, "Err - aantal 0-65535, venster 1-3600 seconden");
      return true;
    }
    _rate_limit[t] = (uint16_t)lim;
    _rate_window[t] = (uint16_t)win;
    _win_start[t] = 0;
    _win_count[t] = 0;
    pfTouch();
    if (lim == 0) {
      snprintf(reply, reply_max, "OK - %s (%02d) geen snelheidslimiet", TYPE_NAMES[t], t);
    } else {
      snprintf(reply, reply_max, "OK - %s (%02d) hoogstens %ld per %lds",
               TYPE_NAMES[t], t, lim, win);
    }
    return true;
  }

  if (strcmp(w, "hash") == 0) {
    char *a = nextWord(&cursor);
    if (a == NULL) {
      snprintf(reply, reply_max, "hash: minimaal %u byte padhash", (unsigned)_min_hash);
      return true;
    }
    long v = strtol(a, NULL, 10);
    if (v < 1 || v > 3) {
      snprintf(reply, reply_max, "Err - de padhash is 1, 2 of 3 byte");
      return true;
    }
    _min_hash = (uint8_t)v;
    pfTouch();
    snprintf(reply, reply_max, "OK - minimaal %ld byte padhash%s", v,
             v > 1 ? " -- oudere nodes vallen hiermee af" : "");
    return true;
  }

  if (strcmp(w, "malformed") == 0) {
    char *a = nextWord(&cursor);
    if (a == NULL) {
      snprintf(reply, reply_max, "misvormd: %s (structurele controle op GRP_TXT)",
               _malformed ? "aan" : "uit");
      return true;
    }
    if (strcmp(a, "on") != 0 && strcmp(a, "off") != 0) {
      snprintf(reply, reply_max, "Err - 'filter malformed on|off'");
      return true;
    }
    _malformed = (strcmp(a, "on") == 0);
    pfTouch();
    snprintf(reply, reply_max, "OK - structurele controle op groepstekst %s",
             _malformed ? "aan" : "uit");
    return true;
  }

  if (strcmp(w, "type") == 0) {
    char *a = nextWord(&cursor), *b = nextWord(&cursor);
    int t;
    if (!parseType(a ? a : "", &t)) {
      snprintf(reply, reply_max, "Err - 'filter type <00-11> [on|off]' ('filter types' voor de lijst)");
      return true;
    }
    if (b == NULL) {
      snprintf(reply, reply_max, "type %02d (%s): %s", t, TYPE_NAMES[t],
               _type_on[t] ? "wordt doorgestuurd" : "wordt NIET doorgestuurd");
      return true;
    }
    if (strcmp(b, "on") != 0 && strcmp(b, "off") != 0) {
      snprintf(reply, reply_max, "Err - 'filter type %02d on|off'", t);
      return true;
    }
    _type_on[t] = (strcmp(b, "on") == 0);
    pfTouch();
    snprintf(reply, reply_max, "OK - %s (%02d) wordt %s doorgestuurd",
             TYPE_NAMES[t], t, _type_on[t] ? "weer" : "NIET meer");
    return true;
  }

  if (strcmp(w, "channel") == 0) {
    char *act = nextWord(&cursor);
    if (act == NULL || strcmp(act, "list") == 0) {
      if (_n_chans == 0) {
        snprintf(reply, reply_max, "kanalen: geen enkel kanaal geblokkeerd");
        return true;
      }
      int p = snprintf(reply, reply_max, "kanalen:");
      for (int i = 0; i < _n_chans && (size_t)p + 30 < reply_max; i++) {
        p += snprintf(reply + p, reply_max - p, " %s(#%02x)",
                      _chans[i].label, (unsigned)_chans[i].hash);
      }
      return true;
    }
    if (strcmp(act, "add") == 0) {
      char *label = nextWord(&cursor), *key = nextWord(&cursor);
      if (label == NULL || key == NULL) {
        snprintf(reply, reply_max,
                 "Err - 'filter channel add <label> <psk-base64|#hh>' (een repeater "
                 "ziet geen kanaalnaam, alleen de hash)");
        return true;
      }
      if (_n_chans >= PF_CHAN_MAX) {
        snprintf(reply, reply_max, "Err - er passen er %d, niet meer", PF_CHAN_MAX);
        return true;
      }
      if (!labelOk(label) || strlen(label) >= PF_LABEL_MAX) {
        snprintf(reply, reply_max,
                 "Err - een label is 1-%d tekens uit a-z A-Z 0-9 - _ .",
                 PF_LABEL_MAX - 1);
        return true;
      }
      const char *why = "";
      uint8_t h = 0;
      if (!chanHashFrom(key, &h, &why)) {
        snprintf(reply, reply_max, "Err - %s", why);
        return true;
      }
      if (chanFind(label) >= 0) {
        snprintf(reply, reply_max, "Err - er staat al een kanaal met het label %s", label);
        return true;
      }
      strncpy(_chans[_n_chans].label, label, PF_LABEL_MAX - 1);
      _chans[_n_chans].label[PF_LABEL_MAX - 1] = 0;
      _chans[_n_chans].hash = h;
      _n_chans++;
      pfTouch();
      snprintf(reply, reply_max,
               "OK - %s geblokkeerd op hash #%02x (let op: 1 op 256 kanalen deelt "
               "die byte)", label, (unsigned)h);
      return true;
    }
    if (strcmp(act, "remove") == 0) {
      char *which = nextWord(&cursor);
      int idx = which ? chanFind(which) : -1;
      if (idx < 0) {
        snprintf(reply, reply_max, "Err - geen kanaal met label of hash '%s'",
                 which ? which : "");
        return true;
      }
      char gone[PF_LABEL_MAX];
      strncpy(gone, _chans[idx].label, sizeof(gone));
      gone[sizeof(gone) - 1] = 0;
      for (int i = idx; i + 1 < _n_chans; i++) _chans[i] = _chans[i + 1];
      _n_chans--;
      pfTouch();
      snprintf(reply, reply_max, "OK - %s wordt weer doorgestuurd", gone);
      return true;
    }
    snprintf(reply, reply_max, "Err - 'filter channel list|add|remove'");
    return true;
  }

  if (strcmp(w, "count") == 0) {
    char *a = nextWord(&cursor);
    if (a != NULL && strcmp(a, "types") == 0) {
      int p = snprintf(reply, reply_max, "weg per type:");
      bool any = false;
      for (int i = 0; i < PF_TYPE_COUNT && (size_t)p + 16 < reply_max; i++) {
        if (_drop_type[i] == 0) continue;      // alleen wat werkelijk weg is
        any = true;
        p += snprintf(reply + p, reply_max - p, " %s:%lu",
                      TYPE_NAMES[i], (unsigned long)_drop_type[i]);
      }
      if (!any) snprintf(reply, reply_max, "weg per type: nog niets weggegooid");
      return true;
    }
    int p = snprintf(reply, reply_max, "weg:");
    for (int i = 1; i < PF_REASON_COUNT && p > 0 && (size_t)p < reply_max; i++) {
      p += snprintf(reply + p, reply_max - p, " %s=%lu",
                    REASON_NAMES[i], (unsigned long)_drop[i]);
    }
    if (p > 0 && (size_t)p < reply_max) {
      snprintf(reply + p, reply_max - p, " | door=%lu vrij=%lu",
               (unsigned long)_passed, (unsigned long)_exempted);
    }
    return true;
  }

  snprintf(reply, reply_max,
           "Err - filter [on|off|reset|types|hops|rate|hash|malformed|type|channel|count]");
  return true;
}

// ----------------------------------------------------------------- startup

void pf_begin(FS &fs, bool armed) {
  _pf_fs = &fs;
  pfDefaults();
  pfClearCounters();
  _disarmed = !armed;

  File f = fs.open(PF_FILE, "r");
  if (!f) return;                       // never configured: defaults, and off

  _loading = true;
  char line[160];
  char reply[160];
  while (f.available()) {
    size_t n = f.readBytesUntil('\n', line, sizeof(line) - 1);
    line[n] = 0;
    while (n > 0 && (line[n - 1] == '\r' || line[n - 1] == ' ')) line[--n] = 0;
    if (line[0] == 0 || line[0] == '#') continue;
    /* A line this build no longer understands is one line refused, and the
     * rest of the file still applies. Printed rather than swallowed: a filter
     * that came back different from what was stored is exactly the thing you
     * want to see in a boot log. */
    if (!pf_command(line, reply, sizeof(reply)) || strncmp(reply, "Err", 3) == 0) {
      Serial.printf("PacketFilter: regel geweigerd: %s (%s)\n", line, reply);
    }
  }
  f.close();
  _loading = false;

  if (_disarmed && _enabled) {
    /* Not written back: the file keeps saying 'on', so a node that starts
     * cleanly next time enforces what its operator chose. Only this boot goes
     * without. */
    _enabled = false;
    Serial.println("PacketFilter: stond AAN, maar veilige modus laat hem uit");
  }
  Serial.printf("PacketFilter: %s, %d kanalen, hash>=%u\n",
                _enabled ? "AAN" : "uit", _n_chans, (unsigned)_min_hash);
}
