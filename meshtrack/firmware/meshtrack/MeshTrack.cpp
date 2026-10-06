#include "MeshTrack.h"
#include "MtConfig.h"
#include "MyMesh.h"
#include "UITask.h"
#include <helpers/MultiSerialInterface.h>
#include <string.h>
#include <stdlib.h>
#include <ctype.h>
#include <stdarg.h>

extern UITask ui_task;

// ---- modus ------------------------------------------------------------------

static bool    s_usb = false;
static MtMode  s_eff = MT_MODE_COMPANION;
static uint32_t s_next_usb_check = 0;
static bool    s_serial_suspended = false;   // stock rescue-CLI heeft Serial

static const char* mode_name(uint8_t m) { return m == MT_MODE_TRACKER ? "tracker" : "companion"; }

MtMode mt_effective_mode() { return s_eff; }

static MtMode wanted_mode() {
  return s_usb ? MT_MODE_COMPANION : (MtMode)mt_cfg.mode;
}

static void apply_mode(MtMode m) {
  if (m == MT_MODE_COMPANION) {
    if (!ui_task.isBluetoothEnabled()) ui_task.enableBluetooth();
  } else {
    if (ui_task.isBluetoothEnabled()) ui_task.disableBluetooth();
  }
  s_eff = m;
}

static void log_line(const char* fmt, ...) {
  if (s_serial_suspended || !Serial) return;
  char buf[160];
  va_list ap; va_start(ap, fmt); vsnprintf(buf, sizeof(buf), fmt, ap); va_end(ap);
  Serial.print(buf); Serial.print("\r\n");
}

static void reevaluate(bool beep) {
  MtMode w = wanted_mode();
  if (w == s_eff) return;
  apply_mode(w);
  log_line("modus: %s (%s)", mode_name(w), s_usb ? "USB-voeding" : "batterij");
  if (beep) ui_task.playModeTune(w == MT_MODE_TRACKER);
}

void mt_on_short_press() {
  ui_task.playModeTune(s_eff == MT_MODE_TRACKER);
}

void mt_on_double_press() {
  mt_cfg.mode = (mt_cfg.mode == MT_MODE_TRACKER) ? MT_MODE_COMPANION : MT_MODE_TRACKER;
  bool ok = mt_cfg_save();
  // De biep bevestigt de KEUZE, ook als USB die nog tegenhoudt.
  ui_task.playModeTune(mt_cfg.mode == MT_MODE_TRACKER);
  log_line("dubbelklik: gekozen modus %s%s%s", mode_name(mt_cfg.mode),
           s_usb && mt_cfg.mode == MT_MODE_TRACKER ? " (actief zodra USB los is)" : "",
           ok ? "" : " [NIET bewaard]");
  reevaluate(false);
}

void mt_on_cli_rescue() { s_serial_suspended = true; }

// ---- serieel ----------------------------------------------------------------

static char s_line[120];
static uint8_t s_len = 0;
static bool s_last_cr = false;
static bool s_ser_conn = false;

static void print_hex(const uint8_t* p, int n) {
  for (int i = 0; i < n; i++) Serial.printf("%02X", p[i]);
}

static void fmt_dur(char* out, size_t n, uint32_t s) {
  if (s == 0) snprintf(out, n, "uit");
  else if (s % 3600 == 0) snprintf(out, n, "%luh", (unsigned long)(s / 3600));
  else if (s % 60 == 0) snprintf(out, n, "%lum", (unsigned long)(s / 60));
  else snprintf(out, n, "%lus", (unsigned long)s);
}

static void banner() {
  Serial.printf("\r\nMeshTrack T1000-E fw %s (MeshCore %s)\r\n", MT_FW_VERSION, FIRMWARE_VERSION);
  Serial.printf("cfg: %s | 'help' voor commando's\r\n", mt_cfg_load_note);
}

static void cmd_status() {
  NodePrefs* p = the_mesh.getNodePrefs();
  char a[12], b[12], c[12], d[12];
  Serial.printf("fw=%s meshcore=%s\r\n", MT_FW_VERSION, FIRMWARE_VERSION);
  Serial.printf("naam=%s\r\n", p->node_name);
  Serial.print("pubkey="); print_hex(the_mesh.self_id.pub_key, PUB_KEY_SIZE); Serial.print("\r\n");
  Serial.printf("modus: actief=%s gekozen=%s usb=%s ble=%s\r\n", mode_name(s_eff), mode_name(mt_cfg.mode),
                s_usb ? "ja" : "nee", ui_task.isBluetoothEnabled() ? "aan" : "uit");
  Serial.printf("batt=%umV radio=%.3fMHz SF%u BW%.1f CR%u TX%ddBm\r\n", board.getBattMilliVolts(),
                (double)p->freq, (unsigned)p->sf, (double)p->bw, (unsigned)p->cr, (int)p->tx_power_dbm);
  fmt_dur(a, sizeof(a), mt_cfg.min_interval_s);  fmt_dur(b, sizeof(b), mt_cfg.max_interval_s);
  fmt_dur(c, sizeof(c), mt_cfg.still_timeout_s); fmt_dur(d, sizeof(d), mt_cfg.heartbeat_s);
  Serial.printf("min_speed=%ukm/h min_dist=%um turn_min=%udeg turn_min_speed=%ukm/h\r\n",
                mt_cfg.min_speed_kmh, mt_cfg.min_dist_m, mt_cfg.turn_min_deg, mt_cfg.turn_min_speed_kmh);
  Serial.printf("min_interval=%s max_interval=%s still_timeout=%s heartbeat=%s\r\n", a, b, c, d);
  Serial.printf("fix_timeout=%us fix_timeout_hb=%us ack_retries=%u track_in_companion=%s accel_sens=%s\r\n",
                mt_cfg.fix_timeout_s, mt_cfg.fix_timeout_hb_s, mt_cfg.ack_retries,
                mt_cfg.track_in_companion ? "aan" : "uit",
                mt_cfg.accel_sens == 0 ? "laag" : mt_cfg.accel_sens == 2 ? "hoog" : "midden");
  Serial.print("target=");
  if (mt_cfg.target_set) print_hex(mt_cfg.target, 32); else Serial.print("(niet ingesteld)");
  Serial.print("\r\n");
  Serial.printf("cfg-bestand: %s%s\r\n", mt_cfg_load_note, mt_cfg_readonly ? " [alleen-lezen]" : "");
  Serial.print("tracking: nog niet actief in 0.1 (alleen modus/knop/menu)\r\n");
}

static void cmd_help() {
  Serial.print("status | cfg                 toestand, pubkey en instellingen\r\n");
  Serial.print("mode companion|tracker       modus bij batterij (USB = altijd companion)\r\n");
  Serial.print("set <param> <waarde>         tijden mogen 30s, 5m, 12h; 0 = uit\r\n");
  Serial.print("  min_speed min_dist turn_min turn_min_speed (km/h, m, graden)\r\n");
  Serial.print("  min_interval max_interval still_timeout heartbeat fix_timeout fix_timeout_hb\r\n");
  Serial.print("  ack_retries track_in_companion on|off accel_sens laag|midden|hoog target <64 hex>\r\n");
  Serial.print("defaults                     instellingen terug naar standaard\r\n");
  Serial.print("backup                       ruwe dump van alle opslag (bevat de PRIVATE KEY)\r\n");
  Serial.print("reboot\r\n");
}

// "90", "90s", "5m", "12h" -> seconden. false bij onzin.
static bool parse_dur(const char* s, uint32_t* out) {
  char* end;
  unsigned long v = strtoul(s, &end, 10);
  if (end == s) return false;
  if (*end == 0 || *end == 's') *out = v;
  else if (*end == 'm') *out = v * 60;
  else if (*end == 'h') *out = v * 3600;
  else return false;
  return true;
}

static bool parse_u16(const char* s, uint16_t max, uint16_t* out) {
  char* end;
  unsigned long v = strtoul(s, &end, 10);
  if (end == s || *end || v > max) return false;
  *out = (uint16_t)v;
  return true;
}

static bool parse_onoff(const char* s, uint8_t* out) {
  if (!strcmp(s, "on") || !strcmp(s, "aan") || !strcmp(s, "1")) { *out = 1; return true; }
  if (!strcmp(s, "off") || !strcmp(s, "uit") || !strcmp(s, "0")) { *out = 0; return true; }
  return false;
}

static bool parse_key(const char* s, uint8_t* out) {
  if (strlen(s) != 64) return false;
  for (int i = 0; i < 32; i++) {
    char h[3] = { s[2 * i], s[2 * i + 1], 0 };
    if (!isxdigit((unsigned char)h[0]) || !isxdigit((unsigned char)h[1])) return false;
    out[i] = (uint8_t)strtoul(h, NULL, 16);
  }
  return true;
}

static void cmd_set(char* args) {
  char* k = strtok(args, " ");
  char* v = strtok(NULL, " ");
  if (!k || !v) { Serial.print("gebruik: set <param> <waarde>\r\n"); return; }
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
  else if (!strcmp(k, "heartbeat"))      { ok = parse_dur(v, &d) && d <= 7 * 86400UL; c.heartbeat_s = d; }
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
  else if (!strcmp(k, "target"))         { ok = parse_key(v, c.target); if (ok) c.target_set = 1; }
  else { Serial.printf("onbekende parameter: %s\r\n", k); return; }
  if (!ok) { Serial.printf("ongeldige waarde voor %s: %s\r\n", k, v); return; }
  mt_cfg = c;
  Serial.printf("%s = %s %s\r\n", k, v, mt_cfg_save() ? "(bewaard)" : "[NIET bewaard]");
  if (c.max_interval_s && c.max_interval_s < c.min_interval_s)
    Serial.print("let op: max_interval < min_interval, min_interval wint\r\n");
}

// Ruwe dump van ExtraFS (0xD4000, contacten/kanalen) + InternalFS (0xED000,
// identiteit/prefs/config/bonds) tot de bootloader (0xF4000). Alleen lezen.
// Zelfde formaat als MU-companion 2.3.1, zodat dezelfde dump.py werkt.
static void cmd_backup() {
  const uint32_t start = 0xD4000UL, end = 0xF4000UL;
  static const char hx[] = "0123456789ABCDEF";
  uint32_t crc = 0;
  Serial.printf("BACKUP-BEGIN start=0x%08lX len=0x%08lX fw=%s\r\n",
                (unsigned long)start, (unsigned long)(end - start), MT_FW_VERSION);
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
  Serial.printf("BACKUP-END crc32=%08lX\r\n", (unsigned long)crc);
}

static void dispatch(char* s) {
  while (*s == ' ') s++;
  if (!*s) return;
  char* args = strchr(s, ' ');
  if (args) { *args++ = 0; while (*args == ' ') args++; } else args = (char*)"";
  if (!strcmp(s, "help") || !strcmp(s, "?")) cmd_help();
  else if (!strcmp(s, "status") || !strcmp(s, "cfg")) cmd_status();
  else if (!strcmp(s, "set")) cmd_set(args);
  else if (!strcmp(s, "mode")) {
    if (!strcmp(args, "companion")) mt_cfg.mode = MT_MODE_COMPANION;
    else if (!strcmp(args, "tracker")) mt_cfg.mode = MT_MODE_TRACKER;
    else { Serial.print("gebruik: mode companion|tracker\r\n"); return; }
    Serial.printf("gekozen modus: %s %s\r\n", mode_name(mt_cfg.mode), mt_cfg_save() ? "(bewaard)" : "[NIET bewaard]");
    reevaluate(true);
  }
  else if (!strcmp(s, "defaults")) {
    mt_cfg_defaults(mt_cfg);
    Serial.printf("instellingen: standaard %s\r\n", mt_cfg_save() ? "(bewaard)" : "[NIET bewaard]");
    reevaluate(true);
  }
  else if (!strcmp(s, "backup")) cmd_backup();
  else if (!strcmp(s, "reboot")) { Serial.print("herstart...\r\n"); delay(100); NVIC_SystemReset(); }
  else Serial.printf("onbekend commando: %s ('help')\r\n", s);
}

static void serial_loop() {
  bool conn = (bool)Serial;
  if (conn && !s_ser_conn) { s_ser_conn = true; s_len = 0; banner(); }
  else if (!conn && s_ser_conn) s_ser_conn = false;

  while (Serial.available()) {
    char ch = (char)Serial.read();
    if (ch == '\n' && s_last_cr) { s_last_cr = false; continue; }
    s_last_cr = (ch == '\r');
    if (ch == '\r' || ch == '\n') {
      Serial.print("\r\n");
      s_line[s_len] = 0;
      s_len = 0;
      dispatch(s_line);
      continue;
    }
    if (ch == 0x08 || ch == 0x7F) { if (s_len) { s_len--; Serial.print("\b \b"); } continue; }
    if (s_len < sizeof(s_line) - 1 && ch >= 0x20) { s_line[s_len++] = ch; Serial.write(ch); }
  }
}

// ---- levenscyclus -----------------------------------------------------------

void mt_begin() {
  mt_cfg_begin();
  s_usb = board.isExternalPowered();
  apply_mode(wanted_mode());
}

void mt_loop() {
  if ((int32_t)(millis() - s_next_usb_check) >= 0) {
    s_next_usb_check = millis() + 500;
    bool usb = board.isExternalPowered();
    if (usb != s_usb) { s_usb = usb; reevaluate(true); }
  }
  if (!s_serial_suspended) serial_loop();
}
