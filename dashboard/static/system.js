"use strict";
// Reiter "System": die Infrastruktur als Baum (Vorlage 04) und die Detailseite eines Kastens (Vorlage 05).
// Farben kommen aus style.css (Klassen sk-…), damit der Baum im hellen und dunklen Modus stimmt.

// ---------------------------------------------------------------- Plätze im Baum
// Koordinaten: 360 breit. Oben Geräte, Tunnel, Server. Im Docker-Rahmen die Container zwei nebeneinander
// (zuerst die festen vier, dann die Container aus bots.toml). Darunter links die Bots, rechts TWS-API und
// IBKR-Konto, ganz unten die Absicherung ausserhalb des Servers.
const B = 156;   // Breite eines halben Kastens
const LINKS = 16, RECHTS = 188;
const FESTE_CONTAINER = ["c-ntfy", "c-dashboard", "c-bot", "c-ib-gateway"];

function plaetze(kaesten) {
  const p = {
    iphone: { x: LINKS, y: 10, w: B, h: 56 },
    laptop: { x: RECHTS, y: 10, w: B, h: 56 },
    tailscale: { x: 52, y: 96, w: 256, h: 54, rund: true },
    server: { x: LINKS, y: 176, w: 328, h: 56 },
  };
  const container = FESTE_CONTAINER.concat(kaesten.map((k) => k.id).filter((id) => id.startsWith("c-") && !FESTE_CONTAINER.includes(id)));
  let y = 290;
  container.forEach((id, i) => {
    p[id] = { x: (i % 2 ? RECHTS : LINKS) + 8, y: y + Math.floor(i / 2) * 64, w: B - 16, h: 54 };
  });
  const rahmenUnten = y + Math.ceil(container.length / 2) * 64 + 4;
  p._rahmen = { x: LINKS - 4, y: 256, w: 336, h: rahmenUnten - 256 };

  // unter dem Rahmen: links die Bots untereinander, rechts TWS-API und IBKR-Konto
  y = rahmenUnten + 30;
  const bots = kaesten.filter((k) => k.id.startsWith("bot-"));
  bots.forEach((k, i) => { p[k.id] = { x: LINKS, y: y + i * 96, w: B, h: 82 }; });
  p["tws-api"] = { x: RECHTS, y, w: B, h: 82 };
  p.ibkr = { x: RECHTS, y: y + 96, w: B, h: 82 };
  const unten = Math.max(y + Math.max(bots.length, 2) * 96, 0) + 30;
  p._absicherung = unten;
  p.backup = { x: LINKS, y: unten + 16, w: B, h: 56 };
  p.totmann = { x: RECHTS, y: unten + 16, w: B, h: 56 };
  p._hoehe = unten + 16 + 56 + 14;
  p._bots = bots.map((k) => k.id);
  return p;
}

// ---------------------------------------------------------------- Zeichnen
// Text kürzen, damit er in den Kasten passt. Ein Zeichen ist im Mittel etwa eine halbe Schriftgrösse breit.
function kuerzen(text, platz, schrift, fett = false) {
  const max = Math.floor(platz / (schrift * (fett ? 0.56 : 0.5)));
  text = String(text ?? "");
  return text.length > max ? text.slice(0, max - 1) + "…" : text;
}

function strecke(svg, x1, y1, x2, y2, klasse = "") {
  const mitte = (y1 + y2) / 2;
  svg.appendChild(svgEl("path", { d: `M${x1},${y1} V${mitte} H${x2} V${y2}`, class: `sk-linie ${klasse}` }));
}

// Linie von unten-Mitte zu oben-Mitte (mit einem Knick)
function verbinde(svg, a, b, klasse = "") {
  strecke(svg, a.x + a.w / 2, a.y + a.h, b.x + b.w / 2, b.y, klasse);
}

// Wohin ein Kasten führt: Bots auf ihre Bot-Seite, Geräte nirgends hin, alles andere auf die eigene Detailseite
function ziel(k) {
  if (k.id.startsWith("bot-")) return `#bots/${k.id}`;
  if (k.id === "iphone" || k.id === "laptop") return null;
  return `#system/${k.id}`;
}

function zeichneKasten(svg, k, p) {
  const link = ziel(k);
  const g = svgEl(link ? "a" : "g", { class: `sk-kasten ${k.stufe}`, "aria-label": `${k.titel}: ${STUFE_TEXT[k.stufe] || k.stufe}` });
  if (link) g.setAttribute("href", link);
  g.appendChild(svgEl("rect", { x: p.x, y: p.y, width: p.w, height: p.h, rx: p.rund ? p.h / 2 : 14, class: "sk-flaeche" }));
  const links = p.x + (p.rund ? 22 : 12);
  g.appendChild(svgEl("circle", { cx: links + 9, cy: p.y + 21, r: 9.5, class: "sk-punkt" }));
  g.appendChild(svgEl("text", { x: links + 9, y: p.y + 25, "text-anchor": "middle", class: "sk-symbol" }, SYMBOL[k.stufe] || "?"));
  const pfeil = link && p.w > 200;  // Pfeil nur an breiten Kästen (Tunnel, Server) – halbe brauchen den Platz für Text
  const pfeilPlatz = pfeil ? 16 : 0;
  g.appendChild(svgEl("text", { x: links + 25, y: p.y + 26, class: "sk-titel" },
    kuerzen(k.kurztitel || k.titel, p.w - (links - p.x) - 30 - pfeilPlatz, 13.5, true)));
  g.appendChild(svgEl("text", { x: links + 25, y: p.y + 43, class: "sk-kurz" },
    kuerzen(k.kurz, p.w - (links - p.x) - 30 - pfeilPlatz, 11.5)));
  if (pfeil) g.appendChild(svgEl("text", { x: p.x + p.w - 14, y: p.y + p.h / 2 + 5, class: "sk-pfeil" }, "›"));
  if (k.modus && p.h >= 70) {
    const breite = String(k.modus).length * 6.2 + 14;
    const art = modusKlasse(k.modus);
    g.appendChild(svgEl("rect", { x: links + 25, y: p.y + 54, width: breite, height: 18, rx: 9, class: `sk-modus ${art}` }));
    g.appendChild(svgEl("text", { x: links + 25 + breite / 2, y: p.y + 67, "text-anchor": "middle", class: `sk-modus-text ${art}` }, k.modus));
  }
  svg.appendChild(g);
}

function rasterHintergrund(svg, hoehe) {
  const muster = svgEl("pattern", { id: "sk-punkte", width: 16, height: 16, patternUnits: "userSpaceOnUse" });
  muster.appendChild(svgEl("circle", { cx: 2, cy: 2, r: 1, class: "sk-raster" }));
  const defs = svgEl("defs");
  defs.appendChild(muster);
  svg.appendChild(defs);
  svg.appendChild(svgEl("rect", { x: 0, y: 0, width: 360, height: hoehe, fill: "url(#sk-punkte)" }));
}

function zeichneSkizze(kaesten) {
  const svg = $("skizze");
  svg.innerHTML = "";
  const p = plaetze(kaesten);
  svg.setAttribute("viewBox", `0 0 360 ${p._hoehe}`);
  rasterHintergrund(svg, p._hoehe);

  // Geräte -> Tunnel (gemeinsamer Bügel), Tunnel -> Server gestrichelt grün
  const t = p.tailscale;
  for (const geraet of [p.iphone, p.laptop]) strecke(svg, geraet.x + geraet.w / 2, geraet.y + geraet.h, t.x + t.w / 2, t.y);
  svg.appendChild(svgEl("line", { x1: t.x + t.w / 2, y1: t.y + t.h, x2: t.x + t.w / 2, y2: p.server.y, class: "sk-linie tunnel" }));
  verbinde(svg, p.server, p._rahmen);

  const rahmen = p._rahmen;
  svg.appendChild(svgEl("rect", { x: rahmen.x, y: rahmen.y, width: rahmen.w, height: rahmen.h, rx: 16, class: "sk-rahmen" }));
  svg.appendChild(svgEl("text", { x: rahmen.x + 14, y: rahmen.y + 22, class: "sk-beschriftung" }, "DOCKER-CONTAINER"));

  // Vom Rahmen nach unten: links zu den Bots (untereinander), rechts TWS-API -> IBKR
  const unten = rahmen.y + rahmen.h;
  if (p._bots.length) {
    const erster = p[p._bots[0]];
    strecke(svg, erster.x + erster.w / 2, unten, erster.x + erster.w / 2, erster.y);
    for (let i = 1; i < p._bots.length; i++) verbinde(svg, p[p._bots[i - 1]], p[p._bots[i]]);
  }
  strecke(svg, p["tws-api"].x + p["tws-api"].w / 2, unten, p["tws-api"].x + p["tws-api"].w / 2, p["tws-api"].y);
  verbinde(svg, p["tws-api"], p.ibkr);

  svg.appendChild(svgEl("text", { x: LINKS, y: p._absicherung, class: "sk-beschriftung" }, "ABSICHERUNG AUSSERHALB"));
  svg.appendChild(svgEl("line", { x1: 196, x2: 344, y1: p._absicherung - 4, y2: p._absicherung - 4, class: "sk-linie" }));

  // Kästen, die der Baum (noch) nicht kennt, erscheinen trotzdem auf ihrer Detailseite
  for (const k of kaesten) if (p[k.id]) zeichneKasten(svg, k, p[k.id]);
}

function zeigeSystem(daten) {
  zeichneSkizze(daten.kaesten || []);
}

// ---------------------------------------------------------------- Detailseite eines Kastens (z.B. Server)
function zeigeSystemteil(id, daten) {
  const seite = $("system-seite");
  seite.innerHTML = "";
  const k = kastenMitId(id);
  if (!k) {
    seite.appendChild(el("p", { class: "leer" }, "Diesen Teil gibt es nicht."));
    return;
  }
  seite.appendChild(el("div", { class: `status-kreis ${k.stufe}`, "aria-hidden": "true" }, SYMBOL[k.stufe] || "?"));
  seite.appendChild(el("h1", { class: "teil-titel" }, k.titel));
  const pille = el("div", { class: "teil-pille" });
  pille.appendChild(el("span", { class: `pille ${k.stufe}` }, k.stufe === "grau" ? `grau – ${k.kurz}` : STUFE_TEXT[k.stufe]));
  seite.appendChild(pille);
  const gruende = gruendeListe(k);
  if (gruende) seite.appendChild(gruende);
  if (k.kacheln) seite.appendChild(teilKacheln(k.kacheln));
  seite.appendChild(el("p", { class: "abschnitt" }, "Details"));
  // Was oben schon als Kachel steht (CPU, RAM, Platte), steht nicht noch einmal in der Liste
  const kachelTitel = (k.kacheln || []).map((x) => x.t);
  seite.appendChild(detailListe((k.zeilen || []).filter((z) => !kachelTitel.some((t) => z.t.startsWith(t)))));
  if (id === "server") seite.appendChild(containerListe(daten));
}

// Drei Kacheln mit kleinem Balken (CPU, RAM, Platte). Ab 80 % wird der Balken gelb, ab 95 % rot.
function teilKacheln(kacheln) {
  const reihe = el("div", { class: "kachel-reihe drei" });
  kacheln.forEach((k) => {
    const kachel = el("div", { class: "kachel" });
    kachel.appendChild(el("span", { class: "beschriftung" }, k.t));
    kachel.appendChild(el("span", { class: "kachel-wert" }, k.w));
    const anteil = k.anteil ?? 0;
    const balken = el("span", { class: `balken ${anteil > 0.95 ? "rot" : anteil > 0.8 ? "gelb" : ""}` });
    const fuellung = el("i");
    fuellung.style.width = `${Math.max(Math.min(anteil, 1) * 100, anteil > 0 ? 3 : 0)}%`;
    balken.appendChild(fuellung);
    kachel.appendChild(balken);
    kachel.appendChild(el("span", { class: "klein gedaempft" }, k.unter));
    reihe.appendChild(kachel);
  });
  return reihe;
}

function containerListe(daten) {
  const box = el("div");
  box.appendChild(el("p", { class: "abschnitt" }, "Docker-Container"));
  const karte = el("div", { class: "detail-liste" });
  (daten.kaesten || []).filter((k) => k.id.startsWith("c-")).forEach((k) => {
    const zeile = el("a", { class: "container-zeile", href: `#system/${k.id}` });
    zeile.appendChild(punkt(k.stufe));
    const mitte = el("span", { class: "mitte" });
    mitte.appendChild(el("b", {}, k.titel));
    mitte.appendChild(el("span", { class: "klein gedaempft" }, k.kurz));
    zeile.appendChild(mitte);
    zeile.appendChild(el("span", { class: "pfeil" }, "›"));
    karte.appendChild(zeile);
  });
  box.appendChild(karte);
  return box;
}
