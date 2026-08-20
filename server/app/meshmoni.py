"""MeshMoni: de monitoringsubsite voor op de telefoon, achter ``/meshmoni``.

Waarom een eigen subsite en geen mobiele stand van de bestaande pagina's. De
publieke repeaterpagina is een dashboard: alles over één node, op een scherm
waar dat past. Wie 's ochtends met een telefoon wil weten "doen mijn diensten
het, en is er vannacht iets gebeurd?" heeft een andere vraag, en die verdient
een eigen antwoord: de sensornodes met hun kanalen (mét de namen uit
``channel_names`` -- nooit kale metricnamen), de historiek als grafiek, een
knop om nú uit te vragen, en de alertenlijst. Als PWA op het beginscherm, met
webpush erachter (zie webpush.py).

Wat hier bewust HERGEBRUIKT wordt in plaats van nagebouwd:

* de login: dezelfde sessiekoek als /admin (auth.read_session). Een PWA die de
  login omzeilt is een gat, dus elke pagina en elk data-endpoint eist een
  sessie -- pagina's leiden om naar het inlogscherm, data-endpoints geven 401
  (een fetch kan met een 303 naar een HTML-pagina niets beginnen);
* de historiek: db.metric_history, dezelfde weg als de grafieken op de
  publieke nodepagina (VictoriaMetrics als die antwoordt, SQLite als vangnet);
* het uitvragen: routes_admin._dispatch, exact de weg van de opvraagknop in de
  beheer-UI, met dezelfde rechtencontrole (rbac "node.uitvragen") en dezelfde
  regel in het audittrail. Eén weg naar de radio, niet twee;
* de kanaalnamen: metrics.channel_label / channel_unit, zodat een kanaal hier
  precies zo heet als op elke andere pagina.

Caching: de data-endpoints sturen ``Cache-Control: no-store``. Een meting uit
een verouderde cache is erger dan geen meting, want ze liegt met een stellig
gezicht. De service worker (static/meshmoni/sw.js) cachet dan ook alléén de
app-schil; elke pagina toont daarnaast een "laatst bijgewerkt"-stempel, zodat
zichtbaar is hoe oud het beeld is als het netwerk wegvalt.
"""
import re
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse

from . import audit, auth, db, metrics, rbac, routes_admin, webpush
from .templating import templates

router = APIRouter(prefix="/meshmoni")

_STATIC = Path(__file__).resolve().parent / "static" / "meshmoni"

_METRIC_RE = re.compile(r"^[a-z0-9_]{1,64}$")


# --- toegang -----------------------------------------------------------------

def _user(request: Request) -> str | None:
    return auth.read_session(request.cookies.get(auth.SESSION_COOKIE))


def _require_page_login(request: Request) -> str:
    """Voor pagina's: geen sessie is een omleiding naar het inlogscherm.

    Hetzelfde scherm als /admin, want het is dezelfde login; een tweede
    inlogscherm zou een tweede plek zijn om het eerste te vergeten.
    """
    user = _user(request)
    if not user:
        raise HTTPException(status_code=303, headers={"Location": "/admin/login"})
    return user


def _require_api_login(request: Request) -> str:
    """Voor data-endpoints: geen sessie is een 401, geen omleiding.

    Een fetch die een 303 naar een HTML-inlogpagina volgt, krijgt een 200 met
    HTML erin en zou dat als data proberen te lezen. 401 laat de app zelf de
    weg naar het inlogscherm wijzen.
    """
    user = _user(request)
    if not user:
        raise HTTPException(401, "niet ingelogd")
    return user


def _check_csrf(request: Request) -> None:
    """Zelfde token als de beheerformulieren, maar uit een header.

    De schrijvende endpoints hier worden door JavaScript aangeroepen, niet door
    een formulier; een header is dan de natuurlijke plek. Het token zelf is
    hetzelfde (auth.csrf_token over de sessiekoek), dus de pagina kan het één
    keer meegeven en de app hoeft niets nieuws te leren.
    """
    cookie = request.cookies.get(auth.SESSION_COOKIE, "")
    token = request.headers.get("x-csrf", "")
    if not cookie or not auth.eq(token, auth.csrf_token(cookie)):
        raise HTTPException(403, "CSRF-controle mislukt")


def _json(data: dict, status_code: int = 200) -> JSONResponse:
    """Elk data-antwoord draagt no-store: metingen komen nooit uit een cache."""
    data.setdefault("generated", db.utcnow())
    return JSONResponse(data, status_code=status_code,
                        headers={"Cache-Control": "no-store"})


# --- de sensornodes ----------------------------------------------------------

def _kanalen(latest, ch_names) -> list[dict]:
    """De kanaaltegels van één node, met de namen die de beheerder gaf.

    Dezelfde regels als de publieke nodepagina: de naam uit ``channel_names``
    vóór het soortlabel, een naamloos kanaal blijft zichtbaar als "kanaal N",
    en een switch leest als op/neer en niet als 1/0.
    """
    out = []
    for m, row in latest.items():
        ch = metrics.channel_metric(m)
        if ch is None:
            continue
        channel, kind = ch
        named = ch_names.get(channel)
        label = metrics.channel_label(channel, kind, named["name"] if named else None)
        unit = metrics.channel_unit(kind, named["unit"] if named else None)
        value = row["value"]
        if kind == "switch" and value is not None:
            display = "op" if value == 1.0 else "neer"
        elif value is None:
            display = row["value_str"] or "—"
        else:
            display = f"{value:g}" + (f" {unit}" if unit else "")
        out.append({"metric": m, "channel": channel, "kind": kind, "label": label,
                    "unit": unit, "value": value, "display": display, "ts": row["ts"]})
    out.sort(key=lambda t: (t["channel"], metrics.CHANNEL_KINDS[t["kind"]][2]))
    return out


def _sensornodes() -> list[dict]:
    """Alle nodes met minstens één kanaalmeting, met hun kanalen erbij.

    "Sensornode" is hier een waarneming en geen vinkje: een node die
    kanaalmetingen instuurt is er een, en een node die daarmee stopt verdwijnt
    niet uit deze lijst zolang zijn laatste metingen er nog staan -- juist een
    dienst die zwijgt is waar een monitoringscherm voor bestaat. Ook verborgen
    nodes staan erin: dit scherm zit achter de login, net als /admin.
    """
    out = []
    for r in db.q("SELECT * FROM repeaters ORDER BY sort_order, name"):
        latest = db.latest_for(r["id"])
        kanalen = _kanalen(latest, db.channel_names_for(r["id"]))
        if not kanalen:
            continue
        online = latest.get("online")
        battery = latest.get("battery_percentage")
        out.append({
            "id": r["id"], "slug": r["slug"], "name": db.public_name(r),
            "last_seen": r["last_seen"],
            "online": online is not None and online["value"] == 1.0,
            "battery": battery["value"] if battery else None,
            "channels": kanalen,
        })
    return out


@router.get("/api/nodes")
def api_nodes(request: Request):
    _require_api_login(request)
    return _json({"nodes": _sensornodes()})


def _stats_en_histogram(points):
    """Min/gemiddelde/max plus een histogram over het opgevraagde venster.

    Het histogram wordt hier geteld en niet in de browser: de punten zijn er
    toch al, en één telling op de server is dezelfde telling voor elk scherm.
    Twaalf klassen -- genoeg vorm om een uitschieter te zien, weinig genoeg om
    op een telefoonscherm te passen.
    """
    vals = [v for _, v in points if v is not None]
    if not vals:
        return None, []
    lo, hi = min(vals), max(vals)
    stats = {"min": lo, "max": hi, "avg": round(sum(vals) / len(vals), 3),
             "last": vals[-1], "n": len(vals)}
    if hi == lo:
        return stats, [{"lo": lo, "hi": hi, "n": len(vals)}]
    bins = 12
    width = (hi - lo) / bins
    counts = [0] * bins
    for v in vals:
        counts[min(int((v - lo) / width), bins - 1)] += 1
    return stats, [{"lo": round(lo + i * width, 6), "hi": round(lo + (i + 1) * width, 6),
                    "n": n} for i, n in enumerate(counts)]


@router.get("/api/nodes/{rid}/history")
def api_history(request: Request, rid: int, metric: str, hours: int = 24):
    """Historiek voor één meting: punten, statistiek en histogram in één keer.

    Dezelfde bron als de grafieken op de publieke nodepagina
    (db.metric_history: VictoriaMetrics, met SQLite als vangnet), zodat deze
    subsite nooit iets anders laat zien dan de site zelf.
    """
    _require_api_login(request)
    row = db.qone("SELECT * FROM repeaters WHERE id=?", (rid,))
    if not row:
        raise HTTPException(404, "Onbekende node")
    if not _METRIC_RE.match(metric):
        raise HTTPException(400, "Onbruikbare metricnaam")
    hours = max(1, min(hours, 2160))
    points = db.metric_history(row, metric, hours)
    stats, histogram = _stats_en_histogram(points)
    # Het label bij de grafiek: de kanaalnaam als die er is, nooit de kale
    # metricnaam. metric_info kent die weg al (channels-sectie), maar de door
    # de beheerder gezette naam moet er hier zelf bij gezocht worden.
    ch = metrics.channel_metric(metric)
    if ch is not None:
        named = db.channel_names_for(rid).get(ch[0])
        label = metrics.channel_label(ch[0], ch[1], named["name"] if named else None)
        unit = metrics.channel_unit(ch[1], named["unit"] if named else None)
    else:
        _, label, unit, _ = metrics.metric_info(metric)
    return _json({"metric": metric, "label": label, "unit": unit, "hours": hours,
                  "points": points, "stats": stats, "histogram": histogram})


@router.post("/api/nodes/{rid}/refresh")
def api_refresh(request: Request, rid: int):
    """Nu uitvragen: exact de weg van de opvraagknop in de beheer-UI.

    routes_admin._dispatch bewandelt MQTT én de poller-wachtrij en zegt welke
    open waren; die uitkomst gaat onvertaald naar de app, want "gestart" beloven
    terwijl er geen weg open was is precies wat die functie moest voorkomen.
    Zelfde recht (node.uitvragen), zelfde regel in het audittrail.
    """
    user = _require_api_login(request)
    row = db.qone("SELECT * FROM repeaters WHERE id=?", (rid,))
    if not row:
        raise HTTPException(404, "Onbekende node")
    _check_csrf(request)
    besluit = rbac.decide(user, "node.uitvragen", row)
    if not besluit.allowed:
        audit.log(user, "node.uitvragen", rep=row, outcome=audit.GEWEIGERD,
                  detail=besluit.reason, ip=routes_admin._ip(request))
        raise HTTPException(403, besluit.reason)
    weg = routes_admin._dispatch(row, "status")
    audit.log(user, "node.uitvragen", rep=row, outcome=routes_admin._uitkomst(weg),
              detail=f"status via meshmoni, weg: {weg}", ip=routes_admin._ip(request))
    return _json({"weg": weg})


# --- alerts ------------------------------------------------------------------

@router.get("/api/alerts")
def api_alerts(request: Request, all: int = 0):
    """De alertenlijst, onbevestigde eerst en standaard alléén die.

    ``all=1`` toont ook de bevestigde -- de lijst is dan geschiedenis in plaats
    van takenlijst. De namen (node, kanaal) worden er hier bij gezocht: een
    alert draagt zelf alleen nummers, en nummers horen dit scherm nooit te
    halen.
    """
    _require_api_login(request)
    webpush.ensure_schema()
    where = "" if all else "WHERE a.acked=0"
    rows = db.q(
        "SELECT a.*, r.name AS node_naam FROM alerts a "
        f"LEFT JOIN repeaters r ON r.id=a.repeater_id {where} "
        "ORDER BY a.id DESC LIMIT 200")
    namen: dict[int, dict] = {}
    items = []
    for a in rows:
        kanaal = None
        if a["channel"] is not None and a["repeater_id"] is not None:
            if a["repeater_id"] not in namen:
                namen[a["repeater_id"]] = db.channel_names_for(a["repeater_id"])
            named = namen[a["repeater_id"]].get(a["channel"])
            kanaal = named["name"] if named else f"kanaal {a['channel']}"
        items.append({"id": a["id"], "repeater_id": a["repeater_id"],
                      "node": a["node_naam"], "channel": a["channel"],
                      "channel_name": kanaal, "text": a["text"],
                      "severity": a["severity"], "ts": a["ts"],
                      "source": a["source"], "acked": bool(a["acked"])})
    open_row = db.qone("SELECT COUNT(*) AS n FROM alerts WHERE acked=0")
    return _json({"alerts": items, "open": open_row["n"] if open_row else 0})


@router.post("/api/alerts/{aid}/ack")
def api_alert_ack(request: Request, aid: int):
    """Bevestigen: de alert blijft bestaan, hij staat alleen niet meer open.

    Verwijderen kan hier met opzet niet. Een alert is een gebeurtenis, en een
    gebeurtenis wegpoetsen omdat ze gezien is zou de vraag "wat is hier vorige
    week gebeurd?" onbeantwoordbaar maken.
    """
    _require_api_login(request)
    _check_csrf(request)
    webpush.ensure_schema()
    if not db.execute_rowcount("UPDATE alerts SET acked=1 WHERE id=?", (aid,)):
        raise HTTPException(404, "Onbekende alert")
    return _json({"acked": aid})


# --- webpush -----------------------------------------------------------------

@router.get("/api/push/status")
def api_push_status(request: Request):
    """Aan of uit, en waarom. De publieke sleutel reist mee zodat de browser
    zich kan abonneren zonder een tweede rondreis."""
    _require_api_login(request)
    return _json(webpush.status())


@router.post("/api/push/subscribe")
async def api_push_subscribe(request: Request):
    user = _require_api_login(request)
    _check_csrf(request)
    st = webpush.status()
    if not st["enabled"]:
        # 409 en niet 400: het verzoek is goedgevormd, de server staat er
        # alleen niet voor open -- en de reden hoort in het antwoord, zodat de
        # app hem kan tonen in plaats van een kaal foutnummer.
        raise HTTPException(409, st["reason"])
    try:
        body = await request.json()
    except ValueError:
        raise HTTPException(400, "Geen geldige JSON")
    if not webpush.subscribe(body if isinstance(body, dict) else {}, user):
        raise HTTPException(400, "Geen bruikbaar pushabonnement")
    return _json({"subscribed": True})


@router.post("/api/push/unsubscribe")
async def api_push_unsubscribe(request: Request):
    _require_api_login(request)
    _check_csrf(request)
    try:
        body = await request.json()
    except ValueError:
        raise HTTPException(400, "Geen geldige JSON")
    endpoint = str((body or {}).get("endpoint", ""))
    return _json({"removed": webpush.unsubscribe(endpoint)})


# --- de app-schil ------------------------------------------------------------

@router.get("/manifest.webmanifest", include_in_schema=False)
def manifest():
    """Het PWA-manifest. Zonder login, met opzet: het bevat niets dan de naam
    en de iconen, en de browser haalt het soms op zonder koekjes mee te sturen
    -- een manifest achter de login is een PWA die willekeurig niet
    installeert."""
    return FileResponse(_STATIC / "manifest.webmanifest",
                        media_type="application/manifest+json")


@router.get("/sw.js", include_in_schema=False)
def service_worker():
    """De service worker, geserveerd onder /meshmoni/ en niet onder /static.

    Dat is geen smaak maar een regel van het platform: een service worker mag
    hoogstens besturen wat onder zijn eigen URL hangt. Vanaf /static zou hij
    /meshmoni niet mogen bedienen, en de meldingen komen juist bij hem binnen.
    """
    return FileResponse(_STATIC / "sw.js", media_type="application/javascript")


def _boot(request: Request, page: str, **extra) -> dict:
    """Wat elke pagina aan het script meegeeft: het CSRF-token, de pushstatus
    en waar we zijn. Alle pagina's staan achter de login, dus dit lekt niets."""
    cookie = request.cookies.get(auth.SESSION_COOKIE, "")
    return {"page": page, "csrf": auth.csrf_token(cookie),
            "push": webpush.status(), **extra}


@router.get("", response_class=HTMLResponse, include_in_schema=False)
@router.get("/", response_class=HTMLResponse, include_in_schema=False)
def index(request: Request):
    _require_page_login(request)
    return templates.TemplateResponse(request, "meshmoni/index.html", {
        "boot": _boot(request, "index"),
    })


@router.get("/node/{rid}", response_class=HTMLResponse, include_in_schema=False)
def node_page(request: Request, rid: int):
    user = _require_page_login(request)
    row = db.qone("SELECT * FROM repeaters WHERE id=?", (rid,))
    if not row:
        raise HTTPException(404, "Onbekende node")
    besluit = rbac.decide(user, "node.uitvragen", row)
    return templates.TemplateResponse(request, "meshmoni/node.html", {
        "boot": _boot(request, "node", node={
            "id": row["id"], "name": db.public_name(row),
            # De knop staat er ook voor wie niet mag: uitgeschakeld, met de
            # reden erbij -- dezelfde afspraak als overal op de site.
            "mag_uitvragen": besluit.allowed, "uitvraag_reden": besluit.reason,
        }),
        "node_naam": db.public_name(row),
    })
