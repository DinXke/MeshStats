#pragma once
// MeshTrack: NMEA-provider voor de T1000-E (AG3335). Gelijk aan stock
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
  static const unsigned long TIME_SYNC_INTERVAL = 1800000;

public:
  MtNmeaProvider(Stream& ser, mesh::RTCClock* clock) : nmea(_buf, sizeof(_buf)), _clock(clock), _ser(&ser) {}

  // De T1000-E-sensormanager stuurt de GPS-pinnen zelf; deze vier blijven leeg
  // zoals in stock (daar staan ze ook uitgecommentarieerd).
  void begin() override {}
  void stop() override {}
  void reset() override {}
  bool isEnabled() override { return true; }

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
  void forgetFix() { _last_valid_ms = 0; }

  void loop() override {
    while (_ser->available()) {
      if (nmea.process((char)_ser->read()) && nmea.isValid()) _last_valid_ms = millis();
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
