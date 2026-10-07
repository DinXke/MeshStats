/* Gebruikers, groepen, deellinks en auditlog. */
(async function () {
  const $ = (id) => document.getElementById(id);
  await MT.initHeader("/users");
  const can = MT.can;
  let groups = [], perms = [], trackers = [], users = [], tgroups = [];

  function msg(el, text, ok) { el.textContent = text || ""; el.className = "msg " + (ok ? "ok" : "err"); }
  const fmtTs = (ts) => (ts ? new Date(ts * 1000).toLocaleString("nl-BE") : "nooit");

  // ---- tabs ------------------------------------------------------------------------
  const tabPerm = { users: "users.manage", groups: "users.manage", shares: "share.manage", audit: "users.manage" };
  function onShow(name) {
    if (name === "audit") loadAudit();
    if (name === "shares") loadShares();
  }
  MT.dialogize($("u-form"), "Gebruiker");
  MT.dialogize($("g-form"), "Groep");
  MT.dialogize($("s-form"), "Deellink");

  // ---- gegevens --------------------------------------------------------------------
  async function loadBase() {
    const g = await MT.api("/api/groups");
    groups = g.groups; perms = g.perms;
    trackers = await MT.api("/api/trackers");
    tgroups = await MT.api("/api/tracker-groups");
    if (can("users.manage")) users = await MT.api("/api/users");
  }

  // ---- gebruikers -------------------------------------------------------------------
  function renderUsers() {
    $("users").innerHTML = users.map((u) => `<div class="titem">
      <span class="avatar">${MT.esc((u.display_name || u.username).slice(0, 1).toUpperCase())}</span>
      <div class="body"><div><strong>${MT.esc(u.display_name || u.username)}</strong> <span class="muted">@${MT.esc(u.username)}</span>
        ${u.active ? "" : ' <span class="pill bad">gedeactiveerd</span>'}</div>
        <div class="muted small">${MT.esc(u.group_name)} · laatst ingelogd ${MT.esc(fmtTs(u.last_login))}</div></div>
      <div class="actions"><button data-ueff="${u.id}">Effectieve rechten</button><button data-uedit="${u.id}">Bewerken</button><button class="danger" data-udel="${u.id}">Verwijderen</button></div></div>`).join("")
      || '<div class="empty">Nog geen gebruikers.</div>';
    document.querySelectorAll("[data-uedit]").forEach((b) => b.addEventListener("click", () => editUser(Number(b.dataset.uedit))));
    document.querySelectorAll("[data-ueff]").forEach((b) => b.addEventListener("click", () => showEffective(Number(b.dataset.ueff))));
    document.querySelectorAll("[data-udel]").forEach((b) => b.addEventListener("click", () => delUser(Number(b.dataset.udel))));
  }
  function openUser(u) {
    $("u-form").hidden = false;
    $("u-id").value = u ? u.id : "";
    $("u-name").value = u ? u.username : "";
    $("u-name").disabled = !!u;
    $("u-display").value = u ? u.display_name : "";
    const sel = u ? u.group_ids : [(groups.find((g) => g.name === "Kijkers") || groups[0]).id];
    $("u-groups").innerHTML = groups.map((g) => `<label class="mini"><input type="checkbox" value="${g.id}"${sel.includes(g.id) ? " checked" : ""}>
      ${MT.esc(g.name)}<span class="muted small"> ${MT.esc(g.description || "")}</span></label>`).join("");
    $("u-pw").value = "";
    $("u-pw").required = !u;
    $("u-pwhint").textContent = u ? "(leeg = ongewijzigd)" : "(min. 8 tekens)";
    $("u-active").checked = u ? !!u.active : true;
    msg($("u-msg"), "");
    $("u-form").scrollIntoView({ behavior: "smooth", block: "nearest" });
  }
  function editUser(id) { openUser(users.find((u) => u.id === id)); }

  // Wat mag deze gebruiker echt, en waarom?
  async function showEffective(id) {
    const e = await MT.api(`/api/users/${id}/effective`);
    const has = e.perms.filter((p) => p.via.length), not = e.perms.filter((p) => !p.via.length);
    const seen = e.trackers.filter((t) => t.sees), hidden = e.trackers.filter((t) => !t.sees);
    const hist = e.history_hours == null ? "–" : e.history_hours === 0 ? "onbeperkt" : `${e.history_hours} uur`;
    $("u-eff").innerHTML = `<div class="cardhead"><h3 style="margin:0">Effectieve rechten: ${MT.esc(e.user.display_name || e.user.username)}</h3>
        <button type="button" class="ghost" id="u-effclose" aria-label="Sluiten">✕</button></div>
      ${e.warnings.map((w) => `<div class="msg err">${MT.esc(w)}</div>`).join("")}
      <div class="muted small">Groepen: <strong>${MT.esc(e.groups.join(", ") || "geen")}</strong> · terugblik ${MT.esc(hist)}${e.history_via.length ? " (via " + MT.esc(e.history_via.join(", ")) + ")" : ""}
        ${e.own_rules.length ? " · eigen meldingsregels: " + MT.esc(e.own_rules.join(", ")) : ""}</div>
      <h4>Rechten (${has.length} van ${e.perms.length})</h4>
      <div class="permgrid">${has.map((p) => `<div class="small"><span class="ok">✓</span> <strong>${MT.esc(p.label)}</strong>
        <span class="muted">via ${MT.esc(p.via.join(", "))}</span></div>`).join("") || '<span class="muted">geen</span>'}</div>
      ${not.length ? `<details><summary class="small muted">Niet toegestaan (${not.length})</summary><div class="permgrid">${not.map((p) =>
        `<div class="small muted">✗ ${MT.esc(p.label)}</div>`).join("")}</div></details>` : ""}
      <h4>Trackers (${e.all_trackers ? "alle, ook toekomstige" : seen.length + " van " + e.trackers.length})</h4>
      <div class="permgrid">${seen.map((t) => `<div class="small"><i class="dot" style="background:${MT.esc(t.color)}"></i>
        <strong>${MT.esc(t.alias)}</strong>${t.kind === "sim" ? " (sim)" : ""}${t.active ? "" : ' <span class="pill">inactief</span>'}
        <div class="muted">${MT.esc(t.via.join(" · "))}</div></div>`).join("") || '<span class="muted">geen enkele tracker</span>'}</div>
      ${hidden.length ? `<details><summary class="small muted">Niet zichtbaar (${hidden.length})</summary><div class="permgrid">${hidden.map((t) =>
        `<div class="small muted">✗ ${MT.esc(t.alias)}</div>`).join("")}</div></details>` : ""}`;
    $("u-eff").hidden = false;
    $("u-effclose").addEventListener("click", () => { $("u-eff").hidden = true; });
    $("u-eff").scrollIntoView({ behavior: "smooth", block: "nearest" });
  }
  async function delUser(id) {
    const u = users.find((x) => x.id === id);
    if (!(await MT.confirm(`Gebruiker "${u.username}" verwijderen?`, { ok: "Verwijderen", danger: true }))) return;
    try { await MT.api(`/api/users/${id}`, { method: "DELETE" }); users = await MT.api("/api/users"); renderUsers(); }
    catch (e) { alert(e.message); }
  }
  $("u-new").addEventListener("click", () => openUser(null));
  $("u-cancel").addEventListener("click", () => { $("u-form").hidden = true; });
  $("u-gen").addEventListener("click", () => {
    const a = "abcdefghjkmnpqrstuvwxyzABCDEFGHJKMNPQRSTUVWXYZ23456789";
    const r = crypto.getRandomValues(new Uint32Array(14));
    $("u-pw").value = [...r].map((x) => a[x % a.length]).join("");
  });
  $("u-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const id = $("u-id").value;
    const gids = [...$("u-groups").querySelectorAll("input:checked")].map((c) => Number(c.value));
    if (!gids.length) { msg($("u-msg"), "Kies minstens één groep."); return; }
    const body = { display_name: $("u-display").value, group_ids: gids, active: $("u-active").checked };
    if ($("u-pw").value) body.password = $("u-pw").value;
    try {
      if (id) await MT.api(`/api/users/${id}`, { method: "PUT", body });
      else await MT.api("/api/users", { method: "POST", body: { ...body, username: $("u-name").value.trim() } });
      $("u-form").hidden = true;
      users = await MT.api("/api/users");
      renderUsers();
    } catch (err) { msg($("u-msg"), err.message); }
  });

  // ---- groepen ----------------------------------------------------------------------
  function renderGroups() {
    $("groups").innerHTML = groups.map((g) => `<div class="titem">
      <div class="body"><div><strong>${MT.esc(g.name)}</strong> <span class="muted small">${g.members} ${g.members === 1 ? "lid" : "leden"}</span></div>
        <div class="muted small">${MT.esc(g.description || "")}</div>
        <div class="chipsline">${g.perms.map((p) => `<span class="pill">${MT.esc((perms.find((x) => x.id === p) || {}).label || p)}</span>`).join(" ")}</div>
        <div class="muted small">${g.all_trackers ? "alle trackers" : [g.tracker_groups.length ? "trackergroepen: " + g.tracker_groups.map((id) => (tgroups.find((x) => x.id === id) || {}).name || "?").join(", ") : null, g.trackers.length ? g.trackers.length + " losse tracker(s)" : null].filter(Boolean).join(" · ") || "geen trackers"} · terugblik ${g.history_hours ? g.history_hours + " u" : "onbeperkt"}</div></div>
      <div class="actions"><button data-gedit="${g.id}">Bewerken</button>${g.members ? "" : `<button class="danger" data-gdel="${g.id}">Verwijderen</button>`}</div></div>`).join("");
    document.querySelectorAll("[data-gedit]").forEach((b) => b.addEventListener("click", () => openGroup(groups.find((g) => g.id === Number(b.dataset.gedit)))));
    document.querySelectorAll("[data-gdel]").forEach((b) => b.addEventListener("click", async () => {
      const g = groups.find((x) => x.id === Number(b.dataset.gdel));
      if (!(await MT.confirm(`Groep "${g.name}" verwijderen?`, { ok: "Verwijderen", danger: true }))) return;
      try { await MT.api(`/api/groups/${g.id}`, { method: "DELETE" }); await loadBase(); renderGroups(); renderUsers(); } catch (e) { alert(e.message); }
    }));
  }
  function trackerChecks(el, selected) {
    el.innerHTML = trackers.map((t) => `<label class="mini"><input type="checkbox" value="${t.id}"${selected.includes(t.id) ? " checked" : ""}>
      <i style="background:${MT.esc(t.color)}"></i>${MT.esc(t.alias)}${t.kind === "sim" ? " (sim)" : ""}</label>`).join("")
      || '<span class="muted">Nog geen trackers.</span>';
  }
  function openGroup(g) {
    $("g-form").hidden = false;
    $("g-id").value = g ? g.id : "";
    $("g-name").value = g ? g.name : "";
    $("g-desc").value = g ? g.description : "";
    $("g-hist").value = g ? g.history_hours : 0;
    $("g-perms").innerHTML = perms.map((p) => `<label class="mini perm" title="${MT.esc(p.help)}"><input type="checkbox" value="${p.id}"
      ${g && g.perms.includes(p.id) ? "checked" : ""}> <span><strong>${MT.esc(p.label)}</strong><span class="muted small"> ${MT.esc(p.help)}</span></span></label>`).join("");
    $("g-alltr").checked = g ? g.all_trackers : true;
    trackerChecks($("g-trackers"), g ? g.trackers : []);
    const tsel = g ? g.tracker_groups : [];
    $("g-tgroups").innerHTML = tgroups.map((t) => `<label class="mini"><input type="checkbox" value="${t.id}"${tsel.includes(t.id) ? " checked" : ""}>
      <i style="background:${MT.esc(t.color)}"></i>${MT.esc(t.name)} <span class="muted small">(${(t.members || t.trackers).length})</span></label>`).join("")
      || '<span class="muted">Nog geen trackergroepen. Maak ze in Beheer.</span>';
    $("g-trwrap").hidden = $("g-alltr").checked;
    msg($("g-msg"), "");
    $("g-form").scrollIntoView({ behavior: "smooth", block: "nearest" });
  }
  const setPerms = (ids) => $("g-perms").querySelectorAll("input").forEach((cb) => { cb.checked = ids.includes(cb.value); });
  $("g-all").addEventListener("click", () => setPerms(perms.map((p) => p.id)));
  $("g-none").addEventListener("click", () => setPerms([]));
  $("g-ro").addEventListener("click", () => setPerms(["map.view", "map.sidebar", "map.tracks", "map.nodes", "zones.view", "log.view"]));
  $("g-kiosk").addEventListener("click", () => setPerms(["map.view", "map.tracks"]));
  $("g-alltr").addEventListener("change", () => { $("g-trwrap").hidden = $("g-alltr").checked; });
  $("g-new").addEventListener("click", () => openGroup(null));
  $("g-cancel").addEventListener("click", () => { $("g-form").hidden = true; });
  $("g-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const id = $("g-id").value;
    const body = { name: $("g-name").value.trim(), description: $("g-desc").value, history_hours: Number($("g-hist").value) || 0,
      perms: [...$("g-perms").querySelectorAll("input:checked")].map((c) => c.value),
      all_trackers: $("g-alltr").checked, trackers: [...$("g-trackers").querySelectorAll("input:checked")].map((c) => Number(c.value)),
      tracker_groups: [...$("g-tgroups").querySelectorAll("input:checked")].map((c) => Number(c.value)) };
    try {
      await MT.api(id ? `/api/groups/${id}` : "/api/groups", { method: id ? "PUT" : "POST", body });
      $("g-form").hidden = true;
      await loadBase(); renderGroups(); renderUsers();
    } catch (err) { msg($("g-msg"), err.message); }
  });

  // ---- deellinks --------------------------------------------------------------------
  async function loadShares() {
    if (!can("share.manage")) return;
    const shares = await MT.api("/api/shares");
    const now = Date.now() / 1000;
    $("shares").innerHTML = shares.map((s) => {
      const expired = s.expires && s.expires < now;
      const names = [...(s.tracker_groups || []).map((id) => "groep " + ((tgroups.find((g) => g.id === id) || {}).name || `#${id}`)),
        ...s.trackers.map((id) => (trackers.find((t) => t.id === id) || {}).alias || `#${id}`)].join(", ");
      return `<div class="titem"><div class="body">
        <div><strong>${MT.esc(s.name)}</strong> ${expired ? '<span class="pill bad">verlopen</span>' : ""}</div>
        <div class="muted small">${MT.esc(names)} · spoor ${s.hours} u · ${s.expires ? "geldig tot " + fmtTs(s.expires) : "onbeperkt geldig"}
          · door ${MT.esc(s.created_by)} · laatst geopend ${MT.esc(fmtTs(s.last_used))}</div>
        <div class="mono small sharelink">${MT.esc(s.url)}</div></div>
        <div class="actions"><button data-copy="${MT.esc(s.url)}">Kopiëren</button><button class="danger" data-sdel="${s.id}">Intrekken</button></div></div>`;
    }).join("") || '<div class="empty">Nog geen deellinks.</div>';
    document.querySelectorAll("[data-copy]").forEach((b) => b.addEventListener("click", () => { navigator.clipboard.writeText(b.dataset.copy); b.textContent = "Gekopieerd"; }));
    document.querySelectorAll("[data-sdel]").forEach((b) => b.addEventListener("click", async () => {
      if (!(await MT.confirm("Deze link intrekken? Wie hem heeft, ziet daarna niets meer.", { ok: "Intrekken", danger: true }))) return;
      await MT.api(`/api/shares/${b.dataset.sdel}`, { method: "DELETE" }); loadShares();
    }));
  }
  $("s-new").addEventListener("click", () => {
    $("s-form").hidden = false;
    trackerChecks($("s-trackers"), []);
    $("s-tgroups").innerHTML = tgroups.map((g) => `<label class="mini"><input type="checkbox" value="${g.id}">
      <i style="background:${MT.esc(g.color)}"></i>${MT.esc(g.name)} <span class="muted small">(${g.trackers.length})</span></label>`).join("")
      || '<span class="muted">Nog geen trackergroepen.</span>';
    msg($("s-msg"), "");
  });
  $("s-cancel").addEventListener("click", () => { $("s-form").hidden = true; });
  $("s-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    try {
      const r = await MT.api("/api/shares", { method: "POST", body: { name: $("s-name").value.trim(),
        trackers: [...$("s-trackers").querySelectorAll("input:checked")].map((c) => Number(c.value)),
        tracker_groups: [...$("s-tgroups").querySelectorAll("input:checked")].map((c) => Number(c.value)),
        hours: Number($("s-hours").value) || 12, sidebar: $("s-sidebar").checked, valid_hours: Number($("s-valid").value) } });
      $("s-form").hidden = true;
      navigator.clipboard && navigator.clipboard.writeText(r.url).catch(() => {});
      loadShares();
    } catch (err) { msg($("s-msg"), err.message); }
  });

  // ---- audit ------------------------------------------------------------------------
  let auditRows = [];
  async function loadAudit() {
    auditRows = await MT.api("/api/audit?limit=500");
    renderAudit();
  }
  function renderAudit() {
    const q = $("a-filter").value.trim().toLowerCase();
    const rows = auditRows.filter((a) => !q || `${a.who} ${a.action} ${a.detail}`.toLowerCase().includes(q));
    $("audit").innerHTML = rows.length ? `<table><thead><tr><th>Tijd</th><th>Wie</th><th>Actie</th><th class="hide-sm">Details</th></tr></thead><tbody>` +
      rows.map((a) => `<tr><td class="small">${MT.esc(fmtTs(a.ts))}</td><td>${MT.esc(a.who)}</td><td>${MT.esc(a.action)}</td>
        <td class="hide-sm muted small">${MT.esc(a.detail)}</td></tr>`).join("") + "</tbody></table>" : '<div class="empty">Niets gevonden.</div>';
  }
  $("a-filter").addEventListener("input", renderAudit);

  // ---- start --------------------------------------------------------------------------
  await loadBase();
  if (can("users.manage")) { renderUsers(); renderGroups(); }
  MT.tabs(document.querySelector(".pagetabs"), onShow, (n) => can(tabPerm[n]));
})();
