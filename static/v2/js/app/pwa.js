/* ===========================================================
   PWA — registers the service worker that makes Jarvis
   installable to a phone's home screen, and handles Web Push
   subscription (used today by Sentry Mode security alerts).
   =========================================================== */
if ("serviceWorker" in navigator) {
  window.addEventListener("load", () => {
    navigator.serviceWorker.register("/sw.js").catch((err) => {
      console.warn("[pwa] service worker registration failed", err);
    });
  });
}

function urlBase64ToUint8Array(base64String) {
  const padding = "=".repeat((4 - (base64String.length % 4)) % 4);
  const base64 = (base64String + padding)
    .replace(/-/g, "+")
    .replace(/_/g, "/");
  const raw = window.atob(base64);
  return Uint8Array.from([...raw].map((c) => c.charCodeAt(0)));
}

export async function subscribePush() {
  if (!("serviceWorker" in navigator) || !("PushManager" in window)) {
    return { ok: false, error: "Push notifications aren't supported in this browser." };
  }
  const permission = await Notification.requestPermission();
  if (permission !== "granted") {
    return { ok: false, error: "Notification permission denied." };
  }
  try {
    const statusRes = await fetch("/api/status");
    const { vapid_public_key } = await statusRes.json();
    if (!vapid_public_key) {
      return { ok: false, error: "Push isn't configured on the server yet." };
    }
    const reg = await navigator.serviceWorker.ready;
    const sub = await reg.pushManager.subscribe({
      userVisibleOnly: true,
      applicationServerKey: urlBase64ToUint8Array(vapid_public_key),
    });
    const json = sub.toJSON();
    await fetch("/api/push/subscribe", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ endpoint: json.endpoint, keys: json.keys }),
    });
    return { ok: true };
  } catch (err) {
    return { ok: false, error: err.message || "Failed to subscribe to push." };
  }
}
