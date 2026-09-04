"""Wat elke ROL op de nodepagina aan en uit ziet staan.

``test_rechtenpoorten.py`` telt of er een poort OP een formulier staat. Dat is
niet hetzelfde als de JUISTE poort: een pagina waar alles voor iedereen uitstaat
haalt die ratel ook. Deze tests gaan over de vraag waar de eigenaar over viel --
"ik zie hier een bediener waarbij bepaalde settings kunnen ingesteld worden in
plaats van greyed out" -- en leggen per rol vast wat er wél en niet mag werken.

De rollen zijn niets anders dan een plafond op de risicoklasse van een handeling
(``rbac.ROL_PLAFOND``), dus de eis is in twee delen te zeggen:

1. **Ordening.** Hoe ruimer de rol, hoe minder er uitstaat. Nooit andersom.
2. **Plafond.** Elk formulier dat voor een rol AANSTAAT, hoort bij een handeling
   die onder zijn plafond valt. Dat is de test die de oorspronkelijke klacht zou
   hebben gevangen.

Met een echte databank, echte toekenningen en de echte route -- niet met een
nagemaakte contextdict, want de poort hangt juist aan wat de route meegeeft.
"""
import re

import pytest
from starlette.requests import Request

from app import config, rbac, routes_admin

ROLLEN = ["lezer", "bediener", "technicus", "beheerder"]


@pytest.fixture
def db(tmp_path, monkeypatch):
    from app import db as db_module
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "test.sqlite3")
    db_module._conn = None
    yield db_module
    if db_module._conn is not None:
        db_module._conn.close()
        db_module._conn = None


def verzoek(path, cookie):
    return Request({
        "type": "http", "http_version": "1.1", "method": "GET",
        "scheme": "http", "server": ("test", 80), "path": path,
        "query_string": b"",
        "headers": [(b"cookie", f"mm_session={cookie}".encode())],
    })


@pytest.fixture
def wereld(db, monkeypatch):
    """Eén node, en vier gebruikers die er elk één rol op hebben."""
    from app import auth, firmware, mqtt_ingest, rbac as r
    # De MQTT-weg moet OPEN staan, anders staan de opvraag- en klokknoppen uit om
    # een andere reden dan de rol -- en dan test dit bestand niets over rollen.
    monkeypatch.setattr(mqtt_ingest, "can_publish", lambda: True)
    monkeypatch.setattr(firmware, "releases",
                        lambda force=False: {"items": [], "error": "", "at": 0})
    # Geen netwerk in een test: de nodepagina vraagt anders de node zelf uit.
    from app import nodeconfig, pktfilter, sensornode
    monkeypatch.setattr(pktfilter, "state",
                        lambda host, timeout=None: {"ok": False, "error": "", "filter": {}})
    monkeypatch.setattr(nodeconfig, "params",
                        lambda *a, **k: {"ok": False, "error": "", "params": []})
    monkeypatch.setattr(sensornode, "_json", lambda *a, **k: {"ok": False, "error": "", "data": {}})

    node = db.get_or_create_repeater("55d9a320a4e3", "Testnode")
    # Full managed: hij publiceert zichzelf (source_prefix == eigen sleutel), is
    # net gezien, en meldt een firmwareversie die de klok en de opdrachten kent.
    # Zonder die drie is elke knop uit en zegt dit bestand niets.
    db.execute("UPDATE repeaters SET ota_host=?, fw_meshmanager=?, source_prefix=?,"
               " source_seen=? WHERE id=?",
               ("http://node.invalid", "2.10.0", "55d9a320a4e3", db.utcnow(),
                node["id"]))
    node = db.qone("SELECT * FROM repeaters WHERE id=?", (node["id"],))

    koek = {}
    for rol in ROLLEN:
        uid = r.maak_gebruiker("u-" + rol, auth.hash_password("wachtwoord123"))
        r.maak_toekenning("user", uid, "node", node["id"], rol, "allow", door="test")
        koek[rol] = auth.make_session("u-" + rol)
    return {"node": node, "koek": koek}


def pagina(wereld, rol):
    resp = routes_admin.node_page(
        verzoek("/admin/repeaters/%d" % wereld["node"]["id"], wereld["koek"][rol]),
        wereld["node"]["id"])
    return resp.body.decode()


def formulieren(html):
    """[(actie, staat-er-iets-aan)] voor elk POST-formulier op de pagina."""
    uit = []
    for stuk in html.split("<form")[1:]:
        vorm = stuk.split("</form>")[0]
        if 'method="post"' not in vorm:
            continue
        m = re.search(r'action="([^"]*)"', vorm)
        actie = m.group(1) if m else ""
        knoppen = re.findall(r"<button[^>]*>", vorm)
        aan = any("disabled" not in b for b in knoppen)
        if re.search(r"<fieldset[^>]*disabled", vorm):
            aan = False
        uit.append((actie, aan))
    return uit


def hoeveel_uit(html):
    knoppen = re.findall(r"<button[^>]*>", html)
    fsets = re.findall(r"<fieldset[^>]*>", html)
    return (sum("disabled" in b for b in knoppen)
            + sum("disabled" in f for f in fsets))


# Welk recht er achter een actie zit. Moet gelijk blijven met de require_perm in
# routes_admin; de laatste test hieronder bewaakt dat elke actie op de pagina
# hier ook een vermelding heeft, zodat een nieuw formulier niet stil buiten deze
# controle valt.
RECHT_PER_STAART = {
    "refresh": "node.uitvragen",
    "settings/refresh": "node.uitvragen",
    "probe": "node.uitvragen",
    "alerts/ack": "node.uitvragen",
    "rename": "node.hernoemen",
    "channels": "node.hernoemen",
    "config": "node.instelling.gewoon",
    "filter": "node.filter.gewoon",
    "clocksync": "node.klok",
    "clockfix": "node.klokherstel",
    "sensor/clock": "node.klok",
    "toggle": "node.zichtbaarheid",
    "visibility": "node.zichtbaarheid",
    "ota": "node.beheeradres",
    "sensor": "node.beheeradres",
    "schedule": "node.schema",
    "monitors": "node.schema",
    "sensor/reboot": "node.herstart",
    "firmware": "node.firmware",
    "delete": "node.verwijderen",
}


def recht_voor(actie):
    staart = actie.rsplit("/admin/repeaters/", 1)[-1].split("/", 1)[-1].strip("/")
    kandidaten = [(len(k), r) for k, r in RECHT_PER_STAART.items() if staart.startswith(k)]
    return max(kandidaten)[1] if kandidaten else None


def test_een_lezer_kan_niets_indrukken(wereld):
    html = pagina(wereld, "lezer")
    aan = [a for a, staat_aan in formulieren(html) if staat_aan]
    assert aan == [], "een lezer mag kijken en verder niets, maar kan: %s" % aan
    # En de knoppen staan er nog wel: uitschakelen, niet verbergen.
    assert hoeveel_uit(html) > 0


def test_een_bediener_krijgt_geen_merkbare_handelingen(wereld):
    """De klacht van de eigenaar, als test. Een bediener mag uitvragen; de klok
    zetten, de zichtbaarheid wijzigen en het schema zijn een klasse zwaarder."""
    html = pagina(wereld, "bediener")
    aan = [a for a, staat_aan in formulieren(html) if staat_aan]
    for verboden in ("/clocksync", "/toggle", "/schedule", "/delete", "/sensor/reboot"):
        assert not any(verboden in a for a in aan), \
            "een bediener kan %s indrukken en dat is minstens merkbaar" % verboden
    # Wat hij wél moet kunnen, anders is de rol zinloos.
    assert any("refresh" in a for a in aan), "een bediener moet kunnen uitvragen"


def test_een_technicus_krijgt_geen_ingrijpende_handelingen(wereld):
    html = pagina(wereld, "technicus")
    aan = [a for a, staat_aan in formulieren(html) if staat_aan]
    assert not any("/delete" in a for a in aan), \
        "verwijderen is ingrijpend en hoort niet bij een technicus"
    assert any("/clocksync" in a for a in aan), "een technicus mag de klok zetten"


def test_een_beheerder_mag_alles_wat_aan_de_node_hangt(wereld):
    html = pagina(wereld, "beheerder")
    aan = [a for a, staat_aan in formulieren(html) if staat_aan]
    assert any("/delete" in a for a in aan), "een beheerder mag verwijderen"


def test_ruimere_rol_is_nooit_meer_uitgeschakeld(wereld):
    """De ordening. Deze test heeft geen absolute getallen nodig en verschuift
    dus niet als er een knop bij komt."""
    standen = [(rol, hoeveel_uit(pagina(wereld, rol))) for rol in ROLLEN]
    for (vorige_rol, vorige), (rol, nu) in zip(standen, standen[1:]):
        assert nu <= vorige, ("%s heeft meer uitgeschakeld (%d) dan %s (%d)"
                              % (rol, nu, vorige_rol, vorige))


@pytest.mark.parametrize("rol", ROLLEN)
def test_wat_aanstaat_valt_onder_het_plafond_van_de_rol(wereld, rol):
    """De eigenlijke eis, over elke rol en elk formulier op de pagina."""
    plafond = rbac.ROL_PLAFOND[rol]
    te_ruim = []
    for actie, staat_aan in formulieren(pagina(wereld, rol)):
        if not staat_aan:
            continue
        recht = recht_voor(actie)
        if recht is None:
            continue          # de laatste test hieronder vangt dit
        klasse = rbac.ACTIONS[recht].klasse
        if rbac._rang(klasse) > rbac._rang(plafond):
            te_ruim.append((actie, recht, klasse))
    assert not te_ruim, ("rol %s (plafond %s) kan te zware handelingen indrukken: %s"
                         % (rol, plafond, te_ruim))


def test_elk_formulier_op_de_pagina_valt_onder_deze_controle(wereld):
    """Een nieuw formulier zonder vermelding in RECHT_PER_STAART zou stil buiten
    de plafondtest vallen. Dan is deze test de plek waar dat opvalt."""
    onbekend = sorted({actie for actie, _ in formulieren(pagina(wereld, "beheerder"))
                       if recht_voor(actie) is None and "/admin/repeaters/" in actie})
    assert not onbekend, ("acties zonder recht in RECHT_PER_STAART: %s -- vul de "
                          "tabel aan met het recht dat de route eist" % onbekend)
