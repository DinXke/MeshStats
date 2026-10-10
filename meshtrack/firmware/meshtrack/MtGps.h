#pragma once
// MeshTrack: NMEA-provider voor de T1000-E (AG3335) en de RAK WisMesh Tag (AT6558R). Gelijk aan stock
// MicroNMEALocationProvider (v1.17.1), plus snelheid, koers, HDOP en het moment
// van de laatste geldige fix. Die laatste is nodig omdat MicroNMEA na het
// uitschakelen van de GPS de oude fix "geldig" blijft noemen.
#include <helpers/sensors/LocationProvider.h>
#include <MicroNMEA.h>
#include <RTClib.h>

class MtNmeaProvider : public LocationProvider {
  char _buf[100];
  MicroNMEA nmea;
  mesh::RTCClock* _clock;
  Stream* _ser;
  unsigned long next_check = 0;
  long time_valid = 0;
  unsigned long _last_time_sync = 0;
  unsigned long _last_valid_ms = 0;
  unsigned long _last_sentence_ms = 0;   // MeshTrack: laatste geldige NMEA-zin (ook zonder fix)
  bool _paused = false;                  // MeshTrack: de GPS-zoeker (RAK3401) leest zelf de UART
  uint32_t _last_valid_unix = 0;     // MeshTrack: GPS-tijd van de laatste geldige fix
  static const unsigned long TIME_SYNC_INTERVAL = 1800000;

public:
  MtNmeaProvider(Stream& ser, mesh::RTCClock* clock) : nmea(_buf, sizeof(_buf)), _clock(clock), _ser(&ser) {}

#if defined(RAK_3401)
  // RAK3401 (0.9.7): de GPS hangt aan 3V3_S, en die voedt ook de versterker: nooit uitschakelen.
  // "GPS uit" = de tracker gebruikt hem niet; de pinnen en baud zoekt MtGpsScan.cpp.
  void begin() override {}
  void stop() override {}
  void reset() override {}
  bool isEnabled() override { return true; }
#elif defined(RAK_WISMESH_TAG)
  // WisMesh Tag (0.9.7): de EnvironmentSensorManager zet de GPS aan/uit via begin()/stop(), zoals
  // stock MicroNMEALocationProvider: PIN_GPS_EN (P1.02, schakelt de 3,3V-voeding van de GPS) hoog =
  // aan. Geen resetpin.
  void begin() override { pinMode(PIN_GPS_EN, OUTPUT); digitalWrite(PIN_GPS_EN, HIGH); }
  void stop() override { pinMode(PIN_GPS_EN, OUTPUT); digitalWrite(PIN_GPS_EN, LOW); }
  void reset() override {}
  bool isEnabled() override { return digitalRead(PIN_GPS_EN) == HIGH; }
#else
  // De T1000-E-sensormanager stuurt de GPS-pinnen zelf; deze vier blijven leeg
  // zoals in stock (daar staan ze ook uitgecommentarieerd).
  void begin() override {}
  void stop() override {}
  void reset() override {}
  bool isEnabled() override { return true; }
#endif

  void syncTime() override { nmea.clear(); LocationProvider::syncTime(); }
  long getLatitude() override { return nmea.getLatitude(); }
  long getLongitude() override { return nmea.getLongitude(); }
  long getAltitude() override { long a = 0; nmea.getAltitude(a); return a; }
  long satellitesCount() override { return nmea.getNumSatellites(); }
  bool isValid() override { return nmea.isValid(); }
  long getTimestamp() override {
    DateTime dt(nmea.getYear(), nmea.getMonth(), nmea.getDay(), nmea.getHour(), nmea.getMinute(), nmea.getSecond());
    return dt.unixtime();
  }
  void sendSentence(const char* s) override { nmea.sendSentence(*_ser, s); }

  // ---- MeshTrack-aanvullingen ----
  float speedKmh() { long v = nmea.getSpeed(); return v < 0 ? 0.0f : (float)v / 1000.0f * 1.852f; }
  float courseDeg() { long c = nmea.getCourse(); return c < 0 ? -1.0f : (float)c / 1000.0f; }
  float hdop() { uint8_t h = nmea.getHDOP(); return h == 255 ? 99.9f : (float)h / 10.0f; }
  // Geldige fix van minder dan max_age_ms oud (0 als er nooit een was).
  bool freshFix(unsigned long max_age_ms) {
    return _last_valid_ms != 0 && nmea.isValid() && (millis() - _last_valid_ms) <= max_age_ms;
  }
  unsigned long fixAgeMs() { return _last_valid_ms ? millis() - _last_valid_ms : 0xFFFFFFFFUL; }
  unsigned long lastValidMs() { return _last_valid_ms; }
  // GPS-tijd (unix) van de laatste geldige fix, 0 = onbekend.
  uint32_t lastValidUnix() { return _last_valid_ms ? _last_valid_unix : 0; }
  void forgetFix() { _last_valid_ms = 0; }
  unsigned long lastSentenceMs() { return _last_sentence_ms; }
  void setPaused(bool p) { _paused = p; if (p) nmea.clear(); }

  void loop() override {
    if (_paused) return;
    while (_ser->available()) {
      bool done = nmea.process((char)_ser->read());
      if (done) _last_sentence_ms = millis() | 1;
      if (done && nmea.isValid()) {
        _last_valid_ms = millis();
        if (nmea.getYear() >= 2024) _last_valid_unix = (uint32_t)getTimestamp();   // GPS-tijd van deze fix
      }
    }
    if (!isValid()) time_valid = 0;
    if ((long)(millis() - next_check) > 0) {
      next_check = millis() + 1000;
      if (!_time_sync_needed && _clock != NULL && (millis() - _last_time_sync) > TIME_SYNC_INTERVAL) {
        _time_sync_needed = true;
      }
      if (_time_sync_needed && time_valid > 2 && _clock != NULL) {
        _clock->setCurrentTime(getTimestamp());
        _time_sync_needed = false;
        _last_time_sync = millis();
      }
      if (isValid()) time_valid++;
    }
  }
};

MtNmeaProvider& mt_gps();
