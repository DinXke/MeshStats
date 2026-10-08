"""MeshTrack-server: FastAPI-app.

Start:  MESHTRACK_CONFIG=/etc/meshtrack/config.yaml python -m meshtrack.main

Toegang: elke aanvraag krijgt een "principal" (ingelogde gebruiker of
deellink, zie rbac.py). Elke endpoint vraagt het recht dat hij nodig heeft en
filtert trackers op wat die principal mag zien. Ook de live-updates over de
WebSocket worden per verbinding gefilterd.
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import re
import secrets
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Optional

from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import auth, config, geofence, ingest, keys, nodes, rbac
from . import settings as setmod
from .alerts import EVENTS, EVENT_TEXT, AlertManager
from .db import DB
from .mesh_client import MeshLink, send_text
from .protocol import HISTORY_STATES, ProtocolError, parse as parse_t1
from .rbac import PERMS, Principal
from .roadgraph import Router
from .sim import SimManager, new_pubkey

log = logging.getLogger("meshtrack")
STATIC = Path(__file__).resolve().parent.parent / "static"
HEX64 = re.compile(r"^[0-9a-fA-F]{64}$")
COLOR = re.compile(r"^#[0-9a-fA-F]{6}$")
USERNAME = re.compile(r"^[A-Za-z0-9._-]{2,32}$")
SHARE_COOKIE = "mt_share"
VERSION = "1.2.0"


# ---- live-updates -----------------------------------------------------------

class Hub:
    """WebSocket-clients, elk met hun eigen principal: een bericht over een
    tracker gaat alleen naar wie die tracker mag zien."""

    def __init__(self) -> None:
        # Per client de cookies; de principal wordt bij elk bericht opnieuw bepaald (gecachet,
        # 10 s), zodat gewijzigde groepen, een gedeactiveerde gebruiker of een ingetrokken
        # deellink meteen gelden, ook voor een kaart die al open staat.
        self.clients: dict[WebSocket, dict[str, str]] = {}

    @staticmethod
    def _allowed(p: Principal, msg: dict[str, Any]) -> bool:
        t = msg.get("type")
        if t in ("position", "tracker", "lost_seen"):
            tid = (msg.get("tracker") or {}).get("id")
            return tid is not None and p.sees(tid)
        if t == "tracker_deleted":
            return p.sees(msg.get("id", -1)) or p.tracker_ids is None
        if t == "geofence":
            own = msg["event"].get("owner")
            return (p.can("zones.view") and p.sees(msg["event"]["tracker_id"])
                    and (own is None or own == p.user_id))
        if t == "geofences":
            return p.can("zones.view")
        return True

    async def send(self, msg: dict[str, Any]) -> None:
        cache: dict[tuple[bool, bool], str] = {}
        for ws, cookies in list(self.clients.items()):
            p = _resolve(cookies)
            if p is None or not p.can("map.view"):
                self.clients.pop(ws, None)
                try:
                    await ws.close(code=4401)
                except Exception:  # noqa: BLE001
                    pass
                continue
            if not self._allowed(p, msg):
                continue
            key = (p.can("map.details"), p.can("companion.view"))
            if key not in cache:
                m = msg if key[0] else strip_msg(msg)
                if m.get("type") == "mesh" and not key[1]:
                    m = {"type": "mesh", "mesh": {k: m["mesh"].get(k) for k in ("connected", "name")}}
                cache[key] = json.dumps(m)
            try:
                await ws.send_text(cache[key])
            except Exception:  # noqa: BLE001
                self.clients.pop(ws, None)


class State:
    cfg: config.Config
    db: DB
    mesh: MeshLink
    hub: Hub
    sims: SimManager
    alerts: AlertManager
    settings: dict
    vault: keys.Vault


def get_settings() -> dict[str, Any]:
    return S.settings


def reload_settings() -> None:
    S.settings = setmod.effective(S.cfg, S.db.settings())


S = State()


def tracker_out(t: dict[str, Any], p: Optional[Principal] = None) -> dict[str, Any]:
    now = int(time.time())
    out = dict(t)
    out["stale"] = not t["last_rx"] or now - t["last_rx"] > S.settings["stale_after_h"] * 3600
    out["has_authkey"] = bool(t.get("authkey"))
    out.pop("authkey", None)                 # nooit in lijsten of live-berichten
    if p is not None and not p.can("map.details"):
        out = strip_tracker(out)
    return out


DETAIL_FIELDS = ("pubkey", "last_snr", "last_path_len", "last_seq", "notes", "authkey")


def strip_tracker(t: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in t.items() if k not in DETAIL_FIELDS}


def strip_msg(msg: dict[str, Any]) -> dict[str, Any]:
    m = dict(msg)
    if "tracker" in m and isinstance(m["tracker"], dict):
        m["tracker"] = strip_tracker(m["tracker"])
    if "position" in m and isinstance(m["position"], dict):
        m["position"] = {k: v for k, v in m["position"].items() if k not in ("snr", "path_len", "raw", "seq")}
    return m


# ---- verwerking van berichten ----------------------------------------------------

async def process(prefix: str, text: str, sender_ts, snr, path_len, simulated: bool = False,
                  via: Optional[dict[str, Any]] = None) -> None:
    before = S.db.tracker_by_prefix(prefix)
    pos = ingest.handle(S.db, S.cfg, prefix, text, sender_ts, snr, path_len)
    if not pos:
        if not simulated:
            log.info("bericht van %s genegeerd: %r", prefix, text[:60])
        return
    t = S.db.tracker(pos["tracker_id"])
    S.db.update_tracker(t["id"], last_via=f"kanaal {via['name']}" if via else "sim")
    if via and S.db.set_tracker_channel(t["id"], via["id"]):   # trackingkanaal volgt het laatste geldige bericht
        _pcache.clear()
        await S.hub.send({"type": "channels"})
    t = S.db.tracker(t["id"])
    extras = pos.pop("extras", [])
    if pos["state"] in HISTORY_STATES:
        await _process_slow(t, pos, extras, before, simulated)
        return
    if not simulated:
        log.info("positie %s seq=%s state=%s%s", t["alias"], pos["seq"], pos["state"],
                 f" (+{len(extras)} eerdere punten)" if extras else "")
    # eerdere punten eerst, chronologisch: zones en de live kaart volgen de echte volgorde
    for ep in extras:
        await S.hub.send({"type": "position", "position": ep, "tracker": tracker_out(t)})
        if not ep["suspect"]:
            for ev in geofence.evaluate(S.db, t["id"], ep["lat"], ep["lon"], ep["ts"]):
                ev["tracker"] = t["alias"]
                await S.hub.send({"type": "geofence", "event": ev})
                S.alerts.fire(t, "zone_in" if ev["event"] == "enter" else "zone_out", ep, ev["geofence"], ev.get("owner"))
    await S.hub.send({"type": "position", "position": pos, "tracker": tracker_out(t)})
    S.alerts.fire(t, pos["state"], pos)
    await _lost_seen(t, pos, before)
    _bat_power_alerts(t, pos, before)
    if pos["lat"] is not None and not pos["suspect"]:
        await _zones(t, pos)


async def _lost_seen(t: dict[str, Any], pos: dict[str, Any], before: Optional[dict[str, Any]]) -> None:
    if before is not None and before.get("lost"):
        # De status 'verloren' blijft; elke regel met lost_seen krijgt een melding (cooldown per regel).
        S.db.mark_lost_seen(t["id"], int(time.time()))
        log.warning("verloren tracker %s is terug opgedoken (%s)", t["alias"], pos["state"])
        S.alerts.fire(t, "lost_seen", pos)
        await S.hub.send({"type": "lost_seen", "tracker": tracker_out(S.db.tracker(t["id"]))})


def _bat_power_alerts(t: dict[str, Any], pos: dict[str, Any], before: Optional[dict[str, Any]]) -> None:
    if pos["bat"] is not None and pos["bat"] < 20 and (before is None or before["last_bat"] is None
                                                          or before["last_bat"] >= 20):
        S.alerts.fire(t, "bat_low", pos)
    if pos.get("power") and before is not None and before.get("last_power") and before["last_power"] != pos["power"]:
        S.alerts.fire(t, "usb_on" if pos["power"] == "u" else "usb_off", pos)


async def _zones(t: dict[str, Any], pos: dict[str, Any]) -> None:
    """Zones toetsen voor een nieuwe (live) positie, met meldingen en de DM van de zone."""
    for ev in geofence.evaluate(S.db, t["id"], pos["lat"], pos["lon"], pos["ts"]):
        ev["tracker"] = t["alias"]
        log.info("geofence: %s %s %s", t["alias"], ev["event"], ev["geofence"])
        await S.hub.send({"type": "geofence", "event": ev})
        S.alerts.fire(t, "zone_in" if ev["event"] == "enter" else "zone_out", pos, ev["geofence"], ev.get("owner"))
        if ev["notify_pubkey"] and S.mesh.connected:
            verb = "is binnengekomen in" if ev["event"] == "enter" else "heeft verlaten:"
            try:
                await send_text(S.mesh, ev["notify_pubkey"], f"MeshTrack: {t['alias']} {verb} {ev['geofence']}")
            except Exception as e:  # noqa: BLE001
                log.warning("geofence-DM mislukt: %s", e)


async def _process_slow(t: dict[str, Any], pos: dict[str, Any], extras: list[dict[str, Any]],
                        before: Optional[dict[str, Any]], simulated: bool) -> None:
    """SlowTrack (L) en FIFO (Q): punten uit het verleden, meestal ouder dan de live-positie. Ze gaan chronologisch naar
    de live kaart als gewone "position"-berichten (de kaart voegt op ts in, de marker volgt
    tracker.last_*, die nooit terug in de tijd gaat). Elk punt krijgt "slow": 1 en, als het
    niet nieuwer is dan de live-positie van vóór dit bericht, "history": 1.
    Geen meldingen voor de toestand L/Q zelf; zones, batterij en voeding alleen voor punten die
    nieuwer zijn dan die live-positie: de zonetoestand (binnen/buiten) is de huidige, en een
    oud punt zou die terugdraaien en valse in/uit-meldingen geven. Wel 'verloren tracker
    gezien': het bericht zelf bewijst dat de tracker nu leeft."""
    prev_ts = before.get("last_ts") if before else None
    pts = extras + ([pos] if pos.pop("stored", True) else [])
    for p in pts:
        p["slow"], p["history"] = 1, int(prev_ts is not None and p["ts"] <= prev_ts)
    live = [p for p in pts if not p["history"]]
    if not simulated:
        log.info("%s %s seq=%s: %d punten (%d nieuwer dan de live-positie)",
                 "FIFO" if pos["state"] == "Q" else "SlowTrack", t["alias"], pos["seq"], len(pts), len(live))
    out = tracker_out(t)
    for p in pts:
        await S.hub.send({"type": "position", "position": p, "tracker": out})
    await _lost_seen(t, pos, before)
    if live and live[-1] is pos:
        _bat_power_alerts(t, pos, before)
    for p in live:
        if p["lat"] is not None and not p["suspect"]:
            await _zones(t, p)


async def on_message(prefix: str, text: str, sender_ts, snr, path_len) -> None:
    """DM aan de server-companion. Sinds 1.0 sturen trackers alleen nog via een kanaal;
    een oud T1-bericht via DM wordt genoteerd bij de genegeerde berichten."""
    if text.startswith("T1|"):
        S.db.log_unknown(prefix, "DM van een tracker (oude firmware): flash naar kanaal-firmware", text)
    else:
        log.info("DM van %s genegeerd: %r", prefix, text[:60])


# ---- kanalen -------------------------------------------------------------------------

def channel_tag(authkey_hex: str, body: str) -> str:
    """Handtekening van een kanaalbericht: eerste 4 bytes HMAC-SHA256 over "<pubkey8>|<rest>"."""
    return hmac.new(bytes.fromhex(authkey_hex), body.encode(), hashlib.sha256).hexdigest()[:8]


async def on_channel(slot: int, text: str, sender_ts, snr, path_len) -> None:
    """Kanaalbericht "naam: T1C|<pk8>|<tag>|<seq>|...". Alleen MeshTrack-berichten op een
    kanaal dat we kennen; de handtekening bewijst dat het van die tracker komt."""
    ch = S.db.channel_by_slot(slot) if slot is not None else None
    if not ch:
        return
    body = text.split(": ", 1)[1] if ": " in text else text
    if body.startswith(("T1A|", "T1F|")):     # eigen bevestigingen (echo via een repeater): negeren
        return
    if not body.startswith("T1C|"):
        return
    parts = body.split("|", 3)
    if len(parts) < 4 or len(parts[1]) != 8:
        S.db.log_unknown("?", f"kanaal {ch['name']}: ongeldig", body)
        return
    _, pk, tag, rest = parts
    t = S.db.tracker_by_prefix(pk)
    if not t or t["kind"] != "real":
        S.db.log_unknown(pk, f"kanaal {ch['name']}: onbekende tracker", body)
        return
    if tag == "-":
        if ch["require_sig"]:
            S.db.log_unknown(pk, f"kanaal {ch['name']}: niet ondertekend", body)
            return
    elif not t.get("authkey") or not hmac.compare_digest(channel_tag(t["authkey"], f"{pk}|{rest}"), tag.lower()):
        S.db.log_unknown(pk, f"kanaal {ch['name']}: ongeldige handtekening", body)
        return
    await process(pk, "T1|" + rest, sender_ts, snr, path_len, via=ch)
    fields = rest.split("|")
    if len(fields) > 1 and fields[1] == "E" and tag != "-" and t.get("authkey"):   # SOS: bevestigen
        _spawn(_sos_ack_later(ch, t, pk, fields[0]))
    if len(fields) > 14 and fields[1] == "Q" and tag != "-" and t.get("authkey"):   # FIFO: bevestiging gevraagd?
        # Ook als alle punten dubbel waren: de tracker moet ze uit zijn wachtrij kunnen halen.
        # Niet bij een ongeldig bericht: dan zou de tracker punten wissen die we nooit hadden.
        try:
            r = parse_t1("T1|" + rest)
        except ProtocolError:
            r = None
        if r is not None and r.ack_requested and r.fix_ts:
            fifo_request(ch, t, pk, r.fix_ts)


_sos_acked: dict[tuple[int, str], float] = {}
_bg: set = set()
SOS_ACK_DELAY_S = 4.0   # los van de afhandeling van het binnenkomende bericht, en na de herhalingen van de SOS


def _spawn(coro) -> asyncio.Task:
    task = asyncio.create_task(coro)
    _bg.add(task)
    task.add_done_callback(_bg.discard)
    return task


# ---- FIFO-bevestiging (T1F) ------------------------------------------------------------
# Een tracker vraagt een bevestiging met vlag "f" op een Q-bericht (alleen als hij verstuurde maar
# nog niet bevestigde punten heeft). De server bundelt: "T1F|<pk8>:<tag>:<upto>|..." met tot
# FIFO_MAX_PER_MSG trackers van hetzelfde kanaal; tag = HMAC(authsleutel van die tracker,
# "<pk8>|F|<upto>"), upto = hoogste fix_ts van de gevraagde Q-punten. Schaal (10-20 trackers):
#  - per tracker: eerste keer FIFO_ACK_DELAY_S na zijn laatste "f"-bericht (debounce), daarna
#    hoogstens één keer per FIFO_TRACKER_INTERVAL_S;
#  - per kanaal hoogstens één T1F per FIFO_CHANNEL_INTERVAL_S, en in totaal T1F_MAX_PER_HOUR per uur.
# Wat moet wachten blijft staan en wordt samengevoegd (per tracker de hoogste upto).
FIFO_ACK_DELAY_S = 20.0
FIFO_TRACKER_INTERVAL_S = 600.0
FIFO_CHANNEL_INTERVAL_S = 60.0
T1F_MAX_PER_HOUR = 20
FIFO_MAX_PER_MSG = 4
FIFO_TICK_S = 2.0
_fifo_pending: dict[int, dict[str, Any]] = {}    # tracker-id -> {upto, ch_id, pk, ready}
_fifo_tracker_sent: dict[int, float] = {}        # tracker-id -> laatste T1F met die tracker erin
_fifo_chan_sent: dict[int, float] = {}           # kanaal-id -> laatste T1F op dat kanaal
_fifo_hour: list[float] = []                     # verzendtijden van het laatste uur (alle kanalen)
_fifo_capped: set[str] = set()                   # al gemelde begrenzingen (geen logspam)


def fifo_entry(authkey_hex: str, pk: str, upto_ts: int) -> str:
    """"<pk8>:<tag>:<upto_ts>", tag = HMAC(authsleutel, "<pk8>|F|<upto_ts>")."""
    return f"{pk}:{channel_tag(authkey_hex, f'{pk}|F|{upto_ts}')}:{upto_ts}"


def fifo_ack_text(entries: list[str]) -> str:
    return "T1F|" + "|".join(entries)


def fifo_request(ch: dict[str, Any], t: dict[str, Any], pk: str, fix_ts: int, now: Optional[float] = None) -> None:
    """Q-bericht met "f": upto bijhouden en het vroegste verzendmoment voor deze tracker zetten."""
    now = time.time() if now is None else now
    e = _fifo_pending.get(t["id"])
    upto = max(fix_ts, e["upto"]) if e else fix_ts
    ready = max(now + FIFO_ACK_DELAY_S, _fifo_tracker_sent.get(t["id"], -1e18) + FIFO_TRACKER_INTERVAL_S)
    _fifo_pending[t["id"]] = {"upto": upto, "ch_id": ch["id"], "pk": pk, "ready": ready}


def _fifo_cap_log(key: str, msg: str, *args: Any) -> None:
    if key not in _fifo_capped:
        _fifo_capped.add(key)
        log.info(msg, *args)


async def fifo_tick(now: Optional[float] = None) -> int:
    """Klaarstaande bevestigingen versturen binnen de limieten. Geeft het aantal T1F-berichten terug."""
    now = time.time() if now is None else now
    _fifo_hour[:] = [x for x in _fifo_hour if now - x < 3600]
    ready = sorted((e["ready"], tid) for tid, e in _fifo_pending.items() if e["ready"] <= now)
    if not ready:
        _fifo_capped.clear()
        return 0
    if not S.mesh.connected:
        return 0                               # blijven staan tot de companion terug is
    by_ch: dict[int, list[int]] = {}
    for _, tid in ready:
        by_ch.setdefault(_fifo_pending[tid]["ch_id"], []).append(tid)
    sent = 0
    for cid, tids in by_ch.items():
        if len(_fifo_hour) >= T1F_MAX_PER_HOUR:
            _fifo_cap_log("hour", "FIFO-bevestiging uitgesteld: maximum %d T1F per uur bereikt (%d trackers wachten)",
                          T1F_MAX_PER_HOUR, len(ready))
            break
        if now - _fifo_chan_sent.get(cid, -1e18) < FIFO_CHANNEL_INTERVAL_S:
            _fifo_cap_log(f"ch{cid}", "FIFO-bevestiging op kanaal %s uitgesteld: max. één T1F per %d s",
                          cid, FIFO_CHANNEL_INTERVAL_S)
            continue
        ch = S.db.channel(cid)
        batch, entries = [], []
        for tid in tids:
            t = S.db.tracker(tid)
            if not ch or not t or not t.get("authkey"):
                _fifo_pending.pop(tid, None)   # kanaal of sleutel weg: niets meer te bevestigen
                continue
            if len(batch) < FIFO_MAX_PER_MSG:
                e = _fifo_pending[tid]
                batch.append((t, e))
                entries.append(fifo_entry(t["authkey"], e["pk"], e["upto"]))
        if not entries:
            continue
        for t, _ in batch:
            _fifo_pending.pop(t["id"], None)
        _fifo_chan_sent[cid] = now
        _fifo_hour.append(now)
        _fifo_capped.discard("hour")
        _fifo_capped.discard(f"ch{cid}")
        try:
            await S.mesh.send_channel(ch["slot"], fifo_ack_text(entries), ch.get("region") or "")
        except Exception as ex:  # noqa: BLE001
            log.warning("FIFO-bevestiging op kanaal %s mislukt: %s", ch["name"], ex)
            for t, e in batch:                 # terugzetten, tenzij er intussen een nieuwer verzoek is
                _fifo_pending.setdefault(t["id"], {**e, "ready": now + FIFO_CHANNEL_INTERVAL_S})
            continue
        sent += 1
        for t, e in batch:
            _fifo_tracker_sent[t["id"]] = now
            log.info("FIFO-bevestiging T1F tot %s naar %s", e["upto"], t["alias"])
    return sent


async def fifo_scheduler() -> None:
    while True:
        await asyncio.sleep(FIFO_TICK_S)
        try:
            await fifo_tick()
        except Exception:  # noqa: BLE001
            log.exception("FIFO-bevestiging")


async def _sos_ack_later(ch: dict[str, Any], t: dict[str, Any], pk: str, seq: str) -> None:
    """Een bevestiging verstuurd vanuit de event-afhandeling van meshcore-py kreeg wel 'OK' maar ging nooit
    de lucht in (gezien in de analyzer); los daarvan en iets later lukt het wel."""
    await asyncio.sleep(SOS_ACK_DELAY_S)
    await sos_ack(ch, t, pk, seq)


def sos_ack_text(authkey_hex: str, pk: str, seq: str) -> str:
    """Bevestiging van een SOS voor de tracker: "T1A|<pk8>|<tag>|<seq>", tag = HMAC(authsleutel, "<pk8>|A|<seq>")."""
    return f"T1A|{pk}|{channel_tag(authkey_hex, f'{pk}|A|{seq}')}|{seq}"


async def sos_ack(ch: dict[str, Any], t: dict[str, Any], pk: str, seq: str) -> None:
    """Eén keer per SOS-bericht een ondertekende bevestiging op hetzelfde kanaal, met de regio
    van het kanaal. De tracker laat dan een eigen toon horen (firmware 0.7.3+)."""
    key = (t["id"], seq)
    now = time.time()
    for k in [k for k, ts in _sos_acked.items() if now - ts > 3600]:
        del _sos_acked[k]
    if key in _sos_acked or not S.mesh.connected:
        return
    _sos_acked[key] = now
    try:
        await S.mesh.send_channel(ch["slot"], sos_ack_text(t["authkey"], pk, seq), ch.get("region") or "")
        log.warning("SOS van %s (seq %s) bevestigd op kanaal %s", t["alias"], seq, ch["name"])
    except Exception as e:  # noqa: BLE001
        _sos_acked.pop(key, None)
        log.warning("SOS-bevestiging aan %s mislukt: %s", t["alias"], e)


async def sync_channels() -> list[str]:
    """Onze kanalen op de server-companion zetten. Geeft problemen terug (leeg = goed)."""
    if not S.mesh.connected:
        return ["companion niet verbonden: kanalen volgen bij de volgende verbinding"]
    issues = []
    for ch in S.db.channels():
        if not ch["active"]:
            continue
        try:
            await S.mesh.set_channel(ch["slot"], ch["name"], ch["secret"])
        except Exception as e:  # noqa: BLE001
            issues.append(f"{ch['name']}: {e}")
    return issues


async def on_sim_message(prefix: str, text: str, sender_ts, snr, path_len) -> None:
    await process(prefix, text, sender_ts, snr, path_len, simulated=True)


def make_router() -> Optional[Router]:
    # Routeren op een eigen wegenbestand (Benelux z14) als dat er is, zodat een
    # grotere weergavekaart de routering niet trager of zwaarder maakt.
    p = Path(S.cfg.tiles_dir) / "roads.pmtiles"
    if not p.exists():
        p = Path(S.cfg.tiles_dir) / "basemap.pmtiles"
    if not p.exists():
        log.warning("simulator: %s ontbreekt, routeren onmogelijk", p)
        return None
    return Router(str(p))


def make_wide_router() -> Optional[Router]:
    """Router voor de reiziger: grote wegen op z11 van de weergavekaart (alle landen)."""
    p = Path(S.cfg.tiles_dir) / "basemap.pmtiles"
    return Router(str(p), z=11) if p.exists() else None   # z11: enkel hoofdwegen, snel en zuinig


def backfill_sim(tid: int, days: float) -> int:
    """Simulator een historiek geven: `days` dagen terug in virtuele tijd rijden en
    de berichten rechtstreeks opslaan (geen live-updates, zones of meldingen)."""
    row = S.db.sim(tid)
    if not row or days <= 0:
        return 0
    router = S.sims.router_for(row["profile"])
    if router is None:
        return 0
    from .sim import SimTracker
    start = time.time() - days * 86400
    sim = SimTracker(row, router, on_sim_message, {"last_lat": row["home_lat"], "last_lon": row["home_lon"],
                                                   "last_bat": 100, "last_seq": 30000})
    count = 0

    def sink(m):
        nonlocal count
        prefix, text, ts, snr, hops = m
        if ingest.handle(S.db, S.cfg, prefix, text, ts, snr, hops, now=ts):
            count += 1

    sim.backfill(start, time.time() - 5, sink)
    return count


async def on_connect() -> None:
    # Trackers sturen via kanalen: de companion moet die kanalen kennen (geen contacten nodig).
    for issue in await sync_channels():
        log.warning("kanaal: %s", issue)
    await S.hub.send({"type": "mesh", "mesh": S.mesh.status()})


async def pruner() -> None:
    while True:
        n = S.db.prune(int(time.time()) - S.settings["retention_days"] * 86400)
        if n:
            log.info("retentie: %d oude posities verwijderd", n)
        await asyncio.sleep(6 * 3600)


async def silent_watch() -> None:
    while True:
        await asyncio.sleep(300)
        try:
            S.alerts.check_silent(S.db.trackers())
        except Exception:  # noqa: BLE001
            log.exception("stilte-controle")


def bootstrap_users() -> None:
    """Eerste start met RBAC: standaardgroepen en een beheerder uit config.yaml."""
    if not S.db.groups():
        for g in rbac.DEFAULT_GROUPS:
            S.db.save_group(None, g)
    # Beheerders hebben altijd alle rechten, ook rechten die in een update bijkwamen.
    for g in S.db.groups():
        if g["name"] == "Beheerders" and set(g["perms"]) != set(PERMS):
            S.db.save_group(g["id"], {**g, "perms": list(PERMS)})
    if S.db.count_users() == 0 and S.cfg.auth_password_hash:
        admins = next(g for g in S.db.groups() if g["name"] == "Beheerders")
        S.db.add_user(S.cfg.auth_user, "Beheerder", S.cfg.auth_password_hash, admins["id"])
        S.db.audit("systeem", "gebruiker aangemaakt", f"{S.cfg.auth_user} (uit config.yaml)")


@asynccontextmanager
async def lifespan(app: FastAPI):
    S.cfg = config.load()
    Path(S.cfg.db_path).parent.mkdir(parents=True, exist_ok=True)
    S.db = DB(S.cfg.db_path)
    S.vault = keys.Vault(S.cfg.keystore_secret or S.cfg.session_secret)
    bootstrap_users()
    reload_settings()
    S.hub = Hub()
    S.mesh = MeshLink(S.cfg.mesh_host, S.cfg.mesh_port, S.cfg.keepalive_s, on_message, on_connect)
    S.sims = SimManager(make_router, on_sim_message, make_wide_router)
    S.alerts = AlertManager(S.db, S.mesh, get_settings, _user_sees)
    S.mesh.on_channel = on_channel
    tasks = [asyncio.create_task(S.mesh.run()), asyncio.create_task(pruner()),
             asyncio.create_task(S.alerts.run()), asyncio.create_task(silent_watch()),
             asyncio.create_task(fifo_scheduler())]
    tiles = Path(S.cfg.tiles_dir)
    if tiles.is_dir():
        app.mount("/tiles", StaticFiles(directory=tiles), name="tiles")
    else:
        log.warning("tegelmap %s ontbreekt: kaart zonder achtergrond", tiles)
    for row in S.db.sims():
        if row["running"]:
            try:
                await S.sims.start(row, S.db.tracker(row["tracker_id"]))
            except Exception as e:  # noqa: BLE001
                log.warning("simulator %s niet gestart: %s", row["alias"], e)
    yield
    await S.sims.stop_all()
    await S.mesh.stop()
    for t in tasks:
        t.cancel()


app = FastAPI(title="MeshTrack", lifespan=lifespan, docs_url=None, redoc_url=None)
app.mount("/static", StaticFiles(directory=STATIC), name="static")

_ASSET = re.compile(r'((?:src|href)="/static/[^"?]+\.(?:js|css|svg))"')


def page(name: str) -> HTMLResponse:
    """HTML-pagina met een versie achter elk script en stylesheet (?v=<wijzigtijd>).
    Cloudflare laat de browser /static 4 uur cachen; zo krijgt elke update een nieuwe URL."""
    html = (STATIC / name).read_text(encoding="utf-8")

    def ver(m: re.Match) -> str:
        f = STATIC / m.group(1).split('"/static/', 1)[1]
        v = int(f.stat().st_mtime) if f.exists() else 0
        return f'{m.group(1)}?v={v}"'
    return HTMLResponse(_ASSET.sub(ver, html), headers={"Cache-Control": "no-cache"})



# ---- wie is dit? -------------------------------------------------------------------

_pcache: dict[tuple, tuple[float, Optional[Principal]]] = {}


def _resolve(cookies: dict[str, str]) -> Optional[Principal]:
    sess = auth.check_session(cookies.get(auth.COOKIE), S.cfg.session_secret)
    # Een ingelogde gebruiker blijft ingelogd, ook met een deellink-cookie in de browser.
    if sess:
        key = ("u",) + sess
        hit = _pcache.get(key)
        if hit and hit[0] > time.time():
            return hit[1]
        user = S.db.user_by_name(sess[0])
        p = None
        if user and user["active"] and user["session_gen"] == sess[1]:
            gids = set(S.db.user_group_ids(user["id"]))
            groups = [g for g in S.db.groups() if g["id"] in gids]
            if groups:
                p = rbac.principal_for_user(user, groups, S.db.channel_members())
        _pcache[key] = (time.time() + 10, p)
        return p
    tok = cookies.get(SHARE_COOKIE)
    if tok:
        key = ("s", tok)
        hit = _pcache.get(key)
        if hit and hit[0] > time.time():
            return hit[1]
        share = S.db.share_by_token(tok)
        p = None
        if share and (share["expires"] is None or share["expires"] > time.time()):
            creator = _user_principal(share["created_by"]) if share.get("channels") else None
            p = rbac.principal_for_share(share, S.db.channel_members(), creator)
        _pcache[key] = (time.time() + 10, p)
        return p
    return None


def _user_principal(username: str) -> Optional[Principal]:
    """Principal van een (actieve) gebruiker, los van een sessie."""
    user = S.db.user_by_name(username)
    if not user or not user["active"]:
        return None
    gids = set(S.db.user_group_ids(user["id"]))
    groups = [g for g in S.db.groups() if g["id"] in gids]
    return rbac.principal_for_user(user, groups, S.db.channel_members()) if groups else None


def _user_sees(user_id: int, tracker_id: int) -> bool:
    key = ("uid", user_id)
    hit = _pcache.get(key)
    if hit and hit[0] > time.time():
        p = hit[1]
    else:
        u = S.db.user(user_id)
        p = _user_principal(u["username"]) if u else None
        _pcache[key] = (time.time() + 10, p)
    return p is not None and p.sees(tracker_id)


def who(request: Request) -> Principal:
    p = getattr(request.state, "p", None)
    if p is None:
        raise HTTPException(401, "niet ingelogd")
    return p


def need(request: Request, *perms: str) -> Principal:
    """Eén van de rechten volstaat."""
    p = who(request)
    if not any(p.can(x) for x in perms):
        raise HTTPException(403, "geen toegang")
    return p


def audit(p: Principal, action: str, detail: str = "") -> None:
    S.db.audit(p.name, action, detail)


PUBLIC = ("/login", "/api/login", "/static/", "/api/health", "/favicon", "/s/", "/help",
          "/offline", "/offline-sw.js", "/manifest.webmanifest", "/api/offline/maps",
          "/tiles/offline/", "/tiles/fonts/", "/tiles/sprites/")
# De offline-app werkt volledig zonder account en gebruikt niets uit de database: wie de
# kanaalsleutel kent, kan meelezen. Van de server komen alleen kaarten, lettertypes en
# symbolen (gewone OpenStreetMap-gegevens).
PAGE_PERMS = {"/": ("map.view",), "/admin": ("trackers.manage", "sims.manage"), "/devices": ("trackers.serial",),
              "/users": ("users.manage", "share.manage"), "/log": ("log.view",), "/kanalen": ("map.view",),
              "/system": ("alerts.manage", "alerts.personal", "system.manage", "companion.view")}


@app.middleware("http")
async def guard(request: Request, call_next):
    path = request.url.path
    request.state.p = _resolve(request.cookies) if S.__dict__.get("db") else None
    p = request.state.p
    if p is None and not path.startswith(PUBLIC):
        if path.startswith("/api/") or path.startswith("/tiles"):
            return JSONResponse({"detail": "niet ingelogd"}, status_code=401)
        return RedirectResponse("/login")
    if p is not None and path in PAGE_PERMS and not any(p.can(x) for x in PAGE_PERMS[path]):
        return RedirectResponse("/" if p.can("map.view") and path != "/" else "/login?geen_toegang=1")
    resp: Response = await call_next(request)
    resp.headers.setdefault("X-Content-Type-Options", "nosniff")
    resp.headers.setdefault("Referrer-Policy", "same-origin")
    resp.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
    if path.startswith("/tiles"):
        resp.headers["Cache-Control"] = "no-cache"
    elif path.startswith("/static") or path in ("/", "/admin", "/devices", "/login", "/users", "/help", "/log", "/system", "/kanalen"):
        # Altijd hervalideren (ETag/Last-Modified -> 304): na een update nooit
        # een oude CSS/JS naast nieuwe HTML.
        resp.headers["Cache-Control"] = "no-cache"
    return resp


# ---- pagina's -----------------------------------------------------------------

@app.get("/")
async def index():
    return page("index.html")


@app.get("/admin")
async def admin():
    return page("admin.html")


@app.get("/devices")
async def devices():
    return page("devices.html")


@app.get("/kanalen")
async def kanalen_page():
    return page("kanalen.html")


# ---- offline-app (PWA) -------------------------------------------------------------

@app.get("/offline")
async def offline_page():
    return page("offline.html")


@app.get("/offline-sw.js")
async def offline_sw():
    return FileResponse(STATIC / "offline-sw.js", media_type="text/javascript",
                        headers={"Cache-Control": "no-cache", "Service-Worker-Allowed": "/"})


@app.get("/manifest.webmanifest")
async def manifest():
    return FileResponse(STATIC / "manifest.webmanifest", media_type="application/manifest+json")


OFFLINE_MAPS = {   # bestand -> (naam, omschrijving)
    "limburg-z14": ("Limburg (gedetailleerd)", "Belgisch en Nederlands Limburg, alle straten (zoom 14)"),
    "belgie-z13": ("België", "Heel België, tot op straatniveau (zoom 13)"),
    "benelux-z10": ("Benelux (overzicht)", "Steden en hoofdwegen (zoom 10)"),
    "benelux-z12": ("Benelux", "Benelux met de meeste straten (zoom 12)"),
    "frankrijk-z10": ("Frankrijk (overzicht)", "Steden en hoofdwegen (zoom 10)"),
    "frankrijk-z12": ("Frankrijk", "Frankrijk met de meeste straten (zoom 12)"),
    "duitsland-z10": ("Duitsland (overzicht)", "Steden en hoofdwegen (zoom 10)"),
    "duitsland-z12": ("Duitsland", "Duitsland met de meeste straten (zoom 12)"),
}


@app.get("/api/offline/maps")
async def offline_maps():
    d = Path(S.cfg.tiles_dir) / "offline"
    out = []
    for key, (name, desc) in OFFLINE_MAPS.items():
        f = d / f"{key}.pmtiles"
        if f.exists():
            out.append({"key": key, "name": name, "description": desc, "size": f.stat().st_size,
                        "url": f"/tiles/offline/{key}.pmtiles", "version": int(f.stat().st_mtime)})
    return out


@app.get("/users")
async def users_page():
    return page("users.html")


@app.get("/help")
async def help_page():
    return page("help.html")


@app.get("/login")
async def login_page():
    return page("login.html")


@app.get("/s/{token}")
async def open_share(token: str, request: Request):
    """Deellink: zet een cookie en toon de kaart (zonder login)."""
    share = S.db.share_by_token(token)
    if not share or (share["expires"] is not None and share["expires"] <= time.time()):
        return PlainTextResponse("Deze link bestaat niet (meer) of is verlopen.", status_code=404)
    S.db.touch_share(share["id"])
    resp = RedirectResponse("/")
    secure = request.headers.get("x-forwarded-proto", request.url.scheme) == "https"
    age = int(share["expires"] - time.time()) if share["expires"] else 30 * 86400
    resp.set_cookie(SHARE_COOKIE, token, max_age=age, httponly=True, samesite="lax", secure=secure)
    return resp


# ---- login ----------------------------------------------------------------------

class Login(BaseModel):
    user: str
    password: str


_fails: dict[str, list[float]] = {}


@app.post("/api/login")
async def login(body: Login, request: Request):
    ip = request.headers.get("cf-connecting-ip") or (request.client.host if request.client else "?")
    recent = [t for t in _fails.get(ip, []) if t > time.time() - 300]
    if len(recent) >= 10:
        raise HTTPException(429, "te veel pogingen, probeer over 5 minuten opnieuw")
    user = S.db.user_by_name(body.user.strip())
    if not user or not user["active"] or not auth.verify_password(body.password, user["password_hash"]):
        _fails[ip] = recent + [time.time()]
        await asyncio.sleep(1)
        raise HTTPException(401, "gebruiker of wachtwoord fout")
    _fails.pop(ip, None)
    S.db.update_user(user["id"], last_login=int(time.time()))
    S.db.audit(user["username"], "ingelogd", ip)
    resp = JSONResponse({"ok": True})
    secure = request.headers.get("x-forwarded-proto", request.url.scheme) == "https"
    resp.set_cookie(auth.COOKIE, auth.make_session(user["username"], S.cfg.session_secret, S.cfg.session_days,
                                                   user["session_gen"]),
                    max_age=S.cfg.session_days * 86400, httponly=True, samesite="lax", secure=secure)
    resp.delete_cookie(SHARE_COOKIE)
    return resp


@app.post("/api/logout")
async def logout():
    resp = JSONResponse({"ok": True})
    resp.delete_cookie(auth.COOKIE)
    resp.delete_cookie(SHARE_COOKIE)
    return resp


class PasswordIn(BaseModel):
    old: str
    new: str = Field(max_length=200)


@app.post("/api/me/password")
async def change_password(b: PasswordIn, request: Request):
    p = who(request)
    if p.kind != "user":
        raise HTTPException(403, "alleen voor gebruikers")
    user = S.db.user(p.user_id)
    if not auth.verify_password(b.old, user["password_hash"]):
        raise HTTPException(401, "huidig wachtwoord fout")
    problem = auth.password_problem(b.new)
    if problem:
        raise HTTPException(422, f"nieuw wachtwoord: {problem}")
    S.db.update_user(user["id"], password_hash=auth.hash_password(b.new))
    audit(p, "eigen wachtwoord gewijzigd")
    fresh = S.db.user(user["id"])
    resp = JSONResponse({"ok": True})
    secure = request.headers.get("x-forwarded-proto", request.url.scheme) == "https"
    resp.set_cookie(auth.COOKIE, auth.make_session(fresh["username"], S.cfg.session_secret, S.cfg.session_days,
                                                   fresh["session_gen"]),
                    max_age=S.cfg.session_days * 86400, httponly=True, samesite="lax", secure=secure)
    return resp


# ---- algemeen ------------------------------------------------------------------

@app.get("/api/health")
async def health():
    m = S.mesh.status()
    return {"ok": True, "mesh_connected": m["connected"], "version": VERSION}


@app.get("/api/me")
async def me(request: Request):
    p = who(request)
    prefs = S.db.user_prefs(p.user_id) if p.kind == "user" else {}
    return {**p.public(), "user_id": p.user_id, "prefs": prefs, "perm_labels": {k: v[0] for k, v in PERMS.items()}}


@app.put("/api/me/prefs")
async def put_prefs(body: dict, request: Request):
    """Weergavevoorkeuren van de gebruiker (favorieten, zichtbaarheid, spoor, thema...).
    Samenvoegen met wat er al is; alleen eenvoudige waarden, max. ~16 kB."""
    p = who(request)
    if p.kind != "user":
        return {"ok": False}
    prefs = S.db.user_prefs(p.user_id)
    for k, v in body.items():
        if not isinstance(k, str) or len(k) > 40:
            continue
        if v is None:
            prefs.pop(k, None)
        else:
            prefs[k] = v
    raw = json.dumps(prefs)
    if len(raw) > 16384:
        raise HTTPException(413, "te veel voorkeuren")
    S.db.set_user_prefs(p.user_id, prefs)
    return {"ok": True}


@app.get("/api/status")
async def status(request: Request):
    p = who(request)
    mesh = S.mesh.status()
    if not p.can("companion.view"):
        mesh = {"connected": mesh["connected"], "name": mesh["name"]}
    return {"mesh": mesh, "map": {"center": S.cfg.map_center, "zoom": S.cfg.map_zoom},
            "tiles": (Path(S.cfg.tiles_dir) / "basemap.pmtiles").exists(),
            "stale_after_s": S.settings["stale_after_h"] * 3600, "version": VERSION}


# ---- trackers ------------------------------------------------------------------

@app.get("/api/trackers")
async def list_trackers(request: Request):
    p = who(request)
    out = [tracker_out(t, p) for t in S.db.trackers() if p.sees(t["id"])]
    if p.can("keys.manage"):
        counts = S.db.key_counts()
        for t in out:
            t["keys"] = counts.get(t["id"], 0)
    return out


class TrackerIn(BaseModel):
    pubkey: Optional[str] = None
    alias: Optional[str] = Field(None, max_length=40)
    color: Optional[str] = None
    icon: Optional[str] = Field(None, max_length=40)
    notes: Optional[str] = Field(None, max_length=500)
    active: Optional[bool] = None
    lost: Optional[bool] = None
    generate_key: bool = False        # nieuw toestel: sleutelpaar op de server maken
    channel_id: Optional[int] = None     # trackingkanaal (0 = geen)


def _check_fields(b: TrackerIn) -> None:
    if b.color is not None and not COLOR.match(b.color):
        raise HTTPException(422, "kleur moet #rrggbb zijn")
    if b.alias is not None and not b.alias.strip():
        raise HTTPException(422, "alias mag niet leeg zijn")


def _set_channel(tid: int, cid: Optional[int]) -> None:
    """Trackingkanaal kiezen in het formulier (0/None = geen kanaal: alleen beheerders zien hem)."""
    if cid and not S.db.channel(cid):
        raise HTTPException(422, "onbekend kanaal")
    if S.db.set_tracker_channel(tid, cid or None):
        _pcache.clear()                # zichtbaarheid kan veranderd zijn


@app.post("/api/trackers")
async def create_tracker(b: TrackerIn, request: Request):
    p = need(request, "trackers.manage")
    prv = None
    if b.generate_key:
        need(request, "keys.manage")
        prv, b.pubkey = keys.new_keypair()
    if not b.pubkey or not HEX64.match(b.pubkey.strip()):
        raise HTTPException(422, "pubkey moet 64 hex-tekens zijn")
    if not b.alias:
        raise HTTPException(422, "alias is verplicht")
    _check_fields(b)
    pk = b.pubkey.strip().lower()
    if any(t["pubkey"] == pk for t in S.db.trackers()):
        raise HTTPException(409, "deze tracker bestaat al")
    tid = S.db.add_tracker(pk, b.alias.strip(), b.color or "#e4572e", b.icon or "", b.notes or "",
                           True if b.active is None else b.active)
    if b.channel_id is not None:
        _set_channel(tid, b.channel_id)
    if prv:
        doc = _profile(S.db.tracker(tid), prv)
        S.db.add_key(tid, "generated", p.name, pk, "nieuw sleutelpaar (server)", keys.summary(doc), S.vault.seal(doc))
    t = S.db.tracker(tid)
    audit(p, "tracker toegevoegd", f"{t['alias']} ({pk[:12]}){' met sleutel van de server' if prv else ''}")
    await S.hub.send({"type": "tracker", "tracker": tracker_out(t)})
    return {"tracker": tracker_out(t, p)}


@app.put("/api/trackers/{tid}")
async def update_tracker(tid: int, b: TrackerIn, request: Request):
    old = S.db.tracker(tid)
    if not old:
        raise HTTPException(404, "onbekende tracker")
    p = need(request, "trackers.manage", "sims.manage" if old["kind"] == "sim" else "trackers.manage")
    _check_fields(b)
    S.db.update_tracker(tid, alias=b.alias.strip() if b.alias else None, color=b.color, icon=b.icon,
                        notes=b.notes, active=b.active)
    if b.channel_id is not None:
        _set_channel(tid, b.channel_id)
    if b.lost is not None and bool(b.lost) != bool(old.get("lost")):
        S.db.set_lost(tid, b.lost)
        audit(p, "tracker verloren gemeld" if b.lost else "tracker niet meer verloren", old["alias"])
    t = S.db.tracker(tid)
    audit(p, "tracker gewijzigd", t["alias"])
    await S.hub.send({"type": "tracker", "tracker": tracker_out(t)})
    return {"tracker": tracker_out(t, p)}


@app.delete("/api/trackers/{tid}")
async def delete_tracker(tid: int, request: Request, keep_contact: bool = False):
    t = S.db.tracker(tid)
    if not t:
        raise HTTPException(404, "onbekende tracker")
    p = need(request, "trackers.manage", "sims.manage" if t["kind"] == "sim" else "trackers.manage")
    await S.sims.stop(tid)
    S.db.delete_tracker(tid)
    note = ""
    if t["kind"] == "real" and not keep_contact and S.mesh.connected:
        try:
            await S.mesh.remove_contact(t["pubkey"])
            note = "contact verwijderd van de companion"
        except Exception as e:  # noqa: BLE001
            note = f"contact niet verwijderd: {e}"
    audit(p, "tracker verwijderd", t["alias"])
    await S.hub.send({"type": "tracker_deleted", "id": tid})
    return {"ok": True, "contact": note}


# ---- kanalen die je mag lezen ---------------------------------------------------------

@app.get("/api/channels/mine")
async def my_channels(request: Request):
    """De kanalen die deze gebruiker mag lezen: voor het kanaalfilter op de kaart en de pagina
    met sleutels. Naam, sleutel en QR alleen bij niveau 'sleutel' (beheerders: alles)."""
    p = who(request)
    members = S.db.channel_members()
    out = []
    for c in S.db.channels():
        lvl = p.channel_level(c["id"])
        if not lvl or not c["active"]:
            continue
        row = {"id": c["id"], "name": c["name"], "level": lvl, "region": c.get("region") or "",
               "trackers": len([t for t in members.get(c["id"], set()) if p.sees(t)])}
        if lvl == "sleutel":
            row["secret"] = c["secret"]
        out.append(row)
    return out


# ---- sleutels, klaarmaken en backups ---------------------------------------------

def _profile(t: dict[str, Any], prv: str) -> dict[str, Any]:
    return keys.profile(t["alias"][:31], prv, t["pubkey"], S.settings)


def _key_tracker(request: Request, tid: int) -> tuple[Principal, dict[str, Any]]:
    p = need(request, "keys.manage")
    t = S.db.tracker(tid)
    if not t or t["kind"] != "real":
        raise HTTPException(404, "onbekende tracker")
    return p, t


@app.get("/api/trackers/{tid}/keys")
async def list_keys(tid: int, request: Request):
    _key_tracker(request, tid)
    return S.db.keys(tid)


@app.get("/api/trackers/{tid}/provision")
async def provision(tid: int, request: Request):
    """Profiel om een toestel klaar te maken: de sleutel van de server (of de nieuwste backup)
    met de huidige standaardinstellingen uit Systeem, naam = alias, doel = de server-companion."""
    p, t = _key_tracker(request, tid)
    rows = S.db.keys(tid)
    if not rows:
        raise HTTPException(404, "de server kent de privésleutel van deze tracker niet")
    doc = S.vault.open(S.db.key(rows[0]["id"])["blob"])
    fresh = _profile(t, doc["private_key"])
    if not t.get("authkey"):
        S.db.set_authkey(t["id"], secrets.token_hex(16))
    fresh["meshtrack"]["authkey"] = S.db.tracker(t["id"])["authkey"]
    if rows[0]["kind"] != "generated":          # backup: eigen kanalen en instellingen behouden
        fresh["channels"] = doc.get("channels") or fresh["channels"]
        fresh["meshtrack"]["settings"] = {**(doc.get("meshtrack") or {}).get("settings", {}),
                                          **fresh["meshtrack"]["settings"]}
    audit(p, "privésleutel opgehaald (klaarmaken)", t["alias"])
    return fresh


class KeyIn(BaseModel):
    doc: dict
    kind: str = "backup"
    note: str = Field("", max_length=200)


@app.post("/api/trackers/{tid}/keys")
async def add_key(tid: int, b: KeyIn, request: Request):
    """Backup bewaren: van het toestel (via USB) of een export uit de MeshCore-app."""
    p, t = _key_tracker(request, tid)
    doc = b.doc
    prv, pub = str(doc.get("private_key") or "").lower(), str(doc.get("public_key") or t["pubkey"]).lower()
    if not keys.check_pair(prv, pub):
        raise HTTPException(422, "privésleutel en pubkey horen niet bij elkaar")
    if pub != t["pubkey"]:
        raise HTTPException(422, f"deze sleutel ({pub[:8]}) is niet die van {t['alias']} ({t['pubkey'][:8]})")
    doc["private_key"], doc["public_key"] = prv, pub
    kind = b.kind if b.kind in ("backup", "import") else "backup"
    kid = S.db.add_key(tid, kind, p.name, pub, b.note.strip(), keys.summary(doc), S.vault.seal(doc))
    audit(p, "backup met privésleutel bewaard", f"{t['alias']} ({kind})")
    return {"id": kid}


@app.get("/api/trackers/{tid}/keys/{kid}")
async def get_key(tid: int, kid: int, request: Request):
    p, t = _key_tracker(request, tid)
    row = S.db.key(kid)
    if not row or row["tracker_id"] != tid:
        raise HTTPException(404, "onbekende backup")
    audit(p, "backup met privésleutel opgehaald", t["alias"])
    return S.vault.open(row["blob"])


@app.delete("/api/trackers/{tid}/keys/{kid}")
async def delete_key(tid: int, kid: int, request: Request):
    p, t = _key_tracker(request, tid)
    row = S.db.key(kid)
    if not row or row["tracker_id"] != tid:
        raise HTTPException(404, "onbekende backup")
    S.db.delete_key(kid)
    audit(p, "backup verwijderd", t["alias"])
    return {"ok": True}


@app.get("/api/trackers/{tid}/authkey")
async def get_authkey(tid: int, request: Request, new: bool = False):
    """Authsleutel om kanaalberichten van deze tracker te ondertekenen (aangemaakt indien nodig).
    Gaat via USB naar de tracker (set authkey)."""
    p = need(request, "trackers.serial", "keys.manage")
    t = S.db.tracker(tid)
    if not t or t["kind"] != "real" or not p.sees(tid):
        raise HTTPException(404, "onbekende tracker")
    key = t.get("authkey")
    if new or not key:
        key = secrets.token_hex(16)
        S.db.set_authkey(tid, key)
        audit(p, "authsleutel aangemaakt" if not t.get("authkey") else "authsleutel vernieuwd", t["alias"])
    return {"authkey": key}


@app.delete("/api/trackers/{tid}/authkey")
async def delete_authkey(tid: int, request: Request):
    p = need(request, "trackers.manage")
    t = S.db.tracker(tid)
    if not t:
        raise HTTPException(404, "onbekende tracker")
    S.db.set_authkey(tid, None)
    audit(p, "authsleutel verwijderd", t["alias"])
    return {"ok": True}


# ---- kanalen (beheer) ----------------------------------------------------------------

class ChannelIn(BaseModel):
    name: str = Field(min_length=1, max_length=31)
    secret: str = ""                      # 32 hex; leeg bij een #hashtag-kanaal = afgeleid van de naam
    slot: int = Field(ge=0, le=39)
    require_sig: bool = True
    active: bool = True
    region: str = Field("be", max_length=30)   # regio (scope) die de tracker krijgt; leeg = geen


def _channel_body(b: ChannelIn, cid: Optional[int]) -> dict[str, Any]:
    name = b.name.strip()
    secret = b.secret.strip().lower()
    if not secret and name.startswith("#"):
        secret = hashlib.sha256(name.encode()).hexdigest()[:32]
    if not keys.is_hex(secret, 32):
        raise HTTPException(422, "sleutel: 32 hex-tekens (of laat leeg voor een #hashtag-kanaal)")
    for c in S.db.channels():
        if c["id"] == cid:
            continue
        if c["name"].lower() == name.lower():
            raise HTTPException(409, "dat kanaal bestaat al")
        if c["slot"] == b.slot:
            raise HTTPException(409, f"kanaalnummer {b.slot} is al in gebruik door {c['name']}")
    region = b.region.strip().lstrip("#")
    if any(ch in region for ch in " #|"):
        raise HTTPException(422, "regio: één woord, zonder #")
    return {"name": name, "secret": secret, "slot": b.slot, "require_sig": b.require_sig, "active": b.active,
            "region": region}


@app.get("/api/channels/device")
async def channels_for_device(request: Request):
    """Kanalen om op een tracker te zetten (USB-formulier): naam, sleutel. Alleen kanalen waarop
    een van je groepen het niveau 'sleutel' heeft (beheerders: alle)."""
    p = need(request, "trackers.serial")
    return [{"id": c["id"], "name": c["name"], "secret": c["secret"], "region": c.get("region") or ""}
            for c in S.db.channels() if c["active"] and p.knows_key(c["id"])]


@app.get("/api/channels")
async def list_channels(request: Request):
    need(request, "system.manage")
    chans = S.db.channels()
    slots = []
    if S.mesh.connected:
        try:
            slots = await S.mesh.channel_slots()
        except Exception as e:  # noqa: BLE001
            log.warning("kanalen van de companion lezen: %s", e)
    by_slot = {s["slot"]: s for s in slots}
    members = S.db.channel_members()
    for c in chans:
        s = by_slot.get(c["slot"])
        c["on_companion"] = bool(s and s["secret"] == c["secret"])
        c["members"] = len(members.get(c["id"], set()))
    return {"channels": chans, "companion": [{"slot": s["slot"], "name": s["name"]} for s in slots if s["name"]],
            "connected": S.mesh.connected}


@app.post("/api/channels")
async def create_channel(b: ChannelIn, request: Request):
    p = need(request, "system.manage")
    c = _channel_body(b, None)
    cid = S.db.save_channel(None, c)
    issues = await sync_channels()
    audit(p, "kanaal toegevoegd", f"{c['name']} (nummer {c['slot']})")
    await S.hub.send({"type": "channels"})
    return {"id": cid, "issues": issues}


@app.put("/api/channels/{cid}")
async def update_channel(cid: int, b: ChannelIn, request: Request):
    p = need(request, "system.manage")
    old = S.db.channel(cid)
    if not old:
        raise HTTPException(404, "onbekend kanaal")
    c = _channel_body(b, cid)
    S.db.save_channel(cid, {**c, "tracker_group_id": None})
    if old["slot"] != c["slot"] and S.mesh.connected:
        try:
            await S.mesh.set_channel(old["slot"], "", "")      # oude plaats vrijmaken
        except Exception:  # noqa: BLE001
            pass
    issues = await sync_channels()
    audit(p, "kanaal gewijzigd", c["name"])
    return {"id": cid, "issues": issues}


@app.delete("/api/channels/{cid}")
async def delete_channel(cid: int, request: Request):
    p = need(request, "system.manage")
    c = S.db.channel(cid)
    if not c:
        raise HTTPException(404, "onbekend kanaal")
    S.db.delete_channel(cid)
    _pcache.clear()                    # trackers van dit kanaal hebben geen kanaal meer
    await S.hub.send({"type": "channels"})
    if S.mesh.connected:
        try:
            await S.mesh.set_channel(c["slot"], "", "")
        except Exception as e:  # noqa: BLE001
            log.warning("kanaal %s op de companion wissen: %s", c["name"], e)
    audit(p, "kanaal verwijderd", c["name"])
    return {"ok": True}


@app.get("/api/firmware")
async def firmware(request: Request):
    need(request, "trackers.serial")
    f = Path(__file__).resolve().parent.parent / "static" / "firmware" / "firmware.json"
    return json.loads(f.read_text(encoding="utf-8")) if f.exists() else {"releases": [], "latest": None}


def _visible(request: Request, tid: int, perm: str = "map.view") -> tuple[Principal, dict[str, Any]]:
    p = need(request, perm)
    t = S.db.tracker(tid)
    if not t or not p.sees(tid):
        raise HTTPException(404, "onbekende tracker")
    return p, t


@app.get("/api/trackers/{tid}/track")
async def track(tid: int, request: Request, hours: float = 24):
    p, _ = _visible(request, tid, "map.tracks")
    h = p.max_hours(min(max(hours, 0.1), 24 * 90))
    rows = S.db.track(tid, int(time.time() - h * 3600))
    if not p.can("map.details"):
        rows = [{k: v for k, v in r.items() if k not in ("snr", "path_len", "seq")} for r in rows]
    return rows


@app.get("/api/trackers/{tid}/export")
async def export_track(tid: int, request: Request, hours: float = 24, fmt: str = "gpx"):
    p, t = _visible(request, tid, "export")
    h = p.max_hours(min(max(hours, 0.1), 24 * 90))
    rows = S.db.track(tid, int(time.time() - h * 3600))
    safe = re.sub(r"[^A-Za-z0-9_-]+", "_", t["alias"]) or "tracker"
    audit(p, "spoor geëxporteerd", f"{t['alias']} {h:g} u {fmt}")
    if fmt == "csv":
        lines = ["tijd_utc,lat,lon,hoogte_m,snelheid_kmh,koers,batterij,toestand"]
        for r in rows:
            ts = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(r["ts"]))
            lines.append(f"{ts},{r['lat']},{r['lon']},{r['alt'] or ''},{r['spd'] or ''},{r['crs'] or ''},"
                         f"{r['bat'] if r['bat'] is not None else ''},{r['state']}")
        return Response("\n".join(lines) + "\n", media_type="text/csv",
                        headers={"Content-Disposition": f'attachment; filename="{safe}.csv"'})
    from xml.sax.saxutils import escape
    pts = "\n".join(
        f'      <trkpt lat="{r["lat"]}" lon="{r["lon"]}">'
        + (f'<ele>{r["alt"]}</ele>' if r["alt"] is not None else "")
        + f'<time>{time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(r["ts"]))}</time>'
        + (f'<extensions><speed>{(r["spd"] or 0) / 3.6:.2f}</speed></extensions>' if r["spd"] is not None else "")
        + "</trkpt>" for r in rows)
    gpx = (f'<?xml version="1.0" encoding="UTF-8"?>\n<gpx version="1.1" creator="MeshTrack {VERSION}" '
           f'xmlns="http://www.topografix.com/GPX/1/1">\n  <trk>\n    <name>{escape(t["alias"])}</name>\n'
           f'    <trkseg>\n{pts}\n    </trkseg>\n  </trk>\n</gpx>\n')
    return Response(gpx, media_type="application/gpx+xml",
                    headers={"Content-Disposition": f'attachment; filename="{safe}.gpx"'})


class PurgeIn(BaseModel):
    older_than_days: float = Field(ge=0, le=3650)   # 0 = alles


@app.get("/api/trackers/{tid}/data")
async def tracker_data(tid: int, request: Request):
    t = S.db.tracker(tid)
    if not t:
        raise HTTPException(404, "onbekende tracker")
    need(request, "trackers.manage", "sims.manage" if t["kind"] == "sim" else "trackers.manage")
    return S.db.position_stats(tid)


@app.post("/api/trackers/{tid}/purge")
async def purge_tracker(tid: int, b: PurgeIn, request: Request):
    t = S.db.tracker(tid)
    if not t:
        raise HTTPException(404, "onbekende tracker")
    p = need(request, "trackers.manage", "sims.manage" if t["kind"] == "sim" else "trackers.manage")
    cutoff = None if b.older_than_days == 0 else int(time.time() - b.older_than_days * 86400)
    n = S.db.purge_positions(tid, cutoff)
    audit(p, "trackergegevens gewist", f"{t['alias']}: {n} posities "
          + ("(alles)" if cutoff is None else f"ouder dan {b.older_than_days:g} dagen"))
    await S.hub.send({"type": "tracker", "tracker": tracker_out(S.db.tracker(tid))})
    return {"deleted": n, **S.db.position_stats(tid)}


@app.get("/api/companion/contacts")
async def companion_contacts(request: Request):
    need(request, "companion.view", "trackers.manage", "alerts.manage", "alerts.personal")
    try:
        return await S.mesh.contacts()
    except ConnectionError as e:
        raise HTTPException(503, str(e))


# ---- simulator -----------------------------------------------------------------

class SimIn(BaseModel):
    alias: Optional[str] = Field(None, max_length=40)
    color: Optional[str] = None
    icon: Optional[str] = Field(None, max_length=40)
    profile: Optional[str] = None
    home_lat: Optional[float] = None
    home_lon: Optional[float] = None
    params: Optional[dict] = None
    loss_pct: Optional[int] = Field(None, ge=0, le=90)
    batt_speed: Optional[float] = Field(None, ge=1, le=100)
    running: Optional[bool] = None
    drive: Optional[dict] = None


def _drive(d: Optional[dict]) -> Optional[dict]:
    """Rijgedrag controleren: speed_pct 30..200, max_kmh 0..200, trip 0.3..80 km,
    roam bool, speeds {wegtype: km/u 3..200}."""
    if d is None:
        return None
    out: dict = {}
    try:
        if d.get("speed_pct") is not None:
            out["speed_pct"] = min(200.0, max(30.0, float(d["speed_pct"])))
        if d.get("max_kmh") is not None:
            out["max_kmh"] = min(200.0, max(0.0, float(d["max_kmh"])))
        lo = float(d["trip_min_km"]) if d.get("trip_min_km") else None
        hi = float(d["trip_max_km"]) if d.get("trip_max_km") else None
        if lo is not None:
            out["trip_min_km"] = min(400.0, max(0.3, lo))
        if hi is not None:
            out["trip_max_km"] = min(400.0, max(out.get("trip_min_km", 0.3), hi))
        out["roam"] = bool(d.get("roam"))
        sp = {}
        for k, v in (d.get("speeds") or {}).items():
            if v not in (None, "", 0) and isinstance(k, str) and len(k) < 30:
                sp[k] = min(200.0, max(3.0, float(v)))
        if sp:
            out["speeds"] = sp
    except (TypeError, ValueError):
        raise HTTPException(422, "ongeldig rijgedrag")
    return out


def _sim_out(row: dict[str, Any]) -> dict[str, Any]:
    st = S.sims.status(row["tracker_id"]) or {"running": False}
    if row["tracker_id"] in _building:
        st = {**st, "note": "historiek wordt opgebouwd", "building": True}
    return {**row, "status": st}


@app.get("/api/sims")
async def list_sims(request: Request):
    p = need(request, "sims.manage")
    return [_sim_out(r) for r in S.db.sims() if p.sees(r["tracker_id"])]


@app.get("/api/sims/defaults")
async def sim_defaults(request: Request):
    """Standaardsnelheden per wegtype en ritlengtes, voor het formulier."""
    need(request, "sims.manage")
    from .roadgraph import SPEEDS
    from .sim import PROFILE
    return {p: {"speeds": SPEEDS[p], "trip": PROFILE[p]["trip"]} for p in SPEEDS}


@app.get("/api/sims/routes")
async def sim_routes(request: Request):
    p = need(request, "map.view")
    if not p.can("sims.manage"):
        return {}
    return {tid: r for tid, r in S.sims.routes().items() if p.sees(tid)}


@app.post("/api/sims")
async def create_sim(b: SimIn, request: Request):
    p = need(request, "sims.manage")
    if not b.alias or not b.alias.strip():
        raise HTTPException(422, "alias is verplicht")
    if b.profile not in (None, "car", "bike", "walk", "travel"):
        raise HTTPException(422, "profiel: car, bike, walk of travel")
    if b.color and not COLOR.match(b.color):
        raise HTTPException(422, "kleur moet #rrggbb zijn")
    lat = b.home_lat if b.home_lat is not None else S.cfg.map_center[1]
    lon = b.home_lon if b.home_lon is not None else S.cfg.map_center[0]
    w, s_, e, n = S.cfg.region_bbox
    if not (w <= lon <= e and s_ <= lat <= n):
        raise HTTPException(422, "thuisbasis ligt buiten het kaartgebied")
    tid = S.db.add_tracker(new_pubkey(), b.alias.strip(), b.color or "#7c3aed", b.icon or "", "simulator",
                           True, kind="sim")
    S.db.save_sim(tid, b.profile or "car", lat, lon, b.params or {}, 5 if b.loss_pct is None else b.loss_pct,
                  4 if b.batt_speed is None else b.batt_speed, b.running is not False, _drive(b.drive) or {})
    row = S.db.sim(tid)
    if S.sims.router_for(row["profile"]) is None:
        S.db.set_sim_running(tid, False)
        raise HTTPException(503, "geen kaarttegels: simulator kan niet routeren")
    days = float(S.settings.get("sim_history_days", 0) or 0)
    asyncio.create_task(_history_then_start(tid, days, bool(row["running"])))
    audit(p, "simulator aangemaakt", b.alias.strip())
    await S.hub.send({"type": "tracker", "tracker": tracker_out(S.db.tracker(tid))})
    return _sim_out(S.db.sim(tid))


@app.put("/api/sims/{tid}")
async def update_sim(tid: int, b: SimIn, request: Request):
    p = need(request, "sims.manage")
    row = S.db.sim(tid)
    if not row or not p.sees(tid):
        raise HTTPException(404, "onbekende simulator")
    if b.profile not in (None, "car", "bike", "walk", "travel"):
        raise HTTPException(422, "profiel: car, bike, walk of travel")
    if b.color and not COLOR.match(b.color):
        raise HTTPException(422, "kleur moet #rrggbb zijn")
    S.db.update_tracker(tid, alias=b.alias.strip() if b.alias else None, color=b.color, icon=b.icon)
    S.db.save_sim(tid, b.profile or row["profile"],
                  row["home_lat"] if b.home_lat is None else b.home_lat,
                  row["home_lon"] if b.home_lon is None else b.home_lon,
                  row["params"] if b.params is None else b.params,
                  row["loss_pct"] if b.loss_pct is None else b.loss_pct,
                  row["batt_speed"] if b.batt_speed is None else b.batt_speed,
                  row["running"] if b.running is None else b.running,
                  row["drive"] if b.drive is None else _drive(b.drive))
    row = S.db.sim(tid)
    if row["running"]:
        await S.sims.start(row, S.db.tracker(tid))     # herstart met nieuwe instellingen
    else:
        await S.sims.stop(tid)
    audit(p, "simulator gewijzigd", f"{row['alias']} ({'rijdt' if row['running'] else 'gestopt'})")
    await S.hub.send({"type": "tracker", "tracker": tracker_out(S.db.tracker(tid))})
    return _sim_out(row)


_building: set[int] = set()     # simulators waarvan de historiek nu opgebouwd wordt


async def _history_then_start(tid: int, days: float, start: bool) -> None:
    """Op de achtergrond: eerst de historiek (virtuele tijd), dan in echte tijd starten.
    Zo overschrijven oude virtuele posities nooit de live-positie."""
    _building.add(tid)
    try:
        if days > 0:
            n = await asyncio.to_thread(backfill_sim, tid, days)
            log.info("simulator %s: historiek van %g dagen, %d posities", tid, days, n)
        row = S.db.sim(tid)
        if row and start:
            await S.sims.start(row, S.db.tracker(tid))
        t = S.db.tracker(tid)
        if t:
            await S.hub.send({"type": "tracker", "tracker": tracker_out(t)})
    except Exception:  # noqa: BLE001
        log.exception("historiek simulator %s", tid)
    finally:
        _building.discard(tid)


@app.post("/api/sims/{tid}/history")
async def regenerate_history(tid: int, request: Request, days: float = 7):
    """Historiek opnieuw opbouwen: bestaande posities van deze simulator wissen en
    `days` dagen in virtuele tijd opnieuw rijden."""
    p = need(request, "sims.manage")
    row = S.db.sim(tid)
    if not row or not p.sees(tid):
        raise HTTPException(404, "onbekende simulator")
    if tid in _building:
        raise HTTPException(409, "de historiek wordt al opgebouwd")
    days = min(max(days, 0.1), 30)
    running = tid in S.sims.sims
    await S.sims.stop(tid)
    S.db.purge_positions(tid)
    asyncio.create_task(_history_then_start(tid, days, running))
    audit(p, "simulatorhistoriek opgebouwd", f"{row['alias']}: {days:g} dagen")
    return {"started": True}


# ---- geofences -----------------------------------------------------------------

def _zone_visible(p: Principal, g: dict[str, Any]) -> bool:
    return g.get("owner") is None or g.get("owner") == p.user_id


def _zone_editable(p: Principal, g: dict[str, Any]) -> bool:
    if g.get("owner") is None:
        return p.can("zones.manage")
    return g["owner"] == p.user_id


@app.get("/api/geofences")
async def list_geofences(request: Request):
    p = need(request, "zones.view")
    return [{**g, "mine": g.get("owner") == p.user_id and p.user_id is not None, "editable": _zone_editable(p, g)}
            for g in S.db.geofences() if _zone_visible(p, g)]


@app.post("/api/geofences")
async def create_geofence(body: dict, request: Request):
    p = need(request, "zones.view")
    personal = bool(body.get("personal")) or not p.can("zones.manage")
    if personal and p.kind != "user":
        raise HTTPException(403, "geen toegang")
    try:
        g = geofence.validate(body)
    except ValueError as e:
        raise HTTPException(422, str(e))
    gid = S.db.add_geofence(g, p.user_id if personal else None)
    audit(p, "eigen zone aangemaakt" if personal else "zone aangemaakt", g["name"])
    await S.hub.send({"type": "geofences"})
    return S.db.geofence(gid)


@app.put("/api/geofences/{gid}")
async def update_geofence(gid: int, body: dict, request: Request):
    p = need(request, "zones.view")
    old = S.db.geofence(gid)
    if not old or not _zone_visible(p, old):
        raise HTTPException(404, "onbekende zone")
    if not _zone_editable(p, old):
        raise HTTPException(403, "geen toegang")
    try:
        g = geofence.validate(body)
    except ValueError as e:
        raise HTTPException(422, str(e))
    S.db.update_geofence(gid, g)
    audit(p, "zone gewijzigd", g["name"])
    await S.hub.send({"type": "geofences"})
    return S.db.geofence(gid)


@app.delete("/api/geofences/{gid}")
async def delete_geofence(gid: int, request: Request):
    p = need(request, "zones.view")
    g = S.db.geofence(gid)
    if not g or not _zone_editable(p, g):
        raise HTTPException(403, "geen toegang")
    S.db.delete_geofence(gid)
    audit(p, "zone verwijderd", g["name"] if g else str(gid))
    await S.hub.send({"type": "geofences"})
    return {"ok": True}


@app.get("/api/geofence-events")
async def geofence_events(request: Request, limit: int = 50):
    p = need(request, "zones.view")
    return [e for e in S.db.geofence_events(min(max(limit, 1), 500))
            if p.sees(e["tracker_id"]) and (e["owner"] is None or e["owner"] == p.user_id)]


@app.get("/api/mesh/nodes")
async def mesh_nodes(request: Request):
    p = need(request, "map.nodes")
    try:
        comp = await S.mesh.nodes()
    except ConnectionError:
        comp = []
    out = await asyncio.to_thread(nodes.merged, S.cfg.openhop_db, comp)
    if not p.can("map.details"):
        out = [{k: v for k, v in n.items() if k not in ("key", "rssi", "snr")} for n in out]
    return out


EVENT_TYPES = {
    "M": "positie (beweging)", "W": "wakker door beweging", "S": "stilgevallen", "H": "heartbeat",
    "N": "geen GPS-fix", "E": "SOS", "P": "handmatig verstuurd", "B": "moduswissel",
    "L": "gelogd punt (SlowTrack)", "Q": "ingehaald punt (FIFO)",
    "zone_in": "zone binnen", "zone_out": "zone buiten", "bat_low": "batterij onder 20 %",
    "suspect": "verdachte positie", "usb_on": "aan de lader (USB)", "usb_off": "van de lader af",
    "lost_seen": "verloren tracker gezien",
}
# NB: "te lang stil" is geen positie maar een meldingsgebeurtenis; die staat in de verzonden meldingen.


@app.get("/api/events")
async def events(request: Request, tracker: str = "", types: str = "", hours: float = 24, since: int = 0,
                 until: int = 0, limit: int = 500):
    p = need(request, "log.view")
    now = int(time.time())
    until = until or now
    since = since or int(until - p.max_hours(min(max(hours, 0.1), 24 * 365)) * 3600)
    if p.history_hours > 0:
        since = max(since, now - p.history_hours * 3600)
    wanted = [x for x in types.split(",") if x in EVENT_TYPES] if types else list(EVENT_TYPES)
    ids = [int(x) for x in tracker.split(",") if x.isdigit()] if tracker else None
    if p.tracker_ids is not None:
        ids = [i for i in (ids if ids is not None else p.tracker_ids) if p.sees(i)]
    if not p.can("zones.view"):
        wanted = [w for w in wanted if not w.startswith("zone")]
    rows = S.db.events(ids, [w for w in wanted if len(w) == 1], since, until, "zone_in" in wanted or "zone_out" in wanted,
                       "bat_low" in wanted, "suspect" in wanted, min(max(limit, 1), 5000),
                       "usb_on" in wanted or "usb_off" in wanted)
    rows = [r for r in rows if not r["type"].startswith("usb") or r["type"] in wanted]
    if "lost_seen" in wanted:
        rows += _lost_events(ids, since, until)
        rows.sort(key=lambda r: r["ts"], reverse=True)
    if "zone_in" not in wanted:
        rows = [r for r in rows if r["type"] != "zone_in"]
    if "zone_out" not in wanted:
        rows = [r for r in rows if r["type"] != "zone_out"]
    if not p.can("map.details"):
        rows = [{k: v for k, v in r.items() if k not in ("snr", "path_len", "hdop")} for r in rows]
    return {"types": EVENT_TYPES, "since": since, "until": until, "events": rows}


def _lost_events(ids: Optional[list[int]], since: int, until: int) -> list[dict[str, Any]]:
    """Berichten van trackers die nu als verloren gemarkeerd zijn, sinds die markering."""
    out = []
    for t in S.db.trackers():
        if not t.get("lost") or (ids is not None and t["id"] not in ids):
            continue
        for r in S.db.track(t["id"], max(since, t["lost_since"] or 0), 500):
            if r["ts"] <= until:
                out.append({**r, "type": "lost_seen", "tracker_id": t["id"], "alias": t["alias"]})
    return out


@app.get("/log")
async def log_page():
    return page("log.html")


@app.get("/api/unknown")
async def unknown(request: Request):
    need(request, "trackers.manage")
    return S.db.unknown()


# ---- gebruikers, groepen, deellinks, audit -------------------------------------

class GroupIn(BaseModel):
    name: str = Field(min_length=1, max_length=40)
    description: str = Field("", max_length=200)
    perms: list[str]
    all_trackers: bool = True
    trackers: list[int] = []
    channels: dict[int, str] = {}       # kanaal-id -> "kaart" | "sleutel"
    history_hours: int = Field(0, ge=0, le=24 * 365)


def _group_body(b: GroupIn) -> dict[str, Any]:
    known = {c["id"] for c in S.db.channels()}
    return {**b.model_dump(), "name": b.name.strip(), "perms": [x for x in b.perms if x in PERMS],
            "channels": {k: v for k, v in b.channels.items() if k in known and v in rbac.LEVELS}}


def _group_audit(name: str, body: dict[str, Any], old: Optional[dict[str, Any]] = None) -> str:
    """Auditregel van een groep: rechten, kanaalrechten en (bij wijzigen) wat er aan de kanalen veranderde."""
    cname = {c["id"]: c["name"] for c in S.db.channels()}
    chans = {int(k): v for k, v in (body.get("channels") or {}).items()}
    parts = [f"rechten: {', '.join(body['perms']) or 'geen'}",
             "kanalen: " + (", ".join(f"{cname.get(k, k)} = {v}" for k, v in sorted(chans.items())) or "geen")
             + (" (+ alle kanalen: kaart)" if body.get("all_trackers") else "")]
    if old is not None:
        before = {int(k): v for k, v in (old.get("channels") or {}).items()}
        diff = [f"{cname.get(k, k)}: {before.get(k, 'geen')} → {chans.get(k, 'geen')}"
                for k in sorted(set(before) | set(chans)) if before.get(k) != chans.get(k)]
        if bool(old.get("all_trackers")) != bool(body.get("all_trackers")):
            diff.append(f"alle kanalen: {'aan' if body.get('all_trackers') else 'uit'}")
        if diff:
            parts.append("gewijzigd: " + "; ".join(diff))
    return f"{name}: " + " · ".join(parts)


def _admins_left(excluding_user: Optional[int] = None, group_override: Optional[tuple[int, list[str]]] = None) -> int:
    """Aantal actieve gebruikers met users.manage (om buitensluiten te vermijden)."""
    groups = {g["id"]: g for g in S.db.groups()}
    if group_override:
        groups[group_override[0]] = {**groups[group_override[0]], "perms": group_override[1]}
    n = 0
    for u in S.db.users():
        if u["id"] == excluding_user or not u["active"]:
            continue
        if any("users.manage" in groups.get(g, {}).get("perms", []) for g in u["group_ids"]):
            n += 1
    return n


def _admin_groups(ids: list[int]) -> bool:
    return any("users.manage" in (S.db.group(g) or {}).get("perms", []) for g in ids)


@app.get("/api/groups")
async def list_groups(request: Request):
    need(request, "users.manage", "share.manage")
    return {"groups": S.db.groups(), "perms": [{"id": k, "label": v[0], "help": v[1]} for k, v in PERMS.items()],
            "channels": [{"id": c["id"], "name": c["name"], "active": c["active"]} for c in S.db.channels()],
            "levels": list(rbac.LEVELS)}


@app.post("/api/groups")
async def create_group(b: GroupIn, request: Request):
    p = need(request, "users.manage")
    if any(g["name"].lower() == b.name.strip().lower() for g in S.db.groups()):
        raise HTTPException(409, "die groep bestaat al")
    body = _group_body(b)
    gid = S.db.save_group(None, body)
    _pcache.clear()
    audit(p, "groep aangemaakt", _group_audit(body["name"], body))
    return S.db.group(gid)


@app.put("/api/groups/{gid}")
async def update_group(gid: int, b: GroupIn, request: Request):
    p = need(request, "users.manage")
    old = S.db.group(gid)
    if not old:
        raise HTTPException(404, "onbekende groep")
    body = _group_body(b)
    perms = body["perms"]
    if _admins_left(group_override=(gid, perms)) == 0:
        raise HTTPException(409, "dan heeft niemand nog gebruikersbeheer; dat kan niet")
    S.db.save_group(gid, body)
    _pcache.clear()
    audit(p, "groep gewijzigd", _group_audit(body["name"], body, old))
    return S.db.group(gid)


@app.delete("/api/groups/{gid}")
async def delete_group(gid: int, request: Request):
    p = need(request, "users.manage")
    g = S.db.group(gid)
    if not g:
        raise HTTPException(404, "onbekende groep")
    if g["members"]:
        raise HTTPException(409, "de groep heeft nog leden")
    S.db.delete_group(gid)
    audit(p, "groep verwijderd", g["name"])
    return {"ok": True}


class UserIn(BaseModel):
    username: Optional[str] = None
    display_name: Optional[str] = Field(None, max_length=60)
    password: Optional[str] = Field(None, max_length=200)
    group_id: Optional[int] = None          # oud: één groep
    group_ids: Optional[list[int]] = None   # 0.5: één of meer groepen
    active: Optional[bool] = None

    def ids(self) -> Optional[list[int]]:
        if self.group_ids is not None:
            return list(dict.fromkeys(self.group_ids))
        return [self.group_id] if self.group_id is not None else None


@app.get("/api/users")
async def list_users(request: Request):
    need(request, "users.manage")
    return S.db.users()


@app.post("/api/users")
async def create_user(b: UserIn, request: Request):
    p = need(request, "users.manage")
    if not b.username or not USERNAME.match(b.username):
        raise HTTPException(422, "gebruikersnaam: 2-32 tekens, letters, cijfers, . _ -")
    if S.db.user_by_name(b.username):
        raise HTTPException(409, "die gebruiker bestaat al")
    gids = b.ids() or []
    if not gids or not all(S.db.group(g) for g in gids):
        raise HTTPException(422, "kies minstens één groep")
    problem = auth.password_problem(b.password or "")
    if problem:
        raise HTTPException(422, f"wachtwoord: {problem}")
    uid = S.db.add_user(b.username, (b.display_name or "").strip(), auth.hash_password(b.password), gids,
                        b.active is not False)
    audit(p, "gebruiker aangemaakt", f"{b.username} in {', '.join(S.db.group(g)['name'] for g in gids)}")
    return next(u for u in S.db.users() if u["id"] == uid)


@app.put("/api/users/{uid}")
async def update_user(uid: int, b: UserIn, request: Request):
    p = need(request, "users.manage")
    u = S.db.user(uid)
    if not u:
        raise HTTPException(404, "onbekende gebruiker")
    gids = b.ids()
    if gids is not None and (not gids or not all(S.db.group(g) for g in gids)):
        raise HTTPException(422, "kies minstens één bestaande groep")
    losing = (b.active is False) or (gids is not None and not _admin_groups(gids))
    if losing and _admins_left(excluding_user=uid) == 0:
        raise HTTPException(409, "dit is de laatste beheerder; dat kan niet")
    pw_hash = None
    if b.password:
        problem = auth.password_problem(b.password)
        if problem:
            raise HTTPException(422, f"wachtwoord: {problem}")
        pw_hash = auth.hash_password(b.password)
    S.db.update_user(uid, display_name=b.display_name, active=b.active, password_hash=pw_hash)
    if gids is not None:
        S.db.set_user_groups(uid, gids)
    _pcache.clear()
    what = [x for x, v in (("naam", b.display_name), ("groepen", gids), ("actief", b.active),
                           ("wachtwoord", b.password)) if v is not None]
    audit(p, "gebruiker gewijzigd", f"{u['username']}: {', '.join(what)}")
    return next(x for x in S.db.users() if x["id"] == uid)


@app.get("/api/users/{uid}/effective")
async def effective_rights(uid: int, request: Request):
    """Wat een gebruiker echt mag en ziet, met de herkomst (welke groep, welk kanaal)."""
    need(request, "users.manage")
    u = S.db.user(uid)
    if not u:
        raise HTTPException(404, "onbekende gebruiker")
    gids = set(S.db.user_group_ids(uid))
    groups = [g for g in S.db.groups() if g["id"] in gids]
    p = rbac.principal_for_user(u, groups, S.db.channel_members()) if groups else None
    perms = [{"id": k, "label": v[0], "via": [g["name"] for g in groups if k in g["perms"]]}
             for k, v in PERMS.items()]
    chans = S.db.channels()
    cname = {c["id"]: c["name"] for c in chans}
    channels = []
    for c in chans:
        via = [f"{g['name']}: {g['channels'][c['id']]}" for g in groups if c["id"] in g["channels"]]
        via += [f"{g['name']}: alle kanalen (kaart)" for g in groups if g["all_trackers"]]
        if "system.manage" in (p.perms if p else set()):
            via.append("beheerder: alle sleutels")
        channels.append({"id": c["id"], "name": c["name"], "level": p.channel_level(c["id"]) if p else None, "via": via})
    trackers = []
    for t in S.db.trackers():
        via = []
        for g in groups:
            if g["all_trackers"]:
                via.append(f"{g['name']}: alle kanalen")
                continue
            if t["id"] in g["trackers"]:
                via.append(f"{g['name']}: losse tracker")
            if t.get("channel_id") in g["channels"]:
                via.append(f"{g['name']}: kanaal {cname.get(t['channel_id'], t['channel_id'])}")
        trackers.append({"id": t["id"], "alias": t["alias"], "kind": t["kind"], "color": t["color"],
                         "channel": cname.get(t.get("channel_id")),
                         "active": bool(t["active"]), "sees": bool(p and p.sees(t["id"])), "via": via})
    hist_src = [g["name"] for g in groups if int(g["history_hours"] or 0) == (p.history_hours if p else -1)]
    rules = [r["name"] for r in S.db.alert_rules() if r.get("owner") == uid]
    return {
        "user": {"id": u["id"], "username": u["username"], "display_name": u["display_name"], "active": bool(u["active"])},
        "groups": [g["name"] for g in groups],
        "perms": perms,
        "all_trackers": bool(p and p.tracker_ids is None),
        "channels": channels,
        "trackers": trackers,
        "history_hours": p.history_hours if p else None,
        "history_via": hist_src,
        "own_rules": rules,
        "warnings": ([] if u["active"] else ["Deze gebruiker is gedeactiveerd en kan niet inloggen."])
                    + ([] if groups else ["Deze gebruiker zit in geen enkele groep en ziet niets."]),
    }


@app.delete("/api/users/{uid}")
async def delete_user(uid: int, request: Request):
    p = need(request, "users.manage")
    u = S.db.user(uid)
    if not u:
        raise HTTPException(404, "onbekende gebruiker")
    if p.user_id == uid:
        raise HTTPException(409, "je kan jezelf niet verwijderen")
    if _admins_left(excluding_user=uid) == 0:
        raise HTTPException(409, "dit is de laatste beheerder; dat kan niet")
    S.db.delete_user(uid)
    _pcache.clear()
    audit(p, "gebruiker verwijderd", u["username"])
    return {"ok": True}


class ShareIn(BaseModel):
    name: str = Field(min_length=1, max_length=60)
    trackers: list[int] = []
    channels: list[int] = []
    hours: int = Field(12, ge=1, le=24 * 30)
    sidebar: bool = False
    valid_hours: int = Field(24, ge=0, le=24 * 365)   # 0 = nooit verlopen


@app.get("/api/shares")
async def list_shares(request: Request):
    p = need(request, "share.manage")
    base = str(request.base_url).rstrip("/")
    return [{**s, "url": f"{base}/s/{s['token']}"} for s in S.db.shares()
            if p.tracker_ids is None or set(s["trackers"]) <= p.tracker_ids]


@app.post("/api/shares")
async def create_share(b: ShareIn, request: Request):
    p = need(request, "share.manage")
    ids = [t for t in b.trackers if S.db.tracker(t) and p.sees(t)]
    chans = [c for c in dict.fromkeys(b.channels) if S.db.channel(c) and p.sees_channel(c)]
    if not ids and not chans:
        raise HTTPException(422, "kies minstens één kanaal of tracker")
    if chans and p.kind != "user":
        raise HTTPException(403, "geen toegang")
    token = secrets.token_urlsafe(18)
    expires = int(time.time() + b.valid_hours * 3600) if b.valid_hours else None
    S.db.add_share(token, b.name.strip(), ids, b.hours, b.sidebar, expires, p.name, chans)
    audit(p, "deellink gemaakt", f"{b.name} ({len(ids)} trackers, {len(chans)} kanalen, "
                                 f"{b.valid_hours or 'onbeperkt'} u geldig)")
    base = str(request.base_url).rstrip("/")
    return {"url": f"{base}/s/{token}"}


@app.delete("/api/shares/{sid}")
async def delete_share(sid: int, request: Request):
    p = need(request, "share.manage")
    s = next((x for x in S.db.shares() if x["id"] == sid), None)
    if not s:
        raise HTTPException(404, "onbekende deellink")
    S.db.delete_share(sid)
    _pcache.clear()
    audit(p, "deellink ingetrokken", s["name"])
    return {"ok": True}


# ---- systeem: instellingen en meldingsregels --------------------------------------

@app.get("/system")
async def system_page():
    return page("system.html")


@app.get("/api/settings")
async def get_settings_api(request: Request):
    need(request, "system.manage")
    return {"values": S.settings, "spec": setmod.describe()}


@app.put("/api/settings")
async def put_settings(body: dict, request: Request):
    p = need(request, "system.manage")
    try:
        changes = setmod.validate(body)
    except ValueError as e:
        raise HTTPException(422, str(e))
    for k, v in changes.items():
        S.db.set_setting(k, v)
    reload_settings()
    audit(p, "systeeminstellingen gewijzigd", ", ".join(f"{k}={v}" for k, v in changes.items()))
    return {"values": S.settings}


class RuleIn(BaseModel):
    name: str = Field(min_length=1, max_length=60)
    active: bool = True
    events: list[str]
    trackers: list[int] = []
    channels: list[int] = []
    recipients: list[dict]
    cooldown_s: int = Field(900, ge=0, le=7 * 86400)


class RuleCreate(RuleIn):
    personal: bool = False


def _rule(b: RuleIn) -> dict[str, Any]:
    ev = [e for e in b.events if e in EVENTS]
    if not ev:
        raise HTTPException(422, "kies minstens één gebeurtenis")
    rc = []
    for r in b.recipients:
        pk = str(r.get("pubkey", "")).strip().lower()
        if not HEX64.match(pk):
            raise HTTPException(422, "ontvanger: pubkey moet 64 hex-tekens zijn")
        rc.append({"pubkey": pk, "name": str(r.get("name", ""))[:40]})
    if not rc:
        raise HTTPException(422, "kies minstens één ontvanger")
    if len(rc) > 25:
        raise HTTPException(422, "maximaal 25 ontvangers per regel")
    d = b.model_dump()
    d.pop("personal", None)
    known = {c["id"] for c in S.db.channels()}
    return {**d, "events": ev, "recipients": rc, "channels": [c for c in dict.fromkeys(b.channels) if c in known]}


def _rule_access(p: Principal, r: dict[str, Any]) -> bool:
    if r.get("owner") is None:
        return p.can("alerts.manage")
    return r["owner"] == p.user_id and p.can("alerts.personal")


def _get_rule(p: Principal, rid: int) -> dict[str, Any]:
    r = next((x for x in S.db.alert_rules() if x["id"] == rid), None)
    if not r or not _rule_access(p, r):
        raise HTTPException(404, "onbekende regel")
    return r


@app.get("/api/alerts")
async def list_alerts(request: Request):
    p = need(request, "alerts.manage", "alerts.personal")
    rules = [{**r, "mine": r.get("owner") is not None} for r in S.db.alert_rules() if _rule_access(p, r)]
    return {"rules": rules, "events": EVENT_TEXT, "queue": S.alerts.status(),
            "can_shared": p.can("alerts.manage"), "can_personal": p.can("alerts.personal") and p.kind == "user"}


@app.post("/api/alerts")
async def create_alert(b: RuleCreate, request: Request):
    p = need(request, "alerts.manage", "alerts.personal")
    personal = b.personal or not p.can("alerts.manage")
    if personal and not (p.can("alerts.personal") and p.kind == "user"):
        raise HTTPException(403, "geen toegang")
    rid = S.db.save_alert_rule(None, _rule(b), p.user_id if personal else None)
    audit(p, "eigen meldingsregel aangemaakt" if personal else "meldingsregel aangemaakt", b.name)
    return {"id": rid}


@app.put("/api/alerts/{rid}")
async def update_alert(rid: int, b: RuleCreate, request: Request):
    p = need(request, "alerts.manage", "alerts.personal")
    _get_rule(p, rid)
    S.db.save_alert_rule(rid, _rule(b))
    audit(p, "meldingsregel gewijzigd", b.name)
    return {"id": rid}


@app.delete("/api/alerts/{rid}")
async def delete_alert(rid: int, request: Request):
    p = need(request, "alerts.manage", "alerts.personal")
    r = _get_rule(p, rid)
    S.db.delete_alert_rule(rid)
    audit(p, "meldingsregel verwijderd", r["name"])
    return {"ok": True}


@app.post("/api/alerts/{rid}/test")
async def test_alert(rid: int, request: Request):
    """Testbericht naar alle ontvangers van de regel (via dezelfde wachtrij)."""
    p = need(request, "alerts.manage", "alerts.personal")
    r = _get_rule(p, rid)
    from .alerts import Job
    text = f"MeshTrack: test van melding '{r['name']}' door {p.display}"
    for rc in r["recipients"]:
        lid = S.db.add_alert_log(r["name"], "(test)", "test", rc.get("name") or rc["pubkey"][:8], text)
        S.alerts.queue.put_nowait(Job(lid, rc["pubkey"], text))
    audit(p, "testmelding", r["name"])
    return {"queued": len(r["recipients"])}


@app.get("/api/alerts/log")
async def alerts_log(request: Request, limit: int = 200):
    need(request, "alerts.manage", "system.manage", "alerts.personal")
    return {"log": S.db.alert_log(min(max(limit, 1), 1000)), "queue": S.alerts.status()}


@app.get("/api/audit")
async def audit_log(request: Request, limit: int = 200):
    need(request, "users.manage")
    return S.db.audit_log(min(max(limit, 1), 1000))


# ---- live ------------------------------------------------------------------------

@app.websocket("/ws")
async def ws(websocket: WebSocket):
    p = _resolve(websocket.cookies)
    if p is None or not p.can("map.view"):
        await websocket.close(code=4401)
        return
    await websocket.accept()
    S.hub.clients[websocket] = dict(websocket.cookies)
    try:
        mesh = S.mesh.status()
        if not p.can("companion.view"):
            mesh = {"connected": mesh["connected"], "name": mesh["name"]}
        await websocket.send_text(json.dumps({"type": "mesh", "mesh": mesh}))
        while True:
            await websocket.receive_text()   # alleen om disconnects te zien
    except WebSocketDisconnect:
        pass
    finally:
        S.hub.clients.pop(websocket, None)


def main() -> None:
    import uvicorn
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    cfg = config.load()
    uvicorn.run("meshtrack.main:app", host=cfg.http_host, port=cfg.http_port, proxy_headers=True,
                forwarded_allow_ips="127.0.0.1", log_level="info")


if __name__ == "__main__":
    main()
