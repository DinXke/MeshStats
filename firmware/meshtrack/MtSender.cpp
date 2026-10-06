// T1-berichten versturen als DM naar het doel (de server-companion), met ACK.
//
// Net als de MeshCore-app: verzenden, wachten op de ACK tot de geschatte
// timeout (op basis van padlengte), anders opnieuw met attempt+1. De laatste
// poging gaat via flood met een gewist pad. Lukt ook dat niet, dan opgeven:
// de volgende push probeert het gewoon opnieuw. Eén bericht tegelijk in de
// lucht; een nieuwer bericht dat binnenkomt terwijl er een loopt, wacht in één
// slot (een nog nieuwer vervangt het, want alleen de recentste positie telt).
#include "MtSender.h"
#include "MtConfig.h"
#include "MyMesh.h"
#include <string.h>

struct Job { bool used; char text[96]; bool manual; };

static Job s_cur = {}, s_next = {};
static uint8_t s_attempt = 0;
static uint32_t s_ts = 0;
static uint32_t s_expected_ack = 0;
static uint32_t s_deadline = 0;
static bool s_waiting = false;
static MtSendStats s_stats = {};
static MtSendDone s_done_cb = nullptr;

static ContactInfo* target_contact(bool create) {
  if (!mt_cfg.target_set) return nullptr;
  ContactInfo* c = the_mesh.lookupContactByPubKey(mt_cfg.target, PUB_KEY_SIZE);
  if (c || !create) return c;
  ContactInfo n;
  memset(&n, 0, sizeof(n));
  n.id = mesh::Identity(mt_cfg.target);
  strncpy(n.name, "MeshTrack-server", sizeof(n.name) - 1);
  n.type = ADV_TYPE_CHAT;
  n.out_path_len = OUT_PATH_UNKNOWN;
  n.lastmod = rtc_clock.getCurrentTime();
  if (!the_mesh.addContact(n)) return nullptr;
  the_mesh.mtSaveContacts();
  return the_mesh.lookupContactByPubKey(mt_cfg.target, PUB_KEY_SIZE);
}

bool mt_sender_ensure_contact() { return target_contact(true) != nullptr; }

static void finish(bool ok) {
  s_waiting = false;
  if (ok) { s_stats.ok++; s_stats.last_ok = true; }
  else { s_stats.failed++; s_stats.last_ok = false; }
  s_stats.last_attempts = s_attempt + 1;
  s_stats.last_ms = millis();
  s_stats.have_last = true;
  bool manual = s_cur.manual;
  s_cur.used = false;
  if (s_done_cb) s_done_cb(ok, manual);
  if (s_next.used) { s_cur = s_next; s_next.used = false; s_attempt = 0; s_ts = 0; }
}

static bool transmit() {
  ContactInfo* c = target_contact(true);
  if (!c) { s_stats.no_target++; return false; }
  // Laatste poging: pad vergeten zodat het via flood gaat (zoals de app).
  if (s_attempt > 0 && s_attempt >= mt_cfg.ack_retries) the_mesh.resetPathTo(*c);
  if (s_ts == 0) s_ts = rtc_clock.getCurrentTimeUnique();
  uint32_t est = 0;
  int r = the_mesh.sendMessage(*c, s_ts, s_attempt, s_cur.text, s_expected_ack, est);
  if (r == MSG_SEND_FAILED) return false;
  s_stats.sent_packets++;
  // Wat speling bovenop de schatting van de mesh, en nooit korter dan 4 s.
  uint32_t wait = est + est / 4;
  if (wait < 4000) wait = 4000;
  s_deadline = millis() + wait;
  s_waiting = true;
  return true;
}

void mt_sender_set_done_cb(MtSendDone cb) { s_done_cb = cb; }

bool mt_send(const char* text, bool manual) {
  Job j;
  j.used = true;
  j.manual = manual;
  strncpy(j.text, text, sizeof(j.text) - 1);
  j.text[sizeof(j.text) - 1] = 0;
  if (s_cur.used) {
    if (s_next.used) s_stats.replaced++;
    s_next = j;
    return true;
  }
  s_cur = j;
  s_attempt = 0;
  s_ts = 0;
  if (!transmit()) { finish(false); return false; }
  return true;
}

void mt_sender_loop() {
  if (!s_cur.used) return;
  if (!s_waiting) {               // volgend bericht uit het slot starten
    if (!transmit()) finish(false);
    return;
  }
  if ((int32_t)(millis() - s_deadline) < 0) return;
  if (s_attempt < mt_cfg.ack_retries) {
    s_attempt++;
    s_stats.retries++;
    if (!transmit()) finish(false);
  } else {
    finish(false);
  }
}

bool mt_sender_busy() { return s_cur.used; }
const MtSendStats& mt_sender_stats() { return s_stats; }

ContactInfo* mt_on_ack(const uint8_t* data) {
  if (!s_waiting || memcmp(data, &s_expected_ack, 4) != 0) return nullptr;
  ContactInfo* c = target_contact(false);
  finish(true);
  return c;
}
