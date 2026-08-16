# Glossary

*[Nederlands](nl/glossary.md)*

MeshCore vocabulary, as MeshStats uses it. Every entry says what the word means,
where it comes from, and — where it matters — what it does **not** mean. The
byte-level definitions live in [`protocol.md`](protocol.md); this page is the
short version you can keep open beside the site.

Words are grouped by what they describe rather than alphabetically, because
several of them only make sense next to each other. There is an
[alphabetical index](#alphabetical-index) at the bottom.

---

## Nodes and roles

### Node

Any MeshCore device on the mesh. Its identity is an **Ed25519 key pair**; the
public key is the node's name in every technical sense. Everything else — the
display name, the location, the role — is advertised and can be changed or
faked. The key cannot.

### Repeater

A node whose job is to forward other people's traffic. Firmware
`examples/simple_repeater`. It has no chat UI, keeps counters about what it
forwarded, and answers a small CLI over LoRa to whoever logs in with its admin
password. Repeaters are what MeshStats is a statistics site *about*.

### Companion

A node paired with an app or a computer, over USB serial, BLE or TCP/WiFi.
Firmware `examples/companion_radio`. It is the node that *hears* the mesh and
has a route out to the internet, so in MeshStats it is the node that publishes.
See [`protocol.md` §2](protocol.md#2-the-companion-protocol-tcp-and-serial) for
the protocol it speaks to its app.

Stock companion firmware accepts **one** TCP client at a time. That single
limitation is the reason both [`proxy.md`](proxy.md) and the
`SerialWifiInterface` change in [`firmware.md`](firmware.md) exist.

### Monitor

A node that has been given the credentials of one or more repeaters and looks
after them on the mesh's behalf: it logs in over LoRa, asks them for their
settings, and checks or sets their clocks. A repeater with a monitor is called
**relayed** — the site never talks to it directly, it asks the monitor.

The distinction is visible in the code as `commanding.is_relayed(rep)` and shapes
the answer of `clocksync.time_route()` in `server/app/clocksync.py`: a relayed
repeater gets its time from its monitor over LoRa, a non-relayed one gets it
straight from the site over MQTT.

### Observer

The node whose radio actually heard a given packet. Every row in the packet
archive has one. It is not the sender and not the destination — it is the
witness. Two observers hearing the same packet produce two rows with the same
packet hash and different SNR, which is exactly what makes a link map possible.

---

## Packets and routing

### Advert

Payload type `ADVERT` (`0x04`). A node announcing itself: public key, timestamp,
signature, and optional extras (name, location, node type). It is **Ed25519
signed**, so an advert is the one packet type whose authorship can be verified
without holding any shared secret.

Adverts are where MeshStats gets node names and map positions from. Full byte
layout: [`protocol.md` §1.6](protocol.md#advert-0x04).

### Flood

Route type `FLOOD`. The packet is rebroadcast by every repeater that hears it
and has not seen it before. Each forwarder **appends** its own key prefix to the
path, so the path grows as the packet travels outward, and on arrival it reads
as the route *back* to the sender.

### Direct

Route type `DIRECT`. The packet carries an explicit list of hops and is
source-routed along it. `path[0]` is the next hop; a node forwards only if that
entry matches its own key prefix, and then **removes** it. The path shrinks as
the packet travels.

Practical consequence: a flood path read left to right is history, a direct path
read left to right is the future.

### Hop

One forwarder on a packet's path. On the wire a hop is **not** an identifier and
**not** a digest — it is the first *n* bytes of that forwarder's Ed25519 public
key, copied verbatim (`Identity::copyHashTo()`, a plain `memcpy`).

With the common 1-byte size there are 256 possible hop values. In a mesh of a
few hundred nodes, several nodes answer to the same value. The site therefore
treats "which node is this hop?" as a question with a *set* of answers, weighed
by evidence — see [`contributing.md`](contributing.md#1-honesty-about-uncertainty)
and `server/app/candidates.py`.

### Path hash size

How many bytes each hop entry occupies: 1, 2, 3 (4 is reserved and rejected).
It is decided **per packet, by whoever sent it first**, from that node's
`hash_mode` CLI setting, and every forwarder keeps it. It is therefore not a
mesh-wide or firmware-version property: sizes 1, 2 and 3 travel side by side on
the same air.

MeshStats reads it off the frame and reports it as `path_hash_size`. Details and
the bit packing: [`protocol.md` §1.4](protocol.md#14-the-path-field).

### Address hash

The 1-byte source and destination hashes **inside an encrypted payload** — not
in the path. Fixed at one byte by `PATH_HASH_SIZE` and by the definition of
`PAYLOAD_VER_1`.

This is the entry people most often get backwards: **the path hash size and the
address hash size are unrelated.** A node configured for `hash_mode 2` puts
two-byte hops in the path and *still* addresses its peers with one byte. The
packet detail page names which of the two it is showing, and how big it is,
for precisely this reason.

### Transport codes

Four bytes that are present **if and only if** the route type is
`TRANSPORT_FLOOD` (`0x00`) or `TRANSPORT_DIRECT` (`0x03`). This is the only
variable-presence field in the header, so a decoder that assumes a fixed offset
after byte 1 will be four bytes out of step on every scoped packet.

- `codes[0]` is computed from a 16-byte scope key **and the packet**, so it
  differs for every packet sent under one and the same key. Only a node holding
  the key can recognise it, by recomputing it.
- `codes[1]` is reserved for the sender's home region; the firmware writes a
  literal zero.

So the presence of the codes proves a packet was scoped. The codes themselves do
**not** name the region — that cannot be recovered from the bytes on the air.

### Scoped / unscoped / share

The three values MeshStats reports in the `scope` column
(`server/app/packets.py`):

| Value | Meaning |
|---|---|
| `scoped` | Transport codes present — sent under some scope key |
| `unscoped` | No transport codes on the wire |
| `share` | Transport codes present and both are zero |

`share` is a deliberate marker, not a degenerate case: `{0, 0}` is the shape of
an advert imported through the app's Share function rather than heard off the
air. `calcTransportCode()` reserves both end values, so a real scope key can
never produce `codes[0] == 0`.

Two cautions about reading these:

- **A mesh reads as mostly `unscoped` even when its repeaters have regions
  configured.** A repeater scopes only what it originates and replies to scoped
  requests; everything it forwards for others passes through unchanged. One
  node's region setting says nothing about the traffic through it.
- **On a DIRECT row, `unscoped` means "not applicable", not "loose in the
  wild".** A direct packet is source-routed, and the firmware never asks which
  region it belongs to.

### Packet hash

The identity of a packet for deduplication, derived from its contents. Two
observers hearing the same transmission report the same hash — which is what
lets the site collapse them into one packet with two witnesses instead of two
packets.

---

## Site vocabulary

| Term | Meaning |
|---|---|
| **Prefix** / `pubkey_prefix` | The leading hex characters of a node's public key, used as its short identity. Different sources publish different lengths — the `meshcore` HA integration five bytes, a node's own firmware six — which is why matching is prefix-tolerant down to 8 hex characters and no further (`MIN_PREFIX_MATCH`). |
| **Slug** | The URL-safe name of a repeater on the site: `/r/<slug>`. |
| **Snapshot** | One `POST /api/v1/ingest` body: one repeater, its current metrics, its neighbours. |
| **Neighbour** | Another node this repeater has heard directly, with the SNR it was heard at. The raw material of the link map. |
| **Heartbeat** | A forced graph point written even when nothing changed, so a flat line is visibly flat rather than absent (`MCS_HEARTBEAT_MIN`). |
| **Clock sync** | The site telling a node what time it is — directly over MQTT, or via that node's monitor over LoRa. `server/app/clocksync.py`. |
| **Settings fetch** | Asking a repeater to read back its own CLI settings. Read-only: the site can request values, never write them. |
| **Facet** | A "top values" breakdown for a searchable field in the packet archive. |

---

## Radio measurements

| Term | Meaning |
|---|---|
| **SNR** | Signal-to-noise ratio in dB, as the receiving radio reported it. LoRa decodes well below 0 dB, so negative SNR is normal and not a fault. |
| **RSSI** | Received signal strength in dBm. Always negative; closer to zero is stronger. |
| **Noise floor** | The ambient level the radio measures with nothing being transmitted. |
| **Airtime** | How long the radio was actually transmitting or receiving. The quantity that matters for duty-cycle limits, and the reason a chatty node is a mesh-wide problem rather than a private one. |

---

## Alphabetical index

[Address hash](#address-hash) ·
[Advert](#advert) ·
[Airtime](#radio-measurements) ·
[Clock sync](#site-vocabulary) ·
[Companion](#companion) ·
[Direct](#direct) ·
[Facet](#site-vocabulary) ·
[Flood](#flood) ·
[Heartbeat](#site-vocabulary) ·
[Hop](#hop) ·
[Monitor](#monitor) ·
[Neighbour](#site-vocabulary) ·
[Node](#node) ·
[Noise floor](#radio-measurements) ·
[Observer](#observer) ·
[Packet hash](#packet-hash) ·
[Path hash size](#path-hash-size) ·
[Prefix](#site-vocabulary) ·
[Repeater](#repeater) ·
[RSSI](#radio-measurements) ·
[Scoped](#scoped--unscoped--share) ·
[Settings fetch](#site-vocabulary) ·
[Share](#scoped--unscoped--share) ·
[Slug](#site-vocabulary) ·
[Snapshot](#site-vocabulary) ·
[SNR](#radio-measurements) ·
[Transport codes](#transport-codes) ·
[Unscoped](#scoped--unscoped--share)

---

## Where to go next

| You want | Read |
|---|---|
| The bytes, exactly | [`protocol.md`](protocol.md) |
| How the pieces fit together | [`architecture.md`](architecture.md) |
| Why the code looks the way it does | [`contributing.md`](contributing.md) |
| Everything else | [`README.md`](README.md) |
