# MeshCore protocols

Two different protocols are described here. They are unrelated to each other and
easy to confuse:

| | Where it runs | What carries it |
|---|---|---|
| [Over-the-air packet format](#1-the-over-the-air-packet-format) | LoRa radio, node to node | raw LoRa frames |
| [Companion TCP/serial protocol](#2-the-companion-protocol-tcp-and-serial) | node to app | TCP, USB serial, BLE |

Both were reconstructed by reading the MeshCore firmware source. Neither is
documented upstream. Everything below is cited to the file and function it came
from, so you can re-check it against your own firmware version.

**Version of record.** All line references are against the MeshCore working
tree at `C:\Users\Public\MeshCore` (companion firmware v1.17.0 line). The wire
format is stable across the v1.1x series, but the companion command numbers are
not — see [Version drift](#version-drift).

---

# 1. The over-the-air packet format

Source of record:

| Concern | File |
|---|---|
| Parsing a received frame | `src/Dispatcher.cpp`, `Dispatcher::tryParsePacket()` |
| Serialising a frame for TX | `src/Dispatcher.cpp`, `Dispatcher::checkSend()` |
| Blob form (same layout) | `src/Packet.cpp`, `Packet::writeTo()` / `readFrom()` |
| Field accessors, constants | `src/Packet.h` |
| Size constants | `src/MeshCore.h` |
| Payload semantics per type | `src/Mesh.cpp`, `Mesh::onRecvPacket()` |
| Advert `app_data` | `src/helpers/AdvertDataHelpers.{h,cpp}` |

## 1.1 Frame layout

```
+--------+------------------+----------+---------------+----------------------+
| header | transport codes  | path_len | path          | payload              |
| 1 byte | 0 or 4 bytes     | 1 byte   | 0..64 bytes   | 0..184 bytes         |
+--------+------------------+----------+---------------+----------------------+
```

There is no preamble, no magic number, no length prefix and no CRC at this
layer. The LoRa PHY already delivers a length and a CRC, so a MeshCore frame
begins immediately with the header byte. `payload` is simply "everything left
over" — `tryParsePacket()` computes `payload_len = len - i` after consuming the
fixed part.

Maximum on-air frame is `MAX_TRANS_UNIT` = **255** bytes.

## 1.2 The header byte

One byte, three bit fields (`src/Packet.h`, lines 8–12):

```
 bit  7   6   5   4   3   2   1   0
     +---+---+---+---+---+---+---+---+
     |  ver  |   payload type    | route |
     +---+---+---+---+---+---+---+---+
       ^       ^                   ^
       |       |                   +-- bits 0-1, PH_ROUTE_MASK 0x03
       |       +---------------------- bits 2-5, PH_TYPE_SHIFT 2, PH_TYPE_MASK 0x0F
       +------------------------------ bits 6-7, PH_VER_SHIFT 6, PH_VER_MASK 0x03
```

`header == 0xFF` is not a valid wire value. It is an in-memory sentinel meaning
"do not retransmit this packet" (`Packet::markDoNotRetransmit()`), set when a
node determines a packet was addressed to itself.

### Route type (bits 0–1)

| Value | Name | Meaning |
|---|---|---|
| `0x00` | `ROUTE_TYPE_TRANSPORT_FLOOD` | Flood, **with** 4 bytes of transport codes |
| `0x01` | `ROUTE_TYPE_FLOOD` | Flood; each forwarder appends its hash to `path` |
| `0x02` | `ROUTE_TYPE_DIRECT` | Source-routed; `path` is the remaining hop list |
| `0x03` | `ROUTE_TYPE_TRANSPORT_DIRECT` | Direct, **with** 4 bytes of transport codes |

The transport-code field is present if and only if the route type is `0x00` or
`0x03` (`Packet::hasTransportCodes()`). This is the only variable-presence field
in the fixed part of the frame, and it is the single most common way to
mis-parse a MeshCore packet: a naive parser that always reads `path_len` from
offset 1 will be four bytes out of step on transport-scoped packets.

### Payload type (bits 2–5)

| Value | Name | Payload begins with | Notes |
|---|---|---|---|
| `0x00` | `PAYLOAD_TYPE_REQ` | dest hash, src hash, MAC | encrypted: timestamp + blob |
| `0x01` | `PAYLOAD_TYPE_RESPONSE` | dest hash, src hash, MAC | reply to REQ / ANON_REQ |
| `0x02` | `PAYLOAD_TYPE_TXT_MSG` | dest hash, src hash, MAC | encrypted: timestamp + text |
| `0x03` | `PAYLOAD_TYPE_ACK` | 4-byte CRC | plaintext, not encrypted |
| `0x04` | `PAYLOAD_TYPE_ADVERT` | pub key, timestamp, signature | **signed, not encrypted** |
| `0x05` | `PAYLOAD_TYPE_GRP_TXT` | channel hash, MAC | group text, unverified sender |
| `0x06` | `PAYLOAD_TYPE_GRP_DATA` | channel hash, MAC | group datagram |
| `0x07` | `PAYLOAD_TYPE_ANON_REQ` | dest hash, ephemeral pub key, MAC | |
| `0x08` | `PAYLOAD_TYPE_PATH` | dest hash, src hash, MAC | encrypted: a path + extra |
| `0x09` | `PAYLOAD_TYPE_TRACE` | tag, auth code, flags | collects per-hop SNR |
| `0x0A` | `PAYLOAD_TYPE_MULTIPART` | 1 packing byte | one of a set |
| `0x0B` | `PAYLOAD_TYPE_CONTROL` | 1 flags byte | control/discovery |
| `0x0C`–`0x0E` | — | — | unassigned |
| `0x0F` | `PAYLOAD_TYPE_RAW_CUSTOM` | application-defined | custom encryption/format |

Unknown payload types are dropped and, importantly, **not** flood-forwarded
(`Mesh::onRecvPacket()` default branch). A new payload type therefore does not
propagate through a mesh of older nodes.

### Payload version (bits 6–7)

| Value | Name | Status |
|---|---|---|
| `0x00` | `PAYLOAD_VER_1` | The only version in use. 1-byte hashes, 2-byte MAC. |
| `0x01`–`0x03` | `PAYLOAD_VER_2..4` | Reserved. Nothing implements them. |

`tryParsePacket()` rejects any frame with `getPayloadVer() > PAYLOAD_VER_1`
before doing anything else. Version is checked first, so a future v2 frame costs
an old node one byte of parsing and nothing more.

## 1.3 Transport codes

Present only for route types `0x00` and `0x03`. Four bytes: two `uint16`s,
little-endian, copied verbatim by `memcpy` on both parse and serialise
(`Dispatcher.cpp` lines 158–163 and 313–316).

They are set by the caller through the `transport_codes` argument of
`Mesh::sendFlood()` / `Mesh::sendZeroHop()` (`src/Mesh.cpp` lines 664–677 and
728–732). The core library only carries them; it never inspects them. The
comment in `src/helpers/BaseSerialInterface.h` describes their purpose as
region scoping.

> **Unverified:** the assignment of specific code values to specific regions or
> scopes is not defined anywhere in the core sources read for this document. If
> you need those values, read the application layer that sets them, not
> `src/`.

## 1.4 The path field

`path_len` is one byte, but it is *not* a byte count. It packs two numbers
(`src/Packet.h` lines 79–83):

```
 bit  7   6   5   4   3   2   1   0
     +-------+-----------------------+
     | sz-1  |      hash count       |
     +-------+-----------------------+
```

| Expression | Meaning |
|---|---|
| `getPathHashSize()` = `(path_len >> 6) + 1` | Bytes per hop entry: 1, 2, 3 or 4 |
| `getPathHashCount()` = `path_len & 63` | Number of hop entries, 0–63 |
| `getPathByteLen()` = count × size | Actual bytes on the wire |

`path_len == 0x00` therefore means "hash size 1, zero hops" — the common case
for a freshly emitted flood packet.

Constraints, enforced in `Packet::isValidPathLen()` and again in
`tryParsePacket()`:

- Upper bits `= 3` (hash size 4) is **reserved and rejected**. `tryParsePacket()`
  refuses the frame; `isValidPathLen()` returns false.
- `count × size` must be ≤ `MAX_PATH_SIZE` (**64**).
- The path must fit within the received frame, or the packet is discarded as
  truncated.

A hop entry is a **prefix of the forwarder's Ed25519 public key**, not a digest.
`Identity::copyHashTo()` is a plain `memcpy` of the first `n` bytes of `pub_key`
(`src/Identity.h` lines 19–26). Comparison is `memcmp` on the same prefix. So a
1-byte "hash" gives 256 buckets and collisions are expected and handled — see
`Mesh::searchPeersByHash()`, which is documented as supporting up to 4
simultaneous matches and simply tries to decrypt against each candidate.

### How the path evolves

**Flood** (`Mesh::routeRecvPacket()`): each forwarder appends its own hash at
`path[count * size]` and increments the count, provided
`(count + 1) * size <= MAX_PATH_SIZE`. The path grows outward from the source,
so on arrival it is the route *back*.

**Direct** (`Mesh::onRecvPacket()`, `Mesh::removeSelfFromPath()`): `path[0]` is
the next hop. A node forwards only if `path[0]` matches its own key prefix, then
removes that entry by shifting the whole path down by one entry and decrementing
the count. The path shrinks as the packet travels.

### What a path can and cannot tell you

This matters for anything that tries to *display* a route, and MeshStats does
exactly that on its live map, so the limit is worth stating plainly.

A hop entry is a key prefix, not an identifier. With `PATH_HASH_SIZE` = 1 there
are **256** possible values. A mesh of a few hundred nodes therefore has hop
values that several nodes answer to — by the birthday bound, a collision among
256 buckets becomes likely at around 20 nodes, and MeshStats already tracks over
200. Ambiguity is the normal case, not a data error.

Consequences for a reader of the path:

| Candidates matching a hop | What you may conclude |
|---|---|
| exactly one | that node forwarded the packet — as certain as this protocol gets |
| several | one of them forwarded it; **which one is not recoverable** |
| none | a node you have never heard an advert from forwarded it |

The firmware itself works this way: `Mesh::searchPeersByHash()` returns up to 4
candidates and simply tries each. Any renderer that picks a single "best"
candidate is inventing certainty the wire format does not carry. MeshStats
resolves every candidate (`_resolve_hop()` in `server/app/routes_api.py`) and
draws unresolved and ambiguous hops as dashed gaps rather than as lines to a
guess.

Two further limits on reading a stored path:

- **Direction depends on route type.** For flood, the path grew behind the
  packet, so it is the route it travelled. For direct, `path` is the route
  *still to be walked* — the hops already passed have been removed.
- **A path does not name the sender.** Only ADVERT carries an identity, so for
  every other payload type the origin of a received packet is unknown, however
  complete the path is.

**Trace is the exception.** For `PAYLOAD_TYPE_TRACE`, `path` does not carry
hashes at all — each hop appends its measured SNR as a signed byte:

```c
pkt->path[pkt->path_len++] = (int8_t) (pkt->getSNR()*4);   // Mesh.cpp:61
```

Note this line increments `path_len` as a raw counter, bypassing the
size/count packing. TRACE therefore only behaves correctly with hash size 1.

## 1.5 Size constants

From `src/MeshCore.h` unless noted:

| Constant | Value | Meaning |
|---|---|---|
| `MAX_TRANS_UNIT` | 255 | Largest on-air frame |
| `MAX_PACKET_PAYLOAD` | 184 | Largest `payload` |
| `MAX_PATH_SIZE` | 64 | Largest `path` in bytes |
| `MAX_HASH_SIZE` | 8 | Packet-hash length (dedup table) |
| `PATH_HASH_SIZE` | 1 | Default bytes per hop entry (V1) |
| `PUB_KEY_SIZE` | 32 | Ed25519 public key |
| `PRV_KEY_SIZE` | 64 | Ed25519 private key |
| `SEED_SIZE` | 32 | |
| `SIGNATURE_SIZE` | 64 | Ed25519 signature |
| `MAX_ADVERT_DATA_SIZE` | 32 | Largest advert `app_data` |
| `CIPHER_KEY_SIZE` | 16 | AES-128 |
| `CIPHER_BLOCK_SIZE` | 16 | |
| `CIPHER_MAC_SIZE` | 2 | Truncated HMAC (V1) |
| `MAX_GROUP_DATA_LENGTH` | 165 | `MAX_PACKET_PAYLOAD - CIPHER_BLOCK_SIZE - 3` |
| `MAX_FRAME_SIZE` | 176 | Companion frame cap (`helpers/BaseSerialInterface.h`) |

The 255 / 184 gap is 71 bytes: 1 header + 1 path_len + up to 64 path + 4
transport codes = 70, plus one byte of slack.

## 1.6 Payload layouts by type

### Encrypted peer-to-peer: REQ, RESPONSE, TXT_MSG, PATH (`0x00`, `0x01`, `0x02`, `0x08`)

```
+-----------+----------+--------+---------------------------+
| dest_hash | src_hash | MAC    | ciphertext                |
| 1 byte    | 1 byte   | 2 byte | multiple of 16 bytes      |
+-----------+----------+--------+---------------------------+
```

From `Mesh::onRecvPacket()` lines 133–140. Both hashes are 1-byte key prefixes
under `PAYLOAD_VER_1`.

The MAC and ciphertext are produced by `Utils::encryptThenMAC()`
(`src/Utils.cpp` lines 135–155) and verified by `Utils::MACThenDecrypt()`:

- Cipher: **AES-128 in ECB mode**, zero-padded to a 16-byte multiple. There is no
  IV and no chaining. Identical plaintext blocks under the same key produce
  identical ciphertext blocks.
- MAC: **HMAC-SHA256 over the ciphertext**, keyed with the full 32-byte shared
  secret, truncated to the first `CIPHER_MAC_SIZE` = **2 bytes**.
- Order is encrypt-then-MAC. Decryption is refused unless the 2-byte MAC matches.

Two bytes of MAC is a 1-in-65536 chance of accepting a random forgery per
attempt. That is a deliberate airtime trade-off, not an oversight — but it means
the MAC is a corruption check that also happens to be keyed, not an
authentication guarantee. Genuine sender authentication in MeshCore comes from
the Ed25519 signature on adverts, not from this MAC.

The shared secret is ECDH on Curve25519, with the Ed25519 public key transposed
to X25519 (`LocalIdentity::calcSharedSecret()`, `src/Identity.h` lines 70–81).

Decrypted `PAYLOAD_TYPE_PATH` plaintext has its own structure
(`Mesh::onRecvPacket()` lines 161–172):

```
+----------+-----------------+------------+------------------+
| path_len | path            | extra_type | extra            |
| 1 byte   | count*size      | 1 byte     | remainder        |
+----------+-----------------+------------+------------------+
```

`extra_type` uses only the low nibble (`data[k++] & 0x0F`); the high nibble is
reserved. `extra` runs to the end of the decrypted block and **may be padded
with zeroes**, because AES-ECB padding is not stripped — the receiver cannot
distinguish trailing zero padding from trailing zero data. Length has to come
from `extra_type`'s own encoding.

### ANON_REQ (`0x07`)

```
+-----------+-------------------+--------+---------------------------+
| dest_hash | sender pub key    | MAC    | ciphertext                |
| 1 byte    | 32 bytes          | 2 byte | multiple of 16 bytes      |
+-----------+-------------------+--------+---------------------------+
```

`Mesh::onRecvPacket()` lines 197–219. The sender ships its full public key so a
node with no prior contact entry can still derive the shared secret.

### Group: GRP_TXT (`0x05`), GRP_DATA (`0x06`)

```
+--------------+--------+---------------------------+
| channel_hash | MAC    | ciphertext                |
| 1 byte       | 2 byte | multiple of 16 bytes      |
+--------------+--------+---------------------------+
```

`Mesh::onRecvPacket()` lines 225–247. The key is the channel PSK, so any holder
of the PSK can forge a message — this is why `Packet.h` calls these
"(unverified)". Decrypted GRP_TXT is `timestamp, "name: msg"`; decrypted
GRP_DATA is `data_type (uint16), data_len, blob`.

Channel lookup is by hash with up to 4 candidate matches, same collision
handling as peers.

### ACK (`0x03`)

```
+----------+
| ack_crc  |
| 4 bytes  |
+----------+
```

`Mesh::onRecvPacket()` lines 117–128. A bare 32-bit value, little-endian,
plaintext. ACKs are handled before the normal direct-route check so a node can
notice an ACK "early" — while it is still forwarding it for someone else
(lines 78–87).

### ADVERT (`0x04`)

```
+---------------+-----------+-------------+---------------------+
| pub_key       | timestamp | signature   | app_data            |
| 32 bytes      | 4 bytes   | 64 bytes    | 0..32 bytes         |
+---------------+-----------+-------------+---------------------+
```

`Mesh::createAdvert()` lines 404–438, parsed at lines 252–291.

- `timestamp` is `uint32` little-endian, UNIX epoch seconds, from the node's RTC.
- `signature` is Ed25519 over the concatenation
  `pub_key || timestamp || app_data` — that is, over the packet's own fields
  *excluding* the signature itself. Both the builder and the verifier assemble
  that message identically into a `PUB_KEY_SIZE + 4 + MAX_ADVERT_DATA_SIZE`
  buffer.
- A node that receives its own advert back logs it and drops it
  (`self_id.matches(id.pub_key)`).
- A failed signature check drops the packet and **stops the flood** — it is never
  forwarded.
- `app_data` longer than `MAX_ADVERT_DATA_SIZE` is clamped to 32 bytes before
  verification, so an oversized advert fails its signature check rather than
  overflowing.

This is the only packet type in the protocol with real sender authentication,
which is why it is the basis for identity in the mesh.

### Advert `app_data` — flag-driven encoding

`src/helpers/AdvertDataHelpers.{h,cpp}`. Byte 0 is a flags byte; every
subsequent field is present only if its flag is set, in a fixed order.

```
 bit  7   6   5   4   3   2   1   0
     +---+---+---+---+---+---+---+---+
     |NAM|FT2|FT1|LAT|    type       |
     +---+---+---+---+---+---+---+---+
```

| Bit | Constant | Value | If set, adds |
|---|---|---|---|
| 0–3 | node type (low nibble) | — | nothing; it is the type itself |
| 4 | `ADV_LATLON_MASK` | `0x10` | `int32` lat + `int32` lon, LE, 8 bytes |
| 5 | `ADV_FEAT1_MASK` | `0x20` | `uint16` extra1, LE, 2 bytes |
| 6 | `ADV_FEAT2_MASK` | `0x40` | `uint16` extra2, LE, 2 bytes |
| 7 | `ADV_NAME_MASK` | `0x80` | UTF-8 name, **rest of `app_data`** |

Node types (low nibble, `getType()` = `_flags & 0x0F`):

| Value | Constant |
|---|---|
| 0 | `ADV_TYPE_NONE` |
| 1 | `ADV_TYPE_CHAT` |
| 2 | `ADV_TYPE_REPEATER` |
| 3 | `ADV_TYPE_ROOM` |
| 4 | `ADV_TYPE_SENSOR` |
| 5–15 | reserved |

Field order in the encoding is **fixed**: lat/lon, then feat1, then feat2, then
name. Because the name is unlength-prefixed and runs to the end of `app_data`,
it must be last, and a parser must know the total `app_data_len` to find where
the name ends. There is no null terminator on the wire; `AdvertDataParser` adds
one when copying into its own buffer.

Coordinates are fixed-point microdegrees: `_lat = lat * 1E6` on encode,
`_lat / 1000000.0` on decode. Range is therefore ±2147 degrees — far more than
needed, and the precision is about 11 cm.

Two encoder quirks worth knowing, both in `AdvertDataBuilder::encodeTo()`:

1. `extra1` / `extra2` are written only `if (_extra1)` — a **feature value of
   zero is indistinguishable from the feature being absent**. You cannot
   transmit an explicit zero in either feature field.
2. The name is truncated with `mesh::validUtf8PrefixLength(_name, 32 - i)`, so it
   is cut on a UTF-8 character boundary rather than mid-sequence. A long name
   silently loses its tail; it never produces invalid UTF-8.

On the decode side, `AdvertDataParser` sets `_valid` only when
`app_data_len >= i` after walking the flagged fields. It does **not** bounds-check
each field against `app_data_len` as it goes, so it reads the flagged fields
first and validates afterwards. Feed it only buffers of at least
`MAX_ADVERT_DATA_SIZE`.

### TRACE (`0x09`)

```
+-----------+-----------+-------+------------------------+
| trace_tag | auth_code | flags | hop list               |
| 4 bytes   | 4 bytes   | 1 byte| appended by hops       |
+-----------+-----------+-------+------------------------+
```

`Mesh::createTrace()` sets `payload_len = 9` and the hop list grows after it.
`Mesh::onRecvPacket()` lines 41–68 handles it.

- `flags` low 2 bits are the path hash size exponent: `path_sz = flags & 0x03`,
  and entry size is `1 << path_sz` (v1.11+).
- The route is pre-computed: the intended hop hashes live in the *payload* after
  the flags byte, while measured SNR values accumulate in *`path`*.
- A node forwards only if its key prefix matches at
  `payload[9 + (path_len << path_sz)]`.
- When `offset >= len` the trace has reached the end of its route and is
  delivered via `onTraceRecv()`.

The offset is computed as `uint16_t` with an explicit comment explaining why:
`path_len` up to 63 times entry size up to 8 exceeds 255, and a `uint8_t` would
wrap and point the comparison at the wrong bytes.

TRACE is also special-cased in `Packet::calculatePacketHash()` — it is the only
type whose `path_len` is mixed into the dedup hash, because a trace can legitimately
revisit the same node on the return leg and must not be suppressed as a duplicate.

`Mesh::sendFlood()` explicitly refuses TRACE packets.

### MULTIPART (`0x0A`)

```
+---------------+---------------------------+
| packing byte  | inner payload             |
| 1 byte        | remainder                 |
+---------------+---------------------------+
```

The packing byte splits into `remaining = payload[0] >> 4` (packets still to
come) and `type = payload[0] & 0x0F` (the wrapped payload type)
(`Mesh::onRecvPacket()` lines 300–304).

Only `type == PAYLOAD_TYPE_ACK` is implemented. The handler rebuilds a synthetic
`Packet` with the packing byte stripped and processes it as a normal ACK, so
multipart ACKs deduplicate against ordinary ACKs. Everything else falls into a
`// FUTURE: other multipart types??` branch and is dropped.

`Mesh::createMultiAck()` builds the reverse: `payload[0] = (remaining << 4) | PAYLOAD_TYPE_ACK`.

### CONTROL (`0x0B`)

```
+-------------+---------------------------+
| flags byte  | application data          |
| 1 byte      | remainder                 |
+-------------+---------------------------+
```

Handled at `Mesh::onRecvPacket()` lines 70–76. The only rule enforced by the
core is a hard restriction: a CONTROL packet is delivered to
`onControlDataRecv()` only when it is **direct-routed, has bit 7 of
`payload[0]` set, and has a hop count of exactly zero**. Comment in source:
"just zero-hop control packets allowed (for this subset of payloads)". It is
never forwarded.

The meaning of the remaining 7 flag bits and the data after them is defined by
the application (`CMD_SEND_CONTROL_DATA` on the companion side), not by `src/`.

### RAW_CUSTOM (`0x0F`)

The payload is entirely application-defined. The core requires it to be
direct-routed and de-duplicates it, then calls `onRawDataRecv()`. It is
deliberately not flood-routed — the source carries the comment
`// don't flood route these (yet)`.

## 1.7 Packet hash and deduplication

`Packet::calculatePacketHash()` (`src/Packet.cpp` lines 41–50):

```
SHA256( payload_type_byte || [path_len if TRACE] || payload )  -> first 8 bytes
```

Note what is **excluded**: the route type, the payload version, and the path.
That is the whole point. The same logical packet arriving over two different
routes, or in flood and direct form, hashes identically and is suppressed as a
duplicate by `wasSeen()` / `markSeen()`.

## 1.8 Worked example — a repeater advert

A repeater called `BE-HSS-JessaZH.VIR` at 50.930000 N, 5.338000 E emits a flood
advert at epoch 1786665600.

**Header.** Flood route, advert type, version 1:

```
route = ROUTE_TYPE_FLOOD          = 0x01
type  = PAYLOAD_TYPE_ADVERT       = 0x04
ver   = PAYLOAD_VER_1             = 0x00

header = (0x00 << 6) | (0x04 << 2) | 0x01
       = 0x00 | 0x10 | 0x01
       = 0x11
```

**Transport codes.** Route type is `0x01`, not `0x00`/`0x03`, so this field is
absent. Zero bytes.

**path_len.** Freshly emitted, no hops yet, default hash size 1:

```
path_len = ((1 - 1) << 6) | 0 = 0x00
```

**app_data.** Repeater, has location, has name:

```
flags = ADV_TYPE_REPEATER | ADV_LATLON_MASK | ADV_NAME_MASK
      = 0x02 | 0x10 | 0x80
      = 0x92

lat = 50.930000 * 1e6 = 50930000 = 0x030948D0  -> LE: D0 48 09 03
lon =  5.338000 * 1e6 =  5338000 = 0x00517390  -> LE: 90 73 51 00
name = "BE-HSS-JessaZH.VIR" (18 bytes, no terminator)
```

```
92                                      flags
D0 48 09 03                             lat
90 73 51 00                             lon
42 45 2D 48 53 53 2D 4A 65 73 73 61     "BE-HSS-Jessa"
5A 48 2E 56 49 52                       "ZH.VIR"
```

`app_data` length = 1 + 4 + 4 + 18 = **27** bytes (≤ 32, so valid).

**Full frame.**

```
offset  bytes                                   field
------  --------------------------------------  ---------------------------
0x00    11                                      header
0x01    00                                      path_len (size 1, count 0)
0x02    <32 bytes>                              pub_key
0x22    80 5A 7E 6A                             timestamp (1786665600 LE)
0x26    <64 bytes>                              Ed25519 signature
0x66    92 D0 48 09 03 90 73 51 00 42 45 ...    app_data (27 bytes)
0x81    (end)
```

**Lengths.**

```
payload_len = 32 + 4 + 64 + 27          = 127
raw length  = 1 + 0 + 1 + 0 + 127       = 129 bytes
```

Cross-check against `Packet::getRawLength()`:

```
2 + getPathByteLen() + payload_len + (hasTransportCodes() ? 4 : 0)
= 2 + 0 + 127 + 0
= 129   ✓
```

**Signature input** is the 63-byte message `pub_key || 80 5A 7E 6A || app_data`,
i.e. 32 + 4 + 27 bytes — *not* the packet bytes, and not including the header or
path.

### The same advert after two flood hops

Two repeaters forward it. Each appends its own 1-byte key prefix. Say `A7` then
`3F`:

```
offset  bytes         field
------  ------------  -------------------------------
0x00    11            header (unchanged)
0x01    02            path_len: size 1, count 2
0x02    A7 3F         path
0x04    <127 bytes>   payload (unchanged)
0x83    (end)
```

`path_len` went `0x00` → `0x02`, and the frame grew by exactly 2 bytes to 131.
The payload — and therefore the signature and the dedup hash — is untouched.

---

# 2. The companion protocol (TCP and serial)

This is the link between a node and a client application: the MeshCore phone
app, `meshcore-cli`, the Home Assistant `meshcore` integration, or MeshStats'
own tooling. It is not a mesh protocol; it never leaves the local link.

Source of record:

| Concern | File |
|---|---|
| TCP framing, multi-client | `MeshStats/firmware/src/helpers/esp32/SerialWifiInterface.cpp` |
| Frame size cap | `MeshCore/src/helpers/BaseSerialInterface.h` |
| Command/response codes | `MeshCore/examples/companion_radio/MyMesh.cpp` |
| An independent implementation | `MeshStats/proxy/mc-proxy/mc_proxy.py` |

## 2.1 Framing

Every frame, in both directions:

```
+--------+-------------------+---------------------+
| marker | length            | payload             |
| 1 byte | 2 bytes, LE       | `length` bytes      |
+--------+-------------------+---------------------+
```

| Marker | Hex | Direction |
|---|---|---|
| `<` | `0x3C` | client → node |
| `>` | `0x3E` | node → client |

The length is little-endian and covers the payload only, not the 3-byte header.
Write side, `SerialWifiInterface::checkRecvFrame()`:

```c
pkt[0] = '>';
pkt[1] = (len & 0xFF);  // LSB
pkt[2] = (len >> 8);    // MSB
```

Read side, `SerialWifiInterface::readFromSlot()`, reads the marker and then the
2-byte length directly into a `uint16_t` — which is correct only because the
ESP32 is little-endian. A portable client must assemble it explicitly, as
`mc_proxy.py` does:

```python
ln = buf[1] | (buf[2] << 8)
```

The maximum payload is `MAX_FRAME_SIZE` = **176** bytes. Frames longer than that
are read and discarded byte by byte, as are frames whose marker is not the
expected one — the parser resynchronises rather than dropping the connection.

The 16-bit length field allows 65535, but nothing may send more than 176. Do not
size buffers from the length field alone.

## 2.2 Payload structure

Byte 0 of the payload is the command, response or push code. The remainder is
code-specific.

Because the header is 3 bytes, **the code byte is at offset 3 of the frame**.
That is how `mc_proxy.py` inspects frames without decoding them:

```python
if len(frame) >= 4 and frame[3] == PKT_SELF_INFO:
```

Command codes, client → node (`examples/companion_radio/MyMesh.cpp` lines 10–68):

| Code | Name | | Code | Name |
|---|---|---|---|---|
| 1 | `CMD_APP_START` | | 33 | `CMD_SIGN_START` |
| 2 | `CMD_SEND_TXT_MSG` | | 34 | `CMD_SIGN_DATA` |
| 3 | `CMD_SEND_CHANNEL_TXT_MSG` | | 35 | `CMD_SIGN_FINISH` |
| 4 | `CMD_GET_CONTACTS` | | 36 | `CMD_SEND_TRACE_PATH` |
| 5 | `CMD_GET_DEVICE_TIME` | | 37 | `CMD_SET_DEVICE_PIN` |
| 6 | `CMD_SET_DEVICE_TIME` | | 38 | `CMD_SET_OTHER_PARAMS` |
| 7 | `CMD_SEND_SELF_ADVERT` | | 39 | `CMD_SEND_TELEMETRY_REQ` |
| 8 | `CMD_SET_ADVERT_NAME` | | 40 | `CMD_GET_CUSTOM_VARS` |
| 9 | `CMD_ADD_UPDATE_CONTACT` | | 41 | `CMD_SET_CUSTOM_VAR` |
| 10 | `CMD_SYNC_NEXT_MESSAGE` | | 42 | `CMD_GET_ADVERT_PATH` |
| 11 | `CMD_SET_RADIO_PARAMS` | | 43 | `CMD_GET_TUNING_PARAMS` |
| 12 | `CMD_SET_RADIO_TX_POWER` | | 50 | `CMD_SEND_BINARY_REQ` |
| 13 | `CMD_RESET_PATH` | | 51 | `CMD_FACTORY_RESET` |
| 14 | `CMD_SET_ADVERT_LATLON` | | 52 | `CMD_SEND_PATH_DISCOVERY_REQ` |
| 15 | `CMD_REMOVE_CONTACT` | | 54 | `CMD_SET_FLOOD_SCOPE_KEY` (v8+) |
| 16 | `CMD_SHARE_CONTACT` | | 55 | `CMD_SEND_CONTROL_DATA` (v8+) |
| 17 | `CMD_EXPORT_CONTACT` | | 56 | `CMD_GET_STATS` (v8+) |
| 18 | `CMD_IMPORT_CONTACT` | | 57 | `CMD_SEND_ANON_REQ` |
| 19 | `CMD_REBOOT` | | 58 | `CMD_SET_AUTOADD_CONFIG` |
| 20 | `CMD_GET_BATT_AND_STORAGE` | | 59 | `CMD_GET_AUTOADD_CONFIG` |
| 21 | `CMD_SET_TUNING_PARAMS` | | 60 | `CMD_GET_ALLOWED_REPEAT_FREQ` |
| 22 | `CMD_DEVICE_QUERY` | | 61 | `CMD_SET_PATH_HASH_MODE` |
| 23 | `CMD_EXPORT_PRIVATE_KEY` | | 62 | `CMD_SEND_CHANNEL_DATA` |
| 24 | `CMD_IMPORT_PRIVATE_KEY` | | 63 | `CMD_SET_DEFAULT_FLOOD_SCOPE` |
| 25 | `CMD_SEND_RAW_DATA` | | 64 | `CMD_GET_DEFAULT_FLOOD_SCOPE` |
| 26 | `CMD_SEND_LOGIN` | | 65 | `CMD_SEND_RAW_PACKET` |
| 27 | `CMD_SEND_STATUS_REQ` | | | |
| 28 | `CMD_HAS_CONNECTION` | | | |
| 29 | `CMD_LOGOUT` | | | |
| 30 | `CMD_GET_CONTACT_BY_KEY` | | | |
| 31 | `CMD_GET_CHANNEL` | | | |
| 32 | `CMD_SET_CHANNEL` | | | |

Codes 44–49 and 53 are unassigned in this version.

`CMD_GET_STATS` (56) takes a sub-type in its second byte:

| Value | Name |
|---|---|
| 0 | `STATS_TYPE_CORE` |
| 1 | `STATS_TYPE_RADIO` |
| 2 | `STATS_TYPE_PACKETS` |

Response codes, node → client, sent in reply to a command:

| Code | Name | Reply to |
|---|---|---|
| 0 | `RESP_CODE_OK` | anything |
| 1 | `RESP_CODE_ERR` | anything |
| 2 | `RESP_CODE_CONTACTS_START` | `CMD_GET_CONTACTS` (first) |
| 3 | `RESP_CODE_CONTACT` | `CMD_GET_CONTACTS` (repeated) |
| 4 | `RESP_CODE_END_OF_CONTACTS` | `CMD_GET_CONTACTS` (last) |
| 5 | `RESP_CODE_SELF_INFO` | `CMD_APP_START` |
| 6 | `RESP_CODE_SENT` | `CMD_SEND_TXT_MSG` |
| 7 | `RESP_CODE_CONTACT_MSG_RECV` | `CMD_SYNC_NEXT_MESSAGE` (ver < 3) |
| 8 | `RESP_CODE_CHANNEL_MSG_RECV` | `CMD_SYNC_NEXT_MESSAGE` (ver < 3) |
| 9 | `RESP_CODE_CURR_TIME` | `CMD_GET_DEVICE_TIME` |
| 10 | `RESP_CODE_NO_MORE_MESSAGES` | `CMD_SYNC_NEXT_MESSAGE` |
| 11 | `RESP_CODE_EXPORT_CONTACT` | `CMD_EXPORT_CONTACT` |
| 12 | `RESP_CODE_BATT_AND_STORAGE` | `CMD_GET_BATT_AND_STORAGE` |
| 13 | `RESP_CODE_DEVICE_INFO` | `CMD_DEVICE_QUERY` |
| 14 | `RESP_CODE_PRIVATE_KEY` | `CMD_EXPORT_PRIVATE_KEY` |
| 15 | `RESP_CODE_DISABLED` | |
| 16 | `RESP_CODE_CONTACT_MSG_RECV_V3` | `CMD_SYNC_NEXT_MESSAGE` (ver ≥ 3) |
| 17 | `RESP_CODE_CHANNEL_MSG_RECV_V3` | `CMD_SYNC_NEXT_MESSAGE` (ver ≥ 3) |
| 18 | `RESP_CODE_CHANNEL_INFO` | `CMD_GET_CHANNEL` |
| 19 | `RESP_CODE_SIGN_START` | `CMD_SIGN_START` |
| 20 | `RESP_CODE_SIGNATURE` | `CMD_SIGN_FINISH` |
| 21 | `RESP_CODE_CUSTOM_VARS` | `CMD_GET_CUSTOM_VARS` |
| 22 | `RESP_CODE_ADVERT_PATH` | `CMD_GET_ADVERT_PATH` |
| 23 | `RESP_CODE_TUNING_PARAMS` | `CMD_GET_TUNING_PARAMS` |
| 24 | `RESP_CODE_STATS` | `CMD_GET_STATS`; byte 2 is the stats type |
| 25 | `RESP_CODE_AUTOADD_CONFIG` | `CMD_GET_AUTOADD_CONFIG` |
| 26 | `RESP_ALLOWED_REPEAT_FREQ` | `CMD_GET_ALLOWED_REPEAT_FREQ` |
| 27 | `RESP_CODE_CHANNEL_DATA_RECV` | |
| 28 | `RESP_CODE_DEFAULT_FLOOD_SCOPE` | `CMD_GET_DEFAULT_FLOOD_SCOPE` |

Push codes, node → client, **unsolicited at any time**. They start at `0x80`, so
the high bit distinguishes a push from a response:

| Code | Name |
|---|---|
| `0x80` | `PUSH_CODE_ADVERT` |
| `0x81` | `PUSH_CODE_PATH_UPDATED` |
| `0x82` | `PUSH_CODE_SEND_CONFIRMED` |
| `0x83` | `PUSH_CODE_MSG_WAITING` |
| `0x84` | `PUSH_CODE_RAW_DATA` |

`PUSH_CODE_LOG_RX_DATA` also exists and is emitted by `MyMesh::logRxRaw()`; its
numeric value is defined further down the same file and was not read for this
document.

Other constants from the same header block:

| Constant | Value |
|---|---|
| `MAX_CHANNEL_DATA_LENGTH` | `MAX_FRAME_SIZE - 9` = 167 |
| `PUBLIC_GROUP_PSK` | `izOH6cXN6mrJ5e26oRXNcg==` (the well-known public channel key) |

## 2.3 The single-client problem

Stock MeshCore accepts exactly one TCP client. The original loop was:

```cpp
auto newClient = server.available();
if (newClient) {
    client.stop();      // the existing companion is kicked off
    client = newClient;
}
```

So Home Assistant and your phone could not both be connected. MeshStats solves
this twice, in two different places, and the two solutions differ in an
important way.

### Firmware: `SerialWifiInterface`, 4 slots with targeted replies

`WIFI_MAX_CLIENTS` (default 4) slots, each holding its own `WiFiClient` **and its
own partial frame header**. Per-slot header state is not optional: two clients
mid-frame would otherwise corrupt each other's length fields.

New connections take a free slot. Only when all slots are busy does the oldest
get dropped (`slot = next_poll % WIFI_MAX_CLIENTS`).

Inbound frames are polled round-robin from `next_poll`, so one chatty client
cannot starve the others.

Outbound frames are **routed**:

```cpp
send_queue[send_queue_len].dest_slot = reply_slot;
```

`reply_slot` is reset to `-1` at the top of every `checkRecvFrame()` and set to
the slot index when a command is handed to the mesh. So anything the mesh writes
immediately after a command is treated as that command's reply and goes to that
client alone; anything written at any other time (adverts, incoming messages,
ACKs) has `dest_slot == -1` and is broadcast to every connected client.

This works because the companion firmware is single-threaded and synchronous: a
command is fully handled, including its writes, before the next frame is read.
Without the distinction, clients see each other's replies and desynchronise their
request/response state machines.

Send queue depth is `FRAME_QUEUE_SIZE` = 4 frames; a full queue drops the write
and returns 0.

### Proxy: `mc_proxy.py`, broadcast everything

The proxy sits in front of an **unmodified** node, so it holds the one upstream
socket and fans out. It cannot use the `reply_slot` trick — it has no visibility
into the node's internal ordering — and since version 1.8.0 it deliberately
broadcasts every node frame to every client instead, because the previous
reply-routing attempt let a busy client steal someone else's answer.

It compensates with two behaviours:

- **`RESP_CODE_SELF_INFO` caching.** The node answers `CMD_APP_START` only once
  per TCP session. The proxy stores the last SELF_INFO frame and answers any
  client's `CMD_APP_START` locally, without forwarding it.
- **Command spacing.** `MCP_MIN_CMD_GAP_S` (default 0.25 s) between upstream
  writes.

Use the firmware slots when you can modify firmware; use the proxy when you
cannot. See [`deployment.md`](deployment.md).

## 2.4 Worked example — a companion frame

`CMD_GET_DEVICE_TIME` (5) with no arguments, client → node:

```
3C 01 00 05
^  ^^^^^ ^
|  |     +-- payload: CMD_GET_DEVICE_TIME
|  +-------- length = 1, little-endian
+----------- marker '<' (0x3C)
```

The node's reply carries `RESP_CODE_CURR_TIME` (9) and a `uint32` timestamp:

```
3E 05 00 09 80 5A 7E 6A
^  ^^^^^ ^  ^^^^^^^^^^^
|  |     |  +----------- uint32 LE timestamp
|  |     +-------------- payload: RESP_CODE_CURR_TIME
|  +-------------------- length = 5
+----------------------- marker '>' (0x3E)
```

And the handshake the proxy sends on connect. `mc_proxy.py` builds it as
`frame(bytes([CMD_APP_START, 0x03]) + b"      " + b"mcproxy")` — command byte,
`0x03`, six spaces, then the app name. That is 15 payload bytes:

```
3C 0F 00 01 03 20 20 20 20 20 20 6D 63 70 72 6F 78 79
^  ^^^^^ ^  ^  ^^^^^^^^^^^^^^^^^ ^^^^^^^^^^^^^^^^^^^
|  |     |  |  6 spaces           "mcproxy"
|  |     |  +-- 0x03
|  |     +----- CMD_APP_START
|  +----------- length = 15
+-------------- marker '<'
```

> The **meaning** of the argument layout — `0x03` as a protocol version, the
> six-space field, the trailing app name — is inferred from this one call site.
> It was not read from `MyMesh.cpp`'s `CMD_APP_START` handler, so treat the field
> semantics as **unverified**. The byte sequence itself is exactly what the proxy
> sends and is known to work.

---

## Version drift

The wire format in part 1 has been stable across the v1.1x series. The companion
codes in part 2 have not: the list above contains gaps (44–49, 53) where codes
were removed, entries marked "v8+" that older firmware rejects, `_V3` response
variants added alongside their predecessors, and at least one renamed constant
(`CMD_GET_BATT_AND_STORAGE`, formerly `CMD_GET_BATTERY_VOLTAGE`).

Before relying on any specific code, check it against
`examples/companion_radio/MyMesh.cpp` in the firmware version you are actually
running. `CMD_APP_START` carries a protocol version byte for exactly this reason.

## Planned

The following is in development and **not** part of the shipped protocol:

- **Raw packet forwarding over MQTT.** The node hex-encodes each received
  over-the-air frame and publishes it, so a server can apply part 1 of this
  document without the firmware needing to parse anything. See
  [`mqtt.md`](mqtt.md).
