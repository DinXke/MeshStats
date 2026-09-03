"""Wat een voorgenomen filterregel werkelijk afknipt, gemeten in plaats van geraden.

Waarom dit bestaat
------------------
De vraag was: "zorg dat ik geen filter kan zetten die de repeater onbereikbaar
maakt via de mesh". Het eerlijke antwoord op die vraag heeft twee helften, en de
tweede is de nuttige.

**De repeater zelf kan niet onbereikbaar worden.** Dat is een ontwerpgarantie van
onze eigen firmware, niet iets wat deze module hoeft af te dwingen: het filter
wordt alleen in ``MyMesh::allowPacketForward()`` gevraagd, dus pakketten die AAN
deze node gericht zijn -- een login, een CLI-commando, een statusverzoek -- komen
er nooit langs, en verkeer van of naar een client in de access list is altijd
vrijgesteld. Zie ``docs/packet-filter.md``. Een slot bouwen tegen iets wat niet
kan, zou suggereren dat het wel kan.

**Maar nodes ACHTER deze node kunnen wel onbereikbaar worden.** Een repeater die
1-byte padhashes niet meer doorstuurt, sluit niemand buiten van zichzelf, maar
knipt wel het verkeer af van iedereen die via dit knooppunt moet. Dat is precies
wat er in dit mesh gebeurde: een companion die 1-byte uitzond werd niet meer
doorgegeven, terwijl de repeater zelf perfect antwoordde. Die fout is duur en van
buiten niet te zien -- de node lijkt gezond.

Waarom meten en niet waarschuwen
--------------------------------
Een vaste waarschuwing ("let op, dit kan verkeer afknippen") wordt na de tweede
keer weggeklikt. Een gemeten getal niet: "dit stopt 69% van het flood-verkeer dat
deze node nu doorgeeft" is een ander gesprek dan "wees voorzichtig". De meting
komt uit ``packets.raw``, wat in dit project de grondwaarheid is, en wordt met de
steekproefgrootte erbij gerapporteerd zodat de lezer weet hoe hard het cijfer is.

Wat deze module NIET doet
-------------------------
Hij weigert niets op eigen gezag. Hij levert een gemeten oordeel; de beslissing
om een zwaardere bevestiging te vragen hoort bij de schrijfweg
(``routes_admin.write_filter``), op dezelfde plek waar de bestaande
risicoklassen al gewogen worden. Twee plaatsen die onafhankelijk "nee" kunnen
zeggen, is een deur met twee sloten waarvan niemand weet welke klemt.

En hij dekt alleen ``hash``. Dat is de enige regel waarvan het afgeknipte deel
mesh-breed te meten is: de padhashgrootte staat onversleuteld in elk frame. Voor
``hops``, ``rate`` en ``channel`` zou hetzelfde cijfer een gok zijn -- die hangen
af van hopafstand, tijdvensters en kanaalsleutels die we niet allemaal hebben.
Liever één regel met een hard cijfer dan vier met een aannemelijk cijfer.
"""

from __future__ import annotations

import re

from . import db

# Hoeveel recente flood-pakketten we wegen. Groot genoeg om een percentage te
# mogen noemen, klein genoeg om de schrijfweg niet op te houden: dit draait
# tussen een klik en een radiocommando.
SAMPLE = 2500

# Vanaf welk afgeknipt aandeel dit een beslissing wordt in plaats van een detail.
# Een kwart is geen natuurwet maar een keuze: onder die grens raakt de regel
# vooral wat hij hoort te raken, erboven raakt hij het mesh.
DREMPEL = 0.25

_HASH_CMD = re.compile(r"^\s*(?:filter\s+)?hash\s+([123])\s*$", re.I)


def _flood_hash_verdeling(sample: int = SAMPLE) -> dict:
    """Hoe het recente FLOOD-verkeer zich verdeelt over padhashgroottes.

    Alleen flood, want alleen flood wordt gefilterd -- direct-gerouteerde
    pakketten komen volgens de firmware nooit langs het filter, en die meerekenen
    zou het afgeknipte deel te klein voorstellen.

    We decoderen ``raw`` in plaats van een kolom te lezen: de padhashgrootte
    wordt bij het opslaan wel geparsed maar niet bewaard (zie de kolomlijst van
    ``packets``), en ``raw`` is hier de grondwaarheid.
    """
    from . import packets as pakket

    verdeling: dict[int, int] = {}
    onleesbaar = 0
    rijen = db.q(
        "SELECT raw FROM packets "
        "WHERE raw IS NOT NULL AND (route IS NULL OR route NOT LIKE '%direct%') "
        "ORDER BY ts DESC LIMIT ?", (int(sample),))
    for rij in rijen:
        ruw = rij["raw"]
        try:
            ontleed = pakket.decode(bytes.fromhex(ruw) if isinstance(ruw, str) else ruw)
            maat = ontleed.get("path_hash_size")
        except Exception:
            maat = None
        if isinstance(maat, int) and 1 <= maat <= 3:
            verdeling[maat] = verdeling.get(maat, 0) + 1
        else:
            onleesbaar += 1
    return {"verdeling": verdeling, "onleesbaar": onleesbaar,
            "gemeten": sum(verdeling.values())}


def check(cmd: str, rep=None, sample: int = SAMPLE) -> dict:
    """Wat deze filterregel zou afknippen. Leeg oordeel = niets te melden.

    Terug komt altijd dezelfde vorm, ook als er niets te zeggen is, zodat de
    aanroeper geen twee codepaden nodig heeft:

    ``van_toepassing``  of deze module iets over dit commando kan zeggen.
    ``zwaar``           of het afgeknipte deel boven de drempel ligt.
    ``aandeel``         het afgeknipte deel als breuk (0.0-1.0).
    ``gemeten``         hoeveel pakketten er onder dat cijfer liggen.
    ``tekst_nl/en``     één zin, met het getal erin, klaar voor het scherm.
    """
    leeg = {"van_toepassing": False, "zwaar": False, "aandeel": 0.0,
            "gemeten": 0, "min_bytes": 0, "tekst_nl": "", "tekst_en": ""}
    m = _HASH_CMD.match(str(cmd or ""))
    if not m:
        return leeg
    minimum = int(m.group(1))

    meting = _flood_hash_verdeling(sample)
    gemeten = meting["gemeten"]
    if not gemeten:
        # Geen meetbare basis. Dan zwijgen we in plaats van een nul te melden --
        # "0% wordt afgeknipt" zou hier een bewering zijn die we niet kunnen doen.
        return dict(leeg, van_toepassing=True, min_bytes=minimum)

    weg = sum(n for maat, n in meting["verdeling"].items() if maat < minimum)
    aandeel = weg / gemeten
    zwaar = aandeel >= DREMPEL
    pct = round(aandeel * 100, 1)
    nl = (
        f"Gemeten over de laatste {gemeten} flood-pakketten: met een minimum van "
        f"{minimum} byte stuurt deze node {pct}% daarvan niet meer door. "
        f"Nodes die alleen via dit knooppunt te bereiken zijn en een kleinere "
        f"padhash gebruiken, vallen daarmee weg. Trek eerst uw companion en de "
        f"betrokken nodes gelijk op {minimum} byte."
    )
    en = (
        f"Measured over the last {gemeten} flood packets: with a minimum of "
        f"{minimum} bytes this node stops forwarding {pct}% of them. Nodes "
        f"reachable only through this hop that use a smaller path hash drop out. "
        f"Align your companion and the nodes involved to {minimum} bytes first."
    )
    return {"van_toepassing": True, "zwaar": zwaar, "aandeel": aandeel,
            "gemeten": gemeten, "min_bytes": minimum,
            "tekst_nl": nl if weg else "", "tekst_en": en if weg else ""}
