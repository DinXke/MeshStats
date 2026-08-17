"""De pagina's met beheerknoppen echt renderen, met een echte sessie en databank.

Dat zijn de vier onder /admin plus de publieke repeaterpagina, want daar staat
sinds jaar en dag dezelfde opvraagknop voor wie ingelogd is -- en "ingelogd" is
sinds het rechtenmodel niet meer hetzelfde als "mag dit".


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
    # Een node die vanzelf ontstaat komt verborgen binnen; de publieke pagina
    # bestaat pas als hij goedgekeurd is, en die pagina is hier het onderwerp.
    db.execute("UPDATE repeaters SET is_public=1")
    dak = db.qone("SELECT * FROM repeaters WHERE id=?", (dak["id"],))
    tuin = db.qone("SELECT * FROM repeaters WHERE id=?", (tuin["id"],))
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


def test_publieke_pagina_zet_de_opvraagknop_uit_in_plaats_van_hem_weg_te_halen(wereld):
    """De knop staat er sinds jaar en dag voor iedereen die ingelogd is.

    Zonder deze controle eindigt een klik van iemand zonder rechten in een kale
    403 — en dat is precies wat een uitgeschakelde knop met een reden moet
    voorkomen.
    """
    from app import routes_public
    dak = wereld["dak"]
    html = tekst(routes_public.repeater_page(
        verzoek(f"/r/{dak['slug']}", wereld["koek"]["lid"]), dak["slug"]))
    # Op de node waar hij technicus is, werkt de knop gewoon (of hij staat uit om
    # een reden die over de weg gaat, niet over het recht).
    assert "Opvragen mag niet" not in html

    tuin = wereld["tuin"]
    html = tekst(routes_public.repeater_page(
        verzoek(f"/r/{tuin['slug']}", wereld["koek"]["lid"]), tuin["slug"]))
    assert "Opvragen mag niet" in html


def test_nodelijst_noemt_de_rol_en_telt_wat_er_niet_staat(wereld):
    from app import routes_admin
    html = tekst(routes_admin.nodes_page(verzoek("/admin", wereld["koek"]["lid"])))
    assert "uw rol: technicus" in html
    # Eén node valt weg, en dat wordt geteld en niet verzwegen -- maar zonder de
    # naam te noemen van wat je niet mag zien.
    assert "1 node(s) worden hier niet getoond" in html
    assert "Tuinnode" not in html


def test_de_filteruitsplitsing_lekt_geen_kanaal_op_de_publieke_pagina(wereld, db):
    """De tellingen per pakkettype zijn openbaar, de geblokkeerde kanalen niet.

    Deze test staat hier en niet bij de eenheidstests van pktfilter.breakdown(),
    omdat het lek dat ertoe doet in het SJABLOON zit: een tak die per ongeluk
    ``is_admin`` vergeet, levert geen fout op maar een publieke pagina met de
    kanaalsleutel van iemand anders erop. Zie docs/privacy.md.
    """
    from app import routes_public
    dak = wereld["dak"]
    db.upsert_filter_state(dak["id"], {
        "on": True, "passed": 900, "exempt": 87,
        "drop": {"hops": 5},
        "stats": {
            "xr": {"ADVERT": {"hops": 5}},
            "rate": {"GRP_TXT": {"seen": 41, "cap": 2, "peak": 20, "lim": 20}},
            "ex": {"TXT_MSG": 87},
            "chan": [{"label": "geheimkanaal", "hash": "a3", "hits": 41}],
        },
    }, dak["pubkey_prefix"])

    anoniem = tekst(routes_public.repeater_page(
        verzoek(f"/r/{dak['slug']}", ""), dak["slug"]))
    # Het blok staat er ook echt. Zonder deze twee bewijst de rest niets: een
    # sjabloon dat de hele sectie overslaat haalt net zo goed 'geen kanaal'.
    assert "Weggegooid per pakkettype" in anoniem
    assert "Druk op de snelheidslimiet" in anoniem
    assert "ADVERT" in anoniem
    # De hash mag wel: die staat onversleuteld in elk groepsbericht op de lucht.
    assert "#a3" in anoniem
    # Het label is de naam die onze beheerder aan het kanaal van een ander gaf.
    assert "geheimkanaal" not in anoniem
    # De ingestelde limiet is een REGEL, en regels staan achter de login.
    assert ">Limiet<" not in anoniem

    ingelogd = tekst(routes_public.repeater_page(
        verzoek(f"/r/{dak['slug']}", wereld["koek"]["admin"]), dak["slug"]))
    assert "geheimkanaal" in ingelogd
    assert "#a3" in ingelogd


def test_voorpagina_zet_de_noemer_bij_de_filtercijfers(wereld, db):
    """Een optelsom zonder 'over hoeveel nodes' is het cijfer van één node in de
    kleren van een groep.

    Deze test staat hier omdat het lek in het SJABLOON zit: de sommen kloppen wel
    in pktfilter.mesh_totals(), maar een kaart die alleen het grote getal toont
    is precies de oneerlijke vorm. Zie docs/privacy.md.
    """
    from app import routes_public
    dak = wereld["dak"]
    db.upsert_filter_state(dak["id"], {
        "on": True, "passed": 900, "exempt": 4, "drop": {"hops": 5, "rate": 2},
    }, dak["pubkey_prefix"])

    html = tekst(routes_public.index(verzoek("/", "")))
    assert "Pakketfilter in dit mesh" in html
    assert "geweerd" in html
    # De noemer: hoeveel nodes melden een filter, van hoeveel in totaal.
    assert "Filter aan bij" in html
    assert "repeaters op deze site" in html
    # De tweede node meldde niets, en dat is een eigen toestand.
    assert "Nooit iets over een filter gemeld" in html
    # En de periode wordt benoemd in plaats van weggelaten.
    assert "laatste herstart" in html


def test_voorpagina_zwijgt_als_geen_node_ooit_een_filter_meldde(wereld):
    """Een kader dat vooral zegt dat er niets te melden is, hoort er niet."""
    from app import routes_public
    html = tekst(routes_public.index(verzoek("/", "")))
    assert "Pakketfilter in dit mesh" not in html


def test_discoverypagina_rendert(wereld):
    """Deze pagina viel om op een ontbrekende import, en geen test merkte het.

    De rest van de suite roept `discovery`-functies rechtstreeks aan en die
    werken; wat er miste was de regel die de module in `routes_admin` haalt. Een
    NameError in een route is geen sjabloonfout en geen logicafout -- hij komt er
    alleen uit door de route werkelijk aan te roepen, en dat is precies wat dit
    bestand doet voor de andere beheerpagina's.
    """
    from app import routes_admin
    html = tekst(routes_admin.discovery_page(verzoek("/admin/discovery", wereld["koek"]["admin"])))
    assert html
