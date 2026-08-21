"""Het room-beheer van een room-server-node.

Wat hier bewaakt wordt:

* **Eén netwerkgrens.** De roomfuncties openen zelf geen socket; ze gaan langs
  ``sensornode`` (``_json`` om te lezen, ``post_form``/``post_json`` om te
  schrijven), en die gaan weer langs ``firmware.open_node`` -- de doelcontrole en
  de vlootsleutel/per-node-login. De tests vervangen die grens en wegen op de
  bytes die eruit gaan, niet op een node die toevallig aanstaat.
* **Een gedeeltelijke wijziging is gedeeltelijk.** ``/room/edit`` mag een leeg
  veld niet als "wis dit" lezen; wat None is, gaat niet mee.
* **Gevoelige sleutels.** Een backup bevat de privésleutels van de rooms. Hij
  gaat geobfusceerd de bewaarplaats in -- niet leesbaar in een databankdump -- en
  opent niet meer met een andere ``secret.key``.
* **Eén toestel, meerdere rooms.** /rooms.json is de bron voor de koppeling
  room-pubkey -> node, zodat losse room-entries op het mesh aan hun eigenaar
  hangen in plaats van als anonieme unmanaged node rond te zweven.
* **De adapterlaag.** De setter voor de alarmroute staat achter één functie met
  configureerbare paden, zodat een afwijkend contract triviaal aan te passen is.
"""
import json

import pytest

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


class _Antwoord:
    def __init__(self, body: bytes):
        self._body = body

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False


ROOMS_JSON = {
    "max": 4, "active": 2,
    "rooms": [
        {"idx": 0, "name": "Storingen", "stealth": False, "guest": True,
         "posts": 12, "pub": "48d7aade232b" + "00" * 10, "uri": "meshcore://join/0",
         "kind": "room", "acl": [{"pub": "dd" * 32, "level": 2}]},
        {"idx": 1, "name": "Alarm", "stealth": True, "guest": False,
         "posts": 3, "pub": "aa" * 16, "uri": "meshcore://join/1", "kind": "room",
         "acl": []},
    ],
    "snode_max": 2, "snode_active": 1,
    "snodes": [
        {"idx": 0, "name": "Weerstation", "stealth": False, "pub": "cc" * 32,
         "uri": "meshcore://contact/add?name=Weerstation&public_key="
                + "cc" * 32 + "&type=4",
         "kind": "sensor", "channels": [1, 5],
         "acl": [{"pub": "ee" * 32, "level": 1}]},
    ],
}

STATUS_MET_MON = {
    "fw": "1.4.0", "wifi": "verbonden",
    "mon": [
        {"ch": 5, "n": "google", "h": "google.com", "st": "op",
         "am": 2, "rm": 0b10, "sn": 0b01},
        {"ch": 1, "n": "batterij", "h": "spanning", "st": "4.1 V",
         "am": 1, "rm": 0b11, "sn": 0},
        {"ch": 4, "n": "wifi", "h": "deze node", "st": "online",
         "am": 3, "rm": 0b01, "sn": 0},
    ],
}

BOT_JSON = {
    "active": True, "name": "MeldBot", "pub": "bb" * 32,
    "uri": "meshcore://contact/add?name=MeldBot&public_key=" + "bb" * 32 + "&type=4",
    "max": 8, "recips": [{"k": "ff" * 32, "l": 1}],
}

CONTACTS_JSON = {
    "max": 100,
    "contacts": [
        {"k": "ab" * 32, "n": "DinX-Home", "t": 1, "h": 1, "s": "8.5", "a": 60, "c": 3},
        {"k": "short", "n": "kapot"},   # halve sleutel -> moet weggefilterd worden
    ],
}


def _rep(db, host="192.168.110.160", seen="2026-08-20T10:00:00Z"):
    rep = db.get_or_create_repeater("48d7aade232b", "MeshUptime")
    db.execute("UPDATE repeaters SET is_public=1 WHERE id=?", (rep["id"],))
    db.set_sensor_host(rep["id"], host, by_admin=True)
    if seen:
        db.execute("UPDATE repeaters SET sensor_seen=?, sensor_fw=? WHERE id=?",
                   (seen, "1.4.0", rep["id"]))
    return db.qone("SELECT * FROM repeaters WHERE id=?", (rep["id"],))


# --- lezen en beheren ---------------------------------------------------------

def test_list_rooms_parseert_de_velden(db, monkeypatch):
    from app import rooms, sensornode
    monkeypatch.setattr(sensornode, "_json",
                        lambda host, path, t=None: {"ok": True, "error": "", "data": ROOMS_JSON})
    uit = rooms.list_rooms(_rep(db))
    assert uit["ok"] and uit["max"] == 4 and uit["active"] == 2
    assert uit["rooms"][0]["name"] == "Storingen" and uit["rooms"][0]["guest"] is True
    assert uit["rooms"][1]["stealth"] is True


def test_list_rooms_geeft_de_reden_bij_een_node_zonder_room_api(db, monkeypatch):
    from app import rooms, sensornode
    monkeypatch.setattr(sensornode, "_json",
                        lambda host, path, t=None: {"ok": False, "error": "kent /rooms.json niet", "data": {}})
    uit = rooms.list_rooms(_rep(db))
    assert not uit["ok"] and "rooms.json" in uit["error"]


def test_add_room_stuurt_de_naam(db, monkeypatch):
    from app import rooms, sensornode
    gezien = {}

    def nep(host, path, fields, t=None):
        gezien["path"], gezien["fields"] = path, fields
        return {"ok": True, "error": "", "data": {"idx": 2, "uri": "meshcore://join/2"}}

    monkeypatch.setattr(sensornode, "post_form", nep)
    uit = rooms.add_room(_rep(db), "Nieuw")
    assert uit["ok"] and uit["idx"] == 2 and uit["uri"].endswith("/2")
    assert gezien["path"] == "/room/add" and gezien["fields"] == {"name": "Nieuw"}


def test_add_room_zonder_naam_gaat_niet_de_deur_uit(db, monkeypatch):
    from app import rooms, sensornode
    monkeypatch.setattr(sensornode, "post_form",
                        lambda *a, **k: pytest.fail("er had niets verstuurd mogen worden"))
    uit = rooms.add_room(_rep(db), "   ")
    assert not uit["ok"] and "naam" in uit["error"]


def test_edit_room_laat_onopgegeven_velden_weg(db, monkeypatch):
    from app import rooms, sensornode
    gezien = {}
    monkeypatch.setattr(sensornode, "post_form",
                        lambda host, path, fields, t=None: gezien.update(fields=fields)
                        or {"ok": True, "error": "", "data": {}})
    rooms.edit_room(_rep(db), 1, name="X", stealth=False)
    # idx, name en stealth gaan mee; pass en guest niet (die waren None).
    assert gezien["fields"] == {"idx": 1, "name": "X", "stealth": "0"}


def test_edit_room_zet_een_gastwachtwoord(db, monkeypatch):
    from app import rooms, sensornode
    gezien = {}
    monkeypatch.setattr(sensornode, "post_form",
                        lambda host, path, fields, t=None: gezien.update(fields=fields)
                        or {"ok": True, "error": "", "data": {}})
    rooms.edit_room(_rep(db), 1, guest="hunter2")
    assert gezien["fields"] == {"idx": 1, "guest": "hunter2"}


def test_edit_room_wist_het_gastwachtwoord_alleen_expliciet(db, monkeypatch):
    """Een leeg gastveld laat het gastwachtwoord staan; wissen gaat via
    guest_clear, en dan gaat er GEEN guest mee (anders weet de node niet of het om
    zetten of wissen gaat). Zo veegt een deelbewerking het gastwachtwoord niet weg.
    """
    from app import rooms, sensornode
    gezien = {}
    monkeypatch.setattr(sensornode, "post_form",
                        lambda host, path, fields, t=None: gezien.update(fields=fields)
                        or {"ok": True, "error": "", "data": {}})
    # Alleen de naam wijzigen, gastveld leeg -> guest gaat NIET mee.
    rooms.edit_room(_rep(db), 1, name="X", guest=None)
    assert "guest" not in gezien["fields"] and "guest_clear" not in gezien["fields"]
    # Expliciet wissen -> guest_clear=1, en geen guest ernaast.
    rooms.edit_room(_rep(db), 1, guest="genegeerd", guest_clear=True)
    assert gezien["fields"] == {"idx": 1, "guest_clear": "1"}


def test_set_alarm_zet_am_en_rm_via_mon_alarm(db, monkeypatch):
    from app import rooms, sensornode
    gezien = {}
    monkeypatch.setattr(sensornode, "post_form",
                        lambda host, path, fields, t=None: gezien.update(path=path, fields=fields)
                        or {"ok": True, "error": "", "data": {"ok": True, "ch": 5, "am": 3, "rm": 5}})
    # ch=5 is het kanaalnummer (mon[].ch), niet de positie; am=3, rm=5 (bitmasker).
    uit = rooms.set_alarm(_rep(db), 5, am=3, rm=5)
    assert uit["ok"]
    assert gezien["path"] == rooms.MON_ALARM_PATH
    assert gezien["fields"] == {rooms.MON_ALARM_CH: 5,
                                rooms.MON_ALARM_AM: 3, rooms.MON_ALARM_RM: 5}


def test_set_alarm_geeft_de_fout_van_de_node_door(db, monkeypatch):
    """Een 4xx/500 van /mon/alarm is geen succes; de tekst komt door post_form."""
    from app import rooms, sensornode
    monkeypatch.setattr(sensornode, "post_form",
                        lambda host, path, fields, t=None: {"ok": False, "error": "onbekend kanaal", "data": {}})
    uit = rooms.set_alarm(_rep(db), 9, am=2, rm=1)
    assert not uit["ok"] and uit["error"] == "onbekend kanaal"


def test_set_alarm_weigert_een_onbekende_modus(db, monkeypatch):
    from app import rooms, sensornode
    monkeypatch.setattr(sensornode, "post_form",
                        lambda *a, **k: pytest.fail("mocht niet versturen"))
    uit = rooms.set_alarm(_rep(db), 5, am=9)
    assert not uit["ok"] and "dm/room/both" in uit["error"]


def test_set_alarm_stuurt_ook_het_sensor_nodes_masker(db, monkeypatch):
    from app import rooms, sensornode
    gezien = {}
    monkeypatch.setattr(sensornode, "post_form",
                        lambda host, path, fields, t=None: gezien.update(fields=fields)
                        or {"ok": True, "error": "", "data": {}})
    # am/rm/sn samen; sn=5 (sensor-nodes 0 en 2).
    rooms.set_alarm(_rep(db), 5, am=3, rm=1, sn=5)
    assert gezien["fields"] == {rooms.MON_ALARM_CH: 5, rooms.MON_ALARM_AM: 3,
                                rooms.MON_ALARM_RM: 1, rooms.MON_ALARM_SN: 5}


# --- virtuele sensor-nodes ----------------------------------------------------

def test_list_snodes_parseert_uit_dezelfde_call(db, monkeypatch):
    from app import rooms, sensornode
    monkeypatch.setattr(sensornode, "_json",
                        lambda host, path, t=None: {"ok": True, "error": "", "data": ROOMS_JSON})
    uit = rooms.list_snodes(_rep(db))
    assert uit["ok"] and uit["max"] == 2 and uit["active"] == 1
    assert uit["snodes"][0]["name"] == "Weerstation"
    assert uit["snodes"][0]["channels"] == [1, 5]


def test_overview_geeft_rooms_en_snodes_uit_een_call(db, monkeypatch):
    from app import rooms, sensornode
    calls = {"n": 0}

    def nep(host, path, t=None):
        calls["n"] += 1
        return {"ok": True, "error": "", "data": ROOMS_JSON}

    monkeypatch.setattr(sensornode, "_json", nep)
    ov = rooms.overview(_rep(db))
    assert ov["ok"]
    assert len(ov["rooms"]["rooms"]) == 2 and len(ov["snodes"]["snodes"]) == 1
    assert calls["n"] == 1     # één /rooms.json-call voor allebei


def test_add_snode_stuurt_de_naam(db, monkeypatch):
    from app import rooms, sensornode
    gezien = {}

    def nep(host, path, fields, t=None):
        gezien.update(path=path, fields=fields)
        return {"ok": True, "error": "", "data": {"idx": 1, "uri": "meshcore://contact/add?x"}}

    monkeypatch.setattr(sensornode, "post_form", nep)
    uit = rooms.add_snode(_rep(db), "Weerstation")
    assert uit["ok"] and uit["idx"] == 1
    assert gezien["path"] == "/snode/add" and gezien["fields"] == {"name": "Weerstation"}


def test_edit_snode_laat_lege_naam_weg(db, monkeypatch):
    from app import rooms, sensornode
    gezien = {}
    monkeypatch.setattr(sensornode, "post_form",
                        lambda host, path, fields, t=None: gezien.update(path=path, fields=fields)
                        or {"ok": True, "error": "", "data": {}})
    rooms.edit_snode(_rep(db), 1, stealth=True)
    assert gezien["path"] == "/snode/edit"
    assert gezien["fields"] == {"idx": 1, "stealth": "1"}   # naam niet meegestuurd


def test_del_snode_stuurt_de_index(db, monkeypatch):
    from app import rooms, sensornode
    gezien = {}
    monkeypatch.setattr(sensornode, "post_form",
                        lambda host, path, fields, t=None: gezien.update(path=path, fields=fields)
                        or {"ok": True, "error": "", "data": {}})
    rooms.del_snode(_rep(db), 1)
    assert gezien["path"] == "/snode/del" and gezien["fields"] == {"idx": 1}


# --- node-centrisch kanaalbeheer + advert -------------------------------------

_STATUS_KANALEN = {
    "fw": "1.4.0",
    "mon": [
        {"ch": 5, "n": "google", "rm": 0b10, "sn": 0b00},
        {"ch": 1, "n": "batterij", "rm": 0b01, "sn": 0b01},
        {"ch": 4, "n": "wifi", "rm": 0b00, "sn": 0b00},
    ],
}


def test_apply_room_channels_zet_alleen_de_eigen_rm_bit(db, monkeypatch):
    """Room 0 (bit 0): kanalen 5 en 4 aan, kanaal 1 uit. Alleen rm verandert, en
    alleen op de kanalen die echt wisselen -- am en sn blijven ongemoeid."""
    from app import rooms, sensornode
    monkeypatch.setattr(sensornode, "status",
                        lambda host, t=None: {"ok": True, "error": "", "data": _STATUS_KANALEN})
    calls = []
    monkeypatch.setattr(rooms, "set_alarm",
                        lambda rep, ch, am=None, rm=None, sn=None:
                        calls.append((ch, am, rm, sn)) or {"ok": True, "error": "", "data": {}})
    uit = rooms.apply_room_channels(_rep(db), 0, {5, 4})
    assert uit["ok"] and uit["changed"] == 3
    # ch5: 0b10 -> 0b11; ch1: 0b01 -> 0b00; ch4: 0b00 -> 0b01. Alleen rm meegegeven.
    assert (5, None, 0b11, None) in calls
    assert (1, None, 0b00, None) in calls
    assert (4, None, 0b01, None) in calls


def test_apply_snode_channels_zet_alleen_de_sn_bit(db, monkeypatch):
    from app import rooms, sensornode
    monkeypatch.setattr(sensornode, "status",
                        lambda host, t=None: {"ok": True, "error": "", "data": _STATUS_KANALEN})
    calls = []
    monkeypatch.setattr(rooms, "set_alarm",
                        lambda rep, ch, am=None, rm=None, sn=None:
                        calls.append((ch, rm, sn)) or {"ok": True, "error": "", "data": {}})
    # sensor-node 0 (bit 0): alleen kanaal 5 aan. ch1 heeft sn-bit 0 al -> uit.
    uit = rooms.apply_snode_channels(_rep(db), 0, {5})
    assert uit["ok"]
    assert (5, None, 0b01) in calls          # ch5 sn 0 -> 1
    assert (1, None, 0b00) in calls          # ch1 sn 1 -> 0
    # ch4 stond al op 0 en is niet gevraagd: niet aangeraakt.
    assert all(c[0] != 4 for c in calls)


def test_apply_channels_laat_ongewijzigde_kanalen_met_rust(db, monkeypatch):
    from app import rooms, sensornode
    monkeypatch.setattr(sensornode, "status",
                        lambda host, t=None: {"ok": True, "error": "", "data": _STATUS_KANALEN})
    calls = []
    monkeypatch.setattr(rooms, "set_alarm",
                        lambda rep, ch, **k: calls.append(ch) or {"ok": True, "error": "", "data": {}})
    # Room 1 (bit 1) exact zoals het al staat: alleen ch5 heeft bit1. Niets wijzigt.
    uit = rooms.apply_room_channels(_rep(db), 1, {5})
    assert uit["ok"] and uit["changed"] == 0 and calls == []


def test_add_monitor_plukt_het_kanaalnummer_uit_de_tekst(db, monkeypatch):
    """POST /monitor antwoordt met platte tekst 'ok <naam> -> kanaal <N>'."""
    from app import rooms, sensornode
    gezien = {}
    monkeypatch.setattr(sensornode, "post_text",
                        lambda host, path, fields, t=None: gezien.update(path=path, fields=fields)
                        or {"ok": True, "error": "", "text": "ok Nieuw -> kanaal 7"})
    uit = rooms.add_monitor(_rep(db), "Nieuw", host="1.1.1.1", interval=60)
    assert uit["ok"] and uit["ch"] == 7
    assert gezien["path"] == rooms.MONITOR_ADD_PATH
    # Formveld voor het interval heet 'int', geen 'kind' meegestuurd.
    assert gezien["fields"] == {"name": "Nieuw", "host": "1.1.1.1", "int": 60}


def test_add_monitor_vangt_een_onverwacht_antwoord_af(db, monkeypatch):
    from app import rooms, sensornode
    monkeypatch.setattr(sensornode, "post_text",
                        lambda host, path, fields, t=None:
                        {"ok": True, "error": "", "text": "Error: geen ruimte"})
    uit = rooms.add_monitor(_rep(db), "Nieuw")
    assert not uit["ok"] and "Error" in uit["error"] and uit["ch"] is None


def test_room_en_snode_advert_sturen_idx_en_flood(db, monkeypatch):
    from app import rooms, sensornode
    gezien = []
    monkeypatch.setattr(sensornode, "post_form",
                        lambda host, path, fields, t=None: gezien.append((path, fields))
                        or {"ok": True, "error": "", "data": {}})
    rooms.room_advert(_rep(db), 2, flood=True)
    rooms.snode_advert(_rep(db), 1, flood=False)
    assert gezien[0] == (rooms.ROOM_ADVERT_PATH, {"idx": 2, "flood": "1"})
    assert gezien[1] == (rooms.SNODE_ADVERT_PATH, {"idx": 1, "flood": "0"})


# --- de per-sleutel-ACL van een room / sensor-node ----------------------------

def test_de_acl_wordt_uit_rooms_json_gelezen(db, monkeypatch):
    from app import rooms, sensornode
    monkeypatch.setattr(sensornode, "_json",
                        lambda host, path, t=None: {"ok": True, "error": "", "data": ROOMS_JSON})
    ov = rooms.overview(_rep(db))
    room0 = ov["rooms"]["rooms"][0]
    assert room0["acl"][0]["pub"] == "dd" * 32
    assert room0["acl"][0]["level"] == 2 and room0["acl"][0]["level_name"] == "readwrite"
    snode0 = ov["snodes"]["snodes"][0]
    assert snode0["acl"][0]["level_name"] == "read"


def test_set_room_acl_stuurt_pubkey_en_niveau(db, monkeypatch):
    from app import rooms, sensornode
    gezien = {}
    monkeypatch.setattr(sensornode, "post_form",
                        lambda host, path, fields, t=None: gezien.update(path=path, fields=fields)
                        or {"ok": True, "error": "", "data": {"ok": True, "level": "admin"}})
    uit = rooms.set_room_acl(_rep(db), 0, "ab" * 32, "admin")
    assert uit["ok"] and uit["level"] == "admin"
    assert gezien["path"] == rooms.ROOM_ACL_PATH
    assert gezien["fields"] == {"idx": 0, "pubkey": "ab" * 32, "level": "admin"}


def test_set_acl_weigert_een_onvolledige_pubkey_of_fout_niveau(db, monkeypatch):
    from app import rooms, sensornode
    monkeypatch.setattr(sensornode, "post_form",
                        lambda *a, **k: pytest.fail("mocht niet versturen"))
    # Te korte sleutel.
    kort = rooms.set_room_acl(_rep(db), 0, "abcd", "read")
    assert not kort["ok"] and "64 hex" in kort["error"]
    # Onbekend niveau.
    fout = rooms.set_room_acl(_rep(db), 0, "ab" * 32, "superuser")
    assert not fout["ok"] and "read, readwrite of admin" in fout["error"]


def test_del_room_acl_stuurt_del_en_prefix(db, monkeypatch):
    from app import rooms, sensornode
    gezien = {}
    monkeypatch.setattr(sensornode, "post_form",
                        lambda host, path, fields, t=None: gezien.update(path=path, fields=fields)
                        or {"ok": True, "error": "", "data": {}})
    uit = rooms.del_room_acl(_rep(db), 0, "abcdef012345")
    assert uit["ok"]
    assert gezien["path"] == rooms.ROOM_ACL_PATH
    assert gezien["fields"] == {"idx": 0, "pubkey": "abcdef012345", "del": "1"}


def test_del_acl_weigert_een_te_korte_prefix(db, monkeypatch):
    from app import rooms, sensornode
    monkeypatch.setattr(sensornode, "post_form",
                        lambda *a, **k: pytest.fail("mocht niet versturen"))
    uit = rooms.del_room_acl(_rep(db), 0, "abcdef")   # < 12 hex
    assert not uit["ok"] and "12 hex" in uit["error"]


def test_snode_acl_gaat_naar_het_snode_pad(db, monkeypatch):
    from app import rooms, sensornode
    paden = []
    monkeypatch.setattr(sensornode, "post_form",
                        lambda host, path, fields, t=None: paden.append(path)
                        or {"ok": True, "error": "", "data": {"ok": True, "level": "read"}})
    rooms.set_snode_acl(_rep(db), 1, "cd" * 32, "read")
    rooms.del_snode_acl(_rep(db), 1, "cdcdcdcdcdcd")
    assert paden == [rooms.SNODE_ACL_PATH, rooms.SNODE_ACL_PATH]


# --- SNMP-monitors ------------------------------------------------------------

def test_add_snmp_monitor_stuurt_de_velden_en_plukt_ch(db, monkeypatch):
    from app import rooms, sensornode
    gezien = {}
    monkeypatch.setattr(sensornode, "post_text",
                        lambda host, path, fields, t=None: gezien.update(path=path, fields=fields)
                        or {"ok": True, "error": "", "text": "ok ups -> kanaal 9"})
    uit = rooms.add_snmp_monitor(_rep(db), "ups", "1.2.3.4", "1.3.6.1.2.1.33.1.2.3",
                                 "numeric", "geheim123", snmparg="1", interval=60)
    assert uit["ok"] and uit["ch"] == 9
    assert gezien["path"] == rooms.SNMP_MONITOR_PATH
    assert gezien["fields"] == {
        "name": "ups", "host": "1.2.3.4", "oid": "1.3.6.1.2.1.33.1.2.3",
        "interp": "numeric", "community": "geheim123", "snmparg": "1", "int": 60}


def test_add_snmp_monitor_weigert_een_onbekende_interpretatie(db, monkeypatch):
    from app import rooms, sensornode
    monkeypatch.setattr(sensornode, "post_text",
                        lambda *a, **k: pytest.fail("mocht niet versturen"))
    uit = rooms.add_snmp_monitor(_rep(db), "x", "h", "1.2.3", "raar", "c")
    assert not uit["ok"] and "interpretatie" in uit["error"]


def test_snmp_presets_dragen_een_oid_en_een_geldige_interpretatie():
    from app import rooms
    assert "if_in" in rooms.SNMP_PRESET_BY_KEY and "ups_batt" in rooms.SNMP_PRESET_BY_KEY
    for p in rooms.SNMP_PRESETS:
        assert p["oid"] and p["interp"] in rooms.SNMP_INTERPS, p["key"]


# --- de notifier-bot ----------------------------------------------------------

def test_bot_parseert_naam_pubkey_en_ontvangers(db, monkeypatch):
    from app import rooms, sensornode
    monkeypatch.setattr(sensornode, "_json",
                        lambda host, path, t=None: {"ok": True, "error": "", "data": BOT_JSON})
    uit = rooms.bot(_rep(db))
    assert uit["ok"] and uit["active"] and uit["name"] == "MeldBot"
    assert uit["pub"] == "bb" * 32 and uit["max"] == 8
    assert uit["recips"][0]["k"] == "ff" * 32 and uit["recips"][0]["l"] == 1


def test_add_bot_recipient_valideert_de_pubkey(db, monkeypatch):
    from app import rooms, sensornode
    gezien = {}
    monkeypatch.setattr(sensornode, "post_form",
                        lambda host, path, fields, t=None: gezien.update(path=path, fields=fields)
                        or {"ok": True, "error": "", "data": {}})
    goed = rooms.add_bot_recipient(_rep(db), "ab" * 32)
    assert goed["ok"] and gezien["path"] == rooms.BOT_RECIPIENT_PATH
    assert gezien["fields"] == {"key": "ab" * 32}
    fout = rooms.add_bot_recipient(_rep(db), "abcd")
    assert not fout["ok"] and "64 hex" in fout["error"]


def test_del_bot_recipient_gebruikt_del_en_valideert_prefix(db, monkeypatch):
    from app import rooms, sensornode
    gezien = {}
    monkeypatch.setattr(sensornode, "post_form",
                        lambda host, path, fields, t=None: gezien.update(fields=fields)
                        or {"ok": True, "error": "", "data": {}})
    rooms.del_bot_recipient(_rep(db), "ffffffffffff")
    assert gezien["fields"] == {"del": "ffffffffffff"}
    kort = rooms.del_bot_recipient(_rep(db), "ffff")
    assert not kort["ok"] and "12 hex" in kort["error"]


def test_bot_sendto_en_post_valideren_en_versturen(db, monkeypatch):
    from app import rooms, sensornode
    gezien = []
    monkeypatch.setattr(sensornode, "post_form",
                        lambda host, path, fields, t=None: gezien.append((path, fields))
                        or {"ok": True, "error": "", "data": {}})
    rooms.bot_sendto(_rep(db), "ab" * 32, "hoi")
    rooms.bot_post(_rep(db), "iedereen")
    assert gezien[0] == (rooms.BOT_SENDTO_PATH, {"key": "ab" * 32, "msg": "hoi"})
    assert gezien[1] == (rooms.BOT_POST_PATH, {"msg": "iedereen"})
    # Leeg bericht en halve sleutel worden geweigerd vóór het net.
    assert not rooms.bot_post(_rep(db), "  ")["ok"]
    assert not rooms.bot_sendto(_rep(db), "ab" * 32, "")["ok"]
    assert not rooms.bot_sendto(_rep(db), "abcd", "hoi")["ok"]


def test_bot_advert_stuurt_flood(db, monkeypatch):
    from app import rooms, sensornode
    gezien = {}
    monkeypatch.setattr(sensornode, "post_form",
                        lambda host, path, fields, t=None: gezien.update(path=path, fields=fields)
                        or {"ok": True, "error": "", "data": {}})
    rooms.bot_advert(_rep(db), flood=False)
    assert gezien["path"] == rooms.BOT_ADVERT_PATH and gezien["fields"] == {"flood": "0"}


# --- de ontdekte contacten ----------------------------------------------------

def test_contacts_filtert_halve_sleutels_weg(db, monkeypatch):
    from app import rooms, sensornode
    monkeypatch.setattr(sensornode, "_json",
                        lambda host, path, t=None: {"ok": True, "error": "", "data": CONTACTS_JSON})
    uit = rooms.contacts(_rep(db))
    assert uit["ok"] and len(uit["contacts"]) == 1
    assert uit["contacts"][0]["k"] == "ab" * 32 and uit["contacts"][0]["n"] == "DinX-Home"


# --- de netwerkprimitieven (post_form / post_json) ----------------------------

def test_post_form_bouwt_een_form_body(db, monkeypatch):
    from app import firmware, sensornode
    gezien = {}
    monkeypatch.setattr(firmware, "NODE_USER", "admin")

    def nep(host, path, data=None, timeout=None, content_type=None):
        gezien.update(path=path, data=data, ct=content_type)
        return _Antwoord(b'{"ok":true,"idx":2}')

    monkeypatch.setattr(firmware, "open_node", nep)
    uit = sensornode.post_form("host", "/room/add", {"name": "X"})
    assert uit["ok"] and uit["data"]["idx"] == 2
    assert gezien["ct"] == "application/x-www-form-urlencoded"
    assert gezien["data"].decode() == "name=X"


def test_post_json_stuurt_een_json_body(db, monkeypatch):
    from app import firmware, sensornode
    gezien = {}
    monkeypatch.setattr(firmware, "NODE_USER", "admin")

    def nep(host, path, data=None, timeout=None, content_type=None):
        gezien.update(data=data, ct=content_type)
        return _Antwoord(b'{"ok":true}')

    monkeypatch.setattr(firmware, "open_node", nep)
    uit = sensornode.post_json("host", "/rooms/restore", {"rooms": []})
    assert uit["ok"]
    assert gezien["ct"] == "application/json"
    assert json.loads(gezien["data"]) == {"rooms": []}


def test_post_leest_een_geweigerde_handeling_uit_ok_false(db, monkeypatch):
    from app import firmware, sensornode
    monkeypatch.setattr(firmware, "NODE_USER", "admin")
    monkeypatch.setattr(firmware, "open_node",
                        lambda *a, **k: _Antwoord(b'{"ok":false,"error":"rooms vol"}'))
    uit = sensornode.post_form("host", "/room/add", {"name": "X"})
    assert not uit["ok"] and uit["error"] == "rooms vol"


def test_post_zonder_weblogin_stuurt_niets(db, monkeypatch):
    from app import firmware, sensornode
    monkeypatch.setattr(firmware, "NODE_USER", "")
    monkeypatch.setattr(firmware, "open_node",
                        lambda *a, **k: pytest.fail("er mag geen verbinding open zonder login"))
    uit = sensornode.post_form("host", "/room/add", {"name": "X"})
    assert not uit["ok"] and "weblogin" in uit["error"]


# --- de koppeling room <-> sensor ---------------------------------------------

def test_couple_koppelt_de_juiste_sensoren_aan_elke_room():
    from app import rooms
    kamers = [{"idx": 0, "name": "a"}, {"idx": 1, "name": "b"}]
    gekoppeld = rooms.couple(kamers, STATUS_MET_MON["mon"])
    namen0 = {s["n"] for s in gekoppeld[0]["sensoren"]}
    namen1 = {s["n"] for s in gekoppeld[1]["sensoren"]}
    # room 0 (bit 0): wifi (rm=0b01, am=3). batterij staat op am=1 en telt niet mee.
    assert namen0 == {"wifi"}
    # room 1 (bit 1): google (rm=0b10, am=2).
    assert namen1 == {"google"}


def test_een_sensor_op_alleen_dm_hoort_bij_geen_enkele_room():
    from app import rooms
    assert not rooms.sensor_in_room({"am": 1, "rm": 0b11}, 0)
    assert rooms.sensor_in_room({"am": 3, "rm": 0b01}, 0)


# --- backup: gevoelige sleutels -----------------------------------------------

def test_backup_wordt_geobfusceerd_bewaard_en_teruggelezen(db):
    from app import rooms
    rep = _rep(db)
    geheim = {"rooms": [{"idx": 0, "pub": "48d7", "priv": "ZEERGEHEIMESLEUTEL"}]}
    opslag = rooms.store_backup(rep, geheim, actor="admin")
    assert opslag["ok"]
    rij = db.room_backup(opslag["id"], rep["id"])
    # De platte sleutel staat NIET leesbaar in de opgeslagen blob.
    assert "ZEERGEHEIMESLEUTEL" not in rij["blob"]
    assert rij["rooms"] == 1 and rij["actor"] == "admin"
    # En hij is wél terug te lezen met dezelfde secret.key.
    terug = rooms.load_stored(rep, opslag["id"])
    assert terug["ok"] and terug["data"] == geheim


def test_een_backup_opent_niet_met_een_andere_secret_key(db, monkeypatch):
    from app import rooms
    rep = _rep(db)
    opslag = rooms.store_backup(rep, {"rooms": []}, actor="admin")
    monkeypatch.setattr(config, "SECRET", b"een-heel-andere-sleutel-dan-net")
    terug = rooms.load_stored(rep, opslag["id"])
    assert not terug["ok"] and "secret.key" in terug["error"]


def test_backup_and_store_bewaart_niets_als_het_ophalen_faalt(db, monkeypatch):
    from app import rooms
    rep = _rep(db)
    monkeypatch.setattr(rooms, "fetch_backup",
                        lambda rep, timeout=None: {"ok": False, "error": "node weg", "data": {}})
    uit = rooms.backup_and_store(rep, actor="admin")
    assert not uit["ok"] and uit["error"] == "node weg"
    assert rooms.list_stored(rep) == []


def test_restore_stored_stuurt_de_bewaarde_config_naar_de_node(db, monkeypatch):
    from app import rooms, sensornode
    rep = _rep(db)
    opslag = rooms.store_backup(rep, {"rooms": [{"idx": 0}]}, actor="admin")
    gezien = {}
    monkeypatch.setattr(sensornode, "post_json",
                        lambda host, path, obj, t=None: gezien.update(path=path, obj=obj)
                        or {"ok": True, "error": "", "data": {}})
    uit = rooms.restore_stored(rep, opslag["id"])
    assert uit["ok"]
    assert gezien["path"] == "/rooms/restore" and gezien["obj"] == {"rooms": [{"idx": 0}]}


# --- de mapping room-pubkey -> fysieke node -----------------------------------

def test_record_owners_koppelt_rooms_snodes_en_bot_en_snoeit(db):
    from app import rooms
    rep = _rep(db)
    bot_ent = {"pub": BOT_JSON["pub"], "name": BOT_JSON["name"], "idx": 0}
    aantal = rooms.record_owners(rep, ROOMS_JSON["rooms"], ROOMS_JSON["snodes"], bot_ent)
    assert aantal == 4
    rijen = {r["room_key"]: r["kind"] for r in db.room_owners_for(rep["id"])}
    assert rijen == {
        db.node_key("48d7aade232b" + "00" * 10): "room",
        db.node_key("aa" * 16): "room",
        db.node_key("cc" * 32): "sensor",
        db.node_key("bb" * 32): "bot",
    }
    # Een volgende ronde waarin room 1, de sensor-node en de bot weg zijn, snoeit ze weg.
    rooms.record_owners(rep, [ROOMS_JSON["rooms"][0]], [], None)
    keys = {r["room_key"] for r in db.room_owners_for(rep["id"])}
    assert keys == {db.node_key("48d7aade232b" + "00" * 10)}


def test_de_nodelijst_koppelt_losse_rooms_en_snodes_aan_hun_eigenaar(db, monkeypatch):
    """Een virtuele room én een virtuele sensor-node verschijnen op het mesh als
    losse nodes; de lijst koppelt beide aan hun eigenaar met de juiste soort."""
    from app import auth, mqtt_ingest, rbac, rooms, routes_admin
    from starlette.requests import Request

    monkeypatch.setattr(mqtt_ingest, "can_publish", lambda: False)
    rbac.maak_gebruiker("admin", auth.hash_password("wachtwoord123"),
                        is_superuser=True)
    rep = _rep(db)
    # De losse entries zoals ze op het mesh binnenkwamen: eigen pubkeys.
    for sleutel in ("aaaaaaaaaaaa", "cccccccccccc", "bbbbbbbbbbbb"):
        r = db.get_or_create_repeater(sleutel, "onbekend")
        db.execute("UPDATE repeaters SET is_public=1 WHERE id=?", (r["id"],))
    bot_ent = {"pub": BOT_JSON["pub"], "name": BOT_JSON["name"], "idx": 0}
    rooms.record_owners(rep, ROOMS_JSON["rooms"], ROOMS_JSON["snodes"], bot_ent)

    cookie = auth.make_session("admin")
    req = Request({"type": "http", "http_version": "1.1", "method": "GET",
                   "scheme": "http", "server": ("test", 80), "path": "/admin",
                   "query_string": b"",
                   "headers": [(b"cookie", f"mm_session={cookie}".encode())]})
    html = routes_admin.nodes_page(req).body.decode()
    assert "room op MeshUptime" in html
    assert "sensor-node op MeshUptime" in html
    assert "bot op MeshUptime" in html
    assert "host van 1 room + 1 sensor-node + 1 bot" in html


def test_de_room_en_sensornode_secties_renderen(db, monkeypatch):
    """De hele Rooms- én Sensor-nodes-sectie, door de echte Jinja-omgeving."""
    from app import (auth, firmware, mqtt_ingest, nodeconfig, rbac,
                     routes_admin)
    from starlette.requests import Request

    monkeypatch.setattr(mqtt_ingest, "can_publish", lambda: False)
    monkeypatch.setattr(firmware, "NODE_USER", "admin")

    def nep_open(host, path, data=None, timeout=None):
        if path == "/rooms.json":
            return _Antwoord(json.dumps(ROOMS_JSON).encode())
        if path == "/status.json":
            return _Antwoord(json.dumps(STATUS_MET_MON).encode())
        if path == "/acl.json":
            return _Antwoord(json.dumps({"acl": [], "nb": []}).encode())
        if path == "/bot.json":
            return _Antwoord(json.dumps(BOT_JSON).encode())
        if path == "/contacts.json":
            return _Antwoord(json.dumps(CONTACTS_JSON).encode())
        return _Antwoord(b"{}")

    monkeypatch.setattr(nodeconfig, "_open", nep_open)
    rbac.maak_gebruiker("admin", auth.hash_password("wachtwoord123"),
                        is_superuser=True)
    rep = _rep(db)

    cookie = auth.make_session("admin")
    req = Request({"type": "http", "http_version": "1.1", "method": "GET",
                   "scheme": "http", "server": ("test", 80),
                   "path": f"/admin/repeaters/{rep['id']}", "query_string": b"",
                   "headers": [(b"cookie", f"mm_session={cookie}".encode())]})
    html = routes_admin.node_page(req, rep["id"]).body.decode()
    assert "Rooms" in html
    assert "Storingen" in html and "meshcore://join/0" in html
    assert "<svg" in html                      # de join-/contact-QR, serverzijde
    assert "Alarmroute per sensor" in html
    # De Sensor-nodes-sectie: naam, contact-link en de gekoppelde kanalen.
    assert "Sensor-nodes" in html and "Weerstation" in html
    assert "type=4" in html                     # de contact-URI van de sensor-node
    # Het node-centrische kanaalbeheer-panel en de advert-knoppen.
    assert "Kanalen beheren" in html and "Kanaal toevoegen" in html
    assert "Advert (flood)" in html and "Advert (zerohop)" in html
    # Het ACL-panel: kop, de wachtwoordloze uitleg, en de bestaande grant.
    assert "Toegang (ACL)" in html and "zonder wachtwoord" in html
    assert ("dd" * 32)[:16] in html            # de room-ACL-sleutel, ingekort
    assert "Sleutel toevoegen" in html
    # De "?"-help-popovers (instellingen-eerst, uitleg erachter).
    assert 'class="help-pop"' in html and 'class="help-btn"' in html
    # De SNMP-monitorsectie met de preset-bibliotheek.
    assert "SNMP-monitor toevoegen" in html and "ifHCInOctets" in html
    assert 'name="community"' in html and 'type="password"' in html
    # De notifier-bot: naam, contact-QR/link, en de ontvangerslijst.
    assert "Notifier-bot" in html and "MeldBot" in html
    assert "Test-DM sturen" in html and ("ff" * 32)[:16] in html
    # De contactenkiezer (datalist) voedt de pubkey-velden.
    assert 'id="mm-contacts"' in html and "DinX-Home" in html
    # De mapping is bij het renderen vastgelegd: 2 rooms + 1 sensor-node + 1 bot.
    rijen = {r["room_key"]: r["kind"] for r in db.room_owners_for(rep["id"])}
    assert rijen[db.node_key("cc" * 32)] == "sensor"
    assert rijen[db.node_key("bb" * 32)] == "bot"
    assert len(rijen) == 4
