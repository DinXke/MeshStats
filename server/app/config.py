"""Configuratie en eerste-start-bootstrap."""
import os
import secrets
from pathlib import Path

DATA_DIR = Path(os.environ.get("MCS_DATA_DIR", Path(__file__).resolve().parent.parent / "data"))
DATA_DIR.mkdir(parents=True, exist_ok=True)

DB_PATH = DATA_DIR / "mcs.sqlite3"
SECRET_FILE = DATA_DIR / "secret.key"

RETENTION_DAYS = int(os.environ.get("MCS_RETENTION_DAYS", "180"))
SITE_NAME = os.environ.get("MCS_SITE_NAME", "MeshCore Repeater Stats")
# Heartbeat: sla een sample ook op als de waarde niet wijzigde maar de vorige
# ouder is dan dit aantal minuten (houdt grafieken doorlopend).
HEARTBEAT_MIN = int(os.environ.get("MCS_HEARTBEAT_MIN", "5"))


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
