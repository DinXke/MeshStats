"""Tests voor de instellingenketen: knop -> wachtrij -> poller -> opslag.

De keten is fragiel op één plek die geen enkele foutmelding oplevert: de
wachtrij op de site is clear-on-read, dus zodra de poller een verzoek heeft
opgehaald bestaat het nergens meer. Gaat er daarna iets mis met het herkennen
van de sleutel, dan is het verzoek weg en blijft de beheerpagina hangen op de
laatste geslaagde uitlezing. Deze tests leggen precies dat vast.

Sinds de nodes rechtstreeks over MQTT publiceren is er een tweede vraag bij
gekomen: is er überhaupt iemand om iets aan te vragen? Onderaan staat wat de
knop doet in elk van de gevallen, want zwijgend schrijven in een wachtrij die
niemand leegt is net het gedrag dat weg moest.
"""
import pytest


@pytest.fixture
def db(tmp_path, monkeypatch):
    """De db-module tegen een verse, tijdelijke database."""
    from app import config
    from app import db as db_module
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "test.sqlite3")
    db_module._conn = None
    yield db_module
    if db_module._conn is not None:
        db_module._conn.close()
        db_module._conn = None


def test_wachtrij_overleeft_een_herstart(db):
    # De wachtrij staat in de settings-tabel, niet in het geheugen van het
    # proces: een container die tussen de klik en de eerstvolgende poll
    # herbouwd wordt, mag het verzoek niet meenemen in zijn val.
    db.request_settings("e3d3f4d7edd0", ["name", "role"])
    db._conn.close()
    db._conn = None  # zoals een verse start de database opnieuw opent

    assert db.pop_settings_requests() == [
        {"prefix": "e3d3f4d7edd0", "params": ["name", "role"]}
    ]


def test_wachtrij_is_leeg_na_uitreiking(db):
    db.request_settings("e3d3f4d7edd0", ["name"])
    assert db.pop_settings_requests()
    assert db.pop_settings_requests() == []


def test_openstaand_verzoek_is_zichtbaar_tot_het_opgehaald_is(db):
    # Waar de beheerpagina op steunt om "nog niemand heeft gepold" te kunnen
    # onderscheiden van "opgehaald en daarna stilte".
    assert db.pending_settings_request("e3d3f4d7edd0") is None
    db.request_settings("e3d3f4d7edd0", ["name"])
    assert db.pending_settings_request("e3d3f4d7edd0")
    assert db.pending_settings_request("55d9a320a4e3") is None
    db.pop_settings_requests()
    assert db.pending_settings_request("e3d3f4d7edd0") is None


def test_antwoord_met_kortere_sleutel_komt_bij_dezelfde_repeater_terecht(db):
    # Home Assistant stuurt vijf sleutelbytes, de eigen firmware van een node
    # zes, en de opgeslagen sleutel groeit mee met de langste die langskwam.
    # Wie hier op string-gelijkheid test, gooit een opvraging weg die één tot
    # twee minuten LoRa-zendtijd heeft gekost.
    rep = db.get_or_create_repeater("e3d3f4d7edd0", "BE-HSS-JessaZH.VIR")

    gevonden = db.find_repeater("e3d3f4d7ed")

    assert gevonden is not None
    assert gevonden["id"] == rep["id"]
    db.upsert_cli_settings(gevonden["id"], {"role": "repeater", "tx": "18"})
    waarden = {r["param"]: r["value"] for r in db.cli_settings_for(rep["id"])}
    assert waarden["role"] == "repeater"
    assert waarden["tx"] == "18"


def test_te_korte_sleutel_matcht_niet(db):
    # Zes hextekens kunnen bij toeval samenvallen; instellingen bij de
    # verkeerde node schrijven is erger dan ze laten liggen.
    db.get_or_create_repeater("e3d3f4d7edd0", "BE-HSS-JessaZH.VIR")

    assert db.find_repeater("e3d3f4") is None


def test_onbeantwoorde_parameter_wordt_bewaard_als_leeg(db):
    # "(geen antwoord)" op de beheerpagina is een opgeslagen NULL met een verse
    # tijdstempel, geen ontbrekende rij: de opvraging liep wel, de repeater
    # zweeg. Het onderscheid met "nooit opgevraagd" moet zichtbaar blijven.
    rep = db.get_or_create_repeater("e3d3f4d7edd0", "BE-HSS-JessaZH.VIR")

    db.upsert_cli_settings(rep["id"], {"name": None, "role": "repeater"})

    rijen = {r["param"]: r for r in db.cli_settings_for(rep["id"])}
    assert rijen["name"]["value"] is None
    assert rijen["name"]["updated"]
    assert "radio" not in rijen


def test_firmwareversie_wordt_onthouden_en_niet_gewist(db):
    # De MeshStats-versie beslist of de knop een opdracht mag publiceren. Een
    # bron die er niets over zegt -- de HTTP-API kent alleen de MeshCore-versie
    # -- mag wat een andere bron wel wist niet uitvegen.
    rep = db.get_or_create_repeater("e3d3f4d7edd0", "BE-HSS-JessaZH.VIR")

    db.record_firmware(rep["id"], "v1.16.0", "1.8.0")
    db.record_firmware(rep["id"], "v1.16.1", None)

    rij = db.qone("SELECT fw, fw_meshstats FROM repeaters WHERE id=?", (rep["id"],))
    assert rij["fw"] == "v1.16.1"
    assert rij["fw_meshstats"] == "1.8.0"


def test_uitreiking_laat_een_spoor_na(db):
    # Het gemeenste geval: de poller neemt het verzoek mee en er komt niets
    # terug. De wachtrij is dan leeg, precies zoals na een geslaagde opvraging,
    # en de tabel op de pagina toont ongewijzigd wat er al stond. Zonder dit
    # spoor is dat van een geslaagde ronde niet te onderscheiden.
    assert db.settings_delivered_at("e3d3f4d7edd0") is None
    db.request_settings("e3d3f4d7edd0", ["name"])
    assert db.settings_delivered_at("e3d3f4d7edd0") is None

    db.pop_settings_requests()

    assert db.settings_delivered_at("e3d3f4d7edd0")
    assert db.settings_delivered_at("55d9a320a4e3") is None


def test_lege_uitreiking_overschrijft_het_spoor_niet(db):
    # De poller pollt om de 30 seconden en haalt meestal niets op. Zou elke
    # lege poll het spoor bijwerken, dan leek elke opvraging net uitgereikt.
    db.request_settings("e3d3f4d7edd0", ["name"])
    db.pop_settings_requests()
    eerst = db.settings_delivered_at("e3d3f4d7edd0")

    db.pop_settings_requests()

    assert db.settings_delivered_at("e3d3f4d7edd0") == eerst


def test_pollerbezoek_wordt_bijgehouden(db):
    # Wat "er is niemand die dit ophaalt" onderscheidbaar maakt van "net
    # opgehaald". Zonder dit ziet een lege wachtrij er in beide gevallen
    # identiek uit.
    assert db.poller_last_seen() is None
    db.note_poller_seen()
    assert db.poller_last_seen()


# --- wat de knop doet, per geval -------------------------------------------

@pytest.fixture
def knop(db, monkeypatch):
    """routes_admin._dispatch met de buitenwereld onder controle.

    Geeft een functie terug die (repeater, opdracht) uitvoert en teruggeeft wat
    er gebeurd is, plus de lijst van wat er naar de broker ging.
    """
    from app import commanding, mqtt_ingest, routes_admin

    verstuurd = []
    staat = {"broker": True, "poller": None}

    monkeypatch.setattr(mqtt_ingest, "can_publish", lambda: staat["broker"])
    monkeypatch.setattr(mqtt_ingest, "publish_command",
                        lambda node, cmd: (verstuurd.append((node, cmd)), True)[1])
    monkeypatch.setattr(db, "poller_last_seen", lambda: staat["poller"])
    # Zodat 'poller aanwezig' niet van de klok afhangt.
    monkeypatch.setattr(commanding, "_fresh",
                        lambda ts, seconds, now: ts == "vers")

    def run(rep, opdracht="settings"):
        return routes_admin._dispatch(rep, opdracht)

    run.verstuurd = verstuurd
    run.staat = staat
    return run


def _node(db, fw="1.8.0"):
    rep = db.get_or_create_repeater("e3d3f4d7edd0", "BE-HSS-JessaZH.VIR")
    db.record_source(rep["id"], "e3d3f4d7edd0")
    db.record_firmware(rep["id"], "v1.16.0", fw)
    return db.qone("SELECT * FROM repeaters WHERE id=?", (rep["id"],))


def test_knop_vraagt_het_de_node_zelf(db, knop):
    rep = _node(db)

    assert knop(rep) == "mqtt"
    assert knop.verstuurd == [("e3d3f4d7edd0", "settings")]
    # Geen poller in zicht, dus ook niets in de wachtrij achtergelaten.
    assert db.pending_settings_request("e3d3f4d7edd0") is None


def test_knop_gebruikt_beide_wegen_als_beide_er_zijn(db, knop):
    rep = _node(db)
    knop.staat["poller"] = "vers"

    assert knop(rep) == "both"
    assert knop.verstuurd
    assert db.pending_settings_request("e3d3f4d7edd0")


def test_knop_valt_terug_op_de_wachtrij(db, knop):
    # Te oude firmware: de node zou de opdracht niet horen, de poller wel.
    rep = _node(db, fw="1.7.2")
    knop.staat["poller"] = "vers"

    assert knop(rep) == "queued"
    assert knop.verstuurd == []
    assert db.pending_settings_request("e3d3f4d7edd0")


def test_knop_belooft_niets_als_er_niemand_is(db, knop):
    # Het geval waar dit allemaal om begonnen is: Home Assistant weg, firmware
    # zonder cmd-topic. Er hoort niets te vertrekken en niets te blijven liggen.
    rep = _node(db, fw="1.7.2")

    assert knop(rep) == "none"
    assert knop.verstuurd == []
    assert db.pending_settings_request("e3d3f4d7edd0") is None


def test_statusknop_stuurt_status_en_niet_settings(db, knop):
    rep = _node(db)

    assert knop(rep, "status") == "mqtt"
    assert knop.verstuurd == [("e3d3f4d7edd0", "status")]
