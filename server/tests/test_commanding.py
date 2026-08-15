"""Tests voor de vraag "kan deze knop iets doen?".

Waarom dit een eigen bestand verdient: het antwoord komt uit vier losse bronnen
(wie publiceert er voor deze repeater, welke firmware draait die, hangt de
broker eraan, en heeft er recent een poller gepold), en fout antwoorden kost
geen foutmelding maar een pagina die iets belooft wat niemand gaat doen. Dat is
precies wat er maandenlang gebeurde: Home Assistant was uit de keten, de
wachtrij liep vol, en de site bleef melden "Opvraging gestart".
"""
from datetime import datetime, timedelta, timezone

import pytest

from app import commanding

NU = datetime(2026, 8, 15, 12, 0, 0, tzinfo=timezone.utc)


def stamp(minutes_ago: float) -> str:
    return (NU - timedelta(minutes=minutes_ago)).strftime("%Y-%m-%dT%H:%M:%SZ")


def rep(**overrides) -> dict:
    """Een repeater die zichzelf publiceert met firmware die opdrachten kent."""
    base = {
        "pubkey_prefix": "e3d3f4d7edd0",
        "source_prefix": "e3d3f4d7edd0",
        "source_seen": stamp(2),
        "fw_meshstats": "1.8.0",
        "fw": "v1.16.0",
    }
    base.update(overrides)
    return base


def monitor(**overrides) -> dict:
    """De node die een andere repeater uitleest en zijn cijfers doorstuurt."""
    base = {
        "pubkey_prefix": "55d9a320a4e3",
        "source_prefix": "55d9a320a4e3",
        "source_seen": stamp(1),
        "fw_meshstats": "1.9.0",
        "fw": "v1.16.0",
    }
    base.update(overrides)
    return base


def route(**kwargs):
    kwargs.setdefault("broker_connected", True)
    kwargs.setdefault("now", NU)
    return commanding.route_for(kwargs.pop("rep_row", rep()), **kwargs)


def test_node_die_zichzelf_publiceert_is_rechtstreeks_bereikbaar():
    r = route()
    assert r["mqtt"] is True
    assert r["blocker"] == ""
    assert r["node"] == "e3d3f4d7edd0"


def test_oudere_firmware_krijgt_geen_opdracht():
    # Firmware onder 1.8.0 schrijft zich niet in op het cmd-topic. De broker
    # aanvaardt het bericht en gooit het weg: publish() slaagt, er gebeurt
    # niets, en niemand ziet het. Precies het soort stilte dat de knop vroeger
    # verborg, dus hier wordt er niet eens gepubliceerd.
    r = route(rep_row=rep(fw_meshstats="1.7.2"))
    assert r["mqtt"] is False
    assert r["blocker"] == "old_fw"


def test_versievergelijking_is_numeriek_niet_alfabetisch():
    # "1.10.0" < "1.8.0" als string, en dat zou net de firmware buitensluiten
    # die het wél kan.
    assert commanding.parse_version("1.10.0") > commanding.MIN_CMD_VERSION
    assert route(rep_row=rep(fw_meshstats="1.10.0"))["mqtt"] is True


def test_zonder_gemelde_versie_wordt_er_niet_gegokt():
    r = route(rep_row=rep(fw_meshstats=None))
    assert r["mqtt"] is False
    assert r["blocker"] == "no_fw"


def test_doorgestuurde_repeater_zonder_bekende_monitor_krijgt_geen_opdracht():
    # De cijfers komen van een node die deze repeater monitort, maar die node
    # is hier zelf geen bekende repeater. Van zijn firmware weten we dus niets,
    # en een opdracht sturen is gokken.
    r = route(rep_row=rep(source_prefix="55d9a320a4e3"))
    assert r["mqtt"] is False
    assert r["blocker"] == "relay_unknown"
    assert r["via_monitor"] is True


def test_doorgestuurde_repeater_gaat_langs_zijn_monitor():
    # Waar 1.9.0 voor bestaat: de monitor logt al in bij deze repeater en pollt
    # hem al, dus kan hij ook gevraagd worden zijn CLI uit te lezen. De opdracht
    # gaat naar de monitor, met de sleutel van het onderwerp erbij.
    r = route(rep_row=rep(source_prefix="55d9a320a4e3"), relay=monitor())
    assert r["mqtt"] is True
    assert r["blocker"] == ""
    assert r["via_monitor"] is True
    assert r["node"] == "55d9a320a4e3"
    assert r["subject"] == "e3d3f4d7edd0"


def test_monitor_moet_de_sweep_kennen():
    # 1.8.0 kent het cmd-topic wel, maar weigert het argument. Dat is geen
    # "misschien": zo'n node telt de opdracht als geweigerd en zwijgt verder,
    # wat op de pagina niet van een onbereikbare node te onderscheiden is.
    r = route(rep_row=rep(source_prefix="55d9a320a4e3"), relay=monitor(fw_meshstats="1.8.0"))
    assert r["mqtt"] is False
    assert r["blocker"] == "old_fw"
    # De versie die de pagina toont, is die van de node die de opdracht krijgt.
    assert r["fw_meshstats"] == "1.8.0"
    assert r["min_fw"] == "1.9.0"


def test_monitor_zonder_gemelde_versie_krijgt_niets():
    r = route(rep_row=rep(source_prefix="55d9a320a4e3"), relay=monitor(fw_meshstats=None))
    assert r["mqtt"] is False
    assert r["blocker"] == "no_fw"


def test_langs_een_monitor_kan_alleen_settings():
    # Een statusbericht namens een ander sturen kan een monitor niet, en het
    # hoeft ook niet: die cijfers stuurt hij uit zichzelf al door.
    assert route(rep_row=rep(source_prefix="55d9a320a4e3"),
                 relay=monitor())["commands"] == ("settings",)
    assert route()["commands"] == ("settings", "status")


def test_eigen_node_houdt_zijn_eigen_versiegrens():
    # De strengere grens geldt alleen voor de weg langs een monitor. Een node
    # die zichzelf uitleest heeft aan 1.8.0 genoeg, en die mag hier niet
    # meeschuiven omdat er een tweede weg bij gekomen is.
    r = route()
    assert r["mqtt"] is True
    assert r["via_monitor"] is False
    assert r["min_fw"] == "1.8.0"


def test_kortere_sleutel_van_dezelfde_node_telt_als_zichzelf():
    # Bronnen sturen verschillende sleutellengtes; op string-gelijkheid testen
    # zou een node die zichzelf publiceert aanzien voor een doorgeefluik.
    r = route(rep_row=rep(source_prefix="e3d3f4d7ed"))
    assert r["mqtt"] is True


def test_te_korte_sleutel_telt_niet_als_dezelfde_node():
    assert commanding.same_key("e3d3f4", "e3d3f4d7edd0") is False


def test_http_bron_heeft_geen_mqtt_weg():
    r = route(rep_row=rep(source_prefix="api"))
    assert r["mqtt"] is False
    assert r["blocker"] == "http_source"
    assert r["node"] is None


def test_broker_weg_blokkeert_maar_overschaduwt_niets():
    # Een wegvallende broker is tijdelijk; te oude firmware niet. Wie beide
    # heeft, hoort de blijvende reden te lezen, want die vraagt om actie.
    assert route(broker_connected=False)["blocker"] == "broker_down"
    assert route(rep_row=rep(fw_meshstats="1.7.2"),
                 broker_connected=False)["blocker"] == "old_fw"


def test_poller_telt_alleen_als_hij_recent_gepold_heeft():
    assert route(poller_seen=stamp(1))["ha"] is True
    assert route(poller_seen=stamp(60))["ha"] is False
    assert route(poller_seen=None)["ha"] is False


def test_node_die_lang_zweeg_wordt_gemeld_maar_niet_geweigerd():
    # Een opdracht wordt nergens bewaard voor een node die offline is, maar
    # "waarschijnlijk niet aangekomen" is iets anders dan "kan niet". De knop
    # blijft werken, de pagina waarschuwt.
    r = route(rep_row=rep(source_seen=stamp(180)))
    assert r["mqtt"] is True
    assert r["node_stale"] is True
    assert route()["node_stale"] is False


def test_onleesbare_tijdstempel_maakt_niets_stuk():
    r = route(rep_row=rep(source_seen="ooit"), poller_seen="gisteren")
    assert r["node_stale"] is True
    assert r["ha"] is False


def test_parse_version_op_rommel():
    assert commanding.parse_version("") is None
    assert commanding.parse_version(None) is None
    assert commanding.parse_version("onbekend") is None
    assert commanding.parse_version("v1.8") == (1, 8)


@pytest.mark.parametrize("a,b,verwacht", [
    ("e3d3f4d7edd0", "e3d3f4d7edd0", True),
    ("e3d3f4d7ed", "e3d3f4d7edd0", True),
    ("e3d3f4d7edd0", "55d9a320a4e3", False),
    ("", "e3d3f4d7edd0", False),
    (None, None, False),
])
def test_same_key(a, b, verwacht):
    assert commanding.same_key(a, b) is verwacht
