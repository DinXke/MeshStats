/* Mijn kanalen: de kanalen die je mag lezen; bij niveau "sleutel" ook sleutel en QR-code
   (meshcore://channel/add) om het kanaal op een eigen companion te zetten. */
(async function () {
  const $ = (id) => document.getElementById(id);
  await MT.initHeader("/kanalen");
  const chans = await MT.api("/api/channels/mine");
  $("chans").innerHTML = chans.map((c) => `<div class="titem"><div class="body">
      <div><strong>${MT.esc(c.name)}</strong> <span class="pill${c.level === "sleutel" ? " ok" : ""}">${c.level === "sleutel" ? "kaart + sleutel" : "kaart"}</span></div>
      <div class="muted small">${c.trackers} tracker${c.trackers === 1 ? "" : "s"}${c.region ? " · regio " + MT.esc(c.region) : ""}
        · <a href="/?kanaal=${c.id}">op de kaart</a></div>
      <div class="qrslot" id="qr-${c.id}" hidden></div></div>
      <div class="actions">${c.secret ? `<button type="button" data-qr="${c.id}" aria-expanded="false">Sleutel en QR-code</button>` : ""}</div></div>`).join("")
    || '<div class="empty">Je mag nog geen kanalen lezen. Vraag een beheerder om je groep rechten op een kanaal te geven.</div>';
  document.querySelectorAll("[data-qr]").forEach((b) => b.addEventListener("click", () => {
    const c = chans.find((x) => x.id === Number(b.dataset.qr));
    const box = $(`qr-${c.id}`);
    const open = box.hidden;
    box.hidden = !open;
    b.setAttribute("aria-expanded", String(open));
    if (!open || box.innerHTML) return;
    const qr = qrcode(0, "M");
    qr.addData(`meshcore://channel/add?name=${encodeURIComponent(c.name)}&secret=${c.secret}`);
    qr.make();
    box.innerHTML = `<div class="row" style="align-items:flex-start;margin-top:10px"><div class="qrbox">${qr.createSvgTag({ cellSize: 5, margin: 2 })}</div>
      <div style="flex:1;min-width:200px"><p>Scan met de MeshCore-app of de offline-app (tabblad Kanalen) om dit kanaal toe te voegen.</p>
        <div class="small muted">Naam</div><code class="mono">${MT.esc(c.name)}</code>
        <div class="small muted" style="margin-top:6px">Sleutel</div><code class="mono">${MT.esc(c.secret.match(/.{1,4}/g).join(" "))}</code>
        <p class="help">Wie deze code of sleutel heeft, kan het kanaal lezen en erop sturen. Deel hem alleen met wie mag.</p></div></div>`;
  }));
})();
