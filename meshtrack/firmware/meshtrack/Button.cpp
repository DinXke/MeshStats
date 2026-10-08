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
volatile uint32_t Button::s_pressAt = 0;
volatile uint32_t Button::s_prevDur = 0;

void Button::isr() {
    uint32_t now = millis();
    bool pressed = digitalRead(s_isrPin) == s_isrActive;
    if (pressed) {
        // Alleen een echte klik: de knop was minstens BUTTON_ISR_RELEASED_MS los
        // (dender bij indrukken/loslaten duurt korter en telt dus niet).
        if (now - s_lastRelease >= BUTTON_ISR_RELEASED_MS) {
            s_prevDur = s_lastRelease - s_pressAt;   // hoe lang de vorige druk duurde
            s_presses++;
            s_pressAt = now;
        }
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
    if (_useIsr) {                       // MeshTrack: digitale knop met interrupt
        _lastReadTime = now;
        updateIsr(now);
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

// MeshTrack 0.8.2: klikken, vasthouden en het klikvenster volledig op de tijdstempels die
// de interrupt vastlegt. Op batterij slaapt de processor tussen twee gebeurtenissen, en dan
// kwam de lus soms op een ongelukkig moment kijken: twee tikken werden één klik. Nu maakt
// het niet uit wanneer de lus draait; ze beslist pas als de knop stabiel los is.
// Een afgelopen druk afhandelen: klik, SOS (2..8 s) of uitschakelen (> 8 s).
void Button::finishPress(uint32_t dur, uint32_t releasedAt) {
    _isrHeld = false;
    if (_longFired) {                     // al uitgeschakeld tijdens het vasthouden
        _clickCount = 0;
    } else if (dur >= BUTTON_LONG_PRESS_TIME_MS) {
        _clickCount = 0;
        triggerEvent(LONG_PRESS);         // de lus sliep tijdens het vasthouden
    } else if (dur >= BUTTON_HOLD_ARM_MS) {
        _clickCount = 0;
        triggerEvent(HOLD_RELEASE);       // 2..8 s = SOS
    } else {
        if (_clickCount < 4) _clickCount++;
        _releaseTime = releasedAt;
        _state = WAITING_FOR_MULTI_CLICK;
    }
}

void Button::updateIsr(uint32_t now) {
    noInterrupts();
    uint32_t presses = s_presses, pressAt = s_pressAt, lastRelease = s_lastRelease, lastEdge = s_lastEdge,
             prevDur = s_prevDur;
    interrupts();
    bool level = readButton();
    bool settled = now - lastEdge > BUTTON_DEBOUNCE_TIME_MS;   // geen flank meer sinds 50 ms

    // Nieuwe drukken sinds de vorige keer. Een nieuwe druk telt pas na loslaten, dus alle
    // drukken behalve de laatste zijn al voorbij.
    uint32_t fresh = presses - _seenPresses;
    if (fresh) {
        _seenPresses = presses;
        triggerEvent(ANY_PRESS);
        // De vorige druk die we al zagen maar nog niet afhandelden (de lus sliep tot nu):
        // met zijn duur volgens de interrupt, als er precies één nieuwe druk bij kwam.
        if (_isrHeld) finishPress(fresh == 1 ? prevDur : 0, pressAt);
        // Drukken die de lus helemaal niet zag: korte klikken.
        for (uint32_t i = 1; i < fresh && _clickCount < 4; i++) _clickCount++;
        _isrHeld = true;
        _pressTime = pressAt;
        _armFired = _warnFired = _longFired = false;
    }

    if (_isrHeld) {
        if (settled && !level) {
            // Losgelaten: hoe lang ingedrukt, volgens de interrupt.
            uint32_t dur = (int32_t)(lastRelease - _pressTime) > 0 ? lastRelease - _pressTime : now - _pressTime;
            finishPress(dur, lastRelease);
        } else if (level) {
            // Nog ingedrukt: wapenbiep, waarschuwing, uitschakelen.
            uint32_t held = now - _pressTime;
            if (!_armFired && held > BUTTON_HOLD_ARM_MS) { _armFired = true; triggerEvent(HOLD_ARM); }
            if (!_warnFired && held > BUTTON_HOLD_WARN_MS) { _warnFired = true; triggerEvent(HOLD_WARN); }
            if (!_longFired && held > BUTTON_LONG_PRESS_TIME_MS) { _longFired = true; _clickCount = 0; triggerEvent(LONG_PRESS); }
        }
    }
    _currentState = _isrHeld && level;

    // Klikvenster: BUTTON_CLICK_TIMEOUT_MS na het laatste loslaten (tijd van de interrupt),
    // en alleen als de knop stabiel los is.
    if (!_isrHeld && _clickCount && settled && !level && now - _releaseTime > BUTTON_CLICK_TIMEOUT_MS) {
        uint8_t n = _clickCount;
        _clickCount = 0;
        _state = IDLE;
        mt_log("knop: %ux", (unsigned)n);
        if (n == 1) triggerEvent(SHORT_PRESS);
        else if (n == 2) triggerEvent(DOUBLE_PRESS);
        else if (n == 3) triggerEvent(TRIPLE_PRESS);
        else triggerEvent(QUADRUPLE_PRESS);
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
        // De interrupt telde deze klik al (die reageert meteen, de lus pas na de
        // debounce). Telde ze er meer, dan zag de lus een vorige tik niet.
        uint32_t n = takeIsrPresses();
        if (n > 1) _clickCount += (n - 1 > 3 ? 3 : n - 1);
        _pressTime = now;
        _state = PRESSED;
        _armFired = _warnFired = false;
        triggerEvent(ANY_PRESS);
    } else {
        // Button released
        // MeshTrack: een snelle dubbelklik ziet de lus als één lange druk (~300-400 ms):
        // het korte loslaten ertussen valt binnen de debounce van 50 ms. De interrupt
        // ziet het wel; elke klik die ze tijdens deze druk telde, komt er hier bij.
        uint32_t extra = takeIsrPresses();
        if (_state == PRESSED) {
            uint32_t pressDuration = now - _pressTime;
            
            if (pressDuration >= BUTTON_HOLD_ARM_MS && pressDuration < BUTTON_LONG_PRESS_TIME_MS) {
                // MeshTrack: losgelaten tussen 2 en 8 s = SOS
                _state = IDLE;
                _clickCount = 0;
                triggerEvent(HOLD_RELEASE);
            } else if (pressDuration < BUTTON_LONG_PRESS_TIME_MS) {
                // Short press detected
                _clickCount += 1 + (extra > 3 ? 3 : extra);
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

// MeshTrack: aantal klikken dat de interrupt telde sinds de vorige keer.
uint32_t Button::takeIsrPresses() {
    if (!_useIsr) return 0;
    uint32_t p = s_presses;
    uint32_t n = p - _seenPresses;
    _seenPresses = p;
    return n;
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