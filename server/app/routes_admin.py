"""Beheerders-backend, in twee werelden gesplitst.

De beheerpagina was één lange lijst secties geworden, in de volgorde waarin ze
ooit toegevoegd zijn. Daardoor stonden dingen die niets met elkaar te maken
hebben naast elkaar: een knop die een node over de radio uitvraagt, en het
invoerveld voor de bewaartermijn van de databank. Die twee horen niet in dezelfde
visuele rang, want de ene kost zendtijd op een gedeelde band en kan een apparaat
op een dak raken, en de andere zet je zo weer terug.

Sindsdien:

``GET /admin``                  nodes en repeaters -- alles wat een handeling op
                                of informatie over een fysiek apparaat is.
``GET /admin/repeaters/{rid}``  één node: identiteit, uitvragen, klok, firmware,
                                verwijderen.
``GET /admin/server``           deze installatie -- accounts, tokens, bewaring,
                                weergave, parameterlijst, kloksynchronisatie en
                                de statusblokken over de server zelf.

De POST-routes zijn gebleven waar ze stonden. Dat is geen luiheid maar het
voorkomt dat een beheerpagina die al in een tabblad openstond bij het volgende
klikken een 404 oplevert. Waar een GET-URL wél verhuisde staat een omleiding.
"""
from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from . import (auth, clocksync, commanding, config, db, firmware, metrics,
               mqtt_ingest, nodeconfig, ratelimit, retention, tsdb)
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
    # Opruimen na de hernoeming: anders blijft een geldig ondertekende sessie
    # onder de oude naam meereizen bij elk verzoek, ongezien en onherroepbaar.
    resp.delete_cookie(auth.LEGACY_SESSION_COOKIE)
    resp.delete_cookie(auth.LEGACY_LOGIN_COOKIE)
    return resp


@router.get("/logout")
def logout():
    resp = RedirectResponse("/", status_code=303)
    resp.delete_cookie(auth.SESSION_COOKIE)
    return resp


# De volgorde waarin de drie beheerniveaus op het scherm komen: van "hier kan
# alles" naar "hier kan niets". Dat is de volgorde waarin je ze nodig hebt --
# wie iets wil dóén begint bovenaan -- en meteen de volgorde waarin het aantal
# knoppen afneemt.
LEVEL_ORDER = (commanding.LEVEL_FULL, commanding.LEVEL_SEMI, commanding.LEVEL_UNMANAGED)


@router.get("", response_class=HTMLResponse)
def nodes_page(request: Request):
    """Wereld 1: alles wat over een apparaat gaat.

    De route bepaalt hier ook het beheerniveau van elke node, en niet de
    template. Dat is dezelfde regel als bij de opdrachtroutes: wat mogelijk is
    wordt vastgesteld vóór de knop getekend wordt.
    """
    user = require_login(request)
    repeaters = db.q("SELECT * FROM repeaters ORDER BY sort_order, name")
    # Eén keer opgevraagd en dan meegegeven, in plaats van per repeater opnieuw:
    # commanding.describe() haalt ze anders zelf op, en dat is bij twintig nodes
    # veertig overbodige vragen aan de broker en de databank.
    broker = mqtt_ingest.can_publish()
    poller = db.poller_last_seen()
    routes = {rep["id"]: commanding.describe(rep, broker_connected=broker,
                                             poller_seen=poller)
              for rep in repeaters}
    groups = [{"level": level,
               "reps": [r for r in repeaters if routes[r["id"]]["level"] == level]}
              for level in LEVEL_ORDER]
    return templates.TemplateResponse(request, "admin/nodes.html", {
        "site_name": config.SITE_NAME, "user": user, "world": "nodes",
        "repeaters": repeaters, "routes": routes,
        # Een repeater die vanzelf uit een bericht ontstaat komt sinds de
        # vertrouwensgrens verborgen binnen (zie db.get_or_create_repeater).
        # Verborgen binnenkomen mag, ongemerkt binnenkomen niet: zonder dit getal
        # bovenaan staat hij ergens tussen de groepen te wachten op een beslissing
        # waarvan niemand weet dat ze genomen moet worden.
        "hidden_repeaters": sum(1 for r in repeaters if not r["is_public"]),
        # Lege groepen weglaten: een kopje "Unmanaged — 0" met niets eronder is
        # ruis, en de uitleg bij zo'n kopje gaat dan over niemand.
        "groups": [g for g in groups if g["reps"]],
        "csrf": auth.csrf_token(request.cookies.get(auth.SESSION_COOKIE, "")),
    })


@router.get("/server", response_class=HTMLResponse)
def server_page(request: Request):
    """Wereld 2: alles wat deze installatie configureert en geen apparaat raakt."""
    user = require_login(request)
    repeaters = db.q("SELECT * FROM repeaters ORDER BY sort_order, name")
    tokens = db.q("SELECT * FROM tokens WHERE revoked=0 ORDER BY created_at")
    layout = metrics.parse_layout(db.get_setting("layout"))
    # nieuw token éénmalig tonen via kortlevende cookie (niet via de URL)
    new_token = request.cookies.get("mm_new_token")
    resp = templates.TemplateResponse(request, "admin/server.html", {
        "site_name": config.SITE_NAME, "user": user, "world": "server",
        # ``repeaters`` staat hier niet meer in de context: de lijst hoort bij
        # Nodes en repeaters. Hij wordt nog wel opgehaald, want clocksync.targets
        # heeft hem nodig om te zeggen wie er straks uit zichzelf een tijd krijgt.
        "tokens": tokens,
        "csrf": auth.csrf_token(request.cookies.get(auth.SESSION_COOKIE, "")),
        "new_token": new_token,
        "mqtt": mqtt_ingest.status(),
        # Wie waar binnenkomt, zodat de vraag "mag het oude
        # topicvoorvoegsel weg?" van de pagina af te lezen is in plaats
        # van te moeten gokken. Zie mqtt_ingest.LEGACY_PREFIX.
        "topic_prefixes": db.topic_prefix_counts(),
        "tsdb": tsdb.status(),
        "clocksync": clocksync.status(),
        "clock_targets": clocksync.targets(repeaters),
        "cli_params": db.get_setting("cli_params", db.DEFAULT_CLI_PARAMS),
        "settings": {
            "heartbeat_min": db.setting_int("heartbeat_min", config.HEARTBEAT_MIN),
            "retention_days": db.setting_int("retention_days", config.RETENTION_DAYS),
            "packet_retention_days": db.setting_int("packet_retention_days",
                                                    config.PACKET_RETENTION_DAYS),
            "packet_max_rows": db.setting_int("packet_max_rows", config.PACKET_MAX_ROWS),
            "db_max_mb": db.setting_int("db_max_mb", config.DB_MAX_MB),
            "history_ranges": ",".join(str(h) for h in metrics.parse_ranges(db.get_setting("history_ranges"))),
        },
        # Wat de opslag op dit ogenblik doet, plus wat de laatste ronde opruimde.
        # Zichtbaarheid is hier de helft van de feature: een bewaartermijn die
        # door een bovengrens niet gehaald wordt, hoort op het scherm te staan en
        # niet pas op te vallen als er een gat in een grafiek zit.
        "storage": retention.overview(),
        "layout": layout,
        "block_names": metrics.BLOCK_NAMES,
    })
    if new_token:
        resp.delete_cookie("mm_new_token")
    return resp


@router.post("/settings")
def save_settings(request: Request, csrf: str = Form(...),
                  heartbeat_min: int | None = Form(default=None),
                  retention_days: int | None = Form(default=None),
                  history_ranges: str | None = Form(default=None),
                  packet_retention_days: int | None = Form(default=None),
                  packet_max_rows: int | None = Form(default=None),
                  db_max_mb: int | None = Form(default=None)):
    """Instellingen opslaan; elk veld apart, en alleen wat er werkelijk in stond.

    Geen enkel veld is verplicht, en dat is geen slordigheid maar de kern van de
    zaak: de instellingen staan sinds de herindeling over twee formulieren
    verdeeld (bewaring en opslag, en weergave). Met ``Form(...)`` zou het ene
    formulier de waarden van het andere als verborgen velden moeten meesturen, en
    dan overschrijft een pagina die al even openstond stilletjes een instelling
    die intussen elders gewijzigd is. ``None`` betekent hier dus: dit formulier
    ging er niet over, laat staan wat er stond. Bij de bewaargrenzen is dat het
    verschil tussen niets doen en data weggooien.

    Sentinel is None en niet 0, want 0 is voor deze velden geen geldige waarde en
    "niet ingevuld" is iets anders dan "op nul gezet" -- dat onderscheid was met
    een standaard van 0 niet te maken.

    Grenzen: de pakkettermijn tot een jaar (langer is een tijdreeksdatabank en
    geen pakkettenlog), het rijmaximum vanaf ``db.PACKET_FIFO_FLOOR`` (lager kan
    de FIFO toch niet honoreren) en het bytemaximum vanaf 16 MB.
    """
    require_login(request)
    check_csrf(request, csrf)
    if heartbeat_min is not None:
        db.set_setting("heartbeat_min", str(max(1, min(1440, heartbeat_min))))
    if retention_days is not None:
        db.set_setting("retention_days", str(max(1, min(3650, retention_days))))
    if packet_retention_days is not None:
        db.set_setting("packet_retention_days", str(max(1, min(365, packet_retention_days))))
    if packet_max_rows is not None:
        db.set_setting("packet_max_rows",
                       str(max(db.PACKET_FIFO_FLOOR, min(50_000_000, packet_max_rows))))
    if db_max_mb is not None:
        db.set_setting("db_max_mb", str(max(16, min(1_000_000, db_max_mb))))
    if history_ranges is not None:
        db.set_setting("history_ranges",
                       ",".join(str(h) for h in metrics.parse_ranges(history_ranges)))
    # Via de opruimlus en niet via db.prune() rechtstreeks: zo doorloopt een
    # verlaagde termijn hetzelfde pad als de uurlijkse ronde -- inclusief de
    # afweging over VACUUM, want juist het verlagen van een termijn is het geval
    # waarin het bestand anders groot blijft terwijl de inhoud gesnoeid is -- en
    # staat het resultaat meteen op de pagina waar de gebruiker net op klikte.
    # Alleen als er iets aan een termijn of grens veranderd is: het weergave-
    # formulier hoeft geen opruimronde uit te lokken.
    if any(v is not None for v in (retention_days, packet_retention_days,
                                   packet_max_rows, db_max_mb)):
        retention.run_once()
    return RedirectResponse("/admin/server", status_code=303)


@router.post("/layout")
def save_layout(request: Request, layout: str = Form(...), csrf: str = Form(...)):
    require_login(request)
    check_csrf(request, csrf)
    import json as _json
    validated = metrics.parse_layout(layout)
    db.set_setting("layout", _json.dumps(validated))
    return RedirectResponse("/admin/server", status_code=303)


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
    # Gaat het langs een monitor, dan reist de sleutel van het onderwerp mee:
    # de opdracht komt aan bij een andere node dan waar ze over gaat. En dan kan
    # niet elke opdracht -- 'status' hoort daar niet, want die cijfers stuurt de
    # monitor uit zichzelf al door. route["commands"] zegt welke wel.
    open_for_this = route["mqtt"] and command in route["commands"]
    sent = open_for_this and mqtt_ingest.publish_command(
        route["node"], command,
        subject=route["subject"] if route["via_monitor"] else None)
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
def refresh_repeater(request: Request, rid: int, csrf: str = Form(...),
                     back: str = Form(default="")):
    """Vraag nu een verse status: rechtstreeks aan de node en/of via een poller.

    ``back`` zegt waar de knop stond en niet waarheen omgeleid moet worden. Dat
    verschil is het hele punt: een veld dat een URL bevat is een open redirect
    zodra iemand het formulier naar zijn eigen adres laat wijzen, en dit
    formulier staat achter een login die dat de moeite waard maakt. Hier komen
    dus alleen de twee bestemmingen uit die deze functie zelf kent.
    """
    require_login(request)
    check_csrf(request, csrf)
    row = db.qone("SELECT * FROM repeaters WHERE id=?", (rid,))
    if not row:
        raise HTTPException(404, "Onbekende repeater")
    outcome = _dispatch(row, "status")
    if back == "node":
        return RedirectResponse(f"/admin/repeaters/{rid}?status={outcome}", status_code=303)
    return RedirectResponse(f"/r/{row['slug']}?refresh={outcome}", status_code=303)


@router.get("/repeaters/{rid}/settings")
def repeater_settings_redirect(request: Request, rid: int):
    """De oude URL van de instellingenpagina, nu een omleiding.

    Deze pagina heette ``/settings`` toen ze alleen over CLI-instellingen ging.
    Ze gaat nu over de node als geheel en staat op ``/admin/repeaters/{rid}``.
    De oude URL blijft omdat hij in documentatie, in bladwijzers en op de
    publieke repeaterpagina stond -- een dode link is hier een gebruiker die
    denkt dat de knop stuk is. De query-string reist mee, zodat een oude POST
    die hier uitkwam zijn melding niet onderweg verliest.
    """
    require_login(request)
    query = request.url.query
    return RedirectResponse(f"/admin/repeaters/{rid}" + (f"?{query}" if query else ""),
                            status_code=303)


@router.get("/repeaters/{rid}", response_class=HTMLResponse)
def node_page(request: Request, rid: int):
    """Alles over één node: identiteit, uitvragen, klok, firmware, verwijderen."""
    return _node_page(request, rid)


def _node_page(request: Request, rid: int, **extra):
    """De pagina van één node, eventueel met de uitslag van een handeling erbij.

    Een eigen functie omdat een schrijfactie diezelfde pagina teruggeeft met zijn
    antwoord erin, en niet een 303 naar een pagina die het antwoord kwijt is. Het
    antwoord van een schrijfactie is namelijk meer dan gelukt-of-niet: er staat in
    wat er ná afloop in de node staat, en dat kan afwijken van wat er gevraagd is.
    Dat past niet in een queryparameter zonder het te verminken.
    """
    user = require_login(request)
    rep = db.qone("SELECT * FROM repeaters WHERE id=?", (rid,))
    if not rep:
        raise HTTPException(404, "Onbekende repeater")
    requested = request.query_params.get("requested", "")
    rows = db.cli_settings_for(rid)
    # Nieuwste antwoord dat we hebben, ongeacht via welke weg het binnenkwam.
    # Samen met het tijdstip waarop de wachtrij is uitgereikt, zegt dit of een
    # poller die het verzoek meenam er ook iets mee gedaan heeft.
    last_answer = max((r["updated"] for r in rows if r["updated"]), default=None)
    delivered = db.settings_delivered_at(rep["pubkey_prefix"])
    # Eén keer gelezen en aan beide knoppen doorgegeven. clocksync.time_route
    # kijkt bewust niet naar de broker -- die vraag hoort bij het versturen en
    # niet bij de weg -- maar de knop hoort dat wél te weten: zonder verbinding
    # eindigt een klik op "er is niets verstuurd", en dat kan de pagina van
    # tevoren zeggen in plaats van achteraf.
    broker = mqtt_ingest.can_publish()
    cfg = nodeconfig.cfg_route(rep)
    return templates.TemplateResponse(request, "admin/node.html", {
        "site_name": config.SITE_NAME, "user": user, "world": "nodes", "rep": rep,
        "settings_rows": rows,
        # De uitslag van een statusopvraging die vanaf déze pagina vertrok. De
        # publieke repeaterpagina heeft dezelfde knop en houdt zijn eigen
        # ?refresh=; welke van de twee je krijgt hangt af van waar je klikte.
        "status": request.query_params.get("status", ""),
        "delivered_since": delivered,
        # ISO-tijdstempels in dit formaat sorteren alfabetisch juist.
        "delivery_unanswered": bool(delivered
                                    and (last_answer is None or last_answer < delivered)),
        # De parameterlijst staat niet meer in deze context: hij geldt voor alle
        # repeaters tegelijk en hoort dus bij Server en site. Hem hier tonen
        # wekte de indruk dat je hem per node kon zetten.
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
        # Klok: dezelfde opzet als hierboven. Welke node de tijd zou krijgen en
        # of dat nu kan, bepaald vóór de knop getekend wordt; en wanneer deze
        # site die node voor het laatst iets stuurde, want dat is het enige wat
        # ze met zekerheid weet -- of de klok daarna ook echt goed stond, weet
        # alleen de node zelf ('wifi clock').
        "clock_route": clocksync.time_route(rep),
        "clock_sent": clocksync.last_sent_iso(rep["source_prefix"] or ""),
        "clock_gap_min": clocksync.MANUAL_MIN_GAP_S // 60,
        "clock_min_fw": ".".join(str(n) for n in clocksync.MIN_TIME_VERSION),
        "clock": request.query_params.get("clock", ""),
        # De reden uit de laatste klokcontrole, zodat een weigering hier
        # meteen zegt wát er mis was in plaats van naar Server en site te
        # verwijzen en de lezer daar te laten zoeken.
        "clocksync_reason": (clocksync.status().get("clock") or {}).get("reason", ""),
        "clock_wait": request.query_params.get("wait", ""),
        "clock_enabled": clocksync.ENABLED,
        "broker": broker,
        # Wat er kán, bepaald vóór de knop getekend wordt: een knop die niets
        # kan doen hoort uitgeschakeld te zijn en te zeggen waarom. De vereiste
        # firmwareversie zit in die route en niet apart hier: welke versie nodig
        # is hangt af van de weg (1.8.0 voor de node zelf, 1.9.0 voor een
        # monitor), en twee plaatsen die dat allebei uitrekenen is er één te veel.
        "route": commanding.describe(rep, broker_connected=broker),
        # Instellingen schrijven. De parameterlijst komt van de node zelf en
        # niet uit een tabel hier: de firmware is er de baas over, en een tweede
        # lijst zou vroeg of laat een parameter aanbieden die de node weigert.
        # Alleen ophalen als er ook echt een weg is, anders staat elke
        # paginaweergave tien seconden op een node te wachten die er niet is.
        "cfg_route": cfg,
        "cfg_params": (nodeconfig.params(cfg["host"]) if cfg["can"]
                       else {"ok": False, "error": "", "params": []}),
        **extra,
    })


@router.post("/repeaters/{rid}/config")
def write_config(request: Request, rid: int, key: str = Form(...),
                 value: str = Form(""), csrf: str = Form(...)):
    """Eén instelling van deze node zetten en meteen teruglezen.

    Synchroon, anders dan de firmware-upgrade: dit is één CLI-aanroep over het
    lokale netwerk en die is in tienden van seconden klaar. Een achtergrondtaak
    met een toestand om te pollen zou hier machinerie zijn om niets.

    Geeft de pagina terug in plaats van een 303, want het antwoord bevat wat er
    ná afloop in de node staat -- en dat is soms iets anders dan wat er gevraagd
    is. Zie nodeconfig.write() voor de twee gemeten redenen waarom.
    """
    require_login(request)
    check_csrf(request, csrf)
    rep = db.qone("SELECT * FROM repeaters WHERE id=?", (rid,))
    if not rep:
        raise HTTPException(404, "Onbekende repeater")
    result = nodeconfig.write(rep, key.strip(), value.strip())
    return _node_page(request, rid, cfg_result=result)


@router.post("/repeaters/{rid}/settings/refresh")
def repeater_settings_refresh(request: Request, rid: int, csrf: str = Form(...)):
    """Vraag de CLI-instellingen op: rechtstreeks aan de node en/of via een poller."""
    require_login(request)
    check_csrf(request, csrf)
    rep = db.qone("SELECT * FROM repeaters WHERE id=?", (rid,))
    if not rep:
        raise HTTPException(404, "Onbekende repeater")
    outcome = _dispatch(rep, "settings")
    return RedirectResponse(f"/admin/repeaters/{rid}?requested={outcome}",
                            status_code=303)


@router.post("/repeaters/{rid}/clocksync")
def repeater_clocksync(request: Request, rid: int, csrf: str = Form(...)):
    """Zet de klok van (de node achter) deze repeater nu, in plaats van morgen.

    Alle beslissingen zitten in clocksync.sync_now, waar ook de planner
    langsloopt. Deze functie doet niets dan de repeater opzoeken en de uitslag
    aan de pagina doorgeven -- juist zodat er geen tweede plek is waar over
    publiceren beslist wordt.
    """
    require_login(request)
    check_csrf(request, csrf)
    rep = db.qone("SELECT * FROM repeaters WHERE id=?", (rid,))
    if not rep:
        raise HTTPException(404, "Onbekende repeater")
    result = clocksync.sync_now(rep)
    # De wachttijd reist mee in de URL, want zonder dat getal is "te snel" een
    # mededeling waar niemand iets mee kan.
    suffix = f"&wait={result['wait_min']}" if result["outcome"] == "too_soon" else ""
    return RedirectResponse(
        f"/admin/repeaters/{rid}?clock={result['outcome']}{suffix}",
        status_code=303)


@router.post("/cli_params")
def save_cli_params(request: Request, cli_params: str = Form(...),
                    csrf: str = Form(...), rid: int = Form(default=0)):
    """De parameterlijst, die voor alle repeaters tegelijk geldt.

    ``rid`` is er alleen nog voor een pagina die vóór de herindeling geopend
    werd: het formulier stond toen op de pagina van één repeater en stuurde zijn
    id mee om terug te kunnen keren. Die waarde wordt genegeerd -- de lijst was
    ook toen al globaal, en dat is precies waarom ze hier is komen staan.
    """
    require_login(request)
    check_csrf(request, csrf)
    cleaned = ",".join(p.strip() for p in cli_params.replace(";", ",").split(",") if p.strip())
    db.set_setting("cli_params", cleaned or db.DEFAULT_CLI_PARAMS)
    return RedirectResponse("/admin/server#cli-params", status_code=303)


# Welke zichtbaarheidsknop een formulier omklapt. Een vaste tabel en geen
# kolomnaam uit het verzoek: ``what`` komt van buiten, en een naam die
# rechtstreeks in een UPDATE terechtkomt is een openstaande deur naar elke
# andere kolom van deze tabel. Dezelfde verdediging als search.Sort.
_VISIBILITY_COLUMNS = {
    "public": "is_public",
    "position": "show_position",
    "name": "show_name",
}


@router.post("/repeaters/{rid}/toggle")
def toggle_repeater(request: Request, rid: int, csrf: str = Form(...),
                    back: str = Form(default=""), what: str = Form(default="public")):
    """Eén zichtbaarheidsknop omklappen. Staat op twee pagina's, dus ``back``
    zegt welke.

    ``what`` is nieuw en heeft daarom "public" als standaard: dat is precies wat
    dit formulier deed toen het nog maar één knop was, en een pagina die nog in
    een tabblad openstaat mag daar niet op stukvallen. Een onbekende waarde
    klapt niets om en gaat gewoon terug -- er valt hier niets te melden wat een
    bezoeker van deze pagina zelf niet ziet staan.

    Zie refresh_repeater voor waarom ``back`` geen URL is maar een woord dat deze
    functie zelf vertaalt.
    """
    require_login(request)
    check_csrf(request, csrf)
    column = _VISIBILITY_COLUMNS.get(what)
    if column:
        db.execute(f"UPDATE repeaters SET {column} = 1 - {column} WHERE id=?", (rid,))
    if back == "node":
        return RedirectResponse(f"/admin/repeaters/{rid}#zichtbaarheid", status_code=303)
    return RedirectResponse("/admin", status_code=303)


@router.post("/repeaters/{rid}/rename")
def rename_repeater(request: Request, rid: int, name: str = Form(...), csrf: str = Form(...)):
    require_login(request)
    check_csrf(request, csrf)
    name = name.strip()
    if name:
        db.execute("UPDATE repeaters SET name=? WHERE id=?", (name, rid))
    # Terug naar de pagina van deze node: daar staat het veld sinds de
    # herindeling, en daar zie je meteen of de nieuwe naam er staat.
    return RedirectResponse(f"/admin/repeaters/{rid}", status_code=303)


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
    resp = RedirectResponse("/admin/server#tokens", status_code=303)
    resp.set_cookie("mm_new_token", token, max_age=60, httponly=True,
                    samesite="lax", secure=_secure(request))
    return resp


@router.post("/tokens/{tid}/revoke")
def revoke_token(request: Request, tid: int, csrf: str = Form(...)):
    require_login(request)
    check_csrf(request, csrf)
    db.execute("UPDATE tokens SET revoked=1 WHERE id=?", (tid,))
    return RedirectResponse("/admin/server#tokens", status_code=303)


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
    resp = RedirectResponse("/admin/server#toegang", status_code=303)
    resp.set_cookie(
        auth.SESSION_COOKIE, auth.make_session(user),
        max_age=auth.SESSION_TTL, httponly=True, samesite="lax", secure=_secure(request),
    )
    return resp


# --- firmware ----------------------------------------------------------------

def _fw_context(request: Request, **extra):
    """Alles wat de firmwarepagina nodig heeft, in één keer.

    Per repeater een ``ota``-blok naast de rij zelf, want elke knop op die pagina
    moet uit dezelfde redenering komen. Twee plekken die allebei uitrekenen of
    een node een image mag krijgen zijn twee plekken die het een keer oneens
    worden, en de eerste keer dat dat gebeurt staat er een knop onder een node
    die hem niet kan uitvoeren.
    """
    repeaters = db.q("SELECT * FROM repeaters ORDER BY sort_order, name")
    rel = firmware.releases()
    rows = []
    for rep in repeaters:
        route = firmware.ota_route(rep)
        rows.append({
            "rep": rep,
            "ota": route,
            "job": firmware.job(rep["id"]),
            # Welke releases een image dragen voor de bouwomgeving die deze node
            # meldde. Leeg als we die omgeving niet kennen -- dan is de eerlijke
            # uitkomst 'niet vast te stellen' en geen lijst om uit te kiezen.
            "builds": [r for r in (rel.get("items") or []) if route["env"] in r["images"]]
                      if route["env"] else [],
        })
    ctx = {
        "site_name": config.SITE_NAME, "user": current_user(request),
        # Firmware is een handeling op een apparaat, dus deze pagina staat in de
        # wereld van de nodes en licht daar op in de tabbalk.
        "world": "nodes", "firmware_tab": True,
        "csrf": auth.csrf_token(request.cookies.get(auth.SESSION_COOKIE, "")),
        "rows": rows,
        "releases": rel.get("items") or [],
        "rel_error": rel.get("error") or "",
        "rel_at": rel.get("at") or 0,
        "repo": firmware.repo_slug(),
        "have_credentials": bool(firmware.NODE_USER),
    }
    ctx.update(extra)
    return templates.TemplateResponse(request, "admin/firmware.html", ctx)


@router.get("/firmware", response_class=HTMLResponse)
def firmware_page(request: Request):
    require_login(request)
    return _fw_context(request)


@router.post("/firmware/refresh")
def firmware_refresh(request: Request, csrf: str = Form(...)):
    require_login(request)
    check_csrf(request, csrf)
    firmware.releases(force=True)
    return RedirectResponse("/admin/firmware", status_code=303)


@router.post("/repeaters/{rid}/ota")
def save_ota(request: Request, rid: int, ota_host: str = Form(""),
             is_critical: str = Form(""), csrf: str = Form(...)):
    require_login(request)
    check_csrf(request, csrf)
    if not db.qone("SELECT id FROM repeaters WHERE id=?", (rid,)):
        raise HTTPException(404, "Onbekende repeater")
    host = (ota_host or "").strip()
    if host:
        try:
            firmware._url(host, "/api/fw")      # zelfde controle als bij het schrijven
        except ValueError as exc:
            raise HTTPException(422, f"Adres onbruikbaar: {exc}") from exc
    db.set_ota_host(rid, host)
    db.set_critical(rid, bool(is_critical))
    return RedirectResponse("/admin/firmware", status_code=303)


@router.post("/repeaters/{rid}/probe")
def probe_node(request: Request, rid: int, csrf: str = Form(...)):
    """Eén keer aankloppen bij de node en onthouden wat hij zegt.

    Bestaat omdat de bouwomgeving nergens anders vandaan komt: hij zit niet in
    het statistiekenbericht en kan er ook niet in, want een node zonder IP-pad
    zou hem dan melden zonder dat er ooit een image langs kan. Eén knop die het
    ophaalt op het moment dat de beheerder zegt dat er een pad is, is eerlijker
    dan een veld dat stilletjes veroudert.
    """
    require_login(request)
    check_csrf(request, csrf)
    rep = db.qone("SELECT * FROM repeaters WHERE id=?", (rid,))
    if not rep:
        raise HTTPException(404, "Onbekende repeater")
    info = firmware.probe(str(rep["ota_host"] or ""))
    if info["ok"]:
        db.record_pio_env(rid, info["env"])
        if info["ver"]:
            db.record_firmware(rid, fw_module=info["ver"])
    return _fw_context(request, probe={"rid": rid, "info": info})


@router.post("/repeaters/{rid}/upgrade")
def start_upgrade(request: Request, rid: int, tag: str = Form(...),
                  expect_env: str = Form(""), confirm: str = Form(""),
                  csrf: str = Form(...)):
    require_login(request)
    check_csrf(request, csrf)
    rep = db.qone("SELECT * FROM repeaters WHERE id=?", (rid,))
    if not rep:
        raise HTTPException(404, "Onbekende repeater")

    # De bevestiging voor een kritieke node. Niet 'weet u het zeker' maar de
    # naam overtypen, want de fout die dit moet vangen is niet twijfel maar een
    # klik op de verkeerde regel -- en daar helpt een ja/nee-vraag niet tegen.
    if rep["is_critical"] and (confirm or "").strip() != (rep["name"] or ""):
        return _fw_context(request, started={
            "rid": rid, "ok": False,
            "error": f"Deze node staat als kritiek gemarkeerd. Typ de naam "
                     f"({rep['name']}) precies over om te bevestigen.",
        })

    result = firmware.start(rep, tag, expect_env)
    return _fw_context(request, started={"rid": rid, **result})


@router.post("/repeaters/{rid}/upgrade/clear")
def clear_upgrade(request: Request, rid: int, csrf: str = Form(...)):
    require_login(request)
    check_csrf(request, csrf)
    firmware.clear_job(rid)
    return RedirectResponse("/admin/firmware", status_code=303)


@router.get("/firmware/jobs")
def firmware_jobs(request: Request):
    """Alleen de toestand van de lopende opdrachten, voor de pagina zelf.

    Het enige stukje /admin dat niet via een formulier en een 303 loopt, en dat
    is niet uit voorkeur maar uit noodzaak: een upgrade duurt langer dan een
    verzoek mag duren, en een pagina die pas na twee minuten iets zegt is een
    pagina waarvan je denkt dat hij hangt.
    """
    require_login(request)
    return firmware.jobs()
