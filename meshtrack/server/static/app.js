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

  // Basiskaart: kaarten die de offline-app op dit toestel bewaarde (OPFS) bovenaan, de kaart van
  // de server eronder voor de rest. Geen kaarten op het toestel (of geen OPFS): alleen de server.
  const devMaps = await MTBasemap.deviceMaps();
  const baseStyle = () => {
    if (devMaps.length) {
      try { return MTBasemap.deviceStyle(dark.matches, devMaps, status.tiles); } catch (_) { /* terug naar de server */ }
    }
    return MTBasemap.style(dark.matches, status.tiles);
  };
  const map = new maplibregl.Map({
    container: "map",
    style: baseStyle(),
    center: status.map.center,
    zoom: status.map.zoom,
    attributionControl: { compact: true },
  });
  map.addControl(new maplibregl.NavigationControl({ showCompass: false }), "top-right");
  map.addControl(new maplibregl.ScaleControl({ unit: "metric" }), "bottom-right");
  if (devMaps.length || status.tiles) {          // waar komt de achtergrond vandaan?
    const src = document.createElement("div");
    src.id = "mapsrc";
    src.className = "mapsrc";
    map.getContainer().appendChild(src);
    // Wat staat er nu in beeld: een kaart van het toestel (gebied en zoom), anders die van de server.
    const showSrc = () => {
      // (zonder serverkaart komt alles wat er te zien is van het toestel)
      const dev = devMaps.length > 0 && (!status.tiles || MTBasemap.deviceCovers(devMaps, map.getCenter(), map.getZoom(), true));
      src.textContent = dev ? "Kaart: op dit toestel" : "Kaart: server";
      src.title = dev
        ? "Deze kaart komt uit de offline-app op dit toestel."
        : devMaps.length
          ? "Hier (of op deze zoom) heeft dit toestel geen kaart: de kaart komt van de server."
          : "De kaart komt van de server. Download je een kaart in de offline-app, dan laadt deze pagina die van dit toestel.";
    };
    showSrc();
    map.on("moveend", showSrc);
  }
  window.MTApp = { map: () => map };     // testhaak (alleen lezen)

  const trackers = new Map();   // id -> tracker
  const tracks = new Map();     // id -> [{lon,lat,spd,ts,state,bat}]
  const sosPts = new Map();     // id -> SOS-posities in de gekozen periode (min. 24 u), los van het spoor
  let chans = [];               // kanalen die je mag lezen (filter)
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
    kanaal: Number(new URLSearchParams(location.search).get("kanaal")) || store.get("kanaal", 0),   // 0 = alle
    trackOn: store.get("trackOn", true),
    hours: store.get("hours", 24),
    color: store.get("color", "tracker"),
    ntypes: new Set(store.get("ntypes", [2])),
    nrecent: store.get("nrecent", true),
  };
  const savePrefs = () => {
    store.set("fav", [...prefs.fav]); store.set("hidden", [...prefs.hidden]); store.set("filter", prefs.filter); store.set("kanaal", prefs.kanaal);
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
      for (let i = 1; i < pts.length; i++) {
        if (pts[i - 1].prio && pts[i].prio) lines.push({ type: "Feature", properties: { prio: 1, sel, color: PRIO, spd: 0 },
          geometry: { type: "LineString", coordinates: [[pts[i - 1].lon, pts[i - 1].lat], [pts[i].lon, pts[i].lat]] } });
      }
      for (const p of pts) {
        points.push({ type: "Feature", properties: { tid: id, prio: p.prio ? 1 : 0, alias: t.alias, color: t.color, spd: p.spd ?? -1, ts: p.ts, state: p.state, bat: p.bat ?? -1, old: p.old || (p.state === "V" && p.fix_age > 120) ? 1 : 0 },
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

  // Op verzoek (toestand V): amber, in het donker lichter (zelfde kleur als --vreq in style.css)
  // V met een fix van meer dan 2 min voor de ontvangst: "laatst gekend" (fix_age als de server die meestuurt)
  const vOld = (p, rx) => p.state === "V" && p.lat != null && (p.fix_age != null ? p.fix_age > 120 : (rx || Date.now() / 1000) - p.ts > 120);
  const vAmber = () => (dark.matches ? "#ffc83d" : "#e0a400");
  const vIsOld = ["all", ["==", ["get", "state"], "V"], ["==", ["get", "old"], 1]];
  const vColor = (c) => ["case", vIsOld, "rgba(0,0,0,0)", ["==", ["get", "state"], "V"], vAmber(), c];   // oud: hol

  // Prioritair (blauwe lichten, server 1.4.0): t.prio + t.prio_until (unix-tijd); per spoorpunt p.prio.
  const PRIO = "#1565ff";
  const isPrio = (t) => !!(t && t.prio) && (!t.prio_until || t.prio_until > Date.now() / 1000);
  const prioText = (t) => {
    if (!t.prio_until) return "Prioritair";
    const m = Math.max(1, Math.ceil((t.prio_until - Date.now() / 1000) / 60));
    return `Prioritair, nog ${m} min`;
  };
  const prioPill = (t) => (isPrio(t) ? ` <span class="pill prio">${prioText(t)}</span>` : "");

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
    map.addLayer({ id: "tracks", type: "line", source: "tracks", filter: ["!=", ["get", "prio"], 1], layout: { "line-join": "round", "line-cap": "round" },
      paint: { "line-color": colorSel.value === "speed" ? speedColor : ["get", "color"], "line-width": ["case", ["==", ["get", "sel"], 1], 4, 2.5] } });
    // gereden met prioriteit (blauwe lichten): blauw en iets breder, bovenop het gewone spoor
    map.addLayer({ id: "tracks-prio", type: "line", source: "tracks", filter: ["==", ["get", "prio"], 1], layout: { "line-join": "round", "line-cap": "round" },
      paint: { "line-color": PRIO, "line-width": ["case", ["==", ["get", "sel"], 1], 5, 3.5] } });
    map.addSource("points", { type: "geojson", data: f.points });
    map.addLayer({ id: "points", type: "circle", source: "points", minzoom: 12,
      // SlowTrack-punten (state L): kleinere, lichtere stippen; de lijn blijft chronologisch
      paint: { "circle-radius": ["interpolate", ["linear"], ["zoom"], 12, ["case", ["match", ["get", "state"], ["L", "Q"], true, false], 1.3, 2], 16, ["case", ["match", ["get", "state"], ["L", "Q"], true, false], 3, 5]],
               "circle-color": vColor(colorSel.value === "speed" ? speedColor : ["get", "color"]),
               "circle-opacity": ["case", ["match", ["get", "state"], ["L", "Q"], true, false], 0.7, 1],
               "circle-stroke-color": ["case", vIsOld, vAmber(), dark.matches ? "#000" : "#fff"],
               "circle-stroke-width": ["case", vIsOld, 1.5, ["match", ["get", "state"], ["L", "Q"], true, false], 0.5, 1] } });
    // Op verzoek (V): amber stip met een kleine ring, bescheiden in het spoor
    map.addLayer({ id: "points-v", type: "circle", source: "points", minzoom: 12, filter: ["==", ["get", "state"], "V"],
      paint: { "circle-radius": ["interpolate", ["linear"], ["zoom"], 12, 4, 16, 8], "circle-color": "rgba(0,0,0,0)",
               "circle-stroke-color": vAmber(), "circle-stroke-width": 1.5 } });
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
    map.setPaintProperty("points", "circle-color", vColor(c));
    map.setPaintProperty("points-v", "circle-stroke-color", vAmber());
  }

  map.on("click", "points", (e) => {
    if (drawing) return;
    const p = e.features[0].properties;
    const lines = [`<strong>${MT.esc(p.alias)}</strong>`, new Date(p.ts * 1000).toLocaleString("nl-BE"),
      MT.esc(MT.STATE[p.state] || p.state) + (p.prio === 1 ? ' <span class="pill prio">Prioritair</span>' : ""), p.spd >= 0 ? `${p.spd} km/u` : null, p.bat >= 0 ? `batterij ${p.bat}%` : null];
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

  // "SlowTrack: laatste burst 14:05" (met datum als het niet vandaag was)
  function slowLine(t) {
    if (!t.last_slow_rx) return "";
    const d = new Date(t.last_slow_rx * 1000);
    const hm = d.toLocaleTimeString("nl-BE", { hour: "2-digit", minute: "2-digit" });
    return `SlowTrack: laatste burst ${d.toDateString() === new Date().toDateString() ? hm : d.toLocaleDateString("nl-BE", { day: "numeric", month: "short" }) + " " + hm}`;
  }

  // Server 1.4.1: de tracker stuurt (de laatste 24 u) op het openbare kanaal Public: niet klaargemaakt
  const pubPill = (t) => (t.op_public ? ` <span class="lostbadge" title="Stuurt op Public${MT.publicSeen(t) ? `, laatst om ${MT.esc(MT.publicSeen(t))}` : ""}: maak hem klaar in Toestellen">op Public</span>` : "");
  function popupHtml(t) {
    const st = MT.STATE[t.last_state] || t.last_state || "–";
    return [
      `<strong>${MT.esc(t.alias)}</strong>${t.kind === "sim" ? ' <span class="pill">virtueel</span>' : ""}${t.lost ? ' <span class="lostbadge">VERLOREN</span>' : ""}${prioPill(t)}${pubPill(t)}`,
      `${MT.esc(st)}${t.last_mode ? " · " + MT.esc(MT.MODE[t.last_mode] || t.last_mode) : ""}`,
      t.last_spd != null ? `${t.last_spd} km/u${t.last_crs != null ? " · koers " + t.last_crs + "°" : ""}` : null,
      t.last_bat != null ? `batterij ${t.last_bat}%` : null,
      `laatst gehoord ${MT.ago(t.last_rx)}`,
      slowLine(t) || null,
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
    // antwoord op een locatieverzoek: amber ring; bij een nieuw antwoord een korte puls
    el.classList.toggle("vreq", t.last_state === "V");
    if (t.last_state === "V" && el.dataset.vts !== String(t.last_ts)) {
      if (el.dataset.vts !== undefined || Date.now() / 1000 - (t.last_rx || 0) < 120) {
        el.classList.remove("vpulse"); void el.offsetWidth; el.classList.add("vpulse");
        setTimeout(() => el.classList.remove("vpulse"), 3600);
      }
      el.dataset.vts = String(t.last_ts);
    }
    el.classList.toggle("lost", !!t.lost);
    // blauwe lichten: knippert blauw/gewone kleur (~1 Hz) met sirene-badge; minder beweging: vaste blauwe ring
    el.classList.toggle("prio", isPrio(t));
    el.setAttribute("aria-label", isPrio(t) ? `${t.alias}: ${prioText(t)}` : t.alias);
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
    if (prefs.filter === "prio" && !isPrio(t)) return false;
    if (prefs.kanaal && t.channel_id !== prefs.kanaal) return false;   // alleen trackers van dit kanaal
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
      const sos = (t.last_state === "E" ? ' <span class="pill sos">SOS</span>' : "") + (t.lost ? ' <span class="lostbadge">VERLOREN</span>' : "") + prioPill(t) + pubPill(t);
      const sim = t.kind === "sim" ? ' <span class="pill">virtueel</span>' : "";
      const meta = [MT.STATE[t.last_state] || "nog niets ontvangen", t.last_bat != null ? `${t.last_bat}%` : null,
                    t.last_spd ? `${t.last_spd} km/u` : null].filter(Boolean).join(" · ");
      const exp = t.id === selected && can("export")
        ? `<div class="stats">Exporteer ${prefs.trackOn ? prefs.hours : 24} u: <a href="/api/trackers/${t.id}/export?fmt=gpx&hours=${prefs.trackOn ? prefs.hours : 24}">GPX</a>
           · <a href="/api/trackers/${t.id}/export?fmt=csv&hours=${prefs.trackOn ? prefs.hours : 24}">CSV</a></div>` : "";
      const follow = t.id === selected && t.last_lat != null
        ? `<div class="stats"><button type="button" class="link" data-follow="${t.id}">${following === t.id ? "volgen stoppen" : "centreren en volgen"}</button></div>` : "";
      const slow = t.id === selected && t.last_slow_rx ? `<div class="stats">${MT.esc(slowLine(t))}</div>` : "";
      const stats = t.id === selected ? `<div class="stats">${MT.esc(statsFor(t.id))}</div>${slow}${exp}${follow}` : "";
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

  // Kanaalfilter: de kanalen die je mag lezen (meer dan één = samen op de kaart, of één kiezen).
  async function loadTgroups() {
    chans = MT.me && MT.me.kind === "share" ? [] : await MT.api("/api/channels/mine").catch(() => []);
    const sel = $("tgfilter");
    $("tgfilter-wrap").hidden = chans.length < 2;
    if (prefs.kanaal && !chans.some((c) => c.id === prefs.kanaal)) prefs.kanaal = 0;
    sel.innerHTML = '<option value="0">Alle kanalen</option>' +
      chans.map((c) => `<option value="${c.id}">${MT.esc(c.name)} (${c.trackers})</option>`).join("");
    sel.value = String(prefs.kanaal);
    if (typeof rqUpdBar === "function") rqUpdBar();
  }
  $("tgfilter").addEventListener("change", () => { prefs.kanaal = Number($("tgfilter").value); savePrefs(); renderList(); rqUpdBar(); });

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
  const restyle = () => map.setStyle(baseStyle());
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

  // SlowTrack (server 1.1): een burst kan 30 berichten na elkaar zijn; lijst en lagen één keer na afloop.
  let burstTimer = null, burstEase = false;
  const followTo = (t) => { if (following === t.id && t.last_lat != null) map.easeTo({ center: [t.last_lon, t.last_lat], duration: 800 }); };
  MT.live((msg) => {
    if (msg.type === "mesh") { MT.meshPill($("mesh"), msg.mesh); status.mesh = msg.mesh; if (msg.mesh && msg.mesh.connected && rq.info) rq.info.connected = true; rqUpdBar(); }
    if (msg.type === "position") {
      const t = msg.tracker;
      trackers.set(t.id, t);
      const p = msg.position;
      // history=1: ouder gelogd punt (niet nieuwer dan de live positie); msg.tracker heeft al de juiste last_*.
      const hist = p.history === 1;
      if (p.lat != null && !p.suspect && hoursNow() && !(hist && p.ts < Date.now() / 1000 - hoursNow() * 3600)) {
        if (!tracks.has(t.id)) tracks.set(t.id, []);
        // Chronologisch invoegen: een bericht kan eerdere punten meebrengen.
        const arr = tracks.get(t.id), item = { lat: p.lat, lon: p.lon, spd: p.spd, ts: p.ts, state: p.state, bat: p.bat, old: vOld(p) ? 1 : 0, prio: p.prio ? 1 : 0 };
        let i = arr.length;
        while (i > 0 && arr[i - 1].ts > item.ts) i--;
        if (!(i > 0 && arr[i - 1].ts === item.ts)) arr.splice(i, 0, item);
      }
      if (p.state === "E" && p.lat != null && !p.suspect) {
        if (!sosPts.has(t.id)) sosPts.set(t.id, []);
        sosPts.get(t.id).push({ lat: p.lat, lon: p.lon, ts: p.ts, state: "E" });
      }
      upsertMarker(t);
      rqNote(t, p);
      if (p.slow === 1) {
        if (!hist) burstEase = true;
        clearTimeout(burstTimer);
        burstTimer = setTimeout(() => {
          renderList(); refreshLayers();
          if (burstEase) followTo(trackers.get(t.id) || t);
          burstEase = false;
        }, 300);
        return;
      }
      renderList();
      refreshLayers();
      followTo(t);
    }
    // Bekende tracker (o.a. begin/einde/afloop van prioriteit): alleen die bijwerken; anders alles opnieuw laden.
    if (msg.type === "tracker" && msg.tracker && trackers.has(msg.tracker.id)) {
      trackers.set(msg.tracker.id, { ...trackers.get(msg.tracker.id), ...msg.tracker });
      upsertMarker(trackers.get(msg.tracker.id)); renderList();
    } else if (msg.type === "tracker" || msg.type === "tracker_deleted" || msg.type === "channels") loadAll();
    if (msg.type === "geofences") loadZones();
    if (msg.type === "loc_request") rqEvent(msg);
    if (msg.type === "lost_seen" && msg.tracker) {
      const t = msg.tracker;
      if (trackers.has(t.id)) { trackers.set(t.id, { ...trackers.get(t.id), ...t }); renderList(); }
      toast(`Verloren tracker ${t.alias} is weer opgedoken`, "out");
    }
    if (msg.type === "geofence") {
      const e = msg.event;
      toast(`${e.tracker} ${e.event === "enter" ? "is binnengekomen in" : "heeft verlaten:"} ${e.geofence}`, e.event === "enter" ? "" : "out");
      if (!$("pane-zones").hidden) refreshEvents();
    }
  });

  // ---- positie vragen (locatieverzoek via de server) ------------------------------------------
  // POST /api/channels/{cid}/request {target:"*"|id} laat de server "T1R|..." op het kanaal sturen; trackers
  // (0.9.5+) antwoorden met een gewone positie met toestand V. GET geeft de wachttijden en de trackers.
  const rqSay = (el, t, ok) => { el.textContent = t || ""; el.className = "msg " + (ok ? "ok" : ok === false ? "err" : ""); };
  const rq = { req: null, info: null, until: {} };      // until: "cid:*" of "cid:id" -> tijdstip (s) tot wanneer wachten
  const nowS = () => Date.now() / 1000;
  const rqWait = (cid, target) => Math.max(0, Math.ceil((rq.until[`${cid}:${target}`] || 0) - nowS()));
  const rqHms = (ts) => new Date(ts * 1000).toLocaleTimeString("nl-BE", { hour: "2-digit", minute: "2-digit", second: "2-digit" });
  const rqChanName = (cid) => ((chans.find((c) => c.id === cid) || {}).name || `kanaal ${cid}`);
  async function rqFetch(path, opts = {}) {             // zoals MT.api, maar met status en het antwoord van de server
    const r = await fetch(path, { ...opts, headers: opts.body ? { "Content-Type": "application/json" } : {}, body: opts.body ? JSON.stringify(opts.body) : undefined });
    if (r.status === 401) { location.href = "/login"; throw new Error("niet ingelogd"); }
    const j = await r.json().catch(() => ({}));
    return { status: r.status, ok: r.ok, j, retry: Number(j.retry_after || r.headers.get("Retry-After") || 0) };
  }
  function rqUpdBar() {
    const show = !kiosk && MT.me && MT.me.kind === "user" && chans.length > 0;
    $("rq-wrap").hidden = !show;
    if (!show) return;
    const cid = prefs.kanaal || 0;
    const w = cid ? rqWait(cid, "*") : 0;
    // zonder verbinding met de mesh kan de server niets versturen
    const off = !!(status.mesh && status.mesh.connected === false) || !!(rq.info && rq.info.cid === cid && rq.info.connected === false);
    $("rq-btn").disabled = off;
    $("rq-btn").textContent = w && !off ? `Positie vragen (alle: nog ${w} s)` : "Positie vragen";
    $("rq-lbl").textContent = off ? "De server is niet met de mesh verbonden" : cid ? `op ${rqChanName(cid)}` : "";
  }
  function rqTarget() { return (document.querySelector('#rq-targets input[name="rq-t"]:checked') || {}).value || "*"; }
  function rqTick() {
    rqUpdBar();
    if (!$("rq-dlg").open) return;
    const cid = Number($("rq-chan").value), b = $("rq-send");
    if (rq.info && rq.info.connected === false) { b.disabled = true; b.textContent = "Versturen"; return; }
    const w = rqWait(cid, rqTarget());
    b.disabled = w > 0;
    b.textContent = w ? `Versturen (nog ${w} s)` : "Versturen";
  }
  setInterval(rqTick, 1000);
  async function rqLoad() {                              // wachttijden en trackers van het gekozen kanaal
    const cid = Number($("rq-chan").value);
    $("rq-targets").innerHTML = '<div class="small muted">Laden…</div>';
    rqSay($("rq-msg"), "");
    const r = await rqFetch(`/api/channels/${cid}/request`).catch((e) => ({ ok: false, j: { detail: e.message } }));
    if (!r.ok) {
      rq.info = null;
      $("rq-targets").innerHTML = "";
      rqSay($("rq-msg"), r.status === 403 ? "Je mag geen positie vragen op dit kanaal." : (r.j.detail || "Kon de wachttijden niet ophalen."), false);
      $("rq-send").disabled = true;
      return;
    }
    rq.info = { ...r.j, cid };
    const t0 = nowS();
    rq.until[`${cid}:*`] = t0 + (r.j.all_wait || 0);
    (r.j.trackers || []).forEach((t) => { rq.until[`${cid}:${t.id}`] = t0 + (t.wait || 0); });
    const cur = rqTarget();
    const list = (r.j.trackers || []).slice().sort((a, b) => String(a.alias).localeCompare(String(b.alias)));
    $("rq-targets").innerHTML = `<label class="askopt"><input type="radio" name="rq-t" value="*"> Alle trackers op dit kanaal (${list.length})</label>`
      + list.map((t) => { const tr = trackers.get(t.id) || {};
        return `<label class="askopt"><input type="radio" name="rq-t" value="${t.id}"><span class="askdot" style="background:${MT.esc(tr.color || "#888")}"></span>
          <span>${MT.esc(t.alias)}</span></label>`; }).join("");
    const radios = [...$("rq-targets").querySelectorAll('input[name="rq-t"]')];
    (radios.find((x) => x.value === cur) || radios[0]).checked = true;
    radios.forEach((x) => x.addEventListener("change", rqTick));
    if (r.j.connected === false) rqSay($("rq-msg"), "De server is niet met de mesh verbonden.", false);
    rqTick();
  }
  function rqOpen() {
    const sel = $("rq-chan");
    sel.innerHTML = chans.map((c) => `<option value="${c.id}">${MT.esc(c.name)}</option>`).join("");
    sel.value = String(prefs.kanaal && chans.some((c) => c.id === prefs.kanaal) ? prefs.kanaal : chans[0].id);
    $("rq-dlg").showModal();
    rqLoad();
  }
  async function rqSend(e) {
    e.preventDefault();
    const cid = Number($("rq-chan").value), tv = rqTarget();
    const target = tv === "*" ? "*" : Number(tv);
    if (rqWait(cid, tv) > 0) return;
    $("rq-send").disabled = true;
    const r = await rqFetch(`/api/channels/${cid}/request`, { method: "POST", body: { target } }).catch((err) => ({ ok: false, j: { detail: err.message } }));
    if (!r.ok) {
      if (r.status === 429 && r.retry) rq.until[`${cid}:${tv}`] = nowS() + r.retry;
      const why = r.status === 503 ? "De server is niet met de mesh verbonden; het verzoek kon niet weg."
        : r.status === 502 ? "Versturen mislukt; probeer het zo opnieuw."
        : r.status === 403 ? "Je mag geen positie vragen op dit kanaal."
        : r.j.detail || "Versturen mislukt.";
      rqSay($("rq-msg"), why, false);
      rqTick();
      return;
    }
    const sent = r.j.sent_at || nowS();
    const info = rq.info && rq.info.cid === cid ? rq.info : { trackers: [], all_interval: 120, tracker_interval: 30 };
    rq.until[`${cid}:${tv}`] = sent + (tv === "*" ? info.all_interval || 120 : info.tracker_interval || 30);
    rq.req = { cid, target, sent, mine: true,
      expect: target === "*" ? (info.trackers || []).map((t) => t.id) : [target], answers: new Map() };
    $("rq-dlg").close();
    rqRender();
  }
  // Antwoord: een live positie met toestand V van een tracker op dat kanaal, na het verzoek.
  function rqNote(t, p) {
    const q = rq.req;
    if (!q || p.state !== "V" || p.history === 1) return;
    if (t.channel_id !== q.cid || (q.target !== "*" && t.id !== q.target)) return;
    if (nowS() < q.sent - 5) return;
    q.answers.set(t.id, { at: nowS(), fix: p.lat != null && !p.suspect, old: vOld(p), fixTs: p.ts, lat: p.lat, lon: p.lon });
    rqRender();
    const pts = [...q.answers.values()].filter((a) => a.fix);
    if (pts.length === 1) map.flyTo({ center: [pts[0].lon, pts[0].lat], zoom: Math.max(map.getZoom(), 14) });
    else if (pts.length > 1) {
      const b = new maplibregl.LngLatBounds();
      pts.forEach((a) => b.extend([a.lon, a.lat]));
      map.fitBounds(b, { padding: 60, maxZoom: 15, duration: 600 });
    }
  }
  function rqRender() {
    const box = $("rq-panel"), q = rq.req;
    for (const [id, m] of markers) m.getElement().classList.toggle("asked", !!q && q.answers.has(id));
    if (!q) { box.hidden = true; box.innerHTML = ""; return; }
    const all = q.target === "*";
    const ids = [...new Set([...q.expect, ...q.answers.keys()])];
    const nameOf = (id) => (trackers.get(id) || {}).alias || `tracker ${id}`;
    const rows = ids.map((id) => {
      const a = q.answers.get(id), tr = trackers.get(id) || {};
      return `<li data-id="${id}"${a ? "" : ' class="wait"'}><span class="askdot" style="background:${MT.esc(tr.color || "#888")}"></span>
        <span class="nm">${MT.esc(nameOf(id))}</span>
        <span class="small">${a ? `${rqHms(a.at)} · ${!a.fix ? '<span class="warn">geen fix</span>' : a.old ? `<i class="vping old" aria-hidden="true"></i> laatst gekend, ${MT.esc(MT.ago(a.fixTs))}` : '<i class="vping" aria-hidden="true"></i> met positie'}` : "nog geen antwoord"}</span></li>`;
    }).join("");
    box.innerHTML = `<div class="askhead"><div><strong>Verzoek verstuurd om ${rqHms(q.sent)}</strong>
        <div class="small muted">${all ? `Alle trackers op ${MT.esc(rqChanName(q.cid))}` : `${MT.esc(nameOf(q.target))} op ${MT.esc(rqChanName(q.cid))}`}</div>
        ${all ? `<div class="small">${q.answers.size} van ${ids.length} ${ids.length === 1 ? "tracker" : "trackers"} geantwoord</div>` : ""}</div>
        <button type="button" class="ghost" id="rq-close" aria-label="Verzoek sluiten">✕</button></div>
      <ul>${rows || '<li class="wait">Nog geen trackers op dit kanaal; antwoorden verschijnen hier.</li>'}</ul>`;
    box.hidden = false;
    $("rq-close").addEventListener("click", () => { rq.req = null; rqRender(); });
    box.querySelectorAll("li[data-id]").forEach((li) => li.addEventListener("click", () => {
      const a = q.answers.get(Number(li.dataset.id));
      if (a && a.fix) map.flyTo({ center: [a.lon, a.lat], zoom: Math.max(map.getZoom(), 15) });
    }));
  }
  // Verzoek van iemand anders op een kanaal dat je ziet: melding, en de wachttijd ook hier laten tellen.
  function rqEvent(m) {
    const cid = m.channel_id, tv = String(m.target);
    const sent = m.sent_at || nowS();
    const keep = rq.info && rq.info.cid === cid ? rq.info : {};
    rq.until[`${cid}:${tv}`] = Math.max(rq.until[`${cid}:${tv}`] || 0, sent + (tv === "*" ? keep.all_interval || 120 : keep.tracker_interval || 30));
    const own = rq.req && rq.req.mine && rq.req.cid === cid && String(rq.req.target) === tv && Math.abs(rq.req.sent - sent) < 5;
    if (own) return;
    const who = m.by ? String(m.by) : "Iemand";
    const what = tv === "*" ? "alle trackers" : ((trackers.get(Number(tv)) || {}).alias || "een tracker");
    toast(`${who} vroeg de positie van ${what} op ${rqChanName(cid)}`);
    rqTick();
  }
  $("rq-btn").addEventListener("click", rqOpen);
  $("rq-chan").addEventListener("change", rqLoad);
  $("rq-form").addEventListener("submit", rqSend);
  $("rq-cancel").addEventListener("click", () => $("rq-dlg").close());

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
