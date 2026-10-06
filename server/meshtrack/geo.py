"""Kleine geometrie op de bol: genoeg voor afstanden van enkele km tot honderden km."""
from __future__ import annotations

import math

R = 6371008.8  # gemiddelde aardstraal (m)


def haversine(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Afstand in meter."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * R * math.asin(min(1.0, math.sqrt(a)))


def bearing(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Koers van punt 1 naar punt 2, in graden 0..360 (0 = noord)."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dl = math.radians(lon2 - lon1)
    y = math.sin(dl) * math.cos(p2)
    x = math.cos(p1) * math.sin(p2) - math.sin(p1) * math.cos(p2) * math.cos(dl)
    return (math.degrees(math.atan2(y, x)) + 360) % 360


def angle_diff(a: float, b: float) -> float:
    """Kleinste hoek tussen twee koersen, 0..180."""
    d = abs(a - b) % 360
    return 360 - d if d > 180 else d


def offset(lat: float, lon: float, north_m: float, east_m: float) -> tuple[float, float]:
    """Punt verschoven over enkele meters (vlakke benadering, voor GPS-ruis)."""
    dlat = north_m / R
    dlon = east_m / (R * math.cos(math.radians(lat)))
    return lat + math.degrees(dlat), lon + math.degrees(dlon)


def interpolate(lat1: float, lon1: float, lat2: float, lon2: float, f: float) -> tuple[float, float]:
    """Lineair tussen twee dichte punten (f 0..1); prima voor routesegmenten."""
    return lat1 + (lat2 - lat1) * f, lon1 + (lon2 - lon1) * f


def point_in_polygon(lat: float, lon: float, ring: list[list[float]]) -> bool:
    """Ray casting. `ring` = [[lon, lat], ...] (GeoJSON-volgorde)."""
    inside = False
    n = len(ring)
    j = n - 1
    for i in range(n):
        xi, yi = ring[i][0], ring[i][1]
        xj, yj = ring[j][0], ring[j][1]
        if (yi > lat) != (yj > lat):
            x = (xj - xi) * (lat - yi) / (yj - yi) + xi
            if lon < x:
                inside = not inside
        j = i
    return inside
