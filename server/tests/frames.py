"""Bouwstenen voor zelfgemaakte MeshCore-frames.

Elke hex in de tests is met deze functies opgebouwd uit de wire-format-kennis
in ``docs/protocol.md`` §1. Er zit geen enkel echt, van de radio opgevangen
pakket tussen: sleutels en handtekeningen zijn herkenbare vulpatronen, posities
en namen zijn verzonnen.
"""

ROUTE_TRANSPORT_FLOOD = 0
ROUTE_FLOOD = 1
ROUTE_DIRECT = 2
ROUTE_TRANSPORT_DIRECT = 3

TYPE_REQ = 0
TYPE_RESPONSE = 1
TYPE_TXT_MSG = 2
TYPE_ACK = 3
TYPE_ADVERT = 4
TYPE_ANON_REQ = 7
TYPE_PATH = 8

# Een duidelijk synthetische identiteit: de pubkey telt gewoon van 1 tot 32,
# de handtekening is 64 keer hetzelfde vulbyte. Geen echte sleutel heeft die
# vorm, en de decoder controleert handtekeningen toch niet.
PUBKEY = bytes(range(1, 33))
SIGNATURE = b"\xab" * 64


def frame(route: int, ptype: int, *, version: int = 0,
          codes: tuple[int, int] | None = None,
          hops: tuple[bytes, ...] = (), hash_size: int = 1,
          payload: bytes = b"") -> bytes:
    """Zet een compleet frame in elkaar volgens docs/protocol.md §1.1.

    ``codes`` moet meegegeven worden bij route 0 en 3 en weggelaten bij 1 en 2;
    dat dwingt deze functie niet af, want de truncatietests bouwen juist frames
    die die belofte breken.
    """
    header = bytes([(version << 6) | (ptype << 2) | route])
    transport = b""
    if codes is not None:
        transport = (codes[0].to_bytes(2, "little")
                     + codes[1].to_bytes(2, "little"))
    descriptor = bytes([((hash_size - 1) << 6) | len(hops)])
    return header + transport + descriptor + b"".join(hops) + payload


def advert_payload(*, pubkey: bytes = PUBKEY, timestamp: int = 1_700_000_000,
                   node_type: int | None = None,
                   lat: float | None = None, lon: float | None = None,
                   feat1: int | None = None, feat2: int | None = None,
                   name: str | None = None) -> bytes:
    """ADVERT-payload: pubkey(32) + timestamp(4 LE) + signature(64) + app_data.

    De app_data volgt de vlaggenbyte-indeling uit docs/protocol.md: elk veld is
    alleen aanwezig als zijn vlag staat, in de vaste volgorde lat/lon, feat1,
    feat2, naam. Zonder enkel optioneel veld ontstaat een 'kale' advert van
    precies 100 bytes, wat de decoder als geldig moet aanvaarden.
    """
    fixed = pubkey + timestamp.to_bytes(4, "little") + SIGNATURE
    if (node_type is None and lat is None and feat1 is None
            and feat2 is None and name is None):
        return fixed

    flags = node_type or 0
    app = b""
    if lat is not None:
        flags |= 0x10
        app += int(lat * 1e6).to_bytes(4, "little", signed=True)
        app += int(lon * 1e6).to_bytes(4, "little", signed=True)
    if feat1 is not None:
        flags |= 0x20
        app += feat1.to_bytes(2, "little")
    if feat2 is not None:
        flags |= 0x40
        app += feat2.to_bytes(2, "little")
    if name is not None:
        flags |= 0x80
        app += name.encode("utf-8")
    return fixed + bytes([flags]) + app


def peer_payload(dest: int, src: int, blob: bytes = b"\x00" * 18) -> bytes:
    """Payload voor REQ/RESPONSE/TXT_MSG/PATH: dest_hash(1) + src_hash(1) +
    MAC(2) + ciphertext. De inhoud na de twee hashes is voor de decoder
    ondoorzichtig; 18 nulbytes volstaan als MAC-plus-één-cipherblok."""
    return bytes([dest, src]) + blob
