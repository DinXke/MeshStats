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

Ritme volgens de ontvangst (link): na een ACK die binnen fast_ack_s binnenkwam,
gaat de tracker in "snel": in beweging elke fast_interval een positie (en
min_interval zakt tot fast_interval). Pas na meer dan fast_keep mislukte zendingen
na elkaar valt hij terug. Na slow_after mislukte zendingen na elkaar gaat hij in
"traag": min_interval en max_interval x slow_factor, tot de volgende ACK.

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
    adaptive: int = 1             # ritme volgens de ontvangst aan (1) of uit (0)
    sample_s: int = 15            # in beweging elke x s een punt bewaren, mee in het volgende bericht (0 = uit)
    fast_interval_s: int = 30     # 0 = uit
    fast_keep: int = 2            # zoveel missers na elkaar blijft hij snel
    fast_ack_s: int = 10          # ACK moet zo snel komen om snel te worden (0 = elke ACK)
    slow_after: int = 3           # 0 = nooit trager
    slow_factor: int = 3

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
    fast: bool = False            # goede ontvangst: snel ritme
    slow: bool = False            # slechte ontvangst: trager
    fails: int = 0                # mislukte zendingen na elkaar

    def sent(self, now: float, lat: Optional[float], lon: Optional[float], crs: Optional[float]) -> None:
        self.last_tx = now
        if lat is not None:
            self.last_lat, self.last_lon = lat, lon
        if crs is not None:
            self.last_crs = crs


FAST_MIN_MOVE_M = 20      # snel ritme: alleen als hij echt verplaatst (geen dubbele punten)


def link_result(st: RuleState, p: Params, ok: bool, ack_s: Optional[float] = None) -> None:
    """Uitkomst van een zending (ACK of na alle pogingen mislukt) bijhouden."""
    if not p.adaptive:
        st.fast = st.slow = False
        st.fails = 0
        return
    if ok:
        st.fails = 0
        st.slow = False
        quick = p.fast_ack_s == 0 or (ack_s is not None and ack_s <= p.fast_ack_s)
        st.fast = p.fast_interval_s > 0 and quick
        return
    st.fails += 1
    if st.fails > p.fast_keep:
        st.fast = False
    if p.slow_after > 0 and st.fails >= p.slow_after:
        st.slow = True


def intervals(st: RuleState, p: Params) -> tuple[int, int]:
    """(min_interval, max_interval) volgens de ontvangst."""
    lo, hi = p.min_interval_s, p.max_interval_s
    if st.slow:
        f = max(1, p.slow_factor)
        return lo * f, hi * f
    if st.fast and p.fast_interval_s > 0:
        return min(lo, p.fast_interval_s), hi
    return lo, hi


def decide(st: RuleState, p: Params, now: float, lat: float, lon: float, spd_kmh: float,
           crs: Optional[float]) -> Optional[str]:
    """Eén GPS-meting in beweging. Geeft de reden ('eerste', 'afstand', 'bocht',
    'snel', 'max_interval') als er verzonden moet worden, anders None."""
    if st.last_tx is None or st.last_lat is None:
        return "eerste"
    since = now - st.last_tx
    min_i, max_i = intervals(st, p)
    if since < min_i:
        return None
    moved = haversine(st.last_lat, st.last_lon, lat, lon)
    if (st.fast and not st.slow and p.fast_interval_s > 0 and since >= p.fast_interval_s
            and moved >= FAST_MIN_MOVE_M):
        return "snel"
    dist_ok = moved >= p.min_dist_m
    fast_ok = p.min_speed_kmh == 0 or spd_kmh >= p.min_speed_kmh
    if fast_ok and dist_ok:
        return "afstand"
    if (p.turn_min_deg > 0 and crs is not None and st.last_crs is not None
            and spd_kmh >= p.turn_min_speed_kmh and angle_diff(crs, st.last_crs) >= p.turn_min_deg):
        return "bocht"
    if max_i > 0 and since >= max_i:
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
