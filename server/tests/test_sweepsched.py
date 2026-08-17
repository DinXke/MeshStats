"""De uitvraagplanner: wanneer er wél en vooral wanneer er níét gevraagd wordt.

Een schema mag geen achterdeur zijn om de zendtijdafspraken heen. De meeste
tests hieronder gaan daarom over de drie grenzen die stapelen -- interval per
node, één ronde tegelijk met een minimumafstand, en een bovengrens per etmaal --
en over het geval dat ze alle drie passeert.
"""
import time

import pytest

from app import sweepsched


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


@pytest.fixture(autouse=True)
def _aan(monkeypatch):
    monkeypatch.setattr(sweepsched, "ENABLED", True)
    monkeypatch.setattr(sweepsched, "MIN_GAP_MIN", 15)
    monkeypatch.setattr(sweepsched, "MAX_PER_DAY", 48)


def _node(db, prefix, naam, uren=None, bereikbaar=True):
    row = db.get_or_create_repeater(prefix, naam)
    db.execute("UPDATE repeaters SET sweep_hours=?, fw_meshmanager=?, source_prefix=? "
               "WHERE id=?",
               (uren, "2.1.0" if bereikbaar else None, prefix, row["id"]))
    return db.qone("SELECT * FROM repeaters WHERE id=?", (row["id"],))


@pytest.fixture
def verzonden(monkeypatch):
    """De weg naar de broker vervangen, zodat de test ziet wat er zou vertrekken."""
    from app import commanding, mqtt_ingest
    uit = []
    monkeypatch.setattr(mqtt_ingest, "can_publish", lambda: True)
    monkeypatch.setattr(mqtt_ingest, "publish_command",
                        lambda node, command, subject=None, epoch=None:
                        uit.append((node, command, subject)) or True)
    monkeypatch.setattr(commanding, "describe",
                        lambda rep, **kw: {"mqtt": True, "commands": ("settings", "status"),
                                           "via_monitor": False, "blocker": "",
                                           "node": rep["pubkey_prefix"],
                                           "subject": rep["pubkey_prefix"]})
    return uit


# --- het interval per node ----------------------------------------------------

def test_zonder_schema_gebeurt_er_niets(db, verzonden):
    """Standaard uit: wie een node toevoegt krijgt geen terugkerende kosten."""
    _node(db, "55d9a320a4e3", "DinX-Home", uren=None)
    uit = sweepsched.run_once()
    assert uit["gestart"] is None and uit["reden"] == "niemand aan de beurt"
    assert verzonden == []


def test_een_node_met_schema_is_meteen_aan_de_beurt(db, verzonden):
    """Niet een vol interval wachten na het instellen: dan zou wie het net
    aanzette een dag in het ongewisse zijn of het werkt."""
    _node(db, "55d9a320a4e3", "DinX-Home", uren=24)
    uit = sweepsched.run_once()
    assert uit["gestart"] == "55d9a320a4e3"
    assert verzonden == [("55d9a320a4e3", "settings", None)]


def test_binnen_het_interval_gebeurt_er_niets_meer(db, verzonden):
    rep = _node(db, "55d9a320a4e3", "DinX-Home", uren=24)
    nu = time.time()
    sweepsched.record(rep["pubkey_prefix"], nu - 3600, "gevraagd")
    assert sweepsched.run_once(nu)["gestart"] is None


def test_na_het_interval_weer_wel(db, verzonden):
    rep = _node(db, "55d9a320a4e3", "DinX-Home", uren=24)
    nu = time.time()
    sweepsched.record(rep["pubkey_prefix"], nu - 25 * 3600, "gevraagd")
    assert sweepsched.run_once(nu)["gestart"] == "55d9a320a4e3"


# --- één tegelijk, met afstand ------------------------------------------------

def test_tien_nodes_tegelijk_leveren_één_ronde_op(db, verzonden):
    """Niet tien timers die toevallig samenvallen: één wachtrij. Tien rondes op
    hetzelfde uur zou de band een uur bezet houden."""
    for i in range(10):
        _node(db, "55d9a32000%02d" % i, "node-%d" % i, uren=24)
    sweepsched.run_once()
    assert len(verzonden) == 1


def test_de_minimumafstand_houdt_de_volgende_tegen(db, verzonden):
    for i in range(3):
        _node(db, "55d9a32000%02d" % i, "node-%d" % i, uren=24)
    nu = time.time()
    assert sweepsched.run_once(nu)["gestart"] is not None
    # Meteen daarna nog een keer: de afstand is nog niet verstreken.
    uit = sweepsched.run_once(nu + 60)
    assert uit["gestart"] is None and uit["reden"] == "minimumafstand"
    # Na de afstand wel.
    assert sweepsched.run_once(nu + 16 * 60)["gestart"] is not None
    assert len(verzonden) == 2


def test_de_meest_achterstallige_wint(db, verzonden):
    nu = time.time()
    a = _node(db, "55d9a320a4e3", "vroeg", uren=24)
    b = _node(db, "e3d3f4d7edd0", "later", uren=24)
    sweepsched.record(a["pubkey_prefix"], nu - 100 * 3600, "gevraagd")
    sweepsched.record(b["pubkey_prefix"], nu - 30 * 3600, "gevraagd")
    # De afstand geldt over alle nodes samen, dus even ver terug leggen.
    sweepsched.run_once(nu)
    assert verzonden[0][0] == "55d9a320a4e3"


# --- het dagbudget ------------------------------------------------------------

def test_het_dagbudget_is_de_grens_die_de_andere_twee_niet_vangen(db, verzonden, monkeypatch):
    """Iemand die twintig nodes op dagelijks zet zonder de optelsom te maken."""
    monkeypatch.setattr(sweepsched, "MAX_PER_DAY", 2)
    nu = time.time()
    for i in range(5):
        _node(db, "55d9a32000%02d" % i, "node-%d" % i, uren=24)
    for stap in range(2):
        assert sweepsched.run_once(nu + stap * 16 * 60)["gestart"] is not None
    uit = sweepsched.run_once(nu + 3 * 16 * 60)
    assert uit["gestart"] is None and uit["reden"] == "dagbudget op"


def test_het_dagbudget_loopt_mee_met_de_klok(db, verzonden, monkeypatch):
    monkeypatch.setattr(sweepsched, "MAX_PER_DAY", 1)
    nu = time.time()
    _node(db, "55d9a320a4e3", "DinX-Home", uren=6)
    assert sweepsched.run_once(nu)["gestart"] is not None
    assert sweepsched.run_once(nu + 7 * 3600)["reden"] == "dagbudget op"
    # Een etmaal later telt de oude ronde niet meer mee.
    assert sweepsched.run_once(nu + 25 * 3600)["gestart"] is not None


# --- geen weg ------------------------------------------------------------------

def test_een_node_zonder_weg_blokkeert_de_wachtrij_niet(db, monkeypatch):
    """Anders blijft hij elke minuut de meest achterstallige en komt niemand
    anders meer aan de beurt."""
    from app import commanding, mqtt_ingest
    monkeypatch.setattr(mqtt_ingest, "can_publish", lambda: True)
    monkeypatch.setattr(mqtt_ingest, "publish_command",
                        lambda *a, **k: pytest.fail("mocht niets versturen"))
    monkeypatch.setattr(commanding, "describe",
                        lambda rep, **kw: {"mqtt": False, "commands": (),
                                           "via_monitor": False, "blocker": "no_fw",
                                           "node": None, "subject": None})
    rep = _node(db, "55d9a320a4e3", "DinX-Home", uren=24)
    uit = sweepsched.run_once()
    assert uit["reden"] == "geen weg"
    # ...en het staat opgeschreven, dus hij is niet meteen weer aan de beurt.
    assert "geen weg" in sweepsched.entry(rep["pubkey_prefix"])["result"]


def test_uitgeschakelde_planner_doet_niets(db, verzonden, monkeypatch):
    monkeypatch.setattr(sweepsched, "ENABLED", False)
    _node(db, "55d9a320a4e3", "DinX-Home", uren=24)
    assert sweepsched.run_once()["reden"] == "uitgeschakeld"
    assert verzonden == []


# --- narekenbaar --------------------------------------------------------------

def test_het_grootboek_overleeft_een_herstart(db):
    """In de databank en niet in het geheugen: een schema dat een herstart niet
    overleeft is geen schema maar een gewoonte van dit proces."""
    rep = _node(db, "55d9a320a4e3", "DinX-Home", uren=24)
    nu = time.time()
    sweepsched.record(rep["pubkey_prefix"], nu, "gevraagd")
    bewaard = db.get_setting(sweepsched._LEDGER_KEY, "")
    assert "55d9a320a4e3" in bewaard and "gevraagd" in bewaard


def test_wanneer_de_volgende_ronde_valt(db):
    rep = _node(db, "55d9a320a4e3", "DinX-Home", uren=24)
    nu = time.time()
    sweepsched.record(rep["pubkey_prefix"], nu - 3600, "gevraagd")
    resterend = sweepsched.next_due_secs(rep, nu)
    assert 22 * 3600 < resterend <= 23 * 3600


def test_zonder_schema_is_er_geen_volgende_ronde(db):
    rep = _node(db, "55d9a320a4e3", "DinX-Home", uren=None)
    assert sweepsched.next_due_secs(rep) is None
    assert sweepsched.due_at(rep) is None


def test_de_minimumafstand_kan_niet_onder_die_van_de_firmware(monkeypatch):
    """MON_SET_MIN_GAP_MS is 600 s; korter instellen heeft geen zin omdat de
    monitor het toch weigert, en zou de site iets laten beloven wat niet gebeurt."""
    from app import config
    monkeypatch.setattr(config, "env", lambda naam, standaard="": "1")
    import importlib
    herladen = importlib.reload(sweepsched)
    assert herladen.MIN_GAP_MIN >= 10
    importlib.reload(sweepsched)


# --- de route -----------------------------------------------------------------

def test_het_schema_zetten_vraagt_de_zwaardere_bevoegdheid(db, monkeypatch):
    """Een klasse zwaarder dan de knop die één ronde start: die kost één keer
    zendtijd, dit kost hem elke dag opnieuw op andermans band."""
    from app import rbac
    assert rbac.ACTIONS["node.schema"].klasse == rbac.KLASSE_MERKBAAR
    assert (rbac._rang(rbac.ACTIONS["node.schema"].klasse)
            > rbac._rang(rbac.ACTIONS["node.uitvragen"].klasse))


def test_het_schema_wordt_geklemd_in_plaats_van_geweigerd(db, monkeypatch):
    from app import routes_admin
    monkeypatch.setattr(routes_admin, "require_perm", lambda request, actie, rep=None: "u")
    monkeypatch.setattr(routes_admin, "check_csrf", lambda request, csrf: None)
    rep = _node(db, "55d9a320a4e3", "DinX-Home", uren=None)

    routes_admin.set_schedule(None, rep["id"], sweep_hours=99999, csrf="x")
    assert db.qone("SELECT sweep_hours FROM repeaters WHERE id=?",
                   (rep["id"],))["sweep_hours"] == 24 * 30

    routes_admin.set_schedule(None, rep["id"], sweep_hours=0, csrf="x")
    # 0 wordt NULL: 'uit' is één toestand en niet twee.
    assert db.qone("SELECT sweep_hours FROM repeaters WHERE id=?",
                   (rep["id"],))["sweep_hours"] is None


# --- 'gevraagd' is geen uitkomst ----------------------------------------------
#
# De reden dat twaalf uur stilte onopgemerkt bleef: publiceren lukt zodra de
# broker de bytes aanneemt, en het grootboek zei daarop 'gevraagd'. Een geslaagde
# en een verdwenen ronde zagen er identiek uit.

def _waarde(db, rep, param, updated):
    db.execute("INSERT INTO repeater_cli(repeater_id, param, value, updated) "
               "VALUES(?,?,?,?) ON CONFLICT(repeater_id, param) DO UPDATE SET "
               "value=excluded.value, updated=excluded.updated",
               (rep["id"], param, "64", updated))


def test_een_ronde_die_niets_opleverde_heet_geen_antwoord(db, verzonden):
    rep = _node(db, "e3d3f4d7edd0", "JessaZH", uren=12)
    _waarde(db, rep, "flood.max", "2026-08-16T11:33:24+00:00")
    nu = time.time()

    assert sweepsched.run_once(nu)["gestart"] == "e3d3f4d7edd0"
    assert sweepsched.entry("e3d3f4d7edd0")["result"] == sweepsched.RESULT_ASKED

    # Termijn verstreken, geen versere waarde: dat is stilte en hoort zo te heten.
    sweepsched.verify_pending(nu + sweepsched.VERIFY_AFTER_S + 1)
    assert sweepsched.entry("e3d3f4d7edd0")["result"] == sweepsched.RESULT_SILENT


def test_een_ronde_met_versere_waarden_heet_antwoord_binnen(db, verzonden):
    rep = _node(db, "e3d3f4d7edd0", "JessaZH", uren=12)
    _waarde(db, rep, "flood.max", "2026-08-16T11:33:24+00:00")
    nu = time.time()
    sweepsched.run_once(nu)

    # De monitor heeft geantwoord: de ingest schreef een versere tijdstempel.
    _waarde(db, rep, "flood.max", "2026-08-17T04:29:00+00:00")
    sweepsched.verify_pending(nu + sweepsched.VERIFY_AFTER_S + 1)
    assert sweepsched.entry("e3d3f4d7edd0")["result"] == sweepsched.RESULT_ANSWERED


def test_binnen_de_termijn_wordt_er_nog_niet_geoordeeld(db, verzonden):
    rep = _node(db, "e3d3f4d7edd0", "JessaZH", uren=12)
    _waarde(db, rep, "flood.max", "2026-08-16T11:33:24+00:00")
    nu = time.time()
    sweepsched.run_once(nu)
    sweepsched.verify_pending(nu + 60)
    assert sweepsched.entry("e3d3f4d7edd0")["result"] == sweepsched.RESULT_ASKED


def test_een_node_zonder_enige_waarde_die_zwijgt(db, verzonden):
    """Nooit iets van gehoord en nu ook niet: dat is stilte en geen succes. Zonder
    de nulmeting zou 'er staat niets' niet te onderscheiden zijn van 'er staat
    iets nieuws'."""
    _node(db, "e3d3f4d7edd0", "JessaZH", uren=12)
    nu = time.time()
    sweepsched.run_once(nu)
    sweepsched.verify_pending(nu + sweepsched.VERIFY_AFTER_S + 1)
    assert sweepsched.entry("e3d3f4d7edd0")["result"] == sweepsched.RESULT_SILENT


def test_de_beoordeling_gebeurt_voor_de_volgende_ronde(db, verzonden):
    """Andersom zou een node die nooit antwoordt elke ronde opnieuw als
    'gevraagd' in het grootboek belanden en zou de stilte nooit opvallen."""
    rep = _node(db, "e3d3f4d7edd0", "JessaZH", uren=1)
    _waarde(db, rep, "flood.max", "2026-08-16T11:33:24+00:00")
    nu = time.time()
    sweepsched.run_once(nu)
    # Een uur later is hij weer aan de beurt; de vorige ronde moet dan al
    # beoordeeld zijn.
    sweepsched.run_once(nu + 3700)
    regel = sweepsched.entry("e3d3f4d7edd0")
    assert regel["result"] == sweepsched.RESULT_ASKED   # de nieuwe ronde
    assert regel["at"] > nu                            # ...en het is de nieuwe


# --- een ontbrekend beheeradres is zichtbaar vóór de knopklik -----------------

def test_nodes_zonder_beheeradres_staan_bovenaan_de_nodelijst(db, monkeypatch):
    """Vandaag twee keer een hele weg dichtgezet door een leeg configuratieveld
    zonder dat er iets aan te zien was. Een ontbrekende voorwaarde hoort te staan
    waar je hem ziet voordat je hem nodig hebt."""
    from app import commanding, mqtt_ingest, routes_admin
    from app.templating import templates

    monkeypatch.setattr(routes_admin, "require_login", lambda request: "beheerder")
    monkeypatch.setattr(mqtt_ingest, "can_publish", lambda: True)
    zonder = _node(db, "55d9a320a4e3", "DinX-Home", uren=6)
    met = _node(db, "aabbccddeeff", "Met-Adres", uren=6)
    db.execute("UPDATE repeaters SET ota_host='http://x' WHERE id=?", (met["id"],))
    reps = db.q("SELECT * FROM repeaters ORDER BY sort_order, name")
    routes = {r["id"]: {"level": commanding.LEVEL_FULL} for r in reps}

    ontbreekt = [r for r in reps
                 if not (r["ota_host"] or "").strip()
                 and routes[r["id"]]["level"] == commanding.LEVEL_FULL]
    assert [r["name"] for r in ontbreekt] == ["DinX-Home"]

    html = templates.env.get_template("admin/nodes.html").render({
        "site_name": "MeshManager", "user": "u", "world": "nodes",
        "repeaters": reps, "routes": routes, "groups": [], "csrf": "x",
        "hidden_repeaters": 0, "onzichtbaar": 0, "no_host_reps": ontbreekt,
    })
    assert "geen beheeradres" in html
    assert "DinX-Home" in html
    assert "Met-Adres" not in html


def test_een_doorgestuurde_node_zonder_adres_is_geen_verzuim(db):
    """Bij een node die alleen over LoRa te bereiken is, is een leeg beheeradres
    de normale toestand. Die erbij zetten zou de waarschuwing waardeloos maken."""
    from app import commanding
    _node(db, "e3d3f4d7edd0", "JessaZH", uren=12)
    reps = db.q("SELECT * FROM repeaters")
    routes = {r["id"]: {"level": commanding.LEVEL_SEMI} for r in reps}
    ontbreekt = [r for r in reps
                 if not (r["ota_host"] or "").strip()
                 and routes[r["id"]]["level"] == commanding.LEVEL_FULL]
    assert ontbreekt == []


@pytest.mark.parametrize("uitkomst,zin", [
    ("gevraagd, geen antwoord", "Het verzoek vertrok en er kwam niets terug"),
    ("gevraagd, antwoord binnen", "de ronde is gelopen"),
    ("versturen mislukt", "geen brokerverbinding"),
    ("gevraagd", "of de waarden verser geworden zijn"),
    ("geen weg (no_fw)", "geen weg naar deze node"),
])
def test_elke_uitkomst_krijgt_zijn_eigen_zin_op_de_pagina(uitkomst, zin):
    """'Gevraagd' was te optimistisch en een geslaagde ronde zag er identiek uit
    aan een verdwenen ronde. Nu niet meer, en de stilte staat er als probleem."""
    from tests.test_nodeconfig import _render
    html = _render(sweep_hours=12,
                   sweep_last={"result": uitkomst, "asked": "2026-08-17T04:27:42+00:00"})
    assert zin in html


def test_de_stilte_wordt_gemarkeerd_en_de_rest_niet():
    from tests.test_nodeconfig import _render
    stil = _render(sweep_hours=12, sweep_last={
        "result": "gevraagd, geen antwoord", "asked": "2026-08-17T04:27:42+00:00"})
    goed = _render(sweep_hours=12, sweep_last={
        "result": "gevraagd, antwoord binnen", "asked": "2026-08-17T04:27:42+00:00"})
    assert stil.count("cmp-off") > goed.count("cmp-off")
