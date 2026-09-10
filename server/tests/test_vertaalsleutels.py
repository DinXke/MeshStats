"""Elke sleutel die het script zelf opbouwt, moet in BEIDE talen bestaan.

Waarom dit een test verdient. De sjablonen dragen hun Nederlandse tekst als
inhoud (``data-i18n`` wisselt hem om), dus daar valt een ontbrekende vertaling
terug op leesbaar Nederlands. Voor tekst die app.js zelf maakt geldt dat niet: die
gaat door ``t("sleutel")`` en levert bij een ontbrekende sleutel de RUWE SLEUTEL
op de pagina op -- "map.zoom_title" onder een knop. Dat is precies het soort fout
die niemand ziet tot een bezoeker op Engels staat.

De sleutels die het script SAMENSTELT (``t("metric." + naam)``) laten hier alleen
hun voorvoegsel achter en eindigen dus op een punt; die worden overgeslagen. Wat
overblijft zijn de letterlijke sleutels, en die horen alle in nl én en te staan.
"""
import io
import re
from pathlib import Path

import pytest

STATIC = Path(__file__).resolve().parent.parent / "app" / "static"
SCRIPTS = ["app.js", "companions.js"]


def _tabellen():
    """{taal: {sleutels}} uit i18n.js, per taalblok gesplitst."""
    tekst = io.open(STATIC / "i18n.js", encoding="utf-8").read()
    # De blokken beginnen met "  <taal>: {" op eigen regel; het volgende blok (of
    # het einde van DICT) sluit het vorige af.
    grenzen = [(m.group(1), m.start()) for m in re.finditer(r"^\s{4}([a-z]{2}):\s*\{", tekst, re.M)]
    assert grenzen, "geen taalblokken gevonden in i18n.js"
    uit = {}
    for taal, start in grenzen:
        # Tot het } dat het blok sluit, en NIET tot het volgende blok of het
        # einde van het bestand: verderop in i18n.js staat gewone code met
        # "en" : erin, en die zou als sleutel meekomen.
        eind = tekst.index(chr(10) + "    }", start)
        uit[taal] = set(re.findall(r'"([A-Za-z0-9_.]+)"\s*:', tekst[start:eind]))
    return uit


def _gebruikte_sleutels():
    uit = set()
    for naam in SCRIPTS:
        pad = STATIC / naam
        if not pad.exists():
            continue
        js = io.open(pad, encoding="utf-8").read()
        for sleutel in re.findall(r'\bt\(\s*"([A-Za-z0-9_.]+)"', js):
            # Een sleutel die het script SAMENSTELT laat hier zijn voorvoegsel
            # achter -- t("metric." + naam), t("arch.f_" + veld) -- en dat is geen
            # sleutel om op te zoeken.
            if not sleutel.endswith((".", "_")):
                uit.add(sleutel)
    return uit


def test_er_zijn_twee_taalblokken():
    talen = _tabellen()
    assert "nl" in talen and "en" in talen
    # Een leeg blok zou elke test hieronder gratis laten slagen.
    assert len(talen["nl"]) > 100 and len(talen["en"]) > 100


@pytest.mark.parametrize("taal", ["nl", "en"])
def test_elke_gebruikte_sleutel_bestaat(taal):
    tabel = _tabellen()[taal]
    mist = sorted(s for s in _gebruikte_sleutels() if s not in tabel)
    assert mist == [], "ontbreekt in %s: %s" % (taal, ", ".join(mist))


def test_de_twee_talen_dekken_dezelfde_sleutels():
    """Niet alleen wat het script gebruikt: een sleutel die maar in een van de
    twee tabellen staat, is een tekst die in de andere taal terugvalt op de
    sleutelnaam zodra iets hem opvraagt."""
    talen = _tabellen()
    alleen_nl = sorted(talen["nl"] - talen["en"])
    alleen_en = sorted(talen["en"] - talen["nl"])
    assert (alleen_nl, alleen_en) == ([], []), \
        "alleen in nl: %s | alleen in en: %s" % (", ".join(alleen_nl), ", ".join(alleen_en))
