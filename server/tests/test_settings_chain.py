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

    def _publish(node, cmd, subject=None):
        # Opgeslagen zoals de node het op het cmd-topic ziet, argument en al:
        # dat is de tekst waar de firmware een exacte match op doet, en dus de
        # enige vorm waarvan het zin heeft ze hier vast te leggen.
        verstuurd.append((node, cmd if subject is None else f"{cmd} {subject}"))
        return True

    monkeypatch.setattr(mqtt_ingest, "publish_command", _publish)
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


# --- de weg langs een monitor ----------------------------------------------

def _doorgestuurd(db, monitor_fw="1.9.0"):
    """Een repeater die zelf niet publiceert, met een monitor die dat wel doet.

    Dit is de dakrepeater: hij hangt op een gebouw, praat alleen over LoRa, en
    zijn cijfers komen binnen omdat een andere node hem uitleest en doorstuurt.
    """
    monitor = db.get_or_create_repeater("55d9a320a4e3", "DinX-Home")
    db.record_source(monitor["id"], "55d9a320a4e3")
    db.record_firmware(monitor["id"], "v1.16.0", monitor_fw)

    dak = db.get_or_create_repeater("e3d3f4d7edd0", "BE-HSS-JessaZH.VIR")
    db.record_source(dak["id"], "55d9a320a4e3")
    return db.qone("SELECT * FROM repeaters WHERE id=?", (dak["id"],))


def test_doorgestuurde_repeater_wordt_via_zijn_monitor_gevraagd(db, knop):
    # Waar 1.9.0 voor bestaat. De opdracht gaat naar de monitor en draagt de
    # sleutel van het onderwerp mee; zonder dat argument zou die node zijn eigen
    # instellingen uitlezen en die onder de verkeerde naam publiceren.
    rep = _doorgestuurd(db)

    assert knop(rep) == "mqtt"
    assert knop.verstuurd == [("55d9a320a4e3", "settings e3d3f4d7edd0")]


def test_monitor_met_oudere_firmware_krijgt_niets(db, knop):
    # 1.8.0 kent het cmd-topic maar weigert het argument en telt de opdracht
    # als geweigerd. Publiceren zou hier een stilte opleveren die op de pagina
    # niet van een onbereikbare node te onderscheiden is.
    rep = _doorgestuurd(db, monitor_fw="1.8.0")

    assert knop(rep) == "none"
    assert knop.verstuurd == []


def test_status_gaat_niet_langs_een_monitor(db, knop):
    # Een monitor kan gevraagd worden de CLI van een ander uit te lezen, maar
    # geen statusbericht namens hem te sturen -- die cijfers stuurt hij uit
    # zichzelf al door, elke ronde. De knop hoort dat niet te beloven.
    rep = _doorgestuurd(db)

    assert knop(rep, "status") == "none"
    assert knop.verstuurd == []


# --- wat er terugkomt: instellingen die een monitor doorstuurt --------------
#
# Tot 1.9.0 gooide de ingest élk instellingenbericht weg dat niet over de
# publicerende node zelf ging. Dat was terecht zolang de firmware nooit iets
# anders stuurde; nu ze dat wel doet, verhuist de regel naar "van de node die
# de cijfers van deze repeater al doorstuurt". Deze tests leggen vast waar die
# grens nu ligt, want ze is de enige die er nog is.

def _bericht(prefix, settings, naam=None):
    import json
    body = {"repeater": {"pubkey_prefix": prefix}, "metrics": {}, "settings": settings}
    if naam:
        body["repeater"]["name"] = naam
    return json.dumps(body).encode()


def test_monitor_mag_instellingen_van_zijn_gemonitorde_repeater_publiceren(db):
    from app import mqtt_ingest
    dak = _doorgestuurd(db)

    mqtt_ingest._handle_payload(
        "meshcore/55d9a320a4e3/stats",
        _bericht("e3d3f4d7edd0", {"role": "repeater", "tx": "22"}))

    waarden = {r["param"]: r["value"] for r in db.cli_settings_for(dak["id"])}
    assert waarden == {"role": "repeater", "tx": "22"}


def test_onbeantwoorde_parameter_uit_een_sweep_komt_binnen_als_leeg(db):
    # De firmware stuurt null voor wat ze wél vroeg en niet kreeg. Zou de
    # ingest dat wegfilteren, dan bleef de pagina een waarde uit maart tonen
    # met alleen een nieuwe tijdstempel eronder -- en de meest voorkomende
    # oorzaak (de monitor heeft daar geen adminrechten) zou onzichtbaar zijn.
    from app import mqtt_ingest
    dak = _doorgestuurd(db)
    db.upsert_cli_settings(dak["id"], {"role": "repeater"})

    mqtt_ingest._handle_payload(
        "meshcore/55d9a320a4e3/stats",
        _bericht("e3d3f4d7edd0", {"role": None, "tx": "22"}))

    rijen = {r["param"]: r for r in db.cli_settings_for(dak["id"])}
    assert rijen["role"]["value"] is None
    assert rijen["role"]["updated"]
    assert rijen["tx"]["value"] == "22"


def test_lege_string_blijft_wel_geweigerd(db):
    # Een leeg antwoord is iets anders dan geen antwoord: de firmware laat een
    # parameter die ze niet kon lezen weg, dus een lege string die tóch
    # binnenkomt draagt niets en zou een gekende waarde uitvegen.
    from app import mqtt_ingest
    dak = _doorgestuurd(db)
    db.upsert_cli_settings(dak["id"], {"role": "repeater"})

    mqtt_ingest._handle_payload(
        "meshcore/55d9a320a4e3/stats", _bericht("e3d3f4d7edd0", {"role": "  "}))

    waarden = {r["param"]: r["value"] for r in db.cli_settings_for(dak["id"])}
    assert waarden["role"] == "repeater"


def test_een_vreemde_node_mag_geen_instellingen_voor_een_ander_schrijven(db):
    # De grens. Wie de brokergegevens heeft kon altijd al cijfers voor eender
    # welke repeater publiceren -- dat gat staat in de kop van mqtt_ingest.py en
    # hoort thuis in een ACL per node. Instellingen kosten nu één stap extra:
    # je moet eerst de node worden waarlangs deze repeater binnenkomt, en dat is
    # een zichtbare wijziging op de beheerpagina (source_prefix).
    from app import mqtt_ingest
    dak = _doorgestuurd(db)
    vreemde = db.get_or_create_repeater("aabbccddeeff", "iemand anders")
    db.record_source(vreemde["id"], "aabbccddeeff")

    mqtt_ingest._handle_payload(
        "meshcore/aabbccddeeff/stats", _bericht("e3d3f4d7edd0", {"role": "client"}))

    assert db.cli_settings_for(dak["id"]) == []


def test_eigen_instellingen_blijven_gewoon_werken(db):
    from app import mqtt_ingest
    eigen = _node(db)

    mqtt_ingest._handle_payload(
        "meshcore/e3d3f4d7edd0/stats", _bericht("e3d3f4d7edd0", {"role": "repeater"}))

    waarden = {r["param"]: r["value"] for r in db.cli_settings_for(eigen["id"])}
    assert waarden == {"role": "repeater"}
