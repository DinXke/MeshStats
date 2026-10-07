#include "MtConfig.h"
#include "MeshTrack.h"
#include <string.h>
#include <Adafruit_LittleFS.h>
#include <InternalFileSystem.h>
using namespace Adafruit_LittleFS_Namespace;

#define MT_CFG_MAGIC 0x3143544DUL   // "MTC1"

static const char* MT_CFG_PATH = "/mt_cfg.dat";
static const char* MT_CFG_TMP  = "/mt_cfg.tmp";

MtCfg mt_cfg;
const char* mt_cfg_load_note = "nog niet geladen";
bool mt_cfg_readonly = false;

uint32_t mt_crc32(uint32_t crc, const uint8_t* p, uint32_t n) {
  crc = ~crc;
  while (n--) {
    crc ^= *p++;
    for (int k = 0; k < 8; k++) crc = (crc >> 1) ^ (0xEDB88320UL & (0UL - (crc & 1)));
  }
  return ~crc;
}

static uint32_t cfg_crc(const MtCfg& c) {
  return mt_crc32(0, (const uint8_t*)&c, offsetof(MtCfg, crc));
}

void mt_cfg_defaults(MtCfg& c) {
  memset(&c, 0, sizeof(c));
  c.mode               = MT_MODE_TRACKER;
  c.track_in_companion = 1;
  c.accel_sens         = 1;
  c.ack_retries        = 2;
  c.min_speed_kmh      = 10;
  c.min_dist_m         = 100;
  c.turn_min_deg       = 30;
  c.turn_min_speed_kmh = 5;
  c.min_interval_s     = 60;
  c.max_interval_s     = 10 * 60;
  c.still_timeout_s    = 5 * 60;
  c.heartbeat_s        = 12 * 3600;
  c.fix_timeout_s      = 90;
  c.fix_timeout_hb_s   = 30;
  c.fast_interval_s    = 30;
  c.fast_keep          = 2;
  c.fast_ack_s         = 10;
  c.slow_after         = 3;
  c.slow_factor        = 3;
  c.sample_s           = 15;
  c.adaptive           = 1;
}

// Eén bestand proberen. 0 = geladen, 1 = bestand van nieuwere fw, -1 = onbruikbaar.
static int load_file(const char* path) {
  File f = InternalFS.open(path, FILE_O_READ);
  if (!f) return -1;
  MtCfg tmp;
  mt_cfg_defaults(tmp);
  uint8_t buf[sizeof(MtCfg)];
  int n = f.read(buf, sizeof(buf));
  f.close();
  if (n < (int)offsetof(MtCfg, mode)) return -1;
  MtCfg* hdr = (MtCfg*)buf;
  if (hdr->magic != MT_CFG_MAGIC) return -1;
  if (hdr->version > MT_CFG_VERSION || hdr->size > sizeof(MtCfg)) return 1;
  if (hdr->size != n || hdr->size < offsetof(MtCfg, _pad) + 2 + 4) return -1;
  // Oudere versie: velden worden enkel achteraan toegevoegd, de crc staat altijd op het einde.
  uint32_t crc;
  memcpy(&crc, buf + hdr->size - 4, 4);
  if (mt_crc32(0, buf, hdr->size - 4) != crc) return -1;
  memcpy(&tmp, buf, hdr->size - 4);          // nieuwe velden houden hun standaardwaarde
  mt_cfg = tmp;
  if (hdr->version < MT_CFG_VERSION) return 2;
  return 0;
}

static bool write_whole(const char* path, const void* data, size_t len) {
  InternalFS.remove(path);
  File f = InternalFS.open(path, FILE_O_WRITE);
  if (!f) return false;
  size_t n = f.write((const uint8_t*)data, len);
  f.close();
  if (n != len) { InternalFS.remove(path); return false; }
  return true;
}

bool mt_cfg_save() {
  if (mt_cfg_readonly) return false;
  mt_cfg.magic   = MT_CFG_MAGIC;
  mt_cfg.version = MT_CFG_VERSION;
  mt_cfg.size    = sizeof(MtCfg);
  mt_cfg.crc     = cfg_crc(mt_cfg);
  if (!write_whole(MT_CFG_TMP, &mt_cfg, sizeof(mt_cfg))) return false;  // oude blijft staan
  if (!InternalFS.rename(MT_CFG_TMP, MT_CFG_PATH)) {
    InternalFS.remove(MT_CFG_PATH);
    if (!InternalFS.rename(MT_CFG_TMP, MT_CFG_PATH)) return false;
  }
  return true;
}

void mt_cfg_begin() {
  mt_cfg_defaults(mt_cfg);
  int r = load_file(MT_CFG_PATH);
  if (r == 0) { mt_cfg_load_note = "geladen v4"; return; }
  if (r == 2) { mt_cfg_load_note = "omgezet naar v4"; mt_cfg_save(); return; }
  if (r == 1) {
    mt_cfg_readonly = true;
    mt_cfg_load_note = "DEFAULTS (bestand van nieuwere firmware, niet overschreven)";
    return;
  }
  if (load_file(MT_CFG_TMP) == 0) {
    mt_cfg_load_note = "hersteld uit .tmp";
    mt_cfg_save();
    return;
  }
  mt_cfg_defaults(mt_cfg);
  mt_cfg_load_note = InternalFS.exists(MT_CFG_PATH) ? "DEFAULTS (bestand onleesbaar)"
                                                    : "DEFAULTS (geen bestand)";
}
