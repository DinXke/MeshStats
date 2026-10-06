/* Gebruikers, groepen, deellinks en auditlog. */
(async function () {
  const $ = (id) => document.getElementById(id);
  await MT.initHeader("/users");
  const can = MT.can;
  let groups = [], perms = [], trackers = [], users = [];

  function msg(el, text, ok) { el.textContent = text || ""; el.className = "msg " + (ok ? "ok" : "err"); }
  const fmtTs = (ts) => (ts ? new Date(ts * 1000).toLocaleString("nl-BE") : "nooit");

  // ---- tabs ------------------------------------------------------------------------
  const tabs = document.querySelectorAll(".pagetabs .tab");
  const tabPerm = { users: "users.manage", groups: "users.manage", shares: "share.manage", audit: "users.manage" };
  tabs.forEach((b) => {
    if (!can(tabPerm[b.dataset.tab])) b.hidden = true;
    b.addEventListener("click", () => show(b.dataset.tab));
  });
  function show(name) {
    tabs.forEach((x) => x.classList.toggle("on", x.dataset.tab === name));
    document.querySelectorAll("main .pane").forEach((p) => { p.hidden = p.id !== "pane-" + name; });
    if (name === "audit") loadAudit();
    if (name === "shares") loadShares();
    history.replaceState(null, "", "#" + name);
  }

  // ---- gegevens --------------------------------------------------------------------
  async function loadBase() {
    const g = await MT.api("/api/groups");
    groups = g.groups; perms = g.perms;
    trackers = await MT.api("/api/trackers");
    if (can("users.manage")) users = await MT.api("/api/users");
  }

  // ---- gebruikers -------------------------------------------------------------------
  function renderUsers() {
    $("u-group").innerHTML = groups.map((g) => `<option value="${g.id}">${MT.esc(g.name)}</option>`).join("");
    $("users").innerHTML = users.map((u) => `<div class="titem">
      <span class="avatar">${MT.esc((u.display_name || u.username).slice(0, 1).toUpperCase())}</span>
      <div class="body"><div><strong>${MT.esc(u.display_name || u.username)}</strong> <span class="muted">@${MT.esc(u.username)}</span>
        ${u.active ? "" : ' <span class="pill bad">gedeactiveerd</span>'}</div>
        <div class="muted small">${MT.esc(u.group_name)} · laatst ingelogd ${MT.esc(fmtTs(u.last_login))}</div></div>
      <div class="actions"><button data-uedit="${u.id}">Bewerken</button><button class="danger" data-udel="${u.id}">Verwijderen</button></div></div>`).join("")
      || '<div class="empty">Nog geen gebruikers.</div>';
    document.querySelectorAll("[data-uedit]").forEach((b) => b.addEventListener("click", () => editUser(Number(b.dataset.uedit))));
    document.querySelectorAll("[data-udel]").forEach((b) => b.addEventListener("click", () => delUser(Number(b.dataset.udel))));
  }
  function openUser(u) {
    $("u-form").hidden = false;
    $("u-id").value = u ? u.id : "";
    $("u-name").value = u ? u.username : "";
    $("u-name").disabled = !!u;
    $("u-display").value = u ? u.display_name : "";
    $("u-group").value = u ? u.group_id : (groups.find((g) => g.name === "Kijkers") || groups[0]).id;
    $("u-pw").value = "";
    $("u-pw").required = !u;
    $("u-pwhint").textContent = u ? "(leeg = ongewijzigd)" : "(min. 8 tekens)";
    $("u-active").checked = u ? !!u.active : true;
    msg($("u-msg"), "");
    $("u-form").scrollIntoView({ behavior: "smooth", block: "nearest" });
  }
  function editUser(id) { openUser(users.find((u) => u.id === id)); }
  async function delUser(id) {
    const u = users.find((x) => x.id === id);
    if (!confirm(`Gebruiker "${u.username}" verwijderen?`)) return;
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
    const body = { display_name: $("u-display").value, group_id: Number($("u-group").value), active: $("u-active").checked };
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
        <div class="muted small">${g.all_trackers ? "alle trackers" : g.trackers.length + " tracker(s)"} · terugblik ${g.history_hours ? g.history_hours + " u" : "onbeperkt"}</div></div>
      <div class="actions"><button data-gedit="${g.id}">Bewerken</button>${g.members ? "" : `<button class="danger" data-gdel="${g.id}">Verwijderen</button>`}</div></div>`).join("");
    document.querySelectorAll("[data-gedit]").forEach((b) => b.addEventListener("click", () => openGroup(groups.find((g) => g.id === Number(b.dataset.gedit)))));
    document.querySelectorAll("[data-gdel]").forEach((b) => b.addEventListener("click", async () => {
      const g = groups.find((x) => x.id === Number(b.dataset.gdel));
      if (!confirm(`Groep "${g.name}" verwijderen?`)) return;
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
    $("g-trackers").hidden = $("g-alltr").checked;
    msg($("g-msg"), "");
    $("g-form").scrollIntoView({ behavior: "smooth", block: "nearest" });
  }
  const setPerms = (ids) => $("g-perms").querySelectorAll("input").forEach((cb) => { cb.checked = ids.includes(cb.value); });
  $("g-all").addEventListener("click", () => setPerms(perms.map((p) => p.id)));
  $("g-none").addEventListener("click", () => setPerms([]));
  $("g-ro").addEventListener("click", () => setPerms(["map.view", "map.sidebar", "map.tracks", "map.nodes", "zones.view", "log.view"]));
  $("g-kiosk").addEventListener("click", () => setPerms(["map.view", "map.tracks"]));
  $("g-alltr").addEventListener("change", () => { $("g-trackers").hidden = $("g-alltr").checked; });
  $("g-new").addEventListener("click", () => openGroup(null));
  $("g-cancel").addEventListener("click", () => { $("g-form").hidden = true; });
  $("g-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const id = $("g-id").value;
    const body = { name: $("g-name").value.trim(), description: $("g-desc").value, history_hours: Number($("g-hist").value) || 0,
      perms: [...$("g-perms").querySelectorAll("input:checked")].map((c) => c.value),
      all_trackers: $("g-alltr").checked, trackers: [...$("g-trackers").querySelectorAll("input:checked")].map((c) => Number(c.value)) };
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
      const names = s.trackers.map((id) => (trackers.find((t) => t.id === id) || {}).alias || `#${id}`).join(", ");
      return `<div class="titem"><div class="body">
        <div><strong>${MT.esc(s.name)}</strong> ${expired ? '<span class="pill bad">verlopen</span>' : ""}</div>
        <div class="muted small">${MT.esc(names)} · spoor ${s.hours} u · ${s.expires ? "geldig tot " + fmtTs(s.expires) : "onbeperkt geldig"}
          · door ${MT.esc(s.created_by)} · laatst geopend ${MT.esc(fmtTs(s.last_used))}</div>
        <div class="mono small sharelink">${MT.esc(s.url)}</div></div>
        <div class="actions"><button data-copy="${MT.esc(s.url)}">Kopiëren</button><button class="danger" data-sdel="${s.id}">Intrekken</button></div></div>`;
    }).join("") || '<div class="empty">Nog geen deellinks.</div>';
    document.querySelectorAll("[data-copy]").forEach((b) => b.addEventListener("click", () => { navigator.clipboard.writeText(b.dataset.copy); b.textContent = "Gekopieerd"; }));
    document.querySelectorAll("[data-sdel]").forEach((b) => b.addEventListener("click", async () => {
      if (!confirm("Deze link intrekken? Wie hem heeft, ziet daarna niets meer.")) return;
      await MT.api(`/api/shares/${b.dataset.sdel}`, { method: "DELETE" }); loadShares();
    }));
  }
  $("s-new").addEventListener("click", () => { $("s-form").hidden = false; trackerChecks($("s-trackers"), []); msg($("s-msg"), ""); });
  $("s-cancel").addEventListener("click", () => { $("s-form").hidden = true; });
  $("s-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    try {
      const r = await MT.api("/api/shares", { method: "POST", body: { name: $("s-name").value.trim(),
        trackers: [...$("s-trackers").querySelectorAll("input:checked")].map((c) => Number(c.value)),
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
  const first = location.hash.slice(1) || (can("users.manage") ? "users" : "shares");
  show(can(tabPerm[first]) ? first : (can("users.manage") ? "users" : "shares"));
})();
