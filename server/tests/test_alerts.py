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
