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


# ---- vaste intervallen (geen ritme volgens de ontvangst sinds firmware 0.7.0) ---------

def test_from_dict_ignores_legacy_and_unknown_keys():
    p = Params.from_dict({"min_interval_s": 45, "adaptive": 1, "fast_interval_s": 20, "fast_keep": 2,
                          "fast_ack_s": 10, "slow_after": 3, "slow_factor": 3, "onzin": "x", "sample_s": None})
    assert p.min_interval_s == 45 and p.sample_s == Params().sample_s
    assert not any(hasattr(p, k) for k in ("adaptive", "fast_interval_s", "slow_factor"))


def test_min_interval_blocks_turn_and_max_interval():
    p = Params(min_interval_s=120, max_interval_s=60)
    st = started()
    lat, lon = moved(20)
    assert decide(st, p, 119, lat, lon, 30, 90) is None          # ook bocht en max_interval wachten
    assert decide(st, p, 120, lat, lon, 30, 90) == "bocht"


def test_max_interval_zero_never_forces():
    p = Params(max_interval_s=0)
    st = started()
    lat, lon = moved(10)
    assert decide(st, p, 10 ** 6, lat, lon, 5, 0) is None


def test_turn_needs_known_course():
    st = started()
    lat, lon = moved(20)
    assert decide(st, P, 60, lat, lon, 20, None) is None         # geen koers (onder 3 km/u)
    st2 = RuleState()
    st2.sent(0, LAT, LON, None)                                  # vorige zending zonder koers
    assert decide(st2, P, 60, lat, lon, 20, 90) is None


def test_intervals_fixed_whatever_happens():
    """Geen ACK-afhankelijk ritme: na elke zending gelden dezelfde min/max-intervallen."""
    st = started()
    for k in range(1, 6):
        t = k * 600
        lat, lon = moved(10 * k)
        assert decide(st, P, t - 1, lat, lon, 5, 0) is None
        assert decide(st, P, t, lat, lon, 5, 0) == "max_interval"
        st.sent(t, lat, lon, 0)
