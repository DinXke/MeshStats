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
      "footer.text": "MeshCore-statistieken · rechtstreeks van de nodes · ",
      "footer.admin": "beheer",

      // --- inloggen (de rest van /admin is enkel Nederlands) ---
      "login.invalid": "Ongeldige inloggegevens",
      "login.invalid_throttled": "Ongeldige inloggegevens — te veel pogingen, wacht {n} s.",
      "login.throttled": "Te veel mislukte pogingen. Probeer over {n} s opnieuw.",
      "login.expired": "Sessie verlopen — probeer opnieuw.",

      // --- startpagina ---
      "home.title": "Repeaters",
      "home.hint": "Live MeshCore-repeaterstatistieken · klik op een repeater voor details",
      "home.empty": "Nog geen repeaters. Zodra een node data doorstuurt verschijnen ze hier.",
      "home.lastseen": "laatst gezien",
      "card.battery": "Batterij",
      "card.uptime": "Uptime",
      "card.neighbors": "Buren",
      "card.temperature": "Temperatuur",

      // --- live pakketkaart ---
      "live.title": "Live pakketten",
      "live.hint": "Elk pakket dat een node opvangt, flitst op de plek van de afzender · klik op een pakket of op een bolletje voor alle details",
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
      // De kaart opent op waar het verkeer zit; wat daarbuiten valt wordt
      // geteld in plaats van verzwegen, en de tekst is meteen de weg terug.
      "live.outside": "{n} nodes buiten beeld · toon alles",
      "live.outside_one": "1 node buiten beeld · toon alles",
      "live.outside_title": "De kaart opent op de nodes die verkeer dragen, niet op elk ooit doorgegeven contact — anders bepaalt één ver contact de schaal. Niets is weggelaten: klik om alle nodes in beeld te brengen, of zoom zelf uit.",
      // Buiten beeld en niet op de kaart zijn twee verschillende dingen: het
      // eerste lost een klik op, het tweede niet. Vandaar een eigen tekst.
      "live.hidden": "{n} nodes tonen hun positie niet",
      "live.hidden_one": "1 node toont zijn positie niet",
      "live.hidden_title": "Deze site kent deze nodes wel, maar hun beheerder heeft gekozen hun positie niet publiek te tonen. Ze staan op geen enkele kaart hier, ook niet als je uitzoomt, en verbindingen naar hen tellen niet mee in de drukte-laag. Hun cijfers en hun naam kunnen er gewoon staan.",
      "live.motion": "Pakketten laten bewegen",
      "live.motion_title": "Toon elk pakket als een stipje dat van de afzender via elke hop naar de waarnemende node reist. Een stuk pad dat we niet kennen, staat gestippeld.",
      "live.motion_reduced": "Uitgeschakeld: je systeem vraagt om minder beweging. Pakketten flitsen in plaats daarvan op.",
      "live.heat": "Drukte op paden tonen",
      "live.heat_title": "Tekent elke verbinding waarover in de hele bewaarde periode pakketten reisden; hoe drukker bereisd ten opzichte van de rest, hoe warmer en dikker de lijn. Alleen stukken tussen twee eenduidig geplaatste nodes tellen mee.",
      "live.heat_tip": "{a} ↔ {b} · {n}× bereisd in {days} d",
      "live.heat_capped": "Let op: er waren meer pakketten dan de kaart in één keer aankan; de oudste tellen nu niet mee.",
      // Verzwijgen zou een ontbrekende drukke lijn laten lezen als een stil
      // stuk mesh; het is precies andersom.
      "live.heat_hidden": "Let op: {n} node(s) tonen hun positie niet, dus verbindingen van en naar hen staan hier niet.",
      "live.heat_min_aria": "Minimaal aantal ritten om een verbinding te tonen",
      "live.heat_min_title": "Verbergt verbindingen waarover minder vaak een pakket reisde dan de drempel. Helemaal links staat de drempel op 1 en zie je alles.",
      "live.heat_min_value": "drempel {n} ritten",
      "live.heat_shown": "{shown} van {total} verbindingen getoond · {hidden} onder de drempel",
      "live.heat_shown_all": "alle {total} verbindingen getoond",
      "live.heat_shown_none": "0 van {total} verbindingen getoond · niets haalt deze drempel",
      "live.heat_quiet": "rustig",
      "live.heat_busy": "druk",
      "live.heat_legend_title": "Kleur en dikte van een lijn zeggen hoe druk die verbinding is ten opzichte van de andere getoonde verbindingen.",

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
      // Alleen in het archief, waar de lezer zelf kolommen aan- en uitzet.
      "col.src": "Afz.hash",
      "col.dest": "Bestemming",
      "col.route": "Route",
      "col.region": "Regio",
      "col.path": "Pad",
      "col.hash": "Hash",

      // --- pakketarchief ---
      "arch.title": "Pakketarchief",
      "arch.hint": "Doorzoek alle bewaarde pakketten · pakketten blijven {days} dagen bewaard",
      // Alleen zichtbaar wanneer een opslaggrens eerder snijdt dan de termijn.
      // De belofte hierboven is dan niet meer waar, en wie een gat in het
      // archief ziet hoort te weten dat het aan die grens ligt en niet aan het
      // mesh.
      "arch.hint_short": "Doorzoek alle bewaarde pakketten · er staat nu {days} dagen aan pakketten van de ingestelde {set}: de opslaggrens snijdt eerder",
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
      "arch.help_syntax": "Clausules met een spatie ertussen moeten allemaal gelden. veld:waarde zoekt exact, 2ae7* op prefix, -type:ACK sluit uit, type:(ADVERT OR ACK) is een van beide, aanhalingstekens voor een waarde met spaties. Een los woord zoekt in namen, sleutels, type, bereik en land.",
      "arch.help_numbers": "Getalvelden — hops, snr, rssi, len, region — kennen ook groter en kleiner dan: hops:>3 zijn pakketten met meer dan drie hops, hops:<2 met minder dan twee, hops:2..5 alles daartussen. >= en <= mogen ook.",
      "arch.help_sort": "Klik op een kolomkop in de resultaten om daarop te sorteren; nog een klik draait de volgorde om.",
      "arch.table_aria": "Zoekresultaten",
      "arch.sort_label": "Sorteren op",
      "arch.sort_by": "Sorteer op deze kolom",
      "arch.sort_flip": "Volgorde omdraaien",
      "arch.sort_desc": "aflopend",
      "arch.sort_asc": "oplopend",
      "arch.cols": "Kolommen",
      "arch.cols_reset": "Standaardkolommen",
      "arch.col_hide": "Kolom {col} verbergen",
      "arch.col_unknown": "Onbekende kolom uit de link overgeslagen: {cols}.",
      "arch.sort_hidden": "Gesorteerd op {col}, een kolom die niet getoond wordt.",
      "arch.sort_hidden_add": "kolom tonen",
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
      "arch.f_dest": "Bestemming (hash)",
      "arch.f_src": "Afzender (hash)",
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
      // Kandidaatweging. De woordkeuze doet hier het werk: "meest
      // waarschijnlijk" en "rangschikking" moeten onmiskenbaar zeggen dat dit
      // een volgorde is en geen vaststelling, en "afgevallen" moet zeggen op
      // welke grond, zodat een lezer die het beter weet het kan tegenspreken.
      "pkt.hop_likely": "meest waarschijnlijk van {n}",
      "pkt.cand_ranked": "meest waarschijnlijk van {n} kandidaten — een rangschikking op wat we gemeten hebben, geen zekerheid",
      "pkt.cand_others": "andere kandidaten: {list}",
      "pkt.cand_also": "ook: {list}",
      "pkt.cand_why_direct": "staat vooraan omdat deze waarnemer hem rechtstreeks heeft gehoord",
      "pkt.cand_why_hop1": "staat vooraan omdat deze waarnemer hem al op 1 hop heeft gehoord",
      "pkt.cand_why_hops": "staat vooraan omdat deze waarnemer hem al op {n} hops heeft gehoord",
      "pkt.cand_why_near": "staat vooraan omdat hij het dichtst bij de waarnemer staat ({km} km)",
      "pkt.cand_why_recent": "staat vooraan omdat hij het recentst gezien is",
      "pkt.cand_dropped_one": "1 kandidaat afgevallen ({list}): te ver weg voor het aantal hops van dit pakket",
      "pkt.cand_dropped": "{n} kandidaten afgevallen ({list}): te ver weg voor het aantal hops van dit pakket",
      "pkt.cand_none_left": "geen kandidaat over",
      // Twee verschillende soorten niets, en het verschil is de moeite waard:
      // hierboven bleef er niemand over ná uitsluiting, hier paste er van meet
      // af aan niemand. In beide gevallen toont de rij de byte zelf — dat is
      // wat we wél weten, en het is in elk pakket van diezelfde afzender
      // dezelfde byte.
      "pkt.cand_none": "geen enkel bekend contact past op deze adreshash",
      "pkt.origin": "afzender",
      "pkt.destination": "waarnemer",
      "pkt.path_note": "Een hop is maar 1, 2 of 3 bytes van een publieke sleutel — zie de hashgrootte hieronder — dus meerdere nodes kunnen dezelfde hop opleveren. Onzekere stukken staan gestippeld op de kaart.",
      "pkt.path_note_direct": "Direct gerouteerd: het pad is de nog af te leggen route, niet de reeds afgelegde.",
      "pkt.loaderror": "Kon de details van dit pakket niet laden.",

      // --- nodedetail (klik op een bolletje op de live kaart) ---
      // Veel van deze teksten dragen een voorbehoud mee in plaats van het weg
      // te laten: wat afgeleid is staat met stippellijn onderstreept en heeft
      // de reden in zijn title. Die redenen staan hieronder voluit, want ze
      // zijn het antwoord op de vraag die het cijfer oproept.
      "node.title": "Nodedetail",
      "node.name": "Naam",
      "node.name_unknown": "naamloos — nooit een advert met naam opgevangen",
      "node.key": "Sleutel",
      "node.key_why": "De eerste bytes van de publieke sleutel, niet de hele sleutel. Zo lang als de bron hem doorgaf: een node noemt er zes, Home Assistant vijf.",
      "node.type": "Nodetype",
      "node.country": "Land",
      "node.position": "Positie",
      "node.position_unknown": "positie onbekend — deze node heeft nooit coördinaten geadverteerd, en staat daarom ook niet als bolletje op de kaart",
      "node.updated": "Laatste advert",
      "node.unknown": "onbekend",

      "node.rep": "Gevolgde repeater",
      "node.rep_status": "Status",
      "node.rep_online": "online",
      "node.rep_offline": "offline",
      "node.rep_battery": "Batterij",
      "node.rep_uptime": "Uptime",
      "node.rep_uptime_v": "{n} dagen",
      "node.rep_link": "Volledige statistieken van deze repeater →",

      "node.traffic": "Verkeer",
      "node.window": "Alle cijfers hieronder gaan enkel over de bewaarde pakketten: {days} dagen bewaartermijn, oudste bewaarde pakket {oldest}. Ouder verkeer is gewist en telt hier niet mee.",
      "node.window_empty": "Er zijn nog geen pakketten bewaard, dus over verkeer valt hier niets te zeggen.",
      "node.sent": "Eigen pakketten",
      "node.sent_n": "{n}",
      "node.sent_none": "geen",
      "node.sent_why": "Alleen een advert noemt zijn afzender voluit. Al het andere verkeer van deze node draagt enkel een adreshash van 1 byte, die honderden nodes kunnen delen — dat meetellen zou een groter getal geven dat deels van iemand anders is. Dit telt dus wat bewijsbaar van deze node komt, niet alles wat hij verstuurde.",
      "node.span": "Gehoord van",
      "node.span_v": "{first} tot {last}",
      "node.hops": "Minste hops",
      "node.hops_v": "{n}",
      "node.hops_why": "Het kleinste aantal hops dat een advert van deze node had afgelegd toen een waarnemer hem oppikte — zo dicht zat hij bij het dichtstbijzijnde oor. Alleen FLOOD-pakketten tellen mee: bij DIRECT is de padlengte de nog te gane route, niet de afgelegde.",
      "node.types": "Pakkettypes",
      "node.scopes": "Bereik",
      "node.ashop": "Als hop in pad",
      "node.ashop_n": "{n}",
      "node.ashop_none": "nooit",
      "node.ashop_why": "Een hop in een pad is 1, 2 of 3 bytes van een sleutel — de verzendende node kiest hoeveel. {n} andere bekende nodes delen de eerste byte met deze, dus dit is een bovengrens: een deel van die pakketten kan langs een van hen gelopen zijn.",
      "node.ashop_why_alone": "Een hop in een pad is 1, 2 of 3 bytes van een sleutel. Geen enkele andere bekende node deelt de eerste byte met deze, dus deze telling is hier uitzonderlijk wél eenduidig.",
      "node.heard": "Zelf gehoord",
      "node.heard_v": "{n} pakketten van {s} afzenders",

      "node.observers": "Wie hoort deze node",
      "node.obs_snr": "SNR gem. {avg} dB · best {best} dB",
      "node.obs_rssi": "RSSI gem. {v} dBm",
      "node.obs_hops": "min. {n} hops",
      "node.obs_note": "Gemeten aan de ontvangende kant: SNR en RSSI zijn wat die waarnemer opving, niet wat deze node uitzond.",
      "node.obs_none": "Niemand heeft in dit venster een advert van deze node opgevangen. Dat betekent niet dat hij stil was — alleen dat niets van hem bewijsbaar hier terechtkwam.",

      "node.links": "Buurrelaties",
      "node.link_hears": "{r} hoort deze node",
      "node.link_hears_back": "deze node hoort {n}",
      "node.link_note": "Een burenrelatie is een meting van de repeater zelf, sleutel en SNR inbegrepen — geen afleiding van deze site.",
      "node.link_none": "Geen buurrelaties bekend. Alleen de repeaters die deze site volgt publiceren hun burenlijst, dus voor de meeste nodes blijft dit leeg.",
      "node.link_capped": "Enkel de sterkste links; de volledige lijst staat op de pagina van de repeater.",
      "node.marker_aria": "Node {name} — open details",
      "node.loaderror": "Kon de gegevens van deze node niet laden.",

      // --- repeaterpagina ---
      "status.online": "ONLINE",
      "status.offline": "OFFLINE",
      "rep.refresh": "↻ Status opvragen",
      "rep.refresh_title": "Vraag nu een verse status: rechtstreeks aan de node, of via een poller over LoRa",
      "rep.refresh_off": "✕ Opvragen kan nu niet",
      "rep.refresh_off_title": "Er is op dit ogenblik geen weg naar deze repeater — zie de beheerpagina van deze node",
      "rep.settings": "⚙ Beheren",
      "rep.settings_title": "Beheerpagina van deze node",
      // Vier meldingen in plaats van één belofte: wat er gebeurd is hangt af van
      // wie er te bereiken viel, en de pagina hoort dat te zeggen.
      "rep.refresh_mqtt": "⏳ De node is gevraagd nu een statusbericht te sturen; binnen ±1 minuut verschijnt een vers datapunt.",
      "rep.refresh_queued": "⏳ Statusverzoek in de wachtrij gezet — de poller vraagt de repeater over LoRa uit; binnen ±1 minuut verschijnt een vers datapunt.",
      "rep.refresh_both": "⏳ Statusverzoek verstuurd naar de node én in de wachtrij gezet; binnen ±1 minuut verschijnt een vers datapunt.",
      "rep.refresh_none": "⚠ Er is niets verstuurd — geen weg naar deze repeater op dit ogenblik. De beheerpagina van deze node zegt waarom.",
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
      // Een tweede reden om niet op deze kaart te staan, en een heel andere:
      // geen gebrek aan gegevens maar een keuze. De twee op één hoop gooien zou
      // de regel hierboven tot een leugen maken.
      "map.hidden": "{n} buur/buren tonen hun positie niet",
      "map.hidden_intro": "Wel bekend, maar hun positie wordt niet publiek getoond: ",

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
      "footer.text": "MeshCore statistics · straight from the nodes · ",
      "footer.admin": "admin",

      "login.invalid": "Invalid credentials",
      "login.invalid_throttled": "Invalid credentials — too many attempts, wait {n} s.",
      "login.throttled": "Too many failed attempts. Try again in {n} s.",
      "login.expired": "Session expired — please try again.",

      "home.title": "Repeaters",
      "home.hint": "Live MeshCore repeater statistics · click a repeater for details",
      "home.empty": "No repeaters yet. They appear here as soon as a node sends data.",
      "home.lastseen": "last seen",
      "card.battery": "Battery",
      "card.uptime": "Uptime",
      "card.neighbors": "Neighbours",
      "card.temperature": "Temperature",

      "live.title": "Live packets",
      "live.hint": "Every packet a node overhears flashes at the sender's location · click a packet or a node dot for all its details",
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
      "live.outside": "{n} nodes outside the view · show all",
      "live.outside_one": "1 node outside the view · show all",
      "live.outside_title": "The map opens on the nodes that carry traffic, not on every contact ever advertised — otherwise one distant contact sets the scale. Nothing is left out: click to bring every node into view, or zoom out yourself.",
      "live.hidden": "{n} nodes do not show their position",
      "live.hidden_one": "1 node does not show its position",
      "live.hidden_title": "This site does know these nodes, but their operator chose not to show their position publicly. They are on no map here, however far you zoom out, and links to them do not count towards the usage layer. Their figures and their name may well be there.",
      "live.motion": "Animate packets",
      "live.motion_title": "Show every packet as a dot travelling from the sender via each hop to the observing node. A stretch of path we do not know is dashed.",
      "live.motion_reduced": "Off: your system asks for reduced motion. Packets flash instead.",
      "live.heat": "Show path usage",
      "live.heat_title": "Draws every link packets travelled over the whole retained period; the busier a link compared to the rest, the warmer and thicker the line. Only stretches between two unambiguously placed nodes count.",
      "live.heat_tip": "{a} ↔ {b} · travelled {n}× in {days} d",
      "live.heat_capped": "Note: there were more packets than the map can take in at once; the oldest are left out for now.",
      "live.heat_hidden": "Note: {n} node(s) do not show their position, so links to and from them are not drawn here.",
      "live.heat_min_aria": "Minimum number of traversals for a link to be shown",
      "live.heat_min_title": "Hides links that packets travelled over less often than the threshold. All the way left the threshold is 1 and you see everything.",
      "live.heat_min_value": "threshold {n} traversals",
      "live.heat_shown": "{shown} of {total} links shown · {hidden} below the threshold",
      "live.heat_shown_all": "all {total} links shown",
      "live.heat_shown_none": "0 of {total} links shown · nothing meets this threshold",
      "live.heat_quiet": "quiet",
      "live.heat_busy": "busy",
      "live.heat_legend_title": "The colour and thickness of a line say how busy that link is compared to the other links shown.",

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
      "col.src": "Src hash",
      "col.dest": "Destination",
      "col.route": "Route",
      "col.region": "Region",
      "col.path": "Path",
      "col.hash": "Hash",

      // --- packet archive ---
      "arch.title": "Packet archive",
      "arch.hint": "Search every retained packet · packets are kept for {days} days",
      "arch.hint_short": "Search every retained packet · {days} days of packets are held out of the {set} configured: the storage limit cuts in first",
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
      "arch.help_syntax": "Space-separated clauses must all hold. field:value matches exactly, 2ae7* by prefix, -type:ACK excludes, type:(ADVERT OR ACK) is either, quotes for a value with spaces. A bare word searches names, keys, type, scope and country.",
      "arch.help_numbers": "Numeric fields — hops, snr, rssi, len, region — also take greater and less than: hops:>3 is packets with more than three hops, hops:<2 fewer than two, hops:2..5 everything between. >= and <= work too.",
      "arch.help_sort": "Click a column heading in the results to sort by it; another click turns the order around.",
      "arch.table_aria": "Search results",
      "arch.sort_label": "Sort by",
      "arch.sort_by": "Sort by this column",
      "arch.sort_flip": "Turn the order around",
      "arch.sort_desc": "descending",
      "arch.sort_asc": "ascending",
      "arch.cols": "Columns",
      "arch.cols_reset": "Default columns",
      "arch.col_hide": "Hide the {col} column",
      "arch.col_unknown": "Unknown column from the link skipped: {cols}.",
      "arch.sort_hidden": "Sorted by {col}, a column that is not shown.",
      "arch.sort_hidden_add": "show the column",
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
      "arch.f_dest": "Destination (hash)",
      "arch.f_src": "Sender (hash)",
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
      "pkt.hop_likely": "most likely of {n}",
      "pkt.cand_ranked": "most likely of {n} candidates — a ranking of what we measured, not a certainty",
      "pkt.cand_others": "other candidates: {list}",
      "pkt.cand_also": "also: {list}",
      "pkt.cand_why_direct": "leads because this observer has heard it directly",
      "pkt.cand_why_hop1": "leads because this observer has already heard it at 1 hop",
      "pkt.cand_why_hops": "leads because this observer has already heard it at {n} hops",
      "pkt.cand_why_near": "leads because it is closest to the observer ({km} km)",
      "pkt.cand_why_recent": "leads because it was seen most recently",
      "pkt.cand_dropped_one": "1 candidate ruled out ({list}): too far away for this packet's hop count",
      "pkt.cand_dropped": "{n} candidates ruled out ({list}): too far away for this packet's hop count",
      "pkt.cand_none_left": "no candidate left",
      "pkt.cand_none": "no known contact matches this address hash",
      "pkt.origin": "sender",
      "pkt.destination": "observer",
      "pkt.path_note": "A hop is only 1, 2 or 3 bytes of a public key — see the hash size below — so several nodes can answer to the same hop. Uncertain stretches are dashed on the map.",
      "pkt.path_note_direct": "Direct routing: the path is the route still to travel, not the one already travelled.",
      "pkt.loaderror": "Could not load the details of this packet.",

      "node.title": "Node detail",
      "node.name": "Name",
      "node.name_unknown": "nameless — no advert carrying a name was ever picked up",
      "node.key": "Key",
      "node.key_why": "The first bytes of the public key, not the whole key. As long as the source passed it on: a node names six, Home Assistant five.",
      "node.type": "Node type",
      "node.country": "Country",
      "node.position": "Position",
      "node.position_unknown": "position unknown — this node has never advertised coordinates, which is also why it has no dot on the map",
      "node.updated": "Last advert",
      "node.unknown": "unknown",

      "node.rep": "Tracked repeater",
      "node.rep_status": "Status",
      "node.rep_online": "online",
      "node.rep_offline": "offline",
      "node.rep_battery": "Battery",
      "node.rep_uptime": "Uptime",
      "node.rep_uptime_v": "{n} days",
      "node.rep_link": "Full statistics for this repeater →",

      "node.traffic": "Traffic",
      "node.window": "Every figure below covers the retained packets only: {days} days of retention, oldest packet held {oldest}. Anything older has been deleted and is not counted here.",
      "node.window_empty": "No packets are retained yet, so there is nothing to say about traffic here.",
      "node.sent": "Own packets",
      "node.sent_n": "{n}",
      "node.sent_none": "none",
      "node.sent_why": "Only an advert names its sender in full. All this node's other traffic carries just a 1-byte address hash that hundreds of nodes can share — counting those in would give a larger number that is partly somebody else's. So this counts what is provably from this node, not everything it sent.",
      "node.span": "Heard from",
      "node.span_v": "{first} to {last}",
      "node.hops": "Fewest hops",
      "node.hops_v": "{n}",
      "node.hops_why": "The fewest hops any advert of this node had travelled when an observer picked it up — that is how close it sat to the nearest ear. FLOOD packets only: on a DIRECT the path length is the route still to go, not the one travelled.",
      "node.types": "Packet types",
      "node.scopes": "Scope",
      "node.ashop": "As a hop in a path",
      "node.ashop_n": "{n}",
      "node.ashop_none": "never",
      "node.ashop_why": "A hop in a path is 1, 2 or 3 bytes of a key — the sending node picks how many. {n} other known nodes share their first byte with this one, so this is an upper bound: some of those packets may have gone through one of them.",
      "node.ashop_why_alone": "A hop in a path is 1, 2 or 3 bytes of a key. No other known node shares its first byte with this one, so here the count is unambiguous for once.",
      "node.heard": "Heard by itself",
      "node.heard_v": "{n} packets from {s} senders",

      "node.observers": "Who hears this node",
      "node.obs_snr": "SNR avg {avg} dB · best {best} dB",
      "node.obs_rssi": "RSSI avg {v} dBm",
      "node.obs_hops": "min. {n} hops",
      "node.obs_note": "Measured at the receiving end: SNR and RSSI are what that observer picked up, not what this node transmitted.",
      "node.obs_none": "Nobody picked up an advert from this node inside this window. That does not mean it was silent — only that nothing of it provably reached here.",

      "node.links": "Neighbour relations",
      "node.link_hears": "{r} hears this node",
      "node.link_hears_back": "this node hears {n}",
      "node.link_note": "A neighbour relation is a measurement by the repeater itself, key and SNR included — not an inference by this site.",
      "node.link_none": "No neighbour relations known. Only the repeaters this site tracks publish a neighbour list, so for most nodes this stays empty.",
      "node.link_capped": "The strongest links only; the full list is on the repeater's own page.",
      "node.marker_aria": "Node {name} — open details",
      "node.loaderror": "Could not load the data for this node.",

      "status.online": "ONLINE",
      "status.offline": "OFFLINE",
      "rep.refresh": "↻ Request status",
      "rep.refresh_title": "Ask for a fresh status now: straight to the node, or through a poller over LoRa",
      "rep.refresh_off": "✕ Cannot request now",
      "rep.refresh_off_title": "No route to this repeater at the moment — the node's admin page says why",
      "rep.settings": "⚙ Manage",
      "rep.settings_title": "Admin page for this node",
      "rep.refresh_mqtt": "⏳ The node has been asked to publish a status message now; a fresh data point appears within ±1 minute.",
      "rep.refresh_queued": "⏳ Status request queued — the poller queries the repeater over LoRa; a fresh data point appears within ±1 minute.",
      "rep.refresh_both": "⏳ Status request sent to the node and queued for the poller; a fresh data point appears within ±1 minute.",
      "rep.refresh_none": "⚠ Nothing was sent — no route to this repeater at the moment. The node's admin page says why.",
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
      "map.hidden": "{n} neighbour(s) do not show their position",
      "map.hidden_intro": "Known, but their position is not shown publicly: ",

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

  // A page may pin its language with <html data-lang-lock="nl">, and that beats
  // both the stored choice and the browser. The admin pages use it: they are
  // deliberately Dutch-only (the reasoning sits in admin/_layout.html), and
  // without the lock a visitor who once picked English on the public site would
  // get English relative times and an English <html lang> on top of Dutch
  // prose -- half a translation, which reads worse than none.
  //
  // Rejected alternative: leave the stored choice alone and merely hide the
  // toggle on those pages. That hides the switch but not its effect, so the
  // half-translated state stays reachable for anyone who ever flipped it.
  var locked = document.documentElement.getAttribute("data-lang-lock");
  // A stored choice wins over the browser; otherwise land on Dutch for anything
  // that is not clearly an English-speaking visitor.
  var lang = (DICT[locked] ? locked : null) || stored() ||
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
