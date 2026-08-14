"""Rebuild ``app/data/borders.json`` -- the country outlines the site classifies
node positions against.

Run it from ``server/``::

    python tools/build_borders.py                 # downloads the pinned source
    python tools/build_borders.py --source local.geojson
    python tools/build_borders.py --tolerance 0.004 --verify

Nobody should ever face that JSON file as a blob of unknown origin, so this
script is the record of how it was made: where the data came from, which
version, what was thrown away and how far the outlines were allowed to move.

Source
------
Natural Earth, **1:50m** Admin 0 - Countries, taken from the project's own
GeoJSON distribution at github.com/nvkelso/natural-earth-vector.

Natural Earth is in the **public domain**; no attribution is required, though
the project asks for credit and the site gives it.

Why 1:50m and not 1:110m
------------------------
1:110m is the obvious choice for a small file and it is unusable here. Checked
against the reference points in ``VERIFY_POINTS`` it puts Maastricht in Belgium,
Maaseik in the Netherlands and Aachen in Belgium -- the Meuse corridor, which is
exactly where this mesh lives. 1:50m gets every reference point right and still
fits in the size budget after trimming. 1:10m is four times larger again for no
gain any node position on this map can detect.

What is thrown away
-------------------
* Everything outside ``REGION``. Rings are *clipped* to that rectangle rather
  than kept or dropped whole, which is where most of the saving comes from:
  France keeps the part of itself inside the box and loses French Guiana, and
  Norway costs only its southern tip. A position outside the box gets no country
  at all, which is the honest answer for a map that does not reach that far.
* Vertices, by Ramer-Douglas-Peucker at ``--tolerance`` degrees.
* Rings that collapse below four points at that tolerance: islands too small to
  be a country's answer at this resolution.

Holes are preserved (a hole is what makes San Marino not Italy).

Encoding
--------
Coordinates are integers in units of 1/``scale`` degrees (4 decimals, ~11 m,
well below the accuracy of the source), and each ring is one flat array holding
the first point followed by deltas::

    [lon0, lat0, dlon1, dlat1, dlon2, dlat2, ...]

Neighbouring vertices are close together, so the deltas are small numbers and
the file is roughly half the size of the same rings written as coordinate pairs.
``decode_ring()`` below and ``_decode_ring()`` in ``app/countries.py`` are the
two readers; they must stay in step.

Known limits, worth stating rather than discovering later
---------------------------------------------------------
* **Enclaves.** No dataset at this scale carries Baarle-Hertog/Baarle-Nassau. A
  node there gets the country that surrounds it.
* **Coastlines.** Within a few hundred metres of water a node can fall just
  outside every outline and be reported as unknown. Central Copenhagen does
  exactly that, in the *source* data as much as here: at 1:50m the harbour
  channel is wider than the city. Unknown is the right answer for a map that
  cannot resolve the place; a nearest-country guess would not be.
* **Region edges.** Anything outside ``REGION`` is unknown by construction.

None of these are bugs in this script. They are the resolution of the map, and
the site is built to say "unknown" rather than to guess past them.

Dependencies: the Python standard library only. Deliberately -- a tool that needs
a GIS stack installed is a tool nobody re-runs.
"""
import argparse
import json
import math
import sys
import urllib.request
from pathlib import Path

SOURCE_URL = ("https://raw.githubusercontent.com/nvkelso/natural-earth-vector/"
              "master/geojson/ne_50m_admin_0_countries.geojson")
SOURCE_NAME = "Natural Earth 1:50m Admin 0 - Countries"
SOURCE_LICENSE = "public domain (naturalearthdata.com)"

# Western and central Europe: everything this mesh could plausibly reach, from
# Ireland to Poland and from Sicily to central Norway. A position outside the box
# is reported as unknown rather than guessed at, which is the honest answer for a
# map that does not contain the place. Widening it costs roughly 10 kB per large
# extra country, against a budget of 100 kB -- measure with --tolerance before
# and after, do not guess.
REGION = {"min_lon": -11.0, "max_lon": 20.0, "min_lat": 35.0, "max_lat": 62.0}

# 4 decimals is ~11 m at this latitude: far finer than the source, and it nearly
# halves the file compared with the source's full precision.
COORD_DECIMALS = 4
SCALE = 10 ** COORD_DECIMALS

# Ground truth for the check. Chosen to hammer the borders this project actually
# sits on -- the Belgian/Dutch/German corner around the Meuse -- rather than to
# look impressive by testing capital cities in the middle of large countries.
VERIFY_POINTS = [
    ("Hasselt", 50.9307, 5.3378, "BE"),
    ("Brussel", 50.8503, 4.3517, "BE"),
    ("Liege", 50.6326, 5.5797, "BE"),
    ("Maaseik", 51.0975, 5.7869, "BE"),
    ("Lanaken", 50.8867, 5.6497, "BE"),
    ("Tongeren", 50.7806, 5.4644, "BE"),
    ("Turnhout", 51.3227, 4.9447, "BE"),
    ("Arlon", 49.6833, 5.8167, "BE"),
    ("Maastricht", 50.8514, 5.6910, "NL"),
    ("Sittard", 50.9983, 5.8697, "NL"),
    ("Venlo", 51.3704, 6.1724, "NL"),
    ("Eindhoven", 51.4416, 5.4697, "NL"),
    ("Terneuzen", 51.3300, 3.8300, "NL"),
    ("Amsterdam", 52.3676, 4.9041, "NL"),
    ("Aachen", 50.7753, 6.0839, "DE"),
    ("Monchengladbach", 51.1805, 6.4428, "DE"),
    ("Koln", 50.9375, 6.9603, "DE"),
    ("Luxembourg", 49.6116, 6.1319, "LU"),
    ("Lille", 50.6292, 3.0573, "FR"),
    ("Paris", 48.8566, 2.3522, "FR"),
    ("London", 51.5074, -0.1278, "GB"),
    ("Dublin", 53.3498, -6.2603, "IE"),
    ("Zurich", 47.3769, 8.5417, "CH"),
    ("Madrid", 40.4168, -3.7038, "ES"),
    ("Milano", 45.4642, 9.1900, "IT"),
    ("Wien", 48.2082, 16.3738, "AT"),
    # Not the city centre: at 1:50m that sits in the harbour channel between
    # Zealand and Amager, in the *source* data as much as here. See "coastlines"
    # in the known limits above.
    ("Kobenhavn", 55.7000, 12.5500, "DK"),
    # Open water: the answer must be "no country", not the nearest one.
    ("Noordzee", 52.5000, 3.2000, None),
]


def ring_bbox(ring):
    lons = [p[0] for p in ring]
    lats = [p[1] for p in ring]
    return min(lons), min(lats), max(lons), max(lats)


def in_region(bbox):
    min_lon, min_lat, max_lon, max_lat = bbox
    return not (max_lon < REGION["min_lon"] or min_lon > REGION["max_lon"] or
                max_lat < REGION["min_lat"] or min_lat > REGION["max_lat"])


def _perp_distance(pt, start, end):
    """Distance from ``pt`` to the segment start-end, in degrees.

    Plain planar geometry on lon/lat. Over one country that is a small enough
    lie: it makes the tolerance slightly tighter east-west than north-south,
    which costs a few vertices and never moves a border the wrong way.
    """
    x0, y0 = pt
    x1, y1 = start
    x2, y2 = end
    dx, dy = x2 - x1, y2 - y1
    if dx == 0 and dy == 0:
        return math.hypot(x0 - x1, y0 - y1)
    t = ((x0 - x1) * dx + (y0 - y1) * dy) / (dx * dx + dy * dy)
    t = max(0.0, min(1.0, t))
    return math.hypot(x0 - (x1 + t * dx), y0 - (y1 + t * dy))


def simplify(ring, tolerance):
    """Ramer-Douglas-Peucker, iterative so a long coastline cannot blow the stack."""
    if len(ring) < 3 or tolerance <= 0:
        return ring
    keep = [False] * len(ring)
    keep[0] = keep[-1] = True
    stack = [(0, len(ring) - 1)]
    while stack:
        first, last = stack.pop()
        if last <= first + 1:
            continue
        worst, worst_i = -1.0, first
        for i in range(first + 1, last):
            d = _perp_distance(ring[i], ring[first], ring[last])
            if d > worst:
                worst, worst_i = d, i
        if worst > tolerance:
            keep[worst_i] = True
            stack.append((first, worst_i))
            stack.append((worst_i, last))
    return [p for p, k in zip(ring, keep) if k]


def clip_to_region(ring):
    """Sutherland-Hodgman: cut a ring down to the part inside REGION.

    The clip window is a rectangle, so this holds for the concave rings a
    coastline produces as well. A ring that leaves and re-enters the window comes
    back with edges running along the window border; those add no area and do not
    disturb a point-in-polygon test, which is all this data is ever used for.
    """
    edges = (
        ("x>=", REGION["min_lon"]), ("x<=", REGION["max_lon"]),
        ("y>=", REGION["min_lat"]), ("y<=", REGION["max_lat"]),
    )
    poly = list(ring)
    for op, bound in edges:
        if not poly:
            return []

        def inside(p):
            if op == "x>=":
                return p[0] >= bound
            if op == "x<=":
                return p[0] <= bound
            if op == "y>=":
                return p[1] >= bound
            return p[1] <= bound

        def cross(a, b):
            if op in ("x>=", "x<="):
                t = (bound - a[0]) / (b[0] - a[0])
                return [bound, a[1] + t * (b[1] - a[1])]
            t = (bound - a[1]) / (b[1] - a[1])
            return [a[0] + t * (b[0] - a[0]), bound]

        out = []
        for i in range(len(poly)):
            cur, prev = poly[i], poly[i - 1]
            cur_in, prev_in = inside(cur), inside(prev)
            if cur_in:
                if not prev_in:
                    out.append(cross(prev, cur))
                out.append(list(cur))
            elif prev_in:
                out.append(cross(prev, cur))
        poly = out
    if poly and poly[0] != poly[-1]:
        poly.append(list(poly[0]))   # keep rings closed
    return poly


def quantize(ring):
    """Ring of float lon/lat to a ring of integer lon/lat in 1/SCALE degrees."""
    out = []
    for lon, lat in ring:
        p = (int(round(lon * SCALE)), int(round(lat * SCALE)))
        # Rounding can duplicate neighbouring vertices; a repeated point adds
        # bytes and does nothing for the outline.
        if not out or out[-1] != p:
            out.append(p)
    return out


def encode_ring(ring):
    """Integer ring to the flat first-point-then-deltas array stored in the file."""
    flat = [ring[0][0], ring[0][1]]
    for i in range(1, len(ring)):
        flat.append(ring[i][0] - ring[i - 1][0])
        flat.append(ring[i][1] - ring[i - 1][1])
    return flat


def decode_ring(flat):
    """Inverse of encode_ring, back to float lon/lat pairs."""
    lon, lat = flat[0], flat[1]
    ring = [(lon / SCALE, lat / SCALE)]
    for i in range(2, len(flat), 2):
        lon += flat[i]
        lat += flat[i + 1]
        ring.append((lon / SCALE, lat / SCALE))
    return ring


def polygons_of(geometry):
    """Every polygon of a feature as a list of rings (exterior first, then holes)."""
    kind = geometry.get("type")
    if kind == "Polygon":
        return [geometry["coordinates"]]
    if kind == "MultiPolygon":
        return list(geometry["coordinates"])
    return []


def build(source_path, tolerance):
    data = json.loads(Path(source_path).read_text(encoding="utf-8"))
    countries = {}
    for feature in data.get("features", []):
        props = feature.get("properties") or {}
        # ISO_A2_EH resolves the cases where ISO_A2 is '-99' (France, Norway and
        # friends carry their code only in the _EH variant).
        code = props.get("ISO_A2_EH") or props.get("ISO_A2") or ""
        code = code.strip().upper()
        if len(code) != 2 or code == "-9":
            continue
        for rings in polygons_of(feature.get("geometry") or {}):
            if not rings or not in_region(ring_bbox(rings[0])):
                continue
            simplified = []
            for i, ring in enumerate(rings):
                # Clip first, then simplify: simplifying first would move
                # vertices across the window edge and leave slivers behind it.
                small = clip_to_region(ring)
                small = quantize(simplify(small, tolerance))
                if len(small) < 4:
                    if i == 0:
                        break        # exterior gone: drop the whole polygon
                    continue         # a hole too small to matter at this scale
                simplified.append(small)
            if simplified:
                countries.setdefault(code, []).append(simplified)

    out = {}
    for code, polys in sorted(countries.items()):
        lons = [p[0] for poly in polys for p in poly[0]]
        lats = [p[1] for poly in polys for p in poly[0]]
        out[code] = {
            "bbox": [min(lons), min(lats), max(lons), max(lats)],
            "polygons": [[encode_ring(r) for r in poly] for poly in polys],
        }
    return {
        "source": SOURCE_NAME,
        "source_url": SOURCE_URL,
        "license": SOURCE_LICENSE,
        "generated_by": "server/tools/build_borders.py",
        "format": "rings are [lon0,lat0,dlon,dlat,...] integers in 1/scale degrees",
        "scale": SCALE,
        "tolerance_deg": tolerance,
        "region": REGION,
        "countries": out,
    }


def _point_in_ring(lon, lat, ring):
    """Ray casting. Kept identical in app/countries.py -- if you change one,
    change the other, because --verify is only worth anything while the check
    and the site agree on what 'inside' means."""
    inside = False
    n = len(ring)
    j = n - 1
    for i in range(n):
        xi, yi = ring[i]
        xj, yj = ring[j]
        if (yi > lat) != (yj > lat):
            x_cross = (xj - xi) * (lat - yi) / (yj - yi) + xi
            if lon < x_cross:
                inside = not inside
        j = i
    return inside


def lookup(doc, lat, lon):
    """Reads the encoded document exactly as the site does, so --verify checks
    the artefact that ships rather than the geometry it was made from."""
    scale = doc["scale"]
    for code, entry in doc["countries"].items():
        min_lon, min_lat, max_lon, max_lat = [v / scale for v in entry["bbox"]]
        if not (min_lon <= lon <= max_lon and min_lat <= lat <= max_lat):
            continue
        for rings in entry["polygons"]:
            if not _point_in_ring(lon, lat, decode_ring(rings[0])):
                continue
            if any(_point_in_ring(lon, lat, decode_ring(h)) for h in rings[1:]):
                continue
            return code
    return None


def verify(doc):
    wrong = []
    for name, lat, lon, want in VERIFY_POINTS:
        got = lookup(doc, lat, lon)
        mark = "ok " if got == want else "BAD"
        if got != want:
            wrong.append(f"{name}: want {want}, got {got}")
        print(f"  {mark} {name:16} {want or '-':4} -> {got or '-'}")
    return wrong


def main():
    here = Path(__file__).resolve().parent
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--source", default=None,
                    help="local GeoJSON; downloads the pinned URL when omitted")
    ap.add_argument("--tolerance", type=float, default=0.004,
                    help="simplification in degrees (default 0.004, about 300 m)")
    ap.add_argument("--out", default=str(here.parent / "app" / "data" / "borders.json"))
    ap.add_argument("--verify", action="store_true",
                    help="check the result against the reference points and fail on any miss")
    args = ap.parse_args()

    source = args.source
    if source is None:
        cache = here / "_ne_50m_source.geojson"
        if not cache.exists():
            print(f"downloading {SOURCE_URL}")
            urllib.request.urlopen(SOURCE_URL, timeout=120)  # fail early on a bad URL
            urllib.request.urlretrieve(SOURCE_URL, cache)
        source = cache

    doc = build(source, args.tolerance)
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    # Separators without spaces: this file is read by machines and its size is
    # the whole reason the build script exists.
    out_path.write_text(json.dumps(doc, separators=(",", ":")), encoding="utf-8")

    size = out_path.stat().st_size
    # Each ring is a flat array of two numbers per vertex.
    vertices = sum(len(r) // 2 for e in doc["countries"].values()
                   for poly in e["polygons"] for r in poly)
    print(f"{out_path}: {len(doc['countries'])} countries, {vertices} vertices, "
          f"{size:,} bytes ({size / 1024:.1f} kB) at tolerance {args.tolerance}")

    if args.verify:
        print("verifying against reference points:")
        wrong = verify(doc)
        if wrong:
            print("\nFAILED:\n  " + "\n  ".join(wrong), file=sys.stderr)
            return 1
        print(f"all {len(VERIFY_POINTS)} reference points correct")
    return 0


if __name__ == "__main__":
    sys.exit(main())
