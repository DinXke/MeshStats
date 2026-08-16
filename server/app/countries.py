"""Turn a coordinate into an ISO country code, offline.

The outlines live in ``app/data/borders.json``, built by
``tools/build_borders.py`` from Natural Earth 1:50m (public domain). That script
is the record of where the data came from and how to rebuild it; read it before
touching the file.

Two rules shape this module:

**No network, ever.** The file is on disk or it is not. A site that phones a
geocoding service to draw a flag would break the moment that service does, and
would leak every node position it holds while it worked.

**Missing data is not an error.** If the file is absent or unreadable,
``available()`` is False and ``lookup()`` returns None for everything. The
country filter then simply does not appear; nothing else on the page notices.
That keeps a deployment that skipped the build step working instead of failing.

What "no country" means
-----------------------
None is returned for a position outside every outline: at sea, outside the
region the file covers, or within a few hundred metres of a coast the source
draws more coarsely than reality. None is an honest answer and the UI shows it
as its own category. Do not replace it with a nearest-country guess -- the whole
point of classifying against real borders was to stop guessing.
"""
import json
import logging
import threading
from pathlib import Path

log = logging.getLogger("meshmanager.countries")

BORDERS_PATH = Path(__file__).resolve().parent / "data" / "borders.json"

_lock = threading.Lock()
_loaded = False
_index: list = []          # [(code, (min_lon, min_lat, max_lon, max_lat), [polygons])]
_meta: dict = {}


def _decode_ring(flat, scale):
    """Flat [lon0, lat0, dlon, dlat, ...] integers back to float lon/lat pairs.

    Mirrors decode_ring() in tools/build_borders.py; the two must stay in step.
    """
    lon, lat = flat[0], flat[1]
    ring = [(lon / scale, lat / scale)]
    for i in range(2, len(flat), 2):
        lon += flat[i]
        lat += flat[i + 1]
        ring.append((lon / scale, lat / scale))
    return ring


def _load() -> None:
    """Read and decode the outlines once. Decoding up front costs a few
    milliseconds at startup and saves it on every position we classify."""
    global _loaded, _index, _meta
    if _loaded:
        return
    _loaded = True
    try:
        doc = json.loads(BORDERS_PATH.read_text(encoding="utf-8"))
        scale = doc["scale"]
        index = []
        for code, entry in doc["countries"].items():
            bbox = tuple(v / scale for v in entry["bbox"])
            polys = [[_decode_ring(r, scale) for r in rings]
                     for rings in entry["polygons"]]
            index.append((code, bbox, polys))
        _index = index
        _meta = {k: v for k, v in doc.items() if k != "countries"}
        log.info("country borders: %d countries from %s",
                 len(_index), doc.get("source", "?"))
    except FileNotFoundError:
        log.info("no %s; the country filter stays off", BORDERS_PATH.name)
    except (ValueError, KeyError, TypeError) as err:
        # A corrupt file is a deployment problem, not a reason to refuse to
        # serve pages: log it loudly and carry on without the feature.
        log.warning("could not read %s (%s); the country filter stays off",
                    BORDERS_PATH, err)


def available() -> bool:
    with _lock:
        _load()
    return bool(_index)


def meta() -> dict:
    """Provenance of the loaded outlines, for the admin page and the logs."""
    with _lock:
        _load()
    return dict(_meta)


def _point_in_ring(lon: float, lat: float, ring) -> bool:
    """Ray casting: count how often a ray to the west crosses the ring."""
    inside = False
    n = len(ring)
    j = n - 1
    for i in range(n):
        xi, yi = ring[i]
        xj, yj = ring[j]
        if (yi > lat) != (yj > lat):
            if lon < (xj - xi) * (lat - yi) / (yj - yi) + xi:
                inside = not inside
        j = i
    return inside


def lookup(lat, lon) -> str | None:
    """ISO 3166-1 alpha-2 code for a position, or None if no outline holds it."""
    if not isinstance(lat, (int, float)) or not isinstance(lon, (int, float)):
        return None
    with _lock:
        _load()
        index = _index
    for code, (min_lon, min_lat, max_lon, max_lat), polys in index:
        # The bounding box rejects almost every country for almost every point,
        # which is what keeps this cheap enough to run inline during ingest.
        if not (min_lon <= lon <= max_lon and min_lat <= lat <= max_lat):
            continue
        for rings in polys:
            if not _point_in_ring(lon, lat, rings[0]):
                continue
            # A hole is what makes San Marino not Italy.
            if any(_point_in_ring(lon, lat, hole) for hole in rings[1:]):
                continue
            return code
    return None
