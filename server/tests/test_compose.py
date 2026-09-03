"""docker-compose.yml moet dezelfde terugval hebben als de code.

Waarom dit bestand bestaat. De hernoeming van MeshStats naar MeshManager gaf de
code een terugval -- ``config.env()`` leest ``MM_X`` en anders ``MCS_X`` -- maar
Compose kreeg die niet. Compose is dan ook geen code: het substitueert. Een
draaiende installatie heeft een ``.env`` met ``MCS_MQTT_USER`` en
``MCS_MQTT_PASS``, en voor de ontbrekende ``MM_``-namen vulde Compose gewoon de
standaard in. De container startte met gebruiker ``meshmanager`` en een leeg
wachtwoord, de broker kende alleen het oude account, en er kwam geen enkel
pakket meer binnen -- terwijl de site 200 bleef antwoorden en op elke pagina
gezond oogde. Dat heeft in productie dertien minuten datastroom gekost.

Deze tests bewaken twee dingen die je met het blote oog over het hoofd ziet:
dat er geen variabele bijkomt zonder terugval, en dat de terugval ook echt de
waarde oplevert die je verwacht. Dat tweede wordt hier nagerekend in plaats van
aangenomen: er draait geen Docker in de testsuite, dus de interpolatie wordt
nagebootst volgens dezelfde regels die ``docker compose config`` toepast, en die
regels zijn met de echte Compose getoetst voordat ze hier zijn opgeschreven.

Weg te halen samen met de rest van de MCS_-terugval; zie server/app/config.py.
"""
import re
from pathlib import Path

import pytest

COMPOSE = Path(__file__).resolve().parent.parent.parent / "docker-compose.yml"

# Namen die niet het MM_/MCS_-patroon volgen maar wél hernoemd zijn.
ANDERE_NAMEN = {"MESHMANAGER_PORT": "MESHSTATS_PORT"}

# Namen die nooit hernoemd zijn en dus geen terugval nodig hebben.
#
# ``TZ`` krijgt met opzet GEEN ``MM_``-voorvoegsel: dat is de naam die de
# container-runtime en glibc zelf honoreren, en een eigen naam ervoor zetten zou
# precies het effect wegnemen waar hij voor bestaat. Hij regelt alleen de
# weergave van LOGREGELS; de opgeslagen tijdstempels blijven UTC omdat
# db.utcnow() expliciet timezone-aware is en er geen naïeve datetime in de code
# staat. Zie de toelichting bij de variabele in docker-compose.yml.
NOOIT_HERNOEMD = {"MQTT_PORT", "TZ"}

# ``${NAAM:-standaard}`` of ``${NAAM-standaard}``, met een standaard die zelf
# weer zo'n constructie mag zijn.
SUBSTITUTIE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(:?-)((?:[^{}]|\{[^{}]*\})*)\}")


def _tekst() -> str:
    return COMPOSE.read_text(encoding="utf-8")


def _oude_naam(naam: str):
    """De naam die deze variabele vóór de hernoeming had, of None."""
    if naam in ANDERE_NAMEN:
        return ANDERE_NAMEN[naam]
    if naam.startswith("MM_"):
        return "MCS_" + naam[3:]
    return None


def _substituties(tekst: str):
    """Alleen de buitenste substituties; de geneste zijn hun standaardwaarde."""
    uit = []
    for m in SUBSTITUTIE.finditer(tekst):
        # Een match die binnen een eerdere match viel, is de geneste helft.
        if uit and m.start() < uit[-1][3]:
            continue
        uit.append((m.group(1), m.group(2), m.group(3), m.end()))
    return [(naam, op, standaard) for naam, op, standaard, _ in uit]


# --- de structuur ------------------------------------------------------------

def test_elke_hernoemde_variabele_valt_terug_op_haar_oude_naam():
    # De regressietest van de storing zelf. Een nieuwe MM_-variabele zonder
    # terugval is geen schoonheidsfout: het is een installatie die na een
    # update in stilte met de verkeerde waarde draait.
    zonder = []
    for naam, _op, standaard in _substituties(_tekst()):
        oud = _oude_naam(naam)
        if oud is None:
            continue
        if oud not in standaard:
            zonder.append(naam)
    assert not zonder, (
        "geen terugval op de oude naam voor: " + ", ".join(zonder))


def test_er_staan_geen_onbekende_variabelen_in():
    # Vangt een variabele die noch hernoemd is noch bewust op de lijst staat --
    # meestal een typefout in een naam, en die is met het blote oog onzichtbaar
    # omdat Compose er stilzwijgend de standaard voor invult.
    onbekend = [naam for naam, _op, _st in _substituties(_tekst())
                if _oude_naam(naam) is None and naam not in NOOIT_HERNOEMD]
    assert not onbekend, "onbekende variabelen: " + ", ".join(onbekend)


def test_een_bewust_lege_waarde_blijft_leeg():
    # MM_TSDB_URL leeg betekent "hou alles in SQLite", en de topicpatronen leeg
    # betekent "gebruik enkel de voorvoegsels". Bij die drie hoort de operator
    # '-' en niet ':-', want ':-' stapt over een expres lege waarde heen en
    # zet de standaard terug -- precies het tegenovergestelde van wat er staat.
    ops = {naam: op for naam, op, _st in _substituties(_tekst())}
    for naam in ("MM_TSDB_URL", "MM_MQTT_TOPIC", "MM_MQTT_RX_TOPIC"):
        assert ops.get(naam) == "-", (
            f"{naam} gebruikt ':-' en negeert daarmee een bewust lege waarde")


# --- het gedrag --------------------------------------------------------------

def _interpoleer(waarde: str, omgeving: dict) -> str:
    """Boots de substitutie van Compose na, van binnen naar buiten.

    ``:-`` valt terug bij ontbrekend OF leeg, ``-`` alleen bij ontbrekend --
    dezelfde regels die ``docker compose config`` toepast.
    """
    binnenste = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(:?-)([^{}]*)\}")
    vorige = None
    while vorige != waarde:
        vorige = waarde

        def een(m):
            naam, op, standaard = m.group(1), m.group(2), m.group(3)
            if naam in omgeving and (op == "-" or omgeving[naam] != ""):
                return omgeving[naam]
            return standaard

        waarde = binnenste.sub(een, waarde)
    return waarde


def _regel(naam: str) -> str:
    """De ruwe substitutie-uitdrukking voor deze variabele uit het bestand."""
    for regel in _tekst().splitlines():
        if regel.strip().startswith(f"{naam}:"):
            return regel.split(":", 1)[1].strip().strip('"')
    raise AssertionError(f"{naam} staat niet in docker-compose.yml")


@pytest.mark.parametrize("omgeving,verwacht", [
    # Het scenario dat de storing veroorzaakte: een .env van vóór de hernoeming.
    ({"MCS_MQTT_USER": "meshstats"}, "meshstats"),
    # Alleen de nieuwe naam.
    ({"MM_MQTT_USER": "nieuw"}, "nieuw"),
    # Allebei: de nieuwe wint, zoals config.env() ook doet.
    ({"MM_MQTT_USER": "nieuw", "MCS_MQTT_USER": "oud"}, "nieuw"),
    # Verse installatie zonder .env.
    ({}, "meshmanager"),
])
def test_de_brokergebruiker_komt_er_in_alle_vier_de_gevallen_goed_uit(
        omgeving, verwacht):
    assert _interpoleer(_regel("MM_MQTT_USER"), omgeving) == verwacht


def test_het_brokerwachtwoord_uit_een_oude_env_bereikt_de_container():
    # Dit is de waarde die het verschil maakte tussen "verbonden" en
    # "Not authorized": een leeg wachtwoord tegen een broker die er een
    # verwacht, zonder dat de site er iets van liet zien.
    assert _interpoleer(_regel("MM_MQTT_PASS"),
                        {"MCS_MQTT_PASS": "geheim"}) == "geheim"


def test_een_oude_poortinstelling_blijft_gelden():
    poort = [r for r in _tekst().splitlines() if "MESHMANAGER_PORT" in r][0]
    assert "9090" in _interpoleer(poort, {"MESHSTATS_PORT": "9090"})


def test_een_lege_tsdb_url_uit_een_oude_env_blijft_leeg():
    # Leeg is hier een antwoord ("hou alles in SQLite") en geen stilte.
    assert _interpoleer(_regel("MM_TSDB_URL"), {"MCS_TSDB_URL": ""}) == ""
