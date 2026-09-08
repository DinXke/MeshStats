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


# --- op de pagina zelf -------------------------------------------------------
#
# De unittests hierboven zeggen dat de afleiding klopt. Ze zeiden niets over de
# vraag of de PAGINA hem gebruikt -- en juist daar zat de fout na de eerste
# poging: de rates staan niet in TILE_METRICS en komen dus langs de tweede
# tegellus, waar de afleiding niet stond. Het fossiel bleef gewoon staan. Deze
# test rendert de echte pagina met een echt fossiel in de databank.

import pytest

from app import config


@pytest.fixture
def echte_db(tmp_path, monkeypatch):
    from app import db as db_module
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "test.sqlite3")
    db_module._conn = None
    yield db_module
    if db_module._conn is not None:
        db_module._conn.close()
        db_module._conn = None


class _Request:
    cookies: dict = {}
    query_params: dict = {}


def _tegels(ctx):
    return {t["metric"]: t for b in ctx["blocks"] if b["type"] == "section"
            for t in b["section"]["tiles"]}


def test_de_pagina_toont_het_fossiel_niet_meer(echte_db, monkeypatch):
    db_ = echte_db
    from app import routes_public

    from datetime import datetime, timedelta, timezone

    def stempel(minuten_terug):
        return (datetime.now(timezone.utc)
                - timedelta(minutes=minuten_terug)).strftime("%Y-%m-%dT%H:%M:%SZ")

    rep = db_.get_or_create_repeater("aabbccddeeff", "Fossielnode")
    db_.execute("UPDATE repeaters SET is_public=1 WHERE id=?", (rep["id"],))
    # Eerst de oude wereld: een rate die door de verdwenen weg is meegestuurd.
    # Ver genoeg terug om buiten het rekenvenster van 90 minuten te vallen, want
    # anders zou de test niet kunnen onderscheiden of het cijfer afgeleid is.
    db_.ingest(rep["id"], "2026-08-13T19:05:17Z",
               {"online": True, "nb_recv": 500, "nb_recv_rate": 11.3}, None)
    # Dan de nieuwe: alleen de teller komt nog binnen.
    db_.ingest(rep["id"], stempel(60), {"online": True, "nb_recv": 1000}, None)
    db_.ingest(rep["id"], stempel(0), {"online": True, "nb_recv": 1600}, None)

    monkeypatch.setattr(routes_public.templates, "TemplateResponse",
                        lambda request, name, ctx: ctx)
    ctx = routes_public.repeater_page(_Request(), rep["slug"])
    tegel = _tegels(ctx)["nb_recv_rate"]

    # +600 berichten in 60 minuten = 10 msg/min. Het fossiel was 11,3.
    assert tegel["display"] == "10 msg/min", tegel
    # En het is geen fossiel meer, dus geen datum eronder.
    assert tegel["stale_since"] is None


def test_zonder_verse_tellers_een_streep_en_niet_het_oude_cijfer(echte_db, monkeypatch):
    """Een node die al weken stil is. Een rate is een uitspraak over NU, dus is
    "geen cijfer" hier het juiste antwoord -- en zeker niet het cijfer dat de
    verdwenen weg als laatste meestuurde."""
    db_ = echte_db
    from app import routes_public

    rep = db_.get_or_create_repeater("aabbccddeeff", "Stille node")
    db_.execute("UPDATE repeaters SET is_public=1 WHERE id=?", (rep["id"],))
    db_.ingest(rep["id"], "2026-08-13T19:05:17Z",
               {"online": True, "nb_recv": 500, "nb_recv_rate": 11.3}, None)

    monkeypatch.setattr(routes_public.templates, "TemplateResponse",
                        lambda request, name, ctx: ctx)
    tegel = _tegels(routes_public.repeater_page(_Request(), rep["slug"]))["nb_recv_rate"]
    assert tegel["value"] is None
    assert "11.3" not in tegel["display"] and "11,3" not in tegel["display"]
