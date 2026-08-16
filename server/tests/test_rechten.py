"""Het rechtenmodel, de migratie ernaartoe, en het audittrail.

Waarom dit bestand bestaat, en waarom het uitgebreider is dan de andere.

Toegang was tot nu toe alles-of-niets, en daar viel weinig aan stuk te gaan: je
kon inloggen of niet. Een rechtenmodel kan op drie manieren fout gaan die geen
van drieën een foutmelding oplevert:

1. **Te ruim.** Iemand mag iets wat hij niet zou mogen. Dat merk je pas als het
   gebeurd is, en bij firmware is "het is gebeurd" een node van een dak halen.
2. **Te krap, en dan vooral: de eigenaar buitengesloten.** De migratie draait op
   een bestaande databank waarin één beheerder alles mocht. Zet die kolom
   verkeerd, en de enige die de rechten kan herstellen kan niet meer inloggen.
   Dat is de duurste fout in dit bestand en hij heeft daarom de eerste sectie.
3. **Vergeten.** Een route die de controle mist. Die valt met geen enkele test
   over gedrag te vangen, want de route werkt -- hij werkt alleen voor iedereen.
   Daarom loopt de laatste sectie de router zelf af.
"""
import inspect
import sqlite3

import pytest

from app import config


@pytest.fixture
def db(tmp_path, monkeypatch):
    """De db-module tegen een verse, tijdelijke databank. Zelfde opzet als elders."""
    from app import db as db_module
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "test.sqlite3")
    db_module._conn = None
    yield db_module
    if db_module._conn is not None:
        db_module._conn.close()
        db_module._conn = None


def maak_node(db, naam="Dakrepeater", sleutel="e3d3f4d7edd0"):
    return db.get_or_create_repeater(sleutel, naam)


def maak_gebruiker(db, naam, superuser=False):
    from app import auth, rbac
    return rbac.maak_gebruiker(naam, auth.hash_password("wachtwoord123"),
                               is_superuser=superuser)


# --- 1. de migratie sluit niemand buiten -------------------------------------
#
# De harde eis. Een bestaande installatie heeft een ``admins``-tabel met twee
# kolommen en verder niets van dit model. Na de migratie hoort dezelfde login te
# werken, met dezelfde rechten als ervoor -- en die waren volledig.

_OUDE_ADMINS = """
CREATE TABLE admins(
  id INTEGER PRIMARY KEY,
  username TEXT UNIQUE NOT NULL,
  pw_hash TEXT NOT NULL
);
"""


def test_migratie_maakt_bestaande_beheerders_serverbeheerder(tmp_path):
    """De kolom komt erbij met DEFAULT 0; de bestaande rij hoort op 1 te staan.

    Dit is precies het geval waarin ALTER TABLE ADD COLUMN de verkeerde kant op
    faalt: hij vult bestaande rijen met de standaard, en die standaard is bewust
    'geen rechten'. POST_MIGRATIONS zet dat recht, één keer.
    """
    from app import db as db_module

    pad = tmp_path / "oud.sqlite3"
    conn = sqlite3.connect(pad)
    conn.row_factory = sqlite3.Row
    try:
        conn.executescript(_OUDE_ADMINS)
        conn.execute("INSERT INTO admins(username, pw_hash) VALUES('admin','pbkdf2$aa$bb')")
        conn.commit()
        db_module._migrate(conn)
        rij = conn.execute("SELECT * FROM admins WHERE username='admin'").fetchone()
        assert rij["is_superuser"] == 1
        assert rij["disabled"] == 0
        # En het wachtwoord is niet aangeraakt: de sessie-vingerafdruk hangt eraan,
        # dus een migratie die de hash herschrijft logt iedereen alsnog uit.
        assert rij["pw_hash"] == "pbkdf2$aa$bb"
    finally:
        conn.close()


def test_bestaande_databank_zonder_de_nieuwe_tabellen_werkt_na_migratie(tmp_path, monkeypatch):
    """Het echte upgradepad: oude databank, nieuwe code, dezelfde login.

    Niet alleen de kolom, maar de hele keten -- inloggen, de sessie lezen, en
    daarna nog iets mogen. Dat laatste is waar het om gaat: een account dat kan
    inloggen maar niets meer mag, is nog steeds buitengesloten.
    """
    from app import auth, config as cfg, rbac
    from app import db as db_module

    pad = tmp_path / "bestaand.sqlite3"
    conn = sqlite3.connect(pad)
    conn.executescript(_OUDE_ADMINS)
    conn.execute("INSERT INTO admins(username, pw_hash) VALUES(?,?)",
                 ("admin", auth.hash_password("hetoudewachtwoord")))
    conn.commit()
    conn.close()

    monkeypatch.setattr(cfg, "DB_PATH", pad)
    db_module._conn = None
    try:
        rij = db_module.qone("SELECT * FROM admins WHERE username='admin'")
        assert auth.verify_password("hetoudewachtwoord", rij["pw_hash"])
        # De sessie die daaruit volgt, wordt ook weer aanvaard.
        assert auth.read_session(auth.make_session("admin")) == "admin"
        # En dit account mag nog steeds alles, op een node die het nooit eerder zag.
        node = maak_node(db_module)
        for handeling in ("node.bekijken", "node.klok", "node.firmware",
                          "node.verwijderen"):
            assert rbac.decide("admin", handeling, node).allowed, handeling
        assert rbac.decide("admin", "server.gebruikers").allowed
    finally:
        if db_module._conn is not None:
            db_module._conn.close()
            db_module._conn = None


def test_een_nieuw_account_krijgt_niets_cadeau(db):
    """De andere kant van dezelfde migratie. De standaard is 0, en dat hoort te gelden
    voor alles wat er ná de migratie bij komt."""
    from app import rbac
    maak_gebruiker(db, "nieuwkomer")
    node = maak_node(db)
    assert not rbac.decide("nieuwkomer", "node.bekijken", node).allowed
    assert not rbac.decide("nieuwkomer", "server.instellingen").allowed


# --- 2. de rollen zijn plafonds ----------------------------------------------

@pytest.mark.parametrize("rol,mag,magniet", [
    ("lezer", ["node.bekijken"],
     ["node.uitvragen", "node.klok", "node.firmware"]),
    ("bediener", ["node.bekijken", "node.uitvragen", "node.hernoemen"],
     ["node.klok", "node.zichtbaarheid", "node.firmware"]),
    ("technicus", ["node.uitvragen", "node.klok", "node.zichtbaarheid"],
     ["node.firmware", "node.verwijderen", "node.instelling.ingrijpend"]),
    ("beheerder", ["node.klok", "node.firmware", "node.verwijderen"], []),
])
def test_rol_is_een_plafond_op_de_risicoklasse(db, rol, mag, magniet):
    """De knip die gevraagd werd: wel de klok zetten, geen firmware flashen."""
    from app import rbac
    uid = maak_gebruiker(db, f"iemand_{rol}")
    node = maak_node(db)
    rbac.maak_toekenning("user", uid, "node", node["id"], rol, "allow")
    for handeling in mag:
        assert rbac.decide(f"iemand_{rol}", handeling, node).allowed, handeling
    for handeling in magniet:
        besluit = rbac.decide(f"iemand_{rol}", handeling, node)
        assert not besluit.allowed, handeling
        # De weigering noemt de klasse en de rol, zodat een uitgeschakelde knop
        # geen raadsel is.
        assert rol in besluit.reason


def test_serverbeheerder_mag_alles_zonder_een_enkele_toekenning(db):
    from app import rbac
    maak_gebruiker(db, "baas", superuser=True)
    node = maak_node(db)
    for handeling in rbac.ACTIONS:
        assert rbac.decide("baas", handeling, node).allowed, handeling


def test_serverhandelingen_zijn_niet_per_node_toe_te_kennen(db):
    """Ook een 'beheerder' op alle nodes komt niet aan de serverinstellingen.

    Dat is de scheiding waar het model op rust: wie tokens mag maken of
    gebruikers mag beheren, kan zichzelf de rest geven.
    """
    from app import rbac
    uid = maak_gebruiker(db, "overal")
    maak_node(db)
    rbac.maak_toekenning("user", uid, "all", None, "beheerder", "allow")
    assert not rbac.decide("overal", "server.tokens").allowed
    assert not rbac.decide("overal", "server.gebruikers").allowed
    assert "serverbeheerder" in rbac.decide("overal", "server.tokens").reason


def test_een_uitgezet_account_mag_niets_meer(db):
    from app import rbac
    uid = maak_gebruiker(db, "vertrokken", superuser=True)
    node = maak_node(db)
    rbac.zet_uit(uid, True)
    assert not rbac.decide("vertrokken", "node.bekijken", node).allowed


def test_een_besluit_is_altijd_waar_ook_als_het_nee_zegt(db):
    """Regressie op een val die er echt in gezeten heeft.

    Een ``__bool__`` die de uitkomst teruggaf leek logisch en betekende dat een
    weigering onwaar was. Een sjabloon dat ``{% if besluit %}`` schrijft, bedoelt
    "is er een besluit" -- want de reden dat het er geen is, is dat de bezoeker
    niet ingelogd is -- en dan sloeg de tak die de knop uitschakelt stilletjes
    over, precies bij de weigering. Elk besluit is dus waar; wie de uitkomst wil,
    vraagt naar ``.allowed``.
    """
    from app import rbac
    maak_gebruiker(db, "iemand")
    besluit = rbac.decide("iemand", "node.firmware", maak_node(db))
    assert besluit.allowed is False
    assert bool(besluit) is True


def test_een_onbekende_handeling_is_een_dichte_deur(db):
    """Fail closed: een tikfout in een routenaam mag geen open deur zijn."""
    from app import rbac
    maak_gebruiker(db, "baas", superuser=True)
    assert not rbac.decide("baas", "node.doeietsgeks", maak_node(db)).allowed


# --- 3. groepen en botsende toekenningen -------------------------------------

def test_rechten_lopen_via_een_gebruikersgroep(db):
    from app import rbac
    uid = maak_gebruiker(db, "lid")
    gid = rbac.maak_groep("user", "Ploeg")
    rbac.zet_lidmaatschap("user", gid, uid, True)
    node = maak_node(db)
    rbac.maak_toekenning("group", gid, "node", node["id"], "technicus", "allow")
    assert rbac.decide("lid", "node.klok", node).allowed
    # En hij vervalt weer zodra het lidmaatschap weg is.
    rbac.zet_lidmaatschap("user", gid, uid, False)
    assert not rbac.decide("lid", "node.klok", node).allowed


def test_rechten_lopen_via_een_nodegroep(db):
    from app import rbac
    uid = maak_gebruiker(db, "lid")
    node = maak_node(db)
    ander = maak_node(db, "Andere", "aabbccddeeff")
    ngid = rbac.maak_groep("node", "Daken")
    rbac.zet_lidmaatschap("node", ngid, node["id"], True)
    rbac.maak_toekenning("user", uid, "nodegroup", ngid, "bediener", "allow")
    assert rbac.decide("lid", "node.uitvragen", node).allowed
    # De node die niet in de groep zit, krijgt niets mee.
    assert not rbac.decide("lid", "node.bekijken", ander).allowed


def test_de_ruimste_toestemming_wint(db):
    """Iemand aan een groep toevoegen mag zijn rechten nooit kleiner maken."""
    from app import rbac
    uid = maak_gebruiker(db, "dubbel")
    node = maak_node(db)
    gid = rbac.maak_groep("user", "Kijkers")
    rbac.zet_lidmaatschap("user", gid, uid, True)
    rbac.maak_toekenning("group", gid, "all", None, "lezer", "allow")
    rbac.maak_toekenning("user", uid, "node", node["id"], "beheerder", "allow")
    besluit = rbac.decide("dubbel", "node.firmware", node)
    assert besluit.allowed
    assert besluit.rol == "beheerder"


def test_weigeren_wint_van_toestaan_hoe_specifiek_die_ook_is(db):
    """De conflictregel, en de reden erachter: wie een uitzondering intrekt, wil
    dat die intrekking het laatste woord heeft."""
    from app import rbac
    uid = maak_gebruiker(db, "geblokkeerd")
    node = maak_node(db)
    # De ruimst mogelijke toestemming, rechtstreeks op deze node...
    rbac.maak_toekenning("user", uid, "node", node["id"], "beheerder", "allow")
    # ...en een weigering op het minst specifieke niveau dat er is.
    rbac.maak_toekenning("user", uid, "all", None, None, "deny")
    besluit = rbac.decide("geblokkeerd", "node.bekijken", node)
    assert not besluit.allowed
    assert "weigering" in besluit.reason


def test_een_weigering_via_een_groep_treft_ook_de_leden(db):
    from app import rbac
    uid = maak_gebruiker(db, "lid")
    gid = rbac.maak_groep("user", "Geschorst")
    rbac.zet_lidmaatschap("user", gid, uid, True)
    node = maak_node(db)
    rbac.maak_toekenning("user", uid, "all", None, "beheerder", "allow")
    rbac.maak_toekenning("group", gid, "node", node["id"], None, "deny")
    assert not rbac.decide("lid", "node.bekijken", node).allowed


def test_een_node_in_geen_enkele_groep_is_alleen_via_alles_bereikbaar(db):
    """De klassieke valkuil: een node die vanzelf uit het verkeer ontstaat zit
    nergens in, en is dan voor iedereen behalve de serverbeheerder onzichtbaar."""
    from app import rbac
    uid = maak_gebruiker(db, "lid")
    los = maak_node(db)
    ngid = rbac.maak_groep("node", "Daken")
    rbac.maak_toekenning("user", uid, "nodegroup", ngid, "beheerder", "allow")
    assert not rbac.decide("lid", "node.bekijken", los).allowed
    assert [r["id"] for r in rbac.nodes_zonder_groep([los])] == [los["id"]]
    # Een toekenning op 'alle nodes' vangt hem wél, en dat is de bedoelde
    # ontsnapping: je hoeft niet elke nieuwe node in een groep te stoppen.
    rbac.maak_toekenning("user", uid, "all", None, "lezer", "allow")
    assert rbac.decide("lid", "node.bekijken", los).allowed


def test_een_toekenning_zonder_geldige_rol_wordt_geweigerd(db):
    from app import rbac
    uid = maak_gebruiker(db, "iemand")
    with pytest.raises(ValueError):
        rbac.maak_toekenning("user", uid, "all", None, "supergebruiker", "allow")
    with pytest.raises(ValueError):
        rbac.maak_toekenning("user", uid, "node", None, "lezer", "allow")


def test_zichtbare_nodes_toont_alleen_wat_iemand_mag(db):
    from app import rbac
    uid = maak_gebruiker(db, "lid")
    een = maak_node(db, "Een", "111111111111")
    twee = maak_node(db, "Twee", "222222222222")
    rbac.maak_toekenning("user", uid, "node", een["id"], "lezer", "allow")
    zichtbaar = rbac.zichtbare_nodes("lid", [een, twee])
    assert [r["name"] for r in zichtbaar] == ["Een"]


# --- 4. het audittrail --------------------------------------------------------

def test_het_trail_legt_de_geslaagde_handeling_vast(db):
    from app import audit
    node = maak_node(db)
    audit.log("bjorn", "node.firmware", rep=node, detail="upgrade naar v1.12.0")
    regel = audit.recent(5)[0]
    assert regel["actor"] == "bjorn"
    assert regel["action"] == "node.firmware"
    assert regel["object_name"] == "Dakrepeater"
    assert regel["outcome"] == "ok"
    assert regel["ts"]


def test_het_trail_legt_ook_de_geweigerde_poging_vast(db, monkeypatch):
    """Een poging die op de rechten afketst is juist de regel die je wil zien."""
    from app import routes_admin
    from fastapi import HTTPException

    node = maak_node(db)
    maak_gebruiker(db, "nieuwsgierig")
    monkeypatch.setattr(routes_admin, "require_login", lambda request: "nieuwsgierig")
    with pytest.raises(HTTPException) as fout:
        routes_admin.require_perm(None, "node.firmware", node)
    assert fout.value.status_code == 403

    from app import audit
    regel = audit.recent(5)[0]
    assert regel["outcome"] == "geweigerd"
    assert regel["actor"] == "nieuwsgierig"
    assert regel["action"] == "node.firmware"
    assert regel["object_id"] == node["id"]


def test_het_trail_van_een_node_blijft_na_het_verwijderen_van_de_gebruiker(db):
    """De naam staat er als tekst in, met opzet: wat er gebeurd is blijft waar
    ook nadat de persoon weg is."""
    from app import audit, rbac
    uid = maak_gebruiker(db, "tijdelijk")
    node = maak_node(db)
    audit.log("tijdelijk", "node.klok", rep=node)
    rbac.verwijder_gebruiker(uid)
    assert audit.recent(5)[0]["actor"] == "tijdelijk"


def test_het_trail_van_een_node_overleeft_de_node(db):
    from app import audit
    node = maak_node(db)
    audit.log("bjorn", "node.verwijderen", rep=node, detail="sleutel e3d3f4d7edd0")
    db.execute("DELETE FROM repeaters WHERE id=?", (node["id"],))
    regel = audit.recent(5)[0]
    assert regel["object_name"] == "Dakrepeater"


def test_een_kapot_trail_laat_de_handeling_doorgaan(db, monkeypatch):
    """Een volle schijf mag een firmware-upgrade niet halverwege doen ontploffen."""
    from app import audit
    monkeypatch.setattr(audit.db, "execute",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("schijf vol")))
    audit.log("bjorn", "node.firmware")     # geen exception


def test_het_trail_wordt_gesnoeid_op_zijn_eigen_termijn(db):
    from app import audit
    db.execute("INSERT INTO audit(ts, actor, action, outcome) "
               "VALUES('2020-01-01T00:00:00Z','oud','node.klok','ok')")
    db.execute("INSERT INTO audit(ts, actor, action, outcome) VALUES(?,?,?,?)",
               (db.utcnow(), "vandaag", "node.klok", "ok"))
    assert audit.prune(days=30) == 1
    assert [r["actor"] for r in audit.recent(10)] == ["vandaag"]


# --- 5. geen route vergeet de controle ---------------------------------------

def test_elke_schrijvende_beheerroute_gaat_door_de_poort():
    """De vangnettest.

    Een route die de rechtencontrole mist, werkt -- hij werkt alleen voor
    iedereen, en dat merk je aan niets. Deze test loopt de router zelf af en
    eist dat elke POST-handler ``require_perm`` aanroept. Wie een uitzondering
    nodig heeft, zet hem met een reden in ROUTES_ZONDER_RECHTENCONTROLE; dat is
    een bewuste handeling en geen vergetelheid.
    """
    from app import routes_admin

    vergeten = []
    for route in routes_admin.router.routes:
        if "POST" not in getattr(route, "methods", set()):
            continue
        naam = route.endpoint.__name__
        if naam in routes_admin.ROUTES_ZONDER_RECHTENCONTROLE:
            continue
        if "require_perm(" not in inspect.getsource(route.endpoint):
            vergeten.append(naam)
    assert vergeten == [], f"zonder rechtencontrole: {vergeten}"


def test_elke_handeling_in_de_routes_bestaat_ook_in_het_model():
    """Een tikfout in een handelingsnaam is een dichte deur (rbac faalt gesloten),
    en dus een knop die het niet meer doet zonder dat iemand weet waarom. Hier
    valt hij op."""
    import re
    from app import rbac, routes_admin

    bron = inspect.getsource(routes_admin)
    genoemd = set(re.findall(r'require_perm\(\s*request,\s*"([^"]+)"', bron))
    assert genoemd, "geen enkele aanroep gevonden -- deze test kijkt naar de verkeerde vorm"
    onbekend = genoemd - set(rbac.ACTIONS)
    assert onbekend == set(), f"onbekende handelingen: {onbekend}"


def test_elke_handeling_heeft_een_geldige_klasse_en_scope():
    from app import rbac
    for naam, h in rbac.ACTIONS.items():
        assert h.klasse in rbac.KLASSEN, naam
        assert h.scope in ("node", "server"), naam
        assert h.tekst and not h.tekst.endswith("."), naam


def test_de_laatste_serverbeheerder_is_te_herkennen(db):
    """De grendel waar de routes op leunen: rbac telt wie er nog over is."""
    from app import rbac
    uid = maak_gebruiker(db, "baas", superuser=True)
    maak_gebruiker(db, "gewoon")
    assert rbac.aantal_serverbeheerders() == 1
    assert rbac.aantal_serverbeheerders(behalve=uid) == 0
    tweede = maak_gebruiker(db, "reserve", superuser=True)
    assert rbac.aantal_serverbeheerders(behalve=uid) == 1
    rbac.zet_uit(tweede, True)
    assert rbac.aantal_serverbeheerders(behalve=uid) == 0
