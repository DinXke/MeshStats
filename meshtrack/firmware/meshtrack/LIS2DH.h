#pragma once
// Minimale LIS2DH-driver (ST, 3 assen) voor de RAK WisMesh Tag (MeshTrack 0.9.7).
//
// RAK noemt een "integrated acceleration sensor based on the ST LIS2DH"; MeshCore en Meshtastic
// gebruiken hem (nog) niet en de aansluiting van zijn INT-pin is niet gedocumenteerd. Daarom:
//  - I2C op de enige bus van de Tag (Wire: SDA P0.25, SCL P0.24), adres 0x18 of 0x19 (SA0),
//    herkend aan WHO_AM_I (0x0F) = 0x33;
//  - GEEN interrupt: configMotionInterrupt() geeft false, MtMotion pollt dan elke seconde;
//  - NIET getest op echte hardware.
// I2C-veiligheid zoals QMA6100P: elke transactie controleert de Wire-terugkeerwaarden en geeft bij
// een fout meteen false; MtMotion schakelt na 10 mislukte lezingen naar "fout" (alleen GPS-snelheid).
#include <Arduino.h>

class MtLIS2DH {
public:
  bool begin();                                   // zoeken + instellen; false = afwezig
  bool isPresent() const { return _present; }
  uint8_t address() const { return _addr; }
  bool read(float& gx, float& gy, float& gz);     // versnelling in g (bereik +-2 g)
  bool configMotionInterrupt(uint8_t) { return false; }   // INT-pin onbekend: pollen

private:
  bool writeReg(uint8_t reg, uint8_t val);
  bool readRegs(uint8_t reg, uint8_t* buf, uint8_t n);
  uint8_t _addr = 0;
  bool _present = false;
};
