"""Tests voor de rauwe-pakketdecoder in app/packets.py.

De testvectoren zijn met tests/frames.py opgebouwd uit docs/protocol.md; geen
echte pakketten. Waar een vector uitleg behoeft staat die erbij.
"""
import frames
from app import packets


def test_advert_flood_volledig():
    # De klassieke vers uitgezonden advert: FLOOD heeft geen transportcodes,
    # dus scope moet 'unscoped' zijn, en de afzender is de enige payload die
    # zijn volledige identiteit meestuurt.
    raw = frames.frame(frames.ROUTE_FLOOD, frames.TYPE_ADVERT,
                       payload=frames.advert_payload(
                           node_type=2, lat=12.345678, lon=-98.765432,
                           name="Testnode Alfa"))
    out = packets.decode(raw)
    assert out["ok"] is True
    assert "error" not in out
    assert out["route_name"] == "FLOOD"
    assert out["payload_name"] == "ADVERT"
    assert out["scope"] == "unscoped"
    assert "transport_codes" not in out
    assert out["pubkey"] == frames.PUBKEY.hex()
    # Contacten worden overal op de eerste drie sleutelbytes geïndexeerd.
    assert out["sender"] == frames.PUBKEY[:3].hex()
    assert out["advert_ts"] == 1_700_000_000
    assert out["node_type"] == "repeater"
    assert out["lat"] == 12.345678
    assert out["lon"] == -98.765432
    assert out["name"] == "Testnode Alfa"


def test_advert_kaal_zonder_app_data():
    # Precies 100 bytes payload: pubkey + timestamp + handtekening, geen
    # app_data. Dat is volgens het protocol geldig, alleen niet informatief.
    raw = frames.frame(frames.ROUTE_FLOOD, frames.TYPE_ADVERT,
                       payload=frames.advert_payload())
    out = packets.decode(raw)
    assert out["ok"] is True
    assert "error" not in out
    assert out["pubkey"] == frames.PUBKEY.hex()
    assert "node_type" not in out
    assert "name" not in out


def test_advert_nulpositie_is_onbekend():
    # De firmware stuurt 0/0 wanneer geen positie is ingesteld; dat op een
    # kaart zetten zou midden in de Atlantische Oceaan prikken.
    raw = frames.frame(frames.ROUTE_FLOOD, frames.TYPE_ADVERT,
                       payload=frames.advert_payload(node_type=1,
                                                     lat=0.0, lon=0.0))
    out = packets.decode(raw)
    assert out["ok"] is True
    assert "lat" not in out
    assert "lon" not in out


def test_advert_feature_velden():
    raw = frames.frame(frames.ROUTE_FLOOD, frames.TYPE_ADVERT,
                       payload=frames.advert_payload(node_type=4,
                                                     feat1=0x0102,
                                                     feat2=0xBEEF))
    out = packets.decode(raw)
    assert out["node_type"] == "sensor"
    assert out["feat1"] == 0x0102
    assert out["feat2"] == 0xBEEF


def test_scope_scoped_met_transportcodes():
    # Transportcodes aanwezig en niet {0,0}: de afzender heeft het pakket tot
    # een regio beperkt. De codes staan little-endian op de draad, dus 0x1234
    # moet als de bytes 34 12 gelezen worden.
    raw = frames.frame(frames.ROUTE_TRANSPORT_FLOOD, frames.TYPE_TXT_MSG,
                       codes=(0x1234, 0),
                       payload=frames.peer_payload(0xAA, 0xBB))
    out = packets.decode(raw)
    assert out["ok"] is True
    assert out["scope"] == "scoped"
    assert out["transport_codes"] == [0x1234, 0]
    # codes[1] is nul, dus er is geen regio te melden.
    assert "scope_region" not in out


def test_scope_region_alleen_bij_tweede_code():
    # codes[1] is het enige veld dat een regio zou kunnen benoemen; alleen als
    # het niet nul is mag het als scope_region verschijnen.
    raw = frames.frame(frames.ROUTE_TRANSPORT_DIRECT, frames.TYPE_TXT_MSG,
                       codes=(5, 7),
                       payload=frames.peer_payload(0x01, 0x02))
    out = packets.decode(raw)
    assert out["scope"] == "scoped"
    assert out["scope_region"] == 7
    assert out["route_name"] == "TRANSPORT_DIRECT"


def test_scope_share_bij_codes_nul_nul():
    # Codes {0,0} zijn geen regio maar de vorm van een advert die via de
    # Share-functie is geïmporteerd in plaats van uit de lucht gehoord; de
    # decoder moet dat als eigen geval rapporteren, niet als 'scoped'.
    raw = frames.frame(frames.ROUTE_TRANSPORT_FLOOD, frames.TYPE_ADVERT,
                       codes=(0, 0),
                       payload=frames.advert_payload())
    out = packets.decode(raw)
    assert out["scope"] == "share"
    assert out["transport_codes"] == [0, 0]
    assert "scope_region" not in out


def test_scope_unscoped_bij_direct():
    # Route DIRECT heeft op de draad geen plaats voor transportcodes; het
    # pakket kan dus per definitie niet gescoped zijn.
    raw = frames.frame(frames.ROUTE_DIRECT, frames.TYPE_ACK,
                       payload=b"\x01\x02\x03\x04")
    out = packets.decode(raw)
    assert out["scope"] == "unscoped"
    assert "transport_codes" not in out


def test_src_en_dest_hash_op_peer_typen():
    # REQ, RESPONSE, TXT_MSG en PATH openen met dest_hash(1) + src_hash(1);
    # de decoder moet die twee bytes als hex teruggeven.
    for ptype in (frames.TYPE_REQ, frames.TYPE_RESPONSE,
                  frames.TYPE_TXT_MSG, frames.TYPE_PATH):
        raw = frames.frame(frames.ROUTE_FLOOD, ptype,
                           payload=frames.peer_payload(0xC3, 0xD4))
        out = packets.decode(raw)
        assert out["ok"] is True
        assert out["dest_hash"] == "c3", out["payload_name"]
        assert out["src_hash"] == "d4", out["payload_name"]


def test_ack_heeft_geen_hashes():
    # Een ACK begint met een CRC, niet met adreshashes; die vier bytes als
    # dest/src rapporteren zou een verzonnen afzender opleveren.
    raw = frames.frame(frames.ROUTE_FLOOD, frames.TYPE_ACK,
                       payload=b"\xde\xad\xbe\xef")
    out = packets.decode(raw)
    assert out["ok"] is True
    assert out["payload_name"] == "ACK"
    assert "dest_hash" not in out
    assert "src_hash" not in out


def test_anon_req_alleen_dest():
    # ANON_REQ noemt alleen zijn bestemming; de bron is een eenmalige sleutel
    # die bewust bij geen enkel contact hoort.
    raw = frames.frame(frames.ROUTE_FLOOD, frames.TYPE_ANON_REQ,
                       payload=bytes([0x5E]) + bytes(32) + bytes(18))
    out = packets.decode(raw)
    assert out["dest_hash"] == "5e"
    assert "src_hash" not in out


def test_pad_met_hops():
    raw = frames.frame(frames.ROUTE_FLOOD, frames.TYPE_ADVERT,
                       hops=(b"\xa7", b"\x3f"),
                       payload=frames.advert_payload())
    out = packets.decode(raw)
    assert out["path_len"] == 2
    assert out["path"] == ["a7", "3f"]


def test_pad_met_hashgrootte_twee():
    # De bovenste twee bits van de descriptor coderen de hashgrootte; bij
    # grootte 2 moeten er twee bytes per hop van de draad komen.
    raw = frames.frame(frames.ROUTE_FLOOD, frames.TYPE_ADVERT,
                       hops=(b"\xa7\x01", b"\x3f\x02"), hash_size=2,
                       payload=frames.advert_payload())
    out = packets.decode(raw)
    assert out["path_len"] == 2
    assert out["path"] == ["a701", "3f02"]


def test_hashgrootte_wordt_gerapporteerd():
    # De hashgrootte staat per pakket op de draad en wordt door de afzender
    # gekozen (hash_mode). Zonder dat getal ziet een lezer alleen 'a701' en kan
    # hij niet weten of dat één hop van twee bytes is of twee van één.
    for size in (1, 2, 3):
        hop = bytes(range(0xA0, 0xA0 + size))
        raw = frames.frame(frames.ROUTE_FLOOD, frames.TYPE_ACK,
                           hops=(hop,), hash_size=size,
                           payload=b"\x01\x02\x03\x04")
        out = packets.decode(raw)
        assert out["ok"] is True
        assert out["path_hash_size"] == size
        assert out["path"] == [hop.hex()]


def test_hashgrootte_vier_is_gereserveerd():
    # Packet::isValidPathLen() weigert hashgrootte 4 en tryParsePacket()
    # weigert padmodus 3: geen enkele node aanvaardt zo'n frame. De rauwe
    # spiegel op de node draait vóór die controle, dus zulke frames komen hier
    # wél binnen -- en doorlezen zou een pad, een payloadgrens en een
    # adreshash in één keer verzinnen.
    raw = frames.frame(frames.ROUTE_FLOOD, frames.TYPE_RESPONSE,
                       hops=(b"\xa7\x01\x02\x03",), hash_size=4,
                       payload=frames.peer_payload(0x11, 0x22))
    out = packets.decode(raw)
    assert out["ok"] is False
    assert out["error"] == "reserved path hash size 4"
    # Wat vóór de descriptor zeker was blijft staan...
    assert out["route_name"] == "FLOOD"
    assert out["payload_name"] == "RESPONSE"
    assert out["scope"] == "unscoped"
    # ...en alles wat de descriptor zou positioneren niet.
    assert "path_len" not in out
    assert "path_hash_size" not in out
    assert "path" not in out
    assert "src_hash" not in out
    assert "dest_hash" not in out


def test_pad_langer_dan_max_path_size():
    # count * size mag MAX_PATH_SIZE (64) niet overschrijden; de firmware
    # gooit het frame weg. 33 hops van 2 bytes is 66.
    raw = frames.frame(frames.ROUTE_FLOOD, frames.TYPE_ACK,
                       hops=tuple(b"\xa7\x01" for _ in range(33)), hash_size=2,
                       payload=b"\x01\x02\x03\x04")
    out = packets.decode(raw)
    assert out["ok"] is False
    assert out["error"] == "path of 66 bytes exceeds MAX_PATH_SIZE"
    assert "path" not in out


def test_payload_groter_dan_max_packet_payload():
    # tryParsePacket() weigert een payload boven MAX_PACKET_PAYLOAD (184).
    raw = frames.frame(frames.ROUTE_FLOOD, frames.TYPE_ACK,
                       payload=b"\x00" * 185)
    out = packets.decode(raw)
    assert out["ok"] is False
    assert "exceeds MAX_PACKET_PAYLOAD" in out["error"]
    # De payload van precies 184 is nog geldig.
    raw = frames.frame(frames.ROUTE_FLOOD, frames.TYPE_ACK,
                       payload=b"\x00" * 184)
    assert packets.decode(raw)["ok"] is True


def test_hashgrootte_verandert_de_adreshashes_niet():
    # Twee verschillende dingen heten 'hash'. De pad-hashgrootte is per pakket
    # instelbaar; dest/src in de payload zijn onder PAYLOAD_VER_1 altijd één
    # byte (PATH_HASH_SIZE). Een node met hash_mode 2 adresseert zijn peers dus
    # nog steeds met één byte.
    raw = frames.frame(frames.ROUTE_FLOOD, frames.TYPE_RESPONSE,
                       hops=(b"\xe3\xd3",), hash_size=2,
                       payload=frames.peer_payload(0x55, 0xE3))
    out = packets.decode(raw)
    assert out["path_hash_size"] == 2
    assert out["path"] == ["e3d3"]
    assert out["dest_hash"] == "55"
    assert out["src_hash"] == "e3"


def test_advert_app_data_stopt_op_max_advert_data_size():
    # Mesh::onRecvPacket() kapt app_data af op 32 bytes vóór het de
    # handtekening controleert. Bytes daarna vallen buiten de handtekening en
    # mogen dus niet in de getoonde naam terechtkomen.
    naam = "A" * 31                      # vlaggenbyte + 31 tekens = 32
    payload = frames.advert_payload(node_type=2, name=naam + "ONGETEKEND")
    out = packets.decode(frames.frame(frames.ROUTE_FLOOD, frames.TYPE_ADVERT,
                                      payload=payload))
    assert out["name"] == naam


def test_payloadhash_stabiel_over_hops_en_routes():
    # De dedup-hash dekt alleen type + payload: hetzelfde bericht dat via een
    # andere route of met een gegroeid pad binnenkomt moet dezelfde hash
    # krijgen, anders herkent de opslag herhalingen niet.
    payload = frames.advert_payload(name="Testnode Alfa")
    vers = frames.frame(frames.ROUTE_FLOOD, frames.TYPE_ADVERT,
                        payload=payload)
    na_twee_hops = frames.frame(frames.ROUTE_FLOOD, frames.TYPE_ADVERT,
                                hops=(b"\xa7", b"\x3f"), payload=payload)
    direct = frames.frame(frames.ROUTE_DIRECT, frames.TYPE_ADVERT,
                          payload=payload)
    hashes = {packets.decode(f)["hash"] for f in (vers, na_twee_hops, direct)}
    assert len(hashes) == 1


def test_onbekende_protocolversie_stopt_de_decodering():
    # Een nieuwere versie kan alles na de header anders indelen; doorgaan zou
    # plausibel ogende onzin opleveren.
    raw = frames.frame(frames.ROUTE_FLOOD, frames.TYPE_ADVERT, version=1,
                       payload=frames.advert_payload())
    out = packets.decode(raw)
    assert out["ok"] is False
    assert "unsupported protocol version" in out["error"]
    assert "scope" not in out


def test_truncaties_geven_fout_zonder_te_gokken():
    # Leeg pakket.
    out = packets.decode(b"")
    assert out["ok"] is False
    assert out["error"] == "empty packet"

    # Route belooft transportcodes maar de bytes ontbreken. 'scoped' en
    # 'share' zijn dan verschillende antwoorden, dus scope moet afwezig
    # blijven in plaats van gegokt.
    header = bytes([(frames.TYPE_TXT_MSG << 2) | frames.ROUTE_TRANSPORT_FLOOD])
    out = packets.decode(header + b"\x12\x34")
    assert out["error"] == "truncated transport codes"
    assert "scope" not in out

    # Alleen een header: de path-descriptor ontbreekt.
    out = packets.decode(bytes([(frames.TYPE_ACK << 2) | frames.ROUTE_FLOOD]))
    assert out["error"] == "missing path descriptor"

    # Descriptor belooft drie hops, er volgt er maar een.
    header = bytes([(frames.TYPE_ACK << 2) | frames.ROUTE_FLOOD])
    out = packets.decode(header + bytes([0x03]) + b"\xa7")
    assert out["error"] == "truncated path"

    # ADVERT-payload korter dan pubkey + timestamp + handtekening.
    out = packets.decode(frames.frame(frames.ROUTE_FLOOD, frames.TYPE_ADVERT,
                                      payload=b"\x01" * 40))
    assert out["error"] == "truncated advert"

    # Vlaggen beloven een positie die er niet meer is.
    kapot = frames.advert_payload()[:100] + bytes([0x10]) + b"\x01\x02"
    out = packets.decode(frames.frame(frames.ROUTE_FLOOD, frames.TYPE_ADVERT,
                                      payload=kapot))
    assert out["error"] == "truncated advert position"


def test_decode_gooit_nooit():
    # Radioruis produceert routinematig onzin; een kapot pakket mag de
    # MQTT-subscriber niet kunnen neerhalen.
    for raw in (None, b"", b"\xff", b"\xff" * 300, bytes(1), bytes(255)):
        out = packets.decode(raw)
        assert isinstance(out, dict)
        assert "ok" in out
