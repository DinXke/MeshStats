"""MeshTrack-server: FastAPI-app.

Start:  MESHTRACK_CONFIG=/etc/meshtrack/config.yaml python -m meshtrack.main
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Optional

from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import auth, config, geofence, ingest
from .db import DB
from .mesh_client import MeshLink, send_text
from .roadgraph import Router
from .sim import SimManager, new_pubkey

log = logging.getLogger("meshtrack")
STATIC = Path(__file__).resolve().parent.parent / "static"
HEX64 = re.compile(r"^[0-9a-fA-F]{64}$")
COLOR = re.compile(r"^#[0-9a-fA-F]{6}$")


# ---- live-updates -----------------------------------------------------------

class Hub:
    def __init__(self) -> None:
        self.clients: set[WebSocket] = set()

    async def send(self, msg: dict[str, Any]) -> None:
        data = json.dumps(msg)
        for ws in list(self.clients):
            try:
                await ws.send_text(data)
            except Exception:  # noqa: BLE001
                self.clients.discard(ws)


class State:
    cfg: config.Config
    db: DB
    mesh: MeshLink
    hub: Hub
    sims: SimManager


S = State()


def tracker_out(t: dict[str, Any]) -> dict[str, Any]:
    now = int(time.time())
    out = dict(t)
    out["stale"] = not t["last_rx"] or now - t["last_rx"] > S.cfg.stale_after_s
    return out


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


@asynccontextmanager
async def lifespan(app: FastAPI):
    S.cfg = config.load()
    Path(S.cfg.db_path).parent.mkdir(parents=True, exist_ok=True)
    S.db = DB(S.cfg.db_path)
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

PUBLIC = ("/login", "/api/login", "/static/login", "/static/style.css", "/api/health", "/favicon")


@app.middleware("http")
async def guard(request: Request, call_next):
    path = request.url.path
    user = auth.check_session(request.cookies.get(auth.COOKIE), S.cfg.session_secret)
    if not user and not path.startswith(PUBLIC):
        if path.startswith("/api/"):
            return JSONResponse({"detail": "niet ingelogd"}, status_code=401)
        return RedirectResponse("/login")
    resp: Response = await call_next(request)
    resp.headers.setdefault("X-Content-Type-Options", "nosniff")
    resp.headers.setdefault("Referrer-Policy", "same-origin")
    if path.startswith("/tiles"):
        resp.headers["Cache-Control"] = "no-cache"
    elif path.startswith("/static") or path in ("/", "/admin", "/login"):
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


@app.get("/login")
async def login_page():
    return FileResponse(STATIC / "login.html")


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
    if body.user != S.cfg.auth_user or not auth.verify_password(body.password, S.cfg.auth_password_hash):
        _fails[ip] = recent + [time.time()]
        await asyncio.sleep(1)
        raise HTTPException(401, "gebruiker of wachtwoord fout")
    _fails.pop(ip, None)
    resp = JSONResponse({"ok": True})
    secure = request.headers.get("x-forwarded-proto", request.url.scheme) == "https"
    resp.set_cookie(auth.COOKIE, auth.make_session(body.user, S.cfg.session_secret, S.cfg.session_days),
                    max_age=S.cfg.session_days * 86400, httponly=True, samesite="lax", secure=secure)
    return resp


@app.post("/api/logout")
async def logout():
    resp = JSONResponse({"ok": True})
    resp.delete_cookie(auth.COOKIE)
    return resp


# ---- API ----------------------------------------------------------------------

@app.get("/api/health")
async def health():
    m = S.mesh.status()
    return {"ok": True, "mesh_connected": m["connected"]}


@app.get("/api/status")
async def status():
    return {"mesh": S.mesh.status(), "map": {"center": S.cfg.map_center, "zoom": S.cfg.map_zoom},
            "tiles": (Path(S.cfg.tiles_dir) / "basemap.pmtiles").exists(),
            "stale_after_s": S.cfg.stale_after_s}


@app.get("/api/trackers")
async def list_trackers():
    return [tracker_out(t) for t in S.db.trackers()]


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
async def create_tracker(b: TrackerIn):
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
    await S.hub.send({"type": "tracker", "tracker": tracker_out(t)})
    return {"tracker": tracker_out(t), "contact": note}


@app.put("/api/trackers/{tid}")
async def update_tracker(tid: int, b: TrackerIn):
    old = S.db.tracker(tid)
    if not old:
        raise HTTPException(404, "onbekende tracker")
    _check_fields(b)
    S.db.update_tracker(tid, alias=b.alias.strip() if b.alias else None, color=b.color, icon=b.icon,
                        notes=b.notes, active=b.active)
    t = S.db.tracker(tid)
    note = await _sync_contact(t) if t["active"] and not old["active"] else ""
    await S.hub.send({"type": "tracker", "tracker": tracker_out(t)})
    return {"tracker": tracker_out(t), "contact": note}


@app.delete("/api/trackers/{tid}")
async def delete_tracker(tid: int, keep_contact: bool = False):
    t = S.db.tracker(tid)
    if not t:
        raise HTTPException(404, "onbekende tracker")
    await S.sims.stop(tid)
    S.db.delete_tracker(tid)
    note = ""
    if t["kind"] == "real" and not keep_contact and S.mesh.connected:
        try:
            await S.mesh.remove_contact(t["pubkey"])
            note = "contact verwijderd van de companion"
        except Exception as e:  # noqa: BLE001
            note = f"contact niet verwijderd: {e}"
    await S.hub.send({"type": "tracker_deleted", "id": tid})
    return {"ok": True, "contact": note}


@app.get("/api/trackers/{tid}/track")
async def track(tid: int, hours: float = 24):
    if not S.db.tracker(tid):
        raise HTTPException(404, "onbekende tracker")
    since = int(time.time() - min(max(hours, 0.1), 24 * 90) * 3600)
    return S.db.track(tid, since)


@app.get("/api/companion/contacts")
async def companion_contacts():
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


def _sim_out(row: dict[str, Any]) -> dict[str, Any]:
    st = S.sims.status(row["tracker_id"]) or {"running": False}
    return {**row, "status": st}


@app.get("/api/sims")
async def list_sims():
    return [_sim_out(r) for r in S.db.sims()]


@app.get("/api/sims/routes")
async def sim_routes():
    return S.sims.routes()


@app.post("/api/sims")
async def create_sim(b: SimIn):
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
                  4 if b.batt_speed is None else b.batt_speed, b.running is not False)
    row = S.db.sim(tid)
    if row["running"]:
        try:
            await S.sims.start(row, S.db.tracker(tid))
        except Exception as ex:  # noqa: BLE001
            S.db.set_sim_running(tid, False)
            raise HTTPException(503, str(ex))
    await S.hub.send({"type": "tracker", "tracker": tracker_out(S.db.tracker(tid))})
    return _sim_out(S.db.sim(tid))


@app.put("/api/sims/{tid}")
async def update_sim(tid: int, b: SimIn):
    row = S.db.sim(tid)
    if not row:
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
                  row["running"] if b.running is None else b.running)
    row = S.db.sim(tid)
    if row["running"]:
        await S.sims.start(row, S.db.tracker(tid))     # herstart met nieuwe instellingen
    else:
        await S.sims.stop(tid)
    await S.hub.send({"type": "tracker", "tracker": tracker_out(S.db.tracker(tid))})
    return _sim_out(row)


# ---- geofences -----------------------------------------------------------------

@app.get("/api/geofences")
async def list_geofences():
    return S.db.geofences()


@app.post("/api/geofences")
async def create_geofence(body: dict):
    try:
        g = geofence.validate(body)
    except ValueError as e:
        raise HTTPException(422, str(e))
    gid = S.db.add_geofence(g)
    await S.hub.send({"type": "geofences"})
    return S.db.geofence(gid)


@app.put("/api/geofences/{gid}")
async def update_geofence(gid: int, body: dict):
    if not S.db.geofence(gid):
        raise HTTPException(404, "onbekende zone")
    try:
        g = geofence.validate(body)
    except ValueError as e:
        raise HTTPException(422, str(e))
    S.db.update_geofence(gid, g)
    await S.hub.send({"type": "geofences"})
    return S.db.geofence(gid)


@app.delete("/api/geofences/{gid}")
async def delete_geofence(gid: int):
    S.db.delete_geofence(gid)
    await S.hub.send({"type": "geofences"})
    return {"ok": True}


@app.get("/api/geofence-events")
async def geofence_events(limit: int = 50):
    return S.db.geofence_events(min(max(limit, 1), 500))


@app.get("/api/mesh/nodes")
async def mesh_nodes():
    try:
        return await S.mesh.nodes()
    except ConnectionError as e:
        raise HTTPException(503, str(e))


@app.get("/api/unknown")
async def unknown():
    return S.db.unknown()


@app.websocket("/ws")
async def ws(websocket: WebSocket):
    if not auth.check_session(websocket.cookies.get(auth.COOKIE), S.cfg.session_secret):
        await websocket.close(code=4401)
        return
    await websocket.accept()
    S.hub.clients.add(websocket)
    try:
        await websocket.send_text(json.dumps({"type": "mesh", "mesh": S.mesh.status()}))
        while True:
            await websocket.receive_text()   # alleen om disconnects te zien
    except WebSocketDisconnect:
        pass
    finally:
        S.hub.clients.discard(websocket)


def main() -> None:
    import uvicorn
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    cfg = config.load()
    uvicorn.run("meshtrack.main:app", host=cfg.http_host, port=cfg.http_port, proxy_headers=True,
                forwarded_allow_ips="127.0.0.1", log_level="info")


if __name__ == "__main__":
    main()
