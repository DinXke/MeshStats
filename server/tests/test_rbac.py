"""Rechten: wie mag wat zien en doen. Draait de echte app (zonder mesh)."""
import os
import tempfile

import pytest
from fastapi.testclient import TestClient

from meshtrack import auth


@pytest.fixture()
def client(tmp_path):
    cfg = tmp_path / "config.yaml"
    cfg.write_text(f"""
mesh: {{host: 127.0.0.1, port: 1}}
db_path: {tmp_path / 'mt.sqlite3'}
tiles_dir: {tmp_path / 'tiles'}
openhop_db: {tmp_path / 'geen.db'}
auth: {{user: admin, password_hash: "{auth.hash_password('beheerder1')}", session_secret: "test-geheim"}}
""".replace("\\\\", "/"), encoding="utf-8")
    os.environ["MESHTRACK_CONFIG"] = str(cfg)
    from meshtrack import main
    main._pcache.clear()
    main._fails.clear()
    with TestClient(main.app) as c:
        yield c, main


def login(c, user, pw):
    r = c.post("/api/login", json={"user": user, "password": pw})
    assert r.status_code == 200, r.text


def test_bootstrap_admin_and_me(client):
    c, _ = client
    assert c.get("/api/trackers").status_code == 401
    login(c, "admin", "beheerder1")
    me = c.get("/api/me").json()
    assert me["group"] == "Beheerders" and "users.manage" in me["perms"]
    names = [g["name"] for g in c.get("/api/groups").json()["groups"]]
    assert names == ["Beheerders", "Operators", "Kijkers", "Kiosk"]


def test_viewer_scope_and_denials(client):
    c, main = client
    login(c, "admin", "beheerder1")
    a = c.post("/api/trackers", json={"pubkey": "aa" * 32, "alias": "A"}).json()["tracker"]["id"]
    b = c.post("/api/trackers", json={"pubkey": "bb" * 32, "alias": "B"}).json()["tracker"]["id"]
    g = c.post("/api/groups", json={"name": "Ploeg A", "perms": ["map.view", "map.sidebar", "map.tracks"],
                                    "all_trackers": False, "trackers": [a], "history_hours": 6}).json()
    r = c.post("/api/users", json={"username": "jan", "password": "geheim123", "group_id": g["id"]})
    assert r.status_code == 200, r.text
    c.post("/api/logout")
    login(c, "jan", "geheim123")
    ts = c.get("/api/trackers").json()
    assert [t["alias"] for t in ts] == ["A"] and "pubkey" not in ts[0]      # geen details
    assert c.get(f"/api/trackers/{b}/track").status_code == 404           # buiten bereik
    assert c.get(f"/api/trackers/{a}/track").status_code == 200
    assert c.post("/api/trackers", json={"pubkey": "cc" * 32, "alias": "C"}).status_code == 403
    assert c.get("/api/users").status_code == 403
    assert c.get("/api/geofences").status_code == 403
    assert c.get(f"/api/trackers/{a}/export").status_code == 403
    assert c.get("/admin", follow_redirects=False).status_code in (302, 307)


def test_password_change_invalidates_old_sessions(client):
    c, main = client
    login(c, "admin", "beheerder1")
    old_cookie = c.cookies.get(auth.COOKIE)
    r = c.post("/api/me/password", json={"old": "beheerder1", "new": "nieuwwachtwoord"})
    assert r.status_code == 200
    assert c.get("/api/me").status_code == 200                           # nieuwe cookie werkt
    c.cookies.set(auth.COOKIE, old_cookie)
    main._pcache.clear()
    assert c.get("/api/me").status_code == 401                           # oude sessie ongeldig


def test_cannot_lock_out_last_admin(client):
    c, _ = client
    login(c, "admin", "beheerder1")
    me_id = next(u["id"] for u in c.get("/api/users").json() if u["username"] == "admin")
    kiosk = next(g["id"] for g in c.get("/api/groups").json()["groups"] if g["name"] == "Kiosk")
    assert c.put(f"/api/users/{me_id}", json={"group_id": kiosk}).status_code == 409
    assert c.put(f"/api/users/{me_id}", json={"active": False}).status_code == 409
    admins = next(g for g in c.get("/api/groups").json()["groups"] if g["name"] == "Beheerders")
    assert c.put(f"/api/groups/{admins['id']}", json={**admins, "perms": ["map.view"]}).status_code == 409


def test_share_link_kiosk(client):
    c, _ = client
    login(c, "admin", "beheerder1")
    a = c.post("/api/trackers", json={"pubkey": "aa" * 32, "alias": "A"}).json()["tracker"]["id"]
    c.post("/api/trackers", json={"pubkey": "bb" * 32, "alias": "B"})
    url = c.post("/api/shares", json={"name": "Wedstrijd", "trackers": [a], "valid_hours": 2}).json()["url"]
    token = url.rsplit("/", 1)[1]
    c.post("/api/logout")
    c.cookies.clear()
    r = c.get(f"/s/{token}", follow_redirects=False)
    assert r.status_code in (302, 307)
    me = c.get("/api/me").json()
    assert me["kind"] == "share" and "map.sidebar" not in me["perms"]
    assert [t["alias"] for t in c.get("/api/trackers").json()] == ["A"]
    assert c.get("/api/geofences").status_code == 403
    assert c.get("/s/bestaatniet").status_code == 404


def test_export_gpx(client):
    c, main = client
    login(c, "admin", "beheerder1")
    tid = c.post("/api/trackers", json={"pubkey": "aa" * 32, "alias": "A"}).json()["tracker"]["id"]
    from meshtrack import ingest
    ingest.handle(main.S.db, main.S.cfg, "aa" * 6, "T1|1|M|50.93|5.33|40|30|90|80|1.0|0")
    r = c.get(f"/api/trackers/{tid}/export?fmt=gpx")
    assert r.status_code == 200 and "<trkpt lat=\"50.93\"" in r.text
    assert c.get(f"/api/trackers/{tid}/export?fmt=csv").text.startswith("tijd_utc,lat,lon")
    assert any(a["action"] == "spoor geëxporteerd" for a in c.get("/api/audit").json())


def _chan(c, name, slot):
    r = c.post("/api/channels", json={"name": name, "slot": slot})
    assert r.status_code == 200, r.text
    return r.json()["id"]


def test_channel_rights_and_multiple_user_groups(client):
    c, main = client
    login(c, "admin", "beheerder1")
    amb, brand = _chan(c, "#ambu", 3), _chan(c, "#brand", 4)
    ids = {n: c.post("/api/trackers", json={"pubkey": k * 32, "alias": n}).json()["tracker"]["id"]
           for n, k in (("Amb1", "a1"), ("Amb2", "a2"), ("Brand", "b1"), ("Fiets", "f1"))}
    for n in ("Amb1", "Amb2"):
        r = c.put(f"/api/trackers/{ids[n]}", json={"channel_id": amb})
        assert r.status_code == 200 and r.json()["tracker"]["channel_id"] == amb
    c.put(f"/api/trackers/{ids['Brand']}", json={"channel_id": brand})
    assert c.put(f"/api/trackers/{ids['Fiets']}", json={"channel_id": 999}).status_code == 422
    view = ["map.view", "map.sidebar", "map.tracks"]
    g1 = c.post("/api/groups", json={"name": "Ploeg Amb", "perms": view, "all_trackers": False,
                                     "channels": {str(amb): "kaart"}, "history_hours": 6}).json()
    assert g1["channels"] == {amb: "kaart"} or g1["channels"] == {str(amb): "kaart"}
    g2 = c.post("/api/groups", json={"name": "Fietsers", "perms": view + ["log.view"], "all_trackers": False,
                                     "trackers": [ids["Fiets"]], "channels": {str(brand): "onzin"}, "history_hours": 24}).json()
    assert not g2["channels"]                                        # onbekend niveau valt weg
    assert c.post("/api/users", json={"username": "els", "password": "geheim123",
                                      "group_ids": [g1["id"], g2["id"]]}).status_code == 200
    c.post("/api/logout")
    login(c, "els", "geheim123")
    me = c.get("/api/me").json()
    assert "log.view" in me["perms"] and me["history_hours"] == 24        # som van de groepen
    assert sorted(t["alias"] for t in c.get("/api/trackers").json()) == ["Amb1", "Amb2", "Fiets"]
    mine = c.get("/api/channels/mine").json()
    assert [(x["name"], x["level"], x["trackers"]) for x in mine] == [("#ambu", "kaart", 2)]
    assert "secret" not in mine[0]                                   # kaartniveau: geen sleutel
    # een tracker die later op het kanaal stuurt, is meteen zichtbaar
    c.post("/api/logout")
    login(c, "admin", "beheerder1")
    c.put(f"/api/trackers/{ids['Brand']}", json={"channel_id": amb})
    c.post("/api/logout")
    login(c, "els", "geheim123")
    assert "Brand" in [t["alias"] for t in c.get("/api/trackers").json()]


def test_last_admin_with_multiple_groups(client):
    c, _ = client
    login(c, "admin", "beheerder1")
    admin = next(x for x in c.get("/api/users").json() if x["username"] == "admin")
    kijk = next(g["id"] for g in c.get("/api/groups").json()["groups"] if g["name"] == "Kijkers")
    assert c.put(f"/api/users/{admin['id']}", json={"group_ids": [kijk]}).status_code == 409
    assert c.put(f"/api/users/{admin['id']}", json={"group_ids": [admin["group_ids"][0], kijk]}).status_code == 200


def test_share_with_channel_is_dynamic_and_limited_to_creator(client):
    c, main = client
    login(c, "admin", "beheerder1")
    ch = _chan(c, "#ploeg", 3)
    a = c.post("/api/trackers", json={"pubkey": "a1" * 32, "alias": "A", "channel_id": ch}).json()["tracker"]["id"]
    b = c.post("/api/trackers", json={"pubkey": "b1" * 32, "alias": "B"}).json()["tracker"]["id"]
    x = c.post("/api/trackers", json={"pubkey": "c1" * 32, "alias": "X"}).json()["tracker"]["id"]
    # beperkte maker: ziet kanaal #ploeg en losse tracker B, niet X
    g = c.post("/api/groups", json={"name": "Deler", "perms": ["map.view", "share.manage"], "all_trackers": False,
                                    "trackers": [b], "channels": {str(ch): "kaart"}}).json()
    c.post("/api/users", json={"username": "deler", "password": "geheim123", "group_ids": [g["id"]]})
    c.post("/api/logout")
    login(c, "deler", "geheim123")
    assert c.post("/api/shares", json={"name": "Leeg", "channels": [999]}).status_code == 422
    url = c.post("/api/shares", json={"name": "Link", "channels": [ch], "hours": 12}).json()["url"]
    token = url.rsplit("/", 1)[1]
    c.post("/api/logout")
    c.get(f"/s/{token}")
    assert [t["alias"] for t in c.get("/api/trackers").json()] == ["A"]
    # later sturen B (maker ziet die) en X (maker ziet die niet) op het kanaal
    c.cookies.clear()
    login(c, "admin", "beheerder1")
    c.put(f"/api/trackers/{b}", json={"channel_id": ch})
    c.put(f"/api/trackers/{x}", json={"channel_id": ch})
    c.post("/api/logout")
    c.cookies.clear()
    main._pcache.clear()
    c.get(f"/s/{token}")
    # X staat nu op het kanaal van de maker en is dus ook voor hem zichtbaar
    assert sorted(t["alias"] for t in c.get("/api/trackers").json()) == ["A", "B", "X"]


def test_effective_rights_shows_origin(client):
    c, _ = client
    login(c, "admin", "beheerder1")
    ch = _chan(c, "#ploeg", 3)
    c.post("/api/trackers", json={"pubkey": "a1" * 32, "alias": "A", "channel_id": ch})
    b = c.post("/api/trackers", json={"pubkey": "b1" * 32, "alias": "B"}).json()["tracker"]["id"]
    c.post("/api/trackers", json={"pubkey": "c1" * 32, "alias": "C"})
    g1 = c.post("/api/groups", json={"name": "G1", "perms": ["map.view"], "all_trackers": False,
                                     "channels": {str(ch): "sleutel"}, "history_hours": 6}).json()
    g2 = c.post("/api/groups", json={"name": "G2", "perms": ["map.view", "log.view"], "all_trackers": False,
                                     "trackers": [b], "channels": {str(ch): "kaart"}, "history_hours": 48}).json()
    uid = c.post("/api/users", json={"username": "piet", "password": "geheim123", "group_ids": [g1["id"], g2["id"]]}).json()["id"]
    e = c.get(f"/api/users/{uid}/effective").json()
    perms = {p["id"]: p["via"] for p in e["perms"]}
    assert perms["map.view"] == ["G1", "G2"] and perms["log.view"] == ["G2"] and perms["users.manage"] == []
    chans = {x["name"]: x for x in e["channels"]}
    assert chans["#ploeg"]["level"] == "sleutel" and chans["#ploeg"]["via"] == ["G1: sleutel", "G2: kaart"]
    tr = {t["alias"]: t for t in e["trackers"]}
    assert tr["A"]["sees"] and tr["A"]["via"] == ["G1: kanaal #ploeg", "G2: kanaal #ploeg"]
    assert tr["B"]["sees"] and tr["B"]["via"] == ["G2: losse tracker"]
    assert not tr["C"]["sees"] and e["history_hours"] == 48 and e["history_via"] == ["G2"]


def test_channel_messages_signed_and_set_tracking_channel(client):
    import asyncio
    c, main = client
    login(c, "admin", "beheerder1")
    tid = c.post("/api/trackers", json={"pubkey": "ab12cd34" + "00" * 28, "alias": "Kanaaltracker"}).json()["tracker"]["id"]
    cid = _chan(c, "#ploeg3", 5)
    ch = c.get("/api/channels").json()["channels"][0]
    assert ch["secret"] == __import__("hashlib").sha256(b"#ploeg3").hexdigest()[:32] and ch["require_sig"]
    key = c.get(f"/api/trackers/{tid}/authkey").json()["authkey"]
    rest = "3|M|50.93|5.33|40|50|90|80|0.9|1|t|b|1800000000"
    good = main.channel_tag(key, f"ab12cd34|{rest}")
    run = lambda txt: asyncio.run(main.on_channel(5, txt, 1800000000, 5.0, 2))
    run(f"Tracker: T1C|ab12cd34|00000000|{rest}")                # foute handtekening
    run(f"Tracker: T1C|ab12cd34|-|{rest}")                       # niet ondertekend, kanaal eist het
    assert not c.get(f"/api/trackers/{tid}/track?hours=999999").json()
    reasons = [u["reason"] for u in c.get("/api/unknown").json()]
    assert "kanaal #ploeg3: ongeldige handtekening" in reasons and "kanaal #ploeg3: niet ondertekend" in reasons
    run(f"🇧🇪 Tracker 1: T1C|ab12cd34|{good}|{rest}")             # afzender = nodenaam (mag alles zijn)
    assert len(c.get(f"/api/trackers/{tid}/track?hours=999999").json()) == 1
    t = next(x for x in c.get("/api/trackers").json() if x["id"] == tid)
    assert t["last_via"] == "kanaal #ploeg3" and t["has_authkey"] and "authkey" not in t
    assert t["channel_id"] == cid                                 # trackingkanaal volgt het bericht
    run("Jan: hallo allemaal")                                    # gewone chat: genegeerd
    run(f"MT: T1C|ab12cd34|{good}|{rest}".replace("|5.33|", "|5.34|"))   # gewijzigd: handtekening klopt niet meer
    assert len(c.get(f"/api/trackers/{tid}/track?hours=999999").json()) == 1


def test_dm_from_old_tracker_is_ignored(client):
    import asyncio
    c, main = client
    login(c, "admin", "beheerder1")
    tid = c.post("/api/trackers", json={"pubkey": "ab12cd34" + "00" * 28, "alias": "Oud"}).json()["tracker"]["id"]
    asyncio.run(main.on_message("ab12cd340000", "T1|1|M|50.93|5.33|40|30|90|80|1.0|0", 1800000000, 5.0, 1))
    assert not c.get(f"/api/trackers/{tid}/track?hours=999999").json()
    assert any("oude firmware" in u["reason"] for u in c.get("/api/unknown").json())


def test_channel_keys_only_with_key_level(client):
    c, main = client
    login(c, "admin", "beheerder1")
    amb, brand = _chan(c, "#ambu", 6), _chan(c, "#brand", 7)
    assert {x["name"] for x in c.get("/api/channels/device").json()} == {"#ambu", "#brand"}   # beheerder: alles
    assert all(x["level"] == "sleutel" and x["secret"] for x in c.get("/api/channels/mine").json())
    g = c.post("/api/groups", json={"name": "Ambu-techniek", "perms": ["map.view", "trackers.serial"], "all_trackers": False,
                                    "channels": {str(amb): "sleutel", str(brand): "kaart"}}).json()
    c.post("/api/users", json={"username": "tech", "password": "geheim123", "group_ids": [g["id"]]})
    c.post("/api/logout")
    login(c, "tech", "geheim123")
    got = c.get("/api/channels/device").json()
    assert [x["name"] for x in got] == ["#ambu"] and got[0]["secret"]
    mine = {x["name"]: x for x in c.get("/api/channels/mine").json()}
    assert mine["#ambu"]["secret"] and "secret" not in mine["#brand"] and mine["#brand"]["level"] == "kaart"
    assert c.get("/api/channels").status_code == 403                 # volledige lijst alleen voor beheer
    # kanaal verwijderen haalt het uit de rechten
    c.post("/api/logout")
    login(c, "admin", "beheerder1")
    c.delete(f"/api/channels/{amb}")
    grp = next(x for x in c.get("/api/groups").json()["groups"] if x["name"] == "Ambu-techniek")
    assert set(grp["channels"]) == {brand} or set(grp["channels"]) == {str(brand)}


def test_migration_from_tracker_groups(tmp_path):
    """Een database van voor 1.0: kanaalgroepen worden kanaalrechten en trackingkanalen."""
    import json, sqlite3, time
    from meshtrack.db import DB, SCHEMA
    p = tmp_path / "oud.sqlite3"
    con = sqlite3.connect(p)
    con.executescript(SCHEMA)
    con.execute("ALTER TABLE groups ADD COLUMN tracker_groups TEXT NOT NULL DEFAULT '[]'")
    con.execute("ALTER TABLE tracker_groups ADD COLUMN includes TEXT NOT NULL DEFAULT '[]'")
    for t in ("alert_rules", "shares"):
        con.execute(f"ALTER TABLE {t} ADD COLUMN tracker_groups TEXT NOT NULL DEFAULT '[]'")
    now = int(time.time())
    con.execute("INSERT INTO trackers(id, pubkey, alias, created) VALUES(1, ?, 'A', ?)", ("a1" * 32, now))
    con.execute("INSERT INTO trackers(id, pubkey, alias, created) VALUES(2, ?, 'B', ?)", ("b1" * 32, now))
    con.execute("INSERT INTO tracker_groups(id, name, created) VALUES(10, 'Kanaal #ambu', ?)", (now,))
    con.execute("INSERT INTO tracker_groups(id, name, created, includes) VALUES(11, 'Interventie', ?, '[10]')", (now,))
    con.execute("INSERT INTO tracker_group_members(group_id, tracker_id) VALUES(10, 1)")
    con.execute("INSERT INTO channels(id, name, secret, slot, created, tracker_group_id) VALUES(5, '#ambu', ?, 3, ?, 10)",
                ("00" * 16, now))
    con.execute("INSERT INTO groups(id, name, perms, all_trackers, trackers, history_hours, created, tracker_groups) "
                "VALUES(1, 'Kijk', ?, 0, '[]', 0, ?, '[11]')", (json.dumps(["map.view"]), now))
    con.execute("INSERT INTO groups(id, name, perms, all_trackers, trackers, history_hours, created, tracker_groups) "
                "VALUES(2, 'Tech', ?, 0, '[]', 0, ?, '[10]')", (json.dumps(["map.view", "trackers.serial"]), now))
    con.commit()
    con.close()
    db = DB(str(p))
    assert db.tracker(1)["channel_id"] == 5 and db.tracker(2)["channel_id"] is None
    groups = {g["name"]: g["channels"] for g in db.groups()}
    assert groups == {"Kijk": {5: "kaart"}, "Tech": {5: "sleutel"}}
    assert db.channel_members() == {5: {1}}
    DB(str(p))                                                     # tweede keer: niets meer te doen


def test_offline_app_uses_only_maps(client):
    """De offline-app gebruikt niets uit de database: alleen kaarten, zonder login."""
    c, main = client
    assert c.get("/offline").status_code == 200
    assert c.get("/offline-sw.js").headers["service-worker-allowed"] == "/"
    off = __import__("pathlib").Path(main.S.cfg.tiles_dir) / "offline"
    off.mkdir(parents=True, exist_ok=True)
    (off / "benelux-z10.pmtiles").write_bytes(b"x" * 1234)
    maps = c.get("/api/offline/maps").json()
    assert [(m["key"], m["size"], m["url"]) for m in maps] == [("benelux-z10", 1234, "/tiles/offline/benelux-z10.pmtiles")]
    assert c.get("/api/trackers").status_code == 401
    login(c, "admin", "beheerder1")
    assert c.get("/api/offline/bundle").status_code == 404          # bestaat niet meer


def test_sos_on_channel_is_confirmed_once(client):
    import asyncio, hashlib, hmac as _hmac
    c, main = client
    login(c, "admin", "beheerder1")
    tid = c.post("/api/trackers", json={"pubkey": "ab12cd34" + "00" * 28, "alias": "SOS-tracker"}).json()["tracker"]["id"]
    _chan(c, "#ploeg3", 5)
    key = c.get(f"/api/trackers/{tid}/authkey").json()["authkey"]
    sent = []

    class FakeMesh:
        connected = True
        async def send_channel(self, slot, text, scope=""):
            sent.append((slot, text, scope))
        def status(self):
            return {"connected": True}

    real, main.S.mesh = main.S.mesh, FakeMesh()
    main._sos_acked.clear()
    try:
        def msg(seq, state):
            rest = f"{seq}|{state}|50.93|5.33|40|50|90|80|0.9|1|t|b|1800000000"
            return f"Tracker: T1C|ab12cd34|{main.channel_tag(key, f'ab12cd34|{rest}')}|{rest}"
        run = lambda txt: asyncio.run(main.on_channel(5, txt, 1800000000, 5.0, 2))
        run(msg(7, "M"))                                   # gewone positie: geen bevestiging
        run(msg(8, "E"))                                   # SOS
        run(msg(8, "E"))                                   # zelfde SOS nog eens gehoord: niet opnieuw
        assert len(sent) == 1
        slot, text, scope = sent[0]
        tag = _hmac.new(bytes.fromhex(key), b"ab12cd34|A|8", hashlib.sha256).hexdigest()[:8]
        assert slot == 5 and text == f"T1A|ab12cd34|{tag}|8" and scope == "be"
    finally:
        main.S.mesh = real
