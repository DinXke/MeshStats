"""Beheerders-backend: login, repeaterbeheer, API-tokens, wachtwoord."""
import time

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from . import auth, config, db, metrics, mqtt_ingest
from .templating import templates

router = APIRouter(prefix="/admin")


def current_user(request: Request) -> str | None:
    return auth.read_session(request.cookies.get(auth.SESSION_COOKIE))


def require_login(request: Request) -> str:
    user = current_user(request)
    if not user:
        raise HTTPException(status_code=303, headers={"Location": "/admin/login"})
    return user


def check_csrf(request: Request, csrf: str):
    cookie = request.cookies.get(auth.SESSION_COOKIE, "")
    if not cookie or csrf != auth.csrf_token(cookie):
        raise HTTPException(403, "CSRF-controle mislukt")


def _secure(request: Request) -> bool:
    return request.headers.get("x-forwarded-proto", request.url.scheme) == "https"


@router.get("/login", response_class=HTMLResponse)
def login_page(request: Request):
    return templates.TemplateResponse(request, "admin/login.html", {
        "site_name": config.SITE_NAME, "error": None,
    })


@router.post("/login")
def login(request: Request, username: str = Form(...), password: str = Form(...)):
    row = db.qone("SELECT * FROM admins WHERE username=?", (username.strip(),))
    if not row or not auth.verify_password(password, row["pw_hash"]):
        time.sleep(1)  # vertraag brute force
        return templates.TemplateResponse(request, "admin/login.html", {
            "site_name": config.SITE_NAME, "error": "Ongeldige inloggegevens",
        }, status_code=401)
    resp = RedirectResponse("/admin", status_code=303)
    resp.set_cookie(
        auth.SESSION_COOKIE, auth.make_session(row["username"]),
        max_age=auth.SESSION_TTL, httponly=True, samesite="lax", secure=_secure(request),
    )
    return resp


@router.get("/logout")
def logout():
    resp = RedirectResponse("/", status_code=303)
    resp.delete_cookie(auth.SESSION_COOKIE)
    return resp


@router.get("", response_class=HTMLResponse)
def dashboard(request: Request):
    user = require_login(request)
    repeaters = db.q("SELECT * FROM repeaters ORDER BY sort_order, name")
    tokens = db.q("SELECT * FROM tokens WHERE revoked=0 ORDER BY created_at")
    layout = metrics.parse_layout(db.get_setting("layout"))
    # nieuw token éénmalig tonen via kortlevende cookie (niet via de URL)
    new_token = request.cookies.get("mcs_new_token")
    resp = templates.TemplateResponse(request, "admin/dashboard.html", {
        "site_name": config.SITE_NAME, "user": user,
        "repeaters": repeaters, "tokens": tokens,
        "csrf": auth.csrf_token(request.cookies.get(auth.SESSION_COOKIE, "")),
        "new_token": new_token,
        "mqtt": mqtt_ingest.status(),
        "settings": {
            "heartbeat_min": db.setting_int("heartbeat_min", config.HEARTBEAT_MIN),
            "retention_days": db.setting_int("retention_days", config.RETENTION_DAYS),
            "history_ranges": ",".join(str(h) for h in metrics.parse_ranges(db.get_setting("history_ranges"))),
        },
        "layout": layout,
        "block_names": metrics.BLOCK_NAMES,
    })
    if new_token:
        resp.delete_cookie("mcs_new_token")
    return resp


@router.post("/settings")
def save_settings(request: Request, heartbeat_min: int = Form(...),
                  retention_days: int = Form(...), history_ranges: str = Form(...),
                  csrf: str = Form(...)):
    require_login(request)
    check_csrf(request, csrf)
    db.set_setting("heartbeat_min", str(max(1, min(1440, heartbeat_min))))
    db.set_setting("retention_days", str(max(1, min(3650, retention_days))))
    db.set_setting("history_ranges", ",".join(str(h) for h in metrics.parse_ranges(history_ranges)))
    db.prune()  # nieuwe bewaartermijn meteen toepassen
    return RedirectResponse("/admin", status_code=303)


@router.post("/layout")
def save_layout(request: Request, layout: str = Form(...), csrf: str = Form(...)):
    require_login(request)
    check_csrf(request, csrf)
    import json as _json
    validated = metrics.parse_layout(layout)
    db.set_setting("layout", _json.dumps(validated))
    return RedirectResponse("/admin", status_code=303)


@router.post("/repeaters/{rid}/refresh")
def refresh_repeater(request: Request, rid: int, csrf: str = Form(...)):
    """Manual status update: queue a request for the Home Assistant integration."""
    require_login(request)
    check_csrf(request, csrf)
    row = db.qone("SELECT slug, pubkey_prefix FROM repeaters WHERE id=?", (rid,))
    if not row:
        raise HTTPException(404, "Onbekende repeater")
    db.request_refresh(row["pubkey_prefix"])
    return RedirectResponse(f"/r/{row['slug']}?refresh=1", status_code=303)


@router.get("/repeaters/{rid}/settings", response_class=HTMLResponse)
def repeater_settings_page(request: Request, rid: int):
    """Readonly-overzicht van de CLI-instellingen van een repeater."""
    user = require_login(request)
    rep = db.qone("SELECT * FROM repeaters WHERE id=?", (rid,))
    if not rep:
        raise HTTPException(404, "Onbekende repeater")
    return templates.TemplateResponse(request, "admin/repeater_settings.html", {
        "site_name": config.SITE_NAME, "user": user, "rep": rep,
        "settings_rows": db.cli_settings_for(rid),
        "cli_params": db.get_setting("cli_params", db.DEFAULT_CLI_PARAMS),
        "csrf": auth.csrf_token(request.cookies.get(auth.SESSION_COOKIE, "")),
        "requested": request.query_params.get("requested") == "1",
    })


@router.post("/repeaters/{rid}/settings/refresh")
def repeater_settings_refresh(request: Request, rid: int, csrf: str = Form(...)):
    """Vraag de CLI-instellingen op via de HA-integratie (LoRa, duurt 1-2 min)."""
    require_login(request)
    check_csrf(request, csrf)
    rep = db.qone("SELECT pubkey_prefix FROM repeaters WHERE id=?", (rid,))
    if not rep:
        raise HTTPException(404, "Onbekende repeater")
    raw = db.get_setting("cli_params", db.DEFAULT_CLI_PARAMS)
    params = [p.strip() for p in raw.replace(";", ",").split(",") if p.strip()][:40]
    db.request_settings(rep["pubkey_prefix"], params)
    return RedirectResponse(f"/admin/repeaters/{rid}/settings?requested=1", status_code=303)


@router.post("/cli_params")
def save_cli_params(request: Request, cli_params: str = Form(...),
                    rid: int = Form(...), csrf: str = Form(...)):
    require_login(request)
    check_csrf(request, csrf)
    cleaned = ",".join(p.strip() for p in cli_params.replace(";", ",").split(",") if p.strip())
    db.set_setting("cli_params", cleaned or db.DEFAULT_CLI_PARAMS)
    return RedirectResponse(f"/admin/repeaters/{rid}/settings", status_code=303)


@router.post("/repeaters/{rid}/toggle")
def toggle_repeater(request: Request, rid: int, csrf: str = Form(...)):
    require_login(request)
    check_csrf(request, csrf)
    db.execute("UPDATE repeaters SET is_public = 1 - is_public WHERE id=?", (rid,))
    return RedirectResponse("/admin", status_code=303)


@router.post("/repeaters/{rid}/rename")
def rename_repeater(request: Request, rid: int, name: str = Form(...), csrf: str = Form(...)):
    require_login(request)
    check_csrf(request, csrf)
    name = name.strip()
    if name:
        db.execute("UPDATE repeaters SET name=? WHERE id=?", (name, rid))
    return RedirectResponse("/admin", status_code=303)


@router.post("/repeaters/{rid}/delete")
def delete_repeater(request: Request, rid: int, csrf: str = Form(...)):
    require_login(request)
    check_csrf(request, csrf)
    db.execute("DELETE FROM samples WHERE repeater_id=?", (rid,))
    db.execute("DELETE FROM latest WHERE repeater_id=?", (rid,))
    db.execute("DELETE FROM neighbors WHERE repeater_id=?", (rid,))
    db.execute("DELETE FROM repeaters WHERE id=?", (rid,))
    return RedirectResponse("/admin", status_code=303)


@router.post("/tokens")
def create_token(request: Request, name: str = Form(...), csrf: str = Form(...)):
    require_login(request)
    check_csrf(request, csrf)
    token = auth.create_token(name.strip() or "token")
    resp = RedirectResponse("/admin", status_code=303)
    resp.set_cookie("mcs_new_token", token, max_age=60, httponly=True,
                    samesite="lax", secure=_secure(request))
    return resp


@router.post("/tokens/{tid}/revoke")
def revoke_token(request: Request, tid: int, csrf: str = Form(...)):
    require_login(request)
    check_csrf(request, csrf)
    db.execute("UPDATE tokens SET revoked=1 WHERE id=?", (tid,))
    return RedirectResponse("/admin", status_code=303)


@router.post("/password")
def change_password(request: Request, current: str = Form(...),
                    new: str = Form(...), csrf: str = Form(...)):
    user = require_login(request)
    check_csrf(request, csrf)
    row = db.qone("SELECT * FROM admins WHERE username=?", (user,))
    if not row or not auth.verify_password(current, row["pw_hash"]):
        raise HTTPException(403, "Huidig wachtwoord onjuist")
    if len(new) < 8:
        raise HTTPException(422, "Nieuw wachtwoord moet minstens 8 tekens zijn")
    db.execute("UPDATE admins SET pw_hash=? WHERE id=?", (auth.hash_password(new), row["id"]))
    return RedirectResponse("/admin", status_code=303)
