"""VictoriaMetrics: where the measurements live.

Division of labour, and it matters:

==================  ==========================================================
VictoriaMetrics     the measurements only -- history and the charts drawn from it
SQLite              repeaters, ``latest``, contacts, neighbours, packets,
                    tokens, admin
==================  ==========================================================

``latest`` deliberately stays in SQLite. It feeds the cards on the home page,
which must render fast and without touching the network; a time-series database
is the wrong shape for "the one current value" anyway.

Why move at all
---------------
Nodes are going from a reading every five minutes to one every ten seconds. In
SQLite that means throwing raw points away to keep the file manageable.
VictoriaMetrics compresses to roughly a byte per point, so keeping full
resolution there is cheaper than thinning it out here.

Naming is fixed
---------------
The existing history was migrated under these names and any deviation silently
splits a series in two::

    write (influx line protocol, POST /write):
        meshstats,repeater=<slug> <metric>=<value> <nanoseconds>
    read (PromQL):
        meshstats_<metric>{repeater="<slug>"}

Metric names come from nodes, so only ``[A-Za-z0-9_]`` survives into a field
name -- see ``safe_metric``.

Never at the cost of the site
-----------------------------
Two rules the rest of this module exists to keep:

1. **Writing may not hold up ingest.** Points are handed to a bounded queue and
   a background thread does the HTTP. An MQTT message is never waiting on a
   socket to a database that might be busy, slow, or gone.
2. **A database that is away may not lose measurements.** Anything that cannot
   be written -- disabled, unreachable, queue full -- is spilled to the SQLite
   ``samples`` table through the callback ``db`` registers. Reads fall back to
   the same table, so the site keeps working exactly as it did before, only
   thinned to the old heartbeat resolution.

``samples`` is therefore not dead weight; it is the safety net, and it is why
this move can be rolled back without losing a day.
"""
import json
import logging
import os
import queue
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

log = logging.getLogger("meshstats.tsdb")

# Empty means "keep everything in SQLite" -- the pre-migration behaviour, which
# stays a supported way to run the site.
URL = os.environ.get("MCS_TSDB_URL", "").strip().rstrip("/")

MEASUREMENT = "meshstats"
METRIC_PREFIX = "meshstats_"

# Short: this runs inside a request for reads, and inside the writer thread for
# writes. Waiting longer helps nobody; the fallback is right there.
WRITE_TIMEOUT_S = 5
QUERY_TIMEOUT_S = 15

# One HTTP request per ingest would work, but a node publishing every ten
# seconds turns that into a request per node per ten seconds, each with its own
# connection setup. Batching a couple of seconds' worth costs at most that much
# delay before a point is queryable -- irrelevant for charts -- and collapses
# the request count by the number of metrics in a snapshot, which is around a
# hundred.
FLUSH_INTERVAL_S = 2.0
MAX_BATCH_POINTS = 2000

# The queue is bounded so a database that stops answering cannot grow until the
# process dies. Past this, points go straight to SQLite instead. Roughly a
# minute of the busiest traffic we expect.
QUEUE_MAX_POINTS = 20000

# Two tries, then spill. Retrying longer just delays the safety net.
WRITE_ATTEMPTS = 2

_q: "queue.Queue" = queue.Queue(maxsize=QUEUE_MAX_POINTS)
_spill = None            # set by db.register_spill
_thread = None
_lock = threading.Lock()

_state = {
    "enabled": bool(URL),
    "url": URL,
    "connected": None,      # None = not tried yet
    "written": 0,           # points accepted by VictoriaMetrics
    "batches": 0,
    "spilled": 0,           # points written to SQLite instead
    "failures": 0,
    "last_error": "",
    "last_write": None,
}

_INVALID = re.compile(r"[^A-Za-z0-9_]")


def enabled() -> bool:
    return bool(URL)


def safe_metric(name: str) -> str:
    """Metric name reduced to what may appear in an influx field key.

    Names come from firmware, and firmware is free to invent them. Everything
    outside [A-Za-z0-9_] is dropped rather than replaced, so the result stays
    identical to the names the existing history was migrated under.
    """
    return _INVALID.sub("", str(name or ""))


def status() -> dict:
    """State for the admin page."""
    with _lock:
        out = dict(_state)
    out["queued"] = _q.qsize()
    return out


def register_spill(fn) -> None:
    """Hand back points that could not be written, so SQLite can keep them.

    ``db`` registers this rather than being imported here: the dependency runs
    one way, and a module that owns the network has no business owning the
    fallback storage as well.
    """
    global _spill
    _spill = fn


def _note(ok: bool, err: str = "") -> None:
    with _lock:
        _state["connected"] = ok
        if ok:
            _state["last_error"] = ""
        else:
            _state["failures"] += 1
            _state["last_error"] = err[:200]


def _escape_tag(value: str) -> str:
    """Influx tag values escape comma, space and equals.

    Slugs are already [a-z0-9-] so this never fires today; it is here so that a
    future slug rule cannot silently corrupt the line protocol.
    """
    return str(value).replace("\\", "\\\\").replace(",", "\\,") \
                     .replace(" ", "\\ ").replace("=", "\\=")


def _line(slug: str, metric: str, value: float, ts_ns: int) -> str | None:
    field = safe_metric(metric)
    if not field:
        return None
    return f"{MEASUREMENT},repeater={_escape_tag(slug)} {field}={float(value)} {ts_ns}"


def _ts_to_ns(ts: str) -> int:
    """'2026-08-14T12:00:00Z' to epoch nanoseconds; falls back to now."""
    try:
        dt = datetime.strptime(ts, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        return int(dt.timestamp() * 1_000_000_000)
    except (TypeError, ValueError):
        return int(time.time() * 1_000_000_000)


def record(repeater_id: int, slug: str, ts: str, points: dict) -> None:
    """Queue one snapshot's measurements. Returns immediately.

    Called from the ingest path, which must not block: everything here is either
    an enqueue or, when that is not possible, a handover to the spill callback.
    """
    if not points:
        return
    ts_ns = _ts_to_ns(ts)
    for metric, value in points.items():
        item = (repeater_id, slug, metric, float(value), ts_ns, ts)
        if not URL:
            _spill_one(item)
            continue
        try:
            _q.put_nowait(item)
        except queue.Full:
            # Backed up: the safety net takes it rather than ingest waiting or
            # the measurement being dropped.
            _spill_one(item)


def _spill_one(item) -> None:
    if _spill is None:
        return
    try:
        _spill([item])
        with _lock:
            _state["spilled"] += 1
    except Exception as err:  # noqa: BLE001 - the fallback must not raise either
        log.warning("spill to SQLite failed: %s", err)


def _spill_batch(items) -> None:
    if _spill is None or not items:
        return
    try:
        _spill(items)
        with _lock:
            _state["spilled"] += len(items)
    except Exception as err:  # noqa: BLE001
        log.warning("spill to SQLite failed: %s", err)


def _post(body: bytes) -> None:
    req = urllib.request.Request(URL + "/write", data=body,
                                 headers={"Content-Type": "text/plain"})
    with urllib.request.urlopen(req, timeout=WRITE_TIMEOUT_S) as resp:
        if resp.status >= 300:
            raise urllib.error.HTTPError(resp.url, resp.status, "write refused",
                                         resp.headers, None)


def _flush(items) -> None:
    """Write one batch, or hand it to SQLite if that will not work."""
    lines = []
    for _rid, slug, metric, value, ts_ns, _ts in items:
        line = _line(slug, metric, value, ts_ns)
        if line:
            lines.append(line)
    if not lines:
        return
    body = ("\n".join(lines) + "\n").encode("utf-8")
    for attempt in range(WRITE_ATTEMPTS):
        try:
            _post(body)
            with _lock:
                _state["written"] += len(lines)
                _state["batches"] += 1
                _state["last_write"] = datetime.now(timezone.utc).strftime(
                    "%Y-%m-%dT%H:%M:%SZ")
            _note(True)
            return
        except Exception as err:  # noqa: BLE001 - any failure means the same thing
            if attempt + 1 >= WRITE_ATTEMPTS:
                _note(False, f"{type(err).__name__}: {err}")
                log.warning("write of %d points failed (%s); spilling to SQLite",
                            len(lines), err)
                _spill_batch(items)
            else:
                time.sleep(0.25)


def _run() -> None:
    """Drain the queue in batches until the process ends."""
    pending = []
    deadline = time.monotonic() + FLUSH_INTERVAL_S
    while True:
        timeout = max(0.05, deadline - time.monotonic())
        try:
            pending.append(_q.get(timeout=timeout))
        except queue.Empty:
            pass
        now = time.monotonic()
        if pending and (len(pending) >= MAX_BATCH_POINTS or now >= deadline):
            batch, pending = pending, []
            deadline = now + FLUSH_INTERVAL_S
            try:
                _flush(batch)
            except Exception as err:  # noqa: BLE001 - the thread must not die
                log.warning("flush crashed: %s", err)
                _spill_batch(batch)
        elif now >= deadline:
            deadline = now + FLUSH_INTERVAL_S


def probe() -> bool:
    """Ask VictoriaMetrics whether it is there, for the admin page and the log."""
    if not URL:
        return False
    try:
        with urllib.request.urlopen(URL + "/health", timeout=WRITE_TIMEOUT_S) as resp:
            ok = resp.status < 300
        _note(ok, "" if ok else "health check refused")
        return ok
    except Exception as err:  # noqa: BLE001
        _note(False, f"{type(err).__name__}: {err}")
        return False


def start() -> None:
    """Start the writer thread (a no-op when no database is configured)."""
    global _thread
    if not URL:
        log.info("No MCS_TSDB_URL configured; measurements stay in SQLite")
        return
    if _thread is not None:
        return
    ok = probe()
    log.info("VictoriaMetrics at %s: %s", URL, "reachable" if ok else "NOT reachable")
    _thread = threading.Thread(target=_run, name="tsdb-writer", daemon=True)
    _thread.start()


# --- reading ------------------------------------------------------------------

# A chart is a few hundred pixels wide, so a few hundred points is all it can
# show. Fixed rungs rather than an exact division, because a step that drifts
# with the request makes two charts of the same range disagree about where the
# buckets start.
STEP_LADDER = [30, 60, 120, 300, 600, 900, 1800, 3600, 7200, 10800, 21600, 43200, 86400]
TARGET_POINTS = 600


def step_for(hours: int) -> int:
    """Seconds per point for a range, keeping a 90-day chart from pulling
    millions of samples. 24 h lands on 300 s, which is exactly the density the
    charts had when nodes published every five minutes."""
    want = (hours * 3600) / TARGET_POINTS
    for step in STEP_LADDER:
        if step >= want:
            return step
    return STEP_LADDER[-1]


def _query_range(query: str, start: int, end: int, step: int):
    params = urllib.parse.urlencode({
        "query": query, "start": start, "end": end, "step": f"{step}s",
    })
    url = f"{URL}/api/v1/query_range?{params}"
    with urllib.request.urlopen(url, timeout=QUERY_TIMEOUT_S) as resp:
        body = json.load(resp)
    if body.get("status") != "success":
        raise ValueError(f"query failed: {body.get('error', 'unknown')}")
    return body.get("data", {}).get("result", [])


def _series(slug: str, metric: str) -> str:
    return f'{METRIC_PREFIX}{safe_metric(metric)}{{repeater="{slug}"}}'


def history(slug: str, metric: str, hours: int):
    """Points for one metric, or None when the caller should use SQLite.

    None means "ask somewhere else" and is returned for every reason the caller
    cannot do anything about: no database configured, unreachable, a bad answer.
    Whether a metric simply has no data is a different thing, and that returns an
    empty list -- the chart then draws its own "no history" message rather than
    silently falling back and drawing something from another source.
    """
    if not URL:
        return None
    field = safe_metric(metric)
    if not field:
        return []
    step = step_for(hours)
    end = int(time.time())
    start = end - hours * 3600
    # avg_over_time with the step as its window, so a bucket summarises the
    # points inside it instead of sampling whichever one happens to sit nearest
    # the boundary. At the finest step this is a no-op; over 90 days it is what
    # keeps a spike from disappearing between buckets.
    query = f"avg_over_time({_series(slug, field)}[{step}s])"
    try:
        result = _query_range(query, start, end, step)
    except Exception as err:  # noqa: BLE001
        _note(False, f"{type(err).__name__}: {err}")
        log.warning("history query for %s failed (%s); falling back to SQLite",
                    field, err)
        return None
    _note(True)
    if not result:
        return []
    points = []
    for ts, value in result[0].get("values", []):
        try:
            points.append((
                datetime.fromtimestamp(float(ts), timezone.utc)
                        .strftime("%Y-%m-%dT%H:%M:%SZ"),
                round(float(value), 3),
            ))
        except (TypeError, ValueError):
            continue   # VictoriaMetrics reports gaps as NaN
    return points


def window_values(slug: str, metric: str, minutes: int):
    """Raw-ish (timestamp, value) pairs over the last ``minutes``, or None.

    Feeds the computed airtime utilisation, which needs the first and last
    reading in a window rather than a drawn curve, so this stays close to the
    stored resolution instead of using the chart's step ladder.
    """
    if not URL:
        return None
    field = safe_metric(metric)
    if not field:
        return []
    end = int(time.time())
    start = end - minutes * 60
    try:
        result = _query_range(_series(slug, field), start, end, 60)
    except Exception as err:  # noqa: BLE001
        _note(False, f"{type(err).__name__}: {err}")
        return None
    if not result:
        return []
    out = []
    for ts, value in result[0].get("values", []):
        try:
            out.append((float(ts), float(value)))
        except (TypeError, ValueError):
            continue
    return out
