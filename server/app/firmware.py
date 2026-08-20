"""Firmware-images van GitHub Releases naar een node schrijven.

Waarom dit een eigen module is en niet een paar routes in ``routes_admin``: er
zitten drie netwerkgrenzen in (GitHub, de node, de broker) en precies één daarvan
mag de beheerpagina laten wachten -- geen. Alles wat traag of onbetrouwbaar is
gebeurt hier, in een achtergronddraad met een leesbare toestand, naar het model
van ``tsdb`` en ``clocksync``.

De volgorde waarin dit werkt is de hele veiligheidsredenering:

1. De **server** haalt het image op bij GitHub en controleert de SHA-256 tegen
   het ``.sha256``-bestand dat naast het image gepubliceerd is. Een halve
   download gaat dus nooit de lucht in. Dit kost niets extra: de bytes moeten
   toch door dit proces heen.
2. De **node** krijgt dezelfde digest mee en controleert hem nog een keer,
   voordat hij zijn bootpartitie omzet. Dat is geen dubbelop maar een tweede
   foutdomein: het eerste vangt een kapotte download, het tweede een kapotte
   verbinding tussen server en node.
3. Pas als beide kloppen herstart de node. Faalt er iets, dan draait hij door op
   wat hij had -- zie ``docs/firmware-upgrade.md`` voor waarom "hij herstartte"
   op de oude weg juist niets bewees.

Wat deze module NIET doet, en waarom niet:

- **Geen upgrade over LoRa.** Een image is ~1,3 MB. Via een monitorende repeater
  op BW 62,5 kHz / SF 8 en binnen de Europese duty-cycle praat je over dagen
  zendtijd. Er is geen codering die die orde van grootte verandert, dus een node
  zonder IP-pad krijgt geen knop maar een uitleg. Voor de dakrepeater van dit
  project is dat een blijvende toestand, geen tijdelijke.
- **Geen upgradewoord op het ``cmd``-topic.** Dat topic is bereikbaar voor
  iedereen met brokergegevens en aanvaardt daarom precies drie vaste woorden.
  Een image gaat over HTTP, achter de login van de node zelf.
- **Geen beste gok bij het kiezen van een image.** Een node die niet zegt met
  welke bouwomgeving hij gemaakt is, krijgt niets. Het verkeerde image op een
  node op een dak is niet meer over de lucht recht te zetten.
"""

from __future__ import annotations

import base64
import configparser
import hashlib
import json
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from . import config, db

# --- instellingen ------------------------------------------------------------

# owner/repo waar de releases staan. Normaal afgeleid uit de git-remote van deze
# werkkopie, want die staat er al en kan niet stilletjes naar een andere repo
# wijzen dan waar de code uit komt. De omgevingsvariabele is er voor de
# container, waar geen .git zit -- zie repo_slug().
REPO_OVERRIDE = config.env("GITHUB_REPO", "").strip()

# Inloggegevens van de beheerpagina van de nodes. Eén paar voor alle nodes: het
# zijn de nodes van één beheerder en de firmware kent maar één account. Niet in
# de database, omdat een wachtwoord dat in een back-up van de statistieken
# meelift een wachtwoord is dat je niet meer kunt overzien.
NODE_USER = config.env("FW_NODE_USER", "").strip()
NODE_PASS = config.env("FW_NODE_PASS", "")

# GitHub laat anoniem 60 verzoeken per uur toe, per IP, gedeeld met alles wat er
# verder op die machine draait. De beheerpagina zou dat in een middagje opmaken
# door bij elke verversing opnieuw te vragen, dus de lijst wordt gecachet en bij
# een fout blijft de laatste goede lijst staan.
CACHE_MIN = int(config.env("FW_CACHE_MIN", "15") or 15)
GITHUB_TIMEOUT_S = 10
DOWNLOAD_TIMEOUT_S = 60

# Naar de node: schrijven van 1,3 MB naar flash duurt op een ESP32 tientallen
# seconden en de node antwoordt pas als hij klaar is.
PUSH_TIMEOUT_S = 180
PROBE_TIMEOUT_S = 5

# Hoe lang we op een node wachten die zou moeten herstarten. Ruim: een node in
# zuinige modus kan zijn wifi opnieuw moeten opbouwen.
RETURN_WAIT_S = 150
RETURN_POLL_S = 5

# Een image is nooit kleiner dan dit en nooit groter dan de applicatiepartitie.
MIN_IMAGE = 200_000
MAX_IMAGE = 6_553_600

# Beide voorvoegsels, en dat blijft even zo. De images heten sinds de
# hernoeming meshmanager-<env>-<versie>.bin, maar de releases die er nu al
# liggen heten meshmanager-...: een site die alleen de nieuwe naam kent, ziet
# in elke oudere release nul images en kan dus niet terug. Juist terug
# kunnen is waar de rollback voor bestaat.
#
# Weg te halen als er geen release meer in de lijst staat met de oude naam.
# Dat is af te lezen op /admin/firmware en niet te gokken.
ASSET_RE = re.compile(
    r"^(?:meshmanager|meshstats)-(?P<env>.+)-(?P<version>\d+\.\d+\.\d+)\.bin$")

# Bouwomgevingen die van naam veranderd zijn: {wat de node meldt: hoe het
# image nu heet}.
#
# Zonder dit zou de hernoeming naar MeshManager alleen met een USB-kabel te
# installeren zijn, en dat is op een dak geen upgradeweg. Een node die nog
# 1.12.0 draait, is gebouwd onder heltec_v4_repeater_meshstats en meldt die
# naam; de release die hem eroverheen moet helpen draagt
# heltec_v4_repeater_meshmanager. Precies een keer moeten die twee elkaar
# vinden -- daarna meldt de node zelf de nieuwe naam.
#
# Alleen in deze richting vertaald: van wat een node zegt naar hoe een image
# heet. Andersom zou een oud image aan een nieuwe node aangeboden worden, en
# dan draait er firmware die op meshcore/ publiceert op een node waarvan de
# site denkt dat hij om is.
#
# Weg te halen zodra geen enkele node de oude envnaam nog meldt; /admin
# toont per node wat hij meldt.
ENV_ALIAS = {
    "heltec_v4_repeater_meshstats": "heltec_v4_repeater_meshmanager",
}


def image_for(release: dict, env: str) -> dict | None:
    """Het image uit deze release dat bij deze bouwomgeving hoort.

    Eerst op de naam die de node zelf meldt, en pas als die niets oplevert
    via ENV_ALIAS. Die volgorde is niet willekeurig: zolang een release nog
    een image met de oude envnaam bevat, is dat het image dat er echt bij
    hoort, en een alias die daar overheen walst zou een terugrol naar een
    oudere versie het verkeerde bestand geven.
    """
    images = release.get("images") or {}
    hit = images.get(env)
    if hit:
        return hit
    alias = ENV_ALIAS.get(env)
    return images.get(alias) if alias else None

_lock = threading.Lock()
_cache: dict = {"at": 0.0, "items": [], "error": "", "slug": ""}


# --- welke repo ---------------------------------------------------------------

def repo_slug() -> str:
    """owner/repo van de repository waar de firmware-releases staan.

    Volgorde: de omgevingsvariabele, anders de git-remote van de werkkopie
    waar deze code in staat. Niet hardgecodeerd, want deze repo is publiek en
    een fork hoort naar zijn eigen releases te kijken -- iemand die dit draait
    met zijn eigen nodes en zijn eigen builds wil niet stilletjes images van een
    vreemde binnenhalen omdat er een naam in de broncode stond.

    Leeg als geen van beide iets oplevert, en dan is de firmwarepagina uit met
    die reden erbij. Dat is het eerlijke antwoord in een container zonder .git
    en zonder MM_GITHUB_REPO.
    """
    if REPO_OVERRIDE:
        return REPO_OVERRIDE

    here = Path(__file__).resolve()
    for parent in here.parents:
        cfg = parent / ".git" / "config"
        if not cfg.is_file():
            continue
        try:
            parser = configparser.ConfigParser()
            parser.read(cfg, encoding="utf-8")
        except (OSError, configparser.Error):
            return ""
        for section in parser.sections():
            if section.replace('"', "").strip() != 'remote origin':
                continue
            url = parser[section].get("url", "")
            m = re.search(r"[:/]([^/:]+)/([^/]+?)(?:\.git)?/?$", url)
            if m:
                return f"{m.group(1)}/{m.group(2)}"
        return ""
    return ""


# --- GitHub -------------------------------------------------------------------

def _get(url: str, timeout: int, accept: str = "application/vnd.github+json") -> bytes:
    req = urllib.request.Request(url, headers={
        "Accept": accept,
        # GitHub weigert verzoeken zonder User-Agent met een 403 die er als een
        # limiet uitziet. Dat is een uur zoeken waard geweest voor iemand.
        "User-Agent": "MeshManager",
        "X-GitHub-Api-Version": "2022-11-28",
    })
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def _parse_release(raw: dict) -> dict:
    """Eén release uit het GitHub-antwoord, teruggebracht tot wat wij gebruiken.

    De assets worden hier al per bouwomgeving uitgesorteerd, zodat de rest van de
    code nooit meer over bestandsnamen hoeft na te denken. Een asset waarvan de
    naam niet aan het patroon voldoet valt weg in plaats van ergens anders voor
    een verrassing te zorgen -- een release mag best ook een merged-bin of een
    elf meedragen.
    """
    images: dict[str, dict] = {}
    checksums: dict[str, str] = {}
    for asset in raw.get("assets") or []:
        name = asset.get("name") or ""
        if name.endswith(".sha256"):
            checksums[name[:-len(".sha256")]] = asset.get("browser_download_url") or ""
            continue
        m = ASSET_RE.match(name)
        if not m:
            continue
        images[m.group("env")] = {
            "env": m.group("env"),
            "name": name,
            "url": asset.get("browser_download_url") or "",
            "size": int(asset.get("size") or 0),
            "sha_url": "",
        }
    for env, image in images.items():
        image["sha_url"] = checksums.get(image["name"], "")

    tag = raw.get("tag_name") or ""
    return {
        "tag": tag,
        "version": tag[len("fw-"):].lstrip("v") if tag.startswith("fw-") else tag.lstrip("v"),
        "name": raw.get("name") or tag,
        "published": (raw.get("published_at") or "")[:19].replace("T", " "),
        "notes": raw.get("body") or "",
        "prerelease": bool(raw.get("prerelease")),
        "images": images,
        "envs": sorted(images),
    }


def releases(force: bool = False) -> dict:
    """De releaselijst, gecachet, met de laatste goede lijst als vangnet.

    Twee dingen kunnen misgaan en ze verdienen verschillende behandeling. Geen
    netwerk of een limiet is tijdelijk: dan blijft de vorige lijst staan met de
    fout ernaast, want een beheerpagina die leeg wordt omdat GitHub even niet
    wilde is een beheerpagina die je op het verkeerde moment in de steek laat.
    Een lege lijst zonder fout is iets anders -- dan zijn er echt geen releases,
    en dat hoort er ook zo te staan.

    De cache telt op de KLOK en niet op de inhoud, en dat verschil is een bug
    waard geweest. "Vers genoeg" afmeten aan of er items in zitten lijkt
    voorzichtig, maar het betekent dat een repository zonder releases nooit een
    geldige cache heeft -- en dat is precies de toestand waarin dit project
    vandaag verkeert. Elke keer dat iemand de beheerpagina opende zou er dan
    opnieuw bij GitHub aangeklopt worden, tot de zestig verzoeken per uur op
    waren en de pagina ging klagen over een limiet die hij zelf had opgemaakt.
    Nu is een geslaagde ophaal die niets opleverde ook een antwoord, en een fout
    zet de klok net zo goed vooruit -- dat laatste is de wachttijd die voorkomt
    dat een kapot netwerk in een strak ritme opnieuw geprobeerd wordt. De knop
    "Lijst nu verversen" is de uitweg voor wie niet wil wachten.
    """
    slug = repo_slug()
    with _lock:
        fresh = (time.time() - _cache["at"]) < CACHE_MIN * 60
        if not force and fresh and _cache["slug"] == slug:
            return dict(_cache)

    if not slug:
        with _lock:
            _cache.update(at=time.time(), slug=slug, error="repo_unknown")
            return dict(_cache)

    url = f"https://api.github.com/repos/{slug}/releases?per_page=30"
    try:
        raw = json.loads(_get(url, GITHUB_TIMEOUT_S))
        items = [_parse_release(r) for r in raw if not r.get("draft")]
        items = [i for i in items if i["images"]]
        with _lock:
            _cache.update(at=time.time(), items=items, error="", slug=slug)
            return dict(_cache)
    except urllib.error.HTTPError as exc:
        error = "rate_limited" if exc.code in (403, 429) else f"http_{exc.code}"
    except (urllib.error.URLError, OSError, ValueError, TimeoutError):
        error = "offline"

    with _lock:
        # Wel de tijd bijwerken: anders probeert elke paginaverversing het
        # opnieuw en is de limiet die je net raakte er over een minuut nog.
        _cache.update(at=time.time(), error=error, slug=slug)
        return dict(_cache)


def release_by_tag(tag: str) -> dict | None:
    for item in releases().get("items") or []:
        if item["tag"] == tag:
            return item
    return None


# --- de node ------------------------------------------------------------------

def _auth_header() -> dict:
    """Basic, preventief meegestuurd.

    De firmware aanvaardt zowel Basic als Digest (``req->authenticate()`` kijkt
    naar allebei). Preventief Basic bespaart een extra ronde -- en die ronde is
    hier duurder dan hij lijkt: bij een 401 op een POST met 1,3 MB body heeft de
    client die body al verstuurd.
    """
    token = base64.b64encode(f"{NODE_USER}:{NODE_PASS}".encode()).decode()
    return {"Authorization": f"Basic {token}"}


def probe(host: str, timeout: int = PROBE_TIMEOUT_S) -> dict:
    """Vraag een node wat hij draait en waarvoor hij gebouwd is.

    Dit is de enige plek waar de bouwomgeving vandaan komt, en dat is met opzet
    hetzelfde kanaal als waarlangs geschreven wordt: wat hier gelezen wordt komt
    van de node die zo meteen het image krijgt, op hetzelfde moment. Een
    envnaam uit de database zou een envnaam kunnen zijn van voor de laatste keer
    dat iemand het bordje omwisselde.
    """
    out = {"ok": False, "error": "", "ver": "", "env": "", "board": "",
           "run": "", "other": {}, "old": False}
    if not host:
        out["error"] = "geen adres"
        return out
    try:
        with open_node(host, "/api/fw", timeout=timeout) as resp:
            data = json.loads(resp.read())
    except TargetRefused as exc:
        # Vóór de andere gevallen, want dit is geen storing: er is niets
        # geprobeerd. "Niet bereikbaar" zou iemand naar het netwerk laten kijken
        # voor een probleem dat op deze server zit.
        out["error"] = f"adres geweigerd: {exc}"
        return out
    except urllib.error.HTTPError as exc:
        if exc.code == 401:
            out["error"] = "aanmelden geweigerd door de node"
        elif exc.code == 404:
            # 404 op /api/fw betekent iets heel bepaalds: de node leeft, praat
            # HTTP, maar draait firmware van voor 1.12.0. Dat is geen fout maar
            # een versie, en de pagina hoort dat anders te zeggen dan "onbereikbaar".
            out["error"] = "node draait firmware zonder /api/fw (ouder dan 1.12.0)"
            out["old"] = True
        else:
            out["error"] = f"node antwoordde HTTP {exc.code}"
        return out
    except (urllib.error.URLError, OSError, ValueError, TimeoutError) as exc:
        out["error"] = f"niet bereikbaar ({type(exc).__name__})"
        return out

    out.update(ok=True, ver=str(data.get("ver") or ""), env=str(data.get("env") or ""),
               board=str(data.get("board") or ""), run=str(data.get("run") or ""),
               other=data.get("other") or {})
    return out


def _url(host: str, path: str) -> str:
    """Een hostveld van de beheerpagina omzetten naar een URL.

    Toegeeflijk over de vorm (met of zonder schema, met of zonder poort), streng
    over het schema: alleen http en https. Zonder die controle is 'file:///etc'
    in een hostveld een manier om deze server zijn eigen bestanden te laten
    lezen, en dit veld staat achter een login maar wordt wel door een mens
    getypt.

    Dit is de VORMcontrole en niet de DOELcontrole. Of de server dit adres mag
    benaderen, en of de vlootinloggegevens mee mogen, staat in
    :func:`check_target` -- en die vraag hangt niet aan de vorm van de tekst maar
    aan wie het adres heeft vastgelegd.
    """
    host = (host or "").strip()
    if not host:
        raise ValueError("leeg adres")
    if "://" not in host:
        host = "http://" + host
    parts = urllib.parse.urlsplit(host)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise ValueError("adres moet http:// of https:// zijn")
    return urllib.parse.urlunsplit((parts.scheme, parts.netloc, path, "", ""))


# --- welk doel mag de server benaderen ---------------------------------------
#
# HET GAT DAT HIER DICHTGAAT, en het zijn er twee tegelijk.
#
# ``ota_host`` en ``sensor_host`` worden door een mens ingetypt, en tot nu toe
# werd alleen het SCHEMA gecontroleerd. Er staat een recht op dat veld
# (``node.beheeradres``) en dat recht is DELEGEERBAAR: een beheerder kan het per
# node aan iemand anders geven. Zet die iemand er het adres van zijn eigen server
# in, dan gebeurt dit:
#
#   1. deze server maakt een verbinding naar een doel dat de gebruiker koos --
#      dat is SSRF, en op een machine die op een LAN staat is dat al erg genoeg;
#   2. en hij stuurt ``MM_FW_NODE_USER``/``MM_FW_NODE_PASS`` mee in de
#      Authorization-header. Dat zijn de inloggegevens waarmee firmware en
#      instellingen naar ELKE node geschreven worden. Eén ingevuld tekstveld en
#      de vloot is weg.
#
# DE SPANNING DIE DIT MOEILIJK MAAKT. De gebruikelijke reparatie is "weiger
# private adressen": 127/8, 10/8, 172.16/12, 192.168/16, 169.254/16, ::1,
# fc00::/7. Maar de nodes van dit project STAAN op 192.168.x -- dat IS wat een
# beheeradres is. Die lijst zou de functie dus afschaffen in plaats van
# beveiligen.
#
# DE OPLOSSING: het onderscheid zit niet in het ADRES maar in WIE HET VASTLEGDE.
# Een LAN-adres opgeven is inherent een beheerdersdaad -- de server moet het
# kunnen bereiken, en wie weet welk adres dat is, kent het netwerk waar de server
# op staat. Dus:
#
#   * een adres INVULLEN mag alleen een serverbeheerder (afgedwongen in
#     routes_admin.save_ota en save_sensor_host). WISSEN mag ieder die het recht
#     heeft: een adres weghalen sluit een weg en kan er nooit een openen.
#   * bij het VERBINDEN wordt getoetst of dit adres werkelijk zo vastgelegd is
#     (``repeaters.host_admin``). Dat is de toegestane-lijst, en de databank is de
#     enige plek waar ze kan staan -- want ze moet ook kloppen voor een rij die er
#     al stond, en na een herstart.
#
# Waarom de toets bij het VERBINDEN staat en niet alleen bij het invullen: dat is
# hetzelfde argument als bij ``nodeconfig.NO_REMOTE``. Een controle die alleen in
# het formulier zit, is met een aangepast verzoek te omzeilen; een controle op de
# plek waar de handeling werkelijk gebeurt, niet. En net als daar staat ze NAAST
# de eerste en niet in plaats ervan -- twee sloten voor één regel, waarvan de
# tweede degene is die telt.
#
# De resolutie hoort erbij: de toets kijkt naar het OPGELOSTE adres en niet naar
# de tekst, want een naam die naar 127.0.0.1 wijst is geen publiek adres. Dat is
# hier geen poort meer -- de toegestane-lijst is de poort -- maar het bepaalt wél
# wat de melding zegt, en het weigert de bereiken die nooit een node kunnen zijn.


def _is_private(ip) -> bool:
    """Of dit een adres uit een eigen netwerk is.

    Loopback, link-local, unique-local en de RFC1918-bereiken. Precies de
    adressen waar de nodes van dit project op staan -- en precies de adressen
    waarheen een SSRF-poging het meest waard is. Vandaar dat dit een vaststelling
    is en geen weigering.
    """
    return bool(ip.is_private or ip.is_loopback or ip.is_link_local)


def _never_a_node(ip) -> str:
    """Bereiken die nooit een beheeradres kunnen zijn, wie ze ook invult.

    Geen toegestane-lijst omheen: een verbinding naar 0.0.0.0 of naar een
    multicastadres is geen node maar een vergissing of een poging, en in beide
    gevallen is weigeren het juiste antwoord.

    ``is_reserved`` staat ACHTER de private-toets en niet ervoor, en dat is geen
    volgorde-detail. Python rekent ``::1`` tot de gereserveerde bereiken, dus
    andersom zou IPv6-loopback hier als "nooit een node" eindigen terwijl het
    gewoon een privaat adres is -- en dan zou de melding iets anders zeggen dan
    er aan de hand is. Wat hier overblijft zijn de bereiken die noch privaat noch
    routeerbaar zijn.
    """
    if ip.is_unspecified:
        return "0.0.0.0 is geen adres van een node"
    if ip.is_multicast:
        return "een multicastadres is geen adres van een node"
    if not _is_private(ip) and getattr(ip, "is_reserved", False):
        return "dat adres valt in een gereserveerd bereik"
    return ""


def resolve_target(host: str) -> dict:
    """Het adres achter een hostveld opzoeken, met de aard ervan erbij.

    ``{"ok", "error", "host", "ip", "private"}``. ``private`` is waar voor
    loopback, link-local, unique-local en de RFC1918-bereiken -- precies de
    adressen waar de nodes van dit project op staan, en precies de adressen
    waarheen een SSRF-poging het meest waard is. Het is dus geen weigering maar
    een vaststelling; wie beslist is ``check_target``.

    Opzoeken en niet raden: een naam die naar 127.0.0.1 wijst is loopback, hoe
    publiek hij ook klinkt.
    """
    import ipaddress
    import socket

    out = {"ok": False, "error": "", "host": "", "ip": "", "private": False}
    try:
        url = _url(host, "/")
    except ValueError as exc:
        out["error"] = str(exc)
        return out
    naam = urllib.parse.urlsplit(url).hostname or ""
    out["host"] = naam
    try:
        # Het EERSTE antwoord, want dat is het adres waarheen de verbinding gaat.
        # Alle antwoorden toetsen zou strenger lijken en iets anders meten.
        info = socket.getaddrinfo(naam, None)
    except OSError as exc:
        out["error"] = f"adres niet op te zoeken ({type(exc).__name__})"
        return out
    if not info:
        out["error"] = "adres niet op te zoeken"
        return out
    try:
        ip = ipaddress.ip_address(info[0][4][0])
    except ValueError:
        out["error"] = "opgezocht adres is geen IP-adres"
        return out

    out["ip"] = str(ip)
    weigering = _never_a_node(ip)
    if weigering:
        out["error"] = weigering
        return out
    out["private"] = _is_private(ip)
    out["ok"] = True
    return out


def trusted_hosts() -> set:
    """De adressen die een serverbeheerder heeft vastgelegd.

    De toegestane-lijst. Ze staat in de databank omdat dat de enige plek is waar
    ze een herstart overleeft en waar ook een rij in past die er al stond.
    Letterlijk vergeleken en niet genormaliseerd: wat er in het veld staat is wat
    er benaderd wordt, en twee vormen van hetzelfde adres zouden betekenen dat de
    lijst iets anders toestaat dan wat er gebeurt.
    """
    uit = set()
    for kolom in ("ota_host", "sensor_host"):
        for r in db.q(f"SELECT {kolom} AS h FROM repeaters "
                      f"WHERE host_admin=1 AND {kolom} IS NOT NULL "
                      f"AND TRIM({kolom}) <> ''"):
            uit.add(str(r["h"]).strip())
    return uit


def check_target(host: str) -> dict:
    """Mag de server dit adres benaderen, mét de vlootinloggegevens?

    ``{"ok", "error", "ip", "private"}``. Dit is de enige plek waar dat besloten
    wordt, en elke uitgaande verbinding naar een node loopt erlangs -- zie
    ``open_node``, waar ``nodeconfig._open``, ``probe`` en ``push`` op uitkomen.

    De regel in één zin: een adres dat een serverbeheerder niet heeft vastgelegd,
    krijgt geen verbinding en dus ook geen wachtwoord. Niet "geen privaat adres"
    -- dat zou de nodes van dit project uitsluiten -- en niet "wel als het in de
    databank staat", want dat veld is met een gedelegeerd recht te vullen. Wat
    telt is dat een serverbeheerder het gezet heeft.

    Waarom de weigering ook voor een PUBLIEK adres geldt. Het lek is niet dat de
    server een LAN aanraakt; het lek is dat er een wachtwoord in de header staat.
    Naar de server van een aanvaller op een publiek adres is dat precies zo erg,
    en "alleen private adressen toetsen" zou het gat openlaten aan de kant waar
    het het makkelijkst te misbruiken is.
    """
    adres = (host or "").strip()
    out = {"ok": False, "error": "", "ip": "", "private": False}
    if not adres:
        out["error"] = "leeg adres"
        return out
    if adres not in trusted_hosts():
        out["error"] = (
            "dit adres is niet door een serverbeheerder vastgelegd. De server "
            "verbindt er daarom niet naartoe en stuurt er geen inloggegevens "
            "naartoe: MM_FW_NODE_USER/MM_FW_NODE_PASS openen elke node, en een "
            "adres dat langs een gedelegeerd recht ingevuld is, is geen adres "
            "waar dat wachtwoord heen mag")
        return out
    doel = resolve_target(adres)
    if not doel["ok"]:
        out["error"] = doel["error"]
        return out
    out.update(ok=True, ip=doel["ip"], private=doel["private"])
    return out


class TargetRefused(ValueError):
    """Het doel mag niet benaderd worden. Zie ``check_target``.

    Een eigen soort en geen kale ValueError, zodat een aanroeper het verschil kan
    maken tussen "dat adres is onbruikbaar van vorm" en "dat adres mag niet" --
    en die tweede hoort op het scherm een andere zin te krijgen, want er valt
    niets aan te repareren door de tekst te verbeteren.
    """


def open_node(host: str, path: str, data: bytes | None = None,
              timeout: int = PROBE_TIMEOUT_S, content_type: str | None = None):
    """Eén verbinding naar een node, met de doelcontrole ervoor.

    ELKE uitgaande verbinding naar een node hoort hier langs te komen. Niet uit
    netheid: dit is de plek waar de vlootinloggegevens aan het verzoek gehangen
    worden, en de controle hoort te staan waar het geheim de deur uit gaat. Een
    tweede plek die zelf een socket opent, is een tweede plek waar de controle
    kan ontbreken -- dezelfde regel als "één schrijfweg" in nodeconfig.py.
    """
    toets = check_target(host)
    if not toets["ok"]:
        raise TargetRefused(toets["error"])
    url = _url(host, path)
    headers = dict(_auth_header())
    if content_type:
        headers["Content-Type"] = content_type
    req = urllib.request.Request(url, data=data, headers=headers,
                                 method="POST" if data is not None else "GET")
    return urllib.request.urlopen(req, timeout=timeout)


def push(host: str, blob: bytes, digest: str, version: str,
         timeout: int = PUSH_TIMEOUT_S) -> dict:
    """Het image naar de node schrijven en zijn antwoord teruggeven.

    Eén smalle functie voor de hele netwerkgrens, zodat een test hem in zijn
    geheel kan vervangen -- dezelfde afspraak als bij ``publish_command``, en om
    dezelfde reden: er is verder nergens in deze module een socket.
    """
    query = urllib.parse.urlencode({"sha256": digest, "size": len(blob), "ver": version})
    try:
        with open_node(host, "/api/fw?" + query, data=blob, timeout=timeout,
                       content_type="application/octet-stream") as resp:
            return json.loads(resp.read())
    except TargetRefused as exc:
        # Eigen stap, want dit is de enige fout in deze functie waarbij er
        # gegarandeerd niets naar de node is gegaan -- en bij een image van ruim
        # een megabyte is dat het verschil tussen "opnieuw proberen" en "eerst
        # gaan kijken wat er half op staat".
        return {"ok": 0, "step": "adres", "msg": f"adres geweigerd: {exc}"}
    except urllib.error.HTTPError as exc:
        # De node antwoordt met JSON, óók bij een fout, en juist dan staat er in
        # welke stap faalde. Die tekst is het enige wat dit hele ontwerp de
        # oude weg voor heeft; hem inslikken en "HTTP 400" tonen zou de fout
        # herhalen die we aan het repareren zijn.
        try:
            return json.loads(exc.read())
        except (ValueError, OSError):
            return {"ok": 0, "step": f"http_{exc.code}", "msg": f"node antwoordde HTTP {exc.code}"}
    except (urllib.error.URLError, OSError, ValueError, TimeoutError) as exc:
        return {"ok": 0, "step": "verbinding", "msg": f"geen antwoord van de node ({type(exc).__name__})"}


def download(image: dict) -> tuple[bytes, str]:
    """Het image plus de gepubliceerde checksum ophalen en controleren.

    Faalt hier iets, dan is er nog niets gebeurd: dit is de reden dat de server
    het image ophaalt en niet de node. Een node die zelf zou downloaden zou een
    halve download pas ontdekken nadat hij zijn partitie al aan het schrijven is,
    en zou daarvoor bovendien bij GitHub moeten kunnen -- op een dak, over een
    verbinding die er is om LoRa-statistieken te versturen.
    """
    if not image.get("sha_url"):
        raise ValueError(f"geen .sha256 naast {image.get('name')}")
    if not (MIN_IMAGE <= int(image.get("size") or 0) <= MAX_IMAGE):
        raise ValueError(f"{image.get('name')} is {image.get('size')} bytes; dat kan geen image zijn")

    want = _get(image["sha_url"], DOWNLOAD_TIMEOUT_S, accept="*/*").decode("utf-8", "replace")
    want = want.strip().split()[0].lower() if want.strip() else ""
    if len(want) != 64 or any(c not in "0123456789abcdef" for c in want):
        raise ValueError("gepubliceerde checksum is geen sha256")

    blob = _get(image["url"], DOWNLOAD_TIMEOUT_S, accept="application/octet-stream")
    have = hashlib.sha256(blob).hexdigest()
    if have != want:
        raise ValueError(f"download klopt niet: verwacht {want[:12]}…, kreeg {have[:12]}…")
    return blob, have


# --- kan deze node een image krijgen -----------------------------------------

def ota_route(rep, host: str | None = None) -> dict:
    """Kan er een firmware-image naar deze repeater, en zo nee waarom niet.

    Bewust NIET hetzelfde als het beheerniveau uit ``commanding``. Een node kan
    ``full_managed`` zijn -- hij publiceert zelf, zijn cmd-topic werkt, zijn klok
    is te zetten -- en toch geen image kunnen ontvangen, omdat commando's van
    twintig bytes en images van 1,3 MB niet over hetzelfde pad hoeven te reizen.
    Die twee door elkaar halen levert precies één soort fout op, en het is de
    dure: een knop die belooft wat hij niet kan.

    Geeft ``can`` plus een ``blocker`` die de pagina in een zin omzet, in de
    stijl van ``commanding.route_for``. Doet geen netwerk -- wat hier bekend is,
    is wat de database weet; de envnaam wordt vlak voor het schrijven bij de
    node zelf opgehaald.
    """
    host = (host or _field(rep, "ota_host") or "").strip()
    out = {
        "can": False,
        "blocker": "",
        "host": host,
        "env": _field(rep, "pio_env") or "",
        "installed": _field(rep, "fw_meshmanager") or "",
        "critical": bool(_field(rep, "is_critical")),
        "relayed": bool(_field(rep, "source_prefix"))
                   and str(_field(rep, "source_prefix") or "")[:8].lower()
                       != str(_field(rep, "pubkey_prefix") or "")[:8].lower(),
    }

    if not NODE_USER:
        out["blocker"] = "no_credentials"
    elif not host:
        # Voor een doorgestuurde node is dit een blijvende toestand en geen
        # vergeten instelling; de pagina hoort dat verschil te maken.
        out["blocker"] = "relayed_only" if out["relayed"] else "no_host"
    elif not out["installed"]:
        out["blocker"] = "no_fw"
    else:
        out["can"] = True
    return out


# --- de upgrade zelf ----------------------------------------------------------

JOBS_KEY = "fw_jobs"
JOBS_MAX = 30

# Toestanden die een opdracht kan hebben. 'niet_teruggekomen' is de reden dat
# deze lijst bestaat: dat is geen mislukking (het schrijven lukte) en geen
# succes (de node is weg), en het moet zichtbaar blijven staan tot iemand ernaar
# gekeken heeft. Een node die na een upgrade stilletjes uit beeld verdwijnt is
# precies de gebeurtenis waar dit hele ontwerp voor bestaat.
BUSY_STATES = ("voorbereiden", "downloaden", "schrijven", "wachten")


def _jobs() -> dict:
    try:
        return json.loads(db.get_setting(JOBS_KEY, "{}")) or {}
    except ValueError:
        return {}


def _save_job(rep_id: int, job: dict) -> None:
    jobs = _jobs()
    jobs[str(rep_id)] = job
    if len(jobs) > JOBS_MAX:
        jobs = dict(sorted(jobs.items(), key=lambda kv: kv[1].get("started", ""))[-JOBS_MAX:])
    db.set_setting(JOBS_KEY, json.dumps(jobs))


def job(rep_id: int) -> dict | None:
    return _jobs().get(str(rep_id))


def jobs() -> dict:
    return _jobs()


def clear_job(rep_id: int) -> None:
    """Een afgeronde opdracht wegklikken.

    Alleen als hij niet meer loopt. Een 'niet teruggekomen' wegklikken mag wel,
    en dat is met opzet de enige manier waarop die melding verdwijnt: iemand
    moet hem gezien hebben.
    """
    current = job(rep_id)
    if current and current.get("state") in BUSY_STATES:
        return
    jobs_now = _jobs()
    jobs_now.pop(str(rep_id), None)
    db.set_setting(JOBS_KEY, json.dumps(jobs_now))


def start(rep, tag: str, expect_env: str = "") -> dict:
    """Een upgrade in gang zetten. Geeft meteen terug, werk gebeurt in een draad.

    Alles wat mis kan gaan zónder het netwerk aan te raken gebeurt hier, vóór de
    303 terug naar de pagina, zodat een typefout een foutmelding oplevert en geen
    opdracht die twee minuten later ergens in een lijst faalt.
    """
    rep_id = int(_field(rep, "id") or 0)
    name = str(_field(rep, "name") or _field(rep, "pubkey_prefix") or "?")
    route = ota_route(rep)

    current = job(rep_id)
    if current and current.get("state") in BUSY_STATES:
        return {"ok": False, "error": "er loopt al een upgrade voor deze node"}
    if not route["can"]:
        return {"ok": False, "error": f"deze node kan geen image ontvangen ({route['blocker']})"}

    release = release_by_tag(tag)
    if not release:
        return {"ok": False, "error": f"onbekende release {tag!r}"}

    started = db.utcnow()
    job_row = {
        "rep": rep_id, "name": name, "tag": tag, "version": release["version"],
        "from": route["installed"], "env": expect_env, "state": "voorbereiden",
        "msg": "", "step": "", "started": started, "ended": "", "bytes": 0,
        "downgrade": _is_downgrade(route["installed"], release["version"]),
    }
    _save_job(rep_id, job_row)

    thread = threading.Thread(target=_run, args=(rep_id, route["host"], tag, expect_env),
                              name=f"fw-{rep_id}", daemon=True)
    thread.start()
    return {"ok": True, "job": job_row}


def _is_downgrade(installed: str, target: str) -> bool:
    """Gaat deze stap omlaag? Onbekend telt niet als omlaag.

    Een downgrade is een geldige handeling -- als een nieuwe versie zich misdraagt
    op een node die je alleen op afstand bereikt, wil je terug -- maar hij verdient
    een waarschuwing en niet een stille toestemming. Wat hij níét terugdraait staat
    in docs/firmware-upgrade.md: instellingen overleven, want die staan op de
    datapartitie, maar een instelling die pas na de oudere versie bestond wordt
    door die oudere versie genegeerd en bij de eerstvolgende opslag uit het bestand
    geschreven.
    """
    def parts(text):
        m = re.match(r"^\s*v?(\d+)\.(\d+)\.(\d+)", str(text or ""))
        return tuple(int(g) for g in m.groups()) if m else None

    a, b = parts(installed), parts(target)
    return bool(a and b and b < a)


def _update(rep_id: int, **fields) -> None:
    current = job(rep_id) or {}
    current.update(fields)
    _save_job(rep_id, current)


def _run(rep_id: int, host: str, tag: str, expect_env: str) -> None:
    """De hele weg, in een achtergronddraad. Vangt alles: een draad die stukloopt
    laat een opdracht eeuwig op 'bezig' staan, en dat is de ene toestand waar
    niemand iets aan heeft."""
    try:
        _run_inner(rep_id, host, tag, expect_env)
    except Exception as exc:                                   # noqa: BLE001
        _update(rep_id, state="mislukt", step="onverwacht",
                msg=f"{type(exc).__name__}: {exc}", ended=db.utcnow())


def _run_inner(rep_id: int, host: str, tag: str, expect_env: str) -> None:
    release = release_by_tag(tag)
    if not release:
        _update(rep_id, state="mislukt", step="release", msg=f"release {tag} verdween uit de lijst",
                ended=db.utcnow())
        return

    # 1. Vraag de node zelf waarvoor hij gebouwd is. Nu pas, want dit is het
    #    moment waarop het antwoord ook echt geldt.
    info = probe(host)
    if not info["ok"]:
        _update(rep_id, state="mislukt", step="node", msg=info["error"], ended=db.utcnow())
        return
    env = info["env"]
    if not env:
        _update(rep_id, state="mislukt", step="env", ended=db.utcnow(),
                msg="de node meldt geen bouwomgeving (image ouder dan 1.12.0); "
                    "welk image hierbij hoort is niet vast te stellen")
        return
    if expect_env and env != expect_env:
        _update(rep_id, state="mislukt", step="env", ended=db.utcnow(),
                msg=f"de node meldt {env!r}, de pagina ging uit van {expect_env!r}")
        return

    image = image_for(release, env)
    if not image:
        _update(rep_id, state="mislukt", step="env", ended=db.utcnow(),
                msg=f"release {tag} heeft geen image voor {env!r} "
                    f"(wel voor: {', '.join(release['envs']) or 'niets'})")
        return

    db.record_pio_env(rep_id, env)

    # 2. Downloaden en controleren voordat er iets de lucht in gaat.
    _update(rep_id, state="downloaden", env=env, msg=image["name"])
    try:
        blob, digest = download(image)
    except (ValueError, urllib.error.URLError, OSError, TimeoutError) as exc:
        _update(rep_id, state="mislukt", step="download", msg=str(exc), ended=db.utcnow())
        return

    # 3. Schrijven. De node controleert dezelfde digest nog een keer.
    _update(rep_id, state="schrijven", bytes=len(blob))
    answer = push(host, blob, digest, release["version"])
    if not answer.get("ok"):
        _update(rep_id, state="mislukt", step=str(answer.get("step") or "node"),
                msg=str(answer.get("msg") or "de node weigerde het image"), ended=db.utcnow())
        return

    # 4. Wachten tot hij terugkomt. Het schrijven is gelukt; of de node ook
    #    terugkeert is een aparte vraag en verdient een apart antwoord.
    _update(rep_id, state="wachten", msg="node herstart")
    deadline = time.time() + RETURN_WAIT_S
    while time.time() < deadline:
        time.sleep(RETURN_POLL_S)
        back = probe(host)
        if not back["ok"]:
            continue
        if back["ver"] == release["version"]:
            db.record_firmware(rep_id, fw_module=back["ver"])
            _nudge(rep_id)
            _update(rep_id, state="gelukt", msg=f"draait {back['ver']}", ended=db.utcnow())
            return
        # Terug op de oude versie is een eigen uitkomst: het schrijven meldde
        # succes en toch draait er iets anders. Dat is precies de fout waar de
        # oude upgradeweg over zweeg, dus hier komt hij met naam en toenaam.
        _update(rep_id, state="mislukt", step="terug_op_oud", ended=db.utcnow(),
                msg=f"node is terug maar draait {back['ver']}, niet {release['version']}")
        return

    _update(rep_id, state="niet_teruggekomen", ended=db.utcnow(),
            msg=f"het image is geschreven en geverifieerd, maar de node antwoordt na "
                f"{RETURN_WAIT_S} s nog niet. Controleer hem; terugvallen kan met "
                f"'wifi fw rollback' over de mesh-CLI.")


def _nudge(rep_id: int) -> None:
    """Vraag de node om nu een statistiekenbericht te publiceren.

    Zonder dit blijft de site de oude versie tonen tot de node uit zichzelf weer
    publiceert -- tot een uur later in zuinige modus. De pagina zou dan een
    geslaagde upgrade tonen naast een versienummer dat hem tegenspreekt, en dat
    is precies het soort halve waarheid dat deze functie moet voorkomen. Faalt
    het, dan is dat geen reden om de upgrade als mislukt te melden: de node
    draait, en de versie komt vanzelf.
    """
    try:
        from . import mqtt_ingest
        rep = db.qone("SELECT * FROM repeaters WHERE id=?", (rep_id,))
        node = str(_field(rep, "pubkey_prefix") or "")
        if node and mqtt_ingest.can_publish():
            mqtt_ingest.publish_command(node, "status")
    except Exception:                                          # noqa: BLE001
        pass


def _field(row, key, default=None):
    """Rijen komen als sqlite3.Row, dicts of None binnen; alle drie zijn geldig.

    Overgenomen uit ``commanding._field`` en om dezelfde reden: de tests geven
    dicts door waar productie een Row geeft, en een module die alleen Rows
    aankan is een module die alleen met een database te testen is.
    """
    if row is None:
        return default
    try:
        value = row[key]
    except (KeyError, IndexError, TypeError):
        try:
            value = row.get(key, default)  # type: ignore[union-attr]
        except AttributeError:
            return default
    return default if value is None else value
