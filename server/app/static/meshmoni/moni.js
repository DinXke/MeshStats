/* MeshMoni — de app-kant van de monitoringsubsite.
 *
 * Alles wat hier getekend wordt komt per fetch uit /meshmoni/api/...; de
 * antwoorden dragen Cache-Control: no-store en de service worker bewaart ze
 * dan ook niet. De stempel onderaan ("bijgewerkt om ...") is de eerlijkheid
 * daarbij: valt het netwerk weg, dan blijft het laatste beeld staan mét de
 * mededeling hoe oud het is, in plaats van een vers ogende leugen.
 *
 * De grafiek en het histogram zijn met de hand op een canvas getekend, zoals
 * de meters op de nodepagina's: geen bibliotheek van een CDN die de PWA
 * offline zou missen, en dit zijn twee kleine tekeningen, geen dashboard.
 */
(function () {
  "use strict";
  var BOOT = window.MONI || {};

  // --- thema (zelfde localStorage-sleutel als de site zelf) -----------------
  var themaKnop = document.getElementById("theme-toggle");
  if (themaKnop) themaKnop.addEventListener("click", function () {
    var licht = document.documentElement.getAttribute("data-theme") === "light";
    if (licht) { document.documentElement.removeAttribute("data-theme"); localStorage.removeItem("mcs-theme"); }
    else { document.documentElement.setAttribute("data-theme", "light"); localStorage.setItem("mcs-theme", "light"); }
  });

  // --- service worker --------------------------------------------------------
  if ("serviceWorker" in navigator) {
    navigator.serviceWorker.register("/meshmoni/sw.js").catch(function () {
      /* geen SW (bv. http zonder localhost): de site blijft gewoon werken */
    });
  }

  // --- de stempel ------------------------------------------------------------
  var stempel = document.getElementById("moni-stempel");
  var laatstBijgewerkt = null;
  function stempelZet() {
    laatstBijgewerkt = new Date();
    toonStempel();
  }
  function toonStempel() {
    if (!stempel || !laatstBijgewerkt) return;
    var sec = Math.round((Date.now() - laatstBijgewerkt.getTime()) / 1000);
    var t = laatstBijgewerkt.toLocaleTimeString("nl-BE", { hour: "2-digit", minute: "2-digit", second: "2-digit" });
    stempel.textContent = "bijgewerkt om " + t + (sec > 90 ? " (" + Math.round(sec / 60) + " min geleden)" : "");
    stempel.dataset.state = sec > 90 ? "oud" : "vers";
  }
  setInterval(toonStempel, 15000);

  function haal(url) {
    return fetch(url, { headers: { "Accept": "application/json" } }).then(function (r) {
      if (r.status === 401) { location.href = "/admin/login"; throw new Error("login"); }
      if (!r.ok) throw new Error("HTTP " + r.status);
      return r.json();
    }).then(function (data) { stempelZet(); return data; });
  }
  function post(url, body) {
    return fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-CSRF": BOOT.csrf || "" },
      body: JSON.stringify(body || {}),
    }).then(function (r) {
      if (r.status === 401) { location.href = "/admin/login"; throw new Error("login"); }
      return r.json().catch(function () { return {}; }).then(function (data) {
        if (!r.ok) throw new Error(data.detail || ("HTTP " + r.status));
        return data;
      });
    });
  }
  function el(tag, cls, tekst) {
    var e = document.createElement(tag);
    if (cls) e.className = cls;
    if (tekst !== undefined) e.textContent = tekst;
    return e;
  }
  function tijdKort(ts) {
    if (!ts) return "";
    var d = new Date(ts);
    return isNaN(d) ? ts : d.toLocaleString("nl-BE", { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" });
  }

  // --- overzicht: nodes ------------------------------------------------------
  function tekenNodes() {
    var vak = document.getElementById("moni-nodes");
    if (!vak) return;
    haal("/meshmoni/api/nodes").then(function (data) {
      vak.querySelectorAll(".moni-node").forEach(function (n) { n.remove(); });
      var leeg = document.getElementById("moni-nodes-leeg");
      if (leeg) leeg.hidden = data.nodes.length > 0;
      data.nodes.forEach(function (node) {
        var kaart = el("a", "moni-node");
        kaart.href = "/meshmoni/node/" + node.id;
        var kop = el("h3");
        var dot = el("span", "moni-dot" + (node.online ? " aan" : ""));
        dot.title = node.online ? "online" : "offline";
        kop.appendChild(dot);
        kop.appendChild(el("span", "", node.name));
        var meta = el("span", "moni-meta", node.battery != null ? Math.round(node.battery) + " %" : "");
        kop.appendChild(meta);
        kaart.appendChild(kop);
        var tegels = el("div", "moni-tegels");
        node.channels.forEach(function (k) {
          var t = el("span", "moni-tegel");
          t.appendChild(el("span", "l", k.label));
          var w = el("span", "w", k.display);
          if (k.kind === "switch") w.className += k.value === 1 ? " op" : " neer";
          t.appendChild(w);
          tegels.appendChild(t);
        });
        kaart.appendChild(tegels);
        vak.appendChild(kaart);
      });
    }).catch(function () {});
  }

  // --- overzicht: alerts -----------------------------------------------------
  var ookBevestigde = false;
  function tekenAlerts() {
    var lijst = document.getElementById("moni-alertlijst");
    if (!lijst) return;
    haal("/meshmoni/api/alerts" + (ookBevestigde ? "?all=1" : "")).then(function (data) {
      var badge = document.getElementById("moni-alertbadge");
      if (badge) { badge.hidden = data.open === 0; badge.textContent = data.open; }
      lijst.textContent = "";
      if (!data.alerts.length) {
        lijst.appendChild(el("p", "moni-leeg", ookBevestigde ? "Nog geen alerts." : "Niets open. Mooi zo."));
        return;
      }
      data.alerts.forEach(function (a) {
        var rij = el("div", "moni-alert ernst-" + (a.severity || "warning") + (a.acked ? " af" : ""));
        var t = el("div", "t");
        var kop = [a.node, a.channel_name].filter(Boolean).join(" — ");
        if (kop) t.appendChild(el("strong", "", kop));
        t.appendChild(el("div", "", a.text));
        // De bron, in woorden en met de latentie erbij. 'mesh' is er seconden
        // na het feit; 'ip' is afgeleid uit de poll en kan een heel
        // pollinterval oud zijn. Dat verschil hoort bij de melding te staan:
        // wie op een push kijkt, leest hier hoe vers "nu" is.
        var bron = a.source;
        if (a.source === "ip") {
          var min = Math.ceil((data.poll_s || 300) / 60);
          bron = "IP-poll · tot " + min + " min na het feit";
        } else if (a.source === "mesh") {
          bron = "via het mesh";
        }
        t.appendChild(el("small", "", tijdKort(a.ts) + " · " + bron));
        rij.appendChild(t);
        if (!a.acked) {
          var knop = el("button", "moni-ack", "✓");
          knop.title = "Bevestigen";
          knop.addEventListener("click", function () {
            post("/meshmoni/api/alerts/" + a.id + "/ack").then(tekenAlerts).catch(function (e) { alert(e.message); });
          });
          rij.appendChild(knop);
        }
        lijst.appendChild(rij);
      });
    }).catch(function () {});
  }
  var allesKnop = document.getElementById("moni-alles");
  if (allesKnop) allesKnop.addEventListener("click", function () {
    ookBevestigde = !ookBevestigde;
    allesKnop.textContent = ookBevestigde ? "alleen open" : "ook bevestigde";
    tekenAlerts();
  });

  // --- push ------------------------------------------------------------------
  function b64NaarBytes(s) {
    var pad = "=".repeat((4 - (s.length % 4)) % 4);
    var raw = atob((s + pad).replace(/-/g, "+").replace(/_/g, "/"));
    var out = new Uint8Array(raw.length);
    for (var i = 0; i < raw.length; i++) out[i] = raw.charCodeAt(i);
    return out;
  }
  function pushUI() {
    var uitleg = document.getElementById("moni-push-uitleg");
    var knop = document.getElementById("moni-push-knop");
    if (!uitleg || !knop) return;
    var st = BOOT.push || {};
    if (!st.enabled) {
      // Uit is uit, met de reden zichtbaar -- dezelfde afspraak als MM_FW_NODE_USER.
      uitleg.textContent = "Pushmeldingen staan uit: " + (st.reason || "onbekende reden");
      return;
    }
    if (!("serviceWorker" in navigator) || !("PushManager" in window)) {
      uitleg.textContent = "Deze browser kan geen webpush. Op een iPhone: zet de site " +
        "eerst via 'Zet op beginscherm' als app neer, en open hem daarvandaan.";
      return;
    }
    navigator.serviceWorker.ready.then(function (reg) {
      return reg.pushManager.getSubscription();
    }).then(function (sub) {
      knop.hidden = false;
      if (sub) {
        uitleg.textContent = "Dit toestel krijgt een melding bij elke nieuwe alert.";
        knop.textContent = "Meldingen uitzetten";
        knop.onclick = function () {
          sub.unsubscribe().then(function () {
            return post("/meshmoni/api/push/unsubscribe", { endpoint: sub.endpoint });
          }).then(pushUI).catch(function (e) { uitleg.textContent = e.message; });
        };
      } else {
        uitleg.textContent = "Zet meldingen aan om bij elke nieuwe alert een pushbericht te krijgen. " +
          "Werkt alleen over HTTPS; op een iPhone pas nadat de site op het beginscherm staat.";
        knop.textContent = "Meldingen aanzetten";
        knop.onclick = function () {
          Notification.requestPermission().then(function (perm) {
            if (perm !== "granted") { uitleg.textContent = "Geen toestemming van de browser gekregen."; return; }
            navigator.serviceWorker.ready.then(function (reg) {
              return reg.pushManager.subscribe({
                userVisibleOnly: true,
                applicationServerKey: b64NaarBytes(st.public_key),
              });
            }).then(function (nieuw) {
              return post("/meshmoni/api/push/subscribe", nieuw.toJSON());
            }).then(pushUI).catch(function (e) { uitleg.textContent = "Abonneren mislukte: " + e.message; });
          });
        };
      }
    });
  }

  // --- nodepagina: kanalen + historiek ---------------------------------------
  var VENSTERS = [{ u: 4, l: "4 u" }, { u: 24, l: "24 u" }, { u: 168, l: "7 d" }, { u: 744, l: "31 d" }];
  var huidigMetric = null, huidigUren = 24;

  function tekenKanalen() {
    var vak = document.getElementById("moni-kanalen");
    if (!vak) return;
    haal("/meshmoni/api/nodes").then(function (data) {
      var node = data.nodes.find(function (n) { return n.id === BOOT.node.id; });
      vak.textContent = "";
      if (!node) { vak.appendChild(el("p", "moni-leeg", "Nog geen kanaalmetingen van deze node.")); return; }
      var tegels = el("div", "moni-tegels");
      node.channels.forEach(function (k) {
        var t = el("button", "moni-tegel");
        t.type = "button";
        t.dataset.metric = k.metric;
        if (k.metric === huidigMetric) t.dataset.actief = "1";
        t.appendChild(el("span", "l", k.label));
        var w = el("span", "w", k.display);
        if (k.kind === "switch") w.className += k.value === 1 ? " op" : " neer";
        t.appendChild(w);
        t.addEventListener("click", function () { kiesMetric(k.metric, k.label); });
        tegels.appendChild(t);
      });
      vak.appendChild(tegels);
    }).catch(function () {});
  }

  function kiesMetric(metric, label) {
    huidigMetric = metric;
    document.querySelectorAll(".moni-tegel").forEach(function (t) {
      if (t.dataset) t.dataset.actief = t.dataset.metric === metric ? "1" : "0";
    });
    var sectie = document.getElementById("moni-historiek");
    sectie.hidden = false;
    document.getElementById("moni-historiek-titel").textContent = label;
    tekenVensters();
    tekenHistoriek();
  }

  function tekenVensters() {
    var vak = document.getElementById("moni-vensters");
    vak.textContent = "";
    VENSTERS.forEach(function (v) {
      var k = el("button", "", v.l);
      k.type = "button";
      if (v.u === huidigUren) k.dataset.actief = "1";
      k.addEventListener("click", function () { huidigUren = v.u; tekenVensters(); tekenHistoriek(); });
      vak.appendChild(k);
    });
  }

  function tekenHistoriek() {
    if (!huidigMetric) return;
    haal("/meshmoni/api/nodes/" + BOOT.node.id + "/history?metric=" +
         encodeURIComponent(huidigMetric) + "&hours=" + huidigUren).then(function (d) {
      lijn(document.getElementById("moni-grafiek"), d.points, d.unit);
      var rij = document.getElementById("moni-statrij");
      rij.textContent = "";
      var s = d.stats || {};
      [["min", s.min], ["gemiddeld", s.avg], ["max", s.max]].forEach(function (p) {
        var vak = el("div", "moni-stat");
        vak.appendChild(el("span", "l", p[0]));
        vak.appendChild(el("span", "w", p[1] == null ? "—" : fmt(p[1]) + (d.unit ? " " + d.unit : "")));
        rij.appendChild(vak);
      });
      staaf(document.getElementById("moni-histogram"), d.histogram || []);
    }).catch(function () {});
  }

  function fmt(v) { return Math.abs(v) >= 100 ? Math.round(v).toString() : (Math.round(v * 100) / 100).toString(); }
  function kleur(naam, terugval) {
    var v = getComputedStyle(document.documentElement).getPropertyValue(naam).trim();
    return v || terugval;
  }

  function maat(canvas) {
    var w = canvas.clientWidth || canvas.parentNode.clientWidth || 320;
    var dpr = window.devicePixelRatio || 1;
    canvas.width = w * dpr;
    canvas.height = parseInt(canvas.getAttribute("height"), 10) * dpr;
    var ctx = canvas.getContext("2d");
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    return { ctx: ctx, w: w, h: parseInt(canvas.getAttribute("height"), 10) };
  }

  function lijn(canvas, punten, eenheid) {
    var m = maat(canvas), ctx = m.ctx;
    ctx.clearRect(0, 0, m.w, m.h);
    var data = (punten || []).filter(function (p) { return p[1] != null; });
    if (data.length < 2) { legeMelding(ctx, m); return; }
    var xs = data.map(function (p) { return Date.parse(p[0]); });
    var ys = data.map(function (p) { return p[1]; });
    var x0 = Math.min.apply(null, xs), x1 = Math.max.apply(null, xs);
    var y0 = Math.min.apply(null, ys), y1 = Math.max.apply(null, ys);
    if (y1 === y0) { y0 -= 1; y1 += 1; }
    var padL = 44, padB = 18, padT = 8, padR = 6;
    function X(x) { return padL + (x - x0) / (x1 - x0) * (m.w - padL - padR); }
    function Y(y) { return m.h - padB - (y - y0) / (y1 - y0) * (m.h - padB - padT); }
    // rasterlijnen + as
    ctx.strokeStyle = kleur("--chart-grid", "rgba(125,143,160,.12)");
    ctx.fillStyle = kleur("--muted", "#7d8fa0");
    ctx.font = "10px " + kleur("--mono", "monospace");
    ctx.lineWidth = 1;
    for (var i = 0; i <= 3; i++) {
      var yv = y0 + (y1 - y0) * i / 3, yy = Y(yv);
      ctx.beginPath(); ctx.moveTo(padL, yy); ctx.lineTo(m.w - padR, yy); ctx.stroke();
      ctx.fillText(fmt(yv) + (eenheid ? " " + eenheid : ""), 2, yy + 3);
    }
    var t0 = new Date(x0), t1 = new Date(x1);
    ctx.fillText(t0.toLocaleString("nl-BE", { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" }), padL, m.h - 4);
    var eind = t1.toLocaleTimeString("nl-BE", { hour: "2-digit", minute: "2-digit" });
    ctx.fillText(eind, m.w - padR - ctx.measureText(eind).width, m.h - 4);
    // de lijn zelf
    ctx.strokeStyle = kleur("--accent", "#35e08c");
    ctx.lineWidth = 2;
    ctx.beginPath();
    data.forEach(function (p, idx) {
      var xx = X(Date.parse(p[0])), yy = Y(p[1]);
      if (idx === 0) ctx.moveTo(xx, yy); else ctx.lineTo(xx, yy);
    });
    ctx.stroke();
  }

  function staaf(canvas, bins) {
    var m = maat(canvas), ctx = m.ctx;
    ctx.clearRect(0, 0, m.w, m.h);
    if (!bins.length) { legeMelding(ctx, m); return; }
    var maxN = Math.max.apply(null, bins.map(function (b) { return b.n; })) || 1;
    var padB = 16, padT = 6;
    var bw = m.w / bins.length;
    ctx.fillStyle = kleur("--cyan", "#4cc9f0");
    bins.forEach(function (b, i) {
      var hoog = (m.h - padB - padT) * b.n / maxN;
      ctx.fillRect(i * bw + 2, m.h - padB - hoog, Math.max(bw - 4, 2), hoog);
    });
    ctx.fillStyle = kleur("--muted", "#7d8fa0");
    ctx.font = "10px " + kleur("--mono", "monospace");
    ctx.fillText(fmt(bins[0].lo), 2, m.h - 4);
    var r = fmt(bins[bins.length - 1].hi);
    ctx.fillText(r, m.w - ctx.measureText(r).width - 2, m.h - 4);
  }

  function legeMelding(ctx, m) {
    ctx.fillStyle = kleur("--muted", "#7d8fa0");
    ctx.font = "12px " + kleur("--sans", "sans-serif");
    ctx.fillText("nog geen metingen in dit venster", 10, m.h / 2);
  }

  // --- de opvraagknop ---------------------------------------------------------
  var uitvraag = document.getElementById("moni-uitvragen");
  if (uitvraag) uitvraag.addEventListener("click", function () {
    var uitkomst = document.getElementById("moni-uitvraag-uitkomst");
    uitvraag.disabled = true;
    post("/meshmoni/api/nodes/" + BOOT.node.id + "/refresh").then(function (d) {
      var tekst = {
        both: "gevraagd, over MQTT en via de poller",
        mqtt: "gevraagd, rechtstreeks over MQTT",
        queued: "in de wachtrij gezet voor de poller",
        none: "geen weg open naar deze node",
      };
      uitkomst.textContent = tekst[d.weg] || d.weg;
      // Even wachten en dan verversen: het antwoord van de node moet eerst
      // over de radio terug en door de ingest heen.
      setTimeout(function () { tekenKanalen(); }, 12000);
    }).catch(function (e) {
      uitkomst.textContent = e.message;
    }).finally(function () { uitvraag.disabled = false; });
  });

  // --- start ------------------------------------------------------------------
  if (BOOT.page === "index") {
    tekenNodes();
    tekenAlerts();
    pushUI();
    setInterval(function () { tekenNodes(); tekenAlerts(); }, 60000);
  } else if (BOOT.page === "node") {
    tekenKanalen();
    setInterval(tekenKanalen, 60000);
  }
})();
