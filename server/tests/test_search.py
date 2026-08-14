"""Tests voor de zoektaal in app/search.py.

De parser belooft twee dingen: hij vertaalt de Kibana-achtige syntaxis naar een
WHERE-fragment met parameters, en hij laat nooit stilletjes iets vallen -- wat
niet te begrijpen is, is een QueryError met een leesbare boodschap.
"""
import sqlite3

import pytest

from app import search


def test_lege_query_matcht_alles():
    q = search.parse("")
    assert q.sql == ""
    assert q.params == []
    assert search.parse(None).sql == ""


def test_veld_gelijk_aan_waarde():
    q = search.parse("type:ADVERT")
    assert q.sql == "p.payload_name = ? COLLATE NOCASE"
    assert q.params == ["ADVERT"]


def test_clausules_worden_met_and_verbonden():
    q = search.parse("type:ADVERT scope:scoped")
    assert q.sql == ("p.payload_name = ? COLLATE NOCASE"
                     " AND p.scope = ? COLLATE NOCASE")
    assert q.params == ["ADVERT", "scoped"]


def test_joker_achteraan_wordt_prefixzoekopdracht():
    q = search.parse("sender:2ae7*")
    assert q.sql == "p.sender LIKE ? ESCAPE '\\'"
    assert q.params == ["2ae7%"]


def test_uitsluiting_met_min_en_not():
    # Beide spellingen moeten dezelfde ontkenning opleveren.
    for tekst in ("-type:ACK", "NOT type:ACK", "not type:ACK"):
        q = search.parse(tekst)
        assert q.sql == "NOT (p.payload_name = ? COLLATE NOCASE)", tekst
        assert q.params == ["ACK"]


def test_or_lijst_binnen_een_veld():
    q = search.parse("type:(ADVERT OR TXT_MSG)")
    assert q.sql == ("(p.payload_name = ? COLLATE NOCASE"
                     " OR p.payload_name = ? COLLATE NOCASE)")
    assert q.params == ["ADVERT", "TXT_MSG"]
    # Kleine letters en een kale spatie als scheiding tellen ook.
    assert search.parse("type:(ADVERT or ACK)").params == ["ADVERT", "ACK"]
    assert search.parse("type:(ADVERT ACK)").params == ["ADVERT", "ACK"]


def test_numerieke_vergelijkingen():
    q = search.parse("snr:>5")
    assert q.sql == "p.snr > ?"
    assert q.params == [5.0]

    q = search.parse("rssi:<=-100")
    assert q.sql == "p.rssi <= ?"
    assert q.params == [-100.0]

    q = search.parse("hops:0")
    assert q.sql == "p.path_len = ?"
    assert q.params == [0.0]


def test_numeriek_bereik():
    q = search.parse("len:20..40")
    assert q.sql == "(p.len >= ? AND p.len <= ?)"
    assert q.params == [20.0, 40.0]
    # Een negatieve ondergrens mag niet als ontkenning gelezen worden.
    assert search.parse("snr:-5..5").params == [-5.0, 5.0]


def test_aanhalingstekens_voor_waarden_met_spaties():
    q = search.parse('name:"Node Een"')
    assert q.params == ["%Node Een%"]
    assert "LIKE" in q.sql


def test_name_en_path_matchen_op_bevatten():
    # Beide kolommen zijn hooibergen (meerdere namen in een expressie, een
    # kommagescheiden hoplijst), dus een exacte match zou nooit raken.
    assert search.parse("name:BE-HSS").params == ["%BE-HSS%"]
    assert search.parse("path:2ae7").params == ["%2ae7%"]


def test_vrije_tekst_zoekt_over_de_tekstvelden():
    q = search.parse("2ae7")
    assert q.sql.count(" OR ") == len(search.FREE_TEXT_FIELDS) - 1
    assert q.params == ["%2ae7%"] * len(search.FREE_TEXT_FIELDS)
    # Een joker achteraan voegt bij bevatten-zoeken niets toe en verdwijnt.
    assert search.parse("2ae7*").params == search.parse("2ae7").params


def test_region_gebruikt_de_afleiding_uit_scope_codes():
    # scope_region staat niet als kolom in de tabel; het veld moet dezelfde
    # SQL-afleiding gebruiken als de API, anders spreken de twee elkaar tegen.
    q = search.parse("region:7")
    assert search.REGION_SQL in q.sql
    assert q.params == [7.0]


def test_ontkenning_met_joker_gecombineerd():
    q = search.parse("-sender:2ae7*")
    assert q.sql == "NOT (p.sender LIKE ? ESCAPE '\\')"
    assert q.params == ["2ae7%"]


def test_underscore_in_naam_is_letterlijk():
    # LIKE ziet '_' als eentekensjoker. Nodenamen zitten er vol mee, dus
    # zonder escaping zou 'name:node_a' ook 'nodeXa' vinden -- een zoekfunctie
    # die er werkend uitziet maar netjes verkeerde rijen teruggeeft. Getest
    # tegen echte SQLite, want dit is een afspraak met de database, niet met
    # een string.
    q = search.parse("name:node_a")
    patroon = q.params[0]
    assert patroon == "%node\\_a%"

    con = sqlite3.connect(":memory:")
    def matcht(tekst):
        return con.execute("SELECT ? LIKE ? ESCAPE '\\'",
                           (tekst, patroon)).fetchone()[0]
    assert matcht("mesh node_a hier") == 1
    assert matcht("mesh nodeXa hier") == 0

    # Hetzelfde geldt voor procent en backslash zelf.
    assert search.parse('name:"50%"').params == ["%50\\%%"]
    assert search.parse(r"name:a\b").params == ["%a\\\\b%"]


def test_onbekend_veld_is_een_fout_met_veldenlijst():
    with pytest.raises(search.QueryError) as err:
        search.parse("veldje:x")
    assert "veldje" in str(err.value)
    # De boodschap moet de wel bestaande velden noemen, zodat de typfout
    # zichzelf verklaart.
    assert "sender" in str(err.value)


def test_onzin_is_altijd_een_fout_nooit_stilte():
    # De kernbelofte van de parser: elke onbegrijpelijke invoer is een
    # QueryError, nooit een stilletjes overgeslagen clausule.
    onzin = [
        "snr:abc",              # tekst waar een getal moet staan
        "len:40..20",           # bereik dat achteruit loopt
        "type:",                # veld zonder waarde
        "-",                    # min zonder iets erachter
        "NOT ",                 # NOT zonder iets erachter
        'name:"open einde',     # aanhalingsteken niet gesloten
        "type:(ADVERT",         # haakje niet gesloten
        "(ADVERT OR ACK)",      # haakjes zonder veld
        "type:()",              # lege lijst
        "sender:*",             # joker zonder stam
    ]
    for tekst in onzin:
        with pytest.raises(search.QueryError):
            search.parse(tekst)


def test_elke_clausule_komt_terug_in_de_sql():
    # Drie clausules in, drie clausules uit: nergens mag er onderweg een
    # verdwijnen.
    q = search.parse("type:ADVERT snr:>5 -scope:share")
    assert q.sql.count(" AND ") == 2
    assert q.params == ["ADVERT", 5.0, "share"]
