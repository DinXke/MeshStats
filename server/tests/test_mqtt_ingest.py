"""Tests voor wat er gebeurt met een bericht dat niet te lezen valt.

De aanleiding staat in de 1.9.1-noot van MeshStatsNet.cpp: een nodenaam met een
aanhalingsteken erin maakte de payload ongeldige JSON, waarna dit bestand hem
weggooide. De node verdween daarmee uit de statistieken terwijl aan de
firmwarekant elke teller "gepubliceerd" bleef melden -- de broker had de bytes
immers aangenomen.

Wat hier vastligt is dus niet het opvangen van de fout (dat gebeurde al), maar
dat de melding bruikbaar is: welk onderwerp, welke fout, en het stuk payload
waar het misging. Zonder dat laatste zegt een logregel "column 87" en moet je
met een sniffer op de broker gaan zitten om te weten wat daar stond.
"""
import logging

import pytest

from app import mqtt_ingest


@pytest.fixture(autouse=True)
def schone_tellers():
    """Elke test begint met nul fouten; de teller staat op moduleniveau."""
    before = dict(mqtt_ingest._state)
    mqtt_ingest._state["errors"] = 0
    mqtt_ingest._state["last_error"] = ""
    yield
    mqtt_ingest._state.update(before)


# De payload zoals een node die met een aanhalingsteken in zijn naam hem
# verstuurde: geldig van vorm, alleen eindigt de naam drie tekens te vroeg.
KAPOTTE_NAAM = b'{"repeater":{"pubkey_prefix":"e3d3f4d7edd0","name":"Bob"s node"},"metrics":{"online":true}}'


def test_onleesbaar_bericht_wordt_overgeslagen_en_geteld(caplog):
    with caplog.at_level(logging.WARNING, logger="meshstats.mqtt"):
        assert mqtt_ingest.handle_message("meshcore/e3d3f4d7edd0/stats", KAPOTTE_NAAM) is False
    assert mqtt_ingest._state["errors"] == 1
    assert "JSONDecodeError" in mqtt_ingest._state["last_error"]


def test_de_payload_staat_in_het_logboek(caplog):
    # Het hele punt: de naam die de fout veroorzaakte moet leesbaar zijn in de
    # melding, want de foutmelding van json noemt enkel een kolomnummer.
    with caplog.at_level(logging.WARNING, logger="meshstats.mqtt"):
        mqtt_ingest.handle_message("meshcore/e3d3f4d7edd0/stats", KAPOTTE_NAAM)
    (regel,) = caplog.messages
    assert 'Bob"s node' in regel
    assert "meshcore/e3d3f4d7edd0/stats" in regel


def test_ongeldige_utf8_blijft_zichtbaar_als_bytes(caplog):
    # De tweede manier waarop dit misgaat: een naam die halverwege een
    # UTF-8-teken afgekapt is. Met vraagtekens in de logregel zou dat niet van
    # het geval hierboven te onderscheiden zijn.
    payload = b'{"repeater":{"name":"caf\xc3"},"metrics":{}}'
    with caplog.at_level(logging.WARNING, logger="meshstats.mqtt"):
        assert mqtt_ingest.handle_message("meshcore/aabbccddeeff/stats", payload) is False
    (regel,) = caplog.messages
    assert "\\xc3" in regel


def test_lange_payload_wordt_afgekapt(caplog):
    # Een node die in een lus onzin publiceert mag geen logbestand volschrijven.
    payload = b'{"rommel":"' + b"x" * 5000 + b'"'
    with caplog.at_level(logging.WARNING, logger="meshstats.mqtt"):
        mqtt_ingest.handle_message("meshcore/aabbccddeeff/stats", payload)
    (regel,) = caplog.messages
    assert len(regel) < mqtt_ingest.MAX_LOG_EXCERPT + 300
    assert f"({len(payload)} bytes)" in regel


def test_payload_blijft_een_regel(caplog):
    # Tien van deze meldingen onder elkaar moeten nog als tien meldingen te
    # lezen zijn, niet als een berg fragmenten zonder herkenbaar begin.
    payload = b'{"a":\n"b"\n'
    with caplog.at_level(logging.WARNING, logger="meshstats.mqtt"):
        mqtt_ingest.handle_message("meshcore/aabbccddeeff/stats", payload)
    (regel,) = caplog.messages
    assert "\n" not in regel


def test_een_goed_bericht_logt_niets(caplog, monkeypatch):
    # De opslag zelf hoort hier niet thuis; wat telt is dat de gewone weg
    # zwijgt, anders verdrinkt de melding hierboven in het gewone verkeer.
    gezien = []
    monkeypatch.setattr(mqtt_ingest, "_handle_payload",
                        lambda topic, raw: gezien.append(topic))
    with caplog.at_level(logging.WARNING, logger="meshstats.mqtt"):
        assert mqtt_ingest.handle_message("meshcore/e3d3f4d7edd0/stats", b"{}") is True
    assert gezien == ["meshcore/e3d3f4d7edd0/stats"]
    assert caplog.messages == []
    assert mqtt_ingest._state["errors"] == 0


def test_een_pakketbericht_gaat_naar_de_pakketkant(caplog, monkeypatch):
    # Het onderscheid tussen /rx en /stats zat in de sluiting in _run() en werd
    # daarmee door geen enkele test geraakt.
    gezien = []
    monkeypatch.setattr(mqtt_ingest, "_handle_rx", lambda topic, raw: gezien.append(topic))
    assert mqtt_ingest.handle_message("meshcore/e3d3f4d7edd0/rx", b"{}") is True
    assert gezien == ["meshcore/e3d3f4d7edd0/rx"]
