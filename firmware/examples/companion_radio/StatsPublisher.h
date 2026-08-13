#pragma once

/* StatsPublisher — publiceert de statistieken van deze node naar een externe
 * statistiekensite, en biedt een kleine beheerpagina om dat in te stellen.
 *
 * - Beheerpagina: http://<node-ip>/  (instellingen + live statistieken)
 * - Push: POST <url>/api/v1/ingest met Bearer-token, JSON-payload
 *
 * Alles is optioneel: zonder ingestelde URL doet de module niets behalve de
 * beheerpagina serveren.
 */

#include <Arduino.h>
#include <FS.h>
#include <WebServer.h>

#define STATS_CFG_FILE      "/stats_cfg.json"
#define STATS_URL_MAX       128
#define STATS_TOKEN_MAX     96

class MyMesh;   // vooruitverwijzing; vermijdt include-cyclus

class StatsPublisher {
public:
  struct Config {
    char url[STATS_URL_MAX];      // bv. https://stats.example.com
    char token[STATS_TOKEN_MAX];  // Bearer-token van de site
    uint32_t interval_secs;       // hoe vaak pushen
    bool enabled;
  };

  StatsPublisher() : _server(80), _fs(nullptr), _mesh(nullptr),
                     _last_push(0), _last_result(0), _push_count(0),
                     _fail_count(0), _started(false) {
    memset(&_cfg, 0, sizeof(_cfg));
    _cfg.interval_secs = 300;
    _cfg.enabled = false;
    _last_error[0] = 0;
  }

  void begin(FS& fs, MyMesh* mesh);
  void loop();

  const Config& getConfig() const { return _cfg; }

private:
  WebServer _server;
  FS* _fs;
  MyMesh* _mesh;
  Config _cfg;
  unsigned long _last_push;
  int _last_result;          // laatste HTTP-statuscode (0 = nog niet geprobeerd)
  uint32_t _push_count;
  uint32_t _fail_count;
  bool _started;
  char _last_error[64];

  void loadConfig();
  void saveConfig();
  bool pushNow();

  void handleRoot();
  void handleSave();
  void handleTest();
  void handleStatsJson();
};
