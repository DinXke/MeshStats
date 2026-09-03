"""Tests voor de uitlegtabel achter de filtercijfers (``app/pfhelp.py``).

Waarom een tabel met alleen tekst erin een test verdient: de fout die hier
gemaakt kan worden is niet een uitzondering maar een LEEG VAKJE. Een metric die
in ``metrics.py`` bestaat maar hier niet, levert een tegel met een getal en een
``?`` die niets zegt -- en dat is precies de toestand die dit bestand moest
wegnemen. Zo'n gat kan geen enkele andere test opmerken, want alles rendert
prima.

Vandaar twee soorten controles:

- **volledigheid**: elke metric uit de lijst heeft een vermelding, en elke
  verplichte tekst is werkelijk gevuld voor elke variant.
- **eerlijkheid**: een regel is óf ondersteund MET syntax, óf niet ondersteund
  MET een reden. Een halfgevulde regel -- ondersteund maar zonder syntax, of
  onondersteund zonder uitleg -- is de vorm waarin een gok binnenglipt.
"""
import pytest

from app import pfhelp, pfstock

# De metricnamen zoals ze in de databank staan. Hier met opzet HERHAALD en niet
# uit ``pfhelp.metric_names()`` gehaald: een test die zijn verwachting uit het
# onderwerp haalt, bewijst alleen dat het onderwerp met zichzelf overeenkomt.
# Deze twaalf zijn de lijst waar de pagina op rekent.
METRICS = (
    "filter_on",
    "filter_dropped",
    "filter_passed",
    "filter_exempt",
    "filter_drop_hops",
    "filter_drop_rate",
    "filter_drop_type",
    "filter_drop_hash",
    "filter_drop_channel",
    "filter_drop_malformed",
    "filter_rate_windows",
    "filter_rate_capped",
)

# Velden die bij ELKE metric en elke variant gevuld moeten zijn, ongeacht of de
# instelling bestaat. Wat het cijfer betekent, staat immers los van de vraag of
# je hem kunt zetten.
VERPLICHT_ALTIJD = ("metric", "subfilter", "soort", "meting_nl", "meting_en",
                    "instelling", "instelling_en")

# Velden die er alleen hoeven te zijn als wij de instelling kennen.
VERPLICHT_ONDERSTEUND = ("syntax", "doet_nl", "doet_en", "leescommando")


# --- volledigheid -------------------------------------------------------------

@pytest.mark.parametrize("metric", METRICS)
@pytest.mark.parametrize("variant", pfhelp.VARIANTS)
def test_elke_metric_heeft_een_vermelding(metric, variant):
    hulp = pfhelp.help_for(metric, variant)
    assert hulp is not None, f"{metric} heeft geen vermelding in pfhelp"
    assert hulp["metric"] == metric
    assert hulp["variant"] == variant


@pytest.mark.parametrize("metric", METRICS)
@pytest.mark.parametrize("variant", pfhelp.VARIANTS)
def test_geen_lege_verplichte_velden(metric, variant):
    hulp = pfhelp.help_for(metric, variant)
    for veld in VERPLICHT_ALTIJD:
        assert str(hulp.get(veld) or "").strip(), (
            f"{metric}/{variant}: veld {veld} is leeg")


@pytest.mark.parametrize("metric", METRICS)
@pytest.mark.parametrize("variant", pfhelp.VARIANTS)
def test_ondersteund_met_syntax_of_niet_met_reden(metric, variant):
    """De eerlijkheidsregel: geen halfgevulde regel.

    Dit is de test die een gok tegenhoudt. Wie een instelling toevoegt waarvan
    hij de syntax niet kent, moet hier langs -- en dan is de enige manier om
    groen te blijven, opschrijven dat hij het niet weet.
    """
    hulp = pfhelp.help_for(metric, variant)
    if hulp["ondersteund"]:
        for veld in VERPLICHT_ONDERSTEUND:
            assert str(hulp.get(veld) or "").strip(), (
                f"{metric}/{variant}: ondersteund maar {veld} is leeg")
    else:
        assert str(hulp.get("niet_nl") or "").strip(), (
            f"{metric}/{variant}: niet ondersteund zonder Nederlandse reden")
        assert str(hulp.get("niet_en") or "").strip(), (
            f"{metric}/{variant}: niet ondersteund zonder Engelse reden")
        assert not str(hulp.get("syntax") or "").strip(), (
            f"{metric}/{variant}: niet ondersteund maar er staat wel syntax")


def test_metric_names_dekt_de_lijst():
    """Geen metric te weinig en geen metric te veel.

    Te veel is ook een fout: een naam hier die de ingest nooit wegschrijft, is
    uitleg bij een tegel die niet bestaat, en die vindt niemand terug.
    """
    assert set(pfhelp.metric_names()) == set(METRICS)


def test_alle_metrics_horen_bij_een_bestaand_subfilter():
    for metric in METRICS:
        sub = pfhelp.subfilter_of(metric)
        assert sub, f"{metric} hangt aan geen enkel subfilter"
        for variant in pfhelp.VARIANTS:
            assert sub in pfhelp._SUBFILTERS[variant], (
                f"subfilter {sub} ontbreekt bij variant {variant}")


def test_all_help_levert_dezelfde_regels_als_help_for():
    tabel = pfhelp.all_help(pfhelp.VARIANT_STOCK)
    assert set(tabel) == set(METRICS)
    for metric, regel in tabel.items():
        assert regel == pfhelp.help_for(metric, pfhelp.VARIANT_STOCK)


# --- de twee valkuilen die deze module moest wegnemen -------------------------

def test_de_waarschuwingen_staan_er_waar_ze_horen():
    """Duur zelf te ontdekken, dus ze horen in de ballon te staan."""
    hash_hulp = pfhelp.help_for("filter_drop_hash", pfhelp.VARIANT_STOCK)
    # Waar het bij deze regel werkelijk over gaat: doorgestuurd verkeer van
    # anderen. Zie de teruggetrokken lockout-bewering in pfhelp.
    assert "ANDEREN" in hash_hulp["waarschuwing_nl"]

    # Een regel op REQ/RESPONSE raakt het beheerverkeer van ANDERE nodes dat hier
    # langs zou gaan -- niet de eigen toegang tot deze node.
    for metric in ("filter_drop_hops", "filter_drop_rate"):
        for variant in pfhelp.VARIANTS:
            hulp = pfhelp.help_for(metric, variant)
            assert "00" in hulp["waarschuwing_nl"] and "01" in hulp["waarschuwing_nl"]


def test_geen_lockout_bewering_meer_in_de_teksten():
    """Teruggetrokken bewering, en ze mag niet terugsluipen.

    De reden staat bij de constanten in pfhelp: voor ons eigen filter is een
    lockout aantoonbaar onmogelijk (docs/packet-filter.md -- het filter wordt
    alleen in allowPacketForward() gevraagd, en verkeer AAN deze node of van een
    client in de access list komt er nooit langs), en voor de EasySkyMesh/
    dutchmeshcore-build is het onbekend. Deze teksten staan achter een '?' op een
    publieke pagina en worden gelezen als feit; een verzonnen gevaar is daar
    schadelijker dan geen waarschuwing.
    """
    verboden = ("lockout", "onbereikbaar maken", "beheerweg over de radio kwijt",
                "lose remote access")
    for variant in pfhelp.VARIANTS:
        for metric in pfhelp.metric_names():
            hulp = pfhelp.help_for(metric, variant)
            if not hulp:
                continue
            for veld in ("doet_nl", "doet_en", "waarschuwing_nl", "waarschuwing_en",
                         "meting_nl", "meting_en", "niet_nl", "niet_en"):
                tekst = (hulp.get(veld) or "").lower()
                for woord in verboden:
                    assert woord not in tekst, f"{variant}/{metric}/{veld}: {woord!r}"

    # De stock-variant overleefde een reboot niet; dat hoort bij de
    # hoofdschakelaar en met het controlecommando erbij.
    aan = pfhelp.help_for("filter_on", pfhelp.VARIANT_STOCK)
    assert "reboot" in aan["waarschuwing_nl"].lower()
    assert "filter count" in aan["waarschuwing_nl"]


def test_de_drukreeksen_verwijzen_naar_elkaar():
    """De verhouding is het cijfer, niet de losse getallen."""
    vensters = pfhelp.help_for("filter_rate_windows", pfhelp.VARIANT_MESHMANAGER)
    geraakt = pfhelp.help_for("filter_rate_capped", pfhelp.VARIANT_MESHMANAGER)
    assert vensters["soort"] == geraakt["soort"] == "druk"
    assert vensters["verhouding_met"] == "filter_rate_capped"
    assert geraakt["verhouding_met"] == "filter_rate_windows"
    # Allebei hangen ze aan de snelheidslimiet, niet aan een eigen instelling.
    assert vensters["subfilter"] == geraakt["subfilter"] == "rate"


def test_hops_nul_betekent_bij_de_twee_varianten_iets_anders():
    """De valkuil waarvoor de tabel per variant gesplitst is.

    ``filter hops <type> 0`` is bij de stock-variant 'geen limiet' en bij onze
    eigen firmware 'helemaal dicht'. Eén gedeelde uitlegtekst zou bij de helft
    van de repeaters het tegenovergestelde aanprijzen.
    """
    stock = pfhelp.help_for("filter_drop_hops", pfhelp.VARIANT_STOCK)
    eigen = pfhelp.help_for("filter_drop_hops", pfhelp.VARIANT_MESHMANAGER)
    assert stock["syntax"] == eigen["syntax"]
    assert stock["doet_nl"] != eigen["doet_nl"]
    assert "geen limiet" in stock["doet_nl"].lower()


def test_kanaallijst_claimt_geen_richting_die_we_niet_kennen():
    """Onze eigen firmware blokkeert; van de andere build weten we het niet.

    Hier stond eerder dat de stock-variant een WITTE lijst is, tegenover een
    zwarte bij ons. Dat was niet te onderbouwen: de firmware-strings van die
    build noemen alleen `channel [list|add|remove]` en "channel %s added/
    removed", en niets over welke kant de lijst op werkt. Vandaar dat de tekst
    daar nu naar `filter channel list` op de node verwijst in plaats van een
    richting te beweren.
    """
    stock = pfhelp.help_for("filter_drop_channel", pfhelp.VARIANT_STOCK)
    eigen = pfhelp.help_for("filter_drop_channel", pfhelp.VARIANT_MESHMANAGER)
    assert "zwarte lijst" in eigen["instelling"]
    for veld in ("instelling", "instelling_en", "doet_nl", "doet_en"):
        tekst = (stock.get(veld) or "").lower()
        assert "witte lijst" not in tekst and "allow-list" not in tekst
    # En het moet de lezer wél ergens heen sturen voor het echte antwoord.
    assert "filter channel list" in stock["doet_nl"]


def test_stock_meldt_minder_cijfers_dan_onze_eigen_firmware():
    """'Deze firmware meldt dit niet' is iets anders dan 'er is niets weg'."""
    for metric in ("filter_passed", "filter_exempt", "filter_drop_type",
                   "filter_rate_windows", "filter_rate_capped"):
        assert not pfhelp.help_for(metric, pfhelp.VARIANT_STOCK)["gemeld"]
    for metric in METRICS:
        assert pfhelp.help_for(metric, pfhelp.VARIANT_MESHMANAGER)["gemeld"]


def test_onbekende_variant_struikelt_niet():
    """Een nieuwe firmwarevariant mag geen pagina omgooien."""
    hulp = pfhelp.help_for("filter_drop_hash", "iets_nieuws")
    assert hulp is not None
    assert hulp["ondersteund"] is False
    assert hulp["niet_nl"] and hulp["niet_en"]
    # 'Onbekend' is geen bewering dat het cijfer niet gemeld wordt.
    assert hulp["gemeld"] is True
    # De meting zelf is variantonafhankelijk en staat er dus wél.
    assert hulp["meting_nl"]


def test_onbekende_metric_levert_none():
    assert pfhelp.help_for("filter_bestaat_niet") is None
    assert pfhelp.subfilter_of("filter_bestaat_niet") == ""


def test_variantnamen_zijn_dezelfde_als_in_pfstock():
    """Twee tabellen die over dezelfde varianten gaan, moeten ze zo noemen."""
    assert pfhelp.VARIANT_STOCK == "meshcore_filter"
    assert pfhelp.VARIANT_MESHMANAGER == "meshmanager"
    for naam in pfhelp.VARIANTS:
        # pfstock.capabilities kent deze namen; een lege 'naam' betekent dat het
        # de lege set was, en dan verwijzen de twee modules naar iets anders.
        assert pfstock.capabilities(naam)["naam"]


# --- de huidige waarde uit de blob --------------------------------------------

# Zoals ``mqtt_ingest._handle_filter`` hem wegschrijft, ingekort.
BLOB_EIGEN = {
    "on": True,
    "hash": 2,
    "malformed": True,
    "channels": 3,
    "blocked_types": 1,
    "passed": 4000,
    "exempt": 12,
    "drop": {"hash": 535, "hops": 40},
    "stats": {"rate": {"ADVERT": {"seen": 4000, "cap": 12, "peak": 9, "lim": 8}}},
}

# Zoals ``pfstock.parse_filter_count`` hem oplevert.
BLOB_STOCK = {
    "on": True,
    "variant": "meshcore_filter",
    "drop": {"hops": 12, "hash": 535},
    "limits": {"ADVERT": {"hops": 3, "rate": 20}},
}


def test_huidige_waarde_uit_de_eigen_blob():
    hash_nu = pfhelp.current_value(BLOB_EIGEN, "filter_drop_hash",
                                   pfhelp.VARIANT_MESHMANAGER)
    assert hash_nu["bekend"] and hash_nu["waarde"] == 2
    assert hash_nu["pad"] == "hash"

    aan = pfhelp.current_value(BLOB_EIGEN, "filter_on", pfhelp.VARIANT_MESHMANAGER)
    assert aan["bekend"] and aan["waarde"] is True

    tempo = pfhelp.current_value(BLOB_EIGEN, "filter_drop_rate",
                                 pfhelp.VARIANT_MESHMANAGER)
    assert tempo["bekend"] and tempo["waarde"] == {"ADVERT": 8}


def test_huidige_waarde_uit_de_stock_blob():
    hops = pfhelp.current_value(BLOB_STOCK, "filter_drop_hops",
                                pfhelp.VARIANT_STOCK)
    assert hops["bekend"] and hops["waarde"] == {"ADVERT": 3}

    tempo = pfhelp.current_value(BLOB_STOCK, "filter_drop_rate",
                                 pfhelp.VARIANT_STOCK)
    assert tempo["bekend"] and tempo["waarde"] == {"ADVERT": 20}


def test_wat_de_blob_niet_draagt_wordt_geen_nul():
    """Ontbrekend is niet nul -- dezelfde regel als in pfstock.

    De ingestelde minimale padhash staat NIET in het antwoord van `filter
    count`; daar staat alleen de dropteller. Een 1 verzinnen zou beweren dat de
    stock-repeater alles doorlaat.
    """
    hash_nu = pfhelp.current_value(BLOB_STOCK, "filter_drop_hash",
                                   pfhelp.VARIANT_STOCK)
    assert hash_nu["bekend"] is False
    assert hash_nu["waarde"] is None
    assert "filter count" in hash_nu["waarom"]


def test_geen_filterstand_is_iets_anders_dan_geen_instelling():
    zonder = pfhelp.current_value(None, "filter_drop_hash",
                                  pfhelp.VARIANT_MESHMANAGER)
    assert zonder["bekend"] is False
    assert "geen filterstand" in zonder["waarom"]

    nog_niets = pfhelp.current_value({"on": True}, "filter_drop_hash",
                                     pfhelp.VARIANT_MESHMANAGER)
    assert nog_niets["bekend"] is False
    assert "nog niet gemeld" in nog_niets["waarom"]


def test_de_tabel_is_niet_te_vergiftigen():
    """Een aanroeper die in het antwoord schrijft, mag de tabel niet raken."""
    eerst = pfhelp.help_for("filter_drop_hash", pfhelp.VARIANT_STOCK)
    eerst["instelling"] = "GEWIJZIGD"
    opnieuw = pfhelp.help_for("filter_drop_hash", pfhelp.VARIANT_STOCK)
    assert opnieuw["instelling"] != "GEWIJZIGD"


def test_typenummers_kloppen_met_de_firmware():
    """De nummering hoort bij de uitleg: `filter hops 05 3` is anders onleesbaar."""
    assert pfhelp.TYPE_NUMBERS["00"] == "REQ"
    assert pfhelp.TYPE_NUMBERS["05"] == "GRP_TXT"
    assert pfhelp.TYPE_NUMBERS["11"] == "CONTROL"
    assert len(pfhelp.TYPE_NUMBERS) == len(pfstock.TYPE_NAMES)
