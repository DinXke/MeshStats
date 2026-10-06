// De tracker: wanneer GPS aan, wanneer zenden, wanneer slapen.
//
//   SLEEP ──(beweging)──> ACQUIRE(wakker) ──(fix)──> zend M ──> MOVING
//   ACQUIRE ──(fix_timeout)──> zend N ──> MOVING (blijft zoeken)
//   MOVING ──(regels, MtRules)──> zend M
//   MOVING ──(geen beweging >= still_timeout)──> zend S ──> SLEEP
//   SLEEP ──(heartbeat)──> ACQUIRE(heartbeat, korte timeout) ──> zend H of N ──> SLEEP
//   enkele klik ──> fix (max fix_timeout_hb) ──> zend P of N, met biep-terugmelding
//
// In trackermodus slaapt ook de radio zodra er niets te verzenden of te
// ontvangen valt (na het ACK-venster); de mesh-lus draait dan niet. In
// companionmodus blijft de radio altijd luisteren (stock gedrag).
#include "MtTracker.h"
#include "MeshTrack.h"
#include "MtConfig.h"
#include "MtRules.h"
#include "MtSender.h"
#include "MtMotion.h"
#include "MtBattery.h"
#include "MtGps.h"
#include "MyMesh.h"
#include "UITask.h"
#include <Adafruit_LittleFS.h>
#include <InternalFileSystem.h>
using namespace Adafruit_LittleFS_Namespace;

extern UITask ui_task;

enum Purpose : uint8_t { PUR_WAKE, PUR_HEARTBEAT };

static MtTState s_state = MT_T_OFF;
static Purpose s_purpose = PUR_WAKE;
static uint32_t s_acq_deadline = 0;
static uint32_t s_last_eval_fix = 0;
static uint32_t s_still_since = 0;          // 0 = beweegt
static uint32_t s_sleep_since = 0;
static uint32_t s_last_hb_s = 0;
static MtRuleState s_rules;
static uint16_t s_seq = 0;
static uint16_t s_seq_saved = 0;
static bool s_gps_ours = false;             // wij hebben de GPS aangezet
static bool s_manual = false;
static uint32_t s_manual_deadline = 0;
static uint32_t s_manual_last = 0;
static bool s_radio_asleep = false;
static uint32_t s_quiet_since = 0;
static char s_last_reason[16] = "-";
static uint32_t s_last_tx_ms = 0;

static const char* SEQ_PATH = "/mt_seq.dat";
#define SEQ_STEP 64

static uint32_t now_s() { return millis() / 1000; }

// ---- volgnummer (overleeft een herstart, anders gooit de server dubbels weg) ---

static void seq_save(uint16_t v) {
  InternalFS.remove(SEQ_PATH);
  File f = InternalFS.open(SEQ_PATH, FILE_O_WRITE);
  if (f) { f.write((const uint8_t*)&v, 2); f.close(); }
  s_seq_saved = v;
}

static void seq_begin() {
  uint16_t v = 0;
  File f = InternalFS.open(SEQ_PATH, FILE_O_READ);
  if (f) { if (f.read((uint8_t*)&v, 2) != 2) v = 0; f.close(); }
  // Na een herstart altijd voorbij alles wat al verstuurd kan zijn.
  s_seq = (uint16_t)(v + SEQ_STEP);
  seq_save(s_seq);
}

static uint16_t seq_next() {
  uint16_t v = s_seq++;
  if ((uint16_t)(s_seq - s_seq_saved) >= SEQ_STEP) seq_save(s_seq);
  return v;
}

// ---- GPS ----------------------------------------------------------------------

static bool gps_is_on() { return strcmp(sensors.getSettingValue(0), "1") == 0; }

static void gps_want(bool on) {
  if (on && !gps_is_on()) { sensors.setSettingValue("gps", "1"); s_gps_ours = true; }
  else if (!on && s_gps_ours) { if (gps_is_on()) sensors.setSettingValue("gps", "0"); s_gps_ours = false; }
}

// ---- radio --------------------------------------------------------------------

static void radio_wake() {
  if (!s_radio_asleep) return;
  radio_driver.onSendFinished();     // standby + "start opnieuw met ontvangen"
  s_radio_asleep = false;
  s_quiet_since = millis();
}

bool mt_radio_paused() { return s_radio_asleep; }

// ---- berichten ----------------------------------------------------------------

static bool tracking_active() {
  MtMode m = mt_effective_mode();
  return m == MT_MODE_TRACKER || mt_cfg.track_in_companion;
}

static void send_report(char state, bool with_pos, bool manual, const char* reason) {
  MtNmeaProvider& g = mt_gps();
  char lat[16] = "", lon[16] = "", alt[8] = "", spd[8] = "", crs[8] = "", hd[8] = "", age[12] = "";
  double la = 0, lo = 0;
  float sp = -1, cr = -1;
  if (with_pos && g.lastValidMs() != 0) {
    la = g.getLatitude() / 1e6;
    lo = g.getLongitude() / 1e6;
    sp = g.speedKmh();
    cr = g.courseDeg();
    snprintf(lat, sizeof(lat), "%.5f", la);
    snprintf(lon, sizeof(lon), "%.5f", lo);
    snprintf(alt, sizeof(alt), "%ld", g.getAltitude() / 1000);
    snprintf(spd, sizeof(spd), "%d", (int)(sp + 0.5f));
    if (sp >= 3 && cr >= 0) snprintf(crs, sizeof(crs), "%d", ((int)(cr + 0.5f)) % 360);
    snprintf(hd, sizeof(hd), "%.1f", g.hdop());
    snprintf(age, sizeof(age), "%lu", (unsigned long)(g.fixAgeMs() / 1000));
  } else {
    with_pos = false;
  }
  int bat = mt_battery_pct(board.getBattMilliVolts());
  char text[96];
  snprintf(text, sizeof(text), "T1|%u|%c|%s|%s|%s|%s|%s|%d|%s|%s|%c",
           (unsigned)seq_next(), state, lat, lon, alt, spd, crs, bat < 0 ? 0 : bat, hd, age,
           mt_effective_mode() == MT_MODE_TRACKER ? 't' : 'c');
  radio_wake();
  mt_send(text, manual);
  s_rules.sent(now_s(), with_pos, la, lo, sp >= 3 ? cr : -1);
  strncpy(s_last_reason, reason, sizeof(s_last_reason) - 1);
  s_last_tx_ms = millis();
  mt_log("tx %s (%s)", text, reason);
}

static void on_send_done(bool ok, bool manual) {
  if (manual) ui_task.playForced(ok ? "ok:d=16,o=7,b=200:16c,16p,16c" : "nok:d=4,o=5,b=100:4c");
  mt_log("tx %s", ok ? "bevestigd (ACK)" : "MISLUKT na alle pogingen");
}

// ---- toestanden ---------------------------------------------------------------

static void enter(MtTState st) {
  s_state = st;
  switch (st) {
    case MT_T_SLEEP:
      s_sleep_since = millis();
      s_still_since = 0;
      gps_want(s_manual);
      break;
    case MT_T_ACQUIRE:
      gps_want(true);
      s_acq_deadline = millis() + 1000UL * (s_purpose == PUR_HEARTBEAT ? mt_cfg.fix_timeout_hb_s : mt_cfg.fix_timeout_s);
      break;
    case MT_T_MOVING:
      gps_want(true);
      s_still_since = 0;
      break;
    case MT_T_OFF:
      gps_want(s_manual);
      break;
  }
}

static MtRuleParams params() {
  MtRuleParams p;
  p.min_speed_kmh = mt_cfg.min_speed_kmh;
  p.min_dist_m = mt_cfg.min_dist_m;
  p.turn_min_deg = mt_cfg.turn_min_deg;
  p.turn_min_speed_kmh = mt_cfg.turn_min_speed_kmh;
  p.min_interval_s = mt_cfg.min_interval_s;
  p.max_interval_s = mt_cfg.max_interval_s;
  return p;
}

static bool moving_now(float spd) {
  if (mt_motion_available()) return millis() - mt_motion_last() < 15000;
  return spd >= MT_STILL_KMH;
}

static void step_tracking() {
  MtNmeaProvider& g = mt_gps();
  bool new_fix = g.freshFix(2500) && g.lastValidMs() != s_last_eval_fix;
  uint32_t now = millis();

  switch (s_state) {
    case MT_T_OFF:
      enter(MT_T_MOVING);
      break;

    case MT_T_SLEEP: {
      bool motion = mt_motion_available() ? (int32_t)(mt_motion_last() - s_sleep_since) > 0 : false;
      if (motion) { s_purpose = PUR_WAKE; enter(MT_T_ACQUIRE); break; }
      if (mt_cfg.heartbeat_s > 0) {
        uint32_t ref = s_last_hb_s ? s_last_hb_s : s_rules.last_tx_s;
        if (now_s() - ref >= mt_cfg.heartbeat_s) { s_purpose = PUR_HEARTBEAT; enter(MT_T_ACQUIRE); }
      }
      break;
    }

    case MT_T_ACQUIRE:
      if (new_fix) {
        s_last_eval_fix = g.lastValidMs();
        if (s_purpose == PUR_HEARTBEAT) {
          send_report('H', true, false, "heartbeat");
          s_last_hb_s = now_s();
          if (moving_now(g.speedKmh())) enter(MT_T_MOVING); else enter(MT_T_SLEEP);
        } else {
          send_report('M', true, false, "wakker");
          enter(MT_T_MOVING);
        }
      } else if ((int32_t)(now - s_acq_deadline) >= 0) {
        send_report('N', false, false, "geen fix");
        if (s_purpose == PUR_HEARTBEAT) { s_last_hb_s = now_s(); enter(MT_T_SLEEP); }
        else enter(MT_T_MOVING);
      }
      break;

    case MT_T_MOVING: {
      float spd = g.freshFix(5000) ? g.speedKmh() : 0;
      if (spd >= MT_STILL_KMH) mt_motion_touch();
      if (!moving_now(spd) && !mt_motion_available() && mt_cfg.heartbeat_s > 0 &&
          now_s() - s_rules.last_tx_s >= mt_cfg.heartbeat_s) {
        // Geen bewegingssensor: hij slaapt nooit, maar meldt in rust toch zijn heartbeat.
        send_report('H', g.freshFix(5000), false, "heartbeat");
      }
      if (moving_now(spd)) {
        s_still_since = 0;
      } else if (s_still_since == 0) {
        s_still_since = now ? now : 1;
      } else if (mt_motion_available() && now - s_still_since >= 1000UL * mt_cfg.still_timeout_s) {
        // Stil: laatste positie melden en slapen. Zonder werkende bewegings-
        // sensor nooit slapen: dan zou hij niet meer wakker worden.
        send_report('S', g.freshFix(30000), false, "stil");
        enter(MT_T_SLEEP);
        break;
      }
      if (new_fix) {
        s_last_eval_fix = g.lastValidMs();
        double la = g.getLatitude() / 1e6, lo = g.getLongitude() / 1e6;
        float cr = g.speedKmh() >= 3 ? g.courseDeg() : -1;
        MtReason r = mt_rules_decide(s_rules, params(), now_s(), la, lo, g.speedKmh(), cr);
        if (r != MT_R_NONE && moving_now(g.speedKmh())) send_report('M', true, false, mt_reason_str(r));
      }
      break;
    }
  }
}

static void step_manual() {
  if (!s_manual) return;
  MtNmeaProvider& g = mt_gps();
  if (g.freshFix(3000)) {
    s_manual = false;
    send_report('P', true, true, "knop");
  } else if ((int32_t)(millis() - s_manual_deadline) >= 0) {
    s_manual = false;
    send_report('N', false, true, "knop, geen fix");
  }
  if (!s_manual && (s_state == MT_T_SLEEP || s_state == MT_T_OFF)) gps_want(false);
}

static void step_radio() {
  bool want_sleep = mt_effective_mode() == MT_MODE_TRACKER && !mt_sender_busy() && !s_manual &&
                    !the_mesh.hasPendingWork();
  if (!want_sleep) { s_quiet_since = 0; radio_wake(); return; }
  if (s_radio_asleep) return;
  if (s_quiet_since == 0) { s_quiet_since = millis(); return; }
  // Nog even luisteren na het laatste verkeer (late ACK, padantwoord).
  if (millis() - s_quiet_since < 3000) return;
  radio_driver.powerOff();
  s_radio_asleep = true;
}

// ---- publiek --------------------------------------------------------------------

void mt_tracker_begin() {
  seq_begin();
  mt_sender_set_done_cb(on_send_done);
  mt_motion_begin(mt_cfg.accel_sens);
  if (mt_cfg.target_set) mt_sender_ensure_contact();
  s_state = MT_T_OFF;
}

void mt_tracker_loop() {
  mt_motion_loop();
  mt_sender_loop();
  if (tracking_active()) step_tracking();
  else if (s_state != MT_T_OFF) enter(MT_T_OFF);
  step_manual();
  step_radio();
}

bool mt_tracker_manual() {
  if (s_manual || millis() - s_manual_last < 10000) return false;   // max 1x per 10 s
  s_manual_last = millis();
  if (!mt_cfg.target_set) return false;
  s_manual = true;
  s_manual_deadline = millis() + 1000UL * mt_cfg.fix_timeout_hb_s;
  gps_want(true);
  radio_wake();
  step_manual();      // verse fix? dan meteen
  return true;
}

void mt_tracker_mode_changed() {
  if (!mt_cfg.target_set) return;
  send_report('B', false, false, "modus");
}

void mt_tracker_target_changed() {
  if (mt_cfg.target_set) mt_sender_ensure_contact();
}

MtTState mt_tracker_state() { return s_state; }

const char* mt_tracker_state_str() {
  switch (s_state) {
    case MT_T_SLEEP: return "slaapt";
    case MT_T_ACQUIRE: return s_purpose == PUR_HEARTBEAT ? "fix zoeken (heartbeat)" : "fix zoeken";
    case MT_T_MOVING: return "beweegt";
    default: return "uit";
  }
}

const char* mt_tracker_last_reason() { return s_last_reason; }
uint32_t mt_tracker_last_tx_ms() { return s_last_tx_ms; }
uint16_t mt_tracker_seq() { return s_seq; }
bool mt_tracker_gps_on() { return gps_is_on(); }
