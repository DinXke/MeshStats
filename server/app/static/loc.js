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
    // Een puntje op ELK doorgegeven locatiepunt (elke #LOC-doorgifte), niet enkel
    // de verbindingslijn -- zo zie je waar de companion telkens gemeld heeft.
    (points || []).forEach(function (p) {
      var pIso = p[2] ? new Date(p[2] * 1000).toISOString() : null;
      L.circleMarker([p[0], p[1]], {
        radius: 3, color: "#2f7d54", weight: 1,
        fillColor: "#3aa76d", fillOpacity: 0.9
      }).bindTooltip(ageText(pIso)).addTo(layer);
    });
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

  // --- de "Vraag locatie op"-knop -------------------------------------------
  //
  // Post naar /loc/<token>/request (fetch, Accept: application/json), toon de
  // melding van de server, en schakel de knop uit voor de afkoeltijd -- de
  // ECHTE rem staat serverzijde, dit is alleen de bijpassende terugkoppeling.
  // Na een gelukte opvraag komt het antwoord van de companion niet synchroon
  // terug (het loopt via de locatie-poll), dus verversen we het spoor nog een
  // halve minuut wat vaker, zodat de nieuwe positie op de kaart verschijnt
  // zodra hij binnen is. Zonder fetch (of JS) blijft het gewone formulier de
  // POST doen en valt de server terug op een redirect naar deze pagina.
  var form = document.getElementById("loc-request-form");
  var btn = document.getElementById("loc-request-btn");
  var msgEl = document.getElementById("loc-request-msg");
  var COOLDOWN = parseInt(window.LOC_REQUEST_COOLDOWN, 10) || 90;
  var LABEL = btn ? btn.innerHTML : "";
  var cooldownTimer = null;

  function setMsg(text, ok) {
    if (!msgEl) return;
    msgEl.textContent = text || "";
    msgEl.className = "small " + (ok ? "ok" : "muted");
  }

  // De knop uitschakelen en aftellen; na afloop weer inschakelen met zijn
  // oorspronkelijke tekst. ``secs`` is het aantal seconden dat hij uit moet.
  function disableFor(secs) {
    if (!btn) return;
    if (cooldownTimer) { clearInterval(cooldownTimer); cooldownTimer = null; }
    var left = Math.max(1, Math.round(secs));
    btn.disabled = true;
    var tick = function () {
      btn.innerHTML = "&#9203; nog " + left + "s";
      left -= 1;
      if (left < 0) {
        clearInterval(cooldownTimer); cooldownTimer = null;
        btn.disabled = false;
        btn.innerHTML = LABEL;
      }
    };
    tick();
    cooldownTimer = setInterval(tick, 1000);
  }

  // Na een gelukte opvraag: een tijdje wat vaker het spoor ophalen (bovenop de
  // vaste 30s-tik), zodat de nieuwe positie snel op de kaart komt. Stopt na
  // ~60s vanzelf.
  function refreshBurst() {
    var n = 0;
    var burst = setInterval(function () {
      load();
      n += 1;
      if (n >= 12) clearInterval(burst);   // 12 x 5s ~ 60s
    }, 5000);
  }

  // Uit de rate-limit-melding ("even wachten — nog <N>s") het aantal seconden
  // vissen, met de volledige afkoeltijd als terugval.
  function waitSecs(msg) {
    var m = /(\d+)\s*s/.exec(msg || "");
    return m ? parseInt(m[1], 10) : COOLDOWN;
  }

  if (form && btn && window.fetch) {
    form.addEventListener("submit", function (ev) {
      ev.preventDefault();
      if (btn.disabled) return;
      btn.disabled = true;
      setMsg("bezig…", true);
      fetch(form.action, {
        method: "POST",
        credentials: "same-origin",
        headers: { "Accept": "application/json" },
      })
        .then(function (r) { return r.json(); })
        .then(function (data) {
          setMsg(data.msg || "", !!data.ok);
          if (data.ok) {
            disableFor(COOLDOWN);
            refreshBurst();
          } else {
            // Een rem-melding houdt de knop uit tot de rem afloopt; een andere
            // weigering (geen afzender) laat hem meteen weer bruikbaar.
            var w = waitSecs(data.msg);
            if (/wachten/.test(data.msg || "")) disableFor(w);
            else { btn.disabled = false; }
          }
        })
        .catch(function () {
          setMsg("kon de opvraag niet versturen", false);
          btn.disabled = false;
        });
    });
  }
})();
