"""JSON API: ingest from Home Assistant plus the public data endpoints."""
import logging
import re
import time
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Header, HTTPException, Query, Request

from . import auth, candidates, config, countries, db, metrics, packets, search

router = APIRouter(prefix="/api/v1")

log = logging.getLogger(__name__)

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
    """Pending commands for a polling client -- Home Assistant today (clear on read):
    refresh = manual status requests, settings = CLI settings look-ups.

    Handing work out is logged, because this is a clear-on-read queue: once the
    poller has taken a request there is no trace of it left anywhere, and the
    only remaining question when nothing happens afterwards -- did the poller
    ever receive it? -- has to be answerable from the journal.

    Every poll is written down as well, the empty ones included. That is what
    tells the admin page whether there is anyone out there to hand a request to
    at all: an unpolled queue looks exactly like one that was emptied a second
    ago, and while nothing was polling, the page kept promising the second.
    """
    require_token(authorization)
    db.note_poller_seen()
    refresh = db.pop_refresh_requests()
    settings = db.pop_settings_requests()
    if refresh or settings:
        log.info("Wachtrij uitgereikt aan poller: %s statusverzoek(en), "
                 "%s instellingenopvraging(en) voor %s",
                 len(refresh), len(settings),
                 ", ".join(s["prefix"] for s in settings) or "-")
    return {"refresh": refresh, "settings": settings}


@router.post("/repeater_settings")
async def repeater_settings(request: Request, authorization: str | None = Header(default=None)):
    """CLI settings of one repeater: {"repeater": {"pubkey_prefix"}, "settings": {param: value}}

    The look-up goes through ``find_repeater`` rather than an equality test on
    the key, for the same reason every other ingest path does: sources disagree
    on how much of the public key they send -- Home Assistant 5 bytes, a node's
    own firmware 6 -- and the stored key grows to the longest one seen. A strict
    match therefore starts answering 404 to Home Assistant the moment the same
    node also reports over MQTT, throwing away a settings sweep that costs one
    to two minutes of LoRa airtime to produce.
    """
    require_token(authorization)
    limit_body(request)
    body = await request.json()
    prefix = str((body.get("repeater") or {}).get("pubkey_prefix", "")).lower().strip()
    values = body.get("settings")
    if not prefix or not isinstance(values, dict):
        raise HTTPException(422, "repeater.pubkey_prefix en settings vereist")
    row = db.find_repeater(prefix)
    if not row:
        log.warning("Instellingen geweigerd: geen repeater met sleutel %s", prefix)
        raise HTTPException(404, "Onbekende repeater")
    answered = sum(1 for v in values.values() if v is not None)
    log.info("Instellingen ontvangen voor %s: %s van %s parameters beantwoord",
             row["slug"], answered, len(values))
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
    db.record_firmware(row["id"], rep.get("fw"), rep.get("fw_meshstats"))

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
#
# The key carries the observer and the hop bound as well as the hash, because the
# same byte resolves differently depending on who heard the packet and what the
# frame says about how far away the node can be. Both are small, repeating values
# -- one observer per node, hop bounds in the single digits -- so the memo still
# collapses a feed's worth of packets onto a handful of entries.
_HOP_CACHE_TTL_S = 60
_hop_cache: dict[tuple, dict] = {}
_observer_cache: dict[str, dict] = {}
_hop_cache_filled = 0.0


def _expire_caches() -> None:
    """Drop both memos together once the TTL is up.

    Together on purpose: a resolution is a function of the observer context it
    was computed from, and letting one expire without the other would serve
    rankings built on evidence that had already been refreshed.
    """
    global _hop_cache_filled
    now = time.monotonic()
    if now - _hop_cache_filled > _HOP_CACHE_TTL_S:
        _hop_cache.clear()
        _observer_cache.clear()
        _hop_cache_filled = now


def _observer_context(observer: str | None) -> dict:
    """What one observer knows of the mesh, as the weighing needs it.

    Two lookups -- the observer's own position and the nodes it has actually
    heard -- shared by every hash resolved for that observer in this minute. The
    reception table is a scan of the packets table, which is far too much work
    to repeat per packet in a feed of hundreds.
    """
    key = (observer or "").lower()
    hit = _observer_cache.get(key)
    if hit is not None:
        return hit
    prefix6 = key[:6]
    home = db.contact_location(prefix6) if prefix6 else None
    hit = {
        "prefix6": prefix6 or None,
        "pos": (home["lat"], home["lon"]) if home else None,
        "evidence": db.observer_receptions(key) if key else {},
    }
    _observer_cache[key] = hit
    return hit


def _resolve_hop(hop_hash: str, observer: str | None = None,
                 role: str = "hop", route: str | None = None,
                 path_len: int | None = None, index: int | None = None) -> dict:
    """Work out which node a single address hash refers to -- honestly.

    A hop entry, a src_hash and a dest_hash are all the same kind of thing: the
    first one or two bytes of a public key (see docs/protocol.md 1.4). One byte
    gives 256 possible values while this site already knows several hundred
    nodes, so two nodes sharing a value is the normal case, not a data error.

    What we may not do is quietly pick a favourite and print it as fact. What we
    may do -- and now do -- is drop the candidates the frame itself places out of
    reach, and order the rest by evidence with the reason attached. The weighing,
    and the reasons it is allowed to lean on, live in app/candidates.py; this
    function only supplies it with the observer's context and the bound that
    follows from this packet's route and hop count.

    ``state`` is one of:
      known      one node stands -- either the only match, or the last one left
                 after an exclusion. Still derived from one byte, never stated.
      likely     several stand, and the evidence puts one above the rest;
                 ``lead`` names the signal that did it
      ambiguous  several stand and nothing separates them: which one it was is
                 not recoverable
      unknown    nothing stands: no contact matches, or all of them were excluded
    """
    _expire_caches()
    bound = candidates.radio_hop_bound(role, route, path_len, index)
    key = (hop_hash, (observer or "").lower(), bound)
    hit = _hop_cache.get(key)
    if hit is not None:
        return hit

    ctx = _observer_context(observer)
    weighed = candidates.weigh(
        [{"prefix": m["prefix6"], "name": m["name"], "lat": m["lat"],
          "lon": m["lon"], "node_type": m["node_type"], "updated": m["updated"]}
         for m in db.contacts_by_key_prefix(hop_hash)],
        evidence=ctx["evidence"], observer6=ctx["prefix6"],
        observer_pos=ctx["pos"], bound=bound,
    )
    hit = {"hash": hop_hash, **weighed}
    _hop_cache[key] = hit
    return hit


def _hop_waypoint(hop_hash: str, observer: str | None = None,
                  route: str | None = None, path_len: int | None = None,
                  index: int | None = None) -> dict:
    """The same resolution as _resolve_hop, reduced to what a moving dot needs.

    A position is handed out only for a hop that resolves to exactly one located
    node. Everything else keeps its state and no coordinates, so the client draws
    that stretch of the route as the guess-free gap it is -- and ``likely`` is
    deliberately on the wrong side of that line. A ranking is good enough to name
    a probable node in words next to the reason it is probable; it is not good
    enough to draw a line on a map, where the reason does not travel with it.
    """
    hop = _resolve_hop(hop_hash, observer, "hop", route, path_len, index)
    one = hop["matches"][0] if hop["state"] == "known" else None
    return {
        "hash": hop["hash"], "state": hop["state"],
        "lat": one["lat"] if one else None, "lon": one["lon"] if one else None,
    }


def _hops(stored: str | None) -> list[str]:
    """The stored ``path`` column back as a list of hop hashes.

    The position in that list is not decoration: it is how far along the route a
    hop sits, and therefore half of the bound its candidates are weighed against.
    Hence one helper, so the two callers cannot drift into indexing differently.
    """
    return [h for h in (stored or "").split(",") if h]


def _scope_codes(stored: str | None) -> list[int] | None:
    """The ``scope_codes`` column back as two numbers, or None."""
    parts = [p for p in (stored or "").split(",") if p]
    try:
        return [int(p) for p in parts] or None
    except ValueError:
        return None


def _trim(res: dict, limit: int = 6) -> dict:
    """A resolution reduced to what a list row needs: names, and the counts the
    reader has to be told about.

    Coordinates and timestamps go; the ranking's own signals stay, because they
    are what the row's tooltip says the order was built from. ``total`` survives
    the trim so a row can still say how many candidates there were even when it
    only prints the first few.
    """
    return {
        "hash": res["hash"], "state": res["state"], "lead": res.get("lead"),
        "total": len(res["matches"]),
        "matches": [{"prefix": m["prefix"], "name": m["name"], "hops": m["hops"],
                     "km": m["km"]} for m in res["matches"][:limit]],
        "dropped": [{"prefix": d["prefix"], "name": d["name"], "km": d["km"],
                     "why": d["why"]} for d in res["dropped"][:limit]],
        "dropped_total": len(res["dropped"]),
    }


def _resolve_src(row) -> dict | None:
    """Who a packet's 1-byte source hash could be, or None when there is nothing
    to resolve -- an advert already names its sender in full, and an ACK carries
    no identity at all.

    Reuses the hop resolver: a src_hash is exactly a hop-sized key prefix, and
    the honesty rules are identical. Only the role differs, and the role is what
    decides whether the frame bounds how far away the node can be -- on a flood
    the path counts backwards to the originator, so it does.
    """
    if row["sender"]:
        return None
    src = row["src_hash"]
    if not src:
        return None
    return _trim(_resolve_hop(src, row["observer"], "src",
                              row["route"], row["path_len"]))


def _resolve_dest(row) -> dict | None:
    """Who a packet's 1-byte destination hash could be, or None when it has none.

    The mirror of _resolve_src, and it has to be a mirror rather than a shared
    call with a role argument passed in from outside: the role is what tells the
    resolver which way the frame bounds the distance. On a flood the path counts
    backwards to the originator, so it bounds where the packet came *from*; the
    destination is bounded on a direct instead. Getting those two the wrong way
    round would exclude the innocent, which is why neither caller may choose.
    """
    dest = row["dest_hash"]
    if not dest:
        return None
    return _trim(_resolve_hop(dest, row["observer"], "dest",
                              row["route"], row["path_len"]))


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

    The first call (since_id=0) returns the newest ``limit`` packets rather
    than the oldest stored ones, so a freshly loaded page opens on the present
    instead of replaying hours of history page by page. Either way the packets
    arrive ascending by id and ``last_id`` is the highest id in the response,
    so the next poll picks up exactly where this one ended.

    That first call also returns every node position we know, so the map can
    draw its base layer from the same request.

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
            # For everything that is not an advert, the closest thing to a
            # sender the frame has: the 1-byte source hash resolved against the
            # contacts we know. Absent when the packet type carries none.
            "src": _resolve_src(p),
            "lat": lat, "lon": lon,
            "origin": None if lat is None else origin,
            "sender_lat": p["sender_lat"], "sender_lon": p["sender_lon"],
            "observer_lat": p["observer_lat"], "observer_lon": p["observer_lon"],
            "path": [_hop_waypoint(h, p["observer"], p["route"], p["path_len"], i)
                     for i, h in enumerate(_hops(p["path"]))],
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


@router.get("/packets/search")
def packet_search(
    q: str = Query("", max_length=500),
    since: str = Query("", max_length=32),
    until: str = Query("", max_length=32),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0, le=100_000),
    facets: str = Query("", max_length=200),
    sort: str = Query("", max_length=40),
):
    """Search the packet archive: rows, total, histogram and facets in one call.

    One call rather than four, because they all answer the same query and a
    page that fires four requests per keystroke of refinement would show them
    resolving at different moments -- a count that briefly disagrees with the
    bars above it reads as a broken search.

    ``sort`` is ``field`` or ``field:asc|desc``, and it orders the rows only.
    The total, the histogram and the facets describe the whole result set, and a
    set does not change by being listed in another order, so they come back
    exactly as they were -- clicking a heading must not make the bar chart
    flicker or the counts move. Ordering does bear on ``offset``: page 5 of one
    order has nothing to do with page 5 of another, so the page resets it.

    Every row carries every field the table can put in a column, whether or not
    the reader has that column switched on. That is a deliberate trade: the page
    lets columns be added and removed on the fly, and a response shaped by the
    current choice would turn each tick of a checkbox into a round trip, with a
    table that blinks and a spinner for data the browser already had. The extra
    weight is small -- a hop list, a payload hash, a destination hash and its
    candidates -- because the one genuinely heavy field is left out on purpose:
    ``raw``, the complete frame in hex, roughly doubles a packet row and has no
    column to appear in. It stays where it belongs, on the detail endpoint for
    the one packet somebody actually opened.

    A query the parser refuses comes back as a 200 with ``error`` set: for this
    endpoint a typo in the query is a normal outcome to render next to the box,
    not an exceptional one worth a 4xx that shows up as noise in proxy logs. An
    impossible sort travels the same road -- most often it is an old link naming
    a column that has since been dropped, which belongs beside the query bar and
    not in a proxy log.
    """
    try:
        parsed = search.parse(q)
        order = search.parse_sort(sort)
    except search.QueryError as err:
        return {"error": str(err), "fields": search.describe_fields()}

    since_ts = _clean_ts(since) or (
        datetime.now(timezone.utc) - timedelta(hours=24)).strftime("%Y-%m-%dT%H:%M:%SZ")
    until_ts = _clean_ts(until) or "9999-12-31T23:59:59Z"

    rows = db.search_packets(parsed, since_ts, until_ts, limit, offset, order)
    total = db.count_packets(parsed, since_ts, until_ts)

    # Bucket size follows the window so the chart always has on the order of
    # sixty bars: per-minute over an hour, per-hour over days.
    window_s = max(60, _window_seconds(since_ts, until_ts, rows))
    bucket_s = max(60, window_s // 60)
    histogram = db.packet_histogram(parsed, since_ts, until_ts, bucket_s)

    facet_out = {}
    for name in [f for f in facets.split(",") if f][:6]:
        field = search.FIELDS.get(name)
        if field is None or not field.facet:
            continue   # not an error: an old bookmark may name a field that was renamed
        column = search.REGION_SQL if name == "region" else field.sql
        facet_out[name] = db.packet_facets(parsed, since_ts, until_ts, column)

    return {
        "total": total,
        "offset": offset,
        # The ordering actually used, normalised. The page reads it back rather
        # than trusting what it asked for, so that the arrow in the heading and
        # the rows underneath it can never disagree about the direction.
        "sort": order.token,
        "bucket_s": bucket_s,
        "histogram": histogram,
        "facets": facet_out,
        "packets": [{
            "id": p["id"], "ts": p["ts"],
            "observer": p["observer"], "observer_name": p["observer_name"],
            "snr": p["snr"], "rssi": p["rssi"], "len": p["len"],
            "route": p["route"], "type": p["payload_name"],
            "scope": p["scope"],
            "scope_region": _scope_region(_scope_codes(p["scope_codes"])),
            "path_len": p["path_len"], "path": p["path"],
            "sender": p["sender"], "sender_name": p["sender_name"],
            "src": _resolve_src(p), "src_hash": p["src_hash"],
            "dest_hash": p["dest_hash"], "dest": _resolve_dest(p),
            "phash": p["phash"],
            "country": p["sender_country"] or p["observer_country"],
        } for p in rows],
    }


# A retention-window of paths is one answer for every visitor, and resolving
# that many hops is the expensive half of computing it -- so the finished
# response is memoised whole. The TTL is five minutes rather than the old
# minute: the window grew from a day to the full retention, so each pass is
# roughly seven times heavier, and the client refreshes on a five-minute clock
# anyway -- a shorter server TTL would redo the pass for readers who can never
# see the difference. Incremental aggregation was considered and rejected:
# counts would also have to *shrink* as packets age past retention, which means
# keeping a timestamp per traversal per segment -- at that point the bookkeeping
# costs more than simply redoing a pass that finishes in seconds.
_HEATMAP_TTL_S = 300
# The window is the full packet retention (7 days by default): the overlay
# answers "which links carry this mesh", and a link that is exercised every
# other day is part of the answer even when the last 24 hours happened to miss
# it. The earlier day-long window systematically hid exactly those slower
# links. Anything older than the retention is gone from the table regardless,
# so this is the widest honest window there is.
#
# A function and not a constant, and that is the point of it. The retention is
# an admin-page setting now (db.retention_settings()), so a reader who raises it
# to 30 days expects the heat map to start covering 30 days -- not after a
# container restart, but on the next pass. A module-level constant reads .env
# once at import and would keep answering with the value the process started
# with, which is exactly the kind of quiet disagreement between a setting and a
# graph that costs an evening to find.


def _heatmap_window_h() -> int:
    return db.retention_settings()["days"] * 24


# The row cap is a guard against a mesh that mirrors every frame it hears, not
# a number a healthy week gets near: the old day-window used 20 000, so a week
# gets ten times that, with headroom. When the cap does bite the response says
# so (``capped``) instead of quietly showing a truncated week as if it were
# complete -- the same no-silent-lies rule the aggregation itself follows.
_HEATMAP_MAX_PACKETS = 200000
_heatmap_cache: dict = {"at": 0.0, "data": None}


def _heat_stop(prefix, name, lat, lon) -> dict | None:
    """A placeable stop along a packet's path, or None where honesty forbids one."""
    if not prefix or lat is None or lon is None:
        return None
    return {"prefix": prefix, "name": name, "lat": lat, "lon": lon}


@router.get("/packets/heatmap")
def packet_heatmap():
    """Link usage over the full packet retention window, aggregated for the
    heat-map overlay.

    One segment per pair of *consecutively placeable* stops along each packet's
    path (sender -> hops -> observer), counted once per traversal. The same
    honesty rule as the drawn route applies: an ambiguous or unknown hop has no
    position we are entitled to use, so it breaks the chain rather than being
    bridged. A single packet's route can afford a dashed guess across such a
    gap; here the guess would be counted and recounted into a solid,
    authoritative-looking line, which is exactly the lie a heat map must not
    tell.

    Segments are undirected -- a link's load is the traffic over it, whichever
    way it went -- and sorted lightest first, so a client drawing them in order
    puts the heavy ones on top. The ascending order is load-bearing beyond
    draw order: the client's rank scale reads a segment's position in this
    list as its rank, which is what makes that scale free to compute.
    """
    now = time.monotonic()
    window_h = _heatmap_window_h()
    # The window is part of the cache key, not just of the answer. Without that,
    # changing the retention on the admin page would leave up to five minutes of
    # a cached overlay that quietly still covers the old period -- and the
    # response says which window it is for, so it would be five minutes of the
    # page stating a number that no longer matches the setting.
    cached = _heatmap_cache["data"]
    if (cached is not None and cached.get("window_h") == window_h
            and now - _heatmap_cache["at"] < _HEATMAP_TTL_S):
        return cached

    since = (datetime.now(timezone.utc)
             - timedelta(hours=window_h)).strftime("%Y-%m-%dT%H:%M:%SZ")
    counts: dict[tuple[str, str], int] = {}
    nodes: dict[str, dict] = {}
    counted = 0
    rows = db.packets_with_paths(since, _HEATMAP_MAX_PACKETS)
    for p in rows:
        stops = [_heat_stop(p["sender"], p["sender_name"],
                            p["sender_lat"], p["sender_lon"])]
        for h in (p["path"] or "").split(","):
            if not h:
                continue
            # Deliberately without an observer or a route: the heat map only
            # ever uses a hop that resolves to exactly one node, so a ranking
            # would change nothing here, and the query behind it is a lean one
            # that does not carry those columns. A resolution that stayed
            # ambiguous before still stays out of the aggregation now.
            hop = _resolve_hop(h)
            one = hop["matches"][0] if hop["state"] == "known" else None
            stops.append(_heat_stop(one["prefix"], one["name"],
                                    one["lat"], one["lon"]) if one else None)
        stops.append(_heat_stop(p["observer6"], p["observer_name"],
                                p["observer_lat"], p["observer_lon"]))

        contributed = False
        for a, b in zip(stops, stops[1:]):
            # A stop equal to its neighbour (the observer is often the last hop)
            # would count a zero-length link; skipping the pair collapses the
            # repeat without breaking the chain around it.
            if a is None or b is None or a["prefix"] == b["prefix"]:
                continue
            nodes.setdefault(a["prefix"], a)
            nodes.setdefault(b["prefix"], b)
            key = ((a["prefix"], b["prefix"]) if a["prefix"] < b["prefix"]
                   else (b["prefix"], a["prefix"]))
            counts[key] = counts.get(key, 0) + 1
            contributed = True
        if contributed:
            counted += 1

    segments = [{"a": nodes[k[0]], "b": nodes[k[1]], "n": n}
                for k, n in counts.items()]
    segments.sort(key=lambda s: s["n"])
    data = {
        "window_h": window_h,
        "packets": counted,
        # An exactly-full result means the query stopped at the cap, so older
        # packets in the window went uncounted. The client is told, so it can
        # say so next to the toggle instead of presenting a truncated week as
        # the whole one. (A result of exactly the cap without truncation is
        # possible but indistinguishable, and warning one time too often is
        # the honest side to err on.)
        "capped": len(rows) >= _HEATMAP_MAX_PACKETS,
        "max": segments[-1]["n"] if segments else 0,
        "segments": segments,
    }
    _heatmap_cache["at"] = now
    _heatmap_cache["data"] = data
    return data


def _clean_ts(value: str) -> str | None:
    """An ISO timestamp in the storage format, or None for anything else."""
    v = (value or "").strip()
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(:\d{2})?Z?", v):
        return v if v.endswith("Z") else v + ("Z" if len(v) == 20 else ":00Z")
    return None


def _window_seconds(since_ts: str, until_ts: str, rows) -> int:
    """The searched window in seconds; an open end is capped at now."""
    try:
        lo = datetime.strptime(since_ts, "%Y-%m-%dT%H:%M:%SZ")
        hi = (datetime.now(timezone.utc).replace(tzinfo=None)
              if until_ts.startswith("9999")
              else datetime.strptime(until_ts, "%Y-%m-%dT%H:%M:%SZ"))
        return int((hi - lo).total_seconds())
    except ValueError:
        return 24 * 3600


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
    hops = _hops(p["path"])

    # Same rule as the advert block: the frame decides where it can, the stored
    # column answers for the rows whose frame was never kept.
    scope = decoded.get("scope") or p["scope"]
    codes = decoded.get("transport_codes") or _scope_codes(p["scope_codes"])

    # Source and destination candidates from the 1-byte payload hashes. The
    # destination gets the same treatment as the source: "who was this for" is
    # the second question anyone with a packet open asks.
    src_hash = decoded.get("src_hash") or p["src_hash"] or None
    dest_hash = decoded.get("dest_hash") or p["dest_hash"] or None
    return {
        "id": p["id"], "ts": p["ts"],
        "observer": p["observer"], "observer_name": p["observer_name"],
        "observer_lat": p["observer_lat"], "observer_lon": p["observer_lon"],
        "observer_country": p["observer_country"],
        "snr": p["snr"], "rssi": p["rssi"], "len": p["len"],
        "route": p["route"], "payload_type": p["payload_type"], "type": p["payload_name"],
        "scope": scope, "scope_codes": codes, "scope_region": _scope_region(codes),
        "path_len": p["path_len"],
        # How many bytes each hop below is written with. Only the frame knows --
        # it is the top two bits of the descriptor, chosen by whoever first sent
        # the packet out of its own hash_mode -- so a row whose raw bytes were
        # never kept has no answer here, and None is that answer rather than a
        # plausible-looking 1. The client needs it because one hop of two bytes
        # and two hops of one byte print as the same four hex characters.
        "path_hash_size": decoded.get("path_hash_size"),
        "sender": p["sender"], "sender_name": p["sender_name"],
        "sender_lat": p["sender_lat"], "sender_lon": p["sender_lon"],
        "sender_country": p["sender_country"],
        # Source and destination are weighed against what this observer has
        # really heard, and against what the frame's own route type allows: a
        # flood bounds where the packet came from, a direct bounds where it is
        # going. See candidates.radio_hop_bound -- getting those two the wrong
        # way round would exclude the innocent.
        "src": _resolve_hop(src_hash, p["observer"], "src", p["route"],
                            p["path_len"]) if src_hash and not p["sender"] else None,
        "dest": _resolve_hop(dest_hash, p["observer"], "dest", p["route"],
                             p["path_len"]) if dest_hash else None,
        "raw": raw,
        "path": [_resolve_hop(h, p["observer"], "hop", p["route"], p["path_len"], i)
                 for i, h in enumerate(hops)],
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


# --- one node, everything this site knows about it ----------------------------
# Behind a dot on the live map. Answered in one request rather than the five it
# is assembled from, because the panel opens on a click and five round trips
# would fill it field by field -- and half-filled panels are read as "this node
# has no neighbours" long before the last response lands.
#
# Note what is *not* here. There is no node-level history: only the two tracked
# repeaters have samples, and those already have a whole page of charts at
# /r/<slug>, which this endpoint links to instead of copying. And there is no
# attempt to total up "packets sent" beyond what an advert states -- see
# ``sent`` below.

# A neighbour list in a side panel is a summary. A tracked repeater's own page
# shows the full table with per-link history, and it is one click away, so the
# panel stops well before it turns into a second copy of it.
_NODE_NEIGHBOR_LIMIT = 12

# The map's node layer is keyed on the six-hex prefix, so that is what comes
# back through a click. Longer keys are accepted and cut down: an operator with
# a full key prefix in hand should not have to work out which six characters the
# API wants.
_NODE_KEY_RE = re.compile(r"[0-9a-fA-F]{6,64}")


def _round(value, digits: int):
    """A number rounded for display, passing None through untouched.

    None means "nothing measured this" and has to survive to the client as
    null: a 0.0 in its place would be read as a measurement of zero, which for
    an SNR is a perfectly plausible and completely wrong reading.
    """
    return None if value is None else round(value, digits)


def _node_identity(rows) -> dict:
    """One identity out of however many contact rows this node owns.

    Merged rather than picked, because the rows are not rivals: one source may
    know the name and another the position (Home Assistant pushes contacts
    without ever hearing an advert), and the rows are keyed on prefixes of
    different length so neither can overwrite the other. First non-empty wins
    per field, and the caller ordered the rows longest key first -- the longest
    key is the least ambiguous identity, so it leads.

    ``updated`` is the newest of all of them: it answers "when did we last hear
    anything of this node", and taking it from the leading row alone would
    report a node as stale because its most talkative key happens to be short.
    """
    out = {"key_prefix": None, "name": None, "node_type": None,
           "country": None, "lat": None, "lon": None, "updated": None}
    for r in rows:
        if out["key_prefix"] is None:
            out["key_prefix"] = r["prefix"]
        for field in ("name", "node_type", "country"):
            if out[field] is None and r[field]:
                out[field] = r[field]
        if out["lat"] is None and r["lat"] is not None and r["lon"] is not None:
            out["lat"], out["lon"] = r["lat"], r["lon"]
        if r["updated"] and (out["updated"] is None or r["updated"] > out["updated"]):
            out["updated"] = r["updated"]
    return out


def _node_sent(prefix6: str) -> dict:
    """This node's own traffic, as far as anything can be attributed to it.

    "As far as" is the whole caveat, and the client repeats it: ``sender`` is
    filled from adverts only, because an advert is the one payload that names
    its origin by a full key prefix. Everything else this node ever transmitted
    carries a one-byte source hash that several hundred known nodes share, and
    counting those in would produce a bigger, friendlier number that is partly
    somebody else's traffic. A ceiling and a floor side by side was considered
    and rejected: two numbers whose difference is pure ambiguity invite the
    reader to average them.

    The totals are folded out of the per-observer rows rather than fetched with
    a second aggregate query. That is a loop over as many rows as there are
    observers -- a handful -- and not over the receptions themselves, which is
    the loop that would matter.
    """
    per_observer = db.node_sent_by_observer(prefix6)
    total = sum(r["n"] for r in per_observer)
    observers = [{
        "prefix": r["observer6"], "observer": r["observer"],
        "name": r["observer_name"], "count": r["n"],
        "first": r["first_ts"], "last": r["last_ts"],
        "snr_avg": _round(r["snr_avg"], 2), "snr_best": _round(r["snr_best"], 2),
        "rssi_avg": _round(r["rssi_avg"], 1), "rssi_best": _round(r["rssi_best"], 1),
        "hops_min": r["hops_min"], "hops_avg": _round(r["hops_avg"], 2),
    } for r in per_observer]

    types: dict[str, int] = {}
    scopes: dict[str, int] = {}
    for r in db.node_sent_breakdown(prefix6):
        # A packet type or scope this site could not read stays a null key
        # rather than being lumped in with a real value: rows stored before the
        # scope column existed genuinely have no answer, and "unscoped" is not
        # a safe stand-in for "unknown" when the whole point of the column is
        # to say whether the sender restricted the packet.
        types[r["payload_name"]] = types.get(r["payload_name"], 0) + r["n"]
        scopes[r["scope"]] = scopes.get(r["scope"], 0) + r["n"]

    return {
        "total": total,
        "first": min((r["first_ts"] for r in per_observer if r["first_ts"]), default=None),
        "last": max((r["last_ts"] for r in per_observer if r["last_ts"]), default=None),
        # Fewest hops any of its adverts had travelled when someone picked it
        # up: the closest thing to "how far into the mesh does this node sit"
        # that a packet can tell us. FLOOD only -- see node_sent_by_observer.
        "hops_min": min((r["hops_min"] for r in per_observer
                         if r["hops_min"] is not None), default=None),
        "observers": observers,
        "types": [{"type": k, "count": v} for k, v in
                  sorted(types.items(), key=lambda kv: -kv[1])],
        "scopes": [{"scope": k, "count": v} for k, v in
                   sorted(scopes.items(), key=lambda kv: -kv[1])],
    }


def _node_repeater(prefix6: str) -> dict | None:
    """The tracked-repeater block, or None for the great majority of nodes.

    Headline figures and a link, deliberately no more: /r/<slug> is a full page
    of charts, neighbour history and settings, and a panel that reproduced part
    of it would be a second version of those numbers to keep in step.
    """
    rep = db.public_repeater_by_prefix6(prefix6)
    if rep is None:
        return None
    latest = db.latest_for(rep["id"])

    def val(metric):
        row = latest.get(metric)
        if row is None:
            return None
        return row["value"] if row["value"] is not None else row["value_str"]

    neighbors = [
        {"prefix": n["prefix"], "name": n["name"], "snr": n["snr"],
         "last_seen": n["last_seen"]}
        for n in db.node_neighbors(rep["id"], _NODE_NEIGHBOR_LIMIT)
    ]
    return {
        "slug": rep["slug"], "name": rep["name"],
        "pubkey_prefix": rep["pubkey_prefix"], "last_seen": rep["last_seen"],
        "url": f"/r/{rep['slug']}",
        "online": val("online") == 1.0,
        "battery_percentage": val("battery_percentage"),
        "uptime": val("uptime"),
        "neighbor_count": val("neighbor_count"),
        "neighbors": neighbors,
        # An exactly-full list means the cap bit and there are more, so the
        # panel can say "the best 12" rather than presenting a truncated list
        # as the whole neighbourhood. Same convention, and the same reasoning,
        # as ``capped`` on the heat map.
        "neighbors_capped": len(neighbors) >= _NODE_NEIGHBOR_LIMIT,
    }


@router.get("/nodes/{prefix}")
def node_detail(prefix: str):
    """Everything the site holds about one node, for the live map's node panel.

    A 404 here means "nothing at all is known", which is a different thing from
    "this node has no traffic": a node that only ever advertised itself is a
    perfectly good answer with an empty ``sent`` block, and the panel says so
    instead of refusing to open.
    """
    if not _NODE_KEY_RE.fullmatch(prefix or ""):
        raise HTTPException(422, "Ongeldige nodesleutel")
    p6 = prefix.lower()[:6]

    contacts = db.node_contacts(p6)
    repeater = _node_repeater(p6)
    sent = _node_sent(p6)
    heard = db.node_reception_summary(p6)
    heard_n = (heard["n"] or 0) if heard else 0
    if not contacts and repeater is None and not sent["total"] and not heard_n:
        raise HTTPException(404, "Onbekende node")

    hop = db.node_hop_appearances(p6)
    return {
        "prefix": p6,
        **_node_identity(contacts),
        # The window every figure below lives in. Both halves are needed: the
        # configured retention is the promise, the oldest packet still held is
        # what that promise has actually delivered so far, and on a server that
        # restarted yesterday those are very different numbers.
        "window": {
            "days": db.setting_int("packet_retention_days",
                                   config.PACKET_RETENTION_DAYS),
            "oldest": db.oldest_packet_ts(),
        },
        "repeater": repeater,
        "sent": sent,
        # Absent, not zeroed, when this node is not an observer: almost no node
        # is one, and a "0 packets heard" line under every dot on the map would
        # read as a mesh where nothing hears anything.
        "heard": None if not heard_n else {
            "total": heard_n, "first": heard["first_ts"],
            "last": heard["last_ts"], "senders": heard["senders"] or 0,
        },
        # A ceiling, and labelled as one by the client: a hop is 1, 2 or 3 bytes
        # of a key, and ``siblings`` says how many known nodes share the
        # narrowest of those. With siblings == 1 the count is exact for the
        # one-byte case as well; with siblings == 12 it is an upper bound and
        # the panel has the number that says so.
        "as_hop": {
            "packets": (hop["n"] or 0) if hop else 0,
            "first": hop["first_ts"] if hop else None,
            "last": hop["last_ts"] if hop else None,
            "siblings": db.node_hash_siblings(p6),
        },
        "neighbor_of": [
            {"slug": r["slug"], "name": r["name"], "snr": r["snr"],
             "last_seen": r["last_seen"], "url": f"/r/{r['slug']}"}
            for r in db.node_heard_by_repeaters(p6)
        ],
    }
