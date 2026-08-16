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


def test_sorteren_zonder_parameter_is_nieuwste_eerst():
    # De lege sorteerparameter moet exact de ORDER BY opleveren die db.py als
    # standaard heeft staan; anders verandert het gedrag van het archief
    # stilzwijgend zodra de API de sortering wél doorgeeft.
    s = search.parse_sort("")
    assert s.key == "time"
    assert s.descending is True
    assert s.sql == "p.ts DESC, p.id DESC"
    assert s.token == "time:desc"
    assert search.parse_sort(None).sql == s.sql


def test_sorteren_op_hops_beide_richtingen():
    aflopend = search.parse_sort("hops:desc")
    assert aflopend.sql == "p.path_len IS NULL, p.path_len DESC, p.id DESC"
    oplopend = search.parse_sort("hops:asc")
    assert oplopend.sql == "p.path_len IS NULL, p.path_len ASC, p.id ASC"
    # Zonder richting is aflopend bedoeld, net als de standaardvolgorde.
    assert search.parse_sort("hops").sql == aflopend.sql


def test_lege_waarden_belanden_in_beide_richtingen_achteraan():
    # "x IS NULL" sorteert 0 vóór 1, dus rijen zonder waarde staan onderaan of
    # de richting nu op of af is. Zonder dat zou "sorteer op SNR, kleinste
    # eerst" openen op een pagina streepjes.
    for tekst in ("snr:asc", "snr:desc"):
        assert search.parse_sort(tekst).sql.startswith("p.snr IS NULL, ")
    # De tijdkolom is NOT NULL in het schema en heeft die term dus niet nodig.
    assert "IS NULL" not in search.parse_sort("time:asc").sql


def test_sortering_heeft_altijd_een_unieke_laatste_sleutel():
    # Zonder unieke tiebreaker kunnen twee rijen met dezelfde waarde tussen
    # pagina 1 en pagina 2 van plaats wisselen, waarna een rij dubbel of
    # helemaal niet verschijnt.
    for naam in search.SORTS:
        for richting in ("asc", "desc"):
            sql = search.parse_sort(f"{naam}:{richting}").sql
            assert sql.endswith(f"p.id {richting.upper()}"), sql


def test_alleen_kolommen_uit_de_tabel_zijn_sorteerbaar():
    # De verdediging tegen SQL-injectie via de sorteerparameter: de sleutel
    # wordt opgezocht, nooit doorgegeven. Alles wat niet in SORTS staat is een
    # QueryError, en geen enkele SQL bevat iets van wat er getypt is.
    onzin = [
        "veldje",                     # bestaat niet
        "p.ts",                       # een kolomnaam is geen sleutel
        "hops; DROP TABLE packets",   # de klassieker
        "hops:desc--",                # richting die niet bestaat
        "hops:willekeurig",
        "name",                       # zoekbaar, maar geen kolom om op te sorteren
        "path",
        "region",                     # sql is een tijdelijke naam, geen kolom
        "1",
    ]
    for tekst in onzin:
        with pytest.raises(search.QueryError):
            search.parse_sort(tekst)


def test_sorteersleutels_verwijzen_naar_echte_kolommen():
    # SORTS wordt uit FIELDS afgeleid; deze test bewaakt dat daar niets
    # binnenglipt wat geen kolomexpressie is die SQLite kan sorteren.
    con = sqlite3.connect(":memory:")
    con.execute("CREATE TABLE p(id INTEGER, ts TEXT, payload_name TEXT, scope TEXT, "
                "sender TEXT, snr REAL, rssi REAL, len INTEGER, path_len INTEGER)")
    con.execute("CREATE TABLE c(country TEXT)")
    con.execute("CREATE TABLE o(country TEXT)")
    for naam in search.SORTS:
        sql = search.parse_sort(naam).sql
        con.execute(f"SELECT p.id FROM p, c, o ORDER BY {sql}").fetchall()


def test_elke_clausule_komt_terug_in_de_sql():
    # Drie clausules in, drie clausules uit: nergens mag er onderweg een
    # verdwijnen.
    q = search.parse("type:ADVERT snr:>5 -scope:share")
    assert q.sql.count(" AND ") == 2
    assert q.params == ["ADVERT", 5.0, "share"]
