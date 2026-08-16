#!/usr/bin/env python3
"""Controleer dat een gebouwd .bin de MeshManager-module werkelijk bevat.

Waarom dit bestaat. De module wordt niet ingeschakeld door hem mee te
compileren, maar door de aanroepen ernaartoe in examples/simple_repeater --
main.cpp en MyMesh.cpp -- en die staan achter `#ifdef MESHMANAGER_NET`. Staat
die define niet in de buildomgeving, dan compileert MeshManagerNet.cpp keurig,
verwijst niemand ernaar, gooit de linker alles weg met --gc-sections en rolt er
een image uit dat een doodgewone MeshCore-repeater is. Zonder foutmelding.

Dat is de gevaarlijkste uitkomst die dit project kent: een release die
stilzwijgend een gewone repeater bevat wordt door de site aangeboden als
upgrade, een node op een dak installeert hem, en daarna is er geen beheerpagina
meer om hem mee terug te draaien. Een gefaalde build is daarbij vergeleken een
prettige middag.

Gecontroleerd wordt daarom niet of het bouwde, maar of de drie dingen die de
site en de node nodig hebben werkelijk in de bytes staan:

  * MESHMANAGER_NAME    -- bewijs dat de module meegelinkt is en niet weggegooid
  * MESHMANAGER_VERSION -- de versie die 'ver' en /api/fw zullen melden
  * MESHMANAGER_ENV     -- de bouwomgeving; zonder deze weigert de site de node
                           te upgraden omdat ze niet kan zien welk image bij
                           welk bord hoort

De verwachte naam en versie worden uit MeshManagerNet.h gelezen, niet hier
herhaald: twee plaatsen met dezelfde waarde zijn twee plaatsen die uit elkaar
gaan lopen, en dan controleert dit script zichzelf in plaats van het image.

Gebruik:
    verify_image.py firmware.bin --env heltec_v4_repeater_meshmanager

Bewust zonder afhankelijkheden; het draait in CI naast release_notes.py.
"""

import argparse
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
HEADER = HERE.parent / "examples" / "simple_repeater" / "MeshManagerNet.h"


def _define(name: str) -> str:
    text = HEADER.read_text(encoding="utf-8")
    m = re.search(r'#define\s+' + name + r'\s+"([^"]+)"', text)
    if not m:
        sys.exit(f"{name} niet gevonden in {HEADER}")
    return m.group(1)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("binary", help="pad naar het gebouwde firmware.bin")
    ap.add_argument("--env", required=True,
                    help="PlatformIO-omgeving waarmee gebouwd is; moet als "
                         "MESHMANAGER_ENV in het image staan")
    args = ap.parse_args()

    path = Path(args.binary)
    if not path.is_file():
        sys.exit(f"geen image op {path}")
    blob = path.read_bytes()

    want = {
        "MESHMANAGER_NAME": _define("MESHMANAGER_NAME"),
        "MESHMANAGER_VERSION": _define("MESHMANAGER_VERSION"),
        "MESHMANAGER_ENV": args.env,
    }

    missing = []
    for label, value in want.items():
        found = value.encode("utf-8") in blob
        print(f"{'ok  ' if found else 'WEG '} {label} = {value!r}")
        if not found:
            missing.append(label)

    print(f"     {path.name}, {len(blob)} bytes")

    if missing:
        sys.exit(
            "\nDit image mist " + ", ".join(missing) + ".\n"
            "Bij MESHMANAGER_NAME betekent dat vrijwel altijd dat de hooks in "
            "examples/simple_repeater ontbreken (firmware/repeater-hooks.patch "
            "niet toegepast) of dat -D MESHMANAGER_NET niet in de "
            "buildomgeving staat: de module is dan meegecompileerd maar door "
            "de linker weggegooid, en dit is een gewone MeshCore-repeater.\n"
            "Bij MESHMANAGER_ENV ontbreekt -D MESHMANAGER_ENV='\"$PIOENV\"' in "
            "firmware/platformio.ci.ini; de site weigert dan te upgraden."
        )


if __name__ == "__main__":
    main()
