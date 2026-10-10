// Batterij: echte LiPo-ontlaadcurve (T1000-E; ook voor de 1000 mAh-LiPo van de WisMesh Tag; overgenomen uit
// MU-companion MuBattery.cpp, zie battery_fix_t1000e.md) en de "spoof" voor de
// MeshCore-app, die lineair rekent: pct = (mv - 3000) / 12.
#include "MtBattery.h"
#include "MtHooks.h"

int mt_battery_pct(uint16_t mv) {
  if (mv == 0) return -1;
  static const struct { uint16_t mv; uint8_t pct; } curve[] = {
    {3300,0},{3450,5},{3500,10},{3550,15},{3600,20},{3650,30},{3700,40},{3750,50},
    {3800,60},{3850,70},{3900,80},{3950,85},{4000,90},{4050,95},{4100,98},{4150,99},{4200,100}
  };
  const int n = (int)(sizeof(curve) / sizeof(curve[0]));
  if (mv <= curve[0].mv) return curve[0].pct;
  if (mv >= curve[n - 1].mv) return curve[n - 1].pct;
  for (int i = 1; i < n; i++) {
    if (mv <= curve[i].mv) {
      uint16_t lo = curve[i - 1].mv, hi = curve[i].mv;
      uint8_t lp = curve[i - 1].pct, hp = curve[i].pct;
      return lp + (int)((uint32_t)(mv - lo) * (hp - lp) / (hi - lo));
    }
  }
  return 100;
}

uint16_t mt_battery_app_mv(uint16_t real_mv) {
  int pct = mt_battery_pct(real_mv);
  if (pct < 0) return real_mv;
  long app = 3000L + (long)pct * 12L;
  if (app < 3000) app = 3000;
  if (app > 4200) app = 4200;
  return (uint16_t)app;
}
