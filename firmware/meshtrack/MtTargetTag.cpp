// MeshTrack 0.9.7: kopie van variants/rak_wismesh_tag/target.cpp (v1.17.1) voor de RAK WisMesh Tag.
// Enige wijzigingen: de GPS-provider is MtNmeaProvider (snelheid, koers, HDOP, leeftijd van de fix;
// GPS aan/uit via PIN_GPS_EN, zie MtGps.h), mt_gps() bestaat, en er is geen MomentaryButton (de
// MeshTrack-knop zit in UITask/Button.cpp, met interrupt-tijdstempels zoals op de T1000-E).
#if defined(RAK_WISMESH_TAG)
#include <Arduino.h>
#include "target.h"
#include <helpers/ArduinoHelpers.h>
#include "MtGps.h"

RAKWismeshTagBoard board;

#ifdef DISPLAY_CLASS
  DISPLAY_CLASS display;
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
#endif   // RAK_WISMESH_TAG
