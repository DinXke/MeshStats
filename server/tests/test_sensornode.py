"""Tests voor de derde weg: een node die zijn eigen API over IP aanbiedt.

Wat hier bewaakt wordt, en waarom elk van die dingen het bewaken waard is.

**Één naamruimte.** Dezelfde sensornode kan langs twee wegen binnenkomen -- over
LoRa via een monitor, of over IP via zijn eigen ``/status.json`` -- en die twee
moeten dezelfde metricnamen en dezelfde getallen opleveren. Doen ze dat niet, dan
staan er twee reeksen per dienst in de databank en tekent de pagina twee
grafieken van hetzelfde, met een knik op het moment dat er van weg gewisseld
werd. De regel die daarbij het snelst stil misgaat is de tijd: die hoort alleen
mee te gaan als het kanaal op staat, precies zoals ``querySensors()`` in de
firmware doet, want anders loopt een grafiek tijdens een storing gewoon door.

**Eén weigeringslijst.** ``nodeconfig.NO_REMOTE`` is de plek waar staat wat er
van afstand nooit gezet wordt. Deze weg heeft een eigen soort verzoek (een hele
CLI-regel in plaats van een sleutel) en mag daar geen tweede lijst voor
optuigen. En de bevestigingsparameter die de node zelf als slot gebruikt
(``confirm=radio``) mag er nooit meegaan -- dat slot van binnenuit openen is het
enige dat deze module echt kapot kan maken.

**Eén parametertabel.** De sensornode publiceert zijn tabel nergens en draait
dezelfde CommonCLI als onze repeaterfirmware, dus ``sensornode.SPEC`` is een
spiegel van ``CFG_PARAMS`` in de firmwarebroncode. Twee plaatsen die het eens
moeten zijn is er één te veel, en het enige dat dat draagt is een test die de
C-tabel uitleest en er regel voor regel tegenaan houdt. Die staat hieronder.

**Een naam die iemand getypt heeft.** De vulling uit ``/status.json`` mag nooit
over een naam heen die een beheerder gezet heeft. Zonder die regel draait een
ronde elke vijf minuten iemands werk terug, en dan is de eerste maatregel dat de
ronde uitgezet wordt.

Er wordt geen socket geopend. De netwerkgrens is één functie
(``nodeconfig._open``) en die wordt hier vervangen, zodat de tests over het
gedrag gaan en niet over een node die toevallig aanstaat.
"""
import io
import json
import re
import urllib.error
from pathlib import Path

import pytest

from app import commanding, config, metrics, nodeconfig, sensornode

FIRMWARE = (Path(__file__).resolve().parent.parent.parent
            / "firmware" / "examples" / "simple_repeater" / "MeshManagerNet.cpp")


@pytest.fixture
def db(tmp_path, monkeypatch):
    """De db-module tegen een verse, tijdelijke database.

    Zelfde opzet als test_db.py en test_kanalen.py: de moduleverbinding leeft op
    moduleniveau en moet per test weggegooid en na afloop gesloten worden, anders
    lekken tests in elkaar en kan Windows de tijdelijke file niet opruimen.
    """
    from app import db as db_module
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "test.sqlite3")
    db_module._conn = None
    yield db_module
    if db_module._conn is not None:
        db_module._conn.close()
        db_module._conn = None


# Een echt antwoord van /status.json, met alle vier de soorten kanalen erin: de
# spanning op 1, twee toestanden in woorden op 2 en 3, wifi op 4, een dienst die
# op staat (met tijd) op 5, en een dienst die neer is (dus zonder tijd) op 6.
STATUS = {
    "fw": "1.4.0", "wifi": "verbonden", "ip": "192.168.110.160", "rssi": -63,
    "uptime": 86400, "heap": 120000, "largest": 60000, "mains": 1,
    "volts": "4.139", "paused": 0,
    "mon": [
        {"ch": 1, "n": "spanning", "h": "batterij", "i": 0, "st": "4.139 V",
         "ms": 0, "f": 0, "c": 0, "k": "vast", "age": 0, "sev": "unk"},
        {"ch": 2, "n": "netvoeding", "h": "klemspanning", "i": 0, "st": "aan",
         "ms": 0, "f": 0, "c": 0, "k": "vast", "age": 0, "sev": "ok"},
        {"ch": 3, "n": "batterijvoeding", "h": "klemspanning", "i": 0, "st": "uit",
         "ms": 0, "f": 0, "c": 0, "k": "vast", "age": 0, "sev": "ok"},
        {"ch": 4, "n": "wifi", "h": "deze node", "i": 0, "st": "online",
         "ms": 0, "f": 0, "c": 0, "k": "vast", "age": 0, "sev": "ok"},
        {"ch": 5, "n": "google", "h": "google.com", "i": 60, "st": "op",
         "ms": 37, "f": 2, "c": 900, "k": "ping", "age": 0, "sev": "ok"},
        {"ch": 6, "n": "hoas", "h": "hoas.scheepers.one", "i": 60, "st": "neer",
         "ms": 412, "f": 9, "c": 900, "k": "ping", "age": 0, "sev": "bad"},
    ],
}

CFG = {
    "name": "MeshUptime", "owner": "", "pubkey": "48d7" * 16, "role": "sensor",
    "freq": "869.525", "bw": "250.000", "sf": 11, "cr": 5,
    "tx": 22, "af": "1.000", "agc": 0, "rxgain": "on", "femrx": "off",
    "lat": "50.930000", "lon": "5.340000", "advint": 120, "fadvint": 3,
    "advloc": "prefs", "repeat": "off", "fmax": 64, "fmaxuns": 3, "fmaxadv": 3,
    "loopd": "moderate", "rxdelay": "0.000", "txdelay": "0.500",
    "dtxdelay": "0.000", "multiack": 0, "hashmode": 0, "cad": "off",
    "intthr": 0, "rdonly": "on", "adcmult": "1.000",
    "pwdef": 0, "pwempty": 0, "mon_used": 2, "mon_max": 8,
    "ch_first": 5, "ch_last": 12, "ch_ever": 2, "ch_free": 6,
    "baked": {"freq": "869.525", "bw": "250.000", "sf": 11, "cr": 5},
}

ACL = {
    "strict": 0, "max": 8, "nbmax": 16,
    "acl": [{"k": "aa" * 32, "n": "JessaZH.VIR", "p": 3, "a": 120}],
    "nb": [{"k": "bb" * 32, "n": "DinX-Home", "t": 2, "h": 1, "s": "8.5",
            "a": 300, "c": 42, "in": 1}],
}


class _Antwoord:
    """Wat urlopen als contextmanager teruggeeft: alleen read()."""

    def __init__(self, body: bytes):
        self._body = body

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False


def _node_op_afstand(monkeypatch, *, cfg=None, cli_reply=b"OK",
                     status_body=None, gezien=None):
    """Vervangt de netwerkgrens en houdt bij wat er verstuurd is.

    Geeft de logboeklijst terug: per verzoek ``(pad, body)``. Dat is waar de
    tests over ``confirm`` op wegen -- niet op een vlag die de module zelf
    meldt, maar op de bytes die eruit gaan.
    """
    verstuurd = []
    monkeypatch.setattr(sensornode.firmware, "NODE_USER", "admin")

    def nep(host, path, data=None, timeout=None):
        verstuurd.append((path, data.decode() if data else ""))
        if path == "/status.json":
            return _Antwoord(json.dumps(
                STATUS if status_body is None else status_body).encode())
        if path == "/cfg.json":
            return _Antwoord(json.dumps(CFG if cfg is None else cfg).encode())
        if path == "/acl.json":
            return _Antwoord(json.dumps(ACL).encode())
        if path == "/cli":
            return _Antwoord(cli_reply)
        raise AssertionError(f"onverwacht pad {path}")

    monkeypatch.setattr(nodeconfig, "_open", nep)
    if gezien is not None:
        monkeypatch.setattr(sensornode, "cli", sensornode.cli)
    return verstuurd


def _sensornode(db, host="192.168.110.160", seen="2026-08-20T10:00:00Z"):
    rep = db.get_or_create_repeater("48d7aade232b", "MeshUptime")
    db.execute("UPDATE repeaters SET is_public=1 WHERE id=?", (rep["id"],))
    # by_admin=True, want dat is wat een serverbeheerder doet als hij dit veld
    # invult -- en zonder die vlag weigert firmware.check_target de verbinding.
    # De tests die de weigering zélf afdwingen staan onderaan dit bestand.
    db.set_sensor_host(rep["id"], host, by_admin=True)
    if seen:
        db.execute("UPDATE repeaters SET sensor_seen=?, sensor_fw=? WHERE id=?",
                   (seen, "1.4.0", rep["id"]))
    return db.qone("SELECT * FROM repeaters WHERE id=?", (rep["id"],))


# --- één naamruimte -----------------------------------------------------------

def test_de_kanalen_krijgen_dezelfde_namen_als_over_het_mesh():
    """``ch<N>_switch`` en ``ch<N>_generic``, en niets eigens.

    De namen komen uit de repeaterfirmware (de publicatie in MeshManagerNet.cpp)
    en deze weg moet ze letterlijk overnemen. Een eigen naam hier -- ``ch5_ping_ms``
    bijvoorbeeld -- zou per dienst een tweede reeks opleveren die naast de eerste
    staat en hetzelfde meet.
    """
    uit = sensornode.metrics_from_status(STATUS)
    assert uit["ch1_voltage"] == 4.139
    assert uit["ch2_switch"] == 1 and uit["ch3_switch"] == 0
    assert uit["ch4_switch"] == 1
    assert uit["ch5_switch"] == 1 and uit["ch5_generic"] == 37
    # Elke naam is er een die de andere weg ook oplevert.
    for naam in uit:
        if naam.startswith("ch"):
            assert metrics.channel_metric(naam) is not None, naam


def test_een_dienst_die_neer_is_levert_geen_tijd():
    """De regel uit querySensors(): de tijd gaat alleen mee als het kanaal op staat.

    Dit is de stille variant van fout gaan. Een tijd bij een dode dienst is geen
    meting maar een oude waarde, en wie hem toch bewaart krijgt een grafiek die
    tijdens een storing gewoon doorloopt -- op precies het moment dat je hem
    leest om te zien of er iets aan de hand was.
    """
    uit = sensornode.metrics_from_status(STATUS)
    assert uit["ch6_switch"] == 0
    assert "ch6_generic" not in uit


@pytest.mark.parametrize("toestand", ["pauze", "stil", "?"])
def test_alles_wat_niet_op_is_leest_als_neer(toestand):
    """LPP_SWITCH kent geen "onbekend", dus de andere weg kan het ook niet melden.

    Hier hetzelfde antwoord geven is dus geen verlies van nuance maar de enige
    manier waarop de twee wegen hetzelfde getal opleveren. De nuance zelf staat
    op de eigen pagina van de node, waar ze thuishoort.
    """
    data = dict(STATUS, mon=[dict(STATUS["mon"][4], st=toestand)])
    uit = sensornode.metrics_from_status(data)
    assert uit["ch5_switch"] == 0 and "ch5_generic" not in uit


def test_de_uptime_staat_in_dagen_zoals_overal_in_dit_project():
    """/status.json meldt seconden en dit project rekent in dagen.

    Een getal dat er in de databank hetzelfde uitziet en iets anders betekent, is
    erger dan een ontbrekend getal: de grafiek loopt gewoon door, met een sprong
    van vier ordes van grootte op het moment dat de bron wisselde.
    """
    assert sensornode.metrics_from_status(STATUS)["uptime"] == 1.0


def test_een_rssi_van_nul_is_geen_meting():
    """Dezelfde toets als de firmware op noise_floor en last_rssi doet.

    Een RSSI in dBm is altijd negatief; 0 betekent dat de driver hem nooit
    ingevuld heeft. Die als meting bewaren tekent een lijn die naar nul duikt op
    een plek waar een gat hoort.
    """
    assert "wifi_rssi" not in sensornode.metrics_from_status(dict(STATUS, rssi=0))
    assert sensornode.metrics_from_status(STATUS)["wifi_rssi"] == -63


def test_het_wifisignaal_heet_niet_last_rssi():
    """Twee radio's, twee namen. ``last_rssi`` is de LoRa-kant en blijft dat."""
    uit = sensornode.metrics_from_status(STATUS)
    assert "last_rssi" not in uit
    assert metrics.CATALOG["wifi_rssi"][2] == "dBm"


def test_een_leeg_of_kapot_antwoord_levert_geen_verzonnen_metingen():
    """Een node zonder sensorlaag meldt een lege kaart, en dat is een antwoord."""
    assert sensornode.metrics_from_status({}) == {"online": True}
    assert sensornode.metrics_from_status(None) == {}
    assert sensornode.metrics_from_status({"mon": [{"ch": "x"}, None, 3]}) \
        == {"online": True}


# --- de namen bij de kanalen --------------------------------------------------

def test_de_namen_komen_uit_de_node_met_het_adres_erbij():
    """``n`` en ``h`` samen, want los zegt geen van beide genoeg.

    "google" zegt niet welk adres er gepingd wordt en "google.com" niet waarom.
    De eenheid komt er alleen bij op een kanaal dat een tijd kan dragen: dát een
    generic sensor milliseconden bevat, weet deze API en het telemetriepakket
    niet -- LPP_GENERIC_SENSOR belooft vier byte en niets over de betekenis.
    """
    namen = sensornode.channel_names_from_status(STATUS)
    assert namen[5] == {"name": "google (google.com)", "unit": "ms"}
    assert namen[2] == {"name": "netvoeding (klemspanning)", "unit": ""}
    assert namen[1]["unit"] == ""


def test_een_gemelde_dienst_krijgt_geen_nepadres_in_zijn_naam():
    """``h`` is dan "(gemeld)", en dat staat al in het soort van het kanaal."""
    data = dict(STATUS, mon=[dict(STATUS["mon"][4], h="(gemeld)", k="gemeld")])
    namen = sensornode.channel_names_from_status(data)
    assert namen[5] == {"name": "google", "unit": "ms"}


def test_een_naam_van_een_mens_wint_van_de_ronde(db):
    """De regel waar deze hele kolom voor bestaat.

    Een beheerder heeft "hoas" omgedoopt tot iets dat hij begrijpt. Draait de
    volgende ronde dat terug, dan is de eerste maatregel dat de ronde uitgezet
    wordt -- en dan werkt er niets meer.
    """
    rep = _sensornode(db)
    db.set_channel_name(rep["id"], 6, "Home Assistant (dak)", "ms",
                        source=db.SOURCE_USER)
    for kanaal, naam in sensornode.channel_names_from_status(STATUS).items():
        db.set_channel_name(rep["id"], kanaal, naam["name"], naam["unit"],
                            source=db.SOURCE_AUTO)
    namen = db.channel_names_for(rep["id"])
    assert namen[6]["name"] == "Home Assistant (dak)"
    assert namen[6]["source"] == db.SOURCE_USER
    # En de rest is wél overgenomen.
    assert namen[5]["name"] == "google (google.com)"
    assert namen[5]["source"] == db.SOURCE_AUTO


def test_een_automatische_naam_mag_wel_door_een_automatische_vervangen_worden(db):
    """Anders zou een dienst die verhuist voor altijd zijn oude naam houden."""
    rep = _sensornode(db)
    db.set_channel_name(rep["id"], 5, "oud", "ms", source=db.SOURCE_AUTO)
    db.set_channel_name(rep["id"], 5, "nieuw", "ms", source=db.SOURCE_AUTO)
    assert db.channel_names_for(rep["id"])[5]["name"] == "nieuw"


def test_wie_een_overgenomen_naam_bevestigt_maakt_hem_van_zichzelf(db):
    """Bewaren met dezelfde tekst is een keuze en geen no-op.

    Anders zou "ja, deze naam is goed" niets betekenen: de rij zou 'auto' blijven
    en de volgende ronde zou hem alsnog mogen wijzigen.
    """
    rep = _sensornode(db)
    db.set_channel_name(rep["id"], 5, "google (google.com)", "ms",
                        source=db.SOURCE_AUTO)
    db.set_channel_name(rep["id"], 5, "google (google.com)", "ms",
                        source=db.SOURCE_USER)
    rij = db.channel_names_for(rep["id"])[5]
    assert rij["source"] == db.SOURCE_USER
    db.set_channel_name(rep["id"], 5, "iets anders", "ms", source=db.SOURCE_AUTO)
    assert db.channel_names_for(rep["id"])[5]["name"] == "google (google.com)"


def test_een_bewust_leeggemaakte_naam_wordt_niet_opnieuw_gevuld(db):
    """Leegmaken is ook een uitspraak, en die hoort een ronde te overleven."""
    rep = _sensornode(db)
    db.set_channel_name(rep["id"], 5, "", "", source=db.SOURCE_USER)
    db.set_channel_name(rep["id"], 5, "google", "ms", source=db.SOURCE_AUTO)
    assert db.channel_names_for(rep["id"])[5]["name"] == ""


# --- de radioregel ------------------------------------------------------------

def test_elk_radiowoord_wordt_geweigerd_vóór_er_iets_vertrekt(monkeypatch):
    """Op de CLI-regel en uit dezelfde lijst als de sleuteltoets in write()."""
    verstuurd = _node_op_afstand(monkeypatch)
    for sleutel in nodeconfig.NO_REMOTE:
        uit = sensornode.cli("host", f"set {sleutel} 868.0")
        assert uit["ok"] is False, sleutel
        assert "van afstand niet gezet" in uit["error"], sleutel
    assert verstuurd == [], "er mocht geen enkel verzoek de deur uit"


def test_de_ontvangstversterking_komt_er_wel_langs(monkeypatch):
    """'radio.rxgain' begint met hetzelfde woord en is iets anders.

    Hij maakt een node hooguit dover en laat hem op hetzelfde kanaal -- de kant
    van de asymmetrie waar 'tx' ook staat. Een weigering op voorvoegsel zou hem
    meenemen, en dan zou de regel iets anders gaan betekenen dan ze zegt. De node
    zelf toetst met exact dezelfde grens ("set radio " mét spatie).
    """
    verstuurd = _node_op_afstand(monkeypatch)
    assert sensornode.cli("host", "set radio.rxgain on")["ok"] is True
    assert verstuurd == [("/cli", "cmd=set+radio.rxgain+on")]


def test_er_gaat_nooit_een_bevestigingsparameter_mee(monkeypatch):
    """Het slot van de node mag niet van binnenuit geopend worden.

    ``POST /cli`` weigert 'set radio' en 'erase' zonder ``confirm``, en dat slot
    is er juist tegen een losse fetch of een voorgeladen link. Deze site is zo'n
    fetch. Dus: geen parameter om het mee te sturen, en deze test kijkt naar de
    bytes en niet naar een vlag.
    """
    verstuurd = _node_op_afstand(monkeypatch)
    for opdracht in ("advert", "reboot", "region save", "time 1755000000",
                     "set tx 20"):
        sensornode.cli("host", opdracht)
    assert verstuurd, "er is niets verstuurd; dan bewijst deze test niets"
    for pad, body in verstuurd:
        assert "confirm" not in body, body


def test_zonder_weblogin_gaat_er_niets_de_deur_uit(monkeypatch):
    monkeypatch.setattr(sensornode.firmware, "NODE_USER", "")
    monkeypatch.setattr(nodeconfig, "_open",
                        lambda *a, **k: pytest.fail("mocht de node niet benaderen"))
    assert sensornode.cli("host", "advert")["ok"] is False
    assert sensornode.status("host")["ok"] is False


# --- één parametertabel -------------------------------------------------------

def _cfg_params_uit_de_firmware() -> dict:
    """CFG_PARAMS uit de C-broncode, als {sleutel: (soort, lo, hi, keuzes, risico)}.

    Leest de tabel en niet de header: wat er in de firmware STAAT is wat de node
    doet, en een tweede beschrijving ervan zou hetzelfde probleem hebben als de
    spiegel die deze functie moet controleren.
    """
    tekst = io.open(FIRMWARE, encoding="utf-8", errors="replace").read()
    begin = tekst.index("static const CfgParam CFG_PARAMS[] = {")
    eind = tekst.index("\n};", begin)
    tabel = tekst[begin:eind]
    # Commentaar eruit vóór het ontleden. In dat blok staat namelijk letterlijk de
    # regel waarmee 'radio' terug te zetten is -- als toelichting, uitgeschakeld --
    # en een parser die commentaar meeneemt concludeert dat de firmware hem weer
    # aanbiedt. Dat is dezelfde soort fout als deze reeks moet vinden, maar dan de
    # verkeerde kant op: een test die alarm slaat over iets dat niet gebeurd is.
    tabel = re.sub(r"/\*.*?\*/", "", tabel, flags=re.S)
    tabel = re.sub(r"//[^\n]*", "", tabel)
    soorten = {"CFG_INT": "int", "CFG_FLOAT": "float", "CFG_BOOL": "bool",
               "CFG_ENUM": "enum", "CFG_TEXT": "text", "CFG_RADIO": "radio"}
    risico = {"RISK_PLAIN": nodeconfig.RISK_PLAIN,
              "RISK_WRITES": nodeconfig.RISK_WRITES,
              "RISK_CUTOFF": nodeconfig.RISK_CUTOFF}
    regel = re.compile(
        r'\{\s*"([^"]+)"\s*,\s*(CFG_\w+)\s*,\s*(-?[\d.]+)\s*,\s*(-?[\d.]+)\s*,'
        r'\s*(NULL|"[^"]*")\s*,\s*(RISK_\w+)')
    uit = {}
    for m in regel.finditer(tabel):
        keuzes = "" if m.group(5) == "NULL" else m.group(5).strip('"')
        uit[m.group(1)] = (soorten[m.group(2)], float(m.group(3)),
                           float(m.group(4)), keuzes, risico[m.group(6)])
    return uit


def test_de_spiegel_van_de_firmwaretabel_klopt_regel_voor_regel():
    """De enige reden dat er een parametertabel in Python mag staan.

    Een sensornode publiceert zijn tabel nergens en draait dezelfde CommonCLI als
    onze repeaterfirmware, dus de firmwaretabel IS de tabel -- maar hij staat in
    C++ en kan hier niet opgehaald worden. Deze test houdt de twee tegen elkaar
    aan: soort, grenzen, keuzes en risicoklasse, per sleutel. Wijkt de firmware
    af, dan valt hij hier om in plaats van bij een node die een waarde weigert
    die de pagina aanbood.
    """
    firmwaretabel = _cfg_params_uit_de_firmware()
    assert firmwaretabel, "CFG_PARAMS niet gevonden -- de vorm is veranderd"
    spiegel = {r[0]: (r[1], float(r[2]), float(r[3]), r[4], r[5])
               for r in sensornode.SPEC}
    for sleutel, verwacht in spiegel.items():
        assert sleutel in firmwaretabel, (
            f"{sleutel} staat in de spiegel en niet in de firmware")
        assert firmwaretabel[sleutel] == verwacht, sleutel


def test_geen_enkele_firmwareparameter_valt_stil_weg():
    """Wat de firmware aanbiedt, is hier aangeboden of hier uitgelegd.

    Zonder deze test blijft een nieuwe parameter in de firmware stilzwijgend uit
    deze weg weg: het formulier zou hem niet tonen en niemand zou weten waarom.
    Nu moet er een reden bij, in NO_READBACK, en die reden komt op de pagina te
    staan.
    """
    firmwaretabel = _cfg_params_uit_de_firmware()
    onverklaard = (set(firmwaretabel)
                   - set(sensornode.CFG_KEYS)
                   - set(sensornode.NO_READBACK))
    assert onverklaard == set(), onverklaard


def test_geen_radiosleutel_in_de_aangeboden_lijst():
    """De weigering hoort niet alleen in write() te staan maar ook in de lijst.

    Twee sloten voor één regel, en het tweede is niet overbodig: een sleutel die
    in de lijst staat, wordt op de pagina getekend -- en een invoerveld dat je
    ziet, is een invoerveld dat iemand probeert.
    """
    aangeboden = {p["key"] for p in sensornode.spec()["params"]}
    assert aangeboden & set(nodeconfig.NO_REMOTE) == set()


def test_elke_aangeboden_sleutel_is_terug_te_lezen():
    """Geen veld zonder spiegelbeeld in /cfg.json.

    Dat is de regel die 'dutycycle' en 'guest.password' buiten deze weg houdt.
    Zonder terugleesbaar veld zou de pagina "gelukt" moeten melden op het woord
    van de node, en MeshCore antwoordt "OK" op dingen die het niet werkelijk
    heeft overgenomen -- de hele reden dat nodeconfig.py bestaat.
    """
    for p in sensornode.spec()["params"]:
        assert sensornode.CFG_KEYS[p["key"]] in CFG, p["key"]


# --- de weg en het niveau -----------------------------------------------------

def test_een_bereikbare_eigen_api_is_volledig_beheerd():
    """Het niveau is een waarneming, en deze waarneming is er een.

    Deze node publiceert niets over MQTT, meldt geen firmwareversie van ons en
    heeft geen monitor die hem doorstuurt -- op alle bestaande toetsen is hij
    unmanaged. En hij is volledig te beheren.
    """
    rij = {"pubkey_prefix": "48d7aade232b", "source_prefix": None,
           "fw_meshmanager": None, "sensor_host": "192.168.110.160",
           "sensor_seen": "2026-08-20T10:00:00Z", "sensor_fw": "1.4.0"}
    route = commanding.route_for(rij, broker_connected=False)
    assert route["level"] == commanding.LEVEL_FULL
    assert "eigen API" in route["level_why"]
    assert "192.168.110.160" in route["level_why"]


def test_een_adres_zonder_antwoord_is_geen_weg():
    """Anders zou een tikfout in een invoerveld een node full managed maken."""
    rij = {"pubkey_prefix": "48d7", "source_prefix": None,
           "fw_meshmanager": None, "sensor_host": "192.168.110.9",
           "sensor_seen": None, "sensor_fw": None}
    route = commanding.route_for(rij, broker_connected=False)
    assert route["level"] == commanding.LEVEL_UNMANAGED
    assert route["ip_api"]["ever"] is False


def test_het_niveau_verschuift_niet_met_een_wegvallende_wifi():
    """Wat deze node IS, verandert niet doordat er nu geen weg is.

    Zelfde regel als bij een full managed node achter een weggevallen broker. De
    versheid staat apart, in ``ip_api``, want dat is de andere vraag -- en juist
    die vraag hoort de pagina te stellen.
    """
    rij = {"pubkey_prefix": "48d7", "source_prefix": None,
           "fw_meshmanager": None, "sensor_host": "192.168.110.160",
           "sensor_seen": "2020-01-01T00:00:00Z", "sensor_fw": "1.4.0"}
    route = commanding.route_for(rij, broker_connected=False)
    assert route["level"] == commanding.LEVEL_FULL
    assert route["ip_api"]["ever"] is True
    assert route["ip_api"]["fresh"] is False


def test_de_verouderingsgrens_haalt_minstens_twee_rondes():
    """Eén overgeslagen ronde is een hik en geen afwezigheid.

    Deze twee getallen staan in twee bestanden (commanding kent de pollronde
    niet, met opzet), en dat is precies waarom het hier nagerekend wordt: wie het
    interval verlaagt zonder deze grens mee te nemen, laat de pagina van een
    node die net één ronde miste melden dat de weg weg is.
    """
    assert commanding.IP_API_STALE_SECS >= 2 * sensornode.INTERVAL_S


def test_zonder_adres_verschijnt_er_geen_afgevallen_kandidaat():
    """Anders staat op elke repeaterpagina een weg die niet over die node gaat."""
    rij = {"id": 1, "pubkey_prefix": "55d9", "source_prefix": "55d9",
           "fw_meshmanager": "2.8.0", "ota_host": "", "sensor_host": None,
           "sensor_seen": None}
    route = nodeconfig.cfg_route(rij, broker_connected=True)
    assert [k["transport"] for k in route["options"]] == ["ip", "mqtt"]


def test_de_eigen_api_staat_vooraan_waar_hij_bestaat(db, monkeypatch):
    """Niet uit voorkeur: zo'n node heeft de andere wegen niet.

    Hem achteraan zetten zou de pagina eerst laten opsommen dat onze firmware er
    niet op staat, voordat ze bij de weg komt die werkt.
    """
    _node_op_afstand(monkeypatch)
    rep = _sensornode(db)
    route = nodeconfig.cfg_route(rep, broker_connected=True)
    assert route["can"] is True
    assert route["transport"] == "sensor"
    assert route["options"][0]["transport"] == "sensor"
    assert route["max_risk"] == nodeconfig.RISK_CUTOFF


# --- schrijven, langs de ene schrijfweg ---------------------------------------

def test_schrijven_gaat_door_write_en_leest_terug(db, monkeypatch):
    """Eén schrijfweg, vier vervoermiddelen -- ook voor deze node.

    Alles wat een schrijfactie kan tegenhouden staat in ``nodeconfig.write()`` en
    gebeurt onverkort; deze weg is alleen het transport. En hij leest terug, met
    een tweede verzoek: ``POST /cli`` geeft alleen de tekst van de CLI, en
    MeshCore antwoordt "OK" op dingen die het niet werkelijk overgenomen heeft.
    """
    verstuurd = _node_op_afstand(monkeypatch)
    rep = _sensornode(db)
    uit = nodeconfig.write(rep, "tx", "22", confirm="MeshUptime")
    assert uit["ok"] is True
    assert uit["transport"] == "sensor"
    assert uit["applied"] == "22" and uit["exact"] is True
    paden = [p for p, _ in verstuurd]
    assert "/cli" in paden and paden[-1] == "/cfg.json", paden


def test_een_geknipte_waarde_is_gelukt_maar_niet_exact(db, monkeypatch):
    """advert.interval wordt bewaard als minuten/2, dus 121 komt terug als 120.

    Geen mislukking en geen succes zonder meer: de pagina hoort te zeggen dat er
    iets anders in staat dan wat er gevraagd is. Dat is de hele reden dat
    ``asked`` en ``applied`` apart bestaan.
    """
    _node_op_afstand(monkeypatch, cfg=dict(CFG, advint=120))
    rep = _sensornode(db)
    uit = nodeconfig.write(rep, "advert.interval", "121")
    assert uit["ok"] is True and uit["applied"] == "120"
    assert uit["exact"] is False


def test_een_weigering_van_de_cli_is_geen_stilte(db, monkeypatch):
    """De tekst van de node gaat mee terug in plaats van "HTTP 400"."""
    _node_op_afstand(monkeypatch, cli_reply=b"Error: unknown command")
    rep = _sensornode(db)
    uit = nodeconfig.write(rep, "tx", "22", confirm="MeshUptime")
    assert uit["ok"] is False
    assert uit["step"] == "node" and "Error" in uit["msg"]


def test_een_gelukte_set_zonder_teruglezing_heet_geen_mislukking(db, monkeypatch):
    """De derde uitkomst, en ze heet hier hetzelfde als over LoRa.

    Het commando is aangenomen en wat er nu in staat weten we niet. "Mislukt"
    laat iemand denken dat er niets gebeurd is, en dat is de gevaarlijkste van de
    drie lezingen.
    """
    verstuurd = []

    def nep(host, path, data=None, timeout=None):
        verstuurd.append(path)
        if path == "/cli":
            return _Antwoord(b"OK")
        raise urllib.error.URLError("weg")

    monkeypatch.setattr(sensornode.firmware, "NODE_USER", "admin")
    monkeypatch.setattr(nodeconfig, "_open", nep)
    rep = _sensornode(db)
    uit = nodeconfig.write(rep, "tx", "22", confirm="MeshUptime")
    assert uit["ok"] is False
    assert uit["step"] == "geen_antwoord"
    assert "aangenomen" in uit["msg"]


def test_een_radiosleutel_komt_niet_tot_het_transport(db, monkeypatch):
    """De weigering staat vóór de routekeuze, dus ook vóór deze weg."""
    monkeypatch.setattr(sensornode.firmware, "NODE_USER", "admin")
    monkeypatch.setattr(nodeconfig, "_open",
                        lambda *a, **k: pytest.fail("mocht de node niet benaderen"))
    rep = _sensornode(db)
    uit = nodeconfig.write(rep, "radio", "869.525 250 11 5")
    assert uit["ok"] is False and uit["step"] == "afstand"


# --- de ronde -----------------------------------------------------------------

def test_een_ronde_schrijft_metingen_namen_en_buren_weg(db, monkeypatch):
    _node_op_afstand(monkeypatch)
    rep = _sensornode(db, seen=None)
    uit = sensornode.poll(rep)
    assert uit["ok"] is True
    laatste = db.latest_for(rep["id"])
    assert laatste["ch5_generic"]["value"] == 37.0
    assert db.channel_names_for(rep["id"])[5]["name"] == "google (google.com)"
    # Op twaalf hextekens en niet op vierenzestig: /acl.json meldt de volle
    # sleutel, en de burentabel van deze site draait op de eerste zes byte.
    assert [r["prefix"] for r in db.neighbor_rows(rep["id"])] == ["bb" * 6]
    # En de waarneming die het beheerniveau draagt.
    verse = db.qone("SELECT * FROM repeaters WHERE id=?", (rep["id"],))
    assert verse["sensor_seen"] and verse["sensor_fw"] == "1.4.0"


def test_een_mislukte_ronde_wist_de_waarneming_niet(db, monkeypatch):
    """Wat we ooit vastgesteld hebben blijft waar, ook als de node nu niet opneemt.

    Zelfde regel als bij ``record_firmware``. Het niveau zou anders op en neer
    springen met een WiFi-verbinding, en dan zegt het niets meer.
    """
    monkeypatch.setattr(sensornode.firmware, "NODE_USER", "admin")
    rep = _sensornode(db, seen="2026-08-20T09:00:00Z")

    def weg(*a, **k):
        raise urllib.error.URLError("weg")

    monkeypatch.setattr(nodeconfig, "_open", weg)
    uit = sensornode.poll(rep)
    assert uit["ok"] is False and "niet bereikbaar" in uit["error"]
    verse = db.qone("SELECT sensor_seen FROM repeaters WHERE id=?", (rep["id"],))
    assert verse["sensor_seen"] == "2026-08-20T09:00:00Z"


def test_een_adres_wissen_wist_ook_de_waarneming(db):
    """Anders houdt een node zijn niveau over aan een adres dat niemand meer kan
    navragen -- en dan belooft de pagina iets waarvan de reden weg is."""
    rep = _sensornode(db)
    db.set_sensor_host(rep["id"], "")
    verse = db.qone("SELECT * FROM repeaters WHERE id=?", (rep["id"],))
    assert verse["sensor_host"] is None and verse["sensor_seen"] is None


def test_een_node_die_omvalt_neemt_de_ronde_niet_mee(db, monkeypatch):
    """De ronde loopt over apparaten die los van elkaar stuk kunnen zijn.

    Een uitzondering op de tweede zou de derde tot na het volgende interval laten
    wachten, zonder dat er ergens staat waarom.
    """
    _sensornode(db)
    db.set_sensor_host(db.get_or_create_repeater("112233445566", "Tweede")["id"],
                       "192.168.110.161")
    monkeypatch.setattr(sensornode, "poll",
                        lambda rep, timeout=None: (_ for _ in ()).throw(RuntimeError("boem")))
    uit = sensornode.run_once()
    assert uit == {"nodes": 2, "ok": 0, "failed": 2}


# --- de klok ------------------------------------------------------------------

def test_de_klok_gaat_langs_hetzelfde_oordeel_als_de_mesh_weg(db, monkeypatch):
    """Geen tweede oordeel over "weten wij hoe laat het is".

    Dat oordeel staat in ``clocksync.check_clock`` en is er streng om een reden:
    de correctie gaat één kant op en de weg terug loopt over een dak. Een knop
    met zijn eigen mening zou een achterdeur om die controle heen zijn.
    """
    verstuurd = _node_op_afstand(monkeypatch)
    rep = _sensornode(db)
    monkeypatch.setattr(sensornode.clocksync, "check_clock",
                        lambda now=None: {"ok": False, "reason": "kernel weet het niet"})
    uit = sensornode.set_clock(rep)
    assert uit["ok"] is False and uit["outcome"] == "no_clock"
    assert uit["reason"] == "kernel weet het niet"
    assert verstuurd == [], "er mocht geen tijd de deur uit"


def test_een_gezette_klok_komt_in_hetzelfde_grootboek(db, monkeypatch):
    """Eén antwoord op "wanneer heeft deze site deze node de tijd gestuurd"."""
    verstuurd = _node_op_afstand(monkeypatch)
    rep = _sensornode(db)
    monkeypatch.setattr(sensornode.clocksync, "check_clock",
                        lambda now=None: {"ok": True, "reason": ""})
    uit = sensornode.set_clock(rep)
    assert uit["ok"] is True and uit["outcome"] == "sent"
    assert verstuurd[0][0] == "/cli" and verstuurd[0][1].startswith("cmd=time+")
    assert sensornode.clocksync.last_sent("48d7aade232b") is not None


# --- de regio -----------------------------------------------------------------

def test_een_regio_wordt_gezet_en_vastgelegd(monkeypatch):
    """Twee opdrachten, want zonder 'region save' is de instelling weg bij de
    eerstvolgende herstart -- en dat merk je dan pas."""
    verstuurd = _node_op_afstand(monkeypatch)
    uit = sensornode.set_region({"sensor_host": "host"}, "home", "eu be")
    assert uit["ok"] is True
    assert [b for _, b in verstuurd] == ["cmd=region+home+eu+be", "cmd=region+save"]


def test_een_regionaam_kan_geen_tweede_opdracht_smokkelen(monkeypatch):
    """De CLI leest tot het einde van de regel, dus de waarde is het laatste
    woord -- en er hoort dan ook geen scheider in te kunnen."""
    verstuurd = _node_op_afstand(monkeypatch)
    for slecht in ("eu be; erase", "eu\nreboot", "eu be && reboot", "", "x" * 41):
        uit = sensornode.set_region({"sensor_host": "host"}, "home", slecht)
        assert uit["ok"] is False, slecht
    assert verstuurd == []


def test_een_mislukte_save_is_geen_gelukte_wijziging(monkeypatch):
    """De derde uitkomst: gezet maar niet vastgelegd. Dat is iets om te weten
    vóór de volgende herstart en niet erna."""
    antwoorden = [b"OK", b"Error: kan niet schrijven"]

    def nep(host, path, data=None, timeout=None):
        return _Antwoord(antwoorden.pop(0))

    monkeypatch.setattr(sensornode.firmware, "NODE_USER", "admin")
    monkeypatch.setattr(nodeconfig, "_open", nep)
    uit = sensornode.set_region({"sensor_host": "host"}, "default", "eu")
    assert uit["ok"] is False
    assert "niet vastgelegd" in uit["error"]


# --- de vergelijking ----------------------------------------------------------

def test_de_terugleesvergelijking_spiegelt_de_firmware():
    """Zelfde marge als ``cfgSameValue()``: getallen met 0,0005 speling, tekst exact.

    Een float die door een tekstveld en terug is geweest wijkt in de vijfde
    decimaal af, en dat is geen mislukking. Een naam die afwijkt is dat wel.
    """
    assert sensornode.same_value("float", "1.000", "1.0") is True
    assert sensornode.same_value("int", "22", "22") is True
    assert sensornode.same_value("int", "22", "20") is False
    assert sensornode.same_value("float", "100", "100 %") is True
    assert sensornode.same_value("text", "DinX", "dinx") is False
    assert sensornode.same_value("enum", "off", "off") is True


def test_de_foutherkenning_laat_een_node_zijn_eigen_naam_houden():
    """Beide spellingen waarmee MeshCore weigert, voluit -- zoals ``cfgIsError()``.

    Op 'Err' alleen zou een node die 'Erratic' heet zijn eigen naam niet meer
    kunnen terugkrijgen.
    """
    assert sensornode.is_error("Error: nope") is True
    assert sensornode.is_error("Err - nope") is True
    assert sensornode.is_error("> Error: nope") is True
    assert sensornode.is_error("Erratic") is False
    assert sensornode.is_error("OK") is False


# --- welk doel mag de server benaderen ---------------------------------------
#
# Twee fouten in één veld, en ze zijn beide HOOG: de server verbindt naar een
# doel dat een gebruiker koos (SSRF), en hij stuurt MM_FW_NODE_USER/PASS mee in
# de Authorization-header -- de inloggegevens waarmee firmware en instellingen
# naar ELKE node geschreven worden. Eén ingevuld tekstveld en de vloot is weg.
#
# De spanning die deze reeks vastlegt: "weiger private adressen" is hier geen
# oplossing, want de nodes van dit project STAAN op 192.168.x. Het onderscheid
# zit dus niet in het adres maar in WIE het vastlegde.

def test_een_adres_dat_geen_serverbeheerder_zette_krijgt_geen_verbinding(db, monkeypatch):
    """De kern van de reparatie, en de reden dat de toets bij het VERBINDEN staat.

    Een controle die alleen in het formulier zit, is met een aangepast verzoek te
    omzeilen. Deze staat op de plek waar het wachtwoord de deur uit gaat.
    """
    from app import firmware

    monkeypatch.setattr(firmware, "NODE_USER", "admin")
    rep = db.get_or_create_repeater("48d7aade232b", "MeshUptime")
    db.set_sensor_host(rep["id"], "192.168.110.160", by_admin=False)
    toets = firmware.check_target("192.168.110.160")
    assert toets["ok"] is False
    assert "serverbeheerder" in toets["error"]


def test_hetzelfde_adres_mag_wel_als_een_serverbeheerder_het_zette(db, monkeypatch):
    """Een LAN-adres opgeven is inherent een beheerdersdaad.

    Daarom is de toegestane-lijst geen adresbereik maar de databank: wat een
    serverbeheerder heeft vastgelegd, mag benaderd worden -- ook, en juist, als
    het 192.168.x is.
    """
    from app import firmware

    monkeypatch.setattr(firmware, "NODE_USER", "admin")
    rep = db.get_or_create_repeater("48d7aade232b", "MeshUptime")
    db.set_sensor_host(rep["id"], "192.168.110.160", by_admin=True)
    assert firmware.check_target("192.168.110.160")["ok"] is True
    assert firmware.check_target("192.168.110.160")["private"] is True


def test_een_publiek_adres_van_een_vreemde_mag_evenmin(db, monkeypatch):
    """Het lek is niet dat de server een LAN aanraakt; het lek is het wachtwoord.

    Naar de server van een aanvaller op een publiek adres is dat precies zo erg,
    en "alleen private adressen toetsen" zou het gat openlaten aan de kant waar
    het het makkelijkst te misbruiken is.
    """
    from app import firmware

    monkeypatch.setattr(firmware, "NODE_USER", "admin")
    db.get_or_create_repeater("48d7aade232b", "MeshUptime")
    assert firmware.check_target("http://example.invalid")["ok"] is False


def test_wissen_haalt_het_adres_van_de_toegestane_lijst(db):
    """Anders erft een nieuw adres de vlag van zijn voorganger.

    Dat zou het gat precies terugzetten: wissen mag met een gedelegeerd recht, en
    daarna zou een ingevuld adres vertrouwd heten zonder dat een serverbeheerder
    er iets van gezien heeft.
    """
    from app import firmware

    rep = db.get_or_create_repeater("48d7aade232b", "MeshUptime")
    db.set_sensor_host(rep["id"], "192.168.110.160", by_admin=True)
    assert "192.168.110.160" in firmware.trusted_hosts()
    db.set_sensor_host(rep["id"], "")
    assert firmware.trusted_hosts() == set()
    db.set_sensor_host(rep["id"], "192.168.110.99", by_admin=False)
    assert firmware.trusted_hosts() == set()


def test_de_toets_kijkt_naar_het_opgeloste_adres_en_niet_naar_de_tekst(db, monkeypatch):
    """Een naam die naar 127.0.0.1 wijst is loopback, hoe publiek hij ook klinkt.

    Dat is hier geen poort meer -- de toegestane-lijst is de poort -- maar het
    bepaalt wél wat de melding zegt, en het is de reden dat er opgezocht wordt in
    plaats van geraden.
    """
    from app import firmware

    monkeypatch.setattr(firmware, "NODE_USER", "admin")
    rep = db.get_or_create_repeater("48d7aade232b", "MeshUptime")
    db.set_sensor_host(rep["id"], "http://localhost:8080", by_admin=True)
    toets = firmware.check_target("http://localhost:8080")
    assert toets["ok"] is True and toets["private"] is True


@pytest.mark.parametrize("adres", ["0.0.0.0", "224.0.0.1"])
def test_bereiken_die_nooit_een_node_zijn_worden_altijd_geweigerd(db, monkeypatch, adres):
    """Ook voor een serverbeheerder. Een verbinding naar 0.0.0.0 of naar een
    multicastadres is geen beheeradres maar een vergissing of een poging."""
    from app import firmware

    monkeypatch.setattr(firmware, "NODE_USER", "admin")
    rep = db.get_or_create_repeater("48d7aade232b", "MeshUptime")
    db.set_sensor_host(rep["id"], adres, by_admin=True)
    assert firmware.check_target(adres)["ok"] is False


def test_geen_enkele_uitgaande_verbinding_gaat_om_de_toets_heen(db, monkeypatch):
    """De vangnettest: één plek waar een socket opengaat.

    ``firmware.open_node`` is de enige functie die de Authorization-header zet, en
    daarom de enige plek waar de toets hoort te staan. Deze test dwingt af dat
    ``nodeconfig._open`` -- waar de instellingenweg, het pakketfilter en de
    sensor-API alle drie op uitkomen -- er werkelijk langs gaat en niet zelf
    verbindt.
    """
    from app import firmware

    monkeypatch.setattr(firmware, "NODE_USER", "admin")
    db.get_or_create_repeater("48d7aade232b", "MeshUptime")
    monkeypatch.setattr(firmware, "_url",
                        lambda *a, **k: pytest.fail("kwam voorbij de toets"))
    with pytest.raises(firmware.TargetRefused):
        nodeconfig._open("192.168.110.160", "/status.json")


def test_een_geweigerd_adres_levert_leesbare_tekst_en_geen_500(db, monkeypatch):
    """De weigering komt op de pagina terecht als een zin.

    ``TargetRefused`` is een ValueError, en elke aanroeper in nodeconfig vangt die
    al af -- dus dit werkt zonder dat elk pad zijn eigen behandeling nodig heeft.
    """
    from app import firmware

    monkeypatch.setattr(firmware, "NODE_USER", "admin")
    db.get_or_create_repeater("48d7aade232b", "MeshUptime")
    uit = sensornode.status("192.168.110.160")
    assert uit["ok"] is False
    assert "serverbeheerder" in uit["error"]


# --- en of de pagina het overleeft -------------------------------------------

def test_de_nodepagina_rendert_met_deze_weg_erop(db, monkeypatch):
    """De vangnettest, en ze vangt iets wat de rest van dit bestand niet vangt.

    Alles hierboven roept functies aan en kijkt naar wat ze teruggeven. Voor een
    sjabloon is dat niet genoeg: bijna alles wat op deze pagina kan misgaan zit in
    de takken die zeggen *waarom* iets uit staat, en een tikfout daarin is een
    lege beheerpagina en geen falende test. Dezelfde reden als
    test_beheerpaginas_renderen.py.

    Met alles erop wat vandaag nieuw is: een bereikbare eigen API, een kanaalnaam
    die van de node komt, een openstaand alarm en een toegangslijst.
    """
    from app import auth, mqtt_ingest, rbac, routes_admin

    rep = _sensornode(db)
    db.ingest(rep["id"], db.utcnow(),
              {"ch5_switch": 1, "ch5_generic": 37, "ch1_voltage": 4.1}, None)
    db.set_channel_name(rep["id"], 5, "google (google.com)", "ms",
                        source=db.SOURCE_AUTO)
    db.add_alert(rep["id"], "hoas onbereikbaar (hoas.local)", source="mesh",
                 severity="hoog")
    rbac.maak_gebruiker("beheerder", "x", is_superuser=True, door="test")
    monkeypatch.setattr(mqtt_ingest, "can_publish", lambda: False)
    monkeypatch.setattr(sensornode, "acl", lambda host, timeout=None: {
        "ok": True, "error": "", "data": {
            "acl": [{"k": "aa" * 32, "n": "DinX-Home", "p": 3, "a": 12}],
            "nb": [{"k": "bb" * 32, "n": "X", "t": 2, "h": 1, "s": "8.5",
                    "a": 30, "c": 4, "in": 1}]}})

    class _Req:
        cookies = {auth.SESSION_COOKIE: auth.make_session("beheerder")}
        query_params: dict = {}

    body = routes_admin.node_page(_Req(), rep["id"]).body.decode("utf-8")
    # Het niveau, en de zin die erbij hoort: deze node publiceert niets over MQTT
    # en heet toch volledig beheerd.
    assert "full managed" in body
    assert "eigen API" in body
    # De sectie, met de eerlijkheid over de weg erin.
    assert "Beheer over IP" in body
    assert "Valt de WiFi weg" in body
    # De kanaalnaam die van de node komt, met de herkomst erbij.
    assert "google (google.com)" in body
    assert "van de node" in body
    # Het alarm, en de toegangslijst waar de mesh-weg op stukloopt.
    assert "hoas onbereikbaar" in body
    assert "Toegangslijst" in body
