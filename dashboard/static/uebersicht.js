"use strict";
// Reiter "Übersicht" (Vorlage 01): in drei Sekunden wissen, ob man genauer hinschauen muss.
// Oben die Wertkarte von Bot C mit Kurve gegen SPY (Daten aus app.js: cUebersicht), darunter drei Kacheln,
// die Sprungkarten zu System und Bots, die grössten Bewegungen und die Absicherung.

function zeigeUebersicht(daten, status) {
  const c = status ? cUebersicht(status) : null;
  zeigeWertkarte(c);
  zeigeKacheln(c);
  zeigeSprungkarten(daten);
  zeigeBewegungen(c, daten);
  zeigeAbsicherung((daten.kaesten || []).filter((k) => k.id === "backup" || k.id === "totmann"));
}

// ---------------------------------------------------------------- Wertkarte
function zeigeWertkarte(c) {
  $("wert-titel").textContent = c ? `Gesamtwert ${c.name}` : "Gesamtwert";
  $("wert-gesamt").textContent = c ? geld(c.wert) : "–";
  const chip = $("wert-chip");
  chip.textContent = c ? prozent(c.rendite) : "–";
  chip.className = `chip ${c ? farbKlasse(c.rendite) : ""}`;
  $("wert-gewinn").textContent = c ? geldMitVorzeichen(c.gewinn) : "";
  $("wert-gewinn").className = c ? farbKlasse(c.gewinn) : "";
  zeichneKurve($("kurve"), c ? c.kurve : null, c ? c.spy : null);
  zeigeLegende(c);
}

function zeigeLegende(c) {
  const legende = $("kurven-legende");
  legende.innerHTML = "";
  $("kurven-datum").textContent = c && c.kurve ? tagMitWochentag(c.kurve.days[c.kurve.days.length - 1]) : "";
  if (!c || !c.kurve) return;
  const eintraege = [[c.name, c.kurve, "gruen"], ["SPY", c.spy, "blau"]];
  for (const [name, reihe, farbe] of eintraege) {
    if (!reihe) continue;
    const teil = el("span");
    teil.appendChild(el("i", { class: `punkt-klein ${farbe === "blau" ? "" : "gruen"}`, style: farbe === "blau" ? "background: var(--akzent)" : "" }));
    teil.appendChild(el("b", {}, `${name} ${prozent(reihe.values[reihe.values.length - 1] - 1, 2)}`));
    legende.appendChild(teil);
  }
}

// "2026-10-21" -> "Mi 21.10."
function tagMitWochentag(tag) {
  const d = new Date(`${tag}T12:00:00`);
  return `${d.toLocaleDateString("de-CH", { weekday: "short" }).replace(".", "")} ${tag.slice(8, 10)}.${tag.slice(5, 7)}.`;
}

// Gefüllte Kurve für Bot C, Linie für SPY. Beide Reihen sind auf 1.0 = Start normiert.
function zeichneKurve(svg, reihe, spy) {
  const breite = Math.max(svg.clientWidth || 320, 200), hoehe = 170;
  svg.setAttribute("viewBox", `0 0 ${breite} ${hoehe}`);
  svg.innerHTML = "";
  const achse = $("kurven-achse");
  achse.innerHTML = "";
  if (!reihe || reihe.values.length < 2) {
    svg.appendChild(svgEl("text", { x: breite / 2, y: hoehe / 2, "text-anchor": "middle", class: "k-leer" },
      "Noch keine Kurve – ab dem 2. Handelstag."));
    return;
  }
  const alle = reihe.values.concat(spy ? spy.values : [], [1]);
  const rand = (Math.max(...alle) - Math.min(...alle)) * 0.12 || 0.005;
  const lo = Math.min(...alle) - rand, hi = Math.max(...alle) + rand;
  const y = (v) => (hi - v) / (hi - lo) * hoehe;
  const x = (i, n) => 2 + i / (n - 1) * (breite - 8);
  const punkte = (werte) => werte.map((v, i) => [x(i, werte.length), y(v)]);

  const verlauf = svgEl("linearGradient", { id: "kurve-verlauf", x1: 0, y1: 0, x2: 0, y2: 1 });
  verlauf.appendChild(svgEl("stop", { offset: "0", style: "stop-color: var(--gruen); stop-opacity: 0.35" }));
  verlauf.appendChild(svgEl("stop", { offset: "1", style: "stop-color: var(--gruen); stop-opacity: 0" }));
  const defs = svgEl("defs");
  defs.appendChild(verlauf);
  svg.appendChild(defs);

  const c = punkte(reihe.values);
  const linie = c.map(([px, py]) => `${px.toFixed(1)},${py.toFixed(1)}`).join(" ");
  svg.appendChild(svgEl("path", { class: "k-flaeche", d: `M${linie.split(" ").join(" L")} L${c[c.length - 1][0]},${hoehe} L${c[0][0]},${hoehe} Z` }));
  svg.appendChild(svgEl("line", { class: "k-null", x1: 0, x2: breite, y1: y(1), y2: y(1) }));
  svg.appendChild(svgEl("line", { class: "k-heute", x1: c[c.length - 1][0], x2: c[c.length - 1][0], y1: 0, y2: hoehe }));
  if (spy && spy.values.length > 1) {
    const s = punkte(spy.values);
    svg.appendChild(svgEl("polyline", { class: "k-linie-spy", points: s.map(([px, py]) => `${px.toFixed(1)},${py.toFixed(1)}`).join(" ") }));
    svg.appendChild(svgEl("circle", { class: "k-punkt-spy", cx: s[s.length - 1][0], cy: s[s.length - 1][1], r: 4.5 }));
  }
  svg.appendChild(svgEl("polyline", { class: "k-linie-c", points: linie }));
  svg.appendChild(svgEl("circle", { class: "k-punkt-c", cx: c[c.length - 1][0], cy: c[c.length - 1][1], r: 4.5 }));

  const tage = reihe.days;
  for (const tag of [tage[0], tage[Math.floor((tage.length - 1) / 2)]]) achse.appendChild(el("span", {}, `${tag.slice(8, 10)}.${tag.slice(5, 7)}.`));
  achse.appendChild(el("span", {}, "heute"));
}

// ---------------------------------------------------------------- Kacheln
function zeigeKacheln(c) {
  $("kachel-investiert").textContent = c && c.anteil != null ? `${(c.anteil * 100).toFixed(1)} %` : "–";
  $("balken-investiert").style.width = c && c.anteil != null ? `${Math.min(c.anteil, 1) * 100}%` : "0";
  $("kachel-cash").textContent = c && c.cash != null ? zahl(c.cash) : "–";
  $("kachel-cash-unter").textContent = c ? "USD frei" : "";
  const lauf = c ? c.letzterLauf : null;
  $("kachel-lauf").textContent = lauf ? wochentagZeit(lauf.ts) : "–";
  $("kachel-lauf-unter").textContent = lauf ? `${zoneKuerzel(lauf.ts)} · ${lauf.ok ? "ok" : "Fehler"} · ${lauf.fills} Fills` : "";
}

// ---------------------------------------------------------------- System und Bots
function zeigeSprungkarten(daten) {
  const ampel = daten.ampel || { stufe: "grau", satz: "–" };
  const zaehler = daten.zaehler || {};
  setzeSatz($("sprung-system"), ampel.stufe, ampel.satz);
  const probleme = [zaehler.rot ? `${zaehler.rot} rot` : "", zaehler.gelb ? `${zaehler.gelb} gelb` : ""].filter(Boolean);
  $("sprung-system-unter").textContent = [...probleme, `${zaehler.gruen || 0} aktiv`, `${zaehler.grau || 0} offen`].join(" · ");

  const bots = (daten.kaesten || []).filter((k) => k.id.startsWith("bot-"));
  const aktiv = bots.filter((k) => k.stufe !== "grau");
  setzeSatz($("sprung-bots"), schlechtesteStufe(aktiv), `${aktiv.length} aktiv`);
  $("sprung-bots-unter").textContent = bots.map((k) => k.stufe === "grau" ? `${k.titel} offen` : k.titel).join(" · ");
}

function setzeSatz(ziel, stufe, text) {
  ziel.innerHTML = "";
  ziel.appendChild(punkt(stufe));
  ziel.appendChild(el("span", {}, text));
}

function schlechtesteStufe(kaesten) {
  for (const stufe of ["rot", "gelb", "gruen"]) if (kaesten.some((k) => k.stufe === stufe)) return stufe;
  return "grau";
}

// ---------------------------------------------------------------- Grösste Bewegungen
// Die drei Positionen mit dem grössten offenen Gewinn oder Verlust in Dollar (Betrag, nicht Prozent).
function zeigeBewegungen(c, daten) {
  const liste = $("bewegungen");
  liste.innerHTML = "";
  const bot = (daten.kaesten || []).find((k) => k.detail === "strategie-c");
  $("bewegungen-link").textContent = bot ? `${bot.titel} ›` : "";
  $("bewegungen-link").href = bot ? `#bots/${bot.id}/positionen` : "#bots";
  const positionen = c ? c.positionen.slice().sort((a, b) => Math.abs(b.pnl) - Math.abs(a.pnl)).slice(0, 3) : [];
  if (!positionen.length) liste.appendChild(el("li", { class: "leer" }, "Keine offenen Positionen."));
  positionen.forEach((p) => liste.appendChild(positionsZeile(p, `${zahl(p.bot_qty)} Stk · ${p.price != null ? zahl(p.price, 2) : "–"}`, null)));
}

// Eine Zeile wie in der Positionsliste: Symbol-Kachel, Name, Unterzeile, G/V rechts
function positionsZeile(p, unterzeile, rechtsUnten) {
  const li = el("li");
  li.appendChild(el("span", { class: "symbol" }, p.symbol));
  const mitte = el("span", { class: "mitte" });
  mitte.appendChild(el("span", { class: "name" }, p.symbol));
  mitte.appendChild(el("span", { class: "klein gedaempft" }, unterzeile));
  li.appendChild(mitte);
  const rechts = el("span", { class: "rechts" });
  rechts.appendChild(el("span", { class: `gv ${farbKlasse(p.pnl)}` }, geldMitVorzeichen(p.pnl)));
  if (rechtsUnten) rechts.appendChild(el("span", { class: "gedaempft" }, rechtsUnten));
  li.appendChild(rechts);
  return li;
}

// ---------------------------------------------------------------- Absicherung
function zeigeAbsicherung(kaesten) {
  const zeile = $("absicherung");
  zeile.innerHTML = "";
  kaesten.forEach((k) => {
    const teil = el("span");
    teil.appendChild(punkt(k.stufe));
    teil.appendChild(el("b", {}, k.kurztitel || k.titel));
    teil.appendChild(el("span", { class: "klein gedaempft" }, k.kurz));
    zeile.appendChild(teil);
  });
}
