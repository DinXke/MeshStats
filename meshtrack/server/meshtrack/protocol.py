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

State `L` (fw 0.8.0, SlowTrack): gelogde punten, in bursts verstuurd naast de gewone
tracking. Het hoofdpunt is het nieuwste punt van dat stuk (met eigen fix_ts), de extra
punten zijn oudere punten. Meestal ouder dan de live-positie: zie ingest.py.
State `Q` (fw 0.9.0, FIFO): ingehaalde punten (store-and-forward van gemiste posities),
verwerkt zoals `L`; de server bevestigt ze met T1F (zie main.py).

Binaire extra punten (fw 0.9.0, op L en Q): `B<base64url zonder padding>`. De bytes zijn
punten, nieuwste eerst, elk t.o.v. het vorige (het eerste t.o.v. het hoofdpunt), elk drie
LEB128-varints: dt = vorige_ts - deze_ts (s), dlat en dlon in 1e-5 graden (zigzag).

Veld 16 (optioneel, fw 0.9.0): vlaggen; `f` = bevestiging gevraagd (Q met verstuurde maar
nog niet bevestigde punten); `g` (fw 0.9.4) = de tracker verstaat de exacte bevestiging per seq.
Sinds server 1.3.1 krijgt alleen een tracker met `g` een T1F (zie main.py); zonder `g` geen T1F.
"""
from __future__ import annotations

import base64
import binascii
import re
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
    "V": "op verzoek",             # fw 0.9.5: antwoord op een T1R-verzoek; live, zoals P
    "B": "modus/boot",
    "L": "gelogd punt (SlowTrack)",
    "Q": "ingehaald punt (FIFO)",
}
HISTORY_STATES = ("L", "Q")      # punten uit het verleden: nooit live-toestand, zie ingest.py
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
    flags: str = ""               # veld 16: letters; "f" = bevestiging gevraagd (FIFO, fw 0.9.0)

    @property
    def ack_requested(self) -> bool:
        return "f" in self.flags

    @property
    def exact_ack(self) -> bool:
        """Vlag "g" (fw 0.9.4+): de tracker verstaat de exacte T1F-bevestiging per seq (zie main.py)."""
        return "g" in self.flags

    @property
    def prio(self) -> bool:
        """Vlag "p" (1.4): het voertuig rijdt prioritair (blauwe lichten). Geldt voor elke toestand."""
        return "p" in self.flags

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
    if len(parts) not in (11, 12, 13, 14, 15, 16):
        raise ProtocolError(f"verwacht 11 tot 16 velden, kreeg {len(parts)}")

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
    if state in ("M", "L", "Q") and lat is None:   # S mag zonder fix (stilgevallen binnen)
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
    if len(rest) > 3 and rest[3].startswith("B"):
        if lat is None:
            raise ProtocolError("extra punten zonder hoofdpunt")
        extra = _binary_extra(rest[3][1:], lat, lon)
    elif len(rest) > 3 and rest[3].startswith("~"):
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
        flags=_flags(rest[4]) if len(rest) > 4 else "",
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


def _flags(v: str) -> str:
    """Veld 16: kleine letters; onbekende letters worden verdragen (latere firmware)."""
    if len(v) > 8 or not all("a" <= ch <= "z" for ch in v):
        raise ProtocolError(f"ongeldige vlaggen {v!r}")
    return v


_B64URL = re.compile(r"^[A-Za-z0-9_-]*$")
MAX_BINARY_EXTRA = 40


def _varints(data: bytes) -> list[int]:
    """LEB128 (unsigned) -> getallen; max 5 bytes per getal."""
    out, n, shift = [], 0, 0
    for b in data:
        n |= (b & 0x7F) << shift
        if b & 0x80:
            shift += 7
            if shift >= 35:
                raise ProtocolError("binaire extra punten: varint te lang")
        else:
            out.append(n)
            n, shift = 0, 0
    if shift:
        raise ProtocolError("binaire extra punten: afgebroken varint")
    return out


def _binary_extra(field: str, lat: float, lon: float) -> list:
    """`B<base64url>` -> [(dt, lat, lon, spd)], dt cumulatief t.o.v. het hoofdpunt (zoals _compact_extra)."""
    from .geo import haversine
    if not _B64URL.match(field) or len(field) % 4 == 1:
        raise ProtocolError("binaire extra punten: geen geldige base64url")
    try:
        data = base64.urlsafe_b64decode(field + "=" * (-len(field) % 4))
    except (binascii.Error, ValueError):
        raise ProtocolError("binaire extra punten: geen geldige base64url")
    nums = _varints(data)
    if len(nums) % 3:
        raise ProtocolError("binaire extra punten: onvolledig punt")
    if len(nums) // 3 > MAX_BINARY_EXTRA:
        raise ProtocolError("binaire extra punten: te veel punten")
    out, dt, plat, plon = [], 0, lat, lon
    for i in range(0, len(nums), 3):
        gap, zla, zlo = nums[i:i + 3]
        dla, dlo = (zla >> 1) ^ -(zla & 1), (zlo >> 1) ^ -(zlo & 1)
        if gap > 86400 or abs(dla) > 2_000_000 or abs(dlo) > 2_000_000:
            raise ProtocolError("binair extra punt buiten bereik")
        nlat, nlon = round(plat + dla / 1e5, 5), round(plon + dlo / 1e5, 5)
        if not (-90 <= nlat <= 90 and -180 <= nlon <= 180):
            raise ProtocolError("extra punt buiten bereik")
        dt += gap
        # dt 0 = zelfde tijd als het vorige punt: geen snelheid (de server slaat het als dubbel over)
        spd = min(round(haversine(nlat, nlon, plat, plon) / gap * 3.6), 1000) if gap else None
        out.append((dt, nlat, nlon, spd))
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
