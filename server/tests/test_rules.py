"""Testgevallen voor de bewegingsregels. Firmware MtRules.cpp moet dezelfde
uitkomsten geven (zelfde tabel komt in firmware/test)."""
from meshtrack.geo import angle_diff, bearing, haversine, offset, point_in_polygon
from meshtrack.rules import Params, RuleState, decide, heartbeat_due, stillness

LAT, LON = 50.93, 5.33
P = Params()


def moved(m_north: float, m_east: float = 0.0):
    return offset(LAT, LON, m_north, m_east)


def started() -> RuleState:
    st = RuleState()
    assert decide(st, P, 0, LAT, LON, 30, 0) == "eerste"
    st.sent(0, LAT, LON, 0)
    return st


def test_rate_limit_blocks_everything():
    st = started()
    lat, lon = moved(5000)
    assert decide(st, P, 59, lat, lon, 90, 0) is None


def test_distance_and_speed():
    st = started()
    lat, lon = moved(150)
    assert decide(st, P, 60, lat, lon, 30, 0) == "afstand"
    lat, lon = moved(80)
    assert decide(st, P, 60, lat, lon, 30, 0) is None          # te weinig afstand


def test_slow_walker_only_by_max_interval():
    st = started()
    lat, lon = moved(150)
    assert decide(st, P, 120, lat, lon, 5, 0) is None           # onder min_speed
    assert decide(st, P, 600, lat, lon, 5, 0) == "max_interval"


def test_min_speed_zero_disables_speed_check():
    st = started()
    lat, lon = moved(150)
    assert decide(st, Params(min_speed_kmh=0), 60, lat, lon, 4, 0) == "afstand"


def test_turn():
    st = started()
    lat, lon = moved(20)
    assert decide(st, P, 60, lat, lon, 20, 45) == "bocht"
    assert decide(st, P, 60, lat, lon, 20, 20) is None          # te kleine bocht
    assert decide(st, P, 60, lat, lon, 3, 90) is None           # te traag: koers is ruis
    assert decide(st, P, 60, lat, lon, 20, 330) == "bocht"      # over noord heen (30 graden)


def test_stillness_and_heartbeat():
    st = started()
    assert not stillness(st, P, 100, 0.5)
    assert not stillness(st, P, 399, 0.5)
    assert stillness(st, P, 400, 0.5)
    st.sleeping = True
    st.last_heartbeat = 400
    assert not stillness(st, P, 500, 0.5)                        # al aan het slapen
    assert not heartbeat_due(st, P, 400 + P.heartbeat_s - 1)
    assert heartbeat_due(st, P, 400 + P.heartbeat_s)
    assert not stillness(st, P, 600, 12)                         # beweegt weer
    assert st.still_since is None


def test_geo_helpers():
    assert abs(haversine(50.93, 5.33, 50.94, 5.33) - 1111.9) < 1
    assert abs(bearing(50.93, 5.33, 50.94, 5.33)) < 0.01
    assert abs(bearing(50.93, 5.33, 50.93, 5.34) - 90) < 0.1
    assert angle_diff(350, 10) == 20 and angle_diff(90, 270) == 180
    lat, lon = offset(50.93, 5.33, 100, 0)
    assert abs(haversine(50.93, 5.33, lat, lon) - 100) < 0.5
    sq = [[5.0, 50.0], [6.0, 50.0], [6.0, 51.0], [5.0, 51.0], [5.0, 50.0]]
    assert point_in_polygon(50.5, 5.5, sq) and not point_in_polygon(51.5, 5.5, sq)


# ---- ritme volgens de ontvangst ------------------------------------------------

from meshtrack.rules import intervals, link_result  # noqa: E402


def _moving_state(p):
    st = RuleState()
    st.sent(0, 50.93, 5.33, 90)
    return st


def test_fast_after_quick_ack_sends_every_fast_interval():
    p = Params(min_interval_s=60, max_interval_s=600, fast_interval_s=20)
    st = _moving_state(p)
    link_result(st, p, True, 4)
    assert st.fast and intervals(st, p) == (20, 600)
    # 25 s later, 60 m verder, onder de afstandsregel (100 m): toch "snel"
    assert decide(st, p, 25, 50.93054, 5.33, 30, 90) == "snel"
    # niet verplaatst: geen dubbel punt
    assert decide(st, p, 25, 50.93, 5.33, 30, 90) is None


def test_slow_ack_does_not_make_fast():
    p = Params(fast_interval_s=20, fast_ack_s=10)
    st = _moving_state(p)
    link_result(st, p, True, 25)
    assert not st.fast


def test_fast_survives_fast_keep_misses_then_drops_and_slows():
    p = Params(min_interval_s=60, max_interval_s=600, fast_interval_s=20, fast_keep=2, slow_after=3, slow_factor=3)
    st = _moving_state(p)
    link_result(st, p, True, 3)
    link_result(st, p, False)
    link_result(st, p, False)
    assert st.fast and not st.slow           # twee missers: nog snel
    link_result(st, p, False)
    assert not st.fast and st.slow           # derde: terug, en trager
    assert intervals(st, p) == (180, 1800)
    assert decide(st, p, 120, 50.94, 5.33, 50, 90) is None   # binnen 3 x min_interval
    link_result(st, p, True, 2)
    assert st.fast and not st.slow and st.fails == 0


def test_fast_off_when_interval_zero():
    p = Params(fast_interval_s=0)
    st = _moving_state(p)
    link_result(st, p, True, 1)
    assert not st.fast and intervals(st, p) == (p.min_interval_s, p.max_interval_s)


def test_adaptive_off_never_changes_rhythm():
    p = Params(adaptive=0, fast_interval_s=20)
    st = _moving_state(p)
    link_result(st, p, True, 1)
    assert not st.fast
    for _ in range(5):
        link_result(st, p, False)
    assert not st.slow and intervals(st, p) == (p.min_interval_s, p.max_interval_s)
