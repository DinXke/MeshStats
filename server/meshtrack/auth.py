"""Eenvoudige login: één gebruiker, wachtwoord als PBKDF2-hash, sessie als
ondertekende cookie. Geen externe afhankelijkheden (moet offline werken).

Hash maken:  python -m meshtrack.auth
"""
from __future__ import annotations

import base64
import getpass
import hashlib
import hmac
import os
import secrets
import time

COOKIE = "mt_session"
ITER = 240_000


def _b64(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).decode().rstrip("=")


def _unb64(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def hash_password(pw: str) -> str:
    salt = os.urandom(16)
    dk = hashlib.pbkdf2_hmac("sha256", pw.encode(), salt, ITER)
    return f"pbkdf2_sha256${ITER}${_b64(salt)}${_b64(dk)}"


def verify_password(pw: str, stored: str) -> bool:
    try:
        algo, it, salt, dk = stored.split("$")
        if algo != "pbkdf2_sha256":
            return False
        calc = hashlib.pbkdf2_hmac("sha256", pw.encode(), _unb64(salt), int(it))
        return hmac.compare_digest(calc, _unb64(dk))
    except (ValueError, TypeError):
        return False


def make_session(user: str, secret: str, days: int) -> str:
    exp = int(time.time()) + days * 86400
    body = f"{user}|{exp}"
    sig = hmac.new(secret.encode(), body.encode(), hashlib.sha256).digest()
    return _b64(body.encode()) + "." + _b64(sig)


def check_session(token: str | None, secret: str) -> str | None:
    """Gebruikersnaam bij een geldige, niet-verlopen sessie, anders None."""
    if not token or not secret or "." not in token:
        return None
    try:
        b, s = token.split(".", 1)
        body = _unb64(b).decode()
        good = hmac.new(secret.encode(), body.encode(), hashlib.sha256).digest()
        if not hmac.compare_digest(good, _unb64(s)):
            return None
        user, exp = body.rsplit("|", 1)
        return user if int(exp) > time.time() else None
    except (ValueError, UnicodeDecodeError):
        return None


if __name__ == "__main__":
    pw = getpass.getpass("Nieuw wachtwoord: ")
    if pw != getpass.getpass("Nog eens: "):
        raise SystemExit("verschillend")
    print("password_hash:", hash_password(pw))
    print("session_secret:", secrets.token_urlsafe(32))
