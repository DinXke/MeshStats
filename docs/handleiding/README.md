# Handleiding (PDF)

`handleiding.html` is de bron; `maak_pdf.py` maakt er `MeshTrack-handleiding.pdf` van (A4, met paginanummers, via
Playwright en de geïnstalleerde Chrome). De screenshots in `img/` komen van een lokale demo-instantie met virtuele
trackers rond Hasselt (niet verbonden met de echte mesh).

Bijwerken na een wijziging die de handleiding raakt:

1. Tekst aanpassen in `handleiding.html` (en dezelfde uitleg in `server/static/help.html`).
2. Eventueel nieuwe screenshots maken van de demo-instantie.
3. `python docs/handleiding/maak_pdf.py` en de PDF kopiëren naar `server/static/MeshTrack-handleiding.pdf`
   (de helppagina linkt ernaar).
