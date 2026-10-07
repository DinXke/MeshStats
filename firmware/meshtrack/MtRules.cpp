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

void mt_rules_link(MtRuleState& st, const MtRuleParams& p, bool ok, uint32_t ack_ms) {
  if (ok) {
    st.fails = 0;
    st.slow = false;
    bool quick = p.fast_ack_s == 0 || ack_ms <= 1000UL * p.fast_ack_s;
    st.fast = p.fast_interval_s > 0 && quick;
    return;
  }
  if (st.fails < 255) st.fails++;
  if (st.fails > p.fast_keep) st.fast = false;
  if (p.slow_after > 0 && st.fails >= p.slow_after) st.slow = true;
}

void mt_rules_intervals(const MtRuleState& st, const MtRuleParams& p, uint32_t& min_i, uint32_t& max_i) {
  min_i = p.min_interval_s;
  max_i = p.max_interval_s;
  if (st.slow) {
    uint32_t f = p.slow_factor ? p.slow_factor : 1;
    min_i *= f;
    max_i *= f;
  } else if (st.fast && p.fast_interval_s > 0 && p.fast_interval_s < min_i) {
    min_i = p.fast_interval_s;
  }
}

MtReason mt_rules_decide(const MtRuleState& st, const MtRuleParams& p, uint32_t now_s,
                         double lat, double lon, float spd_kmh, float crs) {
  if (!st.have_tx || !st.have_pos) return MT_R_FIRST;
  uint32_t since = now_s - st.last_tx_s;
  uint32_t min_i, max_i;
  mt_rules_intervals(st, p, min_i, max_i);
  if (since < min_i) return MT_R_NONE;
  double moved = mt_haversine_m(st.last_lat, st.last_lon, lat, lon);
  if (st.fast && !st.slow && p.fast_interval_s > 0 && since >= p.fast_interval_s && moved >= MT_FAST_MIN_MOVE_M)
    return MT_R_FAST;
  bool dist_ok = moved >= p.min_dist_m;
  bool fast_ok = p.min_speed_kmh == 0 || spd_kmh >= p.min_speed_kmh;
  if (fast_ok && dist_ok) return MT_R_DIST;
  if (p.turn_min_deg > 0 && crs >= 0 && st.last_crs >= 0 && spd_kmh >= p.turn_min_speed_kmh &&
      mt_angle_diff(crs, st.last_crs) >= p.turn_min_deg) return MT_R_TURN;
  if (max_i > 0 && since >= max_i) return MT_R_MAXINT;
  return MT_R_NONE;
}

const char* mt_reason_str(MtReason r) {
  switch (r) {
    case MT_R_FIRST: return "eerste";
    case MT_R_DIST: return "afstand";
    case MT_R_TURN: return "bocht";
    case MT_R_MAXINT: return "max_interval";
    case MT_R_FAST: return "snel";
    default: return "-";
  }
}
