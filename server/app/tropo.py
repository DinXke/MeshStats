"""Tropo-ducting-veld voor MeshChat: van ICON-EU (DWD, zie icon.py), met Open-Meteo als terugval.

Waarom op de server. MeshChat rekende dit eerst in de browser uit en vroeg Open-Meteo
per gebruiker en per kaartbeeld tot 220 rasterpunten op. Open-Meteo telt elk punt als
een aanvraag en begrenst per IP (600 per minuut); wie een paar keer pande, kreeg 429.
Hier halen we het veld één keer per uur op voor heel West-Europa (het gebied van
basemap.pmtiles) en serveren het als één klein JSON-bestand: elke client doet nog één
aanvraag per uur, en die gaat naar ons. Het losse HTML-bestand (file://) mag het ook
ophalen (CORS) en valt terug op Open-Meteo zelf als deze server onbereikbaar is.

Wat er berekend wordt. Per rasterpunt en per modeluur de refractiviteit
N = 77.6·P/T + 3.73e5·e/T² op 1000, 925 en 850 hPa en daaruit de steilste verticale
gradiënt dN/dh (N-eenheden per km) tussen opeenvolgende niveaus. Normaal ≈ -40;
-79..-157 superrefractie; onder -157 ducting. Dezelfde formule als in MeshChat
(src/tropo.js), zodat server en terugvalpad dezelfde kleuren geven.

Gewicht bij Open-Meteo: 3 niveaus × 3 variabelen = 9 variabelen ≤ 10, dus 1 per punt;
1610 punten in 17 blokken van 100. Open-Meteo telt per punt, met een daglimiet van
10.000 per IP: elk uur ophalen (38.640 per dag) zou dus 429 geven. Daarom om de 6 uur
(6.440 per dag); elk antwoord bevat 48 uur voorspelling en slice_field kiest daaruit
het gevraagde uur, dus tussen twee ophaalbeurten blijft het veld bruikbaar. De server
deelt bovendien zijn publieke IP met de browsers thuis die het terugvalpad gebruiken.
"""
import json
import os
import math
import threading
import time
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Query, Response

from . import icon

router = APIRouter()

LEVELS = (1000, 925, 850)
STEP = 0.5
# Het gebied van de vector-tiles (basemap.pmtiles): lon -7..15.2, lat 42..59.
WEST, EAST, SOUTH, NORTH = -7.0, 15.5, 42.0, 59.0
NX = int(round((EAST - WEST) / STEP)) + 1
NY = int(round((NORTH - SOUTH) / STEP)) + 1
CHUNK = 100
CHUNK_PAUSE_S = 3.0
FIRST_RUN_DELAY_S = 20
INTERVAL_S = 6 * 3600     # Open-Meteo-terugval: hoogstens om de 6 uur (daglimiet per IP)
RETRY_S = 900
POLL_S = 1800             # om het half uur kijken of er een nieuwe ICON-EU-run is
SOURCE = os.environ.get("MM_TROPO_SOURCE", "icon")   # icon | openmeteo
API = "https://api.open-meteo.com/v1/forecast"

_lock = threading.Lock()
_field = None          # {"times": [...], "grad": [[48 floats|None] * NX*NY], "fetched": iso}
_thread = None
_last_error = None


def refractivity(t_c, rh, p_hpa):
    tk = t_c + 273.15
    es = 6.112 * math.exp(17.67 * t_c / (t_c + 243.5))
    e = max(0.0, min(100.0, rh)) / 100.0 * es
    return 77.6 * p_hpa / tk + 3.73e5 * e / (tk * tk)


def gradient(hourly, ti):
    """Steilste dN/dh (N/km) tussen opeenvolgende drukniveaus op uurindex ti, of None."""
    best = None
    for a, b in zip(LEVELS, LEVELS[1:]):
        try:
            t1, t2 = hourly[f"temperature_{a}hPa"][ti], hourly[f"temperature_{b}hPa"][ti]
            r1, r2 = hourly[f"relative_humidity_{a}hPa"][ti], hourly[f"relative_humidity_{b}hPa"][ti]
            z1, z2 = hourly[f"geopotential_height_{a}hPa"][ti], hourly[f"geopotential_height_{b}hPa"][ti]
        except (KeyError, IndexError, TypeError):
            continue
        if None in (t1, t2, r1, r2, z1, z2):
            continue
        dz = (z2 - z1) / 1000.0
        if dz < 0.05:
            continue
        g = (refractivity(t2, r2, b) - refractivity(t1, r1, a)) / dz
        if best is None or g < best:
            best = g
    return None if best is None else round(best, 1)


def points():
    """Rasterpunten in rijvolgorde: van noord naar zuid, per rij van west naar oost (zoals MeshChat tekent)."""
    out = []
    for j in range(NY):
        for i in range(NX):
            out.append((round(NORTH - j * STEP, 2), round(WEST + i * STEP, 2)))
    return out


def _variables():
    return ",".join(f"{v}_{p}hPa" for p in LEVELS for v in ("temperature", "relative_humidity", "geopotential_height"))


def _fetch_json(url, timeout=60):
    req = urllib.request.Request(url, headers={"User-Agent": "MeshManager tropo (meshmanager.net)"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def fetch_field(fetch=None, pause=None):
    """Haal het hele raster op en bouw het veld; gooit bij een netwerkfout."""
    fetch = fetch or _fetch_json
    pause = CHUNK_PAUSE_S if pause is None else pause
    pts = points()
    grad = [None] * len(pts)
    times = None
    for o in range(0, len(pts), CHUNK):
        chunk = pts[o:o + CHUNK]
        if o and pause:
            time.sleep(pause)
        q = urllib.parse.urlencode({
            "latitude": ",".join(f"{la:.2f}" for la, _ in chunk),
            "longitude": ",".join(f"{lo:.2f}" for _, lo in chunk),
            "hourly": _variables(), "forecast_days": 2, "timezone": "UTC",
        }, safe=",")
        data = fetch(f"{API}?{q}")
        if not isinstance(data, list):
            data = [data]
        if len(data) != len(chunk):
            raise ValueError(f"Open-Meteo gaf {len(data)} punten voor {len(chunk)}")
        for k, d in enumerate(data):
            h = d.get("hourly") or {}
            if times is None and h.get("time"):
                times = list(h["time"])
            n = len(h.get("time") or [])
            grad[o + k] = [gradient(h, ti) for ti in range(n)] if n else None
    if not times:
        raise ValueError("Open-Meteo gaf geen tijdreeks")
    return {"times": times, "grad": grad, "source": "Open-Meteo", "run": None,
            "grid": {"step": STEP, "w": WEST, "e": EAST, "s": SOUTH, "n": NORTH, "nx": NX, "ny": NY},
            "fetched": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M")}


def set_field(field):
    global _field
    with _lock:
        _field = field


def get_field():
    with _lock:
        return _field


def slice_field(field, hours_ahead, now=None):
    """Het veld voor één uur: index = (nu + h) t.o.v. de eerste modeltijd."""
    now = now or datetime.now(timezone.utc)
    target = now.replace(minute=0, second=0, microsecond=0)
    key = target.strftime("%Y-%m-%dT%H")
    times = field["times"]
    idx = next((i for i, t in enumerate(times) if t[:13] == key), None)
    if idx is None:
        idx = 0 if key < times[0][:13] else len(times) - 1
    # ICON-EU levert stappen om de 3 uur: neem de stap die het dichtst bij (nu + h) ligt.
    want = target + timedelta(hours=int(hours_ahead))
    stamps = [datetime.strptime(t[:13], "%Y-%m-%dT%H").replace(tzinfo=timezone.utc) for t in times]
    idx = min(range(len(stamps)), key=lambda i: abs((stamps[i] - want).total_seconds()))
    g = field.get("grid") or {"step": STEP, "w": WEST, "e": EAST, "s": SOUTH, "n": NORTH, "nx": NX, "ny": NY}
    return {
        **g, "model_time": times[idx], "fetched": field["fetched"], "h": int(hours_ahead),
        "source": field.get("source", "Open-Meteo"), "run": field.get("run"),
        "grad": [(row[idx] if row and idx < len(row) else None) for row in field["grad"]],
    }


def _run():
    """Elk half uur: is er een nieuwere complete ICON-EU-run, haal die dan; lukt ICON-EU niet
    en is er geen (recent) veld, dan Open-Meteo, hoogstens om de 6 uur (daglimiet per IP)."""
    global _last_error
    time.sleep(FIRST_RUN_DELAY_S)
    last_om = 0.0
    while True:
        cur = get_field()
        try:
            if SOURCE == "icon":
                run = icon.latest_run()
                if run is None:
                    raise RuntimeError("geen complete ICON-EU-run bereikbaar")
                if cur is None or cur.get("run") != run.strftime("%Y-%m-%dT%H"):
                    t0 = time.time()
                    set_field(icon.fetch_field(run=run))
                    print(f"[meshmanager] tropo: ICON-EU-run {run:%Y-%m-%d %H} UTC verwerkt in {time.time() - t0:.0f} s", flush=True)
                _last_error = None
            else:
                raise RuntimeError("bron openmeteo gekozen")
        except Exception as e:  # noqa: BLE001 - de lus mag nooit stoppen
            _last_error = str(e)
            if SOURCE == "icon":
                print(f"[meshmanager] tropo: ICON-EU mislukt: {e}", flush=True)
            stale = cur is None or cur.get("source") != "Open-Meteo" and (time.time() - last_om) > INTERVAL_S and _hours_old(cur) > 9
            if (cur is None or stale) and time.time() - last_om > INTERVAL_S:
                try:
                    set_field(fetch_field())
                    last_om = time.time()
                    print("[meshmanager] tropo: veld van Open-Meteo (terugval)", flush=True)
                except Exception as e2:  # noqa: BLE001
                    _last_error = f"{_last_error}; Open-Meteo: {e2}"
                    print(f"[meshmanager] tropo: Open-Meteo mislukt: {e2}", flush=True)
            time.sleep(RETRY_S)
            continue
        time.sleep(POLL_S)


def _hours_old(field):
    try:
        return (datetime.now(timezone.utc) - datetime.strptime(field["fetched"], "%Y-%m-%dT%H:%M").replace(tzinfo=timezone.utc)).total_seconds() / 3600
    except Exception:  # noqa: BLE001
        return 1e9


def start():
    """Start de ophaal-lus (ICON-EU, terugval Open-Meteo); MM_TROPO=0 zet hem uit (tests, offline installaties)."""
    global _thread
    if os.environ.get("MM_TROPO", "1") == "0" or _thread is not None:
        return
    _thread = threading.Thread(target=_run, name="tropo", daemon=True)
    _thread.start()


@router.get("/api/tropo")
def api_tropo(h: int = Query(0, ge=0, le=39)):
    field = get_field()
    if field is None:
        return Response(json.dumps({"error": "nog geen gegevens", "detail": _last_error}), status_code=503,
                        media_type="application/json", headers={"Retry-After": "60", "Cache-Control": "no-store"})
    body = json.dumps(slice_field(field, h), separators=(",", ":"))
    # Tien minuten cachen mag: het veld verandert één keer per uur.
    return Response(body, media_type="application/json", headers={"Cache-Control": "public, max-age=600"})
