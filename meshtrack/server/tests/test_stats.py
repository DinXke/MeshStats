"""Statistieken (1.3): stats_events, migratie, /api/stats/* en paden van trackerberichten."""
import hashlib
import hmac
import os
import sqlite3
import struct
import time

import pytest
from Crypto.Cipher import AES
from fastapi.testclient import TestClient

from meshtrack import auth, paths, stats
from meshtrack.config import Config
from meshtrack.db import DB
from meshtrack.ingest import handle

KEY = "2cb0c5eb473757805eab00f9dd0594c229d6e50b2acec6b57403b6259c9e126f"
PK, PK8 = KEY[:12], KEY[:8]
KEY_B = "bb" * 32
AUTH = "00112233445566778899aabbccddeeff"
SECRET = "8b3387e9c5cdea6ac9e5edbaa115cd72"
NOW = 1_800_000_000                     # veelvoud van 3600


def msg(seq, ts, state="M", lat=50.93, lon=5.33, extra=""):
    return f"T1|{seq}|{state}|{lat:.5f}|{lon:.5f}|40|10|90|70|0.9|0|t|b|{ts}" + (f"|{extra}" if extra else "")


def fill(db, cfg, a, b):
    """Kleine synthetische dataset (zie de verwachte waarden in test_summary_correct)."""
    handle(db, cfg, PK, msg(1, NOW - 1010, extra="~15;-412,201;-398,190"), snr=5.0, path_len=2, now=NOW - 1000)
    handle(db, cfg, PK, msg(2, NOW - 905, state="S"), snr=-3.0, path_len=0, now=NOW - 900)
    handle(db, cfg, PK, "T1|3|N||||||80||", snr=1.0, path_len=255, now=NOW - 800)
    handle(db, cfg, PK, msg(10, NOW - 3000, state="Q", extra="~60;-10,0;-10,0"), snr=7.0, path_len=1, now=NOW - 100)
    assert handle(db, cfg, PK, msg(1, NOW - 1010), snr=5.0, path_len=2, now=NOW - 90) is None          # dup_msg
    assert handle(db, cfg, PK, msg(11, NOW - 3000, state="Q", extra="~60;-10,0;-10,0"), now=NOW - 80) is None  # 3 dup_points
    handle(db, cfg, KEY_B[:12], msg(1, NOW - 600), snr=2.0, path_len=3, now=NOW - 598)


def setup_db():
    db = DB(":memory:")
    a = db.add_tracker(KEY, "Björn")
    b = db.add_tracker(KEY_B, "Bert")
    c1 = db.save_channel(None, {"name": "trk", "secret": SECRET, "slot": 3, "require_sig": 1, "active": 1})
    c2 = db.save_channel(None, {"name": "ander", "secret": "11" * 16, "slot": 4, "require_sig": 1, "active": 1})
    db.set_tracker_channel(a, c1)
    db.set_tracker_channel(b, c2)
    cfg = Config()
    fill(db, cfg, a, b)
    return db, a, b, c1, c2


# ---- migratie --------------------------------------------------------------------------------

def test_migration_adds_extra_and_tables_idempotently(tmp_path):
    p = str(tmp_path / "oud.sqlite3")
    db = DB(p)
    tid = db.add_tracker(KEY, "Björn")
    handle(db, Config(), PK, msg(1, NOW - 100, extra="~15;-412,201"), now=NOW)
    db._c.execute("DROP INDEX positions_stats")
    db._c.execute("ALTER TABLE positions DROP COLUMN extra")       # toestand van vóór 1.3
    db._c.execute("DROP TABLE stats_events")
    db._c.execute("DROP TABLE message_paths")
    db._c.close()
    for _ in range(2):                                               # tweemaal: idempotent
        db = DB(p)
        rows = db._q("SELECT extra, raw FROM positions WHERE tracker_id=? ORDER BY ts", (tid,))
        assert [r["extra"] for r in rows] == [1, 0] and len(rows) == 2
        idx = {r["name"] for r in db._q("SELECT name FROM sqlite_master WHERE type='index'")}
        assert {"positions_stats", "stats_events_ts", "message_paths_rx"} <= idx
        db.stat("invalid", tid)
        db._c.close()


# ---- samenvatting, tijdreeks, verdelingen -----------------------------------------------------

def test_summary_correct():
    db, a, b, c1, c2 = setup_db()
    sc = stats.Scope(NOW - 7 * 86400, NOW)
    trk = db.trackers()
    s = stats.summary(db, sc, trk, {c1: "trk", c2: "ander"})
    t = s["totals"]
    assert t["messages"] == 5
    assert {k: v for k, v in t["messages_by_state"].items() if v} == {"M": 2, "S": 1, "N": 1, "Q": 1}
    assert t["points"] == 8 and t["points_by_kind"] == {"live": 3, "extra": 2, "slow": 0, "fifo": 3}
    assert t["recovered_late"] == 3
    assert (t["dup_msg"], t["dup_points"], t["invalid"], t["unknown"]) == (1, 3, 0, 0)
    assert t["avg_snr"] == 2.4 and t["avg_hops"] == 1.5 and t["trackers_active"] == 2
    ta = next(x for x in s["trackers"] if x["id"] == a)
    assert (ta["messages"], ta["points"], ta["q_messages"], ta["q_points"], ta["l_points"]) == (4, 7, 1, 3, 0)
    assert (ta["min_snr"], ta["max_snr"], ta["avg_snr"]) == (-3.0, 7.0, 2.5)
    assert (ta["delivery_delay_p50"], ta["delivery_delay_p90"]) == (40, 3020)
    assert ta["channel"] == "trk" and ta["fw_mode"] == "t" and ta["bat_last"] == 80 and ta["first_hop"] is None
    # één tracker
    s1 = stats.summary(db, stats.Scope(NOW - 86400, NOW, ids=[b]), [db.tracker(b)], {})
    assert s1["totals"]["messages"] == 1 and s1["totals"]["dup_msg"] == 0


def test_empty_range():
    db, *_ = setup_db()
    sc = stats.Scope(NOW - 500000, NOW - 500000)
    s = stats.summary(db, sc, db.trackers(), {})
    assert s["totals"]["messages"] == 0 and s["totals"]["avg_snr"] is None and s["totals"]["trackers_active"] == 0
    assert all(x["messages"] == 0 and x["delivery_delay_p50"] is None for x in s["trackers"])
    ts = stats.timeseries(db, sc, "hour")
    assert len(ts["series"]) == 1 and ts["series"][0]["messages"] == 0 and ts["series"][0]["avg_snr"] is None
    d = stats.distributions(db, sc)
    assert sum(x["count"] for x in d["snr"]) == 0 and d["hops"] == [] and sum(d["hour_of_day"]) == 0
    assert stats.fifo(db, sc, db.trackers())["trackers"] == []
    s0 = stats.summary(db, stats.Scope(NOW - 86400, NOW, ids=[]), [], {})
    assert s0["totals"]["messages"] == 0 and s0["totals"]["dup_points"] == 0


def test_timeseries_buckets_filled():
    db, *_ = setup_db()
    ts = stats.timeseries(db, stats.Scope(NOW - 3 * 3600, NOW), "hour")
    assert [b["t"] for b in ts["series"]] == [NOW - 10800, NOW - 7200, NOW - 3600, NOW]
    assert [b["messages"] for b in ts["series"]] == [0, 0, 5, 0]
    b = ts["series"][2]
    assert (b["points"], b["live"], b["extra"], b["fifo"], b["trackers"]) == (8, 3, 2, 3, 2)
    assert (b["min_snr"], b["max_snr"], b["avg_snr"], b["avg_bat"]) == (-3.0, 7.0, 2.4, 72.0)
    assert ts["series"][0]["avg_snr"] is None and ts["series"][0]["trackers"] == 0
    assert stats.pick_bucket("auto", NOW - 3 * 86400, NOW) == "hour"
    assert stats.pick_bucket("auto", NOW - 4 * 86400, NOW) == "day"
    days = stats.timeseries(db, stats.Scope(NOW - 10 * 86400, NOW), "day")["series"]
    assert len(days) in (10, 11) and sum(d["messages"] for d in days) == 5
    for d in days:                                                   # lokale middernacht
        from datetime import datetime
        lt = datetime.fromtimestamp(d["t"], stats.TZ)
        assert (lt.hour, lt.minute) == (0, 0)


def test_distributions():
    db, *_ = setup_db()
    d = stats.distributions(db, stats.Scope(NOW - 86400, NOW))
    assert len(d["snr"]) == 18 and d["snr"][0]["lo"] == -20 and d["snr"][-1]["hi"] == 16
    by = {x["lo"]: x["count"] for x in d["snr"] if x["count"]}
    assert by == {-4: 1, 0: 1, 2: 1, 4: 1, 6: 1}
    assert d["hops"] == [{"hops": 0, "count": 1}, {"hops": 1, "count": 1}, {"hops": 2, "count": 1}, {"hops": 3, "count": 1}]
    assert [x["count"] for x in d["delay"]] == [2, 2, 1, 0, 0, 3, 0, 0]
    assert d["delay"][-1] == {"lo": 43200, "hi": None, "count": 0}
    assert sum(d["hour_of_day"]) == 5 and len(d["hour_of_day"]) == 24
    assert d["states"]["Q"] == 1 and d["states"]["E"] == 0


def test_fifo_gaps():
    db = DB(":memory:")
    a = db.add_tracker(KEY, "Björn")
    cfg = Config()
    handle(db, cfg, PK, msg(1, NOW - 4000), now=NOW - 3990)
    handle(db, cfg, PK, msg(2, NOW - 1000), now=NOW - 990)       # gat van 3000 s
    handle(db, cfg, PK, msg(3, NOW - 900), now=NOW - 890)        # geen gat
    handle(db, cfg, PK, msg(4, NOW - 200), now=NOW - 190)        # gat van 700 s, niet gevuld
    handle(db, cfg, PK, msg(20, NOW - 2000, state="Q", extra="~60;-10,0;-10,0"), now=NOW - 100)
    db.stat("t1f_sent", a, ts=NOW - 50)
    f = stats.fifo(db, stats.Scope(NOW - 86400, NOW), db.trackers())["trackers"]
    assert len(f) == 1
    t = f[0]
    assert (t["q_messages"], t["q_points"], t["t1f_sent"], t["avg_points_per_q"]) == (1, 3, 1, 3.0)
    assert t["gaps"] == [{"from": NOW - 4000, "to": NOW - 1000, "filled_points": 3}]
    assert t["max_gap_filled_s"] == 3000


# ---- live app: stats_events, RBAC, cache, paden -----------------------------------------------

class FakeMesh:
    connected = True

    def __init__(self):
        self.sent = []

    async def send_channel(self, slot, text, scope=""):
        self.sent.append((slot, text, scope))

    def status(self):
        return {"connected": True}

    def contact_list(self):
        return [{"public_key": "e3d3" + "00" * 30, "name": "Dak-e3d3", "type": 2},
                {"public_key": "5c39" + "11" * 30, "name": "Kerk", "type": 2},
                {"public_key": "5c39" + "22" * 30, "name": "Toren", "type": 2}]


@pytest.fixture()
def app(tmp_path, monkeypatch):
    cfg = tmp_path / "config.yaml"
    cfg.write_text(f"""
mesh: {{host: 127.0.0.1, port: 1}}
db_path: {tmp_path / 'mt.sqlite3'}
tiles_dir: {tmp_path / 'tiles'}
openhop_db: {tmp_path / 'openhop.db'}
auth: {{user: admin, password_hash: "{auth.hash_password('beheerder1')}", session_secret: "test-geheim"}}
""".replace("\\\\", "/"), encoding="utf-8")
    os.environ["MESHTRACK_CONFIG"] = str(cfg)
    from meshtrack import main
    main._pcache.clear()
    main._stats_cache.clear()
    main._path_channels.update(at=0.0, list=[])
    paths._names.update(at=0.0, nodes=[])
    for d in (main._fifo_pending, main._fifo_tracker_sent, main._fifo_chan_sent, main._fifo_hour, main._fifo_capped,
              main._sos_acked):
        d.clear()
    with TestClient(main.app) as c:
        with monkeypatch.context() as mp:
            mesh = FakeMesh()
            mp.setattr(main.S, "mesh", mesh)
            tid = main.S.db.add_tracker(KEY, "Björn")
            main.S.db.set_authkey(tid, AUTH)
            cid = main.S.db.save_channel(None, {"name": "trk", "secret": SECRET, "slot": 3, "require_sig": 1,
                                                "active": 1, "region": "be"})

            def chan(rest, signed=True, text=None, slot=3):
                tag = main.channel_tag(AUTH, f"{PK8}|{rest}") if signed else "-"
                c.portal.call(main.on_channel, slot, text or f"Björn: T1C|{PK8}|{tag}|{rest}", None, 5.0, 1)
            yield c, main, tid, cid, chan, tmp_path


def login(c, user, pw):
    r = c.post("/api/login", json={"user": user, "password": pw})
    assert r.status_code == 200, r.text


def ev(main, kind):
    return main.S.db._q("SELECT tracker_id, channel_id, n FROM stats_events WHERE kind=?", (kind,))


def test_events_recorded_from_ingest_and_acks(app):
    c, main, tid, cid, chan, _ = app
    now = int(time.time())
    rest = msg(1, now - 30)[3:]
    chan(rest)
    chan(rest)                                                       # zelfde seq: dup_msg
    assert ev(main, "dup_msg") == [{"tracker_id": tid, "channel_id": cid, "n": 1}]
    chan(msg(2, now - 30)[3:])                                       # andere seq, zelfde fix: geen dup (hoofdpunt live)
    chan(msg(5, now - 600, state="Q", extra="~60;-10,0")[3:])
    chan(msg(6, now - 600, state="Q", extra="~60;-10,0")[3:])        # alles al bekend: 2 dup_points
    assert sum(r["n"] for r in ev(main, "dup_points")) == 2
    chan(msg(7, now)[3:], text=f"Björn: T1C|{PK8}|deadbeef|{msg(7, now)[3:]}")      # foute handtekening
    chan("", text="Björn: T1C|kapot")                                                # ongeldig
    chan("", text=f"X: T1C|abcdef12|-|{msg(1, now)[3:]}")                            # onbekende tracker
    c.portal.call(main.on_message, PK, msg(1, now), None, 1.0, 0)                    # oude firmware (DM)
    assert len(ev(main, "invalid")) == 2 and ev(main, "unknown")[0]["channel_id"] == cid
    assert ev(main, "old_fw_dm") == [{"tracker_id": tid, "channel_id": None, "n": 1}]
    # SOS-bevestiging en FIFO-bevestiging
    t, ch = main.S.db.tracker(tid), main.S.db.channel(cid)
    c.portal.call(main.sos_ack, ch, t, PK8, "9")
    assert ev(main, "t1a_sent") == [{"tracker_id": tid, "channel_id": cid, "n": 1}]
    main.fifo_request(ch, t, PK8, now - 600, now=1000)
    assert c.portal.call(main.fifo_tick, 1025) == 1
    assert ev(main, "t1f_msg") == [{"tracker_id": None, "channel_id": cid, "n": 1}]
    assert ev(main, "t1f_sent") == [{"tracker_id": tid, "channel_id": cid, "n": 1}]
    # via de API
    login(c, "admin", "beheerder1")
    s = c.get("/api/stats/summary").json()["totals"]
    assert (s["dup_msg"], s["dup_points"], s["invalid"], s["unknown"], s["t1a_sent"], s["t1f_sent"], s["t1f_msg"]) == \
           (1, 2, 2, 1, 1, 1, 1)
    e = c.get("/api/stats/events", params={"kind": "invalid,unknown", "from": now - 7200, "to": now + 60}).json()
    assert e["bucket"] == "hour" and e["kinds"] == ["invalid", "unknown"] and e["totals"] == {"invalid": 2, "unknown": 1}
    assert sum(b["invalid"] for b in e["series"]) == 2 and set(e["series"][0]) == {"t", "invalid", "unknown"}
    assert c.get("/api/stats/events", params={"kind": "bestaatniet"}).status_code == 400


def test_rbac_filtering_and_cache(app):
    c, main, tid, cid, chan, _ = app
    db = main.S.db
    cid2 = db.save_channel(None, {"name": "ander", "secret": "11" * 16, "slot": 4, "require_sig": 1, "active": 1})
    other = db.add_tracker(KEY_B, "Bert")
    db.set_tracker_channel(tid, cid)
    db.set_tracker_channel(other, cid2)
    now = int(time.time())
    handle(db, main.S.cfg, PK, msg(1, now - 100), snr=4.0, path_len=1, now=now - 90, channel_id=cid)
    handle(db, main.S.cfg, KEY_B[:12], msg(1, now - 100), snr=4.0, path_len=1, now=now - 90, channel_id=cid2)
    db.stat("unknown", None, cid2)
    db.stat("unknown", None, cid)
    login(c, "admin", "beheerder1")
    g = c.post("/api/groups", json={"name": "Ploeg", "perms": ["map.view"], "all_trackers": False,
                                    "channels": {str(cid): "kaart"}}).json()
    assert c.post("/api/users", json={"username": "jan", "password": "geheim123", "group_id": g["id"]}).status_code == 200
    adm = c.get("/api/stats/summary").json()
    assert adm["totals"]["messages"] == 2 and adm["totals"]["unknown"] == 2 and len(adm["trackers"]) == 2
    c.post("/api/logout")
    login(c, "jan", "geheim123")
    s = c.get("/api/stats/summary").json()
    assert [t["alias"] for t in s["trackers"]] == ["Björn"]
    assert s["totals"]["messages"] == 1 and s["totals"]["unknown"] == 1     # enkel het eigen kanaal
    assert c.get("/api/stats/summary", params={"tracker": other}).status_code == 404
    assert c.get("/api/stats/summary", params={"channel": cid2}).json()["totals"]["messages"] == 0
    assert c.get("/api/stats/timeseries").json()["bucket"] == "day"
    assert sum(b["messages"] for b in c.get("/api/stats/timeseries").json()["series"]) == 1
    assert c.get("/api/stats/distributions").json()["states"]["M"] == 1
    assert c.get("/api/stats/fifo").json()["trackers"] == []
    assert c.get("/api/stats/timeseries", params={"bucket": "week"}).status_code == 400
    assert c.get("/api/stats/summary", params={"from": now, "to": now - 10}).status_code == 400
    # cache: 30 s hetzelfde antwoord per principal en query
    handle(db, main.S.cfg, PK, msg(2, now - 50), now=now - 40, channel_id=cid)
    assert c.get("/api/stats/summary").json()["totals"]["messages"] == 1
    main._stats_cache.clear()
    assert c.get("/api/stats/summary").json()["totals"]["messages"] == 2
    c.post("/api/logout")
    assert c.get("/api/stats/summary").status_code == 401


# ---- paden ------------------------------------------------------------------------------------

def grp_packet(secret_hex, text, path, hash_size=2, route=1, ts=NOW):
    key = bytes.fromhex(secret_hex)
    pt = struct.pack("<I", ts) + b"\0" + text.encode()
    pt += b"\0" * (-len(pt) % 16)
    ct = AES.new(key, AES.MODE_ECB).encrypt(pt)
    payload = bytes([hashlib.sha256(key).digest()[0]]) + hmac.new(key, ct, hashlib.sha256).digest()[:2] + ct
    header = (5 << 2) | route
    tc = b"\x53\x5c\x00\x00" if route in (0, 3) else b""
    return bytes([header]) + tc + bytes([((hash_size - 1) << 6) | len(path)]) + b"".join(bytes.fromhex(h) for h in path) + payload


def signed_text(rest):
    tag = hmac.new(bytes.fromhex(AUTH), f"{PK8}|{rest}".encode(), hashlib.sha256).hexdigest()[:8]
    return f"Björn: T1C|{PK8}|{tag}|{rest}"


def test_parse_and_match_packets():
    db = DB(":memory:")
    tid = db.add_tracker(KEY, "Björn")
    db.set_authkey(tid, AUTH)
    cid = db.save_channel(None, {"name": "trk", "secret": SECRET, "slot": 3, "require_sig": 1, "active": 1})
    chans = paths.prepare_channels(db.channels())
    sign = lambda k, b: hmac.new(bytes.fromhex(k), b.encode(), hashlib.sha256).hexdigest()[:8]  # noqa: E731
    rest = msg(42, NOW, state="E")[3:]
    raw = grp_packet(SECRET, signed_text(rest), ["48d7", "e3d3"], route=0)
    p = paths.parse_raw(raw)
    assert (p["route"], p["type"], p["hash_size"], p["hops"], p["path"]) == (0, 5, 2, 2, ["48d7", "e3d3"])
    m = paths.match(db, raw, chans, sign)
    assert m == {"tracker_id": tid, "seq": 42, "state": "E", "path": ["48d7", "e3d3"], "hash_size": 2, "hops": 2,
                 "channel_id": cid}
    assert paths.match(db, grp_packet(SECRET, signed_text(rest), [], hash_size=1), chans, sign)["hops"] == 0
    assert paths.match(db, grp_packet("22" * 16, signed_text(rest), []), chans, sign) is None          # ander kanaal
    assert paths.match(db, grp_packet(SECRET, f"Björn: T1C|{PK8}|00000000|{rest}", []), chans, sign) is None  # foute tag
    assert paths.match(db, grp_packet(SECRET, "Björn: T1A|x|y|1", []), chans, sign) is None
    bad = bytearray(raw)
    bad[-1] ^= 1                                                                                    # MAC klopt niet
    assert paths.match(db, bytes(bad), chans, sign) is None
    nodes = [{"key": "a3" + "0" * 62, "name": "A", "repeater": True}, {"key": "a3" + "1" * 62, "name": "B", "repeater": True},
             {"key": "a3" + "2" * 62, "name": "C", "repeater": False}, {"key": "e3d3" + "0" * 60, "name": "Dak", "repeater": True}]
    assert paths.resolve("a3", nodes) == {"name": "a3 (2 kandidaten)", "candidates": 2}
    assert paths.resolve("e3d3", nodes) == {"name": "Dak", "candidates": 1}
    assert paths.resolve("ffff", nodes) == {"name": None, "candidates": 0}


def make_openhop(path, rows):
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE packets (id INTEGER PRIMARY KEY AUTOINCREMENT, timestamp REAL NOT NULL, type INTEGER NOT NULL, "
                "route INTEGER, rssi INTEGER, snr REAL, rx_radio_id TEXT, raw_packet TEXT, original_path TEXT, payload TEXT)")
    con.execute("CREATE TABLE adverts (pubkey TEXT, node_name TEXT, contact_type TEXT, is_repeater INTEGER)")
    con.executemany("INSERT INTO packets(timestamp, type, rssi, snr, rx_radio_id, raw_packet) VALUES(?,?,?,?,?,?)", rows)
    con.execute("INSERT INTO adverts VALUES(?,?,?,?)", ("48d7" + "ab" * 30, "Heuvel", "Repeater", 1))
    con.commit()
    con.close()


def test_paths_from_companion_and_openhop(app):
    c, main, tid, cid, chan, tmp = app
    now = int(time.time())
    r1, r2 = msg(1, now - 20)[3:], msg(2, now - 10)[3:]
    # bron companion: rauwe push 0x88
    c.portal.call(main.on_rx_log, grp_packet(SECRET, signed_text(r1), ["e3d3"]), 6.5, -90)
    c.portal.call(main.on_rx_log, grp_packet("33" * 16, signed_text(r1), ["e3d3"]), 6.5, -90)     # niet van ons
    rows = main.S.db._q("SELECT tracker_id, seq, state, path, hops, snr, rssi, source FROM message_paths")
    assert rows == [{"tracker_id": tid, "seq": 1, "state": "M", "path": "e3d3", "hops": 1, "snr": 6.5, "rssi": -90,
                     "source": "companion"}]
    # bron openHop: alle kopieën van beide antennes
    oh = str(tmp / "openhop.db")
    make_openhop(oh, [
        (now - 19, 5, -99, 2.0, "bureau", grp_packet(SECRET, signed_text(r1), ["48d7", "e3d3"], route=0).hex()),
        (now - 19, 5, -110, -4.0, "dak", grp_packet(SECRET, signed_text(r1), ["5c39", "e3d3"]).hex()),
        (now - 18, 5, -30, 10.0, "dak", grp_packet(SECRET, signed_text(r1), []).hex()),
        (now - 9, 5, -95, 3.0, "bureau", grp_packet(SECRET, signed_text(r2), ["48d7", "e3d3"]).hex()),
        (now - 9, 4, -95, 3.0, "bureau", "00"),                                             # advert: overslaan
        (now - 8, 5, -95, 3.0, "bureau", grp_packet("44" * 16, "x: hallo", []).hex()),      # ander kanaal
    ])
    sign = main.channel_tag
    assert paths.poll_openhop(main.S.db, oh, sign, now - 3600) == 4
    assert paths.poll_openhop(main.S.db, oh, sign, now - 3600) == 0                         # cursor
    assert main.S.db.settings()[paths.CURSOR_KEY] == 6
    assert sorted(r["radio"] for r in main.S.db._q("SELECT radio FROM message_paths WHERE source='openhop'")) == \
           ["bureau", "bureau", "dak", "dak"]
    handle(main.S.db, main.S.cfg, PK, msg(1, now - 20), snr=2.0, path_len=2, now=now - 19, channel_id=cid)
    handle(main.S.db, main.S.cfg, PK, msg(2, now - 10), snr=3.0, path_len=2, now=now - 9, channel_id=cid)
    login(c, "admin", "beheerder1")
    r = c.get("/api/stats/repeaters").json()
    assert r["source"] == "openhop"
    reps = {x["hash"]: x for x in r["repeaters"]}
    assert reps["e3d3"] == {"hash": "e3d3", "name": "Dak-e3d3", "candidates": 1, "count": 3, "as_first_hop": 0,
                            "as_last_hop": 3, "avg_snr": 0.33, "trackers": 1}
    assert reps["48d7"]["name"] == "Heuvel" and reps["48d7"]["as_first_hop"] == 2 and reps["48d7"]["avg_snr"] is None
    assert reps["5c39"]["name"] == "5c39 (2 kandidaten)" and reps["5c39"]["candidates"] == 2
    assert r["top_paths"][0] == {"path": "48d7,e3d3", "hops": 2, "count": 2, "avg_snr": 2.5}
    assert {"path": "", "hops": 0, "count": 1, "avg_snr": 10.0} in r["top_paths"]
    assert r["hops_by_tracker"] == [{"id": tid, "alias": "Björn", "messages": 2, "avg_hops": 1.0, "direct_pct": 50.0}]
    assert c.get("/api/stats/repeaters", params={"source": "companion"}).json()["repeaters"][0]["hash"] == "e3d3"
    s = c.get("/api/stats/summary").json()
    assert s["path_source"] == "openhop"
    assert s["trackers"][0]["first_hop"] == {"hash": "48d7", "name": "Heuvel", "count": 2}
    # retentie
    main.S.db.prune(now + 10)
    assert main.S.db._q("SELECT COUNT(*) n FROM message_paths")[0]["n"] == 0
    assert main.S.db._q("SELECT COUNT(*) n FROM stats_events")[0]["n"] == 0
