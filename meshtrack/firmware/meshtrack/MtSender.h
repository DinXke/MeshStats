#pragma once
#include <Arduino.h>
#include "MtHooks.h"

struct MtSendStats {
  uint32_t ok, failed, sent_packets, replaced, no_chan;   // no_chan = trackingkanaal ontbrak
  bool have_last, last_ok;
  uint32_t last_ms;
};

// manual: met terugmelding (klik, eerste SOS); tag: wat de afzender meegaf (bv. tijd van het nieuwste punt)
typedef void (*MtSendDone)(bool ok, bool manual, uint32_t tag);

// Verstuurt een T1C-bericht op het trackingkanaal (flood, geen ACK mogelijk).
// keep = niet wijken voor een nieuwer bericht (SOS, klik, stil, heartbeat, moduswissel).
bool mt_send(const char* text, bool manual, bool keep, uint32_t tag = 0);   // false = meteen mislukt
#define MT_TEXT_MAX 156   // MAX_TEXT_LEN van MeshCore is 160
// "<nodenaam>: " gaat ervoor; de ruimte hangt af van de naam (the_mesh.mtSenderLen()).
void mt_sender_loop();
bool mt_sender_busy();                          // bericht wacht nog op verzending
bool mt_sender_ready();                         // trackingkanaal ingesteld (bestaat op het toestel)
void mt_sender_set_done_cb(MtSendDone cb);
const MtSendStats& mt_sender_stats();
const uint8_t* mt_sender_last_block();          // eerste cijferblok van het laatst verstuurde pakket (16 bytes)
