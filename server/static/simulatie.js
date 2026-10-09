/* Toestellen → Simulatie: vereenvoudigde, versnelde weergave van hoe de tracker zich gedraagt
   met zijn huidige instellingen, in klassieke modus of in FIFO-modus (firmware 0.9.0).
   Model: een rit aan vaste snelheid (licht kronkelend, voor het uitdunnen) met een dode zone
   zonder dekking. Een gehoorde herhaling betekent ook: aangekomen op de server.
   Instellingen: uit de status van de verbonden tracker (MTDev.kv); ontbreekt een sleutel, dan uit
   het formulier Instellingen (als dat zichtbaar is), anders standaardwaarden. Testhaak: window.MTSim. */
(function () {
  const $ = (id) => document.getElementById(id);
  const pane = $("pane-sim");
  if (!pane) return;

  const PER_FT = 7;           // punten in een FastTrack-bericht (tekst), hoofdpunt inbegrepen
  const PER_L = 7;            // punten in een SlowTrack-bericht (L, tekst)
  // punten in een inhaalbericht (Q, compact binair): "<naam>: " gaat van elk bericht af (zoals de firmware)
  const nameBytes = (s) => new TextEncoder().encode(s || "").length;
  const perForName = (nb) => Math.max(2, 1 + Math.floor(Math.max(0, 156 - (nb + 2) - 79) / 8));
  const LISTEN_S = 60, LISTEN_EVERY = 600;   // luistervenster bij oude punten zonder recente dekking
  const REST_BACKOFF = [600, 1200, 2400, 3600];   // in rust zonder dekking: opnieuw luisteren na 10, 20, 40, daarna elke 60 min
  const HEAR_S = 10;          // luistervenster voor een herhaling
  const REPEATS = 6;          // aangenomen herhalingen per bericht in de mesh
  const AIR_NORMAL = 1.1, AIR_FULL = 1.4, AIR_ACK = 1.0;   // zendtijd in seconden
  const FT_BUF = 20;          // plaatsen in de FastTrack-buffer (aanname voor de simulatie)
  const BACKOFF = [1, 5, 15, 60];   // minuten wachten na de 1e, 2e, 3e en volgende mislukte poging
  const SRV_ONLY = 0.25;      // kans dat een niet-gehoord inhaalbericht toch op de server aankomt
  const ACK_DELAY = 20, ACK_MIN_GAP = 600;   // T1F: ≈ 20 s na de reeks, per tracker hoogstens 1 per 10 min
  const STOP_KEEP = 600;      // punten rond een stilstand van meer dan 10 min blijven
  const DEF = { sample: 10, min_interval: 30, max_interval: 120, min_dist: 25, min_speed: 0, slow_log: 0, slow_send: 1800,
    fifo_max: 500, fifo_min: 5, fifo_gap: 30, fifo_per_uur: 20, fifo_pogingen: 3, fifo_dun: 10, fifo_snr: -5, fifo_wacht: 1800, still_timeout: 300, track_mode: "classic" };
  const DURK = ["sample", "min_interval", "max_interval", "slow_log", "slow_send", "fifo_gap", "fifo_wacht", "still_timeout"];
  const NUMK = ["min_dist", "min_speed", "fifo_max", "fifo_min", "fifo_per_uur", "fifo_pogingen", "fifo_dun", "fifo_snr"];
  const dB = (n) => `${String(n).replace("-", "−")} dB`;   // met een echt minteken
  const nf = (x, d = 0) => Number(x).toLocaleString("nl-BE", { maximumFractionDigits: d });
  const SVGNS = "http://www.w3.org/2000/svg";

  // ---- instellingen lezen ---------------------------------------------------------------
  function parseDur(v) {
    const s = String(v).trim();
    if (s === "") return null;
    if (/^(uit|off|-)$/i.test(s)) return 0;
    const m = /^(\d+(?:\.\d+)?)\s*([smh]?)$/.exec(s);
    return m ? Number(m[1]) * ({ s: 1, m: 60, h: 3600 }[m[2] || "s"]) : null;
  }
  const panelShown = () => { const p = $("s-panel"); return !!p && !p.hidden; };
  function fromForm(k) {
    const el = document.querySelector(`#s-form [data-set="${k}"]`);
    if (!el || el.disabled || (el.querySelector("input") && el.querySelector("input").disabled)) return null;
    if (el.classList.contains("dur")) {
      const i = el.querySelector("input"), u = el.querySelector("select");
      if (!i || i.value === "") return null;
      return Number(i.value) * ({ s: 1, m: 60, h: 3600 }[u.value] || 1);
    }
    return el.value === "" ? null : el.value;
  }
  function readSettings() {
    const kv = window.MTDev && MTDev.kv && MTDev.kv.pubkey ? MTDev.kv : null;
    const cfg = {}, src = { t: 0, f: 0, d: 0 };
    for (const k of Object.keys(DEF)) {
      let v = kv && kv[k] != null ? kv[k] : null, from = v != null ? "t" : null;
      // niet in de status (oudere firmware): het formulier, maar alleen als dat zichtbaar en ingevuld is
      if (v == null && panelShown()) { v = fromForm(k); if (v != null) from = "f"; }
      let out = null;
      if (v != null) {
        if (DURK.includes(k)) out = typeof v === "number" ? v : parseDur(v);
        else if (k === "track_mode") out = v === "fifo" ? "fifo" : "classic";
        else if (NUMK.includes(k)) { const n = parseFloat(v); out = Number.isFinite(n) ? n : /^uit$/i.test(v) ? 0 : null; }
      }
      if (out == null) { out = DEF[k]; from = "d"; }
      cfg[k] = out;
      src[from]++;
    }
    cfg.fifoOnTracker = !!(kv && "track_mode" in kv);
    cfg.naam = kv ? kv.naam || "" : null;
    cfg.nameBytes = kv ? nameBytes(kv.naam) : null;
    const pb = kv ? parseInt(kv.fifo_per_bericht, 10) : NaN;
    cfg.perTracker = Number.isFinite(pb) ? pb : null;
    const extra = [src.f && "het tabblad Instellingen", src.d && "standaardwaarden"].filter(Boolean).join(" en ");
    cfg.source = kv ? `Instellingen van de verbonden tracker ${kv.naam || ""}`.trim() + (extra ? `, aangevuld met ${extra}` : "") + "."
      : src.f ? "Geen tracker verbonden: waarden uit het tabblad Instellingen, aangevuld met standaardwaarden."
      : "Geen tracker verbonden: standaardwaarden. Verbind een tracker om met zijn eigen instellingen te simuleren.";
    cfg.fifo_min = Math.max(1, Math.min(cfg.fifo_min, cfg.fifo_max));
    cfg.fifo_pogingen = Math.max(1, cfg.fifo_pogingen);
    return cfg;
  }

  // ---- het model ---------------------------------------------------------------------------
  // deterministische "toevalsgetallen" per tijdstip en soort: beide modi zien dezelfde missers
  function hashRnd(t, k) {
    let x = (t * 2654435761 + k * 40503 + 12345) >>> 0;
    x ^= x >>> 16; x = Math.imul(x, 2246822507) >>> 0; x ^= x >>> 13; x = Math.imul(x, 3266489909) >>> 0; x ^= x >>> 16;
    return (x >>> 0) / 4294967296;
  }
  const KIND = { ft: 1, slow: 2, fifo: 3, ack: 4 };
  const KNAME = { ft: "FastTrack-bericht", slow: "SlowTrack-bericht (L)", fifo: "Inhaalbericht (Q)" };

  function ftInterval(cfg, speed) {
    const v = speed / 3.6;
    const moving = speed > 0 && speed >= cfg.min_speed;
    let iv = !moving ? Infinity : cfg.min_dist > 0 ? cfg.min_dist / v : 0;
    iv = Math.max(cfg.min_interval, iv);
    if (cfg.max_interval > 0) iv = Math.max(cfg.min_interval, Math.min(iv, cfg.max_interval));
    return Number.isFinite(iv) ? Math.max(1, Math.round(iv)) : 0;
  }
  function thinEven(arr, n) {
    if (arr.length <= n) return arr.slice();
    if (n <= 0) return [];
    if (n === 1) return [arr[arr.length - 1]];
    const out = [];
    for (let i = 0; i < n; i++) out.push(arr[Math.round(i * (arr.length - 1) / (n - 1))]);
    return [...new Set(out)];
  }

  function makeSim(cfg0, sc, mode) {
    const cfg = { ...cfg0, ...(sc.over || {}) };
    const S = { t: 0, end: Math.round(sc.dur * 60), cfg, sc, mode, iv: ftInterval(cfg, sc.speed),
      points: [], ftBuf: [], slowBuf: [], queue: [], pend: [],
      sent: { ft: 0, slow: 0, fifo: 0, ack: 0 }, ok: { ft: 0, slow: 0, fifo: 0 }, air: 0, ackAir: 0, no: { ft: 0, slow: 0, fifo: 0 },
      lastFt: -1e9, signals: [], lastAckRx: -1e9, retryAt: 0, flushing: false, flushStop: -1e9, lastFlush: -1e9, flushTimes: [], countTimes: [], qMsgs: [], capWhy: "",
      capNoted: false, ackDue: null, ackUpTo: 0, lastAck: -1e9, acked: 0, gaveUp: 0, thinned: 0, evicted: 0, toFifo: 0,
      log: [], fx: [], ver: 0, done: false, snr: null, stable: false,
      perQ: sc.perQ, lastListen: -1e9, listenUntil: -1, listenHeard: 0, listens: 0, waitSends: 0,
      lastMotion: 0, rest: false, restListenAt: Infinity, restStep: 0, restSends: 0, nextSlow: cfg.slow_log };
    const PER = { ft: PER_FT, slow: PER_L, fifo: S.perQ };
    const v = sc.speed / 3.6;
    // beweging: stil tussen st0 en st1 (fracties van de tijd); afgelegde weg per seconde
    const moving = (t) => { const f = t / S.end; return !(f >= sc.st0 && f < sc.st1); };
    const dist = new Float64Array(S.end + 2);
    for (let t = 1; t < dist.length; t++) dist[t] = dist[t - 1] + (moving(t - 1) ? v : 0);
    const L = dist[S.end] || 1;
    const posF = (t) => dist[Math.max(0, Math.min(S.end, Math.floor(t)))] / L;
    // ligging in meter: langs de weg en een zachte kronkel opzij
    const xy = (t) => { const a = dist[Math.min(S.end, t)]; return [a, 260 * Math.sin(a / 1900) + 90 * Math.sin(a / 560 + 1)]; };
    // dekking hangt af van de plaats op de rit, niet van de tijd
    const covered = (t) => { const f = posF(t); return !(f >= sc.dz0 && f < sc.dz1); };
    S.moving = moving; S.posF = posF; S.cfgEff = cfg;
    { let a = -1, b = -1; for (let t = 0; t <= S.end; t++) if (!covered(t)) { if (a < 0) a = t; b = t; } S.dzT = a >= 0 ? [a, b + 1] : null; }
    // SNR: geen ontvangst in de dode zone, zwak aan de rand, sterker verder weg
    function snrAt(t) {
      if (!covered(t)) return null;
      const f = posF(t);
      const d = sc.dz1 > sc.dz0 ? Math.min(Math.abs(f - sc.dz0), Math.abs(f - sc.dz1)) : 1;
      const edge = Math.min(1, (d * L) / 2500);                     // na ≈ 2,5 km volle sterkte
      return Math.max(-20, Math.min(12, Math.round(-14 + 24 * edge + (hashRnd(t, 7) * 6 - 3))));
    }
    const missP = (t) => { const s = snrAt(t); return s == null ? 1 : Math.min(0.95, sc.miss + Math.max(0, -2 - s) * 0.06); };
    S.covered = covered; S.snrAt = snrAt;
    const say = (t, text, cls) => S.log.push({ t, text, cls });
    const point = (t, kind, st) => {
      const p = { id: S.points.length, t, kind, st, srv: 0, srvLate: false, tries: 0, park: false, final: false, pf: false, missed: false, su: false, thin: false, xy: xy(t) };
      S.points.push(p); return p;
    };
    const toServer = (p, late) => { if (!p.srv) p.srvLate = late; p.srv++; };
    const qlen = () => S.queue.length;
    const pl = (n, a = "punt", b = "punten") => `${n} ${n === 1 ? a : b}`;

    function lineDist(p, a, b) {                     // afstand van p tot de lijn a–b, in meter
      const [x, y] = p.xy, [x1, y1] = a.xy, [x2, y2] = b.xy, dx = x2 - x1, dy = y2 - y1, L2 = dx * dx + dy * dy;
      if (!L2) return Math.hypot(x - x1, y - y1);
      const u = Math.max(0, Math.min(1, ((x - x1) * dx + (y - y1) * dy) / L2));
      return Math.hypot(x - (x1 + u * dx), y - (y1 + u * dy));
    }
    // rond een stilstand van meer dan 10 min: een grote tijdsprong, of buren die even lang op dezelfde plek liggen
    const stopNear = (a, p, b) => p.t - a.t > STOP_KEEP || b.t - p.t > STOP_KEEP
      || (b.t - a.t >= STOP_KEEP && Math.hypot(b.xy[0] - a.xy[0], b.xy[1] - a.xy[1]) < 25);
    const busyPt = (p) => p.inQ || p.park;
    // in de wachtrij zetten: op tijd gesorteerd, met uitdunnen van rechte stukken en verdringen op vorm
    function fifoInsert(p, t, why) {
      p.st = "queue"; p.pf = false;
      let i = S.queue.length;
      while (i > 0 && S.queue[i - 1].t > p.t) i--;
      S.queue.splice(i, 0, p);
      S.toFifo++;
      if (why) say(t, why, "bad");
      // uitdunnen: het vorige punt ligt misschien op de lijn tussen zijn buren
      if (cfg.fifo_dun > 0) {
        const j = i - 1;
        if (j >= 1 && i < S.queue.length) {
          const a = S.queue[j - 1], q = S.queue[j], b = S.queue[i];
          if (!busyPt(q) && !stopNear(a, q, b) && lineDist(q, a, b) < cfg.fifo_dun) {
            S.queue.splice(j, 1); q.st = "gone"; q.thin = true; S.thinned++;
          }
        }
      }
      // vol: het punt met de minste vorminformatie valt weg (niet het oudste)
      let n = 0, last = null;
      while (S.queue.length > cfg.fifo_max) {
        let best = -1, bd = Infinity;
        for (let k = 1; k < S.queue.length - 1; k++) {
          const q = S.queue[k];
          if (busyPt(q)) continue;
          const a = S.queue[k - 1], b = S.queue[k + 1];
          const d = stopNear(a, q, b) ? Infinity : lineDist(q, a, b);
          if (d < bd) { bd = d; best = k; }
        }
        if (best < 0) best = 0;
        const [o] = S.queue.splice(best, 1);
        o.st = "gone"; S.evicted++; n++; last = [o, bd];
      }
      if (n) {
        say(t, `Wachtrij vol (${cfg.fifo_max}): ${n === 1 ? `het punt van ${clock(last[0].t).slice(0, 5)} valt weg` : `${n} punten vallen weg`}`
          + (Number.isFinite(last[1]) ? ` (lag maar ${nf(last[1])} m naast de lijn tussen zijn buren)` : ""), "bad");
        S.fx.push({ type: "drop" });
      }
    }
    function ftBufCap(t) {
      while (S.ftBuf.length > FT_BUF) {
        const o = S.ftBuf.shift();
        if (S.mode === "fifo" && o.pf && !o.srv) {
          fifoInsert(o, t, `Gemist hoofdpunt van ${clock(o.t).slice(0, 5)} valt uit de FastTrack-buffer zonder ooit gehoord te zijn → naar de FIFO (${pl(qlen() + 1)})`);
          S.fx.push({ type: "tofifo" });
        } else o.st = "gone";
      }
    }
    function send(t, kind, pts, extra = {}) {
      S.no[kind]++; S.sent[kind]++;
      const ok = covered(t) && hashRnd(t, KIND[kind]) >= missP(t);
      const srvGot = ok || (kind === "fifo" && covered(t) && hashRnd(t, 11) < SRV_ONLY);
      const m = { kind, no: S.no[kind], t, ok, srvGot, res: ok ? t + 2 + Math.floor(hashRnd(t, 9) * 3) : t + HEAR_S, pts, ...extra };
      S.air += pts.length >= PER[kind] ? AIR_FULL : AIR_NORMAL;
      S.pend.push(m);
      S.fx.push({ type: "send", kind });
      return m;
    }
    function signal(t, snr, why) { S.signals.push({ t, snr, why }); S.signals = S.signals.filter((s) => s.t > t - 180); }
    function stableAt(t) {
      const rec = S.signals.filter((s) => s.t > t - 120);
      if (rec.some((s) => s.why === "ack")) return "bevestiging van de server";
      if (rec.some((s) => s.snr != null && s.snr >= cfg.fifo_snr)) return `herhaling met SNR ≥ ${dB(cfg.fifo_snr)}`;
      for (let i = 1; i < rec.length; i++) if (rec[i].t - rec[i - 1].t <= 60) return "twee tekens van dekking binnen 60 s";
      return "";
    }

    function resolve(m, t) {
      const n = m.pts.length;
      if (m.ok) {
        S.ok[m.kind]++;
        if (m.kind === "fifo" && !m.counted) { m.counted = true; S.countTimes.push(m.t); }   // doorgegeven: telt voor fifo_per_uur
        const snr = snrAt(m.t);
        signal(t, snr, "rep");
        S.fx.push({ type: "ok", kind: m.kind });
        if (m.kind === "ft") {
          let late = 0;
          m.pts.forEach((p, i) => {
            const isLate = i > 0 && (p.missed || p.pf || p.t < m.t - S.iv - 1);
            if (isLate) late++;
            toServer(p, isLate);
            if (p.st === "queue") { const k = S.queue.indexOf(p); if (k >= 0) S.queue.splice(k, 1); }
            p.st = "gone";
          });
          if (S.mode === "fifo") S.ftBuf = S.ftBuf.filter((p) => p.st !== "gone");
          say(t, `${KNAME.ft} #${m.no} gehoord ✓ (${pl(n)}${late ? `, waarvan ${late} gemist en meegelift` : ""}; SNR ${dB(snr)})`, late ? "okplus" : "ok");
        } else if (m.kind === "slow") {
          m.pts.forEach((p) => { toServer(p, true); p.st = "gone"; });
          say(t, `${KNAME.slow} #${m.no} gehoord ✓: ${pl(n, "gelogd punt", "gelogde punten")} aangekomen`, "ok");
        } else {
          let k = 0;
          const fin = m.pts.some((p) => p.final);
          m.pts.forEach((p) => { p.inQ = false; toServer(p, true); if (p.st === "queue") { k++; p.st = "gone"; const i = S.queue.indexOf(p); if (i >= 0) S.queue.splice(i, 1); } });
          say(t, `${KNAME.fifo} #${m.no} gehoord ✓: ${pl(k, fin ? "geparkeerd punt" : "punt", fin ? "geparkeerde punten" : "punten")} ingehaald, nog ${qlen()} in de wachtrij`, "ok");
        }
        if (m.kind === "fifo" && m.f) serverAckRequest(t, m);
        return;
      }
      S.fx.push({ type: "miss", kind: m.kind });
      if (m.kind === "ft") {
        if (S.mode === "fifo") {
          const [main, ...extra] = m.pts;
          main.pf = true; main.missed = true;
          extra.forEach((p) => { p.missed = true; });
          say(t, `${KNAME.ft} #${m.no} niet gehoord ✗ → de punten blijven in de FastTrack-buffer en liften mee met het volgende bericht; hoofdpunt gemarkeerd voor de FIFO`, "bad");
        } else {
          m.pts.forEach((p) => { p.st = "gone"; });
          say(t, `${KNAME.ft} #${m.no} niet gehoord ✗ → ${pl(n)} verloren (de buffer werd bij het versturen al gewist)`, "bad");
        }
      } else if (m.kind === "slow") {
        m.pts.forEach((p) => { p.st = "gone"; });
        say(t, `${KNAME.slow} #${m.no} niet gehoord ✗ → ${pl(n, "gelogd punt", "gelogde punten")} verloren`, "bad");
      } else {
        // wel op de server aangekomen, maar de herhaling niet gehoord: de server heeft ze al
        if (m.srvGot) m.pts.forEach((p) => toServer(p, true));
        // mislukte poging: teller per punt omhoog, telkens langer wachten; op = geparkeerd; laatste kans mislukt = opgegeven
        let level = 0, shown = 0, parked = 0, gave = 0;
        m.pts.forEach((p) => {
          p.inQ = false;
          if (p.st !== "queue") return;
          p.tries++; p.su = true;
          if (p.final) { p.st = "gone"; p.gaveup = true; gave++; const i = S.queue.indexOf(p); if (i >= 0) S.queue.splice(i, 1); return; }
          if (p.tries >= cfg.fifo_pogingen) { if (!p.park) parked++; p.park = true; }
          else level = Math.max(level, p.tries);      // geparkeerde punten houden de rest niet op
          shown = Math.max(shown, p.tries);
        });
        S.gaveUp += gave;
        const wait = level ? BACKOFF[Math.min(level, BACKOFF.length) - 1] : 0;
        S.retryAt = t + wait * 60;
        S.flushing = false; S.flushStop = t;
        say(t, `${KNAME.fifo} #${m.no} niet gehoord ✗${m.srvGot ? " (maar wel aangekomen op de server)" : ""}`
          + (gave ? ` → laatste kans mislukt: ${gave} geparkeerde punten opgegeven` : ` (poging ${shown} van ${cfg.fifo_pogingen})` + (parked ? ` → ${parked} punten geparkeerd tot de rest verstuurd is` : ""))
          + (wait ? `; volgende poging ten vroegste over ${wait} min, bij stabiele dekking` : "; de volgende punten gaan door zodra de dekking weer stabiel is"), "bad");
        if (m.srvGot && m.f) serverAckRequest(t, m);
      }
    }
    // server: bevestiging alleen als erom gevraagd werd, ≈ 20 s na de reeks, per tracker hoogstens 1 per 10 min
    function serverAckRequest(t, m) {
      for (const p of m.pts) if (p.srv && p.t > S.ackUpTo) S.ackUpTo = p.t;   // tijdstip van het jongste ontvangen inhaalpunt
      S.ackDue = Math.max(t + ACK_DELAY, S.lastAck + ACK_MIN_GAP);
    }
    function serverAck(t) {
      S.ackDue = null; S.lastAck = t; S.sent.ack++; S.ackAir += AIR_ACK;
      const heard = covered(t) && hashRnd(t, KIND.ack) >= missP(t);
      const upTo = S.ackUpTo;
      if (!heard) { say(t, `Server stuurt T1F "ontvangen tot ${clock(upTo).slice(0, 5)}", maar de tracker hoort het niet`, "bad"); return; }
      signal(t, snrAt(t), "ack"); S.lastAckRx = t;
      let k = 0;
      S.queue = S.queue.filter((p) => { if (p.srv && p.t <= upTo && !p.inQ) { p.st = "gone"; p.acked = true; k++; return false; } return true; });
      S.acked += k;
      // aangekomen maar niet gehoorde Q-berichten tellen alsnog mee, op hun verzendtijd (als dat binnen het uur valt)
      let late = 0;
      for (const m of S.qMsgs) if (!m.counted && m.srvGot && m.t > t - 3600 && m.pts.every((p) => p.t <= upTo)) { m.counted = true; S.countTimes.push(m.t); late++; }
      S.fx.push({ type: "ack" });
      say(t, `Server stuurt T1F "ontvangen tot ${clock(upTo).slice(0, 5)}" → ` + (k ? `${pl(k)} uit de wachtrij (wel aangekomen, herhaling niet gehoord)`
        : "niets meer te schrappen: die punten waren intussen al via een gehoorde herhaling weg")
        + (late ? `; ${late === 1 ? "1 inhaalbericht telt" : `${late} inhaalberichten tellen`} alsnog als doorgegeven` : ""), "info");
    }

    S.step = function () {
      if (S.done) return;
      const t = S.t;
      S.pend = S.pend.filter((m) => { if (m.res <= t) { resolve(m, t); return false; } return true; });
      if (S.ackDue != null && t >= S.ackDue) serverAck(t);
      S.snr = snrAt(t);
      // FastTrack
      let sentNow = false;
      if (S.iv && moving(t) && t - S.lastFt >= S.iv) {
        S.lastFt = t; sentNow = true;
        const main = point(t, "ft", S.mode === "fifo" ? "buf" : "air");
        let extra;
        if (S.mode === "fifo") {
          // eerst meeliften: gemiste hoofdpunten (oudste eerst), dan de rest gelijkmatig verdeeld
          const pf = S.ftBuf.filter((p) => p.pf), rest = S.ftBuf.filter((p) => !p.pf);
          const a = pf.slice(0, PER_FT - 1);
          extra = [...a, ...thinEven(rest, PER_FT - 1 - a.length)].sort((x, y) => x.t - y.t);
          S.ftBuf.push(main);
          ftBufCap(t);
        } else {
          // klassiek: zoveel bewaarde punten als erin passen; de buffer wordt bij het versturen gewist
          extra = thinEven(S.ftBuf, PER_FT - 1);
          S.ftBuf.forEach((p) => { if (!extra.includes(p)) p.st = "gone"; });
          extra.forEach((p) => { p.st = "air"; });
          S.ftBuf = [];
        }
        send(t, "ft", [main, ...extra]);
      }
      if (!sentNow && moving(t) && t > 0 && cfg.sample > 0 && t % cfg.sample === 0) { S.ftBuf.push(point(t, "ft", "buf")); ftBufCap(t); }
      // Rust: langer dan still_timeout geen beweging (versnellingsmeter of GPS ≥ 1,5 km/u)
      const mov = moving(t);
      if (mov) S.lastMotion = t;
      const rest = !mov && t - S.lastMotion > cfg.still_timeout;
      const wasRest = S.rest;
      S.rest = rest;
      if (rest && !wasRest) {
        say(t, `In rust: al ${fmtDur(cfg.still_timeout)} geen beweging. SlowTrack pauzeert (de GPS blijft uit)`, "");
        if (S.mode === "fifo" && qlen()) { S.restListenAt = t; S.restStep = 0; S.restListens = 0; }   // meteen 60 s luisteren
      }
      if (!rest && wasRest) {
        S.restStep = 0; S.restListenAt = Infinity;
        say(t, "Weer in beweging", "");
        if (cfg.slow_log > 0) S.nextSlow = t;           // eerste beweging: meteen een SlowTrack-punt
      }
      // SlowTrack: alleen zolang hij beweegt (of nog niet in rust is)
      if (cfg.slow_log > 0 && !rest && t > 0 && t >= S.nextSlow) {
        S.nextSlow = t + cfg.slow_log;
        if (S.mode === "fifo") fifoInsert(point(t, "slow", "queue"), t);
        else S.slowBuf.push(point(t, "slow", "buf"));
      }
      if (S.mode === "classic" && cfg.slow_log > 0 && cfg.slow_send > 0 && t > 0 && t % cfg.slow_send === 0 && S.slowBuf.length) {
        const pts = thinEven(S.slowBuf, PER_L);
        const drop = S.slowBuf.filter((p) => !pts.includes(p));
        drop.forEach((p) => { p.st = "gone"; });
        pts.forEach((p) => { p.st = "air"; });
        S.slowBuf = [];
        send(t, "slow", pts);
        if (drop.length) say(t, `SlowTrack: ${drop.length + pts.length} gelogde punten, uitgedund tot ${pts.length} in één L-bericht`, "");
      }
      // FIFO leegmaken
      if (S.mode === "fifo") {
        S.flushTimes = S.flushTimes.filter((x) => x > t - 3600);
        S.countTimes = S.countTimes.filter((x) => x > t - 3600);
        S.qMsgs = S.qMsgs.filter((m) => m.t > t - 3600);
        const listen = (why) => {
          S.lastListen = t; S.listenUntil = t + LISTEN_S; S.listenHeard = 0; S.listens++;
          say(t, `Luistervenster van ${LISTEN_S} s: ${why}`, "");
          S.fx.push({ type: "listen" });
        };
        if (rest) {
          // in rust met punten: meteen luisteren; zonder dekking opnieuw na 10, 20, 40 en daarna elke 60 min
          if (qlen() && !S.flushing && t >= S.restListenAt && t >= S.listenUntil) {
            listen(S.restListens++ ? "in rust met punten in de wachtrij, opnieuw" : "net in rust met punten in de wachtrij");
            S.restListenAt = t + LISTEN_S + REST_BACKOFF[Math.min(S.restStep, REST_BACKOFF.length - 1)];
            S.restStep++;
          }
        } else if (cfg.fifo_wacht > 0 && qlen() && !S.flushing && t - S.queue[0].t >= cfg.fifo_wacht
            && !S.signals.some((x) => x.t > t - LISTEN_EVERY) && t - S.lastListen >= LISTEN_EVERY) {
          // in beweging: punten ouder dan fifo_wacht en geen recente dekking: hoogstens 1× per 10 min 60 s luisteren
          listen(`punten ouder dan ${fmtDur(cfg.fifo_wacht)} en geen recente dekking`);
        }
        if (t < S.listenUntil && covered(t) && hashRnd(t, 13) < 0.05) {
          const sn = snrAt(t);
          signal(t, sn, "rx");
          if (!S.listenHeard) say(t, `Pakket via een repeater gehoord tijdens het luisteren (SNR ${dB(sn)})`, "ok");
          S.listenHeard++;
          if (rest) { S.restStep = 0; S.restListenAt = t + REST_BACKOFF[0]; }   // dekking gevonden: terug naar 10 min
        }
        if (t === S.listenUntil && !S.listenHeard) say(t, `Luistervenster voorbij: niets gehoord${rest ? `; opnieuw over ${fmtDur(REST_BACKOFF[Math.min(S.restStep - 1, REST_BACKOFF.length - 1)])}` : ""}`, "");
        const why2 = stableAt(t);
        S.stable = !!why2;
        const oldAge = qlen() ? t - S.queue[0].t : 0;
        const below = qlen() > 0 && qlen() < cfg.fifo_min;
        const byWait = !rest && cfg.fifo_wacht > 0 && below && oldAge >= cfg.fifo_wacht;
        const byRest = rest && below;
        if (!S.flushing && (qlen() >= cfg.fifo_min || byWait || byRest) && t >= S.retryAt && why2 && S.signals.some((s) => s.t > S.flushStop)) {
          S.flushing = true;
          if (byWait) { S.waitSends++; S.fx.push({ type: "wait" }); }
          if (byRest) S.restSends++;
          say(t, byRest ? `In rust met ${pl(qlen())} en stabiele dekking (${why2}): de wachtrij wordt leeggemaakt, ook onder ${cfg.fifo_min} punten`
            : byWait ? `${pl(qlen())} in de wachtrij, minder dan ${cfg.fifo_min}, maar het oudste wacht al ${fmtDur(Math.floor(oldAge / 60) * 60)} (langer dan ${fmtDur(cfg.fifo_wacht)}) en de dekking is stabiel (${why2}): toch versturen`
            : `Stabiele dekking (${why2}) en ${qlen()} ≥ ${cfg.fifo_min} punten: de wachtrij wordt leeggemaakt, oudste eerst`, "info");
          S.fx.push({ type: "flush" });
        }
        if (S.flushing) {
          const busy = S.pend.some((m) => m.kind === "fifo");
          if (!busy && !qlen()) { S.flushing = false; say(t, "Wachtrij leeg: alles ingehaald", "ok"); }
          else if (!busy && t - S.lastFlush >= cfg.fifo_gap && t >= S.retryAt) {
            // twee plafonds: doorgegeven berichten (fifo_per_uur) en alle pogingen (2 × fifo_per_uur)
            const capWhy = S.countTimes.length >= cfg.fifo_per_uur ? "uur" : S.flushTimes.length >= 2 * cfg.fifo_per_uur ? "pogingen" : "";
            S.capWhy = capWhy;
            if (capWhy) {
              if (!S.capNoted) {
                S.capNoted = true;
                say(t, capWhy === "uur" ? `Uurlimiet bereikt: ${cfg.fifo_per_uur} / ${cfg.fifo_per_uur} doorgegeven berichten in het voorbije uur; wachten`
                  : `Veiligheidsgrens bereikt: ${2 * cfg.fifo_per_uur} / ${2 * cfg.fifo_per_uur} pogingen in het voorbije uur (zendtijd van de tracker); wachten`, "info");
              }
            } else {
              S.capNoted = false;
              // eerst de gewone punten (oudste eerst); geparkeerde pas als al de rest weg is, als laatste kans
              let pts = S.queue.filter((p) => !p.park).slice(0, S.perQ);
              if (!pts.length) {
                pts = S.queue.filter((p) => p.park && !p.final).slice(0, S.perQ);
                pts.forEach((p) => { p.final = true; });
                if (pts.length) say(t, `Laatste kans voor ${pl(pts.length, "geparkeerd punt", "geparkeerde punten")}`, "info");
              }
              if (pts.length) {
                const f = S.queue.some((p) => p.su);     // alleen om bevestiging vragen bij verstuurde, niet-gehoorde punten
                pts.forEach((p) => { p.inQ = true; });
                S.lastFlush = t; S.flushTimes.push(t);
                S.qMsgs.push(send(t, "fifo", pts, { f }));
              }
            }
          }
        }
      }
      S.ver++;
      S.t++;
      if (S.t > S.end) {
        S.done = true;
        S.pend.sort((a, b) => a.res - b.res).forEach((m) => resolve(m, m.res));
        S.pend = [];
        const w = S.points.filter((p) => !p.srv && p.st !== "gone").length;
        say(S.end, `Einde van de rit.${w ? ` ${w} ${w === 1 ? "punt staat" : "punten staan"} nog op de tracker${S.mode === "fifo" && qlen() ? ` (${qlen()} in de wachtrij)` : ""}.` : ""}`, "info");
      }
    };
    S.count = function () {
      const c = { logged: S.points.length, live: 0, late: 0, lost: 0, thin: 0, wait: 0, park: 0, dup: 0 };
      for (const p of S.points) {
        if (p.srv) { if (p.srvLate) c.late++; else c.live++; c.dup += p.srv - 1; }
        else if (p.thin) c.thin++;
        else if (p.st === "gone") c.lost++;
        else { c.wait++; if (p.park) c.park++; }
      }
      c.server = c.live + c.late;
      c.pct = c.logged ? Math.round(100 * c.server / c.logged) : 0;
      c.sent = { ...S.sent }; c.ok = { ...S.ok }; c.air = S.air; c.mesh = (S.air + S.ackAir) * (1 + REPEATS);
      c.evicted = S.evicted; c.acked = S.acked; c.gaveUp = S.gaveUp; c.toFifo = S.toFifo;
      c.hour = S.countTimes.filter((x) => x > S.t - 3600).length;
      c.tries = S.flushTimes.filter((x) => x > S.t - 3600).length;
      c.queue = qlen(); c.listens = S.listens; c.waitSends = S.waitSends; c.restSends = S.restSends;
      return c;
    };
    return S;
  }

  // ---- opbouw van het tabblad ---------------------------------------------------------------
  const V = (k) => `<span class="sim-v" data-k="${k}"></span>`;
  const UITLEG = [
    ["doel", "Het doel", "#sim-track",
      `Zo weinig mogelijk van je route verliezen, ook als je uren tot een hele dag buiten bereik zit, en tegelijk het net zo weinig
       mogelijk belasten. Elk bericht wordt immers door meerdere repeaters herhaald.`],
    ["ft", "FastTrack en het luistervenster", "#sim-strip",
      `Onderweg stuurt de tracker geregeld zijn positie (FastTrack), met een paar eerdere punten erbij. Daarna luistert hij zo'n
       10 seconden: dat is de ring rond de stip. Herhaalt een repeater zijn bericht, dan is het aangekomen (groen vinkje). Hoort hij
       niets, dan geldt het als gemist (rood kruisje).`],
    ["lift", "Eerst meeliften", "#sim-ftbox",
      `Een gemist bericht is niet meteen verloren. Zijn punten blijven in de FastTrack-buffer en reizen gratis mee met de volgende
       berichten. Het hoofdpunt krijgt een oranje ring. Pas als het uit de buffer valt zonder ooit gehoord te zijn, gaat het naar de
       wachtrij. Korte gaten vullen zich zo zonder één extra bericht.`],
    ["fifo", "De FIFO-wachtrij", "#sim-fifobox",
      `Voor lange gaten is er de wachtrij (FIFO: wie eerst komt, gaat eerst weg). Ze bewaart punten met hun echte GPS-tijd, tot
       ${V("fifo_max")} stuks, ook na een herstart. In FIFO-modus logt ook SlowTrack erin, zolang de tracker beweegt.`],
    ["dun", "Uitdunnen en verdringen", "#sim-fifobox",
      `Op een recht stuk zegt een punt tussen twee andere niets nieuws. Ligt het minder dan ${V("fifo_dun")} naast de lijn tussen
       zijn buren, dan gaat het er niet in. Is de wachtrij vol, dan valt het punt weg dat het minst over de vorm van je route zegt,
       niet gewoon het oudste. Punten rond een stilstand van meer dan 10 minuten blijven altijd.`],
    ["snr", "Stabiele dekking", "#sim-signal",
      `Aan de rand van het bereik is het signaal zwak en wisselvallig. Leegmaken begint daarom pas bij stabiele dekking: een
       herhaling met een signaal-ruisverhouding (SNR) van minstens ${V("fifo_snr")}, of twee tekens van dekking binnen 60 seconden,
       of een bevestiging van de server. In de simulatie stijgt de SNR naarmate de tracker uit de dode zone rijdt.`],
    ["flush", "Leegmaken", "#sim-fifonote",
      `Daarna stuurt de tracker één inhaalbericht per ${V("fifo_gap")}, oudste punten eerst. Een punt verlaat de wachtrij pas als de
       herhaling gehoord werd. Per uur mogen hoogstens ${V("fifo_per_uur")} berichten doorgegeven worden: alleen berichten waarvan de
       herhaling gehoord werd of die de server later bevestigde (T1F), tellen mee (de teller "x / ${V("fifo_per_uur_n")} doorgegeven").
       Pogingen zonder gehoorde herhaling tellen niet, maar om de zendtijd van de tracker te sparen geldt een vaste veiligheidsgrens van
       ${V("fifo_tries")} pogingen per uur (de teller "pogingen"). Is een van beide bereikt, dan pauzeert het leegmaken.`],
    ["park", "Pogingen en parkeren", "#sim-fifobox",
      `Wordt een inhaalbericht niet gehoord, dan telt dat als een poging voor zijn punten en wacht de tracker telkens langer: 1, 5, 15
       en daarna 60 minuten. Na ${V("fifo_pogingen")} worden de punten geparkeerd (rode ring): ze komen pas aan de beurt als al de rest
       weg is, krijgen dan nog één kans en worden daarna opgegeven. Meestal kwamen ze toch aan; alleen de herhaling werd niet gehoord.
       Zo houdt één koppig bericht de rest niet tegen.`],
    ["ack", "De bevestiging van de server", "#sim-c-ack",
      `Soms komt een inhaalbericht wel aan, maar hoort de tracker de herhaling niet. Heeft hij zulke punten, dan vraagt hij in zijn
       volgende inhaalbericht om een bevestiging. Zo'n 20 seconden na de reeks antwoordt de server "ontvangen tot tijdstip X" (T1F),
       en die punten gaan alsnog uit de wachtrij. De server blijft zuinig: per tracker hoogstens één bevestiging per 10 minuten,
       hoogstens één per minuut per kanaal en hoogstens 20 per uur in totaal, met tot 4 trackers in één bericht. Twintig trackers
       die vooral stilstaan, hebben bijna nooit een bevestiging nodig; in het slechtste geval blijft het bij 20 kleine berichten per uur.`],
    ["bin", "Compacte codering", "#sim-c-msgs",
      `Inhaalberichten (Q) zijn binair gecodeerd: elk punt is een klein verschil met het vorige. Zo passen er zo'n ${V("perq")} punten in
       één bericht, tegen ${PER_FT} in tekst. Minder berichten betekent minder belasting van het net. Op de kaart verschijnen ingehaalde
       punten als lichtere stippen, net als de gelogde punten van SlowTrack (L).`],
    ["wacht", "Niet eindeloos wachten", "#sim-fifonote",
      `Staan er minder punten in de wachtrij dan nodig om te beginnen, dan wacht de tracker normaal tot er genoeg zijn. Zolang hij
       beweegt, stuurt hij ze toch als het oudste punt ouder is dan ${V("fifo_wacht")} en de dekking stabiel is; zonder recente dekking
       luistert hij daarvoor hoogstens één keer per 10 minuten 60 seconden naar het net. Komt hij in rust met punten in de wachtrij,
       dan luistert hij meteen 60 seconden: het radio-icoontje bij de stip. Bij stabiele dekking maakt hij de wachtrij dan leeg, ook
       onder het minimum; de tijd tussen de berichten en de limiet per uur blijven gelden. Hoort hij niets, dan probeert hij het opnieuw
       na 10, 20 en 40 minuten en daarna elk uur. Beweging of gevonden dekking zet dat weer op 10 minuten. Probeer het scenario
       "Door een gat rijden, dan uren parkeren".`],
    ["rust", "SlowTrack in rust", "#sim-strip",
      `In rust, dus als de bewegingssensor en de GPS langer dan ${V("still_timeout")} geen beweging zien, zet SlowTrack de GPS niet meer
       aan. Een geparkeerde tracker logt dus geen reeks punten op dezelfde plek. Bij de eerste beweging logt hij meteen een punt.
       Uitzondering: staat FastTrack uit door een lage batterij, dan blijft SlowTrack loggen, en een SlowTrack-punt met snelheid telt
       dan als beweging. Die uitzondering zit niet in de simulatie.`],
    ["naam", "De naam telt mee", "#sim-namebox",
      `Elk kanaalbericht begint met "naam: ". Hoe langer de naam van de tracker, hoe minder punten er in een inhaalbericht passen:
       met een naam van ${V("nb")} gaan er ≈ ${V("perq")} punten in één bericht. Emoji tellen zwaar: de Belgische vlag 🇧🇪 telt als
       8 bytes. Een korte naam zonder emoji laat meer punten toe.`],
    ["count", "Wat de tellers betekenen", "#sim-count",
      `<strong>Berichten</strong>: hoeveel FastTrack-, L- en Q-berichten de tracker stuurde, en hoeveel bevestigingen (T1F) de server.
       <strong>Zendtijd</strong>: hoe lang de tracker zelf zond. <strong>Meshbelasting</strong>: die zendtijd maal de herhalingen door
       repeaters (aangenomen ≈ ${REPEATS}×). <strong>Op de server</strong>: hoeveel van de gelogde punten in het spoor staan, live of
       later ingehaald. <strong>Uitgedund</strong> punten waren overbodig op een recht stuk; <strong>verloren</strong> punten zijn
       echt weg.`],
  ];
  pane.innerHTML = `
    <p class="sim-intro"><strong>Vereenvoudigde simulatie.</strong> Zo ongeveer gedraagt de tracker zich met zijn huidige
      instellingen op een rit aan vaste snelheid, versneld afgespeeld. In de gearceerde dode zone hoort geen enkele repeater hem.
      "Gehoord" betekent hier: een repeater herhaalt het bericht, en dan is het ook op de server. Bochten, stilstand, batterij en
      echte radio-omstandigheden zitten er maar ruw in; de werkelijkheid wijkt dus af.</p>
    <details class="sim-uitleg" id="sim-uitleg"><summary>Uitleg: hoe werkt de FIFO-modus?</summary>
      <div class="sim-ugrid">${UITLEG.map(([k, h, sel, txt]) => `<section class="sim-usec" data-u="${k}"><h5>${h}</h5><p>${txt}</p>
        <button type="button" class="sim-show" data-sel="${sel}">Aanwijzen in de animatie</button></section>`).join("")}</div>
    </details>
    <div class="small muted" id="sim-src"></div>
    <div class="small muted" id="sim-cfg"></div>
    <div class="sim-presets"><span class="small muted">Scenario:</span>
      <button type="button" data-preset="std">Rit van 1 uur, gat van 30 tot 70 %</button>
      <button type="button" data-preset="long">Lange rit met gat van 3 uur</button>
      <button type="button" data-preset="stand">Door een gat rijden, dan uren parkeren</button></div>
    <div class="sim-fields">
      <div><label for="sim-speed">Snelheid</label><div class="unit"><input id="sim-speed" type="number" min="1" max="200" value="50"><span>km/u</span></div></div>
      <div><label for="sim-dur">Duur van de rit</label><div class="unit"><input id="sim-dur" type="number" min="5" max="1440" value="60"><span>min</span></div></div>
      <div><label for="sim-dz0">Dode zone begint op</label><div class="unit"><input id="sim-dz0" type="number" min="0" max="100" value="30"><span>% van de rit</span></div></div>
      <div><label for="sim-dzl">Lengte van de dode zone</label><div class="unit"><input id="sim-dzl" type="number" min="0" max="100" value="40"><span>% van de rit</span></div></div>
      <div><label for="sim-miss">Kans op een gemiste herhaling ondanks dekking</label><div class="unit"><input id="sim-miss" type="number" min="0" max="100" value="10"><span>%</span></div></div>
      <div><label for="sim-st0">Stilstand begint op</label><div class="unit"><input id="sim-st0" type="number" min="0" max="100" value="0"><span>% van de tijd</span></div></div>
      <div><label for="sim-stl">Lengte van de stilstand</label><div class="unit"><input id="sim-stl" type="number" min="0" max="100" value="0"><span>% van de tijd</span></div></div>
      <div id="sim-namebox"><label for="sim-nb">Naamlengte van de tracker</label><div class="unit"><input id="sim-nb" type="number" min="1" max="31" value="11"><span>bytes</span></div>
        <div class="help" id="sim-nbhelp"></div></div>
    </div>
    <div class="sim-play">
      <div class="seg" id="sim-mode" role="radiogroup" aria-label="Trackmodus"><button type="button" data-v="classic">Klassiek</button><button type="button" data-v="fifo">FIFO</button></div>
      <div class="seg" id="sim-rate" role="radiogroup" aria-label="Afspeelsnelheid"><button type="button" data-v="1">1×</button><button type="button" data-v="10">10×</button><button type="button" data-v="60">60×</button><button type="button" data-v="300">300×</button></div>
      <button type="button" class="primary" id="sim-play">Afspelen</button>
      <button type="button" id="sim-reset">Opnieuw</button>
      <span class="sim-clock" id="sim-clock" aria-live="off">12:00:00</span>
    </div>
    <div class="small muted" id="sim-modenote"></div>
    <div class="sim-signal small" id="sim-signal"></div>
    <div class="sim-stripwrap" tabindex="0" aria-label="Schematische kaart van de rit"><svg id="sim-strip" viewBox="0 0 1000 150" role="img" aria-label="Rit met repeaters, dode zone en tracker"></svg></div>
    <div class="sim-boxes">
      <div class="sim-box" id="sim-ftbox"><h4>FastTrack-buffer <span class="sim-cap" id="sim-ftcount"></span></h4><div class="sim-dots" id="sim-ftbuf"></div><div class="small muted" id="sim-ftnote"></div></div>
      <div class="sim-box" id="sim-slowbox"><h4>SlowTrack-buffer</h4><div class="sim-dots" id="sim-slowbuf"></div><div class="small muted" id="sim-slownote"></div></div>
      <div class="sim-box" id="sim-fifobox"><h4>FIFO-wachtrij <span class="sim-cap" id="sim-fifocount"></span></h4>
        <div class="sim-bar"><span id="sim-fifobar"></span></div>
        <div class="sim-dots small-dots" id="sim-fifo" aria-label="oudste links, nieuwste rechts"></div>
        <div class="small" id="sim-fifonote"></div></div>
      <div class="sim-box sim-count"><h4>Tellers</h4><dl id="sim-count"></dl>
        <div class="help">Zendtijd: ≈ ${nf(AIR_NORMAL, 1)} s per gewoon bericht, ≈ ${nf(AIR_FULL, 1)} s voor een vol bericht.
          Meshbelasting: aangenomen dat elk bericht ≈ ${REPEATS}× herhaald wordt; in werkelijkheid hangt dat af van het aantal repeaters in de buurt.</div></div>
    </div>
    <div class="sim-legend small">
      <span><i class="lg dot"></i> FastTrack-punt</span><span><i class="lg dot slow"></i> SlowTrack-punt</span>
      <span><i class="lg pf"></i> gemist, gemarkeerd voor FIFO</span><span><i class="lg su"></i> verstuurd, herhaling niet gehoord</span>
      <span><i class="lg park"></i> geparkeerd</span></div>
    <h4 class="sim-h">Spoor zoals de server het tekent</h4>
    <div class="sim-stripwrap"><svg id="sim-track" viewBox="0 0 1000 86" role="img" aria-label="Punten op de server in de tijd"></svg></div>
    <div class="sim-legend small">
      <span><i class="lg live"></i> live ontvangen</span><span><i class="lg late"></i> later ingehaald (meegelift, Q of L)</span>
      <span><i class="lg lost">×</i> verloren</span><span><i class="lg thin"></i> uitgedund (overbodig)</span>
      <span><i class="lg wait"></i> nog op de tracker</span><span><i class="lg park"></i> geparkeerd</span></div>
    <h4 class="sim-h">Gebeurtenissen</h4>
    <ol class="sim-log" id="sim-log"></ol>`;

  // uitleg standaard open op een groot scherm, dicht op een telefoon (anders staat de animatie ver weg)
  if (matchMedia("(min-width: 721px)").matches) $("sim-uitleg").open = true;

  const X0 = 40, X1 = 960, ROUTE_Y = 105;
  let scOver = null;
  let cfg = null, sc = null, S = null, mode = null, rate = 60, playing = false, simT = 0, lastWall = 0, logShown = 0;
  let reps = [], fx = [], marks = [], lastBoxes = 0, lastTrack = 0, loopOn = false;
  const xAt = (f) => X0 + (X1 - X0) * f;
  function clock(t) { const s = 12 * 3600 + Math.floor(t); return [Math.floor(s / 3600) % 24, Math.floor(s / 60) % 60, s % 60].map((x) => String(x).padStart(2, "0")).join(":"); }
  const fmtDur = (s) => !s ? "uit" : s % 3600 === 0 ? `${s / 3600} u` : s % 60 === 0 ? `${s / 60} min` : `${s} s`;
  const el = (tag, attrs, parent) => { const e = document.createElementNS(SVGNS, tag); for (const [k, v] of Object.entries(attrs)) e.setAttribute(k, v); if (parent) parent.appendChild(e); return e; };

  function readScenario() {
    const num = (id, d, lo, hi) => { const v = Number($(id).value); return Number.isFinite(v) && $(id).value !== "" ? Math.min(hi, Math.max(lo, v)) : d; };
    const dz0 = num("sim-dz0", 30, 0, 100) / 100, dzl = num("sim-dzl", 40, 0, 100) / 100;
    const st0 = num("sim-st0", 0, 0, 100) / 100, stl = num("sim-stl", 0, 0, 100) / 100;
    const nb = Math.round(num("sim-nb", 11, 1, 64));
    // van de tracker zelf als de naamlengte die van de tracker is, anders berekend zoals de firmware
    const perQ = cfg.perTracker && nb === cfg.nameBytes ? cfg.perTracker : perForName(nb);
    return { speed: num("sim-speed", 50, 1, 200), dur: num("sim-dur", 60, 5, 1440), dz0, dz1: Math.min(1, dz0 + dzl), miss: num("sim-miss", 10, 0, 100) / 100,
      st0, st1: Math.min(1, st0 + stl), nb, perQ, over: scOver };
  }

  function drawStrip() {
    const svg = $("sim-strip");
    svg.textContent = "";
    const L = sc.speed * sc.dur / 60;
    if (sc.dz1 > sc.dz0) {
      el("rect", { x: xAt(sc.dz0), y: 14, width: xAt(sc.dz1) - xAt(sc.dz0), height: 120, class: "sim-dz", rx: 6 }, svg);
      el("text", { x: (xAt(sc.dz0) + xAt(sc.dz1)) / 2, y: 32, class: "sim-dzt", "text-anchor": "middle" }, svg).textContent = "dode zone: geen dekking";
    }
    el("line", { x1: X0, y1: ROUTE_Y, x2: X1, y2: ROUTE_Y, class: "sim-route" }, svg);
    el("text", { x: X0, y: 142, class: "sim-km" }, svg).textContent = "0 km";
    el("text", { x: X1, y: 142, class: "sim-km", "text-anchor": "end" }, svg).textContent = `${nf(L, 1)} km`;
    // repeaters op regelmatige afstand, niet in de dode zone
    reps = [];
    for (let f = 0.06; f < 0.97; f += 0.13) if (f < sc.dz0 - 0.04 || f > sc.dz1 + 0.04) reps.push(f);
    reps.forEach((f, i) => {
      const x = xAt(f), g = el("g", { class: "sim-rep", id: `sim-rep${i}` }, svg);
      el("path", { d: `M${x - 9} 78 L${x} 46 L${x + 9} 78 Z`, class: "sim-mast" }, g);
      el("circle", { cx: x, cy: 44, r: 5, class: "sim-repdot" }, g);
      el("path", { d: `M${x - 12} 36 Q${x} 26 ${x + 12} 36`, class: "sim-wave" }, g);
    });
    el("g", { id: "sim-fxg" }, svg);
    const trk = el("g", { id: "sim-trk" }, svg);
    el("circle", { r: 16, class: "sim-ringbg", id: "sim-ringbg" }, trk);
    el("circle", { r: 16, class: "sim-ring", id: "sim-ring", transform: "rotate(-90)" }, trk);
    el("circle", { r: 8, class: "sim-dot" }, trk);
    el("text", { y: -24, class: "sim-mark", id: "sim-mark", "text-anchor": "middle" }, trk);
    el("text", { y: 36, class: "sim-snrt", id: "sim-snrt", "text-anchor": "middle" }, trk);
    // radio-icoontje: luistervenster
    const li = el("g", { id: "sim-listen", class: "sim-listen", transform: "translate(22,-22)" }, trk);
    el("circle", { r: 3, class: "sim-listen-dot" }, li);
    el("path", { d: "M-7 -5 A9 9 0 0 1 7 -5", class: "sim-listen-arc" }, li);
    el("path", { d: "M-11 -9 A15 15 0 0 1 11 -9", class: "sim-listen-arc" }, li);
    el("text", { x: 14, y: 4, class: "sim-listen-t" }, li).textContent = "luistert";
  }

  function fillVars() {
    const val = { nb: `${sc.nb} bytes`, perq: `${sc.perQ}`, fifo_wacht: cfg.fifo_wacht ? fmtDur(cfg.fifo_wacht) : "(uit)", still_timeout: fmtDur(cfg.still_timeout), fifo_max: `${cfg.fifo_max}`, fifo_dun: cfg.fifo_dun ? `${cfg.fifo_dun} m` : "0 m (uitdunnen staat uit)", fifo_snr: `${dB(cfg.fifo_snr)}`,
      fifo_gap: fmtDur(cfg.fifo_gap), fifo_per_uur: `${cfg.fifo_per_uur}`, fifo_per_uur_n: `${cfg.fifo_per_uur}`, fifo_tries: `${2 * cfg.fifo_per_uur}`,
      fifo_pogingen: `${cfg.fifo_pogingen} ${cfg.fifo_pogingen === 1 ? "poging" : "pogingen"}` };
    pane.querySelectorAll(".sim-v").forEach((s) => { s.textContent = val[s.dataset.k] || ""; });
  }

  function reset() {
    playing = false; simT = 0; logShown = 0; fx = []; marks = [];
    sc = readScenario();
    S = makeSim(cfg, sc, mode);
    drawStrip();
    fillVars();
    $("sim-log").textContent = "";
    $("sim-play").textContent = "Afspelen";
    $("sim-slowbox").hidden = mode !== "classic";
    $("sim-fifobox").hidden = mode !== "fifo";
    const iv = S.iv;
    $("sim-cfg").textContent = `Gebruikt: punt bewaren elke ${fmtDur(cfg.sample)} · FastTrack-bericht elke ${iv ? fmtDur(iv) : "– (rijdt te traag)"}`
      + ` (nooit vaker dan ${fmtDur(cfg.min_interval)}, minstens ${fmtDur(cfg.max_interval)}, ${nf(cfg.min_dist)} m) · SlowTrack ${cfg.slow_log ? `loggen elke ${fmtDur(cfg.slow_log)}${mode === "classic" ? `, versturen elke ${fmtDur(cfg.slow_send)}` : ""}` : "uit"}`
      + (mode === "fifo" ? ` · FIFO: ${cfg.fifo_min} tot ${cfg.fifo_max} punten, elke ${fmtDur(cfg.fifo_gap)}, hoogstens ${cfg.fifo_per_uur} doorgegeven en ${2 * cfg.fifo_per_uur} pogingen per uur,`
        + ` ${cfg.fifo_pogingen} ${cfg.fifo_pogingen === 1 ? "poging" : "pogingen"} per punt, uitdunnen ${cfg.fifo_dun ? `${cfg.fifo_dun} m` : "uit"}, SNR ≥ ${dB(cfg.fifo_snr)}.` : ".");
    if (sc.over) $("sim-cfg").textContent += ` In dit scenario: SlowTrack elke ${fmtDur(sc.over.slow_log)}, inhalen vanaf ${sc.over.fifo_min} punten.`;
    $("sim-nbhelp").textContent = `≈ ${sc.perQ} punten per Q-bericht` + (cfg.perTracker && sc.nb === cfg.nameBytes ? " (zoals de tracker meldt)" : "")
      + (cfg.naam != null ? `. De naam "${cfg.naam}" telt ${cfg.nameBytes} bytes.` : ".");
    render(performance.now(), true);
  }

  function setMode(v) {
    mode = v;
    $("sim-mode").querySelectorAll("button").forEach((b) => { const on = b.dataset.v === v; b.classList.toggle("on", on); b.setAttribute("aria-checked", String(on)); });
    const tr = cfg.fifoOnTracker ? (cfg.track_mode === "fifo" ? "FIFO" : "klassiek") : null;
    $("sim-modenote").textContent = tr ? `Op de tracker staat ${tr}; wissel om te vergelijken.`
      : window.MTDev && MTDev.kv && MTDev.kv.pubkey ? "Deze firmware kent alleen de klassieke modus; FIFO kan vanaf firmware 0.9.0. Wissel om te vergelijken." : "Wissel om beide modi te vergelijken.";
    reset();
  }
  function setRate(v) {
    rate = v;
    $("sim-rate").querySelectorAll("button").forEach((b) => { const on = Number(b.dataset.v) === v; b.classList.toggle("on", on); b.setAttribute("aria-checked", String(on)); });
  }

  // ---- animatie ----------------------------------------------------------------------------
  function trackerXY() { return [xAt(S.posF(Math.min(simT, S.end))), ROUTE_Y]; }
  function nearestRep(x) {
    let best = -1, d = 1e9;
    reps.forEach((f, i) => { const dd = Math.abs(xAt(f) - x); if (dd < d) { d = dd; best = i; } });
    return best;
  }
  function handleFx(now) {
    const [tx, ty] = trackerXY();
    for (const e of S.fx) {
      if (e.type === "send") {
        if (fx.length > 8) continue;
        const ri = S.covered(Math.floor(simT)) ? nearestRep(tx) : -1;
        const [x1, y1] = ri >= 0 ? [xAt(reps[ri]), 48] : [tx, 8];
        fx.push({ t0: now, dur: 450, x0: tx, y0: ty, x1, y1, cls: `sim-pkt ${e.kind}`, fade: ri < 0 });
      } else if (e.type === "ack") {
        const ri = nearestRep(tx);
        if (ri >= 0) fx.push({ t0: now, dur: 600, x0: xAt(reps[ri]), y0: 48, x1: tx, y1: ty, cls: "sim-pkt ack", fade: false });
        marks.push({ t0: now, text: "T1F", cls: "ack" });
      } else if (e.type === "ok") {
        const ri = nearestRep(tx);
        const g = ri >= 0 && $(`sim-rep${ri}`);
        if (g) { g.classList.remove("flash"); void g.getBoundingClientRect(); g.classList.add("flash"); }
        marks.push({ t0: now, text: "✓", cls: "ok" });
      } else if (e.type === "miss") {
        marks.push({ t0: now, text: "✗", cls: "bad" });
      } else if (e.type === "listen") listenWall = now + 1500;   // ook bij 300× even zichtbaar
      else if (e.type === "tofifo") flyToFifo();
      else if (e.type === "drop") { const b = $("sim-fifobox"); b.classList.remove("drop"); void b.offsetWidth; b.classList.add("drop"); }
    }
    S.fx = [];
  }
  let flying = 0, listenWall = 0;
  function flyToFifo() {
    if (flying > 5 || matchMedia("(prefers-reduced-motion: reduce)").matches) return;
    const a = $("sim-ftbuf"), b = $("sim-fifo");
    if (!a || !b || b.offsetParent === null) return;
    const ra = a.getBoundingClientRect(), rb = b.getBoundingClientRect();
    const d = document.createElement("span");
    d.className = "sim-fly";
    d.style.left = `${ra.left + 4}px`; d.style.top = `${ra.top + 4}px`;
    document.body.appendChild(d);
    flying++;
    const dx = rb.right - 14 - (ra.left + 4), dy = rb.top + 6 - (ra.top + 4);
    const anim = d.animate([{ transform: "translate(0,0) scale(1.4)" }, { transform: `translate(${dx}px,${dy}px) scale(.8)` }], { duration: 650, easing: "ease-in-out" });
    anim.onfinish = () => { d.remove(); flying--; };
  }

  function dots(container, pts) {
    container.innerHTML = pts.map((p) => `<i class="d ${p.kind}${p.park ? " park" : p.su ? " su" : ""}${p.pf ? " pf" : ""}${p.inQ ? " pend" : ""}"></i>`).join("");
  }

  function render(now, force) {
    if (!S) return;
    const [tx, ty] = trackerXY();
    const trk = $("sim-trk");
    if (trk) trk.setAttribute("transform", `translate(${tx},${ty})`);
    $("sim-clock").textContent = clock(Math.min(simT, S.end));
    handleFx(now);
    // luistervenster: ring voor het jongste wachtende bericht
    const m = S.pend[S.pend.length - 1], ring = $("sim-ring");
    if (ring) {
      const C = 2 * Math.PI * 16;
      if (m) {
        const pr = Math.min(1, Math.max(0, (simT - m.t) / HEAR_S));
        ring.style.strokeDasharray = `${C * (1 - pr)} ${C}`;
        ring.setAttribute("class", `sim-ring ${m.kind}`);
        $("sim-ringbg").style.opacity = 1;
      } else { ring.style.strokeDasharray = `0 ${C}`; $("sim-ringbg").style.opacity = 0; }
    }
    marks = marks.filter((k) => now - k.t0 < 900);
    const mk = $("sim-mark");
    if (mk) { const k = marks[marks.length - 1]; mk.textContent = k ? k.text : ""; mk.setAttribute("class", `sim-mark ${k ? k.cls : ""}`); }
    const lw = $("sim-listen");
    if (lw) lw.style.opacity = (simT < S.listenUntil && simT >= S.lastListen) || now < listenWall ? 1 : 0;
    const st = $("sim-snrt");
    const still = S.rest ? "in rust" : !S.moving(Math.floor(simT)) ? "staat stil" : "";
    if (st) st.textContent = still ? (S.snr == null ? `${still} · geen ontvangst` : `${still} · SNR ${dB(S.snr)}`) : S.snr == null ? "geen ontvangst" : `SNR ${dB(S.snr)}`;
    // vliegende pakketjes
    const g = $("sim-fxg");
    if (g) {
      fx = fx.filter((f) => now - f.t0 < f.dur);
      g.textContent = "";
      for (const f of fx) {
        const p = (now - f.t0) / f.dur;
        el("circle", { cx: f.x0 + (f.x1 - f.x0) * p, cy: f.y0 + (f.y1 - f.y0) * p, r: 5, class: f.cls, opacity: f.fade ? 1 - p : 1 }, g);
      }
    }
    if (!force && now - lastBoxes < 120) return;
    lastBoxes = now;
    // signaal
    const sig = $("sim-signal");
    sig.innerHTML = `Signaal: <strong>${S.snr == null ? "geen ontvangst" : `SNR ${dB(S.snr)}`}</strong>`
      + (mode === "fifo" ? ` · dekking <strong class="${S.stable ? "okc" : "warn"}">${S.stable ? "stabiel" : "onzeker"}</strong> <span class="muted">(leegmaken vanaf SNR ≥ ${dB(cfg.fifo_snr)})</span>` : "");
    // buffers en wachtrij
    dots($("sim-ftbuf"), S.ftBuf);
    $("sim-ftcount").textContent = `${S.ftBuf.length} / ${FT_BUF}`;
    const npf = S.ftBuf.filter((p) => p.pf).length;
    $("sim-ftnote").textContent = (S.iv ? `Volgend bericht over ${Math.max(0, Math.ceil(S.lastFt + S.iv - simT))} s` : "Geen FastTrack-berichten bij deze snelheid")
      + (mode === "fifo" ? `; ${npf ? `${npf} gemist ${npf === 1 ? "hoofdpunt lift" : "hoofdpunten liften"} mee` : "gemiste punten liften mee met het volgende bericht"}.` : "; de buffer wordt bij elk bericht gewist.");
    if (mode === "classic") {
      dots($("sim-slowbuf"), S.slowBuf);
      $("sim-slownote").textContent = !cfg.slow_log ? "SlowTrack staat uit."
        : `${S.slowBuf.length} gelogd; versturen elke ${fmtDur(cfg.slow_send)} als één L-bericht, uitgedund tot ≈ ${PER_L} punten.`;
    } else {
      dots($("sim-fifo"), S.queue);
      const ce = S.cfgEff;
      $("sim-fifocount").textContent = `${S.queue.length} / ${cfg.fifo_max}`;
      $("sim-fifobar").style.width = `${Math.min(100, 100 * S.queue.length / cfg.fifo_max)}%`;
      const c = S.count();
      $("sim-fifonote").innerHTML = (S.flushing ? `<strong>Leegmaken</strong>, één Q-bericht (≈ ${S.perQ} punten) per ${fmtDur(cfg.fifo_gap)}`
        : S.retryAt > simT && S.queue.length ? `Volgende poging over ${Math.ceil((S.retryAt - simT) / 60)} min`
        : S.queue.length >= ce.fifo_min ? "Wacht op stabiele dekking"
        : S.rest && S.queue.length ? `In rust: leegmaken zodra de dekking stabiel is, ook onder ${ce.fifo_min} punten`
        : `Leegmaken vanaf ${ce.fifo_min} punten` + (ce.fifo_wacht && S.queue.length ? `, of als het oudste ${fmtDur(ce.fifo_wacht)} wacht (nu ${Math.floor((simT - S.queue[0].t) / 60)} min)` : ""))
        + (S.rest && S.queue.length && !S.flushing && Number.isFinite(S.restListenAt) ? ` · in rust: volgende luisterbeurt over ${Math.max(0, Math.ceil((S.restListenAt - simT) / 60))} min` : "")
        + (S.listens ? ` · ${S.listens}× geluisterd` : "")
        + ` · <span class="${c.hour >= cfg.fifo_per_uur ? "warn" : ""}">${c.hour} / ${cfg.fifo_per_uur} doorgegeven</span>`
        + ` · <span class="${c.tries >= 2 * cfg.fifo_per_uur ? "warn" : ""}">${c.tries} / ${2 * cfg.fifo_per_uur} pogingen</span>`
        + (S.capWhy && S.flushing ? ` · <strong class="warn">${S.capWhy === "uur" ? "uurlimiet" : "veiligheidsgrens"} bereikt</strong>` : "")
        + (c.park ? ` · <span class="park">geparkeerd: ${c.park}</span>` : "")
        + (c.gaveUp ? ` · opgegeven: ${c.gaveUp}` : "")
        + (c.evicted ? ` · <span class="warn">${c.evicted} verdrongen</span>` : "");
    }
    // tellers
    const c = S.count();
    const ftMiss = c.sent.ft - c.ok.ft - S.pend.filter((x) => x.kind === "ft").length;
    $("sim-count").innerHTML = `
      <dt id="sim-c-msgs">Berichten</dt><dd>FastTrack ${c.sent.ft} <span class="muted">(${c.ok.ft} ✓, ${ftMiss} ✗)</span>`
        + (mode === "classic" ? ` · L ${c.sent.slow}` : ` · Q ${c.sent.fifo} <span class="muted">(${c.ok.fifo} ✓)</span>`) + `</dd>
      <dt>Zendtijd tracker</dt><dd>≈ ${nf(c.air, 0)} s</dd>
      <dt>Meshbelasting</dt><dd>≈ ${nf(c.mesh, 0)} s <span class="muted">(× ≈ ${REPEATS} herhalingen${c.sent.ack ? ", met de T1F van de server" : ""})</span></dd>
      <dt>Punten gelogd</dt><dd>${c.logged}</dd>
      <dt>Op de server</dt><dd><strong>${c.server}</strong> (${c.pct} %) <span class="muted">· live ${c.live}, ingehaald ${c.late}</span></dd>
      <dt>Uitgedund</dt><dd>${c.thin} <span class="muted">(overbodig op een recht stuk)</span></dd>
      <dt>Verloren</dt><dd>${c.lost}${c.wait ? ` <span class="muted">· nog op de tracker: ${c.wait}</span>` : ""}</dd>`
      + (mode === "fifo" ? `<dt id="sim-c-ack">Serverbevestiging</dt><dd>T1F ${c.sent.ack}× · ${c.acked} punten bevestigd${c.waitSends ? ` · ${c.waitSends}× toch verstuurd na wachten` : ""}${c.restSends ? ` · ${c.restSends}× leeggemaakt in rust` : ""}${c.dup ? ` <span class="muted">· ${c.dup} dubbel ontvangen (server filtert)</span>` : ""}</dd>` : "");
    if (force || now - lastTrack > (S.points.length > 800 ? 600 : 150)) { lastTrack = now; drawTrack(); }
    // gebeurtenissen (nieuwste bovenaan)
    const ol = $("sim-log");
    for (; logShown < S.log.length; logShown++) {
      const e = S.log[logShown], li = document.createElement("li");
      li.className = e.cls || "";
      li.textContent = `${clock(e.t).slice(0, 5)} ${e.text}`;
      ol.prepend(li);
    }
    while (ol.children.length > 400) ol.lastChild.remove();
  }

  function drawTrack() {
    const svg = $("sim-track");
    svg.textContent = "";
    const xt = (t) => X0 + (X1 - X0) * t / S.end;
    if (S.dzT) el("rect", { x: xt(S.dzT[0]), y: 2, width: xt(S.dzT[1]) - xt(S.dzT[0]), height: 70, class: "sim-dz", rx: 4 }, svg);
    if (sc.st1 > sc.st0) el("rect", { x: xt(sc.st0 * S.end), y: 74, width: xt(sc.st1 * S.end) - xt(sc.st0 * S.end), height: 10, class: "sim-stop", rx: 3 }, svg);
    const lanes = { live: 16, late: 36, lost: 56 };
    [["live", "live"], ["late", "ingehaald"], ["lost", "weg"]].forEach(([k, txt]) => { el("text", { x: 2, y: lanes[k] + 4, class: "sim-lane" }, svg).textContent = txt; });
    el("line", { x1: X0, y1: 80, x2: xt(Math.min(simT, S.end)), y2: 80, class: "sim-prog" }, svg);
    let path = "";
    S.points.filter((p) => p.srv).sort((a, b) => a.t - b.t).forEach((p, i) => { path += `${i ? "L" : "M"}${xt(p.t).toFixed(1)} 16 `; });
    if (path) el("path", { d: path, class: "sim-line" }, svg);
    const frag = document.createDocumentFragment();
    for (const p of S.points) {
      const x = xt(p.t);
      if (p.srv) el("circle", { cx: x, cy: p.srvLate ? lanes.late : lanes.live, r: 3.5, class: p.srvLate ? "tp late" : "tp live" }, frag);
      else if (p.thin) el("circle", { cx: x, cy: lanes.lost, r: 2, class: "tp thin" }, frag);
      else if (p.st === "gone") el("path", { d: `M${x - 3} ${lanes.lost - 3} l6 6 m0 -6 l-6 6`, class: "tp lost" }, frag);
      else el("circle", { cx: x, cy: lanes.late, r: 3, class: p.park ? "tp park" : "tp wait" }, frag);
    }
    svg.appendChild(frag);
  }

  function frame(now) {
    if (pane.hidden) { loopOn = false; return; }
    const dt = Math.min(0.25, (now - lastWall) / 1000);
    lastWall = now;
    if (playing && S && !S.done) {
      simT = Math.min(S.end + 1, simT + dt * rate);
      while (!S.done && S.t <= Math.floor(simT)) S.step();
      if (S.done) { playing = false; simT = S.end; $("sim-play").textContent = "Afspelen"; render(now, true); }
    }
    render(now);
    requestAnimationFrame(frame);
  }
  function startLoop() { if (loopOn) return; loopOn = true; lastWall = performance.now(); requestAnimationFrame(frame); }

  // ---- bediening -------------------------------------------------------------------------
  $("sim-mode").querySelectorAll("button").forEach((b) => b.addEventListener("click", () => setMode(b.dataset.v)));
  $("sim-rate").querySelectorAll("button").forEach((b) => b.addEventListener("click", () => setRate(Number(b.dataset.v))));
  $("sim-play").addEventListener("click", () => {
    if (S.done) reset();
    playing = !playing;
    $("sim-play").textContent = playing ? "Pauze" : "Afspelen";
    startLoop();
  });
  $("sim-reset").addEventListener("click", reset);
  ["sim-speed", "sim-dur", "sim-dz0", "sim-dzl", "sim-miss", "sim-st0", "sim-stl", "sim-nb"].forEach((id) => $(id).addEventListener("change", () => {
    pane.querySelectorAll("[data-preset]").forEach((x) => x.classList.remove("on"));
    reset();
  }));
  const PRESETS = { std: { speed: 50, dur: 60, dz0: 30, dzl: 40, st0: 0, stl: 0, miss: 10 }, long: { speed: 50, dur: 300, dz0: 20, dzl: 60, st0: 0, stl: 0, miss: 10 },
    // lang stilstaan: SlowTrack elke 5 min, inhalen pas vanaf 20 punten, zodat "toch versturen na" zichtbaar wordt
    // door een gat rijden en punten verzamelen, dan urenlang parkeren met goed bereik, op het einde weer wegrijden
    stand: { speed: 50, dur: 360, dz0: 25, dzl: 35, st0: 6, stl: 91, miss: 10, over: { slow_log: 300, fifo_min: 20 } } };
  pane.querySelectorAll("[data-preset]").forEach((b) => b.addEventListener("click", () => {
    const p = PRESETS[b.dataset.preset];
    $("sim-speed").value = p.speed; $("sim-dur").value = p.dur; $("sim-dz0").value = p.dz0; $("sim-dzl").value = p.dzl;
    $("sim-st0").value = p.st0; $("sim-stl").value = p.stl; $("sim-miss").value = p.miss;
    scOver = p.over || null;
    pane.querySelectorAll("[data-preset]").forEach((x) => x.classList.toggle("on", x === b));
    reset();
  }));
  // uitleg: het bijhorende onderdeel van de animatie aanwijzen
  pane.querySelectorAll(".sim-show").forEach((b) => b.addEventListener("click", () => {
    const t = pane.querySelector(b.dataset.sel);
    if (!t || t.offsetParent === null) {
      if (b.dataset.sel === "#sim-fifobox" || b.dataset.sel === "#sim-fifonote" || b.dataset.sel === "#sim-c-ack") setMode("fifo");
    }
    const tgt = pane.querySelector(b.dataset.sel);
    if (!tgt) return;
    const box = tgt.closest(".sim-box, .sim-stripwrap, .sim-signal") || tgt;
    box.scrollIntoView({ behavior: matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth", block: "center" });
    box.classList.remove("sim-hl"); void box.offsetWidth; box.classList.add("sim-hl");
    setTimeout(() => box.classList.remove("sim-hl"), 2600);
  }));

  function onShow() {
    const fresh = readSettings();
    const changed = !cfg || JSON.stringify(fresh) !== JSON.stringify(cfg);
    cfg = fresh;
    if (changed && cfg.nameBytes) $("sim-nb").value = cfg.nameBytes;
    $("sim-src").textContent = cfg.source;
    const kvNow = window.MTDev && MTDev.kv && MTDev.kv.pubkey;
    if (changed) setMode(cfg.fifoOnTracker ? cfg.track_mode : kvNow ? "classic" : (mode || "fifo"));
    startLoop();
  }
  new MutationObserver(() => { if (!pane.hidden) onShow(); else playing = false; }).observe(pane, { attributes: true, attributeFilter: ["hidden"] });
  setRate(60);
  if (!pane.hidden) onShow();

  // testhaak
  window.MTSim = {
    get state() { return S; }, get mode() { return mode; }, get cfg() { return cfg; },
    counts: () => S && S.count(),
    finish: () => { while (S && !S.done) S.step(); simT = S.end; render(performance.now(), true); },
  };
})();
