#pragma once
#include <Arduino.h>
#include "MtHooks.h"

struct MtSendStats {
  uint32_t ok, failed, sent_packets, replaced, no_chan;   // no_chan = trackingkanaal ontbrak
  bool have_last, last_ok;
  uint32_t last_ms;
};

// manual: met terugmelding (klik, eerste SOS); tag: wat de afzender meegaf (bv. tijd van het nieuwste punt);
// state: de toestandsletter van het T1C-bericht (M, S, H, L, ...)
typedef void (*MtSendDone)(bool ok, bool manual, uint32_t tag, char state);

// Verstuurt een T1C-bericht op het trackingkanaal (flood, geen ACK mogelijk).
// keep = niet wijken voor een nieuwer bericht (SOS, klik, stil, heartbeat, moduswissel).
bool mt_send(const char* text, bool manual, bool keep, uint32_t tag = 0, char state = 0);   // false = meteen mislukt
#define MT_TEXT_MAX 156   // MAX_TEXT_LEN van MeshCore is 160
// "<nodenaam>: " gaat ervoor; de ruimte hangt af van de naam (the_mesh.mtSenderLen()).
void mt_sender_loop();
bool mt_sender_busy();                          // bericht wacht nog op verzending
bool mt_sender_ready();                         // trackingkanaal ingesteld, bestaat op het toestel en is NIET openbaar
// Staat van het trackingkanaal (0.9.10): nooit MeshTrack-berichten op een openbaar kanaal (Public, of een
// #kanaal waarvan de sleutel uit de naam volgt): die zou iedereen kunnen lezen.
enum MtChanState : uint8_t { MT_CHAN_OK, MT_CHAN_NONE, MT_CHAN_MISSING, MT_CHAN_PUBLIC };
MtChanState mt_chan_state();
const char* mt_chan_state_str(MtChanState s);   // "-", "geen", "ontbreekt", "openbaar"
const char* mt_chan_problem();                  // zin voor het log, "" als het kanaal in orde is
#define MT_CHAN_NONE_IDX 0xFF                    // chan_idx: geen trackingkanaal (standaard vanaf 0.9.10)
void mt_sender_set_done_cb(MtSendDone cb);
const MtSendStats& mt_sender_stats();
const uint8_t* mt_sender_last_block();          // eerste cijferblok van het laatst verstuurde pakket (16 bytes)
