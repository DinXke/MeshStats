"""Telemetrie ophalen van een node waarvoor we geen inloggegevens hebben.

Dit kan omdat MeshCore het toelaat, niet omdat er iets omzeild wordt. Het
onderzoek staat in ``docs/node-management.md``; de kern in drie regels:

* ``guest_password[0] = 0`` in ``CommonCLI.h`` -- het gastwachtwoord is standaard
  leeg, en een leeg wachtwoord matcht erop. Een onbekende node komt dus binnen
  als ``PERM_ACL_GUEST`` op elke repeater waarvan de eigenaar nooit
  ``set guest.password`` gedraaid heeft.
* Een gast krijgt ``REQ_TYPE_GET_STATUS`` zonder énige rechtencontrole -- de
  broncode zegt het er zelf bij: *"guests can also access this now"* -- plus de
  burenlijst, plus basistelemetrie (accuspanning en chiptemperatuur; externe
  sensoren blijven achter).
* De CLI blijft dicht, want ``handleCommand`` wordt alleen onder ``isAdmin()``
  bereikt. Er valt dus niets te zetten, en dat is door de overkant afgedwongen --
  de beste soort.

**Er is geen firmwarewijziging voor nodig.** De monitormachinerie doet dit al:
een monitorregel zonder wachtwoord logt in met een lege string, en een pollronde
vraagt precies die drie dingen op. Wat hier gebeurt is die machinerie aansturen
over HTTP en de uitkomst eerlijk tonen.

Wat dit NIET is, en dat is een bewuste keuze uit het ontwerp: geen ronde over
alles wat ooit gehoord is. De beheerder wijst aan wie bevraagd wordt. Het gaat om
andermans apparatuur op een gedeelde band, en een site die elke node aanklopt die
hij ooit hoorde is niet nieuwsgierig maar onbeschoft.

En het is eerlijk over wat het aanzet. Uitvragen betekent hier: **de node aan de
monitorlijst van de afzender toevoegen**. Dat is geen kiekje maar een
verhouding -- hij gaat mee in elke volgende pollronde tot iemand hem eruit haalt.
Doen alsof het eenmalig is zou de firmware verkeerd voorstellen, en de knop om
hem te vergeten staat er daarom naast.
"""

from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timezone

from . import commanding, db, firmware, nodeconfig

log = logging.getLogger(__name__)

# Wat een pollronde per gemonitorde node kost, uit de firmwareconstanten:
# MON_STEP_MS is 30 s wachten per antwoord en er zijn vier stappen (login,
# status, telemetrie, buren), met MON_GAP_MS van 3 s tussen twee nodes. Dat is
# een BOVENGRENS voor de wandklok en niet de zendtijd zelf: een node die
# antwoordt is in seconden klaar. De bovengrens is wat je moet weten voordat je
# klikt, want dat is wat de machine bezet houdt als er niemand antwoordt.
STEP_S = 30
STEPS_PER_NODE = 4
GAP_S = 3

# Hoe lang een uitvraging de tijd krijgt voordat stilte als uitkomst geldt. Ruim
# boven de bovengrens van één node, zodat een ronde met een paar gemonitorde
# nodes ervoor niet als stilte wordt weggeschreven.
VERIFY_AFTER_S = 300

# Wat een afzender minstens moet draaien. Bewust NIET
# commanding.MIN_MON_CMD_VERSION: dat getal gaat over het woord 'settings' op het
# cmd-topic, en uitvragen raakt dat topic niet aan -- het loopt over /api/mon.
# Wat hier nodig is, is de monitormachinerie mét de tellers per verzoeksoort en de
# loginuitkomst, want daarop rusten de drie stiltes: 'lr' kwam in 1.3.1 en de
# tellers per soort in 1.4.0. Strenger zijn dan nodig zou een node uitsluiten die
# het prima kan, en dat is precies het soort onwaarheid dat de rest van dit
# bestand probeert te vermijden.
MIN_SENDER_VERSION = (1, 4, 0)

_JOBS_KEY = "discovery_jobs"
_JOBS_MAX = 100


# --- wie stuurt het ------------------------------------------------------------

def sender() -> dict:
    """De repeater van waaraf uitvraagverzoeken vertrekken.

    Eén functie, met opzet, want dit is het enige punt waar "wie stuurt dit"
    vandaan komt. Vandaag is er één node met onze firmware en een beheeradres, en
    dan is de keuze geen keuze. Zodra er meer zijn, wordt dit een lijst met een
    volgorde -- dezelfde vorm die ``monitors.candidates()`` al heeft -- en dan
    verandert alleen deze functie en niets erboven.

    Een beheeradres is de harde eis: uitvragen loopt over ``/api/mon`` op de
    afzender, en zonder adres is er geen weg naar zijn monitorlijst.
    """
    uit = {"rep": None, "blocker": "", "candidates": []}
    if not firmware.NODE_USER:
        uit["blocker"] = "no_credentials"
        return uit

    for rep in db.q("SELECT * FROM repeaters ORDER BY sort_order, name"):
        if not str(rep["ota_host"] or "").strip():
            continue
        versie = commanding.parse_version(rep["fw_meshmanager"])
        if versie is None or versie < MIN_SENDER_VERSION:
            continue
        uit["candidates"].append(rep)

    if not uit["candidates"]:
        uit["blocker"] = "no_sender"
        return uit
    uit["rep"] = uit["candidates"][0]
    return uit


# --- wat is er te kiezen -------------------------------------------------------

def heard(host: str) -> dict:
    """De lijst repeaters die de afzender gehoord heeft, om uit te kiezen.

    Komt van de afzender zelf en niet uit onze eigen database, en dat is het
    punt: wat wij ooit in het verkeer zagen zegt niets over wat déze node nu kan
    bereiken. De lijst draagt per regel of hij live gehoord is (met SNR) of uit
    een bewaarde advert komt -- de firmware verzint geen SNR voor een node die
    hij deze keer niet gehoord heeft, en dat onderscheid hoort door te reizen.
    """
    lijst = nodeconfig.monitors(host)
    if not lijst["ok"]:
        return {"ok": False, "error": lijst["error"], "entries": [], "monitored": []}
    bekend = {str(e.get("k", "")).lower() for e in lijst["entries"]}
    kandidaten = []
    for regel in lijst["heard"]:
        # De monitor geeft de volle 32-byte sleutel; de rij die hier straks van
        # gemaakt wordt draagt de korte. Hier al inkorten, zodat het formulier de
        # vorm doorgeeft waarin hij bewaard wordt en er nergens twee vormen van
        # dezelfde node naast elkaar bestaan.
        vol = str(regel.get("k", ""))
        sleutel = db.node_key(vol) or vol.lower()
        kandidaten.append({
            "key": sleutel,
            "full": vol,
            # De naam die de monitor meegeeft, en anders die uit contacts. Een
            # node zonder treffer houdt zijn hex, maar dan als hex op het scherm.
            "name": regel.get("n") or db.contact_name_for(sleutel) or "",
            "snr": regel.get("snr"),
            "age": regel.get("age"),
            "cached": bool(regel.get("cached")),
            "already": sleutel.lower() in bekend
                       or sleutel.lower()[:12] in {b[:12] for b in bekend},
        })
    return {"ok": True, "error": "", "entries": kandidaten,
            "monitored": lijst["entries"]}


def cost(host: str, extra: int = 1) -> dict:
    """Wat een uitvraging kost, in de vorm die vóór de klik op het scherm hoort.

    Eerlijk over twee dingen die makkelijk te verzwijgen zijn. Ten eerste: één
    poll gaat over ÁLLE gemonitorde nodes en niet alleen over de node die je
    aanwijst -- de firmware kent geen poll van één (``MA_POLL`` zet
    ``_mon_next_round`` en de ronde loopt de lijst af). Ten tweede: het getal
    hieronder is een bovengrens voor de wandklok, geen zendtijd. Een node die
    antwoordt is in seconden klaar; een node die zwijgt kost de volle
    wachttijd, en dat is precies het geval dat je aan het uitproberen bent.
    """
    lijst = nodeconfig.monitors(host)
    huidig = len(lijst["entries"]) if lijst["ok"] else 0
    nodes = huidig + max(0, extra)
    return {
        "ok": lijst["ok"], "error": lijst.get("error", ""),
        "monitored": huidig, "nodes": nodes,
        "requests": nodes * STEPS_PER_NODE,
        "worst_secs": nodes * (STEPS_PER_NODE * STEP_S + GAP_S),
    }


# --- het grootboek -------------------------------------------------------------

def _jobs() -> dict:
    try:
        return json.loads(db.get_setting(_JOBS_KEY, "{}")) or {}
    except ValueError:
        return {}


def _save(data: dict) -> None:
    if len(data) > _JOBS_MAX:
        data = dict(sorted(data.items(), key=lambda kv: kv[1].get("at", 0))[-_JOBS_MAX:])
    db.set_setting(_JOBS_KEY, json.dumps(data))


def job(key: str) -> dict:
    return _jobs().get(db.node_key(key) or "", {})


def jobs() -> dict:
    return _jobs()


def _record(key: str, **velden) -> None:
    sleutel = db.node_key(key) or ""
    if not sleutel:
        return
    data = _jobs()
    regel = data.get(sleutel, {})
    regel.update(velden)
    regel.setdefault("key", sleutel)
    data[sleutel] = regel
    _save(data)


# --- uitvragen -----------------------------------------------------------------

def probe(host: str, key: str, label: str = "") -> dict:
    """Voeg de node toe aan de monitorlijst van de afzender en start een ronde.

    Zonder wachtwoord, en dat is de hele truc: de firmware logt dan in met een
    lege string, en de overkant vergelijkt die met zijn gastwachtwoord -- dat
    standaard leeg is. Geen omzeiling, gewoon de deur die openstaat.
    """
    # node_key en niet key_prefix: die laatste KEURT op lengte en kort niet in,
    # en de gehoorde lijst levert de volle 32-byte sleutel uit een advert. Dat
    # verschil leverde rijen op met een sleutel van 64 tekens waar de rest van het
    # systeem er 12 verwacht -- geen naamtreffer, en geen uitvraging, want de
    # monitor adresseert op de korte vorm. Normaliseren aan de rand, hier, en niet
    # repareren aan de andere kant.
    sleutel = db.node_key(key)
    uit = {"ok": False, "step": "", "msg": "", "key": sleutel}
    if not sleutel or len(sleutel) < 8:
        uit.update(step="sleutel", msg="een sleutel van minstens 8 hextekens graag")
        return uit

    # De naam uit contacts als de aanroeper er geen meegaf. Daar staan de namen
    # uit adverts, ook van nodes zonder repeaterrij, en ze waren dus al bekend
    # toen deze pagina de hex als naam ging gebruiken. Geen treffer? Dan blijft
    # het leeg, en toont de pagina de sleutel als sleutel in plaats van hem als
    # naam te vermommen.
    naam = (label or "").strip()[:30] or db.contact_name_for(sleutel)[:30]

    # De rij eerst, en dat is een ordekwestie met gevolgen. get_or_create_repeater
    # WEIGERT boven MAX_REPEATERS in plaats van te snoeien, en die weigering moet
    # vallen vóórdat we de node aan de monitorlijst toevoegen: andersom staat er
    # een regel op de afzender die elke ronde zendtijd kost voor een node die hier
    # nooit een rij krijgt. Een wees op andermans band, en niets dat hem opruimt.
    #
    # Het legt ook de herkomst vast voordat het eerste bericht binnenkomt, zodat
    # het er al staat als iemand de eerste grafiek bekijkt.
    try:
        db.mark_guest_polled(sleutel, True)
    except ValueError as err:
        uit.update(step="opslag", msg=str(err))
        return uit

    toevoegen = nodeconfig.post_mon(host, {"act": "add", "key": sleutel, "name": naam})
    if not toevoegen["ok"]:
        uit.update(step="toevoegen", msg=toevoegen["error"])
        return uit

    # De monitorwijziging wordt door de firmware in msnet_loop() afgehandeld, één
    # actie tegelijk; een poll in hetzelfde verzoek zou tegen die wachtrij
    # botsen ("previous change not applied yet"). Vandaar twee verzoeken, en de
    # tweede pas nadat de eerste geland is.
    time.sleep(1.5)
    pollen = nodeconfig.post_mon(host, {"act": "poll"})
    if not pollen["ok"]:
        uit.update(step="poll", msg=pollen["error"])
        return uit

    nu = time.time()
    _record(sleutel, at=nu, name=naam, via=host, result="gevraagd",
            asked=datetime.fromtimestamp(nu, timezone.utc).isoformat(timespec="seconds"),
            seen_before=_newest_stat(sleutel))
    uit["ok"] = True
    log.info("Uitvraging zonder inloggegevens gestart voor %s via %s", sleutel, host)
    return uit


def forget(host: str, key: str) -> dict:
    """Uit de monitorlijst halen. Het grootboek houdt wat we geleerd hebben."""
    sleutel = db.node_key(key)
    weg = nodeconfig.post_mon(host, {"act": "del", "key": sleutel})
    if weg["ok"]:
        _record(sleutel, result="vergeten")
        # De vlag blijft staan. Wat er verzameld is, is verzameld op deze manier,
        # en de grafiek moet dat over een maand nog kunnen zeggen. Hem afzetten
        # zou de geschiedenis herschrijven naar cijfers die de node zelf
        # gepubliceerd zou hebben, en dat heeft hij nooit gedaan.
        pass
    return weg


def poll_interval(host: str) -> dict:
    """Het pollinterval van de afzender, in seconden.

    **Per monitor en niet per node**, en dat is geen ontwerpkeuze van ons maar de
    vorm van de firmware: ``MA_POLL`` zet ``_mon_next_round`` en de ronde loopt de
    hele monitorlijst af. Er bestaat geen ronde van één node, dus er bestaat geen
    interval van één node. Er hier alsnog een per-node-veld boven bouwen zou een
    knop zijn die iets belooft wat de firmware niet kan doen.

    Wat per node WEL bestaat is aan of uit: in de lijst staan of eruit. Dat is de
    knop 'Vergeten', en dat is de eerlijke vorm van 'zet het uit voor deze node'.

    Het herhaald uitvragen hoeft dus niet gepland te worden -- de node doet het
    zelf, standaard elke 900 s. Een tweede planner hierboven zou een ronde vragen
    die er toch al kwam.
    """
    lijst = nodeconfig.monitors(host)
    if not lijst["ok"]:
        return {"ok": False, "error": lijst["error"], "secs": None}
    return {"ok": True, "error": "", "secs": lijst.get("interval")}


def set_poll_interval(host: str, secs: int) -> dict:
    """Het pollinterval zetten. Ondergrens 60 s, zoals de firmware zelf klemt."""
    n = max(60, min(65535, int(secs or 0)))
    return nodeconfig.post_mon(host, {"act": "iv", "secs": n})


def _newest_stat(key: str) -> str | None:
    rij = db.qone("SELECT last_seen FROM repeaters WHERE pubkey_prefix=?", (key,))
    return (rij["last_seen"] if rij else None) or None


def verify(host: str, now: float | None = None) -> list:
    """Beoordeel uitvragingen waarvan de termijn verstreken is.

    Drie stiltes, en ze zien er van hieraf niet identiek uit als je de monitor
    ernaar vraagt. ``/api/mon`` geeft per regel de loginuitkomst en per soort
    verzoek een teller, en de gehoorde lijst zegt of we zijn adverts nog binnen
    krijgen. Daarmee valt te scheiden wat anders alle drie "geen antwoord" heet:

    * **buiten bereik** -- login onbeantwoord én geen adverts. Een radioprobleem.
    * **gastwachtwoord ingesteld** -- login onbeantwoord terwijl we hem wél horen.
      Hij is er, en hij laat ons niet binnen. Dit is de eigenaar die
      ``set guest.password`` gedraaid heeft, of een node met een ACL waar wij niet
      in staan.
    * **niet ondersteund** -- login gelukt en daarna niets. Ongewoon: een gast
      hoort status te krijgen zonder rechtencontrole. Oudere firmware, of een
      variant die de aanvraag niet kent.

    De middelste is de enige waarvan de reden niet zeker is, en dat staat er dan
    ook zo bij. 'Wij horen hem' is bewijs dat hij bestaat, niet bewijs van
    waaróm hij zwijgt.
    """
    now = time.time() if now is None else now
    veranderd = []
    lijst = nodeconfig.monitors(host)
    gehoord = {str(h.get("k", "")).lower()[:12] for h in (lijst.get("heard") or [])}
    per_sleutel = {str(e.get("k", "")).lower()[:12]: e for e in (lijst.get("entries") or [])}

    for sleutel, regel in _jobs().items():
        if regel.get("result") != "gevraagd":
            continue
        if now - float(regel.get("at") or 0) < VERIFY_AFTER_S:
            continue

        nu_gezien = _newest_stat(sleutel)
        eerder = regel.get("seen_before")
        if nu_gezien and (not eerder or nu_gezien > eerder):
            _record(sleutel, result="antwoord binnen")
            veranderd.append((sleutel, "antwoord binnen"))
            continue

        regel_mon = per_sleutel.get(sleutel[:12])
        login_ok = bool(regel_mon and regel_mon.get("lr"))
        te_horen = sleutel[:12] in gehoord
        if login_ok:
            uitkomst = "niet ondersteund"
        elif te_horen:
            uitkomst = "gastwachtwoord ingesteld"
        else:
            uitkomst = "buiten bereik"
        _record(sleutel, result=uitkomst)
        veranderd.append((sleutel, uitkomst))
        log.info("Uitvraging van %s leverde niets op: %s", sleutel, uitkomst)
    return veranderd
