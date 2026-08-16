#pragma once

/* PacketFilter -- selectively refuses to forward other people's packets.
 *
 * A repeater's whole job is retransmitting traffic it has no stake in. Most
 * days that is exactly what you want. On the other days -- a client stuck in a
 * retry loop, an advert storm, a channel somebody is abusing -- you want to be
 * able to say 'not that, not from here' without taking the node off the air.
 *
 * WHERE THE IDEA COMES FROM. The shape of this filter follows the behaviour
 * documented by the Dutch MeshCore fork in docs/packet_filter_reference.md
 * (https://github.com/Dutch-MeshCore/MeshCore): the same six kinds of rule, the
 * same 'filter ...' command family, the same 'off by default, direct-routed
 * packets bypass it' starting point. NO CODE WAS TAKEN FROM THAT PROJECT -- this
 * was written against the published description. Their licence (MIT, inherited
 * from meshcore-dev/MeshCore) would have permitted copying; the reasoning for
 * not doing so is in docs/contributing.md, under 'Third-party code'.
 *
 * Two places where this deliberately does something else than the reference
 * describes, both because that description assumes something a repeater does
 * not have:
 *
 *   channels     A repeater cannot see a channel NAME. All that is on the air
 *                is one byte: sha256(channel_key)[0]. So blocking takes the
 *                key (from which we compute that byte the way MeshCore does)
 *                or the byte itself -- never a name. One byte collides about
 *                one channel in 256, and that cost is real; see the note at
 *                chanAdd().
 *   malformed    Checking a group message's timestamp, text and UTF-8 needs the
 *                plaintext, and the plaintext needs the channel key, which a
 *                repeater does not hold. What is checked here is what CAN be
 *                checked without a key: the payload holds a hash, a MAC and at
 *                least one cipher block, and the ciphertext is a whole number
 *                of blocks. A packet failing that was never valid from anyone.
 *                One passing may still be nonsense -- hence 'structureel' in
 *                the status line rather than 'geldig'.
 *
 * WHAT IT NEVER TOUCHES, and this is the part that keeps a roof node usable:
 *
 *   - packets addressed to this node. The filter sits in allowPacketForward(),
 *     which is only ever asked about somebody else's packet. A login, a CLI
 *     command, a status request -- none of those go past here at all. No filter
 *     rule can lock you out.
 *   - direct-routed packets. A packet following a path that already names this
 *     node belongs to an established route; dropping those breaks working
 *     conversations instead of curbing a flood.
 *   - packets whose destination or source hash is a client in this node's ACL.
 *     Whoever may administer this node keeps working while a filter is on.
 *
 * AND THE WAY BACK. A filter is the rare setting that makes a node useless
 * without making it unreachable: it still answers, still advertises, still
 * looks green, and quietly forwards nothing. 'filter off' and 'filter reset'
 * therefore work over the mesh CLI like every other command here -- no WiFi, no
 * admin page, no server needed -- and on the site's side they are the CHEAPEST
 * actions in the permission model. A role that may not switch a filter on may
 * still switch one off. Recovery must never be gated harder than the mistake.
 */

#include <Arduino.h>
#include <FS.h>

// The payload types MeshCore has, 0x00..0x0B. Also the width of every table.
#define PF_TYPE_COUNT   12
#define PF_CHAN_MAX     16
#define PF_LABEL_MAX    24

// Why a packet was dropped. Order is the order they are tested in.
enum PfReason {
  PF_PASS = 0,
  PF_R_TYPE,        // this payload type is not forwarded at all
  PF_R_HOPS,        // too many path hashes already
  PF_R_RATE,        // over the per-type budget for this window
  PF_R_HASH,        // path hashes smaller than the minimum
  PF_R_CHANNEL,     // group text on a blocked channel hash
  PF_R_MALFORMED,   // group text whose structure cannot be right
  PF_REASON_COUNT
};

/* Reads /filter_prefs. Safe to call before the mesh is up; the filter simply
 * answers 'allow' until it has been told otherwise.
 *
 * 'armed' false loads the rules but leaves the filter switched OFF, without
 * writing that back to the file. That is what safe mode passes: a node that has
 * restarted three times in a row is a node whose configuration is suspect, and
 * the one setting there that can make it look healthy while doing nothing is
 * this one. The rules are still readable over the CLI, so you can see what it
 * WOULD have done -- they are just not enforced until the node has proved it
 * can stay up. */
void pf_begin(FS &fs, bool armed = true);

// Lazily writes the preferences after a change. Call from the main loop.
void pf_loop();

bool pf_enabled();

/* The decision, called from MyMesh::allowPacketForward() for FLOOD packets
 * only. Returns true when the packet may be forwarded. 'exempt' is the ACL
 * check the caller does -- it is passed in rather than looked up here so this
 * module never has to know what a MyMesh is.
 *
 * Counts as it goes: this is the only place the drop counters move. */
bool pf_allow(uint8_t payload_type, uint8_t hash_count, uint8_t hash_size,
              const uint8_t *payload, int payload_len, bool exempt);

/* The whole 'filter ...' command family. 'rest' is everything after the word
 * 'filter'. Returns false when the command was not understood, so the caller
 * can fall through to the stock CLI.
 *
 * ONE parser for the CLI and for the web API alike -- POST /api/filter passes
 * the same string through here. A second implementation for the second caller
 * is a second set of limits that will disagree with this one on the day it
 * matters. */
bool pf_command(const char *rest, char *reply, size_t reply_max);

/* The reason the LAST pf_allow() call landed on -- PF_PASS when it let the
 * packet through. Only meaningful immediately after that call.
 *
 * An accessor rather than an out-parameter, for a reason that is not taste:
 * pf_allow() is called from a hunk inside somebody else's file
 * (MyMesh::allowPacketForward, repeater-hooks.patch), and every byte of that
 * hunk has to keep applying to an upstream tree we do not control. Changing the
 * signature would widen that patch; this does not. */
uint8_t pf_last_reason();

/* The name of a reason ("hops", "rate", ...), as the CLI, the API and the site
 * already spell it. One spelling of the six reasons, everywhere. */
const char *pf_reason_name(uint8_t reason);

/* The complete state as JSON -- rules, per-type limits, channels, counters --
 * for GET /api/filter. Returns the bytes written, or 0 when it did not fit. */
size_t pf_json(char *out, size_t max);

/* The short version for the statistics message: whether a filter is on and
 * what it has thrown away, per reason. No rule tables.
 *
 * Two objects rather than one because they answer different questions and
 * travel at different prices. 'What has this filter dropped' belongs in every
 * message, costs about 160 bytes, and is the number somebody watching the site
 * needs. 'What are the twelve hop limits' is two kilobytes that change once a
 * month, and belongs behind a request from somebody who is about to change
 * them.
 *
 * It is sent even when the filter is off, and that is the useful part: 'this
 * node reports no filter' and 'this node runs firmware too old to have one'
 * then look different on the site instead of both looking like silence.
 *
 * 'detail' adds the breakdown: type x reason, the rate-limit pressure per type,
 * ACL exemptions per type and hits per blocked channel. Only non-zero entries,
 * so on a node with no filter it costs nothing and on a busy one it stays
 * proportional to what actually happened.
 *
 * WHICH WAY THE DATA TRAVELS, and this is the whole reason the flag exists.
 * The detail goes over MQTT only -- wifi or LAN, where bandwidth is free. The
 * settings sweep and the mesh CLI stay exactly as lean as they were, because
 * there every byte is airtime on a shared band. The short summary above is what
 * rides in every message; the detail rides along on the same topic when it fits,
 * and sets "trunc":1 when it did not, because a breakdown that quietly lost half
 * its rows is worse than one that says so. */
size_t pf_summary_json(char *out, size_t max, bool detail = false);
