/* Gedeelde hulpjes voor kaart- en beheerpagina. */
const MT = {
  async api(path, opts = {}) {
    const r = await fetch(path, {
      ...opts,
      headers: opts.body ? { "Content-Type": "application/json" } : {},
      body: opts.body ? JSON.stringify(opts.body) : undefined,
    });
    if (r.status === 401) { location.href = "/login"; throw new Error("niet ingelogd"); }
    if (r.status === 403 && !opts.quiet403) throw new Error("geen toegang");
    const j = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(j.detail || r.statusText);
    return j;
  },

  esc(s) {
    return String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  },

  ago(ts) {
    if (!ts) return "nooit";
    const s = Math.max(0, Math.round(Date.now() / 1000 - ts));
    if (s < 60) return `${s} s geleden`;
    if (s < 3600) return `${Math.round(s / 60)} min geleden`;
    if (s < 86400) return `${Math.round(s / 3600)} u geleden`;
    return `${Math.round(s / 86400)} d geleden`;
  },

  STATE: { M: "rijdt/stapt", S: "stilgevallen", H: "heartbeat", N: "geen GPS-fix", E: "SOS", B: "modus", P: "handmatig verstuurd", W: "wakker door beweging" },
  MODE: { c: "companion", t: "tracker" },

  TOWNS: [
    ["Hasselt", 50.9307, 5.3378], ["Genk", 50.9650, 5.5000], ["Sint-Truiden", 50.8160, 5.1866],
    ["Tongeren", 50.7806, 5.4646], ["Beringen", 51.0490, 5.2260], ["Leuven", 50.8798, 4.7005],
    ["Brussel", 50.8503, 4.3517], ["Antwerpen", 51.2194, 4.4025], ["Gent", 51.0543, 3.7174],
    ["Brugge", 51.2093, 3.2247], ["Luik", 50.6326, 5.5797], ["Namen", 50.4674, 4.8720],
    ["Maastricht", 50.8514, 5.6910], ["Eindhoven", 51.4416, 5.4697], ["Utrecht", 52.0907, 5.1214],
    ["Rotterdam", 51.9244, 4.4777], ["Amsterdam", 52.3676, 4.9041], ["Luxemburg", 49.6116, 6.1319],
  ],

  me: null,
  can(perm) { return !!(MT.me && MT.me.perms.includes(perm)); },

  /* Kopbalk: menu volgens de rechten, gebruikersmenu met wachtwoord en uitloggen. */
  async initHeader(active) {
    MT.me = await MT.api("/api/me");
    const links = [
      ["/", "Kaart", "map.view"], ["/log", "Logboek", "log.view"],
      ["/admin", "Beheer", ["trackers.manage", "trackers.serial", "sims.manage", "companion.view"]],
      ["/users", "Gebruikers", ["users.manage", "share.manage"]], ["/help", "Help", null],
    ];
    const nav = document.querySelector("header.top nav");
    if (nav) {
      nav.innerHTML = links.filter(([, , p]) => !p || (Array.isArray(p) ? p.some(MT.can) : MT.can(p)))
        .map(([href, label]) => `<a href="${href}"${href === active ? ' class="on"' : ""}>${label}</a>`).join("");
    }
    const hdr = document.querySelector("header.top");
    if (hdr && !document.getElementById("usermenu") && MT.me.kind === "user") {
      hdr.insertAdjacentHTML("beforeend", `<details id="usermenu" class="usermenu"><summary title="${MT.esc(MT.me.group)}">
        <span class="avatar">${MT.esc((MT.me.display || "?").slice(0, 1).toUpperCase())}</span></summary>
        <div class="menu"><div class="who"><strong>${MT.esc(MT.me.display)}</strong><div class="muted small">${MT.esc(MT.me.group)}</div></div>
        <button type="button" id="um-pw">Wachtwoord wijzigen</button><button type="button" id="um-out">Uitloggen</button></div></details>`);
      document.getElementById("um-out").addEventListener("click", async () => { await MT.api("/api/logout", { method: "POST" }); location.href = "/login"; });
      document.getElementById("um-pw").addEventListener("click", MT.passwordDialog);
    }
    return MT.me;
  },

  passwordDialog() {
    let d = document.getElementById("pwdlg");
    if (!d) {
      document.body.insertAdjacentHTML("beforeend", `<dialog id="pwdlg" class="dlg"><form method="dialog" id="pwform">
        <h2>Wachtwoord wijzigen</h2>
        <label for="pw-old">Huidig wachtwoord</label><input id="pw-old" type="password" autocomplete="current-password" required>
        <label for="pw-new">Nieuw wachtwoord (min. 8 tekens)</label><input id="pw-new" type="password" autocomplete="new-password" minlength="8" required>
        <div class="row"><button class="primary" id="pw-save" type="submit">Opslaan</button><button type="button" id="pw-cancel">Annuleren</button></div>
        <div id="pw-msg" class="msg"></div></form></dialog>`);
      d = document.getElementById("pwdlg");
      document.getElementById("pw-cancel").addEventListener("click", () => d.close());
      document.getElementById("pwform").addEventListener("submit", async (e) => {
        e.preventDefault();
        const m = document.getElementById("pw-msg");
        try {
          await MT.api("/api/me/password", { method: "POST", body: { old: document.getElementById("pw-old").value, new: document.getElementById("pw-new").value } });
          m.className = "msg ok"; m.textContent = "Gewijzigd. Andere sessies zijn afgemeld.";
          setTimeout(() => d.close(), 1200);
        } catch (err) { m.className = "msg err"; m.textContent = err.message; }
      });
    }
    d.querySelector("form").reset();
    document.getElementById("pw-msg").textContent = "";
    d.showModal();
  },

  meshPill(el, m) {
    el.className = "pill " + (m.connected ? "ok" : "bad");
    el.textContent = m.connected ? `mesh: ${m.name || "verbonden"}` : "mesh: niet verbonden";
    el.title = m.connected ? `${m.host}:${m.port}` : (m.last_error || "");
  },

  live(onMsg) {
    let delay = 1000;
    const open = () => {
      const ws = new WebSocket((location.protocol === "https:" ? "wss://" : "ws://") + location.host + "/ws");
      ws.onopen = () => { delay = 1000; };
      ws.onmessage = (e) => onMsg(JSON.parse(e.data));
      ws.onclose = (e) => {
        if (e.code === 4401) { location.href = "/login"; return; }
        setTimeout(open, delay); delay = Math.min(delay * 2, 30000);
      };
    };
    open();
  },
};
