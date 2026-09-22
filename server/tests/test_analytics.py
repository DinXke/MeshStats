"""Bezoekcijfers naar Matomo (app/analytics.py).

Waarom dit een test verdient. Het script wordt door élke pagina geladen en door de
Content-Security-Policy toegelaten; een fout hier is ofwel een site die stilletjes
niets meer telt, ofwel een CSP die een vreemde herkomst toelaat. De invarianten:
zonder MM_MATOMO_URL wordt er niets geladen en blijft de CSP dicht; met de URL
staat precies die herkomst in script-src, img-src en connect-src; de chat-hostnaam
krijgt zijn eigen site-id en is ook op die hostnaam bereikbaar; en er staan geen
cookies of DNT-negerende instellingen in de startcode.
"""
import os

os.environ.setdefault("MM_MQTT_HOST", "127.0.0.1")
os.environ["MM_TROPO"] = "0"

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app import analytics  # noqa: E402
from app.main import CHAT_HOST, app  # noqa: E402

client = TestClient(app)
CHAT = {"host": CHAT_HOST}


@pytest.fixture
def matomo(monkeypatch):
    monkeypatch.setenv("MM_MATOMO_URL", "https://matomo.example/")
    monkeypatch.setenv("MM_MATOMO_SITE_ID", "2")
    monkeypatch.setenv("MM_MATOMO_CHAT_SITE_ID", "3")


def test_uit_zonder_url(monkeypatch):
    monkeypatch.delenv("MM_MATOMO_URL", raising=False)
    assert not analytics.enabled()
    r = client.get("/analytics.js")
    assert r.status_code == 200
    assert "_paq" not in r.text and "matomo.php" not in r.text and "matomo.js" not in r.text
    assert r.headers["cache-control"] == "no-store"
    # CSP blijft dicht: geen vreemde herkomst erbij.
    csp = client.get("/").headers.get("content-security-policy", "")
    assert "matomo" not in csp
    assert '<script src="/analytics.js"' not in client.get("/").text


def test_startcode_en_csp_met_url(matomo):
    r = client.get("/analytics.js")
    assert r.status_code == 200
    assert "application/javascript" in r.headers["content-type"]
    assert "max-age=14400" in r.headers["cache-control"]
    assert '"https://matomo.example/"' in r.text
    assert '_paq.push(["setSiteId", "2"])' in r.text
    # Privacy: geen cookies, DNT gerespecteerd.
    assert '"disableCookies"' in r.text and '"setDoNotTrack", true' in r.text
    page = client.get("/")
    assert '<script src="/analytics.js"' in page.text
    csp = page.headers["content-security-policy"]
    for directive in ("script-src", "img-src", "connect-src"):
        deel = [d for d in csp.split(";") if d.strip().startswith(directive)][0]
        assert "https://matomo.example" in deel, directive
    # En niet ergens anders binnengeslopen:
    assert "default-src 'self';" in csp


def test_chat_host_eigen_site_id(matomo):
    r = client.get("/analytics.js", headers=CHAT)
    assert r.status_code == 200
    assert '_paq.push(["setSiteId", "3"])' in r.text          # chat telt apart
    assert '_paq.push(["setSiteId", "2"])' in client.get("/analytics.js").text
    # De chat-hostnaam serveert verder alleen de app; /analytics.js hoort erbij.
    assert client.get("/api/v1/repeaters", headers=CHAT).status_code == 404
    assert "https://matomo.example" in client.get("/", headers=CHAT).headers["content-security-policy"]


def test_chat_valt_terug_op_gewone_site_id(monkeypatch, matomo):
    monkeypatch.delenv("MM_MATOMO_CHAT_SITE_ID", raising=False)
    assert '_paq.push(["setSiteId", "2"])' in client.get("/analytics.js", headers=CHAT).text


def test_url_wordt_genormaliseerd(monkeypatch):
    monkeypatch.setenv("MM_MATOMO_URL", "https://matomo.example///")
    assert analytics.matomo_url() == "https://matomo.example/"
    assert '"https://matomo.example/"' in analytics.snippet()
