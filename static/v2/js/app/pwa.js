/* ===========================================================
   PWA — registers the service worker that makes Jarvis
   installable to a phone's home screen.
   =========================================================== */
if ("serviceWorker" in navigator) {
  window.addEventListener("load", () => {
    navigator.serviceWorker.register("/sw.js").catch((err) => {
      console.warn("[pwa] service worker registration failed", err);
    });
  });
}
