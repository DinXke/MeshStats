"""Bezoekcijfers naar een eigen Matomo, zonder cookies.

Waarom een eigen bestand en geen scriptregel in het sjabloon. De site stuurt een
Content-Security-Policy mee die alleen scripts van zichzelf toelaat; een regel
``<script src="https://matomo.../matomo.js">`` zou daar tegenaan lopen. Door de
kleine startcode vanaf deze server te serveren (``/analytics.js``) valt hij onder
``script-src 'self'`` en staat er niets in de HTML dat per pagina herhaald moet
worden. Zonder ``MM_MATOMO_URL`` is het bestand leeg: een installatie die dit
niet wil, hoeft niets uit te zetten en stuurt niets.

Twee sites, twee tellers. ``meshmanager.net`` en ``chat.meshmanager.net`` zijn
in Matomo twee sites, zodat de chat de statistieken van de hoofdsite niet
vertroebelt; welke van de twee het is, hangt af van de hostnaam van het verzoek
(``MM_MATOMO_CHAT_SITE_ID``, met terugval op de gewone).

Privacy. Geen cookies (``disableCookies``), en wie "Do Not Track" aanzet wordt
niet geteld (``setDoNotTrack``). Daardoor is er geen toestemmingsbanner nodig en
blijft er van een bezoeker niets herkenbaars achter. Dezelfde keuze als op
meshtu.be, waar dezelfde Matomo staat.
"""
import json
import os

from fastapi import APIRouter, Request, Response

router = APIRouter()

EMPTY = "/* Geen bezoekcijfers: MM_MATOMO_URL staat niet ingesteld. */\n"


def matomo_url() -> str:
    """De Matomo-URL met precies één slash op het eind, of leeg als hij niet ingesteld is."""
    u = os.environ.get("MM_MATOMO_URL", "").strip()
    return (u.rstrip("/") + "/") if u else ""


def site_id(is_chat: bool = False) -> str:
    """Site-id voor deze hostnaam; de chat mag een eigen teller hebben."""
    if is_chat:
        chat = os.environ.get("MM_MATOMO_CHAT_SITE_ID", "").strip()
        if chat:
            return chat
    return os.environ.get("MM_MATOMO_SITE_ID", "1").strip() or "1"


def enabled() -> bool:
    return bool(matomo_url())


def snippet(is_chat: bool = False) -> str:
    """De startcode, of een lege (maar geldige) JS-tekst als er geen Matomo is."""
    url = matomo_url()
    if not url:
        return EMPTY
    # json.dumps: de waarden komen uit de omgeving en gaan de JavaScript in.
    return (
        "/*  Bezoekcijfers naar Matomo: geen cookies, en wie DNT aanzet wordt niet geteld.\n"
        "    Zie MM_MATOMO_URL / MM_MATOMO_SITE_ID / MM_MATOMO_CHAT_SITE_ID op de server.  */\n"
        "(function () {\n"
        "  var u = %s;\n"
        "  var _paq = (window._paq = window._paq || []);\n"
        '  _paq.push(["disableCookies"]);\n'
        '  _paq.push(["setDoNotTrack", true]);\n'
        '  _paq.push(["setTrackerUrl", u + "matomo.php"]);\n'
        "  _paq.push([\"setSiteId\", %s]);\n"
        '  _paq.push(["trackPageView"]);\n'
        '  _paq.push(["enableLinkTracking"]);\n'
        "  var d = document, g = d.createElement('script'), s = d.getElementsByTagName('script')[0];\n"
        "  g.async = true; g.src = u + 'matomo.js';\n"
        "  s.parentNode.insertBefore(g, s);\n"
        "})();\n" % (json.dumps(url), json.dumps(site_id(is_chat)))
    )


@router.get("/analytics.js")
def analytics_js(request: Request):
    from .main import _is_chat_host  # laat in de functie: main importeert deze module

    body = snippet(_is_chat_host(request))
    # Vier uur cachen zoals meshtu.be: verandert bijna nooit, en een oude kopie
    # blijft werken. Geen cache als er niets te sturen valt, zodat aanzetten meteen telt.
    cache = "public, max-age=14400" if enabled() else "no-store"
    return Response(body, media_type="application/javascript; charset=utf-8",
                    headers={"Cache-Control": cache, "X-Content-Type-Options": "nosniff"})
