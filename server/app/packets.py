"""Decoder for raw MeshCore LoRa packets.

MeshCore nodes can mirror every packet they hear to MQTT as a hex blob. Nothing
in that blob is self-describing, so this module holds the only written-down copy
of the wire format we have; it was reverse-engineered from the firmware and is
expensive knowledge to re-acquire. Keep the layout documentation below in sync
with the firmware if it ever changes.

Wire format
-----------
::

    byte 0   header
               bits 0-1  route type   0 = TRANSPORT_FLOOD
                                      1 = FLOOD
                                      2 = DIRECT
                                      3 = TRANSPORT_DIRECT
               bits 2-5  payload type (see PAYLOAD_NAMES)
               bits 6-7  protocol version (only 0 exists today)

    if route type is 0 or 3:
      bytes 1-4   two transport codes, uint16 little-endian each

    next byte  path descriptor
               bits 6-7  hash size - 1, so size = (byte >> 6) + 1
               bits 0-5  number of hashes = byte & 63
               followed by (count * size) bytes of path hashes

    remainder  payload, interpreted per payload type

What the firmware refuses
-------------------------
These bytes do not arrive pre-validated. The node mirrors them from
``logRxRaw()``, which ``Dispatcher::checkRecv()`` calls on *everything* the radio
hands up (``src/Dispatcher.cpp`` line 199) -- and only afterwards runs
``tryParsePacket()`` (line 205) and drops the frame if it fails. So this feed
contains frames no MeshCore node ever accepted, and a decoder that is more
permissive than the firmware will present them as fact. The admission rules
below are therefore the firmware's own, from ``tryParsePacket()`` and
``Packet::isValidPathLen()``:

- payload version above ``PAYLOAD_VER_1`` (0) -- rejected before anything else
- path hash size 4, i.e. descriptor bits 6-7 = 3 -- reserved for future use
- ``count * size`` above ``MAX_PATH_SIZE`` (64)
- a path that does not fit inside the frame
- a payload above ``MAX_PACKET_PAYLOAD`` (184)

Each one is reported as an ``error`` with whatever was certain before it, and
never guessed past: a wrong hash size shifts every byte after the descriptor,
so continuing would invent a path, a payload boundary and an address hash all at
once.

Hash sizes: two different fields
--------------------------------
The frame carries two things called a hash, and they are set by different rules.
Confusing them is the reason a node can look "1-byte" on a mesh whose repeaters
are configured for 2.

``path`` hop entries
    1, 2 or 3 bytes, **per packet**, from the descriptor's top two bits. The
    *originator* picks the size from its own ``hash_mode`` preference
    (``path_hash_mode``, ``src/helpers/CommonCLI.h`` line 68; used at
    ``examples/simple_repeater/MyMesh.cpp`` lines 204, 1312 and 1777), and every
    forwarder keeps it -- ``Mesh::routeRecvPacket()`` appends its own hash at
    exactly ``getPathHashSize()`` bytes. So it is a property of the node that
    *sent* the packet, not of the mesh, and it is readable from the frame:
    ``path_hash_size``.

``dest_hash`` / ``src_hash`` in the payload
    Always **one** byte, whatever the path uses. They are written with the
    compile-time constant ``PATH_HASH_SIZE`` = 1 (``src/Mesh.cpp`` line 462,
    ``src/Identity.h`` lines 19-26), and the header's payload version is what
    would ever change that: ``PAYLOAD_VER_1`` is documented as "1-byte src/dest
    hashes, 2-byte MAC" and ``PAYLOAD_VER_2`` as a future "eg. 2-byte hashes,
    4-byte MAC" (``src/Packet.h`` lines 34-37). No firmware implements version
    2, and ``tryParsePacket()`` rejects it. A repeater set to 2-byte path hashes
    therefore still addresses its peers with one byte, and saying so is not the
    decoder being pessimistic -- it is the wire format.

ADVERT payload::

    pubkey(32) + timestamp(uint32 LE) + signature(64) + app_data

    app_data starts with a flags byte; the optional fields that follow appear in
    this exact order, only when their flag bit is set:
      flags & 0x0f  node type: 1 = chat, 2 = repeater, 3 = room, 4 = sensor
      flags & 0x10  lat(int32 LE) and lon(int32 LE), both in microdegrees
      flags & 0x20  feat1 (uint16 LE)
      flags & 0x40  feat2 (uint16 LE)
      flags & 0x80  name: all remaining bytes, UTF-8

Scoping
-------
The two transport codes are how MeshCore keeps flood traffic inside a region, so
their mere presence already answers "was this packet scoped?". That is what
``scope`` reports, and it is decided by the route type alone -- an unscoped
packet has no room on the wire for the codes at all:

``unscoped``
    Route type FLOOD or DIRECT. No transport codes on the wire at all. For a
    FLOOD that means what it says: nothing holds the packet inside a region. For
    a DIRECT packet it means only that the field is absent -- a direct packet is
    source-routed along an explicit hop list rather than flooded, and the
    firmware does not even ask which region it belongs to
    (``MyMesh::onRecvPacket()`` sets ``recv_pkt_region = NULL`` for every
    non-flood route, ``examples/simple_repeater/MyMesh.cpp`` line 794).
``scoped``
    Route type TRANSPORT_FLOOD or TRANSPORT_DIRECT, with at least one non-zero
    code. The sender restricted it to a region.
``share``
    Both codes zero. Not a region but a marker: ``isShare()`` in the repeater
    firmware reads codes {0, 0} as "send to nowhere", the shape an advert has
    when it was imported through the app's Share function instead of being heard
    off the air. The repeater keeps such an advert out of its neighbour table for
    that reason, and lumping it in with real scoped traffic here would hide the
    one case where a zero-hop advert does *not* mean "this node is in range".

Which region a scoped packet belongs to is **not** in the frame, and this module
does not guess at it:

- ``transport_codes[0]`` is ``TransportKey::calcTransportCode(pkt)`` -- a MAC
  over the packet computed with the 16-byte scope key, so it differs for every
  packet sent under the very same key. It identifies no region by itself; it can
  only be recognised by a node that holds the key and recomputes it.
- ``transport_codes[1]`` is reserved for the sender's home region and is the only
  field that could name one. The companion firmware writes a literal zero there
  (``codes[1] = 0;  // REVISIT: set to 'home' Region``), so in practice it is
  almost always absent. It is reported as ``scope_region`` when it is not zero,
  as the bare number it is -- the number-to-name table lives in the mesh's region
  map, not on the wire.

Naming the region of a scoped packet therefore has to be done by a node that has
the scope keys, and published alongside the frame. See ``docs/protocol.md``
§1.3.

A mostly-unscoped mesh is the expected reading, not a decoder failure. Two
firmware facts settle it. First, forwarding never adds a scope:
``Mesh::routeRecvPacket()`` appends a path hash and nothing else, and
``Dispatcher::checkSend()`` re-emits ``transport_codes`` byte for byte, so the
codes on a frame are the *originator's*, however many hops back that was.
Second, a repeater's own default region only reaches packets it originates
(``sendFloodScoped(default_scope, ...)``) and replies whose request was already
scoped (``MyMesh::sendFloodReply()``, ``examples/simple_repeater/MyMesh.cpp``
line 642). Configuring a region on one node therefore cannot make anyone else's
unscoped floods look scoped, and the proportion of ``unscoped`` here is a
measurement of the mesh rather than of this module.

Design rules
------------
Pure functions, no I/O. ``decode`` never raises: radio noise and firmware
mismatches routinely produce truncated or nonsensical frames, and one bad packet
must not be able to take down the MQTT subscriber. Whatever could be parsed with
certainty is returned; the rest is simply absent, with a note in ``error``.
"""
import hashlib

ROUTE_NAMES = {0: "TRANSPORT_FLOOD", 1: "FLOOD", 2: "DIRECT", 3: "TRANSPORT_DIRECT"}

PAYLOAD_NAMES = {
    0: "REQ", 1: "RESPONSE", 2: "TXT_MSG", 3: "ACK", 4: "ADVERT", 5: "GRP_TXT",
    6: "GRP_DATA", 7: "ANON_REQ", 8: "PATH", 9: "TRACE", 10: "MULTIPART",
    11: "CONTROL", 15: "RAW_CUSTOM",
}

PAYLOAD_ADVERT = 4
PAYLOAD_ANON_REQ = 7

# Payload types that begin with dest_hash(1) + src_hash(1) + MAC(2); see
# docs/protocol.md 1.6. GRP_* start with a channel hash and ACK with a CRC, so
# neither carries anything resembling a sender identity.
_HASHED_PEER_TYPES = (0, 1, 2, 8)   # REQ, RESPONSE, TXT_MSG, PATH

ADVERT_NODE_TYPES = {1: "chat", 2: "repeater", 3: "room", 4: "sensor"}

# The firmware's own limits, from src/MeshCore.h. They are what separates a
# frame a node would have accepted from one it dropped -- and this feed carries
# both, because the raw mirror runs before the parser. See "What the firmware
# refuses" above.
MAX_PATH_SIZE = 64
MAX_PACKET_PAYLOAD = 184
MAX_ADVERT_DATA_SIZE = 32

# pubkey(32) + timestamp(4) + signature(64) precede the app_data block
_ADVERT_APP_DATA_OFFSET = 100

# Contacts are keyed on the first three pubkey bytes throughout the app, so
# advert senders are reported in that same 6-hex-character form.
_SENDER_PREFIX_BYTES = 3


def decode(raw: bytes) -> dict:
    """Parse one raw packet into a plain dict. Never raises."""
    out: dict = {"len": len(raw or b""), "ok": False}
    try:
        _decode_into(bytes(raw or b""), out)
    except Exception as err:  # noqa: BLE001 - a corrupt packet is data, not a bug
        out["error"] = f"{type(err).__name__}: {err}"
    return out


def _decode_into(raw: bytes, out: dict) -> None:
    if not raw:
        out["error"] = "empty packet"
        return

    header = raw[0]
    route = header & 0x03
    payload_type = (header >> 2) & 0x0F
    version = (header >> 6) & 0x03
    out["route"] = route
    out["route_name"] = ROUTE_NAMES[route]
    out["payload_type"] = payload_type
    out["payload_name"] = PAYLOAD_NAMES.get(payload_type, f"TYPE{payload_type}")
    out["version"] = version
    if version != 0:
        # A newer version may lay out everything after the header differently,
        # so guessing on would produce plausible-looking nonsense.
        out["error"] = f"unsupported protocol version {version}"
        return

    pos = 1
    if route in (0, 3):
        if len(raw) < pos + 4:
            # The route type promises transport codes, so scoping is in play --
            # but not which kind, and "scoped" and "share" are different answers.
            # Leave scope absent rather than pick one; the error says why.
            out["error"] = "truncated transport codes"
            return
        codes = [
            int.from_bytes(raw[pos:pos + 2], "little"),
            int.from_bytes(raw[pos + 2:pos + 4], "little"),
        ]
        out["transport_codes"] = codes
        out["scope"] = "share" if codes == [0, 0] else "scoped"
        if codes[1]:
            out["scope_region"] = codes[1]
        pos += 4
    else:
        out["scope"] = "unscoped"

    if len(raw) <= pos:
        out["error"] = "missing path descriptor"
        return
    descriptor = raw[pos]
    pos += 1
    hash_size = (descriptor >> 6) + 1
    hash_count = descriptor & 0x3F
    path_bytes = hash_count * hash_size
    # Everything after this byte is positioned by it, so a descriptor the
    # firmware would have refused is not a field to report but a reason to stop:
    # nothing further in the frame is at a known offset. Neither number is
    # published in that case -- a path length read out of a byte we do not trust
    # is a guess wearing a number's clothes.
    if hash_size > 3:
        # Packet::isValidPathLen(): "if (hash_size == 4) return false", and
        # tryParsePacket() refuses path mode 3 outright. Reserved for a future
        # protocol nobody speaks yet.
        out["error"] = "reserved path hash size 4"
        return
    if path_bytes > MAX_PATH_SIZE:
        out["error"] = f"path of {path_bytes} bytes exceeds MAX_PATH_SIZE"
        return
    if len(raw) < pos + path_bytes:
        out["error"] = "truncated path"
        return
    out["path_len"] = hash_count
    # Which of 1, 2 or 3 the originator chose. Worth reporting rather than
    # consuming silently: it is the difference between "this hop names one of
    # 256 nodes" and "one of 65536", and a reader shown a bare two-hex-character
    # hop has no way to tell which of those it is looking at.
    out["path_hash_size"] = hash_size
    out["path"] = [
        raw[pos + i * hash_size:pos + (i + 1) * hash_size].hex()
        for i in range(hash_count)
    ]
    pos += path_bytes

    payload = raw[pos:]
    if len(payload) > MAX_PACKET_PAYLOAD:
        # tryParsePacket() drops the frame here too. On a wire capped at 255
        # bytes this only happens when the header lied or the bytes are noise.
        out["error"] = f"payload of {len(payload)} bytes exceeds MAX_PACKET_PAYLOAD"
        return
    out["payload_len"] = len(payload)
    # Hash the payload rather than the whole frame: a flooded packet gains path
    # hashes and transport codes at every hop, so only the payload stays stable
    # across repeats of what is really the same message.
    out["hash"] = hashlib.sha1(bytes([payload_type]) + payload).hexdigest()[:16]
    out["ok"] = True

    if payload_type == PAYLOAD_ADVERT:
        _decode_advert(payload, out)
    elif payload_type in _HASHED_PEER_TYPES and len(payload) >= 2:
        # REQ, RESPONSE, TXT_MSG and PATH open with a destination and a source
        # hash, one byte each -- fixed by PATH_HASH_SIZE and by the header's
        # payload version, and unaffected by the size the path happens to use.
        # See "Hash sizes: two different fields" above; the two are routinely
        # confused, and a node whose path hashes are two bytes still addresses
        # its peers with one. One byte identifies nobody on its own -- 256
        # buckets against a whole mesh -- but matched against a known contact
        # list it is usually enough, and that matching is the reader's job, not
        # this decoder's.
        out["dest_hash"] = payload[:1].hex()
        out["src_hash"] = payload[1:2].hex()
    elif payload_type == PAYLOAD_ANON_REQ and len(payload) >= 1:
        # ANON_REQ names only its destination; the source is an ephemeral key
        # that intentionally matches no contact.
        out["dest_hash"] = payload[:1].hex()


def _decode_advert(payload: bytes, out: dict) -> None:
    """Pull identity, position and name out of an ADVERT payload."""
    if len(payload) < _ADVERT_APP_DATA_OFFSET:
        out["error"] = "truncated advert"
        return
    out["pubkey"] = payload[:32].hex()
    out["sender"] = payload[:_SENDER_PREFIX_BYTES].hex()
    out["advert_ts"] = int.from_bytes(payload[32:36], "little")

    # Only the first MAX_ADVERT_DATA_SIZE bytes of app_data exist as far as the
    # mesh is concerned: Mesh::onRecvPacket() clamps app_data_len to 32 before
    # it verifies the Ed25519 signature, and AdvertDataParser never sees more.
    # Anything past that is outside the signature, so reading a name from it
    # would show text no node ever checked and any forwarder could have added.
    app = payload[_ADVERT_APP_DATA_OFFSET:_ADVERT_APP_DATA_OFFSET + MAX_ADVERT_DATA_SIZE]
    if not app:
        return  # a bare advert without app_data is valid, just uninformative
    flags = app[0]
    out["node_type"] = ADVERT_NODE_TYPES.get(flags & 0x0F)
    pos = 1

    if flags & 0x10:
        if len(app) < pos + 8:
            out["error"] = "truncated advert position"
            return
        lat = int.from_bytes(app[pos:pos + 4], "little", signed=True) / 1e6
        lon = int.from_bytes(app[pos + 4:pos + 8], "little", signed=True) / 1e6
        pos += 8
        # Firmware sends 0/0 when no position is configured; that is the Atlantic
        # Ocean on a map, so treat it as "unknown" instead of plotting it.
        if (lat or lon) and -90 <= lat <= 90 and -180 <= lon <= 180:
            out["lat"] = round(lat, 6)
            out["lon"] = round(lon, 6)

    for flag, key in ((0x20, "feat1"), (0x40, "feat2")):
        if flags & flag:
            if len(app) < pos + 2:
                out["error"] = f"truncated advert {key}"
                return
            out[key] = int.from_bytes(app[pos:pos + 2], "little")
            pos += 2

    if flags & 0x80:
        name = app[pos:].decode("utf-8", "replace").split("\x00", 1)[0].strip()
        if name:
            out["name"] = name[:64]
