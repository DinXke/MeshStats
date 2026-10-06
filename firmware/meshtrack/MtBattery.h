#pragma once
#include <stdint.h>

int mt_battery_pct(uint16_t mv);          // 0..100, -1 = onbekend
uint16_t mt_battery_app_mv(uint16_t real_mv);
