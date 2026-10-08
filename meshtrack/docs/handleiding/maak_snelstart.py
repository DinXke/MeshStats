"""snelstart.html -> MeshTrack-snelstart.pdf (één A4, zonder marges).

    python docs/handleiding/maak_snelstart.py
"""
import asyncio
from pathlib import Path

from playwright.async_api import async_playwright

HERE = Path(__file__).resolve().parent


async def main():
    async with async_playwright() as p:
        b = await p.chromium.launch(channel="chrome")
        page = await b.new_page()
        await page.goto((HERE / "snelstart.html").as_uri())
        await page.wait_for_load_state("networkidle")
        out = HERE / "MeshTrack-snelstart.pdf"
        await page.pdf(path=str(out), format="A4", print_background=True, prefer_css_page_size=True,
                       margin={"top": "0", "bottom": "0", "left": "0", "right": "0"})
        await b.close()
    print(out, round(out.stat().st_size / 1e3), "kB")


asyncio.run(main())
