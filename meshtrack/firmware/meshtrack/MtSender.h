#pragma once
#include <Arduino.h>
#include "MtHooks.h"

struct MtSendStats {
  uint32_t ok, failed, retries, sent_packets, replaced, no_target;
  bool have_last, last_ok;
  uint8_t last_attempts;
  uint32_t last_ms;
};

// ack_ms: sinds de eerste poging; tag: wat de afzender meegaf (bv. tijd van het nieuwste punt)
typedef void (*MtSendDone)(bool ok, bool manual, uint32_t ack_ms, uint32_t tag);

// retries: herhaalpogingen voor dit bericht. keep = niet opgeven voor een nieuwer bericht
// (SOS, klik, stil, heartbeat, moduswissel); een gewone positie wijkt voor een verse.
bool mt_send(const char* text, bool manual, uint8_t retries, bool keep, uint32_t tag = 0);   // false = meteen mislukt
#define MT_TEXT_MAX 156   // MAX_TEXT_LEN van MeshCore is 160
#define MT_CHAN_TEXT_MAX 150   // kanaal: "MT: " gaat ervoor
#define MT_ACK_NONE 0xFFFFFFFFUL   // ack_ms bij een kanaalbericht: verstuurd, maar geen ACK mogelijk
void mt_sender_loop();
bool mt_sender_busy();                          // bericht in de lucht of wachtend op ACK
bool mt_sender_ensure_contact();                // doel als contact aanmaken indien nodig
void mt_sender_set_done_cb(MtSendDone cb);
const MtSendStats& mt_sender_stats();
