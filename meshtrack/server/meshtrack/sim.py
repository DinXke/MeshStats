"""Virtuele trackers die 24/7 over echte wegen rijden.

Elke simulator kiest zelf bestemmingen rond zijn thuisbasis, rijdt erheen over
het wegennet uit de kaarttegels (roadgraph), parkeert, en begint opnieuw. De
GPS-metingen gaan door exact dezelfde bewegingsregels als de firmware
(rules.py); de berichten gaan als echte T1-berichten door de normale
verwerking, maar NIET over de mesh (geen zendtijd).

Batterij: zakt volgens het verbruiksmodel van de T1000-E (sneller met
batt_speed); onder 20 % gaat hij geparkeerd aan de lader = USB = companion-
modus, tot 100 %.
"""
from __future__ import annotations

import asyncio
import logging
import math
import random
import secrets
import time
from dataclasses import dataclass, field
import os
from datetime import datetime
from typing import Awaitable, Callable, Optional
from zoneinfo import ZoneInfo

from .geo import angle_diff, bearing, haversine, offset
from .roadgraph import Leg, Router
from .rules import STILL_KMH, Params, RuleState, decide, heartbeat_due, stillness

log = logging.getLogger("meshtrack.sim")

Emit = Callable[[str, str, Optional[int], Optional[float], Optional[int]], Awaitable[None]]

# Dagritme in lokale tijd (de server zelf draait meestal in UTC).
TZ = ZoneInfo(os.environ.get("MESHTRACK_TZ", "Europe/Brussels"))


def local_now() -> datetime:
    return datetime.now(TZ)


AIRTIME_S = 0.7    # geschatte zendtijd van één T1-bericht (SF8/BW62,5/CR4/8)

# Rijgedrag per profiel.
PROFILE = {
    #        optrekken/afremmen (km/u per s), bochtsnelheid, ritlengte km, kans op stop per kruispunt, stopduur s
    "car":  dict(acc=2.5, dec=7.0, corner=20, bend=40, trip=(2, 25), stop_p=0.10, stop_s=(5, 45)),
    "bike": dict(acc=1.5, dec=4.0, corner=10, bend=14, trip=(1, 10), stop_p=0.06, stop_s=(3, 25)),
    "walk": dict(acc=2.0, dec=4.0, corner=4, bend=5, trip=(0.5, 4), stop_p=0.03, stop_s=(5, 60)),
}

# Batterijverbruik in %/u (T1000-E 700 mAh, zie PLAN 4.5).
BATT_MOVING = 2.3      # GPS continu + zenden
BATT_SLEEP = 0.03      # trackermodus in rust
BATT_COMPANION = 0.8   # BLE + continu RX
BATT_CHARGE = 35.0     # laden via USB


def new_pubkey() -> str:
    return secrets.token_hex(32)


@dataclass
class SimStats:
    sent: int = 0
    lost: int = 0
    reasons: dict = field(default_factory=dict)
    started: float = field(default_factory=time.time)
    trips: int = 0
    km: float = 0.0

    def per_hour(self) -> dict:
        h = max((time.time() - self.started) / 3600, 1 / 60)
        return {"msgs_h": round(self.sent / h, 1), "airtime_s_h": round(self.sent * AIRTIME_S / h, 1)}


class SimTracker:
    def __init__(self, row: dict, router: Router, emit: Emit, start: Optional[dict] = None):
        self.tid = row["tracker_id"]
        self.alias = row["alias"]
        self.prefix = row["pubkey"][:12]
        self.profile = row["profile"]
        self.prof = PROFILE[self.profile]
        self.home = (row["home_lat"], row["home_lon"])
        self.params = Params.from_dict(row["params"])
        self.loss = row["loss_pct"] / 100
        self.batt_speed = float(row["batt_speed"])
        self.router, self.emit = router, emit
        self.rng = random.Random()

        start = start or {}
        self.lat = start.get("last_lat") or self.home[0]
        self.lon = start.get("last_lon") or self.home[1]
        self.batt = float(start.get("last_bat") if start.get("last_bat") is not None else 100)
        self.seq = ((start.get("last_seq") or 0) + 1) % 65536

        self.phase = "park"                 # park | plan | drive
        self.park_until = time.time() + self.rng.uniform(5, 30)
        self.charging = False
        self.mode = "t"
        self.legs: list[Leg] = []
        self.i = 0                          # huidig routestuk
        self.pos_m = 0.0                    # meters in dat stuk
        self.spd = 0.0
        self.crs: Optional[float] = None
        self.trip_factor = 1.0
        self.stop_until = 0.0
        self.dest: Optional[tuple[float, float]] = None
        self.rules = RuleState()
        self.rules.sleeping = True          # begint geparkeerd
        self.rules.last_heartbeat = time.time()
        self.waking_until: Optional[float] = None
        self.stats = SimStats()
        self.note = "geparkeerd"
        self._task: Optional[asyncio.Task] = None

    # ---- extern -----------------------------------------------------------------

    def start(self) -> None:
        if not self._task or self._task.done():
            self._task = asyncio.create_task(self._run(), name=f"sim-{self.tid}")

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
            self._task = None

    @property
    def running(self) -> bool:
        return bool(self._task and not self._task.done())

    def status(self) -> dict:
        return {
            "tracker_id": self.tid, "running": self.running, "phase": self.phase, "note": self.note,
            "lat": self.lat, "lon": self.lon, "spd": round(self.spd), "batt": round(self.batt, 1),
            "charging": self.charging, "mode": self.mode,
            "dest": self.dest, "route_left_km": round(self._left_m() / 1000, 1) if self.phase == "drive" else None,
            "sent": self.stats.sent, "lost": self.stats.lost, "reasons": self.stats.reasons,
            "trips": self.stats.trips, "km": round(self.stats.km, 1), **self.stats.per_hour(),
        }

    def route_geojson(self) -> Optional[list[list[float]]]:
        if self.phase != "drive" or not self.legs:
            return None
        return [[self.lon, self.lat]] + [[lg.lon, lg.lat] for lg in self.legs[self.i + 1:]]

    # ---- lus ----------------------------------------------------------------------

    async def _run(self) -> None:
        last = time.time()
        while True:
            await asyncio.sleep(1)
            now = time.time()
            dt = min(now - last, 5)
            last = now
            try:
                if self.phase == "park" and now >= self.park_until and not self._needs_charge():
                    await self._plan(now)
                if self.phase == "drive":
                    self._drive(now, dt)
                else:
                    self.spd = 0.0
                self._battery(now, dt)
                await self._observe(now)
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001
                log.exception("simulator %s", self.alias)
                self.phase, self.park_until = "park", now + 120

    # ---- plannen ------------------------------------------------------------------

    def _next_destination(self, now: float) -> tuple[float, float]:
        hour = local_now().hour
        from_home = haversine(self.lat, self.lon, *self.home) / 1000
        if hour >= 21 or hour < 6:
            return self.home if from_home > 0.3 else self.router.random_destination(self.home, 0.5, 2, self.rng)
        if from_home > 15 and self.rng.random() < 0.6:
            return self.home
        lo, hi = self.prof["trip"]
        base = (self.lat, self.lon) if from_home < 30 else self.home
        return self.router.random_destination(base, lo, hi, self.rng)

    async def _plan(self, now: float) -> None:
        self.phase, self.note = "plan", "route berekenen"
        for _ in range(4):
            dest = self._next_destination(now)
            legs = await asyncio.to_thread(self.router.route, (self.lat, self.lon), dest, self.profile)
            if legs and len(legs) >= 2:
                self.legs, self.i, self.pos_m, self.dest = legs, 0, 0.0, (legs[-1].lat, legs[-1].lon)
                self.trip_factor = self.rng.uniform(0.85, 1.05)
                self.phase, self.note = "drive", f"onderweg ({self._left_m() / 1000:.1f} km)"
                self.stats.trips += 1
                # vertrek: eerst naar het beginpunt van de route "springen" is
                # onrealistisch als dat ver is; het ligt altijd op < 3 km en
                # meestal op enkele meters (dichtstbijzijnde weg).
                self.lat, self.lon = legs[0].lat, legs[0].lon
                return
        self.phase, self.note = "park", "geen route gevonden, later opnieuw"
        self.park_until = now + 600

    def _park(self, now: float) -> None:
        hour = local_now().hour
        r = self.rng.random()
        if hour >= 21 or hour < 6:
            wake = local_now().replace(hour=7, minute=0, second=0)
            secs = (wake.timestamp() - now) % 86400 + self.rng.uniform(0, 5400)
        elif r < 0.6:
            secs = self.rng.uniform(10, 40) * 60
        elif r < 0.9:
            secs = self.rng.uniform(1, 3) * 3600
        else:
            secs = self.rng.uniform(3, 6) * 3600
        self.phase, self.park_until, self.legs, self.dest = "park", now + secs, [], None
        self.note = f"geparkeerd tot {datetime.fromtimestamp(self.park_until, TZ):%H:%M}"

    # ---- rijden -------------------------------------------------------------------

    def _left_m(self) -> float:
        if not self.legs:
            return 0.0
        rest = sum(haversine(a.lat, a.lon, b.lat, b.lon) for a, b in zip(self.legs[self.i:], self.legs[self.i + 1:]))
        return max(0.0, rest - self.pos_m)

    def _seg(self, i: int) -> tuple[float, float]:
        a, b = self.legs[i], self.legs[i + 1]
        return haversine(a.lat, a.lon, b.lat, b.lon), bearing(a.lat, a.lon, b.lat, b.lon)

    def _target_speed(self) -> float:
        lg = self.legs[self.i]
        target = lg.limit_kmh * self.trip_factor
        # vooruitkijken: bochten binnen remafstand
        look = max(30.0, (self.spd / 3.6) ** 2 / 2 / (self.prof["dec"] / 3.6) + 15)
        dist = self._seg(self.i)[0] - self.pos_m
        j = self.i
        while dist < look and j + 2 < len(self.legs):
            turn = angle_diff(self._seg(j)[1], self._seg(j + 1)[1])
            if turn > 60:
                target = min(target, self.prof["corner"])
            elif turn > 30:
                target = min(target, self.prof["bend"])
            j += 1
            dist += self._seg(j)[0]
        # aankomst: uitbollen
        left = self._left_m()
        if left < 60:
            target = min(target, max(5.0, left / 4))
        return target

    def _drive(self, now: float, dt: float) -> None:
        if now < self.stop_until:
            self.spd = max(0.0, self.spd - self.prof["dec"] * dt)
            return
        target = self._target_speed()
        if self.spd < target:
            self.spd = min(target, self.spd + self.prof["acc"] * dt)
        else:
            self.spd = max(target, self.spd - self.prof["dec"] * dt)
        self.spd = max(0.0, self.spd + self.rng.gauss(0, 0.3))
        move = self.spd / 3.6 * dt
        self.stats.km += move / 1000
        while move > 0 and self.i + 1 < len(self.legs):
            seg_len, seg_brg = self._seg(self.i)
            room = seg_len - self.pos_m
            if move < room:
                self.pos_m += move
                move = 0
            else:
                move -= room
                self.i += 1
                self.pos_m = 0.0
                if self.i + 1 < len(self.legs):
                    a, b = self.legs[self.i - 1], self.legs[self.i]
                    # kruispunt (wegtype wisselt): soms even stoppen
                    if a.kind != b.kind and self.rng.random() < self.prof["stop_p"]:
                        self.stop_until = now + self.rng.uniform(*self.prof["stop_s"])
            self.crs = seg_brg
        if self.i + 1 >= len(self.legs):
            last = self.legs[-1]
            self.lat, self.lon, self.spd = last.lat, last.lon, 0.0
            self._park(now)
            return
        a, b = self.legs[self.i], self.legs[self.i + 1]
        seg_len = self._seg(self.i)[0] or 1
        f = self.pos_m / seg_len
        self.lat, self.lon = a.lat + (b.lat - a.lat) * f, a.lon + (b.lon - a.lon) * f
        self.note = f"onderweg ({self._left_m() / 1000:.1f} km te gaan)"

    # ---- batterij en lader ----------------------------------------------------------

    def _needs_charge(self) -> bool:
        return self.charging or self.batt < 20

    def _battery(self, now: float, dt: float) -> None:
        h = dt / 3600 * self.batt_speed
        if self.phase == "park" and not self.charging and self.batt < 20:
            self.charging, self.mode = True, "c"
            self._pending_mode = True
            self.note = "aan de lader (companion)"
        if self.charging:
            self.batt = min(100.0, self.batt + BATT_CHARGE * h)
            if self.batt >= 100:
                self.charging, self.mode = False, "t"
                self._pending_mode = True
                self.park_until = min(self.park_until, now + self.rng.uniform(60, 600))
                self.note = "opgeladen"
            return
        if self.mode == "c":
            rate = BATT_COMPANION + (BATT_MOVING - BATT_COMPANION) * (self.phase == "drive")
        else:
            rate = BATT_MOVING if (self.phase == "drive" or not self.rules.sleeping) else BATT_SLEEP
        self.batt = max(0.0, self.batt - rate * h)

    # ---- regels toepassen en verzenden ---------------------------------------------

    async def _observe(self, now: float) -> None:
        if getattr(self, "_pending_mode", False):
            self._pending_mode = False
            await self._send(now, "B", with_pos=False, reason="modus")
        st, p = self.rules, self.params
        moving = self.spd >= STILL_KMH
        if st.sleeping:
            if moving:
                if self.waking_until is None:            # beweging -> GPS aan, fix zoeken
                    self.waking_until = now + self.rng.uniform(5, 25)
                elif now >= self.waking_until:
                    st.sleeping, self.waking_until, st.still_since = False, None, None
                    await self._send(now, "M", reason="wakker")
                return
            if heartbeat_due(st, p, now):
                st.last_heartbeat = now
                await self._send(now, "H", reason="heartbeat")
            return
        if stillness(st, p, now, self.spd):
            await self._send(now, "S", reason="stil")
            st.sleeping, st.last_heartbeat = True, now
            return
        if moving:
            reason = decide(st, p, now, self.lat, self.lon, self.spd, self.crs)
            if reason:
                await self._send(now, "M", reason=reason)

    async def _send(self, now: float, state: str, reason: str, with_pos: bool = True) -> None:
        lat = lon = None
        hdop = None
        if with_pos:
            hdop = round(max(0.6, self.rng.gauss(1.1, 0.25)), 1)
            lat, lon = offset(self.lat, self.lon, self.rng.gauss(0, 2.5 * hdop), self.rng.gauss(0, 2.5 * hdop))
        spd = round(self.spd) if with_pos else None
        crs = round(self.crs) % 360 if (with_pos and self.crs is not None and self.spd >= 3) else None
        fields_ = [
            "T1", str(self.seq), state,
            f"{lat:.5f}" if lat is not None else "", f"{lon:.5f}" if lon is not None else "",
            str(round(self.rng.uniform(25, 60))) if with_pos else "",
            str(spd) if spd is not None else "", str(crs) if crs is not None else "",
            str(int(self.batt)), f"{hdop}" if hdop is not None else "", "0" if with_pos else "",
            self.mode,
        ]
        text = "|".join(fields_)
        self.seq = (self.seq + 1) % 65536
        self.rules.sent(now, lat, lon, crs)
        self.stats.reasons[reason] = self.stats.reasons.get(reason, 0) + 1
        if self.rng.random() < self.loss:
            self.stats.lost += 1
            return
        self.stats.sent += 1
        snr = round(self.rng.uniform(-8, 10), 1)
        hops = self.rng.choice((0, 1, 1, 2, 2, 3))
        await self.emit(self.prefix, text, int(now), snr, hops)


class SimManager:
    def __init__(self, router_factory: Callable[[], Optional[Router]], emit: Emit):
        self._router_factory = router_factory
        self._router: Optional[Router] = None
        self.emit = emit
        self.sims: dict[int, SimTracker] = {}

    @property
    def router(self) -> Optional[Router]:
        if self._router is None:
            self._router = self._router_factory()
        return self._router

    async def start(self, row: dict, tracker: dict) -> SimTracker:
        await self.stop(row["tracker_id"])
        if self.router is None:
            raise RuntimeError("geen kaarttegels: simulator kan niet routeren")
        s = SimTracker(row, self.router, self.emit, tracker)
        self.sims[row["tracker_id"]] = s
        s.start()
        return s

    async def stop(self, tid: int) -> None:
        s = self.sims.pop(tid, None)
        if s:
            await s.stop()

    async def stop_all(self) -> None:
        for tid in list(self.sims):
            await self.stop(tid)

    def status(self, tid: int) -> Optional[dict]:
        s = self.sims.get(tid)
        return s.status() if s else None

    def routes(self) -> dict[int, list]:
        return {tid: r for tid, s in self.sims.items() if (r := s.route_geojson())}
