"""Authenticatie: API-tokens (Bearer), admin-wachtwoorden en sessiecookies."""
import base64
import hashlib
import hmac
import json
import secrets
import time

from . import config, db

SESSION_COOKIE = "mcs_session"
SESSION_TTL = 12 * 3600


# ---- wachtwoorden -----------------------------------------------------------

def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 200_000)
    return f"pbkdf2${salt.hex()}${dk.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        _, salt_hex, dk_hex = stored.split("$")
        dk = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt_hex), 200_000)
        return hmac.compare_digest(dk.hex(), dk_hex)
    except (ValueError, TypeError):
        return False


# ---- API-tokens -------------------------------------------------------------

def create_token(name: str) -> str:
    token = "mcs_" + secrets.token_urlsafe(32)
    db.execute(
        "INSERT INTO tokens(name, token_hash, created_at) VALUES(?,?,?)",
        (name, hashlib.sha256(token.encode()).hexdigest(), db.utcnow()),
    )
    return token


def check_token(token: str) -> bool:
    if not token:
        return False
    h = hashlib.sha256(token.encode()).hexdigest()
    row = db.qone("SELECT id FROM tokens WHERE token_hash=? AND revoked=0", (h,))
    if not row:
        return False
    db.execute("UPDATE tokens SET last_used=? WHERE id=?", (db.utcnow(), row["id"]))
    return True


# ---- sessies (HMAC-signed cookie) ------------------------------------------

def _sign(payload: bytes) -> str:
    return hmac.new(config.SECRET, payload, hashlib.sha256).hexdigest()


def make_session(username: str) -> str:
    payload = base64.urlsafe_b64encode(
        json.dumps({"u": username, "exp": int(time.time()) + SESSION_TTL}).encode()
    ).decode()
    return f"{payload}.{_sign(payload.encode())}"


def read_session(cookie: str | None) -> str | None:
    """Geeft de username terug, of None als de sessie ongeldig/verlopen is."""
    if not cookie or "." not in cookie:
        return None
    payload, sig = cookie.rsplit(".", 1)
    if not hmac.compare_digest(_sign(payload.encode()), sig):
        return None
    try:
        data = json.loads(base64.urlsafe_b64decode(payload))
    except (ValueError, TypeError):
        return None
    if data.get("exp", 0) < time.time():
        return None
    return data.get("u")


def csrf_token(session_cookie: str) -> str:
    return hmac.new(config.SECRET, b"csrf|" + session_cookie.encode(), hashlib.sha256).hexdigest()[:32]
