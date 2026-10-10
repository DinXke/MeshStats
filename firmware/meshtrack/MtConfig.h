#pragma once
// Instellingen van MeshTrack in /mt_cfg.dat (InternalFS).
//
// Opslaan is zo goed als atomair: eerst /mt_cfg.tmp volledig schrijven, dan
// pas het oude bestand vervangen. Ontbreekt het hoofdbestand bij het opstarten
// maar staat er een geldige .tmp, dan wordt die gebruikt. Een bestand van een
// NIEUWERE firmware (hogere versie) wordt nooit overschreven.

#include <stdint.h>
#include <stddef.h>

#define MT_CFG_VERSION 9   // v9: beheercode voor Bluetooth; v5: SlowTrack, fast_min_batt, sos, tx_beep, heard_beep; v6: trackmodus en FIFO; v7: verzoeken, rx_beweging; v8: GPS-pinnen, rust_gps_check, analoge knop (velden achteraan, oudere worden overgenomen)
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
  // ---- v5 (0.8.0); oudere bestanden krijgen hier de standaardwaarden ----
  uint32_t slow_log_s;          // SlowTrack: elke x s een punt loggen, ook in rust (0 = uit)
  uint32_t slow_send_s;         // SlowTrack: gelogde punten elke x s versturen (L-berichten)
  uint8_t  fast_min_batt;       // onder x % batterij geen FastTrack (0 = altijd FastTrack)
  uint8_t  sos_off;             // 1 = SOS met de knop uitgeschakeld (0 = aan, standaard)
  uint8_t  tx_beep;             // 1 = korte biep na elk verstuurd positiebericht
  uint8_t  heard_beep;          // 1 = twee hoge biepjes als een repeater een positiebericht herhaalt
  // ---- v6 (0.9.0); v5-bestanden krijgen hier de standaardwaarden ----
  uint8_t  track_mode;          // 0 = classic (FastTrack + SlowTrack), 1 = fifo (wachtrij voor gemiste posities)
  uint8_t  fifo_per_uur;        // fifo: hooguit x leegmaakberichten per uur (1..60)
  uint16_t fifo_max;            // fifo: wachtrij hooguit x punten (20..500); vol = het oudste valt weg
  uint16_t fifo_min;            // fifo: leegmaken vanaf x punten (1..fifo_max)
  uint16_t fifo_gap_s;          // fifo: tussen twee leegmaakberichten minstens x s (15..300)
  uint8_t  fifo_pogingen;       // fifo: na x niet herhaalde leegmaakberichten is een punt geparkeerd (1..10)
  uint8_t  fifo_dun;            // fifo: punt op een rechte lijn binnen x m = overbodig (0 = uit, 0..100)
  int8_t   fifo_snr;            // fifo: nieuwe leegmaakronde pas bij SNR >= x dB (-20..10), of 2x dekking / T1F
  uint8_t  fifo_wacht;          // fifo (0.9.1): punten ouder dan x min bij stabiele dekking toch versturen, ook onder
                                // fifo_min. 0 = standaard (30 min; was opvulling in 0.9.0), 255 = uit
  // ---- v7 (0.9.5); oudere bestanden krijgen hier 0 = standaard (aan) ----
  uint8_t  verzoek_uit;         // 1 = locatieverzoeken (T1R) niet beantwoorden; 0 = wel (standaard)
  uint8_t  rx_beweging_uit;     // 1 = radio ook in beweging laten slapen; 0 = in beweging blijven luisteren (standaard)
  uint8_t  verzoek_beep;        // 1 = deuntje bij een aanvaard locatieverzoek; 0 = uit (standaard)
  uint8_t  fifo_punten_hoofd;   // fifo: 0 = alle punten van gemiste berichten naar de FIFO (standaard), 1 = alleen hoofdpunten
  // ---- v8 (0.9.7); oudere bestanden krijgen hier 0 = standaard ----
  uint8_t  gps_rx;              // RAK3401: GPS-UART, RX-pin van de nRF (0 = nog niet gevonden: zoeken)
  uint8_t  gps_tx;              // RAK3401: TX-pin van de nRF
  uint8_t  gps_baud;            // RAK3401: 0 = onbekend, anders MT_GPS_BAUDS[code-1]
  uint8_t  rust_gps_check;      // zonder bewegingssensor: in rust elke x min kort de GPS (0 = standaard 5, 255 = uit: GPS blijft aan)
  uint8_t  pin31;               // RAK3401, P0.31: 0 = uit (pin nooit lezen, standaard), 1 = analoge knop, 2 = prioriteitsingang
  uint8_t  prio_niveau;         // prio: 0 = actief laag (optocoupler naar massa, standaard), 1 = actief hoog
  uint8_t  prio_houd;           // prio: na de laatste actieve periode nog x min prioritair (0 = standaard 5, 1..60, 255 = volgt de ingang)
  uint8_t  prio_interval;       // prio: FastTrack hooguit elke x s (0 = standaard 30, 255 = geen wijziging)
  // ---- v9 (0.9.11); oudere bestanden krijgen hier 0 = geen beheercode ----
  uint8_t  ble_admin_set;       // 1 = beheercode ingesteld (instellingen via Bluetooth na ontgrendelen)
  uint8_t  _pad9[3];
  uint8_t  ble_admin_hash[16];  // SHA256(beheercode)[0:16]; de code zelf wordt nergens bewaard
  uint32_t crc;                 // crc32 over alles hiervoor
};

// Vaste indeling: een andere grootte breekt de bewaarde bestanden (zie hierboven).
static_assert(sizeof(MtCfg) == 168, "MtCfg-indeling gewijzigd");
static_assert(offsetof(MtCfg, ble_admin_set) == 144, "v9-velden moeten na de v8-velden komen");
static_assert(offsetof(MtCfg, gps_rx) == 136, "v8-velden moeten na de v7-velden komen");
static_assert(offsetof(MtCfg, verzoek_uit) == 132, "v7-velden moeten na de v6-velden komen");
static_assert(offsetof(MtCfg, track_mode) == 120, "v6-velden moeten na de v5-velden komen");

#define MT_TRACK_CLASSIC 0
#define MT_TRACK_FIFO    1

extern MtCfg mt_cfg;

// fifo_wacht in minuten: 0 = standaard (30), 255 = uit (geeft 0 terug).
inline uint32_t mt_fifo_wacht_min() { return mt_cfg.fifo_wacht == 0 ? 30 : mt_cfg.fifo_wacht == 255 ? 0 : mt_cfg.fifo_wacht; }
// rust_gps_check in minuten: 0 = standaard (5), 255 = uit (geeft 0 terug: de GPS blijft aan, geen rust).
inline uint32_t mt_rust_gps_check_min() { return mt_cfg.rust_gps_check == 0 ? 5 : mt_cfg.rust_gps_check == 255 ? 0 : mt_cfg.rust_gps_check; }
// GPS-baudcodes (gps_baud): 1..5
static const uint32_t MT_GPS_BAUDS[] = { 9600, 38400, 115200, 4800, 57600 };
#define MT_GPS_NBAUDS 5

// Uitkomst van het laden, voor `status` en de bootmelding.
extern const char* mt_cfg_load_note;
extern bool mt_cfg_readonly;    // bestand van nieuwere fw: niet overschrijven

void mt_cfg_defaults(MtCfg& c);
void mt_cfg_begin();
bool mt_cfg_save();
// Kleine bestanden van MeshTrack (op ExtraFS; lezen valt terug op InternalFS van oudere firmware).
bool mt_file_read(const char* path, void* buf, size_t len);
bool mt_file_write(const char* path, const void* data, size_t len);
// Groter bestand (bv. de FIFO) in twee delen (kop + gegevens): eerst volledig naar tmp, dan pas
// hernoemen naar path. Lezen vanaf een positie, alleen op de eigen plaats (ExtraFS).
bool mt_file_write_atomic(const char* path, const char* tmp, const void* a, size_t alen, const void* b, size_t blen);
bool mt_file_read_at(const char* path, size_t off, void* buf, size_t len);
void mt_file_remove(const char* path);
void mt_fs_status(char* out, size_t n);
bool mt_fs_internal_ok();                 // InternalFS leesbaar en niet beschadigd
bool mt_fs_internal_format();             // InternalFS formatteren (wist identiteit en voorkeuren!)   // "opslag_intern=x/7 opslag_extra=y/z" (blokken)
uint32_t mt_crc32(uint32_t crc, const uint8_t* p, uint32_t n);
