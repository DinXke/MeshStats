"""Configuration and first-start bootstrap.

Naamswissel MeshStats -> MeshManager
------------------------------------
Elke omgevingsvariabele heet nu ``MM_...`` en niet meer ``MCS_...``. De oude
naam blijft gelezen worden, zodat een bestaande ``.env`` het na een update
gewoon doet: wie zijn installatie draaiend heeft, mag niet eerst een
configuratiebestand hoeven herschrijven voor de site weer opstart.

Waarom ``MM_`` en niet ``MESHMANAGER_``: het oude voorvoegsel was ook een
initialenreeks (MCS = MeshCore Stats), de ``.env`` staat vol regels met een
commentaarblok erboven, en een voorvoegsel van elf tekens duwt die regels over
de tachtig kolommen waar de rest van dit project zich aan houdt. Het risico op
botsing met iets anders in de omgeving is klein: deze variabelen worden gezet
in docker-compose en in de systemd-unit, allebei een eigen omgeving, en het
achtervoegsel (``MM_MQTT_HOST``, ``MM_TSDB_URL``) is nergens generiek.

Wanneer de terugval weg mag: zodra elke installatie die deze repo gebruikt
minstens één keer met de nieuwe namen herstart is. Praktisch: laat hem staan
tot de volgende hoofdversie, en verwijder dan ``_LEGACY_PREFIX`` samen met de
laatste ``MCS_``-regel uit ``.env.example``.
"""
import os
import secrets
from pathlib import Path

_PREFIX = "MM_"
# Zie de moduletekst hierboven voor wanneer dit weg mag.
_LEGACY_PREFIX = "MCS_"


def env(name: str, default: str = "") -> str:
    """Waarde van ``MM_<name>``, of anders van het oude ``MCS_<name>``.

    Bewust in deze volgorde en niet omgekeerd: wie tijdens de overgang beide
    zet -- bijvoorbeeld omdat docker-compose de nieuwe naam invult terwijl een
    oude ``.env`` de oude nog bevat -- bedoelt de nieuwe. En bewust ``in`` en
    geen ``os.environ.get(...) or ...``: een variabele die met opzet op leeg
    gezet is ("geen TSDB, hou alles in SQLite") is een antwoord, geen stilte,
    en mag niet stilletjes door de oude naam overruled worden.
    """
    key = _PREFIX + name
    if key in os.environ:
        return os.environ[key]
    return os.environ.get(_LEGACY_PREFIX + name, default)


DATA_DIR = Path(env("DATA_DIR", str(Path(__file__).resolve().parent.parent / "data")))
DATA_DIR.mkdir(parents=True, exist_ok=True)

# De databank heet sinds de hernoeming ``meshmanager.sqlite3``. Een bestaand
# ``mcs.sqlite3`` wordt gebruikt waar het staat -- NIET hernoemd.
#
# Hernoemen was de andere optie en is bewust verworpen. Een hernoeming is
# eenrichtingsverkeer: wie na de update terugrolt naar de vorige versie van de
# site (en dat is precies wat je doet als er iets misgaat) vindt dan geen
# databank meer, krijgt een lege aangemaakt, en ziet een site zonder repeaters,
# zonder historiek en zonder beheerderswachtwoord. Dat het bestand er nog staat
# maakt de schrik er niet minder om. Deze kant op kost het alleen een bestand
# met een verouderde naam, en dat is een prijs die je op elk moment alsnog kunt
# betalen: stop de site, hernoem de drie bestanden (``.sqlite3``, ``-wal``,
# ``-shm``) en start opnieuw.
_DB_NAME = "meshmanager.sqlite3"
_LEGACY_DB_NAME = "mcs.sqlite3"
DB_PATH = DATA_DIR / _DB_NAME
if not DB_PATH.exists() and (DATA_DIR / _LEGACY_DB_NAME).exists():
    DB_PATH = DATA_DIR / _LEGACY_DB_NAME

SECRET_FILE = DATA_DIR / "secret.key"

RETENTION_DAYS = int(env("RETENTION_DAYS", "180"))
# Raw packet receptions arrive far faster than metric samples and lose their
# value within days, so they get their own, much shorter retention. All three
# values below are only the DEFAULT for a fresh install: the admin page stores
# the running value in the ``settings`` table, so raising a retention does not
# need a container restart. See db.retention_settings().
PACKET_RETENTION_DAYS = int(env("PACKET_RETENTION_DAYS", "7"))

# The two ceilings that make the retention above safe to raise.
#
# A period alone is not a guarantee. "Keep 30 days" says nothing about how much
# disk that is -- it is a promise about time made in the hope that traffic stays
# what it was. One node that starts mirroring every frame it hears, and 30 days
# is suddenly gigabytes. So the period is what we AIM for and these two are what
# we PROMISE, and when they collide the oldest packets go first (FIFO).
#
# Rows first, because counting rows is a B-tree lookup on the primary key while
# measuring the packet table's real share of the file needs dbstat -- and
# because a row is what a packet actually costs. Measured on the live server:
# 7 477 packets take about 2.5 MB including their three indexes (roughly 335
# bytes a row, of which ~134 is the raw hex frame), at an intake of about 3 738
# packets a day. 200 000 rows is therefore some 53 days of today's traffic in
# about 80 MB -- eight times the default 7-day window, so the cap is a guard
# against an explosion rather than a second retention that quietly overrules
# the first one.
PACKET_MAX_ROWS = int(env("PACKET_MAX_ROWS", "200000"))
# Megabytes second, because megabytes are what actually runs out. This one
# counts the whole file (plus its WAL), not just packets, so it also catches
# growth this app did not predict -- and it is the ceiling that cannot be
# argued away by raising the row cap. 512 MB against the 19 GB free on the
# reference server is 2.6% of the disk and about 35x the database as it stands,
# which is room to grow without ever being the reason a host fills up.
DB_MAX_MB = int(env("DB_MAX_MB", "512"))
# Minutes between two retention passes. Pruning only at startup means a server
# that runs for months never prunes at all -- see retention.py.
PRUNE_MINUTES = int(env("PRUNE_MINUTES", "60"))
SITE_NAME = env("SITE_NAME", "MeshCore Repeater Stats")
# Heartbeat: also store a sample when the value did not change but the previous
# one is older than this many minutes, so charts keep running.
HEARTBEAT_MIN = int(env("HEARTBEAT_MIN", "5"))

# Largest request body we will read, for every method and route. The API used to
# check Content-Length only, which a chunked request simply omits; this cap is
# enforced while streaming instead, so it also covers admin form posts.
MAX_BODY_BYTES = int(env("MAX_BODY_BYTES", str(2_000_000)))

# How many reverse proxies sit in front of the app. Uvicorn runs with
# --forwarded-allow-ips "*", which makes it take the LEFTMOST X-Forwarded-For
# entry -- the one a client can write itself -- so request.client.host is not
# trustworthy for rate limiting. Each proxy appends the address it saw, so the
# honest client address is this many entries from the right: 1 for a single hop
# (cloudflared straight to the app), 2 when a second proxy sits behind it.
# Too high a value hands attackers a spoofable bucket key; too low lumps every
# visitor onto one proxy address, so only raise it when you really added a hop.
TRUSTED_PROXY_HOPS = max(1, int(env("TRUSTED_PROXY_HOPS", "1")))


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
