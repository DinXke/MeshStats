"""Weighing address-hash candidates against what the mesh actually shows.

A ``src_hash``, a ``dest_hash`` and a path hop all name a node by the first byte
or two of its public key. One byte has 256 values while this site already knows
several hundred nodes, so several nodes answering to the same hash is the normal
case rather than a data error (see docs/protocol.md 1.4). Listing every one of
them is honest -- but it is not everything we can say. The database also records
where nodes are, and at how many hops a given observer has actually heard each
of them. Comparing a candidate against *every node ever seen anywhere* throws
that away and makes the lists needlessly wide.

This module does two things with that record, and refuses to do a third:

exclusion
    A candidate the frame itself places outside any plausible radio range is
    dropped. This is the only way a candidate disappears, it needs a bound the
    frame really carries (see :func:`radio_hop_bound`), and the caller is handed
    the dropped ones so the reader can be told how many fell away and why.

ranking
    The survivors are ordered best first on three coarse, nameable signals, in a
    fixed priority: how close in hops this observer has actually heard the node,
    how far away it is, and how recently it was seen.

never
    Naming a winner when the evidence does not separate the top two. A ranking
    that cannot rank is reported as a tie and the caller falls back to "N
    possible". Flipping a coin and printing the result as "most likely" is the
    one thing this project does not do.

Why bands and not a score
-------------------------
The bands below are deliberately coarse and compared in a fixed order rather
than summed into a number. A weighted score with decimals would separate every
pair of candidates, including the pairs the evidence does not actually separate
-- and the tie is the interesting case here, because it is the one where the
honest answer is still "several possible". Three bucketed signals in a fixed
order can also be read out loud: "heard closer, then nearer, then more
recently". Nobody can retell a formula.

What the signals are worth
--------------------------
``hops`` is the strong one, and it is the only signal that is evidence rather
than geography: an ADVERT names its sender by full key prefix, so "this exact
node has been heard by this exact observer, at this many hops" is a measurement.
Distance and recency are priors, and they are used only to order what the hop
evidence leaves level.
"""
import math
from datetime import datetime, timezone

# The widest radio link one hop is allowed to be, for the purpose of declaring a
# candidate physically out of the picture.
#
# This is a rejection threshold, not a model of coverage, so it is set far above
# anything a normal mesh produces rather than at the middle of the distribution.
# On the deployment this was measured against, the furthest node ever heard at
# zero hops sat 24 km away and the furthest at one hop 51 km; 120 km leaves a
# factor of five over the first and still rules out the cross-border neighbours
# that were turning up as candidates for locally-heard traffic. Terrestrial LoRa
# does reach further than this over water or from a hilltop, which is precisely
# why the exclusion is additionally waived for any node this observer has really
# heard at that hop count (see :func:`weigh`): a measurement always beats this
# number.
MAX_RADIO_HOP_KM = 120.0

# Hop tiers. Zero and one hop are one tier on purpose: a node whose advert
# reached us direct and one whose advert came via a single repeater are both
# "in this corner of the mesh", and splitting them would rank on the accident of
# which copy of a flooded advert happened to arrive first.
HEARD_TIER_LOCAL = 2      # heard at this many hops or fewer: tier 0
HEARD_TIER_NEARBY = 4     # heard at fewer than this many hops: tier 1
                          # heard, but further out: tier 2
                          # never heard by this observer at all: tier 3
TIER_UNHEARD = 3

# Distance from the observer, in km. Wide bands, because the point is to
# separate "in the neighbourhood" from "in another country", not to rank two
# nodes that are 30 and 34 km out.
DISTANCE_BANDS_KM = (25.0, 75.0, 200.0)

# Age of the last sighting, in seconds: today, this week, this month, older.
RECENCY_BANDS_S = (24 * 3600, 7 * 24 * 3600, 30 * 24 * 3600)

# The order the signals are compared in. Also the vocabulary the caller uses to
# say *why* the leader leads, so keep these names and the i18n keys in step.
LEAD_SIGNALS = ("hops", "distance", "recency")

_TS = "%Y-%m-%dT%H:%M:%SZ"


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in kilometres."""
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = (math.sin(dp / 2) ** 2 +
         math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2)
    return 2 * r * math.asin(min(1.0, math.sqrt(a)))


def radio_hop_bound(role: str, route: str | None, path_len: int | None,
                    index: int | None = None) -> int | None:
    """At most how many radio links separate this node from the observer -- or
    None when the frame says nothing at all about that.

    Everything here turns on one asymmetry that is easy to get backwards. On a
    FLOOD the path is the route *already travelled*: every forwarder appended
    its own hash, so the frame counts backwards to the originator. On a DIRECT
    the path is the route *still to travel*: the frame counts forwards to the
    destination (docs/protocol.md 1.4). So a flood bounds where a packet came
    from and a direct bounds where it is going, and neither bounds the other.

    ``role``:

    src
        Only on a FLOOD. The originator sits ``path_len`` forwarders back, hence
        ``path_len + 1`` radio links from here. Zero hops is the interesting
        case: we heard the sender's own transmission, so it is inside our radio
        range, full stop.
    dest
        Only on a DIRECT. ``path_len`` forwarders still to go and then the
        destination, and the node we just heard transmit is itself one link from
        us: ``path_len + 2``. Note what this does *not* cover -- a flooded
        packet's destination may sit anywhere in the mesh, however few hops the
        packet has travelled so far. A zero-hop flood tells you where it started,
        not where it is headed.
    hop
        A hop at ``index`` in the path. On a FLOOD, ``path_len - 1 - index``
        forwarders came after it, so it is ``path_len - index`` links from us. On
        a DIRECT it has yet to forward: ``index + 1`` links past the node we
        heard, so ``index + 2`` from us.

    Returning None means "no bound", and no bound means no exclusion. That is
    the safe direction: the ranking still runs.
    """
    if not route or path_len is None or path_len < 0:
        return None
    flood = route.endswith("FLOOD")
    direct = route.endswith("DIRECT")
    if role == "src":
        return path_len + 1 if flood else None
    if role == "dest":
        return path_len + 2 if direct else None
    if role == "hop" and index is not None and 0 <= index < path_len:
        if flood:
            return path_len - index
        if direct:
            return index + 2
    return None


def _heard_tier(hops: int | None) -> int:
    if hops is None:
        return TIER_UNHEARD
    if hops < HEARD_TIER_LOCAL:
        return 0
    if hops < HEARD_TIER_NEARBY:
        return 1
    return 2


def _band(value: float | None, edges) -> int:
    """Which band a value falls in.

    A missing value gets its own band one past the last, so an unplaced or
    never-seen node sorts below every node we can actually place or date. Not
    knowing where something is is a worse reason to put it first than knowing it
    is far away.
    """
    if value is None:
        return len(edges) + 1
    for i, edge in enumerate(edges):
        if value < edge:
            return i
    return len(edges)


def _age_band(seen: str | None, now: datetime) -> int:
    if not seen:
        return len(RECENCY_BANDS_S) + 1
    try:
        then = datetime.strptime(seen, _TS)
    except (TypeError, ValueError):
        return len(RECENCY_BANDS_S) + 1
    return _band(max(0.0, (now - then).total_seconds()), RECENCY_BANDS_S)


def weigh(candidates: list[dict], evidence: dict | None = None,
          observer6: str | None = None,
          observer_pos: tuple | None = None,
          bound: int | None = None,
          now: datetime | None = None) -> dict:
    """Exclude the impossible, rank the rest, and say when it cannot be ranked.

    ``candidates``  rows from the contacts table: prefix, name, lat, lon,
                    node_type, updated.
    ``evidence``    per prefix6, what this observer has actually heard:
                    ``{"hops": int|None, "seen": iso|None}``. Adverts name their
                    sender by full key prefix, so this is measurement, not
                    inference.
    ``observer6``   the observer's own 6-hex key prefix, when known.
    ``observer_pos``the observer's position, when known.
    ``bound``       :func:`radio_hop_bound` for this field of this frame.
    ``now``         injectable clock, so the recency bands are testable.

    Returns ``{"state", "matches", "dropped", "lead"}``:

    known       one candidate stands, whether it was alone from the start or the
                last one left after an exclusion -- ``dropped`` says which. Still
                a derivation from one byte, never a stated identity.
    likely      several stand and the evidence separates the leader from the
                runner-up; ``lead`` names the signal that did it.
    ambiguous   several stand and nothing separates them. Report them all.
    unknown     nothing stands: either no contact matches the hash, or every one
                that did was excluded.
    """
    evidence = evidence or {}
    now = now or datetime.now(timezone.utc).replace(tzinfo=None)
    olat, olon = observer_pos if observer_pos else (None, None)
    observer6 = (observer6 or "")[:6].lower() or None

    scored: list[tuple] = []
    dropped: list[dict] = []
    for c in candidates:
        prefix = (c.get("prefix") or "").lower()
        heard = evidence.get(prefix[:6]) or {}
        hops, seen = heard.get("hops"), heard.get("seen") or c.get("updated")
        lat, lon = c.get("lat"), c.get("lon")
        if observer6 and prefix[:6] == observer6:
            # The observer is not a candidate that has to have been overheard --
            # it is the node doing the listening. Zero hops and zero distance are
            # facts about it, and the evidence table would only say so by the
            # accident of one of its own adverts coming back through the mesh.
            hops, km = 0, 0.0
        elif None not in (olat, olon, lat, lon):
            km = haversine_km(olat, olon, lat, lon)
        else:
            km = None

        entry = {"prefix": prefix, "name": c.get("name"), "lat": lat, "lon": lon,
                 "node_type": c.get("node_type"), "hops": hops,
                 "km": None if km is None else round(km, 1), "seen": seen}

        # Exclusion, with the measurement given the last word: a node this
        # observer has really heard at this hop count stays, whatever the
        # kilometres say. Coverage that beats the threshold is a fact about the
        # world; the threshold is only a stand-in for one.
        if (bound and km is not None and km > bound * MAX_RADIO_HOP_KM
                and (hops is None or hops > bound)):
            dropped.append({**entry, "why": "range", "bound": bound})
            continue

        key = (_heard_tier(hops), _band(km, DISTANCE_BANDS_KM),
               _age_band(seen, now), (c.get("name") or prefix).lower())
        scored.append((key, entry))

    scored.sort(key=lambda s: s[0])
    matches = [m for _, m in scored]
    lead = None
    if not matches:
        state = "unknown"
    elif len(matches) == 1:
        state = "known"
    else:
        # Sorted, so the first position where the top two differ is the signal
        # that put one above the other. If they differ nowhere, the only thing
        # separating them is the alphabetical tiebreak in the sort -- which is
        # not evidence, and must not be dressed up as a ranking.
        top, runner = scored[0][0], scored[1][0]
        lead = next((name for i, name in enumerate(LEAD_SIGNALS)
                     if top[i] != runner[i]), None)
        state = "likely" if lead else "ambiguous"
    return {"state": state, "matches": matches, "dropped": dropped, "lead": lead}
