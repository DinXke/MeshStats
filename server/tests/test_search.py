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


def test_joker_vooraan_zoekt_op_het_einde():
    q = search.parse("name:*circuit")
    assert q.sql == ("COALESCE(c.name, '') || ' ' || COALESCE(o.name, '')"
                     " LIKE ? ESCAPE '\\'")
    assert q.params == ["%circuit"]


def test_joker_aan_beide_kanten_zoekt_op_bevatten():
    # De vorm waar het om gevraagd is: binnen één veld een deeltekst zoeken.
    # Zonder dit stond er een sterretje letterlijk in het patroon en gaf de
    # zoekopdracht nul treffers zonder te klagen -- geen foutmelding, geen
    # resultaat, en niets dat verklaarde waarom.
    q = search.parse("name:*circuit*")
    assert q.params == ["%circuit%"]
    # Ook op een gewoon veld dat anders exact matcht.
    q = search.parse("type:*MSG*")
    assert q.sql == "p.payload_name LIKE ? ESCAPE '\\'"
    assert q.params == ["%MSG%"]


def test_joker_in_het_midden_houdt_de_volgorde_vast():
    # Eén regel voor alle standen van het sterretje: het staat voor "wat dan
    # ook", waar het ook zit.
    assert search.parse("name:BE*VIR").params == ["BE%VIR"]


def test_joker_werkt_ook_binnen_een_or_lijst():
    q = search.parse("type:(*MSG* OR ACK)")
    assert q.sql == ("(p.payload_name LIKE ? ESCAPE '\\'"
                     " OR p.payload_name = ? COLLATE NOCASE)")
    assert q.params == ["%MSG%", "ACK"]


def test_joker_mag_niet_op_een_getalveld():
    # Bevatten op een getal is zinloos, en de melding moet over het sterretje
    # gaan -- niet over "dit is geen getal", wat waar is maar niets uitlegt over
    # wat er getypt werd.
    for tekst in ("snr:*5*", "hops:3*", "region:*7"):
        with pytest.raises(search.QueryError) as err:
            search.parse(tekst)
        assert "sterretje" in str(err.value), tekst
        assert "tekstveld" in str(err.value), tekst
    # Het veld wordt bij naam genoemd, en het soort waar het op stukloopt.
    with pytest.raises(search.QueryError) as err:
        search.parse("snr:*5*")
    assert "snr" in str(err.value)
    assert "getalveld" in str(err.value)


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
    # Een joker aan de rand voegt bij bevatten-zoeken niets toe en verdwijnt.
    assert search.parse("2ae7*").params == search.parse("2ae7").params
    assert search.parse("*2ae7*").params == search.parse("2ae7").params
    # Een joker in het midden zegt wél iets en blijft dus staan.
    assert search.parse("BE*VIR").params[0] == "%BE%VIR%"


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


def test_joker_en_underscore_raken_niet_in_de_knoop():
    # Het gevoelige geval: de joker van de gebruiker en de escaping van LIKE
    # gebruiken hetzelfde mechanisme. 'name:*_*' hoort een letterlijke
    # underscore te zoeken met aan weerskanten "wat dan ook", niet drie keer
    # "één willekeurig teken". Getest tegen echte SQLite, want dit is een
    # afspraak met de database en niet met een string.
    q = search.parse("name:*_*")
    assert q.params == ["%\\_%"]

    con = sqlite3.connect(":memory:")
    def matcht(tekst, patroon):
        return con.execute("SELECT ? LIKE ? ESCAPE '\\'",
                           (tekst, patroon)).fetchone()[0]
    assert matcht("BE-HSS_JessaZH", q.params[0]) == 1
    assert matcht("BE-HSS.JessaZH", q.params[0]) == 0

    # En een getypt procentteken blijft een procentteken, ook naast een joker.
    q = search.parse('name:"*50%*"')
    assert q.params == ["%50\\%%"]
    assert matcht("korting 50% erop", q.params[0]) == 1
    assert matcht("korting 5012 erop", q.params[0]) == 0

    # Een underscore aan de rand van een prefixzoekopdracht net zo.
    q = search.parse("sender:node_*")
    assert q.params == ["node\\_%"]
    assert matcht("node_a", q.params[0]) == 1
    assert matcht("nodeXa", q.params[0]) == 0


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
        "sender:**",            # ook twee jokers zijn nog geen zoekterm
        "snr:*5*",              # joker op een getalveld
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
        "path",                       # kolom in de tabel, maar geen zinnige volgorde
        "1",
    ]
    for tekst in onzin:
        with pytest.raises(search.QueryError):
            search.parse_sort(tekst)


def test_sorteren_op_regio_gebruikt_dezelfde_afleiding_als_zoeken():
    # De regio staat niet als kolom in de tabel; Field.sql is voor dit ene veld
    # een tijdelijke naam die _field_clause vervangt. Sorteren moet dezelfde
    # vervanging doen, anders noemt de query een kolom die niet bestaat.
    sql = search.parse_sort("region:desc").sql
    assert search.REGION_SQL in sql
    assert "p.scope_region" not in sql


def test_sorteersleutels_verwijzen_naar_echte_kolommen():
    # SORTS wordt uit FIELDS afgeleid; deze test bewaakt dat daar niets
    # binnenglipt wat geen kolomexpressie is die SQLite kan sorteren.
    con = sqlite3.connect(":memory:")
    con.execute("CREATE TABLE p(id INTEGER, ts TEXT, payload_name TEXT, scope TEXT, "
                "scope_codes TEXT, sender TEXT, observer TEXT, route TEXT, "
                "src_hash TEXT, dest_hash TEXT, phash TEXT, "
                "fwd TEXT, fwd_reason TEXT, "
                "snr REAL, rssi REAL, len INTEGER, path_len INTEGER)")
    con.execute("CREATE TABLE c(country TEXT)")
    con.execute("CREATE TABLE o(country TEXT)")
    for naam in search.SORTS:
        sql = search.parse_sort(naam).sql
        con.execute(f"SELECT p.id FROM p, c, o ORDER BY {sql}").fetchall()


def test_kolommen_en_velden_delen_een_woordenschat():
    # De kolomlijst mag geen tweede naamgeving worden: elke naam erin is een
    # zoekveld of de tijdkolom, en elke standaardkolom staat ook in de lijst met
    # beschikbare kolommen.
    for naam in search.COLUMNS:
        assert naam == "time" or naam in search.FIELDS, naam
    for naam in search.DEFAULT_COLUMNS:
        assert naam in search.COLUMNS, naam
    # Geen dubbels: de tabel zou de kolom twee keer tekenen.
    assert len(set(search.COLUMNS)) == len(search.COLUMNS)


def test_kolombeschrijving_vertelt_wat_de_pagina_nodig_heeft():
    kolommen = search.describe_columns()
    assert [k["name"] for k in kolommen] == list(search.COLUMNS)
    per_naam = {k["name"]: k for k in kolommen}
    # Sorteerbaarheid komt uit SORTS, niet uit een tweede mening.
    assert per_naam["hops"]["sort"] is True
    assert per_naam["path"]["sort"] is False      # wel kolom, geen volgorde
    assert per_naam["hops"]["default"] is True
    assert per_naam["path"]["default"] is False
    for naam, k in per_naam.items():
        assert k["sort"] == (naam in search.SORTS), naam


def test_elke_clausule_komt_terug_in_de_sql():
    # Drie clausules in, drie clausules uit: nergens mag er onderweg een
    # verdwijnen.
    q = search.parse("type:ADVERT snr:>5 -scope:share")
    assert q.sql.count(" AND ") == 2
    assert q.params == ["ADVERT", 5.0, "share"]


def test_afzender_en_bestemming_zijn_los_te_bevragen():
    # 'sender' is de volledige sleutel die enkel een advert noemt; 'src' is de
    # ene byte die alle andere pakketten dragen. Twee vragen, twee velden -- en
    # de lijst toont die byte zodra er geen naam bij hoort, dus je moet er ook
    # op kunnen doorklikken.
    assert search.parse("src:e3").params == ["e3"]
    assert "p.src_hash" in search.parse("src:e3").sql
    assert "p.dest_hash" in search.parse("dest:c3").sql
    # En ze lopen elkaar niet in de weg: sender: blijft de sleutelkolom.
    assert "p.sender" in search.parse("sender:2ae7c1").sql
