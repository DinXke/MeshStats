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
})();
