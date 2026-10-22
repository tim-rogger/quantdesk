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
  // Die Ampel auf der Übersicht darf nicht "Alles läuft" sagen, solange die Bremse gezogen ist.
  // (Prüfung, weil die Übersicht parallel umgebaut wird – fehlt zeigeAmpel, bleibt wenigstens der rote Balken oben.)
  if (typeof zeigeAmpel === "function") {
    zeigeAmpel({ stufe: "rot", satz: "Notbremse aktiv", was: ["Beide Bots handeln nicht. Fortsetzen nur im Terminal."] });
  }
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
    msg.className = "klein " + (res.ok ? "ok" : "bad");
  } catch (e) {
    msg.textContent = "Server nicht erreichbar.";
    msg.className = "klein bad";
  }
  $("pin").value = "";
  ladeSystem();
  load();  // Strategie C (app.js) zeigt den Stopp ebenfalls an
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
