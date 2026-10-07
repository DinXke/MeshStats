"""T1-berichtprotocol (zie docs/protocol.md).

    T1|<seq>|<state>|<lat>|<lon>|<alt_m>|<spd_kmh>|<crs_deg>|<bat_pct>|<hdop>|<fix_age_s>[|<mode>[|<power>[|<fix_ts>]]]

Lege velden zijn toegestaan waar de spec dat zegt; `mode` (c|t), `power`
(u = USB/laden, b = batterij) en `fix_ts` (GPS-tijd van de fix, unix-seconden)
zijn optioneel. Met fix_ts staat een positie op het juiste moment, ook als het
bericht pas na herhaalpogingen of een wachtrij aankomt.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

STATES = {
    "M": "bewegend",
    "W": "wakker door beweging",
    "S": "stilgevallen",
    "H": "heartbeat",
    "N": "geen fix",
    "E": "SOS",
    "P": "handmatig",
    "B": "modus/boot",
}
MODES = {"c": "companion", "t": "tracker"}
POWER = {"u": "USB", "b": "batterij"}


class ProtocolError(ValueError):
    """Bericht is geen geldig T1-bericht."""


class UnknownVersion(ProtocolError):
    """Begint als een MeshTrack-bericht maar met een versie die we niet kennen."""


@dataclass
class Report:
    seq: int
    state: str
    lat: Optional[float]
    lon: Optional[float]
    alt_m: Optional[int]
    spd_kmh: Optional[int]
    crs_deg: Optional[int]
    bat_pct: Optional[int]
    hdop: Optional[float]
    fix_age_s: Optional[int]
    mode: Optional[str] = None
    power: Optional[str] = None
    fix_ts: Optional[int] = None

    @property
    def has_fix(self) -> bool:
        return self.lat is not None and self.lon is not None


def _opt_int(v: str, lo: int, hi: int, name: str) -> Optional[int]:
    if v == "":
        return None
    try:
        n = int(v)
    except ValueError:
        raise ProtocolError(f"{name}: geen geheel getal: {v!r}")
    if not lo <= n <= hi:
        raise ProtocolError(f"{name}: buiten bereik: {n}")
    return n


def _opt_float(v: str, lo: float, hi: float, name: str) -> Optional[float]:
    if v == "":
        return None
    try:
        f = float(v)
    except ValueError:
        raise ProtocolError(f"{name}: geen getal: {v!r}")
    if not lo <= f <= hi:
        raise ProtocolError(f"{name}: buiten bereik: {f}")
    return f


def is_meshtrack(text: str) -> bool:
    """Lijkt dit op een MeshTrack-bericht (eender welke versie)?"""
    t = text.strip()
    return len(t) >= 3 and t[0] == "T" and t[1:].split("|", 1)[0].isdigit() and "|" in t


def parse(text: str) -> Report:
    t = text.strip()
    parts = t.split("|")
    if not parts or not parts[0].startswith("T") or not parts[0][1:].isdigit():
        raise ProtocolError("geen MeshTrack-bericht")
    if parts[0] != "T1":
        raise UnknownVersion(f"onbekende versie {parts[0]}")
    if len(parts) not in (11, 12, 13, 14):
        raise ProtocolError(f"verwacht 11 tot 14 velden, kreeg {len(parts)}")

    _, seq_s, state, lat_s, lon_s, alt_s, spd_s, crs_s, bat_s, hdop_s, age_s, *rest = parts
    seq = _opt_int(seq_s, 0, 65535, "seq")
    if seq is None:
        raise ProtocolError("seq ontbreekt")
    if state not in STATES:
        raise ProtocolError(f"onbekende state {state!r}")

    lat = _opt_float(lat_s, -90, 90, "lat")
    lon = _opt_float(lon_s, -180, 180, "lon")
    if (lat is None) != (lon is None):
        raise ProtocolError("lat en lon moeten samen gegeven zijn")
    if state == "M" and lat is None:      # S mag zonder fix (stilgevallen binnen)
        raise ProtocolError(f"state {state} vereist een positie")

    mode = power = None
    if rest:
        mode = rest[0] or None
        if mode is not None and mode not in MODES:
            raise ProtocolError(f"onbekende mode {mode!r}")
    if len(rest) > 1:
        power = rest[1] or None
        if power is not None and power not in POWER:
            raise ProtocolError(f"onbekende voeding {power!r}")
    fix_ts = _opt_int(rest[2], 1_500_000_000, 4_000_000_000, "fix_ts") if len(rest) > 2 else None

    return Report(
        seq=seq,
        state=state,
        lat=lat,
        lon=lon,
        alt_m=_opt_int(alt_s, -1000, 20000, "alt"),
        spd_kmh=_opt_int(spd_s, 0, 1000, "spd"),
        crs_deg=_opt_int(crs_s, 0, 359, "crs"),
        bat_pct=_opt_int(bat_s, 0, 100, "bat"),
        hdop=_opt_float(hdop_s, 0, 99.9, "hdop"),
        fix_age_s=_opt_int(age_s, 0, 10**7, "fix_age"),
        mode=mode,
        power=power,
        fix_ts=fix_ts,
    )


def is_suspect(r: Report, bbox: tuple[float, float, float, float], max_hdop: float = 5.0) -> bool:
    """Positie buiten het verwachte gebied of met slechte nauwkeurigheid."""
    if not r.has_fix:
        return False
    w, s, e, n = bbox
    if not (w <= r.lon <= e and s <= r.lat <= n):
        return True
    return r.hdop is not None and r.hdop > max_hdop
