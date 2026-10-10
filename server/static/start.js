/* Beginscherm (/start): Online of Offline kiezen.
   - /api/me met 3 s time-out (niet blind op navigator.onLine vertrouwen): aangemeld,
     aanmelden nodig (401) of geen verbinding. Zonder verbinding komt Offline bovenaan.
   - "Volgende keer meteen zo openen" bewaart de keuze in localStorage (mt.start = online|offline);
     terugdraaien via Meer → Beginscherm (/start?kies=1).
   Het doorsturen bij een bewaarde keuze "offline" en op een computer gebeurt al in start.html. */
(() => {
  const $ = (id) => document.getElementById(id);
  const store = MTApp.store;
  const q = new URLSearchParams(location.search);
  const choosing = q.has("kies");
  const keep = store.get("mt.start");
  let state = "busy";             // busy | in | login | none
  let me = null;

  MTApp.markInApp();
  MTApp.registerSW();

  $("st-keep").checked = !!keep;
  if (choosing && keep) {
    $("st-keep-note").hidden = false;
    $("st-keep-note").textContent = `Nu opent de app meteen ${keep === "online" ? "Online" : "Offline"}. Wil je dat niet meer? Haal het vinkje weg.`;
  }

  const fmtSize = (b) => b >= 1e9 ? `${(b / 1e9).toLocaleString("nl-BE", { maximumFractionDigits: 1 })} GB` : `${Math.round(b / 1e6)} MB`;

  function setOnline(cls, text) {
    const el = $("st-on-state");
    el.className = "st-state " + cls;
    el.textContent = text;
  }

  function order() {
    const cards = $("st-cards"), on = $("st-online"), off = $("st-offline");
    const offFirst = state === "none";
    if (offFirst && cards.firstElementChild !== off) cards.insertBefore(off, on);
    if (!offFirst && cards.firstElementChild !== on) cards.insertBefore(on, off);
    on.classList.toggle("first", !offFirst);
    off.classList.toggle("first", offFirst);
  }

  async function check() {
    state = "busy";
    setOnline("busy", "Verbinding met de server controleren…");
    const ctl = new AbortController();
    const t = setTimeout(() => ctl.abort(), 3000);
    try {
      const r = await fetch("/api/me", { cache: "no-store", credentials: "same-origin", signal: ctl.signal });
      if (r.status === 401 || r.status === 403) {
        state = "login";
        setOnline("warn", "Aanmelden nodig · server bereikbaar");
      } else if (r.ok) {
        me = await r.json().catch(() => ({}));
        state = "in";
        const who = me.kind === "share" ? "via een deellink" : `als ${me.display || me.name || "gebruiker"}`;
        setOnline("ok", `Aangemeld ${who} · server bereikbaar`);
      } else {
        throw new Error(String(r.status));
      }
    } catch (_) {
      state = "none";
      setOnline("warn", "Geen verbinding met de server. Tik om het opnieuw te proberen.");
    } finally {
      clearTimeout(t);
    }
    order();
    return state;
  }

  async function offlineInfo() {
    let n = 0, size = 0;
    try {
      const root = navigator.storage && navigator.storage.getDirectory ? await navigator.storage.getDirectory() : null;
      if (root) for await (const [name, h] of root.entries()) {
        if (!name.endsWith(".pmtiles")) continue;
        n++; size += (await h.getFile()).size;
      }
    } catch (_) { /* geen OPFS */ }
    let t = n ? `${n} ${n === 1 ? "kaart" : "kaarten"} op dit toestel · ${fmtSize(size)}` : "Nog geen offline-kaart op dit toestel";
    if (state === "none") t = (n ? t + " · " : "") + "Aanbevolen zonder netwerk";
    if (!navigator.bluetooth) t += MTApp.ios() ? ". Bluetooth werkt niet op een iPhone of iPad; je kunt wel kaarten bekijken." : ". Bluetooth werkt niet in deze browser; gebruik Chrome of Edge.";
    $("st-off-state").textContent = t;
  }

  function remember(v) {
    if ($("st-keep").checked) store.set("mt.start", v); else store.del("mt.start");
  }

  $("st-online").addEventListener("click", async (e) => {
    e.preventDefault();
    remember("online");
    if (state === "busy" || state === "none") await check();
    if (state === "in") location.href = "/";
    else if (state === "login") location.href = "/login?next=" + encodeURIComponent("/");
  });
  for (const id of ["st-offline-go", "st-go-map", "st-go-tracker"]) $(id).addEventListener("click", () => remember("offline"));
  $("st-keep").addEventListener("change", () => {
    if (!$("st-keep").checked) { store.del("mt.start"); $("st-keep-note").hidden = true; }
  });

  // Installeren (alleen als de browser het aanbiedt en de app nog niet geïnstalleerd is).
  const syncInstall = () => { $("st-install").hidden = !MTApp.canInstall(); };
  document.addEventListener("mt-installable", syncInstall);
  $("st-install").addEventListener("click", () => MTApp.install());
  if (MTApp.ios() && !MTApp.standalone()) $("st-ios").hidden = false;

  addEventListener("online", () => check().then(offlineInfo));
  addEventListener("offline", () => { state = "none"; setOnline("warn", "Geen verbinding met de server. Tik om het opnieuw te proberen."); order(); offlineInfo(); });

  (async () => {
    await check();
    await offlineInfo();
    // Bewaarde keuze "online" (en niet bewust aan het kiezen): meteen door als dat kan.
    if (!choosing && keep === "online") {
      if (state === "in") location.replace("/");
      else if (state === "login") location.replace("/login?next=" + encodeURIComponent("/"));
    }
  })();

  window.MTStart = { state: () => state, check };
})();
