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
    from app import db as db_module
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "test.sqlite3")
    db_module._conn = None
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
    ("mute", {"state": "on"}, "!mute on"),
    ("mute", {"state": "off"}, "!mute off"),
    ("vol", {"slot": "H", "level": "3"}, "!vol H 3"),
    ("vol", {"slot": "msg", "level": "0"}, "!vol msg 0"),
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
    ("fall", {"state": "on"}, "!fall on"),
    ("preset", {"slot": "2", "text": "onderweg"}, "!preset 2 onderweg"),
    ("allow", {"sub": "list"}, "!allow list"),
    ("allow", {"sub": "add", "value": VALID}, "!allow add " + VALID),
    ("allow", {"sub": "del", "value": "abcdef"}, "!allow del abcdef"),
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

    def nep_bot_sendto(rep, pubkey, msg):
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
                        lambda rep, pk, msg: gezien.update(pk=pk, msg=msg) or {"ok": True, "error": ""})
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
                        lambda rep, pk, msg: {"ok": True, "error": ""})
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
                        lambda rep, pk, msg: gezien.update(pk=pk, msg=msg) or {"ok": True, "error": ""})
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
    monkeypatch.setattr(rooms, "bot_sendto", lambda rep, pk, msg: {"ok": True, "error": ""})
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
    monkeypatch.setattr(rooms, "bot_sendto", lambda rep, pk, msg: {"ok": True, "error": ""})
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

    def nep_bot_sendto(rep, pubkey, msg):
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
        lambda rep, pk, msg: verstuurd.append(msg) or {"ok": True, "error": ""})

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

    def nep_bot_sendto(rep, pubkey, msg):
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
