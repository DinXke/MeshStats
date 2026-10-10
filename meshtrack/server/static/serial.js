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
    if (has("fifo_punten")) lines.push([fset("fifo_punten").value === "hoofd" ? "In de wachtrij: alleen de hoofdpunten van gemiste berichten (minder detail, minder berichten)."
      : "In de wachtrij: alle punten van gemiste berichten, uitgedund op rechte stukken (meer detail, meer inhaalberichten)."]);
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
  ["track_mode", "fifo_max", "fifo_min", "fifo_gap", "fifo_per_uur", "fifo_pogingen", "fifo_dun", "fifo_snr", "fifo_wacht", "fifo_punten", "name"].forEach((k) => {
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
          if (rawTap) rawTap(value);                    // companionprotocol: ruwe bytes
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
    for (const l of ls) for (const m of l.matchAll(/([a-z_][a-z0-9_]*)=([^\s]+)/g)) kv[m[1]] = m[2];   // ook pin31=
    // RAK3401 + 1 W: prio=aan (nog 3m12s) | prio=uit | prio=fout (zwevende ingang)
    const pr = /(?:^|\s)prio=(\w+)(?:\s*\(\s*nog\s+([^)]*?)\s*\))?/.exec(ls.join("\n"));
    if (pr) { kv.prio = pr[1].toLowerCase(); if (pr[2]) kv.prio_nog = pr[2]; }
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
      else if (k === "prio_houd") el.value = prioNum(v, 60);        // minuten (status: 5, 5m of 300s)
      else if (k === "prio_interval") el.value = prioNum(v, 1);     // seconden (status: 30, 30s of uit)
      else if (k === "pin31" || k === "prio_niveau") el.value = String(v).toLowerCase();
      else el.value = v;
    });
    gateSince(kv);
    applyNeeds(kv);
    prioUi();
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
    // RAK3401 + 1 W met pin 31 als prioriteit: prioritair ja/nee met de resterende tijd, of een fout (zwevende ingang)
    const ph = String(kv.pin31 || "").toLowerCase() === "prio" ? prioHtml(kv) : "";
    $("s-priostat").innerHTML = ph ? `Nu: ${ph}` : "";
    $("s-info").innerHTML = `<div><strong>${MT.esc(kv.naam || "?")}</strong> · firmware ${MT.esc(kv.fw || "?")}
      · batterij ${MT.esc(kv.batt || "?")} · nu ${MT.esc(kv.actief || "?")}${kv.usb === "ja" ? " (USB)" : ""}${ft}${sb}${fn}${fp}${fd}${ph ? ` · ${ph}` : ""}</div>
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

  // ---- ingang pin 31 (RAK3401 + 1 W, firmware 1.4): drukknop of prioriteit (blauwe lichten) -----
  // pin31=uit|knop|prio; prio_niveau=hoog|laag; prio_houd in minuten (1-60); prio_interval in seconden (0 = gewone interval).
  // De prio-velden zijn alleen zichtbaar (en worden alleen verstuurd) met pin31 = prio.
  const PRIO_KEYS = ["prio_niveau", "prio_houd", "prio_interval"];
  const PRIO_RULES = { prio_houd: [0, 60, "0 tot 60 (minuten; 0 = volgt de ingang)"], prio_interval: [0, 3600, "0 tot 3600 (seconden; 0 = gewone interval)"] };
  // Getal uit de status, omgerekend naar de eenheid van het veld (perUnit = seconden per eenheid).
  function prioNum(v, perUnit) {
    const m = /^(\d+)\s*(s|sec|m|min|h|u)?$/i.exec(String(v || "").trim());
    if (!m) return /^uit$/i.test(String(v || "").trim()) ? 0 : "";
    const sec = Number(m[1]) * ({ s: 1, sec: 1, m: 60, min: 60, h: 3600, u: 3600 }[(m[2] || "").toLowerCase()] || perUnit);
    return Math.round(sec / perUnit);
  }
  const WARN_ICO = '<svg class="ico" viewBox="0 0 24 24" aria-hidden="true"><path d="M12 3 2 20h20L12 3z" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linejoin="round"/><path d="M12 10v4.5" stroke="currentColor" stroke-width="2.4" stroke-linecap="round"/><circle cx="12" cy="17.3" r="1.4" fill="currentColor"/></svg>';
  // Status van de prioriteit als tekst (voor de kaart "Herkend toestel") en als HTML (statusregel).
  function prioText(kv) {
    if (!kv || kv.prio == null) return "";
    if (kv.prio === "aan") return `Prioritair${kv.prio_nog ? `, nog ${kv.prio_nog}` : ""}`;
    if (kv.prio === "uit") return "niet prioritair";
    if (kv.prio === "fout") return "Ingang zweeft (fout): controleer de bedrading / pull-up";
    return kv.prio;
  }
  function prioHtml(kv) {
    const t = prioText(kv);
    if (!t) return "";
    if (kv.prio === "aan") return `<span class="pill prio">${MT.esc(t)}</span>`;
    if (kv.prio === "fout") return `<span class="priofout" role="alert">${WARN_ICO}<span>${MT.esc(t)}</span></span>`;
    return MT.esc(t);
  }
  function prioCheck(k) {
    const el = fset(k), r = PRIO_RULES[k];
    if (!el || !r) return true;
    const v = el.value.trim(), n = Number(v);
    const ok = el.disabled || (v !== "" && Number.isInteger(n) && n >= r[0] && n <= r[1]);
    el.setAttribute("aria-invalid", ok ? "false" : "true");
    const err = $(`${el.id}-err`);
    if (err) { err.hidden = ok; err.textContent = ok ? "" : `Ongeldige waarde: geef een geheel getal van ${r[2]}.`; }
    return ok;
  }
  // Nalooptijd: keuzelijst 0 (volgt de ingang) / 1 / 5 / anders… boven het getalveld (dat wordt verstuurd)
  const HOUD_FIXED = ["0", "1", "5"];
  function houdSync() {
    const inp = $("s-priohoud"), sel = $("s-priohoud-sel");
    if (!inp || !sel) return;
    const v = String(inp.value).trim();
    if (sel.value !== "anders" || !v) sel.value = HOUD_FIXED.includes(v) ? v : v === "" ? "5" : "anders";
    $("s-priohoud-anders").hidden = sel.value !== "anders";
    sel.disabled = inp.disabled;
  }
  if ($("s-priohoud-sel")) $("s-priohoud-sel").addEventListener("change", () => {
    const sel = $("s-priohoud-sel"), inp = $("s-priohoud");
    if (sel.value !== "anders") inp.value = sel.value;
    else if (HOUD_FIXED.includes(String(inp.value))) inp.value = "10";
    $("s-priohoud-anders").hidden = sel.value !== "anders";
    prioCheck("prio_houd");
  });
  function prioUi() {
    const box = $("s-pin31box"), sel = fset("pin31");
    if (!box || !sel) return;
    const usable = !box.hidden && !box.classList.contains("hwoff") && "pin31" in lastKv;
    const prio = usable && sel.value === "prio";
    $("s-prio").hidden = !prio;
    $("s-prio-off").hidden = prio;
    PRIO_KEYS.forEach((k) => {
      const el = fset(k);
      if (!el) return;
      const has = k in lastKv;
      el.disabled = !prio || !has;
      el.dataset.skip = prio && has ? "" : "1";
      prioCheck(k);
    });
    houdSync();
  }
  // pin 31 omzetten: de knopinstellingen (SOS, dubbelklik) meteen grijs of beschikbaar, nog voor het opslaan
  if (fset("pin31")) fset("pin31").addEventListener("change", () => { applyNeeds({ ...lastKv, pin31: fset("pin31").value }); prioUi(); });
  Object.keys(PRIO_RULES).forEach((k) => { const el = fset(k); if (el) el.addEventListener("input", () => prioCheck(k)); });

  // ---- mogelijkheden van het toestel (één centraal mechanisme) ---------------------------------
  // Uit de status: board, knop (digitaal|analoog|uit|geen), buzzer (ja|geen), accel (ja|geen), gps_pinnen
  // (GPS automatisch gevonden), gps (model), tx_max. Ontbreekt een sleutel, dan geldt de tabel per bord;
  // oudere firmware zonder board= is een T1000-E met alles.
  // Velden (of groepen) zeggen met data-needs wat ze nodig hebben: "buzzer", "knop", "accel", "led",
  // "gpsdetect", "board:<id>", "key:<statussleutel>". Ontbreekt de functie: verborgen. Bestaat ze maar staat
  // ze uit (de knop met knop=uit): grijs, met de reden erbij. Verborgen of grijze velden worden niet verstuurd.
  const BOARD_CAPS = {
    t1000e: { knop: "digitaal", buzzer: true, accel: true, led: true, gpsdetect: false },
    wismesh_tag: { knop: "digitaal", buzzer: true, accel: true, led: true, gpsdetect: false },    // LIS2DH (als die bij het opstarten gevonden wordt)
    rak3401_1w: { knop: "uit", buzzer: false, accel: false, led: true, gpsdetect: true },          // voertuigtracker, analoge knop optioneel
  };
  const NEED_NAMES = { buzzer: "buzzer", knop: "knop", accel: "bewegingssensor", led: "statusled", gpsdetect: "automatische GPS-herkenning" };
  function capsOf(kv) {
    const board = kv.board || "t1000e";
    const c = { board, ...(BOARD_CAPS[board] || BOARD_CAPS.t1000e) };
    if (kv.pin31) c.knop = String(kv.pin31).toLowerCase() === "knop" ? "analoog" : "uit";   // pin 31 als knop, prio of niet gebruikt
    else if (kv.knop) c.knop = String(kv.knop).toLowerCase();
    if (kv.pin31) c.pin31 = String(kv.pin31).toLowerCase();
    if (kv.buzzer) c.buzzer = !/^(geen|nee|0)$/i.test(kv.buzzer);
    if (kv.accel) c.accel = !/^(geen|nee|0)$/i.test(kv.accel);
    if ("gps_pinnen" in kv) c.gpsdetect = true;
    c.gpsPins = kv.gps_pinnen && kv.gps_pinnen !== "-" ? kv.gps_pinnen : null;   // rx/tx@baud, of "zoekt"
    c.gpsModel = kv.gps || null;                                                 // ag3335 | at6558r | nmea_uart
    c.accelType = kv.accel_type && kv.accel_type !== "-" ? kv.accel_type : null;
    c.txMax = kv.tx_max || null;
    c.txGain = kv.tx_versterking || null;
    return c;
  }
  // "ok" | "grey" (bestaat, staat uit) | "hide" (bestaat niet op dit toestel), met de reden
  function needState(needs, kv, c) {
    let st = "ok", why = "";
    for (const n of String(needs || "").split(/\s+/).filter(Boolean)) {
      if (n.startsWith("board:")) { if (c.board !== n.slice(6)) return { st: "hide", why: "" }; continue; }
      if (n.startsWith("key:")) { if (!(n.slice(4) in kv)) return { st: "hide", why: "" }; continue; }
      if (n.startsWith("!")) { if (c[n.slice(1)]) return { st: "hide", why: "" }; continue; }   // alleen ZONDER deze functie
      if (n === "knop") {
        if (c.knop === "geen") return { st: "hide", why: "" };
        if (c.knop === "uit") { st = "grey"; why = "pin31" in kv ? "Alleen als pin 31 een drukknop is (kies dat hierboven bij Gebruik van ingang pin 31)."
          : "De knop staat uit."; }
        continue;
      }
      if (!c[n]) return { st: "hide", why: "" };
    }
    return { st, why };
  }
  // Welke instellingssleutels een toestel niet heeft (voor opslaan en terugzetten van een back-up).
  function keyNeeds(key) {
    const el = document.querySelector(`#s-form [data-set="${key}"]`);
    if (!el) return "";
    let s = "", p = el;
    while (p && p !== document) { if (p.dataset && p.dataset.needs) s += " " + p.dataset.needs; p = p.parentElement; }
    return s.trim();
  }
  const keyState = (key, kv) => needState(keyNeeds(key), kv, capsOf(kv)).st;
  function applyNeeds(kv) {
    const c = capsOf(kv);
    document.querySelectorAll("#s-form [data-needs]").forEach((box) => {
      let { st, why } = needState(box.dataset.needs, kv, c);
      if (st === "grey" && box.tagName === "SPAN") st = "hide";   // een zinsdeel in een uitleg: gewoon weglaten
      box.hidden = st === "hide";
      box.classList.toggle("hwoff", st === "grey");
      box.querySelectorAll("[data-set]").forEach((el) => {
        el.dataset.skip = st === "ok" ? "" : "1";
        const inputs = el.matches("input,select") ? [el] : [...el.querySelectorAll("input,select")];
        if (st !== "ok") inputs.forEach((x) => { x.disabled = true; });
        else if (el.dataset.set in kv && !el.closest(".fwold")) inputs.forEach((x) => { x.disabled = false; });   // weer beschikbaar
      });
      let hint = box.querySelector(":scope > .needhint");
      if (st === "grey") {
        if (!hint) { hint = document.createElement("div"); hint.className = "help needhint"; box.appendChild(hint); }
        hint.textContent = why; hint.hidden = false;
      } else if (hint) hint.hidden = true;
    });
    const note = $("s-hwnote");
    const missing = ["knop", "buzzer", "accel"].filter((n) => (n === "knop" ? c.knop === "geen" : !c[n])).map((n) => `geen ${NEED_NAMES[n]}`);
    note.hidden = !missing.length;
    note.textContent = (c.board === "rak3401_1w" ? "Voertuigtracker: " : "Dit toestel heeft ") + missing.join(", ") + ".";
  }
  // Korte samenvatting voor de kaart "Herkend toestel".
  function capsSummary(kv) {
    const c = capsOf(kv), parts = [];
    parts.push(c.knop === "geen" ? "geen knop" : c.knop === "uit" ? "knop uit" : `Knop ✓${c.knop === "analoog" ? " (analoog)" : ""}`);
    parts.push(c.buzzer ? "Buzzer ✓" : "geen buzzer");
    parts.push(c.accel ? `Bewegingssensor ✓${c.accelType ? ` (${c.accelType.toUpperCase()})` : ""}` : "geen bewegingssensor");
    const gm = c.gpsModel && c.gpsModel !== "nmea_uart" ? c.gpsModel.toUpperCase() : c.gpsModel ? "NMEA" : "";
    if (c.gpsPins === "zoekt") parts.push("GPS wordt nog gezocht");
    else if (c.gpsPins) parts.push(`GPS${gm ? ` ${gm}` : ""} automatisch gevonden op ${c.gpsPins}`);
    else if (gm) parts.push(`GPS: ${gm}`);
    if (c.txMax) parts.push(`max. ${String(c.txMax).replace(/dBm$/i, "")} dBm${c.txGain && c.txGain !== "0" ? ` (versterker ${String(c.txGain).replace(/dB$/i, "")} dB)` : ""}`);
    if (c.pin31 === "prio") parts.push("pin 31: blauwe lichten");
    return (c.board === "rak3401_1w" ? "Voertuigtracker: " : "") + parts.join(" · ");
  }
  window.MTCaps = { capsOf, keyState, capsSummary, BOARD_CAPS };
  window.MTHasMotionSensor = (kv) => capsOf(kv).accel;   // de simulatie: zonder sensor alleen rust via de GPS

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

  async function readStatus(ms = 2500) {
    let ls;
    try { ls = await until("status", /^cfg=/, ms); } catch (_) { ls = lines.slice(); }
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
    if (det && det.kind !== "bootloader") { det = null; renderDet(); }
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
    const usb = usbGuess(p.getInfo ? p.getInfo() : {});
    det = { usb, vid: usb.vid, pid: usb.pid };
    await port.open({ baudRate: 115200 });
    await port.setSignals({ dataTerminalReady: true });
    $("s-connect").hidden = true;
    $("s-disconnect").hidden = false;
    $("s-state").className = "pill ok";
    $("s-state").textContent = "verbonden";
    readLoop();
    if (usb.boot) {                                     // bootloader: niets sturen, alleen melden
      await detect("bootloader");
      await closePort();
      $("s-state").textContent = "in de bootloader";
      return null;
    }
    await sleep(700);
    await send("q");
    await sleep(200);
    let ok = await readStatus(2000);
    if (!ok && lines.some((l) => l.trim())) ok = await readStatus(2500);   // MeshTrack die nog opstart
    if (ok) { await detect("meshtrack"); return lastKv; }
    // geen MeshTrack: misschien een stock MeshCore-companion (frameprotocol over USB)
    const mc = await companionProbe().catch(() => null);
    if (mc) {
      await detect("meshcore", mc);
      msg($("s-msg"), "");
      $("s-none").textContent = `Op dit toestel draait MeshCore ${mc.ver || ""} (stock). Flash MeshTrack via het tabblad Firmware; de sleutel wordt eerst als back-up gedownload.`;
      return null;
    }
    await detect("unknown");
    return null;
  }

  // ---- toestel herkennen bij het verbinden -------------------------------------------------------
  // 1. USB-id's (port.getInfo) -> mogelijke hardware, of "staat in de bootloader";
  // 2. MeshTrack-CLI ("status"); 3. anders het MeshCore-companionprotocol over USB ('<' len16 LE payload,
  // antwoord '>' len16 payload): CMD_DEVICE_QUERY (22) en CMD_APP_START (1); 4. anders onbekend.
  // USB-id's per toestel. PID's met "?" zijn nog te bevestigen door de firmware-agent; zonder PID telt alleen de VID.
  // Firmware 0.9.8: in de app melden ALLE drie de toestellen 0x239A:0x8029 (alleen de productnaam verschilt), dus
  // het bord volgt pas uit board= in de status of uit het MeshCore-model. Bootloader: RAK 0x239A:0x0029/0x002A,
  // T1000-E vermoedelijk Seeed 0x2886 (elke PID).
  const USB_IDS = [
    { board: "t1000e", vid: 0x239a, app: [0x8029], boot: [] },
    { board: "t1000e", vid: 0x2886, app: [], boot: ["*"] },
    { board: "wismesh_tag", vid: 0x239a, app: [0x8029], boot: [0x0029, 0x002a] },
    { board: "rak3401_1w", vid: 0x239a, app: [0x8029], boot: [0x0029, 0x002a] },
  ];
  const BOARD_NAMES = { t1000e: "Seeed T1000-E", wismesh_tag: "RAK WisMesh Tag", rak3401_1w: "RAK3401 + 1 W (voertuig)" };
  // Modelnaam uit RESP_CODE_DEVICE_INFO (getManufacturerName in MeshCore) -> ons bord
  function boardFromModel(m) {
    if (/t1000/i.test(m)) return "t1000e";
    if (/wismesh\s*tag/i.test(m)) return "wismesh_tag";
    if (/rak\s*3401/i.test(m)) return "rak3401_1w";
    return null;
  }
  function usbGuess(info) {
    const vid = info && info.usbVendorId, pid = info && info.usbProductId;
    const rows = USB_IDS.filter((r) => r.vid === vid);
    const bootRows = rows.filter((r) => r.boot.includes("*") || r.boot.includes(pid));
    const boot = bootRows.length > 0;
    // kandidaten: bij de bootloader de borden met die bootloader, anders die met dit app-id (of de hele VID)
    const pick = boot ? bootRows : rows.filter((r) => r.app.includes(pid));
    return { vid, pid, boards: [...new Set((pick.length ? pick : rows).map((r) => r.board))], boot };
  }
  let det = null;                                       // laatst herkende toestel
  let rawTap = null;                                    // ruwe bytes voor het companionprotocol
  const hexOf = (a) => [...a].map((b) => b.toString(16).padStart(2, "0")).join("");
  async function writeRaw(bytes) {
    const w = port.writable.getWriter();
    try { await w.write(bytes); } finally { w.releaseLock(); }
  }
  // Eén companion-opdracht sturen en wachten op een antwoordframe met een van de codes.
  async function companionAsk(payload, codes, ms = 1500) {
    let rb = new Uint8Array(0);
    const frames = [];
    rawTap = (v) => {
      const n = new Uint8Array(rb.length + v.length); n.set(rb); n.set(v, rb.length); rb = n;
      for (;;) {
        const i = rb.indexOf(0x3e);                     // '>'
        if (i < 0) { rb = new Uint8Array(0); break; }
        if (rb.length < i + 3) { rb = rb.slice(i); break; }
        const len = rb[i + 1] | (rb[i + 2] << 8);
        if (rb.length < i + 3 + len) { rb = rb.slice(i); break; }
        frames.push(rb.slice(i + 3, i + 3 + len)); rb = rb.slice(i + 3 + len);
      }
    };
    quiet++;
    try {
      const f = new Uint8Array(3 + payload.length);
      f[0] = 0x3c; f[1] = payload.length & 0xff; f[2] = payload.length >> 8; f.set(payload, 3);
      await writeRaw(f);
      const t0 = Date.now();
      while (Date.now() - t0 < ms) {
        const hit = frames.find((x) => codes.includes(x[0]));
        if (hit) return hit;
        await sleep(40);
      }
      return null;
    } finally { rawTap = null; setTimeout(() => { quiet = Math.max(0, quiet - 1); }, 300); }
  }
  async function companionProbe() {
    const td = new TextDecoder();
    const str = (a, x, y) => td.decode(a.slice(x, y)).replace(/\0[\s\S]*$/, "").trim();
    const di = await companionAsk([22, 3], [13]);                         // CMD_DEVICE_QUERY, app-versie 3
    if (!di) return null;
    const res = { verCode: di[1], date: str(di, 8, 20), model: str(di, 20, 60), ver: str(di, 60, 80) };
    const name = new TextEncoder().encode("MeshTrack");
    const si = await companionAsk([1, 3, 0, 0, 0, 0, 0, 0, ...name], [5]); // CMD_APP_START
    if (si) { res.pubkey = hexOf(si.slice(4, 36)); res.naam = str(si, 58, si.length); }
    return res;
  }
  // Privésleutel van een stock-MeshCore-companion (CMD_EXPORT_PRIVATE_KEY 23 -> 14, of 15 als uitgeschakeld).
  async function companionExportKey() {
    const r = await companionAsk([23], [14, 15, 1], 2500);
    if (!r) throw new Error("de companion antwoordt niet op de sleutelvraag");
    if (r[0] !== 14) return null;
    return hexOf(r.slice(1, 65));
  }
  let chanNames = null;
  async function detChannel(t) {
    if (!t || !t.channel_id) return "";
    if (!chanNames) chanNames = await MT.api("/api/channels/mine").then((l) => Object.fromEntries(l.map((c) => [c.id, c.name]))).catch(() => ({}));
    return chanNames[t.channel_id] || "";
  }
  async function detect(kind, extra) {
    det = { ...(det || {}), kind, ...extra };
    if (kind === "meshtrack") {
      det.board = lastKv.board || "t1000e";
      det.boardGuessed = !lastKv.board;
      det.fw = lastKv.fw; det.pubkey = (lastKv.pubkey || "").toLowerCase(); det.naam = lastKv.naam;
      det.batt = lastKv.batt; det.modus = lastKv.actief || lastKv.gekozen;
    } else if (kind === "meshcore") {
      det.board = boardFromModel(det.model || "") || (det.usb.boards.length === 1 ? det.usb.boards[0] : null);
    } else {
      det.board = det.usb.boards.length === 1 ? det.usb.boards[0] : null;
    }
    const t = det.pubkey ? trackers.find((x) => x.pubkey === det.pubkey) : null;
    det.known = t ? { alias: t.alias, id: t.id, chan: await detChannel(t) } : null;
    renderDet();
    if (window.MTDevice && MTDevice.onDetect) MTDevice.onDetect(det);
  }
  const DET_ICON = '<svg viewBox="0 0 24 24" aria-hidden="true"><rect x="6" y="2.5" width="12" height="19" rx="3" fill="none" stroke="currentColor" stroke-width="1.8"/><circle cx="12" cy="8" r="2" fill="currentColor"/><path d="M9 16h6" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"/></svg>';
  function renderDet() {
    const card = $("det");
    if (!det) { card.hidden = true; return; }
    const d = det, rows = [];
    const hw = d.board ? BOARD_NAMES[d.board] + (d.boardGuessed ? " (aangenomen: geen type gemeld)" : "")
      : d.usb.boards.length ? `volgens USB: ${d.usb.boards.map((b) => BOARD_NAMES[b]).join(" of ")}` : "onbekend toestel";
    const fw = d.kind === "bootloader" ? "Bootloader (klaar om te flashen)"
      : d.kind === "meshtrack" ? `MeshTrack ${d.fw || "?"}`
      : d.kind === "meshcore" ? `MeshCore ${d.ver || "?"} (stock)${d.date ? `, ${d.date}` : ""}`
      : "Onbekende firmware (bv. Meshtastic)";
    $("det-ico").innerHTML = DET_ICON;
    $("det-hw").textContent = hw;
    $("det-fw").textContent = fw;
    if (d.naam) rows.push(["Naam", d.naam]);
    if (d.pubkey) rows.push(["Sleutel", `${d.pubkey.slice(0, 8)}…`]);
    if (d.kind === "meshtrack" || d.kind === "meshcore") rows.push(["In MeshTrack", d.known ? `als ${d.known.alias}${d.known.chan ? ` (kanaal ${d.known.chan})` : ""}` : "nog niet"]);
    if (d.kind === "meshtrack") rows.push(["Mogelijkheden", capsSummary(lastKv)]);
    if (d.kind === "meshtrack" && prioText(lastKv)) rows.push(["Prioriteit", prioText(lastKv)]);
    if (d.batt) rows.push(["Batterij", d.batt]);
    if (d.modus) rows.push(["Modus", d.modus]);
    if (d.model && d.kind === "meshcore") rows.push(["Model", d.model]);
    if (d.vid != null) rows.push(["USB", `${d.vid.toString(16).padStart(4, "0")}:${(d.pid || 0).toString(16).padStart(4, "0")}`]);
    $("det-dl").innerHTML = rows.map(([k, v]) => `<dt>${MT.esc(k)}</dt><dd>${MT.esc(v)}</dd>`).join("");
    // één voorgestelde volgende stap
    const act = detAction(d);
    $("det-act").hidden = !act;
    if (act) { $("det-act").textContent = act.label; $("det-act").dataset.go = act.go; }
    $("det-actnote").textContent = act ? act.note || "" : "";
    $("det-use").hidden = !(d.pubkey && !d.known && MT.can("trackers.manage") && (d.kind === "meshtrack" || d.kind === "meshcore"));
    $("det-use").href = `/admin#new?pubkey=${encodeURIComponent(d.pubkey || "")}&alias=${encodeURIComponent(d.naam || "")}`;
    card.hidden = false;
  }
  function detAction(d) {
    const latest = window.MTDevice && MTDevice.latestVersion ? MTDevice.latestVersion(d.board || "t1000e") : null;
    if (d.kind === "bootloader") return { label: "Naar Firmware", go: "fw", note: "Het toestel staat in de bootloader: sleep het .uf2-bestand op het USB-station, of trek de kabel uit en weer in om terug te gaan." };
    if (d.kind === "meshcore") return { label: "MeshTrack flashen", go: "fw", note: "Eerst wordt de sleutel van MeshCore als back-up gedownload." };
    if (d.kind === "unknown") return { label: "MeshTrack flashen", go: "fw", note: "Van onbekende firmware kan geen back-up gemaakt worden." };
    if (latest && window.MTDevice.isNewer(latest, d.fw)) return { label: `Firmware bijwerken naar ${latest}`, go: "fw", note: "Eerst wordt een back-up gedownload." };
    if (!d.known) return { label: "Klaarmaken", go: "prov", note: "Zet de sleutel en de kanalen van de server op dit toestel." };
    return { label: "Back-up nemen", go: "backup", note: "" };
  }
  $("det-act").addEventListener("click", () => {
    const go = $("det-act").dataset.go;
    const tab = go === "backup" ? "prov" : go;
    const b = document.querySelector(`#dtabs [data-tab="${tab}"]`);
    if (b) b.click();
    const focus = go === "fw" ? $("fw-flash") : go === "backup" ? $("bk-server") : $("prov-go");
    if (focus) { focus.scrollIntoView({ block: "center" }); if (!focus.disabled) focus.focus(); }
  });

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
    get det() { return det; }, companionExportKey, BOARD_NAMES,
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
    // pin 31 als prioriteit: ongeldige waarden niet versturen, de fout staat bij het veld
    const badPrio = Object.keys(PRIO_RULES).filter((k) => !prioCheck(k));
    if (badPrio.length) {
      msg($("s-msg"), "Niet opgeslagen: controleer de instellingen bij Ingang pin 31.");
      fset(badPrio[0]).focus();
      return;
    }
    const cmds = [];
    document.querySelectorAll("#s-form [data-set]").forEach((el) => {
      const k = el.dataset.set;
      if (!((el.dataset.kv || k) in lastKv)) return;   // oudere firmware kent deze instelling niet
      if (el.dataset.skip === "1") return;              // bv. bewegingsgevoeligheid zonder bewegingssensor
      let v;
      if (el.classList.contains("dur")) { v = durGet(el); if (v === "0" && el.dataset.offword) v = el.dataset.offword; }   // bv. fifo_wacht uit
      else if (el.dataset.kind === "wacht") v = wachtGet(el);
      else if (el.dataset.unit) { v = el.value.trim(); if (v !== "" && Number(v) > 0) v += el.dataset.unit; }   // duur met eenheid
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
