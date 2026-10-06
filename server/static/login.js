document.getElementById("f").addEventListener("submit", async (e) => {
  e.preventDefault();
  const m = document.getElementById("m");
  m.textContent = "";
  const r = await fetch("/api/login", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ user: document.getElementById("u").value, password: document.getElementById("p").value }),
  });
  if (r.ok) { location.href = "/"; return; }
  const j = await r.json().catch(() => ({}));
  m.textContent = j.detail || "inloggen mislukt";
});
