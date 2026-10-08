from meshtrack.config import Config
from meshtrack.db import DB
from meshtrack.ingest import handle, pick_ts

KEY = "2cb0c5eb473757805eab00f9dd0594c229d6e50b2acec6b57403b6259c9e126f"
NOW = 1_800_000_000


def setup():
    db = DB(":memory:")
    tid = db.add_tracker(KEY, "Björn")
    return db, Config(), tid


def test_stores_and_updates_summary():
    db, cfg, tid = setup()
    out = handle(db, cfg, KEY[:12], "T1|1|M|50.93|5.33|40|37|90|87|1.0|0", sender_ts=NOW - 5, snr=7.5,
                 path_len=2, now=NOW)
    assert out and out["tracker_id"] == tid and out["ts"] == NOW - 5
    t = db.tracker(tid)
    assert (t["last_lat"], t["last_bat"], t["last_spd"], t["last_state"]) == (50.93, 87, 37, "M")
    assert len(db.track(tid, 0)) == 1


def test_duplicate_seq_ignored():
    db, cfg, tid = setup()
    msg = "T1|5|M|50.93|5.33|||||1.0|0"
    assert handle(db, cfg, KEY[:12], msg, now=NOW)
    assert handle(db, cfg, KEY[:12], msg, now=NOW + 60) is None
    # na het dedup-venster mag dezelfde seq terug (wrap)
    assert handle(db, cfg, KEY[:12], msg, now=NOW + cfg.dedup_window_s + 1)


def test_unknown_tracker_logged():
    db, cfg, _ = setup()
    assert handle(db, cfg, "aaaaaaaaaaaa", "T1|1|M|50.9|5.3|||||1|0", now=NOW) is None
    assert db.unknown()[0]["reason"] == "onbekende tracker"
    # gewone chat van een onbekende is geen log waard
    assert handle(db, cfg, "aaaaaaaaaaaa", "hallo", now=NOW) is None
    assert len(db.unknown()) == 1


def test_unknown_version_and_invalid_logged():
    db, cfg, _ = setup()
    assert handle(db, cfg, KEY[:12], "T9|1|M", now=NOW) is None
    assert handle(db, cfg, KEY[:12], "T1|1|M", now=NOW) is None
    reasons = [u["reason"] for u in db.unknown()]
    assert any("onbekende versie" in r for r in reasons) and any("ongeldig" in r for r in reasons)


def test_suspect_does_not_move_marker():
    db, cfg, tid = setup()
    handle(db, cfg, KEY[:12], "T1|1|M|50.93|5.33|||||1.0|0", now=NOW)
    out = handle(db, cfg, KEY[:12], "T1|2|M|40.0|5.33|||||1.0|0", now=NOW + 60)
    assert out["suspect"] == 1
    assert db.tracker(tid)["last_lat"] == 50.93


def test_power_change_in_log():
    db, cfg, tid = setup()
    handle(db, cfg, KEY[:12], "T1|1|M|50.93|5.33|||||1.0|0|t|b", now=NOW)
    handle(db, cfg, KEY[:12], "T1|2|B||||||80|||c|u", now=NOW + 60)
    handle(db, cfg, KEY[:12], "T1|3|M|50.93|5.33|||||1.0|0|t|b", now=NOW + 120)
    ev = db.events(None, [], NOW - 10, NOW + 200, False, False, False, 50, power=True)
    assert [e["type"] for e in ev] == ["usb_off", "usb_on"]
    assert db.tracker(tid)["last_power"] == "b"


def test_pick_ts():
    assert pick_ts(NOW - 10, NOW, None) == NOW - 10
    assert pick_ts(NOW - 30 * 86400, NOW, None) == NOW      # klok fout
    assert pick_ts(NOW + 3600, NOW, None) == NOW            # toekomst
    assert pick_ts(NOW, NOW, 20) == NOW - 20                # fix_age


def test_delete_cascades():
    db, cfg, tid = setup()
    handle(db, cfg, KEY[:12], "T1|1|M|50.93|5.33|||||1.0|0", now=NOW)
    db.delete_tracker(tid)
    assert db.trackers() == [] and db.track(tid, 0) == []


def test_extra_points_stored_chronologically_and_deduplicated():
    db, cfg, tid = setup()
    fts = NOW - 2
    msg = f"T1|10|M|50.93000|5.33000|40|50|90|80|0.9|1|t|b|{fts}|30,-300,120,48;20,-200,80,50;10,-100,40,52"
    out = handle(db, cfg, KEY[:12], msg, now=NOW)
    assert [round(e["ts"]) for e in out["extras"]] == [fts - 30, fts - 20, fts - 10]   # oudste eerst
    tr = db.track(tid, 0)
    assert [p["ts"] for p in tr] == [fts - 30, fts - 20, fts - 10, fts]
    assert (tr[0]["lat"], tr[0]["lon"], tr[0]["spd"]) == (50.927, 5.3312, 48)
    assert db.tracker(tid)["last_lat"] == 50.93                 # hoofdpunt blijft de laatste positie
    # volgend bericht herhaalt twee punten (ACK was verloren): niet dubbel
    msg2 = f"T1|11|M|50.93100|5.33000|40|50|90|80|0.9|1|t|b|{fts + 10}|20,-100,0,50;10,-100,0,52"
    handle(db, cfg, KEY[:12], msg2, now=NOW + 12)
    assert [p["ts"] for p in db.track(tid, 0)] == [fts - 30, fts - 20, fts - 10, fts, fts + 10]


def test_extra_requires_main_point():
    import pytest
    from meshtrack.protocol import ProtocolError, parse
    with pytest.raises(ProtocolError):
        parse("T1|1|N||||||80||||t|b||10,1,1,1")
    with pytest.raises(ProtocolError):
        parse("T1|1|M|50.9|5.3|||||1|0|t|b|1800000000|10,1,1")
