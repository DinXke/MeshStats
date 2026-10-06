"""Offline routeren over de wegen in de eigen kaarttegels (Protomaps-schema).

Geen OSRM of internet: de `roads`-laag van de z14-tegels bevat elke weg met
`kind_detail` (motorway, residential, cycleway, ...) en `oneway`. Per rit
bouwen we een graaf uit de tegels rond vertrek en bestemming en zoeken we met
A* de snelste route. Alleen de wegenlaag wordt gedecodeerd (eigen minimale
MVT-lezer), zodat gebouwen e.d. geen geheugen of tijd kosten.
"""
from __future__ import annotations

import gzip
import heapq
import math
import random
import threading
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from pmtiles.reader import MmapSource, Reader

from .geo import haversine

Z = 14

# km/u per wegtype en profiel; ontbreekt een type, dan is die weg niet bruikbaar.
SPEEDS = {
    "car": {
        "motorway": 120, "motorway_link": 60, "trunk": 90, "trunk_link": 50,
        "primary": 70, "primary_link": 40, "secondary": 70, "secondary_link": 40,
        "tertiary": 50, "tertiary_link": 30, "unclassified": 50, "residential": 30,
        "living_street": 15, "service": 15, "road": 30,
    },
    "bike": {
        "cycleway": 18, "primary": 16, "secondary": 18, "tertiary": 18, "unclassified": 18,
        "residential": 18, "living_street": 12, "service": 12, "track": 12, "path": 14,
        "primary_link": 14, "secondary_link": 14, "tertiary_link": 14, "road": 16,
    },
    "walk": {
        "footway": 5, "pedestrian": 5, "path": 5, "residential": 5, "living_street": 5,
        "service": 5, "track": 5, "unclassified": 5, "tertiary": 5, "cycleway": 5,
        "steps": 3, "sidewalk": 5, "crossing": 5, "road": 5,
    },
}


# ---- minimale MVT-lezer (alleen de laag `roads`) -----------------------------

def _varint(b: bytes, i: int) -> tuple[int, int]:
    r = s = 0
    while True:
        c = b[i]
        i += 1
        r |= (c & 0x7F) << s
        if c < 0x80:
            return r, i
        s += 7


def _fields(b: bytes):
    """Protobuf-velden: (nummer, wiretype, waarde-of-bytes)."""
    i, n = 0, len(b)
    while i < n:
        key, i = _varint(b, i)
        f, wt = key >> 3, key & 7
        if wt == 0:
            v, i = _varint(b, i)
            yield f, wt, v
        elif wt == 2:
            ln, i = _varint(b, i)
            yield f, wt, b[i:i + ln]
            i += ln
        elif wt == 1:
            yield f, wt, b[i:i + 8]
            i += 8
        elif wt == 5:
            yield f, wt, b[i:i + 4]
            i += 4
        else:
            raise ValueError(f"onbekend wiretype {wt}")


def _packed(b: bytes) -> list[int]:
    out, i = [], 0
    while i < len(b):
        v, i = _varint(b, i)
        out.append(v)
    return out


def _value(b: bytes):
    for f, wt, v in _fields(b):
        if f == 1:
            return v.decode("utf-8", "replace")
        if f == 7:
            return bool(v)
        if f in (4, 5):
            return v
        if f == 6:
            return (v >> 1) ^ -(v & 1)
    return None


def _lines(geom: list[int]) -> list[list[tuple[int, int]]]:
    """MVT-geometriecommando's -> lijnen in tegelcoördinaten."""
    lines, cur, x, y, i = [], None, 0, 0, 0
    while i < len(geom):
        cmd, cnt = geom[i] & 7, geom[i] >> 3
        i += 1
        if cmd == 7:
            continue
        for _ in range(cnt):
            dx, dy = geom[i], geom[i + 1]
            i += 2
            x += (dx >> 1) ^ -(dx & 1)
            y += (dy >> 1) ^ -(dy & 1)
            if cmd == 1:
                cur = [(x, y)]
                lines.append(cur)
            elif cur is not None:
                cur.append((x, y))
    return [ln for ln in lines if len(ln) >= 2]


def decode_roads(tile: bytes) -> tuple[int, list[tuple[str, bool, list[tuple[int, int]]]]]:
    """(extent, [(kind_detail, oneway, lijn), ...]) uit één (ongecomprimeerde) tegel."""
    for f, wt, layer in _fields(tile):
        if f != 3:
            continue
        name, keys, values, feats, extent = None, [], [], [], 4096
        for lf, lwt, lv in _fields(layer):
            if lf == 1:
                name = lv.decode()
                if name != "roads":
                    break
            elif lf == 2:
                feats.append(lv)
            elif lf == 3:
                keys.append(lv.decode())
            elif lf == 4:
                values.append(_value(lv))
            elif lf == 5:
                extent = lv
        if name != "roads":
            continue
        out = []
        for fb in feats:
            tags, geom, gtype = [], [], 0
            for ff, fwt, fv in _fields(fb):
                if ff == 2:
                    tags = _packed(fv)
                elif ff == 3:
                    gtype = fv
                elif ff == 4:
                    geom = _packed(fv)
            if gtype != 2:
                continue
            props = {keys[tags[k]]: values[tags[k + 1]] for k in range(0, len(tags) - 1, 2)}
            kd = props.get("kind_detail") or props.get("kind") or ""
            oneway = props.get("oneway") in ("yes", True, "true", "1")
            for ln in _lines(geom):
                out.append((kd, oneway, ln))
        return extent, out
    return 4096, []


# ---- tegels ------------------------------------------------------------------

def tile_xy(lon: float, lat: float, z: int = Z) -> tuple[int, int]:
    n = 2 ** z
    x = int((lon + 180) / 360 * n)
    lr = math.radians(lat)
    y = int((1 - math.log(math.tan(lr) + 1 / math.cos(lr)) / math.pi) / 2 * n)
    return x, y


def tile_to_lonlat(z: int, tx: int, ty: int, px: float, py: float, extent: int) -> tuple[float, float]:
    n = 2 ** z
    lon = (tx + px / extent) / n * 360 - 180
    lat = math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * (ty + py / extent) / n))))
    return lon, lat


def clip_line(line: list[tuple[int, int]], ext: int) -> list[list[tuple[float, float]]]:
    """Lijn afknippen op het tegelvak [0, ext]. Buurtegels knippen dezelfde weg
    dan op exact dezelfde rand, zodat hun eindpunten samenvallen en de graaf
    over tegelgrenzen heen verbonden is (de bufferzone deelt meestal geen punt)."""
    out: list[list[tuple[float, float]]] = []
    cur: list[tuple[float, float]] = []

    def inside(p):
        return 0 <= p[0] <= ext and 0 <= p[1] <= ext

    for a, b in zip(line, line[1:]):
        # Liang-Barsky voor het stuk a-b
        x0, y0 = a
        dx, dy = b[0] - x0, b[1] - y0
        t0, t1 = 0.0, 1.0
        ok = True
        for pp, qq in ((-dx, x0), (dx, ext - x0), (-dy, y0), (dy, ext - y0)):
            if pp == 0:
                if qq < 0:
                    ok = False
                    break
            else:
                r = qq / pp
                if pp < 0:
                    t0 = max(t0, r)
                else:
                    t1 = min(t1, r)
        if not ok or t0 > t1:
            if cur:
                out.append(cur)
                cur = []
            continue
        pa = (x0 + t0 * dx, y0 + t0 * dy)
        pb = (x0 + t1 * dx, y0 + t1 * dy)
        if not cur:
            cur = [pa]
        elif cur[-1] != pa:
            out.append(cur)
            cur = [pa]
        cur.append(pb)
        if t1 < 1.0:          # verlaat het vak
            out.append(cur)
            cur = []
    if cur:
        out.append(cur)
    return [c for c in out if len(c) >= 2]


class TileRoads:
    """Gedecodeerde wegen per tegel, met een kleine LRU-cache."""

    def __init__(self, path: str, cache: int = 300):
        self.path = Path(path)
        self._lock = threading.Lock()
        self._cache: OrderedDict = OrderedDict()
        self._max = cache
        self._f = open(self.path, "rb")
        self._reader = Reader(MmapSource(self._f))
        self.bounds = self._bounds()

    def _bounds(self) -> tuple[float, float, float, float]:
        h = self._reader.header()
        return (h["min_lon_e7"] / 1e7, h["min_lat_e7"] / 1e7, h["max_lon_e7"] / 1e7, h["max_lat_e7"] / 1e7)

    def roads(self, tx: int, ty: int) -> list[tuple[str, bool, list[tuple[float, float]]]]:
        key = (tx, ty)
        with self._lock:
            if key in self._cache:
                self._cache.move_to_end(key)
                return self._cache[key]
            raw = self._reader.get(Z, tx, ty)
        out = []
        if raw:
            if raw[:2] == b"\x1f\x8b":
                raw = gzip.decompress(raw)
            extent, feats = decode_roads(raw)
            for kd, oneway, ln in feats:
                for part in clip_line(ln, extent):
                    pts = [tile_to_lonlat(Z, tx, ty, px, py, extent) for px, py in part]
                    out.append((kd, oneway, pts))
        with self._lock:
            self._cache[key] = out
            while len(self._cache) > self._max:
                self._cache.popitem(last=False)
        return out


# ---- graaf ---------------------------------------------------------------------

SNAP_M = 1.5          # punten dichter bij elkaar dan dit zijn hetzelfde knooppunt
_CELL_DEG = 0.00003   # ~3 m: rooster voor het samenvoegen


@dataclass
class Leg:
    lat: float
    lon: float
    limit_kmh: float      # maximumsnelheid op het stuk dat HIER begint
    kind: str


class Graph:
    def __init__(self) -> None:
        self.lat: list[float] = []
        self.lon: list[float] = []
        self.adj: list[list[tuple[int, float, float, str]]] = []   # (naar, meters, km/u, kind)
        self._grid: dict[tuple[int, int], list[int]] = {}
        self.main: Optional[set[int]] = None   # grootste verbonden deel (na finish())

    def finish(self) -> None:
        """Grootste samenhangende wegennet bepalen (richting genegeerd), zodat
        start en bestemming nooit op een los stukje weg terechtkomen."""
        undirected: list[list[int]] = [[] for _ in self.lat]
        for a, edges in enumerate(self.adj):
            for b, *_ in edges:
                undirected[a].append(b)
                undirected[b].append(a)
        seen = [False] * len(self.lat)
        best: list[int] = []
        for start in range(len(self.lat)):
            if seen[start] or not undirected[start]:
                continue
            comp, stack = [], [start]
            seen[start] = True
            while stack:
                n = stack.pop()
                comp.append(n)
                for m in undirected[n]:
                    if not seen[m]:
                        seen[m] = True
                        stack.append(m)
            if len(comp) > len(best):
                best = comp
        self.main = set(best)

    def _node(self, lon: float, lat: float) -> int:
        cx, cy = int(lon / _CELL_DEG), int(lat / _CELL_DEG)
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for n in self._grid.get((cx + dx, cy + dy), ()):
                    if abs(self.lat[n] - lat) < 2e-5 and abs(self.lon[n] - lon) < 3e-5 \
                            and haversine(lat, lon, self.lat[n], self.lon[n]) <= SNAP_M:
                        return n
        n = len(self.lat)
        self.lat.append(lat)
        self.lon.append(lon)
        self.adj.append([])
        self._grid.setdefault((cx, cy), []).append(n)
        return n

    def add_road(self, pts: list[tuple[float, float]], kmh: float, oneway: bool, kind: str) -> None:
        prev = self._node(*pts[0])
        for lon, lat in pts[1:]:
            cur = self._node(lon, lat)
            if cur != prev:
                d = haversine(self.lat[prev], self.lon[prev], lat, lon)
                self.adj[prev].append((cur, d, kmh, kind))
                if not oneway:
                    self.adj[cur].append((prev, d, kmh, kind))
            prev = cur

    def nearest(self, lat: float, lon: float, max_m: float = 3000) -> Optional[int]:
        best, bd = None, max_m
        cx, cy = int(lon / _CELL_DEG), int(lat / _CELL_DEG)
        for r in (3, 30, 300, 1000):
            for (gx, gy), nodes in self._grid.items() if r >= 300 else self._ring(cx, cy, r):
                if abs(gx - cx) > r or abs(gy - cy) > r:
                    continue
                for n in nodes:
                    if not self.adj[n] or (self.main is not None and n not in self.main):
                        continue
                    d = haversine(lat, lon, self.lat[n], self.lon[n])
                    if d < bd:
                        best, bd = n, d
            if best is not None:
                return best
        return best

    def _ring(self, cx: int, cy: int, r: int):
        for dx in range(-r, r + 1):
            for dy in range(-r, r + 1):
                k = (cx + dx, cy + dy)
                if k in self._grid:
                    yield k, self._grid[k]

    def astar(self, a: int, b: int, vmax: float) -> Optional[list[tuple[int, float, str]]]:
        """Snelste route a->b. Geeft [(knoop, km/u van het stuk ernaartoe, kind), ...]."""
        tl, tn = self.lat[b], self.lon[b]
        h = lambda n: haversine(self.lat[n], self.lon[n], tl, tn) / (vmax / 3.6)  # noqa: E731
        g = {a: 0.0}
        came: dict[int, tuple[int, float, str]] = {}
        pq = [(h(a), a)]
        seen = set()
        while pq:
            _, n = heapq.heappop(pq)
            if n == b:
                path = [(b, 0.0, "")]
                while n in came:
                    p, kmh, kind = came[n]
                    path[-1] = (path[-1][0], kmh, kind)
                    path.append((p, 0.0, ""))
                    n = p
                path.reverse()
                return path
            if n in seen:
                continue
            seen.add(n)
            for m, d, kmh, kind in self.adj[n]:
                ng = g[n] + d / (kmh / 3.6)
                if ng < g.get(m, float("inf")):
                    g[m] = ng
                    came[m] = (n, kmh, kind)
                    heapq.heappush(pq, (ng + h(m), m))
        return None


# Hoofdwegen: in het midden van een lange corridor tellen alleen deze mee.
MAJOR = {"motorway", "motorway_link", "trunk", "trunk_link", "primary", "primary_link",
         "secondary", "secondary_link", "tertiary", "tertiary_link"}


def _tile_center(tx: int, ty: int) -> tuple[float, float]:
    lon, lat = tile_to_lonlat(Z, tx, ty, 0.5, 0.5, 1)
    return lat, lon


def _dist_to_segment_m(p: tuple[float, float], a: tuple[float, float], b: tuple[float, float]) -> float:
    """Afstand van punt p tot lijnstuk a-b (vlakke benadering, ruim genoeg)."""
    k = 111_320.0
    c = math.cos(math.radians(p[0]))
    ax, ay = a[1] * k * c, a[0] * k
    bx, by = b[1] * k * c, b[0] * k
    px, py = p[1] * k * c, p[0] * k
    dx, dy = bx - ax, by - ay
    t = 0.0 if dx == dy == 0 else max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy)))
    return math.hypot(px - (ax + t * dx), py - (ay + t * dy))


class Router:
    def __init__(self, tiles_path: str):
        self.tiles = TileRoads(tiles_path)

    def _add_tile(self, g: Graph, tx: int, ty: int, speeds: dict, profile: str, major_only: bool) -> None:
        for kd, oneway, pts in self.tiles.roads(tx, ty):
            if major_only and kd not in MAJOR:
                continue
            kmh = speeds.get(kd)
            if kmh:
                g.add_road(pts, kmh, oneway and profile == "car", kd)

    def build(self, bbox: tuple[float, float, float, float], profile: str,
              speeds: Optional[dict] = None) -> Graph:
        speeds = speeds or SPEEDS[profile]
        w, s, e, n = bbox
        x0, y1 = tile_xy(w, s)
        x1, y0 = tile_xy(e, n)
        g = Graph()
        for tx in range(x0, x1 + 1):
            for ty in range(y0, y1 + 1):
                self._add_tile(g, tx, ty, speeds, profile, False)
        g.finish()
        return g

    def build_corridor(self, a: tuple[float, float], b: tuple[float, float], profile: str,
                       width_m: float, speeds: Optional[dict] = None, local_m: float = 4000) -> Graph:
        """Graaf voor een lange rit: alleen tegels binnen width_m van de lijn a-b;
        verder dan local_m van vertrek en bestemming enkel de hoofdwegen."""
        speeds = speeds or SPEEDS[profile]
        pad = width_m / 111_320
        w, e = min(a[1], b[1]) - pad * 1.6, max(a[1], b[1]) + pad * 1.6
        s_, n = min(a[0], b[0]) - pad, max(a[0], b[0]) + pad
        x0, y1 = tile_xy(w, s_)
        x1, y0 = tile_xy(e, n)
        g = Graph()
        for tx in range(x0, x1 + 1):
            for ty in range(y0, y1 + 1):
                c = _tile_center(tx, ty)
                if _dist_to_segment_m(c, a, b) > width_m:
                    continue
                near = min(haversine(c[0], c[1], *a), haversine(c[0], c[1], *b)) < local_m
                self._add_tile(g, tx, ty, speeds, profile, major_only=not near and profile == "car")
        g.finish()
        return g

    def route(self, a: tuple[float, float], b: tuple[float, float], profile: str,
              margin_deg: float = 0.03, speeds: Optional[dict] = None) -> Optional[list[Leg]]:
        """Route tussen twee (lat, lon)-punten; None als er geen is. Korte ritten
        in een rechthoek rond beide punten, lange (> 30 km) in een corridor.
        Bedoeld voor ritten tot ~80 km; langere afstanden legt de simulator af
        als een reeks ritten (zwerven)."""
        speeds = speeds or SPEEDS[profile]
        d = haversine(a[0], a[1], b[0], b[1])
        if d <= 30_000:
            w = min(a[1], b[1]) - margin_deg
            e = max(a[1], b[1]) + margin_deg
            s = min(a[0], b[0]) - margin_deg * 0.65
            n = max(a[0], b[0]) + margin_deg * 0.65
            graphs = [lambda: self.build((w, s, e, n), profile, speeds)]
        else:
            graphs = [lambda wm=wm: self.build_corridor(a, b, profile, wm, speeds)
                      for wm in (max(6000, 0.12 * d), max(12000, 0.25 * d))]
        vmax = max(speeds.values())
        for make in graphs:
            g = make()
            na, nb = g.nearest(*a), g.nearest(*b)
            if na is None or nb is None or na == nb:
                continue
            path = g.astar(na, nb, vmax)
            if not path:
                continue
            legs = []
            for i, (node, _, _) in enumerate(path):
                nxt = path[i + 1] if i + 1 < len(path) else (node, 0.0, "")
                legs.append(Leg(g.lat[node], g.lon[node], nxt[1], nxt[2]))
            return legs
        return None

    def random_destination(self, frm: tuple[float, float], min_km: float, max_km: float,
                           rng: random.Random) -> tuple[float, float]:
        w, s, e, n = self.tiles.bounds
        for _ in range(50):
            d = rng.uniform(min_km, max_km) * 1000
            brg = math.radians(rng.uniform(0, 360))
            lat = frm[0] + (d * math.cos(brg)) / 111_320
            lon = frm[1] + (d * math.sin(brg)) / (111_320 * math.cos(math.radians(frm[0])))
            if w + 0.05 < lon < e - 0.05 and s + 0.05 < lat < n - 0.05:
                return lat, lon
        return frm
