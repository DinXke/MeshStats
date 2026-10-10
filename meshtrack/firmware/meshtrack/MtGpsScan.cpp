// GPS-pinnen en -baud zoeken op de RAK3401 (MeshTrack 0.9.7). Zie MtGpsScan.h.
//
// De nRF52840 kan een UART op eender welke pin leggen, dus volstaat Serial1 met andere pinnen. Kandidaten
// (RX/TX van de nRF): de UART-pinnen van de WisBlock-kern (UART1 P0.15/P0.16, UART2 P0.08/P0.06), in
// beide richtingen, telkens op 9600, 38400 en 115200 baud, elk ~3 s. NIET geprobeerd: pinnen die de
// radio of de versterker gebruiken (P0.04 reset, P0.09 busy, P0.10 DIO1, P0.21 FEM, P0.26/P0.03/P0.29/
// P0.30 SPI1), P0.34 (3V3_S en de 5V-boost van de versterker), de I2C-pinnen (P0.13/P0.14) en de leds.
// Gevonden = twee NMEA-zinnen ($G?...*hh) met een juiste checksum. De zoeker blokkeert nooit: hij leest in
// de lus wat er binnenkomt; ondertussen leest de NMEA-provider niet mee (setPaused).
//
// Opnieuw zoeken: bij het opstarten zonder bewaarde pinnen, met "gps zoek", en als de GPS 10 min geen
// geldige zin stuurde (hooguit eens per 30 min, zodat een tracker zonder GPS niet blijft zoeken).
#include "MtGpsScan.h"
#include "MtConfig.h"
#include "MeshTrack.h"
#include "MtGps.h"

#if defined(RAK_3401)

struct Cand { uint8_t rx, tx; };
static const Cand CANDS[] = { {15, 16}, {16, 15}, {8, 6}, {6, 8} };
static const uint32_t SCAN_BAUDS[] = { 9600, 38400, 115200 };
#define N_CANDS (sizeof(CANDS) / sizeof(CANDS[0]))
#define N_SBAUD (sizeof(SCAN_BAUDS) / sizeof(SCAN_BAUDS[0]))
#define SCAN_STEP_MS   3000UL
#define SILENT_MS      600000UL      // 10 min geen geldige zin = opnieuw zoeken
#define RESCAN_GAP_MS  1800000UL     // hooguit eens per 30 min automatisch

static bool s_busy = false;
static uint8_t s_idx = 0;            // kandidaat * N_SBAUD + baud
static uint32_t s_step_ms = 0;
static uint32_t s_applied_ms = 0;    // pinnen gezet (zwijgtijd telt vanaf hier)
static uint32_t s_last_scan_end = 0;
static char s_line[90];
static uint8_t s_len = 0;
static uint8_t s_good = 0;

static uint32_t baud_of(uint8_t code) { return code >= 1 && code <= MT_GPS_NBAUDS ? MT_GPS_BAUDS[code - 1] : 0; }
static uint8_t code_of(uint32_t baud) {
  for (uint8_t i = 0; i < MT_GPS_NBAUDS; i++) if (MT_GPS_BAUDS[i] == baud) return i + 1;
  return 0;
}

static void open_uart(uint8_t rx, uint8_t tx, uint32_t baud) {
  Serial1.end();
  Serial1.setPins(rx, tx);
  Serial1.begin(baud);
}

static void apply_cfg() {
  open_uart(mt_cfg.gps_rx, mt_cfg.gps_tx, baud_of(mt_cfg.gps_baud));
  s_applied_ms = millis() | 1;
}

static bool hexv(char c, uint8_t* v) {
  if (c >= '0' && c <= '9') { *v = c - '0'; return true; }
  if (c >= 'A' && c <= 'F') { *v = c - 'A' + 10; return true; }
  if (c >= 'a' && c <= 'f') { *v = c - 'a' + 10; return true; }
  return false;
}

// "$G?xxx,...*hh" met de juiste XOR-checksum
static bool nmea_ok(const char* l, uint8_t n) {
  if (n < 10 || l[0] != '$' || l[1] != 'G') return false;
  if (!strchr("PNLAB", l[2])) return false;
  uint8_t x = 0, i = 1;
  for (; i < n && l[i] != '*'; i++) x ^= (uint8_t)l[i];
  uint8_t h, lo;
  if (i + 2 >= n || !hexv(l[i + 1], &h) || !hexv(l[i + 2], &lo)) return false;   // "*hh" moet er volledig staan
  return x == (uint8_t)(h << 4 | lo);
}

static void open_step() {
  const Cand& c = CANDS[s_idx / N_SBAUD];
  open_uart(c.rx, c.tx, SCAN_BAUDS[s_idx % N_SBAUD]);
  s_step_ms = millis();
  s_len = 0;
  s_good = 0;
}

bool mt_gps_scan_start() {
  if (s_busy) return true;
  mt_log("gps: pinnen zoeken (%u mogelijkheden, elk %lu s)", (unsigned)(N_CANDS * N_SBAUD), SCAN_STEP_MS / 1000);
  mt_gps().setPaused(true);
  s_busy = true;
  s_idx = 0;
  open_step();
  return true;
}

static void scan_end(bool found) {
  s_busy = false;
  s_last_scan_end = millis() | 1;
  if (found) {
    const Cand& c = CANDS[s_idx / N_SBAUD];
    mt_cfg.gps_rx = c.rx;
    mt_cfg.gps_tx = c.tx;
    mt_cfg.gps_baud = code_of(SCAN_BAUDS[s_idx % N_SBAUD]);
    bool ok = mt_cfg_save();
    mt_log("gps: gevonden op RX P0.%02u / TX P0.%02u, %lu baud%s", (unsigned)c.rx, (unsigned)c.tx,
           (unsigned long)SCAN_BAUDS[s_idx % N_SBAUD], ok ? "" : " [NIET bewaard]");
    s_applied_ms = millis() | 1;
  } else {
    mt_log("gps: geen NMEA gevonden op de geteste pinnen; %s", mt_cfg.gps_baud ? "terug naar de bewaarde pinnen" : "opnieuw over 30 min (of 'gps zoek')");
    if (mt_cfg.gps_baud) apply_cfg();
    else open_uart(CANDS[0].rx, CANDS[0].tx, SCAN_BAUDS[0]);
    s_applied_ms = millis() | 1;
  }
  mt_gps().setPaused(false);
}

void mt_gps_scan_begin() {
  if (mt_cfg.gps_baud && mt_cfg.gps_rx && mt_cfg.gps_tx) {
    apply_cfg();
    mt_log("gps: pinnen RX P0.%02u / TX P0.%02u, %lu baud (bewaard)", (unsigned)mt_cfg.gps_rx, (unsigned)mt_cfg.gps_tx,
           (unsigned long)baud_of(mt_cfg.gps_baud));
  } else {
    mt_gps_scan_start();                       // eerste start: zoeken (niet-blokkerend)
  }
}

void mt_gps_scan_loop() {
  uint32_t now = millis();
  if (!s_busy) {
    // Zweeg de GPS 10 min (geen enkele geldige zin), dan opnieuw zoeken; hooguit eens per 30 min.
    uint32_t last = mt_gps().lastSentenceMs();
    uint32_t ref = last && (int32_t)(last - s_applied_ms) > 0 ? last : s_applied_ms;
    if (ref && now - ref >= SILENT_MS && (!s_last_scan_end || now - s_last_scan_end >= RESCAN_GAP_MS)) {
      mt_log("gps: al 10 min geen NMEA; opnieuw zoeken");
      mt_gps_scan_start();
    }
    return;
  }
  while (Serial1.available()) {
    char ch = (char)Serial1.read();
    if (ch == '$') s_len = 0;
    if (ch == '\r' || ch == '\n') {
      if (s_len && nmea_ok(s_line, s_len) && ++s_good >= 2) { scan_end(true); return; }
      s_len = 0;
      continue;
    }
    if (s_len < sizeof(s_line) - 1) s_line[s_len++] = ch;
    else s_len = 0;
  }
  if (now - s_step_ms >= SCAN_STEP_MS) {
    if (++s_idx >= N_CANDS * N_SBAUD) { scan_end(false); return; }
    open_step();
  }
}

bool mt_gps_scan_busy() { return s_busy; }

bool mt_gps_set_pins(uint8_t rx, uint8_t tx, uint32_t baud) {
  uint8_t code = code_of(baud);
  if (!code || rx == tx || rx > 47 || tx > 47) return false;
  if (s_busy) { s_busy = false; mt_gps().setPaused(false); }
  mt_cfg.gps_rx = rx;
  mt_cfg.gps_tx = tx;
  mt_cfg.gps_baud = code;
  apply_cfg();
  return mt_cfg_save();
}

void mt_gps_pins_str(char* out, size_t n) {
  if (s_busy) snprintf(out, n, "zoekt");
  else if (mt_cfg.gps_baud) snprintf(out, n, "%u/%u@%lu", (unsigned)mt_cfg.gps_rx, (unsigned)mt_cfg.gps_tx,
                                     (unsigned long)baud_of(mt_cfg.gps_baud));
  else snprintf(out, n, "-");
}

#else   // andere borden: GPS vast op het bord

void mt_gps_scan_begin() {}
void mt_gps_scan_loop() {}
bool mt_gps_scan_start() { return false; }
bool mt_gps_scan_busy() { return false; }
bool mt_gps_set_pins(uint8_t, uint8_t, uint32_t) { return false; }
void mt_gps_pins_str(char* out, size_t n) { snprintf(out, n, "vast"); }

#endif
