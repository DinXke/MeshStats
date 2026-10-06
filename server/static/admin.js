/* Beheerpagina: trackers toevoegen/bewerken/verwijderen + instellen via Web Serial. */
(function () {
  const $ = (id) => document.getElementById(id);
  let status = null;
  let trackers = [];

  function msg(el, text, ok) {
    el.textContent = text || "";
    el.className = "msg " + (ok ? "ok" : "err");
  }

  // ---- trackers ---------------------------------------------------------------
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

  async function load() {
    status = await MT.api("/api/status");
    MT.meshPill($("mesh"), status.mesh);
    renderCompanion(status.mesh);
    trackers = await MT.api("/api/trackers");
    $("none").hidden = trackers.length > 0;
    $("rows").innerHTML = trackers.map((t) => `<tr>
      <td><span class="dot" style="background:${MT.esc(t.color)}"></span></td>
      <td><strong>${MT.esc(t.alias)}</strong>${t.active ? "" : ' <span class="pill">inactief</span>'}
        ${t.notes ? `<div class="muted">${MT.esc(t.notes)}</div>` : ""}</td>
      <td class="hide-sm mono">${MT.esc(t.pubkey.slice(0, 16))}…</td>
      <td>${MT.ago(t.last_rx)}</td>
      <td class="hide-sm">${t.last_bat != null ? t.last_bat + "%" : "–"}</td>
      <td style="white-space:nowrap"><button data-edit="${t.id}">Bewerken</button>
        <button class="danger" data-del="${t.id}">Verwijderen</button></td></tr>`).join("");
    document.querySelectorAll("[data-edit]").forEach((b) => b.addEventListener("click", () => edit(Number(b.dataset.edit))));
    document.querySelectorAll("[data-del]").forEach((b) => b.addEventListener("click", () => del(Number(b.dataset.del))));
    const unk = await MT.api("/api/unknown");
    $("unknown").innerHTML = unk.length ? unk.map((u) => `<tr><td>${new Date(u.rx_ts * 1000).toLocaleString("nl-BE")}</td>
      <td class="mono">${MT.esc(u.pubkey_prefix)}</td><td>${MT.esc(u.reason)}</td>
      <td class="hide-sm mono">${MT.esc(u.text)}</td></tr>`).join("") : '<tr><td colspan="4" class="muted">geen</td></tr>';
  }

  function resetForm() {
    $("form").reset();
    $("f-id").value = "";
    $("f-pubkey").disabled = false;
    $("f-color").value = "#e4572e";
    $("f-active").checked = true;
    $("ftitle").textContent = "Tracker toevoegen";
    $("save").textContent = "Toevoegen";
    $("cancel").hidden = true;
    $("contacts").hidden = true;
  }

  function edit(id) {
    const t = trackers.find((x) => x.id === id);
    if (!t) return;
    $("f-id").value = t.id;
    $("f-alias").value = t.alias;
    $("f-color").value = t.color;
    $("f-pubkey").value = t.pubkey;
    $("f-pubkey").disabled = true;   // sleutel = identiteit; nieuwe sleutel = nieuwe tracker
    $("f-notes").value = t.notes;
    $("f-active").checked = !!t.active;
    $("ftitle").textContent = `Bewerken: ${t.alias}`;
    $("save").textContent = "Opslaan";
    $("cancel").hidden = false;
    msg($("fmsg"), "");
    $("form").scrollIntoView({ behavior: "smooth" });
  }

  async function del(id) {
    const t = trackers.find((x) => x.id === id);
    if (!t || !confirm(`Tracker "${t.alias}" verwijderen?\nAlle opgeslagen posities gaan mee weg en het contact wordt van de companion gehaald.`)) return;
    try {
      const r = await MT.api(`/api/trackers/${id}`, { method: "DELETE" });
      msg($("fmsg"), `"${t.alias}" verwijderd. ${r.contact || ""}`, true);
      resetForm();
      load();
    } catch (e) { msg($("fmsg"), e.message); }
  }

  $("form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const id = $("f-id").value;
    const body = { alias: $("f-alias").value.trim(), color: $("f-color").value, notes: $("f-notes").value,
                   active: $("f-active").checked };
    try {
      let r;
      if (id) r = await MT.api(`/api/trackers/${id}`, { method: "PUT", body });
      else r = await MT.api("/api/trackers", { method: "POST", body: { ...body, pubkey: $("f-pubkey").value.trim() } });
      msg($("fmsg"), `Bewaard. ${r.contact || ""}`, true);
      resetForm();
      load();
    } catch (err) { msg($("fmsg"), err.message); }
  });
  $("cancel").addEventListener("click", () => { resetForm(); msg($("fmsg"), ""); });

  $("pick").addEventListener("click", async () => {
    try {
      const cs = (await MT.api("/api/companion/contacts")).filter((c) => c.type === 1);
      const sel = $("contacts");
      sel.innerHTML = '<option value="">– kies een contact –</option>' + cs.map((c) =>
        `<option value="${MT.esc(c.public_key)}">${MT.esc(c.name || "?")} (${MT.esc(c.public_key.slice(0, 8))})</option>`).join("");
      sel.hidden = false;
      if (!cs.length) msg($("fmsg"), "De companion kent nog geen chat-contacten.");
    } catch (e) { msg($("fmsg"), e.message); }
  });
  $("contacts").addEventListener("change", (e) => {
    if (!e.target.value) return;
    $("f-pubkey").value = e.target.value;
    if (!$("f-alias").value) $("f-alias").value = e.target.selectedOptions[0].textContent.replace(/ \(.*\)$/, "");
  });

  $("logout").addEventListener("click", async () => { await MT.api("/api/logout", { method: "POST" }); location.href = "/login"; });

  // ---- Web Serial -------------------------------------------------------------
  let port = null, reader = null, buf = "", lines = [];
  const log = $("s-log");
  const append = (t) => { log.textContent += t; log.scrollTop = log.scrollHeight; };
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  const serialBtns = ["s-status", "s-use", "s-disconnect", "s-cmd", "s-send"];

  if (!("serial" in navigator)) {
    $("serial-note").textContent = "Deze browser kent geen Web Serial. Gebruik Chrome of Edge op een computer (via https of localhost).";
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
  }

  async function send(cmd) {
    if (!port || !port.writable) throw new Error("niet verbonden");
    const w = port.writable.getWriter();
    await w.write(new TextEncoder().encode(cmd + "\r"));
    w.releaseLock();
  }

  async function command(cmd, waitMs = 700) {
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
    return kv;
  }

  function fillForm(kv) {
    const strip = (v) => (v || "").replace(/(km\/h|deg|m)$/, "");
    document.querySelectorAll("[data-set]").forEach((el) => {
      const k = el.dataset.set;
      if (!(k in kv)) return;
      let v = kv[k];
      if (["min_speed", "min_dist", "turn_min", "turn_min_speed"].includes(k)) v = strip(v);
      if (k === "track_in_companion") v = v === "aan" ? "on" : "off";
      if (k === "max_interval" || k === "heartbeat") v = v === "uit" ? "0" : v;
      if (k === "target" && v.startsWith("(")) v = "";
      el.value = v;
    });
    if (kv.gekozen) document.querySelector("[data-mode]").value = kv.gekozen;
  }

  let lastKv = {};
  async function readStatus() {
    const ls = await command("status", 900);
    lastKv = parseStatus(ls);
    if (!lastKv.pubkey) { append("\n[geen status ontvangen: is dit een MeshTrack-tracker?]\n"); return; }
    fillForm(lastKv);
    $("s-form").hidden = false;
  }

  $("s-connect").addEventListener("click", async () => {
    try {
      port = await navigator.serial.requestPort();
      await port.open({ baudRate: 115200 });
      await port.setSignals({ dataTerminalReady: true });
      serialBtns.forEach((id) => { $(id).disabled = false; });
      $("s-connect").disabled = true;
      log.textContent = "";
      readLoop();
      await sleep(400);
      await readStatus();
    } catch (e) { append(`\n[verbinden mislukt: ${e.message}]\n`); }
  });

  $("s-disconnect").addEventListener("click", async () => {
    try { if (reader) await reader.cancel(); await port.close(); } catch (_) {}
    port = null;
    serialBtns.forEach((id) => { $(id).disabled = true; });
    $("s-connect").disabled = false;
    $("s-form").hidden = true;
  });

  $("s-status").addEventListener("click", readStatus);

  $("s-use").addEventListener("click", async () => {
    if (!lastKv.pubkey) await readStatus();
    if (!lastKv.pubkey) return;
    resetForm();
    $("f-pubkey").value = lastKv.pubkey;
    $("f-alias").value = lastKv.naam || "";
    $("form").scrollIntoView({ behavior: "smooth" });
    $("f-alias").focus();
  });

  $("s-target").addEventListener("click", () => {
    const pk = status && status.mesh && status.mesh.pubkey;
    if (pk) document.querySelector('[data-set="target"]').value = pk;
    else append("\n[server-companion niet verbonden: pubkey onbekend]\n");
  });

  $("s-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const cmds = [];
    document.querySelectorAll("[data-set]").forEach((el) => {
      const v = el.value.trim();
      if (v !== "" && !/\s/.test(v)) cmds.push(`set ${el.dataset.set} ${v}`);
    });
    cmds.push(`mode ${document.querySelector("[data-mode]").value}`);
    for (const c of cmds) {
      const out = await command(c, 300);
      if (out.some((l) => /ongeldig|onbekend|NIET/.test(l))) append(`\n[!] ${c} werd niet aanvaard\n`);
    }
    append("\n[instellingen verstuurd]\n");
    await readStatus();
  });

  async function sendFree() {
    const c = $("s-cmd").value.trim();
    if (!c) return;
    $("s-cmd").value = "";
    await send(c);
  }
  $("s-send").addEventListener("click", sendFree);
  $("s-cmd").addEventListener("keydown", (e) => { if (e.key === "Enter") sendFree(); });

  MT.live((m) => {
    if (m.type === "mesh") MT.meshPill($("mesh"), m.mesh);
    if (m.type === "position" || m.type === "tracker" || m.type === "tracker_deleted") load();
  });
  load();
})();
