# Handleiding en snelstart (PDF)

`handleiding.html` is de bron; `maak_pdf.py` maakt er `MeshTrack-handleiding.pdf` van (A4, met paginanummers, via
Playwright en de geïnstalleerde Chrome). `snelstart.html` wordt met `maak_snelstart.py` de one-pager
`MeshTrack-snelstart.pdf` (precies één A4). De screenshots in `img/` komen van een lokale demo-instantie met virtuele
trackers rond Hasselt (niet verbonden met de echte mesh).

Bijwerken na een wijziging die de handleiding raakt:

1. Tekst aanpassen in `handleiding.html` (en dezelfde uitleg in `server/static/help.html`, de snelstart en `README.md`).
2. Eventueel nieuwe screenshots maken van de demo-instantie.
3. Beide PDF's maken met de venv (nooit de Microsoft Store-Python):
   `local/pwvenv311/Scripts/python.exe docs/handleiding/maak_pdf.py` en `.../maak_snelstart.py`.
4. De PDF's kopiëren naar `server/static/` (de helppagina linkt ernaar) en elke pagina nakijken.
