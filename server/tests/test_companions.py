"""De companion-laag: de bestemmingslijst, de commandotaal en de wegen ernaartoe.

Drie dingen kunnen hier stuk zonder een foutmelding:

1. De **commandotaal**. Een verkeerd gebouwde DM is niet een exception maar een
   commando dat de companion niet begrijpt -- of erger, een ander commando dan
   bedoeld. Daarom controleert dit bestand elke tak van ``build`` op de exacte
   tekst, inclusief dat de mesh-vorm de ``!`` draagt.
2. De **grens rond het muteren van de lijst**. Companion-CRUD is een
   serverhandeling; een gewone gebruiker hoort hem niet te kunnen. Dat valt met
   geen enkele gedragstest te vangen -- de route werkt, hij werkt alleen voor
   iedereen -- dus hier staat een test die de weigering afdwingt.
3. Het **renderen** van de nieuwe pagina's, om dezelfde reden als
   test_beheerpaginas_renderen: een sjabloonfout is een lege beheerpagina en geen
   testfout.
"""
import time

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from app import config


@pytest.fixture
def db(tmp_path, monkeypatch):
    from app import companions, db as db_module
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "test.sqlite3")
    db_module._conn = None
    # Elke test krijgt verse repeater-id's die weer bij 1 beginnen -- zonder
    # deze reset zou de bot-cache (companions._bots_cache, zie
    # companions.reset_bots_cache) een node-id uit een VORIGE test kunnen
    # laten doorwerken in deze test.
    companions.reset_bots_cache()
    yield db_module
    if db_module._conn is not None:
        db_module._conn.close()
        db_module._conn = None


def maak_gebruiker(naam, superuser=False):
    from app import auth, rbac
    return rbac.maak_gebruiker(naam, auth.hash_password("wachtwoord123"),
                               is_superuser=superuser)


def verzoek(cookie: str = "", method: str = "GET", accept: str = "",
           qs: str = "") -> Request:
    headers = [(b"cookie", f"mm_session={cookie}".encode())] if cookie else []
    if accept:
        # De fetch-weg van companions.js zet Accept: application/json op de
        # commando-knoppen, zodat companion_cmd JSON teruggeeft in plaats van
        # de PRG-redirect -- zie _wants_json in routes_companions.py.
        headers.append((b"accept", accept.encode()))
    return Request({
        "type": "http", "http_version": "1.1", "method": method,
        "scheme": "http", "server": ("test", 80), "path": "/x",
        "query_string": qs.encode(), "headers": headers,
    })


# --- 1. de databanklaag ------------------------------------------------------

VALID = "a" * 64


def test_companion_crud(db):
    """Toevoegen, lezen, bijwerken, verwijderen -- de hele levensloop."""
    cid = db.add_companion("Björn", VALID, "T1000-E", "handzender", None)
    rij = db.companion(cid)
    assert rij["name"] == "Björn"
    assert rij["pubkey"] == VALID
    assert rij["type"] == "T1000-E"
    # Op de sleutel terug te vinden, ongeacht hoofdletters.
    assert db.companion_by_pubkey(VALID.upper())["id"] == cid
    # Bijwerken raakt alle bewerkbare velden.
    db.update_companion(cid, "Björn 2", "b" * 64, "", "", None)
    rij = db.companion(cid)
    assert rij["name"] == "Björn 2"
    assert rij["pubkey"] == "b" * 64
    assert rij["type"] is None
    assert db.list_companions()[0]["id"] == cid
    # Verwijderen telt de rij.
    assert db.delete_companion(cid) == 1
    assert db.companion(cid) is None
    assert db.delete_companion(cid) == 0


def test_sender_koppeling_wordt_losgekoppeld_bij_verwijderde_node(db):
    """ON DELETE SET NULL: verdwijnt de standaardafzender, dan houdt de companion
    zijn identiteit maar verliest zijn afzender -- geen dangling verwijzing."""
    node = db.get_or_create_repeater("e3d3f4d7edd0", "Dakrepeater")
    cid = db.add_companion("X", VALID, "", "", node["id"])
    assert db.companion(cid)["sender_repeater_id"] == node["id"]
    db.execute("DELETE FROM repeaters WHERE id=?", (node["id"],))
    assert db.companion(cid)["sender_repeater_id"] is None


# --- 2. de commandotaal ------------------------------------------------------

def test_valid_pubkey():
    from app import companions
    assert companions.valid_pubkey(VALID)
    assert companions.valid_pubkey(VALID.upper())
    assert not companions.valid_pubkey("a" * 63)
    assert not companions.valid_pubkey("a" * 65)
    assert not companions.valid_pubkey("z" * 64)
    assert not companions.valid_pubkey("")


@pytest.mark.parametrize("cmd,args,body", [
    ("find", {}, "!find"),
    ("findstop", {}, "!findstop"),
    ("ping", {}, "!ping"),
    ("loc", {}, "!loc"),
    ("cfg", {}, "!cfg"),
    ("status", {}, "!status"),
    ("tunes", {}, "!tunes"),
    ("mute", {"state": "on"}, "!mute on"),
    ("mute", {"state": "off"}, "!mute off"),
    # De "followapp"-uitbreiding: alleen aangehangen als expliciet gevraagd.
    ("mute", {"state": "on", "followapp": "1"}, "!mute on followapp"),
    ("mute", {"state": "off", "followapp": "0"}, "!mute off"),
    ("vol", {"slot": "H", "level": "3"}, "!vol H 3"),
    ("vol", {"slot": "msg", "level": "0"}, "!vol msg 0"),
    # Globaal volume: geen slot (leeg, of het woord 'global').
    ("vol", {"slot": "", "level": "1"}, "!vol 1"),
    ("vol", {"level": "2"}, "!vol 2"),
    ("vol", {"slot": "global", "level": "3"}, "!vol 3"),
    ("gps", {"mode": "ondemand"}, "!gps ondemand"),
    # Beltoon per ernst: slot + een ingebouwde beltoon uit de firmware-bibliotheek.
    ("tune", {"slot": "H", "name": "coin"}, "!tune H preset coin"),
    ("tune", {"slot": "msg", "name": "beep"}, "!tune msg preset beep"),
    # Beltoon-preview: alleen een naam.
    ("play", {"name": "mario-1up"}, "!play mario-1up"),
    ("play", {"name": "warning"}, "!play warning"),
    # Stille periode: bereik + actie (mute/0-3/off), of gewoon uit.
    ("quiet", {"range": "22-7", "action": "mute"}, "!quiet 22-7 mute"),
    ("quiet", {"range": "22-7", "action": "2"}, "!quiet 22-7 2"),
    ("quiet", {"range": "off"}, "!quiet off"),
    ("quiet", {"range": "22-7", "action": "off"}, "!quiet off"),
    # Valdetectie: de OUDE vorm (geen sub) blijft werken...
    ("fall", {"state": "on"}, "!fall on"),
    ("fall", {"state": "off"}, "!fall off"),
    # ...naast de volledige subopdrachtenset uit de firmware-CLI.
    ("fall", {"sub": "on"}, "!fall on"),
    ("fall", {"sub": "off"}, "!fall off"),
    ("fall", {"sub": "sens", "level": "low"}, "!fall sens low"),
    ("fall", {"sub": "sens", "level": "high"}, "!fall sens high"),
    ("fall", {"sub": "nomotion", "state": "on"}, "!fall nomotion on"),
    ("fall", {"sub": "prealarm", "state": "off"}, "!fall prealarm off"),
    ("fall", {"sub": "mm", "state": "on"}, "!fall mm on"),
    ("fall", {"sub": "test"}, "!fall test"),
    ("fall", {"sub": "status"}, "!fall status"),
    ("fall", {"sub": "target", "action": "list"}, "!fall target list"),
    ("fall", {"sub": "target", "action": "add", "value": VALID},
     "!fall target add " + VALID),
    ("fall", {"sub": "target", "action": "del", "value": "abcdef"},
     "!fall target del abcdef"),
    ("preset", {"slot": "2", "text": "onderweg"}, "!preset 2 onderweg"),
    ("allow", {"sub": "list"}, "!allow list"),
    ("allow", {"sub": "add", "value": VALID}, "!allow add " + VALID),
    ("allow", {"sub": "del", "value": "abcdef"}, "!allow del abcdef"),
    ("rxps", {"mode": "off"}, "!rxps off"),
    ("rxps", {"mode": "conservative"}, "!rxps conservative"),
    ("rxps", {"mode": "balanced"}, "!rxps balanced"),
    ("radioshow", {}, "!radio show"),
    ("radio", {"field": "freq", "value": "869.525"}, "!radio freq 869.525 confirm"),
    ("radio", {"field": "bw", "value": "250"}, "!radio bw 250 confirm"),
    ("radio", {"field": "sf", "value": "11"}, "!radio sf 11 confirm"),
    ("radio", {"field": "cr", "value": "5"}, "!radio cr 5 confirm"),
    ("radio", {"field": "tx-power", "value": "-9"}, "!radio tx-power -9 confirm"),
])
def test_build_ok(cmd, args, body):
    """De mesh-vorm draagt de ``!`` en precies de verwachte tekst."""
    from app import companions
    out = companions.build(cmd, args)
    assert out["ok"], out["error"]
    assert out["body"] == body


def test_tune_library_is_de_firmwarelijst():
    """De keuzelijst is precies de ingebouwde firmware-bibliotheek -- letterlijk."""
    from app import companions
    assert companions.TUNE_LIBRARY == (
        "mario-main", "mario-die", "mario-1up", "coin", "powerup",
        "warning", "chime", "alert", "beep")


@pytest.mark.parametrize("cmd,args", [
    ("mute", {"state": "aan"}),
    ("vol", {"slot": "H", "level": "4"}),
    ("vol", {"slot": "X", "level": "2"}),
    ("gps", {"mode": "sometimes"}),
    ("tune", {"slot": "X", "name": "coin"}),
    ("tune", {"slot": "H", "name": "onbekende-toon"}),
    ("play", {"name": "onbekende-toon"}),
    ("play", {"name": ""}),
    ("quiet", {"range": "avond", "action": "mute"}),
    ("quiet", {"range": "22-7", "action": "hard"}),
    ("preset", {"slot": "9", "text": "x"}),
    ("preset", {"slot": "1", "text": ""}),
    ("allow", {"sub": "add", "value": "tekort"}),
    ("allow", {"sub": "del", "value": "abc"}),
    ("vol", {"slot": "bogus", "level": "1"}),
    ("vol", {"slot": "H", "level": "9"}),
    ("fall", {"state": "misschien"}),
    ("fall", {"sub": "onbekend"}),
    ("fall", {"sub": "sens", "level": "extreme"}),
    ("fall", {"sub": "nomotion", "state": "misschien"}),
    ("fall", {"sub": "target", "action": "add", "value": "kort"}),
    ("fall", {"sub": "target", "action": "del", "value": "abc"}),
    ("fall", {"sub": "target", "action": "onbekend"}),
    ("rxps", {"mode": "turbo"}),
    ("radio", {"field": "freq", "value": "niet-numeriek"}),
    ("radio", {"field": "onbekend-veld", "value": "1"}),
    ("radio", {"field": "freq", "value": ""}),
    ("onbekend", {}),
])
def test_build_weigert(cmd, args):
    """Een fout commando wordt geweigerd met een reden, niet als halve DM verstuurd."""
    from app import companions
    out = companions.build(cmd, args)
    assert not out["ok"]
    assert out["error"]
    assert out["body"] == ""


def test_send_command_bouwt_en_verstuurt(db, monkeypatch):
    """send_command bouwt de tekst en geeft hem aan het vervoer; de body komt terug."""
    from app import companions, rooms
    gezien = {}

    def nep_bot_sendto(rep, pubkey, msg, bot=None):
        gezien["pubkey"] = pubkey
        gezien["msg"] = msg
        return {"ok": True, "error": ""}

    monkeypatch.setattr(rooms, "bot_sendto", nep_bot_sendto)
    rep = db.get_or_create_repeater("e3d3f4d7edd0", "Dakrepeater")
    out = companions.send_command(rep, VALID, "vol", {"slot": "H", "level": "2"})
    assert out["ok"]
    assert out["body"] == "!vol H 2"
    assert gezien == {"pubkey": VALID, "msg": "!vol H 2"}


def test_send_command_verstuurt_niets_bij_fout(db, monkeypatch):
    """Een fout commando raakt het vervoer niet -- er gaat niets de band op."""
    from app import companions, rooms
    monkeypatch.setattr(rooms, "bot_sendto",
                        lambda *a, **k: pytest.fail("mocht niet versturen"))
    rep = db.get_or_create_repeater("e3d3f4d7edd0", "Dakrepeater")
    out = companions.send_command(rep, VALID, "vol", {"slot": "H", "level": "9"})
    assert not out["ok"]
    assert out["body"] == ""


# --- 3. de routes: eigen module, rechten en renderen -------------------------
#
# De companion-routes leven in hun EIGEN module (routes_companions) met een eigen
# router. Deze sectie roept ze rechtstreeks daar aan, en dwingt bovendien af dat
# elke schrijvende route van die router door de rechtenpoort gaat -- de vangnettest
# van test_rechten kijkt alleen naar routes_admin.router, dus die van dit onderdeel
# hoort hier.

def _sessie(naam):
    from app import auth
    return auth.make_session(naam)


def test_elke_schrijvende_companionroute_gaat_door_de_poort():
    """De vangnettest voor de eigen router: elke POST roept require_perm aan.

    Zonder deze test zou een route in de nieuwe module de rechtencontrole kunnen
    missen zonder dat iets het merkt -- hij werkt, hij werkt alleen voor iedereen.
    """
    import inspect
    from app import routes_companions

    vergeten = []
    for route in routes_companions.router.routes:
        if "POST" not in getattr(route, "methods", set()):
            continue
        if "require_perm(" not in inspect.getsource(route.endpoint):
            vergeten.append(route.endpoint.__name__)
    assert vergeten == [], f"zonder rechtencontrole: {vergeten}"


def test_companionroutes_noemen_alleen_bestaande_handelingen():
    """Een tikfout in een handelingsnaam is een dichte deur; hier valt hij op."""
    import re
    from app import rbac, routes_companions

    bron = inspect_source(routes_companions)
    genoemd = set(re.findall(r'require_perm\(\s*request,\s*"([^"]+)"', bron))
    assert genoemd, "geen enkele aanroep gevonden -- verkeerde vorm"
    assert genoemd - set(rbac.ACTIONS) == set()


def inspect_source(module):
    import inspect
    return inspect.getsource(module)


def test_companion_toevoegen_vereist_serverbeheerder(db):
    """Een gewone gebruiker mag de lijst niet muteren -- de grens die geen
    gedragstest vangt."""
    from app import routes_companions
    maak_gebruiker("gewoon", superuser=False)
    req = verzoek(_sessie("gewoon"), method="POST")
    with pytest.raises(HTTPException) as fout:
        routes_companions.companion_add(req, name="X", pubkey=VALID, type="",
                                        notes="", sender="", csrf="x")
    assert fout.value.status_code == 403


def test_companion_toevoegen_als_serverbeheerder(db):
    """De serverbeheerder voegt toe; de rij staat erna in de databank, en de
    route redirect (PRG) naar de lijst in plaats van de pagina zelf te
    renderen -- zie de toelichting bij ``_redirect`` in routes_companions.py
    voor waarom (het is de root cause van de "ververst niet" klacht)."""
    from app import auth, routes_companions
    maak_gebruiker("baas", superuser=True)
    cookie = _sessie("baas")
    req = verzoek(cookie, method="POST")
    resp = routes_companions.companion_add(
        req, name="Björn", pubkey=VALID, type="T1000-E", notes="",
        sender="", csrf=auth.csrf_token(cookie))
    assert resp.status_code == 303
    assert resp.headers["location"].startswith("/admin/companions?")
    assert "r=added" in resp.headers["location"]
    assert db.companion_by_pubkey(VALID) is not None

    # De doelpagina zelf leest die code terug en toont dezelfde melding als
    # vroeger rechtstreeks in de POST-respons stond.
    resp2 = routes_companions.companions_page(verzoek(cookie, qs="r=added&n=Bj%C3%B6rn"))
    assert resp2.status_code == 200
    assert "toegevoegd" in resp2.body.decode("utf-8")


def test_companion_toevoegen_fout_redirect_met_reden(db):
    """Een validatiefout is ook een redirect, met een foutcode -- geen 200 met
    de fout meteen op de POST-respons zelf (dat was het oude patroon)."""
    from app import auth, routes_companions
    maak_gebruiker("baas", superuser=True)
    cookie = _sessie("baas")
    resp = routes_companions.companion_add(
        verzoek(cookie, method="POST"), name="", pubkey=VALID, type="",
        notes="", sender="", csrf=auth.csrf_token(cookie))
    assert resp.status_code == 303
    assert "r=err_name" in resp.headers["location"]


def test_companion_bewerken_redirect_naar_eigen_pagina(db):
    """Bewerken redirect naar de companion-pagina zelf (niet de lijst): daar
    staat het bewerkte resultaat, en het formulier stond daar ook."""
    from app import auth, routes_companions
    maak_gebruiker("baas", superuser=True)
    cookie = _sessie("baas")
    cid = db.add_companion("Björn", VALID, "T1000-E", "", None)
    resp = routes_companions.companion_edit(
        verzoek(cookie, method="POST"), cid, name="Björn 2", pubkey=VALID,
        type="", notes="", sender="", csrf=auth.csrf_token(cookie))
    assert resp.status_code == 303
    assert resp.headers["location"] == f"/admin/companions/{cid}?r=edited"
    assert db.companion(cid)["name"] == "Björn 2"

    # De pagina zelf leest de code terug in de melding.
    pagina = routes_companions.companion_page(verzoek(cookie, qs="r=edited"), cid)
    assert "bijgewerkt" in pagina.body.decode("utf-8")


def test_companion_verwijderen_redirect_naar_lijst(db):
    """Verwijderen redirect naar de LIJST (de detailpagina van deze companion
    bestaat na de mutatie niet meer)."""
    from app import auth, routes_companions
    maak_gebruiker("baas", superuser=True)
    cookie = _sessie("baas")
    cid = db.add_companion("Björn", VALID, "T1000-E", "", None)
    resp = routes_companions.companion_delete(
        verzoek(cookie, method="POST"), cid, csrf=auth.csrf_token(cookie))
    assert resp.status_code == 303
    assert resp.headers["location"] == "/admin/companions?r=deleted&n=Bj%C3%B6rn"
    assert db.companion(cid) is None


def test_paginas_renderen(db):
    """companions, companion-detail, senddm en de kaart door de echte
    Jinja-omgeving."""
    from app import routes_companions
    maak_gebruiker("baas", superuser=True)
    cookie = _sessie("baas")
    cid = db.add_companion("Björn", VALID, "T1000-E", "", None)
    for resp in (routes_companions.companions_page(verzoek(cookie)),
                 routes_companions.companion_page(verzoek(cookie), cid),
                 routes_companions.senddm_page(verzoek(cookie)),
                 routes_companions.companions_map_page(verzoek(cookie))):
        assert resp.status_code == 200


def test_companions_status_json_bevat_alle_companions_en_pollt_ondemand(db, monkeypatch):
    """De verversings-route achter companions.js/companions_map.js: geeft de
    actuele locatie/val van elke companion terug, en heeft er ONDERWEG een
    ondemand-poll voor gedraaid (companions.poll_now) -- dit is de "een pagina
    die bekeken wordt, ziet meteen verse data"-eis."""
    from app import companions, routes_companions, sensornode
    monkeypatch.setattr(companions, "_ondemand_last_ts", 0.0)
    maak_gebruiker("baas", superuser=True)
    cookie = _sessie("baas")
    node = db.get_or_create_repeater("e3d3f4d7edd0", "Dakrepeater")
    db.set_sensor_host(node["id"], "10.0.0.5", by_admin=True)
    db.add_companion("Björn", VALID, "T1000-E", "", node["id"])
    db.add_companion("Zonder locatie", "c" * 64, "", "", None)

    def nep_json(host, path, timeout=None):
        return {"ok": True, "error": "", "data": {"companions": [
            {"name": "Björn", "pubkey": VALID, "lat": 51.2, "lon": 5.4, "seen": 3}]}}

    monkeypatch.setattr(sensornode, "_json", nep_json)
    resp = routes_companions.companions_status_json(verzoek(cookie))
    assert resp.status_code == 200
    import json
    data = json.loads(resp.body)
    bij_pubkey = {c["name"]: c for c in data["companions"]}
    assert len(bij_pubkey) == 2
    assert bij_pubkey["Björn"]["lat"] == 51.2
    assert bij_pubkey["Zonder locatie"]["lat"] is None


def test_companions_map_pagina_rendert_ook_met_een_locatie(db):
    """De kaart-pagina met minstens één bekende locatie -- de andere aanroep
    hierboven dekt alleen het lege geval."""
    from app import routes_companions
    maak_gebruiker("baas", superuser=True)
    cookie = _sessie("baas")
    db.add_companion("Björn", VALID, "T1000-E", "", None)
    db.set_companion_location(VALID, 51.2, 5.4, 1000)
    resp = routes_companions.companions_map_page(verzoek(cookie))
    assert resp.status_code == 200


# --- 4. companion-locaties: databank, vertaling van 'seen', en de poll -------

def test_set_companion_location_matcht_op_pubkey_ongeacht_hoofdletters(db):
    """De update gaat op de sleutel, niet op een id -- dezelfde normalisatie als
    companion_by_pubkey."""
    cid = db.add_companion("Björn", VALID, "", "", None)
    assert db.set_companion_location(VALID.upper(), 51.2, 5.4, 1000)
    rij = db.companion(cid)
    assert rij["last_lat"] == 51.2
    assert rij["last_lon"] == 5.4
    assert rij["last_seen"] == 1000
    # Een pubkey die niet in de lijst staat: stilzwijgend niets, geen crash.
    assert db.set_companion_location("f" * 64, 0.0, 0.0, 0) is False


def test_companions_with_location_alleen_wie_een_positie_heeft(db):
    a = db.add_companion("A", VALID, "", "", None)
    db.add_companion("B", "b" * 64, "", "", None)
    db.set_companion_location(VALID, 1.0, 2.0, 100)
    assert [r["id"] for r in db.companions_with_location()] == [a]


def test_iso_from_epoch():
    # Geen ``db``-fixture nodig (geen databank geraakt) -- rechtstreeks de
    # module, want de naam ``db`` op moduleniveau is hier de FIXTURE-functie.
    from app import db as db_module
    assert db_module.iso_from_epoch(None) is None
    assert db_module.iso_from_epoch("rommel") is None
    assert db_module.iso_from_epoch(0) == "1970-01-01T00:00:00Z"


def test_location_nodes_alleen_de_ingestelde_afzenders(db):
    """Een node zonder companion hoort niet in de lijst -- overal aankloppen
    zou voor niets 404's opleveren op nodes die /companions.json niet kennen."""
    from app import companions
    node = db.get_or_create_repeater("e3d3f4d7edd0", "Dakrepeater")
    db.get_or_create_repeater("aabbccddeeff", "Ongebruikte node")
    db.add_companion("Björn", VALID, "", "", node["id"])
    assert [r["id"] for r in companions.location_nodes()] == [node["id"]]


def test_location_nodes_only_rep_id_beperkt_tot_die_ene_node(db):
    """De detailpagina van één companion mag alleen ZIJN afzender pollen, niet
    de andere -- zie companions.poll_now."""
    from app import companions
    a = db.get_or_create_repeater("e3d3f4d7edd0", "A")
    b = db.get_or_create_repeater("aabbccddeeff", "B")
    db.add_companion("Björn", VALID, "", "", a["id"])
    db.add_companion("Ander", "b" * 64, "", "", b["id"])
    assert {r["id"] for r in companions.location_nodes()} == {a["id"], b["id"]}
    assert [r["id"] for r in companions.location_nodes(only_rep_id=a["id"])] == [a["id"]]
    # Een node-id die geen enkele companion als afzender heeft: lege lijst, geen
    # crash op een "onbestaande" filter.
    assert companions.location_nodes(only_rep_id=999999) == []


def test_poll_now_hamerbescherming(db, monkeypatch):
    """Twee ondemand-pollrondes vlak na elkaar: de tweede slaat over (cooldown),
    de eerste heeft het werk al gedaan. Zonder dit zou een pagina die elke
    15-20s ververst (of meerdere open tabbladen) bij elke tik alle
    afzender-nodes lastigvallen."""
    from app import companions, sensornode
    node = db.get_or_create_repeater("e3d3f4d7edd0", "Dakrepeater")
    db.set_sensor_host(node["id"], "10.0.0.5", by_admin=True)
    db.add_companion("Björn", VALID, "", "", node["id"])
    aanroepen = []

    def nep_json(host, path, timeout=None):
        aanroepen.append(host)
        return {"ok": True, "error": "", "data": {"companions": [
            {"name": "Björn", "pubkey": VALID, "lat": 1.0, "lon": 2.0, "seen": 1}]}}

    monkeypatch.setattr(sensornode, "_json", nep_json)
    monkeypatch.setattr(companions, "_ondemand_last_ts", 0.0)
    eerste = companions.poll_now()
    tweede = companions.poll_now()
    assert eerste is not None and eerste["updated"] == 1
    assert tweede is None
    assert len(aanroepen) == 1


def test_secs_to_epoch_onderscheidt_ouderdom_en_absolute_tijd():
    """De heuristiek uit companions._secs_to_epoch: een klein getal is een
    ouderdom in seconden (onbetrouwbare RTC van dit nodetype), een groot getal
    is al een epoch. Gedeeld door ``seen`` en ``fall_ts``."""
    from app import companions
    now = 2_000_000_000.0  # ruim na 2001
    assert companions._secs_to_epoch(120, now) == int(now) - 120
    assert companions._secs_to_epoch(1_999_999_000, now) == 1_999_999_000
    assert companions._secs_to_epoch(None, now) is None
    assert companions._secs_to_epoch(-5, now) is None
    assert companions._secs_to_epoch("120", now) is None


def test_poll_locations_werkt_companion_bij_op_pubkey(db, monkeypatch):
    """De hoofdweg: de afzender-node antwoordt met een locatie, en de companion
    met die pubkey wordt bijgewerkt."""
    from app import companions, sensornode
    node = db.get_or_create_repeater("e3d3f4d7edd0", "Dakrepeater")
    db.set_sensor_host(node["id"], "10.0.0.5", by_admin=True)
    db.add_companion("Björn", VALID, "", "", node["id"])

    def nep_json(host, path, timeout=None):
        assert host == "10.0.0.5"
        assert path == "/companions.json"
        return {"ok": True, "error": "", "data": {"companions": [
            {"name": "Björn", "pubkey": VALID, "lat": 51.2, "lon": 5.4, "seen": 42},
        ]}}

    monkeypatch.setattr(sensornode, "_json", nep_json)
    uit = companions.poll_locations()
    assert uit == {"nodes": 1, "updated": 1, "errors": [],
                   "falls": 0, "fall_alerts_sent": 0, "fall_alerts_failed": 0}
    rij = db.companion_by_pubkey(VALID)
    assert rij["last_lat"] == 51.2
    assert rij["last_lon"] == 5.4
    assert rij["last_seen"] is not None


def test_poll_locations_negeert_ongeldige_entries(db, monkeypatch):
    """Geen pubkey, een halve pubkey, of geen positie: overgeslagen en niet een
    halve of foute rij in de databank."""
    from app import companions, sensornode
    node = db.get_or_create_repeater("e3d3f4d7edd0", "Dakrepeater")
    db.set_sensor_host(node["id"], "10.0.0.5", by_admin=True)
    db.add_companion("Björn", VALID, "", "", node["id"])

    def nep_json(host, path, timeout=None):
        return {"ok": True, "error": "", "data": {"companions": [
            {"name": "halve sleutel", "pubkey": "kort", "lat": 1, "lon": 2},
            {"name": "geen positie", "pubkey": VALID},
        ]}}

    monkeypatch.setattr(sensornode, "_json", nep_json)
    uit = companions.poll_locations()
    assert uit["updated"] == 0
    assert db.companion_by_pubkey(VALID)["last_lat"] is None


def test_poll_locations_ene_node_faalt_de_andere_niet(db, monkeypatch):
    """Eén afzender die niet antwoordt mag de ronde voor de andere niet breken --
    dezelfde lijn als sensornode.run_once."""
    from app import companions, sensornode
    stuk = db.get_or_create_repeater("e3d3f4d7edd0", "Stuk")
    werkt = db.get_or_create_repeater("aabbccddeeff", "Werkt")
    db.set_sensor_host(stuk["id"], "10.0.0.5", by_admin=True)
    db.set_sensor_host(werkt["id"], "10.0.0.6", by_admin=True)
    db.add_companion("A", VALID, "", "", stuk["id"])
    db.add_companion("B", "b" * 64, "", "", werkt["id"])

    def nep_json(host, path, timeout=None):
        if host == "10.0.0.5":
            return {"ok": False, "error": "niet bereikbaar", "data": {}}
        return {"ok": True, "error": "", "data": {"companions": [
            {"name": "B", "pubkey": "b" * 64, "lat": 1.0, "lon": 2.0, "seen": 10},
        ]}}

    monkeypatch.setattr(sensornode, "_json", nep_json)
    uit = companions.poll_locations()
    assert uit["nodes"] == 2
    assert uit["updated"] == 1
    assert len(uit["errors"]) == 1
    assert db.companion_by_pubkey("b" * 64)["last_lat"] == 1.0
    assert db.companion_by_pubkey(VALID)["last_lat"] is None


def test_poll_locations_zonder_afzenders_doet_niets(db):
    from app import companions
    assert companions.poll_locations() == {
        "nodes": 0, "updated": 0, "errors": [],
        "falls": 0, "fall_alerts_sent": 0, "fall_alerts_failed": 0}


def test_companion_cmd_bouwt_tune_en_verstuurt(db, monkeypatch):
    """De commando-route bouwt de nieuwe !tune-vorm en verstuurt hem vanaf de
    standaardafzender van de companion. Zonder JSON-Accept (het kale formulier,
    geen JavaScript) is de respons een PRG-redirect terug naar de
    companion-pagina, met de verstuurde body in de querystring."""
    from app import auth, rooms, routes_companions
    gezien = {}
    monkeypatch.setattr(rooms, "bot_sendto",
                        lambda rep, pk, msg, bot=None: gezien.update(pk=pk, msg=msg) or {"ok": True, "error": ""})
    maak_gebruiker("baas", superuser=True)
    cookie = _sessie("baas")
    node = db.get_or_create_repeater("e3d3f4d7edd0", "Dakrepeater")
    cid = db.add_companion("Björn", VALID, "T1000-E", "", node["id"])
    req = verzoek(cookie, method="POST")
    resp = routes_companions.companion_cmd(
        req, cid, cmd="tune", sender="", slot="H", name="coin",
        csrf=auth.csrf_token(cookie))
    assert resp.status_code == 303
    assert resp.headers["location"] == f"/admin/companions/{cid}?r=cmd_ok&b=%21tune+H+preset+coin"
    assert gezien == {"pk": VALID, "msg": "!tune H preset coin"}


def test_companion_cmd_met_json_accept_geeft_json_zonder_redirect(db, monkeypatch):
    """companions.js onderschept de commando-knoppen met fetch en zet
    Accept: application/json -- dan komt er GEEN navigatie, alleen een korte
    bevestiging, want het mesh-antwoord komt toch niet synchroon terug (zie de
    docstring van companion_cmd)."""
    from app import auth, rooms, routes_companions
    monkeypatch.setattr(rooms, "bot_sendto",
                        lambda rep, pk, msg, bot=None: {"ok": True, "error": ""})
    maak_gebruiker("baas", superuser=True)
    cookie = _sessie("baas")
    node = db.get_or_create_repeater("e3d3f4d7edd0", "Dakrepeater")
    cid = db.add_companion("Björn", VALID, "T1000-E", "", node["id"])
    req = verzoek(cookie, method="POST", accept="application/json")
    resp = routes_companions.companion_cmd(
        req, cid, cmd="find", sender="", csrf=auth.csrf_token(cookie))
    assert resp.status_code == 200
    import json
    data = json.loads(resp.body)
    assert data["ok"] is True
    assert "Björn" in data["msg"] and "!find" in data["msg"]
    assert data["body"] == "!find"


def test_senddm_send_langs_afzenderrechten(db, monkeypatch):
    """Versturen is een node-handeling op de afzender: de serverbeheerder mag,
    en het vervoer krijgt de juiste pubkey. Zonder ``back`` redirect dit naar de
    generieke Send-DM-tab, zoals voorheen."""
    from app import auth, rooms, routes_companions
    gezien = {}
    monkeypatch.setattr(rooms, "bot_sendto",
                        lambda rep, pk, msg, bot=None: gezien.update(pk=pk, msg=msg) or {"ok": True, "error": ""})
    maak_gebruiker("baas", superuser=True)
    cookie = _sessie("baas")
    node = db.get_or_create_repeater("e3d3f4d7edd0", "Dakrepeater")
    req = verzoek(cookie, method="POST")
    resp = routes_companions.senddm_send(req, sender=str(node["id"]), pubkey=VALID,
                                         msg="!find", csrf=auth.csrf_token(cookie))
    assert resp.status_code == 303
    assert resp.headers["location"].startswith("/admin/senddm?")
    assert gezien == {"pk": VALID, "msg": "!find"}


def test_senddm_send_met_back_keert_terug_naar_companionpagina(db, monkeypatch):
    """Het 'Vrij bericht'-formulier op de companion-pagina stuurt ``back`` mee
    en komt daar ook op terug -- niet op de generieke Send-DM-tab. Dit was de
    concrete bug: elke inzending van dat formulier stuurde de bezoeker weg van
    de pagina waar hij op stond."""
    from app import auth, rooms, routes_companions
    monkeypatch.setattr(rooms, "bot_sendto", lambda rep, pk, msg, bot=None: {"ok": True, "error": ""})
    maak_gebruiker("baas", superuser=True)
    cookie = _sessie("baas")
    node = db.get_or_create_repeater("e3d3f4d7edd0", "Dakrepeater")
    cid = db.add_companion("Björn", VALID, "T1000-E", "", node["id"])
    req = verzoek(cookie, method="POST")
    resp = routes_companions.senddm_send(
        req, sender=str(node["id"]), pubkey=VALID, msg="hoi",
        back=f"companion:{cid}", csrf=auth.csrf_token(cookie))
    assert resp.status_code == 303
    assert resp.headers["location"].startswith(f"/admin/companions/{cid}?")
    assert "r=cmd_ok" in resp.headers["location"]

    # Een onbekende companion in ``back`` is geen open redirect: terug naar de
    # generieke tab.
    resp2 = routes_companions.senddm_send(
        verzoek(cookie, method="POST"), sender=str(node["id"]), pubkey=VALID,
        msg="hoi", back="companion:999999", csrf=auth.csrf_token(cookie))
    assert resp2.headers["location"].startswith("/admin/senddm?")


def test_senddm_send_met_json_accept_geeft_json_zonder_redirect(db, monkeypatch):
    """Dezelfde fetch-onderschepping als bij companion_cmd, nu voor het
    Send-DM-formulier (senddm.html en het 'Vrij bericht'-formulier delen de
    ``cmd-ajax``-klasse in companions.js)."""
    from app import auth, rooms, routes_companions
    monkeypatch.setattr(rooms, "bot_sendto", lambda rep, pk, msg, bot=None: {"ok": True, "error": ""})
    maak_gebruiker("baas", superuser=True)
    cookie = _sessie("baas")
    node = db.get_or_create_repeater("e3d3f4d7edd0", "Dakrepeater")
    req = verzoek(cookie, method="POST", accept="application/json")
    resp = routes_companions.senddm_send(req, sender=str(node["id"]), pubkey=VALID,
                                         msg="hoi", csrf=auth.csrf_token(cookie))
    assert resp.status_code == 200
    import json
    data = json.loads(resp.body)
    assert data["ok"] is True and "hoi" not in data["msg"]  # inhoud gaat niet mee in de melding


def test_senddm_send_zonder_afzender_meldt_het(db):
    """Geen afzender is geen 500 maar een nette melding, via redirect."""
    from app import auth, routes_companions
    maak_gebruiker("baas", superuser=True)
    cookie = _sessie("baas")
    req = verzoek(cookie, method="POST")
    resp = routes_companions.senddm_send(req, sender="", pubkey=VALID, msg="hoi",
                                         csrf=auth.csrf_token(cookie))
    assert resp.status_code == 303
    assert "r=nosender" in resp.headers["location"]


# --- 5. valalarm-ontvangers: databanklaag en routes --------------------------

def test_companion_alert_crud(db):
    """Toevoegen, lezen, en verwijderen -- geschaald op de EIGEN companion."""
    cid = db.add_companion("Björn", VALID, "", "", None)
    node = db.get_or_create_repeater("e3d3f4d7edd0", "Dakrepeater")
    aid = db.add_companion_alert(cid, "1" * 64, node["id"], "dochter")
    rij = db.companion_alert(aid)
    assert rij["companion_id"] == cid
    assert rij["recipient_pubkey"] == "1" * 64
    assert rij["sender_repeater_id"] == node["id"]
    assert rij["label"] == "dochter"
    assert [r["id"] for r in db.list_companion_alerts(cid)] == [aid]

    # Verwijderen scoped op companion_id: een andere companion mag hem niet
    # raken via een geraden alert-id.
    andere_cid = db.add_companion("Ander", "9" * 64, "", "", None)
    assert db.delete_companion_alert(aid, andere_cid) == 0
    assert db.companion_alert(aid) is not None
    assert db.delete_companion_alert(aid, cid) == 1
    assert db.companion_alert(aid) is None
    assert db.delete_companion_alert(aid, cid) == 0


def test_companion_alert_cascade_bij_verwijderde_companion(db):
    """ON DELETE CASCADE: verdwijnt de companion, dan verdwijnt ook zijn
    ontvangerslijst -- geen wees-rij die naar een niet-bestaande companion wijst."""
    cid = db.add_companion("Björn", VALID, "", "", None)
    aid = db.add_companion_alert(cid, "1" * 64)
    db.delete_companion(cid)
    assert db.companion_alert(aid) is None


def test_companion_alert_sender_set_null_bij_verwijderde_node(db):
    """ON DELETE SET NULL op sender_repeater_id: de ontvanger blijft staan,
    alleen zijn afzender verdwijnt -- dezelfde lijn als companions.sender_repeater_id."""
    cid = db.add_companion("Björn", VALID, "", "", None)
    node = db.get_or_create_repeater("e3d3f4d7edd0", "Dakrepeater")
    aid = db.add_companion_alert(cid, "1" * 64, node["id"])
    db.execute("DELETE FROM repeaters WHERE id=?", (node["id"],))
    assert db.companion_alert(aid)["sender_repeater_id"] is None


def test_set_companion_fall(db):
    cid = db.add_companion("Björn", VALID, "", "", None)
    db.set_companion_fall(cid, 12345, "val")
    rij = db.companion(cid)
    assert rij["last_escalated_fall_ts"] == 12345
    assert rij["last_fall_kind"] == "val"


def test_companion_alert_add_vereist_serverbeheerder(db):
    from app import routes_companions
    maak_gebruiker("gewoon", superuser=False)
    cid = db.add_companion("Björn", VALID, "", "", None)
    req = verzoek(_sessie("gewoon"), method="POST")
    with pytest.raises(HTTPException) as fout:
        routes_companions.companion_alert_add(
            req, cid, recipient="1" * 64, sender="", label="", csrf="x")
    assert fout.value.status_code == 403
    assert db.list_companion_alerts(cid) == []


def test_companion_alert_add_als_serverbeheerder(db):
    from app import auth, routes_companions
    maak_gebruiker("baas", superuser=True)
    cookie = _sessie("baas")
    cid = db.add_companion("Björn", VALID, "", "", None)
    req = verzoek(cookie, method="POST")
    resp = routes_companions.companion_alert_add(
        req, cid, recipient="1" * 64, sender="", label="dochter",
        csrf=auth.csrf_token(cookie))
    assert resp.status_code == 303
    assert resp.headers["location"] == f"/admin/companions/{cid}?r=alert_added"
    rijen = db.list_companion_alerts(cid)
    assert len(rijen) == 1
    assert rijen[0]["recipient_pubkey"] == "1" * 64
    assert rijen[0]["label"] == "dochter"


def test_companion_alert_add_valideert_pubkey(db):
    """Een halve of onzinnige sleutel wordt geweigerd, niet stilzwijgend
    opgeslagen -- dezelfde regel als companion_add."""
    from app import auth, routes_companions
    maak_gebruiker("baas", superuser=True)
    cookie = _sessie("baas")
    cid = db.add_companion("Björn", VALID, "", "", None)
    req = verzoek(cookie, method="POST")
    resp = routes_companions.companion_alert_add(
        req, cid, recipient="kort", sender="", label="",
        csrf=auth.csrf_token(cookie))
    assert resp.status_code == 303
    assert "r=alert_err_pubkey" in resp.headers["location"]
    assert db.list_companion_alerts(cid) == []


def test_companion_alert_delete_als_serverbeheerder(db):
    from app import auth, routes_companions
    maak_gebruiker("baas", superuser=True)
    cookie = _sessie("baas")
    cid = db.add_companion("Björn", VALID, "", "", None)
    aid = db.add_companion_alert(cid, "1" * 64)
    req = verzoek(cookie, method="POST")
    resp = routes_companions.companion_alert_delete(
        req, cid, aid, csrf=auth.csrf_token(cookie))
    assert resp.status_code == 303
    assert resp.headers["location"] == f"/admin/companions/{cid}?r=alert_deleted"
    assert db.companion_alert(aid) is None


def test_companion_alert_delete_vereist_serverbeheerder(db):
    from app import routes_companions
    maak_gebruiker("gewoon", superuser=False)
    cid = db.add_companion("Björn", VALID, "", "", None)
    aid = db.add_companion_alert(cid, "1" * 64)
    req = verzoek(_sessie("gewoon"), method="POST")
    with pytest.raises(HTTPException) as fout:
        routes_companions.companion_alert_delete(req, cid, aid, csrf="x")
    assert fout.value.status_code == 403
    assert db.companion_alert(aid) is not None


def test_companion_pagina_rendert_met_val_en_ontvangers(db):
    """De nieuwe secties (laatste val, ontvangerslijst) door de echte
    Jinja-omgeving -- een sjabloonfout is een lege pagina en geen testfout."""
    from app import routes_companions
    maak_gebruiker("baas", superuser=True)
    cookie = _sessie("baas")
    cid = db.add_companion("Björn", VALID, "T1000-E", "", None)
    db.add_companion_alert(cid, "1" * 64, None, "dochter")
    db.set_companion_location(VALID, 51.2, 5.4, int(time.time()))
    db.set_companion_fall(cid, int(time.time()) - 10, "val")
    resp = routes_companions.companion_page(verzoek(cookie), cid)
    assert resp.status_code == 200


def test_companions_map_pagina_rendert_met_recente_val(db):
    """De kaart met een fall_recent-marker door de echte Jinja-omgeving."""
    from app import routes_companions
    maak_gebruiker("baas", superuser=True)
    cookie = _sessie("baas")
    cid = db.add_companion("Björn", VALID, "T1000-E", "", None)
    db.set_companion_location(VALID, 51.2, 5.4, int(time.time()))
    db.set_companion_fall(cid, int(time.time()) - 10, "val")
    resp = routes_companions.companions_map_page(verzoek(cookie))
    assert resp.status_code == 200


# --- 6. valescalatie: de pollronde --------------------------------------------

def test_poll_locations_escaleert_nieuwe_val_naar_ontvanger(db, monkeypatch):
    """De hoofdweg: een NIEUWE fall_ts levert een DM op naar de toegewezen
    ontvanger, en de val wordt vastgelegd."""
    from app import companions, rooms, sensornode
    node = db.get_or_create_repeater("e3d3f4d7edd0", "Dakrepeater")
    db.set_sensor_host(node["id"], "10.0.0.5", by_admin=True)
    cid = db.add_companion("Björn", VALID, "", "", node["id"])
    db.add_companion_alert(cid, "1" * 64, node["id"])

    gezien = {}

    def nep_bot_sendto(rep, pubkey, msg, bot=None):
        gezien["pubkey"] = pubkey
        gezien["msg"] = msg
        return {"ok": True, "error": ""}

    monkeypatch.setattr(rooms, "bot_sendto", nep_bot_sendto)

    def nep_json(host, path, timeout=None):
        return {"ok": True, "error": "", "data": {"companions": [
            {"name": "Björn", "pubkey": VALID, "lat": 51.2, "lon": 5.4,
             "seen": 5, "fall_ts": 100, "fall_kind": "val"},
        ]}}

    monkeypatch.setattr(sensornode, "_json", nep_json)
    uit = companions.poll_locations()
    assert uit["falls"] == 1
    assert uit["fall_alerts_sent"] == 1
    assert uit["fall_alerts_failed"] == 0
    assert gezien["pubkey"] == "1" * 64
    assert "VAL" in gezien["msg"] and "Björn" in gezien["msg"]
    assert "51.2" in gezien["msg"] and "5.4" in gezien["msg"]
    assert "openstreetmap.org" in gezien["msg"]

    comp = db.companion(cid)
    assert comp["last_escalated_fall_ts"] is not None
    assert comp["last_fall_kind"] == "val"


def test_poll_locations_ontdubbelt_op_fall_ts(db, monkeypatch):
    """Dezelfde val (zelfde fall_ts) mag over twee rondes maar één keer een
    alarm opleveren -- de kern van de escalatie-eis.

    ``fall_ts`` is hier bewust een grote, absolute epoch (na 2001) en geen
    kleine 'ouderdom': bij een ouderdom verandert de omgerekende epoch met de
    kloktijd van elke aanroep mee, en dat zou deze test laten hangen van de
    exacte timing tussen de twee pollrondes in plaats van van de ontdubbeling
    zelf. Zie companions._secs_to_epoch.
    """
    from app import companions, rooms, sensornode
    node = db.get_or_create_repeater("e3d3f4d7edd0", "Dakrepeater")
    db.set_sensor_host(node["id"], "10.0.0.5", by_admin=True)
    cid = db.add_companion("Björn", VALID, "", "", node["id"])
    db.add_companion_alert(cid, "1" * 64, node["id"])

    verstuurd = []
    monkeypatch.setattr(
        rooms, "bot_sendto",
        lambda rep, pk, msg, bot=None: verstuurd.append(msg) or {"ok": True, "error": ""})

    def nep_json(host, path, timeout=None):
        return {"ok": True, "error": "", "data": {"companions": [
            {"name": "Björn", "pubkey": VALID, "lat": 1.0, "lon": 2.0,
             "seen": 5, "fall_ts": 2_000_000_000, "fall_kind": "nomotion"},
        ]}}

    monkeypatch.setattr(sensornode, "_json", nep_json)
    eerste = companions.poll_locations()
    tweede = companions.poll_locations()
    assert eerste["falls"] == 1 and eerste["fall_alerts_sent"] == 1
    assert tweede["falls"] == 0 and tweede["fall_alerts_sent"] == 0
    assert len(verstuurd) == 1


def test_poll_locations_val_zonder_ontvangers_alleen_vastleggen(db, monkeypatch):
    """Geen toegewezen ontvanger: de val wordt gezien en vastgelegd, maar er
    gaat geen DM de deur uit -- letterlijk de eis uit de opdracht."""
    from app import companions, rooms, sensornode
    node = db.get_or_create_repeater("e3d3f4d7edd0", "Dakrepeater")
    db.set_sensor_host(node["id"], "10.0.0.5", by_admin=True)
    cid = db.add_companion("Björn", VALID, "", "", node["id"])
    # Expres geen db.add_companion_alert().

    monkeypatch.setattr(rooms, "bot_sendto",
                        lambda *a, **k: pytest.fail("mocht niet versturen"))

    def nep_json(host, path, timeout=None):
        return {"ok": True, "error": "", "data": {"companions": [
            {"name": "Björn", "pubkey": VALID, "lat": 1.0, "lon": 2.0,
             "seen": 5, "fall_ts": 2_000_000_500, "fall_kind": "sos"},
        ]}}

    monkeypatch.setattr(sensornode, "_json", nep_json)
    uit = companions.poll_locations()
    assert uit["falls"] == 1
    assert uit["fall_alerts_sent"] == 0
    assert uit["fall_alerts_failed"] == 0
    comp = db.companion(cid)
    assert comp["last_escalated_fall_ts"] == 2_000_000_500
    assert comp["last_fall_kind"] == "sos"

    # Een ontvanger die LATER wordt toegevoegd, krijgt deze oude val niet
    # alsnog: dezelfde fall_ts komt gewoon weer binnen bij de volgende ronde.
    db.add_companion_alert(cid, "1" * 64, node["id"])
    uit2 = companions.poll_locations()
    assert uit2["falls"] == 0
    assert uit2["fall_alerts_sent"] == 0


def test_poll_locations_escalatie_faalt_niet_op_één_ontvanger(db, monkeypatch):
    """Twee ontvangers; het versturen naar de ene mislukt, de andere krijgt
    zijn alarm gewoon -- dezelfde lijn als bij een node die niet antwoordt."""
    from app import companions, rooms, sensornode
    node = db.get_or_create_repeater("e3d3f4d7edd0", "Dakrepeater")
    afz1 = db.get_or_create_repeater("aabbccddeeff", "Afzender1")
    afz2 = db.get_or_create_repeater("112233445566", "Afzender2")
    db.set_sensor_host(node["id"], "10.0.0.5", by_admin=True)
    cid = db.add_companion("Björn", VALID, "", "", node["id"])
    r1, r2 = "1" * 64, "2" * 64
    db.add_companion_alert(cid, r1, afz1["id"])
    db.add_companion_alert(cid, r2, afz2["id"])

    gezien = []

    def nep_bot_sendto(rep, pubkey, msg, bot=None):
        gezien.append((rep["id"], pubkey))
        if rep["id"] == afz1["id"]:
            return {"ok": False, "error": "geen antwoord"}
        return {"ok": True, "error": ""}

    monkeypatch.setattr(rooms, "bot_sendto", nep_bot_sendto)

    def nep_json(host, path, timeout=None):
        return {"ok": True, "error": "", "data": {"companions": [
            {"name": "Björn", "pubkey": VALID, "lat": 1.0, "lon": 2.0,
             "seen": 5, "fall_ts": 2_000_000_000, "fall_kind": "val"},
        ]}}

    monkeypatch.setattr(sensornode, "_json", nep_json)
    uit = companions.poll_locations()
    assert uit["falls"] == 1
    assert uit["fall_alerts_sent"] == 1
    assert uit["fall_alerts_failed"] == 1
    # Allebei geprobeerd, en niet gestopt bij de eerste die faalde.
    assert {pk for _, pk in gezien} == {r1, r2}
    comp = db.companion(cid)
    assert comp["last_escalated_fall_ts"] == 2_000_000_000


def test_poll_locations_geen_fall_ts_doet_niets_met_valstaat(db, monkeypatch):
    """Een normale locatiemelding zonder fall_ts/fall_kind raakt de valstaat
    niet -- geen val is geen val, niet een val met een lege naam."""
    from app import companions, sensornode
    node = db.get_or_create_repeater("e3d3f4d7edd0", "Dakrepeater")
    db.set_sensor_host(node["id"], "10.0.0.5", by_admin=True)
    cid = db.add_companion("Björn", VALID, "", "", node["id"])

    def nep_json(host, path, timeout=None):
        return {"ok": True, "error": "", "data": {"companions": [
            {"name": "Björn", "pubkey": VALID, "lat": 1.0, "lon": 2.0, "seen": 5},
        ]}}

    monkeypatch.setattr(sensornode, "_json", nep_json)
    uit = companions.poll_locations()
    assert uit["falls"] == 0
    comp = db.companion(cid)
    assert comp["last_escalated_fall_ts"] is None
    assert comp["last_fall_kind"] is None


# --- 7. bot-selectie: /bots.json, cache, en de voorrangsorde -----------------
#
# Een afzender-node kan meer dan één bot-identiteit hosten (de MGMT-uitbreiding
# van de firmware). Deze sectie dekt de node-kant (rooms.bots), de cache
# (companions.cached_bots) en de voorrangsorde (resolve_bot/default_bot_for) --
# en dat een commando ook echt met de gekozen bot vertrekt.

def test_rooms_bots_normaliseert_en_markeert_de_alarmbot(db, monkeypatch):
    from app import rooms, sensornode
    node = db.get_or_create_repeater("e3d3f4d7edd0", "Dakrepeater")
    db.set_sensor_host(node["id"], "10.0.0.5", by_admin=True)

    def nep_json(host, path, timeout=None):
        assert path == "/bots.json"
        return {"ok": True, "error": "", "data": {
            "alert": 0,
            "bots": [
                {"idx": 0, "name": "BE-HSS-DinX-ALERT", "pub": "a" * 64},
                {"idx": 1, "name": "BE-HSS-DinX-MGMT", "pub": "b" * 64},
                "rommel",              # geen dict: overgeslagen
                {"name": "geen idx"},  # geen bruikbare idx: overgeslagen
            ],
        }}

    monkeypatch.setattr(sensornode, "_json", nep_json)
    uit = rooms.bots(node)
    assert uit["ok"] and uit["alert"] == 0
    assert [b["idx"] for b in uit["bots"]] == [0, 1]
    assert uit["bots"][0]["alert"] is True
    assert uit["bots"][1]["alert"] is False


def test_rooms_bots_degradeert_zonder_fout_op_oudere_firmware(db, monkeypatch):
    """Geen /bots.json (404 of onbereikbaar): lege lijst, geen crash -- de
    aanroeper valt terug op "geen bot=", het gedrag van vóór deze uitbreiding."""
    from app import rooms, sensornode
    node = db.get_or_create_repeater("e3d3f4d7edd0", "Dakrepeater")
    db.set_sensor_host(node["id"], "10.0.0.5", by_admin=True)
    monkeypatch.setattr(sensornode, "_json",
                        lambda host, path, timeout=None: {"ok": False, "error": "404", "data": {}})
    uit = rooms.bots(node)
    assert not uit["ok"]
    assert uit["bots"] == []


def test_rooms_bot_sendto_stuurt_bot_veld_alleen_als_gekozen(db, monkeypatch):
    from app import rooms, sensornode
    node = db.get_or_create_repeater("e3d3f4d7edd0", "Dakrepeater")
    db.set_sensor_host(node["id"], "10.0.0.5", by_admin=True)
    gezien = {}

    def nep_post_form(host, path, fields):
        gezien.update(fields)
        return {"ok": True, "error": "", "data": {}}

    monkeypatch.setattr(sensornode, "post_form", nep_post_form)
    rooms.bot_sendto(node, VALID, "hoi")
    assert "bot" not in gezien
    rooms.bot_sendto(node, VALID, "hoi", bot="BE-HSS-DinX-MGMT")
    assert gezien["bot"] == "BE-HSS-DinX-MGMT"


def test_cached_bots_hergebruikt_binnen_de_levensduur(db, monkeypatch):
    from app import companions, sensornode
    node = db.get_or_create_repeater("e3d3f4d7edd0", "Dakrepeater")
    db.set_sensor_host(node["id"], "10.0.0.5", by_admin=True)
    aanroepen = []

    def nep_json(host, path, timeout=None):
        aanroepen.append(path)
        return {"ok": True, "error": "", "data": {"alert": 0, "bots": [
            {"idx": 0, "name": "ALERT"}]}}

    monkeypatch.setattr(sensornode, "_json", nep_json)
    companions.cached_bots(node)
    companions.cached_bots(node)
    assert len(aanroepen) == 1
    # ``max_age=0`` forceert een verse ophaling.
    companions.cached_bots(node, max_age=0)
    assert len(aanroepen) == 2


def test_default_bot_for_kiest_de_niet_alarmbot(db, monkeypatch):
    from app import companions, sensornode
    node = db.get_or_create_repeater("e3d3f4d7edd0", "Dakrepeater")
    db.set_sensor_host(node["id"], "10.0.0.5", by_admin=True)
    monkeypatch.setattr(sensornode, "_json", lambda host, path, timeout=None: {
        "ok": True, "error": "", "data": {"alert": 0, "bots": [
            {"idx": 0, "name": "BE-HSS-DinX-ALERT"},
            {"idx": 1, "name": "BE-HSS-DinX-MGMT"},
        ]}})
    assert companions.default_bot_for(node) == "BE-HSS-DinX-MGMT"


def test_default_bot_for_valt_terug_op_alarmbot_zonder_alternatief(db, monkeypatch):
    from app import companions, sensornode
    node = db.get_or_create_repeater("e3d3f4d7edd0", "Dakrepeater")
    db.set_sensor_host(node["id"], "10.0.0.5", by_admin=True)
    monkeypatch.setattr(sensornode, "_json", lambda host, path, timeout=None: {
        "ok": True, "error": "", "data": {"alert": 0, "bots": [
            {"idx": 0, "name": "BE-HSS-DinX-ALERT"}]}})
    assert companions.default_bot_for(node) == "BE-HSS-DinX-ALERT"


def test_default_bot_for_none_zonder_bots_json(db, monkeypatch):
    """Oudere firmware zonder /bots.json: geen bot= meesturen, de node gebruikt
    vanzelf zijn ene bot -- exact het gedrag van vóór deze uitbreiding."""
    from app import companions, sensornode
    node = db.get_or_create_repeater("e3d3f4d7edd0", "Dakrepeater")
    db.set_sensor_host(node["id"], "10.0.0.5", by_admin=True)
    monkeypatch.setattr(sensornode, "_json",
                        lambda host, path, timeout=None: {"ok": False, "error": "404", "data": {}})
    assert companions.default_bot_for(node) is None


def test_resolve_bot_volgorde_override_dan_voorkeur_dan_standaard(db, monkeypatch):
    from app import companions, sensornode
    node = db.get_or_create_repeater("e3d3f4d7edd0", "Dakrepeater")
    db.set_sensor_host(node["id"], "10.0.0.5", by_admin=True)
    monkeypatch.setattr(sensornode, "_json", lambda host, path, timeout=None: {
        "ok": True, "error": "", "data": {"alert": 0, "bots": [
            {"idx": 0, "name": "ALERT"}, {"idx": 1, "name": "MGMT"}]}})
    assert companions.resolve_bot(node, "expliciet", "voorkeur") == "expliciet"
    assert companions.resolve_bot(node, "", "voorkeur") == "voorkeur"
    assert companions.resolve_bot(node, "", "") == "MGMT"
    assert companions.resolve_bot(node, None, None) == "MGMT"


def test_companion_cmd_stuurt_de_bewaarde_bot_voorkeur_mee(db, monkeypatch):
    """Zonder expliciete keuze op het formulier wint de bewaarde voorkeur van
    de companion (companions.preferred_bot) op de MGMT-standaard van de node."""
    from app import auth, companions, rooms, routes_companions, sensornode
    monkeypatch.setattr(sensornode, "_json", lambda host, path, timeout=None: {
        "ok": True, "error": "", "data": {"alert": 0, "bots": [
            {"idx": 0, "name": "ALERT"}, {"idx": 1, "name": "MGMT"}]}})
    gezien = {}
    monkeypatch.setattr(
        rooms, "bot_sendto",
        lambda rep, pk, msg, bot=None: gezien.update(bot=bot) or {"ok": True, "error": ""})
    maak_gebruiker("baas", superuser=True)
    cookie = _sessie("baas")
    node = db.get_or_create_repeater("e3d3f4d7edd0", "Dakrepeater")
    cid = db.add_companion("Björn", VALID, "T1000-E", "", node["id"])
    db.set_companion_bot(cid, "eigen-voorkeur")
    # ``bot=""`` expliciet meegeven: dit roept de route rechtstreeks aan (geen
    # TestClient/ASGI), en zonder expliciete waarde blijft een Form-veld het
    # FastAPI-``Form(...)``-sentinelobject in plaats van de lege string die een
    # echt verzoek zou geven -- dezelfde reden waarom elke andere route-test in
    # dit bestand alle relevante formuliervelden expliciet meegeeft.
    req = verzoek(cookie, method="POST")
    routes_companions.companion_cmd(req, cid, cmd="find", sender="", bot="",
                                    csrf=auth.csrf_token(cookie))
    assert gezien["bot"] == "eigen-voorkeur"

    # Een expliciete keuze op het formulier wint alsnog van die voorkeur.
    req2 = verzoek(cookie, method="POST")
    routes_companions.companion_cmd(req2, cid, cmd="find", sender="", bot="ANDERS",
                                    csrf=auth.csrf_token(cookie))
    assert gezien["bot"] == "ANDERS"

    # Geen voorkeur en geen expliciete keuze: de MGMT-standaard van de node.
    db.set_companion_bot(cid, None)
    req3 = verzoek(cookie, method="POST")
    routes_companions.companion_cmd(req3, cid, cmd="find", sender="", bot="",
                                    csrf=auth.csrf_token(cookie))
    assert gezien["bot"] == "MGMT"


def test_companion_bot_set_vereist_serverbeheerder(db):
    from app import routes_companions
    maak_gebruiker("gewoon", superuser=False)
    cid = db.add_companion("Björn", VALID, "", "", None)
    req = verzoek(_sessie("gewoon"), method="POST")
    with pytest.raises(HTTPException) as fout:
        routes_companions.companion_bot_set(req, cid, bot="x", csrf="x")
    assert fout.value.status_code == 403


def test_companion_bot_set_bewaart_en_wist(db):
    from app import auth, routes_companions
    maak_gebruiker("baas", superuser=True)
    cookie = _sessie("baas")
    cid = db.add_companion("Björn", VALID, "", "", None)
    resp = routes_companions.companion_bot_set(
        verzoek(cookie, method="POST"), cid, bot="BE-HSS-DinX-MGMT",
        csrf=auth.csrf_token(cookie))
    assert resp.status_code == 303
    assert "r=bot_saved" in resp.headers["location"]
    assert db.companion(cid)["preferred_bot"] == "BE-HSS-DinX-MGMT"
    # Een lege keuze wist de voorkeur weer.
    routes_companions.companion_bot_set(
        verzoek(cookie, method="POST"), cid, bot="",
        csrf=auth.csrf_token(cookie))
    assert db.companion(cid)["preferred_bot"] is None


def test_companion_bots_json_route(db, monkeypatch):
    from app import companions, routes_companions, sensornode
    monkeypatch.setattr(sensornode, "_json", lambda host, path, timeout=None: {
        "ok": True, "error": "", "data": {"alert": 0, "bots": [
            {"idx": 0, "name": "ALERT"}, {"idx": 1, "name": "MGMT"}]}})
    maak_gebruiker("baas", superuser=True)
    cookie = _sessie("baas")
    node = db.get_or_create_repeater("e3d3f4d7edd0", "Dakrepeater")
    db.set_sensor_host(node["id"], "10.0.0.5", by_admin=True)
    resp = routes_companions.companion_bots(verzoek(cookie), node["id"])
    assert resp.status_code == 200
    import json
    data = json.loads(resp.body)
    assert data["ok"] is True
    assert data["default"] == "MGMT"
    assert [b["name"] for b in data["bots"]] == ["ALERT", "MGMT"]


# --- 8. de instant-push: POST /api/companion ---------------------------------
#
# Dezelfde deur als /api/sensorpush (sensorpush.require_push_token/check_rate)
# en dezelfde escalatie-functie als de pollronde (companions._handle_fall_report)
# -- deze sectie bewaakt dat allebei kloppen, plus het eigen deel: de
# body-vorm, "skip een foute rij, ga door met de rest", en dat een val
# ONMIDDELLIJK escaleert in plaats van te wachten op de volgende pollronde.

class _PushRequest:
    """Het minimum dat companion_push van een Request aanraakt: headers (voor
    het clientadres van de begrenzing) en de JSON-body. Dezelfde soort fake als
    test_sensorpush.py._Request, en om dezelfde reden: geen TestClient nodig
    om het GEDRAG van het endpoint te bewaken."""

    def __init__(self, body=None, ip=None):
        self.headers = {"x-forwarded-for": ip} if ip else {}
        self.client = None
        self._body = body

    async def json(self):
        if self._body is None:
            raise ValueError("geen body")
        return self._body


@pytest.fixture
def push_open(monkeypatch):
    """De push-deur open (token gezet) en het gedeelde sensorpush-geheugen
    (hartslagtabel, herhalingscache, begrenzing) schoon per test."""
    from app import sensorpush
    monkeypatch.setattr(sensorpush, "TOKEN", "geheim")
    sensorpush.reset()
    yield
    sensorpush.reset()


def _push(body, token="Bearer geheim", ip=None):
    import asyncio
    from app import companions
    return asyncio.run(companions.companion_push(_PushRequest(body, ip=ip),
                                                  authorization=token))


def test_companion_push_zonder_token_op_de_server_is_503(db, push_open, monkeypatch):
    from app import sensorpush
    monkeypatch.setattr(sensorpush, "TOKEN", "")
    with pytest.raises(HTTPException) as fout:
        _push({"companions": [{"pubkey": VALID}]})
    assert fout.value.status_code == 503


def test_companion_push_zonder_of_met_fout_token_is_401(db, push_open):
    for token in (None, "geheim", "Bearer verkeerd", "Basic geheim"):
        with pytest.raises(HTTPException) as fout:
            _push({"companions": [{"pubkey": VALID}]}, token=token)
        assert fout.value.status_code == 401


def test_companion_push_reuses_hetzelfde_token_als_sensorpush(db, push_open, monkeypatch):
    """LETTERLIJK dezelfde deur: een token dat sensorpush accepteert, accepteert
    dit endpoint ook, en omgekeerd -- ze delen ÉÉN TOKEN-variabele."""
    from app import sensorpush
    monkeypatch.setattr(sensorpush, "TOKEN", "ander-token")
    with pytest.raises(HTTPException):
        _push({"companions": [{"pubkey": VALID}]}, token="Bearer geheim")
    db.add_companion("Björn", VALID, "", "", None)
    uit = _push({"companions": [{"pubkey": VALID}]}, token="Bearer ander-token")
    assert uit["ok"] is True


def test_companion_push_geen_json_is_400(db, push_open):
    with pytest.raises(HTTPException) as fout:
        _push(None)
    assert fout.value.status_code == 400


@pytest.mark.parametrize("kapot", [
    {},                            # geen "companions"-sleutel
    {"companions": []},            # leeg
    {"companions": "niet-een-lijst"},
    {"companions": [{"pubkey": VALID}] * 65},   # boven MAX_COMPANIONS_PUSH
])
def test_companion_push_vormfouten_op_het_toplevel_zijn_400(db, push_open, kapot):
    from app import companions
    with pytest.raises(HTTPException) as fout:
        _push(kapot)
    assert fout.value.status_code == 400


def test_companion_push_werkt_locatie_bij(db, push_open):
    db.add_companion("Björn", VALID, "T1000-E", "", None)
    uit = _push({"companions": [
        {"pubkey": VALID, "lat": 51.2, "lon": 5.4, "seen": 42},
    ]})
    assert uit == {"ok": True, "updated": 1, "falls": 0, "fall_alerts_sent": 0,
                   "fall_alerts_failed": 0, "skipped": 0}
    rij = db.companion_by_pubkey(VALID)
    assert rij["last_lat"] == 51.2 and rij["last_lon"] == 5.4


def test_companion_push_onbekende_of_halve_pubkey_wordt_geskipt(db, push_open):
    """Een companion die de node kent maar wij niet (nog) beheren, of een halve
    sleutel: overgeslagen, geen fout, en de rest van de push gaat door."""
    db.add_companion("Bekend", VALID, "", "", None)
    uit = _push({"companions": [
        {"pubkey": "f" * 64, "lat": 1.0, "lon": 2.0},   # niet beheerd
        {"pubkey": "kort", "lat": 1.0, "lon": 2.0},     # halve sleutel
        {"pubkey": VALID, "lat": 51.2, "lon": 5.4},     # wel geldig
    ]})
    assert uit["skipped"] == 2
    assert uit["updated"] == 1


def test_companion_push_zonder_lat_lon_werkt_alleen_de_val_bij(db, push_open, monkeypatch):
    """lat/lon mogen ontbreken (contract): dan blijft de locatie ongemoeid maar
    de val wordt wel gezien en verwerkt."""
    from app import rooms
    monkeypatch.setattr(rooms, "bot_sendto", lambda *a, **k: {"ok": True, "error": ""})
    cid = db.add_companion("Björn", VALID, "", "", None)
    uit = _push({"companions": [
        {"pubkey": VALID, "fall_ts": 2_000_000_000, "fall_kind": "sos"},
    ]})
    assert uit["updated"] == 0
    assert uit["falls"] == 1
    comp = db.companion(cid)
    assert comp["last_lat"] is None
    assert comp["last_escalated_fall_ts"] == 2_000_000_000
    assert comp["last_fall_kind"] == "sos"


def test_companion_push_escaleert_meteen_naar_toegewezen_ontvanger(db, push_open, monkeypatch):
    """De kern van de opdracht: een val komt via de push ONMIDDELLIJK aan bij de
    ontvanger, zonder op de 60s-achtergrondronde te wachten."""
    from app import rooms
    node = db.get_or_create_repeater("e3d3f4d7edd0", "Dakrepeater")
    cid = db.add_companion("Björn", VALID, "", "", node["id"])
    db.add_companion_alert(cid, "1" * 64, node["id"])
    gezien = {}
    monkeypatch.setattr(
        rooms, "bot_sendto",
        lambda rep, pk, msg, bot=None: gezien.update(pk=pk, msg=msg) or {"ok": True, "error": ""})

    uit = _push({"companions": [
        {"pubkey": VALID, "lat": 51.2, "lon": 5.4, "seen": 3,
         "fall_ts": 100, "fall_kind": "val"},
    ]})
    assert uit["falls"] == 1
    assert uit["fall_alerts_sent"] == 1
    assert uit["fall_alerts_failed"] == 0
    assert gezien["pk"] == "1" * 64
    assert "VAL" in gezien["msg"] and "Björn" in gezien["msg"]
    comp = db.companion(cid)
    assert comp["last_escalated_fall_ts"] is not None
    assert comp["last_fall_kind"] == "val"


def test_companion_push_fall_ts_nul_is_geen_val(db, push_open, monkeypatch):
    """Het contract: fall_ts van 0 (of afwezig) betekent 'geen val' -- en niet
    'een val van net nu', wat _secs_to_epoch(0, now) zonder deze regel zou
    opleveren (0 leest onder de epoch-grens als een ouderdom van 0 seconden)."""
    from app import rooms
    monkeypatch.setattr(rooms, "bot_sendto",
                        lambda *a, **k: pytest.fail("mocht niet versturen"))
    cid = db.add_companion("Björn", VALID, "", "", None)
    uit = _push({"companions": [{"pubkey": VALID, "fall_ts": 0}]})
    assert uit["falls"] == 0
    assert db.companion(cid)["last_escalated_fall_ts"] is None


def test_companion_push_val_zonder_ontvangers_alleen_vastleggen(db, push_open, monkeypatch):
    from app import rooms
    monkeypatch.setattr(rooms, "bot_sendto",
                        lambda *a, **k: pytest.fail("mocht niet versturen"))
    cid = db.add_companion("Björn", VALID, "", "", None)
    # Een grote, ABSOLUTE epoch (na 2001) en geen kleine 'ouderdom': zie
    # companions._secs_to_epoch -- een klein getal zou hier gelezen worden als
    # "zoveel seconden geleden" en dus als een epoch rond NU, niet als 500.
    uit = _push({"companions": [
        {"pubkey": VALID, "fall_ts": 2_000_000_500, "fall_kind": "nomotion"},
    ]})
    assert uit["falls"] == 1
    assert uit["fall_alerts_sent"] == 0
    comp = db.companion(cid)
    assert comp["last_escalated_fall_ts"] == 2_000_000_500


def test_companion_push_en_pollronde_delen_de_ontdubbeling(db, push_open, monkeypatch):
    """De EIS achter het delen van _handle_fall_report: een val die al via de
    push binnenkwam, escaleert niet nogmaals wanneer de achtergrondronde
    dezelfde fall_ts daarna ook nog ziet op /companions.json -- en omgekeerd."""
    from app import companions, rooms, sensornode
    node = db.get_or_create_repeater("e3d3f4d7edd0", "Dakrepeater")
    db.set_sensor_host(node["id"], "10.0.0.5", by_admin=True)
    cid = db.add_companion("Björn", VALID, "", "", node["id"])
    db.add_companion_alert(cid, "1" * 64, node["id"])
    verstuurd = []
    monkeypatch.setattr(
        rooms, "bot_sendto",
        lambda rep, pk, msg, bot=None: verstuurd.append(msg) or {"ok": True, "error": ""})

    # 1) De push komt eerst binnen en escaleert.
    uit1 = _push({"companions": [
        {"pubkey": VALID, "lat": 1.0, "lon": 2.0, "fall_ts": 2_000_000_000,
         "fall_kind": "val"},
    ]})
    assert uit1["falls"] == 1 and uit1["fall_alerts_sent"] == 1

    # 2) De achtergrondronde ziet dezelfde val op /companions.json: geen tweede
    #    alarm, dezelfde ontdubbeling als hierboven.
    monkeypatch.setattr(sensornode, "_json", lambda host, path, timeout=None: {
        "ok": True, "error": "", "data": {"companions": [
            {"name": "Björn", "pubkey": VALID, "lat": 1.0, "lon": 2.0,
             "fall_ts": 2_000_000_000, "fall_kind": "val"},
        ]}})
    uit2 = companions.poll_locations()
    assert uit2["falls"] == 0
    assert len(verstuurd) == 1

    # 3) En omgekeerd: een tweede push met dezelfde fall_ts escaleert ook niet
    #    nogmaals.
    uit3 = _push({"companions": [
        {"pubkey": VALID, "fall_ts": 2_000_000_000, "fall_kind": "val"},
    ]})
    assert uit3["falls"] == 0
    assert len(verstuurd) == 1


def test_companion_push_rate_limit_429(db, push_open, monkeypatch):
    from app import sensorpush
    monkeypatch.setattr(sensorpush, "RATE_MAX", 3)
    db.add_companion("Björn", VALID, "", "", None)
    for _ in range(3):
        _push({"companions": [{"pubkey": VALID}]}, ip="203.0.113.9")
    with pytest.raises(HTTPException) as fout:
        _push({"companions": [{"pubkey": VALID}]}, ip="203.0.113.9")
    assert fout.value.status_code == 429


# --- 9. locatie-spoor: het logboek onder de laatste positie ------------------
#
# companion_track legt ELK gemeld punt vast (niet alleen de laatste positie in
# companions.last_lat/last_lon), gehaakt in db.set_companion_location zodat zowel
# de poll- als de push-weg er vanzelf een spoorpunt bij aanleggen. Deze sectie
# bewaakt: dat het punt er komt, de ontdubbeling op (companion_id, ts), de
# vensterquery, en het snoeien.

def test_set_companion_location_legt_spoorpunt_aan(db):
    """Elke opgeslagen positie is óók een spoorpunt -- de ene haak die beide
    wegen (poll én push) dekt."""
    cid = db.add_companion("Björn", VALID, "", "", None)
    assert db.set_companion_location(VALID, 51.2, 5.4, 1000)
    punten = db.companion_track_since(cid, 0)
    assert len(punten) == 1
    assert punten[0]["lat"] == 51.2 and punten[0]["lon"] == 5.4
    assert punten[0]["ts"] == 1000
    # Een pubkey die niemand beheert levert geen spoorpunt op -- geen wees-rij.
    assert db.set_companion_location("f" * 64, 0.0, 0.0, 5) is False
    assert db.companion_track_since(cid, 0) == db.companion_track_since(cid, 0)


def test_spoorpunt_ontdubbelt_op_companion_en_ts(db):
    """Exact hetzelfde punt (zelfde companion, zelfde ts) twee keer -- bv. de
    poll en de push die dezelfde melding zien -- levert maar één rij op; een
    ander ts is een nieuw punt."""
    cid = db.add_companion("Björn", VALID, "", "", None)
    db.set_companion_location(VALID, 1.0, 2.0, 1000)
    db.set_companion_location(VALID, 1.0, 2.0, 1000)   # exact duplicaat
    db.set_companion_location(VALID, 1.1, 2.1, 1001)   # ander tijdstip
    assert len(db.companion_track_since(cid, 0)) == 2


def test_companion_track_since_venster_en_volgorde(db):
    """De vensterquery geeft alleen punten vanaf ``since_epoch``, op tijd
    oplopend -- precies wat een polyline nodig heeft."""
    cid = db.add_companion("Björn", VALID, "", "", None)
    db.set_companion_location(VALID, 1.0, 2.0, 100)
    db.set_companion_location(VALID, 1.1, 2.1, 300)
    db.set_companion_location(VALID, 1.2, 2.2, 200)
    ts = [p["ts"] for p in db.companion_track_since(cid, 150)]
    assert ts == [200, 300]


def test_poll_locations_legt_spoor_aan(db, monkeypatch):
    """De achtergrondronde (poll) legt via set_companion_location ook een
    spoorpunt aan."""
    from app import companions, sensornode
    node = db.get_or_create_repeater("e3d3f4d7edd0", "Dakrepeater")
    db.set_sensor_host(node["id"], "10.0.0.5", by_admin=True)
    cid = db.add_companion("Björn", VALID, "", "", node["id"])
    monkeypatch.setattr(sensornode, "_json", lambda host, path, timeout=None: {
        "ok": True, "error": "", "data": {"companions": [
            {"name": "Björn", "pubkey": VALID, "lat": 51.2, "lon": 5.4, "seen": 42},
        ]}})
    companions.poll_locations()
    assert len(db.companion_track_since(cid, 0)) == 1


def test_companion_push_legt_spoor_aan(db, push_open):
    """De instant-push (POST /api/companion) legt via dezelfde haak ook een
    spoorpunt aan."""
    cid = db.add_companion("Björn", VALID, "", "", None)
    _push({"companions": [{"pubkey": VALID, "lat": 1.0, "lon": 2.0, "seen": 7}]})
    assert len(db.companion_track_since(cid, 0)) == 1


def test_prune_companion_track(db):
    """Snoeien gooit alleen punten ouder dan de grens weg."""
    cid = db.add_companion("Björn", VALID, "", "", None)
    db.set_companion_location(VALID, 1.0, 2.0, 100)
    db.set_companion_location(VALID, 1.1, 2.1, 5000)
    weg = db.prune_companion_track(1000)
    assert weg == 1
    ts = [p["ts"] for p in db.companion_track_since(cid, 0)]
    assert ts == [5000]


def test_spoor_verdwijnt_met_de_companion(db):
    """ON DELETE CASCADE: een verwijderde companion neemt zijn spoor mee."""
    cid = db.add_companion("Björn", VALID, "", "", None)
    db.set_companion_location(VALID, 1.0, 2.0, 100)
    db.delete_companion(cid)
    assert db.companion_track_since(cid, 0) == []


def test_track_window_seconds():
    """De vensters staan op één plek en vallen netjes terug op de standaard."""
    from app import companions
    assert companions.track_window_seconds("1h") == 3600
    assert companions.track_window_seconds("7d") == 7 * 24 * 3600
    # Onbekend of leeg -> de standaard (24h).
    assert companions.track_window_seconds("bogus") == 24 * 3600
    assert companions.track_window_seconds("") == 24 * 3600


# --- 10. publieke deel-link: token, /loc/<token> en het spoor-endpoint -------

def test_companion_by_share_token(db):
    """De omgekeerde opzoeking voor /loc/<token>; leeg/onbekend -> None."""
    cid = db.add_companion("Björn", VALID, "", "", None)
    db.set_companion_share_token(cid, "geheimtoken")
    assert db.companion_by_share_token("geheimtoken")["id"] == cid
    assert db.companion_by_share_token("") is None
    assert db.companion_by_share_token("anders") is None
    # Intrekken (None) maakt de link per direct dood.
    db.set_companion_share_token(cid, None)
    assert db.companion_by_share_token("geheimtoken") is None


def test_share_token_aanmaken_en_intrekken_als_serverbeheerder(db):
    """De route maakt een token (redirect met share_on) en trekt het weer in."""
    from app import auth, routes_companions
    maak_gebruiker("baas", superuser=True)
    cookie = _sessie("baas")
    cid = db.add_companion("Björn", VALID, "", "", None)
    resp = routes_companions.companion_share_set(
        verzoek(cookie, method="POST"), cid, action="on",
        csrf=auth.csrf_token(cookie))
    assert resp.status_code == 303
    assert resp.headers["location"] == f"/admin/companions/{cid}?r=share_on"
    token = db.companion(cid)["share_token"]
    assert token
    # Intrekken wist het token.
    resp2 = routes_companions.companion_share_set(
        verzoek(cookie, method="POST"), cid, action="off",
        csrf=auth.csrf_token(cookie))
    assert "r=share_off" in resp2.headers["location"]
    assert db.companion(cid)["share_token"] is None


def test_share_token_vereist_serverbeheerder(db):
    """Een gewone gebruiker mag geen deel-link aanmaken -- serverhandeling."""
    from app import routes_companions
    maak_gebruiker("gewoon", superuser=False)
    cid = db.add_companion("Björn", VALID, "", "", None)
    req = verzoek(_sessie("gewoon"), method="POST")
    with pytest.raises(HTTPException) as fout:
        routes_companions.companion_share_set(req, cid, action="on", csrf="x")
    assert fout.value.status_code == 403
    assert db.companion(cid)["share_token"] is None


def test_loc_pagina_rendert_zonder_login(db):
    """De publieke deel-pagina rendert zonder cookie -- het token is de sleutel."""
    from app import routes_public
    cid = db.add_companion("Björn", VALID, "T1000-E", "", None)
    db.set_companion_share_token(cid, "geheimtoken")
    db.set_companion_location(VALID, 51.2, 5.4, 1000)
    resp = routes_public.companion_share_page(verzoek(), "geheimtoken")
    assert resp.status_code == 200
    assert "Björn" in resp.body.decode("utf-8")


def test_loc_onbekend_token_is_404(db):
    from app import routes_public
    with pytest.raises(HTTPException) as fout:
        routes_public.companion_share_page(verzoek(), "bestaatniet")
    assert fout.value.status_code == 404


def test_loc_track_json_venster_en_404(db):
    """Het publieke spoor-endpoint filtert op venster en geeft 404 bij een
    onbekend token."""
    import json
    from app import routes_public
    cid = db.add_companion("Björn", VALID, "", "", None)
    db.set_companion_share_token(cid, "geheimtoken")
    now = int(time.time())
    db.set_companion_location(VALID, 1.0, 2.0, now - 100)        # binnen 1u
    db.set_companion_location(VALID, 1.1, 2.1, now - 100_000)    # buiten 1u
    resp = routes_public.companion_share_track(verzoek(), "geheimtoken", window="1h")
    data = json.loads(resp.body)
    assert len(data["points"]) == 1
    assert data["window"] == "1h"
    # Zonder venster -> de standaard (24h) en dan valt het oude punt (100 000 s)
    # er nog steeds buiten, maar het verse erbinnen.
    resp2 = routes_public.companion_share_track(verzoek(), "geheimtoken")
    assert len(json.loads(resp2.body)["points"]) == 1
    with pytest.raises(HTTPException) as fout:
        routes_public.companion_share_track(verzoek(), "bestaatniet")
    assert fout.value.status_code == 404


def test_admin_track_json_en_404(db):
    """De beheerkaart-tegenhanger van het spoor-endpoint (achter login)."""
    import json
    from app import routes_companions
    maak_gebruiker("baas", superuser=True)
    cookie = _sessie("baas")
    cid = db.add_companion("Björn", VALID, "", "", None)
    now = int(time.time())
    db.set_companion_location(VALID, 1.0, 2.0, now - 100)
    resp = routes_companions.companion_track_json(verzoek(cookie), cid, window="24h")
    data = json.loads(resp.body)
    assert len(data["points"]) == 1 and data["points"][0][0] == 1.0
    with pytest.raises(HTTPException) as fout:
        routes_companions.companion_track_json(verzoek(cookie), 999999)
    assert fout.value.status_code == 404


def test_companion_pagina_rendert_met_deel_link(db):
    """De companion-detailpagina met een actieve deel-link door de echte
    Jinja-omgeving -- een sjabloonfout is een lege pagina en geen testfout."""
    from app import routes_companions
    maak_gebruiker("baas", superuser=True)
    cookie = _sessie("baas")
    cid = db.add_companion("Björn", VALID, "T1000-E", "", None)
    db.set_companion_share_token(cid, "geheimtoken")
    resp = routes_companions.companion_page(verzoek(cookie), cid)
    assert resp.status_code == 200
    assert "/loc/geheimtoken" in resp.body.decode("utf-8")


# --- 11. batterij: keuring, databank, ingest (poll + push) en weergave -------
#
# De MeshUptime-node meldt per companion een optioneel batterijpercentage
# (``batt``, 0-100). Deze sectie bewaakt: de keuring (_valid_batt), de
# databanklaag (set_companion_batt), dat beide ingestwegen (poll én push) hem
# oppikken, dat een rapport ZONDER batterij een bekende stand niet wist, en dat
# hij op de kaart-JSON (_loc) en de publieke deel-pagina verschijnt.

def test_valid_batt_keurt_bereik_en_soort():
    """0-100 als heel percentage; alles daarbuiten (of geen getal) -> None."""
    from app import companions
    assert companions._valid_batt(0) == 0
    assert companions._valid_batt(100) == 100
    assert companions._valid_batt(73) == 73
    assert companions._valid_batt(73.6) == 73          # naar heel percentage
    assert companions._valid_batt(None) is None
    assert companions._valid_batt(-1) is None
    assert companions._valid_batt(101) is None
    assert companions._valid_batt("80") is None        # geen string
    assert companions._valid_batt(True) is None        # geen bool


def test_set_companion_batt_databanklaag(db):
    """Zetten op de PUBKEY, ongeacht hoofdletters; een onbekende pubkey levert
    stilzwijgend niets op (geen wees-rij), net als set_companion_location."""
    cid = db.add_companion("Björn", VALID, "", "", None)
    assert db.companion(cid)["batt"] is None            # standaard: onbekend
    assert db.set_companion_batt(VALID.upper(), 55) is True
    assert db.companion(cid)["batt"] == 55
    assert db.set_companion_batt("f" * 64, 40) is False
    assert db.set_companion_batt("", 40) is False


def test_poll_locations_werkt_batterij_bij(db, monkeypatch):
    """De achtergrondronde pikt ``batt`` uit /companions.json op."""
    from app import companions, sensornode
    node = db.get_or_create_repeater("e3d3f4d7edd0", "Dakrepeater")
    db.set_sensor_host(node["id"], "10.0.0.5", by_admin=True)
    cid = db.add_companion("Björn", VALID, "", "", node["id"])
    monkeypatch.setattr(sensornode, "_json", lambda host, path, timeout=None: {
        "ok": True, "error": "", "data": {"companions": [
            {"name": "Björn", "pubkey": VALID, "lat": 51.2, "lon": 5.4,
             "seen": 42, "batt": 88}]}})
    companions.poll_locations()
    assert db.companion(cid)["batt"] == 88


def test_poll_locations_batterij_zonder_locatie(db, monkeypatch):
    """Batterij zonder GPS-fix (geen lat/lon): de stand wordt toch bijgewerkt,
    los van de locatie."""
    from app import companions, sensornode
    node = db.get_or_create_repeater("e3d3f4d7edd0", "Dakrepeater")
    db.set_sensor_host(node["id"], "10.0.0.5", by_admin=True)
    cid = db.add_companion("Björn", VALID, "", "", node["id"])
    monkeypatch.setattr(sensornode, "_json", lambda host, path, timeout=None: {
        "ok": True, "error": "", "data": {"companions": [
            {"name": "Björn", "pubkey": VALID, "batt": 47}]}})
    uit = companions.poll_locations()
    assert uit["updated"] == 0                           # geen locatie
    assert db.companion(cid)["batt"] == 47


def test_poll_locations_zonder_batterij_wist_bekende_stand_niet(db, monkeypatch):
    """Een rapport zonder ``batt`` laat een reeds bekende stand staan -- absent
    betekent 'geen nieuws', niet 'wis de batterij'."""
    from app import companions, sensornode
    node = db.get_or_create_repeater("e3d3f4d7edd0", "Dakrepeater")
    db.set_sensor_host(node["id"], "10.0.0.5", by_admin=True)
    cid = db.add_companion("Björn", VALID, "", "", node["id"])
    db.set_companion_batt(VALID, 60)
    monkeypatch.setattr(sensornode, "_json", lambda host, path, timeout=None: {
        "ok": True, "error": "", "data": {"companions": [
            {"name": "Björn", "pubkey": VALID, "lat": 1.0, "lon": 2.0, "seen": 5}]}})
    companions.poll_locations()
    assert db.companion(cid)["batt"] == 60


def test_companion_push_werkt_batterij_bij(db, push_open):
    """De instant-push pikt ``batt`` op, ook zonder locatie."""
    cid = db.add_companion("Björn", VALID, "", "", None)
    _push({"companions": [{"pubkey": VALID, "batt": 91}]})
    assert db.companion(cid)["batt"] == 91


def test_companion_push_zonder_batterij_wist_bekende_stand_niet(db, push_open):
    cid = db.add_companion("Björn", VALID, "", "", None)
    db.set_companion_batt(VALID, 30)
    _push({"companions": [{"pubkey": VALID, "lat": 1.0, "lon": 2.0}]})
    assert db.companion(cid)["batt"] == 30


def test_companion_push_ongeldige_batterij_wordt_genegeerd(db, push_open):
    """Een batterij buiten 0-100 (of geen getal) wordt niet opgeslagen -- de
    keuring uit _valid_batt geldt op de push-weg net zo goed."""
    cid = db.add_companion("Björn", VALID, "", "", None)
    _push({"companions": [{"pubkey": VALID, "batt": 250}]})
    assert db.companion(cid)["batt"] is None


def test_status_json_bevat_batterij(db, monkeypatch):
    """De kaart-/lijst-JSON (_loc) draagt de batterij mee: bekend als getal,
    onbekend als None."""
    import json
    from app import companions, routes_companions
    monkeypatch.setattr(companions, "_ondemand_last_ts", 0.0)
    monkeypatch.setattr(companions, "poll_now", lambda *a, **k: None)
    maak_gebruiker("baas", superuser=True)
    cookie = _sessie("baas")
    db.add_companion("Met", VALID, "", "", None)
    db.add_companion("Zonder", "c" * 64, "", "", None)
    db.set_companion_batt(VALID, 42)
    resp = routes_companions.companions_status_json(verzoek(cookie))
    data = {c["name"]: c for c in json.loads(resp.body)["companions"]}
    assert data["Met"]["batt"] == 42
    assert data["Zonder"]["batt"] is None


def test_loc_pagina_toont_batterij_als_bekend(db):
    """De publieke deel-pagina toont de batterij zodra companions.batt bekend is,
    en laat hem weg als hij onbekend is."""
    from app import routes_public
    cid = db.add_companion("Björn", VALID, "T1000-E", "", None)
    db.set_companion_share_token(cid, "geheimtoken")
    # Zonder bekende stand: geen batterij-chip (&#128267;) op de pagina.
    resp = routes_public.companion_share_page(verzoek(), "geheimtoken")
    assert "&#128267;" not in resp.body.decode("utf-8")
    # Met bekende stand: de chip met het percentage verschijnt.
    db.set_companion_batt(VALID, 77)
    resp2 = routes_public.companion_share_page(verzoek(), "geheimtoken")
    body = resp2.body.decode("utf-8")
    assert "&#128267;" in body and "77%" in body


def test_companion_detailpagina_rendert_met_batterij(db):
    """De detailpagina met een bekende batterij door de echte Jinja-omgeving --
    een sjabloonfout is een lege pagina en geen testfout."""
    from app import routes_companions
    maak_gebruiker("baas", superuser=True)
    cookie = _sessie("baas")
    cid = db.add_companion("Björn", VALID, "T1000-E", "", None)
    db.set_companion_batt(VALID, 64)
    resp = routes_companions.companion_page(verzoek(cookie), cid)
    assert resp.status_code == 200
    assert 'id="live-batt-pct">64<' in resp.body.decode("utf-8")
