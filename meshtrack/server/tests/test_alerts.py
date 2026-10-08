from meshtrack.alerts import AlertManager
from meshtrack.db import DB
from meshtrack import settings as setmod


class Cfg:
    retention_days = 90
    stale_after_s = 90000


def setup(alert_sims=False):
    db = DB(":memory:")
    st = setmod.effective(Cfg(), {"alert_sims": alert_sims})
    am = AlertManager(db, mesh=None, get_settings=lambda: st)
    tid = db.add_tracker("aa" * 32, "Ziekenwagen 1")
    sid = db.add_tracker("bb" * 32, "Sim", kind="sim")
    db.save_alert_rule(None, {"name": "Wakker", "active": True, "events": ["W", "E"], "trackers": [],
                              "recipients": [{"pubkey": "cc" * 32, "name": "Post"}, {"pubkey": "dd" * 32, "name": "Gsm"}],
                              "cooldown_s": 600})
    return db, am, db.tracker(tid), db.tracker(sid)


def test_fire_queues_one_dm_per_recipient():
    db, am, t, _ = setup()
    assert am.fire(t, "W", {"lat": 50.93, "lon": 5.33, "bat": 80, "ts": 0}) == 2
    assert am.queue.qsize() == 2
    log = db.alert_log()
    assert {l["recipient"] for l in log} == {"Post", "Gsm"} and all(l["status"] == "wacht" for l in log)
    assert "Ziekenwagen 1 is wakker geworden door beweging @50.93000,5.33000 B80%" in log[0]["text"]


def test_cooldown_and_unmatched_events():
    db, am, t, _ = setup()
    assert am.fire(t, "W") == 2
    assert am.fire(t, "W") == 0          # binnen de cooldown
    assert am.fire(t, "E") == 2          # andere gebeurtenis: eigen cooldown
    assert am.fire(t, "S") == 0          # niet in de regel


def test_sims_only_when_enabled():
    _, am, _, sim = setup()
    assert am.fire(sim, "W") == 0
    _, am2, _, sim2 = setup(alert_sims=True)
    assert am2.fire(sim2, "W") == 2


def test_settings_validation():
    import pytest
    assert setmod.validate({"alert_gap_s": "20", "alert_sims": 1}) == {"alert_gap_s": 20, "alert_sims": True}
    with pytest.raises(ValueError):
        setmod.validate({"alert_gap_s": 1})
    with pytest.raises(ValueError):
        setmod.validate({"bestaat_niet": 1})


def test_lost_seen_event():
    db, am, t, _ = setup()
    db.save_alert_rule(None, {"name": "Verloren", "active": True, "events": ["lost_seen"], "trackers": [t["id"]],
                              "recipients": [{"pubkey": "ee" * 32, "name": "Wacht"}], "cooldown_s": 0})
    assert am.fire(t, "lost_seen", {"lat": 51.0, "lon": 4.0, "ts": 0}) == 1
    assert "Ziekenwagen 1 (VERLOREN) is weer opgedoken @51.00000,4.00000" in db.alert_log()[0]["text"]


def test_rule_with_channel_follows_tracking_channel():
    db, am, t, _ = setup()
    other = db.tracker(db.add_tracker("ff" * 32, "Brandweer"))
    cid = db.save_channel(None, {"name": "#ambu", "secret": "00" * 16, "slot": 3, "require_sig": True, "active": True})
    db.set_tracker_channel(t["id"], cid)
    db.save_alert_rule(None, {"name": "Kanaal", "active": True, "events": ["P"], "trackers": [], "channels": [cid],
                              "recipients": [{"pubkey": "ee" * 32, "name": "Wacht"}], "cooldown_s": 0})
    assert am.fire(db.tracker(t["id"]), "P") == 1
    assert am.fire(other, "P") == 0
    db.set_tracker_channel(other["id"], cid)              # stuurt later ook op dit kanaal
    assert am.fire(db.tracker(other["id"]), "P") == 1


def test_personal_rule_only_for_trackers_owner_sees():
    db, _, t, _ = setup()
    st = setmod.effective(Cfg(), {})
    am = AlertManager(db, mesh=None, get_settings=lambda: st, can_see=lambda uid, tid: tid == t["id"])
    other = db.tracker(db.add_tracker("ff" * 32, "Geheim"))
    db.save_alert_rule(None, {"name": "Mijn", "active": True, "events": ["P"], "trackers": [],
                              "recipients": [{"pubkey": "ee" * 32, "name": "Ik"}], "cooldown_s": 0}, owner=7)
    assert am.fire(t, "P") == 1
    assert am.fire(other, "P") == 0      # eigenaar ziet deze tracker niet
