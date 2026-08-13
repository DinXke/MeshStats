"""Publieke HTML-pagina's."""
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse

from . import auth, config, db, metrics
from .templating import templates

router = APIRouter()


@router.get("/", response_class=HTMLResponse)
def index(request: Request):
    rows = db.q("SELECT * FROM repeaters WHERE is_public=1 ORDER BY sort_order, name")
    cards = []
    for r in rows:
        latest = db.latest_for(r["id"])
        def val(m):
            row = latest.get(m)
            return None if row is None or row["value"] is None else row["value"]
        cards.append({
            "slug": r["slug"], "name": r["name"], "prefix": r["pubkey_prefix"],
            "last_seen": r["last_seen"],
            "online": val("online") == 1.0,
            "battery": val("battery_percentage"),
            "uptime": val("uptime"),
            "neighbors": val("neighbor_count"),
            "temperature": val("ch1_temperature") or val("ch2_temperature"),
        })
    return templates.TemplateResponse(request, "index.html", {
        "site_name": config.SITE_NAME, "cards": cards,
    })


@router.get("/r/{slug}", response_class=HTMLResponse)
def repeater_page(request: Request, slug: str):
    r = db.qone("SELECT * FROM repeaters WHERE slug=? AND is_public=1", (slug,))
    if not r:
        raise HTTPException(404, "Onbekende repeater")
    latest = db.latest_for(r["id"])

    # Benutting zelf berekenen uit de airtime-totalen; de HA-waarde valt na
    # elke HA-herstart terug op 0 tot het meetvenster daar weer opgebouwd is.
    computed = {
        "airtime_utilization": db.computed_utilization(r["id"], "airtime"),
        "rx_airtime_utilization": db.computed_utilization(r["id"], "rx_airtime"),
    }

    # tegels per sectie
    sections: dict[str, dict] = {}
    used = set()
    for key, title in metrics.SECTIONS:
        tiles = []
        for m in metrics.TILE_METRICS.get(key, []):
            row = latest.get(m)
            if row is None:
                continue
            # Ch1-spanning meet dezelfde batterij als 'bat' — dubbele tegel weglaten
            if m == "ch1_voltage" and "bat" in latest:
                used.add(m)
                continue
            used.add(m)
            _, label, unit, _ = metrics.metric_info(m)
            tile = _tile(m, label, unit, row)
            if computed.get(m) is not None:
                tile["value"] = computed[m]
                tile["display"] = f"{computed[m]:g} {unit}" if unit else f"{computed[m]:g}"
            tiles.append(tile)
        extra = []
        for m, row in latest.items():
            if m in used:
                continue
            section, label, unit, sort = metrics.metric_info(m)
            if section == key:
                extra.append((sort, m, _tile(m, label, unit, row)))
        for _, m, t in sorted(extra, key=lambda x: x[0]):
            tiles.append(t)
            used.add(m)
        if tiles:
            sections[key] = {"key": key, "title": title, "tiles": tiles}

    # naam uit de neighbor-sensor, met de contactendatabase (adverts) als fallback
    neighbors = db.q(
        "SELECT n.prefix, n.snr, n.last_seen, "
        "CASE WHEN n.name IS NULL OR lower(n.name) = n.prefix "
        "THEN COALESCE(c.name, n.name) ELSE n.name END AS name "
        "FROM neighbors n LEFT JOIN contacts c ON c.prefix6 = n.prefix "
        "WHERE n.repeater_id=? ORDER BY n.snr DESC",
        (r["id"],),
    )
    charts = [
        {"title": title, "metrics": mets, "hours": hours,
         "labels": [metrics.metric_info(m)[1] for m in mets],
         "unit": metrics.metric_info(mets[0])[2]}
        for title, mets, hours in metrics.CHARTS
        if any(m in latest for m in mets)
    ]

    # instelbare indeling en historiekperiodes
    layout = metrics.parse_layout(db.get_setting("layout"))
    ranges = metrics.parse_ranges(db.get_setting("history_ranges"))
    blocks = []
    for item in layout:
        if not item["visible"]:
            continue
        key = item["key"]
        if key == "charts" and charts:
            blocks.append({"type": "charts", "charts": charts})
        elif key == "map" and neighbors and db.contact_location(r["pubkey_prefix"][:6]):
            blocks.append({"type": "map"})
        elif key == "neighbors" and neighbors:
            blocks.append({"type": "neighbors"})
        elif key in sections:
            blocks.append({"type": "section", "section": sections[key]})

    online_row = latest.get("online")
    session_cookie = request.cookies.get(auth.SESSION_COOKIE, "")
    is_admin = auth.read_session(session_cookie) is not None
    return templates.TemplateResponse(request, "repeater.html", {
        "site_name": config.SITE_NAME, "r": r, "blocks": blocks,
        "neighbors": neighbors, "gauges": metrics.GAUGES, "thermos": metrics.THERMOMETERS,
        "ranges": [{"hours": h, "label": metrics.range_label(h)} for h in ranges],
        "default_hours": 24 if 24 in ranges else ranges[0],
        "is_online": online_row is not None and online_row["value"] == 1.0,
        "is_admin": is_admin,
        "csrf": auth.csrf_token(session_cookie) if is_admin else "",
        "refresh_requested": request.query_params.get("refresh") == "1",
        "zones": {
            m: {"min": cfg[0], "max": cfg[1], "segments": cfg[2]}
            for m, cfg in {**metrics.GAUGES, **metrics.THERMOMETERS}.items()
        },
    })


def _tile(metric: str, label: str, unit: str | None, row) -> dict:
    value = row["value"]
    display = row["value_str"] or "—"
    if value is not None:
        if metric == "online":
            display = "Online" if value == 1.0 else "Offline"
        elif metric == "uptime":
            display = _fmt_uptime(value)
        elif value == int(value) and abs(value) < 1e9:
            display = f"{int(value):,}".replace(",", " ")
        else:
            display = f"{value:g}"
        if unit and metric not in ("online", "uptime"):
            display += f" {unit}"
    return {"metric": metric, "label": label, "value": value, "display": display, "ts": row["ts"]}


def _fmt_uptime(days: float) -> str:
    total_min = int(days * 24 * 60)
    d, rest = divmod(total_min, 24 * 60)
    h, m = divmod(rest, 60)
    if d:
        return f"{d} d {h} u"
    if h:
        return f"{h} u {m} min"
    return f"{m} min"
