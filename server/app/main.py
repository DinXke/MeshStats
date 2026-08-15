"""MC Repeater Stats — publieke MeshCore-repeaterstatistieken, gevoed door de nodes zelf.

De nodes publiceren rechtstreeks over MQTT. De HTTP-API blijft bestaan voor wie
via Home Assistant of een eigen script binnenkomt, maar is geen vereiste meer.
"""
import secrets
import sys
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from . import (auth, db, limits, mqtt_ingest, routes_admin, routes_api,
               routes_public, tsdb)

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
app.mount("/static", StaticFiles(directory=str(Path(__file__).resolve().parent / "static")), name="static")


@app.on_event("startup")
def bootstrap():
    db.get_conn()
    if not db.qone("SELECT 1 FROM admins LIMIT 1"):
        password = secrets.token_urlsafe(12)
        db.execute(
            "INSERT INTO admins(username, pw_hash) VALUES(?,?)",
            ("admin", auth.hash_password(password)),
        )
        print(f"[mc-repeater-stats] Eerste start: admin-account aangemaakt.", flush=True)
        print(f"[mc-repeater-stats] Gebruikersnaam: admin  Wachtwoord: {password}", flush=True)
        print(f"[mc-repeater-stats] Wijzig dit meteen via /admin.", flush=True)
    db.prune()
    # Contacts stored before this column existed, or while borders.json was
    # missing, are classified here rather than never: ingest only classifies a
    # position when it changes, and most nodes never move.
    filled = db.classify_countries()
    if filled:
        print(f"[mc-repeater-stats] Land bepaald voor {filled} contact(en).", flush=True)
    # Started before the ingest paths open: the writer thread has to exist by the
    # time the first measurement arrives, or those points take the spill route
    # for no reason.
    tsdb.start()
    mqtt_ingest.start()   # nodes publiceren hun statistieken via MQTT


def set_password():
    """CLI: python -m app.main set-password <gebruikersnaam> — leest wachtwoord van stdin."""
    username = sys.argv[2] if len(sys.argv) > 2 else "admin"
    password = sys.stdin.readline().strip()
    if len(password) < 8:
        print("Wachtwoord moet minstens 8 tekens zijn", file=sys.stderr)
        sys.exit(1)
    db.get_conn()
    if db.qone("SELECT 1 FROM admins WHERE username=?", (username,)):
        db.execute("UPDATE admins SET pw_hash=? WHERE username=?",
                   (auth.hash_password(password), username))
    else:
        db.execute("INSERT INTO admins(username, pw_hash) VALUES(?,?)",
                   (username, auth.hash_password(password)))
    print(f"Wachtwoord ingesteld voor '{username}'")


if __name__ == "__main__" and len(sys.argv) > 1 and sys.argv[1] == "set-password":
    set_password()
