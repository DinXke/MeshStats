"""De batterijcurve, en de meting die ze had moeten geven.

Op 23 oktober 2026 liep de dakrepeater leeg en beschadigde bij het uitvallen
zijn bestandssysteem. Vlak voor het einde stond er **4 %** op de site. Dat getal
kwam uit een rechte lijn van 3,00 V (0 %) naar 4,20 V (100 %), en bij 4 % stond
die cel dus op 3,05 V -- leeg. De lijn loog ook de andere kant op: 3,70 V heette
58 %, terwijl daar nog ongeveer een tiende van de lading in zit.

Wat hier vastligt:

1. De curve is MONOTOON. Meer spanning is nooit minder procent; een grafiek die
   terugspringt terwijl de cel laadt, is erger dan geen grafiek.
2. De RANDEN kloppen: op en onder leeg is het 0, op en boven vol is het 100, en
   er komt nooit iets buiten 0..100 uit.
3. De NONE-gevallen blijven werken: geen meting, geen getal, geen cel (< 2 V) en
   geen geldig getal (NaN) geven None en geen 0 -- "leeg" en "ik weet het niet"
   zijn twee verschillende antwoorden.
4. De IJKPUNTEN: 3,00 V is 0 % (en niet 4 % zoals op de dag zelf), en rond
   3,70 V zakt de curve veel sneller dan de oude rechte lijn.
5. EEN waarheid: een node die zijn eigen (nog lineaire) percentage meestuurt
   overstemt de curve niet meer.
"""
import math

import pytest

from app import metrics


OUDE_LIJN = lambda v: int((v * 1000 - 3000) * 100 // 1200)   # noqa: E731 - de lijn van vóór 2.23.0


# --- de vorm van de curve ---------------------------------------------------

def test_de_tabel_zelf_loopt_op_in_allebei_de_kolommen():
    mvs = [mv for mv, _ in metrics.BATT_CURVE]
    pcts = [p for _, p in metrics.BATT_CURVE]
    assert mvs == sorted(mvs) and len(set(mvs)) == len(mvs)
    assert pcts == sorted(pcts)
    assert (mvs[0], pcts[0]) == (metrics.BATT_EMPTY_MV, 0)
    assert (mvs[-1], pcts[-1]) == (metrics.BATT_FULL_MV, 100)


def test_monotoon_over_het_hele_bereik():
    """Per millivolt van 2,0 tot 4,5 V: nooit omlaag, nooit buiten 0..100."""
    vorig = -1
    for mv in range(2000, 4501):
        pct = metrics.battery_percentage(mv / 1000.0)
        assert pct is not None
        assert 0 <= pct <= 100
        assert pct >= vorig, "bij %d mV zakt het percentage" % mv
        vorig = pct


def test_de_randen():
    assert metrics.battery_percentage(3.000) == 0
    assert metrics.battery_percentage(2.500) == 0        # boven de 2 V, onder leeg
    assert metrics.battery_percentage(2.000) == 0        # precies de ondergrens telt nog
    assert metrics.battery_percentage(4.200) == 100
    assert metrics.battery_percentage(4.350) == 100      # een HV-cel wordt niet 108 %


def test_op_elk_knikpunt_staat_precies_de_tabelwaarde():
    for mv, pct in metrics.BATT_CURVE:
        assert metrics.battery_percentage(mv / 1000.0) == pct


# --- wat géén meting is -----------------------------------------------------

@pytest.mark.parametrize("waarde", [None, "", "leeg", [], {}, float("nan"),
                                    float("inf"), float("-inf")])
def test_geen_bruikbare_meting_geeft_none(waarde):
    assert metrics.battery_percentage(waarde) is None


def test_onder_twee_volt_is_geen_lege_cel_maar_geen_cel():
    assert metrics.battery_percentage(1.999) is None
    assert metrics.battery_percentage(0.0) is None
    assert metrics.battery_percentage(-1.0) is None
    # ... en 2,0 V is dat wél, zie test_de_randen.


def test_een_string_met_een_getal_telt_gewoon_mee():
    """De poller levert soms tekst; dat was zo en blijft zo."""
    assert metrics.battery_percentage("3.900") == metrics.battery_percentage(3.9)


# --- de ijkpunten waar het om begonnen is -----------------------------------

def test_drie_volt_is_nul_procent_en_niet_vier():
    """Het getal van 23 oktober. 3,05 V heette 4 %; de cel was leeg."""
    assert metrics.battery_percentage(3.00) == 0
    assert metrics.battery_percentage(3.05) == 0
    assert OUDE_LIJN(3.05) == 4                    # dit stond er die dag
    # Pas boven de knie van de onderste staart komt er überhaupt een procent bij.
    assert metrics.battery_percentage(3.30) == 1
    assert metrics.battery_percentage(3.29) == 0


@pytest.mark.parametrize("volt, hoogstens", [(3.30, 5), (3.40, 5), (3.50, 10)])
def test_de_onderste_staart_is_bijna_leeg(volt, hoogstens):
    """Waar de oude lijn nog tientallen procenten beloofde, staat nu bijna niets."""
    nieuw = metrics.battery_percentage(volt)
    assert nieuw <= hoogstens
    assert nieuw < OUDE_LIJN(volt) - 15            # en ruim onder de rechte lijn


def test_rond_drie_komma_zeven_zakt_het_veel_sneller_dan_vroeger():
    """3,7 V is 'nominaal' en voelt als het midden. Het is het einde van de rit."""
    assert metrics.battery_percentage(3.70) == 13
    assert OUDE_LIJN(3.70) == 58                   # wat de oude lijn ervan maakte

    # De steilheid zelf: over dezelfde 100 mV rond 3,70 V verliest de curve
    # veel meer dan de rechte lijn ooit kon (die deed overal ~8 % per 100 mV).
    oud = OUDE_LIJN(3.75) - OUDE_LIJN(3.65)
    nieuw = (metrics.battery_percentage(3.75) - metrics.battery_percentage(3.65))
    assert oud == 8
    assert nieuw >= 2 * oud


def test_het_plateau_draagt_de_meeste_lading():
    """Twee vensters van 200 mV: op het plateau gaat er een veelvoud doorheen.

    Precies dát is wat een rechte lijn niet kan: die geeft beide vensters 16 %.
    """
    plateau = metrics.battery_percentage(3.95) - metrics.battery_percentage(3.75)
    staart = metrics.battery_percentage(3.55) - metrics.battery_percentage(3.35)
    assert plateau > 5 * staart
    oud_plateau = OUDE_LIJN(3.95) - OUDE_LIJN(3.75)
    oud_staart = OUDE_LIJN(3.55) - OUDE_LIJN(3.35)
    assert abs(oud_plateau - oud_staart) <= 1          # op een afkapfout na gelijk


def test_halverwege_de_spanning_is_niet_halverwege_de_lading():
    """3,60 V ligt in het midden van 3,0-4,2 V, maar niet in het midden van 0-100 %."""
    assert metrics.battery_percentage(3.60) < 10
    assert OUDE_LIJN(3.60) == 50


# --- één waarheid: wat ingest ermee doet ------------------------------------

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


def _laatste(db, rid, naam):
    rij = db.qone("SELECT value FROM latest WHERE repeater_id=? AND metric=?", (rid, naam))
    return None if rij is None else rij["value"]


def test_alleen_spanning_geeft_een_percentage_uit_de_curve(db):
    rid = _node(db)
    db.ingest(rid, db.utcnow(), {"bat": 3.70}, None, force=True)
    assert _laatste(db, rid, "battery_percentage") == 13


def test_het_percentage_van_de_node_overstemt_de_curve_niet_meer(db):
    """De firmware rekent nog met de rechte lijn. Twee paden, één getal."""
    rid = _node(db)
    db.ingest(rid, db.utcnow(), {"bat": 3.05, "battery_percentage": 4}, None, force=True)
    assert _laatste(db, rid, "battery_percentage") == 0


def test_een_percentage_zonder_spanning_blijft_onaangeroerd(db):
    """Een echte brandstofmeter meet wat wij alleen kunnen schatten."""
    rid = _node(db)
    db.ingest(rid, db.utcnow(), {"battery_percentage": 92}, None, force=True)
    assert _laatste(db, rid, "battery_percentage") == 92


def test_zonder_bruikbare_cel_komt_er_geen_percentage_in_de_databank(db):
    rid = _node(db)
    db.ingest(rid, db.utcnow(), {"bat": 1.2, "battery_percentage": 0}, None, force=True)
    assert _laatste(db, rid, "battery_percentage") is None
    assert _laatste(db, rid, "bat") == pytest.approx(1.2)


# --- de drempels van de accubewaking ----------------------------------------

def test_de_alarmdrempels_liggen_nu_in_de_staart_en_niet_in_het_plateau():
    """Vastgelegd zodat een verschuiving van de curve hier opvalt.

    De drempels van battwatch staan in VOLT en bewegen dus niet mee met deze
    curve -- maar wat ze betekenen verandert wel. Zie CHANGELOG 2.23.0.
    """
    from app import battwatch
    warn = metrics.battery_percentage(battwatch.BATT_WARN_V_DEFAULT)
    crit = metrics.battery_percentage(battwatch.BATT_CRIT_V_DEFAULT)
    assert (warn, crit) == (3, 1)
    assert (OUDE_LIJN(battwatch.BATT_WARN_V_DEFAULT),
            OUDE_LIJN(battwatch.BATT_CRIT_V_DEFAULT)) == (41, 25)


def test_dezelfde_ondergrens_als_de_accubewaking():
    from app import battwatch
    assert metrics.BATT_MIN_USABLE_MV == int(battwatch.BATT_MIN_USABLE_V * 1000)
    assert math.isclose(metrics.BATT_MIN_USABLE_MV / 1000.0, battwatch.BATT_MIN_USABLE_V)
