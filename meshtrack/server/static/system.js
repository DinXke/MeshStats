/* Systeem: meldingsregels, verzonden meldingen, systeeminstellingen. */
(async function () {
  const $ = (id) => document.getElementById(id);
  await MT.initHeader("/system");
  const can = MT.can;
  let rules = [], events = {}, trackers = [], contacts = [], rcpts = [];
  let canShared = false, canPersonal = false;
  const msg = (el, t, ok) => { el.textContent = t || ""; el.className = "msg " + (ok ? "ok" : "err"); };
  const fmt = (ts) => (ts ? new Date(ts * 1000).toLocaleString("nl-BE") : "");

  // ---- tabs ------------------------------------------------------------------------
  const need = { rules: ["alerts.manage", "alerts.personal"], sent: ["alerts.manage", "alerts.personal", "system.manage"], settings: "system.manage" };
  const allowed = (k) => (Array.isArray(need[k]) ? need[k].some(can) : can(need[k]));
  const tabs = document.querySelectorAll(".pagetabs .tab");
  tabs.forEach((b) => { if (!allowed(b.dataset.tab)) b.hidden = true; b.addEventListener("click", () => show(b.dataset.tab)); });
  function show(name) {
    tabs.forEach((x) => x.classList.toggle("on", x.dataset.tab === name));
    document.querySelectorAll("main .pane").forEach((p) => { p.hidden = p.id !== "pane-" + name; });
    if (name === "sent") loadSent();
    if (name === "settings") loadSettings();
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
      <div class="muted small">${x.trackers.length ? x.trackers.map((id) => MT.esc((trackers.find((t) => t.id === id) || {}).alias || "#" + id)).join(", ") : "alle trackers"}
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
    $("r-alltr").checked = !r || !r.trackers.length;
    $("r-trackers").innerHTML = trackers.map((t) => `<label class="mini"><input type="checkbox" value="${t.id}"
      ${r && r.trackers.includes(t.id) ? "checked" : ""}><i style="background:${MT.esc(t.color)}"></i>${MT.esc(t.alias)}</label>`).join("");
    $("r-trackers").hidden = $("r-alltr").checked;
    rcpts = r ? r.recipients.map((x) => ({ ...x })) : [];
    renderRcpts();
    $("r-active").checked = r ? r.active : true;
    $("r-personal").checked = r ? !!r.mine : !canShared;
    $("r-personal").disabled = !!r || !(canShared && canPersonal);
    $("r-personal-wrap").hidden = !(canShared && canPersonal) && !r;
    msg($("r-msg"), "");
    $("r-form").scrollIntoView({ behavior: "smooth", block: "nearest" });
  }
  $("r-alltr").addEventListener("change", () => { $("r-trackers").hidden = $("r-alltr").checked; });
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
