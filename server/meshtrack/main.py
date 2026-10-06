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

from . import auth, config, ingest
from .db import DB
from .mesh_client import MeshLink

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


S = State()


def tracker_out(t: dict[str, Any]) -> dict[str, Any]:
    now = int(time.time())
    out = dict(t)
    out["stale"] = not t["last_rx"] or now - t["last_rx"] > S.cfg.stale_after_s
    return out


async def on_message(prefix: str, text: str, sender_ts, snr, path_len) -> None:
    pos = ingest.handle(S.db, S.cfg, prefix, text, sender_ts, snr, path_len)
    if pos:
        t = S.db.tracker(pos["tracker_id"])
        log.info("positie %s seq=%s state=%s", t["alias"], pos["seq"], pos["state"])
        await S.hub.send({"type": "position", "position": pos, "tracker": tracker_out(t)})
    else:
        log.info("bericht van %s genegeerd: %r", prefix, text[:60])


async def on_connect() -> None:
    # Elke actieve tracker moet als contact op de companion staan.
    for t in S.db.trackers():
        if t["active"]:
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
    tasks = [asyncio.create_task(S.mesh.run()), asyncio.create_task(pruner())]
    tiles = Path(S.cfg.tiles_dir)
    if tiles.is_dir():
        app.mount("/tiles", StaticFiles(directory=tiles), name="tiles")
    else:
        log.warning("tegelmap %s ontbreekt: kaart zonder achtergrond", tiles)
    yield
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
    icon: Optional[str] = Field(None, max_length=8)
    notes: Optional[str] = Field(None, max_length=500)
    active: Optional[bool] = None


def _check_fields(b: TrackerIn) -> None:
    if b.color is not None and not COLOR.match(b.color):
        raise HTTPException(422, "kleur moet #rrggbb zijn")
    if b.alias is not None and not b.alias.strip():
        raise HTTPException(422, "alias mag niet leeg zijn")


async def _sync_contact(t: dict[str, Any]) -> str:
    """Contact op de companion zetten of weghalen; geeft een korte status."""
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
    S.db.delete_tracker(tid)
    note = ""
    if not keep_contact and S.mesh.connected:
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
