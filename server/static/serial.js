/* Toestellen: verbinding via Web Serial, trackerinstellingen lezen en schrijven, terminal.
   device.js (flashen, klaarmaken, backups) gebruikt de verbinding via window.MTDev. */
(function () {
  const $ = (id) => document.getElementById(id);
  let status = null, trackers = [];

  function msg(el, text, ok) {
    el.textContent = text || "";
    el.className = "msg " + (ok ? "ok" : "err");
  }

  async function load() {
    status = await MT.api("/api/status");
    MT.meshPill($("mesh"), status.mesh);
    trackers = await MT.api("/api/trackers");
  }

  // ---- duur-invoer (getal + eenheid) ------------------------------------------------
  const UNITS = [["s", 1, "sec"], ["m", 60, "min"], ["h", 3600, "uur"]];
  document.querySelectorAll(".dur").forEach((d) => {
    d.innerHTML = `<input type="number" min="0" aria-label="waarde"><select aria-label="eenheid">${UNITS.map((u) => `<option value="${u[0]}">${u[2]}</option>`).join("")}</select>`;
    if (d.dataset.off) d.insertAdjacentHTML("beforeend", '<span class="help">0 = uit</span>');
  });
  function durSet(d, v) {
    const [inp, sel] = [d.querySelector("input"), d.querySelector("select")];
    if (!v || v === "uit" || v === "0") { inp.value = 0; sel.value = "m"; return; }
    const m = /^(\d+)([smh]?)$/.exec(v);
    if (!m) return;
    inp.value = m[1];
    sel.value = m[2] || "s";
  }
  function durGet(d) {
    const n = Number(d.querySelector("input").value);
    return n > 0 ? `${n}${d.querySelector("select").value}` : "0";
  }

  // ---- voorinstellingen ---------------------------------------------------------------
  const PRESETS = {
    walk: { min_speed: 0, min_dist: 25, turn_min: 0, turn_min_speed: 3, min_interval: "30s", max_interval: "2m", sample: "10s" },
    bike: { min_speed: 5, min_dist: 80, turn_min: 30, turn_min_speed: 8, min_interval: "30s", max_interval: "3m", sample: "10s" },
    car: { min_speed: 10, min_dist: 150, turn_min: 30, turn_min_speed: 10, min_interval: "30s", max_interval: "5m", sample: "15s" },
    save: { min_speed: 10, min_dist: 300, turn_min: 0, turn_min_speed: 10, min_interval: "3m", max_interval: "15m", sample: "30s" },
  };
  $("s-presets").querySelectorAll("[data-p]").forEach((b) => b.addEventListener("click", () => {
    const p = PRESETS[b.dataset.p];
    for (const [k, v] of Object.entries(p)) {
      const el = document.querySelector(`#s-form [data-set="${k}"]`);
      if (!el) continue;
      if (el.classList.contains("dur")) durSet(el, v); else el.value = v;
    }
    $("s-presets").querySelectorAll("button").forEach((x) => x.classList.toggle("on", x === b));
    msg($("s-msg"), `Voorinstelling "${b.textContent}" ingevuld. Klik op "Opslaan op de tracker" om ze te bewaren.`, true);
  }));

  // ---- geavanceerd ---------------------------------------------------------------------
  function setAdv(on) { $("s-adv").checked = on; $("s-advanced").hidden = !on; }
  $("s-adv").addEventListener("change", () => { setAdv($("s-adv").checked); MT.prefSet("devAdvanced", $("s-adv").checked); });

  // ---- Web Serial -------------------------------------------------------------------
  let port = null, reader = null, buf = "", lines = [], lastKv = {}, quiet = 0;
  const log = $("s-log");
  const append = (t) => {
    if (quiet) return;                      // privésleutel of backup: niet in de terminal
    log.textContent += t;
    if (log.textContent.length > 20000) log.textContent = log.textContent.slice(-15000);
    log.scrollTop = log.scrollHeight;
  };
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

  if (!("serial" in navigator)) {
    $("serial-note").textContent = "Deze browser kan niet met USB-apparaten praten. Gebruik Chrome of Edge op een computer of Android-toestel, via https of localhost.";
    $("s-connect").disabled = true;
  }

  async function readLoop() {
    const dec = new TextDecoder();
    while (port && port.readable) {
      reader = port.readable.getReader();
      try {
        for (;;) {
          const { value, done } = await reader.read();
          if (done) break;
          const s = dec.decode(value, { stream: true });
          append(s);
          buf += s;
          let i;
          while ((i = buf.search(/\r?\n/)) >= 0) { lines.push(buf.slice(0, i)); buf = buf.slice(i).replace(/^\r?\n/, ""); }
        }
      } catch (_) { /* poort weg */ } finally { reader.releaseLock(); }
      break;
    }
    if (port) disconnected("verbinding verbroken");
  }

  async function send(cmd) {
    if (!port || !port.writable) throw new Error("niet verbonden");
    const w = port.writable.getWriter();
    await w.write(new TextEncoder().encode(cmd + "\r"));
    w.releaseLock();
  }

  async function command(cmd, waitMs = 600) {
    lines = [];
    await send(cmd);
    await sleep(waitMs);
    return lines.slice();
  }

  // Commando sturen en lezen tot een regel aan `re` voldoet (of de tijd om is).
  async function until(cmd, re, timeoutMs = 4000, silent = false) {
    if (silent) quiet++;
    try {
      lines = [];
      await send(cmd);
      const t0 = Date.now();
      while (Date.now() - t0 < timeoutMs) {
        if (lines.some((l) => re.test(l))) return lines.slice();
        if (!port) throw new Error("verbinding verbroken");
        await sleep(40);
      }
      throw new Error(`geen antwoord op "${cmd.split(" ").slice(0, 2).join(" ")}"`);
    } finally {
      if (silent) setTimeout(() => { quiet = Math.max(0, quiet - 1); }, 300);
    }
  }

  function parseStatus(ls) {
    const kv = {};
    for (const l of ls) for (const m of l.matchAll(/([a-z_]+)=([^\s]+)/g)) kv[m[1]] = m[2];
    const g = /gekozen=(\w+)/.exec(ls.join("\n"));
    if (g) kv.gekozen = g[1];
    const a = /actief=(\w+)/.exec(ls.join("\n"));
    if (a) kv.actief = a[1];
    const nm = ls.find((l) => l.startsWith("naam="));
    if (nm) kv.naam = nm.slice(5).trim();          // namen mogen spaties bevatten
    return kv;
  }

  const knownTracker = (kv) => trackers.find((t) => t.pubkey === (kv.pubkey || "").toLowerCase());

  function fillForm(kv) {
    const num = (v) => (v || "").replace(/(km\/h|deg|m)$/, "");
    document.querySelectorAll("#s-form [data-set]").forEach((el) => {
      const k = el.dataset.set, kk = el.dataset.kv || k;   // status meldt bv. "naam=", instellen gaat met "set name"
      if (!(kk in kv)) { el.closest("div,label") && (el.disabled = true); return; }
      el.disabled = false;
      const v = kv[kk];
      if (el.classList.contains("dur")) durSet(el, v);
      else if (el.dataset.kind === "bool") el.checked = v === "aan" || v === "on";
      else if (["min_speed", "min_dist", "turn_min", "turn_min_speed"].includes(k)) el.value = num(v);
      else el.value = v;
    });
    setMode(kv.gekozen || "tracker");
    const known = knownTracker(kv);
    $("s-authstate").textContent = kv.authkey === "ja" ? "Authsleutel: ingesteld" : kv.authkey === "nee" ? "Authsleutel: niet ingesteld" : "Authsleutel: (firmware te oud)";
    $("s-auth").disabled = !known || !kv.authkey;
    $("s-info").innerHTML = `<div><strong>${MT.esc(kv.naam || "?")}</strong> · firmware ${MT.esc(kv.fw || "?")}
      · batterij ${MT.esc(kv.batt || "?")} · nu ${MT.esc(kv.actief || "?")}${kv.usb === "ja" ? " (USB)" : ""}</div>
      <div class="mono muted small">${MT.esc(kv.pubkey || "")}</div>
      <div class="small">${known ? `In MeshTrack als <strong>${MT.esc(known.alias)}</strong>` : '<span class="warn">Nog niet in MeshTrack</span>'}</div>`;
    $("s-use").hidden = !!known || !MT.can("trackers.manage");
    $("s-use").href = `/admin#new?pubkey=${encodeURIComponent((kv.pubkey || "").toLowerCase())}&alias=${encodeURIComponent(kv.naam || "")}`;
    if (window.MTDevice) MTDevice.onStatus(kv, known);
  }

  let mode = "tracker";
  function setMode(v) {
    mode = v;
    $("s-mode").querySelectorAll("button").forEach((b) => { const on = b.dataset.v === v; b.classList.toggle("on", on); b.setAttribute("aria-checked", String(on)); });
  }
  $("s-mode").querySelectorAll("button").forEach((b) => b.addEventListener("click", () => setMode(b.dataset.v)));

  // ---- verzenden via (DM of kanaal) ---------------------------------------------------
  let devChans = [], srvChans = [];
  async function readChannels() {
    let ls = [];
    try { ls = await until("chan list", /^chan=einde/, 3000, true); } catch (_) { /* oude firmware */ }
    devChans = ls.map((l) => /^chan=(\d+)\|([0-9A-Fa-f]{32})\|(.*)$/.exec(l)).filter(Boolean)
      .map((m) => ({ slot: Number(m[1]), secret: m[2].toLowerCase(), name: m[3].trim() }));
    srvChans = await MT.api("/api/channels/device").catch(() => []);
    // vóór 0.7 telde het kanaal alleen met transport=kanaal (anders DM, wat niet meer bestaat)
    const cur = lastKv.transport === "kanaal" ? devChans.find((d) => d.slot === Number(lastKv.chan)) : null;
    const curSrv = cur && srvChans.find((s) => s.secret === cur.secret);
    const others = devChans.filter((d) => !srvChans.some((s) => s.secret === d.secret));
    $("s-via").innerHTML = '<option value="">— kies het trackingkanaal —</option>'
      + (srvChans.length ? `<optgroup label="Kanalen van de server">${srvChans.map((s) => `<option value="srv:${s.id}">${MT.esc(s.name)}</option>`).join("")}</optgroup>` : "")
      + (others.length ? `<optgroup label="Andere kanalen op het toestel">${others.map((d) => `<option value="dev:${d.slot}">${d.slot}: ${MT.esc(d.name)}</option>`).join("")}</optgroup>` : "");
    $("s-via").value = !cur ? "" : curSrv ? `srv:${curSrv.id}` : `dev:${cur.slot}`;
    if (!cur) msg($("s-msg"), "Deze tracker heeft nog geen trackingkanaal: kies er een en sla op, anders stuurt hij niets.");
  }

  async function applyVia(bad) {
    const v = $("s-via").value;
    const run = async (cmd, silent) => {
      const out = await until(cmd, /bewaard|ongeldig|onbekend|NIET|gebruik|kies eerst/, 3000, silent);
      if (out.some((l) => /ongeldig|onbekend|NIET|gebruik|kies eerst/.test(l))) bad.push(cmd.split(" ").slice(0, 2).join(" "));
    };
    if (!v) return;                                 // geen kanaal gekozen
    let slot;
    if (v.startsWith("srv:")) {
      const s = srvChans.find((x) => `srv:${x.id}` === v);
      const have = devChans.find((d) => d.secret === s.secret);
      if (have) slot = have.slot;
      else {
        const used = new Set(devChans.map((d) => d.slot));
        slot = 1; while (used.has(slot) && slot < 39) slot++;
        await run(`chan set ${slot} ${s.secret} ${s.name}`, true);
      }
      const t = knownTracker(lastKv);
      if (t && lastKv.authkey !== "ja") {
        const { authkey } = await MT.api(`/api/trackers/${t.id}/authkey`);
        await run(`set authkey ${authkey}`, true);
      } else if (!t) msg($("s-msg"), "Deze tracker staat nog niet in MeshTrack: zet hem erin, anders weigert een kanaal met ondertekening zijn berichten.");
      if (s.region && lastKv.scope !== s.region) await run(`set scope ${s.region}`);
    } else slot = Number(v.slice(4));
    if (!lastKv.scope || lastKv.scope === "-")
      msg($("s-msg"), "Let op: deze tracker heeft geen regio (scope). Berichten zonder regio worden steeds vaker geblokkeerd; zet er een (bv. be) bij de kanaalinstelling op de server.");
    await run(`set chan ${slot}`);
    if (lastKv.transport !== "kanaal") await run("set transport kanaal");   // firmware 0.6: van DM naar kanaal
  }

  async function readStatus() {
    let ls;
    try { ls = await until("status", /^cfg=/, 2500); } catch (_) { ls = lines.slice(); }
    lastKv = parseStatus(ls);
    if (!lastKv.pubkey) {
      msg($("s-msg"), "Geen antwoord van een MeshTrack-tracker. Staat er nog andere firmware op? Flash dan eerst MeshTrack (tabblad Firmware).");
      $("s-none").textContent = "Dit toestel antwoordt niet als MeshTrack-tracker. Flash eerst MeshTrack via het tabblad Firmware.";
      if (window.MTDevice) MTDevice.onStatus(null, null);
      return false;
    }
    fillForm(lastKv);
    if (/^F{64}$|^0{64}$/i.test(lastKv.pubkey || "") || /BESCHADIGD/.test(lastKv.opslag_intern || ""))
      msg($("s-msg"), "De interne opslag van deze tracker is beschadigd (sleutel ongeldig). Typ in Terminal: fs herstel ja. "
        + "Na de herstart zet je de sleutel terug via Klaarmaken & backups → Op dit toestel zetten.");
    $("s-via").disabled = !lastKv.transport;
    if (lastKv.transport) await readChannels();
    else {
      $("s-via").innerHTML = '<option value="">firmware te oud: flash eerst 0.7 of nieuwer</option>';
      msg($("s-msg"), "Deze firmware stuurt nog via DM, wat de server niet meer leest. Flash de nieuwste firmware (tabblad Firmware).");
    }
    $("s-panel").hidden = false;
    $("s-none").hidden = true;
    msg($("s-msg"), "");
    return true;
  }

  function disconnected(why) {
    port = null;
    lastKv = {};
    if (window.MTDevice) MTDevice.onStatus(null, null);
    $("s-connect").hidden = false;
    $("s-disconnect").hidden = true;
    $("s-panel").hidden = true;
    $("s-none").hidden = false;
    $("s-none").textContent = "Verbind eerst een toestel. Daarna verschijnen hier zijn instellingen.";
    $("s-use").hidden = true;
    $("s-info").innerHTML = '<strong>Geen toestel verbonden.</strong>';
    $("s-state").className = "pill";
    $("s-state").textContent = why || "niet verbonden";
  }

  async function openPort(p) {
    port = p;
    await port.open({ baudRate: 115200 });
    await port.setSignals({ dataTerminalReady: true });
    $("s-connect").hidden = true;
    $("s-disconnect").hidden = false;
    $("s-state").className = "pill ok";
    $("s-state").textContent = "verbonden";
    readLoop();
    await sleep(700);
    await send("q");
    await sleep(200);
    if (!(await readStatus())) await readStatus();
    return lastKv.pubkey ? lastKv : null;
  }

  // Een tracker die niet meer antwoordt, mag de pagina niet laten hangen: elke stap hooguit enkele seconden.
  const within = (pr, ms) => Promise.race([Promise.resolve(pr).catch(() => {}), sleep(ms)]);
  async function closePort() {
    const p = port;
    port = null;
    try { if (reader) await within(reader.cancel(), 1500); } catch (_) {}
    try { await within(p.close(), 2500); } catch (_) {}
    disconnected();
    return p;
  }

  // Voor device.js (flashen, klaarmaken, backups)
  window.MTDev = {
    get port() { return port; }, get kv() { return lastKv; }, get trackers() { return trackers; },
    get status() { return status; },
    send, command, until, readStatus, parseStatus, open: openPort, close: closePort, reload: () => load(),
    msg: (t, ok) => msg($("s-msg"), t, ok),
  };

  $("s-connect").addEventListener("click", async () => {
    try {
      const p = await navigator.serial.requestPort();
      log.textContent = "";
      await openPort(p);
    } catch (e) { msg($("dfu-msg"), `Verbinden mislukt: ${e.message}`); $("dfu").hidden = false; if (port) disconnected(); }
  });
  $("s-disconnect").addEventListener("click", closePort);

  $("s-reload").addEventListener("click", readStatus);
  $("s-defaults").addEventListener("click", async () => {
    if (!(await MT.confirm("Alle trackerinstellingen terugzetten naar standaard? Sleutel, contacten en kanalen blijven.", { ok: "Standaardwaarden" }))) return;
    await command("defaults", 500);
    await readStatus();
    msg($("s-msg"), "Standaardwaarden hersteld.", true);
  });

  $("s-auth").addEventListener("click", async () => {
    const t = knownTracker(lastKv);
    if (!t) return;
    try {
      const { authkey } = await MT.api(`/api/trackers/${t.id}/authkey`);
      const out = await until(`set authkey ${authkey}`, /bewaard|ongeldig|onbekend/, 3000, true);
      const ok = out.some((l) => /bewaard/.test(l));
      msg($("s-msg"), ok ? "Authsleutel staat op de tracker." : "Authsleutel niet aanvaard.", ok);
      await readStatus();
    } catch (e) { msg($("s-msg"), e.message); }
  });

  $("s-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const cmds = [];
    document.querySelectorAll("#s-form [data-set]").forEach((el) => {
      const k = el.dataset.set;
      if (!((el.dataset.kv || k) in lastKv)) return;   // oudere firmware kent deze instelling niet
      let v;
      if (el.classList.contains("dur")) v = durGet(el);
      else if (el.dataset.kind === "bool") v = el.checked ? "on" : "off";
      else v = el.value.trim();
      if (el.dataset.spaces && v === (lastKv[el.dataset.kv || k] || "")) return;   // naam ongewijzigd
      if (v !== "" && (el.dataset.spaces || !/\s/.test(v))) cmds.push(`set ${k} ${v}`);
    });
    cmds.push(`mode ${mode}`);
    const bad = [], why = new Set();
    for (const c of cmds) {
      const out = await command(c, 250);
      const err = out.find((l) => /ongeldig|onbekend|NIET/.test(l));
      if (err) {
        bad.push(c.replace(/^set /, ""));
        const m = /(ongeldige? [^(]*|onbekend[^(]*|NIET [^(\]]*)/.exec(err);   // reden zoals de tracker ze geeft
        why.add((m ? m[1] : err).trim());
      }
    }
    if (lastKv.transport) await applyVia(bad);
    await readStatus();
    if (bad.length) {
      const opslag = [...why].some((w) => /NIET bewaard/.test(w));
      msg($("s-msg"), `Niet aanvaard: ${bad.join(", ")}. Reden van de tracker: ${[...why].join("; ") || "onbekend"}.`
        + (opslag ? " De tracker kan zijn instellingen niet bewaren (opslag vol): flash firmware 0.7.1 of nieuwer." : ""));
    }
    else msg($("s-msg"), "Opgeslagen op de tracker.", true);
  });

  async function sendFree() {
    const c = $("s-cmd").value.trim();
    if (!c) return;
    $("s-cmd").value = "";
    try { await send(c); } catch (e) { append(`\n[${e.message}]\n`); }
  }
  $("s-send").addEventListener("click", sendFree);
  $("s-cmd").addEventListener("keydown", (e) => { if (e.key === "Enter") sendFree(); });

  MT.live((m) => { if (m.type === "mesh") MT.meshPill($("mesh"), m.mesh); });
  MT.initHeader("/devices").then(async () => {
    MT.tabs($("dtabs"));
    setAdv(!!MT.prefGet("devAdvanced", false));
    await load();
    document.dispatchEvent(new CustomEvent("mt-devices-ready"));
  });
})();
