/* MeshChat service worker: maakt de pagina installeerbaar (PWA) en offline bruikbaar,
   maar haalt ALTIJD eerst het netwerk zodat een nieuwe versie direct doorkomt.
   Alleen als het netwerk faalt, komt de laatst gecachte versie. */
const CACHE = 'meshchat-v1';
self.addEventListener('install', (e) => { self.skipWaiting(); });
self.addEventListener('activate', (e) => { e.waitUntil(self.clients.claim()); });
self.addEventListener('fetch', (e) => {
  const req = e.request;
  if (req.method !== 'GET' || new URL(req.url).origin !== self.location.origin) return;
  e.respondWith((async () => {
    try {
      const res = await fetch(req, { cache: 'no-store' });
      if (res && res.ok) { const c = await caches.open(CACHE); c.put(req, res.clone()).catch(() => {}); }
      return res;
    } catch (err) {
      const c = await caches.open(CACHE);
      return (await c.match(req, { ignoreSearch: true })) || (req.mode === 'navigate' ? (await c.match('./', { ignoreSearch: true })) : null) || Response.error();
    }
  })());
});
