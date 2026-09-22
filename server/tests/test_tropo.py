"""Tropo-veld voor MeshChat (app/tropo.py).

Waarom dit een test verdient. Het veld gaat als één JSON naar elke MeshChat-client en
vervangt daar honderden aanvragen aan Open-Meteo; een fout in de rastervolgorde of de
uurindex tekent stilletjes de verkeerde kleur op de verkeerde plek en niemand merkt het
aan een foutmelding. De invarianten: de gradiëntformule is die van MeshChat (bekende
waarden), het raster heeft de aangekondigde vorm en volgorde (noord→zuid, west→oost),
een blok Open-Meteo-antwoorden wordt per punt en per uur omgezet, /api/tropo geeft 503
zonder gegevens en daarna een compact veld met CORS en cache-koppen, ook op de
chat-hostnaam.
"""
import os

os.environ.setdefault("MM_MQTT_HOST", "127.0.0.1")
os.environ["MM_TROPO"] = "0"

from datetime import datetime, timezone  # noqa: E402

from fastapi.testclient import TestClient  # noqa: E402

from app import tropo  # noqa: E402
from app.main import CHAT_HOST, app  # noqa: E402

client = TestClient(app)


def test_refractivity_en_gradient_bekende_waarden():
    # Standaardatmosfeer op zeeniveau: N ≈ 300-320.
    n0 = tropo.refractivity(15.0, 70.0, 1013.0)
    assert 300 < n0 < 330
    hourly = {
        "temperature_1000hPa": [14.1], "relative_humidity_1000hPa": [74], "geopotential_height_1000hPa": [255],
        "temperature_925hPa": [10.0], "relative_humidity_925hPa": [52], "geopotential_height_925hPa": [908],
        "temperature_850hPa": [9.8], "relative_humidity_850hPa": [1], "geopotential_height_850hPa": [1609],
    }
    g = tropo.gradient(hourly, 0)
    # 1000→925: (283.2-323.9)/0.653 ≈ -62; 925→850: (233.7-283.2)/0.701 ≈ -71 → steilste ≈ -71
    assert -75 < g < -65
    assert tropo.gradient({"temperature_1000hPa": [None]}, 0) is None


def test_raster_vorm_en_volgorde():
    pts = tropo.points()
    assert len(pts) == tropo.NX * tropo.NY
    assert pts[0] == (tropo.NORTH, tropo.WEST)            # noordwest eerst
    assert pts[1] == (tropo.NORTH, tropo.WEST + tropo.STEP)  # dan naar het oosten
    assert pts[-1] == (tropo.SOUTH, tropo.EAST)           # zuidoost laatst


def _fake_fetch(url):
    # Zoveel antwoorden als er breedtegraden in de URL staan; 48 uur, gradiënt afhankelijk van de lengtegraad.
    from urllib.parse import parse_qs, urlparse
    q = parse_qs(urlparse(url).query)
    lats = q["latitude"][0].split(","); lons = q["longitude"][0].split(",")
    out = []
    for la, lo in zip(lats, lons):
        rh = 1 if float(lo) > 5 else 60   # oostelijk droog boven → steile gradiënt
        h = {"time": [f"2026-09-22T{i % 24:02d}" if i < 24 else f"2026-09-23T{i - 24:02d}" for i in range(48)]}
        for p, t in ((1000, 14.0), (925, 10.0), (850, 9.0)):
            h[f"temperature_{p}hPa"] = [t] * 48
            h[f"relative_humidity_{p}hPa"] = [70 if p == 1000 else rh] * 48
            h[f"geopotential_height_{p}hPa"] = [{1000: 250, 925: 900, 850: 1600}[p]] * 48
        out.append({"latitude": float(la), "longitude": float(lo), "hourly": h})
    return out


def test_fetch_field_bouwt_veld_per_punt_en_uur():
    field = tropo.fetch_field(fetch=_fake_fetch, pause=0)
    assert len(field["times"]) == 48
    assert len(field["grad"]) == tropo.NX * tropo.NY
    west = field["grad"][0]; east = field["grad"][tropo.NX - 1]
    assert len(west) == 48 and west[0] > east[0]   # oosten steiler (negatiever)
    now = datetime(2026, 9, 22, 10, 30, tzinfo=timezone.utc)
    s0 = tropo.slice_field(field, 0, now); s6 = tropo.slice_field(field, 6, now)
    assert s0["model_time"] == "2026-09-22T10" and s6["model_time"] == "2026-09-22T16"
    assert s0["nx"] == tropo.NX and len(s0["grad"]) == tropo.NX * tropo.NY
    assert tropo.slice_field(field, 30, datetime(2026, 9, 23, 22, tzinfo=timezone.utc))["model_time"] == "2026-09-23T23"


def test_api_tropo_zonder_en_met_veld():
    tropo.set_field(None)
    r = client.get("/api/tropo")
    assert r.status_code == 503 and r.headers.get("retry-after") == "60"
    tropo.set_field(tropo.fetch_field(fetch=_fake_fetch, pause=0))
    r = client.get("/api/tropo?h=6")
    assert r.status_code == 200
    body = r.json()
    assert body["h"] == 6 and body["step"] == tropo.STEP and len(body["grad"]) == tropo.NX * tropo.NY
    assert r.headers.get("access-control-allow-origin") == "*"   # ook voor het losse HTML-bestand
    assert "max-age=600" in r.headers.get("cache-control", "")
    assert client.get("/api/tropo?h=99").status_code == 422
    # Op de chat-hostnaam moet het veld ook bereikbaar zijn (de rest is daar 404).
    r = client.get("/api/tropo", headers={"host": CHAT_HOST})
    assert r.status_code == 200
    assert client.get("/api/v1/repeaters", headers={"host": CHAT_HOST}).status_code == 404
