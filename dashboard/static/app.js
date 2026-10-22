"use strict";
// Bot C im Detail: Inhalte aus /api/status (status.json von Bot C, nur gelesen).
// Meldet sich unter DETAILS["strategie-c"] an (bots.toml: detail = "strategie-c") und liefert cUebersicht()
// für die Wertkarte auf der Übersicht. $, el, geld, prozent usw. kommen aus kern.js.
// Die Notbremse (für beide Bots) steht nicht hier, sondern in notbremse.js.

const C_DETAIL = "strategie-c";
const SORTIERUNGEN = { gv: "G/V", wert: "Wert", az: "A–Z" };

// ---------------------------------------------------------------- Zahlen aus status.json
function cPositionen(status) {
  return (status.systems || []).filter((s) => s.bot_qty > 0 || s.entry_pending || s.exit_pending);
}

// Kurven auf 1.0 = Start normiert. Bevorzugt aus dem Report, sonst aus den Tages-Snapshots.
function cKurven(status) {
  const kurven = (status.report && status.report.curves) || {};
  if (kurven["C live (Paper)"]) return { kurve: kurven["C live (Paper)"], spy: kurven["SPY halten"] || null };
  const snaps = (status.snapshots || []).filter((s) => s.c_value && s.spy);
  if (snaps.length < 2) return { kurve: null, spy: null };
  return {
    kurve: { days: snaps.map((s) => s.day), values: snaps.map((s) => s.c_value / snaps[0].c_value) },
    spy: { days: snaps.map((s) => s.day), values: snaps.map((s) => s.spy / snaps[0].spy) },
  };
}

function cSpyRendite(status) {
  const zeile = status.report && status.report.rows && status.report.rows["SPY halten"];
  if (zeile && zeile.USD) return zeile.USD.total_return;
  const { spy } = cKurven(status);
  return spy ? spy.values[spy.values.length - 1] - 1 : null;
}

function cLetzterLauf(status) {
  const laeufe = status.runs || [];
  const letzter = laeufe[laeufe.length - 1];
  return letzter ? { ts: letzter.ts, ok: letzter.ok, fills: letzter.fills ?? 0 } : null;
}

// Offener Gewinn/Verlust: (Kurs − Ø-Einstand) × eigene Stück, über alle offenen Positionen.
// Ohne Gebühren und ohne geschlossene Trades – das ganze Ergebnis steht unter "Seit Start".
function cOffenerGewinn(status) {
  return cPositionen(status).reduce((summe, s) => summe + (s.pnl || 0), 0);
}

function cBotKasten() {
  return ((systemDaten && systemDaten.kaesten) || []).find((k) => k.detail === C_DETAIL);
}

// Für die Wertkarte der Übersicht
function cUebersicht(status) {
  const konto = status.c_account || {};
  const { kurve, spy } = cKurven(status);
  const bot = cBotKasten();
  return {
    name: bot ? bot.titel : "Bot C",
    wert: konto.value ?? null,
    budget: konto.budget ?? null,
    gewinn: konto.value != null && konto.budget ? konto.value - konto.budget : null,
    rendite: konto.value != null && konto.budget ? konto.value / konto.budget - 1 : null,
    anteil: konto.value ? konto.invested / konto.value : null,
    cash: konto.cash ?? null,
    letzterLauf: cLetzterLauf(status),
    kurve, spy,
    positionen: cPositionen(status).filter((s) => s.bot_qty > 0),
  };
}

// ---------------------------------------------------------------- Detailseite von Bot C
DETAILS[C_DETAIL] = {
  reiter: ["positionen", "details", "kriterien"],

  kacheln(k, status) {
    const c = cUebersicht(status);
    const spy = cSpyRendite(status);
    const offen = cOffenerGewinn(status);
    return [
      { t: "Wert", w: geld(c.wert), unter: `Budget ${geld(c.budget)}` },
      { t: "Seit Start", w: prozent(c.rendite), klasse: farbKlasse(c.rendite), unter: spy != null ? `SPY ${prozent(spy)}` : "" },
      { t: "Investiert", w: geld(status.c_account ? status.c_account.invested : null),
        unter: `${c.anteil != null ? (c.anteil * 100).toFixed(1) : "–"} % · Cash ${geld(c.cash)}` },
      { t: "G/V offen", w: geldMitVorzeichen(offen), klasse: farbKlasse(offen),
        unter: `${c.positionen.length} Positionen · ohne Gebühren` },
    ];
  },

  inhalt(reiter, k, status, sortierung) {
    if (reiter === "positionen") return cPositionsListe(k, status, sortierung);
    if (reiter === "kriterien") return cKriterien(status);
    return cDetails(k, status);
  },
};

function cPositionsListe(k, status, sortierung) {
  const box = el("div");
  const art = SORTIERUNGEN[sortierung] ? sortierung : "gv";
  const alle = status.systems || [];
  const positionen = cPositionen(status).slice().sort(vergleicher(art));

  const kopf = el("div", { class: "sortier-zeile" });
  kopf.appendChild(el("span", { class: "gedaempft" }, `${positionen.length} von ${alle.length} Systemen`));
  const links = el("nav", { class: "sortierung", "aria-label": "Sortierung" });
  for (const [schluessel, name] of Object.entries(SORTIERUNGEN)) {
    links.appendChild(el("a", { href: `#bots/${k.id}/positionen/${schluessel}`, class: schluessel === art ? "aktiv" : "" }, name));
  }
  kopf.appendChild(links);
  box.appendChild(kopf);

  const karte = el("div", { class: "positionen-karte" });
  const liste = el("ul", { class: "positionen" });
  if (!positionen.length) liste.appendChild(el("li", { class: "leer" }, "Keine offenen Positionen."));
  positionen.forEach((s) => {
    const zusatz = [s.exit_pending ? "Verkauf offen" : "", s.entry_pending ? "Einstieg offen" : "",
      s.broker_qty > s.bot_qty ? `+${s.broker_qty - s.bot_qty} fremd` : ""].filter(Boolean).join(" · ");
    const unter = `${zahl(s.bot_qty)} × Ø ${s.avg ? zahl(s.avg, 2) : "–"}` + (zusatz ? ` · ${zusatz}` : "");
    liste.appendChild(positionsZeile(s, unter, s.price ? zahl(s.price, 2) : "–"));
  });
  karte.appendChild(liste);
  box.appendChild(karte);

  const gesperrt = alle.filter((s) => s.not_tradable).map((s) => s.symbol);
  const geschlossen = alle.filter((s) => s.closed).map((s) => `${s.symbol} (${s.closed})`);
  const hinweis = [gesperrt.length ? `Ohne Handelsberechtigung: ${gesperrt.join(", ")}` : "",
    geschlossen.length ? `Geschlossen: ${geschlossen.join(", ")}` : ""].filter(Boolean).join(" · ");
  if (hinweis) box.appendChild(el("p", { class: "klein gedaempft unter-titel" }, hinweis));
  return box;
}

function vergleicher(art) {
  if (art === "wert") return (a, b) => (b.bot_qty * (b.price || 0)) - (a.bot_qty * (a.price || 0));
  if (art === "az") return (a, b) => a.symbol.localeCompare(b.symbol);
  return (a, b) => (b.pnl || 0) - (a.pnl || 0);
}

function cDetails(k, status) {
  const box = el("div");
  const fehler = cFehlerText(status);
  if (fehler) box.appendChild(el("div", { class: "banner gelb" }, fehler));
  box.appendChild(el("p", { class: "abschnitt" }, "Details"));
  box.appendChild(detailListe(k.zeilen));

  box.appendChild(el("p", { class: "abschnitt" }, "Letzte Läufe"));
  const laeufe = el("ul", { class: "einfache-liste karte" });
  (status.runs || []).slice().reverse().slice(0, 10).forEach((r) => {
    const li = el("li");
    li.appendChild(punkt(r.ok ? "gruen" : "rot"));
    li.appendChild(el("span", {}, `${zeit(r.ts)} · ${r.mode} · ${r.fills} Fills, ${r.orders} Orders` +
      (r.errors && r.errors.length ? ` · ${r.errors[0]}` : "")));
    laeufe.appendChild(li);
  });
  if (!laeufe.children.length) laeufe.appendChild(el("li", { class: "leer" }, "Noch kein Lauf."));
  box.appendChild(laeufe);

  box.appendChild(el("p", { class: "abschnitt" }, "Verlauf"));
  const verlauf = el("ul", { class: "einfache-liste karte" });
  (status.events || []).slice().reverse().slice(0, 40).forEach((e) => {
    const li = el("li");
    li.appendChild(punkt(e.level === "error" ? "rot" : e.level === "warn" ? "gelb" : "gruen"));
    li.appendChild(el("span", {}, `${zeit(e.ts, { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" })} · ${e.message}`));
    verlauf.appendChild(li);
  });
  if (!verlauf.children.length) verlauf.appendChild(el("li", { class: "leer" }, "Noch keine Ereignisse."));
  box.appendChild(verlauf);

  box.appendChild(el("p", { class: "klein gedaempft unter-titel" }, `Stand Bot C: ${status.generated_at ? zeit(status.generated_at) : "–"}`));
  return box;
}

// Fehler des letzten Laufs und ungültige Zahlen (nan/inf, vom Server durch null ersetzt) sichtbar machen
function cFehlerText(status) {
  const lauf = (status.runs || [])[(status.runs || []).length - 1];
  const fehler = lauf && lauf.errors && lauf.errors.length ? `Letzter Lauf: ${lauf.errors[0]}` : "";
  const kaputt = (status.invalid_numbers || []).length ? `Ungültige Zahlen im Status (nan/inf): ${status.invalid_numbers.join(", ")}` : "";
  return [kaputt, fehler].filter(Boolean).join(" · ");
}

function cKriterien(status) {
  const box = el("div");
  const rep = status.report;
  const stand = rep ? (rep.finished ? (rep.passed ? "bestanden" : "durchgefallen") : `läuft, Monat ${rep.months_done + 1} von 6`) : "";
  box.appendChild(el("p", { class: "abschnitt" }, `Bestehen-Kriterien${stand ? " – " + stand : ""}`));
  const liste = el("ul", { class: "einfache-liste karte" });
  if (!rep) liste.appendChild(el("li", { class: "leer" }, "Noch kein Report (ab dem 2. Handelstag)."));
  (rep ? rep.checks : []).forEach((c) => {
    const li = el("li");
    li.appendChild(punkt(c.ok ? "gruen" : "rot"));
    li.appendChild(el("span", {}, `${c.code} ${c.text} – ${c.detail}`));
    liste.appendChild(li);
  });
  if (rep && rep.estimated_fills) liste.appendChild(el("li", { class: "gedaempft" }, `${rep.estimated_fills} Fill(s) mit geschätztem Tag (siehe ROADMAP).`));
  box.appendChild(liste);
  return box;
}
