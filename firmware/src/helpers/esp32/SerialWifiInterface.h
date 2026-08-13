#pragma once

#include "../BaseSerialInterface.h"
#include <WiFi.h>

// Aantal companions dat tegelijk verbonden mag zijn (Home Assistant, de
// MeshCore-app, meshcore-cli, ...). Elke slot kost ~2-3 kB RAM.
#ifndef WIFI_MAX_CLIENTS
  #define WIFI_MAX_CLIENTS 4
#endif

class SerialWifiInterface : public BaseSerialInterface {
  bool deviceConnected;
  bool _isEnabled;
  unsigned long _last_write;
  unsigned long adv_restart_time;

  WiFiServer server;

  struct FrameHeader {
    uint8_t type;
    uint16_t length;
  };

  struct Frame {
    uint8_t len;
    int8_t dest_slot;   // -1 = naar alle clients (push), anders alleen dat slot
    uint8_t buf[MAX_FRAME_SIZE];
  };

  // Per client een eigen socket en frame-headerstatus: verbindingen mogen
  // elkaars half ontvangen frames niet in de war sturen.
  struct ClientSlot {
    WiFiClient client;
    FrameHeader header;
  };
  ClientSlot slots[WIFI_MAX_CLIENTS];
  int next_poll;    // round-robin, zodat elke client evenveel aan bod komt
  // Slot waarvan we zojuist een commando doorgaven: alles wat de mesh daarna
  // wegschrijft is het antwoord daarop en gaat alléén naar die client.
  // -1 = ongevraagd bericht (advert, inkomend bericht) -> naar iedereen.
  int8_t reply_slot;

  #define FRAME_QUEUE_SIZE  4
  int recv_queue_len;
  Frame recv_queue[FRAME_QUEUE_SIZE];
  int send_queue_len;
  Frame send_queue[FRAME_QUEUE_SIZE];

  void clearBuffers() { recv_queue_len = 0; send_queue_len = 0; }

  void acceptNewClients();
  size_t readFromSlot(ClientSlot& slot, uint8_t dest[]);
  int connectedCount();

protected:

public:
  SerialWifiInterface() : server(WiFiServer()) {
    deviceConnected = false;
    _isEnabled = false;
    _last_write = 0;
    next_poll = 0;
    reply_slot = -1;
    send_queue_len = recv_queue_len = 0;
    for (int i = 0; i < WIFI_MAX_CLIENTS; i++) {
      slots[i].header.type = 0;
      slots[i].header.length = 0;
    }
  }

  void begin(int port);

  // BaseSerialInterface methods
  void enable() override;
  void disable() override;
  bool isEnabled() const override { return _isEnabled; }

  bool isConnected() const override;
  bool isWriteBusy() const override;

  size_t writeFrame(const uint8_t src[], size_t len) override;
  size_t checkRecvFrame(uint8_t dest[]) override;
};

#if WIFI_DEBUG_LOGGING && ARDUINO
  #include <Arduino.h>
  #define WIFI_DEBUG_PRINT(F, ...) Serial.printf("WiFi: " F, ##__VA_ARGS__)
  #define WIFI_DEBUG_PRINTLN(F, ...) Serial.printf("WiFi: " F "\n", ##__VA_ARGS__)
#else
  #define WIFI_DEBUG_PRINT(...) {}
  #define WIFI_DEBUG_PRINTLN(...) {}
#endif
