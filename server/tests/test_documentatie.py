"""Tests voor de invarianten van `docs/`, en met name voor de afbeeldingen.

Waarom documentatie een test verdient. `contributing.md` §10 legt drie regels
vast -- beide talen, dezelfde koppen, dezelfde bestandsnaam -- en die regels
breken zonder dat er ooit iets stukgaat. Een document dat in één taal blijft
staan blijft prima renderen. Een afbeelding die weggegooid of hernoemd wordt
laat op GitHub een kapot kadertje achter dat niemand ziet die de pagina niet
opent.

De afbeeldingen zijn de nieuwste en brosste toevoeging, en ze hebben een eigen
soort verval: ze verouderen stilletijd. Daar kan een test niets aan doen -- of
een schermafbeelding nog klopt met de UI is niet machinaal vast te stellen. Wat
hij wél kan is voorkomen dat het paar uit elkaar loopt en dat een verwijzing in
het niets wijst, zodat wie ze hermaakt merkt dat er eentje ontbreekt in plaats
van dat een lezer het maanden later merkt.

Bewust NIET getest: of de alt-tekst de afbeelding correct beschrijft. Dat is een
oordeel en geen controle, en een test die doet alsof zou de nalezer in slaap
sussen. Wel getest is dat er überhaupt een is en dat ze niet uit drie woorden
bestaat, want dat is precies wat er gebeurt als iemand haast heeft.
"""
import re
from pathlib import Path

import pytest

DOCS = Path(__file__).resolve().parent.parent.parent / "docs"

# Alt-tekst is hier het bijschrift en beschrijft wat er op het scherm staat, dus
# is ze een zin of meer. Deze grens is ruim onder wat de bestaande afbeeldingen
# hebben en ligt ver boven "schermafbeelding van de beheerpagina".
MIN_ALT = 60

IMAGE_RE = re.compile(r"!\[([^\]]*)\]\(([^)]+)\)")
HEADING_RE = re.compile(r"^(#{2,3}) ", re.M)


def engelse_docs() -> list[Path]:
    return sorted(DOCS.glob("*.md"))


def alle_docs() -> list[Path]:
    return sorted(DOCS.rglob("*.md"))


@pytest.mark.parametrize("doc", engelse_docs(), ids=lambda p: p.name)
def test_elk_document_heeft_een_nederlandse_helft(doc):
    assert (DOCS / "nl" / doc.name).exists(), (
        f"{doc.name} bestaat alleen in het Engels; zie contributing.md §10")


@pytest.mark.parametrize("doc", sorted(DOCS.joinpath("nl").glob("*.md")),
                         ids=lambda p: p.name)
def test_elk_nederlands_document_heeft_een_engelse_helft(doc):
    assert (DOCS / doc.name).exists(), (
        f"nl/{doc.name} bestaat alleen in het Nederlands; zie contributing.md §10")


@pytest.mark.parametrize("doc", alle_docs(), ids=lambda p: str(p.relative_to(DOCS)))
def test_de_taalwissel_staat_op_regel_drie(doc):
    """Kop, lege regel, taalwissel. De lezer moet hem op elke pagina op dezelfde
    plek vinden, anders zoekt hij hem niet meer."""
    regels = doc.read_text(encoding="utf-8").splitlines()
    verwacht = (f"*[English](../{doc.name})*" if doc.parent.name == "nl"
                else f"*[Nederlands](nl/{doc.name})*")
    assert regels[2].strip() == verwacht, (
        f"{doc.name}: regel 3 is {regels[2]!r} in plaats van {verwacht!r}")


@pytest.mark.parametrize("doc", engelse_docs(), ids=lambda p: p.name)
def test_beide_helften_hebben_evenveel_koppen(doc):
    """Geen vertaling van de koppen -- die verschillen per taal -- maar wel hun
    aantal. Een halve vertaling valt zo op terwijl ze nog te repareren is."""
    nl = DOCS / "nl" / doc.name
    en_koppen = HEADING_RE.findall(doc.read_text(encoding="utf-8"))
    nl_koppen = HEADING_RE.findall(nl.read_text(encoding="utf-8"))
    assert en_koppen == nl_koppen, (
        f"{doc.name}: {len(en_koppen)} koppen in het Engels tegenover "
        f"{len(nl_koppen)} in het Nederlands, of op een andere diepte")


@pytest.mark.parametrize("doc", alle_docs(), ids=lambda p: str(p.relative_to(DOCS)))
def test_elke_afbeelding_bestaat(doc):
    for alt, doel in IMAGE_RE.findall(doc.read_text(encoding="utf-8")):
        if doel.startswith(("http://", "https://")):
            continue
        assert (doc.parent / doel).resolve().exists(), (
            f"{doc.name} verwijst naar {doel}, en dat bestand staat er niet. "
            f"Hermaken: zie contributing.md §10")


@pytest.mark.parametrize("doc", alle_docs(), ids=lambda p: str(p.relative_to(DOCS)))
def test_elke_afbeelding_heeft_een_beschrijvende_alt_tekst(doc):
    for alt, doel in IMAGE_RE.findall(doc.read_text(encoding="utf-8")):
        assert len(alt.strip()) >= MIN_ALT, (
            f"{doc.name}: de alt-tekst bij {doel} is {len(alt.strip())} tekens. "
            f"Ze is het bijschrift en het enige wat een lezer zonder de "
            f"afbeelding heeft; beschrijf wat er op het scherm staat")


@pytest.mark.parametrize("doc", engelse_docs(), ids=lambda p: p.name)
def test_beide_helften_tonen_dezelfde_afbeeldingen(doc):
    """Dezelfde bestanden, eigen beschrijvingen.

    De beheerpagina's zijn alleen Nederlands, dus er is één reeks afbeeldingen
    voor beide talen. Wat per taal verschilt is de alt-tekst -- en als die in de
    ene taal blijft staan terwijl de andere bijgewerkt wordt, is dat hier te
    zien omdat de bestanden dan uit de pas lopen.
    """
    nl = DOCS / "nl" / doc.name
    en_doelen = [Path(d).name for _, d in IMAGE_RE.findall(doc.read_text(encoding="utf-8"))]
    nl_doelen = [Path(d).name for _, d in IMAGE_RE.findall(nl.read_text(encoding="utf-8"))]
    assert en_doelen == nl_doelen, (
        f"{doc.name} toont {en_doelen} en nl/{doc.name} toont {nl_doelen}")


def test_geen_ongebruikte_afbeeldingen():
    """Een afbeelding die nergens meer staat is een afbeelding die niemand meer
    hermaakt, en die dus stilletjes veroudert tot iemand hem per ongeluk weer
    gebruikt."""
    images = DOCS / "images"
    if not images.exists():
        pytest.skip("nog geen docs/images/")
    gebruikt = set()
    for doc in alle_docs():
        for _, doel in IMAGE_RE.findall(doc.read_text(encoding="utf-8")):
            gebruikt.add(Path(doel).name)
    aanwezig = {p.name for p in images.iterdir() if p.is_file()}
    assert aanwezig <= gebruikt, (
        f"staan in docs/images/ maar worden nergens getoond: "
        f"{sorted(aanwezig - gebruikt)}")
