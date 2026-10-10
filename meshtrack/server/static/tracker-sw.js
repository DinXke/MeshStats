/* Service worker van de MeshTrack tracker-app (/tracker).
   - De app zelf (HTML, scripts, stijl): netwerk eerst, anders uit de cache, zodat ze ook
     zonder internet opent en na een update de nieuwe versie neemt.
   - Lettertypes en kaartsymbolen (/tiles/fonts, /tiles/sprites): cache eerst.
   - Kaarttegels staan als bestand op het toestel (OPFS, gedeeld met /offline), niet in deze cache.
   Nieuwe versie: CACHE ophogen. De pagina toont dan "Nieuwe versie beschikbaar – vernieuwen". */
const CACHE = "mt-tracker-v12";
const SHELL = ["/tracker", "/tracker.webmanifest", "/static/icon-192.png", "/static/icon-512.png", "/static/favicon.svg",
  "/static/style.css", "/static/tracker.css", "/static/tracker.js", "/static/vendor/maplibre-gl.css",
  "/static/vendor/maplibre-gl.js", "/static/vendor/pmtiles.js", "/static/basemap.js"];
const GLYPHS = ["Noto Sans Regular", "Noto Sans Medium", "Noto Sans Italic"];
const RANGES = ["0-255", "256-511", "512-767", "7680-7935", "8192-8447"];

self.addEventListener("install", (e) => {
  e.waitUntil((async () => {
    const c = await caches.open(CACHE);
    await Promise.all(SHELL.map((u) => c.add(u).catch(() => {})));
    // Eerste installatie: meteen actief. Een update wacht tot de gebruiker "vernieuwen" kiest.
    if (!self.registration.active) self.skipWaiting();
  })());
});

self.addEventListener("activate", (e) => {
  e.waitUntil((async () => {
    // Alleen eigen oude caches opruimen; die van de offline-app (mt-offline-*) blijven staan.
    for (const k of await caches.keys()) if (k.startsWith("mt-tracker-") && k !== CACHE) await caches.delete(k);
    await self.clients.claim();
  })());
});

self.addEventListener("message", (e) => {
  if (!e.data) return;
  if (e.data.type === "skip") { self.skipWaiting(); return; }
  if (e.data.type !== "prefetch") return;
  // Lettertypes en sprites alvast ophalen (terwijl er nog internet is).
  e.waitUntil((async () => {
    const c = await caches.open(CACHE);
    const urls = [];
    for (const f of GLYPHS) for (const r of RANGES) urls.push(`/tiles/fonts/${encodeURIComponent(f)}/${r}.pbf`);
    for (const t of ["light", "dark"]) for (const x of ["", "@2x"]) for (const ext of [".json", ".png"]) urls.push(`/tiles/sprites/v4/${t}${x}${ext}`);
    for (const u of urls) {
      if (await c.match(u)) continue;
      try { const r = await fetch(u, { credentials: "same-origin" }); if (r.ok) await c.put(u, r); } catch (_) {}
    }
  })());
});

self.addEventListener("fetch", (e) => {
  const url = new URL(e.request.url);
  if (e.request.method !== "GET" || url.origin !== location.origin) return;
  const p = url.pathname;
  if (p.startsWith("/tiles/fonts/") || p.startsWith("/tiles/sprites/")) {
    e.respondWith((async () => {
      const c = await caches.open(CACHE);
      const hit = await c.match(e.request, { ignoreSearch: true });
      if (hit) return hit;
      const r = await fetch(e.request);
      if (r.ok) c.put(e.request, r.clone());
      return r;
    })());
    return;
  }
  if (p === "/tracker" || p.startsWith("/static/") || p === "/tracker.webmanifest") {
    e.respondWith((async () => {
      const c = await caches.open(CACHE);
      try {
        const r = await fetch(e.request);
        if (r.ok && r.type === "basic") c.put(e.request, r.clone());
        return r;
      } catch (_) {
        // Scripts komen met ?v=<versie>; offline is elke bewaarde versie beter dan niets.
        const hit = await c.match(e.request) || await c.match(e.request, { ignoreSearch: true });
        if (hit) return hit;
        return new Response("Offline en nog niet bewaard. Open de app één keer met internet.", { status: 503, headers: { "Content-Type": "text/plain; charset=utf-8" } });
      }
    })());
  }
});
