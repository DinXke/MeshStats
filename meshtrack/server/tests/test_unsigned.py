"""1.4.2: niet-ondertekende kanaalberichten per tracker toch aanvaarden (allow_unsigned)."""
import os
import time

import pytest
from fastapi.testclient import TestClient

from meshtrack import auth, paths
from meshtrack.alerts import AlertManager
from meshtrack.config import Config
from meshtrack.db import DB
from meshtrack.ingest import handle

KEY = "2cb0c5eb473757805eab00f9dd0594c229d6e50b2acec6b57403b6259c9e126f"
PK, PK8 = KEY[:12], KEY[:8]
AUTH = "00112233445566778899aabbccddeeff"
SECRET = "8b3387e9c5cdea6ac9e5edbaa115cd72"


def msg(seq, ts, state="M", lat=50.93, lon=5.33, extra=""):
    return f"T1|{seq}|{state}|{lat:.5f}|{lon:.5f}|40|10|90|70|0.9|0|t|b|{ts}" + (f"|{extra}" if extra else "")


class FakeMesh:
    connected = True

    def __init__(self):
        self.sent = []

    async def send_channel(self, slot, text, scope=""):
        self.sent.append((slot, text, scope))

    def status(self):
        return {"connected": True}

    def contact_list(self):
        return []


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
            sent = []
            orig = main.S.hub.send

            async def cap(m):
                sent.append(m)
                await orig(m)
            mp.setattr(main.S.hub, "send", cap)
            tid = main.S.db.add_tracker(KEY, "Björn")
            main.S.db.set_authkey(tid, AUTH)
            cid = main.S.db.save_channel(None, {"name": "trk", "secret": SECRET, "slot": 3, "require_sig": 1,
                                                "active": 1, "region": "be"})

            def chan(rest, signed=True, slot=3):
                tag = main.channel_tag(AUTH, f"{PK8}|{rest}") if signed else "-"
                c.portal.call(main.on_channel, slot, f"Björn: T1C|{PK8}|{tag}|{rest}", None, 5.0, 1)
            yield c, main, tid, cid, chan, mesh, sent


def login(c):
    r = c.post("/api/login", json={"user": "admin", "password": "beheerder1"})
    assert r.status_code == 200, r.text


def positions(db, tid):
    return db._q("SELECT seq, extra, unsigned FROM positions WHERE tracker_id=? ORDER BY ts", (tid,))


def test_unsigned_rejected_by_default(app):
    c, main, tid, cid, chan, mesh, sent = app
    now = int(time.time())
    chan(msg(1, now - 10)[3:], signed=False)
    assert positions(main.S.db, tid) == []
    assert "niet ondertekend" in main.S.db.unknown()[0]["reason"]
    login(c)
    tr = next(x for x in c.get("/api/trackers").json() if x["id"] == tid)
    assert tr["allow_unsigned"] == 0


def test_toggle_via_api_accepts_and_marks_with_extras(app):
    c, main, tid, cid, chan, mesh, sent = app
    db = main.S.db
    login(c)
    r = c.put(f"/api/trackers/{tid}", json={"allow_unsigned": True})
    assert r.status_code == 200 and r.json()["tracker"]["allow_unsigned"] == 1
    assert any(a["action"] == "niet-ondertekend aanvaarden aan" for a in db.audit_log())
    now = int(time.time())
    chan(msg(1, now - 10, extra="~15;-412,201;-398,190")[3:], signed=False)
    rows = positions(db, tid)
    assert len(rows) == 3 and all(r["unsigned"] == 1 for r in rows) and sum(r["extra"] for r in rows) == 2
    live = [m for m in sent if m["type"] == "position"]
    assert len(live) == 3 and all(m["position"]["unsigned"] == 1 for m in live)
    assert db._q("SELECT n FROM stats_events WHERE kind='unsigned_ok'") == [{"n": 1}]
    assert "unsigned_ok" in c.get("/api/stats/events").json()["kinds"]
    pts = c.get(f"/api/trackers/{tid}/track").json()
    assert [p["unsigned"] for p in pts] == [1, 1, 1]
    csv = c.get(f"/api/trackers/{tid}/export?fmt=csv").text.splitlines()
    assert csv[0].endswith(",niet_ondertekend") and csv[1].endswith(",1")
    assert "<desc>niet ondertekend</desc>" in c.get(f"/api/trackers/{tid}/export").text
    # SOS zonder handtekening: verwerkt, maar nooit bevestigd
    chan(msg(2, now - 5, state="E")[3:], signed=False)
    assert db.tracker(tid)["last_state"] == "E" and not main._bg and mesh.sent == []   # geen T1A ingepland

    # gewoon uitzetten via de API
    r = c.put(f"/api/trackers/{tid}", json={"allow_unsigned": False})
    assert r.json()["tracker"]["allow_unsigned"] == 0
    assert db.audit_log()[1]["action"] == "niet-ondertekend aanvaarden uit"


def test_signed_message_turns_toggle_off(app):
    c, main, tid, cid, chan, mesh, sent = app
    db = main.S.db
    db.update_tracker(tid, allow_unsigned=True)
    now = int(time.time())
    chan(msg(1, now - 10)[3:], signed=False)
    assert db.tracker(tid)["allow_unsigned"] == 1
    sent.clear()
    chan(msg(2, now - 5)[3:])                                      # geldig ondertekend
    assert db.tracker(tid)["allow_unsigned"] == 0
    a = db.audit_log()[0]
    assert (a["who"], a["action"]) == ("systeem", "niet-ondertekend aanvaarden automatisch uit")
    assert "geldig ondertekend bericht" in a["detail"]
    assert any(m["type"] == "tracker" and m["tracker"]["allow_unsigned"] == 0 for m in sent)
    assert [r["unsigned"] for r in positions(db, tid)] == [1, 0]
    chan(msg(3, now - 2)[3:], signed=False)                         # weer geweigerd
    assert len(positions(db, tid)) == 2


def test_unsigned_on_channel_without_require_sig_is_marked(app):
    c, main, tid, cid, chan, mesh, sent = app
    db = main.S.db
    db.save_channel(cid, {"name": "trk", "secret": SECRET, "slot": 3, "require_sig": 0, "active": 1, "region": "be"})
    chan(msg(1, int(time.time()) - 10)[3:], signed=False)
    assert [r["unsigned"] for r in positions(db, tid)] == [1]
    assert db.tracker(tid)["allow_unsigned"] == 0


def test_alert_text_says_unsigned(app):
    c, main, tid, cid, chan, mesh, sent = app
    db = main.S.db
    db.save_alert_rule(None, {"name": "SOS", "active": True, "events": ["E"], "trackers": [], "channels": [],
                              "recipients": [{"pubkey": "aa" * 32, "name": "Wacht"}], "cooldown_s": 0})
    db.update_tracker(tid, allow_unsigned=True)
    now = int(time.time())
    chan(msg(1, now - 10, state="E")[3:], signed=False)
    assert "Björn stuurt SOS (niet ondertekend) @50.93000,5.33000" in db.alert_log()[0]["text"]
    chan(msg(2, now - 5, state="E")[3:])
    assert "(niet ondertekend)" not in db.alert_log()[0]["text"]


def test_alert_text_unit():
    t = {"alias": "X", "id": 1}
    assert "(niet ondertekend)" in AlertManager.text(t, "E", {"lat": 1.0, "lon": 2.0, "unsigned": 1}, None)
    assert "(niet ondertekend)" not in AlertManager.text(t, "E", {"lat": 1.0, "lon": 2.0, "unsigned": 0}, None)


def test_ingest_flag_and_migration_on_old_db(tmp_path):
    p = str(tmp_path / "oud.sqlite3")
    db = DB(p)
    tid = db.add_tracker(KEY, "Björn")
    handle(db, Config(), PK, msg(1, int(time.time()) - 100), now=int(time.time()))
    db._c.execute("DROP INDEX positions_stats")
    db._c.execute("ALTER TABLE positions DROP COLUMN unsigned")      # toestand van vóór 1.4.2
    db._c.execute("ALTER TABLE trackers DROP COLUMN allow_unsigned")
    db._c.close()
    for _ in range(2):                                                # tweemaal: idempotent
        db = DB(p)
        assert db._q("SELECT unsigned FROM positions WHERE tracker_id=?", (tid,)) == [{"unsigned": 0}]
        assert db.tracker(tid)["allow_unsigned"] == 0
        db._c.close()
    db = DB(p)
    r = handle(db, Config(), PK, msg(2, int(time.time()) - 50, extra="~15;-412,201"), now=int(time.time()), unsigned=True)
    assert r["unsigned"] == 1 and r["extras"][0]["unsigned"] == 1
    assert [x["unsigned"] for x in db._q("SELECT unsigned FROM positions ORDER BY ts")] == [0, 1, 1]
