"""ICON-EU (DWD Open Data) als bron voor het tropo-veld van MeshChat.

Waarom ICON-EU. Open-Meteo (tropo.py) telt per rasterpunt en begrenst per IP; het
model dat Open-Meteo zelf voor Europa gebruikt, ICON-EU van de DWD, staat gratis en
zonder limiet op opendata.dwd.de als GRIB2-bestanden: per variabele, drukniveau en
tijdstap één bestand van ~1 MB (bz2). Wij halen per run T, RELHUM en FI op 1000, 950,
925, 900 en 850 hPa voor de tijdstappen 0..39 uur (om de 3 uur) en rekenen daaruit
de refractiviteitsgradiënt op een raster van 0,25° over het gebied van de kaart.
Vijf niveaus in plaats van drie: dunnere lagen (~400 m) laten meer ducting zien.

Rooster van ICON-EU (regular-lat-lon): 1377 × 657 punten, 0,0625°, eerste punt
29,5° N / -23,5° E (336,5), rijen van zuid naar noord, binnen een rij van west naar
oost. Een run verschijnt ~2,5 uur na zijn analysetijd; runs om 00, 03, ..., 21 UTC.

Decoderen met eccodes (ECMWF, pip-wheel met de bibliotheek erin). Ontbreekt eccodes,
dan meldt fetch_field dat en valt tropo.py terug op Open-Meteo.
"""
import bz2
import math
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone

BASE = "https://opendata.dwd.de/weather/nwp/icon-eu/grib"
LEVELS = (1000, 950, 925, 900, 850)
STEPS = tuple(range(0, 40, 3))            # 0, 3, ..., 39 uur na de run
VARS = (("t", "T"), ("relhum", "RELHUM"), ("fi", "FI"))
G0 = 9.80665
# Kalibratie tegen de Hepburn-kaarten (dxinfocentre.com, 00 UTC 22-09-2026). Dunne lagen
# (1000→950 hPa, ~430 m) pikten 's nachts boven land de grondinversie op (-100..-150 N/km) en
# kleurden Nederland en het Ruhrgebied "sterk" waar Hepburn marginaal gaf. Daarom telt een
# dunne laag alleen mee als ze echt vangt (gradiënt onder DUCT_N_KM: een duct, zoals boven de
# Golf van Biskaje); superrefractie wordt alleen over lagen van minstens MIN_DZ_KM genomen
# (1000→925, 1000→900, 950→850, 925→850, 1000→850).
# Voor MeshCore (868 MHz, nodes op 5-30 m) telt die grondinversie wel: de node zit erin en
# haalt er merkbaar meer bereik uit, ook zonder echte duct. Daarom telt superrefractie in een
# dunne laag voor THIN_WEIGHT mee (het teveel onder -60 N/km gehalveerd): -140 in een dunne
# laag wordt -100 (niveau 3, matig) in plaats van 6 (Hepburn: 1). Elke laag die wij kunnen
# zien is ≥ 200 m dik en vangt daarmee alles boven ~30 MHz, dus 868 MHz zeker.
MIN_DZ_KM = 0.5
DUCT_N_KM = -157.0
THIN_WEIGHT = 0.5
LEVEL1_N_KM = -60.0

# Bronraster
NI, NJ = 1377, 657
LAT0, LON0, INC = 29.5, -23.5, 0.0625
# Doelraster: het volledige ICON-EU-gebied (Atlantische Oceaan tot de Oeral, Sahara tot Noord-Noorwegen)
# op 0,25° = elk 4e punt: 345 × 165 = 56.925 punten. Als gehele getallen (N/km) is dat ~230 kB JSON per
# uur en na compressie door Cloudflare een fractie daarvan; zo eindigt de laag nooit met een rand in beeld.
STEP = 0.25
WEST, EAST, SOUTH, NORTH = -23.5, 62.5, 29.5, 70.5
NX = int(round((EAST - WEST) / STEP)) + 1
NY = int(round((NORTH - SOUTH) / STEP)) + 1
PAUSE_S = 0.3
RUN_LAG_H = 2.5   # zo lang na de analysetijd is een run doorgaans compleet


def grid():
    return {"step": STEP, "w": WEST, "e": EAST, "s": SOUTH, "n": NORTH, "nx": NX, "ny": NY}


def file_url(run, step, level, var_dir, var_name):
    return (f"{BASE}/{run:%H}/{var_dir}/icon-eu_europe_regular-lat-lon_pressure-level_"
            f"{run:%Y%m%d%H}_{step:03d}_{level}_{var_name}.grib2.bz2")


def candidate_runs(now=None):
    """Runs van nieuw naar oud die klaar zouden moeten zijn (analysetijd + RUN_LAG_H)."""
    now = now or datetime.now(timezone.utc)
    latest = now - timedelta(hours=RUN_LAG_H)
    run = latest.replace(minute=0, second=0, microsecond=0)
    run -= timedelta(hours=run.hour % 3)
    return [run - timedelta(hours=3 * k) for k in range(8)]


def _exists(url, timeout=20):
    req = urllib.request.Request(url, method="HEAD", headers={"User-Agent": "MeshManager tropo (meshmanager.net)"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status == 200
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return False
        raise
    except urllib.error.URLError:
        return False


def latest_run(now=None, exists=None):
    """De nieuwste run waarvan de laatste tijdstap er al staat, of None."""
    exists = exists or _exists
    for run in candidate_runs(now):
        if exists(file_url(run, STEPS[-1], LEVELS[-1], "fi", "FI")):
            return run
    return None


def _download(url, timeout=90):
    req = urllib.request.Request(url, headers={"User-Agent": "MeshManager tropo (meshmanager.net)"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def target_indices():
    """Indexen in het bronveld (rij-major, i snelst) voor ons doelraster, noord→zuid, west→oost."""
    import numpy as np
    idx = []
    for j in range(NY):
        lat = NORTH - j * STEP
        jj = int(round((lat - LAT0) / INC))
        for i in range(NX):
            lon = WEST + i * STEP
            ii = int(round((lon - LON0) / INC))
            idx.append(jj * NI + ii)
    return np.asarray(idx)


def decode(grib_bz2, idx):
    """Eén GRIB2-veld (bz2) → waarden op ons doelraster (numpy-array) plus (dataDate, dataTime, step)."""
    import eccodes as ec
    import numpy as np
    data = bz2.decompress(grib_bz2)
    h = ec.codes_new_from_message(data)
    try:
        ni, nj = ec.codes_get(h, "Ni"), ec.codes_get(h, "Nj")
        lat0 = ec.codes_get(h, "latitudeOfFirstGridPointInDegrees")
        lon0 = ec.codes_get(h, "longitudeOfFirstGridPointInDegrees")
        if lon0 > 180:
            lon0 -= 360
        if (ni, nj) != (NI, NJ) or abs(lat0 - LAT0) > 1e-6 or abs(lon0 - LON0) > 1e-6 or not ec.codes_get(h, "jScansPositively"):
            raise ValueError(f"onverwacht rooster {ni}x{nj} vanaf {lat0},{lon0}")
        vals = np.asarray(ec.codes_get_values(h), dtype=float)
        missing = ec.codes_get(h, "missingValue")
        meta = (ec.codes_get(h, "dataDate"), ec.codes_get(h, "dataTime"), ec.codes_get(h, "step"))
    finally:
        ec.codes_release(h)
    out = vals[idx]
    out[out == missing] = math.nan
    return out, meta


def refractivity(t_c, rh, p_hpa):
    import numpy as np
    tk = t_c + 273.15
    es = 6.112 * np.exp(17.67 * t_c / (t_c + 243.5))
    e = np.clip(rh, 0.0, 100.0) / 100.0 * es
    return 77.6 * p_hpa / tk + 3.73e5 * e / (tk * tk)


def gradient_field(levels):
    """levels: {p: (T_c, RH, Z_m)} arrays → steilste dN/dh (N/km) per punt over alle paren niveaus:
    dikke lagen (≥ MIN_DZ_KM) volledig, dunne volledig als ze een duct vormen (< DUCT_N_KM) en anders
    voor THIN_WEIGHT (grondinversie boven land, relevant voor 868 MHz). NaN waar niets bruikbaar."""
    import numpy as np
    ps = sorted(levels, reverse=True)      # 1000 → 850: van laag naar hoog
    best = None
    for i, a in enumerate(ps):
        for b in ps[i + 1:]:
            t1, r1, z1 = levels[a]
            t2, r2, z2 = levels[b]
            dz = (z2 - z1) / 1000.0
            with np.errstate(invalid="ignore", divide="ignore"):
                g = (refractivity(t2, r2, b) - refractivity(t1, r1, a)) / dz
            thin_super = (dz < MIN_DZ_KM) & (g >= DUCT_N_KM)
            g = np.where(thin_super, LEVEL1_N_KM + (g - LEVEL1_N_KM) * THIN_WEIGHT, g)
            best = g if best is None else np.fmin(best, g)
    return best


def fetch_field(run=None, download=None, exists=None, pause=None, now=None):
    """Haal een hele run op en bouw het veld zoals tropo.py het serveert."""
    import numpy as np
    download = download or _download
    pause = PAUSE_S if pause is None else pause
    if run is None:
        run = latest_run(now, exists)
        if run is None:
            raise RuntimeError("geen complete ICON-EU-run gevonden")
    idx = target_indices()
    times, per_step = [], []
    for step in STEPS:
        levels = {}
        for p in LEVELS:
            fields = {}
            for var_dir, var_name in VARS:
                if pause:
                    time.sleep(pause)
                try:
                    raw = download(file_url(run, step, p, var_dir, var_name))
                except urllib.error.HTTPError as e:
                    if e.code == 404:
                        fields = None
                        break
                    raise
                fields[var_name], _meta = decode(raw, idx)
            if fields:
                levels[p] = (fields["T"] - 273.15, fields["RELHUM"], fields["FI"] / G0)
        if len(levels) < 2:
            continue
        grad = gradient_field(levels)
        times.append((run + timedelta(hours=step)).strftime("%Y-%m-%dT%H:00"))
        per_step.append(np.round(grad, 0))
    if not times:
        raise RuntimeError(f"ICON-EU-run {run:%Y-%m-%dT%H} leverde geen tijdstappen")
    stack = np.stack(per_step, axis=1)  # (punten, stappen)
    grad = [[None if math.isnan(v) else int(v) for v in row] for row in stack]  # hele N/km volstaan en houden het JSON klein
    return {"times": times, "grad": grad, "grid": grid(), "source": f"ICON-EU {run:%Y-%m-%d %H} UTC",
            "run": run.strftime("%Y-%m-%dT%H"), "fetched": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M")}
