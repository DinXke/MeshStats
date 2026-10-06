/* Gedeelde hulpjes voor kaart- en beheerpagina. */
const MT = {
  async api(path, opts = {}) {
    const r = await fetch(path, {
      ...opts,
      headers: opts.body ? { "Content-Type": "application/json" } : {},
      body: opts.body ? JSON.stringify(opts.body) : undefined,
    });
    if (r.status === 401) { location.href = "/login"; throw new Error("niet ingelogd"); }
    const j = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(j.detail || r.statusText);
    return j;
  },

  esc(s) {
    return String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  },

  ago(ts) {
    if (!ts) return "nooit";
    const s = Math.max(0, Math.round(Date.now() / 1000 - ts));
    if (s < 60) return `${s} s geleden`;
    if (s < 3600) return `${Math.round(s / 60)} min geleden`;
    if (s < 86400) return `${Math.round(s / 3600)} u geleden`;
    return `${Math.round(s / 86400)} d geleden`;
  },

  STATE: { M: "rijdt/stapt", S: "stilgevallen", H: "heartbeat", N: "geen GPS-fix", E: "SOS", B: "modus", P: "handmatig verstuurd" },
  MODE: { c: "companion", t: "tracker" },

  TOWNS: [
    ["Hasselt", 50.9307, 5.3378], ["Genk", 50.9650, 5.5000], ["Sint-Truiden", 50.8160, 5.1866],
    ["Tongeren", 50.7806, 5.4646], ["Beringen", 51.0490, 5.2260], ["Leuven", 50.8798, 4.7005],
    ["Brussel", 50.8503, 4.3517], ["Antwerpen", 51.2194, 4.4025], ["Gent", 51.0543, 3.7174],
    ["Brugge", 51.2093, 3.2247], ["Luik", 50.6326, 5.5797], ["Namen", 50.4674, 4.8720],
    ["Maastricht", 50.8514, 5.6910], ["Eindhoven", 51.4416, 5.4697], ["Utrecht", 52.0907, 5.1214],
    ["Rotterdam", 51.9244, 4.4777], ["Amsterdam", 52.3676, 4.9041], ["Luxemburg", 49.6116, 6.1319],
  ],

  meshPill(el, m) {
    el.className = "pill " + (m.connected ? "ok" : "bad");
    el.textContent = m.connected ? `mesh: ${m.name || "verbonden"}` : "mesh: niet verbonden";
    el.title = m.connected ? `${m.host}:${m.port}` : (m.last_error || "");
  },

  live(onMsg) {
    let delay = 1000;
    const open = () => {
      const ws = new WebSocket((location.protocol === "https:" ? "wss://" : "ws://") + location.host + "/ws");
      ws.onopen = () => { delay = 1000; };
      ws.onmessage = (e) => onMsg(JSON.parse(e.data));
      ws.onclose = (e) => {
        if (e.code === 4401) { location.href = "/login"; return; }
        setTimeout(open, delay); delay = Math.min(delay * 2, 30000);
      };
    };
    open();
  },
};
