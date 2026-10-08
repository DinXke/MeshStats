#!/usr/bin/env python3
"""Weergavekaart bouwen: z0..13 voor het hele bronarchief, z14 alleen binnen
een bbox (standaard de Benelux). Leest tegel per tegel en schrijft meteen weg,
zodat het ook op een machine met weinig geheugen lukt (pmtiles extract vraagt
alles in één brok op). Vereist: pip install pmtiles

    python3 build_display_tiles.py bron.pmtiles uit.pmtiles [--bbox W,S,E,N] [--detail-zoom 14]
"""
import argparse
import math
import mmap
import sys
import time

from pmtiles.reader import MmapSource, Reader
from pmtiles.tile import deserialize_directory, deserialize_header, tileid_to_zxy
from pmtiles.writer import Writer


def tile_range(bbox, z):
    w, s, e, n = bbox

    def xy(lon, lat):
        k = 2 ** z
        x = int((lon + 180) / 360 * k)
        lr = math.radians(lat)
        y = int((1 - math.log(math.tan(lr) + 1 / math.cos(lr)) / math.pi) / 2 * k)
        return x, y

    x0, y1 = xy(w, s)
    x1, y0 = xy(e, n)
    return x0, x1, y0, y1


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("src")
    ap.add_argument("dst")
    ap.add_argument("--bbox", default="2.5,49.4,7.3,53.6")
    ap.add_argument("--base-max", type=int, default=13)
    ap.add_argument("--detail-zoom", type=int, default=14)
    a = ap.parse_args()
    bbox = tuple(float(v) for v in a.bbox.split(","))
    x0, x1, y0, y1 = tile_range(bbox, a.detail_zoom)

    f = open(a.src, "rb")
    mm = mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ)
    get = lambda off, ln: mm[off:off + ln]   # noqa: E731
    hdr = deserialize_header(get(0, 127))
    meta = Reader(MmapSource(f)).metadata()

    out = open(a.dst, "wb")
    w = Writer(out)
    kept = seen = 0
    t0 = time.time()

    def walk(off, ln):
        nonlocal kept, seen
        for e in deserialize_directory(get(off, ln)):
            if e.run_length == 0:                         # bladdirectory
                walk(hdr["leaf_directory_offset"] + e.offset, e.length)
                continue
            data = None
            for i in range(e.run_length):
                tid = e.tile_id + i
                z, x, y = tileid_to_zxy(tid)
                seen += 1
                keep = z <= a.base_max or (z == a.detail_zoom and x0 <= x <= x1 and y0 <= y <= y1)
                if not keep:
                    continue
                if data is None:
                    data = get(hdr["tile_data_offset"] + e.offset, e.length)
                w.write_tile(tid, data)
                kept += 1
                if kept % 200000 == 0:
                    print(f"{kept} tegels bewaard, {seen} bekeken, {time.time() - t0:.0f} s", flush=True)

    walk(hdr["root_offset"], hdr["root_length"])
    head = {k: hdr[k] for k in ("tile_type", "tile_compression", "min_lon_e7", "min_lat_e7", "max_lon_e7",
                                "max_lat_e7", "center_zoom", "center_lon_e7", "center_lat_e7")}
    w.finalize(head, meta)
    out.close()
    print(f"KLAAR: {kept} tegels in {time.time() - t0:.0f} s -> {a.dst}")


if __name__ == "__main__":
    sys.exit(main())
