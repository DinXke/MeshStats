"""MeshTrack-server: FastAPI-app.

Start:  MESHTRACK_CONFIG=/etc/meshtrack/config.yaml python -m meshtrack.main

Toegang: elke aanvraag krijgt een "principal" (ingelogde gebruiker of
deellink, zie rbac.py). Elke endpoint vraagt het recht dat hij nodig heeft en
filtert trackers op wat die principal mag zien. Ook de live-updates over de
WebSocket worden per verbinding gefilterd.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import secrets
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Optional

from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import auth, config, geofence, ingest, nodes, rbac
from .db import DB
from .mesh_client import MeshLink, send_text
from .rbac import PERMS, Principal
from .roadgraph import Router
from .sim import SimManager, new_pubkey

log = logging.getLogger("meshtrack")
STATIC = Path(__file__).resolve().parent.parent / "static"
HEX64 = re.compile(r"^[0-9a-fA-F]{64}$")
COLOR = re.compile(r"^#[0-9a-fA-F]{6}$")
USERNAME = re.compile(r"^[A-Za-z0-9._-]{2,32}$")
SHARE_COOKIE = "mt_share"
VERSION = "0.3.0"


# ---- live-updates -----------------------------------------------------------

class Hub:
    """WebSocket-clients, elk met hun eigen principal: een bericht over een
    tracker gaat alleen naar wie die tracker mag zien."""

    def __init__(self) -> None:
        self.clients: dict[WebSocket, Principal] = {}

    @staticmethod
    def _allowed(p: Principal, msg: dict[str, Any]) -> bool:
        t = msg.get("type")
        if t in ("position", "tracker"):
            tid = (msg.get("tracker") or {}).get("id")
            return tid is not None and p.sees(tid)
        if t == "tracker_deleted":
            return p.sees(msg.get("id", -1)) or p.tracker_ids is None
        if t == "geofence":
            return p.can("zones.view") and p.sees(msg["event"]["tracker_id"])
        if t == "geofences":
            return p.can("zones.view")
        return True

    async def send(self, msg: dict[str, Any]) -> None:
        cache: dict[tuple[bool, bool], str] = {}
        for ws, p in list(self.clients.items()):
            if not self._allowed(p, msg):
                continue
            key = (p.can("map.details"), p.can("companion.view"))
            if key not in cache:
                m = msg if key[0] else strip_msg(msg)
                if m.get("type") == "mesh" and not key[1]:
                    m = {"type": "mesh", "mesh": {k: m["mesh"].get(k) for k in ("connected", "name")}}
                cache[key] = json.dumps(m)
            try:
                await ws.send_text(cache[key])
            except Exception:  # noqa: BLE001
                self.clients.pop(ws, None)


class State:
    cfg: config.Config
    db: DB
    mesh: MeshLink
    hub: Hub
    sims: SimManager


S = State()


def tracker_out(t: dict[str, Any], p: Optional[Principal] = None) -> dict[str, Any]:
    now = int(time.time())
    out = dict(t)
    out["stale"] = not t["last_rx"] or now - t["last_rx"] > S.cfg.stale_after_s
    if p is not None and not p.can("map.details"):
        out = strip_tracker(out)
    return out


DETAIL_FIELDS = ("pubkey", "last_snr", "last_path_len", "last_seq", "notes")


def strip_tracker(t: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in t.items() if k not in DETAIL_FIELDS}


def strip_msg(msg: dict[str, Any]) -> dict[str, Any]:
    m = dict(msg)
    if "tracker" in m and isinstance(m["tracker"], dict):
        m["tracker"] = strip_tracker(m["tracker"])
    if "position" in m and isinstance(m["position"], dict):
        m["position"] = {k: v for k, v in m["position"].items() if k not in ("snr", "path_len", "raw", "seq")}
    return m


# ---- verwerking van berichten ----------------------------------------------------

async def process(prefix: str, text: str, sender_ts, snr, path_len, simulated: bool = False) -> None:
    pos = ingest.handle(S.db, S.cfg, prefix, text, sender_ts, snr, path_len)
    if not pos:
        if not simulated:
            log.info("bericht van %s genegeerd: %r", prefix, text[:60])
        return
    t = S.db.tracker(pos["tracker_id"])
    if not simulated:
        log.info("positie %s seq=%s state=%s", t["alias"], pos["seq"], pos["state"])
    await S.hub.send({"type": "position", "position": pos, "tracker": tracker_out(t)})
    if pos["lat"] is not None and not pos["suspect"]:
        for ev in geofence.evaluate(S.db, t["id"], pos["lat"], pos["lon"], pos["ts"]):
            ev["tracker"] = t["alias"]
            log.info("geofence: %s %s %s", t["alias"], ev["event"], ev["geofence"])
            await S.hub.send({"type": "geofence", "event": ev})
            if ev["notify_pubkey"] and S.mesh.connected:
                verb = "is binnengekomen in" if ev["event"] == "enter" else "heeft verlaten:"
                try:
                    await send_text(S.mesh, ev["notify_pubkey"], f"MeshTrack: {t['alias']} {verb} {ev['geofence']}")
                except Exception as e:  # noqa: BLE001
                    log.warning("geofence-DM mislukt: %s", e)


async def on_message(prefix: str, text: str, sender_ts, snr, path_len) -> None:
    await process(prefix, text, sender_ts, snr, path_len)


async def on_sim_message(prefix: str, text: str, sender_ts, snr, path_len) -> None:
    await process(prefix, text, sender_ts, snr, path_len, simulated=True)


def make_router() -> Optional[Router]:
    # Routeren op een eigen wegenbestand (Benelux z14) als dat er is, zodat een
    # grotere weergavekaart de routering niet trager of zwaarder maakt.
    p = Path(S.cfg.tiles_dir) / "roads.pmtiles"
    if not p.exists():
        p = Path(S.cfg.tiles_dir) / "basemap.pmtiles"
    if not p.exists():
        log.warning("simulator: %s ontbreekt, routeren onmogelijk", p)
        return None
    return Router(str(p))


async def on_connect() -> None:
    # Elke actieve tracker moet als contact op de companion staan.
    for t in S.db.trackers():
        if t["active"] and t["kind"] == "real":
            try:
                await S.mesh.ensure_contact(t["pubkey"], t["alias"])
            except Exception as e:  # noqa: BLE001
                log.warning("contact %s: %s", t["alias"], e)
    await S.hub.send({"type": "mesh", "mesh": S.mesh.status()})


async def pruner() -> None:
    while True:
        n = S.db.prune(int(time.time()) - S.cfg.retention_days * 86400)
        if n:
            log.info("retentie: %d oude posities verwijderd", n)
        await asyncio.sleep(6 * 3600)


def bootstrap_users() -> None:
    """Eerste start met RBAC: standaardgroepen en een beheerder uit config.yaml."""
    if not S.db.groups():
        for g in rbac.DEFAULT_GROUPS:
            S.db.save_group(None, g)
    # Beheerders hebben altijd alle rechten, ook rechten die in een update bijkwamen.
    for g in S.db.groups():
        if g["name"] == "Beheerders" and set(g["perms"]) != set(PERMS):
            S.db.save_group(g["id"], {**g, "perms": list(PERMS)})
    if S.db.count_users() == 0 and S.cfg.auth_password_hash:
        admins = next(g for g in S.db.groups() if g["name"] == "Beheerders")
        S.db.add_user(S.cfg.auth_user, "Beheerder", S.cfg.auth_password_hash, admins["id"])
        S.db.audit("systeem", "gebruiker aangemaakt", f"{S.cfg.auth_user} (uit config.yaml)")


@asynccontextmanager
async def lifespan(app: FastAPI):
    S.cfg = config.load()
    Path(S.cfg.db_path).parent.mkdir(parents=True, exist_ok=True)
    S.db = DB(S.cfg.db_path)
    bootstrap_users()
    S.hub = Hub()
    S.mesh = MeshLink(S.cfg.mesh_host, S.cfg.mesh_port, S.cfg.keepalive_s, on_message, on_connect)
    S.sims = SimManager(make_router, on_sim_message)
    tasks = [asyncio.create_task(S.mesh.run()), asyncio.create_task(pruner())]
    tiles = Path(S.cfg.tiles_dir)
    if tiles.is_dir():
        app.mount("/tiles", StaticFiles(directory=tiles), name="tiles")
    else:
        log.warning("tegelmap %s ontbreekt: kaart zonder achtergrond", tiles)
    for row in S.db.sims():
        if row["running"]:
            try:
                await S.sims.start(row, S.db.tracker(row["tracker_id"]))
            except Exception as e:  # noqa: BLE001
                log.warning("simulator %s niet gestart: %s", row["alias"], e)
    yield
    await S.sims.stop_all()
    await S.mesh.stop()
    for t in tasks:
        t.cancel()


app = FastAPI(title="MeshTrack", lifespan=lifespan, docs_url=None, redoc_url=None)
app.mount("/static", StaticFiles(directory=STATIC), name="static")


# ---- wie is dit? -------------------------------------------------------------------

_pcache: dict[tuple, tuple[float, Optional[Principal]]] = {}


def _resolve(cookies: dict[str, str]) -> Optional[Principal]:
    sess = auth.check_session(cookies.get(auth.COOKIE), S.cfg.session_secret)
    if sess:
        key = ("u",) + sess
        hit = _pcache.get(key)
        if hit and hit[0] > time.time():
            return hit[1]
        user = S.db.user_by_name(sess[0])
        p = None
        if user and user["active"] and user["session_gen"] == sess[1]:
            group = S.db.group(user["group_id"])
            if group:
                p = rbac.principal_for_user(user, group)
        _pcache[key] = (time.time() + 10, p)
        return p
    tok = cookies.get(SHARE_COOKIE)
    if tok:
        share = S.db.share_by_token(tok)
        if share and (share["expires"] is None or share["expires"] > time.time()):
            return rbac.principal_for_share(share)
    return None


def who(request: Request) -> Principal:
    p = getattr(request.state, "p", None)
    if p is None:
        raise HTTPException(401, "niet ingelogd")
    return p


def need(request: Request, *perms: str) -> Principal:
    """Eén van de rechten volstaat."""
    p = who(request)
    if not any(p.can(x) for x in perms):
        raise HTTPException(403, "geen toegang")
    return p


def audit(p: Principal, action: str, detail: str = "") -> None:
    S.db.audit(p.name, action, detail)


PUBLIC = ("/login", "/api/login", "/static/", "/api/health", "/favicon", "/s/", "/help")
PAGE_PERMS = {"/": ("map.view",), "/admin": ("trackers.manage", "trackers.serial", "sims.manage", "companion.view"),
              "/users": ("users.manage", "share.manage"), "/log": ("log.view",)}


@app.middleware("http")
async def guard(request: Request, call_next):
    path = request.url.path
    request.state.p = _resolve(request.cookies) if S.__dict__.get("db") else None
    p = request.state.p
    if p is None and not path.startswith(PUBLIC):
        if path.startswith("/api/") or path.startswith("/tiles"):
            return JSONResponse({"detail": "niet ingelogd"}, status_code=401)
        return RedirectResponse("/login")
    if p is not None and path in PAGE_PERMS and not any(p.can(x) for x in PAGE_PERMS[path]):
        return RedirectResponse("/" if p.can("map.view") and path != "/" else "/login?geen_toegang=1")
    resp: Response = await call_next(request)
    resp.headers.setdefault("X-Content-Type-Options", "nosniff")
    resp.headers.setdefault("Referrer-Policy", "same-origin")
    resp.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
    if path.startswith("/tiles"):
        resp.headers["Cache-Control"] = "no-cache"
    elif path.startswith("/static") or path in ("/", "/admin", "/login", "/users", "/help", "/log"):
        # Altijd hervalideren (ETag/Last-Modified -> 304): na een update nooit
        # een oude CSS/JS naast nieuwe HTML.
        resp.headers["Cache-Control"] = "no-cache"
    return resp


# ---- pagina's -----------------------------------------------------------------

@app.get("/")
async def index():
    return FileResponse(STATIC / "index.html")


@app.get("/admin")
async def admin():
    return FileResponse(STATIC / "admin.html")


@app.get("/users")
async def users_page():
    return FileResponse(STATIC / "users.html")


@app.get("/help")
async def help_page():
    return FileResponse(STATIC / "help.html")


@app.get("/login")
async def login_page():
    return FileResponse(STATIC / "login.html")


@app.get("/s/{token}")
async def open_share(token: str, request: Request):
    """Deellink: zet een cookie en toon de kaart (zonder login)."""
    share = S.db.share_by_token(token)
    if not share or (share["expires"] is not None and share["expires"] <= time.time()):
        return PlainTextResponse("Deze link bestaat niet (meer) of is verlopen.", status_code=404)
    S.db.touch_share(share["id"])
    resp = RedirectResponse("/")
    secure = request.headers.get("x-forwarded-proto", request.url.scheme) == "https"
    age = int(share["expires"] - time.time()) if share["expires"] else 30 * 86400
    resp.set_cookie(SHARE_COOKIE, token, max_age=age, httponly=True, samesite="lax", secure=secure)
    resp.delete_cookie(auth.COOKIE)
    return resp


# ---- login ----------------------------------------------------------------------

class Login(BaseModel):
    user: str
    password: str


_fails: dict[str, list[float]] = {}


@app.post("/api/login")
async def login(body: Login, request: Request):
    ip = request.headers.get("cf-connecting-ip") or (request.client.host if request.client else "?")
    recent = [t for t in _fails.get(ip, []) if t > time.time() - 300]
    if len(recent) >= 10:
        raise HTTPException(429, "te veel pogingen, probeer over 5 minuten opnieuw")
    user = S.db.user_by_name(body.user.strip())
    if not user or not user["active"] or not auth.verify_password(body.password, user["password_hash"]):
        _fails[ip] = recent + [time.time()]
        await asyncio.sleep(1)
        raise HTTPException(401, "gebruiker of wachtwoord fout")
    _fails.pop(ip, None)
    S.db.update_user(user["id"], last_login=int(time.time()))
    S.db.audit(user["username"], "ingelogd", ip)
    resp = JSONResponse({"ok": True})
    secure = request.headers.get("x-forwarded-proto", request.url.scheme) == "https"
    resp.set_cookie(auth.COOKIE, auth.make_session(user["username"], S.cfg.session_secret, S.cfg.session_days,
                                                   user["session_gen"]),
                    max_age=S.cfg.session_days * 86400, httponly=True, samesite="lax", secure=secure)
    resp.delete_cookie(SHARE_COOKIE)
    return resp


@app.post("/api/logout")
async def logout():
    resp = JSONResponse({"ok": True})
    resp.delete_cookie(auth.COOKIE)
    resp.delete_cookie(SHARE_COOKIE)
    return resp


class PasswordIn(BaseModel):
    old: str
    new: str = Field(max_length=200)


@app.post("/api/me/password")
async def change_password(b: PasswordIn, request: Request):
    p = who(request)
    if p.kind != "user":
        raise HTTPException(403, "alleen voor gebruikers")
    user = S.db.user(p.user_id)
    if not auth.verify_password(b.old, user["password_hash"]):
        raise HTTPException(401, "huidig wachtwoord fout")
    problem = auth.password_problem(b.new)
    if problem:
        raise HTTPException(422, f"nieuw wachtwoord: {problem}")
    S.db.update_user(user["id"], password_hash=auth.hash_password(b.new))
    audit(p, "eigen wachtwoord gewijzigd")
    fresh = S.db.user(user["id"])
    resp = JSONResponse({"ok": True})
    secure = request.headers.get("x-forwarded-proto", request.url.scheme) == "https"
    resp.set_cookie(auth.COOKIE, auth.make_session(fresh["username"], S.cfg.session_secret, S.cfg.session_days,
                                                   fresh["session_gen"]),
                    max_age=S.cfg.session_days * 86400, httponly=True, samesite="lax", secure=secure)
    return resp


# ---- algemeen ------------------------------------------------------------------

@app.get("/api/health")
async def health():
    m = S.mesh.status()
    return {"ok": True, "mesh_connected": m["connected"], "version": VERSION}


@app.get("/api/me")
async def me(request: Request):
    p = who(request)
    return {**p.public(), "perm_labels": {k: v[0] for k, v in PERMS.items()}}


@app.get("/api/status")
async def status(request: Request):
    p = who(request)
    mesh = S.mesh.status()
    if not p.can("companion.view"):
        mesh = {"connected": mesh["connected"], "name": mesh["name"]}
    return {"mesh": mesh, "map": {"center": S.cfg.map_center, "zoom": S.cfg.map_zoom},
            "tiles": (Path(S.cfg.tiles_dir) / "basemap.pmtiles").exists(),
            "stale_after_s": S.cfg.stale_after_s, "version": VERSION}


# ---- trackers ------------------------------------------------------------------

@app.get("/api/trackers")
async def list_trackers(request: Request):
    p = who(request)
    return [tracker_out(t, p) for t in S.db.trackers() if p.sees(t["id"])]


class TrackerIn(BaseModel):
    pubkey: Optional[str] = None
    alias: Optional[str] = Field(None, max_length=40)
    color: Optional[str] = None
    icon: Optional[str] = Field(None, max_length=40)
    notes: Optional[str] = Field(None, max_length=500)
    active: Optional[bool] = None


def _check_fields(b: TrackerIn) -> None:
    if b.color is not None and not COLOR.match(b.color):
        raise HTTPException(422, "kleur moet #rrggbb zijn")
    if b.alias is not None and not b.alias.strip():
        raise HTTPException(422, "alias mag niet leeg zijn")


async def _sync_contact(t: dict[str, Any]) -> str:
    """Contact op de companion zetten of weghalen; geeft een korte status."""
    if t["kind"] != "real":
        return ""
    if not S.mesh.connected:
        return "companion niet verbonden: contact volgt bij de volgende verbinding"
    try:
        if t["active"]:
            await S.mesh.ensure_contact(t["pubkey"], t["alias"])
            return "contact staat op de companion"
        return "inactief: contact niet aangemaakt"
    except Exception as e:  # noqa: BLE001
        return f"contact NIET aangemaakt: {e}"


@app.post("/api/trackers")
async def create_tracker(b: TrackerIn, request: Request):
    p = need(request, "trackers.manage")
    if not b.pubkey or not HEX64.match(b.pubkey.strip()):
        raise HTTPException(422, "pubkey moet 64 hex-tekens zijn")
    if not b.alias:
        raise HTTPException(422, "alias is verplicht")
    _check_fields(b)
    pk = b.pubkey.strip().lower()
    if any(t["pubkey"] == pk for t in S.db.trackers()):
        raise HTTPException(409, "deze tracker bestaat al")
    tid = S.db.add_tracker(pk, b.alias.strip(), b.color or "#e4572e", b.icon or "", b.notes or "",
                           True if b.active is None else b.active)
    t = S.db.tracker(tid)
    note = await _sync_contact(t)
    audit(p, "tracker toegevoegd", f"{t['alias']} ({pk[:12]})")
    await S.hub.send({"type": "tracker", "tracker": tracker_out(t)})
    return {"tracker": tracker_out(t, p), "contact": note}


@app.put("/api/trackers/{tid}")
async def update_tracker(tid: int, b: TrackerIn, request: Request):
    old = S.db.tracker(tid)
    if not old:
        raise HTTPException(404, "onbekende tracker")
    p = need(request, "trackers.manage", "sims.manage" if old["kind"] == "sim" else "trackers.manage")
    _check_fields(b)
    S.db.update_tracker(tid, alias=b.alias.strip() if b.alias else None, color=b.color, icon=b.icon,
                        notes=b.notes, active=b.active)
    t = S.db.tracker(tid)
    note = await _sync_contact(t) if t["active"] and not old["active"] else ""
    audit(p, "tracker gewijzigd", t["alias"])
    await S.hub.send({"type": "tracker", "tracker": tracker_out(t)})
    return {"tracker": tracker_out(t, p), "contact": note}


@app.delete("/api/trackers/{tid}")
async def delete_tracker(tid: int, request: Request, keep_contact: bool = False):
    t = S.db.tracker(tid)
    if not t:
        raise HTTPException(404, "onbekende tracker")
    p = need(request, "trackers.manage", "sims.manage" if t["kind"] == "sim" else "trackers.manage")
    await S.sims.stop(tid)
    S.db.delete_tracker(tid)
    note = ""
    if t["kind"] == "real" and not keep_contact and S.mesh.connected:
        try:
            await S.mesh.remove_contact(t["pubkey"])
            note = "contact verwijderd van de companion"
        except Exception as e:  # noqa: BLE001
            note = f"contact niet verwijderd: {e}"
    audit(p, "tracker verwijderd", t["alias"])
    await S.hub.send({"type": "tracker_deleted", "id": tid})
    return {"ok": True, "contact": note}


def _visible(request: Request, tid: int, perm: str = "map.view") -> tuple[Principal, dict[str, Any]]:
    p = need(request, perm)
    t = S.db.tracker(tid)
    if not t or not p.sees(tid):
        raise HTTPException(404, "onbekende tracker")
    return p, t


@app.get("/api/trackers/{tid}/track")
async def track(tid: int, request: Request, hours: float = 24):
    p, _ = _visible(request, tid, "map.tracks")
    h = p.max_hours(min(max(hours, 0.1), 24 * 90))
    rows = S.db.track(tid, int(time.time() - h * 3600))
    if not p.can("map.details"):
        rows = [{k: v for k, v in r.items() if k not in ("snr", "path_len", "seq")} for r in rows]
    return rows


@app.get("/api/trackers/{tid}/export")
async def export_track(tid: int, request: Request, hours: float = 24, fmt: str = "gpx"):
    p, t = _visible(request, tid, "export")
    h = p.max_hours(min(max(hours, 0.1), 24 * 90))
    rows = S.db.track(tid, int(time.time() - h * 3600))
    safe = re.sub(r"[^A-Za-z0-9_-]+", "_", t["alias"]) or "tracker"
    audit(p, "spoor geëxporteerd", f"{t['alias']} {h:g} u {fmt}")
    if fmt == "csv":
        lines = ["tijd_utc,lat,lon,hoogte_m,snelheid_kmh,koers,batterij,toestand"]
        for r in rows:
            ts = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(r["ts"]))
            lines.append(f"{ts},{r['lat']},{r['lon']},{r['alt'] or ''},{r['spd'] or ''},{r['crs'] or ''},"
                         f"{r['bat'] if r['bat'] is not None else ''},{r['state']}")
        return Response("\n".join(lines) + "\n", media_type="text/csv",
                        headers={"Content-Disposition": f'attachment; filename="{safe}.csv"'})
    from xml.sax.saxutils import escape
    pts = "\n".join(
        f'      <trkpt lat="{r["lat"]}" lon="{r["lon"]}">'
        + (f'<ele>{r["alt"]}</ele>' if r["alt"] is not None else "")
        + f'<time>{time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(r["ts"]))}</time>'
        + (f'<extensions><speed>{(r["spd"] or 0) / 3.6:.2f}</speed></extensions>' if r["spd"] is not None else "")
        + "</trkpt>" for r in rows)
    gpx = (f'<?xml version="1.0" encoding="UTF-8"?>\n<gpx version="1.1" creator="MeshTrack {VERSION}" '
           f'xmlns="http://www.topografix.com/GPX/1/1">\n  <trk>\n    <name>{escape(t["alias"])}</name>\n'
           f'    <trkseg>\n{pts}\n    </trkseg>\n  </trk>\n</gpx>\n')
    return Response(gpx, media_type="application/gpx+xml",
                    headers={"Content-Disposition": f'attachment; filename="{safe}.gpx"'})


class PurgeIn(BaseModel):
    older_than_days: float = Field(ge=0, le=3650)   # 0 = alles


@app.get("/api/trackers/{tid}/data")
async def tracker_data(tid: int, request: Request):
    t = S.db.tracker(tid)
    if not t:
        raise HTTPException(404, "onbekende tracker")
    need(request, "trackers.manage", "sims.manage" if t["kind"] == "sim" else "trackers.manage")
    return S.db.position_stats(tid)


@app.post("/api/trackers/{tid}/purge")
async def purge_tracker(tid: int, b: PurgeIn, request: Request):
    t = S.db.tracker(tid)
    if not t:
        raise HTTPException(404, "onbekende tracker")
    p = need(request, "trackers.manage", "sims.manage" if t["kind"] == "sim" else "trackers.manage")
    cutoff = None if b.older_than_days == 0 else int(time.time() - b.older_than_days * 86400)
    n = S.db.purge_positions(tid, cutoff)
    audit(p, "trackergegevens gewist", f"{t['alias']}: {n} posities "
          + ("(alles)" if cutoff is None else f"ouder dan {b.older_than_days:g} dagen"))
    await S.hub.send({"type": "tracker", "tracker": tracker_out(S.db.tracker(tid))})
    return {"deleted": n, **S.db.position_stats(tid)}


@app.get("/api/companion/contacts")
async def companion_contacts(request: Request):
    need(request, "companion.view", "trackers.manage")
    try:
        return await S.mesh.contacts()
    except ConnectionError as e:
        raise HTTPException(503, str(e))


# ---- simulator -----------------------------------------------------------------

class SimIn(BaseModel):
    alias: Optional[str] = Field(None, max_length=40)
    color: Optional[str] = None
    icon: Optional[str] = Field(None, max_length=40)
    profile: Optional[str] = None
    home_lat: Optional[float] = None
    home_lon: Optional[float] = None
    params: Optional[dict] = None
    loss_pct: Optional[int] = Field(None, ge=0, le=90)
    batt_speed: Optional[float] = Field(None, ge=1, le=100)
    running: Optional[bool] = None
    drive: Optional[dict] = None


def _drive(d: Optional[dict]) -> Optional[dict]:
    """Rijgedrag controleren: speed_pct 30..200, max_kmh 0..200, trip 0.3..80 km,
    roam bool, speeds {wegtype: km/u 3..200}."""
    if d is None:
        return None
    out: dict = {}
    try:
        if d.get("speed_pct") is not None:
            out["speed_pct"] = min(200.0, max(30.0, float(d["speed_pct"])))
        if d.get("max_kmh") is not None:
            out["max_kmh"] = min(200.0, max(0.0, float(d["max_kmh"])))
        lo = float(d["trip_min_km"]) if d.get("trip_min_km") else None
        hi = float(d["trip_max_km"]) if d.get("trip_max_km") else None
        if lo is not None:
            out["trip_min_km"] = min(80.0, max(0.3, lo))
        if hi is not None:
            out["trip_max_km"] = min(80.0, max(out.get("trip_min_km", 0.3), hi))
        out["roam"] = bool(d.get("roam"))
        sp = {}
        for k, v in (d.get("speeds") or {}).items():
            if v not in (None, "", 0) and isinstance(k, str) and len(k) < 30:
                sp[k] = min(200.0, max(3.0, float(v)))
        if sp:
            out["speeds"] = sp
    except (TypeError, ValueError):
        raise HTTPException(422, "ongeldig rijgedrag")
    return out


def _sim_out(row: dict[str, Any]) -> dict[str, Any]:
    st = S.sims.status(row["tracker_id"]) or {"running": False}
    return {**row, "status": st}


@app.get("/api/sims")
async def list_sims(request: Request):
    p = need(request, "sims.manage")
    return [_sim_out(r) for r in S.db.sims() if p.sees(r["tracker_id"])]


@app.get("/api/sims/defaults")
async def sim_defaults(request: Request):
    """Standaardsnelheden per wegtype en ritlengtes, voor het formulier."""
    need(request, "sims.manage")
    from .roadgraph import SPEEDS
    from .sim import PROFILE
    return {p: {"speeds": SPEEDS[p], "trip": PROFILE[p]["trip"]} for p in SPEEDS}


@app.get("/api/sims/routes")
async def sim_routes(request: Request):
    p = need(request, "map.view")
    if not p.can("sims.manage"):
        return {}
    return {tid: r for tid, r in S.sims.routes().items() if p.sees(tid)}


@app.post("/api/sims")
async def create_sim(b: SimIn, request: Request):
    p = need(request, "sims.manage")
    if not b.alias or not b.alias.strip():
        raise HTTPException(422, "alias is verplicht")
    if b.profile not in (None, "car", "bike", "walk"):
        raise HTTPException(422, "profiel: car, bike of walk")
    if b.color and not COLOR.match(b.color):
        raise HTTPException(422, "kleur moet #rrggbb zijn")
    lat = b.home_lat if b.home_lat is not None else S.cfg.map_center[1]
    lon = b.home_lon if b.home_lon is not None else S.cfg.map_center[0]
    w, s_, e, n = S.cfg.region_bbox
    if not (w <= lon <= e and s_ <= lat <= n):
        raise HTTPException(422, "thuisbasis ligt buiten het kaartgebied")
    tid = S.db.add_tracker(new_pubkey(), b.alias.strip(), b.color or "#7c3aed", b.icon or "", "simulator",
                           True, kind="sim")
    S.db.save_sim(tid, b.profile or "car", lat, lon, b.params or {}, 5 if b.loss_pct is None else b.loss_pct,
                  4 if b.batt_speed is None else b.batt_speed, b.running is not False, _drive(b.drive) or {})
    row = S.db.sim(tid)
    if row["running"]:
        try:
            await S.sims.start(row, S.db.tracker(tid))
        except Exception as ex:  # noqa: BLE001
            S.db.set_sim_running(tid, False)
            raise HTTPException(503, str(ex))
    audit(p, "simulator aangemaakt", b.alias.strip())
    await S.hub.send({"type": "tracker", "tracker": tracker_out(S.db.tracker(tid))})
    return _sim_out(S.db.sim(tid))


@app.put("/api/sims/{tid}")
async def update_sim(tid: int, b: SimIn, request: Request):
    p = need(request, "sims.manage")
    row = S.db.sim(tid)
    if not row or not p.sees(tid):
        raise HTTPException(404, "onbekende simulator")
    if b.profile not in (None, "car", "bike", "walk"):
        raise HTTPException(422, "profiel: car, bike of walk")
    if b.color and not COLOR.match(b.color):
        raise HTTPException(422, "kleur moet #rrggbb zijn")
    S.db.update_tracker(tid, alias=b.alias.strip() if b.alias else None, color=b.color, icon=b.icon)
    S.db.save_sim(tid, b.profile or row["profile"],
                  row["home_lat"] if b.home_lat is None else b.home_lat,
                  row["home_lon"] if b.home_lon is None else b.home_lon,
                  row["params"] if b.params is None else b.params,
                  row["loss_pct"] if b.loss_pct is None else b.loss_pct,
                  row["batt_speed"] if b.batt_speed is None else b.batt_speed,
                  row["running"] if b.running is None else b.running,
                  row["drive"] if b.drive is None else _drive(b.drive))
    row = S.db.sim(tid)
    if row["running"]:
        await S.sims.start(row, S.db.tracker(tid))     # herstart met nieuwe instellingen
    else:
        await S.sims.stop(tid)
    audit(p, "simulator gewijzigd", f"{row['alias']} ({'rijdt' if row['running'] else 'gestopt'})")
    await S.hub.send({"type": "tracker", "tracker": tracker_out(S.db.tracker(tid))})
    return _sim_out(row)


# ---- geofences -----------------------------------------------------------------

@app.get("/api/geofences")
async def list_geofences(request: Request):
    need(request, "zones.view")
    return S.db.geofences()


@app.post("/api/geofences")
async def create_geofence(body: dict, request: Request):
    p = need(request, "zones.manage")
    try:
        g = geofence.validate(body)
    except ValueError as e:
        raise HTTPException(422, str(e))
    gid = S.db.add_geofence(g)
    audit(p, "zone aangemaakt", g["name"])
    await S.hub.send({"type": "geofences"})
    return S.db.geofence(gid)


@app.put("/api/geofences/{gid}")
async def update_geofence(gid: int, body: dict, request: Request):
    p = need(request, "zones.manage")
    if not S.db.geofence(gid):
        raise HTTPException(404, "onbekende zone")
    try:
        g = geofence.validate(body)
    except ValueError as e:
        raise HTTPException(422, str(e))
    S.db.update_geofence(gid, g)
    audit(p, "zone gewijzigd", g["name"])
    await S.hub.send({"type": "geofences"})
    return S.db.geofence(gid)


@app.delete("/api/geofences/{gid}")
async def delete_geofence(gid: int, request: Request):
    p = need(request, "zones.manage")
    g = S.db.geofence(gid)
    S.db.delete_geofence(gid)
    audit(p, "zone verwijderd", g["name"] if g else str(gid))
    await S.hub.send({"type": "geofences"})
    return {"ok": True}


@app.get("/api/geofence-events")
async def geofence_events(request: Request, limit: int = 50):
    p = need(request, "zones.view")
    return [e for e in S.db.geofence_events(min(max(limit, 1), 500)) if p.sees(e["tracker_id"])]


@app.get("/api/mesh/nodes")
async def mesh_nodes(request: Request):
    p = need(request, "map.nodes")
    try:
        comp = await S.mesh.nodes()
    except ConnectionError:
        comp = []
    out = await asyncio.to_thread(nodes.merged, S.cfg.openhop_db, comp)
    if not p.can("map.details"):
        out = [{k: v for k, v in n.items() if k not in ("key", "rssi", "snr")} for n in out]
    return out


EVENT_TYPES = {
    "M": "positie (beweging)", "W": "wakker door beweging", "S": "stilgevallen", "H": "heartbeat",
    "N": "geen GPS-fix", "E": "SOS", "P": "handmatig verstuurd", "B": "moduswissel",
    "zone_in": "zone binnen", "zone_out": "zone buiten", "bat_low": "batterij onder 20 %",
    "suspect": "verdachte positie",
}


@app.get("/api/events")
async def events(request: Request, tracker: str = "", types: str = "", hours: float = 24, since: int = 0,
                 until: int = 0, limit: int = 500):
    p = need(request, "log.view")
    now = int(time.time())
    until = until or now
    since = since or int(until - p.max_hours(min(max(hours, 0.1), 24 * 365)) * 3600)
    if p.history_hours > 0:
        since = max(since, now - p.history_hours * 3600)
    wanted = [x for x in types.split(",") if x in EVENT_TYPES] if types else list(EVENT_TYPES)
    ids = [int(x) for x in tracker.split(",") if x.isdigit()] if tracker else None
    if p.tracker_ids is not None:
        ids = [i for i in (ids if ids is not None else p.tracker_ids) if p.sees(i)]
    if not p.can("zones.view"):
        wanted = [w for w in wanted if not w.startswith("zone")]
    rows = S.db.events(ids, [w for w in wanted if len(w) == 1], since, until, "zone_in" in wanted or "zone_out" in wanted,
                       "bat_low" in wanted, "suspect" in wanted, min(max(limit, 1), 5000))
    if "zone_in" not in wanted:
        rows = [r for r in rows if r["type"] != "zone_in"]
    if "zone_out" not in wanted:
        rows = [r for r in rows if r["type"] != "zone_out"]
    if not p.can("map.details"):
        rows = [{k: v for k, v in r.items() if k not in ("snr", "path_len", "hdop")} for r in rows]
    return {"types": EVENT_TYPES, "since": since, "until": until, "events": rows}


@app.get("/log")
async def log_page():
    return FileResponse(STATIC / "log.html")


@app.get("/api/unknown")
async def unknown(request: Request):
    need(request, "trackers.manage")
    return S.db.unknown()


# ---- gebruikers, groepen, deellinks, audit -------------------------------------

class GroupIn(BaseModel):
    name: str = Field(min_length=1, max_length=40)
    description: str = Field("", max_length=200)
    perms: list[str]
    all_trackers: bool = True
    trackers: list[int] = []
    history_hours: int = Field(0, ge=0, le=24 * 365)


def _admins_left(excluding_user: Optional[int] = None, group_override: Optional[tuple[int, list[str]]] = None) -> int:
    """Aantal actieve gebruikers met users.manage (om buitensluiten te vermijden)."""
    groups = {g["id"]: g for g in S.db.groups()}
    if group_override:
        groups[group_override[0]] = {**groups[group_override[0]], "perms": group_override[1]}
    n = 0
    for u in S.db.users():
        if u["id"] == excluding_user or not u["active"]:
            continue
        if "users.manage" in groups.get(u["group_id"], {}).get("perms", []):
            n += 1
    return n


@app.get("/api/groups")
async def list_groups(request: Request):
    need(request, "users.manage", "share.manage")
    return {"groups": S.db.groups(), "perms": [{"id": k, "label": v[0], "help": v[1]} for k, v in PERMS.items()]}


@app.post("/api/groups")
async def create_group(b: GroupIn, request: Request):
    p = need(request, "users.manage")
    if any(g["name"].lower() == b.name.strip().lower() for g in S.db.groups()):
        raise HTTPException(409, "die groep bestaat al")
    gid = S.db.save_group(None, {**b.model_dump(), "name": b.name.strip(), "perms": [x for x in b.perms if x in PERMS]})
    audit(p, "groep aangemaakt", b.name)
    return S.db.group(gid)


@app.put("/api/groups/{gid}")
async def update_group(gid: int, b: GroupIn, request: Request):
    p = need(request, "users.manage")
    if not S.db.group(gid):
        raise HTTPException(404, "onbekende groep")
    perms = [x for x in b.perms if x in PERMS]
    if _admins_left(group_override=(gid, perms)) == 0:
        raise HTTPException(409, "dan heeft niemand nog gebruikersbeheer; dat kan niet")
    S.db.save_group(gid, {**b.model_dump(), "name": b.name.strip(), "perms": perms})
    _pcache.clear()
    audit(p, "groep gewijzigd", f"{b.name}: {', '.join(perms)}")
    return S.db.group(gid)


@app.delete("/api/groups/{gid}")
async def delete_group(gid: int, request: Request):
    p = need(request, "users.manage")
    g = S.db.group(gid)
    if not g:
        raise HTTPException(404, "onbekende groep")
    if g["members"]:
        raise HTTPException(409, "de groep heeft nog leden")
    S.db.delete_group(gid)
    audit(p, "groep verwijderd", g["name"])
    return {"ok": True}


class UserIn(BaseModel):
    username: Optional[str] = None
    display_name: Optional[str] = Field(None, max_length=60)
    password: Optional[str] = Field(None, max_length=200)
    group_id: Optional[int] = None
    active: Optional[bool] = None


@app.get("/api/users")
async def list_users(request: Request):
    need(request, "users.manage")
    return S.db.users()


@app.post("/api/users")
async def create_user(b: UserIn, request: Request):
    p = need(request, "users.manage")
    if not b.username or not USERNAME.match(b.username):
        raise HTTPException(422, "gebruikersnaam: 2-32 tekens, letters, cijfers, . _ -")
    if S.db.user_by_name(b.username):
        raise HTTPException(409, "die gebruiker bestaat al")
    if not b.group_id or not S.db.group(b.group_id):
        raise HTTPException(422, "kies een groep")
    problem = auth.password_problem(b.password or "")
    if problem:
        raise HTTPException(422, f"wachtwoord: {problem}")
    uid = S.db.add_user(b.username, (b.display_name or "").strip(), auth.hash_password(b.password), b.group_id,
                        b.active is not False)
    audit(p, "gebruiker aangemaakt", f"{b.username} in {S.db.group(b.group_id)['name']}")
    return next(u for u in S.db.users() if u["id"] == uid)


@app.put("/api/users/{uid}")
async def update_user(uid: int, b: UserIn, request: Request):
    p = need(request, "users.manage")
    u = S.db.user(uid)
    if not u:
        raise HTTPException(404, "onbekende gebruiker")
    if b.group_id is not None and not S.db.group(b.group_id):
        raise HTTPException(422, "onbekende groep")
    losing = (b.active is False) or (b.group_id is not None and "users.manage" not in S.db.group(b.group_id)["perms"])
    if losing and _admins_left(excluding_user=uid) == 0:
        raise HTTPException(409, "dit is de laatste beheerder; dat kan niet")
    pw_hash = None
    if b.password:
        problem = auth.password_problem(b.password)
        if problem:
            raise HTTPException(422, f"wachtwoord: {problem}")
        pw_hash = auth.hash_password(b.password)
    S.db.update_user(uid, display_name=b.display_name, group_id=b.group_id, active=b.active, password_hash=pw_hash)
    _pcache.clear()
    what = [x for x, v in (("naam", b.display_name), ("groep", b.group_id), ("actief", b.active),
                           ("wachtwoord", b.password)) if v is not None]
    audit(p, "gebruiker gewijzigd", f"{u['username']}: {', '.join(what)}")
    return next(x for x in S.db.users() if x["id"] == uid)


@app.delete("/api/users/{uid}")
async def delete_user(uid: int, request: Request):
    p = need(request, "users.manage")
    u = S.db.user(uid)
    if not u:
        raise HTTPException(404, "onbekende gebruiker")
    if p.user_id == uid:
        raise HTTPException(409, "je kan jezelf niet verwijderen")
    if _admins_left(excluding_user=uid) == 0:
        raise HTTPException(409, "dit is de laatste beheerder; dat kan niet")
    S.db.delete_user(uid)
    _pcache.clear()
    audit(p, "gebruiker verwijderd", u["username"])
    return {"ok": True}


class ShareIn(BaseModel):
    name: str = Field(min_length=1, max_length=60)
    trackers: list[int]
    hours: int = Field(12, ge=1, le=24 * 30)
    sidebar: bool = False
    valid_hours: int = Field(24, ge=0, le=24 * 365)   # 0 = nooit verlopen


@app.get("/api/shares")
async def list_shares(request: Request):
    p = need(request, "share.manage")
    base = str(request.base_url).rstrip("/")
    return [{**s, "url": f"{base}/s/{s['token']}"} for s in S.db.shares()
            if p.tracker_ids is None or set(s["trackers"]) <= p.tracker_ids]


@app.post("/api/shares")
async def create_share(b: ShareIn, request: Request):
    p = need(request, "share.manage")
    ids = [t for t in b.trackers if S.db.tracker(t) and p.sees(t)]
    if not ids:
        raise HTTPException(422, "kies minstens één tracker")
    token = secrets.token_urlsafe(18)
    expires = int(time.time() + b.valid_hours * 3600) if b.valid_hours else None
    S.db.add_share(token, b.name.strip(), ids, b.hours, b.sidebar, expires, p.name)
    audit(p, "deellink gemaakt", f"{b.name} ({len(ids)} trackers, {b.valid_hours or 'onbeperkt'} u geldig)")
    base = str(request.base_url).rstrip("/")
    return {"url": f"{base}/s/{token}"}


@app.delete("/api/shares/{sid}")
async def delete_share(sid: int, request: Request):
    p = need(request, "share.manage")
    s = next((x for x in S.db.shares() if x["id"] == sid), None)
    if not s:
        raise HTTPException(404, "onbekende deellink")
    S.db.delete_share(sid)
    audit(p, "deellink ingetrokken", s["name"])
    return {"ok": True}


@app.get("/api/audit")
async def audit_log(request: Request, limit: int = 200):
    need(request, "users.manage")
    return S.db.audit_log(min(max(limit, 1), 1000))


# ---- live ------------------------------------------------------------------------

@app.websocket("/ws")
async def ws(websocket: WebSocket):
    p = _resolve(websocket.cookies)
    if p is None or not p.can("map.view"):
        await websocket.close(code=4401)
        return
    await websocket.accept()
    S.hub.clients[websocket] = p
    try:
        mesh = S.mesh.status()
        if not p.can("companion.view"):
            mesh = {"connected": mesh["connected"], "name": mesh["name"]}
        await websocket.send_text(json.dumps({"type": "mesh", "mesh": mesh}))
        while True:
            await websocket.receive_text()   # alleen om disconnects te zien
    except WebSocketDisconnect:
        pass
    finally:
        S.hub.clients.pop(websocket, None)


def main() -> None:
    import uvicorn
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    cfg = config.load()
    uvicorn.run("meshtrack.main:app", host=cfg.http_host, port=cfg.http_port, proxy_headers=True,
                forwarded_allow_ips="127.0.0.1", log_level="info")


if __name__ == "__main__":
    main()
