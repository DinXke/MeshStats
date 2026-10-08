#pragma once
// Haken die MeshTrack in de (gekopieerde) stock MyMesh.cpp nodig heeft.
#include <stdint.h>

// Batterij voor de app: virtuele mV zodat de lineaire formule van de app het
// juiste % (volgens de echte LiPo-curve) toont.
uint16_t mt_battery_app_mv(uint16_t real_mv);
// Elk ontvangen ruw pakket (logRxRaw): herhalingen van ons eigen kanaalbericht herkennen, dekking (fifo).
void mt_rx_raw(float snr, const uint8_t raw[], int len);
// Ontvangen kanaalbericht "<naam>: <tekst>". true = T1A (SOS-bevestiging van de server):
// geen berichtbiep.
bool mt_channel_text(uint8_t chan_idx, const char* text);
// Elk pakket dat de radio echt verzonden heeft (logTx): tx_beep, heard_beep.
void mt_tx_packet(uint8_t payload_type, const uint8_t payload[], int len);
// Bluetooth (0.9.1): alleen-lezen CLI. Verzoek [MT_BLE_CMD]['M']['T']<commando> (status, fifo, dump);
// antwoord [MT_BLE_CMD][volgnummer 0..][utf8-tekst] ..., afgesloten met [MT_BLE_CMD][0xFF].
#define MT_BLE_CMD 0x7E          // vrij in v1.17.1: CMD_* tot 65, RESP_CODE_* tot 28, PUSH_CODE_* vanaf 0x80
void mt_ble_cli(const char* cmd, int n);
bool mt_ble_pending();
int mt_ble_next_frame(uint8_t* buf, int max);   // 0 = niets; anders de lengte van het frame in buf
void mt_ble_commit();                            // het frame van mt_ble_next_frame is verstuurd
void mt_ble_abort();                             // verbinding weg: sessie stoppen
