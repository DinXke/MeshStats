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
