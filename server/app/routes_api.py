"""JSON API: ingest from Home Assistant plus the public data endpoints."""
import time

from fastapi import APIRouter, Header, HTTPException, Query, Request

from . import auth, config, countries, db, metrics, packets

router = APIRouter(prefix="/api/v1")

_ingest_count = 0


def require_token(authorization: str | None):
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(401, "Bearer-token vereist")
    if not auth.check_token(authorization.split(" ", 1)[1].strip()):
        raise HTTPException(403, "Ongeldig of ingetrokken token")


def limit_body(request: Request, max_bytes: int = config.MAX_BODY_BYTES):
    """Reject an oversized body on the strength of its declared length.

    Only a courtesy fast path: a chunked request declares no length at all, so
    the limit that actually holds is BodySizeLimitMiddleware, which counts the
    bytes as they arrive. Do not reintroduce a Content-Length requirement here --
    the header is optional, and demanding it broke nothing an attacker cares
    about while rejecting legitimate streaming clients.
    """
    declared = request.headers.get("content-length")
    if declared:
        try:
            if int(declared) > max_bytes:
                raise HTTPException(413, "Payload te groot")
        except ValueError:
            raise HTTPException(400, "Ongeldige Content-Length")


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
    # Same bookkeeping as the MQTT path, so the admin page never shows a stale
    # node prefix for a repeater that has since switched to HTTP ingest.
    db.record_source(row["id"], "api")

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


# Resolving a hop costs a database lookup, and the live feed resolves the path of
# every packet it hands out -- easily a few hundred lookups per poll per visitor,
# for answers that only change when a node we have never heard of advertises
# itself. Hence a short-lived memo: fresh enough that a new node shows up within
# the minute, cheap enough to survive a mesh that mirrors every frame it hears.
_HOP_CACHE_TTL_S = 60
_hop_cache: dict[str, dict] = {}
_hop_cache_filled = 0.0


def _resolve_hop(hop_hash: str) -> dict:
    """Work out which node a single path hop refers to -- honestly.

    A hop entry is not an identifier, it is the first one or two bytes of the
    forwarder's public key (see docs/protocol.md 1.4). One byte gives 256
    possible values while this site already knows several hundred nodes, so two
    nodes sharing a hop value is the normal case, not a data error.

    Therefore: never pick a "best" candidate. Report all of them and let the
    caller draw the difference between knowing and guessing.

    ``state`` is one of:
      known      exactly one node matches -- as certain as this protocol gets
      ambiguous  several nodes match; which one forwarded is not recoverable
      unknown    no node we have ever heard an advert from matches
    """
    global _hop_cache_filled
    now = time.monotonic()
    if now - _hop_cache_filled > _HOP_CACHE_TTL_S:
        _hop_cache.clear()
        _hop_cache_filled = now
    hit = _hop_cache.get(hop_hash)
    if hit is not None:
        return hit

    matches = [
        {"prefix": m["prefix6"], "name": m["name"], "lat": m["lat"], "lon": m["lon"],
         "node_type": m["node_type"]}
        for m in db.contacts_by_key_prefix(hop_hash)
    ]
    state = "known" if len(matches) == 1 else ("ambiguous" if matches else "unknown")
    hit = {"hash": hop_hash, "state": state, "matches": matches}
    _hop_cache[hop_hash] = hit
    return hit


def _hop_waypoint(hop_hash: str) -> dict:
    """The same resolution as _resolve_hop, reduced to what a moving dot needs.

    A position is handed out only for a hop that resolves to exactly one located
    node. Everything else keeps its state and no coordinates, so the client draws
    that stretch of the route as the guess-free gap it is.
    """
    hop = _resolve_hop(hop_hash)
    one = hop["matches"][0] if hop["state"] == "known" else None
    return {
        "hash": hop["hash"], "state": hop["state"],
        "lat": one["lat"] if one else None, "lon": one["lon"] if one else None,
    }


def _scope_codes(stored: str | None) -> list[int] | None:
    """The ``scope_codes`` column back as two numbers, or None."""
    parts = [p for p in (stored or "").split(",") if p]
    try:
        return [int(p) for p in parts] or None
    except ValueError:
        return None


def _scope_region(codes: list[int] | None) -> int | None:
    """The region a scoped packet names, if it names one at all.

    Only the second transport code can: the first is a MAC over the packet, so
    it differs per packet under one and the same scope key. The firmware that
    would fill the second one in still writes a literal zero there, so this is
    absent far more often than not -- which is a fact about the mesh worth
    reporting rather than papering over. See packets.py for the full story.
    """
    return codes[1] if codes and len(codes) > 1 and codes[1] else None


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

    Each packet carries its resolved path as well: the client animates packets
    along it, and looking that up per packet would mean one extra request per
    reception on a mesh that mirrors every frame it hears.
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
            # Whether the sender kept this packet inside a region. NULL on rows
            # stored before the column existed and whose frame was not kept, so
            # the client shows a dash rather than claiming "unscoped".
            "scope": p["scope"],
            # Rare enough that the list can afford to spell it out beside the
            # scope, and interesting enough that it should: the firmware that
            # would fill this in still writes a zero, so a packet that does name
            # its region is worth spotting without opening it.
            "scope_region": _scope_region(_scope_codes(p["scope_codes"])),
            "path_len": p["path_len"],
            "sender": p["sender"], "sender_name": p["sender_name"],
            "lat": lat, "lon": lon,
            "origin": None if lat is None else origin,
            "sender_lat": p["sender_lat"], "sender_lon": p["sender_lon"],
            "observer_lat": p["observer_lat"], "observer_lon": p["observer_lon"],
            "path": [_hop_waypoint(h) for h in (p["path"] or "").split(",") if h],
            # The country of whichever node the reception is attributed to, so
            # filtering by country matches the dot the visitor sees on the map.
            "country": (p["sender_country"] if origin == "sender"
                        else p["observer_country"]) if lat is not None else None,
        })
    out = {
        "last_id": items[-1]["id"] if items else (since_id or db.last_packet_id()),
        "packets": items,
    }
    if since_id <= 0:
        out["nodes"] = [
            {"prefix": n["prefix6"], "name": n["name"], "lat": n["lat"],
             "lon": n["lon"], "node_type": n["node_type"], "country": n["country"]}
            for n in db.located_nodes()
        ]
        # Absent when there are no borders to classify against, which is the
        # client's cue to leave the country filter out of the page entirely.
        if countries.available():
            out["countries"] = db.known_countries()
    return out


@router.get("/packets/{packet_id}")
def packet_detail(packet_id: int):
    """Everything known about one reception: the stored summary, the resolved
    path, and the frame re-decoded from the raw bytes.

    The advert fields are decoded on request rather than stored as columns:
    ``raw`` is the ground truth, and re-running the current decoder over it means
    a decoder fix immediately improves old packets instead of only new ones.
    """
    p = db.packet_by_id(packet_id)
    if p is None:
        raise HTTPException(404, "Onbekend pakket")

    raw = p["raw"]
    decoded: dict = {}
    if raw:
        try:
            decoded = packets.decode(bytes.fromhex(raw))
        except ValueError:
            decoded = {}   # stored hex that is not hex at all: show the rest anyway

    advert = None
    if decoded.get("payload_type") == packets.PAYLOAD_ADVERT:
        advert = {
            "name": decoded.get("name"), "lat": decoded.get("lat"),
            "lon": decoded.get("lon"), "node_type": decoded.get("node_type"),
            "ts": decoded.get("advert_ts"), "pubkey": decoded.get("pubkey"),
        }

    # Packets stored before the path column existed keep a path_len but no path;
    # the client says so rather than pretending the packet took no hops.
    hops = [h for h in (p["path"] or "").split(",") if h]

    # Same rule as the advert block: the frame decides where it can, the stored
    # column answers for the rows whose frame was never kept.
    scope = decoded.get("scope") or p["scope"]
    codes = decoded.get("transport_codes") or _scope_codes(p["scope_codes"])
    return {
        "id": p["id"], "ts": p["ts"],
        "observer": p["observer"], "observer_name": p["observer_name"],
        "observer_lat": p["observer_lat"], "observer_lon": p["observer_lon"],
        "observer_country": p["observer_country"],
        "snr": p["snr"], "rssi": p["rssi"], "len": p["len"],
        "route": p["route"], "payload_type": p["payload_type"], "type": p["payload_name"],
        "scope": scope, "scope_codes": codes, "scope_region": _scope_region(codes),
        "path_len": p["path_len"],
        "sender": p["sender"], "sender_name": p["sender_name"],
        "sender_lat": p["sender_lat"], "sender_lon": p["sender_lon"],
        "sender_country": p["sender_country"],
        "raw": raw,
        "path": [_resolve_hop(h) for h in hops],
        "path_stored": bool(p["path"]) or p["path_len"] == 0,
        "error": decoded.get("error"),
        "advert": advert,
    }


@router.get("/repeaters/{slug}/history")
def repeater_history(
    slug: str,
    metric: str = Query(..., max_length=64),
    hours: int = Query(24, ge=1, le=2160),
):
    r = _public_repeater(slug)
    return {"metric": metric, "hours": hours,
            "points": db.metric_history(r, metric, hours)}
