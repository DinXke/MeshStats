"""De vergelijkingstabel: wat er als afwijking geldt, en vooral wat niet.

De waarde van deze tabel zit in het markeren, en een markering die te vaak afgaat
is net zo onbruikbaar als een die nooit afgaat. Vandaar dat de meeste tests
hieronder gaan over gevallen waarin er juist NIETS gemarkeerd hoort te worden.
"""
import pytest

from app import compare


@pytest.fixture
def db(tmp_path, monkeypatch):
    from app import config
    from app import db as db_module
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "test.sqlite3")
    db_module._conn = None
    yield db_module
    if db_module._conn is not None:
        db_module._conn.close()
        db_module._conn = None


def _nodes(db, hoeveel):
    reps = []
    for i in range(hoeveel):
        row = db.get_or_create_repeater("%012x" % (0x550000000000 + i), "node-%d" % i)
        db.execute("UPDATE repeaters SET fw_meshmanager='2.1.0', source_prefix=? WHERE id=?",
                   (row["pubkey_prefix"], row["id"]))
        reps.append(db.qone("SELECT * FROM repeaters WHERE id=?", (row["id"],)))
    return reps


def _zet(db, rep, **params):
    db.upsert_cli_settings(rep["id"], params, prune=False)


# --- de norm ------------------------------------------------------------------

@pytest.mark.parametrize("waarden,norm", [
    (["64", "64", "64"], "64"),
    (["64", "64", "32"], "64"),
    (["64", "32", "16"], None),          # geen meerderheid: niemand wijkt af
    (["64", "64", "32", "32"], None),    # gelijkspel is geen meerderheid
    (["64", "32"], None),                # twee nodes: 'de meerderheid' is er één
    (["64"], None),
    ([], None),
])
def test_alleen_een_echte_meerderheid_telt_als_norm(waarden, norm):
    assert compare._verdeling(waarden) == norm


def test_stilte_telt_niet_mee_in_de_noemer():
    """Als de helft van de nodes nooit uitgevraagd is, hoort de andere helft nog
    steeds een meerderheid te kunnen vormen -- 'niet gelezen' is geen mening over
    de instelling."""
    assert compare._verdeling(["64", "64", "32", None, compare.MISSING, ""]) == "64"


# --- de tabel -----------------------------------------------------------------

def test_de_afwijker_wordt_gemarkeerd_en_de_rest_niet(db):
    reps = _nodes(db, 4)
    for r in reps[:3]:
        _zet(db, r, **{"flood.max.unscoped": "64"})
    _zet(db, reps[3], **{"flood.max.unscoped": "32"})

    t = compare.build(reps, ["flood.max.unscoped"])
    vlaggen = [rij["afwijkend"]["flood.max.unscoped"] for rij in t["rijen"]]
    assert vlaggen == [False, False, False, True]
    assert t["afwijkers"] == 1
    assert t["norm"]["flood.max.unscoped"] == "64"


def test_een_node_die_als_enige_geen_regio_heeft(db):
    """Björns voorbeeld, en het geval waar deze tabel voor gemaakt is."""
    reps = _nodes(db, 4)
    for r in reps[:3]:
        _zet(db, r, **{"region.default": "eu868"})
    _zet(db, reps[3], **{"region.default": None})     # gevraagd, geen antwoord

    t = compare.build(reps, ["region.default"])
    assert t["rijen"][3]["stil"]["region.default"] is True
    # ...en dat is géén afwijking: het zegt iets over ons, niet over de node.
    assert t["rijen"][3]["afwijkend"]["region.default"] is False


def test_nooit_gevraagd_verschilt_van_geen_antwoord(db):
    reps = _nodes(db, 3)
    _zet(db, reps[0], **{"flood.max": "64"})
    _zet(db, reps[1], **{"flood.max": None})
    # reps[2] krijgt niets: die parameter is voor hem nooit uitgevraagd.

    t = compare.build(reps, ["flood.max"])
    assert t["rijen"][0]["stil"]["flood.max"] is False
    assert t["rijen"][1]["stil"]["flood.max"] is True
    assert t["rijen"][2]["onbekend"]["flood.max"] is True
    assert t["rijen"][1]["onbekend"]["flood.max"] is False


def test_zonder_meerderheid_licht_er_niets_op(db):
    """Vier nodes met vier waarden zijn geen drie afwijkers en een norm."""
    reps = _nodes(db, 4)
    for i, r in enumerate(reps):
        _zet(db, r, **{"tx": str(10 + i)})
    t = compare.build(reps, ["tx"])
    assert t["afwijkers"] == 0
    assert t["norm"]["tx"] is None


def test_ingebouwde_kolommen_komen_uit_de_repeatertabel(db):
    reps = _nodes(db, 3)
    db.execute("UPDATE repeaters SET fw_meshmanager='2.0.0' WHERE id=?", (reps[2]["id"],))
    reps = [db.qone("SELECT * FROM repeaters WHERE id=?", (r["id"],)) for r in reps]

    t = compare.build(reps, ["fw_meshmanager"])
    assert [r["waarden"]["fw_meshmanager"] for r in t["rijen"]] == ["2.1.0", "2.1.0", "2.0.0"]
    assert t["rijen"][2]["afwijkend"]["fw_meshmanager"] is True


# --- kolomkeuze ---------------------------------------------------------------

def test_alleen_parameters_die_ergens_gelezen_zijn_zijn_kiesbaar(db):
    reps = _nodes(db, 2)
    _zet(db, reps[0], **{"flood.max": "64"})
    t = compare.build(reps, ["flood.max"])
    keys = [k for k, _ in t["keuzes"]]
    assert "flood.max" in keys
    assert "nooit.gelezen" not in keys
    for k in compare.BUILTIN_KEYS:
        assert k in keys


def test_onbekende_kolommen_vallen_weg_in_plaats_van_te_breken():
    """De keuze blijft in de instellingen staan; een parameter die tijdelijk niet
    uitgelezen is mag geen beheerpagina onderuithalen."""
    keuzes = ["fw_meshmanager", "flood.max"]
    assert compare.parse_columns("flood.max,verzonnen", keuzes) == ["flood.max"]


def test_een_lege_keuze_valt_terug_op_de_standaard():
    keuzes = compare.BUILTIN_KEYS + ["flood.max", "region.home"]
    uit = compare.parse_columns("", keuzes)
    assert uit and all(k in keuzes for k in uit)
    assert "fw_meshmanager" in uit


def test_de_volgorde_van_de_gekozen_kolommen_blijft_staan():
    keuzes = ["fw_meshmanager", "flood.max", "region.home"]
    assert compare.parse_columns("region.home,fw_meshmanager", keuzes) == \
        ["region.home", "fw_meshmanager"]


# --- de pagina ----------------------------------------------------------------

def test_pagina_rendert_met_een_afwijker(db):
    from app.templating import templates
    reps = _nodes(db, 3)
    for r in reps[:2]:
        _zet(db, r, **{"flood.max": "64"})
    _zet(db, reps[2], **{"flood.max": "8"})
    t = compare.build(reps, ["flood.max"])
    html = templates.env.get_template("admin/compare.html").render({
        "site_name": "MeshManager", "user": "u", "world": "nodes",
        "compare_tab": True, "tabel": t, "csrf": "x", "cfg_result": None,
    })
    assert "cmp-off" in html
    assert "vakje wijkt" in html
    assert "meerderheid: 64" in html


def test_pagina_zonder_repeaters_valt_niet_om(db):
    from app.templating import templates
    t = compare.build([], ["fw_meshmanager"])
    html = templates.env.get_template("admin/compare.html").render({
        "site_name": "MeshManager", "user": "u", "world": "nodes",
        "compare_tab": True, "tabel": t, "csrf": "x", "cfg_result": None,
    })
    assert "Nog geen repeaters" in html


# --- bewerken vanuit de tabel -------------------------------------------------

def _routes(monkeypatch, params=None):
    """De buitenwereld rond de vergelijkingsroutes weghalen."""
    from app import firmware, nodeconfig, routes_admin
    monkeypatch.setattr(routes_admin, "require_login", lambda request: "beheerder")
    monkeypatch.setattr(routes_admin, "check_csrf", lambda request, csrf: None)
    monkeypatch.setattr(firmware, "NODE_USER", "admin")
    lijst = {"ok": True, "error": "", "at": 0, "params": params if params is not None else [
        {"key": "flood.max", "kind": "int", "lo": 0, "hi": 64, "choices": "",
         "risk": 2, "reboot": 0, "secret": 0},
        {"key": "tx", "kind": "int", "lo": 0, "hi": 30, "choices": "",
         "risk": 3, "reboot": 0, "secret": 0},
    ]}
    monkeypatch.setattr(nodeconfig, "params", lambda host, force=False: lijst)
    return routes_admin


def _klaar(db, hoeveel=3):
    reps = _nodes(db, hoeveel)
    for r in reps:
        db.execute("UPDATE repeaters SET ota_host='http://x' WHERE id=?", (r["id"],))
        _zet(db, r, **{"flood.max": "64"})
    return [db.qone("SELECT * FROM repeaters WHERE id=?", (r["id"],)) for r in reps]


def test_de_bewerker_verschijnt_alleen_met_een_geldige_verwijzing(db, monkeypatch):
    """De ?edit= komt uit een URL die iemand geplakt of bewaard kan hebben. Een
    tabel die niet meer laadt omdat een node verwijderd is, is erger dan een
    tabel zonder bewerker."""
    ra = _routes(monkeypatch)
    reps = _klaar(db)
    t = compare.build(reps, ["flood.max"])
    assert ra._compare_editor("", t) is None
    assert ra._compare_editor("onzin", t) is None
    assert ra._compare_editor("999:flood.max", t) is None      # node bestaat niet
    assert ra._compare_editor("%d:" % reps[0]["id"], t) is None
    goed = ra._compare_editor("%d:flood.max" % reps[0]["id"], t)
    assert goed is not None and goed["key"] == "flood.max"
    assert goed["huidig"] == "64"


def test_schrijven_vanuit_de_tabel_loopt_door_dezelfde_functie(db, monkeypatch):
    """Een tweede schrijfpad naast nodeconfig.write() zou een tweede plek zijn
    waar de risicoklassen kunnen ontbreken."""
    from app import nodeconfig
    ra = _routes(monkeypatch)
    reps = _klaar(db)
    gezien = {}
    monkeypatch.setattr(nodeconfig, "write",
                        lambda rep, key, value, confirm="": gezien.update(
                            rep=rep["id"], key=key, value=value, confirm=confirm) or
                        {"ok": True, "step": "", "msg": "", "key": key, "asked": value,
                         "applied": value, "exact": True, "reboot": False})
    monkeypatch.setattr(ra, "_compare_page", lambda request, extra=None: extra)

    uit = ra.compare_write(None, rid=reps[0]["id"], key="flood.max", value="32",
                           confirm="ja", csrf="x")
    assert gezien == {"rep": reps[0]["id"], "key": "flood.max", "value": "32", "confirm": "ja"}
    assert uit["cfg_result"]["ok"] is True


def test_radiovelden_worden_samengevoegd_zoals_op_de_nodepagina(db, monkeypatch):
    from app import nodeconfig
    ra = _routes(monkeypatch)
    reps = _klaar(db)
    gezien = {}
    monkeypatch.setattr(nodeconfig, "write",
                        lambda rep, key, value, confirm="": gezien.update(value=value) or
                        {"ok": True, "step": "", "msg": "", "key": key, "asked": value,
                         "applied": value, "exact": True, "reboot": True})
    monkeypatch.setattr(ra, "_compare_page", lambda request, extra=None: extra)
    ra.compare_write(None, rid=reps[0]["id"], key="radio", value="",
                     confirm=reps[0]["name"], rf="869.525", rb="250", rs="11", rc="5",
                     csrf="x")
    assert gezien["value"] == "869.525 250 11 5"


def test_potlood_alleen_bij_kolommen_die_te_zetten_zijn(db, monkeypatch):
    """Bij de vaste kolommen valt niets te zetten -- die komen uit onze eigen
    tabel en niet uit de CLI van de node."""
    from app.templating import templates
    _routes(monkeypatch)
    reps = _klaar(db)
    t = compare.build(reps, ["fw_meshmanager", "flood.max"])
    html = templates.env.get_template("admin/compare.html").render({
        "site_name": "MeshManager", "user": "u", "world": "nodes", "compare_tab": True,
        "tabel": t, "csrf": "x", "cfg_result": None, "bewerken": None,
        "builtin_keys": compare.BUILTIN_KEYS,
    })
    assert html.count("cmp-edit") == len(reps)      # alleen de flood.max-kolom
    assert "edit=%d:flood.max" % reps[0]["id"] in html
    assert "edit=%d:fw_meshmanager" % reps[0]["id"] not in html


def test_de_zwaarste_klasse_vraagt_ook_hier_om_de_naam(db, monkeypatch):
    from app.templating import templates
    ra = _routes(monkeypatch)
    reps = _klaar(db)
    t = compare.build(reps, ["flood.max"])
    bewerken = ra._compare_editor("%d:tx" % reps[0]["id"], t)
    html = templates.env.get_template("admin/compare.html").render({
        "site_name": "MeshManager", "user": "u", "world": "nodes", "compare_tab": True,
        "tabel": t, "csrf": "x", "cfg_result": None, "bewerken": bewerken,
        "builtin_keys": compare.BUILTIN_KEYS,
    })
    assert 'placeholder="%s"' % reps[0]["name"] in html
    assert "kan de node onbereikbaar maken" in html


# --- de MeshCore-kolom --------------------------------------------------------

def test_meshcore_versie_van_een_node_die_zelf_publiceert(db):
    """Die stuurt hem mee in zijn statistiekbericht; er hoeft niets gevraagd."""
    reps = _nodes(db, 3)
    db.execute("UPDATE repeaters SET fw='v1.17.0'")
    reps = [db.qone("SELECT * FROM repeaters WHERE id=?", (r["id"],)) for r in reps]
    t = compare.build(reps, ["fw"])
    assert [r["waarden"]["fw"] for r in t["rijen"]] == ["v1.17.0"] * 3


def test_zonder_versie_hangt_het_vakje_af_van_of_er_gevraagd_is(db):
    """Het geval waar deze kolom voor bedoeld is. Een doorgestuurde repeater
    stuurt geen fw mee, dus die komt van 'cmd:ver' over LoRa -- en een node die
    nooit uitgevraagd is mag er niet hetzelfde uitzien als een die zweeg."""
    reps = _nodes(db, 3)
    db.execute("UPDATE repeaters SET fw=NULL")
    _zet(db, reps[0], **{"cmd:ver": "v1.16.0 (Build: x)"})   # geantwoord
    _zet(db, reps[1], **{"cmd:ver": None})                   # gevraagd, stil
    # reps[2] is nooit uitgevraagd
    reps = [db.qone("SELECT * FROM repeaters WHERE id=?", (r["id"],)) for r in reps]

    t = compare.build(reps, ["fw"])
    # reps[0] antwoordde wel, maar repeaters.fw wordt door de ingest gevuld en
    # niet door deze tabel; hier telt alleen dat de vraag gesteld is.
    assert t["rijen"][0]["stil"]["fw"] is True
    assert t["rijen"][1]["stil"]["fw"] is True
    assert t["rijen"][2]["onbekend"]["fw"] is True


def test_de_meshcore_kolom_kan_ook_een_afwijker_aanwijzen(db):
    """Waar het uiteindelijk om gaat: welke node draait iets anders."""
    reps = _nodes(db, 4)
    db.execute("UPDATE repeaters SET fw='v1.17.0'")
    db.execute("UPDATE repeaters SET fw='v1.15.0' WHERE id=?", (reps[3]["id"],))
    reps = [db.qone("SELECT * FROM repeaters WHERE id=?", (r["id"],)) for r in reps]
    t = compare.build(reps, ["fw"])
    assert [r["afwijkend"]["fw"] for r in t["rijen"]] == [False, False, False, True]


# --- het antwoord op 'ver' ----------------------------------------------------

@pytest.mark.parametrize("antwoord,fw,module", [
    # Standaard MeshCore: CommonCLI.cpp:271, "%s (Build: %s)".
    ("v1.17.0 (Build: 12 Jan 2026)", "v1.17.0", ""),
    ("1.16.0 (Build: x)", "1.16.0", ""),
    # Met onze module ervoor: mmnet_handle_command vangt 'ver' af.
    ("MeshManager (by DinX) v2.1.0 - MeshCore v1.17.0 (Build: 12 Jan 2026)",
     "v1.17.0", "2.1.0"),
    # De oude naam moet ook nog gelezen kunnen worden: die draait nog op daken.
    ("MeshStats (by DinX) v1.11.0 - MeshCore v1.16.0 (Build: 3 Aug 2025)",
     "v1.16.0", "1.11.0"),
    # Firmware die het commando niet kent, en stilte.
    ("Err - unknown command", "", ""),
    ("??", "", ""),
    ("unknown config: ver", "", ""),
    ("", "", ""),
])
def test_ver_wordt_uit_beide_antwoordvormen_gelezen(antwoord, fw, module):
    from app import mqtt_ingest
    assert mqtt_ingest.parse_ver(antwoord) == (fw, module)


def test_ver_uit_de_sweep_vult_de_firmwarekolom(db, monkeypatch):
    """Eén vraag, twee kolommen -- en voor een doorgestuurde repeater is dit de
    enige plek waar de MeshCore-versie ooit vandaan komt."""
    from app import mqtt_ingest
    baas = db.get_or_create_repeater("55d9a320a4e3", "DinX-Home")
    doel = db.get_or_create_repeater("e3d3f4d7edd0", "JessaZH")
    db.record_source(doel["id"], baas["pubkey_prefix"])
    doel = db.qone("SELECT * FROM repeaters WHERE id=?", (doel["id"],))

    mqtt_ingest._handle_settings(
        doel, baas["pubkey_prefix"],
        {"cmd:ver": "v1.16.0 (Build: 3 Aug 2025)"},
        prior_source=baas["pubkey_prefix"])

    na = db.qone("SELECT * FROM repeaters WHERE id=?", (doel["id"],))
    assert na["fw"] == "v1.16.0"


def test_een_onleesbaar_ver_antwoord_wist_de_kolom_niet(db):
    """record_firmware overschrijft alleen wat er genoemd is. Een node die 'ver'
    niet kent mag een versie die we al hadden niet weggooien."""
    from app import mqtt_ingest
    baas = db.get_or_create_repeater("55d9a320a4e3", "DinX-Home")
    db.record_firmware(baas["id"], fw="v1.17.0")
    baas = db.qone("SELECT * FROM repeaters WHERE id=?", (baas["id"],))

    mqtt_ingest._handle_settings(baas, baas["pubkey_prefix"],
                                 {"cmd:ver": "Err - unknown command"},
                                 prior_source=baas["pubkey_prefix"])
    na = db.qone("SELECT * FROM repeaters WHERE id=?", (baas["id"],))
    assert na["fw"] == "v1.17.0"
