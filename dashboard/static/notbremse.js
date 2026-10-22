"use strict";
// Notbremse für BEIDE Bots. Sie kann NUR anhalten: Das Dashboard legt eine Markierung ab, Bot C und Lotse lesen sie
// vor jedem Lauf, stornieren ihre eigenen offenen Orders und platzieren keine neuen. Nie verkaufen, nie fortsetzen –
// Fortsetzen geht nur im Terminal auf dem Server.
// Zwei Schritte, damit man sie nicht aus Versehen auslöst: PIN + "Notbremse ziehen …" → Bestätigen → erst dann die Anfrage.

function zeigeNotbremse(daten) {
  const halt = daten.halt || { aktiv: false };
  const banner = $("notbremse-banner");
  banner.hidden = !halt.aktiv;
  banner.textContent = halt.aktiv
    ? `Notbremse aktiv seit ${zeit(halt.seit)} – beide Bots handeln nicht. Fortsetzen nur im Terminal.` : "";
  if (!halt.aktiv) return;
  $("stop-form").hidden = true;
  $("notbremse-bestaetigen").hidden = true;
  const stand = $("notbremse-stand");
  stand.textContent = `Aktiv seit ${zeit(halt.seit)}. Fortsetzen nur im Terminal auf dem Server:`;
  stand.appendChild(el("code", { class: "befehl" }, halt.fortsetzen || ""));
  // Die Ampel (Kopf, Übersicht, Systemkarte) setzt kern.js bei gezogener Bremse auf rot "Notbremse aktiv".
}

function bestaetigenZeigen(zeigen) {
  $("notbremse-bestaetigen").hidden = !zeigen;
  $("stop-form").hidden = zeigen;
}

async function bremseZiehen() {
  const msg = $("stop-msg");
  bestaetigenZeigen(false);
  try {
    const res = await fetch("/api/stop", { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ pin: $("pin").value }) });
    const body = await res.json();
    msg.textContent = res.ok ? body.message : (body.detail || "Fehler");
    msg.className = "klein " + (res.ok ? "positiv" : "negativ");
  } catch (e) {
    msg.textContent = "Server nicht erreichbar.";
    msg.className = "klein negativ";
  }
  $("pin").value = "";
  ladeAlles();
}

$("stop-form").addEventListener("submit", (ev) => {
  ev.preventDefault();  // Schritt 1 sendet noch nichts
  if ($("pin").value.trim()) bestaetigenZeigen(true);
});
$("notbremse-abbrechen").addEventListener("click", () => {
  $("pin").value = "";
  bestaetigenZeigen(false);
});
$("notbremse-ja").addEventListener("click", bremseZiehen);
