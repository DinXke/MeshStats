#pragma once
// MeshTrack: tracker-laag bovenop de stock MeshCore companion_radio (v1.17.1).
//
// Twee modi. Volledige companion = stock gedrag (BLE, MeshCore-app). Tracker =
// BLE uit, later ook radio-slaap en bewegingsgestuurd zenden. USB-voeding geeft
// ALTIJD companion; zonder USB geldt de laatst gekozen modus (dubbelklik).
//
// Deze laag raakt de identiteit, prefs, contacten en kanalen van de stock
// DataStore NIET aan: alleen een eigen bestand /mt_cfg.dat op InternalFS.

#include <Arduino.h>
#include <stdint.h>

#define MT_FW_VERSION "0.1.1"

enum MtMode : uint8_t { MT_MODE_COMPANION = 0, MT_MODE_TRACKER = 1 };

void mt_begin();                 // na the_mesh.begin() en de interfaces
void mt_loop();                  // elke loop()

// knop (vanuit UITask)
void mt_on_short_press();        // huidige modus laten horen
void mt_on_double_press();       // modus wisselen
void mt_on_cli_rescue();         // stock rescue-CLI neemt Serial over

MtMode mt_effective_mode();      // wat nu actief is (USB => companion)
