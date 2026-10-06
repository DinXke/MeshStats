#pragma once
// Haken die MeshTrack in de (gekopieerde) stock MyMesh.cpp nodig heeft.
#include <stdint.h>

struct ContactInfo;

// ACK binnen: is het er een van een eigen T1-bericht? Dan de doel-contact
// teruggeven (de mesh leert zo ook het pad), anders nullptr.
ContactInfo* mt_on_ack(const uint8_t* data);

// Batterij voor de app: virtuele mV zodat de lineaire formule van de app het
// juiste % (volgens de echte LiPo-curve) toont.
uint16_t mt_battery_app_mv(uint16_t real_mv);
