/* MeshStats front-end: relative times, gauges, charts, maps and the history
 * modal. Loaded after i18n.js, so every string this file builds goes through
 * MCSI18N.t -- text baked in here would survive a language switch and leave the
 * page half translated. */
(function () {
  "use strict";

  var PALETTE = ["#2bb673", "#e8913a", "#3aa7d0", "#e06c9f"];
  var t = (window.MCSI18N && window.MCSI18N.t) || function (k) { return k; };
  // Theme colours come from the CSS variables (light/dark)
  function cssVar(name, fallback) {
    var v = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
    return v || fallback;
  }
  // Sender of a packet that is not an advert, worked out from the 1-byte source
  // hash the server resolved against the contacts table (the `src` object on a
  // packet). One byte is honest ambiguity: one match reads as a name, several
  // read as "N mogelijk" with every candidate in the mouseover -- the same rule
  // the path hops follow. Shared between the live feed and the archive, which
  // render the same packets in two places.
  function srcLabel(src, t) {
    if (!src || !src.matches || !src.matches.length) return null;
    var names = src.matches.map(function (m) {
      return m.name || (m.prefix || "").toUpperCase();
    });
    if (src.state === "known") {
      return { text: names[0],
               title: t("pkt.src_from_hash", { h: (src.hash || "").toUpperCase() }) };
    }
    return { text: t("pkt.src_multi", { n: src.matches.length }),
             title: t("pkt.src_candidates", { list: names.join(", "),
                                              h: (src.hash || "").toUpperCase() }) };
  }

  // --- packet detail, shared by the live page and the archive -------------------
  // Both pages render the same fragment (templates/_packet_detail.html): the live
  // page inside a docked panel beside the map, the archive inside a modal. The
  // filling is therefore written once, here at the top level, rather than twice.
  // Two panels drifting apart would be worse than a little indirection, because
  // what they render is the honesty rule about derived senders and ambiguous
  // hops -- and a rule stated differently in two places is a rule that will
  // eventually be stated wrongly in one of them.
  //
  // Only one of the two containers ever exists on a page, so the element ids
  // below stay unique and lookup by id is enough; scoping every query to a root
  // element was considered and dropped as ceremony without a payer.
  function txt(id, value) {
    var el = document.getElementById(id);
    if (el) el.textContent = value;
  }

  function nodeLabel(prefix, name) {
    var p = (prefix || "").toUpperCase();
    if (!p && !name) return "";
    return name ? name + (p ? " (" + p + ")" : "") : p;
  }

  // Country names are the one label deliberately left untranslated: a flag and
  // an ISO code read the same in every language, where a list of country names
  // would be a second dictionary to keep in step with the first.
  function flagOf(code) {
    if (!/^[A-Za-z]{2}$/.test(code || "")) return "";
    return String.fromCodePoint.apply(String, code.toUpperCase().split("")
      .map(function (c) { return 0x1F1E6 + c.charCodeAt(0) - 65; }));
  }
  function countryLabel(code) {
    return code ? flagOf(code) + " " + code.toUpperCase() : t("pkt.country_unknown");
  }

  // Whether the sender kept the packet inside a region. Two spellings: the
  // column has room for a word, the panel for the sentence that word stands
  // for -- and for scoped traffic that sentence has to say whether a region
  // was actually named, because almost always it was not.
  function scopeDetail(d) {
    if (!d.scope) return "—";
    if (d.scope === "scoped") {
      return t("scope.scoped") + " — " + (d.scope_region
        ? t("scope.region", { n: d.scope_region })
        : t("scope.region_unnamed"));
    }
    return t("scope." + d.scope) + " — " + t("scope." + d.scope + "_note");
  }

  function hopLabel(hop) {
    if (hop.state === "known") {
      var m = hop.matches[0];
      // Saying which node it was but not being able to place it is exactly why
      // the map shows a dashed gap here; spell that out rather than leaving
      // the reader to wonder why a named hop has no dot.
      return (m.name || m.prefix.toUpperCase()) +
        (m.lat == null || m.lon == null ? " — " + t("pkt.hop_nolocation") : "");
    }
    if (hop.state === "ambiguous") {
      return t("pkt.hop_ambiguous", { n: hop.matches.length }) + ": " +
        hop.matches.map(function (m) { return m.name || m.prefix.toUpperCase(); }).join(", ");
    }
    return t("pkt.hop_unknown");
  }

  // The panel spells out what the column only hints at: every candidate by
  // name, and which hash they were derived from.
  function srcDetail(res) {
    if (!res || !res.matches || !res.matches.length) return null;
    var names = res.matches.map(function (m) {
      return m.name || (m.prefix || "").toUpperCase();
    });
    if (res.state === "known") {
      return names[0] + " · " + t("pkt.src_from_hash", { h: res.hash.toUpperCase() });
    }
    return t("pkt.src_multi", { n: res.matches.length }) + ": " + names.join(", ") +
      " · " + t("pkt.src_from_hash", { h: res.hash.toUpperCase() });
  }

  // One value written the way search.py's parser reads it back. Quotes are the
  // parser's only grouping device and it has no escape for a quote inside one
  // (see _read_value), so an inner quote is dropped rather than smuggled in as
  // a clause boundary the visitor never typed.
  function queryValue(value) {
    var v = String(value).replace(/"/g, "");
    return /[\s()]/.test(v) ? '"' + v + '"' : v;
  }

  function queryClause(field, value, negate) {
    return (negate ? "-" : "") + field + ":" + queryValue(value);
  }

  // A Kibana-style pair beside a value: + narrows the archive query to this
  // field:value, - excludes it. Rendered only when the caller supplies a
  // handler, which is why the live page (no query bar, no query language) gets
  // none, and only for fields search.FIELDS actually knows -- a button that
  // produced "Onbekend veld" would be a trap dressed as a feature.
  // The field names the query language actually knows, handed over by the
  // archive page from search.describe_fields(). Kept as a module-level list
  // rather than threaded through every call: the buttons are rendered from four
  // different places and an extra argument at each of them would only make it
  // easier to forget the check somewhere.
  var SEARCH_FIELDS = null;

  function filterBtns(el, field, value, onFilter) {
    if (!el || !onFilter || value === null || value === undefined || value === "") return;
    if (SEARCH_FIELDS && SEARCH_FIELDS.indexOf(field) < 0) return;
    var wrap = document.createElement("span");
    wrap.className = "fbtns";
    [[false, "+", "arch.filter_add"], [true, "−", "arch.filter_not"]]
      .forEach(function (spec) {
        var b = document.createElement("button");
        b.type = "button";
        b.className = "fbtn";
        b.textContent = spec[1];
        b.title = t(spec[2], { q: queryClause(field, value, spec[0]) });
        b.setAttribute("aria-label", b.title);
        b.addEventListener("click", function (e) {
          // Rows are clickable too (they open the detail); a filter click is
          // about the value, not about the packet it happens to sit in.
          e.preventDefault();
          e.stopPropagation();
          onFilter(field, value, spec[0]);
        });
        wrap.appendChild(b);
      });
    el.appendChild(wrap);
  }

  /* Fill the shared fragment from one /api/v1/packets/{id} response.
   *
   * opts.showCountry -- whether this page can say anything about countries at
   *   all. The live page hides the row when the deployment has no borders to
   *   classify positions against; the archive always shows it, because it has a
   *   Land column and a country: field either way.
   * opts.onFilter -- see filterBtns; omitted on the live page.
   */
  function fillPacketDetail(d, opts) {
    opts = opts || {};
    var onFilter = opts.onFilter || null;
    txt("pkt-time", new Date(d.ts).toLocaleString() + " · " + relTime(d.ts));
    txt("pkt-sender", nodeLabel(d.sender, d.sender_name) || srcDetail(d.src) ||
        t("pkt.sender_unknown"));
    // Only a sender stated by an advert has a key to filter on. A sender merely
    // derived from the 1-byte source hash gets no buttons: sender: searches the
    // stored key column, and offering it here would silently filter on
    // something other than the guess printed next to it.
    filterBtns(document.getElementById("pkt-sender"), "sender", d.sender, onFilter);
    txt("pkt-observer", nodeLabel(d.observer, d.observer_name) || "—");
    filterBtns(document.getElementById("pkt-observer"), "observer", d.observer, onFilter);
    // The destination row only exists for packet types that name one; an empty
    // row on every ACK and advert would be noise.
    var destRow = document.getElementById("pkt-dest-row");
    var destText = srcDetail(d.dest) ||
      (d.dest && d.dest.hash ? "0x" + d.dest.hash.toUpperCase() + " · " +
        t("pkt.hop_unknown") : null);
    destRow.hidden = !destText;
    if (destText) txt("pkt-dest", destText);
    // Whose country to show follows whose position the map used for this
    // packet: the sender's when we know it, the observer's otherwise.
    var countryRow = document.getElementById("pkt-country-row");
    var placed = d.sender_lat != null && d.sender_lon != null;
    var cc = placed ? d.sender_country : d.observer_country;
    countryRow.hidden = !opts.showCountry;
    txt("pkt-country-val", countryLabel(cc) +
        " · " + t(placed ? "pkt.country_of_sender" : "pkt.country_of_observer"));
    filterBtns(document.getElementById("pkt-country-val"), "country", cc, onFilter);
    txt("pkt-type", d.type || "—");
    filterBtns(document.getElementById("pkt-type"), "type", d.type, onFilter);
    txt("pkt-route", d.route || "—");
    filterBtns(document.getElementById("pkt-route"), "route", d.route, onFilter);
    txt("pkt-scope", scopeDetail(d));
    // scope: only, never region:. The region is part of the same sentence here
    // ("gescoped — regio 7"), and a second pair of buttons filtering on half of
    // one sentence is the kind of thing a reader clicks once and never trusts
    // again. The region facet in the sidebar covers that need.
    filterBtns(document.getElementById("pkt-scope"), "scope", d.scope, onFilter);
    // Only a scoped packet carries codes, and only the second of them could
    // ever name a region -- so the row exists to show what the frame actually
    // holds, not to be filled in with a guess when it holds nothing.
    var codesRow = document.getElementById("pkt-scope-codes-row");
    var codes = d.scope_codes;
    codesRow.hidden = !codes || codes.length < 2;
    if (!codesRow.hidden) txt("pkt-scope-codes", codes[0] + " / " + codes[1]);
    txt("pkt-snr", d.snr != null ? d.snr.toFixed(2) + " dB" : "—");
    filterBtns(document.getElementById("pkt-snr"), "snr", d.snr, onFilter);
    txt("pkt-rssi", d.rssi != null ? d.rssi + " dBm" : "—");
    filterBtns(document.getElementById("pkt-rssi"), "rssi", d.rssi, onFilter);
    txt("pkt-len", d.len != null ? d.len + " B" : "—");
    filterBtns(document.getElementById("pkt-len"), "len", d.len, onFilter);
    txt("pkt-pathlen", d.path_len != null ? String(d.path_len) : "—");
    filterBtns(document.getElementById("pkt-pathlen"), "hops", d.path_len, onFilter);
    // Hex in byte pairs so it stays readable, and it wraps rather than
    // widening the page on a phone (see .pktraw).
    txt("pkt-raw", d.raw ? d.raw.toUpperCase().replace(/../g, "$& ").trim() : t("pkt.noraw"));
    var copyBtn = document.getElementById("pkt-raw-copy");
    if (copyBtn) {
      copyBtn.hidden = !d.raw;
      copyBtn.dataset.raw = d.raw || "";
      copyBtn.textContent = t("pkt.copy");
    }

    var list = document.getElementById("pkt-path");
    list.textContent = "";
    (d.path || []).forEach(function (h) {
      var li = document.createElement("li");
      li.className = "hop hop-" + h.state;
      var hex = document.createElement("code");
      hex.textContent = h.hash.toUpperCase();
      var label = document.createElement("span");
      label.textContent = hopLabel(h);
      li.appendChild(hex);
      li.appendChild(label);
      // path: matches on containment in the stored hop list, so the hash is the
      // right value here even when we cannot say which node it was -- "every
      // packet that went through this hop" is a question worth asking about
      // exactly the hops we could not name.
      filterBtns(li, "path", h.hash, onFilter);
      list.appendChild(li);
    });

    var notes = [];
    if (!d.path_stored) notes.push(t("pkt.path_unstored"));
    else if (!(d.path || []).length) notes.push(t("pkt.nopath"));
    if ((d.path || []).length) {
      notes.push(t("pkt.path_note"));
      if (/DIRECT/.test(d.route || "")) notes.push(t("pkt.path_note_direct"));
    }
    txt("pkt-path-note", notes.join(" "));

    var adv = document.getElementById("pkt-advert");
    adv.hidden = !d.advert;
    if (d.advert) {
      txt("pkt-adv-name", d.advert.name || "—");
      txt("pkt-adv-coords", d.advert.lat != null && d.advert.lon != null
        ? d.advert.lat.toFixed(6) + ", " + d.advert.lon.toFixed(6) : "—");
      txt("pkt-adv-type", d.advert.node_type || "—");
      txt("pkt-adv-ts", d.advert.ts
        ? new Date(d.advert.ts * 1000).toLocaleString() : "—");
    }
  }

  // Emptying is its own function rather than "fill with a blank packet": the
  // panel is shown before the fetch resolves, and last packet's numbers left
  // standing under a new title would be read as this packet's.
  function blankPacketDetail() {
    ["pkt-time", "pkt-sender", "pkt-observer", "pkt-dest", "pkt-country-val",
     "pkt-type", "pkt-route", "pkt-scope", "pkt-scope-codes", "pkt-snr",
     "pkt-rssi", "pkt-len", "pkt-pathlen", "pkt-raw", "pkt-path-note"]
      .forEach(function (id) { txt(id, ""); });
    document.getElementById("pkt-path").textContent = "";
    document.getElementById("pkt-advert").hidden = true;
    document.getElementById("pkt-scope-codes-row").hidden = true;
    document.getElementById("pkt-dest-row").hidden = true;
    var copyBtn = document.getElementById("pkt-raw-copy");
    if (copyBtn) copyBtn.hidden = true;
  }

  // The raw bytes are shown spaced for reading but copied unspaced, because
  // whatever they get pasted into (a decoder, a script) wants them the way the
  // API stores them.
  (function wireRawCopy() {
    var btn = document.getElementById("pkt-raw-copy");
    if (!btn) return;
    btn.addEventListener("click", function () {
      var hex = btn.dataset.raw || "";
      if (!hex || !navigator.clipboard) return;
      navigator.clipboard.writeText(hex.toUpperCase()).then(function () {
        btn.textContent = t("pkt.copied");
        setTimeout(function () { btn.textContent = t("pkt.copy"); }, 1500);
      }).catch(function () { /* a browser that refuses leaves the bytes on screen */ });
    });
  })();

  var THEME = document.documentElement.getAttribute("data-theme") === "light" ? "light" : "dark";
  var TEXT = cssVar("--text", "#d7e2ea");
  var TEXT_MUTED = cssVar("--muted", "#7d8fa0");
  var GRID = cssVar("--chart-grid", "rgba(125, 143, 160, .12)");
  var TILE_URL = "https://{s}.basemaps.cartocdn.com/" +
    (THEME === "light" ? "light_all" : "dark_all") + "/{z}/{x}/{y}{r}.png";

  if (typeof Chart !== "undefined") {
    Chart.defaults.color = TEXT_MUTED;
    Chart.defaults.borderColor = GRID;
    Chart.defaults.font.family = "'JetBrains Mono', Consolas, monospace";
    Chart.defaults.font.size = 11;
  }

  // Theme switch: reload so charts and map tiles pick up the new palette too
  var themeBtn = document.getElementById("theme-toggle");
  if (themeBtn) {
    themeBtn.textContent = THEME === "light" ? "☾" : "☀";
    themeBtn.addEventListener("click", function () {
      localStorage.setItem("mcs-theme", THEME === "light" ? "dark" : "light");
      location.reload();
    });
  }

  // --- relative timestamps ---------------------------------------------------
  function relTime(iso) {
    var d = new Date(iso);
    if (isNaN(d)) return iso;
    var s = Math.round((Date.now() - d.getTime()) / 1000);
    if (s < 0) s = 0;
    if (s < 60) return t("time.now");
    if (s < 3600) return t("time.min", { n: Math.round(s / 60) });
    if (s < 86400) return t("time.hour", { n: Math.round(s / 3600) });
    return t("time.day", { n: Math.round(s / 86400) });
  }
  function updateTimes() {
    document.querySelectorAll("time.reltime").forEach(function (el) {
      var iso = el.getAttribute("datetime");
      el.textContent = relTime(iso);
      el.title = new Date(iso).toLocaleString();
    });
  }
  updateTimes();
  setInterval(updateTimes, 30000);

  // --- gauges (half circle with a needle) ------------------------------------
  document.querySelectorAll("[data-gauge]").forEach(function (tile) {
    var canvas = tile.querySelector("canvas");
    var ctx = canvas.getContext("2d");
    var min = parseFloat(tile.dataset.min), max = parseFloat(tile.dataset.max);
    var value = Math.min(max, Math.max(min, parseFloat(tile.dataset.value)));
    var segments = JSON.parse(tile.dataset.segments);
    var w = canvas.width, h = canvas.height, cx = w / 2, cy = h - 6, r = Math.min(w / 2 - 10, h - 14);

    function angle(v) { return Math.PI + ((v - min) / (max - min)) * Math.PI; }
    for (var i = 0; i < segments.length; i++) {
      var from = segments[i][0];
      var to = i + 1 < segments.length ? segments[i + 1][0] : max;
      ctx.beginPath();
      ctx.arc(cx, cy, r, angle(from) + 0.02, angle(to) - 0.02);
      ctx.lineWidth = 10;
      ctx.lineCap = "round";
      ctx.strokeStyle = segments[i][1] + "55";
      ctx.stroke();
    }
    // coloured arc up to the current value
    var segColor = segments[0][1];
    for (var j = 0; j < segments.length; j++) {
      if (value >= segments[j][0]) segColor = segments[j][1];
    }
    ctx.beginPath();
    ctx.arc(cx, cy, r, Math.PI, angle(value));
    ctx.lineWidth = 10;
    ctx.lineCap = "round";
    ctx.strokeStyle = segColor;
    ctx.shadowColor = segColor;
    ctx.shadowBlur = 8;
    ctx.stroke();
    ctx.shadowBlur = 0;
    var a = angle(value);
    ctx.beginPath();
    ctx.moveTo(cx, cy);
    ctx.lineTo(cx + (r - 16) * Math.cos(a), cy + (r - 16) * Math.sin(a));
    ctx.lineWidth = 2.5;
    ctx.strokeStyle = TEXT;
    ctx.stroke();
    ctx.beginPath();
    ctx.arc(cx, cy, 3.5, 0, 2 * Math.PI);
    ctx.fillStyle = TEXT;
    ctx.fill();
  });

  // --- thermometers (horizontal tube with a bulb) -----------------------------
  document.querySelectorAll("[data-thermo]").forEach(function (tile) {
    var canvas = tile.querySelector("canvas");
    var ctx = canvas.getContext("2d");
    var min = parseFloat(tile.dataset.min), max = parseFloat(tile.dataset.max);
    var raw = parseFloat(tile.dataset.value);
    var value = Math.min(max, Math.max(min, raw));
    var segments = JSON.parse(tile.dataset.segments); // [[from, colour], ...]
    var w = canvas.width, h = canvas.height;
    var bulbR = 12, y = 40, tubeH = 12;
    var x0 = 20 + bulbR, x1 = w - 16;

    function xAt(v) { return x0 + ((v - min) / (max - min)) * (x1 - x0); }
    function segColor(v) {
      var c = segments[0][1];
      for (var i = 0; i < segments.length; i++) if (v >= segments[i][0]) c = segments[i][1];
      return c;
    }
    function tube(toX, r) {
      ctx.beginPath();
      ctx.moveTo(x0, y - r);
      ctx.lineTo(toX - r, y - r);
      ctx.arc(toX - r, y, r, -Math.PI / 2, Math.PI / 2);
      ctx.lineTo(x0, y + r);
      ctx.closePath();
    }

    // dark tube and bulb as the background
    ctx.fillStyle = cssVar("--thermo-track", "#1e2b3a");
    tube(x1, tubeH / 2 + 2); ctx.fill();
    ctx.beginPath(); ctx.arc(20, y, bulbR + 2, 0, 2 * Math.PI); ctx.fill();

    // coloured fill up to the current value
    var color = segColor(value);
    ctx.fillStyle = color;
    ctx.shadowColor = color; ctx.shadowBlur = 8;
    ctx.beginPath(); ctx.arc(20, y, bulbR - 2, 0, 2 * Math.PI); ctx.fill();
    var fx = Math.max(xAt(value), x0 + 4);
    tube(fx, tubeH / 2 - 2); ctx.fill();
    ctx.shadowBlur = 0;

    // scale ticks with labels
    ctx.strokeStyle = TEXT_MUTED;
    ctx.fillStyle = TEXT_MUTED;
    ctx.font = "9px 'JetBrains Mono', monospace";
    ctx.textAlign = "center";
    [-20, 0, 20, 40, 60, 80].forEach(function (tick) {
      if (tick < min || tick > max) return;
      var tx = xAt(tick);
      ctx.beginPath();
      ctx.moveTo(tx, y + tubeH / 2 + 4);
      ctx.lineTo(tx, y + tubeH / 2 + 9);
      ctx.stroke();
      ctx.fillText(String(tick), tx, y + tubeH / 2 + 20);
    });
  });

  // --- collapsible sections (per-visitor preference in localStorage) ----------
  document.querySelectorAll("section.collapsible").forEach(function (sec) {
    var key = "mcs-collapse:" + sec.dataset.ckey;
    try {
      if (localStorage.getItem(key) === "1") sec.classList.add("collapsed");
    } catch (e) { /* localStorage can be blocked */ }
    sec.querySelector("h2.sec-toggle").addEventListener("click", function () {
      sec.classList.toggle("collapsed");
      try {
        if (sec.classList.contains("collapsed")) localStorage.setItem(key, "1");
        else localStorage.removeItem(key);
      } catch (e) { /* nothing to do */ }
    });
  });

  // --- admin: draggable layout -------------------------------------------------
  var layoutList = document.getElementById("layout-list");
  if (layoutList) {
    var dragging = null;
    layoutList.querySelectorAll("li").forEach(function (li) {
      li.addEventListener("dragstart", function () { dragging = li; li.classList.add("dragging"); });
      li.addEventListener("dragend", function () { li.classList.remove("dragging"); dragging = null; });
    });
    layoutList.addEventListener("dragover", function (e) {
      e.preventDefault();
      if (!dragging) return;
      var after = null;
      layoutList.querySelectorAll("li:not(.dragging)").forEach(function (li) {
        var rect = li.getBoundingClientRect();
        if (e.clientY < rect.top + rect.height / 2 && after === null) after = li;
      });
      if (after) layoutList.insertBefore(dragging, after);
      else layoutList.appendChild(dragging);
    });
    document.getElementById("layout-form").addEventListener("submit", function () {
      var out = [];
      layoutList.querySelectorAll("li").forEach(function (li) {
        out.push({ key: li.dataset.key, visible: li.querySelector("input[type=checkbox]").checked });
      });
      document.getElementById("layout-json").value = JSON.stringify(out);
    });
  }

  // --- shared chart builder ---------------------------------------------------
  function lineChart(canvas, datasets, unit, showLegend, hours) {
    // Fixed time window: stops the axis collapsing to milliseconds when only a
    // couple of points exist
    var now = Date.now();
    return new Chart(canvas, {
      type: "line",
      data: { datasets: datasets },
      options: {
        responsive: true, maintainAspectRatio: false, animation: false,
        interaction: { mode: "nearest", axis: "x", intersect: false },
        scales: {
          x: {
            type: "time",
            min: hours ? now - hours * 3600 * 1000 : undefined,
            max: hours ? now : undefined,
            time: { tooltipFormat: "dd/MM HH:mm" },
            ticks: { maxTicksLimit: 7 },
            grid: { display: false },
          },
          y: { title: { display: !!unit, text: unit || "" } },
        },
        plugins: {
          legend: { display: !!showLegend, labels: { boxWidth: 14, boxHeight: 2 } },
          tooltip: {
            backgroundColor: cssVar("--tooltip-bg", "#0b0f14"), borderColor: cssVar("--card-edge", "#1e2b3a"), borderWidth: 1,
            titleColor: TEXT, bodyColor: TEXT, padding: 10,
          },
        },
      },
    });
  }

  // Colour zones per metric (same as the gauges), as a gradient over the y-axis
  function zoneFor(metric) {
    if (window.MCS && window.MCS.zones && window.MCS.zones[metric]) return window.MCS.zones[metric];
    if (/^neighbor_[0-9a-f]{6}$/.test(metric || "")) {
      return { min: -25, max: 15, segments: [[-25, "#ff5c5c"], [-10, "#ffb454"], [0, "#35e08c"]] };
    }
    return null;
  }
  function hexToRgba(hex, alpha) {
    return "rgba(" + parseInt(hex.slice(1, 3), 16) + "," + parseInt(hex.slice(3, 5), 16) +
           "," + parseInt(hex.slice(5, 7), 16) + "," + alpha + ")";
  }
  function zoneGradient(zone, alpha) {
    // Scriptable Chart.js colour: a vertical gradient with soft transitions
    // between the zone colours (stops at the middle of each zone).
    return function (context) {
      var chart = context.chart;
      var area = chart.chartArea;
      var yScale = chart.scales.y;
      var last = zone.segments[zone.segments.length - 1][1];
      if (!area || !yScale) return alpha ? hexToRgba(last, alpha) : last;
      var g = chart.ctx.createLinearGradient(0, area.bottom, 0, area.top);
      var lastT = -1;
      for (var i = 0; i < zone.segments.length; i++) {
        var from = zone.segments[i][0];
        var to = i + 1 < zone.segments.length ? zone.segments[i + 1][0] : zone.max;
        var mid = (from + to) / 2;
        var px = yScale.getPixelForValue(mid);
        var t = (area.bottom - px) / (area.bottom - area.top);
        t = Math.min(1, Math.max(0, t));
        if (t <= lastT) t = Math.min(1, lastT + 0.001);
        lastT = t;
        var color = zone.segments[i][1];
        g.addColorStop(t, alpha ? hexToRgba(color, alpha) : color);
      }
      return g;
    };
  }

  function dataset(label, points, i, fill, zone) {
    var stroke = zone ? zoneGradient(zone) : PALETTE[i % PALETTE.length];
    var bg = zone ? zoneGradient(zone, 0.22) : PALETTE[i % PALETTE.length] + "26";
    return {
      label: label,
      data: points.map(function (p) { return { x: p[0], y: p[1] }; }),
      borderColor: stroke,
      backgroundColor: bg,
      borderWidth: 2,
      /* show markers while data is sparse, a clean line once it is not */
      pointRadius: points.length < 60 ? 3 : 0,
      pointBackgroundColor: stroke,
      pointBorderColor: stroke,
      pointHitRadius: 12, tension: 0.25,
      fill: !!fill,
      borderDash: !zone && i === 1 ? [6, 3] : undefined,  /* dash the 2nd series (colour-blind safe) */
    };
  }

  function fetchHistory(metric, hours) {
    return fetch("/api/v1/repeaters/" + encodeURIComponent(window.MCS.slug) +
                 "/history?metric=" + encodeURIComponent(metric) + "&hours=" + hours)
      .then(function (r) { return r.json(); });
  }

  // Metric labels arrive from the server in Dutch; the catalogue ones have a
  // translation, anything a node invented keeps the label it came with.
  function metricLabel(metric, fallback) {
    return t("metric." + metric) === "metric." + metric ? fallback : t("metric." + metric);
  }

  // --- fixed charts -----------------------------------------------------------
  document.querySelectorAll("[data-chart]").forEach(function (canvas) {
    if (typeof Chart === "undefined") return;
    var cfg = JSON.parse(canvas.dataset.chart);
    Promise.all(cfg.metrics.map(function (m) { return fetchHistory(m, cfg.hours); }))
      .then(function (results) {
        var single = cfg.metrics.length === 1;
        var datasets = results.map(function (res, i) {
          return dataset(metricLabel(cfg.metrics[i], cfg.labels[i]), res.points, i, single,
                         single ? zoneFor(cfg.metrics[i]) : null);
        });
        lineChart(canvas, datasets, cfg.unit, cfg.metrics.length > 1, cfg.hours);
      });
  });

  // --- history modal ----------------------------------------------------------
  var modal = document.getElementById("metric-modal");
  if (modal) {
    var modalTitle = document.getElementById("modal-title");
    var modalCanvas = document.getElementById("modal-canvas");
    var modalEmpty = document.getElementById("modal-empty");
    var rangeBtns = modal.querySelectorAll(".rangebtns button");
    var modalChart = null;
    var current = null; // {metric, label, unit}

    function loadModal(hours) {
      rangeBtns.forEach(function (b) {
        b.classList.toggle("active", parseInt(b.dataset.hours, 10) === hours);
      });
      fetchHistory(current.metric, hours).then(function (res) {
        if (modalChart) { modalChart.destroy(); modalChart = null; }
        var has = res.points && res.points.length > 0;
        modalEmpty.hidden = has;
        modalCanvas.parentElement.style.display = has ? "" : "none";
        if (!has) return;
        modalChart = lineChart(modalCanvas,
                               [dataset(current.label, res.points, 0, true, zoneFor(current.metric))],
                               current.unit, false, hours);
      });
    }

    function openModal(metric, label, unit) {
      current = { metric: metric, label: label, unit: unit };
      modalTitle.textContent = label;
      modal.hidden = false;
      document.body.style.overflow = "hidden";
      if (window.mcsModalMap) window.mcsModalMap(metric);
      loadModal((window.MCS && window.MCS.defaultHours) || 24);
    }
    function closeModal() {
      modal.hidden = true;
      document.body.style.overflow = "";
      if (modalChart) { modalChart.destroy(); modalChart = null; }
      if (window.mcsModalMap) window.mcsModalMap(null);
    }

    rangeBtns.forEach(function (b) {
      b.addEventListener("click", function () { loadModal(parseInt(b.dataset.hours, 10)); });
    });
    modal.querySelector(".modal-close").addEventListener("click", closeModal);
    modal.querySelector(".modal-backdrop").addEventListener("click", closeModal);
    document.addEventListener("keydown", function (e) {
      if (e.key === "Escape" && !modal.hidden) closeModal();
    });

    // clickable tiles
    document.querySelectorAll(".tile.clickable").forEach(function (tile) {
      tile.addEventListener("click", function () {
        openModal(tile.dataset.metric,
                  metricLabel(tile.dataset.metric, tile.dataset.label),
                  tile.dataset.unit || "");
      });
    });
    // clickable neighbours
    document.querySelectorAll("tr.nbrow").forEach(function (row) {
      row.addEventListener("click", function () {
        var cell = row.querySelector(".nbname");
        openModal(row.dataset.metric,
                  t("nb.link_snr", { name: cell ? cell.textContent : row.dataset.prefix }),
                  row.dataset.unit || "dB");
      });
    });
    window.mcsOpenModal = openModal;
  }

  // --- neighbour table: sorting (stored locally) and auto-refresh -------------
  var nbTable = document.querySelector("table.neighbors");
  if (nbTable && window.MCS) {
    var nbBody = nbTable.querySelector("tbody");
    var sortState = { key: "snr", dir: -1 };
    try {
      var saved = JSON.parse(localStorage.getItem("mcs-nbsort"));
      if (saved && ["name", "prefix", "snr", "seen"].indexOf(saved.key) !== -1) sortState = saved;
    } catch (e) { /* nothing to do */ }

    function applySort() {
      var rows = Array.prototype.slice.call(nbBody.querySelectorAll("tr.nbrow"));
      rows.sort(function (a, b) {
        var av, bv;
        if (sortState.key === "name") { av = a.dataset.name; bv = b.dataset.name; }
        else if (sortState.key === "prefix") { av = a.dataset.prefix; bv = b.dataset.prefix; }
        else if (sortState.key === "seen") { av = a.dataset.seen; bv = b.dataset.seen; }
        else { av = parseFloat(a.dataset.snr); bv = parseFloat(b.dataset.snr); }
        if (av < bv) return -sortState.dir;
        if (av > bv) return sortState.dir;
        return 0;
      });
      rows.forEach(function (r) { nbBody.appendChild(r); });
      nbTable.querySelectorAll("th.sortable").forEach(function (th) {
        th.classList.remove("sort-asc", "sort-desc");
        if (th.dataset.sort === sortState.key) {
          th.classList.add(sortState.dir === 1 ? "sort-asc" : "sort-desc");
        }
      });
    }

    nbTable.querySelectorAll("th.sortable").forEach(function (th) {
      th.addEventListener("click", function () {
        var key = th.dataset.sort;
        if (sortState.key === key) {
          sortState.dir = -sortState.dir;
        } else {
          // sensible default: name/prefix ascending, SNR and last-heard descending
          sortState = { key: key, dir: (key === "name" || key === "prefix") ? 1 : -1 };
        }
        try { localStorage.setItem("mcs-nbsort", JSON.stringify(sortState)); } catch (e) { /* nothing to do */ }
        applySort();
      });
    });
    applySort();

    // refresh neighbour data every minute and update the cells in place
    function refreshNeighbors() {
      fetch("/api/v1/repeaters/" + encodeURIComponent(window.MCS.slug))
        .then(function (r) { return r.json(); })
        .then(function (d) {
          (d.neighbors || []).forEach(function (n) {
            var row = nbBody.querySelector('tr.nbrow[data-prefix="' + n.prefix + '"]');
            if (!row) return; // a new neighbour shows up on the next page load
            row.dataset.snr = n.snr != null ? n.snr : -999;
            row.dataset.seen = n.last_seen || "";
            var cells = row.children; // chevron, name, prefix, snr, bar, time
            if (n.snr != null) {
              cells[3].textContent = n.snr.toFixed(2);
              cells[3].className = "num " +
                (n.snr >= 0 ? "snr-good" : n.snr >= -10 ? "snr-ok" : "snr-bad");
              var bar = row.querySelector(".snrbar i");
              if (bar) bar.style.width = Math.max(4, Math.min(100, (n.snr + 20) / 35 * 100)) + "%";
            }
            var t = row.querySelector("time.reltime");
            if (t && n.last_seen) t.setAttribute("datetime", n.last_seen);
          });
          updateTimes();
          applySort();
        })
        .catch(function () { /* try again in a minute */ });
    }
    setInterval(refreshNeighbors, 60000);
  }

  // --- link map ---------------------------------------------------------------
  function snrColor(snr) {
    if (snr == null) return "#7d8fa0";
    if (snr >= 0) return "#35e08c";
    if (snr >= -10) return "#ffb454";
    return "#ff5c5c";
  }
  var mapDataPromise = null;
  function getMapData() {
    if (!mapDataPromise) {
      mapDataPromise = fetch("/api/v1/repeaters/" + encodeURIComponent(window.MCS.slug) + "/map")
        .then(function (r) { return r.json(); });
    }
    return mapDataPromise;
  }

  var linkmapEl = document.getElementById("linkmap");
  if (linkmapEl && typeof L !== "undefined" && window.MCS) {
    getMapData().then(function (d) {
      if (!d.repeater) {
        linkmapEl.innerHTML = '<p class="muted" style="padding:1rem"></p>';
        linkmapEl.firstChild.textContent = t("map.nolocation");
        return;
      }
      var map = L.map(linkmapEl, { scrollWheelZoom: false });
      L.tileLayer(TILE_URL, {
        attribution: "&copy; OpenStreetMap &copy; CARTO", maxZoom: 19,
      }).addTo(map);
      var home = [d.repeater.lat, d.repeater.lon];
      L.circleMarker(home, { radius: 8, color: "#4cc9f0", weight: 2, fillColor: "#4cc9f0", fillOpacity: 1 })
        .addTo(map).bindTooltip(d.repeater.name, { direction: "top" });
      var bounds = [home];
      var labelTips = [];
      d.links.forEach(function (l) {
        var color = snrColor(l.snr);
        var label = (l.name || l.prefix.toUpperCase()) +
                    (l.snr != null ? " · " + l.snr.toFixed(2) + " dB" : "");
        var line = L.polyline([home, [l.lat, l.lon]], { color: color, weight: 2, opacity: 0.75 }).addTo(map);
        line.bindTooltip(label, { sticky: true });
        var marker = L.circleMarker([l.lat, l.lon], {
          radius: 5, color: color, weight: 1.5, fillColor: color, fillOpacity: 0.9,
        }).addTo(map);
        marker.bindTooltip(label, { direction: "top" });
        function open() {
          if (window.mcsOpenModal) {
            window.mcsOpenModal("neighbor_" + l.prefix,
                                t("nb.link_snr", { name: l.name || l.prefix.toUpperCase() }), "dB");
          }
        }
        line.on("click", open);
        marker.on("click", open);
        labelTips.push({ marker: marker, text: l.snr != null ? l.snr.toFixed(1) : "?" });
        bounds.push([l.lat, l.lon]);
      });
      map.fitBounds(bounds, { padding: [30, 30] });

      // legend
      var legend = L.control({ position: "bottomright" });
      legend.onAdd = function () {
        var div = L.DomUtil.create("div", "maplegend");
        div.innerHTML = "<strong></strong>" +
          '<span><i style="background:#35e08c"></i> <em></em></span>' +
          '<span><i style="background:#ffb454"></i> <em></em></span>' +
          '<span><i style="background:#ff5c5c"></i> <em></em></span>';
        div.querySelector("strong").textContent = t("map.legend");
        var tips = [t("map.legend_good"), t("map.legend_ok"), t("map.legend_bad")];
        div.querySelectorAll("em").forEach(function (em, i) { em.textContent = tips[i]; });
        return div;
      };
      legend.addTo(map);

      var note = document.getElementById("map-note");
      if (note && d.unlocated > 0) {
        var link = document.createElement("a");
        link.href = "#";
        link.textContent = t("map.unlocated", { n: d.unlocated }) + " ▸";
        note.appendChild(link);
        var list = document.createElement("div");
        list.className = "map-missing";
        list.hidden = true;
        list.textContent = t("map.unlocated_intro") +
                           (d.unlocated_names || []).join(" · ");
        note.parentElement.parentElement.insertBefore(list, note.parentElement.nextSibling);
        link.addEventListener("click", function (e) {
          e.preventDefault();
          list.hidden = !list.hidden;
          link.textContent = link.textContent.slice(0, -1) + (list.hidden ? "▸" : "▾");
        });
      }
      // toggleable SNR labels on the nodes
      var toggle = document.getElementById("map-labels");
      if (toggle) {
        toggle.addEventListener("change", function () {
          labelTips.forEach(function (t) {
            t.marker.unbindTooltip();
            if (toggle.checked) {
              t.marker.bindTooltip(t.text + " dB", {
                permanent: true, direction: "top", className: "snrlabel", offset: [0, -4],
              });
            } else {
              t.marker.bindTooltip(t.text + " dB", { direction: "top" });
            }
          });
        });
      }
    });
  }

  // --- live packet map (public home page) -------------------------------------
  // Colour per payload type, so a burst of adverts is distinguishable from
  // message traffic at a glance without reading the feed.
  var PKT_COLORS = {
    ADVERT: "#35e08c", TXT_MSG: "#4cc9f0", GRP_TXT: "#e06c9f", GRP_DATA: "#e06c9f",
    ACK: "#7d8fa0", PATH: "#ffb454", TRACE: "#c77dff", REQ: "#e8913a",
    RESPONSE: "#e8913a", ANON_REQ: "#e8913a",
  };
  var FLASH_MS = 1600;
  var POLL_MS = 4000;
  var FEED_MAX = 25;
  // A quiet mesh sends a handful of packets per poll; a storm could send
  // hundreds. Animating them all would only produce an unreadable blur.
  var FLASH_MAX_PER_POLL = 40;
  // A packet older than this is backlog, not live traffic. The server already
  // withholds the animation flag from the very first poll, but a tab that sat
  // hidden for an hour catches up through an ordinary poll, and flashing that
  // pile on return would present stale receptions as happening right now. The
  // cut-off is judged per packet on its own timestamp rather than per batch,
  // because a catch-up batch legitimately ends in genuinely fresh packets that
  // deserve their flash. Two minutes sits far above one poll interval plus any
  // reasonable server/browser clock skew, and far below any absence long
  // enough to pile up a misleading backlog.
  var FLASH_STALE_MS = 120000;
  // Packets kept in memory behind the visible feed, so switching the filter can
  // re-render from traffic that already arrived instead of only from what comes
  // next. Bounded because this page is left open for hours.
  var FEED_BUFFER = 300;
  var PATH_COLOR = "#c77dff";

  var livemapEl = document.getElementById("livemap");
  if (livemapEl && typeof L !== "undefined") {
    var lmap = L.map(livemapEl, { scrollWheelZoom: false });
    L.tileLayer(TILE_URL, { attribution: "&copy; OpenStreetMap &copy; CARTO", maxZoom: 19 })
      .addTo(lmap);
    var feedEl = document.getElementById("livefeed");
    var feedEmptyEl = document.getElementById("livefeed-empty");
    var liveCardEl = document.getElementById("livecard");
    var feedHeadEl = document.querySelector(".feedhead");
    var countEl = document.getElementById("live-count");
    var lastId = 0;
    var seen = [];        // {t, p} per reception, for the "last 5 minutes" counter
    var polling = false;
    var recent = [];      // newest first; the data behind the rendered feed
    var openId = null;    // id of the packet whose detail panel is open, if any

    function flash(lat, lon, color) {
      var ring = L.circleMarker([lat, lon], {
        radius: 4, color: color, weight: 2, fillColor: color, fillOpacity: 0.45,
      }).addTo(lmap);
      var start = null;
      function step(now) {
        if (start === null) start = now;
        var k = (now - start) / FLASH_MS;
        if (k >= 1) { lmap.removeLayer(ring); return; }
        ring.setRadius(4 + k * 26);
        ring.setStyle({ opacity: 1 - k, fillOpacity: 0.35 * (1 - k) });
        requestAnimationFrame(step);
      }
      requestAnimationFrame(step);
    }

    // --- packets travelling along their path ----------------------------------
    // One dot per packet, walking sender -> hops -> observer. Everything here is
    // written for a mesh that is about to get much noisier: a single rAF loop
    // drives every dot (not a timer each), the number in flight is capped, the
    // oldest is dropped rather than queued, and a hidden tab runs nothing at all.
    var MOTION_KEY = "mcs-pktmotion";
    var TRAVEL_MS = 2800;        // sender to observer, whole route
    var MAX_TRAVELERS = 18;
    var motionEl = document.getElementById("pkt-motion");
    var reduceMotion = window.matchMedia &&
      window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    var motionWanted = true;
    try {
      var savedMotion = localStorage.getItem(MOTION_KEY);
      if (savedMotion !== null) motionWanted = savedMotion === "1";
    } catch (e) { /* blocked */ }
    if (motionEl) {
      motionEl.checked = motionWanted && !reduceMotion;
      // A visitor who asked their system for less movement gets the flash, and
      // is told why the switch does nothing rather than left to wonder.
      motionEl.disabled = reduceMotion;
      if (reduceMotion) {
        var lbl = motionEl.closest("label");
        if (lbl) lbl.title = t("live.motion_reduced");
      }
      motionEl.addEventListener("change", function () {
        motionWanted = motionEl.checked;
        try { localStorage.setItem(MOTION_KEY, motionWanted ? "1" : "0"); } catch (e) { /* blocked */ }
        if (!motionWanted) clearTravelers();
      });
    }
    function animating() { return motionWanted && !reduceMotion; }

    var travelers = [];
    var travelRaf = null;

    // Route for the dot: the stops we can actually place, in order. ``certain``
    // is false as soon as one stop is missing -- an unplaceable hop, or a sender
    // no advert ever named -- and the route line is then drawn dashed and faint.
    // The dot still crosses that stretch, because the packet demonstrably did;
    // what we do not claim is which way round it went.
    function travelRoute(p) {
      var pts = [], certain = true;
      if (p.sender_lat != null && p.sender_lon != null) pts.push([p.sender_lat, p.sender_lon]);
      else certain = false;
      (p.path || []).forEach(function (h) {
        if (h.lat != null && h.lon != null) pts.push([h.lat, h.lon]);
        else certain = false;
      });
      if (p.observer_lat != null && p.observer_lon != null) pts.push([p.observer_lat, p.observer_lon]);
      else certain = false;
      return pts.length > 1 ? { pts: pts, certain: certain } : null;
    }

    function dropTraveler(tr) {
      lmap.removeLayer(tr.dot);
      lmap.removeLayer(tr.line);
    }

    function clearTravelers() {
      travelers.forEach(dropTraveler);
      travelers = [];
      if (travelRaf !== null) { cancelAnimationFrame(travelRaf); travelRaf = null; }
    }

    function travelStep(now) {
      travelRaf = null;
      for (var i = travelers.length - 1; i >= 0; i--) {
        var tr = travelers[i];
        var k = (now - tr.start) / TRAVEL_MS;
        if (k >= 1) {
          dropTraveler(tr);
          travelers.splice(i, 1);
          continue;
        }
        // Walk the cumulative segment lengths so the dot keeps a steady speed
        // over the whole route instead of racing through the short legs.
        var want = k * tr.total, acc = 0, j = 0;
        while (j < tr.legs.length - 1 && acc + tr.legs[j] < want) { acc += tr.legs[j]; j++; }
        var f = tr.legs[j] ? (want - acc) / tr.legs[j] : 0;
        var a = tr.pts[j], b = tr.pts[j + 1];
        tr.dot.setLatLng([a[0] + (b[0] - a[0]) * f, a[1] + (b[1] - a[1]) * f]);
        tr.line.setStyle({ opacity: (tr.certain ? 0.5 : 0.35) * (1 - k) });
      }
      if (travelers.length) travelRaf = requestAnimationFrame(travelStep);
    }

    function travel(p, color) {
      var route = travelRoute(p);
      if (!route) return false;
      var pts = route.pts, legs = [], total = 0;
      for (var i = 0; i + 1 < pts.length; i++) {
        // Plain planar distance in degrees: at mesh scale it is only used to
        // share the duration between legs, so a projection would buy nothing.
        var dy = pts[i + 1][0] - pts[i][0], dx = pts[i + 1][1] - pts[i][1];
        var d = Math.sqrt(dy * dy + dx * dx) || 1e-9;
        legs.push(d);
        total += d;
      }
      // Oldest out first: a burst must not be able to grow the layer count.
      while (travelers.length >= MAX_TRAVELERS) dropTraveler(travelers.shift());
      travelers.push({
        pts: pts, legs: legs, total: total, certain: route.certain,
        start: performance.now(),
        line: L.polyline(pts, {
          color: color, weight: route.certain ? 2 : 1.5,
          opacity: route.certain ? 0.5 : 0.35,
          dashArray: route.certain ? null : "5 7",
        }).addTo(lmap),
        dot: L.circleMarker(pts[0], {
          radius: 5, color: color, weight: 2, fillColor: color, fillOpacity: 0.95,
        }).addTo(lmap),
      });
      if (travelRaf === null) travelRaf = requestAnimationFrame(travelStep);
      return true;
    }

    // --- heat map of travelled paths --------------------------------------------
    // The server aggregates the full packet retention window (7 days by
    // default) of resolved paths into weighted segments (see
    // /api/v1/packets/heatmap): the feed's own buffer holds ~300 packets,
    // nowhere near enough traversals to show which links carry the mesh.
    // Deliberately ignores the name/country filter: the overlay answers "where
    // does the traffic flow", and a backbone with the busiest half filtered
    // away would be redrawn as something it is not.
    var HEAT_KEY = "mcs-pktheat";
    var HEAT_REFRESH_MS = 300000;  // a summary of a week can be five minutes old
    // Amber: legible over both tile themes, and not a colour the packet dots or
    // the opened route already speak in.
    var HEAT_COLOR = cssVar("--amber", "#ffb454");
    var heatEl = document.getElementById("pkt-heat");
    var heatLayer = null;
    var heatOn = false;
    try {
      heatOn = localStorage.getItem(HEAT_KEY) === "1";
    } catch (e) { /* blocked */ }

    function clearHeat() {
      if (heatLayer) { lmap.removeLayer(heatLayer); heatLayer = null; }
    }

    function drawHeat(d) {
      clearHeat();
      if (!heatOn || !d.segments || !d.segments.length) return;
      var group = L.layerGroup();
      var segs = d.segments;
      var days = Math.max(1, Math.round((d.window_h || 24) / 24));
      // Rank-scaled (empirical CDF), not log(1+n)/log(1+max) as before. The
      // measured distribution is brutally heavy-tailed: with a max of 304
      // traversals, half the segments sit at exactly 1 and ninety percent
      // under 5, so any magnitude-preserving scale -- log included -- crammed
      // ninety percent of the links into the bottom tenth of the visual range
      // and hundreds of near-identical lines melted into one amber wash. The
      // rank scale spends the range on where a link *stands among the others*
      // instead, which is the question a reader asks of a heat map; the exact
      // magnitude lives in the tooltip. It also has no degenerate case: when
      // every link was travelled equally often, everything lands in one tie
      // group at the floor and the map honestly shows nothing standing out
      // (the old max-normalisation drew that same situation at full blast).
      // Min/max normalisation with a max==min guard was rejected as it keeps
      // the crammed-bottom problem; server-sent quantiles were rejected as
      // redundant -- the server already sorts ascending, so the rank is free.
      //
      // k for a segment = fraction of segments strictly lighter than it, so
      // ties share one k (equal counts must look equal) and the once-heard
      // half starts at the very floor: hairline-thin and faint, present but
      // no longer a wash. Hiding them outright was rejected -- honesty about
      // what was heard beats tidiness, and a threshold toggle is a control
      // nobody asked for solving a problem the faint rendering already
      // solves. One forward walk finds the tie groups.
      var ks = new Array(segs.length);
      var start = 0;
      for (var i = 1; i <= segs.length; i++) {
        if (i === segs.length || segs[i].n !== segs[start].n) {
          for (var m = start; m < i; m++) ks[m] = start / segs.length;
          start = i;
        }
      }
      segs.forEach(function (s, si) {
        var k = ks[si];
        // Interactive, unlike most overlays here: the traversal count is the
        // one number this layer exists to show, and it needs a hover target.
        // The node markers are lifted above the lines below, so where the two
        // overlap the node still wins the pointer.
        L.polyline([[s.a.lat, s.a.lon], [s.b.lat, s.b.lon]], {
          color: HEAT_COLOR, weight: 1 + 5 * k, opacity: 0.12 + 0.68 * k,
        }).addTo(group).bindTooltip(t("live.heat_tip", {
          a: s.a.name || s.a.prefix.toUpperCase(),
          b: s.b.name || s.b.prefix.toUpperCase(),
          n: s.n,
          days: days,
        }), { direction: "top", sticky: true });
      });
      // The toggle's tooltip promises the whole retained period; when the
      // server had to cap the aggregation, that promise needs a footnote --
      // silently presenting a truncated week as complete is the one lie this
      // layer must never tell. Rewritten on every draw so a language switch
      // (which restores the static title) is corrected at the next refresh.
      var lbl = heatEl && heatEl.closest ? heatEl.closest("label") : null;
      if (lbl) {
        lbl.title = t("live.heat_title") +
          (d.capped ? " " + t("live.heat_capped") : "");
      }
      heatLayer = group.addTo(lmap);
      // Whichever came second -- this layer or the node dots -- the dots end up
      // on top, hoverable and the same size they always were.
      nodeMarkers.forEach(function (e) { e.m.bringToFront(); });
    }

    function loadHeat() {
      fetch("/api/v1/packets/heatmap")
        .then(function (r) { return r.json(); })
        .then(function (d) { if (heatOn) drawHeat(d); })
        .catch(function () { /* the next toggle or refresh tries again */ });
    }

    if (heatEl) {
      heatEl.checked = heatOn;
      heatEl.addEventListener("change", function () {
        heatOn = heatEl.checked;
        try { localStorage.setItem(HEAT_KEY, heatOn ? "1" : "0"); } catch (e) { /* blocked */ }
        if (heatOn) loadHeat(); else clearHeat();
      });
      if (heatOn) loadHeat();
      // A week-long summary drifts slowly, but this page is left open for hours;
      // refresh it on a clock far slower than the packet poll.
      setInterval(function () {
        if (heatOn && !document.hidden) loadHeat();
      }, HEAT_REFRESH_MS);
    }

    // The sender leads: it is what a reader is looking for. An advert names its
    // own; everything else falls back to the resolved 1-byte source hash, shown
    // in the muted style of a derivation rather than a stated fact. Only when
    // even that gives nothing does the column say "unknown" -- the full reason
    // belongs in the detail panel, not here.
    function senderCell(p) {
      var el = document.createElement("span");
      el.className = "pkt-who";
      if (p.sender_name || p.sender) {
        el.textContent = p.sender_name || p.sender.toUpperCase();
        return el;
      }
      var lbl = srcLabel(p.src, t);
      if (lbl) {
        el.textContent = lbl.text;
        el.title = lbl.title;
        el.classList.add("src-derived");
        return el;
      }
      el.textContent = t("pkt.sender_short");
      return el;
    }

    function cell(cls, text) {
      var el = document.createElement("span");
      el.className = cls;
      el.textContent = text;
      return el;
    }

    // Cell order in the DOM is not the order on screen: the timestamp sits right
    // after the sender so that a narrow screen can put the two on one line, and
    // CSS pushes it to the far right on a wide one. See .pkt-time { order }.
    function feedRow(p, quiet) {
      var li = document.createElement("li");
      var dot = document.createElement("i");
      dot.style.background = PKT_COLORS[p.type] || "#7d8fa0";
      li.appendChild(dot);
      li.appendChild(senderCell(p));

      var when = document.createElement("time");
      when.className = "reltime pkt-time";
      when.setAttribute("datetime", p.ts);
      li.appendChild(when);

      var brk = document.createElement("b");
      brk.className = "pkt-break";
      li.appendChild(brk);

      // Two spellings of the observer, and CSS picks one: the name where there
      // is room for it, the key prefix on a phone. Without that, one long node
      // name pushes every row onto a third line.
      var obs = document.createElement("span");
      obs.className = "pkt-obs";
      obs.appendChild(cell("obs-name", p.observer_name ||
                                       (p.observer || "").toUpperCase() || "—"));
      obs.appendChild(cell("obs-prefix", (p.observer || "").slice(0, 6).toUpperCase() || "—"));
      li.appendChild(obs);
      li.appendChild(cell("pkt-type", p.type || "?"));
      li.appendChild(scopeCell(p));
      li.appendChild(cell("pkt-snr", p.snr != null ? p.snr.toFixed(1) + " dB" : "—"));
      li.appendChild(cell("pkt-rssi", p.rssi != null ? p.rssi + " dBm" : "—"));
      li.appendChild(cell("pkt-hops", p.path_len
        ? t(p.path_len > 1 ? "live.hops_plural" : "live.hops", { n: p.path_len })
        : "—"));
      li.appendChild(cell("pkt-len", p.len != null ? p.len + " B" : "—"));
      li.appendChild(cell("pkt-cc", p.country ? flagOf(p.country) + " " + p.country : "—"));

      li.dataset.id = p.id;
      li.tabIndex = 0;
      li.setAttribute("role", "button");
      if (openId === p.id) li.classList.add("selected");
      // A whole-list re-render (filter change) must not replay the arrival
      // animation on rows that did not just arrive.
      if (quiet) li.style.animation = "none";
      return li;
    }

    // "Heard by" is dead weight while a single node forwards everything -- the
    // same name on every row. It is not dropped, because the moment a second
    // node starts forwarding it becomes one of the most interesting columns:
    // who heard what. So it appears by itself, as soon as the packets on show
    // actually come from more than one observer.
    function updateObserverColumn(shown) {
      if (!liveCardEl) return;
      var first = null, several = false;
      for (var i = 0; i < shown.length && !several; i++) {
        var o = shown[i].observer || "";
        if (first === null) first = o;
        else if (o !== first) several = true;
      }
      liveCardEl.classList.toggle("show-observer", several);
    }

    // --- filtering the feed ---------------------------------------------------
    // Remembered in localStorage next to the theme and the language, so a
    // visitor who only cares about one node keeps that view across visits.
    var FILTER_KEY = "mcs-pktfilter";
    var COUNTRY_KEY = "mcs-pktcountry";
    var filterEl = document.getElementById("pkt-filter");
    var countryEl = document.getElementById("pkt-country");
    var filterCountEl = document.getElementById("pkt-filter-count");
    var filterText = "";
    var filterCountry = "";
    try {
      filterText = localStorage.getItem(FILTER_KEY) || "";
      filterCountry = localStorage.getItem(COUNTRY_KEY) || "";
    } catch (e) { /* blocked */ }
    if (filterEl) filterEl.value = filterText;

    // Built rather than templated, because the region has to be its own element:
    // it is the part a phone drops. Four extra characters are enough to push the
    // second line of a row onto a third, and the panel names the region anyway.
    function scopeCell(p) {
      var el = document.createElement("span");
      el.className = "pkt-scope";
      if (!p.scope) {
        el.textContent = "—";
        return el;
      }
      el.textContent = t("scope." + p.scope);
      if (p.scope_region) {
        var region = document.createElement("span");
        region.className = "pkt-region";
        region.textContent = " · " + p.scope_region;
        el.appendChild(region);
      }
      return el;
    }

    // One haystack for name, prefix, payload type, country and scope: typing
    // "adv", "2ae7", "be", "scoped" or part of a node name all mean the same
    // thing to a visitor -- "show me the packets that mention this".
    function matches(p) {
      if (filterCountry) {
        // "??" is a visitor asking for the packets we could not place. That is a
        // real answer about the mesh, not the absence of a filter.
        var want = filterCountry === "??" ? null : filterCountry;
        if ((p.country || null) !== want) return false;
      }
      var q = filterText.trim().toLowerCase();
      if (!q) return true;
      // The scope goes in twice: as stored, so "scoped" keeps working whatever
      // the page language, and as shown, so a Dutch visitor typing what is on
      // screen finds the same rows. Source candidates count too: a name in the
      // sender column must be findable however it got there.
      var srcNames = (p.src && p.src.matches || []).map(function (m) {
        return m.name || m.prefix;
      }).join(" ");
      return [p.sender_name, p.observer_name, p.sender, p.observer, p.type,
              p.country, p.scope, p.scope && t("scope." + p.scope), srcNames]
        .filter(Boolean).join(" ").toLowerCase().indexOf(q) !== -1;
    }

    // Built from the feed. The server omits the country list entirely when it
    // has no borders to classify against, and then the control stays hidden --
    // but a choice remembered from a deployment that did have borders must be
    // dropped with it, or the visitor is left filtering everything away with no
    // control on screen to undo it.
    function buildCountryFilter(codes) {
      if (!countryEl) return;
      if (!codes || !codes.length) {
        countryEl.hidden = true;
        forgetCountry();
        return;
      }
      var opts = [["", t("live.country_all")]];
      codes.forEach(function (c) { opts.push([c, countryLabel(c)]); });
      opts.push(["??", t("live.country_none")]);
      countryEl.textContent = "";
      opts.forEach(function (pair) {
        var o = document.createElement("option");
        o.value = pair[0];
        o.textContent = pair[1];
        countryEl.appendChild(o);
      });
      // Same trap, milder: a country that has gone quiet since the last visit is
      // no longer on the list, and selecting nothing is better than selecting
      // something unreachable.
      if (filterCountry && !opts.some(function (o) { return o[0] === filterCountry; })) {
        forgetCountry();
      }
      countryEl.value = filterCountry;
      countryEl.hidden = false;
    }

    function forgetCountry() {
      if (!filterCountry) return;
      filterCountry = "";
      try { localStorage.removeItem(COUNTRY_KEY); } catch (e) { /* blocked */ }
      renderFeed();
    }

    if (countryEl) {
      countryEl.addEventListener("change", function () {
        filterCountry = countryEl.value;
        try {
          if (filterCountry) localStorage.setItem(COUNTRY_KEY, filterCountry);
          else localStorage.removeItem(COUNTRY_KEY);
        } catch (e) { /* blocked */ }
        renderFeed(true);
      });
    }

    // --- the node layer follows the filter ------------------------------------
    // Kept as objects rather than thrown away and rebuilt: with a few hundred
    // nodes, recreating every marker on each keystroke is the one thing here
    // that would actually feel slow. Each entry remembers the style it is
    // wearing so a pass only touches the markers whose state really changed.
    var nodeMarkers = [];        // [{n: node, m: marker, style: "on"|"dim"}]
    var mapEmptyEl = document.getElementById("map-empty");
    var pathPrefixes = null;     // prefixes of the open packet's path, or null

    var NODE_ON = { radius: 4, color: "#7d8fa0", weight: 1, fillColor: "#7d8fa0",
                    fillOpacity: 0.5, opacity: 1 };
    // Dimmed rather than removed. Hiding is tidier, but the mesh is the point of
    // this map: a Dutch node means little without the Belgian ones around it,
    // and a path that crosses the filter would otherwise end at markers that are
    // not there. Faint keeps the geography and still lets the matches carry the
    // eye. Tooltips stay attached, so a ghost can still be identified on hover.
    var NODE_DIM = { radius: 3, color: "#7d8fa0", weight: 1, fillColor: "#7d8fa0",
                     fillOpacity: 0.08, opacity: 0.18 };

    // Which nodes the filter is *about*. The country choice always applies. The
    // text only applies when it matches at least one node: otherwise a visitor
    // typing a payload type ("advert") would dim all 218 nodes and be told
    // nothing matches, when what they filtered was traffic, not geography.
    function textMatchesAnyNode() {
      var q = filterText.trim().toLowerCase();
      if (!q) return false;
      return nodeMarkers.some(function (e) { return nodeText(e.n).indexOf(q) !== -1; });
    }

    function nodeText(n) {
      return [n.name, n.prefix, n.country].filter(Boolean).join(" ").toLowerCase();
    }

    function nodeMatches(n, useText) {
      // The open packet's path is exempt, so a route stays whole even where it
      // leaves the filter. A gap in a drawn path has to mean "we do not know",
      // never "you filtered this out" -- see drawPath.
      if (pathPrefixes && pathPrefixes[n.prefix]) return true;
      if (filterCountry) {
        var want = filterCountry === "??" ? null : filterCountry;
        if ((n.country || null) !== want) return false;
      }
      if (!useText) return true;
      return nodeText(n).indexOf(filterText.trim().toLowerCase()) !== -1;
    }

    function applyNodeFilter() {
      if (!nodeMarkers.length) return;
      var useText = textMatchesAnyNode();
      var shown = 0;
      nodeMarkers.forEach(function (e) {
        var want = nodeMatches(e.n, useText) ? "on" : "dim";
        if (want === "on") shown++;
        if (e.style === want) return;      // nothing to repaint
        e.style = want;
        e.m.setStyle(want === "on" ? NODE_ON : NODE_DIM);
      });
      if (mapEmptyEl) mapEmptyEl.hidden = shown > 0;
      return shown;
    }

    // Move the view only when the filter leaves nothing to look at. Filtering to
    // Great Britain while parked over Belgium otherwise shows an empty map, but
    // yanking the view around after every keystroke would fight a visitor who
    // just zoomed somewhere deliberately. So: if a match is already on screen,
    // stay put.
    //
    // With the detail panel open the view is never moved by the *filter*. Do not
    // confuse that with the framing a packet gets when it is opened, which does
    // move the map and must -- see mapPadding(). The distinction is the layout:
    // on a wide screen the panel sits beside the map and leaves the picture
    // intact, so nothing needs to move; the sheet on a phone lies over the map,
    // so the route has to be framed into the strip that is left. Filtering while
    // a packet is open would only fight the framing that packet just asked for.
    function fitToMatches() {
      if (panelOpen()) return;
      var view = lmap.getBounds();
      var pts = [];
      var visible = false;
      var useText = textMatchesAnyNode();
      nodeMarkers.forEach(function (e) {
        if (!nodeMatches(e.n, useText)) return;
        var ll = e.m.getLatLng();
        pts.push(ll);
        if (view.contains(ll)) visible = true;
      });
      if (!pts.length || visible) return;
      lmap.fitBounds(pts, { padding: [40, 40], maxZoom: 12 });
    }

    function updateFeedState() {
      var active = filterText.trim() !== "" || filterCountry !== "";
      var hits = active ? recent.filter(matches).length : recent.length;
      if (feedEmptyEl) feedEmptyEl.hidden = !(active && hits === 0);
      if (filterCountEl) {
        filterCountEl.textContent = active
          ? t("live.filtered", { n: hits, total: recent.length }) : "";
      }
    }

    // The packets the feed is showing: the filtered head of the buffer, capped
    // the same way the rendered list is.
    function visiblePackets() {
      var out = [];
      for (var i = 0; i < recent.length && out.length < FEED_MAX; i++) {
        if (matches(recent[i])) out.push(recent[i]);
      }
      return out;
    }

    // The list scrolls and the header does not, so once the list grows a
    // scrollbar its columns shift left by the scrollbar's width and stop lining
    // up with the headings. Measured rather than assumed: it is zero on the
    // platforms that use overlay scrollbars.
    function syncHeadGutter() {
      if (!feedHeadEl || !feedEl) return;
      var gutter = feedEl.offsetWidth - feedEl.clientWidth;
      feedHeadEl.style.paddingRight = gutter ? "calc(.4rem + " + gutter + "px)" : "";
    }

    function renderFeed(refit) {
      if (feedEl) {
        feedEl.textContent = "";
        var shown = visiblePackets();
        shown.forEach(function (p) { feedEl.appendChild(feedRow(p, true)); });
        updateObserverColumn(shown);
        syncHeadGutter();
      }
      applyNodeFilter();
      if (refit) fitToMatches();
      updateFeedState();
      updateCount();
      updateTimes();
    }

    if (filterEl) {
      filterEl.addEventListener("input", function () {
        filterText = filterEl.value;
        try { localStorage.setItem(FILTER_KEY, filterText); } catch (e) { /* blocked */ }
        renderFeed(true);
      });
    }

    function render(list, animate) {
      var flashes = 0;
      list.forEach(function (p) {
        // Reception time comes from the packet, so a backlog is dated when it
        // was heard; only a packet without a usable timestamp falls back to now.
        var at = Date.parse(p.ts);
        var heard = isNaN(at) ? Date.now() : at;
        // The filter governs the map as well as the list: a visitor watching one
        // node should not have the rest of the mesh moving over their map. And
        // only fresh packets animate: a catch-up batch after a hidden tab still
        // fills the list, but its stale part must not be acted out as live.
        var show = animate && heard >= Date.now() - FLASH_STALE_MS &&
          matches(p) && flashes < FLASH_MAX_PER_POLL;
        if (show) {
          var color = PKT_COLORS[p.type] || "#7d8fa0";
          // A packet with a route travels it; one we cannot place a route for
          // falls back to the flash, which needs only the single position.
          var moved = animating() && travel(p, color);
          if (!moved && p.lat != null && p.lon != null) flash(p.lat, p.lon, color);
          flashes++;
        }
        seen.push({ t: heard, p: p });
        recent.unshift(p);
        if (feedEl && matches(p)) feedEl.insertBefore(feedRow(p), feedEl.firstChild);
      });
      if (recent.length > FEED_BUFFER) recent.length = FEED_BUFFER;
      if (feedEl) {
        while (feedEl.children.length > FEED_MAX) feedEl.removeChild(feedEl.lastChild);
        updateObserverColumn(visiblePackets());
        syncHeadGutter();
      }
      updateFeedState();
      updateTimes();
      updateCount();
    }

    // Counted from the packets themselves rather than from a list of arrival
    // times, for two reasons. The filter has to reach the counter as well -- "42
    // packets" over a list showing two is the same lie the map was telling with
    // its unfiltered markers. And the window is measured on each packet's own
    // timestamp, so the backlog that arrives on the first poll no longer reports
    // hours-old traffic as if it had just been heard.
    function updateCount() {
      if (!countEl) return;
      var cutoff = Date.now() - 300000;
      var n = 0;
      for (var i = 0; i < seen.length; i++) {
        if (seen[i].t >= cutoff && matches(seen[i].p)) n++;
      }
      // Pruning here keeps the window bounded without a timer of its own.
      // Tested on the OLDEST entry: receptions are appended, so seen[0] is the
      // eldest. Checking the last one instead would only ever fire when the
      // whole mesh had gone quiet, and the array would grow all day.
      if (seen.length && seen[0].t < cutoff) {
        seen = seen.filter(function (s) { return s.t >= cutoff; });
      }
      countEl.textContent = n ? t("live.count", { n: n }) : t("live.waiting");
    }

    function poll(first) {
      if (polling) return;
      polling = true;
      fetch("/api/v1/packets?since_id=" + lastId)
        .then(function (r) { return r.json(); })
        .then(function (d) {
          if (first) buildCountryFilter(d.countries);
          if (first && d.nodes) {
            var bounds = [];
            d.nodes.forEach(function (n) {
              var marker = L.circleMarker([n.lat, n.lon], NODE_ON)
                .addTo(lmap)
                .bindTooltip(n.name || n.prefix.toUpperCase(), { direction: "top" });
              // Held on to so the filter can restyle them; see applyNodeFilter.
              nodeMarkers.push({ n: n, m: marker, style: "on" });
              bounds.push([n.lat, n.lon]);
            });
            if (bounds.length) lmap.fitBounds(bounds, { padding: [40, 40], maxZoom: 12 });
            // A filter restored from localStorage has to reach the layer that
            // was only just built, and the view should start where the matches
            // are rather than on the whole mesh.
            applyNodeFilter();
            fitToMatches();
          }
          lastId = d.last_id || lastId;
          // The first response is history (the newest stored packets), not
          // traffic heard while this page was open: list it, but do not set off
          // a firework of flashes for receptions that predate the visit.
          render(d.packets || [], !first);
        })
        .catch(function () { /* next tick tries again */ })
        .then(function () { polling = false; });
    }

    // --- packet detail panel ---------------------------------------------------
    // Deliberately a docked panel and not a modal with a backdrop: the path of
    // the packet is drawn on the live map underneath, and a modal would cover
    // the very thing it is explaining.
    var panel = document.getElementById("packet-panel");
    var pathLayer = null;
    var pathView = null;         // the points the open route was framed on

    // --- the panel as a bottom sheet on a narrow screen -----------------------
    // Must match the media query in style.css; the two are a pair.
    var SHEET_MAX_WIDTH = 820;
    var gripEl = document.getElementById("pkt-grip");

    function sheetMode() { return window.innerWidth <= SHEET_MAX_WIDTH; }

    // Enough for the head and the first few fields -- time, sender, observer,
    // payload type -- while leaving the map worth looking at. Capped against the
    // viewport so landscape, where height is scarce, does not get a sheet that
    // covers everything.
    function peekHeight() {
      return Math.round(Math.min(280, Math.max(132, window.innerHeight * 0.38)));
    }
    function fullHeight() { return Math.round(window.innerHeight * 0.85); }

    function setSheetHeight(px) {
      if (!panel) return;
      var h = Math.max(96, Math.min(px, fullHeight()));
      panel.style.setProperty("--sheet-h", Math.round(h) + "px");
      return h;
    }

    // Always reopened at the peek height, never at whatever the last packet was
    // left at: the reason to open one of these is to see something on the map,
    // and a sheet remembering "fully raised" would hide it every time.
    function resetSheet() {
      if (!panel) return;
      panel.classList.remove("sheet-dragging");
      if (sheetMode()) setSheetHeight(peekHeight());
      else panel.style.removeProperty("--sheet-h");
    }

    function toggleSheet() {
      if (!sheetMode()) return;
      var mid = (peekHeight() + fullHeight()) / 2;
      var now = panel.getBoundingClientRect().height;
      setSheetHeight(now < mid ? fullHeight() : peekHeight());
      // The visible slice of map just changed, so the route has to be reframed
      // for it; that is the whole point of doing this on a phone.
      setTimeout(fitPathView, 200);   // after the height transition
    }

    if (gripEl && panel) {
      var drag = null;
      gripEl.addEventListener("pointerdown", function (e) {
        if (!sheetMode()) return;
        drag = { y: e.clientY, h: panel.getBoundingClientRect().height, moved: false };
        panel.classList.add("sheet-dragging");
        try { gripEl.setPointerCapture(e.pointerId); } catch (err) { /* not captured */ }
        e.preventDefault();
      });
      gripEl.addEventListener("pointermove", function (e) {
        if (!drag) return;
        var dy = drag.y - e.clientY;          // up is a taller sheet
        if (Math.abs(dy) > 4) drag.moved = true;
        setSheetHeight(drag.h + dy);
      });
      function endDrag() {
        if (!drag) return;
        var moved = drag.moved;
        drag = null;
        panel.classList.remove("sheet-dragging");
        if (!moved) { toggleSheet(); return; }   // a tap, not a drag
        // Snap to whichever stop the sheet ended up nearer.
        var mid = (peekHeight() + fullHeight()) / 2;
        setSheetHeight(panel.getBoundingClientRect().height < mid
          ? peekHeight() : fullHeight());
        setTimeout(fitPathView, 200);
      }
      gripEl.addEventListener("pointerup", endDrag);
      gripEl.addEventListener("pointercancel", endDrag);
      gripEl.addEventListener("keydown", function (e) {
        if (e.key !== "Enter" && e.key !== " ") return;
        e.preventDefault();
        toggleSheet();
      });
    }

    // Rotating a phone changes both the stops and the visible slice of map.
    var resizeTimer = null;
    window.addEventListener("resize", function () {
      clearTimeout(resizeTimer);
      resizeTimer = setTimeout(function () {
        // A width change can add or remove the feed's scrollbar, and the header
        // has to be re-gutted for it or the columns stop lining up.
        syncHeadGutter();
        if (!panel || panel.hidden) return;
        if (!sheetMode()) {
          panel.style.removeProperty("--sheet-h");
        } else {
          // Re-clamp, do not reset. A phone fires resize whenever the address
          // bar slides away, and snapping the sheet back to peek in the middle
          // of reading it would be its own bug.
          setSheetHeight(panel.getBoundingClientRect().height);
        }
        fitPathView();
      }, 150);
    });

    function panelOpen() { return !!panel && !panel.hidden; }

    function clearPath() {
      if (pathLayer) { lmap.removeLayer(pathLayer); pathLayer = null; }
      pathView = null;
      if (pathPrefixes) { pathPrefixes = null; applyNodeFilter(); }
    }

    function markSelected(id) {
      if (!feedEl) return;
      Array.prototype.forEach.call(feedEl.children, function (li) {
        li.classList.toggle("selected", parseInt(li.dataset.id, 10) === id);
      });
    }

    function closePanel() {
      if (!panel) return;
      panel.hidden = true;
      openId = null;
      clearPath();
      markSelected(-1);
    }

    // A hop that resolves to several candidates gets a hollow ring on each of
    // them rather than a line: showing all the possibilities is honest, picking
    // one of them would not be.
    function markCandidates(group, hop, bounds) {
      if (!hop || hop.state !== "ambiguous") return;
      hop.matches.forEach(function (m) {
        if (m.lat == null || m.lon == null) return;
        L.circleMarker([m.lat, m.lon], {
          radius: 8, color: PATH_COLOR, weight: 1.5, opacity: 0.8,
          dashArray: "3 3", fillOpacity: 0,
        }).addTo(group).bindTooltip(
          t("pkt.hop_maybe", { name: m.name || m.prefix.toUpperCase() }), { direction: "top" });
        // Candidates count towards the view: a ring nobody can see marks
        // nothing, even if the packet itself travelled a much shorter way.
        bounds.push([m.lat, m.lon]);
      });
    }

    // Draw sender -> every hop -> observer.
    //
    // Only hops that resolve to exactly one known node have a position we are
    // entitled to draw through: a path entry is one or two bytes of a public
    // key, so with hundreds of nodes on the map several of them can answer to
    // the same hop (see _resolve_hop in routes_api.py). Ambiguous and unknown
    // hops are therefore left out of the line and the segment that spans them is
    // dashed -- a solid line through a guess would claim knowledge the protocol
    // cannot give. This is not a bug in the drawing code; it is the protocol.
    function drawPath(d) {
      clearPath();
      // Every node this route touches is exempt from the filter for as long as
      // the panel is open. A route drawn with holes in it would read as
      // uncertainty, and uncertainty here has a precise meaning that the filter
      // must not be allowed to imitate.
      pathPrefixes = {};
      var stops = [{
        lat: d.sender_lat, lon: d.sender_lon,
        label: nodeLabel(d.sender, d.sender_name), role: "origin",
      }];
      if (d.sender) pathPrefixes[d.sender] = true;
      if (d.observer) pathPrefixes[d.observer.slice(0, 6)] = true;
      (d.path || []).forEach(function (h) {
        var m = h.state === "known" ? h.matches[0] : null;
        // Ambiguous hops exempt every candidate: their rings are part of the
        // same answer as the line is.
        (h.matches || []).forEach(function (c) { pathPrefixes[c.prefix] = true; });
        stops.push({
          lat: m ? m.lat : null, lon: m ? m.lon : null,
          label: m ? (m.name || m.prefix.toUpperCase()) : null, role: "hop", hop: h,
        });
      });
      stops.push({
        lat: d.observer_lat, lon: d.observer_lon,
        label: nodeLabel(d.observer, d.observer_name), role: "dest",
      });
      applyNodeFilter();

      var group = L.layerGroup();
      var prev = null, gap = false, view = [];
      stops.forEach(function (s) {
        if (s.lat == null || s.lon == null) {
          gap = true;                 // we cannot place this stop: bridge over it
          markCandidates(group, s.hop, view);
          return;
        }
        var here = [s.lat, s.lon];
        if (prev) {
          L.polyline([prev, here], {
            color: PATH_COLOR, weight: gap ? 2 : 3, opacity: gap ? 0.6 : 0.95,
            dashArray: gap ? "7 8" : null,
          }).addTo(group);
        }
        var end = s.role !== "hop";
        L.circleMarker(here, {
          radius: end ? 7 : 5, color: PATH_COLOR, weight: 2,
          fillColor: PATH_COLOR, fillOpacity: end ? 1 : 0.55,
        }).addTo(group).bindTooltip(
          s.label + (end ? " · " + t(s.role === "origin" ? "pkt.origin" : "pkt.destination") : ""),
          { direction: "top" });
        view.push(here);
        prev = here;
        gap = false;
      });

      pathLayer = group.addTo(lmap);
      pathView = view;
      // Scroll before framing, and instantly: the padding below is measured off
      // the map's real rectangle, and a smooth scroll would still be moving
      // while we measured it.
      var box = livemapEl.getBoundingClientRect();
      if (box.top < 60 || box.bottom > window.innerHeight) {
        livemapEl.scrollIntoView({ block: "start" });
      }
      fitPathView();
    }

    // Frame the route inside the part of the map that is actually visible.
    //
    // The wide layout docks the panel beside the map, so only its width has to
    // be kept clear. The sheet layout puts it *over* the map, and Leaflet knows
    // nothing about that: without this it centres the path in the full map
    // element, half of which is behind the sheet -- the path is drawn correctly
    // and two thirds of it sit under the panel.
    function mapPadding() {
      var box = livemapEl.getBoundingClientRect();
      var open = panel && !panel.hidden;
      var sheet = sheetMode();

      // Vertical, and computed the same way in both layouts. The map element is
      // routinely taller than the part of it on screen -- a phone held sideways
      // has barely 390 px of viewport for a 420 px map -- and on top of that the
      // sheet covers its lower part. Clipping against the viewport and the sheet
      // together covers both without a special case for either.
      var floor = sheet && open ? panel.getBoundingClientRect().top : window.innerHeight;
      var visTop = Math.max(box.top, 0);
      var visBottom = Math.min(box.bottom, window.innerHeight, floor);
      var padTop = Math.max(0, visTop - box.top) + 16;
      var padBottom = Math.max(0, box.bottom - visBottom) + 16;

      // Leave Leaflet a usable band. With the sheet dragged fully up there may be
      // only a sliver of map left, and fitting a route across three countries
      // into twenty pixels produces a dot, not a route -- so the band has a
      // floor, and past it the route is allowed to run behind the sheet. Whoever
      // dragged the sheet up is reading the panel; lowering it reframes.
      var MIN_BAND = 60;
      if (box.height - padTop - padBottom < MIN_BAND) {
        padBottom = Math.max(0, box.height - padTop - MIN_BAND);
      }

      // Horizontal is the only part that differs: the side drawer takes width
      // out of the map, the sheet spans it and takes none.
      var padRight = open && !sheet ? Math.min(430, window.innerWidth * 0.4) : 16;
      return {
        paddingTopLeft: [16, Math.round(padTop)],
        paddingBottomRight: [Math.round(padRight), Math.round(padBottom)],
      };
    }

    function fitPathView() {
      if (!pathView || pathView.length < 2) return;
      var pad = mapPadding();
      pad.maxZoom = 13;
      lmap.fitBounds(pathView, pad);
    }

    // Filling and emptying live at the top level: the archive renders the same
    // fragment. See fillPacketDetail. The live page passes no filter handler --
    // it has no query bar for the buttons to write into -- and lets the country
    // row follow the country filter, which is only present when the deployment
    // has borders to classify positions against.
    function fillPanel(d) {
      fillPacketDetail(d, { showCountry: !!countryEl && !countryEl.hidden });
    }

    function openPacket(id) {
      if (!panel || !id) return;
      openId = id;
      blankPacketDetail();
      panel.hidden = false;
      resetSheet();          // every packet starts at the peek height
      var body = document.getElementById("pkt-body");
      if (body) body.scrollTop = 0;
      markSelected(id);
      clearPath();
      fetch("/api/v1/packets/" + encodeURIComponent(id))
        .then(function (r) {
          if (!r.ok) throw new Error("HTTP " + r.status);
          return r.json();
        })
        .then(function (d) {
          if (openId !== id) return;   // a second click already took over
          fillPanel(d);
          drawPath(d);
        })
        .catch(function () {
          if (openId === id) txt("pkt-path-note", t("pkt.loaderror"));
        });
    }

    if (panel && feedEl) {
      feedEl.addEventListener("click", function (e) {
        var li = e.target.closest("li[data-id]");
        if (li) openPacket(parseInt(li.dataset.id, 10));
      });
      feedEl.addEventListener("keydown", function (e) {
        if (e.key !== "Enter" && e.key !== " ") return;
        var li = e.target.closest("li[data-id]");
        if (!li) return;
        e.preventDefault();
        openPacket(parseInt(li.dataset.id, 10));
      });
      document.getElementById("pkt-close").addEventListener("click", closePanel);
      document.addEventListener("keydown", function (e) {
        if (e.key === "Escape" && !panel.hidden) closePanel();
      });
    }

    renderFeed();   // restores the "no matches" state before any traffic arrives
    poll(true);
    setInterval(function () {
      // No point polling a tab nobody is looking at; the backlog is still there
      // when it comes back, since the server hands out everything after lastId.
      if (!document.hidden) poll(false);
    }, POLL_MS);
    document.addEventListener("visibilitychange", function () {
      if (document.hidden) {
        // A hidden tab throttles rAF to a crawl, so dots would either freeze
        // mid-route or jump on return. Drop them and start clean instead.
        clearTravelers();
        return;
      }
      poll(false);   // catch up at once instead of after a tick
    });
    setInterval(updateCount, 30000);
  }

  // mini-map inside the history modal of a neighbour link
  var modalMapEl = document.getElementById("modal-map");
  var modalMap = null;
  window.mcsModalMap = function (metric) {
    if (!modalMapEl || typeof L === "undefined") return;
    if (modalMap) { modalMap.remove(); modalMap = null; }
    modalMapEl.hidden = true;
    var m = /^neighbor_([0-9a-f]{6})$/.exec(metric || "");
    if (!m) return;
    getMapData().then(function (d) {
      if (!d.repeater) return;
      var link = d.links.find(function (l) { return l.prefix === m[1]; });
      if (!link) return;
      modalMapEl.hidden = false;
      modalMap = L.map(modalMapEl, { scrollWheelZoom: false, zoomControl: false });
      L.tileLayer(TILE_URL, {
        attribution: "&copy; OSM &copy; CARTO", maxZoom: 19,
      }).addTo(modalMap);
      var home = [d.repeater.lat, d.repeater.lon];
      var there = [link.lat, link.lon];
      var color = snrColor(link.snr);
      L.circleMarker(home, { radius: 6, color: "#4cc9f0", fillColor: "#4cc9f0", fillOpacity: 1 })
        .addTo(modalMap).bindTooltip(d.repeater.name);
      L.circleMarker(there, { radius: 6, color: color, fillColor: color, fillOpacity: 1 })
        .addTo(modalMap).bindTooltip(link.name || link.prefix.toUpperCase());
      L.polyline([home, there], { color: color, weight: 2.5, opacity: 0.85 }).addTo(modalMap);
      modalMap.fitBounds([home, there], { padding: [25, 25] });
      setTimeout(function () { modalMap && modalMap.invalidateSize(); }, 100);
    });
  };

  // --- packet archive (/pakketten) ---------------------------------------------
  // Same guard pattern as the live map: the block wires itself only when its
  // root element is on the page.
  var archEl = document.getElementById("archive");
  if (archEl) {
    var PAGE_SIZE = 100;
    // Which fields get a top-values panel. A fixed list rather than "everything
    // facetable": each facet is one GROUP BY over the matches, and eight panels
    // would make every keystroke of refinement eight times as heavy for a
    // sidebar nobody reads past the fourth block of.
    var FACETS = ["type", "scope", "sender", "observer", "country"];
    var qEl = document.getElementById("arch-q");
    var windowEl = document.getElementById("arch-window");
    var errEl = document.getElementById("arch-error");
    var listEl = document.getElementById("arch-list");
    var emptyEl = document.getElementById("arch-empty");
    var countEl2 = document.getElementById("arch-count");
    var facetsEl = document.getElementById("arch-facets");
    var canvasEl = document.getElementById("arch-canvas");
    var pageEl = document.getElementById("arch-page");
    var prevEl = document.getElementById("arch-prev");
    var nextEl = document.getElementById("arch-next");
    var pktModalEl = document.getElementById("pkt-modal");
    var archOffset = 0;
    var archTotal = 0;
    var archSeq = 0;      // stale responses from a slower earlier search are dropped
    var openPktId = null; // id of the packet whose modal is open, if any

    // The field table of the query language, straight from search.py. Without
    // it the buttons would be gated on a hand-copied list that goes stale the
    // first time a field is renamed on the server.
    SEARCH_FIELDS = (archEl.dataset.fields || "").split(",").filter(Boolean);

    // The query, the window and the open packet live in the URL, so a search or
    // a single packet can be sent to someone as a link -- for a search page that
    // is not a nicety, it is what makes results citable.
    var initialPkt = 0;
    (function initFromUrl() {
      var sp = new URLSearchParams(location.search);
      if (sp.get("q")) qEl.value = sp.get("q");
      if (sp.get("w") !== null) windowEl.value = sp.get("w");
      if (!windowEl.value) windowEl.value = "24";
      if (/^\d+$/.test(sp.get("p") || "")) initialPkt = parseInt(sp.get("p"), 10);
    })();

    function pushUrl() {
      var sp = new URLSearchParams();
      if (qEl.value.trim()) sp.set("q", qEl.value.trim());
      if (windowEl.value !== "24") sp.set("w", windowEl.value);
      // replaceState, not pushState: opening and closing a detail is reading,
      // not navigating, and a back button that walked back through every packet
      // somebody glanced at would never reach the previous search.
      if (openPktId) sp.set("p", String(openPktId));
      var qs = sp.toString();
      history.replaceState(null, "", location.pathname + (qs ? "?" + qs : ""));
    }

    function sinceParam() {
      var hours = parseInt(windowEl.value, 10);
      if (!hours) {
        // "alles": bound by the oldest packet actually held rather than by an
        // arbitrary epoch, so the histogram's bucket size stays proportionate.
        return archEl.dataset.oldest || "1970-01-01T00:00:00Z";
      }
      return new Date(Date.now() - hours * 3600e3).toISOString().slice(0, 19) + "Z";
    }

    function runSearch(keepOffset) {
      if (!keepOffset) archOffset = 0;
      var seq = ++archSeq;
      pushUrl();
      var url = "/api/v1/packets/search?q=" + encodeURIComponent(qEl.value.trim()) +
        "&since=" + encodeURIComponent(sinceParam()) +
        "&limit=" + PAGE_SIZE + "&offset=" + archOffset +
        "&facets=" + FACETS.join(",");
      fetch(url).then(function (r) { return r.json(); }).then(function (d) {
        if (seq !== archSeq) return;
        if (d.error) {
          errEl.textContent = d.error;
          errEl.hidden = false;
          return;
        }
        errEl.hidden = true;
        archTotal = d.total;
        renderCount(d);
        renderHistogram(d);
        renderFacets(d.facets || {});
        renderRows(d.packets || []);
        renderPager();
      }).catch(function () {
        if (seq !== archSeq) return;
        errEl.textContent = t("arch.loaderror");
        errEl.hidden = false;
      });
    }

    function renderCount(d) {
      countEl2.textContent = t(archTotal === 1 ? "arch.count_one" : "arch.count",
        { n: archTotal.toLocaleString() });
    }

    // The histogram is drawn by hand on a canvas, like the gauges on the
    // repeater page: sixty bars do not justify a chart library on a page that
    // otherwise needs none.
    function renderHistogram(d) {
      var ctx = canvasEl.getContext("2d");
      var w = canvasEl.width = canvasEl.parentNode.clientWidth - 24;
      var h = canvasEl.height;
      ctx.clearRect(0, 0, w, h);
      var bars = d.histogram || [];
      if (!bars.length) return;
      var max = 0;
      bars.forEach(function (b) { if (b.n > max) max = b.n; });
      // The axis is the searched window, not the data's own extent: a burst of
      // traffic in one bucket must show as one bar in an otherwise empty hour,
      // not as a wall of green filling the whole strip.
      var lo = Math.floor(new Date(sinceParam()).getTime() / 1000);
      var hi = Math.ceil(Date.now() / 1000);
      var span = Math.max(d.bucket_s, hi - lo);
      var bw = Math.max(2, Math.floor(w * d.bucket_s / span) - 1);
      var accent = cssVar("--accent", "#35e08c");
      ctx.fillStyle = accent;
      bars.forEach(function (b) {
        var x = Math.floor((b.t - lo) / span * w);
        var bh = Math.max(1, Math.round((h - 16) * b.n / max));
        ctx.fillRect(x, h - 14 - bh, bw, bh);
      });
      // First and last moment as labels; a full axis would repeat the window
      // picker's answer in smaller type.
      ctx.fillStyle = TEXT_MUTED;
      ctx.font = "10px 'JetBrains Mono', monospace";
      ctx.textBaseline = "bottom";
      ctx.textAlign = "left";
      ctx.fillText(fmtBucket(lo, d.bucket_s), 0, h);
      ctx.textAlign = "right";
      ctx.fillText(fmtBucket(hi, d.bucket_s), w, h);
    }

    function pad2(n) { return (n < 10 ? "0" : "") + n; }

    // "14-08 22:18:30", local time. Day-month leads because a 7-day archive
    // spans dates; the year is never in doubt inside one week.
    function fmtTs(iso) {
      var d = new Date(iso);
      return pad2(d.getDate()) + "-" + pad2(d.getMonth() + 1) + " " +
        pad2(d.getHours()) + ":" + pad2(d.getMinutes()) + ":" + pad2(d.getSeconds());
    }

    function fmtBucket(epoch, bucketS) {
      var d = new Date(epoch * 1000);
      var dm = pad2(d.getDate()) + "-" + pad2(d.getMonth() + 1);
      var hm = pad2(d.getHours()) + ":" + pad2(d.getMinutes());
      return bucketS >= 43200 ? dm : dm + " " + hm;
    }

    function renderFacets(facets) {
      facetsEl.textContent = "";
      FACETS.forEach(function (name) {
        var values = facets[name];
        if (!values || !values.length) return;
        var h4 = document.createElement("h4");
        h4.textContent = t("arch.f_" + name);
        facetsEl.appendChild(h4);
        var ul = document.createElement("ul");
        values.forEach(function (v) {
          var li = document.createElement("li");
          var a = document.createElement("a");
          a.href = "#";
          a.textContent = v.value;
          a.title = t("arch.facet_add", { q: queryClause(name, v.value, false) });
          a.addEventListener("click", function (e) {
            e.preventDefault();
            setFilter(name, v.value, false);
          });
          var n = document.createElement("span");
          n.className = "muted";
          n.textContent = v.count.toLocaleString();
          li.appendChild(a);
          li.appendChild(n);
          ul.appendChild(li);
        });
        facetsEl.appendChild(ul);
      });
      facetsEl.hidden = !facetsEl.children.length;
    }

    /* Read a query back into its clauses, the way search.py's _tokenize does.
     *
     * This exists so a plus on a value that is already excluded can flip that
     * one clause instead of appending a contradiction ("-type:ACK type:ACK"
     * matches nothing and looks like a bug in the page). Reading the query
     * requires the same rules the server parses it with, so this mirrors
     * _tokenize deliberately -- the alternative, keeping the active filters in a
     * JavaScript array beside the text box, would be a second filter mechanism
     * next to the query language, and the moment somebody edited the text by
     * hand the two would disagree about what is being searched.
     *
     * Returns null for anything it cannot read back (an unclosed quote, a
     * dangling minus). The caller then leaves the text alone and only appends:
     * the visitor already has a broken query and the server will say so.
     */
    function tokenizeQuery(text) {
      var out = [], i = 0, n = text.length;
      while (i < n) {
        if (/\s/.test(text.charAt(i))) { i += 1; continue; }
        var neg = false;
        if (text.charAt(i) === "-") {
          neg = true; i += 1;
        } else if (text.substr(i, 4).toUpperCase() === "NOT ") {
          neg = true; i += 4;
          while (i < n && /\s/.test(text.charAt(i))) i += 1;
        }
        if (i >= n) return null;
        var field = null;
        var m = /^([A-Za-z_]+):/.exec(text.slice(i));
        if (m) { field = m[1].toLowerCase(); i += m[0].length; }
        var value;
        if (text.charAt(i) === '"') {
          var q = text.indexOf('"', i + 1);
          if (q < 0) return null;
          value = text.slice(i + 1, q);
          i = q + 1;
        } else if (text.charAt(i) === "(") {
          var close = text.indexOf(")", i + 1);
          if (close < 0) return null;
          // Parentheses are kept in the value so an OR list survives a rewrite
          // untouched; nothing here is entitled to reinterpret it.
          value = text.slice(i, close + 1);
          i = close + 1;
        } else {
          var start = i;
          while (i < n && !/\s/.test(text.charAt(i))) i += 1;
          value = text.slice(start, i);
        }
        if (!value) return null;
        out.push({ neg: neg, field: field, value: value });
      }
      return out;
    }

    // Write the clauses back out. Only used when a clause actually changed, so
    // a query nobody touched keeps the spacing its author gave it. "NOT x" does
    // come back as "-x" -- the parser treats them as one and the same, and
    // remembering which spelling was typed would be bookkeeping for nothing.
    function renderQuery(tokens) {
      return tokens.map(function (tok) {
        var v = tok.value;
        var grouped = v.charAt(0) === "(" && v.charAt(v.length - 1) === ")";
        return (tok.neg ? "-" : "") + (tok.field ? tok.field + ":" : "") +
          (grouped ? v : queryValue(v));
      }).join(" ");
    }

    /* Add (or flip) one field:value clause and search again.
     *
     * Refining rather than replacing is the whole point of the facets and of the
     * plus/minus buttons. Three cases, all of them things a visitor does:
     *   - the very same clause is already there: nothing happens, so clicking
     *     the same value twice is not two identical clauses;
     *   - the opposite clause is there: it is flipped in place, because a plus
     *     on something you excluded means "actually, only this", not "both";
     *   - anything else: appended, which is an AND, which is what a space means.
     * Comparison is case-insensitive because the text fields are matched with
     * COLLATE NOCASE, so "ADVERT" and "advert" really are the same clause.
     */
    function setFilter(field, value, negate) {
      var v = String(value).replace(/"/g, "");
      if (!v) return;
      var current = qEl.value.trim();
      var clause = queryClause(field, v, negate);
      var tokens = tokenizeQuery(current);
      if (tokens) {
        for (var i = 0; i < tokens.length; i++) {
          var tok = tokens[i];
          if (tok.field !== field) continue;
          if (tok.value.toLowerCase() !== v.toLowerCase()) continue;
          if (tok.neg === negate) return;    // already exactly what was asked
          tok.neg = negate;
          qEl.value = renderQuery(tokens);
          runSearch(false);
          return;
        }
      }
      qEl.value = current ? current + " " + clause : clause;
      runSearch(false);
    }

    function archRow(p) {
      var li = document.createElement("li");
      li.dataset.id = p.id;
      li.tabIndex = 0;
      var dot = document.createElement("i");
      dot.style.background = PKT_COLORS[p.type] || "#7d8fa0";
      li.appendChild(dot);
      // Absolute time, not relative: the archive exists to pin down when
      // something happened, and "3 uur geleden" defeats that. Compact 24-hour
      // form rather than toLocaleString: the locale's full rendering runs to
      // 22 characters and squeezes the sender out of its own column.
      var when = document.createElement("time");
      when.className = "pkt-time-abs";
      when.dateTime = p.ts;
      when.textContent = fmtTs(p.ts);
      li.appendChild(when);
      var who = cell2("pkt-who", p.sender_name || (p.sender || "").toUpperCase() || "");
      if (!who.textContent) {
        var lbl = srcLabel(p.src, t);
        who.textContent = lbl ? lbl.text : t("pkt.sender_short");
        if (lbl) { who.title = lbl.title; who.classList.add("src-derived"); }
      }
      // Only a sender an advert stated has a key in the sender column; a sender
      // merely derived from the 1-byte hash gets no buttons, for the same reason
      // the detail panel withholds them there.
      filterBtns(who, "sender", p.sender, setFilter);
      li.appendChild(who);
      li.appendChild(cell2("pkt-obs", p.observer_name ||
        (p.observer || "").slice(0, 6).toUpperCase() || "—", "observer", p.observer));
      li.appendChild(cell2("pkt-type", p.type || "?", "type", p.type));
      var scope = document.createElement("span");
      scope.className = "pkt-scope";
      scope.textContent = p.scope ? t("scope." + p.scope) : "—";
      if (p.scope_region) scope.textContent += " · " + p.scope_region;
      filterBtns(scope, "scope", p.scope, setFilter);
      li.appendChild(scope);
      li.appendChild(cell2("pkt-snr", p.snr != null ? p.snr.toFixed(1) + " dB" : "—",
        "snr", p.snr));
      li.appendChild(cell2("pkt-rssi", p.rssi != null ? p.rssi + " dBm" : "—",
        "rssi", p.rssi));
      li.appendChild(cell2("pkt-hops", p.path_len != null ? String(p.path_len) : "—",
        "hops", p.path_len));
      li.appendChild(cell2("pkt-len", p.len != null ? p.len + " B" : "—", "len", p.len));
      li.appendChild(cell2("pkt-cc", p.country || "—", "country", p.country));
      li.addEventListener("click", function () { openPacketModal(p.id); });
      li.addEventListener("keydown", function (e) {
        if (e.key !== "Enter" && e.key !== " ") return;
        e.preventDefault();
        openPacketModal(p.id);
      });
      return li;
    }

    // The column value carries the buttons rather than a separate control
    // column: what you want to filter on is the value you are looking at, and a
    // row of nine button pairs would be a toolbar, not a result.
    function cell2(cls, text, field, value) {
      var el = document.createElement("span");
      el.className = cls;
      el.textContent = text;
      filterBtns(el, field, value, setFilter);
      return el;
    }

    function renderRows(rows) {
      listEl.textContent = "";
      rows.forEach(function (p) { listEl.appendChild(archRow(p)); });
      emptyEl.hidden = rows.length > 0;
      // Filtering from inside the open detail re-runs the search underneath it,
      // so the row it belongs to has to be marked again on the new list.
      if (openPktId) markOpenRow(openPktId);
    }

    function renderPager() {
      var from = archTotal ? archOffset + 1 : 0;
      var to = Math.min(archOffset + PAGE_SIZE, archTotal);
      pageEl.textContent = t("arch.page", { from: from, to: to,
        total: archTotal.toLocaleString() });
      prevEl.disabled = archOffset <= 0;
      nextEl.disabled = to >= archTotal;
    }

    // --- one packet, in full -------------------------------------------------
    // Until now a click on a row opened the raw JSON of the API in a new tab,
    // which answered the question but asked the reader to parse a packet by eye.
    // The live page already had a panel that reads properly, so this shows that
    // same fragment; nothing new is fetched that the API did not already serve.
    function openPacketModal(id) {
      if (!pktModalEl || !id) return;
      openPktId = id;
      blankPacketDetail();
      pktModalEl.hidden = false;
      document.body.style.overflow = "hidden";   // same as the history modal
      var panelEl = pktModalEl.querySelector(".modal-panel");
      if (panelEl) panelEl.scrollTop = 0;
      var closeBtn = document.getElementById("pkt-close");
      if (closeBtn) closeBtn.focus();
      markOpenRow(id);
      pushUrl();
      fetch("/api/v1/packets/" + encodeURIComponent(id))
        .then(function (r) {
          if (!r.ok) throw new Error("HTTP " + r.status);
          return r.json();
        })
        .then(function (d) {
          if (openPktId !== id) return;   // a second click already took over
          // The archive always has something to say about countries: it has a
          // Land column and a country: field whatever the deployment looks like.
          fillPacketDetail(d, { showCountry: true, onFilter: setFilter });
        })
        .catch(function () {
          if (openPktId === id) txt("pkt-path-note", t("pkt.loaderror"));
        });
    }

    function closePacketModal() {
      if (!pktModalEl || pktModalEl.hidden) return;
      pktModalEl.hidden = true;
      document.body.style.overflow = "";
      openPktId = null;
      markOpenRow(-1);
      pushUrl();
    }

    function markOpenRow(id) {
      Array.prototype.forEach.call(listEl.children, function (li) {
        li.classList.toggle("selected", parseInt(li.dataset.id, 10) === id);
      });
    }

    if (pktModalEl) {
      document.getElementById("pkt-close").addEventListener("click", closePacketModal);
      pktModalEl.querySelector(".modal-backdrop")
        .addEventListener("click", closePacketModal);
      document.addEventListener("keydown", function (e) {
        if (e.key === "Escape" && !pktModalEl.hidden) closePacketModal();
      });
    }

    prevEl.addEventListener("click", function () {
      archOffset = Math.max(0, archOffset - PAGE_SIZE);
      runSearch(true);
    });
    nextEl.addEventListener("click", function () {
      archOffset += PAGE_SIZE;
      runSearch(true);
    });
    document.getElementById("arch-form").addEventListener("submit", function (e) {
      e.preventDefault();
      runSearch(false);
    });
    windowEl.addEventListener("change", function () { runSearch(false); });

    runSearch(false);
    // A link that names a packet opens it straight away, without waiting for the
    // list around it: the detail comes from its own endpoint, and somebody who
    // was sent that link came for the packet, not for the search behind it.
    if (initialPkt) openPacketModal(initialPkt);
  }
})();
