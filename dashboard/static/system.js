"use strict";
// Systemseite: zeichnet system.json (vom Sammler auf dem Host) als Skizze der Infrastruktur.
// Nur lesen – auf dieser Seite gibt es keine Knöpfe und keine Anfragen, die etwas verändern.
// Alle Texte aus den Daten werden mit textContent gesetzt (nie als HTML).

const $ = (id) => document.getElementById(id);
const SVG = "http://www.w3.org/2000/svg";
const VERALTET_NACH_SEKUNDEN = 120;
const NEU_LADEN_SEKUNDEN = 30;

const FARBE = {
  gruen: { linie: "#3fb950", fuellung: "#10261a" },
  gelb: { linie: "#d29922", fuellung: "#2e2410" },
  rot: { linie: "#f85149", fuellung: "#3a1214" },
  grau: { linie: "#6e7681", fuellung: "#0d1117" },
};
const SYMBOL = { gruen: "✓", gelb: "!", rot: "✕", grau: "–" };
const STUFE_TEXT = { gruen: "grün – läuft", gelb: "gelb – stimmt etwas nicht", rot: "rot – aus oder nicht erreichbar",
  grau: "grau – noch nicht eingerichtet (kein Fehler)" };

let daten = null;        // zuletzt erfolgreich geladene system.json
let ladeFehler = "";     // Text, falls das Dashboard gerade nicht antwortet

// ---------------------------------------------------------------- Zeit (immer mit Zeitzone)
function zeit(ts) {
  return new Date(ts * 1000).toLocaleString("de-CH", { weekday: "short", day: "2-digit", month: "2-digit",
    hour: "2-digit", minute: "2-digit", timeZoneName: "short" });
}

function uhrzeitIn(ts, zone) {
  const stadt = zone.split("/").pop().replace(/_/g, " ");
  return new Date(ts * 1000).toLocaleTimeString("de-CH", { hour: "2-digit", minute: "2-digit", timeZone: zone }) + " " + stadt;
}

function relativ(ts) {
  const s = Math.round(ts - Date.now() / 1000);
  const a = Math.abs(s);
  const text = a < 90 ? `${a} s` : a < 5400 ? `${Math.round(a / 60)} min` : a < 172800 ? `${Math.round(a / 3600)} h` : `${Math.round(a / 86400)} d`;
  return s < 0 ? `vor ${text}` : `in ${text}`;
}

function zeitText(zeile) {
  let text = `${zeit(zeile.z)} (${relativ(zeile.z)})`;
  const hier = Intl.DateTimeFormat().resolvedOptions().timeZone;
  if (zeile.zone && zeile.zone !== hier) text += ` · ${uhrzeitIn(zeile.z, zeile.zone)}`;
  return text;
}

// ---------------------------------------------------------------- Skizze
// Feste Plätze im Koordinatensystem der Skizze (360 breit). Die Bots stehen untereinander unter "bot";
// ihre Anzahl kommt aus der Registry, deshalb wird ihr Platz ausgerechnet.
const B = 156;   // Breite eines halben Kastens
const LINKS = 16, RECHTS = 188;

function plaetze(kaesten) {
  const p = {
    iphone: { x: LINKS, y: 8, w: B, h: 50 },
    laptop: { x: RECHTS, y: 8, w: B, h: 50 },
    tailscale: { x: 50, y: 88, w: 260, h: 50, rund: true },
    server: { x: LINKS, y: 168, w: 328, h: 54 },
    "c-ntfy": { x: LINKS + 6, y: 270, w: B - 12, h: 50 },
    "c-dashboard": { x: RECHTS + 6, y: 270, w: B - 12, h: 50 },
    "c-bot": { x: LINKS + 6, y: 330, w: B - 12, h: 50 },
    "c-ib-gateway": { x: RECHTS + 6, y: 330, w: B - 12, h: 50 },
    "tws-api": { x: RECHTS + 12, y: 416, w: B - 24, h: 44, rund: true },
    ibkr: { x: RECHTS, y: 490, w: B, h: 58 },
  };
  let y = 416;
  kaesten.filter((k) => k.id.startsWith("bot-")).forEach((k) => { p[k.id] = { x: LINKS, y, w: B, h: 64 }; y += 76; });
  const unten = Math.max(y, 560) + 30;
  p.backup = { x: LINKS, y: unten, w: B, h: 54 };
  p.totmann = { x: RECHTS, y: unten, w: B, h: 54 };
  p._hoehe = unten + 54 + 8;
  p._absicherung = unten - 10;
  return p;
}

const VERBINDUNGEN = [["iphone", "tailscale"], ["laptop", "tailscale"], ["tailscale", "server"],
  ["c-ib-gateway", "tws-api"], ["tws-api", "ibkr"]];

function svgEl(tag, attrs = {}, text = "") {
  const e = document.createElementNS(SVG, tag);
  for (const [k, v] of Object.entries(attrs)) e.setAttribute(k, v);
  if (text) e.textContent = text;
  return e;
}

function kuerzen(text, platz, schrift) {
  const max = Math.floor(platz / (schrift * 0.56));
  text = String(text ?? "");
  return text.length > max ? text.slice(0, max - 1) + "…" : text;
}

// Linie von unten-Mitte des einen zu oben-Mitte des anderen Kastens (mit einem Knick)
function linie(svg, a, b) {
  const x1 = a.x + a.w / 2, y1 = a.y + a.h, x2 = b.x + b.w / 2, y2 = b.y;
  const mitte = (y1 + y2) / 2;
  svg.appendChild(svgEl("path", { d: `M${x1},${y1} V${mitte} H${x2} V${y2}`, fill: "none", stroke: "#30363d", "stroke-width": 2 }));
}

function modusKlasse(modus) {
  const m = String(modus || "").toUpperCase();
  return m === "PAPER" ? "paper" : m === "ECHT" || m === "LIVE" ? "echt" : "probe";
}

function zeichneKasten(svg, k, p) {
  const f = FARBE[k.stufe] || FARBE.grau;
  const a = svgEl("a", { href: `#k-${k.id}`, "aria-label": `${k.titel}: ${STUFE_TEXT[k.stufe] || k.stufe}` });
  a.appendChild(svgEl("rect", { x: p.x, y: p.y, width: p.w, height: p.h, rx: p.rund ? p.h / 2 : 10, fill: f.fuellung,
    stroke: f.linie, "stroke-width": k.stufe === "rot" ? 2.5 : 1.5, "stroke-dasharray": k.stufe === "grau" ? "5 4" : "none" }));
  const links = p.x + (p.rund ? 18 : 10);
  a.appendChild(svgEl("circle", { cx: links + 8, cy: p.y + 19, r: 8, fill: k.stufe === "grau" ? "none" : f.linie,
    stroke: f.linie, "stroke-dasharray": k.stufe === "grau" ? "3 2" : "none" }));
  a.appendChild(svgEl("text", { x: links + 8, y: p.y + 23, "text-anchor": "middle", "font-size": 11, "font-weight": 700,
    fill: k.stufe === "grau" ? f.linie : "#0d1117" }, SYMBOL[k.stufe] || "?"));
  let titelPlatz = p.w - (links - p.x) - 30;
  if (k.modus) {
    const breite = String(k.modus).length * 7 + 12;
    titelPlatz -= breite + 4;
    const farbe = { paper: ["#3d2a00", "#d29922"], echt: ["#f85149", "#ffffff"], probe: ["#12304d", "#58a6ff"] }[modusKlasse(k.modus)];
    a.appendChild(svgEl("rect", { x: p.x + p.w - breite - 8, y: p.y + 9, width: breite, height: 18, rx: 9, fill: farbe[0] }));
    a.appendChild(svgEl("text", { x: p.x + p.w - breite / 2 - 8, y: p.y + 22, "text-anchor": "middle", "font-size": 10,
      "font-weight": 700, fill: farbe[1] }, k.modus));
  }
  a.appendChild(svgEl("text", { x: links + 22, y: p.y + 23, "font-size": 13, "font-weight": 600, fill: "#e6edf3" },
    kuerzen(k.titel, titelPlatz, 13)));
  a.appendChild(svgEl("text", { x: links, y: p.y + 41, "font-size": 11.5, fill: k.stufe === "rot" ? "#ff9b95" : "#adbac7" },
    kuerzen(k.kurz, p.w - (links - p.x) * 2, 11.5)));
  if (k.letzter_lauf && p.h >= 60) {
    const ergebnis = k.lauf_ok === false ? "gescheitert" : "ok";
    a.appendChild(svgEl("text", { x: links, y: p.y + 57, "font-size": 11, fill: "#8b949e" },
      kuerzen(`Lauf ${relativ(k.letzter_lauf)} · ${ergebnis}`, p.w - 20, 11)));
  }
  svg.appendChild(a);
}

function zeichneSkizze(kaesten) {
  const svg = $("skizze");
  svg.innerHTML = "";
  const p = plaetze(kaesten);
  svg.setAttribute("viewBox", `0 0 360 ${p._hoehe}`);

  // Rahmen "Docker" um die vier Container, Server darüber
  const rahmen = { x: LINKS - 4, y: 248, w: 336, h: 142 };
  linie(svg, p.server, rahmen);
  svg.appendChild(svgEl("rect", { x: rahmen.x, y: rahmen.y, width: rahmen.w, height: rahmen.h, rx: 12, fill: "none",
    stroke: "#30363d", "stroke-dasharray": "4 4" }));
  svg.appendChild(svgEl("text", { x: rahmen.x + 10, y: rahmen.y + 15, "font-size": 10.5, fill: "#8b949e" }, "Docker-Container"));

  for (const [a, b] of VERBINDUNGEN) if (p[a] && p[b]) linie(svg, p[a], p[b]);
  // Bots hängen am Container "bot": eine Linie nach unten an der linken Seite
  const bots = kaesten.filter((k) => k.id.startsWith("bot-"));
  if (bots.length) {
    const x = p["c-bot"].x + p["c-bot"].w / 2, letzter = p[bots[bots.length - 1].id];
    svg.appendChild(svgEl("path", { d: `M${x},${p["c-bot"].y + p["c-bot"].h} V${letzter.y}`, stroke: "#30363d", "stroke-width": 2 }));
  }
  svg.appendChild(svgEl("text", { x: LINKS, y: p._absicherung, "font-size": 10.5, fill: "#8b949e" },
    "Absicherung ausserhalb des Servers"));

  // Kästen, die die Skizze (noch) nicht kennt, erscheinen trotzdem unten in den Einzelheiten
  for (const k of kaesten) if (p[k.id]) zeichneKasten(svg, k, p[k.id]);
}

// ---------------------------------------------------------------- Einzelheiten (Karten unter der Skizze)
function el(tag, attrs = {}, text = "") {
  const e = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) e.setAttribute(k, v);
  if (text !== "") e.textContent = text;
  return e;
}

function karte(k) {
  const art = el("article", { id: `k-${k.id}`, class: `karte ${k.stufe}` });
  const h = el("h3");
  h.appendChild(el("span", { class: "punkt" }, SYMBOL[k.stufe] || "?"));
  h.appendChild(el("span", {}, k.titel));
  if (k.modus) h.appendChild(el("span", { class: `modus ${modusKlasse(k.modus)}` }, k.modus));
  art.appendChild(h);
  art.appendChild(el("p", { class: "stufe" }, STUFE_TEXT[k.stufe] || k.stufe));
  if (k.gruende && k.gruende.length) {
    const ul = el("ul", { class: "gruende" });
    k.gruende.forEach((g) => ul.appendChild(el("li", {}, g)));
    art.appendChild(ul);
  }
  const dl = el("dl", { class: "werte" });
  (k.zeilen || []).forEach((z) => {
    dl.appendChild(el("dt", {}, z.t));
    dl.appendChild(el("dd", {}, z.z != null ? zeitText(z) : z.w ?? "–"));
  });
  art.appendChild(dl);
  return art;
}

function zusammenfassung(zaehler) {
  const grau = zaehler.grau ? ` · ${zaehler.grau} noch nicht eingerichtet` : "";
  if (!zaehler.rot && !zaehler.gelb) return "Alles läuft" + grau;
  const teile = [];
  if (zaehler.rot) teile.push(`${zaehler.rot} rot`);
  if (zaehler.gelb) teile.push(`${zaehler.gelb} gelb`);
  return teile.join(" · ") + grau;
}

// ---------------------------------------------------------------- Laden und Alter prüfen
function zeigen() {
  const kaesten = daten.kaesten || [];
  zeichneSkizze(kaesten);
  const details = $("details");
  details.innerHTML = "";
  kaesten.forEach((k) => details.appendChild(karte(k)));
  $("zusammenfassung").textContent = zusammenfassung(daten.zaehler || {});
  const warnung = $("paper-warnung");
  warnung.hidden = !(daten.warnungen && daten.warnungen.length);
  warnung.textContent = (daten.warnungen || []).join(" ");
  $("stand").textContent = `${zeit(daten.erzeugt)} · ${daten.erzeugt_utc}`;
}

function alterPruefen() {
  const banner = $("veraltet");
  if (!daten) {
    document.body.classList.add("veraltet");
    banner.hidden = false;
    banner.textContent = ladeFehler || "Noch keine Daten.";
    return;
  }
  const alter = Date.now() / 1000 - daten.erzeugt;
  const alt = alter > VERALTET_NACH_SEKUNDEN;
  document.body.classList.toggle("veraltet", alt);
  banner.hidden = !alt && !ladeFehler;
  const utc = new Date(daten.erzeugt * 1000).toLocaleTimeString("de-CH", { hour: "2-digit", minute: "2-digit", timeZone: "UTC" });
  banner.textContent = alt ? `Daten veraltet, Stand ${utc} UTC (${relativ(daten.erzeugt)}). ${ladeFehler}`.trim() : ladeFehler;
}

async function laden() {
  try {
    const antwort = await fetch("/api/system", { cache: "no-store" });
    const neu = await antwort.json();
    if (neu.fehlt) {
      ladeFehler = neu.grund || "Keine Daten.";
    } else {
      daten = neu;
      ladeFehler = "";
      zeigen();
    }
  } catch (e) {
    ladeFehler = "Dashboard nicht erreichbar (Tailscale an?).";
  }
  alterPruefen();
}

laden();
setInterval(laden, NEU_LADEN_SEKUNDEN * 1000);
setInterval(alterPruefen, 10 * 1000);
