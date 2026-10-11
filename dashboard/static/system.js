"use strict";
// Reiter "System": system.json als Skizze der Infrastruktur, darunter je Kasten die Einzelheiten.
// Farben kommen aus style.css (Klassen sk-…), damit die Skizze im hellen und dunklen Modus stimmt.

// ---------------------------------------------------------------- Plätze in der Skizze
// Koordinaten der Skizze: 360 breit. Oben fest: Geräte, Tunnel, Server.
// Im Docker-Rahmen zwei Spalten:
//   links  je Bot sein Container und direkt darunter der Bot (Reihenfolge und Anzahl aus der Registry)
//   rechts die Infrastruktur: ntfy, dashboard, ib-gateway und dessen TWS-API
// Darunter das IBKR-Konto (ausserhalb des Servers) und die Absicherung.
const B = 156;   // Breite eines halben Kastens
const LINKS = 16, RECHTS = 188;
const INNEN = 6; // Abstand der Kästen vom Docker-Rahmen

function plaetze(kaesten) {
  const p = {
    iphone: { x: LINKS, y: 8, w: B, h: 50 },
    laptop: { x: RECHTS, y: 8, w: B, h: 50 },
    tailscale: { x: 50, y: 88, w: 260, h: 50, rund: true },
    server: { x: LINKS, y: 168, w: 328, h: 54 },
    "c-ntfy": { x: RECHTS + INNEN, y: 270, w: B - 2 * INNEN, h: 50 },
    "c-dashboard": { x: RECHTS + INNEN, y: 330, w: B - 2 * INNEN, h: 50 },
    "c-ib-gateway": { x: RECHTS + INNEN, y: 390, w: B - 2 * INNEN, h: 50 },
    "tws-api": { x: RECHTS + INNEN, y: 456, w: B - 2 * INNEN, h: 44, rund: true },
  };
  const linien = [["iphone", "tailscale"], ["laptop", "tailscale"], ["tailscale", "server"],
    ["c-ib-gateway", "tws-api"]];
  const vorhanden = new Set(kaesten.map((k) => k.id));
  let y = 270;
  for (const bot of kaesten.filter((k) => k.id.startsWith("bot-"))) {
    const c = bot.container ? `c-${bot.container}` : null;
    if (c && vorhanden.has(c) && !p[c]) {
      p[c] = { x: LINKS + INNEN, y, w: B - 2 * INNEN, h: 50 };
      y += 60;
    }
    p[bot.id] = { x: LINKS + INNEN, y, w: B - 2 * INNEN, h: 64 };
    if (c && p[c]) linien.push([c, bot.id]);
    y += 64 + 14;
  }
  const rahmenUnten = Math.max(y - 14, 500) + 12;
  p._rahmen = { x: LINKS - 4, y: 248, w: 336, h: rahmenUnten - 248 };
  p.ibkr = { x: RECHTS, y: rahmenUnten + 30, w: B, h: 58 };
  linien.push(["tws-api", "ibkr"]);
  const unten = p.ibkr.y + p.ibkr.h + 40;
  p.backup = { x: LINKS, y: unten, w: B, h: 54 };
  p.totmann = { x: RECHTS, y: unten, w: B, h: 54 };
  p._hoehe = unten + 54 + 8;
  p._absicherung = unten - 10;
  p._linien = linien;
  return p;
}

// ---------------------------------------------------------------- Zeichnen
// Text kürzen, damit er in den Kasten passt. Ein Zeichen ist im Mittel etwa eine halbe Schriftgrösse breit.
function kuerzen(text, platz, schrift, fett = false) {
  const max = Math.floor(platz / (schrift * (fett ? 0.55 : 0.5)));
  text = String(text ?? "");
  return text.length > max ? text.slice(0, max - 1) + "…" : text;
}

// Linie von unten-Mitte des einen zu oben-Mitte des anderen Kastens (mit einem Knick)
function linie(svg, a, b) {
  const x1 = a.x + a.w / 2, y1 = a.y + a.h, x2 = b.x + b.w / 2, y2 = b.y;
  const mitte = (y1 + y2) / 2;
  svg.appendChild(svgEl("path", { d: `M${x1},${y1} V${mitte} H${x2} V${y2}`, class: "sk-linie" }));
}

// Bot-Kästen führen zum Bots-Reiter, alle anderen zur Karte unter der Skizze
function ziel(k) {
  return k.id.startsWith("bot-") ? `#bots/${k.id}` : `#system/k-${k.id}`;
}

function zeichneKasten(svg, k, p) {
  const a = svgEl("a", { href: ziel(k), class: `sk-kasten ${k.stufe}`, "aria-label": `${k.titel}: ${STUFE_TEXT[k.stufe] || k.stufe}` });
  a.appendChild(svgEl("rect", { x: p.x, y: p.y, width: p.w, height: p.h, rx: p.rund ? p.h / 2 : 10, class: "sk-flaeche" }));
  const links = p.x + (p.rund ? 18 : 10);
  a.appendChild(svgEl("circle", { cx: links + 8, cy: p.y + 19, r: 8, class: "sk-punkt" }));
  a.appendChild(svgEl("text", { x: links + 8, y: p.y + 23, "text-anchor": "middle", class: "sk-symbol" }, SYMBOL[k.stufe] || "?"));
  a.appendChild(svgEl("text", { x: links + 22, y: p.y + 23, class: "sk-titel" },
    kuerzen(k.kurztitel || k.titel, p.w - (links - p.x) - 30, 13, true)));
  a.appendChild(svgEl("text", { x: links, y: p.y + 41, class: "sk-kurz" }, kuerzen(k.kurz, p.w - (links - p.x) * 2, 11.5)));
  // Bots: dritte Zeile mit dem letzten Lauf, rechts davon der Modus (PAPER, VORSCHLAG, …) als Abzeichen
  let modusBreite = 0;
  if (k.modus && p.h >= 60) {
    modusBreite = String(k.modus).length * 5.8 + 10;
    const art = modusKlasse(k.modus);
    a.appendChild(svgEl("rect", { x: p.x + p.w - modusBreite - 8, y: p.y + 45, width: modusBreite, height: 16, rx: 8,
      class: `sk-modus ${art}` }));
    a.appendChild(svgEl("text", { x: p.x + p.w - modusBreite / 2 - 8, y: p.y + 57, "text-anchor": "middle",
      class: `sk-modus-text ${art}` }, k.modus));
  }
  if (k.letzter_lauf && p.h >= 60) {
    const ergebnis = k.lauf_ok === false ? "✕" : "✓";
    a.appendChild(svgEl("text", { x: links, y: p.y + 57, class: "sk-klein" },
      kuerzen(`${relativ(k.letzter_lauf)} ${ergebnis}`, p.w - (links - p.x) - modusBreite - 14, 11)));
  }
  svg.appendChild(a);
}

function zeichneSkizze(kaesten) {
  const svg = $("skizze");
  svg.innerHTML = "";
  const p = plaetze(kaesten);
  svg.setAttribute("viewBox", `0 0 360 ${p._hoehe}`);

  // Rahmen "Docker" um die Container, Server darüber
  const rahmen = p._rahmen;
  linie(svg, p.server, rahmen);
  svg.appendChild(svgEl("rect", { x: rahmen.x, y: rahmen.y, width: rahmen.w, height: rahmen.h, rx: 12, class: "sk-rahmen" }));
  svg.appendChild(svgEl("text", { x: rahmen.x + 10, y: rahmen.y + 15, class: "sk-beschriftung" }, "Docker-Container"));
  for (const [a, b] of p._linien) if (p[a] && p[b]) linie(svg, p[a], p[b]);
  svg.appendChild(svgEl("text", { x: LINKS, y: p._absicherung, class: "sk-beschriftung" }, "Absicherung ausserhalb des Servers"));

  // Kästen, die die Skizze (noch) nicht kennt, erscheinen trotzdem unten in den Einzelheiten
  for (const k of kaesten) if (p[k.id]) zeichneKasten(svg, k, p[k.id]);
}

// ---------------------------------------------------------------- Einzelheiten unter der Skizze
function systemKarte(k) {
  const art = el("article", { id: `k-${k.id}`, class: `system-karte karte ${k.stufe}` });
  const h = el("h3", { class: "status" });
  h.appendChild(punkt(k.stufe));
  h.appendChild(el("span", {}, k.titel));
  art.appendChild(h);
  // grau hat verschiedene Gründe ("noch nicht eingerichtet", "offline – kein Fehler") – den echten zeigen
  art.appendChild(el("p", { class: "klein gedaempft" }, k.stufe === "grau" ? `grau – ${k.kurz}` : STUFE_TEXT[k.stufe] || k.stufe));
  if (k.gruende && k.gruende.length) {
    const ul = el("ul", { class: "gruende" });
    k.gruende.forEach((g) => ul.appendChild(el("li", {}, g)));
    art.appendChild(ul);
  }
  art.appendChild(werteListe(k.zeilen));
  return art;
}

function zeigeSystem(daten) {
  const kaesten = daten.kaesten || [];
  zeichneSkizze(kaesten);
  const details = $("system-details");
  details.innerHTML = "";
  kaesten.filter((k) => !k.id.startsWith("bot-")).forEach((k) => details.appendChild(systemKarte(k)));
}
