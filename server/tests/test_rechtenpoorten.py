"""Elk beheerformulier hoort ZICHTBAAR uit te staan voor wie het niet mag.

Twee dingen die uit elkaar gehouden moeten worden:

**De grendel** zit op de server (``require_perm`` in elke route). Die is dicht --
de eerste test hieronder bewijst dat er geen muterende beheerroute is zonder
rechtencontrole. Een bediener die een veld invult en op de knop drukt, krijgt
dus een weigering en geen wijziging.

**De poort** zit in het sjabloon (``{{ recht('...') }}`` → ``mag_attr``, dat
``disabled`` plus de reden in de tooltip zet). Die is op veel formulieren nog
niet aanwezig, en dat is wat de eigenaar zag: een bediener kreeg velden en
knoppen te zien die bruikbaar lijken, met de weigering pas ná de klik. Dat is
geen gat in de beveiliging maar wel een leugen op het scherm -- en in dit
project is dat een fout.

Vandaar de tweede test: een RATEL. Hij houdt het aantal ongepoorte formulieren
per bestand bij en faalt zodra er ergens één bijkomt. De lijst mag alleen
KRIMPEN. Wie een formulier afhandelt, haalt het getal naar beneden; wie er een
toevoegt zonder poort, loopt tegen deze test aan met de uitleg erbij.
"""
import glob
import os
import re

import pytest

TEMPLATES = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                         "app", "templates", "admin")
ROUTES = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                      "app", "routes_admin.py")

# Routes die met opzet geen rechtencontrole hebben, met de reden erbij. Elke
# nieuwe naam hier moet een reden hebben die standhoudt.
GEEN_RECHT_NODIG = {
    # Inloggen kan per definitie niet achter een recht zitten.
    "/login",
    # Je eigen wachtwoord. De route zoekt de rij van de INGELOGDE gebruiker op
    # en eist het huidige wachtwoord; een recht zou betekenen dat een beheerder
    # jouw wachtwoord moet zetten omdat je het zelf niet mag.
    "/password",
}


def _postroutes():
    """(pad, heeft-een-rechtencontrole) per POST-route in routes_admin."""
    src = open(ROUTES, encoding="utf-8").read()
    uit = []
    for blok in re.split(r"\n(?=@router\.)", src):
        m = re.match(r"@router\.post\(\s*[\"']([^\"']+)", blok)
        if not m:
            continue
        pad = m.group(1)
        # require_perm in elke vorm: met een letterlijke naam, of met een dict
        # die de naam uit de risicoklasse haalt (zoals /config en /compare/write).
        uit.append((pad, "require_perm(" in blok))
    return uit


def test_geen_enkele_muterende_beheerroute_zonder_rechtencontrole():
    """De grendel. Deze mag nooit rood staan: hier hangt niet de weergave aan
    maar of iemand het werkelijk kan."""
    zonder = [pad for pad, heeft in _postroutes()
              if not heeft and pad not in GEEN_RECHT_NODIG]
    assert not zonder, (
        "POST-routes zonder require_perm: " + ", ".join(sorted(zonder)))


def test_er_zijn_uberhaupt_routes_gevonden():
    """Als de vorige test groen staat omdat de regex niets meer vindt, bewijst
    hij niets. Deze bewaakt dat."""
    assert len(_postroutes()) > 40


# --- de ratel -----------------------------------------------------------------

def _forms_zonder_poort(pad):
    """Aantal POST-formulieren in dit sjabloon zonder rechtenpoort."""
    s = open(pad, encoding="utf-8").read()
    n = 0
    for stuk in s.split("<form")[1:]:
        vorm = stuk.split("</form>")[0]
        if 'method="post"' not in vorm:
            continue
        if ("recht(" in vorm or "mag_attr" in vorm or "serverrechten" in vorm
                or "disabled" in vorm):
            continue
        n += 1
    return n


# De stand op 2026-09-04, toen deze test geschreven werd. ALLEEN NAAR BENEDEN.
# Een formulier afgehandeld? Haal het getal omlaag in dezelfde commit. Loopt een
# getal op, dan staat er een nieuw formulier zonder poort op het scherm.
#
# login.html staat er niet in: daar is niets te poorten (zie GEEN_RECHT_NODIG).
#
# node.html staat op 0 sinds de herindeling (docs/nl/beheer-ux.md): de pagina is
# opgesplitst in admin/node/_*.html en elk formulier daarin draagt zijn poort.
# Die includes staan niet apart in deze tabel -- ze horen op nul te blijven, en
# de laatste test hieronder telt ze mee omdat hij recursief zoekt. Een include
# die buiten de telling valt zou precies de ontsnapping zijn die deze ratel
# moet voorkomen.
RATEL = {
    "account.html": 1,
    "companion.html": 37,
    "companions.html": 1,
    "compare.html": 2,
    "discovery.html": 4,
    "monitors.html": 2,
    "node.html": 0,
    "server.html": 0,
}


@pytest.mark.parametrize("naam", sorted(RATEL))
def test_de_ratel_loopt_niet_op(naam):
    pad = os.path.join(TEMPLATES, naam)
    nu = _forms_zonder_poort(pad)
    plafond = RATEL[naam]
    assert nu <= plafond, (
        "%s heeft %d formulier(en) zonder rechtenpoort, plafond is %d. Een "
        "formulier zonder poort toont velden en knoppen die bruikbaar lijken "
        "voor wie ze niet mag; de server weigert de klik pas daarna. Zet "
        "{{ recht('<handeling>') }} op de knop (en schakel de velden uit) of "
        "verlaag dit plafond niet." % (naam, nu, plafond))


def test_elk_sjabloon_in_de_ratel_bestaat_nog():
    """Een hernoemd sjabloon zou de ratel stil onbruikbaar maken."""
    for naam in RATEL:
        assert os.path.exists(os.path.join(TEMPLATES, naam)), naam


def test_de_ratel_kent_elk_sjabloon_met_formulieren():
    """Een nieuw sjabloon met ongepoorte formulieren moet hier opduiken en niet
    buiten de telling vallen."""
    ontbreekt = []
    # Recursief, zodat ook de includes onder admin/node/ meetellen: een
    # formulier in een include is net zo goed een formulier op het scherm.
    for pad in glob.glob(os.path.join(TEMPLATES, "**", "*.html"), recursive=True):
        naam = os.path.relpath(pad, TEMPLATES).replace(os.sep, "/")
        if naam in RATEL or naam == "login.html":
            continue
        if _forms_zonder_poort(pad):
            ontbreekt.append(naam)
    assert not ontbreekt, (
        "sjablonen met ongepoorte formulieren die niet in RATEL staan: "
        + ", ".join(sorted(ontbreekt)))
