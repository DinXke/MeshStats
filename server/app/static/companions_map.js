/* Companions — kaart. Puur weergave: een marker per companion met een bekende
 * laatste locatie, met naam + type + hoe oud de melding is in de popup. Geen
 * afhankelijkheid van app.js's relTime() -- die is intern aan zijn eigen IIFE
 * en zou een popup die pas na het laden ingevoegd wordt hooguit bij de
 * eerstvolgende 30s-tik bijwerken. Dit bestand rekent de ouderdom zelf uit.
 *
 * De EERSTE tekening komt uit window.COMPANION_LOCATIONS (door de server
 * meegegeven, zodat de kaart er meteen staat zonder op een fetch te wachten).
 * Daarna ververst een periodieke aanroep van /admin/companions/status.json de
 * MARKERS -- niet de kaart zelf: een refresh die fitBounds() opnieuw zou
 * aanroepen, zou het pan/zoom van de bezoeker onderuit halen bij elke tik. Die
 * route doet er ook een ONDEMAND-poll bij (companions.poll_now, met een eigen
 * hamerbescherming): wie deze kaart open heeft staan, ziet zo de actuele
 * locatie in plaats van te wachten op de achtergrondronde.
 */
(function () {
  "use strict";
  var mapEl = document.getElementById("companion-map");

  var THEME = document.documentElement.getAttribute("data-theme") === "light" ? "light" : "dark";
  var TILE_URL = "https://{s}.basemaps.cartocdn.com/" +
    (THEME === "light" ? "light_all" : "dark_all") + "/{z}/{x}/{y}{r}.png";

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

  function esc(text) {
    return String(text == null ? "" : text).replace(/&/g, "&amp;").replace(/</g, "&lt;");
  }

  function withLocation(list) {
    return (list || []).filter(function (c) {
      return typeof c.lat === "number" && typeof c.lon === "number";
    });
  }

  // Geen enkele companion heeft ooit een locatie gemeld: de server tekent dan
  // geen #companion-map (zie companions_map.html), alleen de uitlegtekst. Zodra
  // er tijdens het kijken alsnog een EERSTE locatie binnenkomt, is een volledige
  // herlading eenvoudiger en betrouwbaarder dan een kaart met tegels en
  // legenda die deze pagina dan alsnog met JavaScript zou moeten optuigen --
  // en er staat hier geen formulier dat een herlading zou kunnen verstoren.
  if (!mapEl || typeof L === "undefined") {
    if (!window.fetch) return;
    var poller = setInterval(function () {
      fetch("/admin/companions/status.json", { credentials: "same-origin" })
        .then(function (r) { return r.json(); })
        .then(function (data) {
          if (withLocation(data.companions).length) {
            clearInterval(poller);
            location.reload();
          }
        })
        .catch(function () { /* volgende tik probeert het opnieuw */ });
    }, 20000);
    return;
  }

  var map = L.map(mapEl, { scrollWheelZoom: false });
  L.tileLayer(TILE_URL, {
    attribution: "&copy; OpenStreetMap &copy; CARTO", maxZoom: 19,
  }).addTo(map);

  // Een companion met een RECENTE val (fall_recent, gezet door routes_companions
  // op basis van FALL_RECENT_S) krijgt een eigen icoon in plaats van de gewone
  // Leaflet-pin -- dezelfde reden als de rode badge op de detailpagina: wie deze
  // kaart in één oogopslag scant, moet een noodgeval niet hoeven op te zoeken
  // tussen tientallen gelijke bolletjes.
  var fallIcon = L.divIcon({
    className: "companion-fall-icon",
    html: "<div style=\"font-size:22px;line-height:22px\">&#9888;&#65039;</div>",
    iconSize: [24, 24], iconAnchor: [12, 20], popupAnchor: [0, -18],
  });

  // Eén laag met alle markers, zodat een ververs-tik hem in zijn geheel kan
  // vervangen (removeLayer + addLayer) zonder de kaart zelf (view, zoom) aan
  // te raken -- dat laatste zou het pan/zoom van de bezoeker bij elke tik
  // resetten, wat een auto-ververs juist NIET moet doen.
  var markerLayer = null;

  function buildMarkers(points) {
    var layer = L.layerGroup();
    points.forEach(function (c) {
      var marker = c.fall_recent
        ? L.marker([c.lat, c.lon], { icon: fallIcon })
        : L.marker([c.lat, c.lon]);
      var label = esc(c.name || "companion") + (c.type ? " · " + esc(c.type) : "");
      var popup = "<strong>" + label + "</strong><br>" + esc(ageText(c.seen_iso));
      // De batterij alleen als de node hem meldde (companions.batt); een companion
      // zonder bekende stand toont niets -- geen "0%" of "onbekend" verzinnen.
      if (typeof c.batt === "number") {
        popup += "<br>&#128267; " + esc(c.batt) + "%";
      }
      if (c.fall_recent) {
        var kind = esc(c.fall_kind || "onbekend");
        popup += "<br><strong style=\"color:var(--red)\">&#9888; val (" + kind + "): " +
          esc(ageText(c.fall_iso)) + "</strong>";
      }
      popup += "<br><a href=\"/admin/companions/" + encodeURIComponent(c.id) +
        "\">beheren &rarr;</a>";
      marker.bindPopup(popup);
      // Klik op een marker tekent het SPOOR van díe companion binnen het gekozen
      // venster (zie hieronder). De popup opent ook -- de twee bijten elkaar niet.
      marker.on("click", function () { loadTrack(c.id); });
      layer.addLayer(marker);
    });
    return layer;
  }

  // --- het SPOOR per companion (1u/6u/24u/7d) --------------------------------
  //
  // Een aparte laag naast markerLayer, zodat de 20s-ververs de markers kan
  // vervangen zonder het getoonde spoor te wissen -- en zodat "wis spoor" en een
  // venster-wissel alleen dit aanraken. De venster-knoppen staan in
  // companions_map.html; de laatst aangeklikte companion bepaalt WELK spoor een
  // venster-wissel opnieuw ophaalt.
  var trackLayer = null;
  var trackCompanionId = null;
  var trackWindows = document.getElementById("track-windows");
  var currentWindow = "24h";
  if (trackWindows) {
    var on = trackWindows.querySelector("button.pill.on[data-window]");
    if (on) currentWindow = on.getAttribute("data-window") || currentWindow;
  }

  function clearTrack() {
    if (trackLayer) { map.removeLayer(trackLayer); trackLayer = null; }
  }

  function loadTrack(id) {
    if (!window.fetch || id == null) return;
    trackCompanionId = id;
    fetch("/admin/companions/" + encodeURIComponent(id) +
          "/track.json?window=" + encodeURIComponent(currentWindow),
          { credentials: "same-origin" })
      .then(function (r) { return r.json(); })
      .then(function (data) {
        clearTrack();
        var raw = data.points || [];
        var pts = raw.map(function (p) { return [p[0], p[1]]; });
        if (pts.length) {
          // Laaggroep: de verbindingslijn PLUS een puntje op ELK doorgegeven
          // locatiepunt (elke #LOC-doorgifte), zodat het hele tracé zichtbaar is.
          trackLayer = L.layerGroup();
          if (pts.length > 1) {
            L.polyline(pts, { color: "#3aa76d", weight: 3, opacity: 0.8 }).addTo(trackLayer);
          }
          raw.forEach(function (p) {
            var tip = p[2] ? new Date(p[2] * 1000).toLocaleString() : "";
            L.circleMarker([p[0], p[1]], {
              radius: 3, color: "#2f7d54", weight: 1,
              fillColor: "#3aa76d", fillOpacity: 0.9
            }).bindTooltip(tip).addTo(trackLayer);
          });
          trackLayer.addTo(map);
        }
      })
      .catch(function () { /* een mislukte ophaling laat de kaart met rust */ });
  }

  if (trackWindows) {
    Array.prototype.forEach.call(trackWindows.querySelectorAll("button[data-window]"),
      function (b) {
        b.addEventListener("click", function () {
          currentWindow = b.getAttribute("data-window") || currentWindow;
          Array.prototype.forEach.call(
            trackWindows.querySelectorAll("button[data-window]"),
            function (x) { x.className = "pill" + (x === b ? " on" : " off"); });
          if (trackCompanionId != null) loadTrack(trackCompanionId);
        });
      });
    var clearBtn = document.getElementById("track-clear");
    if (clearBtn) clearBtn.addEventListener("click", function () {
      trackCompanionId = null;
      clearTrack();
    });
  }

  function showMarkers(points) {
    var nieuw = buildMarkers(points);
    if (markerLayer) map.removeLayer(markerLayer);
    markerLayer = nieuw;
    markerLayer.addTo(map);
  }

  var eerstePunten = withLocation(window.COMPANION_LOCATIONS);
  showMarkers(eerstePunten);

  var bounds = eerstePunten.map(function (c) { return [c.lat, c.lon]; });
  if (bounds.length === 1) {
    map.setView(bounds[0], 14);
  } else if (bounds.length > 1) {
    map.fitBounds(bounds, { padding: [30, 30] });
  }

  // toggleable SNR-achtige labels bestaan hier niet (dat is de linkkaart in
  // app.js); wél dezelfde periodieke ververs als de lijst en de detailpagina
  // (companions.js), via dezelfde route.
  if (window.fetch) {
    setInterval(function () {
      fetch("/admin/companions/status.json", { credentials: "same-origin" })
        .then(function (r) { return r.json(); })
        .then(function (data) { showMarkers(withLocation(data.companions)); })
        .catch(function () { /* volgende tik probeert het opnieuw */ });
    }, 20000);
  }
})();
