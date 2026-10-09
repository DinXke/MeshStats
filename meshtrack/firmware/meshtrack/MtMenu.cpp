// Seriële bediening: een genummerd menu in DOS-stijl dat meteen opent zodra er
// een terminal verbindt, plus losse commando's (status, set, mode, ...) die
// altijd werken, ook met het menu open. De webpagina (Web Serial) gebruikt die
// commando's; een regel die met een letter begint is dus een commando, een
// cijfer kiest in het menu.
#include "MeshTrack.h"
#include "MtConfig.h"
#include "MtTracker.h"
#include "MtSender.h"
#include "MtMotion.h"
#include "MtBattery.h"
#include "MtGps.h"
#include "MyMesh.h"
#include "UITask.h"
extern UITask ui_task;
#include <stdarg.h>
#include <stdlib.h>
#include <string.h>
#include <ctype.h>

// ---- uitvoer -------------------------------------------------------------------

static bool s_suspended = false;      // stock rescue-CLI heeft Serial
static bool s_conn = false;
static bool s_in_menu = false;

// Uitvoer gaat naar Serial, of (Bluetooth, alleen-lezen commando's) naar een buffer: s_cap.
// In de buffer eindigt een regel op "\n" (niet "\r\n": scheelt bytes over Bluetooth).
static char* s_cap = nullptr;
static size_t s_cap_len = 0, s_cap_max = 0;
static bool s_cap_over = false;
static void sink(const char* t) {
  if (!s_cap) { Serial.print(t); return; }
  size_t n = strlen(t);
  if (s_cap_len + n + 1 > s_cap_max) { n = s_cap_max > s_cap_len + 1 ? s_cap_max - s_cap_len - 1 : 0; s_cap_over = true; }
  memcpy(s_cap + s_cap_len, t, n);
  s_cap_len += n;
  s_cap[s_cap_len] = 0;
}
static void sink_eol() { sink(s_cap ? "\n" : "\r\n"); }
static void out(const char* fmt, ...) {
  char buf[200];
  va_list ap; va_start(ap, fmt); vsnprintf(buf, sizeof(buf), fmt, ap); va_end(ap);
  sink(buf);
}
static void outl(const char* fmt, ...) {
  char buf[200];
  va_list ap; va_start(ap, fmt); vsnprintf(buf, sizeof(buf), fmt, ap); va_end(ap);
  sink(buf); sink_eol();
}
static void dump_out(const char* t, bool eol) { sink(t); if (eol) sink_eol(); }

void mt_log(const char* fmt, ...) {
  if (s_suspended || !Serial || s_in_menu) return;
  char buf[200];
  va_list ap; va_start(ap, fmt); vsnprintf(buf, sizeof(buf), fmt, ap); va_end(ap);
  Serial.printf("[%lu] %s\r\n", (unsigned long)(millis() / 1000), buf);
}

void mt_menu_suspend() { s_suspended = true; }

// ---- waarden tonen en zetten ----------------------------------------------------

static void fmt_dur(char* o, size_t n, uint32_t s) {
  if (s == 0) snprintf(o, n, "uit");
  else if (s % 3600 == 0) snprintf(o, n, "%luh", (unsigned long)(s / 3600));
  else if (s % 60 == 0) snprintf(o, n, "%lum", (unsigned long)(s / 60));
  else snprintf(o, n, "%lus", (unsigned long)s);
}

static void fmt_dur_nl(char* o, size_t n, uint32_t s) {
  if (s == 0) snprintf(o, n, "uit");
  else if (s % 3600 == 0) snprintf(o, n, "%lu uur", (unsigned long)(s / 3600));
  else if (s % 60 == 0) snprintf(o, n, "%lu min", (unsigned long)(s / 60));
  else snprintf(o, n, "%lu s", (unsigned long)s);
}

static bool parse_dur(const char* s, uint32_t* out_s) {
  char* end;
  unsigned long v = strtoul(s, &end, 10);
  if (end == s) return false;
  while (*end == ' ') end++;
  if (*end == 0 || *end == 's') *out_s = v;
  else if (*end == 'm') *out_s = v * 60;
  else if (*end == 'h' || *end == 'u') *out_s = v * 3600;
  else return false;
  return true;
}

static bool parse_u16(const char* s, uint16_t max, uint16_t* o) {
  char* end;
  unsigned long v = strtoul(s, &end, 10);
  if (end == s || *end || v > max) return false;
  *o = (uint16_t)v;
  return true;
}

static bool parse_onoff(const char* s, uint8_t* o) {
  if (!strcmp(s, "on") || !strcmp(s, "aan") || !strcmp(s, "1")) { *o = 1; return true; }
  if (!strcmp(s, "off") || !strcmp(s, "uit") || !strcmp(s, "0")) { *o = 0; return true; }
  return false;
}

static bool parse_key(const char* s, uint8_t* o) {
  if (strlen(s) != 64) return false;
  for (int i = 0; i < 32; i++) {
    char h[3] = { s[2 * i], s[2 * i + 1], 0 };
    if (!isxdigit((unsigned char)h[0]) || !isxdigit((unsigned char)h[1])) return false;
    o[i] = (uint8_t)strtoul(h, NULL, 16);
  }
  return true;
}

// Eén parameter zetten. Geeft NULL bij succes, anders een foutmelding.
static bool parse_hex(const char* s, uint8_t* o, int n);   // verderop
static const char* set_param(const char* k, const char* v) {
  MtCfg c = mt_cfg;
  bool ok = false;
  uint32_t d;
  if      (!strcmp(k, "min_speed"))      ok = parse_u16(v, 300, &c.min_speed_kmh);
  else if (!strcmp(k, "min_dist"))       ok = parse_u16(v, 50000, &c.min_dist_m);
  else if (!strcmp(k, "turn_min"))       ok = parse_u16(v, 180, &c.turn_min_deg);
  else if (!strcmp(k, "turn_min_speed")) ok = parse_u16(v, 300, &c.turn_min_speed_kmh);
  else if (!strcmp(k, "min_interval"))   { ok = parse_dur(v, &d) && d >= 10 && d <= 86400; c.min_interval_s = d; }
  else if (!strcmp(k, "max_interval"))   { ok = parse_dur(v, &d) && d <= 86400; c.max_interval_s = d; }
  else if (!strcmp(k, "still_timeout"))  { ok = parse_dur(v, &d) && d >= 30 && d <= 86400; c.still_timeout_s = d; }
  else if (!strcmp(k, "heartbeat"))      { ok = parse_dur(v, &d) && (d == 0 || d >= 60) && d <= 7 * 86400UL; c.heartbeat_s = d; }
  else if (!strcmp(k, "fix_timeout"))    { ok = parse_dur(v, &d) && d >= 10 && d <= 600; c.fix_timeout_s = d; }
  else if (!strcmp(k, "fix_timeout_hb")) { ok = parse_dur(v, &d) && d >= 10 && d <= 600; c.fix_timeout_hb_s = d; }
  else if (!strcmp(k, "sample"))         { ok = parse_dur(v, &d) && (d == 0 || d >= 5) && d <= 600; c.sample_s = d; }
  else if (!strcmp(k, "slow_log"))       { ok = parse_dur(v, &d) && (d == 0 || d >= 30) && d <= 86400; c.slow_log_s = d; }
  else if (!strcmp(k, "slow_send"))      { ok = parse_dur(v, &d) && d >= 60 && d <= 7 * 86400UL; c.slow_send_s = d; }
  else if (!strcmp(k, "fast_min_batt"))  { uint16_t r = 0; ok = parse_u16(v, 100, &r); c.fast_min_batt = (uint8_t)r; }
  else if (!strcmp(k, "sos"))            { uint8_t o = 1; ok = parse_onoff(v, &o); c.sos_off = o ? 0 : 1; }
  else if (!strcmp(k, "tx_beep"))        ok = parse_onoff(v, &c.tx_beep);
  else if (!strcmp(k, "heard_beep"))     ok = parse_onoff(v, &c.heard_beep);
  else if (!strcmp(k, "track_mode")) {
    ok = true;
    if (!strcmp(v, "classic")) c.track_mode = MT_TRACK_CLASSIC;
    else if (!strcmp(v, "fifo")) c.track_mode = MT_TRACK_FIFO;
    else ok = false;
  }
  else if (!strcmp(k, "fifo_max")) {          // fifo_min zakt mee als hij groter zou zijn
    uint16_t r = 0;
    ok = parse_u16(v, 500, &r) && r >= 20;
    if (ok) { c.fifo_max = r; if (c.fifo_min > r) c.fifo_min = r; }
  }
  else if (!strcmp(k, "fifo_min"))       { uint16_t r = 0; ok = parse_u16(v, c.fifo_max, &r) && r >= 1; if (ok) c.fifo_min = r; }
  else if (!strcmp(k, "fifo_gap"))       { ok = parse_dur(v, &d) && d >= 15 && d <= 300; if (ok) c.fifo_gap_s = d; }
  else if (!strcmp(k, "fifo_per_uur"))   { uint16_t r = 0; ok = parse_u16(v, 60, &r) && r >= 1; if (ok) c.fifo_per_uur = (uint8_t)r; }
  else if (!strcmp(k, "fifo_pogingen"))  { uint16_t r = 0; ok = parse_u16(v, 10, &r) && r >= 1; if (ok) c.fifo_pogingen = (uint8_t)r; }
  else if (!strcmp(k, "fifo_dun")) {         // "10" of "10m"
    char* end;
    long r = strtol(v, &end, 10);
    ok = end != v && (*end == 0 || !strcmp(end, "m")) && r >= 0 && r <= 100;
    if (ok) c.fifo_dun = (uint8_t)r;
  }
  else if (!strcmp(k, "fifo_wacht")) {       // "30m", "2h", "uit"/"0"; bewaard in minuten (1..254), 255 = uit
    uint32_t s = 0;
    if (!strcmp(v, "uit") || !strcmp(v, "off") || !strcmp(v, "0")) { c.fifo_wacht = 255; ok = true; }
    else {
      ok = parse_dur(v, &s) && s >= 60 && s <= 254UL * 60;
      if (ok) c.fifo_wacht = (uint8_t)((s + 59) / 60);
    }
  }
  else if (!strcmp(k, "fifo_snr")) {         // "-5" of "-5dB"
    char* end;
    long r = strtol(v, &end, 10);
    ok = end != v && (*end == 0 || !strcmp(end, "db") || !strcmp(end, "dB")) && r >= -20 && r <= 10;
    if (ok) c.fifo_snr = (int8_t)r;
  }
  else if (!strcmp(k, "transport")) {         // sinds 0.7 altijd kanaal; oude scripts sturen dit nog
    if (strcmp(v, "kanaal") && strcmp(v, "channel")) return "DM bestaat niet meer: altijd via het trackingkanaal";
    ok = true;
  }
  else if (!strcmp(k, "chan")) {
    uint16_t r; ChannelDetails ch;
    ok = parse_u16(v, MAX_GROUP_CHANNELS - 1, &r) && the_mesh.mtGetChannel(r, ch) && ch.name[0];
    if (ok) c.chan_idx = r;
  }
  else if (!strcmp(k, "authkey")) {
    ok = true;
    if (!strcmp(v, "-")) { c.authkey_set = 0; memset(c.authkey, 0, 16); }
    else {
      char h[40]; int j = 0;                  // spaties en streepjes mogen (zo toont de server hem)
      for (const char* q = v; *q && j < 39; q++) if (*q != ' ' && *q != '-') h[j++] = *q;
      h[j] = 0;
      ok = parse_hex(h, c.authkey, 16);
      if (ok) c.authkey_set = 1;
    }
  }
  else if (!strcmp(k, "track_in_companion")) ok = parse_onoff(v, &c.track_in_companion);
  else if (!strcmp(k, "accel_sens")) {
    ok = true;
    if (!strcmp(v, "laag") || !strcmp(v, "low")) c.accel_sens = 0;
    else if (!strcmp(v, "midden") || !strcmp(v, "med")) c.accel_sens = 1;
    else if (!strcmp(v, "hoog") || !strcmp(v, "high")) c.accel_sens = 2;
    else ok = false;
  }
  else if (!strcmp(k, "msg_beep")) {
    ok = true;
    if (!strcmp(v, "prive") || !strcmp(v, "privé") || !strcmp(v, "dm")) c.msg_beep = 0;
    else if (!strcmp(v, "alles") || !strcmp(v, "all")) c.msg_beep = 1;
    else if (!strcmp(v, "uit") || !strcmp(v, "nooit") || !strcmp(v, "off")) c.msg_beep = 2;
    else ok = false;
  }
  else if (!strcmp(k, "led")) {
    ok = true;
    if (!strcmp(v, "companion")) c.led_mode = 0;
    else if (!strcmp(v, "altijd") || !strcmp(v, "always")) c.led_mode = 1;
    else if (!strcmp(v, "uit") || !strcmp(v, "off")) c.led_mode = 2;
    else ok = false;
  }
  else return "onbekende parameter";
  if (!ok) return "ongeldige waarde";
  bool sens_changed = c.accel_sens != mt_cfg.accel_sens;
  mt_cfg = c;
  if (!mt_cfg_save()) return "NIET bewaard (opslag)";
  if (sens_changed) mt_motion_set_sens(c.accel_sens);
  return NULL;
}

// SlowTrack: één L-bericht per slow_send met zo'n 6 à 11 punten (minder bij grote afstanden of een lange
// naam; gemeten met een simulatie van send_slow). Logt hij er per periode
// veel meer, dan vallen tussenliggende punten weg (gelijkmatig uitgedund).
#define MT_SLOW_FIT 8
static void slow_ratio_warn(const char* k, const char* pre) {
  if (strcmp(k, "slow_log") && strcmp(k, "slow_send")) return;
  if (!mt_cfg.slow_log_s) return;
  uint32_t n = mt_cfg.slow_send_s / mt_cfg.slow_log_s;
  if (n <= MT_SLOW_FIT) return;   // zelfde grens als de webpagina (1/8)
  outl("%slet op: ~%lu punten per periode, er passen er ~%d in één bericht; de rest valt weg", pre, (unsigned long)n, MT_SLOW_FIT);
}

// FIFO: hoeveel leegmaakberichten per uur er effectief weg mogen, en hoe lang een volle wachtrij
// erover doet. Punten per Q-bericht = schatting op basis van de echte nodenaam: "<naam>: " gaat van
// de MT_TEXT_MAX tekens af, de vaste velden kosten ~79 tekens (incl. "|f"), een binair punt ~8.
// Dezelfde formule staat in serial.js (fifoPerMsg).
static uint32_t fifo_per_msg() { return mt_fifo_per_msg(); }   // formule in MtTracker.cpp
static void fmt_minutes(char* o, size_t n, uint32_t s) {
  uint32_t m = (s + 59) / 60;
  if (m < 60) snprintf(o, n, "%lu min", (unsigned long)(m ? m : 1));
  else { uint32_t t = (s + 180) / 360; snprintf(o, n, "%lu,%lu u", (unsigned long)(t / 10), (unsigned long)(t % 10)); }   // 3,1 u
}
static void fifo_summary(const char* pre) {
  uint32_t gap = mt_cfg.fifo_gap_s ? mt_cfg.fifo_gap_s : 30, per = mt_cfg.fifo_per_uur ? mt_cfg.fifo_per_uur : 1;
  uint32_t by_gap = 3600 / gap;
  bool capped = by_gap > per;                   // het uurplafond remt, niet fifo_gap
  uint32_t eff = capped ? per : by_gap;
  uint32_t pm = fifo_per_msg();
  uint32_t full = (mt_cfg.fifo_max + pm - 1) / pm;
  // Zonder plafond: elke fifo_gap één bericht. Met plafond: per uur 'per' berichten (telkens
  // fifo_gap na elkaar), dan wachten tot het oudste een uur oud is.
  uint32_t secs = !capped ? full * gap : ((full - 1) / per) * 3600 + (((full - 1) % per) + 1) * gap;
  char t[24];
  fmt_minutes(t, sizeof(t), secs);
  outl("%smax %lu berichten/uur (elke %lu s%s) ≈ %lu punten/uur; volle wachtrij (%u) ≈ %lu berichten, leeg in ≈ %s",
       pre, (unsigned long)eff, (unsigned long)gap, capped ? ", begrensd door fifo_per_uur" : "",
       (unsigned long)(eff * pm), (unsigned)mt_cfg.fifo_max, (unsigned long)full, t);
  outl("%s≈ %lu punten per bericht met de naam \"%s\" (%d bytes); een kortere naam zonder emoji laat meer punten toe",
       pre, (unsigned long)pm, the_mesh.mtSender(), the_mesh.mtSenderLen() - 2);
  uint32_t w = mt_fifo_wacht_min();
  {
    char a[24] = "nog geen", b[40];
    long age = mt_tracker_fifo_last_ok_age(), pw = mt_tracker_fifo_partial_wait();
    if (age >= 0) { fmt_minutes(a, sizeof(a), (uint32_t)age); strncat(a, " geleden", sizeof(a) - strlen(a) - 1); }
    if (pw < 0) snprintf(b, sizeof(b), "nooit (fifo_wacht uit)");
    else if (pw == 0) snprintf(b, sizeof(b), "nu");
    else { char m[16]; fmt_minutes(m, sizeof(m), (uint32_t)pw); snprintf(b, sizeof(b), "over %s", m); }
    outl("%slaatste geslaagde inhaalbericht: %s; niet-vol bericht weer mogelijk %s", pre, a, b);
  }
  if (w) {
    outl("%seen vol bericht = minstens %lu punten; een niet-vol inhaalbericht hooguit één keer per %lu min (fifo_wacht),",
         pre, (unsigned long)fifo_per_msg(), (unsigned long)w);
    outl("%sgeslaagd of niet, alleen bij sterke dekking (SNR >= %d dB of T1F) en als de radio toch al wakker is", pre,
         (int)mt_cfg.fifo_snr);
  }
  else outl("%seen vol bericht = minstens %lu punten; niet-volle berichten nooit (fifo_wacht uit)", pre, (unsigned long)fifo_per_msg());
  outl("%snu %u punten in de wachtrij, waarvan %u geparkeerd (na %u mislukte pogingen: lagere voorrang, nooit opgegeven)",
       pre, (unsigned)mt_tracker_fifo_count(), (unsigned)mt_tracker_fifo_parked(), (unsigned)mt_cfg.fifo_pogingen);
  if (capped)
    outl("%slet op: fifo_gap %lu s laat %lu berichten/uur toe, maar fifo_per_uur begrenst op %lu. Dat mag; de wachtrij loopt dan wel trager leeg.",
         pre, (unsigned long)gap, (unsigned long)by_gap, (unsigned long)per);
  outl("%sfifo_per_uur telt alleen berichten die een repeater herhaalde (of die de server bevestigde); pogingen",
       pre);
  outl("%szonder herhaling tellen niet, maar nooit meer dan %lu pogingen per uur (2x fifo_per_uur).", pre,
       (unsigned long)(2 * per));
}
static void fifo_set_note(const char* k, const char* pre) {
  if (!strcmp(k, "fifo_max") || !strcmp(k, "fifo_gap") || !strcmp(k, "fifo_per_uur") || !strcmp(k, "fifo_wacht"))
    fifo_summary(pre);
}

static void fmt_ago(char* o, size_t n, uint32_t ts) {
  uint32_t now = the_mesh.getRTCClock()->getCurrentTime();
  if (!ts || now < ts) { snprintf(o, n, "?"); return; }
  uint32_t s = now - ts;
  if (s < 120) snprintf(o, n, "%lu s", (unsigned long)s);
  else if (s < 7200) snprintf(o, n, "%lu min", (unsigned long)(s / 60));
  else snprintf(o, n, "%lu u %lu min", (unsigned long)(s / 3600), (unsigned long)(s % 3600 / 60));
}

// fifo: de wachtrij tonen; fifo wis ja: wissen.
static void cmd_fifo(const char* args) {
  if (!strcmp(args, "wis ja")) {
    outl(mt_tracker_fifo_clear() ? "fifo: wachtrij gewist (bewaard)" : "fifo: wachtrij gewist, maar NIET bewaard (opslag)");
    return;
  }
  if (*args) { outl("gebruik: fifo | fifo wis ja"); return; }
  uint16_t n = mt_tracker_fifo_count();
  outl("fifo: %u punten in de wachtrij (max %u, leegmaken vanaf %u, %u geparkeerd); track_mode=%s", (unsigned)n,
       (unsigned)mt_cfg.fifo_max, (unsigned)mt_cfg.fifo_min, (unsigned)mt_tracker_fifo_parked(),
       mt_cfg.track_mode == MT_TRACK_FIFO ? "fifo" : "classic");
  if (n) {
    char a[20], b[20];
    fmt_ago(a, sizeof(a), mt_tracker_fifo_oldest());
    fmt_ago(b, sizeof(b), mt_tracker_fifo_newest());
    outl("fifo: oudste=%lu (%s geleden) nieuwste=%lu (%s geleden)", (unsigned long)mt_tracker_fifo_oldest(), a,
         (unsigned long)mt_tracker_fifo_newest(), b);
  }
  char cov[24] = "nog niet gezien";
  if (mt_tracker_fifo_cov_age() >= 0) snprintf(cov, sizeof(cov), "%ld s geleden", mt_tracker_fifo_cov_age());
  uint32_t wait = 0;
  int hour = mt_tracker_fifo_hour(&wait);
  char w[48] = "";
  if (wait) snprintf(w, sizeof(w), "; weer een over %lu min", (unsigned long)((wait + 59) / 60));
  outl("fifo: leegmaken %s; laatste dekking %s", mt_tracker_fifo_state(), cov);
  outl("fifo: laatste uur %d getelde berichten (max %u) en %d pogingen (max %u)%s", hour, (unsigned)mt_cfg.fifo_per_uur,
       mt_tracker_fifo_tries(), 2U * mt_cfg.fifo_per_uur, w);
  outl("fifo: sinds de start %lu punten door de server bevestigd (T1F)", (unsigned long)mt_tracker_fifo_confirmed());
  fifo_summary("fifo: ");
}

// ---- mesh-instellingen (naam, radio, paden, regio) en de sleutel -----------------

static int hexval(char c) {
  if (c >= '0' && c <= '9') return c - '0';
  c = tolower((unsigned char)c);
  return (c >= 'a' && c <= 'f') ? c - 'a' + 10 : -1;
}

static bool parse_hex(const char* s, uint8_t* o, int n) {
  if ((int)strlen(s) != n * 2) return false;
  for (int i = 0; i < n; i++) {
    int a = hexval(s[2 * i]), b = hexval(s[2 * i + 1]);
    if (a < 0 || b < 0) return false;
    o[i] = (uint8_t)(a << 4 | b);
  }
  return true;
}

// Geeft NULL als de sleutel niet bij deze instellingen hoort (de gewone set_param).
// "radio" en "tx" werken pas na een herstart.
static const char* set_mesh(const char* k, char* v, bool* handled) {
  NodePrefs* p = the_mesh.getNodePrefs();
  *handled = true;
  if (!strcmp(k, "name")) {
    if (!*v || strlen(v) >= sizeof(p->node_name)) return "ongeldige waarde";
    strcpy(p->node_name, v);
  } else if (!strcmp(k, "radio")) {          // set radio 869.618 62.5 8 8
    char *e1, *e2, *e3, *e4;          // geen sscanf("%f"): newlib-nano leest geen kommagetallen
    float f = strtod(v, &e1), bw = strtod(e1, &e2);
    int sf = strtol(e2, &e3, 10), cr = strtol(e3, &e4, 10);
    if (e1 == v || e2 == e1 || e3 == e2 || e4 == e3) return "gebruik: set radio <MHz> <BW kHz> <SF> <CR>";
    if (f < 150 || f > 2500 || bw < 7.8f || bw > 500 || sf < 5 || sf > 12 || cr < 5 || cr > 8) return "ongeldige waarde";
    p->freq = f; p->bw = bw; p->sf = sf; p->cr = cr;
  } else if (!strcmp(k, "tx")) {
    int tx = atoi(v);
    if (tx < -9 || tx > MAX_LORA_TX_POWER) return "ongeldige waarde";
    p->tx_power_dbm = tx;
  } else if (!strcmp(k, "path_bytes")) {     // 2 of 3 bytes per hop; 1 is niet toegelaten
    int b = atoi(v);
    if (b < 2 || b > 3) return "ongeldige waarde (2 of 3)";
    p->path_hash_mode = b - 1;
  } else if (!strcmp(k, "scope")) {          // regio: naam zonder #, of - voor geen
    if (!strcmp(v, "-")) {
      memset(p->default_scope_name, 0, sizeof(p->default_scope_name));
      memset(p->default_scope_key, 0, sizeof(p->default_scope_key));
    } else {
      if (*v == '#') v++;
      if (!*v || strlen(v) >= sizeof(p->default_scope_name)) return "ongeldige waarde";
      char tag[34];
      snprintf(tag, sizeof(tag), "#%s", v);
      TransportKeyStore temp;
      TransportKey key;
      temp.getAutoKeyFor(0xFFFF, tag, key);
      memset(p->default_scope_name, 0, sizeof(p->default_scope_name));
      strcpy(p->default_scope_name, v);
      memcpy(p->default_scope_key, key.key, sizeof(p->default_scope_key));
    }
  } else { *handled = false; return NULL; }
  the_mesh.savePrefs();
  return NULL;
}

static void cmd_key(char* args) {
  char* sub = strtok(args, " ");
  char* v = strtok(NULL, " ");
  if (sub && !strcmp(sub, "export")) {
    uint8_t prv[64];
    the_mesh.mtExportKey(prv);
    out("privkey=");
    for (int i = 0; i < 64; i++) out("%02X", prv[i]);
    outl("");
    memset(prv, 0, sizeof(prv));
  } else if (sub && !strcmp(sub, "import") && v) {
    uint8_t prv[64];
    if (!parse_hex(v, prv, 64)) { outl("key: ongeldige sleutel (128 hex)"); return; }
    bool ok = the_mesh.mtImportKey(prv);
    memset(prv, 0, sizeof(prv));
    if (!ok) { outl("key: sleutel geweigerd of niet bewaard"); return; }
    outl("key: sleutel bewaard; 'reboot' om hem te gebruiken");
  } else outl("gebruik: key export | key import <128 hex>");
}

// chan list: "chan=<nr>|<32 hex>|<naam>" per kanaal; chan set <nr> <32 hex> <naam>; chan del <nr>
static void cmd_chan(char* args) {
  char* sub = args;
  char* rest = strchr(args, ' ');
  if (rest) { *rest++ = 0; while (*rest == ' ') rest++; } else rest = (char*)"";
  if (!strcmp(sub, "list")) {
    for (int i = 0; i < MAX_GROUP_CHANNELS; i++) {
      ChannelDetails ch;
      if (!the_mesh.mtGetChannel(i, ch) || !ch.name[0]) continue;
      out("chan=%d|", i);
      for (int j = 0; j < 16; j++) out("%02X", ch.channel.secret[j]);
      outl("|%s", ch.name);
    }
    outl("chan=einde");
  } else if (!strcmp(sub, "set") || !strcmp(sub, "del")) {
    char* nr = rest;
    char* sec = strchr(rest, ' ');
    if (sec) { *sec++ = 0; while (*sec == ' ') sec++; }
    int idx = atoi(nr);
    uint8_t secret[16];
    memset(secret, 0, sizeof(secret));
    const char* name = "";
    if (!strcmp(sub, "set")) {
      char* nm = sec ? strchr(sec, ' ') : NULL;
      if (nm) { *nm++ = 0; while (*nm == ' ') nm++; }
      if (!sec || !nm || !*nm || !parse_hex(sec, secret, 16)) { outl("gebruik: chan set <nr> <32 hex> <naam>"); return; }
      name = nm;
    }
    if (idx < 0 || idx >= MAX_GROUP_CHANNELS || !*nr) { outl("chan: ongeldig nummer"); return; }
    outl(the_mesh.mtSetChannel(idx, name, secret) ? "chan %d bewaard" : "chan %d: NIET bewaard", idx);
  } else outl("gebruik: chan list | chan set <nr> <32 hex> <naam> | chan del <nr>");
}

// ---- status (machine-leesbaar: de webpagina leest de key=waarde-paren) ---------

static void cmd_status() {
  NodePrefs* p = the_mesh.getNodePrefs();
  char a[12], b[12], c[12], d[12];
  uint16_t mv = board.getBattMilliVolts();
  MtNmeaProvider& g = mt_gps();
  const MtSendStats& st = mt_sender_stats();
  outl("fw=%s meshcore=%s", MT_FW_VERSION, FIRMWARE_VERSION);
  outl("naam=%s", p->node_name);
  out("pubkey="); for (int i = 0; i < PUB_KEY_SIZE; i++) out("%02X", the_mesh.self_id.pub_key[i]); outl("");
  outl("modus: actief=%s gekozen=%s usb=%s ble=%s", mt_mode_name(mt_effective_mode()), mt_mode_name(mt_cfg.mode),
       mt_usb() ? "ja" : "nee", ui_task.isBluetoothEnabled() ? "aan" : "uit");
  outl("batt=%umV batt_pct=%d radio=%.3fMHz SF%u BW%.1f CR%u TX%ddBm", mv, mt_battery_pct(mv),
       (double)p->freq, (unsigned)p->sf, (double)p->bw, (unsigned)p->cr, (int)p->tx_power_dbm);
  outl("freq=%.3f bw=%.1f sf=%u cr=%u tx=%d path_bytes=%u scope=%s", (double)p->freq, (double)p->bw,
       (unsigned)p->sf, (unsigned)p->cr, (int)p->tx_power_dbm, (unsigned)p->path_hash_mode + 1,
       p->default_scope_name[0] ? p->default_scope_name : "-");
  fmt_dur(a, sizeof(a), mt_cfg.min_interval_s);  fmt_dur(b, sizeof(b), mt_cfg.max_interval_s);
  fmt_dur(c, sizeof(c), mt_cfg.still_timeout_s); fmt_dur(d, sizeof(d), mt_cfg.heartbeat_s);
  outl("min_speed=%ukm/h min_dist=%um turn_min=%udeg turn_min_speed=%ukm/h",
       mt_cfg.min_speed_kmh, mt_cfg.min_dist_m, mt_cfg.turn_min_deg, mt_cfg.turn_min_speed_kmh);
  outl("min_interval=%s max_interval=%s still_timeout=%s heartbeat=%s", a, b, c, d);
  fmt_dur(a, sizeof(a), mt_cfg.fix_timeout_s); fmt_dur(b, sizeof(b), mt_cfg.fix_timeout_hb_s);
  outl("fix_timeout=%s fix_timeout_hb=%s track_in_companion=%s accel_sens=%s led=%s msg_beep=%s",
       a, b, mt_cfg.track_in_companion ? "aan" : "uit",
       mt_cfg.accel_sens == 0 ? "laag" : mt_cfg.accel_sens == 2 ? "hoog" : "midden",
       mt_cfg.led_mode == 1 ? "altijd" : mt_cfg.led_mode == 2 ? "uit" : "companion",
       mt_cfg.msg_beep == 1 ? "alles" : mt_cfg.msg_beep == 2 ? "uit" : "prive");
  outl("tracker=%s gps=%s fix=%s sat=%ld beweging=%s radio=%s",
       mt_tracker_state_str(), mt_tracker_gps_on() ? "aan" : "uit", g.freshFix(5000) ? "ja" : "nee",
       g.satellitesCount(), mt_motion_mode_str(), mt_radio_paused() ? "slaapt" : "aan");
  {
    char h[8] = "-";
    if (mt_tracker_heard() >= 0) snprintf(h, sizeof(h), "%d", mt_tracker_heard());
    outl("tx_ok=%lu tx_mislukt=%lu laatste=%s seq=%u reden=%s gehoord=%s sos_bevestigd=%s",
         (unsigned long)st.ok, (unsigned long)st.failed,
         !st.have_last ? "-" : st.last_ok ? "ok" : "mislukt", mt_tracker_seq(), mt_tracker_last_reason(), h,
         mt_tracker_sos_confirmed());
  }
  fmt_dur(a, sizeof(a), mt_cfg.sample_s);
  outl("sample=%s buffer=%u", a, (unsigned)mt_tracker_buffered());
  fmt_dur(a, sizeof(a), mt_cfg.slow_log_s); fmt_dur(b, sizeof(b), mt_cfg.slow_send_s);
  outl("slow_log=%s slow_send=%s fast_min_batt=%u sos=%s tx_beep=%s heard_beep=%s", a, b,
       (unsigned)mt_cfg.fast_min_batt, mt_cfg.sos_off ? "uit" : "aan", mt_cfg.tx_beep ? "aan" : "uit",
       mt_cfg.heard_beep ? "aan" : "uit");
  outl("slow_buffer=%u fasttrack=%s slow_per_bericht=6-11", (unsigned)mt_tracker_slow_buffered(),
       mt_tracker_fast_suspended() ? "uit(batterij)" : "aan");
  {
    char cov[12] = "-";
    if (mt_tracker_fifo_cov_age() >= 0) snprintf(cov, sizeof(cov), "%ld", mt_tracker_fifo_cov_age());
    outl("track_mode=%s fifo_max=%u fifo_min=%u fifo_gap=%us fifo_per_uur=%u fifo=%u fifo_dekking=%s",
         mt_cfg.track_mode == MT_TRACK_FIFO ? "fifo" : "classic", (unsigned)mt_cfg.fifo_max, (unsigned)mt_cfg.fifo_min,
         (unsigned)mt_cfg.fifo_gap_s, (unsigned)mt_cfg.fifo_per_uur, (unsigned)mt_tracker_fifo_count(), cov);
    outl("fifo_pogingen=%u fifo_geparkeerd=%u fifo_dun=%u fifo_snr=%d fifo_bevestigd=%lu", (unsigned)mt_cfg.fifo_pogingen,
         (unsigned)mt_tracker_fifo_parked(), (unsigned)mt_cfg.fifo_dun, (int)mt_cfg.fifo_snr,
         (unsigned long)mt_tracker_fifo_confirmed());
    char fw[12] = "uit";
    if (mt_fifo_wacht_min()) snprintf(fw, sizeof(fw), "%lum", (unsigned long)mt_fifo_wacht_min());
    outl("fifo_wacht=%s fifo_per_bericht=%lu", fw, (unsigned long)fifo_per_msg());
  }
  {
    ChannelDetails ch;
    bool have = the_mesh.mtGetChannel(mt_cfg.chan_idx, ch) && ch.name[0];
    out("transport=kanaal chan=%u authkey=%s afzender=%s chan_naam=", (unsigned)mt_cfg.chan_idx,
        mt_cfg.authkey_set ? "ja" : "nee", the_mesh.mtSender());
    outl("%s", have ? ch.name : "-");
  }
  {
    char fs[64];
    mt_fs_status(fs, sizeof(fs));
    outl("%s", fs);
  }
  outl("cfg=%s%s", mt_cfg_load_note, mt_cfg_readonly ? " [alleen-lezen]" : "");
}

// Interne opslag herstellen: formatteren en herstarten. Wist de identiteit en de MeshCore-voorkeuren,
// dus alleen als die toch al verloren zijn (opslag beschadigd of sleutel ongeldig). Daarna de sleutel
// terugzetten vanaf een back-up (Toestellen -> Klaarmaken -> Op dit toestel zetten, of key import).
static void cmd_fs(char* args) {
  const uint8_t* pk = the_mesh.self_id.pub_key;
  bool all_ff = true, all_00 = true;
  for (int i = 0; i < PUB_KEY_SIZE; i++) { all_ff &= pk[i] == 0xFF; all_00 &= pk[i] == 0x00; }
  bool fs_ok = mt_fs_internal_ok();
  bool broken = !fs_ok || all_ff || all_00;
  if (strcmp(args, "herstel ja") && strcmp(args, "herstel forceer ja")) {
    outl("fs: interne opslag %s, sleutel %s.", fs_ok ? "leesbaar" : "BESCHADIGD", all_ff || all_00 ? "ONGELDIG" : "in orde");
    outl("gebruik: fs herstel ja   (formatteert de interne opslag en herstart; alleen als ze beschadigd is of de sleutel ongeldig)");
    return;
  }
  if (!broken && strcmp(args, "herstel forceer ja")) {
    outl("fs: geweigerd: de opslag is in orde en de sleutel is geldig (dit zou de sleutel wissen).");
    return;
  }
  outl("fs: interne opslag wordt geformatteerd; daarna herstart. Zet daarna de sleutel terug vanaf een back-up.");
  delay(100);
  bool ok = mt_fs_internal_format();
  outl("fs: formatteren %s, herstart...", ok ? "gelukt" : "MISLUKT");
  delay(200);
  NVIC_SystemReset();
}

static void cmd_help() {
  outl("Commando's (ook met het menu open):");
  outl("  status | cfg                toestand, pubkey en instellingen");
  outl("  mode companion|tracker      modus bij batterij (USB = altijd companion)");
  outl("  set <param> <waarde>        tijden: 30s, 5m, 2h; 0 = uit");
  outl("    min_speed min_dist turn_min turn_min_speed min_interval max_interval");
  outl("    still_timeout heartbeat fix_timeout fix_timeout_hb");
  outl("    track_in_companion on|off  accel_sens laag|midden|hoog");
  outl("    sample <tijd>   in beweging elke x een punt bewaren, mee in het volgende bericht (0 = uit)");
  outl("    chan <nr>  authkey <32 hex>|-   (trackingkanaal en authsleutel; afzender = de naam)");
  outl("    led companion|altijd|uit (statusled; companion = uit in trackermodus)");
  outl("    msg_beep prive|alles|uit (biep bij berichten als companion zonder app; prive = alleen privéberichten)");
  outl("    slow_log <tijd>    SlowTrack: elke x een punt loggen zolang hij beweegt (0 = uit; minstens 30s)");
  outl("    slow_send <tijd>   SlowTrack: de gelogde punten elke x versturen, in één bericht (minstens 1m)");
  outl("      Tip: kies slow_log ongeveer 1/8 van slow_send (bv. slow_send 30m -> slow_log 4m). Eén bericht");
  outl("      bevat zo'n 6 à 11 punten (minder bij grote afstanden); logt hij er meer, dan vallen er");
  outl("      tussenliggende punten weg (gelijkmatig; het oudste en het nieuwste blijven).");
  outl("    fast_min_batt <0..100>  onder dit % batterij geen FastTrack (bewegingsberichten); SlowTrack,");
  outl("      heartbeat, klik en SOS werken door; weer aan vanaf 3 % hoger (0 = altijd FastTrack)");
  outl("    sos aan|uit        SOS door 2-8 s vasthouden (uit: geen SOS, geen wapenbiep; uitschakelen blijft)");
  outl("    tx_beep aan|uit    korte biep telkens de radio een positiebericht verzonden heeft (niet bij klik/SOS)");
  outl("    heard_beep aan|uit twee hoge biepjes als een repeater een positiebericht herhaalt (niet bij klik/SOS)");
  outl("    track_mode classic|fifo  classic = FastTrack + SlowTrack zoals voorheen; fifo = wachtrij voor posities");
  outl("      die de mesh niet haalden. Punten van een niet herhaald FastTrack-bericht liften eerst mee met de");
  outl("      volgende berichten; lukt dat niet, dan gaat alleen het hoofdpunt (met tijd) in de wachtrij.");
  outl("      SlowTrack-punten gaan er altijd in (geen slow_send). 500 punten is bij 1 bericht per 1 à 2 min");
  outl("      zo'n 8 à 16 uur zonder bereik. Bij stabiele dekking maakt hij de wachtrij leeg: oudste eerst,");
  outl("      zo'n 7 à 10 punten per Q-bericht (minder bij een lange naam of emoji).");
  outl("    fifo_max <20..500>      wachtrij hooguit zoveel punten (standaard 500); vol = het minst");
  outl("      betekenisvolle punt valt weg (dat het dichtst bij de lijn tussen zijn buren ligt)");
  outl("    fifo_min <1..fifo_max>  pas leegmaken vanaf zoveel punten (standaard 5; na een onderbreking vanaf 1)");
  outl("    fifo_gap <tijd>         tussen twee leegmaakberichten minstens (15s..5m, standaard 30s)");
  outl("    fifo_per_uur <1..60>    hooguit zoveel leegmaakberichten per uur (standaard 20). Telt alleen berichten");
  outl("      die een repeater herhaalde (of die de server bevestigde); pogingen zonder herhaling tellen niet,");
  outl("      maar nooit meer dan 2x zoveel pogingen per uur (vaste grens voor de eigen zendtijd).");
  outl("      Een volle wachtrij van 500 punten is zo'n 50 à 70 berichten; elk bericht wordt door meerdere");
  outl("      repeaters herhaald, dus hou fifo_gap ruim.");
  outl("    fifo_pogingen <1..10>   na zoveel niet herhaalde leegmaakberichten is een punt geparkeerd");
  outl("      (standaard 3): lagere voorrang en 60 min wachttijd, zodat het de wachtrij niet blokkeert.");
  outl("      Daarvoor wacht een punt na elke mislukte poging 1, 5 en 15 min. Een punt wordt nooit");
  outl("      opgegeven: het verdwijnt pas als een repeater zijn bericht herhaalt, als de server dat bericht");
  outl("      bevestigt (T1F per volgnummer) of als de wachtrij overloopt. In rust of als companion, met");
  outl("      sterke dekking, krijgen geparkeerde punten (hooguit eens per 30 min) een nieuwe kans.");
  outl("    fifo_dun <0..100>       punt op een rechte weg binnen zoveel m van de lijn = overbodig, weg");
  outl("      (standaard 10, 0 = uit); een punt bij een stop (tijdsprong > 10 min) blijft altijd");
  outl("    fifo_snr <-20..10>      een nieuwe leegmaakronde pas bij een pakket met SNR >= dit (dB, standaard -5),");
  outl("      of bij twee keer dekking binnen 60 s, of een bevestiging (T1F) van de server");
  outl("    fifo_wacht <duur>|uit    een niet-vol inhaalbericht hooguit één keer per fifo_wacht, geslaagd of niet");
  outl("      (standaard 30 min; 1m..4h; uit = nooit een niet-vol bericht). Bovendien moet het laatste geslaagde");
  outl("      inhaalbericht (herhaald door een repeater of bevestigd met een T1F) minstens zo lang geleden zijn.");
  outl("      Een niet-vol bericht gaat alleen weg bij sterke dekking (SNR >= fifo_snr of een T1F; twee keer");
  outl("      dekking binnen 60 s volstaat niet) en als de radio toch al wakker is: de tracker wekt de radio er");
  outl("      nooit voor. Een vol bericht (zoveel punten als er in één bericht passen) volgt de gewone regels.");
  outl("    In rust (langer dan still_timeout geen beweging) en met minstens één vol bericht in de wachtrij");
  outl("      luistert hij 60 s naar repeaters; zonder bereik opnieuw na 10, 20, 40 en daarna elke 60 min.");
  outl("  set name <naam> | set radio <MHz> <BW> <SF> <CR> | set tx <dBm>");
  outl("  set path_bytes 2|3 | set scope <regio>|-   (radio en tx na een reboot)");
  outl("  key export | key import <128 hex>   PRIVATE KEY (import na een reboot)");
  outl("  chan list | chan set <nr> <32 hex> <naam> | chan del <nr>");
  outl("  send                        nu een positie sturen (zoals een klik)");
  outl("  status: gehoord = herhalingen (repeaters) van de laatste klik/SOS; sos_bevestigd = antwoord van de server");
  outl("          slow_buffer = gelogde SlowTrack-punten die nog weg moeten; fasttrack=uit(batterij) = onder fast_min_batt");
  outl("          fifo = punten in de wachtrij; fifo_dekking = seconden sinds de laatste dekking (een repeater");
  outl("          gehoord: herhaling van een eigen bericht of een pakket via een repeater), - = nog nooit");
  outl("          fifo_geparkeerd = punten na fifo_pogingen mislukte pogingen (lagere voorrang); fifo_bevestigd =");
  outl("          sinds de start door de server bevestigde punten (T1F, per bericht)");
  outl("  fifo | fifo wis ja          wachtrij tonen (aantal, oudste en nieuwste punt) / wissen");
  outl("  dump                        interne toestand, machineleesbaar (webpagina /tracker; ook via Bluetooth,");
  outl("                              samen met status en fifo: alleen lezen)");
  outl("  defaults | backup | reboot | menu | q (menu sluiten)");
  outl("  rxlog aan|uit                ontvangen kanaalpakketten en -berichten loggen (tot een herstart)");
  outl("  fs | fs herstel ja          interne opslag controleren / herstellen (alleen als ze beschadigd is)");
}

// Ruwe dump van ExtraFS + InternalFS (0xD4000-0xF4000). Alleen lezen; bevat
// de private key, en is daarom alleen serieel bereikbaar.
static void cmd_backup() {
  const uint32_t start = 0xD4000UL, end = 0xF4000UL;
  static const char hx[] = "0123456789ABCDEF";
  uint32_t crc = 0;
  outl("BACKUP-BEGIN start=0x%08lX len=0x%08lX fw=%s", (unsigned long)start, (unsigned long)(end - start), MT_FW_VERSION);
  char line[8 + 1 + 128 + 2];
  for (uint32_t a = start; a < end; a += 64) {
    const uint8_t* p = (const uint8_t*)a;
    crc = mt_crc32(crc, p, 64);
    int n = snprintf(line, sizeof(line), "%08lX ", (unsigned long)a);
    for (int i = 0; i < 64; i++) { line[n++] = hx[p[i] >> 4]; line[n++] = hx[p[i] & 15]; }
    line[n++] = '\r'; line[n++] = '\n';
    Serial.write((const uint8_t*)line, n);
    if ((a & 0xFFF) == 0) yield();
  }
  outl("BACKUP-END crc32=%08lX", (unsigned long)crc);
}

// ---- menu ---------------------------------------------------------------------

enum Screen : uint8_t { SC_MAIN, SC_MODE, SC_WHEN, SC_RHYTHM, SC_REST, SC_GPS, SC_MAINT, SC_SLOW, SC_FIFO };

struct Item { const char* label; const char* param; uint8_t kind; };   // kind: 0 getal, 1 duur, 2 tekst
#define N_ITEMS(a) ((int)(sizeof(a) / sizeof((a)[0])))
static const Item WHEN[] = {
  {"Minimumsnelheid (km/u, 0 = altijd)", "min_speed", 0},
  {"Minimale verplaatsing (m)", "min_dist", 0},
  {"Scherpe bocht vanaf (graden, 0 = uit)", "turn_min", 0},
  {"Bochten pas boven (km/u)", "turn_min_speed", 0},
};
static const Item RHYTHM[] = {
  {"Nooit vaker dan 1x per", "min_interval", 1},
  {"In beweging minstens 1x per (0 = uit)", "max_interval", 1},
  {"Punt bewaren elke (0 = uit)", "sample", 1},
};
static const Item SLOW[] = {
  {"Punt loggen elke (0 = SlowTrack uit)", "slow_log", 1},
  {"Gelogde punten versturen elke", "slow_send", 1},
  {"Geen FastTrack onder (% batterij, 0 = uit)", "fast_min_batt", 0},
};
static const Item FIFOI[] = {
  {"Trackmodus (classic/fifo)", "track_mode", 2},
  {"Wachtrij hooguit (punten, 20-500)", "fifo_max", 0},
  {"Leegmaken vanaf (punten)", "fifo_min", 0},
  {"Tussen twee leegmaakberichten minstens", "fifo_gap", 1},
  {"Herhaalde leegmaakberichten per uur (1-60)", "fifo_per_uur", 0},
  {"Pogingen voor een punt geparkeerd wordt", "fifo_pogingen", 0},
  {"Rechte lijn uitdunnen (m, 0 = uit)", "fifo_dun", 0},
  {"Nieuwe ronde vanaf SNR (dB, -20..10)", "fifo_snr", 0},
  {"Wachttijd voor een niet-vol bericht", "fifo_wacht", 1},
};
static const Item REST[] = {
  {"Slapen na stilstand van", "still_timeout", 1},
  {"Heartbeat in rust (0 = uit)", "heartbeat", 1},
  {"Bewegingsgevoeligheid (laag/midden/hoog)", "accel_sens", 2},
  {"Biep bij berichten zonder app (prive/alles/uit)", "msg_beep", 2},
};
static const Item GPSI[] = {
  {"GPS-fix zoeken max.", "fix_timeout", 1},
  {"GPS-fix bij heartbeat/klik max.", "fix_timeout_hb", 1},
  {"Trackingkanaal (nummer, zie chan list)", "chan", 0},
  {"Authsleutel kanaal (32 hex, - = geen)", "authkey", 2},
};

static Screen s_screen = SC_MAIN;
static const Item* s_prompt = NULL;     // wacht op een nieuwe waarde

static void value_of(const char* param, char* o, size_t n) {
  if (!strcmp(param, "min_speed")) snprintf(o, n, "%u km/u", mt_cfg.min_speed_kmh);
  else if (!strcmp(param, "min_dist")) snprintf(o, n, "%u m", mt_cfg.min_dist_m);
  else if (!strcmp(param, "turn_min")) snprintf(o, n, mt_cfg.turn_min_deg ? "%u graden" : "uit", mt_cfg.turn_min_deg);
  else if (!strcmp(param, "turn_min_speed")) snprintf(o, n, "%u km/u", mt_cfg.turn_min_speed_kmh);
  else if (!strcmp(param, "min_interval")) fmt_dur_nl(o, n, mt_cfg.min_interval_s);
  else if (!strcmp(param, "max_interval")) fmt_dur_nl(o, n, mt_cfg.max_interval_s);
  else if (!strcmp(param, "still_timeout")) fmt_dur_nl(o, n, mt_cfg.still_timeout_s);
  else if (!strcmp(param, "heartbeat")) fmt_dur_nl(o, n, mt_cfg.heartbeat_s);
  else if (!strcmp(param, "accel_sens")) snprintf(o, n, "%s", mt_cfg.accel_sens == 0 ? "laag" : mt_cfg.accel_sens == 2 ? "hoog" : "midden");
  else if (!strcmp(param, "fix_timeout")) fmt_dur_nl(o, n, mt_cfg.fix_timeout_s);
  else if (!strcmp(param, "fix_timeout_hb")) fmt_dur_nl(o, n, mt_cfg.fix_timeout_hb_s);
  else if (!strcmp(param, "sample")) fmt_dur_nl(o, n, mt_cfg.sample_s);
  else if (!strcmp(param, "slow_log")) fmt_dur_nl(o, n, mt_cfg.slow_log_s);
  else if (!strcmp(param, "slow_send")) fmt_dur_nl(o, n, mt_cfg.slow_send_s);
  else if (!strcmp(param, "fast_min_batt")) snprintf(o, n, mt_cfg.fast_min_batt ? "%u %%" : "uit", (unsigned)mt_cfg.fast_min_batt);
  else if (!strcmp(param, "track_mode")) snprintf(o, n, "%s", mt_cfg.track_mode == MT_TRACK_FIFO ? "fifo" : "classic");
  else if (!strcmp(param, "fifo_max")) snprintf(o, n, "%u punten", (unsigned)mt_cfg.fifo_max);
  else if (!strcmp(param, "fifo_min")) snprintf(o, n, "%u punten", (unsigned)mt_cfg.fifo_min);
  else if (!strcmp(param, "fifo_gap")) fmt_dur_nl(o, n, mt_cfg.fifo_gap_s);
  else if (!strcmp(param, "fifo_per_uur")) snprintf(o, n, "%u", (unsigned)mt_cfg.fifo_per_uur);
  else if (!strcmp(param, "fifo_pogingen")) snprintf(o, n, "%u", (unsigned)mt_cfg.fifo_pogingen);
  else if (!strcmp(param, "fifo_dun")) snprintf(o, n, mt_cfg.fifo_dun ? "%u m" : "uit", (unsigned)mt_cfg.fifo_dun);
  else if (!strcmp(param, "fifo_snr")) snprintf(o, n, "%d dB", (int)mt_cfg.fifo_snr);
  else if (!strcmp(param, "fifo_wacht")) {
    if (mt_fifo_wacht_min()) fmt_dur_nl(o, n, mt_fifo_wacht_min() * 60); else snprintf(o, n, "uit");
  }
  else if (!strcmp(param, "chan")) {
    ChannelDetails ch;
    bool have = the_mesh.mtGetChannel(mt_cfg.chan_idx, ch) && ch.name[0];
    snprintf(o, n, "%u (%s)", (unsigned)mt_cfg.chan_idx, have ? ch.name : "leeg");
  }
  else if (!strcmp(param, "authkey")) snprintf(o, n, "%s", mt_cfg.authkey_set ? "ingesteld" : "niet ingesteld");
  else if (!strcmp(param, "msg_beep")) snprintf(o, n, "%s", mt_cfg.msg_beep == 1 ? "alles" : mt_cfg.msg_beep == 2 ? "uit" : "alleen privé");
  else o[0] = 0;
}

static const char* LINE = "  ==============================================================";

static void header(const char* title) {
  uint16_t mv = board.getBattMilliVolts();
  const MtSendStats& st = mt_sender_stats();
  outl("");
  outl(LINE);
  outl("   MeshTrack T1000-E  fw %s       %s", MT_FW_VERSION, title);
  outl(LINE);
  outl("   %s  |  modus %s%s  |  batterij %d%% (%u mV)", the_mesh.getNodePrefs()->node_name,
       mt_mode_name(mt_effective_mode()), mt_usb() ? " (USB)" : "", mt_battery_pct(mv), mv);
  outl("   tracker %s  |  GPS %s  |  laatste zending: %s", mt_tracker_state_str(),
       mt_tracker_gps_on() ? (mt_gps().freshFix(5000) ? "fix" : "zoekt") : "uit",
       !st.have_last ? "nog geen" : st.last_ok ? "verstuurd" : "mislukt");
  outl(LINE);
}

static void list_items_only(const Item* it, int n) {
  char v[40];
  for (int i = 0; i < n; i++) {
    value_of(it[i].param, v, sizeof(v));
    char lab[48];
    snprintf(lab, sizeof(lab), "%s ", it[i].label);
    int len = strlen(lab);
    while (len < 44) lab[len++] = '.';
    lab[len] = 0;
    outl("   %d  %s %s", i + 1, lab, v);
  }
}

static void list_items(const Item* it, int n) {
  list_items_only(it, n);
  outl("");
  outl("   0  Terug");
}

static void show() {
  s_prompt = NULL;
  switch (s_screen) {
    case SC_MAIN:
      header("HOOFDMENU");
      outl("   1  Status (alles)");
      outl("   2  Modus en knop");
      outl("   3  Wanneer een positie sturen");
      outl("   4  Ritme");
      outl("   5  SlowTrack en trackmodus (FIFO)");
      outl("   6  Stilstand en heartbeat");
      outl("   7  GPS en verzending");
      outl("   8  Nu een positie sturen");
      outl("   9  Onderhoud");
      outl("");
      outl("   0  Menu sluiten (commando's; 'menu' opent het weer)");
      break;
    case SC_MODE:
      header("MODUS EN KNOP");
      outl("   Op batterij nu gekozen: %s.  Met USB altijd companion.", mt_mode_name(mt_cfg.mode));
      outl("");
      outl("   1  Kies tracker   (BLE uit, radio slaapt, zenden bij beweging)");
      outl("   2  Kies companion (MeshCore-app, radio luistert altijd)");
      outl("   3  Ook posities sturen als companion ......... %s", mt_cfg.track_in_companion ? "aan" : "uit");
      outl("");
      outl("   4  Statusled ................................. %s",
           mt_cfg.led_mode == 1 ? "altijd" : mt_cfg.led_mode == 2 ? "uit" : "alleen als companion");
      outl("   5  SOS door vasthouden ....................... %s", mt_cfg.sos_off ? "uit" : "aan");
      outl("   6  Biep na elk verstuurd positiebericht ...... %s", mt_cfg.tx_beep ? "aan" : "uit");
      outl("   7  Biep als een repeater het herhaalt ........ %s", mt_cfg.heard_beep ? "aan" : "uit");
      outl("      (6 en 7 niet bij klik of SOS: die hebben hun eigen terugmelding)");
      outl("");
      outl("   Knop: 1x = positie nu, 2x = modus wisselen, 3x = buzzer aan/uit,");
      if (mt_cfg.sos_off) outl("         2-8 s vasthouden = niets (SOS uit), langer dan 8 s = uitschakelen.");
      else outl("         2-8 s vasthouden en loslaten = SOS, langer dan 8 s = uitschakelen.");
      outl("");
      outl("   0  Terug");
      break;
    case SC_WHEN:   header("WANNEER EEN POSITIE STUREN"); list_items(WHEN, N_ITEMS(WHEN)); break;
    case SC_RHYTHM: header("RITME"); list_items(RHYTHM, N_ITEMS(RHYTHM)); break;
    case SC_REST:   header("STILSTAND EN HEARTBEAT"); list_items(REST, N_ITEMS(REST)); break;
    case SC_SLOW:
      header("SLOWTRACK");
      outl("   Los van de gewone tracking: elke x een punt loggen (niet in rust: dan");
      outl("   pas weer bij beweging) en die punten samen in één bericht versturen. Eén");
      outl("   bericht bevat zo'n 6 à 11 punten (minder bij grote afstanden of een lange naam).");
      outl("   Tip: kies het loginterval ongeveer 1/8 van het verzendinterval");
      outl("   (bv. versturen elke 30 min -> loggen elke 4 min); logt hij meer, dan");
      outl("   vallen er tussenliggende punten weg.");
      outl("   Nu %u punten in de buffer; FastTrack %s.", (unsigned)mt_tracker_slow_buffered(),
           mt_tracker_fast_suspended() ? "uit (batterij te laag)" : "aan");
      outl("");
      list_items_only(SLOW, N_ITEMS(SLOW));
      outl("   4  Trackmodus en FIFO-wachtrij ................ %s", mt_cfg.track_mode == MT_TRACK_FIFO ? "fifo" : "classic");
      outl("");
      outl("   0  Terug");
      break;
    case SC_FIFO: {
      header("TRACKMODUS EN FIFO");
      outl("   classic: FastTrack en SlowTrack zoals voorheen (SlowTrack verstuurt elke slow_send).");
      outl("   fifo: gaten van uren tot een dag overbruggen, met zo weinig mogelijk verlies en zo");
      outl("   weinig mogelijk verkeer op de mesh:");
      outl("   - Hoort hij geen herhaling van een FastTrack-bericht, dan liften de punten eerst gratis");
      outl("     mee met de volgende berichten. Pas als dat niet meer kan, gaat het hoofdpunt (met");
      outl("     zijn tijd) in de wachtrij. SlowTrack-punten gaan er altijd in.");
      outl("   - 500 punten is bij 1 bericht per 1 à 2 min zo'n 8 à 16 uur zonder bereik. Is de");
      outl("     wachtrij vol, dan valt het minst betekenisvolle punt weg, niet het oudste: de vorm");
      outl("     van de route blijft. Op een rechte weg vallen overbodige punten meteen weg (uitdunnen).");
      outl("   - Leegmaken pas bij stabiele dekking (sterk genoeg signaal, of twee keer dekking binnen");
      outl("     60 s): geen verspilde berichten op de rand van het bereik. Oudste eerst, zo'n 7 à 10 punten");
      outl("     per bericht; een volle wachtrij is zo'n 50 à 70 berichten. Elk bericht wordt door meerdere");
      outl("     repeaters herhaald, dus hou fifo_gap ruim.");
      outl("   - Een punt dat na enkele pogingen niet doorraakt, wordt geparkeerd: lagere voorrang, het");
      outl("     blokkeert de rest niet, maar het wordt nooit opgegeven. De server bevestigt per bericht");
      outl("     wat hij kreeg (T1F); alleen die punten verdwijnen uit de wachtrij.");
      char cov[24] = "nog niet gezien";
      if (mt_tracker_fifo_cov_age() >= 0) snprintf(cov, sizeof(cov), "%ld s geleden", mt_tracker_fifo_cov_age());
      outl("   Nu %u punten in de wachtrij; leegmaken %s; laatste dekking %s.", (unsigned)mt_tracker_fifo_count(),
           mt_tracker_fifo_state(), cov);
      fifo_summary("   ");
      outl("");
      list_items(FIFOI, N_ITEMS(FIFOI));
      break;
    }
    case SC_GPS:    header("GPS EN VERZENDING"); list_items(GPSI, N_ITEMS(GPSI)); break;
    case SC_MAINT:
      header("ONDERHOUD");
      outl("   1  Back-up van alle opslag (bevat de PRIVATE KEY)");
      outl("   2  Instellingen terug naar standaard");
      outl("   3  Herstarten");
      outl("");
      outl("   0  Terug");
      break;
  }
  out("\r\n   Keuze: ");
}

static void prompt_for(const Item* it) {
  char v[40];
  value_of(it->param, v, sizeof(v));
  s_prompt = it;
  outl("");
  outl("   %s  (nu: %s)", it->label, v);
  if (it->kind == 1) outl("   Voorbeelden: 30s, 5m, 2h. Enter = niets wijzigen.");
  out("   Nieuwe waarde: ");
}

static void open_menu() { s_in_menu = true; s_screen = SC_MAIN; show(); }

static void menu_choice(int n) {
  const Item* items = NULL;
  int count = 0;
  switch (s_screen) {
    case SC_MAIN:
      switch (n) {
        case 0: s_in_menu = false; outl(""); outl("Menu gesloten. 'help' voor commando's, 'menu' om terug te keren."); return;
        case 1: outl(""); cmd_status(); out("\r\n   Enter = terug: "); s_screen = SC_MAIN; return;
        case 2: s_screen = SC_MODE; break;
        case 3: s_screen = SC_WHEN; break;
        case 4: s_screen = SC_RHYTHM; break;
        case 5: s_screen = SC_SLOW; break;
        case 6: s_screen = SC_REST; break;
        case 7: s_screen = SC_GPS; break;
        case 8:
          outl("");
          outl(mt_tracker_manual() ? "   Positie wordt verstuurd (GPS-fix zoeken, daarna op het trackingkanaal)."
                                   : "   Niet verstuurd: trackingkanaal ontbreekt, of minder dan 10 s na de vorige.");
          break;
        case 9: s_screen = SC_MAINT; break;
        default: break;
      }
      show();
      return;
    case SC_MODE:
      if (n == 1) mt_choose_mode(MT_MODE_TRACKER, false);
      else if (n == 2) mt_choose_mode(MT_MODE_COMPANION, false);
      else if (n == 3) set_param("track_in_companion", mt_cfg.track_in_companion ? "off" : "on");
      else if (n == 4) set_param("led", mt_cfg.led_mode == 0 ? "altijd" : mt_cfg.led_mode == 1 ? "uit" : "companion");
      else if (n == 5) set_param("sos", mt_cfg.sos_off ? "aan" : "uit");
      else if (n == 6) set_param("tx_beep", mt_cfg.tx_beep ? "uit" : "aan");
      else if (n == 7) set_param("heard_beep", mt_cfg.heard_beep ? "uit" : "aan");
      else if (n == 0) s_screen = SC_MAIN;
      show();
      return;
    case SC_MAINT:
      if (n == 1) { outl(""); cmd_backup(); }
      else if (n == 2) { mt_cfg_defaults(mt_cfg); outl("   %s", mt_cfg_save() ? "Standaardwaarden bewaard." : "NIET bewaard."); }
      else if (n == 3) { outl("   Herstarten..."); delay(100); NVIC_SystemReset(); }
      else if (n == 0) s_screen = SC_MAIN;
      show();
      return;
    case SC_WHEN: items = WHEN; count = N_ITEMS(WHEN); break;
    case SC_RHYTHM: items = RHYTHM; count = N_ITEMS(RHYTHM); break;
    case SC_REST: items = REST; count = N_ITEMS(REST); break;
    case SC_SLOW:
      if (n == 4) { s_screen = SC_FIFO; show(); return; }
      items = SLOW; count = N_ITEMS(SLOW);
      break;
    case SC_FIFO:
      if (n == 0) { s_screen = SC_SLOW; show(); return; }
      items = FIFOI; count = N_ITEMS(FIFOI);
      break;
    case SC_GPS: items = GPSI; count = N_ITEMS(GPSI); break;
  }
  if (n == 0) { s_screen = SC_MAIN; show(); return; }
  if (n >= 1 && n <= count) { prompt_for(&items[n - 1]); return; }
  show();
}

// ---- commando's ------------------------------------------------------------------

static void command(char* s) {
  char* args = strchr(s, ' ');
  if (args) { *args++ = 0; while (*args == ' ') args++; } else args = (char*)"";
  for (char* p = s; *p; p++) *p = tolower((unsigned char)*p);
  if (!strcmp(s, "help") || !strcmp(s, "?")) cmd_help();
  else if (!strcmp(s, "status") || !strcmp(s, "cfg")) cmd_status();
  else if (!strcmp(s, "set")) {
    char* k = args;
    char* v = strchr(args, ' ');
    if (v) { *v++ = 0; while (*v == ' ') v++; }
    if (!*k || !v || !*v) { outl("gebruik: set <param> <waarde>"); return; }
    bool handled;
    const char* err = set_mesh(k, v, &handled);
    if (!handled) {
      char* sp = strchr(v, ' ');              // gewone parameters: één woord (de authsleutel mag spaties hebben)
      if (sp && strcmp(k, "authkey")) *sp = 0;
      err = set_param(k, v);
    }
    if (err) outl("%s: %s (%s)", k, err, v);
    else { outl("%s = %s (bewaard)", k, v); slow_ratio_warn(k, ""); fifo_set_note(k, ""); }
  }
  else if (!strcmp(s, "mode")) {
    if (!strcmp(args, "companion")) mt_choose_mode(MT_MODE_COMPANION, false);
    else if (!strcmp(args, "tracker")) mt_choose_mode(MT_MODE_TRACKER, false);
    else { outl("gebruik: mode companion|tracker"); return; }
    outl("gekozen modus: %s (bewaard)", mt_mode_name(mt_cfg.mode));
  }
  else if (!strcmp(s, "rxlog")) {
    bool on = !strcmp(args, "aan") || !strcmp(args, "on");
    mt_set_rxlog(on);
    ChannelDetails ch;
    bool have = the_mesh.mtGetChannel(mt_cfg.chan_idx, ch) && ch.name[0];
    outl("rxlog %s (tot een herstart); trackingkanaal %u heeft hash %02x", on ? "aan" : "uit",
         (unsigned)mt_cfg.chan_idx, have ? ch.channel.hash[0] : 0);
  }
  else if (!strcmp(s, "send")) outl(mt_tracker_manual() ? "positie wordt verstuurd" : "niet verstuurd (geen trackingkanaal of te snel)");
  else if (!strcmp(s, "defaults")) { mt_cfg_defaults(mt_cfg); outl("instellingen: standaard %s", mt_cfg_save() ? "(bewaard)" : "[NIET bewaard]"); }
  else if (!strcmp(s, "backup")) cmd_backup();
  else if (!strcmp(s, "key")) cmd_key(args);
  else if (!strcmp(s, "chan")) cmd_chan(args);
  else if (!strcmp(s, "reboot")) { outl("herstart..."); delay(100); NVIC_SystemReset(); }
  else if (!strcmp(s, "fs")) cmd_fs(args);
  else if (!strcmp(s, "fifo")) cmd_fifo(args);
  else if (!strcmp(s, "dump")) mt_tracker_dump(dump_out);
  else if (!strcmp(s, "menu")) open_menu();
  else if (!strcmp(s, "q")) { if (s_in_menu) { s_in_menu = false; outl(""); outl("Menu gesloten."); } }
  else outl("onbekend commando: %s ('help')", s);
}

static void handle_line(char* line) {
  char* s = line;
  while (*s == ' ') s++;
  if (s_in_menu && s_prompt) {
    const Item* it = s_prompt;
    s_prompt = NULL;
    if (*s) {
      const char* err = set_param(it->param, s);
      outl(err ? "   Fout: %s." : "   Bewaard.", err);
      if (!err) { slow_ratio_warn(it->param, "   "); fifo_set_note(it->param, "   "); }
    }
    show();
    return;
  }
  if (isdigit((unsigned char)*s) && s_in_menu) { menu_choice(atoi(s)); return; }
  if (!*s) { if (s_in_menu) show(); return; }
  if (isalpha((unsigned char)*s) || *s == '?') {
    command(s);
    if (s_in_menu && strcmp(s, "q") && strcmp(s, "menu")) out("\r\n   Keuze: ");
    return;
  }
  if (s_in_menu) show();
}

// ---- Bluetooth: alleen-lezen commando's (0.9.1) -----------------------------------------
// De app (webpagina /tracker) stuurt het companion-commando [0x7E]['M']['T']<ascii-commando>.
// Alleen status, fifo en dump: NOOIT set, key, backup, chan, fs, reboot ... over Bluetooth.
// De uitvoer gaat in een buffer (malloc, vrijgegeven na het versturen) en daarna in stukken
// [0x7E][volgnummer vanaf 0][utf8-tekst] naar de app, afgesloten met [0x7E][0xFF]. MyMesh stuurt
// één stuk per lus en alleen als de verzendrij van de interface niet druk is (gewoon companion-
// verkeer gaat voor).
static char* s_ble = nullptr;
static size_t s_ble_len = 0, s_ble_pos = 0;
static uint8_t s_ble_seq = 0;
static bool s_ble_active = false, s_ble_static = false;
static char s_ble_small[64];

static void ble_free() {
  if (s_ble && !s_ble_static) free(s_ble);
  s_ble = nullptr;
  s_ble_active = s_ble_static = false;
}

void mt_ble_cli(const char* cmd, int n) {
  ble_free();
  char c[24];
  int k = 0;
  for (int i = 0; i < n && k < (int)sizeof(c) - 1; i++) c[k++] = (char)tolower((unsigned char)cmd[i]);
  c[k] = 0;
  while (k && (c[k - 1] == ' ' || c[k - 1] == '\r' || c[k - 1] == '\n' || c[k - 1] == 0)) c[--k] = 0;
  int which = !strcmp(c, "status") ? 1 : !strcmp(c, "fifo") ? 2 : !strcmp(c, "dump") ? 3 : 0;
  size_t cap = which == 3 ? 24576 : which ? 4096 : sizeof(s_ble_small);
  char* buf = which ? (char*)malloc(cap) : nullptr;
  if (!buf) {                                  // geweigerd commando of geen geheugen
    snprintf(s_ble_small, sizeof(s_ble_small), which ? "fout: geen geheugen\n" : "alleen status, fifo en dump via Bluetooth\n");
    s_ble = s_ble_small;
    s_ble_static = true;
    s_ble_len = strlen(s_ble_small);
  } else {
    s_cap = buf; s_cap_max = cap; s_cap_len = 0; s_cap_over = false;
    buf[0] = 0;
    if (which == 1) cmd_status();
    else if (which == 2) cmd_fifo("");
    else {
      s_cap_max = cap - 24;                    // plaats voor de afsluiting als het niet past
      mt_tracker_dump(dump_out);
      s_cap_max = cap;
      if (s_cap_over) sink("afgekapt\nend\n");
    }
    if (s_cap_over && which != 3) sink("afgekapt\n");
    s_ble = buf;
    s_ble_len = s_cap_len;
    s_cap = nullptr;
  }
  s_ble_pos = 0;
  s_ble_seq = 0;
  s_ble_active = true;
  mt_log("bluetooth: '%s' (%u bytes)", which ? c : "geweigerd", (unsigned)s_ble_len);
}

bool mt_ble_pending() { return s_ble_active; }
void mt_ble_abort() { ble_free(); }

// Volgend stuk in buf (hooguit max bytes); 0 = niets meer. mt_ble_commit() na een geslaagde verzending.
static size_t s_ble_take = 0;
int mt_ble_next_frame(uint8_t* buf, int max) {
  if (!s_ble_active) return 0;
  if (s_ble_pos >= s_ble_len) {                // einde
    buf[0] = MT_BLE_CMD;
    buf[1] = 0xFF;
    s_ble_take = 0;
    return 2;
  }
  size_t room = (size_t)(max - 2), rest = s_ble_len - s_ble_pos, n = rest < room ? rest : room;
  if (n < rest) {
    size_t cut = n;                            // op een regeleinde knippen als dat kan
    while (cut > 0 && s_ble[s_ble_pos + cut - 1] != '\n') cut--;
    if (cut > room / 4) n = cut;
    else while (n > 1 && ((uint8_t)s_ble[s_ble_pos + n] & 0xC0) == 0x80) n--;   // niet midden in een UTF-8-teken
  }
  buf[0] = MT_BLE_CMD;
  buf[1] = s_ble_seq;
  memcpy(buf + 2, s_ble + s_ble_pos, n);
  s_ble_take = n;
  return (int)n + 2;
}

void mt_ble_commit() {
  if (!s_ble_active) return;
  if (s_ble_pos >= s_ble_len) { ble_free(); return; }   // eindframe verstuurd
  s_ble_pos += s_ble_take;
  s_ble_seq = s_ble_seq == 0xFE ? 0 : s_ble_seq + 1;     // 0xFF = einde
}

// ---- invoer ---------------------------------------------------------------------

static char s_line[200];
static uint8_t s_len = 0;
static bool s_last_cr = false;

void mt_menu_begin() {}

void mt_menu_loop() {
  if (s_suspended) return;
  bool conn = (bool)Serial;
  if (conn && !s_conn) { s_conn = true; s_len = 0; delay(50); open_menu(); }
  else if (!conn && s_conn) { s_conn = false; s_in_menu = false; }

  while (Serial.available()) {
    char ch = (char)Serial.read();
    if (ch == '\n' && s_last_cr) { s_last_cr = false; continue; }
    s_last_cr = (ch == '\r');
    if (ch == '\r' || ch == '\n') {
      out("\r\n");
      s_line[s_len] = 0;
      s_len = 0;
      handle_line(s_line);
      continue;
    }
    if (ch == 0x08 || ch == 0x7F) { if (s_len) { s_len--; out("\b \b"); } continue; }
    if (s_len < sizeof(s_line) - 1 && (uint8_t)ch >= 0x20) { s_line[s_len++] = ch; Serial.write(ch); }   // ook UTF-8 (namen)
  }
}
