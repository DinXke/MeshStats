"""MC Repeater Stats — publieke MeshCore-repeaterstatistieken, gevoed door de nodes zelf.

De nodes publiceren rechtstreeks over MQTT. De HTTP-API blijft bestaan voor wie
via Home Assistant of een eigen script binnenkomt, maar is geen vereiste meer.
"""
import secrets
import sys
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from . import (auth, clocksync, db, limits, meshmoni, mqtt_ingest, rbac,
               retention, routes_admin, routes_api, routes_public, sweepsched,
               tsdb, webpush)

app = FastAPI(title="MC Repeater Stats", docs_url=None, redoc_url=None, openapi_url=None)

# Registered before security_headers, which (add_middleware inserts at the
# front) leaves this one just inside it: oversized bodies are refused before any
# route, form parser or JSON decoder sees them, and the refusal still picks up
# the security headers on its way out.
app.add_middleware(limits.BodySizeLimitMiddleware)


@app.middleware("http")
async def security_headers(request, call_next):
    resp = await call_next(request)
    h = resp.headers
    h.setdefault("X-Content-Type-Options", "nosniff")
    h.setdefault("X-Frame-Options", "DENY")
    h.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    h.setdefault("Permissions-Policy", "geolocation=(), microphone=(), camera=()")
    # Without Cache-Control, browsers apply heuristic caching to /static and
    # may keep serving yesterday's app.js long after a deploy — readers then
    # see a mix of new API responses and old frontend code. "no-cache" does
    # not forbid storing; it forces revalidation, and StaticFiles already
    # answers those with a cheap 304 via ETag/Last-Modified. Hash-versioned
    # filenames were rejected: they need a build step, and this site
    # deliberately has none.
    if request.url.path.startswith("/static"):
        h.setdefault("Cache-Control", "no-cache")
    h.setdefault(
        "Content-Security-Policy",
        "default-src 'self'; "
        "script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net https://unpkg.com; "
        "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com https://unpkg.com; "
        "font-src https://fonts.gstatic.com; "
        "img-src 'self' data: https://unpkg.com https://*.basemaps.cartocdn.com; "
        "connect-src 'self'; "
        "frame-ancestors 'none'; base-uri 'self'; form-action 'self'; object-src 'none'",
    )
    return resp

app.include_router(routes_api.router)
app.include_router(routes_admin.router)
app.include_router(routes_public.router)
app.include_router(meshmoni.router)   # de PWA-subsite voor op de telefoon
app.mount("/static", StaticFiles(directory=str(Path(__file__).resolve().parent / "static")), name="static")


@app.on_event("startup")
def bootstrap():
    db.get_conn()
    if not db.qone("SELECT 1 FROM admins LIMIT 1"):
        password = secrets.token_urlsafe(12)
        # Serverbeheerder, want anders is er bij de allereerste start een account
        # dat niets mag en niemand die daar iets aan kan veranderen.
        rbac.maak_gebruiker("admin", auth.hash_password(password),
                            is_superuser=True, door="eerste start")
        print(f"[meshmanager] Eerste start: admin-account aangemaakt.", flush=True)
        print(f"[meshmanager] Gebruikersnaam: admin  Wachtwoord: {password}", flush=True)
        print(f"[meshmanager] Wijzig dit meteen via /admin.", flush=True)
    db.prune()
    # ... and again every hour after this one. Pruning only here made the
    # retention an act that happened at startup rather than a rule that holds:
    # a container that runs for months never threw anything away, and the first
    # sign of that is a full disk. See retention.py.
    retention.start()
    # Webpush kijkt periodiek in de alerts-tabel, zoals retention in de zijne;
    # zonder VAPID-sleutels start hij niet en zegt hij waarom (zie webpush.py).
    webpush.start()
    # Contacts stored before this column existed, or while borders.json was
    # missing, are classified here rather than never: ingest only classifies a
    # position when it changes, and most nodes never move.
    filled = db.classify_countries()
    if filled:
        print(f"[meshmanager] Land bepaald voor {filled} contact(en).", flush=True)
    # Started before the ingest paths open: the writer thread has to exist by the
    # time the first measurement arrives, or those points take the spill route
    # for no reason.
    tsdb.start()
    mqtt_ingest.start()   # nodes publiceren hun statistieken via MQTT
    # Als laatste, want deze publiceert en heeft de client hierboven nodig. Hij
    # wacht sowieso vijf minuten voor zijn eerste ronde -- zie FIRST_RUN_DELAY_S
    # -- maar de volgorde hier maakt dat niet toevallig goed.
    clocksync.start()     # en krijgen van ons periodiek de juiste tijd terug
    sweepsched.start()    # en worden volgens hun eigen schema uitgevraagd


def set_password():
    """CLI: python -m app.main set-password <gebruikersnaam> — leest wachtwoord van stdin.

    Dit is de weg terug naar binnen als er niemand meer bij kan, en daarom maakt
    hij een account dat hij zelf moet aanmaken meteen serverbeheerder. Een
    herstelweg die een account zonder rechten oplevert is geen herstelweg.
    Bestaande accounts houden de rechten die ze hadden -- het zetten van een
    wachtwoord is geen reden om iemand te promoveren.
    """
    username = sys.argv[2] if len(sys.argv) > 2 else "admin"
    password = sys.stdin.readline().strip()
    if len(password) < 8:
        print("Wachtwoord moet minstens 8 tekens zijn", file=sys.stderr)
        sys.exit(1)
    db.get_conn()
    if db.qone("SELECT 1 FROM admins WHERE username=?", (username,)):
        db.execute("UPDATE admins SET pw_hash=? WHERE username=?",
                   (auth.hash_password(password), username))
        print(f"Wachtwoord ingesteld voor '{username}'")
    else:
        rbac.maak_gebruiker(username, auth.hash_password(password),
                            is_superuser=True, door="opdrachtregel")
        print(f"Account '{username}' aangemaakt als serverbeheerder")


def promote():
    """CLI: python -m app.main promote <gebruikersnaam> — maak iemand serverbeheerder.

    De tweede herstelweg. Bestaat voor het geval dat wél een account bestaat maar
    geen enkele meer serverbeheerder is: dan is de gebruikerspagina onbereikbaar
    en is een wachtwoord zetten niet genoeg.
    """
    username = sys.argv[2] if len(sys.argv) > 2 else "admin"
    db.get_conn()
    row = db.qone("SELECT id FROM admins WHERE username=?", (username,))
    if not row:
        print(f"Onbekend account '{username}'", file=sys.stderr)
        sys.exit(1)
    rbac.zet_serverbeheerder(row["id"], True)
    rbac.zet_uit(row["id"], False)
    print(f"'{username}' is nu serverbeheerder")


if __name__ == "__main__" and len(sys.argv) > 1:
    if sys.argv[1] == "set-password":
        set_password()
    elif sys.argv[1] == "promote":
        promote()
