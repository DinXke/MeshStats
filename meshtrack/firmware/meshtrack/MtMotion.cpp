// Bewegingsdetectie: QMA6100P op de T1000-E (driver uit MU-companion), LIS2DH op de RAK WisMesh Tag
// (0.9.7; alleen pollen, de INT-pin is niet gedocumenteerd). Zonder sensor (of na "fout") beslist de
// tracker op GPS-snelheid; dan slaapt hij nooit (anders zou hij niet meer wakker worden).
//
// Voorkeur: de any-motion-interrupt op P1.2 (de ISR zet alleen een vlag, nooit
// I2C). Lukt het instellen van die interrupt niet (read-back faalt), dan valt
// het terug op pollen: elke seconde één meting en een verschil t.o.v. de vorige.
// Na 10 mislukte I2C-lezingen schakelt de module zichzelf uit ("fout"); dan
// beslist de tracker alleen op GPS-snelheid. Een hangende I2C-bus mag de lus
// nooit bevriezen (les uit MU-companion v1.0.1).
#include "MtMotion.h"
#include <math.h>
#if defined(T1000_E)
  #include "QMA6100P.h"
  static QMA6100P accel;
  #define MT_ACCEL_INT_PIN QMA_6100P_INT_PIN
#elif defined(RAK_WISMESH_TAG)
  #include "LIS2DH.h"
  static MtLIS2DH accel;                    // geen interrupt: pollen
#else
  // Bord zonder bewegingssensor (RAK3401): altijd "afwezig"; de tracker gebruikt rust via GPS.
  struct MtNoAccel {
    bool begin() { return false; }
    bool configMotionInterrupt(uint8_t) { return false; }
    bool read(float&, float&, float&) { return false; }
  };
  static MtNoAccel accel;
#endif
static volatile bool s_irq = false;
static MtMotionMode s_mode = MT_MOTION_NONE;
static uint32_t s_last_motion = 0;
static uint32_t s_next_poll = 0;
static uint8_t s_fail = 0;
static float s_px = 0, s_py = 0, s_pz = 0;
static bool s_have_prev = false;
static float s_poll_th = 0.08f;

static void isr() { s_irq = true; }

static uint8_t threshold_for(uint8_t sens) {
  // registertellingen: lager = gevoeliger
  return sens == 0 ? 0x14 : sens == 2 ? 0x05 : 0x0A;
}

void mt_motion_begin(uint8_t sens) {
  s_last_motion = millis();
  s_poll_th = sens == 0 ? 0.15f : sens == 2 ? 0.04f : 0.08f;
  if (!accel.begin()) { s_mode = MT_MOTION_NONE; return; }
#ifdef MT_ACCEL_INT_PIN
  if (accel.configMotionInterrupt(threshold_for(sens))) {
    pinMode(MT_ACCEL_INT_PIN, INPUT);
    attachInterrupt(digitalPinToInterrupt(MT_ACCEL_INT_PIN), isr, RISING);
    s_mode = MT_MOTION_INT;
    return;
  }
#else
  (void)isr;
#endif
  s_mode = MT_MOTION_POLL;
}

void mt_motion_set_sens(uint8_t sens) {
  s_poll_th = sens == 0 ? 0.15f : sens == 2 ? 0.04f : 0.08f;
  if (s_mode == MT_MOTION_INT) accel.configMotionInterrupt(threshold_for(sens));
}

void mt_motion_loop() {
  uint32_t now = millis();
  if (s_mode == MT_MOTION_INT) {
    if (s_irq) { s_irq = false; s_last_motion = now; }
    return;
  }
  if (s_mode != MT_MOTION_POLL || (int32_t)(now - s_next_poll) < 0) return;
  s_next_poll = now + 1000;
  float x, y, z;
  if (!accel.read(x, y, z)) {
    if (++s_fail >= 10) s_mode = MT_MOTION_FAULT;
    return;
  }
  s_fail = 0;
  if (s_have_prev && (fabsf(x - s_px) + fabsf(y - s_py) + fabsf(z - s_pz)) > s_poll_th) s_last_motion = now;
  s_px = x; s_py = y; s_pz = z; s_have_prev = true;
}

bool mt_motion_available() { return s_mode == MT_MOTION_INT || s_mode == MT_MOTION_POLL; }
uint32_t mt_motion_last() { return s_last_motion; }
void mt_motion_touch() { s_last_motion = millis(); }
MtMotionMode mt_motion_mode() { return s_mode; }

const char* mt_motion_type() {
  if (s_mode == MT_MOTION_NONE) return "-";
#if defined(T1000_E)
  return "qma6100p";
#elif defined(RAK_WISMESH_TAG)
  return "lis2dh";
#else
  return "-";
#endif
}

const char* mt_motion_mode_str() {
  switch (s_mode) {
    case MT_MOTION_INT: return "interrupt";
    case MT_MOTION_POLL: return "poll";
    case MT_MOTION_FAULT: return "fout";
    default: return "afwezig";
  }
}
