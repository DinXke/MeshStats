/* MeshTrack-iconen: eigen SVG's (24x24, wit op de trackerkleur).
   Gebruik: MTIcons.svg("ambulance"), MTIcons.list() voor een kiezer. */
(function () {
  "use strict";
  const W = 'fill="#fff"';
  const S = 'fill="none" stroke="#fff" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"';
  // basisvormen
  const wheels = (a, b) => `<circle cx="${a}" cy="18" r="2.2" ${W}/><circle cx="${b}" cy="18" r="2.2" ${W}/>`;
  const truck = `<path ${W} d="M2 8h12v9H2zM14 11h4l3 3v3h-7z"/>` + wheels(6, 17);
  const van = `<path ${W} d="M2 7h13l4 4h2v6H2z"/>` + wheels(6, 16);
  const car = `<path ${W} d="M4 11l2-4h10l3 4h2v6H3v-6z"/>` + wheels(7, 17);

  const I = {
    // ---- hulpdiensten
    ambulance:   ["Ziekenwagen", "Hulpdiensten", van + `<path fill="#e11d48" d="M7 9h2v2h2v2H9v2H7v-2H5v-2h2z"/>`],
    mug:         ["MUG / PIT", "Hulpdiensten", car + `<path fill="#e11d48" d="M10 8h2v1.5h1.5v2H12V13h-2v-1.5H8.5v-2H10z"/>`],
    police:      ["Politie", "Hulpdiensten", car + `<rect x="9" y="3.5" width="6" height="2.5" rx="1" fill="#60a5fa"/>`],
    police_van:  ["Politiecombi", "Hulpdiensten", van + `<rect x="5" y="4" width="6" height="2.2" rx="1" fill="#60a5fa"/>`],
    fire_pump:   ["Autopomp", "Hulpdiensten", truck + `<circle cx="8" cy="12.5" r="2.4" fill="#ef4444"/><circle cx="8" cy="12.5" r=".9" ${W}/>`],
    ladder:      ["Ladderwagen", "Hulpdiensten", truck + `<path ${S} stroke="#ef4444" d="M3 7l12-4M4 9l12-4"/>`],
    tanker:      ["Tankwagen", "Hulpdiensten", `<rect x="2" y="8" width="13" height="8" rx="4" ${W}/><path ${W} d="M15 11h3l3 3v3h-6z"/>` + wheels(6, 17)],
    rescue:      ["Hulpverleningsvoertuig", "Hulpdiensten", truck + `<path fill="#f59e0b" d="M5 10h6v1.5H5zM5 13h6v1.5H5z"/>`],
    command:     ["Commandopost", "Hulpdiensten", van + `<path ${S} d="M17 7V2M17 2l3 1.5L17 5"/>`],
    firefighter: ["Brandweerman", "Hulpdiensten", `<path ${W} d="M5 12a7 7 0 0114 0h2v2H3v-2zM11 4h2v4h-2z"/><circle cx="12" cy="18" r="3" ${W}/>`],
    medic:       ["Hulpverlener", "Hulpdiensten", `<circle cx="12" cy="6" r="3" ${W}/><path ${W} d="M6 21v-5a6 6 0 0112 0v5z"/><path fill="#e11d48" d="M11 14h2v1.5h1.5v2H13V19h-2v-1.5H9.5v-2H11z"/>`],
    heli:        ["Helikopter", "Hulpdiensten", `<path ${S} d="M3 5h18M12 5v3"/><path ${W} d="M5 10h10a4 4 0 010 8H9l-4-4z"/><path ${S} d="M15 14h6M8 20h8"/>`],
    boat:        ["Boot", "Hulpdiensten", `<path ${W} d="M3 14h18l-3 5H6z"/><path ${W} d="M7 14V9h7l3 5z"/><path ${S} d="M10 9V5"/>`],
    // ---- voertuigen
    car:         ["Wagen", "Voertuigen", car],
    van:         ["Bestelwagen", "Voertuigen", van],
    truck:       ["Vrachtwagen", "Voertuigen", truck],
    bus:         ["Bus", "Voertuigen", `<rect x="3" y="5" width="18" height="12" rx="2" ${W}/>` + wheels(7, 17) + `<rect x="5" y="7" width="14" height="4" rx="1" fill="#0006"/>`],
    tow:         ["Takelwagen", "Voertuigen", truck + `<path ${S} d="M14 10l6-6M20 4v6"/>`],
    tractor:     ["Tractor", "Voertuigen", `<path ${W} d="M4 9h7l1 4h7v4H4z"/><circle cx="7" cy="17" r="3.2" ${W}/><circle cx="18" cy="18" r="2" ${W}/>`],
    moto:        ["Motor", "Voertuigen", `<circle cx="5" cy="16" r="3" ${S}/><circle cx="19" cy="16" r="3" ${S}/><path ${S} d="M5 16l5-6h4l5 6M14 10l-2-4h3"/>`],
    bike:        ["Fiets", "Voertuigen", `<circle cx="5.5" cy="16" r="3.5" ${S}/><circle cx="18.5" cy="16" r="3.5" ${S}/><path ${S} d="M5.5 16l4-7h6l3 7M9.5 9L12 16h3.5M14 6h3"/>`],
    drone:       ["Drone", "Voertuigen", `<rect x="9" y="9" width="6" height="6" rx="1.5" ${W}/><path ${S} d="M9 9L6 6M15 9l3-3M9 15l-3 3M15 15l3 3"/><circle cx="5" cy="5" r="2.5" ${S}/><circle cx="19" cy="5" r="2.5" ${S}/><circle cx="5" cy="19" r="2.5" ${S}/><circle cx="19" cy="19" r="2.5" ${S}/>`],
    // ---- materieel
    container:   ["Container", "Materieel", `<rect x="2" y="6" width="20" height="12" rx="1" ${W}/><path stroke="#0005" stroke-width="1.5" d="M6 8v8M10 8v8M14 8v8M18 8v8"/>`],
    trailer:     ["Aanhangwagen", "Materieel", `<rect x="5" y="7" width="15" height="9" rx="1" ${W}/><path ${S} d="M5 13H2"/><circle cx="12" cy="18" r="2.2" ${W}/>`],
    generator:   ["Generator", "Materieel", `<rect x="3" y="6" width="18" height="12" rx="2" ${W}/><path fill="#f59e0b" d="M13 7l-5 6h3l-1 4 5-6h-3z"/>`],
    lighttower:  ["Lichtmast", "Materieel", `<path ${S} d="M12 8v12M8 21h8"/><rect x="6" y="3" width="12" height="5" rx="1" ${W}/><path ${S} d="M4 2l2 1M20 2l-2 1"/>`],
    pump:        ["Pomp", "Materieel", `<circle cx="10" cy="13" r="6" ${W}/><circle cx="10" cy="13" r="2" fill="#0006"/><path ${S} d="M16 13h5M10 7V3h5"/>`],
    hose:        ["Slangenhaspel", "Materieel", `<circle cx="12" cy="12" r="8" ${S}/><circle cx="12" cy="12" r="4.5" ${S}/><circle cx="12" cy="12" r="1.5" ${W}/>`],
    tent:        ["Tent / PMA", "Materieel", `<path ${W} d="M12 4l9 15H3z"/><path fill="#0006" d="M12 10l3 9H9z"/>`],
    radio:       ["Radio / repeater", "Materieel", `<path ${S} d="M12 10v11M8 21h8"/><circle cx="12" cy="8" r="2" ${W}/><path ${S} d="M7.5 3.5a7 7 0 000 9M16.5 3.5a7 7 0 010 9"/>`],
    box:         ["Pakket", "Materieel", `<path ${W} d="M12 2l9 5v10l-9 5-9-5V7z"/><path stroke="#0005" stroke-width="1.5" fill="none" d="M3 7l9 5 9-5M12 12v10"/>`],
    toolbox:     ["Gereedschap", "Materieel", `<rect x="3" y="8" width="18" height="11" rx="2" ${W}/><path ${S} d="M9 8V5h6v3"/><rect x="10.5" y="11" width="3" height="3" fill="#0006"/>`],
    // ---- personen en dieren
    person:      ["Persoon", "Personen & dieren", `<circle cx="12" cy="5" r="3" ${W}/><path ${W} d="M7 21v-6a5 5 0 0110 0v6z"/>`],
    walker:      ["Wandelaar", "Personen & dieren", `<circle cx="13" cy="4" r="2.5" ${W}/><path ${S} d="M10 21l2-6 3 3v3M12 15l1-6 4 3M13 9l-4 2-1 3"/>`],
    group:       ["Groep", "Personen & dieren", `<circle cx="8" cy="7" r="2.5" ${W}/><circle cx="16" cy="7" r="2.5" ${W}/><path ${W} d="M3 19v-4a5 5 0 019-1 5 5 0 019 1v4z"/>`],
    dog:         ["Hond", "Personen & dieren", `<path ${W} d="M5 9l2-5 2 3h4l2-3 2 5v4a5 5 0 01-5 5h-2a5 5 0 01-5-5z"/><circle cx="10" cy="11" r="1" fill="#0007"/><circle cx="14" cy="11" r="1" fill="#0007"/><path fill="#0007" d="M11 14h2l-1 1.5z"/>`],
    horse:       ["Paard", "Personen & dieren", `<path ${W} d="M8 21l1-6-4-2 2-6 4-3 1 3 3 1 3 4-2 2-2-2-1 3v6z"/>`],
    cat:         ["Kat", "Personen & dieren", `<path ${W} d="M5 20V9L4 3l5 4h6l5-4-1 6v11z"/><circle cx="9.5" cy="12" r="1" fill="#0007"/><circle cx="14.5" cy="12" r="1" fill="#0007"/>`],
    // ---- grappig
    duck:        ["Badeend", "Grappig", `<circle cx="15" cy="8" r="4" ${W}/><path ${W} d="M3 13h9a5 5 0 009 1c0 4-3 7-9 7s-9-3-9-8z"/><path fill="#f59e0b" d="M18.5 8.5L22 9.5l-3.5 1z"/><circle cx="16" cy="7" r=".9" fill="#0008"/>`],
    rocket:      ["Raket", "Grappig", `<path ${W} d="M12 2c4 3 5 8 4 13H8C7 10 8 5 12 2z"/><circle cx="12" cy="9" r="2" fill="#0006"/><path fill="#f97316" d="M10 16h4l-2 6z"/><path ${W} d="M8 12l-3 4 3 1zM16 12l3 4-3 1z"/>`],
    ghost:       ["Spook", "Grappig", `<path ${W} d="M5 21V10a7 7 0 0114 0v11l-2.5-2-2.3 2-2.2-2-2.2 2-2.3-2z"/><circle cx="9.5" cy="10" r="1.3" fill="#0007"/><circle cx="14.5" cy="10" r="1.3" fill="#0007"/>`],
    crown:       ["Kroon", "Grappig", `<path ${W} d="M3 18l1-11 5 5 3-7 3 7 5-5 1 11z"/><rect x="3" y="18.5" width="18" height="2.5" rx="1" ${W}/>`],
    pizza:       ["Pizza", "Grappig", `<path ${W} d="M12 22L3 5a17 17 0 0118 0z"/><circle cx="10" cy="9" r="1.5" fill="#dc2626"/><circle cx="14" cy="11" r="1.5" fill="#dc2626"/><circle cx="12" cy="15" r="1.3" fill="#dc2626"/>`],
    alien:       ["Alien", "Grappig", `<path ${W} d="M12 2c5 0 8 4 8 8 0 6-5 12-8 12S4 16 4 10c0-4 3-8 8-8z"/><path fill="#0008" d="M6.5 10c2 0 4 1 4.5 3-2 0-4-1-4.5-3zM17.5 10c-2 0-4 1-4.5 3 2 0 4-1 4.5-3z"/>`],
    // ---- symbolen
    star:        ["Ster", "Symbolen", `<path ${W} d="M12 2l3 6.5 7 .8-5.2 4.8 1.5 7L12 17.6 5.7 21l1.5-7L2 9.3l7-.8z"/>`],
    heart:       ["Hart", "Symbolen", `<path ${W} d="M12 21s-8-5-8-11a4.5 4.5 0 018-3 4.5 4.5 0 018 3c0 6-8 11-8 11z"/>`],
    flag:        ["Vlag", "Symbolen", `<path ${S} d="M5 21V3"/><path ${W} d="M6 4h12l-3 4 3 4H6z"/>`],
    home:        ["Thuis / kazerne", "Symbolen", `<path ${W} d="M3 11l9-8 9 8v10h-6v-6H9v6H3z"/>`],
    warning:     ["Waarschuwing", "Symbolen", `<path ${W} d="M12 3l10 18H2z"/><path fill="#0008" d="M11 9h2v6h-2zM11 16.5h2v2h-2z"/>`],
    pin:         ["Speld", "Symbolen", `<path ${W} d="M12 22s-7-7-7-12a7 7 0 0114 0c0 5-7 12-7 12z"/><circle cx="12" cy="10" r="2.5" fill="#0006"/>`],
  };

  window.MTIcons = {
    svg(id) {
      const i = I[id];
      return i ? `<svg viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg" aria-hidden="true">${i[2]}</svg>` : "";
    },
    label(id) { return I[id] ? I[id][0] : ""; },
    list() { return Object.entries(I).map(([id, v]) => ({ id, label: v[0], group: v[1] })); },
    /* Kiezer in een container: knoppen per groep; onPick(id) bij klik. */
    picker(el, current, color, onPick) {
      const groups = {};
      for (const it of this.list()) (groups[it.group] = groups[it.group] || []).push(it);
      el.innerHTML = '<button type="button" class="ico-btn' + (current ? "" : " on") + '" data-ico="" title="Geen icoon"><span class="ico-sw" style="background:' + color + '"></span></button>' +
        Object.entries(groups).map(([g, items]) => `<div class="ico-group">${g}</div>` + items.map((it) =>
          `<button type="button" class="ico-btn${it.id === current ? " on" : ""}" data-ico="${it.id}" title="${it.label}">` +
          `<span class="ico-sw" style="background:${color}">${this.svg(it.id)}</span></button>`).join("")).join("");
      el.querySelectorAll(".ico-btn").forEach((b) => b.addEventListener("click", () => {
        el.querySelectorAll(".ico-btn").forEach((x) => x.classList.toggle("on", x === b));
        onPick(b.dataset.ico);
      }));
    },
  };
})();
