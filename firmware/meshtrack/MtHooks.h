#pragma once
// Haken die MeshTrack in de (gekopieerde) stock MyMesh.cpp nodig heeft.
#include <stdint.h>

// Batterij voor de app: virtuele mV zodat de lineaire formule van de app het
// juiste % (volgens de echte LiPo-curve) toont.
uint16_t mt_battery_app_mv(uint16_t real_mv);
