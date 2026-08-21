"""De benuttingsgrafiek is afgeleid, niet opgeslagen: leidt metric_history de
reeks correct af uit de airtime-teller? Regressietest voor de klacht "ik zie een
waarde maar geen statistieken"."""
from app import db


class _Rep(dict):
    pass


def test_benutting_afgeleid_uit_airtime(monkeypatch):
    # oplopende airtime-teller (minuten), 3 punten van 10 min uit elkaar
    raw = [
        ("2026-08-21T10:00:00Z", 100.0),
        ("2026-08-21T10:10:00Z", 100.5),   # +0,5 min over 10 min -> 5,0 %
        ("2026-08-21T10:20:00Z", 102.5),   # +2,0 min over 10 min -> 20,0 %
    ]
    monkeypatch.setattr(db, "tsdb", type("T", (), {"history": staticmethod(lambda *a, **k: raw)}))
    rep = _Rep(slug="x", id=1)
    pts = db.metric_history(rep, "airtime_utilization", 24)
    assert pts == [("2026-08-21T10:10:00Z", 5.0), ("2026-08-21T10:20:00Z", 20.0)]


def test_tellerreset_wordt_overgeslagen(monkeypatch):
    raw = [
        ("2026-08-21T10:00:00Z", 500.0),
        ("2026-08-21T10:10:00Z", 3.0),     # reset -> negatieve delta -> overslaan
        ("2026-08-21T10:20:00Z", 4.0),     # +1,0 min over 10 min -> 10,0 %
    ]
    monkeypatch.setattr(db, "tsdb", type("T", (), {"history": staticmethod(lambda *a, **k: raw)}))
    pts = db.metric_history(_Rep(slug="x", id=1), "rx_airtime_utilization", 24)
    assert pts == [("2026-08-21T10:20:00Z", 10.0)]
