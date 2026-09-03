"""Wat pfguard mag beweren, en vooral wat niet.

Deze module hangt in de schrijfweg van het pakketfilter en kan een wijziging
tegenhouden. Wat hier vastligt is daarom niet alleen "rekent hij goed" maar ook
"zwijgt hij als hij niets weet" -- een slot dat afgaat op een verzonnen cijfer is
erger dan geen slot, want het wordt weggeklikt en daarna genegeerd.
"""

import pytest

from app import pfguard


# --- wanneer deze module iets te zeggen heeft ---------------------------------
#
# Alleen `hash`. Zie de kop van pfguard: van de andere regels is het afgeknipte
# deel niet mesh-breed te meten, en een aannemelijk cijfer is hier geen cijfer.

@pytest.mark.parametrize("cmd", [
    "hash 1", "hash 2", "hash 3",
    "filter hash 2", "  HASH 2  ",
])
def test_hash_regels_worden_gewogen(cmd, monkeypatch):
    monkeypatch.setattr(pfguard, "_flood_hash_verdeling",
                        lambda sample=0: {"verdeling": {1: 10}, "onleesbaar": 0, "gemeten": 10})
    assert pfguard.check(cmd)["van_toepassing"] is True


@pytest.mark.parametrize("cmd", [
    "", "on", "off", "reset", "malformed on", "hops 05 3",
    "rate 04 20 60", "channel add x #ab", "hash", "hash 4", "hash 0",
    "hash 2 extra", "type 05 off",
])
def test_andere_regels_worden_niet_gewogen(cmd):
    oordeel = pfguard.check(cmd)
    assert oordeel["van_toepassing"] is False
    assert oordeel["zwaar"] is False
    assert oordeel["tekst_nl"] == ""


# --- het gemeten oordeel ------------------------------------------------------

def _meting(verdeling):
    return {"verdeling": verdeling, "onleesbaar": 0,
            "gemeten": sum(verdeling.values())}


def test_zonder_meetbasis_zwijgt_hij(monkeypatch):
    """Geen pakketten betekent geen bewering -- ook niet '0% valt weg'."""
    monkeypatch.setattr(pfguard, "_flood_hash_verdeling",
                        lambda sample=0: _meting({}))
    oordeel = pfguard.check("hash 3")
    assert oordeel["van_toepassing"] is True
    assert oordeel["zwaar"] is False
    assert oordeel["gemeten"] == 0
    assert oordeel["tekst_nl"] == ""


def test_hash_1_knipt_nooit_iets_af(monkeypatch):
    """1 is de kleinst mogelijke padhash, dus er valt niets onder."""
    monkeypatch.setattr(pfguard, "_flood_hash_verdeling",
                        lambda sample=0: _meting({1: 700, 2: 250, 3: 50}))
    oordeel = pfguard.check("hash 1")
    assert oordeel["aandeel"] == 0.0
    assert oordeel["zwaar"] is False
    assert oordeel["tekst_nl"] == ""


def test_het_afgeknipte_deel_wordt_gemeten_en_niet_geschat(monkeypatch):
    # Zoals in dit mesh werkelijk gemeten: het meeste flood-verkeer is 1-byte.
    monkeypatch.setattr(pfguard, "_flood_hash_verdeling",
                        lambda sample=0: _meting({1: 688, 2: 300, 3: 12}))
    oordeel = pfguard.check("hash 2")
    assert oordeel["gemeten"] == 1000
    assert oordeel["aandeel"] == pytest.approx(0.688)
    assert oordeel["zwaar"] is True
    # Het getal hoort in de zin te staan: "wees voorzichtig" wordt weggeklikt,
    # "68.8% valt weg" niet.
    assert "68.8%" in oordeel["tekst_nl"]
    assert "68.8%" in oordeel["tekst_en"]
    # En de weg vooruit ook, want dat was het voorbehoud van de gebruiker.
    assert "companion" in oordeel["tekst_nl"].lower()


def test_hash_3_telt_ook_de_2_bytes_mee(monkeypatch):
    monkeypatch.setattr(pfguard, "_flood_hash_verdeling",
                        lambda sample=0: _meting({1: 400, 2: 400, 3: 200}))
    oordeel = pfguard.check("hash 3")
    assert oordeel["aandeel"] == pytest.approx(0.8)
    assert oordeel["zwaar"] is True


def test_onder_de_drempel_is_het_een_detail_en_geen_besluit(monkeypatch):
    monkeypatch.setattr(pfguard, "_flood_hash_verdeling",
                        lambda sample=0: _meting({1: 5, 2: 995}))
    oordeel = pfguard.check("hash 2")
    assert oordeel["aandeel"] == pytest.approx(0.005)
    assert oordeel["zwaar"] is False
    # Wel gemeld -- er valt iets weg -- maar het houdt niets tegen.
    assert oordeel["tekst_nl"]


def test_de_vorm_is_altijd_dezelfde():
    """De aanroeper hoort geen twee codepaden nodig te hebben."""
    velden = {"van_toepassing", "zwaar", "aandeel", "gemeten", "min_bytes",
              "tekst_nl", "tekst_en"}
    for cmd in ("hash 2", "malformed on", "", None):
        assert set(pfguard.check(cmd)) == velden
