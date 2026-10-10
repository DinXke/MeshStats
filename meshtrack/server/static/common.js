/* Gedeelde hulpjes voor kaart- en beheerpagina. */
const MT = {
  async api(path, opts = {}) {
    const r = await fetch(path, {
      ...opts,
      headers: opts.body ? { "Content-Type": "application/json" } : {},
      body: opts.body ? JSON.stringify(opts.body) : undefined,
    });
    if (r.status === 401) { location.href = MT.loginUrl(); throw new Error("niet ingelogd"); }
    if (r.status === 403 && !opts.quiet403) throw new Error("geen toegang");
    const j = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(j.detail || r.statusText);
    return j;
  },

  // Server 1.4.1: op_public (de laatste 24 u gezien op Public = niet klaargemaakt), public_leak_ts
  publicSeen(t) {
    if (!t || !t.public_leak_ts) return "";
    const d = new Date(t.public_leak_ts * 1000);
    const hm = d.toLocaleTimeString("nl-BE", { hour: "2-digit", minute: "2-digit" });
    return d.toDateString() === new Date().toDateString() ? hm : `${d.toLocaleDateString("nl-BE", { day: "numeric", month: "short" })} ${hm}`;
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

  STATE: { M: "rijdt/stapt", S: "stilgevallen", H: "levensteken", N: "geen GPS-fix", E: "SOS", B: "modus", P: "handmatig verstuurd", W: "wakker door beweging", L: "gelogd punt (SlowTrack)", Q: "ingehaald punt (FIFO)", V: "op verzoek" },
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
  // Vijf thema's, ook in /start, Offline-kaart en Tracker live (appnav.js, zelfde sleutel mt.theme).
  // "contrast" heet Zon (sleutel blijft); een oude keuze "ocean" valt terug op Automatisch.
  THEMES: { auto: "Automatisch", light: "Licht", dark: "Donker", night: "Nacht (rood)", contrast: "Zon (hoog contrast)" },
  THEME_SHORT: { auto: "Auto", light: "Licht", dark: "Donker", night: "Nacht", contrast: "Zon" },
  theme() { const t = MT.prefGet("theme", "auto"); return MT.THEMES[t] ? t : "auto"; },
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
  setTheme(t) {
    if (!MT.THEMES[t]) t = "auto";
    MT.prefSet("theme", t);
    if (t !== "night") { try { localStorage.setItem("mt.theme.day", t); } catch (_) { /* geen opslag */ } }
    MT.applyTheme(t);
    MT.drawThemes();
  },
  // Dag/Nacht in één tik: tussen het laatst gebruikte dagthema en Nacht.
  toggleNight() {
    let day = "auto";
    try { day = localStorage.getItem("mt.theme.day") || "auto"; } catch (_) { /* geen opslag */ }
    MT.setTheme(MT.theme() === "night" ? (MT.THEMES[day] && day !== "night" ? day : "auto") : "night");
  },
  /* Themakiezer: kleine voorbeelden (kleuren van elk thema, naam eronder), aria-pressed. */
  drawThemes() {
    const cur = MT.theme();
    document.querySelectorAll("#um-themes, #ms-themes").forEach((tb) => {
      tb.classList.add("thpick");
      tb.innerHTML = Object.entries(MT.THEMES).map(([k, v]) => `<button type="button" class="thsw" data-th="${k}" aria-pressed="${cur === k}" aria-label="${v}" title="${v}"><span class="thsw-p" aria-hidden="true"><i></i><b></b></span><span class="thsw-n" aria-hidden="true">${MT.THEME_SHORT[k]}</span></button>`).join("");
      tb.querySelectorAll("[data-th]").forEach((b) => b.addEventListener("click", () => MT.setTheme(b.dataset.th)));
    });
    document.querySelectorAll("[data-daynight]").forEach((b) => b.setAttribute("aria-pressed", String(cur === "night")));
  },
  can(perm) { return !!(MT.me && MT.me.perms.includes(perm)); },

  /* Aanmeldpagina met de weg terug (/login?next=<deze pagina>). */
  loginUrl() {
    const here = location.pathname + location.search + location.hash;
    return here && here !== "/" && !here.startsWith("/login") ? "/login?next=" + encodeURIComponent(here) : "/login";
  },

  /* Afmelden: bevestigen (offline-gegevens blijven), de bewaarde startkeuze vergeten. */
  async logout() {
    const ok = await MT.confirm("Je wordt afgemeld op dit toestel. Offline-kaarten, posities en chat van de offline-modus blijven bewaard.",
      { ok: "Afmelden", title: "Afmelden" });
    if (!ok) return;
    try { localStorage.removeItem("mt.start"); } catch (_) { /* geen opslag */ }
    try { await MT.api("/api/logout", { method: "POST" }); } catch (_) { /* toch naar de aanmeldpagina */ }
    location.href = "/login";
  },

  /* Kopbalk en navigatie (één app voor gsm, tablet en computer):
     - gsm (≤ 720 px): tabbalk onderaan (Kaart · Trackers · Tracker live · Meer) en een Meer-venster
       met de andere pagina's volgens de rechten, Offline, thema, help, installeren en afmelden;
     - breder: de pagina's in de kopbalk; wat niet past, schuift in "Meer ▾" (nooit horizontaal
       scrollen), plus de schakelaar Online | Offline en het gebruikersmenu. */
  async initHeader(active) {
    MT.me = await MT.api("/api/me");
    MT.applyTheme(MT.theme());
    try { if (MT.pwa.standalone()) sessionStorage.setItem("mt.inapp", "1"); } catch (_) { /* geen opslag */ }
    const allowed = (p) => !p || (Array.isArray(p) ? p.some(MT.can) : MT.can(p));
    const links = [
      ["/", "Kaart", "map.view"], ["/log", "Logboek", "log.view"], ["/kanalen", "Kanalen", "map.view"],
      ["/statistieken", "Statistieken", "map.view"],
      ["/admin", "Trackers", ["trackers.manage", "sims.manage"]],
      ["/devices", "Toestellen", "trackers.serial"],
      ["/users", "Gebruikers", ["users.manage", "share.manage"]],
      ["/system", "Systeem", ["alerts.manage", "alerts.personal", "system.manage", "companion.view"]],
    ].filter(([href, , p]) => allowed(p) && !(href === "/statistieken" && MT.me.kind !== "user"));
    const offline = [["/offline", "Offline-kaart"], ["/tracker", "Tracker live"], ["/start?kies=1", "Beginscherm"]];
    const esc = MT.esc;
    const a = ([href, label], cls = "") => `<a href="${href}"${href === active ? ' class="on" aria-current="page"' : (cls ? ` class="${cls}"` : "")}>${esc(label)}</a>`;
    const hdr = document.querySelector("header.top");
    const nav = hdr && hdr.querySelector("nav");
    const user = MT.me.kind === "user";

    // ---- breed scherm: kopbalk -------------------------------------------------------
    if (nav) {
      nav.setAttribute("aria-label", "Pagina's");
      nav.innerHTML = links.map((l) => a(l)).join("")
        + `<details class="navmore"><summary>Meer</summary><div class="menu" role="group" aria-label="Meer pagina's">
            <div class="navmore-over"></div>
            <div class="navmore-l">Offline</div>${offline.map((l) => a(l)).join("")}
            <div class="navmore-sep"></div>${a(["/help", "Help"])}</div></details>`;
      nav.insertAdjacentHTML("afterend", `<span class="modesw" role="group" aria-label="Online of offline">
        <a href="/" aria-current="true" title="Online: de volledige site (je bent hier)">Online</a><a href="/offline" title="Offline-kaart en Tracker live, zonder internet">Offline</a></span>`);
      const more = nav.querySelector(".navmore");
      const over = more.querySelector(".navmore-over");
      const items = [...nav.querySelectorAll(":scope > a")];
      // "Priority+": de laatste pagina's naar Meer tot de kopbalk past. De actieve pagina blijft zichtbaar.
      const fit = () => {
        items.forEach((x) => more.before(x));
        if (innerWidth <= 720) return;
        const tooWide = () => hdr.scrollWidth > hdr.clientWidth + 1;
        for (let i = items.length - 1; i > 0 && tooWide(); i--) {
          if (items[i].classList.contains("on") && i > 1) { over.prepend(items[i - 1]); i--; continue; }
          over.prepend(items[i]);
        }
      };
      MT._fitHeader = fit;
      let t = null;
      addEventListener("resize", () => { clearTimeout(t); t = setTimeout(fit, 80); });
      fit();
      if (document.fonts && document.fonts.ready) document.fonts.ready.then(fit);
      document.addEventListener("click", (e) => { if (more.open && !more.contains(e.target)) more.open = false; });
      more.addEventListener("keydown", (e) => { if (e.key === "Escape") { more.open = false; more.querySelector("summary").focus(); } });
    }
    document.querySelectorAll(".msg").forEach((m) => { if (!m.hasAttribute("aria-live")) m.setAttribute("aria-live", "polite"); });
    if (hdr && MT.me.kind === "share" && !document.getElementById("sharelogin")) {
      hdr.insertAdjacentHTML("beforeend", `<a id="sharelogin" class="btnlink" href="${MT.loginUrl()}">Aanmelden</a>`);
    }
    if (hdr && !document.getElementById("usermenu") && user) {
      hdr.insertAdjacentHTML("beforeend", `<details id="usermenu" class="usermenu"><summary title="${esc(MT.me.group)}" aria-label="Gebruikersmenu">
        <span class="avatar">${esc((MT.me.display || "?").slice(0, 1).toUpperCase())}</span></summary>
        <div class="menu"><div class="who"><strong>${esc(MT.me.display)}</strong><div class="muted small">${esc(MT.me.group)}</div></div>
        <div class="muted small">Thema</div><div class="themes" id="um-themes" role="group" aria-label="Thema"></div>
        <button type="button" class="daynight" data-daynight aria-pressed="false"><svg class="dn-ico" viewBox="0 0 24 24" aria-hidden="true"><path d="M20 14.5A8 8 0 019.5 4a8 8 0 1010.5 10.5z"/></svg><span>Nachtmodus (rood)</span></button>
        <button type="button" id="um-pw">Wachtwoord wijzigen</button><button type="button" id="um-out">Afmelden</button></div></details>`);
      document.getElementById("um-out").addEventListener("click", MT.logout);
      document.getElementById("um-pw").addEventListener("click", MT.passwordDialog);
    }

    // ---- gsm: tabbalk onderaan + Meer-venster ---------------------------------------------
    if (hdr && !document.getElementById("mnav")) {
      const has = (h) => links.some(([x]) => x === h);
      const ico = {
        map: '<path d="M9 4L3 6v14l6-2 6 2 6-2V4l-6 2-6-2zM9 4v14M15 6v14"/>',
        trk: '<circle cx="12" cy="10" r="3"/><path d="M12 21s-7-6.2-7-11a7 7 0 0114 0c0 4.8-7 11-7 11z"/>',
        log: '<path d="M4 6h16M4 12h16M4 18h10"/>',
        live: '<path d="M12 2v3M12 19v3M2 12h3M19 12h3"/><circle cx="12" cy="12" r="5"/><circle cx="12" cy="12" r="1.5"/>',
        more: '<circle cx="5" cy="12" r="1.6"/><circle cx="12" cy="12" r="1.6"/><circle cx="19" cy="12" r="1.6"/>',
      };
      const tab = (href, label, i) => `<a href="${href}"${href === active ? ' class="on" aria-current="page"' : ""}><svg viewBox="0 0 24 24" aria-hidden="true">${ico[i]}</svg><span>${esc(label)}</span></a>`;
      const second = has("/admin") ? ["/admin", "Trackers", "trk"] : has("/log") ? ["/log", "Logboek", "log"] : null;
      const inBar = ["/", second && second[0], "/tracker"].filter(Boolean);
      document.body.insertAdjacentHTML("beforeend", `<nav class="mnav" id="mnav" aria-label="Hoofdmenu">
        ${has("/") ? tab("/", "Kaart", "map") : ""}${second ? tab(...second) : ""}${tab("/tracker", "Tracker live", "live")}
        <button type="button" id="mnav-more" aria-haspopup="dialog" aria-controls="msheet"${!inBar.includes(active) ? ' class="on"' : ""}><svg viewBox="0 0 24 24" aria-hidden="true">${ico.more}</svg><span>Meer</span></button></nav>
        <dialog class="msheet" id="msheet" aria-labelledby="msheet-h">
          <div class="msheet-grip" aria-hidden="true"></div>
          ${user ? `<div class="msheet-who"><span class="avatar">${esc((MT.me.display || "?").slice(0, 1).toUpperCase())}</span><div><strong id="msheet-h">${esc(MT.me.display)}</strong><div class="muted small">${esc(MT.me.group)}</div></div></div>`
            : `<h2 class="msheet-h" id="msheet-h">Meer</h2>`}
          <div class="msheet-list">
            ${links.filter(([h]) => !inBar.includes(h)).length ? `<div class="msheet-l">Pagina's</div>${links.filter(([h]) => !inBar.includes(h)).map((l) => a(l)).join("")}` : ""}
            <div class="msheet-l">Offline</div>
            <a href="/offline" class="msheet-off">Naar offline-modus (Offline-kaart)</a>
            <a href="/start?kies=1">Beginscherm (Online of Offline)</a>
            <div class="msheet-l">Thema</div><div class="themes" id="ms-themes" role="group" aria-label="Thema"></div>
            <button type="button" class="daynight" data-daynight aria-pressed="false"><svg class="dn-ico" viewBox="0 0 24 24" aria-hidden="true"><path d="M20 14.5A8 8 0 019.5 4a8 8 0 1010.5 10.5z"/></svg><span>Nachtmodus (rood)</span></button>
            ${a(["/help", "Help"])}
            <button type="button" id="ms-install" hidden>Installeren</button>
            ${user ? '<button type="button" id="ms-pw">Wachtwoord wijzigen</button><button type="button" id="ms-out">Afmelden</button>'
              : `<a href="${MT.loginUrl()}">Aanmelden</a>`}
          </div>
          <button type="button" class="msheet-close" id="msheet-close">Sluiten</button>
        </dialog>`);
      document.body.classList.add("has-mnav");
      const sh = document.getElementById("msheet");
      document.getElementById("mnav-more").addEventListener("click", () => sh.showModal());
      document.getElementById("msheet-close").addEventListener("click", () => sh.close());
      sh.addEventListener("click", (e) => { if (e.target === sh) sh.close(); });          // tik naast het venster
      if (user) {
        document.getElementById("ms-out").addEventListener("click", () => { sh.close(); MT.logout(); });
        document.getElementById("ms-pw").addEventListener("click", () => { sh.close(); MT.passwordDialog(); });
      }
    }
    // Thema-knoppen (gebruikersmenu en Meer-venster)
    document.querySelectorAll("[data-daynight]").forEach((b) => b.addEventListener("click", () => MT.toggleNight()));
    MT.drawThemes();
    MT.pwa.ui();
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
        if (e.code === 4401) { location.href = MT.loginUrl(); return; }
        setTimeout(open, delay); delay = Math.min(delay * 2, 30000);
      };
    };
    open();
  },
};

/* ---- de ene MeshTrack-app (PWA): service worker, nieuwe versie, installeren -------------
   /sw.js (scope "/") bewaart het offline-deel (/start, /offline, /tracker) vooraf, plus scripts,
   stijl en iconen; nooit pagina's of gegevens van de online site. De vroegere losse workers van
   /offline en /tracker melden zich zelf af zodra deze klaar is; hier vragen we alleen hun update
   (zodat die laatste versie binnenkomt). Zelfde logica als appnav.js (offline-deel). */
MT.pwa = (() => {
  let prompt = null;                     // uitgesteld beforeinstallprompt-event
  const store = {
    get(k) { try { return localStorage.getItem(k); } catch (_) { return null; } },
    set(k, v) { try { localStorage.setItem(k, v); } catch (_) { /* geen opslag */ } },
  };
  const standalone = () => {
    try { return window.matchMedia("(display-mode: standalone)").matches || navigator.standalone === true; } catch (_) { return false; }
  };
  const ios = () => /iphone|ipad|ipod/i.test(navigator.userAgent)
    || (navigator.platform === "MacIntel" && navigator.maxTouchPoints > 1);     // iPadOS doet zich voor als Mac

  // "Installeren": in het gebruikersmenu, anders (deellink) in de kopbalk.
  function installButton() {
    let b = document.getElementById("mt-install");
    const want = !!prompt && !standalone();
    if (!b && want) {
      const menu = document.querySelector("#usermenu .menu");
      const hdr = document.querySelector("header.top");
      if (menu) {
        const pw = menu.querySelector("#um-pw");
        (pw || menu).insertAdjacentHTML(pw ? "beforebegin" : "beforeend", '<button type="button" id="mt-install">Installeren</button>');
      } else if (hdr) {
        hdr.insertAdjacentHTML("beforeend", '<button type="button" id="mt-install" class="hdrinstall">Installeren</button>');
      } else return;
      b = document.getElementById("mt-install");
      b.addEventListener("click", async () => {
        const p = prompt;
        if (!p) return;
        prompt = null;
        try { p.prompt(); await p.userChoice; } catch (_) { /* geweigerd of niet meer geldig */ }
        installButton();
      });
    }
    if (b) b.hidden = !want;
    const ms = document.getElementById("ms-install");          // Meer-venster (gsm)
    if (ms) {
      ms.hidden = !want;
      if (!ms.dataset.on) {
        ms.dataset.on = "1";
        ms.addEventListener("click", async () => {
          const p = prompt;
          if (!p) return;
          prompt = null;
          const sh = document.getElementById("msheet"); if (sh && sh.open) sh.close();
          try { p.prompt(); await p.userChoice; } catch (_) { /* geweigerd of niet meer geldig */ }
          installButton();
        });
      }
    }
  }

  // iPhone/iPad kent geen installeerknop: één keer uitleggen hoe het wel gaat.
  function iosHint() {
    if (!ios() || standalone() || store.get("mt.pwa.ioshint") || document.getElementById("mt-ioshint")) return;
    store.set("mt.pwa.ioshint", "1");
    document.body.insertAdjacentHTML("beforeend", `<div id="mt-ioshint" class="pwahint" role="note">
      <span>Installeer MeshTrack als app: tik op <strong>Deel</strong> en kies <strong>Zet op beginscherm</strong>.</span>
      <button type="button" class="ghost" aria-label="Sluiten">✕</button></div>`);
    const h = document.getElementById("mt-ioshint");
    h.querySelector("button").addEventListener("click", () => h.remove());
  }

  function offerUpdate(w) {
    if (!w || !navigator.serviceWorker.controller) return;       // eerste installatie: niets te melden
    let b = document.getElementById("mt-update");
    if (!b) {
      document.body.insertAdjacentHTML("beforeend",
        '<button type="button" id="mt-update" class="swupdate">Nieuwe versie beschikbaar – vernieuwen</button>');
      b = document.getElementById("mt-update");
    }
    b.hidden = false;
    b.onclick = () => { reloading = true; b.disabled = true; w.postMessage({ type: "skip" }); };
  }

  // Oude losse apps (/offline, /tracker met een eigen worker): update vragen zodat hun laatste,
  // afmeldende versie binnenkomt. Hun worker meldt zich pas af als /sw.js klaar is. Zijn ze al weg
  // maar staat hun cache er nog, dan ruimt /sw.js die op (na de lettertypes over te nemen).
  async function retireOld() {
    if (!navigator.serviceWorker.getRegistrations) return;
    const has = { offline: false, tracker: false };
    for (const r of await navigator.serviceWorker.getRegistrations()) {
      const scope = new URL(r.scope).pathname.replace(/\/$/, "");
      const app = scope === "/offline" ? "offline" : scope === "/tracker" ? "tracker" : null;
      if (!app) continue;
      has[app] = true;
      try { await r.update(); } catch (_) { /* geen netwerk: later */ }
      const w = r.active || r.waiting || r.installing;
      if (w) w.postMessage({ type: "retire-check" });
    }
    const keys = await caches.keys().catch(() => []);
    const left = { offline: !has.offline && keys.some((k) => k.startsWith("mt-offline-")), tracker: !has.tracker && keys.some((k) => k.startsWith("mt-tracker-")) };
    if (!left.offline && !left.tracker) return;
    const reg = await navigator.serviceWorker.ready;
    if (reg.active) reg.active.postMessage({ type: "cleanup-old", ...left });
  }

  let reloading = false;
  function register() {
    navigator.serviceWorker.register("/sw.js", { scope: "/" }).then((reg) => {
      if (reg.waiting) offerUpdate(reg.waiting);
      reg.addEventListener("updatefound", () => {
        const w = reg.installing;
        if (w) w.addEventListener("statechange", () => { if (w.state === "installed") offerUpdate(w); });
      });
      navigator.serviceWorker.ready.then(() => setTimeout(() => retireOld().catch(() => {}), 3000));
    }).catch(() => {});
  }

  if ("serviceWorker" in navigator && window.isSecureContext) {
    navigator.serviceWorker.addEventListener("controllerchange", () => { if (reloading) location.reload(); });
    if (document.readyState === "complete") register(); else window.addEventListener("load", register);
  }
  window.addEventListener("beforeinstallprompt", (e) => {
    e.preventDefault();                  // geen eigen balk van de browser: de knop staat in het menu
    prompt = e;
    installButton();
  });
  window.addEventListener("appinstalled", () => { prompt = null; installButton(); });

  return {
    ui() { installButton(); iosHint(); },
    standalone,
    canInstall: () => !!prompt && !standalone(),
  };
})();
