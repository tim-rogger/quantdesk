"use strict";
// Reiter "Bots": Liste (Vorlage 02) und Detailseite eines Bots (Vorlage 03).
// Welche Bots es gibt, kommt aus system.json (deploy/bots.toml) – hier ist kein Bot fest verdrahtet.
// Was ein Bot zusätzlich zeigt, steht in DETAILS[<detail aus bots.toml>] (z.B. Bot C in app.js).

const REITER_NAME = { positionen: "Positionen", details: "Details", kriterien: "Kriterien", vorschlag: "Vorschlag" };

function alleBots(daten) {
  return (daten.kaesten || []).filter((k) => k.id.startsWith("bot-"));
}

// "Bot C" -> "C", "Lotse" -> "L"
function botBuchstabe(name) {
  const woerter = String(name).trim().split(/\s+/);
  return (woerter[woerter.length - 1] || "?")[0].toUpperCase();
}

function botIcon(k, gross = false) {
  return el("span", { class: `bot-icon ${k.stufe}${gross ? " gross" : ""}`, "aria-hidden": "true" }, botBuchstabe(k.titel));
}

// ---------------------------------------------------------------- Liste
function zeigeBotListe(daten) {
  const liste = $("bot-liste");
  liste.innerHTML = "";
  alleBots(daten).forEach((k) => liste.appendChild(k.stufe === "grau" ? graueBotKarte(k) : botKarte(k)));
}

function graueBotKarte(k) {
  const karte = el("a", { class: "karte bot-karte grau", href: `#bots/${k.id}` });
  const kopf = el("div", { class: "bot-kopf" });
  kopf.appendChild(botIcon(k));
  const mitte = el("div", { class: "mitte" });
  mitte.appendChild(el("div", { class: "bot-name" }, k.titel));
  mitte.appendChild(el("div", { class: "bot-strategie" }, k.kurz));
  kopf.appendChild(mitte);
  kopf.appendChild(el("span", { class: "pfeil" }, "›"));
  karte.appendChild(kopf);
  return karte;
}

function botKarte(k) {
  const z = k.zahlen || {};
  const karte = el("a", { class: "karte bot-karte", href: `#bots/${k.id}` });
  const kopf = el("div", { class: "bot-kopf" });
  kopf.appendChild(botIcon(k));
  const mitte = el("div", { class: "mitte" });
  mitte.appendChild(el("div", { class: "bot-name" }, k.titel));
  mitte.appendChild(el("div", { class: "bot-strategie" }, k.strategie || k.strategie_kurz || ""));
  kopf.appendChild(mitte);
  if (k.modus) kopf.appendChild(modusPille(k.modus));
  karte.appendChild(kopf);

  const wert = el("div", { class: "bot-wert" });
  const links = el("div");
  links.appendChild(el("div", { class: "zahl" }, z.wert != null ? geld(z.wert, z.waehrung) : k.investiert || "–"));
  links.appendChild(z.rendite != null
    ? el("div", { class: `rendite ${farbKlasse(z.rendite)}` }, `${prozent(z.rendite)} seit Start`)
    : el("div", { class: "rendite gedaempft" }, "investiert"));
  wert.appendChild(links);
  wert.appendChild(minikurve(k.verlauf));
  karte.appendChild(wert);

  if (k.stufe === "gelb" || k.stufe === "rot") karte.appendChild(gruendeListe(k));
  karte.appendChild(botZahlen(k));
  if (k.vorschlag) {
    const zeile = el("p", { class: "bot-vorschlag" }, "Letzter Vorschlag: ");
    zeile.appendChild(el("b", {}, k.vorschlag));
    karte.appendChild(zeile);
  }
  return karte;
}

function botZahlen(k) {
  const z = k.zahlen || {};
  const reihe = el("div", { class: "bot-zahlen" });
  const eintraege = [
    ["Positionen", z.positionen != null ? String(z.positionen) : "–"],
    ["Investiert", z.anteil_investiert != null ? `${(z.anteil_investiert * 100).toFixed(1)} %` : k.investiert || "–"],
    ["Letzter Lauf", k.letzter_lauf ? wochentagZeit(k.letzter_lauf) : "–"],
  ];
  for (const [titel, wert] of eintraege) {
    const teil = el("span");
    teil.appendChild(el("span", { class: "beschriftung" }, titel));
    teil.appendChild(el("b", {}, wert));
    reihe.appendChild(teil);
  }
  return reihe;
}

// ---------------------------------------------------------------- Detailseite
function zeigeBotSeite(a, daten, status) {
  const seite = $("bot-seite");
  seite.innerHTML = "";
  const k = kastenMitId(a.id);
  if (!k) {
    seite.appendChild(el("p", { class: "leer" }, "Diesen Bot gibt es nicht (mehr) in bots.toml."));
    return;
  }
  const extra = k.detail ? DETAILS[k.detail] : null;
  seite.appendChild(seitenKopf(k));
  const gruende = gruendeListe(k);
  if (gruende) seite.appendChild(gruende);
  seite.appendChild(kachelRaster(extra && extra.kacheln && status ? extra.kacheln(k, status) : standardKacheln(k)));

  const reiter = extra && extra.reiter ? extra.reiter : k.vorschlag ? ["vorschlag", "details"] : ["details"];
  const aktiv = reiter.includes(a.reiter) ? a.reiter : reiter[0];
  seite.appendChild(reiterLeiste(k, reiter, aktiv));
  const inhalt = extra && extra.inhalt && status ? extra.inhalt(aktiv, k, status, a.sortierung) : null;
  seite.appendChild(inhalt || standardInhalt(aktiv, k));
}

function seitenKopf(k) {
  const kopf = el("div", { class: "seiten-kopf" });
  kopf.appendChild(botIcon(k, true));
  const mitte = el("div", { class: "mitte" });
  mitte.appendChild(el("h1", {}, k.titel));
  const status = el("div", { class: "status" });
  status.appendChild(punkt(k.stufe));
  const konto = wertAusZeilen(kastenMitId("ibkr"), "Konto");
  status.appendChild(el("span", {}, [STUFE_WORT[k.stufe], konto].filter(Boolean).join(" · ")));
  mitte.appendChild(status);
  kopf.appendChild(mitte);
  if (k.modus) kopf.appendChild(modusPille(k.modus));
  return kopf;
}

// kacheln = [{t, w, unter, klasse}] – zwei nebeneinander
function kachelRaster(kacheln) {
  const raster = el("div", { class: "kachel-reihe zwei" });
  kacheln.forEach((k) => {
    const kachel = el("div", { class: "kachel" });
    kachel.appendChild(el("span", { class: "beschriftung" }, k.t));
    kachel.appendChild(el("span", { class: `kachel-wert gross ${k.klasse || ""}` }, k.w));
    kachel.appendChild(el("span", { class: "unter" }, k.unter || ""));
    raster.appendChild(kachel);
  });
  return raster;
}

function standardKacheln(k) {
  const z = k.zahlen || {};
  const w = z.waehrung;
  return [
    { t: "Wert", w: geld(z.wert, w), unter: z.budget != null ? `Budget ${geld(z.budget, w)}` : "" },
    { t: "Seit Start", w: prozent(z.rendite), klasse: farbKlasse(z.rendite), unter: z.gewinn != null ? geldMitVorzeichen(z.gewinn, w) : "" },
    { t: "Investiert", w: geld(z.investiert, w),
      unter: z.anteil_investiert != null ? `${(z.anteil_investiert * 100).toFixed(1)} % · ${z.positionen ?? 0} Positionen` : "" },
    { t: "Letzter Lauf", w: k.letzter_lauf ? wochentagZeit(k.letzter_lauf) : "–",
      unter: k.letzter_lauf ? `${zoneKuerzel(k.letzter_lauf)} · ${k.lauf_ok === false ? "gescheitert" : "ok"}` : "" },
  ];
}

// Reiter als Links: jede Ansicht hat ihre eigene Adresse (zurück-Geste funktioniert, keine Knöpfe)
function reiterLeiste(k, reiter, aktiv) {
  const leiste = el("nav", { class: "reiter", "aria-label": "Ansicht" });
  reiter.forEach((r) => leiste.appendChild(el("a", { href: `#bots/${k.id}/${r}`, class: r === aktiv ? "aktiv" : "" }, REITER_NAME[r] || r)));
  return leiste;
}

function standardInhalt(reiter, k) {
  const box = el("div");
  if (reiter === "vorschlag") {
    box.appendChild(el("p", { class: "abschnitt" }, "Letzter Vorschlag"));
    const wichtig = ["Letzter Vorschlag", "Kurs", "Letzter Lauf", "Ergebnis", "Nächster Lauf"];
    box.appendChild(detailListe(wichtig.flatMap((titel) => (k.zeilen || []).filter((z) => z.t === titel))));
    return box;
  }
  box.appendChild(el("p", { class: "abschnitt" }, "Details"));
  box.appendChild(detailListe(k.zeilen));
  return box;
}
