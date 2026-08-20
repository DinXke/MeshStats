"""Tests voor alarmen van een sensornode: de trap naast de polling.

TELEMETRIE IS SNMP-POLLING, EEN ALERT IS EEN SNMP-TRAP.

Die vergelijking is de reden dat dit een eigen bestand is en niet een paar
regels bij test_kanalen.py. De eigenschappen van de twee zijn tegengesteld, en
elk van die tegenstellingen is een plek waar het mis kan gaan:

* een poll komt op een interval, een trap komt éénmalig -- dus een trap die
  verloren gaat, is verloren, en dat is waarom de repeater hem meteen doorzet in
  plaats van hem in het volgende statistiekenbericht te proppen;
* een poll gaat over de node die je vraagt, een trap komt VIA iemand anders. De
  node waarover het alarm gaat staat in de payload, de doorgever in het topic, en
  wie die twee door elkaar haalt hangt een storing aan het verkeerde apparaat;
* een poll is idempotent, een trap wordt HERHAALD. De sensornode stuurt opnieuw
  tot hij een ACK krijgt en de repeater bevestigt een monitorbericht niet, dus
  één storing levert een handvol identieke berichten op;
* en een meting heeft geen lezer nodig, een alarm wel -- vandaar ``acked``, en
  vandaar dat er geen knop is die een alarm verwijdert.

Er komt geen broker aan te pas: ``handle_message`` is de functie die een bericht
verwerkt en die wordt hier rechtstreeks aangeroepen, precies zoals in
test_mqtt_ingest.py.
"""
import json

import pytest

from app import config, mqtt_ingest


@pytest.fixture
def db(tmp_path, monkeypatch):
    """De db-module tegen een verse, tijdelijke database.

    Zelfde opzet als de andere db-tests: de moduleverbinding leeft op
    moduleniveau en moet per test weggegooid en na afloop gesloten worden.
    """
    from app import db as db_module
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "test.sqlite3")
    db_module._conn = None
    # En de voorvoegselcache van de ingest leeg. Die leeft op moduleniveau en
    # onthoudt op welk topic een node zich meldde -- nuttig in productie, en hier
    # een lek van de ene test naar de andere: een node die in dit bestand een
    # alarm stuurt, zou in test_mqtt_command.py niet meer "onbekend" zijn.
    monkeypatch.setattr(mqtt_ingest, "_seen_prefix", {})
    monkeypatch.setattr(mqtt_ingest, "_seen_node", {})
    yield db_module
    if db_module._conn is not None:
        db_module._conn.close()
        db_module._conn = None


DOORGEVER = "aabbccddeeff"      # de repeater die het alarm op de broker zet
ONDERWERP = "48d7aade232b"      # de sensornode waar het alarm over gaat
TOPIC = f"meshmanager/{DOORGEVER}/alert"


def _alarm(text="hoas onbereikbaar (hoas.scheepers.one)", **extra) -> bytes:
    body = {"alert": dict({"pubkey_prefix": ONDERWERP, "name": "MeshUptime",
                           "text": text}, **extra),
            "via": DOORGEVER}
    return json.dumps(body).encode()


# --- binnenkomen --------------------------------------------------------------

def test_een_alarm_komt_binnen_op_zijn_eigen_topic(db):
    """Een derde topic naast stats en rx, en dat is het halve ontwerp.

    Een trap hoort niet te wachten op de volgende ronde, en het
    statistiekenbericht IS die ronde. Erin proppen zou een alarm precies zo traag
    maken als het pollen dat hij moet aanvullen.
    """
    node = db.get_or_create_repeater(ONDERWERP, "MeshUptime")
    assert mqtt_ingest.handle_message(TOPIC, _alarm()) is True
    rijen = db.alerts_for(node["id"])
    assert len(rijen) == 1
    assert rijen[0]["text"].startswith("hoas onbereikbaar")
    assert rijen[0]["source"] == "mesh"
    assert rijen[0]["acked"] == 0


def test_het_alarm_hangt_aan_de_node_en_niet_aan_de_doorgever(db):
    """De kern van deze weg, en de fout die er het makkelijkst in zit.

    Een sensornode publiceert zelf niets: zijn alarm komt bij een repeater binnen
    en die zet het op de broker. Het topic noemt dus de DOORGEVER. Koppelen op het
    topic zou elke storing aan die repeater hangen -- en dan staat er een melding
    over de node op het dak terwijl de kattenbak stilviel.
    """
    doorgever = db.get_or_create_repeater(DOORGEVER, "DinX-Home")
    node = db.get_or_create_repeater(ONDERWERP, "MeshUptime")
    mqtt_ingest.handle_message(TOPIC, _alarm())
    assert len(db.alerts_for(node["id"])) == 1
    assert db.alerts_for(doorgever["id"]) == []


def test_een_alarm_van_een_onbekende_node_wordt_niet_weggegooid(db):
    """Juist bij een node die hier geen rij heeft, wil je de melding hebben.

    Weggooien omdat we de afzender niet kennen is precies de verkeerde kant op
    falen: dan is de enige node waarover je niets weet ook de enige node die
    niets kan melden.
    """
    assert mqtt_ingest.handle_message(TOPIC, _alarm()) is True
    rijen = db.alerts_recent(10)
    assert len(rijen) == 1
    assert rijen[0]["repeater_id"] is None
    assert rijen[0]["node_name"] is None


def test_een_herhaald_alarm_wordt_maar_een_keer_bewaard(db):
    """De sensornode herhaalt tot hij een ACK krijgt, en die geeft niemand.

    De repeater remt dat aan zijn kant al af, en hier staat het nog een keer --
    want die rem leeft in RAM en overleeft geen herstart, en twee repeaters die
    dezelfde node horen zouden er elk een sturen.
    """
    db.get_or_create_repeater(ONDERWERP, "MeshUptime")
    for _ in range(4):
        mqtt_ingest.handle_message(TOPIC, _alarm())
    assert len(db.alerts_recent(10)) == 1


def test_een_ander_alarm_van_dezelfde_node_is_geen_herhaling(db):
    """Anders zou een tweede storing binnen vijf minuten stil verdwijnen."""
    db.get_or_create_repeater(ONDERWERP, "MeshUptime")
    mqtt_ingest.handle_message(TOPIC, _alarm("google onbereikbaar (google.com)"))
    mqtt_ingest.handle_message(TOPIC, _alarm("hoas onbereikbaar (hoas.local)"))
    assert len(db.alerts_recent(10)) == 2


def test_de_eerste_tijd_blijft_staan_bij_een_herhaling(db):
    """Het moment waarop de storing begon, en niet het moment van de laatste
    herhaling. Dat eerste is het getal waar iemand later naar kijkt."""
    node = db.get_or_create_repeater(ONDERWERP, "MeshUptime")
    db.add_alert(node["id"], "hoas onbereikbaar", source="mesh",
                 ts="2026-08-20T10:00:00Z")
    db.add_alert(node["id"], "hoas onbereikbaar", source="mesh",
                 ts="2026-08-20T10:02:00Z")
    rijen = db.alerts_for(node["id"])
    assert len(rijen) == 1 and rijen[0]["ts"] == "2026-08-20T10:00:00Z"


def test_een_alarm_zonder_tekst_wordt_geweigerd_en_niet_stil(db):
    """``handle_message`` geeft False en zet het in de teller.

    Een leeg alarm is geen alarm, en het stilzwijgend bewaren zou een rij
    opleveren waar niemand iets aan heeft -- met een badge erbij die zegt dat er
    iets aan de hand is.
    """
    voor = mqtt_ingest._state["errors"]
    assert mqtt_ingest.handle_message(TOPIC, _alarm(text="")) is False
    assert mqtt_ingest._state["errors"] == voor + 1
    assert db.alerts_recent(10) == []


# --- de tijd ------------------------------------------------------------------

def test_de_tijd_van_de_repeater_wordt_gebruikt_als_hij_klopt(db):
    db.get_or_create_repeater(ONDERWERP, "MeshUptime")
    mqtt_ingest.handle_message(TOPIC, _alarm(ts=1755691200))
    assert db.alerts_recent(1)[0]["ts"] == "2025-08-20T12:00:00Z"


def test_een_klok_uit_2024_wordt_niet_geloofd(db):
    """De sensornode heeft geen gebufferde klok en staat na elke herstart op
    15 mei 2024 -- precies het apparaat dat deze alarmen stuurt.

    Zijn tijdstempel zou een alarm van vandaag jaren in het verleden zetten,
    onder elke andere regel in de lijst, waar niemand hem ziet. Dan liever de
    ontvangsttijd van deze server, die aantoonbaar klopt.
    """
    db.get_or_create_repeater(ONDERWERP, "MeshUptime")
    mqtt_ingest.handle_message(TOPIC, _alarm(ts=1715731200))   # mei 2024
    ts = db.alerts_recent(1)[0]["ts"]
    assert ts.startswith("20") and not ts.startswith("2024")


# --- ernst en kanaal ----------------------------------------------------------

@pytest.mark.parametrize("text,verwacht", [
    ("hoas onbereikbaar (hoas.local)", "hoog"),
    ("printer gemeld als neer", "hoog"),
    ("printer: geen melding meer (>900s)", "hoog"),
    ("TEST hoas onbereikbaar (hoas.local) -- dit is een SIMULATIE, geen echte storing", "laag"),
    ("hoas weer bereikbaar", "laag"),
    ("iets waar geen woord van deze lijst in staat", None),
])
def test_de_ernst_komt_uit_de_tekst_of_ontbreekt(text, verwacht):
    """Een kleine lijst woorden en geen poging tot taalbegrip.

    En de volgorde is de hele functie: een TESTalarm bevat het woord
    "onbereikbaar" ook, want dat is de bedoeling van een test -- hij leest als het
    echte bericht. Andersom toetsen zou elke simulatie als storing melden, en dan
    is de test onbruikbaar geworden door de weergave ervan.
    """
    assert mqtt_ingest.alert_severity(text) == verwacht


def test_een_verzonnen_ernst_is_erger_dan_geen():
    """Wat niet in de lijst past krijgt NULL, en de tekst staat er voluit naast."""
    assert mqtt_ingest.alert_severity("") is None


@pytest.mark.parametrize("text,verwacht", [
    ("kanaal 6: hoas onbereikbaar", 6),
    ("channel 12 down", 12),
    ("ch 3 stil", 3),
    ("hoas onbereikbaar (hoas.local)", None),
    ("kanaal 999 bestaat niet", None),
])
def test_het_kanaal_komt_uit_de_tekst_als_het_erin_staat(text, verwacht):
    """De alarmen van MeshUptime noemen de NAAM van een dienst en niet zijn
    kanaal, dus dit vindt meestal niets -- en dat is waarom het veld mag
    ontbreken in plaats van geraden te worden."""
    assert mqtt_ingest.alert_channel(text) == verwacht


# --- bevestigen ---------------------------------------------------------------

def test_bevestigen_haalt_het_alarm_niet_weg(db):
    """Er is met opzet geen route die een alarm verwijdert.

    Een melding die je zonder spoor kunt wegklikken is een melding die achteraf
    niet meer na te vertellen is. Opruimen doet de bewaartermijn.
    """
    node = db.get_or_create_repeater(ONDERWERP, "MeshUptime")
    alert_id = db.add_alert(node["id"], "hoas onbereikbaar", source="mesh")
    assert db.ack_alert(alert_id) is True
    rijen = db.alerts_for(node["id"])
    assert len(rijen) == 1 and rijen[0]["acked"] == 1


def test_twee_keer_bevestigen_is_geen_fout_maar_ook_geen_wijziging(db):
    """Zodat een dubbele klik of een herladen formulier niets stils doet."""
    node = db.get_or_create_repeater(ONDERWERP, "MeshUptime")
    alert_id = db.add_alert(node["id"], "hoas onbereikbaar", source="mesh")
    assert db.ack_alert(alert_id) is True
    assert db.ack_alert(alert_id) is False


def test_alles_van_een_node_in_een_keer_bevestigen(db):
    """Een node die een uur onbereikbaar was levert tientallen regels op.

    Die één voor één wegklikken betekent dat niemand het doet -- en dan zegt de
    badge over een week nog steeds iets over vorige dinsdag.
    """
    node = db.get_or_create_repeater(ONDERWERP, "MeshUptime")
    ander = db.get_or_create_repeater("112233445566", "Andere node")
    for n in range(3):
        db.add_alert(node["id"], f"dienst {n} onbereikbaar", source="mesh")
    db.add_alert(ander["id"], "iets anders", source="mesh")
    assert db.ack_alerts_for(node["id"]) == 3
    assert db.alerts_open_count(node["id"]) == 0
    # En de andere node blijft ongemoeid: bevestigen gaat over één node.
    assert db.alerts_open_count(ander["id"]) == 1


def test_de_teller_per_node_komt_uit_een_query(db):
    """Eén query voor alle nodes en niet één per node: bij twintig nodes is dat
    twintig keer dezelfde vraag voor één scherm."""
    a = db.get_or_create_repeater(ONDERWERP, "MeshUptime")
    b = db.get_or_create_repeater("112233445566", "Andere node")
    db.add_alert(a["id"], "een", source="mesh")
    db.add_alert(a["id"], "twee", source="mesh")
    db.add_alert(b["id"], "drie", source="mesh")
    assert db.alerts_open_by_node() == {a["id"]: 2, b["id"]: 1}
    assert db.alerts_open_count() == 3


def test_een_alarm_zonder_node_telt_niet_mee_in_de_badge_per_node(db):
    """Anders zou er een badge zonder pagina bestaan. Hij staat wél in het totaal
    en in de lijst -- daar hoort hij te blijven staan."""
    assert db.add_alert(None, "van een onbekende node", source="mesh") > 0
    assert db.alerts_open_by_node() == {}
    assert db.alerts_open_count() == 1
    assert len(db.alerts_recent(10)) == 1


# --- de route -----------------------------------------------------------------

def test_de_beheerroute_bevestigt_en_legt_het_vast(db, monkeypatch):
    """Via de route en niet alleen via db, want daar zit de rechtencontrole en
    het audittrail -- en een handeling zonder spoor is precies wat dit project
    elders overal vermijdt."""
    from app import routes_admin

    node = db.get_or_create_repeater(ONDERWERP, "MeshUptime")
    alert_id = db.add_alert(node["id"], "hoas onbereikbaar", source="mesh")
    gelogd = []
    monkeypatch.setattr(routes_admin, "_rep_or_404",
                        lambda request, rid: node)
    monkeypatch.setattr(routes_admin, "require_perm",
                        lambda request, actie, rep=None: "beheerder")
    monkeypatch.setattr(routes_admin, "check_csrf", lambda request, csrf: None)
    monkeypatch.setattr(routes_admin, "_noteer",
                        lambda *a, **k: gelogd.append(k.get("detail", "")))
    routes_admin.ack_alerts(None, node["id"], alert_id=alert_id, csrf="x")
    assert db.alerts_open_count(node["id"]) == 0
    assert gelogd and "bevestigd" in gelogd[0]


# --- de afleiding uit de IP-poll ------------------------------------------------
#
# De mesh-schakel node->repeater is defect bevestigd (heen werkt, terug niet;
# zes softwareverdenkingen weerlegd -- MeshUptime docs/openstaand.md), dus zolang
# dat ter plekke niet gerepareerd is komt er over het mesh niets binnen. De
# IP-poll ziet elke ronde de volledige toestand, en een OVERGANG daarin is
# dezelfde gebeurtenis als het alarm dat de node gestuurd zou hebben. Wat deze
# reeks vastlegt zijn de vier dingen die daarbij stil kunnen misgaan: een golf na
# een herstart, een gemiste overgang rond een node-herstart, een dubbele melding
# zodra het mesh ooit weer meedoet, en een vorm die van de MQTT-rijen afwijkt
# zodat webpush of /meshmoni er iets anders van maakt.

from app import sensornode


def _status(**over):
    """Een /status.json zoals de node hem stuurt, met kanaal 5 en 6 als dienst."""
    basis = {
        "fw": "1.4.0", "mains": 1, "volts": "4.139",
        "mon": [
            {"ch": 5, "n": "google", "h": "google.com", "st": "op",
             "ms": 37, "k": "ping"},
            {"ch": 6, "n": "hoas", "h": "(gemeld)", "st": "op",
             "ms": 12, "k": "gemeld"},
        ],
    }
    basis.update(over)
    return basis


def _mon(ch, st, k="ping", n=None, h=None):
    return {"ch": ch, "n": n or ("google" if ch == 5 else "hoas"),
            "h": h or ("google.com" if k == "ping" else "(gemeld)"),
            "st": st, "ms": 1, "k": k}


@pytest.fixture
def schone_toestand(monkeypatch):
    """De vorige-toestandtabel leeg, per test. Die leeft op moduleniveau -- met
    opzet, zie het blok boven _toestand -- en zou anders van test naar test
    lekken zoals hij in productie van ronde naar ronde hoort te dragen."""
    monkeypatch.setattr(sensornode, "_toestand", {})


def test_de_eerste_ronde_ijkt_en_meldt_niets(db, schone_toestand):
    """De herstartregel, en hij is de helft van het ontwerp.

    De vorige toestand leeft in het geheugen, dus een serverherstart wist de
    vergelijkingsbasis. Zou de eerste ronde daarna gewoon melden, dan geeft elke
    deploy een golf "nieuwe" alarmen voor toestanden die al dagen zo waren -- en
    een alarmkanaal dat bij elke deploy blaft, leest niemand na een week nog.
    Dus: de eerste ronde legt alleen vast, ook als er iets neer staat.
    """
    node = db.get_or_create_repeater(ONDERWERP, "MeshUptime")
    data = _status(mains=0, mon=[_mon(5, "neer"), _mon(6, "stil", k="gemeld")])
    assert sensornode._derive_alerts(node["id"], data) == 0
    assert db.alerts_recent(10) == []


def test_op_naar_neer_geeft_een_alarm_in_de_firmwarevorm(db, schone_toestand):
    """De tekst spiegelt monitorAlertText, en dat is de halve ontdubbeling:
    komt hetzelfde feit ooit alsnog over het mesh binnen, dan begint dat bericht
    met dezelfde dienstnaam."""
    node = db.get_or_create_repeater(ONDERWERP, "MeshUptime")
    sensornode._derive_alerts(node["id"], _status())
    aantal = sensornode._derive_alerts(node["id"], _status(mon=[
        _mon(5, "neer"), _mon(6, "op", k="gemeld")]))
    assert aantal == 1
    rij = db.alerts_for(node["id"])[0]
    assert rij["text"] == "google onbereikbaar (google.com)"
    assert rij["kind"] == "neer" and rij["severity"] == "hoog"
    assert rij["source"] == "ip" and rij["channel"] == 5


def test_herstel_is_een_lagere_ernst_net_als_in_de_firmware(db, schone_toestand):
    node = db.get_or_create_repeater(ONDERWERP, "MeshUptime")
    sensornode._derive_alerts(node["id"], _status(mon=[_mon(5, "neer")]))
    sensornode._derive_alerts(node["id"], _status(mon=[_mon(5, "op")]))
    rij = db.alerts_for(node["id"])[0]
    assert rij["text"] == "google weer bereikbaar"
    assert rij["kind"] == "op" and rij["severity"] == "laag"


def test_een_melder_die_stilvalt_is_een_andere_boodschap_dan_neer(db, schone_toestand):
    """Bij 'neer' ligt de DIENST plat; bij 'stil' is de MELDER stil en weten wij
    niets. Wie dat door elkaar haalt, gaat de verkeerde kant op zoeken -- de
    firmware maakt precies dit onderscheid en de afleiding hoort het te houden."""
    node = db.get_or_create_repeater(ONDERWERP, "MeshUptime")
    sensornode._derive_alerts(node["id"], _status(mon=[_mon(6, "op", k="gemeld")]))
    sensornode._derive_alerts(node["id"], _status(mon=[_mon(6, "stil", k="gemeld")]))
    rij = db.alerts_for(node["id"])[0]
    assert rij["text"] == "hoas: geen melding meer"
    assert rij["kind"] == "stil" and rij["severity"] == "hoog"
    # En het herstel na een stilte zegt dat de MELDINGEN terug zijn, niet dat
    # een dienst hersteld is die misschien nooit plat lag.
    sensornode._derive_alerts(node["id"], _status(mon=[_mon(6, "op", k="gemeld")]))
    assert db.alerts_for(node["id"])[0]["text"] == "hoas meldt weer"


def test_de_netvoeding_wisselt_en_dat_is_een_alarm(db, schone_toestand):
    node = db.get_or_create_repeater(ONDERWERP, "MeshUptime")
    sensornode._derive_alerts(node["id"], _status(mains=1))
    assert sensornode._derive_alerts(node["id"], _status(mains=0)) == 1
    rij = db.alerts_for(node["id"])[0]
    assert rij["text"].startswith("netvoeding weg")
    assert rij["kind"] == "neer" and rij["severity"] == "hoog"
    assert sensornode._derive_alerts(node["id"], _status(mains=1)) == 1
    assert db.alerts_for(node["id"])[0]["text"] == "netvoeding terug"


def test_geen_overgang_geen_alarm(db, schone_toestand):
    """Een poll is geen gebeurtenis. Twintig rondes met dezelfde neer-toestand
    zijn een storing, niet twintig."""
    node = db.get_or_create_repeater(ONDERWERP, "MeshUptime")
    sensornode._derive_alerts(node["id"], _status())
    data = _status(mon=[_mon(5, "neer")])
    assert sensornode._derive_alerts(node["id"], data) == 1
    for _ in range(5):
        assert sensornode._derive_alerts(node["id"], data) == 0
    assert len(db.alerts_for(node["id"])) == 1


def test_onbekend_wist_de_laatste_uitspraak_niet(db, schone_toestand):
    """Na een herstart van de NODE staan alle kanalen even op '?'.

    'op -> ? -> neer' zou zonder deze regel in twee stille stappen uiteenvallen:
    naar onbekend is geen storing, en vanuit onbekend zou de ijkregel gelden. De
    afleiding onthoudt daarom de laatst BEKENDE toestand en vergelijkt neer met
    op -- en meldt de storing alsnog.
    """
    node = db.get_or_create_repeater(ONDERWERP, "MeshUptime")
    sensornode._derive_alerts(node["id"], _status(mon=[_mon(5, "op")]))
    assert sensornode._derive_alerts(node["id"], _status(mon=[_mon(5, "?")])) == 0
    assert sensornode._derive_alerts(node["id"], _status(mon=[_mon(5, "pauze")])) == 0
    assert sensornode._derive_alerts(node["id"], _status(mon=[_mon(5, "neer")])) == 1


def test_een_nieuw_kanaal_ijkt_eerst(db, schone_toestand):
    """Een monitor die net is aangemaakt en meteen 'neer' meet, kan een tikfout
    in een adres zijn -- geen storing om iemand voor te wekken."""
    node = db.get_or_create_repeater(ONDERWERP, "MeshUptime")
    sensornode._derive_alerts(node["id"], _status(mon=[_mon(5, "op")]))
    data = _status(mon=[_mon(5, "op"), _mon(9, "neer", n="nieuw", h="x.local")])
    assert sensornode._derive_alerts(node["id"], data) == 0


def test_dezelfde_gebeurtenis_via_het_mesh_geeft_geen_tweede_melding(db, schone_toestand):
    """De kruisontdubbeling, in de richting die er straks toe doet.

    Wordt de RF-schakel ooit gerepareerd, dan komt dezelfde storing ook als
    mesh-alarm binnen -- met een tekst die net verschilt en zonder kanaalnummer.
    De sleutel is daarom (node, soort, dienstnaam), en de winst is precies een
    ding: geen tweede pushmelding voor een gebeurtenis.
    """
    node = db.get_or_create_repeater(ONDERWERP, "MeshUptime")
    sensornode._derive_alerts(node["id"], _status())
    sensornode._derive_alerts(node["id"], _status(mon=[_mon(5, "neer")]))
    assert len(db.alerts_for(node["id"])) == 1
    # Het mesh-alarm voor hetzelfde feit, minuten later, met de firmwaretekst.
    assert mqtt_ingest.handle_message(
        TOPIC, _alarm("google onbereikbaar (google.com)")) is True
    assert len(db.alerts_for(node["id"])) == 1


def test_de_ontdubbeling_werkt_ook_andersom(db, schone_toestand):
    """Mesh eerst (seconden na het feit), dan de poll die hetzelfde ziet."""
    node = db.get_or_create_repeater(ONDERWERP, "MeshUptime")
    sensornode._derive_alerts(node["id"], _status())
    mqtt_ingest.handle_message(TOPIC, _alarm("google onbereikbaar (google.com)"))
    assert len(db.alerts_for(node["id"])) == 1
    assert sensornode._derive_alerts(
        node["id"], _status(mon=[_mon(5, "neer")])) == 0
    assert len(db.alerts_for(node["id"])) == 1


def test_twee_verschillende_diensten_dedupen_niet(db, schone_toestand):
    """De sleutel is de dienst, niet de node: twee storingen tegelijk zijn twee
    meldingen. Anders verzwijgt de ontdubbeling precies de tweede storing."""
    node = db.get_or_create_repeater(ONDERWERP, "MeshUptime")
    sensornode._derive_alerts(node["id"], _status())
    aantal = sensornode._derive_alerts(node["id"], _status(mon=[
        _mon(5, "neer"), _mon(6, "stil", k="gemeld")]))
    assert aantal == 2


def test_neer_en_herstel_dedupen_elkaar_niet(db, schone_toestand):
    """Zelfde dienst, andere soort: het herstel hoort er wel doorheen, ook
    binnen het venster -- dat is het bericht waar iemand op wacht."""
    node = db.get_or_create_repeater(ONDERWERP, "MeshUptime")
    sensornode._derive_alerts(node["id"], _status())
    sensornode._derive_alerts(node["id"], _status(mon=[_mon(5, "neer")]))
    assert sensornode._derive_alerts(node["id"], _status(mon=[_mon(5, "op")])) == 1
    assert len(db.alerts_for(node["id"])) == 2


def test_de_ip_rijen_hebben_dezelfde_vorm_als_de_mesh_rijen(db, schone_toestand):
    """Webpush en /meshmoni lezen de tabel en niets anders, dus de twee bronnen
    moeten dezelfde kolommen vullen: text, severity, kind, ts, acked. Wijkt de
    vorm af, dan is dat geen fout die iemand ziet -- de melding komt gewoon
    anders of niet op een telefoon aan."""
    node = db.get_or_create_repeater(ONDERWERP, "MeshUptime")
    sensornode._derive_alerts(node["id"], _status())
    sensornode._derive_alerts(node["id"], _status(mon=[_mon(5, "neer")]))
    mqtt_ingest.handle_message(TOPIC, _alarm("hoas onbereikbaar (hoas.local)"))
    ip_rij, mesh_rij = None, None
    for r in db.alerts_for(node["id"]):
        if r["source"] == "ip":
            ip_rij = r
        elif r["source"] == "mesh":
            mesh_rij = r
    assert ip_rij is not None and mesh_rij is not None
    assert set(ip_rij.keys()) == set(mesh_rij.keys())
    assert ip_rij["severity"] == mesh_rij["severity"] == "hoog"
    assert ip_rij["kind"] == mesh_rij["kind"] == "neer"
    assert ip_rij["acked"] == mesh_rij["acked"] == 0
    # En de ene extra van de IP-weg: het kanaalnummer, dat een mesh-tekst niet
    # draagt. Meer weten is geen vormverschil.
    assert ip_rij["channel"] == 5 and mesh_rij["channel"] is None


def test_de_soort_van_een_mesh_alarm_komt_uit_de_tekst():
    """Dezelfde soortnamen als de afleiding, want dat is de dedupsleutel."""
    assert mqtt_ingest.alert_kind("hoas onbereikbaar (hoas.local)") == "neer"
    assert mqtt_ingest.alert_kind("hoas gemeld als neer") == "neer"
    assert mqtt_ingest.alert_kind("netvoeding weg, node op batterij (3.9V)") == "neer"
    assert mqtt_ingest.alert_kind("hoas: geen melding meer (>900s)") == "stil"
    assert mqtt_ingest.alert_kind("hoas weer bereikbaar na 3 min") == "op"
    assert mqtt_ingest.alert_kind("hoas weer op gemeld na 3 min") == "op"
    assert mqtt_ingest.alert_kind("hoas meldt weer (was 5 min stil)") == "op"
    assert mqtt_ingest.alert_kind("netvoeding terug na 2 min") == "op"
    # Een simulatie leest als het echte bericht -- dat is de bedoeling van een
    # test -- en mag daarom nooit een echte melding onderdrukken of andersom.
    assert mqtt_ingest.alert_kind(
        "TEST hoas onbereikbaar (x) -- dit is een SIMULATIE, geen echte storing") is None


def test_poll_neemt_de_afleiding_mee(db, schone_toestand, monkeypatch):
    """Door poll() zelf en niet alleen door de losse functie: de plek in de
    volgorde (na de metingen) is deel van de afspraak -- wie op de melding klikt
    vindt de cijfers die erbij horen er al."""
    from app import firmware, nodeconfig

    monkeypatch.setattr(firmware, "NODE_USER", "admin")
    node = db.get_or_create_repeater(ONDERWERP, "MeshUptime")
    db.set_sensor_host(node["id"], "192.168.110.160", by_admin=True)
    rep = db.qone("SELECT * FROM repeaters WHERE id=?", (node["id"],))

    antwoorden = {"status": _status()}

    class _Resp:
        def __init__(self, body):
            self._body = body

        def read(self):
            return json.dumps(self._body).encode()

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def nep(host, path, data=None, timeout=None):
        if path == "/status.json":
            return _Resp(antwoorden["status"])
        if path == "/acl.json":
            return _Resp({"acl": [], "nb": []})
        raise AssertionError(path)

    monkeypatch.setattr(nodeconfig, "_open", nep)
    assert sensornode.poll(rep)["alerts"] == 0        # ijkronde
    antwoorden["status"] = _status(mon=[_mon(5, "neer"), _mon(6, "op", k="gemeld")])
    uit = sensornode.poll(rep)
    assert uit["ok"] is True and uit["alerts"] == 1
    assert db.alerts_for(node["id"])[0]["source"] == "ip"


# --- simulaties in de afleiding --------------------------------------------------
#
# Gevonden bij de eerste end-to-end-test: een via de simulatieknop geforceerde
# 'neer' werd een kale echte melding, ernst hoog, zonder enig teken dat het een
# oefening was. De afleiding werkt uit de TOESTAND en omzeilde daarmee de
# tekstmarkering die het mesh-pad wel heeft (de firmware zet TEST/SIMULATIE in
# het bericht zelf). De node meldt de forcering in /status.json (sm per regel),
# en deze reeks legt vast wat de afleiding daarmee hoort te doen: labelen zonder
# de ernst te verlagen, buiten de kruisontdubbeling blijven, en het einde van een
# oefening niet als echt herstel verkopen.


def test_een_gesimuleerde_neer_wordt_gelabeld_en_blijft_hoog(db, schone_toestand):
    """De tekst zegt dat het een oefening is; de ernst blijft staan.

    Dat tweede is geen slordigheid maar het punt van de knop: de gebruiker test
    juist of een HOGE melding doorkomt, en een oefening die als 'laag'
    binnenkomt test iets anders dan het echte geval.
    """
    node = db.get_or_create_repeater(ONDERWERP, "MeshUptime")
    sensornode._derive_alerts(node["id"], _status())
    aantal = sensornode._derive_alerts(node["id"], _status(mon=[
        dict(_mon(5, "neer"), sm="down")]))
    assert aantal == 1
    rij = db.alerts_for(node["id"])[0]
    assert rij["text"] == "google onbereikbaar (google.com) (simulatie)"
    assert rij["severity"] == "hoog"
    # kind=None: dezelfde keuze als mqtt_ingest.alert_kind voor TEST-teksten,
    # zodat een oefening nooit aan de kruisontdubbeling meedoet.
    assert rij["kind"] is None


def test_een_echte_neer_vlak_na_een_gesimuleerde_wordt_niet_onderdrukt(db, schone_toestand):
    """De reden dat een simulatie kind=None krijgt.

    Oefening om 10:00, echte storing om 10:05: met kind='neer' op de oefenrij
    zou de kruisontdubbeling de echte melding als herhaling aanzien -- en dan
    heeft de test het alarmkanaal zelf onklaar gemaakt. Volgorde hier: sim neer,
    sim af (herstel-artefact), echte neer. Drie rijen, waarvan alleen de laatste
    een echte 'neer' met kind is.
    """
    node = db.get_or_create_repeater(ONDERWERP, "MeshUptime")
    sensornode._derive_alerts(node["id"], _status())
    sensornode._derive_alerts(node["id"], _status(mon=[
        dict(_mon(5, "neer"), sm="down")]))
    sensornode._derive_alerts(node["id"], _status(mon=[_mon(5, "op")]))
    assert sensornode._derive_alerts(
        node["id"], _status(mon=[_mon(5, "neer")])) == 1
    rijen = db.alerts_for(node["id"])
    assert len(rijen) == 3
    assert rijen[0]["text"] == "google onbereikbaar (google.com)"
    assert rijen[0]["kind"] == "neer" and "simulatie" not in rijen[0]["text"]


def test_het_einde_van_een_simulatie_is_geen_echt_herstel(db, schone_toestand):
    """De spiegel van het label: loopt de forcering af en blijkt de dienst
    gewoon op, dan is dat "herstel" een artefact van de oefening. Een kaal
    "weer bereikbaar" voor iets dat nooit stuk was, leert de lezer het verkeerde."""
    node = db.get_or_create_repeater(ONDERWERP, "MeshUptime")
    sensornode._derive_alerts(node["id"], _status())
    sensornode._derive_alerts(node["id"], _status(mon=[
        dict(_mon(5, "neer"), sm="down")]))
    assert sensornode._derive_alerts(
        node["id"], _status(mon=[_mon(5, "op")])) == 1
    rij = db.alerts_for(node["id"])[0]
    assert rij["text"] == "google weer bereikbaar (simulatie)"
    assert rij["severity"] == "laag" and rij["kind"] is None


def test_een_simulatie_die_een_echte_storing_maskeerde_meldt_alsnog(db, schone_toestand):
    """Forcering op 'neer' terwijl de dienst ONDERTUSSEN echt neerging: als de
    forcering afloopt verandert de toestand niet ('neer' blijft 'neer') maar de
    herkomst wel -- van beweerd naar gemeten. Dat is het moment waarop we leren
    dat het geen oefening meer is, en dat verdient een echte melding."""
    node = db.get_or_create_repeater(ONDERWERP, "MeshUptime")
    sensornode._derive_alerts(node["id"], _status())
    sensornode._derive_alerts(node["id"], _status(mon=[
        dict(_mon(5, "neer"), sm="down")]))
    assert sensornode._derive_alerts(
        node["id"], _status(mon=[_mon(5, "neer")])) == 1
    rij = db.alerts_for(node["id"])[0]
    assert rij["text"] == "google onbereikbaar (google.com)"
    assert rij["kind"] == "neer" and rij["severity"] == "hoog"


def test_een_forcering_zonder_zichtbare_overgang_meldt_niets(db, schone_toestand):
    """Forceren naar de toestand die er al was, verandert niets observeerbaars."""
    node = db.get_or_create_repeater(ONDERWERP, "MeshUptime")
    sensornode._derive_alerts(node["id"], _status())
    assert sensornode._derive_alerts(node["id"], _status(mon=[
        dict(_mon(5, "op"), sm="up")])) == 0
    assert sensornode._derive_alerts(node["id"], _status(mon=[_mon(5, "op")])) == 0


def test_een_gesimuleerde_netvoedingsuitval_wordt_ook_gelabeld(db, schone_toestand):
    """Dezelfde regels voor het vaste kanaal. De forcering is aan het kale
    mains-veld niet te zien; hij staat in de sm van de vaste kanaalregels."""
    node = db.get_or_create_repeater(ONDERWERP, "MeshUptime")
    vast = {"ch": 2, "n": "netvoeding", "h": "klemspanning", "st": "uit",
            "k": "vast", "sm": "down"}
    sensornode._derive_alerts(node["id"], _status())
    aantal = sensornode._derive_alerts(node["id"], _status(
        mains=0, mon=[vast, _mon(5, "op")]))
    assert aantal == 1
    rij = db.alerts_for(node["id"])[0]
    assert rij["text"] == "netvoeding weg, node op batterij (simulatie)"
    assert rij["severity"] == "hoog" and rij["kind"] is None
