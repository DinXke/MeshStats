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
    "fifo_dun", "fifo_snr"];
  // Keuzes waarbij "uit" een geldige waarde is (bij duren en getallen wordt "uit" een 0).
  const WORD_KEYS = ["track_in_companion", "led", "accel_sens", "msg_beep", "sos", "tx_beep", "heard_beep", "track_mode"];
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

  // ---- firmware-info ---------------------------------------------------------------
  async function loadFirmware() {
    try { fw = await MT.api("/api/firmware"); } catch (_) { fw = null; }
    const r = fw && fw.releases && fw.releases[0];
    if (!r) { $("fw-ver").textContent = "geen"; $("fw-notes").textContent = "Er is nog geen firmware gepubliceerd op deze server."; return; }
    $("fw-ver").textContent = `v${r.version}`;
    $("fw-notes").textContent = `${r.date} · MeshCore ${r.meshcore} · ${r.notes}`;
    $("fw-zip").href = `/static/firmware/${r.zip}`; $("fw-zip").hidden = false;
    $("fw-uf2").href = `/static/firmware/${r.uf2}`; $("fw-uf2").hidden = false;
    $("fw-zip").title = `SHA-256 ${r.zip_sha256}`;
    refresh();
  }

  // Vanuit Trackers: /devices#prov?tracker=<id>[&backup=<id>]
  const pend = new URLSearchParams((location.hash.split("?")[1]) || "");
  let pendDone = false;

  function refresh() {
    const r = fw && fw.releases && fw.releases[0];
    const connected = !!MTDev.port;
    $("fw-flash").disabled = !r || !connected || busy || !("serial" in navigator);
    $("fw-why").textContent = !("serial" in navigator) ? "Deze browser kan niet flashen: gebruik Chrome of Edge."
      : !connected ? "Verbind eerst een toestel (bovenaan)." : busy ? "Even geduld, er loopt nog een taak." : "";
    if (!connected) $("fw-dev").textContent = "verbind eerst een toestel";
    else if (!kv) $("fw-dev").textContent = "op het toestel: geen MeshTrack (of geen antwoord)";
    else $("fw-dev").textContent = r && newer(r.version, kv.fw) ? `op het toestel: v${kv.fw}, update beschikbaar` : `op het toestel: v${kv.fw}`;
    $("fw-badge").hidden = !(r && kv && newer(r.version, kv.fw));
    const allowed = MT.can("keys.manage");
    $("prov").hidden = !connected || !allowed;
    $("prov-none").hidden = !$("prov").hidden;
    $("prov-none").textContent = !allowed ? "Klaarmaken en backups vragen het recht Sleutels en backups."
      : "Verbind eerst een toestel om het klaar te maken of er een backup van te nemen.";
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
    box.innerHTML = `<b>Backup terugzetten</b> voor ${MT.esc(t ? t.alias : "tracker")}. <button type="button" class="primary" id="pend-go">Backup op dit toestel zetten</button>`;
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
    if (!m) throw new Error("backup onvolledig");
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
    if (got !== len || !want || crc32(out) !== parseInt(want[1], 16)) throw new Error("backup beschadigd (CRC klopt niet); probeer opnieuw");
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
    for (const k of MT_KEYS) {
      if (s[k] == null || s[k] === "" || String(s[k]).startsWith("(")) continue;
      if (kv && !(k in kv)) continue;           // oudere firmware op het toestel
      let v = String(s[k]);
      if (["min_speed", "min_dist", "turn_min", "turn_min_speed"].includes(k)) v = v.replace(/(km\/h|deg|m)$/, "");
      if (k === "fast_min_batt") v = v.replace(/%$/, "");
      if (["fifo_max", "fifo_min", "fifo_per_uur", "fifo_pogingen", "fifo_dun"].includes(k)) v = v.replace(/\D/g, "");
      if (k === "fifo_snr") v = v.replace(/[^\d-]/g, "");
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
    const r = fw.releases[0];
    busy = true; refresh();
    steps(["Firmware ophalen", "Backup van het toestel", "Naar de bootloader", "Flashen", "Herstarten en sleutel controleren"]);
    let backup = null;
    const before = kv ? { ...kv } : null;
    try {
      step(0);
      const zip = await (await fetch(`/static/firmware/${r.zip}`, { credentials: "same-origin" })).arrayBuffer();
      const pkg = await MTDFU.readPackage(zip);
      step(0, "done", `v${r.version}, ${Math.round(pkg.bin.length / 1024)} kB`);

      step(1);
      // Ongeldige sleutel (FFFF/0000): de identiteit is al verloren, er valt niets te bewaren. Een tracker met
      // 0.7.1 en een beschadigde opslag antwoordt dan zelfs niet meer op 'backup' (status bleef hangen).
      const invalid = !!(before && /^F{64}$|^0{64}$/i.test(before.pubkey || ""));
      if (before && before.pubkey) {
        let bin = null;
        try { bin = await dumpBin(); }
        catch (e) {
          if (!invalid) throw new Error(`backup mislukt, er is niets geflasht: ${e.message}`);
          if (!(await MT.confirm(`De backup lukt niet (${e.message}), maar de sleutel van dit toestel is toch al ongeldig (FFFF…): er valt niets meer te bewaren. `
            + "Flash je de nieuwe firmware, dan kun je daarna de opslag herstellen (Terminal: fs herstel ja) en je sleutel terugzetten vanaf de server. Toch flashen zonder backup?",
          { ok: "Toch flashen", danger: true, title: "Flashen zonder backup" }))) throw new Error("geannuleerd");
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
      } else {
        if (!(await MT.confirm("Op dit toestel draait geen MeshTrack (of het antwoordt niet). Er kan geen backup gemaakt worden en de sleutel niet gecontroleerd. Toch flashen?", { ok: "Toch flashen", danger: true }))) throw new Error("geannuleerd");
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
      if (before && before.pubkey && st.pubkey !== before.pubkey) {
        step(4, "err", `pubkey is nu ${st.pubkey.slice(0, 8)}…, was ${before.pubkey.slice(0, 8)}…`);
        if (backup) offerRestore(backup);
        throw new Error("DE SLEUTEL IS GEWIJZIGD. Zet de backup terug met de knop hieronder.");
      }
      step(4, "done", before && before.pubkey ? `v${st.fw}, sleutel ongewijzigd (${st.pubkey.slice(0, 8)}…)` : `v${st.fw || "?"}`);
      say($("dfu-msg"), `Klaar: firmware v${st.fw || r.version}${before && before.pubkey ? ", sleutel en instellingen behouden" : ""}.`, true);
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
      const ok = await MT.confirm(`Dit toestel (${kv.naam || "?"}, ${kv.pubkey.slice(0, 8)}…${cur ? `, in MeshTrack als ${cur.alias}` : ""}) krijgt de identiteit van ${doc.name} (${doc.public_key.slice(0, 8)}…).\n\nDe huidige sleutel wordt eerst als backup gedownload${cur && MT.can("keys.manage") ? " en op de server bewaard" : ""}. Doorgaan?`, { ok: "Doorgaan", danger: true, title: "Andere identiteit" });
      if (!ok) return;
    }
    busy = true; refresh();
    steps([same ? "Toestel controleren" : "Backup van de huidige sleutel", "Instellingen zetten", "Herstarten en controleren"]);
    try {
      step(0);
      if (!same) await backupNow(true, `voor klaarmaken als ${doc.name}`);
      step(0, "done");
      step(1);
      await applyDoc(doc, !same, (f, what) => { bar(f); step(1, "busy", what); });
      bar(1);
      step(1, "done");
      step(2);
      const st = await rebootAndCheck(doc.public_key, (t) => step(2, "busy", t));
      step(2, "done", `${st.naam} · ${st.pubkey.slice(0, 8)}… · ${st.path_bytes} bytes per hop · regio ${st.scope}`);
      say($("dfu-msg"), `${isRestore ? "Teruggezet" : "Klaar"}: dit toestel is nu ${st.naam}.`, true);
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
  $("fw-flash").addEventListener("click", () => {
    const r = fw.releases[0];
    const q = kv ? `Firmware v${r.version} flashen op ${kv.naam || "dit toestel"} (nu v${kv.fw})?\n\nEerst worden de opslag en de sleutel als backup gedownload. Niet loskoppelen tijdens het flashen (ongeveer een minuut).`
      : `Firmware v${r.version} flashen op het verbonden toestel?`;
    MT.confirm(q, { ok: "Flashen", title: "Firmware flashen" }).then((ok) => { if (ok) flashFlow(); });
  });
  $("prov-go").addEventListener("click", () => { if ($("prov-t").value) provisionTracker(Number($("prov-t").value)); });
  $("bk-server").addEventListener("click", async () => {
    busy = true; refresh();
    try { const r = await backupNow(true, ""); say($("prov-msg"), `Backup ${r.where}.`, true); MTDev.reload(); }
    catch (e) { say($("prov-msg"), e.message, false); } finally { busy = false; refresh(); }
  });
  $("bk-json").addEventListener("click", async () => {
    busy = true; refresh();
    try { const r = await backupNow(false, ""); say($("prov-msg"), `Backup ${r.where} (MeshCore-app-formaat, met privésleutel).`, true); }
    catch (e) { say($("prov-msg"), e.message, false); } finally { busy = false; refresh(); }
  });
  $("bk-bin").addEventListener("click", async () => {
    busy = true; refresh();
    say($("prov-msg"), "Opslag lezen…");
    try { download(`${safe(kv.naam)}_opslag_${stamp()}.bin`, await dumpBin(), "application/octet-stream"); say($("prov-msg"), "Opslag (128 kB, met privésleutel) gedownload.", true); }
    catch (e) { say($("prov-msg"), e.message, false); } finally { busy = false; refresh(); }
  });

  window.MTDevice = {
    onStatus(k, t) { kv = k && k.pubkey ? k : null; known = t || null; refresh(); },
  };

  document.addEventListener("mt-devices-ready", loadFirmware);
})();
