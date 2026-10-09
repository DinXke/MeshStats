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
    assert parse(q(1, NOW) + "||fg").exact_ack and not parse(q(1, NOW) + "||f").exact_ack


# ---- T1F 1.3.1: exacte bevestiging per seq, alleen met vlag "g" ------------------------------

def tag_of(key, pk, body):
    return hmac.new(bytes.fromhex(key), f"{pk}|F|{body}".encode(), hashlib.sha256).hexdigest()[:8]


def test_compress_seqs():
    from meshtrack.main import compress_seqs
    assert compress_seqs([]) == ""
    assert compress_seqs([7]) == "7"
    assert compress_seqs([9, 3, 4, 5]) == "3-5,9"
    assert compress_seqs([1, 2, 4, 5, 6, 8, 10, 11]) == "1-2,4-6,8,10-11"
    assert compress_seqs([5, 5, 6]) == "5-6"
    assert compress_seqs([65535, 0, 1]) == "0-1,65535"


def test_fifo_entry_tag_and_bundle_text():
    from meshtrack.main import fifo_ack_text, fifo_entry
    assert fifo_entry(AUTH, PK8, [3, 4, 5, 9]) == f"{PK8}:{tag_of(AUTH, PK8, 's3-5,9')}:s3-5,9"
    assert fifo_ack_text(["a:b:s1", "c:d:s2"]) == "T1F|a:b:s1|c:d:s2"


def test_fit_seqs_keeps_most_recent():
    from meshtrack.main import compress_seqs, fit_seqs
    seqs = list(range(100, 160, 2))                     # 30 losse seqs, in ontvangstvolgorde
    got = fit_seqs(seqs, 20)
    assert got == seqs[-len(got):] and 1 + len(compress_seqs(got)) <= 20 and len(got) < len(seqs)
    assert fit_seqs([1, 2, 3], 20) == [1, 2, 3]
    assert fit_seqs([12345], 3) == []


def test_no_t1f_without_g(app):
    main, mp, mesh, tid, cid, chan, tick = app
    t0 = time.time()
    now = int(t0)
    chan(rest_of(1, now - 900))                          # zonder vlaggen
    chan(rest_of(2, now - 600, flags="f"))               # fw 0.9.0-0.9.3: "f" zonder "g"
    assert len(main.S.db.track(tid, 0)) == 2
    assert main._fifo_pending == {} and tick(t0 + 30) == 0 and mesh.sent == []


def test_g_exact_seqs_debounced(app):
    main, mp, mesh, tid, cid, chan, tick = app
    t0 = time.time()
    now = int(t0)
    for seq, ts in ((7, now - 900), (8, now - 800), (9, now - 700), (12, now - 600)):
        chan(rest_of(seq, ts, flags="fg"))
    assert main._fifo_pending[tid]["seqs"] == [7, 8, 9, 12]
    assert tick(t0 + 10) == 0                            # debounce: 20 s na het laatste Q-bericht
    assert tick(t0 + 25) == 1
    (pk, tag, body), = parse_t1f(mesh.sent[0][1])
    assert (pk, body, tag) == (PK8, "s7-9,12", tag_of(AUTH, PK8, "s7-9,12"))
    assert (mesh.sent[0][0], mesh.sent[0][2]) == (3, "be") and main._fifo_pending == {}
    chan(rest_of(13, now - 500, flags="g"))              # "g" zonder "f" telt ook
    assert main._fifo_pending[tid]["seqs"] == [13]


def test_duplicates_included_invalid_and_unsigned_not(app):
    main, mp, mesh, tid, cid, chan, tick = app
    t0 = time.time()
    now = int(t0)
    chan(rest_of(1, now - 900, flags="g"))
    assert tick(t0 + 25) == 1
    chan(rest_of(1, now - 900, flags="g"))               # exacte herhaling (dubbel): toch bevestigen
    chan(rest_of(2, now - 900, flags="g"))               # andere seq, zelfde punten (alles dubbel): ook
    assert main._fifo_pending[tid]["seqs"] == [1, 2] and len(main.S.db.track(tid, 0)) == 1
    main._fifo_pending.clear()
    chan(rest_of(5, now - 100, extra="BA", flags="g"))   # ongeldig binair: geen T1F, wel gelogd
    assert main._fifo_pending == {} and main.S.db.unknown()[0]["reason"].startswith("ongeldig")
    main.S.db.save_channel(cid, {"name": "trk", "secret": "00" * 16, "slot": 3, "require_sig": 0,
                                 "active": 1, "region": "be"})
    chan(rest_of(6, now - 50, flags="g"), signed=False)  # niet ondertekend: geen T1F
    assert main._fifo_pending == {}


def test_truncation_sends_newest_rest_later(app):
    main, mp, mesh, tid, cid, chan, tick = app
    t, ch = main.S.db.tracker(tid), main.S.db.channel(cid)
    seqs = list(range(1000, 1200, 3))                    # 67 losse seqs: past nooit in één T1F
    for s in seqs:
        main.fifo_request(ch, t, PK8, s, now=1000)
    assert tick(1025) == 1
    text = mesh.sent[0][1]
    assert len(text) <= main.T1F_MAX_TEXT
    (pk, tag, body), = parse_t1f(text)
    sent = [int(x) for x in body[1:].split(",")]
    assert sent == seqs[-len(sent):] and tag == tag_of(AUTH, PK8, body)
    assert main._fifo_pending[tid]["seqs"] == seqs[:-len(sent)]          # rest wacht
    assert tick(1100) == 0                               # tracker: hoogstens één per 10 min
    assert tick(1626) == 1
    body2 = parse_t1f(mesh.sent[1][1])[0][2]
    assert [int(x) for x in body2[1:].split(",")] == seqs[:-len(sent)][-len(body2[1:].split(",")):]


def test_bundle_mixed_g_and_old_trackers(app):
    main, mp, mesh, tid, cid, chan, tick = app
    ch = main.S.db.channel(cid)
    trk = [add_tracker(main, i) for i in range(1, 7)]
    t0 = time.time()
    now = int(t0)

    def send(i, seq, flags):
        t, pk, key = trk[i]
        rest = rest_of(seq, now - 300 + seq, flags=flags)
        tg = hmac.new(bytes.fromhex(key), f"{pk}|{rest}".encode(), hashlib.sha256).hexdigest()[:8]
        main_chan = f"T{i + 1}: T1C|{pk}|{tg}|{rest}"
        chan("", text=main_chan)
    for i in range(6):
        send(i, 10 + i, "fg" if i % 2 == 0 else "f")     # 3 nieuwe (g) en 3 oude (alleen f) trackers
    assert sorted(main._fifo_pending) == sorted(trk[i][0]["id"] for i in (0, 2, 4))
    assert tick(t0 + 25) == 1
    got = parse_t1f(mesh.sent[0][1])
    keys = {pk: key for _, pk, key in trk}
    assert sorted(pk for pk, _, _ in got) == sorted(trk[i][1] for i in (0, 2, 4))     # alleen g-trackers
    for pk, tag, body in got:
        assert body.startswith("s") and tag == tag_of(keys[pk], pk, body)
    assert main._fifo_pending == {}


def test_bundling_max_4_per_message(app):
    main, mp, mesh, tid, cid, chan, tick = app
    ch = main.S.db.channel(cid)
    trk = [add_tracker(main, i) for i in range(1, 7)]
    for n, (t, pk, _) in enumerate(trk):
        main.fifo_request(ch, t, pk, 100 + n, now=1000)
    assert tick(1025) == 1
    first = parse_t1f(mesh.sent[0][1])
    assert len(first) == 4
    assert tick(1030) == 0                               # kanaal: max. één T1F per 60 s
    assert tick(1086) == 1
    second = parse_t1f(mesh.sent[1][1])
    assert len(second) == 2 and main._fifo_pending == {}
    keys = {pk: key for _, pk, key in trk}
    for pk, tag, body in first + second:
        assert tag == tag_of(keys[pk], pk, body)


def test_two_channels_send_separately(app):
    main, mp, mesh, tid, cid, chan, tick = app
    cid2 = main.S.db.save_channel(None, {"name": "trk2", "secret": "11" * 16, "slot": 4, "require_sig": 1,
                                         "active": 1, "region": "nl"})
    (a, pa, _), (b, pb, _) = add_tracker(main, 1), add_tracker(main, 2)
    main.fifo_request(main.S.db.channel(cid), a, pa, 1, now=1000)
    main.fifo_request(main.S.db.channel(cid2), b, pb, 1, now=1000)
    assert tick(1025) == 2
    assert sorted((s[0], s[2]) for s in mesh.sent) == [(3, "be"), (4, "nl")]


def test_per_tracker_interval(app):
    main, mp, mesh, tid, cid, chan, tick = app
    t, ch = main.S.db.tracker(tid), main.S.db.channel(cid)
    main.fifo_request(ch, t, PK8, 1, now=1000)
    assert tick(1025) == 1
    main.fifo_request(ch, t, PK8, 2, now=1100)
    main.fifo_request(ch, t, PK8, 3, now=1110)
    assert tick(1200) == 0 and tick(1600) == 0           # hoogstens één per 10 min per tracker
    assert tick(1626) == 1
    assert parse_t1f(mesh.sent[1][1])[0][2] == "s2-3"    # alleen wat na de vorige T1F binnenkwam


def test_hourly_cap_delays_and_logs(app, caplog):
    main, mp, mesh, tid, cid, chan, tick = app
    mp.setattr(main, "T1F_MAX_PER_HOUR", 2)
    mp.setattr(main, "FIFO_MAX_PER_MSG", 1)
    ch = main.S.db.channel(cid)
    for i in range(1, 4):
        t, pk, _ = add_tracker(main, i)
        main.fifo_request(ch, t, pk, i, now=1000)
    caplog.set_level("INFO", logger="meshtrack")
    assert tick(1025) == 1 and tick(1090) == 1
    assert tick(1200) == 0                               # uurlimiet bereikt
    assert any("per uur" in r.getMessage() for r in caplog.records)
    assert len(main._fifo_pending) == 1
    assert tick(1025 + 3600) == 1 and main._fifo_pending == {}


def test_no_mesh_keeps_pending_and_failed_send_restores(app):
    main, mp, mesh, tid, cid, chan, tick = app
    t, ch = main.S.db.tracker(tid), main.S.db.channel(cid)
    main.fifo_request(ch, t, PK8, 4, now=1000)
    mesh.connected = False
    assert tick(1025) == 0 and tid in main._fifo_pending
    mesh.connected = True

    fail = {"on": True}
    orig = mesh.send_channel

    async def flaky(*a, **k):
        if fail["on"]:
            raise RuntimeError("weg")
        await orig(*a, **k)
    mp.setattr(mesh, "send_channel", flaky)
    assert tick(1026) == 0 and main._fifo_pending[tid]["seqs"] == [4]     # mislukt: terugzetten
    main.fifo_request(ch, t, PK8, 5, now=1027)
    assert main._fifo_pending[tid]["seqs"] == [4, 5]
    fail["on"] = False
    assert tick(1100) == 1 and parse_t1f(mesh.sent[-1][1])[0][2] == "s4-5"


def test_own_acks_echoed_back_are_ignored(app):
    main, mp, mesh, tid, cid, chan, tick = app
    t1f = main.fifo_ack_text([main.fifo_entry(AUTH, PK8, [1, 2])])
    old = f"T1F|{PK8}:{tag_of(AUTH, PK8, str(NOW))}:{NOW}"          # oud formaat (echo van een oude server)
    for body in (t1f, old, main.sos_ack_text(AUTH, PK8, "7")):
        chan("", text=f"MeshTrack: {body}")             # echo via een repeater
    assert main.S.db.unknown() == [] and main.S.db.track(tid, 0) == []
    from meshtrack.protocol import is_meshtrack
    assert not is_meshtrack(t1f)
    assert handle(main.S.db, Config(), PK, t1f) is None and main.S.db.unknown() == []

