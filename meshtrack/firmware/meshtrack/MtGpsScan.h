#pragma once
// GPS-pinnen en -baud zoeken (MeshTrack 0.9.7, alleen RAK3401: de GPS hangt er met draadjes aan).
// Op andere borden doen deze functies niets (de GPS zit vast op het bord).
#include <Arduino.h>

void mt_gps_scan_begin();          // bij het opstarten (na sensors.begin): bewaarde pinnen zetten of zoeken
void mt_gps_scan_loop();           // elke lus; zoekt niet-blokkerend, en opnieuw als de GPS 10 min zweeg
bool mt_gps_scan_start();          // CLI "gps zoek"; false = niet op dit bord
bool mt_gps_scan_busy();
bool mt_gps_set_pins(uint8_t rx, uint8_t tx, uint32_t baud);   // CLI "set gps_pinnen"; meteen toepassen
void mt_gps_pins_str(char* out, size_t n);                      // "15/16@9600", "-" of "zoekt"
