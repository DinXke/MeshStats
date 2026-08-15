#pragma once

/* MeshStatsNet -- gives a repeater an IP life next to its mesh life: WiFi, an
 * admin page, firmware upgrades, a console and MQTT publishing.
 *
 * Two topics outbound, and only two, because those are the ones the receiving
 * side subscribes to (<prefix>/+/stats and <prefix>/+/rx). Inventing a third
 * leaf to publish on buys you a publish() that reports success and a broker
 * that drops the message unread -- which is exactly what happened to monitored
 * repeaters before 1.3.0.
 *
 *   <prefix>/<node>/stats   statistics, neighbour list included. Usually about
 *                           this node itself, but the same topic also relays
 *                           readings from OTHER repeaters this node polls: the
 *                           topic still names us, while repeater.pubkey_prefix
 *                           in the payload names the subject, and the far end
 *                           records that difference as source_prefix.
 *   <prefix>/<node>/rx      every received packet, raw and complete (hex)
 *
 * And since 1.8.0 one topic inbound. The rule above is about publishing and
 * says nothing about the other direction: a topic we subscribe to only has to
 * be a topic the sender publishes on, and the site does exactly that.
 *
 *   <prefix>/<node>/cmd     a single word asking this node to do something
 *                           now: 'settings' (read the CLI parameters and send
 *                           them along with the next statistics message) or
 *                           'status' (publish that message immediately).
 *
 * Deliberately NOT a remote CLI. Anything outside those two words is refused
 * and counted, so a broker account -- shared, leaked or simply mistyped --
 * cannot reach 'reboot', 'set', or the wifi commands. This node hangs on a
 * roof; the only thing an attacker on the broker can make it do is publish
 * what it already publishes by itself, at most once every 30 seconds.
 *
 * About monitoring other repeaters. You can log in with that repeater's admin
 * or read/write password, but there is a tidier way that needs no password at
 * all: a blank password makes the far side skip the password check and look
 * your public key up in its access list instead (see handleLoginReq() in
 * MyMesh.cpp). Its operator adds you once with
 *
 *     setperm <your-pubkey-hex> 1
 *
 * where 1 is read-only, 2 read/write and 3 admin -- read-only is enough for
 * status polling. Nobody has to hand out a password, and access can be revoked
 * on their side alone.
 *
 * One thing that cannot be seen from here: a refused login produces no reply at
 * all, exactly like a repeater that is out of range. So 'no answer' means
 * either your key is not in their list yet, or you simply cannot reach them --
 * the heard list on the admin page is what tells the two apart.
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
#define MESHSTATS_VERSION  "1.8.0"

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

/* Called from the receive path when a repeater we monitor answers a login or a
 * status request, or to a CLI command we sent it. Same rule as above: copy
 * only, interpret later. mon_idx indexes MyMesh's monitor table, and type
 * distinguishes a RESPONSE (status/telemetry/neighbours) from a TXT_MSG (a CLI
 * answer). */
void meshstats_on_monitor_response(int mon_idx, uint8_t type, const uint8_t *data, int len);

/* Called for every advert this node hears. Keeps a small cache of who is out
 * there -- key, name, type, when last heard, and coordinates when the advert
 * carries them -- persisted to the file system so names survive a restart.
 * Without it a reboot leaves the monitor list and the heard list showing bare
 * hex keys until the next advert, and those can be hours apart.
 *
 * Copies into RAM only. The file is written lazily from msnet_loop(), never
 * from here: adverts arrive in bursts on a busy mesh, and SPIFFS wears out. */
void meshstats_on_advert(const uint8_t *pub_key, const char *name, uint8_t type,
                         bool has_latlon, int32_t lat, int32_t lon);

/* Name last heard for this public key, or NULL when we have never heard it.
 * prefix_len is in bytes, so a partial key works. */
const char *meshstats_advert_name(const uint8_t *pub_key, int prefix_len);

/* Battery percentage from cell millivolts. Lives here so the admin page, the
 * power management and the published statistics all quote the same number;
 * two curves that disagree by a few percent is a bug report waiting to happen.
 * Returns -1 when the board reports no usable voltage. */
int meshstats_batt_percent(uint16_t milli_volts);

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
 *   wifi mqtt ...        broker settings (see the mqtt sub-help). Its status
 *                        line also counts the commands received on the cmd
 *                        topic and the ones refused, so a site that thinks it
 *                        is asking and a node that never hears it can be told
 *                        apart from either end.
 *   wifi power ...       power management (see the power sub-help)
 *   wifi mon ...         repeaters to monitor (see the mon sub-help). An empty
 *                        password there is a choice, not an omission: it means
 *                        'get in via their access list'.
 */
bool msnet_handle_command(const char *command, char *reply);

// True when the node runs in safe mode (after repeated restarts).
bool msnet_is_safe_mode();
