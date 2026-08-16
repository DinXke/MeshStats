"""Firmware-upgrades: welk image mag naar welke node, en wat er gemeld wordt.

De rode draad is dat deze functie op één punt gevaarlijk is en overal elders
alleen maar vervelend: een image van de verkeerde bouwomgeving op een node die je
niet kunt aanraken. Vandaar dat de meeste tests hieronder gaan over wat er NIET
gebeurt -- geen knop zonder pad, geen image zonder envnaam, geen 'gelukt' zonder
dat de node het bevestigd heeft.
"""
import json

import pytest

from app import firmware


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


def rep(**overrides):
    row = {
        "id": 1, "name": "DinX-Home", "pubkey_prefix": "55d9a320a4e3",
        "fw": "v1.17.0", "fw_meshmanager": "1.11.0",
        "source_prefix": "55d9a320a4e3", "ota_host": "http://node.invalid",
        "pio_env": "heltec_v4_repeater_meshstats", "is_critical": 0,
    }
    row.update(overrides)
    return row


def release(tag="fw-v1.12.0", envs=("heltec_v4_repeater_meshstats",)):
    assets = []
    for env in envs:
        name = f"meshstats-{env}-{tag[len('fw-v'):]}.bin"
        assets.append({"name": name, "browser_download_url": f"https://x/{name}", "size": 1_289_053})
        assets.append({"name": name + ".sha256", "browser_download_url": f"https://x/{name}.sha256",
                       "size": 100})
    # Een release draagt vaak meer dan onze images; dat mag niets breken.
    assets.append({"name": "firmware.elf", "browser_download_url": "https://x/e", "size": 9})
    return {"tag_name": tag, "name": tag, "published_at": "2026-08-16T02:00:00Z",
            "body": "notities", "assets": assets}


# --- welke assets horen bij welke bouwomgeving --------------------------------

def test_assets_worden_per_bouwomgeving_uitgesorteerd():
    parsed = firmware._parse_release(release(envs=("env_a", "env_b")))
    assert parsed["version"] == "1.12.0"
    assert parsed["envs"] == ["env_a", "env_b"]
    # Elke image heeft zijn eigen checksum-URL; zonder die koppeling zou de
    # server een image versturen dat hij nergens tegen kon houden.
    for env in ("env_a", "env_b"):
        assert parsed["images"][env]["sha_url"].endswith(".sha256")


def test_assets_die_niet_aan_het_patroon_voldoen_verdwijnen_stil():
    raw = release()
    raw["assets"].append({"name": "meshstats-losseflodder.bin",
                          "browser_download_url": "https://x/l", "size": 1})
    parsed = firmware._parse_release(raw)
    assert list(parsed["images"]) == ["heltec_v4_repeater_meshstats"]


# --- mag er een image naar deze node ------------------------------------------

def test_zonder_inloggegevens_geen_enkele_knop(monkeypatch):
    monkeypatch.setattr(firmware, "NODE_USER", "")
    assert firmware.ota_route(rep())["blocker"] == "no_credentials"


def test_doorgestuurde_node_krijgt_een_blijvende_reden_en_geen_leeg_veld(monkeypatch):
    """Een node die via een monitor binnenkomt heeft geen IP-pad, en dat is niet
    hetzelfde als een vergeten instelling. De pagina moet die twee uit elkaar
    kunnen houden, anders staat er onder de dakrepeater een uitnodiging om een
    adres in te vullen dat niet bestaat."""
    monkeypatch.setattr(firmware, "NODE_USER", "admin")
    route = firmware.ota_route(rep(ota_host="", source_prefix="55d9a320a4e3",
                                   pubkey_prefix="e3d3f4d7edd0"))
    assert route["blocker"] == "relayed_only"
    assert route["can"] is False


def test_eigen_node_zonder_adres_krijgt_no_host(monkeypatch):
    monkeypatch.setattr(firmware, "NODE_USER", "admin")
    assert firmware.ota_route(rep(ota_host=""))["blocker"] == "no_host"


def test_node_zonder_meshstats_versie_krijgt_niets(monkeypatch):
    monkeypatch.setattr(firmware, "NODE_USER", "admin")
    assert firmware.ota_route(rep(fw_meshmanager=""))["blocker"] == "no_fw"


def test_volledig_ingerichte_node_mag(monkeypatch):
    monkeypatch.setattr(firmware, "NODE_USER", "admin")
    route = firmware.ota_route(rep())
    assert route["can"] is True and route["blocker"] == ""


# --- adressen -----------------------------------------------------------------

@pytest.mark.parametrize("host", ["file:///etc/passwd", "ftp://x", "", "http://"])
def test_onbruikbare_adressen_worden_geweigerd(host):
    with pytest.raises(ValueError):
        firmware._url(host, "/api/fw")


def test_adres_zonder_schema_wordt_http():
    assert firmware._url("10.0.0.5:8080", "/api/fw") == "http://10.0.0.5:8080/api/fw"


# --- omlaag gaan --------------------------------------------------------------

@pytest.mark.parametrize("installed,target,omlaag", [
    ("1.12.0", "1.11.0", True),
    ("1.11.0", "1.12.0", False),
    ("1.12.0", "1.12.0", False),
    ("", "1.12.0", False),          # onbekend is geen stap omlaag
    ("onzin", "1.12.0", False),
])
def test_downgrade_herkennen(installed, target, omlaag):
    assert firmware._is_downgrade(installed, target) is omlaag


# --- de download --------------------------------------------------------------

def test_download_weigert_als_de_checksum_niet_klopt(monkeypatch):
    """De reden dat de server downloadt en niet de node: hier is nog niets
    gebeurd. Een node die zelf zou halen ontdekt dit pas als hij al schrijft."""
    image = {"name": "x.bin", "url": "https://x/b", "sha_url": "https://x/s", "size": 500_000}

    def fake_get(url, timeout, accept="application/vnd.github+json"):
        return b"0" * 64 + b"  x.bin\n" if url.endswith("/s") else b"echte inhoud"

    monkeypatch.setattr(firmware, "_get", fake_get)
    with pytest.raises(ValueError, match="download klopt niet"):
        firmware.download(image)


def test_download_weigert_een_image_dat_geen_image_kan_zijn(monkeypatch):
    image = {"name": "x.bin", "url": "u", "sha_url": "s", "size": 900}
    with pytest.raises(ValueError):
        firmware.download(image)


# --- de opdracht --------------------------------------------------------------

def test_start_weigert_een_node_zonder_pad(db, monkeypatch):
    monkeypatch.setattr(firmware, "NODE_USER", "admin")
    monkeypatch.setattr(firmware, "release_by_tag", lambda tag: firmware._parse_release(release()))
    uit = firmware.start(rep(ota_host=""), "fw-v1.12.0")
    assert uit["ok"] is False and "geen image" in uit["error"]


def test_start_weigert_een_onbekende_release(db, monkeypatch):
    monkeypatch.setattr(firmware, "NODE_USER", "admin")
    monkeypatch.setattr(firmware, "release_by_tag", lambda tag: None)
    uit = firmware.start(rep(), "fw-v9.9.9")
    assert uit["ok"] is False and "onbekende release" in uit["error"]


def test_een_node_die_geen_bouwomgeving_meldt_krijgt_geen_image(db, monkeypatch):
    """Het gevaarlijkste geval van allemaal, dus met naam en toenaam in de
    toestand: liever een opdracht die faalt dan een image dat past bij niets."""
    monkeypatch.setattr(firmware, "release_by_tag", lambda tag: firmware._parse_release(release()))
    monkeypatch.setattr(firmware, "probe", lambda host, timeout=5: {
        "ok": True, "error": "", "ver": "1.11.0", "env": "", "board": "", "run": "app0", "other": {}})
    verstuurd = []
    monkeypatch.setattr(firmware, "push", lambda *a, **k: verstuurd.append(a) or {"ok": 1})

    firmware._save_job(1, {"state": "voorbereiden"})
    firmware._run_inner(1, "http://node.invalid", "fw-v1.12.0", "")
    job = firmware.job(1)
    assert job["state"] == "mislukt" and job["step"] == "env"
    assert verstuurd == []


def test_een_node_die_iets_anders_meldt_dan_de_pagina_dacht_krijgt_niets(db, monkeypatch):
    monkeypatch.setattr(firmware, "release_by_tag", lambda tag: firmware._parse_release(release()))
    monkeypatch.setattr(firmware, "probe", lambda host, timeout=5: {
        "ok": True, "error": "", "ver": "1.11.0", "env": "een_ander_bord",
        "board": "", "run": "app0", "other": {}})
    monkeypatch.setattr(firmware, "push", lambda *a, **k: pytest.fail("mocht niet versturen"))

    firmware._save_job(1, {"state": "voorbereiden"})
    firmware._run_inner(1, "http://node.invalid", "fw-v1.12.0", "heltec_v4_repeater_meshstats")
    assert firmware.job(1)["step"] == "env"


def test_release_zonder_image_voor_deze_bouwomgeving(db, monkeypatch):
    monkeypatch.setattr(firmware, "release_by_tag",
                        lambda tag: firmware._parse_release(release(envs=("ander_bord",))))
    monkeypatch.setattr(firmware, "probe", lambda host, timeout=5: {
        "ok": True, "error": "", "ver": "1.11.0", "env": "heltec_v4_repeater_meshstats",
        "board": "", "run": "app0", "other": {}})
    monkeypatch.setattr(firmware, "push", lambda *a, **k: pytest.fail("mocht niet versturen"))

    db.get_or_create_repeater("55d9a320a4e3", "DinX-Home")
    firmware._save_job(1, {"state": "voorbereiden"})
    firmware._run_inner(1, "http://node.invalid", "fw-v1.12.0", "")
    assert "geen image voor" in firmware.job(1)["msg"]


def test_geslaagde_upgrade_wordt_pas_gelukt_als_de_node_het_bevestigt(db, monkeypatch):
    """Het hele punt van deze functie. De node MELDEN dat het schrijven lukte is
    niet hetzelfde als de node terugzien op de nieuwe versie, en de oude
    upgradeweg verwarde precies die twee."""
    monkeypatch.setattr(firmware, "release_by_tag", lambda tag: firmware._parse_release(release()))
    monkeypatch.setattr(firmware, "download", lambda image: (b"x" * 10, "ab" * 32))
    monkeypatch.setattr(firmware, "push", lambda *a, **k: {"ok": 1, "step": "", "msg": "ok"})
    monkeypatch.setattr(firmware, "RETURN_POLL_S", 0)
    monkeypatch.setattr(firmware, "_nudge", lambda rep_id: None)

    antwoorden = iter([
        {"ok": True, "error": "", "ver": "1.11.0", "env": "heltec_v4_repeater_meshstats",
         "board": "", "run": "app0", "other": {}},                       # vooraf
        {"ok": False, "error": "niet bereikbaar", "ver": "", "env": "",
         "board": "", "run": "", "other": {}},                           # herstart bezig
        {"ok": True, "error": "", "ver": "1.12.0", "env": "heltec_v4_repeater_meshstats",
         "board": "", "run": "app1", "other": {}},                       # terug, nieuw
    ])
    monkeypatch.setattr(firmware, "probe", lambda host, timeout=5: next(antwoorden))

    db.get_or_create_repeater("55d9a320a4e3", "DinX-Home")
    firmware._save_job(1, {"state": "voorbereiden"})
    firmware._run_inner(1, "http://node.invalid", "fw-v1.12.0", "")
    job = firmware.job(1)
    assert job["state"] == "gelukt" and "1.12.0" in job["msg"]


def test_node_terug_op_de_oude_versie_is_een_eigen_uitkomst(db, monkeypatch):
    """Precies de gemeten fout van de oude weg: bytes aanvaard, node herstart,
    oude firmware. Die mag hier nooit als succes doorgaan."""
    monkeypatch.setattr(firmware, "release_by_tag", lambda tag: firmware._parse_release(release()))
    monkeypatch.setattr(firmware, "download", lambda image: (b"x" * 10, "ab" * 32))
    monkeypatch.setattr(firmware, "push", lambda *a, **k: {"ok": 1})
    monkeypatch.setattr(firmware, "RETURN_POLL_S", 0)
    monkeypatch.setattr(firmware, "_nudge", lambda rep_id: None)
    antwoorden = iter([
        {"ok": True, "error": "", "ver": "1.11.0", "env": "heltec_v4_repeater_meshstats",
         "board": "", "run": "app0", "other": {}},
        {"ok": True, "error": "", "ver": "1.11.0", "env": "heltec_v4_repeater_meshstats",
         "board": "", "run": "app0", "other": {}},
    ])
    monkeypatch.setattr(firmware, "probe", lambda host, timeout=5: next(antwoorden))

    db.get_or_create_repeater("55d9a320a4e3", "DinX-Home")
    firmware._save_job(1, {"state": "voorbereiden"})
    firmware._run_inner(1, "http://node.invalid", "fw-v1.12.0", "")
    job = firmware.job(1)
    assert job["state"] == "mislukt" and job["step"] == "terug_op_oud"


def test_node_die_niet_terugkomt_blijft_zichtbaar(db, monkeypatch):
    monkeypatch.setattr(firmware, "release_by_tag", lambda tag: firmware._parse_release(release()))
    monkeypatch.setattr(firmware, "download", lambda image: (b"x" * 10, "ab" * 32))
    monkeypatch.setattr(firmware, "push", lambda *a, **k: {"ok": 1})
    monkeypatch.setattr(firmware, "RETURN_WAIT_S", 0)
    monkeypatch.setattr(firmware, "RETURN_POLL_S", 0)
    monkeypatch.setattr(firmware, "probe", lambda host, timeout=5: {
        "ok": True, "error": "", "ver": "1.11.0", "env": "heltec_v4_repeater_meshstats",
        "board": "", "run": "app0", "other": {}})

    db.get_or_create_repeater("55d9a320a4e3", "DinX-Home")
    firmware._save_job(1, {"state": "voorbereiden"})
    firmware._run_inner(1, "http://node.invalid", "fw-v1.12.0", "")
    job = firmware.job(1)
    assert job["state"] == "niet_teruggekomen"
    # ...en die verdwijnt niet vanzelf: alleen iemand die hem gezien heeft mag
    # hem wegklikken.
    firmware.clear_job(1)
    assert firmware.job(1) is None


def test_een_lopende_opdracht_kan_niet_weggeklikt_worden(db):
    firmware._save_job(1, {"state": "schrijven"})
    firmware.clear_job(1)
    assert firmware.job(1)["state"] == "schrijven"


# --- de releaselijst ----------------------------------------------------------

def test_lijst_blijft_staan_als_github_niet_wil(monkeypatch):
    """Een beheerpagina die leeg wordt omdat GitHub even niet wilde, laat je in
    de steek op het moment dat je hem nodig hebt."""
    monkeypatch.setattr(firmware, "repo_slug", lambda: "DinXke/MeshStats")
    monkeypatch.setattr(firmware, "_get",
                        lambda *a, **k: json.dumps([release()]).encode())
    firmware._cache.update(at=0, items=[], error="", slug="")
    eerst = firmware.releases(force=True)
    assert len(eerst["items"]) == 1

    def stuk(*a, **k):
        raise OSError("netwerk weg")

    monkeypatch.setattr(firmware, "_get", stuk)
    daarna = firmware.releases(force=True)
    assert daarna["error"] == "offline"
    assert len(daarna["items"]) == 1


def test_zonder_repo_geen_lijst_maar_wel_een_reden(monkeypatch):
    monkeypatch.setattr(firmware, "repo_slug", lambda: "")
    firmware._cache.update(at=0, items=[], error="", slug="")
    assert firmware.releases(force=True)["error"] == "repo_unknown"


# --- de bevestiging voor een kritieke node ------------------------------------

@pytest.fixture
def knop(db, monkeypatch):
    """routes_admin.start_upgrade met de buitenwereld eromheen weggehaald.

    Zelfde aanpak als de ``knop``-fixture in test_settings_chain: de route wordt
    rechtstreeks aangeroepen, want er hangt geen middleware tussen die iets doet
    wat deze test wil zien.
    """
    from app import routes_admin

    monkeypatch.setattr(routes_admin, "require_login", lambda request: "beheerder")
    monkeypatch.setattr(routes_admin, "check_csrf", lambda request, csrf: None)
    monkeypatch.setattr(routes_admin, "_fw_context", lambda request, **extra: extra)
    gestart = []
    monkeypatch.setattr(routes_admin.firmware, "start",
                        lambda rep, tag, env: gestart.append((rep["id"], tag, env)) or {"ok": True})

    rid = db.get_or_create_repeater("55d9a320a4e3", "DinX-Home")["id"]
    db.execute("UPDATE repeaters SET fw_meshmanager='1.11.0', ota_host='http://x', is_critical=1 "
               "WHERE id=?", (rid,))

    def roep(confirm=""):
        return routes_admin.start_upgrade(None, rid, tag="fw-v1.12.0",
                                          expect_env="env_a", confirm=confirm, csrf="x")

    roep.gestart = gestart
    roep.rid = rid
    return roep


def test_kritieke_node_zonder_de_juiste_naam_start_niets(knop):
    """De fout die dit vangt is niet twijfel maar een klik op de verkeerde regel,
    en daar helpt een ja/nee-vraag niet tegen -- vandaar de naam overtypen."""
    uit = knop(confirm="")
    assert uit["started"]["ok"] is False
    assert "kritiek" in uit["started"]["error"]
    assert knop.gestart == []

    uit = knop(confirm="DinX-Thuis")
    assert uit["started"]["ok"] is False
    assert knop.gestart == []


def test_kritieke_node_met_de_juiste_naam_start_wel(knop):
    uit = knop(confirm="DinX-Home")
    assert uit["started"]["ok"] is True
    assert knop.gestart == [(knop.rid, "fw-v1.12.0", "env_a")]
