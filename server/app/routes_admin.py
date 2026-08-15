"""Beheerders-backend: login, repeaterbeheer, API-tokens, wachtwoord."""
from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from . import auth, commanding, config, db, metrics, mqtt_ingest, ratelimit, tsdb
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
    if not cookie or not auth.eq(csrf, auth.csrf_token(cookie)):
        raise HTTPException(403, "CSRF-controle mislukt")


def _secure(request: Request) -> bool:
    return request.headers.get("x-forwarded-proto", request.url.scheme) == "https"


def _login_page(request: Request, nonce: str, error: str | None,
                error_key: str | None = None, error_vars: dict | None = None,
                status: int = 200, retry_after: int = 0):
    """Render the login form and (re)issue the nonce its CSRF token hangs off.

    The Dutch wording is rendered server-side so the page reads correctly without
    JavaScript; the key and its variables let static/i18n.js swap in English.
    """
    resp = templates.TemplateResponse(request, "admin/login.html", {
        "site_name": config.SITE_NAME, "error": error,
        "error_key": error_key, "error_vars": error_vars or {},
        "csrf": auth.csrf_token(nonce),
    }, status_code=status)
    resp.set_cookie(auth.LOGIN_COOKIE, nonce, max_age=auth.LOGIN_TTL, httponly=True,
                    samesite="lax", secure=_secure(request))
    if retry_after:
        resp.headers["Retry-After"] = str(retry_after)
    return resp


@router.get("/login", response_class=HTMLResponse)
def login_page(request: Request):
    # A fresh nonce per view: the token is worthless to an attacker who cannot
    # also read the cookie it is derived from.
    return _login_page(request, auth.new_login_nonce(), None)


@router.post("/login")
def login(request: Request, username: str = Form(...), password: str = Form(...),
          csrf: str = Form(default="")):
    nonce = request.cookies.get(auth.LOGIN_COOKIE, "")
    if not nonce or not auth.eq(csrf, auth.csrf_token(nonce)):
        # Also the natural landing spot for a form left open past LOGIN_TTL,
        # hence a message that tells the visitor to simply try again.
        return _login_page(request, auth.new_login_nonce(),
                           "Sessie verlopen — probeer opnieuw.", "login.expired",
                           status=403)

    ip = ratelimit.client_ip(request)
    wait = ratelimit.retry_after(ip, username)
    if wait:
        return _login_page(request, nonce,
                           f"Te veel mislukte pogingen. Probeer over {wait} s opnieuw.",
                           "login.throttled", {"n": wait},
                           status=429, retry_after=wait)

    row = db.qone("SELECT * FROM admins WHERE username=?", (username.strip(),))
    if row:
        ok = auth.verify_password(password, row["pw_hash"])
    else:
        auth.verify_dummy(password)  # equal cost, so timing reveals no usernames
        ok = False
    if not ok:
        wait = ratelimit.record_failure(ip, username)
        if wait:
            return _login_page(
                request, nonce,
                f"Ongeldige inloggegevens — te veel pogingen, wacht {wait} s.",
                "login.invalid_throttled", {"n": wait}, status=429, retry_after=wait)
        return _login_page(request, nonce, "Ongeldige inloggegevens",
                           "login.invalid", status=401)

    ratelimit.record_success(ip, username)
    resp = RedirectResponse("/admin", status_code=303)
    resp.set_cookie(
        auth.SESSION_COOKIE, auth.make_session(row["username"]),
        max_age=auth.SESSION_TTL, httponly=True, samesite="lax", secure=_secure(request),
    )
    resp.delete_cookie(auth.LOGIN_COOKIE)
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
        "tsdb": tsdb.status(),
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


def _dispatch(rep, command: str) -> str:
    """Stuur één opdracht langs elke weg die openstaat. Geeft terug welke.

    Beide wegen worden bewandeld en niet de eerste de beste: ze zijn niet
    uitwisselbaar. De MQTT-weg bereikt de node zelf en alleen als die op dit
    ogenblik aan de broker hangt; de wachtrij bereikt een poller die de repeater
    over LoRa uitvraagt en ook werkt als de node zijn WiFi uit heeft staan. Wie
    er allebei zijn, heeft er allebei iets aan; wie er geen heeft, hoort dat te
    zien en niet "gestart" te lezen.

    Terug komt 'mqtt', 'queued', 'both' of 'none' -- wat de pagina daarna zegt
    hangt daaraan en niet aan wat we hoopten dat er zou gebeuren.
    """
    route = commanding.describe(rep)
    sent = route["mqtt"] and mqtt_ingest.publish_command(route["node"], command)
    queued = route["ha"]
    if command == "settings":
        raw = db.get_setting("cli_params", db.DEFAULT_CLI_PARAMS)
        params = [p.strip() for p in raw.replace(";", ",").split(",") if p.strip()][:40]
        # Ook zonder poller in zicht in de wachtrij zetten zou een verzoek
        # achterlaten dat maanden later door een net geïnstalleerde Home
        # Assistant wordt opgepikt. Alleen zetten als er iemand is om het op te
        # halen, zodat pending_settings_request() blijft betekenen wat het zegt.
        if queued:
            db.request_settings(rep["pubkey_prefix"], params)
    elif queued:
        db.request_refresh(rep["pubkey_prefix"])

    if sent and queued:
        return "both"
    if sent:
        return "mqtt"
    if queued:
        return "queued"
    return "none"


@router.post("/repeaters/{rid}/refresh")
def refresh_repeater(request: Request, rid: int, csrf: str = Form(...)):
    """Vraag nu een verse status: rechtstreeks aan de node en/of via een poller."""
    require_login(request)
    check_csrf(request, csrf)
    row = db.qone("SELECT * FROM repeaters WHERE id=?", (rid,))
    if not row:
        raise HTTPException(404, "Onbekende repeater")
    outcome = _dispatch(row, "status")
    return RedirectResponse(f"/r/{row['slug']}?refresh={outcome}", status_code=303)


@router.get("/repeaters/{rid}/settings", response_class=HTMLResponse)
def repeater_settings_page(request: Request, rid: int):
    """Readonly-overzicht van de CLI-instellingen van een repeater."""
    user = require_login(request)
    rep = db.qone("SELECT * FROM repeaters WHERE id=?", (rid,))
    if not rep:
        raise HTTPException(404, "Onbekende repeater")
    requested = request.query_params.get("requested", "")
    return templates.TemplateResponse(request, "admin/repeater_settings.html", {
        "site_name": config.SITE_NAME, "user": user, "rep": rep,
        "settings_rows": db.cli_settings_for(rid),
        "cli_params": db.get_setting("cli_params", db.DEFAULT_CLI_PARAMS),
        "csrf": auth.csrf_token(request.cookies.get(auth.SESSION_COOKIE, "")),
        # '1' is de oude vorm, van vóór er meer dan één weg was; een pagina die
        # nog in een tabblad openstaat mag daar niet op stukvallen.
        "requested": "both" if requested == "1" else requested,
        # Staat het verzoek er na een herlading nog, dan heeft geen enkele
        # poller sinds de klik iets opgehaald -- een heel ander euvel dan een
        # opvraging die wel vertrok en waarvan het antwoord uitblijft. De pagina
        # hoort dat verschil te tonen in plaats van in beide gevallen "gestart"
        # te melden.
        "queued_since": db.pending_settings_request(rep["pubkey_prefix"]),
        # Wat er kán, bepaald vóór de knop getekend wordt: een knop die niets
        # kan doen hoort uitgeschakeld te zijn en te zeggen waarom.
        "route": commanding.describe(rep),
        "min_fw": ".".join(str(n) for n in commanding.MIN_CMD_VERSION),
    })


@router.post("/repeaters/{rid}/settings/refresh")
def repeater_settings_refresh(request: Request, rid: int, csrf: str = Form(...)):
    """Vraag de CLI-instellingen op: rechtstreeks aan de node en/of via een poller."""
    require_login(request)
    check_csrf(request, csrf)
    rep = db.qone("SELECT * FROM repeaters WHERE id=?", (rid,))
    if not rep:
        raise HTTPException(404, "Onbekende repeater")
    outcome = _dispatch(rep, "settings")
    return RedirectResponse(f"/admin/repeaters/{rid}/settings?requested={outcome}",
                            status_code=303)


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
    # Every session signed under the old password is now invalid, this one
    # included -- so hand this browser a new cookie instead of logging the
    # person who just changed the password out of their own admin page.
    resp = RedirectResponse("/admin", status_code=303)
    resp.set_cookie(
        auth.SESSION_COOKIE, auth.make_session(user),
        max_age=auth.SESSION_TTL, httponly=True, samesite="lax", secure=_secure(request),
    )
    return resp
