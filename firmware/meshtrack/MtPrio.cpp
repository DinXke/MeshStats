// Prioriteitsingang op P0.31 (MeshTrack 0.9.7), zie MtPrio.h.
//
// LET OP (hardware): 12/24 V NOOIT rechtstreeks op de pin. Via een optocoupler (die de pin naar massa
// trekt: prio_niveau laag, standaard) of via een spanningsdeler met zenerdiode (prio_niveau hoog); hooguit
// 3,3 V op de pin.
//
// Bemonsteren: elke 5 ms digitaal (Schmitt-trigger-ingang met pull-up voor laag, pull-down voor hoog), een
// nieuwe stand telt pas na 30 ms stabiel. Elke actieve periode (ook een knipperend zwaailicht) verlengt
// prio_until = nu + prio_houd. prio_houd 0 (= volgt de ingang, voor een schakelaar of een potentiaalvrij
// contact): prioritair zolang de ingang actief is, 200 ms ontdenderd; uit = meteen voorbij. Zwevende pin (ruis): meer dan 100 ruwe wisselingen binnen 1 s = "fout": dan
// nooit de vlag, tot het weer 60 s rustig is.
#include "MtPrio.h"
#include "MtConfig.h"
#include "MeshTrack.h"

uint32_t mt_prio_houd_min() {
  if (mt_cfg.prio_houd == 255) return 0;     // volgt de ingang
  return mt_cfg.prio_houd >= 1 && mt_cfg.prio_houd <= 60 ? mt_cfg.prio_houd : 5;
}
uint32_t mt_prio_interval_s() { return mt_cfg.prio_interval == 255 ? 0 : mt_cfg.prio_interval == 0 ? 30 : mt_cfg.prio_interval; }

#if defined(RAK_3401) && defined(PIN_USER_BTN_ANA)

#define PRIO_PIN PIN_USER_BTN_ANA
static bool s_mode_prio = false;
static uint32_t s_next_ms = 0;
static bool s_raw = false, s_stable = false;
static uint32_t s_raw_since = 0;
static uint32_t s_until = 0;
static bool s_active = false;
static int s_event = 0;
static uint16_t s_raw_changes = 0;
static uint32_t s_win_ms = 0;
static bool s_fault = false;
static uint32_t s_quiet_since = 0;

void mt_prio_apply_mode() {
  bool prio = mt_cfg.pin31 == 2;
  if (prio) pinMode(PRIO_PIN, mt_cfg.prio_niveau ? INPUT_PULLDOWN : INPUT_PULLUP);
  else pinMode(PRIO_PIN, INPUT_PULLUP);            // zoals RAK3401Board::begin (knop of niets)
  if (prio != s_mode_prio) {
    s_mode_prio = prio;
    s_active = s_fault = false;
    s_until = 0;
    s_event = 0;
    s_raw = s_stable = false;
    s_raw_since = s_win_ms = millis();
    s_raw_changes = 0;
  }
}

void mt_prio_loop() {
  static uint8_t applied_mode = 0xFF, applied_lvl = 0xFF;
  if (mt_cfg.pin31 != applied_mode || mt_cfg.prio_niveau != applied_lvl) {
    applied_mode = mt_cfg.pin31;
    applied_lvl = mt_cfg.prio_niveau;
    mt_prio_apply_mode();
  }
  if (!s_mode_prio) return;
  uint32_t now = millis();
  if ((int32_t)(now - s_next_ms) < 0) return;
  s_next_ms = now + 5;
  bool level = digitalRead(PRIO_PIN) == HIGH;
  bool raw = mt_cfg.prio_niveau ? level : !level;    // actief?
  if (raw != s_raw) { s_raw = raw; s_raw_since = now; s_raw_changes++; }
  // ruis: ruwe wisselingen per seconde
  if (now - s_win_ms >= 1000) {
    if (s_raw_changes > 100) {
      if (!s_fault) mt_log("prio-ingang zweeft (%u wisselingen/s): geen prioriteit tot het weer rustig is", (unsigned)s_raw_changes);
      s_fault = true;
      s_quiet_since = 0;
    } else if (s_fault) {
      if (s_raw_changes < 10) { if (!s_quiet_since) s_quiet_since = now; }
      else s_quiet_since = 0;
      if (s_quiet_since && now - s_quiet_since >= 60000UL) { s_fault = false; mt_log("prio-ingang weer rustig"); }
    }
    s_raw_changes = 0;
    s_win_ms = now;
  }
  const uint32_t houd = mt_prio_houd_min();
  if (now - s_raw_since >= (houd ? 30UL : 200UL) && s_stable != s_raw) s_stable = s_raw;   // 30 ms (200 ms: volgt ingang) stabiel
  if (s_fault) {
    if (s_active) { s_active = false; s_until = 0; s_event = 2; mt_log("prio: uit (ingang zweeft)"); }
    return;
  }
  if (s_stable) {
    s_until = now + 60000UL * houd;
    if (!s_active) {
      s_active = true;
      s_event = 1;
      if (houd) mt_log("prio: AAN (ingang actief; houdt %lu min na)", (unsigned long)houd);
      else mt_log("prio: AAN (ingang actief; volgt de ingang)");
    }
  } else if (s_active && !houd) {               // volgt de ingang: meteen voorbij
    s_active = false;
    s_event = 2;
    mt_log("prio: uit (ingang niet meer actief)");
  } else if (s_active && (int32_t)(now - s_until) >= 0) {
    s_active = false;
    s_event = 2;
    mt_log("prio: uit (verlopen)");
  }
}

bool mt_prio_active() { return s_mode_prio && s_active && !s_fault; }
MtPrioState mt_prio_state() {
  if (!s_mode_prio) return MT_PRIO_OFF;
  if (s_fault) return MT_PRIO_FAULT;
  return s_active ? MT_PRIO_ACTIVE : MT_PRIO_IDLE;
}
uint32_t mt_prio_left_s() { return s_active && (int32_t)(s_until - millis()) > 0 ? (s_until - millis()) / 1000 : 0; }
int mt_prio_take_event() { int e = s_event; s_event = 0; return e; }

#else   // andere borden: geen prioriteitsingang

void mt_prio_apply_mode() {}
void mt_prio_loop() {}
bool mt_prio_active() { return false; }
MtPrioState mt_prio_state() { return MT_PRIO_OFF; }
uint32_t mt_prio_left_s() { return 0; }
int mt_prio_take_event() { return 0; }

#endif
