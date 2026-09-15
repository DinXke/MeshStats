"""De accubewaking, en het geval waar ze voor gebouwd is.

Op 14 september 2026 zakte de zonnerepeater BE-HSS-DinX-Home van 3,55 V naar
2,81 V en viel stil. Hij nam de statistieken van drie andere nodes mee, want hij
was ook hun koerier. In ``alerts`` stond over die hele afloop geen enkele regel.
De cijfers waren er wel -- ze werden door niemand gelezen.

Wat hier vastligt:

1. Die afloop levert nu een alarm op, ruim vóór de node stil valt.
2. Zakken meldt EEN keer per trede. Blijft hij laag, dan komt er niets bij --
   anders is het alarm binnen een dag ruis.
3. Terug omhoog telt pas met een marge. Een zonnecel dobbert bij elke wolk rond
   de grens, en een alarm dat om het uur komt en gaat, leest niemand nog.
4. Een OUDE meting oordeelt niet. Een node die niet meer meldt heeft geen lage
   accu, hij heeft geen meting -- en een alarm dat elke ronde over hetzelfde
   getal van gisteren gaat, is het soort alarm dat mensen uitzetten.
5. Onder de twee volt meet een bord geen cel. Dat is geen lege accu maar geen
   accu.
"""
import pytest

from app import battwatch


@pytest.fixture
def db(tmp_path, monkeypatch):
    from app import config
    from app import db as db_module
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "test.sqlite3")
    db_module._conn = None
    yield db_module
    if db_module._conn is not None:
        db_module._conn.close()
        db_module._conn = None


def _node(db, naam="BE-HSS-DinX-Home"):
    return db.get_or_create_repeater("55d9a320a4e3", naam)["id"]


def _meet(db, rid, volt, minuten_geleden=0):
    """Eén accumeting neerzetten, zo oud als gevraagd."""
    from datetime import datetime, timedelta, timezone
    ts = ((datetime.now(timezone.utc) - timedelta(minutes=minuten_geleden))
          .strftime("%Y-%m-%dT%H:%M:%SZ"))
    db.ingest(rid, ts, {"bat": volt}, None, force=True)


def _teksten(db, rid):
    return [r["text"] for r in db.alerts_for(rid, 20)]


def test_de_afloop_van_14_september_geeft_nu_een_alarm(db):
    """De echte reeks, in het tempo waarin ze gebeurde."""
    rid = _node(db)
    _meet(db, rid, 3.545)
    assert battwatch.run_once() == 0          # nog niets aan de hand

    _meet(db, rid, 3.423)                     # 14/09 rond de middag
    assert battwatch.run_once() == 1
    assert "accu laag" in _teksten(db, rid)[0]

    _meet(db, rid, 3.109)                     # 14/09 21:00
    assert battwatch.run_once() == 1
    assert "KRITIEK" in _teksten(db, rid)[0]


def test_laag_blijven_is_geen_nieuwe_gebeurtenis(db):
    rid = _node(db)
    _meet(db, rid, 3.42)
    assert battwatch.run_once() == 1
    for volt in (3.41, 3.40, 3.38):
        _meet(db, rid, volt)
        assert battwatch.run_once() == 0
    assert len(_teksten(db, rid)) == 1


def test_herstel_telt_pas_met_de_marge(db):
    """Een cel die rond de grens dobbert mag geen alarmenmolen worden."""
    rid = _node(db)
    _meet(db, rid, 3.42)
    battwatch.run_once()
    _meet(db, rid, 3.52)                      # net boven de grens, binnen de marge
    assert battwatch.run_once() == 0
    _meet(db, rid, 3.62)                      # ruim erboven
    assert battwatch.run_once() == 1
    assert "weer op peil" in _teksten(db, rid)[0]


def test_een_oude_meting_oordeelt_niet(db):
    rid = _node(db)
    _meet(db, rid, 2.81, minuten_geleden=60 * (battwatch.BATT_STALE_H + 1))
    assert battwatch.run_once() == 0
    assert _teksten(db, rid) == []


def test_onder_twee_volt_is_geen_accu(db):
    """Een bord zonder cel of met een kapotte deler meldt bijna nul."""
    rid = _node(db)
    _meet(db, rid, 0.0)
    assert battwatch.run_once() == 0
    _meet(db, rid, 1.2)
    assert battwatch.run_once() == 0
    assert _teksten(db, rid) == []


def test_een_herstart_is_geen_gebeurtenis(db):
    """De trede staat in settings, niet in het geheugen van deze lus."""
    rid = _node(db)
    _meet(db, rid, 3.42)
    assert battwatch.run_once() == 1
    # Zoals na een herstart van de container: zelfde databank, verse module.
    _meet(db, rid, 3.41)
    assert battwatch.run_once() == 0


def test_omgedraaide_grenzen_worden_rechtgezet(db):
    """Kritiek boven laag is een tikfout, geen instelling."""
    db.set_setting("batt_warn_v", "3.2")
    db.set_setting("batt_crit_v", "3.6")
    warn, crit = battwatch.grenzen()
    assert warn == 3.6 and crit == 3.2


def test_de_grenzen_zijn_in_te_stellen(db):
    rid = _node(db)
    db.set_setting("batt_warn_v", "3.80")
    _meet(db, rid, 3.70)
    assert battwatch.run_once() == 1
    assert "3.80 V" in _teksten(db, rid)[0]
