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
    Route type FLOOD or DIRECT. No transport codes; the packet floods wherever
    it reaches.
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
    out["path_len"] = hash_count
    path_bytes = hash_count * hash_size
    if len(raw) < pos + path_bytes:
        out["error"] = "truncated path"
        return
    out["path"] = [
        raw[pos + i * hash_size:pos + (i + 1) * hash_size].hex()
        for i in range(hash_count)
    ]
    pos += path_bytes

    payload = raw[pos:]
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
        # hash: one byte each under PAYLOAD_VER_1, whatever hash size the path
        # uses. One byte identifies nobody on its own -- 256 buckets against a
        # whole mesh -- but matched against a known contact list it is usually
        # enough, and that matching is the reader's job, not this decoder's.
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

    app = payload[_ADVERT_APP_DATA_OFFSET:]
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
