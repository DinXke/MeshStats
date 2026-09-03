"""Wie mag de pollerwachtrij bedienen, en hoe heet hij dan.

Twee sleutels openen ``/api/v1/commands`` en ``/api/v1/repeater_settings``: een
token uit de tokens-tabel (een losse poller, met een naam) en het vloot-pushtoken
waarmee onze nodes al hun metingen afleveren. Dat tweede is er omdat de
MeshUptime-node die de wachtrij leegmaakt hetzelfde toestel is dat op
``/api/sensorpush`` pusht -- zie ``routes_api.require_poller_token``.

Wat hier vastligt: welke naam het grootboek krijgt (dat is wat de beheerpagina
toont als "wie pollde"), dat het pushtoken NIET meetelt zolang de push-weg
uitstaat (leeg ``MM_PUSH_TOKEN``), en dat een ingetrokken token dicht blijft --
ook al lijkt het op niets anders.
"""
import pytest
from fastapi import HTTPException

from app import auth, routes_api, sensorpush


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


def test_beheertoken_geeft_zijn_naam(db):
    token = auth.create_token("heltec-node")
    assert routes_api.require_poller_token("Bearer " + token) == "heltec-node"


def test_pushtoken_telt_als_node(db, monkeypatch):
    monkeypatch.setattr(sensorpush, "TOKEN", "vloot-geheim")
    assert routes_api.require_poller_token("Bearer vloot-geheim") == "node-push-token"


def test_pushtoken_telt_niet_als_de_pushweg_uitstaat(db, monkeypatch):
    """Een leeg MM_PUSH_TOKEN betekent 'geen push-weg'. Dan mag een lege of
    toevallig gelijke bearer die weg niet alsnog openen."""
    monkeypatch.setattr(sensorpush, "TOKEN", "")
    with pytest.raises(HTTPException) as fout:
        routes_api.require_poller_token("Bearer ")
    assert fout.value.status_code in (401, 403)


def test_ingetrokken_token_blijft_dicht(db, monkeypatch):
    monkeypatch.setattr(sensorpush, "TOKEN", "vloot-geheim")
    token = auth.create_token("oude-poller")
    rij = db.qone("SELECT id FROM tokens WHERE name=?", ("oude-poller",))
    db.execute("UPDATE tokens SET revoked=1 WHERE id=?", (rij["id"],))
    with pytest.raises(HTTPException) as fout:
        routes_api.require_poller_token("Bearer " + token)
    assert fout.value.status_code == 403


def test_zonder_bearer_401(db):
    with pytest.raises(HTTPException) as fout:
        routes_api.require_poller_token(None)
    assert fout.value.status_code == 401
    with pytest.raises(HTTPException) as fout:
        routes_api.require_poller_token("Basic abc")
    assert fout.value.status_code == 401
