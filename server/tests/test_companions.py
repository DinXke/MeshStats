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
    ("vol", {"level": "3"}, "!vol 3"),
    ("gps", {"mode": "ondemand"}, "!gps ondemand"),
    ("tune", {"sev": "H", "rtttl": "two:d=4,o=5,b=100:c"}, "!tune H two:d=4,o=5,b=100:c"),
    ("quiet", {"range": "22-7"}, "!quiet 22-7"),
    ("quiet", {"range": "off"}, "!quiet off"),
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


@pytest.mark.parametrize("cmd,args", [
    ("mute", {"state": "aan"}),
    ("vol", {"level": "4"}),
    ("gps", {"mode": "sometimes"}),
    ("tune", {"sev": "X", "rtttl": "abc"}),
    ("tune", {"sev": "H", "rtttl": ""}),
    ("quiet", {"range": "avond"}),
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
    out = companions.send_command(rep, VALID, "vol", {"level": "2"})
    assert out["ok"]
    assert out["body"] == "!vol 2"
    assert gezien == {"pubkey": VALID, "msg": "!vol 2"}


def test_send_command_verstuurt_niets_bij_fout(db, monkeypatch):
    """Een fout commando raakt het vervoer niet -- er gaat niets de band op."""
    from app import companions, rooms
    monkeypatch.setattr(rooms, "bot_sendto",
                        lambda *a, **k: pytest.fail("mocht niet versturen"))
    rep = db.get_or_create_repeater("e3d3f4d7edd0", "Dakrepeater")
    out = companions.send_command(rep, VALID, "vol", {"level": "9"})
    assert not out["ok"]
    assert out["body"] == ""


# --- 3. de routes: rechten en renderen ---------------------------------------

def _sessie(naam):
    from app import auth
    return auth.make_session(naam)


def test_companion_toevoegen_vereist_serverbeheerder(db):
    """Een gewone gebruiker mag de lijst niet muteren -- de grens die geen
    gedragstest vangt."""
    from app import routes_admin
    maak_gebruiker("gewoon", superuser=False)
    req = verzoek(_sessie("gewoon"), method="POST")
    with pytest.raises(HTTPException) as fout:
        routes_admin.companion_add(req, name="X", pubkey=VALID, type="", notes="",
                                   sender="", csrf="x")
    assert fout.value.status_code == 403


def test_companion_toevoegen_als_serverbeheerder(db):
    """De serverbeheerder voegt toe; de rij staat erna in de databank."""
    from app import auth, routes_admin
    maak_gebruiker("baas", superuser=True)
    cookie = _sessie("baas")
    req = verzoek(cookie, method="POST")
    resp = routes_admin.companion_add(
        req, name="Björn", pubkey=VALID, type="T1000-E", notes="",
        sender="", csrf=auth.csrf_token(cookie))
    assert resp.status_code == 200
    assert db.companion_by_pubkey(VALID) is not None


def test_paginas_renderen(db):
    """companions, companion-detail en senddm door de echte Jinja-omgeving."""
    from app import routes_admin
    maak_gebruiker("baas", superuser=True)
    cookie = _sessie("baas")
    cid = db.add_companion("Björn", VALID, "T1000-E", "", None)
    for resp in (routes_admin.companions_page(verzoek(cookie)),
                 routes_admin.companion_page(verzoek(cookie), cid),
                 routes_admin.senddm_page(verzoek(cookie))):
        assert resp.status_code == 200


def test_senddm_send_langs_afzenderrechten(db, monkeypatch):
    """Versturen is een node-handeling op de afzender: de serverbeheerder mag,
    en het vervoer krijgt de juiste pubkey."""
    from app import auth, rooms, routes_admin
    gezien = {}
    monkeypatch.setattr(rooms, "bot_sendto",
                        lambda rep, pk, msg: gezien.update(pk=pk, msg=msg) or {"ok": True, "error": ""})
    maak_gebruiker("baas", superuser=True)
    cookie = _sessie("baas")
    node = db.get_or_create_repeater("e3d3f4d7edd0", "Dakrepeater")
    req = verzoek(cookie, method="POST")
    resp = routes_admin.senddm_send(req, sender=str(node["id"]), pubkey=VALID,
                                    msg="!find", csrf=auth.csrf_token(cookie))
    assert resp.status_code == 200
    assert gezien == {"pk": VALID, "msg": "!find"}


def test_senddm_send_zonder_afzender_meldt_het(db):
    """Geen afzender is geen 500 maar een nette melding."""
    from app import auth, routes_admin
    maak_gebruiker("baas", superuser=True)
    cookie = _sessie("baas")
    req = verzoek(cookie, method="POST")
    resp = routes_admin.senddm_send(req, sender="", pubkey=VALID, msg="hoi",
                                    csrf=auth.csrf_token(cookie))
    assert resp.status_code == 200
