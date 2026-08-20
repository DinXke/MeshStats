"""Tests voor de gebeurtenis-push van sensornodes: POST /api/sensorpush.

Het contract met de firmwarekant staat in sensorpush.py, en deze tests bewaken
het van de serverkant: de vier antwoorden (200/400/401/404, plus 503 als de weg
dicht staat), de weg van een event naar een alarm, de kruisontdubbeling met de
IP-afleiding IN BEIDE VOLGORDES, de ack-stroom heen (de node bevestigt) en
terug (de server meldt eenmalig wat hier bevestigd is), de stiltebewaking met
haar ijking na een herstart, en de bootteller die een herstart zichtbaar maakt
zonder er een alarm van te maken.

De endpointfunctie wordt rechtstreeks aangeroepen (asyncio.run), zoals de
meshmoni-tests dat doen: er komt geen TestClient aan te pas, en dat is met
opzet -- wat hier getest wordt is het gedrag, niet de FastAPI-bedrading.
"""
import asyncio

import pytest
from fastapi import HTTPException

from app import config, sensornode, sensorpush


@pytest.fixture
def db(tmp_path, monkeypatch):
    """De db-module tegen een verse, tijdelijke database. Zelfde opzet als
    test_alerts.py, en om dezelfde reden (Windows en de moduleverbinding)."""
    from app import db as db_module
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "test.sqlite3")
    db_module._conn = None
    yield db_module
    if db_module._conn is not None:
        db_module._conn.close()
        db_module._conn = None


@pytest.fixture
def push(monkeypatch):
    """De weg open (token gezet) en al het procesgeheugen schoon, per test.

    Het geheugen -- de hartslagtabel, de herhalingscache en de begrenzing --
    leeft op moduleniveau, met opzet (zie sensorpush.py), en zou anders van
    test naar test lekken zoals het in productie van push naar push draagt.
    Ook de vorige-toestandtabel van de IP-afleiding gaat leeg, want de
    kruisontdubbelingstests gebruiken die kant echt.
    """
    monkeypatch.setattr(sensorpush, "TOKEN", "geheim")
    monkeypatch.setattr(sensornode, "_toestand", {})
    sensorpush.reset()
    yield
    sensorpush.reset()


NODE = "48d7aade232b"


class _Request:
    """Het minimum dat het endpoint van een Request aanraakt: de headers (voor
    het clientadres van de begrenzing) en de body."""

    def __init__(self, body=None, ip=None):
        self.headers = {"x-forwarded-for": ip} if ip else {}
        self.client = None
        self._body = body

    async def json(self):
        if self._body is None:
            raise ValueError("geen body")
        return self._body


def _body(**over) -> dict:
    basis = {"node": NODE, "seq": 1, "boot": 1, "hb_s": 60,
             "events": [], "acked": []}
    basis.update(over)
    return basis


def _event(**over) -> dict:
    basis = {"ch": 6, "kind": "neer", "text": "hoas gemeld als neer",
             "sev": "hoog", "sim": 0}
    basis.update(over)
    return basis


def _call(body, token="Bearer geheim", ip=None):
    return asyncio.run(sensorpush.sensorpush(_Request(body, ip=ip),
                                             authorization=token))


# --- de deur -------------------------------------------------------------------

def test_zonder_token_op_de_server_staat_de_weg_dicht(db, push, monkeypatch):
    """Leeg = uit, met de reden erbij: 503 en geen 401. Een node die het juiste
    token stuurt naar een server die er geen heeft, moet kunnen zien dat het
    aan de server ligt en niet aan zijn token."""
    monkeypatch.setattr(sensorpush, "TOKEN", "")
    with pytest.raises(HTTPException) as fout:
        _call(_body())
    assert fout.value.status_code == 503
    assert "MM_PUSH_TOKEN" in fout.value.detail


def test_zonder_of_met_fout_token_is_het_401(db, push):
    for token in (None, "geheim", "Bearer verkeerd", "Basic geheim"):
        with pytest.raises(HTTPException) as fout:
            _call(_body(), token=token)
        assert fout.value.status_code == 401


def test_een_onbekende_node_is_404_en_er_komt_geen_rij_bij(db, push):
    """Dit endpoint mag geen nodes aanmaken: wie het token heeft, heeft daarmee
    nog geen recht om de nodelijst te vullen."""
    with pytest.raises(HTTPException) as fout:
        _call(_body())
    assert fout.value.status_code == 404
    assert db.find_repeater(NODE) is None


@pytest.mark.parametrize("kapot", [
    {"node": "zzzzzzzzzzzz"},                       # geen hex
    {"node": "48d7aa"},                             # te kort: contract zegt 12
    {"node": "48d7aade232b48d7aade232b"},           # te lang
    {"seq": "1"},                                   # tekst waar een getal hoort
    {"seq": True},                                  # bool is geen teller
    {"boot": -1},
    {"hb_s": 70000},                                # buiten uint16
    {"events": {}},                                 # geen lijst
    {"events": [_event(kind="stil")]},              # de node meldt alleen neer/op
    {"events": [_event(sev="midden")]},
    {"events": [_event(sim=2)]},
    {"events": [_event(text="")]},
    {"events": [_event(ch=300)]},
    {"events": [_event()] * (sensorpush.MAX_EVENTS + 1)},
    {"acked": ["5"]},
    {"acked": [5] * (sensorpush.MAX_ACKED + 1)},
])
def test_vormfouten_zijn_400(db, push, kapot):
    db.get_or_create_repeater(NODE, "MeshUptime")
    with pytest.raises(HTTPException) as fout:
        _call(_body(**kapot))
    assert fout.value.status_code == 400


def test_geen_json_is_ook_400(db, push):
    db.get_or_create_repeater(NODE, "MeshUptime")
    with pytest.raises(HTTPException) as fout:
        _call(None)
    assert fout.value.status_code == 400


def test_de_begrenzing_geeft_429_wie_erover_gaat(db, push, monkeypatch):
    db.get_or_create_repeater(NODE, "MeshUptime")
    monkeypatch.setattr(sensorpush, "RATE_MAX", 3)
    for seq in range(3):
        _call(_body(seq=seq), ip="203.0.113.7")
    with pytest.raises(HTTPException) as fout:
        _call(_body(seq=99), ip="203.0.113.7")
    assert fout.value.status_code == 429


# --- events worden alarmen --------------------------------------------------------

def test_een_event_wordt_een_alarm_met_bron_push(db, push):
    node = db.get_or_create_repeater(NODE, "MeshUptime")
    uit = _call(_body(events=[_event()]))
    assert uit == {"ok": 1, "ack": []}
    rijen = db.alerts_for(node["id"])
    assert len(rijen) == 1
    rij = rijen[0]
    assert rij["text"] == "hoas gemeld als neer"
    assert rij["source"] == "push"
    assert rij["kind"] == "neer" and rij["severity"] == "hoog"
    assert rij["channel"] == 6 and rij["acked"] == 0


def test_een_oefening_krijgt_exact_de_markering_van_de_ip_afleiding(db, push):
    """sim=1 gaat door sensornode.mark_simulation -- dezelfde functie, geen
    tweede spelling. De tekst krijgt "(simulatie)", de soort wordt NULL zodat
    een oefening nooit een echte melding onderdrukt of erdoor onderdrukt
    wordt, en de ernst blijft staan: wie test of een hoge melding doorkomt,
    moet een hoge melding krijgen."""
    node = db.get_or_create_repeater(NODE, "MeshUptime")
    _call(_body(events=[_event(sim=1)]))
    rij = db.alerts_for(node["id"])[0]
    spiegel = sensornode.mark_simulation(
        {"text": "hoas gemeld als neer", "kind": "neer"})
    assert rij["text"] == spiegel["text"]
    assert rij["kind"] is None and spiegel["kind"] is None
    assert rij["severity"] == "hoog"
    # En juist DOORDAT de soort NULL is: een echte 'neer' vlak erna komt er
    # gewoon doorheen.
    assert _call(_body(seq=2, events=[_event()]))["ok"] == 1
    assert len(db.alerts_for(node["id"])) == 2


def test_dezelfde_push_tekst_binnen_het_venster_is_een_herhaling(db, push):
    """De gewone tekst-ontdubbeling geldt hier ook: een node die zijn event
    herhaalt omdat hij geen 200 kreeg (met een NIEUWE seq, dus buiten de
    herhalingscache) maakt geen tweede rij."""
    node = db.get_or_create_repeater(NODE, "MeshUptime")
    _call(_body(seq=1, events=[_event()]))
    _call(_body(seq=2, events=[_event()]))
    assert len(db.alerts_for(node["id"])) == 1


# --- de kruisontdubbeling met de IP-afleiding, in beide volgordes -----------------

def _status(st="op"):
    """Een /status.json met kanaal 6 als gemelde dienst 'hoas' -- dezelfde
    dienst en hetzelfde kanaal als _event(), want dat is het hele punt."""
    return {"fw": "1.4.0", "mains": 1, "mon": [
        {"ch": 6, "n": "hoas", "h": "(gemeld)", "st": st, "ms": 12,
         "k": "gemeld"},
    ]}


def test_kruisontdubbeling_push_eerst_dan_poll(db, push):
    """De push is er seconden na het feit; ziet de IP-poll dezelfde overgang
    een ronde later, dan is dat geen tweede storing. (node, kind, kanaal)
    binnen het venster vangt hem, welke tekstvorm de poll ook maakt."""
    node = db.get_or_create_repeater(NODE, "MeshUptime")
    _call(_body(events=[_event()]))
    sensornode._derive_alerts(node["id"], _status("op"))       # ijken
    assert sensornode._derive_alerts(node["id"], _status("neer")) == 0
    assert len(db.alerts_for(node["id"])) == 1
    assert db.alerts_for(node["id"])[0]["source"] == "push"


def test_kruisontdubbeling_poll_eerst_dan_push(db, push):
    """En andersom: heeft de poll de overgang al gezien (bijvoorbeeld omdat de
    push even niet doorkwam en later herhaald werd), dan maakt de push geen
    tweede rij."""
    node = db.get_or_create_repeater(NODE, "MeshUptime")
    sensornode._derive_alerts(node["id"], _status("op"))       # ijken
    assert sensornode._derive_alerts(node["id"], _status("neer")) == 1
    _call(_body(events=[_event()]))
    assert len(db.alerts_for(node["id"])) == 1
    assert db.alerts_for(node["id"])[0]["source"] == "ip"


# --- de ack-stroom: van de node hierheen ------------------------------------------

def test_acked_van_de_node_bevestigt_zijn_open_alarmen_op_dat_kanaal(db, push):
    node = db.get_or_create_repeater(NODE, "MeshUptime")
    db.add_alert(node["id"], "hoas onbereikbaar (hoas.local)", source="ip",
                 channel=5, severity="hoog", kind="neer")
    db.add_alert(node["id"], "google onbereikbaar (google.com)", source="ip",
                 channel=7, severity="hoog", kind="neer")
    _call(_body(acked=[5]))
    assert db.alerts_open_count(node["id"]) == 1        # kanaal 7 staat nog
    open_rij = [r for r in db.alerts_for(node["id"]) if not r["acked"]][0]
    assert open_rij["channel"] == 7


def test_een_ack_van_de_node_laat_een_spoor_na_in_het_audittrail(db, push):
    """Zelfde effect als de ack-knop, dus ook dezelfde navertelbaarheid -- met
    de node als actor, want "wie heeft dit bevestigd" moet één eerlijk
    antwoord houden."""
    from app import audit
    node = db.get_or_create_repeater(NODE, "MeshUptime")
    db.add_alert(node["id"], "hoas gemeld als neer", source="push",
                 channel=6, severity="hoog", kind="neer")
    _call(_body(acked=[6]))
    regels = audit.recent(10, rep_id=node["id"])
    assert regels and regels[0]["actor"] == f"node {NODE}"
    assert "via push" in regels[0]["detail"]


def test_wat_de_node_zelf_bevestigde_komt_niet_terug_in_ack(db, push):
    """De node zei het zelf; het hem nog eens melden zou het ack-antwoord tot
    ruis maken. ack_alerts_from_node zet daarom meteen de afleverstand."""
    node = db.get_or_create_repeater(NODE, "MeshUptime")
    db.add_alert(node["id"], "hoas gemeld als neer", source="push",
                 channel=6, severity="hoog", kind="neer")
    _call(_body(seq=1, acked=[6]))
    assert _call(_body(seq=2))["ack"] == []


# --- de ack-stroom: van hier naar de node -----------------------------------------

def test_een_serverzijde_ack_wordt_precies_een_keer_geleverd(db, push):
    node = db.get_or_create_repeater(NODE, "MeshUptime")
    _call(_body(seq=1, events=[_event(ch=6)]))
    rij = db.alerts_for(node["id"])[0]
    assert db.ack_alert(rij["id"])                       # de knop op de site
    assert _call(_body(seq=2))["ack"] == [6]
    assert _call(_body(seq=3))["ack"] == []              # eenmalig


def test_de_afleverstand_overleeft_een_serverherstart(db, push):
    """De stand staat in de databank (alerts.ack_pushed) en niet in het
    geheugen: na een herstart mag een kanaal dat al gemeld is niet opnieuw
    komen -- anders levert elke deploy elke node zijn hele ack-verleden."""
    node = db.get_or_create_repeater(NODE, "MeshUptime")
    _call(_body(seq=1, events=[_event(ch=6)]))
    db.ack_alert(db.alerts_for(node["id"])[0]["id"])
    assert _call(_body(seq=2))["ack"] == [6]
    sensorpush.reset()                                   # de "herstart"
    sensorpush._seed()
    assert _call(_body(seq=3))["ack"] == []


def test_een_herhaalde_push_krijgt_hetzelfde_antwoord_terug(db, push):
    """Zelfde boot en seq is dezelfde push: het 200-antwoord is dan onderweg
    verloren gegaan. De herhaling moet exact hetzelfde ack-lijstje krijgen --
    de kanalen zijn bij de eerste verwerking al als gemeld aangemerkt en
    zouden anders verdampen -- en niets dubbel verwerken."""
    node = db.get_or_create_repeater(NODE, "MeshUptime")
    db.add_alert(node["id"], "hoas gemeld als neer", source="push",
                 channel=6, severity="hoog", kind="neer")
    db.ack_alert(db.alerts_for(node["id"])[0]["id"])
    eerste = _call(_body(seq=5, events=[_event(ch=7, text="google gemeld als neer")]))
    assert eerste["ack"] == [6]
    tweede = _call(_body(seq=5, events=[_event(ch=7, text="google gemeld als neer")]))
    assert tweede == eerste
    assert len(db.alerts_for(node["id"])) == 2           # niet drie
    rep = db.qone("SELECT * FROM repeaters WHERE id=?", (node["id"],))
    assert rep["push_count"] == 1                        # de herhaling telt niet


def test_een_alarm_zonder_kanaal_komt_nooit_in_ack(db, push):
    """Het antwoord aan de node is per kanaal; een stiltemelding of de
    netvoeding heeft aan zijn kant niets om te sluiten."""
    node = db.get_or_create_repeater(NODE, "MeshUptime")
    db.add_alert(node["id"], "netvoeding weg, node op batterij", source="ip",
                 severity="hoog", kind="neer")
    db.execute("UPDATE alerts SET acked=1 WHERE repeater_id=?", (node["id"],))
    assert _call(_body(seq=1))["ack"] == []


# --- de waarneming: push_seen, tellers, bootteller --------------------------------

def test_de_waarneming_gaat_de_databank_in(db, push):
    node = db.get_or_create_repeater(NODE, "MeshUptime")
    _call(_body(seq=41, boot=3, hb_s=120))
    _call(_body(seq=42, boot=3, hb_s=120))
    rep = db.qone("SELECT * FROM repeaters WHERE id=?", (node["id"],))
    assert rep["push_seen"] is not None
    assert rep["push_hb_s"] == 120
    assert rep["push_seq"] == 42
    assert rep["push_boot"] == 3
    assert rep["push_count"] == 2
    assert rep["push_boot_at"] is None                   # nooit zien verspringen


def test_een_veranderde_bootteller_is_geen_alarm_maar_wel_zichtbaar(db, push):
    node = db.get_or_create_repeater(NODE, "MeshUptime")
    _call(_body(seq=1, boot=3))
    _call(_body(seq=2, boot=4))
    assert db.alerts_for(node["id"]) == []               # geen alarm
    rep = db.qone("SELECT * FROM repeaters WHERE id=?", (node["id"],))
    assert rep["push_boot"] == 4
    assert rep["push_boot_at"] is not None               # wel op de pagina


# --- de stiltebewaking ------------------------------------------------------------

class _Klok:
    """time voor sensorpush: een monotone klok die de test zelf vooruitzet."""

    def __init__(self):
        self.nu = 1000.0

    def monotonic(self):
        return self.nu

    def sleep(self, s):                                  # pragma: no cover
        raise AssertionError("de tests draaien de rondes zelf")


@pytest.fixture
def klok(monkeypatch):
    k = _Klok()
    monkeypatch.setattr(sensorpush, "time", k)
    return k


def test_drie_hartslagen_stilte_is_een_hoog_alarm(db, push, klok):
    """De keuze en de reden: bij 'stil' is de MELDER weg en weten wij niets
    meer -- niet of zijn diensten draaien, niet of hij zelf nog leeft. Dat is
    de weging die de IP-afleiding en de firmware een stilgevallen melder ook
    geven, en een bewakingsnode die wegvalt is nu net waarvoor je gewekt wilt
    worden. Vandaar hoog, en soort 'stil' en niet 'neer': er ligt niets
    aantoonbaar plat, we weten het alleen niet meer."""
    node = db.get_or_create_repeater(NODE, "MeshUptime")
    _call(_body(hb_s=60))
    klok.nu += 179                                       # grens is 3*60
    assert sensorpush._watch_once() == 0
    klok.nu += 2
    assert sensorpush._watch_once() == 1
    rij = db.alerts_for(node["id"])[0]
    assert rij["text"].startswith("node stil (push)")
    assert rij["kind"] == "stil" and rij["severity"] == "hoog"
    assert rij["source"] == "push"
    assert sensorpush.is_stil(node["id"])
    # Eén stilte is één gebeurtenis, hoeveel rondes ze ook duurt.
    klok.nu += 600
    assert sensorpush._watch_once() == 0


def test_de_ondergrens_van_90_s_geldt_voor_een_korte_hartslag(db, push, klok):
    db.get_or_create_repeater(NODE, "MeshUptime")
    _call(_body(hb_s=5))
    klok.nu += 89                                        # 3*5=15, maar de vloer is 90
    assert sensorpush._watch_once() == 0
    klok.nu += 2
    assert sensorpush._watch_once() == 1


def test_de_node_komt_terug_en_dat_is_een_laag_herstel(db, push, klok):
    node = db.get_or_create_repeater(NODE, "MeshUptime")
    _call(_body(seq=1, hb_s=60))
    klok.nu += 181
    sensorpush._watch_once()
    _call(_body(seq=2, hb_s=60))
    rij = db.alerts_for(node["id"])[0]
    assert rij["text"] == "node pusht weer"
    assert rij["kind"] == "op" and rij["severity"] == "laag"
    assert not sensorpush.is_stil(node["id"])


def test_de_nodepagina_toont_de_pushvlag_en_de_bron(db, push, monkeypatch):
    """Vlagvoering: laatste push, hartslag, teller en de herstart staan op de
    nodepagina, en een alarm met bron 'push' krijgt daar zijn eigen woorden --
    naast 'over IP' en 'over het mesh', niet erin opgegaan. Echt gerenderd,
    want de takken van een sjabloon branden pas bij het renderen (zie
    test_beheerpaginas_renderen.py)."""
    from starlette.requests import Request
    from app import auth, mqtt_ingest, rbac, routes_admin
    monkeypatch.setattr(mqtt_ingest, "can_publish", lambda: False)
    from app import firmware
    monkeypatch.setattr(firmware, "releases",
                        lambda force=False: {"items": [], "error": "", "at": 0})

    node = db.get_or_create_repeater(NODE, "MeshUptime")
    rbac.maak_gebruiker("admin", auth.hash_password("wachtwoord123"),
                        is_superuser=True)
    koek = auth.make_session("admin")

    _call(_body(seq=1, boot=3, hb_s=120, events=[_event()]))
    _call(_body(seq=2, boot=4, hb_s=120))                # en een herstart
    # En de stille tak van het sjabloon, want die brandt alleen als hij rendert.
    sensorpush._hb[node["id"]]["stil"] = True

    resp = routes_admin.node_page(Request({
        "type": "http", "http_version": "1.1", "method": "GET",
        "scheme": "http", "server": ("test", 80),
        "path": f"/admin/repeaters/{node['id']}", "query_string": b"",
        "headers": [(b"cookie", f"mm_session={koek}".encode())],
    }), node["id"])
    html = resp.body.decode()
    assert "Gebeurtenis-push" in html
    assert "elke 120 s" in html                          # de beloofde hartslag
    assert "Laatste herstart" in html                    # de bootwissel is te zien
    assert "gepusht door de node zelf" in html           # de bron bij het alarm
    assert "360 s geen push" in html                     # de stille tak, 3×120


def test_meshmoni_ziet_de_pushvlag_bij_de_node(db, push):
    """/meshmoni tekent de pushregel alleen voor nodes die echt pushen; het
    endpoint levert daarvoor 'push' mee (None voor de rest)."""
    from app import meshmoni
    node = db.get_or_create_repeater(NODE, "MeshUptime")
    db.ingest(node["id"], db.utcnow(), {"online": True, "ch6_switch": 1}, None)
    ander = db.get_or_create_repeater("aabbccddeeff", "Stille node")
    db.ingest(ander["id"], db.utcnow(), {"online": True, "ch2_switch": 1}, None)
    _call(_body(seq=1, hb_s=60))
    per_naam = {n["name"]: n for n in meshmoni._sensornodes()}
    assert per_naam["MeshUptime"]["push"] is not None
    assert per_naam["MeshUptime"]["push"]["hb_s"] == 60
    assert per_naam["MeshUptime"]["push"]["count"] == 1
    assert per_naam["MeshUptime"]["push"]["stil"] is False
    assert per_naam["Stille node"]["push"] is None


def test_migratie_merkt_historisch_bevestigde_alarmen_als_al_gemeld(tmp_path):
    """Een databank van vóór deze feature heeft geen ack_pushed-kolom. De
    migratie voegt hem toe met DEFAULT 0, en de POST_MIGRATION zet alles wat
    al bevestigd was op 1 -- anders zou de allereerste push van elke node de
    kanalen van maanden oude, lang bevestigde storingen aangeleverd krijgen.
    Wat toen nog OPEN stond blijft 0: wordt dat later bevestigd, dan hoort de
    node het wél te horen."""
    import sqlite3
    from app import db as db_module

    conn = sqlite3.connect(tmp_path / "oud.sqlite3")
    conn.row_factory = sqlite3.Row
    try:
        conn.executescript(
            "CREATE TABLE alerts(id INTEGER PRIMARY KEY, repeater_id INTEGER,"
            " channel INTEGER, text TEXT NOT NULL, severity TEXT,"
            " ts TEXT NOT NULL, source TEXT NOT NULL, acked INTEGER DEFAULT 0,"
            " kind TEXT);")
        conn.execute("INSERT INTO alerts(repeater_id, channel, text, ts, "
                     "source, acked) VALUES(1, 5, 'oud en gezien', "
                     "'2026-01-01T00:00:00Z', 'mesh', 1)")
        conn.execute("INSERT INTO alerts(repeater_id, channel, text, ts, "
                     "source, acked) VALUES(1, 6, 'oud en nog open', "
                     "'2026-01-01T00:00:00Z', 'mesh', 0)")
        conn.commit()

        db_module._migrate(conn)

        stand = {r["channel"]: r["ack_pushed"]
                 for r in conn.execute("SELECT channel, ack_pushed FROM alerts")}
        assert stand == {5: 1, 6: 0}
    finally:
        conn.close()


def test_een_serverherstart_ijkt_en_geeft_geen_valse_stilte(db, push, klok):
    """Het geheugen is na een herstart leeg, en push_seen in de databank kan
    minuten oud zijn. IJken op NU (zoals de eerste ronde van de IP-afleiding)
    betekent: geen golf valse alarmen bij elke deploy -- en een node die echt
    wegbleef, wordt alsnog gemeld zodra hij ná het ijken drie hartslagen stil
    blijft."""
    node = db.get_or_create_repeater(NODE, "MeshUptime")
    _call(_body(hb_s=60))
    klok.nu += 3600                                      # de node zweeg een uur...
    sensorpush.reset()                                   # ...en de server herstart
    sensorpush._seed()
    assert sensorpush._watch_once() == 0                 # ijken, niet alarmeren
    klok.nu += 181                                       # maar de belofte blijft gelden
    assert sensorpush._watch_once() == 1
    assert db.alerts_for(node["id"])[0]["kind"] == "stil"
