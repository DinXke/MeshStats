#pragma once
// Prioriteitsingang op P0.31 (MeshTrack 0.9.7, alleen RAK3401; instelling pin31 prio), bv. de blauwe
// zwaailichten van een brandweerwagen via een optocoupler. Zolang hij actief is (plus prio_houd), draagt
// elk T1C-bericht de vlag "p" in veld 16. Op andere borden: altijd uit.
#include <Arduino.h>

enum MtPrioState : uint8_t { MT_PRIO_OFF, MT_PRIO_IDLE, MT_PRIO_ACTIVE, MT_PRIO_FAULT };

void mt_prio_apply_mode();         // pin31/prio_niveau gewijzigd: pinconfiguratie opnieuw
void mt_prio_loop();               // elke lus: pin bemonsteren
bool mt_prio_active();             // vlag "p" zetten?
MtPrioState mt_prio_state();
uint32_t mt_prio_left_s();         // resterende tijd (actief)
int mt_prio_take_event();          // 1 = net actief geworden, 2 = net verlopen, 0 = niets
uint32_t mt_prio_houd_min();       // prio_houd in minuten (standaard 5); 0 = volgt de ingang (schakelaar, contact)
uint32_t mt_prio_interval_s();     // prio_interval in s, 0 = geen wijziging (standaard 30)
