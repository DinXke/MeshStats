"""Eén vorm voor de sleutel van een node, en het opruimen van wat dat niet was.

Dit is dezelfde fout als de hoofdletters in de MQTT-topics, in een andere
gedaante: één identiteit die op twee plaatsen een andere vorm heeft. Daar was het
antwoord onthouden in plaats van berekenen; hier is het één functie in plaats van
elke nieuwe weg zijn eigen variant.

Drie gevolgen kwamen uit die ene oorzaak, en de tests hieronder dekken ze alle
drie: geen naamtreffer (de hex belandde in het naamveld), geen uitvraging (de
monitor adresseert op de korte vorm) en niets van wat de site over de node wist
(bron, firmware, laatst gezien -- alles hangt aan de sleutel).
"""
import pytest

from app import db as db_module


@pytest.fixture
def db(tmp_path, monkeypatch):
    from app import config
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "test.sqlite3")
    db_module._conn = None
    yield db_module
    if db_module._conn is not None:
        db_module._conn.close()
        db_module._conn = None


VOL = "c3f275442697c3aeae75c350beb3ec5a92ac4d3b421ad5a40c7bb2436f3812bd"
KORT = "c3f275442697"


# --- de ene vorm ---------------------------------------------------------------

def test_node_key_kort_in_waar_key_prefix_alleen_keurt(db):
    """Het verschil dat de bug was. key_prefix keurt een voorvoegsel zoals het
    ergens langskomt -- in een topic, in een payload -- en daar zijn langere
    vormen legitiem, dus het kort niet in. node_key levert de IDENTITEIT van een
    rij, en die heeft één lengte."""
    assert db.node_key(VOL) == KORT
    assert db.key_prefix(VOL) == VOL          # keurt goed, kort niet in
    assert db.node_key(VOL.upper()) == KORT   # en normaliseert de kast


@pytest.mark.parametrize("waarde", ["", "zz", "0x1234", None])
def test_wat_geen_sleutel_is_wordt_leeg(db, waarde):
    assert db.node_key(waarde) == ""


def test_een_al_korte_sleutel_blijft_zichzelf(db):
    assert db.node_key("e3d3f4d7edd0") == "e3d3f4d7edd0"


def test_contacts_gebruikt_dezelfde_lengte(db):
    """Daar stond het getal 12 los in de code; nu komt het uit dezelfde bron."""
    db.upsert_advert(VOL, name="BE-SLK-HSBRIXEL")
    rij = db.qone("SELECT prefix, prefix6 FROM contacts")
    assert len(rij["prefix"]) == db.NODE_KEY_HEX
    assert rij["prefix6"] == KORT[:6]


# --- de naam ------------------------------------------------------------------

def test_de_naam_komt_uit_contacts(db):
    """De namen waren wél bekend -- ze stonden in deze tabel -- en het is niet aan
    een nieuwe weg om zijn eigen naamloosheid mee te brengen."""
    db.upsert_advert(VOL, name="BE-SLK-HSBRIXEL")
    assert db.contact_name_for(VOL) == "BE-SLK-HSBRIXEL"
    assert db.contact_name_for(KORT) == "BE-SLK-HSBRIXEL"


def test_zonder_treffer_geen_verzonnen_naam(db):
    assert db.contact_name_for("aabbccddeeff") == ""


# --- de standaard --------------------------------------------------------------

def test_een_opgehaalde_node_komt_verborgen_binnen(db):
    """Het gaat per definitie om de node van iemand anders."""
    db.mark_guest_polled(KORT, True)
    rij = db.qone("SELECT is_public, is_guest_polled FROM repeaters")
    assert rij["is_public"] == 0
    assert rij["is_guest_polled"] == 1


def test_ook_via_de_gewone_ingestweg_verborgen(db):
    db.get_or_create_repeater("aabbccddeeff", "Nieuw")
    assert db.qone("SELECT is_public FROM repeaters")["is_public"] == 0


# --- het opruimen van bestaande rijen -----------------------------------------

def _lange_rij(db, key=VOL, publiek=1, gast=1, naam=None):
    db.execute("INSERT INTO repeaters(slug, pubkey_prefix, name, created_at, "
               "is_public, is_guest_polled) VALUES(?,?,?,?,?,?)",
               (key[:20], key, naam or key, db.utcnow(), publiek, gast))


def test_een_lange_sleutel_wordt_ingekort(db):
    _lange_rij(db)
    db._shorten_long_node_keys(db.get_conn())
    rij = db.qone("SELECT pubkey_prefix FROM repeaters")
    assert rij["pubkey_prefix"] == KORT


def test_bij_het_inkorten_komt_de_naam_uit_contacts(db):
    db.upsert_advert(VOL, name="BE-SLK-HSBRIXEL")
    _lange_rij(db)
    db._shorten_long_node_keys(db.get_conn())
    assert db.qone("SELECT name FROM repeaters")["name"] == "BE-SLK-HSBRIXEL"


def test_een_zelf_getypte_naam_blijft_staan(db):
    """Alleen een naam die de sleutel zelf was, wordt vervangen."""
    db.upsert_advert(VOL, name="BE-SLK-HSBRIXEL")
    _lange_rij(db, naam="Zelf verzonnen")
    db._shorten_long_node_keys(db.get_conn())
    assert db.qone("SELECT name FROM repeaters")["name"] == "Zelf verzonnen"


def test_een_opgehaalde_node_gaat_bij_het_opruimen_terug_naar_verborgen(db):
    """Met een sleutel van 64 tekens kon niemand zien wát hij publiek zette."""
    _lange_rij(db, publiek=1, gast=1)
    db._shorten_long_node_keys(db.get_conn())
    assert db.qone("SELECT is_public FROM repeaters")["is_public"] == 0


def test_een_eigen_repeater_blijft_publiek(db):
    """Alleen wat wij bij iemand anders zijn gaan ophalen wordt verborgen; een
    eigen repeater met een lange sleutel is een andere zaak."""
    _lange_rij(db, publiek=1, gast=0)
    db._shorten_long_node_keys(db.get_conn())
    assert db.qone("SELECT is_public FROM repeaters")["is_public"] == 1


def test_een_botsing_gooit_de_lange_rij_weg_en_houdt_de_korte(db):
    """De korte rij is de oudere: hij wordt elders aangewezen en draagt wat een
    beheerder erover besloten heeft. De lange draagt niets dat de korte niet ook
    kan krijgen -- behalve de herkomst, en die verhuist mee."""
    kort_id = db.get_or_create_repeater(KORT, "BE-SLK-HSBRIXEL")["id"]
    _lange_rij(db, gast=1)
    db._shorten_long_node_keys(db.get_conn())

    rijen = db.q("SELECT id, pubkey_prefix, name, is_guest_polled FROM repeaters")
    assert len(rijen) == 1
    assert rijen[0]["id"] == kort_id
    assert rijen[0]["name"] == "BE-SLK-HSBRIXEL"      # de naam van de korte blijft
    assert rijen[0]["is_guest_polled"] == 1            # de herkomst verhuist mee


def test_opruimen_is_herhaalbaar(db):
    _lange_rij(db)
    conn = db.get_conn()
    db._shorten_long_node_keys(conn)
    db._shorten_long_node_keys(conn)
    assert len(db.q("SELECT 1 FROM repeaters")) == 1


def test_een_onbruikbare_lange_sleutel_wordt_niet_aangeraakt(db):
    """Geen hex: dan is er niets om naar in te korten, en weggooien is niet aan
    deze migratie."""
    db.execute("INSERT INTO repeaters(slug, pubkey_prefix, name, created_at) "
               "VALUES('x','nietseensgeldigehexmaarwellang','x',?)", (db.utcnow(),))
    db._shorten_long_node_keys(db.get_conn())
    assert db.qone("SELECT pubkey_prefix FROM repeaters")["pubkey_prefix"] \
        == "nietseensgeldigehexmaarwellang"
