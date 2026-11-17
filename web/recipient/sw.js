"use strict";

const CACHE = "rakshanetra-recipient-v21";
const ROOT = "/recipient/";
const SHELL = [ROOT, ...["alarm.js", "app.js", "scanner.js", "style.css", "brand.png", "icon-192.png", "manifest.webmanifest"]
  .map(asset => ROOT + asset)];

self.addEventListener("install", event => {
  event.waitUntil(caches.open(CACHE).then(cache => cache.addAll(SHELL)).then(() => self.skipWaiting()));
});
self.addEventListener("activate", event => {
  event.waitUntil(caches.keys().then(keys => Promise.all(keys
    .filter(key => key.startsWith("rakshanetra-recipient-") && key !== CACHE)
    .map(key => caches.delete(key)))).then(() => self.clients.claim()));
});
self.addEventListener("fetch", event => {
  const url = new URL(event.request.url);
  if (event.request.method === "GET" && url.origin === self.location.origin && SHELL.includes(url.pathname)) {
    event.respondWith(fetch(event.request).catch(async () => (await caches.match(url.pathname)) || Response.error()));
  }
});
self.addEventListener("push", event => {
  let data = {};
  try { data = event.data?.json() || {}; } catch (_) {}
  const id = /^[0-9a-f]{32}$/.test(data.incident || "") ? data.incident : "";
  event.waitUntil((async () => {
    let displayed = false;
    try {
      await self.registration.showNotification(data.title || "RakshaNetra alert", {
        body: data.body || "Open RakshaNetra to review the alert.",
        icon: ROOT + "icon-192.png",
        tag: id || "rakshanetra-alert",
        requireInteraction: true,
        renotify: true,
        vibrate: [500, 180, 500, 180, 800],
        actions: [{action: "open", title: "View incident"}, {action: "acknowledge", title: "Acknowledge"}],
        data: {incident: id}
      });
      displayed = true;
    } catch (_) {}
    await Promise.allSettled([
      reportReceipt(id, displayed ? "displayed" : "display-failed"),
      self.clients.matchAll({type: "window", includeUncontrolled: true}).then(clients => {
        for (const client of clients) client.postMessage?.({type: "alert", incident: id});
      })
    ]);
  })());
});

async function reportReceipt(incident, status) {
  if (!/^[0-9a-f]{32}$/.test(incident)) return;
  try {
    await fetch("/api/recipient/notification/receipt", {
      method: "POST", credentials: "same-origin",
      headers: {"Content-Type": "application/json", "X-Raksha-Recipient": "1"},
      body: JSON.stringify({incident, status}), signal: AbortSignal.timeout(5000)
    });
  } catch (_) {}
}
self.addEventListener("notificationclick", event => {
  event.notification.close();
  const id = event.notification.data?.incident;
  const url = new URL(ROOT + (/^[0-9a-f]{32}$/.test(id || "") ? "#incident=" + id : ""), self.location.origin).href;
  event.waitUntil((async () => {
    await reportReceipt(id, "opened");
    if (event.action === "acknowledge" && /^[0-9a-f]{32}$/.test(id || "")) {
      try {
        const response = await fetch("/api/recipient/incidents/" + id + "/review", {
          method: "POST", credentials: "same-origin",
          headers: {"Content-Type": "application/json", "X-Raksha-Recipient": "1"},
          body: JSON.stringify({action: "acknowledged"}), signal: AbortSignal.timeout(5000)
        });
        if (response.ok) {
          const windows = await self.clients.matchAll({type: "window", includeUncontrolled: true});
          windows.forEach(client => client.postMessage?.({type: "acknowledged", incident: id}));
          return;
        }
      } catch (_) {}
    }
    const clients = await self.clients.matchAll({type: "window", includeUncontrolled: true});
    const recipient = clients.find(client => new URL(client.url).pathname === ROOT);
    if (recipient) {
      try {
        const view = await recipient.navigate(url);
        if (view) {
          await view.focus();
          return;
        }
      } catch (_) {
        // A closing tab may disappear while the notification is opened.
      }
    }
    await self.clients.openWindow(url);
  })());
});
