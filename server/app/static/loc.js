/* Publieke deel-pagina van een companion (/loc/<token>). Alleen-lezen: een
 * marker op de laatste positie plus een polyline van het recente spoor, met een
 * venster-keuze (1u/6u/24u/7d) die alleen het spoor herlaadt.
 *
 * Geen login en geen afhankelijkheid van app.js's interne helpers -- dit bestand
 * rekent de ouderdom zelf uit en praat alleen met de publieke track-JSON. Zelfde
 * lijn als companions_map.js: vanilla JS, één IIFE, geen build.
 *
 * De EERSTE tekening leunt op window.LOC_LAST (de laatste positie, door de
 * server meegegeven zodat de kaart er meteen staat) en haalt daarna het spoor op
 * voor het standaardvenster. Een klik op een venster-knop herlaadt uitsluitend
 * de polyline -- niet de kaart -- zodat de pan/zoom van de bezoeker blijft staan.
 */
(function () {
  "use strict";
  var token = window.LOC_TOKEN || "";
  var currentWindow = window.LOC_DEFAULT_WINDOW || "24h";
  var last = window.LOC_LAST || null;   // [lat, lon] of null
  var mapEl = document.getElementById("loc-map");

  var THEME = document.documentElement.getAttribute("data-theme") === "light" ? "light" : "dark";
  var TILE_URL = "https://{s}.basemaps.cartocdn.com/" +
    (THEME === "light" ? "light_all" : "dark_all") + "/{z}/{x}/{y}{r}.png";

  function trackUrl() {
    return "/loc/" + encodeURIComponent(token) + "/track.json?window=" +
      encodeURIComponent(currentWindow);
  }

  // Zonder locatie tekent de server geen #loc-map (alleen de uitlegtekst). Zodra
  // er tijdens het kijken alsnog een EERSTE positie binnenkomt, is een volledige
  // herlading eenvoudiger en betrouwbaarder dan de kaart hier alsnog met tegels
  // en legenda optuigen -- dezelfde keuze als companions_map.js.
  if (!mapEl || typeof L === "undefined") {
    if (!window.fetch || !token) return;
    var poller = setInterval(function () {
      fetch(trackUrl(), { credentials: "same-origin" })
        .then(function (r) { return r.json(); })
        .then(function (data) {
          if ((data.points || []).length) {
            clearInterval(poller);
            location.reload();
          }
        })
        .catch(function () { /* volgende tik probeert het opnieuw */ });
    }, 30000);
    return;
  }

  var map = L.map(mapEl, { scrollWheelZoom: false });
  L.tileLayer(TILE_URL, {
    attribution: "&copy; OpenStreetMap &copy; CARTO", maxZoom: 19,
  }).addTo(map);

  if (last && last.length === 2) {
    map.setView([last[0], last[1]], 14);
  } else {
    map.setView([50.9, 5.3], 8);   // grof: Limburg/BE, tot het eerste punt er is
  }

  // Eén laag voor marker + polyline samen, zodat een venster-wissel hem in zijn
  // geheel kan vervangen zonder de kaart-view aan te raken -- net als de
  // markerLayer op de beheerkaart.
  var trackLayer = null;

  function ageText(iso) {
    if (!iso) return "tijdstip onbekend";
    var d = new Date(iso);
    if (isNaN(d.getTime())) return "tijdstip onbekend";
    var s = Math.round((Date.now() - d.getTime()) / 1000);
    if (s < 0) s = 0;
    if (s < 60) return "zojuist";
    if (s < 3600) return Math.round(s / 60) + " min geleden";
    if (s < 86400) return Math.round(s / 3600) + " uur geleden";
    return Math.round(s / 86400) + " dag(en) geleden";
  }

  function draw(points) {
    var layer = L.layerGroup();
    var latlngs = (points || []).map(function (p) { return [p[0], p[1]]; });
    if (latlngs.length > 1) {
      L.polyline(latlngs, { color: "#3aa76d", weight: 3, opacity: 0.8 }).addTo(layer);
    }
    // De marker staat op het NIEUWSTE spoorpunt als er een spoor is, anders op
    // de door de server meegegeven laatste positie.
    var here = latlngs.length ? latlngs[latlngs.length - 1] : last;
    if (here && here.length === 2) {
      var ts = points && points.length ? points[points.length - 1][2] : null;
      var iso = ts ? new Date(ts * 1000).toISOString() : null;
      L.marker(here).bindPopup(ageText(iso)).addTo(layer);
    }
    if (trackLayer) map.removeLayer(trackLayer);
    trackLayer = layer;
    trackLayer.addTo(map);
  }

  function load() {
    if (!token || !window.fetch) { draw([]); return; }
    fetch(trackUrl(), { credentials: "same-origin" })
      .then(function (r) { return r.json(); })
      .then(function (data) { draw(data.points || []); })
      .catch(function () { draw([]); });
  }

  // De venster-knoppen: kies er een, en alleen het spoor herlaadt.
  var windows = document.getElementById("loc-windows");
  if (windows) {
    Array.prototype.forEach.call(windows.querySelectorAll("button[data-window]"),
      function (b) {
        b.addEventListener("click", function () {
          currentWindow = b.getAttribute("data-window") || currentWindow;
          Array.prototype.forEach.call(windows.querySelectorAll("button[data-window]"),
            function (x) {
              var on = x === b;
              x.className = "pill" + (on ? " on" : " off");
            });
          load();
        });
      });
  }

  load();
  // Zachtjes bijhouden: elke 30s het spoor opnieuw ophalen zodat een open pagina
  // meebeweegt zonder de view te resetten.
  if (window.fetch) setInterval(load, 30000);
})();
