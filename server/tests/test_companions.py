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


def verzoek(cookie: str = "", method: str = "GET") -> Request:
    headers = [(b"cookie", f"mm_session={cookie}".encode())] if cookie else []
    return Request({
        "type": "http", "http_version": "1.1", "method": method,
        "scheme": "http", "server": ("test", 80), "path": "/x",
        "query_string": b"", "headers": headers,
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
    """De serverbeheerder voegt toe; de rij staat erna in de databank."""
    from app import auth, routes_companions
    maak_gebruiker("baas", superuser=True)
    cookie = _sessie("baas")
    req = verzoek(cookie, method="POST")
    resp = routes_companions.companion_add(
        req, name="Björn", pubkey=VALID, type="T1000-E", notes="",
        sender="", csrf=auth.csrf_token(cookie))
    assert resp.status_code == 200
    assert db.companion_by_pubkey(VALID) is not None


def test_paginas_renderen(db):
    """companions, companion-detail en senddm door de echte Jinja-omgeving."""
    from app import routes_companions
    maak_gebruiker("baas", superuser=True)
    cookie = _sessie("baas")
    cid = db.add_companion("Björn", VALID, "T1000-E", "", None)
    for resp in (routes_companions.companions_page(verzoek(cookie)),
                 routes_companions.companion_page(verzoek(cookie), cid),
                 routes_companions.senddm_page(verzoek(cookie))):
        assert resp.status_code == 200


def test_companion_cmd_bouwt_tune_en_verstuurt(db, monkeypatch):
    """De commando-route bouwt de nieuwe !tune-vorm en verstuurt hem vanaf de
    standaardafzender van de companion."""
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
    assert resp.status_code == 200
    assert gezien == {"pk": VALID, "msg": "!tune H preset coin"}


def test_senddm_send_langs_afzenderrechten(db, monkeypatch):
    """Versturen is een node-handeling op de afzender: de serverbeheerder mag,
    en het vervoer krijgt de juiste pubkey."""
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
    assert resp.status_code == 200
    assert gezien == {"pk": VALID, "msg": "!find"}


def test_senddm_send_zonder_afzender_meldt_het(db):
    """Geen afzender is geen 500 maar een nette melding."""
    from app import auth, routes_companions
    maak_gebruiker("baas", superuser=True)
    cookie = _sessie("baas")
    req = verzoek(cookie, method="POST")
    resp = routes_companions.senddm_send(req, sender="", pubkey=VALID, msg="hoi",
                                         csrf=auth.csrf_token(cookie))
    assert resp.status_code == 200
