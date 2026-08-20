/* MeshMoni service worker.
 *
 * Twee taken, en met opzet niet meer:
 *
 * 1. De APP-SCHIL cachen: stylesheet, script, iconen. Dat is wat de PWA nodig
 *    heeft om als app te openen. DATA WORDT NOOIT GECACHET: een meting uit een
 *    verouderde cache is een leugen met een stellig gezicht, dus alles wat
 *    geen schil is gaat gewoon naar het netwerk en faalt daar eerlijk. De
 *    "laatst bijgewerkt"-stempel op elke pagina hoort bij die afspraak.
 *
 * 2. Pushberichten tonen. De payload komt versleuteld binnen en is de JSON die
 *    webpush.py opstelde: {title, body, url, ...}.
 *
 * De cache-naam draagt een versienummer: hoog het op als de schil verandert,
 * dan gooit activate de oude weg.
 */
"use strict";

var CACHE = "meshmoni-schil-v1";
var SCHIL = [
  "/static/style.css",
  "/static/meshmoni/moni.css",
  "/static/meshmoni/moni.js",
  "/static/meshmoni/icon.svg",
  "/static/meshmoni/icon-192.png",
  "/static/meshmoni/icon-512.png",
];

self.addEventListener("install", function (e) {
  e.waitUntil(caches.open(CACHE).then(function (c) { return c.addAll(SCHIL); })
    .then(function () { return self.skipWaiting(); }));
});

self.addEventListener("activate", function (e) {
  e.waitUntil(caches.keys().then(function (namen) {
    return Promise.all(namen.filter(function (n) { return n !== CACHE; })
      .map(function (n) { return caches.delete(n); }));
  }).then(function () { return self.clients.claim(); }));
});

self.addEventListener("fetch", function (e) {
  var url = new URL(e.request.url);
  var isSchil = url.origin === location.origin && SCHIL.indexOf(url.pathname) !== -1;
  if (e.request.method !== "GET" || !isSchil) return; // data: altijd het netwerk
  // Schil: cache eerst (de app moet openen zonder netwerk), en op de
  // achtergrond verversen zodat een deploy niet eeuwig oud blijft.
  e.respondWith(caches.match(e.request).then(function (uitCache) {
    var vers = fetch(e.request).then(function (antwoord) {
      if (antwoord.ok) {
        var kopie = antwoord.clone();
        caches.open(CACHE).then(function (c) { c.put(e.request, kopie); });
      }
      return antwoord;
    }).catch(function () { return uitCache; });
    return uitCache || vers;
  }));
});

self.addEventListener("push", function (e) {
  var data = {};
  try { data = e.data ? e.data.json() : {}; } catch (fout) { data = { body: e.data && e.data.text() }; }
  e.waitUntil(self.registration.showNotification(data.title || "MeshManager", {
    body: data.body || "",
    icon: "/static/meshmoni/icon-192.png",
    badge: "/static/meshmoni/icon-192.png",
    tag: "meshmoni-alert",       // nieuwe melding vervangt de vorige op het scherm
    renotify: true,              // maar trilt wel opnieuw: het is een nieuwe alert
    data: { url: data.url || "/meshmoni" },
  }));
});

self.addEventListener("notificationclick", function (e) {
  e.notification.close();
  var doel = (e.notification.data && e.notification.data.url) || "/meshmoni";
  e.waitUntil(self.clients.matchAll({ type: "window", includeUncontrolled: true }).then(function (vensters) {
    for (var i = 0; i < vensters.length; i++) {
      if (vensters[i].url.indexOf("/meshmoni") !== -1 && "focus" in vensters[i]) {
        vensters[i].navigate(doel);
        return vensters[i].focus();
      }
    }
    return self.clients.openWindow(doel);
  }));
});
