"""FIFO (state Q, firmware 0.9.0): ingehaalde punten, binaire extra punten en de bevestiging T1F."""
import base64
import hashlib
import hmac
import os
import time

import pytest
from fastapi.testclient import TestClient

from meshtrack import auth
from meshtrack.config import Config
from meshtrack.db import DB
from meshtrack.ingest import handle
from meshtrack.protocol import ProtocolError, parse

KEY = "2cb0c5eb473757805eab00f9dd0594c229d6e50b2acec6b57403b6259c9e126f"
PK = KEY[:12]
PK8 = KEY[:8]
AUTH = "00112233445566778899aabbccddeeff"
NOW = 1_800_000_000


# ---- kleine encoder zoals de firmware ----------------------------------------------------

def varint(n: int) -> bytes:
    out = bytearray()
    while True:
        b = n & 0x7F
        n >>= 7
        out.append(b | (0x80 if n else 0))
        if not n:
            return bytes(out)


def zz(n: int) -> int:
    return (n << 1) ^ (n >> 63)


def encode(points) -> str:
    """points: [(dt, dlat, dlon)] nieuwste eerst, elk t.o.v. het vorige; -> 'B<base64url zonder padding>'."""
    raw = b"".join(varint(dt) + varint(zz(a)) + varint(zz(o)) for dt, a, o in points)
    return "B" + base64.urlsafe_b64encode(raw).decode().rstrip("=")


def q(seq, ts, lat=50.93, lon=5.33, extra="", state="Q"):
    return f"T1|{seq}|{state}|{lat:.5f}|{lon:.5f}|40|10|90|70|0.9|0|t|b|{ts}" + (f"|{extra}" if extra else "")


def setup():
    db = DB(":memory:")
    tid = db.add_tracker(KEY, "Björn")
    return db, Config(), tid


# ---- binaire extra punten ---------------------------------------------------------------

def test_binary_roundtrip_equals_text_form():
    pts = [(60, -412, 201), (60, -398, 190), (40, 3, -5)]
    rb = parse(q(1, NOW, extra=encode(pts)))
    rt = parse(q(1, NOW, extra="~60;-412,201;-398,190;3,-5@40"))
    assert rb.extra == rt.extra
    assert [e[0] for e in rb.extra] == [60, 120, 160]
    assert (rb.extra[0][1], rb.extra[0][2]) == (50.92588, 5.33201)


def test_binary_large_values_and_limits():
    pts = [(86400, -2_000_000, 1_999_999)]
    r = parse(q(1, NOW, lat=10.0, lon=10.0, extra=encode(pts)))
    assert r.extra[0][:3] == (86400, -10.0, 29.99999)
    assert len(parse(q(1, NOW, extra=encode([(10, 1, 1)] * 40))).extra) == 40     # binair: tot 40
    with pytest.raises(ProtocolError):
        parse(q(1, NOW, extra=encode([(10, 1, 1)] * 41)))
    assert parse(q(1, NOW, extra="B")).extra == []                                 # leeg = geen punten
    assert parse(q(1, NOW, extra=encode([(5, 1, 1)]), state="L")).state == "L"     # ook op L


@pytest.mark.parametrize("bad", [
    "Babc=",                          # padding hoort er niet bij
    "Bab+/",                          # geen base64url
    "BA",                             # lengte % 4 == 1
    "B" + base64.urlsafe_b64encode(b"\x80").decode().rstrip("="),            # afgebroken varint
    "B" + base64.urlsafe_b64encode(b"\x01\x02").decode().rstrip("="),        # onvolledig punt
    "B" + base64.urlsafe_b64encode(b"\xff\xff\xff\xff\xff\x01\x00\x00").decode().rstrip("="),  # varint te lang
    encode([(86401, 0, 0)]),          # dt buiten bereik
    encode([(10, 2_000_001, 0)]),     # dlat buiten bereik
])
def test_malformed_binary_rejected_and_logged(bad):
    with pytest.raises(ProtocolError):
        parse(q(1, NOW, extra=bad))
    db, cfg, _ = setup()
    assert handle(db, cfg, PK, q(1, NOW - 10, extra=bad), now=NOW) is None
    assert db.unknown()[0]["reason"].startswith("ongeldig")


# ---- Q in de database -------------------------------------------------------------------

def test_Q_is_history_like_L():
    db, cfg, tid = setup()
    handle(db, cfg, PK, f"T1|1|M|50.95000|5.33000|40|30|90|80|0.9|1|t|b|{NOW - 10}", now=NOW - 5)
    out = handle(db, cfg, PK, q(2, NOW - 600, extra=encode([(60, -10, 0), (60, -10, 0)])), now=NOW)
    assert out and out["stored"] and [e["state"] for e in out["extras"]] == ["Q", "Q"]
    t = db.tracker(tid)
    assert (t["last_ts"], t["last_lat"], t["last_state"], t["last_bat"]) == (NOW - 10, 50.95, "M", 80)
    assert (t["last_fifo_rx"], t["last_fifo_ts"], t["last_slow_rx"]) == (NOW, NOW - 600, None)
    assert [p["ts"] for p in db.track(tid, 0)] == [NOW - 720, NOW - 660, NOW - 600, NOW - 10]
    # zelfde punten opnieuw (andere seq): niets dubbel, wel gehoord
    assert handle(db, cfg, PK, q(3, NOW - 600, extra=encode([(60, -10, 0)])), now=NOW + 30) is None
    assert len(db.track(tid, 0)) == 4 and db.tracker(tid)["last_fifo_rx"] == NOW + 30
    # Q-seq valt samen met een L-seq: geen herhaling (aparte soort)
    assert handle(db, cfg, PK, q(2, NOW - 900, state="L"), now=NOW + 40)


def test_newer_Q_moves_position_keeps_state():
    db, cfg, tid = setup()
    handle(db, cfg, PK, f"T1|1|S|50.95000|5.33000|40|0|90|80|0.9|1|t|b|{NOW - 900}", now=NOW - 890)
    handle(db, cfg, PK, q(2, NOW - 5, lat=51.0), now=NOW)
    t = db.tracker(tid)
    assert (t["last_ts"], t["last_lat"], t["last_state"]) == (NOW - 5, 51.0, "S")



# ---- live: T1F, echo's --------------------------------------------------------------------

class FakeMesh:
    connected = True

    def __init__(self):
        self.sent = []

    async def send_channel(self, slot, text, scope=""):
        self.sent.append((slot, text, scope))

    def status(self):
        return {"connected": True}


@pytest.fixture()
def app(tmp_path, monkeypatch):
    cfg = tmp_path / "config.yaml"
    cfg.write_text(f"""
mesh: {{host: 127.0.0.1, port: 1}}
db_path: {tmp_path / 'mt.sqlite3'}
tiles_dir: {tmp_path / 'tiles'}
openhop_db: {tmp_path / 'geen.db'}
auth: {{user: admin, password_hash: "{auth.hash_password('beheerder1')}", session_secret: "test-geheim"}}
""".replace("\\\\", "/"), encoding="utf-8")
    os.environ["MESHTRACK_CONFIG"] = str(cfg)
    from meshtrack import main
    main._pcache.clear()
    for d in (main._fifo_pending, main._fifo_tracker_sent, main._fifo_chan_sent, main._fifo_hour, main._fifo_capped):
        d.clear()
    with TestClient(main.app) as c:
        with monkeypatch.context() as mp:
            mesh = FakeMesh()
            mp.setattr(main.S, "mesh", mesh)
            tid = main.S.db.add_tracker(KEY, "Björn")
            main.S.db.set_authkey(tid, AUTH)
            cid = main.S.db.save_channel(None, {"name": "trk", "secret": "00" * 16, "slot": 3, "require_sig": 1,
                                                "active": 1, "region": "be"})

            def chan(rest, signed=True, text=None):
                tag = main.channel_tag(AUTH, f"{PK8}|{rest}") if signed else "-"
                c.portal.call(main.on_channel, 3, text or f"Björn: T1C|{PK8}|{tag}|{rest}", None, 5.0, 1)

            def tick(now):
                return c.portal.call(main.fifo_tick, now)
            yield main, mp, mesh, tid, cid, chan, tick


def rest_of(seq, ts, extra="", flags=""):
    r = q(seq, ts, extra=extra)[3:]
    return r + (("|" if extra else "||") + flags if flags else "")


def add_tracker(main, i):
    pub = f"{i:02x}" * 32
    key = f"{i:02x}" * 16
    tid = main.S.db.add_tracker(pub, f"T{i}")
    main.S.db.set_authkey(tid, key)
    return main.S.db.tracker(tid), pub[:8], key


def parse_t1f(text):
    assert text.startswith("T1F|")
    return [e.split(":") for e in text[4:].split("|")]


def test_flags_field_16():
    assert parse(q(1, NOW) + "||f").ack_requested
    assert parse(q(1, NOW, extra=encode([(5, 1, 1)])) + "|f").extra
    assert not parse(q(1, NOW) + "||").ack_requested and not parse(q(1, NOW)).ack_requested
    assert parse(q(1, NOW) + "||fx").ack_requested            # onbekende vlaggen verdragen
    with pytest.raises(ProtocolError):
        parse(q(1, NOW) + "||F!")


def test_fifo_entry_tag_and_bundle_text():
    from meshtrack.main import fifo_ack_text, fifo_entry
    tag = hmac.new(bytes.fromhex(AUTH), f"{PK8}|F|{NOW}".encode(), hashlib.sha256).hexdigest()[:8]
    assert fifo_entry(AUTH, PK8, NOW) == f"{PK8}:{tag}:{NOW}"
    assert fifo_ack_text(["a:b:1", "c:d:2"]) == "T1F|a:b:1|c:d:2"


def test_t1f_only_on_request_and_debounced(app):
    main, mp, mesh, tid, cid, chan, tick = app
    t0 = time.time()
    now = int(t0)
    chan(rest_of(1, now - 900))                          # zonder "f": nooit een T1F
    assert main._fifo_pending == {}
    chan(rest_of(2, now - 600, flags="f"))
    chan(rest_of(3, now - 700, flags="f"))               # ouder punt: upto blijft het hoogste
    assert len(main.S.db.track(tid, 0)) == 3
    assert tick(t0 + 10) == 0                            # debounce: nog niet
    assert tick(t0 + 25) == 1
    (pk, tag, upto), = parse_t1f(mesh.sent[0][1])
    assert (pk, int(upto)) == (PK8, now - 600) and (mesh.sent[0][0], mesh.sent[0][2]) == (3, "be")
    assert tag == main.channel_tag(AUTH, f"{PK8}|F|{now - 600}")
    assert main._fifo_pending == {}


def test_debounce_restarts_on_each_flagged_q(app):
    main, mp, mesh, tid, cid, chan, tick = app
    t, ch = main.S.db.tracker(tid), main.S.db.channel(cid)
    main.fifo_request(ch, t, PK8, NOW - 500, now=1000)
    main.fifo_request(ch, t, PK8, NOW - 400, now=1015)
    assert tick(1025) == 0                               # 20 s na het LAATSTE verzoek
    assert tick(1035) == 1 and parse_t1f(mesh.sent[0][1])[0][2] == str(NOW - 400)


def test_t1f_for_duplicates_not_for_invalid_or_unsigned(app):
    main, mp, mesh, tid, cid, chan, tick = app
    t0 = time.time()
    now = int(t0)
    chan(rest_of(1, now - 900, flags="f"))
    assert tick(t0 + 25) == 1
    chan(rest_of(1, now - 900, flags="f"))               # exacte herhaling (dubbel): toch bevestigen
    assert tid in main._fifo_pending and len(main.S.db.track(tid, 0)) == 1
    main._fifo_pending.clear()
    chan(rest_of(5, now - 100, extra="BA", flags="f"))   # ongeldig binair: geen T1F, wel gelogd
    assert main._fifo_pending == {} and main.S.db.unknown()[0]["reason"].startswith("ongeldig")
    main.S.db.save_channel(cid, {"name": "trk", "secret": "00" * 16, "slot": 3, "require_sig": 0,
                                 "active": 1, "region": "be"})
    chan(rest_of(6, now - 50, flags="f"), signed=False)  # niet ondertekend: geen T1F
    assert main._fifo_pending == {}


def test_bundling_per_channel_with_tag_per_entry(app):
    main, mp, mesh, tid, cid, chan, tick = app
    ch = main.S.db.channel(cid)
    trk = [add_tracker(main, i) for i in range(1, 7)]
    for n, (t, pk, _) in enumerate(trk):
        main.fifo_request(ch, t, pk, NOW + n, now=1000)
    assert tick(1025) == 1
    first = parse_t1f(mesh.sent[0][1])
    assert len(first) == 4
    assert tick(1030) == 0                               # kanaal: max. één T1F per 60 s
    assert tick(1086) == 1
    second = parse_t1f(mesh.sent[1][1])
    assert len(second) == 2 and main._fifo_pending == {}
    keys = {pk: key for _, pk, key in trk}
    got = first + second
    assert sorted(pk for pk, _, _ in got) == sorted(keys)
    for pk, tag, upto in got:                            # elke tag met de sleutel van die tracker
        assert tag == hmac.new(bytes.fromhex(keys[pk]), f"{pk}|F|{upto}".encode(), hashlib.sha256).hexdigest()[:8]


def test_two_channels_send_separately(app):
    main, mp, mesh, tid, cid, chan, tick = app
    cid2 = main.S.db.save_channel(None, {"name": "trk2", "secret": "11" * 16, "slot": 4, "require_sig": 1,
                                         "active": 1, "region": "nl"})
    (a, pa, _), (b, pb, _) = add_tracker(main, 1), add_tracker(main, 2)
    main.fifo_request(main.S.db.channel(cid), a, pa, NOW, now=1000)
    main.fifo_request(main.S.db.channel(cid2), b, pb, NOW, now=1000)
    assert tick(1025) == 2
    assert sorted((s[0], s[2]) for s in mesh.sent) == [(3, "be"), (4, "nl")]


def test_per_tracker_interval_and_merge(app):
    main, mp, mesh, tid, cid, chan, tick = app
    t, ch = main.S.db.tracker(tid), main.S.db.channel(cid)
    main.fifo_request(ch, t, PK8, NOW, now=1000)
    assert tick(1025) == 1
    main.fifo_request(ch, t, PK8, NOW + 100, now=1100)
    main.fifo_request(ch, t, PK8, NOW + 50, now=1110)    # samenvoegen: hoogste upto blijft
    assert tick(1200) == 0 and tick(1600) == 0           # hoogstens één per 10 min per tracker
    assert tick(1626) == 1
    assert parse_t1f(mesh.sent[1][1])[0][2] == str(NOW + 100)


def test_hourly_cap_delays_and_logs(app, caplog):
    main, mp, mesh, tid, cid, chan, tick = app
    mp.setattr(main, "T1F_MAX_PER_HOUR", 2)
    mp.setattr(main, "FIFO_MAX_PER_MSG", 1)
    ch = main.S.db.channel(cid)
    for i in range(1, 4):
        t, pk, _ = add_tracker(main, i)
        main.fifo_request(ch, t, pk, NOW, now=1000)
    caplog.set_level("INFO", logger="meshtrack")
    assert tick(1025) == 1 and tick(1090) == 1
    assert tick(1200) == 0                               # uurlimiet bereikt
    assert any("per uur" in r.getMessage() for r in caplog.records)
    assert len(main._fifo_pending) == 1
    assert tick(1025 + 3600) == 1 and main._fifo_pending == {}


def test_no_mesh_keeps_pending(app):
    main, mp, mesh, tid, cid, chan, tick = app
    main.fifo_request(main.S.db.channel(cid), main.S.db.tracker(tid), PK8, NOW, now=1000)
    mesh.connected = False
    assert tick(1025) == 0 and tid in main._fifo_pending
    mesh.connected = True
    assert tick(1026) == 1


def test_own_acks_echoed_back_are_ignored(app):
    main, mp, mesh, tid, cid, chan, tick = app
    t1f = main.fifo_ack_text([main.fifo_entry(AUTH, PK8, NOW)])
    for body in (t1f, main.sos_ack_text(AUTH, PK8, "7")):
        chan("", text=f"MeshTrack: {body}")             # echo via een repeater
    assert main.S.db.unknown() == [] and main.S.db.track(tid, 0) == []
    from meshtrack.protocol import is_meshtrack
    assert not is_meshtrack(t1f)
    assert handle(main.S.db, Config(), PK, t1f) is None and main.S.db.unknown() == []
