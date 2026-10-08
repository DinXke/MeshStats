// De tracker: wanneer GPS aan, wanneer zenden, wanneer slapen.
//
//   SLEEP ──(beweging)──> ACQUIRE(wakker) ──(fix)──> zend M ──> MOVING
//   ACQUIRE ──(fix_timeout)──> zend N ──> MOVING (blijft zoeken)
//   MOVING ──(regels, MtRules)──> zend M
//   MOVING ──(geen beweging >= still_timeout)──> zend S ──> SLEEP
//   SLEEP ──(heartbeat)──> ACQUIRE(heartbeat, korte timeout) ──> zend H of N ──> SLEEP
//   enkele klik ──> fix (max fix_timeout_hb) ──> zend P of N, met biep-terugmelding
//
// Terugmelding (klik en eerste SOS): een kanaalbericht heeft geen ACK, maar de tracker
// hoort een repeater die zijn flood-pakket herhaalt (zelfde eerste cijferblok). Gehoord
// binnen MT_HEAR_MS = twee hoge biepjes, anders een lage toon. Op een SOS antwoordt de
// server op hetzelfde kanaal met "T1A|<pk8>|<tag8>|<seq>": dan klinkt MT_TUNE_SOSOK.
//
// In trackermodus slaapt ook de radio zodra er niets te verzenden of te
// ontvangen valt (na een kort luistervenster); de mesh-lus draait dan niet. In
// companionmodus blijft de radio altijd luisteren (stock gedrag).
#include "MtTracker.h"
#include "MeshTrack.h"
#include "MtConfig.h"
#include "MtRules.h"
#include "MtSender.h"
#include "MtMotion.h"
#include "MtBattery.h"
#include "MtGps.h"
#include "MyMesh.h"
#include "UITask.h"
#include <SHA256.h>
#include <Adafruit_LittleFS.h>
#include <InternalFileSystem.h>
using namespace Adafruit_LittleFS_Namespace;

extern UITask ui_task;

enum Purpose : uint8_t { PUR_WAKE, PUR_HEARTBEAT };

static MtTState s_state = MT_T_OFF;
static Purpose s_purpose = PUR_WAKE;
static uint32_t s_acq_deadline = 0;
static uint32_t s_last_eval_fix = 0;
static uint32_t s_still_since = 0;          // 0 = beweegt
static uint32_t s_sleep_since = 0;
static uint32_t s_last_hb_s = 0;
static MtRuleState s_rules;
static uint16_t s_seq = 0;
static uint16_t s_seq_saved = 0;
static bool s_gps_ours = false;             // wij hebben de GPS aangezet
static bool s_manual = false;
static uint32_t s_manual_deadline = 0;
static uint32_t s_manual_last = 0;
static bool s_radio_asleep = false;
static uint32_t s_quiet_since = 0;
static char s_last_reason[16] = "-";
static uint32_t s_last_tx_ms = 0;
static uint32_t s_listen_until = 0;         // radio wakker houden tot (herhaling / T1A afwachten)

// herhalingen van het laatst bewaakte bericht (klik of eerste SOS)
static uint8_t s_w_block[16];
static bool s_w_have = false;               // er is een bewaakt bericht (gehoord= telt)
static bool s_w_wait = false;               // wacht nog op de eerste herhaling (terugmelding)
static uint32_t s_w_deadline = 0;
static uint16_t s_w_heard = 0;

// SOS-volgnummers van de laatste SOS-reeks (3 berichten) en welke de server bevestigde
static uint16_t s_sos_seq[3];
static uint8_t s_sos_n = 0, s_sos_conf = 0;  // s_sos_conf: bit per plaats in s_sos_seq
static bool s_sos_any = false;

static const char* SEQ_PATH = "/mt_seq.dat";
#define SEQ_STEP 64

static uint32_t now_s() { return millis() / 1000; }

// ---- volgnummer (overleeft een herstart, anders gooit de server dubbels weg) ---

static void seq_save(uint16_t v) {
  mt_file_write(SEQ_PATH, &v, 2);
  s_seq_saved = v;
}

static void seq_begin() {
  uint16_t v = 0;
  if (!mt_file_read(SEQ_PATH, &v, 2)) v = 0;
  // Na een herstart altijd voorbij alles wat al verstuurd kan zijn.
  s_seq = (uint16_t)(v + SEQ_STEP);
  seq_save(s_seq);
}

static uint16_t seq_next() {
  uint16_t v = s_seq++;
  if ((uint16_t)(s_seq - s_seq_saved) >= SEQ_STEP) seq_save(s_seq);
  return v;
}

// HMAC-SHA256(authsleutel, body), eerste 4 bytes als 8 hex; zonder sleutel "-".
static void auth_tag(const char* body, char out[9]) {
  if (!mt_cfg.authkey_set) { strcpy(out, "-"); return; }
  SHA256 sha;
  uint8_t mac[4];
  sha.resetHMAC(mt_cfg.authkey, 16);
  sha.update(body, strlen(body));
  sha.finalizeHMAC(mt_cfg.authkey, 16, mac, 4);
  snprintf(out, 9, "%02x%02x%02x%02x", mac[0], mac[1], mac[2], mac[3]);
}

static void own_pk8(char out[9]) {
  for (int i = 0; i < 4; i++) snprintf(out + 2 * i, 3, "%02x", the_mesh.self_id.pub_key[i]);
}

// ---- GPS ----------------------------------------------------------------------

static bool gps_is_on() { return strcmp(sensors.getSettingValue(0), "1") == 0; }

static void gps_want(bool on) {
  if (on && !gps_is_on()) { sensors.setSettingValue("gps", "1"); s_gps_ours = true; }
  else if (!on && s_gps_ours) { if (gps_is_on()) sensors.setSettingValue("gps", "0"); s_gps_ours = false; }
}

// ---- radio --------------------------------------------------------------------

static void radio_wake() {
  if (!s_radio_asleep) return;
  radio_driver.onSendFinished();     // standby + "start opnieuw met ontvangen"
  s_radio_asleep = false;
  s_quiet_since = millis();
}

bool mt_radio_paused() { return s_radio_asleep; }

// ---- punten bewaren -------------------------------------------------------------
// In beweging elke sample_s een punt; elk bericht met een positie neemt zoveel eerdere
// punten mee als in 156 tekens past (oudste eerst, als dt,dlat,dlon,spd t.o.v. het
// hoofdpunt). Pas als het bericht echt verstuurd is verdwijnen ze uit de buffer: kon
// het niet weg (geen trackingkanaal), dan reizen zijn punten mee met het volgende.
// Een kanaalbericht heeft geen ACK; of het aankwam, weet de tracker niet.
struct MtPt { uint32_t ts; int32_t lat, lon; uint16_t spd; };   // lat/lon in 1e-5 graden
#define MT_PTS 24
static MtPt s_pts[MT_PTS];
static uint8_t s_npts = 0;

static void pts_push(uint32_t ts, double la, double lo, float sp) {
  if (!ts) return;
  if (s_npts && s_pts[s_npts - 1].ts >= ts) return;            // al bewaard
  if (s_npts == MT_PTS) { memmove(s_pts, s_pts + 1, sizeof(MtPt) * (MT_PTS - 1)); s_npts--; }
  s_pts[s_npts++] = { ts, (int32_t)lround(la * 1e5), (int32_t)lround(lo * 1e5), (uint16_t)(sp < 0 ? 0 : sp + 0.5f) };
}

static void pts_sent(uint32_t upto) {
  uint8_t k = 0;
  while (k < s_npts && s_pts[k].ts <= upto) k++;
  if (k) { memmove(s_pts, s_pts + k, sizeof(MtPt) * (s_npts - k)); s_npts -= k; }
}

static void pts_sample(MtNmeaProvider& g) {
  if (!mt_cfg.sample_s || !g.lastValidUnix()) return;
  uint32_t ts = g.lastValidUnix();
  if (s_npts && ts - s_pts[s_npts - 1].ts < mt_cfg.sample_s) return;
  double la = g.getLatitude() / 1e6, lo = g.getLongitude() / 1e6;
  if (s_npts && g.speedKmh() < 3 &&
      mt_haversine_m(s_pts[s_npts - 1].lat / 1e5, s_pts[s_npts - 1].lon / 1e5, la, lo) < 5) return;   // stil: geen dubbele punten
  pts_push(ts, la, lo, g.speedKmh());
}

uint8_t mt_tracker_buffered() { return s_npts; }

// ---- berichten ----------------------------------------------------------------

static bool tracking_active() {
  MtMode m = mt_effective_mode();
  return m == MT_MODE_TRACKER || mt_cfg.track_in_companion;
}

static void send_report(char state, bool with_pos, bool manual, const char* reason) {
  MtNmeaProvider& g = mt_gps();
  char lat[16] = "", lon[16] = "", alt[8] = "", spd[8] = "", crs[8] = "", hd[8] = "", age[12] = "", fts[12] = "";
  double la = 0, lo = 0;
  float sp = -1, cr = -1;
  if (with_pos && g.lastValidMs() != 0) {
    la = g.getLatitude() / 1e6;
    lo = g.getLongitude() / 1e6;
    sp = g.speedKmh();
    cr = g.courseDeg();
    snprintf(lat, sizeof(lat), "%.5f", la);
    snprintf(lon, sizeof(lon), "%.5f", lo);
    snprintf(alt, sizeof(alt), "%ld", g.getAltitude() / 1000);
    snprintf(spd, sizeof(spd), "%d", (int)(sp + 0.5f));
    if (sp >= 3 && cr >= 0) snprintf(crs, sizeof(crs), "%d", ((int)(cr + 0.5f)) % 360);
    snprintf(hd, sizeof(hd), "%.1f", g.hdop());
    snprintf(age, sizeof(age), "%lu", (unsigned long)(g.fixAgeMs() / 1000));
    if (g.lastValidUnix()) snprintf(fts, sizeof(fts), "%lu", (unsigned long)g.lastValidUnix());
  } else {
    with_pos = false;
  }
  int bat = mt_battery_pct(board.getBattMilliVolts());
  char text[MT_TEXT_MAX + 1];
  // 13e veld: voeding (u = USB/laden, b = batterij). Elk bericht draagt het mee,
  // zodat de server een gemiste in/uitplug-melding bij het volgende bericht inhaalt.
  // 14e veld: GPS-tijd van de fix. Zo staat de positie op het juiste moment, ook na
  // als het bericht even in de wachtrij stond.
  uint16_t seq = seq_next();
  int n = snprintf(text, sizeof(text), "T1|%u|%c|%s|%s|%s|%s|%s|%d|%s|%s|%c|%c|%s",
           (unsigned)seq, state, lat, lon, alt, spd, crs, bat < 0 ? 0 : bat, hd, age,
           mt_effective_mode() == MT_MODE_TRACKER ? 't' : 'c', mt_usb() ? 'u' : 'b', fts);
  // Kanaal: "T1C|<pubkey 8 hex>|<handtekening 8 hex>|<seq>|..." (zonder "T1|"); de
  // handtekening = HMAC-SHA256(authsleutel, "<pubkey8>|<rest>"), eerste 4 bytes.
  // "<naam>: " gaat ervoor (MAX_TEXT_LEN omvat de naam), plus T1C|pk|tag| (min "T1|") en 2 bytes marge.
  const int limit = MT_TEXT_MAX - the_mesh.mtSenderLen() - 19 - 2;
  // 15e veld: eerdere punten, compact. "~<interval>" en daarna per punt het verschil met het
  // vorige (nieuwste eerst, te beginnen bij het hoofdpunt) in 1e-5 graden: ";dlat,dlon".
  // Wijkt de tijd tussen twee punten af van het interval, dan volgt "@<seconden>".
  // De server berekent de snelheid uit afstand en tijd. Zo passen een negental punten.
  uint32_t tag = 0, main_ts = with_pos ? g.lastValidUnix() : 0;
  if (main_ts) {
    int32_t pla = lround(la * 1e5), plo = lround(lo * 1e5);
    uint32_t pts = main_ts;
    int k = (int)s_npts - 1;
    while (k >= 0 && s_pts[k].ts >= main_ts) k--;
    if (k >= 0) {
      uint32_t step = mt_cfg.sample_s ? mt_cfg.sample_s : pts - s_pts[k].ts;
      char head[16];
      int hm = snprintf(head, sizeof(head), "|~%lu", (unsigned long)step);
      if (n + hm + 6 <= limit) {
        memcpy(text + n, head, hm + 1);
        n += hm;
        for (; k >= 0; k--) {
          const MtPt& q = s_pts[k];
          uint32_t gap = pts - q.ts;
          char item[40];
          int m = gap == step ? snprintf(item, sizeof(item), ";%ld,%ld", (long)(q.lat - pla), (long)(q.lon - plo))
                              : snprintf(item, sizeof(item), ";%ld,%ld@%lu", (long)(q.lat - pla), (long)(q.lon - plo),
                                         (unsigned long)gap);
          if (n + m > limit) break;            // oudere punten passen niet meer
          memcpy(text + n, item, m + 1);
          n += m;
          pla = q.lat; plo = q.lon; pts = q.ts;
        }
      }
    }
    pts_push(main_ts, la, lo, sp);          // ook het hoofdpunt: bij een misser gaat het mee met het volgende
    tag = main_ts;
  }
  radio_wake();
  // Belangrijke berichten wijken niet voor een nieuwere gewone positie.
  bool keep = manual || state == 'E' || state == 'S' || state == 'H' || state == 'B';
  {
    char pk[9], sig[9], body[MT_TEXT_MAX + 1];
    own_pk8(pk);
    snprintf(body, sizeof(body), "%s|%s", pk, text + 3);          // text begint met "T1|"
    auth_tag(body, sig);
    snprintf(text, sizeof(text), "T1C|%s|%s|%s", pk, sig, body + 9);
  }
  // Eigen positie ook naar een verbonden app (alleen als companion: dan staat Bluetooth aan).
  if (mt_effective_mode() == MT_MODE_COMPANION) the_mesh.mtQueueOwn(mt_cfg.chan_idx, text);
  mt_send(text, manual, keep, tag);
  s_rules.sent(now_s(), with_pos, la, lo, sp >= 3 ? cr : -1);
  strncpy(s_last_reason, reason, sizeof(s_last_reason) - 1);
  s_last_tx_ms = millis();
  if (state == 'E') {
    if (s_sos_n < 3) s_sos_seq[s_sos_n++] = seq;
    s_listen_until = millis() + 30000;     // radio wakker: antwoord (T1A) van de server afwachten
  }
  mt_log("tx %s (%s)", text, reason);
}

static MtRuleParams params();

// Een kanaalbericht kent geen ACK: "ok" = de radio heeft het verstuurd. Met terugmelding
// (manual) bewaken we daarna of een repeater het herhaalt.
static void on_send_done(bool ok, bool manual, uint32_t tag) {
  if (ok && tag) pts_sent(tag);
  if (manual && !ok) ui_task.playForced(MT_TUNE_NOK);
  if (manual && ok) {
    memcpy(s_w_block, mt_sender_last_block(), 16);
    s_w_have = s_w_wait = true;
    s_w_heard = 0;
    s_w_deadline = millis() + MT_HEAR_MS;
    if ((int32_t)(s_w_deadline - s_listen_until) > 0) s_listen_until = s_w_deadline;
  }
  if (ok) mt_log("tx op kanaal %u verstuurd", (unsigned)mt_cfg.chan_idx);
  else mt_log("tx MISLUKT: trackingkanaal %u ontbreekt op het toestel", (unsigned)mt_cfg.chan_idx);
}

static void step_watch() {
  if (!s_w_wait || (int32_t)(millis() - s_w_deadline) < 0) return;
  s_w_wait = false;
  ui_task.playForced(MT_TUNE_NOK);
  mt_log("geen herhaling gehoord binnen %u s", (unsigned)(MT_HEAR_MS / 1000));
}

// Ruw pakket: [header][4 transportcodes bij route 0/3][path_len][pad][payload]. GRP_TXT-payload
// = [kanaalhash 1][MAC 2][cijferblokken...]; is het eerste blok het onze, dan is dit een herhaling.
void mt_rx_raw(const uint8_t raw[], int len) {
  if (!s_w_have || len < 2) return;
  uint8_t route = raw[0] & 3;
  if (((raw[0] >> 2) & 15) != PAYLOAD_TYPE_GRP_TXT) return;
  int i = 1 + ((route == 0 || route == 3) ? 4 : 0);
  if (i >= len) return;
  uint8_t pl = raw[i++];
  int hs = (pl >> 6) + 1, hc = pl & 63;
  if (hs > 3) return;
  int path = i;
  i += hs * hc;
  if (i + 3 + 16 > len || memcmp(raw + i + 3, s_w_block, 16) != 0) return;
  s_w_heard++;
  char rep[7] = "-";
  if (hc) for (int k = 0; k < hs; k++) snprintf(rep + 2 * k, 3, "%02x", raw[path + (hc - 1) * hs + k]);
  mt_log("gehoord via repeater %s (%u hops)", rep, (unsigned)hc);
  if (s_w_wait) { s_w_wait = false; ui_task.playForced(MT_TUNE_OK); }
}

// "<naam>: T1A|<pk8>|<tag8>|<seq>" op het trackingkanaal: de server bevestigt een SOS.
// tag8 = HMAC-SHA256(authsleutel, "<pk8>|A|<seq>"), eerste 4 bytes.
bool mt_channel_text(uint8_t chan_idx, const char* text) {
  if (chan_idx != mt_cfg.chan_idx) return false;
  const char* p = strstr(text, ": ");
  if (!p || strncmp(p + 2, "T1A|", 4) != 0) return false;
  p += 6;
  char pk[9], body[24], tag[9];
  own_pk8(pk);
  if (strncmp(p, pk, 8) != 0 || p[8] != '|' || strlen(p) < 19 || p[17] != '|') return true;   // niet voor ons
  char* end;
  unsigned long seq = strtoul(p + 18, &end, 10);
  if (end == p + 18 || *end || seq > 0xFFFF) return true;
  snprintf(body, sizeof(body), "%s|A|%lu", pk, seq);
  auth_tag(body, tag);
  if (!mt_cfg.authkey_set || strncmp(p + 9, tag, 8) != 0) { mt_log("T1A met foute handtekening genegeerd"); return true; }
  for (uint8_t k = 0; k < s_sos_n; k++) {
    if (s_sos_seq[k] != seq) continue;
    if (s_sos_conf & (1 << k)) return true;     // al bevestigd (dubbel antwoord)
    s_sos_conf |= 1 << k;
    ui_task.playForced(MT_TUNE_SOSOK);
    mt_log("SOS bevestigd door de server (seq %lu)", seq);
    return true;
  }
  mt_log("T1A voor onbekende SOS (seq %lu) genegeerd", seq);
  return true;
}

// ---- toestanden ---------------------------------------------------------------

static void enter(MtTState st) {
  s_state = st;
  switch (st) {
    case MT_T_SLEEP:
      s_sleep_since = millis();
      s_still_since = 0;
      gps_want(s_manual);
      break;
    case MT_T_ACQUIRE:
      gps_want(true);
      s_acq_deadline = millis() + 1000UL * (s_purpose == PUR_HEARTBEAT ? mt_cfg.fix_timeout_hb_s : mt_cfg.fix_timeout_s);
      break;
    case MT_T_MOVING:
      gps_want(true);
      s_still_since = 0;
      break;
    case MT_T_OFF:
      gps_want(s_manual);
      break;
  }
}

static MtRuleParams params() {
  MtRuleParams p;
  p.min_speed_kmh = mt_cfg.min_speed_kmh;
  p.min_dist_m = mt_cfg.min_dist_m;
  p.turn_min_deg = mt_cfg.turn_min_deg;
  p.turn_min_speed_kmh = mt_cfg.turn_min_speed_kmh;
  p.min_interval_s = mt_cfg.min_interval_s;
  p.max_interval_s = mt_cfg.max_interval_s;
  return p;
}

static bool moving_now(float spd) {
  if (mt_motion_available()) return millis() - mt_motion_last() < 15000;
  return spd >= MT_STILL_KMH;
}

static void step_tracking() {
  MtNmeaProvider& g = mt_gps();
  bool new_fix = g.freshFix(2500) && g.lastValidMs() != s_last_eval_fix;
  uint32_t now = millis();

  switch (s_state) {
    case MT_T_OFF:
      enter(MT_T_MOVING);
      break;

    case MT_T_SLEEP: {
      bool motion = mt_motion_available() ? (int32_t)(mt_motion_last() - s_sleep_since) > 0 : false;
      if (motion) { s_purpose = PUR_WAKE; enter(MT_T_ACQUIRE); break; }
      if (mt_cfg.heartbeat_s > 0) {
        uint32_t ref = s_last_hb_s ? s_last_hb_s : s_rules.last_tx_s;
        if (now_s() - ref >= mt_cfg.heartbeat_s) { s_purpose = PUR_HEARTBEAT; enter(MT_T_ACQUIRE); }
      }
      break;
    }

    case MT_T_ACQUIRE:
      if (new_fix) {
        s_last_eval_fix = g.lastValidMs();
        if (moving_now(g.speedKmh())) pts_sample(g);
        if (s_purpose == PUR_HEARTBEAT) {
          send_report('H', true, false, "heartbeat");
          s_last_hb_s = now_s();
          if (moving_now(g.speedKmh())) enter(MT_T_MOVING); else enter(MT_T_SLEEP);
        } else {
          send_report('W', true, false, "wakker");     // eerste positie na rust: wakker door beweging
          enter(MT_T_MOVING);
        }
      } else if ((int32_t)(now - s_acq_deadline) >= 0) {
        send_report('N', false, false, "geen fix");
        if (s_purpose == PUR_HEARTBEAT) { s_last_hb_s = now_s(); enter(MT_T_SLEEP); }
        else enter(MT_T_MOVING);
      }
      break;

    case MT_T_MOVING: {
      float spd = g.freshFix(5000) ? g.speedKmh() : 0;
      if (spd >= MT_STILL_KMH) mt_motion_touch();
      if (!moving_now(spd) && !mt_motion_available() && mt_cfg.heartbeat_s > 0 &&
          now_s() - s_rules.last_tx_s >= mt_cfg.heartbeat_s) {
        // Geen bewegingssensor: hij slaapt nooit, maar meldt in rust toch zijn heartbeat.
        send_report('H', g.freshFix(5000), false, "heartbeat");
      }
      if (moving_now(spd)) {
        s_still_since = 0;
      } else if (s_still_since == 0) {
        s_still_since = now ? now : 1;
      } else if (mt_motion_available() && now - s_still_since >= 1000UL * mt_cfg.still_timeout_s) {
        // Stil: laatste positie melden en slapen. Zonder werkende bewegings-
        // sensor nooit slapen: dan zou hij niet meer wakker worden.
        send_report('S', g.freshFix(30000), false, "stil");
        enter(MT_T_SLEEP);
        break;
      }
      if (new_fix) {
        s_last_eval_fix = g.lastValidMs();
        if (moving_now(g.speedKmh())) pts_sample(g);     // punt bewaren voor het volgende bericht
        double la = g.getLatitude() / 1e6, lo = g.getLongitude() / 1e6;
        float cr = g.speedKmh() >= 3 ? g.courseDeg() : -1;
        MtReason r = mt_rules_decide(s_rules, params(), now_s(), la, lo, g.speedKmh(), cr);
        if (r != MT_R_NONE && moving_now(g.speedKmh())) send_report('M', true, false, mt_reason_str(r));
      }
      break;
    }
  }
}

// ---- SOS: meteen (laatst gekende positie als er geen verse fix is), daarna
// nog twee keer met een verse positie, telkens 60 s later. Negeert de rate limit.
static uint8_t s_sos_left = 0;
static uint32_t s_sos_next = 0;

static void step_sos() {
  if (!s_sos_left || (int32_t)(millis() - s_sos_next) < 0) return;
  bool first = s_sos_left == 3;              // alleen de eerste met terugmelding (minder lawaai)
  s_sos_left--;
  s_sos_next = millis() + 60000;
  MtNmeaProvider& g = mt_gps();
  send_report('E', g.lastValidMs() != 0, first, "SOS");
}

bool mt_tracker_sos() {
  if (!mt_sender_ready()) return false;
  gps_want(true);
  radio_wake();
  s_sos_left = 3;
  s_sos_next = millis();
  s_sos_n = s_sos_conf = 0;                   // nieuwe reeks
  s_sos_any = true;
  step_sos();
  return true;
}

static void step_manual() {
  if (!s_manual) return;
  MtNmeaProvider& g = mt_gps();
  if (g.freshFix(3000)) {
    s_manual = false;
    send_report('P', true, true, "knop");
  } else if ((int32_t)(millis() - s_manual_deadline) >= 0) {
    s_manual = false;
    send_report('N', false, true, "knop, geen fix");
  }
  if (!s_manual && (s_state == MT_T_SLEEP || s_state == MT_T_OFF)) gps_want(false);
}

static void step_radio() {
  bool want_sleep = mt_effective_mode() == MT_MODE_TRACKER && !mt_sender_busy() && !s_manual && !s_sos_left &&
                    (int32_t)(millis() - s_listen_until) >= 0 && !the_mesh.hasPendingWork();
  if (!want_sleep) { s_quiet_since = 0; radio_wake(); return; }
  if (s_radio_asleep) return;
  if (s_quiet_since == 0) { s_quiet_since = millis(); return; }
  // Nog even luisteren na het laatste verkeer (berichten voor de app, padantwoord).
  if (millis() - s_quiet_since < 3000) return;
  radio_driver.powerOff();
  s_radio_asleep = true;
}

// ---- publiek --------------------------------------------------------------------

void mt_tracker_begin() {
  seq_begin();
  mt_sender_set_done_cb(on_send_done);
  mt_motion_begin(mt_cfg.accel_sens);
  s_state = MT_T_OFF;
}

void mt_tracker_loop() {
  mt_motion_loop();
  mt_sender_loop();
  if (tracking_active()) step_tracking();
  else if (s_state != MT_T_OFF) enter(MT_T_OFF);
  step_manual();
  step_sos();
  step_watch();
  step_radio();
}

bool mt_tracker_manual() {
  if (s_manual || millis() - s_manual_last < 10000) return false;   // max 1x per 10 s
  s_manual_last = millis();
  if (!mt_sender_ready()) return false;
  s_manual = true;
  s_manual_deadline = millis() + 1000UL * mt_cfg.fix_timeout_hb_s;
  gps_want(true);
  radio_wake();
  step_manual();      // verse fix? dan meteen
  return true;
}

void mt_tracker_mode_changed() {
  if (!mt_sender_ready()) return;
  send_report('B', false, false, "modus");
}

// USB in of uit. Een B-bericht, ook als de modus niet wisselt; hooguit eens per
// 30 s (een wiebelende stekker mag de mesh niet vullen).
void mt_tracker_power_changed() {
  static uint32_t last = 0;
  if (!mt_sender_ready() || (last && millis() - last < 30000)) return;
  last = millis();
  send_report('B', mt_gps().freshFix(60000), false, mt_usb() ? "USB in" : "USB uit");
}

MtTState mt_tracker_state() { return s_state; }

const char* mt_tracker_state_str() {
  switch (s_state) {
    case MT_T_SLEEP: return "slaapt";
    case MT_T_ACQUIRE: return s_purpose == PUR_HEARTBEAT ? "fix zoeken (heartbeat)" : "fix zoeken";
    case MT_T_MOVING: return "beweegt";
    default: return "uit";
  }
}

const char* mt_tracker_last_reason() { return s_last_reason; }
uint32_t mt_tracker_last_tx_ms() { return s_last_tx_ms; }
uint16_t mt_tracker_seq() { return s_seq; }
int mt_tracker_heard() { return s_w_have ? s_w_heard : -1; }
const char* mt_tracker_sos_confirmed() { return !s_sos_any ? "-" : s_sos_conf ? "ja" : "nee"; }
bool mt_tracker_gps_on() { return gps_is_on(); }
