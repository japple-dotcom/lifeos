/* Service worker: makes Life OS installable and lets the app open instantly, even on a weak signal.
   Only the app's own files are cached; household data (/api) is never stored on the phone. */
const CACHE = "lifeos-shell-v23";
const SHELL = ["./", "index.html", "styles.css", "app.js", "manifest.webmanifest", "wall.html", "wall.css", "wall.js", "kids.html", "kids.js",
  "more.js", "sitter.html", "sitter.js", "sitter.css", "extra.js", "week.html", "week.js", "week.css",
  "icons/icon-192.png", "icons/icon-512.png", "icons/icon-maskable-512.png", "icons/apple-touch-icon.png"];

self.addEventListener("install", (e) => {
  e.waitUntil(caches.open(CACHE).then((c) => c.addAll(SHELL)).then(() => self.skipWaiting()));
});

self.addEventListener("activate", (e) => {
  e.waitUntil(caches.keys().then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k))))
    .then(() => self.clients.claim()));
});

self.addEventListener("fetch", (e) => {
  const url = new URL(e.request.url);
  if (e.request.method !== "GET" || url.origin !== location.origin || !url.pathname.startsWith("/app/")) return;
  // Network first so updates show straight away; fall back to the cached copy when offline.
  e.respondWith(
    fetch(e.request, { cache: "no-cache" }).then((res) => {
      const copy = res.clone();
      caches.open(CACHE).then((c) => c.put(e.request, copy));
      return res;
    }).catch(() => caches.match(e.request).then((r) => r || caches.match("index.html")))
  );
});

// ---------- notifications ----------
self.addEventListener("push", (e) => {
  let data = {};
  try { data = e.data ? e.data.json() : {}; } catch { data = { body: e.data && e.data.text() }; }
  const actions = (data.actions || []).map((a) => ({ action: a.action, title: a.title }));
  e.waitUntil(self.registration.showNotification(data.title || "Life OS", {
    body: data.body || "",
    icon: "icons/icon-192.png",
    badge: "icons/icon-192.png",
    tag: data.actions && data.actions.length ? undefined : "lifeos",
    requireInteraction: !!(data.actions && data.actions.length),
    actions,
    data: { url: data.url || "/app/#today", actions: data.actions || [] },
  }));
});

self.addEventListener("notificationclick", (e) => {
  const n = e.notification;
  n.close();
  const chosen = (n.data.actions || []).find((a) => a.action === e.action);
  if (chosen) {
    // Approve / Reject straight from the notification, then confirm with a second notification.
    e.waitUntil(fetch(chosen.url, { method: "POST" }).then((r) =>
      self.registration.showNotification(r.ok ? (e.action === "approve" ? "Approved" : "Rejected") : "That didn't work",
        { body: r.ok ? n.body.split("\n")[0] : "Open Life OS to try again.", icon: "icons/icon-192.png", tag: "lifeos-result" })
    ).catch(() => self.registration.showNotification("No connection", { body: "Open Life OS to try again.", tag: "lifeos-result" })));
    return;
  }
  e.waitUntil(clients.matchAll({ type: "window", includeUncontrolled: true }).then((list) => {
    for (const c of list) if (c.url.includes("/app/")) { c.navigate(n.data.url); return c.focus(); }
    return clients.openWindow(n.data.url);
  }));
});
