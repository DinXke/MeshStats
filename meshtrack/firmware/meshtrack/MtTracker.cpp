// De tracker: wanneer GPS aan, wanneer zenden, wanneer slapen.
//
// FastTrack (de toestandsmachine, ongewijzigd sinds 0.7):
//   SLEEP ──(beweging)──> ACQUIRE(wakker) ──(fix)──> zend W ──> MOVING
//   ACQUIRE ──(fix_timeout)──> zend N ──> MOVING (blijft zoeken)
//   MOVING ──(regels, MtRules)──> zend M
//   MOVING ──(geen beweging >= still_timeout)──> zend S ──> SLEEP
//   SLEEP ──(heartbeat)──> ACQUIRE(heartbeat, korte timeout) ──> zend H of N ──> SLEEP
//   enkele klik ──> fix (max fix_timeout_hb) ──> zend P of N, met biep-terugmelding
//   batterij < fast_min_batt ──> SLEEP zonder S-bericht; beweging wekt hem niet meer, de
//     heartbeat wel (daarna altijd terug naar SLEEP). Terug aan vanaf fast_min_batt + 3 %.
//
// SlowTrack (0.8, los van FastTrack, ook als die slaapt of uit staat wegens de batterij):
//   elke slow_log ──> punt in de eigen buffer (64 punten); geen verse fix, dan de GPS hooguit
//     fix_timeout_hb aan. Minder dan 5 m van het vorige punt en < 3 km/u = geen nieuw punt.
//   elke slow_send ──> één L-bericht: hoofdpunt = het nieuwste punt, de oudere als extra
//     punten (zoals M). Passen ze niet allemaal, dan wordt gelijkmatig uitgedund (oudste en
//     nieuwste blijven). Verstuurd = buffer leeg; mislukt = buffer blijft voor de volgende keer.
//   De GPS gaat weer uit als FastTrack, een klik of een SOS hem niet nodig heeft.
//
// Terugmelding (klik en eerste SOS): een kanaalbericht heeft geen ACK, maar de tracker
// hoort een repeater die zijn flood-pakket herhaalt (zelfde eerste cijferblok). Gehoord
// binnen MT_HEAR_MS = twee hoge biepjes, anders een lage toon. Op een SOS antwoordt de
// server op hetzelfde kanaal met "T1A|<pk8>|<tag8>|<seq>": dan klinkt MT_TUNE_SOSOK.
// Met heard_beep worden ook de automatische berichten (niet klik/SOS) bewaakt: herhaald =
// twee hoge biepjes (één keer per bericht), niet gehoord = stil. tx_beep: korte biep zodra
// de radio een automatisch positiebericht echt verzonden heeft (logTx).
//
// Trackmodus (0.9, track_mode):
//   classic ──> FastTrack + SlowTrack zoals hierboven (slow_send verstuurt de SlowTrack-buffer).
//   fifo    ──> wachtrij (FIFO, op tijd gesorteerd, fifo_max punten, bewaard in /mt_fifo.dat):
//     FastTrack-bericht met positie ──(radio verzendt)──> MT_HEAR_MS luisteren
//        ──(herhaling gehoord)──> zijn punten zijn binnen (weg uit de FastTrack-buffer)
//        ──(stil)──> punten blijven in de buffer en liften mee met de volgende berichten; pas als
//           een hoofdpunt de buffer verlaat zonder ooit herhaald te zijn: dat hoofdpunt de wachtrij in
//     SlowTrack-punt (slow_log) ──> meteen de wachtrij in (zelfde rustregel); geen slow_send
//     dekking = een herhaling van een eigen pakket, of eender welk flood-pakket met >= 1 hop;
//       stabiel (voor een nieuwe ronde) = SNR >= fifo_snr, twee keer binnen 60 s, of een T1F
//     NIET ACTIEF ──(>= fifo_min punten, of >= 1 als het eerder gestopt is, én dekking < 30 s,
//        én na de laatste stop)──> LEEGMAKEN
//     vol = het minst betekenisvolle punt weg; fifo_dun = overbodige punten op een rechte lijn weg
//     LEEGMAKEN ──(elke fifo_gap, hooguit fifo_per_uur per uur)──> één Q-bericht met de oudste
//        punten (hoofdpunt = het nieuwste van dat deel, de oudere binair met hun tijd)
//        ──(herhaald)──> die punten uit de wachtrij, bewaren, volgende
//        ──(niet herhaald)──> punten blijven, pogingen + 1, wachttijd 1/5/15/60 min, NIET ACTIEF
//           (wacht op nieuwe dekking). Vanaf fifo_pogingen = geparkeerd: lagere voorrang en 60 min
//           wachttijd, maar NOOIT opgegeven (0.9.4). Thuis/companion met sterke dekking: teller op 0.
//     T1F van de server per volgnummer (elk Q-bericht draagt "g", plus "f" als er onbevestigde punten
//        zijn) = precies de punten van die berichten zijn binnen. Het oude upto_ts wordt genegeerd.
//     Vol bericht (>= punten per bericht klaar) = gewone regels; niet-vol bericht (0.9.3) alleen als het
//        laatste geslaagde inhaalbericht EN de laatste niet-volle poging minstens fifo_wacht geleden zijn,
//        de laatste dekking sterk was (SNR >= fifo_snr of T1F) en de radio toch al wakker is.
//     Leegmaakberichten tellen niet als FastTrack-misser (geen kringloop).
//
// In trackermodus slaapt ook de radio zodra er niets te verzenden of te
// ontvangen valt (na een kort luistervenster); de mesh-lus draait dan niet. In
// companionmodus blijft de radio altijd luisteren (stock gedrag). Tijdens het
// leegmaken van de FIFO blijft de radio wakker (behalve als het uurplafond bereikt is).
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
static bool s_slow_gps = false;             // SlowTrack zoekt een fix: de GPS niet uitzetten
static bool s_fast_off = false;             // FastTrack uit wegens de batterij (fast_min_batt)
static uint8_t s_sos_left = 0;              // SOS-berichten die nog moeten volgen
static uint32_t s_sos_next = 0;

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

// FastTrack en SlowTrack vragen elk de GPS; hij gaat pas uit als geen van beide hem nodig heeft.
static void gps_want(bool on) {
  if (on && !gps_is_on()) { sensors.setSettingValue("gps", "1"); s_gps_ours = true; }
  else if (!on && s_gps_ours && !s_slow_gps) { if (gps_is_on()) sensors.setSettingValue("gps", "0"); s_gps_ours = false; }
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
// fifo (0.9): de punten blijven tot een bericht dat ze droeg HERHAALD is (meeliften: korte gaten
// vullen de volgende berichten gratis op). Hoofdpunten (PT_MAIN) die de buffer verlaten zonder ooit
// in een herhaald bericht te zitten (buffer vol, past niet meer in het venster, of ouder dan
// 10 min) gaan de FIFO in; gewone tussenpunten vallen dan weg, zoals voorheen.
#define MT_ALT_NONE  (-32768)          // hoogte onbekend
#define PT_MAIN      1                 // hoofdpunt van een bericht
struct MtPt { uint32_t ts; int32_t lat, lon; uint16_t spd; int16_t alt; uint8_t fl; };   // lat/lon in 1e-5 graden
#define MT_PTS 24
static MtPt s_pts[MT_PTS];
static uint8_t s_npts = 0;
static bool fifo_mode();
static void fifo_take_main(const MtPt& q);

static void pts_push(uint32_t ts, double la, double lo, float sp, long alt = MT_ALT_NONE, bool main = false) {
  if (!ts) return;
  if (s_npts && s_pts[s_npts - 1].ts == ts && main) {          // zelfde fix al als tussenpunt bewaard
    s_pts[s_npts - 1].fl |= PT_MAIN;
    if (alt != MT_ALT_NONE) s_pts[s_npts - 1].alt = (int16_t)(alt < -1000 ? -1000 : alt > 20000 ? 20000 : alt);
    return;
  }
  if (s_npts && s_pts[s_npts - 1].ts >= ts) return;            // al bewaard
  if (s_npts == MT_PTS) {
    if (fifo_mode() && (s_pts[0].fl & PT_MAIN)) fifo_take_main(s_pts[0]);   // buffer vol
    memmove(s_pts, s_pts + 1, sizeof(MtPt) * (MT_PTS - 1));
    s_npts--;
  }
  if (alt != MT_ALT_NONE) alt = alt < -1000 ? -1000 : alt > 20000 ? 20000 : alt;
  s_pts[s_npts++] = { ts, (int32_t)lround(la * 1e5), (int32_t)lround(lo * 1e5), (uint16_t)(sp < 0 ? 0 : sp + 0.5f),
                      (int16_t)alt, (uint8_t)(main ? PT_MAIN : 0) };
}

static void pts_sent(uint32_t upto) {
  uint8_t k = 0;
  while (k < s_npts && s_pts[k].ts <= upto) k++;
  if (k) { memmove(s_pts, s_pts + k, sizeof(MtPt) * (s_npts - k)); s_npts -= k; }
}

// fifo: een bericht met de punten lo..hi is herhaald: die zijn binnen. Oudere punten pasten er niet
// meer in en zullen er ook nooit meer in passen: hoofdpunten naar de FIFO, de rest weg.
static void pts_heard(uint32_t lo, uint32_t hi) {
  uint8_t w = 0;
  for (uint8_t r = 0; r < s_npts; r++) {
    const MtPt& q = s_pts[r];
    if (q.ts >= lo && q.ts <= hi) continue;
    if (q.ts < lo) { if (q.fl & PT_MAIN) fifo_take_main(q); continue; }
    s_pts[w++] = q;
  }
  s_npts = w;
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

// ---- FIFO (0.9, track_mode fifo) --------------------------------------------------
// Wachtrij van punten die de mesh (waarschijnlijk) niet haalden, op tijd gesorteerd ([0] = oudste),
// zonder dubbele tijden. Vol = het minst betekenisvolle punt valt weg. Bewaard op ExtraFS: kop + punten, via tmp en
// rename, met een crc; geschreven na elke 10 nieuwe punten (of een kwartier na het eerste
// onbewaarde punt) en na elke leegmaakstap.
// Per punt een pogingenteller: een leegmaakbericht dat niet herhaald wordt, verhoogt hem voor al
// zijn punten en geeft ze een wachttijd (1, 5, 15, daarna 60 min). Vanaf fifo_pogingen is een punt
// "geparkeerd": lagere voorrang en 60 min wachttijd, zodat het de wachtrij niet blokkeert. Een punt
// wordt nooit opgegeven (0.9.4): het verdwijnt alleen na een herhaling van zijn bericht, een T1F per
// volgnummer, of als de wachtrij overloopt (het minst betekenisvolle punt).
struct MtFPt {
  uint32_t ts;                 // fix_ts (GPS)
  int32_t lat, lon;            // 1e-5 graden
  uint16_t spd;                // km/u
  int16_t alt;                 // m, MT_ALT_NONE = onbekend
  uint8_t tries;               // niet herhaalde leegmaakberichten met dit punt
  uint8_t flags;               // FPT_SENT: zat al in een verstuurd leegmaakbericht (voor T1F)
  uint16_t retry_min;          // alleen in RAM (na het laden 0): niet opnieuw voor deze minuut (sinds de start)
};
static_assert(sizeof(MtFPt) == 20, "MtFPt moet 20 bytes zijn (bestandsindeling v2)");
#define FPT_SENT     1
#define MT_FIFO_CAP  500               // grootste fifo_max
#define MT_FIFO_MAGIC 0x3146544DUL     // "MTF1"
#define MT_FL_HOUR 60                  // grootste fifo_per_uur
#define MT_FL_TRIES (2 * MT_FL_HOUR)   // pogingen per uur: hooguit 2x fifo_per_uur
// v1 (0.9.0-ontwikkeling): 16-byte punten zonder teller, kop zonder uurgeschiedenis. Wordt omgezet.
// v2 (0.9.0/0.9.1): kop met de verzendtijden van het laatste uur (unix, 0 = leeg), punten van 20 bytes.
// v3 (0.9.2): kop met twee ringen: getelde berichten (herhaald of door T1F bevestigd) en alle
//   pogingen. Een v2-kop wordt overgenomen: zijn tijden tellen voor beide ringen (voorzichtig).
struct MtFifoHdr1 { uint32_t magic; uint16_t version, count; uint32_t crc; };
struct MtFifoHdr2 { uint32_t magic; uint16_t version, count; uint32_t crc; uint32_t hist[MT_FL_HOUR]; };
struct MtFifoHdr3 { uint32_t magic; uint16_t version, count; uint32_t crc; uint32_t counted[MT_FL_HOUR]; uint32_t tries[MT_FL_TRIES]; };
// v4 (0.9.3): v3 + de tijd (unix, 0 = nooit) van het laatste geslaagde inhaalbericht en van de laatste
// poging met een niet-vol inhaalbericht, voor fifo_wacht.
struct MtFifoHdr { uint32_t magic; uint16_t version, count; uint32_t crc; uint32_t counted[MT_FL_HOUR]; uint32_t tries[MT_FL_TRIES];
                   uint32_t last_ok, last_partial; };
static const char* FIFO_PATH = "/mt_fifo.dat";
static const char* FIFO_TMP  = "/mt_fifo.tmp";
static MtFPt s_fifo[MT_FIFO_CAP];
static uint16_t s_nfifo = 0;
static uint16_t s_fifo_unsaved = 0;
static uint32_t s_fifo_unsaved_since = 0;
static bool s_cov_have = false;             // ooit dekking gezien (sinds de start)
static uint32_t s_cov_ms = 0;               // laatste dekking (millis)
static bool s_cov_stable = false;           // ooit stabiele dekking gezien
static uint32_t s_cov_stable_ms = 0;        // laatste stabiele dekking (zie fifo_coverage)
// Uurplafonds (0.9.2). fifo_per_uur telt alleen leegmaakberichten die de mesh echt belastten: een
// repeater herhaalde ze, of de server bevestigde ze later met een T1F. Daarnaast een vaste
// veiligheidsgrens voor de eigen zendtijd: hooguit 2x fifo_per_uur pogingen (herhaald of niet) per uur.
// Elke ring houdt verzendtijden bij, in RAM als millis (| 1; 0 = leeg) en, als de klok gezet is, ook
// als unix-tijd: die gaan mee in de kop van het bestand, zodat een herstart de plafonds niet wist. Na
// het laden tellen de bewaarde tijden mee zodra de klok geldig is.
struct MtRing { uint32_t* ms; uint32_t* ux; uint32_t* ld; uint8_t n; };
static uint32_t s_rc_ms[MT_FL_HOUR], s_rc_ux[MT_FL_HOUR], s_rc_ld[MT_FL_HOUR];
static uint32_t s_rt_ms[MT_FL_TRIES], s_rt_ux[MT_FL_TRIES], s_rt_ld[MT_FL_TRIES];
static MtRing s_ring_cnt = { s_rc_ms, s_rc_ux, s_rc_ld, MT_FL_HOUR };     // getelde berichten
static MtRing s_ring_try = { s_rt_ms, s_rt_ux, s_rt_ld, MT_FL_TRIES };    // alle pogingen
// Verstuurde leegmaakberichten (0.9.4): volgnummer, verzendtijd en PRECIES welke punten ze droegen.
// Een T1F bevestigt per volgnummer ("s<seqs>"); alleen die punten verdwijnen, nooit een tijdsbereik
// (0.9.3 wiste zo punten die de server nooit kreeg). Alleen in RAM: na een herstart blijven de punten
// gewoon in de wachtrij en gaan ze opnieuw mee (de server filtert dubbels).
#define MT_SENT_MSGS 40
#define MT_SENT_PTS  31                     // = MT_SLOW_MAX_ITEMS + 1
struct MtSentMsg { uint32_t ms, ux; uint16_t seq; uint8_t n, ok; uint32_t ts[MT_SENT_PTS]; };
static MtSentMsg s_sent[MT_SENT_MSGS];      // ms = 0: leeg
static uint8_t s_sent_i = 0;
// Laatste geslaagde inhaalbericht (herhaald of door T1F bevestigd), op zijn verzendtijd. fifo_wacht
// (0.9.3): een niet-vol inhaalbericht pas als dit minstens fifo_wacht geleden is. Nooit = lang geleden.
static uint32_t s_ok_ms = 0;                // millis | 1 (deze start), 0 = niet in deze start
static uint32_t s_ok_ux = 0;                // unix (ook uit het bestand), 0 = onbekend
static uint32_t s_pa_ms = 0, s_pa_ux = 0;  // laatste poging met een niet-vol bericht (geslaagd of niet), idem
static void last_ok_set(uint32_t ms, uint32_t ux) {
  if (!s_ok_ms || (int32_t)(ms - s_ok_ms) > 0) s_ok_ms = ms | 1;
  if (ux && ux > s_ok_ux) s_ok_ux = ux;
}

static bool fifo_mode() { return mt_cfg.track_mode == MT_TRACK_FIFO; }
static uint16_t fifo_cap() { return mt_cfg.fifo_max < 20 ? 20 : mt_cfg.fifo_max > MT_FIFO_CAP ? MT_FIFO_CAP : mt_cfg.fifo_max; }
static uint16_t now_min() { return (uint16_t)(millis() / 60000UL); }
static uint32_t rtc_unix() {
  uint32_t t = the_mesh.getRTCClock()->getCurrentTime();
  return t >= 1700000000UL ? t : 0;           // 0 = klok (nog) niet gezet
}

// Een verzendtijd toevoegen (ook een vroegere, bij een T1F): een lege of verlopen plaats, anders de oudste.
static void ring_add(MtRing& r, uint32_t ms, uint32_t ux) {
  uint32_t now = millis();
  int k = 0;
  uint32_t best = 0;
  for (int i = 0; i < r.n; i++) {
    if (!r.ms[i] || now - r.ms[i] >= 3600000UL) { k = i; break; }
    if (now - r.ms[i] > best) { best = now - r.ms[i]; k = i; }
  }
  r.ms[k] = ms | 1;
  r.ux[k] = ux;
}

// Aantal in het laatste uur (eigen + bewaarde van voor de herstart); oldest_age_s = leeftijd van het oudste.
static int ring_count(const MtRing& r, uint32_t* oldest_age_s = nullptr) {
  uint32_t now = millis(), oldest = 0, unow = rtc_unix();
  int n = 0;
  for (int i = 0; i < r.n; i++) {
    if (!r.ms[i] || now - r.ms[i] >= 3600000UL) continue;
    n++;
    if ((now - r.ms[i]) / 1000 > oldest) oldest = (now - r.ms[i]) / 1000;
  }
  if (unow) {
    for (int i = 0; i < r.n; i++) {
      uint32_t t = r.ld[i];
      if (!t || t > unow || unow - t >= 3600) continue;
      n++;
      if (unow - t > oldest) oldest = unow - t;
    }
  }
  if (oldest_age_s) *oldest_age_s = oldest;
  return n;
}

// Unix-tijden van het laatste uur voor de kop van het bestand.
static void ring_store(const MtRing& r, uint32_t* out) {
  uint32_t now = rtc_unix();
  int k = 0;
  for (int i = 0; i < r.n && k < r.n; i++) {     // eigen verzendingen van het laatste uur
    uint32_t t = r.ux[i];
    if (t && now && t <= now && now - t < 3600 && r.ms[i] && millis() - r.ms[i] < 3600000UL) out[k++] = t;
  }
  for (int i = 0; i < r.n && k < r.n; i++) {     // en de bewaarde van voor de herstart
    uint32_t t = r.ld[i];
    if (t && (!now || (t <= now && now - t < 3600))) out[k++] = t;
  }
}

static void sent_add(uint16_t seq, uint32_t ms, uint32_t ux, const uint32_t* ts, int n) {
  MtSentMsg& m = s_sent[s_sent_i];
  s_sent_i = (uint8_t)((s_sent_i + 1) % MT_SENT_MSGS);
  m.ms = ms | 1;
  m.ux = ux;
  m.seq = seq;
  m.ok = 0;
  m.n = (uint8_t)(n > MT_SENT_PTS ? MT_SENT_PTS : n);
  memcpy(m.ts, ts, sizeof(uint32_t) * m.n);
}

static void sent_mark_ok(uint16_t seq) {
  for (int i = 0; i < MT_SENT_MSGS; i++) if (s_sent[i].ms && s_sent[i].seq == seq) s_sent[i].ok = 1;
}

// Staat seq in "2301,2305-2307" (len tekens)?
static bool seq_in_list(const char* l, int len, uint16_t seq) {
  int i = 0;
  while (i < len) {
    char* end;
    unsigned long a = strtoul(l + i, &end, 10), b = a;
    int j = end - l;
    if (j == i) return false;                  // geen getal: ongeldig
    if (j < len && l[j] == '-') {
      i = j + 1;
      b = strtoul(l + i, &end, 10);
      j = end - l;
      if (j == i) return false;
    }
    if (a <= 0xFFFF && b <= 0xFFFF && a <= b && seq >= a && seq <= b) return true;
    if (j < len && l[j] != ',') return false;
    i = j + 1;
  }
  return false;
}

static bool seq_list_valid(const char* l, int len) {
  if (len <= 0) return false;
  for (int i = 0; i < len; i++) if (!((l[i] >= '0' && l[i] <= '9') || l[i] == ',' || l[i] == '-')) return false;
  return true;
}

static int fifo_find_ts_pos(uint32_t ts) {
  for (uint16_t i = 0; i < s_nfifo; i++) if (s_fifo[i].ts == ts) return i;
  return -1;
}

static void fifo_evict_one();

static bool fifo_save() {
  static MtFifoHdr h;               // 732 bytes: niet op de stapel
  memset(&h, 0, sizeof(h));
  h.magic = MT_FIFO_MAGIC;
  h.version = 4;
  h.count = s_nfifo;
  h.crc = mt_crc32(0, (const uint8_t*)s_fifo, sizeof(MtFPt) * s_nfifo);
  ring_store(s_ring_cnt, h.counted);
  ring_store(s_ring_try, h.tries);
  h.last_ok = s_ok_ux;
  h.last_partial = s_pa_ux;
  bool ok = mt_file_write_atomic(FIFO_PATH, FIFO_TMP, &h, sizeof(h), s_fifo, sizeof(MtFPt) * s_nfifo);
  if (ok) s_fifo_unsaved = 0;
  else mt_log("fifo: bewaren MISLUKT (%u punten)", (unsigned)s_nfifo);
  return ok;
}

static bool fifo_load_file(const char* path) {
  MtFifoHdr1 h1;
  if (!mt_file_read_at(path, 0, &h1, sizeof(h1)) || h1.magic != MT_FIFO_MAGIC || h1.count > MT_FIFO_CAP) return false;
  if (h1.version == 1) {                       // omzetten: punt per punt (16 bytes), teller 0
    uint32_t crc = 0;
    for (uint16_t i = 0; i < h1.count; i++) {
      uint8_t b[16];
      if (!mt_file_read_at(path, sizeof(h1) + 16UL * i, b, 16)) return false;
      crc = mt_crc32(crc, b, 16);
      MtFPt& p = s_fifo[i];
      memset(&p, 0, sizeof(p));
      memcpy(&p, b, 16);                       // ts, lat, lon, spd, alt: zelfde plaats
    }
    if (crc != h1.crc) return false;
    s_nfifo = h1.count;
    return true;
  }
  if (h1.version == 2) {                       // 0.9.0/0.9.1: één ring; telt voor beide (voorzichtig)
    MtFifoHdr2 h;
    if (!mt_file_read_at(path, 0, &h, sizeof(h))) return false;
    if (h.count && !mt_file_read_at(path, sizeof(h), s_fifo, sizeof(MtFPt) * h.count)) return false;
    if (mt_crc32(0, (const uint8_t*)s_fifo, sizeof(MtFPt) * h.count) != h.crc) return false;
    s_nfifo = h.count;
    memcpy(s_rc_ld, h.hist, sizeof(s_rc_ld));
    memset(s_rt_ld, 0, sizeof(s_rt_ld));
    memcpy(s_rt_ld, h.hist, sizeof(h.hist));
    return true;
  }
  if (h1.version != 3 && h1.version != 4) return false;
  static MtFifoHdr h;               // 740 bytes: niet op de stapel
  memset(&h, 0, sizeof(h));
  size_t hs = h1.version == 3 ? sizeof(MtFifoHdr3) : sizeof(MtFifoHdr);   // v3: zonder last_ok (= nooit)
  if (!mt_file_read_at(path, 0, &h, hs)) return false;
  if (h.count && !mt_file_read_at(path, hs, s_fifo, sizeof(MtFPt) * h.count)) return false;
  if (mt_crc32(0, (const uint8_t*)s_fifo, sizeof(MtFPt) * h.count) != h.crc) return false;
  s_nfifo = h.count;
  memcpy(s_rc_ld, h.counted, sizeof(s_rc_ld));
  memcpy(s_rt_ld, h.tries, sizeof(s_rt_ld));
  s_ok_ux = h.last_ok;
  s_pa_ux = h.last_partial;
  return true;
}

static void fifo_load() {
  s_nfifo = 0;
  if (!fifo_load_file(FIFO_PATH) && !fifo_load_file(FIFO_TMP)) s_nfifo = 0;
  for (uint16_t i = 0; i < s_nfifo; i++) s_fifo[i].retry_min = 0;   // wachttijden gelden niet over een herstart
  uint16_t cap = fifo_cap();
  while (s_nfifo > cap) fifo_evict_one();     // fifo_max verkleind: de minst betekenisvolle punten weg
}

// Afstand (m) van punt b tot het lijnstuk a-c (vlakke benadering; ruim goed genoeg voor enkele km).
static float seg_dist_m(const MtFPt& a, const MtFPt& b, const MtFPt& c) {
  const float ky = 1.11195f;                                      // m per 1e-5 graad breedte
  const float kx = ky * cosf(b.lat * 1e-5f * 0.01745329f);         // m per 1e-5 graad lengte
  float cx = (c.lon - a.lon) * kx, cy = (c.lat - a.lat) * ky;
  float bx = (b.lon - a.lon) * kx, by = (b.lat - a.lat) * ky;
  float l2 = cx * cx + cy * cy;
  float t = l2 > 0 ? (bx * cx + by * cy) / l2 : 0;
  if (t < 0) t = 0;
  if (t > 1) t = 1;
  float dx = bx - t * cx, dy = by - t * cy;
  return sqrtf(dx * dx + dy * dy);
}

// Volle wachtrij: het minst betekenisvolle punt weg = het punt dat het dichtst bij het lijnstuk tussen
// zijn twee buren ligt. Het eerste en het laatste punt blijven altijd.
static void fifo_evict_one() {
  if (s_nfifo < 3) { if (s_nfifo) { memmove(s_fifo, s_fifo + 1, sizeof(MtFPt) * (s_nfifo - 1)); s_nfifo--; } return; }
  uint16_t best = 1;
  float bd = 1e30f;
  for (uint16_t i = 1; i + 1 < s_nfifo; i++) {
    float d = seg_dist_m(s_fifo[i - 1], s_fifo[i], s_fifo[i + 1]);
    if (d < bd) { bd = d; best = i; }
  }
  memmove(s_fifo + best, s_fifo + best + 1, sizeof(MtFPt) * (s_nfifo - best - 1));
  s_nfifo--;
}

// Eén punt toevoegen op zijn plaats in de tijd. false = dubbel (zelfde tijd).
// Vol: eerst het minst betekenisvolle punt weg. fifo_dun: komt het nieuwe punt achteraan, en ligt het
// vorige laatste punt dan binnen fifo_dun m van de lijn tussen zijn voorganger en het nieuwe punt
// (rechte weg), dan is dat vorige overbodig. Een punt na of voor een tijdsprong van meer dan 10 min
// blijft altijd (zo blijven stops zichtbaar).
static bool fifo_add(const MtFPt& p0) {
  MtFPt p = p0;
  p.tries = 0;
  p.flags = 0;
  p.retry_min = 0;
  if (!p.ts) return false;
  if (fifo_find_ts_pos(p.ts) >= 0) return false;
  if (s_nfifo >= fifo_cap() || s_nfifo >= MT_FIFO_CAP) fifo_evict_one();
  int i = s_nfifo;
  while (i > 0 && s_fifo[i - 1].ts > p.ts) i--;
  memmove(s_fifo + i + 1, s_fifo + i, sizeof(MtFPt) * (s_nfifo - i));
  s_fifo[i] = p;
  s_nfifo++;
  if (mt_cfg.fifo_dun && i == s_nfifo - 1 && s_nfifo >= 3) {
    const MtFPt& a = s_fifo[s_nfifo - 3];
    const MtFPt& b = s_fifo[s_nfifo - 2];
    if (p.ts - b.ts <= 600 && b.ts - a.ts <= 600 && seg_dist_m(a, b, p) <= mt_cfg.fifo_dun) {
      s_fifo[s_nfifo - 2] = p;
      s_nfifo--;
    }
  }
  if (!s_fifo_unsaved++) s_fifo_unsaved_since = millis();
  return true;
}

static int fifo_find(uint32_t ts) { return fifo_find_ts_pos(ts); }

static void fifo_remove_ts(const uint32_t* ts, int n) {
  uint16_t w = 0;
  for (uint16_t r = 0; r < s_nfifo; r++) {
    bool drop = false;
    for (int k = 0; k < n && !drop; k++) drop = s_fifo[r].ts == ts[k];
    if (!drop) s_fifo[w++] = s_fifo[r];
  }
  s_nfifo = w;
}

static uint16_t fifo_parked() {
  uint16_t n = 0;
  for (uint16_t i = 0; i < s_nfifo; i++) n += s_fifo[i].tries >= mt_cfg.fifo_pogingen;
  return n;
}

static MtFPt fifo_pt(uint32_t ts, double la, double lo, float sp, long alt) {
  if (alt < -1000) alt = -1000;
  if (alt > 20000) alt = 20000;
  MtFPt p = { ts, (int32_t)lround(la * 1e5), (int32_t)lround(lo * 1e5), (uint16_t)(sp < 0 ? 0 : sp + 0.5f), (int16_t)alt, 0, 0, 0 };
  return p;
}

// Een hoofdpunt dat de FastTrack-buffer verlaat zonder ooit in een herhaald bericht te zitten.
static void fifo_take_main(const MtPt& q) {
  MtFPt f = { q.ts, q.lat, q.lon, q.spd, q.alt, 0, 0, 0 };
  if (fifo_add(f)) mt_log("fifo: punt toegevoegd (geen herhaling gehoord), totaal %u", (unsigned)s_nfifo);
}

// Dekking: een repeater heeft ons of iemand anders gehoord en doorgegeven. Gelogd als ze nieuw
// is (de vorige was ouder dan 30 s), en alleen in fifo-modus (anders te veel lawaai).
// Stabiel (mag een NIEUWE leegmaakronde starten): SNR >= fifo_snr, of twee keer dekking binnen
// 60 s, of een geldige T1F van de server. Eén zwak pakket op de rand van het bereik is dat niet.
#define MT_SNR_NONE 999.0f                  // geen SNR (T1F)
static uint32_t s_cov_unix = 0;             // dump: laatste dekking (unix), SNR en of ze stabiel was
static float s_cov_snr = MT_SNR_NONE;
static bool s_cov_last_stable = false;
static bool s_cov_last_strong = false;      // laatste dekking: SNR >= fifo_snr of een T1F (voor niet-volle berichten)
static void fifo_coverage(const char* why, unsigned hops, const char* rep, bool strong, float snr) {
  uint32_t now = millis();
  bool fresh = s_cov_have && now - s_cov_ms < 30000;
  s_cov_last_stable = strong || (s_cov_have && now - s_cov_ms < 60000);
  s_cov_last_strong = strong;
  if (s_cov_last_stable) { s_cov_stable = true; s_cov_stable_ms = now; }
  s_cov_unix = the_mesh.getRTCClock()->getCurrentTime();
  s_cov_snr = snr;
  s_cov_have = true;
  s_cov_ms = now;
  if (!fresh && fifo_mode()) mt_log("fifo: dekking (%s, %u hops, laatste repeater %s)", why, hops, rep);
}

// T1F van de server per volgnummer: precies de punten van die leegmaakberichten zijn binnen. Een niet
// gehoord bericht telt dan alsnog als geslaagd (fifo_per_uur op zijn verzendtijd als dat in het laatste
// uur viel, en voor fifo_wacht). msgs = aantal herkende berichten; geeft het aantal verwijderde punten.
static uint32_t s_fifo_confirmed = 0;          // sinds de start door T1F verwijderd (status fifo_bevestigd)
static uint16_t fifo_confirm_seqs(const char* l, int len, int* msgs, int* late) {
  uint16_t n0 = s_nfifo;
  *msgs = *late = 0;
  for (int i = 0; i < MT_SENT_MSGS; i++) {
    MtSentMsg& m = s_sent[i];
    if (!m.ms || !seq_in_list(l, len, m.seq)) continue;
    (*msgs)++;
    fifo_remove_ts(m.ts, m.n);
    if (!m.ok) {
      if (millis() - m.ms < 3600000UL) ring_add(s_ring_cnt, m.ms, m.ux);
      last_ok_set(m.ms, m.ux);
      (*late)++;
    }
    m.ms = 0;                                  // afgehandeld
  }
  uint16_t gone = n0 - s_nfifo;
  s_fifo_confirmed += gone;
  if (gone || *late) fifo_save();
  return gone;
}

// Welke punten het FastTrack-bericht dat net naar de zender ging, meedroeg (fifo-modus). on_send_done
// koppelt dat aan de bewaking van het bericht (zelfde tag = fix_ts van het hoofdpunt).
static bool s_cap_set = false;
static uint32_t s_cap_lo = 0, s_cap_hi = 0;   // oudste meegenomen punt .. hoofdpunt

// ---- laatst gekende fix (0.9.6) ------------------------------------------------------
// In RAM bij elke nieuwe fix; op ExtraFS (/mt_lastfix.dat, via tmp + rename, met crc) bij het in rust gaan,
// voor een geplande herstart/uitschakeling, en onderweg hooguit eens per 10 min als hij > 20 m verplaatste.
// Zo kan een locatieverzoek binnen (geen GPS-ontvangst) toch de laatst gekende plek geven, ook na een herstart.
#define MT_LF_MAGIC 0x464C544DUL     // "MTLF"
struct MtLastFix { uint32_t magic; uint32_t ts; int32_t lat, lon; int16_t alt; uint16_t hdop10; uint32_t crc; };
static const char* LF_PATH = "/mt_lastfix.dat";
static const char* LF_TMP  = "/mt_lastfix.tmp";
static MtLastFix s_lf = {};                 // ts 0 = nooit een fix
static uint32_t s_lf_saved_ts = 0;          // ts van wat in het bestand staat
static int32_t s_lf_saved_lat = 0, s_lf_saved_lon = 0;
static uint32_t s_lf_saved_ms = 0;

static uint32_t lf_crc(const MtLastFix& f) { return mt_crc32(0, (const uint8_t*)&f, offsetof(MtLastFix, crc)); }

static void lastfix_load() {
  MtLastFix f;
  if (!mt_file_read_at(LF_PATH, 0, &f, sizeof(f)) && !mt_file_read_at(LF_TMP, 0, &f, sizeof(f))) return;
  if (f.magic != MT_LF_MAGIC || f.crc != lf_crc(f) || !f.ts) return;
  s_lf = f;
  s_lf_saved_ts = f.ts;
  s_lf_saved_lat = f.lat;
  s_lf_saved_lon = f.lon;
}

static void lastfix_save() {
  if (!s_lf.ts || s_lf.ts == s_lf_saved_ts) return;   // niets nieuws: geen slijtage
  s_lf.magic = MT_LF_MAGIC;
  s_lf.crc = lf_crc(s_lf);
  if (!mt_file_write_atomic(LF_PATH, LF_TMP, &s_lf, sizeof(s_lf), nullptr, 0)) { mt_log("laatste fix: bewaren MISLUKT"); return; }
  s_lf_saved_ts = s_lf.ts;
  s_lf_saved_lat = s_lf.lat;
  s_lf_saved_lon = s_lf.lon;
  s_lf_saved_ms = millis() | 1;
}

void mt_tracker_save_lastfix() { lastfix_save(); }

// Elke loop: nieuwe fix in RAM; onderweg hooguit eens per 10 min naar flash als hij > 20 m verplaatste
// (of als er nog niets bewaard is).
static void lastfix_track() {
  MtNmeaProvider& g = mt_gps();
  if (!g.freshFix(2500) || !g.lastValidUnix() || g.lastValidUnix() == s_lf.ts) return;
  long alt = g.getAltitude() / 1000;
  s_lf.ts = g.lastValidUnix();
  s_lf.lat = (int32_t)lround(g.getLatitude() / 10.0);
  s_lf.lon = (int32_t)lround(g.getLongitude() / 10.0);
  s_lf.alt = (int16_t)(alt < -1000 ? -1000 : alt > 20000 ? 20000 : alt);
  float h = g.hdop() * 10;
  s_lf.hdop10 = (uint16_t)(h < 0 ? 0 : h > 9999 ? 9999 : h + 0.5f);
  if (!s_lf_saved_ts) { lastfix_save(); return; }
  if (s_lf_saved_ms && millis() - s_lf_saved_ms < 600000UL) return;
  if (mt_haversine_m(s_lf_saved_lat / 1e5, s_lf_saved_lon / 1e5, s_lf.lat / 1e5, s_lf.lon / 1e5) > 20) lastfix_save();
}

// send_report: positie uit de laatst gekende fix in plaats van de GPS (alleen voor V, 0.9.6)
static const MtLastFix* s_pos_override = nullptr;

// ---- berichten ----------------------------------------------------------------

static bool tracking_active() {
  MtMode m = mt_effective_mode();
  return m == MT_MODE_TRACKER || mt_cfg.track_in_companion;
}

// "T1|<rest>" ondertekenen als T1C, naar een verbonden app (companion) en naar de radio.
// text wordt overschreven met het T1C-bericht (voor het log).
static void sign_and_send(char* text, char state, bool manual, bool keep, uint32_t tag) {
  radio_wake();
  {
    char pk[9], sig[9], body[MT_TEXT_MAX + 1];
    own_pk8(pk);
    snprintf(body, sizeof(body), "%s|%s", pk, text + 3);          // text begint met "T1|"
    auth_tag(body, sig);
    snprintf(text, MT_TEXT_MAX + 1, "T1C|%s|%s|%s", pk, sig, body + 9);
  }
  // Eigen positie ook naar een verbonden app (alleen als companion: dan staat Bluetooth aan).
  if (mt_effective_mode() == MT_MODE_COMPANION) the_mesh.mtQueueOwn(mt_cfg.chan_idx, text);
  mt_send(text, manual, keep, tag, state);
}

static void send_report(char state, bool with_pos, bool manual, const char* reason) {
  MtNmeaProvider& g = mt_gps();
  char lat[16] = "", lon[16] = "", alt[8] = "", spd[8] = "", crs[8] = "", hd[8] = "", age[12] = "", fts[12] = "";
  double la = 0, lo = 0;
  float sp = -1, cr = -1;
  if (with_pos && s_pos_override) {             // laatst gekende fix (V zonder verse fix)
    const MtLastFix& f = *s_pos_override;
    la = f.lat / 1e5;
    lo = f.lon / 1e5;
    snprintf(lat, sizeof(lat), "%.5f", la);
    snprintf(lon, sizeof(lon), "%.5f", lo);
    snprintf(alt, sizeof(alt), "%d", (int)f.alt);
    snprintf(hd, sizeof(hd), "%.1f", f.hdop10 / 10.0);
    uint32_t now_unix = the_mesh.getRTCClock()->getCurrentTime();
    if (now_unix >= f.ts) snprintf(age, sizeof(age), "%lu", (unsigned long)(now_unix - f.ts));
    snprintf(fts, sizeof(fts), "%lu", (unsigned long)f.ts);
  } else if (with_pos && g.lastValidMs() != 0) {
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
  uint32_t tag = 0, main_ts = !with_pos ? 0 : s_pos_override ? s_pos_override->ts : g.lastValidUnix();
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
    // fifo: onthouden welke punten dit bericht droeg; herhaald = die zijn binnen.
    s_cap_set = fifo_mode();
    s_cap_lo = pts;
    s_cap_hi = main_ts;
    // ook het hoofdpunt: bij een misser gaat het mee met het volgende
    pts_push(main_ts, la, lo, sp, g.getAltitude() / 1000, true);
    tag = main_ts;
  } else {
    s_cap_set = false;
  }
  // Belangrijke berichten wijken niet voor een nieuwere gewone positie.
  bool keep = manual || state == 'E' || state == 'S' || state == 'H' || state == 'B' || state == 'V';
  sign_and_send(text, state, manual, keep, tag);
  s_rules.sent(now_s(), with_pos, la, lo, sp >= 3 ? cr : -1);
  strncpy(s_last_reason, reason, sizeof(s_last_reason) - 1);
  s_last_tx_ms = millis();
  if (state == 'E') {
    if (s_sos_n < 3) s_sos_seq[s_sos_n++] = seq;
    s_listen_until = millis() + 30000;     // radio wakker: antwoord (T1A) van de server afwachten
  }
  mt_log("tx %s (%s)", text, reason);
}

// ---- SlowTrack -------------------------------------------------------------------
// Los van FastTrack: elke slow_log een punt in een eigen buffer, elke slow_send één
// L-bericht met die punten. Raakt de regels (s_rules), de heartbeat en de FastTrack-buffer
// niet aan.
struct MtSPt { uint32_t ts; int32_t lat, lon; uint16_t spd; int16_t alt; };   // lat/lon in 1e-5 graden, alt in m
#define MT_SLOW_PTS 64
#define MT_SLOW_MAX_ITEMS 30        // extra punten per bericht (grens van de server)
static MtSPt s_slow[MT_SLOW_PTS];
static uint8_t s_nslow = 0;
static bool s_slow_have_prev = false;       // vorig punt (ook als het al verstuurd is): geen dubbele punten in rust
static int32_t s_slow_prev_lat = 0, s_slow_prev_lon = 0;
static bool s_slow_on = false;              // SlowTrack loopt (timers gezet)
static bool s_slow_acq = false;             // GPS staat aan voor een SlowTrack-punt
static uint32_t s_slow_acq_deadline = 0;
static uint32_t s_slow_log_due = 0, s_slow_send_due = 0;

// Heeft FastTrack, een klik of een SOS de GPS nu nodig?
static bool s_req_gps = false;              // een locatieverzoek zoekt een fix: de GPS niet uitzetten
static bool fast_needs_gps() {
  return s_manual || s_sos_left || s_req_gps || (tracking_active() && (s_state == MT_T_ACQUIRE || s_state == MT_T_MOVING));
}

static void slow_release() {
  s_slow_acq = false;
  s_slow_gps = false;
  if (!fast_needs_gps()) gps_want(false);
}

static void slow_take(MtNmeaProvider& g) {
  uint32_t ts = g.lastValidUnix();
  if (!ts || (s_nslow && s_slow[s_nslow - 1].ts >= ts)) return;
  double la = g.getLatitude() / 1e6, lo = g.getLongitude() / 1e6;
  float sp = g.speedKmh();
  if (sp >= MT_STILL_KMH) mt_motion_touch();   // de GPS ziet snelheid: dat is beweging, ook als de sensor niets voelt
  if (s_slow_have_prev && sp < 3 && mt_haversine_m(s_slow_prev_lat / 1e5, s_slow_prev_lon / 1e5, la, lo) < 5) return;
  if (fifo_mode()) {                           // fifo: meteen de wachtrij in, geen eigen buffer
    MtFPt f = fifo_pt(ts, la, lo, sp, g.getAltitude() / 1000);
    if (fifo_add(f)) mt_log("fifo: SlowTrack-punt toegevoegd, totaal %u", (unsigned)s_nfifo);
    s_slow_have_prev = true;
    s_slow_prev_lat = f.lat;
    s_slow_prev_lon = f.lon;
    return;
  }
  if (s_nslow == MT_SLOW_PTS) { memmove(s_slow, s_slow + 1, sizeof(MtSPt) * (MT_SLOW_PTS - 1)); s_nslow--; }
  long alt = g.getAltitude() / 1000;
  if (alt < -1000) alt = -1000;
  if (alt > 20000) alt = 20000;
  MtSPt& p = s_slow[s_nslow++];
  p = { ts, (int32_t)lround(la * 1e5), (int32_t)lround(lo * 1e5), (uint16_t)(sp < 0 ? 0 : sp + 0.5f), (int16_t)alt };
  s_slow_have_prev = true;
  s_slow_prev_lat = p.lat;
  s_slow_prev_lon = p.lon;
}

// Verstuurd: alle punten tot en met het hoofdpunt weg, ook de uitgedunde (bewust).
static void slow_sent(uint32_t upto) {
  uint8_t k = 0;
  while (k < s_nslow && s_slow[k].ts <= upto) k++;
  if (k) { memmove(s_slow, s_slow + k, sizeof(MtSPt) * (s_nslow - k)); s_nslow -= k; }
}

// ---- binaire extra punten (0.9; L- en Q-berichten) ----------------------------------
// Veld 15 = "B" + base64url (A-Z a-z 0-9 - _, zonder opvulling). De bytes zijn de punten, NIEUWSTE
// eerst na het hoofdpunt, elk t.o.v. het vorige (te beginnen bij het hoofdpunt), als drie varints
// (LEB128): dt = vorige_ts - deze_ts (s), dlat en dlon = deze - vorige (1e-5 graden, zigzag).
struct XPt { uint32_t ts; int32_t lat, lon; };

static int varint_put(uint8_t* o, uint32_t v) {
  int n = 0;
  do { uint8_t b = v & 0x7F; v >>= 7; o[n++] = b | (v ? 0x80 : 0); } while (v);
  return n;
}
static uint32_t zigzag(int32_t v) { return ((uint32_t)v << 1) ^ (uint32_t)(v >> 31); }
static int b64url_len(int n) { return (n * 4 + 2) / 3; }
static int b64url(char* o, const uint8_t* p, int n) {
  static const char A[] = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_";
  int k = 0;
  for (int i = 0; i < n; i += 3) {
    uint32_t v = (uint32_t)p[i] << 16 | (i + 1 < n ? (uint32_t)p[i + 1] << 8 : 0) | (i + 2 < n ? p[i + 2] : 0);
    o[k++] = A[(v >> 18) & 63];
    o[k++] = A[(v >> 12) & 63];
    if (i + 1 < n) o[k++] = A[(v >> 6) & 63];
    if (i + 2 < n) o[k++] = A[v & 63];
  }
  return k;
}

// x[0..c-1] van oud naar nieuw, x[c-1] = het hoofdpunt. Zet "|B..." achter text[len] en past len
// aan; false (en text ongewijzigd) als het niet binnen limit past.
static bool append_bin_extras(char* text, int& len, int limit, const XPt* x, int c) {
  if (c < 2) return true;
  uint8_t buf[128];
  int nb = 0;
  for (int k = c - 2; k >= 0; k--) {
    const XPt& prev = x[k + 1];
    const XPt& q = x[k];
    if (prev.ts - q.ts > 86400) return false;   // de server neemt hooguit een dag tussen twee punten
    uint8_t t[15];
    int m = varint_put(t, prev.ts - q.ts);
    m += varint_put(t + m, zigzag(q.lat - prev.lat));
    m += varint_put(t + m, zigzag(q.lon - prev.lon));
    if (nb + m > (int)sizeof(buf)) return false;
    memcpy(buf + nb, t, m);
    nb += m;
  }
  if (len + 2 + b64url_len(nb) > limit) return false;
  text[len++] = '|';
  text[len++] = 'B';
  len += b64url(text + len, buf, nb);
  text[len] = 0;
  return true;
}

// Eén L-bericht: hoofdpunt = het nieuwste punt (met eigen fix_ts), daarna de oudere punten binair
// in veld 15 (append_bin_extras). Passen ze niet allemaal binnen de grens, dan blijven het oudste
// en het nieuwste en wordt gelijkmatig uitgedund.
static void send_slow() {
  const int n = s_nslow;
  const MtSPt& m = s_slow[n - 1];
  int bat = mt_battery_pct(board.getBattMilliVolts());
  char age[12] = "";
  uint32_t now_unix = the_mesh.getRTCClock()->getCurrentTime();
  if (now_unix >= m.ts && now_unix - m.ts < 10000000UL) snprintf(age, sizeof(age), "%lu", (unsigned long)(now_unix - m.ts));
  uint16_t seq = seq_next();
  char base[MT_TEXT_MAX + 1];
  int bn = snprintf(base, sizeof(base), "T1|%u|L|%.5f|%.5f|%d|%u||%d||%s|%c|%c|%lu",
                    (unsigned)seq, m.lat / 1e5, m.lon / 1e5, (int)m.alt, (unsigned)m.spd, bat < 0 ? 0 : bat, age,
                    mt_effective_mode() == MT_MODE_TRACKER ? 't' : 'c', mt_usb() ? 'u' : 'b', (unsigned long)m.ts);
  const int limit = MT_TEXT_MAX - the_mesh.mtSenderLen() - 19 - 2;   // zoals send_report
  char text[MT_TEXT_MAX + 1];
  uint8_t sel[MT_SLOW_MAX_ITEMS + 1];
  XPt x[MT_SLOW_MAX_ITEMS + 1];
  int c = n < MT_SLOW_MAX_ITEMS + 1 ? n : MT_SLOW_MAX_ITEMS + 1;
  int used = 1;
  for (; c >= 1; c--) {
    // c punten gelijkmatig over de hele buffer: sel[0] = oudste, sel[c-1] = nieuwste
    if (c == 1) sel[0] = n - 1;
    else for (int i = 0; i < c; i++) sel[i] = (uint8_t)((i * (n - 1) + (c - 1) / 2) / (c - 1));
    memcpy(text, base, bn + 1);
    int len = bn;
    used = 1;
    if (c == 1) break;
    for (int i = 0; i < c; i++) x[i] = { s_slow[sel[i]].ts, s_slow[sel[i]].lat, s_slow[sel[i]].lon };
    if (append_bin_extras(text, len, limit, x, c)) { used = c; break; }   // anders: minder punten
  }
  sign_and_send(text, 'L', false, true, m.ts);
  strncpy(s_last_reason, "slowtrack", sizeof(s_last_reason) - 1);
  s_last_tx_ms = millis();
  mt_log("tx %s (slowtrack: %d van %d punten)", text, used, n);
}

// In rust: langer dan still_timeout geen beweging volgens de bewegingssensor. Gebruikt door SlowTrack
// (dan geen GPS meer) en door de FIFO (stilstaan is het moment om de wachtrij leeg te maken).
static bool at_rest() {
  return mt_motion_available() && mt_cfg.still_timeout_s && !s_manual &&
         millis() - mt_motion_last() >= 1000UL * mt_cfg.still_timeout_s;
}

static void step_slow() {
  uint32_t now = millis();
  if (!tracking_active()) {
    if (s_slow_acq) slow_release();
    s_slow_on = false;
    return;
  }
  const uint32_t log_ms = 1000UL * mt_cfg.slow_log_s, send_ms = 1000UL * mt_cfg.slow_send_s;
  if (!s_slow_on) {
    s_slow_on = true;
    s_slow_log_due = now;                        // meteen een eerste punt
    s_slow_send_due = now + send_ms;
  }
  // Een kortere instelling geldt meteen, niet pas na de oude periode.
  if ((int32_t)(s_slow_send_due - now) > (int32_t)send_ms) s_slow_send_due = now + send_ms;
  if (log_ms && (int32_t)(s_slow_log_due - now) > (int32_t)log_ms) s_slow_log_due = now + log_ms;

  // In rust (0.9.1): langer dan still_timeout geen beweging volgens de sensor = de tracker ligt stil.
  // Dan geen GPS meer aanzetten voor SlowTrack (het punt zou toch op dezelfde plek liggen); de eerste
  // beweging logt meteen een punt. Volgt de sensor, niet de slaapstand van FastTrack: met fast_min_batt
  // slaapt FastTrack ook tijdens het rijden, en dan moet SlowTrack juist doorgaan.
  // Niet als FastTrack uit staat (fast_min_batt): dan is SlowTrack de enige die de GPS aanzet, en in een
  // goed geveerde auto op een gladde weg kan de sensor minutenlang niets voelen. Dan blijft hij loggen,
  // en een SlowTrack-fix met snelheid telt als beweging (zoals bij FastTrack).
  bool resting = at_rest() && !s_fast_off;
  static bool was_resting = false;
  if (log_ms && resting != was_resting) {
    was_resting = resting;
    mt_log(resting ? "slowtrack: in rust, geen GPS tot er beweging is" : "slowtrack: beweging, weer loggen");
  }
  MtNmeaProvider& g = mt_gps();
  if (resting) {
    if (s_slow_acq) slow_release();
    s_slow_log_due = now;                        // bij de eerste beweging meteen een punt
  } else if (!log_ms) {
    if (s_slow_acq) slow_release();              // SlowTrack uitgezet terwijl hij een fix zocht
  } else if (s_slow_acq) {
    bool timeout = (int32_t)(now - s_slow_acq_deadline) >= 0;
    if (g.freshFix(2500) && (g.hdop() <= 2.5f || timeout)) { slow_take(g); slow_release(); }
    else if (timeout) {
      mt_log("slowtrack: geen fix binnen %u s", (unsigned)mt_cfg.fix_timeout_hb_s);
      slow_release();
    }
  } else if ((int32_t)(now - s_slow_log_due) >= 0) {
    s_slow_log_due += log_ms;
    if ((int32_t)(s_slow_log_due - now) <= 0) s_slow_log_due = now + log_ms;   // achterop (bv. na een lange fix)
    if (g.freshFix(2500)) slow_take(g);          // GPS staat al aan (FastTrack, klik): meteen
    else {
      s_slow_acq = s_slow_gps = true;
      s_slow_acq_deadline = now + 1000UL * mt_cfg.fix_timeout_hb_s;
      gps_want(true);
    }
  }

  // fifo: geen slow_send. Wat nog in de SlowTrack-buffer zat (van classic) gaat de wachtrij in.
  if (fifo_mode()) {
    if (s_nslow) {
      for (uint8_t k = 0; k < s_nslow; k++) {
        const MtSPt& q = s_slow[k];
        MtFPt f = { q.ts, q.lat, q.lon, q.spd, q.alt, 0, 0, 0 };
        fifo_add(f);
      }
      mt_log("fifo: %u SlowTrack-punten uit de buffer overgenomen, totaal %u", (unsigned)s_nslow, (unsigned)s_nfifo);
      s_nslow = 0;
    }
    s_slow_send_due = now + send_ms;           // na een terugkeer naar classic: een volle periode
    return;
  }

  // Versturen ook als slow_log net op 0 gezet is: wat in de buffer zit gaat nog mee.
  if ((int32_t)(now - s_slow_send_due) >= 0) {
    s_slow_send_due = now + send_ms;
    if (!s_nslow) return;
    if (!mt_sender_ready()) {
      mt_log("slowtrack: %u punten wachten, trackingkanaal %u ontbreekt", (unsigned)s_nslow, (unsigned)mt_cfg.chan_idx);
      return;
    }
    send_slow();
  }
}

uint8_t mt_tracker_slow_buffered() { return s_nslow; }
bool mt_tracker_fast_suspended() { return s_fast_off; }

// fast_min_batt: onder de grens geen FastTrack, terug aan vanaf grens + 3 % (geen
// heen-en-weer rond de grens). Met USB of fast_min_batt 0 altijd aan.
static void step_battery() {
  static uint32_t next = 0;
  if ((int32_t)(millis() - next) < 0) return;
  next = millis() + 30000;
  bool off = s_fast_off;
  int pct = mt_battery_pct(board.getBattMilliVolts());
  if (!mt_cfg.fast_min_batt || mt_usb() || pct < 0) off = false;
  else if (pct < mt_cfg.fast_min_batt) off = true;
  else if (pct >= mt_cfg.fast_min_batt + 3) off = false;
  if (off == s_fast_off) return;
  s_fast_off = off;
  mt_log(off ? "fasttrack uit: batterij %d%% < %u%% (SlowTrack, heartbeat, klik en SOS werken)"
             : "fasttrack weer aan: batterij %d%% (grens %u%%)", pct, (unsigned)mt_cfg.fast_min_batt);
}

static MtRuleParams params();

// ---- FIFO leegmaken -----------------------------------------------------------------
// Eén leegmaakbericht tegelijk: het volgende pas als het vorige herhaald is (en fifo_gap voorbij),
// zodat nooit dezelfde punten twee keer onderweg zijn. Niet herhaald = pogingenteller + wachttijd
// voor die punten, en stoppen tot er nieuwe dekking is.
static bool s_fl_active = false;            // leegmaken bezig
static bool s_fl_pending = false;           // al eens begonnen en er bleven punten over: hervatten vanaf 1 punt
static bool s_fl_inflight = false;          // een leegmaakbericht wacht op zijn uitkomst
static uint16_t s_fl_seq = 0;               // volgnummer van het bericht onderweg (T1F per volgnummer)
static bool s_fl_ever = false;              // al eens een leegmaakbericht verstuurd (sinds de start)
static bool s_fl_stopped = false;
static uint32_t s_fl_stop_ms = 0;           // dekking van voor deze stop telt niet om te hervatten
static uint32_t s_fl_sent_ms = 0;
static uint32_t s_fl_sent_unix = 0;
static uint32_t s_fl_tag = 0;               // fix_ts van het hoofdpunt van het bericht onderweg
static uint32_t s_fl_ts[MT_SLOW_MAX_ITEMS + 1];   // de punten van dat bericht
static uint8_t s_fl_n = 0;

// Getelde berichten (herhaald of door T1F bevestigd) en alle pogingen in het laatste uur.
static int fl_sent_last_hour(uint32_t* oldest_age_s = nullptr) { return ring_count(s_ring_cnt, oldest_age_s); }
static int fl_tries_last_hour(uint32_t* oldest_age_s = nullptr) { return ring_count(s_ring_try, oldest_age_s); }

// Welk plafond is bereikt? 0 = geen, 1 = fifo_per_uur (getelde berichten), 2 = pogingen (2x fifo_per_uur).
// wait_s = tot wanneer (het langste van de bereikte plafonds).
static int fl_cap(uint32_t* wait_s = nullptr) {
  uint32_t oc = 0, ot = 0, w = 0;
  int cap = 0;
  if (fl_tries_last_hour(&ot) >= 2 * mt_cfg.fifo_per_uur) { cap = 2; w = ot < 3600 ? 3600 - ot : 0; }
  if (fl_sent_last_hour(&oc) >= mt_cfg.fifo_per_uur) {
    uint32_t wc = oc < 3600 ? 3600 - oc : 0;
    if (!cap || wc > w) { cap = 1; w = wc; }
  }
  if (wait_s) *wait_s = cap ? w + 1 : 0;
  return cap;
}
static bool fl_capped() { return fl_cap() != 0; }

// Wachttijd voorbij? (retry_min 0 = meteen; een onmogelijk verre waarde = vergeten, dus ook meteen)
static bool fifo_due(const MtFPt& p) {
  int16_t d = (int16_t)(p.retry_min - now_min());
  return p.retry_min == 0 || d <= 0 || d > 60;
}
static bool fifo_is_parked(const MtFPt& p) { return p.tries >= mt_cfg.fifo_pogingen; }

// Welke punten mag het volgende leegmaakbericht meenemen? Oudste eerst, op tijd gesorteerd, en
// alleen punten waarvan de wachttijd voorbij is. Gewone punten gaan voor; geparkeerde punten (lagere
// voorrang, 60 min wachttijd) gaan mee als er geen gewone punten klaarstaan. Een punt wordt NOOIT
// opgegeven (0.9.4): het verdwijnt alleen na een herhaling, een T1F per volgnummer of als de wachtrij
// overloopt. Geeft het aantal (hooguit max_n) en zet *parked.
static int fifo_pick(uint16_t* sel, int max_n, bool* parked) {
  bool normal_due = false;
  for (uint16_t i = 0; i < s_nfifo && !normal_due; i++) normal_due = !fifo_is_parked(s_fifo[i]) && fifo_due(s_fifo[i]);
  *parked = !normal_due;
  int n = 0;
  for (uint16_t i = 0; i < s_nfifo && n < max_n; i++) {
    const MtFPt& p = s_fifo[i];
    if (fifo_is_parked(p) != *parked || !fifo_due(p)) continue;
    if (n && p.ts - s_fifo[sel[n - 1]].ts > 86400) break;   // de server neemt hooguit een dag tussen twee punten
    sel[n++] = i;
  }
  return n;
}

static void fifo_stop(const char* why) {
  if (s_fl_active) mt_log("fifo: leegmaken gestopt (%s), nog %u punten", why, (unsigned)s_nfifo);
  s_fl_active = false;
  s_fl_stopped = true;
  s_fl_stop_ms = millis();
  s_fl_pending = s_nfifo > 0;
}

static uint8_t fifo_backoff_min(uint8_t tries) {
  if (tries >= mt_cfg.fifo_pogingen) return 60;   // geparkeerd: lange wachttijd
  return tries <= 1 ? 1 : tries == 2 ? 5 : tries == 3 ? 15 : 60;
}

static void flush_done(bool heard) {
  if (!s_fl_inflight) return;
  s_fl_inflight = false;
  mt_log("fifo: bericht met %u punten verstuurd, %s", (unsigned)s_fl_n, heard ? "gehoord" : "niet gehoord");
  if (heard) {                                // belastte de mesh: telt voor fifo_per_uur, en is geslaagd
    ring_add(s_ring_cnt, s_fl_sent_ms, s_fl_sent_unix);
    last_ok_set(s_fl_sent_ms, s_fl_sent_unix);
    sent_mark_ok(s_fl_seq);                   // een latere T1F voor dit bericht telt niet nog eens
    fifo_remove_ts(s_fl_ts, s_fl_n);
  } else {                                     // teller op, wachttijd, punten blijven (nooit opgeven)
    for (int j = 0; j < s_fl_n; j++) {
      int k = fifo_find(s_fl_ts[j]);
      if (k < 0) continue;
      MtFPt& p = s_fifo[k];
      if (p.tries < 255) p.tries++;
      uint16_t r = (uint16_t)(now_min() + fifo_backoff_min(p.tries));
      p.retry_min = r ? r : 1;
    }
  }
  fifo_save();
  if (!s_nfifo) {
    s_fl_active = s_fl_pending = false;
    mt_log("fifo: wachtrij leeg");
  }
  if (!heard) fifo_stop("geen herhaling gehoord");
}

// Eén Q-bericht ("ingehaald punt"): hoofdpunt = het nieuwste gekozen punt (met eigen fix_ts), de
// oudere binair in veld 15 (zie append_bin_extras), elk met zijn echte tijd. Zoveel punten als
// passen (hooguit 31), oudste eerst gekozen.
static void send_flush(const uint16_t* sel, int cmax, bool parked) {
  int bat = mt_battery_pct(board.getBattMilliVolts());
  uint32_t now_unix = the_mesh.getRTCClock()->getCurrentTime();
  uint16_t seq = seq_next();
  // 16e veld, letters: "g" in ELK Q-bericht = "bevestig mij per volgnummer" (T1F met s<seqs>, 0.9.4);
  // "f" erbij alleen als er punten verstuurd zijn waarvan geen herhaling gehoord werd: dan stuurt de
  // server (hooguit eens per 10 min) een T1F.
  bool want_f = false;
  for (uint16_t i = 0; i < s_nfifo && !want_f; i++) want_f = (s_fifo[i].flags & FPT_SENT) != 0;
  const char* flags = want_f ? "fg" : "g";
  const int limit = MT_TEXT_MAX - the_mesh.mtSenderLen() - 19 - 2 - (int)strlen(flags) - 2;   // zoals send_report
  char text[MT_TEXT_MAX + 1];
  XPt x[MT_SLOW_MAX_ITEMS + 1];
  int c = cmax, len = 0;
  for (; c >= 1; c--) {
    const MtFPt& m = s_fifo[sel[c - 1]];
    char age[12] = "", alt[8] = "";
    if (now_unix >= m.ts && now_unix - m.ts < 10000000UL) snprintf(age, sizeof(age), "%lu", (unsigned long)(now_unix - m.ts));
    if (m.alt != MT_ALT_NONE) snprintf(alt, sizeof(alt), "%d", (int)m.alt);
    len = snprintf(text, sizeof(text), "T1|%u|Q|%.5f|%.5f|%s|%u||%d||%s|%c|%c|%lu",
                   (unsigned)seq, m.lat / 1e5, m.lon / 1e5, alt, (unsigned)m.spd, bat < 0 ? 0 : bat, age,
                   mt_effective_mode() == MT_MODE_TRACKER ? 't' : 'c', mt_usb() ? 'u' : 'b', (unsigned long)m.ts);
    if (c == 1) break;
    for (int i = 0; i < c; i++) x[i] = { s_fifo[sel[i]].ts, s_fifo[sel[i]].lat, s_fifo[sel[i]].lon };
    if (append_bin_extras(text, len, limit, x, c)) break;   // anders: één punt minder
  }
  if (c < 1) c = 1;
  len += snprintf(text + len, sizeof(text) - len, c == 1 ? "||%s" : "|%s", flags);   // veld 15 leeg bij 1 punt
  s_fl_n = (uint8_t)c;
  for (int i = 0; i < c; i++) s_fl_ts[i] = s_fifo[sel[i]].ts;
  s_fl_tag = s_fl_ts[c - 1];
  s_fl_seq = seq;
  s_fl_inflight = s_fl_ever = true;
  s_fl_sent_ms = millis();
  s_fl_sent_unix = rtc_unix();
  ring_add(s_ring_try, s_fl_sent_ms, s_fl_sent_unix);   // elke poging telt voor de veiligheidsgrens
  uint16_t total = s_nfifo;
  // on_send_done (meestal meteen, binnen sign_and_send) herkent het aan s_fl_inflight + tag + 'Q'.
  sign_and_send(text, 'Q', false, true, s_fl_tag);
  if (s_fl_inflight) {                         // verstuurd: punten markeren en het bericht onthouden (T1F)
    for (int i = 0; i < c; i++) { int k = fifo_find(s_fl_ts[i]); if (k >= 0) s_fifo[k].flags |= FPT_SENT; }
    sent_add(seq, s_fl_sent_ms, s_fl_sent_unix, s_fl_ts, c);
  }
  strncpy(s_last_reason, "fifo", sizeof(s_last_reason) - 1);
  s_last_tx_ms = millis();
  mt_log("tx %s (fifo: seq %u, %d van %u punten%s%s)", text, (unsigned)seq, c, (unsigned)total,
         parked ? ", geparkeerde punten" : "", want_f ? ", vraagt T1F" : "");
}

// Punten per Q-bericht (schatting op basis van de echte nodenaam): "<naam>: " gaat van de MT_TEXT_MAX
// tekens af, de vaste velden kosten ~79 tekens (incl. "|f"), een binair punt ~8. Zelfde formule als
// serial.js (fifoPerMsg). Een "vol" inhaalbericht = minstens zoveel punten klaar.
uint32_t mt_fifo_per_msg() {
  int room = MT_TEXT_MAX - the_mesh.mtSenderLen() - 79;
  int n = 1 + (room > 0 ? room / 8 : 0);
  return n < 2 ? 2 : n;
}

// Seconden sinds het laatste geslaagde inhaalbericht; -1 = nooit (of onbekend: klok niet gezet).
static long last_ok_age() {
  if (s_ok_ms) return (long)((millis() - s_ok_ms) / 1000);
  uint32_t u = rtc_unix();
  if (s_ok_ux && u && u >= s_ok_ux) return (long)(u - s_ok_ux);
  return -1;
}
static long last_partial_age() {
  if (s_pa_ms) return (long)((millis() - s_pa_ms) / 1000);
  uint32_t u = rtc_unix();
  if (s_pa_ux && u && u >= s_pa_ux) return (long)(u - s_pa_ux);
  return -1;
}
// Seconden tot een niet-vol inhaalbericht in de tijd mag: 0 = nu, -1 = nooit (fifo_wacht uit). Beide
// moeten minstens fifo_wacht geleden zijn: het laatste geslaagde inhaalbericht en de laatste poging met
// een niet-vol bericht (hooguit één niet-vol bericht per fifo_wacht, geslaagd of niet).
static long partial_wait() {
  uint32_t w = mt_fifo_wacht_min();
  if (!w) return -1;
  long wait = 0;
  long a = last_ok_age(), b = last_partial_age();
  if (a >= 0 && a < (long)(w * 60)) wait = (long)(w * 60) - a;
  if (b >= 0 && b < (long)(w * 60) && (long)(w * 60) - b > wait) wait = (long)(w * 60) - b;
  return wait;
}
// Niet-vol bericht: de laatste dekking (< 30 s) moet sterk zijn (SNR >= fifo_snr of T1F); twee keer
// dekking binnen 60 s volstaat hier niet.
static bool partial_snr_ok() { return s_cov_have && millis() - s_cov_ms < 30000UL && s_cov_last_strong; }
static bool s_fifo_full = false;            // er staat minstens één vol bericht klaar (step_fifo)

static void pts_age_out();
static void step_fifo() {
  uint32_t now = millis();
  pts_age_out();
  uint16_t cap = fifo_cap();
  if (s_nfifo > cap) {                         // fifo_max verkleind: de minst betekenisvolle punten weg
    while (s_nfifo > cap) fifo_evict_one();
    fifo_save();
  }
  if (s_fifo_unsaved && (s_fifo_unsaved >= 10 || now - s_fifo_unsaved_since >= 15UL * 60000UL)) fifo_save();
  if (s_fl_inflight && now - s_fl_sent_ms > 90000UL) flush_done(false);   // nooit een uitkomst: als niet gehoord
  if (!fifo_mode() || !tracking_active()) {
    if (s_fl_active) fifo_stop(fifo_mode() ? "tracking uit" : "classic");
    return;
  }
  if (s_fl_inflight || !s_nfifo) return;
  uint16_t sel[MT_SLOW_MAX_ITEMS + 1];
  bool parked = false;
  int cand = fifo_pick(sel, MT_SLOW_MAX_ITEMS + 1, &parked);
  // Thuis of in companionmodus met sterke dekking: geparkeerde punten krijgen een nieuwe kans (teller
  // op 0), hooguit eens per 30 min. Zo raakt thuis alles weg.
  {
    static uint32_t reset_ms = 0;
    static bool reset_ever = false;
    if ((at_rest() || mt_effective_mode() == MT_MODE_COMPANION) && partial_snr_ok() &&
        (!reset_ever || now - reset_ms >= 30UL * 60000UL)) {
      uint16_t n = 0;
      for (uint16_t i = 0; i < s_nfifo; i++)
        if (fifo_is_parked(s_fifo[i])) { s_fifo[i].tries = 0; s_fifo[i].retry_min = 0; n++; }
      if (n) {
        reset_ever = true;
        reset_ms = now;
        fifo_save();
        mt_log("fifo: sterke dekking in rust: %u geparkeerde punten krijgen een nieuwe kans", (unsigned)n);
        cand = fifo_pick(sel, MT_SLOW_MAX_ITEMS + 1, &parked);
      }
    }
  }
  // 0.9.3: een VOL inhaalbericht (minstens mt_fifo_per_msg() punten klaar) volgt de gewone regels; een
  // NIET-VOL bericht mag alleen als het laatste geslaagde inhaalbericht minstens fifo_wacht geleden is
  // (fifo_wacht uit = nooit). Zo komen er geen reeksen berichten met maar 1 of 2 punten meer.
  // Geldt ook voor hervatten na een stop, voor in rust en voor geparkeerde punten.
  bool full = cand >= (int)mt_fifo_per_msg();
  s_fifo_full = full;
  // Niet-vol: alleen als de radio toch al wakker is (na een eigen bericht en zijn luistervenster, als
  // companion, tijdens een ronde met volle berichten of een luistervenster voor een volle wachtrij),
  // met sterke dekking, en in de tijd toegelaten. Nooit de radio wekken voor een niet-vol bericht.
  bool partial_ok = cand && partial_wait() == 0 && partial_snr_ok() && !mt_radio_paused();
  bool may_send = full || partial_ok;
  if (!s_fl_active) {
    // fifo_min blijft de drempel om een ronde te beginnen; na een stop en in rust volstaat 1 punt,
    // maar dan beslist de regel hierboven of een niet-vol bericht al mag.
    bool rest = at_rest();
    uint16_t need = (s_fl_pending || rest) ? 1 : mt_cfg.fifo_min;
    // Stilstaand in trackermodus verstuurt FastTrack niets en slaapt de radio: dan ziet de tracker nooit
    // dekking. Kan er binnenkort iets weg (vol meteen, niet-vol pas als fifo_wacht voorbij is) zonder
    // recente dekking, dan 60 s luisteren. Geen dekking gevonden: telkens langer wachten (10, 20, 40,
    // dan elke 60 min), zodat een tracker op een plek zonder bereik zijn batterij niet leegluistert.
    // Beweging of gevonden dekking zet de wachttijd terug op 10 min.
    static uint32_t probe_next = 0, probe_gap = 600000UL;
    static bool rest_before = false, may_before = false;
    bool cov_fresh = s_cov_stable && now - s_cov_stable_ms < 30000UL;
    // Alleen voor een VOLLE wachtrij: nooit luisteren (radio wekken) voor een niet-vol bericht.
    if (rest && (!rest_before || (full && !may_before))) probe_next = now;   // net in rust, of net vol: meteen
    if (!rest || cov_fresh) probe_gap = 600000UL;
    rest_before = rest;
    may_before = full;
    if (rest && full && !cov_fresh && !fl_capped() && (int32_t)(now - probe_next) >= 0) {
      probe_next = now + probe_gap;
      if (probe_gap < 3600000UL) probe_gap = probe_gap * 2 > 3600000UL ? 3600000UL : probe_gap * 2;
      if ((int32_t)(now + 60000UL - s_listen_until) > 0) s_listen_until = now + 60000UL;
      mt_log("fifo: in rust met %u punten (vol bericht klaar); 60 s luisteren naar repeaters", (unsigned)s_nfifo);
    }
    // Een nieuwe ronde alleen bij stabiele dekking (fifo_snr, twee keer binnen 60 s of een T1F).
    if (s_nfifo < need || !may_send || !s_cov_stable || now - s_cov_stable_ms >= 30000UL) return;
    if (s_fl_stopped && (int32_t)(s_cov_stable_ms - s_fl_stop_ms) <= 0) return;   // dekking van voor de stop
    if (fl_capped()) return;
    s_fl_active = s_fl_pending = true;
    mt_log("fifo: leegmaken begint (%u punten, %s bericht, stabiele dekking %lu s geleden)", (unsigned)s_nfifo,
           full ? "vol" : "niet-vol", (unsigned long)((now - s_cov_stable_ms) / 1000));
  }
  if (!cand) {                                 // alles wacht nog (wachttijd): pauze, radio mag slapen
    s_fl_active = false;
    mt_log("fifo: leegmaken gepauzeerd (de %u punten wachten op een nieuwe poging)", (unsigned)s_nfifo);
    return;
  }
  if (!may_send) {                             // rest is niet vol en (nog) niet toegelaten
    s_fl_active = false;
    long w = partial_wait();
    if (w > 0) mt_log("fifo: leegmaken gepauzeerd (%d punten, niet vol; niet-vol bericht ten vroegste over %ld min)", cand, (w + 59) / 60);
    else if (w < 0) mt_log("fifo: leegmaken gepauzeerd (%d punten, niet vol; fifo_wacht uit: wacht op een vol bericht)", cand);
    else mt_log("fifo: leegmaken gepauzeerd (%d punten, niet vol; wacht op sterke dekking terwijl de radio toch wakker is)", cand);
    return;
  }
  if (s_fl_ever && now - s_fl_sent_ms < 1000UL * mt_cfg.fifo_gap_s) return;
  if (fl_capped() || mt_sender_busy()) return;
  if (!mt_sender_ready()) { fifo_stop("trackingkanaal ontbreekt"); return; }
  if (!full) {                                 // poging met een niet-vol bericht: hooguit één per fifo_wacht
    s_pa_ms = millis() | 1;
    s_pa_ux = rtc_unix();
  }
  send_flush(sel, cand, parked);
}

// fifo: hoofdpunten die al 10 min in de FastTrack-buffer zitten zonder herhaald bericht (bv. omdat
// de tracker stilstaat en niets meer verstuurt), naar de FIFO: anders gaan ze verloren bij een herstart.
static void pts_age_out() {
  static uint32_t next = 0;
  if (!fifo_mode() || (int32_t)(millis() - next) < 0) return;
  next = millis() + 10000;
  uint32_t now = rtc_unix();
  if (!now) return;
  uint8_t w = 0;
  for (uint8_t r = 0; r < s_npts; r++) {
    const MtPt& q = s_pts[r];
    if ((q.fl & PT_MAIN) && q.ts <= now && now - q.ts > 600) { fifo_take_main(q); continue; }
    s_pts[w++] = q;
  }
  s_npts = w;
}

// In trackermodus de radio wakker houden: leegmaken bezig (niet als het uurplafond bereikt is).
// Nooit wakker houden voor een niet-vol bericht alleen.
static bool fifo_keep_awake() { return s_fl_inflight || (s_fl_active && s_fifo_full && !fl_capped()); }

// Een kanaalbericht kent geen ACK: "ok" = de radio heeft het verstuurd. Met terugmelding
// (manual) bewaken we daarna of een repeater het herhaalt.
// Automatische berichten (niet klik/SOS) voor tx_beep en heard_beep: eerst wachten tot de
// radio het pakket echt verzendt (mt_tx_packet), daarna MT_HEAR_MS luisteren naar herhalingen.
// fifo: elk bericht met een positie (TXW_FIFO, ook klik en SOS, zonder biep) en elk leegmaakbericht
// (TXW_FLUSH, toestand Q) wordt op dezelfde manier bewaakt.
#define TXW_USED  1
#define TXW_BEEP  2     // tx_beep: biep bij verzenden
#define TXW_HEAR  4     // heard_beep: herhalingen bewaken
#define TXW_SENT  8     // de radio heeft het verzonden
#define TXW_HEARD 16    // al gebiept voor een herhaling
#define TXW_FIFO  32    // fifo: FastTrack-bericht; herhaald = zijn punten zijn binnen (pts_heard)
#define TXW_FLUSH 64    // fifo: leegmaakbericht
#define TXW_REP   128   // er is een herhaling gehoord
#define TXW_FAST  256   // automatisch FastTrack-bericht (dump: gemist/gehoord tellen)
#define TXW_LISTEN (TXW_HEAR | TXW_FIFO | TXW_FLUSH)
struct TxWatch { uint8_t block[16]; uint32_t t; uint16_t fl; uint32_t lo, hi; };   // lo..hi: punten van een fifo-bericht

// Tellers sinds de start en laatste gebeurtenissen (CLI 'dump', webpagina /tracker).
static uint32_t s_cnt_fast = 0, s_cnt_slow = 0, s_cnt_q = 0, s_cnt_heard = 0, s_cnt_missed = 0;
static uint32_t s_ltx_unix = 0;             // laatste bericht naar de radio (unix), toestand, gelukt
static char s_ltx_state = 0;
static bool s_ltx_ok = false;
static uint32_t s_lh_unix = 0;              // laatste herhaling van een eigen bericht
static float s_lh_snr = 0;
static char s_lh_rep[7] = "-";
#define MT_TXW 6
static TxWatch s_txw[MT_TXW];

// Bewaking afsluiten. FastTrack-bericht zonder herhaling: niets (zijn punten blijven in de buffer en
// reizen mee met de volgende berichten); leegmaakbericht = uitkomst.
static void txw_finish(TxWatch& w) {
  if ((w.fl & TXW_FAST) && (w.fl & (TXW_HEAR | TXW_FIFO)) && !(w.fl & TXW_REP)) s_cnt_missed++;   // beluisterd, niet herhaald
  if ((w.fl & (TXW_FIFO | TXW_REP)) == (TXW_FIFO | TXW_REP) && fifo_mode()) pts_heard(w.lo, w.hi);
  if (w.fl & TXW_FLUSH) flush_done((w.fl & TXW_REP) != 0);
  w.fl = 0;
}

static int txw_add(const uint8_t* block, uint16_t fl) {
  int k = 0;
  for (int i = 0; i < MT_TXW; i++) {
    if (!(s_txw[i].fl & TXW_USED)) { k = i; break; }
    if ((int32_t)(s_txw[i].t - s_txw[k].t) < 0) k = i;     // vol: de oudste wijkt
  }
  if (s_txw[k].fl & TXW_USED) txw_finish(s_txw[k]);        // verdrongen: als niet gehoord
  memcpy(s_txw[k].block, block, 16);
  s_txw[k].t = millis();
  s_txw[k].fl = TXW_USED | fl;
  return k;
}

static void on_send_done(bool ok, bool manual, uint32_t tag, char state) {
  bool flush = s_fl_inflight && state == 'Q' && tag && tag == s_fl_tag;
  bool fifo_watch = !flush && s_cap_set && tag && tag == s_cap_hi && state != 'L' && state != 'Q';
  if (fifo_watch) s_cap_set = false;
  // fifo: de punten blijven in de buffer tot een herhaling gehoord is (pts_heard), niet bij verzenden.
  if (ok && tag && !flush) { if (state == 'L') slow_sent(tag); else if (!fifo_watch) pts_sent(tag); }
  if (state) {
    s_ltx_unix = the_mesh.getRTCClock()->getCurrentTime();
    s_ltx_state = state;
    s_ltx_ok = ok;
    if (ok) { if (state == 'L') s_cnt_slow++; else if (state == 'Q') s_cnt_q++; else s_cnt_fast++; }
  }
  uint16_t fl = 0;
  // Klik en SOS hebben hun eigen terugmelding: geen tx_beep of heard_beep (geen dubbele biep).
  if (ok && !manual && state && state != 'E') fl |= (mt_cfg.tx_beep ? TXW_BEEP : 0) | (mt_cfg.heard_beep ? TXW_HEAR : 0);
  if (ok && fifo_watch) fl |= TXW_FIFO;
  if (ok && flush) fl |= TXW_FLUSH;
  if (fl && !manual && state != 'L' && state != 'Q') fl |= TXW_FAST;   // klik/eerste SOS telt via s_w
  if (fl) {
    int k = txw_add(mt_sender_last_block(), fl);
    if (fl & TXW_FIFO) { s_txw[k].lo = s_cap_lo; s_txw[k].hi = s_cap_hi; }
    if ((fl & TXW_LISTEN) && (int32_t)(millis() + MT_HEAR_MS - s_listen_until) > 0) s_listen_until = millis() + MT_HEAR_MS;
  }
  if (flush && !ok) flush_done(false);
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

// De radio heeft een pakket verzonden (MyMesh::logTx). Een GRP_TXT met het eerste cijferblok
// van een bewaakt bericht = ons positiebericht is echt de lucht in.
void mt_tx_packet(uint8_t payload_type, const uint8_t payload[], int len) {
  if (payload_type != PAYLOAD_TYPE_GRP_TXT || len < 3 + 16) return;
  for (int i = 0; i < MT_TXW; i++) {
    TxWatch& w = s_txw[i];
    if ((w.fl & (TXW_USED | TXW_SENT)) != TXW_USED || memcmp(payload + 3, w.block, 16) != 0) continue;
    w.fl |= TXW_SENT;
    w.t = millis();
    if (w.fl & TXW_BEEP) ui_task.playForced(MT_TUNE_TX);
    if (w.fl & TXW_LISTEN) {
      if ((int32_t)(w.t + MT_HEAR_MS - s_listen_until) > 0) s_listen_until = w.t + MT_HEAR_MS;
    } else {
      w.fl = 0;
    }
    return;
  }
}

static void step_watch_auto() {
  uint32_t now = millis();
  for (int i = 0; i < MT_TXW; i++) {
    TxWatch& w = s_txw[i];
    if (!(w.fl & TXW_USED)) continue;
    if ((w.fl & (TXW_FLUSH | TXW_REP)) == (TXW_FLUSH | TXW_REP)) {   // leegmaakbericht herhaald: meteen verder
      w.fl &= ~TXW_FLUSH;
      flush_done(true);
    }
    if ((w.fl & (TXW_FIFO | TXW_REP)) == (TXW_FIFO | TXW_REP)) {     // FastTrack-bericht herhaald
      w.fl &= ~TXW_FIFO;
      if (fifo_mode()) pts_heard(w.lo, w.hi);
    }
    if (!(w.fl & TXW_SENT) && now - w.t > 60000) txw_finish(w);                  // nooit verzonden
    else if ((w.fl & TXW_SENT) && now - w.t > MT_HEAR_MS) txw_finish(w);         // luistertijd voorbij
  }
}

static void step_watch() {
  step_watch_auto();
  if (!s_w_wait || (int32_t)(millis() - s_w_deadline) < 0) return;
  s_w_wait = false;
  s_cnt_missed++;
  ui_task.playForced(MT_TUNE_NOK);
  mt_log("geen herhaling gehoord binnen %u s", (unsigned)(MT_HEAR_MS / 1000));
}

// Ruw pakket: [header][4 transportcodes bij route 0/3][path_len][pad][payload]. GRP_TXT-payload
// = [kanaalhash 1][MAC 2][cijferblokken...]; is het eerste blok het onze, dan is dit een herhaling.
// Ontvangstlog (CLI 'rxlog aan', tot een herstart): elk kanaalpakket van de radio en elk ontcijferd
// kanaalbericht, om te zien waar een bericht blijft.
static bool s_rxlog = false;
void mt_set_rxlog(bool on) { s_rxlog = on; }

void mt_rx_raw(float snr, const uint8_t raw[], int len) {
  const bool strong = snr >= mt_cfg.fifo_snr;     // fifo: genoeg voor een nieuwe leegmaakronde
  if (s_rxlog && len >= 2 && ((raw[0] >> 2) & 15) == PAYLOAD_TYPE_GRP_TXT) {
    int j = 1 + (((raw[0] & 3) == 0 || (raw[0] & 3) == 3) ? 4 : 0);
    if (j < len) {
      uint8_t q = raw[j];
      int k = j + 1 + ((q >> 6) + 1) * (q & 63);
      if (k < len) mt_log("rx kanaalpakket hash %02x, %u hops, %d bytes", raw[k], (unsigned)(q & 63), len);
    }
  }
  if (len < 2) return;
  uint8_t route = raw[0] & 3;
  // Dekking (fifo): eender welk flood-pakket dat al via minstens één repeater kwam.
  if (route == ROUTE_TYPE_FLOOD || route == ROUTE_TYPE_TRANSPORT_FLOOD) {
    int j = 1 + (route == ROUTE_TYPE_TRANSPORT_FLOOD ? 4 : 0);
    if (j < len) {
      uint8_t q = raw[j];
      int qs = (q >> 6) + 1, qc = q & 63;
      if (qs <= 3 && qc >= 1 && j + 1 + qs * qc <= len) {
        char rep[7];
        for (int k = 0; k < qs; k++) snprintf(rep + 2 * k, 3, "%02x", raw[j + 1 + (qc - 1) * qs + k]);
        fifo_coverage("pakket via repeater", (unsigned)qc, rep, strong, snr);
      }
    }
  }
  if (((raw[0] >> 2) & 15) != PAYLOAD_TYPE_GRP_TXT) return;
  int i = 1 + ((route == 0 || route == 3) ? 4 : 0);
  if (i >= len) return;
  uint8_t pl = raw[i++];
  int hs = (pl >> 6) + 1, hc = pl & 63;
  if (hs > 3) return;
  int path = i;
  i += hs * hc;
  if (i + 3 + 16 > len) return;
  const uint8_t* blk = raw + i + 3;
  bool manual_hit = s_w_have && memcmp(blk, s_w_block, 16) == 0;
  int auto_hit = -1;                           // bewaakt bericht (heard_beep of fifo); ook bij een klik met fifo-bewaking
  for (int k = 0; k < MT_TXW; k++) {
    if ((s_txw[k].fl & (TXW_USED | TXW_SENT)) == (TXW_USED | TXW_SENT) && (s_txw[k].fl & TXW_LISTEN) &&
        memcmp(blk, s_txw[k].block, 16) == 0) { auto_hit = k; break; }
  }
  if (!manual_hit && auto_hit < 0) return;
  char rep[7] = "-";
  if (hc) for (int k = 0; k < hs; k++) snprintf(rep + 2 * k, 3, "%02x", raw[path + (hc - 1) * hs + k]);
  fifo_coverage("eigen bericht herhaald", (unsigned)hc, rep, strong, snr);
  // dump: elk bericht één keer als gehoord tellen (een klik met fifo-bewaking niet dubbel)
  if (manual_hit ? s_w_heard == 0 : !(s_txw[auto_hit].fl & TXW_REP)) s_cnt_heard++;
  s_lh_unix = the_mesh.getRTCClock()->getCurrentTime();
  s_lh_snr = snr;
  memcpy(s_lh_rep, rep, sizeof(s_lh_rep));
  if (auto_hit >= 0) s_txw[auto_hit].fl |= TXW_REP;
  if (manual_hit) {
    s_w_heard++;
    mt_log("gehoord via repeater %s (%u hops)", rep, (unsigned)hc);
    if (s_w_wait) { s_w_wait = false; ui_task.playForced(MT_TUNE_OK); }
  } else {
    TxWatch& w = s_txw[auto_hit];
    mt_log("%s gehoord via repeater %s (%u hops)", (w.fl & TXW_FLUSH) ? "fifo-bericht" : "automatisch bericht", rep, (unsigned)hc);
    if ((w.fl & TXW_HEAR) && !(w.fl & TXW_HEARD)) { w.fl |= TXW_HEARD; ui_task.playForced(MT_TUNE_OK); }
  }
}

// "<naam>: T1A|<pk8>|<tag8>|<seq>" op het trackingkanaal: de server bevestigt een SOS.
// tag8 = HMAC-SHA256(authsleutel, "<pk8>|A|<seq>"), eerste 4 bytes.
// Zelfde kanaal als het trackingkanaal = zelfde geheim (hetzelfde kanaal kan op twee nummers staan).
static bool is_track_channel(uint8_t chan_idx) {
  ChannelDetails a, b;
  return the_mesh.mtGetChannel(chan_idx, a) && the_mesh.mtGetChannel(mt_cfg.chan_idx, b) &&
         memcmp(a.channel.secret, b.channel.secret, 16) == 0;
}

// "T1F|<pk8>:<tag8>:s<seqs>|<pk8>:<tag8>:s<seqs>|..." (hooguit 4 trackers, 0.9.4): de server kreeg de
// Q-berichten met die volgnummers ("2301,2305-2307"). tag8 = HMAC-SHA256(authsleutel, "<pk8>|F|s<seqs>"),
// eerste 4 bytes. Alleen ons eigen deel telt. De oude vorm "<pk8>:<tag8>:<upto>" wordt genegeerd: een
// tijdsbereik wiste in 0.9.3 punten die de server nooit gekregen had.
static void handle_t1f(uint8_t chan_idx, const char* p) {
  if (!is_track_channel(chan_idx)) {
    mt_log("T1F op kanaal %u genegeerd (trackingkanaal is %u)", (unsigned)chan_idx, (unsigned)mt_cfg.chan_idx);
    return;
  }
  char pk[9];
  own_pk8(pk);
  for (const char* e = p; e && *e; ) {
    const char* next = strchr(e, '|');
    int elen = next ? (int)(next - e) : (int)strlen(e);
    if (elen >= 19 && strncmp(e, pk, 8) == 0 && e[8] == ':' && e[17] == ':') {
      const char* v = e + 18;
      int vlen = elen - 18;
      if (*v != 's') { mt_log("T1F met tijdsbereik genegeerd (0.9.4: alleen bevestiging per volgnummer)"); return; }
      const char* l = v + 1;
      int llen = vlen - 1;
      if (!seq_list_valid(l, llen) || llen > 120) { mt_log("T1F met ongeldige volgnummerlijst genegeerd"); return; }
      char body[140], tag[9];
      snprintf(body, sizeof(body), "%s|F|s%.*s", pk, llen, l);
      auth_tag(body, tag);
      if (!mt_cfg.authkey_set || strncmp(e + 9, tag, 8) != 0) { mt_log("T1F met foute handtekening genegeerd"); return; }
      fifo_coverage("T1F van de server", 0, "-", true, MT_SNR_NONE);
      int msgs, late;
      uint16_t n = fifo_confirm_seqs(l, llen, &msgs, &late);
      mt_log("fifo: server bevestigt s%.*s: %d bekende berichten (%d niet gehoord), %u punten uit de wachtrij, nog %u",
             llen > 60 ? 60 : llen, l, msgs, late, (unsigned)n, (unsigned)s_nfifo);
      return;
    }
    e = next ? next + 1 : nullptr;
  }
  if (s_rxlog) mt_log("T1F zonder deel voor deze tracker");
}

// ---- locatieverzoeken (0.9.5) --------------------------------------------------------------
// "T1R|<doel>|<nonce>" op het trackingkanaal (van eender welke companion, bv. de app via een tracker als
// companion): doel = "*" (iedereen) of de pk8 van één tracker (8 hex, hoofdletters mogen), nonce = 4 tot 8
// hex. Antwoord = een gewoon T1C-positiebericht met toestand V ("op verzoek"), met positie als er een fix
// is. Ontdubbeld op nonce (8 laatste, 10 min), hooguit 1x per 120 s voor "*" en 1x per 30 s gericht,
// na een willekeurige wachttijd (2-20 s voor "*", 1-3 s gericht) zodat niet alle trackers tegelijk zenden.
struct MtNonce { char n[9]; uint32_t ms; };
static MtNonce s_nonces[8];
static uint8_t s_nonce_i = 0;
static bool s_req_pending = false, s_req_gps_started = false;
static uint32_t s_req_due = 0, s_req_deadline = 0;
static uint32_t s_req_star_ms = 0, s_req_tgt_ms = 0;
static bool s_req_star_ever = false, s_req_tgt_ever = false;
static uint32_t s_req_last_unix = 0, s_req_answered = 0, s_req_ignored = 0;

static void req_ignore(const char* why) {
  s_req_ignored++;
  mt_log("verzoek genegeerd: %s", why);
}

static void handle_t1r(uint8_t chan_idx, const char* text, const char* p) {
  if (!is_track_channel(chan_idx)) {
    mt_log("T1R op kanaal %u genegeerd (trackingkanaal is %u)", (unsigned)chan_idx, (unsigned)mt_cfg.chan_idx);
    return;
  }
  // p = "<doel>|<nonce>"
  const char* bar = strchr(p, '|');
  if (!bar) { req_ignore("ongeldig formaat"); return; }
  int tl = bar - p;
  const char* nonce = bar + 1;
  int nl = strlen(nonce);
  while (nl && (nonce[nl - 1] == ' ' || nonce[nl - 1] == '\r' || nonce[nl - 1] == '\n')) nl--;
  bool nonce_ok = nl >= 4 && nl <= 8;
  for (int i = 0; i < nl && nonce_ok; i++) nonce_ok = isxdigit((unsigned char)nonce[i]);
  bool star = tl == 1 && p[0] == '*';
  char pk[9];
  own_pk8(pk);
  if (!star) {
    if (tl != 8) { req_ignore("ongeldig doel"); return; }
    for (int i = 0; i < 8; i++) if (tolower((unsigned char)p[i]) != pk[i]) return;   // voor een andere tracker: stil
  }
  if (!nonce_ok) { req_ignore("ongeldige nonce"); return; }
  if (mt_cfg.verzoek_uit) { req_ignore("verzoek uit"); return; }
  char nn[9];
  for (int i = 0; i < nl; i++) nn[i] = (char)tolower((unsigned char)nonce[i]);
  nn[nl] = 0;
  uint32_t now = millis();
  for (int i = 0; i < 8; i++)
    if (s_nonces[i].ms && now - s_nonces[i].ms < 600000UL && !strcmp(s_nonces[i].n, nn)) { req_ignore("al gezien (zelfde nonce)"); return; }
  if (s_req_pending) { req_ignore("er loopt al een verzoek"); return; }
  if (star && s_req_star_ever && now - s_req_star_ms < 120000UL) { req_ignore("* hooguit 1x per 120 s"); return; }
  if (!star && s_req_tgt_ever && now - s_req_tgt_ms < 30000UL) { req_ignore("gericht hooguit 1x per 30 s"); return; }
  MtNonce& slot = s_nonces[s_nonce_i];
  s_nonce_i = (uint8_t)((s_nonce_i + 1) % 8);
  memcpy(slot.n, nn, nl + 1);
  slot.ms = now | 1;
  if (star) { s_req_star_ms = now; s_req_star_ever = true; } else { s_req_tgt_ms = now; s_req_tgt_ever = true; }
  uint32_t jitter = star ? (uint32_t)random(2000, 20001) : (uint32_t)random(1000, 3001);
  s_req_pending = true;
  s_req_gps_started = false;
  s_req_due = now + jitter;
  s_req_last_unix = the_mesh.getRTCClock()->getCurrentTime();
  // afzendernaam = alles voor ": " (de companion zet die er meestal voor)
  char name[33] = "?";
  if (p != text + 4) {
    int k = (int)(p - 6 - text);
    if (k > 0) { if (k > 32) k = 32; memcpy(name, text, k); name[k] = 0; }
  }
  mt_log("verzoek van %s voor %s, antwoord over %lu s", name, star ? "*" : pk, (unsigned long)((jitter + 999) / 1000));
  if (mt_cfg.verzoek_beep) ui_task.playForced(MT_TUNE_REQ);   // ook met de buzzer gedempt (3x klikken)
}

static void step_req() {
  if (!s_req_pending || (int32_t)(millis() - s_req_due) < 0) return;
  MtNmeaProvider& g = mt_gps();
  bool fresh = g.freshFix(30000);
  if (!fresh && !s_req_gps_started) {          // geen verse fix: GPS aan (zoals een klik), hooguit fix_timeout_hb
    s_req_gps_started = true;
    s_req_gps = true;
    s_req_deadline = millis() + 1000UL * mt_cfg.fix_timeout_hb_s;
    gps_want(true);
    return;
  }
  if (!fresh && (int32_t)(millis() - s_req_deadline) < 0) return;
  if (!mt_sender_ready()) {
    mt_log("verzoek niet beantwoord: trackingkanaal %u ontbreekt op het toestel", (unsigned)mt_cfg.chan_idx);
  } else {
    if (fresh || !s_lf.ts) {
      send_report('V', fresh, false, "verzoek");
      mt_log("verzoek beantwoord (V%s)", fresh ? "" : ", zonder positie (nog nooit een fix)");
    } else {                                   // geen verse fix: de laatst gekende plek, met zijn echte tijd
      uint32_t now_unix = the_mesh.getRTCClock()->getCurrentTime();
      s_pos_override = &s_lf;
      send_report('V', true, false, "verzoek");
      s_pos_override = nullptr;
      mt_log("verzoek beantwoord (V, laatst gekend, %lu min oud)",
             (unsigned long)(now_unix >= s_lf.ts ? (now_unix - s_lf.ts) / 60 : 0));
    }
    s_req_answered++;
  }
  s_req_pending = false;
  if (s_req_gps) {
    s_req_gps = false;
    if (!fast_needs_gps()) gps_want(false);    // uit als niets anders hem nodig heeft
  }
}

bool mt_channel_text(uint8_t chan_idx, const char* text) {
  if (s_rxlog) mt_log("rx kanaalbericht op kanaal %u: %.40s", (unsigned)chan_idx, text);
  {
    const char* f = strncmp(text, "T1F|", 4) == 0 ? text + 4 : nullptr;
    if (!f) { f = strstr(text, ": T1F|"); if (f) f += 6; }
    if (f) { handle_t1f(chan_idx, f); return true; }
    const char* r = strncmp(text, "T1R|", 4) == 0 ? text + 4 : nullptr;
    if (!r) { r = strstr(text, ": T1R|"); if (r) r += 6; }
    if (r) { handle_t1r(chan_idx, text, r); return true; }
  }
  // "T1A|" na de afzendernaam ("naam: T1A|...") of meteen aan het begin: de companion van openHop
  // zet de naam er niet altijd voor.
  const char* p = strncmp(text, "T1A|", 4) == 0 ? text : strstr(text, ": T1A|");
  if (!p) return false;
  p += p == text ? 4 : 6;
  // Zelfde kanaal = zelfde geheim (hetzelfde kanaal kan op twee nummers staan na een terugzetting).
  ChannelDetails a, b;
  if (!the_mesh.mtGetChannel(chan_idx, a) || !the_mesh.mtGetChannel(mt_cfg.chan_idx, b) ||
      memcmp(a.channel.secret, b.channel.secret, 16) != 0) {
    mt_log("T1A op kanaal %u genegeerd (trackingkanaal is %u)", (unsigned)chan_idx, (unsigned)mt_cfg.chan_idx);
    return true;
  }
  char pk[9], body[24], tag[9];
  own_pk8(pk);
  if (strncmp(p, pk, 8) != 0 || p[8] != '|' || strlen(p) < 19 || p[17] != '|') {
    mt_log("T1A voor een andere tracker (%.8s)", p);
    return true;
  }
  char* end;
  unsigned long seq = strtoul(p + 18, &end, 10);
  if (end == p + 18 || *end || seq > 0xFFFF) { mt_log("T1A met ongeldig volgnummer genegeerd"); return true; }
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
      lastfix_save();                          // in rust: laatst gekende fix bewaren
      s_sleep_since = millis();
      s_still_since = 0;
      gps_want(s_manual || s_req_gps);
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
      gps_want(s_manual || s_req_gps);
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

  // FastTrack uit wegens de batterij: slapen zonder S-bericht; alleen een heartbeat-fix loopt nog.
  if (s_fast_off && s_state != MT_T_SLEEP && !(s_state == MT_T_ACQUIRE && s_purpose == PUR_HEARTBEAT)) {
    enter(MT_T_SLEEP);
    return;
  }

  switch (s_state) {
    case MT_T_OFF:
      enter(MT_T_MOVING);
      break;

    case MT_T_SLEEP: {
      bool motion = mt_motion_available() ? (int32_t)(mt_motion_last() - s_sleep_since) > 0 : false;
      if (motion && !s_fast_off) { s_purpose = PUR_WAKE; enter(MT_T_ACQUIRE); break; }
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
          if (moving_now(g.speedKmh()) && !s_fast_off) enter(MT_T_MOVING); else enter(MT_T_SLEEP);
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
  if (!s_manual && !s_req_gps && (s_state == MT_T_SLEEP || s_state == MT_T_OFF)) gps_want(false);
}

// rx_beweging (0.9.5): in beweging blijft de radio luisteren (locatieverzoeken, dekking). In rust geldt de
// gewone slaap. Staat FastTrack uit (fast_min_batt), dan telt alleen at_rest().
static bool moving_listen() {
  if (mt_cfg.rx_beweging_uit || at_rest()) return false;
  return s_fast_off || s_state == MT_T_MOVING || s_state == MT_T_ACQUIRE;
}

static void step_radio() {
  bool want_sleep = mt_effective_mode() == MT_MODE_TRACKER && !mt_sender_busy() && !s_manual && !s_sos_left &&
                    (int32_t)(millis() - s_listen_until) >= 0 && !the_mesh.hasPendingWork() && !fifo_keep_awake() &&
                    !moving_listen();
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
  lastfix_load();
  {                                           // wachttijd voor verzoeken: per toestel anders (pubkey), niet bij elke tracker gelijk
    const uint8_t* k = the_mesh.self_id.pub_key;
    randomSeed(((uint32_t)k[0] << 24 | (uint32_t)k[1] << 16 | (uint32_t)k[2] << 8 | k[3]) ^ micros());
  }
  fifo_load();
  if (s_nfifo) mt_log("fifo: %u punten uit /mt_fifo.dat geladen", (unsigned)s_nfifo);
  mt_motion_begin(mt_cfg.accel_sens);
  s_state = MT_T_OFF;
}

void mt_tracker_loop() {
  mt_motion_loop();
  mt_sender_loop();
  step_battery();
  if (tracking_active()) step_tracking();
  else if (s_state != MT_T_OFF) enter(MT_T_OFF);
  step_slow();
  step_fifo();
  step_manual();
  step_req();
  lastfix_track();
  {
    static bool rest_before = false;           // net in rust: laatst gekende fix bewaren
    bool rest = at_rest();
    if (rest && !rest_before) lastfix_save();
    rest_before = rest;
  }
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

// ---- dump (0.9.1): interne toestand voor de webpagina /tracker, alleen lezen -------------------
// Formaat: zie MtTracker.h. o(tekst, regel_af) schrijft naar Serial of naar de Bluetooth-buffer.
static void dump_unix(char* o, size_t n, uint32_t t) { if (t) snprintf(o, n, "%lu", (unsigned long)t); else snprintf(o, n, "-"); }

void mt_tracker_dump(MtDumpOut o) {
  char b[160], t[16];
  MtNmeaProvider& g = mt_gps();
  uint32_t now_unix = the_mesh.getRTCClock()->getCurrentTime();
  o("mtdump 1", true);
  snprintf(b, sizeof(b), "now %lu", (unsigned long)now_unix);
  o(b, true);
  if (g.lastValidMs()) {
    snprintf(b, sizeof(b), "pos %lu %ld %ld %d %ld %.1f", (unsigned long)g.lastValidUnix(), lround(g.getLatitude() / 10.0),
             lround(g.getLongitude() / 10.0), (int)(g.speedKmh() + 0.5f), g.satellitesCount(), g.hdop());
    o(b, true);
  } else {
    o("pos -", true);
  }
  if (s_lf.ts) snprintf(b, sizeof(b), "lastfix %lu %ld %ld", (unsigned long)s_lf.ts, (long)s_lf.lat, (long)s_lf.lon);
  else snprintf(b, sizeof(b), "lastfix -");
  o(b, true);
  const char* sw = s_state == MT_T_SLEEP ? "slaapt" : s_state == MT_T_ACQUIRE ? "zoekt" : s_state == MT_T_MOVING ? "beweegt" : "uit";
  snprintf(b, sizeof(b), "mode %s %s %s bat=%d usb=%d", mt_mode_name(mt_effective_mode()), fifo_mode() ? "fifo" : "classic", sw,
           mt_battery_pct(board.getBattMilliVolts()), mt_usb() ? 1 : 0);
  o(b, true);
  if (s_ltx_state) { dump_unix(t, sizeof(t), s_ltx_unix); snprintf(b, sizeof(b), "last_tx %s %c %d", t, s_ltx_state, s_ltx_ok ? 1 : 0); }
  else snprintf(b, sizeof(b), "last_tx -");
  o(b, true);
  if (s_lh_unix) snprintf(b, sizeof(b), "last_heard %lu %.1f %s", (unsigned long)s_lh_unix, s_lh_snr, s_lh_rep);
  else snprintf(b, sizeof(b), "last_heard -");
  o(b, true);
  if (s_cov_have) {
    char sn[12] = "-";
    if (s_cov_snr < MT_SNR_NONE - 1) snprintf(sn, sizeof(sn), "%.1f", s_cov_snr);
    dump_unix(t, sizeof(t), s_cov_unix);
    snprintf(b, sizeof(b), "cov %s %s %d", t, sn, s_cov_last_stable ? 1 : 0);
  } else snprintf(b, sizeof(b), "cov -");
  o(b, true);
  snprintf(b, sizeof(b), "cnt fast=%lu slow=%lu q=%lu heard=%lu missed=%lu t1f=%lu hour=%d/%u tries=%d/%u",
           (unsigned long)s_cnt_fast, (unsigned long)s_cnt_slow, (unsigned long)s_cnt_q, (unsigned long)s_cnt_heard,
           (unsigned long)s_cnt_missed, (unsigned long)s_fifo_confirmed, fl_sent_last_hour(), (unsigned)mt_cfg.fifo_per_uur,
           fl_tries_last_hour(), 2U * mt_cfg.fifo_per_uur);
  o(b, true);
  // leegmaken: actief (of gepauzeerd door een uurplafond, cap=uur|pogingen), gestopt (wacht op dekking) of gepauzeerd (wachttijden)
  const char* fs = "idle";
  const char* capw = "-";
  strcpy(t, "-");
  if (fifo_mode() && (s_fl_active || s_fl_inflight)) {
    uint32_t wait = 0;
    int cap = fl_cap(&wait);
    fs = cap ? "gepauzeerd" : "actief";
    capw = cap == 1 ? "uur" : cap == 2 ? "pogingen" : "-";
    uint32_t el = (millis() - s_fl_sent_ms) / 1000;
    if (s_fl_ever && el < mt_cfg.fifo_gap_s && mt_cfg.fifo_gap_s - el > wait) wait = mt_cfg.fifo_gap_s - el;
    dump_unix(t, sizeof(t), now_unix + wait);
  } else if (fifo_mode() && s_fl_pending && s_nfifo) {
    uint16_t sel[MT_SLOW_MAX_ITEMS + 1];
    bool fin;
    fs = fifo_pick(sel, MT_SLOW_MAX_ITEMS + 1, &fin) ? "gestopt" : "gepauzeerd";
  }
  {
    bool full = false;
    if (s_nfifo) {
      uint16_t sel[MT_SLOW_MAX_ITEMS + 1];
      bool fin;
      full = fifo_pick(sel, MT_SLOW_MAX_ITEMS + 1, &fin) >= (int)mt_fifo_per_msg();
    }
    char dn[16] = "-";
    long pw = partial_wait();
    if (pw >= 0) dump_unix(dn, sizeof(dn), now_unix + (uint32_t)pw);
    snprintf(b, sizeof(b), "flush %s next=%s cap=%s vol=%d deel_na=%s deel_snr=%d", fs, t, capw, full ? 1 : 0, dn,
             partial_snr_ok() ? 1 : 0);
  }
  o(b, true);
  // punten, hooguit 20 per regel
  for (int i = 0; i < s_npts; i++) {
    if (i % 20 == 0) { if (i) o("", true); o("pts F ", false); }
    const MtPt& q = s_pts[i];
    snprintf(b, sizeof(b), "%s%lu,%ld,%ld,%s", i % 20 ? ";" : "", (unsigned long)q.ts, (long)q.lat, (long)q.lon,
             (q.fl & PT_MAIN) ? (fifo_mode() ? "mp" : "m") : "-");
    o(b, false);
  }
  {
    char lu[16];
    dump_unix(lu, sizeof(lu), s_req_last_unix);
    snprintf(b, sizeof(b), "req last=%s answered=%lu ignored=%lu", lu, (unsigned long)s_req_answered, (unsigned long)s_req_ignored);
    o(b, true);
  }
  if (s_npts) o("", true);
  for (int i = 0; i < s_nslow; i++) {
    if (i % 20 == 0) { if (i) o("", true); o("pts S ", false); }
    const MtSPt& q = s_slow[i];
    snprintf(b, sizeof(b), "%s%lu,%ld,%ld,-", i % 20 ? ";" : "", (unsigned long)q.ts, (long)q.lat, (long)q.lon);
    o(b, false);
  }
  if (s_nslow) o("", true);
  for (int i = 0; i < s_nfifo; i++) {
    if (i % 20 == 0) { if (i) o("", true); o("pts Q ", false); }
    const MtFPt& q = s_fifo[i];
    char f[8];
    int k = 0;
    if (q.flags & FPT_SENT) f[k++] = 's';
    if (fifo_is_parked(q)) f[k++] = 'k';
    if (q.tries) k += snprintf(f + k, sizeof(f) - k, "%u", (unsigned)q.tries);
    if (!k) f[k++] = '-';
    f[k] = 0;
    snprintf(b, sizeof(b), "%s%lu,%ld,%ld,%s", i % 20 ? ";" : "", (unsigned long)q.ts, (long)q.lat, (long)q.lon, f);
    o(b, false);
    if ((i & 63) == 63) yield();
  }
  if (s_nfifo) o("", true);
  o("end", true);
}

uint16_t mt_tracker_fifo_count() { return s_nfifo; }
uint32_t mt_tracker_fifo_oldest() { return s_nfifo ? s_fifo[0].ts : 0; }
uint32_t mt_tracker_fifo_newest() { return s_nfifo ? s_fifo[s_nfifo - 1].ts : 0; }
long mt_tracker_fifo_cov_age() { return s_cov_have ? (long)((millis() - s_cov_ms) / 1000) : -1; }
int mt_tracker_fifo_hour(uint32_t* wait_s) {
  fl_cap(wait_s);
  return fl_sent_last_hour();
}
int mt_tracker_fifo_tries() { return fl_tries_last_hour(); }
long mt_tracker_fifo_last_ok_age() { return last_ok_age(); }
long mt_tracker_fifo_partial_wait() { return partial_wait(); }
long mt_tracker_fifo_last_partial_age() { return last_partial_age(); }
int mt_tracker_fifo_cap() { return fl_cap(); }
uint16_t mt_tracker_fifo_parked() { return fifo_parked(); }
uint32_t mt_tracker_lastfix_ts() { return s_lf.ts; }
uint32_t mt_tracker_fifo_confirmed() { return s_fifo_confirmed; }
const char* mt_tracker_fifo_state() {
  if (!fifo_mode()) return "uit (classic)";
  if (s_fl_inflight) return "bericht onderweg";
  if (s_fl_active) {
    int cap = fl_cap();
    return cap == 1 ? "gepauzeerd (fifo_per_uur bereikt)" : cap == 2 ? "gepauzeerd (2x fifo_per_uur pogingen bereikt)" : "bezig";
  }
  if (s_fl_pending && s_nfifo) return "gestopt of gepauzeerd, wacht op dekking of een nieuwe poging";
  return "wacht";
}
bool mt_tracker_fifo_clear() {
  s_nfifo = 0;
  s_fifo_unsaved = 0;
  s_fl_active = s_fl_pending = false;
  s_fl_inflight = false;                      // een bericht onderweg verandert niets meer
  for (int i = 0; i < MT_TXW; i++) s_txw[i].fl &= ~TXW_FLUSH;
  mt_file_remove(FIFO_TMP);
  return fifo_save();
}
