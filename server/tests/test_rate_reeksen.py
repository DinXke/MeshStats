"""De rate-grafieken zijn AFGELEID uit hun teller, niet opgeslagen.

Waarom dit bestand bestaat: de tegel "Ontvangstrate 11,3 msg/min" stond op de
nodepagina met een lege grafiek eronder. De tegel rekent live uit de teller; de
grafiek vroeg een reeks ``nb_recv_rate`` op die alleen de oude Home
Assistant-weg ooit wegschreef -- en die is uit de keten. Negen metrics, negen
lege grafieken, over de hele historie.

Dezelfde klacht en dezelfde remedie als bij de benutting, dus ook dezelfde
eisen: de helling per interval, een tellerreset overslaan, en niets verzinnen
waar de teller zwijgt. De laatste test is de vangnettest: een nieuwe ``*_rate``
in de catalogus zonder bron valt hier door, en niet pas op de site.
"""
from app import db, metrics


class _Rep(dict):
    pass


def _met_reeks(monkeypatch, raw):
    monkeypatch.setattr(db, "tsdb",
                        type("T", (), {"history": staticmethod(lambda *a, **k: raw)}))
    return _Rep(slug="x", id=1)


def test_ontvangstrate_afgeleid_uit_de_teller(monkeypatch):
    # oplopende berichtenteller, 10 minuten tussen de punten
    raw = [
        ("2026-09-08T10:00:00Z", 1000.0),
        ("2026-09-08T10:10:00Z", 1100.0),   # +100 in 10 min -> 10,0 msg/min
        ("2026-09-08T10:20:00Z", 1105.0),   # +5   in 10 min ->  0,5 msg/min
    ]
    rep = _met_reeks(monkeypatch, raw)
    assert db.metric_history(rep, "nb_recv_rate", 24) == [
        ("2026-09-08T10:10:00Z", 10.0), ("2026-09-08T10:20:00Z", 0.5)]


def test_een_herstart_geeft_geen_uitschieter(monkeypatch):
    """Na een herstart begint de teller bij nul. Dat is geen negatieve rate en
    ook geen piek van duizenden per minuut: dat interval hoort er niet te zijn."""
    raw = [
        ("2026-09-08T10:00:00Z", 90000.0),
        ("2026-09-08T10:10:00Z", 12.0),     # herstart -> overslaan
        ("2026-09-08T10:20:00Z", 32.0),     # +20 in 10 min -> 2,0 msg/min
    ]
    rep = _met_reeks(monkeypatch, raw)
    assert db.metric_history(rep, "nb_sent_rate", 24) == [("2026-09-08T10:20:00Z", 2.0)]


def test_zonder_teller_geen_verzonnen_rate(monkeypatch):
    rep = _met_reeks(monkeypatch, [])
    assert db.metric_history(rep, "sent_flood_rate", 24) == []


def test_elke_rate_in_de_catalogus_heeft_een_bron():
    """De vangnettest. Een metric die op de site aangeboden wordt maar door
    niemand geschreven of afgeleid wordt, is een lege grafiek met een label."""
    # Op de EENHEID en niet op de naam: ``filter_drop_rate`` eindigt ook op
    # _rate maar is een teller ("hoeveel gooide de snelheidslimiet weg"), en die
    # wordt wel gewoon geschreven. Wat hier bedoeld wordt is een metric die per
    # minuut uitgedrukt staat, en dat is precies wat de eenheid zegt.
    zonder = [m for m, (_s, _l, eenheid, _o) in metrics.CATALOG.items()
              if eenheid == "msg/min" and m not in db._RATE_BASIS]
    assert zonder == [], "geen bron voor: %s" % ", ".join(zonder)


def test_elke_bron_bestaat_ook_als_metric():
    ontbreekt = [b for b in db._RATE_BASIS.values() if b not in metrics.CATALOG]
    assert ontbreekt == [], "bron staat niet in de catalogus: %s" % ", ".join(ontbreekt)
