/* MeshStats front-end: relative times, gauges, charts, maps and the history
 * modal. Loaded after i18n.js, so every string this file builds goes through
 * MCSI18N.t -- text baked in here would survive a language switch and leave the
 * page half translated. */
(function () {
  "use strict";

  var PALETTE = ["#2bb673", "#e8913a", "#3aa7d0", "#e06c9f"];
  var t = (window.MCSI18N && window.MCSI18N.t) || function (k) { return k; };
  // Theme colours come from the CSS variables (light/dark)
  function cssVar(name, fallback) {
    var v = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
    return v || fallback;
  }

  // Every catch that deliberately carries on says so out loud. A `.catch` with
  // an empty body is the right shape for "the network hiccuped, the next tick
  // tries again" and the wrong shape for everything else: it swallows genuine
  // render errors just as quietly, and one of those left the live map without
  // a single dot, line or feed row for a day with a perfectly clean console.
  // Nothing here changes behaviour -- the page still carries on -- it only
  // stops the failure from being invisible.
  function logFail(what, err) {
    if (window.console && console.error) console.error("[meshstats] " + what, err);
  }
  // --- address-hash candidates ---------------------------------------------
  // A sender, a destination and a path hop are all named by one or two bytes of
  // a public key, which on a mesh of several hundred nodes routinely fits more
  // than one node. The server no longer merely lists the matches: it drops the
  // ones the frame places out of radio reach and ranks the rest on evidence
  // (server/app/candidates.py). Four states come back, and each reads
  // differently:
  //
  //   known      one node stands. A name -- but dotted, because one byte is a
  //              derivation however few nodes answer to it.
  //   likely     several stand and one ranks above the rest. The leader's name,
  //              dotted, with the others and the ground for the order alongside.
  //   ambiguous  several stand and nothing separates them. "N mogelijk", as
  //              before: putting one of them first here would be a coin toss
  //              printed as a conclusion.
  //   unknown    nothing stands. Not "no information": the byte off the wire is
  //              still there, and it is printed as 0xNN. It is the only handle
  //              this sender has, it is the same handle in every packet they
  //              send, and the archive can filter on it -- so a row that showed
  //              only the word "onbekend" was throwing away the one fact the
  //              frame did give.
  //
  // A fifth state is not needed for "this packet type carries no such hash at
  // all" -- an ADVERT names its sender outright, an ACK names nobody. The
  // server sends no object at all for those, and a missing object is a
  // different thing from an object whose hash matched nothing. Where the
  // helpers below return null, the caller's own wording applies ("onbekend",
  // "—"); where they return a label, there was a byte on the wire.
  //
  // Shared between the live feed and the archive, which render the same packets
  // in two places and must tell the same story in both.
  function candNames(res) {
    return (res.matches || []).map(function (m) {
      return m.name || (m.prefix || "").toUpperCase();
    });
  }

  // List rows get a trimmed resolution (a count plus the first few names), the
  // detail panel the whole one. Read the counts from whichever arrived.
  function candTotal(res) {
    return res.total != null ? res.total : (res.matches || []).length;
  }
  function droppedTotal(res) {
    return res.dropped_total != null ? res.dropped_total : (res.dropped || []).length;
  }

  // Which measurement put the leader on top, in the leader's own figures. The
  // server names the signal that broke the tie; the sentence stays about what
  // was measured rather than about how sure anyone feels.
  function leadReason(res) {
    var m = (res.matches || [])[0];
    if (!m || !res.lead) return null;
    if (res.lead === "hops") {
      if (m.hops == null) return null;
      if (m.hops === 0) return t("pkt.cand_why_direct");
      return t(m.hops === 1 ? "pkt.cand_why_hop1" : "pkt.cand_why_hops", { n: m.hops });
    }
    if (res.lead === "distance") {
      return m.km == null ? null : t("pkt.cand_why_near", { km: m.km });
    }
    if (res.lead === "recency") return t("pkt.cand_why_recent");
    return null;
  }

  // An excluded candidate must never simply vanish: say how many went and on
  // what ground, so a reader who knows better can disagree with the reasoning.
  function droppedNote(res) {
    var n = droppedTotal(res);
    if (!n) return null;
    var names = (res.dropped || []).map(function (m) {
      return m.name || (m.prefix || "").toUpperCase();
    });
    return t(n === 1 ? "pkt.cand_dropped_one" : "pkt.cand_dropped",
             { n: n, list: names.join(", ") });
  }

  // Where the name came from. Said in every state, because the dotted underline
  // is a promise that this sentence exists somewhere.
  function hashNote(res) {
    return t("pkt.src_from_hash", { h: (res.hash || "").toUpperCase() });
  }

  // Everything about the ordering: that it is one, who else is in it, why this
  // one leads, and who was ruled out. Empty when there is nothing to rank.
  function rankNote(res) {
    var bits = [];
    var names = candNames(res);
    if (res.state === "likely") {
      bits.push(t("pkt.cand_ranked", { n: candTotal(res) }));
      if (names.length > 1) {
        bits.push(t("pkt.cand_others", { list: names.slice(1).join(", ") }));
      }
      var why = leadReason(res);
      if (why) bits.push(why);
    } else if (res.state === "ambiguous") {
      bits.push(t("pkt.src_candidates", { list: names.join(", "),
                                          h: (res.hash || "").toUpperCase() }));
    } else if (!names.length) {
      // Two different nothings, and the difference is the whole reason the
      // exclusion prints its own note: a hash that fits no contact we have ever
      // heard of, against one whose candidates were all ruled out.
      bits.push(t(droppedTotal(res) ? "pkt.cand_none_left" : "pkt.cand_none"));
    }
    var drop = droppedNote(res);
    if (drop) bits.push(drop);
    return bits.join(" · ");
  }

  // The hash as the list prints it: 0x92, never a bare 92. Node keys are shown
  // as bare uppercase hex all over this site, so an unprefixed byte would read
  // as a very short key prefix -- which is exactly the confusion this whole
  // module exists to prevent.
  function hashText(res) {
    return "0x" + (res.hash || "").toUpperCase();
  }

  // Null only when there was no such hash on the wire at all. Every other case
  // -- named, ranked, ambiguous, or matching nothing we know -- has something
  // true to print, and the caller marks all of them as derived.
  function srcLabel(src, t) {
    if (!src || !src.hash) return null;
    var named = src.matches && src.matches.length;
    var text = !named ? hashText(src)
      : (src.state === "ambiguous" ? t("pkt.src_multi", { n: candTotal(src) })
                                   : candNames(src)[0]);
    var rank = rankNote(src);
    return { text: text, title: hashNote(src) + (rank ? " · " + rank : "") };
  }

  // --- packet detail, shared by the live page and the archive -------------------
  // Both pages render the same fragment (templates/_packet_detail.html): the live
  // page inside a docked panel beside the map, the archive inside a modal. The
  // filling is therefore written once, here at the top level, rather than twice.
  // Two panels drifting apart would be worse than a little indirection, because
  // what they render is the honesty rule about derived senders and ambiguous
  // hops -- and a rule stated differently in two places is a rule that will
  // eventually be stated wrongly in one of them.
  //
  // Only one of the two containers ever exists on a page, so the element ids
  // below stay unique and lookup by id is enough; scoping every query to a root
  // element was considered and dropped as ceremony without a payer.
  function txt(id, value) {
    var el = document.getElementById(id);
    if (el) el.textContent = value;
  }

  function nodeLabel(prefix, name) {
    var p = (prefix || "").toUpperCase();
    if (!p && !name) return "";
    return name ? name + (p ? " (" + p + ")" : "") : p;
  }

  // Country names are the one label deliberately left untranslated: a flag and
  // an ISO code read the same in every language, where a list of country names
  // would be a second dictionary to keep in step with the first.
  function flagOf(code) {
    if (!/^[A-Za-z]{2}$/.test(code || "")) return "";
    return String.fromCodePoint.apply(String, code.toUpperCase().split("")
      .map(function (c) { return 0x1F1E6 + c.charCodeAt(0) - 65; }));
  }
  function countryLabel(code) {
    return code ? flagOf(code) + " " + code.toUpperCase() : t("pkt.country_unknown");
  }

  // Whether the sender kept the packet inside a region. Two spellings: the
  // column has room for a word, the panel for the sentence that word stands
  // for -- and for scoped traffic that sentence has to say whether a region
  // was actually named, because almost always it was not.
  function scopeDetail(d) {
    if (!d.scope) return "—";
    if (d.scope === "scoped") {
      return t("scope.scoped") + " — " + (d.scope_region
        ? t("scope.region", { n: d.scope_region })
        : t("scope.region_unnamed"));
    }
    return t("scope." + d.scope) + " — " + t("scope." + d.scope + "_note");
  }

  function hopLabel(hop) {
    if (hop.state === "known" || hop.state === "likely") {
      var m = hop.matches[0];
      var s = m.name || m.prefix.toUpperCase();
      // A ranked hop says so in the line itself, and names the runners-up
      // there rather than only in the hover: the map still draws this hop as a
      // gap with a ring on every candidate, and a name here that looked as firm
      // as a resolved one would contradict the picture beside it.
      if (hop.state === "likely") {
        s += " · " + t("pkt.hop_likely", { n: candTotal(hop) }) +
          " (" + t("pkt.cand_also", { list: candNames(hop).slice(1).join(", ") }) + ")";
      }
      // Saying which node it was but not being able to place it is exactly why
      // the map shows a dashed gap here; spell that out rather than leaving
      // the reader to wonder why a named hop has no dot.
      if (m.lat == null || m.lon == null) s += " — " + t("pkt.hop_nolocation");
      return s;
    }
    if (hop.state === "ambiguous") {
      return t("pkt.hop_ambiguous", { n: candTotal(hop) }) + ": " +
        candNames(hop).join(", ");
    }
    return t("pkt.hop_unknown");
  }

  // A muted second line under a derived name. The column can put its reasoning
  // in a tooltip; the panel cannot, because the panel is what a phone opens and
  // a touch screen has no hover. So everything the tooltip says has to be
  // readable here as plain text -- who else was in the running, why this one
  // came first, and how many were ruled out.
  function candNote(text) {
    var el = document.createElement("span");
    el.className = "muted small candnote";
    el.textContent = text;
    return el;
  }

  /* Fill one field of the panel from a resolved address hash.
   *
   * Returns false when there is nothing at all to say, so the caller can decide
   * what an absent answer looks like in its own row.
   */
  function fillResolved(id, res) {
    var el = document.getElementById(id);
    if (!el) return false;
    var lbl = srcLabel(res, t);
    if (!lbl) return false;
    el.textContent = "";
    // One builder for all four states, the nameless one included. It used to
    // branch, and the branch drifted: the panel said "onbekende node" where the
    // list said nothing at all. Whatever srcLabel prints in a row is what the
    // panel prints too, with the reasoning spelled out underneath instead of
    // hidden in a tooltip.
    var main = document.createElement("span");
    main.className = "src-derived";
    main.textContent = lbl.text;
    main.title = hashNote(res);
    el.appendChild(main);
    el.appendChild(candNote(lbl.title));
    return true;
  }

  // One value written the way search.py's parser reads it back. Quotes are the
  // parser's only grouping device and it has no escape for a quote inside one
  // (see _read_value), so an inner quote is dropped rather than smuggled in as
  // a clause boundary the visitor never typed.
  function queryValue(value) {
    var v = String(value).replace(/"/g, "");
    return /[\s()]/.test(v) ? '"' + v + '"' : v;
  }

  function queryClause(field, value, negate) {
    return (negate ? "-" : "") + field + ":" + queryValue(value);
  }

  // The field names the query language actually knows, handed over by the
  // archive page from search.describe_fields(). Kept as a module-level list
  // rather than threaded through every call: the buttons are rendered from four
  // different places and an extra argument at each of them would only make it
  // easier to forget the check somewhere.
  var SEARCH_FIELDS = null;

  // A Kibana-style pair beside a value: + narrows the archive query to this
  // field:value, - excludes it. Rendered only when the caller supplies a
  // handler, which is why the live page (no query bar, no query language) gets
  // none, and only for fields search.FIELDS actually knows -- a button that
  // produced "Onbekend veld" would be a trap dressed as a feature.
  function filterBtns(el, field, value, onFilter) {
    if (!el || !onFilter || value === null || value === undefined || value === "") return;
    if (SEARCH_FIELDS && SEARCH_FIELDS.indexOf(field) < 0) return;
    var wrap = document.createElement("span");
    wrap.className = "fbtns";
    [[false, "+", "arch.filter_add"], [true, "−", "arch.filter_not"]]
      .forEach(function (spec) {
        var b = document.createElement("button");
        b.type = "button";
        b.className = "fbtn";
        b.textContent = spec[1];
        b.title = t(spec[2], { q: queryClause(field, value, spec[0]) });
        b.setAttribute("aria-label", b.title);
        b.addEventListener("click", function (e) {
          // Rows are clickable too (they open the detail); a filter click is
          // about the value, not about the packet it happens to sit in.
          e.preventDefault();
          e.stopPropagation();
          onFilter(field, value, spec[0]);
        });
        wrap.appendChild(b);
      });
    el.appendChild(wrap);
  }

  /* Fill the shared fragment from one /api/v1/packets/{id} response.
   *
   * opts.showCountry -- whether this page can say anything about countries at
   *   all. The live page hides the row when the deployment has no borders to
   *   classify positions against; the archive always shows it, because it has a
   *   Land column and a country: field either way.
   * opts.onFilter -- see filterBtns; omitted on the live page.
   */
  function fillPacketDetail(d, opts) {
    opts = opts || {};
    var onFilter = opts.onFilter || null;
    txt("pkt-time", new Date(d.ts).toLocaleString() + " · " + relTime(d.ts));
    // An advert states its sender; everything else at best derives one. The
    // stated name is plain text, the derived one goes through fillResolved and
    // comes out dotted with its reasoning underneath.
    var stated = nodeLabel(d.sender, d.sender_name);
    if (stated) txt("pkt-sender", stated);
    else if (!fillResolved("pkt-sender", d.src)) txt("pkt-sender", t("pkt.sender_unknown"));
    // Which field the buttons filter on follows what the row is actually
    // showing. A sender an advert stated has a key, so sender: asks for that
    // node. A sender only derived from the address byte has no key -- sender:
    // would filter on something other than what is printed, which is why it was
    // withheld here -- but src: asks for every packet carrying that byte, which
    // is exactly what the row was derived from. Wider than "this node", and
    // honest about being wider.
    if (d.sender) {
      filterBtns(document.getElementById("pkt-sender"), "sender", d.sender, onFilter);
    } else if (d.src && d.src.hash) {
      filterBtns(document.getElementById("pkt-sender"), "src", d.src.hash, onFilter);
    }
    txt("pkt-observer", nodeLabel(d.observer, d.observer_name) || "—");
    filterBtns(document.getElementById("pkt-observer"), "observer", d.observer, onFilter);
    // The destination row only exists for packet types that name one; an empty
    // row on every ACK and advert would be noise.
    var destRow = document.getElementById("pkt-dest-row");
    destRow.hidden = !(d.dest && d.dest.hash);
    if (!destRow.hidden) {
      fillResolved("pkt-dest", d.dest);
      filterBtns(document.getElementById("pkt-dest"), "dest", d.dest.hash, onFilter);
    }
    // Whose country to show follows whose position the map used for this
    // packet: the sender's when we know it, the observer's otherwise.
    var countryRow = document.getElementById("pkt-country-row");
    var placed = d.sender_lat != null && d.sender_lon != null;
    var cc = placed ? d.sender_country : d.observer_country;
    countryRow.hidden = !opts.showCountry;
    txt("pkt-country-val", countryLabel(cc) +
        " · " + t(placed ? "pkt.country_of_sender" : "pkt.country_of_observer"));
    filterBtns(document.getElementById("pkt-country-val"), "country", cc, onFilter);
    txt("pkt-type", d.type || "—");
    filterBtns(document.getElementById("pkt-type"), "type", d.type, onFilter);
    txt("pkt-route", d.route || "—");
    filterBtns(document.getElementById("pkt-route"), "route", d.route, onFilter);
    txt("pkt-scope", scopeDetail(d));
    // scope: only, never region:. The region is part of the same sentence here
    // ("gescoped — regio 7"), and a second pair of buttons filtering on half of
    // one sentence is the kind of thing a reader clicks once and never trusts
    // again. The region facet in the sidebar covers that need.
    filterBtns(document.getElementById("pkt-scope"), "scope", d.scope, onFilter);
    // Only a scoped packet carries codes, and only the second of them could
    // ever name a region -- so the row exists to show what the frame actually
    // holds, not to be filled in with a guess when it holds nothing.
    var codesRow = document.getElementById("pkt-scope-codes-row");
    var codes = d.scope_codes;
    codesRow.hidden = !codes || codes.length < 2;
    if (!codesRow.hidden) txt("pkt-scope-codes", codes[0] + " / " + codes[1]);
    txt("pkt-snr", d.snr != null ? d.snr.toFixed(2) + " dB" : "—");
    filterBtns(document.getElementById("pkt-snr"), "snr", d.snr, onFilter);
    txt("pkt-rssi", d.rssi != null ? d.rssi + " dBm" : "—");
    filterBtns(document.getElementById("pkt-rssi"), "rssi", d.rssi, onFilter);
    txt("pkt-len", d.len != null ? d.len + " B" : "—");
    filterBtns(document.getElementById("pkt-len"), "len", d.len, onFilter);
    txt("pkt-pathlen", d.path_len != null ? String(d.path_len) : "—");
    filterBtns(document.getElementById("pkt-pathlen"), "hops", d.path_len, onFilter);
    // The size of one hop hash, which is not a property of the mesh but a choice
    // the sending node made (its hash_mode) and every forwarder kept -- 1, 2 and
    // 3 travel side by side on the same air. Shown only when there is a hop for
    // it to describe: on a packet heard straight from its sender the descriptor
    // still carries a size, but it sizes nothing, and printing it there would
    // invite exactly the reading it is meant to prevent.
    var hopSizeRow = document.getElementById("pkt-hopsize-row");
    if (hopSizeRow) {
      var hs = d.path_hash_size;
      hopSizeRow.hidden = !hs || !d.path_len;
      if (!hopSizeRow.hidden) {
        txt("pkt-hopsize", t(hs === 1 ? "pkt.hopsize_one" : "pkt.hopsize_n", { n: hs }));
      }
    }
    // Hex in byte pairs so it stays readable, and it wraps rather than
    // widening the page on a phone (see .pktraw).
    txt("pkt-raw", d.raw ? d.raw.toUpperCase().replace(/../g, "$& ").trim() : t("pkt.noraw"));
    var copyBtn = document.getElementById("pkt-raw-copy");
    if (copyBtn) {
      copyBtn.hidden = !d.raw;
      copyBtn.dataset.raw = d.raw || "";
      copyBtn.textContent = t("pkt.copy");
    }

    var list = document.getElementById("pkt-path");
    list.textContent = "";
    (d.path || []).forEach(function (h) {
      var li = document.createElement("li");
      li.className = "hop hop-" + h.state;
      var hex = document.createElement("code");
      hex.textContent = h.hash.toUpperCase();
      var label = document.createElement("span");
      label.textContent = hopLabel(h);
      li.appendChild(hex);
      li.appendChild(label);
      // The same reasoning the sender and destination rows print underneath
      // themselves, kept to the hover here: a path of eight hops with a
      // paragraph under each would drown the field it belongs to. The list
      // entry already names the candidates it ranked, so nothing is hidden --
      // only the ground for the order is.
      var why = rankNote(h);
      if (why) li.title = why;
      // path: matches on containment in the stored hop list, so the hash is the
      // right value here even when we cannot say which node it was -- "every
      // packet that went through this hop" is a question worth asking about
      // exactly the hops we could not name.
      filterBtns(li, "path", h.hash, onFilter);
      list.appendChild(li);
    });

    var notes = [];
    if (!d.path_stored) notes.push(t("pkt.path_unstored"));
    else if (!(d.path || []).length) notes.push(t("pkt.nopath"));
    if ((d.path || []).length) {
      notes.push(t("pkt.path_note"));
      if (/DIRECT/.test(d.route || "")) notes.push(t("pkt.path_note_direct"));
    }
    txt("pkt-path-note", notes.join(" "));

    var adv = document.getElementById("pkt-advert");
    adv.hidden = !d.advert;
    if (d.advert) {
      txt("pkt-adv-name", d.advert.name || "—");
      txt("pkt-adv-coords", d.advert.lat != null && d.advert.lon != null
        ? d.advert.lat.toFixed(6) + ", " + d.advert.lon.toFixed(6) : "—");
      txt("pkt-adv-type", d.advert.node_type || "—");
      txt("pkt-adv-ts", d.advert.ts
        ? new Date(d.advert.ts * 1000).toLocaleString() : "—");
    }
  }

  // Emptying is its own function rather than "fill with a blank packet": the
  // panel is shown before the fetch resolves, and last packet's numbers left
  // standing under a new title would be read as this packet's.
  function blankPacketDetail() {
    ["pkt-time", "pkt-sender", "pkt-observer", "pkt-dest", "pkt-country-val",
     "pkt-type", "pkt-route", "pkt-scope", "pkt-scope-codes", "pkt-snr",
     "pkt-rssi", "pkt-len", "pkt-pathlen", "pkt-hopsize", "pkt-raw",
     "pkt-path-note"]
      .forEach(function (id) { txt(id, ""); });
    document.getElementById("pkt-path").textContent = "";
    document.getElementById("pkt-advert").hidden = true;
    document.getElementById("pkt-scope-codes-row").hidden = true;
    document.getElementById("pkt-dest-row").hidden = true;
    var hopSizeRow = document.getElementById("pkt-hopsize-row");
    if (hopSizeRow) hopSizeRow.hidden = true;
    var copyBtn = document.getElementById("pkt-raw-copy");
    if (copyBtn) copyBtn.hidden = true;
  }

  // The raw bytes are shown spaced for reading but copied unspaced, because
  // whatever they get pasted into (a decoder, a script) wants them the way the
  // API stores them.
  (function wireRawCopy() {
    var btn = document.getElementById("pkt-raw-copy");
    if (!btn) return;
    btn.addEventListener("click", function () {
      var hex = btn.dataset.raw || "";
      if (!hex || !navigator.clipboard) return;
      navigator.clipboard.writeText(hex.toUpperCase()).then(function () {
        btn.textContent = t("pkt.copied");
        setTimeout(function () { btn.textContent = t("pkt.copy"); }, 1500);
      }).catch(function () { /* a browser that refuses leaves the bytes on screen */ });
    });
  })();

  var THEME = document.documentElement.getAttribute("data-theme") === "light" ? "light" : "dark";
  var TEXT = cssVar("--text", "#d7e2ea");
  var TEXT_MUTED = cssVar("--muted", "#7d8fa0");
  var GRID = cssVar("--chart-grid", "rgba(125, 143, 160, .12)");
  var TILE_URL = "https://{s}.basemaps.cartocdn.com/" +
    (THEME === "light" ? "light_all" : "dark_all") + "/{z}/{x}/{y}{r}.png";

  if (typeof Chart !== "undefined") {
    Chart.defaults.color = TEXT_MUTED;
    Chart.defaults.borderColor = GRID;
    Chart.defaults.font.family = "'JetBrains Mono', Consolas, monospace";
    Chart.defaults.font.size = 11;
  }

  // Theme switch: reload so charts and map tiles pick up the new palette too
  var themeBtn = document.getElementById("theme-toggle");
  if (themeBtn) {
    themeBtn.textContent = THEME === "light" ? "☾" : "☀";
    themeBtn.addEventListener("click", function () {
      localStorage.setItem("mcs-theme", THEME === "light" ? "dark" : "light");
      location.reload();
    });
  }

  // --- relative timestamps ---------------------------------------------------
  function relTime(iso) {
    var d = new Date(iso);
    if (isNaN(d)) return iso;
    var s = Math.round((Date.now() - d.getTime()) / 1000);
    if (s < 0) s = 0;
    if (s < 60) return t("time.now");
    if (s < 3600) return t("time.min", { n: Math.round(s / 60) });
    if (s < 86400) return t("time.hour", { n: Math.round(s / 3600) });
    return t("time.day", { n: Math.round(s / 86400) });
  }
  function updateTimes() {
    document.querySelectorAll("time.reltime").forEach(function (el) {
      var iso = el.getAttribute("datetime");
      el.textContent = relTime(iso);
      el.title = new Date(iso).toLocaleString();
    });
  }
  updateTimes();
  setInterval(updateTimes, 30000);

  // --- gauges (half circle with a needle) ------------------------------------
  document.querySelectorAll("[data-gauge]").forEach(function (tile) {
    var canvas = tile.querySelector("canvas");
    var ctx = canvas.getContext("2d");
    var min = parseFloat(tile.dataset.min), max = parseFloat(tile.dataset.max);
    var value = Math.min(max, Math.max(min, parseFloat(tile.dataset.value)));
    var segments = JSON.parse(tile.dataset.segments);
    var w = canvas.width, h = canvas.height, cx = w / 2, cy = h - 6, r = Math.min(w / 2 - 10, h - 14);

    function angle(v) { return Math.PI + ((v - min) / (max - min)) * Math.PI; }
    for (var i = 0; i < segments.length; i++) {
      var from = segments[i][0];
      var to = i + 1 < segments.length ? segments[i + 1][0] : max;
      ctx.beginPath();
      ctx.arc(cx, cy, r, angle(from) + 0.02, angle(to) - 0.02);
      ctx.lineWidth = 10;
      ctx.lineCap = "round";
      ctx.strokeStyle = segments[i][1] + "55";
      ctx.stroke();
    }
    // coloured arc up to the current value
    var segColor = segments[0][1];
    for (var j = 0; j < segments.length; j++) {
      if (value >= segments[j][0]) segColor = segments[j][1];
    }
    ctx.beginPath();
    ctx.arc(cx, cy, r, Math.PI, angle(value));
    ctx.lineWidth = 10;
    ctx.lineCap = "round";
    ctx.strokeStyle = segColor;
    ctx.shadowColor = segColor;
    ctx.shadowBlur = 8;
    ctx.stroke();
    ctx.shadowBlur = 0;
    var a = angle(value);
    ctx.beginPath();
    ctx.moveTo(cx, cy);
    ctx.lineTo(cx + (r - 16) * Math.cos(a), cy + (r - 16) * Math.sin(a));
    ctx.lineWidth = 2.5;
    ctx.strokeStyle = TEXT;
    ctx.stroke();
    ctx.beginPath();
    ctx.arc(cx, cy, 3.5, 0, 2 * Math.PI);
    ctx.fillStyle = TEXT;
    ctx.fill();
  });

  // --- thermometers (horizontal tube with a bulb) -----------------------------
  document.querySelectorAll("[data-thermo]").forEach(function (tile) {
    var canvas = tile.querySelector("canvas");
    var ctx = canvas.getContext("2d");
    var min = parseFloat(tile.dataset.min), max = parseFloat(tile.dataset.max);
    var raw = parseFloat(tile.dataset.value);
    var value = Math.min(max, Math.max(min, raw));
    var segments = JSON.parse(tile.dataset.segments); // [[from, colour], ...]
    var w = canvas.width, h = canvas.height;
    var bulbR = 12, y = 40, tubeH = 12;
    var x0 = 20 + bulbR, x1 = w - 16;

    function xAt(v) { return x0 + ((v - min) / (max - min)) * (x1 - x0); }
    function segColor(v) {
      var c = segments[0][1];
      for (var i = 0; i < segments.length; i++) if (v >= segments[i][0]) c = segments[i][1];
      return c;
    }
    function tube(toX, r) {
      ctx.beginPath();
      ctx.moveTo(x0, y - r);
      ctx.lineTo(toX - r, y - r);
      ctx.arc(toX - r, y, r, -Math.PI / 2, Math.PI / 2);
      ctx.lineTo(x0, y + r);
      ctx.closePath();
    }

    // dark tube and bulb as the background
    ctx.fillStyle = cssVar("--thermo-track", "#1e2b3a");
    tube(x1, tubeH / 2 + 2); ctx.fill();
    ctx.beginPath(); ctx.arc(20, y, bulbR + 2, 0, 2 * Math.PI); ctx.fill();

    // coloured fill up to the current value
    var color = segColor(value);
    ctx.fillStyle = color;
    ctx.shadowColor = color; ctx.shadowBlur = 8;
    ctx.beginPath(); ctx.arc(20, y, bulbR - 2, 0, 2 * Math.PI); ctx.fill();
    var fx = Math.max(xAt(value), x0 + 4);
    tube(fx, tubeH / 2 - 2); ctx.fill();
    ctx.shadowBlur = 0;

    // scale ticks with labels
    ctx.strokeStyle = TEXT_MUTED;
    ctx.fillStyle = TEXT_MUTED;
    ctx.font = "9px 'JetBrains Mono', monospace";
    ctx.textAlign = "center";
    [-20, 0, 20, 40, 60, 80].forEach(function (tick) {
      if (tick < min || tick > max) return;
      var tx = xAt(tick);
      ctx.beginPath();
      ctx.moveTo(tx, y + tubeH / 2 + 4);
      ctx.lineTo(tx, y + tubeH / 2 + 9);
      ctx.stroke();
      ctx.fillText(String(tick), tx, y + tubeH / 2 + 20);
    });
  });

  // --- collapsible sections (per-visitor preference in localStorage) ----------
  document.querySelectorAll("section.collapsible").forEach(function (sec) {
    var key = "mcs-collapse:" + sec.dataset.ckey;
    try {
      if (localStorage.getItem(key) === "1") sec.classList.add("collapsed");
    } catch (e) { /* localStorage can be blocked */ }
    sec.querySelector("h2.sec-toggle").addEventListener("click", function () {
      sec.classList.toggle("collapsed");
      try {
        if (sec.classList.contains("collapsed")) localStorage.setItem(key, "1");
        else localStorage.removeItem(key);
      } catch (e) { /* nothing to do */ }
    });
  });

  // --- admin: draggable layout -------------------------------------------------
  var layoutList = document.getElementById("layout-list");
  if (layoutList) {
    var dragging = null;
    layoutList.querySelectorAll("li").forEach(function (li) {
      li.addEventListener("dragstart", function () { dragging = li; li.classList.add("dragging"); });
      li.addEventListener("dragend", function () { li.classList.remove("dragging"); dragging = null; });
    });
    layoutList.addEventListener("dragover", function (e) {
      e.preventDefault();
      if (!dragging) return;
      var after = null;
      layoutList.querySelectorAll("li:not(.dragging)").forEach(function (li) {
        var rect = li.getBoundingClientRect();
        if (e.clientY < rect.top + rect.height / 2 && after === null) after = li;
      });
      if (after) layoutList.insertBefore(dragging, after);
      else layoutList.appendChild(dragging);
    });
    document.getElementById("layout-form").addEventListener("submit", function () {
      var out = [];
      layoutList.querySelectorAll("li").forEach(function (li) {
        out.push({ key: li.dataset.key, visible: li.querySelector("input[type=checkbox]").checked });
      });
      document.getElementById("layout-json").value = JSON.stringify(out);
    });
  }

  // --- shared chart builder ---------------------------------------------------
  function lineChart(canvas, datasets, unit, showLegend, hours) {
    // Fixed time window: stops the axis collapsing to milliseconds when only a
    // couple of points exist
    var now = Date.now();
    return new Chart(canvas, {
      type: "line",
      data: { datasets: datasets },
      options: {
        responsive: true, maintainAspectRatio: false, animation: false,
        interaction: { mode: "nearest", axis: "x", intersect: false },
        scales: {
          x: {
            type: "time",
            min: hours ? now - hours * 3600 * 1000 : undefined,
            max: hours ? now : undefined,
            time: { tooltipFormat: "dd/MM HH:mm" },
            ticks: { maxTicksLimit: 7 },
            grid: { display: false },
          },
          y: { title: { display: !!unit, text: unit || "" } },
        },
        plugins: {
          legend: { display: !!showLegend, labels: { boxWidth: 14, boxHeight: 2 } },
          tooltip: {
            backgroundColor: cssVar("--tooltip-bg", "#0b0f14"), borderColor: cssVar("--card-edge", "#1e2b3a"), borderWidth: 1,
            titleColor: TEXT, bodyColor: TEXT, padding: 10,
          },
        },
      },
    });
  }

  // Colour zones per metric (same as the gauges), as a gradient over the y-axis
  function zoneFor(metric) {
    if (window.MCS && window.MCS.zones && window.MCS.zones[metric]) return window.MCS.zones[metric];
    if (/^neighbor_[0-9a-f]{6}$/.test(metric || "")) {
      return { min: -25, max: 15, segments: [[-25, "#ff5c5c"], [-10, "#ffb454"], [0, "#35e08c"]] };
    }
    return null;
  }
  function hexToRgba(hex, alpha) {
    return "rgba(" + parseInt(hex.slice(1, 3), 16) + "," + parseInt(hex.slice(3, 5), 16) +
           "," + parseInt(hex.slice(5, 7), 16) + "," + alpha + ")";
  }
  function zoneGradient(zone, alpha) {
    // Scriptable Chart.js colour: a vertical gradient with soft transitions
    // between the zone colours (stops at the middle of each zone).
    return function (context) {
      var chart = context.chart;
      var area = chart.chartArea;
      var yScale = chart.scales.y;
      var last = zone.segments[zone.segments.length - 1][1];
      if (!area || !yScale) return alpha ? hexToRgba(last, alpha) : last;
      var g = chart.ctx.createLinearGradient(0, area.bottom, 0, area.top);
      var lastT = -1;
      for (var i = 0; i < zone.segments.length; i++) {
        var from = zone.segments[i][0];
        var to = i + 1 < zone.segments.length ? zone.segments[i + 1][0] : zone.max;
        var mid = (from + to) / 2;
        var px = yScale.getPixelForValue(mid);
        var t = (area.bottom - px) / (area.bottom - area.top);
        t = Math.min(1, Math.max(0, t));
        if (t <= lastT) t = Math.min(1, lastT + 0.001);
        lastT = t;
        var color = zone.segments[i][1];
        g.addColorStop(t, alpha ? hexToRgba(color, alpha) : color);
      }
      return g;
    };
  }

  function dataset(label, points, i, fill, zone) {
    var stroke = zone ? zoneGradient(zone) : PALETTE[i % PALETTE.length];
    var bg = zone ? zoneGradient(zone, 0.22) : PALETTE[i % PALETTE.length] + "26";
    return {
      label: label,
      data: points.map(function (p) { return { x: p[0], y: p[1] }; }),
      borderColor: stroke,
      backgroundColor: bg,
      borderWidth: 2,
      /* show markers while data is sparse, a clean line once it is not */
      pointRadius: points.length < 60 ? 3 : 0,
      pointBackgroundColor: stroke,
      pointBorderColor: stroke,
      pointHitRadius: 12, tension: 0.25,
      fill: !!fill,
      borderDash: !zone && i === 1 ? [6, 3] : undefined,  /* dash the 2nd series (colour-blind safe) */
    };
  }

  function fetchHistory(metric, hours) {
    return fetch("/api/v1/repeaters/" + encodeURIComponent(window.MCS.slug) +
                 "/history?metric=" + encodeURIComponent(metric) + "&hours=" + hours)
      .then(function (r) { return r.json(); });
  }

  // Metric labels arrive from the server in Dutch; the catalogue ones have a
  // translation, anything a node invented keeps the label it came with.
  function metricLabel(metric, fallback) {
    return t("metric." + metric) === "metric." + metric ? fallback : t("metric." + metric);
  }

  // --- fixed charts -----------------------------------------------------------
  document.querySelectorAll("[data-chart]").forEach(function (canvas) {
    if (typeof Chart === "undefined") return;
    var cfg = JSON.parse(canvas.dataset.chart);
    Promise.all(cfg.metrics.map(function (m) { return fetchHistory(m, cfg.hours); }))
      .then(function (results) {
        var single = cfg.metrics.length === 1;
        var datasets = results.map(function (res, i) {
          return dataset(metricLabel(cfg.metrics[i], cfg.labels[i]), res.points, i, single,
                         single ? zoneFor(cfg.metrics[i]) : null);
        });
        lineChart(canvas, datasets, cfg.unit, cfg.metrics.length > 1, cfg.hours);
      });
  });

  // --- history modal ----------------------------------------------------------
  var modal = document.getElementById("metric-modal");
  if (modal) {
    var modalTitle = document.getElementById("modal-title");
    var modalCanvas = document.getElementById("modal-canvas");
    var modalEmpty = document.getElementById("modal-empty");
    var rangeBtns = modal.querySelectorAll(".rangebtns button");
    var modalChart = null;
    var current = null; // {metric, label, unit}

    function loadModal(hours) {
      rangeBtns.forEach(function (b) {
        b.classList.toggle("active", parseInt(b.dataset.hours, 10) === hours);
      });
      fetchHistory(current.metric, hours).then(function (res) {
        if (modalChart) { modalChart.destroy(); modalChart = null; }
        var has = res.points && res.points.length > 0;
        modalEmpty.hidden = has;
        modalCanvas.parentElement.style.display = has ? "" : "none";
        if (!has) return;
        modalChart = lineChart(modalCanvas,
                               [dataset(current.label, res.points, 0, true, zoneFor(current.metric))],
                               current.unit, false, hours);
      });
    }

    function openModal(metric, label, unit) {
      current = { metric: metric, label: label, unit: unit };
      modalTitle.textContent = label;
      modal.hidden = false;
      document.body.style.overflow = "hidden";
      if (window.mcsModalMap) window.mcsModalMap(metric);
      loadModal((window.MCS && window.MCS.defaultHours) || 24);
    }
    function closeModal() {
      modal.hidden = true;
      document.body.style.overflow = "";
      if (modalChart) { modalChart.destroy(); modalChart = null; }
      if (window.mcsModalMap) window.mcsModalMap(null);
    }

    rangeBtns.forEach(function (b) {
      b.addEventListener("click", function () { loadModal(parseInt(b.dataset.hours, 10)); });
    });
    modal.querySelector(".modal-close").addEventListener("click", closeModal);
    modal.querySelector(".modal-backdrop").addEventListener("click", closeModal);
    document.addEventListener("keydown", function (e) {
      if (e.key === "Escape" && !modal.hidden) closeModal();
    });

    // clickable tiles
    document.querySelectorAll(".tile.clickable").forEach(function (tile) {
      tile.addEventListener("click", function () {
        openModal(tile.dataset.metric,
                  metricLabel(tile.dataset.metric, tile.dataset.label),
                  tile.dataset.unit || "");
      });
    });
    // clickable neighbours
    document.querySelectorAll("tr.nbrow").forEach(function (row) {
      row.addEventListener("click", function () {
        var cell = row.querySelector(".nbname");
        openModal(row.dataset.metric,
                  t("nb.link_snr", { name: cell ? cell.textContent : row.dataset.prefix }),
                  row.dataset.unit || "dB");
      });
    });
    window.mcsOpenModal = openModal;
  }

  // --- neighbour table: sorting (stored locally) and auto-refresh -------------
  var nbTable = document.querySelector("table.neighbors");
  if (nbTable && window.MCS) {
    var nbBody = nbTable.querySelector("tbody");
    var sortState = { key: "snr", dir: -1 };
    try {
      var saved = JSON.parse(localStorage.getItem("mcs-nbsort"));
      if (saved && ["name", "prefix", "snr", "seen"].indexOf(saved.key) !== -1) sortState = saved;
    } catch (e) { /* nothing to do */ }

    function applySort() {
      var rows = Array.prototype.slice.call(nbBody.querySelectorAll("tr.nbrow"));
      rows.sort(function (a, b) {
        var av, bv;
        if (sortState.key === "name") { av = a.dataset.name; bv = b.dataset.name; }
        else if (sortState.key === "prefix") { av = a.dataset.prefix; bv = b.dataset.prefix; }
        else if (sortState.key === "seen") { av = a.dataset.seen; bv = b.dataset.seen; }
        else { av = parseFloat(a.dataset.snr); bv = parseFloat(b.dataset.snr); }
        if (av < bv) return -sortState.dir;
        if (av > bv) return sortState.dir;
        return 0;
      });
      rows.forEach(function (r) { nbBody.appendChild(r); });
      nbTable.querySelectorAll("th.sortable").forEach(function (th) {
        th.classList.remove("sort-asc", "sort-desc");
        if (th.dataset.sort === sortState.key) {
          th.classList.add(sortState.dir === 1 ? "sort-asc" : "sort-desc");
        }
      });
    }

    nbTable.querySelectorAll("th.sortable").forEach(function (th) {
      th.addEventListener("click", function () {
        var key = th.dataset.sort;
        if (sortState.key === key) {
          sortState.dir = -sortState.dir;
        } else {
          // sensible default: name/prefix ascending, SNR and last-heard descending
          sortState = { key: key, dir: (key === "name" || key === "prefix") ? 1 : -1 };
        }
        try { localStorage.setItem("mcs-nbsort", JSON.stringify(sortState)); } catch (e) { /* nothing to do */ }
        applySort();
      });
    });
    applySort();

    // refresh neighbour data every minute and update the cells in place
    function refreshNeighbors() {
      fetch("/api/v1/repeaters/" + encodeURIComponent(window.MCS.slug))
        .then(function (r) { return r.json(); })
        .then(function (d) {
          (d.neighbors || []).forEach(function (n) {
            var row = nbBody.querySelector('tr.nbrow[data-prefix="' + n.prefix + '"]');
            if (!row) return; // a new neighbour shows up on the next page load
            row.dataset.snr = n.snr != null ? n.snr : -999;
            row.dataset.seen = n.last_seen || "";
            var cells = row.children; // chevron, name, prefix, snr, bar, time
            if (n.snr != null) {
              cells[3].textContent = n.snr.toFixed(2);
              cells[3].className = "num " +
                (n.snr >= 0 ? "snr-good" : n.snr >= -10 ? "snr-ok" : "snr-bad");
              var bar = row.querySelector(".snrbar i");
              if (bar) bar.style.width = Math.max(4, Math.min(100, (n.snr + 20) / 35 * 100)) + "%";
            }
            var t = row.querySelector("time.reltime");
            if (t && n.last_seen) t.setAttribute("datetime", n.last_seen);
          });
          updateTimes();
          applySort();
        })
        .catch(function () { /* try again in a minute */ });
    }
    setInterval(refreshNeighbors, 60000);
  }

  // --- link map ---------------------------------------------------------------
  function snrColor(snr) {
    if (snr == null) return "#7d8fa0";
    if (snr >= 0) return "#35e08c";
    if (snr >= -10) return "#ffb454";
    return "#ff5c5c";
  }
  var mapDataPromise = null;
  function getMapData() {
    if (!mapDataPromise) {
      mapDataPromise = fetch("/api/v1/repeaters/" + encodeURIComponent(window.MCS.slug) + "/map")
        .then(function (r) { return r.json(); });
    }
    return mapDataPromise;
  }

  var linkmapEl = document.getElementById("linkmap");
  if (linkmapEl && typeof L !== "undefined" && window.MCS) {
    getMapData().then(function (d) {
      if (!d.repeater) {
        linkmapEl.innerHTML = '<p class="muted" style="padding:1rem"></p>';
        linkmapEl.firstChild.textContent = t("map.nolocation");
        return;
      }
      var map = L.map(linkmapEl, { scrollWheelZoom: false });
      L.tileLayer(TILE_URL, {
        attribution: "&copy; OpenStreetMap &copy; CARTO", maxZoom: 19,
      }).addTo(map);
      var home = [d.repeater.lat, d.repeater.lon];
      L.circleMarker(home, { radius: 8, color: "#4cc9f0", weight: 2, fillColor: "#4cc9f0", fillOpacity: 1 })
        .addTo(map).bindTooltip(d.repeater.name, { direction: "top" });
      var bounds = [home];
      var labelTips = [];
      d.links.forEach(function (l) {
        var color = snrColor(l.snr);
        var label = (l.name || l.prefix.toUpperCase()) +
                    (l.snr != null ? " · " + l.snr.toFixed(2) + " dB" : "");
        var line = L.polyline([home, [l.lat, l.lon]], { color: color, weight: 2, opacity: 0.75 }).addTo(map);
        line.bindTooltip(label, { sticky: true });
        var marker = L.circleMarker([l.lat, l.lon], {
          radius: 5, color: color, weight: 1.5, fillColor: color, fillOpacity: 0.9,
        }).addTo(map);
        marker.bindTooltip(label, { direction: "top" });
        function open() {
          if (window.mcsOpenModal) {
            window.mcsOpenModal("neighbor_" + l.prefix,
                                t("nb.link_snr", { name: l.name || l.prefix.toUpperCase() }), "dB");
          }
        }
        line.on("click", open);
        marker.on("click", open);
        labelTips.push({ marker: marker, text: l.snr != null ? l.snr.toFixed(1) : "?" });
        bounds.push([l.lat, l.lon]);
      });
      map.fitBounds(bounds, { padding: [30, 30] });

      // legend
      var legend = L.control({ position: "bottomright" });
      legend.onAdd = function () {
        var div = L.DomUtil.create("div", "maplegend");
        div.innerHTML = "<strong></strong>" +
          '<span><i style="background:#35e08c"></i> <em></em></span>' +
          '<span><i style="background:#ffb454"></i> <em></em></span>' +
          '<span><i style="background:#ff5c5c"></i> <em></em></span>';
        div.querySelector("strong").textContent = t("map.legend");
        var tips = [t("map.legend_good"), t("map.legend_ok"), t("map.legend_bad")];
        div.querySelectorAll("em").forEach(function (em, i) { em.textContent = tips[i]; });
        return div;
      };
      legend.addTo(map);

      var note = document.getElementById("map-note");
      if (note && d.unlocated > 0) {
        var link = document.createElement("a");
        link.href = "#";
        link.textContent = t("map.unlocated", { n: d.unlocated }) + " ▸";
        note.appendChild(link);
        var list = document.createElement("div");
        list.className = "map-missing";
        list.hidden = true;
        list.textContent = t("map.unlocated_intro") +
                           (d.unlocated_names || []).join(" · ");
        note.parentElement.parentElement.insertBefore(list, note.parentElement.nextSibling);
        link.addEventListener("click", function (e) {
          e.preventDefault();
          list.hidden = !list.hidden;
          link.textContent = link.textContent.slice(0, -1) + (list.hidden ? "▸" : "▾");
        });
      }
      // toggleable SNR labels on the nodes
      var toggle = document.getElementById("map-labels");
      if (toggle) {
        toggle.addEventListener("change", function () {
          labelTips.forEach(function (t) {
            t.marker.unbindTooltip();
            if (toggle.checked) {
              t.marker.bindTooltip(t.text + " dB", {
                permanent: true, direction: "top", className: "snrlabel", offset: [0, -4],
              });
            } else {
              t.marker.bindTooltip(t.text + " dB", { direction: "top" });
            }
          });
        });
      }
    });
  }

  // --- node detail, behind a dot on the live map --------------------------------
  // Fills templates/_node_detail.html, and lives at the top level next to
  // fillPacketDetail for the same reason that one does: the archive already
  // resolves senders and observers to these very nodes, so the day it wants to
  // show one it can include the fragment and call this -- instead of growing a
  // second rendering that would have to be taught the same honesty rules again,
  // and would eventually be taught one of them wrongly.
  //
  // Those rules, in this panel: a figure that was counted is printed plainly, a
  // figure that was inferred carries a dotted underline with the reasoning in
  // its title, and something nobody ever told us is written out as unknown
  // rather than dropped. Dropping it is the tempting one and the wrong one -- a
  // missing row reads as "does not apply", an empty row reads as zero, and
  // neither of those is what "we do not know" means.

  // The visible half of an inference. .src-derived is the packet panel's own
  // class for a value that was worked out rather than stated; reused here
  // rather than copied under a node- name, because the two must never end up
  // looking different -- the reader is being told the same thing.
  function markDerived(id, title) {
    var el = document.getElementById(id);
    if (!el) return;
    el.classList.add("src-derived");
    el.title = title;
  }

  // Absolute first, relative second. The absolute time is what someone
  // correlating with another log needs; the relative one is what makes "is this
  // node still alive" answerable at a glance.
  function whenText(iso) {
    return iso ? new Date(iso).toLocaleString() + " · " + relTime(iso)
               : t("node.unknown");
  }

  function nodeRow(id, show) {
    var row = document.getElementById(id);
    if (row) row.hidden = !show;
    return !!show;
  }

  // One entry in the observer or neighbour list: who, one headline number, and
  // the measurements behind it on a second line. Two lines rather than one wide
  // row because this panel is 320 px wide on a phone, and a single line would
  // either wrap into an unreadable block or lose the numbers to an ellipsis.
  function nodeListItem(who, num, meta, href) {
    var li = document.createElement("li");
    var top = document.createElement("span");
    top.className = "nodelist-top";
    var name = document.createElement(href ? "a" : "span");
    name.className = "nodelist-who";
    name.textContent = who;
    if (href) name.href = href;
    top.appendChild(name);
    if (num) {
      var n = document.createElement("span");
      n.className = "nodelist-num";
      n.textContent = num;
      top.appendChild(n);
    }
    li.appendChild(top);
    if (meta) {
      var m = document.createElement("span");
      m.className = "nodelist-meta";
      m.textContent = meta;
      li.appendChild(m);
    }
    return li;
  }

  /* Fill the shared fragment from one /api/v1/nodes/{prefix} response.
   *
   * opts.showCountry -- whether this deployment can say anything about
   *   countries at all, exactly as fillPacketDetail uses it.
   */
  function fillNodeDetail(d, opts) {
    opts = opts || {};
    txt("node-name", d.name || t("node.name_unknown"));
    // The full key prefix when a node advertised itself, the six-character map
    // key when all we ever got was a contact push. Both are prefixes, never a
    // whole public key, and the title says so -- the panel's headline field is
    // the last place to let a reader believe they are looking at an identity
    // they could verify a signature against.
    txt("node-key", (d.key_prefix || d.prefix).toUpperCase());
    markDerived("node-key", t("node.key_why"));
    txt("node-nodetype", d.node_type || t("node.unknown"));
    if (nodeRow("node-country-row", opts.showCountry)) {
      txt("node-country", countryLabel(d.country));
    }
    // A node without coordinates is not left off this panel: half the contacts
    // this site knows have never advertised a position, and a blank where the
    // position should be would read as an oversight rather than as the fact it
    // is. It is also why such a node has no dot on the map -- worth saying in
    // the one place someone might wonder.
    txt("node-position", d.lat != null && d.lon != null
      ? d.lat.toFixed(6) + ", " + d.lon.toFixed(6)
      : t("node.position_unknown"));
    txt("node-updated", whenText(d.updated));

    // --- the tracked-repeater block ------------------------------------------
    var rep = d.repeater;
    document.getElementById("node-rep").hidden = !rep;
    if (rep) {
      txt("node-rep-status", t(rep.online ? "node.rep_online" : "node.rep_offline") +
          " · " + whenText(rep.last_seen));
      txt("node-rep-battery", rep.battery_percentage != null
        ? Math.round(rep.battery_percentage) + " %" : t("node.unknown"));
      txt("node-rep-uptime", rep.uptime != null
        ? t("node.rep_uptime_v", { n: rep.uptime.toFixed(1) }) : t("node.unknown"));
      var link = document.getElementById("node-rep-link");
      link.href = rep.url;
      link.textContent = t("node.rep_link");
    }

    // --- traffic --------------------------------------------------------------
    var win = d.window || {};
    txt("node-window", win.oldest
      ? t("node.window", { days: win.days, oldest: whenText(win.oldest) })
      : t("node.window_empty"));

    var sent = d.sent || { total: 0, observers: [], types: [], scopes: [] };
    txt("node-sent", sent.total
      ? t("node.sent_n", { n: sent.total }) : t("node.sent_none"));
    // Always derived, even when the number is exact. What is inferred is not the
    // count but its meaning: this is everything provably from this node, which
    // is not the same as everything it sent, and a reader who is not told that
    // will read the smaller number as the second thing.
    markDerived("node-sent", t("node.sent_why"));

    // One timestamp when everything arrived in the same second, two otherwise.
    // "X to X" is not more precise than "X", only longer, and a range that
    // repeats itself invites the reader to look for a difference there is none
    // of.
    if (nodeRow("node-span-row", !!sent.first)) {
      txt("node-span", sent.first === sent.last ? whenText(sent.first)
        : t("node.span_v", { first: whenText(sent.first),
                             last: whenText(sent.last) }));
    }
    if (nodeRow("node-hops-row", sent.hops_min != null)) {
      txt("node-hops", t("node.hops_v", { n: sent.hops_min }));
      markDerived("node-hops", t("node.hops_why"));
    }
    if (nodeRow("node-types-row", !!(sent.types || []).length)) {
      txt("node-types", sent.types.map(function (x) {
        return (x.type || t("node.unknown")) + " " + x.count + "×";
      }).join(" · "));
    }
    if (nodeRow("node-scopes-row", !!(sent.scopes || []).length)) {
      txt("node-scopes", sent.scopes.map(function (x) {
        return (x.scope ? t("scope." + x.scope) : t("node.unknown")) +
          " " + x.count + "×";
      }).join(" · "));
    }

    // How often this node's key turns up as a hop in somebody else's path. A
    // ceiling, and how much of a ceiling depends on how crowded its first key
    // byte is -- so the panel says which of the two situations it is in rather
    // than attaching the same vague warning to both.
    var hop = d.as_hop || { packets: 0, siblings: 0 };
    txt("node-ashop", hop.packets ? t("node.ashop_n", { n: hop.packets })
                                  : t("node.ashop_none"));
    markDerived("node-ashop", hop.siblings > 1
      ? t("node.ashop_why", { n: hop.siblings - 1 })
      : t("node.ashop_why_alone"));

    if (nodeRow("node-heard-row", !!d.heard)) {
      txt("node-heard", t("node.heard_v", { n: d.heard.total,
                                            s: d.heard.senders }));
    }

    // --- who hears this node ---------------------------------------------------
    var obs = document.getElementById("node-observers");
    obs.textContent = "";
    (sent.observers || []).forEach(function (o) {
      var meta = [];
      if (o.snr_avg != null) {
        meta.push(t("node.obs_snr", { avg: o.snr_avg.toFixed(2),
                                      best: o.snr_best.toFixed(2) }));
      }
      if (o.rssi_avg != null) meta.push(t("node.obs_rssi", { v: Math.round(o.rssi_avg) }));
      if (o.hops_min != null) meta.push(t("node.obs_hops", { n: o.hops_min }));
      meta.push(whenText(o.last));
      obs.appendChild(nodeListItem(
        nodeLabel(o.prefix, o.name) || o.prefix.toUpperCase(),
        o.count + "×", meta.join(" · ")));
    });
    txt("node-observers-note", (sent.observers || []).length
      ? t("node.obs_note") : t("node.obs_none"));

    // --- neighbour relations ----------------------------------------------------
    // Both directions in one list, each entry saying which way round it is. They
    // are not symmetric and must not be presented as if they were: a repeater
    // hearing this node is a measurement that repeater published, and this node
    // hearing others exists only for the handful of repeaters this site follows.
    var links = document.getElementById("node-links");
    links.textContent = "";
    (d.neighbor_of || []).forEach(function (r) {
      links.appendChild(nodeListItem(
        t("node.link_hears", { r: r.name }),
        r.snr != null ? r.snr.toFixed(2) + " dB" : "",
        whenText(r.last_seen), r.url));
    });
    ((rep && rep.neighbors) || []).forEach(function (n) {
      links.appendChild(nodeListItem(
        t("node.link_hears_back", { n: nodeLabel(n.prefix, n.name) || n.prefix.toUpperCase() }),
        n.snr != null ? n.snr.toFixed(2) + " dB" : "",
        whenText(n.last_seen)));
    });
    var linkNotes = [];
    if (!links.children.length) linkNotes.push(t("node.link_none"));
    else linkNotes.push(t("node.link_note"));
    if (rep && rep.neighbors_capped) linkNotes.push(t("node.link_capped"));
    txt("node-links-note", linkNotes.join(" "));
  }

  // Emptied rather than refilled with a blank node, for the reason the packet
  // panel is: the panel is shown before the fetch resolves, and the previous
  // node's numbers left standing under a new name would be read as this one's.
  // The dotted underlines have to go with them -- a stale "derived" mark on an
  // empty field is a claim about nothing.
  function blankNodeDetail() {
    ["node-name", "node-key", "node-nodetype", "node-country", "node-position",
     "node-updated", "node-window", "node-sent", "node-span", "node-hops",
     "node-types", "node-scopes", "node-ashop", "node-heard",
     "node-observers-note", "node-links-note"].forEach(function (id) {
      var el = document.getElementById(id);
      if (!el) return;
      el.textContent = "";
      el.classList.remove("src-derived");
      el.removeAttribute("title");
    });
    ["node-rep", "node-span-row", "node-hops-row", "node-types-row",
     "node-scopes-row", "node-heard-row", "node-country-row"].forEach(function (id) {
      var el = document.getElementById(id);
      if (el) el.hidden = true;
    });
    ["node-observers", "node-links"].forEach(function (id) {
      var el = document.getElementById(id);
      if (el) el.textContent = "";
    });
  }

  // --- live packet map (public home page) -------------------------------------
  // Colour per payload type, so a burst of adverts is distinguishable from
  // message traffic at a glance without reading the feed.
  var PKT_COLORS = {
    ADVERT: "#35e08c", TXT_MSG: "#4cc9f0", GRP_TXT: "#e06c9f", GRP_DATA: "#e06c9f",
    ACK: "#7d8fa0", PATH: "#ffb454", TRACE: "#c77dff", REQ: "#e8913a",
    RESPONSE: "#e8913a", ANON_REQ: "#e8913a",
  };
  var FLASH_MS = 1600;
  var POLL_MS = 4000;
  var FEED_MAX = 25;
  // A quiet mesh sends a handful of packets per poll; a storm could send
  // hundreds. Animating them all would only produce an unreadable blur.
  var FLASH_MAX_PER_POLL = 40;
  // A packet older than this is backlog, not live traffic. The server already
  // withholds the animation flag from the very first poll, but a tab that sat
  // hidden for an hour catches up through an ordinary poll, and flashing that
  // pile on return would present stale receptions as happening right now. The
  // cut-off is judged per packet on its own timestamp rather than per batch,
  // because a catch-up batch legitimately ends in genuinely fresh packets that
  // deserve their flash. Two minutes sits far above one poll interval plus any
  // reasonable server/browser clock skew, and far below any absence long
  // enough to pile up a misleading backlog.
  var FLASH_STALE_MS = 120000;
  // Packets kept in memory behind the visible feed, so switching the filter can
  // re-render from traffic that already arrived instead of only from what comes
  // next. Bounded because this page is left open for hours.
  var FEED_BUFFER = 300;
  var PATH_COLOR = "#c77dff";

  var livemapEl = document.getElementById("livemap");
  if (livemapEl && typeof L !== "undefined") {
    var lmap = L.map(livemapEl, { scrollWheelZoom: false });
    // A view immediately, before a single layer is added, and this is load
    // bearing. Leaflet queues a layer's onAdd until the map has a centre and a
    // zoom. With several layers waiting, they run in the order they were asked
    // for -- and the shared SVG renderer is not one of them: it gets registered
    // in the map's layer list by whichever vector layer asks for it first, but
    // its own onAdd is queued behind that layer. A LayerGroup queued earlier
    // then runs, asks for the renderer, finds it already registered, and so
    // never initialises it; its polylines clip against a renderer that has no
    // bounds yet and throw. That is not a hypothetical: with the heat map
    // switched on it happened on every load, and because the exception escaped
    // through fitBounds into the packet poll's catch it took the node markers
    // and the packet feed down with it. Nothing is ever queued if the map has a
    // view from the start. The world view lasts only until the first packet
    // response frames the map on the real mesh (openingView), and it
    // deliberately hard codes no location of its own. Because it is a
    // placeholder and not an answer, everything that reads the current view
    // waits for that framing rather than acting on this -- see viewSet.
    lmap.setView([0, 0], 2);
    L.tileLayer(TILE_URL, { attribution: "&copy; OpenStreetMap &copy; CARTO", maxZoom: 19 })
      .addTo(lmap);
    var feedEl = document.getElementById("livefeed");
    var feedEmptyEl = document.getElementById("livefeed-empty");
    var liveCardEl = document.getElementById("livecard");
    var feedHeadEl = document.querySelector(".feedhead");
    var countEl = document.getElementById("live-count");
    var lastId = 0;
    var seen = [];        // {t, p} per reception, for the "last 5 minutes" counter
    var polling = false;
    var recent = [];      // newest first; the data behind the rendered feed
    var openId = null;    // id of the packet whose detail panel is open, if any

    function flash(lat, lon, color) {
      var ring = L.circleMarker([lat, lon], {
        radius: 4, color: color, weight: 2, fillColor: color, fillOpacity: 0.45,
      }).addTo(lmap);
      var start = null;
      function step(now) {
        if (start === null) start = now;
        var k = (now - start) / FLASH_MS;
        if (k >= 1) { lmap.removeLayer(ring); return; }
        ring.setRadius(4 + k * 26);
        ring.setStyle({ opacity: 1 - k, fillOpacity: 0.35 * (1 - k) });
        requestAnimationFrame(step);
      }
      requestAnimationFrame(step);
    }

    // --- packets travelling along their path ----------------------------------
    // One dot per packet, walking sender -> hops -> observer. Everything here is
    // written for a mesh that is about to get much noisier: a single rAF loop
    // drives every dot (not a timer each), the number in flight is capped, the
    // oldest is dropped rather than queued, and a hidden tab runs nothing at all.
    var MOTION_KEY = "mcs-pktmotion";
    var TRAVEL_MS = 2800;        // sender to observer, whole route
    var MAX_TRAVELERS = 18;
    var motionEl = document.getElementById("pkt-motion");
    var reduceMotion = window.matchMedia &&
      window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    var motionWanted = true;
    try {
      var savedMotion = localStorage.getItem(MOTION_KEY);
      if (savedMotion !== null) motionWanted = savedMotion === "1";
    } catch (e) { /* blocked */ }
    if (motionEl) {
      motionEl.checked = motionWanted && !reduceMotion;
      // A visitor who asked their system for less movement gets the flash, and
      // is told why the switch does nothing rather than left to wonder.
      motionEl.disabled = reduceMotion;
      if (reduceMotion) {
        var lbl = motionEl.closest("label");
        if (lbl) lbl.title = t("live.motion_reduced");
      }
      motionEl.addEventListener("change", function () {
        motionWanted = motionEl.checked;
        try { localStorage.setItem(MOTION_KEY, motionWanted ? "1" : "0"); } catch (e) { /* blocked */ }
        if (!motionWanted) clearTravelers();
      });
    }
    function animating() { return motionWanted && !reduceMotion; }

    var travelers = [];
    var travelRaf = null;

    // Route for the dot: the stops we can actually place, in order. ``certain``
    // is false as soon as one stop is missing -- an unplaceable hop, or a sender
    // no advert ever named -- and the route line is then drawn dashed and faint.
    // The dot still crosses that stretch, because the packet demonstrably did;
    // what we do not claim is which way round it went.
    function travelRoute(p) {
      var pts = [], certain = true;
      if (p.sender_lat != null && p.sender_lon != null) pts.push([p.sender_lat, p.sender_lon]);
      else certain = false;
      (p.path || []).forEach(function (h) {
        if (h.lat != null && h.lon != null) pts.push([h.lat, h.lon]);
        else certain = false;
      });
      if (p.observer_lat != null && p.observer_lon != null) pts.push([p.observer_lat, p.observer_lon]);
      else certain = false;
      return pts.length > 1 ? { pts: pts, certain: certain } : null;
    }

    function dropTraveler(tr) {
      lmap.removeLayer(tr.dot);
      lmap.removeLayer(tr.line);
    }

    function clearTravelers() {
      travelers.forEach(dropTraveler);
      travelers = [];
      if (travelRaf !== null) { cancelAnimationFrame(travelRaf); travelRaf = null; }
    }

    function travelStep(now) {
      travelRaf = null;
      for (var i = travelers.length - 1; i >= 0; i--) {
        var tr = travelers[i];
        var k = (now - tr.start) / TRAVEL_MS;
        if (k >= 1) {
          dropTraveler(tr);
          travelers.splice(i, 1);
          continue;
        }
        // Walk the cumulative segment lengths so the dot keeps a steady speed
        // over the whole route instead of racing through the short legs.
        var want = k * tr.total, acc = 0, j = 0;
        while (j < tr.legs.length - 1 && acc + tr.legs[j] < want) { acc += tr.legs[j]; j++; }
        var f = tr.legs[j] ? (want - acc) / tr.legs[j] : 0;
        var a = tr.pts[j], b = tr.pts[j + 1];
        tr.dot.setLatLng([a[0] + (b[0] - a[0]) * f, a[1] + (b[1] - a[1]) * f]);
        tr.line.setStyle({ opacity: (tr.certain ? 0.5 : 0.35) * (1 - k) });
      }
      if (travelers.length) travelRaf = requestAnimationFrame(travelStep);
    }

    function travel(p, color) {
      var route = travelRoute(p);
      if (!route) return false;
      var pts = route.pts, legs = [], total = 0;
      for (var i = 0; i + 1 < pts.length; i++) {
        // Plain planar distance in degrees: at mesh scale it is only used to
        // share the duration between legs, so a projection would buy nothing.
        var dy = pts[i + 1][0] - pts[i][0], dx = pts[i + 1][1] - pts[i][1];
        var d = Math.sqrt(dy * dy + dx * dx) || 1e-9;
        legs.push(d);
        total += d;
      }
      // Oldest out first: a burst must not be able to grow the layer count.
      while (travelers.length >= MAX_TRAVELERS) dropTraveler(travelers.shift());
      travelers.push({
        pts: pts, legs: legs, total: total, certain: route.certain,
        start: performance.now(),
        line: L.polyline(pts, {
          color: color, weight: route.certain ? 2 : 1.5,
          opacity: route.certain ? 0.5 : 0.35,
          dashArray: route.certain ? null : "5 7",
        }).addTo(lmap),
        dot: L.circleMarker(pts[0], {
          radius: 5, color: color, weight: 2, fillColor: color, fillOpacity: 0.95,
        }).addTo(lmap),
      });
      if (travelRaf === null) travelRaf = requestAnimationFrame(travelStep);
      return true;
    }

    // --- heat map of travelled paths --------------------------------------------
    // The server aggregates the full packet retention window (7 days by
    // default) of resolved paths into weighted segments (see
    // /api/v1/packets/heatmap): the feed's own buffer holds ~300 packets,
    // nowhere near enough traversals to show which links carry the mesh.
    // Deliberately ignores the name/country filter: the overlay answers "where
    // does the traffic flow", and a backbone with the busiest half filtered
    // away would be redrawn as something it is not.
    var HEAT_KEY = "mcs-pktheat";
    var HEAT_MIN_KEY = "mcs-pktheat-min";
    var HEAT_REFRESH_MS = 300000;  // a summary of a week can be five minutes old
    // Five anchors, quietest first, sampled from the Turbo colormap; see the
    // --heat-* block in style.css for where they come from and what they were
    // measured at. They live in CSS so each theme can carry its own set: the
    // hues are identical in both, only the lightness differs, because Turbo is
    // designed for a black background and its middle vanishes on a white map.
    // The fallbacks are the dark set, so the layer still draws if the
    // stylesheet somehow did not load.
    var HEAT_ANCHORS = [
      cssVar("--heat-1", "#3490f8"),
      cssVar("--heat-2", "#39ef9c"),
      cssVar("--heat-3", "#ebd22e"),
      cssVar("--heat-4", "#ff871e"),
      cssVar("--heat-5", "#d93b10"),
    ];
    // Line width runs off the same value as the colour, on purpose. A rainbow
    // is the one thing every colour-accessibility guide warns about, and this
    // one was picked deliberately anyway -- so the magnitude gets a second,
    // colour-free channel to travel on. Anyone who cannot separate the green
    // from the yellow can still see which line is thicker.
    var HEAT_W_MIN = 1.2, HEAT_W_MAX = 5.6;
    // Opacity varies little and stays high. A nearly transparent line is
    // invisible on its own, but a few hundred of them stacked add up to an
    // even wash -- precisely the fog this layer used to be. From 0.55 up a
    // single line reads as a line and an overlap covers rather than sums.
    var HEAT_O_MIN = 0.55, HEAT_O_MAX = 0.95;
    // Steps in the lookup table. Interpolating on the fly per segment would be
    // a thousand colour conversions per redraw; 64 steps is far past the point
    // where a reader could see a band, and it is built once.
    var HEAT_STEPS = 64;

    // --- Oklab interpolation ----------------------------------------------
    // Mixing two saturated colours channel by channel in sRGB drags the
    // midpoint through a muddy, darker shade -- the halfway point between the
    // green and the yellow anchor is the worst of it. Oklab is near enough to
    // perceptually uniform that a straight line through it stays as bright and
    // as colourful as its two ends, which is what keeps the ramp free of the
    // dead stretch a naive rainbow has.
    function srgbToLinear(c) {
      return c <= 0.04045 ? c / 12.92 : Math.pow((c + 0.055) / 1.055, 2.4);
    }
    function linearToSrgb(c) {
      return c <= 0.0031308 ? 12.92 * c : 1.055 * Math.pow(c, 1 / 2.4) - 0.055;
    }
    function hexToOklab(hex) {
      var h = String(hex).replace("#", "");
      if (h.length === 3) h = h[0] + h[0] + h[1] + h[1] + h[2] + h[2];
      var n = parseInt(h, 16);
      if (!isFinite(n)) n = 0;
      var r = srgbToLinear(((n >> 16) & 255) / 255);
      var g = srgbToLinear(((n >> 8) & 255) / 255);
      var b = srgbToLinear((n & 255) / 255);
      var l = Math.cbrt(0.4122214708 * r + 0.5363325363 * g + 0.0514459929 * b);
      var m = Math.cbrt(0.2119034982 * r + 0.6806995451 * g + 0.1073969566 * b);
      var s = Math.cbrt(0.0883024619 * r + 0.2817188376 * g + 0.6299787005 * b);
      return [
        0.2104542553 * l + 0.7936177850 * m - 0.0040720468 * s,
        1.9779984951 * l - 2.4285922050 * m + 0.4505937099 * s,
        0.0259040371 * l + 0.7827717662 * m - 0.8086757660 * s,
      ];
    }
    function oklabToHex(lab) {
      var l = Math.pow(lab[0] + 0.3963377774 * lab[1] + 0.2158037573 * lab[2], 3);
      var m = Math.pow(lab[0] - 0.1055613458 * lab[1] - 0.0638541728 * lab[2], 3);
      var s = Math.pow(lab[0] - 0.0894841775 * lab[1] - 1.2914855480 * lab[2], 3);
      var rgb = [
        4.0767416621 * l - 3.3077115913 * m + 0.2309699292 * s,
        -1.2684380046 * l + 2.6097574011 * m - 0.3413193965 * s,
        -0.0041960863 * l - 0.7034186147 * m + 1.7076147010 * s,
      ];
      var out = "#";
      for (var i = 0; i < 3; i++) {
        // Clamp per channel: a point between two in-gamut colours can still
        // land just outside sRGB, and a wrapped byte would be a wild colour.
        var v = Math.round(Math.min(1, Math.max(0, linearToSrgb(rgb[i]))) * 255);
        out += (v < 16 ? "0" : "") + v.toString(16);
      }
      return out;
    }
    // The ramp, built once: HEAT_LUT[i] is the colour at i/(steps-1) along it.
    var HEAT_LUT = (function () {
      var labs = HEAT_ANCHORS.map(hexToOklab);
      var lut = [];
      for (var i = 0; i < HEAT_STEPS; i++) {
        var pos = (i / (HEAT_STEPS - 1)) * (labs.length - 1);
        var a = Math.min(labs.length - 2, Math.floor(pos));
        var f = pos - a;
        lut.push(oklabToHex([
          labs[a][0] + (labs[a + 1][0] - labs[a][0]) * f,
          labs[a][1] + (labs[a + 1][1] - labs[a][1]) * f,
          labs[a][2] + (labs[a + 1][2] - labs[a][2]) * f,
        ]));
      }
      return lut;
    })();
    function heatColor(k) {
      var i = Math.round(k * (HEAT_STEPS - 1));
      return HEAT_LUT[Math.min(HEAT_STEPS - 1, Math.max(0, i))];
    }
    // Default threshold. What this layer is for is seeing which links actually
    // carry the mesh -- the roads the traffic follows -- not an inventory of
    // every link ever overheard. 1 (draw everything) is the honest-looking
    // choice and the wrong default for that question: on a week of traffic
    // well over a third of the segments were travelled exactly once, and a
    // single traversal is the weakest thing this data can say -- one packet
    // whose path was reconstructed, with nothing confirming the link a second
    // time. They are also the single largest source of visual noise. 2 drops
    // exactly that class and nothing else, so the map opens on every link seen
    // at least twice. The slider's leftmost notch is always 1, one keystroke
    // away, for anyone who wants the raw picture back -- and the readout beside
    // the slider states from the first paint how many links are being held
    // back, so nobody mistakes the opening view for the whole mesh.
    var HEAT_MIN_DEFAULT = 2;
    var HEAT_REDRAW_MS = 90;  // one drag emits dozens of input events
    var heatEl = document.getElementById("pkt-heat");
    var heatCtlEl = document.getElementById("heat-ctl");
    var heatMinEl = document.getElementById("heat-min");
    var heatCountEl = document.getElementById("heat-count");
    var heatLegendEl = document.getElementById("heat-legend");
    var heatLegendLoEl = document.getElementById("heat-legend-lo");
    var heatLegendHiEl = document.getElementById("heat-legend-hi");
    var heatLegendRampEl = document.getElementById("heat-legend-ramp");
    var heatLayer = null;
    var heatOn = false;
    // The payload is kept so the slider can refilter what is already in memory:
    // dragging must not fire a request per notch, and the server's answer is
    // cached for minutes anyway, so a round trip would only add lag.
    var heatData = null;
    var heatStops = [1];
    var heatMin = HEAT_MIN_DEFAULT;
    var heatRedrawTimer = null;
    try {
      heatOn = localStorage.getItem(HEAT_KEY) === "1";
      var storedMin = parseInt(localStorage.getItem(HEAT_MIN_KEY), 10);
      if (isFinite(storedMin) && storedMin >= 1) heatMin = storedMin;
    } catch (e) { /* blocked */ }

    function dropHeatLayer() {
      if (heatLayer) { lmap.removeLayer(heatLayer); heatLayer = null; }
    }

    function clearHeat() {
      dropHeatLayer();
      heatData = null;
      if (heatCtlEl) heatCtlEl.hidden = true;
      if (heatLegendEl) heatLegendEl.hidden = true;
    }

    // Slider notches are the traversal counts that actually occur in this data
    // set, in order. A linear 1..max slider is unusable here -- with a busiest
    // link near 900 and half the field at 1 or 2, every setting worth having
    // sits in the first one percent of the travel. A log scale fixes the worst
    // of that but still spends notches on counts nobody recorded. Walking the
    // observed values gives a scale that is percentile-shaped for free: the
    // crowded low end (1, 2, 3, 4, 5 ...) gets a notch each, exactly where the
    // useful settings are, the long thin tail collapses to a handful of steps,
    // no notch is a no-op, position 0 is always 1 ("show everything"), and the
    // range re-fits itself when the traffic changes instead of hard-coding a
    // maximum that goes stale.
    function heatScale(segs) {
      var seen = {};
      var stops = [];
      segs.forEach(function (s) {
        if (!seen[s.n]) { seen[s.n] = 1; stops.push(s.n); }
      });
      stops.sort(function (a, b) { return a - b; });
      return stops.length ? stops : [1];
    }

    // Nearest notch at or below the wanted threshold: a remembered value of 7
    // must still mean "roughly there" on a day when no link was travelled
    // exactly seven times.
    function heatStopIndex(want) {
      var idx = 0;
      for (var i = 0; i < heatStops.length; i++) {
        if (heatStops[i] <= want) idx = i; else break;
      }
      return idx;
    }

    // A fresh payload: re-fit the slider to it, then draw. Split from the
    // drawing itself so the slider can redraw without touching the network.
    function drawHeat(d) {
      dropHeatLayer();
      heatData = (d && d.segments && d.segments.length) ? d : null;
      if (!heatOn || !heatData) {
        if (heatCtlEl) heatCtlEl.hidden = true;
        if (heatLegendEl) heatLegendEl.hidden = true;
        return;
      }
      heatStops = heatScale(heatData.segments);
      var idx = heatStopIndex(heatMin);
      heatMin = heatStops[idx];
      if (heatMinEl) {
        heatMinEl.max = String(heatStops.length - 1);
        heatMinEl.value = String(idx);
      }
      if (heatCtlEl) heatCtlEl.hidden = false;
      renderHeat();
    }

    function renderHeat() {
      dropHeatLayer();
      if (!heatOn || !heatData) return;
      var d = heatData;
      var group = L.layerGroup();
      var all = d.segments;
      var days = Math.max(1, Math.round((d.window_h || 24) / 24));
      // Ascending by count, so ties sit together for the rank walk below and
      // the busiest lines are drawn last -- where two links cross, the busier
      // one ends up on top, which is the one a reader is looking for.
      var segs = all.filter(function (s) { return s.n >= heatMin; })
        .sort(function (a, b) { return a.n - b.n; });
      heatReadout(segs.length, all.length);
      if (!segs.length) {
        if (heatLegendEl) heatLegendEl.hidden = true;
        return;
      }
      // Rank-scaled (empirical CDF), not log(1+n)/log(1+max) as before. The
      // measured distribution is brutally heavy-tailed: with a busiest link
      // near nine hundred traversals, half the segments sit at 1 or 2 and
      // ninety percent under 15, so any magnitude-preserving scale -- log
      // included -- crammed ninety percent of the links into the bottom tenth
      // of the visual range and hundreds of near-identical lines melted into
      // one amber wash. The rank scale spends the range on where a link
      // *stands among the others* instead, which is the question a reader asks
      // of a heat map; the exact magnitude lives in the tooltip and in the
      // legend. It also has no degenerate case: when every link was travelled
      // equally often, everything lands in one tie group at the floor and the
      // map honestly shows nothing standing out (the old max-normalisation
      // drew that same situation at full blast). Min/max normalisation with a
      // max==min guard was rejected as it keeps the crammed-bottom problem.
      //
      // k for a segment = fraction of segments strictly lighter than it, so
      // ties share one k: equal counts must look equal. The rank is taken over
      // the segments *currently shown*, not over the whole data set. That does
      // mean sliding the threshold repaints the survivors, which is normally a
      // sin -- but the rule it breaks is about colour that carries identity,
      // where the hue is the thing's name. Here the colour is a magnitude
      // scale, it never appears without the legend that states its class
      // boundaries in real traversal counts, and a scale that keeps spending
      // its two lowest classes on links the reader just asked to hide is
      // exactly the unreadable map we started from.
      var ks = new Array(segs.length);
      var start = 0;
      for (var i = 1; i <= segs.length; i++) {
        if (i === segs.length || segs[i].n !== segs[start].n) {
          for (var m = start; m < i; m++) ks[m] = start / segs.length;
          start = i;
        }
      }
      segs.forEach(function (s, si) {
        var k = ks[si];
        // Interactive, unlike most overlays here: the traversal count is the
        // one number this layer exists to show, and it needs a hover target.
        // The node markers are lifted above the lines below, so where the two
        // overlap the node still wins the pointer.
        L.polyline([[s.a.lat, s.a.lon], [s.b.lat, s.b.lon]], {
          color: heatColor(k),
          weight: HEAT_W_MIN + (HEAT_W_MAX - HEAT_W_MIN) * k,
          opacity: HEAT_O_MIN + (HEAT_O_MAX - HEAT_O_MIN) * k,
          // Round ends and joins: a mesh link is a hairline at the quiet end
          // and butt caps leave visible nicks where segments meet at a node.
          lineCap: "round", lineJoin: "round",
        }).addTo(group).bindTooltip(t("live.heat_tip", {
          a: s.a.name || s.a.prefix.toUpperCase(),
          b: s.b.name || s.b.prefix.toUpperCase(),
          n: s.n,
          days: days,
        }), { direction: "top", sticky: true });
      });
      heatLegend(segs[0].n, segs[segs.length - 1].n);
      // The toggle's tooltip promises the whole retained period; when the
      // server had to cap the aggregation, that promise needs a footnote --
      // silently presenting a truncated week as complete is the one lie this
      // layer must never tell. Rewritten on every draw so a language switch
      // (which restores the static title) is corrected at the next refresh.
      var lbl = heatEl && heatEl.closest ? heatEl.closest("label") : null;
      if (lbl) {
        lbl.title = t("live.heat_title") +
          (d.capped ? " " + t("live.heat_capped") : "");
      }
      // The heat map is an overlay on top of the map, never a precondition for
      // it. If Leaflet refuses this layer, that is this layer's problem: say so
      // and leave the node markers and the packet feed exactly as they were.
      try {
        heatLayer = group.addTo(lmap);
        // Whichever came second -- this layer or the node dots -- the dots end
        // up on top, hoverable and the same size they always were.
        nodeMarkers.forEach(function (e) { e.m.bringToFront(); });
      } catch (e) {
        logFail("drukte-heatmap tekenen", e);
        heatLayer = null;
      }
    }

    // No silent filtering: the moment the threshold hides links, the page says
    // how many and out of how many. A map that quietly drops four hundred
    // observations and looks tidy for it is the same lie as a truncated week
    // presented as complete. The slider's own value is spoken through
    // aria-valuetext, because "8 of 46" tells a screen reader nothing -- the
    // notch index is an implementation detail, the traversal count is the fact.
    function heatReadout(shown, total) {
      if (heatMinEl) {
        heatMinEl.setAttribute("aria-valuetext", t("live.heat_min_value", { n: heatMin }));
      }
      if (!heatCountEl) return;
      var key = shown >= total ? "live.heat_shown_all"
        : (shown ? "live.heat_shown" : "live.heat_shown_none");
      heatCountEl.textContent = t(key, {
        shown: shown, total: total, hidden: total - shown,
      });
    }

    // A colour scale nobody can decode is decoration. The bar is painted from
    // the same lookup table the lines use, and the two numbers beside it are
    // the lowest and highest traversal count actually on the map right now --
    // so a colour can be turned back into a number, and both ends move with
    // the threshold instead of quietly claiming a range that is not drawn.
    function heatLegend(lo, hi) {
      if (!heatLegendEl || !heatLegendRampEl) return;
      if (!heatLegendRampEl.style.backgroundImage) {
        var stops = [];
        for (var i = 0; i < HEAT_LUT.length; i++) {
          stops.push(HEAT_LUT[i] + " " + ((i / (HEAT_LUT.length - 1)) * 100).toFixed(2) + "%");
        }
        heatLegendRampEl.style.backgroundImage =
          "linear-gradient(90deg, " + stops.join(", ") + ")";
      }
      if (heatLegendLoEl) heatLegendLoEl.textContent = lo + "×";
      if (heatLegendHiEl) heatLegendHiEl.textContent = hi + "×";
      heatLegendEl.hidden = false;
    }

    function loadHeat() {
      fetch("/api/v1/packets/heatmap")
        .then(function (r) { return r.json(); })
        .then(function (d) { if (heatOn) drawHeat(d); })
        .catch(function (e) {
          // The next toggle or refresh tries again either way, but a failure
          // that is not the network's fault should not hide behind that.
          logFail("drukte-heatmap ophalen of tekenen", e);
        });
    }

    if (heatMinEl) {
      heatMinEl.addEventListener("input", function () {
        var idx = parseInt(heatMinEl.value, 10);
        if (!isFinite(idx)) idx = 0;
        idx = Math.max(0, Math.min(heatStops.length - 1, idx));
        heatMin = heatStops[idx];
        try {
          localStorage.setItem(HEAT_MIN_KEY, String(heatMin));
        } catch (e) { /* blocked */ }
        // The readout is a handful of comparisons and must feel instant while
        // dragging; tearing down and rebuilding a thousand Leaflet polylines
        // is not, so that trails behind on a short timer. Held to the trailing
        // edge on purpose: what a reader wants to see is where they let go.
        heatReadout(heatData ? heatData.segments.filter(function (s) {
          return s.n >= heatMin;
        }).length : 0, heatData ? heatData.segments.length : 0);
        if (heatRedrawTimer) clearTimeout(heatRedrawTimer);
        heatRedrawTimer = setTimeout(function () {
          heatRedrawTimer = null;
          renderHeat();
        }, HEAT_REDRAW_MS);
      });
    }

    if (heatEl) {
      heatEl.checked = heatOn;
      heatEl.addEventListener("change", function () {
        heatOn = heatEl.checked;
        try { localStorage.setItem(HEAT_KEY, heatOn ? "1" : "0"); } catch (e) { /* blocked */ }
        if (heatOn) loadHeat(); else clearHeat();
      });
      if (heatOn) loadHeat();
      // A week-long summary drifts slowly, but this page is left open for hours;
      // refresh it on a clock far slower than the packet poll.
      setInterval(function () {
        if (heatOn && !document.hidden) loadHeat();
      }, HEAT_REFRESH_MS);
    }

    // The sender leads: it is what a reader is looking for. An advert names its
    // own; everything else falls back to the resolved 1-byte source hash, shown
    // in the muted style of a derivation rather than a stated fact. Where that
    // hash fits no node we know, the byte itself is printed -- 0x92 -- because
    // it is still the same sender in every packet it sends, and a row reading
    // only "onbekend" made that unknowable. The word is left for the packets
    // that genuinely carry no sender byte at all.
    function senderCell(p) {
      var el = document.createElement("span");
      el.className = "pkt-who";
      if (p.sender_name || p.sender) {
        el.textContent = p.sender_name || p.sender.toUpperCase();
        return el;
      }
      var lbl = srcLabel(p.src, t);
      if (lbl) {
        el.textContent = lbl.text;
        el.title = lbl.title;
        el.classList.add("src-derived");
        return el;
      }
      el.textContent = t("pkt.sender_short");
      return el;
    }

    function cell(cls, text) {
      var el = document.createElement("span");
      el.className = cls;
      el.textContent = text;
      return el;
    }

    // Cell order in the DOM is not the order on screen: the timestamp sits right
    // after the sender so that a narrow screen can put the two on one line, and
    // CSS pushes it to the far right on a wide one. See .pkt-time { order }.
    function feedRow(p, quiet) {
      var li = document.createElement("li");
      var dot = document.createElement("i");
      dot.style.background = PKT_COLORS[p.type] || "#7d8fa0";
      li.appendChild(dot);
      li.appendChild(senderCell(p));

      var when = document.createElement("time");
      when.className = "reltime pkt-time";
      when.setAttribute("datetime", p.ts);
      li.appendChild(when);

      var brk = document.createElement("b");
      brk.className = "pkt-break";
      li.appendChild(brk);

      // Two spellings of the observer, and CSS picks one: the name where there
      // is room for it, the key prefix on a phone. Without that, one long node
      // name pushes every row onto a third line.
      var obs = document.createElement("span");
      obs.className = "pkt-obs";
      obs.appendChild(cell("obs-name", p.observer_name ||
                                       (p.observer || "").toUpperCase() || "—"));
      obs.appendChild(cell("obs-prefix", (p.observer || "").slice(0, 6).toUpperCase() || "—"));
      li.appendChild(obs);
      li.appendChild(cell("pkt-type", p.type || "?"));
      li.appendChild(scopeCell(p));
      li.appendChild(cell("pkt-snr", p.snr != null ? p.snr.toFixed(1) + " dB" : "—"));
      li.appendChild(cell("pkt-rssi", p.rssi != null ? p.rssi + " dBm" : "—"));
      li.appendChild(cell("pkt-hops", p.path_len
        ? t(p.path_len > 1 ? "live.hops_plural" : "live.hops", { n: p.path_len })
        : "—"));
      li.appendChild(cell("pkt-len", p.len != null ? p.len + " B" : "—"));
      li.appendChild(cell("pkt-cc", p.country ? flagOf(p.country) + " " + p.country : "—"));

      li.dataset.id = p.id;
      li.tabIndex = 0;
      li.setAttribute("role", "button");
      if (openId === p.id) li.classList.add("selected");
      // A whole-list re-render (filter change) must not replay the arrival
      // animation on rows that did not just arrive.
      if (quiet) li.style.animation = "none";
      return li;
    }

    // "Heard by" is dead weight while a single node forwards everything -- the
    // same name on every row. It is not dropped, because the moment a second
    // node starts forwarding it becomes one of the most interesting columns:
    // who heard what. So it appears by itself, as soon as the packets on show
    // actually come from more than one observer.
    function updateObserverColumn(shown) {
      if (!liveCardEl) return;
      var first = null, several = false;
      for (var i = 0; i < shown.length && !several; i++) {
        var o = shown[i].observer || "";
        if (first === null) first = o;
        else if (o !== first) several = true;
      }
      liveCardEl.classList.toggle("show-observer", several);
    }

    // --- filtering the feed ---------------------------------------------------
    // Remembered in localStorage next to the theme and the language, so a
    // visitor who only cares about one node keeps that view across visits.
    var FILTER_KEY = "mcs-pktfilter";
    var COUNTRY_KEY = "mcs-pktcountry";
    var filterEl = document.getElementById("pkt-filter");
    var countryEl = document.getElementById("pkt-country");
    var filterCountEl = document.getElementById("pkt-filter-count");
    var filterText = "";
    var filterCountry = "";
    try {
      filterText = localStorage.getItem(FILTER_KEY) || "";
      filterCountry = localStorage.getItem(COUNTRY_KEY) || "";
    } catch (e) { /* blocked */ }
    if (filterEl) filterEl.value = filterText;

    // Built rather than templated, because the region has to be its own element:
    // it is the part a phone drops. Four extra characters are enough to push the
    // second line of a row onto a third, and the panel names the region anyway.
    function scopeCell(p) {
      var el = document.createElement("span");
      el.className = "pkt-scope";
      if (!p.scope) {
        el.textContent = "—";
        return el;
      }
      el.textContent = t("scope." + p.scope);
      if (p.scope_region) {
        var region = document.createElement("span");
        region.className = "pkt-region";
        region.textContent = " · " + p.scope_region;
        el.appendChild(region);
      }
      return el;
    }

    // One haystack for name, prefix, payload type, country and scope: typing
    // "adv", "2ae7", "be", "scoped" or part of a node name all mean the same
    // thing to a visitor -- "show me the packets that mention this".
    function matches(p) {
      if (filterCountry) {
        // "??" is a visitor asking for the packets we could not place. That is a
        // real answer about the mesh, not the absence of a filter.
        var want = filterCountry === "??" ? null : filterCountry;
        if ((p.country || null) !== want) return false;
      }
      var q = filterText.trim().toLowerCase();
      if (!q) return true;
      // The scope goes in twice: as stored, so "scoped" keeps working whatever
      // the page language, and as shown, so a Dutch visitor typing what is on
      // screen finds the same rows. Source candidates count too: a name in the
      // sender column must be findable however it got there.
      var srcNames = (p.src && p.src.matches || []).map(function (m) {
        return m.name || m.prefix;
      }).join(" ");
      return [p.sender_name, p.observer_name, p.sender, p.observer, p.type,
              p.country, p.scope, p.scope && t("scope." + p.scope), srcNames]
        .filter(Boolean).join(" ").toLowerCase().indexOf(q) !== -1;
    }

    // Built from the feed. The server omits the country list entirely when it
    // has no borders to classify against, and then the control stays hidden --
    // but a choice remembered from a deployment that did have borders must be
    // dropped with it, or the visitor is left filtering everything away with no
    // control on screen to undo it.
    function buildCountryFilter(codes) {
      if (!countryEl) return;
      if (!codes || !codes.length) {
        countryEl.hidden = true;
        forgetCountry();
        return;
      }
      var opts = [["", t("live.country_all")]];
      codes.forEach(function (c) { opts.push([c, countryLabel(c)]); });
      opts.push(["??", t("live.country_none")]);
      countryEl.textContent = "";
      opts.forEach(function (pair) {
        var o = document.createElement("option");
        o.value = pair[0];
        o.textContent = pair[1];
        countryEl.appendChild(o);
      });
      // Same trap, milder: a country that has gone quiet since the last visit is
      // no longer on the list, and selecting nothing is better than selecting
      // something unreachable.
      if (filterCountry && !opts.some(function (o) { return o[0] === filterCountry; })) {
        forgetCountry();
      }
      countryEl.value = filterCountry;
      countryEl.hidden = false;
    }

    function forgetCountry() {
      if (!filterCountry) return;
      filterCountry = "";
      try { localStorage.removeItem(COUNTRY_KEY); } catch (e) { /* blocked */ }
      renderFeed();
    }

    if (countryEl) {
      countryEl.addEventListener("change", function () {
        filterCountry = countryEl.value;
        try {
          if (filterCountry) localStorage.setItem(COUNTRY_KEY, filterCountry);
          else localStorage.removeItem(COUNTRY_KEY);
        } catch (e) { /* blocked */ }
        renderFeed(true);
      });
    }

    // --- the node layer follows the filter ------------------------------------
    // Kept as objects rather than thrown away and rebuilt: with a few hundred
    // nodes, recreating every marker on each keystroke is the one thing here
    // that would actually feel slow. Each entry remembers the style it is
    // wearing so a pass only touches the markers whose state really changed.
    var nodeMarkers = [];        // [{n: node, m: marker, style: "on"|"dim"}]
    var mapEmptyEl = document.getElementById("map-empty");
    var pathPrefixes = null;     // prefixes of the open packet's path, or null

    var NODE_ON = { radius: 4, color: "#7d8fa0", weight: 1, fillColor: "#7d8fa0",
                    fillOpacity: 0.5, opacity: 1 };
    // Dimmed rather than removed. Hiding is tidier, but the mesh is the point of
    // this map: a Dutch node means little without the Belgian ones around it,
    // and a path that crosses the filter would otherwise end at markers that are
    // not there. Faint keeps the geography and still lets the matches carry the
    // eye. Tooltips stay attached, so a ghost can still be identified on hover.
    var NODE_DIM = { radius: 3, color: "#7d8fa0", weight: 1, fillColor: "#7d8fa0",
                     fillOpacity: 0.08, opacity: 0.18 };

    // Which nodes the filter is *about*. The country choice always applies. The
    // text only applies when it matches at least one node: otherwise a visitor
    // typing a payload type ("advert") would dim all 218 nodes and be told
    // nothing matches, when what they filtered was traffic, not geography.
    function textMatchesAnyNode() {
      var q = filterText.trim().toLowerCase();
      if (!q) return false;
      return nodeMarkers.some(function (e) { return nodeText(e.n).indexOf(q) !== -1; });
    }

    function nodeText(n) {
      return [n.name, n.prefix, n.country].filter(Boolean).join(" ").toLowerCase();
    }

    function nodeMatches(n, useText) {
      // The open packet's path is exempt, so a route stays whole even where it
      // leaves the filter. A gap in a drawn path has to mean "we do not know",
      // never "you filtered this out" -- see drawPath.
      if (pathPrefixes && pathPrefixes[n.prefix]) return true;
      // The node whose panel is open is exempt for the same reason: a dot that
      // fades out from under the panel explaining it is the map contradicting
      // itself, and worse, it hides the one node the reader is looking at.
      if (selectedNode && selectedNode.n.prefix === n.prefix) return true;
      if (filterCountry) {
        var want = filterCountry === "??" ? null : filterCountry;
        if ((n.country || null) !== want) return false;
      }
      if (!useText) return true;
      return nodeText(n).indexOf(filterText.trim().toLowerCase()) !== -1;
    }

    function applyNodeFilter() {
      if (!nodeMarkers.length) return;
      var useText = textMatchesAnyNode();
      var shown = 0;
      nodeMarkers.forEach(function (e) {
        var want = nodeMatches(e.n, useText) ? "on" : "dim";
        if (want === "on") shown++;
        if (e.style === want) return;      // nothing to repaint
        e.style = want;
        e.m.setStyle(want === "on" ? NODE_ON : NODE_DIM);
      });
      if (mapEmptyEl) mapEmptyEl.hidden = shown > 0;
      updateOutside();
      return shown;
    }

    // Move the view only when the filter leaves nothing to look at. Filtering to
    // Great Britain while parked over Belgium otherwise shows an empty map, but
    // yanking the view around after every keystroke would fight a visitor who
    // just zoomed somewhere deliberately. So: if a match is already on screen,
    // stay put.
    //
    // With the detail panel open the view is never moved by the *filter*. Do not
    // confuse that with the framing a packet gets when it is opened, which does
    // move the map and must -- see mapPadding(). The distinction is the layout:
    // on a wide screen the panel sits beside the map and leaves the picture
    // intact, so nothing needs to move; the sheet on a phone lies over the map,
    // so the route has to be framed into the strip that is left. Filtering while
    // a packet is open would only fight the framing that packet just asked for.
    function fitToMatches() {
      // Nothing to compare against before the map has been framed at all; the
      // opening view waits for a size, so this can genuinely run first.
      if (!viewSet || panelOpen()) return;
      var view = lmap.getBounds();
      var pts = [];
      var visible = false;
      var useText = textMatchesAnyNode();
      nodeMarkers.forEach(function (e) {
        if (!nodeMatches(e.n, useText)) return;
        var ll = e.m.getLatLng();
        pts.push(ll);
        if (view.contains(ll)) visible = true;
      });
      if (!pts.length || visible) return;
      lmap.fitBounds(pts, { padding: [40, 40], maxZoom: 12 });
    }

    // --- where the map opens ---------------------------------------------------
    // It used to open on a fitBounds over every contact the site knows. That
    // list is fed by adverts, and an advert can be relayed across the mesh or
    // imported through the app's Share function from anywhere at all: by August
    // it ran from London (lon -0.33) to Berlin (lon 13.14). The opening view was
    // therefore the North Sea, with the Belgian cluster that carries all the
    // actual traffic as a speck -- which reads as "the map shows no nodes". And
    // it got worse by itself: every distant contact that ever arrives widens the
    // opening view, permanently. maxZoom cannot help, it bounds the other
    // direction.
    //
    // Three things replace it, in this order of authority:
    //   1. the view the visitor last left the map at,
    //   2. otherwise the nodes that carry traffic, with outliers trimmed,
    //   3. and whatever ends up outside is counted on the map, with one click
    //      to bring it all into view.
    //
    // What this does *not* do is drop anything. Every located node is still a
    // marker, still hoverable, still clickable, still reachable by zooming out.
    // This decides where the map starts, never what it contains -- hiding the
    // far nodes would trade one silent lie for another.
    var VIEW_KEY = "mcs-mapview";
    // Trim 5% off each end per axis. Deliberately a fraction and not a fixed
    // count, so it scales with the mesh: it is what keeps this fix from wearing
    // out the way the old fitBounds did.
    var OUTLIER_FRAC = 0.05;
    // A floor under the opening zoom, as a backstop and nothing more. The trim
    // handles the ordinary case; this catches the pathological one, where there
    // are too few positions for a distribution to exist at all -- four nodes,
    // one of them in Berlin, is not an outlier, it is the mesh, and without a
    // floor the map would still open on half of Europe. Roughly 500 km across,
    // so a genuinely regional mesh is never clipped by it.
    var MIN_OPEN_ZOOM = 7;
    var outsideEl = document.getElementById("map-outside");

    // Whether the map is on screen at a usable size at all. The "Live pakketten"
    // block is collapsible and the choice is remembered per visitor, so a
    // returning reader who folded it away loads this page with a map element of
    // zero width. Leaflet answers a zero-size viewport with maxZoom out of
    // getBoundsZoom and a degenerate rectangle out of getBounds, so every
    // decision below would be made on nonsense -- and, now that the view is
    // remembered, nonsense that would be stored and then restored on every later
    // visit.
    //
    // Measured on the element, deliberately not through lmap.getSize(): Leaflet
    // caches that and only recomputes it when invalidateSize() says so. Asking
    // Leaflet therefore keeps answering 0 forever after a single zero-width
    // read -- including inside the very observer whose job is to call
    // invalidateSize, which deadlocks it and leaves the map permanently blank
    // instead of merely mis-framed. Found exactly that way.
    function mapSized() {
      return livemapEl.clientWidth > 0 && livemapEl.clientHeight > 0;
    }

    // Whether the map has been framed on the mesh yet, as opposed to merely
    // having the placeholder world view it is created with. The framing is
    // allowed to wait for the element to have a size, so there is a real
    // window in which the map shows [0,0] at zoom 2 -- and everything that
    // reads the current view has to be able to answer "not yet" during it.
    // Counting how many nodes fall outside a view of the whole planet would
    // answer zero, and remembering that view would hand the next visit an
    // opening shot of the Atlantic. Leaflet knows only whether a view exists,
    // never whether it means anything, so this flag is ours.
    var viewSet = false;

    function storedView() {
      try {
        var v = JSON.parse(localStorage.getItem(VIEW_KEY) || "null");
        // Validated rather than trusted. localStorage outlives deploys, so a
        // value from an older format -- or one a visitor edited -- would
        // otherwise reach Leaflet, which answers a NaN centre with a blank grey
        // map and no error. Exactly the symptom being fixed here.
        if (!v || typeof v.lat !== "number" || typeof v.lon !== "number" ||
            typeof v.z !== "number") return null;
        if (!isFinite(v.lat) || !isFinite(v.lon) || !isFinite(v.z)) return null;
        if (Math.abs(v.lat) > 90 || Math.abs(v.lon) > 180) return null;
        if (v.z < 1 || v.z > 19) return null;
        return v;
      } catch (e) { return null; }        // blocked storage, or not JSON
    }

    // Saved on every settled move, debounced. It stores programmatic framings
    // too -- the one an opened packet's route asks for, say -- and that is
    // meant: the promise is "the map is where you left it", and a route you
    // went to look at is somewhere you went. The alternative, telling apart
    // user gestures from code-driven moves, needs a flag around every fitBounds
    // in this file and gets forgotten at the first new one.
    var viewSaveTimer = null;
    function saveView() {
      if (!viewSet || !mapSized()) return;   // nothing meaningful to remember yet
      clearTimeout(viewSaveTimer);
      viewSaveTimer = setTimeout(function () {
        var c = lmap.getCenter();
        try {
          localStorage.setItem(VIEW_KEY, JSON.stringify({
            lat: +c.lat.toFixed(5), lon: +c.lng.toFixed(5), z: lmap.getZoom(),
          }));
        } catch (e) { /* blocked: the next visit simply reframes */ }
      }, 400);
    }

    // The 5th-to-95th percentile box of a set of positions, taken per axis.
    //
    // Self-limiting by construction, which is why it needs no size check.
    // Flooring the low index and ceiling the high one means it never trims more
    // than the fraction implies: twenty points lose nothing at all, three
    // hundred lose fifteen a side. That is the right behaviour at both ends --
    // there is nothing to call an outlier when there is no distribution to be
    // an outlier in.
    //
    // Per axis rather than by distance from a centre, because a centre is the
    // very thing one far node moves, so a radial measure would be computed from
    // the damage it is meant to undo. Leaflet wants a rectangle either way.
    function quantileBox(pts, frac) {
      function axis(index) {
        var v = pts.map(function (p) { return p[index]; })
                   .sort(function (a, b) { return a - b; });
        var last = v.length - 1;
        return [v[Math.floor(last * frac)], v[Math.ceil(last * (1 - frac))]];
      }
      var la = axis(0), lo = axis(1);
      return [[la[0], lo[0]], [la[1], lo[1]]];
    }

    // The positions this mesh is demonstrably using, read off the batch of
    // packets the first poll returns. Every point here comes from a real
    // reception: a sender an advert named in full, an observer that heard
    // something, or a hop that resolved to exactly one placeable node -- the
    // same rule the drawn route follows, so the opening view never leans on a
    // guess the map itself refuses to draw.
    //
    // One vote per position, not one per reception. Weighting the percentiles by
    // how chatty a node is was tried first and is wrong twice over: the busiest
    // node's forty copies eat the whole trim budget, so the box closes around
    // it and the trim stops protecting anything; and "the outermost 5%" is a
    // statement about nodes, not about packets. Measured on the live feed it
    // was the difference between a 60 km box with 88 nodes in view and a 120 km
    // one with 195 -- the second is both the wider picture and the better
    // guarded one.
    //
    // Keyed on the rounded coordinates rather than on a node key, because the
    // three sources name their node differently (a sender by key prefix, an
    // observer by a longer one, a hop by an address hash) and the position is
    // the only thing this function actually wants. Two nodes on one rooftop
    // collapsing into one vote costs nothing here.
    function trafficPoints(list) {
      var seen = {}, pts = [];
      function add(lat, lon) {
        if (lat == null || lon == null) return;
        var key = lat.toFixed(5) + "," + lon.toFixed(5);
        if (seen[key]) return;
        seen[key] = true;
        pts.push([lat, lon]);
      }
      (list || []).forEach(function (p) {
        add(p.sender_lat, p.sender_lon);
        add(p.observer_lat, p.observer_lon);
        (p.path || []).forEach(function (h) { add(h.lat, h.lon); });
      });
      return pts;
    }

    // Held until the map has a size to be framed into; see mapSized.
    var pendingOpening = null;

    function openingView(packets) {
      if (!mapSized()) { pendingOpening = packets || []; return; }
      // Cleared here rather than by the callers, so that whichever of them
      // gets there first -- the observer or the poll -- frames the map once
      // and the other finds nothing left to do. Without this the poll would
      // re-frame every four seconds, yanking the map back from wherever the
      // reader had just panned it.
      pendingOpening = null;
      var stored = storedView();
      if (stored) {
        lmap.setView([stored.lat, stored.lon], stored.z);
        viewSet = true;
        return;
      }
      var pts = trafficPoints(packets);
      if (pts.length < 2) {
        // A mesh that has been quiet, or a first poll that happened to land in
        // a gap, has no traffic to point at. Fall back to the nodes themselves
        // -- through the same trim, which is what stops a single imported
        // contact from setting the scale even here.
        pts = nodeMarkers.map(function (e) { return [e.n.lat, e.n.lon]; });
      }
      if (!pts.length) return;
      lmap.fitBounds(quantileBox(pts, OUTLIER_FRAC), { padding: [40, 40], maxZoom: 12 });
      viewSet = true;
      // Pulled back to the floor around the same centre. The centre is the
      // trimmed box's, so it is already outlier-proof; only the span can still
      // be absurd, and only in the small-set case the floor exists for.
      if (lmap.getZoom() < MIN_OPEN_ZOOM) lmap.setZoom(MIN_OPEN_ZOOM);
    }

    // What the framing left out, said out loud. A map that quietly shows part of
    // a mesh is the same silent omission this site refuses everywhere else, and
    // the count doubles as the answer to "where did my node go": off screen, one
    // click away. Counted over the nodes the filter is showing rather than over
    // all of them, so the number always matches the dots being looked for.
    function updateOutside() {
      if (!outsideEl) return;
      if (!viewSet || !mapSized()) { outsideEl.hidden = true; return; }
      var view = lmap.getBounds();
      var n = 0;
      nodeMarkers.forEach(function (e) {
        if (e.style === "on" && !view.contains(e.m.getLatLng())) n++;
      });
      outsideEl.hidden = !n;
      outsideEl.textContent = t(n === 1 ? "live.outside_one" : "live.outside", { n: n });
      outsideEl.title = t("live.outside_title");
    }

    if (outsideEl) {
      outsideEl.addEventListener("click", function () {
        var pts = nodeMarkers.filter(function (e) { return e.style === "on"; })
                             .map(function (e) { return e.m.getLatLng(); });
        // maxZoom because "show everything" on a mesh of one node would
        // otherwise drop the reader onto a rooftop.
        if (pts.length) lmap.fitBounds(pts, { padding: [40, 40], maxZoom: 12 });
      });
    }

    lmap.on("moveend", function () {
      updateOutside();
      saveView();
    });

    // Expanding a folded-away live map fires no window resize, so nothing told
    // Leaflet the element had grown from nothing to 420 px: it kept drawing
    // against the viewport it was built with, and the reader got a grey box with
    // no nodes in it -- the same complaint that started all of this, by a second
    // route. Watching the element itself catches that, and rotating a phone, and
    // anything else that changes the box, in one place. Deliberately never
    // disconnected: it only fires on a real size change, and the count of what
    // is off screen has to follow the box it is counted against.
    if (typeof ResizeObserver !== "undefined") {
      new ResizeObserver(function () {
        if (!mapSized()) return;
        lmap.invalidateSize();
        if (pendingOpening) openingView(pendingOpening);
        updateOutside();
      }).observe(livemapEl);
    }

    function updateFeedState() {
      var active = filterText.trim() !== "" || filterCountry !== "";
      var hits = active ? recent.filter(matches).length : recent.length;
      if (feedEmptyEl) feedEmptyEl.hidden = !(active && hits === 0);
      if (filterCountEl) {
        filterCountEl.textContent = active
          ? t("live.filtered", { n: hits, total: recent.length }) : "";
      }
    }

    // The packets the feed is showing: the filtered head of the buffer, capped
    // the same way the rendered list is.
    function visiblePackets() {
      var out = [];
      for (var i = 0; i < recent.length && out.length < FEED_MAX; i++) {
        if (matches(recent[i])) out.push(recent[i]);
      }
      return out;
    }

    // The list scrolls and the header does not, so once the list grows a
    // scrollbar its columns shift left by the scrollbar's width and stop lining
    // up with the headings. Measured rather than assumed: it is zero on the
    // platforms that use overlay scrollbars.
    function syncHeadGutter() {
      if (!feedHeadEl || !feedEl) return;
      var gutter = feedEl.offsetWidth - feedEl.clientWidth;
      feedHeadEl.style.paddingRight = gutter ? "calc(.4rem + " + gutter + "px)" : "";
    }

    function renderFeed(refit) {
      if (feedEl) {
        feedEl.textContent = "";
        var shown = visiblePackets();
        shown.forEach(function (p) { feedEl.appendChild(feedRow(p, true)); });
        updateObserverColumn(shown);
        syncHeadGutter();
      }
      applyNodeFilter();
      if (refit) fitToMatches();
      updateFeedState();
      updateCount();
      updateTimes();
    }

    if (filterEl) {
      filterEl.addEventListener("input", function () {
        filterText = filterEl.value;
        try { localStorage.setItem(FILTER_KEY, filterText); } catch (e) { /* blocked */ }
        renderFeed(true);
      });
    }

    function render(list, animate) {
      var flashes = 0;
      list.forEach(function (p) {
        // Reception time comes from the packet, so a backlog is dated when it
        // was heard; only a packet without a usable timestamp falls back to now.
        var at = Date.parse(p.ts);
        var heard = isNaN(at) ? Date.now() : at;
        // The filter governs the map as well as the list: a visitor watching one
        // node should not have the rest of the mesh moving over their map. And
        // only fresh packets animate: a catch-up batch after a hidden tab still
        // fills the list, but its stale part must not be acted out as live.
        var show = animate && heard >= Date.now() - FLASH_STALE_MS &&
          matches(p) && flashes < FLASH_MAX_PER_POLL;
        if (show) {
          var color = PKT_COLORS[p.type] || "#7d8fa0";
          // A packet with a route travels it; one we cannot place a route for
          // falls back to the flash, which needs only the single position.
          var moved = animating() && travel(p, color);
          if (!moved && p.lat != null && p.lon != null) flash(p.lat, p.lon, color);
          flashes++;
        }
        seen.push({ t: heard, p: p });
        recent.unshift(p);
        if (feedEl && matches(p)) feedEl.insertBefore(feedRow(p), feedEl.firstChild);
      });
      if (recent.length > FEED_BUFFER) recent.length = FEED_BUFFER;
      if (feedEl) {
        while (feedEl.children.length > FEED_MAX) feedEl.removeChild(feedEl.lastChild);
        updateObserverColumn(visiblePackets());
        syncHeadGutter();
      }
      updateFeedState();
      updateTimes();
      updateCount();
    }

    // Counted from the packets themselves rather than from a list of arrival
    // times, for two reasons. The filter has to reach the counter as well -- "42
    // packets" over a list showing two is the same lie the map was telling with
    // its unfiltered markers. And the window is measured on each packet's own
    // timestamp, so the backlog that arrives on the first poll no longer reports
    // hours-old traffic as if it had just been heard.
    function updateCount() {
      if (!countEl) return;
      var cutoff = Date.now() - 300000;
      var n = 0;
      for (var i = 0; i < seen.length; i++) {
        if (seen[i].t >= cutoff && matches(seen[i].p)) n++;
      }
      // Pruning here keeps the window bounded without a timer of its own.
      // Tested on the OLDEST entry: receptions are appended, so seen[0] is the
      // eldest. Checking the last one instead would only ever fire when the
      // whole mesh had gone quiet, and the array would grow all day.
      if (seen.length && seen[0].t < cutoff) {
        seen = seen.filter(function (s) { return s.t >= cutoff; });
      }
      countEl.textContent = n ? t("live.count", { n: n }) : t("live.waiting");
    }

    function poll(first) {
      if (polling) return;
      polling = true;
      fetch("/api/v1/packets?since_id=" + lastId)
        .then(function (r) { return r.json(); })
        .then(function (d) {
          if (first) buildCountryFilter(d.countries);
          // The map and the feed are two readings of the same response and
          // neither is worth more than the other. Building the map used to be
          // able to throw straight past the render() below, so a map that
          // refused to draw also emptied the packet list -- two failures for
          // the price of one, and the visible symptom pointed away from the
          // cause. They fail separately now.
          if (first && d.nodes) {
            try {
              d.nodes.forEach(function (n) {
                var marker = L.circleMarker([n.lat, n.lon], NODE_ON)
                  .addTo(lmap)
                  .bindTooltip(n.name || n.prefix.toUpperCase(), { direction: "top" });
                // Held on to so the filter can restyle them; see applyNodeFilter.
                // The entry, not the node, is what the panel is opened with: the
                // marker travels with it, and the panel needs it to draw its ring
                // and to give focus back on close.
                var entry = { n: n, m: marker, style: "on" };
                marker.on("click", function (e) {
                  // Kept off the map: Leaflet would otherwise deliver this click
                  // to the map as well, where the outside-click handler would
                  // close the panel this very click is opening.
                  L.DomEvent.stopPropagation(e);
                  openNode(entry);
                });
                nodeMarkers.push(entry);
              });
              // Where the map starts. Not a fitBounds over every marker just
              // created -- see openingView for what that produced and why.
              openingView(d.packets);
              // Still deliberately a second pass. The map is given a view the
              // moment it is created now, so a marker's SVG element does exist
              // by the time the loop adds it -- but this pass costs nothing and
              // keeps the guarantee local, instead of resting on how far up the
              // file somebody remembers to leave that setView alone.
              nodeMarkers.forEach(focusableNode);
              // A filter restored from localStorage has to reach the layer that
              // was only just built, and the view should start where the matches
              // are rather than on the whole mesh.
              applyNodeFilter();
              fitToMatches();
            } catch (e) {
              logFail("nodes op de kaart zetten", e);
            }
          }
          // The opening framing waits for the map to have a size, which a
          // folded-away section does not give it. The observer above normally
          // delivers that the instant it unfolds; this is the second chance,
          // so a browser that never delivers a resize still ends up with a
          // framed map within one poll rather than never. Costs one null check
          // on every other poll there will ever be.
          if (pendingOpening) openingView(pendingOpening);
          lastId = d.last_id || lastId;
          // The first response is history (the newest stored packets), not
          // traffic heard while this page was open: list it, but do not set off
          // a firework of flashes for receptions that predate the visit.
          render(d.packets || [], !first);
        })
        .catch(function (e) {
          // The poll runs on a timer, so the next tick tries again regardless;
          // this only makes sure a broken response or a broken render is not
          // indistinguishable from a quiet mesh.
          logFail("pakketten ophalen of tonen", e);
        })
        .then(function () { polling = false; });
    }

    // --- packet detail panel ---------------------------------------------------
    // Deliberately a docked panel and not a modal with a backdrop: the path of
    // the packet is drawn on the live map underneath, and a modal would cover
    // the very thing it is explaining.
    var panel = document.getElementById("packet-panel");
    var pathLayer = null;
    var pathView = null;         // the points the open route was framed on

    // --- the panel as a bottom sheet on a narrow screen -----------------------
    // Must match the media query in style.css; the two are a pair.
    var SHEET_MAX_WIDTH = 820;
    var gripEl = document.getElementById("pkt-grip");

    function sheetMode() { return window.innerWidth <= SHEET_MAX_WIDTH; }

    // Enough for the head and the first few fields -- time, sender, observer,
    // payload type -- while leaving the map worth looking at. Capped against the
    // viewport so landscape, where height is scarce, does not get a sheet that
    // covers everything.
    function peekHeight() {
      return Math.round(Math.min(280, Math.max(132, window.innerHeight * 0.38)));
    }
    function fullHeight() { return Math.round(window.innerHeight * 0.85); }

    function setSheetHeight(px) {
      if (!panel) return;
      var h = Math.max(96, Math.min(px, fullHeight()));
      panel.style.setProperty("--sheet-h", Math.round(h) + "px");
      return h;
    }

    // Always reopened at the peek height, never at whatever the last packet was
    // left at: the reason to open one of these is to see something on the map,
    // and a sheet remembering "fully raised" would hide it every time.
    function resetSheet() {
      if (!panel) return;
      panel.classList.remove("sheet-dragging");
      if (sheetMode()) setSheetHeight(peekHeight());
      else panel.style.removeProperty("--sheet-h");
    }

    function toggleSheet() {
      if (!sheetMode()) return;
      var mid = (peekHeight() + fullHeight()) / 2;
      var now = panel.getBoundingClientRect().height;
      setSheetHeight(now < mid ? fullHeight() : peekHeight());
      // The visible slice of map just changed, so the route has to be reframed
      // for it; that is the whole point of doing this on a phone.
      setTimeout(fitPathView, 200);   // after the height transition
    }

    if (gripEl && panel) {
      var drag = null;
      gripEl.addEventListener("pointerdown", function (e) {
        if (!sheetMode()) return;
        drag = { y: e.clientY, h: panel.getBoundingClientRect().height, moved: false };
        panel.classList.add("sheet-dragging");
        try { gripEl.setPointerCapture(e.pointerId); } catch (err) { /* not captured */ }
        e.preventDefault();
      });
      gripEl.addEventListener("pointermove", function (e) {
        if (!drag) return;
        var dy = drag.y - e.clientY;          // up is a taller sheet
        if (Math.abs(dy) > 4) drag.moved = true;
        setSheetHeight(drag.h + dy);
      });
      function endDrag() {
        if (!drag) return;
        var moved = drag.moved;
        drag = null;
        panel.classList.remove("sheet-dragging");
        if (!moved) { toggleSheet(); return; }   // a tap, not a drag
        // Snap to whichever stop the sheet ended up nearer.
        var mid = (peekHeight() + fullHeight()) / 2;
        setSheetHeight(panel.getBoundingClientRect().height < mid
          ? peekHeight() : fullHeight());
        setTimeout(fitPathView, 200);
      }
      gripEl.addEventListener("pointerup", endDrag);
      gripEl.addEventListener("pointercancel", endDrag);
      gripEl.addEventListener("keydown", function (e) {
        if (e.key !== "Enter" && e.key !== " ") return;
        e.preventDefault();
        toggleSheet();
      });
    }

    // Rotating a phone changes both the stops and the visible slice of map.
    var resizeTimer = null;
    window.addEventListener("resize", function () {
      clearTimeout(resizeTimer);
      resizeTimer = setTimeout(function () {
        // A width change can add or remove the feed's scrollbar, and the header
        // has to be re-gutted for it or the columns stop lining up.
        syncHeadGutter();
        if (!panel || panel.hidden) return;
        if (!sheetMode()) {
          panel.style.removeProperty("--sheet-h");
        } else {
          // Re-clamp, do not reset. A phone fires resize whenever the address
          // bar slides away, and snapping the sheet back to peek in the middle
          // of reading it would be its own bug.
          setSheetHeight(panel.getBoundingClientRect().height);
        }
        fitPathView();
      }, 150);
    });

    // Whichever detail panel is open, or null. There is never more than one --
    // the packet panel and the node panel share a slot and close each other --
    // and everything that has to keep clear of the drawer (the map framing
    // above all) asks this rather than naming one of the two, so that a panel
    // added later is kept clear of by construction.
    function openPanelEl() {
      if (panel && !panel.hidden) return panel;
      if (nodePanel && !nodePanel.hidden) return nodePanel;
      return null;
    }

    function panelOpen() { return !!openPanelEl(); }

    function clearPath() {
      if (pathLayer) { lmap.removeLayer(pathLayer); pathLayer = null; }
      pathView = null;
      if (pathPrefixes) { pathPrefixes = null; applyNodeFilter(); }
    }

    function markSelected(id) {
      if (!feedEl) return;
      Array.prototype.forEach.call(feedEl.children, function (li) {
        li.classList.toggle("selected", parseInt(li.dataset.id, 10) === id);
      });
    }

    function closePanel() {
      if (!panel) return;
      panel.hidden = true;
      openId = null;
      clearPath();
      markSelected(-1);
    }

    // A hop that resolves to several candidates gets a hollow ring on each of
    // them rather than a line: showing all the possibilities is honest, picking
    // one of them would not be. A ranked hop ("likely") is drawn exactly the
    // same way. The ranking is a sentence with its reasons attached, and a line
    // on a map carries no sentence -- it would arrive at the reader as a claim
    // about where the packet went.
    function markCandidates(group, hop, bounds) {
      if (!hop || (hop.state !== "ambiguous" && hop.state !== "likely")) return;
      hop.matches.forEach(function (m) {
        if (m.lat == null || m.lon == null) return;
        L.circleMarker([m.lat, m.lon], {
          radius: 8, color: PATH_COLOR, weight: 1.5, opacity: 0.8,
          dashArray: "3 3", fillOpacity: 0,
        }).addTo(group).bindTooltip(
          t("pkt.hop_maybe", { name: m.name || m.prefix.toUpperCase() }), { direction: "top" });
        // Candidates count towards the view: a ring nobody can see marks
        // nothing, even if the packet itself travelled a much shorter way.
        bounds.push([m.lat, m.lon]);
      });
    }

    // Draw sender -> every hop -> observer.
    //
    // Only hops that resolve to exactly one known node have a position we are
    // entitled to draw through: a path entry is one, two or three bytes of a
    // public key -- the sending node picks which, see path_hash_size -- so with
    // hundreds of nodes on the map several of them can answer to the same hop
    // (see _resolve_hop in routes_api.py). Ambiguous and unknown
    // hops are therefore left out of the line and the segment that spans them is
    // dashed -- a solid line through a guess would claim knowledge the protocol
    // cannot give. This is not a bug in the drawing code; it is the protocol.
    function drawPath(d) {
      clearPath();
      // Every node this route touches is exempt from the filter for as long as
      // the panel is open. A route drawn with holes in it would read as
      // uncertainty, and uncertainty here has a precise meaning that the filter
      // must not be allowed to imitate.
      pathPrefixes = {};
      var stops = [{
        lat: d.sender_lat, lon: d.sender_lon,
        label: nodeLabel(d.sender, d.sender_name), role: "origin",
      }];
      if (d.sender) pathPrefixes[d.sender] = true;
      if (d.observer) pathPrefixes[d.observer.slice(0, 6)] = true;
      (d.path || []).forEach(function (h) {
        var m = h.state === "known" ? h.matches[0] : null;
        // Ambiguous and ranked hops exempt every candidate: their rings are
        // part of the same answer as the line is.
        (h.matches || []).forEach(function (c) { pathPrefixes[c.prefix] = true; });
        stops.push({
          lat: m ? m.lat : null, lon: m ? m.lon : null,
          label: m ? (m.name || m.prefix.toUpperCase()) : null, role: "hop", hop: h,
        });
      });
      stops.push({
        lat: d.observer_lat, lon: d.observer_lon,
        label: nodeLabel(d.observer, d.observer_name), role: "dest",
      });
      applyNodeFilter();

      var group = L.layerGroup();
      var prev = null, gap = false, view = [];
      stops.forEach(function (s) {
        if (s.lat == null || s.lon == null) {
          gap = true;                 // we cannot place this stop: bridge over it
          markCandidates(group, s.hop, view);
          return;
        }
        var here = [s.lat, s.lon];
        if (prev) {
          L.polyline([prev, here], {
            color: PATH_COLOR, weight: gap ? 2 : 3, opacity: gap ? 0.6 : 0.95,
            dashArray: gap ? "7 8" : null,
          }).addTo(group);
        }
        var end = s.role !== "hop";
        L.circleMarker(here, {
          radius: end ? 7 : 5, color: PATH_COLOR, weight: 2,
          fillColor: PATH_COLOR, fillOpacity: end ? 1 : 0.55,
        }).addTo(group).bindTooltip(
          s.label + (end ? " · " + t(s.role === "origin" ? "pkt.origin" : "pkt.destination") : ""),
          { direction: "top" });
        view.push(here);
        prev = here;
        gap = false;
      });

      pathLayer = group.addTo(lmap);
      pathView = view;
      // Scroll before framing, and instantly: the padding below is measured off
      // the map's real rectangle, and a smooth scroll would still be moving
      // while we measured it.
      var box = livemapEl.getBoundingClientRect();
      if (box.top < 60 || box.bottom > window.innerHeight) {
        livemapEl.scrollIntoView({ block: "start" });
      }
      fitPathView();
    }

    // Frame the route inside the part of the map that is actually visible.
    //
    // The wide layout docks the panel beside the map, so only its width has to
    // be kept clear. The sheet layout puts it *over* the map, and Leaflet knows
    // nothing about that: without this it centres the path in the full map
    // element, half of which is behind the sheet -- the path is drawn correctly
    // and two thirds of it sit under the panel.
    function mapPadding() {
      var box = livemapEl.getBoundingClientRect();
      var openEl = openPanelEl();
      var open = !!openEl;
      var sheet = sheetMode();

      // Vertical, and computed the same way in both layouts. The map element is
      // routinely taller than the part of it on screen -- a phone held sideways
      // has barely 390 px of viewport for a 420 px map -- and on top of that the
      // sheet covers its lower part. Clipping against the viewport and the sheet
      // together covers both without a special case for either.
      var floor = sheet && open ? openEl.getBoundingClientRect().top : window.innerHeight;
      var visTop = Math.max(box.top, 0);
      var visBottom = Math.min(box.bottom, window.innerHeight, floor);
      var padTop = Math.max(0, visTop - box.top) + 16;
      var padBottom = Math.max(0, box.bottom - visBottom) + 16;

      // Leave Leaflet a usable band. With the sheet dragged fully up there may be
      // only a sliver of map left, and fitting a route across three countries
      // into twenty pixels produces a dot, not a route -- so the band has a
      // floor, and past it the route is allowed to run behind the sheet. Whoever
      // dragged the sheet up is reading the panel; lowering it reframes.
      var MIN_BAND = 60;
      if (box.height - padTop - padBottom < MIN_BAND) {
        padBottom = Math.max(0, box.height - padTop - MIN_BAND);
      }

      // Horizontal is the only part that differs: the side drawer takes width
      // out of the map, the sheet spans it and takes none.
      var padRight = open && !sheet ? Math.min(430, window.innerWidth * 0.4) : 16;
      return {
        paddingTopLeft: [16, Math.round(padTop)],
        paddingBottomRight: [Math.round(padRight), Math.round(padBottom)],
      };
    }

    function fitPathView() {
      if (!pathView || pathView.length < 2) return;
      var pad = mapPadding();
      pad.maxZoom = 13;
      lmap.fitBounds(pathView, pad);
    }

    // Filling and emptying live at the top level: the archive renders the same
    // fragment. See fillPacketDetail. The live page passes no filter handler --
    // it has no query bar for the buttons to write into -- and lets the country
    // row follow the country filter, which is only present when the deployment
    // has borders to classify positions against.
    function fillPanel(d) {
      fillPacketDetail(d, { showCountry: !!countryEl && !countryEl.hidden });
    }

    function openPacket(id) {
      if (!panel || !id) return;
      // One slot, one panel. See openNode for the whole argument; the two calls
      // are each other's mirror image so that neither order of clicking can end
      // up with both drawers fighting over the same edge of the screen.
      closeNodePanel();
      openId = id;
      blankPacketDetail();
      panel.hidden = false;
      resetSheet();          // every packet starts at the peek height
      var body = document.getElementById("pkt-body");
      if (body) body.scrollTop = 0;
      markSelected(id);
      clearPath();
      fetch("/api/v1/packets/" + encodeURIComponent(id))
        .then(function (r) {
          if (!r.ok) throw new Error("HTTP " + r.status);
          return r.json();
        })
        .then(function (d) {
          if (openId !== id) return;   // a second click already took over
          fillPanel(d);
          drawPath(d);
        })
        .catch(function () {
          if (openId === id) txt("pkt-path-note", t("pkt.loaderror"));
        });
    }

    if (panel && feedEl) {
      feedEl.addEventListener("click", function (e) {
        var li = e.target.closest("li[data-id]");
        if (li) openPacket(parseInt(li.dataset.id, 10));
      });
      feedEl.addEventListener("keydown", function (e) {
        if (e.key !== "Enter" && e.key !== " ") return;
        var li = e.target.closest("li[data-id]");
        if (!li) return;
        e.preventDefault();
        openPacket(parseInt(li.dataset.id, 10));
      });
      document.getElementById("pkt-close").addEventListener("click", closePanel);
      document.addEventListener("keydown", function (e) {
        if (e.key === "Escape" && !panel.hidden) closePanel();
      });
    }

    // --- node detail panel -----------------------------------------------------
    // Behind a click on a dot. The same docked drawer as the packet panel and
    // deliberately the same slot: two drawers would either overlap or halve the
    // map, and both of them want the map framed around their own subject. So
    // opening one closes the other, in both directions, and the reader is never
    // left wondering which of two panels the map is currently obeying. A
    // second, narrower drawer beside the first was tried in the head and
    // dropped -- at 320 px there is no room for one drawer, let alone two.
    var nodePanel = document.getElementById("node-panel");
    var selectedNode = null;    // the marker entry whose panel is open, if any
    var nodeRing = null;        // the ring drawn around that marker
    var nodeReq = 0;            // sequence guard: a slow answer must not land
                                // in a panel that has moved on to another node

    // Selected colour: the cyan the site already uses for "this is the thing
    // being talked about" (the link map's home node, the hop hashes in the
    // packet panel), and not the packet route's purple -- a route and a
    // selection are different claims and must not share a colour.
    var NODE_SEL_COLOR = cssVar("--cyan", "#4cc9f0");

    function clearNodeRing() {
      if (nodeRing) { lmap.removeLayer(nodeRing); nodeRing = null; }
    }

    // A ring around the marker rather than a bigger, brighter dot: the dot's
    // own size and colour already carry meaning (matched or dimmed by the
    // filter), and overwriting them to show a selection would cost the reader
    // the filter state of the very node they are reading about.
    function ringNode(entry) {
      clearNodeRing();
      var ll = entry.m.getLatLng();
      nodeRing = L.circleMarker(ll, {
        radius: 11, color: NODE_SEL_COLOR, weight: 2, opacity: 0.95,
        fillColor: NODE_SEL_COLOR, fillOpacity: 0.1,
        // Not interactive: it sits on top of the marker, and a ring that ate
        // the click would make the node it highlights unclickable.
        interactive: false,
      }).addTo(lmap);
      entry.m.bringToFront();
      // Bring the node out from behind the panel if that is where it ended up,
      // and no further: panning a map someone deliberately positioned is a cost,
      // and it is only worth paying when the subject is not visible.
      var pad = mapPadding();
      if (lmap.panInside) lmap.panInside(ll, pad);
    }

    function closeNodePanel(refocus) {
      if (!nodePanel || nodePanel.hidden) return;
      var was = selectedNode;
      nodePanel.hidden = true;
      selectedNode = null;
      nodeReq++;              // any answer still in flight is now for nobody
      clearNodeRing();
      applyNodeFilter();      // the exemption goes with the selection
      // Focus goes back where the reader left it, but only when the panel was
      // closed deliberately (Escape, the cross). After a click elsewhere the
      // pointer has already moved on and yanking focus back to the map would
      // scroll the page to it.
      if (!refocus || !was) return;
      var el = was.m.getElement();
      if (el && el.focus) el.focus();
    }

    function openNode(entry) {
      if (!nodePanel) return;
      closePanel();
      selectedNode = entry;
      blankNodeDetail();
      // The name and key are already on the map layer, so the panel opens
      // named instead of blank while the request is in flight. Everything the
      // server has to answer stays empty until it has answered -- a heading
      // filled from one source and figures from another are exactly how a
      // panel starts telling a half-truth.
      txt("node-name", entry.n.name || t("node.name_unknown"));
      txt("node-key", entry.n.prefix.toUpperCase());
      nodePanel.hidden = false;
      var body = document.getElementById("node-body");
      if (body) body.scrollTop = 0;
      ringNode(entry);
      applyNodeFilter();
      nodePanel.focus();
      var token = ++nodeReq;
      fetch("/api/v1/nodes/" + encodeURIComponent(entry.n.prefix))
        .then(function (r) {
          if (!r.ok) throw new Error("HTTP " + r.status);
          return r.json();
        })
        .then(function (d) {
          if (token !== nodeReq) return;   // another node took the panel over
          fillNodeDetail(d, { showCountry: !!countryEl && !countryEl.hidden });
        })
        .catch(function () {
          if (token === nodeReq) txt("node-window", t("node.loaderror"));
        });
    }

    // Every node dot is a tab stop with a name. That is one stop per node --
    // a few hundred on this mesh -- which is a real cost for a keyboard user
    // and was weighed against the alternative of a map they cannot open at
    // all. Leaflet's own L.Marker is focusable by default for the same reason,
    // and the filter box sits directly above the map, so the list is
    // shortenable by whoever is walking it. Dimmed nodes keep their stop: they
    // are still real nodes, and a filter that removed them from the keyboard
    // while leaving them on screen would be a second, invisible filter.
    function focusableNode(entry) {
      var el = entry.m.getElement();
      if (!el) return;
      el.setAttribute("tabindex", "0");
      el.setAttribute("role", "button");
      el.setAttribute("aria-label", t("node.marker_aria", {
        name: entry.n.name || entry.n.prefix.toUpperCase(),
      }));
      el.addEventListener("keydown", function (e) {
        if (e.key !== "Enter" && e.key !== " ") return;
        e.preventDefault();
        openNode(entry);
      });
    }

    if (nodePanel) {
      document.getElementById("node-close").addEventListener("click", function () {
        closeNodePanel(true);
      });
      document.addEventListener("keydown", function (e) {
        if (e.key === "Escape" && !nodePanel.hidden) closeNodePanel(true);
      });
      // Click outside closes. Clicks on the map's own interactive layers are
      // exempt, because those are the node markers: without the exemption a
      // click on a second node would close the panel on the way to opening it,
      // and the reader would see it blink.
      document.addEventListener("click", function (e) {
        if (nodePanel.hidden) return;
        if (nodePanel.contains(e.target)) return;
        if (e.target.closest && e.target.closest(".leaflet-interactive")) return;
        closeNodePanel();
      });
    }

    renderFeed();   // restores the "no matches" state before any traffic arrives
    poll(true);
    setInterval(function () {
      // No point polling a tab nobody is looking at; the backlog is still there
      // when it comes back, since the server hands out everything after lastId.
      if (!document.hidden) poll(false);
    }, POLL_MS);
    document.addEventListener("visibilitychange", function () {
      if (document.hidden) {
        // A hidden tab throttles rAF to a crawl, so dots would either freeze
        // mid-route or jump on return. Drop them and start clean instead.
        clearTravelers();
        return;
      }
      poll(false);   // catch up at once instead of after a tick
    });
    setInterval(updateCount, 30000);
  }

  // mini-map inside the history modal of a neighbour link
  var modalMapEl = document.getElementById("modal-map");
  var modalMap = null;
  window.mcsModalMap = function (metric) {
    if (!modalMapEl || typeof L === "undefined") return;
    if (modalMap) { modalMap.remove(); modalMap = null; }
    modalMapEl.hidden = true;
    var m = /^neighbor_([0-9a-f]{6})$/.exec(metric || "");
    if (!m) return;
    getMapData().then(function (d) {
      if (!d.repeater) return;
      var link = d.links.find(function (l) { return l.prefix === m[1]; });
      if (!link) return;
      modalMapEl.hidden = false;
      modalMap = L.map(modalMapEl, { scrollWheelZoom: false, zoomControl: false });
      L.tileLayer(TILE_URL, {
        attribution: "&copy; OSM &copy; CARTO", maxZoom: 19,
      }).addTo(modalMap);
      var home = [d.repeater.lat, d.repeater.lon];
      var there = [link.lat, link.lon];
      var color = snrColor(link.snr);
      L.circleMarker(home, { radius: 6, color: "#4cc9f0", fillColor: "#4cc9f0", fillOpacity: 1 })
        .addTo(modalMap).bindTooltip(d.repeater.name);
      L.circleMarker(there, { radius: 6, color: color, fillColor: color, fillOpacity: 1 })
        .addTo(modalMap).bindTooltip(link.name || link.prefix.toUpperCase());
      L.polyline([home, there], { color: color, weight: 2.5, opacity: 0.85 }).addTo(modalMap);
      modalMap.fitBounds([home, there], { padding: [25, 25] });
      setTimeout(function () { modalMap && modalMap.invalidateSize(); }, 100);
    });
  };

  // --- packet archive (/pakketten) ---------------------------------------------
  // Same guard pattern as the live map: the block wires itself only when its
  // root element is on the page.
  var archEl = document.getElementById("archive");
  if (archEl) {
    var PAGE_SIZE = 100;
    // Which fields get a top-values panel. A fixed list rather than "everything
    // facetable": each facet is one GROUP BY over the matches, and eight panels
    // would make every keystroke of refinement eight times as heavy for a
    // sidebar nobody reads past the fourth block of.
    var FACETS = ["type", "scope", "sender", "observer", "country"];
    var qEl = document.getElementById("arch-q");
    var windowEl = document.getElementById("arch-window");
    var errEl = document.getElementById("arch-error");
    var listEl = document.getElementById("arch-list");
    var emptyEl = document.getElementById("arch-empty");
    var countEl2 = document.getElementById("arch-count");
    var facetsEl = document.getElementById("arch-facets");
    var canvasEl = document.getElementById("arch-canvas");
    var pageEl = document.getElementById("arch-page");
    var prevEl = document.getElementById("arch-prev");
    var nextEl = document.getElementById("arch-next");
    var pktModalEl = document.getElementById("pkt-modal");
    var headEl = archEl.querySelector(".feedhead");
    var sortSelEl = document.getElementById("arch-sort");
    var sortDirEl = document.getElementById("arch-sortdir");
    var archOffset = 0;
    var archTotal = 0;
    var archSeq = 0;      // stale responses from a slower earlier search are dropped
    var openPktId = null; // id of the packet whose modal is open, if any
    // Which column the rows are ordered by, and which way. Held here rather than
    // read back out of the DOM: the URL, the column headings and the phone's
    // picker all have to say the same thing, and three readers of one variable
    // are easier to keep honest than three readers of each other.
    var archSort = "time";
    var archDesc = true;

    // The field table of the query language, straight from search.py. Without
    // it the buttons would be gated on a hand-copied list that goes stale the
    // first time a field is renamed on the server.
    SEARCH_FIELDS = (archEl.dataset.fields || "").split(",").filter(Boolean);

    // The sortable columns and their kind, from search.SORTS. Same reasoning as
    // SEARCH_FIELDS above: a heading that offers an order the server does not
    // have would be a button whose only output is an error message.
    var SORT_KINDS = {};
    (archEl.dataset.sorts || "").split(",").forEach(function (pair) {
      var bits = pair.split(":");
      if (bits.length === 2 && bits[0]) SORT_KINDS[bits[0]] = bits[1];
    });
    function sortable(key) {
      return Object.prototype.hasOwnProperty.call(SORT_KINDS, key);
    }
    function sortToken() { return archSort + (archDesc ? ":desc" : ":asc"); }

    // The query, the window, the order and the open packet live in the URL, so a
    // search or a single packet can be sent to someone as a link -- for a search
    // page that is not a nicety, it is what makes results citable. The order
    // belongs in there for the same reason the query does: "kijk, deze node zit
    // altijd op zeven hops" is a claim about a list in a particular order, and a
    // link that lands on another one does not show it.
    var initialPkt = 0;
    (function initFromUrl() {
      var sp = new URLSearchParams(location.search);
      if (sp.get("q")) qEl.value = sp.get("q");
      if (sp.get("w") !== null) windowEl.value = sp.get("w");
      if (!windowEl.value) windowEl.value = "24";
      var s = (sp.get("sort") || "").split(":");
      // An unknown column in the link is ignored rather than sent on: the
      // server would refuse it, and answering an old bookmark with an error
      // where the default order would do is unkind for no gain.
      if (sortable(s[0])) {
        archSort = s[0];
        archDesc = s[1] !== "asc";
      }
      if (/^\d+$/.test(sp.get("p") || "")) initialPkt = parseInt(sp.get("p"), 10);
    })();

    function pushUrl() {
      var sp = new URLSearchParams();
      if (qEl.value.trim()) sp.set("q", qEl.value.trim());
      if (windowEl.value !== "24") sp.set("w", windowEl.value);
      // Left out while it is the default, so the plain archive link stays plain.
      if (archSort !== "time" || !archDesc) sp.set("sort", sortToken());
      // replaceState, not pushState: opening and closing a detail is reading,
      // not navigating, and a back button that walked back through every packet
      // somebody glanced at would never reach the previous search.
      if (openPktId) sp.set("p", String(openPktId));
      var qs = sp.toString();
      history.replaceState(null, "", location.pathname + (qs ? "?" + qs : ""));
    }

    function sinceParam() {
      var hours = parseInt(windowEl.value, 10);
      if (!hours) {
        // "alles": bound by the oldest packet actually held rather than by an
        // arbitrary epoch, so the histogram's bucket size stays proportionate.
        return archEl.dataset.oldest || "1970-01-01T00:00:00Z";
      }
      return new Date(Date.now() - hours * 3600e3).toISOString().slice(0, 19) + "Z";
    }

    function runSearch(keepOffset) {
      if (!keepOffset) archOffset = 0;
      var seq = ++archSeq;
      pushUrl();
      var url = "/api/v1/packets/search?q=" + encodeURIComponent(qEl.value.trim()) +
        "&since=" + encodeURIComponent(sinceParam()) +
        "&limit=" + PAGE_SIZE + "&offset=" + archOffset +
        "&sort=" + encodeURIComponent(sortToken()) +
        "&facets=" + FACETS.join(",");
      fetch(url).then(function (r) { return r.json(); }).then(function (d) {
        if (seq !== archSeq) return;
        if (d.error) {
          errEl.textContent = d.error;
          errEl.hidden = false;
          return;
        }
        errEl.hidden = true;
        // The order the server actually used, read back rather than assumed, so
        // the arrow in the heading can never point one way while the rows go the
        // other -- the one thing a sorted table must never do.
        if (d.sort) adoptSort(d.sort);
        archTotal = d.total;
        renderCount(d);
        renderHistogram(d);
        renderFacets(d.facets || {});
        renderRows(d.packets || []);
        renderPager();
      }).catch(function () {
        if (seq !== archSeq) return;
        errEl.textContent = t("arch.loaderror");
        errEl.hidden = false;
      });
    }

    function renderCount(d) {
      countEl2.textContent = t(archTotal === 1 ? "arch.count_one" : "arch.count",
        { n: archTotal.toLocaleString() });
    }

    // The histogram is drawn by hand on a canvas, like the gauges on the
    // repeater page: sixty bars do not justify a chart library on a page that
    // otherwise needs none.
    function renderHistogram(d) {
      var ctx = canvasEl.getContext("2d");
      var w = canvasEl.width = canvasEl.parentNode.clientWidth - 24;
      var h = canvasEl.height;
      ctx.clearRect(0, 0, w, h);
      var bars = d.histogram || [];
      if (!bars.length) return;
      var max = 0;
      bars.forEach(function (b) { if (b.n > max) max = b.n; });
      // The axis is the searched window, not the data's own extent: a burst of
      // traffic in one bucket must show as one bar in an otherwise empty hour,
      // not as a wall of green filling the whole strip.
      var lo = Math.floor(new Date(sinceParam()).getTime() / 1000);
      var hi = Math.ceil(Date.now() / 1000);
      var span = Math.max(d.bucket_s, hi - lo);
      var bw = Math.max(2, Math.floor(w * d.bucket_s / span) - 1);
      var accent = cssVar("--accent", "#35e08c");
      ctx.fillStyle = accent;
      bars.forEach(function (b) {
        var x = Math.floor((b.t - lo) / span * w);
        var bh = Math.max(1, Math.round((h - 16) * b.n / max));
        ctx.fillRect(x, h - 14 - bh, bw, bh);
      });
      // First and last moment as labels; a full axis would repeat the window
      // picker's answer in smaller type.
      ctx.fillStyle = TEXT_MUTED;
      ctx.font = "10px 'JetBrains Mono', monospace";
      ctx.textBaseline = "bottom";
      ctx.textAlign = "left";
      ctx.fillText(fmtBucket(lo, d.bucket_s), 0, h);
      ctx.textAlign = "right";
      ctx.fillText(fmtBucket(hi, d.bucket_s), w, h);
    }

    function pad2(n) { return (n < 10 ? "0" : "") + n; }

    // "14-08 22:18:30", local time. Day-month leads because a 7-day archive
    // spans dates; the year is never in doubt inside one week.
    function fmtTs(iso) {
      var d = new Date(iso);
      return pad2(d.getDate()) + "-" + pad2(d.getMonth() + 1) + " " +
        pad2(d.getHours()) + ":" + pad2(d.getMinutes()) + ":" + pad2(d.getSeconds());
    }

    function fmtBucket(epoch, bucketS) {
      var d = new Date(epoch * 1000);
      var dm = pad2(d.getDate()) + "-" + pad2(d.getMonth() + 1);
      var hm = pad2(d.getHours()) + ":" + pad2(d.getMinutes());
      return bucketS >= 43200 ? dm : dm + " " + hm;
    }

    function renderFacets(facets) {
      facetsEl.textContent = "";
      FACETS.forEach(function (name) {
        var values = facets[name];
        if (!values || !values.length) return;
        var h4 = document.createElement("h4");
        h4.textContent = t("arch.f_" + name);
        facetsEl.appendChild(h4);
        var ul = document.createElement("ul");
        values.forEach(function (v) {
          var li = document.createElement("li");
          var a = document.createElement("a");
          a.href = "#";
          a.textContent = v.value;
          a.title = t("arch.facet_add", { q: queryClause(name, v.value, false) });
          a.addEventListener("click", function (e) {
            e.preventDefault();
            setFilter(name, v.value, false);
          });
          var n = document.createElement("span");
          n.className = "muted";
          n.textContent = v.count.toLocaleString();
          li.appendChild(a);
          li.appendChild(n);
          ul.appendChild(li);
        });
        facetsEl.appendChild(ul);
      });
      facetsEl.hidden = !facetsEl.children.length;
    }

    /* Read a query back into its clauses, the way search.py's _tokenize does.
     *
     * This exists so a plus on a value that is already excluded can flip that
     * one clause instead of appending a contradiction ("-type:ACK type:ACK"
     * matches nothing and looks like a bug in the page). Reading the query
     * requires the same rules the server parses it with, so this mirrors
     * _tokenize deliberately -- the alternative, keeping the active filters in a
     * JavaScript array beside the text box, would be a second filter mechanism
     * next to the query language, and the moment somebody edited the text by
     * hand the two would disagree about what is being searched.
     *
     * Returns null for anything it cannot read back (an unclosed quote, a
     * dangling minus). The caller then leaves the text alone and only appends:
     * the visitor already has a broken query and the server will say so.
     */
    function tokenizeQuery(text) {
      var out = [], i = 0, n = text.length;
      while (i < n) {
        if (/\s/.test(text.charAt(i))) { i += 1; continue; }
        var neg = false;
        if (text.charAt(i) === "-") {
          neg = true; i += 1;
        } else if (text.substr(i, 4).toUpperCase() === "NOT ") {
          neg = true; i += 4;
          while (i < n && /\s/.test(text.charAt(i))) i += 1;
        }
        if (i >= n) return null;
        var field = null;
        var m = /^([A-Za-z_]+):/.exec(text.slice(i));
        if (m) { field = m[1].toLowerCase(); i += m[0].length; }
        var value;
        if (text.charAt(i) === '"') {
          var q = text.indexOf('"', i + 1);
          if (q < 0) return null;
          value = text.slice(i + 1, q);
          i = q + 1;
        } else if (text.charAt(i) === "(") {
          var close = text.indexOf(")", i + 1);
          if (close < 0) return null;
          // Parentheses are kept in the value so an OR list survives a rewrite
          // untouched; nothing here is entitled to reinterpret it.
          value = text.slice(i, close + 1);
          i = close + 1;
        } else {
          var start = i;
          while (i < n && !/\s/.test(text.charAt(i))) i += 1;
          value = text.slice(start, i);
        }
        if (!value) return null;
        out.push({ neg: neg, field: field, value: value });
      }
      return out;
    }

    // Write the clauses back out. Only used when a clause actually changed, so
    // a query nobody touched keeps the spacing its author gave it. "NOT x" does
    // come back as "-x" -- the parser treats them as one and the same, and
    // remembering which spelling was typed would be bookkeeping for nothing.
    function renderQuery(tokens) {
      return tokens.map(function (tok) {
        var v = tok.value;
        var grouped = v.charAt(0) === "(" && v.charAt(v.length - 1) === ")";
        return (tok.neg ? "-" : "") + (tok.field ? tok.field + ":" : "") +
          (grouped ? v : queryValue(v));
      }).join(" ");
    }

    /* Add (or flip) one field:value clause and search again.
     *
     * Refining rather than replacing is the whole point of the facets and of the
     * plus/minus buttons. Three cases, all of them things a visitor does:
     *   - the very same clause is already there: nothing happens, so clicking
     *     the same value twice is not two identical clauses;
     *   - the opposite clause is there: it is flipped in place, because a plus
     *     on something you excluded means "actually, only this", not "both";
     *   - anything else: appended, which is an AND, which is what a space means.
     * Comparison is case-insensitive because the text fields are matched with
     * COLLATE NOCASE, so "ADVERT" and "advert" really are the same clause.
     */
    function setFilter(field, value, negate) {
      var v = String(value).replace(/"/g, "");
      if (!v) return;
      var current = qEl.value.trim();
      var clause = queryClause(field, v, negate);
      var tokens = tokenizeQuery(current);
      if (tokens) {
        for (var i = 0; i < tokens.length; i++) {
          var tok = tokens[i];
          if (tok.field !== field) continue;
          if (tok.value.toLowerCase() !== v.toLowerCase()) continue;
          if (tok.neg === negate) return;    // already exactly what was asked
          tok.neg = negate;
          qEl.value = renderQuery(tokens);
          runSearch(false);
          return;
        }
      }
      qEl.value = current ? current + " " + clause : clause;
      runSearch(false);
    }

    function archRow(p) {
      var li = document.createElement("li");
      li.dataset.id = p.id;
      li.tabIndex = 0;
      // The roles that make the headings' aria-sort mean something: a table
      // whose rows have cells. Set here rather than in the template because the
      // rows are built here; the header row and the table itself carry theirs in
      // packets.html.
      li.setAttribute("role", "row");
      var dot = document.createElement("i");
      dot.style.background = PKT_COLORS[p.type] || "#7d8fa0";
      // No cell of its own: the colour repeats what the Type column already
      // says, so with roles in place it would be an empty extra column that a
      // screen reader has to walk past on every row.
      dot.setAttribute("aria-hidden", "true");
      li.appendChild(dot);
      // Absolute time, not relative: the archive exists to pin down when
      // something happened, and "3 uur geleden" defeats that. Compact 24-hour
      // form rather than toLocaleString: the locale's full rendering runs to
      // 22 characters and squeezes the sender out of its own column.
      var when = document.createElement("time");
      when.className = "pkt-time-abs";
      when.setAttribute("role", "cell");
      when.dateTime = p.ts;
      when.textContent = fmtTs(p.ts);
      li.appendChild(when);
      var who = cell2("pkt-who", p.sender_name || (p.sender || "").toUpperCase() || "");
      if (!who.textContent) {
        var lbl = srcLabel(p.src, t);
        who.textContent = lbl ? lbl.text : t("pkt.sender_short");
        if (lbl) { who.title = lbl.title; who.classList.add("src-derived"); }
      }
      // Only a sender an advert stated has a key in the sender column; a sender
      // merely derived from the address hash gets no buttons, for the same
      // reason the detail panel withholds them there.
      filterBtns(who, "sender", p.sender, setFilter);
      li.appendChild(who);
      li.appendChild(cell2("pkt-obs", p.observer_name ||
        (p.observer || "").slice(0, 6).toUpperCase() || "—", "observer", p.observer));
      li.appendChild(cell2("pkt-type", p.type || "?", "type", p.type));
      var scope = document.createElement("span");
      scope.className = "pkt-scope";
      scope.setAttribute("role", "cell");
      scope.textContent = p.scope ? t("scope." + p.scope) : "—";
      if (p.scope_region) scope.textContent += " · " + p.scope_region;
      filterBtns(scope, "scope", p.scope, setFilter);
      li.appendChild(scope);
      li.appendChild(cell2("pkt-snr", p.snr != null ? p.snr.toFixed(1) + " dB" : "—",
        "snr", p.snr));
      li.appendChild(cell2("pkt-rssi", p.rssi != null ? p.rssi + " dBm" : "—",
        "rssi", p.rssi));
      li.appendChild(cell2("pkt-hops", p.path_len != null ? String(p.path_len) : "—",
        "hops", p.path_len));
      li.appendChild(cell2("pkt-len", p.len != null ? p.len + " B" : "—", "len", p.len));
      li.appendChild(cell2("pkt-cc", p.country || "—", "country", p.country));
      li.addEventListener("click", function () { openPacketModal(p.id); });
      li.addEventListener("keydown", function (e) {
        if (e.key !== "Enter" && e.key !== " ") return;
        e.preventDefault();
        openPacketModal(p.id);
      });
      return li;
    }

    // The column value carries the buttons rather than a separate control
    // column: what you want to filter on is the value you are looking at, and a
    // row of nine button pairs would be a toolbar, not a result.
    function cell2(cls, text, field, value) {
      var el = document.createElement("span");
      el.className = cls;
      el.setAttribute("role", "cell");
      el.textContent = text;
      filterBtns(el, field, value, setFilter);
      return el;
    }

    function renderRows(rows) {
      listEl.textContent = "";
      rows.forEach(function (p) { listEl.appendChild(archRow(p)); });
      emptyEl.hidden = rows.length > 0;
      // Filtering from inside the open detail re-runs the search underneath it,
      // so the row it belongs to has to be marked again on the new list.
      if (openPktId) markOpenRow(openPktId);
    }

    function renderPager() {
      var from = archTotal ? archOffset + 1 : 0;
      var to = Math.min(archOffset + PAGE_SIZE, archTotal);
      pageEl.textContent = t("arch.page", { from: from, to: to,
        total: archTotal.toLocaleString() });
      prevEl.disabled = archOffset <= 0;
      nextEl.disabled = to >= archTotal;
    }

    // --- ordering the results ------------------------------------------------
    // A heading becomes a real <button> instead of getting a click handler of
    // its own. That is what buys focus, Enter and space without a keydown
    // handler reimplementing all three, and what makes a screen reader announce
    // something you can press; the aria-sort that says which way it points goes
    // on the heading around it, because that is the element with the
    // columnheader role.
    function wireSortHeaders() {
      if (!headEl) return;
      Array.prototype.forEach.call(headEl.querySelectorAll("[data-sort]"),
        function (cell) {
          var key = cell.getAttribute("data-sort");
          if (!sortable(key)) {
            // The server does not order by this column. Drop the marker so the
            // heading stays a heading rather than becoming a dead button.
            cell.removeAttribute("data-sort");
            return;
          }
          cell.setAttribute("role", "columnheader");
          var btn = document.createElement("button");
          btn.type = "button";
          btn.className = "sortbtn";
          btn.textContent = cell.textContent.trim();
          // The translation key moves along with the text. Left on the heading
          // it would set textContent there on the next apply() and throw the
          // button out of the page with it.
          var i18nKey = cell.getAttribute("data-i18n");
          if (i18nKey) {
            cell.removeAttribute("data-i18n");
            btn.setAttribute("data-i18n", i18nKey);
          }
          btn.setAttribute("data-i18n-title", "arch.sort_by");
          btn.title = t("arch.sort_by");
          cell.textContent = "";
          cell.appendChild(btn);
          btn.addEventListener("click", function () { setSort(key); });
        });
    }

    // The phone's picker is filled from the headings rather than from
    // SORT_KINDS, so the two views offer the same columns in the same order --
    // and so a column the server can sort but the table does not show can never
    // turn up here alone.
    function buildSortPicker() {
      if (!sortSelEl || !headEl) return;
      Array.prototype.forEach.call(headEl.querySelectorAll("[data-sort]"),
        function (cell) {
          var opt = document.createElement("option");
          opt.value = cell.getAttribute("data-sort");
          opt.textContent = cell.textContent.trim();
          sortSelEl.appendChild(opt);
        });
      sortSelEl.addEventListener("change", function () {
        setSort(sortSelEl.value, true);
      });
      if (sortDirEl) {
        sortDirEl.addEventListener("click", function () { setSort(archSort); });
      }
    }

    /* Order by this column, and search again from the first page.
     *
     * Clicking the column that is already active turns the order around, which
     * is what a second click on a heading means everywhere; clicking another one
     * starts from its own natural end -- a number or a moment from the high end,
     * a word from its A. That first direction comes from the field kind the
     * server sent, so it stays right when a column changes type.
     *
     * ``pick`` is set by the phone's picker, which chose a column and not a
     * direction: turning the order around there because the same column happened
     * to be selected already would answer a choice nobody made.
     */
    function setSort(key, pick) {
      if (!sortable(key)) return;
      if (key === archSort && !pick) archDesc = !archDesc;
      else if (key !== archSort) archDesc = SORT_KINDS[key] !== "text";
      archSort = key;
      renderSort();
      // Back to page one. Offset counts rows in an order that no longer exists,
      // so keeping it would land the reader somewhere in the middle of a list
      // they have not seen the top of.
      runSearch(false);
    }

    // Take over an order the server reported ("hops:desc"), without searching.
    function adoptSort(token) {
      var bits = String(token).split(":");
      if (!sortable(bits[0])) return;
      archSort = bits[0];
      archDesc = bits[1] !== "asc";
      renderSort();
    }

    function renderSort() {
      if (headEl) {
        Array.prototype.forEach.call(headEl.querySelectorAll("[data-sort]"),
          function (cell) {
            var on = cell.getAttribute("data-sort") === archSort;
            // aria-sort is the whole announcement for a screen reader; the
            // arrow drawn by style.css is the same sentence for everyone else.
            cell.setAttribute("aria-sort",
              on ? (archDesc ? "descending" : "ascending") : "none");
            cell.classList.toggle("sorted", on);
          });
      }
      if (sortSelEl) sortSelEl.value = archSort;
      if (sortDirEl) {
        // The button says which way it points now, not what it would do: that
        // reads as a state that can be changed, and it saves a second control
        // for the direction that a phone has no room for.
        sortDirEl.textContent = (archDesc ? "↓ " : "↑ ") +
          t(archDesc ? "arch.sort_desc" : "arch.sort_asc");
      }
    }

    // --- one packet, in full -------------------------------------------------
    // Until now a click on a row opened the raw JSON of the API in a new tab,
    // which answered the question but asked the reader to parse a packet by eye.
    // The live page already had a panel that reads properly, so this shows that
    // same fragment; nothing new is fetched that the API did not already serve.
    function openPacketModal(id) {
      if (!pktModalEl || !id) return;
      openPktId = id;
      blankPacketDetail();
      pktModalEl.hidden = false;
      document.body.style.overflow = "hidden";   // same as the history modal
      var panelEl = pktModalEl.querySelector(".modal-panel");
      if (panelEl) panelEl.scrollTop = 0;
      var closeBtn = document.getElementById("pkt-close");
      if (closeBtn) closeBtn.focus();
      markOpenRow(id);
      pushUrl();
      fetch("/api/v1/packets/" + encodeURIComponent(id))
        .then(function (r) {
          if (!r.ok) throw new Error("HTTP " + r.status);
          return r.json();
        })
        .then(function (d) {
          if (openPktId !== id) return;   // a second click already took over
          // The archive always has something to say about countries: it has a
          // Land column and a country: field whatever the deployment looks like.
          fillPacketDetail(d, { showCountry: true, onFilter: setFilter });
        })
        .catch(function () {
          if (openPktId === id) txt("pkt-path-note", t("pkt.loaderror"));
        });
    }

    function closePacketModal() {
      if (!pktModalEl || pktModalEl.hidden) return;
      pktModalEl.hidden = true;
      document.body.style.overflow = "";
      openPktId = null;
      markOpenRow(-1);
      pushUrl();
    }

    function markOpenRow(id) {
      Array.prototype.forEach.call(listEl.children, function (li) {
        li.classList.toggle("selected", parseInt(li.dataset.id, 10) === id);
      });
    }

    if (pktModalEl) {
      document.getElementById("pkt-close").addEventListener("click", closePacketModal);
      pktModalEl.querySelector(".modal-backdrop")
        .addEventListener("click", closePacketModal);
      document.addEventListener("keydown", function (e) {
        if (e.key === "Escape" && !pktModalEl.hidden) closePacketModal();
      });
    }

    prevEl.addEventListener("click", function () {
      archOffset = Math.max(0, archOffset - PAGE_SIZE);
      runSearch(true);
    });
    nextEl.addEventListener("click", function () {
      archOffset += PAGE_SIZE;
      runSearch(true);
    });
    document.getElementById("arch-form").addEventListener("submit", function (e) {
      e.preventDefault();
      runSearch(false);
    });
    windowEl.addEventListener("change", function () { runSearch(false); });

    wireSortHeaders();
    buildSortPicker();
    renderSort();
    runSearch(false);
    // A link that names a packet opens it straight away, without waiting for the
    // list around it: the detail comes from its own endpoint, and somebody who
    // was sent that link came for the packet, not for the search behind it.
    if (initialPkt) openPacketModal(initialPkt);
  }
})();
