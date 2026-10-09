/* Service worker van de volledige MeshTrack-site (scope "/").
   - Pagina's (HTML): altijd van het netwerk, nooit bewaard (ze horen bij een aangemelde gebruiker).
     Lukt het netwerk niet, dan komt de algemene pagina "Geen verbinding met de server"
     (offline-site.html, zonder gegevens).
   - Scripts, stijl, iconen, MapLibre (/static): uit de cache. Met ?v=<versie> cache eerst
     (elke versie heeft een eigen URL), zonder ?v netwerk eerst.
   - Nooit bewaard: /api, /ws, /tiles (pmtiles vraagt stukken met Range), /login, /logout,
     deellinks (/s/...), firmware en pdf's.
   - /offline en /tracker hebben hun eigen service worker (smallere scope). Hun pagina's en de
     bestanden die ze laden, laat deze worker volledig met rust.
   BUILD en ASSETS vult de server in (zie main.py, site_sw): elke wijziging aan een bestand
   geeft een nieuwe BUILD, en de pagina toont dan "Nieuwe versie beschikbaar – vernieuwen". */
const BUILD = "dev";
const ASSETS = [];
const PREFIX = "mt-site-v1";
const CACHE = PREFIX + "-" + BUILD;
const FALLBACK = "/offline-site.html";

// Pagina's en bestanden van de losse apps: niet aankomen.
const OWN_APP = /^\/(offline|tracker)(\/|$|\.webmanifest$|-sw\.js$)/;
const NEVER = /^\/(api\/|ws$|tiles\/|login$|logout$|s\/|sw\.js$|static\/firmware\/)|\.pdf$/;

self.addEventListener("install", (e) => {
  e.waitUntil((async () => {
    const c = await caches.open(CACHE);
    // Niet alles-of-niets: één ontbrekend bestand mag de installatie niet tegenhouden.
    await Promise.all([FALLBACK, ...ASSETS].map((u) => c.add(new Request(u, { cache: "reload" })).catch(() => {})));
    // Eerste installatie: meteen actief. Een update wacht tot de gebruiker "vernieuwen" kiest.
    if (!self.registration.active) self.skipWaiting();
  })());
});

self.addEventListener("activate", (e) => {
  e.waitUntil((async () => {
    // Alleen eigen oude caches opruimen; mt-offline-* en mt-tracker-* blijven staan.
    for (const k of await caches.keys()) if (k.startsWith("mt-site-") && k !== CACHE) await caches.delete(k);
    await self.clients.claim();
  })());
});

self.addEventListener("message", (e) => {
  if (e.data && e.data.type === "skip") self.skipWaiting();
});

// Alleen gewone, statische antwoorden van deze site bewaren: nooit HTML, nooit iets met een cookie.
function storable(r) {
  if (!r || !r.ok || r.type !== "basic" || r.status !== 200) return false;
  if (r.headers.has("set-cookie")) return false;
  const ct = (r.headers.get("content-type") || "").toLowerCase();
  return !ct.startsWith("text/html") && !ct.includes("json");
}

async function fromStatic(req, url) {
  const c = await caches.open(CACHE);
  const versioned = url.searchParams.has("v");
  if (versioned) {
    const hit = await c.match(req);
    if (hit) return hit;
  }
  try {
    const r = await fetch(req);
    if (storable(r)) {
      const copy = r.clone();
      // Oudere versies van hetzelfde bestand weggooien, dan de nieuwe bewaren.
      for (const k of await c.keys()) {
        const ku = new URL(k.url);
        if (ku.pathname === url.pathname && ku.search !== url.search) await c.delete(k);
      }
      await c.put(req, copy);
    }
    return r;
  } catch (err) {
    // Geen netwerk: elke bewaarde versie is beter dan niets.
    const hit = await c.match(req) || await c.match(req, { ignoreSearch: true });
    if (hit) return hit;
    throw err;
  }
}

async function page(req) {
  try {
    return await fetch(req);
  } catch (err) {
    const c = await caches.open(CACHE);
    const hit = await c.match(FALLBACK, { ignoreSearch: true });
    if (hit) return hit;
    throw err;
  }
}

self.addEventListener("fetch", (e) => {
  const req = e.request;
  if (req.method !== "GET") return;
  const url = new URL(req.url);
  if (url.origin !== location.origin) return;
  const p = url.pathname;
  if (OWN_APP.test(p)) return;
  // Pagina's (ook /login): netwerk; page() bewaart nooit iets, het vangt alleen een netwerkfout op.
  if (req.mode === "navigate") { e.respondWith(page(req)); return; }
  if (NEVER.test(p)) return;                                    // gewoon netwerk, niets bewaren
  // Bestanden die een pagina van /offline of /tracker laadt (alleen als hun eigen worker er
  // nog niet staat, komen die hier binnen): ook met rust laten.
  if (req.referrer) {
    try { if (OWN_APP.test(new URL(req.referrer).pathname)) return; } catch (_) {}
  }
  if (p.startsWith("/static/")) e.respondWith(fromStatic(req, url));
  // Al de rest (bv. /manifest.webmanifest): gewoon netwerk.
});
