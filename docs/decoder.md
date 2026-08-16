# The packet decoder

*[Nederlands](nl/decoder.md)*

`server/app/packets.py` turns a raw MeshCore frame into a dict. It is a pure
function with no I/O, and it holds the only written-down copy of the wire format
this project has — reverse-engineered from the firmware, and expensive knowledge
to re-acquire.

[`protocol.md`](protocol.md) specifies the format itself. This document is about
what the *decoder* extracts, what it refuses, and why it refuses it.

## `decode()` never raises

```python
pkt = packets.decode(frame_bytes)
```

Radio noise and firmware mismatches routinely produce truncated or nonsensical
frames, and one bad packet must not be able to take down the MQTT subscriber.
So: whatever could be parsed **with certainty** is returned, the rest is simply
absent, and `error` holds a note about what stopped it. The dict always has at
least `len` and `ok`.

| Key | Present when | Contents |
|---|---|---|
| `len` | always | Frame length in bytes |
| `ok` | always | True once the header, path and payload boundary are all certain |
| `error` | on any refusal | Why parsing stopped |
| `route`, `route_name` | header read | 0–3 / `TRANSPORT_FLOOD`, `FLOOD`, `DIRECT`, `TRANSPORT_DIRECT` |
| `payload_type`, `payload_name` | header read | 0–15 / `ADVERT`, `TXT_MSG`, … or `TYPE<n>` |
| `version` | header read | Protocol version, 0–3 |
| `transport_codes` | scoped routes | `[code0, code1]`, uint16 LE each |
| `scope` | route type known | `unscoped`, `scoped` or `share` |
| `scope_region` | `codes[1] != 0` | The region number, bare |
| `path_len` | descriptor accepted | Number of hop hashes |
| `path_hash_size` | descriptor accepted | 1, 2 or 3 bytes per hop |
| `path` | descriptor accepted | List of hex strings |
| `payload_len` | payload accepted | Bytes after the path |
| `hash` | payload accepted | 16 hex characters, see below |
| `dest_hash`, `src_hash` | payload type carries them | Two hex characters each |
| `pubkey`, `sender`, `advert_ts` | ADVERT | Full key, first 3 bytes, node timestamp |
| `node_type`, `lat`, `lon`, `feat1`, `feat2`, `name` | ADVERT, per flag | Advert app_data |

`payload_name` falls back to `TYPE<n>` rather than being dropped: a payload type
this firmware does not know is still a fact about the mesh.

## The five admission rules

These bytes do **not** arrive pre-validated. The node mirrors them from
`logRxRaw()`, which `Dispatcher::checkRecv()` calls on *everything* the radio
hands up (`src/Dispatcher.cpp` line 199) — and only afterwards runs
`tryParsePacket()` (line 205) and drops the frame if it fails.

So this feed contains frames no MeshCore node ever accepted, and a decoder more
permissive than the firmware would present them as fact. The rules below are
therefore the firmware's own, from `tryParsePacket()` and
`Packet::isValidPathLen()`:

| # | Refusal | `error` | Source |
|---|---|---|---|
| 1 | Payload version above `PAYLOAD_VER_1` (0) | `unsupported protocol version <n>` | Rejected before anything else |
| 2 | Path hash size 4 (descriptor bits 6–7 = 3) | `reserved path hash size 4` | `isValidPathLen()`: "if (hash_size == 4) return false" |
| 3 | `count × size` above `MAX_PATH_SIZE` (64) | `path of <n> bytes exceeds MAX_PATH_SIZE` | `MeshCore.h` |
| 4 | A path that does not fit inside the frame | `truncated path` | |
| 5 | A payload above `MAX_PACKET_PAYLOAD` (184) | `payload of <n> bytes exceeds MAX_PACKET_PAYLOAD` | `MeshCore.h` |

Each one is reported with whatever was certain **before** it, and never guessed
past. A wrong hash size shifts every byte after the descriptor, so continuing
would invent a path, a payload boundary and an address hash all at once.

Rule 2 has a second consequence worth spelling out: when the descriptor is
refused, **neither** `path_len` nor `path_hash_size` is published. A path length
read out of a byte we do not trust is a guess wearing a number's clothes.

Two more truncation refusals sit outside that table, for the same reason:
`truncated transport codes` (the route type promises codes and the frame is too
short — leaving `scope` absent rather than picking between `scoped` and `share`)
and `missing path descriptor`.

## Wire layout, as the decoder walks it

```
byte 0   header
           bits 0-1  route type   0 = TRANSPORT_FLOOD
                                  1 = FLOOD
                                  2 = DIRECT
                                  3 = TRANSPORT_DIRECT
           bits 2-5  payload type
           bits 6-7  protocol version (only 0 exists today)

if route type is 0 or 3:
  bytes 1-4   two transport codes, uint16 little-endian each

next byte  path descriptor
           bits 6-7  hash size - 1, so size = (byte >> 6) + 1
           bits 0-5  number of hashes = byte & 63
           followed by (count * size) bytes of path hashes

remainder  payload, interpreted per payload type
```

## Hash sizes: two different fields

The frame carries two things called a hash and they are set by different rules.
Confusing them is why a node can look "1-byte" on a mesh whose repeaters are
configured for 2.

**Path hop entries** are 1, 2 or 3 bytes, **per packet**, from the descriptor's
top two bits. The *originator* picks the size from its own `hash_mode`
preference (`path_hash_mode`, `src/helpers/CommonCLI.h` line 68) and every
forwarder keeps it — `Mesh::routeRecvPacket()` appends its own hash at exactly
`getPathHashSize()` bytes. So it is a property of the node that **sent** the
packet, not of the mesh, and it is readable from the frame as `path_hash_size`.

**`dest_hash` and `src_hash` in the payload** are always **one** byte, whatever
the path uses. They are written with the compile-time constant `PATH_HASH_SIZE`
= 1 (`src/Mesh.cpp` line 462, `src/Identity.h` lines 19–26). The header's
payload version is what would ever change that: `PAYLOAD_VER_1` is documented as
"1-byte src/dest hashes, 2-byte MAC" and `PAYLOAD_VER_2` as a future "eg. 2-byte
hashes, 4-byte MAC" (`src/Packet.h` lines 34–37). No firmware implements
version 2, and rule 1 above rejects it. A repeater set to 2-byte path hashes
therefore still addresses its peers with one byte, and saying so is not the
decoder being pessimistic — it is the wire format.

`path_hash_size` is reported rather than consumed silently because it is the
difference between "this hop names one of 256 nodes" and "one of 65 536", and a
reader shown a bare two-hex-character hop has no way to tell which they are
looking at. It is the reason `GET /api/v1/packets/{id}` returns it and returns
`null` when the frame was not kept.

## Address hashes per payload type

| Payload types | What is taken |
|---|---|
| `REQ` (0), `RESPONSE` (1), `TXT_MSG` (2), `PATH` (8) | `dest_hash` = byte 0, `src_hash` = byte 1 |
| `ANON_REQ` (7) | `dest_hash` only — the source is an ephemeral key that intentionally matches no contact |
| `ADVERT` (4) | Neither; the payload names its sender in full |
| `GRP_TXT` (5), `GRP_DATA` (6) | Neither; they start with a channel hash |
| `ACK` (3) | Neither; it starts with a CRC |
| everything else | Neither |

`_HASHED_PEER_TYPES = (0, 1, 2, 8)` is the list, and it exists because those four
begin with `dest_hash(1) + src_hash(1) + MAC(2)` — see
[`protocol.md`](protocol.md#16-payload-layouts-by-type) §1.6.

One byte identifies nobody on its own: 256 buckets against a whole mesh. Matched
against a known contact list it is usually enough, and that matching is the
reader's job, not the decoder's — see [`candidates.md`](candidates.md).

## Scoping

The two transport codes are how MeshCore keeps flood traffic inside a region, so
their **mere presence** already answers "was this packet scoped?". That is what
`scope` reports, and it is decided by the route type alone:

| `scope` | When | What it means |
|---|---|---|
| `unscoped` | Route type FLOOD or DIRECT | No transport codes on the wire at all |
| `scoped` | TRANSPORT_FLOOD / TRANSPORT_DIRECT, at least one code non-zero | The sender restricted it to a region |
| `share` | Both codes zero | Not a region but a marker |

For a FLOOD, `unscoped` means what it says: nothing holds the packet inside a
region. For a DIRECT it means only that the field is absent — a direct packet is
source-routed along an explicit hop list rather than flooded, and the firmware
does not even ask which region it belongs to (`MyMesh::onRecvPacket()` sets
`recv_pkt_region = NULL` for every non-flood route,
`examples/simple_repeater/MyMesh.cpp` line 794).

`share` is `isShare()` in the repeater firmware: codes `{0, 0}` read as "send to
nowhere", the shape an advert has when it was imported through the app's Share
function instead of being heard off the air. The repeater keeps such an advert
out of its neighbour table for that reason, and lumping it in with real scoped
traffic here would hide the one case where a zero-hop advert does **not** mean
"this node is in range".

### Which region — and why the decoder does not guess

Not in the frame, and this module does not invent it:

- `transport_codes[0]` is `TransportKey::calcTransportCode(pkt)` — a MAC over
  the packet computed with the 16-byte scope key, so it differs for every packet
  sent under the very same key. It identifies no region by itself; it can only
  be recognised by a node that holds the key and recomputes it.
- `transport_codes[1]` is reserved for the sender's home region and is the only
  field that could name one. The companion firmware writes a literal zero there
  (`codes[1] = 0;  // REVISIT: set to 'home' Region`), so in practice it is
  almost always absent. It is reported as `scope_region` when it is not zero, as
  the bare number it is — the number-to-name table lives in the mesh's region
  map, not on the wire.

Naming the region of a scoped packet therefore has to be done by a node that has
the scope keys, and published alongside the frame. See
[`protocol.md`](protocol.md#13-transport-codes) §1.3.

### A mostly-unscoped mesh is the expected reading

Two firmware facts settle it, and they are worth keeping because "why is
everything unscoped?" is the first question anyone asks of the scope column.

**Forwarding never adds a scope.** `Mesh::routeRecvPacket()` appends a path hash
and nothing else, and `Dispatcher::checkSend()` re-emits `transport_codes` byte
for byte. The codes on a frame are the *originator's*, however many hops back
that was.

**A repeater's own default region only reaches packets it originates**
(`sendFloodScoped(default_scope, …)`) and replies whose request was already
scoped (`MyMesh::sendFloodReply()`,
`examples/simple_repeater/MyMesh.cpp` line 642).

Configuring a region on one node therefore cannot make anyone else's unscoped
floods look scoped, and the proportion of `unscoped` in the archive is a
measurement of the mesh rather than of this module.

## The payload hash

```python
out["hash"] = hashlib.sha1(bytes([payload_type]) + payload).hexdigest()[:16]
```

The **payload**, not the whole frame — because a flooded packet gains path
hashes and transport codes at every hop, so only the payload stays stable across
repeats of what is really the same message. The payload type is prepended so two
different types cannot collide on identical bytes.

That stability is what makes `db.insert_packet()`'s de-duplication work: the
same `(observer, phash)` within `PACKET_DUP_WINDOW_S` (60 s) is one reception,
not several. Without it the live map would show a flooded advert once per node
in range.

## ADVERT

```
pubkey(32) + timestamp(uint32 LE) + signature(64) + app_data
```

`_ADVERT_APP_DATA_OFFSET` is therefore 100. `sender` is the first
`_SENDER_PREFIX_BYTES` = 3 bytes of the key, in hex — six characters, because
contacts are keyed on that form throughout the app.

`app_data` starts with a flags byte; the optional fields that follow appear in
this exact order, only when their bit is set:

| Bit | Field |
|---|---|
| `flags & 0x0f` | Node type: 1 = chat, 2 = repeater, 3 = room, 4 = sensor |
| `flags & 0x10` | `lat` and `lon`, int32 LE each, in microdegrees |
| `flags & 0x20` | `feat1`, uint16 LE |
| `flags & 0x40` | `feat2`, uint16 LE |
| `flags & 0x80` | `name`: all remaining bytes, UTF-8 |

Three details that are not obvious:

**Only the first `MAX_ADVERT_DATA_SIZE` (32) bytes of `app_data` exist** as far
as the mesh is concerned. `Mesh::onRecvPacket()` clamps `app_data_len` to 32
before it verifies the Ed25519 signature, and `AdvertDataParser` never sees
more. Anything past that is **outside the signature**, so reading a name from it
would show text no node ever checked and any forwarder could have added.

**0/0 is not a position.** Firmware sends it when none is configured, and that
is the Atlantic Ocean on a map, so it is treated as unknown rather than plotted.
Coordinates outside ±90 / ±180 are dropped the same way.

**The name is truncated at 64 characters** and cut at the first NUL, decoded
with `errors="replace"`. A bare advert without `app_data` is valid, just
uninformative, and returns early.

## What the ingest path does with the result

`db.insert_packet()` writes the decoded fields into `packets`, and does one more
thing: when the packet is an ADVERT with a `pubkey`, it calls
`db.upsert_advert()` to refresh the `contacts` table. That is what later lets
the live map place a packet at all — the decoder feeds the identity store, and
the identity store feeds every resolution the site performs.

## Tests

`server/tests/test_packets.py` covers this module: scope classification, address
hashes per payload type, ADVERT fields, path parsing and every truncation.
`server/tests/frames.py` builds MeshCore frames from the protocol knowledge in
`docs/protocol.md` §1; **there is not one real, captured packet in the test
directory**.

## Related documents

| Question | Document |
|---|---|
| The wire format itself, byte by byte | [`protocol.md`](protocol.md) |
| Which node a hash belongs to | [`candidates.md`](candidates.md) |
| Where the decoded fields are stored | [`database.md`](database.md#packets) |
| How a decoded packet is served | [`api.md`](api.md#get-apiv1packetspacket_id) |
