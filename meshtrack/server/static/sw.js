/* Service worker van MeshTrack: één app voor de volledige site én het offline-deel (scope "/").
   - Offline-deel (/start, /offline, /tracker, "Geen verbinding" en alles wat die pagina's laden):
     vooraf bewaard bij de installatie. Pagina's netwerk eerst (hoogstens 3 s wachten), anders
     uit de cache; zo openen ze altijd, ook zonder bereik.
   - Pagina's van de online site: altijd van het netwerk, nooit bewaard (ze horen bij een
     aangemelde gebruiker). Lukt het netwerk niet, dan de pagina "Geen verbinding"
     (offline-site.html, zonder gegevens) met de weg naar Offline.
   - Scripts, stijl, iconen, MapLibre (/static): met ?v=<versie> cache eerst (elke versie heeft
     een eigen URL), zonder ?v netwerk eerst.
   - Lettertypes en kaartsymbolen (/tiles/fonts, /tiles/sprites): cache eerst, in een eigen cache
     die updates overleeft (mt-maps-v1). Ze worden overgenomen uit de caches van de oude losse
     apps (mt-offline-*, mt-tracker-*) vóór die verdwijnen.
   - Nooit bewaard: /api, /ws, de rest van /tiles (pmtiles vraagt stukken met Range; offline-kaarten
     staan als bestand op het toestel, OPFS), /login, /logout, deellinks (/s/...), firmware en pdf's.
   - Na een volledige installatie zet de worker bij het activeren een merkteken (/__mt-ready) in
     zijn cache. De oude workers van /offline en /tracker kijken daarnaar: pas dan melden ze zich af.
   BUILD, ASSETS en PAGES_V vult de server in (zie main.py, site_sw): elke wijziging aan een bestand
   geeft een nieuwe BUILD, en de pagina toont dan "Nieuwe versie beschikbaar – vernieuwen". */
const BUILD = "dev";
const ASSETS = [];
const PAGES_V = "";
const PREFIX = "mt-app";
const CACHE = PREFIX + "-" + BUILD;
const MAPS = "mt-maps-v1";                       // lettertypes + sprites, los van de BUILD
const FALLBACK = "/offline-site.html";
const READY = "/__mt-ready";

// Pagina's van het offline-deel: vooraf bewaard, openen ook zonder netwerk.
const SHELL = ["/start", "/offline", "/tracker", FALLBACK];
// Zonder deze bestanden werkt het offline-deel niet: de installatie mislukt dan (en wordt later
// opnieuw geprobeerd) in plaats van een half werkende app achter te laten.
const CRITICAL = /^\/static\/(style\.css|offline\.js|tracker\.js|tracker\.css|appnav\.js|start\.js|tabs\.js|basemap\.js|vendor\/(maplibre-gl\.js|maplibre-gl\.css|pmtiles\.js))$/;
const EXTRA = ["/manifest.webmanifest"];
const GLYPHS = ["Noto Sans Regular", "Noto Sans Medium", "Noto Sans Italic"];
const RANGES = ["0-255", "256-511", "512-767", "7680-7935", "8192-8447"];
function mapAssetUrls() {
  const urls = [];
  for (const f of GLYPHS) for (const r of RANGES) urls.push(`/tiles/fonts/${encodeURIComponent(f)}/${r}.pbf`);
  for (const t of ["light", "dark"]) for (const x of ["", "@2x"]) for (const ext of [".json", ".png"]) urls.push(`/tiles/sprites/v4/${t}${x}${ext}`);
  return urls;
}
const SHELL_RE = /^\/(start|offline|tracker)$/;
const MAPASSET_RE = /^\/tiles\/(fonts|sprites)\//;
const NEVER = /^\/(api\/|ws$|tiles\/|login$|logout$|s\/|sw\.js$|offline-sw\.js$|tracker-sw\.js$|static\/firmware\/)|\.pdf$/;

// Lettertypes en sprites uit de caches van de oude losse apps overnemen (alleen wat nog ontbreekt).
async function adoptMapAssets() {
  const dst = await caches.open(MAPS);
  for (const k of await caches.keys()) {
    if (!k.startsWith("mt-offline-") && !k.startsWith("mt-tracker-")) continue;
    const src = await caches.open(k);
    for (const req of await src.keys()) {
      if (!MAPASSET_RE.test(new URL(req.url).pathname)) continue;
      if (await dst.match(req, { ignoreSearch: true })) continue;
      const r = await src.match(req);
      if (r) await dst.put(req, r);
    }
  }
}

async function fetchMapAssets() {
  const c = await caches.open(MAPS);
  let ok = 0;
  const urls = mapAssetUrls();
  for (const u of urls) {
    if (await c.match(u)) { ok++; continue; }
    try { const r = await fetch(u, { credentials: "same-origin" }); if (r.ok) { await c.put(u, r); ok++; } } catch (_) { /* later opnieuw */ }
  }
  return { ok, total: urls.length };
}

self.addEventListener("install", (e) => {
  e.waitUntil((async () => {
    const c = await caches.open(CACHE);
    const critical = [...SHELL, ...ASSETS.filter((u) => CRITICAL.test(new URL(u, location.origin).pathname))];
    const rest = [...ASSETS.filter((u) => !critical.includes(u)), ...EXTRA];
    // Offline-deel: alles of niets (een fout laat de installatie mislukken).
    await Promise.all(critical.map(async (u) => {
      const r = await fetch(new Request(u, { cache: "reload", credentials: "same-origin" }));
      if (!r.ok || r.redirected) throw new Error(`niet bewaard: ${u} (${r.status})`);
      await c.put(u, r);
    }));
    // De rest van de site: niet alles-of-niets.
    await Promise.all(rest.map((u) => c.add(new Request(u, { cache: "reload" })).catch(() => {})));
    await adoptMapAssets().catch(() => {});
    await fetchMapAssets().catch(() => {});
    // Eerste installatie: meteen actief. Een update wacht tot de gebruiker "vernieuwen" kiest.
    if (!self.registration.active) self.skipWaiting();
  })());
});

self.addEventListener("activate", (e) => {
  e.waitUntil((async () => {
    // Eigen oude versies opruimen (en de cache van de vroegere site-worker, mt-site-*).
    // mt-offline-* en mt-tracker-* NIET: die ruimen de oude workers zelf op als ze zich afmelden.
    for (const k of await caches.keys()) {
      if ((k.startsWith(PREFIX + "-") && k !== CACHE) || k.startsWith("mt-site-")) await caches.delete(k);
    }
    const c = await caches.open(CACHE);
    await c.put(READY, new Response(JSON.stringify({ build: BUILD, pages: PAGES_V, ts: Date.now() }), { headers: { "Content-Type": "application/json" } }));
    await self.clients.claim();
  })());
});

self.addEventListener("message", (e) => {
  const d = e.data || {};
  if (d.type === "skip") { self.skipWaiting(); return; }
  // Na het downloaden van een kaart (offline.js): lettertypes en sprites zeker bewaren.
  if (d.type === "prefetch") {
    e.waitUntil((async () => {
      await adoptMapAssets().catch(() => {});
      const res = await fetchMapAssets();
      if (e.source && e.source.postMessage) e.source.postMessage({ type: "prefetched", ...res });
    })());
    return;
  }
  // Oude losse apps zijn al weg (geen registratie meer voor /offline of /tracker): hun
  // caches nog opruimen, maar eerst de lettertypes en sprites overnemen.
  if (d.type === "cleanup-old") {
    e.waitUntil((async () => {
      await adoptMapAssets();
      for (const k of await caches.keys()) {
        if ((d.offline && k.startsWith("mt-offline-")) || (d.tracker && k.startsWith("mt-tracker-"))) await caches.delete(k);
      }
    })().catch(() => {}));
  }
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
    // Geen netwerk: elke bewaarde versie is beter dan niets (ook uit een oude cache).
    const hit = await c.match(req) || await c.match(req, { ignoreSearch: true }) || await caches.match(req, { ignoreSearch: true });
    if (hit) return hit;
    throw err;
  }
}

async function mapAsset(req) {
  const c = await caches.open(MAPS);
  const hit = await c.match(req, { ignoreSearch: true }) || await caches.match(req, { ignoreSearch: true });
  if (hit) return hit;
  const r = await fetch(req);
  if (r.ok && r.type === "basic") c.put(req, r.clone());
  return r;
}

function timeout(ms) { return new Promise((_, rej) => setTimeout(() => rej(new Error("time-out")), ms)); }

// Offline-deel: netwerk eerst (max. 3 s), anders de vooraf bewaarde pagina. Het netwerkantwoord
// wordt NIET in de cache gezet: die volgt alleen de BUILD, zodat pagina en scripts bij elkaar passen.
async function shell(req, path) {
  try {
    const r = await Promise.race([fetch(req), timeout(3000)]);
    if (r && r.ok && !r.redirected) return r;
    throw new Error("geen goed antwoord");
  } catch (err) {
    const c = await caches.open(CACHE);
    const hit = await c.match(path);
    if (hit) return hit;
    try { return await fetch(req); } catch (_) { /* ook dat lukt niet */ }
    const fb = await c.match(FALLBACK);
    if (fb) return fb;
    throw err;
  }
}

// Online pagina's: netwerk; bewaart nooit iets, vangt alleen een netwerkfout op.
async function page(req) {
  try {
    return await fetch(req);
  } catch (err) {
    const c = await caches.open(CACHE);
    const hit = await c.match(FALLBACK, { ignoreSearch: true }) || await caches.match(FALLBACK, { ignoreSearch: true });
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
  if (req.mode === "navigate") {
    if (SHELL_RE.test(p)) { e.respondWith(shell(req, p)); return; }
    e.respondWith(page(req));
    return;
  }
  if (MAPASSET_RE.test(p)) { e.respondWith(mapAsset(req)); return; }
  if (NEVER.test(p)) return;                                    // gewoon netwerk, niets bewaren
  if (p.startsWith("/static/")) { e.respondWith(fromStatic(req, url)); return; }
  if (p === "/manifest.webmanifest") {
    e.respondWith(fetch(req).catch(async () => (await caches.match(p)) || Response.error()));
  }
  // Al de rest: gewoon netwerk.
});
