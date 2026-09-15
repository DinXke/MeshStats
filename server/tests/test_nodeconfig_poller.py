"""De vijfde schrijfweg: de opdrachtwachtrij, uitgevoerd over LoRa.

WAAROM DEZE WEG ER IS. Tot nu toe was 'mesh' de enige schrijfweg naar een node
zonder IP-pad, en die liep over de MONITOR van die node. Toen de dakrepeater
zijn accu leegtrok viel het schrijven met hem weg -- terwijl de node zelf gewoon
bereikbaar bleef en de poller zijn instellingen nog elke ronde ophaalde. De
pagina zei "de doorstuurder is hier zelf niet bekend", en dat klopte, maar het
was niet het hele verhaal.

Wat hier vastligt:

1. Geen poller ooit gezien = geen kandidaat. Anders staat op elke nodepagina een
   afgevallen weg die deze installatie niet gebruikt -- dezelfde regel als bij
   de eigen API van een node.
2. De wachtrij staat ACHTERAAN. Hij is de traagste en de enige zonder
   teruglezing in hetzelfde verzoek.
3. Schrijven zet TWEE opdrachten klaar: de set en de teruglezing, zodat de
   poller ze in EEN sessie doet.
4. ``applied`` blijft leeg. Er is niets teruggelezen op het moment dat deze
   functie antwoordt, en een gevraagde waarde als gemeten waarde vastleggen is
   precies de onwaarheid waar nodeconfig tegen gebouwd is.
"""
import pytest

from app import nodeconfig


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


def _repeater_zonder_ip(db):
    """Een stock repeater zoals JessaZH: geen eigen API, geen IP-pad, en zijn
    cijfers komen binnen via de HTTP-ingest van de poller."""
    rep = db.get_or_create_repeater("cb80b5fec849", "BE-HSS-JessaZH.URS")
    db.execute("UPDATE repeaters SET source_prefix='api', ota_host=NULL, "
               "sensor_host=NULL, sensor_seen=NULL WHERE id=?", (rep["id"],))
    return db.qone("SELECT * FROM repeaters WHERE id=?", (rep["id"],))


def test_zonder_poller_geen_kandidaat(db):
    rep = _repeater_zonder_ip(db)
    route = nodeconfig.cfg_route(rep, broker_connected=False)
    assert "poller" not in [k["transport"] for k in route["options"]]


def test_verse_poller_maakt_de_weg_open(db):
    rep = _repeater_zonder_ip(db)
    db.note_poller_seen("meshuptime", caps=["settings", "refresh"])
    route = nodeconfig.cfg_route(rep, broker_connected=False)
    assert route["can"] is True
    assert route["transport"] == "poller"
    # Achteraan: de traagste weg, en de enige zonder teruglezing in hetzelfde
    # verzoek.
    assert route["options"][-1]["transport"] == "poller"


def test_poller_die_geen_instellingen_doet_is_geen_weg(db):
    rep = _repeater_zonder_ip(db)
    db.note_poller_seen("iets-anders", caps=["refresh"])
    route = nodeconfig.cfg_route(rep, broker_connected=False)
    assert route["can"] is False
    poller = [k for k in route["options"] if k["transport"] == "poller"][0]
    assert poller["blocker"] == "poller_no_settings"


def test_de_drempels_gelden_onverkort_op_deze_weg(db):
    """Zelfde ingang, dus zelfde bevestiging: `tx` kan een node onbereikbaar
    maken en vraagt daarom dat je zijn naam overtypt -- ook als het commando via
    de wachtrij gaat."""
    rep = _repeater_zonder_ip(db)
    db.note_poller_seen("meshuptime", caps=["settings"])
    uit = nodeconfig.write(rep, "tx", "20")
    assert uit["ok"] is False
    assert uit["step"] == "bevestiging"
    assert db.pop_settings_requests() == []


def test_schrijven_zet_de_set_en_de_teruglezing_klaar(db):
    rep = _repeater_zonder_ip(db)
    db.note_poller_seen("meshuptime", caps=["settings"])
    uit = nodeconfig.write(rep, "tx", "20", confirm=rep["name"])
    assert uit["ok"] is True
    assert uit["transport"] == "poller"
    # De wachtrij draagt allebei, en in deze volgorde: eerst zetten, dan lezen.
    wachtrij = db.pop_settings_requests()
    assert len(wachtrij) == 1
    # Eerst zetten, dan lezen -- de poller voert de lijst in volgorde uit binnen
    # dezelfde login.
    assert wachtrij[0]["params"] == ["cmd:set tx 20", "tx"]


def test_er_wordt_niets_teruggelezen_en_dus_niets_onthouden(db):
    rep = _repeater_zonder_ip(db)
    db.note_poller_seen("meshuptime", caps=["settings"])
    uit = nodeconfig.write(rep, "tx", "20", confirm=rep["name"])
    assert uit["applied"] == ""
    assert uit["exact"] is False
    # Niet in de instellingentabel: die hoort te tonen wat er in de node staat,
    # en dat weet nog niemand.
    assert [r["param"] for r in db.cli_settings_for(rep["id"])] == []
