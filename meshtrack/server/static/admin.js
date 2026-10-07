/* Beheerpagina: trackers (echt of virtueel), USB-instellingen, server-companion. */
(function () {
  const $ = (id) => document.getElementById(id);
  let status = null, trackers = [], sims = [], tgroups = [];
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
    [trackers, sims, tgroups] = await Promise.all([MT.api("/api/trackers"), MT.can("sims.manage") ? MT.api("/api/sims") : [],
      MT.api("/api/tracker-groups")]);
    renderTgroups();
    const simBy = Object.fromEntries(sims.map((s) => [s.tracker_id, s]));
    $("trackers").innerHTML = trackers.length ? trackers.map((t) => {
      const sim = simBy[t.id];
      const kind = t.kind === "sim" ? `<span class="pill">virtueel${sim && sim.status && sim.status.running ? " · rijdt" : " · gestopt"}</span>` : "";
      const meta = [MT.ago(t.last_rx), t.last_bat != null ? `batterij ${t.last_bat}%` : null,
                    t.last_state ? MT.STATE[t.last_state] : null, t.last_via && t.kind === "real" ? `via ${t.last_via}` : null,
                    t.kind === "real" && t.has_authkey ? "authsleutel" : null].filter(Boolean).join(" · ");
      return `<div class="titem">
        <span class="tico big" style="background:${MT.esc(t.color)}">${t.icon ? MTIcons.svg(t.icon) : ""}</span>
        <div class="body"><div><strong>${MT.esc(t.alias)}</strong> ${kind}${t.active ? "" : ' <span class="pill">inactief</span>'}${t.lost ? ` <span class="pill lost">verloren${t.lost_seen ? " · terug gezien " + MT.esc(MT.ago(t.lost_seen)) : ""}</span>` : ""}${t.keys ? ` <span class="pill" title="${t.keys} sleutel(s)/backup(s) op de server">sleutel op server</span>` : ""}</div>
          <div class="muted small">${MT.esc(meta)}</div>
          ${t.kind === "real" ? `<div class="mono muted small">${MT.esc(t.pubkey.slice(0, 16))}…</div>` : ""}
          ${t.notes && t.notes !== "simulator" ? `<div class="muted small">${MT.esc(t.notes)}</div>` : ""}
          ${(t.groups || []).length ? `<div class="chipsline">${t.groups.map((id) => tgroups.find((g) => g.id === id)).filter(Boolean).map((g) => `<span class="pill" style="border-color:${MT.esc(g.color)}">${MT.esc(g.name)}</span>`).join(" ")}</div>` : ""}</div>
        <div class="actions"><button data-edit="${t.id}">Bewerken</button>
          <button class="danger" data-del="${t.id}">Verwijderen</button></div></div>`;
    }).join("") : '<div class="empty">Nog geen trackers. Klik op "+ Tracker".</div>';
    document.querySelectorAll("[data-edit]").forEach((b) => b.addEventListener("click", () => edit(Number(b.dataset.edit))));
    document.querySelectorAll("[data-del]").forEach((b) => b.addEventListener("click", () => del(Number(b.dataset.del))));
    if (!MT.can("trackers.manage")) return;
    const unk = await MT.api("/api/unknown");
    $("unknown").innerHTML = unk.length ? unk.map((u) => `<div class="ev"><span class="mono">${MT.esc(u.pubkey_prefix)}</span>
      · ${MT.esc(u.reason)}<div class="muted small">${new Date(u.rx_ts * 1000).toLocaleString("nl-BE")} · <span class="mono">${MT.esc(u.text)}</span></div></div>`).join("")
      : '<div class="empty">geen</div>';
  }

  // ---- trackergroepen ----------------------------------------------------------------
  function tgChecks(el, selected) {
    el.innerHTML = trackers.map((t) => `<label class="mini"><input type="checkbox" value="${t.id}"${selected.includes(t.id) ? " checked" : ""}>
      <i style="background:${MT.esc(t.color)}"></i>${MT.esc(t.alias)}${t.kind === "sim" ? " (sim)" : ""}</label>`).join("")
      || '<span class="muted">Nog geen trackers.</span>';
  }
  function renderTgroups() {
    $("tgcard").hidden = !MT.can("trackers.manage");
    $("tglist").innerHTML = tgroups.map((g) => `<div class="titem">
      <span class="tico big" style="background:${MT.esc(g.color)}"></span>
      <div class="body"><div><strong>${MT.esc(g.name)}</strong> <span class="muted small">${g.trackers.length} tracker(s)</span></div>
        <div class="muted small">${MT.esc(g.description || "")}</div>
        <div class="muted small">${MT.esc(g.trackers.map((id) => (trackers.find((t) => t.id === id) || {}).alias).filter(Boolean).join(", "))}</div></div>
      <div class="actions"><button type="button" data-tgedit="${g.id}">Bewerken</button><button type="button" class="danger" data-tgdel="${g.id}">Verwijderen</button></div></div>`).join("")
      || '<div class="empty">Nog geen trackergroepen.</div>';
    $("tglist").querySelectorAll("[data-tgedit]").forEach((b) => b.addEventListener("click", () => openTg(tgroups.find((g) => g.id === Number(b.dataset.tgedit)))));
    $("tglist").querySelectorAll("[data-tgdel]").forEach((b) => b.addEventListener("click", async () => {
      const g = tgroups.find((x) => x.id === Number(b.dataset.tgdel));
      if (!confirm(`Trackergroep "${g.name}" verwijderen? De trackers zelf blijven; gebruikersgroepen die via deze groep keken, zien ze niet meer.`)) return;
      try { await MT.api(`/api/tracker-groups/${g.id}`, { method: "DELETE" }); load(); } catch (e) { alert(e.message); }
    }));
  }
  function openTg(g) {
    $("tg-form").hidden = false;
    $("tg-id").value = g ? g.id : "";
    $("tg-name").value = g ? g.name : "";
    $("tg-color").value = g ? g.color : "#64748b";
    $("tg-desc").value = g ? g.description : "";
    tgChecks($("tg-trackers"), g ? g.trackers : []);
    msg($("tg-msg"), "");
  }
  $("tg-new").addEventListener("click", () => openTg(null));
  $("tg-cancel").addEventListener("click", () => { $("tg-form").hidden = true; });
  $("tg-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const id = $("tg-id").value;
    const body = { name: $("tg-name").value.trim(), color: $("tg-color").value, description: $("tg-desc").value,
      trackers: [...$("tg-trackers").querySelectorAll("input:checked")].map((c) => Number(c.value)) };
    try {
      await MT.api(id ? `/api/tracker-groups/${id}` : "/api/tracker-groups", { method: id ? "PUT" : "POST", body });
      $("tg-form").hidden = true;
      load();
    } catch (err) { msg($("tg-msg"), err.message); }
  });
  function fillFormGroups(selected) {
    $("f-tgwrap").hidden = !tgroups.length || !MT.can("trackers.manage");
    $("f-tgroups").innerHTML = tgroups.map((g) => `<label class="mini"><input type="checkbox" value="${g.id}"${selected.includes(g.id) ? " checked" : ""}>
      <i style="background:${MT.esc(g.color)}"></i>${MT.esc(g.name)}</label>`).join("");
  }

  // ---- rijgedrag (simulator) ----------------------------------------------------------
  const KIND_NL = { motorway: "snelweg", motorway_link: "op-/afrit snelweg", trunk: "autoweg", trunk_link: "op-/afrit autoweg",
    primary: "gewestweg", primary_link: "afrit gewestweg", secondary: "secundaire weg", secondary_link: "afrit secundair",
    tertiary: "lokale verbindingsweg", tertiary_link: "afrit lokaal", unclassified: "buitenweg", residential: "woonstraat",
    living_street: "woonerf", service: "dienstweg / parking", road: "onbekende weg", cycleway: "fietspad", track: "veldweg",
    path: "pad", footway: "voetpad", pedestrian: "voetgangerszone", steps: "trappen", sidewalk: "stoep", crossing: "oversteek" };
  let simDefaults = null;
  async function renderSpeeds(profile, current) {
    if (MT.me && !MT.can("sims.manage")) return;
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
    $("f-data").hidden = true;
    $("f-histrow").hidden = true;
    $("f-genkey").checked = false;
    $("f-pubrow").hidden = false;
    $("f-genrow").hidden = !MT.can("keys.manage");
    $("f-genhelp").hidden = !MT.can("keys.manage");
    $("f-keys").hidden = true;
    fillFormGroups([]);
    icon = "";
    setVirtual(false);
    renderIcons();
    fillDrive(null);
    renderSpeeds("car", null);
    if (MT.me && !MT.can("trackers.manage")) { setVirtual(true); $("f-virtual").disabled = true; }
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
    $("f-lost").checked = !!t.lost;
    $("f-authrow").hidden = t.kind !== "real" || !(MT.can("trackers.serial") || MT.can("keys.manage"));
    $("f-authkey").textContent = t.has_authkey ? "verborgen" : "nog geen";
    $("f-authcopy").hidden = true;
    fillFormGroups(t.groups || []);
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
        $("f-histrow").hidden = false;
        fillDrive(s.drive);
        renderSpeeds(s.profile, (s.drive || {}).speeds);
        $("f-loss").value = s.loss_pct;
        $("f-batt").value = s.batt_speed;
        $("f-running").checked = !!s.running;
      }
    } else {
      $("f-pubkey").value = t.pubkey;
      $("f-pubkey").disabled = true;           // sleutel = identiteit
      $("f-genrow").hidden = true;             // alleen bij een nieuwe tracker
      $("f-genhelp").hidden = true;
    }
    renderIcons();
    $("save").textContent = "Opslaan";
    loadDataInfo(t.id);
    if (window.MTDevice) MTDevice.onEdit(t);
    openForm(`Bewerken: ${t.alias}`);
  }

  $("f-history").addEventListener("click", async () => {
    if (!confirm("Alle posities van deze simulator wissen en de historiek opnieuw opbouwen?")) return;
    try {
      await MT.api(`/api/sims/${$("f-id").value}/history`, { method: "POST" });
      msg($("fmsg"), "De historiek wordt op de achtergrond opgebouwd; de simulator rijdt daarna verder.", true);
    } catch (e) { msg($("fmsg"), e.message); }
  });

  async function loadDataInfo(id) {
    $("f-data").hidden = false;
    const d = await MT.api(`/api/trackers/${id}/data`);
    const f = (ts) => (ts ? new Date(ts * 1000).toLocaleString("nl-BE") : "–");
    $("f-datainfo").textContent = d.n ? `${d.n} posities, van ${f(d.first)} tot ${f(d.last)}` : "Geen opgeslagen posities.";
  }
  async function purge(all) {
    const id = $("f-id").value;
    const days = all ? 0 : Number($("f-purgedays").value);
    if (!all && !(days > 0)) return;
    const what = all ? "ALLE posities" : `alle posities ouder dan ${days} dagen`;
    if (!confirm(`${what} van deze tracker wissen? Dit kan niet ongedaan gemaakt worden.`)) return;
    try {
      const r = await MT.api(`/api/trackers/${id}/purge`, { method: "POST", body: { older_than_days: days } });
      msg($("fmsg"), `${r.deleted} posities gewist.`, true);
      loadDataInfo(id);
    } catch (e) { msg($("fmsg"), e.message); }
  }
  $("f-purge").addEventListener("click", () => purge(false));
  $("f-purgeall").addEventListener("click", () => purge(true));

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
                   active: $("f-active").checked, lost: $("f-lost").checked };
    const groups = [...$("f-tgroups").querySelectorAll("input:checked")].map((c) => Number(c.value));
    try {
      let r;
      if ($("f-virtual").checked) {
        if (id) {
          await MT.api(`/api/trackers/${id}`, { method: "PUT", body: { ...base, ...(MT.can("trackers.manage") ? { groups } : {}) } });
          r = await MT.api(`/api/sims/${id}`, { method: "PUT", body: { ...simBody(), alias: base.alias, color: base.color, icon } });
        } else {
          r = await MT.api("/api/sims", { method: "POST", body: { ...simBody(), ...base } });
          if (groups.length && r && r.tracker_id && MT.can("trackers.manage")) await MT.api(`/api/trackers/${r.tracker_id}`, { method: "PUT", body: { groups } });
        }
        msg($("fmsg"), "Bewaard. De simulator rijdt zodra zijn eerste route berekend is.", true);
      } else {
        const gen = !id && $("f-genkey").checked;
        if (id) r = await MT.api(`/api/trackers/${id}`, { method: "PUT", body: { ...base, groups } });
        else r = await MT.api("/api/trackers", { method: "POST", body: { ...base, groups, pubkey: gen ? null : $("f-pubkey").value.trim(), generate_key: gen } });
        msg($("fmsg"), `Bewaard. ${r.contact || ""}`, true);
        if (gen && window.MTDevice) { await load(); MTDevice.offerProvision(r.tracker); return; }
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
  $("f-genkey").addEventListener("change", () => { $("f-pubrow").hidden = $("f-genkey").checked; });
  const groupKey = (k) => k.match(/.{1,4}/g).join(" ");
  async function showAuth(fresh) {
    const id = $("f-id").value;
    if (!id) return;
    if (fresh && !confirm("Een nieuwe authsleutel maken? De tracker moet dan ook de nieuwe krijgen, anders worden zijn kanaalberichten geweigerd.")) return;
    try {
      const { authkey } = await MT.api(`/api/trackers/${id}/authkey${fresh ? "?new=1" : ""}`);
      $("f-authkey").textContent = groupKey(authkey);
      $("f-authcopy").hidden = false;
      $("f-authcopy").onclick = () => navigator.clipboard.writeText(authkey);
    } catch (e) { msg($("fmsg"), e.message); }
  }
  $("f-authshow").addEventListener("click", () => showAuth(false));
  $("f-authnew").addEventListener("click", () => showAuth(true));
  $("contacts").addEventListener("change", (e) => {
    if (!e.target.value) return;
    $("f-pubkey").value = e.target.value;
    if (!$("f-alias").value) $("f-alias").value = e.target.selectedOptions[0].textContent.replace(/ \(.*\)$/, "");
  });


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
  let port = null, reader = null, buf = "", lines = [], lastKv = {}, quiet = 0;
  const log = $("s-log");
  const append = (t) => {
    if (quiet) return;                      // privésleutel of backup: niet in de terminal
   log.textContent += t; if (log.textContent.length > 20000) log.textContent = log.textContent.slice(-15000); log.scrollTop = log.scrollHeight; };
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
    $("s-authstate").textContent = kv.authkey === "ja" ? "Authsleutel: ingesteld" : kv.authkey === "nee" ? "Authsleutel: niet ingesteld" : "Authsleutel: (firmware te oud)";
    $("s-auth").disabled = !known || !kv.authkey;
    $("s-ritme").textContent = kv.ritme ? `Ritme nu: ${kv.ritme}.` : "Deze firmware kent het ritme volgens de ontvangst nog niet (vanaf 0.4.0).";
    const known = trackers.find((t) => t.pubkey === (kv.pubkey || "").toLowerCase());
    $("s-info").innerHTML = `<div><strong>${MT.esc(kv.naam || "?")}</strong> · firmware ${MT.esc(kv.fw || "?")}
      · batterij ${MT.esc(kv.batt || "?")} · nu ${MT.esc(kv.actief || "?")}${kv.usb === "ja" ? " (USB)" : ""}</div>
      <div class="mono muted small">${MT.esc(kv.pubkey || "")}</div>
      <div class="small">${known ? `In MeshTrack als <strong>${MT.esc(known.alias)}</strong>` : '<span class="warn">Nog niet in MeshTrack</span>'}</div>`;
    $("s-use").hidden = !!known || !MT.can("trackers.manage");
    if (window.MTDevice) MTDevice.onStatus(kv, known);
  }

  let mode = "tracker";
  function setMode(v) {
    mode = v;
    $("s-mode").querySelectorAll("button").forEach((b) => b.classList.toggle("on", b.dataset.v === v));
  }
  $("s-mode").querySelectorAll("button").forEach((b) => b.addEventListener("click", () => setMode(b.dataset.v)));

  // Kanalen op het toestel, voor de keuzelijst "Verzenden via".
  let devChans = [], srvChans = [];
  async function readChannels() {
    let ls = [];
    try { ls = await until("chan list", /^chan=einde/, 3000, true); } catch (_) { /* oude firmware */ }
    devChans = ls.map((l) => /^chan=(\d+)\|([0-9A-Fa-f]{32})\|(.*)$/.exec(l)).filter(Boolean)
      .map((m) => ({ slot: Number(m[1]), secret: m[2].toLowerCase(), name: m[3].trim() }));
    srvChans = await MT.api("/api/channels/device").catch(() => []);
    const cur = lastKv.transport === "kanaal" ? devChans.find((d) => d.slot === Number(lastKv.chan)) : null;
    const curSrv = cur && srvChans.find((s) => s.secret === cur.secret);
    const others = devChans.filter((d) => !srvChans.some((s) => s.secret === d.secret));
    $("s-via").innerHTML = '<option value="dm">DM naar de server (standaard)</option>'
      + (srvChans.length ? `<optgroup label="Kanalen van de server">${srvChans.map((s) => `<option value="srv:${s.id}">${MT.esc(s.name)}</option>`).join("")}</optgroup>` : "")
      + (others.length ? `<optgroup label="Andere kanalen op het toestel">${others.map((d) => `<option value="dev:${d.slot}">${d.slot}: ${MT.esc(d.name)}</option>`).join("")}</optgroup>` : "");
    $("s-via").value = !cur ? "dm" : curSrv ? `srv:${curSrv.id}` : `dev:${cur.slot}`;
  }

  // "Verzenden via" toepassen: kanaal (met sleutel) en authsleutel op het toestel zetten.
  async function applyVia(bad) {
    const v = $("s-via").value;
    const run = async (cmd, silent) => {
      const out = await until(cmd, /bewaard|ongeldig|onbekend|NIET|gebruik|kies eerst/, 3000, silent);
      if (out.some((l) => /ongeldig|onbekend|NIET|gebruik|kies eerst/.test(l))) bad.push(cmd.split(" ").slice(0, 2).join(" "));
    };
    if (v === "dm") { if (lastKv.transport !== "dm") await run("set transport dm"); return; }
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
      const t = trackers.find((x) => x.pubkey === (lastKv.pubkey || "").toLowerCase());
      if (t && lastKv.authkey !== "ja") {
        const { authkey } = await MT.api(`/api/trackers/${t.id}/authkey`);
        await run(`set authkey ${authkey}`, true);
      } else if (!t) msg($("s-msg"), "Deze tracker staat nog niet in MeshTrack: zet hem erin, anders weigert een kanaal met ondertekening zijn berichten.");
      if (s.region && lastKv.scope !== s.region) await run(`set scope ${s.region}`);
    } else slot = Number(v.slice(4));
    if (!lastKv.scope || lastKv.scope === "-")
      msg($("s-msg"), "Let op: deze tracker heeft geen regio (scope). Berichten zonder regio worden steeds vaker geblokkeerd; zet er een (bv. be) bij de kanaalinstelling op de server.");
    await run(`set chan ${slot}`);
    await run("set transport kanaal");
  }

  async function readStatus() {
    let ls;
    try { ls = await until("status", /^cfg=/, 2500); } catch (_) { ls = lines.slice(); }
    lastKv = parseStatus(ls);
    if (!lastKv.pubkey) {
      msg($("s-msg"), "Geen antwoord van een MeshTrack-tracker. Staat er nog andere firmware op? Flash dan eerst MeshTrack (hierboven).");
      if (window.MTDevice) MTDevice.onStatus(null, null);
      return false;
    }
    fillForm(lastKv);
    if (lastKv.transport) await readChannels();
    $("s-via").closest("fieldset").querySelector("#s-via").disabled = !lastKv.transport;
    $("s-panel").hidden = false;
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

  async function closePort() {
    const p = port;
    port = null;
    try { if (reader) await reader.cancel(); } catch (_) {}
    try { await p.close(); } catch (_) {}
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
    } catch (e) { msg($("s-msg"), `Verbinden mislukt: ${e.message}`); if (port) disconnected(); }
  });

  $("s-disconnect").addEventListener("click", closePort);

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

  $("s-auth").addEventListener("click", async () => {
    const t = trackers.find((x) => x.pubkey === (lastKv.pubkey || "").toLowerCase());
    if (!t) return;
    try {
      const { authkey } = await MT.api(`/api/trackers/${t.id}/authkey`);
      const out = await until(`set authkey ${authkey}`, /bewaard|ongeldig|onbekend/, 3000, true);
      msg($("s-msg"), out.some((l) => /bewaard/.test(l)) ? "Authsleutel staat op de tracker." : "Authsleutel niet aanvaard.", out.some((l) => /bewaard/.test(l)));
      await readStatus();
    } catch (e) { msg($("s-msg"), e.message); }
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
      if (!(k in lastKv)) return;              // oudere firmware kent deze instelling niet
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
    if (lastKv.transport) await applyVia(bad);
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

  // Secties volgens de rechten. Alleen simulators beheren = enkel virtuele trackers.
  function applyPerms() {
    const can = MT.can;
    const sec = (id) => document.getElementById(id).closest("section");
    const manageAny = can("trackers.manage") || can("sims.manage");
    sec("trackers").hidden = !manageAny;
    $("new").hidden = !manageAny;
    if (!can("trackers.manage")) { $("f-virtual").checked = true; $("f-virtual").disabled = true; setVirtual(true); }
    if (!can("sims.manage")) document.querySelector("#f-virtual").closest("label").hidden = true;
    sec("s-connect").hidden = !can("trackers.serial");
    $("s-use").hidden = !can("trackers.manage");
    sec("qr").hidden = !can("companion.view");
    sec("unknown").hidden = !can("trackers.manage");
  }

  MT.live((m) => {
    if (m.type === "mesh") MT.meshPill($("mesh"), m.mesh);
    if (m.type === "tracker" || m.type === "tracker_deleted" || m.type === "tracker_groups") load();
  });
  MT.initHeader("/admin").then(async () => {
    applyPerms();
    await load();
    const m = /edit=(\d+)/.exec(location.hash);   // vanaf de kaart: meteen bewerken
    if (m) edit(Number(m[1]));
  });
  setInterval(load, 30000);
})();
