"""MC Repeater Stats — publieke MeshCore-repeaterstatistieken, gevoed door de nodes zelf.

De nodes publiceren rechtstreeks over MQTT. De HTTP-API blijft bestaan voor wie
via Home Assistant of een eigen script binnenkomt, maar is geen vereiste meer.
"""
import secrets
import sys
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from . import (auth, clocksync, companions, db, hadiscovery, limits, meshmoni,
               mqtt_ingest, rbac, retention, routes_admin, routes_api,
               routes_companions, routes_public, sensornode, sensorpush,
               sweepsched, tsdb, webpush)

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
    # deliberately has none. /tiles krijgt dezelfde behandeling: de vector-tiles
    # (basemap.pmtiles) worden per byte-range opgehaald, en bij een wissel van
    # dekkingsgebied verandert de hele indeling -- zonder revalidatie zou een
    # browser oude en nieuwe ranges kunnen mengen en de kaart beschadigen. De
    # ETag verandert mee, dus revalidatie is een goedkope 304 zolang niets wijzigt.
    if request.url.path.startswith("/static") or request.url.path.startswith("/tiles"):
        h.setdefault("Cache-Control", "no-cache")
    h.setdefault(
        "Content-Security-Policy",
        "default-src 'self'; "
        "script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net https://unpkg.com; "
        "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com https://unpkg.com; "
        "font-src https://fonts.gstatic.com; "
        "img-src 'self' data: https://unpkg.com; "
        "connect-src 'self'; "
        # MapLibre GL maakt zijn tegel-worker als blob-URL aan; zonder deze regel
        # blokkeert default-src 'self' hem. De vector-tiles, fonts en sprites zelf
        # zijn same-origin (/tiles/...) en vallen onder connect-src 'self'.
        "worker-src blob:; "
        "frame-ancestors 'none'; base-uri 'self'; form-action 'self'; object-src 'none'",
    )
    return resp

app.include_router(routes_api.router)
app.include_router(routes_admin.router)
# Companion-beheer als eigen module met eigen router (zelfde /admin-prefix, eigen
# top-level navigatie-onderdeel). Zie routes_companions.py.
app.include_router(routes_companions.router)
app.include_router(routes_public.router)
app.include_router(meshmoni.router)   # de PWA-subsite voor op de telefoon
app.include_router(sensorpush.router)  # gebeurtenis-push van sensornodes
app.include_router(companions.router)  # instant-push van companion-locatie/-val (POST /api/companion)
app.mount("/static", StaticFiles(directory=str(Path(__file__).resolve().parent / "static")), name="static")
# Zelf-gehoste kaart-assets: vector-tiles (basemap.pmtiles), glyph-fonts en sprites,
# als read-only volume gemount op /tiles (zie docker-compose.yml). Starlette's
# StaticFiles ondersteunt Range-requests, wat pmtiles nodig heeft om alleen de
# bekeken tegels op te halen. check_dir=False zodat de app ook zonder het volume
# opstart -- de kaarten vallen dan terug op OSM-raster (zie static/basemap.js).
app.mount("/tiles", StaticFiles(directory="/tiles", check_dir=False), name="tiles")


@app.on_event("startup")
def bootstrap():
    # Eerste regel van elke start: welke build dit is. Het journal van een
    # container die maanden draait begint dan met het antwoord op de vraag die
    # bij elke storing als eerste komt.
    from . import version as _version
    print("[meshmanager] MeshManager %s" % _version.info()["label"], flush=True)
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
    # En de nodes die niet over MQTT binnenkomen maar hun eigen API over IP
    # aanbieden, worden op hun eigen ritme uitgelezen. Deze weg raakt de broker
    # niet, dus hij hangt niet aan de client hierboven -- hij staat er toch onder,
    # omdat de opstartvolgorde daarmee de leesbare volgorde blijft: eerst de weg
    # waar dit project rond gebouwd is, dan de weg ernaast.
    sensornode.start()
    # De stiltebewaking van de gebeurtenis-push. Na sensornode.start() om
    # dezelfde leesvolgorde-reden; hij doet zelf niets zolang MM_PUSH_TOKEN
    # leeg is, en ijkt zijn startpunt op nu -- zie sensorpush._seed.
    sensorpush.start()
    # De locatie van de beheerde companions, uit /companions.json op hun
    # afzender-node -- dezelfde soort weg als sensornode.start() hierboven
    # (een periodieke GET over het lokale net) maar met zijn eigen klein
    # schema, want companions.py kent de sensornode-laag al en andersom zou een
    # kringverwijzing zijn. Zie companions.py voor de aannames over dat
    # endpoint.
    companions.start_location_poll()
    # En als laatste de weg naar buiten: onze telemetrie als HA-entiteiten op de
    # broker van Home Assistant. Hij hangt een haak in db.ingest (zie
    # register_ingest_hook), dus hij moet ná de ingest-wegen starten -- en zonder
    # MM_HA_MQTT_HOST + MM_HA_DISCOVERY_ENABLED start hij niet en zegt hij waarom.
    hadiscovery.start()


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
