#include "Button.h"
#include "MeshTrack.h"   // mt_log

Button::Button(uint8_t pin, bool activeState) 
    : _pin(pin), _activeState(activeState), _isAnalog(false), _analogThreshold(20) {
    _currentState = false;  // Initialize as not pressed
    _lastState = _currentState;
}

Button::Button(uint8_t pin, bool activeState, bool isAnalog, uint16_t analogThreshold)
    : _pin(pin), _activeState(activeState), _isAnalog(isAnalog), _analogThreshold(analogThreshold) {
    _currentState = false;  // Initialize as not pressed
    _lastState = _currentState;
}

uint8_t Button::s_isrPin = 0;
bool Button::s_isrActive = HIGH;
volatile uint32_t Button::s_presses = 0;
volatile uint32_t Button::s_lastRelease = 0;
volatile uint32_t Button::s_lastEdge = 0;

void Button::isr() {
    uint32_t now = millis();
    bool pressed = digitalRead(s_isrPin) == s_isrActive;
    if (pressed) {
        // Alleen een echte klik: de knop was even los (dender bij het loslaten telt niet).
        if (now - s_lastRelease >= BUTTON_ISR_RELEASED_MS) s_presses++;
    } else {
        s_lastRelease = now;
    }
    s_lastEdge = now;
}

void Button::begin() {
    _currentState = readButton();
    _lastState = _currentState;
    if (!_isAnalog && !_useIsr) {        // één digitale knop per toestel
        s_isrPin = _pin;
        s_isrActive = _activeState;
        s_lastRelease = millis();
        attachInterrupt(digitalPinToInterrupt(_pin), Button::isr, CHANGE);
        _useIsr = true;
        _seenPresses = s_presses;
    }
}

void Button::update() {
    uint32_t now = millis();
    
    // Read button at specified interval
    if (now - _lastReadTime < BUTTON_READ_INTERVAL_MS) {
        return;
    }
    _lastReadTime = now;
    
    bool newState = readButton();
    
    // Check if state has changed
    if (newState != _lastState) {
        _stateChangeTime = now;
    }
    
    // Debounce check
    if ((now - _stateChangeTime) > BUTTON_DEBOUNCE_TIME_MS) {
        if (newState != _currentState) {
            _currentState = newState;
            handleStateChange();
        }
    }
    
    _lastState = newState;

    // MeshTrack: een klik die de interrupt zag maar de lus niet (in- en uitgedrukt
    // tussen twee metingen). Pas als de knop alweer los is en de dender voorbij is.
    if (_useIsr && !_currentState && !newState && _state != PRESSED) {
        uint32_t presses = s_presses;
        if (presses != _seenPresses && now - s_lastEdge > BUTTON_DEBOUNCE_TIME_MS) {
            uint32_t missed = presses - _seenPresses;
            _seenPresses = presses;
            if (missed > 3) missed = 3;
            while (missed--) {
                triggerEvent(ANY_PRESS);
                _clickCount++;
            }
            _releaseTime = now;
            _state = WAITING_FOR_MULTI_CLICK;
        }
    }

    // Handle multi-click timeout
    if (_state == WAITING_FOR_MULTI_CLICK && (now - _releaseTime) > BUTTON_CLICK_TIMEOUT_MS) {
        // Timeout reached, process the clicks
        mt_log("knop: %ux", (unsigned)_clickCount);
        if (_clickCount == 1) {
            triggerEvent(SHORT_PRESS);
        } else if (_clickCount == 2) {
            triggerEvent(DOUBLE_PRESS);
        } else if (_clickCount == 3) {
            triggerEvent(TRIPLE_PRESS);
        } else if (_clickCount >= 4) {
            triggerEvent(QUADRUPLE_PRESS);
        }

        _clickCount = 0;
        _state = IDLE;
    }
    
    // MeshTrack: tijdens het vasthouden eerst "gewapend", dan de waarschuwing.
    if (_state == PRESSED && !_armFired && (now - _pressTime) > BUTTON_HOLD_ARM_MS) {
        _armFired = true;
        triggerEvent(HOLD_ARM);
    }
    if (_state == PRESSED && !_warnFired && (now - _pressTime) > BUTTON_HOLD_WARN_MS) {
        _warnFired = true;
        triggerEvent(HOLD_WARN);
    }

    // Handle long press while button is held
    if (_state == PRESSED && (now - _pressTime) > BUTTON_LONG_PRESS_TIME_MS) {
        triggerEvent(LONG_PRESS);
        _state = IDLE;  // Prevent multiple press events
        _clickCount = 0;
    }
}

bool Button::readButton() {
    if (_isAnalog) {
        return (analogRead(_pin) < _analogThreshold);
    } else {
        return (digitalRead(_pin) == _activeState);
    }
}

void Button::handleStateChange() {
    uint32_t now = millis();
    
    if (_currentState) {
        // Button pressed
        if (_useIsr) _seenPresses = s_presses;   // deze klik zag de lus zelf
        _pressTime = now;
        _state = PRESSED;
        _armFired = _warnFired = false;
        triggerEvent(ANY_PRESS);
    } else {
        // Button released
        if (_state == PRESSED) {
            uint32_t pressDuration = now - _pressTime;
            
            if (pressDuration >= BUTTON_HOLD_ARM_MS && pressDuration < BUTTON_LONG_PRESS_TIME_MS) {
                // MeshTrack: losgelaten tussen 2 en 8 s = SOS
                _state = IDLE;
                _clickCount = 0;
                triggerEvent(HOLD_RELEASE);
            } else if (pressDuration < BUTTON_LONG_PRESS_TIME_MS) {
                // Short press detected
                _clickCount++;
                _releaseTime = now;
                _state = WAITING_FOR_MULTI_CLICK;
            } else {
                // Long press already handled in update()
                _state = IDLE;
                _clickCount = 0;
            }
        }
    }
}

void Button::triggerEvent(EventType event) {
    _lastEvent = event;
    
    switch (event) {
        case ANY_PRESS:
            if (_onAnyPress) _onAnyPress();
            break;
        case SHORT_PRESS:
            if (_onShortPress) _onShortPress();
            break;
        case DOUBLE_PRESS:
            if (_onDoublePress) _onDoublePress();
            break;
        case TRIPLE_PRESS:
            if (_onTriplePress) _onTriplePress();
            break;
        case QUADRUPLE_PRESS:
            if (_onQuadruplePress) _onQuadruplePress();
            break;
        case LONG_PRESS:
            if (_onLongPress) _onLongPress();
            break;
        case HOLD_ARM:
            if (_onHoldArm) _onHoldArm();
            break;
        case HOLD_WARN:
            if (_onHoldWarn) _onHoldWarn();
            break;
        case HOLD_RELEASE:
            if (_onHoldRelease) _onHoldRelease();
            break;
        default:
            break;
    }
}