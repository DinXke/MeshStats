"""De brug van een `cmd:neighbors`-antwoord naar de burenlijst.

Stock firmware heeft `neighbors` in de gewone CLI; het antwoord is tekst met per
buur ``<4 byte sleutel>:<seconden geleden>:<snr x 4>``. Deze brug moet dat op
exact dezelfde plek laten landen als de MQTT-weg van de dakrepeater deed: de
``neighbors``-tabel plus de meting ``neighbor_count``.

Wat hier vastligt:

1. Zes hextekens, niet acht. Overal in deze databank is een buurprefix er zes;
   acht zou elke buur een tweede rij geven naast zijn eigen historie en hem
   tegelijk naamloos maken (de naamkoppeling zoekt op ``prefix6``).
2. SNR komt als SNR x 4 over de lijn en wordt gedeeld.
3. "geen buren" (``-none-``) is een UITKOMST en wordt opgeslagen; "ik kon het
   niet lezen" (een node die het commando niet kent) is er geen en verandert
   niets. Die twee verwarren zou een node met buren als buurloos laten zien.
"""
import pytest

from app import nbstock


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


# Zoals URS het teruggaf (RepeaterCli maakt van de regeleindes spaties).
ANTWOORD = ("2AE7AFA9:182:-11 208C4190:188:-46 624F70C1:195:41 "
            "060679AD:197:10 0A87727B:198:-47")


def _rep(db):
    return db.get_or_create_repeater("cb80b5fec849", "BE-HSS-JessaZH.URS")["id"]


def test_prefix_wordt_zes_hextekens():
    buren = nbstock.parse_neighbors(ANTWOORD)
    assert [b["prefix"] for b in buren][:2] == ["2ae7af", "208c41"]


def test_snr_wordt_gedeeld_door_vier():
    buren = nbstock.parse_neighbors(ANTWOORD)
    assert buren[0]["snr"] == pytest.approx(-2.75)
    assert buren[2]["snr"] == pytest.approx(10.25)


def test_seconden_worden_minuten():
    buren = nbstock.parse_neighbors("2AE7AFA9:182:-11 208C4190:3600:0")
    assert [b["seen_min"] for b in buren] == [3, 60]


def test_geen_buren_is_een_uitkomst():
    assert nbstock.parse_neighbors("-none-") == []


def test_onleesbaar_antwoord_is_geen_lege_lijst():
    """Een node die het commando niet kent mag geen buurloze node worden."""
    assert nbstock.parse_neighbors("Unknown command") is None
    assert nbstock.parse_neighbors("") is None
    assert nbstock.parse_neighbors(None) is None


def test_afgekapt_antwoord_levert_wat_er_wel_staat():
    """Upstream kapt af op ~134 tekens; de laatste buur kan half zijn. Wat
    leesbaar is telt, de rest valt weg -- een half gelezen sleutel opslaan zou
    een buur verzinnen die niet bestaat."""
    buren = nbstock.parse_neighbors("2AE7AFA9:182:-11 208C41")
    assert len(buren) == 1


def test_brug_vult_tabel_en_meting(db):
    rid = _rep(db)
    n = nbstock.apply_cli_neighbors(rid, {"cmd:neighbors": ANTWOORD}, source="cli")
    assert n == 5
    rijen = db.node_neighbors(rid, 20)
    assert len(rijen) == 5
    assert db.latest_for(rid)["neighbor_count"]["value"] == 5.0


def test_ronde_zonder_burenantwoord_doet_niets(db):
    rid = _rep(db)
    assert nbstock.apply_cli_neighbors(rid, {"name": "x", "cmd:clock": "13:37"}) is None
    assert db.node_neighbors(rid, 20) == []
    assert "neighbor_count" not in db.latest_for(rid)
