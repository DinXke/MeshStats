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


def test_tracker_groups_and_multiple_user_groups(client):
    c, main = client
    login(c, "admin", "beheerder1")
    ids = {n: c.post("/api/trackers", json={"pubkey": k * 32, "alias": n}).json()["tracker"]["id"]
           for n, k in (("Amb1", "a1"), ("Amb2", "a2"), ("Brand", "b1"), ("Fiets", "f1"))}
    amb = c.post("/api/tracker-groups", json={"name": "Ziekenwagens", "trackers": [ids["Amb1"]]}).json()
    brand = c.post("/api/tracker-groups", json={"name": "Brandweer", "trackers": [ids["Brand"], ids["Amb2"]]}).json()
    # een tracker in meerdere groepen
    r = c.put(f"/api/trackers/{ids['Amb2']}", json={"groups": [amb["id"], brand["id"]]})
    assert sorted(r.json()["tracker"]["groups"]) == sorted([amb["id"], brand["id"]])
    view = ["map.view", "map.sidebar", "map.tracks"]
    g1 = c.post("/api/groups", json={"name": "Ploeg Amb", "perms": view, "all_trackers": False,
                                     "tracker_groups": [amb["id"]], "history_hours": 6}).json()
    g2 = c.post("/api/groups", json={"name": "Fietsers", "perms": view + ["log.view"], "all_trackers": False,
                                     "trackers": [ids["Fiets"]], "history_hours": 24}).json()
    assert c.post("/api/users", json={"username": "els", "password": "geheim123",
                                      "group_ids": [g1["id"], g2["id"]]}).status_code == 200
    u = next(x for x in c.get("/api/users").json() if x["username"] == "els")
    assert u["group_ids"] == [g1["id"], g2["id"]] and u["group_name"] == "Ploeg Amb, Fietsers"
    c.post("/api/logout")
    login(c, "els", "geheim123")
    me = c.get("/api/me").json()
    assert "log.view" in me["perms"] and me["history_hours"] == 24        # som van de groepen
    assert sorted(t["alias"] for t in c.get("/api/trackers").json()) == ["Amb1", "Amb2", "Fiets"]
    tg = {g["name"]: g["trackers"] for g in c.get("/api/tracker-groups").json()}
    assert tg == {"Ziekenwagens": sorted([ids["Amb1"], ids["Amb2"]]), "Brandweer": [ids["Amb2"]]}
    assert c.post("/api/tracker-groups", json={"name": "X"}).status_code == 403
    # een tracker die later in de groep komt, is meteen zichtbaar
    c.post("/api/logout")
    login(c, "admin", "beheerder1")
    c.put(f"/api/tracker-groups/{amb['id']}", json={"name": "Ziekenwagens", "trackers": [ids["Amb1"], ids["Amb2"], ids["Brand"]]})
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


def test_share_with_tracker_group_is_dynamic_and_limited_to_creator(client):
    c, main = client
    login(c, "admin", "beheerder1")
    a = c.post("/api/trackers", json={"pubkey": "a1" * 32, "alias": "A"}).json()["tracker"]["id"]
    b = c.post("/api/trackers", json={"pubkey": "b1" * 32, "alias": "B"}).json()["tracker"]["id"]
    x = c.post("/api/trackers", json={"pubkey": "c1" * 32, "alias": "X"}).json()["tracker"]["id"]
    tg = c.post("/api/tracker-groups", json={"name": "Ploeg", "trackers": [a]}).json()
    # beperkte maker: ziet A en B, niet X
    g = c.post("/api/groups", json={"name": "Deler", "perms": ["map.view", "share.manage"], "all_trackers": False,
                                    "trackers": [a, b]}).json()
    c.post("/api/users", json={"username": "deler", "password": "geheim123", "group_ids": [g["id"]]})
    c.post("/api/logout")
    login(c, "deler", "geheim123")
    url = c.post("/api/shares", json={"name": "Link", "tracker_groups": [tg["id"]], "hours": 12}).json()["url"]
    token = url.rsplit("/", 1)[1]
    c.post("/api/logout")
    c.get(f"/s/{token}")
    assert [t["alias"] for t in c.get("/api/trackers").json()] == ["A"]
    # later komen B (maker ziet die) en X (maker ziet die niet) in de groep
    c.cookies.clear()
    login(c, "admin", "beheerder1")
    c.put(f"/api/tracker-groups/{tg['id']}", json={"name": "Ploeg", "trackers": [a, b, x]})
    c.post("/api/logout")
    c.cookies.clear()
    main._pcache.clear()
    c.get(f"/s/{token}")
    assert sorted(t["alias"] for t in c.get("/api/trackers").json()) == ["A", "B"]


def test_effective_rights_shows_origin(client):
    c, _ = client
    login(c, "admin", "beheerder1")
    a = c.post("/api/trackers", json={"pubkey": "a1" * 32, "alias": "A"}).json()["tracker"]["id"]
    b = c.post("/api/trackers", json={"pubkey": "b1" * 32, "alias": "B"}).json()["tracker"]["id"]
    c.post("/api/trackers", json={"pubkey": "c1" * 32, "alias": "C"})
    tg = c.post("/api/tracker-groups", json={"name": "Ploeg", "trackers": [a]}).json()
    g1 = c.post("/api/groups", json={"name": "G1", "perms": ["map.view"], "all_trackers": False,
                                     "tracker_groups": [tg["id"]], "history_hours": 6}).json()
    g2 = c.post("/api/groups", json={"name": "G2", "perms": ["map.view", "log.view"], "all_trackers": False,
                                     "trackers": [b], "history_hours": 48}).json()
    uid = c.post("/api/users", json={"username": "piet", "password": "geheim123", "group_ids": [g1["id"], g2["id"]]}).json()["id"]
    e = c.get(f"/api/users/{uid}/effective").json()
    perms = {p["id"]: p["via"] for p in e["perms"]}
    assert perms["map.view"] == ["G1", "G2"] and perms["log.view"] == ["G2"] and perms["users.manage"] == []
    tr = {t["alias"]: t for t in e["trackers"]}
    assert tr["A"]["sees"] and tr["A"]["via"] == ["G1: trackergroep Ploeg"]
    assert tr["B"]["sees"] and tr["B"]["via"] == ["G2: losse tracker"]
    assert not tr["C"]["sees"] and e["history_hours"] == 48 and e["history_via"] == ["G2"]


def test_channel_messages_signed_and_grouped(client):
    import asyncio
    c, main = client
    login(c, "admin", "beheerder1")
    tid = c.post("/api/trackers", json={"pubkey": "ab12cd34" + "00" * 28, "alias": "Kanaaltracker"}).json()["tracker"]["id"]
    r = c.post("/api/channels", json={"name": "#ploeg3", "slot": 5})
    assert r.status_code == 200, r.text
    ch = c.get("/api/channels").json()["channels"][0]
    assert ch["secret"] == __import__("hashlib").sha256(b"#ploeg3").hexdigest()[:32] and ch["require_sig"]
    key = c.get(f"/api/trackers/{tid}/authkey").json()["authkey"]
    rest = "3|M|50.93|5.33|40|50|90|80|0.9|1|t|b|1800000000"
    good = main.channel_tag(key, f"ab12cd34|{rest}")
    run = lambda txt: asyncio.run(main.on_channel(5, txt, 1800000000, 5.0, 2))
    run(f"MT: T1C|ab12cd34|00000000|{rest}")                     # foute handtekening
    run(f"MT: T1C|ab12cd34|-|{rest}")                            # niet ondertekend, kanaal eist het
    assert not c.get(f"/api/trackers/{tid}/track?hours=999999").json()
    reasons = [u["reason"] for u in c.get("/api/unknown").json()]
    assert "kanaal #ploeg3: ongeldige handtekening" in reasons and "kanaal #ploeg3: niet ondertekend" in reasons
    run(f"MT: T1C|ab12cd34|{good}|{rest}")
    assert len(c.get(f"/api/trackers/{tid}/track?hours=999999").json()) == 1
    t = next(x for x in c.get("/api/trackers").json() if x["id"] == tid)
    assert t["last_via"] == "kanaal #ploeg3" and t["has_authkey"] and "authkey" not in t
    grp = next(g for g in c.get("/api/tracker-groups").json() if g["name"] == "Kanaal #ploeg3")
    assert grp["trackers"] == [tid]                               # automatisch in de kanaalgroep
    run("Jan: hallo allemaal")                                    # gewone chat: genegeerd
    run(f"MT: T1C|ab12cd34|{good}|{rest}".replace("|5.33|", "|5.34|"))   # gewijzigd: handtekening klopt niet meer
    assert len(c.get(f"/api/trackers/{tid}/track?hours=999999").json()) == 1


def test_channel_keys_only_for_admins_or_own_group(client):
    c, main = client
    login(c, "admin", "beheerder1")
    c.post("/api/channels", json={"name": "#ambu", "slot": 6})
    c.post("/api/channels", json={"name": "#brand", "slot": 7})
    chans = {x["name"]: x for x in c.get("/api/channels").json()["channels"]}
    assert {x["name"] for x in c.get("/api/channels/device").json()} == {"#ambu", "#brand"}   # beheerder: alles
    g = c.post("/api/groups", json={"name": "Ambu-techniek", "perms": ["map.view", "trackers.serial"], "all_trackers": False,
                                    "tracker_groups": [chans["#ambu"]["tracker_group_id"]]}).json()
    c.post("/api/users", json={"username": "tech", "password": "geheim123", "group_ids": [g["id"]]})
    c.post("/api/logout")
    login(c, "tech", "geheim123")
    got = c.get("/api/channels/device").json()
    assert [x["name"] for x in got] == ["#ambu"] and got[0]["secret"]
    assert c.get("/api/channels").status_code == 403                 # volledige lijst alleen voor beheer


def test_group_of_groups_combines_channels(client):
    c, main = client
    login(c, "admin", "beheerder1")
    ids = {n: c.post("/api/trackers", json={"pubkey": k * 32, "alias": n}).json()["tracker"]["id"]
           for n, k in (("A", "a1"), ("B", "b1"), ("C", "c1"), ("D", "d1"))}
    ga = c.post("/api/tracker-groups", json={"name": "Kanaal #ambu", "trackers": [ids["A"]]}).json()
    gb = c.post("/api/tracker-groups", json={"name": "Kanaal #brand", "trackers": [ids["B"]]}).json()
    combo = c.post("/api/tracker-groups", json={"name": "Interventie", "trackers": [ids["C"]],
                                                "includes": [ga["id"], gb["id"]]}).json()
    assert combo["trackers"] == [ids["C"]] and combo["members"] == sorted([ids["A"], ids["B"], ids["C"]])
    g = c.post("/api/groups", json={"name": "Coördinatie", "perms": ["map.view"], "all_trackers": False,
                                    "tracker_groups": [combo["id"]]}).json()
    c.post("/api/users", json={"username": "coord", "password": "geheim123", "group_ids": [g["id"]]})
    # later komt D op kanaal #ambu: meteen zichtbaar via de gecombineerde groep
    c.put(f"/api/tracker-groups/{ga['id']}", json={"name": "Kanaal #ambu", "trackers": [ids["A"], ids["D"]]})
    c.post("/api/logout")
    login(c, "coord", "geheim123")
    assert sorted(t["alias"] for t in c.get("/api/trackers").json()) == ["A", "B", "C", "D"]
    # een ingesloten groep verwijderen haalt hem uit de combinatie
    c.post("/api/logout")
    login(c, "admin", "beheerder1")
    c.delete(f"/api/tracker-groups/{gb['id']}")
    combo2 = next(x for x in c.get("/api/tracker-groups").json() if x["id"] == combo["id"])
    assert combo2["includes"] == [ga["id"]] and ids["B"] not in combo2["members"]


def test_offline_bundle_and_maps(client, tmp_path):
    c, main = client
    assert c.get("/offline").status_code == 200                     # app opent zonder login
    assert c.get("/offline-sw.js").headers["service-worker-allowed"] == "/"
    assert c.get("/api/offline/bundle").status_code == 401
    login(c, "admin", "beheerder1")
    tid = c.post("/api/trackers", json={"pubkey": "ab12cd34" + "00" * 28, "alias": "Kanaaltracker"}).json()["tracker"]["id"]
    key = c.get(f"/api/trackers/{tid}/authkey").json()["authkey"]
    c.post("/api/channels", json={"name": "#ambu", "slot": 6})
    c.post("/api/channels", json={"name": "#brand", "slot": 7})
    chans = {x["name"]: x for x in c.get("/api/channels").json()["channels"]}
    b = c.get("/api/offline/bundle").json()
    assert {x["name"] for x in b["channels"]} == {"#ambu", "#brand"}
    assert b["trackers"] == [{"pk8": "ab12cd34", "alias": "Kanaaltracker", "color": b["trackers"][0]["color"],
                              "icon": b["trackers"][0]["icon"], "authkey": key}]
    # kijker van groep #ambu: alleen dat kanaal, geen authsleutels
    g = c.post("/api/groups", json={"name": "Ambu", "perms": ["map.view"], "all_trackers": False,
                                    "tracker_groups": [chans["#ambu"]["tracker_group_id"]]}).json()
    c.put(f"/api/tracker-groups/{chans['#ambu']['tracker_group_id']}", json={"name": "Kanaal #ambu", "trackers": [tid]})
    c.post("/api/users", json={"username": "kijker", "password": "geheim123", "group_ids": [g["id"]]})
    c.post("/api/logout")
    login(c, "kijker", "geheim123")
    b = c.get("/api/offline/bundle").json()
    assert [x["name"] for x in b["channels"]] == ["#ambu"]
    assert [t["pk8"] for t in b["trackers"]] == ["ab12cd34"] and "authkey" not in b["trackers"][0]
    # kaarten: alleen bestanden die echt bestaan
    off = __import__("pathlib").Path(main.S.cfg.tiles_dir) / "offline"
    off.mkdir(parents=True, exist_ok=True)
    (off / "benelux-z10.pmtiles").write_bytes(b"x" * 1234)
    maps = c.get("/api/offline/maps").json()
    assert [(m["key"], m["size"], m["url"]) for m in maps] == [("benelux-z10", 1234, "/tiles/offline/benelux-z10.pmtiles")]
