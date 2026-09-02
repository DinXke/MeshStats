/* Companion-UI: twee client-side stukken die elk zichzelf uitschakelen als hun
 * ankerpunt niet op de pagina staat.
 *
 *  1. De SERIËLE CLI in de browser (companionpagina). Praat via de Web Serial
 *     API rechtstreeks met een companion die via USB aan DEZE computer hangt --
 *     geen server ertussen, geen mesh. Op serieel vervalt de `!` vooraan.
 *     Alleen Chrome/Edge; op de rest verschijnt een uitleg in plaats van een
 *     dode knop.
 *
 *  2. De BESTEMMINGS-KIEZER van de Send-DM-tab. Zodra er een afzender-node
 *     gekozen is, haalt hij de contacten van die node op
 *     (/admin/senddm/contacts/{rid}.json) en vult de keuzelijst + datalist. De
 *     companions staan er serverzijde al in, dus zonder JavaScript werkt de tab
 *     ook -- dit voegt de node-contacten toe.
 *
 * Bewust vanilla JS, één IIFE, geen build en geen afhankelijkheden -- dezelfde
 * lijn als app.js.
 */
(function () {
  "use strict";

  // --- 1. Web Serial terminal ------------------------------------------------
  var panel = document.getElementById("serial-panel");
  if (panel) wireSerial(panel);

  function wireSerial(panel) {
    var unsupported = document.getElementById("serial-unsupported");
    var controls = document.getElementById("serial-controls");
    var io = document.getElementById("serial-io");
    var connectBtn = document.getElementById("serial-connect");
    var disconnectBtn = document.getElementById("serial-disconnect");
    var statusEl = document.getElementById("serial-status");
    var logEl = document.getElementById("serial-log");
    var form = document.getElementById("serial-form");
    var input = document.getElementById("serial-input");

    // Feature-detect. Web Serial vereist een secure context (https of
    // localhost); een browser die het niet kent of een pagina die niet secure
    // is, krijgt de uitleg te zien in plaats van een knop die niets doet.
    if (!("serial" in navigator)) {
      if (unsupported) unsupported.hidden = false;
      return;
    }
    if (controls) controls.hidden = false;

    var port = null, reader = null, writer = null, keepReading = false;

    function log(line, cls) {
      if (!logEl) return;
      var atBottom = logEl.scrollHeight - logEl.scrollTop - logEl.clientHeight < 8;
      var span = document.createElement("span");
      if (cls) span.className = cls;
      span.textContent = line + "\n";
      logEl.appendChild(span);
      if (atBottom) logEl.scrollTop = logEl.scrollHeight;
    }

    function setStatus(text, connected) {
      if (statusEl) statusEl.textContent = text;
      if (connectBtn) connectBtn.hidden = connected;
      if (disconnectBtn) disconnectBtn.hidden = !connected;
      if (io) io.hidden = !connected;
    }

    async function connect() {
      try {
        port = await navigator.serial.requestPort();
      } catch (e) {
        // Geen poort gekozen (de gebruiker sloot de dialoog) is geen fout.
        log("Verbinden afgebroken.", "muted");
        return;
      }
      try {
        await port.open({ baudRate: 115200 });
      } catch (e) {
        log("Kon de poort niet openen: " + (e && e.message ? e.message : e), "err");
        port = null;
        return;
      }
      setStatus("verbonden", true);
      log("Verbonden op 115200 baud. Typ commando's ZONDER de ! vooraan.", "muted");
      try {
        writer = port.writable.getWriter();
      } catch (e) {
        log("Geen schrijfkanaal: " + e, "err");
      }
      keepReading = true;
      readLoop();
    }

    async function readLoop() {
      var decoder = new TextDecoder();
      var buffer = "";
      try {
        reader = port.readable.getReader();
        while (keepReading) {
          var res = await reader.read();
          if (res.done) break;
          buffer += decoder.decode(res.value, { stream: true });
          // Op regeleinden knippen zodat de log per regel groeit en niet per
          // willekeurige brok bytes.
          var parts = buffer.split(/\r\n|\n|\r/);
          buffer = parts.pop();
          for (var i = 0; i < parts.length; i++) {
            if (parts[i].length) log("< " + parts[i]);
          }
        }
      } catch (e) {
        if (keepReading) log("Leesfout: " + (e && e.message ? e.message : e), "err");
      } finally {
        try { if (reader) reader.releaseLock(); } catch (e) {}
        reader = null;
      }
    }

    async function send(text) {
      var cmd = String(text || "").trim();
      if (!cmd) return;
      // Op serieel vervalt de `!` -- wie hem uit gewoonte toch typt, krijgt hem
      // hier afgehaald in plaats van een onbegrepen commando bij de companion.
      if (cmd.charAt(0) === "!") cmd = cmd.slice(1);
      if (!writer) { log("Niet verbonden.", "err"); return; }
      try {
        await writer.write(new TextEncoder().encode(cmd + "\r\n"));
        log("> " + cmd, "muted");
      } catch (e) {
        log("Schrijffout: " + (e && e.message ? e.message : e), "err");
      }
    }

    async function disconnect() {
      keepReading = false;
      try { if (reader) await reader.cancel(); } catch (e) {}
      try { if (writer) { await writer.close(); writer.releaseLock && writer.releaseLock(); } } catch (e) {}
      writer = null;
      try { if (port) await port.close(); } catch (e) {}
      port = null;
      setStatus("niet verbonden", false);
      log("Verbinding verbroken.", "muted");
    }

    if (connectBtn) connectBtn.addEventListener("click", connect);
    if (disconnectBtn) disconnectBtn.addEventListener("click", disconnect);
    if (form) form.addEventListener("submit", function (e) {
      e.preventDefault();
      send(input.value);
      input.value = "";
      input.focus();
    });
    Array.prototype.forEach.call(panel.querySelectorAll(".serial-fill"), function (b) {
      b.addEventListener("click", function () {
        if (input) { input.value = b.getAttribute("data-cmd") || ""; input.focus(); }
      });
    });
    // De beltoon-preview over serieel: dezelfde bibliotheek als de mesh-knoppen,
    // maar hier als `play <naam>` (zonder !), meteen over de poort verstuurd.
    var tuneSel = document.getElementById("serial-tune");
    var tunePlay = document.getElementById("serial-tune-play");
    if (tunePlay && tuneSel) tunePlay.addEventListener("click", function () {
      send("play " + tuneSel.value);
    });
    // Een tab die weggaat terwijl de poort nog open is, laat het toestel anders
    // vastzitten voor de volgende pagina.
    window.addEventListener("beforeunload", function () { if (port) disconnect(); });
  }

  // --- 2. Send-DM bestemmings-kiezer -----------------------------------------
  var senderSel = document.getElementById("dm-sender");
  if (senderSel) wireDmChooser(senderSel);

  function wireDmChooser(senderSel) {
    var pick = document.getElementById("dm-dest-pick");
    var pubkey = document.getElementById("dm-pubkey");
    var note = document.getElementById("dm-dest-note");
    var datalist = document.getElementById("dm-dests");
    var base = senderSel.getAttribute("data-contacts") || "/admin/senddm/contacts/";

    // De companions die er serverzijde al in staan, bewaren we zodat een
    // herlaadronde ze niet dubbel toevoegt en ze bij een nieuwe afzender blijven.
    function resetOptions() {
      if (!pick) return;
      pick.innerHTML = "";
      var first = document.createElement("option");
      first.value = "";
      first.textContent = "— kies een bestemming —";
      pick.appendChild(first);
    }

    function addOption(list, value, label) {
      var o = document.createElement("option");
      o.value = value;
      o.textContent = label;
      list.appendChild(o);
    }

    senderSel.addEventListener("change", function () {
      var rid = senderSel.value;
      if (!rid) { if (note) note.textContent = ""; return; }
      if (note) note.textContent = "contacten laden…";
      fetch(base + encodeURIComponent(rid) + ".json", { credentials: "same-origin" })
        .then(function (r) { return r.json(); })
        .then(function (data) {
          resetOptions();
          if (datalist) datalist.innerHTML = "";
          var comps = data.companions || [];
          var contacts = data.contacts || [];
          comps.forEach(function (c) {
            if (pick) addOption(pick, c.pubkey, c.name + " (companion)");
            if (datalist) addDatalist(datalist, c.pubkey, c.name + " (companion)");
          });
          contacts.forEach(function (c) {
            var label = (c.name || c.pubkey.slice(0, 12)) + " (contact)";
            if (pick) addOption(pick, c.pubkey, label);
            if (datalist) addDatalist(datalist, c.pubkey, label);
          });
          if (note) {
            if (!data.ok && data.error) {
              note.textContent = "contacten van de node niet opgehaald: " + data.error +
                " (companions staan er wel)";
            } else {
              note.textContent = comps.length + " companion(s), " + contacts.length +
                " nodecontact(en)";
            }
          }
        })
        .catch(function (e) {
          if (note) note.textContent = "kon contacten niet laden";
        });
    });

    function addDatalist(list, value, label) {
      var o = document.createElement("option");
      o.value = value;
      o.setAttribute("label", label);
      list.appendChild(o);
    }

    if (pick) pick.addEventListener("change", function () {
      if (pick.value && pubkey) pubkey.value = pick.value;
    });
  }

  // --- 3. Commando's en Send-DM: fetch in plaats van een volledige page-load -
  //
  // Elke commando-knop en het 'Vrij bericht'/Send-DM-formulier posten naar een
  // FIRE-AND-FORGET DM: het versturen lukt of niet, maar het mesh-antwoord (als
  // er een komt) loopt niet via deze respons terug -- zie de docstring van
  // companion_cmd in routes_companions.py. Een volledige paginaherlading na elke
  // druk op zo'n knop is dus meer dan nodig, en had een echt gevolg: ververste
  // iemand de pagina uit gewoonte (de browser biedt dat aan na een POST-zonder-
  // redirect), dan ging de DM een TWEEDE keer de mesh op. Met JavaScript wordt
  // de submit onderschept en gaat het verzoek via fetch met
  // ``Accept: application/json``; de route herkent dat (``_wants_json``) en
  // geeft een korte ``{ok, msg}`` terug in plaats van de volledige pagina.
  //
  // Zonder fetch/FormData (of zonder JavaScript) blijft het formulier gewoon
  // werken: dan komt er geen JSON-Accept binnen en valt de route terug op zijn
  // PRG-redirect, met dezelfde tekst in de resultaatbanner van de doelpagina.
  var ajaxForms = document.querySelectorAll("form.cmd-ajax");
  if (ajaxForms.length && window.fetch && window.FormData) {
    Array.prototype.forEach.call(ajaxForms, wireAjaxForm);
  }

  function wireAjaxForm(form) {
    var note = document.createElement("span");
    note.className = "muted small cmd-note";
    form.appendChild(note);
    form.addEventListener("submit", function (e) {
      e.preventDefault();
      // De RADIO-waarschuwing: een formulier met ``data-confirm`` (het
      // "Radioparameter zetten"-formulier op de companion-pagina) mag pas de
      // deur uit ná een expliciete bevestiging -- bovenop de rode
      // waarschuwingstekst in de kaart zelf, niet in plaats ervan. Annuleren
      // stuurt niets: geen fetch, geen commando, geen note-tekst.
      var confirmText = form.getAttribute("data-confirm");
      if (confirmText && !window.confirm(confirmText)) return;
      var fd = new FormData(form);
      // Een formulier met twee submit-knoppen (bv. "Afspelen" vs "Toewijzen",
      // allebei name="cmd" met een andere value) laat FormData(form) NIET zien
      // welke knop is ingedrukt -- dat weet alleen het submit-event zelf, via
      // ``submitter``. Zonder dit zou de fetch-weg altijd het EERSTE commando
      // van het formulier sturen, ongeacht de aangeklikte knop.
      var inzender = e.submitter;
      if (inzender && inzender.name) fd.set(inzender.name, inzender.value);
      // De bot-kiezer (zie wireBotPicker hieronder) mag meesturen welke
      // bot-identiteit deze DM verstuurt -- alleen als de pagina er een heeft
      // én er iets anders dan "standaard" gekozen is.
      document.dispatchEvent(new CustomEvent("cmd-ajax:before-send",
        { detail: { formData: fd, form: form } }));
      note.className = "muted small cmd-note";
      note.textContent = "bezig…";
      fetch(form.action, {
        method: "POST", body: fd, credentials: "same-origin",
        headers: { "Accept": "application/json" },
      })
        .then(function (r) { return r.json(); })
        .then(function (data) {
          note.className = (data.ok ? "ok" : "bad") + " cmd-note";
          note.textContent = data.msg || (data.ok ? "gelukt" : "niet gelukt");
        })
        .catch(function () {
          note.className = "bad cmd-note";
          note.textContent = "kon niet versturen (netwerkfout) — probeer opnieuw";
        });
    });
  }

  // --- 3b. De bot-kiezer: welke bot-identiteit commando's verstuurt ----------
  //
  // Alleen op de companion-pagina (die het #bot-picker-anker rendert): de
  // Send-DM-tab en de kaart hebben geen commando-knoppen en dus niets om een
  // bot voor te kiezen. Eén kiezer per pagina en niet één per formulier: elk
  // formulier heeft al zijn eigen afzender-select, en een bot-select
  // ernaast op elk van de tien+ kaartjes zou de pagina onleesbaar maken voor
  // wat in de praktijk zelden verandert. De LAATST aangeraakte afzender-select
  // bepaalt welke bots hier staan; wisselt iemand van afzender in één
  // formulier zonder de bot-kiezer bij te werken, dan blijft de server toch
  // correct -- companions.resolve_bot valt terug op zijn eigen standaard voor
  // de node waarnaar dat formulier ECHT verstuurt.
  var botPicker = document.getElementById("bot-picker");
  if (botPicker) wireBotPicker(botPicker);

  function wireBotPicker(el) {
    var select = document.getElementById("bot-choice");
    var note = document.getElementById("bot-note");
    if (!select) return;
    var standaardOptie = select.innerHTML;   // de server-gerenderde "— standaard —"

    function loadFor(rid) {
      select.innerHTML = standaardOptie;
      if (!rid) { if (note) note.textContent = ""; return; }
      if (note) note.textContent = "bots laden…";
      fetch("/admin/companions/bots/" + encodeURIComponent(rid) + ".json",
            { credentials: "same-origin" })
        .then(function (r) { return r.json(); })
        .then(function (data) {
          (data.bots || []).forEach(function (b) {
            var o = document.createElement("option");
            o.value = b.name || String(b.idx);
            o.textContent = (b.name || ("bot " + b.idx)) + (b.alert ? " (alarm)" : "");
            select.appendChild(o);
          });
          if (note) {
            note.textContent = data.ok
              ? (data.bots.length + " bot(s) gevonden op deze node")
              : ("bots niet opgehaald: " + (data.error || "onbekende fout") +
                 " (standaard blijft werken)");
          }
        })
        .catch(function () { if (note) note.textContent = "kon bots niet laden"; });
    }

    loadFor(el.getAttribute("data-rep"));

    // Elke afzender-select op de pagina (één per commando-formulier) ververst
    // deze lijst zodra hij verandert.
    Array.prototype.forEach.call(document.querySelectorAll('select[name="sender"]'),
      function (s) {
        s.addEventListener("change", function () { loadFor(s.value); });
      });

    document.addEventListener("cmd-ajax:before-send", function (e) {
      if (select.value) e.detail.formData.set("bot", select.value);
    });
  }

  // --- 4. Live gegevens: locatie/gezien/val, elke 15-20s ververst -------------
  //
  // Gedeeld met companions_map.js (dezelfde /admin/companions/status.json), maar
  // hier voor de LIJST (de "laatst gezien"-kolom) en de DETAILPAGINA (de
  // valbadge en de locatieregel bovenaan). Elke tik roept dezelfde route aan,
  // die op zijn beurt een ONDEMAND-poll probeert (companions.poll_now, met een
  // eigen hamerbescherming) zodat een pagina die je ECHT bekijkt de actuele
  // locatie/val te zien krijgt in plaats van te wachten op de achtergrondronde.

  // Dezelfde "hoe lang geleden"-tekst als companions_map.js. Bewust hier
  // gekopieerd en niet gedeeld met app.js's interne relTime(): die is intern
  // aan zijn eigen IIFE, en deze twee bestanden draaien onafhankelijk van
  // elkaar (companions.html laadt companions_map.js niet, en omgekeerd).
  function ageText(iso) {
    if (!iso) return "onbekend";
    var d = new Date(iso);
    if (isNaN(d.getTime())) return "onbekend";
    var s = Math.round((Date.now() - d.getTime()) / 1000);
    if (s < 0) s = 0;
    if (s < 60) return "zojuist";
    if (s < 3600) return Math.round(s / 60) + " min geleden";
    if (s < 86400) return Math.round(s / 3600) + " uur geleden";
    return Math.round(s / 86400) + " dag(en) geleden";
  }

  function statusUrl(repId) {
    return "/admin/companions/status.json" + (repId ? "?rep=" + encodeURIComponent(repId) : "");
  }

  // 4a. De companions-lijst: "laatst gezien" + val-badge per rij.
  var compTable = document.getElementById("companions-table");
  if (compTable) wireLiveList(compTable);

  function wireLiveList(table) {
    var body = table.querySelector("tbody");

    function fillCell(cell, c) {
      cell.textContent = "";
      if (c.seen_iso) {
        var t = document.createElement("time");
        t.className = "reltime";
        t.setAttribute("datetime", c.seen_iso);
        t.textContent = ageText(c.seen_iso);
        t.title = new Date(c.seen_iso).toLocaleString();
        cell.appendChild(t);
      } else {
        var span = document.createElement("span");
        span.className = "muted";
        span.textContent = "— onbekend —";
        cell.appendChild(span);
      }
      cell.appendChild(document.createTextNode(" "));
      var pill = document.createElement("span");
      pill.className = "bad live-fall-pill";
      pill.hidden = !c.fall_recent;
      pill.textContent = "⚠ val";
      cell.appendChild(pill);
    }

    function tick() {
      fetch(statusUrl(), { credentials: "same-origin" })
        .then(function (r) { return r.json(); })
        .then(function (data) {
          (data.companions || []).forEach(function (c) {
            var row = body.querySelector('tr[data-id="' + c.id + '"]');
            var cell = row && row.querySelector(".live-seen");
            if (cell) fillCell(cell, c);
          });
        })
        .catch(function () { /* volgende tik probeert het opnieuw */ });
    }
    tick();
    setInterval(tick, 20000);
  }

  // 4b. De companion-detailpagina: de valbadge bovenaan en de locatieregel.
  var compHead = document.getElementById("companion-head");
  if (compHead) wireLiveDetail(compHead);

  function wireLiveDetail(head) {
    var cid = parseInt(head.getAttribute("data-cid"), 10);
    var repId = head.getAttribute("data-rep") || "";
    var updated = document.getElementById("live-updated");

    function applyFall(c) {
      var badge = document.getElementById("live-fall-badge");
      var kind = document.getElementById("live-fall-kind");
      if (!badge) return;
      badge.hidden = !c.fall_recent;
      if (kind) kind.textContent = c.fall_kind ? " (" + c.fall_kind + ")" : "";
    }

    function applyBatt(c) {
      // De batterij-chip alleen tonen als de laatste ronde een stand kende
      // (companions.batt, via _loc); onbekend blijft verborgen -- geen "0%".
      var chip = document.getElementById("live-batt");
      if (!chip) return;
      var heeft = typeof c.batt === "number";
      chip.hidden = !heeft;
      if (!heeft) return;
      var pct = document.getElementById("live-batt-pct");
      if (pct) pct.textContent = c.batt;
    }

    function applyLocation(c) {
      var known = document.getElementById("live-loc-known");
      var unknown = document.getElementById("live-loc-unknown");
      if (!known || !unknown) return;
      var heeft = typeof c.lat === "number" && typeof c.lon === "number";
      known.hidden = !heeft;
      unknown.hidden = heeft;
      if (!heeft) return;
      var coords = document.getElementById("live-loc-coords");
      if (coords) coords.textContent = c.lat.toFixed(5) + ", " + c.lon.toFixed(5);
      var seen = document.getElementById("live-loc-seen");
      if (seen && c.seen_iso) {
        seen.setAttribute("datetime", c.seen_iso);
        seen.textContent = ageText(c.seen_iso);
        seen.title = new Date(c.seen_iso).toLocaleString();
      }
    }

    function tick() {
      fetch(statusUrl(repId), { credentials: "same-origin" })
        .then(function (r) { return r.json(); })
        .then(function (data) {
          var c = (data.companions || []).filter(function (x) { return x.id === cid; })[0];
          if (!c) return;
          applyFall(c);
          applyBatt(c);
          applyLocation(c);
          if (updated) updated.textContent = "· ververst " + new Date().toLocaleTimeString();
        })
        .catch(function () { /* volgende tik probeert het opnieuw */ });
    }
    tick();
    setInterval(tick, 15000);
  }
})();
