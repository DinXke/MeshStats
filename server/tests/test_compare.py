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
