/* Changelog of this module (see MESHSTATS_VERSION in MeshStatsNet.h).
 *
 * 1.5.0  Adverts are cached on the file system (key, name, type, last heard,
 *        coordinates), so node names survive a restart instead of showing bare
 *        hex until the next advert hours later. Written lazily, like the ACL.
 *        Also: a metric that is not actually available is now left out of the
 *        payload rather than published as 0 -- JessaZH was reporting
 *        noise_floor 0, which drew a line diving to zero on a graph where a
 *        gap belonged.
 * 1.4.0  A monitored repeater is now read with three requests instead of one:
 *        status, telemetry (CayenneLPP -> ch<N>_temperature / ch<N>_voltage)
 *        and neighbours, published as one message with a "neighbors" array.
 *        Any of the three may fail without losing the others. Per-type
 *        counters and trace lines say which part came back.
 * 1.3.1  A poll that stalled after a successful login looked exactly like one
 *        whose request was never sent -- both leave polls=1, oks=0, lr=1. Added
 *        a trace of the poll sequence (admin page, 'wifi mon trace', serial),
 *        and one flood retry per step: the status request is the first packet
 *        that depends on the path learned from the login, and nothing had ever
 *        tested that path.
 * 1.3.0  Fixed monitored repeaters never arriving anywhere: their statistics
 *        went to a '<prefix>/<node>/mon' topic that nothing subscribes to, so
 *        publish() succeeded and the data was dropped at the broker. Now on the
 *        ordinary stats topic, with the subject in the payload. Same mistake
 *        fixed for the neighbour list, which belongs inside the stats payload
 *        as a "neighbors" array. Separate polls/reads/published counters so a
 *        gap like that is visible without a sniffer. Chip temperature renamed
 *        mcu_temperature; ch1_temperature stays reserved for a real sensor.
 * 1.2.0  Monitor other repeaters: pick them from the heard list or paste a
 *        public key, log in with a password or via their access list, poll
 *        GET_STATUS over the mesh and publish the result to <prefix>/<node>/mon.
 * 1.1.0  Task watchdog: a hung loop() now becomes a reboot, so the existing
 *        boot-counter safety nets can actually fire. See WDT_TIMEOUT_S below.
 * 1.0.0  MQTT publishing (own stats + every raw packet), battery- and
 *        clock-aware publish interval with hysteresis, power-save WiFi mode
 *        with a forced-on escape hatch, admin page restyled after the public
 *        MeshStats site with light/dark themes and NL/EN translation, own
 *        version reported by 'ver', on the page and in the stats payload.
 */

#include "MeshStatsNet.h"
#include "MyMesh.h"

#include <WiFi.h>
#include <ESPAsyncWebServer.h>
#include <AsyncElegantOTA.h>
#include <PubSubClient.h>
#include <target.h>
#include <Update.h>
#include <esp_task_wdt.h>
#include <esp_idf_version.h>
#include <helpers/sensors/LPPDataHelpers.h>   // decodes the telemetry replies

#define MSNET_CFG_FILE    "/msnet.json"
#define MSNET_BOOT_FILE   "/msboot"

#define SSID_MAX      33
#define PASS_MAX      65
#define USER_MAX      17

#define MQTT_HOST_MAX     64
#define MQTT_USER_MAX     32
#define MQTT_PREFIX_MAX   32

/* Received packets wait here until msnet_loop() can ship them. Eight slots of
 * 255 bytes is ~2 kB of RAM, which buys roughly one burst of traffic; beyond
 * that we would rather lose packets than memory. */
#define MQTT_RX_QUEUE      8
#define MQTT_RX_MAX_LEN  255      // MAX_TRANS_UNIT
#define MQTT_RETRY_MS  15000UL    // do not hammer a broker that will not answer
#define MQTT_DRAIN_MAX     4      // packets per loop pass, so the mesh keeps its turn
/* Largest message we will ever hand to PubSubClient. The stats payload is the
 * big one because it carries the neighbour array: MAX_NEIGHBOURS entries of
 * ~70 characters each plus the envelope. Anything longer is truncated at the
 * source (fewer neighbours) rather than being refused here, because publish()
 * silently drops whatever exceeds its buffer. */
#define MQTT_PUB_MAX    5120

#define STA_TIMEOUT_MS      30000UL    // try this long before broadcasting our own SSID
#define STA_RETRY_MS       300000UL    // while in AP mode, retry the network every 5 min
/* Two safety nets, because a fault can also live in starting the web server --
 * safe mode itself would then hang in it.
 *   3 restarts: safe mode (own network + admin page, nothing else)
 *   6 restarts: this module does not start at all. What remains is a plain
 *               MeshCore repeater with its mesh CLI and 'start ota'.
 * Five minutes of continuous uptime counts as a successful start and resets
 * the counter. */
#define SAFE_MODE_BOOTS          3
#define DISABLE_BOOTS            6
#define STABLE_UPTIME_MS   300000UL

/* Cell voltage window used to derive a percentage. Same curve as the rest of
 * this project. It is crude at both ends of a LiPo discharge curve, but it
 * never has to be accurate: it only has to pick one of five buckets. */
#define BATT_EMPTY_MV     3000
#define BATT_FULL_MV      4200

/* Task watchdog. DO NOT REMOVE THIS BECAUSE IT LOOKS REDUNDANT -- it closes a
 * hole the other three safety nets cannot reach.
 *
 * The three nets above (safe mode after 3 restarts, module off after 6, no
 * halt() on radio failure) all key off *restarts*. We watched a sibling node
 * fail in a way that produces none: after a flash it answered on no TCP port
 * at all while ping kept working on and off. That is the signature of a
 * blocked loop(). WiFi and lwip live in their own FreeRTOS tasks and keep
 * answering pings, while the application task stands still. No crash, no
 * backtrace, no restart -- so the boot counter never advances and safe mode
 * never arrives. On a roof that is a dead node.
 *
 * This turns such a hang back into a restart, which is the one event the rest
 * of the machinery does know how to handle: the counter climbs, and a node
 * that keeps hanging lands in safe mode by itself. panic=true is deliberate:
 * the panic handler prints a backtrace before rebooting (the framework is
 * built with PANIC_PRINT_REBOOT), so the next person gets the diagnosis the
 * sibling node never produced.
 *
 * Why 30 s and not the framework default of 5 s: several things legitimately
 * block this task far longer than five seconds, and a spurious reboot loop on
 * a roof is worse than the illness. The long pole is an MQTT connect to a
 * broker given as a hostname -- lwip's DNS wait plus the socket timeout is
 * roughly 15-20 s of blocked loop() with nothing wrong. SPIFFS writes and a
 * flash erase add a few more. Thirty seconds clears that worst realistic case
 * with room to spare, and still brings a hung node back inside half a minute.
 *
 * Note this also relaxes the idle-task watchdog the core installs at 5 s, to
 * the same 30 s. That is intended: the operations above starve the idle task
 * for exactly the same reasons.
 */
#define WDT_TIMEOUT_S       30

#define FORCE_DEFAULT_MIN   30    // 'wifi on' without an argument

// Compiled-in defaults; overridable from the admin page or the CLI.
#ifndef WIFI_SSID
  #define WIFI_SSID ""
#endif
#ifndef WIFI_PWD
  #define WIFI_PWD ""
#endif

struct Config {
  char ssid[SSID_MAX];
  char pass[PASS_MAX];
  char ap_pass[PASS_MAX];
  char user[USER_MAX];              // console login
  char console_pass[PASS_MAX];

  // MQTT
  char mqtt_host[MQTT_HOST_MAX];
  char mqtt_user[MQTT_USER_MAX];
  char mqtt_pass[PASS_MAX];
  char mqtt_prefix[MQTT_PREFIX_MAX];
  uint16_t mqtt_port;
  uint16_t mqtt_enabled;
  uint16_t mqtt_rx;                 // also forward every received packet

  /* Power management. All of it is tunable rather than compiled in: the right
   * numbers depend on the panel, the cell and the season, and they have to be
   * changeable over the mesh without a reflash. */
  uint16_t pwr_mode;                // 0 = always reachable, 1 = power save
  uint16_t pwr_window;              // seconds reachable after waking
  uint16_t wifi_sleep;              // modem-sleep while associated
  uint16_t tx_power;                // dBm, 0 = leave the driver default alone
  uint16_t bat_full, bat_high, bat_norm, bat_crit;   // level boundaries in %
  uint16_t bat_hyst;                // % past a boundary before the level moves
  uint16_t bat_live;                // % above which raw packets go out at once
  uint16_t full_hold;               // minutes above bat_full before 'full' counts
  uint16_t iv_full, iv_high, iv_norm, iv_low, iv_crit;   // publish interval, secs
  uint16_t night_from, night_to;    // night window, hours UTC
  uint16_t night_factor;            // interval multiplier during that window
};

enum WifiState { WIFI_TRYING, WIFI_OK, WIFI_FALLBACK_AP };
enum PwrMode { PWR_ALWAYS = 0, PWR_SAVE = 1 };
enum BattLevel { LV_FULL = 0, LV_HIGH, LV_NORMAL, LV_LOW, LV_CRITICAL };

// Only used for CLI replies; the admin page translates level codes itself.
static const char *LEVEL_NL[] = { "vol", "hoog", "normaal", "laag", "kritiek" };
static const char HEXCHARS[] = "0123456789abcdef";

static FS *_fs = nullptr;
static MyMesh *_mesh = nullptr;
static Config _cfg;
static AsyncWebServer _server(80);
static WiFiServer _console(23);
static WiFiClient _client;

static WifiState _state = WIFI_TRYING;
static unsigned long _state_since = 0;
static unsigned long _last_retry = 0;
static bool _safe_mode = false;
static bool _disabled = false;
static bool _boot_cleared = false;
static bool _started = false;
static char _ap_ssid[SSID_MAX];
static char _node_hex[13];

// Power management state.
static bool _asleep = false;
static unsigned long _wake_at = 0;      // when to bring WiFi back up
static unsigned long _awake_until = 0;  // end of the reachability window
static unsigned long _force_until = 0;  // 'wifi on <min>' overrides everything
static uint8_t _level = LV_NORMAL;
static uint8_t _batt_pct = 0;
static uint16_t _batt_mv = 0;
static bool _batt_known = false;
static unsigned long _full_since = 0;   // first moment the cell read as full
static unsigned long _batt_read_at = 0;
static bool _published_this_wake = false;

/* The main loop runs thousands of times a second and the battery does not move
 * that fast. On some boards reading it also switches a divider on, so polling
 * it every pass would itself cost energy. */
#define BATT_POLL_MS   10000UL

// MQTT state.
static WiFiClient _mqtt_net;
static PubSubClient _mqtt(_mqtt_net);
static unsigned long _mqtt_last_try = 0;
static unsigned long _mqtt_last_push = 0;
static uint32_t _stats_count = 0;
static uint32_t _rx_count = 0;
static uint32_t _drop_count = 0;
static uint32_t _fail_count = 0;
/* Error as a code, not a sentence: the admin page speaks two languages and
 * translates it itself. "" | "conn" | "stats" | "pkt" */
static const char *_mqtt_err = "";
static int _mqtt_err_rc = 0;

struct RxItem {
  uint32_t ms;
  int16_t snr4;      // SNR times 4, the way the radio reports it
  int16_t rssi;
  uint8_t len;
  uint8_t data[MQTT_RX_MAX_LEN];
};
static RxItem _rx_queue[MQTT_RX_QUEUE];
static volatile uint8_t _rx_head = 0, _rx_tail = 0;

/* The web server runs in its own task. We never write settings from there, but
 * in loop(); these flags hand the work over. */
static volatile bool _apply_wifi = false;
static volatile bool _apply_mqtt = false;
static volatile bool _apply_power = false;

// Console state
enum ConsoleState { CON_USER, CON_PASS, CON_READY };
static ConsoleState _con_state = CON_USER;
static char _con_line[160];
static size_t _con_len = 0;
static uint8_t _con_tries = 0;
static unsigned long _con_active = 0;

/* A session that is gone still reports as 'connected' on the ESP32 for a
 * while. Without these two timers one aborted connection would close the
 * console for good -- exactly the channel you need when something is wrong.
 *   CON_IDLE_MS     we close a silent session ourselves after this
 *   CON_TAKEOVER_MS if the existing session is quiet longer than this, a new
 *                   connection may take it over instead of being refused
 */
#define CON_IDLE_MS       300000UL
#define CON_TAKEOVER_MS    60000UL

/* millis() wraps after ~49 days. The signed difference keeps every deadline
 * comparison in this file correct across that wrap. A deadline of 0 means
 * 'not scheduled'. */
static bool passed(unsigned long deadline) {
  return deadline != 0 && (long)(millis() - deadline) >= 0;
}

static uint32_t secsLeft(unsigned long deadline) {
  if (deadline == 0 || passed(deadline)) return 0;
  return (uint32_t)((deadline - millis()) / 1000UL);
}

// ------------------------------------------------------------------ watchdog

static bool _wdt_watching = false;
static unsigned long _wdt_ota_deadline = 0;

// A real upload over local WiFi is a matter of seconds; this is the point at
// which we stop believing one is still in progress.
#define WDT_OTA_MAX_MS   300000UL

/* Two different APIs, picked at compile time so a framework upgrade does not
 * quietly break the one safety net that catches hangs. Arduino core 2.x (IDF
 * 4.x, what this build uses) takes seconds plus a panic flag; core 3.x (IDF
 * 5.x) takes a config struct and refuses a second init, hence reconfigure. */
static void wdtBegin() {
#if ESP_IDF_VERSION_MAJOR >= 5
  esp_task_wdt_config_t cfg = {};
  cfg.timeout_ms = WDT_TIMEOUT_S * 1000;
  cfg.idle_core_mask = 0;          // leave idle-task subscriptions as the core set them
  cfg.trigger_panic = true;
  if (esp_task_wdt_init(&cfg) == ESP_ERR_INVALID_STATE) esp_task_wdt_reconfigure(&cfg);
#else
  esp_task_wdt_init(WDT_TIMEOUT_S, true);   // already running at 5 s; this retunes it
#endif
  // Called from setup(), so NULL is the task that also runs loop().
  if (esp_task_wdt_add(NULL) == ESP_OK) _wdt_watching = true;
  Serial.printf("MeshStatsNet: watchdog %s (%d s)\n",
                _wdt_watching ? "actief" : "NIET actief", WDT_TIMEOUT_S);
}

/* An OTA upload writes and erases flash from the async task. Those stretches
 * stop the world for longer than any normal operation, and a watchdog reboot
 * halfway through a firmware write is the one reboot we must not cause. So we
 * step out for the duration instead of trying to guess a timeout that covers
 * it. */
static void wdtFeed() {
  if (Update.isRunning()) {
    /* An abandoned upload (browser closed halfway) never reaches Update.end(),
     * so isRunning() would stay true forever and quietly leave this node
     * without a watchdog -- the exact silent failure this whole thing exists
     * to prevent. Hence a deadline on how long we are willing to believe it. */
    if (_wdt_ota_deadline == 0) _wdt_ota_deadline = millis() + WDT_OTA_MAX_MS;

    if (!passed(_wdt_ota_deadline)) {
      if (_wdt_watching && esp_task_wdt_delete(NULL) == ESP_OK) _wdt_watching = false;
      return;
    }
  } else {
    _wdt_ota_deadline = 0;
  }

  if (!_wdt_watching) {
    if (esp_task_wdt_add(NULL) != ESP_OK) return;
    _wdt_watching = true;
  }
  esp_task_wdt_reset();
}

// ------------------------------------------------------------------ settings

static void loadConfig() {
  memset(&_cfg, 0, sizeof(_cfg));
  strncpy(_cfg.ssid, WIFI_SSID, SSID_MAX - 1);
  strncpy(_cfg.pass, WIFI_PWD, PASS_MAX - 1);
  strcpy(_cfg.ap_pass, "meshcore");
  strcpy(_cfg.user, "admin");
  strcpy(_cfg.console_pass, "meshcore");

  strcpy(_cfg.mqtt_prefix, "meshcore");
  _cfg.mqtt_port = 1883;
  _cfg.mqtt_enabled = 0;
  _cfg.mqtt_rx = 1;

  _cfg.pwr_mode = PWR_ALWAYS;   // a new install stays reachable until told otherwise
  _cfg.pwr_window = 180;
  _cfg.wifi_sleep = 1;
  _cfg.tx_power = 0;
  _cfg.bat_full = 95;
  _cfg.bat_high = 90;
  _cfg.bat_norm = 70;
  _cfg.bat_crit = 40;
  _cfg.bat_hyst = 3;
  _cfg.bat_live = 85;
  _cfg.full_hold = 30;
  _cfg.iv_full = 60;
  _cfg.iv_high = 120;
  _cfg.iv_norm = 300;
  _cfg.iv_low = 900;
  _cfg.iv_crit = 3600;
  _cfg.night_from = 22;
  _cfg.night_to = 5;
  _cfg.night_factor = 4;

  if (!_fs) return;
  File f = _fs->open(MSNET_CFG_FILE, "r");
  if (!f) return;
  String s = f.readString();
  f.close();

  /* Very small parser: we write this file ourselves, so the format is fixed.
   * Anything missing keeps the default above, which is what makes adding a new
   * setting to an already-deployed node harmless. */
  auto grab = [&](const char *key, char *out, size_t max) {
    String pat = String("\"") + key + "\":\"";
    int i = s.indexOf(pat);
    if (i < 0) return;
    i += pat.length();
    int j = s.indexOf('"', i);
    if (j < 0) return;
    String v = s.substring(i, j);
    strncpy(out, v.c_str(), max - 1);
    out[max - 1] = 0;
  };
  auto num = [&](const char *key, uint16_t &out) {
    String pat = String("\"") + key + "\":";
    int i = s.indexOf(pat);
    if (i < 0) return;
    i += pat.length();
    uint32_t v = 0;
    bool any = false;
    while (i < (int)s.length() && s[i] >= '0' && s[i] <= '9') {
      v = v * 10 + (s[i++] - '0');
      any = true;
    }
    if (any) out = (v > 65535) ? 65535 : (uint16_t)v;
  };

  grab("ssid", _cfg.ssid, SSID_MAX);
  grab("pass", _cfg.pass, PASS_MAX);
  grab("ap_pass", _cfg.ap_pass, PASS_MAX);
  grab("user", _cfg.user, USER_MAX);
  grab("console_pass", _cfg.console_pass, PASS_MAX);

  grab("mqtt_host", _cfg.mqtt_host, MQTT_HOST_MAX);
  grab("mqtt_user", _cfg.mqtt_user, MQTT_USER_MAX);
  grab("mqtt_pass", _cfg.mqtt_pass, PASS_MAX);
  grab("mqtt_prefix", _cfg.mqtt_prefix, MQTT_PREFIX_MAX);
  if (_cfg.mqtt_prefix[0] == 0) strcpy(_cfg.mqtt_prefix, "meshcore");
  num("mqtt_port", _cfg.mqtt_port);
  if (_cfg.mqtt_port == 0) _cfg.mqtt_port = 1883;
  num("mqtt_enabled", _cfg.mqtt_enabled);
  num("mqtt_rx", _cfg.mqtt_rx);

  num("pwr_mode", _cfg.pwr_mode);
  num("pwr_window", _cfg.pwr_window);
  num("wifi_sleep", _cfg.wifi_sleep);
  num("tx_power", _cfg.tx_power);
  num("bat_full", _cfg.bat_full);
  num("bat_high", _cfg.bat_high);
  num("bat_norm", _cfg.bat_norm);
  num("bat_crit", _cfg.bat_crit);
  num("bat_hyst", _cfg.bat_hyst);
  num("bat_live", _cfg.bat_live);
  num("full_hold", _cfg.full_hold);
  num("iv_full", _cfg.iv_full);
  num("iv_high", _cfg.iv_high);
  num("iv_norm", _cfg.iv_norm);
  num("iv_low", _cfg.iv_low);
  num("iv_crit", _cfg.iv_crit);
  num("night_from", _cfg.night_from);
  num("night_to", _cfg.night_to);
  num("night_factor", _cfg.night_factor);

  if (_cfg.pwr_window < 30) _cfg.pwr_window = 30;   // shorter is not worth waking for
  if (_cfg.night_factor == 0) _cfg.night_factor = 1;
}

static void saveConfig() {
  if (!_fs) return;
  File f = _fs->open(MSNET_CFG_FILE, "w");
  if (!f) return;
  f.printf("{\"ssid\":\"%s\",\"pass\":\"%s\",\"ap_pass\":\"%s\","
           "\"user\":\"%s\",\"console_pass\":\"%s\","
           "\"mqtt_host\":\"%s\",\"mqtt_port\":%u,\"mqtt_user\":\"%s\","
           "\"mqtt_pass\":\"%s\",\"mqtt_prefix\":\"%s\","
           "\"mqtt_enabled\":%u,\"mqtt_rx\":%u,",
           _cfg.ssid, _cfg.pass, _cfg.ap_pass, _cfg.user, _cfg.console_pass,
           _cfg.mqtt_host, _cfg.mqtt_port, _cfg.mqtt_user, _cfg.mqtt_pass,
           _cfg.mqtt_prefix, _cfg.mqtt_enabled, _cfg.mqtt_rx);
  f.printf("\"pwr_mode\":%u,\"pwr_window\":%u,\"wifi_sleep\":%u,\"tx_power\":%u,"
           "\"bat_full\":%u,\"bat_high\":%u,\"bat_norm\":%u,\"bat_crit\":%u,"
           "\"bat_hyst\":%u,\"bat_live\":%u,\"full_hold\":%u,"
           "\"iv_full\":%u,\"iv_high\":%u,\"iv_norm\":%u,\"iv_low\":%u,\"iv_crit\":%u,"
           "\"night_from\":%u,\"night_to\":%u,\"night_factor\":%u}",
           _cfg.pwr_mode, _cfg.pwr_window, _cfg.wifi_sleep, _cfg.tx_power,
           _cfg.bat_full, _cfg.bat_high, _cfg.bat_norm, _cfg.bat_crit,
           _cfg.bat_hyst, _cfg.bat_live, _cfg.full_hold,
           _cfg.iv_full, _cfg.iv_high, _cfg.iv_norm, _cfg.iv_low, _cfg.iv_crit,
           _cfg.night_from, _cfg.night_to, _cfg.night_factor);
  f.close();
}

/* Boot counter: every start increments it, and only STABLE_UPTIME_MS of
 * running resets it. Three starts without ever becoming stable means something
 * is structurally wrong -- then we leave out everything that is not needed. */
static void checkSafeMode() {
  if (!_fs) return;
  uint8_t count = 0;
  File f = _fs->open(MSNET_BOOT_FILE, "r");
  if (f) { count = f.read(); f.close(); }
  if (count > 200) count = 0;          // invalid, start over

  _safe_mode = (count >= SAFE_MODE_BOOTS);
  _disabled = (count >= DISABLE_BOOTS);

  f = _fs->open(MSNET_BOOT_FILE, "w");
  if (f) { f.write((uint8_t)(count + 1)); f.close(); }
}

static void clearBootCount() {
  if (!_fs || _boot_cleared) return;
  File f = _fs->open(MSNET_BOOT_FILE, "w");
  if (f) { f.write((uint8_t)0); f.close(); }
  _boot_cleared = true;
}

// ---------------------------------------------------------------------- wifi

static void startAP() {
  WiFi.mode(WIFI_AP_STA);              // keep the AP up while we retry the STA
  WiFi.softAP(_ap_ssid, _cfg.ap_pass);
  _state = WIFI_FALLBACK_AP;
  _state_since = millis();
  _last_retry = millis();
  _asleep = false;
  Serial.printf("MeshStatsNet: eigen netwerk '%s' actief op %s\n",
                _ap_ssid, WiFi.softAPIP().toString().c_str());
}

static void startSTA() {
  if (_cfg.ssid[0] == 0) { startAP(); return; }
  if (_state != WIFI_FALLBACK_AP) WiFi.mode(WIFI_STA);
  WiFi.begin(_cfg.ssid, _cfg.pass);
  _asleep = false;
  if (_state != WIFI_FALLBACK_AP) {
    _state = WIFI_TRYING;
    _state_since = millis();
  }
  Serial.printf("MeshStatsNet: verbinden met '%s'...\n", _cfg.ssid);
}

/* Applied every time we associate, because a reconnect resets both settings.
 * Modem-sleep is the cheap win here: it lets the radio idle between beacons
 * without giving up reachability. Lowering TX power helps too, but only when
 * the AP is close -- hence off by default. */
static void applyRadioTuning() {
  WiFi.setSleep(_cfg.wifi_sleep != 0);
  if (_cfg.tx_power >= 2 && _cfg.tx_power <= 20) {
    WiFi.setTxPower((wifi_power_t)(_cfg.tx_power * 4));   // API takes quarter dBm
  }
}

// Machine-readable for the admin page; the page owns the wording.
static const char *wifiStateCode() {
  if (_asleep) return "off";
  if (_state == WIFI_OK) return "ok";
  if (_state == WIFI_FALLBACK_AP) return "ap";
  return "try";
}

// Dutch, for the CLI.
static const char *stateNameNl() {
  if (_asleep) return "uit (zuinig)";
  if (_state == WIFI_OK) return "verbonden";
  if (_state == WIFI_FALLBACK_AP) return "eigen netwerk (WiFi onbereikbaar)";
  return "verbinden...";
}

// --------------------------------------------------------- power management

/* Battery percentage over BATT_EMPTY_MV..BATT_FULL_MV. A board that reports no
 * voltage at all is treated as 'unknown', and unknown is treated as mains
 * power further on: a node that cannot measure its cell should not be
 * throttled by a guess. */
int meshstats_batt_percent(uint16_t mv) {
  if (mv < 2000) return -1;                      // no usable reading
  if (mv <= BATT_EMPTY_MV) return 0;
  if (mv >= BATT_FULL_MV) return 100;
  return (int)(((uint32_t)(mv - BATT_EMPTY_MV) * 100) / (BATT_FULL_MV - BATT_EMPTY_MV));
}

static uint8_t battPercent(bool *known) {
  _batt_mv = board.getBattMilliVolts();
  int pct = meshstats_batt_percent(_batt_mv);
  *known = (pct >= 0);
  return (pct < 0) ? 0 : (uint8_t)pct;
}

// Lowest percentage that still belongs to this level.
static uint16_t levelEntry(uint8_t lv) {
  switch (lv) {
    case LV_FULL:   return _cfg.bat_full;
    case LV_HIGH:   return _cfg.bat_high;
    case LV_NORMAL: return _cfg.bat_norm;
    case LV_LOW:    return _cfg.bat_crit;
    default:        return 0;
  }
}

static uint8_t rawLevel(uint8_t pct) {
  if (pct >= _cfg.bat_full) return LV_FULL;
  if (pct >= _cfg.bat_high) return LV_HIGH;
  if (pct >= _cfg.bat_norm) return LV_NORMAL;
  if (pct >= _cfg.bat_crit) return LV_LOW;
  return LV_CRITICAL;
}

/* Levels move with hysteresis: a level is only left once the percentage is
 * bat_hyst past the boundary. A cell hovering exactly on a threshold is
 * precisely the situation where the panel is marginal and stable behaviour
 * matters most -- flapping between two intervals there would cost energy for
 * nothing.
 *
 * The top level has an extra condition: the cell must have read full for
 * full_hold minutes. 'Full' should mean surplus, not the first sunbeam of the
 * morning. */
static void updatePowerLevel() {
  if (_batt_read_at != 0 && millis() - _batt_read_at < BATT_POLL_MS) return;
  _batt_read_at = millis();

  bool known;
  uint8_t pct = battPercent(&known);
  _batt_pct = pct;
  _batt_known = known;

  if (!known) { _level = LV_FULL; return; }

  uint8_t want = rawLevel(pct);
  if (want == LV_FULL) {
    if (_full_since == 0) _full_since = millis();
    if (millis() - _full_since < (unsigned long)_cfg.full_hold * 60000UL) want = LV_HIGH;
  } else {
    _full_since = 0;
  }

  if (want < _level) {              // better than the current level
    if (pct < levelEntry(want) + _cfg.bat_hyst) want = _level;
  } else if (want > _level) {       // worse
    if (pct + _cfg.bat_hyst >= levelEntry(_level)) want = _level;
  }
  _level = want;
}

/* Night is a bonus, never a dependency: an unset or implausible clock simply
 * means 'not night', so a wrong RTC can never make this node quieter than the
 * battery rules on their own would. */
static bool isNight() {
  uint8_t h;
  if (!_mesh || !_mesh->getClockHour(&h)) return false;
  if (_cfg.night_from == _cfg.night_to) return false;
  if (_cfg.night_from < _cfg.night_to) return h >= _cfg.night_from && h < _cfg.night_to;
  return h >= _cfg.night_from || h < _cfg.night_to;    // window wraps past midnight
}

static uint32_t currentIntervalSecs() {
  uint32_t iv;
  switch (_level) {
    case LV_FULL:   iv = _cfg.iv_full; break;
    case LV_HIGH:   iv = _cfg.iv_high; break;
    case LV_NORMAL: iv = _cfg.iv_norm; break;
    case LV_LOW:    iv = _cfg.iv_low;  break;
    default:        iv = _cfg.iv_crit; break;
  }
  if (isNight()) iv *= _cfg.night_factor;
  if (iv < 30) iv = 30;
  return iv;
}

static bool isForced() {
  if (_force_until == 0) return false;
  if (passed(_force_until)) { _force_until = 0; return false; }
  return true;
}

static void wifiSleep() {
  if (_mqtt.connected()) _mqtt.disconnect();
  if (_client) _client.stop();          // no point holding a console we cannot reach
  WiFi.disconnect(true);
  WiFi.mode(WIFI_OFF);
  _asleep = true;
  _state = WIFI_TRYING;                 // waking starts the state machine over
  _wake_at = millis() + currentIntervalSecs() * 1000UL;
  Serial.printf("MeshStatsNet: wifi uit, volgende ronde over %u s\n",
                (unsigned)currentIntervalSecs());
}

static void wifiWake() {
  _awake_until = millis() + (unsigned long)_cfg.pwr_window * 1000UL;
  _wake_at = 0;
  _published_this_wake = false;
  startSTA();
}

/* Radio silence saves far more than a slower publish interval does, so in
 * power-save mode WiFi is off by default and only comes up long enough to ship
 * what is queued and to stay reachable for pwr_window seconds.
 *
 * Two rules keep this from locking anyone out:
 *   - safe mode never sleeps; that mode exists to be reachable
 *   - a forced window ('wifi on <min>') outranks everything, at any battery
 *     level, and also re-enables the AP fallback so a broken WiFi config still
 *     ends with a network you can reach
 */
static void powerLoop() {
  updatePowerLevel();

  if (_safe_mode || _cfg.pwr_mode == PWR_ALWAYS || isForced()) {
    if (_asleep) wifiWake();
    return;
  }

  if (_asleep) {
    if (passed(_wake_at)) wifiWake();
    return;
  }

  if (!passed(_awake_until)) return;

  /* Waking up and then sleeping again without having said anything would waste
   * the whole round, so a connected broker gets a little extra time to take
   * the backlog and this round's stats. Never more than a minute, though: an
   * unreachable broker must not keep the radio on all day. */
  bool unfinished = (_rx_head != _rx_tail) ||
                    (_cfg.mqtt_enabled && _cfg.mqtt_host[0] && !_published_this_wake);
  if (unfinished && _mqtt.connected() && !passed(_awake_until + 60000UL)) return;

  wifiSleep();
}

/* Whether received packets go out the moment they arrive, rather than a few
 * per pass. Above bat_live there is charge to spare and the point of this node
 * is watching packets move across the map live, so nothing should sit in a
 * queue waiting its turn.
 *
 * Returns a code rather than a bool because the honest answer has three parts,
 * and the page must not promise a live view it cannot deliver:
 *   "on"   forwarding immediately
 *   "batt" battery below the threshold, saving comes first
 *   "save" power-save mode -- WiFi is off between rounds, so 'immediate' is
 *          impossible by construction, whatever the battery says
 */
static const char *liveCode() {
  if (_cfg.pwr_mode != PWR_ALWAYS) return "save";
  if (_batt_known && _batt_pct < _cfg.bat_live) return "batt";
  return "on";
}

static bool liveForwarding() {
  return strcmp(liveCode(), "on") == 0;
}

static const char *powerStateCode() {
  if (isForced()) return "forced";
  if (_cfg.pwr_mode == PWR_ALWAYS || _safe_mode) return "always";
  return _asleep ? "asleep" : "awake";
}

static uint32_t powerSecsLeft() {
  if (isForced()) return secsLeft(_force_until);
  if (_cfg.pwr_mode == PWR_ALWAYS || _safe_mode) return 0;
  return _asleep ? secsLeft(_wake_at) : secsLeft(_awake_until);
}

// Dutch one-liner for the CLI; the admin page builds its own from the codes.
static void powerSummaryNl(char *out, size_t max) {
  uint32_t iv = currentIntervalSecs();
  const char *night = isNight() ? ", nacht" : "";
  const char *st = powerStateCode();

  if (strcmp(st, "forced") == 0) {
    snprintf(out, max, "opgevorderd, nog %u min (elke %u s%s)",
             (unsigned)(powerSecsLeft() / 60 + 1), (unsigned)iv, night);
  } else if (strcmp(st, "always") == 0) {
    snprintf(out, max, "altijd bereikbaar, elke %u s%s", (unsigned)iv, night);
  } else if (strcmp(st, "asleep") == 0) {
    snprintf(out, max, "zuinig, wifi terug over %u s", (unsigned)powerSecsLeft());
  } else {
    snprintf(out, max, "zuinig, nog %u s bereikbaar (elke %u s%s)",
             (unsigned)powerSecsLeft(), (unsigned)iv, night);
  }
}

// ---------------------------------------------------------------------- mqtt

static void mqttTopic(const char *leaf, char *out, size_t max) {
  snprintf(out, max, "%s/%s/%s", _cfg.mqtt_prefix,
           _node_hex[0] ? _node_hex : "node", leaf);
}

static bool mqttEnsure() {
  if (_mqtt.connected()) return true;
  if (!_cfg.mqtt_enabled || _cfg.mqtt_host[0] == 0) return false;
  if (WiFi.status() != WL_CONNECTED) return false;

  // Backing off matters here: a broker that does not answer costs a full
  // socket timeout per attempt, and that time comes straight out of the mesh.
  if (_mqtt_last_try != 0 && millis() - _mqtt_last_try < MQTT_RETRY_MS) return false;
  _mqtt_last_try = millis();

  char client_id[32];
  snprintf(client_id, sizeof(client_id), "meshcore-%s",
           _node_hex[0] ? _node_hex : "node");

  bool ok = _cfg.mqtt_user[0]
    ? _mqtt.connect(client_id, _cfg.mqtt_user, _cfg.mqtt_pass)
    : _mqtt.connect(client_id);

  if (ok) {
    _mqtt_err = "";
  } else {
    _fail_count++;
    _mqtt_err = "conn";
    _mqtt_err_rc = _mqtt.state();
  }
  return ok;
}

static bool mqttPublishStats() {
  if (!_mesh || !mqttEnsure()) return false;

  static char body[MQTT_PUB_MAX];
  size_t n = _mesh->fillStatsJson(body, sizeof(body));
  if (n == 0) return false;

  char topic[96];
  mqttTopic("stats", topic, sizeof(topic));

  if (!_mqtt.publish(topic, (const uint8_t *)body, n, false)) {
    _fail_count++;
    _mqtt_err = "stats";
    return false;
  }
  _stats_count++;
  _published_this_wake = true;
  _mqtt_err = "";
  return true;
}

static void mqttDrainRx() {
  if (_rx_head == _rx_tail) return;

  /* While asleep the queue keeps filling on purpose -- that is the point of
   * buffering -- but once we are up and the broker still will not take them,
   * old packets have no value left. Dropping beats sending a pile of stale
   * traffic minutes later. */
  if (!mqttEnsure()) {
    if (_asleep || WiFi.status() != WL_CONNECTED) return;
    while (_rx_tail != _rx_head) {
      _rx_tail = (uint8_t)((_rx_tail + 1) % MQTT_RX_QUEUE);
      _drop_count++;
    }
    return;
  }

  char topic[96];
  mqttTopic("rx", topic, sizeof(topic));

  /* With charge to spare, empty the whole buffer in one pass so nothing waits
   * a round. The cap is a bound on one pass, not a brake: at these radio
   * settings a full-size packet occupies the air for roughly half a second, so
   * even the lower figure drains far faster than LoRa can deliver. */
  int cap = liveForwarding() ? MQTT_RX_QUEUE : MQTT_DRAIN_MAX;

  for (int guard = 0; guard < cap && _rx_tail != _rx_head; guard++) {
    RxItem &it = _rx_queue[_rx_tail];

    static char body[MQTT_RX_MAX_LEN * 2 + 96];
    int n = snprintf(body, sizeof(body),
      "{\"t\":%u,\"snr\":%.2f,\"rssi\":%d,\"len\":%u,\"raw\":\"",
      (unsigned)it.ms, it.snr4 / 4.0f, (int)it.rssi, (unsigned)it.len);

    for (uint8_t i = 0; i < it.len; i++) {
      body[n++] = HEXCHARS[it.data[i] >> 4];
      body[n++] = HEXCHARS[it.data[i] & 0x0F];
    }
    body[n++] = '"';
    body[n++] = '}';

    if (!_mqtt.publish(topic, (const uint8_t *)body, n, false)) {
      _fail_count++;
      _mqtt_err = "pkt";
      return;              // leave it queued; try again next pass
    }
    _rx_count++;
    _rx_tail = (uint8_t)((_rx_tail + 1) % MQTT_RX_QUEUE);
  }
}

static void mqttLoop() {
  if (!_cfg.mqtt_enabled || _cfg.mqtt_host[0] == 0) return;
  if (_asleep) return;

  if (_mqtt.connected()) _mqtt.loop();
  mqttDrainRx();

  if (millis() - _mqtt_last_push < currentIntervalSecs() * 1000UL) return;
  _mqtt_last_push = millis();
  mqttPublishStats();
}

void meshstats_on_raw_packet(float snr, float rssi, const uint8_t raw[], int len) {
  if (!_started || _disabled || _safe_mode) return;
  if (!_cfg.mqtt_enabled || !_cfg.mqtt_rx) return;
  if (len <= 0 || len > MQTT_RX_MAX_LEN) return;

  uint8_t next = (uint8_t)((_rx_head + 1) % MQTT_RX_QUEUE);
  if (next == _rx_tail) {     // full: rather lose a packet than hold up reception
    _drop_count++;
    return;
  }

  RxItem &it = _rx_queue[_rx_head];
  it.ms = millis();
  it.snr4 = (int16_t)(snr * 4);
  it.rssi = (int16_t)rssi;
  it.len = (uint8_t)len;
  memcpy(it.data, raw, len);
  _rx_head = next;
}

// -------------------------------------------------------------- advert cache

/* Who is out there, kept across restarts. neighbours[] in MyMesh lives in RAM
 * and starts empty after every flash, so without this the monitor list and the
 * heard list show bare hex keys until each node happens to advertise again --
 * which at an advert interval of hours is most of a day.
 *
 * Written lazily, following the same reasoning as the repeater's own ACL
 * (dirty_contacts_expiry in MyMesh): a busy mesh delivers adverts in bursts,
 * and writing each one would grind through SPIFFS erase cycles for data that
 * changes by a name every few hours. So changes accumulate in RAM and go to
 * disk at most once every ADV_WRITE_DELAY, and only when something actually
 * differs from what is already stored. */
#define MSADV_FILE        "/adverts.dat"
#define MSADV_MAGIC       0x41565331UL    // "AVS1"
#define ADV_CACHE_MAX     48
#define ADV_NAME_MAX      24
#define ADV_WRITE_DELAY   120000UL        // 2 minutes of quiet before writing

struct AdvEntry {
  uint8_t  key[PUB_KEY_SIZE];
  char     name[ADV_NAME_MAX];
  uint8_t  type;
  uint32_t heard;                 // epoch seconds, 0 when the clock was unset
  int32_t  lat, lon;              // 1e-6 degrees; 0,0 means 'not advertised'
};

static AdvEntry _adv[ADV_CACHE_MAX];
static int _adv_count = 0;
static unsigned long _adv_dirty_at = 0;   // 0 = nothing to write

static int advFind(const uint8_t *key, int prefix_len) {
  if (prefix_len <= 0 || prefix_len > PUB_KEY_SIZE) return -1;
  for (int i = 0; i < _adv_count; i++) {
    if (memcmp(_adv[i].key, key, prefix_len) == 0) return i;
  }
  return -1;
}

const char *meshstats_advert_name(const uint8_t *pub_key, int prefix_len) {
  int i = advFind(pub_key, prefix_len);
  return (i >= 0 && _adv[i].name[0]) ? _adv[i].name : NULL;
}

static void advLoad() {
  _adv_count = 0;
  if (!_fs) return;
  File f = _fs->open(MSADV_FILE, "r");
  if (!f) return;

  uint32_t magic = 0;
  uint16_t entry_size = 0, count = 0;
  /* The entry size is part of the header because these are raw structs: add a
   * field one day and an old file would read back as garbage names and
   * nonsense timestamps. A mismatch just starts the cache empty. */
  if (f.read((uint8_t *)&magic, 4) == 4 && magic == MSADV_MAGIC &&
      f.read((uint8_t *)&entry_size, 2) == 2 && entry_size == (uint16_t)sizeof(AdvEntry) &&
      f.read((uint8_t *)&count, 2) == 2) {
    if (count > ADV_CACHE_MAX) count = ADV_CACHE_MAX;
    for (uint16_t i = 0; i < count; i++) {
      if (f.read((uint8_t *)&_adv[_adv_count], sizeof(AdvEntry)) != (int)sizeof(AdvEntry)) break;
      _adv[_adv_count].name[ADV_NAME_MAX - 1] = 0;   // never trust a stored string
      _adv_count++;
    }
  }
  f.close();
  Serial.printf("MeshStatsNet: %d adverts geladen\n", _adv_count);
}

static void advSave() {
  if (!_fs) return;
  File f = _fs->open(MSADV_FILE, "w");
  if (!f) return;
  uint32_t magic = MSADV_MAGIC;
  uint16_t entry_size = (uint16_t)sizeof(AdvEntry);
  uint16_t count = (uint16_t)_adv_count;
  f.write((const uint8_t *)&magic, 4);
  f.write((const uint8_t *)&entry_size, 2);
  f.write((const uint8_t *)&count, 2);
  for (int i = 0; i < _adv_count; i++) {
    f.write((const uint8_t *)&_adv[i], sizeof(AdvEntry));
  }
  f.close();
  _adv_dirty_at = 0;
}

void meshstats_on_advert(const uint8_t *pub_key, const char *name, uint8_t type,
                         bool has_latlon, int32_t lat, int32_t lon) {
  if (!_started || _disabled) return;

  uint32_t now = 0;
  if (_mesh) {
    uint8_t h;
    // Only trust the clock if it looks set; a bogus 'heard' would evict wrongly.
    if (_mesh->getClockHour(&h)) now = _mesh->getRTCClock()->getCurrentTime();
  }

  int i = advFind(pub_key, PUB_KEY_SIZE);
  if (i < 0) {
    if (_adv_count < ADV_CACHE_MAX) {
      i = _adv_count++;
    } else {
      /* Full: drop whoever we heard longest ago. This runs on a node with a
       * few hundred kB of flash, not a server. */
      i = 0;
      for (int j = 1; j < _adv_count; j++) {
        if (_adv[j].heard < _adv[i].heard) i = j;
      }
    }
    memset(&_adv[i], 0, sizeof(AdvEntry));
    memcpy(_adv[i].key, pub_key, PUB_KEY_SIZE);
    _adv_dirty_at = millis() + ADV_WRITE_DELAY;
  }

  AdvEntry &e = _adv[i];
  e.heard = now;
  if (e.type != type) { e.type = type; _adv_dirty_at = millis() + ADV_WRITE_DELAY; }

  // A name straight off the air always wins over a stored one.
  if (name && *name && strncmp(e.name, name, ADV_NAME_MAX - 1) != 0) {
    strncpy(e.name, name, ADV_NAME_MAX - 1);
    e.name[ADV_NAME_MAX - 1] = 0;
    _adv_dirty_at = millis() + ADV_WRITE_DELAY;
  }
  if (has_latlon && (e.lat != lat || e.lon != lon)) {
    e.lat = lat;
    e.lon = lon;
    _adv_dirty_at = millis() + ADV_WRITE_DELAY;
  }
  /* Deliberately no write here, and 'heard' alone never schedules one: the
   * timestamp changes on every advert, and chasing it would defeat the whole
   * point of writing lazily. It rides along with the next real change. */
}

// ------------------------------------------------------ monitored repeaters

/* Polling other repeaters over the mesh. Per peer the sequence is the one a
 * chat client performs: an ANON_REQ carrying the password (or an empty one,
 * which makes the far side skip the password check and consult its access list
 * instead), then a REQ of type GET_STATUS once that is accepted, then a
 * RESPONSE carrying RepeaterStats.
 *
 * It is a state machine driven from msnet_loop(), one peer at a time. Not
 * because that is simpler, but because this node is a repeater: a burst of
 * logins from the very node meant to relay other people's traffic is
 * antisocial, and every flooded login costs the whole mesh airtime.
 *
 * A refused login cannot be told from an unreachable one -- the far side
 * answers a rejected login with silence. Hence LOGIN_NOANSWER rather than a
 * pretence of knowing which of the two happened. */

#define MSMON_CFG_FILE   "/msmon.json"
#define MON_KEY_HEX_MAX  65      // 64 hex chars + NUL
#define MON_NAME_MAX     24
#define MON_PASS_MAX     16      // the protocol truncates at 15 characters
/* Minimum pasted key: 6 bytes. That is what this firmware itself uses to name
 * a node (MQTT topics, the id on the page), and short enough to copy off a
 * screenshot. Below it, collisions stop being theoretical once a few dozen
 * nodes are on the air, and monitoring the wrong repeater is worse than being
 * asked for two more characters. */
#define MON_MIN_HEX      12
/* A first login is flooded and its answer comes back over an unknown number of
 * hops; 20 s turned out to be tight for that. */
#define MON_STEP_MS      30000UL // how long to wait for one login/status answer
#define MON_GAP_MS        3000UL // breathing space between peers
#define MON_HEARD_MAX      40    // heard rows handed to the page

enum MonLogin { LOGIN_NONE = 0, LOGIN_OK = 1, LOGIN_NOANSWER = 2 };

struct MonEntry {
  char key[MON_KEY_HEX_MAX];   // longest key seen for this node, lowercase hex
  char name[MON_NAME_MAX];
  char pass[MON_PASS_MAX];     // empty is a valid choice: try their access list
  bool enabled;
  // runtime only, never persisted
  int8_t   mesh_idx;           // index in MyMesh's table, -1 = not resolvable yet
  uint8_t  login_res;
  bool     logged_in;
  /* Three counters, not two. 'oks' used to be incremented only on a successful
   * publish, which meant a reading that was fetched but never delivered looked
   * exactly like one that was never fetched -- and finding out took a sniffer
   * on the broker. Now: polls = attempts, oks = answers received and parsed,
   * pubs = actually published. Any gap between them is visible on the page. */
  uint32_t polls, oks, pubs;
  // Per request type, so a round that half worked says which half.
  uint32_t ok_st, ok_tl, ok_nb;
  uint8_t  fails;              // consecutive rounds that produced nothing
  unsigned long last_ok;
};

/* Three requests each waiting out a 30 s timeout is 90 s spent on a peer that
 * is simply not there. With a handful of dead entries a round would run
 * practically without pause, and this node's day job is relaying other
 * people's traffic. So after this many barren rounds an entry is only retried
 * every fourth one. Any answer at all clears it. */
#define MON_BACKOFF_AFTER   3
#define MON_BACKOFF_EVERY   4

static MonEntry _mon[MAX_MONITORS];
static int _mon_count = 0;
static uint16_t _mon_interval = 900;

enum MonState { MST_IDLE, MST_LOGIN_WAIT, MST_REQ_WAIT, MST_TELEM_WAIT, MST_NBR_WAIT, MST_GAP };
static MonState _mon_state = MST_IDLE;
static int _mon_cur = -1;
static uint8_t _mon_retry = 0;      // one flood retry per step, then give up

/* Results of the round in progress, gathered before anything is published.
 * Each of the three requests can fail on its own, and a partial reading beats
 * none -- so we collect what comes back and publish once, at the end, with
 * whatever we have. */
static RepeaterStats _mon_st;
static bool _mon_have_st = false;
static int  _mon_st_len = 0;        // bytes of RepeaterStats that actually arrived
static uint8_t _mon_nbr[176];       // raw neighbours reply, decoded when publishing
static int  _mon_nbr_len = 0;
static bool _mon_have_nbr = false;

/* Telemetry arrives as CayenneLPP, so which channels a node reports on is not
 * known in advance. Values are kept under the channel the source used rather
 * than renamed to something we assume they mean. */
#define MON_TELEM_MAX 6
struct MonTelem { uint8_t channel; char kind; float value; };   // kind: 't' or 'v'
static MonTelem _mon_tl[MON_TELEM_MAX];
static int _mon_tl_n = 0;
static unsigned long _mon_deadline = 0;
static unsigned long _mon_next_round = 0;
static uint32_t _mon_round = 0;

/* A short trace of the poll sequence, because the counters alone cannot tell
 * two very different failures apart: a status request that was never sent
 * (packet pool empty) and one that was sent but never answered both leave
 * polls=1, oks=0, lr=1. Readable from the admin page and over the mesh CLI, so
 * diagnosing a node on a roof does not need a serial cable. */
#define MON_TRACE_LINES  12
#define MON_TRACE_LEN    64
static char _mon_trace[MON_TRACE_LINES][MON_TRACE_LEN];
static uint32_t _mon_trace_n = 0;

static void monTrace(const char *fmt, ...) {
  char *dest = _mon_trace[_mon_trace_n % MON_TRACE_LINES];
  int p = snprintf(dest, MON_TRACE_LEN, "%lus ", (unsigned long)(millis() / 1000));
  va_list ap;
  va_start(ap, fmt);
  vsnprintf(dest + p, MON_TRACE_LEN - p, fmt, ap);
  va_end(ap);
  _mon_trace_n++;
  Serial.print("MON: ");
  Serial.println(dest);
}

// One staging slot is enough: only ever one poll is outstanding.
static volatile bool _mon_got_reply = false;
static uint8_t _mon_reply[MAX_PACKET_PAYLOAD];
static int _mon_reply_len = 0;
static int _mon_reply_idx = -1;

/* Lowercases and drops the separators people paste along, but rejects anything
 * that is not hex. Strict on purpose: a silently mangled key becomes a monitor
 * entry that can never work, with nothing on screen to say why. */
static bool normaliseKey(char *key) {
  char out[MON_KEY_HEX_MAX];
  int n = 0;
  for (const char *p = key; *p; p++) {
    char c = *p;
    if (c == ' ' || c == ':' || c == '-' || c == '\t') continue;
    if (c >= 'A' && c <= 'F') c = (char)(c - 'A' + 'a');
    if (!((c >= '0' && c <= '9') || (c >= 'a' && c <= 'f'))) return false;
    if (n >= MON_KEY_HEX_MAX - 1) return false;
    out[n++] = c;
  }
  out[n] = 0;
  if (n < MON_MIN_HEX || n > 64 || (n & 1)) return false;
  strcpy(key, out);
  return true;
}

/* Two keys mean the same node when the shorter is a prefix of the longer.
 * Different sources hand out different lengths for one repeater -- Home
 * Assistant 5 bytes, this firmware 6 -- and treating those as two nodes is
 * exactly how one repeater ends up listed twice with its history split. */
static bool sameNode(const char *a, const char *b) {
  size_t la = strlen(a), lb = strlen(b);
  size_t n = (la < lb) ? la : lb;
  return n > 0 && memcmp(a, b, n) == 0;
}

static int findMonitor(const char *key) {
  for (int i = 0; i < _mon_count; i++) {
    if (sameNode(_mon[i].key, key)) return i;
  }
  return -1;
}

static void saveMonitors() {
  if (!_fs) return;
  File f = _fs->open(MSMON_CFG_FILE, "w");
  if (!f) return;
  f.printf("{\"iv\":%u,\"m\":[", _mon_interval);
  for (int i = 0; i < _mon_count; i++) {
    f.printf("%s{\"k\":\"%s\",\"n\":\"%s\",\"p\":\"%s\",\"e\":%d}",
             i ? "," : "", _mon[i].key, _mon[i].name, _mon[i].pass,
             _mon[i].enabled ? 1 : 0);
  }
  f.print("]}");
  f.close();
}

static void loadMonitors() {
  memset(_mon, 0, sizeof(_mon));
  _mon_count = 0;
  _mon_interval = 900;
  if (!_fs) return;

  File f = _fs->open(MSMON_CFG_FILE, "r");
  if (!f) return;
  String s = f.readString();
  f.close();

  auto strOf = [](const String &src, const char *key, char *out, size_t max) {
    String pat = String("\"") + key + "\":\"";
    int i = src.indexOf(pat);
    if (i < 0) { out[0] = 0; return; }
    i += pat.length();
    int j = src.indexOf('"', i);
    if (j < 0) { out[0] = 0; return; }
    String v = src.substring(i, j);
    strncpy(out, v.c_str(), max - 1);
    out[max - 1] = 0;
  };
  auto intOf = [](const String &src, const char *key, long fallback) -> long {
    String pat = String("\"") + key + "\":";
    int i = src.indexOf(pat);
    if (i < 0) return fallback;
    i += pat.length();
    long v = 0;
    bool any = false;
    while (i < (int)src.length() && src[i] >= '0' && src[i] <= '9') {
      v = v * 10 + (src[i++] - '0');
      any = true;
    }
    return any ? v : fallback;
  };

  long iv = intOf(s, "iv", 900);
  _mon_interval = (iv < 60) ? 60 : (iv > 65535 ? 65535 : (uint16_t)iv);

  int pos = 0;
  while (_mon_count < MAX_MONITORS) {
    int b = s.indexOf("{\"k\":\"", pos);
    if (b < 0) break;
    int e = s.indexOf('}', b);
    if (e < 0) break;
    String item = s.substring(b, e + 1);
    pos = e + 1;

    MonEntry &m = _mon[_mon_count];
    memset(&m, 0, sizeof(m));
    m.mesh_idx = -1;
    strOf(item, "k", m.key, sizeof(m.key));
    strOf(item, "n", m.name, sizeof(m.name));
    strOf(item, "p", m.pass, sizeof(m.pass));
    m.enabled = intOf(item, "e", 1) != 0;
    if (normaliseKey(m.key) && findMonitor(m.key) < 0) _mon_count++;
  }
}

/* Rebuilds MyMesh's radio-level table. Only entries whose full 32-byte key we
 * know can go in: the shared secret is ECDH over the whole key, so a pasted
 * prefix stays inert until we hear that repeater advertise itself. */
static void syncMonitorsToMesh() {
  if (!_mesh) return;
  _mesh->clearMonitors();
  for (int i = 0; i < _mon_count; i++) {
    _mon[i].mesh_idx = -1;
    _mon[i].logged_in = false;
    if (strlen(_mon[i].key) < 64) continue;

    uint8_t key[PUB_KEY_SIZE];
    if (!mesh::Utils::fromHex(key, PUB_KEY_SIZE, _mon[i].key)) continue;
    int idx = _mesh->addMonitor(key);
    if (idx >= 0) _mon[i].mesh_idx = (int8_t)idx;
  }
}

/* Matches entries against the heard list: upgrades a prefix to the full key,
 * and lets the advert name win over anything typed by hand. Cheap enough (a
 * handful of memcmp) to run once per round. */
static bool resolveMonitors() {
  if (!_mesh) return false;
  bool changed = false;

  for (int i = 0; i < _mon_count; i++) {
    size_t hex_len = strlen(_mon[i].key);
    int plen = (int)(hex_len / 2);
    if (plen > PUB_KEY_SIZE) plen = PUB_KEY_SIZE;

    uint8_t prefix[PUB_KEY_SIZE];
    if (!mesh::Utils::fromHex(prefix, plen, _mon[i].key)) continue;

    uint8_t full[PUB_KEY_SIZE];
    char nm[MON_NAME_MAX];
    nm[0] = 0;

    if (_mesh->findNeighbourByPrefix(prefix, plen, full, nm, sizeof(nm))) {
      if (hex_len < 64) {
        mesh::Utils::toHex(_mon[i].key, full, PUB_KEY_SIZE);
        hex_len = 64;
        changed = true;
      }
    }
    /* Fall back to the stored adverts, which is what makes a name reappear
     * after a restart instead of waiting hours for the next advert. */
    if (nm[0] == 0) {
      const char *cached = meshstats_advert_name(prefix, plen);
      if (cached) {
        strncpy(nm, cached, sizeof(nm) - 1);
        nm[sizeof(nm) - 1] = 0;
      }
    }
    if (nm[0] && strcmp(_mon[i].name, nm) != 0) {
      strncpy(_mon[i].name, nm, sizeof(_mon[i].name) - 1);
      _mon[i].name[sizeof(_mon[i].name) - 1] = 0;
      changed = true;
    }
  }
  return changed;
}

void meshstats_on_monitor_response(int mon_idx, const uint8_t *data, int len) {
  if (!_started || _disabled || _safe_mode) return;
  if (len <= 0 || len > (int)sizeof(_mon_reply)) return;
  if (_mon_got_reply) return;            // previous one not consumed yet

  memcpy(_mon_reply, data, len);
  _mon_reply_len = len;
  _mon_reply_idx = mon_idx;
  _mon_got_reply = true;
}

/* Publishes one polled repeater on OUR OWN stats topic, with the subject's
 * prefix in the payload.
 *
 * This used to go to a '<prefix>/<node>/mon' topic of my own invention, and
 * that was the bug: the receiving side subscribes to exactly two patterns,
 * '<prefix>/+/stats' and '<prefix>/+/rx'. Everything else reaches the broker
 * and is discarded there unread, which is why two successful readings produced
 * a publish that returned success and a site that never heard of them.
 *
 * The relay case is deliberately supported at the far end: the topic names who
 * published, repeater.pubkey_prefix names who it is about, and the difference
 * is recorded as source_prefix. So the right topic is the ordinary one. */
// Clears the collected results; called when a peer's turn begins.
static void monResetResults() {
  memset(&_mon_st, 0, sizeof(_mon_st));
  _mon_have_st = false;
  _mon_st_len = 0;
  _mon_have_nbr = false;
  _mon_nbr_len = 0;
  _mon_tl_n = 0;
}

/* Decodes a CayenneLPP telemetry reply. Only temperature and voltage are kept:
 * those are the two the site has fields for, and skipData() lets us step over
 * everything else without having to understand it. */
static void monDecodeTelemetry(const uint8_t *data, int len) {
  _mon_tl_n = 0;
  if (len <= 4) return;

  LPPReader reader(&data[4], (uint8_t)(len - 4));
  uint8_t channel, type;
  while (_mon_tl_n < MON_TELEM_MAX && reader.readHeader(channel, type)) {
    float v;
    if (type == LPP_TEMPERATURE && reader.readTemperature(v)) {
      _mon_tl[_mon_tl_n].channel = channel;
      _mon_tl[_mon_tl_n].kind = 't';
      _mon_tl[_mon_tl_n].value = v;
      _mon_tl_n++;
    } else if (type == LPP_VOLTAGE && reader.readVoltage(v)) {
      _mon_tl[_mon_tl_n].channel = channel;
      _mon_tl[_mon_tl_n].kind = 'v';
      _mon_tl[_mon_tl_n].value = v;
      _mon_tl_n++;
    } else {
      reader.skipData(type);
    }
  }
}

/* Publishes one polled repeater on OUR OWN stats topic, with the subject's
 * prefix in the payload.
 *
 * This used to go to a '<prefix>/<node>/mon' topic of my own invention, and
 * that was the bug: the receiving side subscribes to exactly two patterns,
 * '<prefix>/+/stats' and '<prefix>/+/rx'. Everything else reaches the broker
 * and is discarded there unread, which is why two successful readings produced
 * a publish that returned success and a site that never heard of them.
 *
 * The relay case is deliberately supported at the far end: the topic names who
 * published, repeater.pubkey_prefix names who it is about, and the difference
 * is recorded as source_prefix. So the right topic is the ordinary one. */
static bool publishMonitorRound(MonEntry &m) {
  if (!_mon_have_st && _mon_tl_n == 0 && !_mon_have_nbr) return false;
  if (!mqttEnsure()) return false;

  char prefix_hex[13];
  memcpy(prefix_hex, m.key, 12);
  prefix_hex[12] = 0;

  static char body[MQTT_PUB_MAX];
  int p = snprintf(body, sizeof(body),
    "{\"repeater\":{\"pubkey_prefix\":\"%s\",\"name\":\"%s\"},\"metrics\":{\"online\":true",
    prefix_hex, m.name);
  if (p <= 0 || p >= (int)sizeof(body)) return false;

  if (_mon_have_st) {
    const RepeaterStats &st = _mon_st;

    /* Two separate reasons a field may be missing, and both must leave it out
     * rather than send a zero:
     *   ST_HAS  -- an older firmware sent a shorter struct, so those bytes
     *              never arrived and our zeroed copy reads 0
     *   physics -- the bytes arrived but the far side never filled them in. A
     *              noise floor or an RSSI in dBm cannot be >= 0; JessaZH
     *              reports noise_floor 0, which means 'my driver does not
     *              measure this', not 'the band is silent'.
     * Counters stay unfiltered: zero packets sent is a fact, not a gap. */
    #define ST_HAS(f) ((int)(offsetof(RepeaterStats, f) + sizeof(st.f)) <= _mon_st_len)

    p += snprintf(body + p, sizeof(body) - p,
      ",\"uptime\":%.5f,\"airtime\":%.1f,\"rx_airtime\":%.1f,"
      "\"nb_recv\":%u,\"nb_sent\":%u,\"sent_flood\":%u,\"sent_direct\":%u,"
      "\"recv_flood\":%u,\"recv_direct\":%u,\"tx_queue_len\":%u",
      st.total_up_time_secs / 86400.0,
      st.total_air_time_secs / 60.0f, st.total_rx_air_time_secs / 60.0f,
      (unsigned)st.n_packets_recv, (unsigned)st.n_packets_sent,
      (unsigned)st.n_sent_flood, (unsigned)st.n_sent_direct,
      (unsigned)st.n_recv_flood, (unsigned)st.n_recv_direct,
      (unsigned)st.curr_tx_queue_len);

    if (ST_HAS(noise_floor) && st.noise_floor < 0) {
      p += snprintf(body + p, sizeof(body) - p, ",\"noise_floor\":%d", (int)st.noise_floor);
    }
    if (ST_HAS(last_rssi) && st.last_rssi < 0) {
      p += snprintf(body + p, sizeof(body) - p, ",\"last_rssi\":%d", (int)st.last_rssi);
    }
    // SNR of 0.0 dB is a real reading; only a node that heard nothing has none.
    if (ST_HAS(last_snr) && st.n_packets_recv > 0) {
      p += snprintf(body + p, sizeof(body) - p, ",\"last_snr\":%.2f", st.last_snr / 4.0f);
    }
    if (ST_HAS(err_events)) {
      p += snprintf(body + p, sizeof(body) - p, ",\"err_events\":%u", (unsigned)st.err_events);
    }
    if (ST_HAS(n_flood_dups)) {
      p += snprintf(body + p, sizeof(body) - p, ",\"direct_dups\":%u,\"flood_dups\":%u",
                    (unsigned)st.n_direct_dups, (unsigned)st.n_flood_dups);
    }
    if (ST_HAS(n_recv_errors)) {
      p += snprintf(body + p, sizeof(body) - p, ",\"recv_errors\":%u",
                    (unsigned)st.n_recv_errors);
    }
    /* battery_percentage is derived rather than asked for: the same cell
     * voltage through the same shared curve, so it costs no extra packet. A
     * board reporting no usable voltage gets neither field. */
    int pct = meshstats_batt_percent(st.batt_milli_volts);
    if (pct >= 0) {
      p += snprintf(body + p, sizeof(body) - p, ",\"bat\":%.3f,\"battery_percentage\":%d",
                    st.batt_milli_volts / 1000.0f, pct);
    }
    #undef ST_HAS
  }

  /* Under the channel the source itself used. On a MeshCore repeater channel 1
   * is its own board, so ch1_temperature there is the MCU die rather than the
   * outside air -- but that is the far side's naming to make, not ours to
   * reinterpret. */
  for (int i = 0; i < _mon_tl_n && p < (int)sizeof(body) - 64; i++) {
    p += snprintf(body + p, sizeof(body) - p, ",\"ch%u_%s\":%.2f",
                  (unsigned)_mon_tl[i].channel,
                  _mon_tl[i].kind == 't' ? "temperature" : "voltage",
                  _mon_tl[i].value);
  }

  int nbr_written = 0;
  char nbr_json[MONITOR_NBR_MAX * 56 + 32];
  nbr_json[0] = 0;

  if (_mon_have_nbr && _mon_nbr_len >= 8) {
    int16_t total, returned;
    memcpy(&total, &_mon_nbr[4], 2);
    memcpy(&returned, &_mon_nbr[6], 2);
    if (total >= 0) {
      p += snprintf(body + p, sizeof(body) - p, ",\"neighbor_count\":%d", (int)total);
    }

    int q = snprintf(nbr_json, sizeof(nbr_json), ",\"neighbors\":[");
    const int entry = MONITOR_NBR_PREFIX + 5;      // key + 4 age + 1 snr
    for (int i = 0; i < returned; i++) {
      int off = 8 + i * entry;
      if (off + entry > _mon_nbr_len) break;

      char hex[MONITOR_NBR_PREFIX * 2 + 1];
      mesh::Utils::toHex(hex, &_mon_nbr[off], MONITOR_NBR_PREFIX);
      uint32_t secs_ago;
      memcpy(&secs_ago, &_mon_nbr[off + MONITOR_NBR_PREFIX], 4);
      int8_t snr4 = (int8_t)_mon_nbr[off + MONITOR_NBR_PREFIX + 4];

      /* No name field: their reply carries only key, age and SNR, and the far
       * end keeps any name it already had when we leave it out. */
      int w = snprintf(nbr_json + q, sizeof(nbr_json) - q,
                       "%s{\"prefix\":\"%s\",\"snr\":%.2f,\"seen_min\":%u}",
                       nbr_written ? "," : "", hex, snr4 / 4.0f,
                       (unsigned)(secs_ago / 60));
      if (w <= 0 || (size_t)(q + w) >= sizeof(nbr_json) - 2) break;
      q += w;
      nbr_written++;
    }
    snprintf(nbr_json + q, sizeof(nbr_json) - q, "]");
  }

  p += snprintf(body + p, sizeof(body) - p, "}%s,\"via\":\"%s\"}",
                nbr_written ? nbr_json : "", _node_hex);
  if (p <= 0 || p >= (int)sizeof(body)) return false;

  char topic[96];
  mqttTopic("stats", topic, sizeof(topic));
  if (!_mqtt.publish(topic, (const uint8_t *)body, p, false)) {
    _fail_count++;
    _mqtt_err = "stats";
    return false;
  }
  m.pubs++;
  m.fails = 0;                 // something came back: no reason to rest
  m.last_ok = millis();
  monTrace("pub st=%d tl=%d nb=%d %db", _mon_have_st ? 1 : 0, _mon_tl_n, nbr_written, p);
  return true;
}

// A round that yielded nothing at all counts towards the backoff.
static void monRoundFailed(MonEntry &m) {
  if (m.fails < 255) m.fails++;
}

/* Node names come off the air and end up inside JSON. Anything with a quote in
 * it would otherwise produce a broken page rather than an odd-looking name. */
static void jsonEsc(char *dest, size_t max, const char *src) {
  size_t o = 0;
  for (const char *p = src; *p && o + 2 < max; p++) {
    unsigned char c = (unsigned char)*p;
    if (c == '"' || c == '\\') {
      dest[o++] = '\\';
      dest[o++] = (char)c;
    } else if (c >= 0x20) {
      dest[o++] = (char)c;
    }
  }
  dest[o] = 0;
}

/* Config changes arrive on the web server's task; the list is only ever
 * mutated in loop(). Same hand-over the wifi/mqtt/power forms already use. */
enum MonAction { MA_NONE = 0, MA_ADD, MA_DEL, MA_PASS, MA_ENABLE, MA_INTERVAL, MA_POLL };
static volatile uint8_t _mon_action = MA_NONE;
static char _ma_key[MON_KEY_HEX_MAX];
static char _ma_name[MON_NAME_MAX];
static char _ma_pass[MON_PASS_MAX];
static uint16_t _ma_num = 0;

static bool monAdd(const char *key_in, const char *name) {
  char key[MON_KEY_HEX_MAX];
  strncpy(key, key_in, sizeof(key) - 1);
  key[sizeof(key) - 1] = 0;
  if (!normaliseKey(key)) return false;

  int i = findMonitor(key);
  if (i >= 0) {
    // Already known under a shorter or longer form: merge, never duplicate.
    if (strlen(key) > strlen(_mon[i].key)) strcpy(_mon[i].key, key);
    if (name && *name && _mon[i].name[0] == 0) {
      strncpy(_mon[i].name, name, sizeof(_mon[i].name) - 1);
      _mon[i].name[sizeof(_mon[i].name) - 1] = 0;
    }
    return true;
  }
  if (_mon_count >= MAX_MONITORS) return false;

  MonEntry &m = _mon[_mon_count];
  memset(&m, 0, sizeof(m));
  m.mesh_idx = -1;
  m.enabled = true;
  strcpy(m.key, key);
  if (name) {
    strncpy(m.name, name, sizeof(m.name) - 1);
    m.name[sizeof(m.name) - 1] = 0;
  }
  _mon_count++;
  return true;
}

static bool monDelete(const char *key) {
  int i = findMonitor(key);
  if (i < 0) return false;
  for (int j = i; j < _mon_count - 1; j++) _mon[j] = _mon[j + 1];
  _mon_count--;
  memset(&_mon[_mon_count], 0, sizeof(MonEntry));
  return true;
}

// Runs in loop(): applies whatever the page or CLI staged, then persists.
static void applyMonAction() {
  uint8_t act = _mon_action;
  _mon_action = MA_NONE;
  if (act == MA_NONE) return;

  bool touched_list = false;
  switch (act) {
    case MA_ADD:
      touched_list = monAdd(_ma_key, _ma_name);
      break;
    case MA_DEL:
      touched_list = monDelete(_ma_key);
      break;
    case MA_PASS: {
      int i = findMonitor(_ma_key);
      if (i >= 0) {
        strncpy(_mon[i].pass, _ma_pass, sizeof(_mon[i].pass) - 1);
        _mon[i].pass[sizeof(_mon[i].pass) - 1] = 0;
        _mon[i].logged_in = false;        // credentials changed: log in again
        _mon[i].login_res = LOGIN_NONE;
      }
      break;
    }
    case MA_ENABLE: {
      int i = findMonitor(_ma_key);
      if (i >= 0) _mon[i].enabled = (_ma_num != 0);
      break;
    }
    case MA_INTERVAL:
      _mon_interval = _ma_num;
      break;
    case MA_POLL:
      _mon_next_round = millis();         // start a round on the next pass
      return;                             // nothing to persist
  }

  saveMonitors();
  if (touched_list) syncMonitorsToMesh();
}

/* Fires the request belonging to a step and waits for it. A send that fails
 * (packet pool empty) skips that step rather than stalling the sequence -- the
 * round still ends in a publish. */
static void monStep(MonState next) {
  if (_mon_cur < 0) return;
  MonEntry &m = _mon[_mon_cur];
  _mon_retry = 0;

  while (true) {
    bool sent = false;
    if (next == MST_REQ_WAIT)        sent = _mesh->sendMonitorStatusReq(m.mesh_idx);
    else if (next == MST_TELEM_WAIT) sent = _mesh->sendMonitorTelemetryReq(m.mesh_idx);
    else if (next == MST_NBR_WAIT)   sent = _mesh->sendMonitorNeighboursReq(m.mesh_idx);

    if (sent) {
      _mon_state = next;
      _mon_deadline = millis() + MON_STEP_MS;
      return;
    }
    monTrace("step %d NOT SENT (pool full)", (int)next);

    if (next == MST_REQ_WAIT)        next = MST_TELEM_WAIT;
    else if (next == MST_TELEM_WAIT) next = MST_NBR_WAIT;
    else break;
  }

  if (!publishMonitorRound(m)) monRoundFailed(m);
  _mon_state = MST_GAP;
  _mon_deadline = millis() + MON_GAP_MS;
}

// Moves to the next entry worth polling, or ends the round.
static void monitorAdvance() {
  for (int i = _mon_cur + 1; i < _mon_count; i++) {
    if (!_mon[i].enabled || _mon[i].mesh_idx < 0) continue;
    if (_mon[i].fails >= MON_BACKOFF_AFTER && (_mon_round % MON_BACKOFF_EVERY) != 0) {
      continue;                        // resting; see MON_BACKOFF_AFTER
    }

    _mon_cur = i;
    _mon_retry = 0;
    _mon[i].polls++;
    monResetResults();
    int hops = _mesh->getMonitorPathLen(_mon[i].mesh_idx);

    if (_mon[i].logged_in) {
      monTrace("req %s hops=%d %.6s", hops < 0 ? "FLOOD" : "direct", hops, _mon[i].key);
      monStep(MST_REQ_WAIT);
      if (_mon_state != MST_GAP) return;
    } else if (_mesh->sendMonitorLogin(_mon[i].mesh_idx, _mon[i].pass)) {
      monTrace("login sent %s hops=%d %.6s", hops < 0 ? "FLOOD" : "direct", hops, _mon[i].key);
      _mon_state = MST_LOGIN_WAIT;
      _mon_deadline = millis() + MON_STEP_MS;
      return;
    } else {
      monTrace("login NOT SENT (pool full) %.6s", _mon[i].key);
    }
    // packet pool empty: leave this one for the next round rather than spin
  }

  _mon_cur = -1;
  _mon_state = MST_IDLE;
  _mon_round++;
  _mon_next_round = millis() + (unsigned long)_mon_interval * 1000UL;
}

static void monitorLoop() {
  if (_safe_mode || _mon_count == 0 || !_mesh) return;
  /* No broker means nowhere to put the answers, and every poll costs the whole
   * mesh airtime. Same for a tired battery: monitoring is a luxury, staying
   * reachable is not. */
  if (!_cfg.mqtt_enabled || _cfg.mqtt_host[0] == 0) return;
  if (_level >= LV_LOW) return;

  if (_mon_got_reply) {
    int idx = _mon_reply_idx;
    _mon_got_reply = false;

    if (_mon_cur >= 0 && _mon[_mon_cur].mesh_idx == idx) {
      MonEntry &m = _mon[_mon_cur];
      if (_mon_state == MST_LOGIN_WAIT) {
        m.login_res = LOGIN_OK;
        m.logged_in = true;
        int hops = _mesh->getMonitorPathLen(m.mesh_idx);
        monTrace("login OK len=%d, req %s hops=%d", _mon_reply_len,
                 hops < 0 ? "FLOOD" : "direct", hops);
        monStep(MST_REQ_WAIT);
        return;
      }
      /* Each answer moves to the next request type. A failure at any step only
       * costs that step: the round still ends in a publish with whatever came
       * back, because half a reading is worth more than none. */
      if (_mon_state == MST_REQ_WAIT) {
        m.oks++;                      // status read succeeded
        m.ok_st++;
        int n = _mon_reply_len - 4;
        if (n >= 20) {
          if (n > (int)sizeof(_mon_st)) n = sizeof(_mon_st);
          memcpy(&_mon_st, &_mon_reply[4], n);   // older firmware sends fewer fields
          _mon_st_len = n;                       // ... which is why we remember how many
          _mon_have_st = true;
        }
        monTrace("status len=%d ok", _mon_reply_len);
        monStep(MST_TELEM_WAIT);
        return;
      }
      if (_mon_state == MST_TELEM_WAIT) {
        monDecodeTelemetry(_mon_reply, _mon_reply_len);
        if (_mon_tl_n > 0) m.ok_tl++;
        monTrace("telem len=%d, %d values", _mon_reply_len, _mon_tl_n);
        monStep(MST_NBR_WAIT);
        return;
      }
      if (_mon_state == MST_NBR_WAIT) {
        _mon_nbr_len = (_mon_reply_len > (int)sizeof(_mon_nbr))
                       ? (int)sizeof(_mon_nbr) : _mon_reply_len;
        memcpy(_mon_nbr, _mon_reply, _mon_nbr_len);
        _mon_have_nbr = (_mon_nbr_len >= 8);
        if (_mon_have_nbr) m.ok_nb++;
        monTrace("nbrs len=%d", _mon_reply_len);
        publishMonitorRound(m);
        _mon_state = MST_GAP;
        _mon_deadline = millis() + MON_GAP_MS;
        return;
      }
      monTrace("reply len=%d but state=%d, dropped", _mon_reply_len, (int)_mon_state);
    } else {
      monTrace("reply for idx=%d, current=%d, dropped", idx,
               _mon_cur >= 0 ? _mon[_mon_cur].mesh_idx : -1);
    }
  }

  switch (_mon_state) {
    case MST_IDLE:
      if (!passed(_mon_next_round)) return;
      if (resolveMonitors()) {           // a prefix may have become a full key
        saveMonitors();
        syncMonitorsToMesh();
      }
      _mon_cur = -1;
      monTrace("round start, %d entries", _mon_count);
      monitorAdvance();
      break;

    case MST_LOGIN_WAIT:
      if (!passed(_mon_deadline)) return;
      if (_mon_cur < 0) { _mon_state = MST_GAP; _mon_deadline = millis() + MON_GAP_MS; break; }

      /* Silence. Either they refused us or they are out of reach, and the
       * protocol gives no way to tell those apart. If the attempt went direct,
       * one stale hop would explain it, so retry once by flood before drawing
       * any conclusion -- the trace then says which of the two worked. */
      if (_mon_retry == 0 && _mesh->getMonitorPathLen(_mon[_mon_cur].mesh_idx) >= 0) {
        _mesh->resetMonitorPath(_mon[_mon_cur].mesh_idx);
        if (_mesh->sendMonitorLogin(_mon[_mon_cur].mesh_idx, _mon[_mon_cur].pass)) {
          monTrace("login timeout, retry FLOOD");
          _mon_retry = 1;
          _mon_deadline = millis() + MON_STEP_MS;
          return;
        }
      }
      monTrace("login timeout, giving up (retry=%u)", _mon_retry);
      _mon[_mon_cur].login_res = LOGIN_NOANSWER;
      monRoundFailed(_mon[_mon_cur]);
      _mon_state = MST_GAP;
      _mon_deadline = millis() + MON_GAP_MS;
      break;

    case MST_REQ_WAIT:
      if (!passed(_mon_deadline)) return;
      if (_mon_cur < 0) { _mon_state = MST_GAP; _mon_deadline = millis() + MON_GAP_MS; break; }

      /* The login just worked, so they are reachable and they know us. A status
       * request that then goes unanswered points at the route rather than the
       * session: the login was flooded and answered over a path we learned from
       * it, and that path is the one thing the status request relies on and the
       * login did not. So drop it and ask once more by flood, in this same
       * round, before writing the round off. */
      if (_mon_retry == 0) {
        _mesh->resetMonitorPath(_mon[_mon_cur].mesh_idx);
        if (_mesh->sendMonitorStatusReq(_mon[_mon_cur].mesh_idx)) {
          monTrace("req timeout, retry FLOOD");
          _mon_retry = 1;
          _mon_deadline = millis() + MON_STEP_MS;
          return;
        }
        monTrace("req timeout, retry NOT SENT (pool full)");
      } else {
        monTrace("req timeout after flood retry, giving up");
      }
      // Log in again next round; the session may be what went stale.
      _mon[_mon_cur].logged_in = false;
      monRoundFailed(_mon[_mon_cur]);
      _mon_state = MST_GAP;
      _mon_deadline = millis() + MON_GAP_MS;
      break;

    /* Telemetry and neighbours get no flood retry. They are extras on top of a
     * status reading we may already hold, and spending another round trip (and
     * everyone else's airtime) on a nice-to-have is the wrong trade for a node
     * whose day job is relaying other people's traffic. */
    case MST_TELEM_WAIT:
      if (!passed(_mon_deadline)) return;
      if (_mon_cur < 0) { _mon_state = MST_GAP; _mon_deadline = millis() + MON_GAP_MS; break; }
      monTrace("telem timeout, skipping");
      monStep(MST_NBR_WAIT);
      break;

    case MST_NBR_WAIT:
      if (!passed(_mon_deadline)) return;
      if (_mon_cur < 0) { _mon_state = MST_GAP; _mon_deadline = millis() + MON_GAP_MS; break; }
      monTrace("nbrs timeout, publishing what we have");
      if (!publishMonitorRound(_mon[_mon_cur])) monRoundFailed(_mon[_mon_cur]);
      _mon_state = MST_GAP;
      _mon_deadline = millis() + MON_GAP_MS;
      break;

    case MST_GAP:
      if (!passed(_mon_deadline)) return;
      monitorAdvance();
      break;
  }
}

// ---------------------------------------------------------------- admin page

/* One static PROGMEM string, sent in a single write. The earlier version built
 * the HTML in pieces with live values baked in; every piece is a separate
 * blocking write, and with the latency spikes of ESP32 wifi (modem-sleep) the
 * main loop stalled inside them -- taking the mesh down with it. So: one send,
 * and the page fetches its data as JSON afterwards.
 *
 * Styling follows the public MeshStats site (same tokens, cards, green section
 * heads) so the two stay one visual family. Theme and language live entirely
 * in the browser: colours are CSS variables swapped by data-theme, and every
 * label carries a data-i18n key that JavaScript fills from one of two small
 * dictionaries. Both choices are remembered in localStorage.
 *
 * That is also why /api/status returns codes instead of finished sentences:
 * the firmware should not have an opinion about the reader's language. */
static const char PAGE[] PROGMEM =
  "<!doctype html><html><head><meta charset=utf-8>"
  "<meta name=viewport content='width=device-width,initial-scale=1'>"
  "<title>MeshCore repeater</title><style>"
  ":root{--bg:#0b0f14;--grid:#10161e;--card:#121a23;--edge:#1e2b3a;--text:#d7e2ea;"
  "--muted:#7d8fa0;--accent:#35e08c;--dim:#1d7a4f;--cyan:#4cc9f0;--amber:#ffb454;"
  "--bar:rgba(11,15,20,.82);--line:rgba(255,255,255,.014);"
  "--mono:ui-monospace,'Cascadia Code',Consolas,monospace;"
  "--sans:system-ui,-apple-system,'Segoe UI',Roboto,sans-serif}"
  ":root[data-theme=light]{--bg:#eef3f1;--grid:#e2eae6;--card:#fff;--edge:#d2ddd7;"
  "--text:#16241d;--muted:#5b6b63;--accent:#0e9c60;--dim:#0b7a4b;--cyan:#0b7fa8;"
  "--amber:#b8741a;--bar:rgba(255,255,255,.86);--line:rgba(10,40,25,.04)}"
  "*{box-sizing:border-box}html{background:var(--bg)}"
  "body{margin:0;color:var(--text);font:15px/1.5 var(--sans);min-height:100vh;"
  "background:radial-gradient(ellipse 80% 50% at 50% -10%,rgba(53,224,140,.07),transparent),"
  "repeating-linear-gradient(0deg,transparent 0 23px,var(--line) 23px 24px),"
  "repeating-linear-gradient(90deg,transparent 0 23px,var(--line) 23px 24px),var(--bg);"
  "background-attachment:fixed}"
  ".topbar{display:flex;justify-content:space-between;align-items:center;gap:.6rem;"
  "padding:.7rem 1.2rem;background:var(--bar);backdrop-filter:blur(8px);"
  "border-bottom:1px solid var(--edge);position:sticky;top:0;z-index:10}"
  ".brand{font-family:var(--mono);font-weight:700;color:var(--accent);letter-spacing:.03em;"
  "text-shadow:0 0 12px rgba(53,224,140,.35)}"
  "main{max-width:680px;margin:0 auto;padding:.5rem 1.2rem 3rem}"
  "h1{font-size:1.5rem;margin:1.2rem 0 .2rem;letter-spacing:-.01em}"
  "h2{font-family:var(--mono);font-size:.82rem;margin:2rem 0 .7rem;text-transform:uppercase;"
  "letter-spacing:.2em;color:var(--accent)}h2::before{content:'\\25B8  ';color:var(--dim)}"
  "h3{font-family:var(--mono);font-size:.72rem;text-transform:uppercase;letter-spacing:.12em;"
  "color:var(--muted);margin:1.3rem 0 .4rem}"
  "a{color:var(--cyan);text-decoration:none}"
  "small{color:var(--muted);font-size:.78em}"
  ".ok{color:var(--accent)}.bad{color:var(--amber)}"
  ".mini{padding:.15rem .5rem;font-size:.75rem;border-radius:99px}"
  "td.act{text-align:right;white-space:nowrap}"
  ".trace{font-family:var(--mono);font-size:.75rem;color:var(--muted);margin:0;"
  "white-space:pre-wrap;word-break:break-word;max-height:12rem;overflow-y:auto}"
  ".muted{color:var(--muted);font-size:.85rem}"
  ".card{background:linear-gradient(180deg,rgba(255,255,255,.025),transparent 55%),var(--card);"
  "border:1px solid var(--edge);border-radius:10px;padding:1rem}"
  ".warn{border-color:var(--amber);margin:1rem 0}"
  "table{width:100%;border-collapse:collapse}"
  "td{padding:.4rem .5rem;border-bottom:1px solid var(--edge);font-family:var(--mono);font-size:.85rem}"
  "td:first-child{color:var(--muted);white-space:nowrap}tr:last-child td{border-bottom:none}"
  "input,select,button{font:inherit;padding:.45rem .7rem;border-radius:7px;"
  "border:1px solid var(--edge);background:var(--grid);color:var(--text)}"
  "input:focus,select:focus{outline:1px solid var(--dim);border-color:var(--dim)}"
  "button{background:var(--dim);color:#eafff4;cursor:pointer;font-family:var(--mono);font-size:.85rem}"
  "button:hover{background:var(--accent);color:#06130c;box-shadow:0 0 12px rgba(53,224,140,.35)}"
  ".pill{background:transparent;border:1px solid var(--edge);color:var(--muted);"
  "border-radius:99px;padding:.2rem .6rem;font-size:.78rem}"
  ".pill:hover{background:transparent;color:var(--text);border-color:var(--muted);box-shadow:none}"
  "label{display:block;margin-bottom:.8rem}"
  "label input,label select{width:100%;margin-top:.25rem}"
  ".row{display:flex;gap:.6rem}.row label{flex:1}"
  "label input.ck{width:auto;margin:0 .45rem 0 0}"
  "code{font-family:var(--mono);font-size:.85em;color:var(--cyan)}"
  "</style></head><body>"
  "<div class=topbar><span class=brand>MeshStats</span>"
  "<span><button class=pill id=lg></button> <button class=pill id=th></button></span></div>"
  "<main>"
  "<h1 id=nm>MeshCore</h1><p class=muted id=sub></p>"
  "<div id=safe></div>"
  "<h2 data-i18n=t_state></h2><div class=card><table id=st></table></div>"
  "<h2 data-i18n=t_wifi></h2><div class=card><form id=f>"
  "<label><span data-i18n=l_ssid></span><input name=ssid></label>"
  "<label><span data-i18n=l_pass></span><input name=pass type=password data-i18n-ph=ph_unch></label>"
  "<label><span data-i18n=l_appass></span><input name=ap_pass type=password data-i18n-ph=ph_unch></label>"
  "<button type=submit data-i18n=b_saveconn></button></form>"
  "<p class=muted data-i18n=h_wifi></p></div>"
  "<h2 data-i18n=t_power></h2><div class=card><form id=p>"
  "<div class=row><label><span data-i18n=l_mode></span><select name=mode>"
  "<option value=0 data-i18n=o_always></option><option value=1 data-i18n=o_save></option>"
  "</select></label>"
  "<label style='max-width:9rem'><span data-i18n=l_window></span>"
  "<input name=window type=number min=30 max=3600></label></div>"
  "<label><input class=ck type=checkbox name=sleep><span data-i18n=l_sleep></span></label>"
  "<button type=submit data-i18n=b_save></button></form>"
  "<p class=muted data-i18n=h_power></p></div>"
  "<h2 data-i18n=t_mqtt></h2><div class=card><form id=m>"
  "<div class=row><label><span data-i18n=l_broker></span><input name=host placeholder='10.0.0.5'></label>"
  "<label style='max-width:7rem'><span data-i18n=l_port></span>"
  "<input name=port type=number min=1 max=65535></label></div>"
  "<div class=row><label><span data-i18n=l_user></span><input name=user></label>"
  "<label><span data-i18n=l_pass></span><input name=pass type=password data-i18n-ph=ph_unch></label></div>"
  "<label><span data-i18n=l_prefix></span><input name=prefix></label>"
  "<label><input class=ck type=checkbox name=enabled><span data-i18n=l_enabled></span></label>"
  "<label><input class=ck type=checkbox name=rx><span data-i18n=l_rx></span></label>"
  "<button type=submit data-i18n=b_save></button></form><table id=mt></table>"
  "<p class=muted><span data-i18n=h_topics></span> <code id=tp></code></p></div>"
  "<h2 data-i18n=t_mon></h2><div class=card>"
  "<p class=muted data-i18n=h_mon></p>"
  "<label><span data-i18n=l_filter></span><input id=fl></label>"
  "<h3 data-i18n=t_heard></h3><table id=hl></table>"
  "<h3 data-i18n=t_manual></h3>"
  "<div class=row><label><span data-i18n=l_key></span><input id=mk></label>"
  "<label style='max-width:9rem'><span data-i18n=l_name></span><input id=mn></label></div>"
  "<button id=mab data-i18n=b_add></button>"
  "<h3 data-i18n=t_monlist></h3><table id=ml></table>"
  "<h3 data-i18n=t_trace></h3><pre id=tr class=trace></pre>"
  "<p class=muted data-i18n=h_acl></p>"
  "<div class=row style='margin-top:.6rem'>"
  "<label style='max-width:10rem'><span data-i18n=l_moniv></span>"
  "<input id=miv type=number min=60 max=65535></label></div>"
  "<button id=mivb data-i18n=b_save></button> "
  "<button id=mpb class=pill data-i18n=b_pollnow></button></div>"
  "<h2 data-i18n=t_fw></h2><div class=card>"
  "<p class=muted data-i18n=h_fw></p><p><a href=/update data-i18n=a_fw></a></p></div>"
  "<h2 data-i18n=t_backup></h2><div class=card>"
  "<p class=muted data-i18n=h_backup></p>"
  "<p><a href=/api/backup data-i18n=a_backup></a></p>"
  "<form id=r style='margin-top:.8rem'>"
  "<label><span data-i18n=l_restore></span><input type=file name=f accept='.mcb'></label>"
  "<button type=submit data-i18n=b_restore></button></form></div>"
  "</main><script>"
  "var T={nl:{"
  "t_state:'Toestand',t_wifi:'WiFi',t_power:'Energie',t_mqtt:'MQTT',t_fw:'Firmware',"
  "t_backup:'Back-up',l_ssid:'Netwerk (SSID)',l_pass:'Wachtwoord',"
  "l_appass:'Wachtwoord van het eigen netwerk',b_saveconn:'Opslaan en verbinden',b_save:'Opslaan',"
  "ph_unch:'ongewijzigd',"
  "h_wifi:'Lukt verbinden niet, dan zendt de repeater zijn eigen netwerk uit en blijft hij het "
  "jouwe proberen. Via de mesh-CLI werkt wifi altijd.',"
  "l_mode:'Modus',o_always:'Altijd bereikbaar',o_save:'Zuinig (WiFi meestal uit)',"
  "l_window:'Venster (s)',l_sleep:'Modem-sleep terwijl WiFi aan staat',"
  "h_power:'Hoe voller de accu, hoe vaker de repeater publiceert; \\u2019s nachts trager. In de "
  "zuinige modus staat WiFi normaal uit en komt hij elke ronde even boven water; ontvangen "
  "pakketten wachten intussen in een buffer. Kwijt geraakt? wifi on 30 via de mesh-CLI zet WiFi "
  "meteen 30 minuten aan, ook bij een lege accu. Drempels en intervallen wijzig je met "
  "wifi power set <naam> <waarde>.',"
  "l_broker:'Broker',l_port:'Poort',l_user:'Gebruiker',l_prefix:'Topicprefix',"
  "l_enabled:'Doorsturen ingeschakeld',l_rx:'Ook elk ontvangen pakket doorsturen',"
  "h_topics:'Topics:',"
  "h_fw:'Upgraden kan hier, over je gewone WiFi. Een afgebroken upload laat de huidige firmware "
  "staan.',a_fw:'Firmware uploaden \\u2192',"
  "h_backup:'De back-up bevat alles uit het bestandssysteem: je sleutelpaar, de "
  "repeater-instellingen, de ACL en de netwerkinstellingen. Wie dit bestand heeft, heeft de "
  "identiteit van je node \\u2014 bewaar het veilig.',a_backup:'Back-up downloaden \\u2192',"
  "l_restore:'Back-up terugzetten',b_restore:'Terugzetten en herstarten',"
  "safe:'Veilige modus: de repeater is meermaals herstart, dus alle extra\\u2019s staan uit. "
  "Herstart hem opnieuw zodra je de oorzaak weg hebt.',"
  "s_wifi:'WiFi',s_ip:'IP',s_net:'Netwerk',s_signal:'Signaal',s_uptime:'Uptime',"
  "s_heap:'Vrij geheugen',s_batt:'Batterij',s_power:'Energie',s_wdt:'Watchdog',"
  "s_mcu:'Chiptemperatuur',h_mcu:'van de chip zelf, niet de buitenlucht',"
  "m_reads:'gelezen',m_pubs:'verstuurd',m_kinds:'status/telemetrie/buren',"
  "u_min:'min',d_on:'actief (% s)',d_off:'uit (upload bezig)',s_live:'Live doorsturen',"
  "lv_on:'aan \\u2014 pakketten gaan meteen door',"
  "lv_batt:'uit \\u2014 accu onder %%',"
  "lv_save:'niet mogelijk in zuinige modus (WiFi gaat tussendoor uit)',"
  "w_ok:'verbonden',w_try:'verbinden\\u2026',w_ap:'eigen netwerk (WiFi onbereikbaar)',"
  "w_off:'uit (zuinig)',lv0:'vol',lv1:'hoog',lv2:'normaal',lv3:'laag',lv4:'kritiek',"
  "b_unknown:'onbekend (aangenomen: netstroom)',"
  "p_always:'altijd bereikbaar',p_forced:'opgevorderd, nog % min',"
  "p_awake:'zuinig, nog % s bereikbaar',p_asleep:'zuinig, wifi terug over % s',"
  "p_every:'publiceert elke % s',p_night:'nacht',"
  "m_broker:'Broker',m_stats:'Statistieken',m_pkts:'Pakketten',m_queue:'Wachtrij',"
  "m_errors:'Fouten',m_sent:'verstuurd',m_fwd:'doorgestuurd',m_drop:'laten vallen',"
  "mq_off:'uit',mq_conn:'verbonden',mq_disc:'niet verbonden',mq_unset:'niet ingesteld',"
  "e_conn:'verbinding faalde (rc %)',e_stats:'stats versturen faalde',"
  "e_pkt:'pakket versturen faalde',"
  "t_mon:'Monitoren',t_heard:'Gehoorde repeaters',t_manual:'Handmatig toevoegen',"
  "t_monlist:'Wordt gemonitord',t_trace:'Verloop laatste ronde',l_filter:'Filteren',l_key:'Publieke sleutel (hex)',"
  "l_name:'Naam',b_add:'Toevoegen',l_moniv:'Interval (s)',b_pollnow:'Nu ophalen',"
  "h_mon:'Deze repeater kan op andere repeaters inloggen, hun status ophalen en die "
  "doorsturen naar de site. Kies er een uit de gehoorde lijst, of plak een publieke "
  "sleutel van een node die je nog niet gehoord hebt.',"
  "h_acl:'Laat het wachtwoord leeg om via de access list van de andere node binnen te "
  "raken: die operator voegt jouw sleutel dan \\u00e9\\u00e9nmalig toe met "
  "setperm <jouw-sleutel> 1 (1=alleen lezen, 2=lezen/schrijven, 3=beheerder). Netter dan "
  "wachtwoorden rondsturen. Een geweigerde login is niet te onderscheiden van een "
  "onbereikbare node: beide zwijgen.',"
  "m_none:'nog niets',m_heardnone:'nog geen repeaters gehoord',h_stored:'uit bewaarde adverts',"
  "st_unres:'wacht op advert',st_never:'nog niet geprobeerd',st_ok:'login gelukt',"
  "st_noans:'geen antwoord (geweigerd of onbereikbaar)',"
  "ph_pw:'leeg = via access list',u_ago:'geleden',u_never:'nooit',"
  "a_badkey:'Dat is geen geldige sleutel. Alleen hex, minstens % tekens.',"
  "a_saved:'Opgeslagen. De repeater verbindt opnieuw; ververs deze pagina zo dadelijk.',"
  "a_pick:'Kies eerst een back-upbestand.',"
  "a_conf:'Alle instellingen en sleutels worden overschreven. Doorgaan?'},"
  "en:{"
  "t_state:'Status',t_wifi:'WiFi',t_power:'Power',t_mqtt:'MQTT',t_fw:'Firmware',"
  "t_backup:'Backup',l_ssid:'Network (SSID)',l_pass:'Password',"
  "l_appass:'Password of its own network',b_saveconn:'Save and connect',b_save:'Save',"
  "ph_unch:'unchanged',"
  "h_wifi:'If it cannot connect, the repeater broadcasts its own network and keeps retrying "
  "yours. Over the mesh CLI, wifi always works.',"
  "l_mode:'Mode',o_always:'Always reachable',o_save:'Power save (WiFi mostly off)',"
  "l_window:'Window (s)',l_sleep:'Modem sleep while WiFi is up',"
  "h_power:'The fuller the battery, the more often the repeater publishes; slower at night. In "
  "power-save mode WiFi is normally off and only surfaces once per round; received packets wait "
  "in a buffer meanwhile. Locked out? wifi on 30 over the mesh CLI brings WiFi up for 30 minutes "
  "right away, even on a flat battery. Thresholds and intervals are changed with "
  "wifi power set <name> <value>.',"
  "l_broker:'Broker',l_port:'Port',l_user:'User',l_prefix:'Topic prefix',"
  "l_enabled:'Forwarding enabled',l_rx:'Also forward every received packet',"
  "h_topics:'Topics:',"
  "h_fw:'Upgrade here, over your normal WiFi. An aborted upload leaves the current firmware in "
  "place.',a_fw:'Upload firmware \\u2192',"
  "h_backup:'The backup holds everything in the file system: your key pair, the repeater "
  "settings, the ACL and the network settings. Whoever has this file has your node\\u2019s "
  "identity \\u2014 keep it safe.',a_backup:'Download backup \\u2192',"
  "l_restore:'Restore a backup',b_restore:'Restore and restart',"
  "safe:'Safe mode: the repeater restarted repeatedly, so all extras are off. Restart it once "
  "you have removed the cause.',"
  "s_wifi:'WiFi',s_ip:'IP',s_net:'Network',s_signal:'Signal',s_uptime:'Uptime',"
  "s_heap:'Free memory',s_batt:'Battery',s_power:'Power',s_wdt:'Watchdog',"
  "s_mcu:'Chip temperature',h_mcu:'of the chip itself, not the outside air',"
  "m_reads:'read',m_pubs:'published',m_kinds:'status/telemetry/neighbours',"
  "u_min:'min',d_on:'armed (% s)',d_off:'off (upload in progress)',s_live:'Live forwarding',"
  "lv_on:'on \\u2014 packets go out immediately',"
  "lv_batt:'off \\u2014 battery below %%',"
  "lv_save:'not possible in power-save mode (WiFi goes off between rounds)',"
  "w_ok:'connected',w_try:'connecting\\u2026',w_ap:'own network (WiFi unreachable)',"
  "w_off:'off (power save)',lv0:'full',lv1:'high',lv2:'normal',lv3:'low',lv4:'critical',"
  "b_unknown:'unknown (assuming mains)',"
  "p_always:'always reachable',p_forced:'forced up, % min left',"
  "p_awake:'power save, reachable for % s',p_asleep:'power save, wifi back in % s',"
  "p_every:'publishes every % s',p_night:'night',"
  "m_broker:'Broker',m_stats:'Statistics',m_pkts:'Packets',m_queue:'Queue',"
  "m_errors:'Errors',m_sent:'sent',m_fwd:'forwarded',m_drop:'dropped',"
  "mq_off:'off',mq_conn:'connected',mq_disc:'not connected',mq_unset:'not configured',"
  "e_conn:'connection failed (rc %)',e_stats:'publishing stats failed',"
  "e_pkt:'publishing packet failed',"
  "t_mon:'Monitoring',t_heard:'Repeaters heard',t_manual:'Add by hand',"
  "t_monlist:'Being monitored',t_trace:'Last round in detail',l_filter:'Filter',l_key:'Public key (hex)',"
  "l_name:'Name',b_add:'Add',l_moniv:'Interval (s)',b_pollnow:'Poll now',"
  "h_mon:'This repeater can log in to other repeaters, fetch their status and forward it "
  "to the site. Pick one from the heard list, or paste the public key of a node you have "
  "not heard yet.',"
  "h_acl:'Leave the password empty to get in via the other node\\u2019s access list: its "
  "operator adds your key once with setperm <your-key> 1 (1=read-only, 2=read/write, "
  "3=admin). Tidier than passing passwords around. A refused login cannot be told from an "
  "unreachable node: both stay silent.',"
  "m_none:'nothing yet',m_heardnone:'no repeaters heard yet',h_stored:'from stored adverts',"
  "st_unres:'waiting for advert',st_never:'not tried yet',st_ok:'login succeeded',"
  "st_noans:'no answer (refused or unreachable)',"
  "ph_pw:'empty = via access list',u_ago:'ago',u_never:'never',"
  "a_badkey:'That is not a valid key. Hex only, at least % characters.',"
  "a_saved:'Saved. The repeater is reconnecting; refresh this page in a moment.',"
  "a_pick:'Pick a backup file first.',"
  "a_conf:'All settings and keys will be overwritten. Continue?'}};"
  "var $=function(s){return document.querySelector(s)},last=null;"
  "var L=localStorage.getItem('mslang')||((navigator.language||'').indexOf('nl')==0?'nl':'en');"
  "var TH=localStorage.getItem('mstheme')||"
  "(matchMedia('(prefers-color-scheme: light)').matches?'light':'dark');"
  "function theme(){document.documentElement.setAttribute('data-theme',TH);"
  "$('#th').textContent=TH=='light'?'\\u263e':'\\u2600'}"
  "function lang(){var d=T[L];document.documentElement.lang=L;renderMon();"
  "$('#lg').textContent=L=='nl'?'EN':'NL';"
  "document.querySelectorAll('[data-i18n]').forEach(function(e){"
  "var v=d[e.getAttribute('data-i18n')];if(v)e.textContent=v});"
  "document.querySelectorAll('[data-i18n-ph]').forEach(function(e){"
  "var v=d[e.getAttribute('data-i18n-ph')];if(v)e.placeholder=v});"
  "render()}"
  "function rows(o){var h='';for(var k in o){h+='<tr><td>'+k+'</td><td>'+o[k]+'</td></tr>'}return h}"
  "function fill(f,c){for(var k in c){var e=f[k];if(!e)continue;"
  "if(e.type=='checkbox')e.checked=!!c[k];else e.value=c[k]}}"
  "function pwrtext(d){var t=T[L],p=d.pwr,s=t['p_'+p.st]||p.st;"
  "s=s.replace('%',p.st=='forced'?Math.ceil(p.secs/60):p.secs);"
  "return s+' \\u00b7 '+t.p_every.replace('%',p.iv)+(p.night?' ('+t.p_night+')':'')}"
  "function render(){var d=last,t=T[L];if(!d)return;"
  "$('#nm').textContent=d.name;"
  "$('#sub').textContent=d.ms+' \\u00b7 MeshCore '+d.fw+' \\u00b7 '+d.board"
  "+' \\u00b7 id '+d.node;"
  "var s={};s[t.s_wifi]=t['w_'+d.wifi.st];s[t.s_ip]=d.wifi.ip;s[t.s_net]=d.wifi.net;"
  "s[t.s_signal]=d.wifi.rssi+' dBm';s[t.s_uptime]=d.wifi.up+' '+t.u_min;"
  "s[t.s_heap]=d.wifi.heap+' bytes';"
  "s[t.s_batt]=d.bat.known?((d.bat.mv/1000).toFixed(2)+' V \\u00b7 '+d.bat.pct+'% \\u00b7 '"
  "+t['lv'+d.bat.lv]):t.b_unknown;"
  "s[t.s_power]=pwrtext(d);"
  "if(d.mcu_t>-100)s[t.s_mcu]=d.mcu_t.toFixed(1)+' \\u00b0C <small>'+t.h_mcu+'</small>';"
  "s[t.s_live]=(t['lv_'+d.live]||d.live).replace('%',d.livepct);"
  "s[t.s_wdt]=d.wdt?t.d_on.replace('%',d.wdt_s):t.d_off;$('#st').innerHTML=rows(s);"
  "var q=d.mqtt,m={};m[t.m_broker]=t['mq_'+q.st];"
  "m[t.m_stats]=q.stats+' '+t.m_sent;"
  "m[t.m_pkts]=q.pkt+' '+t.m_fwd+', '+q.drop+' '+t.m_drop;"
  "m[t.m_queue]=q.queue;"
  "m[t.m_errors]=q.fail+(q.err?' \\u2014 '+(t['e_'+q.err]||q.err).replace('%',q.rc):'');"
  "$('#mt').innerHTML=rows(m);"
  "$('#f').ssid.value=d.ssid;fill($('#p'),d.pwr);fill($('#m'),d.mqtt);"
  "$('#tp').textContent=q.prefix+'/'+d.node+'/stats + /rx';"
  "$('#safe').innerHTML=d.safe?'<div class=\"card warn\">'+t.safe+'</div>':''}"
  "function load(){fetch('/api/status').then(function(r){return r.json()})"
  ".then(function(d){last=d;render()})}"
  // ---- monitoring -------------------------------------------------------
  "var mon=null;"
  "function esc(s){return String(s).replace(/[&<>\"]/g,function(c){"
  "return {'&':'&amp;','<':'&lt;','>':'&gt;','\"':'&quot;'}[c]})}"
  "function hit(o,q){if(!q)return true;q=q.toLowerCase();"
  "return (o.n||'').toLowerCase().indexOf(q)>=0||(o.k||'').toLowerCase().indexOf(q)>=0}"
  "function ago(s,t){return s<0?t.u_never:(s<120?s+' s':Math.round(s/60)+' min')+' '+t.u_ago}"
  "function monst(m,t){if(!m.res)return'<span class=bad>'+t.st_unres+'</span>';"
  "if(m.lr==1)return'<span class=ok>'+t.st_ok+'</span>';"
  "if(m.lr==2)return'<span class=bad>'+t.st_noans+'</span>';return t.st_never}"
  "function renderMon(){var d=mon,t=T[L];if(!d)return;"
  "var q=$('#fl').value,h='',i;"
  "for(i=0;i<d.heard.length;i++){var x=d.heard[i];if(!hit(x,q))continue;"
  "h+='<tr><td>'+esc(x.n||'-')+'<br><small>'+x.k.substr(0,12)+'</small></td>'"
  "+'<td>'+(x.snr==null?'<span class=muted>'+t.h_stored+'</span>':x.snr.toFixed(1)+' dB')"
  "+'<br><small>'+ago(x.age,t)+'</small></td>'"
  "+'<td class=act><button class=\"mini ha\" data-k=\"'+x.k+'\" data-n=\"'+esc(x.n||'')+'\">+</button></td></tr>'}"
  "$('#hl').innerHTML=h||'<tr><td>'+t.m_heardnone+'</td></tr>';"
  "h='';"
  "for(i=0;i<d.mon.length;i++){var m=d.mon[i];if(!hit(m,q))continue;"
  "h+='<tr><td>'+esc(m.n||'-')+'<br><small>'+m.k.substr(0,12)+'</small></td>'"
  "+'<td>'+monst(m,t)+'<br><small>'+m.oks+' '+t.m_reads+', '+m.pubs+' '+t.m_pubs"
  "+' / '+m.polls+' \\u00b7 '+ago(m.age,t)+'<br>'+t.m_kinds+': '+m.st+'/'+m.tl+'/'+m.nb"
  "+'</small></td>'"
  "+'<td><input type=password class=mp data-k=\"'+m.k+'\" placeholder=\"'"
  "+(m.pw?t.ph_unch:t.ph_pw)+'\"></td>'"
  "+'<td class=act><input type=checkbox class=\"ck me\" data-k=\"'+m.k+'\"'"
  "+(m.e?' checked':'')+'> <button class=\"mini md\" data-k=\"'+m.k+'\">\\u2715</button></td></tr>'}"
  "$('#ml').innerHTML=h||'<tr><td>'+t.m_none+'</td></tr>';"
  "$('#tr').textContent=(d.trace&&d.trace.length)?d.trace.join('\\n'):t.m_none;"
  "$('#miv').value=d.iv}"
  "function loadMon(){fetch('/api/mon').then(function(r){return r.json()})"
  ".then(function(d){mon=d;renderMon()})}"
  "function monPost(b,cb){fetch('/api/mon',{method:'POST',body:new URLSearchParams(b)})"
  ".then(function(r){return r.json()}).then(function(j){"
  "if(j.err=='key')alert(T[L].a_badkey.replace('%',mon?mon.minhex:12));"
  "if(cb)cb();loadMon()})}"
  "$('#fl').oninput=renderMon;"
  "document.addEventListener('click',function(e){var b=e.target;"
  "if(b.classList.contains('ha'))monPost({act:'add',key:b.dataset.k,name:b.dataset.n});"
  "else if(b.classList.contains('md'))monPost({act:'del',key:b.dataset.k})});"
  "document.addEventListener('change',function(e){var b=e.target;"
  "if(b.classList.contains('me'))monPost({act:'en',key:b.dataset.k,on:b.checked?1:''});"
  "else if(b.classList.contains('mp'))monPost({act:'pass',key:b.dataset.k,pass:b.value})});"
  "$('#mab').onclick=function(){monPost({act:'add',key:$('#mk').value,name:$('#mn').value},"
  "function(){$('#mk').value='';$('#mn').value=''})};"
  "$('#mivb').onclick=function(){monPost({act:'iv',secs:$('#miv').value})};"
  "$('#mpb').onclick=function(){monPost({act:'poll'})};"
  "function post(u,f,cb){fetch(u,{method:'POST',body:new URLSearchParams(new FormData(f))})"
  ".then(cb)}"
  "$('#th').onclick=function(){TH=TH=='light'?'dark':'light';"
  "localStorage.setItem('mstheme',TH);theme()};"
  "$('#lg').onclick=function(){L=L=='nl'?'en':'nl';localStorage.setItem('mslang',L);lang()};"
  "$('#f').onsubmit=function(e){e.preventDefault();post('/api/wifi',$('#f'),function(){"
  "$('#f').pass.value='';$('#f').ap_pass.value='';alert(T[L].a_saved)})};"
  "$('#p').onsubmit=function(e){e.preventDefault();post('/api/power',$('#p'),load)};"
  "$('#m').onsubmit=function(e){e.preventDefault();post('/api/mqtt',$('#m'),function(){"
  "$('#m').pass.value='';load()})};"
  "$('#r').onsubmit=function(e){e.preventDefault();var f=$('#r').f.files[0];"
  "if(!f){alert(T[L].a_pick);return}if(!confirm(T[L].a_conf))return;"
  "var b=new FormData();b.append('f',f);"
  "fetch('/api/restore',{method:'POST',body:b}).then(function(r){return r.json()})"
  ".then(function(j){alert(j.msg)})};"
  "theme();lang();load();loadMon();setInterval(load,5000);setInterval(loadMon,20000);"
  "</script></body></html>";

/* The admin page hands out your keys (backup) and can flash firmware. That may
 * not sit open on your network without a login. */
static bool requireAuth(AsyncWebServerRequest *req) {
  if (req->authenticate(_cfg.user, _cfg.console_pass)) return true;
  req->requestAuthentication();
  return false;
}

/* Values and codes only -- no finished sentences. The page renders them in the
 * reader's language, which is also why the battery arrives as millivolts,
 * percentage and level rather than as a formatted string. */
static void handleStatus(AsyncWebServerRequest *req) {
  if (!requireAuth(req)) return;
  static char body[1600];
  IPAddress ip = (_state == WIFI_FALLBACK_AP) ? WiFi.softAPIP() : WiFi.localIP();

  // "%.1f" of a NAN prints 'nan', which is not JSON and would blank the page.
  float mcu_t = board.getMCUTemperature();
  if (isnan(mcu_t)) mcu_t = -999.0f;

  int n = snprintf(body, sizeof(body),
    "{\"name\":\"%s\",\"node\":\"%s\",\"board\":\"%s\",\"fw\":\"%s\","
    "\"ms\":\"%s v%s\",\"ssid\":\"%s\",\"safe\":%d,"
    "\"wifi\":{\"st\":\"%s\",\"ip\":\"%s\",\"net\":\"%s\",\"rssi\":%d,"
    "\"up\":%lu,\"heap\":%u},"
    "\"bat\":{\"known\":%d,\"mv\":%u,\"pct\":%u,\"lv\":%u},\"wdt\":%d,\"wdt_s\":%d,"
    "\"mcu_t\":%.1f,"
    "\"pwr\":{\"st\":\"%s\",\"secs\":%u,\"iv\":%u,\"night\":%d,"
    "\"mode\":%u,\"window\":%u,\"sleep\":%u},\"live\":\"%s\",\"livepct\":%u,",
    _mesh ? _mesh->getNodeName() : "repeater", _node_hex,
    board.getManufacturerName(), FIRMWARE_VERSION,
    MESHSTATS_NAME, MESHSTATS_VERSION,
    _cfg.ssid, _safe_mode ? 1 : 0,
    wifiStateCode(), ip.toString().c_str(),
    _state == WIFI_FALLBACK_AP ? _ap_ssid : _cfg.ssid,
    (int)WiFi.RSSI(), (unsigned long)(millis() / 60000UL), (unsigned)ESP.getFreeHeap(),
    _batt_known ? 1 : 0, (unsigned)_batt_mv, (unsigned)_batt_pct, (unsigned)_level,
    _wdt_watching ? 1 : 0, WDT_TIMEOUT_S, mcu_t,
    powerStateCode(), (unsigned)powerSecsLeft(), (unsigned)currentIntervalSecs(),
    isNight() ? 1 : 0, _cfg.pwr_mode, _cfg.pwr_window, _cfg.wifi_sleep,
    liveCode(), _cfg.bat_live);

  // Truncation here would ship broken JSON, so clamp before appending.
  if (n < 0 || (size_t)n >= sizeof(body)) n = sizeof(body) - 1;

  snprintf(body + n, sizeof(body) - n,
    "\"mqtt\":{\"host\":\"%s\",\"port\":%u,\"user\":\"%s\",\"prefix\":\"%s\","
    "\"enabled\":%u,\"rx\":%u,\"st\":\"%s\",\"stats\":%u,\"pkt\":%u,\"drop\":%u,"
    "\"queue\":%u,\"fail\":%u,\"err\":\"%s\",\"rc\":%d}}",
    _cfg.mqtt_host, _cfg.mqtt_port, _cfg.mqtt_user, _cfg.mqtt_prefix,
    _cfg.mqtt_enabled, _cfg.mqtt_rx,
    !_cfg.mqtt_enabled ? "off" : (_mqtt.connected() ? "conn"
      : (_cfg.mqtt_host[0] ? "disc" : "unset")),
    (unsigned)_stats_count, (unsigned)_rx_count, (unsigned)_drop_count,
    (unsigned)((_rx_head - _rx_tail + MQTT_RX_QUEUE) % MQTT_RX_QUEUE),
    (unsigned)_fail_count, _mqtt_err, _mqtt_err_rc);

  req->send(200, "application/json", body);
}

// Empty password fields mean 'leave it alone'; otherwise visiting the page
// would wipe a password you cannot see.
static void copyParam(AsyncWebServerRequest *req, const char *name, char *out, size_t max) {
  if (!req->hasParam(name, true)) return;
  strncpy(out, req->getParam(name, true)->value().c_str(), max - 1);
  out[max - 1] = 0;
}

static uint16_t paramNum(AsyncWebServerRequest *req, const char *name, uint16_t fallback,
                         uint16_t lo, uint16_t hi) {
  if (!req->hasParam(name, true)) return fallback;
  long v = req->getParam(name, true)->value().toInt();
  if (v < lo || v > hi) return fallback;
  return (uint16_t)v;
}

static void handleWifiPost(AsyncWebServerRequest *req) {
  if (!requireAuth(req)) return;
  copyParam(req, "ssid", _cfg.ssid, SSID_MAX);
  if (req->hasParam("pass", true) && req->getParam("pass", true)->value().length() > 0) {
    copyParam(req, "pass", _cfg.pass, PASS_MAX);
  }
  if (req->hasParam("ap_pass", true) && req->getParam("ap_pass", true)->value().length() >= 8) {
    copyParam(req, "ap_pass", _cfg.ap_pass, PASS_MAX);
  }
  _apply_wifi = true;      // saving and reconnecting happens in loop()
  req->send(200, "application/json", "{\"ok\":1}");
}

static void handlePowerPost(AsyncWebServerRequest *req) {
  if (!requireAuth(req)) return;
  _cfg.pwr_mode = paramNum(req, "mode", _cfg.pwr_mode, 0, 1);
  _cfg.pwr_window = paramNum(req, "window", _cfg.pwr_window, 30, 3600);
  _cfg.wifi_sleep = req->hasParam("sleep", true) ? 1 : 0;
  _apply_power = true;
  req->send(200, "application/json", "{\"ok\":1}");
}

static void handleMqttPost(AsyncWebServerRequest *req) {
  if (!requireAuth(req)) return;
  copyParam(req, "host", _cfg.mqtt_host, MQTT_HOST_MAX);
  copyParam(req, "user", _cfg.mqtt_user, MQTT_USER_MAX);
  copyParam(req, "prefix", _cfg.mqtt_prefix, MQTT_PREFIX_MAX);
  if (_cfg.mqtt_prefix[0] == 0) strcpy(_cfg.mqtt_prefix, "meshcore");
  if (req->hasParam("pass", true) && req->getParam("pass", true)->value().length() > 0) {
    copyParam(req, "pass", _cfg.mqtt_pass, PASS_MAX);
  }
  _cfg.mqtt_port = paramNum(req, "port", _cfg.mqtt_port, 1, 65535);
  _cfg.mqtt_enabled = req->hasParam("enabled", true) ? 1 : 0;
  _cfg.mqtt_rx = req->hasParam("rx", true) ? 1 : 0;
  _apply_mqtt = true;
  req->send(200, "application/json", "{\"ok\":1}");
}

/* Monitor list plus the heard repeaters to pick from, in one fetch. Passwords
 * never leave the node: only whether one is set, because "no password" is a
 * meaningful setting here and the page has to be able to show which of the two
 * you chose. */
static void handleMonJson(AsyncWebServerRequest *req) {
  if (!requireAuth(req)) return;
  static char body[6500];
  char esc[MON_TRACE_LEN * 2 + 4];   // also used for trace lines, the longest
  int p = 0;

  p += snprintf(body + p, sizeof(body) - p,
                "{\"iv\":%u,\"max\":%d,\"minhex\":%d,\"mon\":[",
                _mon_interval, MAX_MONITORS, MON_MIN_HEX);

  for (int i = 0; i < _mon_count && p < (int)sizeof(body) - 400; i++) {
    jsonEsc(esc, sizeof(esc), _mon[i].name);
    long age = _mon[i].last_ok ? (long)((millis() - _mon[i].last_ok) / 1000UL) : -1;
    p += snprintf(body + p, sizeof(body) - p,
      "%s{\"k\":\"%s\",\"n\":\"%s\",\"pw\":%d,\"e\":%d,\"res\":%d,\"lr\":%u,"
      "\"polls\":%u,\"oks\":%u,\"pubs\":%u,\"st\":%u,\"tl\":%u,\"nb\":%u,\"age\":%ld}",
      i ? "," : "", _mon[i].key, esc, _mon[i].pass[0] ? 1 : 0,
      _mon[i].enabled ? 1 : 0, _mon[i].mesh_idx >= 0 ? 1 : 0,
      (unsigned)_mon[i].login_res, (unsigned)_mon[i].polls, (unsigned)_mon[i].oks,
      (unsigned)_mon[i].pubs, (unsigned)_mon[i].ok_st, (unsigned)_mon[i].ok_tl,
      (unsigned)_mon[i].ok_nb, age);
  }

  p += snprintf(body + p, sizeof(body) - p, "],\"heard\":[");

  int n_heard = _mesh ? _mesh->getNumNeighbours() : 0;
  if (n_heard > MON_HEARD_MAX) n_heard = MON_HEARD_MAX;
  int written = 0;
  for (int i = 0; i < n_heard && p < (int)sizeof(body) - 200; i++) {
    char hex[PUB_KEY_SIZE * 2 + 1], name[MON_NAME_MAX];
    uint32_t secs_ago;
    int8_t snr4;
    if (!_mesh->getNeighbourAt(i, hex, name, sizeof(name), &secs_ago, &snr4)) continue;

    // Names in neighbours[] are lost on restart; the advert cache is not.
    if (name[0] == 0) {
      uint8_t key[PUB_KEY_SIZE];
      if (mesh::Utils::fromHex(key, PUB_KEY_SIZE, hex)) {
        const char *cached = meshstats_advert_name(key, PUB_KEY_SIZE);
        if (cached) {
          strncpy(name, cached, sizeof(name) - 1);
          name[sizeof(name) - 1] = 0;
        }
      }
    }
    jsonEsc(esc, sizeof(esc), name);
    p += snprintf(body + p, sizeof(body) - p,
                  "%s{\"k\":\"%s\",\"n\":\"%s\",\"snr\":%.2f,\"age\":%u}",
                  written ? "," : "", hex, esc, snr4 / 4.0f, (unsigned)secs_ago);
    written++;
  }

  /* Then the repeaters we only know from stored adverts. After a restart
   * neighbours[] is empty, so without these the list a user picks from would
   * be blank until every node happens to advertise again. They carry no SNR --
   * we have not heard them this run, and inventing one would be a lie about
   * whether they are reachable right now. */
  uint32_t now = 0;
  {
    uint8_t h;
    if (_mesh && _mesh->getClockHour(&h)) now = _mesh->getRTCClock()->getCurrentTime();
  }
  for (int i = 0; i < _adv_count && p < (int)sizeof(body) - 200; i++) {
    if (_adv[i].type != ADV_TYPE_REPEATER) continue;

    uint8_t full[PUB_KEY_SIZE];
    char nm[MON_NAME_MAX];
    if (_mesh && _mesh->findNeighbourByPrefix(_adv[i].key, PUB_KEY_SIZE, full, nm, sizeof(nm))) {
      continue;                      // already listed above, with a live SNR
    }
    char hex[PUB_KEY_SIZE * 2 + 1];
    mesh::Utils::toHex(hex, _adv[i].key, PUB_KEY_SIZE);
    jsonEsc(esc, sizeof(esc), _adv[i].name);

    long age = (now && _adv[i].heard && now > _adv[i].heard)
               ? (long)(now - _adv[i].heard) : -1;
    p += snprintf(body + p, sizeof(body) - p,
                  "%s{\"k\":\"%s\",\"n\":\"%s\",\"age\":%ld,\"cached\":1}",
                  written ? "," : "", hex, esc, age);
    written++;
  }

  // Oldest first, so the page reads top-to-bottom like a log.
  p += snprintf(body + p, sizeof(body) - p, "],\"trace\":[");
  uint32_t first = (_mon_trace_n > MON_TRACE_LINES) ? _mon_trace_n - MON_TRACE_LINES : 0;
  written = 0;
  for (uint32_t i = first; i < _mon_trace_n && p < (int)sizeof(body) - 120; i++) {
    jsonEsc(esc, sizeof(esc), _mon_trace[i % MON_TRACE_LINES]);
    p += snprintf(body + p, sizeof(body) - p, "%s\"%s\"", written ? "," : "", esc);
    written++;
  }
  snprintf(body + p, sizeof(body) - p, "]}");

  req->send(200, "application/json", body);
}

static void handleMonPost(AsyncWebServerRequest *req) {
  if (!requireAuth(req)) return;
  if (_mon_action != MA_NONE) {          // previous change not applied yet
    req->send(200, "application/json", "{\"ok\":0,\"err\":\"busy\"}");
    return;
  }

  String act = req->hasParam("act", true) ? req->getParam("act", true)->value() : "";
  _ma_key[0] = _ma_name[0] = _ma_pass[0] = 0;
  _ma_num = 0;

  if (act == "iv") {
    long v = req->hasParam("secs", true) ? req->getParam("secs", true)->value().toInt() : 0;
    if (v < 60 || v > 65535) {
      req->send(200, "application/json", "{\"ok\":0,\"err\":\"range\"}");
      return;
    }
    _ma_num = (uint16_t)v;
    _mon_action = MA_INTERVAL;
    req->send(200, "application/json", "{\"ok\":1}");
    return;
  }
  if (act == "poll") {
    _mon_action = MA_POLL;
    req->send(200, "application/json", "{\"ok\":1}");
    return;
  }

  copyParam(req, "key", _ma_key, sizeof(_ma_key));
  /* Validated here rather than in loop(), because a mistyped key is the one
   * error worth telling the user about immediately. */
  if (!normaliseKey(_ma_key)) {
    req->send(200, "application/json", "{\"ok\":0,\"err\":\"key\"}");
    return;
  }

  if (act == "add") {
    copyParam(req, "name", _ma_name, sizeof(_ma_name));
    _mon_action = MA_ADD;
  } else if (act == "del") {
    _mon_action = MA_DEL;
  } else if (act == "pass") {
    // An empty password is a choice, not a missing field: it means 'try their
    // access list'. So no length check here.
    copyParam(req, "pass", _ma_pass, sizeof(_ma_pass));
    _mon_action = MA_PASS;
  } else if (act == "en") {
    _ma_num = req->hasParam("on", true) ? 1 : 0;
    _mon_action = MA_ENABLE;
  } else {
    req->send(200, "application/json", "{\"ok\":0,\"err\":\"act\"}");
    return;
  }
  req->send(200, "application/json", "{\"ok\":1}");
}

// ------------------------------------------------------------ backup/restore

/* Format, deliberately line-based so we never have to hold a whole file in
 * memory:
 *
 *   MESHSTATS-BACKUP 1
 *   FILE /identity 64
 *   <hex, 64 bytes per line>
 *   ...
 *   END
 *
 * This contains everything in the file system: your key pair, the repeater
 * prefs, the ACL and the network settings. Keep such a backup safe -- whoever
 * has it, has your node's identity.
 */
#define BACKUP_FILE   "/backup.mcb"
#define RESTORE_FILE  "/restore.mcb"
#define HEX_PER_LINE  64

static bool skipInBackup(const char *name) {
  return strcmp(name, BACKUP_FILE) == 0 || strcmp(name, RESTORE_FILE) == 0 ||
         strcmp(name, MSNET_BOOT_FILE) == 0;
}

static bool writeBackupFile() {
  if (!_fs) return false;
  File out = _fs->open(BACKUP_FILE, "w");
  if (!out) return false;

  out.print("MESHSTATS-BACKUP 1\n");

  File dir = _fs->open("/");
  File f = dir.openNextFile();
  uint8_t buf[HEX_PER_LINE];
  char hex[HEX_PER_LINE * 2 + 2];

  while (f) {
    // f.name() returns with or without a leading slash depending on core version
    String path = f.name();
    if (!path.startsWith("/")) path = "/" + path;

    if (!f.isDirectory() && !skipInBackup(path.c_str())) {
      out.printf("FILE %s %u\n", path.c_str(), (unsigned)f.size());
      int n;
      while ((n = f.read(buf, sizeof(buf))) > 0) {
        int p = 0;
        for (int i = 0; i < n; i++) {
          hex[p++] = HEXCHARS[buf[i] >> 4];
          hex[p++] = HEXCHARS[buf[i] & 0x0F];
        }
        hex[p++] = '\n';
        hex[p] = 0;
        out.print(hex);
      }
    }
    f = dir.openNextFile();
  }
  out.print("END\n");
  out.close();
  return true;
}

static void handleBackup(AsyncWebServerRequest *req) {
  if (!requireAuth(req)) return;
  if (!writeBackupFile()) {
    req->send(500, "text/plain", "backup mislukt");
    return;
  }
  char fname[64];
  snprintf(fname, sizeof(fname), "meshcore-%s.mcb", _node_hex);

  AsyncWebServerResponse *res = req->beginResponse(*_fs, BACKUP_FILE, "application/octet-stream");
  res->addHeader("Content-Disposition", String("attachment; filename=\"") + fname + "\"");
  req->send(res);
}

static uint8_t hexVal(char c) {
  if (c >= '0' && c <= '9') return c - '0';
  if (c >= 'a' && c <= 'f') return c - 'a' + 10;
  if (c >= 'A' && c <= 'F') return c - 'A' + 10;
  return 0xFF;
}

/* Reads the uploaded file back line by line and writes the files out. Only
 * once everything succeeded do we restart; if anything goes wrong the node is
 * still exactly as it was. */
static bool applyRestore(char *err, size_t err_max) {
  File in = _fs->open(RESTORE_FILE, "r");
  if (!in) { snprintf(err, err_max, "geen bestand ontvangen"); return false; }

  char line[HEX_PER_LINE * 2 + 8];
  size_t len = in.readBytesUntil('\n', line, sizeof(line) - 1);
  line[len] = 0;
  if (strncmp(line, "MESHSTATS-BACKUP", 16) != 0) {
    in.close();
    snprintf(err, err_max, "dit is geen backupbestand");
    return false;
  }

  File out;
  uint32_t remaining = 0;
  int files = 0;
  uint8_t buf[HEX_PER_LINE];

  while (in.available()) {
    len = in.readBytesUntil('\n', line, sizeof(line) - 1);
    line[len] = 0;
    if (len && line[len - 1] == '\r') line[--len] = 0;
    if (len == 0) continue;

    if (strncmp(line, "FILE ", 5) == 0) {
      if (out) out.close();
      char *sp = strrchr(line + 5, ' ');
      if (!sp) { in.close(); snprintf(err, err_max, "onleesbare regel"); return false; }
      *sp = 0;
      remaining = (uint32_t)atol(sp + 1);
      out = _fs->open(line + 5, "w");
      if (!out) { in.close(); snprintf(err, err_max, "kan %s niet schrijven", line + 5); return false; }
      files++;
    } else if (strncmp(line, "END", 3) == 0) {
      break;
    } else if (out) {
      size_t n = len / 2;
      if (n > sizeof(buf)) n = sizeof(buf);
      for (size_t i = 0; i < n; i++) {
        uint8_t hi = hexVal(line[i * 2]), lo = hexVal(line[i * 2 + 1]);
        if (hi == 0xFF || lo == 0xFF) { in.close(); out.close();
          snprintf(err, err_max, "beschadigde inhoud"); return false; }
        buf[i] = (hi << 4) | lo;
      }
      if (n > remaining) n = remaining;
      out.write(buf, n);
      remaining -= n;
    }
  }
  if (out) out.close();
  in.close();
  _fs->remove(RESTORE_FILE);

  if (files == 0) { snprintf(err, err_max, "backup bevatte geen bestanden"); return false; }
  snprintf(err, err_max, "%d bestanden teruggezet", files);
  return true;
}

static volatile bool _reboot_pending = false;
static unsigned long _reboot_at = 0;

// ------------------------------------------------------------------- console

static void consolePrompt() {
  if (_con_state == CON_USER) _client.print("gebruiker: ");
  else if (_con_state == CON_PASS) _client.print("wachtwoord: ");
  else _client.print("> ");
}

static void consoleWelcome() {
  _client.printf("\r\nMeshCore repeater %s (%s)\r\n",
                 _mesh ? _mesh->getNodeName() : "", board.getManufacturerName());
  _client.print("Log in om de CLI te gebruiken.\r\n");
  _con_state = CON_USER;
  _con_len = 0;
  _con_tries = 0;
  _con_active = millis();
  consolePrompt();
}

static void consoleHandleLine() {
  _con_line[_con_len] = 0;

  if (_con_state == CON_USER) {
    _con_state = (strcmp(_con_line, _cfg.user) == 0) ? CON_PASS : CON_USER;
    if (_con_state == CON_USER) _client.print("onbekende gebruiker\r\n");
  } else if (_con_state == CON_PASS) {
    if (strcmp(_con_line, _cfg.console_pass) == 0) {
      _con_state = CON_READY;
      _client.print("\r\nWelkom. 'help' voor de MeshCore-commando's, "
                    "'wifi' voor de netwerkinstellingen, 'quit' om af te sluiten.\r\n");
    } else {
      _con_state = CON_USER;
      if (++_con_tries >= 3) {
        _client.print("te veel pogingen\r\n");
        _client.stop();
        _con_len = 0;
        return;
      }
      _client.print("onjuist wachtwoord\r\n");
    }
  } else if (_con_line[0]) {
    if (strcmp(_con_line, "quit") == 0 || strcmp(_con_line, "exit") == 0) {
      _client.print("tot ziens\r\n");
      _client.stop();
      _con_len = 0;
      return;
    }
    char reply[160];
    reply[0] = 0;
    if (!msnet_handle_command(_con_line, reply) && _mesh) {
      _mesh->handleCommand(0, _con_line, reply);
    }
    if (reply[0]) { _client.print(reply); _client.print("\r\n"); }
  }

  _con_len = 0;
  consolePrompt();
}

static void consoleLoop() {
  // Clean up a silent session ourselves, even if the far end never closed it.
  if (_client && _client.connected() && millis() - _con_active > CON_IDLE_MS) {
    _client.stop();
  }

  if (_console.hasClient()) {
    WiFiClient fresh = _console.available();
    bool busy = _client && _client.connected() &&
                (millis() - _con_active < CON_TAKEOVER_MS);
    if (busy) {
      fresh.print("Er is al een sessie actief.\r\n");
      fresh.stop();
    } else {
      if (_client) _client.stop();      // let go of any stale session
      _client = fresh;
      consoleWelcome();
    }
  }
  if (!_client || !_client.connected()) return;

  while (_client.available()) {
    char c = _client.read();
    _con_active = millis();
    if (c == '\n' || c == '\r') {
      if (_con_len > 0 || c == '\r') consoleHandleLine();
    } else if (c >= 32 && _con_len < sizeof(_con_line) - 1) {
      _con_line[_con_len++] = c;
    }
  }
}

// -------------------------------------------------------------- CLI commands

/* Returns the value after 'key' (possibly empty), or NULL when arg does not
 * start with that keyword. Empty is meaningful: 'wifi mqtt host' clears the
 * broker. */
static const char *subArg(const char *arg, const char *key) {
  size_t n = strlen(key);
  if (strncmp(arg, key, n) != 0) return NULL;
  const char *p = arg + n;
  if (*p != 0 && *p != ' ') return NULL;
  while (*p == ' ') p++;
  return p;
}

static bool isOn(const char *v) {
  return strcmp(v, "on") == 0 || strcmp(v, "aan") == 0 || strcmp(v, "1") == 0;
}

/* Every power setting in one table: adding a knob here makes it settable over
 * the mesh without another command branch, which matters for a node you can
 * only reach over the air. */
struct Tunable { const char *name; uint16_t *value; uint16_t lo, hi; };
static const Tunable TUNABLES[] = {
  { "mode",         &_cfg.pwr_mode,     0, 1 },
  { "window",       &_cfg.pwr_window,  30, 3600 },
  { "sleep",        &_cfg.wifi_sleep,   0, 1 },
  { "txpower",      &_cfg.tx_power,     0, 20 },
  { "full",         &_cfg.bat_full,     0, 100 },
  { "high",         &_cfg.bat_high,     0, 100 },
  { "norm",         &_cfg.bat_norm,     0, 100 },
  { "crit",         &_cfg.bat_crit,     0, 100 },
  { "hyst",         &_cfg.bat_hyst,     0, 20 },
  { "live",         &_cfg.bat_live,     0, 100 },
  { "hold",         &_cfg.full_hold,    0, 1440 },
  { "iv_full",      &_cfg.iv_full,     30, 65535 },
  { "iv_high",      &_cfg.iv_high,     30, 65535 },
  { "iv_norm",      &_cfg.iv_norm,     30, 65535 },
  { "iv_low",       &_cfg.iv_low,      30, 65535 },
  { "iv_crit",      &_cfg.iv_crit,     30, 65535 },
  { "night_from",   &_cfg.night_from,   0, 24 },
  { "night_to",     &_cfg.night_to,     0, 24 },
  { "night_factor", &_cfg.night_factor, 1, 64 },
};

static void handlePowerCommand(const char *arg, char *reply) {
  const char *v;

  if (*arg == 0) {
    char pwr[96];
    powerSummaryNl(pwr, sizeof(pwr));
    if (_batt_known) {
      snprintf(reply, 155, "%s; accu %u%% (%s), venster %us", pwr,
               (unsigned)_batt_pct, LEVEL_NL[_level], (unsigned)_cfg.pwr_window);
    } else {
      snprintf(reply, 155, "%s; accu onbekend, venster %us", pwr, (unsigned)_cfg.pwr_window);
    }
    return;
  }
  if (strcmp(arg, "altijd") == 0 || strcmp(arg, "always") == 0) {
    _cfg.pwr_mode = PWR_ALWAYS;
    saveConfig();
    if (_asleep) wifiWake();
    strcpy(reply, "OK - altijd bereikbaar");
    return;
  }
  if (strcmp(arg, "zuinig") == 0 || strcmp(arg, "save") == 0) {
    _cfg.pwr_mode = PWR_SAVE;
    _awake_until = millis() + (unsigned long)_cfg.pwr_window * 1000UL;
    saveConfig();
    snprintf(reply, 155, "OK - zuinig, nog %us bereikbaar", (unsigned)_cfg.pwr_window);
    return;
  }
  if ((v = subArg(arg, "set")) != NULL) {
    char name[16];
    unsigned val;
    if (sscanf(v, "%15s %u", name, &val) == 2) {
      for (unsigned i = 0; i < sizeof(TUNABLES) / sizeof(TUNABLES[0]); i++) {
        if (strcmp(name, TUNABLES[i].name) != 0) continue;
        if (val < TUNABLES[i].lo || val > TUNABLES[i].hi) {
          snprintf(reply, 155, "Err - %s moet %u..%u zijn", name,
                   (unsigned)TUNABLES[i].lo, (unsigned)TUNABLES[i].hi);
          return;
        }
        *TUNABLES[i].value = (uint16_t)val;
        saveConfig();
        snprintf(reply, 155, "OK - %s=%u, nu elke %us", name, val,
                 (unsigned)currentIntervalSecs());
        return;
      }
    }
    strcpy(reply, "Err - namen: mode window sleep txpower full high norm crit hyst live hold "
                  "iv_full iv_high iv_norm iv_low iv_crit night_from night_to night_factor");
    return;
  }
  strcpy(reply, "Err - wifi power [altijd|zuinig|set <naam> <waarde>]");
}

static const char *LOGIN_NL[] = { "nooit geprobeerd", "gelukt", "geen antwoord" };

static void handleMonCommand(const char *arg, char *reply) {
  const char *v;

  if (*arg == 0) {
    int resolved = 0, ok = 0;
    for (int i = 0; i < _mon_count; i++) {
      if (_mon[i].mesh_idx >= 0) resolved++;
      if (_mon[i].login_res == LOGIN_OK) ok++;
    }
    snprintf(reply, 155, "%d gemonitord (%d bruikbaar, %d ingelogd), elke %us, max %d",
             _mon_count, resolved, ok, (unsigned)_mon_interval, MAX_MONITORS);
    return;
  }
  if (_mon_action != MA_NONE) { strcpy(reply, "Err - vorige wijziging nog bezig"); return; }

  if ((v = subArg(arg, "list")) != NULL) {
    int n = (*v) ? atoi(v) : 0;           // optional index; default the first
    if (n < 0 || n >= _mon_count) { strcpy(reply, "Err - geen regel met dat nummer"); return; }
    MonEntry &m = _mon[n];
    snprintf(reply, 155, "%d: %.12s %.14s %s, %s, login %s, %up/%ug/%uv, st%u tl%u nb%u",
             n, m.key, m.name[0] ? m.name : "-",
             m.enabled ? "aan" : "uit",
             m.mesh_idx >= 0 ? "bruikbaar" : "wacht op advert",
             LOGIN_NL[m.login_res < 3 ? m.login_res : 0],
             (unsigned)m.polls, (unsigned)m.oks, (unsigned)m.pubs,
             (unsigned)m.ok_st, (unsigned)m.ok_tl, (unsigned)m.ok_nb);
    return;
  }
  if ((v = subArg(arg, "add")) != NULL) {
    char key[MON_KEY_HEX_MAX], name[MON_NAME_MAX];
    name[0] = 0;
    if (sscanf(v, "%64s %23s", key, name) < 1) { strcpy(reply, "Err - gebruik: wifi mon add <hex> [naam]"); return; }
    strncpy(_ma_key, key, sizeof(_ma_key) - 1); _ma_key[sizeof(_ma_key) - 1] = 0;
    if (!normaliseKey(_ma_key)) {
      snprintf(reply, 155, "Err - sleutel moet hex zijn, minstens %d tekens", MON_MIN_HEX);
      return;
    }
    strncpy(_ma_name, name, sizeof(_ma_name) - 1); _ma_name[sizeof(_ma_name) - 1] = 0;
    _mon_action = MA_ADD;
    snprintf(reply, 155, "OK - %.16s toegevoegd", _ma_key);
    return;
  }
  if ((v = subArg(arg, "del")) != NULL) {
    strncpy(_ma_key, v, sizeof(_ma_key) - 1); _ma_key[sizeof(_ma_key) - 1] = 0;
    if (!normaliseKey(_ma_key)) { strcpy(reply, "Err - ongeldige sleutel"); return; }
    _mon_action = MA_DEL;
    strcpy(reply, "OK - verwijderd");
    return;
  }
  if ((v = subArg(arg, "pass")) != NULL) {
    char key[MON_KEY_HEX_MAX], pw[MON_PASS_MAX];
    pw[0] = 0;
    int got = sscanf(v, "%64s %15s", key, pw);
    if (got < 1) { strcpy(reply, "Err - gebruik: wifi mon pass <hex> [woord]"); return; }
    strncpy(_ma_key, key, sizeof(_ma_key) - 1); _ma_key[sizeof(_ma_key) - 1] = 0;
    if (!normaliseKey(_ma_key)) { strcpy(reply, "Err - ongeldige sleutel"); return; }
    strncpy(_ma_pass, pw, sizeof(_ma_pass) - 1); _ma_pass[sizeof(_ma_pass) - 1] = 0;
    _mon_action = MA_PASS;
    // No password is a real choice: the far side then checks its access list.
    strcpy(reply, pw[0] ? "OK - wachtwoord ingesteld" : "OK - leeg, via hun access list");
    return;
  }
  if ((v = subArg(arg, "on")) != NULL || (v = subArg(arg, "off")) != NULL) {
    bool on = (arg[1] == 'n');
    strncpy(_ma_key, v, sizeof(_ma_key) - 1); _ma_key[sizeof(_ma_key) - 1] = 0;
    if (!normaliseKey(_ma_key)) { strcpy(reply, "Err - ongeldige sleutel"); return; }
    _ma_num = on ? 1 : 0;
    _mon_action = MA_ENABLE;
    strcpy(reply, on ? "OK - monitoren aan" : "OK - monitoren uit");
    return;
  }
  if ((v = subArg(arg, "iv")) != NULL) {
    long secs = atol(v);
    if (secs < 60 || secs > 65535) { strcpy(reply, "Err - interval 60..65535 s"); return; }
    _ma_num = (uint16_t)secs;
    _mon_action = MA_INTERVAL;
    snprintf(reply, 155, "OK - elke %ld s", secs);
    return;
  }
  if (strcmp(arg, "poll") == 0) {
    _mon_action = MA_POLL;
    strcpy(reply, "OK - ronde gestart");
    return;
  }
  if ((v = subArg(arg, "trace")) != NULL) {
    /* One line per call: a CLI reply is 160 bytes, and this has to be readable
     * over the mesh from wherever you happen to be. 0 = newest. */
    if (_mon_trace_n == 0) { strcpy(reply, "geen trace"); return; }
    uint32_t back = (*v) ? (uint32_t)atol(v) : 0;
    uint32_t avail = (_mon_trace_n < MON_TRACE_LINES) ? _mon_trace_n : MON_TRACE_LINES;
    if (back >= avail) { snprintf(reply, 155, "Err - 0..%u", (unsigned)(avail - 1)); return; }
    snprintf(reply, 155, "[%u] %s", (unsigned)back,
             _mon_trace[(_mon_trace_n - 1 - back) % MON_TRACE_LINES]);
    return;
  }
  strcpy(reply, "Err - wifi mon [list <n>|add <hex> [naam]|del <hex>|pass <hex> [woord]|"
                "on <hex>|off <hex>|iv <s>|poll|trace <n>]");
}

static void handleMqttCommand(const char *arg, char *reply) {
  const char *v;

  if (*arg == 0) {
    snprintf(reply, 155, "%s, broker=%.40s:%u, prefix=%.16s, rx=%s, stats=%u pkt=%u drop=%u",
             _cfg.mqtt_enabled ? (_mqtt.connected() ? "verbonden" : "niet verbonden") : "uit",
             _cfg.mqtt_host[0] ? _cfg.mqtt_host : "-", (unsigned)_cfg.mqtt_port,
             _cfg.mqtt_prefix, _cfg.mqtt_rx ? "aan" : "uit",
             (unsigned)_stats_count, (unsigned)_rx_count, (unsigned)_drop_count);
    return;
  }
  if ((v = subArg(arg, "host")) != NULL) {
    strncpy(_cfg.mqtt_host, v, MQTT_HOST_MAX - 1);
    _cfg.mqtt_host[MQTT_HOST_MAX - 1] = 0;
    _apply_mqtt = true;
    snprintf(reply, 155, "OK - broker=%.60s", _cfg.mqtt_host);
  } else if ((v = subArg(arg, "port")) != NULL) {
    long p = atol(v);
    if (p < 1 || p > 65535) { strcpy(reply, "Err - poort 1..65535"); return; }
    _cfg.mqtt_port = (uint16_t)p;
    _apply_mqtt = true;
    snprintf(reply, 155, "OK - poort=%u", (unsigned)_cfg.mqtt_port);
  } else if ((v = subArg(arg, "user")) != NULL) {
    strncpy(_cfg.mqtt_user, v, MQTT_USER_MAX - 1);
    _cfg.mqtt_user[MQTT_USER_MAX - 1] = 0;
    _apply_mqtt = true;
    strcpy(reply, "OK - gebruiker opgeslagen");
  } else if ((v = subArg(arg, "pass")) != NULL) {
    strncpy(_cfg.mqtt_pass, v, PASS_MAX - 1);
    _cfg.mqtt_pass[PASS_MAX - 1] = 0;
    _apply_mqtt = true;
    strcpy(reply, "OK - wachtwoord opgeslagen");
  } else if ((v = subArg(arg, "prefix")) != NULL) {
    strncpy(_cfg.mqtt_prefix, v, MQTT_PREFIX_MAX - 1);
    _cfg.mqtt_prefix[MQTT_PREFIX_MAX - 1] = 0;
    if (_cfg.mqtt_prefix[0] == 0) strcpy(_cfg.mqtt_prefix, "meshcore");
    _apply_mqtt = true;
    snprintf(reply, 155, "OK - topics %.20s/%s/stats en /rx", _cfg.mqtt_prefix, _node_hex);
  } else if ((v = subArg(arg, "rx")) != NULL) {
    _cfg.mqtt_rx = isOn(v) ? 1 : 0;
    _apply_mqtt = true;
    snprintf(reply, 155, "OK - ruwe pakketten %s", _cfg.mqtt_rx ? "aan" : "uit");
  } else if (strcmp(arg, "on") == 0 || strcmp(arg, "aan") == 0) {
    _cfg.mqtt_enabled = 1;
    _apply_mqtt = true;
    strcpy(reply, "OK - doorsturen aan");
  } else if (strcmp(arg, "off") == 0 || strcmp(arg, "uit") == 0) {
    _cfg.mqtt_enabled = 0;
    _apply_mqtt = true;
    strcpy(reply, "OK - doorsturen uit");
  } else if (strcmp(arg, "test") == 0) {
    strcpy(reply, mqttPublishStats() ? "OK - verstuurd" : "Err - versturen faalde");
  } else {
    strcpy(reply, "Err - wifi mqtt [host|port|user|pass|prefix|rx on|off|on|off|test]");
  }
}

// -------------------------------------------------------------------- public

bool msnet_is_safe_mode() { return _safe_mode; }

bool msnet_handle_command(const char *command, char *reply) {
  if (_disabled) return false;   // leave everything to the stock firmware

  if (memcmp(command, "wifi", 4) != 0) {
    /* Both versions in one line: this module and the MeshCore release it is
     * built on. If this module ever disables itself, the answer falls through
     * to stock MeshCore -- so a missing MeshStats name is itself the
     * diagnosis. */
    if (strcmp(command, "ver") == 0) {
      snprintf(reply, 155, "%s v%s - MeshCore %s (Build: %s)",
               MESHSTATS_NAME, MESHSTATS_VERSION, FIRMWARE_VERSION, FIRMWARE_BUILD_DATE);
      return true;
    }
    /* 'start ota' hands over to the stock soft-AP updater instead of merely
     * printing the URL of our own /update page.
     *
     * It used to do the latter, on the assumption that an upload over the normal
     * network always works. It does not: uploads to /update have failed
     * repeatedly on real hardware. And because that reply replaced the stock
     * behaviour, the one fallback that did work had been taken away with it. A
     * recovery path must never depend on the thing you are recovering from.
     *
     * Both servers want port 80, so ours has to go first. After this the node
     * only serves the update page until it reboots -- which is precisely what
     * you want from a command whose whole purpose is reflashing. */
    if (memcmp(command, "start ota", 9) == 0) {
      if (_asleep) {
        strcpy(reply, "WiFi staat uit (zuinig). Eerst 'wifi on 30'.");
        return true;
      }
      _server.end();
      _console.end();
      _started = false;          // stop serving from our own loop
      if (!board.startOTAUpdate(_mesh ? _mesh->getNodeName() : "repeater", reply)) {
        strcpy(reply, "Err - OTA niet beschikbaar in deze build");
      }
      return true;
    }
    return false;
  }

  const char *arg = command + 4;
  while (*arg == ' ') arg++;
  const char *v;

  if (*arg == 0) {
    IPAddress ip = (_state == WIFI_FALLBACK_AP) ? WiFi.softAPIP() : WiFi.localIP();
    char batt[24];
    if (_batt_known) snprintf(batt, sizeof(batt), "%u%% (%s)", (unsigned)_batt_pct, LEVEL_NL[_level]);
    else strcpy(batt, "onbekend");
    snprintf(reply, 155, "%s, ssid=%.20s, ip=%s, rssi=%d, accu=%s, elke %us",
             stateNameNl(), _state == WIFI_FALLBACK_AP ? _ap_ssid : _cfg.ssid,
             ip.toString().c_str(), (int)WiFi.RSSI(), batt,
             (unsigned)currentIntervalSecs());
  } else if ((v = subArg(arg, "ssid")) != NULL) {
    strncpy(_cfg.ssid, v, SSID_MAX - 1);
    _cfg.ssid[SSID_MAX - 1] = 0;
    saveConfig();
    snprintf(reply, 155, "OK - ssid=%s ('wifi connect' om te verbinden)", _cfg.ssid);
  } else if ((v = subArg(arg, "pass")) != NULL) {
    strncpy(_cfg.pass, v, PASS_MAX - 1);
    _cfg.pass[PASS_MAX - 1] = 0;
    saveConfig();
    strcpy(reply, "OK - wachtwoord opgeslagen ('wifi connect' om te verbinden)");
  } else if (memcmp(arg, "connect", 7) == 0) {
    _state = WIFI_TRYING;
    startSTA();
    strcpy(reply, "OK - verbinden...");
  } else if (memcmp(arg, "ap", 2) == 0) {
    startAP();
    sprintf(reply, "OK - eigen netwerk '%s' actief", _ap_ssid);
  } else if ((v = subArg(arg, "on")) != NULL) {
    /* The way back in when the node is asleep: force WiFi up regardless of
     * mode or battery, and hold it there long enough to actually do something.
     * Deliberately not limited by the battery rules -- being locked out of a
     * node on a roof costs more than the charge does. */
    unsigned mins = (*v) ? (unsigned)atol(v) : FORCE_DEFAULT_MIN;
    if (mins == 0 || mins > 720) mins = FORCE_DEFAULT_MIN;
    _force_until = millis() + (unsigned long)mins * 60000UL;
    if (_asleep) wifiWake();
    snprintf(reply, 155, "OK - wifi %u min geforceerd aan", mins);
  } else if (memcmp(arg, "off", 3) == 0) {
    _force_until = 0;
    if (_cfg.pwr_mode == PWR_SAVE && !_safe_mode) {
      _awake_until = millis();      // powerLoop puts it to sleep this pass
      strcpy(reply, "OK - terug naar zuinig beheer");
    } else {
      strcpy(reply, "OK - terug naar automatisch beheer (modus: altijd bereikbaar)");
    }
  } else if ((v = subArg(arg, "mqtt")) != NULL) {
    handleMqttCommand(v, reply);
  } else if ((v = subArg(arg, "mon")) != NULL) {
    handleMonCommand(v, reply);
  } else if ((v = subArg(arg, "power")) != NULL) {
    handlePowerCommand(v, reply);
  } else if ((v = subArg(arg, "console")) != NULL) {
    char u[USER_MAX], p[PASS_MAX];
    if (sscanf(v, "%16s %64s", u, p) == 2) {
      strcpy(_cfg.user, u);
      strcpy(_cfg.console_pass, p);
      saveConfig();
      strcpy(reply, "OK - console-login gewijzigd");
    } else {
      strcpy(reply, "Err - gebruik: wifi console <gebruiker> <wachtwoord>");
    }
  } else if (memcmp(arg, "wdt", 3) == 0) {
    /* Bewijst de hele keten hang -> watchdog -> herstart -> bootteller, zonder
     * het risico ervan. Een oneindige lus zou de node onherroepelijk ophangen
     * als de watchdog niet blijkt te werken, en die hangt op een dak. Dit
     * blokkeert begrensd: slaat de watchdog toe, dan herstart de node halverwege
     * (bewezen); slaat hij niet toe, dan komt de node gewoon terug en weten we
     * dat het net niet gespannen staat -- zonder schade. */
    unsigned long einde = millis() + (WDT_TIMEOUT_S + 10) * 1000UL;
    while (!passed(einde)) { }      // bewust niets aankloppen
    strcpy(reply, "Watchdog sloeg NIET toe - het vangnet werkt niet");
  } else {
    strcpy(reply, "Err - wifi [ssid|pass|connect|ap|on <min>|off|console|mqtt|power|mon|wdt]");
  }
  return true;
}

void msnet_begin(FS &fs, MyMesh *mesh) {
  _fs = &fs;
  _mesh = mesh;

  checkSafeMode();

  /* Before the _disabled return on purpose: a node that has switched this
   * module off is exactly the one that must still be able to reboot itself out
   * of a hang. */
  wdtBegin();

  if (_disabled) {
    // Even safe mode did not hold. Everything of ours stays off; what remains
    // is a plain MeshCore repeater, with mesh CLI and 'start ota'.
    Serial.println("MeshStatsNet: uitgeschakeld na herhaalde herstarts");
    _started = true;      // only so the boot counter can still be cleared
    return;
  }

  loadConfig();
  advLoad();          // before the monitors: they borrow names from it
  loadMonitors();
  syncMonitorsToMesh();

  if (_mesh) mesh::Utils::toHex(_node_hex, _mesh->self_id.pub_key, 6);
  snprintf(_ap_ssid, sizeof(_ap_ssid), "MeshCore-%s", _node_hex);

  /* A raw packet becomes over 500 characters in hex, and the neighbour payload
   * grows with the number of neighbours; the default 256-byte buffer is far too
   * small and publish() would silently refuse. */
  _mqtt.setBufferSize(MQTT_PUB_MAX);
  _mqtt.setSocketTimeout(4);
  _mqtt.setKeepAlive(60);
  _mqtt.setServer(_cfg.mqtt_host, _cfg.mqtt_port);

  updatePowerLevel();

  if (_safe_mode) {
    // Something made this node restart repeatedly. Only its own network and
    // the admin page, so you can get in and put it right.
    Serial.println("MeshStatsNet: VEILIGE MODUS na herhaalde herstarts");
    startAP();
  } else {
    startSTA();
    /* A fresh start in power-save mode still gets a full window: after a power
     * cut you want a chance to reach the node before it goes quiet. */
    _awake_until = millis() + (unsigned long)_cfg.pwr_window * 1000UL;
  }

  _server.on("/", HTTP_GET, [](AsyncWebServerRequest *req) {
    // send_P streams straight from flash; send() would first copy all 14 kB
    // into a heap String, on a node that also has to keep a mesh running.
    req->send_P(200, "text/html; charset=utf-8", PAGE);
  });
  _server.on("/api/status", HTTP_GET, handleStatus);
  _server.on("/api/wifi", HTTP_POST, handleWifiPost);
  _server.on("/api/power", HTTP_POST, handlePowerPost);
  _server.on("/api/mqtt", HTTP_POST, handleMqttPost);
  _server.on("/api/mon", HTTP_GET, handleMonJson);
  _server.on("/api/mon", HTTP_POST, handleMonPost);
  _server.on("/api/backup", HTTP_GET, handleBackup);

  _server.on("/api/restore", HTTP_POST,
    [](AsyncWebServerRequest *req) {                       // upload is in
      char msg[80];
      bool ok = applyRestore(msg, sizeof(msg));
      char body[128];
      snprintf(body, sizeof(body), "{\"ok\":%d,\"msg\":\"%s\"}", ok ? 1 : 0, msg);
      req->send(ok ? 200 : 400, "application/json", body);
      if (ok) {                                            // restart with the restored data
        _reboot_pending = true;
        _reboot_at = millis() + 1500;
      }
    },
    [](AsyncWebServerRequest *req, const String &filename, size_t index,
       uint8_t *data, size_t len, bool final) {            // write it out piece by piece
      static File up;
      if (index == 0) {
        if (!requireAuth(req)) return;
        up = _fs->open(RESTORE_FILE, "w");
      }
      if (up) up.write(data, len);
      if (final && up) up.close();
    });

  // The firmware upload behind the same login too: whoever gets in here can
  // write firmware and download your keys.
  AsyncElegantOTA.begin(&_server, _cfg.user, _cfg.console_pass);
  _server.begin();
  _console.begin();
  _console.setNoDelay(true);

  _started = true;
  _mqtt_last_push = millis();
}

void msnet_loop() {
  if (!_started) return;

  /* First thing every pass, and before any early return below: reaching this
   * line is the proof that loop() is still turning. */
  wdtFeed();

  // Up long enough: this firmware works, so the boot counter may go back to
  // zero. Also while disabled, so the next start tries everything again.
  if (!_boot_cleared && millis() > STABLE_UPTIME_MS) clearBootCount();
  if (_disabled) return;

  // After a restore, wait a moment so the response still reaches the browser.
  if (_reboot_pending && millis() > _reboot_at) ESP.restart();

  // Advert cache: one write once the burst has settled, not one per advert.
  if (_adv_dirty_at != 0 && passed(_adv_dirty_at)) advSave();

  if (_apply_wifi) {
    _apply_wifi = false;
    saveConfig();
    _state = WIFI_TRYING;
    startSTA();
  }
  if (_apply_mqtt) {
    _apply_mqtt = false;
    saveConfig();
    _mqtt.disconnect();          // reconnect with the new settings
    _mqtt_last_try = 0;
    _mqtt.setServer(_cfg.mqtt_host, _cfg.mqtt_port);
  }
  if (_apply_power) {
    _apply_power = false;
    saveConfig();
    if (!_asleep) applyRadioTuning();
    if (_cfg.pwr_mode == PWR_SAVE) {
      // Give whoever just pressed Save the full window to keep working.
      _awake_until = millis() + (unsigned long)_cfg.pwr_window * 1000UL;
    }
  }

  powerLoop();
  if (_asleep) return;      // radio off: nothing below has anything to do

  bool up = (WiFi.status() == WL_CONNECTED);

  switch (_state) {
    case WIFI_TRYING:
      if (up) {
        _state = WIFI_OK;
        _state_since = millis();
        applyRadioTuning();
        Serial.printf("MeshStatsNet: verbonden, http://%s/\n",
                      WiFi.localIP().toString().c_str());
      } else if (millis() - _state_since > STA_TIMEOUT_MS) {
        /* In power-save mode, raising an AP nobody is waiting for is the most
         * expensive thing we could do, so we go back to sleep and try again
         * next round. Unless the window was forced -- then someone is standing
         * next to it looking for a network. */
        if (_cfg.pwr_mode == PWR_SAVE && !_safe_mode && !isForced()) {
          wifiSleep();
          return;
        }
        startAP();
      }
      break;

    case WIFI_OK:
      if (!up) {                       // lost the connection: try again
        _state = WIFI_TRYING;
        _state_since = millis();
        startSTA();
      }
      break;

    case WIFI_FALLBACK_AP:
      if (up) {                        // the network is back
        _state = WIFI_OK;
        _state_since = millis();
        WiFi.softAPdisconnect(true);
        WiFi.mode(WIFI_STA);
        applyRadioTuning();
        Serial.printf("MeshStatsNet: netwerk terug, http://%s/\n",
                      WiFi.localIP().toString().c_str());
      } else if (millis() - _last_retry > STA_RETRY_MS) {
        _last_retry = millis();
        startSTA();                    // the AP stays up meanwhile
      }
      break;
  }

  if (!_safe_mode) {
    consoleLoop();
    mqttLoop();
    /* Deliberately below the _asleep return above: polling happens over the
     * mesh, but the answers go out over MQTT. Collecting stats we cannot
     * publish would spend other people's airtime for nothing. */
    applyMonAction();
    monitorLoop();
  }
}
