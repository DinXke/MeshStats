#pragma once
#include <Arduino.h>

enum MtTState : uint8_t { MT_T_OFF, MT_T_SLEEP, MT_T_ACQUIRE, MT_T_MOVING };

void mt_tracker_begin();
void mt_tracker_loop();
bool mt_tracker_manual();          // enkele klik: positie nu; false = genegeerd (te snel / geen trackingkanaal)
bool mt_tracker_sos();             // SOS: meteen, en daarna nog 2x met verse positie
void mt_tracker_mode_changed();    // stuurt een B-bericht
void mt_tracker_power_changed();   // USB in/uit: B-bericht (max. 1x per 30 s)
bool mt_radio_paused();            // true = mesh-lus overslaan (radio slaapt)

MtTState mt_tracker_state();
const char* mt_tracker_state_str();
const char* mt_tracker_last_reason();
uint8_t mt_tracker_buffered();         // bewaarde punten (mee in het volgende bericht)
uint8_t mt_tracker_slow_buffered();    // SlowTrack: gelogde punten die nog verstuurd moeten worden
bool mt_tracker_fast_suspended();      // FastTrack uit wegens de batterij (fast_min_batt)
uint32_t mt_tracker_last_tx_ms();
uint16_t mt_tracker_seq();
void mt_set_rxlog(bool on);           // ontvangstlog aan/uit (niet bewaard)
int mt_tracker_heard();                // herhalingen gehoord van het laatst bewaakte bericht, -1 = geen
const char* mt_tracker_sos_confirmed();   // laatste SOS-reeks: "ja", "nee" of "-"
bool mt_tracker_gps_on();
// FIFO (track_mode fifo, 0.9)
uint16_t mt_tracker_fifo_count();      // punten in de wachtrij
uint32_t mt_tracker_fifo_oldest();     // fix_ts van het oudste punt (0 = leeg)
uint32_t mt_tracker_fifo_newest();
long mt_tracker_fifo_cov_age();        // s sinds de laatste dekking, -1 = nog nooit
int mt_tracker_fifo_hour(uint32_t* wait_s);   // leegmaakberichten in het laatste uur; wait_s = pauze tot er weer een mag
const char* mt_tracker_fifo_state();   // leegmaken: bezig, gestopt, ...
bool mt_tracker_fifo_clear();
uint16_t mt_tracker_fifo_parked();
uint32_t mt_tracker_fifo_confirmed();
// dump (0.9.1, CLI en Bluetooth, alleen lezen): "mtdump 1", now, pos, mode, last_tx, last_heard, cov,
// cnt, flush, pts F/S/Q (hooguit 20 punten per regel), end. o(tekst, true) = regel afsluiten.
typedef void (*MtDumpOut)(const char* text, bool eol);
void mt_tracker_dump(MtDumpOut o);  // sinds de start door een T1F van de server verwijderde punten     // geparkeerde punten (pogingen >= fifo_pogingen)          // wachtrij wissen (en bewaren)
