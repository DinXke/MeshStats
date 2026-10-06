#pragma once
#include <Arduino.h>
#include "MtHooks.h"

struct MtSendStats {
  uint32_t ok, failed, retries, sent_packets, replaced, no_target;
  bool have_last, last_ok;
  uint8_t last_attempts;
  uint32_t last_ms;
};

typedef void (*MtSendDone)(bool ok, bool manual);

bool mt_send(const char* text, bool manual);   // false = meteen mislukt (geen doel/radio)
void mt_sender_loop();
bool mt_sender_busy();                          // bericht in de lucht of wachtend op ACK
bool mt_sender_ensure_contact();                // doel als contact aanmaken indien nodig
void mt_sender_set_done_cb(MtSendDone cb);
const MtSendStats& mt_sender_stats();
