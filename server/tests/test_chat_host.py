"""MeshChat op zijn eigen hostnaam (main.py: _chat_host_response).

Waarom dit een test verdient. De hostnaam-afhandeling zit vóór alle routes en
beslist per verzoek of het de chat, de tiles of een 404 wordt. Een vergissing
daar is onzichtbaar op meshmanager.net zelf (alles werkt) en breekt alleen
chat.meshmanager.net -- precies de plek die niemand in de terminal ziet. De
invarianten: op de chat-host geeft / de chat, blijven sw.js en het manifest
bereikbaar (anders geen PWA), blijft /tiles doorgaan (anders geen kaart), is de
rest 404 (geen beheerpagina's op een tweede origin) en krijgt alles de
beveiligingskoppen. En op de gewone host verandert er niets.
"""
import os

os.environ.setdefault("MM_MQTT_HOST", "127.0.0.1")

from fastapi.testclient import TestClient  # noqa: E402

from app.main import CHAT_HOST, app  # noqa: E402

client = TestClient(app)
CHAT = {"host": CHAT_HOST}


def test_chat_host_root_is_de_chat():
    r = client.get("/", headers=CHAT)
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]
    assert "MeshChat" in r.text
    assert r.headers.get("cache-control") == "no-cache"
    assert "geolocation=(self)" in r.headers.get("permissions-policy", "")
    assert "content-security-policy" in r.headers


def test_chat_host_pwa_bestanden():
    assert client.get("/sw.js", headers=CHAT).status_code == 200
    assert client.get("/manifest.webmanifest", headers=CHAT).status_code == 200


def test_chat_host_chat_pad_stuurt_door_naar_wortel():
    r = client.get("/chat/", headers=CHAT, follow_redirects=False)
    assert r.status_code == 301
    assert r.headers["location"] == "/"
    r = client.get("/chat/sw.js", headers=CHAT, follow_redirects=False)
    assert r.status_code == 301 and r.headers["location"] == "/sw.js"


def test_chat_host_rest_is_404():
    for p in ("/admin", "/api/v1/ping", "/static/app.js", "/meshmoni"):
        assert client.get(p, headers=CHAT).status_code == 404, p


def test_chat_host_tiles_gaan_door():
    # OPTIONS-preflight bestaat als route; op de chat-host moet die ook antwoorden
    r = client.options("/tiles/basemap.pmtiles", headers={**CHAT, "Origin": "null", "Access-Control-Request-Method": "GET"})
    assert r.status_code == 204
    assert r.headers.get("access-control-allow-origin") == "*"


def test_gewone_host_ongewijzigd():
    r = client.get("/chat/", follow_redirects=False)
    assert r.status_code == 200
    assert client.get("/", follow_redirects=False).status_code in (200, 302, 307)
