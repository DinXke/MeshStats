"""Dezelfde sleutel, twee vormen: ``summarise`` moet er allebei mee omgaan.

De filterstand komt langs twee wegen binnen en ``channels`` heeft dan een andere
vorm: in het statistiekenbericht van onze eigen firmware is het een LIJST van
geblokkeerde kanalen (``{label, hash}``), elders een geteld aantal. ``int()`` op
zo'n lijst gooit een TypeError.

Die fout heeft de hele nodepagina EN ``/api/v1/repeaters/<slug>`` neergehaald
(2026-09-04) -- niet toen het filter gebouwd werd, maar pas op de dag dat er
werkelijk een kanaal geblokkeerd werd op een node. Tot dat moment was
``channels`` altijd leeg en deed ``int(None or 0)`` gewoon zijn werk.

Vandaar deze tests: niet één vorm vastleggen, maar bewijzen dat geen enkele vorm
een pagina meer kan neerhalen.
"""
import pytest

from app import pktfilter


def test_een_geblokkeerd_kanaal_als_lijst():
    """De vorm die de fout veroorzaakte: één kanaal, als lijst met een dict."""
    uit = pktfilter.summarise(
        {"on": True, "channels": [{"label": "#dinx", "hash": "ec"}], "drop": {"hash": 3}})
    assert uit["regels"] == 1
    assert uit["aan"] is True and uit["bekend"] is True
    assert uit["weg"] == 3


def test_hetzelfde_veld_als_aantal():
    assert pktfilter.summarise({"on": True, "channels": 2})["regels"] == 2


def test_beide_vormen_geven_hetzelfde_aantal_regels():
    lijst = pktfilter.summarise({"on": True, "channels": [{"label": "a"}, {"label": "b"}]})
    getal = pktfilter.summarise({"on": True, "channels": 2})
    assert lijst["regels"] == getal["regels"] == 2


def test_dichtgezette_types_ook_in_beide_vormen():
    assert pktfilter.summarise({"on": True, "blocked_types": ["GRP_TXT", "TRACE"]})["regels"] == 2
    assert pktfilter.summarise({"on": True, "blocked_types": 2})["regels"] == 2


@pytest.mark.parametrize("stand", [
    {"on": True, "channels": "twee"},                 # tekst waar een getal hoort
    {"on": True, "channels": {"a": 1, "b": 2}},       # een dict
    {"on": True, "channels": None, "hash": None},
    {"on": True, "hash": "twee"},
    {"on": True, "hash": [1, 2]},
    {"on": True, "blocked_types": "GRP_TXT"},
    {"on": False, "channels": [], "blocked_types": []},
])
def test_geen_enkele_vorm_haalt_de_pagina_neer(stand):
    """Dit is de eigenlijke eis. Wat er ook in die blob staat -- hij komt van een
    node, dus alles kan -- er hoort een samenvatting uit te komen en geen
    uitzondering die een pagina 500 geeft."""
    uit = pktfilter.summarise(stand)
    assert isinstance(uit["regels"], int) and uit["regels"] >= 0
    assert isinstance(uit["tekst"], str) and uit["tekst"]


def test_de_minimale_padhash_telt_alleen_als_hij_boven_een_staat():
    # 1 byte is de standaard en blokkeert niets: geen regel.
    assert pktfilter.summarise({"on": True, "hash": 1})["regels"] == 0
    assert pktfilter.summarise({"on": True, "hash": 2})["regels"] == 1
    # Ontbrekend telt als 1 (de standaard) en niet als 0, want een node die het
    # niet meldt heeft de standaard staan.
    assert pktfilter.summarise({"on": True})["regels"] == 0


def test_een_lege_stand_blijft_onbekend():
    """Niet 'uit': daar hangt de hele drie-toestandenlezing van dit scherm aan."""
    uit = pktfilter.summarise(None)
    assert uit["bekend"] is False and uit["tekst"] == "onbekend"
