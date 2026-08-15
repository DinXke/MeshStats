/* Bilingual public site (Dutch / English), entirely client-side.
 *
 * The templates render Dutch and tag every translatable node with data-i18n, so
 * the page stays readable without JavaScript and search engines still see real
 * content. This script swaps that text out when the visitor picks English.
 * Nothing about the language lives on the server: no sessions, no per-language
 * URLs, and the choice rides along in localStorage next to the theme and the
 * collapsed-block preferences.
 *
 * Text that JavaScript builds itself (relative times, map tooltips, chart
 * labels) must go through MCSI18N.t as well, otherwise the page ends up half
 * translated the moment anything re-renders.
 *
 * Keys carry the Dutch wording as their fallback, so a missing translation
 * degrades to Dutch instead of showing a raw key.
 */
(function () {
  "use strict";

  var DICT = {
    nl: {
      "nav.admin": "⚙ Beheer",
      "nav.theme_title": "Wissel licht/donker thema",
      "nav.lang_title": "Taal wisselen / switch language",
      "footer.text": "MeshCore-statistieken · gevoed door Home Assistant · ",
      "footer.admin": "beheer",

      // --- inloggen (de rest van /admin is enkel Nederlands) ---
      "login.invalid": "Ongeldige inloggegevens",
      "login.invalid_throttled": "Ongeldige inloggegevens — te veel pogingen, wacht {n} s.",
      "login.throttled": "Te veel mislukte pogingen. Probeer over {n} s opnieuw.",
      "login.expired": "Sessie verlopen — probeer opnieuw.",

      // --- startpagina ---
      "home.title": "Repeaters",
      "home.hint": "Live MeshCore-repeaterstatistieken · klik op een repeater voor details",
      "home.empty": "Nog geen repeaters. Zodra Home Assistant data doorstuurt verschijnen ze hier.",
      "home.lastseen": "laatst gezien",
      "card.battery": "Batterij",
      "card.uptime": "Uptime",
      "card.neighbors": "Buren",
      "card.temperature": "Temperatuur",

      // --- live pakketkaart ---
      "live.title": "Live pakketten",
      "live.hint": "Elk pakket dat een node opvangt, flitst op de plek van de afzender · klik op een pakket voor alle details",
      "live.waiting": "wachten op verkeer…",
      "live.count": "{n} pakketten in de laatste 5 minuten",
      "live.hops": "{n} hop",
      "live.hops_plural": "{n} hops",
      "live.filter_ph": "Filter op naam, prefix, type of land…",
      "live.filter_aria": "Pakketten filteren",
      "live.country_aria": "Filteren op land",
      "live.country_all": "Alle landen",
      "live.country_none": "Land onbekend",
      "live.nomatch": "Geen pakketten die aan het filter voldoen.",
      "live.map_nomatch": "Geen nodes op de kaart die aan het filter voldoen.",
      "live.filtered": "{n} van {total} komen overeen",
      "live.motion": "Pakketten laten bewegen",
      "live.motion_title": "Toon elk pakket als een stipje dat van de afzender via elke hop naar de waarnemende node reist. Een stuk pad dat we niet kennen, staat gestippeld.",
      "live.motion_reduced": "Uitgeschakeld: je systeem vraagt om minder beweging. Pakketten flitsen in plaats daarvan op.",
      "live.heat": "Drukte op paden tonen",
      "live.heat_title": "Tekent elke verbinding waarover in de hele bewaarde periode pakketten reisden; hoe drukker bereisd ten opzichte van de rest, hoe dikker de lijn. Alleen stukken tussen twee eenduidig geplaatste nodes tellen mee.",
      "live.heat_tip": "{a} ↔ {b} · {n}× bereisd in {days} d",
      "live.heat_capped": "Let op: er waren meer pakketten dan de kaart in één keer aankan; de oudste tellen nu niet mee.",

      // --- kolomkoppen van de pakkettenlijst ---
      "col.sender": "Afzender",
      "col.time": "Tijd",
      "col.observer": "Gehoord door",
      "col.type": "Type",
      "col.scope": "Bereik",
      "col.snr": "SNR",
      "col.rssi": "RSSI",
      "col.hops": "Hops",
      "col.len": "Lengte",
      "col.country": "Land",

      // --- pakketarchief ---
      "arch.title": "Pakketarchief",
      "arch.hint": "Doorzoek alle bewaarde pakketten · pakketten blijven {days} dagen bewaard",
      "arch.link": "Zoeken in het archief",
      "arch.query_ph": "bv. type:ADVERT scope:scoped snr:>5 · leeg = alles",
      "arch.query_aria": "Zoekopdracht",
      "arch.window_aria": "Tijdvenster",
      "arch.search": "Zoeken",
      "arch.w1": "laatste uur",
      "arch.w6": "laatste 6 uur",
      "arch.w24": "laatste 24 uur",
      "arch.w72": "laatste 3 dagen",
      "arch.w168": "laatste 7 dagen",
      "arch.wall": "alles",
      "arch.help": "Zoektaal en velden",
      "arch.help_syntax": "Clausules met een spatie ertussen moeten allemaal gelden. veld:waarde zoekt exact, 2ae7* op prefix, -type:ACK sluit uit, type:(ADVERT OR ACK) is een van beide, snr:>5 en len:20..40 voor getallen, aanhalingstekens voor een waarde met spaties. Een los woord zoekt in namen, sleutels, type, bereik en land.",
      "arch.count": "{n} pakketten gevonden",
      "arch.count_one": "1 pakket gevonden",
      "arch.empty": "Geen pakketten die aan de zoekopdracht voldoen.",
      "arch.page": "{from}–{to} van {total}",
      "arch.prev": "← nieuwer",
      "arch.next": "ouder →",
      "arch.loaderror": "Kon de zoekresultaten niet laden.",
      "arch.facet_add": "Voeg {q} toe aan de zoekopdracht",
      "arch.filter_add": "Alleen deze waarde — voegt {q} toe aan de zoekopdracht",
      "arch.filter_not": "Deze waarde uitsluiten — voegt {q} toe aan de zoekopdracht",
      "arch.f_type": "Payloadtype",
      "arch.f_route": "Routetype",
      "arch.f_scope": "Bereik",
      "arch.f_region": "Regio",
      "arch.f_sender": "Afzender (sleutel)",
      "arch.f_observer": "Waarnemer (sleutel)",
      "arch.f_name": "Naam van afzender of waarnemer",
      "arch.f_country": "Land",
      "arch.f_snr": "SNR",
      "arch.f_rssi": "RSSI",
      "arch.f_len": "Lengte in bytes",
      "arch.f_hops": "Aantal hops",
      "arch.f_path": "Hop in het pad",
      "arch.f_hash": "Payloadhash",

      // --- bereik van een pakket (transportcodes) ---
      "scope.unscoped": "ongescoped",
      "scope.scoped": "gescoped",
      "scope.share": "Share",
      "scope.unscoped_note": "geen transportcodes, verspreidt zich overal waar het raakt",
      "scope.share_note": "beide codes 0, oftewel 'naar nergens': zo ziet een advert eruit die via Share in de app is binnengehaald in plaats van uit de lucht gehoord",
      "scope.region": "regio {n}",
      "scope.region_unnamed": "de afzender noemt geen regio. Alleen de tweede transportcode kan er een noemen en die staat op 0; de eerste is een controlegetal over dit ene pakket, geen regionummer",

      // --- pakketdetail ---
      "pkt.title": "Pakketdetail",
      "pkt.sheet_grip": "Paneel hoger of lager slepen",
      "pkt.time": "Tijdstip",
      "pkt.sender": "Afzender",
      "pkt.observer": "Gehoord door",
      "pkt.type": "Payloadtype",
      "pkt.route": "Routetype",
      "pkt.scope": "Bereik",
      "pkt.scope_codes": "Transportcodes",
      "pkt.snr": "SNR",
      "pkt.rssi": "RSSI",
      "pkt.len": "Lengte",
      "pkt.pathlen": "Padlengte",
      "pkt.path": "Pad",
      "pkt.raw": "Ruwe bytes (hex)",
      "pkt.copy": "Kopieer",
      "pkt.copied": "Gekopieerd",
      "pkt.advert": "Inhoud van de advert",
      "pkt.adv_name": "Naam",
      "pkt.adv_coords": "Coördinaten",
      "pkt.adv_type": "Nodetype",
      "pkt.adv_ts": "Tijdstempel",
      "pkt.unknown": "onbekend",
      "pkt.country": "Land",
      "pkt.country_unknown": "onbekend",
      "pkt.country_of_sender": "positie van de afzender",
      "pkt.country_of_observer": "positie van de waarnemer",
      "pkt.sender_unknown": "onbekend — enkel adverts noemen hun afzender voluit",
      "pkt.sender_short": "onbekend",
      "pkt.dest": "Bestemming",
      // "1-byte hash" alleen leidde tot verwarring met de hashgrootte van het
      // pad: die is 1, 2 of 3 bytes en per pakket verschillend. De adreshash van
      // afzender en bestemming is iets anders en ligt in het protocol vast op
      // één byte, wat geen node kan instellen. Dat verschil staat nu in de tekst.
      "pkt.src_from_hash": "afgeleid uit adreshash 0x{h}, die in dit protocol altijd 1 byte is",
      "pkt.src_multi": "{n} mogelijk",
      "pkt.src_candidates": "adreshash 0x{h} (protocolvast op 1 byte) past op: {list}",
      "pkt.hopsize": "Hashgrootte pad",
      "pkt.hopsize_one": "1 byte per hop · gekozen door de verzendende node, niet door het protocol",
      "pkt.hopsize_n": "{n} bytes per hop · gekozen door de verzendende node, niet door het protocol",
      "pkt.nopath": "Geen hops: rechtstreeks van de afzender gehoord.",
      "pkt.path_unstored": "Het pad van dit pakket is niet bewaard (ouder dan deze functie).",
      "pkt.noraw": "Niet bewaard voor dit pakket.",
      "pkt.hop_unknown": "onbekende node",
      "pkt.hop_nolocation": "locatie onbekend",
      "pkt.hop_ambiguous": "{n} mogelijke nodes",
      "pkt.hop_maybe": "mogelijk: {name}",
      "pkt.origin": "afzender",
      "pkt.destination": "waarnemer",
      "pkt.path_note": "Een hop is maar 1, 2 of 3 bytes van een publieke sleutel — zie de hashgrootte hieronder — dus meerdere nodes kunnen dezelfde hop opleveren. Onzekere stukken staan gestippeld op de kaart.",
      "pkt.path_note_direct": "Direct gerouteerd: het pad is de nog af te leggen route, niet de reeds afgelegde.",
      "pkt.loaderror": "Kon de details van dit pakket niet laden.",

      // --- repeaterpagina ---
      "status.online": "ONLINE",
      "status.offline": "OFFLINE",
      "rep.refresh": "↻ Status opvragen",
      "rep.refresh_title": "Vraag via Home Assistant een verse status en telemetrie op over LoRa",
      "rep.settings": "⚙ Instellingen",
      "rep.settings_title": "CLI-instellingen van deze repeater",
      "rep.refresh_notice": "⏳ Statusupdate aangevraagd — Home Assistant vraagt de repeater nu uit; binnen ±1 minuut verschijnt een vers datapunt.",
      "rep.lastupdate": "laatste update",
      "rep.hint": "💡 Klik op een tegel of buur voor de historiek",

      // --- blokken ---
      "block.status": "Status",
      "block.battery": "Batterij & solar",
      "block.messages": "Berichten",
      "block.airtime": "Airtime",
      "block.other": "Overig",
      "block.charts": "Grafieken",
      "block.map": "Linkkaart",
      "block.neighbors": "Buren ({n})",

      // --- burentabel ---
      "nb.node": "Node",
      "nb.prefix": "Prefix",
      "nb.snr": "SNR (dB)",
      "nb.link": "Link",
      "nb.lastheard": "Laatst gehoord",
      "nb.link_snr": "Link {name} — SNR",

      // --- kaart ---
      "map.labels": "SNR-labels tonen",
      "map.nolocation": "Nog geen locatie bekend voor deze repeater.",
      "map.legend": "SNR link",
      "map.legend_good": "goed (≥0 dB)",
      "map.legend_ok": "matig (-10..0 dB)",
      "map.legend_bad": "zwak (<-10 dB)",
      "map.unlocated": "{n} buur/buren zonder bekende locatie niet op de kaart",
      "map.unlocated_intro": "Nog geen advert met locatie ontvangen van: ",

      // --- historiekvenster ---
      "modal.close": "Sluiten",
      "modal.empty": "Nog geen historiek voor deze periode.",

      // --- tijd en periodes ---
      "time.now": "zonet",
      "time.min": "{n} min geleden",
      "time.hour": "{n} u geleden",
      "time.day": "{n} d geleden",
      "range.hours": "{n} u",
      "range.days": "{n} d",
      "fmt.uptime_dh": "{d} d {h} u",
      "fmt.uptime_hm": "{h} u {m} min",
      "fmt.uptime_m": "{m} min",

      // --- grafiektitels ---
      "chart.voltage": "Spanning (24 u)",
      "chart.battery_week": "Batterijspanning (7 d)",
      "chart.temperature": "Temperatuur (48 u)",
      "chart.mcu_temperature": "Chiptemperatuur (48 u)",
      "chart.msg_rates": "Berichtenrates (24 u)",
      "chart.neighbor_count": "Aantal buren (7 d)",

      // --- metrieken (moeten gelijklopen met metrics.CATALOG) ---
      "metric.online": "Online",
      "metric.uptime": "Uptime",
      "metric.neighbor_count": "Buren (repeaters gezien)",
      "metric.tx_queue_len": "TX-wachtrij",
      "metric.noise_floor": "Ruisvloer",
      "metric.last_rssi": "Laatste RSSI",
      "metric.last_snr": "Laatste SNR",
      "metric.out_path_len": "Padlengte",
      "metric.mcu_temperature": "Chiptemperatuur",
      "metric_hint.mcu_temperature": "Temperatuur van de chip zelf, niet van de buitenlucht. Een ESP32-S3 met WiFi aan draait 20 à 30 °C boven de omgeving.",
      "metric.battery_percentage": "Batterij",
      "metric.bat": "Batterijspanning",
      "metric.ch1_voltage": "Ch1 spanning",
      "metric.ch1_temperature": "Ch1 temperatuur",
      "metric.ch2_voltage": "Ch2 spanning",
      "metric.ch2_temperature": "Ch2 temperatuur",
      "metric.ch1_battery": "Ch1 batterij",
      "metric.ch1_current": "Ch1 stroom",
      "metric.nb_recv": "Ontvangen totaal",
      "metric.nb_sent": "Verzonden totaal",
      "metric.recv_flood": "Ontvangen flood",
      "metric.recv_direct": "Ontvangen direct",
      "metric.sent_flood": "Verzonden flood",
      "metric.sent_direct": "Verzonden direct",
      "metric.flood_dups": "Flood-dubbelen",
      "metric.recv_errors": "RX-fouten",
      "metric.nb_recv_rate": "Ontvangstrate",
      "metric.nb_sent_rate": "Verzendrate",
      "metric.recv_flood_rate": "Ontvangen flood-rate",
      "metric.recv_direct_rate": "Ontvangen direct-rate",
      "metric.sent_flood_rate": "Verzonden flood-rate",
      "metric.sent_direct_rate": "Verzonden direct-rate",
      "metric.flood_dups_rate": "Flood-dubbelen-rate",
      "metric.direct_dups_rate": "Direct-dubbelen-rate",
      "metric.recv_errors_rate": "RX-foutenrate",
      "metric.direct_dups": "Direct-dubbelen",
      "metric.full_evts": "Volle wachtrij-events",
      "metric.airtime_utilization": "TX-benutting",
      "metric.rx_airtime_utilization": "RX-benutting",
      "metric.airtime": "TX-airtime totaal",
      "metric.rx_airtime": "RX-airtime totaal",
      "metric.request_successes": "Verzoeken gelukt",
      "metric.request_failures": "Verzoeken mislukt",
      "metric.out_path": "Uitgaand pad",
    },

    en: {
      "nav.admin": "⚙ Admin",
      "nav.theme_title": "Switch light/dark theme",
      "nav.lang_title": "Taal wisselen / switch language",
      "footer.text": "MeshCore statistics · fed by Home Assistant · ",
      "footer.admin": "admin",

      "login.invalid": "Invalid credentials",
      "login.invalid_throttled": "Invalid credentials — too many attempts, wait {n} s.",
      "login.throttled": "Too many failed attempts. Try again in {n} s.",
      "login.expired": "Session expired — please try again.",

      "home.title": "Repeaters",
      "home.hint": "Live MeshCore repeater statistics · click a repeater for details",
      "home.empty": "No repeaters yet. They appear here as soon as Home Assistant sends data.",
      "home.lastseen": "last seen",
      "card.battery": "Battery",
      "card.uptime": "Uptime",
      "card.neighbors": "Neighbours",
      "card.temperature": "Temperature",

      "live.title": "Live packets",
      "live.hint": "Every packet a node overhears flashes at the sender's location · click a packet for all its details",
      "live.waiting": "waiting for traffic…",
      "live.count": "{n} packets in the last 5 minutes",
      "live.hops": "{n} hop",
      "live.hops_plural": "{n} hops",
      "live.filter_ph": "Filter by name, prefix, type or country…",
      "live.filter_aria": "Filter packets",
      "live.country_aria": "Filter by country",
      "live.country_all": "All countries",
      "live.country_none": "Country unknown",
      "live.nomatch": "No packets match the filter.",
      "live.map_nomatch": "No nodes on the map match the filter.",
      "live.filtered": "{n} of {total} match",
      "live.motion": "Animate packets",
      "live.motion_title": "Show every packet as a dot travelling from the sender via each hop to the observing node. A stretch of path we do not know is dashed.",
      "live.motion_reduced": "Off: your system asks for reduced motion. Packets flash instead.",
      "live.heat": "Show path usage",
      "live.heat_title": "Draws every link packets travelled over the whole retained period; the busier a link compared to the rest, the thicker the line. Only stretches between two unambiguously placed nodes count.",
      "live.heat_tip": "{a} ↔ {b} · travelled {n}× in {days} d",
      "live.heat_capped": "Note: there were more packets than the map can take in at once; the oldest are left out for now.",

      "col.sender": "Sender",
      "col.time": "Time",
      "col.observer": "Heard by",
      "col.type": "Type",
      "col.scope": "Scope",
      "col.snr": "SNR",
      "col.rssi": "RSSI",
      "col.hops": "Hops",
      "col.len": "Length",
      "col.country": "Country",

      // --- packet archive ---
      "arch.title": "Packet archive",
      "arch.hint": "Search every retained packet · packets are kept for {days} days",
      "arch.link": "Search the archive",
      "arch.query_ph": "e.g. type:ADVERT scope:scoped snr:>5 · empty = everything",
      "arch.query_aria": "Search query",
      "arch.window_aria": "Time window",
      "arch.search": "Search",
      "arch.w1": "last hour",
      "arch.w6": "last 6 hours",
      "arch.w24": "last 24 hours",
      "arch.w72": "last 3 days",
      "arch.w168": "last 7 days",
      "arch.wall": "everything",
      "arch.help": "Query language and fields",
      "arch.help_syntax": "Space-separated clauses must all hold. field:value matches exactly, 2ae7* by prefix, -type:ACK excludes, type:(ADVERT OR ACK) is either, snr:>5 and len:20..40 for numbers, quotes for a value with spaces. A bare word searches names, keys, type, scope and country.",
      "arch.count": "{n} packets found",
      "arch.count_one": "1 packet found",
      "arch.empty": "No packets match the query.",
      "arch.page": "{from}–{to} of {total}",
      "arch.prev": "← newer",
      "arch.next": "older →",
      "arch.loaderror": "Could not load the search results.",
      "arch.facet_add": "Add {q} to the query",
      "arch.filter_add": "Only this value — adds {q} to the query",
      "arch.filter_not": "Exclude this value — adds {q} to the query",
      "arch.f_type": "Payload type",
      "arch.f_route": "Route type",
      "arch.f_scope": "Scope",
      "arch.f_region": "Region",
      "arch.f_sender": "Sender (key)",
      "arch.f_observer": "Observer (key)",
      "arch.f_name": "Name of sender or observer",
      "arch.f_country": "Country",
      "arch.f_snr": "SNR",
      "arch.f_rssi": "RSSI",
      "arch.f_len": "Length in bytes",
      "arch.f_hops": "Hop count",
      "arch.f_path": "Hop in the path",
      "arch.f_hash": "Payload hash",

      // --- packet scope (transport codes) ---
      "scope.unscoped": "unscoped",
      "scope.scoped": "scoped",
      "scope.share": "Share",
      "scope.unscoped_note": "no transport codes, floods wherever it reaches",
      "scope.share_note": "both codes 0, meaning 'send to nowhere': what an advert looks like when it was imported through Share in the app instead of heard off the air",
      "scope.region": "region {n}",
      "scope.region_unnamed": "the sender names no region. Only the second transport code can name one and it is 0; the first is a check value over this one packet, not a region number",

      "pkt.title": "Packet detail",
      "pkt.sheet_grip": "Drag the panel up or down",
      "pkt.time": "Time",
      "pkt.sender": "Sender",
      "pkt.observer": "Heard by",
      "pkt.type": "Payload type",
      "pkt.route": "Route type",
      "pkt.scope": "Scope",
      "pkt.scope_codes": "Transport codes",
      "pkt.snr": "SNR",
      "pkt.rssi": "RSSI",
      "pkt.len": "Length",
      "pkt.pathlen": "Path length",
      "pkt.path": "Path",
      "pkt.raw": "Raw bytes (hex)",
      "pkt.copy": "Copy",
      "pkt.copied": "Copied",
      "pkt.advert": "Advert contents",
      "pkt.adv_name": "Name",
      "pkt.adv_coords": "Coordinates",
      "pkt.adv_type": "Node type",
      "pkt.adv_ts": "Timestamp",
      "pkt.unknown": "unknown",
      "pkt.country": "Country",
      "pkt.country_unknown": "unknown",
      "pkt.country_of_sender": "position of the sender",
      "pkt.country_of_observer": "position of the observer",
      "pkt.sender_unknown": "unknown — only adverts name their sender in full",
      "pkt.sender_short": "unknown",
      "pkt.dest": "Destination",
      "pkt.src_from_hash": "derived from address hash 0x{h}, always 1 byte in this protocol",
      "pkt.src_multi": "{n} possible",
      "pkt.src_candidates": "address hash 0x{h} (fixed at 1 byte by the protocol) matches: {list}",
      "pkt.hopsize": "Path hash size",
      "pkt.hopsize_one": "1 byte per hop · chosen by the sending node, not by the protocol",
      "pkt.hopsize_n": "{n} bytes per hop · chosen by the sending node, not by the protocol",
      "pkt.nopath": "No hops: heard straight from the sender.",
      "pkt.path_unstored": "The path of this packet was not stored (it predates this feature).",
      "pkt.noraw": "Not stored for this packet.",
      "pkt.hop_unknown": "unknown node",
      "pkt.hop_nolocation": "location unknown",
      "pkt.hop_ambiguous": "{n} possible nodes",
      "pkt.hop_maybe": "possibly: {name}",
      "pkt.origin": "sender",
      "pkt.destination": "observer",
      "pkt.path_note": "A hop is only 1, 2 or 3 bytes of a public key — see the hash size below — so several nodes can answer to the same hop. Uncertain stretches are dashed on the map.",
      "pkt.path_note_direct": "Direct routing: the path is the route still to travel, not the one already travelled.",
      "pkt.loaderror": "Could not load the details of this packet.",

      "status.online": "ONLINE",
      "status.offline": "OFFLINE",
      "rep.refresh": "↻ Request status",
      "rep.refresh_title": "Ask Home Assistant to fetch fresh status and telemetry over LoRa",
      "rep.settings": "⚙ Settings",
      "rep.settings_title": "CLI settings of this repeater",
      "rep.refresh_notice": "⏳ Status update requested — Home Assistant is querying the repeater now; a fresh data point appears within ±1 minute.",
      "rep.lastupdate": "last update",
      "rep.hint": "💡 Click a tile or a neighbour for its history",

      "block.status": "Status",
      "block.battery": "Battery & solar",
      "block.messages": "Messages",
      "block.airtime": "Airtime",
      "block.other": "Other",
      "block.charts": "Charts",
      "block.map": "Link map",
      "block.neighbors": "Neighbours ({n})",

      "nb.node": "Node",
      "nb.prefix": "Prefix",
      "nb.snr": "SNR (dB)",
      "nb.link": "Link",
      "nb.lastheard": "Last heard",
      "nb.link_snr": "Link {name} — SNR",

      "map.labels": "Show SNR labels",
      "map.nolocation": "No location known for this repeater yet.",
      "map.legend": "Link SNR",
      "map.legend_good": "good (≥0 dB)",
      "map.legend_ok": "fair (-10..0 dB)",
      "map.legend_bad": "weak (<-10 dB)",
      "map.unlocated": "{n} neighbour(s) without a known location are not on the map",
      "map.unlocated_intro": "No advert with a location received yet from: ",

      "modal.close": "Close",
      "modal.empty": "No history for this period yet.",

      "time.now": "just now",
      "time.min": "{n} min ago",
      "time.hour": "{n} h ago",
      "time.day": "{n} d ago",
      "range.hours": "{n} h",
      "range.days": "{n} d",
      "fmt.uptime_dh": "{d} d {h} h",
      "fmt.uptime_hm": "{h} h {m} min",
      "fmt.uptime_m": "{m} min",

      "chart.voltage": "Voltage (24 h)",
      "chart.battery_week": "Battery voltage (7 d)",
      "chart.temperature": "Temperature (48 h)",
      "chart.mcu_temperature": "Chip temperature (48 h)",
      "chart.msg_rates": "Message rates (24 h)",
      "chart.neighbor_count": "Neighbour count (7 d)",

      "metric.online": "Online",
      "metric.uptime": "Uptime",
      "metric.neighbor_count": "Neighbours (repeaters seen)",
      "metric.tx_queue_len": "TX queue",
      "metric.noise_floor": "Noise floor",
      "metric.last_rssi": "Last RSSI",
      "metric.last_snr": "Last SNR",
      "metric.out_path_len": "Path length",
      "metric.mcu_temperature": "Chip temperature",
      "metric_hint.mcu_temperature": "Temperature of the chip itself, not of the outside air. An ESP32-S3 with WiFi on runs 20 to 30 °C above ambient.",
      "metric.battery_percentage": "Battery",
      "metric.bat": "Battery voltage",
      "metric.ch1_voltage": "Ch1 voltage",
      "metric.ch1_temperature": "Ch1 temperature",
      "metric.ch2_voltage": "Ch2 voltage",
      "metric.ch2_temperature": "Ch2 temperature",
      "metric.ch1_battery": "Ch1 battery",
      "metric.ch1_current": "Ch1 current",
      "metric.nb_recv": "Received total",
      "metric.nb_sent": "Sent total",
      "metric.recv_flood": "Received flood",
      "metric.recv_direct": "Received direct",
      "metric.sent_flood": "Sent flood",
      "metric.sent_direct": "Sent direct",
      "metric.flood_dups": "Flood duplicates",
      "metric.recv_errors": "RX errors",
      "metric.nb_recv_rate": "Receive rate",
      "metric.nb_sent_rate": "Send rate",
      "metric.recv_flood_rate": "Received flood rate",
      "metric.recv_direct_rate": "Received direct rate",
      "metric.sent_flood_rate": "Sent flood rate",
      "metric.sent_direct_rate": "Sent direct rate",
      "metric.flood_dups_rate": "Flood duplicate rate",
      "metric.direct_dups_rate": "Direct duplicate rate",
      "metric.recv_errors_rate": "RX error rate",
      "metric.direct_dups": "Direct duplicates",
      "metric.full_evts": "Queue-full events",
      "metric.airtime_utilization": "TX utilisation",
      "metric.rx_airtime_utilization": "RX utilisation",
      "metric.airtime": "TX airtime total",
      "metric.rx_airtime": "RX airtime total",
      "metric.request_successes": "Requests succeeded",
      "metric.request_failures": "Requests failed",
      "metric.out_path": "Outgoing path",
    },
  };

  var STORAGE_KEY = "mcs-lang";

  function stored() {
    try {
      var v = localStorage.getItem(STORAGE_KEY);
      return DICT[v] ? v : null;
    } catch (e) {
      return null;   // localStorage can be blocked
    }
  }

  // A stored choice always wins; otherwise follow the browser and land on Dutch
  // for anything that is not clearly an English-speaking visitor.
  var lang = stored() ||
    (/^en\b/i.test(navigator.language || "") ? "en" : "nl");

  function t(key, vars) {
    var s = DICT[lang][key];
    if (s === undefined) s = DICT.nl[key];
    if (s === undefined) return key;
    if (vars) {
      s = s.replace(/\{(\w+)\}/g, function (m, name) {
        return vars[name] === undefined ? m : vars[name];
      });
    }
    return s;
  }

  function varsOf(el) {
    var raw = el.getAttribute("data-i18n-vars");
    if (!raw) return null;
    try {
      return JSON.parse(raw);
    } catch (e) {
      return null;
    }
  }

  function known(key) {
    return key in DICT.nl || key in DICT[lang];
  }

  function apply(root) {
    root = root || document;
    // Unknown keys keep whatever the template rendered. Metrics a node invents
    // land in the catalogue-less fallback path, and a Dutch label there beats a
    // literal "metric.some_sensor" on screen.
    function each(attr, set) {
      root.querySelectorAll("[" + attr + "]").forEach(function (el) {
        var key = el.getAttribute(attr);
        if (known(key)) set(el, t(key, varsOf(el)));
      });
    }
    each("data-i18n", function (el, v) { el.textContent = v; });
    each("data-i18n-title", function (el, v) { el.title = v; });
    each("data-i18n-ph", function (el, v) { el.placeholder = v; });
    each("data-i18n-aria", function (el, v) { el.setAttribute("aria-label", v); });
  }

  window.MCSI18N = { lang: lang, t: t, apply: apply, has: known };

  document.documentElement.lang = lang;
  apply(document);

  var btn = document.getElementById("lang-toggle");
  if (btn) {
    btn.textContent = lang.toUpperCase();
    btn.addEventListener("click", function () {
      try {
        localStorage.setItem(STORAGE_KEY, lang === "nl" ? "en" : "nl");
      } catch (e) { /* nothing to do; the reload just keeps the old language */ }
      // Reload rather than re-translate in place: charts, the Leaflet map and
      // every already-rendered tooltip would each need their own re-render,
      // which is a lot of machinery for something a visitor does once.
      location.reload();
    });
  }
})();
