"""Bewegingsregels: wanneer stuurt een tracker een positie?

Dit is de REFERENTIE voor de firmware (firmware/meshtrack/MtRules.cpp volgt
exact dezelfde logica). De simulator gebruikt deze code, zodat wat je in de
simulator afstelt ook op de echte tracker zo werkt.

    rate_ok = (nu - laatste_tx) >= min_interval
    afstand = afstand(laatste_tx_pos, pos) >= min_dist
    snel    = min_speed == 0  OF  spd >= min_speed
    bocht   = turn_min > 0 EN spd >= turn_min_speed EN |koers - laatste_tx_koers| >= turn_min
    forceer = max_interval > 0 EN (nu - laatste_tx) >= max_interval

    zend als rate_ok EN ((snel EN afstand) OF bocht OF forceer)

Stilstand: langer dan still_timeout onder STILL_KMH -> `S` en slapen; in rust
elke heartbeat een `H`; beweging -> wakker, eerste fix wordt meteen verstuurd.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from .geo import angle_diff, haversine

STILL_KMH = 1.5   # onder deze snelheid telt de tracker als stilstaand


@dataclass
class Params:
    min_speed_kmh: int = 10
    min_dist_m: int = 100
    turn_min_deg: int = 30
    turn_min_speed_kmh: int = 5
    min_interval_s: int = 60
    max_interval_s: int = 600
    still_timeout_s: int = 300
    heartbeat_s: int = 12 * 3600

    @classmethod
    def from_dict(cls, d: dict) -> "Params":
        p = cls()
        for k in p.__dataclass_fields__:
            if k in d and d[k] is not None:
                setattr(p, k, int(d[k]))
        return p


@dataclass
class RuleState:
    last_tx: Optional[float] = None
    last_lat: Optional[float] = None
    last_lon: Optional[float] = None
    last_crs: Optional[float] = None
    still_since: Optional[float] = None
    sleeping: bool = False
    last_heartbeat: Optional[float] = None
    reasons: dict = field(default_factory=dict)   # telling per reden, voor statistiek

    def sent(self, now: float, lat: Optional[float], lon: Optional[float], crs: Optional[float]) -> None:
        self.last_tx = now
        if lat is not None:
            self.last_lat, self.last_lon = lat, lon
        if crs is not None:
            self.last_crs = crs


def decide(st: RuleState, p: Params, now: float, lat: float, lon: float, spd_kmh: float,
           crs: Optional[float]) -> Optional[str]:
    """Eén GPS-meting in beweging. Geeft de reden ('eerste', 'afstand', 'bocht',
    'max_interval') als er verzonden moet worden, anders None."""
    if st.last_tx is None or st.last_lat is None:
        return "eerste"
    since = now - st.last_tx
    if since < p.min_interval_s:
        return None
    dist_ok = haversine(st.last_lat, st.last_lon, lat, lon) >= p.min_dist_m
    fast_ok = p.min_speed_kmh == 0 or spd_kmh >= p.min_speed_kmh
    if fast_ok and dist_ok:
        return "afstand"
    if (p.turn_min_deg > 0 and crs is not None and st.last_crs is not None
            and spd_kmh >= p.turn_min_speed_kmh and angle_diff(crs, st.last_crs) >= p.turn_min_deg):
        return "bocht"
    if p.max_interval_s > 0 and since >= p.max_interval_s:
        return "max_interval"
    return None


def stillness(st: RuleState, p: Params, now: float, spd_kmh: float) -> bool:
    """Bijhouden hoe lang de tracker stilstaat. True = nu stilgevallen (stuur `S`)."""
    if spd_kmh >= STILL_KMH:
        st.still_since = None
        return False
    if st.still_since is None:
        st.still_since = now
        return False
    return not st.sleeping and now - st.still_since >= p.still_timeout_s


def heartbeat_due(st: RuleState, p: Params, now: float) -> bool:
    if not st.sleeping or p.heartbeat_s <= 0:
        return False
    ref = st.last_heartbeat or st.last_tx or now
    return now - ref >= p.heartbeat_s
