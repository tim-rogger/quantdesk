"use strict";
// Reiter "Übersicht": in drei Sekunden wissen, ob man genauer hinschauen muss.
// Ampel-Satz (rechnet der Sammler, bewerten.ampel), eine Karte pro Bot, eine Zeile Absicherung.

function zeigeUebersicht(daten) {
  zeigeAmpel(daten.ampel || { stufe: "grau", satz: "Keine Ampel in den Daten", was: [] });
  const bots = (daten.kaesten || []).filter((k) => k.id.startsWith("bot-"));
  const raster = $("bot-karten");
  raster.innerHTML = "";
  bots.forEach((k) => raster.appendChild(botKarte(k)));
  zeigeAbsicherung((daten.kaesten || []).filter((k) => k.id === "backup" || k.id === "totmann"));
}

function zeigeAmpel(ampel) {
  const karte = $("ampel");
  karte.className = `ampel karte ${ampel.stufe}`;
  karte.replaceChild(punkt(ampel.stufe, true), karte.firstElementChild);
  $("ampel-satz").textContent = ampel.satz;
  $("ampel-was").textContent = (ampel.was || []).join(" · ");
}

// Kompakte Karte: Name, Kurzstrategie, Modus, Ampel mit einem Wort, zwei Zahlen. Antippen -> Bots-Seite.
function botKarte(k) {
  const karte = el("a", { class: `bot-karte karte ${k.stufe}`, href: `#bots/${k.id}` });
  const kopf = el("div", { class: "zeile-zwischen" });
  kopf.appendChild(el("strong", { class: "bot-name" }, k.titel));
  if (k.modus) kopf.appendChild(modusAbzeichen(k.modus));
  karte.appendChild(kopf);
  karte.appendChild(el("span", { class: "klein gedaempft" }, k.strategie_kurz || ""));

  const status = el("span", { class: "status" });
  status.appendChild(punkt(k.stufe));
  status.appendChild(el("span", {}, STUFE_WORT[k.stufe] || k.stufe));
  karte.appendChild(status);

  karte.appendChild(zahl("Investiert", k.investiert || "–"));
  karte.appendChild(zahl("Letzter Lauf", k.letzter_lauf ? zeit(k.letzter_lauf, { weekday: "short", hour: "2-digit", minute: "2-digit" }) : "–",
    k.letzter_lauf ? relativ(k.letzter_lauf) : ""));
  return karte;
}

function zahl(beschriftung, wert, zusatz = "") {
  const block = el("span", { class: "zahl" });
  block.appendChild(el("span", { class: "klein gedaempft" }, beschriftung));
  block.appendChild(el("span", { class: "zahl-wert" }, wert));
  if (zusatz) block.appendChild(el("span", { class: "klein gedaempft" }, zusatz));
  return block;
}

// Absicherung: klein, aber sichtbar – damit nicht vergessen geht, dass sie fehlt
function zeigeAbsicherung(kaesten) {
  const zeile = $("absicherung");
  zeile.innerHTML = "";
  kaesten.forEach((k) => {
    const teil = el("span", { class: "absicherung-teil" });
    teil.appendChild(punkt(k.stufe));
    teil.appendChild(el("span", {}, k.kurztitel || k.titel));
    teil.appendChild(el("span", { class: "klein gedaempft" }, k.kurz));
    zeile.appendChild(teil);
  });
}
