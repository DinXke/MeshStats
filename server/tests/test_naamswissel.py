"""De terugvalpaden van de hernoeming MeshStats -> MeshManager.

Deze tests bewaken één belofte: een installatie die vandaag draait, blijft
draaien als alleen de server bijgewerkt wordt. Dat is geen luxe maar de kern
van de migratie -- nodes en server gaan nooit op hetzelfde moment om, dus er is
altijd een periode waarin de oude namen nog waar zijn.

Elk van deze tests hoort te VERDWIJNEN op de dag dat de bijbehorende terugval
weg mag. Zolang ze er staan, staat de terugval er ook.

De configuratie wordt in een apart proces gemeten. ``app.config`` leest de
omgeving één keer, bij het importeren, en dat is precies goed voor een server
maar onmeetbaar binnen een testrun die de module al geladen heeft.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

from app import db, mqtt_ingest

SERVER_DIR = Path(__file__).resolve().parent.parent

_MEET = """
import json, sys
from app import config
print(json.dumps({
    "data_dir": str(config.DATA_DIR),
    "db_path": str(config.DB_PATH),
    "site_name": config.SITE_NAME,
    "retention": config.RETENTION_DAYS,
}))
"""


def _lees_config(env: dict) -> dict:
    omgeving = {k: v for k, v in os.environ.items()
                if not k.startswith(("MM_", "MCS_"))}
    omgeving.update(env)
    uit = subprocess.run([sys.executable, "-c", _MEET], cwd=str(SERVER_DIR),
                         env=omgeving, capture_output=True, text=True, check=True)
    return json.loads(uit.stdout.strip().splitlines()[-1])


# --- omgevingsvariabelen ----------------------------------------------------

def test_een_bestaande_env_met_oude_namen_blijft_werken(tmp_path):
    # Wie zijn site draaiend heeft, mag niet eerst een configuratiebestand
    # hoeven herschrijven voor hij weer opstart.
    uit = _lees_config({"MCS_DATA_DIR": str(tmp_path),
                        "MCS_SITE_NAME": "Oude naam",
                        "MCS_RETENTION_DAYS": "42"})
    assert uit["data_dir"] == str(tmp_path)
    assert uit["site_name"] == "Oude naam"
    assert uit["retention"] == 42


def test_de_nieuwe_naam_wint_als_beide_gezet_zijn(tmp_path):
    # Komt echt voor tijdens de overgang: docker-compose vult de nieuwe naam in
    # terwijl een oude .env de oude nog bevat. Wie beide zet, bedoelt de nieuwe.
    uit = _lees_config({"MM_DATA_DIR": str(tmp_path),
                        "MM_SITE_NAME": "Nieuw",
                        "MCS_SITE_NAME": "Oud"})
    assert uit["site_name"] == "Nieuw"


def test_een_bewust_lege_nieuwe_waarde_is_een_antwoord(tmp_path):
    # Leeg betekent iets ("geen tijdreeksdatabank, hou alles in SQLite") en mag
    # dus niet stilzwijgend door de oude naam overruled worden.
    uit = _lees_config({"MM_DATA_DIR": str(tmp_path),
                        "MM_SITE_NAME": "",
                        "MCS_SITE_NAME": "Oud"})
    assert uit["site_name"] == ""


# --- de databankbestandsnaam ------------------------------------------------

def test_een_bestaande_databank_met_de_oude_naam_wordt_gewoon_gebruikt(tmp_path):
    # Het scenario dat telt: de site draait al maanden, en na de update moet
    # dezelfde databank open -- niet een lege nieuwe naast een bestand dat
    # niemand meer leest.
    oud = tmp_path / "mcs.sqlite3"
    oud.write_bytes(b"SQLite format 3\x00")
    uit = _lees_config({"MM_DATA_DIR": str(tmp_path)})
    assert uit["db_path"] == str(oud)


def test_de_oude_databank_wordt_niet_hernoemd(tmp_path):
    # Bewust niet, want hernoemen is eenrichtingsverkeer: wie terugrolt naar de
    # vorige versie van de site vindt dan geen databank meer en krijgt een lege.
    oud = tmp_path / "mcs.sqlite3"
    oud.write_bytes(b"SQLite format 3\x00")
    _lees_config({"MM_DATA_DIR": str(tmp_path)})
    assert oud.exists()
    assert not (tmp_path / "meshmanager.sqlite3").exists()


def test_een_verse_installatie_krijgt_de_nieuwe_naam(tmp_path):
    uit = _lees_config({"MM_DATA_DIR": str(tmp_path)})
    assert uit["db_path"] == str(tmp_path / "meshmanager.sqlite3")


# --- de firmwareversie in de payload ----------------------------------------

def test_de_oude_sleutel_voor_de_moduleversie_telt_nog_mee():
    # Deze versie beslist of de site een node iets mág vragen. Hem niet
    # herkennen grijst knoppen uit op nodes die het prima aankunnen.
    assert db.payload_module_version({"fw_meshstats": "1.11.0"}) == "1.11.0"


def test_de_nieuwe_sleutel_gaat_voor():
    assert db.payload_module_version(
        {"fw_meshmanager": "2.0.0", "fw_meshstats": "1.11.0"}) == "2.0.0"


def test_zonder_versie_blijft_het_leeg():
    assert db.payload_module_version({}) is None
    assert db.payload_module_version(None) is None


# --- het onthouden van het topicvoorvoegsel ---------------------------------

def test_het_voorvoegsel_overleeft_een_herstart_van_de_site():
    # De cache in het geheugen is na een herstart leeg, terwijl de node nog
    # steeds luistert waar hij gisteren luisterde. Zonder deze kolom is elke
    # opdracht in de eerste minuten na een herstart een gok.
    rij = db.get_or_create_repeater("aa11bb22cc33", "Voorvoegseltest")
    db.record_topic_prefix("aa11bb22cc33", "meshcore")
    assert db.topic_prefix_for("aa11bb22cc33") == "meshcore"

    mqtt_ingest._seen_prefix.pop("aa11bb22cc33", None)
    assert mqtt_ingest.command_prefix("aa11bb22cc33") == "meshcore"
    assert mqtt_ingest.command_topics("aa11bb22cc33") == (
        "meshcore/aa11bb22cc33/cmd",)

    db.record_topic_prefix("aa11bb22cc33", "meshmanager")
    assert db.topic_prefix_for("aa11bb22cc33") == "meshmanager"
    assert rij is not None


def test_een_leeg_voorvoegsel_wist_niets():
    # Een bericht op een topic zonder herkenbaar voorvoegsel mag niet wissen
    # wat we al wisten.
    db.get_or_create_repeater("dd44ee55ff66", "Leegtest")
    db.record_topic_prefix("dd44ee55ff66", "meshcore")
    db.record_topic_prefix("dd44ee55ff66", "")
    assert db.topic_prefix_for("dd44ee55ff66") == "meshcore"
