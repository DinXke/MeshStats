"""Tests voor de fijnmazige zichtbaarheid per node: positie tonen, naam tonen.

Waarom dit bestand zwaarder is dan een gewone functietest. Een privacyschakelaar
is geen weergavekeuze maar een belofte, en een belofte die op één van de zeven
uitgangen niet nagekomen wordt, is erger dan geen belofte: de beheerder denkt
dat de positie weg is en handelt daarnaar. Deze suite dekt daarom élke weg waar
een positie of een naam naar buiten kan, één test per endpoint, en niet één test
op de laag eronder -- want dat de view klopt bewijst niet dat elk endpoint hem
ook gebruikt, en dat laatste is precies wat hier stuk kan gaan.

De tweede helft is even belangrijk en makkelijker te vergeten:
``test_standaard_verandert_niets`` legt vast dat een databank die deze kolommen
er net bij kreeg zich exact gedraagt zoals gisteren. De standaardwaarde 1 is een
harde eis -- er staan repeaters op daken die 's ochtends gewoon op de kaart
horen te staan -- en een harde eis zonder test is een hoop.

De routes worden rechtstreeks aangeroepen, zoals in test_nodes.py: er hangt geen
middleware tussen die deze antwoorden verandert.
"""
import pytest

import frames
from app import config, packets

# De node waar alles hier over gaat: de synthetische sleutel uit frames.py, die
# van 1 tot 32 telt. Zes hextekens voor elke tabel die op prefix6 indexeert,
# twaalf voor de repeaterrij zelf.
P6 = frames.PUBKEY[:3].hex()             # '010203'
PREFIX = frames.PUBKEY[:6].hex()         # '010203040506'
HASH_NAME = "0x" + PREFIX[:2].upper()    # '0x01' -- de adreshash, zoals app.js

LAT, LON = 50.9, 5.3
NAAM = "Testnode"

# Een tweede node, die niets verbergt: hij is de controle in bijna elke test.
# Zonder hem zou "de positie is weg" ook kunnen betekenen "er komt sowieso niets
# meer uit dit endpoint".
ANDER = bytes([0xAA]) + bytes(range(2, 33))
A6 = ANDER[:3].hex()                     # 'aa0203'
ALAT, ALON = 51.2, 4.4


@pytest.fixture
def db(tmp_path, monkeypatch):
    """De db-module tegen een verse, tijdelijke database.

    Zelfde opzet als test_nodes.py, plus het legen van de memo's in routes_api:
    die leven op moduleniveau en zouden anders een resolutie uit een vórige test
    -- met een positie die daar nog zichtbaar was -- als antwoord teruggeven.
    Precies het soort valse groen waar deze suite tegen bestaat.
    """
    from app import db as db_module
    from app import routes_api
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "test.sqlite3")
    db_module._conn = None
    routes_api._hop_cache.clear()
    routes_api._observer_cache.clear()
    routes_api._hop_cache_filled = 0.0
    routes_api._heatmap_cache["at"] = 0.0
    routes_api._heatmap_cache["data"] = None
    yield db_module
    if db_module._conn is not None:
        db_module._conn.close()
        db_module._conn = None


def _advert(*, ts: int, pubkey: bytes = frames.PUBKEY, name: str = NAAM,
            lat: float = LAT, lon: float = LON,
            hops: tuple[bytes, ...] = ()) -> tuple[str, dict]:
    """Een advert van een node, als hex plus decodering.

    ``ts`` verschilt per aanroep omdat insert_packet duplicaten binnen een
    minuut per waarnemer wegfiltert op de payloadhash.
    """
    raw = frames.frame(frames.ROUTE_FLOOD, frames.TYPE_ADVERT, hops=hops,
                       payload=frames.advert_payload(
                           pubkey=pubkey, timestamp=ts, node_type=2,
                           lat=lat, lon=lon, name=name))
    return raw.hex(), packets.decode(raw)


def _repeater(db, prefix: str = PREFIX, slug: str = "testnode",
              naam: str = NAAM) -> int:
    """Een gevolgde repeater met de standaardzichtbaarheid: alles aan."""
    return db.execute(
        "INSERT INTO repeaters(slug, pubkey_prefix, name, created_at) "
        "VALUES(?,?,?, '2026-01-01T00:00:00Z')", (slug, prefix, naam))


def _verberg(db, rid: int, *, positie: bool = False, naam: bool = False) -> None:
    """Klap één of beide schakelaars om, zoals de beheerpagina dat doet."""
    if positie:
        db.execute("UPDATE repeaters SET show_position = 0 WHERE id=?", (rid,))
    if naam:
        db.execute("UPDATE repeaters SET show_name = 0 WHERE id=?", (rid,))


def _verkeer(db, *, waarnemer: str = "bbbbbb111111") -> int:
    """Eén opgevangen advert van onze node. Geeft het pakket-id terug."""
    raw, pkt = _advert(ts=1)
    return db.insert_packet(waarnemer, pkt, snr=6.0, rssi=-90, raw=raw)


def _feed(routes_api) -> dict:
    """Het endpoint rechtstreeks aanroepen, met alle parameters expliciet: de
    standaardwaarden in de signature zijn FastAPI's Query-objecten, die alleen
    door de server zelf ingevuld worden."""
    return routes_api.packet_feed(0, 200)


def _zoek(routes_api, q: str = "") -> dict:
    """Idem voor de zoekfunctie; zie tests/test_search_sort.py."""
    return routes_api.packet_search(q=q, since="2020-01-01T00:00:00Z", until="",
                                    limit=100, offset=0, facets="", sort="")


# --- de kolommen zelf --------------------------------------------------------

def test_kolommen_bestaan_en_staan_standaard_aan(db):
    """De standaard is hier het ontwerp, niet een detail.

    ALTER TABLE ADD COLUMN vult bestaande rijen met de standaard, dus een 1 hier
    is wat ervoor zorgt dat een repeater die er gisteren al stond vandaag
    onveranderd zichtbaar is.
    """
    rid = _repeater(db)
    rij = db.qone("SELECT * FROM repeaters WHERE id=?", (rid,))
    assert rij["show_position"] == 1
    assert rij["show_name"] == 1


def test_migratie_geeft_een_bestaande_rij_de_standaard(db, tmp_path, monkeypatch):
    """Een rij die vóór de migratie bestond, komt er zichtbaar uit.

    Nagespeeld zoals het in het echt gaat: een tabel zonder de twee kolommen,
    met een rij erin, en dan pas de migratie eroverheen. Wie dit ooit naar
    DEFAULT 0 verandert, laat deze test vallen -- en dat is precies de bedoeling.
    """
    import sqlite3
    pad = tmp_path / "oud.sqlite3"
    oud = sqlite3.connect(pad)
    oud.executescript(db.SCHEMA)
    oud.execute("INSERT INTO repeaters(slug, pubkey_prefix, name, created_at) "
                "VALUES('dak','010203040506','Dak','2026-01-01T00:00:00Z')")
    oud.commit()
    oud.close()

    monkeypatch.setattr(config, "DB_PATH", pad)
    db._conn = None
    rij = db.qone("SELECT * FROM repeaters WHERE slug='dak'")
    assert rij["show_position"] == 1 and rij["show_name"] == 1


# --- /api/v1/repeaters/{slug}/map -------------------------------------------

def test_map_toont_eigen_positie_niet_meer(db):
    from app import routes_api
    rid = _repeater(db)
    _verkeer(db)
    assert routes_api.repeater_map("testnode")["repeater"]["lat"] == pytest.approx(LAT)

    _verberg(db, rid, positie=True)
    assert routes_api.repeater_map("testnode")["repeater"] is None


def test_map_van_een_ander_laat_de_verborgen_buur_weg_en_zegt_dat(db):
    """De kaart van repeater S, met onze node als buur.

    Twee dingen tegelijk: de coördinaten van de buur staan er niet meer in, en
    de kaart telt hem apart van de buren waarvan we simpelweg nooit een positie
    hoorden. Die twee op één hoop gooien zou de uitklaptekst ("nog geen advert
    met locatie ontvangen") tot een leugen maken.
    """
    from app import routes_api
    rid = _repeater(db)
    sid = _repeater(db, prefix="ffeeddccbbaa", slug="buur", naam="Buur")
    _verkeer(db)
    db.execute("INSERT INTO neighbors(repeater_id, prefix, name, snr, last_seen) "
               "VALUES(?,?,?,?, '2026-01-01T00:00:00Z')", (sid, P6, NAAM, 3.0))
    # Een tweede buur waarvan nooit een advert met locatie binnenkwam.
    db.execute("INSERT INTO neighbors(repeater_id, prefix, name, snr, last_seen) "
               "VALUES(?,?,?,?, '2026-01-01T00:00:00Z')", (sid, "999999", "Stil", 1.0))

    voor = routes_api.repeater_map("buur")
    assert [l["prefix"] for l in voor["links"]] == [P6]
    assert voor["unlocated"] == 1 and voor["hidden"] == 0

    _verberg(db, rid, positie=True)
    na = routes_api.repeater_map("buur")
    assert na["links"] == []
    # Nog steeds één buur zonder advert, plus één die zijn positie niet toont.
    assert na["unlocated"] == 1 and na["unlocated_names"] == ["Stil"]
    assert na["hidden"] == 1 and na["hidden_names"] == [NAAM]


# --- /api/v1/packets ---------------------------------------------------------

def test_feed_levert_geen_positie_van_de_afzender(db):
    from app import routes_api
    rid = _repeater(db)
    _verkeer(db)

    voor = _feed(routes_api)
    assert voor["packets"][0]["sender_lat"] == pytest.approx(LAT)
    assert voor["packets"][0]["lat"] == pytest.approx(LAT)
    assert voor["packets"][0]["origin"] == "sender"

    _verberg(db, rid, positie=True)
    na = _feed(routes_api)
    p = na["packets"][0]
    assert p["sender_lat"] is None and p["sender_lon"] is None
    # Geen afzenderpositie én geen waarnemerpositie: het pakket krijgt geen
    # plek op de kaart in plaats van een geleende.
    assert p["lat"] is None and p["lon"] is None and p["origin"] is None
    assert p["country"] is None


def test_feed_levert_geen_positie_van_de_waarnemer(db):
    """Dezelfde node, nu als het oor in plaats van als de mond.

    ``observer_lat`` is een tweede weg naar precies dezelfde coördinaten, en een
    schakelaar die alleen de eerste dichtdoet is er geen.
    """
    from app import routes_api
    rid = _repeater(db)
    # Eerst een eigen advert, zodat de node met positie in contacts staat --
    # anders bewijst deze test niets over de schakelaar.
    _verkeer(db)
    # En nu hoort onze node een advert van iemand anders.
    raw, pkt = _advert(ts=7, pubkey=ANDER, name="Ander", lat=ALAT, lon=ALON)
    db.insert_packet(PREFIX, pkt, snr=1.0, raw=raw)

    def gehoord_door_ons(res):
        # De feed draagt beide pakketten; dit is het pakket waarin onze node het
        # oor is in plaats van de mond.
        return [p for p in res["packets"] if p["observer"] == PREFIX][0]

    assert gehoord_door_ons(_feed(routes_api))["observer_lat"] == pytest.approx(LAT)

    _verberg(db, rid, positie=True)
    na = gehoord_door_ons(_feed(routes_api))
    assert na["observer_lat"] is None and na["observer_lon"] is None
    # De afzender van dat pakket verbergt niets, dus daar valt de weergave op
    # terug: het pakket verdwijnt niet van de kaart, het verhuist.
    assert na["lat"] == pytest.approx(ALAT) and na["origin"] == "sender"


def test_feed_laat_de_verborgen_node_uit_de_contactenlijst(db):
    """De meegestuurde nodelaag: de basiskaart van de live kaart.

    En de telling erbij, want een kaart die stil een bolletje weglaat, beweert
    alles te tonen wat ze weet.
    """
    from app import routes_api
    rid = _repeater(db)
    _verkeer(db)
    raw, pkt = _advert(ts=8, pubkey=ANDER, name="Ander", lat=ALAT, lon=ALON)
    db.insert_packet("cccccc222222", pkt, raw=raw)

    voor = _feed(routes_api)
    assert {n["prefix"] for n in voor["nodes"]} == {P6, A6}
    assert "hidden_nodes" not in voor

    _verberg(db, rid, positie=True)
    routes_api._hop_cache.clear()
    na = _feed(routes_api)
    assert {n["prefix"] for n in na["nodes"]} == {A6}
    assert na["hidden_nodes"] == 1


# --- /api/v1/packets/search --------------------------------------------------

def test_zoeken_levert_geen_positie_en_vindt_de_naam_niet_meer(db):
    """Het archief draagt geen kale coördinaten, maar wel twee afgeleiden ervan.

    Een zoekrij toont geen lat/lon -- die kolommen zitten niet in het antwoord --
    maar de kandidaten achter een adreshash dragen wél een afstand in
    kilometers tot de waarnemer, en die is uit twee posities berekend. Een
    schakelaar die de coördinaten wegneemt en de afstand laat staan, geeft de
    positie in een andere eenheid weg. Het land gaat om dezelfde reden mee: het
    is uit de coördinaten berekend.
    """
    from app import routes_api
    rid = _repeater(db)
    _verkeer(db)
    # Een waarnemer met een eigen positie, zodat er überhaupt een afstand te
    # berekenen valt, plus een pakket waarvan de bronhash op onze node past.
    raw_o, pkt_o = _advert(ts=5, pubkey=ANDER, name="Oor", lat=ALAT, lon=ALON)
    db.insert_packet(ANDER[:6].hex(), pkt_o, raw=raw_o)
    raw = frames.frame(frames.ROUTE_FLOOD, frames.TYPE_TXT_MSG,
                       payload=frames.peer_payload(0x99, frames.PUBKEY[0]))
    db.insert_packet(ANDER[:6].hex(), packets.decode(raw), raw=raw.hex())

    def bron(res):
        return [p for p in res["packets"] if p["src"]][0]["src"]

    voor = _zoek(routes_api, "")
    assert voor["total"] == 3
    assert [p["sender_name"] for p in voor["packets"] if p["sender"] == P6] == [NAAM]
    kandidaat = [m for m in bron(voor)["matches"] if m["prefix"] == P6][0]
    assert kandidaat["km"] is not None and kandidaat["name"] == NAAM

    _verberg(db, rid, positie=True, naam=True)
    routes_api._hop_cache.clear()
    routes_api._observer_cache.clear()
    na = _zoek(routes_api, "")
    rij = [p for p in na["packets"] if p["sender"] == P6][0]
    assert rij["sender_name"] == HASH_NAME
    assert rij["country"] is None
    kandidaat = [m for m in bron(na)["matches"] if m["prefix"] == P6][0]
    assert kandidaat["km"] is None
    assert kandidaat["name"] == HASH_NAME
    # De zoekfunctie loopt over dezelfde kolommen, dus de echte naam is er ook
    # als zoekterm niet meer: hem wél laten matchen zou de naam alsnog
    # bevestigen aan wie hem al vermoedde.
    assert _zoek(routes_api, f'name:"{NAAM}"')["total"] == 0
    assert _zoek(routes_api, f'name:"{HASH_NAME}"')["total"] >= 1


# --- /api/v1/packets/heatmap -------------------------------------------------

def _heat(db, routes_api):
    routes_api._heatmap_cache["at"] = 0.0
    routes_api._heatmap_cache["data"] = None
    routes_api._hop_cache.clear()
    routes_api._observer_cache.clear()
    return routes_api.packet_heatmap()


def test_heatmap_breekt_de_keten_bij_een_verborgen_node(db):
    """Een node zonder zichtbare positie is geen halte, en dus geen eindpunt.

    Hetzelfde gedrag als bij een dubbelzinnige hop, met opzet: de keten breekt
    daar, en er wordt niet overheen gebrugd. Een geraden segment zou hier keer
    op keer geteld worden tot het er als een stevige, gezaghebbende lijn uitzag
    -- precies de leugen die een drukte-kaart niet mag vertellen.
    """
    from app import routes_api
    rid = _repeater(db)
    # Onze node zendt een advert; een waarnemer met een eigen positie hoort hem.
    raw_o, pkt_o = _advert(ts=3, pubkey=ANDER, name="Oor", lat=ALAT, lon=ALON)
    db.insert_packet(ANDER[:6].hex(), pkt_o, raw=raw_o)
    raw, pkt = _advert(ts=4)
    db.insert_packet(ANDER[:6].hex(), pkt, raw=raw)

    voor = _heat(db, routes_api)
    assert len(voor["segments"]) == 1
    assert {voor["segments"][0]["a"]["prefix"],
            voor["segments"][0]["b"]["prefix"]} == {P6, A6}
    assert voor["hidden_nodes"] == 0

    _verberg(db, rid, positie=True)
    na = _heat(db, routes_api)
    assert na["segments"] == []
    assert na["max"] == 0
    # Geteld, niet verzwegen: de overlay kan er een voetnoot van maken.
    assert na["hidden_nodes"] == 1


# --- /api/v1/packets/{id} ----------------------------------------------------

def test_pakketdetail_levert_geen_positie(db):
    from app import routes_api
    rid = _repeater(db)
    pid = _verkeer(db)

    voor = routes_api.packet_detail(pid)
    assert voor["sender_lat"] == pytest.approx(LAT)

    _verberg(db, rid, positie=True)
    na = routes_api.packet_detail(pid)
    assert na["sender_lat"] is None and na["sender_lon"] is None
    assert na["sender_country"] is None
    # Het advert-blok is een herlezing van de opgeslagen bytes en niet van de
    # contactentabel; het frame draagt de coördinaten zelf. Dat het hier nog
    # staat is bewust en staat zo in docs/api.md: die bytes zijn door de lucht
    # gegaan en iedereen met een radio had ze kunnen opvangen. Deze test legt
    # dat vast zodat de keuze zichtbaar blijft in plaats van een vergetelheid.
    assert na["advert"]["lat"] == pytest.approx(LAT)


def test_pakketdetail_geeft_een_verborgen_node_geen_coordinaat_als_hop(db):
    """De hopresolutie is een derde weg naar een positie.

    Een hop lost op naar een node uit de contactentabel, en die resolutie draagt
    lat/lon en een afstand in kilometers mee. Zonder handhaving hier zou een
    verborgen positie via de omweg van een pad alsnog op tafel liggen.
    """
    from app import routes_api
    rid = _repeater(db)
    _verkeer(db)   # zorgt dat de node in contacts staat
    # Een pakket van iemand anders dat via onze node liep.
    raw = frames.frame(frames.ROUTE_FLOOD, frames.TYPE_TXT_MSG,
                       hops=(bytes([frames.PUBKEY[0]]),),
                       payload=frames.peer_payload(0x99, 0x42))
    pid = db.insert_packet("cccccc222222", packets.decode(raw), raw=raw.hex())

    voor = routes_api.packet_detail(pid)
    hop = voor["path"][0]
    assert [m["prefix"] for m in hop["matches"]] == [P6]
    assert hop["matches"][0]["lat"] == pytest.approx(LAT)

    _verberg(db, rid, positie=True)
    routes_api._hop_cache.clear()
    routes_api._observer_cache.clear()
    na = routes_api.packet_detail(pid)
    hop = na["path"][0]
    # De node blijft een kandidaat -- verbergen is geen wissen, en doen alsof
    # deze hop nergens op past zou een tweede onwaarheid zijn -- maar hij komt
    # zonder plek en zonder afstand.
    assert [m["prefix"] for m in hop["matches"]] == [P6]
    assert hop["matches"][0]["lat"] is None and hop["matches"][0]["lon"] is None
    assert hop["matches"][0]["km"] is None


def test_feed_tekent_geen_pad_over_een_verborgen_node(db):
    """Dezelfde hop, nu langs de bewegende stipjes van de live kaart."""
    from app import routes_api
    rid = _repeater(db)
    _verkeer(db)
    raw = frames.frame(frames.ROUTE_FLOOD, frames.TYPE_TXT_MSG,
                       hops=(bytes([frames.PUBKEY[0]]),),
                       payload=frames.peer_payload(0x99, 0x42))
    db.insert_packet("cccccc222222", packets.decode(raw), raw=raw.hex())

    voor = _feed(routes_api)
    stap = [p for p in voor["packets"] if p["path"]][0]["path"][0]
    assert stap["lat"] == pytest.approx(LAT)

    _verberg(db, rid, positie=True)
    routes_api._hop_cache.clear()
    routes_api._observer_cache.clear()
    na = _feed(routes_api)
    stap = [p for p in na["packets"] if p["path"]][0]["path"][0]
    assert stap["lat"] is None and stap["lon"] is None


# --- /api/v1/nodes/{prefix} --------------------------------------------------

def test_nodedetail_levert_geen_positie_en_geen_naam(db):
    from app import routes_api
    rid = _repeater(db)
    _verkeer(db)

    voor = routes_api.node_detail(P6)
    assert voor["lat"] == pytest.approx(LAT) and voor["name"] == NAAM
    assert voor["country"] is None or isinstance(voor["country"], str)
    assert voor["repeater"]["name"] == NAAM

    _verberg(db, rid, positie=True, naam=True)
    na = routes_api.node_detail(P6)
    assert na["lat"] is None and na["lon"] is None
    assert na["country"] is None
    assert na["name"] == HASH_NAME
    # Het blok van de gevolgde repeater komt uit de repeaterrij, niet uit de
    # contactentabel: een tweede naamweg die apart afgedekt moet zijn.
    assert na["repeater"]["name"] == HASH_NAME
    # De node bestaat nog steeds -- verbergen is geen 404.
    assert na["sent"]["total"] == 1


def test_nodedetail_noemt_een_verborgen_naam_ook_niet_als_buur(db):
    """Drie naamwegen in één antwoord, en ze moeten alle drie dicht.

    ``neighbors.name`` is de gemene: de repeater stuurt zijn burenlijst mét
    namen mee en die naam wint normaal van wat wij uit adverts weten. Een node
    die zijn naam verbergt zou daar zo weer uit komen.
    """
    from app import routes_api
    rid = _repeater(db)
    sid = _repeater(db, prefix="ffeeddccbbaa", slug="buur", naam="Buur")
    _verkeer(db)
    raw, pkt = _advert(ts=9, pubkey=ANDER, name="Ander", lat=ALAT, lon=ALON)
    db.insert_packet("cccccc222222", pkt, raw=raw)
    db.execute("INSERT INTO neighbors(repeater_id, prefix, name, snr, last_seen) "
               "VALUES(?,?,?,?, '2026-01-01T00:00:00Z')", (sid, P6, NAAM, 3.0))
    db.execute("INSERT INTO neighbors(repeater_id, prefix, name, snr, last_seen) "
               "VALUES(?,?,?,?, '2026-01-01T00:00:00Z')", (rid, A6, "Ander", 2.0))

    voor = routes_api.node_detail(P6)
    assert [n["name"] for n in voor["repeater"]["neighbors"]] == ["Ander"]
    assert [r["name"] for r in routes_api.node_detail(A6)["neighbor_of"]] == [NAAM]

    _verberg(db, rid, naam=True)
    # Als buur in de lijst van de andere repeater.
    assert [n["name"] for n in routes_api.repeater_detail("buur")["neighbors"]] == [HASH_NAME]
    # En in het "wordt gehoord door"-blok van de node die hij zelf hoort.
    assert [r["name"] for r in routes_api.node_detail(A6)["neighbor_of"]] == [HASH_NAME]


# --- de naam over /api/v1/repeaters -----------------------------------------

def test_repeaterlijst_toont_de_adreshash_in_plaats_van_de_naam(db):
    from app import routes_api, routes_public
    rid = _repeater(db)

    assert [r["name"] for r in routes_api.list_repeaters()] == [NAAM]

    _verberg(db, rid, naam=True)
    lijst = routes_api.list_repeaters()
    assert [r["name"] for r in lijst] == [HASH_NAME]
    # De sleutelprefix blijft: die zit in elke advert die deze node uitzendt en
    # is voor niemand met een radio geheim. Doen alsof deze site hem geheim kan
    # houden zou een belofte zijn die het apparaat zelf tegenspreekt.
    assert lijst[0]["pubkey_prefix"] == PREFIX
    # De repeater blijft publiek: naam verbergen is niet hetzelfde als de node
    # verbergen, en de cijfers horen er gewoon te staan.
    assert routes_api.repeater_detail("testnode")["name"] == HASH_NAME
    # En de startpagina toont dezelfde naam als de API; twee verschillende
    # namen voor één node zou de schakelaar meteen waardeloos maken.
    assert db.public_name(db.qone("SELECT * FROM repeaters WHERE id=?", (rid,))) == HASH_NAME
    assert routes_public is not None


def test_naam_en_positie_staan_los_van_elkaar(db):
    """Twee schakelaars, geen glijdende schaal.

    Wie alleen zijn plek verbergt, houdt zijn naam -- en andersom. Ze in elkaar
    laten lopen zou de beheerder een keuze afnemen die hij net gekregen heeft.
    """
    from app import routes_api
    rid = _repeater(db)
    _verkeer(db)

    _verberg(db, rid, positie=True)
    d = routes_api.node_detail(P6)
    assert d["lat"] is None and d["name"] == NAAM

    db.execute("UPDATE repeaters SET show_position = 1, show_name = 0 WHERE id=?", (rid,))
    d = routes_api.node_detail(P6)
    assert d["lat"] == pytest.approx(LAT) and d["name"] == HASH_NAME


# --- de standaard verandert niets -------------------------------------------

def test_standaard_verandert_niets(db):
    """Met de standaardwaarden komt overal precies uit wat er vroeger uitkwam.

    De belangrijkste test van dit bestand. Björn heeft twee repeaters op een dak
    staan en heeft niets gevraagd; als deze migratie ook maar één ding van zijn
    kaart haalt, is de functie een verslechtering, hoe net de schakelaars ook
    werken. Daarom hier alle zeven uitgangen in één test, ongewijzigd.
    """
    from app import routes_api
    _repeater(db)
    pid = _verkeer(db)
    sid = _repeater(db, prefix="ffeeddccbbaa", slug="buur", naam="Buur")
    db.execute("INSERT INTO neighbors(repeater_id, prefix, name, snr, last_seen) "
               "VALUES(?,?,?,?, '2026-01-01T00:00:00Z')", (sid, P6, NAAM, 3.0))

    # ORDER BY sort_order, name -- "Buur" staat alfabetisch voorop.
    assert [r["name"] for r in routes_api.list_repeaters()] == ["Buur", NAAM]

    kaart = routes_api.repeater_map("testnode")
    assert kaart["repeater"] == {"name": NAAM, "lat": pytest.approx(LAT),
                                 "lon": pytest.approx(LON)}
    buurkaart = routes_api.repeater_map("buur")
    assert [l["prefix"] for l in buurkaart["links"]] == [P6]
    assert buurkaart["hidden"] == 0 and buurkaart["hidden_names"] == []

    feed = _feed(routes_api)
    assert feed["packets"][0]["sender_lat"] == pytest.approx(LAT)
    assert feed["packets"][0]["sender_name"] == NAAM
    assert {n["prefix"] for n in feed["nodes"]} == {P6}
    assert "hidden_nodes" not in feed

    zoek = _zoek(routes_api, "")
    assert zoek["total"] == 1
    assert zoek["packets"][0]["sender_name"] == NAAM

    heat = routes_api.packet_heatmap()
    assert heat["hidden_nodes"] == 0

    detail = routes_api.packet_detail(pid)
    assert detail["sender_lat"] == pytest.approx(LAT)
    assert detail["sender_name"] == NAAM

    node = routes_api.node_detail(P6)
    assert node["lat"] == pytest.approx(LAT) and node["name"] == NAAM
    assert node["repeater"]["name"] == NAAM
    assert [r["name"] for r in node["neighbor_of"]] == ["Buur"]


def test_de_beheerpagina_blijft_de_waarheid_tonen(db):
    """Verbergen is een keuze over bezoekers, niet over de beheerder.

    Wie de schakelaar omzet moet de echte naam blijven zien, anders kan hij hem
    niet meer terugzetten en weet hij ook niet meer welke node hij voor zich
    heeft -- op een pagina waar elke knop een echt apparaat raakt is dat de
    duurste fout die er is.
    """
    rid = _repeater(db)
    _verberg(db, rid, positie=True, naam=True)
    rij = db.qone("SELECT * FROM repeaters WHERE id=?", (rid,))
    assert rij["name"] == NAAM
    assert db.contact_location(P6) is None   # de publieke kant, ter vergelijking
