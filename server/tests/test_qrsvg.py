"""De ingebouwde QR-encoder (qrsvg.py).

Er zit geen decoder in de testomgeving, dus deze test verankert de code op de
tussenstappen die met de hand na te rekenen zijn tegen de norm (ISO/IEC 18004):
de format-bits en versie-bits (BCH, met gepubliceerde vectoren), de versiekeuze
op de capaciteitsgrens, en de vaste patronen in de matrix. Samen met de
render-test in test_rooms.py -- die een echte QR in de pagina zet -- is dat genoeg
om te merken dat er iets stukgaat.
"""
import pytest

from app import qrsvg


def test_format_bits_matchen_de_gepubliceerde_vectoren():
    """Niveau M, masker 0 en 1: de 15-bit format-strings staan in de norm."""
    assert "".join(map(str, qrsvg._format_bits(0))) == "101010000010010"
    assert "".join(map(str, qrsvg._format_bits(1))) == "101000100100101"


def test_versie_bits_matchen_de_norm():
    """De 18-bit versie-informatie voor versie 7 is een bekende vector."""
    assert "".join(map(str, qrsvg._versie_bits(7))) == "000111110010010100"


def test_de_versietabel_klopt_met_de_bekende_totalen():
    """Databytes * blokken per versie, tegen de tabel van niveau M."""
    verwacht = {1: 16, 2: 28, 3: 44, 4: 64, 5: 86,
                6: 108, 7: 124, 8: 154, 9: 182, 10: 216}
    for versie, aantal in verwacht.items():
        assert qrsvg._data_codewoorden(versie) == aantal, versie


def test_versiekeuze_valt_op_de_capaciteitsgrens_om():
    # Versie 1-M draagt 16 databytes = 128 bits; 4 (modus) + 8 (tel) laat 14 bytes
    # over. Byte vijftien past er niet meer in en tilt naar versie 2.
    assert qrsvg._kies_versie(b"a" * 14) == 1
    assert qrsvg._kies_versie(b"a" * 15) == 2


def test_een_te_lange_tekst_wordt_geweigerd():
    with pytest.raises(ValueError):
        qrsvg._kies_versie(b"a" * 300)


def test_de_matrix_heeft_de_juiste_maat_en_zoekpatronen():
    m = qrsvg._matrix(b"HELLO")
    assert len(m) == 21 and all(len(r) == 21 for r in m)
    # De drie zoekers: hun donkere 3x3-kern staat op vaste plekken.
    for r, c in ((3, 3), (3, 17), (17, 3)):
        assert m[r][c] is True
    # De vaste donkere module net onder de linkerbovenzoeker.
    assert m[len(m) - 8][8] is True


def test_svg_is_zelfstandig_en_bevat_een_pad():
    svg = qrsvg.svg("meshcore://join/abc", titel="join Storingen")
    assert svg.startswith("<svg") and "viewBox" in svg
    assert "<path" in svg and "join Storingen" in svg
    # Geen externe verwijzing: alles staat in de string zelf.
    assert "http://" not in svg.replace("http://www.w3.org/2000/svg", "")


def test_een_lange_join_uri_rendert_zonder_fout():
    lang = "meshcore://join?g=" + "A1b2C3d4" * 20
    svg = qrsvg.svg(lang)
    assert svg.startswith("<svg")
