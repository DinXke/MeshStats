"""Catalogus van bekende MeshCore-repeatermetrics: labels, eenheden en indeling.

Onbekende metrics die via de API binnenkomen worden automatisch getoond in de
sectie 'Overig'.
"""

# section, label, unit, sort  (volgorde zoals het HA-dashboard van JessaZH.VIR)
CATALOG = {
    # Status
    "online":                 ("status", "Online", None, 0),
    "uptime":                 ("status", "Uptime", "d", 1),
    "neighbor_count":         ("status", "Buren (repeaters gezien)", None, 2),
    "tx_queue_len":           ("status", "TX-wachtrij", None, 3),
    "noise_floor":            ("status", "Ruisvloer", "dBm", 4),
    "last_rssi":              ("status", "Laatste RSSI", "dBm", 5),
    "last_snr":               ("status", "Laatste SNR", "dB", 6),
    "out_path_len":           ("status", "Padlengte", "hops", 7),
    # Batterij & solar
    "battery_percentage":     ("battery", "Batterij", "%", 0),
    "bat":                    ("battery", "Batterijspanning", "V", 1),
    "ch1_voltage":            ("battery", "Ch1 spanning", "V", 2),
    "ch1_temperature":        ("battery", "Ch1 temperatuur", "°C", 3),
    "ch2_voltage":            ("battery", "Ch2 spanning", "V", 4),
    "ch2_temperature":        ("battery", "Ch2 temperatuur", "°C", 5),
    "ch1_battery":            ("battery", "Ch1 batterij", "%", 6),
    "ch1_current":            ("battery", "Ch1 stroom", "mA", 7),
    # Berichten
    "nb_recv":                ("messages", "Ontvangen totaal", None, 0),
    "nb_sent":                ("messages", "Verzonden totaal", None, 1),
    "recv_flood":             ("messages", "Ontvangen flood", None, 2),
    "recv_direct":            ("messages", "Ontvangen direct", None, 3),
    "sent_flood":             ("messages", "Verzonden flood", None, 4),
    "sent_direct":            ("messages", "Verzonden direct", None, 5),
    "flood_dups":             ("messages", "Flood-dubbelen", None, 6),
    "recv_errors":            ("messages", "RX-fouten", None, 7),
    "nb_recv_rate":           ("messages", "Ontvangstrate", "msg/min", 8),
    "nb_sent_rate":           ("messages", "Verzendrate", "msg/min", 9),
    "recv_flood_rate":        ("messages", "Ontvangen flood-rate", "msg/min", 10),
    "recv_direct_rate":       ("messages", "Ontvangen direct-rate", "msg/min", 11),
    "sent_flood_rate":        ("messages", "Verzonden flood-rate", "msg/min", 12),
    "sent_direct_rate":       ("messages", "Verzonden direct-rate", "msg/min", 13),
    "flood_dups_rate":        ("messages", "Flood-dubbelen-rate", "msg/min", 14),
    "direct_dups_rate":       ("messages", "Direct-dubbelen-rate", "msg/min", 15),
    "recv_errors_rate":       ("messages", "RX-foutenrate", "msg/min", 16),
    "direct_dups":            ("messages", "Direct-dubbelen", None, 17),
    "full_evts":              ("messages", "Volle wachtrij-events", None, 18),
    # Airtime
    "airtime_utilization":    ("airtime", "TX-benutting", "%", 0),
    "rx_airtime_utilization": ("airtime", "RX-benutting", "%", 1),
    "airtime":                ("airtime", "TX-airtime totaal", "min", 2),
    "rx_airtime":             ("airtime", "RX-airtime totaal", "min", 3),
    # Overig (bekend maar minder prominent)
    "request_successes":      ("other", "Verzoeken gelukt", None, 0),
    "request_failures":       ("other", "Verzoeken mislukt", None, 1),
    "out_path":               ("other", "Uitgaand pad", None, 2),
}

SECTIONS = [
    ("status", "Status"),
    ("battery", "Batterij & solar"),
    ("messages", "Berichten"),
    ("airtime", "Airtime"),
    ("other", "Overig"),
]

# Tegels die prominent bovenaan staan (zoals de tiles in het HA-dashboard)
TILE_METRICS = {
    "status": ["online", "uptime", "neighbor_count", "tx_queue_len",
               "noise_floor", "last_rssi", "last_snr", "out_path_len"],
    "battery": ["battery_percentage", "bat", "ch1_voltage", "ch1_temperature"],
    "messages": ["nb_recv", "nb_sent", "recv_flood", "recv_direct",
                 "sent_flood", "sent_direct", "flood_dups", "recv_errors"],
    "airtime": ["airtime_utilization", "rx_airtime_utilization",
                "airtime", "rx_airtime"],
}

# Grafieken per repeaterpagina: (titel, [metrics], uren)
CHARTS = [
    ("Spanning (24 u)", ["bat", "ch1_voltage"], 24),
    ("Batterijspanning (7 d)", ["bat"], 168),
    ("Temperatuur (48 u)", ["ch1_temperature"], 48),
    ("Berichtenrates (24 u)", ["nb_recv_rate", "nb_sent_rate"], 24),
    ("Aantal buren (7 d)", ["neighbor_count"], 168),
]

# Meters (gauges): metric -> (min, max, [(vanaf, kleur), ...])
GAUGES = {
    "battery_percentage": (0, 100, [(0, "#ff5c5c"), (25, "#ffb454"), (50, "#35e08c")]),
    # Werkbereik van een gemiddelde 1S-lithiumcel: onder ~3,4 V wordt het kritiek
    "bat": (3.0, 4.2, [(3.0, "#ff5c5c"), (3.4, "#ffb454"), (3.7, "#35e08c")]),
    "airtime_utilization": (0, 10, [(0, "#35e08c"), (2, "#ffb454"), (5, "#ff5c5c")]),
    "rx_airtime_utilization": (0, 100, [(0, "#35e08c"), (30, "#ffb454"), (60, "#ff5c5c")]),
}

# Thermometers: metric -> (min, max, [(vanaf, kleur), ...])
THERMOMETERS = {
    "ch1_temperature": (-20, 80, [(-20, "#4cc9f0"), (0, "#35e08c"), (45, "#ffb454"), (60, "#ff5c5c")]),
    "ch2_temperature": (-20, 80, [(-20, "#4cc9f0"), (0, "#35e08c"), (45, "#ffb454"), (60, "#ff5c5c")]),
}


def metric_info(name: str):
    """(section, label, unit, sort) — met fallback voor onbekende metrics."""
    return CATALOG.get(name, ("other", name.replace("_", " "), None, 99))


# ---- instelbare weergave (beheerd via /admin, opgeslagen in settings) -------

DEFAULT_RANGES = [4, 24, 48, 168, 744, 2160]  # uren in het historiekvenster

# Blokken op de publieke repeaterpagina, in standaardvolgorde
DEFAULT_LAYOUT = [
    {"key": "status", "visible": True},
    {"key": "battery", "visible": True},
    {"key": "messages", "visible": True},
    {"key": "airtime", "visible": True},
    {"key": "other", "visible": True},
    {"key": "charts", "visible": True},
    {"key": "map", "visible": True},
    {"key": "neighbors", "visible": True},
]
BLOCK_NAMES = {
    "status": "Status", "battery": "Batterij & solar", "messages": "Berichten",
    "airtime": "Airtime", "other": "Overig", "charts": "Grafieken",
    "map": "Linkkaart", "neighbors": "Buren",
}


def range_label(hours: int) -> str:
    if hours % 24 == 0 and hours >= 24:
        return f"{hours // 24} d"
    return f"{hours} u"


def parse_ranges(raw: str | None) -> list[int]:
    if not raw:
        return DEFAULT_RANGES
    out = []
    for part in raw.replace(";", ",").split(","):
        try:
            h = int(part.strip())
        except ValueError:
            continue
        if 1 <= h <= 8760 and h not in out:
            out.append(h)
    return sorted(out) or DEFAULT_RANGES


def parse_layout(raw: str | None) -> list[dict]:
    """Valideert de opgeslagen indeling; ontbrekende blokken komen achteraan."""
    import json
    layout = []
    if raw:
        try:
            for item in json.loads(raw):
                key = item.get("key")
                if key in BLOCK_NAMES and not any(b["key"] == key for b in layout):
                    layout.append({"key": key, "visible": bool(item.get("visible", True))})
        except (ValueError, AttributeError, TypeError):
            layout = []
    for block in DEFAULT_LAYOUT:
        if not any(b["key"] == block["key"] for b in layout):
            layout.append(dict(block))
    return layout
