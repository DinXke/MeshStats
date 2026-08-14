#include "MeshStatsNet.h"
#include "MyMesh.h"

#include <WiFi.h>
#include <ESPAsyncWebServer.h>
#include <AsyncElegantOTA.h>
#include <target.h>

#define MSNET_CFG_FILE    "/msnet.json"
#define MSNET_BOOT_FILE   "/msboot"

#define SSID_MAX      33
#define PASS_MAX      65
#define USER_MAX      17

#define STA_TIMEOUT_MS      30000UL    // zo lang proberen voor we zelf uitzenden
#define STA_RETRY_MS       300000UL    // in AP-modus elke 5 min opnieuw proberen
/* Twee vangnetten, want een fout kan ook in het opstarten van de webserver
 * zitten — dan zou de veilige modus er zelf in blijven hangen.
 *   3 herstarts: veilige modus (eigen netwerk + beheerpagina, verder niets)
 *   6 herstarts: deze module start helemaal niet meer op. Wat overblijft is
 *                een gewone MeshCore-repeater met zijn mesh-CLI en 'start ota'.
 * Draait de node 5 minuten aan een stuk, dan geldt de start als geslaagd en
 * gaat de teller terug op nul. */
#define SAFE_MODE_BOOTS          3
#define DISABLE_BOOTS            6
#define STABLE_UPTIME_MS   300000UL

// Ingebouwde standaardwaarden; te overschrijven via de beheerpagina of CLI.
#ifndef WIFI_SSID
  #define WIFI_SSID ""
#endif
#ifndef WIFI_PWD
  #define WIFI_PWD ""
#endif

struct Config {
  char ssid[SSID_MAX];
  char pass[PASS_MAX];
  char ap_pass[PASS_MAX];
  char user[USER_MAX];       // console-login
  char console_pass[PASS_MAX];
};

enum WifiState { WIFI_TRYING, WIFI_OK, WIFI_FALLBACK_AP };

static FS *_fs = nullptr;
static MyMesh *_mesh = nullptr;
static Config _cfg;
static AsyncWebServer _server(80);
static WiFiServer _console(23);
static WiFiClient _client;

static WifiState _state = WIFI_TRYING;
static unsigned long _state_since = 0;
static unsigned long _last_retry = 0;
static bool _safe_mode = false;
static bool _disabled = false;
static bool _boot_cleared = false;
static bool _started = false;
static char _ap_ssid[SSID_MAX];
static char _node_hex[13];

// De webserver draait in een eigen taak; instellingen wegschrijven doen we
// daar niet, maar in loop(). Deze vlag geeft het werk door.
static volatile bool _apply_wifi = false;

// Console-toestand
enum ConsoleState { CON_USER, CON_PASS, CON_READY };
static ConsoleState _con_state = CON_USER;
static char _con_line[160];
static size_t _con_len = 0;
static uint8_t _con_tries = 0;
static unsigned long _con_active = 0;

/* Een sessie die er niet meer is, blijft op de ESP32 nog een tijd 'connected'
 * melden. Zonder deze twee tijden zou één afgebroken verbinding de console
 * voorgoed dichtzetten — net het kanaal dat je nodig hebt als er iets mis is.
 *   CON_IDLE_MS     hierna sluiten we een stille sessie zelf
 *   CON_TAKEOVER_MS is de bestaande sessie langer stil, dan mag een nieuwe
 *                   verbinding ze overnemen in plaats van geweigerd te worden
 */
#define CON_IDLE_MS       300000UL
#define CON_TAKEOVER_MS    60000UL

// ------------------------------------------------------------- instellingen

static void loadConfig() {
  memset(&_cfg, 0, sizeof(_cfg));
  strncpy(_cfg.ssid, WIFI_SSID, SSID_MAX - 1);
  strncpy(_cfg.pass, WIFI_PWD, PASS_MAX - 1);
  strcpy(_cfg.ap_pass, "meshcore");
  strcpy(_cfg.user, "admin");
  strcpy(_cfg.console_pass, "meshcore");

  if (!_fs) return;
  File f = _fs->open(MSNET_CFG_FILE, "r");
  if (!f) return;
  String s = f.readString();
  f.close();

  auto grab = [&](const char *key, char *out, size_t max) {
    String pat = String("\"") + key + "\":\"";
    int i = s.indexOf(pat);
    if (i < 0) return;                 // ontbreekt: ingebouwde waarde blijft
    i += pat.length();
    int j = s.indexOf('"', i);
    if (j < 0) return;
    String v = s.substring(i, j);
    strncpy(out, v.c_str(), max - 1);
    out[max - 1] = 0;
  };
  grab("ssid", _cfg.ssid, SSID_MAX);
  grab("pass", _cfg.pass, PASS_MAX);
  grab("ap_pass", _cfg.ap_pass, PASS_MAX);
  grab("user", _cfg.user, USER_MAX);
  grab("console_pass", _cfg.console_pass, PASS_MAX);
}

static void saveConfig() {
  if (!_fs) return;
  File f = _fs->open(MSNET_CFG_FILE, "w");
  if (!f) return;
  f.printf("{\"ssid\":\"%s\",\"pass\":\"%s\",\"ap_pass\":\"%s\","
           "\"user\":\"%s\",\"console_pass\":\"%s\"}",
           _cfg.ssid, _cfg.pass, _cfg.ap_pass, _cfg.user, _cfg.console_pass);
  f.close();
}

/* Bootteller: elke start hoogt hem op, en pas na STABLE_UPTIME_MS draaien
 * zetten we hem terug op nul. Drie starts zonder ooit stabiel te worden
 * betekent dat er iets structureel mis is — dan laten we alles wat niet
 * strikt nodig is uit. */
static void checkSafeMode() {
  if (!_fs) return;
  uint8_t count = 0;
  File f = _fs->open(MSNET_BOOT_FILE, "r");
  if (f) { count = f.read(); f.close(); }
  if (count > 200) count = 0;          // ongeldig, opnieuw beginnen

  _safe_mode = (count >= SAFE_MODE_BOOTS);
  _disabled = (count >= DISABLE_BOOTS);

  f = _fs->open(MSNET_BOOT_FILE, "w");
  if (f) { f.write((uint8_t)(count + 1)); f.close(); }
}

static void clearBootCount() {
  if (!_fs || _boot_cleared) return;
  File f = _fs->open(MSNET_BOOT_FILE, "w");
  if (f) { f.write((uint8_t)0); f.close(); }
  _boot_cleared = true;
}

// -------------------------------------------------------------------- wifi

static void startAP() {
  WiFi.mode(WIFI_AP_STA);              // AP blijft staan terwijl we STA blijven proberen
  WiFi.softAP(_ap_ssid, _cfg.ap_pass);
  _state = WIFI_FALLBACK_AP;
  _state_since = millis();
  _last_retry = millis();
  Serial.printf("MeshStatsNet: eigen netwerk '%s' actief op %s\n",
                _ap_ssid, WiFi.softAPIP().toString().c_str());
}

static void startSTA() {
  if (_cfg.ssid[0] == 0) { startAP(); return; }
  if (_state != WIFI_FALLBACK_AP) WiFi.mode(WIFI_STA);
  WiFi.begin(_cfg.ssid, _cfg.pass);
  if (_state != WIFI_FALLBACK_AP) {
    _state = WIFI_TRYING;
    _state_since = millis();
  }
  Serial.printf("MeshStatsNet: verbinden met '%s'...\n", _cfg.ssid);
}

static const char *stateName() {
  if (_state == WIFI_OK) return "verbonden";
  if (_state == WIFI_FALLBACK_AP) return "eigen netwerk (WiFi onbereikbaar)";
  return "verbinden...";
}

// ------------------------------------------------------------- beheerpagina

static const char PAGE[] PROGMEM =
  "<!doctype html><html lang=nl><head><meta charset=utf-8>"
  "<meta name=viewport content='width=device-width,initial-scale=1'>"
  "<title>MeshCore repeater</title><style>"
  "body{margin:0;background:#0b0f14;color:#d7e2ea;font:15px/1.5 system-ui,sans-serif}"
  "main{max-width:620px;margin:0 auto;padding:1rem 1.2rem 3rem}"
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
  "td:first-child{color:#7d8fa0;white-space:nowrap}"
  ".muted{color:#7d8fa0;font-size:.85rem}"
  "a{color:#35e08c}.warn{background:#3a2416;border-color:#7a4a1d}"
  "</style></head><body><main>"
  "<h1 id=nm>MeshCore repeater</h1><p class=muted id=sub>laden...</p>"
  "<div id=safe></div>"
  "<h2>Toestand</h2><div class=card><table id=st></table></div>"
  "<h2>WiFi</h2><div class=card><form id=f>"
  "<label>Netwerk (SSID)<input name=ssid></label>"
  "<label>Wachtwoord<input name=pass type=password placeholder='ongewijzigd'></label>"
  "<label>Wachtwoord van het eigen netwerk<input name=ap_pass type=password placeholder='ongewijzigd'></label>"
  "<button type=submit>Opslaan en verbinden</button></form>"
  "<p class=muted>Lukt verbinden niet, dan zendt de repeater zijn eigen netwerk uit "
  "en blijft hij het jouwe proberen. Via de mesh-CLI werkt <code>wifi</code> altijd.</p></div>"
  "<h2>Firmware</h2><div class=card>"
  "<p class=muted>Upgraden kan hier, over je gewone WiFi. Een afgebroken upload "
  "laat de huidige firmware staan.</p>"
  "<p><a href=/update>Firmware uploaden &rarr;</a></p></div>"
  "<h2>Back-up</h2><div class=card>"
  "<p class=muted>De back-up bevat alles uit het bestandssysteem: je sleutelpaar, "
  "de repeater-instellingen, de ACL en de netwerkinstellingen. Wie dit bestand "
  "heeft, heeft de identiteit van je node &mdash; bewaar het veilig.</p>"
  "<p><a href=/api/backup>Back-up downloaden &rarr;</a></p>"
  "<form id=r style='margin-top:.8rem'>"
  "<label>Back-up terugzetten<input type=file name=f accept='.mcb'></label>"
  "<button type=submit>Terugzetten en herstarten</button></form></div>"
  "<script>"
  "var $=function(s){return document.querySelector(s)};"
  "function load(){fetch('/api/status').then(function(r){return r.json()}).then(function(d){"
  "$('#nm').textContent=d.name;"
  "$('#sub').textContent='id '+d.node+' \\u00b7 '+d.board+' \\u00b7 '+d.fw;"
  "var h='';for(var k in d.status){h+='<tr><td>'+k+'</td><td>'+d.status[k]+'</td></tr>'}"
  "$('#st').innerHTML=h;"
  "$('#f').ssid.value=d.ssid;"
  "$('#safe').innerHTML=d.safe_mode?'<div class=\"card warn\">Veilige modus: de repeater "
  "is meermaals herstart, dus alle extra\\u2019s staan uit. Herstart hem opnieuw zodra "
  "je de oorzaak weg hebt.</div>':''})}"
  "$('#f').onsubmit=function(e){e.preventDefault();"
  "fetch('/api/wifi',{method:'POST',body:new URLSearchParams(new FormData($('#f')))})"
  ".then(function(){$('#f').pass.value='';$('#f').ap_pass.value='';"
  "alert('Opgeslagen. De repeater verbindt opnieuw; ververs deze pagina zo dadelijk.')})};"
  "$('#r').onsubmit=function(e){e.preventDefault();"
  "var f=$('#r').f.files[0];if(!f){alert('Kies eerst een back-upbestand.');return}"
  "if(!confirm('Alle instellingen en sleutels worden overschreven met '+f.name+'. Doorgaan?'))return;"
  "var d=new FormData();d.append('f',f);"
  "fetch('/api/restore',{method:'POST',body:d}).then(function(r){return r.json()})"
  ".then(function(j){alert(j.msg);})};"
  "load();setInterval(load,5000);"
  "</script></main></body></html>";

/* De beheerpagina geeft toegang tot je sleutels (backup) en tot het flashen van
 * firmware. Dat mag niet zonder login open staan op je netwerk. */
static bool requireAuth(AsyncWebServerRequest *req) {
  if (req->authenticate(_cfg.user, _cfg.console_pass)) return true;
  req->requestAuthentication();
  return false;
}

static void handleStatus(AsyncWebServerRequest *req) {
  if (!requireAuth(req)) return;
  char body[720];
  IPAddress ip = (_state == WIFI_FALLBACK_AP) ? WiFi.softAPIP() : WiFi.localIP();

  snprintf(body, sizeof(body),
    "{\"name\":\"%s\",\"node\":\"%s\",\"board\":\"%s\",\"fw\":\"%s\","
    "\"ssid\":\"%s\",\"safe_mode\":%d,"
    "\"status\":{\"WiFi\":\"%s\",\"IP\":\"%s\",\"Netwerk\":\"%s\","
    "\"Signaal\":\"%d dBm\",\"Uptime\":\"%lu min\",\"Vrij geheugen\":\"%u bytes\","
    "\"Batterij\":\"%.2f V\"}}",
    _mesh ? _mesh->getNodeName() : "repeater", _node_hex,
    board.getManufacturerName(), FIRMWARE_VERSION,
    _cfg.ssid, _safe_mode ? 1 : 0,
    stateName(), ip.toString().c_str(),
    _state == WIFI_FALLBACK_AP ? _ap_ssid : _cfg.ssid,
    (int)WiFi.RSSI(), (unsigned long)(millis() / 60000UL),
    (unsigned)ESP.getFreeHeap(), board.getBattMilliVolts() / 1000.0f);

  req->send(200, "application/json", body);
}

static void handleWifiPost(AsyncWebServerRequest *req) {
  if (!requireAuth(req)) return;
  if (req->hasParam("ssid", true)) {
    strncpy(_cfg.ssid, req->getParam("ssid", true)->value().c_str(), SSID_MAX - 1);
    _cfg.ssid[SSID_MAX - 1] = 0;
  }
  // Lege wachtwoordvelden betekenen "laat staan"; anders zou een bezoek aan de
  // pagina het wachtwoord wissen.
  if (req->hasParam("pass", true) && req->getParam("pass", true)->value().length() > 0) {
    strncpy(_cfg.pass, req->getParam("pass", true)->value().c_str(), PASS_MAX - 1);
    _cfg.pass[PASS_MAX - 1] = 0;
  }
  if (req->hasParam("ap_pass", true) && req->getParam("ap_pass", true)->value().length() >= 8) {
    strncpy(_cfg.ap_pass, req->getParam("ap_pass", true)->value().c_str(), PASS_MAX - 1);
    _cfg.ap_pass[PASS_MAX - 1] = 0;
  }
  _apply_wifi = true;      // opslaan en herverbinden gebeurt in loop()
  req->send(200, "application/json", "{\"ok\":1}");
}

// ------------------------------------------------------------ backup/restore

/* Formaat, bewust regelgebaseerd zodat we nooit een heel bestand in het
 * geheugen moeten houden:
 *
 *   MESHSTATS-BACKUP 1
 *   FILE /identity 64
 *   <hex, 64 bytes per regel>
 *   ...
 *   END
 *
 * Hierin zit alles uit het bestandssysteem: je sleutelpaar, de repeater-prefs,
 * de ACL en de netwerkinstellingen. Bewaar zo'n backup veilig — wie hem heeft,
 * heeft de identiteit van je node.
 */
#define BACKUP_FILE   "/backup.mcb"
#define RESTORE_FILE  "/restore.mcb"
#define HEX_PER_LINE  64

static bool skipInBackup(const char *name) {
  return strcmp(name, BACKUP_FILE) == 0 || strcmp(name, RESTORE_FILE) == 0 ||
         strcmp(name, MSNET_BOOT_FILE) == 0;
}

static bool writeBackupFile() {
  if (!_fs) return false;
  File out = _fs->open(BACKUP_FILE, "w");
  if (!out) return false;

  out.print("MESHSTATS-BACKUP 1\n");

  File dir = _fs->open("/");
  File f = dir.openNextFile();
  uint8_t buf[HEX_PER_LINE];
  char hex[HEX_PER_LINE * 2 + 2];

  while (f) {
    // f.name() geeft naargelang de core-versie met of zonder leidende slash terug
    String path = f.name();
    if (!path.startsWith("/")) path = "/" + path;

    if (!f.isDirectory() && !skipInBackup(path.c_str())) {
      out.printf("FILE %s %u\n", path.c_str(), (unsigned)f.size());
      int n;
      while ((n = f.read(buf, sizeof(buf))) > 0) {
        int p = 0;
        for (int i = 0; i < n; i++) {
          static const char H[] = "0123456789abcdef";
          hex[p++] = H[buf[i] >> 4];
          hex[p++] = H[buf[i] & 0x0F];
        }
        hex[p++] = '\n';
        hex[p] = 0;
        out.print(hex);
      }
    }
    f = dir.openNextFile();
  }
  out.print("END\n");
  out.close();
  return true;
}

static void handleBackup(AsyncWebServerRequest *req) {
  if (!requireAuth(req)) return;
  if (!writeBackupFile()) {
    req->send(500, "text/plain", "backup mislukt");
    return;
  }
  char fname[64];
  snprintf(fname, sizeof(fname), "meshcore-%s.mcb", _node_hex);

  AsyncWebServerResponse *res = req->beginResponse(*_fs, BACKUP_FILE, "application/octet-stream");
  res->addHeader("Content-Disposition", String("attachment; filename=\"") + fname + "\"");
  req->send(res);
}

static uint8_t hexVal(char c) {
  if (c >= '0' && c <= '9') return c - '0';
  if (c >= 'a' && c <= 'f') return c - 'a' + 10;
  if (c >= 'A' && c <= 'F') return c - 'A' + 10;
  return 0xFF;
}

/* Leest het geüploade bestand regel per regel terug en schrijft de bestanden
 * weg. Pas als alles gelukt is herstarten we; gaat er iets mis, dan staat de
 * node nog altijd zoals hij stond. */
static bool applyRestore(char *err, size_t err_max) {
  File in = _fs->open(RESTORE_FILE, "r");
  if (!in) { snprintf(err, err_max, "geen bestand ontvangen"); return false; }

  char line[HEX_PER_LINE * 2 + 8];
  size_t len = in.readBytesUntil('\n', line, sizeof(line) - 1);
  line[len] = 0;
  if (strncmp(line, "MESHSTATS-BACKUP", 16) != 0) {
    in.close();
    snprintf(err, err_max, "dit is geen backupbestand");
    return false;
  }

  File out;
  uint32_t remaining = 0;
  int files = 0;
  uint8_t buf[HEX_PER_LINE];

  while (in.available()) {
    len = in.readBytesUntil('\n', line, sizeof(line) - 1);
    line[len] = 0;
    if (len && line[len - 1] == '\r') line[--len] = 0;
    if (len == 0) continue;

    if (strncmp(line, "FILE ", 5) == 0) {
      if (out) out.close();
      char *sp = strrchr(line + 5, ' ');
      if (!sp) { in.close(); snprintf(err, err_max, "onleesbare regel"); return false; }
      *sp = 0;
      remaining = (uint32_t)atol(sp + 1);
      out = _fs->open(line + 5, "w");
      if (!out) { in.close(); snprintf(err, err_max, "kan %s niet schrijven", line + 5); return false; }
      files++;
    } else if (strncmp(line, "END", 3) == 0) {
      break;
    } else if (out) {
      size_t n = len / 2;
      if (n > sizeof(buf)) n = sizeof(buf);
      for (size_t i = 0; i < n; i++) {
        uint8_t hi = hexVal(line[i * 2]), lo = hexVal(line[i * 2 + 1]);
        if (hi == 0xFF || lo == 0xFF) { in.close(); out.close();
          snprintf(err, err_max, "beschadigde inhoud"); return false; }
        buf[i] = (hi << 4) | lo;
      }
      if (n > remaining) n = remaining;
      out.write(buf, n);
      remaining -= n;
    }
  }
  if (out) out.close();
  in.close();
  _fs->remove(RESTORE_FILE);

  if (files == 0) { snprintf(err, err_max, "backup bevatte geen bestanden"); return false; }
  snprintf(err, err_max, "%d bestanden teruggezet", files);
  return true;
}

static volatile bool _reboot_pending = false;
static unsigned long _reboot_at = 0;

// ----------------------------------------------------------------- console

static void consolePrompt() {
  if (_con_state == CON_USER) _client.print("gebruiker: ");
  else if (_con_state == CON_PASS) _client.print("wachtwoord: ");
  else _client.print("> ");
}

static void consoleWelcome() {
  _client.printf("\r\nMeshCore repeater %s (%s)\r\n",
                 _mesh ? _mesh->getNodeName() : "", board.getManufacturerName());
  _client.print("Log in om de CLI te gebruiken.\r\n");
  _con_state = CON_USER;
  _con_len = 0;
  _con_tries = 0;
  _con_active = millis();
  consolePrompt();
}

static void consoleHandleLine() {
  _con_line[_con_len] = 0;

  if (_con_state == CON_USER) {
    _con_state = (strcmp(_con_line, _cfg.user) == 0) ? CON_PASS : CON_USER;
    if (_con_state == CON_USER) _client.print("onbekende gebruiker\r\n");
  } else if (_con_state == CON_PASS) {
    if (strcmp(_con_line, _cfg.console_pass) == 0) {
      _con_state = CON_READY;
      _client.print("\r\nWelkom. 'help' voor de MeshCore-commando's, "
                    "'wifi' voor de netwerkinstellingen, 'quit' om af te sluiten.\r\n");
    } else {
      _con_state = CON_USER;
      if (++_con_tries >= 3) {
        _client.print("te veel pogingen\r\n");
        _client.stop();
        _con_len = 0;
        return;
      }
      _client.print("onjuist wachtwoord\r\n");
    }
  } else if (_con_line[0]) {
    if (strcmp(_con_line, "quit") == 0 || strcmp(_con_line, "exit") == 0) {
      _client.print("tot ziens\r\n");
      _client.stop();
      _con_len = 0;
      return;
    }
    char reply[160];
    reply[0] = 0;
    if (!msnet_handle_command(_con_line, reply) && _mesh) {
      _mesh->handleCommand(0, _con_line, reply);
    }
    if (reply[0]) { _client.print(reply); _client.print("\r\n"); }
  }

  _con_len = 0;
  consolePrompt();
}

static void consoleLoop() {
  // Stille sessie zelf opruimen, ook als de tegenpartij nooit netjes afsloot.
  if (_client && _client.connected() && millis() - _con_active > CON_IDLE_MS) {
    _client.stop();
  }

  if (_console.hasClient()) {
    WiFiClient fresh = _console.available();
    bool busy = _client && _client.connected() &&
                (millis() - _con_active < CON_TAKEOVER_MS);
    if (busy) {
      fresh.print("Er is al een sessie actief.\r\n");
      fresh.stop();
    } else {
      if (_client) _client.stop();      // eventuele oude sessie loslaten
      _client = fresh;
      consoleWelcome();
    }
  }
  if (!_client || !_client.connected()) return;

  while (_client.available()) {
    char c = _client.read();
    _con_active = millis();
    if (c == '\n' || c == '\r') {
      if (_con_len > 0 || c == '\r') consoleHandleLine();
    } else if (c >= 32 && _con_len < sizeof(_con_line) - 1) {
      _con_line[_con_len++] = c;
    }
  }
}

// ------------------------------------------------------------------ publiek

bool msnet_is_safe_mode() { return _safe_mode; }

bool msnet_handle_command(const char *command, char *reply) {
  if (_disabled) return false;   // laat alles aan de standaardfirmware over

  if (memcmp(command, "wifi", 4) != 0) {
    // 'start ota' zou een tweede webserver op poort 80 openen; onze eigen
    // uploadpagina staat er al, dus we verwijzen ernaar.
    if (memcmp(command, "start ota", 9) == 0) {
      IPAddress ip = (_state == WIFI_FALLBACK_AP) ? WiFi.softAPIP() : WiFi.localIP();
      sprintf(reply, "Altijd actief: http://%s/update", ip.toString().c_str());
      return true;
    }
    return false;
  }

  const char *arg = command + 4;
  while (*arg == ' ') arg++;

  if (*arg == 0) {
    IPAddress ip = (_state == WIFI_FALLBACK_AP) ? WiFi.softAPIP() : WiFi.localIP();
    sprintf(reply, "%s, ssid=%s, ip=%s, rssi=%d",
            stateName(), _state == WIFI_FALLBACK_AP ? _ap_ssid : _cfg.ssid,
            ip.toString().c_str(), (int)WiFi.RSSI());
  } else if (memcmp(arg, "ssid ", 5) == 0) {
    strncpy(_cfg.ssid, arg + 5, SSID_MAX - 1);
    _cfg.ssid[SSID_MAX - 1] = 0;
    saveConfig();
    sprintf(reply, "OK - ssid=%s ('wifi connect' om te verbinden)", _cfg.ssid);
  } else if (memcmp(arg, "pass ", 5) == 0) {
    strncpy(_cfg.pass, arg + 5, PASS_MAX - 1);
    _cfg.pass[PASS_MAX - 1] = 0;
    saveConfig();
    strcpy(reply, "OK - wachtwoord opgeslagen ('wifi connect' om te verbinden)");
  } else if (memcmp(arg, "connect", 7) == 0) {
    _state = WIFI_TRYING;
    startSTA();
    strcpy(reply, "OK - verbinden...");
  } else if (memcmp(arg, "ap", 2) == 0) {
    startAP();
    sprintf(reply, "OK - eigen netwerk '%s' actief", _ap_ssid);
  } else if (memcmp(arg, "console ", 8) == 0) {
    char u[USER_MAX], p[PASS_MAX];
    if (sscanf(arg + 8, "%16s %64s", u, p) == 2) {
      strcpy(_cfg.user, u);
      strcpy(_cfg.console_pass, p);
      saveConfig();
      strcpy(reply, "OK - console-login gewijzigd");
    } else {
      strcpy(reply, "Err - gebruik: wifi console <gebruiker> <wachtwoord>");
    }
  } else {
    strcpy(reply, "Err - wifi [ssid|pass|connect|ap|console]");
  }
  return true;
}

void msnet_begin(FS &fs, MyMesh *mesh) {
  _fs = &fs;
  _mesh = mesh;

  checkSafeMode();

  if (_disabled) {
    // Zelfs de veilige modus hield het niet. Alles van ons blijft uit; er
    // blijft een gewone MeshCore-repeater over, met mesh-CLI en 'start ota'.
    Serial.println("MeshStatsNet: uitgeschakeld na herhaalde herstarts");
    _started = true;      // enkel nog om de bootteller te kunnen wissen
    return;
  }

  loadConfig();

  if (_mesh) mesh::Utils::toHex(_node_hex, _mesh->self_id.pub_key, 6);
  snprintf(_ap_ssid, sizeof(_ap_ssid), "MeshCore-%s", _node_hex);

  if (_safe_mode) {
    // Iets liet deze node herhaaldelijk herstarten. Enkel het eigen netwerk en
    // de beheerpagina, zodat je erbij kan om het recht te zetten.
    Serial.println("MeshStatsNet: VEILIGE MODUS na herhaalde herstarts");
    startAP();
  } else {
    startSTA();
  }

  _server.on("/", HTTP_GET, [](AsyncWebServerRequest *req) {
    req->send(200, "text/html; charset=utf-8", PAGE);
  });
  _server.on("/api/status", HTTP_GET, handleStatus);
  _server.on("/api/wifi", HTTP_POST, handleWifiPost);
  _server.on("/api/backup", HTTP_GET, handleBackup);

  _server.on("/api/restore", HTTP_POST,
    [](AsyncWebServerRequest *req) {                       // upload is binnen
      char msg[80];
      bool ok = applyRestore(msg, sizeof(msg));
      char body[128];
      snprintf(body, sizeof(body), "{\"ok\":%d,\"msg\":\"%s\"}", ok ? 1 : 0, msg);
      req->send(ok ? 200 : 400, "application/json", body);
      if (ok) {                                            // herstart met de teruggezette gegevens
        _reboot_pending = true;
        _reboot_at = millis() + 1500;
      }
    },
    [](AsyncWebServerRequest *req, const String &filename, size_t index,
       uint8_t *data, size_t len, bool final) {            // stukje per stukje wegschrijven
      static File up;
      if (index == 0) {
        if (!requireAuth(req)) return;
        up = _fs->open(RESTORE_FILE, "w");
      }
      if (up) up.write(data, len);
      if (final && up) up.close();
    });

  // Ook de firmware-upload achter dezelfde login: wie hier binnen kan, kan
  // firmware schrijven en je sleutels downloaden.
  AsyncElegantOTA.begin(&_server, _cfg.user, _cfg.console_pass);
  _server.begin();
  _console.begin();
  _console.setNoDelay(true);

  _started = true;
}

void msnet_loop() {
  if (!_started) return;

  // Lang genoeg overeind: deze firmware werkt, bootteller mag terug op nul.
  // Ook in uitgeschakelde toestand, zodat de volgende start weer alles probeert.
  if (!_boot_cleared && millis() > STABLE_UPTIME_MS) clearBootCount();
  if (_disabled) return;

  // Na een restore even wachten zodat het antwoord de browser nog haalt.
  if (_reboot_pending && millis() > _reboot_at) ESP.restart();

  if (_apply_wifi) {
    _apply_wifi = false;
    saveConfig();
    _state = WIFI_TRYING;
    startSTA();
  }

  bool up = (WiFi.status() == WL_CONNECTED);

  switch (_state) {
    case WIFI_TRYING:
      if (up) {
        _state = WIFI_OK;
        _state_since = millis();
        Serial.printf("MeshStatsNet: verbonden, http://%s/\n",
                      WiFi.localIP().toString().c_str());
      } else if (millis() - _state_since > STA_TIMEOUT_MS) {
        startAP();
      }
      break;

    case WIFI_OK:
      if (!up) {                       // verbinding kwijt: opnieuw proberen
        _state = WIFI_TRYING;
        _state_since = millis();
        startSTA();
      }
      break;

    case WIFI_FALLBACK_AP:
      if (up) {                        // netwerk is terug
        _state = WIFI_OK;
        _state_since = millis();
        WiFi.softAPdisconnect(true);
        WiFi.mode(WIFI_STA);
        Serial.printf("MeshStatsNet: netwerk terug, http://%s/\n",
                      WiFi.localIP().toString().c_str());
      } else if (millis() - _last_retry > STA_RETRY_MS) {
        _last_retry = millis();
        startSTA();                    // AP blijft intussen gewoon staan
      }
      break;
  }

  if (!_safe_mode) consoleLoop();
}
