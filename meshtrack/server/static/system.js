/* Systeem: meldingsregels, verzonden meldingen, systeeminstellingen. */
(async function () {
  const $ = (id) => document.getElementById(id);
  await MT.initHeader("/system");
  const can = MT.can;
  let rules = [], events = {}, trackers = [], contacts = [], rcpts = [], tgroups = [];
  const tgName = (id) => (tgroups.find((g) => g.id === id) || {}).name || `groep #${id}`;
  let canShared = false, canPersonal = false;
  const msg = (el, t, ok) => { el.textContent = t || ""; el.className = "msg " + (ok ? "ok" : "err"); };
  const fmt = (ts) => (ts ? new Date(ts * 1000).toLocaleString("nl-BE") : "");

  // ---- tabs ------------------------------------------------------------------------
  const need = { rules: ["alerts.manage", "alerts.personal"], sent: ["alerts.manage", "alerts.personal", "system.manage"],
    channels: "system.manage", settings: "system.manage" };
  const allowed = (k) => (Array.isArray(need[k]) ? need[k].some(can) : can(need[k]));
  const tabs = document.querySelectorAll(".pagetabs .tab");
  tabs.forEach((b) => { if (!allowed(b.dataset.tab)) b.hidden = true; b.addEventListener("click", () => show(b.dataset.tab)); });
  function show(name) {
    tabs.forEach((x) => x.classList.toggle("on", x.dataset.tab === name));
    document.querySelectorAll("main .pane").forEach((p) => { p.hidden = p.id !== "pane-" + name; });
    if (name === "sent") loadSent();
    if (name === "settings") loadSettings();
    if (name === "channels") loadChannels();
    history.replaceState(null, "", "#" + name);
  }

  // ---- regels ------------------------------------------------------------------------
  async function loadRules() {
    const r = await MT.api("/api/alerts");
    rules = r.rules; events = r.events;
    canShared = r.can_shared; canPersonal = r.can_personal;
    $("rules").innerHTML = rules.map((x) => `<div class="titem"><div class="body">
      <div><strong>${MT.esc(x.name)}</strong> ${x.active ? "" : '<span class="pill">uit</span>'}${x.mine ? ' <span class="pill ok">eigen</span>' : ""}</div>
      <div class="chipsline">${x.events.map((e) => `<span class="pill">${MT.esc(events[e] || e)}</span>`).join(" ")}</div>
      <div class="muted small">${x.trackers.length || (x.tracker_groups || []).length ? [...(x.tracker_groups || []).map((id) => "groep " + MT.esc(tgName(id))), ...x.trackers.map((id) => MT.esc((trackers.find((t) => t.id === id) || {}).alias || "#" + id))].join(", ") : "alle trackers"}
        → ${x.recipients.map((c) => MT.esc(c.name || c.pubkey.slice(0, 8))).join(", ")}</div></div>
      <div class="actions"><button data-test="${x.id}">Test</button><button data-edit="${x.id}">Bewerken</button>
        <button class="danger" data-del="${x.id}">Verwijderen</button></div></div>`).join("")
      || '<div class="empty">Nog geen meldingsregels.</div>';
    document.querySelectorAll("[data-edit]").forEach((b) => b.addEventListener("click", () => openRule(rules.find((r) => r.id === Number(b.dataset.edit)))));
    document.querySelectorAll("[data-del]").forEach((b) => b.addEventListener("click", async () => {
      if (!confirm("Deze regel verwijderen?")) return;
      await MT.api(`/api/alerts/${b.dataset.del}`, { method: "DELETE" }); loadRules();
    }));
    document.querySelectorAll("[data-test]").forEach((b) => b.addEventListener("click", async () => {
      const r = await MT.api(`/api/alerts/${b.dataset.test}/test`, { method: "POST" });
      b.textContent = `${r.queued} in de wachtrij`;
    }));
  }

  function renderRcpts() {
    $("r-rcpts").innerHTML = rcpts.map((c, i) => `<span class="pill rcpt">${MT.esc(c.name || c.pubkey.slice(0, 8))}
      <button type="button" class="x" data-rm="${i}" aria-label="Verwijder">✕</button></span>`).join(" ") || '<span class="muted">Nog geen ontvangers.</span>';
    $("r-rcpts").querySelectorAll("[data-rm]").forEach((b) => b.addEventListener("click", () => { rcpts.splice(Number(b.dataset.rm), 1); renderRcpts(); }));
  }

  function openRule(r) {
    $("r-form").hidden = false;
    $("r-id").value = r ? r.id : "";
    $("r-name").value = r ? r.name : "";
    $("r-cool").value = r ? String(r.cooldown_s) : "900";
    if (![...$("r-cool").options].some((o) => o.value === $("r-cool").value)) $("r-cool").value = "900";
    $("r-events").innerHTML = Object.entries(events).map(([k, v]) => `<label class="mini"><input type="checkbox" value="${k}"
      ${r && r.events.includes(k) ? "checked" : ""}> ${MT.esc(v)}</label>`).join("");
    $("r-alltr").checked = !r || (!r.trackers.length && !(r.tracker_groups || []).length);
    const tsel = r ? r.tracker_groups || [] : [];
    $("r-tgroups").innerHTML = tgroups.map((g) => `<label class="mini"><input type="checkbox" value="${g.id}"${tsel.includes(g.id) ? " checked" : ""}>
      <i style="background:${MT.esc(g.color)}"></i>${MT.esc(g.name)} <span class="muted small">(${g.trackers.length})</span></label>`).join("")
      || '<span class="muted">Nog geen trackergroepen.</span>';
    $("r-trackers").innerHTML = trackers.map((t) => `<label class="mini"><input type="checkbox" value="${t.id}"
      ${r && r.trackers.includes(t.id) ? "checked" : ""}><i style="background:${MT.esc(t.color)}"></i>${MT.esc(t.alias)}</label>`).join("");
    $("r-trwrap").hidden = $("r-alltr").checked;
    rcpts = r ? r.recipients.map((x) => ({ ...x })) : [];
    renderRcpts();
    $("r-active").checked = r ? r.active : true;
    $("r-personal").checked = r ? !!r.mine : !canShared;
    $("r-personal").disabled = !!r || !(canShared && canPersonal);
    $("r-personal-wrap").hidden = !(canShared && canPersonal) && !r;
    msg($("r-msg"), "");
    $("r-form").scrollIntoView({ behavior: "smooth", block: "nearest" });
  }
  $("r-alltr").addEventListener("change", () => { $("r-trwrap").hidden = $("r-alltr").checked; });
  $("r-new").addEventListener("click", () => openRule(null));
  $("r-cancel").addEventListener("click", () => { $("r-form").hidden = true; });
  $("r-add").addEventListener("click", () => {
    const pk = $("r-contact").value;
    if (!pk || rcpts.some((c) => c.pubkey === pk)) return;
    rcpts.push({ pubkey: pk, name: $("r-contact").selectedOptions[0].dataset.name || "" });
    renderRcpts();
  });
  $("r-addpk").addEventListener("click", () => {
    const pk = $("r-pk").value.trim().toLowerCase();
    if (!/^[0-9a-f]{64}$/.test(pk)) { msg($("r-msg"), "Een pubkey is 64 hex-tekens."); return; }
    if (!rcpts.some((c) => c.pubkey === pk)) rcpts.push({ pubkey: pk, name: $("r-pkname").value.trim() });
    $("r-pk").value = ""; $("r-pkname").value = "";
    renderRcpts();
  });
  $("r-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const id = $("r-id").value;
    const body = { name: $("r-name").value.trim(), active: $("r-active").checked, cooldown_s: Number($("r-cool").value),
      events: [...$("r-events").querySelectorAll("input:checked")].map((c) => c.value),
      trackers: $("r-alltr").checked ? [] : [...$("r-trackers").querySelectorAll("input:checked")].map((c) => Number(c.value)),
      tracker_groups: $("r-alltr").checked ? [] : [...$("r-tgroups").querySelectorAll("input:checked")].map((c) => Number(c.value)),
      recipients: rcpts, personal: $("r-personal").checked };
    try {
      await MT.api(id ? `/api/alerts/${id}` : "/api/alerts", { method: id ? "PUT" : "POST", body });
      $("r-form").hidden = true;
      loadRules();
    } catch (err) { msg($("r-msg"), err.message); }
  });

  // ---- verzonden ---------------------------------------------------------------------
  async function loadSent() {
    const r = await MT.api("/api/alerts/log?limit=300");
    $("q").textContent = r.queue.queued ? `${r.queue.queued} in de wachtrij${r.queue.sending ? ", nu naar " + r.queue.sending : ""}` : "wachtrij leeg";
    const cls = { ok: "ok", mislukt: "bad", wacht: "" };
    $("sent").innerHTML = r.log.length ? `<table><thead><tr><th>Tijd</th><th>Regel</th><th>Tracker</th><th>Naar</th><th>Status</th>
      <th class="hide-sm">Bericht</th></tr></thead><tbody>` + r.log.map((l) => `<tr><td class="small nowrap">${fmt(l.ts)}</td>
      <td>${MT.esc(l.rule)}</td><td>${MT.esc(l.tracker)}</td><td>${MT.esc(l.recipient)}</td>
      <td><span class="pill ${cls[l.status] || ""}">${MT.esc(l.status)}</span></td>
      <td class="hide-sm muted small">${MT.esc(l.text)}</td></tr>`).join("") + "</tbody></table>"
      : '<div class="empty">Nog niets verzonden.</div>';
  }

  // ---- kanalen -----------------------------------------------------------------------
  let chans = [], compSlots = [];
  async function loadChannels() {
    const r = await MT.api("/api/channels");
    chans = r.channels; compSlots = r.companion;
    $("channels").innerHTML = chans.map((c) => `<div class="titem"><div class="body">
        <div><strong>${MT.esc(c.name)}</strong> <span class="muted small">nummer ${c.slot}</span>
          ${c.active ? "" : '<span class="pill">uit</span>'}
          ${!r.connected ? '<span class="pill">companion niet verbonden</span>' : c.on_companion ? '<span class="pill ok">op de companion</span>' : '<span class="pill warn">nog niet op de companion</span>'}
          ${c.require_sig ? '<span class="pill">ondertekend</span>' : '<span class="pill warn">ook niet-ondertekend</span>'}</div>
        <div class="muted small">regio ${MT.esc(c.region || "geen")} · ${c.members} tracker(s) sturen via dit kanaal ·
          <a href="/?tgroup=${c.tracker_group_id}">kaart van dit kanaal</a></div></div>
      <div class="actions"><button data-cqr="${c.id}">QR-code</button><button data-cedit="${c.id}">Bewerken</button><button class="danger" data-cdel="${c.id}">Verwijderen</button></div></div>`).join("")
      || '<div class="empty">Nog geen kanalen. De trackers sturen via DM.</div>';
    document.querySelectorAll("[data-cedit]").forEach((b) => b.addEventListener("click", () => openChannel(chans.find((c) => c.id === Number(b.dataset.cedit)))));
    document.querySelectorAll("[data-cqr]").forEach((b) => b.addEventListener("click", () => {
      const c = chans.find((x) => x.id === Number(b.dataset.cqr));
      const url = `meshcore://channel/add?name=${encodeURIComponent(c.name)}&secret=${c.secret}`;
      const qr = qrcode(0, "M");
      qr.addData(url);
      qr.make();
      $("c-qr").innerHTML = `<div class="cardhead"><h3 style="margin:0">QR-code: ${MT.esc(c.name)}</h3>
          <button type="button" class="ghost" id="c-qrclose" aria-label="Sluiten">✕</button></div>
        <div class="row" style="align-items:flex-start"><div class="qrbox">${qr.createSvgTag({ cellSize: 5, margin: 2 })}</div>
          <div style="flex:1;min-width:220px"><p>Scan met de MeshCore-app om dit kanaal toe te voegen.</p>
            <div class="small muted">Sleutel</div><code class="mono">${MT.esc(c.secret.match(/.{1,4}/g).join(" "))}</code>
            <p class="help">Wie deze code of sleutel heeft, kan het kanaal lezen en erop sturen. Deel hem alleen met wie mag.</p></div></div>`;
      $("c-qr").hidden = false;
      $("c-qrclose").addEventListener("click", () => { $("c-qr").hidden = true; });
      $("c-qr").scrollIntoView({ behavior: "smooth", block: "nearest" });
    }));
    document.querySelectorAll("[data-cdel]").forEach((b) => b.addEventListener("click", async () => {
      const c = chans.find((x) => x.id === Number(b.dataset.cdel));
      if (!confirm(`Kanaal "${c.name}" verwijderen? De server luistert dan niet meer mee; trackers die erop sturen, worden niet meer ontvangen.`)) return;
      await MT.api(`/api/channels/${c.id}`, { method: "DELETE" }); loadChannels();
    }));
  }
  function openChannel(c) {
    $("c-form").hidden = false;
    $("c-id").value = c ? c.id : "";
    $("c-name").value = c ? c.name : "";
    $("c-secret").value = c && !c.name.startsWith("#") ? c.secret : "";
    const used = new Set([...compSlots.map((s) => s.slot), ...chans.map((x) => x.slot)]);
    let free = 8; while (used.has(free) && free < 39) free++;
    $("c-slot").value = c ? c.slot : free;
    $("c-slots").textContent = compSlots.length ? "Nu op de companion: " + compSlots.map((s) => `${s.slot} = ${s.name}`).join(", ") : "";
    $("c-sig").checked = c ? c.require_sig : true;
    $("c-region").value = c ? (c.region || "") : "be";
    $("c-active").checked = c ? c.active : true;
    msg($("c-msg"), "");
  }
  $("c-new").addEventListener("click", () => openChannel(null));
  $("c-cancel").addEventListener("click", () => { $("c-form").hidden = true; });
  $("c-gen").addEventListener("click", () => {
    $("c-secret").value = [...crypto.getRandomValues(new Uint8Array(16))].map((b) => b.toString(16).padStart(2, "0")).join("");
  });
  $("c-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const id = $("c-id").value, slot = Number($("c-slot").value);
    const other = compSlots.find((s) => s.slot === slot && !chans.some((c) => c.slot === slot && String(c.id) === id));
    if (other && !confirm(`Op nummer ${slot} van de companion staat nu "${other.name}". Overschrijven?`)) return;
    const body = { name: $("c-name").value.trim(), secret: $("c-secret").value.trim(), slot,
      require_sig: $("c-sig").checked, active: $("c-active").checked, region: $("c-region").value.trim() };
    try {
      const r = await MT.api(id ? `/api/channels/${id}` : "/api/channels", { method: id ? "PUT" : "POST", body });
      $("c-form").hidden = true;
      await loadChannels();
      if (r.issues && r.issues.length) alert(r.issues.join("\n"));
    } catch (err) { msg($("c-msg"), err.message); }
  });

  // ---- instellingen ------------------------------------------------------------------
  async function loadSettings() {
    const r = await MT.api("/api/settings");
    $("settings").innerHTML = r.spec.map((s) => s.type === "bool"
      ? `<div><label class="switch block"><input type="checkbox" data-key="${s.key}"${r.values[s.key] ? " checked" : ""}><span></span> ${MT.esc(s.label)}</label>
         <div class="help">${MT.esc(s.help)}</div></div>`
      : s.type === "text"
      ? `<div><label>${MT.esc(s.label)}</label><input type="text" data-key="${s.key}" maxlength="${s.max}" value="${MT.esc(r.values[s.key])}">
         <div class="help">${MT.esc(s.help)}</div></div>`
      : `<div><label>${MT.esc(s.label)}</label><input type="number" data-key="${s.key}" min="${s.min}" max="${s.max}" step="${s.step || 1}" value="${r.values[s.key]}">
         <div class="help">${MT.esc(s.help)} (standaard ${s.default})</div></div>`).join("");
  }
  $("set-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const body = {};
    $("settings").querySelectorAll("[data-key]").forEach((el) => { body[el.dataset.key] = el.type === "checkbox" ? el.checked : el.type === "text" ? el.value.trim() : Number(el.value); });
    try { await MT.api("/api/settings", { method: "PUT", body }); msg($("set-msg"), "Bewaard.", true); }
    catch (err) { msg($("set-msg"), err.message); }
  });

  // ---- start ---------------------------------------------------------------------------
  trackers = await MT.api("/api/trackers");
  tgroups = await MT.api("/api/tracker-groups").catch(() => []);
  if (can("alerts.manage") || can("alerts.personal")) {
    try {
      contacts = (await MT.api("/api/companion/contacts")).filter((c) => c.type === 1).sort((a, b) => (a.name || "").localeCompare(b.name || ""));
    } catch (_) { contacts = []; }
    $("r-contact").insertAdjacentHTML("beforeend", contacts.map((c) =>
      `<option value="${MT.esc(c.public_key)}" data-name="${MT.esc(c.name || "")}">${MT.esc(c.name || "?")} (${MT.esc(c.public_key.slice(0, 8))})</option>`).join(""));
    await loadRules();
  }
  const first = location.hash.slice(1);
  show(first && allowed(first) ? first : (allowed("rules") ? "rules" : "settings"));
  setInterval(() => { if (!$("pane-sent").hidden) loadSent(); }, 10000);
})();
