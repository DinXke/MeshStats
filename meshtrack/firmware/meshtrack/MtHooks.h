#pragma once
// Haken die MeshTrack in de (gekopieerde) stock MyMesh.cpp nodig heeft.
#include <stdint.h>

// Batterij voor de app: virtuele mV zodat de lineaire formule van de app het
// juiste % (volgens de echte LiPo-curve) toont.
uint16_t mt_battery_app_mv(uint16_t real_mv);
// Elk ontvangen ruw pakket (logRxRaw): herhalingen van ons eigen kanaalbericht herkennen.
void mt_rx_raw(const uint8_t raw[], int len);
// Ontvangen kanaalbericht "<naam>: <tekst>". true = T1A (SOS-bevestiging van de server):
// geen berichtbiep.
bool mt_channel_text(uint8_t chan_idx, const char* text);
// Elk pakket dat de radio echt verzonden heeft (logTx): tx_beep, heard_beep.
void mt_tx_packet(uint8_t payload_type, const uint8_t payload[], int len);
