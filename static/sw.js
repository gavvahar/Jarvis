// Minimal service worker: its presence is what makes Jarvis installable as a
// PWA. It also cache-first serves the static app shell (CSS/JS/vendor) so the
// UI keeps rendering on a flaky connection. Full offline command queuing is a
// separate roadmap item.
const CACHE_NAME = "jarvis-static-v1";

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches
      .keys()
      .then((names) => Promise.all(names.filter((name) => name !== CACHE_NAME).map((name) => caches.delete(name))))
      .then(() => self.clients.claim()),
  );
});

self.addEventListener("fetch", (event) => {
  const url = new URL(event.request.url);
  const isStaticAsset = url.origin === self.location.origin && url.pathname.startsWith("/static/v2/");
  if (event.request.method !== "GET" || !isStaticAsset) return;

  event.respondWith(
    caches.match(event.request, { ignoreSearch: true }).then(
      (cached) =>
        cached ||
        fetch(event.request).then((response) => {
          const copy = response.clone();
          caches.open(CACHE_NAME).then((cache) => cache.put(event.request, copy));
          return response;
        }),
    ),
  );
});
