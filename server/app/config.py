"""Configuration and first-start bootstrap."""
import os
import secrets
from pathlib import Path

DATA_DIR = Path(os.environ.get("MCS_DATA_DIR", Path(__file__).resolve().parent.parent / "data"))
DATA_DIR.mkdir(parents=True, exist_ok=True)

DB_PATH = DATA_DIR / "mcs.sqlite3"
SECRET_FILE = DATA_DIR / "secret.key"

RETENTION_DAYS = int(os.environ.get("MCS_RETENTION_DAYS", "180"))
# Raw packet receptions arrive far faster than metric samples and lose their
# value within days, so they get their own, much shorter retention.
PACKET_RETENTION_DAYS = int(os.environ.get("MCS_PACKET_RETENTION_DAYS", "7"))
SITE_NAME = os.environ.get("MCS_SITE_NAME", "MeshCore Repeater Stats")
# Heartbeat: also store a sample when the value did not change but the previous
# one is older than this many minutes, so charts keep running.
HEARTBEAT_MIN = int(os.environ.get("MCS_HEARTBEAT_MIN", "5"))

# Largest request body we will read, for every method and route. The API used to
# check Content-Length only, which a chunked request simply omits; this cap is
# enforced while streaming instead, so it also covers admin form posts.
MAX_BODY_BYTES = int(os.environ.get("MCS_MAX_BODY_BYTES", str(2_000_000)))

# How many reverse proxies sit in front of the app. Uvicorn runs with
# --forwarded-allow-ips "*", which makes it take the LEFTMOST X-Forwarded-For
# entry -- the one a client can write itself -- so request.client.host is not
# trustworthy for rate limiting. Each proxy appends the address it saw, so the
# honest client address is this many entries from the right: 1 for a single hop
# (cloudflared straight to the app), 2 when a second proxy sits behind it.
# Too high a value hands attackers a spoofable bucket key; too low lumps every
# visitor onto one proxy address, so only raise it when you really added a hop.
TRUSTED_PROXY_HOPS = max(1, int(os.environ.get("MCS_TRUSTED_PROXY_HOPS", "1")))


def get_secret() -> bytes:
    if SECRET_FILE.exists():
        return SECRET_FILE.read_bytes()
    secret = secrets.token_bytes(32)
    SECRET_FILE.write_bytes(secret)
    try:
        os.chmod(SECRET_FILE, 0o600)
    except OSError:
        pass
    return secret


SECRET = get_secret()
