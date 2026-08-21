"""Serverzijde SNMP-discovery (app/snmp.py).

Er gaat geen echt pakket het net op: de subprocess-grens (``_run``) en de twee
primitieven (``get``/``walk``) worden vervangen. Wat hier bewaakt wordt:

* de ``-OQn``-uitvoer wordt correct ontleed, en absente varbinds ("No Such
  Object") vallen weg;
* ``get``/``walk`` bouwen de juiste net-snmp-argumenten (v2c, community, doel);
* ``discover`` maakt van een apparaat een kieslijst waarvan elk item precies de
  vorm draagt die de node accepteert (basis-OID + snmparg + interpretatie:
  octet-tellers -> rate, status -> status, gauges -> numeric);
* ontbreekt net-snmp, dan is dat een nette melding en geen crash.
"""
import pytest

from app import snmp


def test_parse_leest_oqn_en_laat_absente_varbinds_weg():
    tekst = (".1.3.6.1.2.1.1.5.0 sw1\n"
             ".1.3.6.1.2.1.2.2.1.8.9 No Such Instance currently exists\n"
             ".1.3.6.1.2.1.1.1.0 My Switch, rev 2\n")
    rijen = snmp._parse(tekst)
    assert ("1.3.6.1.2.1.1.5.0", "sw1") in rijen
    assert ("1.3.6.1.2.1.1.1.0", "My Switch, rev 2") in rijen
    # De "No Such Instance"-regel is eruit.
    assert all("No Such" not in v for _, v in rijen)


def test_index_haalt_de_staart_na_de_basis():
    assert snmp._index("1.3.6.1.2.1.2.2.1.2.3", "1.3.6.1.2.1.2.2.1.2") == "3"
    assert snmp._index(".1.3.6.1.2.1.2.2.1.2.3", "1.3.6.1.2.1.2.2.1.2") == "3"
    assert snmp._index("1.3.6.1.2.1.1.5.0", "1.3.6.1.2.1.2.2.1.2") == ""


def test_get_bouwt_de_juiste_argumenten_en_parseert(monkeypatch):
    gezien = {}

    def nep(args, timeout):
        gezien["args"] = args
        return 0, ".1.3.6.1.2.1.1.5.0 sw1\n", ""

    monkeypatch.setattr(snmp, "_run", nep)
    uit = snmp.get("10.0.0.1", "publiek", ["1.3.6.1.2.1.1.5.0"], port=161)
    assert uit["ok"] and uit["values"]["1.3.6.1.2.1.1.5.0"] == "sw1"
    a = gezien["args"]
    assert a[0] == snmp.SNMPGET and "-OQn" in a and "-v2c" in a
    assert "publiek" in a and "10.0.0.1" in a


def test_get_met_afwijkende_poort_zet_host_dubbelepunt_poort(monkeypatch):
    gezien = {}
    monkeypatch.setattr(snmp, "_run", lambda args, t: gezien.update(args=args) or (0, "", ""))
    snmp.get("10.0.0.1", "c", ["1.3.6"], port=1610)
    assert "10.0.0.1:1610" in gezien["args"]


def test_walk_geeft_rijen_terug(monkeypatch):
    monkeypatch.setattr(snmp, "_run",
                        lambda args, t: (0, ".1.3.6.1.2.1.2.2.1.2.1 lo\n.1.3.6.1.2.1.2.2.1.2.2 eth0\n", ""))
    uit = snmp.walk("h", "c", snmp.IF_DESCR)
    assert uit["ok"] and len(uit["rows"]) == 2
    assert uit["rows"][1] == ("1.3.6.1.2.1.2.2.1.2.2", "eth0")


def test_discover_zonder_net_snmp_meldt_het_netjes(monkeypatch):
    monkeypatch.setattr(snmp, "available", lambda: False)
    uit = snmp.discover("10.0.0.1", "c")
    assert not uit["ok"] and "net-snmp" in uit["error"]


def _nep_discovery(monkeypatch):
    """Een apparaat met één bruikbare interface (eth0, idx 2) en een UPS."""
    monkeypatch.setattr(snmp, "available", lambda: True)

    def nep_get(host, community, oids, port=161, timeout=None):
        vals = {}
        if snmp.SYS["name"] in oids:
            vals = {snmp.SYS["name"]: "sw1", snmp.SYS["descr"]: "My Switch",
                    snmp.SYS["uptime"]: "12345", snmp.SYS["objectid"]: "1.3.6.1.4.1.9"}
        elif snmp.UPS_MODEL in oids:
            vals = {snmp.UPS_MODEL: "APC Smart-UPS",
                    snmp.UPS_BATT + ".0": "2", snmp.UPS_MIN + ".0": "42",
                    snmp.UPS_CHARGE + ".0": "95"}
        return {"ok": True, "error": "", "values": vals}

    walks = {
        snmp.IF_DESCR: [("1.3.6.1.2.1.2.2.1.2.1", "lo"),
                        ("1.3.6.1.2.1.2.2.1.2.2", "eth0")],
        snmp.IF_NAME: [("1.3.6.1.2.1.31.1.1.1.1.2", "eth0")],
        snmp.IF_OPER: [("1.3.6.1.2.1.2.2.1.8.1", "1"),
                       ("1.3.6.1.2.1.2.2.1.8.2", "2")],
        snmp.IF_HCIN: [("1.3.6.1.2.1.31.1.1.1.6.2", "123456789")],
        snmp.IF_HCOUT: [("1.3.6.1.2.1.31.1.1.1.10.2", "987654321")],
        snmp.UPS_LOAD: [("1.3.6.1.2.1.33.1.4.4.1.5.1", "30")],
        snmp.UPS_VIN: [("1.3.6.1.2.1.33.1.3.3.1.3.1", "230")],
        snmp.UPS_VOUT: [("1.3.6.1.2.1.33.1.4.4.1.2.1", "229")],
    }
    monkeypatch.setattr(snmp, "get", nep_get)
    monkeypatch.setattr(snmp, "walk",
                        lambda host, community, base, port=161, timeout=None:
                        {"ok": True, "error": "", "rows": walks.get(base, [])})


def test_discover_bouwt_interface_items_in_node_vorm(monkeypatch):
    _nep_discovery(monkeypatch)
    uit = snmp.discover("10.0.0.1", "publiek")
    assert uit["ok"] and uit["system"]["name"] == "sw1"
    items = {it["key"]: it for it in uit["items"]}
    # eth0 (idx 2) heeft in/uit-verkeer (rate, HC-octet-basis) en status.
    assert items["if_in_2"]["oid"] == snmp.IF_HCIN
    assert items["if_in_2"]["snmparg"] == "2" and items["if_in_2"]["interp"] == "rate"
    assert "sw1 eth0 in" == items["if_in_2"]["name"]
    assert items["if_oper_2"]["interp"] == "status" and items["if_oper_2"]["preview"] == "down"
    # lo (idx 1) heeft geen HC-tellers: alleen een statusitem, geen verkeer.
    assert "if_oper_1" in items and "if_in_1" not in items


def test_discover_neemt_de_ups_mee(monkeypatch):
    _nep_discovery(monkeypatch)
    uit = snmp.discover("10.0.0.1", "publiek")
    assert uit["ups"] and uit["ups"]["model"] == "APC Smart-UPS"
    items = {it["key"]: it for it in uit["items"]}
    assert items["ups_batt"]["interp"] == "status" and items["ups_batt"]["preview"] == "normaal"
    assert items["ups_min"]["oid"] == snmp.UPS_MIN and items["ups_min"]["snmparg"] == ""
    # Per-lijn belasting en spanningen (lijn 1).
    assert items["ups_load_1"]["snmparg"] == "1" and items["ups_load_1"]["oid"] == snmp.UPS_LOAD
    assert "ups_vin_1" in items and "ups_vout_1" in items


def test_discover_meldt_een_stil_apparaat(monkeypatch):
    monkeypatch.setattr(snmp, "available", lambda: True)
    monkeypatch.setattr(snmp, "get",
                        lambda *a, **k: {"ok": False, "error": "time-out", "values": {}})
    uit = snmp.discover("10.0.0.1", "c")
    assert not uit["ok"] and "time-out" in uit["error"]
