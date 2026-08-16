"""Tests voor de kandidaatweging in app/candidates.py.

De inzet van deze module is niet "welke node is het", maar "wanneer mogen we dat
zeggen". De tests zijn dan ook zo geschreven dat ze vooral de weigering
vastleggen: geen winnaar bij gelijkspel, geen naam als alles is afgevallen, en
geen uitsluiting op een veld dat het frame niet begrenst.

Alle contacten en pakketgegevens zijn verzonnen. Het grootste deel voert de
weging rechtstreeks aan, zonder database; de laatste sectie laat hem lopen zoals
de API dat doet, tegen een wegwerp-SQLite. De coördinaten liggen steeds op één
breedtegraad zodat de afstanden met de hand na te rekenen zijn: één graad lengte
op 51 graden noorderbreedte is ongeveer 70 km.
"""
from datetime import datetime

import pytest

from app import candidates

NU = datetime(2026, 8, 16, 12, 0, 0)
WAARNEMER = ("aa11bb", (51.0, 5.0))


def contact(prefix, name, lat=None, lon=None, updated="2026-08-16T09:00:00Z"):
    return {"prefix": prefix, "name": name, "lat": lat, "lon": lon,
            "node_type": "repeater", "updated": updated}


def gehoord(hops, seen="2026-08-16T09:00:00Z"):
    return {"hops": hops, "seen": seen}


def weeg(kandidaten, bewijs=None, bound=None):
    return candidates.weigh(kandidaten, evidence=bewijs or {},
                            observer6=WAARNEMER[0], observer_pos=WAARNEMER[1],
                            bound=bound, now=NU)


# --- de grens die het frame wel of niet trekt --------------------------------

@pytest.mark.parametrize("rol,route,padlengte,verwacht", [
    # Een flood draagt het reeds afgelegde pad: hij begrenst waar het pakket
    # vandaan komt, niet waar het heen gaat.
    ("src", "FLOOD", 0, 1),
    ("src", "TRANSPORT_FLOOD", 3, 4),
    ("src", "DIRECT", 0, None),
    # Een direct pakket is bronroutering: het pad is de nog af te leggen route,
    # dus hij begrenst juist de bestemming.
    ("dest", "DIRECT", 0, 2),
    ("dest", "TRANSPORT_DIRECT", 2, 4),
    ("dest", "FLOOD", 0, None),
])
def test_grens_volgt_het_routetype(rol, route, padlengte, verwacht):
    assert candidates.radio_hop_bound(rol, route, padlengte) == verwacht


def test_geen_grens_zonder_route_of_padlengte():
    assert candidates.radio_hop_bound("src", None, 0) is None
    assert candidates.radio_hop_bound("src", "FLOOD", None) is None


def test_hopgrens_telt_vanaf_de_waarnemer():
    # Vier hops in een flood: de eerste ligt vier schakels terug, de laatste één.
    assert candidates.radio_hop_bound("hop", "FLOOD", 4, 0) == 4
    assert candidates.radio_hop_bound("hop", "FLOOD", 4, 3) == 1
    # Bij een direct pakket ligt de eerste hop juist het dichtst bij ons.
    assert candidates.radio_hop_bound("hop", "DIRECT", 4, 0) == 2
    assert candidates.radio_hop_bound("hop", "DIRECT", 4, 3) == 5


# --- fysieke uitsluiting -----------------------------------------------------

def test_nul_hops_sluit_de_onbereikbare_kandidaat_uit():
    """Rechtstreeks gehoord betekent binnen radiobereik; 700 km is dat niet."""
    res = weeg(
        [contact("aa11bb", "De waarnemer zelf", 51.0, 5.0),
         contact("aa22cc", "Ver weg", 51.0, 15.0)],
        bound=candidates.radio_hop_bound("src", "FLOOD", 0),
    )
    assert res["state"] == "known"
    assert [m["name"] for m in res["matches"]] == ["De waarnemer zelf"]
    # Afgevallen, maar niet verdwenen: de weergave moet kunnen zeggen wie en
    # waarom.
    assert [(d["name"], d["why"]) for d in res["dropped"]] == [("Ver weg", "range")]


def test_zonder_grens_valt_er_niemand_af():
    """Een flood zegt niets over de afstand tot zijn bestemming.

    Precies het geval uit de melding: nul hops, maar het gaat om de bestemming
    van een geflood pakket. Dan is er geen fysieke grond om iemand te schrappen
    en blijft alleen de rangschikking over.
    """
    res = weeg(
        [contact("aa11bb", "Dichtbij", 51.0, 5.0),
         contact("aa22cc", "Ver weg", 51.0, 15.0)],
        bound=candidates.radio_hop_bound("dest", "FLOOD", 0),
    )
    assert res["dropped"] == []
    assert len(res["matches"]) == 2


def test_meting_wint_van_de_drempel():
    """Een node die deze waarnemer echt op nul hops heeft gehoord blijft staan.

    De kilometergrens is een schatting van wat een radioschakel aankan; een
    ontvangst is een waarneming. Waar die twee botsen wint de waarneming, anders
    schrijft de drempel de werkelijkheid voor in plaats van andersom.
    """
    ver = contact("aa22cc", "Ver maar gehoord", 51.0, 15.0)
    res = weeg([ver], bewijs={"aa22cc": gehoord(0)},
               bound=candidates.radio_hop_bound("src", "FLOOD", 0))
    assert res["dropped"] == []
    assert res["state"] == "known"


def test_alles_afgevallen_geeft_onbekend():
    """Niet de minst onwaarschijnlijke, maar geen enkele."""
    res = weeg(
        [contact("aa22cc", "Ver weg", 51.0, 15.0),
         contact("aa33dd", "Nog verder", 51.0, 20.0)],
        bound=candidates.radio_hop_bound("src", "FLOOD", 0),
    )
    assert res["state"] == "unknown"
    assert res["matches"] == []
    assert len(res["dropped"]) == 2


# --- rangschikking -----------------------------------------------------------

def test_gehoord_door_deze_waarnemer_gaat_voor():
    """Het voorbeeld uit de melding, nagebouwd: één lokale node en twee die
    alleen diep uit het mesh zijn komen aanwaaien."""
    res = weeg(
        [contact("aa11cc", "Lokaal", 51.0, 5.1),
         contact("aa22cc", "Duitsland-1", 51.0, 7.0),
         contact("aa33cc", "Duitsland-2", 51.0, 7.2)],
        bewijs={"aa11cc": gehoord(1), "aa22cc": gehoord(8), "aa33cc": gehoord(13)},
    )
    assert res["state"] == "likely"
    assert res["lead"] == "hops"
    assert [m["name"] for m in res["matches"]] == ["Lokaal", "Duitsland-1", "Duitsland-2"]


def test_afstand_beslist_als_de_hops_gelijk_liggen():
    res = weeg(
        [contact("aa11cc", "Dichtbij", 51.0, 5.1),
         contact("aa22cc", "Verderop", 51.0, 7.0)],
        bewijs={"aa11cc": gehoord(2), "aa22cc": gehoord(3)},
    )
    assert res["state"] == "likely"
    assert res["lead"] == "distance"
    assert res["matches"][0]["name"] == "Dichtbij"


def test_recentheid_beslist_als_hops_en_afstand_gelijk_liggen():
    res = weeg(
        [contact("aa11cc", "Vandaag", 51.0, 5.1),
         contact("aa22cc", "Vorige maand", 51.0, 5.2)],
        bewijs={"aa11cc": gehoord(2, "2026-08-16T08:00:00Z"),
                "aa22cc": gehoord(2, "2026-07-01T08:00:00Z")},
    )
    assert res["state"] == "likely"
    assert res["lead"] == "recency"
    assert res["matches"][0]["name"] == "Vandaag"


def test_nooit_gehoord_zakt_onder_wel_gehoord():
    res = weeg(
        [contact("aa11cc", "Onbekend in dit mesh", 51.0, 5.0),
         contact("aa22cc", "Wel gehoord", 51.0, 6.5)],
        bewijs={"aa22cc": gehoord(5)},
    )
    assert res["matches"][0]["name"] == "Wel gehoord"
    assert res["lead"] == "hops"


def test_de_waarnemer_zelf_telt_als_rechtstreeks_gehoord():
    """De waarnemer hoeft zichzelf niet te hebben opgevangen om er te zijn.

    Of zijn eigen advert ooit via het mesh bij hem terugkomt is toeval; dat hij
    op nul hops en nul kilometer van zichzelf staat, is dat niet.
    """
    res = weeg(
        [contact("aa11bb", "De waarnemer zelf", 51.0, 5.0),
         contact("aa22cc", "Nooit gehoord", 51.0, 5.6)],
    )
    top = res["matches"][0]
    assert top["name"] == "De waarnemer zelf"
    assert top["hops"] == 0 and top["km"] == 0.0
    assert res["lead"] == "hops"


# --- weigeren te kiezen ------------------------------------------------------

def test_ononderscheidbare_kandidaten_blijven_ambigu():
    """Niets scheidt deze twee: dan geen kop-of-munt met een pluimpje erop."""
    res = weeg(
        [contact("aa11cc", "Alfa", 51.0, 5.1),
         contact("aa22cc", "Bravo", 51.0, 5.15)],
        bewijs={"aa11cc": gehoord(2), "aa22cc": gehoord(2)},
    )
    assert res["state"] == "ambiguous"
    assert res["lead"] is None
    assert len(res["matches"]) == 2


def test_zonder_enig_signaal_blijft_alles_ambigu():
    """Geen posities, geen ontvangsten, geen datums: dan valt er niets te wegen."""
    res = candidates.weigh(
        [contact("aa11cc", "Alfa", updated=None), contact("aa22cc", "Bravo", updated=None)],
        now=NU,
    )
    assert res["state"] == "ambiguous"
    assert res["lead"] is None


def test_een_enkele_kandidaat_blijft_known():
    res = weeg([contact("aa11cc", "Enige", 51.0, 5.1)])
    assert res["state"] == "known"
    assert res["dropped"] == []


def test_geen_kandidaten_is_onbekend():
    res = weeg([])
    assert res["state"] == "unknown"
    assert res["matches"] == [] and res["dropped"] == []


# --- afstand -----------------------------------------------------------------

def test_haversine_klopt_ongeveer():
    # Eén graad lengte op 51 graden noorderbreedte: ongeveer 70 km.
    d = candidates.haversine_km(51.0, 5.0, 51.0, 6.0)
    assert 69.0 < d < 71.0
    assert candidates.haversine_km(51.0, 5.0, 51.0, 5.0) == 0.0


# --- de weg van database tot antwoord ---------------------------------------
# Bovenstaande tests voeren de weging met de hand; deze laat hem lopen zoals de
# API dat doet, inclusief de contactentabel, de ontvangsten en de grens die uit
# het routetype volgt. Het geval is dat uit de melding, met verzonnen nodes:
# een geflood pakket dat op nul hops is opgevangen, met een bestemmingshash die
# op vier contacten past waarvan er twee alleen diep uit het mesh bekend zijn.

@pytest.fixture
def db(tmp_path, monkeypatch):
    from app import config
    from app import db as db_module
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "test.sqlite3")
    db_module._conn = None
    db_module.get_conn()
    yield db_module
    if db_module._conn is not None:
        db_module._conn.close()
        db_module._conn = None


WAARNEMER_SLEUTEL = "55a0010203ff"


def _vul_mesh(db):
    for prefix6, naam, lat, lon in [
        ("55a001", "Waarnemer-thuis", 51.00, 5.00),
        ("55687c", "Zelfde-land", 51.20, 4.10),
        ("559ccf", "Overkant-1", 51.50, 7.20),
        ("55f665", "Overkant-2", 51.60, 6.90),
    ]:
        db.execute(
            "INSERT INTO contacts(prefix, prefix6, name, lat, lon, node_type, updated) "
            "VALUES(?,?,?,?,?,'repeater','2026-08-15T00:00:00Z')",
            (prefix6 + "00", prefix6, naam, lat, lon))
    for prefix6, hops in [("55687c", 3), ("559ccf", 8), ("55f665", 13)]:
        db.execute(
            "INSERT INTO packets(ts, observer, route, payload_name, path_len, sender) "
            "VALUES('2026-08-15T00:00:00Z',?,'FLOOD','ADVERT',?,?)",
            (WAARNEMER_SLEUTEL, hops, prefix6))


def _verse_resolver():
    """routes_api met lege memo's: die overleven anders van test tot test."""
    from app import routes_api
    routes_api._hop_cache.clear()
    routes_api._observer_cache.clear()
    routes_api._hop_cache_filled = 0.0
    return routes_api


def test_bestemming_van_een_flood_wordt_gerangschikt_niet_uitgedund(db):
    """Vier kandidaten, geen enkele uitgesloten, de lokale bovenaan.

    Nul hops zegt bij een flood alleen waar het pakket vandáán komt. De
    bestemming mag er dus niet om worden geschrapt -- maar de waarnemer heeft
    de eerste kandidaat wel van dichtbij gehoord en de andere twee alleen van
    heel ver, en dat is genoeg om een volgorde te verantwoorden.
    """
    _vul_mesh(db)
    api = _verse_resolver()
    res = api._resolve_hop("55", WAARNEMER_SLEUTEL, "dest", "FLOOD", 0)

    assert res["state"] == "likely"
    assert res["lead"] == "hops"
    assert res["dropped"] == []
    assert [m["name"] for m in res["matches"]] == [
        "Waarnemer-thuis", "Zelfde-land", "Overkant-1", "Overkant-2"]


def test_afzender_van_dezelfde_flood_wordt_wel_uitgedund(db):
    """Dezelfde hash, hetzelfde pakket, het andere veld: nu telt nul hops wel.

    Het pakket is rechtstreeks opgevangen, dus zijn afzender stond binnen
    radiobereik -- en de twee nodes aan de overkant staan daar honderden
    kilometers buiten.
    """
    _vul_mesh(db)
    api = _verse_resolver()
    res = api._resolve_hop("55", WAARNEMER_SLEUTEL, "src", "FLOOD", 0)

    assert res["state"] == "likely"
    assert [m["name"] for m in res["matches"]] == ["Waarnemer-thuis", "Zelfde-land"]
    assert sorted(d["name"] for d in res["dropped"]) == ["Overkant-1", "Overkant-2"]
    assert {d["why"] for d in res["dropped"]} == {"range"}


def test_onbekende_hash_blijft_onbekend(db):
    _vul_mesh(db)
    api = _verse_resolver()
    res = api._resolve_hop("77", WAARNEMER_SLEUTEL, "dest", "FLOOD", 0)
    assert res["state"] == "unknown"
    assert res["matches"] == []


# --- de byte blijft staan waar de naam ontbreekt -----------------------------
# Een adreshash die op geen enkel bekend contact past is geen reden om het
# pakket anoniem te noemen: de byte staat in het frame, hij is in elk pakket van
# diezelfde afzender dezelfde, en het archief kan er op filteren. Wat wél
# onderscheiden moet blijven is het pakkettype dat zo'n byte helemaal niet
# draagt -- een ADVERT noemt zijn afzender voluit, een ACK noemt niemand. Daar
# een hash tonen zou liegen, dus daar geeft de API geen object en geen hash.

def _pakket(db, **kolommen):
    velden = {"ts": "2026-08-15T12:00:00Z", "observer": WAARNEMER_SLEUTEL,
              "route": "FLOOD", "payload_name": "TXT_MSG", "path_len": 0}
    velden.update(kolommen)
    namen = ",".join(velden)
    db.execute(f"INSERT INTO packets({namen}) VALUES({','.join('?' * len(velden))})",
               tuple(velden.values()))
    return db.qone("SELECT * FROM packets ORDER BY id DESC LIMIT 1")


def test_hash_zonder_treffer_houdt_zijn_byte(db):
    """Niets past erop, maar de byte gaat wel mee naar de client.

    Onbekend is niet hetzelfde als niets: de hash staat in het frame, hij is in
    elk pakket van diezelfde afzender dezelfde, en het archief kan er op
    filteren. De lijst toont hem dan ook als 0x92 in plaats van enkel het woord
    "onbekend".
    """
    _vul_mesh(db)
    api = _verse_resolver()
    rij = _pakket(db, src_hash="92", dest_hash="93")

    src = api._resolve_src(rij)
    dest = api._resolve_hop("93", WAARNEMER_SLEUTEL, "dest", "FLOOD", 0)
    for res in (src, dest):
        assert res is not None
        assert res["state"] == "unknown"
        assert res["matches"] == []
    assert src["hash"] == "92"
    assert dest["hash"] == "93"


def test_pakket_zonder_adreshash_geeft_geen_object(db):
    """De lege string is de sentinel van de decoder voor "dit type draagt er geen".

    Dit is het onderscheid dat de weergave nodig heeft en dat geen vijfde
    toestand vraagt: geen object betekent "hier valt niets te tonen", en daar is
    "onbekend" het juiste woord. Een object met een hash die nergens op past
    betekent "we weten wél iets", en daar hoort de byte te staan. Een byte
    verzinnen waar het frame er geen draagt zou liegen zijn.
    """
    _vul_mesh(db)
    api = _verse_resolver()
    leeg = _pakket(db, payload_name="ACK", src_hash="", dest_hash="")
    assert api._resolve_src(leeg) is None

    # En een rij van vóór de kolommen bestonden: NULL, niet leeg, zelfde antwoord.
    oud = _pakket(db, payload_name="ACK")
    assert api._resolve_src(oud) is None


def test_advert_leidt_geen_afzender_af(db):
    """Een advert noemt zijn afzender voluit; dan is de byte overbodig."""
    _vul_mesh(db)
    api = _verse_resolver()
    adv = _pakket(db, payload_name="ADVERT", sender="55a001", src_hash="55")
    assert api._resolve_src(adv) is None
