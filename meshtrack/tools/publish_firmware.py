#!/usr/bin/env python3
"""MeshTrack-firmware bouwen en publiceren in de webinterface (server/static/firmware/).

Per bord: PlatformIO-build (env), het DFU-pakket (.zip) kopiëren, een .uf2 maken (alleen de app,
vanaf het begin van de app voor dat bord), het pakket controleren (softdevice, device_type, crc16
van de app) en de versie bovenaan firmware.json zetten, met per bord een eigen ingang.

    python tools/publish_firmware.py 0.9.7 "notities"                 # alle borden bouwen en publiceren
    python tools/publish_firmware.py 0.9.7 "notities" --board wismesh_tag
    python tools/publish_firmware.py 0.9.7 "notities" --no-build      # bestaande builds publiceren

Een tweede publicatie van dezelfde versie overschrijft die versie (geen dubbele ingang); borden die nu
niet gebouwd worden, behouden hun gegevens uit de vorige publicatie van die versie.

firmware.json, per release:
  version, date, notes, meshcore,
  zip, zip_sha256, uf2, app_size          (T1000-E, voor oudere webpagina's)
  boards: { <bord>: { zip, zip_sha256, uf2, uf2_sha256, app_size, app_start, sd_req, device_type,
                      uf2_family, name } }
"""
import argparse
import datetime
import hashlib
import json
import shutil
import struct
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MESHCORE = Path("C:/Users/Public/MeshCore-std")
PIO = Path("C:/.platformio/penv/Scripts/pio.exe")
OUT = ROOT / "server" / "static" / "firmware"
UF2_FAMILY = 0xADA52840          # nRF52840 (Adafruit-bootloader)

# Borden: env, bestandsnaam, begin van de app (na de SoftDevice) en de SoftDevice die de bootloader eist.
BOARDS = {
    "t1000e": {
        "name": "Seeed T1000-E", "env": "t1000e_meshtrack", "prefix": "meshtrack-t1000e",
        "app_start": 0x27000, "sd_req": [0x0123],      # S140 v7.3.0
    },
    "wismesh_tag": {
        "name": "RAK WisMesh Tag", "env": "rak_wismesh_tag_meshtrack", "prefix": "meshtrack-wismesh-tag",
        "app_start": 0x26000, "sd_req": [0x00B6],      # S140 v6.1.1
    },
    "rak3401_1w": {
        "name": "RAK19007 + RAK3401 + RAK13302 (1 W)", "env": "rak3401_meshtrack", "prefix": "meshtrack-rak3401-1w",
        "app_start": 0x26000, "sd_req": [0x00B6],      # S140 v6.1.1
    },
}
DEVICE_TYPE = 0x0052             # Adafruit nRF52 (nrfutil --dev-type 0x0052)


def make_uf2(binary: bytes, start: int) -> bytes:
    blocks = [binary[i:i + 256] for i in range(0, len(binary), 256)]
    out = b""
    for n, chunk in enumerate(blocks):
        chunk = chunk.ljust(256, b"\x00")
        hdr = struct.pack("<8I", 0x0A324655, 0x9E5D5157, 0x2000, start + n * 256, 256, n, len(blocks), UF2_FAMILY)
        out += hdr + chunk + b"\x00" * (476 - 256) + struct.pack("<I", 0x0AB16F30)
    return out


def crc16_ccitt(data: bytes, crc: int = 0xFFFF) -> int:
    """Zoals nrfutil (init 0xFFFF, poly 0x1021)."""
    for b in data:
        crc = (crc >> 8 & 0xFF) | (crc << 8 & 0xFFFF)
        crc ^= b
        crc ^= (crc & 0xFF) >> 4
        crc ^= (crc << 12) & 0xFFFF
        crc ^= ((crc & 0xFF) << 5) & 0xFFFF
    return crc & 0xFFFF


def build(env: str):
    print(f"bouwen: {env} ...", flush=True)
    r = subprocess.run([str(PIO), "run", "-e", env], cwd=MESHCORE, timeout=1800)
    if r.returncode != 0:
        sys.exit(f"build van {env} MISLUKT (code {r.returncode})")


def check_package(board: str, cfg: dict, zsrc: Path) -> tuple:
    with zipfile.ZipFile(zsrc) as z:
        manifest = json.loads(z.read("manifest.json"))
        appm = manifest["manifest"]["application"]
        app = z.read(appm["bin_file"])
    ip = appm.get("init_packet_data", {})
    sd = ip.get("softdevice_req", [])
    dev = ip.get("device_type")
    crc = ip.get("firmware_crc16")
    errors = []
    if sorted(sd) != sorted(cfg["sd_req"]):
        errors.append(f"softdevice_req {sd} != verwacht {cfg['sd_req']}")
    if dev != DEVICE_TYPE:
        errors.append(f"device_type {dev} != {DEVICE_TYPE}")
    if crc is not None and crc != crc16_ccitt(app):
        errors.append(f"firmware_crc16 {crc} klopt niet met de app ({crc16_ccitt(app)})")
    if not (100_000 < len(app) < 0xD4000 - cfg["app_start"]):
        errors.append(f"app-grootte {len(app)} buiten bereik")
    # vectortabel: begin-SP in RAM, reset-vector in de app
    sp, reset = struct.unpack_from("<II", app, 0)
    if not (0x20000000 < sp <= 0x20040000):
        errors.append(f"begin-SP 0x{sp:08X} niet in RAM")
    if not (cfg["app_start"] < (reset & ~1) < cfg["app_start"] + len(app)):
        errors.append(f"reset-vector 0x{reset:08X} niet in de app (0x{cfg['app_start']:X}..)")
    if errors:
        sys.exit(f"{board}: DFU-pakket afgekeurd: " + "; ".join(errors))
    return app, sd, dev


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("version")
    ap.add_argument("notes")
    ap.add_argument("--board", choices=sorted(BOARDS), action="append",
                    help="alleen dit bord (mag meermaals); standaard alle borden")
    ap.add_argument("--no-build", action="store_true", help="niet bouwen, bestaande builds publiceren")
    a = ap.parse_args()
    boards = a.board or list(BOARDS)
    OUT.mkdir(parents=True, exist_ok=True)

    meta_path = OUT / "firmware.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.exists() else {"releases": []}
    old = next((r for r in meta["releases"] if r["version"] == a.version), None)
    entry = {
        "version": a.version,
        "date": datetime.date.today().isoformat(),
        "notes": a.notes,
        "meshcore": "v1.17.1",
        "boards": dict(old.get("boards", {})) if old else {},
    }
    if old and "boards" not in old and "zip" in old:      # oudere ingang: alleen T1000-E
        entry["boards"]["t1000e"] = {k: old[k] for k in ("zip", "zip_sha256", "uf2", "app_size") if k in old}

    for board in boards:
        cfg = BOARDS[board]
        if not a.no_build:
            build(cfg["env"])
        zsrc = MESHCORE / ".pio" / "build" / cfg["env"] / "firmware.zip"
        app, sd, dev = check_package(board, cfg, zsrc)
        zname = f"{cfg['prefix']}-{a.version}.zip"
        uname = f"{cfg['prefix']}-{a.version}.uf2"
        shutil.copyfile(zsrc, OUT / zname)
        uf2 = make_uf2(app, cfg["app_start"])
        (OUT / uname).write_bytes(uf2)
        entry["boards"][board] = {
            "name": cfg["name"],
            "zip": zname, "zip_sha256": hashlib.sha256((OUT / zname).read_bytes()).hexdigest(),
            "uf2": uname, "uf2_sha256": hashlib.sha256(uf2).hexdigest(),
            "app_size": len(app), "app_start": f"0x{cfg['app_start']:X}",
            "sd_req": [f"0x{x:04X}" for x in sd], "device_type": f"0x{dev:04X}",
            "uf2_family": f"0x{UF2_FAMILY:08X}",
        }
        print(f"gepubliceerd: {board}: {zname} ({len(app)} bytes app, sd {entry['boards'][board]['sd_req']}), {uname}")

    t = entry["boards"].get("t1000e")
    if t:                                                  # bovenste velden: oudere webpagina's (T1000-E)
        entry.update({"zip": t["zip"], "zip_sha256": t["zip_sha256"], "uf2": t["uf2"], "app_size": t["app_size"]})
    meta["releases"] = [r for r in meta["releases"] if r["version"] != a.version]
    meta["releases"].insert(0, entry)
    meta["latest"] = a.version
    meta_path.write_text(json.dumps(meta, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
