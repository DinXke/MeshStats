"""Meldingen: bij gebeurtenissen een DM via de mesh naar gekozen ontvangers.

Gebeurtenissen: de toestand van een bericht (W, S, E, N, P, B, H, M), zones
(zone_in/zone_out), batterij onder 20 % (bat_low) en "te lang stil" (silent).
Een regel kiest gebeurtenissen, trackers en ontvangers, met een cooldown per
tracker en gebeurtenis.

Verzenden gebeurt achter elkaar uit één wachtrij, met een instelbare pauze
tussen DM's: veel ontvangers mogen de mesh niet overspoelen. Per DM
`alert_attempts` pogingen (de laatste via flood); lukt dat niet, dan tot
`alert_rounds` rondes later opnieuw.
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Any, Callable, Optional

from meshcore import EventType

log = logging.getLogger("meshtrack.alerts")

EVENT_TEXT = {
    "W": "is wakker geworden door beweging", "S": "is stilgevallen", "E": "stuurt SOS",
    "N": "heeft geen GPS-fix", "P": "stuurde handmatig een positie", "B": "wisselde van modus",
    "H": "stuurde een heartbeat", "M": "beweegt", "zone_in": "kwam binnen in", "zone_out": "verliet",
    "bat_low": "heeft minder dan 20 % batterij", "silent": "is te lang stil",
}
EVENTS = list(EVENT_TEXT)


@dataclass
class Job:
    log_id: int
    pubkey: str
    text: str
    round: int = 1
    not_before: float = 0.0


class AlertManager:
    def __init__(self, db, mesh, get_settings: Callable[[], dict[str, Any]]):
        self.db, self.mesh, self.get_settings = db, mesh, get_settings
        self.queue: asyncio.Queue[Job] = asyncio.Queue()
        self._last: dict[tuple, float] = {}       # (regel, tracker, gebeurtenis) -> tijd
        self._silent_sent: set[int] = set()
        self.sending: Optional[str] = None

    # ---- regels toepassen -------------------------------------------------------

    def fire(self, tracker: dict[str, Any], event: str, pos: Optional[dict[str, Any]] = None,
             zone: Optional[str] = None) -> int:
        """Gebeurtenis melden. Geeft het aantal ingeplande DM's terug."""
        st = self.get_settings()
        if tracker.get("kind") == "sim" and not st["alert_sims"]:
            return 0
        if event != "silent":
            self._silent_sent.discard(tracker["id"])
        now = time.time()
        n = 0
        for r in self.db.alert_rules():
            if not r["active"] or event not in r["events"] or not r["recipients"]:
                continue
            if r["trackers"] and tracker["id"] not in r["trackers"]:
                continue
            key = (r["id"], tracker["id"], event)
            if now - self._last.get(key, 0) < r["cooldown_s"]:
                continue
            self._last[key] = now
            text = self.text(tracker, event, pos, zone)
            for rc in r["recipients"]:
                lid = self.db.add_alert_log(r["name"], tracker["alias"], event, rc.get("name") or rc["pubkey"][:8], text)
                self.queue.put_nowait(Job(lid, rc["pubkey"], text))
                n += 1
        return n

    @staticmethod
    def text(tracker: dict[str, Any], event: str, pos: Optional[dict[str, Any]], zone: Optional[str]) -> str:
        what = EVENT_TEXT.get(event, event)
        if zone:
            what = f"{what} {zone}"
        parts = [f"MeshTrack: {tracker['alias']} {what}"]
        p = pos or {}
        lat, lon = p.get("lat", tracker.get("last_lat")), p.get("lon", tracker.get("last_lon"))
        if lat is not None and lon is not None:
            parts.append(f"@{lat:.5f},{lon:.5f}")
        bat = p.get("bat", tracker.get("last_bat"))
        if bat is not None:
            parts.append(f"B{bat}%")
        parts.append(time.strftime("%H:%M", time.localtime(p.get("ts") or time.time())))
        return " ".join(parts)[:140]

    def check_silent(self, trackers: list[dict[str, Any]]) -> None:
        hours = self.get_settings()["silent_alert_h"]
        if not hours:
            return
        limit = time.time() - hours * 3600
        for t in trackers:
            if not t["active"] or not t["last_rx"] or t["id"] in self._silent_sent:
                continue
            if t["last_rx"] < limit:
                self._silent_sent.add(t["id"])
                self.fire(t, "silent")

    # ---- verzenden --------------------------------------------------------------

    async def run(self) -> None:
        while True:
            job = await self.queue.get()
            wait = job.not_before - time.time()
            if wait > 0:
                # nog niet aan de beurt (volgende ronde): achteraan terugzetten
                await asyncio.sleep(min(wait, 5))
                self.queue.put_nowait(job)
                continue
            st = self.get_settings()
            ok, attempts = await self._send(job, st)
            if ok:
                self.db.finish_alert_log(job.log_id, "ok", attempts)
            elif job.round < st["alert_rounds"]:
                job.round += 1
                job.not_before = time.time() + st["alert_round_pause_s"]
                self.queue.put_nowait(job)
            else:
                self.db.finish_alert_log(job.log_id, "mislukt", attempts)
            await asyncio.sleep(st["alert_gap_s"])

    async def _send(self, job: Job, st: dict[str, Any]) -> tuple[bool, int]:
        if not self.mesh.connected or not self.mesh.mc:
            return False, 0
        mc = self.mesh.mc
        self.sending = job.pubkey[:8]
        try:
            await self.mesh.ensure_contact(job.pubkey, f"melding-{job.pubkey[:8]}")
            res = await mc.commands.send_msg_with_retry(
                job.pubkey, job.text, max_attempts=st["alert_attempts"],
                max_flood_attempts=1, flood_after=st["alert_flood_after"])
            ok = res is not None and getattr(res, "type", None) != EventType.ERROR
            return ok, st["alert_attempts"] if not ok else 1
        except Exception as e:  # noqa: BLE001
            log.warning("melding naar %s mislukt: %s", job.pubkey[:8], e)
            return False, st["alert_attempts"]
        finally:
            self.sending = None

    def status(self) -> dict[str, Any]:
        return {"queued": self.queue.qsize(), "sending": self.sending}
