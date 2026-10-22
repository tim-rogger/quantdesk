"use strict";
// Gemeinsamer Kern der App: Hilfsfunktionen, Laden der Daten, Alter prüfen, Seiten wechseln.
// Nur lesen – nirgends eine Anfrage, die etwas verändert. Daten immer mit textContent einsetzen, nie als HTML.
//
// Adressen (alles nach dem #):
//   uebersicht | bots | system                     die drei Reiter
//   bots/<bot>[/<reiter>[/<sortierung>]]          Detailseite eines Bots, z.B. bots/bot-c/positionen/wert
//   system/<kasten>                                Detailseite eines Kastens, z.B. system/server

const $ = (id) => document.getElementById(id);
const VERALTET_NACH_SEKUNDEN = 120;
const NEU_LADEN_SEKUNDEN = 30;
const TITEL = { uebersicht: "Übersicht", bots: "Bots", system: "System" };

const SYMBOL = { gruen: "✓", gelb: "!", rot: "✕", grau: "–" };
const STUFE_WORT = { gruen: "läuft", gelb: "prüfen", rot: "Fehler", grau: "nicht eingerichtet" };
const STUFE_TEXT = { gruen: "grün – läuft", gelb: "gelb – stimmt etwas nicht", rot: "rot – aus oder nicht erreichbar",
  grau: "grau – kein Fehler" };

const NOTBREMSE_AMPEL = { stufe: "rot", satz: "Notbremse aktiv", was: ["Beide Bots handeln nicht. Fortsetzen nur im Terminal."] };

// Zusatz-Inhalte einzelner Bots (Registry-Feld "detail"), z.B. DETAILS["strategie-c"] in app.js
const DETAILS = {};

let systemDaten = null;  // zuletzt erfolgreich geladene system.json (vom Sammler)
let cStatus = null;      // zuletzt geladene status.json von Bot C (/api/status)
let ladeFehler = "";     // Text, falls das Dashboard gerade nicht antwortet

// ---------------------------------------------------------------- Bausteine
function el(tag, attrs = {}, text = "") {
  const e = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) e.setAttribute(k, v);
  if (text !== "" && text != null) e.textContent = text;
  return e;
}

function svgEl(tag, attrs = {}, text = "") {
  const e = document.createElementNS("http://www.w3.org/2000/svg", tag);
  for (const [k, v] of Object.entries(attrs)) e.setAttribute(k, v);
  if (text) e.textContent = text;
  return e;
}

function punkt(stufe) {
  return el("span", { class: `punkt ${stufe}`, "aria-hidden": "true" });
}

function modusKlasse(modus) {
  const m = String(modus || "").toUpperCase();
  return m === "PAPER" ? "paper" : m === "ECHT" || m === "LIVE" ? "echt" : "probe";
}

function modusPille(modus) {
  return el("span", { class: `pille klein-pille ${modusKlasse(modus)}` }, modus);
}

// ---------------------------------------------------------------- Zahlen
// Schweizer Schreibweise mit Tausender-Apostroph: 265'649
function zahl(v, stellen = 0) {
  return v.toLocaleString("de-CH", { minimumFractionDigits: stellen, maximumFractionDigits: stellen });
}

function geld(v, waehrung = "USD") {
  if (v == null) return "–";
  return waehrung === "USD" ? `${zahl(v)} $` : `${zahl(v)} ${waehrung}`;
}

// Mit Vorzeichen (echtes Minus): +1'649 $ / −886 $
function geldMitVorzeichen(v, waehrung = "USD") {
  if (v == null) return "–";
  const zeichen = v > 0.5 ? "+" : v < -0.5 ? "−" : "";
  return zeichen + geld(Math.abs(v), waehrung);
}

// 0.0062 -> "+0.6 %"
function prozent(v, stellen = 1) {
  if (v == null) return "–";
  const zeichen = v > 0 ? "+" : v < 0 ? "−" : "";
  return `${zeichen}${Math.abs(v * 100).toFixed(stellen)} %`;
}

function farbKlasse(v) {
  return v == null || Math.abs(v) < 1e-9 ? "" : v > 0 ? "positiv" : "negativ";
}

// ---------------------------------------------------------------- Zeit (immer mit Zeitzone)
function zeit(ts, opts = { weekday: "short", day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" }) {
  return new Date(ts * 1000).toLocaleString("de-CH", { ...opts, timeZoneName: "short" });
}

// "Di 21:30" – kurz für Kacheln; die Zeitzone steht dann darunter (zoneKuerzel)
function wochentagZeit(ts) {
  const d = new Date(ts * 1000);
  const tag = d.toLocaleDateString("de-CH", { weekday: "short" }).replace(".", "");
  return `${tag} ${d.toLocaleTimeString("de-CH", { hour: "2-digit", minute: "2-digit" })}`;
}

function zoneKuerzel(ts) {
  const teile = new Intl.DateTimeFormat("de-CH", { timeZoneName: "short" }).formatToParts(new Date(ts * 1000));
  return (teile.find((t) => t.type === "timeZoneName") || {}).value || "";
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

// Zeile aus system.json: {t, w} (Text) oder {t, z, zone} (Zeitpunkt), optional f = Farbe des Werts
function zeitText(zeile) {
  let text = `${zeit(zeile.z)} (${relativ(zeile.z)})`;
  const hier = Intl.DateTimeFormat().resolvedOptions().timeZone;
  if (zeile.zone && zeile.zone !== hier) text += ` · ${uhrzeitIn(zeile.z, zeile.zone)}`;
  return text;
}

function detailListe(zeilen) {
  const dl = el("dl", { class: "detail-liste" });
  (zeilen || []).forEach((z) => {
    const reihe = el("div", { class: "reihe" });
    reihe.appendChild(el("dt", {}, z.t));
    reihe.appendChild(el("dd", { class: z.f || "" }, z.z != null ? zeitText(z) : z.w ?? "–"));
    dl.appendChild(reihe);
  });
  return dl;
}

function gruendeListe(k) {
  if (!k.gruende || !k.gruende.length) return null;
  const ul = el("ul", { class: `gruende ${k.stufe}` });
  k.gruende.forEach((g) => ul.appendChild(el("li", {}, g)));
  return ul;
}

// Mini-Kurve (Sparkline) ohne Achsen: grün, wenn der letzte Wert über dem ersten liegt
function minikurve(werte, breite = 120, hoehe = 40) {
  const svg = svgEl("svg", { viewBox: `0 0 ${breite} ${hoehe}`, class: "minikurve", "aria-hidden": "true" });
  if (!werte || werte.length < 2) return svg;
  const lo = Math.min(...werte), hi = Math.max(...werte), spanne = hi - lo || 1;
  const punkte = werte.map((v, i) => `${(i / (werte.length - 1) * (breite - 4) + 2).toFixed(1)},` +
    `${(hoehe - 4 - (v - lo) / spanne * (hoehe - 8)).toFixed(1)}`).join(" ");
  svg.classList.add(werte[werte.length - 1] >= werte[0] ? "positiv" : "negativ");
  svg.appendChild(svgEl("polyline", { points: punkte }));
  return svg;
}

function kastenMitId(id) {
  return ((systemDaten && systemDaten.kaesten) || []).find((k) => k.id === id);
}

function wertAusZeilen(k, titel) {
  const z = ((k && k.zeilen) || []).find((x) => x.t === titel);
  return z ? z.w : null;
}

// ---------------------------------------------------------------- Adresse lesen und Seite zeigen
function adresse() {
  const teile = location.hash.slice(1).split("/");
  let ansicht = teile[0] || (location.pathname === "/system" ? "system" : "uebersicht");
  if (!TITEL[ansicht]) ansicht = "uebersicht";
  if (ansicht === "bots" && teile[1]) return { ansicht: "bot", id: teile[1], reiter: teile[2], sortierung: teile[3] };
  if (ansicht === "system" && teile[1]) return { ansicht: "systemteil", id: teile[1].replace(/^k-/, "") };
  return { ansicht };
}

function zeigeAktuelleSeite(nachOben = false) {
  const a = adresse();
  for (const name of ["uebersicht", "bots", "bot", "system", "systemteil"]) $(`ansicht-${name}`).hidden = name !== a.ansicht;
  const reiter = a.ansicht === "bot" ? "bots" : a.ansicht === "systemteil" ? "system" : a.ansicht;
  document.querySelectorAll(".tabbar a").forEach((link) => {
    const aktiv = link.dataset.ansicht === reiter;
    link.classList.toggle("aktiv", aktiv);
    if (aktiv) link.setAttribute("aria-current", "page"); else link.removeAttribute("aria-current");
  });
  zeigeKopf(a);
  if (systemDaten) {
    if (a.ansicht === "uebersicht") zeigeUebersicht(systemDaten, cStatus);
    if (a.ansicht === "bots") zeigeBotListe(systemDaten);
    if (a.ansicht === "bot") zeigeBotSeite(a, systemDaten, cStatus);
    if (a.ansicht === "system") zeigeSystem(systemDaten);
    if (a.ansicht === "systemteil") zeigeSystemteil(a.id, systemDaten);
  }
  if (nachOben) window.scrollTo(0, 0);
}

// ---------------------------------------------------------------- Kopf
function zeigeKopf(a) {
  const detail = a.ansicht === "bot" || a.ansicht === "systemteil";
  $("kopf-gross").hidden = detail;
  $("zurueck").hidden = !detail;
  if (detail) {
    $("zurueck").href = a.ansicht === "bot" ? "#bots" : "#system";
    $("zurueck").textContent = a.ansicht === "bot" ? "‹ Bots" : "‹ System";
    return;
  }
  $("seitentitel").textContent = TITEL[a.ansicht];
  const pille = $("kopf-pille");
  pille.hidden = true;
  pille.innerHTML = "";
  if (!systemDaten) return;
  const kaesten = systemDaten.kaesten || [];
  if (a.ansicht === "uebersicht") {
    const konto = kastenMitId("ibkr");
    $("untertitel").textContent = `QuantDesk · ${wertAusZeilen(konto, "Konto") || "–"}`;
    pille.hidden = false;
    pille.className = "pille " + (konto && konto.paper ? "paper" : konto && konto.stufe === "rot" ? "echt" : "");
    pille.textContent = konto && konto.paper ? "PAPER" : konto && konto.stufe === "rot" ? "NICHT PAPER" : "PAPER ?";
  }
  if (a.ansicht === "bots") {
    const bots = kaesten.filter((k) => k.id.startsWith("bot-"));
    const offen = bots.filter((k) => k.stufe === "grau").length;
    $("untertitel").textContent = `${bots.length - offen} aktiv` + (offen ? ` · ${offen} noch nicht eingerichtet` : "");
  }
  if (a.ansicht === "system") {
    $("untertitel").textContent = `Geprüft ${wochentagZeit(systemDaten.erzeugt).split(" ")[1]} ${zoneKuerzel(systemDaten.erzeugt)}`;
    const ampel = systemDaten.ampel || { stufe: "grau", satz: "–" };
    pille.hidden = false;
    pille.className = `pille ${ampel.stufe}`;
    pille.appendChild(punkt(ampel.stufe));
    pille.appendChild(document.createTextNode(ampel.satz));
  }
}

function zeigeWarnungen(daten) {
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

async function holeJson(url) {
  const antwort = await fetch(url, { cache: "no-store" });
  return antwort.json();
}

async function ladeAlles() {
  try {
    const [system, status] = await Promise.all([holeJson("/api/system"), holeJson("/api/status")]);
    cStatus = status.missing ? null : status;
    // Ist die Notbremse gezogen, darf nirgends "Alles läuft" stehen (Kopf-Pille, Übersicht, Systemkarte)
    if (system.halt && system.halt.aktiv) system.ampel = NOTBREMSE_AMPEL;
    zeigeNotbremse(system);  // notbremse.js – auch wenn die Sammler-Daten fehlen
    if (system.fehlt) {
      ladeFehler = system.grund || "Keine Daten.";
    } else {
      systemDaten = system;
      ladeFehler = "";
      zeigeWarnungen(system);
    }
    zeigeAktuelleSeite();
  } catch (e) {
    ladeFehler = "Dashboard nicht erreichbar (Tailscale an?).";
  }
  alterPruefen();
}

window.addEventListener("hashchange", () => zeigeAktuelleSeite(true));
window.addEventListener("DOMContentLoaded", () => {
  zeigeAktuelleSeite();
  ladeAlles();
  setInterval(ladeAlles, NEU_LADEN_SEKUNDEN * 1000);
  setInterval(alterPruefen, 10 * 1000);
  if ("serviceWorker" in navigator) navigator.serviceWorker.register("/sw.js");
});
