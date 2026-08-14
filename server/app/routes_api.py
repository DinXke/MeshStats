"""JSON API: ingest from Home Assistant plus the public data endpoints."""
from fastapi import APIRouter, Header, HTTPException, Query, Request

from . import auth, db, metrics

router = APIRouter(prefix="/api/v1")

_ingest_count = 0


def require_token(authorization: str | None):
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(401, "Bearer-token vereist")
    if not auth.check_token(authorization.split(" ", 1)[1].strip()):
        raise HTTPException(403, "Ongeldig of ingetrokken token")


def limit_body(request: Request, max_bytes: int = 2_000_000):
    try:
        if int(request.headers.get("content-length") or 0) > max_bytes:
            raise HTTPException(413, "Payload te groot")
    except ValueError:
        raise HTTPException(411, "Content-Length vereist")


@router.get("/ping")
def ping(authorization: str | None = Header(default=None)):
    """Connection test for the Home Assistant integration."""
    require_token(authorization)
    return {"ok": True, "app": "mc-repeater-stats", "version": 1}


@router.post("/contacts")
async def contacts(request: Request, authorization: str | None = Header(default=None)):
    """Contact positions from meshcore adverts: {"contacts": [{prefix,name,lat,lon,type}]}"""
    require_token(authorization)
    limit_body(request)
    body = await request.json()
    items = body.get("contacts")
    if not isinstance(items, list):
        raise HTTPException(422, "contacts moet een lijst zijn")
    return {"ok": True, "count": db.upsert_contacts(items)}


@router.get("/commands")
def commands(authorization: str | None = Header(default=None)):
    """Pending commands for the Home Assistant integration (clear on read):
    refresh = manual status requests, settings = CLI settings look-ups."""
    require_token(authorization)
    return {"refresh": db.pop_refresh_requests(), "settings": db.pop_settings_requests()}


@router.post("/repeater_settings")
async def repeater_settings(request: Request, authorization: str | None = Header(default=None)):
    """CLI settings of one repeater: {"repeater": {"pubkey_prefix"}, "settings": {param: value}}"""
    require_token(authorization)
    limit_body(request)
    body = await request.json()
    prefix = str((body.get("repeater") or {}).get("pubkey_prefix", "")).lower().strip()
    values = body.get("settings")
    if not prefix or not isinstance(values, dict):
        raise HTTPException(422, "repeater.pubkey_prefix en settings vereist")
    row = db.qone("SELECT id FROM repeaters WHERE pubkey_prefix=?", (prefix,))
    if not row:
        raise HTTPException(404, "Onbekende repeater")
    db.upsert_cli_settings(row["id"], values)
    return {"ok": True, "count": len(values)}


@router.post("/ingest")
async def ingest(request: Request, authorization: str | None = Header(default=None)):
    """Snapshot of one repeater. Payload:
    {
      "repeater": {"pubkey_prefix": "e3d3f4d7ed", "name": "BE-HSS-JessaZH.VIR"},
      "ts": "2026-08-07T12:00:00Z",            # optional, server time otherwise
      "metrics": {"bat": 4.15, "online": true, ...},
      "neighbors": [{"prefix": "2ae7af", "name": "...", "snr": -4.25}, ...]  # optional
    }
    """
    require_token(authorization)
    limit_body(request)
    body = await request.json()
    rep = body.get("repeater") or {}
    prefix = str(rep.get("pubkey_prefix", "")).lower().strip()
    if not prefix:
        raise HTTPException(422, "repeater.pubkey_prefix ontbreekt")
    mets = body.get("metrics") or {}
    if not isinstance(mets, dict):
        raise HTTPException(422, "metrics moet een object zijn")
    row = db.get_or_create_repeater(prefix, rep.get("name"))
    ts = body.get("ts") or db.utcnow()
    db.ingest(row["id"], ts, mets, body.get("neighbors"), force=bool(body.get("force")))

    global _ingest_count
    _ingest_count += 1
    if _ingest_count % 500 == 1:
        db.prune()
    return {"ok": True, "repeater": row["slug"]}


def _public_repeater(slug: str):
    row = db.qone("SELECT * FROM repeaters WHERE slug=? AND is_public=1", (slug,))
    if not row:
        raise HTTPException(404, "Onbekende repeater")
    return row


@router.get("/repeaters")
def list_repeaters():
    rows = db.q("SELECT * FROM repeaters WHERE is_public=1 ORDER BY sort_order, name")
    out = []
    for r in rows:
        latest = db.latest_for(r["id"])
        def val(m):
            row = latest.get(m)
            return None if row is None else (row["value"] if row["value"] is not None else row["value_str"])
        out.append({
            "slug": r["slug"], "name": r["name"], "pubkey_prefix": r["pubkey_prefix"],
            "last_seen": r["last_seen"],
            "online": val("online") == 1.0,
            "battery_percentage": val("battery_percentage"),
            "uptime": val("uptime"),
            "neighbor_count": val("neighbor_count"),
        })
    return out


@router.get("/repeaters/{slug}")
def repeater_detail(slug: str):
    r = _public_repeater(slug)
    latest = db.latest_for(r["id"])
    mets = {}
    for name, row in latest.items():
        section, label, unit, sort = metrics.metric_info(name)
        mets[name] = {
            "value": row["value"] if row["value"] is not None else row["value_str"],
            "ts": row["ts"], "label": label, "unit": unit, "section": section, "sort": sort,
        }
    neighbors = [
        {"prefix": n["prefix"], "name": n["name"], "snr": n["snr"], "last_seen": n["last_seen"]}
        for n in db.q(
            "SELECT n.prefix, n.snr, n.last_seen, "
            "CASE WHEN n.name IS NULL OR lower(n.name) = n.prefix "
            "THEN COALESCE(c.name, n.name) ELSE n.name END AS name "
            "FROM neighbors n LEFT JOIN contacts c ON c.prefix6 = n.prefix "
            "WHERE n.repeater_id=? ORDER BY n.snr DESC",
            (r["id"],),
        )
    ]
    return {
        "slug": r["slug"], "name": r["name"], "pubkey_prefix": r["pubkey_prefix"],
        "last_seen": r["last_seen"], "metrics": mets, "neighbors": neighbors,
    }


@router.get("/repeaters/{slug}/map")
def repeater_map(slug: str):
    """Map data: the repeater's position plus every neighbour we can place."""
    r = _public_repeater(slug)
    home = db.contact_location(r["pubkey_prefix"][:6])
    links = []
    unlocated = []
    for n in db.q(
        "SELECT n.prefix, n.snr, n.last_seen, "
        "CASE WHEN n.name IS NULL OR lower(n.name) = n.prefix "
        "THEN COALESCE(c.name, n.name) ELSE n.name END AS name "
        "FROM neighbors n LEFT JOIN contacts c ON c.prefix6 = n.prefix "
        "WHERE n.repeater_id=?",
        (r["id"],),
    ):
        loc = db.contact_location(n["prefix"])
        if loc is None:
            unlocated.append(n["name"] or n["prefix"].upper())
            continue
        links.append({
            "prefix": n["prefix"], "name": n["name"] or loc["name"],
            "snr": n["snr"], "last_seen": n["last_seen"],
            "lat": loc["lat"], "lon": loc["lon"], "node_type": loc["node_type"],
        })
    return {
        "repeater": None if home is None else
            {"name": r["name"], "lat": home["lat"], "lon": home["lon"]},
        "links": links,
        "unlocated": len(unlocated),
        "unlocated_names": sorted(unlocated, key=str.lower),
    }


@router.get("/packets")
def packet_feed(
    since_id: int = Query(0, ge=0),
    limit: int = Query(200, ge=1, le=500),
):
    """Overheard LoRa packets newer than ``since_id``, for the live map.

    Polled rather than pushed: a few seconds of latency costs nothing here, and
    plain polling survives proxies, sleeping laptops and restarts that SSE or
    websockets would each need their own reconnect handling for.

    The first call (since_id=0) also returns every node position we know, so the
    map can draw its base layer from the same request.
    """
    rows = db.recent_packets(since_id, limit)
    items = []
    for p in rows:
        # Only adverts identify their sender; for everything else the observer's
        # own position is the most honest place to show the reception.
        lat, lon, origin = p["sender_lat"], p["sender_lon"], "sender"
        if lat is None or lon is None:
            lat, lon, origin = p["observer_lat"], p["observer_lon"], "observer"
        items.append({
            "id": p["id"], "ts": p["ts"],
            "observer": p["observer"], "observer_name": p["observer_name"],
            "snr": p["snr"], "rssi": p["rssi"], "len": p["len"],
            "route": p["route"], "type": p["payload_name"],
            "path_len": p["path_len"],
            "sender": p["sender"], "sender_name": p["sender_name"],
            "lat": lat, "lon": lon,
            "origin": None if lat is None else origin,
        })
    out = {
        "last_id": items[-1]["id"] if items else (since_id or db.last_packet_id()),
        "packets": items,
    }
    if since_id <= 0:
        out["nodes"] = [
            {"prefix": n["prefix6"], "name": n["name"], "lat": n["lat"],
             "lon": n["lon"], "node_type": n["node_type"]}
            for n in db.located_nodes()
        ]
    return out


@router.get("/repeaters/{slug}/history")
def repeater_history(
    slug: str,
    metric: str = Query(..., max_length=64),
    hours: int = Query(24, ge=1, le=2160),
):
    r = _public_repeater(slug)
    return {"metric": metric, "hours": hours, "points": db.history(r["id"], metric, hours)}
