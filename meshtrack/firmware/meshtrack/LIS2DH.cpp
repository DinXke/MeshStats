// MeshTrack 0.9.7: LIS2DH (RAK WisMesh Tag), zie LIS2DH.h.
#if defined(RAK_WISMESH_TAG)
#include "LIS2DH.h"
#include <Wire.h>

#define LIS_REG_WHO_AM_I  0x0F   // -> 0x33
#define LIS_REG_CTRL1     0x20   // ODR, LPen, Z/Y/X
#define LIS_REG_CTRL4     0x23   // BDU, FS, HR
#define LIS_REG_OUT_X_L   0x28   // 6 bytes; bit 7 van het adres = automatisch ophogen
#define LIS_WHO_AM_I      0x33

bool MtLIS2DH::writeReg(uint8_t reg, uint8_t val) {
  Wire.beginTransmission(_addr);
  Wire.write(reg);
  Wire.write(val);
  return Wire.endTransmission() == 0;
}

bool MtLIS2DH::readRegs(uint8_t reg, uint8_t* buf, uint8_t n) {
  Wire.beginTransmission(_addr);
  Wire.write(n > 1 ? (uint8_t)(reg | 0x80) : reg);
  if (Wire.endTransmission(false) != 0) return false;
  if (Wire.requestFrom(_addr, n) != n) return false;
  for (uint8_t i = 0; i < n; i++) {
    if (!Wire.available()) return false;             // nooit wachten op bytes die niet komen
    buf[i] = (uint8_t)Wire.read();
  }
  return true;
}

bool MtLIS2DH::begin() {
  _present = false;
  static const uint8_t addrs[] = { 0x18, 0x19 };
  for (uint8_t a : addrs) {
    _addr = a;
    uint8_t id = 0;
    if (readRegs(LIS_REG_WHO_AM_I, &id, 1) && id == LIS_WHO_AM_I) { _present = true; break; }
  }
  if (!_present) { _addr = 0; return false; }
  // 10 Hz, normale modus (LPen = 0), X/Y/Z aan; BDU aan, +-2 g, geen hoge resolutie.
  if (!writeReg(LIS_REG_CTRL1, 0x27) || !writeReg(LIS_REG_CTRL4, 0x80)) { _present = false; return false; }
  return true;
}

bool MtLIS2DH::read(float& gx, float& gy, float& gz) {
  if (!_present) return false;
  uint8_t b[6];
  if (!readRegs(LIS_REG_OUT_X_L, b, 6)) return false;
  // links uitgelijnd 16 bits; bij +-2 g is 1 g = 16384 (ongeacht 8/10/12 bits resolutie)
  int16_t x = (int16_t)(b[0] | (b[1] << 8)), y = (int16_t)(b[2] | (b[3] << 8)), z = (int16_t)(b[4] | (b[5] << 8));
  gx = x / 16384.0f;
  gy = y / 16384.0f;
  gz = z / 16384.0f;
  return true;
}
#endif   // RAK_WISMESH_TAG
