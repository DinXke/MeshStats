"""Een QR-code als SVG, zonder een extern pakket.

Waarom dit hier staat en niet als dependency
--------------------------------------------
De join-link van een room is een lange tekst, en een QR ernaast is het verschil
tussen "typ deze veertig tekens over op je telefoon" en "richt de camera erop".
De site heeft geen bouwstap en de pagina's zijn self-contained achter een strak
``Content-Security-Policy`` (zie main.py): een QR-bibliotheek van een CDN mag er
niet in, en een pakket erbij op de server zou een stille dependency zijn die bij
de volgende ``pip install`` mee moet. Vandaar een kleine, volledige encoder hier
-- byte-modus, foutcorrectie niveau M, versies 1 t/m 10 -- die een ``<svg>``
teruggeeft dat rechtstreeks in de pagina gaat. Ruim genoeg voor een join-URI
(versie 10-M draagt 216 databytes) en klein genoeg om te overzien.

Het is de standaardcode uit ISO/IEC 18004: modusindicator, tekental, data, de
Reed-Solomon-foutcorrectie over GF(256), de vaste patronen (zoekers, timing,
uitlijning), en de acht maskers met hun strafscore waarvan de laagste wint. Er
zit geen decoder bij -- de test bewaakt de tussenstappen die met de hand na te
rekenen zijn (de format-bits, de matrixmaat, de zoekpatronen) plus dat elke
lengte tot de grens een geldige versie oplevert.
"""
from __future__ import annotations

# --- GF(256), de rekenwereld van Reed-Solomon --------------------------------
#
# De QR-norm rekent in het lichaam GF(256) met het priempolynoom 0x11d. De twee
# tabellen (macht en logaritme) maken vermenigvuldigen een optelling van
# logaritmen, precies zoals een rekenliniaal.
_EXP = [0] * 512
_LOG = [0] * 256


def _init_gf() -> None:
    x = 1
    for i in range(255):
        _EXP[i] = x
        _LOG[x] = i
        x <<= 1
        if x & 0x100:
            x ^= 0x11D
    for i in range(255, 512):
        _EXP[i] = _EXP[i - 255]


_init_gf()


def _gf_mul(a: int, b: int) -> int:
    if a == 0 or b == 0:
        return 0
    return _EXP[_LOG[a] + _LOG[b]]


def _rs_generator(n: int) -> list[int]:
    """Het generatorpolynoom van graad ``n`` voor ``n`` foutcorrectiecodewoorden."""
    g = [1]
    for i in range(n):
        # g(x) *= (x - a^i)
        nieuw = [0] * (len(g) + 1)
        for j, coef in enumerate(g):
            nieuw[j] ^= coef
            nieuw[j + 1] ^= _gf_mul(coef, _EXP[i])
        g = nieuw
    return g


def _rs_ec(data: list[int], n: int) -> list[int]:
    """De ``n`` foutcorrectiecodewoorden bij een blok databytes."""
    gen = _rs_generator(n)
    rest = [0] * n
    for byte in data:
        factor = byte ^ rest[0]
        rest = rest[1:] + [0]
        if factor != 0:
            lg = _LOG[factor]
            for i, gcoef in enumerate(gen[1:]):
                rest[i] ^= _EXP[lg + _LOG[gcoef]] if gcoef else 0
    return rest


# --- de versietabel (alleen niveau M, versies 1..10) -------------------------
#
#   versie -> (ec-codewoorden per blok, [(aantal blokken, databytes per blok), ...])
#
# De getallen komen letterlijk uit de tabel van ISO/IEC 18004 voor niveau M.
# test_qrsvg.py rekent per versie na dat databytes*blokken klopt met de bekende
# totalen, zodat een tikfout hier opvalt.
_VERSIES = {
    1:  (10, [(1, 16)]),
    2:  (16, [(1, 28)]),
    3:  (26, [(1, 44)]),
    4:  (18, [(2, 32)]),
    5:  (24, [(2, 43)]),
    6:  (16, [(4, 27)]),
    7:  (18, [(4, 31)]),
    8:  (22, [(2, 38), (2, 39)]),
    9:  (22, [(3, 36), (2, 37)]),
    10: (26, [(4, 43), (1, 44)]),
}

# Waar de uitlijningspatronen staan (kruispunten van deze coördinaten, behalve
# waar ze een zoeker zouden raken). Uit dezelfde norm.
_ALIGN = {
    1: [], 2: [6, 18], 3: [6, 22], 4: [6, 26], 5: [6, 30], 6: [6, 34],
    7: [6, 22, 38], 8: [6, 24, 42], 9: [6, 26, 46], 10: [6, 28, 50],
}

# Niveau M in de format-bits.
_EC_M = 0b00


def _data_codewoorden(versie: int) -> int:
    ec_per_blok, groepen = _VERSIES[versie]
    return sum(aantal * bytes_per for aantal, bytes_per in groepen)


def _encode_data(tekst: bytes, versie: int) -> list[int]:
    """De databytes (vóór foutcorrectie) voor deze tekst in byte-modus."""
    cap = _data_codewoorden(versie) * 8
    tel_bits = 8 if versie <= 9 else 16

    bits: list[int] = []

    def zet(waarde: int, n: int) -> None:
        for i in range(n - 1, -1, -1):
            bits.append((waarde >> i) & 1)

    zet(0b0100, 4)             # modusindicator: byte
    zet(len(tekst), tel_bits)  # tekental
    for byte in tekst:
        zet(byte, 8)
    # Afsluiter, hooguit vier nullen en niet verder dan de capaciteit.
    for _ in range(min(4, cap - len(bits))):
        bits.append(0)
    # Aanvullen tot een heel byte.
    while len(bits) % 8 != 0:
        bits.append(0)

    codewoorden = [int("".join(str(b) for b in bits[i:i + 8]), 2)
                   for i in range(0, len(bits), 8)]
    # Opvullen met de twee vaste pad-bytes, om en om, tot de capaciteit.
    pad = [0xEC, 0x11]
    i = 0
    while len(codewoorden) < cap // 8:
        codewoorden.append(pad[i % 2])
        i += 1
    return codewoorden


def _kies_versie(tekst: bytes) -> int:
    for versie in range(1, 11):
        cap = _data_codewoorden(versie) * 8
        tel_bits = 8 if versie <= 9 else 16
        nodig = 4 + tel_bits + 8 * len(tekst)
        if nodig <= cap:
            return versie
    raise ValueError("tekst te lang voor een QR tot versie 10")


def _interleave(codewoorden: list[int], versie: int) -> list[int]:
    """Data- en foutcorrectiecodewoorden verweven zoals de norm voorschrijft."""
    ec_per_blok, groepen = _VERSIES[versie]
    blokken_data: list[list[int]] = []
    blokken_ec: list[list[int]] = []
    idx = 0
    for aantal, bytes_per in groepen:
        for _ in range(aantal):
            blok = codewoorden[idx:idx + bytes_per]
            idx += bytes_per
            blokken_data.append(blok)
            blokken_ec.append(_rs_ec(blok, ec_per_blok))

    uit: list[int] = []
    max_data = max(len(b) for b in blokken_data)
    for i in range(max_data):
        for blok in blokken_data:
            if i < len(blok):
                uit.append(blok[i])
    for i in range(ec_per_blok):
        for blok in blokken_ec:
            uit.append(blok[i])
    return uit


# --- de matrix ----------------------------------------------------------------

def _grootte(versie: int) -> int:
    return 17 + versie * 4


def _lege_matrix(n: int):
    # None = nog niet gezet (en dus vrij voor data); True/False = een module.
    return [[None] * n for _ in range(n)]


def _zet_functiepatronen(m, versie: int):
    """De vaste patronen, plus een tweede matrix die zegt waar data NIET mag."""
    n = _grootte(versie)
    vast = [[False] * n for _ in range(n)]

    def zoeker(r, c):
        for dr in range(-1, 8):
            for dc in range(-1, 8):
                rr, cc = r + dr, c + dc
                if not (0 <= rr < n and 0 <= cc < n):
                    continue
                rand = dr in (0, 6) and 0 <= dc <= 6
                rand = rand or (dc in (0, 6) and 0 <= dr <= 6)
                kern = 2 <= dr <= 4 and 2 <= dc <= 4
                m[rr][cc] = rand or kern
                vast[rr][cc] = True

    zoeker(0, 0)
    zoeker(0, n - 7)
    zoeker(n - 7, 0)

    # Timing: om en om, op rij 6 en kolom 6.
    for i in range(8, n - 8):
        waarde = (i % 2 == 0)
        if m[6][i] is None:
            m[6][i] = waarde
            vast[6][i] = True
        if m[i][6] is None:
            m[i][6] = waarde
            vast[i][6] = True

    # Uitlijningspatronen op de kruispunten die geen zoeker raken.
    posities = _ALIGN[versie]
    for r in posities:
        for c in posities:
            if vast[r][c]:
                continue
            for dr in range(-2, 3):
                for dc in range(-2, 3):
                    rr, cc = r + dr, c + dc
                    rand = max(abs(dr), abs(dc))
                    m[rr][cc] = rand != 1
                    vast[rr][cc] = True

    # De vaste donkere module.
    m[n - 8][8] = True
    vast[n - 8][8] = True

    # De plekken die de format-bits innemen, reserveren (ze worden later gezet).
    for i in range(9):
        for (r, c) in ((8, i), (i, 8)):
            if 0 <= r < n and 0 <= c < n:
                vast[r][c] = True
    for i in range(8):
        vast[8][n - 1 - i] = True
        vast[n - 1 - i][8] = True

    # Versie-informatie (v >= 7): twee blokken van 6x3.
    if versie >= 7:
        for i in range(18):
            r, c = i // 3, i % 3
            vast[r][n - 11 + c] = True
            vast[n - 11 + c][r] = True

    return vast


def _plaats_data(m, vast, bits: list[int], versie: int):
    n = _grootte(versie)
    it = iter(bits)
    kolom = n - 1
    omhoog = True
    while kolom > 0:
        if kolom == 6:      # de timingkolom overslaan
            kolom -= 1
        rijen = range(n - 1, -1, -1) if omhoog else range(n)
        for r in rijen:
            for dc in (0, 1):
                c = kolom - dc
                if vast[r][c]:
                    continue
                try:
                    bit = next(it)
                except StopIteration:
                    bit = 0
                m[r][c] = bool(bit)
        kolom -= 2
        omhoog = not omhoog


_MASKS = [
    lambda r, c: (r + c) % 2 == 0,
    lambda r, c: r % 2 == 0,
    lambda r, c: c % 3 == 0,
    lambda r, c: (r + c) % 3 == 0,
    lambda r, c: (r // 2 + c // 3) % 2 == 0,
    lambda r, c: (r * c) % 2 + (r * c) % 3 == 0,
    lambda r, c: ((r * c) % 2 + (r * c) % 3) % 2 == 0,
    lambda r, c: ((r + c) % 2 + (r * c) % 3) % 2 == 0,
]


def _format_bits(masker: int) -> list[int]:
    """De 15 format-bits voor niveau M en dit masker (BCH + het vaste masker)."""
    data = (_EC_M << 3) | masker
    rest = data << 10
    g = 0b10100110111
    for i in range(14, 9, -1):
        if rest & (1 << i):
            rest ^= g << (i - 10)
    volledig = ((data << 10) | rest) ^ 0b101010000010010
    return [(volledig >> i) & 1 for i in range(14, -1, -1)]


def _versie_bits(versie: int) -> list[int]:
    """De 18 versie-bits (BCH), alleen nodig vanaf versie 7."""
    rest = versie << 12
    g = 0b1111100100101
    for i in range(17, 11, -1):
        if rest & (1 << i):
            rest ^= g << (i - 12)
    volledig = (versie << 12) | rest
    return [(volledig >> i) & 1 for i in range(17, -1, -1)]


def _zet_format(m, versie: int, masker: int):
    n = _grootte(versie)
    bits = _format_bits(masker)
    # Rond de linkerbovenzoeker.
    for i in range(6):
        m[8][i] = bool(bits[i])
    m[8][7] = bool(bits[6])
    m[8][8] = bool(bits[7])
    m[7][8] = bool(bits[8])
    for i in range(9, 15):
        m[14 - i][8] = bool(bits[i])
    # De tweede kopie, langs de andere twee zoekers.
    for i in range(8):
        m[n - 1 - i][8] = bool(bits[i])
    for i in range(8, 15):
        m[8][n - 15 + i] = bool(bits[i])

    if versie >= 7:
        vbits = _versie_bits(versie)
        for i in range(18):
            r, c = i // 3, i % 3
            m[r][n - 11 + c] = bool(vbits[17 - i])
            m[n - 11 + c][r] = bool(vbits[17 - i])


def _straf(m, n: int) -> int:
    """De strafscore van een gemaskerde matrix; de laagste wint."""
    straf = 0
    # Regel 1: reeksen van 5+ gelijke modules in rij en kolom.
    for lijnen in (m, list(zip(*m))):
        for lijn in lijnen:
            run = 1
            for i in range(1, n):
                if lijn[i] == lijn[i - 1]:
                    run += 1
                else:
                    if run >= 5:
                        straf += 3 + (run - 5)
                    run = 1
            if run >= 5:
                straf += 3 + (run - 5)
    # Regel 2: 2x2-blokken van dezelfde kleur.
    for r in range(n - 1):
        for c in range(n - 1):
            if m[r][c] == m[r][c + 1] == m[r + 1][c] == m[r + 1][c + 1]:
                straf += 3
    # Regel 3: het zoekerachtige patroon 1:1:3:1:1 met vier witte modules ernaast.
    patroon1 = [True, False, True, True, True, False, True,
                False, False, False, False]
    patroon2 = list(reversed(patroon1))
    for lijnen in (m, list(zip(*m))):
        for lijn in lijnen:
            lijn = list(lijn)
            for i in range(n - 10):
                venster = lijn[i:i + 11]
                if venster == patroon1 or venster == patroon2:
                    straf += 40
    # Regel 4: afwijking van vijftig procent donker.
    donker = sum(1 for rij in m for x in rij if x)
    verhouding = donker * 100 // (n * n)
    straf += (abs(verhouding - 50) // 5) * 10
    return straf


def _matrix(tekst: bytes) -> list[list[bool]]:
    versie = _kies_versie(tekst)
    data = _encode_data(tekst, versie)
    stroom = _interleave(data, versie)
    bits = [(byte >> i) & 1 for byte in stroom for i in range(7, -1, -1)]

    n = _grootte(versie)
    basis = _lege_matrix(n)
    vast = _zet_functiepatronen(basis, versie)
    _plaats_data(basis, vast, bits, versie)

    beste = None
    beste_straf = None
    for masker in range(8):
        m = [rij[:] for rij in basis]
        for r in range(n):
            for c in range(n):
                if not vast[r][c] and _MASKS[masker](r, c):
                    m[r][c] = not m[r][c]
        _zet_format(m, versie, masker)
        s = _straf(m, n)
        if beste_straf is None or s < beste_straf:
            beste_straf, beste = s, m
    return [[bool(x) for x in rij] for rij in beste]


# --- naar SVG -----------------------------------------------------------------

def svg(tekst: str, *, rand: int = 4, titel: str = "") -> str:
    """Een QR-code voor ``tekst`` als ``<svg>``-string, klaar om in te sluiten.

    ``rand`` is de stille zone in modules (de norm vraagt vier). De code tekent
    één zwart pad op een witte achtergrond en schaalt via ``viewBox`` mee met de
    ruimte die de pagina hem geeft -- geen vaste pixelmaat, zodat hij scherp
    blijft op elk scherm. De kleuren staan hard op zwart-op-wit met opzet: een QR
    moet contrastvast zijn, ook in een donker thema, dus hij krijgt zijn eigen
    witte veld en erft de themakleuren niet.
    """
    ruw = str(tekst or "").encode("utf-8")
    m = _matrix(ruw)
    n = len(m)
    totaal = n + rand * 2

    delen = []
    for r in range(n):
        for c in range(n):
            if m[r][c]:
                delen.append(f"M{c + rand} {r + rand}h1v1h-1z")
    pad = "".join(delen)

    label = (f'<title>{_esc(titel)}</title>' if titel else "")
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {totaal} {totaal}" '
        f'width="100%" height="100%" shape-rendering="crispEdges" '
        f'role="img" aria-label="{_esc(titel or "QR-code")}">'
        f'{label}'
        f'<rect width="{totaal}" height="{totaal}" fill="#ffffff"/>'
        f'<path d="{pad}" fill="#000000"/>'
        f'</svg>'
    )


def _esc(tekst: str) -> str:
    return (str(tekst).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))
