/* Gedeeld door het beginscherm (/start) en het offline-deel (/offline, /tracker) van de ene
   MeshTrack-app. Werkt zonder account en zonder netwerk; laadt niets van de server.
   - service worker /sw.js (scope "/") registreren + melding "Nieuwe versie – vernieuwen";
   - de oude losse apps (/offline, /tracker met een eigen worker) laten afmelden zodra de
     hoofdworker klaar is, en hun lege resten opruimen;
   - installeren (beforeinstallprompt) voor de hoofdapp;
   - in een oude, apart geïnstalleerde /offline- of /tracker-app één keer uitleggen hoe je
     overstapt zonder je offline kaarten te verliezen. */
(() => {
  const store = {
    get(k) { try { return localStorage.getItem(k); } catch (_) { return null; } },
    set(k, v) { try { localStorage.setItem(k, v); } catch (_) { /* geen opslag */ } },
    del(k) { try { localStorage.removeItem(k); } catch (_) { /* geen opslag */ } },
  };
  const sess = {
    get(k) { try { return sessionStorage.getItem(k); } catch (_) { return null; } },
    set(k, v) { try { sessionStorage.setItem(k, v); } catch (_) { /* geen opslag */ } },
  };
  const standalone = () => {
    try { return matchMedia("(display-mode: standalone)").matches || navigator.standalone === true; } catch (_) { return false; }
  };
  const ios = () => /iphone|ipad|ipod/i.test(navigator.userAgent) || (navigator.platform === "MacIntel" && navigator.maxTouchPoints > 1);

  // ---- installeren -------------------------------------------------------------------------
  let installEvt = null;
  addEventListener("beforeinstallprompt", (e) => {
    e.preventDefault();
    installEvt = e;
    document.dispatchEvent(new CustomEvent("mt-installable", { detail: true }));
  });
  addEventListener("appinstalled", () => {
    installEvt = null;
    store.set("mt.app.installed", "1");
    document.dispatchEvent(new CustomEvent("mt-installable", { detail: false }));
  });
  async function install() {
    const p = installEvt;
    if (!p) return false;
    installEvt = null;
    try { p.prompt(); const r = await p.userChoice; return r && r.outcome === "accepted"; } catch (_) { return false; } finally {
      document.dispatchEvent(new CustomEvent("mt-installable", { detail: false }));
    }
  }

  // ---- service worker ----------------------------------------------------------------------
  let reloading = false;
  function offerUpdate(w, btn) {
    if (!w || !navigator.serviceWorker.controller) return;          // eerste installatie: niets te melden
    let b = btn || document.getElementById("mt-update");
    if (!b) {
      document.body.insertAdjacentHTML("beforeend", '<button type="button" id="mt-update" class="swupdate">Nieuwe versie beschikbaar – vernieuwen</button>');
      b = document.getElementById("mt-update");
    }
    b.hidden = false;
    b.onclick = () => { reloading = true; b.disabled = true; w.postMessage({ type: "skip" }); };
  }

  // Oude losse apps: hun worker kijkt zelf of de hoofdworker klaar is; wij vragen alleen een
  // update (zodat de laatste, afmeldende versie binnenkomt) en geven een duwtje. Is hun worker al
  // weg maar staat hun cache er nog, dan ruimt de hoofdworker die op (na de lettertypes over te nemen).
  async function retireOld() {
    if (!navigator.serviceWorker.getRegistrations) return;
    const regs = await navigator.serviceWorker.getRegistrations();
    const has = { offline: false, tracker: false };
    for (const r of regs) {
      const scope = new URL(r.scope).pathname.replace(/\/$/, "");
      const app = scope === "/offline" ? "offline" : scope === "/tracker" ? "tracker" : null;
      if (!app) continue;
      has[app] = true;
      try { await r.update(); } catch (_) { /* geen netwerk: later */ }
      const w = r.active || r.waiting || r.installing;
      if (w) w.postMessage({ type: "retire-check" });
    }
    if (has.offline && has.tracker) return;
    const keys = await caches.keys().catch(() => []);
    const left = { offline: !has.offline && keys.some((k) => k.startsWith("mt-offline-")), tracker: !has.tracker && keys.some((k) => k.startsWith("mt-tracker-")) };
    if (!left.offline && !left.tracker) return;
    const reg = await navigator.serviceWorker.ready;
    if (reg.active) reg.active.postMessage({ type: "cleanup-old", ...left });
  }

  function registerSW(opts = {}) {
    if (!("serviceWorker" in navigator) || !window.isSecureContext) return;
    navigator.serviceWorker.addEventListener("controllerchange", () => { if (reloading) location.reload(); });
    const go = () => navigator.serviceWorker.register("/sw.js", { scope: "/" }).then((reg) => {
      if (reg.waiting) offerUpdate(reg.waiting, opts.updateButton);
      reg.addEventListener("updatefound", () => {
        const w = reg.installing;
        if (w) w.addEventListener("statechange", () => { if (w.state === "installed") offerUpdate(w, opts.updateButton); });
      });
      navigator.serviceWorker.ready.then(() => setTimeout(() => retireOld().catch(() => {}), 1500));
    }).catch(() => {});
    if (document.readyState === "complete") go(); else addEventListener("load", go);
  }

  // ---- in de ene app? ----------------------------------------------------------------------
  // De nieuwe app start op /start (start_url) of een snelkoppeling via /start; het online deel
  // zet dezelfde vlag. Een oude losse app opent /offline of /tracker rechtstreeks, zonder vlag.
  function markInApp() { if (standalone()) sess.set("mt.inapp", "1"); }
  function oldInstall() {
    return standalone() && /^\/(offline|tracker)$/.test(location.pathname) && !sess.get("mt.inapp") && !new URLSearchParams(location.search).has("app");
  }

  function migrationBanner(where) {
    if (!oldInstall() || store.get("mt.migr.ok")) return;
    const box = document.createElement("div");
    box.className = "migrbanner";
    box.setAttribute("role", "region");
    box.setAttribute("aria-label", "MeshTrack is nu één app");
    box.innerHTML = `<p><strong>MeshTrack is nu één app.</strong> Installeer de nieuwe MeshTrack-app en verwijder daarna de oude.
      Vink bij het verwijderen <strong>NIET</strong> ‘ook gegevens wissen’ aan, anders verdwijnen je offline kaarten.</p>
      <p class="migrsub">${ios() ? "Op een iPhone of iPad heeft elk icoon zijn eigen opslag: kaarten en posities van deze app gaan niet mee naar de nieuwe app. Download je kaarten daar opnieuw. " : ""}Je posities, chat en kaarten blijven op dit toestel; de nieuwe app gebruikt ze meteen.</p>
      <div class="migrbtns"><button type="button" class="primary" data-act="install" hidden>MeshTrack installeren</button>
      <a class="btnlike" data-act="open" href="/start?kies=1" target="_blank" rel="noopener">Nieuwe app openen</a>
      <button type="button" data-act="ok">Begrepen</button></div>`;
    (where || document.body.firstElementChild).insertAdjacentElement("afterend", box);
    const ib = box.querySelector('[data-act="install"]');
    const sync = () => { ib.hidden = !installEvt; };
    sync();
    document.addEventListener("mt-installable", sync);
    ib.addEventListener("click", install);
    box.querySelector('[data-act="ok"]').addEventListener("click", () => { store.set("mt.migr.ok", "1"); box.remove(); dispatchEvent(new Event("resize")); });
    dispatchEvent(new Event("resize"));
  }

  // ---- thema: dezelfde vijf als op de online site, zelfde sleutel (mt.theme, JSON) -------------
  // "contrast" heet nu Zon (sleutel blijft, zo werken bewaarde keuzes); "ocean" bestaat niet meer -> auto.
  const THEMES = { auto: ["Auto", "Automatisch"], light: ["Licht", "Licht"], dark: ["Donker", "Donker"], night: ["Nacht", "Nacht (rood)"], contrast: ["Zon", "Zon (hoog contrast)"] };
  const theme = {
    names: THEMES,
    get() { let t = "auto"; try { t = JSON.parse(store.get("mt.theme") || '"auto"'); } catch (_) { /* oud formaat */ } return THEMES[t] ? t : "auto"; },
    isDark() { const t = theme.get(); return t === "dark" || t === "night" || (t === "auto" && matchMedia("(prefers-color-scheme: dark)").matches); },
    apply(t, save = true) {
      if (!THEMES[t]) t = "auto";
      if (t === "auto") document.documentElement.removeAttribute("data-theme"); else document.documentElement.setAttribute("data-theme", t);
      if (save) { store.set("mt.theme", JSON.stringify(t)); if (t !== "night") store.set("mt.theme.day", t); }
      document.querySelectorAll(".thpick [data-th]").forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.th === t)));
      document.querySelectorAll("[data-daynight]").forEach((b) => b.setAttribute("aria-pressed", String(t === "night")));
      document.dispatchEvent(new CustomEvent("mt-theme", { detail: t }));
    },
    // Dag/Nacht in één tik: tussen het laatst gebruikte dagthema en Nacht.
    toggleNight() {
      const day = store.get("mt.theme.day");
      theme.apply(theme.get() === "night" ? (THEMES[day] && day !== "night" ? day : "auto") : "night");
    },
    swatches(cur) {
      return Object.entries(THEMES).map(([k, [short, full]]) => `<button type="button" class="thsw" data-th="${k}" aria-pressed="${k === cur}" aria-label="${full}" title="${full}"><span class="thsw-p" aria-hidden="true"><i></i><b></b></span><span class="thsw-n" aria-hidden="true">${short}</span></button>`).join("");
    },
    picker(el) {
      if (!el) return;
      el.classList.add("thpick");
      el.innerHTML = theme.swatches(theme.get());
      el.querySelectorAll("[data-th]").forEach((b) => b.addEventListener("click", () => theme.apply(b.dataset.th)));
    },
  };
  document.addEventListener("click", (e) => { const b = e.target.closest("[data-daynight]"); if (b) theme.toggleNight(); });
  theme.apply(theme.get(), false);      // oude/ongeldige waarde (bv. "ocean") meteen rechtzetten op het scherm
  matchMedia("(prefers-color-scheme: dark)").addEventListener("change", () => { if (theme.get() === "auto") document.dispatchEvent(new CustomEvent("mt-theme", { detail: "auto" })); });

  window.MTApp = {
    theme,
    store, standalone, ios, install, registerSW, markInApp, oldInstall, migrationBanner, retireOld,
    canInstall: () => !!installEvt && !standalone(),
  };
})();
