"""Het kanaalfilter van een stock-repeater: lezen, zetten, en wat we niet weten.

Deze regel is anders dan de andere filterregels, en op drie manieren:

1. Hij draagt een VRIJE NAAM. De firmware leest daar precies één woord, dus een
   naam met een spatie zou stilletjes op het eerste woord geblokkeerd worden --
   en dan blokkeert de repeater een ander kanaal dan je bedoelde.
2. Hij heeft geen aan/uit. De lijst IS de stand; verwijderen is het uitzetten.
3. En het venijnigste: op een LEGE lijst antwoordt deze firmware helemaal niet
   (``handleCommand`` zet ``reply_len`` op 0 en er gaat geen pakket de lucht in).
   'Niets geblokkeerd' en 'geen antwoord' komen dus als hetzelfde aan, en dat
   verschil mogen we niet wegpoetsen: ``parse_filter_channels`` geeft daarom
   None bij stilte en nooit een lege lijst.
"""
import pytest

from app import commanding, pfstock, pktfilter


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


# --- de lijst lezen -----------------------------------------------------------

@pytest.mark.parametrize("tekst", [
    None, "", "   ",
    "> Filter: syntax error 'filter channel [list | add | remove] <#name | Public>'",
    "> filter channel [list | add | remove] <#name | Public>",
    "> Filter: command error",
])
def test_stilte_en_fouten_leveren_geen_lege_lijst(tekst):
    """None en niet ``[]``: een lege lijst zou beweren dat er niets geblokkeerd
    is, en dat weten we bij stilte juist niet."""
    assert pfstock.parse_filter_channels(tekst) is None


def test_naam_met_hash_tussen_haakjes():
    # De firmwarestring ``%s (%s)`` staat pal naast de kanaalcode.
    uit = pfstock.parse_filter_channels("dutch (a3) Public (7f)")
    assert uit == [{"label": "dutch", "hash": "a3"},
                   {"label": "Public", "hash": "7f"}]


def test_losse_namen_zonder_hash():
    uit = pfstock.parse_filter_channels("#dutch, #test")
    assert [c["label"] for c in uit] == ["#dutch", "#test"]
    assert all(c["hash"] == "" for c in uit)


def test_dezelfde_naam_komt_er_een_keer_in():
    uit = pfstock.parse_filter_channels("dutch (a3) dutch (a3)")
    assert len(uit) == 1


def test_ruiswoorden_worden_geen_kanaal():
    """Het antwoord kan de echo van het commando dragen; 'channel' en 'list'
    zijn geen kanaalnamen."""
    uit = pfstock.parse_filter_channels("filter channel list #dutch")
    assert [c["label"] for c in uit] == ["#dutch"]


def test_een_absurd_lange_naam_valt_af():
    assert pfstock.parse_filter_channels("#" + "a" * 60) is None


# --- de brug naar de opslag ---------------------------------------------------

def test_de_lijst_landt_in_de_filterstand(db):
    rid = db.get_or_create_repeater("e3d3f4d7edd0", "BE-HSS-JessaZH")["id"]
    assert pfstock.apply_cli_filter(
        rid, {"cmd:filter channel list": "dutch (a3)"}, "cli") is True
    assert db.filter_state_for(rid)["channels"] == [{"label": "dutch", "hash": "a3"}]


def test_stilte_op_de_lijst_wist_de_bekende_kanalen_niet(db):
    """Een sweep waarin de kanaalvraag geen antwoord kreeg, mag de lijst van
    gisteren niet weggooien -- dat zou 'niets geblokkeerd' beweren."""
    rid = db.get_or_create_repeater("e3d3f4d7edd0", "BE-HSS-JessaZH")["id"]
    pfstock.apply_cli_filter(rid, {"cmd:filter channel list": "dutch (a3)"}, "cli")
    pfstock.apply_cli_filter(rid, {"cmd:filter channel list": None,
                                   "cmd:filter": "> Filter on: Blocked [ Hops: 0 ]"}, "cli")
    assert db.filter_state_for(rid)["channels"] == [{"label": "dutch", "hash": "a3"}]


# --- de schrijfweg ------------------------------------------------------------

def rep(**over):
    row = {"id": 7, "name": "BE-HSS-JessaZH", "pubkey_prefix": "e3d3f4d7edd0",
           "fw": "v1.17.1-PS+filter+rollback", "fw_meshmanager": "",
           "source_prefix": "48d7aade232b", "ota_host": ""}
    row.update(over)
    return row


@pytest.fixture(autouse=True)
def _verse_poller(monkeypatch):
    monkeypatch.setattr(commanding, "describe",
                        lambda r: {"poller": True, "poller_name": "node-push-token"})


def test_een_kanaal_blokkeren_gaat_de_wachtrij_in(db):
    uit = pktfilter.queue_write(rep(), "channel add #dutch", confirm="ja")
    assert uit["ok"] is True
    wachtrij = db.pop_settings_requests()
    assert wachtrij[0]["params"][0] == "cmd:filter channel add #dutch"


def test_weer_doorlaten_vraagt_geen_bevestiging(db):
    """De weg terug is de goedkoopste handeling -- dezelfde regel als bij
    ``filter off``."""
    uit = pktfilter.queue_write(rep(), "channel remove #dutch")
    assert uit["ok"] is True
    assert db.pop_settings_requests()[0]["params"][0] == "cmd:filter channel remove #dutch"


def test_een_naam_met_een_spatie_wordt_hier_geweigerd(db):
    """Niet daar. De firmware zou 'mijn' blokkeren en 'kanaal' negeren, en dan
    staat er een regel die iemand anders raakt dan bedoeld."""
    uit = pktfilter.queue_write(rep(), "channel add mijn kanaal", confirm="ja")
    assert uit["ok"] is False and uit["step"] == "commando"
    assert "spaties" in uit["msg"]
    assert db.pop_settings_requests() == []


@pytest.mark.parametrize("naam", ["-begint-fout", "met/slash", "#", "'; drop", "a" * 40])
def test_een_naam_die_deze_firmware_niet_aanvaardt(db, naam):
    uit = pktfilter.queue_write(rep(), "channel add " + naam, confirm="ja")
    assert uit["ok"] is False and uit["step"] == "commando"
    assert db.pop_settings_requests() == []


def test_alleen_add_remove_en_list(db):
    uit = pktfilter.queue_write(rep(), "channel wisdit", confirm="ja")
    assert uit["ok"] is False and "add, remove of list" in uit["msg"]
    assert db.pop_settings_requests() == []


def test_de_lijst_opvragen_neemt_geen_naam(db):
    assert pktfilter.queue_write(rep(), "channel list #dutch")["ok"] is False
    uit = pktfilter.queue_write(rep(), "channel list")
    assert uit["ok"] is True
    assert db.pop_settings_requests()[0]["params"][0] == "cmd:filter channel list"
