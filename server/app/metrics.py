"""Catalogus van bekende MeshCore-repeatermetrics: labels, eenheden en indeling.

Onbekende metrics die via de API binnenkomen worden automatisch getoond in de
sectie 'Overig'.

Kanaalmetingen (``ch<N>_...``) zijn de uitzondering op dat laatste: die krijgen
hun eigen sectie, want er kunnen er willekeurig veel zijn en ze horen bij
elkaar. Zie :func:`channel_metric` verderop.
"""
import re

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
    # Die temperature of the MCU, not the air around the node. It lives under
    # status rather than with the battery channels because it says something
    # about the node itself; ch1_temperature stays where it is, for an actual
    # sensor channel. A node can report both, and then they are two different
    # measurements in two different sections -- see the hint below.
    "mcu_temperature":        ("status", "Chiptemperatuur", "°C", 8),
    # De WiFi-verbinding, en niet de LoRa-radio. Een eigen naam en nadrukkelijk
    # niet ``last_rssi``: dat is een ander signaal van een andere radio, en twee
    # grootheden onder één naam is precies hoe een grafiek gaat liegen.
    #
    # Deze meting bestaat alleen voor een node die de server over IP uitleest, en
    # ze staat er om één reden: ze meet de gezondheid van precies dát pad. Zakt ze
    # weg, dan verdwijnt de beheerweg -- en dat is geen theoretisch geval maar een
    # gemeten geval. Zie sensornode.py.
    "wifi_rssi":              ("status", "WiFi-signaal", "dBm", 9),
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
    # Pakketfilter -- wat deze repeater WEIGERDE door te sturen, per reden.
    # Een eigen sectie en niet bij 'Berichten', want deze getallen gaan over een
    # keuze van de beheerder en niet over wat de radio deed. Ze naast
    # 'Ontvangen flood' zetten zou suggereren dat het metingen van dezelfde
    # soort zijn, en juist het verschil is hier het punt: een lijn die omhoog
    # loopt betekent dat er iets wegvalt omdat iemand dat zo ingesteld heeft.
    "filter_on":              ("filter", "Filter aan", None, 0),
    "filter_dropped":         ("filter", "Weggegooid totaal", None, 1),
    "filter_passed":          ("filter", "Doorgelaten", None, 2),
    "filter_exempt":          ("filter", "Vrijgesteld (ACL)", None, 3),
    "filter_drop_hops":       ("filter", "Weg: te veel hops", None, 4),
    "filter_drop_rate":       ("filter", "Weg: snelheidslimiet", None, 5),
    "filter_drop_type":       ("filter", "Weg: type dicht", None, 6),
    "filter_drop_hash":       ("filter", "Weg: padhash te klein", None, 7),
    "filter_drop_channel":    ("filter", "Weg: geblokkeerd kanaal", None, 8),
    "filter_drop_malformed":  ("filter", "Weg: misvormde groepstekst", None, 9),
    # De druk op de snelheidslimiet, als twee reeksen. Zonder noemer zegt 'de
    # limiet heeft 12 keer gebeten' niets: 12 van de 4000 vensters is een limiet
    # die ruim staat, 12 van de 14 is er een die structureel verkeer wegsnijdt,
    # en het aantal weggegooide pakketten kan in beide gevallen gelijk zijn.
    # Dezelfde redenering als 'Doorgelaten' naast 'Weggegooid'.
    "filter_rate_windows":    ("filter", "Snelheidsvensters met verkeer", None, 10),
    "filter_rate_capped":     ("filter", "Vensters waarin de limiet beet", None, 11),
    # Overig (bekend maar minder prominent)
    "request_successes":      ("other", "Verzoeken gelukt", None, 0),
    "request_failures":       ("other", "Verzoeken mislukt", None, 1),
    "out_path":               ("other", "Uitgaand pad", None, 2),
}

SECTIONS = [
    ("status", "Status"),
    ("battery", "Batterij & solar"),
    ("channels", "Kanalen"),
    ("messages", "Berichten"),
    ("airtime", "Airtime"),
    ("filter", "Pakketfilter"),
    ("other", "Overig"),
]

# Tegels die prominent bovenaan staan (zoals de tiles in het HA-dashboard)
TILE_METRICS = {
    "status": ["online", "uptime", "neighbor_count", "tx_queue_len",
               "noise_floor", "last_rssi", "last_snr", "out_path_len",
               "mcu_temperature", "wifi_rssi"],
    "battery": ["battery_percentage", "bat", "ch1_voltage", "ch1_temperature"],
    "messages": ["nb_recv", "nb_sent", "recv_flood", "recv_direct",
                 "sent_flood", "sent_direct", "flood_dups", "recv_errors"],
    "airtime": ["airtime_utilization", "rx_airtime_utilization",
                "airtime", "rx_airtime"],
    # 'Doorgelaten' staat er met opzet naast 'Weggegooid': een weggooiteller
    # zonder noemer zegt niets. Duizend weg is veel op tienduizend en bijna
    # niets op een miljoen, en dat verschil is precies wat je wil weten voor je
    # aan de regels gaat zitten.
    "filter": ["filter_on", "filter_dropped", "filter_passed", "filter_exempt"],
}

# Charts on a repeater page: (key, title, [metrics], hours). The key is the
# translation key for the title; the Dutch title is the no-JavaScript fallback.
CHARTS = [
    ("voltage", "Spanning (24 u)", ["bat", "ch1_voltage"], 24),
    ("battery_week", "Batterijspanning (7 d)", ["bat"], 168),
    ("temperature", "Temperatuur (48 u)", ["ch1_temperature"], 48),
    # Its own chart rather than a second line on the one above. On the node that
    # was renamed, the old ch1_temperature series *is* chip temperature under a
    # name that promised something else; drawing the two in one frame, one
    # stopping where the other starts, would read as a single measurement with a
    # gap in it. Two panels say plainly that these are two series.
    ("mcu_temperature", "Chiptemperatuur (48 u)", ["mcu_temperature"], 48),
    ("msg_rates", "Berichtenrates (24 u)", ["nb_recv_rate", "nb_sent_rate"], 24),
    ("neighbor_count", "Aantal buren (7 d)", ["neighbor_count"], 168),
    # Weggegooid naast doorgelaten in één frame, want dat is de vergelijking die
    # de vraag beantwoordt. Alleen getekend als er ooit iets van gemeld is: een
    # lege grafiek op elke nodepagina zou suggereren dat er iets stuk is op elke
    # node die simpelweg geen filter heeft.
    ("filter", "Pakketfilter (24 u)", ["filter_dropped", "filter_passed"], 24),
    # De druk op de snelheidslimiet in één frame: hoe vaak beet hij, tegen hoe
    # vaak hij de kans had. Twee lijnen die uit elkaar lopen betekent een limiet
    # die ruim staat; twee die tegen elkaar aan kruipen er een die knelt.
    ("filter_rate", "Snelheidslimiet (24 u)",
     ["filter_rate_capped", "filter_rate_windows"], 24),
]

# Meters (gauges): metric -> (min, max, [(vanaf, kleur), ...])
GAUGES = {
    "battery_percentage": (0, 100, [(0, "#ff5c5c"), (25, "#ffb454"), (50, "#35e08c")]),
    # Werkbereik van een gemiddelde 1S-lithiumcel: onder ~3,4 V wordt het kritiek
    "bat": (3.0, 4.2, [(3.0, "#ff5c5c"), (3.4, "#ffb454"), (3.7, "#35e08c")]),
    "airtime_utilization": (0, 10, [(0, "#35e08c"), (2, "#ffb454"), (5, "#ff5c5c")]),
    "rx_airtime_utilization": (0, 100, [(0, "#35e08c"), (30, "#ffb454"), (60, "#ff5c5c")]),
    # A dial, deliberately not the thermometer below. Silicon and outside air are
    # not the same quantity, and a thermometer pointing at 60 next to an ambient
    # reading of 25 invites exactly the wrong conclusion. The scale is the chip's:
    # an ESP32-S3 with WiFi on sits happily around 50-70 °C, gets worth watching
    # past 75, and is genuinely hot past 90.
    "mcu_temperature": (0, 110, [(0, "#35e08c"), (75, "#ffb454"), (90, "#ff5c5c")]),
}

# Thermometers: metric -> (min, max, [(vanaf, kleur), ...])
# Only for temperatures of the world around the node -- a real sensor channel.
# The MCU die temperature is a gauge above; see the comment there.
THERMOMETERS = {
    "ch1_temperature": (-20, 80, [(-20, "#4cc9f0"), (0, "#35e08c"), (45, "#ffb454"), (60, "#ff5c5c")]),
    "ch2_temperature": (-20, 80, [(-20, "#4cc9f0"), (0, "#35e08c"), (45, "#ffb454"), (60, "#ff5c5c")]),
}

# Extra explanation on a tile, as a tooltip. The key is a translation key; the
# Dutch text is the no-JavaScript fallback. Only for metrics whose name alone
# invites a wrong reading.
HINTS = {
    "mcu_temperature": ("metric_hint.mcu_temperature",
                        "Temperatuur van de chip zelf, niet van de buitenlucht. "
                        "Een ESP32-S3 met WiFi aan draait 20 à 30 °C boven de omgeving."),
    "wifi_rssi": ("metric_hint.wifi_rssi",
                  "Het signaal van de WiFi-verbinding, niet van de LoRa-radio. "
                  "Deze node wordt over IP beheerd; zakt dit weg, dan verdwijnt "
                  "die beheerweg en blijft alleen het mesh over."),
}


# ---- kanaalmetingen ---------------------------------------------------------
#
# Een sensornode antwoordt in CayenneLPP: per meting een kanaalnummer, een type
# en een waarde. De firmware die zo'n node uitleest bewaart elke meting onder
# het kanaal dat de bron zelf gebruikte en hernoemt niets -- zie
# monDecodeTelemetry in de repeaterfirmware. Wat hier binnenkomt heet dus
# ``ch<N>_voltage``, ``ch<N>_temperature``, ``ch<N>_switch`` of
# ``ch<N>_generic``, met N het kanaalnummer van de bron.
#
# Twee LPP-types op HETZELFDE kanaal is normaal en geen vergissing: een
# uptimemonitor zet per dienst een switch (bereikbaar ja/nee) én een generic
# sensor (responstijd) op één nummer. Het soort zit daarom in de metricnaam en
# niet alleen het kanaal, zodat beide naast elkaar bewaard blijven in plaats van
# elkaar te overschrijven.
#
# De MENSELIJKE naam hoort hier niet: die is per node verschillend en staat in
# de tabel ``channel_names`` (zie db.channel_names_for). Deze module levert het
# soortlabel en de indeling; de pagina plakt de naam ervoor.
CHANNEL_METRIC = re.compile(r"^ch([0-9]{1,3})_(voltage|temperature|switch|generic)$")

# soort -> (label, eenheid, sorteervolgorde binnen één kanaal)
CHANNEL_KINDS = {
    "voltage":     ("spanning", "V", 0),
    "temperature": ("temperatuur", "°C", 1),
    "switch":      ("toestand", None, 2),
    # Bewust geen eenheid: LPP_GENERIC_SENSOR is 4 byte met vermenigvuldiger 1
    # en belooft niets over wát er gemeten is. Een uptimemonitor zet er hele
    # milliseconden pingtijd in, maar dat weet alleen wie de node kent -- vandaar
    # het eenheidsveld bij de naam, per node in te vullen.
    "generic":     ("meetwaarde", None, 3),
}


def channel_metric(name: str):
    """``(kanaal, soort)`` voor een kanaalmeting, anders ``None``."""
    m = CHANNEL_METRIC.match(name)
    return (int(m.group(1)), m.group(2)) if m else None


def channel_label(channel: int, kind: str, name: str | None = None) -> str:
    """Het label bij één kanaalmeting.

    Zonder naam wordt dat "kanaal 6 — toestand". Een naamloos kanaal moet
    zichtbaar blijven: een meting waarvan we de naam niet kennen is nog steeds
    een meting, en ze verbergen zou de node stiller maken dan hij is.
    """
    kind_label = CHANNEL_KINDS[kind][0]
    head = (name or "").strip() or f"kanaal {channel}"
    return f"{head} — {kind_label}"


def channel_unit(kind: str, unit: str | None = None) -> str | None:
    """De eenheid: die van het LPP-type, of de door de beheerder gezette."""
    kind_unit = CHANNEL_KINDS[kind][1]
    return kind_unit or ((unit or "").strip() or None)


def channels_seen(names) -> list[dict]:
    """De kanalen die in deze metricnamen voorkomen, op nummer gesorteerd.

    Per kanaal: het nummer en de soorten die eronder binnenkwamen. Dat laatste is
    wat de beheerpagina nodig heeft om te kunnen zeggen wát er op een kanaal
    staat, want een kanaal met een switch én een generic sensor is een dienst met
    een toestand en een responstijd, en een kanaal met alleen een switch is dat
    niet.

    Gaten in de nummering blijven gaten. Ze worden niet opgevuld en niet
    hernummerd: zie de waarschuwing bij db.channel_names_for -- een verschoven
    nummer laat elke bewaarde naam stil naar de verkeerde dienst wijzen.
    """
    found: dict[int, set] = {}
    for name in names:
        ch = channel_metric(name)
        if ch is not None:
            found.setdefault(ch[0], set()).add(ch[1])
    return [
        {"channel": channel,
         "kinds": sorted(kinds, key=lambda k: CHANNEL_KINDS[k][2]),
         "kind_labels": [CHANNEL_KINDS[k][0]
                         for k in sorted(kinds, key=lambda k: CHANNEL_KINDS[k][2])],
         # Alleen bij een generic sensor heeft een eigen eenheid zin: de andere
         # soorten hebben er al een uit het LPP-type (V, °C) of horen er geen te
         # hebben (een toestand is op of neer).
         "wants_unit": "generic" in kinds}
        for channel, kinds in sorted(found.items())
    ]


def metric_info(name: str, names=None):
    """(section, label, unit, sort) — met fallback voor onbekende metrics.

    ``names`` is de kanaalnaamtabel van ÉÉN node (``db.channel_names_for``), en
    hij is optioneel omdat het antwoord zonder hem nog steeds bruikbaar is: dan
    heet kanaal 6 "kanaal 6". Meegeven hoort de regel te zijn en niet de
    uitzondering -- een tegel die ``ch6_generic`` heet is voor niemand leesbaar,
    en dat is een implementatiedetail dat naar buiten lekt.

    **Eén functie en geen tweede per pagina.** Deze koppeling gold tot nu toe
    alleen op de plekken die er zelf aan dachten: de publieke tegels en de
    JSON-API hadden elk hun eigen paar regels, de grafieken hun eigen, en de
    catalogus wist er niets van. Vier plaatsen die hetzelfde moeten zeggen, is
    drie te veel -- en de vierde die het niet doet is precies de plek waar een
    lezer straks ``ch6_generic`` ziet staan.

    De NAAM wint van het label, de INDELING blijft. ``ch1_voltage`` staat in
    CATALOG onder de batterij en blijft daar staan; wat een naam verandert is hoe
    het kanaal HEET, niet waar het op de pagina hoort. Anders zou een node die
    één kanaal een naam geeft zijn hele pagina zien herschikken.
    """
    ch = channel_metric(name)
    known = CATALOG.get(name)

    if ch is not None:
        channel, kind = ch
        gezet = (names or {}).get(channel)
        # De naam en de eenheid zoals ze gezet zijn, of niets. Een sqlite3.Row
        # laat zich met [] uitlezen en een dict ook, dus beide kunnen hier binnen
        # zonder dat de aanroeper hem eerst hoeft om te bouwen.
        naam = (gezet["name"] if gezet is not None else "") or ""
        eenheid = (gezet["unit"] if gezet is not None else "") or ""
        if known is not None:
            # Kanaal 1 en 2 staan in de catalogus, bij de batterij: op een
            # MeshCore-repeater is kanaal 1 het eigen bord. Een gezette naam
            # overschrijft dat label wél -- die is specifieker dan onze catalogus,
            # want de beheerder weet welk kanaal welke sensor is -- maar de sectie
            # en de sorteervolgorde blijven van de catalogus.
            sectie, label, eenheid_catalogus, sort = known
            if naam:
                return (sectie, channel_label(channel, kind, naam),
                        channel_unit(kind, eenheid) or eenheid_catalogus, sort)
            return known
        # Kanaalmetingen krijgen hun eigen sectie in plaats van 'Overig'.
        # Op kanaal, dan op soort: de metingen van één dienst komen zo bij
        # elkaar te staan in plaats van alle toestanden bij elkaar en alle
        # pingtijden bij elkaar.
        sort = channel * 10 + CHANNEL_KINDS[kind][2]
        return ("channels", channel_label(channel, kind, naam),
                channel_unit(kind, eenheid), sort)

    if known is not None:
        return known
    return ("other", name.replace("_", " "), None, 99)


# ---- instelbare weergave (beheerd via /admin, opgeslagen in settings) -------

DEFAULT_RANGES = [4, 24, 48, 168, 744, 2160]  # uren in het historiekvenster

# Blokken op de publieke repeaterpagina, in standaardvolgorde
DEFAULT_LAYOUT = [
    {"key": "status", "visible": True},
    {"key": "battery", "visible": True},
    {"key": "channels", "visible": True},
    {"key": "messages", "visible": True},
    {"key": "airtime", "visible": True},
    # Het pakketfilter had wel een sectie met tegels en een grafiek, maar stond
    # niet in deze lijst -- en parse_layout laat alleen door wat in BLOCK_NAMES
    # staat, dus die tegels zijn nooit op een nodepagina terechtgekomen. Sinds
    # 2.6.0 staat de uitsplitsing er ook in, en dat is meteen de reden dat het
    # gemist werd: er was tot nu toe weinig te zien.
    {"key": "filter", "visible": True},
    {"key": "other", "visible": True},
    {"key": "charts", "visible": True},
    {"key": "map", "visible": True},
    {"key": "neighbors", "visible": True},
]
BLOCK_NAMES = {
    "status": "Status", "battery": "Batterij & solar", "channels": "Kanalen",
    "messages": "Berichten",
    "airtime": "Airtime", "filter": "Pakketfilter", "other": "Overig",
    "charts": "Grafieken", "map": "Linkkaart", "neighbors": "Buren",
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
