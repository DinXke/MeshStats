/* Kaartpagina: trackers live op een offline vectorkaart. */
(async function () {
  const dark = window.matchMedia("(prefers-color-scheme: dark)");
  const status = await MT.api("/api/status");
  MT.meshPill(document.getElementById("mesh"), status.mesh);

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
  const tracks = new Map();     // id -> [[lon,lat],...]
  const markers = new Map();    // id -> maplibregl.Marker
  let selected = null;
  const hoursSel = document.getElementById("hours");

  // ---- sporen ----------------------------------------------------------------
  function trackData() {
    const features = [];
    for (const [id, pts] of tracks) {
      const t = trackers.get(id);
      if (!t || pts.length < 2) continue;
      features.push({ type: "Feature", properties: { color: t.color, sel: id === selected ? 1 : 0 },
                      geometry: { type: "LineString", coordinates: pts } });
    }
    return { type: "FeatureCollection", features };
  }

  function addTrackLayers() {
    if (map.getSource("tracks")) return;
    map.addSource("tracks", { type: "geojson", data: trackData() });
    map.addLayer({ id: "tracks-casing", type: "line", source: "tracks",
      layout: { "line-join": "round", "line-cap": "round" },
      paint: { "line-color": dark.matches ? "#000" : "#fff", "line-width": ["case", ["==", ["get", "sel"], 1], 7, 5], "line-opacity": 0.7 } });
    map.addLayer({ id: "tracks", type: "line", source: "tracks",
      layout: { "line-join": "round", "line-cap": "round" },
      paint: { "line-color": ["get", "color"], "line-width": ["case", ["==", ["get", "sel"], 1], 4, 2.5] } });
  }

  function refreshTracks() {
    const src = map.getSource("tracks");
    if (src) src.setData(trackData());
  }

  async function loadTrack(id) {
    const h = Number(hoursSel.value);
    if (!h) { tracks.set(id, []); return; }
    const pts = await MT.api(`/api/trackers/${id}/track?hours=${h}`);
    tracks.set(id, pts.filter((p) => !p.suspect).map((p) => [p.lon, p.lat]));
  }

  // ---- markers en lijst -------------------------------------------------------
  function popupHtml(t) {
    const st = MT.STATE[t.last_state] || t.last_state || "–";
    const parts = [
      `<strong>${MT.esc(t.alias)}</strong>`,
      `${MT.esc(st)}${t.last_mode ? " · " + MT.esc(MT.MODE[t.last_mode] || t.last_mode) : ""}`,
      t.last_spd != null ? `${t.last_spd} km/u` : null,
      t.last_bat != null ? `batterij ${t.last_bat}%` : null,
      `laatst gehoord ${MT.ago(t.last_rx)}`,
      t.last_ts ? `positie ${MT.ago(t.last_ts)}` : null,
      t.last_snr != null ? `SNR ${t.last_snr} dB${t.last_path_len != null ? ", " + t.last_path_len + " hops" : ""}` : null,
      t.last_lat != null ? `<span class="mono">${t.last_lat.toFixed(5)}, ${t.last_lon.toFixed(5)}</span>` : null,
    ];
    return parts.filter(Boolean).join("<br>");
  }

  function upsertMarker(t) {
    let m = markers.get(t.id);
    if (t.last_lat == null || !t.active) {
      if (m) { m.remove(); markers.delete(t.id); }
      return;
    }
    if (!m) {
      const el = document.createElement("div");
      el.className = "marker";
      el.innerHTML = '<span class="mlabel"></span>';
      el.addEventListener("click", (e) => { e.stopPropagation(); select(t.id, false); });
      m = new maplibregl.Marker({ element: el }).setLngLat([t.last_lon, t.last_lat])
        .setPopup(new maplibregl.Popup({ offset: 14, closeButton: false })).addTo(map);
      markers.set(t.id, m);
    }
    const el = m.getElement();
    el.style.background = t.color;
    el.classList.toggle("stale", t.stale);
    el.classList.toggle("sos", t.last_state === "E");
    el.querySelector(".mlabel").textContent = t.alias;
    m.setLngLat([t.last_lon, t.last_lat]);
    m.getPopup().setHTML(popupHtml(t));
  }

  function renderList() {
    const list = document.getElementById("list");
    const items = [...trackers.values()].filter((t) => t.active)
      .sort((a, b) => a.alias.localeCompare(b.alias));
    if (!items.length) {
      list.innerHTML = '<div class="empty">Nog geen trackers. Voeg er een toe via <a href="/admin">Trackers</a>.</div>';
      return;
    }
    list.innerHTML = items.map((t) => {
      const sos = t.last_state === "E" ? ' <span class="pill sos">SOS</span>' : "";
      const meta = [MT.STATE[t.last_state] || "nog niets ontvangen",
                    t.last_bat != null ? `${t.last_bat}%` : null,
                    t.last_spd ? `${t.last_spd} km/u` : null].filter(Boolean).join(" · ");
      return `<div class="trk${t.stale ? " stale" : ""}${t.id === selected ? " sel" : ""}" data-id="${t.id}">
        <span class="dot" style="background:${MT.esc(t.color)};margin-top:5px"></span>
        <div><div class="name">${MT.esc(t.alias)}${sos}</div>
        <div class="meta">${MT.esc(meta)}</div>
        <div class="meta">${MT.ago(t.last_rx)}</div></div></div>`;
    }).join("");
    list.querySelectorAll(".trk").forEach((el) => el.addEventListener("click", () => select(Number(el.dataset.id), true)));
  }

  function select(id, fly) {
    selected = id;
    const t = trackers.get(id);
    const m = markers.get(id);
    if (t && t.last_lat != null && fly) map.flyTo({ center: [t.last_lon, t.last_lat], zoom: Math.max(map.getZoom(), 14) });
    if (m && !m.getPopup().isOpen()) m.togglePopup();
    renderList();
    refreshTracks();
  }

  async function loadAll() {
    const list = await MT.api("/api/trackers");
    trackers.clear();
    list.forEach((t) => trackers.set(t.id, t));
    await Promise.all(list.filter((t) => t.active).map((t) => loadTrack(t.id)));
    for (const id of [...markers.keys()]) if (!trackers.has(id)) { markers.get(id).remove(); markers.delete(id); }
    list.forEach(upsertMarker);
    renderList();
    refreshTracks();
  }

  map.on("load", async () => {
    addTrackLayers();
    await loadAll();
    const withPos = [...trackers.values()].filter((t) => t.active && t.last_lat != null);
    if (withPos.length > 1) {
      const b = new maplibregl.LngLatBounds();
      withPos.forEach((t) => b.extend([t.last_lon, t.last_lat]));
      map.fitBounds(b, { padding: 80, maxZoom: 14, duration: 0 });
    } else if (withPos.length === 1) {
      map.jumpTo({ center: [withPos[0].last_lon, withPos[0].last_lat], zoom: 13 });
    }
  });
  map.on("style.load", () => { addTrackLayers(); refreshTracks(); });
  dark.addEventListener("change", () => map.setStyle(MTBasemap.style(dark.matches, status.tiles)));
  hoursSel.addEventListener("change", async () => {
    await Promise.all([...trackers.keys()].map(loadTrack));
    refreshTracks();
  });

  // ---- live ---------------------------------------------------------------------
  MT.live((msg) => {
    if (msg.type === "mesh") MT.meshPill(document.getElementById("mesh"), msg.mesh);
    if (msg.type === "position") {
      const t = msg.tracker;
      trackers.set(t.id, t);
      const p = msg.position;
      if (p.lat != null && !p.suspect && Number(hoursSel.value)) {
        if (!tracks.has(t.id)) tracks.set(t.id, []);
        tracks.get(t.id).push([p.lon, p.lat]);
      }
      upsertMarker(t);
      renderList();
      refreshTracks();
    }
    if (msg.type === "tracker" || msg.type === "tracker_deleted") loadAll();
  });

  // "x min geleden" actueel houden
  setInterval(() => { renderList(); for (const t of trackers.values()) upsertMarker(t); }, 30000);
})();
