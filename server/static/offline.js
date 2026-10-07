/* MeshTrack offline-app: posities via Bluetooth van een MeshCore-companion, zonder internet.
   De companion ontcijfert de kanaalberichten (hij kent kanaal en sleutel); deze app leest
   "T1C|..." berichten, kijkt de controletekens na als de authsleutel bekend is, bewaart alles
   in IndexedDB en tekent sporen op een kaart uit een lokaal pmtiles-bestand (OPFS). */
(function () {
  const $ = (id) => document.getElementById(id);
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const say = (el, t, ok) => { el.textContent = t || ""; el.className = "msg " + (ok ? "ok" : ok === false ? "err" : ""); };
  const ago = (ts) => {
    if (!ts) return "nooit";
    const s = Math.max(0, Math.round(Date.now() / 1000 - ts));
    return s < 60 ? `${s} s geleden` : s < 3600 ? `${Math.round(s / 60)} min geleden` : s < 86400 ? `${Math.round(s / 3600)} u geleden` : `${Math.round(s / 86400)} d geleden`;
  };
  const fmtSize = (b) => (b > 1e9 ? (b / 1e9).toFixed(1) + " GB" : Math.round(b / 1e6) + " MB");
  const STATE = { M: "rijdt/stapt", S: "stilgevallen", H: "heartbeat", N: "geen GPS-fix", E: "SOS", B: "modus/voeding", P: "handmatig", W: "wakker" };

  // ---- opslag ----------------------------------------------------------------------
  let db;
  function openDb() {
    return new Promise((res, rej) => {
      const r = indexedDB.open("mt-offline", 1);
      r.onupgradeneeded = () => {
        const d = r.result;
        const p = d.createObjectStore("pos", { keyPath: "k" });
        p.createIndex("ts", "ts");
        d.createObjectStore("meta", { keyPath: "key" });
      };
      r.onsuccess = () => res(r.result);
      r.onerror = () => rej(r.error);
    });
  }
  const tx = (store, mode) => db.transaction(store, mode).objectStore(store);
  const req = (r) => new Promise((res, rej) => { r.onsuccess = () => res(r.result); r.onerror = () => rej(r.error); });
  const metaGet = async (key, def) => { const v = await req(tx("meta", "readonly").get(key)); return v ? v.value : def; };
  const metaSet = (key, value) => req(tx("meta", "readwrite").put({ key, value }));

  let bundle = { trackers: [], channels: [] };
  const known = () => Object.fromEntries((bundle.trackers || []).map((t) => [t.pk8, t]));
  const posAll = [];                          // alle posities in het geheugen (chronologisch per tracker)

  async function loadPositions() {
    const since = Date.now() / 1000 - 7 * 86400 - 3600;
    const all = await req(tx("pos", "readonly").index("ts").getAll(IDBKeyRange.lowerBound(since)));
    posAll.length = 0;
    posAll.push(...all);
  }

  async function storePositions(list) {
    const st = tx("pos", "readwrite");
    let added = 0;
    for (const p of list) {
      const exists = await req(st.get(p.k));
      if (exists) continue;
      st.put(p);
      posAll.push(p);
      added++;
    }
    if (added) { renderAll(); }
    return added;
  }

  // ---- berichten ontcijferen ---------------------------------------------------------
  // "T1C|<pk8>|<tag>|<seq>|<state>|<lat>|<lon>|<alt>|<spd>|<crs>|<bat>|<hdop>|<age>|<mode>|<power>|<fix_ts>|<extra>"
  async function hmacTag(keyHex, body) {
    const raw = new Uint8Array(keyHex.match(/../g).map((h) => parseInt(h, 16)));
    const k = await crypto.subtle.importKey("raw", raw, { name: "HMAC", hash: "SHA-256" }, false, ["sign"]);
    const mac = new Uint8Array(await crypto.subtle.sign("HMAC", k, new TextEncoder().encode(body)));
    return [...mac.slice(0, 4)].map((b) => b.toString(16).padStart(2, "0")).join("");
  }
  function hav(a1, o1, a2, o2) {
    const R = 6371008.8, r = Math.PI / 180, dp = (a2 - a1) * r, dl = (o2 - o1) * r;
    const h = Math.sin(dp / 2) ** 2 + Math.cos(a1 * r) * Math.cos(a2 * r) * Math.sin(dl / 2) ** 2;
    return 2 * R * Math.asin(Math.min(1, Math.sqrt(h)));
  }
  function extras(field, lat, lon) {
    const out = [];
    if (!field) return out;
    if (field.startsWith("~")) {                   // compact: ~interval;dlat,dlon[@s]
      const [head, ...items] = field.split(";");
      const step = parseInt(head.slice(1), 10);
      let dt = 0, pla = lat, plo = lon;
      for (const it of items) {
        const [xy, gs] = it.split("@");
        const [a, b] = xy.split(",").map(Number);
        const gap = gs ? parseInt(gs, 10) : step;
        const nla = +(pla + a / 1e5).toFixed(5), nlo = +(plo + b / 1e5).toFixed(5);
        dt += gap;
        out.push({ dt, lat: nla, lon: nlo, spd: Math.round(hav(nla, nlo, pla, plo) / gap * 3.6) });
        pla = nla; plo = nlo;
      }
    } else {                                       // ouder: dt,dlat,dlon,spd t.o.v. het hoofdpunt
      for (const it of field.split(";")) {
        const [dt, a, b, sp] = it.split(",");
        out.push({ dt: +dt, lat: +(lat + a / 1e5).toFixed(5), lon: +(lon + b / 1e5).toFixed(5), spd: sp === "" ? null : +sp });
      }
    }
    return out;
  }

  async function decode(text, frameTs, meta) {
    const body = text.includes(": ") ? text.slice(text.indexOf(": ") + 2) : text;
    if (!body.startsWith("T1C|")) return null;
    const parts = body.split("|");
    if (parts.length < 15) return { bad: "onvolledig" };
    const [, pk, tag, ...f] = parts;
    const restStr = body.split("|").slice(3).join("|");
    const tr = known()[pk];
    let verified = null;
    if (tag !== "-" && tr && tr.authkey) verified = (await hmacTag(tr.authkey, `${pk}|${restStr}`)) === tag.toLowerCase();
    if (verified === false) return { bad: "ongeldige controletekens", pk };
    const [seq, state, la, lo, alt, spd, crs, bat, hdop, age, mode, power, fts, extra] = f;
    const lat = la === "" ? null : +la, lon = lo === "" ? null : +lo;
    const tsMain = fts ? +fts : (frameTs || Math.round(Date.now() / 1000)) - (+age || 0);
    const base = { pk, seq: +seq, bat: bat === "" ? null : +bat, mode, power, verified, own: !!meta.own, chan: meta.chan, rx: Math.round(Date.now() / 1000) };
    const out = [];
    if (lat !== null) {
      for (const e of extras(extra, lat, lon)) {
        out.push({ ...base, k: `${pk}:${tsMain - e.dt}`, ts: tsMain - e.dt, state: "M", lat: e.lat, lon: e.lon, spd: e.spd, extra: true });
      }
    }
    out.push({ ...base, k: `${pk}:${tsMain}:${state}`, ts: tsMain, state, lat, lon, alt: alt === "" ? null : +alt,
      spd: spd === "" ? null : +spd, crs: crs === "" ? null : +crs, hdop: hdop === "" ? null : +hdop });
    return { positions: out, pk, state };
  }

  // ---- Bluetooth (MeshCore companion, Nordic UART) ------------------------------------------
  const NUS = "6e400001-b5a3-f393-e0a9-e50e24dcca9e", RXC = "6e400002-b5a3-f393-e0a9-e50e24dcca9e", TXC = "6e400003-b5a3-f393-e0a9-e50e24dcca9e";
  let dev = null, rxc = null, waiter = null, syncing = false, again = false, chanNames = {}, devChans = [];
  const log = (t) => { const el = $("bt-log"); el.textContent = `${new Date().toLocaleTimeString("nl-BE")} ${t}\n` + el.textContent.slice(0, 4000); };

  function onFrame(f) {
    if (f[0] >= 0x80) {                              // push
      if (f[0] === 0x83) syncAll();
      return;
    }
    if (waiter && waiter.codes.includes(f[0])) { const w = waiter; waiter = null; w.resolve(f); }
  }
  function ask(bytes, codes, ms = 4000) {
    return new Promise((resolve, reject) => {
      waiter = { codes, resolve };
      const t = setTimeout(() => { if (waiter && waiter.resolve === resolve) { waiter = null; reject(new Error("geen antwoord van de companion")); } }, ms);
      const done = resolve;
      waiter.resolve = (f) => { clearTimeout(t); done(f); };
      const write = rxc.properties.write ? rxc.writeValue(bytes) : rxc.writeValueWithoutResponse(bytes);
      write.catch((e) => { clearTimeout(t); waiter = null; reject(e); });
    });
  }

  async function connect() {
    if (!navigator.bluetooth) { say($("bt-msg"), "Deze browser kan geen Bluetooth. Gebruik Chrome op Android of op een computer.", false); return; }
    try {
      dev = await navigator.bluetooth.requestDevice({ filters: [{ services: [NUS] }, { namePrefix: "MeshCore" }], optionalServices: [NUS] });
      dev.addEventListener("gattserverdisconnected", () => setBt(false));
      say($("bt-msg"), "Verbinden… (bij de eerste keer vraagt het toestel om te koppelen, pincode 123456)");
      const srv = await dev.gatt.connect();
      const svc = await srv.getPrimaryService(NUS);
      rxc = await svc.getCharacteristic(RXC);
      const txc = await svc.getCharacteristic(TXC);
      txc.addEventListener("characteristicvaluechanged", (e) => { const v = e.target.value; onFrame(new Uint8Array(v.buffer.slice(v.byteOffset, v.byteOffset + v.byteLength))); });
      await txc.startNotifications();
      await ask(new Uint8Array([22, 3]), [13]);                                  // apparaatinfo, protocol v3
      const name = new TextEncoder().encode("MeshTrack offline");
      const self = await ask(new Uint8Array([1, 3, 0, 0, 0, 0, 0, 0, ...name]), [5]);
      const devName = new TextDecoder().decode(self.slice(58)).replace(/\0.*$/, "") || dev.name || "companion";
      setBt(true, devName);
      say($("bt-msg"), "Verbonden.", true);
      await readChannels();
      await syncAll();
    } catch (e) { say($("bt-msg"), `Verbinden mislukt: ${e.message}`, false); setBt(false); }
  }
  function setBt(on, name) {
    $("btpill").textContent = on ? `Bluetooth: ${name || "verbonden"}` : "Bluetooth: uit";
    $("btpill").className = "pill" + (on ? " ok" : "");
    $("bt-connect").hidden = on; $("bt-disconnect").hidden = !on; $("bt-sync").hidden = !on;
    $("bt-info").textContent = on ? `Verbonden met ${name}.` : "";
    if (!on) { rxc = null; devChans = []; renderDevChans(); }
  }

  async function syncAll() {
    if (!rxc) return;
    if (syncing) { again = true; return; }
    syncing = true; again = false;
    let n = 0, stored = 0, rejected = 0;
    try {
      for (let i = 0; i < 300; i++) {
        const f = await ask(new Uint8Array([10]), [16, 17, 7, 8, 10], 5000);
        if (f[0] === 10) break;                         // geen berichten meer
        n++;
        if (f[0] !== 17 && f[0] !== 8) continue;       // enkel kanaalberichten
        const v3 = f[0] === 17;
        const o = v3 ? 4 : 1;
        const own = v3 && f[2] === 1;
        const chan = f[o];
        const ts = new DataView(f.buffer).getUint32(o + 3, true);
        const text = new TextDecoder().decode(f.slice(o + 7)).replace(/\0+$/, "");
        const r = await decode(text, ts, { own, chan: chan === 0xFF ? "DM" : (chanNames[chan] || `kanaal ${chan}`) });
        if (!r) continue;
        if (r.bad) { rejected++; log(`geweigerd (${r.bad}) ${r.pk || ""}`); continue; }
        stored += await storePositions(r.positions);
        const tr = known()[r.pk];
        log(`${own ? "eigen · " : ""}${tr ? tr.alias : r.pk} ${STATE[r.state] || r.state} (+${r.positions.length - 1} punten)`);
      }
    } catch (e) { say($("bt-msg"), e.message, false); }
    finally { syncing = false; }
    if (again && rxc) setTimeout(syncAll, 50);
    if (n) say($("bt-msg"), `${n} bericht(en) gelezen, ${stored} nieuwe positie(s)${rejected ? `, ${rejected} geweigerd` : ""}.`, true);
  }

  async function readChannels() {
    devChans = []; chanNames = {};
    for (let i = 0; i < 40; i++) {
      let f;
      try { f = await ask(new Uint8Array([31, i]), [18, 1], 2500); } catch (_) { break; }
      if (f[0] !== 18) break;
      const name = new TextDecoder().decode(f.slice(2, 34)).replace(/\0.*$/, "");
      const secret = [...f.slice(34, 50)].map((b) => b.toString(16).padStart(2, "0")).join("");
      if (name) { devChans.push({ idx: i, name, secret }); chanNames[i] = name; }
    }
    renderDevChans();
  }
  function renderDevChans() {
    $("ch-dev").innerHTML = !rxc ? "Niet verbonden." : devChans.length
      ? devChans.map((c) => `<div>${c.idx}: <strong>${esc(c.name)}</strong>${(bundle.channels || []).some((s) => s.secret === c.secret) ? ' <span class="pill ok">MeshTrack</span>' : ""}</div>`).join("")
      : "Geen kanalen op de companion.";
    renderSrvChans();
  }

  async function sha16(name) {
    const h = new Uint8Array(await crypto.subtle.digest("SHA-256", new TextEncoder().encode(name)));
    return [...h.slice(0, 16)].map((b) => b.toString(16).padStart(2, "0")).join("");
  }
  async function addChannel(name, secret) {
    if (!rxc) { say($("ch-msg"), "Verbind eerst met een companion (tabblad Verbinding).", false); return; }
    name = (name || "").trim();
    secret = (secret || "").replace(/[\s-]/g, "").toLowerCase();
    if (!name) { say($("ch-msg"), "Geef een naam.", false); return; }
    if (!secret && name.startsWith("#")) secret = await sha16(name);
    if (!/^[0-9a-f]{32}$/.test(secret)) { say($("ch-msg"), "De sleutel moet 32 hex-tekens zijn (of laat leeg bij een #kanaal).", false); return; }
    const have = devChans.find((c) => c.secret === secret);
    if (have) { say($("ch-msg"), `Dit kanaal staat al op de companion (nummer ${have.idx}).`, true); return; }
    const used = new Set(devChans.map((c) => c.idx));
    let idx = 1; while (used.has(idx) && idx < 39) idx++;
    const nb = new Uint8Array(32); nb.set(new TextEncoder().encode(name).slice(0, 31));
    const sb = new Uint8Array(secret.match(/../g).map((h) => parseInt(h, 16)));
    try {
      const f = await ask(new Uint8Array([32, idx, ...nb, ...sb]), [0, 1]);
      if (f[0] !== 0) throw new Error("de companion weigerde het kanaal");
      say($("ch-msg"), `Kanaal ${name} staat op de companion (nummer ${idx}).`, true);
      await readChannels();
    } catch (e) { say($("ch-msg"), e.message, false); }
  }
  function renderSrvChans() {
    const ch = bundle.channels || [];
    $("ch-srv").innerHTML = ch.length ? ch.map((c, i) => `<div class="row" style="margin:4px 0;align-items:center"><span style="flex:1"><strong>${esc(c.name)}</strong>
      ${devChans.some((d) => d.secret === c.secret) ? '<span class="pill ok">op de companion</span>' : ""}</span>
      <button type="button" data-srvch="${i}">Op de companion zetten</button></div>`).join("")
      : "Geen kanalen (of nog niet opgehaald van de server).";
    $("ch-srv").querySelectorAll("[data-srvch]").forEach((b) => b.addEventListener("click", () => { const c = ch[Number(b.dataset.srvch)]; addChannel(c.name, c.secret); }));
  }

  // QR: meshcore://channel/add?name=...&secret=...
  function useQr(text) {
    try {
      const u = new URL(text);
      const name = u.searchParams.get("name"), secret = u.searchParams.get("secret");
      if (!name) throw new Error();
      $("ch-name").value = name; $("ch-secret").value = secret || "";
      say($("ch-msg"), `Gelezen: ${name}. Klik op "Op de companion zetten".`, true);
    } catch (_) { say($("ch-msg"), "Dit is geen kanaal-QR-code van MeshCore.", false); }
  }
  async function scanQr() {
    if (!("BarcodeDetector" in window)) { say($("ch-msg"), "Deze browser kan geen QR-codes lezen. Vul naam en sleutel in.", false); return; }
    const det = new BarcodeDetector({ formats: ["qr_code"] });
    const v = $("qrvideo");
    let stream;
    try { stream = await navigator.mediaDevices.getUserMedia({ video: { facingMode: "environment" } }); }
    catch (e) { say($("ch-msg"), `Camera niet beschikbaar: ${e.message}`, false); return; }
    v.srcObject = stream; v.hidden = false; await v.play();
    const stop = () => { stream.getTracks().forEach((t) => t.stop()); v.hidden = true; };
    const t0 = Date.now();
    const loop = async () => {
      if (Date.now() - t0 > 30000) { stop(); say($("ch-msg"), "Geen QR-code gevonden.", false); return; }
      try { const r = await det.detect(v); if (r.length) { stop(); useQr(r[0].rawValue); return; } } catch (_) {}
      requestAnimationFrame(loop);
    };
    loop();
  }
  async function qrFromFile(file) {
    if (!("BarcodeDetector" in window)) { say($("ch-msg"), "Deze browser kan geen QR-codes lezen. Vul naam en sleutel in.", false); return; }
    const bmp = await createImageBitmap(file);
    const r = await new BarcodeDetector({ formats: ["qr_code"] }).detect(bmp);
    if (r.length) useQr(r[0].rawValue); else say($("ch-msg"), "Geen QR-code gevonden op de foto.", false);
  }

  // ---- kaart ---------------------------------------------------------------------------
  let map = null, markers = new Map(), hours = 24, trackOn = true;
  const dark = () => matchMedia("(prefers-color-scheme: dark)").matches;

  async function opfs() { return navigator.storage && navigator.storage.getDirectory ? navigator.storage.getDirectory() : null; }
  async function mapFile(key) {
    const root = await opfs();
    if (!root) return null;
    try { const h = await root.getFileHandle(`${key}.pmtiles`); return await h.getFile(); } catch (_) { return null; }
  }

  async function initMap() {
    const active = await metaGet("activeMap", null);
    const file = active ? await mapFile(active) : null;
    let style;
    if (file) style = MTBasemap.offlineStyle(dark(), new File([file], `${active}.pmtiles`));
    else if (navigator.onLine) style = MTBasemap.style(dark(), true);
    else style = MTBasemap.style(dark(), false);
    const view = await metaGet("view", { center: [5.33, 50.93], zoom: 10 });
    if (map) map.remove();
    markers.forEach((m) => m.remove()); markers = new Map();
    map = new maplibregl.Map({ container: "omap", style, center: view.center, zoom: view.zoom, attributionControl: { compact: true } });
    map.addControl(new maplibregl.NavigationControl({ showCompass: false }), "top-right");
    map.on("moveend", () => metaSet("view", { center: map.getCenter().toArray(), zoom: map.getZoom() }));
    map.on("load", () => {
      map.addSource("trk", { type: "geojson", data: { type: "FeatureCollection", features: [] } });
      map.addLayer({ id: "trk", type: "line", source: "trk", paint: { "line-color": ["get", "color"], "line-width": 3, "line-opacity": 0.85 } });
      map.addSource("pts", { type: "geojson", data: { type: "FeatureCollection", features: [] } });
      map.addLayer({ id: "pts", type: "circle", source: "pts", paint: { "circle-radius": 3, "circle-color": ["get", "color"], "circle-stroke-color": "#fff", "circle-stroke-width": 1 } });
      map.on("click", "pts", (e) => {
        const p = e.features[0].properties;
        new maplibregl.Popup({ closeButton: false }).setLngLat(e.lngLat).setHTML(`<strong>${esc(p.name)}</strong><br>${new Date(p.ts * 1000).toLocaleString("nl-BE")}${p.spd !== "null" && p.spd != null ? `<br>${p.spd} km/u` : ""}`).addTo(map);
      });
      renderAll();
    });
    if (!file && !navigator.onLine) say($("maps-msg"), "Geen offline kaart op dit toestel: download er een terwijl je internet hebt.", false);
  }

  function colorOf(pk) { const t = known()[pk]; if (t) return t.color; let h = 0; for (const c of pk) h = (h * 31 + c.charCodeAt(0)) % 360; return `hsl(${h},70%,45%)`; }
  function nameOf(pk) { const t = known()[pk]; return t ? t.alias : `tracker ${pk}`; }

  function renderAll() {
    const now = Date.now() / 1000, since = now - hours * 3600;
    const by = {};
    for (const p of posAll) if (p.lat != null) (by[p.pk] = by[p.pk] || []).push(p);
    const lines = [], pts = [];
    for (const [pk, arr] of Object.entries(by)) {
      arr.sort((a, b) => a.ts - b.ts);
      const sel = arr.filter((p) => p.ts >= since);
      const color = colorOf(pk), name = nameOf(pk);
      if (trackOn && sel.length > 1) lines.push({ type: "Feature", properties: { color }, geometry: { type: "LineString", coordinates: sel.map((p) => [p.lon, p.lat]) } });
      if (trackOn) for (const p of sel) pts.push({ type: "Feature", properties: { color, name, ts: p.ts, spd: p.spd }, geometry: { type: "Point", coordinates: [p.lon, p.lat] } });
      const last = arr[arr.length - 1];
      upsertMarker(pk, last, color, name);
    }
    if (map && map.getSource("trk")) {
      map.getSource("trk").setData({ type: "FeatureCollection", features: lines });
      map.getSource("pts").setData({ type: "FeatureCollection", features: pts });
    }
    renderList(by);
  }
  function upsertMarker(pk, p, color, name) {
    if (!map) return;
    let m = markers.get(pk);
    if (!m) {
      const el = document.createElement("div");
      el.innerHTML = `<div style="position:relative"><div class="omark"></div><span class="olabel"></span></div>`;
      m = new maplibregl.Marker({ element: el }).setLngLat([p.lon, p.lat]).addTo(map);
      markers.set(pk, m);
    }
    const t = known()[pk];
    const el = m.getElement();
    el.querySelector(".omark").style.background = color;
    el.querySelector(".omark").innerHTML = t && t.icon && window.MTIcons ? MTIcons.svg(t.icon) : "";
    el.querySelector(".olabel").textContent = name;
    m.setLngLat([p.lon, p.lat]);
  }
  function renderList(by) {
    const items = Object.entries(by).map(([pk, arr]) => ({ pk, last: arr[arr.length - 1], n: arr.length }))
      .sort((a, b) => b.last.ts - a.last.ts);
    $("o-list").innerHTML = items.length ? items.map(({ pk, last, n }) => {
      const t = known()[pk];
      const ver = last.verified === true ? '<span class="pill ok">gecontroleerd</span>' : last.verified === null ? '<span class="pill">niet gecontroleerd</span>' : "";
      return `<div class="otrk" data-pk="${esc(pk)}"><span class="omark" style="background:${esc(colorOf(pk))}">${t && t.icon && window.MTIcons ? MTIcons.svg(t.icon) : ""}</span>
        <div class="body"><div class="nm">${esc(nameOf(pk))} ${last.own ? '<span class="pill">eigen</span>' : ""} ${last.state === "E" ? '<span class="pill sos">SOS</span>' : ""}</div>
        <div class="sub">${esc(ago(last.ts))} · ${esc(STATE[last.state] || last.state || "")}${last.bat != null ? " · " + last.bat + " %" : ""} · ${esc(last.chan || "")} · ${n} punten</div>
        <div class="sub">${ver}</div></div></div>`;
    }).join("") : '<div class="empty">Nog geen posities. Verbind met een companion (tabblad Verbinding).</div>';
    $("o-list").querySelectorAll(".otrk").forEach((el) => el.addEventListener("click", () => {
      const arr = by[el.dataset.pk]; const p = arr[arr.length - 1];
      if (map) map.flyTo({ center: [p.lon, p.lat], zoom: Math.max(map.getZoom(), 14) });
    }));
  }
  $("o-hours").querySelectorAll("[data-h]").forEach((b) => b.addEventListener("click", () => {
    hours = Number(b.dataset.h); metaSet("hours", hours);
    $("o-hours").querySelectorAll("button").forEach((x) => x.classList.toggle("on", x === b));
    renderAll();
  }));
  $("o-trackon").addEventListener("change", () => { trackOn = $("o-trackon").checked; renderAll(); });

  // ---- kaarten downloaden (OPFS) -----------------------------------------------------------
  async function renderMaps() {
    const root = await opfs();
    const stored = [];
    if (root) for await (const [name, h] of root.entries()) if (name.endsWith(".pmtiles")) stored.push({ key: name.replace(/\.pmtiles$/, ""), size: (await h.getFile()).size });
    const active = await metaGet("activeMap", null);
    const names = await metaGet("mapNames", {});
    $("maps-stored").innerHTML = stored.length ? `<h3 class="small">Op dit toestel</h3>` + stored.map((m) => `<div class="mapbox">
      <strong>${esc(names[m.key] || m.key)}</strong> <span class="muted small">${fmtSize(m.size)}</span> ${m.key === active ? '<span class="pill ok">in gebruik</span>' : ""}
      <div class="row">${m.key !== active ? `<button type="button" data-use="${esc(m.key)}">Gebruiken</button>` : ""}<button type="button" class="danger" data-delmap="${esc(m.key)}">Verwijderen</button></div></div>`).join("")
      : '<div class="small muted">Nog geen offline kaart op dit toestel.</div>';
    $("maps-stored").querySelectorAll("[data-use]").forEach((b) => b.addEventListener("click", async () => { await metaSet("activeMap", b.dataset.use); await initMap(); renderMaps(); }));
    $("maps-stored").querySelectorAll("[data-delmap]").forEach((b) => b.addEventListener("click", async () => {
      if (!confirm("Deze kaart van het toestel verwijderen?")) return;
      await root.removeEntry(`${b.dataset.delmap}.pmtiles`);
      if (active === b.dataset.delmap) { await metaSet("activeMap", null); await initMap(); }
      renderMaps();
    }));
    if (navigator.storage && navigator.storage.estimate) {
      const e = await navigator.storage.estimate();
      $("maps-quota").textContent = `Opslag in gebruik: ${fmtSize(e.usage || 0)} van ongeveer ${fmtSize(e.quota || 0)}.`;
    }
    if (!navigator.onLine) return;
    try {
      const r = await fetch("/api/offline/maps", { credentials: "same-origin" });
      if (r.status === 401) { $("maps-avail").innerHTML = 'Log in om kaarten te downloaden. <a href="/login?next=/offline">Inloggen</a>'; return; }
      const list = await r.json();
      $("maps-avail").innerHTML = list.map((m) => `<div class="mapbox"><strong>${esc(m.name)}</strong> <span class="muted small">${fmtSize(m.size)}</span>
        <div class="muted small">${esc(m.description)}</div>
        <div class="row"><button type="button" data-dl="${esc(m.key)}">${stored.some((s) => s.key === m.key) ? "Opnieuw downloaden" : "Downloaden"}</button></div>
        <div class="bar" hidden id="bar-${esc(m.key)}"><span></span></div></div>`).join("") || "Geen kaarten beschikbaar.";
      $("maps-avail").querySelectorAll("[data-dl]").forEach((b) => b.addEventListener("click", () => download(list.find((m) => m.key === b.dataset.dl), b)));
    } catch (_) { $("maps-avail").textContent = "Server niet bereikbaar."; }
  }
  async function download(m, btn) {
    const root = await opfs();
    if (!root) { say($("maps-msg"), "Deze browser kan geen bestanden bewaren.", false); return; }
    if (navigator.storage.persist) navigator.storage.persist();
    btn.disabled = true;
    const bar = $(`bar-${m.key}`); bar.hidden = false;
    try {
      const r = await fetch(m.url, { credentials: "same-origin" });
      if (!r.ok) throw new Error(`download mislukt (${r.status})`);
      const h = await root.getFileHandle(`${m.key}.pmtiles.part`, { create: true });
      const w = await h.createWritable();
      const rd = r.body.getReader();
      let got = 0;
      for (;;) {
        const { value, done } = await rd.read();
        if (done) break;
        await w.write(value);
        got += value.length;
        bar.querySelector("span").style.width = `${Math.round(got / m.size * 100)}%`;
      }
      await w.close();
      try { await root.removeEntry(`${m.key}.pmtiles`); } catch (_) {}
      await h.move(`${m.key}.pmtiles`);
      const names = await metaGet("mapNames", {}); names[m.key] = m.name; await metaSet("mapNames", names);
      if (!(await metaGet("activeMap", null))) await metaSet("activeMap", m.key);
      if (navigator.serviceWorker && navigator.serviceWorker.controller) navigator.serviceWorker.controller.postMessage({ type: "prefetch" });
      say($("maps-msg"), `${m.name} staat op dit toestel.`, true);
      await initMap();
    } catch (e) { say($("maps-msg"), e.message, false); try { await root.removeEntry(`${m.key}.pmtiles.part`); } catch (_) {} }
    btn.disabled = false;
    renderMaps();
  }

  // ---- gegevens ----------------------------------------------------------------------------
  async function fetchBundle() {
    try {
      const r = await fetch("/api/offline/bundle", { credentials: "same-origin" });
      if (r.status === 401) { $("d-login").hidden = false; say($("d-msg"), "Log eerst in.", false); return; }
      if (!r.ok) throw new Error(r.statusText);
      bundle = await r.json();
      await metaSet("bundle", bundle);
      say($("d-msg"), `${bundle.trackers.length} trackers en ${bundle.channels.length} kanalen opgehaald.`, true);
      renderBundleInfo(); renderSrvChans(); renderAll();
    } catch (e) { say($("d-msg"), `Ophalen mislukt: ${e.message}`, false); }
  }
  function renderBundleInfo() {
    const nKeys = (bundle.trackers || []).filter((t) => t.authkey).length;
    $("d-bundleinfo").textContent = bundle.ts ? `Laatst opgehaald ${new Date(bundle.ts * 1000).toLocaleString("nl-BE")} door ${bundle.user}: ${bundle.trackers.length} trackers (${nKeys} met authsleutel), ${bundle.channels.length} kanalen.` : "Nog niet opgehaald.";
  }
  async function renderStats() {
    const n = await req(tx("pos", "readonly").count());
    const pks = new Set(posAll.map((p) => p.pk));
    $("d-stats").textContent = `${n} posities bewaard van ${pks.size} tracker(s).`;
  }
  $("d-bundle").addEventListener("click", fetchBundle);
  $("d-clear").addEventListener("click", async () => {
    if (!confirm("Alle bewaarde posities van dit toestel wissen?")) return;
    await req(tx("pos", "readwrite").clear());
    posAll.length = 0; markers.forEach((m) => m.remove()); markers = new Map();
    renderAll(); renderStats();
  });
  $("d-export").addEventListener("click", async () => {
    const all = await req(tx("pos", "readonly").getAll());
    const rows = [["tracker", "naam", "tijd", "toestand", "lat", "lon", "km/u", "batterij", "via", "gecontroleerd"]]
      .concat(all.sort((a, b) => a.ts - b.ts).map((p) => [p.pk, nameOf(p.pk), new Date(p.ts * 1000).toISOString(), p.state, p.lat, p.lon, p.spd ?? "", p.bat ?? "", p.chan || "", p.verified === true ? "ja" : p.verified === null ? "onbekend" : "nee"]));
    const blob = new Blob([rows.map((r) => r.join(";")).join("\n")], { type: "text/csv" });
    const a = document.createElement("a"); a.href = URL.createObjectURL(blob); a.download = `meshtrack-offline-${new Date().toISOString().slice(0, 10)}.csv`; a.click();
  });

  // ---- tabbladen en start --------------------------------------------------------------------
  const tabs = [...document.querySelectorAll("#otabs .tab")];
  function show(name) {
    tabs.forEach((b) => b.classList.toggle("on", b.dataset.tab === name));
    document.querySelectorAll("#opanel .pane").forEach((p) => { p.hidden = p.id !== "pane-" + name; });
    if (name === "maps") renderMaps();
    if (name === "data") { renderBundleInfo(); renderStats(); }
  }
  tabs.forEach((b) => b.addEventListener("click", () => show(b.dataset.tab)));
  function net() { const on = navigator.onLine; $("net").textContent = on ? "online" : "offline"; $("net").className = "pill netpill " + (on ? "on" : "off"); }
  addEventListener("online", net); addEventListener("offline", net);

  $("bt-connect").addEventListener("click", connect);
  $("bt-disconnect").addEventListener("click", () => { if (dev && dev.gatt.connected) dev.gatt.disconnect(); setBt(false); });
  $("bt-sync").addEventListener("click", syncAll);
  $("ch-add").addEventListener("click", () => addChannel($("ch-name").value, $("ch-secret").value));
  $("ch-scan").addEventListener("click", scanQr);
  $("ch-file").addEventListener("change", (e) => { const f = e.target.files[0]; e.target.value = ""; if (f) qrFromFile(f); });

  // Voor tests en foutzoeken: een bericht zoals de companion het doorgeeft verwerken.
  window.MTOffline = {
    decode,
    async ingest(text, ts, meta) { const r = await decode(text, ts, meta || {}); return r && r.positions ? storePositions(r.positions) : r; },
  };

  (async () => {
    net();
    if ("serviceWorker" in navigator) navigator.serviceWorker.register("/offline-sw.js", { scope: "/offline" }).catch(() => {});
    db = await openDb();
    bundle = await metaGet("bundle", { trackers: [], channels: [] });
    hours = await metaGet("hours", 24);
    $("o-hours").querySelectorAll("button").forEach((x) => x.classList.toggle("on", Number(x.dataset.h) === hours));
    await loadPositions();
    show("trk");
    await initMap();
    renderSrvChans();
    if (navigator.onLine && !bundle.ts) fetchBundle();
    setInterval(renderAll, 30000);
  })();
})();
