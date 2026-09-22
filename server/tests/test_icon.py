"""ICON-EU als bron voor het tropo-veld (app/icon.py).

Waarom dit een test verdient. Het decoderen van GRIB2 en het uitknippen van ons
0,25°-raster uit het 0,0625°-rooster van de DWD is precies het soort werk waar een
verschoven index geen fout geeft maar de verkeerde plek kleurt. Daarom bouwen we hier
echte GRIB2-berichten (eccodes-sample regular_ll_pl_grib2) met waarden die een functie
van lengte- en breedtegraad zijn, en controleren dat elk doelpunt de waarde van zijn
eigen coördinaat krijgt. Daarnaast: de keuze van de nieuwste complete run, de
bestandsnamen van opendata.dwd.de, de gradiënt met vijf niveaus (steilste laag wint,
ontbrekend niveau overgeslagen), en dat het veld de vorm heeft die /api/tropo verwacht.
"""
import bz2
import os
import re
from datetime import datetime, timezone

import pytest

os.environ.setdefault("MM_MQTT_HOST", "127.0.0.1")
os.environ["MM_TROPO"] = "0"

ec = pytest.importorskip("eccodes")
np = pytest.importorskip("numpy")

from app import icon, tropo  # noqa: E402


def _grib(values2d, level, short_name, step):
    """Een GRIB2-bericht op het ICON-EU-rooster (bz2), rijen zuid→noord."""
    h = ec.codes_grib_new_from_samples("regular_ll_pl_grib2")
    ec.codes_set(h, "Ni", icon.NI); ec.codes_set(h, "Nj", icon.NJ)
    ec.codes_set(h, "latitudeOfFirstGridPointInDegrees", icon.LAT0)
    ec.codes_set(h, "longitudeOfFirstGridPointInDegrees", icon.LON0 + 360)
    ec.codes_set(h, "latitudeOfLastGridPointInDegrees", icon.LAT0 + (icon.NJ - 1) * icon.INC)
    ec.codes_set(h, "longitudeOfLastGridPointInDegrees", icon.LON0 + (icon.NI - 1) * icon.INC)
    ec.codes_set(h, "iDirectionIncrementInDegrees", icon.INC); ec.codes_set(h, "jDirectionIncrementInDegrees", icon.INC)
    ec.codes_set(h, "jScansPositively", 1)
    ec.codes_set(h, "typeOfLevel", "isobaricInhPa"); ec.codes_set(h, "level", level)
    ec.codes_set(h, "shortName", short_name); ec.codes_set(h, "step", step)
    ec.codes_set_values(h, values2d.reshape(-1))
    msg = ec.codes_get_message(h); ec.codes_release(h)
    return bz2.compress(msg)


def _lonlat():
    lon = icon.LON0 + np.arange(icon.NI) * icon.INC
    lat = icon.LAT0 + np.arange(icon.NJ) * icon.INC
    return np.meshgrid(lon, lat)       # (NJ, NI)


def test_bestandsnaam_en_runkeuze():
    run = datetime(2026, 9, 21, 21, tzinfo=timezone.utc)
    assert icon.file_url(run, 6, 1000, "t", "T") == (
        "https://opendata.dwd.de/weather/nwp/icon-eu/grib/21/t/"
        "icon-eu_europe_regular-lat-lon_pressure-level_2026092121_006_1000_T.grib2.bz2")
    now = datetime(2026, 9, 22, 0, 50, tzinfo=timezone.utc)
    runs = icon.candidate_runs(now)
    assert runs[0] == run and runs[1].hour == 18            # 00:50 - 2,5 u = 22:20 → run 21
    # Alleen run 18 is compleet (laatste stap aanwezig).
    assert icon.latest_run(now, exists=lambda u: "_2026092118_" in u) == datetime(2026, 9, 21, 18, tzinfo=timezone.utc)
    assert icon.latest_run(now, exists=lambda u: False) is None


def test_decode_knipt_het_juiste_punt_uit():
    LON, LAT = _lonlat()
    raw = _grib(1000 * LAT + LON, 1000, "t", 0)           # waarde codeert de coördinaat
    idx = icon.target_indices()
    vals, meta = icon.decode(raw, idx)
    assert vals.shape == (icon.NX * icon.NY,) and meta[2] == 0
    # noordwest eerst, zuidoost laatst, west→oost binnen een rij
    assert abs(vals[0] - (1000 * icon.NORTH + icon.WEST)) < 0.05
    assert abs(vals[1] - (1000 * icon.NORTH + icon.WEST + icon.STEP)) < 0.05
    assert abs(vals[-1] - (1000 * icon.SOUTH + icon.EAST)) < 0.05
    mid = (icon.NY // 2) * icon.NX + icon.NX // 2
    assert abs(vals[mid] - (1000 * (icon.NORTH - (icon.NY // 2) * icon.STEP) + icon.WEST + (icon.NX // 2) * icon.STEP)) < 0.05


def test_gradient_vijf_niveaus_en_ontbrekend_niveau():
    n = 4
    lv = {
        1000: (np.full(n, 14.0), np.full(n, 70.0), np.full(n, 250.0)),
        950: (np.full(n, 12.0), np.full(n, 60.0), np.full(n, 680.0)),
        925: (np.full(n, 10.0), np.array([52.0, 5.0, 52.0, 52.0]), np.full(n, 900.0)),   # punt 1: droog boven → steil
        850: (np.full(n, 9.8), np.full(n, 30.0), np.full(n, 1600.0)),
    }
    g = icon.gradient_field(lv)
    assert g.shape == (n,) and g[1] < g[0] - 30           # het droge punt is duidelijk steiler
    # Een dunne laag (1000→950, ~430 m) met superrefractie telt voor de helft: grondinversie boven land.
    warm_dry = (np.full(n, 20.0), np.full(n, 20.0), np.full(n, 680.0))
    thin = icon.gradient_field({1000: lv[1000], 950: warm_dry})
    raw = (icon.refractivity(20.0, 20.0, 950) - icon.refractivity(14.0, 70.0, 1000)) / 0.43
    assert raw < -100 and np.allclose(thin, -60 + (raw + 60) * 0.5) and (thin > raw).all()
    # ... maar een dunne laag die wél vangt (duct, < -157 N/km) blijft staan: zeer droog en warm boven vochtig.
    duct = icon.gradient_field({1000: (np.full(n, 14.0), np.full(n, 95.0), np.full(n, 100.0)),
                                950: (np.full(n, 20.0), np.full(n, 3.0), np.full(n, 520.0))})
    assert (duct < icon.DUCT_N_KM).all()
    # Zelfde som per laag als tropo.py (scalair) voor punt 0 tussen 1000 en 925 (950 tussenin, dus alleen richting)
    ref = tropo.gradient({"temperature_1000hPa": [14.0], "relative_humidity_1000hPa": [70], "geopotential_height_1000hPa": [250],
                          "temperature_925hPa": [10.0], "relative_humidity_925hPa": [52], "geopotential_height_925hPa": [900],
                          "temperature_850hPa": [9.8], "relative_humidity_850hPa": [30], "geopotential_height_850hPa": [1600]}, 0)
    assert g[0] < 0 and ref < 0


def test_fetch_field_bouwt_veld_uit_grib():
    LON, LAT = _lonlat()
    run = datetime(2026, 9, 21, 18, tzinfo=timezone.utc)
    pat = re.compile(r"_(\d{10})_(\d{3})_(\d+)_([A-Z]+)\.grib2\.bz2$")

    def fake_download(url):
        m = pat.search(url); step, level, var = int(m.group(2)), int(m.group(3)), m.group(4)
        if var == "T":
            v = 273.15 + 15.0 - (1000 - level) * 0.03 + np.zeros_like(LAT)
        elif var == "RELHUM":
            v = np.where(LON > 5, 5.0, 70.0) if level < 1000 else np.full_like(LAT, 75.0)   # oosten droog boven
        else:
            v = np.full_like(LAT, {1000: 250, 950: 680, 925: 900, 900: 1130, 850: 1600}[level] * icon.G0)
        return _grib(v, level, {"T": "t", "RELHUM": "r", "FI": "z"}[var], step)

    icon_steps = icon.STEPS
    try:
        icon.STEPS = (0, 3)   # twee stappen volstaan voor de vorm; scheelt 165 GRIB-berichten in de test
        field = icon.fetch_field(run=run, download=fake_download, pause=0)
    finally:
        icon.STEPS = icon_steps
    assert field["times"] == ["2026-09-21T18:00", "2026-09-21T21:00"]
    assert field["grid"]["nx"] == icon.NX and len(field["grad"]) == icon.NX * icon.NY
    assert field["source"].startswith("ICON-EU 2026-09-21 18")
    west = field["grad"][0]; east = field["grad"][icon.NX - 1]
    assert len(west) == 2 and east[0] < west[0] - 30      # oosten steiler
    # Door /api/tropo te snijden geeft het ICON-raster in het antwoord, niet de Open-Meteo-constanten.
    s = tropo.slice_field(field, 3, datetime(2026, 9, 21, 18, 20, tzinfo=timezone.utc))
    assert s["step"] == icon.STEP and s["nx"] == icon.NX and s["model_time"] == "2026-09-21T21:00" and s["source"].startswith("ICON-EU")
    assert len(s["grad"]) == icon.NX * icon.NY
