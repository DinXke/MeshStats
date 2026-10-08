"""Sleutels en toestelbackups.

MeshCore bewaart een privésleutel van 64 bytes in het formaat van orlp/ed25519:
SHA-512(seed) met de gebruikelijke clamping. De publieke sleutel is scalar×G
van de eerste 32 bytes. Zo maakt de server een sleutelpaar dat de firmware met
`key import` overneemt.

Backups volgen het exportformaat van de MeshCore-app (name, public_key,
private_key, radio_settings, channels, ...) met een extra blok "meshtrack"
(paden, regio, trackerinstellingen). Ze staan versleuteld (AES-GCM) in de
database; de sleutel daarvoor komt uit `keystore_secret` in config.yaml, of
anders uit `session_secret`.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
from typing import Any

from Crypto.Cipher import AES
from Crypto.PublicKey import ECC
from Crypto.PublicKey.ECC import EccKey

HEX = set("0123456789abcdef")


def is_hex(s: str, n: int) -> bool:
    return isinstance(s, str) and len(s) == n and set(s.lower()) <= HEX


def pub_from_prv(prv: bytes) -> bytes:
    d = int.from_bytes(prv[:32], "little")
    point = ECC._curves["ed25519"].G * d
    return EccKey(curve="ed25519", point=point).export_key(format="raw")


def new_keypair() -> tuple[str, str]:
    """(privkey 128 hex, pubkey 64 hex). Geen pubkey die met 00 of FF begint (gereserveerd)."""
    while True:
        h = bytearray(hashlib.sha512(os.urandom(32)).digest())
        h[0] &= 248
        h[31] &= 63
        h[31] |= 64
        pub = pub_from_prv(bytes(h))
        if pub[0] not in (0x00, 0xFF):
            return bytes(h).hex(), pub.hex()


def check_pair(prv_hex: str, pub_hex: str) -> bool:
    if not is_hex(prv_hex, 128) or not is_hex(pub_hex, 64):
        return False
    return pub_from_prv(bytes.fromhex(prv_hex)).hex() == pub_hex.lower()


class Vault:
    def __init__(self, secret: str):
        self.key = hashlib.sha256(("meshtrack-keystore|" + secret).encode()).digest()

    def seal(self, obj: dict[str, Any]) -> str:
        nonce = os.urandom(12)
        c = AES.new(self.key, AES.MODE_GCM, nonce=nonce)
        ct, tag = c.encrypt_and_digest(json.dumps(obj, ensure_ascii=False).encode())
        return base64.b64encode(nonce + tag + ct).decode()

    def open(self, blob: str) -> dict[str, Any]:
        raw = base64.b64decode(blob)
        c = AES.new(self.key, AES.MODE_GCM, nonce=raw[:12])
        return json.loads(c.decrypt_and_verify(raw[28:], raw[12:28]))


def profile(name: str, prv: str, pub: str, s: dict[str, Any], target: str = "") -> dict[str, Any]:
    """Een nieuw toestel: app-exportformaat met de standaardinstellingen uit Systeem."""
    channels = []
    if s.get("prov_public_channel"):
        channels.append({"name": "Public", "secret": "8b3387e9c5cdea6ac9e5edbaa115cd72"})
    return {
        "name": name,
        "public_key": pub,
        "private_key": prv,
        "radio_settings": {
            "frequency": round(float(s["prov_freq"]) * 1000),
            "bandwidth": round(float(s["prov_bw"]) * 1000),
            "spreading_factor": int(s["prov_sf"]),
            "coding_rate": int(s["prov_cr"]),
            "tx_power": int(s["prov_tx"]),
        },
        "channels": channels,
        "meshtrack": {
            "path_bytes": int(s["prov_path_bytes"]),
            "scope": str(s.get("prov_scope") or ""),
            "mode": "tracker",
            "settings": {"target": target} if target else {},
        },
    }


def summary(doc: dict[str, Any]) -> dict[str, Any]:
    """Wat de lijst van backups toont (zonder geheimen)."""
    r = doc.get("radio_settings") or {}
    mt = doc.get("meshtrack") or {}
    return {
        "name": doc.get("name"),
        "radio": f"{r.get('frequency', 0) / 1000:.3f} MHz BW{r.get('bandwidth', 0) / 1000:g} SF{r.get('spreading_factor')} CR{r.get('coding_rate')}" if r else "",
        "channels": len(doc.get("channels") or []),
        "contacts": len(doc.get("contacts") or []),
        "path_bytes": mt.get("path_bytes"),
        "scope": mt.get("scope"),
        "fw": mt.get("fw"),
    }
