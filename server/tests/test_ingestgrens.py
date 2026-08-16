"""Tests voor de vertrouwensgrens rond de ingest.

Wat hier bewaakt wordt is niet dat er iets geweigerd wordt, maar WAAR. Het
verschil is de hele maatregel: een bericht met tweehonderd verzonnen
metrieknamen mag de databank niet raken en daarna opgeruimd worden, het mag hem
niet raken. Elke test hieronder controleert daarom naast de uitzondering ook dat
de tabel leeg gebleven is.

De tweede helft gaat over ``is_public``. Een repeater die vanzelf ontstaat uit
een MQTT-bericht verscheen tot nu toe meteen op de publieke voorpagina; wie op
het topic mocht publiceren, publiceerde daarmee ook op de site. Dat het nu
verborgen binnenkomt is een gedragswijziging, en een gedragswijziging zonder
test is een gedragswijziging die bij de volgende opruimbeurt sneuvelt.
"""
import pytest

from app import config


@pytest.fixture
def db(tmp_path, monkeypatch):
    """De db-module tegen een verse, tijdelijke database."""
    from app import db as db_module
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "test.sqlite3")
    db_module._conn = None
    yield db_module
    if db_module._conn is not None:
        db_module._conn.close()
        db_module._conn = None


def _count(db, table: str) -> int:
    return db.qone(f"SELECT COUNT(*) AS n FROM {table}")["n"]


# --- de sleutel ---------------------------------------------------------------

@pytest.mark.parametrize("waarde", [
    "",                     # leeg
    "x",                    # te kort
    "zz",                   # geen hex
    "aabb;DROP",            # leestekens
    "aa bb",                # spatie
    "a" * 65,               # te lang (MAX_KEY_HEX is 64)
    None,
    12.5,                   # een JSON-getal met een punt erin
    True,
])
def test_onzinnige_sleutel_maakt_geen_repeater(db, waarde):
    with pytest.raises(ValueError):
        db.get_or_create_repeater(waarde, "Iemand")
    assert _count(db, "repeaters") == 0


@pytest.mark.parametrize("waarde", ["ab", "aabbcc", "AABBCC", " aabbccddeeff ",
                                    "a" * 64])
def test_een_echte_sleutel_komt_er_gewoon_door(db, waarde):
    rij = db.get_or_create_repeater(waarde, "Node")
    assert rij["pubkey_prefix"] == waarde.strip().lower()


def test_key_prefix_geeft_leeg_terug_in_plaats_van_op_te_werpen(db):
    # De zachte variant, voor bellers die een alternatief hebben.
    assert db.key_prefix("AaBbCc") == "aabbcc"
    assert db.key_prefix("niet-hex") == ""
    assert db.key_prefix(None) == ""


# --- de aantallen -------------------------------------------------------------

def test_te_veel_metrieken_wordt_geweigerd_voor_het_de_databank_raakt(db):
    veel = {f"verzonnen_{i}": i for i in range(db.MAX_METRICS_PER_MESSAGE + 1)}
    with pytest.raises(ValueError, match="metrieken in één bericht"):
        db.check_snapshot("aabbcc", veel)
    assert _count(db, "latest") == 0


def test_precies_het_maximum_mag_nog(db):
    net_goed = {f"m{i}": i for i in range(db.MAX_METRICS_PER_MESSAGE)}
    assert db.check_snapshot("aabbcc", net_goed) == "aabbcc"


def test_te_veel_buren_wordt_geweigerd(db):
    buren = [{"prefix": "aabbcc"}] * (db.MAX_NEIGHBORS_PER_MESSAGE + 1)
    with pytest.raises(ValueError, match="buren in één bericht"):
        db.check_snapshot("aabbcc", {"online": 1}, buren)
    assert _count(db, "neighbors") == 0


def test_een_onmogelijk_lange_metrieknaam_wordt_geweigerd(db):
    with pytest.raises(ValueError, match="onbruikbare metrieknaam"):
        db.check_snapshot("aabbcc", {"x" * (db.MAX_METRIC_NAME + 1): 1})


def test_metrics_moet_een_object_zijn(db):
    with pytest.raises(ValueError, match="metrics moet"):
        db.check_snapshot("aabbcc", ["nee"])


def test_neighbors_moet_een_lijst_zijn(db):
    with pytest.raises(ValueError, match="neighbors moet"):
        db.check_snapshot("aabbcc", {"online": 1}, {"prefix": "aabbcc"})


# --- losse burenregels: eruit, maar de rest blijft ----------------------------

def test_een_kapotte_burenregel_kost_de_goede_regels_niets(db):
    """Aantallen keuren het bericht af, losse regels alleen zichzelf.

    Dat onderscheid is met opzet gemaakt en dus het waard om vast te leggen:
    veertig goede buren weggooien omdat er één rare tussen zit, kost meer dan
    het beschermt.
    """
    rep = db.get_or_create_repeater("aabbcc112233", "Node")
    db.ingest(rep["id"], db.utcnow(), {"online": True}, [
        {"prefix": "ddeeff", "snr": -3.0},          # goed
        {"prefix": "niet-hex", "snr": -4.0},        # eruit
        {"prefix": "", "snr": -5.0},                # eruit
        {"prefix": "a" * 65, "snr": -6.0},          # eruit: te lang
        "helemaal geen object",                     # eruit
        {"prefix": "112233", "snr": -7.0},          # goed
    ])

    prefixen = {r["prefix"] for r in db.q("SELECT prefix FROM neighbors")}
    assert prefixen == {"ddeeff", "112233"}
    # En de metriek van datzelfde bericht staat er gewoon.
    assert db.latest_for(rep["id"])["online"]["value"] == 1.0


def test_een_kapotte_burenregel_maakt_ook_geen_latest_rij(db):
    """De tweede helft van hetzelfde gat: neighbor_<prefix> is óók een rij."""
    rep = db.get_or_create_repeater("aabbcc112233", "Node")
    db.ingest(rep["id"], db.utcnow(), {"online": True},
              [{"prefix": "onzin!!", "snr": -4.0}])

    namen = set(db.latest_for(rep["id"]))
    assert not any(n.startswith("neighbor_") for n in namen)


# --- het repeaterplafond ------------------------------------------------------

def test_het_repeaterplafond_weigert_in_plaats_van_te_snoeien(db, monkeypatch):
    """Weigeren en niet verwijderen, want een repeater weggooien is zijn
    historiek weggooien -- ``latest`` en ``repeater_cli`` hangen er met CASCADE
    aan."""
    monkeypatch.setattr(db, "MAX_REPEATERS", 3)
    for i in range(3):
        db.get_or_create_repeater(f"aabbcc11223{i}", f"Node {i}")

    with pytest.raises(ValueError, match="al 3 repeaters bekend"):
        db.get_or_create_repeater("ffffffffffff", "De vierde")

    assert _count(db, "repeaters") == 3
    # De bestaande drie zijn ongemoeid: het plafond raakt alleen het aanmaken.
    assert db.find_repeater("aabbcc112230") is not None


def test_een_bekende_repeater_komt_er_boven_het_plafond_nog_steeds_door(db, monkeypatch):
    rep = db.get_or_create_repeater("aabbcc112233", "Node")
    monkeypatch.setattr(db, "MAX_REPEATERS", 1)
    weer = db.get_or_create_repeater("aabbcc112233", "Node met nieuwe naam")
    assert weer["id"] == rep["id"]
    assert weer["name"] == "Node met nieuwe naam"


# --- is_public ----------------------------------------------------------------

def test_een_nieuwe_repeater_komt_verborgen_binnen(db):
    rep = db.get_or_create_repeater("aabbcc112233", "Nieuweling")
    assert rep["is_public"] == 0


def test_een_bestaande_repeater_blijft_zichtbaar(db):
    """De belofte aan wie deze site al draait: er verandert niets aan wat hij ziet.

    De INSERT draait alleen voor een sleutel die nog niet bestond, dus een
    repeater die vandaag publiek is, is dat morgen nog -- ook als er intussen
    berichten van binnenkomen.
    """
    rep = db.get_or_create_repeater("aabbcc112233", "Bestaand")
    db.execute("UPDATE repeaters SET is_public=1 WHERE id=?", (rep["id"],))

    for i in range(3):
        db.get_or_create_repeater("aabbcc112233", "Bestaand")
        db.ingest(rep["id"], db.utcnow(), {"online": True}, None)

    assert db.qone("SELECT is_public FROM repeaters WHERE id=?",
                   (rep["id"],))["is_public"] == 1


def test_de_beheerpagina_telt_hoeveel_er_verborgen_wachten(db):
    # Verborgen binnenkomen mag, ongemerkt binnenkomen niet.
    db.get_or_create_repeater("aabbcc112233", "Een")
    zichtbaar = db.get_or_create_repeater("ddeeff445566", "Twee")
    db.execute("UPDATE repeaters SET is_public=1 WHERE id=?", (zichtbaar["id"],))

    overzicht = db.storage_overview()
    assert overzicht["repeaters"] == 2
    assert overzicht["repeaters_hidden"] == 1
    assert overzicht["repeaters_max"] == db.MAX_REPEATERS


# --- de MQTT-weg als geheel ---------------------------------------------------

def test_mqtt_topic_met_een_onzinnige_node_wordt_geweigerd(db):
    import json

    from app import mqtt_ingest

    payload = json.dumps({"metrics": {"online": True}}).encode()
    assert mqtt_ingest.handle_message("meshmanager/niet-hex/stats", payload) is False
    assert _count(db, "repeaters") == 0
    assert "sleutel" in mqtt_ingest.status()["last_error"]


def test_mqtt_bericht_met_te_veel_metrieken_wordt_geweigerd(db):
    import json

    from app import mqtt_ingest

    veel = {f"m{i}": i for i in range(db.MAX_METRICS_PER_MESSAGE + 5)}
    payload = json.dumps({"metrics": veel}).encode()
    assert mqtt_ingest.handle_message("meshmanager/aabbcc112233/stats", payload) is False
    assert _count(db, "repeaters") == 0
    assert _count(db, "latest") == 0


@pytest.mark.parametrize("voorvoegsel", ["meshmanager", "meshcore"])
def test_beide_topicvoorvoegsels_komen_even_ver(db, voorvoegsel):
    """De hernoeming naar MeshManager mag de grens niet scheef zetten.

    De site luistert op allebei; als de keuring aan één van de twee zou hangen,
    zou een node die nog niet geflasht is stilletjes buitengesloten worden -- of,
    erger, de oude weg zou de gecontroleerde niet zijn.
    """
    import json

    from app import mqtt_ingest

    goed = json.dumps({"metrics": {"online": True}}).encode()
    assert mqtt_ingest.handle_message(f"{voorvoegsel}/aabbcc112233/stats", goed) is True
    assert db.find_repeater("aabbcc112233") is not None

    slecht = json.dumps({"repeater": {"pubkey_prefix": "onzin!"},
                         "metrics": {"online": True}}).encode()
    assert mqtt_ingest.handle_message(f"{voorvoegsel}/aabbcc112233/stats", slecht) is False
    assert _count(db, "repeaters") == 1
