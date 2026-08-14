#pragma once

#include <helpers/esp32/SerialWifiInterface.h>
#include <helpers/esp32/SerialBLEInterface.h>

/**
 * Runs the BLE and WiFi(TCP) companion interfaces side by side.
 * Frames received on either side are handed to the mesh; replies and pushes
 * go to the side that most recently delivered a frame, falling back to
 * whichever side has a live connection.
 */
class DualSerialInterface : public BaseSerialInterface {
  SerialWifiInterface _wifi;
  SerialBLEInterface _ble;
  BaseSerialInterface* _last_active;

  BaseSerialInterface* target() const {
    if (_last_active->isConnected()) return _last_active;
    if (_wifi.isConnected()) return (BaseSerialInterface *) &_wifi;
    if (_ble.isConnected()) return (BaseSerialInterface *) &_ble;
    return _last_active;
  }

public:
  DualSerialInterface() { _last_active = &_ble; }

  void begin(int tcp_port, const char* ble_prefix, char* ble_name, uint32_t ble_pin) {
    _wifi.begin(tcp_port);
    _ble.begin(ble_prefix, ble_name, ble_pin);
  }

  // BaseSerialInterface methods
  void enable() override { _wifi.enable(); _ble.enable(); }
  void disable() override { _wifi.disable(); _ble.disable(); }
  bool isEnabled() const override { return _wifi.isEnabled() || _ble.isEnabled(); }

  bool isConnected() const override { return _wifi.isConnected() || _ble.isConnected(); }

  bool isWriteBusy() const override { return target()->isWriteBusy(); }

  size_t writeFrame(const uint8_t src[], size_t len) override {
    return target()->writeFrame(src, len);
  }

  size_t checkRecvFrame(uint8_t dest[]) override {
    size_t len = _wifi.checkRecvFrame(dest);
    if (len > 0) { _last_active = &_wifi; return len; }
    len = _ble.checkRecvFrame(dest);
    if (len > 0) { _last_active = &_ble; return len; }
    return 0;
  }
};
