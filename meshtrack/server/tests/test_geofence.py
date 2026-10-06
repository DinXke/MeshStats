import pytest

from meshtrack.db import DB
from meshtrack.geofence import evaluate, validate

KEY = "ab" * 32


def setup():
    db = DB(":memory:")
    tid = db.add_tracker(KEY, "Test")
    gid = db.add_geofence(validate({"name": "Kazerne", "kind": "circle",
                                    "geom": {"center": [5.33, 50.93], "radius": 200}}))
    return db, tid, gid


def test_first_position_sets_state_without_event():
    db, tid, gid = setup()
    assert evaluate(db, tid, 50.93, 5.33, 1) == []
    assert db.geofence_inside(gid, tid) is True


def test_exit_and_enter():
    db, tid, gid = setup()
    evaluate(db, tid, 50.93, 5.33, 1)
    ev = evaluate(db, tid, 50.94, 5.33, 2)           # ~1,1 km verder
    assert [e["event"] for e in ev] == ["exit"]
    assert evaluate(db, tid, 50.941, 5.33, 3) == []    # blijft buiten
    ev = evaluate(db, tid, 50.9301, 5.3301, 4)
    assert [e["event"] for e in ev] == ["enter"]
    assert len(db.geofence_events()) == 2


def test_polygon_and_tracker_filter():
    db = DB(":memory:")
    a = db.add_tracker(KEY, "A")
    b = db.add_tracker("cd" * 32, "B")
    db.add_geofence(validate({"name": "Zone", "kind": "polygon", "trackers": [a],
                              "geom": [[5.3, 50.9], [5.4, 50.9], [5.4, 51.0], [5.3, 51.0]]}))
    evaluate(db, a, 50.85, 5.35, 1)
    assert evaluate(db, a, 50.95, 5.35, 2)[0]["event"] == "enter"
    evaluate(db, b, 50.85, 5.35, 1)
    assert evaluate(db, b, 50.95, 5.35, 2) == []        # B valt buiten de filter


def test_only_enter_notifies():
    db = DB(":memory:")
    tid = db.add_tracker(KEY, "A")
    db.add_geofence(validate({"name": "Z", "kind": "circle", "on_exit": False,
                              "geom": {"center": [5.33, 50.93], "radius": 200}}))
    evaluate(db, tid, 50.93, 5.33, 1)
    assert evaluate(db, tid, 50.95, 5.33, 2) == []
    assert evaluate(db, tid, 50.93, 5.33, 3)[0]["event"] == "enter"


@pytest.mark.parametrize("bad", [
    {"name": "", "kind": "circle", "geom": {"center": [5, 50], "radius": 100}},
    {"name": "x", "kind": "circle", "geom": {"center": [5, 50], "radius": 1}},
    {"name": "x", "kind": "polygon", "geom": [[5, 50], [5, 51]]},
    {"name": "x", "kind": "star", "geom": []},
    {"name": "x", "kind": "circle", "geom": {"center": [5, 50], "radius": 100}, "notify_pubkey": "zz"},
])
def test_validate_rejects(bad):
    with pytest.raises(ValueError):
        validate(bad)
