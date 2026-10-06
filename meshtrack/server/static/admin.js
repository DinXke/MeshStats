/* Beheerpagina: trackers (echt of virtueel), USB-instellingen, server-companion. */
(function () {
  const $ = (id) => document.getElementById(id);
  let status = null, trackers = [], sims = [];
  let icon = "";

  function msg(el, text, ok) {
    el.textContent = text || "";
    el.className = "msg " + (ok ? "ok" : "err");
  }

  // ---- server-companion -----------------------------------------------------------
  function renderCompanion(m) {
    if (!m.pubkey) return;
    $("c-name").textContent = m.name || "";
    $("c-key").textContent = m.pubkey;
    $("c-copy").disabled = false;
    const url = `meshcore://contact/add?name=${encodeURIComponent(m.name || "")}&public_key=${m.pubkey}&type=1`;
    const qr = qrcode(0, "M");
    qr.addData(url);
    qr.make();
    $("qr").innerHTML = qr.createSvgTag({ cellSize: 4, margin: 0 });
    $("qr").title = url;
  }
  $("c-copy").addEventListener("click", () => navigator.clipboard.writeText($("c-key").textContent));

  // ---- lijst -------------------------------------------------------------------------
  async function load() {
    status = await MT.api("/api/status");
    MT.meshPill($("mesh"), status.mesh);
    renderCompanion(status.mesh);
    [trackers, sims] = await Promise.all([MT.api("/api/trackers"), MT.api("/api/sims")]);
    const simBy = Object.fromEntries(sims.map((s) => [s.tracker_id, s]));
    $("trackers").innerHTML = trackers.length ? trackers.map((t) => {
      const sim = simBy[t.id];
      const kind = t.kind === "sim" ? `<span class="pill">virtueel${sim && sim.status && sim.status.running ? " · rijdt" : " · gestopt"}</span>` : "";
      const meta = [MT.ago(t.last_rx), t.last_bat != null ? `batterij ${t.last_bat}%` : null,
                    t.last_state ? MT.STATE[t.last_state] : null].filter(Boolean).join(" · ");
      return `<div class="titem">
        <span class="tico big" style="background:${MT.esc(t.color)}">${t.icon ? MTIcons.svg(t.icon) : ""}</span>
        <div class="body"><div><strong>${MT.esc(t.alias)}</strong> ${kind}${t.active ? "" : ' <span class="pill">inactief</span>'}</div>
          <div class="muted small">${MT.esc(meta)}</div>
          ${t.kind === "real" ? `<div class="mono muted small">${MT.esc(t.pubkey.slice(0, 16))}…</div>` : ""}
          ${t.notes && t.notes !== "simulator" ? `<div class="muted small">${MT.esc(t.notes)}</div>` : ""}</div>
        <div class="actions"><button data-edit="${t.id}">Bewerken</button>
          <button class="danger" data-del="${t.id}">Verwijderen</button></div></div>`;
    }).join("") : '<div class="empty">Nog geen trackers. Klik op "+ Tracker".</div>';
    document.querySelectorAll("[data-edit]").forEach((b) => b.addEventListener("click", () => edit(Number(b.dataset.edit))));
    document.querySelectorAll("[data-del]").forEach((b) => b.addEventListener("click", () => del(Number(b.dataset.del))));
    const unk = await MT.api("/api/unknown");
    $("unknown").innerHTML = unk.length ? unk.map((u) => `<div class="ev"><span class="mono">${MT.esc(u.pubkey_prefix)}</span>
      · ${MT.esc(u.reason)}<div class="muted small">${new Date(u.rx_ts * 1000).toLocaleString("nl-BE")} · <span class="mono">${MT.esc(u.text)}</span></div></div>`).join("")
      : '<div class="empty">geen</div>';
  }

  // ---- rijgedrag (simulator) ----------------------------------------------------------
  const KIND_NL = { motorway: "snelweg", motorway_link: "op-/afrit snelweg", trunk: "autoweg", trunk_link: "op-/afrit autoweg",
    primary: "gewestweg", primary_link: "afrit gewestweg", secondary: "secundaire weg", secondary_link: "afrit secundair",
    tertiary: "lokale verbindingsweg", tertiary_link: "afrit lokaal", unclassified: "buitenweg", residential: "woonstraat",
    living_street: "woonerf", service: "dienstweg / parking", road: "onbekende weg", cycleway: "fietspad", track: "veldweg",
    path: "pad", footway: "voetpad", pedestrian: "voetgangerszone", steps: "trappen", sidewalk: "stoep", crossing: "oversteek" };
  let simDefaults = null;
  async function renderSpeeds(profile, current) {
    if (!simDefaults) simDefaults = await MT.api("/api/sims/defaults");
    const def = simDefaults[profile];
    $("d-speeds").innerHTML = Object.entries(def.speeds).map(([k, v]) =>
      `<div><label>${MT.esc(KIND_NL[k] || k)}</label><div class="unit"><input type="number" min="3" max="200" data-kind="${MT.esc(k)}"
        placeholder="${v}" value="${current && current[k] ? current[k] : ""}"><span>km/u</span></div></div>`).join("");
    $("d-tmin").placeholder = def.trip[0];
    $("d-tmax").placeholder = def.trip[1];
  }
  function driveBody() {
    const speeds = {};
    document.querySelectorAll("#d-speeds [data-kind]").forEach((el) => { if (el.value) speeds[el.dataset.kind] = Number(el.value); });
    return { speed_pct: Number($("d-pct").value) || 100, max_kmh: Number($("d-max").value) || 0,
             trip_min_km: Number($("d-tmin").value) || null, trip_max_km: Number($("d-tmax").value) || null,
             roam: $("d-roam").checked, speeds };
  }
  function fillDrive(d) {
    d = d || {};
    $("d-pct").value = d.speed_pct || 100;
    $("d-max").value = d.max_kmh || 0;
    $("d-tmin").value = d.trip_min_km || "";
    $("d-tmax").value = d.trip_max_km || "";
    $("d-roam").checked = !!d.roam;
  }
  $("f-profile").addEventListener("change", () => renderSpeeds($("f-profile").value, null));

  // ---- formulier --------------------------------------------------------------------
  $("f-town").innerHTML = MT.TOWNS.map((t, i) => `<option value="${i}">${t[0]}</option>`).join("");

  function renderIcons() { MTIcons.picker($("f-icons"), icon, $("f-color").value, (id) => { icon = id; }); }
  $("f-color").addEventListener("input", renderIcons);

  function setVirtual(v) {
    $("f-virtual").checked = v;
    $("f-real").hidden = v;
    $("f-sim").hidden = !v;
  }
  $("f-virtual").addEventListener("change", () => setVirtual($("f-virtual").checked));

  function openForm(title) {
    $("ftitle").textContent = title;
    $("formcard").hidden = false;
    msg($("fmsg"), "");
    $("formcard").scrollIntoView({ behavior: "smooth", block: "start" });
  }

  function resetForm() {
    $("form").reset();
    $("f-id").value = "";
    $("f-pubkey").disabled = false;
    $("f-color").value = "#e4572e";
    $("f-active").checked = true;
    $("f-running").checked = true;
    $("f-virtual").disabled = false;
    $("contacts").hidden = true;
    icon = "";
    setVirtual(false);
    renderIcons();
    fillDrive(null);
    renderSpeeds("car", null);
    $("save").textContent = "Toevoegen";
  }

  $("new").addEventListener("click", () => { resetForm(); openForm("Tracker toevoegen"); $("f-alias").focus(); });
  $("fclose").addEventListener("click", () => { $("formcard").hidden = true; });
  $("cancel").addEventListener("click", () => { $("formcard").hidden = true; });

  function edit(id) {
    const t = trackers.find((x) => x.id === id);
    if (!t) return;
    resetForm();
    $("f-id").value = t.id;
    $("f-alias").value = t.alias;
    $("f-color").value = t.color;
    $("f-notes").value = t.notes === "simulator" ? "" : t.notes;
    $("f-active").checked = !!t.active;
    icon = t.icon || "";
    setVirtual(t.kind === "sim");
    $("f-virtual").disabled = true;            // soort wisselen = nieuwe tracker
    if (t.kind === "sim") {
      const s = sims.find((x) => x.tracker_id === id);
      if (s) {
        $("f-profile").value = s.profile;
        const ti = MT.TOWNS.findIndex((x) => Math.abs(x[1] - s.home_lat) < 1e-4 && Math.abs(x[2] - s.home_lon) < 1e-4);
        if (ti < 0) {
          $("f-town").insertAdjacentHTML("afterbegin", `<option value="custom">eigen plek (${s.home_lat.toFixed(4)}, ${s.home_lon.toFixed(4)})</option>`);
          $("f-town").value = "custom";
          $("f-town").dataset.lat = s.home_lat;
          $("f-town").dataset.lon = s.home_lon;
        } else $("f-town").value = ti;
        document.querySelectorAll("[data-sp]").forEach((el) => { if (s.params[el.dataset.sp] != null) el.value = s.params[el.dataset.sp]; });
        fillDrive(s.drive);
        renderSpeeds(s.profile, (s.drive || {}).speeds);
        $("f-loss").value = s.loss_pct;
        $("f-batt").value = s.batt_speed;
        $("f-running").checked = !!s.running;
      }
    } else {
      $("f-pubkey").value = t.pubkey;
      $("f-pubkey").disabled = true;           // sleutel = identiteit
    }
    renderIcons();
    $("save").textContent = "Opslaan";
    openForm(`Bewerken: ${t.alias}`);
  }

  async function del(id) {
    const t = trackers.find((x) => x.id === id);
    if (!t || !confirm(`"${t.alias}" verwijderen?\nAlle opgeslagen posities gaan mee weg.`)) return;
    try {
      const r = await MT.api(`/api/trackers/${id}`, { method: "DELETE" });
      msg($("fmsg"), `"${t.alias}" verwijderd. ${r.contact || ""}`, true);
      $("formcard").hidden = true;
      load();
    } catch (e) { alert(e.message); }
  }

  function simBody() {
    const params = {};
    document.querySelectorAll("[data-sp]").forEach((el) => { params[el.dataset.sp] = Number(el.value); });
    const sel = $("f-town");
    let lat, lon;
    if (sel.value === "custom") { lat = Number(sel.dataset.lat); lon = Number(sel.dataset.lon); }
    else { const t = MT.TOWNS[Number(sel.value)]; lat = t[1]; lon = t[2]; }
    return { profile: $("f-profile").value, home_lat: lat, home_lon: lon, params,
             loss_pct: Number($("f-loss").value), batt_speed: Number($("f-batt").value), running: $("f-running").checked,
             drive: driveBody() };
  }

  $("form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const id = $("f-id").value;
    const base = { alias: $("f-alias").value.trim(), color: $("f-color").value, icon, notes: $("f-notes").value,
                   active: $("f-active").checked };
    try {
      let r;
      if ($("f-virtual").checked) {
        if (id) {
          await MT.api(`/api/trackers/${id}`, { method: "PUT", body: base });
          r = await MT.api(`/api/sims/${id}`, { method: "PUT", body: { ...simBody(), alias: base.alias, color: base.color, icon } });
        } else {
          r = await MT.api("/api/sims", { method: "POST", body: { ...simBody(), ...base } });
        }
        msg($("fmsg"), "Bewaard. De simulator rijdt zodra zijn eerste route berekend is.", true);
      } else {
        if (id) r = await MT.api(`/api/trackers/${id}`, { method: "PUT", body: base });
        else r = await MT.api("/api/trackers", { method: "POST", body: { ...base, pubkey: $("f-pubkey").value.trim() } });
        msg($("fmsg"), `Bewaard. ${r.contact || ""}`, true);
      }
      $("formcard").hidden = true;
      load();
    } catch (err) { msg($("fmsg"), err.message); }
  });

  $("pick").addEventListener("click", async () => {
    try {
      const cs = (await MT.api("/api/companion/contacts")).filter((c) => c.type === 1)
        .sort((a, b) => (a.name || "").localeCompare(b.name || ""));
      const sel = $("contacts");
      sel.innerHTML = '<option value="">– kies een contact –</option>' + cs.map((c) =>
        `<option value="${MT.esc(c.public_key)}">${MT.esc(c.name || "?")} (${MT.esc(c.public_key.slice(0, 8))})</option>`).join("");
      sel.hidden = false;
      sel.focus();
    } catch (e) { msg($("fmsg"), e.message); }
  });
  $("contacts").addEventListener("change", (e) => {
    if (!e.target.value) return;
    $("f-pubkey").value = e.target.value;
    if (!$("f-alias").value) $("f-alias").value = e.target.selectedOptions[0].textContent.replace(/ \(.*\)$/, "");
  });

  $("logout").addEventListener("click", async () => { await MT.api("/api/logout", { method: "POST" }); location.href = "/login"; });

  // ---- duur-invoer (getal + eenheid) ------------------------------------------------
  const UNITS = [["s", 1, "sec"], ["m", 60, "min"], ["h", 3600, "uur"]];
  document.querySelectorAll(".dur").forEach((d) => {
    d.innerHTML = `<input type="number" min="0"><select>${UNITS.map((u) => `<option value="${u[0]}">${u[2]}</option>`).join("")}</select>`;
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

  // ---- Web Serial -------------------------------------------------------------------
  let port = null, reader = null, buf = "", lines = [], lastKv = {};
  const log = $("s-log");
  const append = (t) => { log.textContent += t; if (log.textContent.length > 20000) log.textContent = log.textContent.slice(-15000); log.scrollTop = log.scrollHeight; };
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

  function parseStatus(ls) {
    const kv = {};
    for (const l of ls) for (const m of l.matchAll(/([a-z_]+)=([^\s]+)/g)) kv[m[1]] = m[2];
    const g = /gekozen=(\w+)/.exec(ls.join("\n"));
    if (g) kv.gekozen = g[1];
    const a = /actief=(\w+)/.exec(ls.join("\n"));
    if (a) kv.actief = a[1];
    return kv;
  }

  function fillForm(kv) {
    const num = (v) => (v || "").replace(/(km\/h|deg|m)$/, "");
    document.querySelectorAll("#s-form [data-set]").forEach((el) => {
      const k = el.dataset.set;
      if (!(k in kv)) return;
      const v = kv[k];
      if (el.classList.contains("dur")) durSet(el, v);
      else if (el.dataset.kind === "bool") el.checked = v === "aan" || v === "on";
      else if (["min_speed", "min_dist", "turn_min", "turn_min_speed"].includes(k)) el.value = num(v);
      else if (k === "target") el.value = v.startsWith("(") ? "" : v;
      else el.value = v;
    });
    setMode(kv.gekozen || "tracker");
    const known = trackers.find((t) => t.pubkey === (kv.pubkey || "").toLowerCase());
    $("s-info").innerHTML = `<div><strong>${MT.esc(kv.naam || "?")}</strong> · firmware ${MT.esc(kv.fw || "?")}
      · batterij ${MT.esc(kv.batt || "?")} · nu ${MT.esc(kv.actief || "?")}${kv.usb === "ja" ? " (USB)" : ""}</div>
      <div class="mono muted small">${MT.esc(kv.pubkey || "")}</div>
      <div class="small">${known ? `In MeshTrack als <strong>${MT.esc(known.alias)}</strong>` : '<span class="warn">Nog niet in MeshTrack</span>'}</div>`;
    $("s-use").hidden = !!known;
  }

  let mode = "tracker";
  function setMode(v) {
    mode = v;
    $("s-mode").querySelectorAll("button").forEach((b) => b.classList.toggle("on", b.dataset.v === v));
  }
  $("s-mode").querySelectorAll("button").forEach((b) => b.addEventListener("click", () => setMode(b.dataset.v)));

  async function readStatus() {
    const ls = await command("status", 900);
    lastKv = parseStatus(ls);
    if (!lastKv.pubkey) { msg($("s-msg"), "Geen antwoord van de tracker. Is dit een MeshTrack-tracker?"); return false; }
    fillForm(lastKv);
    $("s-panel").hidden = false;
    msg($("s-msg"), "");
    return true;
  }

  function disconnected(why) {
    port = null;
    $("s-connect").hidden = false;
    $("s-disconnect").hidden = true;
    $("s-panel").hidden = true;
    $("s-state").className = "pill";
    $("s-state").textContent = why || "niet verbonden";
  }

  $("s-connect").addEventListener("click", async () => {
    try {
      port = await navigator.serial.requestPort();
      await port.open({ baudRate: 115200 });
      await port.setSignals({ dataTerminalReady: true });
      $("s-connect").hidden = true;
      $("s-disconnect").hidden = false;
      $("s-state").className = "pill ok";
      $("s-state").textContent = "verbonden";
      log.textContent = "";
      readLoop();
      await sleep(500);
      await send("q");                       // een eventueel open menu sluiten
      await sleep(200);
      if (!(await readStatus())) await readStatus();
    } catch (e) { msg($("s-msg"), `Verbinden mislukt: ${e.message}`); if (port) disconnected(); }
  });

  $("s-disconnect").addEventListener("click", async () => {
    const p = port;
    port = null;
    try { if (reader) await reader.cancel(); await p.close(); } catch (_) {}
    disconnected();
  });

  $("s-reload").addEventListener("click", readStatus);
  $("s-defaults").addEventListener("click", async () => {
    if (!confirm("Alle trackerinstellingen terugzetten naar standaard? (Sleutel en contacten blijven.)")) return;
    await command("defaults", 500);
    await readStatus();
    msg($("s-msg"), "Standaardwaarden hersteld.", true);
  });

  $("s-use").addEventListener("click", () => {
    resetForm();
    $("f-pubkey").value = (lastKv.pubkey || "").toLowerCase();
    $("f-alias").value = lastKv.naam || "";
    openForm("Tracker toevoegen");
  });

  $("s-target").addEventListener("click", () => {
    const pk = status && status.mesh && status.mesh.pubkey;
    if (pk) document.querySelector('#s-form [data-set="target"]').value = pk;
    else msg($("s-msg"), "De server-companion is niet verbonden; zijn pubkey is onbekend.");
  });

  $("s-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const cmds = [];
    document.querySelectorAll("#s-form [data-set]").forEach((el) => {
      const k = el.dataset.set;
      let v;
      if (el.classList.contains("dur")) v = durGet(el);
      else if (el.dataset.kind === "bool") v = el.checked ? "on" : "off";
      else v = el.value.trim();
      if (v !== "" && !/\s/.test(v)) cmds.push(`set ${k} ${v}`);
    });
    cmds.push(`mode ${mode}`);
    const bad = [];
    for (const c of cmds) {
      const out = await command(c, 250);
      if (out.some((l) => /ongeldig|onbekend|NIET/.test(l))) bad.push(c.replace(/^set /, ""));
    }
    await readStatus();
    if (bad.length) msg($("s-msg"), `Niet aanvaard: ${bad.join(", ")}`);
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

  MT.live((m) => {
    if (m.type === "mesh") MT.meshPill($("mesh"), m.mesh);
    if (m.type === "tracker" || m.type === "tracker_deleted") load();
  });
  load();
  setInterval(load, 30000);
})();
