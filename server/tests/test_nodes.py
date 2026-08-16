"""Tests voor het nodedetail achter een bolletje op de live kaart.

Waarom dit een eigen bestand verdient: het antwoord van ``/api/v1/nodes/{prefix}``
is bijna helemaal samengesteld uit dingen die *niet* rechtstreeks in een kolom
staan -- hoeveel verkeer aan een node toe te schrijven valt, wie hem hoort, hoe
vaak hij als hop opduikt -- en elk van die afleidingen heeft een voorbehoud dat
de gebruiker te zien krijgt. Een test die alleen "geeft 200 terug" controleert,
bewaakt het enige niet wat hier stuk kan: dat een onzeker getal onzeker blijft
en een onbekende positie onbekend.

De route-functie wordt rechtstreeks aangeroepen in plaats van via een HTTP-client:
er hangt geen enkele middleware tussen die dit antwoord zou veranderen, en een
testclient zou alleen een extra afhankelijkheid zijn.
"""
import pytest
from fastapi import HTTPException

import frames
from app import config, packets


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
    yield db_module
    if db_module._conn is not None:
        db_module._conn.close()
        db_module._conn = None


# De pubkey uit frames.py telt van 1 tot 32, dus de node waar bijna alles hier
# over gaat heet '010203' in elke tabel die op zes hextekens indexeert.
P6 = frames.PUBKEY[:3].hex()


def _advert(*, ts: int, name: str | None = "Testnode",
            lat: float | None = 50.9, lon: float | None = 5.3,
            hops: tuple[bytes, ...] = ()) -> tuple[str, dict]:
    """Een advert van de standaardnode, als hex plus decodering.

    ``ts`` verschilt per aanroep omdat insert_packet duplicaten binnen een
    minuut per waarnemer wegfiltert op de payloadhash; een andere tijdstempel
    maakt er een ander pakket van, precies zoals in het echt.
    """
    raw = frames.frame(frames.ROUTE_FLOOD, frames.TYPE_ADVERT, hops=hops,
                       payload=frames.advert_payload(
                           timestamp=ts, node_type=2, lat=lat, lon=lon,
                           name=name))
    return raw.hex(), packets.decode(raw)


def _detail(prefix: str = P6) -> dict:
    from app import routes_api
    return routes_api.node_detail(prefix)


def test_onbekende_node_geeft_404(db):
    # Een sleutel waar niets van bekend is, is iets anders dan een node zonder
    # verkeer: die laatste heeft wel een antwoord (zie verderop).
    with pytest.raises(HTTPException) as err:
        _detail("aabbcc")
    assert err.value.status_code == 404


def test_ongeldige_sleutel_geeft_422(db):
    # Geen hex, en te kort. Allebei een vraag die nooit ergens op kan slaan,
    # dus geen 404 ("bestaat niet") maar 422 ("dit is geen sleutel").
    for onzin in ("zzzzzz", "abc", ""):
        with pytest.raises(HTTPException) as err:
            _detail(onzin)
        assert err.value.status_code == 422


def test_langere_sleutel_wordt_ingekort(db):
    # Wie een volledige sleutelprefix in handen heeft moet niet zelf hoeven
    # uitrekenen welke zes tekens de API wil.
    db.insert_packet("bbbbbb111111", _advert(ts=1)[1], raw=_advert(ts=1)[0])
    assert _detail(frames.PUBKEY[:6].hex().upper())["prefix"] == P6


def test_bekende_node_met_verkeer(db):
    # Twee waarnemers vangen elk een advert van dezelfde node op, met
    # verschillende signaalsterkte en op verschillende afstand in hops.
    raw_a, pkt_a = _advert(ts=1, hops=())
    db.insert_packet("bbbbbb111111", pkt_a, snr=6.0, rssi=-90, raw=raw_a)
    raw_b, pkt_b = _advert(ts=2, hops=(b"\x0c", b"\x0d"))
    db.insert_packet("cccccc222222", pkt_b, snr=-4.0, rssi=-110, raw=raw_b)

    d = _detail()
    assert d["prefix"] == P6
    assert d["name"] == "Testnode"
    assert d["node_type"] == "repeater"
    assert d["lat"] == pytest.approx(50.9)
    # De volledige sleutelprefix zoals de advert hem gaf: langer dan de zes
    # tekens waarop de kaart indexeert, en dat verschil hoort zichtbaar te zijn.
    assert d["key_prefix"] == frames.PUBKEY[:6].hex()

    assert d["sent"]["total"] == 2
    assert {o["prefix"] for o in d["sent"]["observers"]} == {"bbbbbb", "cccccc"}
    # Het dichtstbijzijnde oor hoorde hem zonder tussenstop; dat is het getal
    # dat "hoe diep zit deze node in de mesh" beantwoordt.
    assert d["sent"]["hops_min"] == 0
    assert d["sent"]["types"] == [{"type": "ADVERT", "count": 2}]
    assert d["sent"]["scopes"] == [{"scope": "unscoped", "count": 2}]
    assert d["sent"]["first"] is not None and d["sent"]["last"] is not None


def test_snr_per_waarnemer_is_van_de_ontvangende_kant(db):
    # Twee ontvangsten door dezelfde waarnemer: het gemiddelde is van hem, niet
    # van de node. Een node zendt met één vermogen; wat verschilt is het oor.
    for i, snr in enumerate((6.0, 2.0), start=1):
        raw, pkt = _advert(ts=i)
        db.insert_packet("bbbbbb111111", pkt, snr=snr, rssi=-100, raw=raw)

    obs = _detail()["sent"]["observers"]
    assert len(obs) == 1
    assert obs[0]["count"] == 2
    assert obs[0]["snr_avg"] == pytest.approx(4.0)
    assert obs[0]["snr_best"] == pytest.approx(6.0)


def test_directe_pakketten_tellen_niet_mee_voor_hops(db):
    # Bij FLOOD is de padlengte de reeds afgelegde route, bij DIRECT de nog te
    # gane. Ze samen middelen zou deze node als buurman rapporteren op grond
    # van een pakket dat toevallig bijna aangekomen was.
    raw_flood, pkt_flood = _advert(ts=1, hops=(b"\x0c", b"\x0d"))
    db.insert_packet("bbbbbb111111", pkt_flood, raw=raw_flood)
    direct = frames.frame(frames.ROUTE_DIRECT, frames.TYPE_ADVERT,
                          hops=(b"\x0c",),
                          payload=frames.advert_payload(timestamp=9,
                                                        node_type=2,
                                                        lat=50.9, lon=5.3,
                                                        name="Testnode"))
    db.insert_packet("bbbbbb111111", packets.decode(direct), raw=direct.hex())

    d = _detail()
    assert d["sent"]["total"] == 2          # allebei geteld als verkeer
    assert d["sent"]["hops_min"] == 2       # maar alleen de FLOOD telt in hops


def test_node_zonder_positie_blijft_een_antwoord(db):
    # Een advert zonder coördinaten registreert de node wel. De positie stil
    # weglaten zou als een vergissing lezen in plaats van als het feit dat het
    # is -- en het is meteen de reden dat zo'n node geen bolletje heeft.
    raw, pkt = _advert(ts=1, lat=None, lon=None)
    db.insert_packet("bbbbbb111111", pkt, raw=raw)

    d = _detail()
    assert d["lat"] is None and d["lon"] is None
    assert d["name"] == "Testnode"
    assert d["sent"]["total"] == 1


def test_node_zonder_verkeer(db):
    # Een contact dat via Home Assistant binnenkwam en waarvan nooit een pakket
    # is opgevangen. Alles wat over verkeer gaat hoort dan leeg te zijn, en
    # niets ervan mag ontbreken -- een afwezig veld leest als 'niet van
    # toepassing' waar 'nul' bedoeld is.
    db.upsert_contacts([{"prefix": frames.PUBKEY[:6].hex(), "name": "Stille",
                         "lat": 50.9, "lon": 5.3, "type": "repeater"}])

    d = _detail()
    assert d["name"] == "Stille"
    assert d["sent"]["total"] == 0
    assert d["sent"]["observers"] == []
    assert d["sent"]["first"] is None
    assert d["sent"]["hops_min"] is None
    # 'heard' is bewust afwezig in plaats van nul: bijna geen enkele node is
    # zelf waarnemer, en een regel "0 gehoord" onder elk bolletje zou een mesh
    # beschrijven waarin niets iets hoort.
    assert d["heard"] is None
    assert d["as_hop"]["packets"] == 0


def test_als_hop_is_een_bovengrens_met_zijn_reden(db):
    # Een pakket van iemand anders dat via een hop '01' liep. Dat is één byte,
    # en de node hierboven begint ook met 01 -- dus dit telt mee, met het
    # voorbehoud dat het net zo goed een naamgenoot kan zijn geweest.
    raw_adv, pkt_adv = _advert(ts=1)
    db.insert_packet("bbbbbb111111", pkt_adv, raw=raw_adv)
    ander = frames.frame(frames.ROUTE_FLOOD, frames.TYPE_TXT_MSG,
                         hops=(b"\x01",),
                         payload=frames.peer_payload(0xC3, 0xD4))
    db.insert_packet("bbbbbb111111", packets.decode(ander), raw=ander.hex())

    hop = _detail()["as_hop"]
    assert hop["packets"] == 1
    # Hoeveel bekende nodes die eerste byte delen bepaalt hoe groot het
    # voorbehoud is; de client zet dat getal in de uitleg bij de stippellijn.
    assert hop["siblings"] >= 1


def test_hop_van_twee_bytes_matcht_ook(db):
    # De verzendende node kiest of een hop 1, 2 of 3 bytes is, dus alle drie de
    # breedtes moeten geprobeerd worden. Zonder de tweede zou dit pakket
    # ontbreken bij een node die er wel degelijk doorheen kan zijn gelopen.
    raw_adv, pkt_adv = _advert(ts=1)
    db.insert_packet("bbbbbb111111", pkt_adv, raw=raw_adv)
    ander = frames.frame(frames.ROUTE_FLOOD, frames.TYPE_TXT_MSG,
                         hops=(b"\x01\x02",), hash_size=2,
                         payload=frames.peer_payload(0xC3, 0xD4))
    db.insert_packet("bbbbbb111111", packets.decode(ander), raw=ander.hex())
    assert _detail()["as_hop"]["packets"] == 1


def test_hop_matcht_op_hele_entry_niet_op_een_stuk(db):
    # '01' mag niet matchen op de hop '0199': het pad is een lijst, en een
    # ledenmatch is iets anders dan een tekstzoekopdracht.
    raw_adv, pkt_adv = _advert(ts=1)
    db.insert_packet("bbbbbb111111", pkt_adv, raw=raw_adv)
    ander = frames.frame(frames.ROUTE_FLOOD, frames.TYPE_TXT_MSG,
                         hops=(b"\x01\x99",), hash_size=2,
                         payload=frames.peer_payload(0xC3, 0xD4))
    db.insert_packet("bbbbbb111111", packets.decode(ander), raw=ander.hex())
    assert _detail()["as_hop"]["packets"] == 0


def test_node_die_zelf_waarnemer_is(db):
    # De handvol nodes die deze site voeden hebben ook een ontvangstkant. Die
    # hoort erbij te staan, want het is het enige dat over hun *oor* iets zegt.
    raw_adv, pkt_adv = _advert(ts=1)
    db.insert_packet("bbbbbb111111", pkt_adv, raw=raw_adv)
    # Diezelfde node hoort er zelf twee van iemand anders.
    for i in (1, 2):
        ander = frames.frame(frames.ROUTE_FLOOD, frames.TYPE_TXT_MSG,
                             payload=frames.peer_payload(0xC3, 0xD0 + i))
        db.insert_packet(frames.PUBKEY[:6].hex(), packets.decode(ander),
                         raw=ander.hex())

    d = _detail()
    assert d["heard"]["total"] == 2
    assert d["heard"]["first"] is not None


def test_gevolgde_repeater_verwijst_naar_zijn_eigen_pagina(db):
    # Voor de twee eigen repeaters is er al een hele pagina met grafieken. Het
    # paneel geeft de kerncijfers en een link; het bouwt die pagina niet na.
    raw_adv, pkt_adv = _advert(ts=1)
    db.insert_packet("bbbbbb111111", pkt_adv, raw=raw_adv)
    rep = db.get_or_create_repeater(frames.PUBKEY[:6].hex(), "Testrepeater")
    # Een repeater die vanzelf uit een bericht ontstaat komt VERBORGEN binnen
    # (zie get_or_create_repeater), en dit paneel is een publieke route. De
    # beheerder zet hem zichtbaar -- hier in één regel, in het echt met de knop
    # in /admin.
    db.execute("UPDATE repeaters SET is_public=1 WHERE id=?", (rep["id"],))
    db.ingest(rep["id"], db.utcnow(),
              {"online": True, "battery_percentage": 92.0, "uptime": 12.5},
              [{"prefix": "bbbbbb", "name": "Buurman", "snr": -3.5}])

    d = _detail()
    assert d["repeater"]["url"] == "/r/" + rep["slug"]
    assert d["repeater"]["online"] is True
    assert d["repeater"]["battery_percentage"] == pytest.approx(92.0)
    assert d["repeater"]["neighbors"][0]["prefix"] == "bbbbbb"
    assert d["repeater"]["neighbors_capped"] is False


def test_niet_publieke_repeater_krijgt_geen_blok(db):
    # De publieke API laat een verborgen repeater nergens anders uitlekken; dit
    # paneel is geen uitzondering.
    raw_adv, pkt_adv = _advert(ts=1)
    db.insert_packet("bbbbbb111111", pkt_adv, raw=raw_adv)
    rep = db.get_or_create_repeater(frames.PUBKEY[:6].hex(), "Verborgen")
    # Expliciet, ook al is dit sinds de vertrouwensgrens de standaard: deze test
    # gaat over wat de publieke API met een verborgen repeater doet, niet over
    # hoe hij verborgen raakte.
    db.execute("UPDATE repeaters SET is_public=0 WHERE id=?", (rep["id"],))
    assert _detail()["repeater"] is None


def test_buurrelatie_van_een_gewone_node(db):
    # Andersom: een node die zelf niets publiceert, maar wel in de burenlijst
    # van een gevolgde repeater staat. Dat is de enige meting in dit hele
    # paneel die van een node komt in plaats van uit een afleiding hier.
    raw_adv, pkt_adv = _advert(ts=1)
    db.insert_packet("bbbbbb111111", pkt_adv, raw=raw_adv)
    rep = db.get_or_create_repeater("bbbbbb111111", "Waarnemer")
    db.execute("UPDATE repeaters SET is_public=1 WHERE id=?", (rep["id"],))
    db.ingest(rep["id"], db.utcnow(), {"online": True},
              [{"prefix": P6, "name": "Testnode", "snr": -3.5}])

    buren = _detail()["neighbor_of"]
    assert len(buren) == 1
    assert buren[0]["slug"] == rep["slug"]
    assert buren[0]["snr"] == pytest.approx(-3.5)
    assert buren[0]["url"] == "/r/" + rep["slug"]


def test_venster_noemt_bewaartermijn_en_oudste_pakket(db):
    # Een kaal getal leest als 'ooit'. Het venster hoort erbij, en met beide
    # helften: de ingestelde bewaartermijn is de belofte, het oudste bewaarde
    # pakket is wat die belofte tot nu toe heeft waargemaakt.
    raw_adv, pkt_adv = _advert(ts=1)
    db.insert_packet("bbbbbb111111", pkt_adv, raw=raw_adv)

    win = _detail()["window"]
    assert win["days"] == config.PACKET_RETENTION_DAYS
    assert win["oldest"] is not None
