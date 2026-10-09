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
  const durSec = (d) => Math.max(0, Number(d.querySelector("input").value) || 0) * UNITS.find((u) => u[0] === d.querySelector("select").value)[1];

  // ---- fifo_wacht (0.9.3): keuzelijst 15/30/60 min, nooit (= uit) of anders… (duur 1 min tot 4 u) ----
  const WACHT_FIXED = { 900: "15m", 1800: "30m", 3600: "60m" };
  const parseSec = (v) => {
    if (v == null || v === "" || v === "uit" || v === "0") return 0;
    const m = /^(\d+)([smh]?)$/.exec(String(v).trim());
    return m ? Number(m[1]) * ({ s: 1, m: 60, h: 3600 }[m[2] || "s"]) : 0;
  };
  const wachtParts = (el) => [el.querySelector(":scope > select"), el.querySelector(":scope > .dur")];
  function wachtSet(el, v) {
    const [sel, d] = wachtParts(el), s = parseSec(v);
    if (!s) sel.value = "uit";
    else if (WACHT_FIXED[s]) sel.value = WACHT_FIXED[s];
    else { sel.value = "anders"; durSet(d, s % 3600 === 0 ? `${s / 3600}h` : s % 60 === 0 ? `${s / 60}m` : `${s}s`); }
    d.hidden = sel.value !== "anders";
  }
  function wachtGet(el) {
    const [sel, d] = wachtParts(el);
    if (sel.value !== "anders") return sel.value;
    const v = durGet(d);
    return v === "0" ? "uit" : v;
  }
  function wachtSec(el) {
    const [sel, d] = wachtParts(el);
    return sel.value === "anders" ? durSec(d) : parseSec(sel.value);
  }
  document.querySelectorAll('#s-form [data-kind="wacht"]').forEach((el) => {
    const [sel, d] = wachtParts(el);
    sel.addEventListener("change", () => {
      d.hidden = sel.value !== "anders";
      if (sel.value === "anders" && !d.querySelector("input").value) durSet(d, "45m");
    });
  });

  // ---- SlowTrack: hoeveel gelogde punten in één bericht (firmware dunt uit tot zo'n 8; 6 à 11 naargelang de afstanden) ----
  const SLOW_MAX = 8;
  function slowCalc() {
    const out = $("s-slowcalc"), lg = document.querySelector('#s-form [data-set="slow_log"]'), sd = document.querySelector('#s-form [data-set="slow_send"]');
    if (!out || !lg || !sd) return;
    const every = durSec(lg), send = durSec(sd);
    let t = "", warn = false;
    if (lg.querySelector("input").disabled) t = "";
    else if (!every) t = "SlowTrack staat uit.";
    else if (isFifo()) t = "FIFO-modus: de gelogde punten gaan naar de wachtrij en worden bij dekking ingehaald.";
    else if (send) {
      const n = Math.floor(send / every);
      if (n < 1) t = "Je logt minder vaak dan je verstuurt: hooguit 1 punt per bericht.";
      else if (n > SLOW_MAX) { t = `≈ ${n} punten per periode; er passen er zo'n ${SLOW_MAX} in één bericht, dus ongeveer ${n - SLOW_MAX} worden weggelaten.`; warn = true; }
      else t = `≈ ${n} ${n === 1 ? "punt" : "punten"} per bericht: alles past erin.`;
    }
    out.textContent = t;
    out.classList.toggle("warn", warn);
  }
  ["slow_log", "slow_send"].forEach((k) => {
    const d = document.querySelector(`#s-form [data-set="${k}"]`);
    if (d) { d.addEventListener("input", slowCalc); d.addEventListener("change", slowCalc); }
  });

  // ---- Trackmodus (firmware 0.9.0): classic of fifo ----------------------------------
  // In FIFO-modus gaan niet-herhaalde FastTrack-punten en de SlowTrack-punten naar een wachtrij
  // die bij dekking wordt leeggemaakt; slow_send wordt dan niet gebruikt.
  const fset = (k) => document.querySelector(`#s-form [data-set="${k}"]`);
  const fifoKnown = () => "track_mode" in lastKv;
  const isFifo = () => fifoKnown() && fset("track_mode").value === "fifo";
  function fifoUi() {
    const known = fifoKnown(), fifo = isFifo();
    const box = $("s-fifo");
    if (!box) return;
    box.classList.toggle("off", known && !fifo);
    // alleen in FIFO-modus, en alleen sleutels die de firmware kent
    box.querySelectorAll("input,select").forEach((x) => { const d = x.closest("[data-set]"); x.disabled = !fifo || !(d && d.dataset.set in lastKv); });
    $("s-fifooff").hidden = !known || fifo;
    // slow_send: niet gebruikt in FIFO-modus
    const sd = fset("slow_send");
    if (sd && "slow_send" in lastKv) {
      sd.querySelectorAll("input,select").forEach((x) => { x.disabled = fifo; });
      sd.closest("div:not(.dur)").classList.toggle("off", fifo);
    }
    $("s-slowsend-fifo").hidden = !fifo;
    const mx = Number(fset("fifo_max").value);
    fset("fifo_min").max = mx >= 1 ? String(Math.min(500, mx)) : "500";
    fifoCalc();
    slowCalc();
  }
  function fifoCalc() {
    const out = $("s-fifocalc");
    if (!out) return;
    out.textContent = "";
    out.classList.remove("warn");
    if (!isFifo()) return;
    const has = (k) => k in lastKv;
    const max = Math.max(0, Number(fset("fifo_max").value) || 0), gap = durSec(fset("fifo_gap")),
      per = Math.max(0, Number(fset("fifo_per_uur").value) || 0),
      pog = has("fifo_pogingen") ? Math.max(0, Number(fset("fifo_pogingen").value) || 0) : 0,
      dun = has("fifo_dun") ? Math.max(0, Number(fset("fifo_dun").value) || 0) : null,
      snr = has("fifo_snr") && fset("fifo_snr").value !== "" ? Number(fset("fifo_snr").value) : null;
    if (!max || !gap || !per) return;
    const FIFO_PER_MSG = fifoPerMsg(), naam = ($("s-name").value || lastKv.naam || "").trim(), nb = nameBytes(naam);
    // effectief aantal berichten per uur: begrensd door de tussentijd of door "per uur"
    const byGap = 3600 / gap, eff = Math.max(1, Math.floor(Math.min(byGap, per)));
    const n = Math.ceil(max / FIFO_PER_MSG);
    const nl = (x, d) => x.toLocaleString("nl-BE", { maximumFractionDigits: d });
    // Zelfde berekening als de firmware (fifo_summary in MtMenu.cpp): zonder uurplafond elke gap één
    // bericht; met plafond per uur 'per' berichten na elkaar, dan wachten tot het oudste een uur oud is.
    const capped = byGap > per;
    const s = !capped ? n * gap : Math.floor((n - 1) / per) * 3600 + (((n - 1) % per) + 1) * gap;
    const m = Math.ceil(s / 60);
    const dur = m < 60 ? `${Math.max(1, m)} min` : `${nl(Math.round(s / 360) / 10, 1)} u`;
    const gapTxt = gap < 120 ? `${gap} s` : `${nl(gap / 60, 1)} min`;
    const limit = per < byGap ? "begrensd door het aantal herhaalde berichten per uur" : "begrensd door de tijd tussen de berichten";
    const warn = gap < 30 || per > 30;
    const lines = [
      [`Max ${eff} doorgegeven ${eff === 1 ? "bericht" : "berichten"} per uur (elke ${gapTxt}, ${limit}) ≈ ${eff * FIFO_PER_MSG} ingehaalde punten per uur `
        + `(compact binair, ≈ ${FIFO_PER_MSG} punten per bericht). Volle wachtrij (${max} punten) ≈ ${n} ${n === 1 ? "bericht" : "berichten"}, leeg in ≈ ${dur}, als elke herhaling gehoord wordt.`],
      [`Alleen berichten waarvan de herhaling gehoord werd of die de server later bevestigde, tellen voor de ${per} per uur. `
        + `Pogingen zonder gehoorde herhaling tellen niet, maar het blijft bij hoogstens ${2 * per} pogingen per uur.`],
    ];
    lines.push([`≈ ${FIFO_PER_MSG} punten per bericht met de naam "${naam}" (${nb} bytes); een kortere naam zonder emoji laat meer punten toe.`]);
    if (warn) lines.push(["Opgelet: elk bericht wordt door meerdere repeaters herhaald; dit belast de mesh fel.", "warn"]);
    lines.push([`Een vol bericht = minstens ${FIFO_PER_MSG} punten; volle berichten gaan bij stabiele dekking, elke ${gapTxt}, binnen de limieten per uur.`]);
    if (has("fifo_wacht")) {
      const w = wachtSec(fset("fifo_wacht")), wt = w % 3600 === 0 ? `${w / 3600} u` : w % 60 === 0 ? `${w / 60} min` : `${w} s`;
      lines.push([w ? `Een niet-vol bericht hooguit 1× per ${wt}, geslaagd of niet, alleen bij sterke dekking en als de radio toch wakker is `
        + "(na een eigen bericht, in companionmodus of tijdens een ronde volle berichten). Daarvoor wekt de tracker zijn radio nooit."
        : "Niet-volle berichten staan uit (nooit): wat te weinig is voor een vol bericht, wacht tot er genoeg punten zijn."]);
      lines.push(["In rust luistert hij alleen 60 s als er een vol bericht klaarstaat; zonder dekking opnieuw na 10, 20 en 40 min en daarna elk uur. "
        + "SlowTrack logt alleen zolang hij beweegt."]);
    }
    if (dun != null) lines.push([dun ? `Rechte stukken: punten die minder dan ${dun} m naast de lijn tussen hun buren liggen, gaan er niet in.` : "Rechte stukken worden niet uitgedund."]);
    if (snr != null) lines.push([`Leegmaken begint pas bij stabiele dekking: een herhaling met SNR ≥ ${snr} dB, twee tekens van dekking binnen 60 s, of een bevestiging van de server. Een niet-vol bericht vraagt een herhaling met SNR ≥ ${snr} dB of een bevestiging van de server.`]);
    if (pog) lines.push([`Een inhaalbericht dat niet gehoord wordt, krijgt tot ${pog} ${pog === 1 ? "poging" : "pogingen"} (telkens langer wachten: 1, 5, 15, daarna 60 min); daarna worden zijn punten geparkeerd: lagere voorrang en 60 min wachten, maar nooit opgegeven. In rust of als companion krijgen ze bij sterke dekking een nieuwe kans, hoogstens 1× per 30 min.`]);
    for (const [t, cls] of lines) {
      const d = document.createElement("div");
      d.textContent = t;
      if (cls) d.className = cls;
      out.appendChild(d);
    }
  }
  ["track_mode", "fifo_max", "fifo_min", "fifo_gap", "fifo_per_uur", "fifo_pogingen", "fifo_dun", "fifo_snr", "fifo_wacht", "name"].forEach((k) => {
    const d = fset(k);
    if (d) { d.addEventListener("input", fifoUi); d.addEventListener("change", fifoUi); }
  });
  // Punten per Q-bericht: "<naam>: " gaat van elk bericht af. Zoals de firmware: 1 + (156 − (naam + 2) − 79) / 8, minstens 2.
  const nameBytes = (s) => new TextEncoder().encode(s || "").length;
  const perForName = (nb) => Math.max(2, 1 + Math.floor(Math.max(0, 156 - (nb + 2) - 79) / 8));
  function fifoPerMsg() {
    const naam = ($("s-name").value || "").trim();
    const fromStatus = parseInt(lastKv.fifo_per_bericht, 10);
    if (Number.isFinite(fromStatus) && (!naam || naam === lastKv.naam)) return fromStatus;   // de tracker weet het zelf
    return perForName(nameBytes(naam || lastKv.naam));
  }
  // Duur in seconden voor de statusweergave ("45 s", "12 min", "3 u")
  const ago = (s) => s < 120 ? `${s} s` : s < 7200 ? `${Math.round(s / 60)} min` : `${Math.round(s / 3600)} u`;

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
      else if (el.dataset.kind === "wacht") wachtSet(el, v);
      else if (el.dataset.kind === "bool") el.checked = v === "aan" || v === "on";
      else if (["min_speed", "min_dist", "turn_min", "turn_min_speed"].includes(k)) el.value = num(v);
      else if (k === "fast_min_batt") el.value = v === "uit" ? 0 : v.replace(/%$/, "");
      else if (["fifo_max", "fifo_min", "fifo_per_uur", "fifo_pogingen", "fifo_dun"].includes(k)) el.value = v.replace(/\D/g, "");
      else if (k === "fifo_snr") el.value = v.replace(/[^\d-]/g, "");
      else el.value = v;
    });
    gateSince(kv);
    fifoUi();                                       // roept ook slowCalc op
    setMode(kv.gekozen || "tracker");
    const known = knownTracker(kv);
    $("s-authstate").textContent = kv.authkey === "ja" ? "Authsleutel: ingesteld" : kv.authkey === "nee" ? "Authsleutel: niet ingesteld" : "Authsleutel: (firmware te oud)";
    $("s-auth").disabled = !known || !kv.authkey;
    // firmware 0.8.0: fasttrack=aan | uit(batterij), slow_buffer=<aantal gelogde punten>
    const ft = kv.fasttrack == null ? "" : kv.fasttrack === "aan" ? " · FastTrack aan"
      : ` · <span class="warn">FastTrack ${MT.esc(kv.fasttrack.replace(/\(([^)]*)\)/, " ($1)"))}</span>`;
    const sb = kv.slow_buffer == null ? "" : ` · SlowTrack: ${MT.esc(kv.slow_buffer)} ${kv.slow_buffer === "1" ? "punt" : "punten"} te versturen`;
    // firmware 0.9.0: fifo=<punten in de wachtrij>, fifo_dekking=<seconden sinds laatste dekking | ->
    const fn = kv.fifo == null ? "" : ` · FIFO: ${MT.esc(kv.fifo)} ${kv.fifo === "1" ? "punt" : "punten"} in de wachtrij`;
    const dsec = parseInt(kv.fifo_dekking, 10);
    const fp = (kv.fifo_geparkeerd == null ? "" : ` · geparkeerd: ${MT.esc(kv.fifo_geparkeerd)}`)
      + (kv.fifo_bevestigd == null ? "" : ` · bevestigd door de server: ${MT.esc(kv.fifo_bevestigd)}`);
    const fd = kv.fifo_dekking == null ? "" : ` · dekking: ${Number.isFinite(dsec) ? `${MT.esc(ago(dsec))} geleden` : "nog niet gezien"}`;
    $("s-info").innerHTML = `<div><strong>${MT.esc(kv.naam || "?")}</strong> · firmware ${MT.esc(kv.fw || "?")}
      · batterij ${MT.esc(kv.batt || "?")} · nu ${MT.esc(kv.actief || "?")}${kv.usb === "ja" ? " (USB)" : ""}${ft}${sb}${fn}${fp}${fd}</div>
      <div class="mono muted small">${MT.esc(kv.pubkey || "")}</div>
      <div class="small">${known ? `In MeshTrack als <strong>${MT.esc(known.alias)}</strong>` : '<span class="warn">Nog niet in MeshTrack</span>'}</div>`;
    $("s-use").hidden = !!known || !MT.can("trackers.manage");
    $("s-use").href = `/admin#new?pubkey=${encodeURIComponent((kv.pubkey || "").toLowerCase())}&alias=${encodeURIComponent(kv.naam || "")}`;
    if (window.MTDevice) MTDevice.onStatus(kv, known);
  }

  // Instellingen van nieuwere firmware ([data-since] met [data-keys]): kent het toestel ze niet
  // (de sleutel ontbreekt in status), dan uitgeschakeld met een hint. Opslaan slaat ze dan over.
  function gateSince(kv) {
    document.querySelectorAll("#s-form [data-since]").forEach((box) => {
      const ok = box.dataset.keys.split(/\s+/).every((k) => k in kv);
      box.classList.toggle("fwold", !ok);
      box.querySelectorAll("input,select").forEach((x) => { x.disabled = !ok; });
      let hint = box.querySelector(":scope > .fwhint");
      if (!hint) {
        hint = document.createElement("div");
        hint.className = "help fwhint";
        hint.textContent = `Vanaf firmware ${box.dataset.since}: flash de nieuwste firmware (tabblad Firmware) om dit in te stellen.`;
        const lg = box.querySelector(":scope > legend");
        if (lg) lg.after(hint); else box.prepend(hint);
      }
      hint.hidden = ok;
    });
    // één hint per groep is genoeg
    document.querySelectorAll("#s-form fieldset").forEach((fs) => {
      [...fs.querySelectorAll(".fwhint:not([hidden])")].slice(1).forEach((h) => { h.hidden = true; });
    });
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
        + "Na de herstart zet je de sleutel terug via Klaarmaken & back-ups → Op dit toestel zetten.");
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
      if (el.classList.contains("dur")) { v = durGet(el); if (v === "0" && el.dataset.offword) v = el.dataset.offword; }   // bv. fifo_wacht uit
      else if (el.dataset.kind === "wacht") v = wachtGet(el);
      else if (el.dataset.kind === "bool") v = el.checked ? (el.dataset.yes || "on") : (el.dataset.no || "off");
      else v = el.value.trim();
      if (el.dataset.spaces && v === (lastKv[el.dataset.kv || k] || "")) return;   // naam ongewijzigd
      if (v !== "" && (el.dataset.spaces || !/\s/.test(v))) cmds.push(`set ${k} ${v}`);
    });
    // FIFO (0.9.0): fifo_min mag niet boven fifo_max. Volgorde zo kiezen dat de tracker nooit een
    // tussentoestand met min > max ziet: verhoogt min boven het huidige max, dan eerst max.
    if ("fifo_min" in lastKv && "fifo_max" in lastKv) {
      const nMin = Number(fset("fifo_min").value), nMax = Number(fset("fifo_max").value);
      if (nMin > nMax) { msg($("s-msg"), `"Inhalen vanaf" (${nMin}) mag niet groter zijn dan "Wachtrij maximaal" (${nMax}).`); return; }
      const iMin = cmds.findIndex((c) => c.startsWith("set fifo_min ")), iMax = cmds.findIndex((c) => c.startsWith("set fifo_max "));
      if (iMin >= 0 && iMax >= 0) {
        const [cMin, cMax] = [cmds[iMin], cmds[iMax]];
        const maxFirst = nMin > (parseInt(lastKv.fifo_max, 10) || 0);
        cmds[Math.min(iMin, iMax)] = maxFirst ? cMax : cMin;
        cmds[Math.max(iMin, iMax)] = maxFirst ? cMin : cMax;
      }
    }
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
