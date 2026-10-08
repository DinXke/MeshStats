"""T1-berichtprotocol (zie docs/protocol.md).

    T1|<seq>|<state>|<lat>|<lon>|<alt_m>|<spd_kmh>|<crs_deg>|<bat_pct>|<hdop>|<fix_age_s>[|<mode>[|<power>[|<fix_ts>[|<extra>]]]]

`extra`: eerdere punten. Compact (fw 0.6.0+): `~<interval>;dlat,dlon[@s];...`, nieuwste
eerst; elk punt is het verschil met het vorige (het eerste met het hoofdpunt) in 1e-5
graden en ligt <interval> seconden eerder, of `@s` seconden als dat afwijkt. De snelheid
volgt uit afstand en tijd. Ouder (fw 0.5.0): `dt,dlat,dlon,spd;...` t.o.v. het hoofdpunt.

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
    extra: list = None            # [(dt_s, lat, lon, spd)] eerdere punten, oud of nieuw door elkaar

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
    if len(parts) not in (11, 12, 13, 14, 15):
        raise ProtocolError(f"verwacht 11 tot 15 velden, kreeg {len(parts)}")

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
    extra = []
    if len(rest) > 3 and rest[3].startswith("~"):
        if lat is None:
            raise ProtocolError("extra punten zonder hoofdpunt")
        extra = _compact_extra(rest[3], lat, lon)
    elif len(rest) > 3 and rest[3]:
        if lat is None:
            raise ProtocolError("extra punten zonder hoofdpunt")
        items = rest[3].split(";")
        if len(items) > 20:
            raise ProtocolError("te veel extra punten")
        for it in items:
            f = it.split(",")
            if len(f) != 4:
                raise ProtocolError(f"extra punt: verwacht dt,dlat,dlon,spd: {it!r}")
            dt = _opt_int(f[0], 1, 86400, "extra dt")
            dla = _opt_int(f[1], -2_000_000, 2_000_000, "extra dlat")
            dlo = _opt_int(f[2], -2_000_000, 2_000_000, "extra dlon")
            if dt is None or dla is None or dlo is None:
                raise ProtocolError(f"extra punt onvolledig: {it!r}")
            plat, plon = round(lat + dla / 1e5, 5), round(lon + dlo / 1e5, 5)
            if not (-90 <= plat <= 90 and -180 <= plon <= 180):
                raise ProtocolError("extra punt buiten bereik")
            extra.append((dt, plat, plon, _opt_int(f[3], 0, 1000, "extra spd")))

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
        extra=extra,
    )


def _compact_extra(field: str, lat: float, lon: float) -> list:
    """`~15;-412,201;-398,190@40` -> [(dt, lat, lon, spd)], dt cumulatief t.o.v. het hoofdpunt."""
    from .geo import haversine
    head, *items = field.split(";")
    step = _opt_int(head[1:], 1, 86400, "interval")
    if step is None or len(items) > 30:
        raise ProtocolError("extra punten: ongeldig interval of te veel punten")
    out, dt, plat, plon = [], 0, lat, lon
    for it in items:
        xy, _, gap_s = it.partition("@")
        f = xy.split(",")
        if len(f) != 2:
            raise ProtocolError(f"extra punt: verwacht dlat,dlon[@s]: {it!r}")
        dla = _opt_int(f[0], -2_000_000, 2_000_000, "extra dlat")
        dlo = _opt_int(f[1], -2_000_000, 2_000_000, "extra dlon")
        gap = _opt_int(gap_s, 1, 86400, "extra tijd") if gap_s else step
        if dla is None or dlo is None:
            raise ProtocolError(f"extra punt onvolledig: {it!r}")
        nlat, nlon = round(plat + dla / 1e5, 5), round(plon + dlo / 1e5, 5)
        if not (-90 <= nlat <= 90 and -180 <= nlon <= 180):
            raise ProtocolError("extra punt buiten bereik")
        dt += gap
        spd = round(haversine(nlat, nlon, plat, plon) / gap * 3.6)
        out.append((dt, nlat, nlon, min(spd, 1000)))
        plat, plon = nlat, nlon
    return out


def is_suspect(r: Report, bbox: tuple[float, float, float, float], max_hdop: float = 5.0) -> bool:
    """Positie buiten het verwachte gebied of met slechte nauwkeurigheid."""
    if not r.has_fix:
        return False
    w, s, e, n = bbox
    if not (w <= r.lon <= e and s <= r.lat <= n):
        return True
    return r.hdop is not None and r.hdop > max_hdop
