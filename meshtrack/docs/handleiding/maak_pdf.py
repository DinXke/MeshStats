"""handleiding.html -> MeshTrack-handleiding.pdf (A4, met paginanummers).

    pip install playwright   # gebruikt de geïnstalleerde Chrome
    python docs/handleiding/maak_pdf.py
"""
import asyncio
from pathlib import Path

from playwright.async_api import async_playwright

HERE = Path(__file__).resolve().parent
FOOT = """<div style="width:100%;font:8px Segoe UI,Arial;color:#5d6a66;padding:0 16mm;display:flex;justify-content:space-between">
<span>MeshTrack – handleiding</span><span><span class="pageNumber"></span> / <span class="totalPages"></span></span></div>"""


async def main():
    async with async_playwright() as p:
        b = await p.chromium.launch(channel="chrome")
        page = await b.new_page()
        await page.goto((HERE / "handleiding.html").as_uri())
        await page.wait_for_load_state("networkidle")
        out = HERE / "MeshTrack-handleiding.pdf"
        await page.pdf(path=str(out), format="A4", print_background=True, prefer_css_page_size=True,
                       display_header_footer=True, header_template="<div></div>", footer_template=FOOT)
        await b.close()
    print(out, round(out.stat().st_size / 1e6, 1), "MB")


asyncio.run(main())
