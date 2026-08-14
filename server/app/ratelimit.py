"""In-memory brute-force throttle for the admin login.

The site is reachable from the public internet through a cloudflared tunnel, so
``POST /admin/login`` is exposed to anyone. A single ``time.sleep(1)`` per failed
attempt does not slow a parallel attacker down at all -- it only ties up a
threadpool worker per request, which is a self-inflicted denial of service on
top of the brute force it fails to stop.

Two independent buckets are kept, because each closes a hole the other leaves:

``ip:<address>``
    Stops one host hammering many usernames.
``user:<name>``
    Stops a botnet spreading attempts over thousands of addresses. This is the
    bucket that actually holds the line here, because the client address is only
    as honest as the proxy chain (see :func:`client_ip`) while the username is
    read straight from the form.

Failures inside a rolling window are counted; the first few are free so a
mistyped password costs nothing, and after that every further failure doubles a
lockout up to a ceiling. Locking on username means anyone can lock the admin
account out on purpose, which is why the ceiling is minutes rather than hours --
a nuisance beats an unbounded guessing budget, and the operator can restart the
service to clear it.

State lives in this process only. That is deliberate: the deployment is a single
uvicorn process, and a table in SQLite would turn every login attempt from the
internet into a write. A restart forgets the counters, which is the one case
where an attacker gains something -- and they do not get to trigger restarts.
"""
import ipaddress
import threading
import time

from . import config

# Failures older than this drop out of the count, so an occasional typo over a
# working day never accumulates into a lockout.
WINDOW_S = 15 * 60

# Attempts that pass without any penalty.
FREE_ATTEMPTS = 5

# Lockout after the free attempts: BASE * 2**(n) seconds, capped per bucket.
BASE_LOCK_S = 2
MAX_LOCK_IP_S = 15 * 60
MAX_LOCK_USER_S = 5 * 60

# Hard ceiling on the number of tracked keys. An attacker rotating addresses or
# usernames must not be able to grow this dict until the process runs out of
# memory; past the cap the entries closest to expiry are dropped first.
MAX_ENTRIES = 4096

_lock = threading.Lock()
# key -> [failure count, timestamp of last failure, locked-until timestamp]
_buckets: dict[str, list[float]] = {}


def client_ip(request) -> str:
    """Best available client address for rate limiting.

    ``request.client.host`` cannot be used: uvicorn runs with
    ``--forwarded-allow-ips "*"`` and then takes the *first* X-Forwarded-For
    entry, which any client can put there itself. Proxies append the address
    they saw, so entries are trustworthy from the right, and only as far back as
    there are proxies we actually run. Reading the header ourselves and counting
    from the right is what keeps a spoofed prefix from creating a fresh bucket
    on every request.

    Falls back to the transport address when the header is missing or malformed;
    the caller only needs a stable key, never a routable address.
    """
    header = request.headers.get("x-forwarded-for", "")
    parts = [p.strip() for p in header.split(",") if p.strip()]
    if parts:
        # hops=1 -> last entry, hops=2 -> the one before it, and so on.
        idx = len(parts) - config.TRUSTED_PROXY_HOPS
        candidate = parts[max(0, idx)]
        try:
            return str(ipaddress.ip_address(candidate))
        except ValueError:
            pass  # not an address: fall through rather than key on junk
    peer = getattr(request.client, "host", None)
    return peer or "unknown"


def _prune(now: float) -> None:
    """Drop expired buckets; caller holds the lock."""
    for key, entry in list(_buckets.items()):
        if entry[1] + WINDOW_S < now and entry[2] < now:
            del _buckets[key]
    if len(_buckets) > MAX_ENTRIES:
        # Oldest last-failure first: those are closest to expiring anyway.
        for key, _ in sorted(_buckets.items(), key=lambda kv: kv[1])[
            : len(_buckets) - MAX_ENTRIES
        ]:
            del _buckets[key]


def _keys(ip: str, username: str) -> list[tuple[str, int]]:
    return [
        (f"ip:{ip}", MAX_LOCK_IP_S),
        (f"user:{username.strip().lower()[:64]}", MAX_LOCK_USER_S),
    ]


def retry_after(ip: str, username: str) -> int:
    """Seconds the caller must wait, or 0 when the attempt may proceed."""
    now = time.time()
    with _lock:
        _prune(now)
        wait = 0.0
        for key, _ in _keys(ip, username):
            entry = _buckets.get(key)
            if entry and entry[2] > now:
                wait = max(wait, entry[2] - now)
        return int(wait) + 1 if wait > 0 else 0


def record_failure(ip: str, username: str) -> int:
    """Count a failed login and return the resulting lockout in seconds."""
    now = time.time()
    with _lock:
        _prune(now)
        wait = 0.0
        for key, max_lock in _keys(ip, username):
            entry = _buckets.get(key)
            # A bucket that has been quiet for a full window starts over.
            if entry is None or entry[1] + WINDOW_S < now:
                entry = [0.0, now, 0.0]
                _buckets[key] = entry
            entry[0] += 1
            entry[1] = now
            over = entry[0] - FREE_ATTEMPTS
            if over > 0:
                lock = min(BASE_LOCK_S * (2 ** (over - 1)), max_lock)
                entry[2] = max(entry[2], now + lock)
                wait = max(wait, entry[2] - now)
        return int(wait) + 1 if wait > 0 else 0


def record_success(ip: str, username: str) -> None:
    """Clear both buckets: whoever proved the password is not the attacker."""
    with _lock:
        for key, _ in _keys(ip, username):
            _buckets.pop(key, None)


def reset() -> None:
    """Drop all state. Only used by the tests."""
    with _lock:
        _buckets.clear()
