"""1.5: glijdende sessie, /login?next=, /start en de ene app (sw.js), bladeren in logboek en audit."""
import os
import re
import shutil
import time

import pytest
from fastapi.testclient import TestClient

from meshtrack import auth

SECRET = "test-geheim"


@pytest.fixture()
def client(tmp_path):
    cfg = tmp_path / "config.yaml"
    cfg.write_text(f"""
mesh: {{host: 127.0.0.1, port: 1}}
db_path: {tmp_path / 'mt.sqlite3'}
tiles_dir: {tmp_path / 'tiles'}
openhop_db: {tmp_path / 'geen.db'}
auth: {{user: admin, password_hash: "{auth.hash_password('beheerder1')}", session_secret: "{SECRET}"}}
""".replace("\\\\", "/"), encoding="utf-8")
    os.environ["MESHTRACK_CONFIG"] = str(cfg)
    from meshtrack import main
    main._pcache.clear()
    main._fails.clear()
    with TestClient(main.app) as c:
        yield c, main


def login(c, user="admin", pw="beheerder1", **extra):
    r = c.post("/api/login", json={"user": user, "password": pw, **extra})
    assert r.status_code == 200, r.text
    return r


def session_cookies(r):
    return [v for v in r.headers.get_list("set-cookie") if v.startswith(auth.COOKIE + "=")]


def old_token(main, days_old: float, gen: int = 0, user="admin"):
    """Sessie die days_old dagen geleden werd aangemaakt."""
    tok = auth.make_session(user, SECRET, main.S.cfg.session_days, gen)
    body = auth._unb64(tok.split(".")[0]).decode()
    u, g, exp = body.split("|")
    body = f"{u}|{g}|{int(exp) - int(days_old * 86400)}"
    import hashlib, hmac
    sig = hmac.new(SECRET.encode(), body.encode(), hashlib.sha256).digest()
    return auth._b64(body.encode()) + "." + auth._b64(sig)


# ---- glijdende sessie ------------------------------------------------------------

def test_needs_renewal_once_per_day():
    tok = auth.make_session("a", SECRET, 30)
    assert not auth.needs_renewal(tok, SECRET, 30)
    assert auth.needs_renewal(tok, SECRET, 30, now=time.time() + 86400 + 5)
    assert not auth.needs_renewal(tok, "ander-geheim", 30, now=time.time() + 86400 + 5)
    assert not auth.needs_renewal(None, SECRET, 30)
    assert auth.session_info(tok, SECRET)[:2] == ("a", 0)


def test_fresh_session_not_rewritten(client):
    c, _ = client
    login(c)
    r = c.get("/api/me")
    assert r.status_code == 200 and not session_cookies(r)


def test_old_session_is_extended(client):
    c, main = client
    login(c)
    c.cookies.set(auth.COOKIE, old_token(main, 3))
    r = c.get("/api/me")
    assert r.status_code == 200
    sc = session_cookies(r)
    assert len(sc) == 1 and f"Max-Age={main.S.cfg.session_days * 86400}" in sc[0] and "HttpOnly" in sc[0]
    new = sc[0].split(";")[0].split("=", 1)[1].strip('"')
    user, gen, exp = auth.session_info(new, SECRET)
    assert (user, gen) == ("admin", 0) and exp > time.time() + (main.S.cfg.session_days - 1) * 86400
    # daarna een dag niets meer
    c.cookies.set(auth.COOKIE, new)
    assert not session_cookies(c.get("/api/me"))


def test_logout_and_session_gen_still_win(client):
    c, main = client
    login(c)
    c.cookies.set(auth.COOKIE, old_token(main, 3))
    r = c.post("/api/logout")
    sc = session_cookies(r)
    assert len(sc) == 1 and ('Max-Age=0' in sc[0] or 'expires=' in sc[0].lower())   # alleen het wissen
    # verouderde generatie: geen verlenging, gewoon niet ingelogd
    uid = main.S.db.user_by_name("admin")["id"]
    main.S.db.update_user(uid, password_hash=auth.hash_password("nieuw-wachtwoord"))
    main._pcache.clear()
    c.cookies.set(auth.COOKIE, old_token(main, 3, gen=0))
    r = c.get("/api/me")
    assert r.status_code == 401 and not session_cookies(r)


def test_share_link_not_turned_into_session(client):
    c, main = client
    login(c)
    a = c.post("/api/trackers", json={"pubkey": "aa" * 32, "alias": "A"}).json()["tracker"]["id"]
    url = c.post("/api/shares", json={"name": "kiosk", "trackers": [a], "valid_hours": 2}).json()["url"]
    c.post("/api/logout")
    c.cookies.clear()
    c.get(f"/s/{url.rsplit('/', 1)[1]}", follow_redirects=False)
    r = c.get("/api/me")
    assert r.json()["kind"] == "share" and not session_cookies(r)


# ---- /login?next= ----------------------------------------------------------------

@pytest.mark.parametrize("val,ok", [
    ("/admin", True), ("/log?x=1&y=2", True), ("/", True),
    ("//evil.example", False), ("/\\evil.example", False), ("https://evil.example", False),
    ("evil", False), ("", False), (None, False), ("/a\r\nSet-Cookie: x", False), ("/" + "a" * 2001, False),
])
def test_safe_next(val, ok):
    assert auth.safe_next(val) == (val if ok else None)


def test_unauthenticated_page_redirects_with_next(client):
    c, _ = client
    r = c.get("/admin?tab=sims", follow_redirects=False)
    assert r.status_code in (302, 307) and r.headers["location"] == "/login?next=%2Fadmin%3Ftab%3Dsims"
    assert c.get("/", follow_redirects=False).headers["location"] == "/login"
    assert c.get("/api/me", follow_redirects=False).status_code == 401


def test_login_returns_checked_next(client):
    c, _ = client
    assert login(c, next="/log").json()["next"] == "/log"
    c.cookies.clear()
    assert login(c, next="//evil.example").json()["next"] == "/"
    c.cookies.clear()
    r = c.post("/api/login?next=/system", json={"user": "admin", "password": "beheerder1"})
    assert r.json()["next"] == "/system"
    c.cookies.clear()
    assert login(c).json()["next"] == "/"


def test_login_page_when_already_logged_in(client):
    c, _ = client
    assert c.get("/login?next=/admin", follow_redirects=False).status_code == 200    # niet aangemeld
    login(c)
    r = c.get("/login?next=/admin", follow_redirects=False)
    assert r.status_code in (302, 307) and r.headers["location"] == "/admin"
    r = c.get("/login?next=//evil.example", follow_redirects=False)
    assert r.status_code == 200
    assert c.get("/login", follow_redirects=False).status_code == 200


# ---- /start, oude workers, sw.js ---------------------------------------------------

@pytest.fixture()
def fake_static(client, tmp_path, monkeypatch):
    """Kopie van static/ met een eigen start.html en een sw.js met PAGES_V (los van de frontend)."""
    c, main = client
    d = tmp_path / "static"
    shutil.copytree(main.STATIC, d, ignore=shutil.ignore_patterns("firmware", "vendor", "*.pdf"))
    shutil.copytree(main.STATIC / "vendor", d / "vendor")
    (d / "start.html").write_text('<!doctype html><html><head><title>Start</title>'
                                  '<script src="/static/start.js"></script></head><body>start</body></html>',
                                  encoding="utf-8")
    (d / "start.js").write_text("// start\n", encoding="utf-8")
    sw = (d / "sw.js").read_text(encoding="utf-8")
    if 'const PAGES_V = "";' not in sw:
        sw = sw.replace("const ASSETS = [];", 'const ASSETS = [];\nconst PAGES_V = "";', 1)
    (d / "sw.js").write_text(sw, encoding="utf-8")
    monkeypatch.setattr(main, "STATIC", d)
    return c, main, d


def test_start_public_no_cache(fake_static):
    c, _, _ = fake_static
    r = c.get("/start", follow_redirects=False)
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/html")
    assert r.headers["cache-control"] == "no-cache" and "set-cookie" not in r.headers
    assert 'href="/manifest.webmanifest"' in r.text                 # deel van de ene app
    assert re.search(r'/static/start\.js\?v=\d+', r.text)


def test_start_missing_is_404_not_500(fake_static):
    c, _, d = fake_static
    (d / "start.html").unlink()
    assert c.get("/start", follow_redirects=False).status_code == 404


def test_offline_and_tracker_still_work(client):
    c, _ = client
    for p in ("/offline", "/tracker", "/offline.webmanifest", "/tracker.webmanifest"):
        r = c.get(p, follow_redirects=False)
        assert r.status_code == 200, p
    r = c.get("/tracker-sw.js")
    assert r.status_code == 200 and r.headers["service-worker-allowed"] == "/tracker"
    r = c.get("/offline-sw.js")
    assert r.status_code == 200 and r.headers["service-worker-allowed"] == "/"


def test_old_workers_get_kill_switch_when_file_gone(fake_static):
    c, _, d = fake_static
    for name, scope in (("offline-sw.js", "/"), ("tracker-sw.js", "/tracker")):
        (d / name).unlink(missing_ok=True)
        r = c.get("/" + name, follow_redirects=False)
        assert r.status_code == 200 and r.headers["content-type"].startswith("text/javascript")
        assert r.headers["service-worker-allowed"] == scope and r.headers["cache-control"] == "no-cache"
        assert "unregister()" in r.text


def test_sw_lists_new_assets_and_pages_v(fake_static):
    c, main, d = fake_static
    js = c.get("/sw.js").text
    build = re.search(r'const BUILD = "([0-9a-f]{12})";', js).group(1)
    assert f'const PAGES_V = "{build}";' in js
    assets = re.search(r"const ASSETS = (\[.*?\]);", js).group(1)
    for name in ("offline.js", "tracker.js", "tracker.css", "start.js"):
        if (d / name).exists():
            assert f'"/static/{name}?v=' in assets, name
    assert "appnav.js" not in assets or (d / "appnav.js").exists()        # ontbrekend = overgeslagen
    assert "offline.css" not in assets or (d / "offline.css").exists()
    import json
    for u in json.loads(assets):
        assert (d / u.split("?")[0][len("/static/"):]).exists(), u   # /static zelf wijst naar de echte map


@pytest.mark.parametrize("name", ["start.html", "offline.html", "tracker.html", "offline-site.html", "tracker.js"])
def test_sw_build_follows_pages(fake_static, name):
    c, _, d = fake_static
    b = lambda: re.search(r'const BUILD = "(\w+)";', c.get("/sw.js").text).group(1)
    one = b()
    st = (d / name).stat()
    os.utime(d / name, (st.st_atime, st.st_mtime + 7))
    assert b() != one


def test_sw_tolerates_missing_assets(fake_static):
    c, _, d = fake_static
    (d / "start.js").unlink()
    (d / "start.html").unlink()
    r = c.get("/sw.js")
    assert r.status_code == 200 and "start.js" not in r.text.split("const ASSETS = ", 1)[1].split(";", 1)[0]


# ---- logboek en audit bladeren -----------------------------------------------------

def test_audit_paging(client):
    c, main = client
    login(c)
    for i in range(30):
        main.S.db.audit("test", "regel", str(i))
    full = c.get("/api/audit").json()
    assert len(full) == len(main.S.db.audit_log(1000))                      # standaard ongewijzigd
    first = c.get("/api/audit?limit=10").json()
    second = c.get(f"/api/audit?limit=10&before_id={first[-1]['id']}").json()
    assert len(first) == len(second) == 10 and second[0]["id"] < first[-1]["id"]
    assert c.get("/api/audit?limit=10&offset=10").json() == second
    assert [r["id"] for r in first + second] == [r["id"] for r in full[:20]]


def test_events_paging(client):
    c, main = client
    login(c)
    tid = main.S.db.add_tracker("ab" * 32, "T1")
    now = int(time.time())
    for i in range(25):
        main.S.db.add_position(tid, {"ts": now - 1000 + i * 10, "rx_ts": now - 1000 + i * 10, "seq": i,
                                     "state": "M", "lat": 50.9, "lon": 5.3, "bat": 80, "suspect": 0, "raw": ""})
    q = "/api/events?types=M&hours=1"
    full = c.get(q).json()
    assert len(full["events"]) == 25 and full["more"] is False and full["offset"] == 0
    p1 = c.get(q + "&limit=10").json()
    assert len(p1["events"]) == 10 and p1["more"] is True
    p3 = c.get(q + f"&limit=10&offset=20&since={p1['since']}&until={p1['until']}").json()
    assert len(p3["events"]) == 5 and p3["more"] is False
    p2 = c.get(q + f"&limit=10&offset=10&since={p1['since']}&until={p1['until']}").json()
    seen = [e["id"] for e in p1["events"] + p2["events"] + p3["events"]]
    assert seen == [e["id"] for e in full["events"]] and len(set(seen)) == 25
