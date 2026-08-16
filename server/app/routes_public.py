"""Public HTML pages."""
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse

from . import auth, commanding, config, db, metrics, search
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
        # Without a single placeable node the live map would be an empty grey
        # box, so the whole block (and Leaflet with it) stays out of the page.
        "has_livemap": bool(db.located_nodes()),
    })


@router.get("/pakketten", response_class=HTMLResponse)
def packets_page(request: Request):
    """The packet archive: query-bar search over everything still retained."""
    return templates.TemplateResponse(request, "packets.html", {
        "site_name": config.SITE_NAME,
        "span": db.packet_span(),
        "fields": search.describe_fields(),
        "sorts": search.describe_sorts(),
        "columns": search.describe_columns(),
        "retention_days": db.setting_int("packet_retention_days",
                                         config.PACKET_RETENTION_DAYS),
    })


@router.get("/r/{slug}", response_class=HTMLResponse)
def repeater_page(request: Request, slug: str):
    r = db.qone("SELECT * FROM repeaters WHERE slug=? AND is_public=1", (slug,))
    if not r:
        raise HTTPException(404, "Onbekende repeater")
    latest = db.latest_for(r["id"])

    # Compute utilisation from the airtime totals ourselves: the value Home
    # Assistant reports drops back to 0 after every HA restart, until its own
    # measurement window has been rebuilt.
    computed = {
        "airtime_utilization": db.computed_utilization(r, "airtime"),
        "rx_airtime_utilization": db.computed_utilization(r, "rx_airtime"),
    }

    # tiles per section
    sections: dict[str, dict] = {}
    used = set()
    for key, title in metrics.SECTIONS:
        tiles = []
        for m in metrics.TILE_METRICS.get(key, []):
            row = latest.get(m)
            if row is None:
                continue
            # Ch1 voltage measures the same battery as 'bat' -- drop the duplicate tile
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

    # name from the neighbour sensor, falling back to the contacts table (adverts)
    neighbors = db.q(
        "SELECT n.prefix, n.snr, n.last_seen, "
        "CASE WHEN n.name IS NULL OR lower(n.name) = n.prefix "
        "THEN COALESCE(c.name, n.name) ELSE n.name END AS name "
        "FROM neighbors n LEFT JOIN contacts c ON c.prefix6 = n.prefix "
        "WHERE n.repeater_id=? ORDER BY n.snr DESC",
        (r["id"],),
    )
    charts = [
        {"key": key, "title": title, "metrics": mets, "hours": hours,
         "labels": [metrics.metric_info(m)[1] for m in mets],
         "unit": metrics.metric_info(mets[0])[2]}
        for key, title, mets, hours in metrics.CHARTS
        if any(m in latest for m in mets)
    ]

    # configurable block order and history ranges
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
        # 'mqtt' | 'queued' | 'both' | 'none', en '1' uit oudere links. Wat er
        # werkelijk gebeurd is, want de knop kan tegenwoordig ook nergens heen,
        # en dan hoort de pagina dat te zeggen in plaats van een update te
        # beloven die niemand gaat halen.
        "refresh_state": ("both" if request.query_params.get("refresh") == "1"
                          else request.query_params.get("refresh", "")),
        # Alleen voor beheerders berekend: de knop staat er voor niemand anders.
        "route": commanding.describe(r) if is_admin else None,
        "zones": {
            m: {"min": cfg[0], "max": cfg[1], "segments": cfg[2]}
            for m, cfg in {**metrics.GAUGES, **metrics.THERMOMETERS}.items()
        },
    })


def _tile(metric: str, label: str, unit: str | None, row) -> dict:
    value = row["value"]
    display = row["value_str"] or "—"
    # Most tile values are numbers and read the same in every language; uptime is
    # the exception, so it carries a translation key alongside its Dutch text.
    i18n_key = i18n_vars = None
    if value is not None:
        if metric == "online":
            display = "Online" if value == 1.0 else "Offline"
        elif metric == "uptime":
            display, i18n_key, i18n_vars = _fmt_uptime(value)
        elif value == int(value) and abs(value) < 1e9:
            display = f"{int(value):,}".replace(",", " ")
        else:
            display = f"{value:g}"
        if unit and metric not in ("online", "uptime"):
            display += f" {unit}"
    hint_key, hint_text = metrics.HINTS.get(metric, (None, None))
    return {"metric": metric, "label": label, "value": value, "display": display,
            "ts": row["ts"], "i18n": i18n_key, "i18n_vars": i18n_vars,
            "hint": hint_text, "hint_key": hint_key}


def _fmt_uptime(days: float) -> tuple[str, str, dict]:
    """Dutch rendering plus the translation key and values behind it."""
    total_min = int(days * 24 * 60)
    d, rest = divmod(total_min, 24 * 60)
    h, m = divmod(rest, 60)
    if d:
        return f"{d} d {h} u", "fmt.uptime_dh", {"d": d, "h": h}
    if h:
        return f"{h} u {m} min", "fmt.uptime_hm", {"h": h, "m": m}
    return f"{m} min", "fmt.uptime_m", {"m": m}
