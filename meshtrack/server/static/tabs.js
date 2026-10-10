/* Tabbladen, los van common.js (ook bruikbaar in /offline en /tracker).
   - MTTabs.init(strook, opties): role=tablist/tab/tabpanel, aria-selected, aria-controls,
     pijltjes links/rechts, Home/End; alleen de actieve tab zit in de tabvolgorde.
   - MTTabs.scroller(strook): schuifbare tabstrook (gsm) met een vervaging aan de kant waar nog
     tabbladen staan; de actieve tab schuift vanzelf in beeld.
   Elke .pagetabs en .tabs krijgt automatisch de scroller (behalve .st-tabs: die regelt het zelf).
   MT.tabs (common.js) blijft bestaan voor de pagina's met #hash-tabbladen; deze helper is voor de rest. */
const MTTabs = (() => {
  const reduced = () => { try { return matchMedia("(prefers-reduced-motion: reduce)").matches; } catch (_) { return false; } };

  function scroller(bar) {
    if (!bar || bar.dataset.tabscroll) return;
    bar.dataset.tabscroll = "1";
    const fade = () => {
      const max = bar.scrollWidth - bar.clientWidth;
      bar.classList.toggle("more-r", max > 2 && bar.scrollLeft < max - 2);
      bar.classList.toggle("more-l", max > 2 && bar.scrollLeft > 2);
    };
    const toActive = () => {
      const b = bar.querySelector('[aria-selected="true"], .on');
      if (b && bar.scrollWidth > bar.clientWidth + 2) {
        const left = b.offsetLeft - bar.offsetLeft - (bar.clientWidth - b.offsetWidth) / 2;
        bar.scrollTo({ left, behavior: reduced() ? "auto" : "smooth" });
      }
      fade();
    };
    bar.addEventListener("scroll", fade, { passive: true });
    if (window.ResizeObserver) new ResizeObserver(fade).observe(bar);
    // een andere tab actief (door MT.tabs, MTTabs.init of eigen code): in beeld schuiven
    new MutationObserver((recs) => { if (recs.some((r) => r.target !== bar)) toActive(); }).observe(bar, { subtree: true, attributes: true, attributeFilter: ["class", "aria-selected", "hidden"] });
    toActive();
  }

  /* opties: onShow(naam), allowed(naam) -> bool, key = data-attribuut met de naam ("tab"),
     pane(naam) -> element (standaard #pane-<naam>), initial = naam om mee te starten. */
  function init(bar, { onShow = null, allowed = null, key = "tab", pane = null, initial = null } = {}) {
    const attr = "data-" + key;
    const btns = [...bar.querySelectorAll(`[${attr}]`)];
    const paneOf = pane || ((n) => document.getElementById("pane-" + n));
    const name = (b) => b.getAttribute(attr);
    const ok = (b) => !b.hidden && (!allowed || allowed(name(b)));
    bar.setAttribute("role", "tablist");
    btns.forEach((b) => {
      const p = paneOf(name(b));
      b.setAttribute("role", "tab");
      if (b.tagName === "BUTTON" && !b.hasAttribute("type")) b.type = "button";
      b.id = b.id || `tab-${key}-${name(b)}`;
      if (p) {
        p.id = p.id || `pane-${key}-${name(b)}`;
        p.setAttribute("role", "tabpanel");
        p.setAttribute("aria-labelledby", b.id);
        b.setAttribute("aria-controls", p.id);
      }
      if (allowed && !allowed(name(b))) b.hidden = true;
      b.addEventListener("click", () => show(name(b)));
      b.addEventListener("keydown", (e) => {
        const vis = btns.filter(ok), i = vis.indexOf(b);
        let n = null;
        if (e.key === "ArrowRight") n = vis[(i + 1) % vis.length];
        if (e.key === "ArrowLeft") n = vis[(i - 1 + vis.length) % vis.length];
        if (e.key === "Home") n = vis[0];
        if (e.key === "End") n = vis[vis.length - 1];
        if (n) { e.preventDefault(); show(name(n)); n.focus(); }
      });
    });
    function show(n) {
      btns.forEach((b) => {
        const on = name(b) === n;
        b.classList.toggle("on", on);
        b.setAttribute("aria-selected", String(on));
        b.tabIndex = on ? 0 : -1;
        const p = paneOf(name(b));
        if (p) p.hidden = !on;
      });
      if (onShow) onShow(n);
    }
    const first = btns.find((b) => name(b) === initial && ok(b)) || btns.find((b) => b.classList.contains("on") && ok(b)) || btns.find(ok);
    if (first) show(name(first));
    return show;
  }

  const auto = () => document.querySelectorAll(".pagetabs:not(.st-tabs), .tabs:not(.st-tabs)").forEach(scroller);
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", auto); else auto();
  return { init, scroller };
})();
