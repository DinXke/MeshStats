#include "MeshTrack.h"
#include "MtConfig.h"
#include "MtTracker.h"
#include "MtSender.h"
#include "MyMesh.h"
#include "UITask.h"

extern UITask ui_task;

static bool    s_usb = false;
static MtMode  s_eff = MT_MODE_COMPANION;
static uint32_t s_next_usb_check = 0;

const char* mt_mode_name(uint8_t m) { return m == MT_MODE_TRACKER ? "tracker" : "companion"; }
MtMode mt_effective_mode() { return s_eff; }
bool mt_usb() { return s_usb; }

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

static void reevaluate(bool beep, bool power_event = false) {
  MtMode w = wanted_mode();
  if (w == s_eff) {
    if (power_event) mt_tracker_power_changed();   // zelfde modus, toch de voeding melden
    return;
  }
  apply_mode(w);
  mt_log("modus: %s (%s)", mt_mode_name(w), s_usb ? "USB-voeding" : "batterij");
  if (beep) ui_task.playModeTune(w == MT_MODE_TRACKER);
  mt_tracker_mode_changed();
}

void mt_choose_mode(MtMode m, bool beep) {
  mt_cfg.mode = m;
  bool ok = mt_cfg_save();
  if (beep) ui_task.playModeTune(m == MT_MODE_TRACKER);
  mt_log("gekozen modus: %s%s%s", mt_mode_name(m),
         s_usb && m == MT_MODE_TRACKER ? " (actief zodra USB los is)" : "", ok ? "" : " [NIET bewaard]");
  reevaluate(false);
}

void mt_on_short_press() {
  // Positie nu versturen. De modusbiep bevestigt dat de klik binnen is; twee hoge
  // biepjes volgen zodra het bericht op het trackingkanaal verstuurd is. Ontbreekt
  // het trackingkanaal, dan meteen een lage toon.
  if (!mt_sender_ready()) {
    ui_task.playForced(MT_TUNE_NOK);
    mt_log("klik: trackingkanaal %u ontbreekt op het toestel", (unsigned)mt_cfg.chan_idx);
    return;
  }
  ui_task.playModeTune(s_eff == MT_MODE_TRACKER);
  if (!mt_tracker_manual()) mt_log("klik genegeerd (te snel na de vorige)");
}

void mt_on_double_press() {
  mt_choose_mode(mt_cfg.mode == MT_MODE_TRACKER ? MT_MODE_COMPANION : MT_MODE_TRACKER, true);
}

void mt_on_cli_rescue() { mt_menu_suspend(); }

void mt_on_sos() {
  mt_log("SOS via de knop");
  if (!mt_tracker_sos()) {
    ui_task.playForced(MT_TUNE_NOK);
    mt_log("SOS niet verstuurd: trackingkanaal %u ontbreekt op het toestel", (unsigned)mt_cfg.chan_idx);
  }
}

bool mt_led_allowed() {
  switch (mt_cfg.led_mode) {
    case 1: return true;
    case 2: return false;
    default: return s_eff == MT_MODE_COMPANION;
  }
}

bool mt_mesh_paused() { return mt_radio_paused(); }

void mt_begin() {
  mt_cfg_begin();
  s_usb = board.isExternalPowered();
  apply_mode(wanted_mode());
  mt_tracker_begin();
  mt_menu_begin();
}

void mt_loop() {
  if ((int32_t)(millis() - s_next_usb_check) >= 0) {
    s_next_usb_check = millis() + 500;
    bool usb = board.isExternalPowered();
    if (usb != s_usb) { s_usb = usb; reevaluate(true, true); }
  }
  mt_tracker_loop();
  mt_menu_loop();
}
