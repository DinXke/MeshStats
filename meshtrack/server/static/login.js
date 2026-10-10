document.getElementById("f").addEventListener("submit", async (e) => {
  e.preventDefault();
  const m = document.getElementById("m");
  m.textContent = "";
  const btn = e.submitter || document.querySelector("#f button[type=submit]");
  if (btn) btn.disabled = true;
  let r;
  try {
    r = await fetch("/api/login", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ user: document.getElementById("u").value, password: document.getElementById("p").value }),
    });
  } catch (_) {
    m.textContent = "Geen verbinding met de server. Controleer je internet en probeer opnieuw.";
    if (btn) btn.disabled = false;
    return;
  }
  if (btn) btn.disabled = false;
  if (r.ok) {   // terug naar de pagina die om de login vroeg (alleen eigen paden); server 1.5 geeft {ok, next}
    const j = await r.json().catch(() => ({}));
    const next = j.next || new URLSearchParams(location.search).get("next") || "/";
    location.href = /^\/(?![\/\\])/.test(next) ? next : "/";
    return;
  }
  const j = await r.json().catch(() => ({}));
  // foutmelding bij het formulier (role=alert leest ze voor); het wachtwoordveld krijgt de focus terug
  const d = r.status === 401 ? "Gebruikersnaam of wachtwoord klopt niet." : (j.detail || "Aanmelden mislukt.");
  m.textContent = d.charAt(0).toUpperCase() + d.slice(1) + (/[.!?]$/.test(d) ? "" : ".");
  document.getElementById("p").setAttribute("aria-invalid", "true");
  document.getElementById("p").select();
});
// Doorgestuurd omdat een pagina niet mag (zie guard in main.py): zeggen waarom.
if (new URLSearchParams(location.search).has("geen_toegang")) {
  document.getElementById("m").textContent = "Je hebt geen toegang tot die pagina. Meld je aan met een ander account.";
}
