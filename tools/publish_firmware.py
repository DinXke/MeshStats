#!/usr/bin/env python3
"""Een firmwarebuild publiceren in de webinterface (server/static/firmware/).

Kopieert het DFU-pakket (.zip) en maakt een .uf2 (app-only, 0x27000), berekent
de SHA-256 en zet de versie bovenaan firmware.json, met de wijzigingen.

    python tools/publish_firmware.py 0.2.1 "SOS door vasthouden; led uit in trackermodus"
"""
import datetime
import hashlib
import json
import shutil
import struct
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BUILD = Path("C:/Users/Public/MeshCore-std/.pio/build/t1000e_meshtrack")
OUT = ROOT / "server" / "static" / "firmware"
APP_START = 0x27000
UF2_FAMILY = 0xADA52840


def make_uf2(binary: bytes, start: int) -> bytes:
    blocks = [binary[i:i + 256] for i in range(0, len(binary), 256)]
    out = b""
    for n, chunk in enumerate(blocks):
        chunk = chunk.ljust(256, b"\x00")
        hdr = struct.pack("<8I", 0x0A324655, 0x9E5D5157, 0x2000, start + n * 256, 256, n, len(blocks), UF2_FAMILY)
        out += hdr + chunk + b"\x00" * (476 - 256) + struct.pack("<I", 0x0AB16F30)
    return out


def main():
    if len(sys.argv) < 3:
        sys.exit(__doc__)
    version, notes = sys.argv[1], sys.argv[2]
    OUT.mkdir(parents=True, exist_ok=True)
    zsrc = BUILD / "firmware.zip"
    zname = f"meshtrack-t1000e-{version}.zip"
    shutil.copyfile(zsrc, OUT / zname)
    with zipfile.ZipFile(zsrc) as z:
        manifest = json.loads(z.read("manifest.json"))
        app = z.read(manifest["manifest"]["application"]["bin_file"])
    uname = f"meshtrack-t1000e-{version}.uf2"
    (OUT / uname).write_bytes(make_uf2(app, APP_START))

    meta_path = OUT / "firmware.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.exists() else {"releases": []}
    meta["releases"] = [r for r in meta["releases"] if r["version"] != version]
    meta["releases"].insert(0, {
        "version": version,
        "date": datetime.date.today().isoformat(),
        "notes": notes,
        "zip": zname, "zip_sha256": hashlib.sha256((OUT / zname).read_bytes()).hexdigest(),
        "uf2": uname, "app_size": len(app),
        "meshcore": "v1.17.1",
    })
    meta["latest"] = version
    meta_path.write_text(json.dumps(meta, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"gepubliceerd: {zname} ({len(app)} bytes app), {uname}")


if __name__ == "__main__":
    main()
