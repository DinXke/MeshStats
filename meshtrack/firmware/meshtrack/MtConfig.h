#pragma once
// Instellingen van MeshTrack in /mt_cfg.dat (InternalFS).
//
// Opslaan is zo goed als atomair: eerst /mt_cfg.tmp volledig schrijven, dan
// pas het oude bestand vervangen. Ontbreekt het hoofdbestand bij het opstarten
// maar staat er een geldige .tmp, dan wordt die gebruikt. Een bestand van een
// NIEUWERE firmware (hogere versie) wordt nooit overschreven.

#include <stdint.h>
#include <stddef.h>

#define MT_CFG_VERSION 4   // v4: kanaal + authsleutel (velden achteraan, oudere worden overgenomen)
// De indeling NOOIT wijzigen (flash-compatibel): ongebruikte velden blijven staan.

struct MtCfg {
  uint32_t magic;               // 'MTC1'
  uint16_t version;
  uint16_t size;                // sizeof(MtCfg) van de schrijver
  uint8_t  mode;                // MtMode bij geen USB
  uint8_t  track_in_companion;  // ook volgen in companionmodus
  uint8_t  accel_sens;          // 0 laag, 1 midden, 2 hoog
  uint8_t  ack_retries;         // niet meer gebruikt (0.7)
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
  uint8_t  target_set;          // niet meer gebruikt (0.7)
  uint8_t  target[32];          // niet meer gebruikt (0.7; was de pubkey van de server-companion)
  uint8_t  led_mode;            // 0 = alleen als companion, 1 = altijd, 2 = nooit (zat vroeger in de opvulling: v1 blijft leesbaar)
  uint8_t  _pad[2];
  // ---- v2: ritme volgens de ontvangst (niet meer gebruikt sinds 0.7: geen ACK's) ----
  uint16_t fast_interval_s;     // na een snelle ACK in beweging elke x s (0 = uit)
  uint8_t  fast_keep;           // zoveel missers na elkaar blijft hij snel
  uint8_t  fast_ack_s;          // ACK binnen x s = goede ontvangst (0 = elke ACK)
  uint8_t  slow_after;          // na x mislukte zendingen na elkaar trager (0 = nooit)
  uint8_t  slow_factor;         // intervallen x factor als hij traag is
  uint8_t  fast_retries;        // herhaalpogingen voor gewone posities bij goede ontvangst (zat in de opvulling, 0 = standaard)
  uint8_t  _pad2[1];
  // ---- v3 ----
  uint16_t sample_s;            // in beweging elke x s een punt bewaren; mee in het volgende bericht (0 = uit)
  uint8_t  adaptive;            // niet meer gebruikt (0.7)
  uint8_t  msg_beep;            // biep bij berichten als companion zonder app: 0 = alleen privé (standaard), 1 = alles, 2 = nooit (was opvulling)
  // ---- v4 ----
  uint8_t  transport;           // niet meer gebruikt (0.7: altijd kanaal)
  uint8_t  chan_idx;            // kanaalnummer op dit toestel (chan list)
  uint8_t  authkey_set;
  uint8_t  _pad4;
  uint8_t  authkey[16];         // ondertekent kanaalberichten (HMAC); van de server, via USB
  uint32_t crc;                 // crc32 over alles hiervoor
};

extern MtCfg mt_cfg;

// Uitkomst van het laden, voor `status` en de bootmelding.
extern const char* mt_cfg_load_note;
extern bool mt_cfg_readonly;    // bestand van nieuwere fw: niet overschrijven

void mt_cfg_defaults(MtCfg& c);
void mt_cfg_begin();
bool mt_cfg_save();
// Kleine bestanden van MeshTrack (op ExtraFS; lezen valt terug op InternalFS van oudere firmware).
bool mt_file_read(const char* path, void* buf, size_t len);
bool mt_file_write(const char* path, const void* data, size_t len);
void mt_fs_status(char* out, size_t n);   // "opslag_intern=x/7 opslag_extra=y/z" (blokken)
uint32_t mt_crc32(uint32_t crc, const uint8_t* p, uint32_t n);
