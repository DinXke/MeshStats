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

from . import (audit, auth, clocksync, commanding, compare, config, db,
               discovery, firmware, metrics, monitors, mqtt_ingest, nodeconfig,
               pktfilter, ratelimit, rbac, retention, sensornode, sensorpush,
               sweepsched, tsdb)
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


# --- de poort ----------------------------------------------------------------
#
# Elke schrijvende beheerroute gaat hierdoorheen, en er is een test
# (test_rechten.py) die dat controleert door de routes van deze router af te
# lopen. Een controle per route die met de hand overgeschreven wordt, is een
# controle die bij de volgende route vergeten wordt; dit is de ene plek.

def require_perm(request: Request, action: str, rep=None) -> str:
    """Ingelogd én bevoegd, anders 403. Een weigering komt in het audittrail.

    De weigering wordt hier vastgelegd en niet bij de aanroeper, om dezelfde
    reden als hierboven: een geweigerde poging is juist de rij die je later wil
    terugvinden, en dat mag niet afhangen van of iemand eraan dacht hem te
    loggen.
    """
    user = require_login(request)
    besluit = rbac.decide(user, action, rep)
    if not besluit.allowed:
        audit.log(user, action, rep=rep, outcome=audit.GEWEIGERD,
                  detail=besluit.reason, ip=_ip(request))
        raise HTTPException(403, besluit.reason)
    return user


def require_server_admin(request: Request, waarvoor: str) -> str:
    """Ingelogd én SERVERbeheerder, anders 403. De weigering komt in het trail.

    Bestaat naast ``require_perm`` omdat er handelingen zijn waarvan de zwaarte
    niet aan een NODE hangt maar aan deze installatie. De eerste is het
    beheeradres: dat veld bepaalt waarheen de server verbindt, en hij stuurt daar
    de inloggegevens naartoe die ELKE node openen. ``node.beheeradres`` is een
    delegeerbaar recht per node; wie dat krijgt, hoort daarmee niet de sleutel van
    de hele vloot te kunnen omleiden. Zie firmware.check_target voor het gat en
    de afweging.

    Waarom niet een nieuwe handeling in ACTIONS: dit is geen recht dat je uitdeelt
    maar een grens die niet te delegeren is. Een handeling met de tekst "een adres
    invullen waar het vlootwachtwoord heen gaat" zou aan iemand toegekend kunnen
    worden, en dat is precies wat hier niet mag.
    """
    user = require_login(request)
    ik = rbac.load(user)
    if not getattr(ik, "is_superuser", False):
        reden = (f"{waarvoor} mag alleen een serverbeheerder: de server stuurt "
                 f"de inloggegevens die elke node openen naar dat adres")
        audit.log(user, "node.beheeradres", outcome=audit.GEWEIGERD,
                  detail=reden, ip=_ip(request))
        raise HTTPException(403, reden)
    return user


def _rep_or_404(request: Request, rid: int):
    """De repeater, of 404 -- maar niet vóór er iemand ingelogd is.

    De login-controle staat hier en niet pas bij ``require_perm``, en dat is geen
    dubbelop. De routes moeten de rij ophalen vóór ze de rechten kunnen wegen
    (het recht gaat immers over déze node), en zonder deze regel zou een
    onbekende bezoeker aan het verschil tussen een 404 en een omleiding naar het
    inlogscherm kunnen aflezen welke node-id's bestaan. Dat is een klein lek, en
    het gaat juist over de verborgen nodes.
    """
    require_login(request)
    row = db.qone("SELECT * FROM repeaters WHERE id=?", (rid,))
    if not row:
        raise HTTPException(404, "Onbekende repeater")
    return row


def _ip(request) -> str:
    """Het adres voor in het audittrail, of leeg.

    Even weerbaar als audit.log zelf, en om dezelfde reden: het schrijven van een
    regel mag de handeling waar hij over gaat nooit laten stranden. Een verzoek
    zonder herkenbaar adres levert dan een regel zonder adres op, en niet een
    firmware-upgrade die halverwege afbreekt.
    """
    try:
        return ratelimit.client_ip(request)
    except Exception:  # noqa: BLE001
        return ""


def _noteer(request, user: str, action: str, *, rep=None,
            outcome: str = audit.OK, detail: str = "") -> None:
    audit.log(user, action, rep=rep, outcome=outcome, detail=detail, ip=_ip(request))


# De schrijvende beheerroutes die géén rechtencontrole hebben, met de reden
# erbij. Ze staan hier zodat test_rechten.py de rest kan afdwingen: elke POST
# onder /admin gaat door require_perm, tenzij hij in deze lijst staat. Een lijst
# van uitzonderingen die je moet bijwerken is precies de bedoeling -- iemand die
# een route toevoegt en de controle vergeet, komt langs een rode test.
ROUTES_ZONDER_RECHTENCONTROLE = {
    # Het inlogscherm zelf. Er is nog geen gebruiker om iets over te beslissen.
    "login",
    # Het eigen wachtwoord wijzigen. Elke ingelogde gebruiker mag dat, en de
    # controle die telt staat in de functie zelf: het huidige wachtwoord moet
    # kloppen. Er is geen node en geen rol die hier iets aan zou toevoegen.
    "change_password",
}


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
        # Een uitgezet account faalt ná de wachtwoordcontrole en niet ervoor: zo
        # kost een uitgezette naam evenveel tijd als een bestaande, en verraadt
        # het inlogscherm niet welke accounts er nog zijn. De melding blijft
        # bewust dezelfde als bij een verkeerd wachtwoord.
        if ok and row["disabled"]:
            audit.log(row["username"], "login", outcome=audit.GEWEIGERD,
                      detail="account staat uit", ip=ip)
            ok = False
    else:
        auth.verify_dummy(password)  # equal cost, so timing reveals no usernames
        ok = False
    if not ok:
        # Zonder het wachtwoord en zonder te zeggen of de naam bestond: wat hier
        # vastligt is dát er een mislukte poging was, met welk adres en op welke
        # naam. Dat is genoeg om een aanval te herkennen en te weinig om er een
        # gebruikerslijst uit af te leiden.
        audit.log(username.strip()[:64] or "onbekend", "login",
                  outcome=audit.MISLUKT, detail="ongeldige inloggegevens", ip=ip)
        wait = ratelimit.record_failure(ip, username)
        if wait:
            return _login_page(
                request, nonce,
                f"Ongeldige inloggegevens — te veel pogingen, wacht {wait} s.",
                "login.invalid_throttled", {"n": wait}, status=429, retry_after=wait)
        return _login_page(request, nonce, "Ongeldige inloggegevens",
                           "login.invalid", status=401)

    ratelimit.record_success(ip, username)
    audit.log(row["username"], "login", outcome=audit.OK, ip=ip)
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
    ik = rbac.load(user)
    alle = db.q("SELECT * FROM repeaters ORDER BY sort_order, name")
    # Wat deze gebruiker mag zien, en niet wat er staat. Filteren en niet grijs
    # maken: zie rbac.zichtbare_nodes voor waarom die twee hier niet hetzelfde
    # zijn.
    repeaters = rbac.zichtbare_nodes(ik, alle)
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
        "rollen": {r["id"]: rbac.rol_op_node(ik, r) for r in repeaters},
        "serverrechten": rbac.serverrechten(ik),
        # Hoeveel nodes er zijn waar deze gebruiker niets over mag weten. Niet
        # welke: dat zou de lijst zijn die hij niet hoort te zien. Het getal
        # staat er zodat "waar is die node gebleven" een antwoord heeft.
        "onzichtbaar": len(alle) - len(repeaters),
        # Een repeater die vanzelf uit een bericht ontstaat komt sinds de
        # vertrouwensgrens verborgen binnen (zie db.get_or_create_repeater).
        # Verborgen binnenkomen mag, ongemerkt binnenkomen niet: zonder dit getal
        # bovenaan staat hij ergens tussen de groepen te wachten op een beslissing
        # waarvan niemand weet dat ze genomen moet worden.
        "hidden_repeaters": sum(1 for r in repeaters if not r["is_public"]),
        # Nodes waarvan wij ons deel van de configuratie niet af hebben. Bovenaan
        # en niet pas bij een knopklik, omdat dit vandaag twee keer een hele weg
        # dichtzette zonder dat er iets aan te zien was: een leeg beheeradres
        # blokkeert zowel de HTTP-schrijfweg als de weg die bij de monitor
        # aanklopt, en beide meldden dat pas als iemand het probeerde. Dezelfde
        # reden als het MQTT-gezondheidsblok: een ontbrekende voorwaarde hoort te
        # staan waar je hem ziet voordat je hem nodig hebt.
        "no_host_reps": [r for r in repeaters
                         if not (r["ota_host"] or "").strip()
                         and routes[r["id"]]["level"] == commanding.LEVEL_FULL],
        # Lege groepen weglaten: een kopje "Unmanaged — 0" met niets eronder is
        # ruis, en de uitleg bij zo'n kopje gaat dan over niemand.
        "groups": [g for g in groups if g["reps"]],
        # Openstaande alarmen, per node en in totaal. Bovenaan de nodelijst en
        # niet weggestopt op de pagina van één node: een alarm is een trap en het
        # hele punt van een trap is dat je hem ziet zonder ernaar te zoeken. Eén
        # query voor alle nodes -- zie db.alerts_open_by_node.
        "alerts_open": db.alerts_open_by_node(),
        "alerts_total": db.alerts_open_count(),
        "alerts_recent": db.alerts_recent(12),
        "csrf": auth.csrf_token(request.cookies.get(auth.SESSION_COOKIE, "")),
    })


@router.get("/compare", response_class=HTMLResponse)
def compare_page(request: Request):
    """Alle repeaters naast elkaar, met de afwijkers gemarkeerd."""
    return _compare_page(request)


def _compare_page(request: Request, extra: dict | None = None):
    """De tabel, eventueel met de uitslag van een schrijfactie erbij.

    Een eigen weergave naast /admin en niet een kolom erbij, omdat het een andere
    vraag beantwoordt. /admin vraagt "hoe staat deze node ervoor" en groepeert
    daarom op beheerniveau; hier is de vraag "welke node loopt uit de pas", en die
    kun je alleen stellen als de waarden naast elkaar staan.

    De kolomkeuze volgt de afspraak van het pakketarchief -- een URL-parameter
    wint van wat er bewaard is -- maar bewaart serverzijdig in plaats van in
    localStorage. Reden: beheer is een gedeelde taak. Wie een tabel inricht die
    laat zien dat één node uit de pas loopt, wil dat de volgende die inlogt
    hetzelfde ziet, en niet dat die keuze in één browser blijft hangen.
    """
    user = require_login(request)
    repeaters = db.q("SELECT * FROM repeaters ORDER BY sort_order, name")
    broker = mqtt_ingest.can_publish()

    gekozen = request.query_params.get("cols", "")
    if not gekozen:
        gekozen = db.get_setting(compare.SETTING_KEY, "")

    voorlopig = compare.build(repeaters, None, broker_connected=broker)
    keys = [k for k, _ in voorlopig["keuzes"]]
    kolommen = compare.parse_columns(gekozen, keys)
    tabel = compare.build(repeaters, kolommen, broker_connected=broker)

    return templates.TemplateResponse(request, "admin/compare.html", {
        "site_name": config.SITE_NAME, "user": user, "world": "nodes",
        "compare_tab": True,
        "tabel": tabel,
        "csrf": auth.csrf_token(request.cookies.get(auth.SESSION_COOKIE, "")),
        "bewerken": _compare_editor(request.query_params.get("edit", ""), tabel),
        # De vaste kolommen komen uit de repeatertabel en niet uit de CLI, dus
        # daar valt niets aan te zetten -- het sjabloon moet dat verschil kennen
        # om geen potloodje te tekenen bij een waarde die geen knop verdient.
        "builtin_keys": compare.BUILTIN_KEYS,
        # Wat er van afstand nooit gezet wordt. Ook hier, want dit is een tweede
        # knop naar dezelfde schrijfactie.
        "cfg_no_remote": nodeconfig.NO_REMOTE,
        "cfg_no_remote_reason": nodeconfig.NO_REMOTE_REASON,
        "cfg_result": None,
        **(extra or {}),
    })


def _compare_editor(spec: str, tabel: dict) -> dict | None:
    """Het bewerkvenster onder de tabel, of None.

    Eén bewerker die de tabel aanstuurt, en niet een invoerveld in elk vakje.
    Bij twintig nodes en zes kolommen zijn dat honderdtwintig formulieren op één
    pagina, elk met hun eigen bevestiging -- en juist de bevestiging is wat er
    dan onleesbaar wordt. De risicoklassen blijven onverkort gelden; ze staan
    hier alleen op één plek in beeld in plaats van honderdtwintig keer.

    ``edit`` heeft de vorm ``<rid>:<sleutel>``. Klopt er iets niet aan, dan geen
    bewerker in plaats van een foutmelding: dit komt uit een URL die iemand
    geplakt of bewaard kan hebben, en een tabel die niet meer laadt omdat een
    node verwijderd is, is erger dan een tabel zonder bewerker.
    """
    if ":" not in (spec or ""):
        return None
    rid_raw, _, key = spec.partition(":")
    if not rid_raw.isdigit():
        return None
    rij = next((r for r in tabel["rijen"] if r["rep"]["id"] == int(rid_raw)), None)
    if rij is None or not key:
        return None

    # params_for kiest zelf de bron die bij de gekozen weg hoort: de node over
    # HTTP, of de tabel die hij over MQTT meestuurde. Hier hoeft dat niet bekend
    # te zijn, en dat is het hele punt van die functie.
    lijst = nodeconfig.params_for(rij["rep"], rij["cfg"])
    param = next((p for p in lijst.get("params") or [] if p.get("key") == key), None)
    return {
        "rij": rij, "key": key, "param": param, "lijst": lijst,
        "huidig": rij["waarden"].get(key),
    }


@router.post("/compare/write")
def compare_write(request: Request, rid: int = Form(...), key: str = Form(...),
                  value: str = Form(""), confirm: str = Form(""),
                  rf: str = Form(""), rb: str = Form(""), rs: str = Form(""),
                  rc: str = Form(""), csrf: str = Form(...)):
    """Eén instelling zetten vanuit de vergelijkingstabel.

    Dezelfde weg als vanaf de nodepagina -- letterlijk dezelfde functie -- zodat
    de risicoklassen, de grenzen en het teruglezen hier vanzelf gelden. Een
    tweede schrijfpad naast nodeconfig.write() zou een tweede plek zijn waar die
    drempels kunnen ontbreken, en dat is precies de fout die je pas ontdekt als
    er een node stil is.
    """
    check_csrf(request, csrf)
    rep = _rep_or_404(request, rid)
    # Dezelfde risicogestuurde poort als de nodepagina, en met opzet niet iets
    # lichters omdat dit "maar" een tabelcel is: het is dezelfde schrijfactie op
    # dezelfde node. Een tweede, soepelere ingang naar hetzelfde is precies hoe
    # een drempel in de praktijk verdwijnt.
    require_perm(request, {
        nodeconfig.RISK_PLAIN: "node.instelling.gewoon",
        nodeconfig.RISK_WRITES: "node.instelling.merkbaar",
    }.get(nodeconfig.risk_of(rep, key.strip()), "node.instelling.ingrijpend"), rep)
    if key.strip() == "radio" and (rf or rb or rs or rc):
        value = " ".join(v.strip() for v in (rf, rb, rs, rc))
    result = nodeconfig.write(rep, key.strip(), value.strip(), confirm)
    return _compare_page(request, {"cfg_result": result, "cfg_rid": rid})


@router.post("/compare/columns")
def compare_columns(request: Request, csrf: str = Form(...),
                    col: list[str] = Form(default=[])):
    """De kolomkeuze bewaren.

    Vinkjes, dus wat niet meekomt is uitgezet -- en een lege keuze is dan ook een
    geldig verzoek, geen fout. ``compare.parse_columns`` maakt er bij het tonen
    weer de standaardkolommen van, want een tabel zonder kolommen is geen tabel;
    dat hoort daar en niet hier, zodat een handmatig leeggemaakte instelling
    hetzelfde uitpakt als een instelling die nooit gezet is.
    """
    check_csrf(request, csrf)
    # Een weergavekeuze en geen handeling op een node: hij hoort bij de
    # serverinstellingen, want de kolomkeuze staat serverbreed opgeslagen en
    # geldt dus voor iedereen die de tabel opent.
    require_perm(request, "server.instellingen")
    db.set_setting(compare.SETTING_KEY, ",".join(c.strip() for c in col if c.strip()))
    return RedirectResponse("/admin/compare", status_code=303)


@router.get("/discovery", response_class=HTMLResponse)
def discovery_page(request: Request):
    """Telemetrie ophalen van nodes waarvoor we geen inloggegevens hebben."""
    return _discovery_page(request)


def _discovery_page(request: Request, extra: dict | None = None):
    user = require_login(request)
    afzender = discovery.sender()
    host = str((afzender["rep"]["ota_host"] if afzender["rep"] is not None else "") or "")

    # Beoordeel eerst wat er van eerdere uitvragingen geworden is, dan pas tonen.
    if host:
        try:
            discovery.verify(host)
        except Exception:                                  # noqa: BLE001
            pass

    lijst = discovery.heard(host) if host else {"ok": False, "error": "",
                                                "entries": [], "monitored": []}
    banen = discovery.jobs()
    # Wat er werkelijk binnenkwam, per uitgevraagde node: onze eigen tabellen,
    # want de afzender publiceert de uitkomst over MQTT onder de naam van het
    # doelwit. Dat is dezelfde weg als bij een gewone gemonitorde repeater.
    resultaten = {}
    for sleutel in banen:
        rij = db.find_repeater(sleutel)
        if rij is None:
            continue
        resultaten[sleutel] = {
            "rep": rij,
            "metrics": db.latest_for(rij["id"]),
        }

    ctx = {
        "site_name": config.SITE_NAME, "user": user, "world": "nodes",
        "discovery_tab": True,
        "sender": afzender, "sender_host": host,
        "heard": lijst,
        "cost": discovery.cost(host) if host else None,
        "poll_iv": discovery.poll_interval(host) if host else None,
        "jobs": banen, "results": resultaten,
        "csrf": auth.csrf_token(request.cookies.get(auth.SESSION_COOKIE, "")),
        "outcome": None,
    }
    ctx.update(extra or {})
    return templates.TemplateResponse(request, "admin/discovery.html", ctx)


@router.post("/discovery/probe")
def discovery_probe(request: Request, key: str = Form(...), label: str = Form(""),
                    csrf: str = Form(...)):
    """Eén node uitvragen. De beheerder wijst aan; er is geen ronde over alles."""
    afzender = discovery.sender()
    if afzender["rep"] is None:
        require_login(request)
        return _discovery_page(request, {"outcome": {
            "ok": False, "msg": "er is geen node die dit kan versturen"}})
    # De bevoegdheid hangt aan de AFZENDER en niet aan het doelwit, en dat is de
    # eerlijke plaatsing: het doelwit is niet van ons en staat misschien niet eens
    # in onze tabellen, terwijl wat hier verandert de monitorlijst van onze eigen
    # node is. 'Een merkbare instelling schrijven' dus -- het kost zendtijd op een
    # gedeelde band en het legt een terugkerende verhouding aan.
    require_perm(request, "node.instelling.merkbaar", afzender["rep"])
    check_csrf(request, csrf)
    host = str(afzender["rep"]["ota_host"] or "")
    uit = discovery.probe(host, key, label)
    return _discovery_page(request, {"outcome": {
        "ok": uit["ok"],
        "msg": uit["msg"] or f"uitgevraagd via {afzender['rep']['name']}",
        "key": uit["key"]}})


@router.post("/discovery/interval")
def discovery_interval(request: Request, secs: int = Form(...), csrf: str = Form(...)):
    """Het pollinterval van de afzender zetten.

    Per MONITOR en niet per node, want de firmware kent geen ronde van één node.
    Zie discovery.poll_interval() voor waarom een per-node-veld hier een knop zou
    zijn die iets belooft wat de firmware niet kan doen.
    """
    afzender = discovery.sender()
    if afzender["rep"] is None:
        require_login(request)
        raise HTTPException(409, "Geen afzender beschikbaar")
    require_perm(request, "node.instelling.merkbaar", afzender["rep"])
    check_csrf(request, csrf)
    uit = discovery.set_poll_interval(str(afzender["rep"]["ota_host"] or ""), secs)
    return _discovery_page(request, {"outcome": {
        "ok": uit["ok"], "msg": uit["error"] or "pollinterval aangepast"}})


@router.post("/discovery/forget")
def discovery_forget(request: Request, key: str = Form(...), csrf: str = Form(...)):
    afzender = discovery.sender()
    if afzender["rep"] is None:
        require_login(request)
        raise HTTPException(409, "Geen afzender beschikbaar")
    require_perm(request, "node.instelling.merkbaar", afzender["rep"])
    check_csrf(request, csrf)
    uit = discovery.forget(str(afzender["rep"]["ota_host"] or ""), key)
    return _discovery_page(request, {"outcome": {
        "ok": uit["ok"], "msg": uit["error"] or "uit de monitorlijst gehaald"}})


def _keuzes(*waarden) -> list:
    """De aangevinkte kandidaten als rij-ids, in de opgegeven volgorde.

    Alles wat geen getal is valt weg in plaats van een fout op te leveren. Dat
    dekt de lege keuze uit het formulier ("— geen —") en ook een aanroep die de
    velden niet alle drie meegeeft, wat de tests in deze map doen wanneer ze een
    route rechtstreeks aanroepen. Een 500 op een leeg selectievakje zou een
    foutmelding voor niemand zijn.
    """
    uit = []
    for waarde in waarden:
        tekst = str(waarde or "").strip()
        if tekst.isdigit():
            uit.append(int(tekst))
    return uit


@router.get("/monitors", response_class=HTMLResponse)
def monitors_page(request: Request):
    """Welke repeater welke node mag uitvragen: per node en per nodegroep.

    Eén pagina en niet een blok per node, om dezelfde reden als de
    vergelijkingstabel: dit is een vraag over de verzameling. "Welke node heeft
    nog geen beheerder" en "welke lijst komt nooit voorbij de eerste kandidaat"
    stel je over alle nodes tegelijk, en per node doorklikken zou dat verstoppen.
    """
    return _monitors_page(request)


def _monitors_page(request: Request, extra: dict | None = None):
    user = require_login(request)
    reps = db.q("SELECT * FROM repeaters ORDER BY sort_order, name")
    zichtbaar = rbac.zichtbare_nodes(user, reps) if hasattr(rbac, "zichtbare_nodes") else reps
    # Kandidaten uit de ZICHTBARE nodes en niet uit alle: een monitor die je niet
    # mag zien, hoort niet in een keuzelijst te staan waar je zijn naam uit kunt
    # lezen. Dat de rechten per node gelden maakt dit geen detail -- de keuzelijst
    # was de enige plek op deze pagina waar een naam van buiten je bereik langskwam.
    mogelijk = monitors.possible(zichtbaar)
    groepen = rbac.nodegroepen()

    ctx = {
        "site_name": config.SITE_NAME, "user": user, "world": "nodes",
        "monitors_tab": True,
        "rows": monitors.overview(zichtbaar, mogelijk),
        "possible": mogelijk,
        "max_candidates": monitors.MAX_CANDIDATES,
        "groups": [{"group": g,
                    "monitors": monitors._group_rows(g["id"]),
                    "members": len(rbac.leden("node", g["id"]))}
                   for g in groepen],
        # De uitslag van de laatste geplande poging per node, zodat een lijst die
        # nooit voorbij de eerste kandidaat komt op te merken valt.
        "attempts": {r["pubkey_prefix"]: sweepsched.entry(r["pubkey_prefix"])
                     for r in zichtbaar},
        "csrf": auth.csrf_token(request.cookies.get(auth.SESSION_COOKIE, "")),
        "outcome": None, "rights": None,
    }
    ctx.update(extra or {})
    return templates.TemplateResponse(request, "admin/monitors.html", ctx)


@router.post("/repeaters/{rid}/monitors")
def save_node_monitors(request: Request, rid: int, m1: str = Form(""),
                       m2: str = Form(""), m3: str = Form(""), csrf: str = Form(...)):
    """De geordende lijst voor één node. Leeg betekent: terug naar de waarneming.

    Dezelfde klasse als het uitvraagschema, en om dezelfde reden: wie hier iets
    zet bepaalt van wélke node er zendtijd afgaat, elke ronde opnieuw.
    """
    rep = db.qone("SELECT * FROM repeaters WHERE id=?", (rid,))
    if not rep:
        raise HTTPException(404, "Onbekende repeater")
    user = require_perm(request, "node.schema", rep)
    check_csrf(request, csrf)

    gekozen, geweigerd = [], []
    for waarde in _keuzes(m1, m2, m3):
        monitor = db.qone("SELECT * FROM repeaters WHERE id=?", (waarde,))
        probleem = monitors.check(rep, monitor)
        if probleem:
            # Weigeren en niet stil weglaten: een lijst die korter blijkt dan wat
            # iemand koos, zonder dat er staat waarom, is precies de lijst die
            # liegt waar dit tegen gebouwd is.
            geweigerd.append(f"{monitor['name'] if monitor else waarde}: {probleem}")
            continue
        gekozen.append(monitor["id"])

    if geweigerd:
        return _monitors_page(request, {"outcome": {
            "ok": False, "rid": rid,
            "msg": "niet opgeslagen — " + "; ".join(geweigerd)}})

    monitors.set_for_node(rid, gekozen)
    audit.log(user, "node.schema", rep=rep,
              detail=f"monitorlijst: {len(gekozen)} kandidaat(en)" if gekozen
              else "monitorlijst gewist (terug naar de waarneming)")
    return _monitors_page(request, {"outcome": {
        "ok": True, "rid": rid,
        "msg": f"{len(gekozen)} kandidaat(en) opgeslagen" if gekozen
        else "lijst gewist; de waarneming geldt weer"}})


@router.post("/nodegroups/{gid}/monitors")
def save_group_monitors(request: Request, gid: int, m1: str = Form(""),
                        m2: str = Form(""), m3: str = Form(""), csrf: str = Form(...)):
    """Hetzelfde per nodegroep, zodat twintig nodes niet twintig keer hoeven.

    Een groepslijst wordt NIET per lid gecontroleerd: of een monitor een node kan
    bereiken hangt van die node af, en een groep kan leden hebben die verschillen.
    De pagina van elke node laat daarna zien wat er voor hém uitkomt -- de groep
    is een voorkeur, niet een belofte per lid.
    """
    groep = db.qone("SELECT * FROM node_groups WHERE id=?", (gid,))
    if not groep:
        raise HTTPException(404, "Onbekende nodegroep")
    user = require_perm(request, "server.instellingen")
    check_csrf(request, csrf)

    gekozen = _keuzes(m1, m2, m3)
    monitors.set_for_group(gid, gekozen)
    audit.log(user, "server.instellingen",
              detail=f"monitorlijst nodegroep {groep['name']}: {len(gekozen)}")
    return _monitors_page(request, {"outcome": {
        "ok": True, "gid": gid, "msg": f"{len(gekozen)} kandidaat(en) voor de groep"}})


@router.post("/repeaters/{rid}/monitors/rights")
def check_monitor_rights(request: Request, rid: int, mid: int = Form(...),
                         csrf: str = Form(...)):
    """Vraag de monitor zelf of hij bij deze node binnenkomt.

    Apart van het opslaan omdat dit het netwerk op gaat: op een pagina met twintig
    nodes zou dit twintig HTTP-verzoeken per weergave zijn. Wie het wil weten,
    vraagt het.
    """
    rep = db.qone("SELECT * FROM repeaters WHERE id=?", (rid,))
    monitor = db.qone("SELECT * FROM repeaters WHERE id=?", (mid,))
    if not rep or not monitor:
        raise HTTPException(404, "Onbekende repeater")
    require_perm(request, "node.bekijken", rep)
    check_csrf(request, csrf)
    return _monitors_page(request, {"rights": {
        "rid": rid, "monitor": monitor, "note": monitors.rights_note(rep, monitor)}})


@router.get("/server", response_class=HTMLResponse)
def server_page(request: Request):
    """Wereld 2: alles wat deze installatie configureert en geen apparaat raakt.

    Voorbehouden aan een serverbeheerder, en dat is geen strengheid maar wat de
    pagina is: elk formulier erop is een serverhandeling, en die zijn per
    definitie niet per node of per groep toe te kennen (zie rbac.decide). Een
    halve pagina tonen zou een lijst tokens en accounts laten zien aan iemand die
    er niets mee mag.
    """
    user = require_perm(request, "server.instellingen")
    repeaters = db.q("SELECT * FROM repeaters ORDER BY sort_order, name")
    tokens = db.q("SELECT * FROM tokens WHERE revoked=0 ORDER BY created_at")
    layout = metrics.parse_layout(db.get_setting("layout"))
    # Eén keer opgehaald: de groepenlijsten worden twee keer gebruikt (de lijst
    # zelf en de ledentabellen ernaast), en twee keer dezelfde vraag stellen is
    # twee keer een kans dat er tussendoor iets verandert.
    ug = rbac.gebruikersgroepen()
    ng = rbac.nodegroepen()
    # nieuw token éénmalig tonen via kortlevende cookie (niet via de URL)
    new_token = request.cookies.get("mm_new_token")
    resp = templates.TemplateResponse(request, "admin/server.html", {
        "site_name": config.SITE_NAME, "user": user, "world": "server",
        "serverrechten": rbac.serverrechten(user),
        # Gebruikersbeheer hoort onder Server en site, en de gegevens ervoor
        # komen hier binnen in plaats van op een eigen pagina: het is een sectie
        # tussen de tokens en de bewaartermijn, want het gaat over deze
        # installatie en niet over een apparaat.
        "gebruikers": rbac.gebruikers(),
        "gebruikersgroepen": ug,
        "nodegroepen": ng,
        "groepsleden": {g["id"]: rbac.leden("user", g["id"]) for g in ug},
        "nodegroepsleden": {g["id"]: rbac.leden("node", g["id"]) for g in ng},
        "toekenningen": rbac.toekenningen(),
        "rollen": rbac.ROLLEN, "rol_uitleg": rbac.ROL_UITLEG,
        "klasse_uitleg": rbac.KLASSE_UITLEG,
        "handelingen": rbac.ACTIONS,
        "losse_nodes": rbac.nodes_zonder_groep(repeaters),
        "alle_repeaters": repeaters,
        "audit": audit.recent(40),
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
        # De uitleesronde voor sensornodes. Zichtbaar hier en niet alleen in het
        # logboek, om dezelfde reden als bij de kloksynchronisatie hieronder: een
        # ronde die uitstaat mag er niet uitzien als een die nooit iets vond.
        "sensor_poll": sensornode.status_summary(),
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
    user = require_perm(request, "server.instellingen")
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
    # Welke velden dit formulier meebracht, niet wat erin stond: de waarden staan
    # op de pagina en het trail hoeft geen tweede kopie van de instellingen te
    # worden. Wat je later wil weten is wie er aan de bewaartermijn zat.
    gewijzigd = [naam for naam, waarde in (
        ("heartbeat_min", heartbeat_min), ("retention_days", retention_days),
        ("packet_retention_days", packet_retention_days),
        ("packet_max_rows", packet_max_rows), ("db_max_mb", db_max_mb),
        ("history_ranges", history_ranges)) if waarde is not None]
    _noteer(request, user, "server.instellingen", detail=", ".join(gewijzigd))
    return RedirectResponse("/admin/server", status_code=303)


@router.post("/layout")
def save_layout(request: Request, layout: str = Form(...), csrf: str = Form(...)):
    user = require_perm(request, "server.instellingen")
    check_csrf(request, csrf)
    import json as _json
    validated = metrics.parse_layout(layout)
    db.set_setting("layout", _json.dumps(validated))
    _noteer(request, user, "server.instellingen", detail="weergave van de publieke site")
    return RedirectResponse("/admin/server", status_code=303)


def _dispatch(rep, command: str) -> str:
    """Stuur één opdracht langs elke weg die openstaat. Geeft terug welke.

    Elke weg wordt bewandeld en niet de eerste de beste: ze zijn niet
    uitwisselbaar. De MQTT-weg bereikt de node zelf en alleen als die op dit
    ogenblik aan de broker hangt; de wachtrij bereikt een poller die de repeater
    over LoRa uitvraagt en ook werkt als de node zijn WiFi uit heeft staan; de
    eigen API van een sensornode werkt juist alleen als die WiFi er is. Wie er
    meer dan één heeft, heeft er meer dan één iets aan; wie er geen heeft, hoort
    dat te zien en niet "gestart" te lezen.

    Terug komt 'ip', 'mqtt', 'queued', 'both' of 'none' -- wat de pagina daarna
    zegt hangt daaraan en niet aan wat we hoopten dat er zou gebeuren.

    'ip' staat apart en niet onder 'both', en dat is met opzet: bij de andere
    wegen betekent een geslaagde verzending "er is iets vertrokken" en niets over
    de aankomst. Bij deze weg is het antwoord al binnen op het moment dat deze
    functie terugkeert -- de cijfers staan in de databank, niet in een wachtrij.
    Dat verschil hoort de pagina te kunnen zeggen.
    """
    route = commanding.describe(rep)
    # De eigen API eerst, want die levert een ANTWOORD op en niet een verzoek.
    # Voor een node die zo binnenkomt bestaan de andere twee wegen niet, dus dit
    # is geen voorrang die iets anders wegdrukt -- het is de enige weg die er is.
    if route["ip_api"]["ever"]:
        if command == "settings":
            gelezen = sensornode.values(str(rep["sensor_host"] or ""))
            if gelezen["ok"] and gelezen["values"]:
                # prune=False: dit antwoord gaat alleen over de velden die
                # /cfg.json draagt, en een rij waar het niets over te zeggen had
                # mag het niet weggooien. Dezelfde regel als bij de eigen ronde
                # van een node -- zie db.upsert_cli_settings.
                db.upsert_cli_settings(int(rep["id"]), gelezen["values"], prune=False)
                return "ip"
        else:
            if sensornode.poll(rep)["ok"]:
                return "ip"
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


def _uitkomst(weg: str) -> str:
    """De uitslag van _dispatch als uitkomst voor het audittrail.

    'none' is geen fout van de gebruiker maar wel een handeling die niets
    bereikte, en dat is precies het geval waarvan je later wil weten dat het zich
    voordeed. 'both', 'mqtt' en 'ip' zijn geslaagd; 'queued' staat er als
    'deels', want er is een verzoek neergelegd en nog niets gebeurd.
    """
    if weg == "none":
        return audit.MISLUKT
    if weg == "queued":
        return audit.DEELS
    return audit.OK


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
    row = _rep_or_404(request, rid)
    user = require_perm(request, "node.uitvragen", row)
    check_csrf(request, csrf)
    outcome = _dispatch(row, "status")
    _noteer(request, user, "node.uitvragen", rep=row, detail=f"status, weg: {outcome}",
            outcome=_uitkomst(outcome))
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
    # Alleen ingelogd, en met opzet geen rechtencontrole: deze route doet niets
    # dan een 303 naar de pagina die de controle wél doet. Hier al weigeren zou
    # betekenen dat een oude bladwijzer een ander antwoord geeft dan de nieuwe
    # URL -- en dat de node bestaat zou dan uit een 404 blijken op een adres dat
    # alleen een doorverwijzing is.
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

    De rij komt via ``_rep_or_404`` binnen en niet met een eigen query: die
    controleert eerst of er iemand ingelogd is, zodat het verschil tussen een 404
    en een omleiding naar het inlogscherm niet verraadt welke node-id's bestaan.
    """
    rep = _rep_or_404(request, rid)
    user = require_perm(request, "node.bekijken", rep)
    requested = request.query_params.get("requested", "")
    ch_names = db.channel_names_for(rid)
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
    cfg = nodeconfig.cfg_route(rep, broker_connected=broker)
    cfg_params = nodeconfig.params_for(rep, cfg)
    # Gaat het schrijven over LoRa, dan blijft de uitslag op de MONITOR staan en
    # niet hier. Dat is met opzet -- zie nodeconfig.mesh_state -- en het heeft dit
    # gevolg: de pagina haalt hem op bij het tonen, zodat een herlading een
    # handeling van een halve minuut alsnog laat zien in plaats van hem te
    # verliezen omdat de browser niet is blijven wachten.
    cfg_mesh = (nodeconfig.mesh_state(cfg["host"])
                if cfg["can"] and cfg["transport"] == "mesh"
                else {"ok": False, "error": "", "job": {}})

    # Voor een doorgestuurde node: hoe komt zijn monitor bij hem binnen, en werkt
    # dat. Alleen ophalen als er een monitor met een beheeradres is, anders staat
    # elke paginaweergave op een node te wachten die er niet is.
    # Het pakketfilter. De stand komt uit twee bronnen en dat is met opzet:
    # ``fstate`` is wat de node in zijn laatste statistiekenbericht meldde -- die
    # is er altijd, ook voor een node zonder IP-pad, en hij is misschien een paar
    # minuten oud. ``flive`` wordt alleen opgehaald als er een schrijfweg is, en
    # is de stand van nu inclusief de regeltabellen die niet in het bericht
    # passen. Wie iets gaat wijzigen krijgt de tweede te zien; wie alleen kijkt
    # heeft aan de eerste genoeg en hoeft er geen node voor wakker te maken.
    froute = pktfilter.filter_route(rep)
    fstate = db.filter_state_for(rid)
    flive = (pktfilter.state(froute["host"], pktfilter.FILTER_PEEK_TIMEOUT_S)
             if froute["can"] else {"ok": False, "error": "", "filter": {}})
    relay = db.find_repeater(rep["source_prefix"]) if cfg["relayed"] else None
    relay_host = str((relay["ota_host"] if relay else "") or "")
    rights = (nodeconfig.rights_for(relay_host, rep["pubkey_prefix"])
              if relay_host else None)
    # De eigen API van de node, als die er is. Twee blikken en ze antwoorden twee
    # vragen. ``sensor_last`` is wat de laatste ronde opleverde -- dat kost niets,
    # het staat in het geheugen. ``sensor_acl`` gaat wél het netwerk op, en alleen
    # als de weg er is: dat is de toegangslijst van de node, en die bevat het
    # antwoord op de vraag waarom de MESH-weg naar hem niet werkt. Staat de
    # sleutel van zijn monitor er niet in en heeft die monitor geen wachtwoord,
    # dan is 'LOGIN_NOANSWER' geen storing maar een weigering -- en dat verschil
    # is waar iemand anders een middag op verliest.
    sensor_route = (nodeconfig._route_sensor(rep)
                    if str(rep["sensor_host"] or "").strip() else None)
    sensor_acl = (sensornode.acl(sensor_route["host"])
                  if sensor_route is not None and sensor_route["can"]
                  else {"ok": False, "error": "", "data": {}})
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
        # De vorige schrijfactie van de monitor, als die weg gebruikt wordt.
        "cfg_mesh": cfg_mesh,
        "cfg_mesh_steps": nodeconfig.MESH_STEPS,
        # De laatste schrijfactie die deze node over MQTT meldde. Zelfde rol als
        # cfg_mesh hierboven: bij die weg blijft de uitslag op de monitor staan
        # en bij deze komt hij mee in een statistiekenbericht, dus in beide
        # gevallen is er iets te tonen dat niet uit dit verzoek komt.
        "cfg_mqtt": nodeconfig.cfgset_state(rep["source_prefix"] or ""),
        # Drie dingen over het filter, en ze beantwoorden drie vragen. 'Staat er
        # een filter aan en wat gooit het weg' (uit het laatste bericht, altijd
        # beschikbaar), 'wat zijn de regels precies' (van de node zelf, alleen
        # als hij bereikbaar is) en 'mag ik eraan komen' (de route). Ze door
        # elkaar halen levert precies één soort fout op: een pagina die beweert
        # dat er geen filter aanstaat omdat de node net niet antwoordde.
        "filter_route": froute,
        "filter_live": flive,
        "filter_seen": pktfilter.summarise(fstate),
        "filter_types": pktfilter.TYPE_NAMES,
        # Welke van de twee wegen zijn monitor gebruikt, en waar het op stukloopt.
        # Een sweep die op stilte uitloopt heeft drie oorzaken die er van hieraf
        # identiek uitzien, en dat verschil is waar iemand een half uur op
        # verliest.
        "rights": rights,
        "relay": relay,
        # Het schema, en wat de vorige ronde opleverde. Zonder dat tweede is een
        # schema een belofte die je niet kunt narekenen -- en met de drie
        # celtoestanden erbij is "nooit gevraagd" straks iets anders dan "het
        # schema staat uit".
        "sweep_hours": sweepsched.interval_hours(rep),
        "sweep_next": sweepsched.next_due_secs(rep),
        "sweep_last": sweepsched.entry(rep["pubkey_prefix"]),
        "sweep_status": sweepsched.status(),
        "cfg_params": cfg_params,
        # Wat er van afstand nooit gezet wordt. Als lijst naar het sjabloon en
        # niet als redenering daarin, om dezelfde reden als bij ``rechten``: een
        # sjabloon dat zelf redeneert is een tweede plek waar het antwoord
        # vandaan komt. De weigering zelf staat in nodeconfig.write() én in de
        # firmware; dit is alleen wat de pagina erover zegt.
        "cfg_no_remote": nodeconfig.NO_REMOTE,
        "cfg_no_remote_reason": nodeconfig.NO_REMOTE_REASON,
        "cfg_transport_text": nodeconfig.TRANSPORT_TEXT,
        # Waarom een weg afvalt, in het Nederlands. Als tabel naar het
        # sjabloon en niet als drie if-takken daarin: dezelfde zin hoort op
        # elke plek te staan waar de reden opduikt, en een sjabloon dat hem
        # zelf formuleert is de tweede plek waar hij anders gaat luiden.
        "cfg_blocker_text": nodeconfig.BLOCKER_TEXT,
        # Gegroepeerd op risicoklasse, want dat is waar de bediening op stuurt:
        # gewoon opslaan, bevestigen, of de naam overtypen. De groepen komen uit
        # de firmware mee zodat de indeling niet op twee plaatsen bestaat.
        "cfg_groups": [
            (risk, [q for q in cfg_params.get("params") or []
                    if int(q.get("risk") or 1) == risk])
            for risk in (nodeconfig.RISK_PLAIN, nodeconfig.RISK_WRITES,
                         nodeconfig.RISK_CUTOFF)
        ],
        # Wat de laatste uitleesronde vond, zodat elk veld zijn huidige waarde
        # kan tonen in plaats van leeg te beginnen. Een leeg veld naast een
        # parameter nodigt uit tot gokken.
        "cfg_now": {r["param"]: r["value"] for r in rows if r["value"] is not None},
        # En wat er mág, uit dezelfde beweging. De sjabloon vraagt
        # ``rechten['node.klok']`` en redeneert niet zelf: een sjabloon dat zelf
        # redeneert is een tweede plek waar het antwoord vandaan komt, en de
        # eerste keer dat die twee het oneens zijn belooft een knop iets wat de
        # route weigert.
        "rechten": rbac.rechten_op(user, rep),
        # De kanalen die deze node werkelijk gestuurd heeft, met de namen die er
        # al bij staan. Uit de metingen zelf en niet uit een lijst die iemand
        # vooraf moest invullen: welke kanalen een node heeft, weet alleen die
        # node. Een kanaal verschijnt hier dus zodra er één meting van binnen is.
        # ``herkomst`` staat erbij en dat is meer dan aardigheid: het zegt of deze
        # naam overgenomen is uit /status.json of door iemand getypt, en dat is
        # precies het verschil dat bepaalt of de volgende ronde eraan mag komen.
        # Zonder die kolom ziet een beheerder een naam staan zonder te weten of
        # hij hem morgen nog terugvindt.
        "channels": [
            dict(c, name=(ch_names[c["channel"]]["name"]
                          if c["channel"] in ch_names else ""),
                 unit=(ch_names[c["channel"]]["unit"] or ""
                       if c["channel"] in ch_names else ""),
                 herkomst=(ch_names[c["channel"]]["source"]
                           if c["channel"] in ch_names else ""))
            for c in metrics.channels_seen(db.latest_for(rid))
        ],
        # De derde weg: de eigen API van deze node. ``sensor_route`` is None voor
        # elke node zonder adres -- dan tekent het sjabloon de sectie helemaal
        # niet, in plaats van een blok met vier uitgeschakelde knoppen op elke
        # repeaterpagina van de site.
        "sensor_route": sensor_route,
        "sensor_last": sensornode.last(rid),
        "sensor_acl": sensor_acl,
        "sensor_interval_s": sensornode.INTERVAL_S,
        "sensor_enabled": sensornode.ENABLED,
        "sensor_region_fields": sensornode.REGION_FIELDS,
        "sensor_no_readback": sensornode.NO_READBACK,
        # Leeg tenzij er zojuist een handeling langs deze weg gebeurd is; die
        # routes geven de pagina terug met hun antwoord erin in plaats van een
        # 303 die het kwijt is. Zelfde opzet als bij ``cfg_result``.
        "sensor_result": None,
        # De gebeurtenis-push: staat de weg open op deze server, en acht de
        # stiltebewaking deze node op dit moment stil. De waarnemingen zelf
        # (laatste push, hartslag, tellers) staan als push_*-kolommen in ``rep``.
        "push_enabled": sensorpush.enabled(),
        "push_stil": sensorpush.is_stil(rid),
        "mijn_rol": rbac.rol_op_node(user, rep),
        "serverrechten": rbac.serverrechten(user),
        # De alarmen van deze node, en hoeveel er nog openstaan. Een eigen lijst
        # naast het audittrail hieronder, want ze antwoorden op twee
        # verschillende vragen: het trail zegt wat WIJ met deze node gedaan
        # hebben, de alarmen zeggen wat DE NODE ons gemeld heeft.
        "alerts": db.alerts_for(rid, 30),
        "alerts_open": db.alerts_open_count(rid),
        # Wat er met déze node gebeurd is, en door wie. Op de nodepagina en niet
        # alleen op de serverpagina: de vraag "wie heeft deze node geflasht"
        # stel je terwijl je naar die node kijkt.
        "audit": audit.recent(15, rep_id=rid),
        **extra,
    })


@router.post("/repeaters/{rid}/config")
def write_config(request: Request, rid: int, key: str = Form(...),
                 value: str = Form(""), confirm: str = Form(""),
                 rf: str = Form(""), rb: str = Form(""), rs: str = Form(""),
                 rc: str = Form(""), csrf: str = Form(...)):
    """Eén instelling van deze node zetten en meteen teruglezen.

    Synchroon, anders dan de firmware-upgrade: dit is één CLI-aanroep over het
    lokale netwerk en die is in tienden van seconden klaar. Een achtergrondtaak
    met een toestand om te pollen zou hier machinerie zijn om niets.

    Geeft de pagina terug in plaats van een 303, want het antwoord bevat wat er
    ná afloop in de node staat -- en dat is soms iets anders dan wat er gevraagd
    is. Zie nodeconfig.write() voor de twee gemeten redenen waarom.
    """
    check_csrf(request, csrf)
    rep = _rep_or_404(request, rid)
    # Het recht hangt aan de risicoklasse van déze parameter, en niet aan een
    # kaal "mag deze gebruiker schrijven". Zo is "wel de zendtijd bijstellen,
    # niet aan de radio komen" een rol en geen uitzondering. Kent de node de
    # parameter niet, dan is de zwaarste klasse de veilige aanname: een
    # onbekende parameter als ongevaarlijk behandelen is precies hoe je een node
    # van de lucht haalt.
    _risk_perm = {
        nodeconfig.RISK_PLAIN: "node.instelling.gewoon",
        nodeconfig.RISK_WRITES: "node.instelling.merkbaar",
    }.get(nodeconfig.risk_of(rep, key.strip()), "node.instelling.ingrijpend")
    require_perm(request, _risk_perm, rep)
    # 'radio' is de enige parameter die uit vier getallen bestaat, en die vier
    # krijgen elk hun eigen invoerveld met hun eigen minimum en maximum. Eén
    # tekstveld waarin je "869.525 250 11 5" moet typen is precies het soort veld
    # waarin een tikfout een node van de lucht haalt.
    if key.strip() == "radio" and (rf or rb or rs or rc):
        value = " ".join(v.strip() for v in (rf, rb, rs, rc))
    result = nodeconfig.write(rep, key.strip(), value.strip(), confirm)
    return _node_page(request, rid, cfg_result=result)


@router.post("/repeaters/{rid}/filter")
def write_filter(request: Request, rid: int, cmd: str = Form(""),
                 arg1: str = Form(""), arg2: str = Form(""),
                 confirm: str = Form(""), csrf: str = Form(...)):
    """Eén filterregel van deze node zetten en de nieuwe stand teruglezen.

    Synchroon en met de pagina als antwoord, om dezelfde redenen als bij
    ``write_config``: het is één aanroep over het lokale netwerk, en het antwoord
    is meer dan gelukt-of-niet -- er staat de volledige stand ná afloop in.

    Het recht hangt aan wat de regel aanricht en niet aan een kaal "mag deze
    gebruiker aan het filter komen". Een snelheidslimiet bijstellen en een
    pakkettype helemaal dichtzetten zijn twee handelingen die er in het
    formulier hetzelfde uitzien, en die hoor je aan verschillende mensen te
    kunnen geven. De huidige stand gaat mee in de weging voor het ene geval
    waarin dat uitmaakt: aanzetten terwijl er al een categorale regel klaarstaat
    is de klik die het verkeer stilzet, niet de klik die de regel maakte.
    """
    check_csrf(request, csrf)
    rep = _rep_or_404(request, rid)
    # De regel komt in stukken binnen: het vaste deel in een verborgen veld, de
    # getallen in invoervelden met hun eigen min en max. Zo staat er in het
    # formulier geen tekstveld waarin je een hele commandoregel kunt typen --
    # dat zou een CLI op een webpagina zijn, en dan is de risicoweging een
    # kwestie van hoe iemand toevallig spelt.
    cmd = " ".join(deel.strip() for deel in (cmd, arg1, arg2) if deel.strip())
    route = pktfilter.filter_route(rep)
    huidig = (pktfilter.state(route["host"]).get("filter") or {}) if route["can"] else {}
    _risk_perm = {
        pktfilter.RISK_PLAIN: "node.filter.gewoon",
        pktfilter.RISK_WRITES: "node.filter.merkbaar",
    }.get(pktfilter.risk_of(cmd, huidig), "node.filter.ingrijpend")
    user = require_perm(request, _risk_perm, rep)

    result = pktfilter.write(rep, cmd, confirm, huidig)
    # In het audittrail de zin en niet de commandoregel: "GRP_TXT (05) helemaal
    # niet meer doorsturen" is over een half jaar nog te lezen, "hops 05 0" niet.
    _noteer(request, user, _risk_perm, rep=rep,
            outcome=audit.OK if result["ok"] else audit.MISLUKT,
            detail=f"{result['wat']} -- {result['msg']}"[:400])
    return _node_page(request, rid, filter_result=result)


@router.post("/repeaters/{rid}/schedule")
def set_schedule(request: Request, rid: int, sweep_hours: int = Form(0),
                 csrf: str = Form(...)):
    """Het uitvraagschema van één node zetten. 0 is uit.

    Een klasse zwaarder dan de knop die één ronde start, en dat is geen
    strengheid om de strengheid: die knop kost één keer zendtijd, dit kost hem
    elke dag opnieuw op een band die van iedereen is. Wie het aanzet legt een
    terugkerende last op andermans mesh.
    """
    rep = db.qone("SELECT * FROM repeaters WHERE id=?", (rid,))
    if not rep:
        raise HTTPException(404, "Onbekende repeater")
    user = require_perm(request, "node.schema", rep)
    check_csrf(request, csrf)

    # Eén uur is de ondergrens en een maand de bovengrens. Korter dan een uur
    # heeft geen betekenis naast een minimumafstand van kwartieren, en langer dan
    # een maand is hetzelfde als uit -- met het verschil dat 'uit' eerlijk is over
    # wat het is. Klemmen en niet weigeren: dit veld komt uit een keuzelijst, en
    # een 422 op een waarde die niemand kan typen is een foutmelding voor niemand.
    uren = max(0, min(24 * 30, int(sweep_hours or 0)))
    db.execute("UPDATE repeaters SET sweep_hours=? WHERE id=?", (uren or None, rid))
    audit.log(user, "node.schema", rep=rep,
              detail=f"uitvraagschema op {uren} uur" if uren else "uitvraagschema uit")
    return RedirectResponse(f"/admin/repeaters/{rid}", status_code=303)


@router.post("/repeaters/{rid}/settings/refresh")
def repeater_settings_refresh(request: Request, rid: int, csrf: str = Form(...)):
    """Vraag de CLI-instellingen op: rechtstreeks aan de node en/of via een poller."""
    rep = _rep_or_404(request, rid)
    user = require_perm(request, "node.uitvragen", rep)
    check_csrf(request, csrf)
    outcome = _dispatch(rep, "settings")
    _noteer(request, user, "node.uitvragen", rep=rep,
            detail=f"instellingen, weg: {outcome}", outcome=_uitkomst(outcome))
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
    rep = _rep_or_404(request, rid)
    user = require_perm(request, "node.klok", rep)
    check_csrf(request, csrf)
    result = clocksync.sync_now(rep)
    _noteer(request, user, "node.klok", rep=rep, detail=result["outcome"],
            outcome=audit.OK if result["outcome"] == "sent" else audit.MISLUKT)
    # De wachttijd reist mee in de URL, want zonder dat getal is "te snel" een
    # mededeling waar niemand iets mee kan.
    suffix = f"&wait={result['wait_min']}" if result["outcome"] == "too_soon" else ""
    return RedirectResponse(
        f"/admin/repeaters/{rid}?clock={result['outcome']}{suffix}",
        status_code=303)


# --- de eigen API van een sensornode -----------------------------------------
#
# Zes routes, en ze staan hier bij elkaar omdat ze allemaal over hetzelfde
# vervoermiddel gaan: HTTP naar de node zelf. Wat ze NIET met elkaar delen is de
# handeling, en dus ook niet het recht -- het adres invullen is iets anders dan
# de node herstarten, en dat verschil hoort in ACTIONS te staan en niet in een
# gedeelde "sensor mag"-vlag.
#
# Wat hier met opzet NIET staat is het schrijven van een instelling. Dat loopt
# door ``write_config`` hierboven, langs ``nodeconfig.write()``, met al zijn
# drempels -- de weigeringslijst, de grenzen, de risicoklassen, de bevestiging.
# Een tweede ingang hier zou een tweede plek zijn waar een drempel kan ontbreken.

@router.post("/repeaters/{rid}/sensor")
def save_sensor_host(request: Request, rid: int, sensor_host: str = Form(""),
                     csrf: str = Form(...)):
    """Het adres van de eigen API van deze node zetten of wissen.

    ``node.beheeradres`` en dezelfde controle als bij ``ota_host``: dit veld
    wordt door een mens getypt en 'file:///etc' erin zou deze server zijn eigen
    bestanden laten lezen.

    Een apart veld naast ``ota_host`` en niet hetzelfde -- zie de uitleg bij de
    kolom in db.py. Kort: ``ota_host`` betekent "daar staat onze
    repeaterfirmware", en dat is een belofte die over deze node niet waar is.
    """
    rep = _rep_or_404(request, rid)
    host = (sensor_host or "").strip()
    # Dezelfde grens als bij het beheeradres, en om dezelfde reden: hierheen gaan
    # MM_FW_NODE_USER/MM_FW_NODE_PASS, en die openen elke node. Zie
    # firmware.check_target en require_server_admin.
    if host:
        require_server_admin(request, "een adres voor de eigen API invullen")
    user = require_perm(request, "node.beheeradres", rep)
    check_csrf(request, csrf)
    if host:
        try:
            firmware._url(host, "/status.json")
        except ValueError as exc:
            raise HTTPException(422, f"Adres onbruikbaar: {exc}") from exc
    db.set_sensor_host(rid, host, by_admin=bool(host))
    # Het adres staat niet in het trail. Deze repo is publiek en het trail is
    # exporteerbaar; een intern adres hoort daar niet in, en de vraag die het
    # trail beantwoordt is wie eraan zat.
    _noteer(request, user, "node.beheeradres", rep=rep,
            detail=("adres van de eigen API gezet" if host
                    else "adres van de eigen API gewist"))
    return RedirectResponse(f"/admin/repeaters/{rid}#eigen-api", status_code=303)


@router.post("/repeaters/{rid}/sensor/poll")
def sensor_poll(request: Request, rid: int, csrf: str = Form(...)):
    """Nu uitlezen in plaats van bij de volgende ronde.

    Dezelfde functie die de ronde gebruikt en geen tweede weg ernaast: wat deze
    knop doet is het wachten overslaan, niet iets anders doen.
    """
    rep = _rep_or_404(request, rid)
    user = require_perm(request, "node.uitvragen", rep)
    check_csrf(request, csrf)
    uitslag = sensornode.poll(rep)
    _noteer(request, user, "node.uitvragen", rep=rep,
            detail=f"eigen API uitgelezen: {uitslag['metrics']} metingen",
            outcome=audit.OK if uitslag["ok"] else audit.MISLUKT)
    return _node_page(request, rid, sensor_result={"soort": "poll", **uitslag})


@router.post("/repeaters/{rid}/sensor/advert")
def sensor_advert(request: Request, rid: int, zerohop: str = Form(""),
                  csrf: str = Form(...)):
    """De node zich laten melden op het mesh, mesh-breed of alleen aan zijn buren."""
    rep = _rep_or_404(request, rid)
    user = require_perm(request, "node.uitvragen", rep)
    check_csrf(request, csrf)
    uitslag = sensornode.send_advert(rep, zerohop=bool(zerohop))
    _noteer(request, user, "node.uitvragen", rep=rep,
            detail=f"advert gestuurd over de eigen API ({uitslag['cmd']})",
            outcome=audit.OK if uitslag["ok"] else audit.MISLUKT)
    return _node_page(request, rid, sensor_result={"soort": "advert", **uitslag})


@router.post("/repeaters/{rid}/sensor/clock")
def sensor_clock(request: Request, rid: int, csrf: str = Form(...)):
    """De klok van deze node op de servertijd zetten, over IP.

    Het oordeel of dat mag komt uit ``clocksync.check_clock`` en niet van hier;
    zie ``sensornode.set_clock``. Deze functie zoekt de node op en geeft de
    uitslag door.
    """
    rep = _rep_or_404(request, rid)
    user = require_perm(request, "node.klok", rep)
    check_csrf(request, csrf)
    uitslag = sensornode.set_clock(rep)
    _noteer(request, user, "node.klok", rep=rep,
            detail=f"klok over de eigen API: {uitslag['outcome']}",
            outcome=audit.OK if uitslag["ok"] else audit.MISLUKT)
    return _node_page(request, rid, sensor_result={"soort": "clock", **uitslag})


@router.post("/repeaters/{rid}/sensor/reboot")
def sensor_reboot(request: Request, rid: int, confirm: str = Form(""),
                  csrf: str = Form(...)):
    """De node herstarten.

    Met de naam van de node overgetypt als bevestiging, en dat is geen
    zwaarwichtigheid: een herstart is onschuldig en de klik op de VERKEERDE node
    is dat niet. Een ja/nee-vraag beschermt tegen twijfel en niet tegen de
    verkeerde regel -- dezelfde afweging als bij de firmwarepagina en bij
    ``nodeconfig.confirmation_for``.
    """
    rep = _rep_or_404(request, rid)
    user = require_perm(request, "node.herstart", rep)
    check_csrf(request, csrf)
    if confirm.strip() != str(rep["name"] or ""):
        return _node_page(request, rid, sensor_result={
            "soort": "reboot", "ok": False, "error": (
                f"typ de naam van de node ({rep['name']}) precies over om een "
                f"herstart te bevestigen"), "reply": ""})
    uitslag = sensornode.reboot(rep)
    _noteer(request, user, "node.herstart", rep=rep,
            detail="herstart aangevraagd over de eigen API",
            outcome=audit.OK if uitslag["ok"] else audit.MISLUKT)
    return _node_page(request, rid, sensor_result={"soort": "reboot", **uitslag})


@router.post("/repeaters/{rid}/sensor/region")
def sensor_region(request: Request, rid: int, veld: str = Form(""),
                  naam: str = Form(""), confirm: str = Form(""),
                  csrf: str = Form(...)):
    """Een regioveld zetten en vastleggen.

    ``node.instelling.merkbaar`` met een bevestiging erbij. Een scope bepaalt wie
    dit verkeer doorstuurt, dus een verkeerde waarde kan een node stil buiten het
    bereik van zijn buren zetten -- en 'stil' is hier het probleem. Het verschil
    met de radio, en de reden dat dit een bevestiging is en geen weigering: deze
    fout is van hieraf terug te draaien, want de node blijft over IP bereikbaar
    en dit commando kan opnieuw.
    """
    rep = _rep_or_404(request, rid)
    user = require_perm(request, "node.instelling.merkbaar", rep)
    check_csrf(request, csrf)
    if confirm.strip() != "ja":
        return _node_page(request, rid, sensor_result={
            "soort": "region", "ok": False, "error":
                "een regiowijziging moet bevestigd worden", "set": "", "saved": ""})
    uitslag = sensornode.set_region(rep, veld.strip(), naam)
    _noteer(request, user, "node.instelling.merkbaar", rep=rep,
            detail=f"regio {veld.strip()} -> {naam.strip()} over de eigen API",
            outcome=audit.OK if uitslag["ok"] else audit.MISLUKT)
    return _node_page(request, rid, sensor_result={"soort": "region", **uitslag})


# --- alarmen ------------------------------------------------------------------
#
# Bevestigen en niets anders. Er is geen route die een alarm VERWIJDERT, en dat
# is een keuze: een alarm dat je kunt wegklikken zonder spoor is een alarm dat
# achteraf niet meer na te vertellen is. Opruimen doet de bewaartermijn, samen
# met de rest van de historiek.
#
# ``node.uitvragen`` en geen eigen handeling: dit is de lichtste klasse die over
# één node gaat, er verandert niets op het apparaat, en er gaat geen pakket de
# lucht in. Wie een node mag uitvragen, mag zeggen dat hij zijn melding gezien
# heeft.

@router.post("/repeaters/{rid}/alerts/ack")
def ack_alerts(request: Request, rid: int, alert_id: int = Form(default=0),
               csrf: str = Form(...)):
    """Eén alarm bevestigen, of alle openstaande van deze node.

    Twee handelingen in één route omdat het dezelfde handeling is met een andere
    omvang. En de omvang moet er zijn: een node die een uur onbereikbaar was
    levert tientallen regels op, en die één voor één wegklikken betekent dat
    niemand het doet -- en dan zegt de badge over een week nog steeds iets over
    vorige dinsdag.
    """
    rep = _rep_or_404(request, rid)
    user = require_perm(request, "node.uitvragen", rep)
    check_csrf(request, csrf)
    if alert_id:
        gelukt = db.ack_alert(int(alert_id))
        detail = f"alarm {int(alert_id)} bevestigd" if gelukt else                  f"alarm {int(alert_id)} was al bevestigd of bestaat niet"
    else:
        aantal = db.ack_alerts_for(rid)
        gelukt = aantal > 0
        detail = f"{aantal} alarm(en) bevestigd"
    _noteer(request, user, "node.uitvragen", rep=rep, detail=detail,
            outcome=audit.OK if gelukt else audit.DEELS)
    return RedirectResponse(f"/admin/repeaters/{rid}#alarmen", status_code=303)


@router.post("/cli_params")
def save_cli_params(request: Request, cli_params: str = Form(...),
                    csrf: str = Form(...), rid: int = Form(default=0)):
    """De parameterlijst, die voor alle repeaters tegelijk geldt.

    ``rid`` is er alleen nog voor een pagina die vóór de herindeling geopend
    werd: het formulier stond toen op de pagina van één repeater en stuurde zijn
    id mee om terug te kunnen keren. Die waarde wordt genegeerd -- de lijst was
    ook toen al globaal, en dat is precies waarom ze hier is komen staan.
    """
    user = require_perm(request, "server.instellingen")
    check_csrf(request, csrf)
    cleaned = ",".join(p.strip() for p in cli_params.replace(";", ",").split(",") if p.strip())
    db.set_setting("cli_params", cleaned or db.DEFAULT_CLI_PARAMS)
    _noteer(request, user, "server.instellingen", detail="op te vragen parameters")
    return RedirectResponse("/admin/server#cli-params", status_code=303)


# Welke zichtbaarheidsknop een formulier omklapt. Een vaste tabel en geen
# kolomnaam uit het verzoek: ``what`` komt van buiten, en een naam die
# rechtstreeks in een UPDATE terechtkomt is een openstaande deur naar elke
# andere kolom van deze tabel. Dezelfde verdediging als search.Sort.
_VISIBILITY_COLUMNS = {
    "public": "is_public",
    "position": "show_position",
    "name": "show_name",
    # De namen bij de kanalen. Een derde vlag en geen bijzaak: anders dan de naam
    # en de positie van een node is een kanaalnaam nooit over de radio gegaan, en
    # sinds hij automatisch uit de eigen API van een sensornode komt, heeft er
    # niemand per naam besloten dat hij publiek mag. Zie de kolomtoelichting in
    # db.py voor waarom hij toch op 1 begint.
    "channels": "show_channels",
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
    rep = _rep_or_404(request, rid)
    user = require_perm(request, "node.zichtbaarheid", rep)
    check_csrf(request, csrf)
    column = _VISIBILITY_COLUMNS.get(what)
    if column:
        db.execute(f"UPDATE repeaters SET {column} = 1 - {column} WHERE id=?", (rid,))
        _noteer(request, user, "node.zichtbaarheid", rep=rep,
                detail=f"{column} omgeklapt naar {0 if rep[column] else 1}")
    if back == "node":
        return RedirectResponse(f"/admin/repeaters/{rid}#zichtbaarheid", status_code=303)
    return RedirectResponse("/admin", status_code=303)


@router.post("/repeaters/{rid}/channels")
async def save_channel_names(request: Request, rid: int):
    """Bewaart de namen bij de kanalen van één node.

    Het formulier stuurt per kanaal ``ch_naam_<N>`` en, waar dat zin heeft,
    ``ch_eenheid_<N>``. Het kanaalnummer staat IN de veldnaam en wordt niet uit
    de volgorde van de rijen afgeleid. Dat is geen omslachtigheid maar het hele
    punt: volgorde is precies wat hier niet mag verschuiven, en een rangnummer
    zou bij een verdwenen kanaal alle namen een plaats laten opschuiven -- stil,
    zonder foutmelding, met verkeerde cijfers als enige spoor. Zie de
    waarschuwing bij db.channel_names_for.

    ``node.hernoemen`` en geen eigen handeling: dit is naamgeving en niets
    anders. Er gaat geen pakket de lucht in, de node merkt er niets van, en het
    is dezelfde soort ingreep als het hernoemen van de node zelf -- alleen een
    laag dieper.

    Async omdat het hele formulier in één keer gelezen moet worden: naam en
    eenheid van elk kanaal komen samen binnen, en los na elkaar schrijven zou de
    tweede de eerste laten wissen.
    """
    rep = _rep_or_404(request, rid)
    user = require_perm(request, "node.hernoemen", rep)
    form = await request.form()
    check_csrf(request, str(form.get("csrf", "")))

    # Alleen kanalen waarvan we werkelijk een meting hebben. Zonder die controle
    # maakt een verzonnen veldnaam in een POST een rij aan voor een kanaal dat
    # niet bestaat, en die blijft daarna in de lijst staan zonder dat er ooit een
    # meting bij komt.
    known = {c["channel"] for c in metrics.channels_seen(db.latest_for(rid))}
    velden: dict[int, dict] = {}
    for key, value in form.items():
        for prefix, veld in (("ch_naam_", "name"), ("ch_eenheid_", "unit")):
            if not key.startswith(prefix):
                continue
            try:
                channel = int(key[len(prefix):])
            except ValueError:
                break
            if channel in known:
                velden.setdefault(channel, {})[veld] = str(value)
            break

    # Naam en eenheid samen per kanaal, in één schrijfactie: los na elkaar zou de
    # tweede de eerste weer wissen, want een rij bewaart beide velden.
    gewijzigd = []
    was = db.channel_names_for(rid)
    for channel, vals in sorted(velden.items()):
        naam = vals.get("name", "")
        oud = was[channel]["name"] if channel in was else ""
        db.set_channel_name(rid, channel, naam, vals.get("unit"))
        if naam.strip() != oud:
            gewijzigd.append(f"kanaal {channel}: '{oud}' → '{naam.strip()}'")
    if gewijzigd:
        _noteer(request, user, "node.hernoemen", rep=rep,
                detail="; ".join(gewijzigd))
    return RedirectResponse(f"/admin/repeaters/{rid}#kanalen", status_code=303)


@router.post("/repeaters/{rid}/rename")
def rename_repeater(request: Request, rid: int, name: str = Form(...), csrf: str = Form(...)):
    rep = _rep_or_404(request, rid)
    user = require_perm(request, "node.hernoemen", rep)
    check_csrf(request, csrf)
    name = name.strip()
    if name:
        db.execute("UPDATE repeaters SET name=? WHERE id=?", (name, rid))
        _noteer(request, user, "node.hernoemen", rep=rep,
                detail=f"'{rep['name']}' → '{name}'")
    # Terug naar de pagina van deze node: daar staat het veld sinds de
    # herindeling, en daar zie je meteen of de nieuwe naam er staat.
    return RedirectResponse(f"/admin/repeaters/{rid}", status_code=303)


@router.post("/repeaters/{rid}/delete")
def delete_repeater(request: Request, rid: int, csrf: str = Form(...)):
    rep = _rep_or_404(request, rid)
    user = require_perm(request, "node.verwijderen", rep)
    check_csrf(request, csrf)
    # Het trail eerst, en dan pas wissen: erna zou de naam van een node die niet
    # meer bestaat uit een rij moeten komen die net verdwenen is. De regel houdt
    # de naam als tekst vast, zodat "wie heeft die node weggegooid" te
    # beantwoorden blijft nadat de rij weg is.
    _noteer(request, user, "node.verwijderen", rep=rep,
            detail=f"sleutel {rep['pubkey_prefix']}")
    db.execute("DELETE FROM node_group_members WHERE repeater_id=?", (rid,))
    db.execute("DELETE FROM grants WHERE object_type='node' AND object_id=?", (rid,))
    db.execute("DELETE FROM samples WHERE repeater_id=?", (rid,))
    db.execute("DELETE FROM latest WHERE repeater_id=?", (rid,))
    db.execute("DELETE FROM neighbors WHERE repeater_id=?", (rid,))
    # De kanaalnamen horen bij de metingen hierboven en gaan mee. Expliciet en
    # niet op de cascade vertrouwen, net als de regels erboven: die tabel heeft
    # wél ON DELETE CASCADE, maar dan zou dit de enige verwijdering zijn die er
    # niet staat -- en de volgende lezer moet niet hoeven nagaan welke van deze
    # tabellen zichzelf opruimt en welke niet.
    db.execute("DELETE FROM channel_names WHERE repeater_id=?", (rid,))
    db.execute("DELETE FROM repeaters WHERE id=?", (rid,))
    return RedirectResponse("/admin", status_code=303)


@router.post("/tokens")
def create_token(request: Request, name: str = Form(...), csrf: str = Form(...)):
    user = require_perm(request, "server.tokens")
    check_csrf(request, csrf)
    naam = name.strip() or "token"
    token = auth.create_token(naam, door=user)
    # De naam van het token, nooit het token zelf. Dat gaat één keer over het
    # scherm via een kortlevende koek en komt verder nergens terecht -- niet in
    # een URL, niet in een log, en dus ook niet hier.
    _noteer(request, user, "server.tokens", detail=f"token '{naam}' aangemaakt")
    resp = RedirectResponse("/admin/server#tokens", status_code=303)
    resp.set_cookie("mm_new_token", token, max_age=60, httponly=True,
                    samesite="lax", secure=_secure(request))
    return resp


@router.post("/tokens/{tid}/revoke")
def revoke_token(request: Request, tid: int, csrf: str = Form(...)):
    user = require_perm(request, "server.tokens")
    check_csrf(request, csrf)
    row = db.qone("SELECT name FROM tokens WHERE id=?", (tid,))
    db.execute("UPDATE tokens SET revoked=1 WHERE id=?", (tid,))
    _noteer(request, user, "server.tokens",
            detail=f"token '{row['name'] if row else tid}' ingetrokken")
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
    # Dát het gebeurd is, nooit wat er gezet is. Een wachtwoord hoort niet in een
    # log en niet in een URL, en dat geldt voor het audittrail net zo hard.
    _noteer(request, user, "eigen.wachtwoord", detail="eigen wachtwoord gewijzigd")
    # Every session signed under the old password is now invalid, this one
    # included -- so hand this browser a new cookie instead of logging the
    # person who just changed the password out of their own admin page.
    resp = RedirectResponse("/admin/account", status_code=303)
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
    user = current_user(request)
    ik = rbac.load(user)
    repeaters = rbac.zichtbare_nodes(ik, db.q("SELECT * FROM repeaters ORDER BY sort_order, name"))
    rel = firmware.releases()
    rows = []
    for rep in repeaters:
        route = firmware.ota_route(rep)
        rows.append({
            "rep": rep,
            "ota": route,
            "job": firmware.job(rep["id"]),
            # Naast wat er kán (``ota``) wat er mág. Firmware is de duurste
            # handeling op deze site -- een verkeerde image is een node van een
            # dak halen -- dus de knop hoort uitgeschakeld te staan met de reden
            # erbij, en niet te verdwijnen.
            "mag": rbac.decide(ik, "node.firmware", rep),
            "mag_beheeradres": rbac.decide(ik, "node.beheeradres", rep),
            "mag_uitvragen": rbac.decide(ik, "node.uitvragen", rep),
            # Welke releases een image dragen voor de bouwomgeving die deze node
            # meldde. Leeg als we die omgeving niet kennen -- dan is de eerlijke
            # uitkomst 'niet vast te stellen' en geen lijst om uit te kiezen.
            "builds": [r for r in (rel.get("items") or []) if route["env"] in r["images"]]
                      if route["env"] else [],
        })
    ctx = {
        "site_name": config.SITE_NAME, "user": user,
        "serverrechten": rbac.serverrechten(ik),
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
    require_perm(request, "server.firmwarelijst")
    check_csrf(request, csrf)
    firmware.releases(force=True)
    return RedirectResponse("/admin/firmware", status_code=303)


@router.post("/repeaters/{rid}/ota")
def save_ota(request: Request, rid: int, ota_host: str = Form(""),
             is_critical: str = Form(""), csrf: str = Form(...)):
    rep = _rep_or_404(request, rid)
    host = (ota_host or "").strip()
    # Wissen mag wie het recht heeft; INVULLEN alleen een serverbeheerder. Dat
    # onderscheid is de kern van de reparatie: een adres weghalen sluit een weg en
    # kan er nooit een openen, en een adres invullen bepaalt waarheen de server de
    # inloggegevens stuurt die elke node openen. Zie firmware.check_target.
    if host:
        require_server_admin(request, "een beheeradres invullen")
    # En het recht op DEZE node geldt hoe dan ook. Voor een serverbeheerder is dat
    # geen dubbelop maar de gewone weg: rbac.decide laat hem alles, en zo komt de
    # handeling langs één plek in het audittrail terecht.
    user = require_perm(request, "node.beheeradres", rep)
    check_csrf(request, csrf)
    if host:
        try:
            firmware._url(host, "/api/fw")      # zelfde vormcontrole als bij het schrijven
        except ValueError as exc:
            raise HTTPException(422, f"Adres onbruikbaar: {exc}") from exc
    db.set_ota_host(rid, host, by_admin=bool(host))
    db.set_critical(rid, bool(is_critical))
    # Het adres staat er niet in. Deze repo is publiek en dit trail is
    # exporteerbaar; een beheeradres is een intern adres, en de vraag die het
    # trail moet beantwoorden is wie eraan zat, niet wat het was.
    _noteer(request, user, "node.beheeradres", rep=rep,
            detail=("beheeradres gezet" if host else "beheeradres gewist")
                   + (", kritiek" if is_critical else ", niet kritiek"))
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
    rep = _rep_or_404(request, rid)
    user = require_perm(request, "node.uitvragen", rep)
    check_csrf(request, csrf)
    info = firmware.probe(str(rep["ota_host"] or ""))
    if info["ok"]:
        db.record_pio_env(rid, info["env"])
        if info["ver"]:
            db.record_firmware(rid, fw_module=info["ver"])
    _noteer(request, user, "node.uitvragen", rep=rep,
            detail=f"aangeklopt bij de node; omgeving {info.get('env') or 'onbekend'}",
            outcome=audit.OK if info["ok"] else audit.MISLUKT)
    return _fw_context(request, probe={"rid": rid, "info": info})


@router.post("/repeaters/{rid}/upgrade")
def start_upgrade(request: Request, rid: int, tag: str = Form(...),
                  expect_env: str = Form(""), confirm: str = Form(""),
                  csrf: str = Form(...)):
    rep = _rep_or_404(request, rid)
    user = require_perm(request, "node.firmware", rep)
    check_csrf(request, csrf)

    # De bevestiging voor een kritieke node. Niet 'weet u het zeker' maar de
    # naam overtypen, want de fout die dit moet vangen is niet twijfel maar een
    # klik op de verkeerde regel -- en daar helpt een ja/nee-vraag niet tegen.
    if rep["is_critical"] and (confirm or "").strip() != (rep["name"] or ""):
        _noteer(request, user, "node.firmware", rep=rep, outcome=audit.MISLUKT,
                detail=f"naar {tag}; bevestiging voor een kritieke node ontbrak")
        return _fw_context(request, started={
            "rid": rid, "ok": False,
            "error": f"Deze node staat als kritiek gemarkeerd. Typ de naam "
                     f"({rep['name']}) precies over om te bevestigen.",
        })

    result = firmware.start(rep, tag, expect_env)
    # Het starten wordt vastgelegd en niet de afloop: die komt uit een thread die
    # minuten later klaar is, en tegen die tijd is er geen verzoek meer om hem
    # aan op te hangen. Wat hier moet staan is wie het in gang zette en met welke
    # release -- dat is de vraag die na een node die niet terugkomt gesteld wordt.
    _noteer(request, user, "node.firmware", rep=rep,
            detail=f"upgrade naar {tag} gestart",
            outcome=audit.OK if result.get("ok") else audit.MISLUKT)
    return _fw_context(request, started={"rid": rid, **result})


@router.post("/repeaters/{rid}/upgrade/clear")
def clear_upgrade(request: Request, rid: int, csrf: str = Form(...)):
    rep = _rep_or_404(request, rid)
    user = require_perm(request, "node.firmware", rep)
    check_csrf(request, csrf)
    firmware.clear_job(rid)
    _noteer(request, user, "node.firmware", rep=rep, detail="opdracht opgeruimd")
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


# --- eigen account -----------------------------------------------------------

@router.get("/account", response_class=HTMLResponse)
def account_page(request: Request):
    """Eigen wachtwoord en eigen rechten, voor iedereen die kan inloggen.

    Bestaat sinds toegang niet meer alles-of-niets is. Het wachtwoordformulier
    stond op Server en site, en die pagina is voorbehouden aan serverbeheerders
    -- een gebruiker met rechten op twee nodes zou zijn eigen wachtwoord dan niet
    meer kunnen wijzigen.

    De pagina toont ook wat deze gebruiker mag, en dat is geen versiering: een
    uitgeschakelde knop zegt waarom hij uit staat, maar "waar mag ik dan wél bij"
    is een vraag die je niet node voor node hoort te beantwoorden.
    """
    user = require_login(request)
    ik = rbac.load(user)
    repeaters = rbac.zichtbare_nodes(ik, db.q("SELECT * FROM repeaters ORDER BY sort_order, name"))
    return templates.TemplateResponse(request, "admin/account.html", {
        "site_name": config.SITE_NAME, "user": user, "world": "account",
        "ik": ik, "serverrechten": rbac.serverrechten(ik),
        "mijn_nodes": [{"rep": r, "rol": rbac.rol_op_node(ik, r)} for r in repeaters],
        "rol_uitleg": rbac.ROL_UITLEG, "klasse_uitleg": rbac.KLASSE_UITLEG,
        "handelingen": rbac.ACTIONS,
        "audit": audit.recent(20, actor=user),
        "csrf": auth.csrf_token(request.cookies.get(auth.SESSION_COOKIE, "")),
    })


# --- gebruikers, groepen en toekenningen -------------------------------------
#
# Allemaal voorbehouden aan een serverbeheerder (rbac.decide laat 'server.*' niet
# anders toe), en allemaal met een regel in het audittrail. Het toekennen van
# rechten is zelf de gevoeligste handeling op deze site: wie hier iets mag, mag
# zichzelf morgen alles geven.

@router.post("/users")
def create_user(request: Request, username: str = Form(...), password: str = Form(...),
                is_superuser: str = Form(""), csrf: str = Form(...)):
    """Een account erbij. De beheerder zet het wachtwoord en kan het niet teruglezen.

    Het wachtwoord komt binnen over POST en gaat er gehasht in; nergens onderweg
    staat het in een URL, in een log of in het audittrail. Dat is dezelfde regel
    die overal in dit project geldt, en hij is hier het scherpst: dit is het
    formulier waarmee iemand een wachtwoord voor een ánder zet.
    """
    user = require_perm(request, "server.gebruikers")
    check_csrf(request, csrf)
    naam = username.strip()
    if not naam or len(naam) > 64:
        raise HTTPException(422, "Gebruikersnaam ontbreekt of is te lang")
    if len(password) < 8:
        raise HTTPException(422, "Wachtwoord moet minstens 8 tekens zijn")
    if rbac.gebruiker_op_naam(naam):
        raise HTTPException(409, f"'{naam}' bestaat al")
    rbac.maak_gebruiker(naam, auth.hash_password(password),
                        is_superuser=bool(is_superuser), door=user)
    _noteer(request, user, "server.gebruikers",
            detail=f"account '{naam}' aangemaakt"
                   + (" als serverbeheerder" if is_superuser else ""))
    return RedirectResponse("/admin/server#gebruikers", status_code=303)


@router.post("/users/{uid}/password")
def set_user_password(request: Request, uid: int, password: str = Form(...),
                      csrf: str = Form(...)):
    """Een wachtwoord voor iemand anders zetten.

    Zonder het huidige wachtwoord te kennen -- dat is het punt van deze knop -- en
    zonder het nieuwe te kunnen teruglezen. De sessies van die gebruiker vervallen
    hierdoor vanzelf: de wachtwoordvingerafdruk in hun koek klopt niet meer (zie
    auth.password_stamp). Dat is precies het gewenste gedrag als de reden voor
    deze knop is dat er iets misging.
    """
    user = require_perm(request, "server.gebruikers")
    check_csrf(request, csrf)
    row = db.qone("SELECT * FROM admins WHERE id=?", (uid,))
    if not row:
        raise HTTPException(404, "Onbekend account")
    if len(password) < 8:
        raise HTTPException(422, "Wachtwoord moet minstens 8 tekens zijn")
    db.execute("UPDATE admins SET pw_hash=? WHERE id=?", (auth.hash_password(password), uid))
    _noteer(request, user, "server.gebruikers",
            detail=f"wachtwoord gezet voor '{row['username']}'")
    return RedirectResponse("/admin/server#gebruikers", status_code=303)


@router.post("/users/{uid}/flags")
def set_user_flags(request: Request, uid: int, is_superuser: str = Form(""),
                   disabled: str = Form(""), csrf: str = Form(...)):
    """Serverbeheerder ja/nee en uitgezet ja/nee, in één formulier.

    Met de ene controle die deze hele feature moet overleven: de laatste actieve
    serverbeheerder kan niet weg. Zonder die grendel is één verkeerd vinkje een
    installatie waar niemand meer bij de gebruikers kan, en loopt de weg terug
    langs de opdrachtregel op de server zelf.
    """
    user = require_perm(request, "server.gebruikers")
    check_csrf(request, csrf)
    row = db.qone("SELECT * FROM admins WHERE id=?", (uid,))
    if not row:
        raise HTTPException(404, "Onbekend account")
    super_ = bool(is_superuser)
    uit = bool(disabled)
    if (not super_ or uit) and rbac.aantal_serverbeheerders(behalve=uid) == 0 \
            and row["is_superuser"] and not row["disabled"]:
        raise HTTPException(
            409, "Dit is de laatste actieve serverbeheerder. Maak eerst iemand "
                 "anders serverbeheerder.")
    rbac.zet_serverbeheerder(uid, super_)
    rbac.zet_uit(uid, uit)
    _noteer(request, user, "server.gebruikers",
            detail=f"'{row['username']}': "
                   f"{'serverbeheerder' if super_ else 'gewone gebruiker'}, "
                   f"{'uit' if uit else 'actief'}")
    return RedirectResponse("/admin/server#gebruikers", status_code=303)


@router.post("/users/{uid}/delete")
def delete_user(request: Request, uid: int, csrf: str = Form(...)):
    user = require_perm(request, "server.gebruikers")
    check_csrf(request, csrf)
    row = db.qone("SELECT * FROM admins WHERE id=?", (uid,))
    if not row:
        raise HTTPException(404, "Onbekend account")
    if row["is_superuser"] and not row["disabled"] \
            and rbac.aantal_serverbeheerders(behalve=uid) == 0:
        raise HTTPException(409, "Dit is de laatste actieve serverbeheerder.")
    rbac.verwijder_gebruiker(uid)
    _noteer(request, user, "server.gebruikers",
            detail=f"account '{row['username']}' verwijderd")
    return RedirectResponse("/admin/server#gebruikers", status_code=303)


@router.post("/groups")
def create_group(request: Request, soort: str = Form(...), name: str = Form(...),
                 note: str = Form(""), csrf: str = Form(...)):
    user = require_perm(request, "server.gebruikers")
    check_csrf(request, csrf)
    if soort not in ("user", "node"):
        raise HTTPException(422, "Onbekende groepssoort")
    if not name.strip():
        raise HTTPException(422, "Een groep heeft een naam nodig")
    rbac.maak_groep(soort, name, note)
    _noteer(request, user, "server.gebruikers",
            detail=f"{'gebruikers' if soort == 'user' else 'node'}groep "
                   f"'{name.strip()}' aangemaakt")
    return RedirectResponse("/admin/server#groepen", status_code=303)


@router.post("/groups/{soort}/{gid}/delete")
def remove_group(request: Request, soort: str, gid: int, csrf: str = Form(...)):
    """Een groep weg, en met haar de toekenningen die erop stonden.

    Die toekenningen meenemen is geen opruimwerk maar een veiligheidsmaatregel:
    een toekenning die naar een verdwenen groep wijst, is een rij waarvan niemand
    meer kan zien wat ze betekent -- en als er later een groep met hetzelfde id
    ontstaat, betekent ze ineens iets anders.
    """
    user = require_perm(request, "server.gebruikers")
    check_csrf(request, csrf)
    if soort not in ("user", "node"):
        raise HTTPException(422, "Onbekende groepssoort")
    rbac.verwijder_groep(soort, gid)
    _noteer(request, user, "server.gebruikers", detail=f"groep {soort}/{gid} verwijderd")
    return RedirectResponse("/admin/server#groepen", status_code=303)


@router.post("/groups/{soort}/{gid}/members")
def set_group_member(request: Request, soort: str, gid: int, member: int = Form(...),
                     lid: str = Form(""), csrf: str = Form(...)):
    user = require_perm(request, "server.gebruikers")
    check_csrf(request, csrf)
    if soort not in ("user", "node"):
        raise HTTPException(422, "Onbekende groepssoort")
    rbac.zet_lidmaatschap(soort, gid, member, bool(lid))
    _noteer(request, user, "server.gebruikers",
            detail=f"lidmaatschap {soort}/{gid}: {member} "
                   f"{'toegevoegd' if lid else 'verwijderd'}")
    return RedirectResponse("/admin/server#groepen", status_code=303)


@router.post("/grants")
def create_grant(request: Request, subject_type: str = Form(...),
                 subject_id: int = Form(...), object_type: str = Form(...),
                 object_id: int = Form(default=0), role: str = Form(default=""),
                 effect: str = Form(default="allow"), csrf: str = Form(...)):
    user = require_perm(request, "server.gebruikers")
    check_csrf(request, csrf)
    try:
        rbac.maak_toekenning(subject_type, subject_id, object_type,
                             object_id or None, role or None, effect, door=user)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    _noteer(request, user, "server.gebruikers",
            detail=f"toekenning: {subject_type} {subject_id} → "
                   f"{object_type} {object_id or 'alle'} ({effect} {role})")
    return RedirectResponse("/admin/server#toekenningen", status_code=303)


@router.post("/grants/{grant_id}/delete")
def remove_grant(request: Request, grant_id: int, csrf: str = Form(...)):
    user = require_perm(request, "server.gebruikers")
    check_csrf(request, csrf)
    rbac.verwijder_toekenning(grant_id)
    _noteer(request, user, "server.gebruikers", detail=f"toekenning {grant_id} ingetrokken")
    return RedirectResponse("/admin/server#toekenningen", status_code=303)


@router.get("/audit", response_class=HTMLResponse)
def audit_page(request: Request):
    """Het volledige audittrail, nieuwste eerst.

    Een eigen pagina en niet alleen het blok van veertig regels op Server en
    site: veertig regels zijn genoeg om te zien dat er iets gebeurd is, en te
    weinig om terug te kijken naar de avond waarop een node niet terugkwam.
    """
    user = require_perm(request, "server.audit")
    try:
        limit = int(request.query_params.get("n", "200"))
    except ValueError:
        limit = 200
    return templates.TemplateResponse(request, "admin/audit.html", {
        "site_name": config.SITE_NAME, "user": user, "world": "server",
        "regels": audit.recent(limit),
        "limit": limit,
        "serverrechten": rbac.serverrechten(user),
        "bewaardagen": db.setting_int("audit_retention_days", audit.DEFAULT_AUDIT_DAYS),
        "csrf": auth.csrf_token(request.cookies.get(auth.SESSION_COOKIE, "")),
    })
