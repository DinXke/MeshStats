"""De reden bij een uitgeschakelde knop 'status opvragen' moet WAAR zijn.

De pagina zei "er is op dit ogenblik geen weg naar deze repeater". Bij JessaZH
was dat onwaar: zijn instellingen zijn prima op te vragen (de MeshUptime-node
doet dat over LoRa), alleen een statusbericht niet -- dat gaat over een ander
protocol dat de poller nog niet kent.

Een reden die niet klopt is erger dan een knop die uitstaat, want hij stuurt de
lezer de verkeerde kant op: naar de netwerkkabel in plaats van naar de knop
ernaast die het wel doet. Vandaar dat deze tests niet de formulering vastleggen
maar de EIS: als de knop uitstaat is er een reden, en die reden ontkent niet wat
er wél kan.
"""
import pytest

from app import commanding


def rep(**over):
    row = {"id": 7, "name": "BE-HSS-JessaZH", "pubkey_prefix": "e3d3f4d7edd0",
           "source_prefix": "48d7aade232b", "fw_meshmanager": "",
           "fw": "v1.17.1-PS+filter+rollback", "source_seen": None}
    row.update(over)
    return row


def route(**over):
    kw = {"broker_connected": True, "poller_seen": None, "poller_name": None,
          "poller_caps": None}
    rij = over.pop("rep_row", rep())
    kw.update(over)
    return commanding.route_for(rij, **kw)


VERS = "2099-01-01T00:00:00Z"      # een tijdstip dat nooit verjaart


def _kan_status(r):
    return ("status" in r["commands"] and r["mqtt"]) or r["poller_refresh"]


def test_de_poller_die_alleen_instellingen_doet_krijgt_de_echte_reden():
    r = route(poller_seen=VERS, poller_name="node-push-token", poller_caps=["settings"])
    assert _kan_status(r) is False
    reden = r["refresh_why"]
    assert reden, "een uitgeschakelde knop zonder reden is de fout zelf"
    # Hij noemt de poller, zegt wat er wél kan, en beweert niet dat er geen weg is.
    assert "node-push-token" in reden
    assert "Instellingen" in reden
    assert "geen weg" not in reden


def test_zonder_poller_mag_er_wel_staan_dat_er_geen_weg_is():
    r = route()
    assert _kan_status(r) is False
    assert "poller" in r["refresh_why"]


def test_een_poller_die_het_kan_geeft_geen_reden():
    """Leeg betekent: de knop staat aan. Een reden bij een werkende knop zou op
    het scherm terechtkomen als uitleg waarom iets niet kan."""
    r = route(poller_seen=VERS, poller_name="home-assistant",
              poller_caps=["settings", "refresh"])
    assert _kan_status(r) is True
    assert r["refresh_why"] == ""


def test_een_node_die_zichzelf_publiceert_geeft_geen_reden():
    eigen = rep(source_prefix="e3d3f4d7edd0", fw_meshmanager="2.10.0",
                pubkey_prefix="e3d3f4d7edd0")
    r = route(rep_row=eigen)
    assert _kan_status(r) is True
    assert r["refresh_why"] == ""


def test_een_doorgestuurde_node_op_de_mqtt_weg_zegt_wat_de_monitor_wel_kan():
    """Via een monitor kan 'settings' wel en 'status' niet. Dat verschil is de
    hele reden dat route["commands"] bestaat, en het hoort in de zin te staan."""
    doorgestuurd = rep(fw_meshmanager="")
    r = route(rep_row=doorgestuurd,
              relay={"pubkey_prefix": "48d7aade232b", "fw_meshmanager": "2.10.0",
                     "name": "MeshUptime"})
    if r["mqtt"] and r["via_monitor"]:
        assert "INSTELLINGEN" in r["refresh_why"]
        assert _kan_status(r) is False


@pytest.mark.parametrize("caps", [[], ["settings"], ["refresh"], ["settings", "refresh"], None])
def test_er_is_altijd_of_een_werkende_knop_of_een_reden(caps):
    """De eis in één regel, over elke combinatie."""
    for poller in (None, VERS):
        r = route(poller_seen=poller, poller_name="p", poller_caps=caps)
        assert _kan_status(r) or r["refresh_why"], (caps, poller)
