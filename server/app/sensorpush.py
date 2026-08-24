"""Gebeurtenis-push: een sensornode meldt zijn overgangen zelf, over IP.

De vierde weg naast de broker, de monitor en de IP-poll -- en hij bestaat om
het gat dat die drie samen laten liggen. De mesh-schakel node->repeater is
defect (heen werkt, terug niet; zie sensornode.py), dus een alarm over het mesh
komt er op dit ogenblik niet. De IP-poll ziet elke overgang wél, maar pas bij
de volgende ronde: tot MM_SENSOR_POLL_S (standaard 300 s) na het feit. Een node
die zijn gebeurtenis ZELF meteen komt brengen, heeft geen van beide problemen:
seconden na het feit, zonder zendtijd, zolang zijn WiFi er is.

Het contract (afgesproken met de firmwarekant; wijzig het niet eenzijdig)::

    POST /api/sensorpush
    Authorization: Bearer {MM_PUSH_TOKEN}
    {"node":"<pubkey_prefix 12 hex>","seq":<uint32>,"boot":<uint32>,
     "hb_s":<uint16>,
     "events":[{"ch":int,"kind":"neer"|"op","text":str,"sev":"hoog"|"laag",
                "sim":0|1},...],
     "acked":[int,...]}

    200: {"ok":1,"ack":[int,...]}
    401 bij fout of ontbrekend token; 404 bij een onbekende node; 400 bij
    vormfouten; 503 zolang MM_PUSH_TOKEN leeg is.

Waarom een bearer-token en geen sessie. Dit is een machine-endpoint: de
tegenpartij is een microcontroller, en die heeft geen cookies, geen
inlogformulier en geen CSRF-token -- precies zoals de ingest-endpoints onder
/api/v1. De CSRF-controle van deze site is per route (routes_admin.check_csrf,
meshmoni._check_csrf) en deze route heeft er met opzet geen: er is geen sessie
om te vervalsen. Wat er wél is, is het token; leeg betekent dat de weg dicht
is (503 met de reden), dezelfde afspraak als MM_FW_NODE_USER en de
VAPID-sleutels.

Wat een push oplevert, in volgorde:

* de WAARNEMING: push_seen, de beloofde hartslag, de tellers en de bootteller
  gaan de databank in (db.record_push_seen). Een veranderde bootteller is een
  herstart -- geen alarm, wel zichtbaar op de nodepagina;
* de EVENTS worden alarmen (db.add_alert, source='push'), met de tekst zoals
  de node hem stuurde. Een oefening (sim=1) krijgt exact dezelfde markering
  als bij de IP-afleiding: sensornode.mark_simulation, dezelfde functie en
  niet een kopie. De kruisontdubbeling van add_alert (node, kind, kanaal,
  venster) vangt het geval dat de IP-poll dezelfde overgang ook nog ziet;
* de ACKED-lijst van de node bevestigt zijn eigen alarmen per kanaal --
  hetzelfde effect als de ack-knop, met een regel in het audittrail waarvan de
  actor de node is, zodat "wie heeft dit bevestigd" één eerlijk antwoord houdt;
* het ANTWOORD draagt de kanalen waarvan alarmen aan de SERVERKANT bevestigd
  zijn (/meshmoni of de beheer-UI) en die de node nog niet gehoord heeft.
  Eenmalig: de afleverstand staat in de databank (alerts.ack_pushed) en
  overleeft dus een herstart. Gaat het antwoord onderweg verloren, dan vangt
  de herhalingscache hieronder de retry -- zelfde boot en seq is dezelfde
  push, en die krijgt hetzelfde antwoord terug.

De stiltebewaking
-----------------
Een node die pusht, belooft daarmee een hartslag (``hb_s``). Blijft hij langer
dan drie keer die belofte stil (ondergrens 90 s, voor een node die elke paar
seconden zou beloven), dan is dat een alarm: "node stil (push)". De soort is
'stil' en de ernst is HOOG, en dat is een keuze met een reden: bij 'stil' is de
MELDER weg en weten wij niets meer -- niet of zijn diensten draaien, niet of
hij zelf nog leeft. Dat is dezelfde weging die de IP-afleiding en de firmware
aan een stilgevallen melder geven, en een bewakingsnode die wegvalt is nu net
de gebeurtenis waarvoor iemand die dit inschakelt gewekt wil worden. Komt de
node terug, dan volgt "node pusht weer" (soort 'op', laag) -- zelfde patroon
als elk herstel.

De bewaking leeft in het geheugen en ijkt na een serverherstart opnieuw:
_seed() zet voor elke node die ooit pushte het startpunt op NU, niet op zijn
oude push_seen. Een herstart geeft dus nooit een golf valse stiltemeldingen
(zelfde principe als de eerste ronde van de IP-afleiding), en een node die
tijdens de herstart écht wegviel, wordt alsnog gemeld zodra hij na het ijken
drie hartslagen stil blijft.

De begrenzing
-------------
Redelijk en niet streng: één node pusht hooguit elke hb_s seconden plus een
handvol gebeurtenissen, dus tientallen verzoeken per minuut per adres is al
ver boven elk eerlijk gebruik. Wie erover gaat krijgt 429 en de vraag het
rustiger aan te doen; de teller is per client-adres (ratelimit.client_ip,
dezelfde adreslogica als de loginbegrenzing) en leeft in dit proces.
"""
from __future__ import annotations

import hmac
import logging
import re
import threading
import time

from fastapi import APIRouter, Header, HTTPException, Request

from . import audit, config, db, ratelimit, sensornode

log = logging.getLogger("meshmanager.sensorpush")

router = APIRouter()

# Leeg = de weg is dicht (503 met reden). Op moduleniveau zoals de
# VAPID-sleutels, en met de MCS_-terugval die config.env elke variabele geeft.
TOKEN = config.env("PUSH_TOKEN", "").strip()

# --- de grenzen van één push ----------------------------------------------------
#
# Ruim boven wat een node ooit eerlijk stuurt (hij heeft een handvol kanalen),
# en ver onder wat iemand met een gestolen token de alerts-tabel in zou willen
# schuiven. De bodylimiet van de site (MAX_BODY_BYTES) geldt hier ook, maar die
# telt bytes en geen rijen.
MAX_EVENTS = 32
MAX_ACKED = 32

# De stiltegrens: drie beloofde hartslagen, met een vloer. De factor drie omdat
# één gemiste push nog een WiFi-hapering is (de voedingsmeting van 19 augustus
# liet precies dat zien) en drie op rij dat niet meer; de vloer van 90 s omdat
# drie keer een heel korte hartslag anders elke slaapstand van een radio tot
# alarm maakt.
SILENCE_FACTOR = 3
SILENCE_FLOOR_S = 90
WATCH_INTERVAL_S = 15

# De begrenzing per client-adres: verzoeken per venster. Eén node op zijn
# snelst is een push per paar seconden; 60 per minuut laat een handvol nodes
# achter één NAT-adres met ruimte over, en houdt een script dat losgaat buiten.
RATE_MAX = 60
RATE_WINDOW_S = 60
_RATE_MAX_KEYS = 1024

# Het contract zegt 12 hex, en dat is ook NODE_KEY_HEX -- de vorm waarin elke
# repeaterrij zijn sleutel draagt. Strenger dan find_repeater zelf zou eisen,
# met opzet: dit is een machinekoppelvlak met één afzender (onze firmware), en
# een afwijkende vorm is daar geen gebruiksgemak maar een bug die vroeg mag
# opvallen.
_NODE_RE = re.compile(r"^[0-9a-f]{12}$")

_EVENT_KINDS = ("neer", "op")
_EVENT_SEV = ("hoog", "laag")

_lock = threading.Lock()
# rid -> {"last": time.monotonic(), "hb_s": int, "stil": bool}
_hb: dict[int, dict] = {}
# rid -> {"boot": int, "seq": int, "resp": dict} -- de herhalingscache. Eén
# antwoord per node, want een node herhaalt alleen zijn LAATSTE push: hij gaat
# pas verder als hij een 200 heeft.
_last_response: dict[int, dict] = {}
# adres -> [tijdstippen] voor de begrenzing.
_rate: dict[str, list[float]] = {}

_state = {"pushes": 0, "last_push": None}


def reset() -> None:
    """Alle procesgeheugen weg. Alleen voor de tests, zoals ratelimit.reset."""
    with _lock:
        _hb.clear()
        _last_response.clear()
        _rate.clear()
        _state["pushes"] = 0
        _state["last_push"] = None


def enabled() -> bool:
    return bool(TOKEN)


# --- de vorm van het verzoek ----------------------------------------------------

class Vormfout(ValueError):
    """Een verzoek dat niet aan het contract voldoet; de tekst zegt waar."""


def _uint(waarde, hi: int, veld: str) -> int:
    """Een niet-negatief geheel getal binnen zijn bereik, of een Vormfout.

    ``bool`` is in Python een int en wordt hier apart geweigerd: een firmware
    die ``true`` stuurt waar een teller hoort, stuurt niet wat ze denkt te
    sturen, en dat hoort een 400 te zijn en geen stille 1.
    """
    if isinstance(waarde, bool) or not isinstance(waarde, int):
        raise Vormfout(f"{veld} moet een geheel getal zijn")
    if not 0 <= waarde <= hi:
        raise Vormfout(f"{veld} valt buiten 0..{hi}")
    return waarde


def _parse(body) -> dict:
    """Het contract afgedwongen, veld voor veld. Werpt Vormfout met de plek erin.

    Streng, en dat is hier de vriendelijke keuze: de enige afzender is onze
    eigen firmware, dus elke afwijking is een bug aan een van beide kanten --
    en een 400 met de veldnaam is de snelste weg om te zien aan welke.
    """
    if not isinstance(body, dict):
        raise Vormfout("de body moet een JSON-object zijn")
    node = str(body.get("node") or "").strip().lower()
    if not _NODE_RE.fullmatch(node):
        raise Vormfout("node moet een pubkey_prefix van 12 hextekens zijn")
    uit = {
        "node": node,
        "seq": _uint(body.get("seq"), 2**32 - 1, "seq"),
        "boot": _uint(body.get("boot"), 2**32 - 1, "boot"),
        "hb_s": _uint(body.get("hb_s"), 2**16 - 1, "hb_s"),
        "events": [],
        "acked": [],
    }

    events = body.get("events", [])
    if not isinstance(events, list) or len(events) > MAX_EVENTS:
        raise Vormfout(f"events moet een lijst van hoogstens {MAX_EVENTS} zijn")
    for i, ev in enumerate(events):
        if not isinstance(ev, dict):
            raise Vormfout(f"events[{i}] moet een object zijn")
        kind = ev.get("kind")
        if kind not in _EVENT_KINDS:
            raise Vormfout(f"events[{i}].kind moet 'neer' of 'op' zijn")
        sev = ev.get("sev")
        if sev not in _EVENT_SEV:
            raise Vormfout(f"events[{i}].sev moet 'hoog' of 'laag' zijn")
        sim = ev.get("sim")
        if sim not in (0, 1) or isinstance(sim, bool):
            raise Vormfout(f"events[{i}].sim moet 0 of 1 zijn")
        text = ev.get("text")
        if not isinstance(text, str) or not text.strip():
            raise Vormfout(f"events[{i}].text moet een niet-lege tekst zijn")
        uit["events"].append({
            "ch": _uint(ev.get("ch"), 255, f"events[{i}].ch"),
            "kind": kind, "sev": sev, "sim": sim,
            "text": text.strip()[:500],
        })

    acked = body.get("acked", [])
    if not isinstance(acked, list) or len(acked) > MAX_ACKED:
        raise Vormfout(f"acked moet een lijst van hoogstens {MAX_ACKED} zijn")
    uit["acked"] = [_uint(ch, 255, f"acked[{i}]")
                    for i, ch in enumerate(acked)]
    return uit


# --- de bearer-controle, herbruikbaar voor verwante node-push-endpoints ---------
#
# ``/api/companion`` (companions.py, de instant-push van locatie/valmeldingen)
# deelt dezelfde vertrouwde afzender als deze push -- de MeshUptime-node -- en
# hoort dus dezelfde deur te delen: één token, één 401/503-vorm, niet een tweede
# kopie die later uiteen kan lopen. Vandaar dat deze controle hier een eigen,
# los aanroepbare functie is en niet alleen inline in ``sensorpush`` hieronder.

def require_push_token(authorization: str | None) -> None:
    """De bearer-controle van dit endpoint. Werpt HTTPException (503 als de weg
    dicht staat, 401 bij een ontbrekend of fout token); geeft niets terug bij
    een geldig token."""
    if not TOKEN:
        raise HTTPException(503, "gebeurtenis-push staat uit op deze server "
                                 "(MM_PUSH_TOKEN is leeg)")
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(401, "Bearer-token vereist")
    if not hmac.compare_digest(authorization.split(" ", 1)[1].strip(), TOKEN):
        raise HTTPException(401, "Ongeldig token")


# --- de begrenzing ---------------------------------------------------------------

def _rate_check(adres: str) -> bool:
    """True als dit verzoek mag; anders is het venster vol. Onder het slot,
    en begrensd in geheugen zoals de loginbegrenzing (MAX_ENTRIES daar)."""
    now = time.monotonic()
    with _lock:
        stampen = [t for t in _rate.get(adres, []) if now - t < RATE_WINDOW_S]
        if len(stampen) >= RATE_MAX:
            _rate[adres] = stampen
            return False
        stampen.append(now)
        _rate[adres] = stampen
        if len(_rate) > _RATE_MAX_KEYS:
            # De sleutels met het oudste laatste verzoek eerst weg: die zijn
            # het dichtst bij vervallen, en een aanvaller die adressen rondpompt
            # mag dit dict niet tot het werkgeheugen laten groeien.
            for sleutel, _ in sorted(_rate.items(),
                                     key=lambda kv: kv[1][-1] if kv[1] else 0.0
                                     )[:len(_rate) - _RATE_MAX_KEYS]:
                del _rate[sleutel]
        return True


def check_rate(request: Request) -> None:
    """De begrenzing van dit endpoint, herbruikbaar voor verwante node-push-
    endpoints (zie ``require_push_token`` hierboven voor dezelfde reden). Werpt
    429 als het venster vol is."""
    if not _rate_check(ratelimit.client_ip(request)):
        raise HTTPException(429, "Te veel pushes; probeer het zo terug")


# --- de stiltebewaking -----------------------------------------------------------

def _silence_after_s(hb_s: int) -> int:
    return max(SILENCE_FLOOR_S, SILENCE_FACTOR * int(hb_s or 0))


def _seen(rid: int, hb_s: int) -> None:
    """Deze node pushte zojuist: het ijkpunt vooruit, en herstel melden als hij
    stil stond. De herstelmelding komt HIER en niet in de watchdog: het moment
    dat hij terug is, is het moment dat hij binnenkomt."""
    now = time.monotonic()
    with _lock:
        entry = _hb.get(rid)
        herstel = entry is not None and entry["stil"]
        _hb[rid] = {"last": now, "hb_s": int(hb_s), "stil": False}
    if herstel:
        db.add_alert(rid, "node pusht weer", source="push",
                     severity="laag", kind="op")
        log.info("Sensorpush: node %s pusht weer na een stilte", rid)


def _seed() -> None:
    """Na een (server)start: elke node die ooit pushte krijgt NU als ijkpunt.

    Niet zijn oude ``push_seen``, met opzet -- dat zou elke deploy die langer
    duurt dan drie hartslagen een golf valse stiltemeldingen geven, en een
    alarmkanaal dat bij elke deploy blaft leest niemand na een week nog.
    Dezelfde regel als de eerste ronde van de IP-afleiding. De prijs staat
    erbij: een node die tijdens de herstart wegviel wordt pas drie hartslagen
    ná het ijken gemeld. setdefault, zodat een push die de draad vóór was zijn
    eigen, versere ijkpunt houdt.
    """
    now = time.monotonic()
    with _lock:
        for r in db.push_nodes():
            _hb.setdefault(int(r["id"]),
                           {"last": now, "hb_s": int(r["push_hb_s"] or 0),
                            "stil": False})


def _watch_once() -> int:
    """Eén bewakingsronde: wie is over zijn stiltegrens? Geeft het aantal terug.

    ``stil`` klapt om vóór het alarm geschreven wordt, en er komt geen tweede
    alarm zolang hij stil blijft: één stilte is één gebeurtenis, hoeveel rondes
    ze ook duurt -- dezelfde regel als "geen overgang, geen alarm" bij de poll.
    """
    now = time.monotonic()
    stil_geworden = []
    with _lock:
        for rid, entry in _hb.items():
            grens = _silence_after_s(entry["hb_s"])
            if not entry["stil"] and now - entry["last"] > grens:
                entry["stil"] = True
                stil_geworden.append((rid, grens))
    for rid, grens in stil_geworden:
        db.add_alert(rid, f"node stil (push): langer dan {grens} s geen push",
                     source="push", severity="hoog", kind="stil")
        log.warning("Sensorpush: node %s is stil (grens %s s)", rid, grens)
    return len(stil_geworden)


def is_stil(rid) -> bool:
    """Of de stiltebewaking deze node op dit moment stil acht. Voor de UI."""
    with _lock:
        entry = _hb.get(int(rid or 0))
        return bool(entry and entry["stil"])


_thread = None


def _run() -> None:
    _seed()
    while True:
        time.sleep(WATCH_INTERVAL_S)
        try:
            _watch_once()
        except Exception:               # noqa: BLE001 -- zelfde reden als elke ronde
            log.exception("Sensorpush-stiltebewaking: ronde afgebroken")


def start() -> None:
    """De stiltebewaking starten -- alleen als de weg zelf open is.

    Zonder token komt er nooit een push binnen, dus zou de bewaking een draad
    zijn die elke vijftien seconden vaststelt dat er niets te bewaken valt.
    """
    global _thread
    if not TOKEN:
        log.info("Gebeurtenis-push staat uit (MM_PUSH_TOKEN is leeg)")
        return
    if _thread is not None:
        return
    _thread = threading.Thread(target=_run, daemon=True,
                               name="meshmanager-sensorpush")
    _thread.start()


# --- het endpoint ----------------------------------------------------------------

@router.post("/api/sensorpush")
async def sensorpush(request: Request,
                     authorization: str | None = Header(default=None)):
    """Eén push van één node: events erin, ack-kanalen eruit. Zie het contract
    in de moduletekst; de volgorde van verwerken staat daar ook, met redenen."""
    require_push_token(authorization)
    check_rate(request)

    try:
        body = await request.json()
    except ValueError:
        raise HTTPException(400, "Geen geldige JSON")
    try:
        push = _parse(body)
    except Vormfout as fout:
        raise HTTPException(400, str(fout))

    rij = db.find_repeater(push["node"])
    if rij is None:
        # 404 en geen stille rij erbij: dit endpoint mag geen nodes AANMAKEN.
        # Wie het token heeft, heeft daarmee nog geen recht om de nodelijst te
        # vullen -- een node bestaat hier doordat hij via de gewone wegen
        # binnenkwam of door een beheerder is gezet.
        raise HTTPException(404, "Onbekende node")
    rid = int(rij["id"])

    # De herhalingscache. Zelfde boot en seq is dezelfde push: het 200-antwoord
    # is dan onderweg verloren gegaan, en de node hoort exact hetzelfde terug
    # te krijgen -- inclusief de ack-kanalen die anders verdampt zouden zijn,
    # want die zijn bij de eerste verwerking al als gemeld aangemerkt.
    with _lock:
        vorige = _last_response.get(rid)
        if (vorige is not None and vorige["boot"] == push["boot"]
                and vorige["seq"] == push["seq"]):
            return dict(vorige["resp"])

    # Eerst de waarneming, net als bij de poll: dat de node pushte hoort vast
    # te staan ook als het verwerken hierna ergens op strandt.
    herstart = db.record_push_seen(rid, push["hb_s"], push["seq"], push["boot"])
    if herstart:
        log.info("Sensorpush: node %s is herstart (bootteller %s)",
                 push["node"], push["boot"])
    _seen(rid, push["hb_s"])

    nieuw = 0
    for ev in push["events"]:
        alert = {"text": ev["text"], "kind": ev["kind"]}
        if ev["sim"]:
            # Dezelfde functie als de IP-afleiding, geen tweede spelling.
            sensornode.mark_simulation(alert)
        if db.add_alert(rid, alert["text"], source="push", channel=ev["ch"],
                        severity=ev["sev"], kind=alert["kind"]):
            nieuw += 1
            log.warning("ALERT (push) van %s: %s", push["node"],
                        alert["text"][:120])

    # De node bevestigt zijn eigen alarmen. Eén audit-regel per push en niet
    # per kanaal: het is één handeling van één afzender, en het trail moet na
    # te vertellen zijn zonder te verzuipen.
    bevestigd: list[str] = []
    for ch in push["acked"]:
        aantal = db.ack_alerts_from_node(rid, ch)
        if aantal:
            bevestigd.append(f"kanaal {ch} ({aantal})")
    if bevestigd:
        audit.log(f"node {push['node']}", "node.uitvragen", rep=rid,
                  detail="alarm(en) bevestigd door de node zelf, via push: "
                         + ", ".join(bevestigd))

    # En de andere richting: wat hier bevestigd is en de node nog niet weet.
    acks = db.pop_acked_channels(rid)

    resp = {"ok": 1, "ack": acks}
    with _lock:
        _last_response[rid] = {"boot": push["boot"], "seq": push["seq"],
                               "resp": dict(resp)}
        _state["pushes"] += 1
        _state["last_push"] = db.utcnow()
    return resp


def status_summary() -> dict:
    """Wat de pagina's over deze weg te melden hebben: aan of uit, en waarom."""
    return {
        "enabled": enabled(),
        "reason": None if enabled() else
                  "MM_PUSH_TOKEN is leeg; zet hem in .env om de weg te openen",
        "pushes": _state["pushes"],
        "last_push": _state["last_push"],
    }
