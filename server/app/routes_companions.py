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
import time

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse

from . import audit, auth, companions, config, db, rbac, rooms
# De poort en haar helpers staan in routes_admin en worden hier hergebruikt, niet
# gekopieerd: een tweede kopie van de rechtencontrole is een tweede plek om hem
# verkeerd te krijgen. Zie routes_admin voor de uitleg bij elk.
from .routes_admin import (_noteer, _rep_or_404, check_csrf, require_login,
                           require_perm)
from .templating import templates

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


def _mag_versturen(user, reps) -> dict:
    """Per afzender-rid of deze gebruiker er een DM vanaf mag sturen. Zo kan de
    keuzelijst een node tonen maar de knop uitschakelen met de reden erbij --
    dezelfde lijn als overal: niet verbergen, uitleggen."""
    return {r["id"]: rbac.decide(user, "node.instelling.merkbaar", r) for r in reps}


# --- de companions-lijst + CRUD ----------------------------------------------

def _companions_page(request: Request, extra: dict | None = None):
    user = require_login(request)
    ik = rbac.load(user)
    senders = _sender_reps(ik)
    reps = db.q("SELECT * FROM repeaters")
    ctx = {
        "site_name": config.SITE_NAME, "user": user, "world": "companions",
        "companions_tab": True,
        "companions": db.list_companions(),
        "rep_by_id": {r["id"]: r for r in reps},
        "senders": senders,
        "mag_beheren": rbac.decide(ik, "server.companions"),
        "serverrechten": rbac.serverrechten(ik),
        "csrf": auth.csrf_token(request.cookies.get(auth.SESSION_COOKIE, "")),
        "result": None,
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

    def _loc(c):
        fall_ts = c["last_escalated_fall_ts"]
        out = {"id": c["id"], "name": c["name"], "type": c["type"],
               "lat": c["last_lat"], "lon": c["last_lon"],
               "seen_iso": db.iso_from_epoch(c["last_seen"]),
               # Alleen een recente val kleurt de marker anders -- zie
               # FALL_RECENT_S hierboven voor waarom een val van maanden terug
               # niet voor altijd rood moet blijven.
               "fall_recent": bool(fall_ts) and (now - fall_ts) < FALL_RECENT_S,
               "fall_kind": c["last_fall_kind"], "fall_iso": db.iso_from_epoch(fall_ts)}
        return out

    ctx = {
        "site_name": config.SITE_NAME, "user": user, "world": "companions",
        "companions_map_tab": True,
        "locations": [_loc(c) for c in locaties],
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
        "serverrechten": rbac.serverrechten(ik),
        "csrf": auth.csrf_token(request.cookies.get(auth.SESSION_COOKIE, "")),
        "result": None,
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
        return _companions_page(request, {"result": {
            "ok": False, "msg": "een companion heeft een naam nodig"}})
    if not companions.valid_pubkey(sleutel):
        return _companions_page(request, {"result": {
            "ok": False, "msg": "een volledige pubkey van 64 hex-tekens is vereist"}})
    if db.companion_by_pubkey(sleutel):
        return _companions_page(request, {"result": {
            "ok": False, "msg": "die pubkey staat al in de lijst"}})
    cid = db.add_companion(naam, sleutel, type, notes, _keuze_id(sender))
    _noteer(request, user, "server.companions",
            detail=f"companion toegevoegd: {naam} ({sleutel[:12]})")
    return _companions_page(request, {"result": {
        "ok": True, "msg": f"companion '{naam}' toegevoegd", "cid": cid}})


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
        return _companions_page(request, {"result": {
            "ok": False, "msg": "een companion heeft een naam nodig"}})
    if not companions.valid_pubkey(sleutel):
        return _companions_page(request, {"result": {
            "ok": False, "msg": "een volledige pubkey van 64 hex-tekens is vereist"}})
    andere = db.companion_by_pubkey(sleutel)
    if andere and andere["id"] != int(cid):
        return _companions_page(request, {"result": {
            "ok": False, "msg": "die pubkey staat al bij een andere companion"}})
    db.update_companion(cid, naam, sleutel, type, notes, _keuze_id(sender))
    _noteer(request, user, "server.companions",
            detail=f"companion bijgewerkt: {naam} ({sleutel[:12]})")
    return _companions_page(request, {"result": {
        "ok": True, "msg": f"companion '{naam}' bijgewerkt"}})


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
    return _companions_page(request, {"result": {
        "ok": True, "msg": f"companion '{naam}' verwijderd"}})


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
        return _companion_page(request, cid, {"result": {
            "ok": False,
            "msg": "een volledige pubkey van 64 hex-tekens is vereist voor de ontvanger"}})
    aid = db.add_companion_alert(cid, sleutel, _keuze_id(sender), label)
    _noteer(request, user, "server.companions",
            detail=f"valalarm-ontvanger toegevoegd bij {comp['name']}: {sleutel[:12]}")
    return _companion_page(request, cid, {"result": {
        "ok": True, "msg": "ontvanger toegevoegd", "aid": aid}})


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
    return _companion_page(request, cid, {"result": {
        "ok": bool(verwijderd),
        "msg": "ontvanger verwijderd" if verwijderd else "onbekende ontvanger"}})


@router.post("/companions/{cid}/cmd")
def companion_cmd(request: Request, cid: int, cmd: str = Form(""),
                  sender: str = Form(""), state: str = Form(""),
                  level: str = Form(""), slot: str = Form(""),
                  name: str = Form(""), range: str = Form(""),
                  action: str = Form(""), mode: str = Form(""), sub: str = Form(""),
                  value: str = Form(""), text: str = Form(""), csrf: str = Form(...)):
    """Een T1000-E-commando naar deze companion sturen, via de Send-DM-weg.

    De knoppen op de companionpagina posten hierheen met ``cmd`` en de bijhorende
    argumenten; ``companions.send_command`` bouwt de DM-tekst (mét ``!``) en
    verstuurt hem vanaf de gekozen afzender. Zonder gekozen afzender valt hij terug
    op de standaardafzender van de companion.
    """
    comp = db.companion(cid)
    if not comp:
        raise HTTPException(404, "Onbekende companion")
    rid = _keuze_id(sender) or (comp["sender_repeater_id"] or 0)
    rep = db.qone("SELECT * FROM repeaters WHERE id=?", (rid,)) if rid else None
    if rep is None:
        # Geen afzender: geen require_perm mogelijk, en dus ook niet versturen.
        # Een eerlijke melding in plaats van een 500 op een lege keuze.
        require_login(request)
        return _companion_page(request, cid, {"result": {
            "ok": False, "msg": "kies eerst een afzender-node (die de DM verstuurt)"}})
    user = require_perm(request, "node.instelling.merkbaar", rep)
    check_csrf(request, csrf)
    args = {"state": state, "level": level, "slot": slot, "name": name,
            "range": range, "action": action, "mode": mode, "sub": sub,
            "value": value, "text": text}
    res = companions.send_command(rep, comp["pubkey"], cmd, args)
    _noteer(request, user, "node.instelling.merkbaar", rep=rep,
            detail=(f"companion {comp['name']}: {res['body']} verstuurd"
                    if res["ok"] else f"companion-commando mislukt: {res['error']}"),
            outcome=audit.OK if res["ok"] else audit.MISLUKT)
    return _companion_page(request, cid, {"result": {
        "ok": res["ok"],
        "msg": (f"verstuurd: {res['body']}" if res["ok"]
                else f"niet verstuurd — {res['error']}")}})


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
        "result": None,
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


@router.post("/senddm/send")
def senddm_send(request: Request, sender: str = Form(""), pubkey: str = Form(""),
                msg: str = Form(""), csrf: str = Form(...)):
    """Een DM versturen vanaf de gekozen afzender-node naar de gekozen pubkey.

    De bestemming komt als volledige pubkey binnen (de kiezer vult hem in, of hij
    is met de hand getypt). Versturen is een node-handeling op de afzender, want
    het kost zendtijd op díe node -- vandaar require_perm op de afzender-rij."""
    rid = _keuze_id(sender)
    rep = db.qone("SELECT * FROM repeaters WHERE id=?", (rid,)) if rid else None
    if rep is None:
        require_login(request)
        return _senddm_page(request, {"result": {
            "ok": False, "msg": "kies eerst een afzender-node"}})
    user = require_perm(request, "node.instelling.merkbaar", rep)
    check_csrf(request, csrf)
    res = companions.send_dm(rep, pubkey, msg)
    naar = db.companion_by_pubkey(pubkey)
    label = naar["name"] if naar else (str(pubkey or "").strip()[:12] or "?")
    # Alleen naar wie, nooit de inhoud -- dezelfde regel als bij bot_sendto.
    _noteer(request, user, "node.instelling.merkbaar", rep=rep,
            detail=(f"DM naar {label} vanaf {rep['name']}" if res["ok"]
                    else f"DM mislukt: {res['error']}"),
            outcome=audit.OK if res["ok"] else audit.MISLUKT)
    return _senddm_page(request, {"result": {
        "ok": res["ok"],
        "msg": (f"DM verstuurd naar {label}" if res["ok"]
                else f"niet verstuurd — {res['error']}")}})
