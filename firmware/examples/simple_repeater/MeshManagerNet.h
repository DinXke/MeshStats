#pragma once

/* MeshManagerNet -- gives a repeater an IP life next to its mesh life: WiFi, an
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
 *   <prefix>/<node>/cmd     one word asking this node to do something now:
 *                           'settings' (read our own CLI parameters and send
 *                           them along with the next statistics message),
 *                           'status' (publish that message immediately), or
 *                           since 1.9.0 'settings <sleutel>' -- fetch the CLI
 *                           settings of a repeater we MONITOR, over LoRa, and
 *                           publish those under that repeater's name. That
 *                           last one is the only path to a repeater which does
 *                           not publish to MQTT itself.
 *                           Since 1.10.0 also 'time <epoch>': set our own clock
 *                           to that UNIX time in UTC seconds, and then check
 *                           the clocks of the repeaters we monitor over LoRa.
 *                           Since 2.8.0 also 'set <param> <waarde>': set one of
 *                           OUR OWN CLI parameters, from the compiled-in
 *                           CFG_PARAMS table, and report the read-back with the
 *                           next statistics message.
 *
 * Deliberately NOT a remote CLI. The word is still matched against a list of
 * exactly four, so a broker account -- shared, leaked or simply mistyped --
 * cannot reach 'reboot' or the wifi commands. The arguments that exist do not
 * change that. The one on 'settings' is never text that reaches a
 * CLI, it only selects one entry from the monitor list, and that list is
 * writable solely from the admin page and the mesh CLI, both of which ask for a
 * password. The commands actually sent are the compiled-in parameter table. The
 * one on 'time' is parsed here as a number, checked against a window of years,
 * and never reaches a CLI either.
 *
 * 'set' is the one that genuinely raises the ceiling of this topic, so it is
 * worth being exact about how far. The parameter name is looked up in
 * CFG_PARAMS and the command is then built from the table's own key, so no text
 * from the message becomes a command; the value passes cfgCheckValue(), the same
 * sieve as the two HTTP write paths; and the parameter's risk class must not
 * exceed CFG_MQTT_MAX_RISK, which stands at 'changes behaviour noticeably' and
 * not at 'can cut this node off'. That last line is where this channel differs
 * from the other two on purpose: they have an authenticated counterparty (this
 * node's web login, or a monitor's), this one has whoever the broker let in --
 * on a broker with one shared account, every node that speaks to it. So the
 * settings you adjust on an ordinary day go through here, and the handful that
 * can take a node off the air keep their two authenticated roads.
 *
 * Radio parameters do not appear on this road either, and for a reason that has
 * nothing to do with the channel: since 2.6.0 'radio' is not in CFG_PARAMS at
 * all, so no remote path offers it. A wrong 'tx' leaves a node reachable and
 * reversible; a wrong frequency, spreading factor, coding rate or bandwidth does
 * not. See the note where that row used to stand.
 *
 * This node hangs on a roof; the most an
 * attacker on the broker can make it do is publish what it already publishes by
 * itself, read out a repeater its operator already chose to monitor, move
 * this node's clock forward inside that window, or set one of the reversible
 * halves of its own parameter table -- at most once every 30
 * seconds, and for the two that cost airtime at most once every ten minutes
 * resp. once an hour.
 *
 * On 'time' and why it only ever moves clocks FORWARD, here and on the far
 * side: an advert carries the emitting node's clock, and every node that
 * already knows us refuses an advert whose timestamp is not higher than the one
 * it stored (onAdvertRecv in MyMesh.cpp). Step this node's clock back by an
 * hour and its adverts are ignored by the whole mesh for an hour -- a roof
 * repeater made invisible by a maintenance command, which is the one thing this
 * firmware may not do. MeshCore's own 'time' and 'clock sync' refuse to go
 * backwards for what is probably the same reason; this module refuses for that
 * one.
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
 * Read-only is NOT enough for the settings sweep of 1.9.0, and this is the one
 * place where that distinction bites. A repeater only runs a CLI command for a
 * client it considers an admin (handleCommand is reached from onPeerDataRecv
 * only under client->isAdmin()), and it says nothing at all to one it does
 * not. So a read-only monitor logs in perfectly, sends eighteen commands and
 * hears eighteen silences -- which looks exactly like a node that is out of
 * range. Hence 'setperm <your-pubkey-hex> 3', or the admin password, if the
 * settings of that repeater are supposed to be readable here. The sweep
 * publishes its silences rather than hiding them precisely so this is
 * diagnosable from the site instead of from a serial cable.
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
 *  - upgrading?               POST /api/fw, which verifies the image before it
 *                             switches the boot partition and only restarts
 *                             when that succeeded. /update is still there,
 *                             unchanged, as the fallback for when this path is
 *                             the thing that broke
 *  - upgrade went wrong?      the previous image is still in the other
 *                             application partition, and POST /api/fw/rollback
 *                             or 'wifi fw rollback' boots it again. The mesh
 *                             CLI form is the one that survives an upgrade
 *                             which lost the WiFi
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

/* Included from here rather than from MyMesh.cpp on purpose. The packet filter
 * is part of this module's surface, and MyMesh.cpp already includes this header
 * behind the same MESHMANAGER_NET guard -- so the forwarding hook reaches
 * pf_allow() without repeater-hooks.patch having to touch the include block.
 * One less hunk in a patch that has to keep applying to somebody else's tree. */
#include "PacketFilter.h"

/* Version of THIS module, not of MeshCore. The two move independently: this
 * firmware tracks upstream MeshCore releases, while everything in MeshManagerNet
 * has its own semantic version. 'ver' prints both, because when something is
 * wrong the first question is which of the two you are looking at. */
/* De naam hieronder is niet enkel opsmuk: hij is het antwoord op "is deze
 * node al om?". Hij komt uit 'ver', staat op de beheerpagina van de node
 * zelf, en het hoofdgetal 2 is wat de site in fw_meshmanager binnenkrijgt.
 * Zo is de overgang na het flashen af te lezen in plaats van te moeten
 * geloven. */
#define MESHMANAGER_NAME     "MeshManager (by DinX)"
#define MESHMANAGER_VERSION  "2.8.1"

class MyMesh;

// Call from setup(), after the file system and the mesh are up.
void mmnet_begin(FS &fs, MyMesh *mesh);

// Call from loop(). Never does anything that blocks for long.
void mmnet_loop();

/* Called from the receive path for every packet that comes in. Only copies
 * into a ring buffer -- publishing happens later, in mmnet_loop(). A full
 * queue drops the packet and counts it; waiting here would hold up reception.
 * Safe to call before mmnet_begin() or when the module is disabled. */
void meshmanager_on_raw_packet(float snr, float rssi, const uint8_t raw[], int len);

/* Het oordeel van het pakketfilter, gekoppeld aan het pakket waar het over gaat.
 * Aangeroepen vanuit MyMesh::allowPacketForward(), meteen na pf_allow().
 *
 * Het pakket wacht op dat moment nog in de rx-ring op publicatie, dus het
 * oordeel haalt het in en reist mee in hetzelfde rx-bericht: geen tweede
 * bericht, geen volgordeprobleem, en geen oordeel over een pakket dat de server
 * nooit gezien heeft.
 *
 * De koppeling gaat op INHOUD en niet op positie: 'payload' is de staart van het
 * frame dat wij bewaard hebben (docs/protocol.md §1.1), dus een memcmp op die
 * staart wijst het pakket exact aan. Koppelen op positie ging stil mis zodra er
 * twee pakketten tegelijk onderweg waren -- zie de toelichting bij de functie.
 *
 * Veilig aan te roepen als het doorsturen van pakketten uitstaat of de module
 * niet draait: dan valt er eenvoudigweg niets te stempelen. 'reason' is een
 * PfReason en telt alleen als 'allowed' onwaar is. */
void meshmanager_on_forward_verdict(bool allowed, uint8_t reason,
                                    const uint8_t *payload, int payload_len);

/* Called from the receive path when a repeater we monitor answers a login or a
 * status request, or to a CLI command we sent it. Same rule as above: copy
 * only, interpret later. mon_idx indexes MyMesh's monitor table, and type
 * distinguishes a RESPONSE (status/telemetry/neighbours) from a TXT_MSG (a CLI
 * answer). */
void meshmanager_on_monitor_response(int mon_idx, uint8_t type, const uint8_t *data, int len);

/* Called for every advert this node hears. Keeps a small cache of who is out
 * there -- key, name, type, when last heard, and coordinates when the advert
 * carries them -- persisted to the file system so names survive a restart.
 * Without it a reboot leaves the monitor list and the heard list showing bare
 * hex keys until the next advert, and those can be hours apart.
 *
 * Copies into RAM only. The file is written lazily from mmnet_loop(), never
 * from here: adverts arrive in bursts on a busy mesh, and SPIFFS wears out. */
void meshmanager_on_advert(const uint8_t *pub_key, const char *name, uint8_t type,
                         bool has_latlon, int32_t lat, int32_t lon);

/* Name last heard for this public key, or NULL when we have never heard it.
 * prefix_len is in bytes, so a partial key works. */
const char *meshmanager_advert_name(const uint8_t *pub_key, int prefix_len);

/* Battery percentage from cell millivolts. Lives here so the admin page, the
 * power management and the published statistics all quote the same number;
 * two curves that disagree by a few percent is a bug report waiting to happen.
 * Returns -1 when the board reports no usable voltage. */
int meshmanager_batt_percent(uint16_t milli_volts);

/* Intercepts the wifi commands. Returns true if the command was ours.
 * Called from the serial CLI, the mesh CLI and the telnet console alike, so a
 * broken WiFi configuration can still be fixed over the mesh:
 *
 *   ver                  version of this module plus the MeshCore version it
 *                        is built on. No MeshManager name in the answer means
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
 *                        'get in via their access list'. 'wifi mon settings
 *                        <hex>' starts the LoRa settings sweep of one of them
 *                        and reports on the previous one; together with 'wifi
 *                        mon trace' that is how a sweep which fails silently
 *                        (see the admin-rights note above) gets diagnosed
 *                        without a serial cable. Since 2.4.0 'wifi mon set
 *                        <hex> <param> <waarde>' does the same thing the other
 *                        way round: it writes ONE CLI setting to a monitored
 *                        repeater over LoRa and then reads that parameter back,
 *                        so what is reported is what stands in the node rather
 *                        than what it answered. 'wifi mon set' on its own shows
 *                        how the last one ended.
 *   wifi fw              which version runs from which application partition,
 *                        which build environment this image was compiled for,
 *                        what the other partition holds and how the last upload
 *                        ended.
 *   wifi fw rollback     boot the other partition again -- the firmware from
 *                        before the last upgrade, still in flash because an OTA
 *                        never erases the slot it is not writing. Over the mesh
 *                        on purpose: an upgrade whose only fault is that it
 *                        cannot join the WiFi takes every IP route into this
 *                        node with it, and LoRa is up before any of them.
 *   filter ...           the packet filter: which forwarded packets this node
 *                        still relays. Off by default. 'filter' alone reports
 *                        the state and what has been dropped; 'filter off' and
 *                        'filter reset' are the way back. The rules and their
 *                        limits are documented in PacketFilter.h.
 *   wifi clock           our own clock, when the site last set it, and what the
 *                        last check of the monitored nodes' clocks found.
 *                        Read-only on purpose: there is no way to type a time
 *                        in here, because the whole point of the feature is
 *                        that the time comes from a machine which has a reason
 *                        to know what time it is. A person at a serial cable
 *                        does not, and neither does this node.
 */
bool mmnet_handle_command(const char *command, char *reply);

/* The 'filter ...' commands are intercepted by the same function -- the packet
 * filter that decides which of OTHER people's packets this repeater still
 * forwards. It lives in PacketFilter.{h,cpp}; this note is here because the CLI
 * list above is where you go looking for it.
 *
 * Reachable over the mesh CLI like everything else here, and that is the whole
 * safety story: a filter makes a node useless without making it unreachable, so
 * 'filter off' and 'filter reset' must survive the loss of WiFi, of the admin
 * page and of the server. LoRa is up before any of those.
 */

// True when the node runs in safe mode (after repeated restarts).
bool mmnet_is_safe_mode();
