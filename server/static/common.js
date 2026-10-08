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

  STATE: { M: "rijdt/stapt", S: "stilgevallen", H: "heartbeat", N: "geen GPS-fix", E: "SOS", B: "modus", P: "handmatig verstuurd", W: "wakker door beweging", L: "gelogd punt (SlowTrack)", Q: "ingehaald punt (FIFO)" },
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

  // ---- voorkeuren: in het gebruikersprofiel op de server, met localStorage als
  // snelle kopie (en als enige bron voor deellinks) -----------------------------
  _pending: {}, _timer: null,
  prefGet(key, def) {
    if (MT.me && MT.me.kind === "user" && MT.me.prefs && key in MT.me.prefs) return MT.me.prefs[key];
    try { const v = localStorage.getItem("mt." + key); return v === null ? def : JSON.parse(v); } catch (_) { return def; }
  },
  prefSet(key, val) {
    try { localStorage.setItem("mt." + key, JSON.stringify(val)); } catch (_) { /* geen opslag */ }
    if (!MT.me || MT.me.kind !== "user") return;
    MT.me.prefs[key] = val;
    MT._pending[key] = val;
    clearTimeout(MT._timer);
    MT._timer = setTimeout(() => {
      const body = MT._pending; MT._pending = {};
      MT.api("/api/me/prefs", { method: "PUT", body }).catch(() => {});
    }, 800);
  },

  // ---- thema's --------------------------------------------------------------------
  THEMES: { auto: "Automatisch", light: "Licht", dark: "Donker", night: "Nacht (rood)", contrast: "Hoog contrast", ocean: "Oceaan" },
  theme() { return MT.prefGet("theme", "auto"); },
  isDark() {
    const t = MT.theme();
    if (t === "dark" || t === "night") return true;
    if (t === "auto") return window.matchMedia("(prefers-color-scheme: dark)").matches;
    return false;
  },
  applyTheme(t) {
    if (t === "auto") document.documentElement.removeAttribute("data-theme");
    else document.documentElement.setAttribute("data-theme", t);
    document.dispatchEvent(new CustomEvent("mt-theme"));
  },
  setTheme(t) { MT.prefSet("theme", t); MT.applyTheme(t); },
  can(perm) { return !!(MT.me && MT.me.perms.includes(perm)); },

  /* Kopbalk: menu volgens de rechten, gebruikersmenu met wachtwoord en uitloggen. */
  async initHeader(active) {
    MT.me = await MT.api("/api/me");
    MT.applyTheme(MT.theme());
    const links = [
      ["/", "Kaart", "map.view"], ["/log", "Logboek", "log.view"], ["/kanalen", "Kanalen", "map.view"],
      ["/admin", "Trackers", ["trackers.manage", "sims.manage"]],
      ["/devices", "Toestellen", "trackers.serial"],
      ["/users", "Gebruikers", ["users.manage", "share.manage"]],
      ["/system", "Systeem", ["alerts.manage", "alerts.personal", "system.manage", "companion.view"]],
      ["/help", "Help", null],
    ];
    // losse apps: voor elke ingelogde gebruiker, als kleine groep achteraan (op een gsm in hetzelfde menu)
    const apps = [["/offline", "Offline-kaart"], ["/tracker", "Tracker live"]];
    const nav = document.querySelector("header.top nav");
    if (nav) {
      const a = ([href, label]) => `<a href="${href}"${href === active ? ' class="on"' : ""}>${label}</a>`;
      nav.innerHTML = links.filter(([, , p]) => !p || (Array.isArray(p) ? p.some(MT.can) : MT.can(p))).map(a).join("")
        + (MT.me.kind === "user" ? `<span class="navapps" role="group" aria-label="Apps"><span class="navapps-l">Apps</span>${apps.map(a).join("")}</span>` : "");
    }
    const hdr = document.querySelector("header.top");
    if (hdr && nav && !hdr.querySelector(".navtoggle")) {     // gsm: menuknop
      hdr.querySelector("h1").insertAdjacentHTML("beforebegin",
        '<button type="button" class="navtoggle" aria-label="Menu" aria-expanded="false">☰</button>');
      const tg = hdr.querySelector(".navtoggle");
      tg.addEventListener("click", () => {
        const open = hdr.classList.toggle("navopen");
        tg.setAttribute("aria-expanded", String(open));
      });
    }
    document.querySelectorAll(".msg").forEach((m) => { if (!m.hasAttribute("aria-live")) m.setAttribute("aria-live", "polite"); });
    if (hdr && MT.me.kind === "share" && !document.getElementById("sharelogin")) {
      hdr.insertAdjacentHTML("beforeend", '<a id="sharelogin" class="btnlink" href="/login">Inloggen</a>');
    }
    if (hdr && !document.getElementById("usermenu") && MT.me.kind === "user") {
      hdr.insertAdjacentHTML("beforeend", `<details id="usermenu" class="usermenu"><summary title="${MT.esc(MT.me.group)}">
        <span class="avatar">${MT.esc((MT.me.display || "?").slice(0, 1).toUpperCase())}</span></summary>
        <div class="menu"><div class="who"><strong>${MT.esc(MT.me.display)}</strong><div class="muted small">${MT.esc(MT.me.group)}</div></div>
        <div class="muted small">Thema</div><div class="themes" id="um-themes"></div>
        <button type="button" id="um-pw">Wachtwoord wijzigen</button><button type="button" id="um-out">Uitloggen</button></div></details>`);
      const tb = document.getElementById("um-themes");
      const drawThemes = () => {
        tb.innerHTML = Object.entries(MT.THEMES).map(([k, v]) => `<button type="button" data-th="${k}"${MT.theme() === k ? ' class="on"' : ""}>${v}</button>`).join("");
        tb.querySelectorAll("[data-th]").forEach((b) => b.addEventListener("click", () => { MT.setTheme(b.dataset.th); drawThemes(); }));
      };
      drawThemes();
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

  /* Tabbladen: knoppen [data-tab] in container, panelen #pane-<naam>. ARIA, pijltjestoetsen,
     actief tabblad in location.hash. onShow(naam) bij elke wissel. */
  tabs(container, onShow, allowed) {
    const btns = [...container.querySelectorAll("[data-tab]")];
    container.setAttribute("role", "tablist");
    const ok = (b) => !b.hidden && (!allowed || allowed(b.dataset.tab));
    btns.forEach((b) => {
      const pane = document.getElementById("pane-" + b.dataset.tab);
      b.setAttribute("role", "tab");
      b.id = b.id || "tab-" + b.dataset.tab;
      if (pane) { pane.setAttribute("role", "tabpanel"); pane.setAttribute("aria-labelledby", b.id); b.setAttribute("aria-controls", pane.id); }
      if (allowed && !allowed(b.dataset.tab)) b.hidden = true;
      b.addEventListener("click", () => show(b.dataset.tab));
      b.addEventListener("keydown", (e) => {
        const vis = btns.filter(ok), i = vis.indexOf(b);
        let n = null;
        if (e.key === "ArrowRight") n = vis[(i + 1) % vis.length];
        if (e.key === "ArrowLeft") n = vis[(i - 1 + vis.length) % vis.length];
        if (e.key === "Home") n = vis[0];
        if (e.key === "End") n = vis[vis.length - 1];
        if (n) { e.preventDefault(); show(n.dataset.tab); n.focus(); }
      });
    });
    function show(name) {
      btns.forEach((b) => {
        const on = b.dataset.tab === name;
        b.classList.toggle("on", on);
        b.setAttribute("aria-selected", String(on));
        b.tabIndex = on ? 0 : -1;
        const pane = document.getElementById("pane-" + b.dataset.tab);
        if (pane) pane.hidden = !on;
      });
      const h = location.hash.slice(1).split("?")[0];
      if (h !== name) history.replaceState(null, "", "#" + name + (location.hash.includes("?") && h === name ? "?" + location.hash.split("?")[1] : ""));
      if (onShow) onShow(name);
    }
    const want = location.hash.slice(1).split("?")[0];
    const first = btns.find((b) => b.dataset.tab === want && ok(b)) || btns.find(ok);
    if (first) show(first.dataset.tab);
    return show;
  },

  /* Een formulier in een zijpaneel zetten. Bestaande code mag form.hidden blijven
     gebruiken: hidden = false opent het paneel, true sluit het. */
  dialogize(form, title) {
    const d = document.createElement("dialog");
    d.className = "side";
    d.setAttribute("aria-label", title || "Formulier");
    d.innerHTML = `<div class="dlghead"><h2></h2><button type="button" class="ghost" aria-label="Sluiten">✕</button></div><div class="dlgbody"></div>`;
    d.querySelector("h2").textContent = title || "";
    document.body.appendChild(d);
    d.querySelector(".dlgbody").appendChild(form);
    form.hidden = false;
    d.querySelector(".dlghead button").addEventListener("click", () => d.close());
    Object.defineProperty(form, "hidden", {
      configurable: true,
      get() { return !d.open; },
      set(v) { if (v) { if (d.open) d.close(); } else if (!d.open) d.showModal(); },
    });
    form.setTitle = (t) => { d.querySelector("h2").textContent = t; d.setAttribute("aria-label", t); };
    form.dialog = d;
    form.scrollIntoView = () => {};
    return d;
  },

  /* Bevestigen met een eigen venster. danger = rode knop. Geeft een Promise<boolean>. */
  confirm(text, { ok = "OK", danger = false, title = "Bevestigen" } = {}) {
    return new Promise((resolve) => {
      const d = document.createElement("dialog");
      d.className = "dlg";
      d.innerHTML = `<form method="dialog"><h2></h2><p class="cbody"></p>
        <div class="row"><button value="ok" class="${danger ? "danger" : "primary"}"></button><button value="no" type="submit">Annuleren</button></div></form>`;
      d.querySelector("h2").textContent = title;
      d.querySelector(".cbody").textContent = text;
      d.querySelector("button[value=ok]").textContent = ok;
      document.body.appendChild(d);
      d.addEventListener("close", () => { resolve(d.returnValue === "ok"); d.remove(); });
      d.showModal();
      d.querySelector("button[value=no]").focus();
    });
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
