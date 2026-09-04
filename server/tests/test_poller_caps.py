"""Wat een poller zegt te kunnen, en wat de site daarmee belooft.

De opdrachtwachtrij draagt twee soorten verzoeken: instellingenopvragingen en
statusverzoeken. De MeshUptime-node voert de eerste uit en laat de tweede vallen
(ander protocol). Zonder deze opgave bood de beheerpagina een knop "status
opvragen" aan die een verzoek in een wachtrij legde dat gegarandeerd weggegooid
werd -- precies de belofte die ``commanding`` moest wegwerken.

De regel die dit bestand bewaakt: WIE ZWIJGT KAN ALLES. Een poller van vóór dit
veld (de Home Assistant-integratie) mag door een nieuwe kolom niet stilletjes de
helft van zijn werk verliezen.
"""
import pytest

from app import auth, commanding, routes_api, sensorpush


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


def _poll(db, caps=None):
    token = auth.create_token("test-poller")
    routes_api.commands(caps=caps, authorization="Bearer " + token)


def test_zonder_opgave_blijft_alles_mogelijk(db):
    _poll(db)
    assert db.poller_last_caps() is None
    assert set(commanding.DEFAULT_POLLER_CAPS) == {"settings", "refresh"}


def test_opgave_wordt_bewaard(db):
    _poll(db, "settings")
    assert db.poller_last_caps() == ["settings"]
    assert db.poller_last_name() == "test-poller"


def test_twee_soorten_in_een_opgave(db):
    _poll(db, "settings,refresh")
    assert db.poller_last_caps() == ["refresh", "settings"]


def test_onbekende_namen_worden_niet_bewaard(db):
    """Dit veld komt uit een querystring. Wat we niet kennen slaan we niet op --
    anders bewaart de instelling rommel die niemand meer kan duiden."""
    _poll(db, "settings,rm -rf,<script>,refresh ")
    assert db.poller_last_caps() == ["refresh", "settings"]


def test_lege_opgave_is_een_antwoord_en_geen_stilte(db):
    """``?caps=`` betekent 'ik doe niets van de wachtrij', en dat is iets anders
    dan niets zeggen."""
    _poll(db, "")
    assert db.poller_last_caps() == []


def test_een_zwijgende_poller_wist_de_vorige_opgave_niet(db):
    _poll(db, "settings")
    _poll(db)
    assert db.poller_last_caps() == ["settings"]


def test_het_pushtoken_mag_ook_zijn_kunnen_melden(db, monkeypatch):
    monkeypatch.setattr(sensorpush, "TOKEN", "vloot-geheim")
    routes_api.commands(caps="settings", authorization="Bearer vloot-geheim")
    assert db.poller_last_name() == "node-push-token"
    assert db.poller_last_caps() == ["settings"]
