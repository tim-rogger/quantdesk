"use strict";
// Gemeinsamer Kern der App: Hilfsfunktionen, Laden von system.json, Alter prüfen, Reiter wechseln.
// Nur lesen – nirgends eine Anfrage, die etwas verändert. Daten immer mit textContent einsetzen, nie als HTML.

const $ = (id) => document.getElementById(id);
const VERALTET_NACH_SEKUNDEN = 120;
const NEU_LADEN_SEKUNDEN = 30;
const ANSICHTEN = { uebersicht: "Übersicht", bots: "Bots", system: "System" };

const SYMBOL = { gruen: "✓", gelb: "!", rot: "✕", grau: "–" };
const STUFE_WORT = { gruen: "läuft", gelb: "prüfen", rot: "Fehler", grau: "nicht eingerichtet" };
const STUFE_TEXT = { gruen: "grün – läuft", gelb: "gelb – stimmt etwas nicht", rot: "rot – aus oder nicht erreichbar",
  grau: "grau – kein Fehler" };

let systemDaten = null;  // zuletzt erfolgreich geladene system.json
let ladeFehler = "";     // Text, falls das Dashboard gerade nicht antwortet

// ---------------------------------------------------------------- Bausteine
function el(tag, attrs = {}, text = "") {
  const e = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) e.setAttribute(k, v);
  if (text !== "") e.textContent = text;
  return e;
}

function svgEl(tag, attrs = {}, text = "") {
  const e = document.createElementNS("http://www.w3.org/2000/svg", tag);
  for (const [k, v] of Object.entries(attrs)) e.setAttribute(k, v);
  if (text) e.textContent = text;
  return e;
}

function punkt(stufe, gross = false) {
  return el("span", { class: `punkt ${stufe}${gross ? " gross" : ""}`, "aria-hidden": "true" }, SYMBOL[stufe] || "?");
}

function modusKlasse(modus) {
  const m = String(modus || "").toUpperCase();
  return m === "PAPER" ? "paper" : m === "ECHT" || m === "LIVE" ? "echt" : "probe";
}

function modusAbzeichen(modus) {
  return el("span", { class: `abzeichen ${modusKlasse(modus)}` }, modus);
}

// ---------------------------------------------------------------- Zeit (immer mit Zeitzone)
// ts = Unix-Sekunden. Angezeigt in der Zeitzone des Handys, mit Kürzel: "Fr., 09.10., 16:00 MESZ".
function zeit(ts, opts = { weekday: "short", day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" }) {
  return new Date(ts * 1000).toLocaleString("de-CH", { ...opts, timeZoneName: "short" });
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

// Zeile aus system.json: {t, w} (Text) oder {t, z, zone} (Zeitpunkt)
function zeitText(zeile) {
  let text = `${zeit(zeile.z)} (${relativ(zeile.z)})`;
  const hier = Intl.DateTimeFormat().resolvedOptions().timeZone;
  if (zeile.zone && zeile.zone !== hier) text += ` · ${uhrzeitIn(zeile.z, zeile.zone)}`;
  return text;
}

function werteListe(zeilen) {
  const dl = el("dl", { class: "werte" });
  (zeilen || []).forEach((z) => {
    dl.appendChild(el("dt", {}, z.t));
    dl.appendChild(el("dd", {}, z.z != null ? zeitText(z) : z.w ?? "–"));
  });
  return dl;
}

// ---------------------------------------------------------------- Reiter
function aktuelleAnsicht() {
  const name = location.hash.slice(1).split("/")[0];
  if (ANSICHTEN[name]) return name;
  return location.pathname === "/system" ? "system" : "uebersicht";
}

function zeigeAnsicht() {
  const name = aktuelleAnsicht();
  for (const key of Object.keys(ANSICHTEN)) $(`ansicht-${key}`).hidden = key !== name;
  document.querySelectorAll(".tabbar a").forEach((a) => {
    const aktiv = a.dataset.ansicht === name;
    a.classList.toggle("aktiv", aktiv);
    if (aktiv) a.setAttribute("aria-current", "page"); else a.removeAttribute("aria-current");
  });
  $("seitentitel").textContent = ANSICHTEN[name];
  window.scrollTo(0, 0);
  // #bots/bot-lotse oder #system/k-server: direkt zu diesem Bot bzw. dieser Karte springen
  const ziel = location.hash.split("/")[1];
  if (name === "bots") oeffneBot(ziel);
  if (name === "system" && ziel && $(ziel)) $(ziel).scrollIntoView({ block: "start" });
}

// ---------------------------------------------------------------- Kopf: Paper-Abzeichen und Warnung
function zeigeKopf(daten) {
  const konto = (daten.kaesten || []).find((k) => k.id === "ibkr");
  const abzeichen = $("paper-abzeichen");
  if (konto && konto.paper) {
    abzeichen.textContent = "PAPER";
    abzeichen.className = "abzeichen paper";
  } else if (konto && konto.stufe === "rot") {
    abzeichen.textContent = "NICHT PAPER";
    abzeichen.className = "abzeichen echt";
  } else {
    abzeichen.textContent = "PAPER ?";
    abzeichen.className = "abzeichen";
  }
  const warnung = $("warnung");
  warnung.hidden = !(daten.warnungen && daten.warnungen.length);
  warnung.textContent = (daten.warnungen || []).join(" ");
  $("stand").textContent = `${zeit(daten.erzeugt)} · ${daten.erzeugt_utc}`;
}

// ---------------------------------------------------------------- Laden und Alter prüfen
// Daten älter als 2 Minuten: die ganze App abblenden. Lieber "ich weiss es nicht" als ein altes Grün.
function alterPruefen() {
  const banner = $("veraltet");
  if (!systemDaten) {
    document.body.classList.add("veraltet");
    banner.hidden = false;
    banner.textContent = ladeFehler || "Noch keine Daten.";
    return;
  }
  const alt = Date.now() / 1000 - systemDaten.erzeugt > VERALTET_NACH_SEKUNDEN;
  document.body.classList.toggle("veraltet", alt);
  banner.hidden = !alt && !ladeFehler;
  const utc = new Date(systemDaten.erzeugt * 1000).toLocaleTimeString("de-CH", { hour: "2-digit", minute: "2-digit", timeZone: "UTC" });
  banner.textContent = alt ? `Daten veraltet, Stand ${utc} UTC (${relativ(systemDaten.erzeugt)}). ${ladeFehler}`.trim() : ladeFehler;
}

async function ladeSystem() {
  try {
    const antwort = await fetch("/api/system", { cache: "no-store" });
    const neu = await antwort.json();
    if (neu.fehlt) {
      ladeFehler = neu.grund || "Keine Daten.";
    } else {
      systemDaten = neu;
      ladeFehler = "";
      zeigeKopf(neu);
      zeigeUebersicht(neu);
      zeigeBots(neu);
      zeigeSystem(neu);
    }
    zeigeNotbremse(neu);  // notbremse.js – auch wenn die Sammler-Daten fehlen
  } catch (e) {
    ladeFehler = "Dashboard nicht erreichbar (Tailscale an?).";
  }
  alterPruefen();
}

window.addEventListener("hashchange", zeigeAnsicht);
window.addEventListener("DOMContentLoaded", () => {
  zeigeAnsicht();
  ladeSystem();
  setInterval(ladeSystem, NEU_LADEN_SEKUNDEN * 1000);
  setInterval(alterPruefen, 10 * 1000);
  if ("serviceWorker" in navigator) navigator.serviceWorker.register("/sw.js");
});
