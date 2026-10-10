#pragma once

#include <MeshCore.h>
#include <helpers/ui/DisplayDriver.h>
#include <helpers/SensorManager.h>
#include <stddef.h>

#ifdef PIN_BUZZER
  #include <helpers/ui/buzzer.h>
#endif

#include "AbstractUITask.h"
#include "NodePrefs.h"

#include "Button.h"

#ifdef HAS_DRV2605
  #include <helpers/ui/DRV2605Vibration.h>
#endif

class UITask : public AbstractUITask {
  DisplayDriver* _display;
  SensorManager* _sensors;
#ifdef PIN_BUZZER
  genericBuzzer buzzer;
#endif
#ifdef HAS_DRV2605
  DRV2605Vibration vibration;
#endif
  unsigned long _next_refresh, _auto_off;
  NodePrefs* _node_prefs;
  char _version_info[32];
  char _origin[62];
  char _msg[80];
  char _alert[80];
  int _msgcount;
  bool _need_refresh = true;
  bool _displayWasOn = false;  // Track display state before button press
  unsigned long ui_started_at;
  // MeshTrack: modusbiep klinkt ook als de buzzer stil staat
  bool _mt_restore_quiet = false;
  bool _mt_quiet_before = false;

  // Button handlers
#ifdef PIN_USER_BTN
  Button* _userButton = nullptr;
#endif
#ifdef PIN_USER_BTN_ANA
  Button* _userButtonAnalog = nullptr;
  // MeshTrack 0.9.7 (RAK3401): optionele analoge knop (instelling pin31 knop); zwevende pin herkennen
  uint32_t _anaPress[10] = {};
  uint8_t _anaPressI = 0;
  void mtAnalogSetup();
  void mtAnalogPress();
#endif
#if !defined(PIN_BUZZER) && defined(LED_GREEN) && defined(LED_BLUE)
  // MeshTrack 0.9.7: geen buzzer: terugmelding (klik, SOS, modus ...) met de leds
  #define MT_LED_FEEDBACK 1
  uint8_t _ledPin = 0, _ledLeft = 0;
  bool _ledOn = false;
  uint16_t _ledOnMs = 0, _ledOffMs = 0;
  uint32_t _ledNext = 0;
  void mtLedStart(const char* rtttl);
  void mtLedLoop();
#endif

  void renderCurrScreen();
  void userLedHandler();
  void renderBatteryIndicator(uint16_t batteryMilliVolts);
  
  // Button action handlers
  void handleButtonAnyPress();
  void handleButtonShortPress();
  void handleButtonDoublePress();
  void handleButtonTriplePress();
  void handleButtonQuadruplePress();
  void handleButtonLongPress();

 
public:

  UITask(mesh::MainBoard* board, MultiSerialInterface* serial) : AbstractUITask(board, serial), _display(NULL), _sensors(NULL) {
      _next_refresh = 0;
      ui_started_at = 0;
  }
  void begin(DisplayDriver* display, SensorManager* sensors, NodePrefs* node_prefs);

  bool hasDisplay() const { return _display != NULL; }
  void clearMsgPreview();

  // from AbstractUITask
  void msgRead(int msgcount) override;
  void newMsg(uint8_t path_len, const char* from_name, const char* text, int msgcount) override;
  void notify(UIEventType t = UIEventType::none) override;
  void loop() override;

  void shutdown(bool restart = false);
  void playModeTune(bool tracker);   // MeshTrack
  void playForced(const char* rtttl); // MeshTrack: ook als de buzzer stil staat
};
