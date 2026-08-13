#include "StatsPublisher.h"
#include "MyMesh.h"
#include <WiFi.h>
#include <HTTPClient.h>
#include <WiFiClientSecure.h>

// ---------------------------------------------------------------- config i/o

void StatsPublisher::loadConfig() {
  if (!_fs) return;
  File f = _fs->open(STATS_CFG_FILE, "r");
  if (!f) return;

  // Heel eenvoudige parser: we bewaren zelf, dus het formaat ligt vast.
  // {"url":"...","token":"...","interval":300,"enabled":1}
  String s = f.readString();
  f.close();

  auto grab = [&](const char* key, char* out, size_t max) {
    String pat = String("\"") + key + "\":\"";
    int i = s.indexOf(pat);
    if (i < 0) { out[0] = 0; return; }
    i += pat.length();
    int j = s.indexOf('"', i);
    if (j < 0) { out[0] = 0; return; }
    String v = s.substring(i, j);
    strncpy(out, v.c_str(), max - 1);
    out[max - 1] = 0;
  };
  grab("url", _cfg.url, STATS_URL_MAX);
  grab("token", _cfg.token, STATS_TOKEN_MAX);

  int i = s.indexOf("\"interval\":");
  if (i >= 0) _cfg.interval_secs = (uint32_t)s.substring(i + 11).toInt();
  if (_cfg.interval_secs < 30) _cfg.interval_secs = 30;

  i = s.indexOf("\"enabled\":");
  _cfg.enabled = (i >= 0) && (s.substring(i + 10).toInt() != 0);
}

void StatsPublisher::saveConfig() {
  if (!_fs) return;
  File f = _fs->open(STATS_CFG_FILE, "w");
  if (!f) return;
  f.printf("{\"url\":\"%s\",\"token\":\"%s\",\"interval\":%u,\"enabled\":%d}",
           _cfg.url, _cfg.token, (unsigned)_cfg.interval_secs, _cfg.enabled ? 1 : 0);
  f.close();
}

// ------------------------------------------------------------------- pushing

bool StatsPublisher::pushNow() {
  if (!_mesh || _cfg.url[0] == 0 || WiFi.status() != WL_CONNECTED) return false;
  // Een HTTP-verzoek kost enkele kB; bij weinig vrij geheugen slaan we deze
  // ronde over in plaats van de node te laten crashen.
  if (ESP.getFreeHeap() < 40000) {
    _fail_count++;
    snprintf(_last_error, sizeof(_last_error), "te weinig geheugen (%u)", (unsigned)ESP.getFreeHeap());
    return false;
  }

  static char body[1024];
  size_t n = _mesh->fillStatsJson(body, sizeof(body));
  if (n == 0) return false;

  String endpoint = String(_cfg.url);
  if (endpoint.endsWith("/")) endpoint.remove(endpoint.length() - 1);
  endpoint += "/api/v1/ingest";

  HTTPClient http;
  bool ok = false;
  if (endpoint.startsWith("https://")) {
    // Publieke statistiekensites draaien vaak achter een tunnel met een
    // certificaat dat we hier niet kunnen valideren; de payload bevat geen
    // geheimen en het token beschermt de API.
    WiFiClientSecure secure;
    secure.setInsecure();
    http.begin(secure, endpoint);
    http.addHeader("Content-Type", "application/json");
    http.addHeader("Authorization", String("Bearer ") + _cfg.token);
    _last_result = http.POST((uint8_t *)body, n);
    ok = (_last_result >= 200 && _last_result < 300);
    http.end();
  } else {
    WiFiClient plain;
    http.begin(plain, endpoint);
    http.addHeader("Content-Type", "application/json");
    http.addHeader("Authorization", String("Bearer ") + _cfg.token);
    _last_result = http.POST((uint8_t *)body, n);
    ok = (_last_result >= 200 && _last_result < 300);
    http.end();
  }

  if (ok) {
    _push_count++;
    _last_error[0] = 0;
  } else {
    _fail_count++;
    snprintf(_last_error, sizeof(_last_error), "HTTP %d", _last_result);
  }
  return ok;
}

// ---------------------------------------------------------------- web server

static const char PAGE_HEAD[] PROGMEM =
  "<!doctype html><html lang=nl><head><meta charset=utf-8>"
  "<meta name=viewport content='width=device-width,initial-scale=1'>"
  "<title>MeshCore node</title><style>"
  "body{margin:0;background:#0b0f14;color:#d7e2ea;font:15px/1.5 system-ui,sans-serif}"
  "main{max-width:640px;margin:0 auto;padding:1rem 1.2rem 3rem}"
  "h1{font-size:1.4rem;margin:1rem 0 .3rem}"
  "h2{font-size:.8rem;text-transform:uppercase;letter-spacing:.18em;color:#35e08c;margin:1.8rem 0 .6rem}"
  ".card{background:#121a23;border:1px solid #1e2b3a;border-radius:10px;padding:1rem}"
  "label{display:block;margin-bottom:.8rem}"
  "input{width:100%;margin-top:.25rem;padding:.45rem .7rem;border-radius:7px;"
  "border:1px solid #1e2b3a;background:#10161e;color:#d7e2ea;box-sizing:border-box}"
  "button{padding:.45rem .9rem;border:none;border-radius:7px;background:#1d7a4f;color:#eafff4;cursor:pointer}"
  "button:hover{background:#35e08c;color:#06130c}"
  "table{width:100%;border-collapse:collapse}"
  "td{padding:.35rem .5rem;border-bottom:1px solid #1e2b3a;font-family:ui-monospace,monospace;font-size:.9rem}"
  "td:first-child{color:#7d8fa0}"
  ".muted{color:#7d8fa0;font-size:.85rem}"
  "</style></head><body><main>";

void StatsPublisher::handleRoot() {
  // In stukjes versturen: één grote String opbouwen kost te veel heap op een
  // node die ook mesh, WiFi en BLE draait (dat leidde tot een crash).
  char buf[320];

  _server.setContentLength(CONTENT_LENGTH_UNKNOWN);
  _server.send(200, "text/html; charset=utf-8", "");
  _server.sendContent_P(PAGE_HEAD);

  snprintf(buf, sizeof(buf),
    "<h1>&#128225; %s</h1><p class=muted>Beheer van deze node &middot; IP %s</p>",
    _mesh ? _mesh->getNodeName() : "MeshCore node",
    WiFi.localIP().toString().c_str());
  _server.sendContent(buf);

  _server.sendContent(F("<h2>Statistieken doorsturen</h2><div class=card>"
                        "<form method=post action=/save>"));
  snprintf(buf, sizeof(buf),
    "<label>URL van de statistiekensite<input name=url value='%s' "
    "placeholder='http://10.0.0.5:8080'></label>", _cfg.url);
  _server.sendContent(buf);
  snprintf(buf, sizeof(buf),
    "<label>API-token<input name=token type=password value='%s'></label>", _cfg.token);
  _server.sendContent(buf);
  snprintf(buf, sizeof(buf),
    "<label>Interval (seconden)<input name=interval type=number min=30 max=86400 value=%u></label>"
    "<label><input type=checkbox name=enabled value=1 style='width:auto'%s> Doorsturen ingeschakeld</label>"
    "<button type=submit>Opslaan</button></form>"
    "<form method=post action=/test style='margin-top:.6rem'><button type=submit>Nu versturen</button></form>",
    (unsigned)_cfg.interval_secs, _cfg.enabled ? " checked" : "");
  _server.sendContent(buf);

  snprintf(buf, sizeof(buf),
    "<table style='margin-top:.8rem'><tr><td>Laatste resultaat</td><td>%s%d</td></tr>"
    "<tr><td>Gelukt / mislukt</td><td>%u / %u</td></tr>"
    "<tr><td>Vrij geheugen</td><td>%u bytes</td></tr></table></div>",
    _last_result == 0 ? "nog niet verstuurd " : "HTTP ", _last_result == 0 ? 0 : _last_result,
    (unsigned)_push_count, (unsigned)_fail_count, (unsigned)ESP.getFreeHeap());
  _server.sendContent(buf);

  _server.sendContent(F("<h2>Live statistieken</h2><div class=card>"
    "<div id=stats class=muted>laden...</div></div>"
    "<script>fetch('/stats.json').then(r=>r.json()).then(d=>{"
    "var m=d.metrics||{},h='<table>';"
    "for(var k in m){h+='<tr><td>'+k+'</td><td>'+m[k]+'</td></tr>';}"
    "h+='</table>';document.getElementById('stats').innerHTML=h;});</script>"
    "</main></body></html>"));
  _server.sendContent("");
}

void StatsPublisher::handleSave() {
  if (_server.hasArg("url")) {
    strncpy(_cfg.url, _server.arg("url").c_str(), STATS_URL_MAX - 1);
    _cfg.url[STATS_URL_MAX - 1] = 0;
  }
  if (_server.hasArg("token") && _server.arg("token").length() > 0) {
    strncpy(_cfg.token, _server.arg("token").c_str(), STATS_TOKEN_MAX - 1);
    _cfg.token[STATS_TOKEN_MAX - 1] = 0;
  }
  if (_server.hasArg("interval")) {
    uint32_t v = (uint32_t)_server.arg("interval").toInt();
    _cfg.interval_secs = v < 30 ? 30 : v;
  }
  _cfg.enabled = _server.hasArg("enabled");
  saveConfig();
  _server.sendHeader("Location", "/");
  _server.send(303, "text/plain", "opgeslagen");
}

void StatsPublisher::handleTest() {
  pushNow();
  _server.sendHeader("Location", "/");
  _server.send(303, "text/plain", "verstuurd");
}

void StatsPublisher::handleStatsJson() {
  static char body[1024];
  size_t n = _mesh ? _mesh->fillStatsJson(body, sizeof(body)) : 0;
  if (n == 0) { _server.send(503, "application/json", "{}"); return; }
  _server.send(200, "application/json", body);
}

// -------------------------------------------------------------------- public

void StatsPublisher::begin(FS& fs, MyMesh* mesh) {
  _fs = &fs;
  _mesh = mesh;
  loadConfig();

  _server.on("/", HTTP_GET, [this]() { handleRoot(); });
  _server.on("/save", HTTP_POST, [this]() { handleSave(); });
  _server.on("/test", HTTP_POST, [this]() { handleTest(); });
  _server.on("/stats.json", HTTP_GET, [this]() { handleStatsJson(); });
  _server.begin();
  _started = true;
  _last_push = millis();
}

void StatsPublisher::loop() {
  if (!_started) return;
  _server.handleClient();

  if (!_cfg.enabled || _cfg.url[0] == 0) return;
  if (millis() - _last_push < (unsigned long)_cfg.interval_secs * 1000UL) return;
  _last_push = millis();
  pushNow();
}
