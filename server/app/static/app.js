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

    function feedRow(p, quiet) {
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
      li.dataset.id = p.id;
      li.tabIndex = 0;
      li.setAttribute("role", "button");
      if (openId === p.id) li.classList.add("selected");
      // A whole-list re-render (filter change) must not replay the arrival
      // animation on rows that did not just arrive.
      if (quiet) li.style.animation = "none";
      return li;
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

    // One haystack for name, prefix, payload type and country: typing "adv",
    // "2ae7", "be" or part of a node name all mean the same thing to a visitor
    // -- "show me the packets that mention this".
    function matches(p) {
      if (filterCountry) {
        // "??" is a visitor asking for the packets we could not place. That is a
        // real answer about the mesh, not the absence of a filter.
        var want = filterCountry === "??" ? null : filterCountry;
        if ((p.country || null) !== want) return false;
      }
      var q = filterText.trim().toLowerCase();
      if (!q) return true;
      return [p.sender_name, p.observer_name, p.sender, p.observer, p.type, p.country]
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
    // stay put. The open detail panel is left alone entirely -- its path was
    // framed on purpose when the packet was opened.
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

    function renderFeed(refit) {
      if (feedEl) {
        feedEl.textContent = "";
        for (var i = 0, shown = 0; i < recent.length && shown < FEED_MAX; i++) {
          if (!matches(recent[i])) continue;
          feedEl.appendChild(feedRow(recent[i], true));
          shown++;
        }
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
        // The filter governs the map as well as the list: a visitor watching one
        // node should not have the rest of the mesh moving over their map.
        var show = animate && matches(p) && flashes < FLASH_MAX_PER_POLL;
        if (show) {
          var color = PKT_COLORS[p.type] || "#7d8fa0";
          // A packet with a route travels it; one we cannot place a route for
          // falls back to the flash, which needs only the single position.
          var moved = animating() && travel(p, color);
          if (!moved && p.lat != null && p.lon != null) flash(p.lat, p.lon, color);
          flashes++;
        }
        // Reception time comes from the packet, so a backlog is dated when it
        // was heard; only a packet without a usable timestamp falls back to now.
        var at = Date.parse(p.ts);
        seen.push({ t: isNaN(at) ? Date.now() : at, p: p });
        recent.unshift(p);
        if (feedEl && matches(p)) feedEl.insertBefore(feedRow(p), feedEl.firstChild);
      });
      if (recent.length > FEED_BUFFER) recent.length = FEED_BUFFER;
      if (feedEl) {
        while (feedEl.children.length > FEED_MAX) feedEl.removeChild(feedEl.lastChild);
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
          // The first response is backlog, not live traffic: list it, but do not
          // set off a firework of flashes for packets heard hours ago.
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

    function txt(id, value) {
      var el = document.getElementById(id);
      if (el) el.textContent = value;
    }

    function nodeLabel(prefix, name) {
      var p = (prefix || "").toUpperCase();
      if (!p && !name) return "";
      return name ? name + (p ? " (" + p + ")" : "") : p;
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

    function panelOpen() { return !!panel && !panel.hidden; }

    function clearPath() {
      if (pathLayer) { lmap.removeLayer(pathLayer); pathLayer = null; }
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
      if (view.length > 1) {
        // Keep the line clear of the detail panel, which docks to the right on a
        // wide screen and along the bottom on a narrow one.
        var wide = window.innerWidth > 820;
        lmap.fitBounds(view, {
          paddingTopLeft: [30, 30],
          paddingBottomRight: wide ? [Math.min(430, window.innerWidth * 0.4), 30] : [30, 120],
          maxZoom: 13,
        });
      }
      var box = livemapEl.getBoundingClientRect();
      if (box.top < 60 || box.bottom > window.innerHeight) {
        livemapEl.scrollIntoView({ behavior: "smooth", block: "start" });
      }
    }

    function fillPanel(d) {
      txt("pkt-time", new Date(d.ts).toLocaleString() + " · " + relTime(d.ts));
      txt("pkt-sender", nodeLabel(d.sender, d.sender_name) || t("pkt.sender_unknown"));
      txt("pkt-observer", nodeLabel(d.observer, d.observer_name) || "—");
      // Whose country to show follows whose position the map used for this
      // packet: the sender's when we know it, the observer's otherwise.
      var countryRow = document.getElementById("pkt-country-row");
      var placed = d.sender_lat != null && d.sender_lon != null;
      var cc = placed ? d.sender_country : d.observer_country;
      countryRow.hidden = !countryEl || countryEl.hidden;
      txt("pkt-country-val", countryLabel(cc) +
          " · " + t(placed ? "pkt.country_of_sender" : "pkt.country_of_observer"));
      txt("pkt-type", d.type || "—");
      txt("pkt-route", d.route || "—");
      txt("pkt-snr", d.snr != null ? d.snr.toFixed(2) + " dB" : "—");
      txt("pkt-rssi", d.rssi != null ? d.rssi + " dBm" : "—");
      txt("pkt-len", d.len != null ? d.len + " B" : "—");
      txt("pkt-pathlen", d.path_len != null ? String(d.path_len) : "—");
      // Hex in byte pairs so it stays readable, and it wraps rather than
      // widening the page on a phone (see .pktraw).
      txt("pkt-raw", d.raw ? d.raw.toUpperCase().replace(/../g, "$& ").trim() : t("pkt.noraw"));

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

    function blankPanel() {
      ["pkt-time", "pkt-sender", "pkt-observer", "pkt-type", "pkt-route", "pkt-snr",
       "pkt-rssi", "pkt-len", "pkt-pathlen", "pkt-raw", "pkt-path-note"].forEach(function (id) {
        txt(id, "");
      });
      document.getElementById("pkt-path").textContent = "";
      document.getElementById("pkt-advert").hidden = true;
    }

    function openPacket(id) {
      if (!panel || !id) return;
      openId = id;
      blankPanel();
      panel.hidden = false;
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
})();
