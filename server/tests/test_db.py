"""Tests voor opslag, kolommigraties en de backfill in app/db.py.

Alles draait tegen een tijdelijke SQLite-file; de raw-hex komt uit dezelfde
zelfgemaakte frames als in test_packets.py.
"""
import sqlite3

import pytest

import frames
from app import config, packets


@pytest.fixture
def db(tmp_path, monkeypatch):
    """De db-module tegen een verse, tijdelijke database.

    De module houdt een verbinding op moduleniveau vast; die moet per test
    weggegooid en na afloop gesloten worden, anders lekken tests in elkaar en
    kan Windows de tijdelijke file niet opruimen.
    """
    from app import db as db_module
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "test.sqlite3")
    db_module._conn = None
    yield db_module
    if db_module._conn is not None:
        db_module._conn.close()
        db_module._conn = None


def _scoped_txt_msg() -> tuple[str, dict]:
    """Een gescoped TXT_MSG-frame als hex, plus zijn decodering."""
    raw = frames.frame(frames.ROUTE_TRANSPORT_FLOOD, frames.TYPE_TXT_MSG,
                       codes=(5, 7),
                       payload=frames.peer_payload(0xC3, 0xD4))
    return raw.hex(), packets.decode(raw)


def test_insert_bewaart_decoderkolommen(db):
    raw_hex, pkt = _scoped_txt_msg()
    row_id = db.insert_packet("aabbcc112233", pkt, snr=7.5, rssi=-95,
                              raw=raw_hex)
    assert row_id is not None
    row = db.qone("SELECT * FROM packets WHERE id=?", (row_id,))
    assert row["scope"] == "scoped"
    assert row["scope_codes"] == "5,7"
    assert row["src_hash"] == "d4"
    assert row["dest_hash"] == "c3"
    assert row["raw"] == raw_hex


def test_backfill_herstelt_geleegde_kolommen(db):
    # Het raw-frame is het enige volledige verslag van een pakket; alles
    # daarnaast is een samenvatting die de backfill eruit moet kunnen
    # herafleiden. Kolommen leegmaken en backfillen simuleert een database
    # van voor het bestaan van die kolommen.
    raw_hex, pkt = _scoped_txt_msg()
    row_id = db.insert_packet("aabbcc112233", pkt, raw=raw_hex)
    db.execute("UPDATE packets SET scope=NULL, scope_codes=NULL, "
               "src_hash=NULL, dest_hash=NULL WHERE id=?", (row_id,))

    conn = db.get_conn()
    db._backfill_from_raw(conn)
    conn.commit()

    row = db.qone("SELECT * FROM packets WHERE id=?", (row_id,))
    assert row["scope"] == "scoped"
    assert row["scope_codes"] == "5,7"
    assert row["src_hash"] == "d4"
    assert row["dest_hash"] == "c3"


def test_backfill_laat_rijen_zonder_raw_met_rust(db):
    # Een rij van voor de raw-kolom heeft niets om uit terug te lezen; NULL
    # laten staan is dan het eerlijke antwoord.
    db.execute("INSERT INTO packets(ts, observer, payload_name) "
               "VALUES(?,?,?)", (db.utcnow(), "aabbcc112233", "ACK"))
    conn = db.get_conn()
    db._backfill_from_raw(conn)
    conn.commit()
    row = db.qone("SELECT scope, src_hash FROM packets")
    assert row["scope"] is None
    assert row["src_hash"] is None


def test_backfill_is_zelfbegrenzend_via_lege_string(db):
    # Een ACK draagt geen src_hash. De backfill schrijft dan een lege string
    # als wachtpost: zonder die zou elke start opnieuw alle ACK's decoderen,
    # zoekend naar een hash die er nooit was.
    raw = frames.frame(frames.ROUTE_FLOOD, frames.TYPE_ACK,
                       payload=b"\xde\xad\xbe\xef")
    row_id = db.insert_packet("aabbcc112233", packets.decode(raw),
                              raw=raw.hex())
    db.execute("UPDATE packets SET scope=NULL, src_hash=NULL, dest_hash=NULL "
               "WHERE id=?", (row_id,))

    conn = db.get_conn()
    db._backfill_from_raw(conn)
    conn.commit()

    row = db.qone("SELECT * FROM packets WHERE id=?", (row_id,))
    assert row["scope"] == "unscoped"
    assert row["src_hash"] == ""
    assert row["dest_hash"] == ""
    # En daarmee vindt een tweede run niets meer te doen.
    resterend = conn.execute(
        "SELECT COUNT(*) FROM packets "
        "WHERE (scope IS NULL OR src_hash IS NULL) AND raw IS NOT NULL"
    ).fetchone()[0]
    assert resterend == 0


# De packets-tabel zoals hij eruitzag voordat de latere kolommen bestonden;
# het startpunt van de migratietest.
_OUDE_PACKETS = """
CREATE TABLE packets(
  id INTEGER PRIMARY KEY,
  ts TEXT NOT NULL,
  observer TEXT NOT NULL,
  snr REAL,
  rssi REAL,
  len INTEGER,
  route TEXT,
  payload_type INTEGER,
  payload_name TEXT,
  path_len INTEGER,
  sender TEXT,
  phash TEXT
);
CREATE TABLE repeaters(id INTEGER PRIMARY KEY, slug TEXT, pubkey_prefix TEXT,
                       name TEXT, created_at TEXT);
CREATE TABLE contacts(prefix TEXT PRIMARY KEY, prefix6 TEXT, name TEXT,
                      lat REAL, lon REAL, node_type TEXT, updated TEXT);
"""


def test_kolommigraties_voegen_toe_zonder_dataverlies(tmp_path):
    from app import db as db_module

    pad = tmp_path / "oud.sqlite3"
    conn = sqlite3.connect(pad)
    conn.row_factory = sqlite3.Row
    try:
        conn.executescript(_OUDE_PACKETS)
        conn.execute("INSERT INTO packets(ts, observer, snr, payload_name) "
                     "VALUES('2026-01-01T00:00:00Z', 'aabbcc112233', 7.5, "
                     "'TXT_MSG')")
        conn.commit()

        db_module._migrate(conn)

        kolommen = {r["name"]
                    for r in conn.execute("PRAGMA table_info(packets)")}
        for _tabel, kolom, _decl in db_module.COLUMN_MIGRATIONS:
            if _tabel == "packets":
                assert kolom in kolommen
        # De bestaande rij is onaangeroerd: oude waarden intact, nieuwe
        # kolommen leeg.
        row = conn.execute("SELECT * FROM packets").fetchone()
        assert row["snr"] == 7.5
        assert row["payload_name"] == "TXT_MSG"
        assert row["scope"] is None
        assert row["src_hash"] is None

        # Nogmaals draaien mag niets doen en nergens over struikelen.
        db_module._migrate(conn)
    finally:
        conn.close()


def test_migratie_gevolgd_door_backfill_vult_oude_rijen(tmp_path):
    # Het echte upgradepad in een notendop: een oude database krijgt bij een
    # nieuwe start eerst de kolommen erbij en dan de backfill eroverheen, en
    # een rij die zijn raw-frame nog heeft komt daar compleet uit.
    from app import db as db_module

    raw_hex, _ = _scoped_txt_msg()
    pad = tmp_path / "oud.sqlite3"
    conn = sqlite3.connect(pad)
    conn.row_factory = sqlite3.Row
    try:
        conn.executescript(_OUDE_PACKETS)
        db_module._migrate(conn)
        conn.execute("INSERT INTO packets(ts, observer, raw) "
                     "VALUES('2026-01-01T00:00:00Z', 'aabbcc112233', ?)",
                     (raw_hex,))
        db_module._backfill_from_raw(conn)
        conn.commit()

        row = conn.execute("SELECT * FROM packets").fetchone()
        assert row["scope"] == "scoped"
        assert row["scope_codes"] == "5,7"
        assert row["src_hash"] == "d4"
    finally:
        conn.close()
