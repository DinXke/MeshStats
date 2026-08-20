"""Tests voor kanaalmetingen van een sensornode en de namen die erbij horen.

Waarom dit een eigen bestand verdient. Een sensornode antwoordt in CayenneLPP, en
dat formaat draagt drie dingen: kanaalnummer, type, waarde. Geen naam. Alles wat
hier getest wordt volgt uit die ene beperking, en elk van die gevolgen is een
plek waar een meting stil kan verdwijnen of stil bij de verkeerde dienst kan
belanden:

* **twee soorten op één kanaal** -- een dienst komt binnen als een switch
  (bereikbaar ja/nee) én een generic sensor (responstijd) onder hetzelfde nummer.
  Een opslag die "één waarde per kanaal" veronderstelt gooit de helft weg, en dat
  is de fout die deze tests als eerste moeten uitsluiten;
* **een naamloos kanaal blijft zichtbaar** -- als "kanaal N", want een meting
  waarvan we de naam niet kennen is nog steeds een meting;
* **de naam hangt aan het nummer** en niet aan een rangnummer of een positie in
  een lijst, want een verschuivend nummer laat elke bewaarde naam naar de
  verkeerde dienst wijzen zonder ergens een fout op te leveren.

De routefuncties worden rechtstreeks aangeroepen en niet via een HTTP-client: er
hangt geen middleware tussen die deze antwoorden verandert.
"""
import pytest

from app import config, metrics


@pytest.fixture
def db(tmp_path, monkeypatch):
    """De db-module tegen een verse, tijdelijke database.

    Zelfde opzet als test_db.py: de moduleverbinding leeft op moduleniveau en
    moet per test weggegooid en na afloop gesloten worden, anders lekken tests in
    elkaar en kan Windows de tijdelijke file niet opruimen.
    """
    from app import db as db_module
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "test.sqlite3")
    db_module._conn = None
    yield db_module
    if db_module._conn is not None:
        db_module._conn.close()
        db_module._conn = None


# Wat een MeshUptime-node op het mesh zet, zoals de repeaterfirmware het na
# decodering doorstuurt. Kanaal 1 spanning, 2/3/4 de vaste toestanden, en vanaf 5
# per dienst een switch mét een generic sensor -- twee records op één nummer.
TELEMETRIE = {
    "online": True,
    "ch1_voltage": 4.11,
    "ch2_switch": 1,
    "ch3_switch": 0,
    "ch4_switch": 1,
    "ch5_switch": 1,
    "ch5_generic": 11,
    "ch6_switch": 1,
    "ch6_generic": 12,
}


def _node(db, metrics_dict=None):
    rep = db.get_or_create_repeater("aabbccddeeff", "Uptimenode")
    # Een nieuwe node komt verborgen binnen, en de publieke pagina bestaat dus
    # niet tot iemand hem zichtbaar zet. Hier expliciet aanzetten: de tests
    # hieronder gaan over wat er op die pagina staat, niet over of ze bestaat.
    db.execute("UPDATE repeaters SET is_public=1 WHERE id=?", (rep["id"],))
    db.ingest(rep["id"], db.utcnow(),
              TELEMETRIE if metrics_dict is None else metrics_dict, None)
    return db.qone("SELECT * FROM repeaters WHERE id=?", (rep["id"],))


# --- het wireformaat ---------------------------------------------------------
#
# WAT DIT WEL EN NIET TEST. De echte decoder staat in C++ in de repeaterfirmware
# (monDecodeTelemetry in MeshStatsNet.cpp) en draait op de node, niet hier. De
# functie hieronder is een SPIEGEL van die code: dezelfde stappen, dezelfde
# vermenigvuldigers, dezelfde metricnamen. Wat dit vastlegt is dus het CONTRACT
# tussen de firmware en deze server -- welke bytes welke metricnaam en welke
# waarde moeten worden -- en niet dat de firmware zelf goed gebouwd is.
#
# Waarom dat het testen waard is: het contract is het stuk dat stil kan
# verschuiven. Verandert iemand aan één kant een vermenigvuldiger, een
# byteorde of een veldnaam, dan blijven beide kanten op zichzelf werken en gaan
# alleen de cijfers niet meer over hetzelfde. Precies de foutklasse waar de rest
# van dit bestand ook over gaat.
#
# LPP IS BIG-ENDIAN, anders dan elk ander meerbyte-veld in MeshCore. Dat is de
# makkelijkste plek in het hele systeem om de byteorde te verhaspelen, en daarom
# staan hier echte bytes en geen hulpfunctie die ze opbouwt.

LPP_VOLTAGE = 116
LPP_GENERIC_SENSOR = 100
LPP_TEMPERATURE = 103
LPP_SWITCH = 142
LPP_LUMINOSITY = 101

# Wat de firmware over de stroom moet stappen per type, als het geen type is dat
# ze bewaart. Zelfde tabel als lppValueLen() in de firmware.
_VALUE_LEN = {136: 9, 240: 8, 134: 6, 113: 6, 100: 4, 118: 4, 130: 4, 131: 4,
              133: 4, 135: 3, 2: 2, 3: 2, 101: 2, 103: 2, 125: 2, 115: 2,
              121: 2, 116: 2, 117: 2, 132: 2, 128: 2}


def decodeer(reply: bytes) -> dict:
    """Spiegel van monDecodeTelemetry: bytes in, metricnamen uit.

    De eerste vier byte zijn de weerkaatste tijdstempel van het verzoek en horen
    overgeslagen te worden; daarna komt de LPP-stroom.
    """
    out: dict = {}
    buf, pos = reply[4:], 0
    while pos + 2 < len(buf):
        channel, lpp_type = buf[pos], buf[pos + 1]
        if channel == 0:
            break                       # kanaal 0 sluit de stroom af
        pos += 2
        vlen = _VALUE_LEN.get(lpp_type, 1)
        if pos + vlen > len(buf):
            break                       # afgekapt record: stoppen, niet raden
        raw = buf[pos:pos + vlen]
        if lpp_type == LPP_VOLTAGE:
            out[f"ch{channel}_voltage"] = int.from_bytes(raw, "big") / 100
        elif lpp_type == LPP_TEMPERATURE:
            out[f"ch{channel}_temperature"] = \
                int.from_bytes(raw, "big", signed=True) / 10
        elif lpp_type == LPP_SWITCH:
            out[f"ch{channel}_switch"] = 1 if raw[0] else 0
        elif lpp_type == LPP_GENERIC_SENSOR:
            out[f"ch{channel}_generic"] = int.from_bytes(raw, "big")
        pos += vlen
    return out


# Een antwoord zoals een MeshUptime-node het stuurt.
ANTWOORD = bytes([
    0xDE, 0xAD, 0xBE, 0xEF,                 # weerkaatste tijdstempel
    1, LPP_VOLTAGE, 0x01, 0x9B,             # 411 / 100 = 4,11 V
    2, LPP_SWITCH, 1,                       # netvoeding
    3, LPP_SWITCH, 0,                       # batterijvoeding
    4, LPP_SWITCH, 1,                       # wifi online
    5, LPP_SWITCH, 1,
    5, LPP_GENERIC_SENSOR, 0, 0, 0, 11,     # zelfde kanaal, ander type
    6, LPP_SWITCH, 1,
    6, LPP_GENERIC_SENSOR, 0, 0, 0, 12,
])


def test_twee_types_op_een_kanaal_geven_twee_metrics():
    """De kern: kanaal 5 draagt een toestand én een responstijd.

    Zou de naam alleen uit het kanaal bestaan, dan overschreef de tweede de
    eerste en was de helft van wat de node zei stil weg.
    """
    gedecodeerd = decodeer(ANTWOORD)
    assert gedecodeerd["ch5_switch"] == 1
    assert gedecodeerd["ch5_generic"] == 11
    assert gedecodeerd["ch6_generic"] == 12


def test_de_hele_kanaalkaart_van_meshuptime():
    """Precies de kaart uit het ontwerp van het zusterproject."""
    assert decodeer(ANTWOORD) == {
        "ch1_voltage": 4.11,
        "ch2_switch": 1, "ch3_switch": 0, "ch4_switch": 1,
        "ch5_switch": 1, "ch5_generic": 11,
        "ch6_switch": 1, "ch6_generic": 12,
    }


def test_lpp_is_big_endian():
    """Elk ander meerbyte-veld in MeshCore is little-endian; dit niet.

    Met de bytes omgedraaid zou 4,11 V als 397,07 V gelezen worden -- een fout die
    opvalt. Bij een pingtijd valt hij niet op, en dat is het gevaar.
    """
    assert decodeer(bytes([0, 0, 0, 0, 1, LPP_VOLTAGE, 0x01, 0x9B])) \
        == {"ch1_voltage": 4.11}
    assert decodeer(bytes([0, 0, 0, 0, 5, LPP_GENERIC_SENSOR, 0, 0, 1, 0])) \
        == {"ch5_generic": 256}


def test_negatieve_temperatuur_blijft_negatief():
    """Temperatuur is signed, spanning en generic sensor niet."""
    assert decodeer(bytes([0, 0, 0, 0, 7, LPP_TEMPERATURE, 0xFF, 0x9C])) \
        == {"ch7_temperature": -10.0}


def test_onbekend_type_wordt_overgeslagen_zonder_de_stroom_te_ontsporen():
    """Een verkeerde overslaglengte verschuift alles wat erna komt.

    Dat is de stille variant van deze fout: er verdwijnt niet één meting, maar
    alles achter het onbekende record komt op een verkeerd kanaal terecht.
    """
    reply = bytes([0, 0, 0, 0,
                   8, LPP_LUMINOSITY, 0x01, 0x00,     # 2 byte, niet bewaard
                   9, LPP_GENERIC_SENSOR, 0, 0, 0, 42])
    assert decodeer(reply) == {"ch9_generic": 42}


def test_afgekapt_record_wordt_niet_geraden():
    assert decodeer(bytes([0, 0, 0, 0, 5, LPP_GENERIC_SENSOR, 0, 0])) == {}
    assert decodeer(bytes([0, 0, 0, 0])) == {}


def test_kanaal_nul_sluit_de_stroom_af():
    reply = bytes([0, 0, 0, 0, 5, LPP_SWITCH, 1, 0, 0, 0, 6, LPP_SWITCH, 1])
    assert decodeer(reply) == {"ch5_switch": 1}


def test_gedecodeerde_bytes_komen_tot_in_de_databank(db):
    """De hele keten in één test: bytes -> metricnamen -> opslag -> label.

    Dit is wat 'volledig door de keten' betekent, en het is de enige test hier die
    de radiokant en de sitekant aan elkaar knoopt.
    """
    rep = db.get_or_create_repeater("aabbccddeeff", "Uptimenode")
    db.ingest(rep["id"], db.utcnow(), decodeer(ANTWOORD), None)
    db.set_channel_name(rep["id"], 6, "google", "ms")

    latest = db.latest_for(rep["id"])
    assert latest["ch6_switch"]["value"] == 1.0
    assert latest["ch6_generic"]["value"] == 12.0
    section, label, unit, _ = metrics.metric_info("ch6_generic")
    assert section == "channels"
    naam = db.channel_names_for(rep["id"])[6]
    assert metrics.channel_label(6, "generic", naam["name"]) == "google — meetwaarde"
    assert metrics.channel_unit("generic", naam["unit"]) == "ms"


# --- de metricnamen ----------------------------------------------------------

def test_kanaalmeting_wordt_herkend():
    assert metrics.channel_metric("ch6_generic") == (6, "generic")
    assert metrics.channel_metric("ch142_switch") == (142, "switch")
    assert metrics.channel_metric("ch1_voltage") == (1, "voltage")
    assert metrics.channel_metric("ch2_temperature") == (2, "temperature")


def test_gewone_metrics_zijn_geen_kanaalmeting():
    """Anders zou 'bat' of 'airtime' in de kanalensectie belanden."""
    for name in ("bat", "airtime", "neighbor_count", "mcu_temperature",
                 "ch_switch", "chx_switch", "ch1_battery", "ch1_current"):
        assert metrics.channel_metric(name) is None, name


def test_kanaalmeting_krijgt_eigen_sectie_en_niet_overig():
    """Een kanaal in 'Overig' is een meting die niemand meer terugvindt."""
    section, label, unit, sort = metrics.metric_info("ch6_generic")
    assert section == "channels"
    assert "kanaal 6" in label


def test_ch1_en_ch2_blijven_bij_de_batterij():
    """Bestaande tegels mogen niet verhuizen door een nieuwe sectie.

    Op een MeshCore-repeater is kanaal 1 het eigen bord; die tegels stonden altijd
    bij de batterij en de pagina van elke bestaande node zou anders veranderen
    voor een verbetering die alleen nieuwe kanalen nodig hebben.
    """
    assert metrics.metric_info("ch1_voltage")[0] == "battery"
    assert metrics.metric_info("ch2_temperature")[0] == "battery"


def test_kanalen_sorteren_per_kanaal_en_dan_per_soort():
    """De twee metingen van één dienst horen bij elkaar te staan.

    Op soort sorteren zou alle toestanden bij elkaar zetten en alle responstijden
    bij elkaar, en dan staat de toestand van dienst A naast die van dienst B in
    plaats van naast zijn eigen tijd.
    """
    order = sorted(["ch6_generic", "ch5_switch", "ch6_switch", "ch5_generic"],
                   key=lambda m: metrics.metric_info(m)[3])
    assert order == ["ch5_switch", "ch5_generic", "ch6_switch", "ch6_generic"]


# --- labels ------------------------------------------------------------------

def test_naamloos_kanaal_blijft_zichtbaar():
    """Een naamloze meting is nog steeds een meting."""
    assert metrics.channel_label(6, "switch") == "kanaal 6 — toestand"
    assert metrics.channel_label(6, "switch", "") == "kanaal 6 — toestand"
    assert metrics.channel_label(6, "switch", "   ") == "kanaal 6 — toestand"


def test_naam_komt_voor_het_soortlabel():
    assert metrics.channel_label(6, "generic", "google") == "google — meetwaarde"


def test_eenheid_van_het_type_gaat_voor():
    """Spanning is volt; dat laat je een beheerder niet overschrijven.

    Bij een generic sensor is er juist geen eenheid uit het type -- 4 byte met
    vermenigvuldiger 1 belooft niets over wát er gemeten is -- en dan mag de
    beheerder er een zetten.
    """
    assert metrics.channel_unit("voltage", "ms") == "V"
    assert metrics.channel_unit("generic", "ms") == "ms"
    assert metrics.channel_unit("generic", None) is None
    assert metrics.channel_unit("switch", None) is None


# --- opslag: twee soorten op één kanaal --------------------------------------

def test_switch_en_generic_op_hetzelfde_kanaal_blijven_beide_bewaard(db):
    """De belangrijkste test van dit bestand.

    Kanaal 5 draagt een toestand én een responstijd. Als de opslag op het kanaal
    zou sleutelen in plaats van op (kanaal, soort), overschrijft de tweede de
    eerste en is de helft van wat de node zei stil weg.
    """
    rep = _node(db)
    latest = db.latest_for(rep["id"])
    assert latest["ch5_switch"]["value"] == 1.0
    assert latest["ch5_generic"]["value"] == 11.0
    assert latest["ch6_generic"]["value"] == 12.0


def test_kanaalmetingen_komen_in_de_historiek(db):
    """Een responstijd is een tijdreeks zoals elke andere meting."""
    rep = _node(db)
    punten = db.history(rep["id"], "ch6_generic", 24)
    assert punten and punten[-1][1] == 12.0


def test_switch_nul_wordt_bewaard_en_niet_als_leeg_gelezen(db):
    """0 is een meting: 'de dienst is neer', niet 'we weten het niet'."""
    rep = _node(db)
    assert db.latest_for(rep["id"])["ch3_switch"]["value"] == 0.0


# --- namen bij kanalen -------------------------------------------------------

def test_naam_bewaren_en_teruglezen(db):
    rep = _node(db)
    db.set_channel_name(rep["id"], 6, "google", "ms")
    row = db.channel_names_for(rep["id"])[6]
    assert (row["name"], row["unit"]) == ("google", "ms")


def test_lege_naam_van_een_mens_blijft_een_uitspraak(db):
    """Leegmaken is iets anders dan nooit ingevuld hebben.

    Vroeger verdween de rij, en dat was juist zolang er niets anders was dat hem
    kon vullen. Sinds de eigen API van een sensornode de namen aanlevert (zie
    ``sensornode.py``) is een verdwenen rij een uitnodiging: de eerstvolgende
    ronde vult hem opnieuw, en dan is de wissing van de beheerder ongedaan
    gemaakt zonder dat er ergens iets over te lezen valt.

    Dus blijft er een rij staan met een LEGE naam en herkomst 'user'. Voor de
    weergave is dat hetzelfde -- ``channel_label`` maakt van een lege naam
    "kanaal N", precies zoals bij een ontbrekende rij -- en voor de automaat is
    het het verschil tussen "nog niets" en "nee"."""
    rep = _node(db)
    db.set_channel_name(rep["id"], 6, "google", "ms")
    db.set_channel_name(rep["id"], 6, "", "")
    rij = db.channel_names_for(rep["id"])[6]
    assert rij["name"] == "" and rij["source"] == db.SOURCE_USER
    assert metrics.channel_label(6, "switch", rij["name"]) == "kanaal 6 — toestand"


def test_namen_van_twee_nodes_lopen_niet_door_elkaar(db):
    """Kanaal 6 betekent per node iets anders; de sleutel is (node, kanaal)."""
    a = _node(db)
    b = db.get_or_create_repeater("112233445566", "Andere node")
    db.ingest(b["id"], db.utcnow(), TELEMETRIE, None)
    db.set_channel_name(a["id"], 6, "google", "ms")
    db.set_channel_name(b["id"], 6, "router", "ms")
    assert db.channel_names_for(a["id"])[6]["name"] == "google"
    assert db.channel_names_for(b["id"])[6]["name"] == "router"


def test_kanalen_uit_de_metingen_met_hun_soorten(db):
    """De beheerpagina leest de kanalen uit de metingen, niet uit een vaste lijst.

    Welke kanalen een node heeft weet alleen die node.
    """
    rep = _node(db)
    gezien = metrics.channels_seen(db.latest_for(rep["id"]))
    per_nummer = {c["channel"]: c for c in gezien}
    assert sorted(per_nummer) == [1, 2, 3, 4, 5, 6]
    assert per_nummer[5]["kinds"] == ["switch", "generic"]
    assert per_nummer[2]["kinds"] == ["switch"]
    # Alleen een generic sensor heeft een eigen eenheid nodig.
    assert per_nummer[5]["wants_unit"] is True
    assert per_nummer[2]["wants_unit"] is False
    assert per_nummer[1]["wants_unit"] is False


def test_gat_in_de_nummering_blijft_een_gat(db):
    """Nummers mogen niet opschuiven om de lijst netjes te maken.

    Dit is de fout waar de hele opzet tegen beschermt: schuift kanaal 7 op naar 6
    omdat 6 verdwenen is, dan wijst de naam 'google' stil naar een andere dienst.
    Er komt geen foutmelding, alleen verkeerde cijfers.
    """
    rep = _node(db, {"ch5_switch": 1, "ch9_switch": 1})
    assert [c["channel"] for c in
            metrics.channels_seen(db.latest_for(rep["id"]))] == [5, 9]


# --- weergave ----------------------------------------------------------------

def test_publieke_pagina_toont_kanalen_met_hun_naam(db, monkeypatch):
    from app import routes_public

    rep = _node(db)
    db.set_channel_name(rep["id"], 6, "google", "ms")
    monkeypatch.setattr(routes_public.templates, "TemplateResponse",
                        lambda request, name, ctx: ctx)
    ctx = routes_public.repeater_page(_Request(), rep["slug"])

    tegels = {t["metric"]: t for b in ctx["blocks"] if b["type"] == "section"
              for t in b["section"]["tiles"]}
    assert tegels["ch6_generic"]["label"] == "google — meetwaarde"
    assert tegels["ch6_generic"]["display"] == "12 ms"
    # En een kanaal zonder naam verdwijnt niet.
    assert tegels["ch5_generic"]["label"] == "kanaal 5 — meetwaarde"


def test_switch_leest_als_op_of_neer_en_niet_als_getal(db, monkeypatch):
    from app import routes_public

    rep = _node(db)
    monkeypatch.setattr(routes_public.templates, "TemplateResponse",
                        lambda request, name, ctx: ctx)
    ctx = routes_public.repeater_page(_Request(), rep["slug"])
    tegels = {t["metric"]: t for b in ctx["blocks"] if b["type"] == "section"
              for t in b["section"]["tiles"]}
    assert tegels["ch2_switch"]["display"] == "op"
    assert tegels["ch3_switch"]["display"] == "neer"


def test_elke_responstijd_krijgt_een_grafiek(db, monkeypatch):
    """Per generic-sensorkanaal één grafiek, uit de metingen en niet uit een
    vaste lijst: bij een nieuwe dienst zou een vaste lijst stil niets tonen."""
    from app import routes_public

    rep = _node(db)
    db.set_channel_name(rep["id"], 6, "google", "ms")
    monkeypatch.setattr(routes_public.templates, "TemplateResponse",
                        lambda request, name, ctx: ctx)
    ctx = routes_public.repeater_page(_Request(), rep["slug"])

    charts = [c for b in ctx["blocks"] if b["type"] == "charts" for c in b["charts"]]
    per_key = {c["key"]: c for c in charts}
    assert per_key["ch6_generic"]["title"].startswith("google — meetwaarde")
    assert per_key["ch6_generic"]["unit"] == "ms"
    # Een switch krijgt géén lijndiagram: 0/1 is daar geen goede vorm voor. Hij
    # blijft als tegel aanklikbaar, dus zijn historiek is niet weg.
    assert "ch6_switch" not in per_key


def test_api_geeft_de_naam_het_kanaal_en_het_soort(db):
    from app import routes_api

    rep = _node(db)
    db.set_channel_name(rep["id"], 6, "google", "ms")
    body = routes_api.repeater_detail(rep["slug"])
    entry = body["metrics"]["ch6_generic"]
    assert entry["label"] == "google — meetwaarde"
    assert entry["unit"] == "ms"
    assert (entry["channel"], entry["kind"]) == (6, "generic")
    # Zonder naam blijft het kanaal in de API staan, met zijn nummer als naam.
    assert body["metrics"]["ch5_generic"]["label"] == "kanaal 5 — meetwaarde"


def test_kanalenblok_staat_in_de_standaardindeling():
    """Zonder dit blok in de indeling zou de sectie nooit getekend worden."""
    keys = [b["key"] for b in metrics.parse_layout(None)]
    assert "channels" in keys
    # Een oudere bewaarde indeling kent het blok niet; het hoort er dan bij te
    # komen in plaats van de sectie stil te laten vervallen.
    keys = [b["key"] for b in metrics.parse_layout('[{"key": "status"}]')]
    assert "channels" in keys


# --- de beheerpagina ---------------------------------------------------------
#
# De POST is een async functie omdat ze het hele formulier in één keer moet lezen:
# naam en eenheid van elk kanaal komen samen binnen, en los na elkaar schrijven
# zou de tweede de eerste laten wissen. Hier wordt ze met asyncio.run gedraaid in
# plaats van met een extra testafhankelijkheid erbij.

def _sessie(db):
    """Een geldige sessie van een beheerder die alles mag.

    Er moet een echt account zijn, met rechten: elke sessie draagt een
    vingerafdruk van het wachtwoord van dat account -- juist zodat een
    wachtwoordwijziging oude cookies ongeldig maakt -- en rbac beslist daarna
    apart of die gebruiker deze node mag hernoemen. Zonder account is er niets om
    tegen te ijken en weigert read_session de sessie, terecht.
    """
    from app import auth, rbac
    rbac.maak_gebruiker("beheerder", auth.hash_password("wachtwoord123"),
                        is_superuser=True)
    return auth.make_session("beheerder")


def _admin_post(db, rid, velden):
    import asyncio

    from app import auth, routes_admin

    sessie = _sessie(db)
    req = _AdminRequest(sessie, dict(velden, csrf=auth.csrf_token(sessie)))
    return asyncio.run(routes_admin.save_channel_names(req, rid))


def test_beheerder_zet_naam_en_eenheid_in_een_keer(db):
    rep = _node(db)
    _admin_post(db, rep["id"], {"ch_naam_6": "google", "ch_eenheid_6": "ms"})
    row = db.channel_names_for(rep["id"])[6]
    assert (row["name"], row["unit"]) == ("google", "ms")


def test_veldnaam_draagt_het_kanaalnummer_en_niet_de_rijpositie(db):
    """Twee kanalen tegelijk, en de nummers uit de veldnamen halen.

    Zou de route op rijvolgorde vertrouwen, dan zou een verdwenen kanaal elke
    naam een plaats laten opschuiven -- stil, met verkeerde cijfers als spoor.
    """
    rep = _node(db)
    _admin_post(db, rep["id"], {"ch_naam_5": "router", "ch_naam_6": "google"})
    namen = db.channel_names_for(rep["id"])
    assert namen[5]["name"] == "router"
    assert namen[6]["name"] == "google"


def test_onbekend_kanaal_wordt_geweigerd(db):
    """Anders maakt een verzonnen veldnaam een rij aan die nooit een meting krijgt."""
    rep = _node(db)
    _admin_post(db, rep["id"], {"ch_naam_99": "verzonnen"})
    assert 99 not in db.channel_names_for(rep["id"])


def test_beheerpagina_toont_de_kanalen_met_hun_bewaarde_naam(db, monkeypatch):
    from app import routes_admin

    rep = _node(db)
    db.set_channel_name(rep["id"], 6, "google", "ms")
    monkeypatch.setattr(routes_admin.templates, "TemplateResponse",
                        lambda request, name, ctx: ctx)
    # De nodepagina praat met de node als er een weg naartoe is; die weg hoort
    # niet bij wat deze test vastlegt en zou hem aan een broker hangen.
    monkeypatch.setattr(routes_admin.mqtt_ingest, "can_publish", lambda: False)
    ctx = routes_admin.node_page(_AdminRequest(_sessie(db), {}), rep["id"])

    per_nummer = {c["channel"]: c for c in ctx["channels"]}
    assert per_nummer[6]["name"] == "google"
    assert per_nummer[6]["unit"] == "ms"
    # Een kanaal zonder naam staat er met een leeg veld en niet weggelaten: je
    # kunt geen naam invullen bij een rij die er niet is.
    assert per_nummer[5]["name"] == ""


class _Request:
    """Het minimum dat repeater_page van een Request aanraakt."""
    cookies: dict = {}
    query_params: dict = {}


class _AdminRequest:
    """Een Request met een geldige sessie en, waar nodig, een formulier."""

    def __init__(self, session, form):
        from app import auth
        self.cookies = {auth.SESSION_COOKIE: session}
        self.query_params = {}
        self._form = form

    async def form(self):
        return self._form


# --- de naam gaat overal mee -------------------------------------------------
#
# Wat hieronder getest wordt is niet dat de koppeling BESTAAT -- dat doen de tests
# hierboven -- maar dat ze OVERAL geldt. Dat is een andere soort fout: de tabel
# werkte, de nodepagina toonde de namen, en op de publieke tegels, in de API en in
# de legenda van een grafiek stond nog "Ch1 spanning" of "ch6 generic". Eén plek
# die het niet doet is genoeg om een lezer met een ruwe metricnaam achter te laten,
# en dat is een implementatiedetail dat naar buiten lekt.

def test_de_catalogus_kent_de_naam_als_je_hem_meegeeft():
    """``metric_info`` is de ene plek die bepaalt hoe een kanaal heet.

    Vier plaatsen deden dit eerder elk voor zich (de tegels, de API, de
    grafieken, en de catalogus deed het niet). Vier plaatsen die hetzelfde moeten
    zeggen, is drie te veel.
    """
    namen = {6: {"name": "google", "unit": "ms"}}
    sectie, label, eenheid, sort = metrics.metric_info("ch6_generic", namen)
    assert (sectie, label, eenheid) == ("channels", "google — meetwaarde", "ms")
    # Zonder naamtabel blijft het antwoord bruikbaar, en dat is de reden dat de
    # parameter optioneel is.
    assert metrics.metric_info("ch6_generic")[1] == "kanaal 6 — meetwaarde"


def test_een_naam_wint_van_het_catalogus_label_maar_niet_van_de_indeling():
    """Kanaal 1 en 2 staan in de catalogus, bij de batterij.

    Een gezette naam is specifieker dan onze catalogus -- de beheerder weet welke
    sensor op welk kanaal zit -- dus die wint van het LABEL. De SECTIE en de
    sorteervolgorde blijven van de catalogus: anders zou één naam de hele pagina
    van een node herschikken.
    """
    zonder = metrics.metric_info("ch1_voltage")
    met = metrics.metric_info("ch1_voltage", {1: {"name": "paneel", "unit": ""}})
    assert zonder[1] == "Ch1 spanning"
    assert met[1] == "paneel — spanning"
    assert met[0] == zonder[0] == "battery"
    assert met[3] == zonder[3]
    # De eenheid van het LPP-type blijft: een spanning is volt, wat er ook in het
    # eenheidsveld staat.
    assert met[2] == "V"


def test_de_naam_van_kanaal_1_staat_ook_op_de_tegel(db, monkeypatch):
    """Kanaal 1 komt langs de catalogus-lus en niet langs de kanalen-lus.

    Dat is precies het kanaal dat een sensornode als eerste vult ("spanning"), en
    tot nu toe bleef die tegel "Ch1 spanning" heten hoe je het kanaal ook noemde.
    """
    from app import routes_public

    rep = _node(db, {"ch1_voltage": 4.11, "ch5_switch": 1})
    db.set_channel_name(rep["id"], 1, "paneel", "")
    monkeypatch.setattr(routes_public.templates, "TemplateResponse",
                        lambda request, name, ctx: ctx)
    ctx = routes_public.repeater_page(_Request(), rep["slug"])
    tegels = {t["metric"]: t for b in ctx["blocks"] if b["type"] == "section"
              for t in b["section"]["tiles"]}
    assert tegels["ch1_voltage"]["label"] == "paneel — spanning"


def test_de_vaste_grafieken_dragen_de_naam_in_hun_legenda(db, monkeypatch):
    """'Spanning (24 u)' tekent ch1_voltage, en dat kanaal kan een naam hebben."""
    from app import routes_public

    rep = _node(db, {"ch1_voltage": 4.11})
    db.set_channel_name(rep["id"], 1, "paneel", "")
    monkeypatch.setattr(routes_public.templates, "TemplateResponse",
                        lambda request, name, ctx: ctx)
    ctx = routes_public.repeater_page(_Request(), rep["slug"])
    charts = {c["key"]: c for b in ctx["blocks"] if b["type"] == "charts"
              for c in b["charts"]}
    assert "paneel — spanning" in charts["voltage"]["labels"]


def test_de_api_geeft_de_naam_en_de_eenheid_bij_de_meting(db):
    from app import routes_api

    rep = _node(db)
    db.set_channel_name(rep["id"], 6, "google", "ms")
    antwoord = routes_api.repeater_detail(rep["slug"])
    meting = antwoord["metrics"]["ch6_generic"]
    assert meting["label"] == "google — meetwaarde"
    assert meting["unit"] == "ms"
    assert (meting["channel"], meting["kind"]) == (6, "generic")


def test_de_tijdreeks_kan_zijn_eigen_as_benoemen(db):
    """Wie een reeks tekent hoort de as te kunnen benoemen zonder een tweede
    verzoek. ``ch6_generic`` met 412 erbij is anders een getal zonder betekenis."""
    from app import routes_api

    rep = _node(db)
    db.set_channel_name(rep["id"], 6, "google", "ms")
    antwoord = routes_api.repeater_history(rep["slug"], metric="ch6_generic", hours=24)
    assert antwoord["label"] == "google — meetwaarde"
    assert antwoord["unit"] == "ms"


# --- publiek of niet ---------------------------------------------------------

def test_kanaalnamen_zijn_per_node_publiek_te_maken_of_niet(db):
    """Een derde zichtbaarheidsvlag, naast naam en positie.

    En hij is nodig om een reden die de andere twee niet hebben: een kanaalnaam is
    nooit uitgezonden. De naam en de positie van een node reizen in elke advert
    mee, dus die verbergen is een keuze over iets dat al bestaat. Een kanaalnaam
    komt uit de eigen API van de node of uit een toetsenbord, en hij kan een
    intern adres bevatten dat over de radio nooit langskwam.
    """
    rep = _node(db)
    db.set_channel_name(rep["id"], 6, "hoas (hoas.scheepers.one)", "ms")
    assert db.channel_names_for(rep["id"], public=True)[6]["name"].startswith("hoas")
    db.execute("UPDATE repeaters SET show_channels=0 WHERE id=?", (rep["id"],))
    assert db.channel_names_for(rep["id"], public=True) == {}
    # Voor de beheerder blijft hij staan: die naam is voor hem.
    assert db.channel_names_for(rep["id"])[6]["name"].startswith("hoas")


def test_een_verborgen_kanaal_valt_terug_op_kanaal_n_en_niet_op_de_metricnaam(db, monkeypatch):
    """De ruwe metricnaam verklapt het nummer én de vorm van de meting alsnog.

    En het kanaal verdwijnt niet: een naamloze meting is nog steeds een meting, en
    juist het teken dat er iets binnenkomt dat nog benoemd moet worden.
    """
    from app import routes_public

    rep = _node(db)
    db.set_channel_name(rep["id"], 6, "hoas (hoas.scheepers.one)", "ms")
    db.execute("UPDATE repeaters SET show_channels=0 WHERE id=?", (rep["id"],))
    monkeypatch.setattr(routes_public.templates, "TemplateResponse",
                        lambda request, name, ctx: ctx)
    ctx = routes_public.repeater_page(_Request(), rep["slug"])
    tegels = {t["metric"]: t for b in ctx["blocks"] if b["type"] == "section"
              for t in b["section"]["tiles"]}
    assert tegels["ch6_generic"]["label"] == "kanaal 6 — meetwaarde"
    assert "hoas" not in str(ctx["blocks"])


def test_een_bestaande_node_blijft_zijn_kanaalnamen_tonen(db):
    """De standaard van de nieuwe kolom, en waarom hij 1 is.

    ALTER TABLE ADD COLUMN vult bestaande rijen met de standaard, en de vorige
    versie toonde deze namen al publiek. Een privacykolom die bij het toevoegen
    stilzwijgend iets van een bestaande pagina haalt, is een slechtere fout dan de
    kolom die ontbrak.
    """
    rep = _node(db)
    assert rep["show_channels"] == 1
