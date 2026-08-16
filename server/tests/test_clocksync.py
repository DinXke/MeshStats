"""Tests voor de klok die de site naar het mesh stuurt.

Wat hier vastligt is bijna allemaal een WEIGERING, en dat is geen toeval. De
correctie gaat één kant op: de firmware zet een klok alleen vooruit, omdat een
node die zijn klok terugzet zijn eigen adverts ongeldig maakt voor iedereen die
hem al kent. Een fout die hier vertrekt is aan de overkant dus niet meer terug
te draaien zonder er met een kabel bij te gaan staan -- en die overkant is een
dak.

Dus: elke controle die tussen "de server denkt iets" en "er vertrekt een
bericht" staat, hoort een test te hebben die bewijst dat ze werkelijk tegenhoudt
in plaats van alleen te loggen.
"""
import time

import pytest

from app import clocksync, mqtt_ingest


# --- de klokcontrole zelf -----------------------------------------------------

def _kernel(ok=True, synchronised=True, max_error=1.0, detail="ok"):
    return {"available": True, "synchronised": synchronised,
            "max_error_s": max_error, "detail": detail, "ok": ok}


@pytest.fixture(autouse=True)
def _fresh_reference(monkeypatch):
    """Het referentiepaar is procesbreed; zonder dit lekt de ene test in de andere."""
    monkeypatch.setattr(clocksync, "_ref_wall", None)
    monkeypatch.setattr(clocksync, "_ref_mono", None)


def test_zonder_adjtimex_wordt_er_niets_beweerd(monkeypatch):
    # Geen antwoord van de kernel is hier geen "waarschijnlijk wel goed". Een
    # ontwikkelmachine zonder adjtimex hoort niet stilzwijgend het hele mesh te
    # mogen ijken.
    monkeypatch.setattr(clocksync, "kernel_clock",
                        lambda: {"available": False, "synchronised": False,
                                 "max_error_s": None, "ok": False,
                                 "detail": "adjtimex niet beschikbaar"})
    out = clocksync.check_clock(now=1_800_000_000.0)
    assert out["ok"] is False
    assert "adjtimex" in out["reason"]


def test_een_ongesynchroniseerde_kernel_houdt_alles_tegen(monkeypatch):
    monkeypatch.setattr(clocksync, "kernel_clock",
                        lambda: _kernel(ok=False, synchronised=False,
                                        detail="de kernel meldt zijn klok als NIET gesynchroniseerd"))
    out = clocksync.check_clock(now=1_800_000_000.0)
    assert out["ok"] is False
    assert "NIET gesynchroniseerd" in out["reason"]


def test_een_sprong_van_de_wandklok_wordt_betrapt(monkeypatch):
    # De kernel is tevreden, maar de wandklok is met een uur verschoven terwijl
    # de monotone klok een seconde verder is. Dan is de tijd gezet en niet
    # verlopen, en is de vraag welke van de twee kanten juist was -- een vraag
    # die deze code niet kan beantwoorden, dus vertrekt er niets.
    monkeypatch.setattr(clocksync, "kernel_clock", lambda: _kernel())
    mono = [1000.0]
    monkeypatch.setattr(clocksync.time, "monotonic", lambda: mono[0])

    assert clocksync.check_clock(now=1_800_000_000.0)["ok"] is True
    mono[0] = 1001.0
    out = clocksync.check_clock(now=1_800_003_600.0)
    assert out["ok"] is False
    assert "monotone" in out["reason"]


def test_na_een_afgekeurde_sprong_gaat_het_de_ronde_erna_weer_door(monkeypatch):
    # Zonder dit zou één correctie de feature voorgoed uitzetten: het
    # referentiepaar moet ook meeschuiven als de ronde afgekeurd wordt.
    monkeypatch.setattr(clocksync, "kernel_clock", lambda: _kernel())
    mono = [1000.0]
    monkeypatch.setattr(clocksync.time, "monotonic", lambda: mono[0])

    clocksync.check_clock(now=1_800_000_000.0)
    mono[0] = 1001.0
    assert clocksync.check_clock(now=1_800_003_600.0)["ok"] is False
    mono[0] = 1002.0
    assert clocksync.check_clock(now=1_800_003_601.0)["ok"] is True


def test_een_klok_die_ver_achteruit_sprong_wordt_geweigerd(monkeypatch, tmp_path):
    # Overleeft een herstart, in tegenstelling tot de monotone controle: een
    # host die opstart zonder netwerk zet zijn klok op een RTC-waarde of op de
    # bouwdatum, en adjtimex kan daar op dat moment best tevreden over zijn.
    monkeypatch.setattr(clocksync, "kernel_clock", lambda: _kernel())
    seen = {}
    monkeypatch.setattr(clocksync.db, "get_setting", lambda k, d=None: seen.get(k, d))
    monkeypatch.setattr(clocksync.db, "set_setting", lambda k, v: seen.__setitem__(k, v))

    assert clocksync.check_clock(now=1_800_000_000.0)["ok"] is True

    # De herstart zelf: de monotone referentie is weg, dus de controle
    # hierboven zegt deze ronde niets. Precies het gat dat deze test bewaakt.
    monkeypatch.setattr(clocksync, "_ref_wall", None)
    monkeypatch.setattr(clocksync, "_ref_mono", None)

    out = clocksync.check_clock(now=1_800_000_000.0 - 86_400)
    assert out["ok"] is False
    assert "vroeger" in out["reason"]


# --- wie er een bericht krijgt ------------------------------------------------

def _rep(**kw):
    row = {"pubkey_prefix": "e3d3f4d7edd0", "name": "Node", "source_prefix": "e3d3f4d7edd0",
           "source_seen": "2026-08-16T12:00:00Z", "fw_meshstats": "1.10.0"}
    row.update(kw)
    return row


NOW = clocksync.datetime(2026, 8, 16, 12, 5, tzinfo=clocksync.timezone.utc)


def test_een_node_met_de_juiste_firmware_komt_in_aanmerking():
    (t,) = clocksync.targets([_rep()], now=NOW)
    assert t["ok"] is True


def test_te_oude_firmware_krijgt_niets():
    # Een 1.9.1-node kent het topic wel maar weigert het woord en telt het als
    # geweigerd -- een teller op een dak die niemand leest. Hier stranden.
    (t,) = clocksync.targets([_rep(fw_meshstats="1.9.1")], now=NOW)
    assert t["ok"] is False
    assert "1.10.0" in t["why"]


def test_versies_worden_op_getal_vergeleken_en_niet_op_tekst():
    # "1.10.0" komt alfabetisch vóór "1.9.1"; net de firmware die het wél kan.
    (t,) = clocksync.targets([_rep(fw_meshstats="1.10.1")], now=NOW)
    assert t["ok"] is True


def test_een_doorgestuurde_repeater_krijgt_hier_niets():
    # Zijn tijd komt van zijn monitor, over LoRa. Publiceren op zijn cmd-topic
    # zou publiceren zijn op een topic waar niemand naar luistert.
    (t,) = clocksync.targets([_rep(source_prefix="55d9a320a4e3")], now=NOW)
    assert t["ok"] is False
    assert "monitor" in t["why"]


def test_een_node_waar_al_een_dag_niets_van_kwam_krijgt_niets():
    (t,) = clocksync.targets([_rep(source_seen="2026-08-14T12:00:00Z")], now=NOW)
    assert t["ok"] is False


def test_een_repeater_via_de_http_api_krijgt_niets():
    (t,) = clocksync.targets([_rep(source_prefix="api")], now=NOW)
    assert t["ok"] is False


# --- de knop: welke node krijgt het bericht ----------------------------------

def test_de_knop_op_een_doorgestuurde_repeater_mikt_op_zijn_monitor(monkeypatch):
    # De dakrepeater publiceert zelf niets. Zijn klok komt van de node die hem
    # monitort, en dus gaat het bericht daarheen -- niet naar een cmd-topic waar
    # niemand naar luistert.
    monitor = _rep(pubkey_prefix="55d9a320a4e3", fw_meshstats="1.11.0")
    monkeypatch.setattr(clocksync.db, "find_repeater", lambda p: monitor)
    r = clocksync.time_route(_rep(source_prefix="55d9a320a4e3"), now=NOW)
    assert r["ok"] is True
    assert r["node"] == "55d9a320a4e3"
    assert r["via_monitor"] is True


def test_bij_een_monitor_telt_de_firmware_van_de_monitor(monkeypatch):
    # Niet die van het onderwerp: een node die zelf niet publiceert meldt nergens
    # een versie, dus daarop gokken kost een opdracht die stil geweigerd wordt.
    monkeypatch.setattr(clocksync.db, "find_repeater",
                        lambda p: _rep(pubkey_prefix="55d9a320a4e3", fw_meshstats="1.9.1"))
    r = clocksync.time_route(_rep(source_prefix="55d9a320a4e3", fw_meshstats="1.11.0"),
                             now=NOW)
    assert r["ok"] is False
    assert r["blocker"] == "old_fw"


def test_de_dagelijkse_ronde_stuurt_niet_twee_keer_naar_dezelfde_monitor():
    # Twee doorgestuurde repeaters achter een monitor. De ronde loopt over alle
    # repeaters, dus zonder deze regel kreeg die monitor hetzelfde bericht twee
    # keer, en deed hij twee klokrondes over de lucht voor de prijs van een. De
    # knop mag de monitorweg wel gebruiken; dat is een handeling van een mens.
    rows = [_rep(pubkey_prefix="aaaaaaaaaaaa", source_prefix="55d9a320a4e3"),
            _rep(pubkey_prefix="bbbbbbbbbbbb", source_prefix="55d9a320a4e3"),
            _rep(pubkey_prefix="55d9a320a4e3", source_prefix="55d9a320a4e3")]
    got = [t for t in clocksync.targets(rows, now=NOW) if t["ok"]]
    assert [t["node"] for t in got] == ["55d9a320a4e3"]


# --- de knop: dezelfde controles als de automatische ronde -------------------

@pytest.fixture
def knop(monkeypatch):
    """Een open weg en een verbonden broker; legt vast wat er vertrok."""
    sent = []
    monkeypatch.setattr(clocksync, "ENABLED", True)
    monkeypatch.setattr(clocksync, "time_route",
                        lambda rep, **kw: {"id": 1, "prefix": "e3", "name": "Dak",
                                           "node": "55d9a320a4e3", "via_monitor": True,
                                           "ok": True, "blocker": "", "why": "",
                                           "fw_meshstats": "1.11.0"})
    monkeypatch.setattr(mqtt_ingest, "can_publish", lambda: True)
    monkeypatch.setattr(mqtt_ingest, "publish_command",
                        lambda node, cmd, subject=None, epoch=None:
                        sent.append((node, cmd, epoch)) or True)
    store = {}
    monkeypatch.setattr(clocksync.db, "get_setting", lambda k, d=None: store.get(k, d))
    monkeypatch.setattr(clocksync.db, "set_setting", lambda k, v: store.__setitem__(k, v))
    monkeypatch.setattr(clocksync, "_rebooted_since", lambda n, s, w: False)
    return sent


def test_de_knop_weigert_wanneer_de_serverklok_niet_vaststaat(monkeypatch, knop):
    # Het punt van deze test: een handmatige start mag geen achterdeur om de
    # klokcontrole heen zijn. Een verkeerde tijd is op een node niet meer terug
    # te draaien, en dat verandert niet doordat er iemand op een knop drukte in
    # plaats van dat een timer afliep.
    monkeypatch.setattr(clocksync, "check_clock",
                        lambda now=None: {"ok": False, "reason": "kapotte klok",
                                          "kernel": {}, "epoch": 0})
    out = clocksync.sync_now(_rep(), now=1_800_000_000.0)
    assert out["outcome"] == "no_clock"
    assert out["reason"] == "kapotte klok"
    assert knop == []


def test_de_knop_stuurt_time_met_een_epoch_naar_de_juiste_node(monkeypatch, knop):
    monkeypatch.setattr(clocksync, "check_clock",
                        lambda now=None: {"ok": True, "reason": "", "kernel": {}, "epoch": 0})
    out = clocksync.sync_now(_rep(), now=1_800_000_000.0)
    assert out["outcome"] == "sent"
    assert knop[0][0] == "55d9a320a4e3"
    assert knop[0][1] == "time"
    assert knop[0][2] > clocksync.mqtt_ingest.MIN_EPOCH


def test_twee_keer_klikken_stuurt_niet_twee_keer(monkeypatch, knop):
    # De firmware zou de tweede ronde toch weigeren (hoogstens een per uur), dus
    # publiceren zou hier "verstuurd" melden voor een ronde die niet gebeurt.
    # Dat is de valse geslaagdheid die deze knop niet mag hebben.
    monkeypatch.setattr(clocksync, "check_clock",
                        lambda now=None: {"ok": True, "reason": "", "kernel": {}, "epoch": 0})
    assert clocksync.sync_now(_rep(), now=1_800_000_000.0)["outcome"] == "sent"
    out = clocksync.sync_now(_rep(), now=1_800_000_060.0)
    assert out["outcome"] == "too_soon"
    assert 55 <= out["wait_min"] <= 60
    assert len(knop) == 1


def test_na_het_uur_mag_het_weer(monkeypatch, knop):
    monkeypatch.setattr(clocksync, "check_clock",
                        lambda now=None: {"ok": True, "reason": "", "kernel": {}, "epoch": 0})
    clocksync.sync_now(_rep(), now=1_800_000_000.0)
    out = clocksync.sync_now(_rep(), now=1_800_000_000.0 + clocksync.MANUAL_MIN_GAP_S + 1)
    assert out["outcome"] == "sent"
    assert len(knop) == 2


def test_een_node_die_intussen_herstartte_hoeft_niet_te_wachten(monkeypatch, knop):
    # De uitzondering die ertoe doet. Een node die zojuist herstartte staat op
    # de datum uit zijn firmware -- precies de toestand waarvoor dit bestaat --
    # en dan is "wacht nog veertig minuten" het slechtste antwoord dat een knop
    # kan geven.
    monkeypatch.setattr(clocksync, "check_clock",
                        lambda now=None: {"ok": True, "reason": "", "kernel": {}, "epoch": 0})
    clocksync.sync_now(_rep(), now=1_800_000_000.0)
    monkeypatch.setattr(clocksync, "_rebooted_since", lambda n, s, w: True)
    out = clocksync.sync_now(_rep(), now=1_800_000_060.0)
    assert out["outcome"] == "sent"
    assert len(knop) == 2


def test_een_mislukte_publicatie_blokkeert_het_uur_niet(monkeypatch, knop):
    # Anders zou een weggevallen brokerverbinding de knop een uur lang laten
    # beweren dat er net gesynchroniseerd is.
    monkeypatch.setattr(clocksync, "check_clock",
                        lambda now=None: {"ok": True, "reason": "", "kernel": {}, "epoch": 0})
    monkeypatch.setattr(mqtt_ingest, "publish_command",
                        lambda node, cmd, subject=None, epoch=None: False)
    assert clocksync.sync_now(_rep(), now=1_800_000_000.0)["outcome"] == "failed"
    monkeypatch.setattr(mqtt_ingest, "publish_command",
                        lambda node, cmd, subject=None, epoch=None:
                        knop.append((node, cmd, epoch)) or True)
    assert clocksync.sync_now(_rep(), now=1_800_000_010.0)["outcome"] == "sent"


def test_zonder_brokerverbinding_meldt_de_knop_dat(monkeypatch, knop):
    monkeypatch.setattr(clocksync, "check_clock",
                        lambda now=None: {"ok": True, "reason": "", "kernel": {}, "epoch": 0})
    monkeypatch.setattr(mqtt_ingest, "can_publish", lambda: False)
    out = clocksync.sync_now(_rep(), now=1_800_000_000.0)
    assert out["outcome"] == "no_route"
    assert out["blocker"] == "broker_down"
    assert knop == []


def test_een_dichte_weg_publiceert_niet(monkeypatch, knop):
    monkeypatch.setattr(clocksync, "time_route",
                        lambda rep, **kw: {"id": 1, "prefix": "e3", "name": "Dak",
                                           "node": None, "via_monitor": False, "ok": False,
                                           "blocker": "old_fw", "why": "te oude firmware",
                                           "fw_meshstats": "1.9.1"})
    out = clocksync.sync_now(_rep(), now=1_800_000_000.0)
    assert out["outcome"] == "no_route"
    assert out["blocker"] == "old_fw"
    assert knop == []


# --- de ronde -----------------------------------------------------------------

@pytest.fixture
def geen_publicatie(monkeypatch):
    """Legt vast of er iets vertrok, zonder een broker in de buurt."""
    sent = []
    monkeypatch.setattr(mqtt_ingest, "can_publish", lambda: True)
    monkeypatch.setattr(mqtt_ingest, "publish_command",
                        lambda node, cmd, subject=None, epoch=None:
                        sent.append((node, cmd, epoch)) or True)
    return sent


def test_een_onbetrouwbare_klok_publiceert_niets(monkeypatch, geen_publicatie):
    # De kern van deze module. Niet loggen-en-toch-doen: niets.
    monkeypatch.setattr(clocksync, "check_clock",
                        lambda now=None: {"ok": False, "reason": "kapotte klok",
                                          "kernel": {}, "epoch": 0})
    monkeypatch.setattr(clocksync, "targets", lambda *a, **k: [
        {"prefix": "e3", "name": "n", "node": "e3d3f4d7edd0", "ok": True, "why": ""}])
    out = clocksync.run_once(now=1_800_000_000.0)
    assert geen_publicatie == []
    assert out["last_result"] == "geweigerd"
    assert out["last_reason"] == "kapotte klok"


def test_zonder_brokerverbinding_vertrekt_er_niets(monkeypatch, geen_publicatie):
    monkeypatch.setattr(clocksync, "check_clock",
                        lambda now=None: {"ok": True, "reason": "", "kernel": {}, "epoch": 0})
    monkeypatch.setattr(mqtt_ingest, "can_publish", lambda: False)
    out = clocksync.run_once(now=1_800_000_000.0)
    assert geen_publicatie == []
    assert out["last_result"] == "geen brokerverbinding"


def test_een_geslaagde_ronde_stuurt_time_met_een_epoch(monkeypatch, geen_publicatie):
    monkeypatch.setattr(clocksync, "check_clock",
                        lambda now=None: {"ok": True, "reason": "", "kernel": {}, "epoch": 0})
    monkeypatch.setattr(clocksync, "targets", lambda *a, **k: [
        {"prefix": "e3", "name": "n", "node": "e3d3f4d7edd0", "ok": True, "why": ""},
        {"prefix": "55", "name": "m", "node": "55d9a320a4e3", "ok": False, "why": "oud"}])
    out = clocksync.run_once(now=1_800_000_000.0)
    assert [n for n, _, _ in geen_publicatie] == ["e3d3f4d7edd0"]
    assert geen_publicatie[0][1] == "time"
    assert geen_publicatie[0][2] > clocksync.mqtt_ingest.MIN_EPOCH
    assert out["published"] == 1
    assert out["last_ok"]
