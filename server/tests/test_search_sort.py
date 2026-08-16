"""Tests voor het sorteren van de zoekresultaten in het pakketarchief.

De sorteerparameter raakt twee lagen: search.parse_sort maakt er een ORDER BY
van (getest in test_search.py) en het zoek-endpoint moet die volgorde op de
rijen toepassen -- en op niets anders. Wat hier bewaakt wordt is dat laatste:
dat de rijen echt in de gevraagde volgorde staan, dat pagineren met een
sortering geen rijen dubbel of helemaal niet laat zien, en dat het totaal, het
histogram en de facetten er niet van veranderen.

Alles draait tegen een tijdelijke SQLite-file, zoals in test_db.py.
"""
import pytest


@pytest.fixture
def db(tmp_path, monkeypatch):
    """De db-module tegen een verse, tijdelijke database.

    Dezelfde opzet als in test_db.py: de module houdt een verbinding op
    moduleniveau vast, die per test weggegooid en na afloop gesloten moet worden.
    """
    from app import config
    from app import db as db_module
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "test.sqlite3")
    db_module._conn = None
    yield db_module
    if db_module._conn is not None:
        db_module._conn.close()
        db_module._conn = None


VENSTER = {"since": "2026-08-15T00:00:00Z", "until": "2026-08-15T23:59:59Z"}


def _pakket(db, minuut: int, hops, snr=None, payload="ADVERT"):
    """Eén pakket op 2026-08-15, op de opgegeven minuut na middernacht."""
    ts = "2026-08-15T%02d:%02d:00Z" % (minuut // 60, minuut % 60)
    db.execute(
        "INSERT INTO packets(ts, observer, route, payload_name, path_len, snr, "
        "sender, len) VALUES(?,?, 'FLOOD', ?, ?, ?, 'aabbcc', 24)",
        (ts, "aabbcc112233", payload, hops, snr))


def _zoek(**kwargs):
    """Het endpoint rechtstreeks aanroepen.

    Alle parameters expliciet: de standaardwaarden in de signature zijn
    FastAPI's Query-objecten, die alleen door de server zelf ingevuld worden.
    """
    from app import routes_api
    args = {"q": "", "limit": 100, "offset": 0, "facets": "", "sort": ""}
    args.update(VENSTER)
    args.update(kwargs)
    return routes_api.packet_search(**args)


def _hops(res):
    return [p["path_len"] for p in res["packets"]]


def test_zonder_sorteerparameter_staat_het_nieuwste_bovenaan(db):
    # De bestaande volgorde van het archief mag niet veranderd zijn door het
    # bestaan van de parameter.
    for minuut, hops in [(10, 1), (20, 9), (30, 5)]:
        _pakket(db, minuut, hops)
    res = _zoek()
    assert [p["ts"] for p in res["packets"]] == [
        "2026-08-15T00:30:00Z", "2026-08-15T00:20:00Z", "2026-08-15T00:10:00Z"]
    assert res["sort"] == "time:desc"


def test_sorteren_op_hops_in_beide_richtingen(db):
    for minuut, hops in [(10, 1), (20, 9), (30, 5), (40, 3)]:
        _pakket(db, minuut, hops)

    assert _hops(_zoek(sort="hops:desc")) == [9, 5, 3, 1]
    assert _hops(_zoek(sort="hops:asc")) == [1, 3, 5, 9]
    # Zonder richting: aflopend, zoals parse_sort belooft.
    assert _hops(_zoek(sort="hops")) == [9, 5, 3, 1]
    assert _zoek(sort="hops")["sort"] == "hops:desc"


def test_pakketten_zonder_hops_staan_altijd_onderaan(db):
    # Een pakket waarvan het hopveld leeg is, is geen pakket met nul hops. Het
    # hoort dus niet bovenaan te verschijnen zodra iemand oplopend sorteert.
    for minuut, hops in [(10, 4), (20, None), (30, 0)]:
        _pakket(db, minuut, hops)
    assert _hops(_zoek(sort="hops:asc")) == [0, 4, None]
    assert _hops(_zoek(sort="hops:desc")) == [4, 0, None]


def test_pagineren_op_een_gesorteerde_lijst_verliest_niets(db):
    # Twaalf pakketten met slechts drie verschillende hopwaarden: zonder een
    # unieke laatste sorteersleutel mag SQLite gelijke rijen per query anders
    # ordenen, en dan verschijnt er tussen twee pagina's een rij dubbel terwijl
    # een andere verdwijnt.
    for i in range(12):
        _pakket(db, i * 5, [2, 2, 7][i % 3])

    gezien = []
    for offset in (0, 4, 8):
        pagina = _zoek(sort="hops:desc", limit=4, offset=offset)
        assert len(pagina["packets"]) == 4
        gezien.extend(p["id"] for p in pagina["packets"])

    assert len(set(gezien)) == 12
    # En de volgorde over de pagina's heen is nog steeds aflopend.
    alles = _zoek(sort="hops:desc", limit=100)
    assert [p["id"] for p in alles["packets"]] == gezien


def test_dezelfde_vraag_geeft_twee_keer_dezelfde_volgorde(db):
    for i in range(20):
        _pakket(db, i * 3, 5)
    eerste = [p["id"] for p in _zoek(sort="hops:asc", limit=6)["packets"]]
    tweede = [p["id"] for p in _zoek(sort="hops:asc", limit=6)["packets"]]
    assert eerste == tweede


def test_totaal_histogram_en_facetten_veranderen_niet_mee(db):
    # De volgorde gaat over de pagina die je ziet, niet over de verzameling
    # waar hij uit komt. Een klik op een kolomkop mag de balken en de tellingen
    # dus niet laten bewegen.
    for minuut, hops, payload in [(10, 1, "ADVERT"), (70, 9, "ACK"),
                                  (130, 5, "ADVERT"), (190, 3, "ADVERT")]:
        _pakket(db, minuut, hops, payload=payload)

    standaard = _zoek(facets="type")
    gesorteerd = _zoek(sort="hops:asc", facets="type")

    assert gesorteerd["total"] == standaard["total"] == 4
    assert gesorteerd["histogram"] == standaard["histogram"]
    assert gesorteerd["facets"] == standaard["facets"]
    assert gesorteerd["bucket_s"] == standaard["bucket_s"]
    # Alleen de rijen staan anders.
    assert _hops(gesorteerd) != _hops(standaard)


def test_snr_sorteren_gebruikt_de_getalvolgorde(db):
    # Niet de tekstvolgorde: "-10" komt vóór "-9" als string, en erna als getal.
    for minuut, snr in [(10, -10.0), (20, -9.0), (30, 5.5)]:
        _pakket(db, minuut, 2, snr=snr)
    assert [p["snr"] for p in _zoek(sort="snr:asc")["packets"]] == [-10.0, -9.0, 5.5]


def test_onmogelijke_sortering_is_een_leesbare_fout_geen_rijen(db):
    _pakket(db, 10, 3)
    for onzin in ("verzonnen", "hops:omhoog", "p.ts", "hops); DROP TABLE packets--"):
        res = _zoek(sort=onzin)
        assert "error" in res, onzin
        assert "packets" not in res
        # De veldenlijst gaat mee, zodat de pagina kan vertellen wat wél kan.
        assert res["fields"]
    # En de tabel staat er nog.
    assert db.qone("SELECT COUNT(*) AS n FROM packets")["n"] == 1


def test_alle_aangeboden_sorteringen_werken_echt(db):
    # De pagina biedt precies aan wat search.SORTS zegt; elk van die sleutels
    # moet een query opleveren die SQLite uitvoert, niet alleen een die parst.
    from app import search
    _pakket(db, 10, 3, snr=4.0)
    _pakket(db, 20, 8, snr=-2.0)
    for naam in search.SORTS:
        for richting in ("asc", "desc"):
            res = _zoek(sort=f"{naam}:{richting}")
            assert len(res["packets"]) == 2, naam
            assert res["sort"] == f"{naam}:{richting}"
