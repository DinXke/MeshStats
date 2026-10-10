/* Logboek: alle gebeurtenissen per tracker, filterbaar en exporteerbaar. */
(async function () {
  const $ = (id) => document.getElementById(id);
  await MT.initHeader("/log");
  const ALERT = ["W", "S", "E", "N", "P", "B", "zone_in", "zone_out", "bat_low", "suspect"];
  const CLS = { E: "bad", bat_low: "warn", suspect: "warn", zone_in: "ok", zone_out: "warn", W: "ok" };
  const store = {
    get(k, d) { try { const v = localStorage.getItem("mt.log." + k); return v === null ? d : JSON.parse(v); } catch (_) { return d; } },
    set(k, v) { try { localStorage.setItem("mt.log." + k, JSON.stringify(v)); } catch (_) { /* geen opslag */ } },
  };
  let types = {}, rows = [];

  const trackers = await MT.api("/api/trackers");
  $("f-trk").innerHTML = trackers.map((t) => `<option value="${t.id}">${MT.esc(t.alias)}${t.kind === "sim" ? " (sim)" : ""}</option>`).join("");
  const savedTrk = store.get("trk", []);
  [...$("f-trk").options].forEach((o) => { o.selected = savedTrk.includes(Number(o.value)); });

  function selectedTypes() { return [...$("f-types").querySelectorAll("input:checked")].map((c) => c.value); }
  function renderTypes(sel) {
    $("f-types").innerHTML = Object.entries(types).map(([k, v]) =>
      `<label class="mini"><input type="checkbox" value="${k}"${sel.includes(k) ? " checked" : ""}> ${MT.esc(v)}</label>`).join("");
  }
  const setTypes = (list) => $("f-types").querySelectorAll("input").forEach((c) => { c.checked = list.includes(c.value); });
  $("t-all").addEventListener("click", () => setTypes(Object.keys(types)));
  $("t-none").addEventListener("click", () => setTypes([]));
  $("t-alerts").addEventListener("click", () => setTypes(ALERT));
  $("f-period").addEventListener("change", () => { $("f-custom").hidden = $("f-period").value !== "custom"; });

  function query(limit = PAGE) {
    const trk = [...$("f-trk").selectedOptions].map((o) => Number(o.value));
    const ready = Object.keys(types).length > 0;
    const q = new URLSearchParams({ limit: String(limit) });
    if (ready) q.set("types", selectedTypes().join(",") || "none");
    if (trk.length) q.set("tracker", trk.join(","));
    if ($("f-period").value === "custom") {
      if ($("f-from").value) q.set("since", Math.floor(new Date($("f-from").value) / 1000));
      if ($("f-to").value) q.set("until", Math.floor(new Date($("f-to").value) / 1000));
    } else q.set("hours", $("f-period").value);
    store.set("trk", trk); store.set("period", $("f-period").value);
    if (ready) store.set("types", selectedTypes());
    return q;
  }

  function desc(e) {
    if (e.type === "zone_in") return `kwam binnen in zone ${MT.esc(e.zone)}`;
    if (e.type === "zone_out") return `verliet zone ${MT.esc(e.zone)}`;
    const bits = [];
    if (e.spd != null && e.type !== "N") bits.push(`${e.spd} km/u`);
    if (e.bat != null) bits.push(`batterij ${e.bat}%`);
    if (e.mode) bits.push(MT.MODE[e.mode] || e.mode);
    if (e.snr != null) bits.push(`SNR ${e.snr}`);
    if (e.path_len != null && e.path_len < 64) bits.push(e.path_len === 0 ? "rechtstreeks" : `${e.path_len} hops`);
    return bits.join(" · ");
  }

  // Per 200 van de server (1.5: offset + more). "Meer laden" vraagt de volgende 200 binnen dezelfde
  // since/until als de eerste pagina, zodat er niets verschuift als er intussen nieuwe bijkomen.
  // CSV haalt alles van de filter op (tot 5000), niet alleen wat er op het scherm staat.
  const PAGE = 200;
  let win = { since: 0, until: 0 }, more = false, busy = false;
  function render() {
    const q = $("f-text").value.trim().toLowerCase();
    const list = rows.filter((e) => !q || `${e.alias} ${e.zone || ""} ${types[e.type] || ""}`.toLowerCase().includes(q));
    $("count").textContent = `${list.length}${more ? "+" : ""} gebeurtenissen` + (q && list.length < rows.length ? ` (van ${rows.length} geladen)` : "");
    $("events").innerHTML = (list.length ? `<table class="log"><thead><tr><th>Tijd</th><th>Tracker</th><th>Gebeurtenis</th>
      <th class="hide-sm">Details</th><th><span class="sr">Op de kaart</span></th></tr></thead><tbody>` + list.map((e) => `<tr>
      <td class="small nowrap lt">${new Date(e.rx_ts * 1000).toLocaleString("nl-BE")}</td>
      <td class="ltrk"><span class="tico" style="background:${MT.esc(e.color)}">${e.icon ? MTIcons.svg(e.icon) : ""}</span> ${MT.esc(e.alias)}</td>
      <td class="lev"><span class="pill ${CLS[e.type] || ""}">${MT.esc(types[e.type] || e.type)}</span></td>
      <td class="hide-sm muted small ldet">${desc(e)}</td>
      <td class="lmap">${e.lat != null ? `<a href="/?focus=${e.tracker_id}&lat=${e.lat}&lon=${e.lon}" title="Op de kaart" aria-label="${MT.esc(e.alias)} op de kaart">kaart</a>` : ""}</td></tr>`).join("")
      + "</tbody></table>" : `<div class="empty">${rows.length ? "Niets gevonden in de geladen gebeurtenissen." : "Geen gebeurtenissen voor deze filter."}</div>`)
      + (more ? `<div class="row logmore"><button type="button" id="f-more">Meer laden (oudere)</button>
          ${q ? '<span class="muted small">Zoeken gebeurt in wat geladen is.</span>' : ""}</div>` : "");
    const mb = $("f-more");
    if (mb) mb.addEventListener("click", loadMore);
    filterSummary();
  }

  // samenvatting op de dichte filterbalk: wat staat er nu ingesteld?
  function filterSummary() {
    const trk = [...$("f-trk").selectedOptions].length;
    const nT = Object.keys(types).length, sel = selectedTypes().length;
    const per = $("f-period").selectedOptions[0] ? $("f-period").selectedOptions[0].textContent : "";
    $("f-sum").textContent = [per, trk ? `${trk} tracker${trk === 1 ? "" : "s"}` : "alle trackers",
      nT ? (sel === nT ? "alle soorten" : `${sel} van ${nT} soorten`) : ""].filter(Boolean).join(" · ");
  }
  $("f-box").addEventListener("change", filterSummary);
  $("f-box").addEventListener("click", (e) => { if (e.target.closest("button")) setTimeout(filterSummary); });
  if (matchMedia("(max-width: 720px)").matches) $("f-box").open = false;      // gsm: eerst de resultaten

  // keep = live bijwerken: evenveel rijen opnieuw ophalen als er nu staan (vanaf het nieuwste)
  async function load(keep) {
    const n = keep === true ? Math.max(PAGE, rows.length) : PAGE;
    const r = await MT.api("/api/events?" + query(n).toString());
    if (!Object.keys(types).length) {
      types = r.types;
      const saved = store.get("types", null);
      renderTypes(saved && saved.length ? saved : Object.keys(types));
      return load();
    }
    rows = r.events;
    more = !!r.more;
    win = { since: r.since, until: r.until };
    render();
  }

  async function loadMore() {
    if (busy) return;
    busy = true;
    const mb = $("f-more");
    if (mb) { mb.disabled = true; mb.textContent = "Laden…"; }
    try {
      const q = query(PAGE);
      q.delete("hours");
      q.set("since", win.since); q.set("until", win.until); q.set("offset", rows.length);
      const r = await MT.api("/api/events?" + q.toString());
      rows = rows.concat(r.events);
      more = !!r.more;
      render();
    } catch (e) {
      if (mb) { mb.disabled = false; mb.textContent = "Opnieuw proberen"; }
    } finally { busy = false; }
  }

  $("f-go").addEventListener("click", load);
  $("f-text").addEventListener("input", render);
  $("f-csv").addEventListener("click", async () => {
    const b = $("f-csv");
    b.disabled = true;
    let all = rows;
    try {
      const q = query(5000);
      if (win.until) { q.delete("hours"); q.set("since", win.since); q.set("until", win.until); }
      all = (await MT.api("/api/events?" + q.toString())).events;
    } catch (_) { /* geen verbinding: dan wat er geladen is */ } finally { b.disabled = false; }
    const head = "tijd,tracker,gebeurtenis,lat,lon,snelheid_kmh,batterij,zone\n";
    const body = all.map((e) => [new Date(e.rx_ts * 1000).toISOString(), `"${(e.alias || "").replace(/"/g, '""')}"`,
      types[e.type] || e.type, e.lat ?? "", e.lon ?? "", e.spd ?? "", e.bat ?? "", e.zone ? `"${e.zone}"` : ""].join(",")).join("\n");
    const a = document.createElement("a");
    a.href = URL.createObjectURL(new Blob([head + body + "\n"], { type: "text/csv" }));
    a.download = "meshtrack-logboek.csv";
    a.click();
  });
  $("f-period").value = store.get("period", "24");
  $("f-custom").hidden = $("f-period").value !== "custom";

  let pending = null;
  MT.live((m) => {
    if (!$("f-live").checked || (m.type !== "position" && m.type !== "geofence")) return;
    clearTimeout(pending);
    pending = setTimeout(() => load(true), 1500);       // bundelen: niet bij elk bericht opnieuw laden; geladen pagina's blijven
  });
  load();
})();
