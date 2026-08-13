"""Constanten voor MC Repeater Stats."""
import re

DOMAIN = "mc_repeater_stats"

CONF_BASE_URL = "base_url"
CONF_TOKEN = "token"
CONF_REPEATERS = "repeaters"
CONF_AUTO_ADD = "auto_add"
CONF_PASSWORDS = "passwords"  # {prefix: repeater-admin-wachtwoord}

SETTINGS_LOGIN_WAIT = 12   # s wachten na send_login
SETTINGS_RESPONSE_TIMEOUT = 12  # s wachten op het eerste antwoord per get-commando
SETTINGS_QUIET_GAP = 5     # s stilte voor we een meerregelig antwoord afsluiten
SETTINGS_PARAM_CAP = 45    # harde limiet per parameter (bv. lange region-lijsten)

DEBOUNCE_SECONDS = 10
FULL_PUSH_INTERVAL = 300  # elke 5 min een volledige snapshot
COMMAND_POLL_INTERVAL = 30  # elke 30 s checken op handmatige statusverzoeken
REFRESH_PUSH_DELAY = 35  # s wachten op het LoRa-antwoord vóór de geforceerde push

# sensor.meshcore_<prefix>_<rest> / binary_sensor.meshcore_<prefix>_<rest>
RE_ENTITY = re.compile(r"^(?:sensor|binary_sensor)\.meshcore_([0-9a-f]{6,12})_(.+)$")
RE_NEIGHBOR = re.compile(r"^neighbor_([0-9a-f]{6})$")
RE_CONTACT = re.compile(r"^binary_sensor\.meshcore_.+_([0-9a-f]{12})_contact$")
RE_NEIGHBOR_SEEN = re.compile(r"^neighbor_([0-9a-f]{6})_seen$")
RE_NAME = re.compile(r"MeshCore Repeater: (.+?) \([0-9a-f]+\)")
RE_NEIGHBOR_NAME = re.compile(r"Neighbor (.+?) SNR$")

# Bekende metricnamen, langste eerst zodat bv. 'battery_percentage' niet als
# 'bat' met suffix wordt gelezen. De entity-id eindigt op een geslugde nodenaam
# (bv. bat_be_hss_jessazh_vir) die we hiermee afknippen.
KNOWN_METRICS = sorted([
    "bat", "battery_percentage", "uptime",
    "airtime_utilization", "rx_airtime_utilization", "rx_airtime", "airtime",
    "nb_recv_rate", "nb_sent_rate", "nb_recv", "nb_sent",
    "tx_queue_len", "noise_floor", "last_rssi", "last_snr",
    "sent_flood_rate", "sent_direct_rate", "recv_flood_rate", "recv_direct_rate",
    "recv_errors_rate", "flood_dups_rate", "direct_dups_rate",
    "sent_flood", "sent_direct", "recv_flood", "recv_direct",
    "full_evts", "direct_dups", "flood_dups", "recv_errors",
    "out_path_len", "out_path", "request_successes", "request_failures",
    "ch1_voltage", "ch1_temperature", "ch1_battery", "ch1_current",
    "ch2_voltage", "ch2_temperature",
    "online", "neighbor_count", "contact",
], key=len, reverse=True)
