#pragma once
#include <Arduino.h>

enum MtMotionMode : uint8_t { MT_MOTION_NONE, MT_MOTION_INT, MT_MOTION_POLL, MT_MOTION_FAULT };

void mt_motion_begin(uint8_t sens);      // 0 laag, 1 midden, 2 hoog
void mt_motion_set_sens(uint8_t sens);
void mt_motion_loop();
bool mt_motion_available();               // werkt de sensor (interrupt of poll)?
uint32_t mt_motion_last();                // millis() van de laatste beweging
void mt_motion_touch();                   // beweging "gezien" (bv. GPS-snelheid)
MtMotionMode mt_motion_mode();
const char* mt_motion_mode_str();
const char* mt_motion_type();             // "qma6100p", "lis2dh" of "-" (geen sensor gevonden)
