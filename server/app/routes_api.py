"""JSON-API: ingest vanuit Home Assistant + publieke data-endpoints."""
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
    """Verbindingstest voor de Home Assistant-integratie."""
    require_token(authorization)
    return {"ok": True, "app": "mc-repeater-stats", "version": 1}


@router.post("/contacts")
async def contacts(request: Request, authorization: str | None = Header(default=None)):
    """Contactlocaties uit de meshcore-adverts: {"contacts": [{prefix,name,lat,lon,type}]}"""
    require_token(authorization)
    limit_body(request)
    body = await request.json()
    items = body.get("contacts")
    if not isinstance(items, list):
        raise HTTPException(422, "contacts moet een lijst zijn")
    return {"ok": True, "count": db.upsert_contacts(items)}


@router.get("/commands")
def commands(authorization: str | None = Header(default=None)):
    """Openstaande opdrachten voor de HA-integratie (clear-on-read):
    refresh = handmatige statusverzoeken; settings = CLI-settings-opvragingen."""
    require_token(authorization)
    return {"refresh": db.pop_refresh_requests(), "settings": db.pop_settings_requests()}


@router.post("/repeater_settings")
async def repeater_settings(request: Request, authorization: str | None = Header(default=None)):
    """CLI-instellingen van een repeater: {"repeater": {"pubkey_prefix"}, "settings": {param: waarde}}"""
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
    """Snapshot van één repeater. Payload:
    {
      "repeater": {"pubkey_prefix": "e3d3f4d7ed", "name": "BE-HSS-JessaZH.VIR"},
      "ts": "2026-08-07T12:00:00Z",            # optioneel, anders servertijd
      "metrics": {"bat": 4.15, "online": true, ...},
      "neighbors": [{"prefix": "2ae7af", "name": "...", "snr": -4.25}, ...]  # optioneel
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
    """Kaartdata: locatie van de repeater + alle buren met bekende locatie."""
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


@router.get("/repeaters/{slug}/history")
def repeater_history(
    slug: str,
    metric: str = Query(..., max_length=64),
    hours: int = Query(24, ge=1, le=2160),
):
    r = _public_repeater(slug)
    return {"metric": metric, "hours": hours, "points": db.history(r["id"], metric, hours)}
