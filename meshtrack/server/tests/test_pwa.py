"""De volledige site als app (PWA): manifest, service worker en de pagina "geen verbinding".
Alles openbaar en zonder gegevens; de API blijft dicht."""
import os
import re

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


def test_manifest_public_and_valid(client):
    c, _ = client
    r = c.get("/manifest.webmanifest", follow_redirects=False)
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("application/manifest+json")
    assert r.headers["cache-control"] == "no-cache"
    j = r.json()
    assert (j["id"], j["start_url"], j["scope"], j["display"]) == ("/", "/", "/", "standalone")
    assert j["name"] == "MeshTrack" and j["short_name"] == "MeshTrack"
    assert j["theme_color"] == "#0b6e4f" and j["background_color"] == "#f4f3f0"
    sizes = {(i["sizes"], i["purpose"]) for i in j["icons"]}
    assert {("192x192", "any"), ("512x512", "any"), ("512x512", "maskable")} <= sizes
    for i in j["icons"]:
        assert c.get(i["src"]).status_code == 200
    assert [s["url"] for s in j["shortcuts"]] == ["/", "/tracker", "/offline", "/statistieken"]
    assert [s["name"] for s in j["shortcuts"]] == ["Kaart", "Tracker live", "Offline-kaart", "Statistieken"]


def test_offline_app_keeps_own_manifest(client):
    c, _ = client
    r = c.get("/offline.webmanifest")
    assert r.status_code == 200 and r.headers["content-type"].startswith("application/manifest+json")
    assert (r.json()["start_url"], r.json()["scope"], r.json()["id"]) == ("/offline", "/offline", "/offline")
    page = c.get("/offline").text
    assert 'href="/offline.webmanifest"' in page and 'href="/manifest.webmanifest"' not in page
    sw = c.get("/offline-sw.js").text
    assert '"/offline.webmanifest"' in sw and '"/manifest.webmanifest"' not in sw
    # ruimt alleen de eigen caches op
    assert 'k.startsWith("mt-offline-")' in sw
    t = c.get("/tracker").text
    assert 'href="/tracker.webmanifest"' in t and 'href="/manifest.webmanifest"' not in t


def test_service_worker_public_with_headers(client):
    c, main = client
    r = c.get("/sw.js", follow_redirects=False)
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/javascript")
    assert r.headers["service-worker-allowed"] == "/"
    assert r.headers["cache-control"] == "no-cache"
    assert "set-cookie" not in r.headers
    js = r.text
    build = re.search(r'const BUILD = "([0-9a-f]{12})";', js)
    assert build, "BUILD niet ingevuld"
    assets = re.search(r"const ASSETS = (\[.*?\]);", js).group(1)
    assert '"/static/common.js?v=' in assets and '"/static/vendor/maplibre-gl.js?v=' in assets
    assert '"/static/icon-192.png"' in assets
    for bad in ("/api/", "/tiles/", "/login", "/firmware/", ".pdf", ".html"):
        assert bad not in assets
    # elke vooraf bewaarde URL bestaat, zonder login
    import json
    for u in json.loads(assets):
        assert c.get(u).status_code == 200, u
    # dezelfde ?v= als de pagina's, zodat de cache meteen raak is
    v = re.search(r'/static/style\.css\?v=(\d+)', assets).group(1)
    assert f"/static/style.css?v={v}" in c.get("/offline-site.html").text
    # stabiel zolang er niets wijzigt
    assert c.get("/sw.js").text == js
    # alleen de eigen caches opruimen
    assert 'k.startsWith("mt-site-")' in js and "mt-site-v1" in js


def test_sw_build_changes_with_assets(client, tmp_path, monkeypatch):
    c, main = client
    one = c.get("/sw.js").text
    f = main.STATIC / "common.js"
    st = f.stat()
    try:
        os.utime(f, (st.st_atime, st.st_mtime + 5))
        two = c.get("/sw.js").text
    finally:
        os.utime(f, (st.st_atime, st.st_mtime))
    b = lambda s: re.search(r'const BUILD = "(\w+)";', s).group(1)
    assert b(one) != b(two)


def test_offline_site_page_public_without_data(client):
    c, _ = client
    r = c.get("/offline-site.html", follow_redirects=False)
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/html")
    assert r.headers["cache-control"] == "no-cache"
    assert "set-cookie" not in r.headers
    assert "Geen verbinding met de server" in r.text
    assert 'href="/offline"' in r.text and 'href="/tracker"' in r.text and "Opnieuw proberen" in r.text
    # alleen de openbare gezondheidscheck (om te weten of de server terug is), verder geen API
    assert set(re.findall(r"/api/[\w/]+", r.text)) == {"/api/health"}


def test_pages_link_site_manifest(client):
    c, _ = client
    login = c.get("/login").text
    assert login.count('rel="manifest"') == 1 and 'href="/manifest.webmanifest"' in login
    assert '<meta name="theme-color" content="#0b6e4f">' in login
    c.post("/api/login", json={"user": "admin", "password": "beheerder1"})
    for p in ("/", "/admin", "/devices", "/users", "/log", "/system", "/kanalen", "/statistieken", "/help"):
        r = c.get(p, follow_redirects=False)
        assert r.status_code == 200, p
        assert r.text.count('rel="manifest"') == 1 and 'href="/manifest.webmanifest"' in r.text, p
        assert 'name="theme-color"' in r.text, p
        assert r.text.index('rel="manifest"') < r.text.index("</head>"), p


def test_api_still_closed(client):
    c, _ = client
    for p in ("/api/trackers", "/api/me", "/api/status"):
        assert c.get(p).status_code == 401, p
    assert c.get("/", follow_redirects=False).status_code in (302, 307)
