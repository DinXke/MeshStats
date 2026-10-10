/* Statistieken: kerncijfers, verkeer, ontvangst, inhalen (FIFO), trackers en kwaliteit.
   Alle grafieken worden hier zelf getekend in SVG (geen externe bibliotheek), met een
   tooltip bij muis én tik, toetsenbordbediening en per grafiek een tabel als alternatief.
   Filters staan in de URL (deelbaar) en in localStorage (onthouden). */
(function () {
  "use strict";
  const $ = (id) => document.getElementById(id);
  const esc = MT.esc;
  const H = 3600, D = 86400;
  const LS_KEY = "mt.stats.filters";
  const PERIODS = { "24h": D, "7d": 7 * D, "30d": 30 * D };
  const reduced = () => window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  // ---- opmaak ------------------------------------------------------------------------
  const nfCache = {};
  const nf = (d) => (nfCache[d] ||= new Intl.NumberFormat("nl-BE", { minimumFractionDigits: d, maximumFractionDigits: d }));
  // echt minteken (−) i.p.v. het koppelteken dat Intl geeft: leest beter en breekt niet af
  const num = (v, d = 0) => (v == null || !isFinite(v) ? "–" : nf(d).format(d ? v : Math.round(v)).replace(/^-/, "−"));
  const pct = (part, whole) => (whole ? num((100 * part) / whole, part / whole < 0.1 && part > 0 ? 1 : 0) + "\u00a0%" : "–");
  function dur(s) {
    if (s == null || !isFinite(s)) return "–";
    s = Math.max(0, Math.round(s));
    if (s < 60) return `${s} s`;
    if (s < H) return `${Math.round(s / 60)} min`;
    if (s < D) { const h = Math.floor(s / H), m = Math.round((s % H) / 60); return m ? `${h} u ${m} min` : `${h} u`; }
    const d = Math.floor(s / D), h = Math.round((s % D) / H);
    return h ? `${d} d ${h} u` : `${d} d`;
  }
  const dt = (t) => new Date(t * 1000);
  const dayLab = (d) => d.toLocaleDateString("nl-BE", { weekday: "short", day: "numeric", month: "numeric" });
  const dayLong = (t) => dt(t).toLocaleDateString("nl-BE", { weekday: "short", day: "numeric", month: "short" });
  const hm = (t) => dt(t).toLocaleTimeString("nl-BE", { hour: "2-digit", minute: "2-digit" });
  const stamp = (t) => `${dayLong(t)} ${hm(t)}`;
  function bucketLabel(t, bucket) {
    if (bucket === "day") return dayLong(t);
    return `${dayLong(t)}, ${hm(t)}–${hm(t + H)}`;
  }
  const STATE_COLOR = { M: 1, S: 2, H: 3, Q: 4, L: 5, W: 6, N: 7, E: 8 };
  const stateName = (k) => (MT.STATE[k] ? `${MT.STATE[k]} (${k})` : k);
  const MODE = { c: "companion", t: "tracker", fifo: "FIFO", classic: "klassiek", klassiek: "klassiek", slow: "SlowTrack", slowtrack: "SlowTrack" };
  const KINDS = [
    { key: "live", label: "live", color: "var(--st-c1)" },
    { key: "extra", label: "extra", color: "var(--st-c2)" },
    { key: "slow", label: "SlowTrack", color: "var(--st-c3)" },
    { key: "fifo", label: "FIFO", color: "var(--st-c4)" },
  ];

  // ==== tooltip (één voor de hele pagina) ===============================================
  const tip = $("st-tip");
  const clearers = new Set();
  function showTip(html, x, y) {
    tip.innerHTML = html;
    tip.hidden = false;
    const w = tip.offsetWidth, h = tip.offsetHeight, vw = window.innerWidth;
    let left = x + 14, top = y - h - 12;
    if (left + w > vw - 8) left = Math.max(8, x - w - 14);
    if (top < 8) top = y + 18;
    tip.style.left = left + "px";
    tip.style.top = top + "px";
  }
  function hideTip() { tip.hidden = true; clearers.forEach((f) => f()); }
  document.addEventListener("pointerdown", (e) => { if (!e.target.closest(".st-svg, .st-donut")) hideTip(); });
  // bij scrollen verdwijnt de tooltip, behalve als hij bij het toetsenbord hoort (focus scrolt de grafiek zelf in beeld)
  let reanchor = null;      // tooltip van het toetsenbord: na scrollen opnieuw naast de markering zetten
  window.addEventListener("scroll", () => {
    if (tip.hidden) return;
    if (reanchor && document.activeElement?.matches?.(".st-svg .hit")) reanchor(); else hideTip();
  }, { passive: true });
  document.addEventListener("keydown", (e) => { if (e.key === "Escape") hideTip(); });
  const tipRow = (color, label, value) => `<div class="r"><i style="background:${color}"></i>${esc(label)}<span class="v">${value}</span></div>`;

  /* Aanwijzen met muis, vinger of toetsenbord. locate(ev) -> index of null, show(i) tekent
     de markering en geeft {html, x, y} terug (x,y in schermcoördinaten). */
  function interact(svg, hit, n, locate, show, clear) {
    let cur = -1;
    const at = (i, ev) => {
      if (i == null || i < 0 || i >= n) { clear(); tip.hidden = true; cur = -1; return; }
      cur = i;
      const r = show(i);
      if (!r) { tip.hidden = true; return; }
      if (ev) { reanchor = null; showTip(r.html, ev.clientX, ev.clientY); }
      else { const b = svg.getBoundingClientRect(); showTip(r.html, b.left + r.x, b.top + r.y); reanchor = () => at(cur); }
    };
    svg.addEventListener("pointermove", (ev) => { if (ev.pointerType === "mouse" || ev.buttons) at(locate(ev), ev); });
    svg.addEventListener("pointerdown", (ev) => at(locate(ev), ev));
    svg.addEventListener("pointerleave", (ev) => { if (ev.pointerType === "mouse") { clear(); tip.hidden = true; cur = -1; } });
    if (hit) {
      hit.addEventListener("keydown", (e) => {
        let i = cur;
        if (e.key === "ArrowRight") i = Math.min(n - 1, cur + 1);
        else if (e.key === "ArrowLeft") i = Math.max(0, cur < 0 ? n - 1 : cur - 1);
        else if (e.key === "Home") i = 0;
        else if (e.key === "End") i = n - 1;
        else return;
        e.preventDefault();
        at(i);
      });
      hit.addEventListener("focus", () => { if (cur < 0) at(n - 1); });
      hit.addEventListener("blur", () => { clear(); tip.hidden = true; cur = -1; });
    }
    clearers.add(() => { clear(); cur = -1; });
  }
  const svgX = (svg, ev) => ev.clientX - svg.getBoundingClientRect().left;
  const svgY = (svg, ev) => ev.clientY - svg.getBoundingClientRect().top;

  // ==== schalen en assen ================================================================
  function nice(lo, hi, n = 4) {
    if (!isFinite(lo) || !isFinite(hi)) { lo = 0; hi = 1; }
    if (hi === lo) { hi = lo + (lo === 0 ? 1 : Math.abs(lo) * 0.5); }
    const raw = (hi - lo) / n, p = 10 ** Math.floor(Math.log10(raw)), m = raw / p;
    const step = (m <= 1 ? 1 : m <= 2 ? 2 : m <= 2.5 ? 2.5 : m <= 5 ? 5 : 10) * p;
    const a = Math.floor(lo / step + 1e-9) * step, b = Math.ceil(hi / step - 1e-9) * step;
    const ticks = [];
    for (let v = a; v <= b + step / 2; v += step) ticks.push(+v.toFixed(10));
    return { lo: a, hi: b, ticks, step };
  }
  function timeTicks(t0, t1, maxTicks) {
    const span = Math.max(1, t1 - t0);
    const steps = [H, 2 * H, 3 * H, 6 * H, 12 * H, D, 2 * D, 7 * D, 14 * D, 28 * D];
    const step = steps.find((s) => span / s <= maxTicks) || 28 * D;
    const out = [], d = new Date(t0 * 1000);
    if (step < D) {
      d.setMinutes(0, 0, 0);
      for (let k = 0; k < 2000 && d.getTime() / 1000 <= t1; k++, d.setHours(d.getHours() + 1)) {
        const t = d.getTime() / 1000;
        if (t >= t0 && d.getHours() % (step / H) === 0) out.push({ t, lab: d.getHours() === 0 ? dayLab(d) : `${String(d.getHours()).padStart(2, "0")}:00` });
      }
    } else {
      d.setHours(0, 0, 0, 0);
      for (let k = 0; k < 2000 && d.getTime() / 1000 <= t1; k++, d.setDate(d.getDate() + 1)) {
        const t = d.getTime() / 1000, day = Math.round((d.getTime() - d.getTimezoneOffset() * 60000) / 864e5);
        const ok = step < 7 * D ? day % (step / D) === 0 : d.getDay() === 1 && Math.floor(day / 7) % (step / (7 * D)) === 0;
        if (t >= t0 && ok) out.push({ t, lab: step < 7 * D ? dayLab(d) : d.toLocaleDateString("nl-BE", { day: "numeric", month: "short" }) });
      }
    }
    return out;
  }
  const textW = (s) => String(s).length * 6.3;
  let uid = 0;
  function svgOpen(w, h, title, desc) {
    const id = "sv" + (++uid);
    return { id, html: `<svg class="st-svg" width="${w}" height="${h}" viewBox="0 0 ${w} ${h}" role="img" aria-labelledby="${id}t ${id}d">`
      + `<title id="${id}t">${esc(title)}</title><desc id="${id}d">${esc(desc)}</desc>` };
  }
  function legend(series, totals) {
    if (series.length < 2) return "";
    return `<div class="st-legend" aria-hidden="true">${series.map((s, i) =>
      `<span><i style="background:${s.color}"></i>${esc(s.label)}${totals ? ` <span class="v">${totals[i]}</span>` : ""}</span>`).join("")}</div>`;
  }
  function table(cols, rows, caption) {
    if (!rows.length) return "";
    return `<details class="st-tbl"><summary>Toon als tabel</summary><div class="st-tblwrap"><table>
      <caption class="muted small" style="text-align:left;padding:6px 8px">${esc(caption)}</caption>
      <thead><tr>${cols.map((c) => `<th scope="col"${c.num ? ' class="num"' : ""}>${esc(c.label)}</th>`).join("")}</tr></thead>
      <tbody>${rows.map((r) => `<tr>${r.map((v, i) => `<td${cols[i].num ? ' class="num"' : ""}>${esc(v)}</td>`).join("")}</tr>`).join("")}</tbody>
      </table></div></details>`;
  }
  /* pad door punten, met onderbrekingen waar een waarde ontbreekt */
  function linePath(pts) {
    let d = "", pen = false;
    for (const p of pts) {
      if (p == null) { pen = false; continue; }
      d += (pen ? "L" : "M") + p[0].toFixed(1) + "," + p[1].toFixed(1);
      pen = true;
    }
    return d;
  }
  function bandPath(top, bot) {
    // aaneengesloten stukken waar beide bestaan
    let d = "", seg = [];
    const flush = () => {
      if (seg.length) {
        d += "M" + seg.map((i) => top[i][0].toFixed(1) + "," + top[i][1].toFixed(1)).join("L")
          + "L" + seg.slice().reverse().map((i) => bot[i][0].toFixed(1) + "," + bot[i][1].toFixed(1)).join("L") + "Z";
      }
      seg = [];
    };
    top.forEach((p, i) => { if (p && bot[i]) seg.push(i); else flush(); });
    flush();
    return d;
  }

  // ==== grafiek: tijdreeks (gestapelde vlakken, lijn met band, gestapelde staven) =========
  /* opt: {title, desc, data:[{t,...}], bucket, series:[{key,label,color}], type:"area"|"bars"|"line",
           band:{lo,hi}, yFmt, height, unit, ref:{v,label}} */
  function timeChart(el, opt, anim) {
    const data = opt.data || [];
    const W = Math.max(300, el.clientWidth), Hh = opt.height || (W < 500 ? 200 : 240);
    const bs = opt.bucket === "day" ? D : H;
    const n = data.length;
    const ser = opt.series;
    const stacked = opt.type !== "line";
    const val = (r, k) => { const v = r[k]; return v == null || !isFinite(v) ? null : +v; };
    // y-domein
    let lo = 0, hi = 0;
    const cum = data.map((r) => {
      let c = 0;
      return ser.map((s) => { const v = val(r, s.key); if (stacked) { c += v || 0; return c; } return v; });
    });
    if (stacked) hi = Math.max(0, ...cum.map((c) => c[c.length - 1] || 0));
    else {
      const vals = [];
      data.forEach((r) => { ser.forEach((s) => vals.push(val(r, s.key))); if (opt.band) vals.push(val(r, opt.band.lo), val(r, opt.band.hi)); });
      const ok = vals.filter((v) => v != null);
      lo = Math.min(...ok); hi = Math.max(...ok);
      if (opt.ref) { lo = Math.min(lo, opt.ref.v); hi = Math.max(hi, opt.ref.v); }
      const pad = (hi - lo) * 0.08 || 1; lo -= pad; hi += pad;
    }
    const ys = nice(lo, hi, Hh < 220 ? 3 : 4);
    const yFmt = opt.yFmt || ((v) => num(v, ys.step < 1 ? 1 : 0));
    const ml = Math.max(...ys.ticks.map((t) => textW(yFmt(t)))) + 10, mr = 10, mt = 8, mb = 24;
    const iw = W - ml - mr, ih = Hh - mt - mb;
    const t0 = n ? data[0].t : 0, t1 = n ? data[n - 1].t : 1;
    const span = t1 - t0 + bs;
    const X = (t) => ml + ((t - t0 + bs / 2) / span) * iw;
    const Y = (v) => mt + ih - ((v - ys.lo) / (ys.hi - ys.lo)) * ih;
    const sv = svgOpen(W, Hh, opt.title, opt.desc);
    let s = sv.html;
    // raster en assen
    s += `<g class="grid">${ys.ticks.map((t) => `<line x1="${ml}" x2="${W - mr}" y1="${Y(t).toFixed(1)}" y2="${Y(t).toFixed(1)}"/>`).join("")}</g>`;
    s += `<g class="ax">${ys.ticks.map((t) => `<text x="${ml - 6}" y="${(Y(t) + 3.5).toFixed(1)}" text-anchor="end">${esc(yFmt(t))}</text>`).join("")}`;
    const ticks = timeTicks(t0, t1 + bs, Math.max(3, Math.floor(iw / 60)));
    // labels aan de rand niet laten afknippen: daar rechts of links uitlijnen
    s += ticks.map((k) => {
      const x = ml + ((k.t - t0) / span) * iw, hw = textW(k.lab) / 2;
      const anchor = x + hw > W ? "end" : x - hw < 0 ? "start" : "middle";
      return x < ml - 1 || x > W - mr + 1 ? "" : `<line x1="${x.toFixed(1)}" x2="${x.toFixed(1)}" y1="${mt + ih}" y2="${mt + ih + 4}"/><text x="${x.toFixed(1)}" y="${Hh - 6}" text-anchor="${anchor}">${esc(k.lab)}</text>`;
    }).join("");
    if (ys.lo < 0 && ys.hi > 0) s += `<line class="base" x1="${ml}" x2="${W - mr}" y1="${Y(0)}" y2="${Y(0)}"/>`;
    s += `<line class="base" x1="${ml}" x2="${W - mr}" y1="${mt + ih}" y2="${mt + ih}"/></g>`;
    if (opt.ref && opt.ref.v > ys.lo && opt.ref.v < ys.hi) {
      s += `<g><line x1="${ml}" x2="${W - mr}" y1="${Y(opt.ref.v)}" y2="${Y(opt.ref.v)}" stroke="var(--warn)" stroke-dasharray="4 4" stroke-width="1"/>
        <text x="${W - mr - 2}" y="${Y(opt.ref.v) - 4}" text-anchor="end" style="fill:var(--warn)">${esc(opt.ref.label)}</text></g>`;
    }
    // markeringen
    if (opt.type === "bars") {
      const bw = iw / Math.max(1, n), gap = bw > 6 ? 2 : bw > 3 ? 1 : 0, w = Math.max(1, bw - gap);
      s += `<g>`;
      data.forEach((r, i) => {
        const x = ml + i * bw + gap / 2;
        let prev = 0, g = "";
        ser.forEach((se, k) => {
          const c = cum[i][k];
          if (c > prev) g += `<rect x="${x.toFixed(1)}" y="${Y(c).toFixed(1)}" width="${w.toFixed(1)}" height="${Math.max(0.5, Y(prev) - Y(c) - (k ? 0 : 0)).toFixed(1)}" fill="${se.color}"/>`;
          prev = c;
        });
        if (g) s += `<g class="bar grow" data-i="${i}" style="animation-delay:${Math.min(400, i * (400 / n)).toFixed(0)}ms">${g}</g>`;
      });
      s += `</g>`;
    } else if (opt.type === "area") {
      s += `<g class="reveal">`;
      let prevPts = data.map((r) => [X(r.t), Y(0)]);
      ser.forEach((se, k) => {
        const top = data.map((r, i) => [X(r.t), Y(cum[i][k])]);
        if (n === 1) { // één emmer: smalle staaf i.p.v. onzichtbaar vlak
          s += `<rect x="${(X(t0) - 6).toFixed(1)}" y="${top[0][1].toFixed(1)}" width="12" height="${(prevPts[0][1] - top[0][1]).toFixed(1)}" fill="${se.color}"/>`;
        } else {
          s += `<path d="${bandPath(top, prevPts)}" fill="${se.color}" fill-opacity="0.82" stroke="var(--panel)" stroke-width="1"/>`;
          s += `<path d="${linePath(top)}" fill="none" stroke="${se.color}" stroke-width="1.5"/>`;
        }
        prevPts = top;
      });
      s += `</g>`;
    } else {
      s += `<g class="reveal">`;
      if (opt.band) {
        const top = data.map((r) => (val(r, opt.band.hi) == null ? null : [X(r.t), Y(val(r, opt.band.hi))]));
        const bot = data.map((r) => (val(r, opt.band.lo) == null ? null : [X(r.t), Y(val(r, opt.band.lo))]));
        s += `<path d="${bandPath(top, bot)}" fill="${ser[0].color}" fill-opacity="0.16"/>`;
      }
      ser.forEach((se) => {
        const pts = data.map((r) => (val(r, se.key) == null ? null : [X(r.t), Y(val(r, se.key))]));
        s += `<path d="${linePath(pts)}" fill="none" stroke="${se.color}" stroke-width="2" stroke-linejoin="round" stroke-linecap="round"/>`;
        if (n < 60) s += pts.map((p) => (p ? `<circle cx="${p[0].toFixed(1)}" cy="${p[1].toFixed(1)}" r="2.2" fill="${se.color}"/>` : "")).join("");
      });
      s += `</g>`;
    }
    // aanwijslaag
    s += `<g class="mk" pointer-events="none"></g>`;
    s += `<rect class="hit" x="${ml}" y="${mt}" width="${iw}" height="${ih}" tabindex="0" aria-label="${esc(opt.title)}: gebruik de pijltjestoetsen om per ${opt.bucket === "day" ? "dag" : "uur"} de waarden te lezen"/>`;
    s += `</svg>`;
    const tot = ser.map((se) => num(data.reduce((a, r) => a + (val(r, se.key) || 0), 0)));
    const showTot = stacked && opt.legendTotals !== false;
    // tabel
    const cols = [{ label: opt.bucket === "day" ? "Dag" : "Uur" }, ...ser.map((se) => ({ label: se.label, num: true }))];
    if (opt.band) cols.push({ label: "minimum", num: true }, { label: "maximum", num: true });
    if (stacked && ser.length > 1) cols.push({ label: "totaal", num: true });
    const rows = data.map((r, i) => {
      const row = [bucketLabel(r.t, opt.bucket), ...ser.map((se) => (val(r, se.key) == null ? "–" : yFmtT(opt, val(r, se.key))))];
      if (opt.band) row.push(val(r, opt.band.lo) == null ? "–" : yFmtT(opt, val(r, opt.band.lo)), val(r, opt.band.hi) == null ? "–" : yFmtT(opt, val(r, opt.band.hi)));
      if (stacked && ser.length > 1) row.push(num(cum[i][ser.length - 1]));
      return row;
    });
    el.innerHTML = legend(ser, showTot ? tot : null) + `<div class="st-scroll${anim ? " st-anim" : ""}">${s}</div>` + table(cols, rows, opt.title);
    const svg = el.querySelector("svg"), mk = svg.querySelector(".mk"), hit = svg.querySelector(".hit");
    const clear = () => { mk.innerHTML = ""; svg.querySelectorAll(".bar.dim").forEach((b) => b.classList.remove("dim")); };
    interact(svg, hit, n,
      (ev) => { const x = svgX(svg, ev); if (x < ml - 4 || x > W - mr + 4) return null; return Math.max(0, Math.min(n - 1, Math.floor(((x - ml) / iw) * n))); },
      (i) => {
        const r = data[i], x = opt.type === "bars" ? ml + (i + 0.5) * (iw / n) : X(r.t);
        let m = `<line class="xh" x1="${x}" x2="${x}" y1="${mt}" y2="${mt + ih}"/>`;
        if (opt.type === "bars") svg.querySelectorAll(".bar").forEach((b) => b.classList.toggle("dim", +b.dataset.i !== i));
        else ser.forEach((se, k) => {
          const v = stacked ? cum[i][k] : val(r, se.key);
          if (v != null) m += `<circle class="dotm" cx="${x}" cy="${Y(v)}" r="4.5" fill="${se.color}"/>`;
        });
        mk.innerHTML = m;
        let html = `<div class="t">${esc(bucketLabel(r.t, opt.bucket))}</div>`;
        const list = stacked ? ser.slice().reverse() : ser;
        html += list.map((se) => tipRow(se.color, se.label, val(r, se.key) == null ? "–" : yFmtT(opt, val(r, se.key)))).join("");
        if (opt.band && val(r, opt.band.lo) != null) html += `<div class="muted">bereik ${yFmtT(opt, val(r, opt.band.lo))} tot ${yFmtT(opt, val(r, opt.band.hi))}</div>`;
        if (stacked && ser.length > 1) html += `<div class="r"><span>totaal</span><span class="v">${num(cum[i][ser.length - 1])}</span></div>`;
        return { html, x, y: mt + 10 };
      }, clear);
  }
  const yFmtT = (opt, v) => (opt.tipFmt ? opt.tipFmt(v) : num(v));

  // ==== grafiek: staven per categorie (histogram, uur van de dag, hops) =====================
  /* opt: {title, desc, items:[{label, axis, value, tip}], color, unit, every, height} */
  function barChart(el, opt, anim) {
    const items = opt.items, n = items.length;
    const W = Math.max(opt.minWidth || 280, el.clientWidth), Hh = opt.height || 200;
    const max = Math.max(0, ...items.map((d) => d.value));
    const ys = nice(0, max || 1, 3);
    const ml = Math.max(...ys.ticks.map((t) => textW(num(t)))) + 10, mr = 6, mt = 14, mb = 24;
    const iw = W - ml - mr, ih = Hh - mt - mb, bw = iw / n, gap = bw > 8 ? Math.min(6, bw * 0.18) : 1;
    const Y = (v) => mt + ih - (v / ys.hi) * ih;
    const every = opt.every || Math.max(1, Math.ceil(n / Math.max(2, Math.floor(iw / 44))));
    const sv = svgOpen(W, Hh, opt.title, opt.desc);
    let s = sv.html;
    s += `<g class="grid">${ys.ticks.map((t) => `<line x1="${ml}" x2="${W - mr}" y1="${Y(t).toFixed(1)}" y2="${Y(t).toFixed(1)}"/>`).join("")}</g>`;
    s += `<g class="ax">${ys.ticks.map((t) => `<text x="${ml - 6}" y="${(Y(t) + 3.5).toFixed(1)}" text-anchor="end">${num(t)}</text>`).join("")}`;
    s += items.map((d, i) => (i % every ? "" : `<text x="${(ml + (i + 0.5) * bw).toFixed(1)}" y="${Hh - 6}" text-anchor="middle">${esc(d.axis ?? d.label)}</text>`)).join("");
    s += `<line class="base" x1="${ml}" x2="${W - mr}" y1="${mt + ih}" y2="${mt + ih}"/></g><g>`;
    items.forEach((d, i) => {
      if (!d.value) return;
      const x = ml + i * bw + gap / 2, w = Math.max(1, bw - gap), y = Y(d.value), h = mt + ih - y;
      const r = Math.min(4, w / 2, h);
      // afgeronde bovenkant, vlakke voet op de basislijn
      s += `<path class="bar grow" data-i="${i}" style="animation-delay:${Math.round((i * 300) / n)}ms" fill="${d.color || opt.color}" d="M${x.toFixed(1)},${(y + h).toFixed(1)}V${(y + r).toFixed(1)}Q${x.toFixed(1)},${y.toFixed(1)} ${(x + r).toFixed(1)},${y.toFixed(1)}H${(x + w - r).toFixed(1)}Q${(x + w).toFixed(1)},${y.toFixed(1)} ${(x + w).toFixed(1)},${(y + r).toFixed(1)}V${(y + h).toFixed(1)}Z"/>`;
    });
    if (opt.peakLabel && max > 0) {
      const i = items.findIndex((d) => d.value === max);
      s += `<text class="lbl fade" x="${(ml + (i + 0.5) * bw).toFixed(1)}" y="${(Y(max) - 4).toFixed(1)}" text-anchor="middle">${num(max)}</text>`;
    }
    s += `</g><rect class="hit" x="${ml}" y="${mt}" width="${iw}" height="${ih}" tabindex="0" aria-label="${esc(opt.title)}: gebruik de pijltjestoetsen om de waarden te lezen"/></svg>`;
    const total = items.reduce((a, d) => a + d.value, 0);
    // opt.legend: uitleg bij staven die een andere kleur krijgen (zo hangt niets aan kleur alleen)
    el.innerHTML = (opt.legend ? legend(opt.legend) : "") + `<div class="st-scroll${anim ? " st-anim" : ""}">${s}</div>`
      + table([{ label: opt.catLabel || "Klasse" }, { label: opt.unit || "aantal", num: true }, { label: "aandeel", num: true }],
        items.map((d) => [d.label, num(d.value), pct(d.value, total)]), opt.title);
    const svg = el.querySelector("svg");
    const clear = () => svg.querySelectorAll(".bar.dim").forEach((b) => b.classList.remove("dim"));
    interact(svg, svg.querySelector(".hit"), n,
      (ev) => { const x = svgX(svg, ev); if (x < ml || x > W - mr) return null; return Math.min(n - 1, Math.floor(((x - ml) / iw) * n)); },
      (i) => {
        svg.querySelectorAll(".bar").forEach((b) => b.classList.toggle("dim", +b.dataset.i !== i));
        const d = items[i];
        return { html: `<div class="t">${esc(d.label)}</div>${tipRow(d.color || opt.color, opt.unit || "aantal", num(d.value))}<div class="muted">${pct(d.value, total)} van het totaal</div>`,
          x: ml + (i + 0.5) * bw, y: Y(d.value) };
      }, clear);
  }

  // ==== grafiek: donut =====================================================================
  function donut(el, opt, anim) {
    const items = opt.items.filter((d) => d.value > 0);
    const total = items.reduce((a, d) => a + d.value, 0);
    const S = 170, R = 64, sw = 22, C = 2 * Math.PI * R;
    const sv = svgOpen(S, S, opt.title, opt.desc);
    let s = sv.html + `<g transform="rotate(-90 ${S / 2} ${S / 2})">`;
    let off = 0;
    items.forEach((d, i) => {
      const len = (d.value / total) * C, vis = Math.max(0.5, len - (items.length > 1 ? 2 : 0));
      s += `<circle class="arc" data-i="${i}" cx="${S / 2}" cy="${S / 2}" r="${R}" fill="none" stroke="${d.color}" stroke-width="${sw}"
        stroke-dasharray="${vis.toFixed(2)} ${(C - vis).toFixed(2)}" stroke-dashoffset="${(-off).toFixed(2)}" style="animation-delay:${i * 60}ms"/>`;
      off += len;
    });
    s += `</g><text class="ctr-v" x="${S / 2}" y="${S / 2 + 4}" text-anchor="middle">${num(total)}</text>
      <text class="ctr-l" x="${S / 2}" y="${S / 2 + 22}" text-anchor="middle">${esc(opt.centerLabel || "totaal")}</text></svg>`;
    const leg = `<div class="st-legend">${items.map((d, i) => `<button type="button" data-i="${i}"><i style="background:${d.color}"></i>${esc(d.label)}<span class="v">${num(d.value)} · ${pct(d.value, total)}</span></button>`).join("")}</div>`;
    el.innerHTML = `<div class="st-donut${anim ? " st-anim" : ""}">${s}${leg}</div>`
      + table([{ label: opt.catLabel || "Soort" }, { label: "aantal", num: true }, { label: "aandeel", num: true }],
        items.map((d) => [d.label, num(d.value), pct(d.value, total)]), opt.title);
    const svg = el.querySelector("svg"), btns = [...el.querySelectorAll(".st-legend button")];
    const clear = () => { svg.querySelectorAll(".arc").forEach((a) => a.classList.remove("dim")); btns.forEach((b) => b.classList.remove("on")); };
    const show = (i) => {
      svg.querySelectorAll(".arc").forEach((a) => a.classList.toggle("dim", +a.dataset.i !== i));
      btns.forEach((b) => b.classList.toggle("on", +b.dataset.i === i));
      const d = items[i];
      return { html: `<div class="t">${esc(d.label)}</div>${tipRow(d.color, "berichten", num(d.value))}<div class="muted">${pct(d.value, total)} van alle berichten</div>`, x: S / 2, y: 20 };
    };
    interact(svg, null, items.length, (ev) => { const a = ev.target.closest(".arc"); return a ? +a.dataset.i : null; }, show, clear);
    btns.forEach((b) => {
      const go = (ev) => { const r = show(+b.dataset.i); const bb = b.getBoundingClientRect(); showTip(r.html, ev && ev.clientX ? ev.clientX : bb.right - 20, ev && ev.clientY ? ev.clientY : bb.top); };
      b.addEventListener("pointerenter", (ev) => { if (ev.pointerType === "mouse") go(ev); });
      b.addEventListener("pointerleave", (ev) => { if (ev.pointerType === "mouse") { clear(); tip.hidden = true; } });
      b.addEventListener("click", (ev) => go(ev.detail ? ev : null));
      b.addEventListener("focus", () => go(null));
      b.addEventListener("blur", () => { clear(); tip.hidden = true; });
    });
  }

  // ==== grafiek: warmtekaart weekdag × uur ======================================================
  function heatmap(el, opt, anim) {
    const m = opt.matrix, days = ["ma", "di", "wo", "do", "vr", "za", "zo"];
    // past ook op een gsm (≈ 13 px per uur) zonder zijwaarts te schuiven
    const W = Math.max(280, el.clientWidth), ml = 26, mt = 6, mb = 40, mr = 4;
    const cw = (W - ml - mr) / 24, ch = Math.min(28, Math.max(20, cw * 0.8)), Hh = mt + ch * 7 + mb;
    const max = Math.max(1, ...m.flat());
    const sv = svgOpen(W, Hh, opt.title, opt.desc);
    let s = sv.html + `<g class="ax">`;
    days.forEach((d, r) => { s += `<text x="${ml - 6}" y="${(mt + r * ch + ch / 2 + 4).toFixed(1)}" text-anchor="end">${d}</text>`; });
    for (let h = 0; h < 24; h += 3) s += `<text x="${(ml + h * cw + cw / 2).toFixed(1)}" y="${mt + 7 * ch + 14}" text-anchor="middle">${h}u</text>`;
    s += `</g><g class="fade">`;
    m.forEach((row, r) => row.forEach((v, h) => {
      const op = v ? 0.12 + 0.88 * (v / max) : 1;
      s += `<rect x="${(ml + h * cw + 1).toFixed(1)}" y="${(mt + r * ch + 1).toFixed(1)}" width="${(cw - 2).toFixed(1)}" height="${(ch - 2).toFixed(1)}" rx="${cw < 16 ? 2 : 3}"
        fill="${v ? "var(--st-seq)" : "var(--st-grid)"}" fill-opacity="${v ? op.toFixed(2) : 0.45}"/>`;
    }));
    // schaal
    const lx = W - mr - 150, ly = Hh - 12;
    s += `<text x="${lx - 6}" y="${ly + 4}" text-anchor="end">minder</text>`;
    for (let k = 0; k < 5; k++) s += `<rect x="${lx + k * 22}" y="${ly - 5}" width="20" height="10" rx="2" fill="var(--st-seq)" fill-opacity="${(0.12 + 0.22 * k).toFixed(2)}"/>`;
    s += `<text x="${lx + 114}" y="${ly + 4}">meer</text>`;
    s += `</g><rect class="sel" fill="none" stroke="var(--text)" stroke-width="2" rx="3" width="0" height="0" pointer-events="none"/>
      <rect class="hit" x="${ml}" y="${mt}" width="${W - ml - mr}" height="${7 * ch}" tabindex="0" aria-label="${esc(opt.title)}: gebruik de pijltjestoetsen om per uur de waarden te lezen"/></svg>`;
    const rows = [];
    m.forEach((row, r) => row.forEach((v, h) => { if (v) rows.push([days[r], `${h}:00–${h + 1}:00`, num(v)]); }));
    el.innerHTML = `<div class="st-scroll${anim ? " st-anim" : ""}">${s}</div>` + table([{ label: "Weekdag" }, { label: "Uur" }, { label: "berichten", num: true }], rows, opt.title);
    const svg = el.querySelector("svg"), sel = svg.querySelector(".sel");
    const clear = () => sel.setAttribute("width", 0);
    interact(svg, svg.querySelector(".hit"), 7 * 24,
      (ev) => {
        const x = svgX(svg, ev), y = svgY(svg, ev);
        const h = Math.floor((x - ml) / cw), r = Math.floor((y - mt) / ch);
        return h < 0 || h > 23 || r < 0 || r > 6 ? null : r * 24 + h;
      },
      (i) => {
        const r = Math.floor(i / 24), h = i % 24;
        sel.setAttribute("x", ml + h * cw + 1); sel.setAttribute("y", mt + r * ch + 1);
        sel.setAttribute("width", cw - 2); sel.setAttribute("height", ch - 2);
        const full = ["maandag", "dinsdag", "woensdag", "donderdag", "vrijdag", "zaterdag", "zondag"][r];
        return { html: `<div class="t">${full}, ${h}:00–${h + 1}:00</div>${tipRow("var(--st-seq)", "berichten", num(m[r][h]))}`, x: ml + h * cw + cw, y: mt + r * ch };
      }, clear);
  }

  // ==== grafiek: horizontale staven per tracker (inhalen) ======================================
  function hbars(el, opt, anim) {
    const rows = opt.rows, ser = opt.series;
    const W = Math.max(300, el.clientWidth);
    const lw = Math.min(140, Math.max(70, ...rows.map((r) => textW(r.label) + 8)));
    const rh = ser.length * 12 + 14, mt = 4, mb = 22, mr = 68;
    const Hh = mt + rows.length * rh + mb;
    const max = Math.max(1, ...rows.flatMap((r) => ser.map((s) => r[s.key] || 0)));
    const xs = nice(0, max, W < 500 ? 3 : 5);
    const iw = W - lw - mr, X = (v) => lw + (v / xs.hi) * iw;
    const sv = svgOpen(W, Hh, opt.title, opt.desc);
    let s = sv.html + `<g class="grid">${xs.ticks.map((t) => `<line x1="${X(t)}" x2="${X(t)}" y1="${mt}" y2="${Hh - mb}"/>`).join("")}</g>
      <g class="ax">${xs.ticks.map((t) => `<text x="${X(t)}" y="${Hh - 6}" text-anchor="middle">${num(t)}</text>`).join("")}</g>`;
    rows.forEach((r, i) => {
      const y0 = mt + i * rh + 6;
      const lab = r.label.length > 18 ? r.label.slice(0, 17) + "…" : r.label;
      s += `<text x="${lw - 8}" y="${y0 + ser.length * 6 + 3}" text-anchor="end" class="lbl" style="font-weight:500">${esc(lab)}</text>`;
      ser.forEach((se, k) => {
        const v = r[se.key] || 0, w = Math.max(v ? 2 : 0, X(v) - lw);
        s += `<rect class="bar growx" data-i="${i}" x="${lw}" y="${y0 + k * 12}" width="${w.toFixed(1)}" height="10" rx="2" fill="${se.color}" style="animation-delay:${i * 40}ms"/>`;
      });
      if (opt.note) s += `<text x="${W - 4}" y="${y0 + ser.length * 6 + 3}" text-anchor="end">${esc(opt.note(r))}</text>`;
    });
    s += `<rect class="hit" x="0" y="${mt}" width="${W}" height="${rows.length * rh}" tabindex="0" aria-label="${esc(opt.title)}: gebruik de pijltjestoetsen om per tracker de waarden te lezen"/></svg>`;
    el.innerHTML = legend(ser) + `<div class="st-scroll${anim ? " st-anim" : ""}">${s}</div>` + (opt.table || "");
    const svg = el.querySelector("svg");
    const clear = () => svg.querySelectorAll(".bar.dim").forEach((b) => b.classList.remove("dim"));
    interact(svg, svg.querySelector(".hit"), rows.length,
      (ev) => { const y = svgY(svg, ev) - mt; const i = Math.floor(y / rh); return i >= 0 && i < rows.length ? i : null; },
      (i) => {
        svg.querySelectorAll(".bar").forEach((b) => b.classList.toggle("dim", +b.dataset.i !== i));
        const r = rows[i];
        return { html: `<div class="t">${esc(r.label)}</div>${ser.map((se) => tipRow(se.color, se.label, num(r[se.key]))).join("")}${opt.tipExtra ? opt.tipExtra(r) : ""}`,
          x: X(Math.max(...ser.map((se) => r[se.key] || 0))), y: mt + i * rh + 6 };
      }, clear);
  }

  // ==== grafiek: tijdlijn met opgevulde gaten ================================================
  function gapChart(el, opt, anim) {
    const rows = opt.rows, t0 = opt.from, t1 = opt.to;
    const W = Math.max(300, el.clientWidth);
    const lw = Math.min(130, Math.max(70, ...rows.map((r) => textW(r.label) + 8)));
    const rh = 24, mt = 4, mb = 24, mr = 8, Hh = mt + rows.length * rh + mb;
    const iw = W - lw - mr, X = (t) => lw + ((Math.min(t1, Math.max(t0, t)) - t0) / (t1 - t0)) * iw;
    const flat = [];
    const sv = svgOpen(W, Hh, opt.title, opt.desc);
    let s = sv.html + `<g class="ax">`;
    timeTicks(t0, t1, Math.max(3, Math.floor(iw / 60))).forEach((k) => {
      s += `<line x1="${X(k.t)}" x2="${X(k.t)}" y1="${mt}" y2="${Hh - mb}" style="stroke:var(--st-grid)"/><text x="${X(k.t)}" y="${Hh - 6}" text-anchor="middle">${esc(k.lab)}</text>`;
    });
    s += `</g>`;
    rows.forEach((r, i) => {
      const y = mt + i * rh;
      const lab = r.label.length > 16 ? r.label.slice(0, 15) + "…" : r.label;
      s += `<text x="${lw - 8}" y="${y + rh / 2 + 4}" text-anchor="end" class="lbl" style="font-weight:500">${esc(lab)}</text>`;
      s += `<line x1="${lw}" x2="${W - mr}" y1="${y + rh / 2}" y2="${y + rh / 2}" stroke="var(--line)" stroke-width="2" stroke-linecap="round"/>`;
      r.gaps.forEach((g) => {
        const k = flat.length;
        flat.push({ r, g });
        const x = X(g.from), w = Math.max(3, X(g.to) - x);
        s += `<rect class="bar growx" data-k="${k}" x="${x.toFixed(1)}" y="${y + 5}" width="${w.toFixed(1)}" height="${rh - 10}" rx="3" fill="var(--st-c4)" style="animation-delay:${i * 50}ms"/>`;
      });
    });
    s += `</svg>`;
    el.innerHTML = `<div class="st-scroll${anim ? " st-anim" : ""}">${s}</div>` + (opt.table || "");
    const svg = el.querySelector("svg");
    const clear = () => svg.querySelectorAll(".bar.dim").forEach((b) => b.classList.remove("dim"));
    interact(svg, null, flat.length, (ev) => { const b = ev.target.closest("[data-k]"); return b ? +b.dataset.k : null; },
      (k) => {
        svg.querySelectorAll(".bar").forEach((b) => b.classList.toggle("dim", +b.dataset.k !== k));
        const { r, g } = flat[k];
        return { html: `<div class="t">${esc(r.label)}</div><div>${esc(stamp(g.from))} → ${esc(hm(g.to))}</div>
          ${tipRow("var(--st-c4)", "gat", dur(g.to - g.from))}${tipRow("var(--st-c4)", "ingehaalde punten", num(g.filled_points))}`, x: X(g.to), y: 0 };
      }, clear);
  }

  // ==== grafiek: gestapelde horizontale staven (repeaters, hops per tracker) ==================
  /* opt: {title, desc, rows:[{label,...}], series:[{key,label,color}], max, xFmt, note(r), tipExtra(r), table} */
  function stackBars(el, opt, anim) {
    const rows = opt.rows, ser = opt.series;
    const W = Math.max(300, el.clientWidth);
    const lw = Math.min(150, Math.max(70, ...rows.map((r) => textW(r.label) + 8)));
    const rh = 26, mt = 4, mb = 22, mr = opt.note ? 62 : 10, Hh = mt + rows.length * rh + mb;
    const tot = (r) => ser.reduce((a, s) => a + (r[s.key] || 0), 0);
    const xs = nice(0, opt.max || Math.max(1, ...rows.map(tot)), W < 500 ? 3 : 5);
    const iw = W - lw - mr, X = (v) => lw + (v / xs.hi) * iw;
    const xFmt = opt.xFmt || ((v) => num(v));
    const sv = svgOpen(W, Hh, opt.title, opt.desc);
    let s = sv.html + `<g class="grid">${xs.ticks.map((t) => `<line x1="${X(t)}" x2="${X(t)}" y1="${mt}" y2="${Hh - mb}"/>`).join("")}</g>
      <g class="ax">${xs.ticks.map((t) => `<text x="${X(t)}" y="${Hh - 6}" text-anchor="middle">${esc(xFmt(t))}</text>`).join("")}</g>`;
    rows.forEach((r, i) => {
      const y = mt + i * rh + 5, h = rh - 10;
      const lab = r.label.length > 20 ? r.label.slice(0, 19) + "…" : r.label;
      s += `<text x="${lw - 8}" y="${y + h / 2 + 4}" text-anchor="end" class="lbl" style="font-weight:500">${esc(lab)}</text>`;
      let x = lw, g = "";
      ser.forEach((se) => {
        const v = r[se.key] || 0;
        if (!v) return;
        const w = Math.max(1, X(v) - lw - 2);
        g += `<rect x="${x.toFixed(1)}" y="${y}" width="${w.toFixed(1)}" height="${h}" rx="2" fill="${se.color}"/>`;
        x += w + 2;
      });
      s += `<g class="bar growx" data-i="${i}" style="animation-delay:${i * 40}ms">${g}</g>`;
      if (opt.note) s += `<text x="${W - 4}" y="${y + h / 2 + 4}" text-anchor="end">${esc(opt.note(r))}</text>`;
    });
    s += `<rect class="hit" x="0" y="${mt}" width="${W}" height="${rows.length * rh}" tabindex="0" aria-label="${esc(opt.title)}: gebruik de pijltjestoetsen om per rij de waarden te lezen"/></svg>`;
    el.innerHTML = legend(ser) + `<div class="st-scroll${anim ? " st-anim" : ""}">${s}</div>` + (opt.table || "");
    const svg = el.querySelector("svg");
    const clear = () => svg.querySelectorAll(".bar.dim").forEach((b) => b.classList.remove("dim"));
    interact(svg, svg.querySelector(".hit"), rows.length,
      (ev) => { const i = Math.floor((svgY(svg, ev) - mt) / rh); return i >= 0 && i < rows.length ? i : null; },
      (i) => {
        svg.querySelectorAll(".bar").forEach((b) => b.classList.toggle("dim", +b.dataset.i !== i));
        const r = rows[i];
        return { html: `<div class="t">${esc(r.label)}</div>${ser.map((se) => tipRow(se.color, se.label, esc(xFmt(r[se.key] || 0)))).join("")}${opt.tipExtra ? opt.tipExtra(r) : ""}`,
          x: X(tot(r)), y: mt + i * rh + 5 };
      }, clear);
  }

  // ==== routes als ketting: tracker → hop → … → antenne ======================================
  function chains(el, opt, anim) {
    const max = Math.max(1, ...opt.paths.map((p) => p.count));
    const step = el.clientWidth < 500 ? 66 : 76;
    const html = opt.paths.map((p, i) => {
      const hops = p.hops;
      const nodes = [{ k: "trk", lab: "tracker" }, ...hops.map((h) => ({ k: "hop", lab: h.short, full: h.full })), { k: "srv", lab: "antenne" }];
      const pad = 28, w = (nodes.length - 1) * step + 2 * pad, cy = 16;
      let s = `<svg class="st-chain" width="${w}" height="44" viewBox="0 0 ${w} 44" role="img" aria-label="${esc(`Route ${i + 1}: tracker, ${hops.map((h) => h.full).join(", ") || "rechtstreeks"}, antenne; ${num(p.count)} berichten`)}">`;
      s += `<line class="ln" x1="${pad}" x2="${w - pad}" y1="${cy}" y2="${cy}"/>`;
      nodes.forEach((n, k) => {
        const x = pad + k * step;
        if (n.k === "hop") s += `<circle class="nd" cx="${x}" cy="${cy}" r="9"><title>${esc(n.full)}</title></circle>`;
        else s += `<rect class="nd ${n.k}" x="${x - 9}" y="${cy - 9}" width="18" height="18" rx="${n.k === "srv" ? 4 : 9}"/>`
          + `<text class="ic" x="${x}" y="${cy + 4}" text-anchor="middle">${n.k === "srv" ? "S" : "T"}</text>`;
        s += `<text class="lb" x="${x}" y="40" text-anchor="middle">${esc(n.lab)}</text>`;
      });
      if (!reduced()) s += `<circle class="trav" cx="${pad}" cy="${cy}" r="4" style="--len:${w - 2 * pad}px;animation-duration:${(1.2 + hops.length * 0.5).toFixed(1)}s;animation-delay:${(i * 0.4).toFixed(1)}s"/>`;
      s += `</svg>`;
      return `<div class="st-route${anim ? " fade" : ""}"><div class="st-scroll">${s}</div>
        <div class="st-rmeta"><span class="st-rbar"><span style="width:${((100 * p.count) / max).toFixed(1)}%"></span></span>
        <strong>${num(p.count)}</strong> <span class="muted">berichten${p.avg_snr == null ? "" : ` · ${num(p.avg_snr, 1)} dB`}</span></div></div>`;
    }).join("");
    el.innerHTML = `<div class="st-routes${anim ? " st-anim" : ""}">${html}</div>` + table(
      [{ label: "Route" }, { label: "berichten", num: true }, { label: "gem. SNR", num: true }],
      opt.paths.map((p) => [["tracker", ...p.hops.map((h) => h.full), "antenne"].join(" → "), num(p.count), p.avg_snr == null ? "–" : `${num(p.avg_snr, 1)} dB`]), opt.title);
  }

  // ==== sparkline =================================================================================
  function spark(vals, cls, label) {
    const w = 84, h = 24, ok = vals.map((v, i) => [i, v]).filter((p) => p[1] != null && isFinite(p[1]));
    if (ok.length < 2) return `<span class="muted small">–</span>`;
    const lo = Math.min(...ok.map((p) => p[1])), hi = Math.max(...ok.map((p) => p[1]));
    const X = (i) => 1 + (i / (vals.length - 1)) * (w - 4), Y = (v) => h - 3 - ((v - lo) / (hi - lo || 1)) * (h - 6);
    const pts = vals.map((v, i) => (v == null || !isFinite(v) ? null : [X(i), Y(v)]));
    const last = ok[ok.length - 1];
    const area = cls === "snr" ? "" : `<path class="a" d="${linePath(pts)}L${X(last[0]).toFixed(1)},${h}L${X(ok[0][0]).toFixed(1)},${h}Z"/>`;
    return `<svg class="spark ${cls}" width="${w}" height="${h}" viewBox="0 0 ${w} ${h}" role="img" aria-label="${esc(label)}"><title>${esc(label)}</title>${area}<path class="l" d="${linePath(pts)}"/><circle cx="${X(last[0]).toFixed(1)}" cy="${Y(last[1]).toFixed(1)}" r="2.2"/></svg>`;
  }

  // ==== toestanden: laden, leeg, fout =================================================================
  const EMPTY_ICON = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" aria-hidden="true"><path d="M3 3v18h18"/><path d="M7 15l3-3 3 2 5-6" stroke-dasharray="2 2.5"/></svg>`;
  const skel = (el, h = 200) => { el.innerHTML = `<div class="st-skel" style="height:${h}px" aria-busy="true" aria-label="Laden…"></div>`; };
  const empty = (el, text) => { el.innerHTML = `<div class="st-empty">${EMPTY_ICON}<div>${esc(text || "Geen gegevens in deze periode")}</div></div>`; };
  function failed(el, err) {
    el.innerHTML = `<div class="st-error" role="alert"><div>Kon deze gegevens niet laden${err ? `: ${esc(err)}` : ""}.</div><button type="button">Opnieuw proberen</button></div>`;
    el.querySelector("button").addEventListener("click", () => load({ anim: true }));
  }

  // ==== filters ======================================================================================
  const F = { period: "7d", from: null, to: null, tracker: "", channel: "", auto: false };
  function readFilters() {
    const q = new URLSearchParams(location.search);
    let src = null;
    if (["periode", "tracker", "kanaal", "van", "tot", "auto"].some((k) => q.has(k))) {
      src = { period: q.get("periode"), from: +q.get("van") || null, to: +q.get("tot") || null,
        tracker: q.get("tracker") || "", channel: q.get("kanaal") || "", auto: q.get("auto") === "1" };
    } else {
      try { src = JSON.parse(localStorage.getItem(LS_KEY) || "null"); } catch (_) { src = null; }
    }
    if (src) Object.assign(F, src);
    if (!PERIODS[F.period] && F.period !== "custom") F.period = "7d";
    if (F.period === "custom" && !(F.from && F.to && F.to > F.from)) F.period = "7d";
  }
  function saveFilters() {
    try { localStorage.setItem(LS_KEY, JSON.stringify(F)); } catch (_) { /* geen opslag */ }
    const q = new URLSearchParams();
    q.set("periode", F.period);
    if (F.period === "custom") { q.set("van", F.from); q.set("tot", F.to); }
    if (F.tracker) q.set("tracker", F.tracker);
    if (F.channel) q.set("kanaal", F.channel);
    if (F.auto) q.set("auto", "1");
    history.replaceState(null, "", location.pathname + "?" + q + location.hash);
  }
  function range() {
    if (F.period === "custom") return { from: F.from, to: F.to };
    const to = Math.floor(Date.now() / 1000);
    return { from: to - PERIODS[F.period], to };
  }
  const pickBucket = (r) => (r.to - r.from <= 8 * D ? "hour" : "day");
  function query(r, extra) {
    const q = new URLSearchParams({ from: r.from, to: r.to });
    if (F.tracker) q.set("tracker", F.tracker);
    if (F.channel) q.set("channel", F.channel);
    Object.entries(extra || {}).forEach(([k, v]) => q.set(k, v));
    return q.toString();
  }
  const toLocalInput = (t) => { const d = dt(t); d.setMinutes(d.getMinutes() - d.getTimezoneOffset()); return d.toISOString().slice(0, 16); };
  const fromLocalInput = (s) => (s ? Math.floor(new Date(s).getTime() / 1000) : null);

  let TRACKERS = [], CHANNELS = [];
  function trackerName(id) { const t = TRACKERS.find((x) => String(x.id) === String(id)); return t ? t.alias || `#${t.id}` : `#${id}`; }
  function channelName(id) { const c = CHANNELS.find((x) => String(x.id) === String(id)); return c ? c.name : String(id); }
  function fillSelects() {
    const ts = $("f-tracker"), cs = $("f-channel");
    const list = TRACKERS.filter((t) => !F.channel || t.channel_id == null || String(t.channel_id) === String(F.channel))
      .slice().sort((a, b) => String(a.alias || "").localeCompare(String(b.alias || ""), "nl"));
    ts.innerHTML = `<option value="">Alle trackers</option>` + list.map((t) => `<option value="${esc(t.id)}">${esc(t.alias || "#" + t.id)}</option>`).join("");
    if (F.tracker && !list.some((t) => String(t.id) === String(F.tracker))) ts.insertAdjacentHTML("beforeend", `<option value="${esc(F.tracker)}">${esc(trackerName(F.tracker))}</option>`);
    ts.value = F.tracker;
    cs.innerHTML = `<option value="">Alle kanalen</option>` + CHANNELS.map((c) => `<option value="${esc(c.id)}">${esc(c.name)}</option>`).join("");
    cs.value = F.channel;
  }
  function syncControls() {
    document.querySelectorAll("#f-period [data-p]").forEach((b) => {
      const on = b.dataset.p === F.period;
      b.classList.toggle("on", on);
      b.setAttribute("aria-pressed", String(on));
    });
    $("f-customwrap").hidden = F.period !== "custom";
    if (F.period === "custom") { $("f-from").value = toLocalInput(F.from); $("f-to").value = toLocalInput(F.to); }
    $("f-auto").checked = !!F.auto;
    fillSelects();
    const chips = [];
    if (F.tracker) chips.push(["tracker", `Tracker: ${trackerName(F.tracker)}`]);
    if (F.channel) chips.push(["channel", `Kanaal: ${channelName(F.channel)}`]);
    // samenvatting op de knop “Meer filters” (gsm)
    $("f-sum").textContent = [F.tracker ? trackerName(F.tracker) : "alle trackers", F.channel ? channelName(F.channel) : "alle kanalen"]
      .concat(F.auto ? ["elke minuut"] : []).join(" · ");
    const box = $("f-active");
    box.hidden = !chips.length;
    box.innerHTML = chips.map(([k, l]) => `<span class="chip on">${esc(l)}<button type="button" data-clear="${k}" aria-label="Filter ${esc(l)} wissen">✕</button></span>`).join("");
    box.querySelectorAll("[data-clear]").forEach((b) => b.addEventListener("click", () => { F[b.dataset.clear] = ""; changed(); }));
  }
  function changed() { saveFilters(); syncControls(); load({ anim: true }); setAuto(); }

  // ==== kerncijfers ====================================================================================
  /* volgorde = belangrijkheid: komt alles binnen, hoe is de ontvangst, hoeveel werd ingehaald.
     Op een gsm staan alleen de eerste vier (main) open, de rest achter “Meer kerncijfers”. */
  const KPI = [
    { id: "active", label: "Actieve trackers", main: true },
    { id: "msgs", label: "Berichten ontvangen", main: true },
    { id: "snr", label: "Gemiddelde SNR", main: true },
    { id: "fifo", label: "Ingehaald via FIFO", main: true },
    { id: "points", label: "Locatiepunten ontvangen", wide: true },
    { id: "delay", label: "Afleververtraging (mediaan)" },
    { id: "dups", label: "Duplicaten weggefilterd" },
    { id: "acks", label: "Bevestigingen verstuurd" },
  ];
  function kpiSkeleton() {
    $("kpis").innerHTML = KPI.map((k) => `<div class="st-kpi loading${k.wide ? " wide" : ""}${k.main ? "" : " sec"}" id="k-${k.id}">
      <div class="k-l">${esc(k.label)}</div><div class="k-v" aria-live="off">0</div><div class="k-x"></div><div class="k-s">…</div></div>`).join("");
  }
  /* oordeel over de ontvangst in gewone taal; altijd met een woord, nooit alleen een kleur */
  function snrVerdict(v) {
    if (v == null || !isFinite(v)) return null;
    if (v >= 5) return { w: "uitstekend", c: "ok" };
    if (v >= -5) return { w: "goed", c: "ok" };
    if (v >= -10) return { w: "matig", c: "warn" };
    return { w: "zwak", c: "bad" };
  }
  const tag = (text, cls) => `<span class="k-tag ${cls}">${esc(text)}</span>`;
  function periodPhrase() {
    if (F.period === "24h") return "De afgelopen 24 uur";
    if (F.period === "7d") return "De afgelopen 7 dagen";
    if (F.period === "30d") return "De afgelopen 30 dagen";
    return "In deze periode";
  }
  /* één zin bovenaan: het antwoord op de drie vragen die je hier komt stellen */
  function renderSummary(sum) {
    const box = $("st-summary");
    if (!sum) { box.hidden = true; return; }
    const T = sum.totals || {}, kinds = T.points_by_kind || {};
    const known = (sum.trackers || []).length, act = T.trackers_active || 0;
    box.hidden = false;
    if (!T.messages && !T.points) { box.innerHTML = `${esc(periodPhrase())} kwam er <strong>niets</strong> binnen${F.tracker || F.channel ? " voor deze filter" : ""}.`; return; }
    const who = F.tracker
      ? `stuurde <strong>${esc(trackerName(F.tracker))}</strong> ${num(T.messages)} berichten`
      : `hoorden we <strong>${num(act)}${known > act ? ` van de ${num(known)}` : ""} tracker${act === 1 && known <= act ? "" : "s"}</strong> (${num(T.messages)} berichten)`;
    const v = snrVerdict(T.avg_snr);
    const rx = v ? `, de ontvangst was <strong>${v.w}</strong> (gemiddeld ${num(T.avg_snr, 1)}&nbsp;dB)` : "";
    const ptot = KINDS.reduce((a, k) => a + (kinds[k.key] || 0), 0);
    const fifo = ptot ? ` en <strong>${pct(kinds.fifo || 0, ptot)}</strong> van de punten werd achteraf ingehaald` : "";
    box.innerHTML = `${esc(periodPhrase())} ${who}${rx}${fifo}.`;
  }
  const kpiPrev = {};
  function countUp(el, id, to, fmt, anim) {
    const from = kpiPrev[id] ?? 0;
    kpiPrev[id] = to;
    if (to == null || !isFinite(to)) { el.innerHTML = fmt(null); return; }
    if (!anim || reduced() || from === to) { el.innerHTML = fmt(to); return; }
    const t0 = performance.now(), T = 900;
    const step = (now) => {
      const p = Math.min(1, (now - t0) / T), e = 1 - (1 - p) ** 3;
      el.innerHTML = fmt(from + (to - from) * e, p < 1);
      if (p < 1) requestAnimationFrame(step);
    };
    requestAnimationFrame(step);
  }
  function setKpi(id, value, fmt, sub, extra, anim) {
    const box = $("k-" + id);
    box.classList.remove("loading");
    countUp(box.querySelector(".k-v"), id, value, fmt, anim);
    box.querySelector(".k-s").innerHTML = sub || "";
    box.querySelector(".k-x").innerHTML = extra || "";
    box.setAttribute("aria-label", `${KPI.find((k) => k.id === id).label}: ${box.querySelector(".k-v").textContent}`);
  }
  function delayPercentiles(T, dist) {
    if (T.delivery_delay_p50 != null) return [T.delivery_delay_p50, T.delivery_delay_p90];
    const bins = (dist && dist.delay) || [];
    const tot = bins.reduce((a, b) => a + b.count, 0);
    if (!tot) return [null, null];
    const q = (p) => {
      let acc = 0;
      for (const b of bins) {
        if (acc + b.count >= p * tot) { const f = (p * tot - acc) / (b.count || 1); return b.lo + f * ((b.hi ?? b.lo * 2) - b.lo); }
        acc += b.count;
      }
      return bins[bins.length - 1].hi;
    };
    return [q(0.5), q(0.9)];
  }
  function renderKpis(sum, dist, anim) {
    renderSummary(sum);
    if (!sum) { document.querySelectorAll(".st-kpi").forEach((k) => { k.classList.remove("loading"); k.querySelector(".k-v").textContent = "–"; k.querySelector(".k-s").textContent = "niet beschikbaar"; }); return; }
    const T = sum.totals || {}, R = sum.range || range();
    const hours = Math.max(1, ((R.to || 0) - (R.from || 0)) / H);
    const kinds = T.points_by_kind || {};
    const st = T.messages_by_state || {};
    const qMsg = st.Q ?? (sum.trackers || []).reduce((a, t) => a + (t.q_messages || 0), 0);
    const qPts = kinds.fifo ?? (sum.trackers || []).reduce((a, t) => a + (t.q_points || 0), 0);
    const intF = (v) => num(v);
    const unitF = (u, d) => (v) => (v == null ? "–" : `${num(v, d)}<small>${u}</small>`);
    setKpi("msgs", T.messages, intF, `≈ ${num(T.messages / hours, T.messages / hours < 10 ? 1 : 0)} per uur`, "", anim);
    const ptot = KINDS.reduce((a, k) => a + (kinds[k.key] || 0), 0) || 1;
    const bar = `<div class="k-bar" aria-hidden="true">${KINDS.map((k) => `<span style="flex-grow:${kinds[k.key] || 0};background:${k.color}"></span>`).join("")}</div>`;
    const leg = `<div class="k-leg">${KINDS.map((k) => `<span><i style="background:${k.color}"></i>${k.label} ${pct(kinds[k.key] || 0, ptot)}</span>`).join("")}</div>`;
    setKpi("points", T.points, intF, leg, bar, anim);
    setKpi("fifo", qPts, unitF(" punten", 0), `${kinds.fifo != null && T.points ? `${pct(qPts, ptot)} van alle punten, ` : ""}in ${num(qMsg)} inhaalberichten`, "", anim);
    const v = snrVerdict(T.avg_snr);
    setKpi("snr", T.avg_snr, unitF(" dB", 1), T.avg_hops == null ? "" : `gemiddeld ${num(T.avg_hops, 1)} hops`, v ? `<div>${tag(v.w, v.c)}</div>` : "", anim);
    const known = (sum.trackers || []).length, act = T.trackers_active || 0;
    const quiet = known > act ? known - act : 0;
    setKpi("active", T.trackers_active, intF, known ? `van de ${num(known)} tracker${known === 1 ? "" : "s"}` : "stuurden iets in deze periode",
      known ? `<div>${quiet ? tag(`${num(quiet)} stil`, "warn") : tag("alle actief", "ok")}</div>` : "", anim);
    setKpi("dups", (T.dup_points || 0) + (T.dup_msg || 0), intF, `${num(T.dup_msg || 0)} berichten · ${num(T.dup_points || 0)} punten`, "", anim);
    setKpi("acks", (T.t1a_sent || 0) + (T.t1f_sent || 0), intF, `T1A ${num(T.t1a_sent || 0)} · T1F ${num(T.t1f_sent || 0)}`, "", anim);
    const [p50, p90] = delayPercentiles(T, dist);
    setKpi("delay", p50, (v) => esc(dur(v)), [p90 == null ? "" : `90 % binnen ${esc(dur(p90))}`,
      T.recovered_late ? `${num(T.recovered_late)} punten meer dan 5 min onderweg` : ""].filter(Boolean).join(" · "), "", anim);
  }

  // ==== gegevens laden ====================================================================================
  let DATA = null, seq = 0, dirty = new Set(), activeTab = "verkeer", firstAnim = new Set();
  const PANES = ["verkeer", "ontvangst", "fifo", "trackers", "repeaters", "kwaliteit"];
  function normSeries(j) {
    // tijdreeks: {series:[{t,...}]} of rechtstreeks een lijst
    if (!j) return [];
    const s = Array.isArray(j) ? j : j.series || j.data || [];
    if (Array.isArray(s)) return s.slice().sort((a, b) => a.t - b.t);
    // {series:{kind:[{t,count}]}} -> samenvoegen
    const map = new Map();
    Object.entries(s).forEach(([k, arr]) => (arr || []).forEach((p) => {
      const r = map.get(p.t) || { t: p.t }; r[k] = p.count ?? p.value ?? p.n ?? 0; map.set(p.t, r);
    }));
    return [...map.values()].sort((a, b) => a.t - b.t);
  }
  async function load({ anim = false, quiet = false } = {}) {
    const my = ++seq;
    const r = range(), bucket = pickBucket(r);
    if (!quiet) {
      kpiSkeleton();
      document.querySelectorAll(".st-chart .st-body").forEach((el) => skel(el, el.closest("#c-states") ? 170 : 200));
      skel($("trk-table"), 240);
    }
    $("f-refresh").classList.add("spin");
    const get = (path, extra) => MT.api(`/api/stats/${path}?${query(r, extra)}`);
    const span = r.to - r.from;
    const jobs = {
      summary: get("summary"),
      ts: get("timeseries", { bucket }),
      dist: get("distributions"),
      fifo: get("fifo"),
      events: get("events", { bucket }),
      prio: get("events", { bucket, kind: "prio_start" }),   // voertuigtrackers die prioritair beginnen te rijden
      repeaters: get("repeaters"),
    };
    jobs.hourly = bucket === "hour" ? jobs.ts : span <= 35 * D ? get("timeseries", { bucket: "hour" }) : Promise.resolve(null);
    const keys = Object.keys(jobs);
    const res = await Promise.allSettled(keys.map((k) => jobs[k]));
    if (my !== seq) return;
    $("f-refresh").classList.remove("spin");
    const d = { range: r, bucket, err: {} };
    res.forEach((x, i) => { if (x.status === "fulfilled") d[keys[i]] = x.value; else d.err[keys[i]] = x.reason && x.reason.message || "fout"; });
    if (d.summary && d.summary.range) d.range = { from: d.summary.range.from ?? r.from, to: d.summary.range.to ?? r.to };
    d.series = normSeries(d.ts);
    d.hourSeries = d.hourly ? normSeries(d.hourly) : null;
    d.events = d.events ? normSeries(d.events) : null;
    // prio_start per periode; de waarde kan als prio_start of als count komen
    d.prio = d.prio ? normSeries(d.prio).map((x) => ({ t: x.t, prio_start: +(x.prio_start ?? x.count ?? x.n ?? x.value ?? 0) })) : null;
    const T = (d.summary && d.summary.totals) || {};
    d.isEmpty = !!d.summary && !T.messages && !T.points;
    DATA = d;
    // kaartjes van trackers aanvullen met wat de samenvatting kent
    (d.summary && d.summary.trackers || []).forEach((t) => { if (!TRACKERS.some((x) => String(x.id) === String(t.id))) TRACKERS.push({ id: t.id, alias: t.alias }); });
    const allFail = keys.every((k) => d.err[k]);
    $("st-err").hidden = !allFail;
    // niets geladen: geen acht lege kaartjes tonen, de foutmelding bovenaan zegt genoeg
    $("kpis").hidden = allFail; $("kpi-more").hidden = allFail;
    if (allFail) $("st-err").textContent = `Kon de statistieken niet laden: ${d.err.summary}.`;
    renderKpis(d.summary, d.dist, !quiet);
    $("st-updated").textContent = `${stamp(d.range.from)} – ${stamp(d.range.to)} · bijgewerkt om ${hm(Date.now() / 1000)}`;
    PANES.forEach((p) => dirty.add(p));
    if (anim) PANES.forEach((p) => firstAnim.add(p));
    sparkCache.clear();
    renderPane(activeTab);
  }
  function renderPane(name) {
    if (!DATA || !dirty.has(name)) return;
    dirty.delete(name);
    const anim = firstAnim.delete(name) && !reduced();
    ({ verkeer: paneTraffic, ontvangst: paneReception, fifo: paneFifo, trackers: paneTrackers, repeaters: paneRepeaters, kwaliteit: paneQuality })[name](DATA, anim);
  }
  /* één grafiek tekenen met de juiste toestand (fout / leeg / inhoud) */
  function draw(id, errKey, isEmpty, fn) {
    const el = document.querySelector(`#${id} .st-body`) || $(id);
    if (DATA.err[errKey]) return failed(el, DATA.err[errKey]);
    if (DATA.isEmpty || isEmpty) return empty(el, typeof isEmpty === "string" ? isEmpty : null);
    try { fn(el); } catch (e) { console.error(e); failed(el, "tekenen mislukt"); }
  }
  const sumKey = (rows, k) => rows.reduce((a, r) => a + (+r[k] || 0), 0);

  // ---- Verkeer ----
  function paneTraffic(d, anim) {
    const sr = d.series;
    draw("c-points", "ts", !sr.length || !KINDS.some((k) => sumKey(sr, k.key)), (el) => timeChart(el, {
      title: "Locatiepunten in de tijd", desc: `Gestapelde vlakken met het aantal punten per ${d.bucket === "day" ? "dag" : "uur"}, opgesplitst in live, extra, SlowTrack en FIFO.`,
      data: sr, bucket: d.bucket, type: sr.length > 60 ? "area" : "bars", series: KINDS,
    }, anim));
    // Prioritaire ritten: alleen tonen als er zijn, of als er een voertuigtracker (RAK3401 + 1 W) bestaat
    const prio = d.prio || [], prioTot = sumKey(prio, "prio_start");
    const hasRak = TRACKERS.some((t) => t.board === "rak3401_1w");
    $("c-prio").hidden = !(prioTot > 0 || hasRak);
    if (!$("c-prio").hidden) draw("c-prio", "prio", !prioTot, (el) => timeChart(el, {
      title: "Prioritaire ritten (gestart)", desc: `Staven met het aantal keer per ${d.bucket === "day" ? "dag" : "uur"} dat een voertuigtracker prioritair begon te rijden.`,
      data: prio, bucket: d.bucket, type: "bars", series: [{ key: "prio_start", label: "rijdt prioritair", color: "var(--st-c8)" }], height: 160,
    }, anim));
    draw("c-messages", "ts", !sumKey(sr, "messages"), (el) => timeChart(el, {
      title: "Berichten in de tijd", desc: `Lijn met het aantal ontvangen berichten per ${d.bucket === "day" ? "dag" : "uur"}.`,
      data: sr, bucket: d.bucket, type: "area", series: [{ key: "messages", label: "berichten", color: "var(--st-c1)" }], height: 180,
    }, anim));
    const states = (d.dist && d.dist.states && Object.keys(d.dist.states).length ? d.dist.states : d.summary && d.summary.totals && d.summary.totals.messages_by_state) || {};
    const ents = Object.entries(states).filter(([, v]) => v > 0).sort((a, b) => b[1] - a[1]);
    const items = [], other = { label: "overige", value: 0, color: "var(--st-other)" };
    ents.forEach(([k, v]) => { if (STATE_COLOR[k]) items.push({ label: stateName(k), value: v, color: `var(--st-c${STATE_COLOR[k]})` }); else other.value += v; });
    if (other.value) items.push(other);
    draw("c-states", d.dist ? "dist" : "summary", !items.length, (el) => donut(el, {
      title: "Soort berichten", desc: "Ringdiagram met het aandeel van elke berichttoestand.", items, centerLabel: "berichten", catLabel: "Toestand",
    }, anim));
    const hod = (d.dist && d.dist.hour_of_day) || [];
    draw("c-hours", "dist", !hod.some((v) => v), (el) => barChart(el, {
      title: "Actief per uur van de dag", desc: "Staafdiagram met het aantal berichten per uur van de dag (0 tot 23 uur).",
      items: hod.map((v, h) => ({ label: `${h}:00–${h + 1}:00`, axis: `${h}u`, value: v || 0 })), color: "var(--st-seq)",
      unit: "berichten", catLabel: "Uur", every: el.clientWidth < 500 ? 6 : 3, peakLabel: true,
    }, anim));
    const hs = d.hourSeries;
    const heatEmpty = !hs ? "Kies een periode van hoogstens 35 dagen om dit te zien." : !sumKey(hs, "messages");
    draw("c-heat", "hourly", heatEmpty, (el) => {
      const m = Array.from({ length: 7 }, () => Array(24).fill(0));
      hs.forEach((r) => { const x = dt(r.t); m[(x.getDay() + 6) % 7][x.getHours()] += +r.messages || 0; });
      heatmap(el, { title: "Berichten per weekdag en uur", desc: "Warmtekaart: rijen zijn weekdagen, kolommen uren; donkerder is meer berichten.", matrix: m }, anim);
    });
  }

  // ---- Ontvangst ----
  function paneReception(d, anim) {
    const sr = d.series;
    draw("c-snr", "ts", !sr.some((r) => r.avg_snr != null), (el) => timeChart(el, {
      title: "SNR in de tijd", desc: "Lijn met de gemiddelde SNR per periode en een band van minimum tot maximum.",
      data: sr, bucket: d.bucket, type: "line", series: [{ key: "avg_snr", label: "gemiddelde SNR", color: "var(--st-c1)" }],
      band: { lo: "min_snr", hi: "max_snr" }, yFmt: (v) => `${num(v)} dB`, tipFmt: (v) => `${num(v, 1)} dB`, ref: { v: -10, label: "grens ≈ −10 dB" },
    }, anim));
    const snr = (d.dist && d.dist.snr) || [];
    draw("c-snrhist", "dist", !snr.some((b) => b.count), (el) => barChart(el, {
      title: "Verdeling van de SNR", desc: "Histogram van het aantal berichten per SNR-strook in dB.",
      items: snr.map((b) => ({ label: `${num(b.lo)} tot ${num(b.hi)} dB`, axis: num(b.lo), value: b.count, color: b.hi <= -10 ? "var(--st-c2)" : "var(--st-c1)" })),
      color: "var(--st-c1)", unit: "berichten", catLabel: "SNR",
      legend: [{ label: "boven −10 dB", color: "var(--st-c1)" }, { label: "−10 dB of lager: onbetrouwbaar", color: "var(--st-c2)" }],
    }, anim));
    const hops = (d.dist && d.dist.hops) || [];
    draw("c-hops", "dist", !hops.some((b) => b.count), (el) => barChart(el, {
      title: "Aantal hops", desc: "Staafdiagram met het aantal berichten per aantal hops.",
      items: hops.map((b) => ({ label: b.hops === 0 ? "rechtstreeks (0 hops)" : `${b.hops} hop${b.hops === 1 ? "" : "s"}`, axis: String(b.hops), value: b.count })),
      color: "var(--st-c3)", unit: "berichten", catLabel: "Hops", peakLabel: true, every: 1,
    }, anim));
  }

  // ---- Inhalen ----
  function fifoIllustration() {
    const box = $("fifo-illus");
    if (box.firstChild) return;
    const live1 = [40, 68, 96, 124], live2 = [258, 286, 314], late = [150, 176, 202, 228];
    box.innerHTML = `<svg viewBox="0 0 360 150" aria-hidden="true">
      <circle class="pl" cx="26" cy="14" r="5" style="animation:none;opacity:1"/><text class="lab" x="36" y="18">live</text>
      <circle class="pf" cx="82" cy="14" r="5" style="animation:none;opacity:1"/><text class="lab" x="92" y="18">ingehaald (later binnen)</text>
      <path class="mesh" d="M150 62 q40 -22 80 0"/>
      <text class="lab" x="190" y="48" text-anchor="middle">later via de mesh</text>
      <rect class="gap" x="138" y="96" width="104" height="28" rx="6"/>
      <text class="lab" x="190" y="142" text-anchor="middle">geen verbinding</text>
      <line class="tl" x1="20" y1="110" x2="340" y2="110"/>
      ${[...live1, ...late, ...live2].map((x) => `<line class="tick" x1="${x}" x2="${x}" y1="104" y2="116"/>`).join("")}
      ${live1.map((x, i) => `<circle class="pl" cx="${x}" cy="110" r="7" style="animation-delay:${(i * 0.35).toFixed(2)}s"/>`).join("")}
      ${live2.map((x, i) => `<circle class="pl" cx="${x}" cy="110" r="7" style="animation-delay:${(3.2 + i * 0.35).toFixed(2)}s"/>`).join("")}
      ${late.map((x, i) => `<circle class="pf" cx="${x}" cy="110" r="7" style="animation-delay:${(i * 0.22).toFixed(2)}s"/>`).join("")}
      <text class="lab" x="82" y="90" text-anchor="middle">live ontvangen</text>
      <text class="lab" x="340" y="138" text-anchor="end">tijd →</text>
    </svg>`;
  }
  function paneFifo(d, anim) {
    fifoIllustration();
    const ft = ((d.fifo && d.fifo.trackers) || []).filter((t) => t.q_messages || t.q_points || (t.gaps && t.gaps.length));
    const name = (t) => t.alias || `#${t.id}`;
    const rows = ft.slice().sort((a, b) => (b.q_points || 0) - (a.q_points || 0)).slice(0, 15).map((t) => ({ ...t, label: name(t) }));
    draw("c-fifo", "fifo", !rows.length, (el) => hbars(el, {
      title: "Ingehaald per tracker", desc: "Horizontale staven met per tracker het aantal ingehaalde berichten en punten.",
      rows, series: [{ key: "q_messages", label: "inhaalberichten", color: "var(--st-c7)" }, { key: "q_points", label: "ingehaalde punten", color: "var(--st-c4)" }],
      note: (r) => (r.avg_points_per_q ? `${num(r.avg_points_per_q, 1)} pt/ber.` : ""),
      tipExtra: (r) => `<div class="muted">${num(r.avg_points_per_q, 1)} punten per bericht · ${num(r.t1f_sent)} T1F · langste gat ${esc(dur(r.max_gap_filled_s))}</div>`,
      table: table([{ label: "Tracker" }, { label: "inhaalberichten", num: true }, { label: "ingehaalde punten", num: true }, { label: "punten per bericht", num: true }, { label: "T1F verstuurd", num: true }, { label: "langste gat", num: true }],
        rows.map((r) => [r.label, num(r.q_messages), num(r.q_points), num(r.avg_points_per_q, 1), num(r.t1f_sent), dur(r.max_gap_filled_s)]), "Ingehaald per tracker"),
    }, anim));
    const R = d.range;
    const grows = ft.filter((t) => t.gaps && t.gaps.length).map((t) => ({ label: name(t), gaps: t.gaps }))
      .sort((a, b) => Math.max(...b.gaps.map((g) => g.to - g.from)) - Math.max(...a.gaps.map((g) => g.to - g.from))).slice(0, 12);
    const all = grows.flatMap((r) => r.gaps.map((g) => ({ r, g }))).sort((a, b) => (b.g.to - b.g.from) - (a.g.to - a.g.from)).slice(0, 10);
    draw("c-gaps", "fifo", !grows.length, (el) => gapChart(el, {
      title: "Opgevulde gaten", desc: "Tijdlijn per tracker met de periodes die achteraf via FIFO werden opgevuld.", rows: grows, from: R.from, to: R.to,
      table: `<h4 class="small" style="margin:12px 0 4px">Grootste opgevulde gaten</h4><div class="st-tblwrap"><table><thead><tr><th>Tracker</th><th>Van</th><th>Tot</th><th class="num">Duur</th><th class="num">Punten</th></tr></thead><tbody>`
        + all.map(({ r, g }) => `<tr><td>${esc(r.label)}</td><td>${esc(stamp(g.from))}</td><td>${esc(stamp(g.to))}</td><td class="num">${esc(dur(g.to - g.from))}</td><td class="num">${num(g.filled_points)}</td></tr>`).join("")
        + `</tbody></table></div>`,
    }, anim));
    const dl = (d.dist && d.dist.delay) || [];
    draw("c-delay", "dist", !dl.some((b) => b.count), (el) => barChart(el, {
      title: "Afleververtraging", desc: "Histogram van de tijd tussen meting en aankomst op de server.",
      items: dl.map((b) => ({ label: b.hi == null ? `meer dan ${dur(b.lo)}` : `${dur(b.lo)} tot ${dur(b.hi)}`, axis: dur(b.lo).replace(" min", "m").replace(" s", "s").replace(" u", "u").replace(" d", "d"), value: b.count, color: b.lo >= 300 ? "var(--st-c4)" : "var(--st-c1)" })),
      color: "var(--st-c1)", unit: "punten", catLabel: "Vertraging",
      legend: [{ label: "minder dan 5 min (live)", color: "var(--st-c1)" }, { label: "5 min of meer (ingehaald)", color: "var(--st-c4)" }],
    }, anim));
  }

  // ---- Trackers ----
  let sortKey = "messages", sortDir = -1;
  const sparkCache = new Map();
  async function sparkData(id) {
    const r = DATA.range, key = id + ":" + r.from + ":" + r.to;
    if (sparkCache.has(key)) return sparkCache.get(key);
    const span = r.to - r.from;
    const bucket = span <= 3 * D ? "hour" : "day";
    const q = new URLSearchParams({ from: r.from, to: r.to, tracker: id, bucket });
    const p = MT.api(`/api/stats/timeseries?${q}`).then(normSeries).catch(() => null);
    sparkCache.set(key, p);
    return p;
  }
  function paneTrackers(d) {
    const el = $("trk-table");
    if (d.err.summary) return failed(el, d.err.summary);
    const list = (d.summary.trackers || []).slice();
    $("trk-count").textContent = list.length ? `${list.length} tracker${list.length === 1 ? "" : "s"}` : "";
    if (!list.length) return empty(el);
    const cols = [
      { k: "alias", l: "Tracker" }, { k: "messages", l: "Berichten", num: 1 }, { k: "points", l: "Punten", num: 1, opt: 1 },
      { k: null, l: "Verloop" }, { k: "avg_snr", l: "SNR", num: 1 }, { k: "avg_hops", l: "Hops", num: 1, opt: 1 },
      { k: "last_rx", l: "Laatst ontvangen" }, { k: "bat_last", l: "Batterij" }, { k: "fw_mode", l: "Modus", opt: 1 },
      { k: "delivery_delay_p50", l: "Vertraging", num: 1, opt: 1 },
    ];
    list.sort((a, b) => {
      const x = a[sortKey], y = b[sortKey];
      if (typeof x === "string" || typeof y === "string") return sortDir * String(x ?? "").localeCompare(String(y ?? ""), "nl");
      return sortDir * ((x ?? -Infinity) - (y ?? -Infinity));
    });
    const bat = (v) => {
      if (v == null) return `<span class="muted">–</span>`;
      const p = v > 100 ? Math.max(0, Math.min(100, ((v / (v > 1000 ? 1000 : 1)) - 3.3) / 0.9 * 100)) : v;
      const txt = v > 100 ? `${num(v > 1000 ? v / 1000 : v, 2)} V` : `${num(v)} %`;
      return `<span class="st-bat ${p < 20 ? "low" : p < 40 ? "mid" : ""}" title="batterij ${txt}"><i><b style="width:${Math.round(p)}%"></b></i>${txt}</span>`;
    };
    const ch = (t) => (t.channel == null ? "" : typeof t.channel === "number" ? channelName(t.channel) : t.channel);
    el.innerHTML = `<div class="st-scroll"><table class="st-ttable"><caption class="sr-only" style="position:absolute;left:-9999px">Statistieken per tracker</caption><thead><tr>${cols.map((c) =>
      `<th scope="col" class="${c.num ? "num " : ""}${c.opt ? "col-opt" : ""}${c.k ? "" : " col-nosort"}"${c.k === sortKey ? ` aria-sort="${sortDir > 0 ? "ascending" : "descending"}"` : ""}>${c.k
        ? `<button type="button" data-sort="${c.k}">${esc(c.l)}<span class="ar" aria-hidden="true">${c.k === sortKey ? (sortDir > 0 ? "▲" : "▼") : ""}</span></button>` : `<span style="display:block;padding:8px 6px;font-size:12px;color:var(--muted);font-weight:600;text-transform:uppercase;letter-spacing:.04em">${esc(c.l)}</span>`}</th>`).join("")}</tr></thead><tbody>${list.map((t) => `
      <tr tabindex="0" data-id="${esc(t.id)}"${String(t.id) === String(F.tracker) ? ' class="sel"' : ""} title="${String(t.id) === String(F.tracker) ? "Filter op deze tracker wissen" : "Toon alleen deze tracker"}">
        <td><div class="nm">${esc(t.alias || "#" + t.id)}</div><div class="sub">${esc(ch(t))}</div>${t.first_hop ? `<div class="sub" title="meest gebruikte eerste hop: ${esc(t.first_hop.name || t.first_hop.hash)} (${num(t.first_hop.count)} kopieën)">eerste hop: ${esc(t.first_hop.name || t.first_hop.hash)}</div>` : ""}</td>
        <td class="num" data-l="Berichten">${num(t.messages)}${t.q_messages ? `<div class="sub">${num(t.q_messages)} ingehaald</div>` : ""}</td>
        <td class="num col-opt">${num(t.points)}${t.q_points ? `<div class="sub">${num(t.q_points)} FIFO</div>` : ""}</td>
        <td class="c-spk"><div class="spk" data-spk="${esc(t.id)}"><span class="muted small">…</span></div></td>
        <td class="num" data-l="SNR">${t.avg_snr == null ? "–" : `${num(t.avg_snr, 1)} dB`}<div class="sub">${t.min_snr == null ? "" : `${num(t.min_snr)} tot ${num(t.max_snr)}`}</div></td>
        <td class="num col-opt">${num(t.avg_hops, 1)}</td>
        <td class="nowrap" data-l="Laatst ontvangen"><span title="${t.last_rx ? esc(stamp(t.last_rx)) : ""}">${esc(MT.ago(t.last_rx))}</span></td>
        <td class="nowrap" data-l="Batterij">${bat(t.bat_last)}</td>
        <td class="col-opt">${esc(MODE[t.fw_mode] || t.fw_mode || "–")}</td>
        <td class="num col-opt nowrap">${esc(dur(t.delivery_delay_p50))}<div class="sub">p90 ${esc(dur(t.delivery_delay_p90))}</div></td>
      </tr>`).join("")}</tbody></table></div>`;
    el.querySelectorAll("[data-sort]").forEach((b) => b.addEventListener("click", () => {
      const k = b.dataset.sort;
      if (sortKey === k) sortDir = -sortDir; else { sortKey = k; sortDir = k === "alias" || k === "fw_mode" ? 1 : -1; }
      paneTrackers(DATA);
      el.querySelector(`[data-sort="${k}"]`).focus();
    }));
    el.querySelectorAll("tbody tr").forEach((tr) => {
      const go = () => { F.tracker = String(tr.dataset.id) === String(F.tracker) ? "" : tr.dataset.id; changed(); window.scrollTo({ top: 0, behavior: reduced() ? "auto" : "smooth" }); };
      tr.addEventListener("click", go);
      tr.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); go(); } });
    });
    // sparklines: per tracker een kleine tijdreeks, hoogstens 4 tegelijk
    el.querySelectorAll("[data-spk]").forEach((b) => { const t = list.find((x) => String(x.id) === b.dataset.spk); if (t && !t.messages) b.innerHTML = `<span class="muted small">geen verkeer</span>`; });
    const ids = list.filter((t) => t.messages).slice(0, 40).map((t) => t.id);
    let k = 0;
    const worker = async () => {
      while (k < ids.length) {
        const id = ids[k++];
        const s = await sparkData(id);
        const box = el.querySelector(`[data-spk="${CSS.escape(String(id))}"]`);
        if (!box) continue;
        if (!s || !s.length) { box.innerHTML = `<span class="muted small">–</span>`; continue; }
        box.innerHTML = spark(s.map((r) => +r.messages || 0), "msg", `Berichten: van ${num(Math.min(...s.map((r) => +r.messages || 0)))} tot ${num(Math.max(...s.map((r) => +r.messages || 0)))} per stap`)
          + spark(s.map((r) => (r.avg_snr == null ? null : +r.avg_snr)), "snr", "Verloop van de gemiddelde SNR");
      }
    };
    for (let w = 0; w < 4; w++) worker();
  }

  // ---- Repeaters ----
  function repLabel(r) {
    if (!r) return "";
    const h = String(r.hash || "?");
    if ((r.candidates || 1) > 1) return `${h} (${r.candidates} kandidaten)`;
    return r.name ? `${r.name} (${h})` : h;
  }
  function repIllustration() {
    const box = $("rep-illus");
    if (box.firstChild) return;
    const xs = [30, 110, 190, 270, 340], lab = ["tracker", "eerste hop", "tussenin", "laatste hop", "antenne"];
    box.innerHTML = `<svg viewBox="0 0 370 110" aria-hidden="true">
      <line class="tl" x1="30" y1="50" x2="340" y2="50"/>
      ${xs.map((x, i) => (i === 0 || i === 4
        ? `<rect class="${i ? "srvn" : "trkn"}" x="${x - 12}" y="38" width="24" height="24" rx="${i ? 5 : 12}"/><text class="icn" x="${x}" y="55" text-anchor="middle">${i ? "S" : "T"}</text>`
        : `<circle class="${i === 1 ? "h1" : i === 3 ? "h3" : "h2"}" cx="${x}" cy="50" r="11"/>`)
        + `<text class="lab" x="${x}" y="84" text-anchor="middle">${lab[i]}</text>`).join("")}
      <text class="lab" x="70" y="28" text-anchor="middle">hoort de tracker</text>
      <text class="lab" x="305" y="28" text-anchor="middle">bereikt ons</text>
      ${reduced() ? "" : `<circle class="trav" cx="30" cy="50" r="5" style="--len:310px;animation-duration:3.2s"/>`}
    </svg>`;
  }
  function paneRepeaters(d, anim) {
    repIllustration();
    const R = d.repeaters || {};
    const reps = (R.repeaters || []).slice().sort((a, b) => (b.count || 0) - (a.count || 0)).slice(0, 15).map((r) => {
      const c = r.count || 0, f = Math.min(c, r.as_first_hop || 0), l = Math.min(c, r.as_last_hop || 0);
      const only = Math.max(0, f + l - c);           // eerste én laatste: een pad met één hop
      return { ...r, label: repLabel(r), first: f - only, only, last: l - only, mid: Math.max(0, c - f - l + only) };
    });
    const segs = [
      { key: "first", label: "eerste hop", color: "var(--st-c1)" },
      { key: "only", label: "enige hop (eerste én laatste)", color: "var(--st-c7)" },
      { key: "last", label: "laatste hop", color: "var(--st-c3)" },
      { key: "mid", label: "tussenin", color: "var(--st-other)" },
    ];
    draw("c-rep", "repeaters", !reps.length, (el) => stackBars(el, {
      title: "Repeaters die het meeste doorgaven", desc: "Gerangschikte staven met per repeater het aantal doorgegeven berichten, opgesplitst naar plaats in het pad.",
      rows: reps, series: segs, note: (r) => (r.avg_snr == null ? "" : `${num(r.avg_snr, 1)} dB`),
      tipExtra: (r) => `<div class="r"><span>totaal</span><span class="v">${num(r.count)}</span></div><div class="muted">gem. SNR ${r.avg_snr == null ? "–" : num(r.avg_snr, 1) + " dB"} · ${num(r.trackers)} tracker${r.trackers === 1 ? "" : "s"}</div>`,
      table: table([{ label: "Repeater" }, { label: "berichten", num: true }, { label: "eerste hop", num: true }, { label: "laatste hop", num: true }, { label: "gem. SNR", num: true }, { label: "trackers", num: true }],
        reps.map((r) => [r.label, num(r.count), num(r.as_first_hop), num(r.as_last_hop), r.avg_snr == null ? "–" : `${num(r.avg_snr, 1)} dB`, num(r.trackers)]), "Repeaters die het meeste doorgaven"),
    }, anim));
    const byHash = new Map((R.repeaters || []).map((r) => [String(r.hash).toLowerCase(), r]));
    const paths = (R.top_paths || []).slice().sort((a, b) => b.count - a.count).slice(0, 8).map((p) => ({
      ...p,
      hops: String(p.path || "").split(",").map((h) => h.trim()).filter(Boolean).map((h) => {
        const r = byHash.get(h.toLowerCase());
        const short = r && r.name && (r.candidates || 1) <= 1 ? (r.name.length > 8 ? r.name.slice(0, 7) + "…" : r.name) : h;
        return { short, full: r ? repLabel(r) : h };
      }),
    }));
    draw("c-paths", "repeaters", !paths.length, (el) => chains(el, { title: "Meest gebruikte routes", paths }, anim));
    const hb = (R.hops_by_tracker || []).slice().sort((a, b) => (b.direct_pct || 0) - (a.direct_pct || 0)).slice(0, 20)
      .map((t) => ({ ...t, label: t.alias || `#${t.id}`, direct: +t.direct_pct || 0, via: Math.max(0, 100 - (+t.direct_pct || 0)) }));
    draw("c-hopstrk", "repeaters", !hb.length, (el) => stackBars(el, {
      title: "Hops per tracker", desc: "Staven van 100 procent per tracker: het deel rechtstreeks en het deel via repeaters.",
      rows: hb, max: 100, xFmt: (v) => `${num(v)} %`,
      series: [{ key: "direct", label: "rechtstreeks", color: "var(--st-c3)" }, { key: "via", label: "via repeaters", color: "var(--st-c1)" }],
      note: (r) => (r.avg_hops == null ? "" : `${num(r.avg_hops, 1)} hops`),
      table: table([{ label: "Tracker" }, { label: "rechtstreeks", num: true }, { label: "via repeaters", num: true }, { label: "gem. hops", num: true }],
        hb.map((r) => [r.label, `${num(r.direct)} %`, `${num(r.via)} %`, num(r.avg_hops, 1)]), "Hops per tracker"),
    }, anim));
  }

  // ---- Kwaliteit ----
  function paneQuality(d, anim) {
    const ev = d.events || [];
    const q = [
      { key: "dup_points", label: "dubbele punten", color: "var(--st-c1)" },
      { key: "dup_msg", label: "dubbele berichten", color: "var(--st-c2)" },
      { key: "invalid", label: "ongeldig", color: "var(--st-c3)" },
      { key: "unknown", label: "onbekend", color: "var(--st-c4)" },
      { key: "old_fw_dm", label: "oude firmware (DM)", color: "var(--st-c5)" },
    ];
    draw("c-quality", "events", !q.some((s) => sumKey(ev, s.key)), (el) => timeChart(el, {
      title: "Weggefilterd in de tijd", desc: "Gestapelde staven met duplicaten, ongeldige, onbekende en DM-berichten van oude firmware per periode.",
      data: ev, bucket: d.bucket, type: "bars", series: q, height: 200,
    }, anim));
    const a = [
      { key: "t1a_sent", label: "T1A (SOS)", color: "var(--st-c8)" },
      { key: "t1f_sent", label: "T1F (ingehaald)", color: "var(--st-c4)" },
    ];
    draw("c-acks", "events", !a.some((s) => sumKey(ev, s.key)), (el) => timeChart(el, {
      title: "Bevestigingen verstuurd", desc: "Gestapelde staven met het aantal T1A- en T1F-bevestigingen per periode.",
      data: ev, bucket: d.bucket, type: "bars", series: a, height: 200,
    }, anim));
  }

  // ==== automatisch vernieuwen ==============================================================================
  let autoTimer = null;
  function setAuto() {
    clearInterval(autoTimer);
    if (F.auto) autoTimer = setInterval(() => { if (!document.hidden) load({ quiet: true }); }, 60000);
  }
  document.addEventListener("visibilitychange", () => { if (!document.hidden && F.auto && DATA && Date.now() / 1000 - DATA.range.to > 60) load({ quiet: true }); });

  // ==== opstart ===================================================================================================
  async function init() {
    await MT.initHeader("/statistieken");
    if (MT.me.kind !== "user") {          // deellinks: de statistiek-API geeft 403
      document.querySelectorAll(".st-filters, .st-kpis, .st-kmore, .st-tabs, .st-pane").forEach((e) => { e.hidden = true; });
      $("st-err").hidden = false;
      $("st-err").textContent = "Statistieken zijn alleen beschikbaar als je ingelogd bent met een eigen account.";
      return;
    }
    readFilters();
    const [tr, ch] = await Promise.allSettled([MT.api("/api/trackers"), MT.api("/api/channels/mine")]);
    TRACKERS = tr.status === "fulfilled" ? tr.value : [];
    CHANNELS = ch.status === "fulfilled" ? ch.value : [];
    // een onthouden tracker of kanaal dat je niet (meer) mag zien, geeft een 404: filter laten vallen
    if (F.tracker && tr.status === "fulfilled" && !TRACKERS.some((t) => String(t.id) === String(F.tracker))) F.tracker = "";
    if (F.channel && ch.status === "fulfilled" && !CHANNELS.some((c) => String(c.id) === String(F.channel))) F.channel = "";
    syncControls();
    saveFilters();
    document.querySelectorAll("#f-period [data-p]").forEach((b) => b.addEventListener("click", () => {
      if (b.dataset.p === "custom") {
        const r = range();
        F.period = "custom"; F.from = F.from && F.period === "custom" ? F.from : r.from; F.to = r.to;
        syncControls(); $("f-from").focus();
        return;
      }
      F.period = b.dataset.p; changed();
    }));
    $("f-apply").addEventListener("click", () => {
      const a = fromLocalInput($("f-from").value), b = fromLocalInput($("f-to").value);
      if (!a || !b || b <= a) { $("f-to").setCustomValidity("‘Tot’ moet na ‘Van’ liggen."); $("f-to").reportValidity(); return; }
      $("f-to").setCustomValidity("");
      F.period = "custom"; F.from = a; F.to = b; changed();
    });
    $("f-tracker").addEventListener("change", (e) => { F.tracker = e.target.value; changed(); });
    $("f-channel").addEventListener("change", (e) => {
      F.channel = e.target.value;
      const t = TRACKERS.find((x) => String(x.id) === String(F.tracker));
      if (F.channel && t && t.channel_id != null && String(t.channel_id) !== F.channel) F.tracker = "";
      changed();
    });
    $("f-auto").addEventListener("change", (e) => { F.auto = e.target.checked; saveFilters(); syncControls(); setAuto(); if (F.auto) load({ quiet: true }); });
    $("f-refresh").addEventListener("click", () => load({ quiet: true }));
    // gsm: tracker/kanaal/vernieuwen open- en dichtklappen
    $("f-toggle").addEventListener("click", () => {
      const open = $("f-toggle").closest(".st-filters").classList.toggle("open");
      $("f-toggle").setAttribute("aria-expanded", String(open));
    });
    // gsm: de minder belangrijke kerncijfers tonen of verbergen
    $("kpi-more").addEventListener("click", () => {
      const all = $("kpis").classList.toggle("all");
      $("kpi-more").setAttribute("aria-expanded", String(all));
      $("kpi-more").textContent = all ? "Minder kerncijfers" : "Meer kerncijfers";
    });
    const tabBar = document.querySelector(".st-tabs");
    // schuifbare tabstrook (gsm): een vervaging toont dat er links/rechts nog tabbladen zijn
    const tabFade = () => {
      const max = tabBar.scrollWidth - tabBar.clientWidth;
      tabBar.classList.toggle("more-r", tabBar.scrollLeft < max - 2);
      tabBar.classList.toggle("more-l", tabBar.scrollLeft > 2);
    };
    tabBar.addEventListener("scroll", tabFade, { passive: true });
    window.addEventListener("resize", tabFade);
    MT.tabs(tabBar, (name) => {
      activeTab = name; hideTip(); renderPane(name);
      const b = tabBar.querySelector(".tab.on");
      if (b && tabBar.scrollWidth > tabBar.clientWidth) {
        const left = b.offsetLeft - (tabBar.clientWidth - b.offsetWidth) / 2;
        tabBar.scrollTo({ left, behavior: reduced() ? "auto" : "smooth" });
      }
      tabFade();
    });
    // bij een andere breedte opnieuw tekenen (zonder animatie)
    let lastW = document.querySelector("main").clientWidth, rt = null;
    new ResizeObserver(() => {
      const w = document.querySelector("main").clientWidth;
      if (Math.abs(w - lastW) < 8) return;
      lastW = w;
      clearTimeout(rt);
      rt = setTimeout(() => { if (!DATA) return; PANES.forEach((p) => { if (p !== "trackers") dirty.add(p); }); firstAnim.clear(); renderPane(activeTab); }, 150);
    }).observe(document.querySelector("main"));
    // thema gewisseld: alles staat in CSS-variabelen, niets te doen.
    setAuto();
    await load({ anim: true });
  }
  kpiSkeleton();
  init().catch((e) => { $("st-err").hidden = false; $("st-err").textContent = e.message; });
})();
