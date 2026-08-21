"""SNMP-discovery op de SERVER, zodat niemand OIDs hoeft over te typen.

Waarom hier en niet op de node
------------------------------
De NODE blijft de poller: hij vraagt straks periodiek één OID op via de bestaande
``POST /monitor/snmp``. Maar een node heeft geen rijke SNMP-stack en de gebruiker
weet de OIDs niet. Dus doet de SERVER eenmalig een gerichte *walk* van een
apparaat, presenteert de monitorbare waarden als een kieslijst, en maakt voor het
gekozene de node-monitors aan (``rooms.add_snmp_monitor``). De node pollt daarna.

Welke SNMP-stack, en waarom
---------------------------
**net-snmp via subprocess** (``snmpget``/``snmpbulkwalk``), niet een Python-
pakket. De afweging:

* net-snmp is de referentie-implementatie: v2c, HC-counters (64-bit), timeticks,
  alles klopt en is jaren beproefd. De uitvoer met ``-OQn`` is één regel per
  varbind (``.numerieke.oid waarde``) en dus triviaal te ontleden.
* Een pure-Python-pakket (pysnmp) zou een dependency én een bewegend doel zijn:
  de API is de laatste jaren meermaals omgegooid (sync -> asyncio, een fork). Dat
  is precies het soort stille breuk dat dit project vermijdt.
* De prijs is één OS-pakket in het image (``snmp``), en dat staat in de
  Dockerfile en in docs/rooms.md. Ontbreekt het, dan zegt ``available()`` dat en
  meldt de pagina het netjes in plaats van te crashen.

De COMMUNITY is een geheim: hij gaat als argument mee naar ``snmpget`` maar wordt
hier NOOIT teruggegeven, onthouden of gelogd. De discovery-uitslag draagt alleen
publieke informatie (OIDs, namen, waarden).

Randvoorwaarde: de SERVER moet het apparaat over UDP/161 kunnen bereiken. Op een
LAN is dat meestal zo. Staat een apparaat alleen achter de node, dan werkt deze
weg niet -- node-side discovery is een latere optie. Dat staat in de docs.

Testbaarheid
------------
Alles gaat langs één subprocess-primitief (``_run``); de tests vervangen dat (of
``get``/``walk``) en er gaat geen echt pakket het net op.
"""
from __future__ import annotations

import shutil
import subprocess

SNMPGET = "snmpget"
SNMPWALK = "snmpbulkwalk"

TIMEOUT_S = 5          # per snmp-aanroep, aan de node-kant van "even geduld"
RETRIES = 1
WALK_TIMEOUT_S = 20    # een bulkwalk mag wat langer

# --- de OID-woordenschat (numeriek, want we draaien zonder MIB's) -------------
SYS = {
    "descr": "1.3.6.1.2.1.1.1.0",
    "objectid": "1.3.6.1.2.1.1.2.0",
    "uptime": "1.3.6.1.2.1.1.3.0",
    "name": "1.3.6.1.2.1.1.5.0",
}
IF_DESCR = "1.3.6.1.2.1.2.2.1.2"
IF_TYPE = "1.3.6.1.2.1.2.2.1.3"
IF_OPER = "1.3.6.1.2.1.2.2.1.8"
IF_NAME = "1.3.6.1.2.1.31.1.1.1.1"
IF_HCIN = "1.3.6.1.2.1.31.1.1.1.6"
IF_HCOUT = "1.3.6.1.2.1.31.1.1.1.10"
IF_HISPEED = "1.3.6.1.2.1.31.1.1.1.15"
IF_ALIAS = "1.3.6.1.2.1.31.1.1.1.18"

UPS_MODEL = "1.3.6.1.2.1.33.1.1.2.0"
UPS_BATT = "1.3.6.1.2.1.33.1.2.1"          # scalar (.0)
UPS_MIN = "1.3.6.1.2.1.33.1.2.3"           # scalar (.0)
UPS_CHARGE = "1.3.6.1.2.1.33.1.2.4"        # scalar (.0)
UPS_LOAD = "1.3.6.1.2.1.33.1.4.4.1.5"      # per lijn
UPS_VIN = "1.3.6.1.2.1.33.1.3.3.1.3"       # per lijn
UPS_VOUT = "1.3.6.1.2.1.33.1.4.4.1.2"      # per lijn

OPER_STATUS = {"1": "up", "2": "down", "3": "testing", "4": "unknown",
               "5": "dormant", "6": "notPresent", "7": "lowerLayerDown"}
BATT_STATUS = {"1": "onbekend", "2": "normaal", "3": "laag", "4": "leeg"}


def available() -> bool:
    """Of de net-snmp-programma's op het pad staan."""
    return bool(shutil.which(SNMPGET) and shutil.which(SNMPWALK))


# --- de subprocess-grens (de enige plek die het net op gaat) ------------------

def _run(args: list[str], timeout: int) -> tuple[int, str, str]:
    """Eén net-snmp-aanroep. Geeft ``(returncode, stdout, stderr)``.

    De enige plek die een extern programma start; de tests vervangen deze. Een
    ontbrekend programma of een time-out wordt een nette ``(rc, "", reden)`` in
    plaats van een exception.
    """
    try:
        p = subprocess.run(args, capture_output=True, text=True,
                           timeout=timeout, check=False)
        return p.returncode, p.stdout, p.stderr
    except FileNotFoundError:
        return 127, "", "net-snmp is niet geïnstalleerd (snmpget/snmpbulkwalk)"
    except subprocess.TimeoutExpired:
        return 124, "", "time-out: het apparaat antwoordde niet op UDP/161"
    except OSError as exc:  # noqa: BLE001
        return 1, "", f"kon snmp niet starten ({type(exc).__name__})"


def _target(host: str, port: int) -> str:
    h = str(host or "").strip()
    return f"{h}:{int(port)}" if port and int(port) != 161 else h


def _absent(waarde: str) -> bool:
    t = (waarde or "").strip()
    return (not t or t.startswith("No Such") or t.startswith("No more")
            or t == "NULL")


def _parse(tekst: str) -> list[tuple[str, str]]:
    """De ``-OQn``-uitvoer ontleden tot ``[(oid, waarde)]``, absente varbinds eruit."""
    rijen = []
    for regel in (tekst or "").splitlines():
        regel = regel.strip()
        if not regel or " " not in regel:
            continue
        oid, waarde = regel.split(" ", 1)
        oid = oid.lstrip(".").strip()
        waarde = waarde.strip().strip('"')
        if _absent(waarde):
            continue
        rijen.append((oid, waarde))
    return rijen


def get(host: str, community: str, oids: list[str], port: int = 161,
        timeout: int | None = None) -> dict:
    """``snmpget`` van één of meer OIDs. ``{"ok","error","values":{oid: waarde}}``."""
    out = {"ok": False, "error": "", "values": {}}
    if not str(host or "").strip():
        out["error"] = "geen host"
        return out
    args = [SNMPGET, "-OQn", "-v2c", "-c", str(community or ""),
            "-t", str(timeout or TIMEOUT_S), "-r", str(RETRIES),
            _target(host, port), *oids]
    rc, sout, serr = _run(args, (timeout or TIMEOUT_S) * (RETRIES + 1) + 3)
    if rc != 0 and not sout.strip():
        out["error"] = (serr or "snmpget mislukte").strip().splitlines()[0][:200]
        return out
    out["values"] = {oid: waarde for oid, waarde in _parse(sout)}
    out["ok"] = True
    return out


def walk(host: str, community: str, base: str, port: int = 161,
         timeout: int | None = None) -> dict:
    """``snmpbulkwalk`` van een subtree. ``{"ok","error","rows":[(oid, waarde)]}``."""
    out = {"ok": False, "error": "", "rows": []}
    if not str(host or "").strip():
        out["error"] = "geen host"
        return out
    args = [SNMPWALK, "-OQn", "-v2c", "-c", str(community or ""),
            "-t", str(timeout or TIMEOUT_S), "-r", str(RETRIES),
            _target(host, port), base]
    rc, sout, serr = _run(args, (timeout or WALK_TIMEOUT_S) + 5)
    if rc != 0 and not sout.strip():
        out["error"] = (serr or "snmpbulkwalk mislukte").strip().splitlines()[0][:200]
        return out
    out["rows"] = _parse(sout)
    out["ok"] = True
    return out


def _index(oid: str, base: str) -> str:
    """De index-staart van ``oid`` na ``base`` (bv. '3' of '1.2')."""
    base = base.lstrip(".")
    oid = oid.lstrip(".")
    if oid.startswith(base + "."):
        return oid[len(base) + 1:]
    return ""


def _bytes_kort(waarde: str) -> str:
    try:
        n = int(waarde)
    except (TypeError, ValueError):
        return waarde
    for eenheid in ("B", "KB", "MB", "GB", "TB", "PB"):
        if n < 1024:
            return f"{n} {eenheid}"
        n //= 1024
    return f"{n} EB"


# --- de discovery zelf --------------------------------------------------------

def discover(host: str, community: str, port: int = 161,
             timeout: int | None = None) -> dict:
    """Een apparaat aftasten en de monitorbare waarden als kieslijst opleveren.

    ``{"ok","error","system":{...},"interfaces":[...],"ups":{...}|None,"items":[...]}``.
    Elk ``item`` draagt precies wat er nodig is om er via ``rooms.add_snmp_monitor``
    een node-monitor van te maken: ``oid`` + ``interp`` + ``snmparg``, plus een
    voorgestelde ``name`` en een ``preview`` van de nu-gemeten waarde.

    De ``oid``/``snmparg``/``interp`` volgen exact de vorm die de node al accepteert
    (dezelfde basis-OIDs als de preset-bibliotheek): octet-counters -> ``rate``,
    oper-/batterijstatus -> ``status``, gauges -> ``numeric``.
    """
    out = {"ok": False, "error": "", "system": {}, "interfaces": [],
           "ups": None, "items": []}
    if not available():
        out["error"] = ("net-snmp is niet geïnstalleerd op de server "
                        "(snmpget/snmpbulkwalk); zie docs/rooms.md")
        return out
    if not str(host or "").strip():
        out["error"] = "vul een IP of hostnaam in"
        return out

    # 1. Systeem -- ook de eerste bereikbaarheidstoets.
    sysget = get(host, community, list(SYS.values()), port, timeout)
    if not sysget["ok"]:
        out["error"] = sysget["error"] or "het apparaat antwoordde niet"
        return out
    sv = sysget["values"]
    out["system"] = {
        "name": sv.get(SYS["name"], ""),
        "descr": sv.get(SYS["descr"], ""),
        "uptime": sv.get(SYS["uptime"], ""),
        "objectid": sv.get(SYS["objectid"], ""),
    }
    if not sv:
        out["error"] = ("verbonden, maar geen SNMP-antwoord -- klopt de community "
                        "en staat SNMP v2c aan op het apparaat?")
        return out
    kort = (out["system"]["name"] or str(host)).split(".")[0].split()[0][:16]

    items = []

    # 2. Interfaces.
    kolommen = {
        "descr": walk(host, community, IF_DESCR, port, timeout),
        "name": walk(host, community, IF_NAME, port, timeout),
        "alias": walk(host, community, IF_ALIAS, port, timeout),
        "oper": walk(host, community, IF_OPER, port, timeout),
        "hispeed": walk(host, community, IF_HISPEED, port, timeout),
        "hcin": walk(host, community, IF_HCIN, port, timeout),
        "hcout": walk(host, community, IF_HCOUT, port, timeout),
    }
    basis = {"descr": IF_DESCR, "name": IF_NAME, "alias": IF_ALIAS,
             "oper": IF_OPER, "hispeed": IF_HISPEED, "hcin": IF_HCIN,
             "hcout": IF_HCOUT}
    perif: dict[str, dict] = {}
    for sleutel, res in kolommen.items():
        for oid, waarde in res.get("rows", []):
            idx = _index(oid, basis[sleutel])
            if idx:
                perif.setdefault(idx, {})[sleutel] = waarde

    for idx in sorted(perif, key=lambda i: (len(i), i)):
        rij = perif[idx]
        naam = rij.get("name") or rij.get("descr") or f"if{idx}"
        alias = rij.get("alias") or ""
        speed = rij.get("hispeed")
        iface = {"idx": idx, "name": naam, "alias": alias,
                 "oper": OPER_STATUS.get(rij.get("oper", ""), rij.get("oper", "")),
                 "speed_mbps": speed, "items": []}
        if "hcin" in rij:
            items.append({
                "key": f"if_in_{idx}", "group": "interface",
                "label": f"{naam} — inkomend verkeer (bandbreedte)",
                "name": f"{kort} {naam} in"[:40],
                "oid": IF_HCIN, "snmparg": idx, "interp": "rate",
                "preview": _bytes_kort(rij["hcin"]) + " (teller)"})
            iface["items"].append(f"if_in_{idx}")
        if "hcout" in rij:
            items.append({
                "key": f"if_out_{idx}", "group": "interface",
                "label": f"{naam} — uitgaand verkeer (bandbreedte)",
                "name": f"{kort} {naam} uit"[:40],
                "oid": IF_HCOUT, "snmparg": idx, "interp": "rate",
                "preview": _bytes_kort(rij["hcout"]) + " (teller)"})
            iface["items"].append(f"if_out_{idx}")
        if "oper" in rij:
            items.append({
                "key": f"if_oper_{idx}", "group": "interface",
                "label": f"{naam} — operationele status (up/down)",
                "name": f"{kort} {naam} status"[:40],
                "oid": IF_OPER, "snmparg": idx, "interp": "status",
                "preview": iface["oper"]})
            iface["items"].append(f"if_oper_{idx}")
        out["interfaces"].append(iface)

    # 3. UPS-MIB, alleen als het apparaat erop antwoordt.
    upsget = get(host, community,
                 [UPS_MODEL, UPS_BATT + ".0", UPS_MIN + ".0", UPS_CHARGE + ".0"],
                 port, timeout)
    uv = upsget["values"] if upsget["ok"] else {}
    heeft_ups = any(k in uv for k in (UPS_BATT + ".0", UPS_MIN + ".0",
                                      UPS_CHARGE + ".0", UPS_MODEL))
    if heeft_ups:
        ups = {"model": uv.get(UPS_MODEL, ""), "values": {}}
        if UPS_BATT + ".0" in uv:
            rauw = uv[UPS_BATT + ".0"]
            items.append({
                "key": "ups_batt", "group": "ups",
                "label": "UPS — batterijstatus (normaal/laag/leeg)",
                "name": f"{kort} UPS batt"[:40],
                "oid": UPS_BATT, "snmparg": "", "interp": "status",
                "preview": BATT_STATUS.get(rauw, rauw)})
            ups["values"]["batterijstatus"] = BATT_STATUS.get(rauw, rauw)
        if UPS_MIN + ".0" in uv:
            items.append({
                "key": "ups_min", "group": "ups",
                "label": "UPS — resterende minuten",
                "name": f"{kort} UPS min"[:40],
                "oid": UPS_MIN, "snmparg": "", "interp": "numeric",
                "preview": uv[UPS_MIN + ".0"] + " min"})
            ups["values"]["resterende minuten"] = uv[UPS_MIN + ".0"]
        if UPS_CHARGE + ".0" in uv:
            items.append({
                "key": "ups_charge", "group": "ups",
                "label": "UPS — batterijlading %",
                "name": f"{kort} UPS batt %"[:40],
                "oid": UPS_CHARGE, "snmparg": "", "interp": "numeric",
                "preview": uv[UPS_CHARGE + ".0"] + " %"})
            ups["values"]["lading %"] = uv[UPS_CHARGE + ".0"]
        # Per-lijn: belasting en spanningen.
        for basis_oid, sleutel, etiket, eenheid, itp in (
                (UPS_LOAD, "load", "belasting %", " %", "numeric"),
                (UPS_VIN, "vin", "ingangsspanning", " V", "numeric"),
                (UPS_VOUT, "vout", "uitgangsspanning", " V", "numeric")):
            res = walk(host, community, basis_oid, port, timeout)
            for oid, waarde in res.get("rows", []):
                lijn = _index(oid, basis_oid)
                if not lijn:
                    continue
                items.append({
                    "key": f"ups_{sleutel}_{lijn}", "group": "ups",
                    "label": f"UPS — {etiket} (lijn {lijn})",
                    "name": f"{kort} UPS {sleutel} {lijn}"[:40],
                    "oid": basis_oid, "snmparg": lijn, "interp": itp,
                    "preview": waarde + eenheid})
        out["ups"] = ups

    out["items"] = items
    out["ok"] = True
    return out
