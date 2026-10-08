#include "MtConfig.h"
#include "MeshTrack.h"
#include <string.h>
#include <Adafruit_LittleFS.h>
#include <InternalFileSystem.h>
using namespace Adafruit_LittleFS_Namespace;

// MeshTrack bewaart zijn bestanden op ExtraFS (100 kB, ook de contacten en kanalen staan daar).
// InternalFS is maar 7 blokken van 4 kB en zit vol met de identiteit en de MeshCore-voorkeuren:
// een extra tijdelijk bestand paste er niet meer bij, waardoor elke opslag mislukte (0.7.0).
// Bestanden van oudere firmware op InternalFS worden bij het opstarten verhuisd.
#if defined(EXTRAFS)
  #include <CustomLFS.h>
  extern CustomLFS ExtraFS;
  static Adafruit_LittleFS& cfs() { return ExtraFS; }
  static const bool CFS_IS_EXTRA = true;
#else
  static Adafruit_LittleFS& cfs() { return InternalFS; }
  static const bool CFS_IS_EXTRA = false;
#endif

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
  c.sample_s           = 15;
  c.slow_log_s         = 0;          // SlowTrack standaard uit
  c.slow_send_s        = 30 * 60;
  c.track_mode         = MT_TRACK_CLASSIC;
  c.fifo_max           = 500;
  c.fifo_min           = 5;
  c.fifo_gap_s         = 30;
  c.fifo_per_uur       = 20;
  c.fifo_pogingen      = 3;
  c.fifo_dun           = 10;
  c.fifo_snr           = -5;
}

// Waarden buiten het toegelaten bereik (beschadigd of met de hand gemaakt) terug naar standaard.
static void cfg_sanitize(MtCfg& c) {
  MtCfg d;
  mt_cfg_defaults(d);
  if (c.track_mode > MT_TRACK_FIFO) c.track_mode = d.track_mode;
  if (c.fifo_max < 20 || c.fifo_max > 500) c.fifo_max = d.fifo_max;
  if (c.fifo_min < 1 || c.fifo_min > c.fifo_max) c.fifo_min = c.fifo_max < d.fifo_min ? c.fifo_max : d.fifo_min;
  if (c.fifo_gap_s < 15 || c.fifo_gap_s > 300) c.fifo_gap_s = d.fifo_gap_s;
  if (c.fifo_per_uur < 1 || c.fifo_per_uur > 60) c.fifo_per_uur = d.fifo_per_uur;
  if (c.fifo_pogingen < 1 || c.fifo_pogingen > 10) c.fifo_pogingen = d.fifo_pogingen;
  if (c.fifo_dun > 100) c.fifo_dun = d.fifo_dun;
  if (c.fifo_snr < -20 || c.fifo_snr > 10) c.fifo_snr = d.fifo_snr;
}

// Eén bestand proberen. 0 = geladen, 1 = bestand van nieuwere fw, -1 = onbruikbaar.
static int load_file(Adafruit_LittleFS& fs, const char* path) {
  File f = fs.open(path, FILE_O_READ);
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
  cfg_sanitize(tmp);
  mt_cfg = tmp;
  if (hdr->version < MT_CFG_VERSION) return 2;
  return 0;
}

static bool write_whole(Adafruit_LittleFS& fs, const char* path, const void* data, size_t len) {
  fs.remove(path);
  File f = fs.open(path, FILE_O_WRITE);
  if (!f) return false;
  size_t n = f.write((const uint8_t*)data, len);
  f.close();
  if (n != len) { fs.remove(path); return false; }
  return true;
}

// Klein bestand (bv. het volgnummer) lezen: eerst van ExtraFS, anders nog van InternalFS (oude firmware).
bool mt_file_read(const char* path, void* buf, size_t len) {
  for (int i = 0; i < (CFS_IS_EXTRA ? 2 : 1); i++) {
    Adafruit_LittleFS& fs = i == 0 ? cfs() : (Adafruit_LittleFS&)InternalFS;
    File f = fs.open(path, FILE_O_READ);
    if (!f) continue;
    size_t n = f.read((uint8_t*)buf, len);
    f.close();
    if (n == len) return true;
  }
  return false;
}

// Klein bestand schrijven op ExtraFS; een oude kopie op InternalFS verdwijnt (blok vrij).
bool mt_file_write(const char* path, const void* data, size_t len) {
  if (!write_whole(cfs(), path, data, len)) return false;
  if (CFS_IS_EXTRA) InternalFS.remove(path);
  return true;
}

// Eerst alles naar tmp; pas als dat volledig gelukt is, tmp hernoemen naar path. Mislukt het
// onderweg, dan blijft het oude bestand staan (de lezer valt terug op tmp als path ontbreekt).
bool mt_file_write_atomic(const char* path, const char* tmp, const void* a, size_t alen, const void* b, size_t blen) {
  Adafruit_LittleFS& fs = cfs();
  fs.remove(tmp);
  File f = fs.open(tmp, FILE_O_WRITE);
  if (!f) return false;
  size_t n = f.write((const uint8_t*)a, alen);
  if (blen) n += f.write((const uint8_t*)b, blen);
  f.close();
  if (n != alen + blen) { fs.remove(tmp); return false; }
  if (!fs.rename(tmp, path)) {
    fs.remove(path);
    if (!fs.rename(tmp, path)) return false;
  }
  return true;
}

bool mt_file_read_at(const char* path, size_t off, void* buf, size_t len) {
  File f = cfs().open(path, FILE_O_READ);
  if (!f) return false;
  bool ok = f.seek(off) && f.read((uint8_t*)buf, len) == (int)len;
  f.close();
  return ok;
}

void mt_file_remove(const char* path) { cfs().remove(path); }

// Bezetting van een bestandssysteem in blokken (zoals DataStore van MeshCore). Op een beschadigd
// bestandssysteem kan lfs_traverse naar onbestaande blokken wijzen of in een kring lopen (0.7.1 bleef
// daar hangen, waardoor 'status' nooit af kwam): een onmogelijk blok of meer dan 2x het totaal = beschadigd.
struct FsCount { uint32_t n, total; };
static int count_block(void* p, lfs_block_t block) {
  FsCount* c = (FsCount*)p;
  if (block >= c->total || ++c->n > 2 * c->total) return LFS_ERR_CORRUPT;
  return 0;
}
static bool fs_use(Adafruit_LittleFS& fs, uint32_t& used, uint32_t& total) {
  FsCount c = { 0, fs._getFS()->cfg->block_count };
  int err = lfs_traverse(fs._getFS(), count_block, &c);
  used = c.n;
  total = c.total;
  return err == 0 && c.n <= c.total;
}
bool mt_fs_internal_ok() {
  uint32_t u, t;
  return fs_use(InternalFS, u, t);
}
bool mt_fs_internal_format() { return InternalFS.format(); }
void mt_fs_status(char* out, size_t n) {
  uint32_t iu, it, eu = 0, et = 0;
  bool iok = fs_use(InternalFS, iu, it), eok = true;
  if (CFS_IS_EXTRA) eok = fs_use(cfs(), eu, et);
  char a[24], b[24];
  if (iok) snprintf(a, sizeof(a), "%lu/%lu", (unsigned long)iu, (unsigned long)it); else snprintf(a, sizeof(a), "BESCHADIGD");
  if (eok) snprintf(b, sizeof(b), "%lu/%lu", (unsigned long)eu, (unsigned long)et); else snprintf(b, sizeof(b), "BESCHADIGD");
  snprintf(out, n, "opslag_intern=%s opslag_extra=%s", a, b);
}

bool mt_cfg_save() {
  if (mt_cfg_readonly) return false;
  mt_cfg.magic   = MT_CFG_MAGIC;
  mt_cfg.version = MT_CFG_VERSION;
  mt_cfg.size    = sizeof(MtCfg);
  mt_cfg.crc     = cfg_crc(mt_cfg);
  Adafruit_LittleFS& fs = cfs();
  if (!write_whole(fs, MT_CFG_TMP, &mt_cfg, sizeof(mt_cfg))) return false;  // oude blijft staan
  if (!fs.rename(MT_CFG_TMP, MT_CFG_PATH)) {
    fs.remove(MT_CFG_PATH);
    if (!fs.rename(MT_CFG_TMP, MT_CFG_PATH)) return false;
  }
  if (CFS_IS_EXTRA) {                       // oude kopieën op InternalFS opruimen
    InternalFS.remove(MT_CFG_PATH);
    InternalFS.remove(MT_CFG_TMP);
  }
  return true;
}

void mt_cfg_begin() {
  mt_cfg_defaults(mt_cfg);
  // Eerst de eigen plaats (ExtraFS), daarna wat oudere firmware op InternalFS liet staan.
  for (int i = 0; i < (CFS_IS_EXTRA ? 2 : 1); i++) {
    Adafruit_LittleFS& fs = i == 0 ? cfs() : (Adafruit_LittleFS&)InternalFS;
    const bool moved = i == 1;
    int r = load_file(fs, MT_CFG_PATH);
    if (r == 1) {
      mt_cfg_readonly = true;
      mt_cfg_load_note = "DEFAULTS (bestand van nieuwere firmware, niet overschreven)";
      return;
    }
    if (r != 0 && r != 2 && load_file(fs, MT_CFG_TMP) == 0) r = 3;
    if (r == 0 && !moved) { mt_cfg_load_note = "geladen v6"; return; }
    if (r == 0 || r == 2 || r == 3) {
      bool ok = mt_cfg_save();             // naar ExtraFS (en de oude kopie weg)
      mt_cfg_load_note = moved ? (ok ? "verhuisd naar ExtraFS" : "geladen van InternalFS [verhuizen MISLUKT]")
                       : r == 2 ? "omgezet naar v6" : "hersteld uit .tmp";
      return;
    }
  }
  mt_cfg_defaults(mt_cfg);
  mt_cfg_load_note = cfs().exists(MT_CFG_PATH) ? "DEFAULTS (bestand onleesbaar)" : "DEFAULTS (geen bestand)";
}
