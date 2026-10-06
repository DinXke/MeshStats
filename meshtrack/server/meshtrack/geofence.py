"""Geofences: binnen/buiten bijhouden per tracker en zone, melding bij wissel."""
from __future__ import annotations

from typing import Any

from .db import DB
from .geo import haversine, point_in_polygon


def contains(g: dict[str, Any], lat: float, lon: float) -> bool:
    if g["kind"] == "circle":
        clon, clat = g["geom"]["center"]
        return haversine(lat, lon, clat, clon) <= float(g["geom"]["radius"])
    return point_in_polygon(lat, lon, g["geom"])


def evaluate(db: DB, tracker_id: int, lat: float, lon: float, ts: int) -> list[dict[str, Any]]:
    """Nieuwe posities toetsen. Geeft de gebeurtenissen (enter/exit) terug die
    gemeld moeten worden. De eerste positie ooit in een zone zet alleen de
    toestand (geen melding): we weten niet of hij er net binnenkwam."""
    events = []
    for g in db.geofences():
        if not g["active"] or (g["trackers"] and tracker_id not in g["trackers"]):
            continue
        inside = contains(g, lat, lon)
        was = db.geofence_inside(g["id"], tracker_id)
        if was is None:
            db.set_geofence_inside(g["id"], tracker_id, inside)
            continue
        if inside == was:
            continue
        db.set_geofence_inside(g["id"], tracker_id, inside)
        kind = "enter" if inside else "exit"
        if (kind == "enter" and g["on_enter"]) or (kind == "exit" and g["on_exit"]):
            eid = db.add_geofence_event(ts, g["id"], tracker_id, kind, lat, lon)
            events.append({"id": eid, "ts": ts, "geofence_id": g["id"], "geofence": g["name"],
                           "tracker_id": tracker_id, "event": kind, "lat": lat, "lon": lon,
                           "notify_pubkey": g["notify_pubkey"]})
    return events


def validate(body: dict[str, Any]) -> dict[str, Any]:
    """Invoer van de API controleren en normaliseren. ValueError bij fouten."""
    name = (body.get("name") or "").strip()
    if not name or len(name) > 60:
        raise ValueError("naam is verplicht (max 60 tekens)")
    kind = body.get("kind")
    geom = body.get("geom")
    if kind == "circle":
        c = geom.get("center") if isinstance(geom, dict) else None
        r = geom.get("radius") if isinstance(geom, dict) else None
        if not (isinstance(c, list) and len(c) == 2 and isinstance(r, (int, float)) and 10 <= r <= 200_000):
            raise ValueError("cirkel: center [lon,lat] en radius 10..200000 m")
        geom = {"center": [float(c[0]), float(c[1])], "radius": float(r)}
    elif kind == "polygon":
        if not (isinstance(geom, list) and 3 <= len(geom) <= 500):
            raise ValueError("polygoon: 3..500 punten")
        geom = [[float(p[0]), float(p[1])] for p in geom]
        if geom[0] != geom[-1]:
            geom.append(geom[0])
    else:
        raise ValueError("kind moet circle of polygon zijn")
    color = body.get("color") or "#3b82f6"
    if not (isinstance(color, str) and len(color) == 7 and color.startswith("#")):
        raise ValueError("kleur moet #rrggbb zijn")
    trackers = [int(t) for t in (body.get("trackers") or [])]
    pk = (body.get("notify_pubkey") or "").strip().lower()
    if pk and (len(pk) != 64 or any(ch not in "0123456789abcdef" for ch in pk)):
        raise ValueError("notify_pubkey moet leeg of 64 hex zijn")
    return {"name": name, "kind": kind, "geom": geom, "color": color, "trackers": trackers,
            "on_enter": bool(body.get("on_enter", True)), "on_exit": bool(body.get("on_exit", True)),
            "notify_pubkey": pk, "active": bool(body.get("active", True))}
