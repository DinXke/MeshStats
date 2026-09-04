"""De brug van een `cmd:filter count`-antwoord naar filterstand en metrics.

``pfstock.parse_filter_count`` heeft zijn eigen tests; dit bestand gaat over wat
er daarna gebeurt. Want dat was de klacht -- "ik zie geen verschil" -- en die lag
niet aan de parser: het antwoord landde als tekst in ``repeater_cli`` en verder
niets. De brug ``apply_cli_filter`` moet dat antwoord op precies dezelfde plek
laten landen als een node die zijn filterstand zelf meepubliceert (de MQTT-weg),
zodat de rest van de site niet kan zien langs welke weg het kwam.

Drie dingen liggen hier vast:

1. Een sweep ZONDER filterantwoord doet niets -- ook geen lege stand, want
   'nooit iets gemeld' is een andere toestand dan 'meldt dat er niets aanstaat'.
2. Een echt antwoord vult de blob (``filter_state_for``) EN de metrics
   (``latest_for``) onder dezelfde namen als de MQTT-weg.
3. Wat de stock-variant niet meldt, ONTBREEKT. Geen ``filter_passed`` van nul:
   een verzonnen nul ziet er in een grafiek precies uit als een meting.
"""
import pytest

from app import pfstock


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


# Het antwoord zoals de gepatchte stock-repeater het over de CLI teruggeeft:
# hoofdschakelaar + weggegooide aantallen, dan de limiettabel per pakkettype.
ANTWOORD = (
    "> Filter off: Blocked [ Hops: 4 | Rate: 0 | Channel: 0 | Hash: 1 | Malformed: 2 ]\n"
    "[TYPE: HOPS,RATE]\n"
    "00: 0,0\n"
    "01: 3,20\n"
)


def _rep(db):
    return db.get_or_create_repeater("e3d3f4d7edd0", "BE-HSS-JessaZH")["id"]


def test_sweep_zonder_filterantwoord_doet_niets(db):
    rid = _rep(db)
    values = {"name": "BE-HSS-JessaZH", "radio": "869.618,250,11,5", "cmd:clock": "13:37"}
    assert pfstock.apply_cli_filter(rid, values, source="cli") is False
    # Geen lege stand achterlaten: None blijft None.
    assert db.filter_state_for(rid) is None


def test_geen_filtertekst_in_het_antwoord_is_geen_filterstand(db):
    """Een node die het commando niet kent, antwoordt met een foutregel. Die
    mag geen half gevulde blob worden -- zonder ``Filter on/off:`` weten we niet
    eens of we naar het goede antwoord kijken."""
    rid = _rep(db)
    values = {"cmd:filter count": "Unknown command: filter"}
    assert pfstock.apply_cli_filter(rid, values, source="cli") is False
    assert db.filter_state_for(rid) is None


def test_filterantwoord_landt_als_stand_en_als_metrics(db):
    rid = _rep(db)
    assert pfstock.apply_cli_filter(rid, {"cmd:filter count": ANTWOORD}, source="cli") is True

    stand = db.filter_state_for(rid)
    assert stand is not None
    assert stand["on"] is False
    assert stand["variant"] == "meshcore_filter"
    assert stand["drop"] == {"hops": 4, "rate": 0, "kanaal": 0, "hash": 1, "misvormd": 2}
    # De limiettabel is configuratie, geen teller: apart, en 0,0 blijft staan.
    assert stand["limits"]["REQ"] == {"hops": 0, "rate": 0}
    assert stand["limits"]["RESPONSE"] == {"hops": 3, "rate": 20}

    # Dezelfde metricnamen als de MQTT-weg (mqtt_ingest.FILTER_DROP_METRICS),
    # zodat tegels en grafieken er zonder tweede codepad iets van maken.
    namen = set(db.latest_for(rid))
    assert {"filter_drop_hops", "filter_drop_hash", "filter_drop_malformed",
            "filter_dropped", "filter_on"} <= namen


def test_stock_variant_verzint_geen_passed_of_exempt(db):
    """ONTBREKEND IS NIET NUL: de stock-variant meldt geen doorgelaten of
    vrijgestelde pakketten, dus die reeksen mogen niet ontstaan."""
    rid = _rep(db)
    pfstock.apply_cli_filter(rid, {"cmd:filter count": ANTWOORD}, source="cli")
    stand = db.filter_state_for(rid)
    assert "passed" not in stand and "exempt" not in stand
    namen = set(db.latest_for(rid))
    assert "filter_passed" not in namen and "filter_exempt" not in namen
    # En geen dropteller per TYPE: de patch telt per reden, niet in een kruistabel.
    assert "type" not in stand["drop"]
    assert "filter_drop_type" not in namen


def test_sleutel_wordt_ongeacht_hoofdletters_herkend(db):
    """De paramnaam komt uit een wachtrij die mensen én firmware vullen."""
    rid = _rep(db)
    assert pfstock.apply_cli_filter(rid, {"CMD:Filter count": ANTWOORD}) is True
    assert db.filter_state_for(rid)["drop"]["hash"] == 1


# --- twee antwoorden, één stand (zoals JessaZH ze werkelijk geeft) -------------

JESSA_TABEL = ("[TYPE: HOPS,RATE] 00: 0,0 01: 0,0 02: 0,0 03: 0,0 04: 0,0 05: 0,0 "
               "06: 0,0 07: 0,0 08: 0,0 09: 0,0 10: 0,0 11: 0,0")
JESSA_STATUS = "> Filter off: Blocked [ Hops: 3 | Rate: 0 | Channel: 0 | Hash: 1 | Malformed: 0 ]"


def test_tabel_en_status_in_twee_pushes_worden_een_stand(db):
    """`filter count` en `filter` komen als aparte pushes; geen van beide mag de
    ander wissen, en alleen de statusregel levert een meetpunt."""
    rid = _rep(db)
    assert pfstock.apply_cli_filter(rid, {"cmd:filter count": JESSA_TABEL}, "cli") is True
    stand = db.filter_state_for(rid)
    assert "on" not in stand and len(stand["limits"]) == 11
    # Een tabel is configuratie, geen meting: geen filter_on/filter_dropped-punt.
    assert not {"filter_on", "filter_dropped"} & set(db.latest_for(rid))

    assert pfstock.apply_cli_filter(rid, {"cmd:filter": JESSA_STATUS}, "cli") is True
    stand = db.filter_state_for(rid)
    assert stand["on"] is False and stand["drop"]["hops"] == 3
    assert len(stand["limits"]) == 11            # de tabel van de vorige push staat er nog
    assert {"filter_on", "filter_dropped", "filter_drop_hops"} <= set(db.latest_for(rid))


def test_help_en_status_in_dezelfde_push(db):
    """Een volledige ronde levert ook `cmd:filter help`; die mag de stand niet
    storen en de statusregel ernaast moet gewoon landen."""
    rid = _rep(db)
    values = {"cmd:filter help": "> filter [ help | on | off | reset | types | count ]",
              "cmd:filter": JESSA_STATUS, "cmd:filter count": JESSA_TABEL}
    assert pfstock.apply_cli_filter(rid, values, "cli") is True
    stand = db.filter_state_for(rid)
    assert stand["on"] is False and stand["drop"]["hash"] == 1 and len(stand["limits"]) == 11
