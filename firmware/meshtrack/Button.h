#pragma once

#include <Arduino.h>
#include <functional>

// Button timing configuration
#define BUTTON_DEBOUNCE_TIME_MS    50      // Debounce time in ms
#define BUTTON_CLICK_TIMEOUT_MS    500     // Max time between clicks for multi-click
// MeshTrack: vasthouden 2..8 s = SOS (bij loslaten), langer dan 8 s = uitschakelen.
#define BUTTON_LONG_PRESS_TIME_MS  8000    // uitschakelen
#define BUTTON_HOLD_ARM_MS         2000    // SOS gewapend (biep)
#define BUTTON_HOLD_WARN_MS        6500    // waarschuwing: nog even en hij schakelt uit
#define BUTTON_READ_INTERVAL_MS    10      // How often to read the button

class Button {
public:
    enum EventType {
        NONE,
        SHORT_PRESS,
        DOUBLE_PRESS,
        TRIPLE_PRESS,
        QUADRUPLE_PRESS,
        LONG_PRESS,
        ANY_PRESS,
        HOLD_ARM,
        HOLD_WARN,
        HOLD_RELEASE
    };

    using EventCallback = std::function<void()>;

    Button(uint8_t pin, bool activeState = LOW);
    Button(uint8_t pin, bool activeState, bool isAnalog, uint16_t analogThreshold = 20);
    
    void begin();
    void update();
    
    // Set callbacks for different events
    void onShortPress(EventCallback callback) { _onShortPress = callback; }
    void onDoublePress(EventCallback callback) { _onDoublePress = callback; }
    void onTriplePress(EventCallback callback) { _onTriplePress = callback; }
    void onQuadruplePress(EventCallback callback) { _onQuadruplePress = callback; }
    void onLongPress(EventCallback callback) { _onLongPress = callback; }
    void onAnyPress(EventCallback callback) { _onAnyPress = callback; }
    void onHoldArm(EventCallback callback) { _onHoldArm = callback; }
    void onHoldWarn(EventCallback callback) { _onHoldWarn = callback; }
    void onHoldRelease(EventCallback callback) { _onHoldRelease = callback; }
    
    // State getters
    bool isPressed() const { return _currentState; }
    EventType getLastEvent() const { return _lastEvent; }

private:
    enum State {
        IDLE,
        PRESSED,
        RELEASED,
        WAITING_FOR_MULTI_CLICK
    };

    uint8_t _pin;
    bool _activeState;
    bool _isAnalog;
    uint16_t _analogThreshold;
    
    State _state = IDLE;
    bool _currentState;
    bool _lastState;
    
    uint32_t _stateChangeTime = 0;
    uint32_t _pressTime = 0;
    uint32_t _releaseTime = 0;
    uint32_t _lastReadTime = 0;
    
    uint8_t _clickCount = 0;
    EventType _lastEvent = NONE;
    
    // Callbacks
    EventCallback _onShortPress = nullptr;
    EventCallback _onDoublePress = nullptr;
    EventCallback _onTriplePress = nullptr;
    EventCallback _onQuadruplePress = nullptr;
    EventCallback _onLongPress = nullptr;
    EventCallback _onAnyPress = nullptr;
    EventCallback _onHoldArm = nullptr;
    EventCallback _onHoldWarn = nullptr;
    EventCallback _onHoldRelease = nullptr;
    bool _armFired = false;
    bool _warnFired = false;
    
    bool readButton();
    void handleStateChange();
    void triggerEvent(EventType event);
};