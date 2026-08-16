"""Tests voor het opruimen: bewaartermijn, de twee bovengrenzen en hun botsing.

Wat hier bewaakt wordt is niet "er wordt iets verwijderd" maar de volgorde
waarin dat gebeurt, want die is de hele belofte. Eerst gaat weg wat te oud is,
daarna -- als het er dan nog te veel zijn -- gaat de OUDSTE weg tot het past.
Precies die tweede helft is wat je niet ziet als je alleen telt: een FIFO die
per ongeluk de nieuwste rijen wegsnoeit levert exact dezelfde rijtelling op en
een compleet nutteloze databank.

De rijen worden hier rechtstreeks met SQL geplaatst in plaats van via
``insert_packet``. Dat is geen kortere weg om de ingest te omzeilen maar de
enige manier om er duizenden te maken met tijdstempels die dagen uit elkaar
liggen: ``insert_packet`` stempelt op nu en filtert duplicaten binnen een
minuut weg, en beide staan een test over bewaartermijnen in de weg.
"""
from datetime import datetime, timedelta, timezone

import pytest

from app import config

TS = "%Y-%m-%dT%H:%M:%SZ"


@pytest.fixture
def db(tmp_path, monkeypatch):
    """De db-module tegen een verse, tijdelijke database.

    Zelfde opzet als test_db.py: de moduleverbinding leeft op moduleniveau en
    moet per test weggegooid en na afloop gesloten worden, anders lekken tests
    in elkaar en kan Windows de tijdelijke file niet opruimen.
    """
    from app import db as db_module
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "test.sqlite3")
    db_module._conn = None
    # De ondergrens van de FIFO is in productie 1 000 rijen -- ruim genoeg dat
    # een bovengrens de tabel nooit helemaal leegt. Hier staat hij laag, anders
    # zou elke test duizend rijen moeten schrijven om überhaupt iets te mogen
    # zien gebeuren.
    monkeypatch.setattr(db_module, "PACKET_FIFO_FLOOR", 2)
    yield db_module
    if db_module._conn is not None:
        db_module._conn.close()
        db_module._conn = None


def _place(db, n: int, *, oldest_days_ago: float = 1.0, step_h: float = 1.0,
           raw: str = "ab" * 64) -> list[int]:
    """``n`` pakketten, van oud naar nieuw. Geeft de id's terug in die volgorde.

    In één transactie, want de bytetest heeft er duizenden nodig om een bestand
    van meer dan een megabyte te maken en een commit per rij maakt daar seconden
    van.
    """
    start = datetime.now(timezone.utc) - timedelta(days=oldest_days_ago)
    ids = []
    with db._lock:
        conn = db.get_conn()
        for i in range(n):
            ts = (start + timedelta(hours=step_h * i)).strftime(TS)
            cur = conn.execute(
                "INSERT INTO packets(ts, observer, payload_name, raw) VALUES(?,?,?,?)",
                (ts, "aabbcc", "ADVERT", raw))
            ids.append(cur.lastrowid)
        conn.commit()
    return ids


def _ids(db) -> list[int]:
    return [r["id"] for r in db.q("SELECT id FROM packets ORDER BY id")]


# --- de bewaartermijn ---------------------------------------------------------

def test_snoeit_pakketten_ouder_dan_de_termijn(db):
    db.set_setting("packet_retention_days", "5")
    oud = _place(db, 3, oldest_days_ago=30, step_h=24)      # 30, 29, 28 dagen oud
    vers = _place(db, 3, oldest_days_ago=1, step_h=1)

    report = db.prune()

    assert report["packets_age"] == 3
    assert _ids(db) == vers
    assert all(i not in _ids(db) for i in oud)
    # Geen bovengrens geraakt: de termijn is gewoon gehaald.
    assert report["limit_hit"] == ""


def test_termijn_komt_uit_de_instellingentabel_en_niet_uit_de_omgeving(db, monkeypatch):
    """De hele reden dat dit in ``settings`` staat: wijzigen zonder herstart."""
    monkeypatch.setattr(config, "PACKET_RETENTION_DAYS", 7)
    assert db.retention_settings()["days"] == 7
    db.set_setting("packet_retention_days", "30")
    assert db.retention_settings()["days"] == 30

    _place(db, 2, oldest_days_ago=20, step_h=1)
    assert db.prune()["packets_age"] == 0       # binnen de 30, buiten de 7


# --- de FIFO op rijen ---------------------------------------------------------

def test_rijmaximum_gooit_de_oudste_eerst_weg(db):
    db.set_setting("packet_retention_days", "365")   # de termijn mag niets doen
    db.set_setting("packet_max_rows", "4")
    ids = _place(db, 10, oldest_days_ago=5, step_h=1)

    report = db.prune()

    assert report["packets_age"] == 0
    assert report["packets_rows"] == 6
    assert report["limit_hit"] == "rows"
    # Dit is de assertie die ertoe doet: de vier NIEUWSTE staan er nog.
    assert _ids(db) == ids[-4:]
    assert report["packets_left"] == 4


def test_rijmaximum_doet_niets_zolang_het_past(db):
    db.set_setting("packet_retention_days", "365")
    db.set_setting("packet_max_rows", "50")
    ids = _place(db, 10, oldest_days_ago=5, step_h=1)

    report = db.prune()

    assert report["packets_rows"] == 0
    assert report["limit_hit"] == ""
    assert _ids(db) == ids


def test_rijmaximum_snoeit_nooit_onder_de_ondergrens(db):
    """Een absurd lage grens leegt de tabel niet; hij stopt op de bodem."""
    db.set_setting("packet_retention_days", "365")
    db.set_setting("packet_max_rows", "1")           # onder PACKET_FIFO_FLOOR (2)
    ids = _place(db, 6, oldest_days_ago=5, step_h=1)

    db.prune()

    assert _ids(db) == ids[-2:]


# --- de FIFO op bestandsgrootte ----------------------------------------------

def test_bestandsgrootte_snoeit_de_oudste_weg(db):
    """De byte-bovengrens bijt ook wanneer de rijgrens ruim gehaald wordt.

    Vandaar de vierduizend rijen met een volle raw-frame erin: de laagst
    toegestane bovengrens is één megabyte, dus een test die er iets van wil zien
    gebeuren moet daar eerst overheen komen.
    """
    db.set_setting("packet_retention_days", "365")
    db.set_setting("packet_max_rows", "100000")
    ids = _place(db, 4000, oldest_days_ago=5, step_h=0.01, raw="cd" * 300)
    assert db.db_bytes() > 1024 * 1024
    db.set_setting("db_max_mb", "1")                 # ver onder wat er nu staat

    report = db.prune()

    assert report["packets_rows"] == 0               # de rijgrens deed niets
    assert report["packets_bytes"] > 0
    assert report["limit_hit"] == "bytes"
    resterend = _ids(db)
    # Weer de kern: wat er overblijft is de NIEUWE staart van de reeks.
    assert resterend == ids[len(ids) - len(resterend):]


def test_bestandsgrootte_laat_alles_staan_als_het_past(db):
    db.set_setting("packet_retention_days", "365")
    db.set_setting("db_max_mb", "1024")
    ids = _place(db, 20, oldest_days_ago=5, step_h=1)

    report = db.prune()

    assert report["packets_bytes"] == 0
    assert _ids(db) == ids


# --- de wisselwerking ---------------------------------------------------------

def test_termijn_en_rijmaximum_samen(db):
    """Eerst de termijn, dan de bovengrens -- en de pagina hoort te weten welke sneed.

    Twintig pakketten over veertig dagen. De termijn van tien dagen haalt er
    vijftien weg; van de vijf die overblijven mogen er nog maar twee staan.
    Wat rest zijn de twee jongste, en het rapport zegt dat de termijn niet de
    beperkende factor was.
    """
    db.set_setting("packet_retention_days", "10")
    db.set_setting("packet_max_rows", "2")
    ids = _place(db, 20, oldest_days_ago=40, step_h=48)   # om de twee dagen

    report = db.prune()

    assert report["packets_age"] == 15
    assert report["packets_rows"] == 3
    assert report["limit_hit"] == "rows"
    assert _ids(db) == ids[-2:]
    assert report["packets_left"] == 2
    # En het getal waar de beheerpagina op afgaat: er staat minder dan de
    # ingestelde termijn, dus de belofte is niet gehaald.
    assert report["effective_days"] is not None
    assert report["effective_days"] < report["days"]


def test_ruime_grenzen_laten_de_termijn_de_termijn(db):
    """De omgekeerde volgorde: haalt de termijn het wél, dan waarschuwt niets."""
    db.set_setting("packet_retention_days", "10")
    db.set_setting("packet_max_rows", "1000")
    db.set_setting("db_max_mb", "1024")
    _place(db, 12, oldest_days_ago=40, step_h=96)         # om de vier dagen

    report = db.prune()

    assert report["packets_age"] > 0
    assert report["packets_rows"] == 0
    assert report["packets_bytes"] == 0
    assert report["limit_hit"] == ""


def test_overzicht_meldt_dat_de_termijn_niet_gehaald_wordt(db):
    from app import retention

    db.set_setting("packet_retention_days", "30")
    db.set_setting("packet_max_rows", "3")
    _place(db, 20, oldest_days_ago=25, step_h=24)
    retention.run_once()

    overzicht = retention.overview()
    assert overzicht["limit_hit"] == "rows"
    assert overzicht["falls_short"] is True
    assert overzicht["packets"] == 3
    assert overzicht["effective_days"] < 30
    # Persistent: de laatste ronde wordt in ``settings`` bewaard, zodat de
    # beheerpagina er na een herstart nog steeds over kan vertellen.
    assert retention.last_report()["packets_rows"] == 17


def test_overzicht_zwijgt_als_er_niets_aan_de_hand_is(db):
    from app import retention

    db.set_setting("packet_retention_days", "30")
    db.set_setting("packet_max_rows", "1000")
    _place(db, 5, oldest_days_ago=2, step_h=1)
    retention.run_once()

    overzicht = retention.overview()
    assert overzicht["limit_hit"] == ""
    assert overzicht["falls_short"] is False
    assert overzicht["over_ceiling"] is False


# --- de heatmap volgt de instelling ------------------------------------------

def test_heatmapvenster_volgt_de_ingestelde_bewaartermijn(db, monkeypatch):
    """Het venster is de ingestelde termijn, niet de waarde uit de omgeving."""
    from app import routes_api

    monkeypatch.setattr(config, "PACKET_RETENTION_DAYS", 7)
    assert routes_api._heatmap_window_h() == 7 * 24
    db.set_setting("packet_retention_days", "30")
    assert routes_api._heatmap_window_h() == 30 * 24


def test_heatmap_cache_houdt_geen_oud_venster_vast(db, monkeypatch):
    """Wijzigt de termijn, dan mag het antwoord uit de cache niet blijven staan.

    Zonder het venster in de cachesleutel zou de kaart tot vijf minuten lang een
    ander getal melden dan er in de instellingen staat -- en dat getal staat in
    het antwoord, dus het is zichtbaar verkeerd en niet alleen intern.
    """
    from app import routes_api

    routes_api._heatmap_cache["at"] = 0.0
    routes_api._heatmap_cache["data"] = None
    db.set_setting("packet_retention_days", "3")
    assert routes_api.packet_heatmap()["window_h"] == 72

    db.set_setting("packet_retention_days", "10")
    # Binnen de TTL, dus de cache zou het oude antwoord teruggeven als het
    # venster geen deel van de sleutel was.
    assert routes_api.packet_heatmap()["window_h"] == 240


# --- VACUUM -------------------------------------------------------------------

def test_vacuum_slaat_over_als_er_weinig_vrije_ruimte_is(db):
    """De gewone toestand: een databank zonder gat erin wordt niet herschreven."""
    _place(db, 20, oldest_days_ago=2, step_h=1)

    uitslag = db.maybe_vacuum()

    assert uitslag["ran"] is False
    assert "niet nodig" in uitslag["reason"]


def test_vacuum_geeft_ruimte_terug_als_erom_gevraagd_wordt(db):
    _place(db, 800, oldest_days_ago=40, step_h=1, raw="ef" * 300)
    voor = db.db_bytes()
    db.set_setting("packet_retention_days", "1")
    db.prune()

    uitslag = db.maybe_vacuum(force=True)

    assert uitslag["ran"] is True
    assert uitslag["after"] <= voor
    assert uitslag["at"]


# --- latest en repeater_cli ---------------------------------------------------
#
# De twee tabellen die tot voor kort helemaal niet gesnoeid werden, en waar het
# byteplafond niets tegen kon beginnen: dat verwijdert uitsluitend uit
# ``packets``. Een opgeblazen ``latest`` liet het dus pakketten wegsnoeien tot de
# bodem terwijl het bestand precies even groot bleef.

def _meting(db, repeater_id: int, metriek: str, dagen_geleden: float) -> None:
    ts = (datetime.now(timezone.utc) - timedelta(days=dagen_geleden)).strftime(TS)
    db.execute("INSERT OR REPLACE INTO latest(repeater_id, metric, ts, value) "
               "VALUES(?,?,?,?)", (repeater_id, metriek, ts, 1.0))


def _wees(db, sql: str, params) -> None:
    """Schrijf een rij die naar een repeater wijst die niet bestaat.

    Vraagt om de sleutelcontrole even uit te zetten, en dat is meteen het
    antwoord op "waarom snoeit prune() wezen als CASCADE ze niet toelaat": zo
    ontstaan ze. Een databank die ooit door een herstelscript of een oudere
    versie met ``foreign_keys=OFF`` is aangeraakt, houdt ze -- en dan is deze
    regel het enige dat ze opruimt.
    """
    with db._lock:
        conn = db.get_conn()
        conn.execute("PRAGMA foreign_keys=OFF")
        conn.execute(sql, params)
        conn.commit()
        conn.execute("PRAGMA foreign_keys=ON")


def test_verweesde_latest_rijen_verdwijnen(db):
    rep = db.get_or_create_repeater("aabbcc112233", "Node")
    _meting(db, rep["id"], "online", 0)
    _wees(db, "INSERT INTO latest(repeater_id, metric, ts, value) VALUES(?,?,?,?)",
          (9999, "online", db.utcnow(), 1.0))

    db.prune()

    over = {r["repeater_id"] for r in db.q("SELECT repeater_id FROM latest")}
    assert over == {rep["id"]}


def test_uitgestorven_metrieken_van_een_levende_repeater_gaan_weg(db):
    """Een naam die één keer langskwam en nooit meer, bij een node die nog leeft."""
    db.set_setting("retention_days", "180")
    rep = db.get_or_create_repeater("aabbcc112233", "Node")
    _meting(db, rep["id"], "online", 0)             # vers
    _meting(db, rep["id"], "verzonnen_ooit", 200)   # buiten de termijn

    db.prune()

    assert set(db.latest_for(rep["id"])) == {"online"}


def test_een_dode_repeater_houdt_zijn_laatst_bekende_waarden(db):
    """Het voorwaardje dat de vorige regel zo omzichtig maakt: dood laten liggen.

    Bij een repeater die al een half jaar stil ligt zijn ál zijn waarden oud.
    Die dan wissen maakt zijn kaart leeg, terwijl "dit was het laatste wat we van
    hem hoorden" precies is wat iemand komt zoeken.
    """
    db.set_setting("retention_days", "180")
    dood = db.get_or_create_repeater("aabbcc112233", "Al lang stil")
    levend = db.get_or_create_repeater("ddeeff445566", "Nog bezig")
    _meting(db, dood["id"], "online", 300)
    _meting(db, dood["id"], "battery_percentage", 300)
    _meting(db, levend["id"], "online", 0)

    db.prune()

    assert set(db.latest_for(dood["id"])) == {"online", "battery_percentage"}


def test_het_plafond_per_repeater_houdt_de_nieuwste(db, monkeypatch):
    """Wat er bij misbruik werkelijk toe doet: binnen de bewaartermijn staat de
    uitgestorven-regel machteloos, het plafond niet."""
    monkeypatch.setattr(db, "MAX_LATEST_PER_REPEATER", 10)
    rep = db.get_or_create_repeater("aabbcc112233", "Node")
    for i in range(40):
        # Allemaal binnen de termijn, oplopend in tijd: rij 39 is de nieuwste.
        _meting(db, rep["id"], f"verzonnen_{i:02d}", 40 - i)

    n = db.prune()["latest"]

    over = set(db.latest_for(rep["id"]))
    assert n == 30
    assert over == {f"verzonnen_{i:02d}" for i in range(30, 40)}


def test_het_plafond_telt_per_repeater_en_niet_over_de_hele_tabel(db, monkeypatch):
    monkeypatch.setattr(db, "MAX_LATEST_PER_REPEATER", 5)
    een = db.get_or_create_repeater("aabbcc112233", "Een")
    twee = db.get_or_create_repeater("ddeeff445566", "Twee")
    for i in range(5):
        _meting(db, een["id"], f"m{i}", 1)
        _meting(db, twee["id"], f"m{i}", 1)

    db.prune()

    assert len(db.latest_for(een["id"])) == 5
    assert len(db.latest_for(twee["id"])) == 5


def test_repeater_cli_krijgt_dezelfde_drie_regels(db, monkeypatch):
    monkeypatch.setattr(db, "MAX_CLI_PER_REPEATER", 4)
    db.set_setting("retention_days", "180")
    rep = db.get_or_create_repeater("aabbcc112233", "Node")
    oud = (datetime.now(timezone.utc) - timedelta(days=300)).strftime(TS)
    nu = db.utcnow()
    _wees(db, "INSERT INTO repeater_cli(repeater_id, param, value, updated) "
              "VALUES(?,?,?,?)", (9999, "wees", "x", nu))
    db.execute("INSERT INTO repeater_cli(repeater_id, param, value, updated) "
               "VALUES(?,?,?,?)", (rep["id"], "vergeten", "x", oud))
    for i in range(6):
        db.execute("INSERT INTO repeater_cli(repeater_id, param, value, updated) "
                   "VALUES(?,?,?,?)", (rep["id"], f"p{i}", "x", nu))

    db.prune()

    params = {r["param"] for r in db.cli_settings_for(rep["id"])}
    assert "vergeten" not in params            # buiten de termijn
    assert len(params) == 4                    # plafond
    assert db.qone("SELECT COUNT(*) AS n FROM repeater_cli "
                   "WHERE repeater_id=9999")["n"] == 0


def test_het_rapport_noemt_wat_er_uit_latest_en_cli_ging(db, monkeypatch):
    """Anders is het weer stil snoeien, en daar begon dit hele bestand mee."""
    monkeypatch.setattr(db, "MAX_LATEST_PER_REPEATER", 2)
    rep = db.get_or_create_repeater("aabbcc112233", "Node")
    for i in range(6):
        _meting(db, rep["id"], f"m{i}", 6 - i)

    rapport = db.prune()

    assert rapport["latest"] == 4
    assert rapport["cli"] == 0
