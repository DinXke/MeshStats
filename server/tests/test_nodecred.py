"""De eigen weblogin per node: opslag, obfuscatie, terugval, en rotatie.

Wat hier bewaakt wordt, en waarom het het bewaken waard is.

**Eén lek is één node, niet de vloot.** Tot deze laag deelde elke node de
vlootsleutel (MM_FW_NODE_USER/MM_FW_NODE_PASS). De hele reden dat deze code
bestaat is dat een gelekte node zich tot zichzelf beperkt; dus de tests gaan er
vooral over dat de JUISTE credential de deur uit gaat -- de eigen login als die
er is, de vlootsleutel als terugval -- en dat op precies één plek
(firmware.open_node).

**Je sluit jezelf nooit buiten.** Roteren bewaart de nieuwe login PAS nadat de
node hem bevestigd heeft. Faalt de node, dan verandert er niets aan de opslag.
Die volgorde is de halve veiligheid van de rotatie en staat hieronder in meer
dan één test.

**Het geheim lekt niet naar buiten.** Het wachtwoord komt niet terug uit
rotate_cred, niet in het audittrail, en niet in de blob die in de databank staat
(die is geobfusceerd). Ook dat wordt hier vastgelegd.

Er wordt geen socket geopend. De netwerkgrens (``firmware.urllib.request.urlopen``
en ``firmware.open_node``) wordt vervangen, zodat de tests over het gedrag gaan.
"""
import base64
import io
import json
import urllib.error

import pytest

from app import config, firmware, nodecred, sensornode


@pytest.fixture
def db(tmp_path, monkeypatch):
    from app import db as db_module
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "test.sqlite3")
    db_module._conn = None
    yield db_module
    if db_module._conn is not None:
        db_module._conn.close()
        db_module._conn = None


def _node(db, host="192.168.110.160"):
    rep = db.get_or_create_repeater("48d7aade232b", "MeshUptime")
    db.set_sensor_host(rep["id"], host, by_admin=True)
    return db.qone("SELECT * FROM repeaters WHERE id=?", (rep["id"],))


# --- obfuscatie ---------------------------------------------------------------

@pytest.mark.parametrize("plain", ["", "hunter2", "pässwörd-éé", "x" * 200,
                                   "mm-AbC123"])
def test_obfuscatie_is_een_ronde_die_terugkomt(plain):
    """Wat erin gaat komt eruit; anders bewaart de rotatie iets wat de node
    nooit aannam."""
    assert nodecred.deobfuscate(nodecred.obfuscate(plain)) == plain


def test_dezelfde_input_geeft_niet_dezelfde_blob():
    """Een verse nonce per keer, zodat de databank niet verraadt welke nodes
    hetzelfde wachtwoord hebben."""
    assert nodecred.obfuscate("zelfde") != nodecred.obfuscate("zelfde")


def test_de_blob_bevat_het_wachtwoord_niet_leesbaar():
    """Het punt van de obfuscatie: een blik in de databank of een back-up levert
    het wachtwoord niet op."""
    blob = nodecred.obfuscate("geheimpje123")
    assert "geheimpje123" not in blob
    assert b"geheimpje123" not in base64.b64decode(blob)


def test_een_gewijzigde_blob_wordt_geweigerd():
    """De markering vangt een corrupte rij of een blob van een andere
    installatie op, en geeft None in plaats van een onzinwachtwoord."""
    blob = nodecred.obfuscate("geheim")
    raw = bytearray(base64.b64decode(blob))
    raw[-1] ^= 0x01
    assert nodecred.deobfuscate(base64.b64encode(bytes(raw)).decode()) is None


def test_een_blob_van_een_andere_sleutel_opent_niet(monkeypatch):
    """Wie de databank kopieert maar de secret.key niet heeft, komt er niet in.

    (Dat de secret.key meestal naast de databank staat, is de eerlijke grens die
    in de docs staat -- maar het scheiden van de twee doet wat het belooft.)
    """
    blob = nodecred.obfuscate("geheim")
    monkeypatch.setattr(config, "SECRET", b"een-heel-andere-sleutel-0000000000")
    assert nodecred.deobfuscate(blob) is None


@pytest.mark.parametrize("junk", [None, "", "geen-base64!!", "QUJD"])
def test_onleesbare_waarden_geven_none(junk):
    """Geen uitzondering op rommel: None betekent overal 'val terug op de
    vlootsleutel'."""
    assert nodecred.deobfuscate(junk) is None


def test_gegenereerde_login_is_lang_en_willekeurig():
    u1, p1 = nodecred.generate()
    u2, p2 = nodecred.generate()
    assert u1.startswith("mm-") and len(p1) >= 20
    assert (u1, p1) != (u2, p2)


# --- opslag en opzoeking ------------------------------------------------------

def test_opslaan_en_terugvinden_op_sensor_host(db):
    rep = _node(db)
    nodecred.store(rep["id"], "mm-abc", "ww123")
    assert nodecred.for_host("192.168.110.160") == ("mm-abc", "ww123")
    assert nodecred.has_own("192.168.110.160") is True


def test_zonder_opgeslagen_login_is_er_geen(db):
    _node(db)
    assert nodecred.for_host("192.168.110.160") is None
    assert nodecred.has_own("192.168.110.160") is False


def test_lookup_matcht_ook_op_ota_host(db):
    """Dezelfde node, bereikt via zijn ota_host: dezelfde weblogin."""
    rep = db.get_or_create_repeater("48d7aade232b", "MeshUptime")
    db.set_ota_host(rep["id"], "192.168.5.5", by_admin=True)
    nodecred.store(rep["id"], "mm-x", "wwx")
    assert nodecred.for_host("192.168.5.5") == ("mm-x", "wwx")


def test_wissen_valt_terug_op_de_vloot(db):
    rep = _node(db)
    nodecred.store(rep["id"], "mm-abc", "ww123")
    db.clear_node_web_cred(rep["id"])
    assert nodecred.for_host("192.168.110.160") is None


def test_lege_gebruiker_telt_niet_als_login(db):
    """Een half ingevulde rij is geen login: dan is de vlootsleutel het juiste
    antwoord."""
    rep = _node(db)
    db.execute("UPDATE repeaters SET web_user='', web_pass_enc=? WHERE id=?",
               (nodecred.obfuscate("x"), rep["id"]))
    assert nodecred.for_host("192.168.110.160") is None


# --- welke credential gaat de deur uit ---------------------------------------

def _decode_auth(header_value: str) -> tuple[str, str]:
    token = header_value.split(" ", 1)[1]
    user, _, pw = base64.b64decode(token).decode().partition(":")
    return user, pw


def test_auth_header_gebruikt_de_vloot_zonder_eigen_login(db, monkeypatch):
    monkeypatch.setattr(firmware, "NODE_USER", "vlootgebruiker")
    monkeypatch.setattr(firmware, "NODE_PASS", "vlootww")
    _node(db)
    assert _decode_auth(firmware._auth_header("192.168.110.160")["Authorization"]) \
        == ("vlootgebruiker", "vlootww")


def test_auth_header_gebruikt_de_eigen_login_als_die_er_is(db, monkeypatch):
    monkeypatch.setattr(firmware, "NODE_USER", "vlootgebruiker")
    monkeypatch.setattr(firmware, "NODE_PASS", "vlootww")
    rep = _node(db)
    nodecred.store(rep["id"], "mm-eigen", "eigenww")
    assert _decode_auth(firmware._auth_header("192.168.110.160")["Authorization"]) \
        == ("mm-eigen", "eigenww")


class _Resp:
    def __init__(self, body=b"{}"):
        self._body = body

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False


def test_open_node_stuurt_de_eigen_login_mee(db, monkeypatch):
    """De vangnet: niet alleen _auth_header, maar de credential zoals die
    werkelijk op de socket belandt."""
    monkeypatch.setattr(firmware, "NODE_USER", "vloot")
    monkeypatch.setattr(firmware, "NODE_PASS", "vlootww")
    rep = _node(db)
    nodecred.store(rep["id"], "mm-eigen", "eigenww")

    gezien = {}

    def nep_urlopen(req, timeout=None):
        gezien["auth"] = req.headers.get("Authorization")
        return _Resp(b'{"ok":1}')

    monkeypatch.setattr(firmware.urllib.request, "urlopen", nep_urlopen)
    with firmware.open_node("192.168.110.160", "/status.json"):
        pass
    assert _decode_auth(gezien["auth"]) == ("mm-eigen", "eigenww")


# --- rotatie ------------------------------------------------------------------

def _fake_node(monkeypatch, *, reply=b'{"ok":1}', http_error=None):
    """Vervangt de socket onder firmware.open_node en logt wat eruit ging.

    De echte open_node draait er nog omheen -- inclusief _auth_header -- zodat de
    test ook meet WELKE credential meeging, niet alleen dat er iets gebeurde.
    """
    verstuurd = []

    def nep_urlopen(req, timeout=None):
        verstuurd.append({
            "url": req.full_url,
            "auth": req.headers.get("Authorization"),
            "body": req.data.decode() if req.data else "",
        })
        if http_error is not None:
            raise http_error
        return _Resp(reply)

    monkeypatch.setattr(firmware.urllib.request, "urlopen", nep_urlopen)
    return verstuurd


def test_rotatie_bootstrapt_met_de_vloot_en_bewaart_de_nieuwe(db, monkeypatch):
    """De eerste rotatie: aanmelden met de vlootsleutel, dan de eigen login
    bewaren. Vanaf dan gebruikt de node zijn eigen login."""
    monkeypatch.setattr(firmware, "NODE_USER", "vloot")
    monkeypatch.setattr(firmware, "NODE_PASS", "vlootww")
    rep = _node(db)
    verstuurd = _fake_node(monkeypatch)

    uit = sensornode.rotate_cred(rep)
    assert uit["ok"] is True
    assert uit["user"].startswith("mm-")
    # De aanmelding ging met de HUIDIGE (vloot)credential, want er was nog geen
    # eigen login toen het verzoek vertrok.
    assert verstuurd[0]["url"].endswith("/web/cred")
    assert _decode_auth(verstuurd[0]["auth"]) == ("vloot", "vlootww")
    body = json.loads(verstuurd[0]["body"])
    assert body["user"] == uit["user"] and body["pass"]
    # En de nieuwe login is nu opgeslagen, geobfusceerd.
    cred = nodecred.for_host("192.168.110.160")
    assert cred is not None and cred[0] == uit["user"]
    row = db.qone("SELECT web_pass_enc FROM repeaters WHERE id=?", (rep["id"],))
    assert body["pass"] not in (row["web_pass_enc"] or "")


def test_tweede_rotatie_meldt_zich_met_de_eigen_login(db, monkeypatch):
    """Bootstrap is eenmalig: daarna is de HUIDIGE credential de eigen login."""
    monkeypatch.setattr(firmware, "NODE_USER", "vloot")
    monkeypatch.setattr(firmware, "NODE_PASS", "vlootww")
    rep = _node(db)
    nodecred.store(rep["id"], "mm-oud", "oudww")
    verstuurd = _fake_node(monkeypatch)

    sensornode.rotate_cred(db.qone("SELECT * FROM repeaters WHERE id=?", (rep["id"],)))
    assert _decode_auth(verstuurd[0]["auth"]) == ("mm-oud", "oudww")


def test_mislukte_rotatie_laat_de_opslag_ongemoeid(db, monkeypatch):
    """De kern: faalt de node, dan sluit je jezelf niet buiten."""
    monkeypatch.setattr(firmware, "NODE_USER", "vloot")
    monkeypatch.setattr(firmware, "NODE_PASS", "vlootww")
    rep = _node(db)
    nodecred.store(rep["id"], "mm-oud", "oudww")
    _fake_node(monkeypatch, reply=b'{"ok":0}')

    uit = sensornode.rotate_cred(db.qone("SELECT * FROM repeaters WHERE id=?",
                                         (rep["id"],)))
    assert uit["ok"] is False
    # Nog steeds de oude login -- niets veranderd.
    assert nodecred.for_host("192.168.110.160") == ("mm-oud", "oudww")


def test_rotatie_bewaart_niets_bij_een_http_fout(db, monkeypatch):
    monkeypatch.setattr(firmware, "NODE_USER", "vloot")
    monkeypatch.setattr(firmware, "NODE_PASS", "vlootww")
    rep = _node(db)
    fout = urllib.error.HTTPError("http://x/web/cred", 401, "nee", {},
                                  io.BytesIO(b"aanmelden geweigerd"))
    _fake_node(monkeypatch, http_error=fout)

    uit = sensornode.rotate_cred(rep)
    assert uit["ok"] is False
    assert nodecred.for_host("192.168.110.160") is None


def test_rotatie_weigert_een_adres_dat_geen_serverbeheerder_zette(db, monkeypatch):
    """De doelcontrole staat ervoor: een adres met een gedelegeerd recht krijgt
    geen verbinding en dus ook geen rotatie."""
    monkeypatch.setattr(firmware, "NODE_USER", "vloot")
    monkeypatch.setattr(firmware, "NODE_PASS", "vlootww")
    rep = db.get_or_create_repeater("48d7aade232b", "MeshUptime")
    db.set_sensor_host(rep["id"], "192.168.110.160", by_admin=False)
    _fake_node(monkeypatch)
    uit = sensornode.rotate_cred(
        db.qone("SELECT * FROM repeaters WHERE id=?", (rep["id"],)))
    assert uit["ok"] is False
    assert nodecred.for_host("192.168.110.160") is None


def test_rotatie_zonder_adres_doet_niets(db):
    rep = db.get_or_create_repeater("48d7aade232b", "MeshUptime")
    uit = sensornode.rotate_cred(
        db.qone("SELECT * FROM repeaters WHERE id=?", (rep["id"],)))
    assert uit["ok"] is False
    assert "adres" in uit["error"]


def test_rotatie_zonder_enige_credential_meldt_dat(db, monkeypatch):
    """Geen vlootsleutel en nog geen eigen login: er is niets om de wijziging
    mee aan te melden."""
    monkeypatch.setattr(firmware, "NODE_USER", "")
    monkeypatch.setattr(firmware, "NODE_PASS", "")
    rep = _node(db)
    uit = sensornode.rotate_cred(rep)
    assert uit["ok"] is False
    assert "MM_FW_NODE_USER" in uit["error"]


# --- de route en de pagina ----------------------------------------------------

class _Req:
    def __init__(self, user):
        from app import auth
        self.cookies = {auth.SESSION_COOKIE: auth.make_session(user)}
        self.query_params: dict = {}


def _user(db, naam, *, superuser):
    from app import rbac
    rbac.maak_gebruiker(naam, "x", is_superuser=superuser, door="test")


def test_de_rotatieknop_is_alleen_voor_een_serverbeheerder(db, monkeypatch):
    """Roteren praat langs de sleutel die elke node opent; dat is geen
    gedelegeerd recht. Een gewone gebruiker krijgt 403."""
    from fastapi import HTTPException
    from app import auth, routes_admin

    monkeypatch.setattr(firmware, "NODE_USER", "vloot")
    rep = _node(db)
    _user(db, "gewoon", superuser=False)
    # require_server_admin staat vóór require_perm in de route, dus dit struikelt
    # op de serverbeheergrens ongeacht node-rechten -- precies de bedoeling.
    with pytest.raises(HTTPException) as fout:
        routes_admin.sensor_rotate_cred(
            _Req("gewoon"), rep["id"], csrf=auth.csrf_token(
                _Req("gewoon").cookies[auth.SESSION_COOKIE]))
    assert fout.value.status_code == 403


def test_de_pagina_waarschuwt_voor_de_gedeelde_sleutel(db, monkeypatch):
    """Een node op de vlootsleutel hoort een waarschuwing te tonen, net als een
    leeg mesh-wachtwoord."""
    from app import mqtt_ingest, routes_admin

    monkeypatch.setattr(firmware, "NODE_USER", "vloot")
    monkeypatch.setattr(mqtt_ingest, "can_publish", lambda: False)
    monkeypatch.setattr(sensornode, "acl", lambda host, timeout=None: {
        "ok": False, "error": "", "data": {}})
    rep = _node(db)
    _user(db, "baas", superuser=True)

    body = routes_admin.node_page(_Req("baas"), rep["id"]).body.decode("utf-8")
    assert "deelt de vlootsleutel" in body
    # De knop staat er, en voor een serverbeheerder niet uitgeschakeld.
    assert "rotate-cred" in body


def test_de_pagina_toont_een_eigen_login_zonder_waarschuwing(db, monkeypatch):
    from app import mqtt_ingest, routes_admin

    monkeypatch.setattr(firmware, "NODE_USER", "vloot")
    monkeypatch.setattr(mqtt_ingest, "can_publish", lambda: False)
    monkeypatch.setattr(sensornode, "acl", lambda host, timeout=None: {
        "ok": False, "error": "", "data": {}})
    rep = _node(db)
    nodecred.store(rep["id"], "mm-eigen", "eigenww")
    _user(db, "baas", superuser=True)

    body = routes_admin.node_page(_Req("baas"), rep["id"]).body.decode("utf-8")
    assert "eigen weblogin" in body
    assert "mm-eigen" in body
    assert "deelt de vlootsleutel" not in body
