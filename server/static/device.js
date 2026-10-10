/* Toestel via USB: firmware flashen (met backup en sleutelcontrole), een tracker
   klaarmaken met een sleutel van de server, en backups naar of van de server.
   Gebruikt MTDev (admin.js) voor de seriële verbinding en MTDFU (dfu.js).

   Backups volgen het exportformaat van de MeshCore-app (name, public_key,
   private_key, radio_settings, channels) met een extra blok "meshtrack". */
(function () {
  const $ = (id) => document.getElementById(id);
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  const BAD = /ongeldig|onbekend|NIET|geweigerd|gebruik:|mislukt/;
  const MT_KEYS = ["min_speed", "min_dist", "turn_min", "turn_min_speed", "min_interval", "max_interval", "still_timeout",
    "heartbeat", "fix_timeout", "fix_timeout_hb", "track_in_companion", "accel_sens", "led",
    "sample", "chan", "msg_beep",
    "slow_log", "slow_send", "fast_min_batt", "sos", "tx_beep", "heard_beep",   // vanaf 0.8.0
    "track_mode", "fifo_max", "fifo_min", "fifo_gap", "fifo_per_uur", "fifo_pogingen",   // vanaf 0.9.0 (FIFO-modus)
    "fifo_dun", "fifo_snr", "fifo_wacht",   // fifo_wacht vanaf 0.9.1
    "verzoek", "rx_beweging", "verzoek_beep",   // vanaf 0.9.5 (locatieverzoeken)
    "fifo_punten",   // vanaf 0.9.7
    "pin31", "prio_niveau", "prio_houd", "prio_interval",   // RAK3401 + 1 W: ingang pin 31 als drukknop of prioriteit (blauwe lichten)
    "rust_gps_check"];   // 0.9.8: rust via de GPS bij toestellen zonder bewegingssensor
  // Keuzes waarbij "uit" een geldige waarde is (bij duren en getallen wordt "uit" een 0).
  const WORD_KEYS = ["track_in_companion", "led", "accel_sens", "msg_beep", "sos", "tx_beep", "heard_beep", "track_mode", "fifo_wacht", "verzoek", "rx_beweging", "verzoek_beep", "fifo_punten",
    "pin31", "prio_niveau", "rust_gps_check"];
  let fw = null, kv = null, known = null, busy = false;

  function say(el, text, ok) { el.textContent = text || ""; el.className = "msg " + (ok ? "ok" : ok === false ? "err" : ""); }
  const fwNum = (v) => (v || "0").split(".").map((x) => parseInt(x, 10) || 0);
  const newer = (a, b) => { const x = fwNum(a), y = fwNum(b); for (let i = 0; i < 3; i++) if (x[i] !== y[i]) return x[i] > y[i]; return false; };
  const stamp = () => new Date().toISOString().slice(0, 16).replace(/[-:T]/g, "").replace(/^(\d{8})/, "$1-");
  const safe = (s) => (s || "toestel").replace(/[^\p{L}\p{N}_-]+/gu, "_").replace(/^_+|_+$/g, "") || "toestel";

  function download(name, data, type) {
    const a = document.createElement("a");
    a.href = URL.createObjectURL(new Blob([data], { type }));
    a.download = name;
    document.body.appendChild(a);
    a.click();
    setTimeout(() => { URL.revokeObjectURL(a.href); a.remove(); }, 2000);
  }

  // ---- firmware-info, per toestel (bord) --------------------------------------------------
  // firmware.json: per release "boards": { t1000e: {zip, uf2, zip_sha256, app_size}, wismesh_tag: {...} };
  // oudere releases hebben alleen de velden bovenaan (dat is de T1000-E). Een tracker meldt board= in zijn
  // status; zonder board= (oudere firmware) is het een T1000-E.
  const BOARDS = { t1000e: "Seeed T1000-E", wismesh_tag: "RAK WisMesh Tag", rak3401_1w: "RAK3401 + 1 W (voertuig)" };
  // Hoe het toestel in de bootloader komt (de firmware-agent vult details aan).
  const BOOT_HELP = {
    t1000e: "De pagina zet de T1000-E zelf in de bootloader (met de 1200-baudtruc via USB). Voor de UF2: zet het toestel in de "
      + "bootloader; er verschijnt dan een USB-station waarop je het .uf2-bestand sleept.",
    rak3401_1w: "De pagina zet de RAK3401 zelf in de bootloader (1200-baudtruc via USB). Lukt dat niet, druk dan twee keer snel na "
      + "elkaar op de resetknop van de RAK3401: er verschijnt een USB-station waarop je het .uf2-bestand sleept.",
    wismesh_tag: "De pagina zet de WisMesh Tag zelf in de bootloader (1200-baudtruc via USB, zoals bij een RAK4631). Lukt dat niet, "
      + "druk dan twee keer snel na elkaar op de resetknop: er verschijnt een USB-station waarop je het .uf2-bestand sleept.",
  };
  // De bootloader weigert alleen T1000-E <-> RAK (SoftDevice v7 tegen v6, sd_req 0x0123 tegen 0x00B6). WisMesh Tag en
  // RAK3401 delen sd_req, device_type en de UF2-basis: een verkeerd pakket tussen die twee wordt AANVAARD.
  const SD_NOTE = "De bootloader weigert wel een pakket van de T1000-E op een RAK-toestel en omgekeerd, maar tussen de WisMesh Tag "
    + "en de RAK3401 aanvaardt hij ook het verkeerde pakket. Daar beschermt alleen deze pagina.";
  const RAK = ["wismesh_tag", "rak3401_1w"];
  const CONFIRM_WORD = { wismesh_tag: "TAG", rak3401_1w: "RAK3401" };
  // Naam uit firmware.json (boards.<id>.name), anders de vaste naam.
  function boardName(b) {
    const x = fw && fw.releases ? fw.releases.find((r) => r.boards && r.boards[b] && r.boards[b].name) : null;
    return x ? x.boards[b].name : BOARDS[b] || b;
  }
  // Bevestigen door een woord te typen (voor een RAK-pakket op een toestel waarvan het type niet vaststaat).
  function typedConfirm(text, word) {
    return new Promise((resolve) => {
      const d = document.createElement("dialog");
      d.className = "dlg";
      d.innerHTML = `<form method="dialog"><h2>Toestel niet vastgesteld</h2><p class="cbody"></p>
        <label for="tc-in" class="tclab"></label><input id="tc-in" autocomplete="off" autocapitalize="characters" spellcheck="false">
        <div class="row"><button value="ok" class="danger" disabled>Toch flashen</button><button value="no" type="submit">Annuleren</button></div></form>`;
      d.querySelector(".cbody").textContent = text;
      d.querySelector(".tclab").textContent = `Typ ${word} om te bevestigen`;
      const inp = d.querySelector("#tc-in"), ok = d.querySelector("button[value=ok]");
      inp.addEventListener("input", () => { ok.disabled = inp.value.trim().toUpperCase() !== word; });
      document.body.appendChild(d);
      d.addEventListener("close", () => { resolve(d.returnValue === "ok" && inp.value.trim().toUpperCase() === word); d.remove(); });
      d.showModal();
      inp.focus();
    });
  }
  // Het bord van het verbonden toestel: board= van MeshTrack, of afgeleid uit het MeshCore-model.
  const devBoard = () => (kv && kv.board) || (!kv && MTDev.det && MTDev.det.kind === "meshcore" ? MTDev.det.board : null);
  function relFiles(r, board) {
    if (r.boards && r.boards[board]) return r.boards[board];
    if (board === "t1000e" && r.zip) return { zip: r.zip, uf2: r.uf2, zip_sha256: r.zip_sha256, app_size: r.app_size };
    return null;
  }
  const lsGet = (k) => { try { return localStorage.getItem(k); } catch (_) { return null; } };
  const lsSet = (k, v) => { try { localStorage.setItem(k, v); } catch (_) { /* privévenster */ } };
  const selBoard = () => $("fw-board").value;
  // Nieuwste release met een build voor het gekozen toestel.
  const latestFor = (board) => (fw && fw.releases ? fw.releases.find((r) => relFiles(r, board)) : null) || null;
  function renderFirmware() {
    const board = selBoard();
    [...$("fw-board").options].forEach((o) => { o.textContent = boardName(o.value); });
    const top = fw && fw.releases && fw.releases[0];
    if (!top) { $("fw-ver").textContent = "geen"; $("fw-notes").textContent = "Er is nog geen firmware gepubliceerd op deze server."; return; }
    const r = latestFor(board), f = r && relFiles(r, board);
    $("fw-ver").textContent = r ? `v${r.version}` : "nog niet beschikbaar";
    $("fw-notes").textContent = r ? `${r.date} · MeshCore ${r.meshcore} · ${r.notes}` : `Er is nog geen firmware voor de ${boardName(board)}.`;
    $("fw-zip").hidden = $("fw-uf2").hidden = !f;
    if (f) {
      $("fw-zip").href = `/static/firmware/${f.zip}`; $("fw-zip").setAttribute("download", f.zip);
      $("fw-zip").title = f.zip_sha256 ? `SHA-256 ${f.zip_sha256}` : "";
      $("fw-uf2").hidden = !f.uf2;
      if (f.uf2) { $("fw-uf2").href = `/static/firmware/${f.uf2}`; $("fw-uf2").setAttribute("download", f.uf2); }
    }
    $("fw-boot").textContent = `${BOOT_HELP[board]} ${SD_NOTE}`;
    // alle versies, met de bestanden voor dit toestel
    $("fw-rels").innerHTML = fw.releases.map((x) => {
      const ff = relFiles(x, board);
      const links = ff ? `<a href="/static/firmware/${MT.esc(ff.zip)}" download="${MT.esc(ff.zip)}">.zip</a>`
        + (ff.uf2 ? ` · <a href="/static/firmware/${MT.esc(ff.uf2)}" download="${MT.esc(ff.uf2)}">.uf2</a>` : "") : '<span class="muted">nog niet beschikbaar</span>';
      return `<div class="fwrel"><strong>v${MT.esc(x.version)}</strong> <span class="muted">${MT.esc(x.date || "")}</span> · ${links}</div>`;
    }).join("");
    refresh();
  }
  async function loadFirmware() {
    try { fw = await MT.api("/api/firmware"); } catch (_) { fw = null; }
    renderFirmware();
  }
  // Keuze van het toestel: bij een verbonden tracker met board= volgt de keuze die; anders de laatste keuze.
  $("fw-board").value = BOARDS[lsGet("mt.fwBoard")] ? lsGet("mt.fwBoard") : "t1000e";
  $("fw-board").addEventListener("change", () => { lsSet("mt.fwBoard", selBoard()); renderFirmware(); });

  // Vanuit Trackers: /devices#prov?tracker=<id>[&backup=<id>]
  const pend = new URLSearchParams((location.hash.split("?")[1]) || "");
  let pendDone = false;

  function refresh() {
    const r = latestFor(selBoard());
    const connected = !!MTDev.port;
    const db = devBoard();
    const mismatch = !!(db && db !== selBoard());
    $("fw-flash").disabled = !r || !connected || busy || !("serial" in navigator) || mismatch;
    $("fw-why").textContent = !("serial" in navigator) ? "Deze browser kan niet flashen: gebruik Chrome of Edge."
      : !connected ? "Verbind eerst een toestel (bovenaan)." : busy ? "Even geduld, er loopt nog een taak."
      : mismatch ? `Dit toestel is een ${boardName(db)}; kies bij Toestel de juiste firmware.`
      : !r ? `Er is nog geen firmware voor de ${boardName(selBoard())}.` : "";
    const det = MTDev.det;
    $("fw-boardnote").textContent = det && det.kind === "bootloader"
      ? (det.usb && det.usb.boards.some((b) => RAK.includes(b))
        ? "Het toestel staat in de bootloader: de pagina kan niet nagaan welk toestel het is. Kies zorgvuldig; de bootloaders van de WisMesh Tag en de RAK3401 aanvaarden elkaars firmware."
        : "Het toestel staat in de bootloader (volgens USB een T1000-E). Die bootloader weigert firmware voor een RAK-toestel.")
      : !kv ? (db ? `verbonden: ${boardName(db)} (MeshCore)` : "") : kv.board ? `verbonden: ${boardName(kv.board)}`
      : "verbonden: geen type gemeld (oudere firmware, dus een T1000-E)";
    $("fw-boardnote").classList.toggle("warn", !!(det && det.kind === "bootloader"));

    if (!connected) $("fw-dev").textContent = "verbind eerst een toestel";
    else if (!kv) $("fw-dev").textContent = "op het toestel: geen MeshTrack (of geen antwoord)";
    else $("fw-dev").textContent = r && newer(r.version, kv.fw) ? `op het toestel: v${kv.fw}, update beschikbaar` : `op het toestel: v${kv.fw}`;
    $("fw-badge").hidden = !(r && kv && !mismatch && newer(r.version, kv.fw));
    const allowed = MT.can("keys.manage");
    $("prov").hidden = !connected || !allowed;
    $("prov-none").hidden = !$("prov").hidden;
    $("prov-none").textContent = !allowed ? "Klaarmaken en back-ups vragen het recht Sleutels en back-ups."
      : "Verbind eerst een toestel om het klaar te maken of er een back-up van te nemen.";
    if (!$("prov").hidden) {
      const ts = MTDev.trackers.filter((t) => t.kind === "real" && t.keys);
      const cur = $("prov-t").value;
      $("prov-t").innerHTML = ts.length ? ts.map((t) => `<option value="${t.id}">${MT.esc(t.alias)} (${MT.esc(t.pubkey.slice(0, 8))})${kv && kv.pubkey && kv.pubkey.toLowerCase() === t.pubkey ? " · dit toestel" : ""}</option>`).join("")
        : '<option value="">Geen trackers met een sleutel op de server</option>';
      if (pend.get("tracker") && ts.some((t) => String(t.id) === pend.get("tracker"))) $("prov-t").value = pend.get("tracker");
      else if (cur && ts.some((t) => String(t.id) === cur)) $("prov-t").value = cur;
      else if (known && ts.some((t) => t.id === known.id)) $("prov-t").value = known.id;
      $("prov-go").disabled = !ts.length || busy || !kv;
      ["bk-server", "bk-json", "bk-bin"].forEach((id) => { $(id).disabled = !kv || busy; });
      $("bk-server").disabled = !kv || !known || busy;
      $("bk-server").title = known ? "" : "Dit toestel staat nog niet in MeshTrack";
      showPending();
    }
  }

  // Een backup die vanuit Trackers gekozen werd: klaar om terug te zetten.
  async function showPending() {
    const tid = pend.get("tracker"), bid = pend.get("backup");
    if (!tid || pendDone) return;
    const t = MTDev.trackers.find((x) => String(x.id) === tid);
    const box = $("prov-pending");
    box.hidden = false;
    if (!bid) {
      box.innerHTML = `<b>${MT.esc(t ? t.alias : "Tracker")}</b> is gekozen. Klik op <em>Op dit toestel zetten</em> om dit toestel klaar te maken.`;
      return;
    }
    box.innerHTML = `<b>Back-up terugzetten</b> voor ${MT.esc(t ? t.alias : "tracker")}. <button type="button" class="primary" id="pend-go">Back-up op dit toestel zetten</button>`;
    $("pend-go").onclick = async () => {
      try {
        const doc = await MT.api(`/api/trackers/${tid}/keys/${bid}`);
        pendDone = true;
        box.hidden = true;
        provisionFlow(doc, true);
      } catch (e) { say($("prov-msg"), e.message, false); }
    };
  }

  // ---- uitlezen van het toestel ----------------------------------------------------
  async function deviceDoc() {
    const st = MTDev.parseStatus(await MTDev.until("status", /^cfg=/, 4000));
    const kl = await MTDev.until("key export", /^privkey=|^onbekend/, 3000, true);
    const pk = kl.find((l) => l.startsWith("privkey="));
    if (!pk) throw new Error("dit toestel kan zijn sleutel niet exporteren (firmware 0.3.0 of nieuwer nodig)");
    const cl = await MTDev.until("chan list", /^chan=einde/, 4000, true);
    const channels = cl.map((l) => /^chan=(\d+)\|([0-9A-Fa-f]{32})\|(.*)$/.exec(l)).filter(Boolean)
      .map((m) => ({ name: m[3].trim(), secret: m[2].toLowerCase() }));
    const settings = {};
    for (const k of MT_KEYS) if (st[k] != null) settings[k] = st[k];
    return {
      name: st.naam || "",
      public_key: (st.pubkey || "").toLowerCase(),
      private_key: pk.slice(8).trim().toLowerCase(),
      radio_settings: {
        frequency: Math.round(parseFloat(st.freq) * 1000), bandwidth: Math.round(parseFloat(st.bw) * 1000),
        spreading_factor: parseInt(st.sf, 10), coding_rate: parseInt(st.cr, 10), tx_power: parseInt(st.tx, 10),
      },
      channels,
      meshtrack: { fw: st.fw, path_bytes: parseInt(st.path_bytes, 10) || 2, scope: st.scope === "-" ? "" : st.scope,
                   mode: st.gekozen || "tracker", settings, saved: new Date().toISOString() },
    };
  }

  // Ruwe dump van de opslag (0xD4000-0xF4000), met CRC-controle.
  async function dumpBin() {
    const ls = await MTDev.until("backup", /^BACKUP-END/, 60000, true);
    const begin = ls.findIndex((l) => l.startsWith("BACKUP-BEGIN"));
    const m = /start=0x([0-9A-F]+) len=0x([0-9A-F]+)/.exec(ls[begin] || "");
    if (!m) throw new Error("back-up onvolledig");
    const start = parseInt(m[1], 16), len = parseInt(m[2], 16);
    const out = new Uint8Array(len);
    let got = 0;
    for (const l of ls.slice(begin + 1)) {
      const r = /^([0-9A-F]{8}) ([0-9A-F]{128})$/.exec(l.trim());
      if (!r) continue;
      const off = parseInt(r[1], 16) - start;
      for (let i = 0; i < 64; i++) out[off + i] = parseInt(r[2].substr(i * 2, 2), 16);
      got += 64;
    }
    const want = /crc32=([0-9A-F]{8})/.exec(ls.find((l) => l.startsWith("BACKUP-END")) || "");
    if (got !== len || !want || crc32(out) !== parseInt(want[1], 16)) throw new Error("back-up beschadigd (CRC klopt niet); probeer opnieuw");
    return out;
  }
  function crc32(u8) {
    let c = ~0;
    for (const b of u8) { c ^= b; for (let k = 0; k < 8; k++) c = (c >>> 1) ^ (0xedb88320 & -(c & 1)); }
    return (~c) >>> 0;
  }

  function jsonName(doc) { return `${safe(doc.name)}_meshcore_config_${stamp()}.json`; }

  // Backup van het verbonden toestel: altijd downloaden, en naar de server als de tracker gekend is.
  async function backupNow(toServer, note) {
    const doc = await deviceDoc();
    download(jsonName(doc), JSON.stringify(doc, null, 2), "application/json");
    let where = "gedownload";
    const t = MTDev.trackers.find((x) => x.pubkey === doc.public_key);
    if (toServer && t && MT.can("keys.manage")) {
      await MT.api(`/api/trackers/${t.id}/keys`, { method: "POST", body: { doc, kind: "backup", note: note || "" } });
      where += " en op de server bewaard";
    }
    return { doc, where };
  }

  // ---- instellingen op het toestel zetten -------------------------------------------
  async function run(cmd, silent) {
    const ls = (await MTDev.until(cmd, /^(?!\[).*(bewaard|onbekend|ongeldig|NIET|gebruik|geweigerd)/, 3000, silent))
      .filter((l) => !l.startsWith("["));          // logregels van de firmware negeren
    const bad = ls.find((l) => BAD.test(l));
    if (bad) throw new Error(`${cmd.startsWith("key") ? "key import" : cmd.split(" ").slice(0, 2).join(" ")}: ${bad.trim()}`);
  }

  async function applyDoc(doc, importKey, progress) {
    const r = doc.radio_settings || {}, mt = doc.meshtrack || {};
    const steps = [];
    if (importKey) steps.push([`key import ${doc.private_key}`, "sleutel", true]);
    if (doc.name) steps.push([`set name ${doc.name}`, "naam"]);
    if (r.frequency) steps.push([`set radio ${r.frequency / 1000} ${r.bandwidth / 1000} ${r.spreading_factor} ${r.coding_rate}`, "radio"]);
    if (r.tx_power != null) steps.push([`set tx ${r.tx_power}`, "zendvermogen"]);
    steps.push([`set path_bytes ${Math.max(2, mt.path_bytes || 2)}`, "2-byte paden"]);
    const scope = mt.scope != null ? mt.scope : ((doc.channels || [])[0] || {}).region_scope_name;
    if (scope != null) steps.push([`set scope ${String(scope).replace(/^#/, "") || "-"}`, "regio"]);
    (doc.channels || []).forEach((c, i) => { if (c.name && /^[0-9a-f]{32}$/i.test(c.secret || "")) steps.push([`chan set ${i} ${c.secret} ${c.name}`, `kanaal ${c.name}`, true]); });
    const s = mt.settings || {};
    const skipped = [];
    for (const k of MT_KEYS) {
      if (s[k] == null || s[k] === "" || String(s[k]).startsWith("(")) continue;
      if (kv && !(k in kv)) continue;           // oudere firmware op het toestel
      // back-up van een ander toestel: wat dit toestel niet heeft (bv. buzzer, knop), niet zetten
      if (kv && window.MTCaps && MTCaps.keyState(k, kv) === "hide") { skipped.push(k); continue; }
      let v = String(s[k]);
      if (["min_speed", "min_dist", "turn_min", "turn_min_speed"].includes(k)) v = v.replace(/(km\/h|deg|m)$/, "");
      if (k === "fast_min_batt") v = v.replace(/%$/, "");
      if (["fifo_max", "fifo_min", "fifo_per_uur", "fifo_pogingen", "fifo_dun"].includes(k)) v = v.replace(/\D/g, "");
      if (k === "fifo_snr") v = v.replace(/[^\d-]/g, "");
      // prio_houd (minuten) en prio_interval (seconden) als duur met eenheid; 0 blijft 0
      if (k === "prio_houd" || k === "prio_interval") {
        const m = /^(\d+)\s*(s|m|h)?$/i.exec(v);
        if (m) {
          const sec = Number(m[1]) * ({ s: 1, m: 60, h: 3600 }[(m[2] || "").toLowerCase()] || (k === "prio_houd" ? 60 : 1));
          v = !sec ? "0" : k === "prio_houd" ? `${Math.round(sec / 60)}m` : `${sec}s`;
        }
      }
      if (v === "uit" && !WORD_KEYS.includes(k)) v = "0";
      steps.push([`set ${k} ${v}`, k]);
    }
    // fifo_min mag nooit boven fifo_max komen: gaat min boven het huidige max, dan max eerst, anders min eerst
    const iMin = steps.findIndex((x) => x[1] === "fifo_min"), iMax = steps.findIndex((x) => x[1] === "fifo_max");
    if (iMin >= 0 && iMax >= 0 && kv) {
      const maxFirst = (parseInt(s.fifo_min, 10) || 0) > (parseInt(kv.fifo_max, 10) || 0);
      const [a, b] = [steps[iMin], steps[iMax]];
      steps[Math.min(iMin, iMax)] = maxFirst ? b : a;
      steps[Math.max(iMin, iMax)] = maxFirst ? a : b;
    }
    if (mt.authkey && /^[0-9a-f]{32}$/i.test(mt.authkey) && (!kv || "authkey" in kv)) steps.push([`set authkey ${mt.authkey}`, "authsleutel", true]);
    if (mt.mode) steps.push([`mode ${mt.mode}`, "modus"]);
    for (let i = 0; i < steps.length; i++) {
      progress(i / steps.length, steps[i][1]);
      await run(steps[i][0], !!steps[i][2]);
    }
    return skipped;
  }

  // Na een herstart komt de app terug als (nieuwe) poort met hetzelfde USB-id.
  async function reconnect(info, waitMs) {
    const t0 = Date.now();
    while (Date.now() - t0 < waitMs) {
      const ports = await navigator.serial.getPorts();
      const p = ports.find((x) => { const i = x.getInfo(); return !x.readable && i.usbVendorId === info.usbVendorId && i.usbProductId === info.usbProductId; });
      if (p) {
        try { await sleep(800); return await MTDev.open(p); } catch (_) { /* nog niet klaar */ }
      }
      await sleep(700);
    }
    return null;
  }

  async function rebootAndCheck(expectPub, setStep) {
    const info = MTDev.port.getInfo();
    try { await MTDev.send("reboot"); } catch (_) {}
    await sleep(300);
    await MTDev.close();
    setStep("wachten tot het toestel herstart…");
    let st = await reconnect(info, 25000);
    if (!st) st = await askPort("Het toestel kwam niet vanzelf terug. Kies het opnieuw.", null);
    if (!st) throw new Error("toestel niet teruggevonden; verbind het opnieuw en controleer de pubkey");
    if (expectPub && (st.pubkey || "").toLowerCase() !== expectPub.toLowerCase())
      throw new Error(`pubkey na herstart is ${(st.pubkey || "?").slice(0, 8)}, verwacht ${expectPub.slice(0, 8)}`);
    return st;
  }

  // Een knop in het stappenpaneel: requestPort mag alleen na een klik.
  function askPort(text, filters) {
    return new Promise((resolve) => {
      $("dfu").hidden = false;
      $("dfu-act").innerHTML = `<span class="small">${MT.esc(text)}</span> <button class="primary" id="dfu-pick">Poort kiezen</button> <button id="dfu-skip">Annuleren</button>`;
      $("dfu-pick").addEventListener("click", async () => {
        try {
          const p = await navigator.serial.requestPort(filters ? { filters } : {});
          $("dfu-act").innerHTML = "";
          resolve(filters ? p : await MTDev.open(p));
        } catch (_) { /* geannuleerd: knop blijft */ }
      });
      $("dfu-skip").addEventListener("click", () => { $("dfu-act").innerHTML = ""; resolve(null); });
    });
  }

  // ---- stappenpaneel ---------------------------------------------------------------
  let stepEls = [];
  function steps(names) {
    $("dfu").hidden = false;
    $("dfu-steps").innerHTML = names.map((n) => `<li>${MT.esc(n)} <span class="small muted"></span></li>`).join("");
    stepEls = [...$("dfu-steps").children];
    $("dfu-bar").style.width = "0%";
    $("dfu-act").innerHTML = "";
    say($("dfu-msg"), "");
  }
  function step(i, state, detail) {
    stepEls.forEach((li, k) => { if (k < i) li.className = "done"; });
    const li = stepEls[i];
    if (!li) return;
    li.className = state || "busy";
    li.querySelector("span").textContent = detail || "";
  }
  const bar = (f) => { $("dfu-bar").style.width = `${Math.round(f * 100)}%`; };

  // ---- flashen ---------------------------------------------------------------------
  async function flashFlow() {
    const board = selBoard(), r = latestFor(board), files = relFiles(r, board);
    busy = true; refresh();
    steps(["Firmware ophalen", "Back-up van het toestel", "Naar de bootloader", "Flashen", "Herstarten en sleutel controleren"]);
    let backup = null, stock = null;
    const before = kv ? { ...kv } : null;
    try {
      step(0);
      const zip = await (await fetch(`/static/firmware/${files.zip}`, { credentials: "same-origin" })).arrayBuffer();
      const pkg = await MTDFU.readPackage(zip);
      step(0, "done", `v${r.version} voor de ${boardName(board)}, ${Math.round(pkg.bin.length / 1024)} kB`);

      step(1);
      // Ongeldige sleutel (FFFF/0000): de identiteit is al verloren, er valt niets te bewaren. Een tracker met
      // 0.7.1 en een beschadigde opslag antwoordt dan zelfs niet meer op 'backup' (status bleef hangen).
      const invalid = !!(before && /^F{64}$|^0{64}$/i.test(before.pubkey || ""));
      if (before && before.pubkey) {
        let bin = null;
        try { bin = await dumpBin(); }
        catch (e) {
          if (!invalid) throw new Error(`back-up mislukt, er is niets geflasht: ${e.message}`);
          if (!(await MT.confirm(`De back-up lukt niet (${e.message}), maar de sleutel van dit toestel is toch al ongeldig (FFFF…): er valt niets meer te bewaren. `
            + "Flash je de nieuwe firmware, dan kun je daarna de opslag herstellen (Terminal: fs herstel ja) en je sleutel terugzetten vanaf de server. Toch flashen zonder back-up?",
          { ok: "Toch flashen", danger: true, title: "Flashen zonder back-up" }))) throw new Error("geannuleerd");
        }
        if (bin) download(`${safe(before.naam)}_opslag_${stamp()}.bin`, bin, "application/octet-stream");
      }
      if (before && before.pubkey && invalid) {
        step(1, "done", "overgeslagen: sleutel ongeldig, niets te bewaren");
      } else if (before && before.pubkey) {
        if (newer(before.fw, "0.2.9")) {
          backup = (await backupNow(true, `voor flashen v${r.version}`)).doc;
          step(1, "done", "opslag (.bin) en sleutel (.json) gedownload" + (MTDev.trackers.some((t) => t.pubkey === backup.public_key) && MT.can("keys.manage") ? ", ook op de server" : ""));
        } else step(1, "done", "opslag (.bin) gedownload");
      } else if (MTDev.det && MTDev.det.kind === "meshcore") {
        // stock MeshCore: geen MeshTrack-back-up, wel de privésleutel via de companion (CMD_EXPORT_PRIVATE_KEY)
        const d = MTDev.det;
        stock = { pubkey: (d.pubkey || "").toLowerCase(), naam: d.naam || "" };
        const priv = await MTDev.companionExportKey();
        if (priv) {
          backup = { name: stock.naam, public_key: stock.pubkey, private_key: priv, radio_settings: {}, channels: [],
            meshtrack: { note: `MeshCore ${d.ver || "?"} (stock), voor het flashen van MeshTrack v${r.version}` } };
          download(`${safe(stock.naam)}_meshcore-sleutel_${stamp()}.json`, JSON.stringify(backup, null, 2), "application/json");
          step(1, "done", "sleutel van MeshCore (stock) als .json gedownload");
        } else {
          if (!(await MT.confirm("Deze MeshCore-firmware laat de sleutel niet exporteren. Bij het flashen blijft de opslag normaal behouden, "
            + "maar zonder back-up is er geen weg terug als de sleutel toch verandert. Toch flashen?", { ok: "Toch flashen", danger: true, title: "Geen back-up mogelijk" }))) throw new Error("geannuleerd");
          step(1, "done", "overgeslagen: de export van de sleutel staat uit in deze firmware");
        }
      } else {
        if (!(await MT.confirm("Op dit toestel draait geen MeshTrack (of het antwoordt niet). Er kan geen back-up gemaakt worden en de sleutel niet gecontroleerd. Toch flashen?", { ok: "Toch flashen", danger: true }))) throw new Error("geannuleerd");
        step(1, "done", "overgeslagen (geen MeshTrack)");
      }

      step(2);
      const info = MTDev.port.getInfo();
      const app = await MTDev.close();
      await MTDFU.touch(app);
      let boot = null;
      const t0 = Date.now();
      while (!boot && Date.now() - t0 < 6000) {
        await sleep(500);
        boot = (await navigator.serial.getPorts()).find((p) => !p.readable && p !== app &&
          !(p.getInfo().usbVendorId === info.usbVendorId && p.getInfo().usbProductId === info.usbProductId));
      }
      if (!boot) boot = await askPort("Kies de bootloader-poort (de eerste keer is dat nodig; daarna gaat het vanzelf).",
        [{ usbVendorId: 0x2886 }, { usbVendorId: 0x239a }]);
      if (!boot) throw new Error("geen bootloader gekozen; het toestel staat nu in de bootloader. Trek de USB-kabel uit en terug in om te annuleren.");
      step(2, "done");

      step(3);
      await MTDFU.flash(boot, pkg, (f, t) => { bar(f); step(3, "busy", t); });
      step(3, "done");

      step(4, "busy", "wachten tot het toestel terugkomt…");
      await sleep(2500);
      let st = await reconnect(info, 25000);
      if (!st) st = await askPort("Het toestel kwam niet vanzelf terug. Kies het opnieuw.", null);
      if (!st) throw new Error("geflasht, maar het toestel is niet teruggevonden; verbind het opnieuw en controleer de pubkey");
      const prevKey = ((before && before.pubkey) || (stock && stock.pubkey) || "").toLowerCase();
      if (prevKey && (st.pubkey || "").toLowerCase() !== prevKey) {
        step(4, "err", `pubkey is nu ${(st.pubkey || "?").slice(0, 8)}…, was ${prevKey.slice(0, 8)}…`);
        if (backup) offerRestore(backup);
        throw new Error("DE SLEUTEL IS GEWIJZIGD. Zet de back-up terug met de knop hieronder.");
      }
      step(4, "done", prevKey ? `v${st.fw}, sleutel ongewijzigd (${st.pubkey.slice(0, 8)}…)` : `v${st.fw || "?"}`);
      say($("dfu-msg"), `Klaar: firmware v${st.fw || r.version}${prevKey ? (stock ? ", sleutel behouden" : ", sleutel en instellingen behouden") : ""}.`, true);
      // De status opnieuw lezen: tijdens het herstarten kan een statusvraag mislukt zijn, en dan dachten de
      // andere tabbladen (back-up, klaarmaken) dat er geen MeshTrack-toestel verbonden was.
      try { await MTDev.readStatus(); } catch (_) { /* de knop Vernieuwen bovenaan doet hetzelfde */ }
    } catch (e) {
      say($("dfu-msg"), e.message, false);
      const i = stepEls.findIndex((li) => li.className === "busy");
      if (i >= 0) step(i, "err", "");
    } finally {
      busy = false; refresh();
    }
  }

  function offerRestore(doc) {
    $("dfu-act").innerHTML = '<button class="danger" id="dfu-restore">Sleutel en instellingen terugzetten</button>';
    $("dfu-restore").addEventListener("click", () => provisionFlow(doc, true));
  }

  // ---- klaarmaken / terugzetten -----------------------------------------------------
  async function provisionFlow(doc, isRestore) {
    if (!kv) { say($("prov-msg"), "Geen MeshTrack-toestel verbonden. Flash eerst de firmware.", false); return; }
    if (newer("0.3.0", kv.fw)) { say($("prov-msg"), `Firmware v${kv.fw} kan geen sleutel ontvangen. Flash eerst v0.3.0 of nieuwer.`, false); return; }
    const same = kv.pubkey.toLowerCase() === doc.public_key;
    if (!same) {
      const cur = MTDev.trackers.find((t) => t.pubkey === kv.pubkey.toLowerCase());
      const ok = await MT.confirm(`Dit toestel (${kv.naam || "?"}, ${kv.pubkey.slice(0, 8)}…${cur ? `, in MeshTrack als ${cur.alias}` : ""}) krijgt de identiteit van ${doc.name} (${doc.public_key.slice(0, 8)}…).\n\nDe huidige sleutel wordt eerst als back-up gedownload${cur && MT.can("keys.manage") ? " en op de server bewaard" : ""}. Doorgaan?`, { ok: "Doorgaan", danger: true, title: "Andere identiteit" });
      if (!ok) return;
    }
    busy = true; refresh();
    steps([same ? "Toestel controleren" : "Back-up van de huidige sleutel", "Instellingen zetten", "Herstarten en controleren"]);
    try {
      step(0);
      if (!same) await backupNow(true, `voor klaarmaken als ${doc.name}`);
      step(0, "done");
      step(1);
      const skipped = await applyDoc(doc, !same, (f, what) => { bar(f); step(1, "busy", what); });
      bar(1);
      step(1, "done", skipped.length ? `overgeslagen (niet op dit toestel): ${skipped.join(", ")}` : "");
      step(2);
      const st = await rebootAndCheck(doc.public_key, (t) => step(2, "busy", t));
      step(2, "done", `${st.naam} · ${st.pubkey.slice(0, 8)}… · ${st.path_bytes} bytes per hop · regio ${st.scope}`);
      say($("dfu-msg"), `${isRestore ? "Teruggezet" : "Klaar"}: dit toestel is nu ${st.naam}.${skipped.length ? ` Niet gezet, want dit toestel heeft ze niet: ${skipped.join(", ")}.` : ""}`, true);
      say($("prov-msg"), "");
      MTDev.reload();
    } catch (e) {
      say($("dfu-msg"), e.message, false);
      const i = stepEls.findIndex((li) => li.className === "busy");
      if (i >= 0) step(i, "err", "");
    } finally {
      busy = false; refresh();
    }
  }

  async function provisionTracker(tid) {
    try {
      const doc = await MT.api(`/api/trackers/${tid}/provision`);
      await provisionFlow(doc, false);
    } catch (e) { say($("prov-msg"), e.message, false); }
  }

  // ---- knoppen ---------------------------------------------------------------------
  $("fw-flash").addEventListener("click", async () => {
    const board = selBoard(), r = latestFor(board);
    if (!r) return;
    // nooit een pakket voor een ander toestel
    const db = devBoard();
    if (db && db !== board) {
      say($("dfu-msg"), `Niet geflasht: dit toestel meldt zich als ${boardName(db)}, maar je koos firmware voor de ${boardName(board)}. Kies bij Toestel de juiste.`, false);
      $("dfu").hidden = false;
      return;
    }
    if (!db && RAK.includes(board)) {
      const why = kv ? "Dit toestel meldt geen type (oudere MeshTrack, dus normaal een T1000-E)."
        : "Het type van dit toestel is niet vastgesteld (geen MeshTrack-status en geen MeshCore-model).";
      const ok = await typedConfirm(`${why} Je koos firmware voor de ${boardName(board)}. ${SD_NOTE}`, CONFIRM_WORD[board]);
      if (!ok) return;
    }
    const q = kv ? `Firmware v${r.version} voor de ${boardName(board)} flashen op ${kv.naam || "dit toestel"} (nu v${kv.fw})?\n\nEerst worden de opslag en de sleutel als back-up gedownload. Niet loskoppelen tijdens het flashen (ongeveer een minuut).`
      : `Firmware v${r.version} voor de ${boardName(board)} flashen op het verbonden toestel?`;
    MT.confirm(q, { ok: "Flashen", title: "Firmware flashen" }).then((ok) => { if (ok) flashFlow(); });
  });
  $("prov-go").addEventListener("click", () => { if ($("prov-t").value) provisionTracker(Number($("prov-t").value)); });
  $("bk-server").addEventListener("click", async () => {
    busy = true; refresh();
    try { const r = await backupNow(true, ""); say($("prov-msg"), `Back-up ${r.where}.`, true); MTDev.reload(); }
    catch (e) { say($("prov-msg"), e.message, false); } finally { busy = false; refresh(); }
  });
  $("bk-json").addEventListener("click", async () => {
    busy = true; refresh();
    try { const r = await backupNow(false, ""); say($("prov-msg"), `Back-up ${r.where} (MeshCore-app-formaat, met privésleutel).`, true); }
    catch (e) { say($("prov-msg"), e.message, false); } finally { busy = false; refresh(); }
  });
  $("bk-bin").addEventListener("click", async () => {
    busy = true; refresh();
    say($("prov-msg"), "Opslag lezen…");
    try { download(`${safe(kv.naam)}_opslag_${stamp()}.bin`, await dumpBin(), "application/octet-stream"); say($("prov-msg"), "Opslag (128 kB, met privésleutel) gedownload.", true); }
    catch (e) { say($("prov-msg"), e.message, false); } finally { busy = false; refresh(); }
  });

  window.MTDevice = {
    latestVersion: (board) => { const x = latestFor(board); return x ? x.version : null; },
    isNewer: (a, b) => newer(a, b),
    // herkend toestel: de keuze in Firmware volgt (MeshTrack met board= doet dat ook via onStatus)
    onDetect(d) {
      if (!d) return;
      let want = d.board && BOARDS[d.board] ? d.board : null;
      if (!want && d.usb && d.usb.boards.length && !d.usb.boards.includes(selBoard())) want = d.usb.boards[0];
      if (want && selBoard() !== want) { $("fw-board").value = want; lsSet("mt.fwBoard", want); renderFirmware(); } else refresh();
    },
    onStatus(k, t) {
      kv = k && k.pubkey ? k : null; known = t || null;
      // het toestel meldt zijn type: de keuze volgt (en wordt onthouden)
      if (kv && kv.board && BOARDS[kv.board] && selBoard() !== kv.board) { $("fw-board").value = kv.board; lsSet("mt.fwBoard", kv.board); renderFirmware(); return; }
      refresh();
    },
  };

  document.addEventListener("mt-devices-ready", loadFirmware);
})();
