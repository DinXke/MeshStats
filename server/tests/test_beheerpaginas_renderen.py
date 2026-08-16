"""De beheerpagina's echt renderen, met een echte sessie en een echte databank.

De rest van de suite roept routefuncties aan en kijkt naar wat ze teruggeven; bij
een sjabloon is dat te weinig. Bijna alles wat er op deze pagina's mis kan gaan
zit in de takken die zeggen *waarom* iets er niet is -- een uitgeschakelde knop,
een lege groep, een gebruiker zonder rechten -- en die branden pas bij het
renderen. Een tikfout erin levert geen testfout op maar een lege beheerpagina,
precies op het moment dat iemand hem nodig heeft. Dezelfde reden waarom
test_firmware.py de firmwarepagina door de echte Jinja-omgeving haalt.

Starlette rendert een TemplateResponse bij het aanmaken, dus het aanroepen van de
route is genoeg: een sjabloonfout komt hier naar buiten als een exception.
"""
import pytest
from starlette.requests import Request

from app import config


@pytest.fixture
def db(tmp_path, monkeypatch):
    from app import db as db_module
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "test.sqlite3")
    db_module._conn = None
    yield db_module
    if db_module._conn is not None:
        db_module._conn.close()
        db_module._conn = None


def verzoek(path: str, cookie: str, query: str = "") -> Request:
    return Request({
        "type": "http", "http_version": "1.1", "method": "GET",
        "scheme": "http", "server": ("test", 80), "path": path,
        "query_string": query.encode(),
        "headers": [(b"cookie", f"mm_session={cookie}".encode())],
    })


@pytest.fixture
def wereld(db, monkeypatch):
    """Een installatie met twee gebruikers, twee nodes, twee groepen en wat trail.

    Genoeg om elke tak van elke sjabloon te raken: een node met rechten en een
    zonder, een groep met leden en een lege, een toestemming en een weigering.
    """
    from app import auth, mqtt_ingest, rbac
    # Geen broker en geen GitHub in een test.
    monkeypatch.setattr(mqtt_ingest, "can_publish", lambda: False)
    from app import firmware
    monkeypatch.setattr(firmware, "releases",
                        lambda force=False: {"items": [], "error": "", "at": 0})

    baas = rbac.maak_gebruiker("admin", auth.hash_password("wachtwoord123"),
                               is_superuser=True)
    lid = rbac.maak_gebruiker("lid", auth.hash_password("wachtwoord123"))
    dak = db.get_or_create_repeater("e3d3f4d7edd0", "Dakrepeater")
    tuin = db.get_or_create_repeater("aabbccddeeff", "Tuinnode")
    ug = rbac.maak_groep("user", "Ploeg", "de mensen die de daken doen")
    rbac.maak_groep("user", "Leeg")
    ng = rbac.maak_groep("node", "Daken")
    rbac.zet_lidmaatschap("user", ug, lid, True)
    rbac.zet_lidmaatschap("node", ng, dak["id"], True)
    rbac.maak_toekenning("group", ug, "nodegroup", ng, "technicus", "allow", door="admin")
    rbac.maak_toekenning("user", lid, "node", tuin["id"], None, "deny", door="admin")

    from app import audit
    audit.log("admin", "node.firmware", rep=dak, detail="upgrade naar v1.12.0")
    audit.log("lid", "node.firmware", rep=dak, outcome=audit.GEWEIGERD,
              detail="u mag de firmware van deze node niet schrijven")
    return {"dak": dak, "tuin": tuin,
            "koek": {n: auth.make_session(n) for n in ("admin", "lid")}}


def tekst(resp) -> str:
    return resp.body.decode()


def test_serverpagina_toont_gebruikers_groepen_en_toekenningen(wereld):
    from app import routes_admin
    html = tekst(routes_admin.server_page(verzoek("/admin/server", wereld["koek"]["admin"])))
    assert "Gebruikers" in html and "Toekenningen" in html
    assert "Ploeg" in html and "Daken" in html
    # De lege groep hoort er ook te staan: hem weglaten zou een groep verstoppen
    # die iemand net aanmaakte.
    assert "Leeg" in html
    assert "weigeren" in html
    assert "Audittrail" in html and "upgrade naar v1.12.0" in html


def test_serverpagina_is_dicht_voor_een_gewone_gebruiker(wereld):
    from fastapi import HTTPException
    from app import routes_admin
    with pytest.raises(HTTPException) as fout:
        routes_admin.server_page(verzoek("/admin/server", wereld["koek"]["lid"]))
    assert fout.value.status_code == 403


def test_nodelijst_toont_alleen_de_eigen_nodes(wereld):
    from app import routes_admin
    html = tekst(routes_admin.nodes_page(verzoek("/admin", wereld["koek"]["lid"])))
    assert "Dakrepeater" in html
    assert "Tuinnode" not in html


def test_nodepagina_schakelt_uit_wat_niet_mag_in_plaats_van_het_te_verbergen(wereld):
    """De lijn van deze site: een handeling die niet mag hoort er te staan, uit,
    met de reden erbij."""
    from app import routes_admin
    html = tekst(routes_admin.node_page(
        verzoek(f"/admin/repeaters/{wereld['dak']['id']}", wereld["koek"]["lid"]),
        wereld["dak"]["id"]))
    # De technicus ziet de firmwareknop, uitgeschakeld, met de reden.
    assert "firmware" in html.lower()
    assert "technicus" in html
    assert "kan de bereikbaarheid afsnijden" in html


def test_nodepagina_weigert_een_node_zonder_rechten(wereld):
    from fastapi import HTTPException
    from app import routes_admin
    with pytest.raises(HTTPException) as fout:
        routes_admin.node_page(
            verzoek(f"/admin/repeaters/{wereld['tuin']['id']}", wereld["koek"]["lid"]),
            wereld["tuin"]["id"])
    assert fout.value.status_code == 403


def test_accountpagina_werkt_voor_iedereen_die_kan_inloggen(wereld):
    from app import routes_admin
    html = tekst(routes_admin.account_page(verzoek("/admin/account", wereld["koek"]["lid"])))
    assert "Mijn account" in html
    assert "Dakrepeater" in html and "technicus" in html
    # Zonder rechten op de serverpagina staat die tab er niet.
    assert "/admin/server" not in html


def test_audittrail_pagina_toont_de_geweigerde_poging(wereld):
    from app import routes_admin
    html = tekst(routes_admin.audit_page(verzoek("/admin/audit", wereld["koek"]["admin"])))
    assert "geweigerd" in html
    assert "lid" in html


def test_firmwarepagina_toont_alleen_de_eigen_nodes(wereld):
    from app import routes_admin
    html = tekst(routes_admin.firmware_page(verzoek("/admin/firmware", wereld["koek"]["lid"])))
    assert "Dakrepeater" in html
    assert "Tuinnode" not in html
    # De technicus mag het beheeradres wel zetten en de firmware niet. Beide
    # knoppen staan er; alleen de tweede staat uit.
    assert "disabled" in html


def test_nodelijst_noemt_de_rol_en_telt_wat_er_niet_staat(wereld):
    from app import routes_admin
    html = tekst(routes_admin.nodes_page(verzoek("/admin", wereld["koek"]["lid"])))
    assert "uw rol: technicus" in html
    # Eén node valt weg, en dat wordt geteld en niet verzwegen -- maar zonder de
    # naam te noemen van wat je niet mag zien.
    assert "1 node(s) worden hier niet getoond" in html
    assert "Tuinnode" not in html
