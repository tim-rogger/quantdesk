// Service-Worker: App-Hülle offline verfügbar, Daten immer frisch vom Server (mit Rückfall auf den letzten Stand).
// CACHE erhöhen, wenn sich die Liste ändert. Die Adressen müssen genau so in index.html stehen (mit ?v=…).
const CACHE = "quantdesk-v9";
const SHELL = ["/", "/static/style.css?v=9", "/static/kern.js?v=9", "/static/uebersicht.js?v=9", "/static/bots.js?v=9",
  "/static/system.js?v=9", "/static/app.js?v=9", "/static/notbremse.js?v=9", "/static/icon.svg", "/static/icon-180.png", "/static/icon-192.png",
  "/manifest.webmanifest"];

self.addEventListener("install", (e) => {
  e.waitUntil(caches.open(CACHE).then((c) => c.addAll(SHELL)).then(() => self.skipWaiting()));
});

self.addEventListener("activate", (e) => {
  e.waitUntil(caches.keys().then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k))))
    .then(() => self.clients.claim()));
});

self.addEventListener("fetch", (e) => {
  const url = new URL(e.request.url);
  if (e.request.method !== "GET" || url.origin !== location.origin) return;
  // Immer zuerst das Netz (so kommen Updates sofort an), offline der zuletzt gespeicherte Stand
  e.respondWith(fetch(e.request).then((res) => {
    if (res.ok) {
      const copy = res.clone();
      caches.open(CACHE).then((c) => c.put(e.request, copy));
    }
    return res;
  }).catch(() => caches.match(e.request)));
});
