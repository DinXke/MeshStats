#pragma once

/* MeshStatsNet -- gives a repeater an IP life next to its mesh life: WiFi, an
 * admin page, firmware upgrades, a console and MQTT publishing.
 *
 * Two kinds of MQTT messages:
 *   <prefix>/<node>/stats   this node's own statistics, periodically (JSON)
 *   <prefix>/<node>/rx      every received packet, raw and complete (hex)
 *
 * The node deliberately does not parse those packets: it forwards the bytes
 * exactly as they came off the air. That saves memory here, and the receiving
 * side can learn to extract more from them without a firmware update.
 *
 * The guiding assumption for everything below: this repeater hangs on a roof
 * and runs off a solar panel. It may never become unreachable, and it may
 * never spend more energy than the panel brings in. Hence:
 *
 *  - WiFi fails?              it broadcasts its own SSID with the same admin
 *                             page, and keeps retrying your network
 *                             (self-healing)
 *  - admin page broken?       the mesh CLI keeps working (wifi commands)
 *  - my code crashes?         a boot counter starts it in safe mode after 3
 *                             restarts: mesh + AP + page, nothing else
 *  - my code hangs?           a task watchdog turns a blocked loop() into a
 *                             restart, so the boot counter above can see it.
 *                             A hang answers pings while serving nothing, and
 *                             would otherwise never restart at all
 *  - upload in progress?      the watchdog steps aside while flash is being
 *                             written, so it can never abort an OTA
 *  - radio init fails?        we do not hang forever like the stock firmware,
 *                             but start the network side anyway so you can
 *                             reflash
 *  - upgrading?               /update on the normal admin page, so over your
 *                             own WiFi and not only via the OTA soft-AP
 *  - battery running down?    the publish interval follows the cell level (and
 *                             the clock, if it happens to be valid), and in
 *                             power-save mode WiFi is off most of the time --
 *                             radio silence saves far more than a slower
 *                             interval ever will
 *  - node asleep and you
 *    need it now?             'wifi on <minutes>' over the mesh CLI brings it
 *                             up immediately and holds it there, whatever the
 *                             battery says
 *
 * The web server is asynchronous (AsyncWebServer). A blocking server stalls the
 * main loop and with it the mesh -- behaviour we already saw on the companion
 * node.
 */

#include <Arduino.h>
#include <FS.h>

/* Version of THIS module, not of MeshCore. The two move independently: this
 * firmware tracks upstream MeshCore releases, while everything in MeshStatsNet
 * has its own semantic version. 'ver' prints both, because when something is
 * wrong the first question is which of the two you are looking at. */
#define MESHSTATS_NAME     "MeshStats (by DinX)"
#define MESHSTATS_VERSION  "1.1.0"

class MyMesh;

// Call from setup(), after the file system and the mesh are up.
void msnet_begin(FS &fs, MyMesh *mesh);

// Call from loop(). Never does anything that blocks for long.
void msnet_loop();

/* Called from the receive path for every packet that comes in. Only copies
 * into a ring buffer -- publishing happens later, in msnet_loop(). A full
 * queue drops the packet and counts it; waiting here would hold up reception.
 * Safe to call before msnet_begin() or when the module is disabled. */
void meshstats_on_raw_packet(float snr, float rssi, const uint8_t raw[], int len);

/* Intercepts the wifi commands. Returns true if the command was ours.
 * Called from the serial CLI, the mesh CLI and the telnet console alike, so a
 * broken WiFi configuration can still be fixed over the mesh:
 *
 *   ver                  version of this module plus the MeshCore version it
 *                        is built on. No MeshStats name in the answer means
 *                        this module is not running.
 *   wifi                 state, IP, signal, battery, publish interval
 *   wifi ssid <name>     set the network (empty = the compiled-in default)
 *   wifi pass <word>     set the password
 *   wifi connect         reconnect using the stored credentials
 *   wifi ap              broadcast our own network now
 *   wifi on [minutes]    force WiFi up and hold it there (default 30 min).
 *                        Works in power-save mode and at any battery level:
 *                        this is the way back in when the node is asleep.
 *   wifi off             back to automatic power management
 *   wifi console <user> <pass>   console credentials
 *   wifi mqtt ...        broker settings (see the mqtt sub-help)
 *   wifi power ...       power management (see the power sub-help)
 */
bool msnet_handle_command(const char *command, char *reply);

// True when the node runs in safe mode (after repeated restarts).
bool msnet_is_safe_mode();
