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

  var livemapEl = document.getElementById("livemap");
  if (livemapEl && typeof L !== "undefined") {
    var lmap = L.map(livemapEl, { scrollWheelZoom: false });
    L.tileLayer(TILE_URL, { attribution: "&copy; OpenStreetMap &copy; CARTO", maxZoom: 19 })
      .addTo(lmap);
    var feedEl = document.getElementById("livefeed");
    var countEl = document.getElementById("live-count");
    var lastId = 0;
    var seenTimes = [];   // reception timestamps, for the "per minute" counter
    var polling = false;

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

    function feedRow(p) {
      var li = document.createElement("li");
      var who = p.sender_name || p.observer_name ||
                (p.sender || p.observer || "").toUpperCase();
      var bits = [p.type || "?"];
      if (p.snr != null) bits.push(p.snr.toFixed(1) + " dB");
      if (p.path_len) {
        bits.push(t(p.path_len > 1 ? "live.hops_plural" : "live.hops", { n: p.path_len }));
      }
      li.innerHTML = '<i style="background:' + (PKT_COLORS[p.type] || "#7d8fa0") + '"></i>' +
        '<span class="pkt-who"></span><span class="pkt-meta"></span>' +
        '<time class="reltime" datetime="' + p.ts + '"></time>';
      li.querySelector(".pkt-who").textContent = who;
      li.querySelector(".pkt-meta").textContent = bits.join(" · ");
      return li;
    }

    function render(list, animate) {
      var flashes = 0;
      list.forEach(function (p) {
        if (p.lat != null && p.lon != null && animate && flashes < FLASH_MAX_PER_POLL) {
          flash(p.lat, p.lon, PKT_COLORS[p.type] || "#7d8fa0");
          flashes++;
        }
        seenTimes.push(Date.now());
        if (feedEl) {
          feedEl.insertBefore(feedRow(p), feedEl.firstChild);
          while (feedEl.children.length > FEED_MAX) feedEl.removeChild(feedEl.lastChild);
        }
      });
      updateTimes();
      updateCount();
    }

    function updateCount() {
      if (!countEl) return;
      var cutoff = Date.now() - 300000;
      seenTimes = seenTimes.filter(function (t) { return t >= cutoff; });
      countEl.textContent = seenTimes.length
        ? t("live.count", { n: seenTimes.length })
        : t("live.waiting");
    }

    function poll(first) {
      if (polling) return;
      polling = true;
      fetch("/api/v1/packets?since_id=" + lastId)
        .then(function (r) { return r.json(); })
        .then(function (d) {
          if (first && d.nodes) {
            var bounds = [];
            d.nodes.forEach(function (n) {
              L.circleMarker([n.lat, n.lon], {
                radius: 4, color: "#7d8fa0", weight: 1, fillColor: "#7d8fa0",
                fillOpacity: 0.5,
              }).addTo(lmap).bindTooltip(n.name || n.prefix.toUpperCase(), { direction: "top" });
              bounds.push([n.lat, n.lon]);
            });
            if (bounds.length) lmap.fitBounds(bounds, { padding: [40, 40], maxZoom: 12 });
          }
          lastId = d.last_id || lastId;
          // The first response is backlog, not live traffic: list it, but do not
          // set off a firework of flashes for packets heard hours ago.
          render(d.packets || [], !first);
        })
        .catch(function () { /* next tick tries again */ })
        .then(function () { polling = false; });
    }

    poll(true);
    setInterval(function () {
      // No point polling a tab nobody is looking at; the backlog is still there
      // when it comes back, since the server hands out everything after lastId.
      if (!document.hidden) poll(false);
    }, POLL_MS);
    document.addEventListener("visibilitychange", function () {
      if (!document.hidden) poll(false);   // catch up at once instead of after a tick
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
})();
