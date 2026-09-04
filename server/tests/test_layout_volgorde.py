"""Ontbrekende blokken in een opgeslagen indeling komen op hun standaardplek.

Het pakketfilterblok kwam later bij dan de indeling van de meeste installaties.
Wie zijn indeling ooit bewaard had, kreeg het nieuwe blok automatisch achteraan
-- onder "Overig" -- niet omdat hij dat koos maar omdat zijn opgeslagen lijst er
nog niets van wist. Een bewuste volgorde blijft wél gerespecteerd.
"""
from app import metrics


def test_ontbrekend_blok_komt_voor_het_eerste_blok_dat_er_na_hoort():
    raw = '[{"key": "status"}, {"key": "other"}, {"key": "charts"}]'
    keys = [b["key"] for b in metrics.parse_layout(raw)]
    assert keys.index("filter") < keys.index("other")
    assert keys[0] == "status"
    # Alles uit de standaard staat er precies één keer.
    assert sorted(keys) == sorted(b["key"] for b in metrics.DEFAULT_LAYOUT)


def test_bewuste_volgorde_blijft_staan():
    raw = '[{"key": "other"}, {"key": "filter"}, {"key": "status"}]'
    keys = [b["key"] for b in metrics.parse_layout(raw)]
    assert keys.index("other") < keys.index("filter") < keys.index("status")


def test_zonder_opgeslagen_indeling_de_standaard():
    assert [b["key"] for b in metrics.parse_layout(None)] == [b["key"] for b in metrics.DEFAULT_LAYOUT]
