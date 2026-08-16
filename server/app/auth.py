"""Authenticatie: API-tokens (Bearer), admin-wachtwoorden en sessiecookies."""
import base64
import hashlib
import hmac
import json
import secrets
import time

from . import config, db

# Koeknamen dragen de nieuwe naam. Eén keer uitloggen is de hele prijs: een
# sessie leeft twaalf uur en het inlogscherm staat een klik verderop. De oude
# koek expliciet wissen bij het inloggen is bewust WEL gedaan (zie
# routes_admin.login) -- een vergeten mcs_session-koek die maanden later nog
# meegestuurd wordt, is een geldig ondertekend token dat niemand meer ziet.
SESSION_COOKIE = "mm_session"
LEGACY_SESSION_COOKIE = "mcs_session"
SESSION_TTL = 12 * 3600

# Pre-session cookie that anchors the CSRF token on the login form; the visitor
# has no session yet, so the token has to hang off something else.
LOGIN_COOKIE = "mm_login"
LEGACY_LOGIN_COOKIE = "mcs_login"
LOGIN_TTL = 30 * 60


def eq(a: str, b: str) -> bool:
    """Constant-time comparison for anything secret.

    A plain ``==`` on a token leaks, through its running time, how many leading
    characters were right, which turns guessing into a character-by-character
    walk. Every comparison of a token, signature or digest in this application
    goes through here.
    """
    return hmac.compare_digest(str(a or ""), str(b or ""))


# ---- wachtwoorden -----------------------------------------------------------

def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 200_000)
    return f"pbkdf2${salt.hex()}${dk.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        _, salt_hex, dk_hex = stored.split("$")
        dk = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt_hex), 200_000)
        return eq(dk.hex(), dk_hex)
    except (ValueError, TypeError):
        return False


# A real hash to check against when the username does not exist, so a wrong
# username and a wrong password cost the same 200_000 rounds. Without it the
# response time alone tells an attacker which accounts are worth attacking.
_DUMMY_HASH = hash_password(secrets.token_urlsafe(16))


def verify_dummy(password: str) -> None:
    verify_password(password, _DUMMY_HASH)


# ---- API-tokens -------------------------------------------------------------

def create_token(name: str) -> str:
    # Nieuwe tokens dragen het nieuwe voorvoegsel. Bestaande tokens blijven
    # gewoon werken: er wordt op de hash gecontroleerd, niet op het voorvoegsel,
    # dus dit is enkel wat je op een token leest.
    token = "mm_" + secrets.token_urlsafe(32)
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


def password_stamp(username: str) -> str | None:
    """Short fingerprint of the account's current password hash, or None.

    Signed into every session so that changing a password silently invalidates
    the sessions minted under the old one: the stamp in the cookie no longer
    matches the account. This is what makes a stolen cookie revocable without a
    session table -- the account row we already read is the revocation list, and
    the hash never leaves the server because only its HMAC is published.
    """
    row = db.qone("SELECT pw_hash FROM admins WHERE username=?", (username,))
    if not row:
        return None
    return hmac.new(config.SECRET, b"pwstamp|" + row["pw_hash"].encode(),
                    hashlib.sha256).hexdigest()[:16]


def make_session(username: str) -> str:
    payload = base64.urlsafe_b64encode(
        json.dumps({"u": username, "exp": int(time.time()) + SESSION_TTL,
                    "v": password_stamp(username) or ""}).encode()
    ).decode()
    return f"{payload}.{_sign(payload.encode())}"


def read_session(cookie: str | None) -> str | None:
    """Geeft de username terug, of None als de sessie ongeldig/verlopen is."""
    if not cookie or "." not in cookie:
        return None
    payload, sig = cookie.rsplit(".", 1)
    if not eq(_sign(payload.encode()), sig):
        return None
    try:
        data = json.loads(base64.urlsafe_b64decode(payload))
    except (ValueError, TypeError):
        return None
    if data.get("exp", 0) < time.time():
        return None
    username = data.get("u")
    if not username:
        return None
    # Signature and expiry only prove the cookie is ours and still young; the
    # stamp is what proves the password behind it has not been changed since.
    # Sessions from before this check carry no stamp and are rejected, which is
    # the intended one-off logout.
    stamp = password_stamp(username)
    if stamp is None or not eq(stamp, data.get("v", "")):
        return None
    return username


def csrf_token(anchor: str) -> str:
    """CSRF token bound to a cookie value: the session cookie for a logged-in
    admin, the short-lived login nonce for the login form itself."""
    return hmac.new(config.SECRET, b"csrf|" + anchor.encode(), hashlib.sha256).hexdigest()[:32]


def new_login_nonce() -> str:
    return secrets.token_urlsafe(24)
