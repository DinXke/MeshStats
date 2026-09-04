"""Het filter van een stock-repeater met filterpatch zetten via de pollerwachtrij.

De IP-weg (``pktfilter.write``) bestaat voor onze eigen firmware. JessaZH draait
de stock-firmware met filterpatch, wordt doorgestuurd en heeft geen IP-pad; voor
hem is de weg de wachtrij die de MeshUptime-node leegmaakt. Wat hier vastligt:

1. De weg is alleen open als alle drie de voorwaarden gelden: doorgestuurd,
   filterpatch, verse poller. Elke ontbrekende voorwaarde heeft zijn eigen naam.
2. Een wijziging gaat de wachtrij in MET het teruglezen erachter, in die
   volgorde, zodat de nieuwe stand in dezelfde LoRa-sessie terugkomt.
3. De bevestiging en de syntaxis van die firmware worden op de server getoetst:
   geen `type`-regel (bestaat daar niet), geen `hash 2` zonder `ja`.
"""
import pytest

from app import commanding, pktfilter


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
        "id": 7, "name": "BE-HSS-JessaZH", "pubkey_prefix": "e3d3f4d7edd0",
        "fw": "v1.17.1-PS+filter+rollback", "fw_meshmanager": "",
        # Doorgestuurd door de MeshUptime-node: een andere sleutel dan de eigen.
        "source_prefix": "48d7aade232b", "ota_host": "",
    }
    row.update(overrides)
    return row


def _poller(monkeypatch, aan: bool, naam="node-push-token"):
    monkeypatch.setattr(commanding, "describe",
                        lambda r: {"poller": aan, "poller_name": naam if aan else None})


def test_weg_open_met_verse_poller(monkeypatch):
    _poller(monkeypatch, True)
    route = pktfilter.queue_route(rep())
    assert route["can"] is True
    assert route["poller_name"] == "node-push-token"
    assert route["variant"] == "meshcore_filter"


def test_weg_dicht_zonder_poller(monkeypatch):
    _poller(monkeypatch, False)
    route = pktfilter.queue_route(rep())
    assert route["can"] is False and route["blocker"] == "no_poller"


def test_weg_dicht_zonder_filterpatch(monkeypatch):
    _poller(monkeypatch, True)
    route = pktfilter.queue_route(rep(fw="v1.17.1"))
    assert route["can"] is False and route["blocker"] == "no_filter_patch"


def test_weg_dicht_als_de_node_niet_doorgestuurd_wordt(monkeypatch):
    """Een node die zichzelf publiceert heeft een betere weg dan de wachtrij."""
    _poller(monkeypatch, True)
    route = pktfilter.queue_route(rep(source_prefix="e3d3f4d7edd0"))
    assert route["can"] is False and route["blocker"] == "not_relayed"


def test_wijziging_gaat_met_teruglezen_de_wachtrij_in(db, monkeypatch):
    _poller(monkeypatch, True)
    uit = pktfilter.queue_write(rep(), "hash 2", confirm="ja")
    assert uit["ok"] is True and uit["step"] == "queued" and uit["queued"] is True
    assert "node-push-token" in uit["msg"]
    wachtrij = db.pop_settings_requests()
    assert len(wachtrij) == 1
    assert wachtrij[0]["prefix"] == "e3d3f4d7edd0"
    assert wachtrij[0]["params"] == ["cmd:filter hash 2", "cmd:filter", "cmd:filter count"]


def test_de_weg_terug_vraagt_geen_bevestiging(db, monkeypatch):
    _poller(monkeypatch, True)
    uit = pktfilter.queue_write(rep(), "off")
    assert uit["ok"] is True
    assert db.pop_settings_requests()[0]["params"][0] == "cmd:filter off"


def test_zonder_bevestiging_niets_in_de_wachtrij(db, monkeypatch):
    _poller(monkeypatch, True)
    uit = pktfilter.queue_write(rep(), "hash 2")
    assert uit["ok"] is False and uit["step"] == "bevestiging"
    assert db.pop_settings_requests() == []


def test_type_regel_bestaat_niet_op_deze_firmware(db, monkeypatch):
    """`filter type` kent de stock-variant niet; het zou een LoRa-sessie kosten
    die met 'command error' eindigt. Op de server weigeren, niet in de lucht."""
    _poller(monkeypatch, True)
    uit = pktfilter.queue_write(rep(), "type 05 off", confirm="BE-HSS-JessaZH")
    assert uit["ok"] is False and uit["step"] == "commando"
    assert "type" in uit["msg"]
    assert db.pop_settings_requests() == []


def test_zonder_poller_gaat_er_niets_de_wachtrij_in(db, monkeypatch):
    _poller(monkeypatch, False)
    uit = pktfilter.queue_write(rep(), "off")
    assert uit["ok"] is False and uit["step"] == "route" and "no_poller" in uit["msg"]
    assert db.pop_settings_requests() == []
