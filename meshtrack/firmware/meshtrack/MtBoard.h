#pragma once
// Welk bord? MeshTrack bouwt voor de Seeed T1000-E en (vanaf 0.9.7) de RAK WisMesh Tag. Alles wat
// per bord verschilt, zit achter T1000_E / RAK_WISMESH_TAG (gezet door de variant in platformio.ini):
//   - target (radio, GPS-sensormanager):  MtTarget.cpp (T1000-E) / MtTargetTag.cpp (Tag)
//   - GPS aan/uit:                         MtGps.h (begin/stop: Tag via PIN_GPS_EN)
//   - versnellingsmeter:                   QMA6100P (T1000-E, interrupt) / LIS2DH (Tag, pollen)
//   - knop, buzzer, leds, batterij:        pinnen en board-klasse uit de variant; Button.cpp is gelijk
// Het bestandssysteem (ExtraFS 0xD4000..0xED000, InternalFS 0xED000..0xF4000) ligt op beide borden
// gelijk; alleen het begin van de app verschilt (S140 v7: 0x27000, S140 v6: 0x26000).

// MT_HAS_BUZZER / MT_HAS_BUTTON: voor status (buzzer=, knop=) en de webpagina. MT_HAS_BUTTON 2 = optionele
// analoge knop (instelling knop). MT_TX_GAIN_DB: versterking na de SX1262 (0 = geen versterker).
#if defined(RAK_3401)
  // RAK19007 + RAK3401 + RAK13302 (SKY66122, 1 W): voertuigtracker. Geen buzzer, geen bewegingssensor,
  // GPS via draadjes (pinnen en baud automatisch gezocht, MtGpsScan.cpp), RTC RAK12002 (RV3028) via
  // MeshCore. 3V3_S (WB_IO2, P0.34) voedt ook de versterker: nooit uitschakelen.
  #define MT_BOARD_ID   "rak3401_1w"
  #define MT_BOARD_NAME "RAK3401 1W"
  #define MT_HAS_BUZZER 0
  #define MT_HAS_BUTTON 2
  #ifndef MT_TX_GAIN_DB
    #define MT_TX_GAIN_DB 9           // RAK13302: hooguit +9 dB (Meshtastic TX_GAIN_LORA)
  #endif
#elif defined(RAK_WISMESH_TAG)
  #define MT_BOARD_ID   "wismesh_tag"
  #define MT_BOARD_NAME "WisMesh Tag"
  #define MT_HAS_BUZZER 1
  #define MT_HAS_BUTTON 1
#elif defined(T1000_E)
  #define MT_BOARD_ID   "t1000e"
  #define MT_BOARD_NAME "T1000-E"
  #define MT_HAS_BUZZER 1
  #define MT_HAS_BUTTON 1
#else
  #error "MeshTrack: onbekend bord (verwacht T1000_E, RAK_WISMESH_TAG of RAK_3401)"
#endif
#ifndef MT_TX_GAIN_DB
  #define MT_TX_GAIN_DB 0
#endif
