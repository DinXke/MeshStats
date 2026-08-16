"""De schermafbeeldingen in ``docs/images/`` opnieuw maken.

Twee stappen, in twee terminals::

    python tools/demo_data.py --port 8472 --no-login     # laat draaien
    python tools/screenshots.py                          # schrijft docs/images/

Zie ``tools/demo_data.py`` voor waar de verzonnen data vandaan komt en waarom
er geen echte installatie in beeld mag komen.

Waarom headless Chrome en niet een echte browser met de hand
------------------------------------------------------------
Een afbeelding die met de hand gemaakt is, is bij de volgende UI-wijziging een
afbeelding die niemand durft aan te raken: het vensterformaat is vergeten, het
thema was toevallig licht, en de helft van de reeks staat op een andere breedte.
Dan blijft een verouderde afbeelding staan omdat hem hermaken duurder lijkt dan
hem laten liegen. Dit script maakt de hele reeks in één keer, altijd op 1280
breed, altijd in hetzelfde thema.

De uitsneden
------------
De beheerpagina van één node is ruim 3500 px hoog. Een afbeelding daarvan toont
niets; de secties eruit tonen wel iets. De uitsneden staan hieronder als
pixelgrenzen, en dat is het enige brosse aan dit bestand: wijzigt het sjabloon,
dan schuiven ze. Ze zijn opnieuw af te lezen door de pagina in een browser te
openen en dit in de console te plakken::

    document.querySelectorAll('section').forEach((s,i) => {
      const r = s.getBoundingClientRect(), h = s.querySelector('h2');
      console.log(i, h && h.textContent.trim(),
                  Math.round(r.top + scrollY), Math.round(r.bottom + scrollY));
    });

Het script bewaart daarnaast elke ongesneden paginaopname in een tijdelijke map
en meldt waar, zodat controleren niet betekent dat je alles opnieuw moet doen.
"""
import argparse
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

DOCS_IMAGES = Path(__file__).resolve().parent.parent.parent / "docs" / "images"

# Vaste breedte voor de hele reeks. 1280 is breed genoeg dat de beheerpagina
# niet in zijn smalle indeling schiet en smal genoeg dat de tekst op een
# gewoon scherm leesbaar blijft.
WIDTH = 1280

CHROME_CANDIDATES = [
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    "/usr/bin/google-chrome",
    "/usr/bin/chromium",
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
]

# (bestandsnaam, pad, hoogte van de opname, uitsnede of None voor de hele pagina)
#
# ``uitsnede`` is (boven, onder) in pixels vanaf de bovenkant van het document.
SHOTS = [
    ("beheer-nodes-overzicht.png", "/admin", 1400, None),
    ("beheer-node-semi-managed.png", "/admin/repeaters/3", 3600, (0, 790)),
    ("beheer-node-instellingen.png", "/admin/repeaters/3", 3600, (1300, 2420)),
    ("beheer-node-klok.png", "/admin/repeaters/3", 3600, (2436, 2895)),
    ("beheer-node-unmanaged.png", "/admin/repeaters/4", 2900, (1300, 2260)),
    ("beheer-firmware.png", "/admin/firmware", 2500, (635, 1790)),
]


def find_chrome() -> str:
    for path in CHROME_CANDIDATES:
        if Path(path).exists():
            return path
    found = shutil.which("google-chrome") or shutil.which("chromium")
    if found:
        return found
    sys.exit("Geen Chrome of Chromium gevonden; pas CHROME_CANDIDATES aan.")


def capture(chrome: str, url: str, height: int, out: Path) -> None:
    subprocess.run(
        [chrome, "--headless=new", "--disable-gpu", "--hide-scrollbars",
         "--force-device-scale-factor=1", "--virtual-time-budget=6000",
         f"--window-size={WIDTH},{height}", f"--screenshot={out}", url],
        check=True, capture_output=True,
    )
    if not out.exists():
        sys.exit(f"Chrome schreef niets voor {url}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--base", default="http://127.0.0.1:8472",
                    help="waar tools/demo_data.py draait")
    args = ap.parse_args()

    from PIL import Image

    chrome = find_chrome()
    DOCS_IMAGES.mkdir(parents=True, exist_ok=True)
    raw_dir = Path(tempfile.mkdtemp(prefix="meshmanager-shots-"))

    for name, path, height, box in SHOTS:
        raw = raw_dir / name
        capture(chrome, args.base + path, height, raw)
        target = DOCS_IMAGES / name
        if box is None:
            shutil.copyfile(raw, target)
        else:
            top, bottom = box
            with Image.open(raw) as im:
                if bottom > im.height:
                    sys.exit(f"{name}: uitsnede tot {bottom} px valt buiten een "
                             f"opname van {im.height} px -- verhoog de hoogte.")
                im.crop((0, top, WIDTH, bottom)).save(target)
        print(f"[shots] {target.name}  ({path})")

    print(f"[shots] Ongesneden opnamen staan in {raw_dir}")
    print(f"[shots] Klaar: {len(SHOTS)} afbeeldingen in {DOCS_IMAGES}")


if __name__ == "__main__":
    main()
