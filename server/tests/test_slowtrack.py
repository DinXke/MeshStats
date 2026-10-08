"""SlowTrack (state L, firmware 0.8.0): gelogde punten in bursts, meestal ouder dan de live-positie."""
import os
import sqlite3
import time

import pytest
from fastapi.testclient import TestClient

from meshtrack import auth, geofence
from meshtrack.config import Config
from meshtrack.db import DB
from meshtrack.ingest import handle
from meshtrack.protocol import ProtocolError, parse

KEY = "2cb0c5eb473757805eab00f9dd0594c229d6e50b2acec6b57403b6259c9e126f"
PK = KEY[:12]
NOW = 1_800_000_000


def setup():
    db = DB(":memory:")
    tid = db.add_tracker(KEY, "Björn")
    return db, Config(), tid


def m(seq, ts, lat=50.93, lon=5.33, bat=80, state="M"):
    return f"T1|{seq}|{state}|{lat:.5f}|{lon:.5f}|40|30|90|{bat}|0.9|1|t|b|{ts}"


def slow(seq, ts, lat=50.93, lon=5.33, bat=70, extra=""):
    return f"T1|{seq}|L|{lat:.5f}|{lon:.5f}|40|10|90|{bat}|0.9|0|t|b|{ts}" + (f"|{extra}" if extra else "")


def test_parse_L():
    r = parse(slow(7, NOW, extra="~120;10,5;10,5@300"))
    assert r.state == "L" and r.fix_ts == NOW and r.has_fix
    assert [e[0] for e in r.extra] == [120, 420]               # oudere punten, cumulatief
    assert (r.extra[1][1], r.extra[1][2]) == (50.9302, 5.3301)
    with pytest.raises(ProtocolError):                          # L zonder positie
        parse("T1|7|L|||||||||t|b|1800000000")


def test_old_L_does_not_move_current_position():
    db, cfg, tid = setup()
    handle(db, cfg, PK, m(1, NOW - 10, bat=80), now=NOW)
    out = handle(db, cfg, PK, slow(2, NOW - 600, lat=51.0, bat=40, extra="~300;10,10"), now=NOW + 5)
    assert out and out["stored"] and len(out["extras"]) == 1
    t = db.tracker(tid)
    assert (t["last_ts"], t["last_lat"], t["last_state"], t["last_bat"], t["last_seq"]) == (NOW - 10, 50.93, "M", 80, 1)
    assert (t["last_rx"], t["last_slow_rx"], t["last_slow_ts"]) == (NOW + 5, NOW + 5, NOW - 600)


def test_newer_L_moves_current_position_but_keeps_state():
    db, cfg, tid = setup()
    handle(db, cfg, PK, m(1, NOW - 1000, state="S"), now=NOW - 990)
    handle(db, cfg, PK, slow(2, NOW - 5, lat=51.0, bat=60), now=NOW)
    t = db.tracker(tid)
    assert (t["last_ts"], t["last_lat"], t["last_bat"], t["last_state"]) == (NOW - 5, 51.0, 60, "S")
    # zonder eerdere toestand wordt het L
    db2, _, tid2 = setup()
    handle(db2, cfg, PK, slow(1, NOW - 5), now=NOW)
    assert db2.tracker(tid2)["last_state"] == "L"


def test_track_is_chronological_with_L_points():
    db, cfg, tid = setup()
    t0 = NOW - 3600
    handle(db, cfg, PK, m(1, t0), now=t0 + 2)
    handle(db, cfg, PK, m(2, t0 + 600, lat=50.94), now=t0 + 602)
    # SlowTrack-burst komt pas later: hoofdpunt t0+300, extra punten t0+180 en t0+60
    handle(db, cfg, PK, slow(3, t0 + 300, extra="~120;-10,0;-10,0"), now=t0 + 900)
    tr = db.track(tid, 0)
    assert [p["ts"] for p in tr] == [t0, t0 + 60, t0 + 180, t0 + 300, t0 + 600]
    assert [p["state"] for p in tr] == ["M", "L", "L", "L", "M"]
    assert db.tracker(tid)["last_ts"] == t0 + 600


def test_no_duplicate_on_same_fix_ts():
    db, cfg, tid = setup()
    fts = NOW - 300
    handle(db, cfg, PK, m(1, fts), now=NOW - 290)
    # hoofdpunt valt samen met het M-punt, het extra punt is nieuw
    out = handle(db, cfg, PK, slow(2, fts, extra="~120;5,5"), now=NOW)
    assert out and not out["stored"] and [e["ts"] for e in out["extras"]] == [fts - 120]
    assert [p["ts"] for p in db.track(tid, 0)] == [fts - 120, fts]
    # zelfde punten opnieuw met een andere seq: niets nieuws, wel "gehoord"
    assert handle(db, cfg, PK, slow(3, fts, extra="~120;5,5"), now=NOW + 60) is None
    assert len(db.track(tid, 0)) == 2
    assert db.tracker(tid)["last_slow_rx"] == NOW + 60


def test_seq_dedupe_separate_for_L():
    db, cfg, tid = setup()
    handle(db, cfg, PK, m(5, NOW - 100), now=NOW - 95)
    # aparte teller in de firmware: L met dezelfde seq is geen herhaling
    assert handle(db, cfg, PK, slow(5, NOW - 1000), now=NOW)
    # een echte herhaling van het L-bericht wel
    assert handle(db, cfg, PK, slow(5, NOW - 1000), now=NOW + 30) is None
    # en een M-herhaling blijft een herhaling
    assert handle(db, cfg, PK, m(5, NOW - 100), now=NOW + 40) is None
    assert len(db.track(tid, 0)) == 2


def test_burst_of_several_L_messages_after_newer_M():
    db, cfg, tid = setup()
    t0 = NOW - 1800
    handle(db, cfg, PK, m(40, NOW - 30, lat=50.95), now=NOW - 25)      # nieuwste live-positie al binnen
    for i, seq in enumerate((37, 38, 39)):                             # oudere seqs, na de M aangekomen
        base = t0 + 900 + i * 300                                      # 3 punten per bericht, 100 s uit elkaar
        assert handle(db, cfg, PK, slow(seq, base, extra="~100;-1,0;-1,0"), now=NOW + i)
    tr = db.track(tid, 0)
    ts = [p["ts"] for p in tr]
    assert ts == sorted(ts) and len(ts) == 10
    assert [p["state"] for p in tr].count("L") == 9
    t = db.tracker(tid)
    assert (t["last_ts"], t["last_lat"], t["last_state"]) == (NOW - 30, 50.95, "M")


def test_migration_adds_slow_columns(tmp_path):
    path = str(tmp_path / "oud.sqlite3")
    db = DB(path)
    tid = db.add_tracker(KEY, "Björn")
    handle(db, Config(), PK, m(1, NOW - 10), now=NOW)
    db._c.close()
    c = sqlite3.connect(path)                                          # schema van vóór 1.1
    c.execute("ALTER TABLE trackers DROP COLUMN last_slow_rx")
    c.execute("ALTER TABLE trackers DROP COLUMN last_slow_ts")
    c.commit()
    c.close()
    db = DB(path)
    t = db.tracker(tid)
    assert t["last_lat"] == 50.93 and t["last_slow_rx"] is None and "last_slow_ts" in t
    assert len(db.track(tid, 0)) == 1


# ---- live-verwerking (zones, meldingen, WebSocket) ------------------------------------

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
    with TestClient(main.app) as c:
        fired, sent = [], []
        monkeypatch.setattr(main.S.alerts, "fire", lambda t, ev, *a, **k: fired.append(ev) or 0)

        async def send(msg):
            sent.append(msg)
        monkeypatch.setattr(main.S.hub, "send", send)
        tid = main.S.db.add_tracker(KEY, "Björn")
        gid = main.S.db.add_geofence(geofence.validate(
            {"name": "Thuis", "kind": "circle", "geom": {"center": [5.33, 50.93], "radius": 500}}))

        def run(text):
            c.portal.call(main.process, PK, text, None, 5.0, 1)
        yield main, run, tid, gid, fired, sent


def test_old_L_points_fire_no_alerts_or_zones(app):
    main, run, tid, gid, fired, sent = app
    now = int(time.time())
    run(m(1, now - 20))                                    # binnen de zone: zet de toestand
    run(m(2, now - 10, bat=80))
    fired.clear()
    sent.clear()
    # oude punten ver buiten de zone, lage batterij, andere voeding: niets mag afgaan
    run(slow(3, now - 600, lat=51.2, bat=10, extra="~120;0,10").replace("|t|b|", "|t|u|"))
    assert fired == []
    assert main.S.db.geofence_inside(gid, tid) is True
    assert main.S.db.geofence_events() == []
    pos = [s["position"] for s in sent if s["type"] == "position"]
    assert [p["ts"] for p in pos] == [now - 720, now - 600]
    assert all(p["slow"] == 1 and p["history"] == 1 and p["state"] == "L" for p in pos)
    assert all(s["tracker"]["last_ts"] == now - 10 for s in sent if s["type"] == "position")


def test_newer_L_point_is_live_for_zones(app):
    main, run, tid, gid, fired, sent = app
    now = int(time.time())
    run(m(1, now - 600))                                   # binnen de zone
    fired.clear()
    sent.clear()
    # SlowTrack-punten nieuwer dan de live-positie: oudste nog binnen, nieuwste buiten
    run(slow(2, now - 5, lat=50.95, extra="~200;-2000,0"))
    assert fired == ["zone_out"]                            # geen melding voor L zelf
    pos = [s["position"] for s in sent if s["type"] == "position"]
    assert [p["history"] for p in pos] == [0, 0]
    assert main.S.db.tracker(tid)["last_lat"] == 50.95
