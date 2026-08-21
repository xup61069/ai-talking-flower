/* 花花 PWA：離線快取靜態資源 + 推播提醒 */
const CACHE = "flower-v1";
const ASSETS = ["./", "./index.html", "./theme.css", "./app.js", "./manifest.json"];

self.addEventListener("install", (e) => {
  e.waitUntil(caches.open(CACHE).then((c) => c.addAll(ASSETS)).catch(() => {}));
  self.skipWaiting();
});

self.addEventListener("activate", (e) => {
  e.waitUntil(
    caches.keys().then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k))))
  );
  self.clients.claim();
});

self.addEventListener("fetch", (e) => {
  const url = new URL(e.request.url);
  // API 走網路，不快取
  if (url.pathname.startsWith("/api/")) return;
  e.respondWith(
    caches.match(e.request).then((hit) => hit || fetch(e.request).catch(() => hit))
  );
});

self.addEventListener("push", (e) => {
  const data = e.data ? e.data.json() : { title: "花花提醒", body: "有新提醒到期囉！" };
  e.waitUntil(
    self.registration.showNotification(data.title || "花花", {
      body: data.body || "",
      icon: "data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 100 100'><text y='.9em' font-size='90'>🌸</text></svg>",
      tag: "flower-reminder",
    })
  );
});

self.addEventListener("message", (e) => {
  const data = e.data || {};
  self.registration.showNotification(data.title || "花花", {
    body: data.body || "",
    tag: "flower-reminder",
  });
});
