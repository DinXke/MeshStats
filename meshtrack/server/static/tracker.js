/* MeshTrack /tracker: de binnenkant van één tracker, live via Bluetooth (companionmodus) of USB.
   - Bluetooth: MeshCore-companion over Nordic UART. Vraag: [0x7E,'M','T',...commando]; antwoord in
     stukken [0x7E, volgnummer, ...tekst] en tot slot [0x7E, 0xFF].
   - USB (Web Serial, 115200 baud): "q" sluit het menu, daarna "status" en "dump" als tekstregels.
   Werkt zonder account; van de server komen alleen de pagina en de kaarten (of de kaarten uit OPFS). */
(function () {
  "use strict";
  const $ = (id) => document.getElementById(id);
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  const store = {
    get(k, def) { try { const v = localStorage.getItem(k); return v === null ? def : JSON.parse(v); } catch (_) { return def; } },
    set(k, v) { try { localStorage.setItem(k, JSON.stringify(v)); } catch (_) {} },
  };
  const nl = (x, d = 0) => Number(x).toLocaleString("nl-BE", { maximumFractionDigits: d });
  const COL = { pos: "#0b6e4f", F: "#2563eb", pend: "#f59e0b", S: "#16a34a", Q: "#7c3aed", park: "#dc2626" };
  const STATE = { M: "rijdt/stapt", S: "stilgevallen", H: "heartbeat", N: "geen GPS-fix", E: "SOS", B: "modus/voeding", P: "handmatig", W: "wakker", L: "gelogd punt (SlowTrack)", Q: "ingehaald punt (FIFO)" };
  const FLUSH = { idle: "wacht", actief: "bezig met leegmaken", gepauzeerd: "gepauzeerd", gestopt: "gestopt" };
  const CAPWHY = { uur: "uurgrens bereikt", pogingen: "te veel pogingen" };
  // Toestand van het leegmaken, met de reden als een plafond het pauzeert ("gepauzeerd: uurgrens bereikt").
  const flushText = (f) => { const t = FLUSH[f.state] || f.state; return f.cap && f.state !== "actief" && f.state !== "idle" ? `${t}: ${CAPWHY[f.cap] || f.cap}` : t; };
  const SETNAME = { F: "FastTrack", S: "SlowTrack", Q: "FIFO" };

  // ---- tijd ----------------------------------------------------------------------------
  // Leeftijden rekenen we tegen de klok van de tracker ("now" in de dump) plus de tijd sinds ontvangst,
  // zodat een scheve klok op de telefoon niets uitmaakt.
  let dump = null, status = {}, gotAt = 0;
  const trackerNow = () => (dump && dump.now ? dump.now + (Date.now() - gotAt) / 1000 : Date.now() / 1000);
  function dur(s) {
    s = Math.max(0, Math.round(s));
    if (s < 60) return `${s} s`;
    if (s < 3600) return `${Math.round(s / 60)} min`;
    if (s < 86400) { const h = Math.floor(s / 3600), m = Math.round((s % 3600) / 60); return m ? `${h} u ${m} min` : `${h} u`; }
    return `${nl(s / 86400, 1)} d`;
  }
  const ago = (ts) => (ts ? `${dur(trackerNow() - ts)} geleden` : "nooit");
  const clock = (ts) => new Date(ts * 1000).toLocaleTimeString("nl-BE", { hour: "2-digit", minute: "2-digit" });
  const clockS = (ts) => new Date(ts * 1000).toLocaleTimeString("nl-BE", { hour: "2-digit", minute: "2-digit", second: "2-digit" });
  const dateTime = (ts) => new Date(ts * 1000).toLocaleString("nl-BE", { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit", second: "2-digit" });
  // Duur uit de status ("30s", "2m", "1h", "uit") in seconden
  function durSec(v) {
    if (v == null || v === "" || v === "uit" || v === "-") return 0;
    const m = /^(\d+(?:\.\d+)?)\s*(s|m|min|u|h|d)?$/i.exec(String(v).trim());
    if (!m) return 0;
    const n = Number(m[1]), u = (m[2] || "s").toLowerCase();
    return Math.round(n * ({ s: 1, m: 60, min: 60, u: 3600, h: 3600, d: 86400 }[u]));
  }

  // ---- ontleden ------------------------------------------------------------------------
  const int = (v) => { const n = parseInt(v, 10); return Number.isFinite(n) ? n : null; };
  const num = (v) => { const n = Number(v); return v !== undefined && v !== "" && v !== "-" && Number.isFinite(n) ? n : null; };
  function parsePts(body) {
    const out = [];
    for (const it of body.split(";")) {
      const t = it.trim();
      if (!t || t === "-") continue;
      const [ts, la, lo, fl] = t.split(",");
      const tsn = int(ts), a = int(la), b = int(lo);
      if (tsn === null || a === null || b === null) continue;
      out.push({ ts: tsn, lat: a / 1e5, lon: b / 1e5, flags: (fl || "-").trim() });
    }
    return out;
  }
  function parseDump(text) {
    const d = { now: null, pos: null, mode: null, lastTx: null, lastHeard: null, cov: null, cnt: {}, flush: null, F: [], S: [], Q: [], unknown: [] };
    let inDump = false;
    for (const raw of text.split(/\r?\n/)) {
      const line = raw.trim();
      if (!line) continue;
      if (/^mtdump\b/.test(line)) { inDump = true; d.version = int(line.split(/\s+/)[1]); continue; }
      if (line === "end") break;
      if (line === "afgekapt") { d.truncated = true; continue; }
      const [key, ...rest] = line.split(/\s+/);
      const dash = rest[0] === "-";
      switch (key) {
        case "now": d.now = int(rest[0]); break;
        case "pos": if (!dash && rest.length >= 3) d.pos = { ts: int(rest[0]), lat: int(rest[1]) / 1e5, lon: int(rest[2]) / 1e5, spd: num(rest[3]), sats: int(rest[4]), hdop: num(rest[5]) }; break;
        case "mode": {
          const kv = {};
          rest.forEach((x) => { const i = x.indexOf("="); if (i > 0) kv[x.slice(0, i)] = x.slice(i + 1); });
          const plain = rest.filter((x) => !x.includes("="));
          d.mode = { mode: plain[0] || null, track: plain[1] || null, state: plain.slice(2).join(" ") || null, bat: int(kv.bat), usb: kv.usb === "1" };
          break;
        }
        case "last_tx": if (!dash) d.lastTx = { ts: int(rest[0]), st: rest[1] || "?", ok: rest[2] === "1" }; break;
        case "last_heard": if (!dash) d.lastHeard = { ts: int(rest[0]), snr: num(rest[1]), rep: rest[2] || "" }; break;
        case "cov": if (!dash) d.cov = { ts: int(rest[0]), snr: num(rest[1]), stable: rest[2] === "1" }; break;
        case "cnt":
          for (const x of rest) {
            const i = x.indexOf("=");
            if (i <= 0) continue;
            const k = x.slice(0, i), v = x.slice(i + 1);
            // hour=<doorgegeven>/<fifo_per_uur>, sinds 0.9.2 ook tries=<pogingen>/<2×fifo_per_uur>
            if (k === "hour" || k === "tries") { const [a, b] = v.split("/"); d.cnt[k] = int(a); d.cnt[k + "Limit"] = int(b); } else d.cnt[k] = int(v);
          }
          break;
        case "flush": {
          const nx = rest.find((x) => x.startsWith("next="));
          const cp = rest.find((x) => x.startsWith("cap="));                    // sinds 0.9.2: cap=uur|pogingen|-
          const capv = cp ? cp.slice(4) : "";
          d.flush = { state: rest[0] || "?", next: nx ? int(nx.slice(5)) : null, cap: capv && capv !== "-" ? capv : null };
          break;
        }
        case "pts": {
          const set = rest[0];
          if (set === "F" || set === "S" || set === "Q") d[set].push(...parsePts(rest.slice(1).join(" ")));
          else d.unknown.push(line);
          break;
        }
        default: if (inDump) d.unknown.push(line);
      }
    }
    // FastTrack en SlowTrack chronologisch; de FIFO komt al van oud naar nieuw, maar zeker is zeker.
    for (const k of ["F", "S", "Q"]) d[k].sort((a, b) => a.ts - b.ts);
    d.ok = inDump || d.now !== null;
    return d;
  }
  function parseStatus(text) {
    const kv = {};
    const ls = text.split(/\r?\n/);
    for (const l of ls) for (const m of l.matchAll(/([a-z_]+)=([^\s]+)/g)) kv[m[1]] = m[2];
    const nm = ls.find((l) => l.trim().startsWith("naam="));
    if (nm) kv.naam = nm.trim().slice(5).trim();          // namen mogen spaties bevatten
    return kv;
  }
  const qInfo = (fl) => ({ sent: fl.includes("s"), parked: fl.includes("k"), tries: int((fl.match(/\d+/) || [])[0]) || 0 });
  const fInfo = (fl) => ({ main: fl.includes("m"), pending: fl.includes("p"), heard: fl.includes("h") });

  // ---- logboek -------------------------------------------------------------------------
  const logEl = $("tk-log");
  function log(t) {
    const line = `${new Date().toLocaleTimeString("nl-BE")} ${t}\n`;
    logEl.textContent = (line + logEl.textContent).slice(0, 12000);
  }
  $("tk-logclear").addEventListener("click", () => { logEl.textContent = ""; });
  function say(t, kind) { const el = $("tk-msg"); el.textContent = t || ""; el.className = "tk-msg" + (kind ? " " + kind : ""); }

  // ---- verbinding: gemeenschappelijk -----------------------------------------------------
  // conn = { kind: "ble"|"usb", label, run(cmd) -> Promise<string>, close() }
  let conn = null, busy = false;
  function setConn(c) {
    conn = c;
    const on = !!c;
    $("tk-connect").hidden = on;
    $("tk-live").hidden = !on;
    const st = $("tk-state");
    st.className = "pill tk-state" + (on ? " ok" : "");
    st.textContent = on ? (c.kind === "ble" ? "Bluetooth" : "USB") : "niet verbonden";
    connecting(null);
    if (!on) { stopAuto(); $("tk-who").textContent = dump ? "Niet verbonden · laatste gegevens blijven staan" : "Niet verbonden"; }
    $("tk-intro").hidden = on || !!dump;
    $("slot-ov").hidden = !dump;
    if (dump) renderHero();
  }
  // Tijdens het verbinden: beide knoppen uit, tekst en draaiend icoon op de gekozen knop.
  function connecting(btn) {
    for (const b of [$("tk-bt"), $("tk-usb")]) {
      const lbl = b.querySelector("span");
      if (!b.dataset.lbl) b.dataset.lbl = lbl.textContent;
      if (btn) { b.disabled = true; } else { b.disabled = b.dataset.off === "1"; }
      lbl.textContent = b === btn ? "Verbinden…" : b.dataset.lbl;
      b.classList.toggle("spin", b === btn);
    }
    $("tk-connect").setAttribute("aria-busy", String(!!btn));
    if (btn) { $("tk-state").className = "pill tk-state busy"; $("tk-state").textContent = "verbinden…"; }
  }
  function lost(why) {
    if (!conn) return;
    log(`verbinding verbroken${why ? `: ${why}` : ""}`);
    conn = null;
    setConn(null);
    say(why ? `Verbinding verbroken: ${why}` : "Verbinding verbroken.", "err");
  }

  // Nooit twee vragen tegelijk: een nieuwe vraag zou de lopende vervangen. Daarom achter elkaar.
  function queued(fn) {
    let chain = Promise.resolve();
    return (...args) => {
      const p = chain.then(() => fn(...args));
      chain = p.catch(() => {});
      return p;
    };
  }
  // Een fout van de tracker komt als één enkele regel ("alleen status, fifo en dump via Bluetooth", "fout: ...").
  function checkReply(text) {
    const t = text.split(/\r?\n/).map((l) => l.trim()).filter((l) => l && l !== "end").join("\n");
    if (t && !t.includes("\n") && (/^fout\b/i.test(t) || /^alleen\b/i.test(t))) throw new Error(`de tracker antwoordt „${t}”`);
    return text;
  }

  // ---- Bluetooth (MeshCore companion, Nordic UART) -----------------------------------------
  const NUS = "6e400001-b5a3-f393-e0a9-e50e24dcca9e", RXC = "6e400002-b5a3-f393-e0a9-e50e24dcca9e", TXC = "6e400003-b5a3-f393-e0a9-e50e24dcca9e";
  async function connectBle() {
    if (!navigator.bluetooth) { say("Deze browser kan geen Bluetooth. Gebruik Chrome of Edge op Android of op een computer.", "err"); return; }
    let dev, rxc, waiter = null, collector = null;
    const write = (bytes) => (rxc.properties.write ? rxc.writeValue(bytes) : rxc.writeValueWithoutResponse(bytes));
    function onFrame(f) {
      if (!f.length) return;
      if (f[0] === 0x7e) { if (collector) collector(f); return; }      // MeshTrack-antwoord
      if (f[0] >= 0x80) return;                                       // pushes van de companion: hier niet nodig
      if (waiter && waiter.codes.includes(f[0])) { const w = waiter; waiter = null; w.resolve(f); }
    }
    function ask(bytes, codes, ms = 3000) {
      return new Promise((resolve, reject) => {
        const t = setTimeout(() => { waiter = null; reject(new Error("geen antwoord van de companion")); }, ms);
        waiter = { codes, resolve: (f) => { clearTimeout(t); resolve(f); } };
        write(bytes).catch((e) => { clearTimeout(t); waiter = null; reject(e); });
      });
    }
    // Eén MeshTrack-commando: stukken (hooguit 170 bytes tekst) in volgorde van aankomst tot [0x7E, 0xFF].
    // Het volgnummer loopt na 0xFE weer vanaf 0; BLE-meldingen komen in volgorde aan, dus sorteren we niet.
    function run(cmd, onProgress, ms = 8000) {
      return new Promise((resolve, reject) => {
        const parts = [];
        let bytes = 0, timer;
        const done = (err, text) => { clearTimeout(timer); collector = null; err ? reject(err) : resolve(text); };
        const arm = () => { clearTimeout(timer); timer = setTimeout(() => done(new Error(`geen (volledig) antwoord op "${cmd}"`)), ms); };
        collector = (f) => {
          if (f.length < 2) return;
          if (f[1] === 0xff) {
            const total = new Uint8Array(bytes);
            let o = 0;
            for (const p of parts) { total.set(p, o); o += p.length; }
            done(null, new TextDecoder().decode(total));
            return;
          }
          const p = f.slice(2);
          parts.push(p);
          bytes += p.length;
          if (onProgress) onProgress(bytes, parts.length);
          arm();
        };
        arm();
        write(new Uint8Array([0x7e, 0x4d, 0x54, ...new TextEncoder().encode(cmd)])).catch((e) => done(e));
      });
    }
    try {
      dev = await navigator.bluetooth.requestDevice({ filters: [{ services: [NUS] }, { namePrefix: "MeshCore" }, { namePrefix: "MeshTrack" }], optionalServices: [NUS] });
    } catch (e) {
      if (e && e.name !== "NotFoundError") say(`Bluetooth: ${e.message}`, "err");
      return;
    }
    try {
      connecting($("tk-bt"));
      say("Vraagt het toestel om te koppelen, gebruik dan pincode 123456.");
      dev.addEventListener("gattserverdisconnected", () => lost(""));
      const srv = await dev.gatt.connect();
      const svc = await srv.getPrimaryService(NUS);
      rxc = await svc.getCharacteristic(RXC);
      const txc = await svc.getCharacteristic(TXC);
      txc.addEventListener("characteristicvaluechanged", (e) => { const v = e.target.value; onFrame(new Uint8Array(v.buffer.slice(v.byteOffset, v.byteOffset + v.byteLength))); });
      await txc.startNotifications();
      // Zelfde opstart als de offline-app; antwoordt de companion niet, dan proberen we toch verder.
      let devName = dev.name || "tracker";
      try {
        await ask(new Uint8Array([22, 3]), [13]);
        const self = await ask(new Uint8Array([1, 3, 0, 0, 0, 0, 0, 0, ...new TextEncoder().encode("MeshTrack tracker")]), [5]);
        devName = new TextDecoder().decode(self.slice(58)).replace(/\0.*$/, "") || devName;
      } catch (e) { log(`companion-opstart: ${e.message}`); }
      log(`Bluetooth verbonden met ${devName}`);
      setConn({ kind: "ble", label: devName, run: queued(run), close: async () => { try { dev.gatt.disconnect(); } catch (_) {} } });
      say("Verbonden via Bluetooth.", "ok");
      $("tk-who").textContent = devName;
      await refresh(true);
      startAutoIfWanted();
    } catch (e) {
      say(`Verbinden via Bluetooth mislukt: ${e.message}. Staat de tracker in companionmodus?`, "err");
      log(`Bluetooth mislukt: ${e.message}`);
      try { dev.gatt.disconnect(); } catch (_) {}
      conn = null; setConn(null);
    }
  }

  // ---- USB (Web Serial) ----------------------------------------------------------------------
  async function connectUsb() {
    if (!("serial" in navigator)) { say("Deze browser kan niet met USB-toestellen praten. Gebruik Chrome of Edge op een computer of Android-toestel.", "err"); return; }
    let port;
    try { port = await navigator.serial.requestPort(); } catch (e) {
      if (e && e.name !== "NotFoundError") say(`USB: ${e.message}`, "err");
      return;
    }
    let reader = null, buf = "", lines = [], alive = true;
    const dec = new TextDecoder();
    async function readLoop() {
      try {
        reader = port.readable.getReader();
        for (;;) {
          const { value, done } = await reader.read();
          if (done) break;
          buf += dec.decode(value, { stream: true });
          let i;
          while ((i = buf.search(/\r?\n/)) >= 0) {
            const l = buf.slice(0, i);
            buf = buf.slice(i).replace(/^\r?\n/, "");
            if (l.startsWith("[")) log(l);           // live logregels van de tracker
            else lines.push(l);
            if (lines.length > 5000) lines.splice(0, lines.length - 5000);
          }
        }
      } catch (_) { /* poort weg */ } finally { try { reader.releaseLock(); } catch (_) {} }
      if (alive) { alive = false; lost("USB-kabel los of poort gesloten"); }
    }
    async function send(t) {
      const w = port.writable.getWriter();
      try { await w.write(new TextEncoder().encode(t)); } finally { w.releaseLock(); }
    }
    async function until(cmd, re, ms) {
      lines = [];
      await send(cmd + "\r\n");
      const t0 = Date.now();
      while (Date.now() - t0 < ms) {
        const i = lines.findIndex((l) => re.test(l.trim()));
        if (i >= 0) return lines.slice(0, i + 1);
        if (!alive) throw new Error("verbinding verbroken");
        await sleep(40);
      }
      return null;
    }
    async function run(cmd, onProgress) {
      if (cmd === "status") {
        const ls = await until("status", /^cfg=/, 3000);
        if (ls) return ls.join("\n");
        if (lines.length) return lines.join("\n");          // oudere firmware zonder cfg=-regel
        throw new Error('geen antwoord op "status"');
      }
      const prog = onProgress ? setInterval(() => onProgress(lines.reduce((n, l) => n + l.length + 2, 0), lines.length), 200) : null;
      let ls;
      try { ls = await until(cmd, /^(end|fout\b.*)$/, 12000); } finally { clearInterval(prog); }
      if (ls && /^fout\b/.test(ls[ls.length - 1].trim())) return ls[ls.length - 1].trim();
      if (!ls) throw new Error(`geen (volledig) antwoord op "${cmd}"`);
      // vanaf de laatste "mtdump"-regel: wat ervoor staat, is echo of iets anders
      let start = -1;
      ls.forEach((l, i) => { if (/^mtdump\b/.test(l.trim())) start = i; });
      return (start >= 0 ? ls.slice(start) : ls).join("\n");
    }
    try {
      connecting($("tk-usb"));
      say("Verbinden via USB…");
      await port.open({ baudRate: 115200 });
      try { await port.setSignals({ dataTerminalReady: true }); } catch (_) {}
      readLoop();
      await sleep(700);
      await send("q\r\n");                                   // menu sluiten
      await sleep(250);
      log("USB verbonden");
      setConn({
        kind: "usb", label: "USB", run: queued(run),
        close: async () => {
          alive = false;
          try { if (reader) await Promise.race([reader.cancel(), sleep(1500)]); } catch (_) {}
          try { await Promise.race([port.close(), sleep(2500)]); } catch (_) {}
        },
      });
      say("Verbonden via USB.", "ok");
      await refresh(true);
      startAutoIfWanted();
    } catch (e) {
      alive = false;
      say(`Verbinden via USB mislukt: ${e.message}`, "err");
      try { await port.close(); } catch (_) {}
      conn = null; setConn(null);
    }
  }

  async function disconnect() {
    const c = conn;
    conn = null;
    setConn(null);
    say("Losgekoppeld.");
    if (c) { log("losgekoppeld"); await c.close(); }
  }

  // ---- vernieuwen --------------------------------------------------------------------------
  const history = [];                      // laatste dumps voor de sparklines
  const HIST_MAX = 60;
  let statusAt = 0;
  async function refresh(withStatus) {
    if (!conn || busy) return;
    busy = true;
    $("tk-refresh").classList.add("spin");
    $("tk-refresh").disabled = true;
    try {
      // De instellingen veranderen zelden: bij verbinden en daarna hooguit om de 5 minuten.
      if (withStatus || !statusAt || Date.now() - statusAt > 300000) {
        try {
          const st = checkReply(await conn.run("status"));
          $("tk-rawstatus").textContent = st;
          const kv = parseStatus(st);
          if (Object.keys(kv).length) { status = kv; statusAt = Date.now(); }
        } catch (e) { log(`status: ${e.message}`); }
      }
      if (!conn) return;
      const lbl = $("tk-refresh").querySelector("span");
      const text = checkReply(await conn.run("dump", (b, n) => { lbl.textContent = `Ophalen… ${nl(b / 1024, 1)} kB`; $("tk-refresh").setAttribute("aria-label", `Dump ophalen, ${n} stukken ontvangen`); }));
      const d = parseDump(text);
      $("tk-raw").textContent = text;
      $("tk-rawtime").textContent = `· ${new Date().toLocaleTimeString("nl-BE")}`;
      if (!d.ok) throw new Error("het antwoord is geen dump. Heeft de tracker firmware 0.9.1 of nieuwer?");
      dump = d; gotAt = Date.now();
      history.push({ t: d.now || Math.round(Date.now() / 1000), snr: d.lastHeard ? d.lastHeard.snr : d.cov ? d.cov.snr : null, q: d.Q.length, bat: d.mode ? d.mode.bat : null });
      if (history.length > HIST_MAX) history.shift();
      if (d.unknown.length) log(`onbekende regels genegeerd: ${d.unknown.length}`);
      if (d.truncated) { say("De dump is afgekapt: de tracker had niet genoeg plaats om alle punten te sturen.", "err"); log("dump afgekapt"); }
      else say("");
      renderAll();
    } catch (e) {
      const m = String(e.message || e);
      say(`Vernieuwen mislukt. ${m.charAt(0).toUpperCase()}${m.slice(1)}${/[.?!]$/.test(m) ? "" : "."}${dump ? " De vorige gegevens blijven staan." : ""}`, "err");
      log(`dump: ${e.message}`);
    } finally {
      busy = false;
      $("tk-refresh").querySelector("span").textContent = "Nu vernieuwen";
      $("tk-refresh").removeAttribute("aria-label");
      $("tk-refresh").classList.remove("spin");
      $("tk-refresh").disabled = false;
    }
  }

  // ---- automatisch vernieuwen ---------------------------------------------------------------
  // Eén keuzelijst: "Auto uit" (0) of elke 5/10/30/60 s. De oude schakelaar (mt.tracker.auto) telt nog mee.
  let autoTimer = null;
  $("tk-every").value = store.get("mt.tracker.auto", true) ? String(store.get("mt.tracker.every", 10)) : "0";
  if (!$("tk-every").value) $("tk-every").value = "10";
  const autoSec = () => Number($("tk-every").value) || 0;
  function stopAuto() { clearInterval(autoTimer); autoTimer = null; }
  function startAutoIfWanted() {
    stopAuto();
    if (!conn || !autoSec()) return;
    autoTimer = setInterval(() => { if (!document.hidden) refresh(false); }, autoSec() * 1000);
  }
  $("tk-every").addEventListener("change", () => {
    const s = autoSec();
    store.set("mt.tracker.auto", s > 0);
    if (s) store.set("mt.tracker.every", s);
    startAutoIfWanted();
    if (dump) renderHero();
  });
  $("tk-refresh").addEventListener("click", () => refresh(false));
  $("tk-bt").addEventListener("click", connectBle);
  $("tk-usb").addEventListener("click", connectUsb);
  $("tk-disconnect").addEventListener("click", disconnect);
  // Leeftijden ("3 min geleden") lopen door tussen twee dumps.
  setInterval(() => { if (dump) renderCards(); }, 5000);

  // ---- berekeningen ----------------------------------------------------------------------------
  function fifoEstimate() {
    const per = int(status.fifo_per_bericht), gap = durSec(status.fifo_gap), cap = int(status.fifo_per_uur) || 0;
    const q = dump ? dump.Q.filter((p) => !qInfo(p.flags).sent).length : 0;
    if (!per || !gap) return null;
    const n = Math.ceil(q / per);
    if (!n) return { n: 0, s: 0, q, per, gap, cap };
    // Zelfde formule als de firmware: zonder uurplafond elke gap één bericht; met plafond 'cap'
    // berichten na elkaar, dan wachten tot het oudste een uur oud is.
    const capped = cap > 0 && 3600 / gap > cap;
    const s = !capped ? n * gap : Math.floor((n - 1) / cap) * 3600 + (((n - 1) % cap) + 1) * gap;
    return { n, s, q, per, gap, cap, capped };
  }
  function counts() {
    const d = dump;
    const r = { F: d.F.length, S: d.S.length, Q: d.Q.length, pend: 0, parked: 0, sent: 0, tries: 0 };
    d.F.forEach((p) => { if (fInfo(p.flags).pending) r.pend++; });
    d.Q.forEach((p) => { const i = qInfo(p.flags); if (i.parked) r.parked++; if (i.sent) r.sent++; if (i.tries) r.tries++; });
    return r;
  }

  // ---- weergave: kaarten (cards) ----------------------------------------------------------------
  const kv = (rows) => `<dl class="tk-kv">${rows.filter(Boolean).map(([k, v, sub]) => `<dt>${esc(k)}</dt><dd>${v}${sub ? `<span class="sub">${sub}</span>` : ""}</dd>`).join("")}</dl>`;
  const card = (title, body, cls = "", id = "") => `<article class="card tk-card${cls ? " " + cls : ""}"${id ? ` id="${id}"` : ""}><h2>${esc(title)}</h2>${body}</article>`;
  const meter = (parts) => `<div class="tk-meter" role="presentation">${parts.map(([pct, cls]) => `<span class="${cls || ""}" data-w="${Math.max(0, Math.min(100, pct)).toFixed(1)}"></span>`).join("")}</div>`;
  const pct = (a, b) => (b > 0 ? (a / b) * 100 : 0);
  function applyMeters(root) { root.querySelectorAll(".tk-meter span[data-w]").forEach((s) => { s.style.width = `${s.dataset.w}%`; }); }

  function cardState() {
    const m = dump.mode || {};
    const bat = m.bat != null ? `${m.bat}%` : "?";
    const batCls = m.bat != null && m.bat < 20 ? "tk-bad" : "";
    const pos = dump.pos;
    return card("Toestand", kv([
      ["Modus", esc(m.mode === "companion" ? "companion" : m.mode === "tracker" ? "tracker" : m.mode || "?")],
      ["Trackmodus", esc(m.track || status.track_mode || "?")],
      ["Tracker", esc(m.state || status.tracker || "?")],
      ["Batterij", `<span class="${batCls}">${esc(bat)}</span>${m.usb ? " · USB" : ""}`],
      ["GPS", pos ? `${esc(ago(pos.ts))}` : "geen fix", pos ? `${pos.sats != null ? `${pos.sats} sat.` : ""}${pos.hdop != null ? ` · HDOP ${nl(pos.hdop, 1)}` : ""}${pos.spd != null ? ` · ${nl(pos.spd)} km/u` : ""}` : ""],
    ]));
  }
  function cardRadio() {
    const t = dump.lastTx, h = dump.lastHeard, c = dump.cov;
    return card("Radio", kv([
      ["Laatst verstuurd", t ? esc(ago(t.ts)) : "nog niets", t ? `${esc(STATE[t.st] || t.st)} · ${t.ok ? '<span class="tk-good">verzonden</span>' : '<span class="tk-bad">mislukt</span>'}` : ""],
      ["Laatst herhaald", h ? esc(ago(h.ts)) : "nog niet gehoord", h ? `${h.snr != null ? `SNR ${nl(h.snr, 1)} dB` : ""}${h.rep ? ` · repeater ${esc(h.rep)}` : ""}` : ""],
      ["Laatste dekking", c ? esc(ago(c.ts)) : "geen", c ? `${c.snr != null ? `SNR ${nl(c.snr, 1)} dB · ` : ""}${c.stable ? '<span class="tk-good">stabiel</span>' : '<span class="tk-warn">niet stabiel</span>'}` : ""],
    ]));
  }
  function cardFifo(id) {
    const c = counts();
    const max = int(status.fifo_max);
    const est = fifoEstimate();
    const wacht = durSec(status.fifo_wacht);
    const oldest = dump.Q.length ? dump.Q[0].ts : null;
    const oldAge = oldest ? trackerNow() - oldest : 0;
    const fifoMode = (dump.mode && dump.mode.track === "fifo") || status.track_mode === "fifo";
    let body = "";
    body += `<div class="tk-block"><h3>Vulling</h3>${meter([[pct(c.Q - c.parked, max || Math.max(c.Q, 1)), "q"], [pct(c.parked, max || Math.max(c.Q, 1)), "k"]])}
      <div class="tk-meterlbl"><span>${c.Q} ${max ? `/ ${max}` : ""} punten</span><span>${c.parked ? `${c.parked} geparkeerd` : ""}</span></div></div>`;
    if (est) {
      body += `<div class="tk-block">${kv([
        ["Berichten nodig", est.n ? `${est.n}` : "0", `${est.per} punten per bericht`],
        ["Leeg in", est.n ? `≈ ${esc(dur(est.s))}` : "leeg", est.n ? `elke ${esc(dur(est.gap))}${est.cap ? ` · hoogstens ${est.cap} per uur` : ""}${est.capped ? " (plafond telt)" : ""}` : ""],
      ])}</div>`;
    }
    if (oldest) {
      body += `<div class="tk-block"><h3>Oudste punt</h3>${wacht ? meter([[pct(oldAge, wacht), oldAge >= wacht ? "warn" : "q"]]) : ""}
        <div class="tk-meterlbl"><span>${esc(dur(oldAge))} oud</span><span>${wacht ? `toch versturen na ${esc(dur(wacht))}` : "toch versturen staat uit"}</span></div></div>`;
    }
    if (dump.flush) {
      body += `<div class="tk-block">${kv([["Leegmaken", esc(flushText(dump.flush)), dump.flush.next ? `volgende poging ${esc(clock(dump.flush.next))} (${dump.flush.next > trackerNow() ? `over ${esc(dur(dump.flush.next - trackerNow()))}` : "nu"})` : ""]])}</div>`;
    }
    if (!fifoMode) body += '<p class="tk-note">De tracker staat in de klassieke modus: de FIFO-wachtrij wordt niet gebruikt.</p>';
    return card("FIFO-wachtrij", body, "", id);
  }
  function cardCounts(id) {
    const c = counts();
    return card("Buffers", `<div class="tk-bigrow">
      <div class="tk-big f"><b>${c.F}</b><span>FastTrack</span></div>
      <div class="tk-big s"><b>${c.S}</b><span>SlowTrack</span></div>
      <div class="tk-big q"><b>${c.Q}</b><span>FIFO</span></div></div>` + kv([
      ["FastTrack wacht op FIFO", `${c.pend}`],
      ["FIFO verstuurd, onbevestigd", `${c.sent}`],
      ["FIFO geparkeerd", `<span class="${c.parked ? "tk-bad" : ""}">${c.parked}</span>`],
      ["FIFO met mislukte pogingen", `${c.tries}`],
    ]), "", id);
  }
  function cardSent() {
    const n = dump.cnt;
    const heard = n.heard || 0, missed = n.missed || 0, tot = heard + missed;
    return card("Berichten sinds opstart", `<div class="tk-bigrow">
      <div class="tk-big f"><b>${n.fast ?? "–"}</b><span>FastTrack</span></div>
      <div class="tk-big s"><b>${n.slow ?? "–"}</b><span>SlowTrack</span></div>
      <div class="tk-big q"><b>${n.q ?? "–"}</b><span>FIFO</span></div></div>
      <div class="tk-block"><h3>Gehoord door een repeater</h3>${meter([[pct(heard, tot), "heard"], [pct(missed, tot), "missed"]])}
      <div class="tk-meterlbl"><span>${heard} gehoord${tot ? ` (${Math.round(pct(heard, tot))}%)` : ""}</span><span>${missed} niet gehoord</span></div></div>` +
      `<p class="tk-note">„Niet gehoord” telt alleen berichten waarna de tracker op een herhaling wachtte: in FIFO-modus, met de piep bij een herhaling (heard_beep) aan, na een druk op de knop en bij de eerste SOS. In de klassieke modus zonder die piep blijft dat getal dus laag.</p>` +
      `<div class="tk-block">${kv([["Bevestigd door de server (T1F)", `${n.t1f ?? "–"}`]])}</div>`);
  }
  function cardCap() {
    const n = dump.cnt;
    const lim = n.hourLimit || int(status.fifo_per_uur) || 0;
    const h = n.hour ?? 0;
    // Sinds 0.9.2: alleen doorgegeven inhaalberichten tellen voor het plafond; pogingen hebben een eigen grens (2×).
    const hasTries = n.tries != null;
    const tLim = n.triesLimit || (hasTries && lim ? 2 * lim : 0), t = n.tries ?? 0;
    const cap = dump.flush && dump.flush.cap;
    const row = (lbl, val, max, full) => `<div class="tk-block"><h3>${lbl}</h3>${max ? meter([[pct(val, max), full ? "missed" : "q"]]) : ""}
      <div class="tk-meterlbl"><span><b>${val} / ${max || "–"}</b> dit uur</span><span>${full ? "grens bereikt" : ""}</span></div></div>`;
    const hFull = lim > 0 && h >= lim, tFull = tLim > 0 && t >= tLim;
    let note;
    if (cap === "pogingen" || (!cap && tFull)) note = "Te veel pogingen dit uur: de volgende inhaalberichten wachten tot de oudste poging een uur oud is.";
    else if (cap === "uur" || hFull) note = "De uurgrens is bereikt: de volgende inhaalberichten wachten tot het oudste doorgegeven bericht een uur oud is.";
    else note = hasTries ? "Alleen inhaalberichten (FIFO) die een repeater herhaalde of die de server bevestigde, tellen als doorgegeven. Pogingen zonder herhaling tellen niet mee, maar per uur mogen er hoogstens twee keer zoveel pogingen zijn."
      : "Inhaalberichten (FIFO) die dit uur al vertrokken zijn.";
    return card("Uurplafond", row(hasTries ? "Doorgegeven" : "Verstuurd", h, lim, hFull) + (hasTries ? row("Pogingen", t, tLim, tFull) : "") + `<p class="tk-note">${note}</p>`);
  }
  function spark(title, key, unit, digits = 0) {
    const vals = history.map((h) => h[key]).filter((v) => v != null);
    const last = vals.length ? vals[vals.length - 1] : null;
    if (vals.length < 2) return `<div class="tk-spark"><h3>${esc(title)}</h3><p class="tk-empty">Nog te weinig metingen.</p><b>${last != null ? `${nl(last, digits)}${unit}` : "–"}</b></div>`;
    let lo = Math.min(...vals), hi = Math.max(...vals);
    if (hi === lo) { hi += 1; lo -= 1; }
    const W = 100, H = 40;
    const pts = vals.map((v, i) => `${((i / (vals.length - 1)) * W).toFixed(2)},${(H - 2 - ((v - lo) / (hi - lo)) * (H - 4)).toFixed(2)}`);
    return `<div class="tk-spark"><h3>${esc(title)}</h3>
      <svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" role="img" aria-label="${esc(title)}: ${vals.length} metingen, nu ${nl(last, digits)}${unit}">
        <polygon class="area" points="0,${H} ${pts.join(" ")} ${W},${H}"/><polyline points="${pts.join(" ")}"/></svg>
      <b>${nl(last, digits)}${esc(unit)}</b></div>`;
  }
  function cardHistory() {
    return card("Verloop", spark("SNR laatste herhaling", "snr", " dB", 1) + spark("Lengte FIFO-wachtrij", "q", "") + spark("Batterij", "bat", "%") +
      `<p class="tk-note">De laatste ${HIST_MAX} vernieuwingen, alleen zolang deze pagina openstaat.</p>`, "tk-wide");
  }
  function cardSettings() {
    const s = status;
    if (!Object.keys(s).length) return "";
    const v = (k) => (s[k] != null ? esc(s[k]) : "–");
    return card("Instellingen", kv([
      ["Trackmodus", v("track_mode")],
      ["FIFO maximaal / vanaf", `${v("fifo_max")} / ${v("fifo_min")}`],
      ["Tijd tussen inhaalberichten", s.fifo_gap ? esc(dur(durSec(s.fifo_gap))) : "–"],
      ["Per uur", v("fifo_per_uur")],
      ["Toch versturen na", s.fifo_wacht === "uit" ? "uit" : s.fifo_wacht ? esc(dur(durSec(s.fifo_wacht))) : "–"],
      ["Punten per bericht", v("fifo_per_bericht")],
      ["SlowTrack loggen / sturen", `${v("slow_log")} / ${v("slow_send")}`],
    ]));
  }

  // ---- in één oogopslag: werkt het, loopt de wachtrij leeg, wanneer kwam hij laatst door? ----------
  const ICON = {
    ok: '<svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="12" r="10"/><path d="M7 12.5l3.2 3.2L17 9"/></svg>',
    warn: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 3l10 18H2z"/><path d="M12 10v5M12 18v.01"/></svg>',
    bad: '<svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="12" r="10"/><path d="M8.5 8.5l7 7M15.5 8.5l-7 7"/></svg>',
  };
  const LVL = { ok: "In orde", warn: "Let op", bad: "Probleem" };
  let lastLvl = "";
  function verdict() {
    const now = trackerNow(), h = dump.lastHeard;
    const fifoMode = (dump.mode && dump.mode.track === "fifo") || status.track_mode === "fifo";
    const age = h ? now - h.ts : Infinity;
    if (!h) return { lvl: fifoMode ? "bad" : "warn", title: "Nog geen herhaling gehoord", sub: fifoMode ? "Nog geen enkele repeater heeft de tracker herhaald." : "In de klassieke modus luistert de tracker alleen met heard_beep aan." };
    if (age > 1800) return { lvl: "warn", title: `Al ${dur(age)} niet gehoord`, sub: "Geen repeater heeft de tracker sindsdien herhaald." };
    return { lvl: "ok", title: `Laatst gehoord ${dur(age)} geleden`, sub: `door repeater ${h.rep || "?"}${h.snr != null ? ` · SNR ${nl(h.snr, 1)} dB` : ""}` };
  }
  function renderHero() {
    if (!dump) return;
    const v = verdict(), c = counts(), est = fifoEstimate();
    const unsent = dump.Q.filter((p) => !qInfo(p.flags).sent).length;
    const t = dump.lastTx, m = dump.mode || {}, pos = dump.pos;
    const fl = dump.flush && (dump.flush.state === "gepauzeerd" || dump.flush.state === "gestopt") ? dump.flush : null;
    const tile = (lbl, val, sub, cls = "") => `<div class="tk-tile${cls ? " " + cls : ""}"><span class="tk-tilelbl">${lbl}</span><b>${val}</b><span class="tk-tilesub">${sub || "&nbsp;"}</span></div>`;
    const agoHtml = (ts) => `${esc(dur(trackerNow() - ts))} <small>geleden</small>`;
    const qSub = !c.Q ? "leeg" : !unsent ? "alles verstuurd" : est ? `leeg in ≈ ${esc(dur(est.s))}${fl ? ` · ${esc(flushText(fl))}` : ""}` : "";
    const upd = gotAt ? new Date(gotAt).toLocaleTimeString("nl-BE") : "";
    const foot = conn ? `Bijgewerkt om ${upd} · ${autoSec() ? `elke ${autoSec()} s` : "auto uit"}` : `Niet verbonden · gegevens van ${upd}`;
    $("ov-hero").innerHTML = `<section class="card tk-hero ${v.lvl}" aria-labelledby="tk-herot">
      <div class="tk-verdict">${ICON[v.lvl]}<div><span class="tk-lvl">${LVL[v.lvl]}</span><h2 id="tk-herot">${esc(v.title)}</h2><p>${esc(v.sub)}</p></div></div>
      <div class="tk-tiles">
        ${tile("Wachtrij", unsent ? `${unsent} <small>te versturen</small>` : "0", qSub)}
        ${tile("Laatst verstuurd", t ? agoHtml(t.ts) : "nog niets", t ? `${esc(STATE[t.st] || t.st)} · ${t.ok ? "verzonden" : '<span class="tk-bad">mislukt</span>'}` : "")}
        ${tile("GPS", pos ? agoHtml(pos.ts) : "geen fix", pos && pos.sats != null ? `${pos.sats} satellieten` : "", pos ? "" : "warn")}
        ${tile("Batterij", m.bat != null ? `${m.bat}%` : "?", m.usb ? "aan USB" : "", m.bat != null && m.bat < 20 ? "warn" : "")}
      </div>
      ${c.parked ? `<p class="tk-heronote">${ICON.warn}${c.parked} ${c.parked === 1 ? "punt" : "punten"} geparkeerd na te veel mislukte pogingen.</p>` : ""}
      <p class="tk-herofoot">${esc(foot)}</p></section>`;
    if (v.lvl !== lastLvl) { $("tk-sr").textContent = `${LVL[v.lvl]}: ${v.title.charAt(0).toLowerCase()}${v.title.slice(1)}`; lastLvl = v.lvl; }
  }

  function renderCards() {
    if (!dump) return;
    renderHero();
    const ov = $("ov-cards");
    ov.innerHTML = cardFifo() + cardRadio() + cardState() + cardCounts();
    applyMeters(ov);
    const st = $("stat-cards");
    st.innerHTML = cardSent() + cardCap() + cardHistory() + cardSettings();
    applyMeters(st);
    $("buf-fifo").outerHTML = cardFifo("buf-fifo");
    $("buf-counts").outerHTML = cardCounts("buf-counts");
    applyMeters($("p-buf"));
    const name = status.naam || (conn && conn.label) || "tracker";
    $("tk-who").textContent = `${name}${status.fw ? ` · fw ${status.fw}` : ""}${conn ? "" : " · niet verbonden"}`;
  }

  // ---- tijdlijn -----------------------------------------------------------------------------
  function renderTimeline() {
    const el = $("tk-timeline");
    const sets = [["F", "FastTrack"], ["S", "SlowTrack"], ["Q", "FIFO"]];
    const all = [...dump.F, ...dump.S, ...dump.Q];
    if (!all.length) { el.innerHTML = '<p class="tk-empty">Geen punten in de buffers.</p>'; $("tk-gaps").textContent = ""; return; }
    const t1 = Math.max(trackerNow(), ...all.map((p) => p.ts));
    const t0 = Math.min(...all.map((p) => p.ts));
    const span = Math.max(60, t1 - t0);
    const x = (ts) => (((ts - t0) / span) * 100).toFixed(2);
    // gaten: meer dan 15 minuten tussen twee punten (per buffer gearceerd; samen = nergens een punt)
    const gapsOf = (list) => { const g = []; for (let i = 1; i < list.length; i++) if (list[i] - list[i - 1] > 900) g.push([list[i - 1], list[i]]); return g; };
    const gaps = gapsOf(all.map((p) => p.ts).sort((a, b) => a - b));
    let html = "";
    tl.t0 = t0; tl.span = span;
    for (const [k, nm] of sets) {
      const gapHtml = gapsOf(dump[k].map((p) => p.ts)).map(([a, b]) => `<b style-l="${x(a)}" style-w="${(((b - a) / span) * 100).toFixed(2)}"></b>`).join("");
      const dots = dump[k].map((p, i) => `<i class="${dotCls(k, p)}" data-i="${i}" style-l="${x(p.ts)}"></i>`).join("");
      // Elke rij is één schuifregelaar: tikken of slepen kiest het dichtstbijzijnde punt, pijltjes stappen verder.
      html += `<span class="tk-tlname" id="tl-n-${k}">${nm}</span><div class="tk-tlrow" data-k="${k}" tabindex="${dump[k].length ? 0 : -1}" role="slider" aria-labelledby="tl-n-${k}"
        aria-valuemin="0" aria-valuemax="${Math.max(0, dump[k].length - 1)}" aria-valuenow="0" aria-valuetext="${dump[k].length} punten; tik of gebruik de pijltjestoetsen">${gapHtml}${dots}<span class="now"></span></div>`;
    }
    const ticks = 4;
    let axis = "";
    for (let i = 0; i <= ticks; i++) { const t = t0 + (span * i) / ticks; axis += `<span style-l="${((i / ticks) * 100).toFixed(2)}">${esc(span > 86400 ? dateTime(t).replace(/:\d\d$/, "") : clock(t))}</span>`; }
    html += `<span></span><div class="tk-tlaxis">${axis}</div>`;
    el.innerHTML = html;
    // posities via de stijl-API (geen inline style-attributen in de HTML)
    el.querySelectorAll("[style-l]").forEach((n) => { n.style.left = `${n.getAttribute("style-l")}%`; if (n.hasAttribute("style-w")) n.style.width = `${n.getAttribute("style-w")}%`; n.removeAttribute("style-l"); n.removeAttribute("style-w"); });
    const big = gaps.length ? gaps.reduce((a, g) => (g[1] - g[0] > a[1] - a[0] ? g : a)) : null;
    $("tk-gaps").textContent = `${dur(span)} in beeld, van ${clock(t0)} tot nu. Gearceerd: meer dan 15 min tussen twee punten van dezelfde buffer.` + (big ? ` In geen enkele buffer een punt: ${gaps.length}× langer dan 15 min, het langst ${dur(big[1] - big[0])} vanaf ${clock(big[0])}.` : "");
    // gekozen punt na een nieuwe dump terugzoeken (op tijdstip; de indexen schuiven)
    if (tl.sel) {
      const i = dump[tl.sel.k].findIndex((p) => p.ts === tl.sel.ts);
      if (i >= 0) tlSelect(tl.sel.k, i); else { tl.sel = null; tlDetail(null); }
    }
  }
  function dotCls(k, p) {
    if (k === "F") return fInfo(p.flags).pending ? "p" : "f";
    if (k === "Q") { const i = qInfo(p.flags); return i.parked ? "k" : i.sent ? "qs" : "q"; }
    return "s";
  }
  const tl = { sel: null, t0: 0, span: 1 };
  function tlDetail(k, p) {
    const el = $("tk-tldetail");
    if (!p) { el.textContent = "Tik op een rij (of sleep erover) om een punt te bekijken."; el.classList.remove("on"); return; }
    el.classList.add("on");
    el.innerHTML = `<i class="tk-sw ${dotCls(k, p)}" aria-hidden="true"></i><span><strong>${esc(SETNAME[k])}</strong> · ${esc(clockS(p.ts))} · ${esc(dur(trackerNow() - p.ts))} geleden${(() => { const f = flagPills(k, p.flags); return f.includes(">–<") ? "" : `<br>${f}`; })()}</span>`;
  }
  function tlSelect(k, i) {
    const list = dump && dump[k];
    if (!list || !list.length) return;
    i = Math.max(0, Math.min(list.length - 1, i));
    const p = list[i];
    tl.sel = { k, ts: p.ts };
    const root = $("tk-timeline");
    root.querySelectorAll(".tk-tlrow i.sel").forEach((n) => n.classList.remove("sel"));
    const row = root.querySelector(`.tk-tlrow[data-k="${k}"]`);
    const dot = row && row.querySelector(`i[data-i="${i}"]`);
    if (dot) dot.classList.add("sel");
    if (row) { row.setAttribute("aria-valuenow", String(i)); row.setAttribute("aria-valuetext", `${clockS(p.ts)}, ${dur(trackerNow() - p.ts)} geleden`); row.dataset.i = String(i); }
    tlDetail(k, p);
  }
  // dichtstbijzijnde punt bij een x-positie in een rij
  function tlNearest(row, clientX) {
    const list = dump && dump[row.dataset.k];
    if (!list || !list.length) return -1;
    const r = row.getBoundingClientRect();
    const ts = tl.t0 + ((clientX - r.left) / r.width) * tl.span;
    let best = 0, bd = Infinity;
    list.forEach((p, i) => { const d = Math.abs(p.ts - ts); if (d < bd) { bd = d; best = i; } });
    return best;
  }
  (function timelineEvents() {
    const root = $("tk-timeline");
    let drag = null;
    const pick = (row, x) => { const i = tlNearest(row, x); if (i >= 0) tlSelect(row.dataset.k, i); };
    root.addEventListener("pointerdown", (e) => {
      const row = e.target.closest(".tk-tlrow");
      if (!row || !dump) return;
      drag = row;
      try { row.setPointerCapture(e.pointerId); } catch (_) {}
      pick(row, e.clientX);
    });
    root.addEventListener("pointermove", (e) => {
      const row = drag || (e.pointerType === "mouse" ? e.target.closest(".tk-tlrow") : null);   // muis: ook zweven
      if (row && dump) pick(row, e.clientX);
    });
    const end = () => { drag = null; };
    root.addEventListener("pointerup", end);
    root.addEventListener("pointercancel", end);
    root.addEventListener("keydown", (e) => {
      const row = e.target.closest(".tk-tlrow");
      if (!row || !dump) return;
      const n = dump[row.dataset.k].length, cur = row.dataset.i != null ? Number(row.dataset.i) : n;
      const step = { ArrowRight: 1, ArrowUp: 1, ArrowLeft: -1, ArrowDown: -1, PageUp: 10, PageDown: -10 }[e.key];
      let i = null;
      if (step !== undefined) i = cur + step; else if (e.key === "Home") i = 0; else if (e.key === "End") i = n - 1;
      if (i === null) return;
      e.preventDefault();
      tlSelect(row.dataset.k, i);
    });
  })();

  // ---- puntenlijst ---------------------------------------------------------------------------
  let listSet = store.get("mt.tracker.list", "Q");
  function flagPills(set, fl) {
    const out = [];
    if (set === "F") { const i = fInfo(fl); if (i.main) out.push('<span class="pill tk-m">hoofdpunt</span>'); if (i.pending) out.push('<span class="pill tk-p">wacht op FIFO</span>'); if (i.heard) out.push('<span class="pill tk-h">gehoord</span>'); }
    if (set === "Q") { const i = qInfo(fl); if (i.sent) out.push('<span class="pill tk-s">verstuurd</span>'); if (i.parked) out.push('<span class="pill tk-k">geparkeerd</span>'); if (i.tries) out.push(`<span class="pill">${i.tries}× geprobeerd</span>`); }
    return out.join(" ") || `<span class="muted">${set === "Q" ? "nog niet verstuurd" : "–"}</span>`;
  }
  function renderList() {
    document.querySelectorAll("#buf-seg button").forEach((b) => { b.classList.toggle("on", b.dataset.b === listSet); b.setAttribute("aria-pressed", String(b.dataset.b === listSet)); });
    const el = $("buf-list");
    if (!dump) { el.innerHTML = '<p class="tk-empty tk-pad">Nog geen gegevens.</p>'; return; }
    const pts = dump[listSet];
    if (!pts.length) { el.innerHTML = `<p class="tk-empty tk-pad">De ${SETNAME[listSet]}-buffer is leeg.</p>`; return; }
    const rows = pts.slice().reverse().map((p) => `<tr><td>${esc(clockS(p.ts))}<br><span class="muted tk-small">${esc(dur(trackerNow() - p.ts))} geleden</span></td>
      <td class="c">${p.lat.toFixed(5)}<br>${p.lon.toFixed(5)}</td><td>${flagPills(listSet, p.flags)}</td></tr>`).join("");
    el.innerHTML = `<table><thead><tr><th scope="col"><span aria-hidden="true">Tijd ↓</span><span class="tk-sr">Tijd, nieuwste eerst</span></th><th scope="col">Positie</th><th scope="col">Toestand</th></tr></thead><tbody>${rows}</tbody></table>`;
  }
  document.querySelectorAll("#buf-seg button").forEach((b) => b.addEventListener("click", () => { listSet = b.dataset.b; store.set("mt.tracker.list", listSet); renderList(); }));

  // ---- kaart ---------------------------------------------------------------------------------
  const THEMES = { auto: "Automatisch", light: "Licht", dark: "Donker", night: "Nacht (rood)", contrast: "Hoog contrast", ocean: "Oceaan" };
  const theme = () => store.get("mt.theme", "auto");
  const dark = () => { const t = theme(); return t === "dark" || t === "night" || (t === "auto" && matchMedia("(prefers-color-scheme: dark)").matches); };
  function applyTheme(t) {
    if (t === "auto") document.documentElement.removeAttribute("data-theme");
    else document.documentElement.setAttribute("data-theme", t);
    store.set("mt.theme", t);
    $("tk-themes").querySelectorAll("button").forEach((b) => { b.classList.toggle("on", b.dataset.th === t); b.setAttribute("aria-pressed", String(b.dataset.th === t)); });
  }
  $("tk-themes").innerHTML = Object.entries(THEMES).map(([k, v]) => `<button type="button" data-th="${k}">${v}</button>`).join("");
  $("tk-themes").querySelectorAll("button").forEach((b) => b.addEventListener("click", () => {
    const wasDark = dark();
    applyTheme(b.dataset.th);
    if (dark() !== wasDark) initMap();
  }));
  matchMedia("(prefers-color-scheme: dark)").addEventListener("change", () => { if (theme() === "auto") initMap(); });
  applyTheme(theme());

  const LAYERS = [
    { id: "pos", label: "Nu", sw: "pos" },
    { id: "F", label: "FastTrack", sw: "f" },
    { id: "S", label: "SlowTrack", sw: "s" },
    { id: "Q", label: "FIFO", sw: "q" },
    { id: "lines", label: "Lijnen", sw: "line" },
  ];
  const vis = Object.assign({ pos: true, F: true, S: true, Q: true, lines: true }, store.get("mt.tracker.layers", {}));
  // Schakelbare lagen als chips (44 px); de betekenis van ringen en vormen als gewone tekstregel eronder.
  $("tk-legend").innerHTML = LAYERS.map((l) => `<label class="tk-chip"><input type="checkbox" data-layer="${l.id}" ${vis[l.id] ? "checked" : ""}><i class="tk-sw ${l.sw}" aria-hidden="true"></i>${l.label}</label>`).join("");
  $("tk-key").innerHTML = '<span><i class="tk-sw p" aria-hidden="true"></i>wacht op FIFO</span><span><i class="tk-sw qs" aria-hidden="true"></i>verstuurd, onbevestigd</span><span><i class="tk-sw k" aria-hidden="true"></i>geparkeerd</span>';
  $("tk-legend").querySelectorAll("input[data-layer]").forEach((c) => c.addEventListener("change", () => {
    vis[c.dataset.layer] = c.checked;
    store.set("mt.tracker.layers", vis);
    applyVis();
  }));

  let map = null, mapReady = false, posMarker = null, fitted = false;
  async function storedMaps() {
    try {
      const root = navigator.storage && navigator.storage.getDirectory ? await navigator.storage.getDirectory() : null;
      const out = [];
      if (root) for await (const [name, h] of root.entries()) {
        if (!name.endsWith(".pmtiles") || h.kind !== "file") continue;
        const f = await h.getFile();
        const key = name.replace(/\.pmtiles$/, "");
        out.push({ key, size: f.size, file: f, zoom: Number((key.match(/-z(\d+)$/) || [0, 0])[1]) });
      }
      return out.sort((a, b) => a.zoom - b.zoom || b.size - a.size);
    } catch (_) { return []; }
  }
  // Basiskaart: 1) kaarten uit OPFS (zelfde oorsprong als /offline), 2) de volledige kaart van de site
  // als je aangemeld bent, 3) de downloadbare offline-kaarten van de server (die zijn openbaar),
  // 4) een lege achtergrond.
  async function baseStyle() {
    const maps = await storedMaps();
    if (maps.length) return { style: MTBasemap.offlineStyle(dark(), maps.map((m) => new File([m.file], `${m.key}.pmtiles`))), note: "" };
    if (navigator.onLine) {
      try {
        const r = await fetch("/tiles/basemap.pmtiles", { headers: { Range: "bytes=0-15" }, credentials: "same-origin", cache: "no-store" });
        if ((r.status === 206 || r.status === 200) && !r.redirected) return { style: MTBasemap.style(dark(), true), note: "" };
      } catch (_) {}
      try {
        const r = await fetch("/api/offline/maps", { credentials: "same-origin" });
        const list = r.ok ? await r.json() : [];
        if (Array.isArray(list) && list.length) {
          const sorted = list.map((m) => ({ ...m, zoom: Number((m.key.match(/-z(\d+)$/) || [0, 0])[1]) })).sort((a, b) => a.zoom - b.zoom || b.size - a.size);
          const base = MTBasemap.style(dark(), true);
          const tmpl = base.layers;
          const layers = tmpl.filter((l) => l.type === "background"), sources = {};
          sorted.forEach((m, i) => {
            sources["pm" + i] = { type: "vector", url: `pmtiles://${location.origin}${m.url}`, attribution: "&copy; OpenStreetMap" };
            tmpl.forEach((l) => { if (l.type === "background") return; const c = JSON.parse(JSON.stringify(l)); c.id = `${l.id}__${i}`; c.source = "pm" + i; layers.push(c); });
          });
          return { style: { ...base, sources, layers }, note: "" };
        }
      } catch (_) {}
    }
    return { style: MTBasemap.style(dark(), false), note: "Geen kaart beschikbaar: download er een in de offline-app (tabblad Kaarten) terwijl je internet hebt." };
  }
  async function initMap() {
    if (typeof maplibregl === "undefined") return;
    const { style, note } = await baseStyle();
    $("tk-mapnote").textContent = note;
    const view = store.get("mt.tracker.view", { center: [5.33, 50.93], zoom: 10 });
    if (map) { const c = map.getCenter(); view.center = [c.lng, c.lat]; view.zoom = map.getZoom(); map.remove(); }
    mapReady = false; posMarker = null;
    map = new maplibregl.Map({ container: "tk-mapcanvas", style, center: view.center, zoom: view.zoom, attributionControl: { compact: true }, cooperativeGestures: false });
    map.addControl(new maplibregl.NavigationControl({ showCompass: false }), "top-right");
    map.on("moveend", () => store.set("mt.tracker.view", { center: map.getCenter().toArray(), zoom: map.getZoom() }));
    map.on("error", (e) => { if (e && e.error) log(`kaart: ${e.error.message || e.error}`); });
    map.on("load", () => {
      const empty = { type: "FeatureCollection", features: [] };
      map.addSource("tk-lines", { type: "geojson", data: empty });
      map.addSource("tk-pts", { type: "geojson", data: empty });
      for (const k of ["S", "F", "Q"]) {
        map.addLayer({ id: `line-${k}`, type: "line", source: "tk-lines", filter: ["==", ["get", "set"], k],
          layout: { "line-join": "round", "line-cap": "round" },
          paint: { "line-color": COL[k], "line-width": 1.5, "line-opacity": 0.7 } });
      }
      // Vormen en niet alleen kleur (kleurenblind, zon): SlowTrack vierkant, geparkeerd ruit,
      // wacht op FIFO met oranje ring, verstuurd met witte kern. Zelfde vormen in legende en tijdlijn.
      addShapes();
      const sym = (id, filter, icon) => map.addLayer({ id, type: "symbol", source: "tk-pts", filter,
        layout: { "icon-image": icon, "icon-allow-overlap": true, "icon-ignore-placement": true } });
      sym("pts-S", ["==", ["get", "set"], "S"], "tk-S");
      sym("pts-F", ["==", ["get", "set"], "F"], ["case", ["get", "pending"], "tk-Fp", "tk-F"]);
      sym("pts-Q", ["all", ["==", ["get", "set"], "Q"], ["!", ["get", "sent"]]], ["case", ["get", "parked"], "tk-K", "tk-Q"]);
      sym("pts-Q-sent", ["all", ["==", ["get", "set"], "Q"], ["get", "sent"]], "tk-Qs");
      // Tikken met een vinger: zoek in een vierkant van 28 px rond de tik, niet alleen op de stip zelf.
      const PTS = ["pts-S", "pts-F", "pts-Q", "pts-Q-sent"];
      const near = (pt, r) => map.queryRenderedFeatures([[pt.x - r, pt.y - r], [pt.x + r, pt.y + r]], { layers: PTS.filter((id) => map.getLayer(id)) });
      map.on("click", (e) => {
        const fs = near(e.point, 14);
        if (!fs.length) return;
        const d = (f) => { const p = map.project(f.geometry.coordinates); return (p.x - e.point.x) ** 2 + (p.y - e.point.y) ** 2; };
        const f = fs.reduce((a, b) => (d(b) < d(a) ? b : a));
        popup(f.properties, { lng: f.geometry.coordinates[0], lat: f.geometry.coordinates[1] });
      });
      map.on("mousemove", (e) => { map.getCanvas().style.cursor = near(e.point, 6).length ? "pointer" : ""; });
      mapReady = true;
      renderMap();
    });
  }
  // Puntsymbolen tekenen (canvas, 2× scherpte) en aan de kaartstijl toevoegen.
  function addShapes() {
    const R = 2, S = 24 * R, c = S / 2;
    const draw = (name, kind, r, fill, ring, core) => {
      if (map.hasImage(name)) return;
      const cv = document.createElement("canvas");
      cv.width = cv.height = S;
      const g = cv.getContext("2d");
      const path = (rr) => {
        g.beginPath();
        const q = rr * R;
        if (kind === "square") g.rect(c - q, c - q, 2 * q, 2 * q);
        else if (kind === "diamond") { const k = q * 1.35; g.moveTo(c, c - k); g.lineTo(c + k, c); g.lineTo(c, c + k); g.lineTo(c - k, c); g.closePath(); }
        else g.arc(c, c, q, 0, Math.PI * 2);
      };
      if (ring) { path(r + 3.6); g.fillStyle = ring; g.fill(); }
      path(r + 1.3); g.fillStyle = "#fff"; g.fill();
      path(r); g.fillStyle = fill; g.fill();
      if (core) { g.beginPath(); g.arc(c, c, 2.2 * R, 0, Math.PI * 2); g.fillStyle = "#fff"; g.fill(); }
      map.addImage(name, g.getImageData(0, 0, S, S), { pixelRatio: R });
    };
    draw("tk-F", "circle", 4.5, COL.F);
    draw("tk-Fp", "circle", 4.5, COL.F, COL.pend);
    draw("tk-S", "square", 4, COL.S);
    draw("tk-Q", "circle", 5.5, COL.Q);
    draw("tk-Qs", "circle", 5.5, COL.Q, null, true);
    draw("tk-K", "diamond", 5, COL.park);
  }
  function popup(p, ll) {
    const set = p.set;
    const fl = String(p.flags || "-");
    const el = document.createElement("div");
    el.innerHTML = `<strong>${esc(SETNAME[set] || set)}</strong><br>${esc(dateTime(p.ts))}<br><span class="muted">${esc(dur(trackerNow() - p.ts))} geleden</span><br>${flagPills(set, fl)}`;
    new maplibregl.Popup({ closeButton: true, maxWidth: "260px" }).setLngLat(ll).setDOMContent(el).addTo(map);
  }
  function applyVis() {
    if (!map || !mapReady) return;
    const set = (id, on) => { if (map.getLayer(id)) map.setLayoutProperty(id, "visibility", on ? "visible" : "none"); };
    for (const k of ["F", "S", "Q"]) { set(`pts-${k}`, vis[k]); set(`line-${k}`, vis[k] && vis.lines); }
    set("pts-Q-sent", vis.Q);
    if (posMarker) posMarker.getElement().hidden = !vis.pos;
  }
  function renderMap() {
    if (!map || !mapReady || !dump) return;
    const pts = [], lines = [];
    for (const k of ["F", "S", "Q"]) {
      const list = dump[k];
      for (const p of list) {
        const f = k === "F" ? fInfo(p.flags) : {}, q = k === "Q" ? qInfo(p.flags) : {};
        pts.push({ type: "Feature", geometry: { type: "Point", coordinates: [p.lon, p.lat] },
          properties: { set: k, ts: p.ts, flags: p.flags, pending: !!f.pending, sent: !!q.sent, parked: !!q.parked } });
      }
      if (list.length > 1) lines.push({ type: "Feature", geometry: { type: "LineString", coordinates: list.map((p) => [p.lon, p.lat]) }, properties: { set: k } });
    }
    map.getSource("tk-pts").setData({ type: "FeatureCollection", features: pts });
    map.getSource("tk-lines").setData({ type: "FeatureCollection", features: lines });
    const pos = dump.pos;
    if (pos && Number.isFinite(pos.lat) && Number.isFinite(pos.lon)) {
      if (!posMarker) {
        const el = document.createElement("div");
        el.className = "tk-pos";
        el.setAttribute("aria-label", "Huidige positie");
        posMarker = new maplibregl.Marker({ element: el }).setLngLat([pos.lon, pos.lat]).addTo(map);
      } else posMarker.setLngLat([pos.lon, pos.lat]);
      posMarker.getElement().classList.toggle("old", trackerNow() - pos.ts > 300);
    } else if (posMarker) { posMarker.remove(); posMarker = null; }
    applyVis();
    if (!fitted) { fitted = fit(); }
  }
  function fit() {
    if (!map || !dump || $("tk-mapcanvas").clientHeight < 50) return false;   // kaart niet in beeld: later
    const c = [];
    for (const k of ["F", "S", "Q"]) if (vis[k]) dump[k].forEach((p) => c.push([p.lon, p.lat]));
    if (dump.pos) c.push([dump.pos.lon, dump.pos.lat]);
    if (!c.length) return false;
    const b = c.reduce((bb, x) => bb.extend(x), new maplibregl.LngLatBounds(c[0], c[0]));
    map.fitBounds(b, { padding: 40, maxZoom: 16, duration: 0 });
    return true;
  }
  $("tk-fit").addEventListener("click", fit);

  // ---- tabs ------------------------------------------------------------------------------------
  const TABS = ["ov", "kaart", "buf", "stat", "ruw"];
  function placeMap(tab) {
    const slot = tab === "kaart" ? $("slot-kaart") : tab === "ov" ? $("slot-ov") : null;
    const m = $("tk-map");
    if (slot && m.parentElement !== slot) slot.appendChild(m);
    m.classList.toggle("placed", !!slot);
    if (map) requestAnimationFrame(() => { map.resize(); if (!fitted && mapReady) fitted = fit(); });
  }
  function showTab(name, focus) {
    if (!TABS.includes(name)) name = "ov";
    document.querySelectorAll(".tk-tabs [role=tab]").forEach((b) => {
      const on = b.dataset.tab === name;
      b.setAttribute("aria-selected", String(on));
      b.tabIndex = on ? 0 : -1;
      if (on && focus) b.focus();
    });
    TABS.forEach((t) => { $(`p-${t}`).hidden = t !== name; });
    $("tk-main").classList.toggle("is-map", name === "kaart");
    $("tk-main").scrollTop = 0;
    placeMap(name);
    if (name === "buf") renderList();
    store.set("mt.tracker.tab", name);
  }
  const tabBtns = [...document.querySelectorAll(".tk-tabs [role=tab]")];
  tabBtns.forEach((b, i) => {
    b.addEventListener("click", () => showTab(b.dataset.tab));
    b.addEventListener("keydown", (e) => {
      const d = { ArrowRight: 1, ArrowLeft: -1, Home: -i, End: tabBtns.length - 1 - i }[e.key];
      if (d === undefined) return;
      e.preventDefault();
      showTab(tabBtns[(i + d + tabBtns.length) % tabBtns.length].dataset.tab, true);
    });
  });

  function renderAll() {
    $("tk-intro").hidden = true;
    $("tk-fit").hidden = false;
    if ($("slot-ov").hidden) { $("slot-ov").hidden = false; if (map) map.resize(); }   // eerst zichtbaar, dan pas inzoomen
    renderCards();
    renderTimeline();
    renderMap();
    renderList();
  }

  // ---- ondersteuning, installeren, service worker -----------------------------------------------
  (function support() {
    const bt = !!navigator.bluetooth, usb = "serial" in navigator;
    $("tk-bt").disabled = !bt; $("tk-bt").dataset.off = bt ? "" : "1";
    $("tk-usb").disabled = !usb; $("tk-usb").dataset.off = usb ? "" : "1";
    if (!bt && usb) { $("tk-bt").classList.remove("primary"); $("tk-usb").classList.add("primary"); }   // de knop die werkt, is de hoofdknop
    const ios = /iPad|iPhone|iPod/.test(navigator.userAgent) || (navigator.platform === "MacIntel" && navigator.maxTouchPoints > 1);
    let t = "";
    if (!bt && !usb) t = ios ? "Op een iPhone of iPad kan geen enkele browser met Bluetooth- of USB-toestellen praten. Gebruik Chrome op Android of op een computer."
      : "Deze browser kan geen Bluetooth en geen USB. Gebruik Chrome of Edge op Android of op een computer (via https).";
    else if (!bt) t = "Bluetooth is niet beschikbaar in deze browser; USB wel.";
    else if (!usb) t = "USB is niet beschikbaar in deze browser (bv. op Android zonder Web Serial); Bluetooth wel.";
    // Eén melding, in de introkaart (niet daarnaast nog eens in de balk).
    $("tk-support").textContent = t;
    $("tk-support").hidden = !t;
    $("tk-support").classList.toggle("bad", !bt && !usb);
    // Installeren op een iPhone heeft alleen zin als die browser Bluetooth of USB kan (bv. Bluefy).
    const standalone = matchMedia("(display-mode: standalone)").matches || navigator.standalone;
    if (ios && !standalone && (bt || usb)) $("tk-ios").hidden = false;
  })();

  let installEvt = null;
  window.addEventListener("beforeinstallprompt", (e) => { e.preventDefault(); installEvt = e; $("tk-install").hidden = false; });
  window.addEventListener("appinstalled", () => { $("tk-install").hidden = true; installEvt = null; });
  $("tk-install").addEventListener("click", async () => {
    if (!installEvt) return;
    installEvt.prompt();
    try { await installEvt.userChoice; } catch (_) {}
    installEvt = null;
    $("tk-install").hidden = true;
  });

  // De volledige site vraagt internet en aanmelden: zonder netwerk eerst even zeggen.
  $("tk-site").addEventListener("click", (e) => {
    if (navigator.onLine) return;
    e.preventDefault();
    say("De volledige site vraagt internet en aanmelden. Deze pagina en de offline-kaart werken wel zonder.", "err");
  });

  if ("serviceWorker" in navigator) {
    let reloading = false;
    navigator.serviceWorker.addEventListener("controllerchange", () => { if (reloading) location.reload(); });
    navigator.serviceWorker.register("/tracker-sw.js", { scope: "/tracker" }).then((reg) => {
      const offer = (w) => {
        if (!w || !navigator.serviceWorker.controller) return;     // eerste installatie: niets te melden
        const b = $("tk-update");
        b.hidden = false;
        b.onclick = () => { reloading = true; w.postMessage({ type: "skip" }); };
      };
      if (reg.waiting) offer(reg.waiting);
      reg.addEventListener("updatefound", () => {
        const w = reg.installing;
        if (w) w.addEventListener("statechange", () => { if (w.state === "installed") offer(w); });
      });
      // Lettertypes en symbolen alvast bewaren zolang er internet is.
      navigator.serviceWorker.ready.then((r) => { if (navigator.onLine && r.active) r.active.postMessage({ type: "prefetch" }); });
    }).catch(() => {});
  }

  // ---- start -----------------------------------------------------------------------------------
  setConn(null);
  showTab(store.get("mt.tracker.tab", "ov"));
  renderList();
  initMap().catch((e) => log(`kaart: ${e.message}`));

  // Testhaak (alleen lezen): ontleden zonder toestel.
  window.MTTracker = { parseDump, parseStatus, durSec, fifoEstimate: () => fifoEstimate(), state: () => ({ dump, status, history: history.slice() }), map: () => map };
})();
