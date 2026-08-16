"""Een ingest die stilvalt, moet dat zeggen.

De aanleiding staat in docker-compose.yml en in test_compose.py: bij de
hernoeming naar MeshManager startte een container met een leeg brokerwachtwoord,
de broker antwoordde 'Not authorized', en er kwam dertien minuten lang geen
enkel pakket binnen. De site bleef intussen 200 antwoorden en oogde op elke
pagina gezond -- de tellers stonden er wel, maar "Verbonden: nee" in het grijs
tussen twaalf andere regels is niet hetzelfde als iets zeggen.

Wat hier vastligt is dus niet of de ingest werkt, maar of hij LIEGT wanneer hij
niet werkt. Dat is dezelfde belofte die dit project elders overal doet: liever
"ik weet het niet" dan een getal dat er goed uitziet.

De grens tussen 'stil' en 'goed' wordt met een verzonnen klok gemeten en niet
met echte tijd; een test die op een wachttijd leunt, is een test die op een
trage machine iets anders meet dan hij zegt.
"""
import pytest

from app import mqtt_ingest


@pytest.fixture
def schoon(monkeypatch):
    """Verse tellers, en een broker die is ingesteld tenzij een test anders wil."""
    staat = {"connected": False, "messages": 0, "packets": 0, "errors": 0,
             "last_error": "", "last_msg": None, "last_packet": None,
             "commands": 0, "refusals": 0, "connects": 0,
             "started": "2026-08-16T12:00:00Z"}
    monkeypatch.setattr(mqtt_ingest, "_state", staat)
    monkeypatch.setattr(mqtt_ingest, "MQTT_HOST", "broker.invalid")
    return staat


def _nu(monkeypatch, wanneer):
    """Zet de klok die health() gebruikt op een vast moment."""
    monkeypatch.setattr(mqtt_ingest.db, "utcnow", lambda: wanneer)


# --- de vier toestanden ------------------------------------------------------

def test_zonder_broker_is_dit_geen_alarm(schoon, monkeypatch):
    # De HTTP-ingest is een geldige manier om dit te draaien. Een installatie
    # die bewust geen MQTT gebruikt, mag geen rode balk krijgen -- dan leert
    # iedereen de balk negeren en is hij niets meer waard op de dag dat het wél
    # ergens om gaat.
    monkeypatch.setattr(mqtt_ingest, "MQTT_HOST", "")
    oordeel = mqtt_ingest.health()
    assert oordeel["state"] == "uit"
    assert oordeel["ok"] is True


def test_een_geweigerde_verbinding_is_ondubbelzinnig_kapot(schoon, monkeypatch):
    # Precies de storing van dertien minuten.
    _nu(monkeypatch, "2026-08-16T12:01:00Z")
    schoon["refusals"] = 3
    schoon["last_error"] = "verbinding geweigerd (code 5): niet geautoriseerd"
    oordeel = mqtt_ingest.health()
    assert oordeel["state"] == "geweigerd"
    assert oordeel["ok"] is False
    # De reden moet meereizen: "geweigerd" alleen stuurt iemand naar de logs,
    # en juist dat kostte de tijd.
    assert "geautoriseerd" in oordeel["why"]


def test_geen_verbinding_zonder_weigering_heet_anders(schoon, monkeypatch):
    # Netwerk of verkeerde host is een ander probleem dan verkeerde
    # inloggegevens, en de twee door elkaar halen stuurt iemand de verkeerde
    # kant op.
    _nu(monkeypatch, "2026-08-16T12:01:00Z")
    oordeel = mqtt_ingest.health()
    assert oordeel["state"] == "weg"
    assert oordeel["ok"] is False


def test_verbonden_en_verkeer_is_in_orde(schoon, monkeypatch):
    _nu(monkeypatch, "2026-08-16T12:05:00Z")
    schoon["connected"] = True
    schoon["last_msg"] = "2026-08-16T12:04:00Z"
    oordeel = mqtt_ingest.health()
    assert oordeel["state"] == "goed"
    assert oordeel["ok"] is True


# --- de stilte ---------------------------------------------------------------

def test_lang_verbonden_zonder_een_enkel_bericht_is_verdacht(schoon, monkeypatch):
    # De gemeenste variant: de verbinding staat, dus "Verbonden: ja", maar een
    # ACL op de broker gooit elk topic weg. De node ziet zijn publish slagen en
    # merkt er niets van; hier is de enige plaats waar het op te merken valt.
    _nu(monkeypatch, "2026-08-16T14:00:00Z")     # twee uur na de start
    schoon["connected"] = True
    oordeel = mqtt_ingest.health()
    assert oordeel["state"] == "stil"
    assert oordeel["ok"] is False
    assert "sinds de start" in oordeel["why"]


def test_een_ingest_die_net_gestart_is_klaagt_nog_niet(schoon, monkeypatch):
    # Anders staat de balk er bij elke herstart even, en dat leert iedereen hem
    # weg te klikken.
    _nu(monkeypatch, "2026-08-16T12:05:00Z")
    schoon["connected"] = True
    assert mqtt_ingest.health()["state"] == "goed"


def test_een_node_in_zuinige_modus_zet_de_balk_niet_aan(schoon, monkeypatch):
    # 's Nachts publiceert een node op zonnestroom hooguit een keer per uur. De
    # drempel staat daar ruim boven, want een waarschuwing die elke nacht afgaat
    # is geen waarschuwing meer.
    _nu(monkeypatch, "2026-08-16T13:00:00Z")
    schoon["connected"] = True
    schoon["last_msg"] = "2026-08-16T12:00:00Z"      # een uur geleden
    assert mqtt_ingest.health()["state"] == "goed"


def test_de_stiltedrempel_telt_ook_ruwe_pakketten(schoon, monkeypatch):
    # Een node kan pakketten doorsturen zonder statistieken; dat is verkeer en
    # dus geen stilte.
    _nu(monkeypatch, "2026-08-16T14:00:00Z")
    schoon["connected"] = True
    schoon["last_packet"] = "2026-08-16T13:59:00Z"
    assert mqtt_ingest.health()["state"] == "goed"


# --- de weigering in woorden -------------------------------------------------

@pytest.mark.parametrize("code,fragment", [
    (4, "gebruikersnaam of het wachtwoord"),
    (5, "niet geautoriseerd"),
])
def test_een_weigering_wordt_uitgelegd_in_plaats_van_genummerd(code, fragment):
    # "connection refused (code 5)" is geen aanwijzing. Dit wel: het noemt de
    # twee variabelen die je moet nakijken, en dat is precies wat er die
    # dertien minuten ontbrak.
    assert fragment in mqtt_ingest._WEIGERING[code]
    assert "MM_MQTT_USER" in mqtt_ingest._WEIGERING[code]


def test_een_kapotte_tijdstempel_breekt_het_oordeel_niet(schoon, monkeypatch):
    # Dit draait in de weergave van de pagina die juist moet vertellen dat er
    # iets mis is. Een exception hier zou die melding wissen.
    _nu(monkeypatch, "niet eens een datum")
    schoon["connected"] = True
    schoon["last_msg"] = "ook niet"
    assert mqtt_ingest.health()["state"] == "goed"
