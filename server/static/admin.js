/* Trackers: lijst (echt en virtueel), trackergroepen, genegeerde berichten. Formulieren in een zijpaneel. */
(function () {
  const $ = (id) => document.getElementById(id);
  let status = null, trackers = [], sims = [], tgroups = [], unknown = [];
  let icon = "", filter = "all";

  function msg(el, text, ok) {
    el.textContent = text || "";
    el.className = "msg " + (ok ? "ok" : "err");
  }
  const fmtTs = (ts) => (ts ? new Date(ts * 1000).toLocaleString("nl-BE") : "–");

  // ---- zijpanelen -------------------------------------------------------------------
  const form = $("form"), tgForm = $("tg-form");
  MT.dialogize(form, "Tracker");
  MT.dialogize(tgForm, "Trackergroep");

  // ---- laden --------------------------------------------------------------------------
  async function load() {
    status = await MT.api("/api/status");
    MT.meshPill($("mesh"), status.mesh);
    [trackers, sims, tgroups] = await Promise.all([MT.api("/api/trackers"), MT.can("sims.manage") ? MT.api("/api/sims") : [],
      MT.api("/api/tracker-groups")]);
    renderList();
    renderTgroups();
    if (MT.can("trackers.manage")) {
      unknown = await MT.api("/api/unknown");
      renderUnknown();
    }
  }

  // ---- trackerlijst -------------------------------------------------------------------
  function matches(t, q) {
    if (filter === "real" && t.kind !== "real") return false;
    if (filter === "sim" && t.kind !== "sim") return false;
    if (filter === "lost" && !t.lost) return false;
    if (filter === "inactive" && t.active) return false;
    return !q || t.alias.toLowerCase().includes(q) || (t.notes || "").toLowerCase().includes(q);
  }

  function renderList() {
    const q = $("t-search").value.trim().toLowerCase();
    const simBy = Object.fromEntries(sims.map((s) => [s.tracker_id, s]));
    const rows = trackers.filter((t) => matches(t, q));
    $("ttable").hidden = !rows.length;
    $("t-empty").hidden = !!rows.length;
    $("t-empty").innerHTML = trackers.length
      ? "Geen tracker past bij de zoekopdracht of de filter."
      : `Nog geen trackers. Voeg een echte tracker toe, of start een virtuele om te oefenen.
         <div class="cta"><button class="primary" type="button" id="t-empty-new">+ Tracker</button></div>`;
    const en = $("t-empty-new");
    if (en) en.addEventListener("click", () => newTracker());
    $("trackers").innerHTML = rows.map((t) => {
      const sim = simBy[t.id];
      const pills = [
        t.kind === "sim" ? `<span class="pill">virtueel${sim && sim.status && sim.status.running ? " · rijdt" : " · gestopt"}</span>` : "",
        t.active ? "" : '<span class="pill">inactief</span>',
        t.lost ? `<span class="pill lost">verloren${t.lost_seen ? " · terug gezien" : ""}</span>` : "",
        t.last_state === "E" ? '<span class="pill sos">SOS</span>' : "",
      ].join(" ");
      const sub = [t.kind === "real" ? `${t.pubkey.slice(0, 8)}…` : null, t.last_via && t.kind === "real" ? `via ${t.last_via}` : null,
        t.kind === "real" && t.keys ? "sleutel op server" : null].filter(Boolean).join(" · ");
      const groups = (t.groups || []).map((id) => tgroups.find((g) => g.id === id)).filter(Boolean)
        .map((g) => `<span class="pill" style="border-color:${MT.esc(g.color)}">${MT.esc(g.name)}</span>`).join(" ");
      return `<tr data-id="${t.id}" tabindex="0" aria-label="${MT.esc(t.alias)} bewerken">
        <td><span class="tico" style="background:${MT.esc(t.color)}">${t.icon ? MTIcons.svg(t.icon) : ""}</span></td>
        <td><div class="nm">${MT.esc(t.alias)} ${pills}</div><div class="sub">${MT.esc(sub)}</div>
          <div class="mob">${MT.esc([MT.ago(t.last_rx), t.last_bat != null ? t.last_bat + " %" : null].filter(Boolean).join(" · "))}</div></td>
        <td class="col-opt" data-ago="${t.last_rx || 0}">${MT.esc(MT.ago(t.last_rx))}</td>
        <td class="col-opt">${t.last_bat != null ? t.last_bat + " %" : "–"}</td>
        <td class="col-opt">${MT.esc(t.last_state ? MT.STATE[t.last_state] || t.last_state : "–")}</td>
        <td class="col-opt">${groups}</td></tr>`;
    }).join("");
    $("trackers").querySelectorAll("tr").forEach((tr) => {
      const go = () => edit(Number(tr.dataset.id));
      tr.addEventListener("click", go);
      tr.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); go(); } });
    });
  }
  $("t-search").addEventListener("input", renderList);
  $("t-chips").querySelectorAll(".chip").forEach((c) => c.addEventListener("click", () => {
    filter = c.dataset.f;
    $("t-chips").querySelectorAll(".chip").forEach((x) => x.classList.toggle("on", x === c));
    renderList();
  }));
  // "laatst gehoord" bijwerken zonder de lijst opnieuw op te bouwen
  setInterval(() => document.querySelectorAll("[data-ago]").forEach((td) => { td.textContent = MT.ago(Number(td.dataset.ago) || 0); }), 15000);

  // ---- genegeerde berichten ---------------------------------------------------------
  function renderUnknown() {
    $("unk-count").hidden = !unknown.length;
    $("unk-count").textContent = unknown.length;
    $("unknown").innerHTML = unknown.length ? unknown.map((u, i) => `<div class="ev"><span class="mono">${MT.esc(u.pubkey_prefix)}</span>
      · ${MT.esc(u.reason)}${/onbekende tracker/.test(u.reason) ? ` <button type="button" class="link" data-unew="${i}">Als tracker toevoegen</button>` : ""}
      <div class="muted small">${fmtTs(u.rx_ts)} · <span class="mono">${MT.esc(u.text)}</span></div></div>`).join("")
      : '<div class="empty">Geen genegeerde berichten. Alles wat binnenkomt, komt van gekende trackers.</div>';
    $("unknown").querySelectorAll("[data-unew]").forEach((b) => b.addEventListener("click", async () => {
      const u = unknown[Number(b.dataset.unew)];
      newTracker();
      // volledige pubkey uit de contacten van de companion, als die er is
      try {
        const c = (await MT.api("/api/companion/contacts")).find((x) => x.public_key.startsWith(u.pubkey_prefix.toLowerCase()));
        if (c) { $("f-pubkey").value = c.public_key; $("f-alias").value = c.name || ""; }
        else msg($("fmsg"), `Het begin van de pubkey is ${u.pubkey_prefix}; vul de volledige pubkey in (Toestellen of MeshCore-app).`);
      } catch (_) { /* companion niet verbonden */ }
      showTab("dev");
    }));
  }

  // ---- trackergroepen ----------------------------------------------------------------
  function tgChecks(el, selected) {
    el.innerHTML = trackers.map((t) => `<label class="mini"><input type="checkbox" value="${t.id}"${selected.includes(t.id) ? " checked" : ""}>
      <i style="background:${MT.esc(t.color)}"></i>${MT.esc(t.alias)}${t.kind === "sim" ? " (virtueel)" : ""}</label>`).join("")
      || '<span class="muted">Nog geen trackers.</span>';
  }
  function renderTgroups() {
    $("tglist").innerHTML = tgroups.map((g) => `<div class="titem">
      <span class="tico big" style="background:${MT.esc(g.color)}"></span>
      <div class="body"><div><strong>${MT.esc(g.name)}</strong> <span class="muted small">${g.members.length} tracker(s)</span>
        ${g.channel ? `<span class="pill">kanaal</span>` : ""}</div>
        <div class="muted small">${MT.esc(g.description || "")}</div>
        ${(g.includes || []).length ? `<div class="small">omvat: ${MT.esc(g.includes.map((id) => (tgroups.find((x) => x.id === id) || {}).name).filter(Boolean).join(", "))}</div>` : ""}
        <div class="muted small">${MT.esc(g.members.map((id) => (trackers.find((t) => t.id === id) || {}).alias).filter(Boolean).join(", "))}</div></div>
      <div class="actions"><a class="btnlink" href="/?tgroup=${g.id}">Kaart</a><button type="button" data-tgedit="${g.id}">Bewerken</button></div></div>`).join("")
      || '<div class="empty">Nog geen trackergroepen. Maak er een, bv. per dienst of ploeg.</div>';
    $("tglist").querySelectorAll("[data-tgedit]").forEach((b) => b.addEventListener("click", () => openTg(tgroups.find((g) => g.id === Number(b.dataset.tgedit)))));
  }
  function openTg(g) {
    tgForm.setTitle(g ? `Trackergroep: ${g.name}` : "Nieuwe trackergroep");
    $("tg-id").value = g ? g.id : "";
    $("tg-name").value = g ? g.name : "";
    $("tg-color").value = g ? g.color : "#64748b";
    $("tg-desc").value = g ? g.description : "";
    tgChecks($("tg-trackers"), g ? g.trackers : []);
    const inc = g ? g.includes || [] : [];
    const others = tgroups.filter((x) => !g || x.id !== g.id);
    $("tg-includes").innerHTML = others.map((x) => `<label class="mini"><input type="checkbox" value="${x.id}"${inc.includes(x.id) ? " checked" : ""}>
      <i style="background:${MT.esc(x.color)}"></i>${MT.esc(x.name)} <span class="muted small">(${x.trackers.length})</span></label>`).join("")
      || '<span class="muted">Nog geen andere groepen of kanalen.</span>';
    msg($("tg-msg"), "");
    let del = $("tg-delete");
    if (!del) {
      tgForm.querySelector(".sticky-actions").insertAdjacentHTML("beforeend", '<button type="button" class="danger" id="tg-delete" style="margin-left:auto">Verwijderen</button>');
      del = $("tg-delete");
      del.addEventListener("click", async () => {
        const gg = tgroups.find((x) => String(x.id) === $("tg-id").value);
        if (!gg || !(await MT.confirm(`Trackergroep "${gg.name}" verwijderen? De trackers zelf blijven; gebruikersgroepen die via deze groep keken, zien ze niet meer.`, { ok: "Verwijderen", danger: true }))) return;
        try { await MT.api(`/api/tracker-groups/${gg.id}`, { method: "DELETE" }); tgForm.hidden = true; load(); } catch (e) { msg($("tg-msg"), e.message); }
      });
    }
    del.hidden = !g;
    tgForm.hidden = false;
    $("tg-name").focus();
  }
  $("tg-new").addEventListener("click", () => openTg(null));
  $("tg-cancel").addEventListener("click", () => { tgForm.hidden = true; });
  tgForm.addEventListener("submit", async (e) => {
    e.preventDefault();
    const id = $("tg-id").value;
    const body = { name: $("tg-name").value.trim(), color: $("tg-color").value, description: $("tg-desc").value,
      trackers: [...$("tg-trackers").querySelectorAll("input:checked")].map((c) => Number(c.value)),
      includes: [...$("tg-includes").querySelectorAll("input:checked")].map((c) => Number(c.value)) };
    try {
      await MT.api(id ? `/api/tracker-groups/${id}` : "/api/tracker-groups", { method: id ? "PUT" : "POST", body });
      tgForm.hidden = true;
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
    if (!MT.can("sims.manage")) return;
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

  // ---- trackerformulier -------------------------------------------------------------
  $("f-town").innerHTML = MT.TOWNS.map((t, i) => `<option value="${i}">${t[0]}</option>`).join("");

  function renderIcons() { MTIcons.picker($("f-icons"), icon, $("f-color").value, (id) => { icon = id; }); }
  $("f-color").addEventListener("input", renderIcons);

  function showTab(name) {
    $("f-tabs").querySelectorAll("[data-ft]").forEach((b) => b.classList.toggle("on", b.dataset.ft === name));
    form.querySelectorAll("[data-fp]").forEach((p) => { p.hidden = p.dataset.fp !== name; });
  }
  $("f-tabs").querySelectorAll("[data-ft]").forEach((b) => b.addEventListener("click", () => showTab(b.dataset.ft)));

  function setVirtual(v) {
    $("f-virtual").checked = v;
    $("f-kind").querySelectorAll("button").forEach((b) => b.classList.toggle("on", (b.dataset.v === "sim") === v));
    $("ft-dev").hidden = v;
    $("ft-sim").hidden = !v;
    $("f-lostrow").hidden = v;
    const cur = $("f-tabs").querySelector("button.on");
    if (cur && cur.hidden) showTab("gen");
  }
  $("f-kind").querySelectorAll("button").forEach((b) => b.addEventListener("click", () => {
    if ($("f-id").value) return;                 // soort wisselen = nieuwe tracker
    setVirtual(b.dataset.v === "sim");
  }));

  function resetForm() {
    form.reset();
    $("f-id").value = "";
    $("f-pubkey").disabled = false;
    $("f-color").value = "#e4572e";
    $("f-active").checked = true;
    $("f-running").checked = true;
    $("contacts").hidden = true;
    $("ft-data").hidden = true;
    $("f-histrow").hidden = true;
    $("f-genkey").checked = false;
    $("f-pubrow").hidden = false;
    $("f-genrow").hidden = !MT.can("keys.manage");
    $("f-genhelp").hidden = !MT.can("keys.manage");
    $("f-keys").hidden = true;
    $("f-authrow").hidden = true;
    $("f-kind").hidden = !(MT.can("trackers.manage") && MT.can("sims.manage"));
    fillFormGroups([]);
    icon = "";
    setVirtual(!MT.can("trackers.manage"));
    renderIcons();
    fillDrive(null);
    renderSpeeds("car", null);
    showTab("gen");
    $("save").textContent = "Toevoegen";
    msg($("fmsg"), "");
  }

  function newTracker() {
    resetForm();
    form.setTitle("Tracker toevoegen");
    form.hidden = false;
    $("f-alias").focus();
  }
  $("new").addEventListener("click", newTracker);
  $("cancel").addEventListener("click", () => { form.hidden = true; });

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
    $("f-kind").hidden = true;
    fillFormGroups(t.groups || []);
    icon = t.icon || "";
    setVirtual(t.kind === "sim");
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
        form.querySelectorAll("[data-sp]").forEach((el) => {
          const v = s.params[el.dataset.sp];
          if (v == null) return;
          if (el.dataset.kind === "bool") el.checked = !!Number(v); else el.value = v;
        });
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
      $("f-genrow").hidden = true;
      $("f-genhelp").hidden = true;
      $("f-authrow").hidden = !(MT.can("trackers.serial") || MT.can("keys.manage"));
      $("f-authkey").textContent = t.has_authkey ? "verborgen" : "nog geen";
      $("f-authcopy").hidden = true;
      loadKeys(t).catch(() => {});
    }
    renderIcons();
    $("ft-data").hidden = false;
    $("save").textContent = "Opslaan";
    loadDataInfo(t.id);
    form.setTitle(`Bewerken: ${t.alias}`);
    form.hidden = false;
  }

  $("f-history").addEventListener("click", async () => {
    if (!(await MT.confirm("Alle posities van deze simulator wissen en de historiek opnieuw opbouwen?", { ok: "Opnieuw opbouwen", danger: true }))) return;
    try {
      await MT.api(`/api/sims/${$("f-id").value}/history`, { method: "POST" });
      msg($("fmsg"), "De historiek wordt op de achtergrond opgebouwd; de simulator rijdt daarna verder.", true);
    } catch (e) { msg($("fmsg"), e.message); }
  });

  async function loadDataInfo(id) {
    const d = await MT.api(`/api/trackers/${id}/data`);
    $("f-datainfo").textContent = d.n ? `${d.n} posities, van ${fmtTs(d.first)} tot ${fmtTs(d.last)}` : "Geen opgeslagen posities.";
  }
  async function purge(all) {
    const id = $("f-id").value;
    const t = trackers.find((x) => String(x.id) === id);
    const days = all ? 0 : Number($("f-purgedays").value);
    if (!all && !(days > 0)) return;
    const what = all ? "ALLE posities" : `alle posities ouder dan ${days} dagen`;
    if (!(await MT.confirm(`${what} van "${t ? t.alias : "deze tracker"}" wissen? Dit kan niet ongedaan gemaakt worden.`, { ok: "Wissen", danger: true }))) return;
    try {
      const r = await MT.api(`/api/trackers/${id}/purge`, { method: "POST", body: { older_than_days: days } });
      msg($("fmsg"), `${r.deleted} posities gewist.`, true);
      loadDataInfo(id);
    } catch (e) { msg($("fmsg"), e.message); }
  }
  $("f-purge").addEventListener("click", () => purge(false));
  $("f-purgeall").addEventListener("click", () => purge(true));

  $("f-delete").addEventListener("click", async () => {
    const t = trackers.find((x) => String(x.id) === $("f-id").value);
    if (!t || !(await MT.confirm(`"${t.alias}" verwijderen? Alle opgeslagen posities, sleutels en backups gaan mee weg.`, { ok: "Verwijderen", danger: true, title: "Tracker verwijderen" }))) return;
    try {
      await MT.api(`/api/trackers/${t.id}`, { method: "DELETE" });
      form.hidden = true;
      load();
    } catch (e) { msg($("fmsg"), e.message); }
  });

  function simBody() {
    const params = {};
    form.querySelectorAll("[data-sp]").forEach((el) => { params[el.dataset.sp] = el.dataset.kind === "bool" ? (el.checked ? 1 : 0) : Number(el.value); });
    const sel = $("f-town");
    let lat, lon;
    if (sel.value === "custom") { lat = Number(sel.dataset.lat); lon = Number(sel.dataset.lon); }
    else { const t = MT.TOWNS[Number(sel.value)]; lat = t[1]; lon = t[2]; }
    return { profile: $("f-profile").value, home_lat: lat, home_lon: lon, params,
             loss_pct: Number($("f-loss").value), batt_speed: Number($("f-batt").value), running: $("f-running").checked,
             drive: driveBody() };
  }

  form.addEventListener("submit", async (e) => {
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
      } else {
        const gen = !id && $("f-genkey").checked;
        if (!id && !gen && !/^[0-9a-fA-F]{64}$/.test($("f-pubkey").value.trim())) { showTab("dev"); throw new Error("Vul de pubkey in (64 hex-tekens), of kies 'Nieuw toestel'."); }
        if (id) r = await MT.api(`/api/trackers/${id}`, { method: "PUT", body: { ...base, groups } });
        else r = await MT.api("/api/trackers", { method: "POST", body: { ...base, groups, pubkey: gen ? null : $("f-pubkey").value.trim(), generate_key: gen } });
        if (gen && r.tracker) {
          form.hidden = true;
          if (await MT.confirm(`"${r.tracker.alias}" heeft nu een sleutel op de server. Nu het toestel klaarmaken via Toestellen?`, { ok: "Naar Toestellen", title: "Toestel klaarmaken" }))
            location.href = `/devices#prov?tracker=${r.tracker.id}`;
          load();
          return;
        }
      }
      form.hidden = true;
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
  $("f-genkey").addEventListener("change", () => { $("f-pubrow").hidden = $("f-genkey").checked; });

  // ---- authsleutel ------------------------------------------------------------------
  const groupKey = (k) => k.match(/.{1,4}/g).join(" ");
  async function showAuth(fresh) {
    const id = $("f-id").value;
    if (!id) return;
    if (fresh && !(await MT.confirm("Een nieuwe authsleutel maken? De tracker moet dan ook de nieuwe krijgen, anders worden zijn kanaalberichten geweigerd.", { ok: "Nieuwe sleutel", danger: true }))) return;
    try {
      const { authkey } = await MT.api(`/api/trackers/${id}/authkey${fresh ? "?new=1" : ""}`);
      $("f-authkey").textContent = groupKey(authkey);
      $("f-authcopy").hidden = false;
      $("f-authcopy").onclick = () => navigator.clipboard.writeText(authkey);
    } catch (e) { msg($("fmsg"), e.message); }
  }
  $("f-authshow").addEventListener("click", () => showAuth(false));
  $("f-authnew").addEventListener("click", () => showAuth(true));

  // ---- sleutels en backups op de server ---------------------------------------------
  let editing = null;
  function download(name, data, type) {
    const a = document.createElement("a");
    a.href = URL.createObjectURL(new Blob([data], { type }));
    a.download = name;
    document.body.appendChild(a);
    a.click();
    setTimeout(() => { URL.revokeObjectURL(a.href); a.remove(); }, 2000);
  }
  const jsonName = (doc) => `${(doc.name || "toestel").replace(/[^\p{L}\p{N}_-]+/gu, "_")}_meshcore_config_${new Date().toISOString().slice(0, 10)}.json`;
  async function loadKeys(t) {
    editing = t;
    if (!MT.can("keys.manage") || t.kind !== "real") { $("f-keys").hidden = true; return; }
    $("f-keys").hidden = false;
    const rows = await MT.api(`/api/trackers/${t.id}/keys`);
    const KIND = { generated: "sleutel van de server", backup: "backup van het toestel", import: "export uit de app" };
    $("f-keylist").innerHTML = rows.length ? rows.map((k) => {
      const s = k.summary || {};
      const bits = [s.radio, s.path_bytes ? `${s.path_bytes} bytes per hop` : null, s.scope ? `regio ${s.scope}` : null,
        s.channels ? `${s.channels} ${s.channels === 1 ? "kanaal" : "kanalen"}` : null, s.contacts ? `${s.contacts} contacten (niet teruggezet)` : null, s.fw ? `fw ${s.fw}` : null].filter(Boolean);
      return `<div class="ev"><strong>${MT.esc(KIND[k.kind] || k.kind)}</strong> · ${fmtTs(k.ts)} · ${MT.esc(k.who)}
        ${k.note ? `<div class="muted">${MT.esc(k.note)}</div>` : ""}<div class="muted">${MT.esc(bits.join(" · "))}</div>
        <div class="row" style="margin-top:4px">${MT.can("trackers.serial") ? `<a class="btnlink" href="/devices#prov?tracker=${t.id}&backup=${k.id}">Op een toestel zetten</a>` : ""}
        <button type="button" data-kget="${k.id}">Downloaden</button><button type="button" class="danger" data-kdel="${k.id}">Verwijderen</button></div></div>`;
    }).join("") : '<div class="empty">Nog geen sleutel of backup op de server. Maak er een via Toestellen ("Backup naar de server"), of importeer een export uit de MeshCore-app.</div>';
    $("f-keylist").querySelectorAll("[data-kget]").forEach((b) => b.addEventListener("click", async () => {
      const doc = await MT.api(`/api/trackers/${t.id}/keys/${b.dataset.kget}`);
      download(jsonName(doc), JSON.stringify(doc, null, 2), "application/json");
    }));
    $("f-keylist").querySelectorAll("[data-kdel]").forEach((b) => b.addEventListener("click", async () => {
      if (!(await MT.confirm("Deze backup (met privésleutel) van de server verwijderen?", { ok: "Verwijderen", danger: true }))) return;
      await MT.api(`/api/trackers/${t.id}/keys/${b.dataset.kdel}`, { method: "DELETE" });
      loadKeys(t); load();
    }));
  }
  $("f-keyfile").addEventListener("change", async (e) => {
    const f = e.target.files[0];
    e.target.value = "";
    if (!f || !editing) return;
    try {
      const doc = JSON.parse(await f.text());
      if (!doc.private_key) throw new Error("dit bestand bevat geen privésleutel");
      await MT.api(`/api/trackers/${editing.id}/keys`, { method: "POST", body: { doc, kind: "import", note: f.name } });
      loadKeys(editing); load();
    } catch (err) { msg($("fmsg"), `Importeren mislukt: ${err.message}`); }
  });

  // ---- start ---------------------------------------------------------------------------
  let showPage = null;
  MT.live((m) => {
    if (m.type === "mesh") MT.meshPill($("mesh"), m.mesh);
    if ((m.type === "tracker" || m.type === "tracker_deleted" || m.type === "tracker_groups") && !form.dialog.open && !tgForm.dialog.open) load();
  });
  const startHash = location.hash;                          // vóór de tabbladen de hash herschrijven
  MT.initHeader("/admin").then(async () => {
    const manage = MT.can("trackers.manage") || MT.can("sims.manage");
    showPage = MT.tabs($("ptabs"), null, (n) => n === "trackers" ? manage : MT.can("trackers.manage"));
    $("new").hidden = !manage;
    await load();
    const h = startHash;
    const m = /edit=(\d+)/.exec(h);                         // vanaf de kaart: meteen bewerken
    if (m) edit(Number(m[1]));
    const n = /new\?(.*)$/.exec(h);                         // vanaf Toestellen: tracker toevoegen met deze pubkey
    if (n) {
      const q = new URLSearchParams(n[1]);
      newTracker();
      setVirtual(false);
      $("f-pubkey").value = q.get("pubkey") || "";
      $("f-alias").value = q.get("alias") || "";
    }
  });
})();
