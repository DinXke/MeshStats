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


def make_session(user: str, secret: str, days: int, gen: int = 0) -> str:
    """Ondertekende sessie: gebruiker, generatie en vervaldatum. Een hogere
    generatie in de database (nieuw wachtwoord, gedeactiveerd) maakt alle
    oudere sessies van die gebruiker ongeldig."""
    exp = int(time.time()) + days * 86400
    body = f"{user}|{gen}|{exp}"
    sig = hmac.new(secret.encode(), body.encode(), hashlib.sha256).digest()
    return _b64(body.encode()) + "." + _b64(sig)


def check_session(token: str | None, secret: str) -> tuple[str, int] | None:
    """(gebruiker, generatie) bij een geldige, niet-verlopen sessie, anders None."""
    info = session_info(token, secret)
    return (info[0], info[1]) if info else None


def needs_renewal(token: str | None, secret: str, days: int, now: float | None = None) -> bool:
    """Glijdende sessie: vernieuwen als de sessie al minstens een dag oud is (vervaldatum meer
    dan een dag vroeger dan bij een verse sessie). Zo hoogstens één nieuwe cookie per dag."""
    info = session_info(token, secret)
    if not info:
        return False
    now = time.time() if now is None else now
    return info[2] < now + days * 86400 - 86400


def session_info(token: str | None, secret: str) -> tuple[str, int, int] | None:
    """(gebruiker, generatie, vervaltijd) bij een geldige, niet-verlopen sessie, anders None."""
    if not token or not secret or "." not in token:
        return None
    try:
        b, s = token.split(".", 1)
        body = _unb64(b).decode()
        good = hmac.new(secret.encode(), body.encode(), hashlib.sha256).digest()
        if not hmac.compare_digest(good, _unb64(s)):
            return None
        parts = body.split("|")
        if len(parts) == 2:                 # oud formaat zonder generatie
            user, exp, gen = parts[0], parts[1], "0"
        else:
            user, gen, exp = parts[0], parts[1], parts[2]
        return (user, int(gen), int(exp)) if int(exp) > time.time() else None
    except (ValueError, UnicodeDecodeError):
        return None


def safe_next(target: str | None) -> str | None:
    """Doel na het inloggen: alleen een relatief pad op deze site ("/..."), nooit "//host" of
    "/\\host" (de browser maakt daar een andere site van) en geen stuurtekens. Anders None."""
    if not target or len(target) > 2000 or not target.startswith("/"):
        return None
    if target[1:2] in ("/", "\\") or any(ord(ch) < 32 or ord(ch) == 127 for ch in target):
        return None
    return target


def password_problem(pw: str) -> str | None:
    """Minimale eisen; None = goed."""
    if len(pw) < 8:
        return "minstens 8 tekens"
    return None


if __name__ == "__main__":
    pw = getpass.getpass("Nieuw wachtwoord: ")
    if pw != getpass.getpass("Nog eens: "):
        raise SystemExit("verschillend")
    print("password_hash:", hash_password(pw))
    print("session_secret:", secrets.token_urlsafe(32))
