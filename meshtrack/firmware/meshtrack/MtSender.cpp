// T1C-berichten versturen op het trackingkanaal (MeshTrack 0.7: alleen nog kanalen).
//
// Een kanaalbericht gaat één keer via flood de mesh in; een ACK bestaat niet. Er staat
// hooguit één bericht klaar: een nieuwer vervangt het, behalve als het klaarstaande
// belangrijk is (SOS, klik, stil, heartbeat, moduswissel).
#include "MtSender.h"
#include "MtConfig.h"
#include "MyMesh.h"
#include "MeshTrack.h"   // mt_log
#include <string.h>
#include <SHA256.h>

struct Job { bool used; char text[MT_TEXT_MAX + 1]; bool manual; bool keep; uint32_t tag; char state; };

static Job s_cur = {}, s_next = {};
static MtSendStats s_stats = {};
static MtSendDone s_done_cb = nullptr;
static uint8_t s_block[16];                     // eerste cijferblok van het laatste pakket

// De bekende sleutel van het kanaal "Public" (MeshCore PUBLIC_GROUP_PSK izOH6cXN6mrJ5e26oRXNcg==).
static const uint8_t PUBLIC_SECRET[16] = { 0x8b, 0x33, 0x87, 0xe9, 0xc5, 0xcd, 0xea, 0x6a,
                                           0xc9, 0xe5, 0xed, 0xba, 0xa1, 0x15, 0xcd, 0x72 };

// Volgt de sleutel uit een naam? (#kanalen: sleutel = eerste 16 bytes van SHA256("#naam"))
static bool secret_from_name(const uint8_t* secret, const char* prefix, const char* name) {
  SHA256 sha;
  uint8_t h[32];
  sha.reset();
  if (*prefix) sha.update(prefix, strlen(prefix));
  sha.update(name, strlen(name));
  sha.finalize(h, sizeof(h));
  return memcmp(h, secret, 16) == 0;
}

static bool channel_public(const ChannelDetails& ch) {
  const uint8_t* s = ch.channel.secret;
  if (memcmp(s, PUBLIC_SECRET, 16) == 0) return true;
  const char* n = ch.name;
  if (*n == '#') return secret_from_name(s, "", n) || secret_from_name(s, "", n + 1);
  return secret_from_name(s, "#", n) || secret_from_name(s, "", n);
}

MtChanState mt_chan_state() {
  if (mt_cfg.chan_idx == MT_CHAN_NONE_IDX) return MT_CHAN_NONE;
  ChannelDetails ch;
  if (!the_mesh.mtGetChannel(mt_cfg.chan_idx, ch) || !ch.name[0]) return MT_CHAN_MISSING;
  return channel_public(ch) ? MT_CHAN_PUBLIC : MT_CHAN_OK;
}

const char* mt_chan_state_str(MtChanState s) {
  switch (s) {
    case MT_CHAN_OK: return "-";
    case MT_CHAN_NONE: return "geen";
    case MT_CHAN_MISSING: return "ontbreekt";
    default: return "openbaar";
  }
}

bool mt_sender_ready() { return mt_chan_state() == MT_CHAN_OK; }

static void finish(bool ok) {
  if (ok) { s_stats.ok++; s_stats.last_ok = true; }
  else { s_stats.failed++; s_stats.last_ok = false; }
  s_stats.last_ms = millis();
  s_stats.have_last = true;
  bool manual = s_cur.manual;
  uint32_t tag = s_cur.tag;
  char state = s_cur.state;
  s_cur.used = false;
  if (s_done_cb) s_done_cb(ok, manual, tag, state);
  if (s_next.used) { s_cur = s_next; s_next.used = false; }
}

static bool transmit() {
  MtChanState cs = mt_chan_state();             // nooit op een openbaar kanaal (ook niet als iets mt_send toch aanroept)
  if (cs != MT_CHAN_OK) {
    if (cs == MT_CHAN_PUBLIC) mt_log("trackingkanaal is openbaar (Public): niet verstuurd");
    s_stats.no_chan++;
    return false;
  }
  if (!the_mesh.mtSendChannel(mt_cfg.chan_idx, s_cur.text, s_block)) { s_stats.no_chan++; return false; }
  s_stats.sent_packets++;
  return true;
}

void mt_sender_set_done_cb(MtSendDone cb) { s_done_cb = cb; }

bool mt_send(const char* text, bool manual, bool keep, uint32_t tag, char state) {
  Job j;
  j.used = true;
  j.tag = tag;
  j.state = state;
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
const uint8_t* mt_sender_last_block() { return s_block; }
