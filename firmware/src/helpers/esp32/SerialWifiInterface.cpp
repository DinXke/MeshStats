#include "SerialWifiInterface.h"
#include <WiFi.h>

void SerialWifiInterface::begin(int port) {
  // wifi setup is handled outside of this class, only starts the server
  server.begin(port);
}

// ---------- public methods
void SerialWifiInterface::enable() {
  if (_isEnabled) return;

  _isEnabled = true;
  clearBuffers();
}

void SerialWifiInterface::disable() {
  _isEnabled = false;
}

size_t SerialWifiInterface::writeFrame(const uint8_t src[], size_t len) {
  if (len > MAX_FRAME_SIZE) {
    WIFI_DEBUG_PRINTLN("writeFrame(), frame too big, len=%d\n", len);
    return 0;
  }

  if (deviceConnected && len > 0) {
    if (send_queue_len >= FRAME_QUEUE_SIZE) {
      WIFI_DEBUG_PRINTLN("writeFrame(), send_queue is full!");
      return 0;
    }

    send_queue[send_queue_len].len = len;  // add to send queue
    // Alles wat de mesh wegschrijft direct na een doorgegeven commando is het
    // antwoord daarop; dat gaat alleen naar de client die het vroeg.
    send_queue[send_queue_len].dest_slot = reply_slot;
    memcpy(send_queue[send_queue_len].buf, src, len);
    send_queue_len++;

    return len;
  }
  return 0;
}

bool SerialWifiInterface::isWriteBusy() const {
  return false;
}

int SerialWifiInterface::connectedCount() {
  int n = 0;
  for (int i = 0; i < WIFI_MAX_CLIENTS; i++) {
    if (slots[i].client.connected()) n++;
  }
  return n;
}

// Nieuwe verbindingen aannemen in een vrij slot. Alleen als alle slots bezet
// zijn wordt de oudste vervangen; zo kunnen meerdere companions (Home
// Assistant, de app, meshcore-cli) tegelijk verbonden blijven.
void SerialWifiInterface::acceptNewClients() {
  while (true) {
    WiFiClient newClient = server.available();
    if (!newClient) break;

    int slot = -1;
    for (int i = 0; i < WIFI_MAX_CLIENTS; i++) {
      if (!slots[i].client.connected()) { slot = i; break; }
    }
    if (slot < 0) {
      slot = next_poll % WIFI_MAX_CLIENTS;   // alles bezet: oudste opgeven
      WIFI_DEBUG_PRINTLN("All %d slots busy, replacing slot %d", WIFI_MAX_CLIENTS, slot);
      slots[slot].client.stop();
    }

    slots[slot].client = newClient;
    slots[slot].header.type = 0;      // frame-status hoort bij de verbinding
    slots[slot].header.length = 0;
    WIFI_DEBUG_PRINTLN("Got connection in slot %d (%d connected)", slot, connectedCount());
  }
}

// Leest hoogstens één compleet frame uit dit slot; 0 = (nog) niets compleet.
size_t SerialWifiInterface::readFromSlot(ClientSlot& slot, uint8_t dest[]) {
  WiFiClient& client = slot.client;
  FrameHeader& header = slot.header;

  // check if we are waiting for a frame header
  if (header.type == 0 || header.length == 0) {

    // make sure we have received enough bytes for a frame header
    // 3 bytes frame header = (1 byte frame type) + (2 bytes frame length as unsigned 16-bit little endian)
    const int frame_header_length = 3;
    if (client.available() < frame_header_length) return 0;

    // read frame header
    client.readBytes(&header.type, 1);
    client.readBytes((uint8_t*)&header.length, 2);
  }

  if (header.type == 0 || header.length == 0) return 0;

  // make sure we have received enough bytes for the required frame length
  int available = client.available();
  int frame_type = header.type;
  int frame_length = header.length;
  if (frame_length > available) {
    WIFI_DEBUG_PRINTLN("Waiting for %d more bytes", frame_length - available);
    return 0;
  }

  // skip frames that are larger than MAX_FRAME_SIZE
  if (frame_length > MAX_FRAME_SIZE) {
    WIFI_DEBUG_PRINTLN("Skipping frame: length=%d is larger than MAX_FRAME_SIZE=%d", frame_length, MAX_FRAME_SIZE);
    while (frame_length > 0) {
      uint8_t skip[1];
      int skipped = client.read(skip, 1);
      frame_length -= skipped;
    }
    header.type = 0; header.length = 0;
    return 0;
  }

  // skip frames that are not expected type
  // '<' is 0x3c which indicates a frame sent from app to radio
  if (frame_type != '<') {
    WIFI_DEBUG_PRINTLN("Skipping frame: type=0x%x is unexpected", frame_type);
    while (frame_length > 0) {
      uint8_t skip[1];
      int skipped = client.read(skip, 1);
      frame_length -= skipped;
    }
    header.type = 0; header.length = 0;
    return 0;
  }

  // read frame data to provided buffer
  client.readBytes(dest, frame_length);

  // ready for next frame
  header.type = 0; header.length = 0;
  return frame_length;
}

size_t SerialWifiInterface::checkRecvFrame(uint8_t dest[]) {
  // Vanaf hier is alles wat de mesh wegschrijft ongevraagd, tot we hieronder
  // een commando van een specifieke client doorgeven.
  reply_slot = -1;
  acceptNewClients();

  int connected = connectedCount();
  if (connected > 0) {
    if (!deviceConnected) {
      WIFI_DEBUG_PRINTLN("Got connection");
      deviceConnected = true;
    }
  } else {
    if (deviceConnected) {
      deviceConnected = false;
      WIFI_DEBUG_PRINTLN("Disconnected");
    }
    return 0;
  }

  // uitgaande frames versturen: antwoorden naar de vrager, de rest naar allen
  if (send_queue_len > 0) {
    _last_write = millis();
    int len = send_queue[0].len;

    uint8_t pkt[3+len]; // use same header as serial interface so client can delimit frames
    pkt[0] = '>';
    pkt[1] = (len & 0xFF);  // LSB
    pkt[2] = (len >> 8);    // MSB
    memcpy(&pkt[3], send_queue[0].buf, send_queue[0].len);
    int8_t to_slot = send_queue[0].dest_slot;
    if (to_slot >= 0 && to_slot < WIFI_MAX_CLIENTS) {
      if (slots[to_slot].client.connected()) slots[to_slot].client.write(pkt, 3 + len);
    } else {
      // ongevraagd bericht (advert, inkomend bericht, ack): naar alle clients
      for (int i = 0; i < WIFI_MAX_CLIENTS; i++) {
        if (slots[i].client.connected()) slots[i].client.write(pkt, 3 + len);
      }
    }
    send_queue_len--;
    for (int i = 0; i < send_queue_len; i++) {   // delete top item from queue
      send_queue[i] = send_queue[i + 1];
    }
    return 0;
  }

  // inkomende frames: round-robin, zodat één drukke client de andere niet
  // wegdrukt
  for (int n = 0; n < WIFI_MAX_CLIENTS; n++) {
    int i = (next_poll + n) % WIFI_MAX_CLIENTS;
    if (!slots[i].client.connected()) continue;
    size_t len = readFromSlot(slots[i], dest);
    if (len > 0) {
      next_poll = (i + 1) % WIFI_MAX_CLIENTS;
      reply_slot = (int8_t)i;   // antwoord hoort bij deze client
      return len;
    }
  }

  return 0;
}

bool SerialWifiInterface::isConnected() const {
  return deviceConnected;
}
