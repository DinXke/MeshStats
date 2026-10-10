// MeshTrack 0.9.7: kopie van variants/rak3401/target.cpp (v1.17.1) voor RAK19007 + RAK3401 + RAK13302.
// Wijzigingen: GPS-provider MtNmeaProvider (Serial1; pinnen en baud zet MtGpsScan.cpp), mt_gps(),
// geen MomentaryButton (de optionele analoge knop zit in UITask/Button.cpp, instelling knop).
// RTC: AutoDiscoverRTCClock vindt een RAK12002 (RV3028, 0x52) op Wire (I2C1 SDA P0.13 / SCL P0.14).
#if defined(RAK_3401)
#include <Arduino.h>
#include "target.h"
#include <helpers/ArduinoHelpers.h>
#include "MtGps.h"

RAK3401Board board;

#ifdef DISPLAY_CLASS
  DISPLAY_CLASS display;      // SSD1306 als die er is; zonder scherm geeft begin() false
#endif

RADIO_CLASS radio = new Module(P_LORA_NSS, P_LORA_DIO_1, P_LORA_RESET, P_LORA_BUSY, SPI);

WRAPPER_CLASS radio_driver(radio, board);

VolatileRTCClock fallback_clock;
AutoDiscoverRTCClock rtc_clock(fallback_clock);

MtNmeaProvider nmea(Serial1, &rtc_clock);
MtNmeaProvider& mt_gps() { return nmea; }
EnvironmentSensorManager sensors = EnvironmentSensorManager(nmea);

bool radio_init() {
  rtc_clock.begin(Wire);
  return radio.std_init(&SPI);
}

mesh::LocalIdentity radio_new_identity() {
  RadioNoiseListener rng(radio);
  return mesh::LocalIdentity(&rng);  // create new random identity
}
#endif   // RAK_3401
