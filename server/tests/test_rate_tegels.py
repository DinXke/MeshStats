"""De rate-TEGEL mag geen fossiel tonen.

De klacht was "er missen statistieken", en de grafiek was maar de helft ervan.
De andere helft is erger: op de nodepagina stond "Ontvangstrate 11,3 msg/min"
met "laatste update: net nu" erboven, terwijl dat cijfer van 13 augustus was --
de dag waarop de Home Assistant-weg (die de rates zelf uitrekende en meestuurde)
uit de keten ging. Een leeg vakje is te zien; een oud getal niet.

Twee eisen dus: de tegel rekent live uit de teller, en waar dat niet kan komt er
een streep in plaats van het oude cijfer.
"""
from app import db, routes_public


class _Rep(dict):
    pass


def test_de_tegel_rekent_uit_de_teller(monkeypatch):
    # 90-minutenvenster: +900 berichten in 60 min -> 15 msg/min
    monkeypatch.setattr(db, "tsdb", type("T", (), {
        "window_values": staticmethod(lambda *a, **k: [(0.0, 1000.0), (3600.0, 1900.0)])}))
    assert db.computed_rate(_Rep(slug="x", id=1), "nb_recv") == 15.0


def test_een_te_kort_venster_geeft_geen_cijfer(monkeypatch):
    monkeypatch.setattr(db, "tsdb", type("T", (), {
        "window_values": staticmethod(lambda *a, **k: [(0.0, 1000.0), (120.0, 1010.0)])}))
    assert db.computed_rate(_Rep(slug="x", id=1), "nb_recv") is None


def test_na_een_herstart_geen_cijfer(monkeypatch):
    monkeypatch.setattr(db, "tsdb", type("T", (), {
        "window_values": staticmethod(lambda *a, **k: [(0.0, 90000.0), (3600.0, 12.0)])}))
    assert db.computed_rate(_Rep(slug="x", id=1), "nb_recv") is None


def test_benutting_blijft_procent(monkeypatch):
    """Dezelfde helling, andere eenheid: dit mag de refactor niet verschoven
    hebben. +6 minuten airtime in 60 minuten is 10 %."""
    monkeypatch.setattr(db, "tsdb", type("T", (), {
        "window_values": staticmethod(lambda *a, **k: [(0.0, 100.0), (3600.0, 106.0)])}))
    assert db.computed_utilization(_Rep(slug="x", id=1), "airtime") == 10.0


def test_fossiel_alleen_als_de_node_daarna_nog_meldde():
    # gemeten weken voor de laatste melding -> de datum hoort erbij
    assert routes_public._fossiel("2026-08-13T19:05:17Z",
                                  "2026-09-08T09:38:33Z") == "2026-08-13T19:05:17Z"
    # gemeten in dezelfde ronde -> niets erbij
    assert routes_public._fossiel("2026-09-08T09:32:12Z", "2026-09-08T09:38:33Z") is None
    # een node die zelf al dagen stil is: alles is even oud, en dat zegt de kop
    # van de pagina al. Geen datum per tegel.
    assert routes_public._fossiel("2026-09-01T10:00:00Z", "2026-09-01T10:20:00Z") is None
    assert routes_public._fossiel(None, "2026-09-08T09:38:33Z") is None
    assert routes_public._fossiel("2026-09-08T09:38:33Z", None) is None
