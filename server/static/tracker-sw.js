/* Service worker van de vroegere losse tracker-app (/tracker) – LAATSTE versie (afmelden).
   MeshTrack is nu één app: /sw.js (scope "/") bewaart /offline, /tracker en alles wat ze nodig
   hebben. Deze worker blijft de pagina bedienen zoals vroeger (netwerk eerst, anders de oude
   cache) tot de hoofdworker actief is én zijn vooraf bewaarde bestanden compleet zijn (merkteken
   /__mt-ready in een cache mt-app-*). Pas dan:
     1. lettertypes en kaartsymbolen overzetten naar de cache van de hoofdworker (mt-maps-v1);
     2. zichzelf afmelden;
     3. de eigen cache wissen.
   Nooit eerder: zonder internet zou /tracker anders niet meer openen.
   Gegevens van de app (IndexedDB "mt-offline", kaarten in OPFS, localStorage) horen bij de
   oorsprong, niet bij deze worker: die blijven onaangeroerd. */
const APP = "tracker";
const CACHE = "mt-tracker-v13";
const OWN = (p) => p === "/tracker" || p.startsWith("/static/") || p === "/tracker.webmanifest";
const MAPS = "mt-maps-v1";
const MAPASSET_RE = /^\/tiles\/(fonts|sprites)\//;

self.addEventListener("install", () => { self.skipWaiting(); });

async function mainReady() {
  for (const k of await caches.keys()) {
    if (!k.startsWith("mt-app-")) continue;
    const c = await caches.open(k);
    if (await c.match("/__mt-ready") && await c.match("/offline") && await c.match("/tracker")) return true;
  }
  return false;
}

let retiring = null;
function retire() {
  if (!retiring) {
    retiring = (async () => {
      if (!(await mainReady())) return false;
      const dst = await caches.open(MAPS);
      if (await caches.has(CACHE)) {
        const src = await caches.open(CACHE);
        for (const req of await src.keys()) {
          if (!MAPASSET_RE.test(new URL(req.url).pathname)) continue;
          if (await dst.match(req, { ignoreSearch: true })) continue;
          const r = await src.match(req);
          if (r) await dst.put(req, r);
        }
      }
      const ok = await self.registration.unregister();
      if (ok) {
        await caches.delete(CACHE);
        for (const cl of await self.clients.matchAll({ includeUncontrolled: true })) cl.postMessage({ type: "mt-old-sw-retired", app: APP });
      }
      return ok;
    })().catch(() => false).then((ok) => { if (!ok) retiring = null; return ok; });
  }
  return retiring;
}

self.addEventListener("activate", (e) => {
  e.waitUntil((async () => {
    await self.clients.claim();
    await retire();
  })());
});

self.addEventListener("message", (e) => {
  const d = e.data || {};
  if (d.type === "retire-check" || d.type === "prefetch" || d.type === "skip") e.waitUntil(retire());
});

self.addEventListener("fetch", (e) => {
  const url = new URL(e.request.url);
  if (e.request.method !== "GET" || url.origin !== location.origin) return;
  const p = url.pathname;
  if (e.request.mode === "navigate") e.waitUntil(retire());
  if (MAPASSET_RE.test(p)) {
    e.respondWith((async () => {
      const hit = await caches.match(e.request, { ignoreSearch: true });   // eigen cache of die van de hoofdworker
      if (hit) return hit;
      return fetch(e.request);
    })());
    return;
  }
  if (OWN(p)) {
    e.respondWith((async () => {
      try {
        const r = await fetch(e.request);
        if (r.ok && r.type === "basic" && !retiring && await caches.has(CACHE)) {
          const copy = r.clone();
          (await caches.open(CACHE)).put(e.request, copy).catch(() => {});
        }
        return r;
      } catch (_) {
        // Offline: elke bewaarde versie is beter dan niets (eigen cache of die van de hoofdworker).
        const hit = await caches.match(e.request) || await caches.match(e.request, { ignoreSearch: true })
          || (e.request.mode === "navigate" ? await caches.match("/" + APP) : null);
        if (hit) return hit;
        return new Response("Offline en nog niet bewaard. Open de app één keer met internet.", { status: 503, headers: { "Content-Type": "text/plain; charset=utf-8" } });
      }
    })());
  }
});
