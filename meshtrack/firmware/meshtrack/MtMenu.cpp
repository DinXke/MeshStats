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

static void out(const char* fmt, ...) {
  char buf[200];
  va_list ap; va_start(ap, fmt); vsnprintf(buf, sizeof(buf), fmt, ap); va_end(ap);
  Serial.print(buf);
}
static void outl(const char* fmt, ...) {
  char buf[200];
  va_list ap; va_start(ap, fmt); vsnprintf(buf, sizeof(buf), fmt, ap); va_end(ap);
  Serial.print(buf); Serial.print("\r\n");
}

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
  else if (!strcmp(k, "ack_retries"))    { uint16_t r; ok = parse_u16(v, 5, &r); c.ack_retries = r; }
  else if (!strcmp(k, "track_in_companion")) ok = parse_onoff(v, &c.track_in_companion);
  else if (!strcmp(k, "accel_sens")) {
    ok = true;
    if (!strcmp(v, "laag") || !strcmp(v, "low")) c.accel_sens = 0;
    else if (!strcmp(v, "midden") || !strcmp(v, "med")) c.accel_sens = 1;
    else if (!strcmp(v, "hoog") || !strcmp(v, "high")) c.accel_sens = 2;
    else ok = false;
  }
  else if (!strcmp(k, "target")) { ok = parse_key(v, c.target); if (ok) c.target_set = 1; }
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
  bool target_changed = memcmp(c.target, mt_cfg.target, 32) != 0 || c.target_set != mt_cfg.target_set;
  mt_cfg = c;
  if (!mt_cfg_save()) return "NIET bewaard (opslag)";
  if (sens_changed) mt_motion_set_sens(c.accel_sens);
  if (target_changed) mt_tracker_target_changed();
  return NULL;
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
  outl("fix_timeout=%s fix_timeout_hb=%s ack_retries=%u track_in_companion=%s accel_sens=%s led=%s",
       a, b, mt_cfg.ack_retries, mt_cfg.track_in_companion ? "aan" : "uit",
       mt_cfg.accel_sens == 0 ? "laag" : mt_cfg.accel_sens == 2 ? "hoog" : "midden",
       mt_cfg.led_mode == 1 ? "altijd" : mt_cfg.led_mode == 2 ? "uit" : "companion");
  out("target=");
  if (mt_cfg.target_set) for (int i = 0; i < 32; i++) out("%02X", mt_cfg.target[i]); else out("(niet_ingesteld)");
  outl("");
  outl("tracker=%s gps=%s fix=%s sat=%ld beweging=%s radio=%s",
       mt_tracker_state_str(), mt_tracker_gps_on() ? "aan" : "uit", g.freshFix(5000) ? "ja" : "nee",
       g.satellitesCount(), mt_motion_mode_str(), mt_radio_paused() ? "slaapt" : "aan");
  outl("tx_ok=%lu tx_mislukt=%lu herhaald=%lu laatste=%s seq=%u reden=%s",
       (unsigned long)st.ok, (unsigned long)st.failed, (unsigned long)st.retries,
       !st.have_last ? "-" : st.last_ok ? "ok" : "mislukt", mt_tracker_seq(), mt_tracker_last_reason());
  outl("cfg=%s%s", mt_cfg_load_note, mt_cfg_readonly ? " [alleen-lezen]" : "");
}

static void cmd_help() {
  outl("Commando's (ook met het menu open):");
  outl("  status | cfg                toestand, pubkey en instellingen");
  outl("  mode companion|tracker      modus bij batterij (USB = altijd companion)");
  outl("  set <param> <waarde>        tijden: 30s, 5m, 2h; 0 = uit");
  outl("    min_speed min_dist turn_min turn_min_speed min_interval max_interval");
  outl("    still_timeout heartbeat fix_timeout fix_timeout_hb ack_retries");
  outl("    track_in_companion on|off  accel_sens laag|midden|hoog  target <64 hex>");
  outl("    led companion|altijd|uit (statusled; companion = uit in trackermodus)");
  outl("  set name <naam> | set radio <MHz> <BW> <SF> <CR> | set tx <dBm>");
  outl("  set path_bytes 2|3 | set scope <regio>|-   (radio en tx na een reboot)");
  outl("  key export | key import <128 hex>   PRIVATE KEY (import na een reboot)");
  outl("  chan list | chan set <nr> <32 hex> <naam> | chan del <nr>");
  outl("  send                        nu een positie sturen (zoals een klik)");
  outl("  defaults | backup | reboot | menu | q (menu sluiten)");
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

enum Screen : uint8_t { SC_MAIN, SC_MODE, SC_WHEN, SC_RHYTHM, SC_REST, SC_GPS, SC_MAINT };

struct Item { const char* label; const char* param; uint8_t kind; };   // kind: 0 getal, 1 duur, 2 tekst
static const Item WHEN[] = {
  {"Minimum snelheid (km/u, 0 = altijd)", "min_speed", 0},
  {"Minimum verplaatsing (m)", "min_dist", 0},
  {"Scherpe bocht vanaf (graden, 0 = uit)", "turn_min", 0},
  {"Bochten pas boven (km/u)", "turn_min_speed", 0},
};
static const Item RHYTHM[] = {
  {"Nooit vaker dan 1x per", "min_interval", 1},
  {"In beweging minstens 1x per (0 = uit)", "max_interval", 1},
};
static const Item REST[] = {
  {"Slapen na stilstand van", "still_timeout", 1},
  {"Heartbeat in rust (0 = uit)", "heartbeat", 1},
  {"Bewegingsgevoeligheid (laag/midden/hoog)", "accel_sens", 2},
};
static const Item GPSI[] = {
  {"GPS-fix zoeken max.", "fix_timeout", 1},
  {"GPS-fix bij heartbeat/klik max.", "fix_timeout_hb", 1},
  {"Herhaalpogingen zonder ACK (0-5)", "ack_retries", 0},
  {"Doel: pubkey server-companion", "target", 2},
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
  else if (!strcmp(param, "ack_retries")) snprintf(o, n, "%u", mt_cfg.ack_retries);
  else if (!strcmp(param, "target")) {
    if (mt_cfg.target_set) snprintf(o, n, "%02X%02X%02X%02X...", mt_cfg.target[0], mt_cfg.target[1], mt_cfg.target[2], mt_cfg.target[3]);
    else snprintf(o, n, "niet ingesteld");
  } else o[0] = 0;
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
       !st.have_last ? "nog geen" : st.last_ok ? "OK (ACK)" : "mislukt");
  outl(LINE);
}

static void list_items(const Item* it, int n) {
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
      outl("   5  Stilstand en heartbeat");
      outl("   6  GPS en verzending");
      outl("   7  Nu een positie sturen");
      outl("   8  Onderhoud");
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
      outl("");
      outl("   Knop: 1x = positie nu, 2x = modus wisselen, 3x = buzzer aan/uit,");
      outl("         2-8 s vasthouden en loslaten = SOS, langer dan 8 s = uitschakelen.");
      outl("");
      outl("   0  Terug");
      break;
    case SC_WHEN:   header("WANNEER EEN POSITIE STUREN"); list_items(WHEN, 4); break;
    case SC_RHYTHM: header("RITME"); list_items(RHYTHM, 2); break;
    case SC_REST:   header("STILSTAND EN HEARTBEAT"); list_items(REST, 3); break;
    case SC_GPS:    header("GPS EN VERZENDING"); list_items(GPSI, 4); break;
    case SC_MAINT:
      header("ONDERHOUD");
      outl("   1  Backup van alle opslag (bevat de PRIVATE KEY)");
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
        case 5: s_screen = SC_REST; break;
        case 6: s_screen = SC_GPS; break;
        case 7:
          outl("");
          outl(mt_tracker_manual() ? "   Positie wordt verstuurd (GPS-fix zoeken, daarna ACK afwachten)."
                                   : "   Niet verstuurd: geen doel ingesteld, of minder dan 10 s na de vorige.");
          break;
        case 8: s_screen = SC_MAINT; break;
        default: break;
      }
      show();
      return;
    case SC_MODE:
      if (n == 1) mt_choose_mode(MT_MODE_TRACKER, false);
      else if (n == 2) mt_choose_mode(MT_MODE_COMPANION, false);
      else if (n == 3) set_param("track_in_companion", mt_cfg.track_in_companion ? "off" : "on");
      else if (n == 4) set_param("led", mt_cfg.led_mode == 0 ? "altijd" : mt_cfg.led_mode == 1 ? "uit" : "companion");
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
    case SC_WHEN: items = WHEN; count = 4; break;
    case SC_RHYTHM: items = RHYTHM; count = 2; break;
    case SC_REST: items = REST; count = 3; break;
    case SC_GPS: items = GPSI; count = 4; break;
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
      char* sp = strchr(v, ' ');              // gewone parameters: één woord
      if (sp) *sp = 0;
      err = set_param(k, v);
    }
    if (err) outl("%s: %s (%s)", k, err, v);
    else outl("%s = %s (bewaard)", k, v);
  }
  else if (!strcmp(s, "mode")) {
    if (!strcmp(args, "companion")) mt_choose_mode(MT_MODE_COMPANION, false);
    else if (!strcmp(args, "tracker")) mt_choose_mode(MT_MODE_TRACKER, false);
    else { outl("gebruik: mode companion|tracker"); return; }
    outl("gekozen modus: %s (bewaard)", mt_mode_name(mt_cfg.mode));
  }
  else if (!strcmp(s, "send")) outl(mt_tracker_manual() ? "positie wordt verstuurd" : "niet verstuurd (geen doel of te snel)");
  else if (!strcmp(s, "defaults")) { mt_cfg_defaults(mt_cfg); outl("instellingen: standaard %s", mt_cfg_save() ? "(bewaard)" : "[NIET bewaard]"); }
  else if (!strcmp(s, "backup")) cmd_backup();
  else if (!strcmp(s, "key")) cmd_key(args);
  else if (!strcmp(s, "chan")) cmd_chan(args);
  else if (!strcmp(s, "reboot")) { outl("herstart..."); delay(100); NVIC_SystemReset(); }
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
