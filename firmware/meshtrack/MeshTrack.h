#pragma once
// MeshTrack: tracker-laag bovenop de stock MeshCore companion_radio (v1.17.1).
//
// Twee modi. Volledige companion = stock gedrag (BLE, MeshCore-app). Tracker =
// BLE uit, radio slaapt tussen berichten, zenden op beweging. USB-voeding geeft
// ALTIJD companion; zonder USB geldt de laatst gekozen modus (dubbelklik).
//
// Deze laag raakt de identiteit, prefs, contacten en kanalen van de stock
// DataStore NIET aan: eigen bestanden /mt_cfg.dat en /mt_seq.dat op InternalFS.

#include <Arduino.h>
#include <stdint.h>

#define MT_FW_VERSION "0.8.1"

// Terugmelding na een klik/SOS: twee hoge biepjes = een repeater herhaalde het bericht
// (gehoord binnen MT_HEAR_MS), lage toon = geen herhaling gehoord of niet verstuurd
// (trackingkanaal ontbreekt). SOSOK = de server bevestigde de SOS (T1A).
#define MT_TUNE_OK    "ok:d=16,o=7,b=200:16c,16p,16c"
#define MT_TUNE_NOK   "nok:d=4,o=5,b=100:4c"
#define MT_TUNE_SOSOK "sosok:d=16,o=6,b=180:16c,16e,16g,8c7"
#define MT_HEAR_MS    12000
// tx_beep: korte biep zodra de radio een positiebericht echt verzonden heeft
#define MT_TUNE_TX    "tx:d=32,o=7,b=200:32e"

enum MtMode : uint8_t { MT_MODE_COMPANION = 0, MT_MODE_TRACKER = 1 };

void mt_begin();                 // na the_mesh.begin() en de interfaces
void mt_loop();                  // elke loop()

// knop (vanuit UITask)
void mt_on_short_press();        // positie nu versturen
void mt_on_double_press();       // modus wisselen
void mt_on_cli_rescue();         // stock rescue-CLI neemt Serial over
void mt_on_sos();                // knop 2..8 s vastgehouden en losgelaten
bool mt_led_allowed();           // mag de statusled knipperen?

MtMode mt_effective_mode();      // wat nu actief is (USB => companion)
bool mt_usb();
const char* mt_mode_name(uint8_t m);
void mt_choose_mode(MtMode m, bool beep);   // gekozen modus (bij batterij) zetten en bewaren
bool mt_mesh_paused();           // main.cpp: mesh-lus overslaan

// serieel (MtMenu.cpp)
void mt_menu_begin();
void mt_menu_loop();
void mt_menu_suspend();
void mt_log(const char* fmt, ...);
