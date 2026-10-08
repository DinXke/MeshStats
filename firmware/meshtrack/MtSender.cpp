// T1C-berichten versturen op het trackingkanaal (MeshTrack 0.7: alleen nog kanalen).
//
// Een kanaalbericht gaat één keer via flood de mesh in; een ACK bestaat niet. Er staat
// hooguit één bericht klaar: een nieuwer vervangt het, behalve als het klaarstaande
// belangrijk is (SOS, klik, stil, heartbeat, moduswissel).
#include "MtSender.h"
#include "MtConfig.h"
#include "MyMesh.h"
#include <string.h>

struct Job { bool used; char text[MT_TEXT_MAX + 1]; bool manual; bool keep; uint32_t tag; };

static Job s_cur = {}, s_next = {};
static MtSendStats s_stats = {};
static MtSendDone s_done_cb = nullptr;

bool mt_sender_ready() {
  ChannelDetails ch;
  return the_mesh.mtGetChannel(mt_cfg.chan_idx, ch) && ch.name[0];
}

static void finish(bool ok) {
  if (ok) { s_stats.ok++; s_stats.last_ok = true; }
  else { s_stats.failed++; s_stats.last_ok = false; }
  s_stats.last_ms = millis();
  s_stats.have_last = true;
  bool manual = s_cur.manual;
  uint32_t tag = s_cur.tag;
  s_cur.used = false;
  if (s_done_cb) s_done_cb(ok, manual, tag);
  if (s_next.used) { s_cur = s_next; s_next.used = false; }
}

static bool transmit() {
  if (!the_mesh.mtSendChannel(mt_cfg.chan_idx, s_cur.text)) { s_stats.no_chan++; return false; }
  s_stats.sent_packets++;
  return true;
}

void mt_sender_set_done_cb(MtSendDone cb) { s_done_cb = cb; }

bool mt_send(const char* text, bool manual, bool keep, uint32_t tag) {
  Job j;
  j.used = true;
  j.tag = tag;
  j.manual = manual;
  j.keep = keep;
  strncpy(j.text, text, sizeof(j.text) - 1);
  j.text[sizeof(j.text) - 1] = 0;
  if (s_cur.used) {
    if (s_next.used) {
      if (s_next.keep && !j.keep) return true;   // een belangrijk bericht niet verdringen
      s_stats.replaced++;
    }
    s_next = j;
    return true;
  }
  s_cur = j;
  bool ok = transmit();
  finish(ok);
  return ok;
}

void mt_sender_loop() {
  if (!s_cur.used) return;        // volgend bericht uit het slot
  finish(transmit());
}

bool mt_sender_busy() { return s_cur.used; }
const MtSendStats& mt_sender_stats() { return s_stats; }
