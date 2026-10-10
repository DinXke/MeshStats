/* MeshTrack offline-app: posities via Bluetooth van een MeshCore-companion, zonder internet.
   De companion ontcijfert de kanaalberichten (hij kent kanaal en sleutel); deze app leest
   "T1C|..." berichten, bewaart alles in IndexedDB en tekent sporen op een kaart uit een lokaal
   pmtiles-bestand (OPFS). Van de server komen alleen de kaarten: niets uit de MeshTrack-database.
   Namen komen uit de contactenlijst van de companion. */
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
  const V_OLD_S = 120;                              // ouder dan 2 min: "laatst gekend"
  const STATE = { M: "rijdt/stapt", S: "stilgevallen", H: "heartbeat", N: "geen GPS-fix", E: "SOS", B: "modus/voeding", P: "handmatig", W: "wakker", L: "gelogd punt (SlowTrack)", Q: "ingehaald punt (FIFO)", V: "op verzoek" };

  // ---- opslag ----------------------------------------------------------------------
  let db;
  function openDb() {
    return new Promise((res, rej) => {
      const r = indexedDB.open("mt-offline", 2);
      r.onupgradeneeded = (e) => {
        const d = r.result;
        if (e.oldVersion < 1) {
          const p = d.createObjectStore("pos", { keyPath: "k" });
          p.createIndex("ts", "ts");
          d.createObjectStore("meta", { keyPath: "key" });
        }
        if (e.oldVersion < 2) d.createObjectStore("chat", { keyPath: "k" }).createIndex("ts", "ts");   // 0.9.1
      };
      r.onsuccess = () => res(r.result);
      r.onerror = () => rej(r.error);
    });
  }
  const tx = (store, mode) => db.transaction(store, mode).objectStore(store);
  const req = (r) => new Promise((res, rej) => { r.onsuccess = () => res(r.result); r.onerror = () => rej(r.error); });
  const metaGet = async (key, def) => { const v = await req(tx("meta", "readonly").get(key)); return v ? v.value : def; };
  const metaSet = (key, value) => req(tx("meta", "readwrite").put({ key, value }));

  let contactNames = {};                      // pk8 -> naam, uit de contacten van de companion
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
  function hav(a1, o1, a2, o2) {
    const R = 6371008.8, r = Math.PI / 180, dp = (a2 - a1) * r, dl = (o2 - o1) * r;
    const h = Math.sin(dp / 2) ** 2 + Math.cos(a1 * r) * Math.cos(a2 * r) * Math.sin(dl / 2) ** 2;
    return 2 * R * Math.asin(Math.min(1, Math.sqrt(h)));
  }
  // Binair (firmware 0.9.0, o.a. Q-berichten): "B" + base64url zonder opvulling. Punten van nieuw naar oud,
  // elk t.o.v. het vorige (te beginnen bij het hoofdpunt), als drie LEB128-varints: dt in seconden (unsigned),
  // dlat en dlon in 1e-5 graden (zigzag).
  function extrasBin(b64, lat, lon) {
    const out = [];
    let bytes;
    try {
      const s = b64.replace(/-/g, "+").replace(/_/g, "/");
      const bin = atob(s + "=".repeat((4 - (s.length % 4)) % 4));
      bytes = Uint8Array.from(bin, (c) => c.charCodeAt(0));
    } catch (_) { return out; }
    let i = 0;
    const uv = () => {                              // LEB128 zonder teken; null als de bytes op zijn
      let v = 0, mul = 1;
      for (;;) {
        if (i >= bytes.length) return null;
        const b = bytes[i++];
        v += (b & 0x7f) * mul;
        if (!(b & 0x80)) return v;
        mul *= 128;
        if (mul > 2 ** 49) return null;             // onzin: afbreken
      }
    };
    const zz = (v) => (v % 2 ? -(v + 1) / 2 : v / 2);
    let dt = 0, pla = lat, plo = lon;
    while (i < bytes.length && out.length < 200) {
      const g = uv(), a = uv(), b = uv();
      if (g === null || a === null || b === null) break;
      const nla = +(pla + zz(a) / 1e5).toFixed(5), nlo = +(plo + zz(b) / 1e5).toFixed(5);
      dt += g;
      out.push({ dt, lat: nla, lon: nlo, spd: g > 0 ? Math.round(hav(nla, nlo, pla, plo) / g * 3.6) : null });
      pla = nla; plo = nlo;
    }
    return out;
  }
  function extras(field, lat, lon) {
    const out = [];
    if (!field) return out;
    if (field.startsWith("B")) return extrasBin(field.slice(1), lat, lon);
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
    const [, pk0, , ...f] = parts;                 // controletekens: alleen de server kan ze nakijken
    const pk = pk0.toLowerCase();
    const [seq, state, la, lo, alt, spd, crs, bat, hdop, age, mode, power, fts, extra] = f;
    const lat = la === "" ? null : +la, lon = lo === "" ? null : +lo;
    const tsMain = fts ? +fts : (frameTs || Math.round(Date.now() / 1000)) - (+age || 0);
    const base = { pk, seq: +seq, bat: bat === "" ? null : +bat, mode, power, own: !!meta.own, chan: meta.chan, rx: Math.round(Date.now() / 1000) };
    const out = [];
    if (lat !== null) {
      for (const e of extras(extra, lat, lon)) {
        // SlowTrack (L) en FIFO (Q): ook de meegestuurde oudere punten zijn gelogde of ingehaalde punten
        out.push({ ...base, k: `${pk}:${tsMain - e.dt}`, ts: tsMain - e.dt, state: state === "L" || state === "Q" ? state : "M", lat: e.lat, lon: e.lon, spd: e.spd, extra: true });
      }
    }
    // Op verzoek (V) zonder verse fix: laatst gekende positie, met de echte fix-tijd (meer dan 2 min voor de ontvangst)
    const rxTs = frameTs || Math.round(Date.now() / 1000);
    const old = state === "V" && lat !== null && rxTs - tsMain > V_OLD_S;
    out.push({ ...base, k: `${pk}:${tsMain}:${state}`, ts: tsMain, state, lat, lon, old, alt: alt === "" ? null : +alt,
      spd: spd === "" ? null : +spd, crs: crs === "" ? null : +crs, hdop: hdop === "" ? null : +hdop });
    return { positions: out, pk, state };
  }

  // ---- Bluetooth (MeshCore companion, Nordic UART) ------------------------------------------
  const NUS = "6e400001-b5a3-f393-e0a9-e50e24dcca9e", RXC = "6e400002-b5a3-f393-e0a9-e50e24dcca9e", TXC = "6e400003-b5a3-f393-e0a9-e50e24dcca9e";
  let selfName = "", dev = null, rxc = null, waiter = null, syncing = false, again = false, chanNames = {}, devChans = [];
  const log = (t) => { const el = $("bt-log"); el.textContent = `${new Date().toLocaleTimeString("nl-BE")} ${t}\n` + el.textContent.slice(0, 4000); };

  function onFrame(f) {
    if (f[0] >= 0x80) {                              // push
      if (f[0] === 0x83) syncAll();
      else if (f[0] === 0x89) readContacts();      // nieuw contact gehoord
      else if (f[0] === 0x88) onRawRx(f);
      return;
    }
    if (contactSink && (f[0] === 3 || f[0] === 4)) { contactSink(f); return; }
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
      selfName = devName;
      setBt(true, devName);
      say($("bt-msg"), "Verbonden.", true);
      await readChannels();
      await readContacts();
      await applyScope();
      await syncAll();
    } catch (e) { say($("bt-msg"), `Verbinden mislukt: ${e.message}`, false); setBt(false); }
  }
  function setBt(on, name) {
    $("btpill").textContent = on ? `Bluetooth: ${name || "verbonden"}` : "Bluetooth: uit";
    $("btpill").className = "pill" + (on ? " ok" : "");
    $("bt-connect").hidden = on; $("bt-disconnect").hidden = !on; $("bt-sync").hidden = !on;
    $("bt-info").textContent = on ? `Verbonden met ${name}.` : "";
    if (!on) { rxc = null; devChans = []; renderDevChans(); }
    renderChat();
  }

  async function syncAll() {
    if (!rxc) return;
    if (syncing) { again = true; return; }
    syncing = true; again = false;
    let n = 0, stored = 0, rejected = 0, chats = 0;
    try {
      for (let i = 0; i < 300; i++) {
        const f = await ask(new Uint8Array([10]), [16, 17, 7, 8, 10], 5000);
        if (f[0] === 10) break;                         // geen berichten meer
        n++;
        const dv = new DataView(f.buffer);
        if (f[0] === 16 || f[0] === 7) {               // privébericht aan de companion: chat
          const o = f[0] === 16 ? 4 : 1;
          const from = [...f.slice(o, o + 6)].map((b) => b.toString(16).padStart(2, "0")).join("");
          const txtType = f[o + 7], ts = dv.getUint32(o + 8, true);
          const text = new TextDecoder().decode(f.slice(o + 12 + (txtType === 2 ? 4 : 0))).replace(/\0+$/, "");
          if (!text.startsWith("T1|")) chats += await addChat({ dm: true, chan: "privé", from, text, ts });
          continue;
        }
        if (f[0] !== 17 && f[0] !== 8) continue;
        const v3 = f[0] === 17;
        const o = v3 ? 4 : 1;
        const own = v3 && f[2] === 1;
        const chan = f[o];
        const ts = dv.getUint32(o + 3, true);
        const text = new TextDecoder().decode(f.slice(o + 7)).replace(/\0+$/, "");
        const chanName = chanNames[chan] || `kanaal ${chan}`;   // eigen kopieën van de tracker: altijd op het volgkanaal
        const r = await decode(text, ts, { own, chan: chanName });
        if (!r) {                                       // geen trackerbericht: gewone chat
          const i2 = text.indexOf(": ");
          if (text.slice(i2 + 2).startsWith("T1A|")) { log("SOS-bevestiging van de server gezien"); continue; }   // voor de tracker
          if (text.slice(i2 + 2).startsWith("T1F|")) { log("ontvangstbevestiging (FIFO) van de server gezien"); continue; }   // idem
          if (text.slice(i2 + 2).startsWith("T1R|")) { log("positieverzoek gezien"); continue; }   // verzoek aan de trackers
          chats += await addChat({ chan: chanName, chanIdx: chan, from: i2 > 0 ? text.slice(0, i2) : "?",
            text: i2 > 0 ? text.slice(i2 + 2) : text, ts });
          continue;
        }
        if (r.bad) { rejected++; log(`geweigerd (${r.bad}) ${r.pk || ""}`); continue; }
        stored += await storePositions(r.positions);
        noteAnswer(r);
        const ch = devChans.find((c) => c.idx === chan);
        if (own && ch) {                                  // eigen kanaalbericht van de tracker: herhalingen tellen
          const main = r.positions[r.positions.length - 1];
          watchFor("pos", main.k, ch.secret, ts, text, 2);
        }
        log(`${own ? "eigen · " : ""}${nameOf(r.pk)} ${STATE[r.state] || r.state} (+${r.positions.length - 1} punten)`);
      }
    } catch (e) { say($("bt-msg"), e.message, false); }
    finally { syncing = false; }
    if (again && rxc) setTimeout(syncAll, 50);
    if (n) say($("bt-msg"), `${n} bericht(en) gelezen, ${stored} nieuwe positie(s)${chats ? `, ${chats} chatbericht(en)` : ""}${rejected ? `, ${rejected} geweigerd` : ""}.`, true);
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
    renderDevChans(); renderChat(); renderAll();
  }
  function renderDevChans() {
    $("ch-dev").innerHTML = !rxc ? "Niet verbonden." : devChans.length
      ? devChans.map((c) => `<div>${c.idx}: <strong>${esc(c.name)}</strong></div>`).join("")
      : "Geen kanalen op de companion.";
  }

  // Namen van trackers uit de contacten van de companion (CMD_GET_CONTACTS).
  let contactSink = null;
  async function readContacts() {
    const names = await metaGet("names", {});
    const done = new Promise((res) => {
      const t = setTimeout(res, 8000);
      contactSink = (f) => {
        if (f[0] === 3) {
          const nm = new TextDecoder().decode(f.slice(100, 132)).replace(/\0.*$/, "");
          if (nm) names[hex(f.slice(1, 5))] = nm;
        } else { clearTimeout(t); res(); }
      };
    });
    try { await ask(new Uint8Array([4]), [2, 1]); await done; } catch (_) {} finally { contactSink = null; }
    contactNames = names;
    await metaSet("names", names);
    renderAll();
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

  // ---- herhalingen van eigen berichten ------------------------------------------------------
  // Een kanaalbericht is AES-128-ECB met het kanaalgeheim; het eerste blok is
  // [tijd 4][0]["naam: tekst" ...]. Dat blok rekenen we zelf uit; elk ontvangen pakket
  // (push 0x88) met hetzelfde blok is ons bericht, herhaald door een repeater. Het laatste
  // stuk van het pad is de repeater die we hoorden.
  const hex = (a) => [...a].map((b) => b.toString(16).padStart(2, "0")).join("");
  const unhex = (h) => new Uint8Array(h.match(/../g).map((x) => parseInt(x, 16)));
  let watch = [];                                   // { block, until, store, k }
  async function aesBlock(secretHex, block) {
    const key = await crypto.subtle.importKey("raw", unhex(secretHex).slice(0, 16), { name: "AES-CBC" }, false, ["encrypt"]);
    const out = new Uint8Array(await crypto.subtle.encrypt({ name: "AES-CBC", iv: new Uint8Array(16) }, key, block));
    return hex(out.slice(0, 16));                   // CBC met nul-IV over 1 blok = ECB
  }
  async function watchFor(store, k, secretHex, ts, text, tsSlack) {
    const t = new TextEncoder().encode(text);
    for (let d = -tsSlack; d <= tsSlack; d++) {
      const b = new Uint8Array(16);
      new DataView(b.buffer).setUint32(0, ts + d, true);
      b.set(t.slice(0, 11), 5);
      watch.push({ block: await aesBlock(secretHex, b), until: Date.now() + 180000, store, k });
    }
  }
  async function onRawRx(f) {
    const now = Date.now();
    watch = watch.filter((w) => w.until > now);
    if (!watch.length) return;
    const snr = new Int8Array(f.buffer)[1] / 4;
    const raw = f.slice(3);
    const route = raw[0] & 3, type = (raw[0] >> 2) & 15;
    if (type !== 5) return;                          // alleen kanaaltekst
    let i = 1 + (route === 0 || route === 3 ? 4 : 0);
    const pl = raw[i++], size = (pl >> 6) + 1, count = pl & 63;
    const path = raw.slice(i, i + size * count); i += size * count;
    const block = hex(raw.slice(i + 3, i + 19));     // na kanaalhash (1) en MAC (2)
    const w = watch.find((x) => x.block === block);
    if (!w) return;
    const last = count ? hex(path.slice((count - 1) * size, count * size)) : "direct";
    const st = tx(w.store, "readwrite");
    const rec = await req(st.get(w.k));
    if (!rec) return;
    rec.rep = rec.rep || [];
    const via = hex(path) || "direct";
    if (!rec.rep.some((r) => r.path === via)) rec.rep.push({ path: via, last, hops: count, snr });
    st.put(rec);
    const mem = (w.store === "chat" ? chatLog : posAll).find((x) => x.k === w.k);
    if (mem) mem.rep = rec.rep;
    if (w.store === "chat") renderChat(); else renderAll();
  }
  const repText = (rep) => !rep || !rep.length ? "" :
    `${rep.length}× gehoord via ${[...new Set(rep.map((r) => r.last))].slice(0, 4).join(", ")}`;

  // ---- regio (scope) voor wat de app verstuurt ----------------------------------------------
  async function applyScope() {
    if (!rxc) return;
    const name = (await metaGet("scope", "be")).replace(/^#/, "").trim();
    try {
      if (name) {
        const h = new Uint8Array(await crypto.subtle.digest("SHA-256", new TextEncoder().encode("#" + name)));
        await ask(new Uint8Array([54, 0, ...h.slice(0, 16)]), [0, 1]);
      } else await ask(new Uint8Array([54, 0]), [0, 1]);     // standaard van de companion
      log(`regio voor versturen: ${name || "standaard van de companion"}`);
    } catch (e) { log(`regio instellen mislukt: ${e.message}`); }
  }

  // ---- chat: alles wat geen trackerbericht is ---------------------------------------------
  const chatLog = [];
  let unread = 0;
  const lsGet = (k) => { try { return localStorage.getItem(k); } catch (_) { return null; } };
  const lsSet = (k, v) => { try { localStorage.setItem(k, v); } catch (_) {} };
  async function loadChat() {
    const all = await req(tx("chat", "readonly").index("ts").getAll(IDBKeyRange.lowerBound(Date.now() / 1000 - 7 * 86400)));
    chatLog.length = 0; chatLog.push(...all);
  }
  async function addChat(m) {
    m.k = `${m.chan}|${m.ts}|${m.from}|${m.text}`;
    m.rx = Math.round(Date.now() / 1000);
    const st = tx("chat", "readwrite");
    if (await req(st.get(m.k))) return 0;
    st.put(m);
    chatLog.push(m);
    if ($("chat").hidden && !m.own) unread++;
    renderChat();
    return 1;
  }
  function renderChat() {
    const b = $("chat-badge");
    b.textContent = unread > 99 ? "99+" : String(unread); b.hidden = !unread;
    const sel = $("chat-chan"), cur = sel.value;
    const names = [...new Set(devChans.map((c) => c.name).concat(chatLog.map((m) => m.chan)))];
    sel.innerHTML = '<option value="">Alle kanalen</option>' +
      names.map((n) => `<option${n === cur ? " selected" : ""}>${esc(n)}</option>`).join("");
    const shown = chatLog.filter((m) => !sel.value || m.chan === sel.value).sort((a, b) => a.ts - b.ts).slice(-300);
    const list = $("chat-list");
    const atEnd = list.scrollHeight - list.scrollTop - list.clientHeight < 40;
    list.innerHTML = shown.length ? shown.map((m) => `<div class="cmsg${m.own ? " own" : ""}">
      <div class="cmeta">${m.own ? "jij" : esc(m.dm ? `privé van ${m.from}` : m.from)}${sel.value ? "" : ` · ${esc(m.chan)}`} · ${new Date(m.ts * 1000).toLocaleTimeString("nl-BE", { hour: "2-digit", minute: "2-digit" })}</div>
      <div class="ctext">${esc(m.text)}</div>${m.own ? `<div class="cmeta">${esc(repText(m.rep) || "nog niet gehoord via een repeater")}</div>` : ""}</div>`).join("") : '<div class="empty">Nog geen chatberichten.</div>';
    if (atEnd) list.scrollTop = list.scrollHeight;
    const target = devChans.find((c) => c.name === sel.value);
    $("chat-text").disabled = $("chat-send").disabled = !rxc || !target;
    $("chat-text").placeholder = !rxc ? "Verbind met een companion om te sturen"
      : !target ? "Kies een kanaal om te sturen" : `Bericht op ${target.name}`;
  }
  function chatOpen(on) {
    $("chat").hidden = !on; $("chat-btn").setAttribute("aria-expanded", String(on));
    lsSet("mt.off.chat", on ? "1" : "0");
    if (on) unread = 0;
    renderChat();
    if (on) { const l = $("chat-list"); l.scrollTop = l.scrollHeight; }
  }
  async function sendChat(e) {
    e.preventDefault();
    const text = $("chat-text").value.trim();
    const target = devChans.find((c) => c.name === $("chat-chan").value);
    if (!text || !target || !rxc) return;
    const ts = Math.round(Date.now() / 1000);
    const body = new TextEncoder().encode(text).slice(0, 140);
    const f = new Uint8Array(7 + body.length);   // CMD_SEND_CHANNEL_TXT_MSG: [3, type, kanaal, ts, tekst]
    f.set([3, 0, target.idx]); new DataView(f.buffer).setUint32(3, ts, true); f.set(body, 7);
    try {
      const r = await ask(f, [0, 1, 6]);
      if (r[0] === 1) throw new Error("de companion weigerde het bericht");
      $("chat-text").value = ""; say($("chat-msg"), "");
      const m = { chan: target.name, chanIdx: target.idx, from: selfName || "jij", text, ts, own: true };
      await addChat(m);
      watchFor("chat", m.k, target.secret, ts, `${selfName}: ${text}`, 0);
    } catch (err) { say($("chat-msg"), err.message, false); }
  }
  $("chat-btn").addEventListener("click", () => chatOpen($("chat").hidden));
  $("chat-close").addEventListener("click", () => chatOpen(false));
  $("chat-chan").addEventListener("change", renderChat);
  $("chat-form").addEventListener("submit", sendChat);

  // ---- kaart ---------------------------------------------------------------------------
  let map = null, markers = new Map(), hours = 24, trackOn = true;
  // Thema's: dezelfde als op de website, onder dezelfde sleutel (mt.theme) bewaard; ook de kaart volgt.
  const THEMES = { auto: "Automatisch", light: "Licht", dark: "Donker", night: "Nacht (rood)", contrast: "Hoog contrast", ocean: "Oceaan" };
  const theme = () => { try { return JSON.parse(localStorage.getItem("mt.theme") || '"auto"'); } catch (_) { return "auto"; } };
  const dark = () => { const t = theme(); return t === "dark" || t === "night" || (t === "auto" && matchMedia("(prefers-color-scheme: dark)").matches); };
  function applyTheme(t) {
    if (t === "auto") document.documentElement.removeAttribute("data-theme");
    else document.documentElement.setAttribute("data-theme", t);
    try { localStorage.setItem("mt.theme", JSON.stringify(t)); } catch (_) {}
    $("d-themes").querySelectorAll("button").forEach((b) => b.classList.toggle("on", b.dataset.th === t));
  }
  $("d-themes").innerHTML = Object.entries(THEMES).map(([k, v]) => `<button type="button" data-th="${k}">${v}</button>`).join("");
  $("d-themes").querySelectorAll("button").forEach((b) => b.addEventListener("click", () => {
    const wasDark = dark();
    applyTheme(b.dataset.th);
    if (dark() !== wasDark && map) initMap();          // lichte of donkere kaart
  }));
  matchMedia("(prefers-color-scheme: dark)").addEventListener("change", () => { if (theme() === "auto" && map) initMap(); });
  applyTheme(theme());

  async function opfs() { return navigator.storage && navigator.storage.getDirectory ? navigator.storage.getDirectory() : null; }
  // Alle kaarten op het toestel, van grof naar gedetailleerd (zoom uit de naam, "-z14"; bij
  // gelijke zoom eerst de grootste, zodat een kleinere uitsnede erbovenop ligt).
  async function storedMaps() {
    const root = await opfs();
    const out = [];
    if (root) for await (const [name, h] of root.entries()) {
      if (!name.endsWith(".pmtiles")) continue;
      const f = await h.getFile();
      const key = name.replace(/\.pmtiles$/, "");
      out.push({ key, size: f.size, file: f, zoom: Number((key.match(/-z(\d+)$/) || [0, 0])[1]) });
    }
    return out.sort((a, b) => a.zoom - b.zoom || b.size - a.size);
  }

  async function initMap() {
    const maps = await storedMaps();
    let style;
    if (maps.length) style = MTBasemap.offlineStyle(dark(), maps.map((m) => new File([m.file], `${m.key}.pmtiles`)));
    else style = MTBasemap.style(dark(), false);      // nog geen kaart op het toestel: lege achtergrond
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
      map.addLayer({ id: "pts", type: "circle", source: "pts", paint: { "circle-radius": ["case", ["match", ["get", "state"], ["L", "Q"], true, false], 2, 3], "circle-color": ["case", ["all", ["==", ["get", "state"], "V"], ["==", ["get", "old"], 1]], "rgba(0,0,0,0)", ["==", ["get", "state"], "V"], vAmber(), ["get", "color"]],
        "circle-opacity": ["case", ["match", ["get", "state"], ["L", "Q"], true, false], 0.7, 1], "circle-stroke-color": ["case", ["all", ["==", ["get", "state"], "V"], ["==", ["get", "old"], 1]], vAmber(), "#fff"],
        "circle-stroke-width": ["case", ["all", ["==", ["get", "state"], "V"], ["==", ["get", "old"], 1]], 1.5, ["match", ["get", "state"], ["L", "Q"], true, false], 0.5, 1] } });
      // Op verzoek (V): amber stip met een kleine ring, bescheiden in het spoor (zelfde stijl als op de site)
      map.addLayer({ id: "pts-v", type: "circle", source: "pts", filter: ["==", ["get", "state"], "V"],
        paint: { "circle-radius": 6, "circle-color": "rgba(0,0,0,0)", "circle-stroke-color": vAmber(), "circle-stroke-width": 1.5 } });
      map.on("click", "pts", (e) => {
        const p = e.features[0].properties;
        new maplibregl.Popup({ closeButton: false }).setLngLat(e.lngLat).setHTML(`<strong>${esc(p.name)}</strong><br>${new Date(p.ts * 1000).toLocaleString("nl-BE")}${p.spd !== "null" && p.spd != null ? `<br>${p.spd} km/u` : ""}${["L", "Q", "V"].includes(p.state) ? `<br>${STATE[p.state]}` : ""}`).addTo(map);
      });
      renderAll();
    });
    if (!maps.length) say($("maps-msg"), "Nog geen kaart op dit toestel: download er hieronder een terwijl je internet hebt.", false);
  }

  const vAmber = () => (dark() ? "#ffc83d" : "#e0a400");   // Op verzoek (V); zelfde als --vreq in style.css
  function colorOf(pk) { let h = 0; for (const c of pk) h = (h * 31 + c.charCodeAt(0)) % 360; return `hsl(${h},70%,45%)`; }
  function nameOf(pk) { return contactNames[pk] || `tracker ${pk}`; }

  // Kanaalkeuze: de kanalen die op de companion staan (plus die waarop al posities binnenkwamen).
  let chanFilter = "";
  function renderChanSelect() {
    const sel = $("o-chan");
    const names = [...new Set(devChans.map((c) => c.name).concat(posAll.map((p) => p.chan).filter(Boolean)))];
    const want = ["", ...names, "__add"].join("\n");
    if (sel.dataset.opts !== want) {
      sel.dataset.opts = want;
      sel.innerHTML = '<option value="">Alle kanalen</option>' + names.map((n) => `<option>${esc(n)}</option>`).join("") +
        '<option value="__add">+ Kanaal toevoegen…</option>';
    }
    sel.value = names.includes(chanFilter) ? chanFilter : "";
  }
  $("o-chan").addEventListener("change", () => {
    if ($("o-chan").value === "__add") { $("o-chan").value = chanFilter; show("ch"); $("ch-name").focus(); return; }
    chanFilter = $("o-chan").value; metaSet("chanFilter", chanFilter); renderAll(); });

  function renderAll() {
    const now = Date.now() / 1000, since = now - hours * 3600;
    const by = {};
    renderChanSelect();
    const cf = $("o-chan").value;
    for (const p of posAll) if (p.lat != null && (!cf || p.chan === cf)) (by[p.pk] = by[p.pk] || []).push(p);
    for (const [pk, m] of markers) if (!by[pk]) { m.remove(); markers.delete(pk); }   // ander kanaal: marker weg
    const lines = [], pts = [];
    for (const [pk, arr] of Object.entries(by)) {
      arr.sort((a, b) => a.ts - b.ts);
      const sel = arr.filter((p) => p.ts >= since);
      const color = colorOf(pk), name = nameOf(pk);
      if (trackOn && sel.length > 1) lines.push({ type: "Feature", properties: { color }, geometry: { type: "LineString", coordinates: sel.map((p) => [p.lon, p.lat]) } });
      if (trackOn) for (const p of sel) pts.push({ type: "Feature", properties: { color, name, ts: p.ts, spd: p.spd, state: p.state, old: p.old ? 1 : 0 }, geometry: { type: "Point", coordinates: [p.lon, p.lat] } });
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
    const el = m.getElement();
    const mk = el.querySelector(".omark");
    mk.style.background = color;
    // antwoord op een locatieverzoek: amber ring; bij een nieuw antwoord een korte puls
    mk.classList.toggle("vreq", p.state === "V" && !p.old);
    mk.classList.toggle("vold", p.state === "V" && !!p.old);
    if (p.state === "V" && mk.dataset.vk !== p.k) {
      if (mk.dataset.vk !== undefined || Date.now() / 1000 - (p.rx || 0) < 120) {
        mk.classList.remove("vpulse"); void mk.offsetWidth; mk.classList.add("vpulse");
        setTimeout(() => mk.classList.remove("vpulse"), 3600);
      }
      mk.dataset.vk = p.k;
    }
    el.querySelector(".olabel").textContent = name;
    m.setLngLat([p.lon, p.lat]);
  }
  function renderList(by) {
    const items = Object.entries(by).map(([pk, arr]) => ({ pk, last: arr[arr.length - 1], n: arr.length }))
      .sort((a, b) => b.last.ts - a.last.ts);
    $("o-list").innerHTML = items.length ? items.map(({ pk, last, n }) => {
      return `<div class="otrk" data-pk="${esc(pk)}"><span class="omark" style="background:${esc(colorOf(pk))}"></span>
        <div class="body"><div class="nm">${esc(nameOf(pk))} ${last.own ? '<span class="pill">eigen</span>' : ""} ${last.state === "E" ? '<span class="pill sos">SOS</span>' : ""}</div>
        <div class="sub">${esc(ago(last.ts))} · ${last.state === "V" ? `<i class="vping${last.old ? " old" : ""}" aria-hidden="true"></i> ` : ""}${esc(STATE[last.state] || last.state || "")}${last.bat != null ? " · " + last.bat + " %" : ""} · ${esc(last.chan || "")} · ${n} punten</div>
        ${last.own && last.rep ? `<div class="sub"><span class="pill">${esc(repText(last.rep))}</span></div>` : ""}</div></div>`;
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

  // ---- positie vragen (T1R) ------------------------------------------------------------------
  // "T1R|<doel>|<nonce>" als kanaalbericht op het trackingkanaal; doel = "*" (alle trackers) of de pk8
  // van één tracker. Trackers (firmware 0.9.5+) antwoorden met een gewoon T1C-bericht met toestand V.
  const ASK_ALL_S = 120, ASK_ONE_S = 30;            // de app zelf: "alle" hoogstens 1× per 2 min, één tracker 1× per 30 s
  let askReq = null, askAllAt = 0;
  const askOneAt = {};
  const nowS = () => Math.floor(Date.now() / 1000);
  const hms = (ts) => new Date(ts * 1000).toLocaleTimeString("nl-BE", { hour: "2-digit", minute: "2-digit", second: "2-digit" });
  // Trackingkanalen: kanalen op de companion waarop al trackerposities binnenkwamen (anders alle kanalen).
  function trackChans() {
    const seen = new Set(posAll.map((p) => p.chan).filter(Boolean));
    const mt = devChans.filter((c) => seen.has(c.name));
    return mt.length ? mt : devChans;
  }
  function defaultChan(list) {                      // het kanaal met de jongste trackerpositie
    let best = null, bt = -1;
    for (const p of posAll) if (p.ts > bt && list.some((c) => c.name === p.chan)) { bt = p.ts; best = p.chan; }
    return (list.find((c) => c.name === best) || list[0] || {}).idx;
  }
  function trackersOn(chanName) {
    return [...new Set(posAll.filter((p) => p.chan === chanName).map((p) => p.pk))].sort((a, b) => nameOf(a).localeCompare(nameOf(b)));
  }
  const askWait = (target) => Math.max(0, (target === "*" ? askAllAt + ASK_ALL_S : (askOneAt[target] || 0) + ASK_ONE_S) - nowS());
  const askTarget = () => (document.querySelector('#ask-targets input[name="ask-t"]:checked') || {}).value || "*";
  function askTick() {                              // aftellen op de knoppen
    const w = askWait(askTarget()), b = $("ask-send");
    b.disabled = w > 0 || !rxc;
    b.textContent = w > 0 ? `Versturen (nog ${w} s)` : "Versturen";
    const wa = askWait("*");
    $("o-ask-btn").textContent = wa > 0 ? `Positie vragen (alle: nog ${wa} s)` : "Positie vragen";
  }
  function renderAskTargets() {
    const ch = devChans.find((c) => c.idx === Number($("ask-chan").value));
    const pks = ch ? trackersOn(ch.name) : [];
    const cur = askTarget();
    $("ask-targets").innerHTML = `<label class="askopt"><input type="radio" name="ask-t" value="*"> Alle trackers op dit kanaal${pks.length ? ` (${pks.length} gezien)` : ""}</label>`
      + pks.map((pk) => `<label class="askopt"><input type="radio" name="ask-t" value="${esc(pk)}"><span class="omark sm" style="background:${esc(colorOf(pk))}"></span>
        <span>${esc(nameOf(pk))} <span class="mono muted">${esc(pk)}</span></span></label>`).join("");
    const radios = [...$("ask-targets").querySelectorAll('input[name="ask-t"]')];
    (radios.find((x) => x.value === cur) || radios[0]).checked = true;
    radios.forEach((x) => x.addEventListener("change", askTick));
    askTick();
  }
  function openAsk() {
    say($("o-ask-note"), "");
    if (!rxc) { say($("o-ask-note"), "Verbind eerst met een companion (tabblad Verbinding).", false); return; }
    const list = trackChans();
    if (!list.length) { say($("o-ask-note"), "Er staat nog geen kanaal op de companion (tabblad Kanalen).", false); return; }
    $("ask-chan").innerHTML = list.map((c) => `<option value="${c.idx}">${esc(c.name)}</option>`).join("");
    $("ask-chan").value = String(defaultChan(list));
    say($("ask-msg"), "");
    renderAskTargets();
    $("ask-dlg").showModal();
  }
  async function sendAsk(e) {
    e.preventDefault();
    const ch = devChans.find((c) => c.idx === Number($("ask-chan").value));
    const target = askTarget();
    if (!ch || !rxc || askWait(target) > 0) return;
    const nonce = hex(crypto.getRandomValues(new Uint8Array(3)));
    const text = `T1R|${target}|${nonce}`, ts = nowS();
    const body = new TextEncoder().encode(text);
    const f = new Uint8Array(7 + body.length);      // CMD_SEND_CHANNEL_TXT_MSG: [3, type 0, kanaal, ts (u32 LE), tekst]
    f.set([3, 0, ch.idx]); new DataView(f.buffer).setUint32(3, ts, true); f.set(body, 7);
    $("ask-send").disabled = true;
    try {
      const r = await ask(f, [0, 1, 6]);
      if (r[0] === 1) throw new Error("de companion weigerde het bericht (kanaal onbekend?)");
      if (target === "*") askAllAt = ts; else askOneAt[target] = ts;
      askReq = { ts, target, nonce, chan: ch.name, expect: target === "*" ? trackersOn(ch.name) : [target], answers: new Map() };
      $("ask-dlg").close();
      log(`positieverzoek verstuurd op ${ch.name}: ${text}`);
      renderAsk();
    } catch (err) { say($("ask-msg"), err.message, false); askTick(); }
  }
  // Antwoord: een T1C-bericht met toestand V, na het verzoek, op hetzelfde kanaal.
  function noteAnswer(r) {
    if (!askReq || !r || r.state !== "V") return;
    if (askReq.target !== "*" && r.pk !== askReq.target) return;
    const main = r.positions[r.positions.length - 1];
    if (main.chan && main.chan !== askReq.chan) return;
    if ((main.rx || nowS()) < askReq.ts - 5) return;
    askReq.answers.set(r.pk, { ts: main.ts, rx: main.rx || nowS(), fix: main.lat != null, old: !!main.old, lat: main.lat, lon: main.lon });
    renderAsk();
    focusAnswers();
  }
  function focusAnswers() {
    if (!map || !askReq) return;
    const pts = [...askReq.answers.values()].filter((a) => a.fix);
    if (!pts.length) return;
    if (pts.length === 1) { map.flyTo({ center: [pts[0].lon, pts[0].lat], zoom: Math.max(map.getZoom(), 14) }); return; }
    const b = new maplibregl.LngLatBounds();
    pts.forEach((p) => b.extend([p.lon, p.lat]));
    map.fitBounds(b, { padding: 60, maxZoom: 15, duration: 600 });
  }
  function askHighlight() {                         // de trackers die antwoordden, laten oplichten op de kaart
    for (const [pk, m] of markers) m.getElement().querySelector(".omark").classList.toggle("asked", !!askReq && askReq.answers.has(pk));
  }
  function renderAsk() {
    const box = $("o-ask");
    if (!askReq) { box.hidden = true; box.innerHTML = ""; askHighlight(); return; }
    const a = askReq, all = a.target === "*";
    const pks = [...new Set([...a.expect, ...a.answers.keys()])];
    const got = a.answers.size;
    const rows = pks.map((pk) => {
      const x = a.answers.get(pk);
      return `<li data-pk="${esc(pk)}"${x ? "" : ' class="wait"'}><span class="omark sm" style="background:${esc(colorOf(pk))}"></span>
        <span class="nm">${esc(nameOf(pk))} <span class="mono muted small">${esc(pk)}</span></span>
        <span class="small">${x ? `${esc(hms(x.rx))} · ${!x.fix ? '<span class="warn">geen fix</span>' : x.old ? `<i class="vping old" aria-hidden="true"></i> laatst gekend, ${esc(ago(x.ts))}` : '<i class="vping" aria-hidden="true"></i> met positie'}` : "nog geen antwoord"}</span></li>`;
    }).join("");
    box.innerHTML = `<div class="askhead"><div><strong>Verzoek verstuurd om ${esc(hms(a.ts))}</strong>
        <div class="small muted">${all ? `Alle trackers op ${esc(a.chan)}` : `${esc(nameOf(a.target))} op ${esc(a.chan)}`}</div>
        ${all ? `<div class="small">${got} van ${pks.length} ${pks.length === 1 ? "tracker" : "trackers"} geantwoord</div>` : ""}</div>
        <button type="button" id="o-ask-close" aria-label="Verzoek sluiten">✕</button></div>
      <ul>${rows || '<li class="wait">Nog geen trackers gezien op dit kanaal; antwoorden verschijnen hier.</li>'}</ul>`;
    box.hidden = false;
    $("o-ask-close").addEventListener("click", () => { askReq = null; renderAsk(); });
    box.querySelectorAll("li[data-pk]").forEach((li) => li.addEventListener("click", () => {
      const x = a.answers.get(li.dataset.pk);
      if (x && x.fix && map) map.flyTo({ center: [x.lon, x.lat], zoom: Math.max(map.getZoom(), 15) });
    }));
    askHighlight();
  }
  $("o-ask-btn").addEventListener("click", openAsk);
  $("ask-chan").addEventListener("change", renderAskTargets);
  $("ask-form").addEventListener("submit", sendAsk);
  $("ask-cancel").addEventListener("click", () => $("ask-dlg").close());
  setInterval(askTick, 1000);

  // ---- kaarten downloaden (OPFS) -----------------------------------------------------------
  async function renderMaps() {
    const root = await opfs();
    const stored = await storedMaps();
    const names = await metaGet("mapNames", {});
    $("maps-stored").innerHTML = stored.length ? `<h3 class="small">Op dit toestel (samen op de kaart)</h3>` + stored.slice().reverse().map((m) => `<div class="mapbox">
      <strong>${esc(names[m.key] || m.key)}</strong> <span class="muted small">${fmtSize(m.size)}</span> <span class="pill ok">in gebruik</span>
      <div class="row"><button type="button" class="danger" data-delmap="${esc(m.key)}">Verwijderen</button></div></div>`).join("")
      : '<div class="small muted">Nog geen offline kaart op dit toestel.</div>';
    $("maps-stored").querySelectorAll("[data-delmap]").forEach((b) => b.addEventListener("click", async () => {
      if (!confirm("Deze kaart van het toestel verwijderen?")) return;
      await root.removeEntry(`${b.dataset.delmap}.pmtiles`);
      await initMap();
      renderMaps();
    }));
    if (navigator.storage && navigator.storage.estimate) {
      const e = await navigator.storage.estimate();
      $("maps-quota").textContent = `Opslag in gebruik: ${fmtSize(e.usage || 0)} van ongeveer ${fmtSize(e.quota || 0)}.`;
    }
    if (!navigator.onLine) return;
    try {
      const r = await fetch("/api/offline/maps", { credentials: "same-origin" });
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
      if (navigator.serviceWorker && navigator.serviceWorker.controller) navigator.serviceWorker.controller.postMessage({ type: "prefetch" });
      say($("maps-msg"), `${m.name} staat op dit toestel.`, true);
      await initMap();
    } catch (e) { say($("maps-msg"), e.message, false); try { await root.removeEntry(`${m.key}.pmtiles.part`); } catch (_) {} }
    btn.disabled = false;
    renderMaps();
  }

  // ---- gegevens ----------------------------------------------------------------------------
  async function renderStats() {
    const n = await req(tx("pos", "readonly").count());
    const pks = new Set(posAll.map((p) => p.pk));
    $("d-stats").textContent = `${n} posities bewaard van ${pks.size} tracker(s).`;
  }
  $("d-clear").addEventListener("click", async () => {
    if (!confirm("Alle bewaarde posities en chatberichten van dit toestel wissen?")) return;
    await req(tx("pos", "readwrite").clear());
    await req(tx("chat", "readwrite").clear()); chatLog.length = 0; renderChat();
    posAll.length = 0; markers.forEach((m) => m.remove()); markers = new Map();
    renderAll(); renderStats();
  });
  $("d-export").addEventListener("click", async () => {
    const all = await req(tx("pos", "readonly").getAll());
    const rows = [["tracker", "naam", "tijd", "toestand", "lat", "lon", "km/u", "batterij", "via"]]
      .concat(all.sort((a, b) => a.ts - b.ts).map((p) => [p.pk, nameOf(p.pk), new Date(p.ts * 1000).toISOString(), p.state, p.lat, p.lon, p.spd ?? "", p.bat ?? "", p.chan || ""]));
    const blob = new Blob([rows.map((r) => r.join(";")).join("\n")], { type: "text/csv" });
    const a = document.createElement("a"); a.href = URL.createObjectURL(blob); a.download = `meshtrack-offline-${new Date().toISOString().slice(0, 10)}.csv`; a.click();
  });

  // ---- tabbladen en start --------------------------------------------------------------------
  const tabs = [...document.querySelectorAll("#otabs .tab")];
  function show(name) {
    tabs.forEach((b) => b.classList.toggle("on", b.dataset.tab === name));
    document.querySelectorAll("#opanel .pane").forEach((p) => { p.hidden = p.id !== "pane-" + name; });
    if (name === "maps") renderMaps();
    if (name === "data") renderStats();
  }
  tabs.forEach((b) => b.addEventListener("click", () => show(b.dataset.tab)));
  function net() { const on = navigator.onLine; $("net").textContent = on ? "online" : "offline"; $("net").className = "pill netpill " + (on ? "on" : "off"); }
  addEventListener("online", net); addEventListener("offline", net);

  $("bt-scope").addEventListener("change", async () => { await metaSet("scope", $("bt-scope").value.trim()); applyScope(); });
  $("bt-connect").addEventListener("click", connect);
  $("bt-disconnect").addEventListener("click", () => { if (dev && dev.gatt.connected) dev.gatt.disconnect(); setBt(false); });
  $("bt-sync").addEventListener("click", syncAll);
  $("ch-add").addEventListener("click", () => addChannel($("ch-name").value, $("ch-secret").value));
  $("ch-scan").addEventListener("click", scanQr);
  $("ch-file").addEventListener("change", (e) => { const f = e.target.files[0]; e.target.value = ""; if (f) qrFromFile(f); });

  // Onderpaneel op een gsm, zoals op de online kaart: slepen aan de greep past de hoogte aan,
  // tikken wisselt half/klein, helemaal naar onder verbergt het (dan een knop "Paneel").
  // De hoogte wordt per toestel onthouden.
  (function sheet() {
    const panel = $("opanel"), handle = $("osheet"), showBtn = $("osheet-show");
    const phone = () => matchMedia("(max-width: 760px)").matches;
    const vh = () => window.innerHeight - 48;
    const lsSetF = (v) => { try { localStorage.setItem("mt.off.sheet", String(v)); } catch (_) {} };
    const lsGetF = () => { const v = Number(lsGet("mt.off.sheet")); return Number.isFinite(v) && lsGet("mt.off.sheet") !== null ? v : 0.48; };
    const resize = () => { if (map) map.resize(); };
    const apply = (frac) => {
      if (!phone()) { panel.style.height = ""; panel.classList.remove("sheet-hidden"); showBtn.hidden = true; resize(); return; }
      if (frac < 0.12) {
        panel.classList.add("sheet-hidden"); showBtn.hidden = false; lsSetF(0); resize(); return;
      }
      panel.classList.remove("sheet-hidden"); showBtn.hidden = true;
      frac = Math.min(0.92, Math.max(0.16, frac));
      panel.style.height = Math.round(frac * vh()) + "px";
      lsSetF(frac);
      setTimeout(resize, 240);                       // na de animatie van de hoogte
    };
    let startY = 0, startH = 0, moved = false, dragging = false;
    handle.addEventListener("pointerdown", (e) => {
      if (!phone()) return;
      try { handle.setPointerCapture(e.pointerId); } catch (_) { /* geen echte aanwijzer */ }
      startY = e.clientY; startH = panel.offsetHeight; moved = false; dragging = true;
      panel.classList.add("dragging");
    });
    handle.addEventListener("pointermove", (e) => {
      if (!dragging) return;
      const dy = startY - e.clientY;
      if (Math.abs(dy) > 6) moved = true;
      panel.style.height = Math.max(0, startH + dy) + "px";
    });
    const end = () => {
      if (!dragging) return;
      dragging = false;
      const frac = panel.offsetHeight / vh();       // eerst meten, dan pas de animatie terug aan
      panel.classList.remove("dragging");
      if (!moved) { apply(frac > 0.3 ? 0.18 : 0.5); return; }
      apply(frac);
    };
    handle.addEventListener("pointerup", end);
    handle.addEventListener("pointercancel", end);
    showBtn.addEventListener("click", () => apply(0.48));
    window.addEventListener("resize", () => apply(lsGetF()));
    apply(lsGetF());
  })();

  // Voor tests en foutzoeken: een bericht zoals de companion het doorgeeft verwerken.
  window.MTOffline = {
    decode,
    addChat, onFrame, onRawRx,
    // nep-companion voor tests: { write(bytes) } die antwoorden via MTOffline.onFrame teruggeeft
    async attach(fake, name) { rxc = { properties: { write: true }, writeValue: async (b) => fake.write(b) }; selfName = name; setBt(true, name); await readChannels(); await readContacts(); await applyScope(); await syncAll(); },
    async ingest(text, ts, meta) {
      const r = await decode(text, ts, meta || {});
      if (!r || !r.positions) return r;
      const n = await storePositions(r.positions);
      noteAnswer(r);
      return n;
    },
  };

  (async () => {
    net();
    if ("serviceWorker" in navigator) navigator.serviceWorker.register("/offline-sw.js", { scope: "/offline" }).catch(() => {});
    db = await openDb();
    contactNames = await metaGet("names", {});
    chanFilter = await metaGet("chanFilter", "");
    hours = await metaGet("hours", 24);
    $("bt-scope").value = await metaGet("scope", "be");
    $("o-hours").querySelectorAll("button").forEach((x) => x.classList.toggle("on", Number(x.dataset.h) === hours));
    await loadPositions();
    await loadChat();
    chatOpen(lsGet("mt.off.chat") === "1");
    show((await storedMaps()).length ? "trk" : "maps");   // eerste keer: eerst een kaart kiezen
    await initMap();
    setInterval(renderAll, 30000);
  })();
})();
