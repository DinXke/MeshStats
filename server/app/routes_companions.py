"""Companion-beheer als een op zichzelf staande module.

Waarom een eigen module en niet een sectie in ``routes_admin``
--------------------------------------------------------------
Companion-beheer is een eigen ding: een lijst met handzenders (T1000-E e.d.), de
commando's ernaartoe, en een generieke Send-DM-weg. Dat heeft niets te maken met
het uitvragen van repeaters, de firmwarelijst of de serverinstellingen die in
``routes_admin`` staan. Het als eigen router afsplitsen houdt beide leesbaar en
geeft companion-beheer zijn eigen top-level navigatie-onderdeel met twee
sub-items (Companions-lijst en Send-DM).

De grenzen blijven exact als voorheen, en dat is geen toeval maar de reden dat
dit een veilige verplaatsing is:

*   de LIJST muteren is een SERVERhandeling (``server.companions``);
*   een DM VERSTUREN is een NODE-handeling op de afzender
    (``node.instelling.merkbaar``), want het kost zendtijd op díe node;
*   RBAC, CSRF en het audittrail lopen langs dezelfde poort als in
    ``routes_admin`` -- de controlefuncties worden er dan ook uit geïmporteerd en
    niet overgeschreven. Één plek die ja of nee zegt, ook al staan de routes nu
    ergens anders.

Het vervoer zit in ``companions.send_dm`` (nu de bot van de afzender-node) en
nergens hier: deze routes geven een afzender-node en een tekst.
"""
import secrets
import time
from urllib.parse import urlencode

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from . import audit, auth, companions, config, db, rbac, rooms
# De poort en haar helpers staan in routes_admin en worden hier hergebruikt, niet
# gekopieerd: een tweede kopie van de rechtencontrole is een tweede plek om hem
# verkeerd te krijgen. Zie routes_admin voor de uitleg bij elk.
from .routes_admin import (_noteer, _rep_or_404, check_csrf, require_login,
                           require_perm)
from .templating import templates

# --- na-een-mutatie: redirect (PRG) en niet een 200 op de POST zelf ----------
#
# Elke schrijvende route hieronder eindigde vroeger met het rechtstreeks
# terugrenderen van de doelpagina (een 200 op de POST). Dat is precies het
# patroon dat de rest van de admin-UI (routes_admin.py) NERGENS gebruikt --
# daar eindigt iedere schrijvende route met een 303-redirect naar een GET. Het
# verschil is niet cosmetisch: een 200 op een POST betekent dat de browser die
# pagina onthoudt als "bereikt via POST", en drukt de bezoeker daarna op
# verversen (of navigeert terug), dan biedt de browser "verzoek opnieuw
# verzenden" aan -- bevestigt iemand dat uit gewoonte, dan gaat bijvoorbeeld
# een companion-commando een TWEEDE keer de mesh op. Wie annuleert, blijft naar
# een niet-verversende pagina kijken en denkt dat de knop het niet deed. Dat is
# de kern van de klacht "de beheer-UI ververst niet altijd na een actie".
#
# De oplossing is dezelfde als overal elders: Post/Redirect/Get. De uitslag
# (gelukt/niet gelukt, en de tekst) reist niet in een sessie-flash mee -- die
# machinerie bestaat hier nergens -- maar als een kort resultaatcode in de
# querystring van de redirect, precies zoals ``routes_admin.refresh_repeater``
# al ``?status=<woord>`` gebruikt. De GET-route zet die code om in dezelfde
# Nederlandse tekst die hier vroeger rechtstreeks gerenderd werd.
def _redirect(url: str, **params) -> RedirectResponse:
    """Een 303-redirect naar ``url``, met de gegeven parameters als querystring
    (lege waarden worden weggelaten). 303 en niet 302: dwingt de browser de
    volgende stap als GET te doen, ongeacht wat de browser van de oorspronkelijke
    methode zou onthouden -- exact de PRG-eis hierboven."""
    schoon = {k: v for k, v in params.items() if v not in (None, "")}
    qs = ("?" + urlencode(schoon)) if schoon else ""
    return RedirectResponse(url + qs, status_code=303)

router = APIRouter(prefix="/admin")

# Hoe lang een val als "recent" telt op de kaart en de detailpagina -- een
# eigen marker/badge die na een dag nog steeds de allereerste val van een
# companion toont, zou een oefening of een oude melding laten doorgaan voor
# een actuele noodsituatie. Vierentwintig uur is ruim genoeg om "iemand is
# gevallen en er is nog niemand wezen kijken" zichtbaar te houden zonder een
# handzender die zes maanden geleden één keer viel voor altijd rood te kleuren.
FALL_RECENT_S = 24 * 3600


def _keuze_id(waarde) -> int | None:
    """Eén gekozen rij-id uit een formulierveld, of None voor de lege keuze.

    Een dropdown met "— geen —" stuurt een lege string, en dat hoort None te
    worden en geen 500 op int("")."""
    tekst = str(waarde or "").strip()
    return int(tekst) if tekst.isdigit() else None


# --- gedeelde context ---------------------------------------------------------

def _sender_reps(user) -> list:
    """De afzender-kandidaten voor deze gebruiker: zichtbare nodes met een
    bereikbare eigen API (waar de bot op draait). Op naam gesorteerd -- gebouwd
    voor tientallen kandidaten die voorspelbaar moeten staan."""
    reps = db.q("SELECT * FROM repeaters ORDER BY name COLLATE NOCASE, sort_order")
    zichtbaar = rbac.zichtbare_nodes(user, reps)
    return companions.sender_candidates(zichtbaar)


def _loc(c, now: float) -> dict:
    """Eén companion-rij naar de vorm die de kaart, de detailpagina-verversing
    en de lijst-verversing allemaal delen (zie ``companions_status_json``
    hieronder). Eén functie en geen drie keer dezelfde velden opnieuw uitrekenen
    -- een latere vierde afnemer zou anders licht kunnen verschillen van de
    andere drie zonder dat iemand het merkt."""
    fall_ts = c["last_escalated_fall_ts"]
    return {"id": c["id"], "name": c["name"], "type": c["type"],
            "lat": c["last_lat"], "lon": c["last_lon"],
            "seen_iso": db.iso_from_epoch(c["last_seen"]),
            # Alleen een recente val kleurt de marker/badge anders -- zie
            # FALL_RECENT_S hierboven voor waarom een val van maanden terug
            # niet voor altijd rood moet blijven.
            "fall_recent": bool(fall_ts) and (now - fall_ts) < FALL_RECENT_S,
            "fall_kind": c["last_fall_kind"], "fall_iso": db.iso_from_epoch(fall_ts)}


def _mag_versturen(user, reps) -> dict:
    """Per afzender-rid of deze gebruiker er een DM vanaf mag sturen. Zo kan de
    keuzelijst een node tonen maar de knop uitschakelen met de reden erbij --
    dezelfde lijn als overal: niet verbergen, uitleggen."""
    return {r["id"]: rbac.decide(user, "node.instelling.merkbaar", r) for r in reps}


# --- de resultaatbanner na een redirect: code -> Nederlandse tekst -----------
#
# Elke pagina hieronder heeft zijn EIGEN codelijst (dezelfde code kan op de ene
# pagina iets anders betekenen dan op de andere -- ze delen geen namespace,
# want elke pagina leest alleen zijn eigen ``?r=``). ``n``/``b``/``e`` zijn de
# enige vrije tekst die meereist (naam, DM-body, foutmelding); Jinja's
# auto-escape maakt die net zo veilig als toen ze nog rechtstreeks in de
# gerenderde pagina stonden.

def _companions_result(request: Request) -> dict | None:
    r = request.query_params.get("r", "")
    n = request.query_params.get("n", "")
    if r == "added":
        return {"ok": True, "msg": f"companion '{n}' toegevoegd"}
    if r == "deleted":
        return {"ok": True, "msg": f"companion '{n}' verwijderd"}
    if r == "err_name":
        return {"ok": False, "msg": "een companion heeft een naam nodig"}
    if r == "err_pubkey":
        return {"ok": False, "msg": "een volledige pubkey van 64 hex-tekens is vereist"}
    if r == "err_dup":
        return {"ok": False, "msg": "die pubkey staat al in de lijst"}
    return None


def _companion_result(request: Request, comp) -> dict | None:
    r = request.query_params.get("r", "")
    if r == "edited":
        return {"ok": True, "msg": f"companion '{comp['name']}' bijgewerkt"}
    if r == "err_name":
        return {"ok": False, "msg": "een companion heeft een naam nodig"}
    if r == "err_pubkey":
        return {"ok": False, "msg": "een volledige pubkey van 64 hex-tekens is vereist"}
    if r == "err_dup":
        return {"ok": False, "msg": "die pubkey staat al bij een andere companion"}
    if r == "alert_added":
        return {"ok": True, "msg": "ontvanger toegevoegd"}
    if r == "alert_err_pubkey":
        return {"ok": False,
                "msg": "een volledige pubkey van 64 hex-tekens is vereist voor de ontvanger"}
    if r == "alert_deleted":
        return {"ok": True, "msg": "ontvanger verwijderd"}
    if r == "alert_notfound":
        return {"ok": False, "msg": "onbekende ontvanger"}
    if r == "cmd_nosender":
        return {"ok": False, "msg": "kies eerst een afzender-node (die de DM verstuurt)"}
    if r == "cmd_ok":
        return {"ok": True,
                "msg": f"commando verzonden naar {comp['name']}: "
                       f"{request.query_params.get('b', '')}"}
    if r == "cmd_err":
        return {"ok": False, "msg": f"niet verstuurd — {request.query_params.get('e', '')}"}
    if r == "bot_saved":
        return {"ok": True, "msg": "bot-voorkeur opgeslagen"}
    if r == "share_on":
        return {"ok": True, "msg": "publieke deel-link aangemaakt"}
    if r == "share_off":
        return {"ok": True, "msg": "publieke deel-link ingetrokken"}
    return None


def _senddm_result(request: Request) -> dict | None:
    r = request.query_params.get("r", "")
    if r == "nosender":
        return {"ok": False, "msg": "kies eerst een afzender-node"}
    if r == "sent":
        return {"ok": True, "msg": f"DM verstuurd naar {request.query_params.get('t', '')}"}
    if r == "err":
        return {"ok": False, "msg": f"niet verstuurd — {request.query_params.get('e', '')}"}
    return None


# --- de companions-lijst + CRUD ----------------------------------------------

def _companions_page(request: Request, extra: dict | None = None):
    user = require_login(request)
    ik = rbac.load(user)
    senders = _sender_reps(ik)
    reps = db.q("SELECT * FROM repeaters")
    comps = db.list_companions()
    now = time.time()
    ctx = {
        "site_name": config.SITE_NAME, "user": user, "world": "companions",
        "companions_tab": True,
        "companions": comps,
        # "Laatst gezien" + val-badge in de tabel, en het startpunt voor de
        # auto-ververs (companions.js leest dezelfde velden terug uit
        # /admin/companions/status.json) -- zie ``_loc`` hierboven.
        "loc_by_id": {c["id"]: _loc(c, now) for c in comps},
        "rep_by_id": {r["id"]: r for r in reps},
        "senders": senders,
        "mag_beheren": rbac.decide(ik, "server.companions"),
        "serverrechten": rbac.serverrechten(ik),
        "csrf": auth.csrf_token(request.cookies.get(auth.SESSION_COOKIE, "")),
        "result": _companions_result(request),
    }
    ctx.update(extra or {})
    return templates.TemplateResponse(request, "admin/companions.html", ctx)


@router.get("/companions", response_class=HTMLResponse)
def companions_page(request: Request):
    """Het eerste sub-item: de beheerde companions.

    Een lijst en niet een blok per node, om dezelfde reden als de andere
    verzamel-pagina's: "welke companion hoort bij welke afzender" en "welke heeft
    nog geen standaardafzender" stel je over de hele verzameling tegelijk.
    """
    return _companions_page(request)


# --- de kaart (derde sub-item) -------------------------------------------------
#
# Vóór ``/companions/{cid}`` gedefinieerd en niet erna, en dat is geen
# schoonheidskeuze: FastAPI/Starlette matcht routes in registratievolgorde, en
# ``{cid}`` is getypeerd als ``int``. Stond deze route erachter, dan zou
# ``/companions/kaart`` eerst op ``{cid}`` botsen -- "kaart" is geen geldig
# getal -- en een 422 krijgen in plaats van deze pagina. Dezelfde volgorde-eis
# geldt al her en der in routes_admin.py (``/repeaters/{rid}/settings`` vóór
# ``/repeaters/{rid}``).

def _companions_map_page(request: Request, extra: dict | None = None):
    user = require_login(request)
    ik = rbac.load(user)
    locaties = db.companions_with_location()
    now = time.time()

    ctx = {
        "site_name": config.SITE_NAME, "user": user, "world": "companions",
        "companions_map_tab": True,
        "locations": [_loc(c, now) for c in locaties],
        # De tijdvensters voor het spoor per companion op de kaart, uit
        # companions.py zodat de knoppen en de server-validatie dezelfde bron
        # delen (zie companion_track_json en companions.TRACK_WINDOWS).
        "track_windows": companions.track_windows_for_ui(),
        "track_window_default": companions.TRACK_WINDOW_DEFAULT,
        "serverrechten": rbac.serverrechten(ik),
        "csrf": auth.csrf_token(request.cookies.get(auth.SESSION_COOKIE, "")),
        "result": None,
    }
    ctx.update(extra or {})
    return templates.TemplateResponse(request, "admin/companions_map.html", ctx)


@router.get("/companions/kaart", response_class=HTMLResponse)
def companions_map_page(request: Request):
    """Het derde sub-item: een kaart met de laatst gemelde locatie van elke
    companion die er een heeft. Puur weergave -- geen CRUD, geen commando's,
    dus geen ``mag_beheren`` nodig zoals de andere twee sub-items."""
    return _companions_map_page(request)


# --- live gegevens: locatie/gezien/val, voor de auto-ververs op alle drie ----
#
# Vóór ``/companions/{cid}`` gedefinieerd, om dezelfde reden als ``/kaart``
# hierboven: "status.json" is geen geldig getal en zou anders op ``{cid}``
# botsen.
#
# Deze ene route bedient alle drie de pagina's (companions.js/companions_map.js
# roepen hem periodiek aan): de lijst, de detailpagina en de kaart tonen elk hun
# eigen deel van dezelfde ``companions``-tabel, en delen daarom ook dezelfde
# ONDEMAND-poll -- zie companions.poll_now. Die poll is een EXTRA verzoek aan de
# afzender-node, gedaan op het moment dat iemand ECHT naar de data kijkt (een
# open pagina, of zijn auto-ververs-tik), in plaats van te wachten op de
# achtergrondronde van maximaal ``companions.LOC_INTERVAL_S`` oud. De
# hamerbescherming zit in ``poll_now`` zelf: bij meerdere tabbladen of een korte
# ververs-cyclus wordt de meeste van deze aanroepen overgeslagen (ze lezen dan
# gewoon de -- nog steeds vrij verse -- databank).
@router.get("/companions/status.json")
def companions_status_json(request: Request, rep: int | None = None):
    """De actuele locatie/gezien/val van elke companion, als JSON.

    ``rep`` beperkt de ONDEMAND-poll tot die ene afzender-node (de
    companion-detailpagina kent haar eigen node en hoeft de andere niet te
    storen); zonder ``rep`` (lijst, kaart) gaat de poll langs alle
    afzender-nodes, net als de achtergrondronde. De JSON zelf bevat in beide
    gevallen ALLE companions -- de query kost niets en de aanroeper filtert zelf
    op het id dat hem aangaat.
    """
    require_login(request)
    companions.poll_now(only_rep_id=rep)
    now = time.time()
    return JSONResponse({"companions": [_loc(c, now) for c in db.list_companions()]})


# --- het locatie-spoor voor de beheerkaart ------------------------------------
#
# Vóór ``/companions/{cid}`` gedefinieerd is niet nodig (dit pad heeft een extra
# segment en botst dus niet met ``{cid}``), maar het staat hier bij de andere
# JSON-route omdat het dezelfde soort weg is: een aparte GET die de kaart met
# fetch aanroept. De publieke tegenhanger (/loc/<token>/track.json, zonder login)
# staat in routes_public en deelt via companions.TRACK_WINDOWS dezelfde vensters,
# zodat de beheerkant en de deel-link niet uiteen kunnen lopen.
@router.get("/companions/{cid}/track.json")
def companion_track_json(request: Request, cid: int, window: str = ""):
    """De spoorpunten van één companion binnen het gekozen tijdvenster, als JSON.

    Voor de beheerkaart (companions_map.js): klik een marker, kies een venster
    (1u/6u/24u/7d), en de kaart tekent de polyline. Alleen-lezen en achter de
    gewone login -- ``require_login`` en niet ``require_perm``, net als
    ``companions_status_json`` hierboven, want er wordt niets gemuteerd en de
    kaart toont sowieso alleen wat deze installatie al beheert."""
    require_login(request)
    comp = db.companion(cid)
    if not comp:
        raise HTTPException(404, "Onbekende companion")
    since = int(time.time()) - companions.track_window_seconds(window)
    punten = db.companion_track_since(cid, since)
    return JSONResponse({
        "window": window or companions.TRACK_WINDOW_DEFAULT,
        "points": [[p["lat"], p["lon"], p["ts"]] for p in punten],
    })


def _companion_page(request: Request, cid: int, extra: dict | None = None):
    user = require_login(request)
    ik = rbac.load(user)
    comp = db.companion(cid)
    if not comp:
        raise HTTPException(404, "Onbekende companion")
    senders = _sender_reps(ik)
    default_sender = db.qone("SELECT * FROM repeaters WHERE id=?",
                             (comp["sender_repeater_id"],)) if comp["sender_repeater_id"] else None
    reps = db.q("SELECT * FROM repeaters")
    fall_ts = comp["last_escalated_fall_ts"]
    ctx = {
        "site_name": config.SITE_NAME, "user": user, "world": "companions",
        "companions_tab": True,
        "comp": comp,
        # Epoch (companions.last_seen) naar ISO, zodat de pagina dezelfde
        # <time class="reltime">-machinerie kan gebruiken als de rest van de
        # site in plaats van zelf een "geleden"-tekst te bouwen.
        "last_seen_iso": db.iso_from_epoch(comp["last_seen"]),
        # Idem voor de laatst VERWERKTE val (companions.set_companion_fall) --
        # "verwerkt" en niet "gemeld", want zonder toegewezen ontvanger wordt
        # een val hier ook vastgelegd zonder dat er een alarm uitging.
        "last_fall_iso": db.iso_from_epoch(fall_ts),
        "fall_recent": bool(fall_ts) and (time.time() - fall_ts) < FALL_RECENT_S,
        "senders": senders,
        "default_sender": default_sender,
        # De toegewezen valalarm-ontvangers van deze companion, met de naam van
        # hun afzender-node erbij (of "— standaard —" als er geen eigen
        # afzender gekozen is en companions._escalate_fall op de
        # standaardafzender van de companion terugvalt).
        "alerts": db.list_companion_alerts(cid),
        "rep_by_id": {r["id"]: r for r in reps},
        "mag_versturen": _mag_versturen(ik, senders),
        "mag_beheren": rbac.decide(ik, "server.companions"),
        # De keuzelijsten voor de commando-UI, uit companions.py zodat de UI en de
        # validatie dezelfde bron delen -- een beltoon die de firmware niet kent,
        # hoort ook niet in de dropdown te staan.
        "severities": companions.SEVERITIES,
        "gps_modes": companions.GPS_MODES,
        "tune_library": companions.TUNE_LIBRARY,
        "vol_levels": companions.VOL_LEVELS,
        "quiet_actions": companions.QUIET_ACTIONS,
        "rxps_modes": companions.RXPS_MODES,
        "radio_fields": companions.RADIO_FIELDS,
        # De publieke deel-link (companions.share_token): het pad zelf wordt hier
        # opgebouwd zodat de sjabloon alleen hoeft te tonen wat er staat -- leeg
        # betekent "geen link". Een pad en geen volledige URL: de host hangt van
        # de reverse proxy af (meshmanager.net), en het pad is genoeg om er op de
        # pagina een klikbare, kopieerbare link van te maken.
        "share_url": f"/loc/{comp['share_token']}" if comp["share_token"] else "",
        # De tijdvensters voor het spoor op de kaart, uit companions.py zodat de
        # UI en de server dezelfde bron delen.
        "track_windows": companions.track_windows_for_ui(),
        "track_window_default": companions.TRACK_WINDOW_DEFAULT,
        "serverrechten": rbac.serverrechten(ik),
        "csrf": auth.csrf_token(request.cookies.get(auth.SESSION_COOKIE, "")),
        "result": _companion_result(request, comp),
    }
    ctx.update(extra or {})
    return templates.TemplateResponse(request, "admin/companion.html", ctx)


@router.get("/companions/{cid}", response_class=HTMLResponse)
def companion_page(request: Request, cid: int):
    """Eén companion: de commando-knoppen (over het mesh) en de seriële CLI (in de
    browser). De seriële weg staat volledig client-side in companions.js."""
    return _companion_page(request, cid)


@router.post("/companions/add")
def companion_add(request: Request, name: str = Form(""), pubkey: str = Form(""),
                  type: str = Form(""), notes: str = Form(""),
                  sender: str = Form(""), csrf: str = Form(...)):
    """Een companion toevoegen. Serverhandeling: de bestemmingslijst is
    installatiebreed."""
    user = require_perm(request, "server.companions")
    check_csrf(request, csrf)
    naam = str(name or "").strip()
    sleutel = str(pubkey or "").strip()
    if not naam:
        return _redirect("/admin/companions", r="err_name")
    if not companions.valid_pubkey(sleutel):
        return _redirect("/admin/companions", r="err_pubkey")
    if db.companion_by_pubkey(sleutel):
        return _redirect("/admin/companions", r="err_dup")
    db.add_companion(naam, sleutel, type, notes, _keuze_id(sender))
    _noteer(request, user, "server.companions",
            detail=f"companion toegevoegd: {naam} ({sleutel[:12]})")
    return _redirect("/admin/companions", r="added", n=naam)


@router.post("/companions/{cid}/edit")
def companion_edit(request: Request, cid: int, name: str = Form(""),
                   pubkey: str = Form(""), type: str = Form(""),
                   notes: str = Form(""), sender: str = Form(""),
                   csrf: str = Form(...)):
    """Een companion bijwerken. Alle velden tegelijk; zie db.update_companion."""
    user = require_perm(request, "server.companions")
    check_csrf(request, csrf)
    comp = db.companion(cid)
    if not comp:
        raise HTTPException(404, "Onbekende companion")
    naam = str(name or "").strip()
    sleutel = str(pubkey or "").strip()
    if not naam:
        return _redirect(f"/admin/companions/{cid}", r="err_name")
    if not companions.valid_pubkey(sleutel):
        return _redirect(f"/admin/companions/{cid}", r="err_pubkey")
    andere = db.companion_by_pubkey(sleutel)
    if andere and andere["id"] != int(cid):
        return _redirect(f"/admin/companions/{cid}", r="err_dup")
    db.update_companion(cid, naam, sleutel, type, notes, _keuze_id(sender))
    _noteer(request, user, "server.companions",
            detail=f"companion bijgewerkt: {naam} ({sleutel[:12]})")
    return _redirect(f"/admin/companions/{cid}", r="edited")


@router.post("/companions/{cid}/delete")
def companion_delete(request: Request, cid: int, csrf: str = Form(...)):
    """Een companion verwijderen. Alleen de rij in deze lijst -- er staat geen
    apparaat op het spel, en de companion zelf merkt er niets van."""
    user = require_perm(request, "server.companions")
    check_csrf(request, csrf)
    comp = db.companion(cid)
    if not comp:
        raise HTTPException(404, "Onbekende companion")
    naam = comp["name"]
    db.delete_companion(cid)
    _noteer(request, user, "server.companions",
            detail=f"companion verwijderd: {naam}")
    return _redirect("/admin/companions", r="deleted", n=naam)


@router.post("/companions/{cid}/alerts/add")
def companion_alert_add(request: Request, cid: int, recipient: str = Form(""),
                        sender: str = Form(""), label: str = Form(""),
                        csrf: str = Form(...)):
    """Eén valalarm-ontvanger toevoegen. Serverhandeling en geen node-handeling
    ondanks de gekozen afzender: hier wordt nog niets verstuurd, alleen de
    LIJST met wie een toekomstig valalarm krijgt uitgebreid -- dezelfde lijn
    als companion-CRUD hierboven."""
    user = require_perm(request, "server.companions")
    check_csrf(request, csrf)
    comp = db.companion(cid)
    if not comp:
        raise HTTPException(404, "Onbekende companion")
    sleutel = str(recipient or "").strip()
    if not companions.valid_pubkey(sleutel):
        return _redirect(f"/admin/companions/{cid}", r="alert_err_pubkey")
    db.add_companion_alert(cid, sleutel, _keuze_id(sender), label)
    _noteer(request, user, "server.companions",
            detail=f"valalarm-ontvanger toegevoegd bij {comp['name']}: {sleutel[:12]}")
    return _redirect(f"/admin/companions/{cid}", r="alert_added")


@router.post("/companions/{cid}/alerts/{aid}/delete")
def companion_alert_delete(request: Request, cid: int, aid: int,
                           csrf: str = Form(...)):
    """Eén valalarm-ontvanger verwijderen. Raakt alleen de lijst -- geen DM, geen
    apparaat, dus dezelfde serverrechten als toevoegen."""
    user = require_perm(request, "server.companions")
    check_csrf(request, csrf)
    comp = db.companion(cid)
    if not comp:
        raise HTTPException(404, "Onbekende companion")
    verwijderd = db.delete_companion_alert(aid, cid)
    _noteer(request, user, "server.companions",
            detail=(f"valalarm-ontvanger verwijderd bij {comp['name']} (id {aid})"
                    if verwijderd else
                    f"valalarm-ontvanger niet gevonden bij {comp['name']} (id {aid})"))
    return _redirect(f"/admin/companions/{cid}",
                     r="alert_deleted" if verwijderd else "alert_notfound")


def _wants_json(request: Request) -> bool:
    """Of de aanroeper JSON verwacht in plaats van de volledige pagina.

    De commando-knoppen zijn fire-and-forget: companions.js onderschept hun
    submit en post via ``fetch`` met ``Accept: application/json``, zodat de
    knop een korte bevestiging kan tonen ZONDER de pagina te herladen (het
    mesh-antwoord komt toch niet synchroon terug -- zie de commandodocumentatie
    in companions.py). Zonder JavaScript (of een browser die fetch niet doet)
    blijft het gewone formulier werken: dan komt hier geen JSON-Accept binnen en
    valt de route terug op de PRG-redirect hieronder, met dezelfde tekst in de
    resultaatbanner."""
    return "application/json" in request.headers.get("accept", "")


@router.post("/companions/{cid}/cmd")
def companion_cmd(request: Request, cid: int, cmd: str = Form(""),
                  sender: str = Form(""), state: str = Form(""),
                  level: str = Form(""), slot: str = Form(""),
                  name: str = Form(""), range: str = Form(""),
                  action: str = Form(""), mode: str = Form(""), sub: str = Form(""),
                  value: str = Form(""), text: str = Form(""),
                  followapp: str = Form(""), field: str = Form(""),
                  bot: str = Form(""), csrf: str = Form(...)):
    """Een T1000-E-commando naar deze companion sturen, via de Send-DM-weg.

    De knoppen op de companionpagina posten hierheen met ``cmd`` en de bijhorende
    argumenten; ``companions.send_command`` bouwt de DM-tekst (mét ``!``) en
    verstuurt hem vanaf de gekozen afzender. Zonder gekozen afzender valt hij terug
    op de standaardafzender van de companion.

    Dit is een FIRE-AND-FORGET DM: het versturen lukt of niet, maar een eventueel
    antwoord van de companion komt niet via deze route terug (dat loopt via de
    locatie-/valpoll, zie companions.poll_locations). De melding hier gaat dus
    alleen over "is de DM de deur uit", nooit over wat de companion ermee deed --
    zie ``_wants_json`` voor hoe dat met en zonder JavaScript getoond wordt.
    """
    json_out = _wants_json(request)
    comp = db.companion(cid)
    if not comp:
        raise HTTPException(404, "Onbekende companion")
    rid = _keuze_id(sender) or (comp["sender_repeater_id"] or 0)
    rep = db.qone("SELECT * FROM repeaters WHERE id=?", (rid,)) if rid else None
    if rep is None:
        # Geen afzender: geen require_perm mogelijk, en dus ook niet versturen.
        # Een eerlijke melding in plaats van een 500 op een lege keuze.
        require_login(request)
        if json_out:
            return JSONResponse({"ok": False,
                                 "msg": "kies eerst een afzender-node (die de DM verstuurt)"})
        return _redirect(f"/admin/companions/{cid}", r="cmd_nosender")
    user = require_perm(request, "node.instelling.merkbaar", rep)
    check_csrf(request, csrf)
    args = {"state": state, "level": level, "slot": slot, "name": name,
            "range": range, "action": action, "mode": mode, "sub": sub,
            "value": value, "text": text, "followapp": followapp, "field": field}
    # Welke bot-identiteit verstuurt: een expliciete keuze op dit formulier
    # wint, dan de bewaarde voorkeur van de companion, dan de MGMT-standaard
    # van de gekozen afzender-node. Zie companions.resolve_bot.
    bot_keuze = companions.resolve_bot(rep, bot, comp["preferred_bot"])
    res = companions.send_command(rep, comp["pubkey"], cmd, args, bot=bot_keuze)
    _noteer(request, user, "node.instelling.merkbaar", rep=rep,
            detail=(f"companion {comp['name']}: {res['body']} verstuurd"
                    if res["ok"] else f"companion-commando mislukt: {res['error']}"),
            outcome=audit.OK if res["ok"] else audit.MISLUKT)
    msg = (f"commando verzonden naar {comp['name']}: {res['body']}" if res["ok"]
          else f"niet verstuurd — {res['error']}")
    if json_out:
        return JSONResponse({"ok": res["ok"], "msg": msg, "body": res["body"]})
    if res["ok"]:
        return _redirect(f"/admin/companions/{cid}", r="cmd_ok", b=res["body"])
    return _redirect(f"/admin/companions/{cid}", r="cmd_err", e=res["error"])


# --- de generieke Send-DM-tab (tweede sub-item) -------------------------------

def _senddm_page(request: Request, extra: dict | None = None):
    user = require_login(request)
    ik = rbac.load(user)
    senders = _sender_reps(ik)
    ctx = {
        "site_name": config.SITE_NAME, "user": user, "world": "companions",
        "senddm_tab": True,
        "senders": senders,
        "mag_versturen": _mag_versturen(ik, senders),
        "companions": db.list_companions(),
        "serverrechten": rbac.serverrechten(ik),
        "csrf": auth.csrf_token(request.cookies.get(auth.SESSION_COOKIE, "")),
        "result": _senddm_result(request),
    }
    ctx.update(extra or {})
    return templates.TemplateResponse(request, "admin/senddm.html", ctx)


@router.get("/senddm", response_class=HTMLResponse)
def senddm_page(request: Request):
    """De generieke Send-DM-tab: afzender-node -> bestemming -> tekst -> versturen.

    Herbruikbaar en niet aan companions gebonden: de bestemming mag een companion
    zijn óf een gewoon contact van de gekozen afzender-node (opgehaald via
    /senddm/contacts/{rid}.json)."""
    return _senddm_page(request)


@router.get("/senddm/contacts/{rid}.json")
def senddm_contacts(request: Request, rid: int):
    """De bestemmings-kiezer voor een gekozen afzender-node: zijn eigen contacten
    plus de beheerde companions, allebei met een volledige pubkey.

    Een aparte GET omdat dit het netwerk op gaat (de node om zijn /contacts.json
    vragen): pas ophalen als er een afzender gekozen is, niet bij elke weergave van
    de tab. De companions komen uit de databank en kosten niets."""
    rep = _rep_or_404(request, rid)
    require_perm(request, "node.bekijken", rep)
    got = rooms.contacts(rep)
    node_contacts = [{"pubkey": c["k"], "name": c["n"], "type": c.get("t"),
                      "source": "node"}
                     for c in got.get("contacts", [])] if got.get("ok") else []
    comps = [{"pubkey": c["pubkey"], "name": c["name"], "type": c["type"],
              "source": "companion", "id": c["id"]}
             for c in db.list_companions()]
    return JSONResponse({"ok": got.get("ok", False), "error": got.get("error", ""),
                         "contacts": node_contacts, "companions": comps})


@router.get("/companions/bots/{rid}.json")
def companion_bots(request: Request, rid: int):
    """De bot-identiteiten van een afzender-node (``/bots.json``), voor de
    bot-kiezer bij de commando-knoppen en 'Vrij bericht'. Een aparte GET, net
    als ``senddm_contacts`` hierboven: dit gaat het netwerk op (of leest de
    korte cache, zie ``companions.cached_bots``), dus pas ophalen zodra er
    een afzender gekozen is."""
    rep = _rep_or_404(request, rid)
    require_perm(request, "node.bekijken", rep)
    data = companions.cached_bots(rep)
    return JSONResponse({"ok": data["ok"], "error": data["error"],
                         "bots": data["bots"],
                         "default": companions.default_bot_for(rep)})


@router.post("/companions/{cid}/bot")
def companion_bot_set(request: Request, cid: int, bot: str = Form(""),
                      csrf: str = Form(...)):
    """De bewaarde bot-voorkeur van deze companion zetten (of wissen met een
    lege keuze). Serverhandeling zoals de rest van de companion-CRUD: dit
    raakt alleen de LIJST-rij, er wordt hier nog niets verstuurd."""
    user = require_perm(request, "server.companions")
    check_csrf(request, csrf)
    comp = db.companion(cid)
    if not comp:
        raise HTTPException(404, "Onbekende companion")
    db.set_companion_bot(cid, bot or None)
    _noteer(request, user, "server.companions",
            detail=f"bot-voorkeur van {comp['name']} gezet op "
                   f"{bot.strip() or '— standaard —'}")
    return _redirect(f"/admin/companions/{cid}", r="bot_saved")


@router.post("/companions/{cid}/share")
def companion_share_set(request: Request, cid: int, action: str = Form(""),
                        csrf: str = Form(...)):
    """De publieke deel-link van een companion aan- of uitzetten. Serverhandeling
    zoals de rest van de companion-CRUD: dit raakt alleen de LIJST-rij
    (``companions.share_token``), er wordt niets naar een node verstuurd.

    ``action`` is 'on' (een NIEUW token maken -- ook als er al een was, wat de
    oude link meteen ongeldig maakt: dat is de weg om een gelekte link te
    vervangen) of 'off' (intrekken, token terug op NULL). Het token zelf wordt
    HIER gemaakt (``secrets.token_urlsafe(16)``, URL-veilig en niet te raden) en
    niet in de databanklaag, zodat db.py geen kennis van tokenformaat hoeft te
    hebben -- dezelfde lijn als waar de andere geheimen van deze site gemaakt
    worden."""
    user = require_perm(request, "server.companions")
    check_csrf(request, csrf)
    comp = db.companion(cid)
    if not comp:
        raise HTTPException(404, "Onbekende companion")
    if str(action or "").strip().lower() == "off":
        db.set_companion_share_token(cid, None)
        _noteer(request, user, "server.companions",
                detail=f"publieke deel-link ingetrokken voor {comp['name']}")
        return _redirect(f"/admin/companions/{cid}", r="share_off")
    db.set_companion_share_token(cid, secrets.token_urlsafe(16))
    _noteer(request, user, "server.companions",
            detail=f"publieke deel-link aangemaakt voor {comp['name']}")
    return _redirect(f"/admin/companions/{cid}", r="share_on")


def _back_target(back: str) -> str:
    """Waarheen een POST-formulier terugkeert na ``senddm_send``.

    ``back`` zegt WAAR het formulier stond en niet waarheen omgeleid moet
    worden -- dezelfde regel als ``routes_admin.refresh_repeater``: een veld
    met een kant-en-klare URL zou een open redirect zijn zodra iemand het
    formulier naar zijn eigen adres laat wijzen, en dit formulier staat achter
    een login die dat de moeite waard maakt. Hier komen dus alleen de twee
    bestemmingen uit die deze functie zelf kent: de generieke Send-DM-tab (geen
    ``back``, of een onherkenbare waarde), of de companion-pagina waar het
    'Vrij bericht'-formulier vandaan kwam (``companion:<id>``, en alleen als die
    companion ook echt bestaat).

    Zonder deze functie stuurde ELKE inzending van het 'Vrij bericht'-formulier
    op de companion-pagina de bezoeker naar de Send-DM-tab -- weg van de pagina
    waar hij net op stond, zonder dat de nieuwe toestand (er staat hier niets
    te wijzigen, maar wél de bevestiging) op de plek verscheen waar hij hem
    verwachtte.
    """
    back = str(back or "").strip()
    if back.startswith("companion:"):
        try:
            cid = int(back.split(":", 1)[1])
        except ValueError:
            cid = None
        if cid is not None and db.companion(cid):
            return f"/admin/companions/{cid}"
    return "/admin/senddm"


@router.post("/senddm/send")
def senddm_send(request: Request, sender: str = Form(""), pubkey: str = Form(""),
                msg: str = Form(""), back: str = Form(""), bot: str = Form(""),
                csrf: str = Form(...)):
    """Een DM versturen vanaf de gekozen afzender-node naar de gekozen pubkey.

    De bestemming komt als volledige pubkey binnen (de kiezer vult hem in, of hij
    is met de hand getypt). Versturen is een node-handeling op de afzender, want
    het kost zendtijd op díe node -- vandaar require_perm op de afzender-rij.

    ``back`` (zie ``_back_target``) bepaalt de redirect: de generieke Send-DM-tab
    zelf stuurt geen ``back`` mee en komt op zichzelf terug; het 'Vrij
    bericht'-formulier op de companion-pagina stuurt ``companion:<id>`` mee en
    komt op DIE pagina terug, met dezelfde melding als de commando-knoppen
    (``cmd_ok``/``cmd_err``) -- het is tenslotte dezelfde soort DM naar
    dezelfde companion.

    Net als ``companion_cmd`` geeft deze route JSON in plaats van een redirect
    als de aanroeper dat vraagt (``_wants_json``) -- beide formulieren staan in
    companions.js achter dezelfde ``cmd-ajax``-onderschepping.
    """
    json_out = _wants_json(request)
    doel = _back_target(back)
    rid = _keuze_id(sender)
    rep = db.qone("SELECT * FROM repeaters WHERE id=?", (rid,)) if rid else None
    if rep is None:
        require_login(request)
        if json_out:
            return JSONResponse({"ok": False, "msg": "kies eerst een afzender-node"})
        if doel == "/admin/senddm":
            return _redirect(doel, r="nosender")
        return _redirect(doel, r="cmd_nosender")
    user = require_perm(request, "node.instelling.merkbaar", rep)
    check_csrf(request, csrf)
    naar = db.companion_by_pubkey(pubkey)
    # Is de bestemming een beheerde companion, dan telt zijn bewaarde
    # bot-voorkeur mee -- dezelfde volgorde als companion_cmd hierboven.
    bot_keuze = companions.resolve_bot(rep, bot, naar["preferred_bot"] if naar else None)
    res = companions.send_dm(rep, pubkey, msg, bot=bot_keuze)
    label = naar["name"] if naar else (str(pubkey or "").strip()[:12] or "?")
    # Alleen naar wie, nooit de inhoud -- dezelfde regel als bij bot_sendto.
    _noteer(request, user, "node.instelling.merkbaar", rep=rep,
            detail=(f"DM naar {label} vanaf {rep['name']}" if res["ok"]
                    else f"DM mislukt: {res['error']}"),
            outcome=audit.OK if res["ok"] else audit.MISLUKT)
    if json_out:
        msg_out = (f"DM verstuurd naar {label}" if res["ok"]
                  else f"niet verstuurd — {res['error']}")
        return JSONResponse({"ok": res["ok"], "msg": msg_out})
    if doel == "/admin/senddm":
        if res["ok"]:
            return _redirect(doel, r="sent", t=label)
        return _redirect(doel, r="err", e=res["error"])
    if res["ok"]:
        return _redirect(doel, r="cmd_ok", b=msg)
    return _redirect(doel, r="cmd_err", e=res["error"])
