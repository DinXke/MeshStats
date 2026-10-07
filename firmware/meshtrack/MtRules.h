#pragma once
// Bewegingsregels. Exacte port van server/meshtrack/rules.py (de referentie,
// met tests in server/tests/test_rules.py). Tijden in seconden.
//
//   rate_ok = (nu - laatste_tx) >= min_interval
//   afstand = afstand(laatste_tx_pos, pos) >= min_dist
//   snel    = min_speed == 0  OF  spd >= min_speed
//   bocht   = turn_min > 0 EN spd >= turn_min_speed EN |koers - laatste_tx_koers| >= turn_min
//   forceer = max_interval > 0 EN (nu - laatste_tx) >= max_interval
//   zend als rate_ok EN ((snel EN afstand) OF bocht OF forceer)
//
// Ritme volgens de ontvangst: na een ACK binnen fast_ack_s "snel" (in beweging
// elke fast_interval een positie, als hij minstens 20 m verplaatste); pas na meer
// dan fast_keep missers na elkaar terug. Na slow_after missers na elkaar "traag":
// min_interval en max_interval x slow_factor, tot de volgende ACK.
#include <stdint.h>

#define MT_STILL_KMH 1.5f

struct MtRuleParams {
  uint16_t min_speed_kmh, min_dist_m, turn_min_deg, turn_min_speed_kmh;
  uint32_t min_interval_s, max_interval_s;
  uint16_t fast_interval_s;
  uint8_t  fast_keep, fast_ack_s, slow_after, slow_factor;
  uint8_t  adaptive;            // 0 = ritme volgens de ontvangst uit
};

#define MT_FAST_MIN_MOVE_M 20

struct MtRuleState {
  bool     have_tx = false;
  uint32_t last_tx_s = 0;
  double   last_lat = 0, last_lon = 0;
  bool     have_pos = false;
  float    last_crs = -1;          // -1 = onbekend
  bool     fast = false, slow = false;
  uint8_t  fails = 0;
  void sent(uint32_t now_s, bool pos, double lat, double lon, float crs) {
    have_tx = true; last_tx_s = now_s;
    if (pos) { have_pos = true; last_lat = lat; last_lon = lon; }
    if (crs >= 0) last_crs = crs;
  }
};

enum MtReason : uint8_t { MT_R_NONE = 0, MT_R_FIRST, MT_R_DIST, MT_R_TURN, MT_R_MAXINT, MT_R_FAST };

void mt_rules_link(MtRuleState& st, const MtRuleParams& p, bool ok, uint32_t ack_ms);
void mt_rules_intervals(const MtRuleState& st, const MtRuleParams& p, uint32_t& min_i, uint32_t& max_i);

MtReason mt_rules_decide(const MtRuleState& st, const MtRuleParams& p, uint32_t now_s,
                         double lat, double lon, float spd_kmh, float crs);
double mt_haversine_m(double lat1, double lon1, double lat2, double lon2);
float mt_angle_diff(float a, float b);
const char* mt_reason_str(MtReason r);
