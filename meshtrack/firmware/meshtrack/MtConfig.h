#pragma once
// Instellingen van MeshTrack in /mt_cfg.dat (InternalFS).
//
// Opslaan is zo goed als atomair: eerst /mt_cfg.tmp volledig schrijven, dan
// pas het oude bestand vervangen. Ontbreekt het hoofdbestand bij het opstarten
// maar staat er een geldige .tmp, dan wordt die gebruikt. Een bestand van een
// NIEUWERE firmware (hogere versie) wordt nooit overschreven.

#include <stdint.h>

#define MT_CFG_VERSION 1

struct MtCfg {
  uint32_t magic;               // 'MTC1'
  uint16_t version;
  uint16_t size;                // sizeof(MtCfg) van de schrijver
  uint8_t  mode;                // MtMode bij geen USB
  uint8_t  track_in_companion;  // ook volgen in companionmodus
  uint8_t  accel_sens;          // 0 laag, 1 midden, 2 hoog
  uint8_t  ack_retries;
  uint16_t min_speed_kmh;       // 0 = uit
  uint16_t min_dist_m;
  uint16_t turn_min_deg;        // 0 = uit
  uint16_t turn_min_speed_kmh;
  uint32_t min_interval_s;      // rate limit: nooit vaker dan 1x per
  uint32_t max_interval_s;      // in beweging minstens 1x per (0 = uit)
  uint32_t still_timeout_s;
  uint32_t heartbeat_s;         // 0 = uit
  uint16_t fix_timeout_s;
  uint16_t fix_timeout_hb_s;
  uint8_t  target_set;
  uint8_t  target[32];          // pubkey server-companion
  uint8_t  _pad[3];
  uint32_t crc;                 // crc32 over alles hiervoor
};

extern MtCfg mt_cfg;

// Uitkomst van het laden, voor `status` en de bootmelding.
extern const char* mt_cfg_load_note;
extern bool mt_cfg_readonly;    // bestand van nieuwere fw: niet overschrijven

void mt_cfg_defaults(MtCfg& c);
void mt_cfg_begin();
bool mt_cfg_save();
uint32_t mt_crc32(uint32_t crc, const uint8_t* p, uint32_t n);
