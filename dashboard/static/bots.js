"use strict";
// Reiter "Bots": jeder Bot aus deploy/bots.toml vollständig, als aufklappbare Karte.
// Welche Bots es gibt und was sie zeigen, kommt aus system.json – hier ist kein Bot fest verdrahtet.

const KACHELN = ["Investiert", "Budget", "Letzter Lauf", "Nächster Lauf"];

function zeigeBots(daten) {
  const liste = $("bot-liste");
  const offen = [...liste.querySelectorAll("details[open]")].map((d) => d.id);
  zusatzBloeckeZurueck();
  liste.innerHTML = "";
  (daten.kaesten || []).filter((k) => k.id.startsWith("bot-")).forEach((k) => {
    const karte = botDetail(k);
    karte.open = offen.includes(karte.id);
    liste.appendChild(karte);
  });
  if (aktuelleAnsicht() === "bots") oeffneBot(location.hash.split("/")[1], false);
}

function botDetail(k) {
  const karte = el("details", { class: `bot-detailkarte karte ${k.stufe}`, id: k.id });
  const kopf = el("summary");
  const zeile1 = el("span", { class: "zeile-zwischen" });
  const name = el("span", { class: "status" });
  name.appendChild(punkt(k.stufe));
  name.appendChild(el("strong", { class: "bot-name" }, k.titel));
  zeile1.appendChild(name);
  if (k.modus) zeile1.appendChild(modusAbzeichen(k.modus));
  kopf.appendChild(zeile1);
  kopf.appendChild(el("span", { class: "klein gedaempft" }, `${STUFE_WORT[k.stufe] || k.stufe} · ${k.kurz}`));
  karte.appendChild(kopf);

  const inhalt = el("div", { class: "inhalt" });
  if (k.gruende && k.gruende.length) {
    const ul = el("ul", { class: "gruende" });
    k.gruende.forEach((g) => ul.appendChild(el("li", {}, g)));
    inhalt.appendChild(ul);
  }
  inhalt.appendChild(kacheln(k.zeilen || []));
  inhalt.appendChild(werteListe((k.zeilen || []).filter((z) => !KACHELN.includes(z.t))));
  const zusatz = k.detail ? $(`detail-${k.detail}`) : null;
  if (zusatz) {
    zusatz.hidden = false;
    inhalt.appendChild(zusatz);
  }
  karte.appendChild(inhalt);
  return karte;
}

// Die vier wichtigsten Werte gross, zwei nebeneinander
function kacheln(zeilen) {
  const raster = el("div", { class: "kacheln" });
  KACHELN.forEach((titel) => {
    const z = zeilen.find((x) => x.t === titel);
    if (!z) return;
    const kachel = el("div", { class: "kachel" });
    kachel.appendChild(el("span", { class: "klein gedaempft" }, titel));
    if (z.z != null) {
      const hier = Intl.DateTimeFormat().resolvedOptions().timeZone;
      const dort = z.zone && z.zone !== hier ? ` · ${uhrzeitIn(z.z, z.zone)}` : "";
      kachel.appendChild(el("span", { class: "zahl-wert zeitpunkt" }, zeit(z.z, { weekday: "short", hour: "2-digit", minute: "2-digit" })));
      kachel.appendChild(el("span", { class: "klein gedaempft" }, relativ(z.z) + dort));
    } else {
      kachel.appendChild(el("span", { class: "zahl-wert" }, z.w ?? "–"));
    }
    raster.appendChild(kachel);
  });
  return raster;
}

// Zusatz-Blöcke (z.B. Strategie C) gehören dem HTML, nicht dieser Liste: vor dem Neuzeichnen zurücklegen
function zusatzBloeckeZurueck() {
  document.querySelectorAll("#bot-liste .bot-detail").forEach((block) => {
    block.hidden = true;
    $("ansicht-bots").appendChild(block);
  });
}

// #bots/bot-lotse: diesen Bot aufklappen und hinscrollen
function oeffneBot(id, scrollen = true) {
  const karte = id ? $(id) : null;
  if (!karte || karte.tagName !== "DETAILS") return;
  karte.open = true;
  if (scrollen) karte.scrollIntoView({ block: "start" });
}
