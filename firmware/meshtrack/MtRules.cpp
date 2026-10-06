#include "MtRules.h"
#include <math.h>

double mt_haversine_m(double lat1, double lon1, double lat2, double lon2) {
  const double R = 6371008.8, d2r = M_PI / 180.0;
  double p1 = lat1 * d2r, p2 = lat2 * d2r, dp = p2 - p1, dl = (lon2 - lon1) * d2r;
  double a = sin(dp / 2) * sin(dp / 2) + cos(p1) * cos(p2) * sin(dl / 2) * sin(dl / 2);
  if (a > 1.0) a = 1.0;
  return 2 * R * asin(sqrt(a));
}

float mt_angle_diff(float a, float b) {
  float d = fmodf(fabsf(a - b), 360.0f);
  return d > 180.0f ? 360.0f - d : d;
}

MtReason mt_rules_decide(const MtRuleState& st, const MtRuleParams& p, uint32_t now_s,
                         double lat, double lon, float spd_kmh, float crs) {
  if (!st.have_tx || !st.have_pos) return MT_R_FIRST;
  uint32_t since = now_s - st.last_tx_s;
  if (since < p.min_interval_s) return MT_R_NONE;
  bool dist_ok = mt_haversine_m(st.last_lat, st.last_lon, lat, lon) >= p.min_dist_m;
  bool fast_ok = p.min_speed_kmh == 0 || spd_kmh >= p.min_speed_kmh;
  if (fast_ok && dist_ok) return MT_R_DIST;
  if (p.turn_min_deg > 0 && crs >= 0 && st.last_crs >= 0 && spd_kmh >= p.turn_min_speed_kmh &&
      mt_angle_diff(crs, st.last_crs) >= p.turn_min_deg) return MT_R_TURN;
  if (p.max_interval_s > 0 && since >= p.max_interval_s) return MT_R_MAXINT;
  return MT_R_NONE;
}

const char* mt_reason_str(MtReason r) {
  switch (r) {
    case MT_R_FIRST: return "eerste";
    case MT_R_DIST: return "afstand";
    case MT_R_TURN: return "bocht";
    case MT_R_MAXINT: return "max_interval";
    default: return "-";
  }
}
