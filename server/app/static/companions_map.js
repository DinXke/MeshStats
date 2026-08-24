/* Companions — kaart. Puur weergave: een marker per companion met een bekende
 * laatste locatie (window.COMPANION_LOCATIONS, gezet door companions_map.html),
 * met naam + type + hoe oud de melding is in de popup. Geen afhankelijkheid van
 * app.js's relTime() -- die is intern aan zijn eigen IIFE en zou een popup die
 * pas na het laden ingevoegd wordt hooguit bij de eerstvolgende 30s-tik
 * bijwerken. Deze pagina rekent de ouderdom zelf uit, één keer bij het bouwen
 * van de marker -- prima voor een achtergrondronde die om de paar minuten
 * ververst en niet om de seconde.
 */
(function () {
  "use strict";
  var mapEl = document.getElementById("companion-map");
  if (!mapEl || typeof L === "undefined") return;

  // Zelfde thema-detectie en tegel-URL als app.js (CartoDB, licht/donker naar
  // data-theme). Geen import nodig: het thema staat al op <html> vóór dit
  // script draait (zie het inline scriptje in base.html).
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

  var points = (window.COMPANION_LOCATIONS || []).filter(function (c) {
    return typeof c.lat === "number" && typeof c.lon === "number";
  });
  if (!points.length) return;

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

  var bounds = [];
  points.forEach(function (c) {
    var marker = c.fall_recent
      ? L.marker([c.lat, c.lon], { icon: fallIcon }).addTo(map)
      : L.marker([c.lat, c.lon]).addTo(map);
    var label = esc(c.name || "companion") + (c.type ? " · " + esc(c.type) : "");
    var popup = "<strong>" + label + "</strong><br>" + esc(ageText(c.seen_iso));
    if (c.fall_recent) {
      var kind = esc(c.fall_kind || "onbekend");
      popup += "<br><strong style=\"color:var(--red)\">&#9888; val (" + kind + "): " +
        esc(ageText(c.fall_iso)) + "</strong>";
    }
    popup += "<br><a href=\"/admin/companions/" + encodeURIComponent(c.id) +
      "\">beheren &rarr;</a>";
    marker.bindPopup(popup);
    bounds.push([c.lat, c.lon]);
  });

  if (bounds.length === 1) {
    map.setView(bounds[0], 14);
  } else {
    map.fitBounds(bounds, { padding: [30, 30] });
  }
})();
