import pytest

from meshtrack.protocol import ProtocolError, UnknownVersion, is_meshtrack, is_suspect, parse

BBOX = (2.0, 49.0, 7.8, 54.0)


def test_full_message():
    r = parse("T1|412|M|50.93012|5.33781|42|37|184|87|1.2|0")
    assert r.seq == 412 and r.state == "M"
    assert r.lat == pytest.approx(50.93012) and r.lon == pytest.approx(5.33781)
    assert (r.alt_m, r.spd_kmh, r.crs_deg, r.bat_pct, r.fix_age_s) == (42, 37, 184, 87, 0)
    assert r.hdop == pytest.approx(1.2)
    assert r.mode is None and r.has_fix


def test_optional_fields_and_mode():
    r = parse("T1|7|H|50.9|5.3||||55||120|t")
    assert r.alt_m is None and r.spd_kmh is None and r.hdop is None
    assert r.mode == "t" and r.bat_pct == 55


def test_no_fix():
    r = parse("T1|8|N||||||40||")
    assert not r.has_fix and r.state == "N" and r.bat_pct == 40 and r.hdop is None


def test_mode_message_without_position():
    r = parse("T1|9|B||||||80|||c")
    assert r.state == "B" and r.mode == "c" and not r.has_fix


@pytest.mark.parametrize("bad", [
    "hallo",
    "T1|1|M|50.9|5.3",                      # te weinig velden
    "T1|70000|M|50.9|5.3|0|0|0|50|1|0",     # seq te groot
    "T1|1|X|50.9|5.3|0|0|0|50|1|0",         # onbekende state
    "T1|1|M|||0|0|0|50|1|0",                # M zonder positie
    "T1|1|H|50.9||0|0|0|50|1|0",            # lat zonder lon
    "T1|1|M|95.0|5.3|0|0|0|50|1|0",         # lat buiten bereik
    "T1|1|M|50.9|5.3|0|0|400|50|1|0",       # koers buiten bereik
    "T1|1|M|50.9|5.3|0|0|0|150|1|0",        # batterij buiten bereik
    "T1|1|M|50.9|5.3|0|0|0|50|1|0|x",       # onbekende mode
])
def test_invalid(bad):
    with pytest.raises(ProtocolError):
        parse(bad)


def test_unknown_version():
    with pytest.raises(UnknownVersion):
        parse("T2|1|M|50.9|5.3|0|0|0|50|1|0")
    assert is_meshtrack("T2|1|M")
    assert not is_meshtrack("Tom zegt hallo")


def test_suspect():
    assert not is_suspect(parse("T1|1|M|50.9|5.3|0|0|0|50|1.0|0"), BBOX)
    assert is_suspect(parse("T1|1|M|40.0|5.3|0|0|0|50|1.0|0"), BBOX)   # buiten gebied
    assert is_suspect(parse("T1|1|M|50.9|5.3|0|0|0|50|7.5|0"), BBOX)   # slechte hdop
    assert not is_suspect(parse("T1|1|N||||||50||"), BBOX)


def test_manual_state():
    r = parse("T1|10|P|50.93|5.33|40|0||91|1.1|2|t")
    assert r.state == "P" and r.has_fix and r.mode == "t"
