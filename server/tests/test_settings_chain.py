"""Tests voor de instellingenketen: knop -> wachtrij -> poller -> opslag.

De keten is fragiel op één plek die geen enkele foutmelding oplevert: de
wachtrij op de site is clear-on-read, dus zodra de poller een verzoek heeft
opgehaald bestaat het nergens meer. Gaat er daarna iets mis met het herkennen
van de sleutel, dan is het verzoek weg en blijft de beheerpagina hangen op de
laatste geslaagde uitlezing. Deze tests leggen precies dat vast.
"""
import pytest


@pytest.fixture
def db(tmp_path, monkeypatch):
    """De db-module tegen een verse, tijdelijke database."""
    from app import config
    from app import db as db_module
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "test.sqlite3")
    db_module._conn = None
    yield db_module
    if db_module._conn is not None:
        db_module._conn.close()
        db_module._conn = None


def test_wachtrij_overleeft_een_herstart(db):
    # De wachtrij staat in de settings-tabel, niet in het geheugen van het
    # proces: een container die tussen de klik en de eerstvolgende poll
    # herbouwd wordt, mag het verzoek niet meenemen in zijn val.
    db.request_settings("e3d3f4d7edd0", ["name", "role"])
    db._conn.close()
    db._conn = None  # zoals een verse start de database opnieuw opent

    assert db.pop_settings_requests() == [
        {"prefix": "e3d3f4d7edd0", "params": ["name", "role"]}
    ]


def test_wachtrij_is_leeg_na_uitreiking(db):
    db.request_settings("e3d3f4d7edd0", ["name"])
    assert db.pop_settings_requests()
    assert db.pop_settings_requests() == []


def test_openstaand_verzoek_is_zichtbaar_tot_het_opgehaald_is(db):
    # Waar de beheerpagina op steunt om "nog niemand heeft gepold" te kunnen
    # onderscheiden van "opgehaald en daarna stilte".
    assert db.pending_settings_request("e3d3f4d7edd0") is None
    db.request_settings("e3d3f4d7edd0", ["name"])
    assert db.pending_settings_request("e3d3f4d7edd0")
    assert db.pending_settings_request("55d9a320a4e3") is None
    db.pop_settings_requests()
    assert db.pending_settings_request("e3d3f4d7edd0") is None


def test_antwoord_met_kortere_sleutel_komt_bij_dezelfde_repeater_terecht(db):
    # Home Assistant stuurt vijf sleutelbytes, de eigen firmware van een node
    # zes, en de opgeslagen sleutel groeit mee met de langste die langskwam.
    # Wie hier op string-gelijkheid test, gooit een opvraging weg die één tot
    # twee minuten LoRa-zendtijd heeft gekost.
    rep = db.get_or_create_repeater("e3d3f4d7edd0", "BE-HSS-JessaZH.VIR")

    gevonden = db.find_repeater("e3d3f4d7ed")

    assert gevonden is not None
    assert gevonden["id"] == rep["id"]
    db.upsert_cli_settings(gevonden["id"], {"role": "repeater", "tx": "18"})
    waarden = {r["param"]: r["value"] for r in db.cli_settings_for(rep["id"])}
    assert waarden["role"] == "repeater"
    assert waarden["tx"] == "18"


def test_te_korte_sleutel_matcht_niet(db):
    # Zes hextekens kunnen bij toeval samenvallen; instellingen bij de
    # verkeerde node schrijven is erger dan ze laten liggen.
    db.get_or_create_repeater("e3d3f4d7edd0", "BE-HSS-JessaZH.VIR")

    assert db.find_repeater("e3d3f4") is None


def test_onbeantwoorde_parameter_wordt_bewaard_als_leeg(db):
    # "(geen antwoord)" op de beheerpagina is een opgeslagen NULL met een verse
    # tijdstempel, geen ontbrekende rij: de opvraging liep wel, de repeater
    # zweeg. Het onderscheid met "nooit opgevraagd" moet zichtbaar blijven.
    rep = db.get_or_create_repeater("e3d3f4d7edd0", "BE-HSS-JessaZH.VIR")

    db.upsert_cli_settings(rep["id"], {"name": None, "role": "repeater"})

    rijen = {r["param"]: r for r in db.cli_settings_for(rep["id"])}
    assert rijen["name"]["value"] is None
    assert rijen["name"]["updated"]
    assert "radio" not in rijen
