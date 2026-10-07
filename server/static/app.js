/* Kaartpagina: trackers live, zones (geofences), simulator. Volledig offline. */
(async function () {
  const $ = (id) => document.getElementById(id);
  const dark = { get matches() { return MT.isDark(); } };
  await MT.initHeader("/");
  const can = MT.can;
  const kiosk = !can("map.sidebar");
  if (kiosk) document.body.classList.add("kiosk");
  if (!can("zones.view")) document.querySelector('.tab[data-tab="zones"]').hidden = true;
  if (!can("sims.manage")) document.querySelector('.tab[data-tab="sim"]').hidden = true;
  // Eigen zones mag iedereen met "zones bekijken"; gedeelde enkel met "zones beheren".
  if (MT.me.kind !== "user" && !can("zones.manage")) { $("z-circle").hidden = true; $("z-poly").hidden = true; }
  if (!can("map.nodes")) document.querySelector('details[data-sect="nodes"]').hidden = true;
  if (!can("map.tracks")) document.querySelector('details[data-sect="track"]').hidden = true;
  const status = await MT.api("/api/status");
  MT.meshPill($("mesh"), status.mesh);

  const map = new maplibregl.Map({
    container: "map",
    style: MTBasemap.style(dark.matches, status.tiles),
    center: status.map.center,
    zoom: status.map.zoom,
    attributionControl: { compact: true },
  });
  map.addControl(new maplibregl.NavigationControl({ showCompass: false }), "top-right");
  map.addControl(new maplibregl.ScaleControl({ unit: "metric" }), "bottom-right");

  const trackers = new Map();   // id -> tracker
  const tracks = new Map();     // id -> [{lon,lat,spd,ts,state,bat}]
  const sosPts = new Map();     // id -> SOS-posities in de gekozen periode (min. 24 u), los van het spoor
  let sosMarkers = [];
  const markers = new Map();    // id -> maplibregl.Marker
  let zones = [];
  let simRoutes = {};
  let selected = null;
  let following = null;              // tracker-id die de kaart volgt

  // Per-browser voorkeuren. localStorage kan ontbreken (privévenster): dan
  // gewoon met standaardwaarden werken.
  const store = { get: (k, d) => MT.prefGet(k, d), set: (k, v) => MT.prefSet(k, v) };
  const prefs = {
    fav: new Set(store.get("fav", [])),
    hidden: new Set(store.get("hidden", [])),
    filter: store.get("filter", "all"),
    tgroup: Number(new URLSearchParams(location.search).get("tgroup")) || store.get("tgroup", 0),   // 0 = alle
    trackOn: store.get("trackOn", true),
    hours: store.get("hours", 24),
    color: store.get("color", "tracker"),
    ntypes: new Set(store.get("ntypes", [2])),
    nrecent: store.get("nrecent", true),
  };
  const savePrefs = () => {
    store.set("fav", [...prefs.fav]); store.set("hidden", [...prefs.hidden]); store.set("filter", prefs.filter); store.set("tgroup", prefs.tgroup);
    store.set("trackOn", prefs.trackOn); store.set("hours", prefs.hours); store.set("color", prefs.color);
    store.set("ntypes", [...prefs.ntypes]); store.set("nrecent", prefs.nrecent);
  };
  const onMap = (t) => t.active && !prefs.hidden.has(t.id);
  const hoursNow = () => (prefs.trackOn ? prefs.hours : 0);
  const colorSel = { get value() { return prefs.color; } };

  function setSeg(el, attr, val) {
    el.querySelectorAll("button").forEach((b) => b.classList.toggle("on", b.dataset[attr] === String(val)));
  }
  setSeg($("hours"), "h", prefs.hours);
  setSeg($("colormode"), "c", prefs.color);
  $("trackon").checked = prefs.trackOn;
  $("speedlegend").hidden = prefs.color !== "speed";
  document.querySelectorAll(".chip").forEach((c) => c.classList.toggle("on", c.dataset.filter === prefs.filter));

  // ---- hulpjes -------------------------------------------------------------------
  const speedColor = ["interpolate", ["linear"], ["get", "spd"], 0, "#9ca3af", 2, "#22c55e", 15, "#22c55e",
    30, "#eab308", 60, "#f97316", 90, "#dc2626"];

  function circlePoly(lon, lat, r, n = 64) {
    const pts = [];
    for (let i = 0; i <= n; i++) {
      const a = (i / n) * 2 * Math.PI;
      const dy = (r * Math.cos(a)) / 111320;
      const dx = (r * Math.sin(a)) / (111320 * Math.cos((lat * Math.PI) / 180));
      pts.push([lon + dx, lat + dy]);
    }
    return pts;
  }

  function dist(a, b) {
    const R = 6371008.8, p1 = (a.lat * Math.PI) / 180, p2 = (b.lat * Math.PI) / 180;
    const dp = p2 - p1, dl = ((b.lon - a.lon) * Math.PI) / 180;
    const h = Math.sin(dp / 2) ** 2 + Math.cos(p1) * Math.cos(p2) * Math.sin(dl / 2) ** 2;
    return 2 * R * Math.asin(Math.min(1, Math.sqrt(h)));
  }

  function toast(text, cls) {
    const el = document.createElement("div");
    el.className = "toast " + (cls || "");
    el.textContent = text;
    $("toasts").appendChild(el);
    setTimeout(() => el.remove(), 8000);
  }

  // ---- tabs ------------------------------------------------------------------------
  document.querySelectorAll(".tab").forEach((b) => b.addEventListener("click", () => {
    document.querySelectorAll(".tab").forEach((x) => x.classList.toggle("on", x === b));
    document.querySelectorAll(".pane").forEach((p) => { p.hidden = p.id !== "pane-" + b.dataset.tab; });
    if (b.dataset.tab === "sim") refreshSims();
    if (b.dataset.tab === "zones") refreshEvents();
  }));

  // ---- kaartlagen --------------------------------------------------------------------
  function trackFeatures() {
    const lines = [], points = [];
    const bySpeed = colorSel.value === "speed";
    for (const [id, pts] of tracks) {
      const t = trackers.get(id);
      if (!t || !onMap(t) || pts.length === 0) continue;
      const sel = id === selected ? 1 : 0;
      if (bySpeed) {
        for (let i = 1; i < pts.length; i++) {
          lines.push({ type: "Feature", properties: { spd: ((pts[i - 1].spd || 0) + (pts[i].spd || 0)) / 2, sel, color: t.color },
            geometry: { type: "LineString", coordinates: [[pts[i - 1].lon, pts[i - 1].lat], [pts[i].lon, pts[i].lat]] } });
        }
      } else if (pts.length > 1) {
        lines.push({ type: "Feature", properties: { color: t.color, sel, spd: 0 },
          geometry: { type: "LineString", coordinates: pts.map((p) => [p.lon, p.lat]) } });
      }
      for (const p of pts) {
        points.push({ type: "Feature", properties: { tid: id, alias: t.alias, color: t.color, spd: p.spd ?? -1, ts: p.ts, state: p.state, bat: p.bat ?? -1 },
          geometry: { type: "Point", coordinates: [p.lon, p.lat] } });
      }
    }
    return { lines: { type: "FeatureCollection", features: lines }, points: { type: "FeatureCollection", features: points } };
  }

  function zoneFeatures() {
    return { type: "FeatureCollection", features: zones.filter((z) => z.active).map((z) => ({
      type: "Feature", properties: { id: z.id, name: z.name, color: z.color },
      geometry: { type: "Polygon", coordinates: [z.kind === "circle" ? circlePoly(z.geom.center[0], z.geom.center[1], z.geom.radius) : z.geom] },
    })) };
  }

  let meshNodes = [];
  const NTYPE = { 1: "companion", 2: "repeater", 3: "room", 4: "sensor" };
  function nodeFeatures() {
    const cutoff = Date.now() / 1000 - 7 * 86400;
    return { type: "FeatureCollection", features: meshNodes
      .filter((n) => prefs.ntypes.has(n.type) && (!prefs.nrecent || (n.last_advert || 0) >= cutoff))
      .map((n) => ({ type: "Feature", properties: { name: n.name, type: n.type, last: n.last_advert || 0, hops: n.hops ?? -1,
          direct: n.direct ? 1 : 0, rssi: n.rssi ?? null, snr: n.snr ?? null, adverts: n.adverts ?? null },
        geometry: { type: "Point", coordinates: [n.lon, n.lat] } })) };
  }
  async function loadNodes() {
    if (!can("map.nodes") || !prefs.ntypes.size) { meshNodes = []; refreshLayers(); return; }
    try { meshNodes = await MT.api("/api/mesh/nodes"); } catch (_) { meshNodes = []; }
    refreshLayers();
  }
  document.querySelectorAll("[data-ntype]").forEach((cb) => {
    cb.checked = prefs.ntypes.has(Number(cb.dataset.ntype));
    cb.addEventListener("change", () => {
      const t = Number(cb.dataset.ntype);
      if (cb.checked) prefs.ntypes.add(t); else prefs.ntypes.delete(t);
      savePrefs(); loadNodes();
    });
  });
  $("nodes-recent").checked = prefs.nrecent;
  $("nodes-recent").addEventListener("change", () => { prefs.nrecent = $("nodes-recent").checked; savePrefs(); refreshLayers(); });
  map.on("click", "nodes", (e) => {
    if (drawing) return;
    const p = e.features[0].properties;
    const hops = p.hops >= 0 && p.hops < 64 ? ` · ${p.hops === 0 ? "rechtstreeks" : p.hops + " hops"} tot de server` : "";
    const direct = p.direct ? `<br><span style="color:#22c55e">directe buur van de server</span>` : "";
    const sig = p.rssi != null && p.rssi !== "null" ? `<br>RSSI ${p.rssi} dBm · SNR ${p.snr} dB` : "";
    const cnt = p.adverts != null && p.adverts !== "null" ? ` · ${p.adverts} adverts` : "";
    new maplibregl.Popup({ closeButton: false }).setLngLat(e.lngLat)
      .setHTML(`<strong>${MT.esc(p.name)}</strong><br>${NTYPE[p.type] || "node"}${hops}${direct}${sig}<br>laatste advert ${MT.ago(p.last)}${cnt}`).addTo(map);
  });
  map.on("mouseenter", "nodes", () => { if (!drawing) map.getCanvas().style.cursor = "pointer"; });
  map.on("mouseleave", "nodes", () => { if (!drawing) map.getCanvas().style.cursor = ""; });
  setInterval(loadNodes, 5 * 60 * 1000);

  function simRouteFeatures() {
    return { type: "FeatureCollection", features: Object.entries(simRoutes).map(([tid, coords]) => ({
      type: "Feature", properties: { color: (trackers.get(Number(tid)) || {}).color || "#7c3aed" },
      geometry: { type: "LineString", coordinates: coords },
    })) };
  }

  function addLayers() {
    if (map.getSource("tracks")) return;
    const f = trackFeatures();
    map.addSource("zones", { type: "geojson", data: zoneFeatures() });
    map.addLayer({ id: "zones-fill", type: "fill", source: "zones", paint: { "fill-color": ["get", "color"], "fill-opacity": 0.12 } });
    map.addLayer({ id: "zones-line", type: "line", source: "zones", paint: { "line-color": ["get", "color"], "line-width": 2, "line-dasharray": [2, 1] } });
    map.addLayer({ id: "zones-label", type: "symbol", source: "zones",
      layout: { "text-field": ["get", "name"], "text-font": ["Noto Sans Medium"], "text-size": 12 },
      paint: { "text-color": ["get", "color"], "text-halo-color": dark.matches ? "#000" : "#fff", "text-halo-width": 1.5 } });
    map.addSource("nodes", { type: "geojson", data: nodeFeatures() });
    map.addLayer({ id: "nodes", type: "circle", source: "nodes",
      paint: { "circle-radius": ["interpolate", ["linear"], ["zoom"], 7, 2.5, 12, 5, 15, 7],
               "circle-color": ["match", ["get", "type"], 2, "#0ea5e9", 3, "#a855f7", 4, "#f59e0b", "#64748b"],
               "circle-stroke-color": ["case", ["==", ["get", "direct"], 1], "#22c55e", dark.matches ? "#000" : "#fff"],
               "circle-stroke-width": ["case", ["==", ["get", "direct"], 1], 2.5, 1], "circle-opacity": 0.9 } });
    map.addLayer({ id: "nodes-label", type: "symbol", source: "nodes", minzoom: 11,
      layout: { "text-field": ["get", "name"], "text-font": ["Noto Sans Regular"], "text-size": 11, "text-offset": [0, 1.1], "text-anchor": "top" },
      paint: { "text-color": dark.matches ? "#cbd5e1" : "#334155", "text-halo-color": dark.matches ? "#000" : "#fff", "text-halo-width": 1.2 } });
    map.addSource("simroutes", { type: "geojson", data: simRouteFeatures() });
    map.addLayer({ id: "simroutes", type: "line", source: "simroutes",
      paint: { "line-color": ["get", "color"], "line-width": 2, "line-opacity": 0.55, "line-dasharray": [1, 2] } });
    map.addSource("tracks", { type: "geojson", data: f.lines });
    map.addLayer({ id: "tracks-casing", type: "line", source: "tracks", layout: { "line-join": "round", "line-cap": "round" },
      paint: { "line-color": dark.matches ? "#000" : "#fff", "line-width": ["case", ["==", ["get", "sel"], 1], 7, 5], "line-opacity": 0.7 } });
    map.addLayer({ id: "tracks", type: "line", source: "tracks", layout: { "line-join": "round", "line-cap": "round" },
      paint: { "line-color": colorSel.value === "speed" ? speedColor : ["get", "color"], "line-width": ["case", ["==", ["get", "sel"], 1], 4, 2.5] } });
    map.addSource("points", { type: "geojson", data: f.points });
    map.addLayer({ id: "points", type: "circle", source: "points", minzoom: 12,
      paint: { "circle-radius": ["interpolate", ["linear"], ["zoom"], 12, 2, 16, 5],
               "circle-color": colorSel.value === "speed" ? speedColor : ["get", "color"],
               "circle-stroke-color": dark.matches ? "#000" : "#fff", "circle-stroke-width": 1 } });
    map.addSource("draw", { type: "geojson", data: { type: "FeatureCollection", features: [] } });
    map.addLayer({ id: "draw-fill", type: "fill", source: "draw", paint: { "fill-color": "#3b82f6", "fill-opacity": 0.15 } });
    map.addLayer({ id: "draw-line", type: "line", source: "draw", paint: { "line-color": "#3b82f6", "line-width": 2 } });
    map.addLayer({ id: "draw-pts", type: "circle", source: "draw", filter: ["==", "$type", "Point"],
      paint: { "circle-radius": 5, "circle-color": "#fff", "circle-stroke-color": "#3b82f6", "circle-stroke-width": 2 } });
  }

  function refreshLayers() {
    renderSos();
    const f = trackFeatures();
    if (!map.getSource("tracks")) return;
    map.getSource("tracks").setData(f.lines);
    map.getSource("points").setData(f.points);
    map.getSource("zones").setData(zoneFeatures());
    map.getSource("simroutes").setData(simRouteFeatures());
    map.getSource("nodes").setData(nodeFeatures());
    const c = colorSel.value === "speed" ? speedColor : ["get", "color"];
    map.setPaintProperty("tracks", "line-color", c);
    map.setPaintProperty("points", "circle-color", c);
  }

  map.on("click", "points", (e) => {
    if (drawing) return;
    const p = e.features[0].properties;
    const lines = [`<strong>${MT.esc(p.alias)}</strong>`, new Date(p.ts * 1000).toLocaleString("nl-BE"),
      MT.esc(MT.STATE[p.state] || p.state), p.spd >= 0 ? `${p.spd} km/u` : null, p.bat >= 0 ? `batterij ${p.bat}%` : null];
    new maplibregl.Popup({ closeButton: false }).setLngLat(e.lngLat).setHTML(lines.filter(Boolean).join("<br>")).addTo(map);
  });
  map.on("mouseenter", "points", () => { if (!drawing) map.getCanvas().style.cursor = "pointer"; });
  map.on("mouseleave", "points", () => { if (!drawing) map.getCanvas().style.cursor = ""; });

  // ---- trackers --------------------------------------------------------------------
  async function loadTrack(id) {
    if (!can("map.tracks")) { tracks.set(id, []); sosPts.set(id, []); return; }
    // SOS-markers ook zonder spoor: dan de laatste 24 uur.
    const h = hoursNow() || 24;
    const pts = (await MT.api(`/api/trackers/${id}/track?hours=${h}`)).filter((p) => !p.suspect);
    tracks.set(id, hoursNow() ? pts : []);
    sosPts.set(id, pts.filter((p) => p.state === "E"));
  }

  // SOS: rode cirkel met "SOS". Herhalingen van dezelfde noodoproep (binnen 5 min en 150 m) tellen één keer.
  function renderSos() {
    sosMarkers.forEach((m) => m.remove());
    sosMarkers = [];
    for (const [id, pts] of sosPts) {
      const t = trackers.get(id);
      if (!t || !onMap(t)) continue;
      let prev = null;
      for (const p of [...pts].sort((a, b) => a.ts - b.ts)) {
        if (prev && p.ts - prev.ts < 300 && dist(prev, p) < 150) continue;
        prev = p;
        const el = document.createElement("div");
        el.className = "sosmark";
        el.textContent = "SOS";
        el.title = `${t.alias}: SOS`;
        const html = `<strong>${MT.esc(t.alias)}</strong> <span class="pill sos">SOS</span><br>${new Date(p.ts * 1000).toLocaleString("nl-BE")}`
          + `<br><span class="mono">${p.lat.toFixed(5)}, ${p.lon.toFixed(5)}</span>`;
        sosMarkers.push(new maplibregl.Marker({ element: el }).setLngLat([p.lon, p.lat])
          .setPopup(new maplibregl.Popup({ offset: 18, closeButton: false }).setHTML(html)).addTo(map));
      }
    }
  }

  function popupHtml(t) {
    const st = MT.STATE[t.last_state] || t.last_state || "–";
    return [
      `<strong>${MT.esc(t.alias)}</strong>${t.kind === "sim" ? ' <span class="pill">virtueel</span>' : ""}${t.lost ? ' <span class="lostbadge">VERLOREN</span>' : ""}`,
      `${MT.esc(st)}${t.last_mode ? " · " + MT.esc(MT.MODE[t.last_mode] || t.last_mode) : ""}`,
      t.last_spd != null ? `${t.last_spd} km/u${t.last_crs != null ? " · koers " + t.last_crs + "°" : ""}` : null,
      t.last_bat != null ? `batterij ${t.last_bat}%` : null,
      `laatst gehoord ${MT.ago(t.last_rx)}`,
      t.last_snr != null ? `SNR ${t.last_snr} dB${t.last_path_len != null && t.last_path_len < 64 ? ", " + t.last_path_len + " hops" : ""}` : null,
      t.last_lat != null ? `<span class="mono">${t.last_lat.toFixed(5)}, ${t.last_lon.toFixed(5)}</span>` : null,
    ].filter(Boolean).join("<br>");
  }

  function upsertMarker(t) {
    let m = markers.get(t.id);
    if (t.last_lat == null || !onMap(t)) {
      if (m) { m.remove(); markers.delete(t.id); }
      return;
    }
    if (!m) {
      const el = document.createElement("div");
      el.className = "marker";
      el.innerHTML = '<span class="arrow"></span><span class="ico"></span><span class="mlabel"></span>';
      el.addEventListener("click", (e) => { e.stopPropagation(); if (!drawing) select(t.id, false); });
      m = new maplibregl.Marker({ element: el }).setLngLat([t.last_lon, t.last_lat])
        .setPopup(new maplibregl.Popup({ offset: 16, closeButton: false })).addTo(map);
      markers.set(t.id, m);
    }
    const el = m.getElement();
    el.style.background = t.color;
    el.classList.toggle("stale", t.stale);
    el.classList.toggle("sos", t.last_state === "E");
    el.classList.toggle("lost", !!t.lost);
    const moving = t.last_crs != null && (t.last_spd || 0) >= 3;
    el.classList.toggle("has-crs", moving);
    if (moving) el.querySelector(".arrow").style.transform = `rotate(${t.last_crs}deg)`;
    const ico = t.icon ? MTIcons.svg(t.icon) : "";
    el.classList.toggle("big", !!ico);
    el.querySelector(".ico").innerHTML = ico;
    el.querySelector(".mlabel").textContent = t.alias;
    m.setLngLat([t.last_lon, t.last_lat]);
    m.getPopup().setHTML(popupHtml(t));
  }

  function statsFor(id) {
    const pts = tracks.get(id) || [];
    if (pts.length < 2) return "";
    let d = 0, max = 0, sum = 0, n = 0;
    for (let i = 1; i < pts.length; i++) d += dist(pts[i - 1], pts[i]);
    for (const p of pts) if (p.spd != null) { max = Math.max(max, p.spd); if (p.spd >= 3) { sum += p.spd; n++; } }
    return `${(d / 1000).toFixed(1)} km · max ${max} km/u · gem. ${n ? Math.round(sum / n) : 0} km/u · ${pts.length} punten`;
  }

  function matches(t, q) {
    if (!t.active) return false;
    if (prefs.filter === "fav" && !prefs.fav.has(t.id)) return false;
    if (prefs.filter === "real" && t.kind === "sim") return false;
    if (prefs.filter === "sim" && t.kind !== "sim") return false;
    if (prefs.tgroup && !(t.groups || []).includes(prefs.tgroup)) return false;
    return !q || t.alias.toLowerCase().includes(q) || (t.notes || "").toLowerCase().includes(q);
  }

  function filtered() {
    const q = $("search").value.trim().toLowerCase();
    return [...trackers.values()].filter((t) => matches(t, q)).sort((a, b) =>
      (prefs.fav.has(b.id) - prefs.fav.has(a.id)) || a.alias.localeCompare(b.alias));
  }

  function renderList() {
    const items = filtered();
    if (!items.length) {
      const any = [...trackers.values()].some((t) => t.active);
      $("list").innerHTML = any ? '<div class="empty">Geen tracker past bij de zoekopdracht of de filter.</div>'
        : '<div class="empty">Nog geen trackers. Voeg er een toe via <a href="/admin">Trackers</a> of start een simulator.</div>';
      return;
    }
    $("list").innerHTML = items.map((t) => {
      const sos = (t.last_state === "E" ? ' <span class="pill sos">SOS</span>' : "") + (t.lost ? ' <span class="lostbadge">VERLOREN</span>' : "");
      const sim = t.kind === "sim" ? ' <span class="pill">virtueel</span>' : "";
      const meta = [MT.STATE[t.last_state] || "nog niets ontvangen", t.last_bat != null ? `${t.last_bat}%` : null,
                    t.last_spd ? `${t.last_spd} km/u` : null].filter(Boolean).join(" · ");
      const exp = t.id === selected && can("export")
        ? `<div class="stats">Exporteer ${prefs.trackOn ? prefs.hours : 24} u: <a href="/api/trackers/${t.id}/export?fmt=gpx&hours=${prefs.trackOn ? prefs.hours : 24}">GPX</a>
           · <a href="/api/trackers/${t.id}/export?fmt=csv&hours=${prefs.trackOn ? prefs.hours : 24}">CSV</a></div>` : "";
      const follow = t.id === selected && t.last_lat != null
        ? `<div class="stats"><button type="button" class="link" data-follow="${t.id}">${following === t.id ? "volgen stoppen" : "centreren en volgen"}</button></div>` : "";
      const stats = t.id === selected ? `<div class="stats">${MT.esc(statsFor(t.id))}</div>${exp}${follow}` : "";
      const vis = !prefs.hidden.has(t.id);
      const ico = t.icon ? MTIcons.svg(t.icon) : "";
      return `<div class="trk${t.stale ? " stale" : ""}${t.id === selected ? " sel" : ""}${vis ? "" : " hiddenmap"}" data-id="${t.id}">
        <input type="checkbox" class="vis" data-vis="${t.id}"${vis ? " checked" : ""} aria-label="${MT.esc(t.alias)} op de kaart tonen" title="Op de kaart tonen">
        <span class="tico" style="background:${MT.esc(t.color)}">${ico}</span>
        <div class="body"><div class="name">${MT.esc(t.alias)}${sim}${sos}</div>
        <div class="meta">${MT.esc(meta)}</div><div class="meta">${MT.ago(t.last_rx)}</div>${stats}</div>
        <button class="fav${prefs.fav.has(t.id) ? " on" : ""}" data-fav="${t.id}" aria-label="Favoriet" title="Favoriet">${prefs.fav.has(t.id) ? "★" : "☆"}</button></div>`;
    }).join("");
    $("list").querySelectorAll(".trk").forEach((el) => el.addEventListener("click", (e) => {
      if (e.target.closest(".vis, .fav")) return;
      select(Number(el.dataset.id), true);
    }));
    $("list").querySelectorAll("[data-follow]").forEach((b) => b.addEventListener("click", (e) => {
      e.stopPropagation();
      const id = Number(b.dataset.follow);
      setFollow(following === id ? null : id);
    }));
    $("list").querySelectorAll("[data-vis]").forEach((cb) => cb.addEventListener("change", () => {
      const id = Number(cb.dataset.vis);
      if (cb.checked) prefs.hidden.delete(id); else prefs.hidden.add(id);
      savePrefs(); applyVisibility();
    }));
    $("list").querySelectorAll("[data-fav]").forEach((b) => b.addEventListener("click", () => {
      const id = Number(b.dataset.fav);
      if (prefs.fav.has(id)) prefs.fav.delete(id); else prefs.fav.add(id);
      savePrefs(); renderList();
    }));
  }

  function applyVisibility() {
    for (const t of trackers.values()) upsertMarker(t);
    renderList();
    refreshLayers();
  }

  document.querySelectorAll(".chip").forEach((c) => c.addEventListener("click", () => {
    prefs.filter = c.dataset.filter;
    document.querySelectorAll(".chip").forEach((x) => x.classList.toggle("on", x === c));
    savePrefs(); renderList();
  }));
  $("show-all").addEventListener("click", () => { prefs.hidden.clear(); savePrefs(); applyVisibility(); });
  $("show-none").addEventListener("click", () => { for (const t of trackers.values()) prefs.hidden.add(t.id); savePrefs(); applyVisibility(); });
  $("show-filtered").addEventListener("click", () => {
    const keep = new Set(filtered().map((t) => t.id));
    for (const t of trackers.values()) { if (keep.has(t.id)) prefs.hidden.delete(t.id); else prefs.hidden.add(t.id); }
    savePrefs(); applyVisibility();
  });

  function setFollow(id) {
    following = id;
    $("followbtn").hidden = id == null;
    if (id != null) {
      const t = trackers.get(id);
      $("followbtn").textContent = `${t ? t.alias : "tracker"} volgen: stoppen`;
      if (t && t.last_lat != null) map.easeTo({ center: [t.last_lon, t.last_lat], zoom: Math.max(map.getZoom(), 15) });
    }
    renderList();
  }
  $("followbtn").addEventListener("click", () => setFollow(null));
  // Zelf de kaart verslepen stopt het volgen (zoomen niet).
  map.on("dragstart", () => { if (following != null) setFollow(null); });

  function select(id, fly) {
    selected = id;
    if (prefs.hidden.has(id)) { prefs.hidden.delete(id); savePrefs(); upsertMarker(trackers.get(id)); }
    const t = trackers.get(id);
    const m = markers.get(id);
    if (t && t.last_lat != null && fly) {
      map.flyTo({ center: [t.last_lon, t.last_lat], zoom: Math.max(map.getZoom(), 14),
        offset: phone() ? [0, -$("side").offsetHeight / 2] : [0, 0] });
    }
    if (m && !m.getPopup().isOpen()) m.togglePopup();
    renderList();
    refreshLayers();
  }

  $("search").addEventListener("input", renderList);
  $("search").addEventListener("keydown", (e) => {
    if (e.key !== "Enter") return;
    const first = $("list").querySelector(".trk");
    if (window.innerWidth <= 720) $("search").blur();
    if (first) select(Number(first.dataset.id), true);
  });

  let tgroups = [];
  async function loadTgroups() {
    tgroups = await MT.api("/api/tracker-groups").catch(() => []);
    const sel = $("tgfilter");
    $("tgfilter-wrap").hidden = !tgroups.length;
    if (prefs.tgroup && !tgroups.some((g) => g.id === prefs.tgroup)) prefs.tgroup = 0;
    const chans = tgroups.filter((g) => g.channel), plain = tgroups.filter((g) => !g.channel);
    const opt = (g, label) => `<option value="${g.id}">${MT.esc(label)} (${g.trackers.length})</option>`;
    sel.innerHTML = '<option value="0">Alle trackers</option>'
      + (chans.length ? `<optgroup label="Kanalen">${chans.map((g) => opt(g, g.channel)).join("")}</optgroup>` : "")
      + (plain.length ? `<optgroup label="Trackergroepen">${plain.map((g) => opt(g, g.name)).join("")}</optgroup>` : "");
    sel.value = String(prefs.tgroup);
  }
  $("tgfilter").addEventListener("change", () => { prefs.tgroup = Number($("tgfilter").value); savePrefs(); renderList(); });

  async function loadAll() {
    await loadTgroups();
    const list = await MT.api("/api/trackers");
    trackers.clear();
    list.forEach((t) => trackers.set(t.id, t));
    await Promise.all(list.filter((t) => t.active).map((t) => loadTrack(t.id)));
    for (const id of [...markers.keys()]) if (!trackers.has(id)) { markers.get(id).remove(); markers.delete(id); }
    list.forEach(upsertMarker);
    renderList();
    refreshLayers();
    fillTrackerSelect();
  }

  // ---- zones (geofences) -------------------------------------------------------------
  let drawing = null;   // {kind, pts:[], center, radius}

  function setDrawData(features) {
    const src = map.getSource("draw");
    if (src) src.setData({ type: "FeatureCollection", features });
  }

  function startDraw(kind) {
    drawing = { kind, pts: [], center: null, radius: 0 };
    document.body.classList.add("drawing");
    map.doubleClickZoom.disable();
    $("z-form").hidden = true;
    $("z-help").hidden = false;
    $("z-help").textContent = kind === "circle"
      ? "Klik het middelpunt, beweeg en klik opnieuw voor de straal. Esc = stoppen."
      : "Klik de hoekpunten. Dubbelklik of klik op het eerste punt om te sluiten. Esc = stoppen.";
  }

  function endDraw() {
    drawing = null;
    document.body.classList.remove("drawing");
    map.doubleClickZoom.enable();
    $("z-help").hidden = true;
    setDrawData([]);
  }

  function drawPreview(cursor) {
    if (!drawing) return;
    const feats = [];
    if (drawing.kind === "circle" && drawing.center) {
      const r = cursor ? dist({ lon: drawing.center[0], lat: drawing.center[1] }, { lon: cursor[0], lat: cursor[1] }) : drawing.radius;
      drawing.radius = Math.max(10, Math.round(r));
      feats.push({ type: "Feature", geometry: { type: "Polygon", coordinates: [circlePoly(drawing.center[0], drawing.center[1], drawing.radius)] } });
      feats.push({ type: "Feature", geometry: { type: "Point", coordinates: drawing.center } });
    } else if (drawing.kind === "polygon" && drawing.pts.length) {
      const ring = cursor ? [...drawing.pts, cursor] : [...drawing.pts];
      if (ring.length >= 3) feats.push({ type: "Feature", geometry: { type: "Polygon", coordinates: [[...ring, ring[0]]] } });
      else feats.push({ type: "Feature", geometry: { type: "LineString", coordinates: ring } });
      drawing.pts.forEach((p) => feats.push({ type: "Feature", geometry: { type: "Point", coordinates: p } }));
    }
    setDrawData(feats);
  }

  function finishDraw() {
    const d = drawing;
    endDraw();
    openZoneForm({ kind: d.kind, geom: d.kind === "circle" ? { center: d.center, radius: d.radius } : d.pts });
    setDrawData(d.kind === "circle"
      ? [{ type: "Feature", geometry: { type: "Polygon", coordinates: [circlePoly(d.center[0], d.center[1], d.radius)] } }]
      : [{ type: "Feature", geometry: { type: "Polygon", coordinates: [[...d.pts, d.pts[0]]] } }]);
  }

  map.on("mousemove", (e) => { if (drawing) drawPreview([e.lngLat.lng, e.lngLat.lat]); });
  map.on("click", (e) => {
    const p = [e.lngLat.lng, e.lngLat.lat];
    if (picking) { setHome(p[1], p[0], "gekozen op de kaart"); picking = false; document.body.classList.remove("drawing"); return; }
    if (!drawing) return;
    if (drawing.kind === "circle") {
      if (!drawing.center) { drawing.center = p; return; }
      drawPreview(p);
      finishDraw();
      return;
    }
    if (drawing.pts.length >= 3) {
      const first = map.project(drawing.pts[0]);
      if (Math.hypot(first.x - e.point.x, first.y - e.point.y) < 12) { finishDraw(); return; }
    }
    drawing.pts.push(p);
    drawPreview(null);
  });
  map.on("dblclick", (e) => {
    if (!drawing || drawing.kind !== "polygon") return;
    e.preventDefault();
    if (drawing.pts.length > 3) drawing.pts.pop();   // dubbelklik telt ook als klik
    if (drawing.pts.length >= 3) finishDraw();
  });
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape") { if (drawing) endDraw(); if (picking) { picking = false; document.body.classList.remove("drawing"); } }
  });

  $("z-circle").addEventListener("click", () => startDraw("circle"));
  $("z-poly").addEventListener("click", () => startDraw("polygon"));

  let zoneDraft = null;
  function fillTrackerSelect(selectedIds = []) {
    $("z-trackers").innerHTML = [...trackers.values()].sort((a, b) => a.alias.localeCompare(b.alias))
      .map((t) => `<option value="${t.id}"${selectedIds.includes(t.id) ? " selected" : ""}>${MT.esc(t.alias)}</option>`).join("");
  }

  function openZoneForm(z) {
    zoneDraft = { kind: z.kind, geom: z.geom };
    $("z-id").value = z.id || "";
    $("z-name").value = z.name || "";
    $("z-color").value = z.color || "#3b82f6";
    $("z-enter").checked = z.on_enter !== 0 && z.on_enter !== false;
    $("z-exit").checked = z.on_exit !== 0 && z.on_exit !== false;
    $("z-notify").value = z.notify_pubkey || "";
    $("z-personal").checked = z.id ? !!z.mine : !can("zones.manage");
    $("z-personal").disabled = !!z.id || !can("zones.manage");
    $("z-personal-wrap").hidden = !can("zones.manage");
    $("z-radius-wrap").hidden = z.kind !== "circle";
    if (z.kind === "circle") $("z-radius").value = Math.round(z.geom.radius);
    fillTrackerSelect(z.trackers || []);
    $("z-msg").textContent = "";
    $("z-form").hidden = false;
    $("z-name").focus();
  }

  $("z-cancel").addEventListener("click", () => { $("z-form").hidden = true; setDrawData([]); });
  $("z-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const geom = zoneDraft.kind === "circle" ? { center: zoneDraft.geom.center, radius: Number($("z-radius").value) } : zoneDraft.geom;
    const body = { name: $("z-name").value, kind: zoneDraft.kind, geom, color: $("z-color").value,
      on_enter: $("z-enter").checked, on_exit: $("z-exit").checked, notify_pubkey: $("z-notify").value.trim(),
      trackers: [...$("z-trackers").selectedOptions].map((o) => Number(o.value)), active: true,
      personal: $("z-personal").checked };
    try {
      const id = $("z-id").value;
      await MT.api(id ? `/api/geofences/${id}` : "/api/geofences", { method: id ? "PUT" : "POST", body });
      $("z-form").hidden = true;
      setDrawData([]);
      await loadZones();
    } catch (err) { $("z-msg").className = "msg err"; $("z-msg").textContent = err.message; }
  });

  async function loadZones() {
    if (!can("zones.view")) { zones = []; return; }
    zones = await MT.api("/api/geofences");
    $("zones").innerHTML = zones.length ? zones.map((z) => `<div class="item">
      <span class="dot" style="background:${MT.esc(z.color)}"></span> <strong>${MT.esc(z.name)}</strong>
      <span class="muted">${z.kind === "circle" ? Math.round(z.geom.radius) + " m" : (z.geom.length - 1) + " punten"}</span>
      ${z.active ? "" : '<span class="pill">uit</span>'}${z.mine ? ' <span class="pill ok">eigen</span>' : ""}
      <div class="row"><button data-zgo="${z.id}">Toon</button><button data-zedit="${z.id}">Bewerken</button>
      <button data-ztog="${z.id}">${z.active ? "Uitzetten" : "Aanzetten"}</button>
      <button class="danger" data-zdel="${z.id}">Verwijderen</button></div></div>`).join("")
      : '<div class="empty">Nog geen zones. Teken er een met de knoppen hierboven.</div>';
    zones.forEach((z) => {
      if (!z.editable) $("zones").querySelectorAll(`[data-zedit="${z.id}"],[data-ztog="${z.id}"],[data-zdel="${z.id}"]`).forEach((b) => b.remove());
    });
    const byId = (id) => zones.find((z) => z.id === Number(id));
    $("zones").querySelectorAll("[data-zgo]").forEach((b) => b.addEventListener("click", () => {
      const z = byId(b.dataset.zgo);
      const ring = z.kind === "circle" ? circlePoly(z.geom.center[0], z.geom.center[1], z.geom.radius) : z.geom;
      const bb = new maplibregl.LngLatBounds();
      ring.forEach((p) => bb.extend(p));
      map.fitBounds(bb, { padding: 60, maxZoom: 16 });
    }));
    $("zones").querySelectorAll("[data-zedit]").forEach((b) => b.addEventListener("click", () => openZoneForm(byId(b.dataset.zedit))));
    $("zones").querySelectorAll("[data-ztog]").forEach((b) => b.addEventListener("click", async () => {
      const z = byId(b.dataset.ztog);
      await MT.api(`/api/geofences/${z.id}`, { method: "PUT", body: { ...z, active: !z.active } });
      loadZones();
    }));
    $("zones").querySelectorAll("[data-zdel]").forEach((b) => b.addEventListener("click", async () => {
      const z = byId(b.dataset.zdel);
      if (!confirm(`Zone "${z.name}" verwijderen?`)) return;
      await MT.api(`/api/geofences/${z.id}`, { method: "DELETE" });
      loadZones();
    }));
    refreshLayers();
  }

  async function refreshEvents() {
    if (!can("zones.view")) return;
    const ev = await MT.api("/api/geofence-events?limit=30");
    $("events").innerHTML = ev.length ? ev.map((e) => `<div class="ev">
      <span class="${e.event === "enter" ? "in" : "out"}">${e.event === "enter" ? "▶ binnen" : "◀ buiten"}</span>
      <strong>${MT.esc(e.tracker)}</strong> · ${MT.esc(e.geofence)}
      <div class="muted">${new Date(e.ts * 1000).toLocaleString("nl-BE")}</div></div>`).join("")
      : '<div class="empty">Nog geen meldingen.</div>';
  }

  // ---- simulator -----------------------------------------------------------------------
  const TOWNS = MT.TOWNS;
  let home = { lat: TOWNS[0][1], lon: TOWNS[0][2] };
  let picking = false;
  $("s-town").innerHTML = TOWNS.map((t, i) => `<option value="${i}">${t[0]}</option>`).join("");
  function setHome(lat, lon, label) {
    home = { lat, lon };
    $("s-home").textContent = `${lat.toFixed(5)}, ${lon.toFixed(5)} (${label})`;
  }
  setHome(TOWNS[0][1], TOWNS[0][2], TOWNS[0][0]);
  $("s-town").addEventListener("change", () => { const t = TOWNS[$("s-town").value]; setHome(t[1], t[2], t[0]); });
  $("s-pick").addEventListener("click", () => { picking = true; document.body.classList.add("drawing"); });

  $("s-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const params = {};
    document.querySelectorAll("[data-p]").forEach((el) => { params[el.dataset.p] = Number(el.value); });
    try {
      await MT.api("/api/sims", { method: "POST", body: { alias: $("s-alias").value.trim(), color: $("s-color").value,
        profile: $("s-profile").value, home_lat: home.lat, home_lon: home.lon, params,
        loss_pct: Number($("s-loss").value), batt_speed: Number($("s-batt").value), running: true } });
      $("s-msg").className = "msg ok";
      $("s-msg").textContent = "Gestart. De eerste route wordt berekend…";
      $("s-alias").value = "";
      refreshSims();
    } catch (err) { $("s-msg").className = "msg err"; $("s-msg").textContent = err.message; }
  });

  const PROF = { car: "auto", bike: "fiets", walk: "te voet", travel: "reiziger" };
  async function refreshSims() {
    if (!can("sims.manage")) return;
    const sims = await MT.api("/api/sims");
    simRoutes = await MT.api("/api/sims/routes");
    refreshLayers();
    $("sims").innerHTML = sims.length ? sims.map((s) => {
      const st = s.status || {};
      const run = st.running;
      const info = run ? [MT.esc(st.note || st.phase), st.spd ? `${st.spd} km/u` : null,
        `batterij ${st.batt}%${st.charging ? " ⚡" : ""}`].filter(Boolean).join(" · ") : "gestopt";
      const counts = run ? `${st.sent} verstuurd, ${st.lost} verloren · ${st.msgs_h}/u · zendtijd ${st.airtime_s_h} s/u · ${st.trips} ritten, ${st.km} km` : "";
      return `<div class="item"><span class="dot" style="background:${MT.esc(s.color)}"></span>
        <strong>${MT.esc(s.alias)}</strong> <span class="muted">${PROF[s.profile] || s.profile}</span>
        <div class="stats">${info}</div><div class="stats">${counts}</div>
        <div class="row"><button data-sgo="${s.tracker_id}">Toon</button>
        <a class="btnlink" href="/admin#edit=${s.tracker_id}">Bewerken</a>
        <button data-srun="${s.tracker_id}" data-on="${run ? 0 : 1}">${run ? "Stop" : "Start"}</button>
        <button class="danger" data-sdel="${s.tracker_id}">Verwijderen</button></div></div>`;
    }).join("") : '<div class="empty">Nog geen simulators.</div>';
    $("sims").querySelectorAll("[data-sgo]").forEach((b) => b.addEventListener("click", () => select(Number(b.dataset.sgo), true)));
    $("sims").querySelectorAll("[data-srun]").forEach((b) => b.addEventListener("click", async () => {
      await MT.api(`/api/sims/${b.dataset.srun}`, { method: "PUT", body: { running: b.dataset.on === "1" } });
      refreshSims();
    }));
    $("sims").querySelectorAll("[data-sdel]").forEach((b) => b.addEventListener("click", async () => {
      if (!confirm("Simulator en al zijn posities verwijderen?")) return;
      await MT.api(`/api/trackers/${b.dataset.sdel}`, { method: "DELETE" });
      refreshSims();
    }));
  }
  setInterval(() => { if (!$("pane-sim").hidden || Object.keys(simRoutes).length) refreshSims(); }, 5000);

  // ---- opstart en live --------------------------------------------------------------------
  map.on("load", async () => {
    addLayers();
    await loadAll();
    await loadZones();
    refreshSims();
    loadNodes();
    const qs = new URLSearchParams(location.search);
    if (qs.get("focus")) {
      // vanuit het logboek: die tracker kiezen en naar dat punt vliegen
      const id = Number(qs.get("focus"));
      if (trackers.has(id)) select(id, false);
      if (qs.get("lat")) {
        const ll = [Number(qs.get("lon")), Number(qs.get("lat"))];
        map.jumpTo({ center: ll, zoom: 15 });
        new maplibregl.Marker({ color: "#f59e0b" }).setLngLat(ll).addTo(map);
      }
      return;
    }
    const withPos = [...trackers.values()].filter((t) => t.active && t.last_lat != null);
    if (withPos.length > 1) {
      const b = new maplibregl.LngLatBounds();
      withPos.forEach((t) => b.extend([t.last_lon, t.last_lat]));
      map.fitBounds(b, { padding: pad(), maxZoom: 14, duration: 0 });
    } else if (withPos.length === 1) {
      map.jumpTo({ center: [withPos[0].last_lon, withPos[0].last_lat], zoom: 13 });
    }
  });
  map.on("style.load", () => { addLayers(); refreshLayers(); });
  const restyle = () => map.setStyle(MTBasemap.style(dark.matches, status.tiles));
  document.addEventListener("mt-theme", restyle);
  window.matchMedia("(prefers-color-scheme: dark)").addEventListener("change", () => { if (MT.theme() === "auto") restyle(); });
  async function reloadTracks() { await Promise.all([...trackers.keys()].map(loadTrack)); refreshLayers(); renderList(); }
  $("hours").querySelectorAll("button").forEach((b) => b.addEventListener("click", () => {
    prefs.hours = Number(b.dataset.h); prefs.trackOn = true; $("trackon").checked = true;
    setSeg($("hours"), "h", prefs.hours); savePrefs(); reloadTracks();
  }));
  $("trackon").addEventListener("change", () => { prefs.trackOn = $("trackon").checked; savePrefs(); reloadTracks(); });
  $("colormode").querySelectorAll("button").forEach((b) => b.addEventListener("click", () => {
    prefs.color = b.dataset.c; setSeg($("colormode"), "c", prefs.color); savePrefs();
    $("speedlegend").hidden = prefs.color !== "speed"; refreshLayers();
  }));

  const phone = () => window.innerWidth <= 720;
  // Inklapbare blokken: stand per browser onthouden (telefoon: standaard dicht).
  document.querySelectorAll("details.sect").forEach((d) => {
    const key = "sect." + d.dataset.sect;
    const saved = store.get(key, null);
    d.open = saved === null ? (phone() ? false : d.open) : saved;
    d.addEventListener("toggle", () => store.set(key, d.open));
  });

  // Versleepbare zijbalk (alleen op een breed scherm).
  const side = $("side");
  const setW = (w) => { side.style.width = Math.max(200, Math.min(640, w)) + "px"; map.resize(); };
  const savedW = store.get("sideW", null);
  if (savedW && !phone()) setW(savedW);
  $("resizer").addEventListener("pointerdown", (e) => {
    e.preventDefault();
    $("resizer").setPointerCapture(e.pointerId);
    document.body.classList.add("resizing");
    const move = (ev) => setW(ev.clientX - side.getBoundingClientRect().left);
    const up = () => {
      document.body.classList.remove("resizing");
      $("resizer").removeEventListener("pointermove", move);
      store.set("sideW", side.offsetWidth);
    };
    $("resizer").addEventListener("pointermove", move);
    $("resizer").addEventListener("pointerup", up, { once: true });
  });
  $("resizer").addEventListener("keydown", (e) => {
    if (e.key === "ArrowLeft" || e.key === "ArrowRight") {
      setW(side.offsetWidth + (e.key === "ArrowRight" ? 20 : -20));
      store.set("sideW", side.offsetWidth);
    }
  });
  $("resizer").addEventListener("dblclick", () => { side.style.width = ""; store.set("sideW", null); map.resize(); });
  // ruimte die het onderpaneel van de kaart inneemt (telefoon)
  const pad = () => (phone() ? { top: 60, left: 40, right: 40, bottom: $("side").offsetHeight + 30 } : 80);

  // Telefoon: onderpaneel versleepbaar. Omlaag tot onder ~12 % = verbergen (dan
  // een knop "Lijst"); de hoogte wordt onthouden. Tikken wisselt half/klein.
  (function sheet() {
    const side = $("side"), handle = $("sheet"), showBtn = $("sheet-show");
    const vh = () => window.innerHeight - 48;
    const apply = (frac) => {
      if (!phone()) return;
      if (frac < 0.12) {
        side.classList.add("sheet-hidden"); showBtn.hidden = false; store.set("sheetFrac", 0); map.resize(); return;
      }
      side.classList.remove("sheet-hidden"); showBtn.hidden = true;
      frac = Math.min(0.92, Math.max(0.14, frac));
      side.style.height = Math.round(frac * vh()) + "px";
      store.set("sheetFrac", frac);
      map.resize();
    };
    apply(store.get("sheetFrac", 0.45));
    let startY = 0, startH = 0, moved = false, dragging = false;
    handle.addEventListener("pointerdown", (e) => {
      if (!phone()) return;
      try { handle.setPointerCapture(e.pointerId); } catch (_) { /* geen echte aanwijzer */ }
      startY = e.clientY; startH = side.offsetHeight; moved = false; dragging = true;
      side.style.transition = "none";
    });
    handle.addEventListener("pointermove", (e) => {
      if (!dragging) return;
      const dy = startY - e.clientY;
      if (Math.abs(dy) > 6) moved = true;
      side.style.height = Math.max(0, startH + dy) + "px";
    });
    const end = () => {
      if (!dragging) return;
      dragging = false;
      const frac = side.offsetHeight / vh();   // eerst meten, dan pas de animatie terug aan
      side.style.transition = "";
      if (!moved) { apply(frac > 0.3 ? 0.18 : 0.5); return; }
      apply(frac);
    };
    handle.addEventListener("pointerup", end);
    handle.addEventListener("pointercancel", end);
    showBtn.addEventListener("click", () => apply(0.45));
    window.addEventListener("resize", () => { if (phone()) apply(store.get("sheetFrac", 0.45)); else side.style.height = ""; });
  })();

  MT.live((msg) => {
    if (msg.type === "mesh") MT.meshPill($("mesh"), msg.mesh);
    if (msg.type === "position") {
      const t = msg.tracker;
      trackers.set(t.id, t);
      const p = msg.position;
      if (p.lat != null && !p.suspect && hoursNow()) {
        if (!tracks.has(t.id)) tracks.set(t.id, []);
        // Chronologisch invoegen: een bericht kan eerdere punten meebrengen.
        const arr = tracks.get(t.id), item = { lat: p.lat, lon: p.lon, spd: p.spd, ts: p.ts, state: p.state, bat: p.bat };
        let i = arr.length;
        while (i > 0 && arr[i - 1].ts > item.ts) i--;
        if (!(i > 0 && arr[i - 1].ts === item.ts)) arr.splice(i, 0, item);
      }
      if (p.state === "E" && p.lat != null && !p.suspect) {
        if (!sosPts.has(t.id)) sosPts.set(t.id, []);
        sosPts.get(t.id).push({ lat: p.lat, lon: p.lon, ts: p.ts, state: "E" });
      }
      upsertMarker(t);
      renderList();
      refreshLayers();
      if (following === t.id && t.last_lat != null) map.easeTo({ center: [t.last_lon, t.last_lat], duration: 800 });
    }
    if (msg.type === "tracker" || msg.type === "tracker_deleted" || msg.type === "tracker_groups") loadAll();
    if (msg.type === "geofences") loadZones();
    if (msg.type === "lost_seen" && msg.tracker) {
      const t = msg.tracker;
      if (trackers.has(t.id)) { trackers.set(t.id, { ...trackers.get(t.id), ...t }); renderList(); }
      toast(`Verloren tracker ${t.alias} is terug opgedoken`, "out");
    }
    if (msg.type === "geofence") {
      const e = msg.event;
      toast(`${e.tracker} ${e.event === "enter" ? "is binnengekomen in" : "heeft verlaten:"} ${e.geofence}`, e.event === "enter" ? "" : "out");
      if (!$("pane-zones").hidden) refreshEvents();
    }
  });

  setInterval(() => { renderList(); for (const t of trackers.values()) upsertMarker(t); }, 30000);
  if (kiosk) {
    // Kioskweergave: elke minuut alle trackers in beeld (geen zijbalk om te kiezen).
    setInterval(() => {
      const pts = [...trackers.values()].filter((t) => t.active && t.last_lat != null);
      if (!pts.length) return;
      const b = new maplibregl.LngLatBounds();
      pts.forEach((t) => b.extend([t.last_lon, t.last_lat]));
      map.fitBounds(b, { padding: 80, maxZoom: 15, duration: 1500 });
    }, 60000);
  }
})();
