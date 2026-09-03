"""Tests voor de tweede filtervariant in het mesh (``app/pfstock.py``).

Waarom dit een eigen bestand verdient: er praten twee verschillende
filterimplementaties tegen dezelfde pagina, en de kleinere van de twee levert
een SUBSET van de cijfers. De fout die hier voorkomen wordt is niet een
foutmelding maar een grafiek: ``passed: 0`` verzinnen omdat de stock-firmware
geen doorlaatteller heeft, ziet er precies zo uit als een repeater die niets
doorlaat. Vandaar dat de helft van deze tests over ONTBREKENDE sleutels gaat en
niet over verkeerde waarden.

De tweede helft gaat over de vraag "welke variant praat er eigenlijk", want daar
hangt af of een leeg vak op de pagina een melding is of een eigenschap.
"""
import sqlite3

import pytest

from app import pfstock

# --- de voorbeeldantwoorden ---------------------------------------------------
#
# Zoals ze uit `filter count` en `filter types` komen, prompt-echo incluis: dat
# is de tekst die de CLI-weg teruggeeft en dus de tekst die de parser aankan
# moet zijn.

UIT = """> Filter off: Blocked [ Hops: 0 | Rate: 0 | Channel: 0 | Hash: 0 | Malformed: 0 ]
[TYPE: HOPS,RATE]
00: 0,0
01: 0,0
02: 0,0
03: 0,0
04: 0,0
05: 0,0
06: 0,0
07: 0,0
08: 0,0
09: 0,0
10: 0,0
"""

AAN = """> Filter on: Blocked [ Hops: 1 | Rate: 7 | Channel: 0 | Hash: 535 | Malformed: 2 ]
[TYPE: HOPS,RATE]
00: 0,0
01: 0,0
02: 3,0
03: 0,0
04: 2,20
05: 0,0
06: 0,0
07: 0,0
08: 0,0
09: 0,0
10: 0,0
"""

TYPES = ("00=REQ 01=RESPONSE 02=TXT_MSG 03=ACK 04=ADVERT 05=GRP_TXT "
         "06=GRP_DATA 07=ANON_REQ 08=PATH 09=TRACE 10=MULTIPART 11=CONTROL")


# --- de tellers en de limieten ------------------------------------------------

def test_hoofdschakelaar_uit_en_aan():
    assert pfstock.parse_filter_count(UIT)["on"] is False
    assert pfstock.parse_filter_count(AAN)["on"] is True


def test_de_vijf_gemelde_redenen_komen_in_drop():
    blob = pfstock.parse_filter_count(AAN)
    assert blob["drop"] == {"hops": 1, "rate": 7, "hash": 535, "misvormd": 2,
                            "kanaal": 0}


def test_een_gemelde_nul_blijft_een_nul():
    """Een nul die de node meldt is een meting en hoort in de blob.

    Dezelfde regel als in ``mqtt_ingest._filter_metrics``: 'dit filter gooide
    niets weg op de kanaalregel' is een cijfer, en het weglaten zou de reeks
    laten ophouden op een node waar niets aan de hand is.
    """
    blob = pfstock.parse_filter_count(UIT)
    assert blob["drop"]["kanaal"] == 0
    assert set(blob["drop"]) == {"hops", "rate", "hash", "kanaal", "misvormd"}


def test_geen_verzonnen_type_teller():
    # De stock-variant kan een pakkettype niet dichtzetten en telt er dus niets
    # voor. Een sleutel 'type' zou beweren dat die regel bestaat.
    assert "type" not in pfstock.parse_filter_count(AAN)["drop"]


@pytest.mark.parametrize("sleutel", ["passed", "exempt"])
def test_wat_de_firmware_niet_meldt_staat_er_niet_als_nul(sleutel):
    assert sleutel not in pfstock.parse_filter_count(AAN)


@pytest.mark.parametrize("sleutel", ["hash", "malformed", "channels",
                                     "blocked_types", "disarmed"])
def test_de_regelvelden_blijven_leeg(sleutel):
    """De valkuil van dit formaat: ``Hash`` en ``Malformed`` zijn hier TELLERS.

    In de blob van onze eigen firmware is ``hash`` de ingestelde minimale
    padhash en ``malformed`` een aan/uit-schakelaar. Ze uit dit antwoord vullen
    zou een teller als een regel presenteren -- en 535 weggegooide pakketten
    zouden dan een 'minimale padhash 535' worden.
    """
    blob = pfstock.parse_filter_count(AAN)
    assert sleutel not in blob


def test_limieten_zijn_configuratie_en_staan_niet_bij_de_tellers():
    blob = pfstock.parse_filter_count(AAN)
    assert blob["limits"]["TXT_MSG"] == {"hops": 3, "rate": 0}
    assert blob["limits"]["ADVERT"] == {"hops": 2, "rate": 20}
    # En vooral: niet in drop, want ze lopen nooit op.
    assert set(blob["drop"]) & set(blob["limits"]) == set()


def test_nul_komma_nul_is_geen_limiet_maar_wel_een_melding():
    # 'voor dit type geldt geen limiet' is iets anders dan 'dit type stond niet
    # in het antwoord'; een formulier moet die twee kunnen onderscheiden.
    limits = pfstock.parse_filter_count(UIT)["limits"]
    assert len(limits) == 11
    assert limits["REQ"] == {"hops": 0, "rate": 0}


def test_variantmerk_staat_in_de_blob():
    assert pfstock.parse_filter_count(AAN)["variant"] == "meshcore_filter"


def test_nummering_van_de_node_krijgt_voorrang():
    """Wie ``filter types`` vroeg, mag die nummering laten meewegen.

    Een patch die er een type bij zet mag geen labels laten verschuiven, dus de
    lijst van de node wint van onze eigen lijst.
    """
    namen = {2: "GROEPSTEKST"}
    blob = pfstock.parse_filter_count(AAN, names=namen)
    assert "GROEPSTEKST" in blob["limits"]
    assert "TXT_MSG" not in blob["limits"]
    # De rest valt terug op onze lijst.
    assert blob["limits"]["ADVERT"] == {"hops": 2, "rate": 20}


# --- ontbrekende en afgekapte blokken -----------------------------------------

def test_alleen_de_kopregel_levert_geen_lege_limiettabel():
    blob = pfstock.parse_filter_count(
        "Filter on: Blocked [ Hops: 4 | Rate: 0 | Channel: 0 | Hash: 0 | Malformed: 0 ]")
    assert blob["on"] is True
    assert blob["drop"]["hops"] == 4
    # Geen lege dict maar geen sleutel: 'niet gemeld' en 'leeg' zijn niet
    # hetzelfde, en de pagina zegt die twee anders.
    assert "limits" not in blob


def test_alleen_de_limiettabel_levert_geen_tellers():
    blob = pfstock.parse_filter_count("Filter off:\n[TYPE: HOPS,RATE]\n04: 2,20\n")
    assert blob["on"] is False
    assert "drop" not in blob
    assert blob["limits"] == {"ADVERT": {"hops": 2, "rate": 20}}


def test_afgekapte_laatste_regel_wordt_niet_half_gelezen():
    tekst = AAN[:AAN.index("04: 2,20")] + "04: 2,"
    blob = pfstock.parse_filter_count(tekst)
    assert "ADVERT" not in blob["limits"]
    assert blob["limits"]["TXT_MSG"] == {"hops": 3, "rate": 0}
    # De tellers uit de kopregel staan er nog: die regel was compleet.
    assert blob["drop"]["hash"] == 535


def test_afgekapt_midden_in_de_kopregel_is_geen_filterantwoord():
    # Zonder hoofdschakelaar weten we niet of we naar het goede antwoord kijken.
    assert pfstock.parse_filter_count("> Filter") is None


def test_onbekende_categorie_wordt_genegeerd_en_niet_doorgegeven():
    blob = pfstock.parse_filter_count(
        "Filter on: Blocked [ Hops: 2 | Gremlins: 9 ]")
    assert blob["drop"] == {"hops": 2}


# --- rommel en onzin ----------------------------------------------------------

@pytest.mark.parametrize("tekst", [
    "",
    "   \n\n",
    None,
    b"Filter on: Blocked [ Hops: 0 ]",
    "Err - unknown command",
    "OK",
    "> ver\nv1.17.1-PS+filter+rollback (Build: 3 Feb 2026)",
    "{\"on\": true, \"drop\": {}}",
])
def test_wat_geen_filterantwoord_is_levert_none(tekst):
    assert pfstock.parse_filter_count(tekst) is None


@pytest.mark.parametrize("waarde", ["-1", "99999999999999", "4000000001"])
def test_onmogelijke_teller_valt_af_zonder_de_rest_mee_te_nemen(waarde):
    blob = pfstock.parse_filter_count(
        f"Filter on: Blocked [ Hops: {waarde} | Rate: 7 ]")
    assert "hops" not in blob["drop"]
    # De andere vier zijn gemeten en staan hier los van.
    assert blob["drop"]["rate"] == 7


@pytest.mark.parametrize("regel", ["04: -1,20", "04: 2,-1", "04: 999,20",
                                   "04: 2,99999", "04: 2", "04: 2,20,30",
                                   "04: a,b"])
def test_onmogelijke_limietregel_valt_helemaal_af(regel):
    """Hier de hele regel en niet één veld.

    De twee getallen komen uit hetzelfde paar; 'hoplimiet 2, snelheidslimiet
    onbekend' is geen toestand die de node kan hebben, en hem half opslaan zou
    een formulier een verzonnen waarde laten terugschrijven.
    """
    blob = pfstock.parse_filter_count(f"Filter on:\n{regel}\n05: 1,10\n")
    assert blob.get("limits", {}) == {"GRP_TXT": {"hops": 1, "rate": 10}}


def test_limiet_voor_een_type_dat_niet_gezet_kan_worden_valt_af():
    # `filter types` toont t/m 11, maar `filter hops`/`filter rate` aanvaarden
    # alleen 0-10. Een limiet voor 11 kan dus nergens vandaan komen.
    blob = pfstock.parse_filter_count("Filter on:\n10: 1,1\n11: 3,30\n")
    assert set(blob["limits"]) == {"MULTIPART"}


def test_limietregels_verzinnen_geen_tellers():
    # De regel ``04: 2,20`` bevat een dubbele punt en cijfers. Zou de
    # tellerparser over de hele tekst lopen, dan zou hij er een reden bij maken.
    blob = pfstock.parse_filter_count("Filter off:\n00: 0,0\n04: 2,20\n")
    assert "drop" not in blob


# --- de typenummering ---------------------------------------------------------

def test_types_op_een_regel():
    namen = pfstock.parse_filter_types(TYPES)
    assert namen[0] == "REQ"
    assert namen[11] == "CONTROL"
    assert len(namen) == 12


def test_types_over_meerdere_regels():
    namen = pfstock.parse_filter_types("> filter types\n00=REQ\n01=RESPONSE\n")
    assert namen == {0: "REQ", 1: "RESPONSE"}


def test_nummers_zijn_getallen_en_geen_strings():
    # Zodat er numeriek op gesorteerd en vergeleken kan worden: "10" < "9".
    namen = pfstock.parse_filter_types(TYPES)
    assert sorted(namen)[-1] == 11


def test_type_buiten_de_nummering_wordt_genegeerd():
    namen = pfstock.parse_filter_types("00=REQ 99=ONZIN")
    assert namen == {0: "REQ"}


@pytest.mark.parametrize("tekst", ["", None, "Err - unknown command",
                                   "00: 0,0", b"00=REQ"])
def test_geen_typeantwoord_levert_none(tekst):
    assert pfstock.parse_filter_types(tekst) is None


# --- welke variant praat er ---------------------------------------------------

def rij(**velden) -> dict:
    basis = {"fw": "", "fw_meshmanager": ""}
    basis.update(velden)
    return basis


def test_onze_eigen_module_is_de_meshmanager_variant():
    assert pfstock.variant(rij(fw="v1.17.0", fw_meshmanager="2.10.0")) == "meshmanager"


def test_versievergelijking_is_numeriek():
    # "2.10.0" komt alfabetisch vóór "2.3.0", en dat is net de firmware die het
    # wél kan.
    assert pfstock.variant(rij(fw_meshmanager="2.10.0")) == "meshmanager"
    assert pfstock.variant(rij(fw_meshmanager="2.3.0")) == "meshmanager"


def test_gepatchte_stock_firmware():
    assert pfstock.variant(rij(fw="v1.17.1-PS+filter+rollback")) == "meshcore_filter"


def test_onze_module_wint_van_het_plusteken():
    """Onze module draait bovenop MeshCore, dus die firmwarestring kan óók
    ``+filter`` bevatten. Dan is de rijkere weg de juiste."""
    r = rij(fw="v1.17.1-PS+filter", fw_meshmanager="2.10.0")
    assert pfstock.variant(r) == "meshmanager"


def test_bekende_firmware_zonder_filter():
    assert pfstock.variant(rij(fw="v1.17.0")) == "geen"


def test_te_oude_module_heeft_echt_geen_filter():
    # 2.0.0 kent het filter niet, dus dit is geen 'onbekend' maar een 'geen'.
    assert pfstock.variant(rij(fw="v1.17.0", fw_meshmanager="2.0.0")) == "geen"


def test_nog_niets_gehoord_is_niet_hetzelfde_als_geen_filter():
    assert pfstock.variant(rij()) == "onbekend"
    assert pfstock.variant(None) == "onbekend"
    assert pfstock.variant({}) == "onbekend"


def test_een_sqlite_rij_komt_hier_net_zo_goed_binnen():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("CREATE TABLE r(fw TEXT, fw_meshmanager TEXT)")
    conn.execute("INSERT INTO r VALUES('v1.17.1-PS+filter', NULL)")
    row = conn.execute("SELECT * FROM r").fetchone()
    assert pfstock.variant(row) == "meshcore_filter"
    conn.close()


def test_een_rij_zonder_firmwarekolommen_gokt_niet():
    # Een sqlite3.Row zonder die kolommen gooit IndexError; dat mag geen 500 zijn.
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("CREATE TABLE r(id INTEGER)")
    conn.execute("INSERT INTO r VALUES(1)")
    row = conn.execute("SELECT * FROM r").fetchone()
    assert pfstock.variant(row) == "onbekend"
    conn.close()


def test_de_ondergrens_blijft_gelijk_aan_die_van_de_schrijfweg():
    # Twee constanten met opzet, één waarheid: pfstock trekt de schrijfweg niet
    # mee voor één tupel, maar mag er ook niet van afwijken.
    from app import pktfilter
    assert pfstock.MIN_MESHMANAGER_FILTER == pktfilter.MIN_FILTER_VERSION


# --- wat een variant kan leveren ----------------------------------------------

def test_de_stock_variant_kan_minder_en_zegt_dat():
    stock = pfstock.capabilities("meshcore_filter")
    assert stock["aan_uit"] is True
    assert stock["drop_per_reden"] is True
    assert stock["limieten"] is True
    # En dit is waar het om gaat: een leeg vak dat uitgelegd kan worden.
    assert stock["passed"] is False
    assert stock["exempt"] is False
    assert stock["drop_per_type"] is False
    assert stock["snelheidsdruk"] is False
    assert stock["kanalen"] is False


def test_onze_eigen_firmware_kan_alles_wat_de_stock_variant_kan():
    mm = pfstock.capabilities("meshmanager")
    stock = pfstock.capabilities("meshcore_filter")
    for sleutel, waarde in stock.items():
        if waarde is True:
            assert mm[sleutel] is True, sleutel
    assert set(stock["drop_redenen"]) < set(mm["drop_redenen"])


def test_de_redenen_horen_bij_de_sleutels_die_de_parser_oplevert():
    stock = pfstock.capabilities("meshcore_filter")
    blob = pfstock.parse_filter_count(AAN)
    assert set(stock["drop_redenen"]) == set(blob["drop"])


def test_een_variant_zonder_filter_belooft_niets():
    for naam in ("geen", "onbekend", "iets-van-volgend-jaar"):
        caps = pfstock.capabilities(naam)
        assert not any(v for v in caps.values() if isinstance(v, bool))
        assert caps["drop_redenen"] == ()


def test_de_tabel_is_niet_te_vergiftigen():
    # Uit een template gelezen, dus een aanroeper die erin schrijft mag de
    # volgende aanroep niet raken.
    pfstock.capabilities("meshmanager")["passed"] = False
    assert pfstock.capabilities("meshmanager")["passed"] is True


# --- past het op de bestaande opslag ------------------------------------------

def test_de_bestaande_samenvatting_leest_deze_blob_zonder_tweede_codepad():
    from app import pktfilter
    samenvatting = pktfilter.summarise(pfstock.parse_filter_count(AAN))
    assert samenvatting["bekend"] is True
    assert samenvatting["aan"] is True
    assert samenvatting["weg"] == 1 + 7 + 0 + 535 + 2
    # Geen verzonnen regels: de ontbrekende regelvelden mogen niet als een
    # ingestelde padhash of een kanaalregel gaan meetellen.
    assert samenvatting["regels"] == 0
    assert samenvatting["door"] == 0


def test_de_ingest_maakt_geen_doorlaatreeks_van_niets():
    """De reden dat ``passed`` ontbreekt in plaats van 0 te zijn.

    ``_filter_metrics`` maakt van elke gemelde nul een metric, en dat is goed:
    een gemeten nul hoort in de grafiek. Precies daarom mag een cijfer dat de
    firmware niet kent er niet als nul in staan -- dan zou de doorlaatgrafiek
    van deze repeater een vlakke lijn op nul worden in plaats van afwezig.
    """
    from app import mqtt_ingest
    metrics = mqtt_ingest._filter_metrics(pfstock.parse_filter_count(AAN))
    assert metrics["filter_on"] == 1.0
    assert metrics["filter_drop_hash"] == 535.0
    assert metrics["filter_dropped"] == 545.0
    assert "filter_passed" not in metrics
    assert "filter_exempt" not in metrics
    assert "filter_drop_type" not in metrics
