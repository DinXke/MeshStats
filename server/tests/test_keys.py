import pytest

from meshtrack import keys
from meshtrack import settings as setmod
from meshtrack.db import DB

# Testsleutelpaar uit MeshCore (Identity.cpp, validatePrivateKey)
TEST_PRV = ("7065e18fd9fabb70c1ed90dca19907de698c88b709ea146eafd93d9b830c7b60"
            "c4681193c79bbc39945ba8064104bb618f8fd7a84a0af6f57033d6e8ddcd6471")
TEST_PUB = "1ec77175b0918ed206f9ae04ec136d6d5d4315bb26305427f645b492e9350c10"


class Cfg:
    retention_days = 90
    stale_after_s = 90000


def test_pub_from_meshcore_private_key():
    assert keys.pub_from_prv(bytes.fromhex(TEST_PRV)).hex() == TEST_PUB
    assert keys.check_pair(TEST_PRV, TEST_PUB.upper())
    assert not keys.check_pair(TEST_PRV, "00" * 32)
    assert not keys.check_pair("zz", TEST_PUB)


def test_new_keypair_is_valid_and_not_reserved():
    for _ in range(20):
        prv, pub = keys.new_keypair()
        assert keys.check_pair(prv, pub)
        assert pub[:2] not in ("00", "ff")


def test_vault_roundtrip_and_tamper():
    v = keys.Vault("geheim")
    blob = v.seal({"private_key": TEST_PRV, "name": "🇧🇪 Test"})
    assert v.open(blob)["name"] == "🇧🇪 Test"
    with pytest.raises(ValueError):
        keys.Vault("ander").open(blob)


def test_profile_uses_system_settings():
    st = setmod.effective(Cfg(), {"prov_scope": "nl", "prov_path_bytes": 3})
    doc = keys.profile("Tracker 7", TEST_PRV, TEST_PUB, st, "ab" * 32)
    assert doc["radio_settings"] == {"frequency": 869618, "bandwidth": 62500, "spreading_factor": 8,
                                     "coding_rate": 8, "tx_power": 22}
    assert doc["meshtrack"]["path_bytes"] == 3 and doc["meshtrack"]["scope"] == "nl"
    assert doc["meshtrack"]["settings"]["target"] == "ab" * 32
    assert doc["channels"][0]["name"] == "Public"
    s = keys.summary(doc)
    assert s["radio"] == "869.618 MHz BW62.5 SF8 CR8" and "private_key" not in s


def test_text_and_float_settings():
    assert setmod.validate({"prov_scope": " be ", "prov_freq": "869.525"}) == {"prov_scope": "be", "prov_freq": 869.525}
    with pytest.raises(ValueError):
        setmod.validate({"prov_scope": "#be"})
    with pytest.raises(ValueError):
        setmod.validate({"prov_path_bytes": 1})          # 1 byte per hop is niet toegelaten


def test_db_keys_and_lost():
    db = DB(":memory:")
    tid = db.add_tracker(TEST_PUB, "T")
    kid = db.add_key(tid, "generated", "admin", TEST_PUB, "", {"name": "T"}, "blob")
    assert db.keys(tid)[0]["summary"] == {"name": "T"} and "blob" not in db.keys(tid)[0]
    assert db.key(kid)["blob"] == "blob" and db.key_counts() == {tid: 1}
    db.set_lost(tid, True)
    t = db.tracker(tid)
    assert t["lost"] == 1 and t["lost_since"]
    since = t["lost_since"]
    db.set_lost(tid, True)                                 # opnieuw: tijdstip blijft
    assert db.tracker(tid)["lost_since"] == since
    db.mark_lost_seen(tid, 123)
    assert db.tracker(tid)["lost"] == 1                    # gezien: blijft verloren
    db.set_lost(tid, False)
    assert db.tracker(tid)["lost"] == 0 and db.tracker(tid)["lost_seen"] is None
    db.delete_tracker(tid)
    assert db.key(kid) is None
