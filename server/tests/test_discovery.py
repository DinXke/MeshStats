"""Telemetrie ophalen zonder inloggegevens, en de drie stiltes eromheen.

Het interessante zit niet in het geslaagde geval maar in de vier uitkomsten. Van
een afstand heet alles wat niet antwoordt "geen antwoord", en dat is precies de
soort halve waarheid die deze functie zichtbaar moet maken in plaats van te
herhalen. De monitor weet genoeg om ze te scheiden: de loginuitkomst zegt of we
binnengelaten zijn, en de gehoorde lijst zegt of de node er überhaupt is.
"""
import time

import pytest

from app import db as db_module
from app import discovery, firmware, nodeconfig


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
def _schoon(monkeypatch):
    monkeypatch.setattr(firmware, "NODE_USER", "admin")
    nodeconfig._params.clear()


DOEL = "e3d3f4d7edd0"


def _afzender(db, host="http://x", versie="2.0.0"):
    rij = db.get_or_create_repeater("55d9a320a4e3", "DinX-Home")
    db.execute("UPDATE repeaters SET ota_host=?, fw_meshmanager=? WHERE id=?",
               (host, versie, rij["id"]))
    return db.qone("SELECT * FROM repeaters WHERE id=?", (rij["id"],))


def _monlijst(monkeypatch, entries=(), heard=()):
    monkeypatch.setattr(nodeconfig, "monitors",
                        lambda host: {"ok": True, "error": "",
                                      "entries": list(entries), "heard": list(heard)})


# --- wie stuurt het ------------------------------------------------------------

def test_zonder_weblogin_is_er_niets_om_het_mee_te_versturen(db, monkeypatch):
    monkeypatch.setattr(firmware, "NODE_USER", "")
    _afzender(db)
    assert discovery.sender()["blocker"] == "no_credentials"


def test_een_node_zonder_beheeradres_kan_geen_afzender_zijn(db):
    """Uitvragen loopt over /api/mon op de afzender; zonder adres is er geen weg
    naar zijn monitorlijst."""
    _afzender(db, host="")
    assert discovery.sender()["blocker"] == "no_sender"


def test_een_node_met_te_oude_firmware_kan_geen_afzender_zijn(db):
    """De grens is 1.4.0 en niet de grens van het cmd-topic: uitvragen loopt over
    /api/mon en heeft de tellers per verzoeksoort nodig, want daarop rusten de
    drie stiltes. Strenger zijn dan nodig sluit een node uit die het prima kan."""
    _afzender(db, versie="1.3.0")
    assert discovery.sender()["blocker"] == "no_sender"


def test_een_node_van_1_4_0_kan_het_wel(db):
    _afzender(db, versie="1.4.0")
    assert discovery.sender()["blocker"] == ""


def test_de_afzender_is_de_node_met_onze_firmware_en_een_adres(db):
    """Eén functie die zegt wie het stuurt, zodat het later een lijst kan worden
    zonder dat er iets boven verandert."""
    rij = _afzender(db)
    uit = discovery.sender()
    assert uit["rep"]["id"] == rij["id"]
    assert uit["blocker"] == ""


# --- wat het kost --------------------------------------------------------------

def test_de_kosten_rekenen_de_hele_monitorlijst_mee(db, monkeypatch):
    """De firmware kent geen poll van één node: MA_POLL zet _mon_next_round en de
    ronde loopt de lijst af. Dat verzwijgen zou de kostenopgave onwaar maken."""
    _monlijst(monkeypatch, entries=[{"k": "aaaa"}, {"k": "bbbb"}])
    uit = discovery.cost("http://x", extra=1)
    assert uit["monitored"] == 2
    assert uit["nodes"] == 3
    assert uit["requests"] == 3 * discovery.STEPS_PER_NODE
    assert uit["worst_secs"] > 0


def test_zonder_monitorlijst_geen_kostenopgave(db, monkeypatch):
    monkeypatch.setattr(nodeconfig, "monitors",
                        lambda host: {"ok": False, "error": "niet bereikbaar",
                                      "entries": [], "heard": []})
    assert discovery.cost("http://x")["ok"] is False


# --- de keuzelijst -------------------------------------------------------------

def test_de_gehoorde_lijst_komt_van_de_afzender(db, monkeypatch):
    """Niet uit onze eigen tabellen: wat wij ooit in het verkeer zagen zegt niets
    over wat déze node nu kan bereiken."""
    _monlijst(monkeypatch,
              entries=[{"k": DOEL.upper()}],
              heard=[{"k": DOEL.upper(), "n": "JessaZH", "snr": 7},
                     {"k": "AABBCCDDEEFF", "n": "Ver", "age": 900, "cached": 1}])
    uit = discovery.heard("http://x")
    assert uit["ok"] is True
    namen = {e["name"]: e for e in uit["entries"]}
    # De node die al gemonitord wordt is als zodanig gemarkeerd.
    assert namen["JessaZH"]["already"] is True
    assert namen["Ver"]["already"] is False
    # En een node uit een bewaarde advert draagt geen verzonnen SNR.
    assert namen["Ver"]["cached"] is True and namen["Ver"]["snr"] is None


# --- uitvragen -----------------------------------------------------------------

def test_uitvragen_voegt_toe_zonder_wachtwoord_en_polt(db, monkeypatch):
    """Zonder wachtwoord is de hele truc: de firmware logt dan in met een lege
    string, en die matcht op het gastwachtwoord dat standaard leeg is."""
    gezien = []
    monkeypatch.setattr(nodeconfig, "post_mon",
                        lambda host, velden: gezien.append(velden) or {"ok": True, "error": ""})
    monkeypatch.setattr(discovery.time, "sleep", lambda s: None)
    uit = discovery.probe("http://x", DOEL, "JessaZH")
    assert uit["ok"] is True
    assert gezien[0]["act"] == "add" and gezien[0]["key"] == DOEL
    assert "pass" not in gezien[0]
    assert gezien[1]["act"] == "poll"


def test_een_te_korte_sleutel_gaat_het_netwerk_niet_op(db, monkeypatch):
    monkeypatch.setattr(nodeconfig, "post_mon",
                        lambda host, velden: pytest.fail("mocht niets versturen"))
    assert discovery.probe("http://x", "ab")["step"] == "sleutel"


def test_een_mislukte_toevoeging_polt_niet(db, monkeypatch):
    beurten = []

    def nep(host, velden):
        beurten.append(velden["act"])
        return {"ok": False, "error": "node niet bereikbaar (URLError)"}

    monkeypatch.setattr(nodeconfig, "post_mon", nep)
    uit = discovery.probe("http://x", DOEL)
    assert uit["step"] == "toevoegen"
    assert beurten == ["add"]


# --- de vier uitkomsten --------------------------------------------------------

def _gevraagd(db, monkeypatch, nu):
    monkeypatch.setattr(nodeconfig, "post_mon",
                        lambda host, velden: {"ok": True, "error": ""})
    monkeypatch.setattr(discovery.time, "sleep", lambda s: None)
    monkeypatch.setattr(discovery.time, "time", lambda: nu)
    discovery.probe("http://x", DOEL)


def test_antwoord_binnen_als_er_verse_cijfers_zijn(db, monkeypatch):
    nu = time.time()
    _gevraagd(db, monkeypatch, nu)
    rij = db.get_or_create_repeater(DOEL, "JessaZH")
    db.execute("UPDATE repeaters SET last_seen=? WHERE id=?",
               ("2026-08-17T15:00:00+00:00", rij["id"]))
    _monlijst(monkeypatch, entries=[{"k": DOEL, "lr": 1}], heard=[{"k": DOEL}])

    discovery.verify("http://x", nu + discovery.VERIFY_AFTER_S + 1)
    assert discovery.job(DOEL)["result"] == "antwoord binnen"


def test_buiten_bereik_als_we_hem_niet_horen(db, monkeypatch):
    """Login onbeantwoord én geen adverts: dat is een radioprobleem en geen
    instelling."""
    nu = time.time()
    _gevraagd(db, monkeypatch, nu)
    _monlijst(monkeypatch, entries=[{"k": DOEL, "lr": 0}], heard=[])
    discovery.verify("http://x", nu + discovery.VERIFY_AFTER_S + 1)
    assert discovery.job(DOEL)["result"] == "buiten bereik"


def test_gastwachtwoord_als_we_hem_wel_horen(db, monkeypatch):
    """Hij is er en hij laat ons niet binnen. Dat we hem horen bewijst dat hij
    bestaat, niet waaróm hij zwijgt -- en zo staat het er ook."""
    nu = time.time()
    _gevraagd(db, monkeypatch, nu)
    _monlijst(monkeypatch, entries=[{"k": DOEL, "lr": 0}], heard=[{"k": DOEL}])
    discovery.verify("http://x", nu + discovery.VERIFY_AFTER_S + 1)
    assert discovery.job(DOEL)["result"] == "gastwachtwoord ingesteld"


def test_niet_ondersteund_als_de_login_lukte_maar_er_niets_kwam(db, monkeypatch):
    """Ongewoon: een gast hoort de status te krijgen zonder rechtencontrole."""
    nu = time.time()
    _gevraagd(db, monkeypatch, nu)
    _monlijst(monkeypatch, entries=[{"k": DOEL, "lr": 1}], heard=[{"k": DOEL}])
    discovery.verify("http://x", nu + discovery.VERIFY_AFTER_S + 1)
    assert discovery.job(DOEL)["result"] == "niet ondersteund"


def test_binnen_de_termijn_wordt_er_niet_geoordeeld(db, monkeypatch):
    nu = time.time()
    _gevraagd(db, monkeypatch, nu)
    _monlijst(monkeypatch, entries=[{"k": DOEL, "lr": 0}], heard=[])
    discovery.verify("http://x", nu + 10)
    assert discovery.job(DOEL)["result"] == "gevraagd"


def test_vergeten_haalt_hem_uit_de_monitorlijst(db, monkeypatch):
    gezien = []
    monkeypatch.setattr(nodeconfig, "post_mon",
                        lambda host, velden: gezien.append(velden) or {"ok": True, "error": ""})
    monkeypatch.setattr(discovery.time, "sleep", lambda s: None)
    discovery.probe("http://x", DOEL)
    discovery.forget("http://x", DOEL)
    assert gezien[-1] == {"act": "del", "key": DOEL}
    assert discovery.job(DOEL)["result"] == "vergeten"


# --- de pagina -----------------------------------------------------------------

def _render(**over):
    from app.templating import templates
    ctx = {
        "site_name": "MeshManager", "user": "u", "world": "nodes",
        "discovery_tab": True, "csrf": "x", "outcome": None,
        "sender": {"rep": {"id": 1, "name": "DinX-Home",
                           "pubkey_prefix": "55d9a320a4e3", "ota_host": "http://x"},
                   "blocker": "", "candidates": [{"id": 1}]},
        "sender_host": "http://x",
        "heard": {"ok": True, "error": "", "entries": [], "monitored": []},
        "cost": {"ok": True, "error": "", "monitored": 1, "nodes": 2,
                 "requests": 8, "worst_secs": 246},
        "poll_iv": {"ok": True, "error": "", "secs": 900},
        "jobs": {}, "results": {},
    }
    ctx.update(over)
    return templates.env.get_template("admin/discovery.html").render(ctx)


def test_de_kosten_staan_op_de_pagina_voor_de_klik():
    html = _render()
    assert "Wat het kost" in html
    assert "8 verzoeken" in html
    assert "hele monitorlijst af" in html


@pytest.mark.parametrize("uitkomst,zin", [
    ("buiten bereik", "wijst op bereik en niet op rechten"),
    ("gastwachtwoord ingesteld", "laat ons niet binnen"),
    ("niet ondersteund", "zonder rechtencontrole"),
])
def test_elke_stilte_krijgt_zijn_eigen_uitleg(uitkomst, zin):
    html = _render(jobs={DOEL: {"name": "JessaZH", "result": uitkomst,
                                "asked": "2026-08-17T15:00:00+00:00"}})
    assert zin in html


def test_het_vermoeden_wordt_als_vermoeden_gepresenteerd():
    """Dat we hem horen bewijst dat hij bestaat, niet waaróm hij zwijgt."""
    html = _render(jobs={DOEL: {"name": "JessaZH",
                                "result": "gastwachtwoord ingesteld",
                                "asked": "2026-08-17T15:00:00+00:00"}})
    assert "vermoeden en geen zekerheid" in html


def test_zonder_afzender_geen_formulier():
    html = _render(sender={"rep": None, "blocker": "no_sender", "candidates": []})
    assert "Niets om het mee te versturen" in html
    assert 'action="/admin/discovery/probe"' not in html


def test_de_uitkomst_toont_de_binnengekomen_metingen():
    metingen = {"battery": {"value": 4.02, "value_str": None, "ts": "2026-08-17T15:00:00+00:00"},
                "uptime": {"value": 12.5, "value_str": None, "ts": "2026-08-17T15:00:00+00:00"}}
    html = _render(
        jobs={DOEL: {"name": "JessaZH", "result": "antwoord binnen",
                     "asked": "2026-08-17T15:00:00+00:00"}},
        results={DOEL: {"rep": {"id": 2}, "metrics": metingen}})
    assert "4.02" in html
    assert "Externe sensoren blijven achter" in html


# --- herhaald uitvragen, en waar het ritme vandaan komt ------------------------

def test_het_interval_is_per_monitor_en_niet_per_node(db, monkeypatch):
    """Geen ontwerpkeuze van ons maar de vorm van de firmware: MA_POLL zet
    _mon_next_round en de ronde loopt de hele lijst af. Er bestaat geen ronde van
    één node, dus er bestaat geen interval van één node."""
    monkeypatch.setattr(nodeconfig, "monitors",
                        lambda host: {"ok": True, "error": "", "entries": [],
                                      "heard": [], "interval": 900})
    assert discovery.poll_interval("http://x")["secs"] == 900


def test_het_interval_wordt_geklemd_zoals_de_firmware_klemt(db, monkeypatch):
    gezien = []
    monkeypatch.setattr(nodeconfig, "post_mon",
                        lambda host, velden: gezien.append(velden) or {"ok": True, "error": ""})
    discovery.set_poll_interval("http://x", 10)
    discovery.set_poll_interval("http://x", 999999)
    assert [g["secs"] for g in gezien] == [60, 65535]
    assert all(g["act"] == "iv" for g in gezien)


def test_de_pagina_zegt_dat_de_node_het_zelf_doet():
    html = _render()
    assert "per monitor en niet per node" in html
    assert "Ondergrens 60 s" in html


# --- herkomst ------------------------------------------------------------------

def test_uitvragen_markeert_de_herkomst_en_houdt_hem_niet_publiek(db, monkeypatch):
    """Een cijfer dat wij bij iemand opgehaald hebben is niet hetzelfde als een
    cijfer dat een node zelf publiceert, en de rij moet dat zeggen. Niet-publiek,
    want het gaat per definitie om de node van iemand anders."""
    monkeypatch.setattr(nodeconfig, "post_mon",
                        lambda host, velden: {"ok": True, "error": ""})
    monkeypatch.setattr(discovery.time, "sleep", lambda s: None)
    discovery.probe("http://x", DOEL, "JessaZH")

    rij = db.qone("SELECT * FROM repeaters WHERE pubkey_prefix=?", (DOEL,))
    assert rij is not None
    assert rij["is_guest_polled"] == 1
    assert rij["is_public"] == 0


def test_vergeten_wist_de_herkomst_niet(db, monkeypatch):
    """Wat er verzameld is, is zo verzameld, en de grafiek moet dat over een maand
    nog kunnen zeggen. De vlag afzetten zou de geschiedenis herschrijven naar
    cijfers die de node zelf gepubliceerd zou hebben -- en dat heeft hij nooit."""
    monkeypatch.setattr(nodeconfig, "post_mon",
                        lambda host, velden: {"ok": True, "error": ""})
    monkeypatch.setattr(discovery.time, "sleep", lambda s: None)
    discovery.probe("http://x", DOEL)
    discovery.forget("http://x", DOEL)
    rij = db.qone("SELECT is_guest_polled FROM repeaters WHERE pubkey_prefix=?", (DOEL,))
    assert rij["is_guest_polled"] == 1


def test_de_pagina_zegt_dat_een_gat_niet_gepolst_betekent():
    metingen = {"battery": {"value": 4.0, "value_str": None, "ts": "2026-08-17T15:00:00+00:00"}}
    html = _render(
        jobs={DOEL: {"name": "JessaZH", "result": "antwoord binnen",
                     "asked": "2026-08-17T15:00:00+00:00"}},
        results={DOEL: {"rep": {"id": 2}, "metrics": metingen}})
    assert "niet gepolst" in html
    assert "niet publiek" in html


def test_boven_het_repeaterplafond_gaat_er_niets_de_lucht_in(db, monkeypatch):
    """De weigering moet vallen vóór de node aan de monitorlijst wordt toegevoegd.
    Andersom staat er een regel op de afzender die elke ronde zendtijd kost voor
    een node die hier nooit een rij krijgt -- een wees op andermans band."""
    monkeypatch.setattr(nodeconfig, "post_mon",
                        lambda host, velden: pytest.fail("mocht niets versturen"))
    monkeypatch.setattr(db_module, "mark_guest_polled",
                        lambda key, on=True: (_ for _ in ()).throw(
                            ValueError("al 500 repeaters bekend")))
    uit = discovery.probe("http://x", DOEL)
    assert uit["ok"] is False and uit["step"] == "opslag"
    assert "500" in uit["msg"]
