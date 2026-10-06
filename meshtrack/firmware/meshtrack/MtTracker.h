#pragma once
#include <Arduino.h>

enum MtTState : uint8_t { MT_T_OFF, MT_T_SLEEP, MT_T_ACQUIRE, MT_T_MOVING };

void mt_tracker_begin();
void mt_tracker_loop();
bool mt_tracker_manual();          // enkele klik: positie nu; false = genegeerd (te snel / geen doel)
void mt_tracker_mode_changed();    // stuurt een B-bericht
void mt_tracker_target_changed();
bool mt_radio_paused();            // true = mesh-lus overslaan (radio slaapt)

MtTState mt_tracker_state();
const char* mt_tracker_state_str();
const char* mt_tracker_last_reason();
uint32_t mt_tracker_last_tx_ms();
uint16_t mt_tracker_seq();
bool mt_tracker_gps_on();
