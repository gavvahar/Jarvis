/* ===========================================================
   VIGIL LOCK — device-camera lock (Vigil Mode add-on)
   Uses this device's own webcam (getUserMedia — not a network
   camera) to check who's in front of the screen while Vigil Mode
   is ARMED. If a face is matched that isn't the logged-in user's,
   and the logged-in user's own face isn't in the same frame, for
   3 consecutive checks (~15s), the JARVIS UI is blanked until the
   logged-in user's face reappears alone (or they re-login).
   In-app lock only — no browser API can trigger a real OS lock.
   =========================================================== */
import { $, socket } from "./core.js";

const CAPTURE_INTERVAL_MS = 5000;
const MISMATCH_THRESHOLD = 3;

const overlayEl = $("vigil-lock");
const statusEl = $("vigil-lock-status");
const loginBtn = $("vigil-lock-login-btn");

let ownUserId = null;
let faceEnrolled = false;
let vigilMode = "auto";
let stream = null;
let videoEl = null;
let canvasEl = null;
let captureTimer = null;
let mismatchCount = 0;
let locked = false;
let capturing = false;

function shouldCapture() {
  return (
    vigilMode === "armed" &&
    faceEnrolled &&
    !!ownUserId &&
    document.visibilityState === "visible"
  );
}

async function startCapture() {
  if (capturing) return;
  capturing = true;
  try {
    stream = await navigator.mediaDevices.getUserMedia({ video: true });
  } catch (err) {
    console.warn("[vigil-lock] camera unavailable, lock disabled", err);
    capturing = false;
    return;
  }
  videoEl = document.createElement("video");
  videoEl.srcObject = stream;
  videoEl.muted = true;
  videoEl.playsInline = true;
  videoEl.style.display = "none";
  document.body.appendChild(videoEl);
  await videoEl.play().catch(() => {});
  canvasEl = document.createElement("canvas");
  captureTimer = setInterval(captureFrame, CAPTURE_INTERVAL_MS);
}

function stopCapture() {
  if (captureTimer) clearInterval(captureTimer);
  captureTimer = null;
  if (stream) stream.getTracks().forEach((t) => t.stop());
  stream = null;
  if (videoEl) videoEl.remove();
  videoEl = null;
  canvasEl = null;
  capturing = false;
  mismatchCount = 0;
}

function updateCaptureState() {
  if (shouldCapture()) {
    startCapture();
  } else {
    stopCapture();
    if (locked) unlock();
  }
}

function captureFrame() {
  if (!videoEl || !videoEl.videoWidth) return;
  canvasEl.width = videoEl.videoWidth;
  canvasEl.height = videoEl.videoHeight;
  canvasEl.getContext("2d").drawImage(videoEl, 0, 0);
  canvasEl.toBlob(
    (blob) => {
      if (blob) checkFrame(blob);
    },
    "image/jpeg",
    0.7,
  );
}

async function checkFrame(blob) {
  let faces = [];
  try {
    const fd = new FormData();
    fd.append("image", blob, "frame.jpg");
    const r = await fetch("/api/face/check-presence", {
      method: "POST",
      body: fd,
    });
    ({ faces } = await r.json());
  } catch {
    return;
  }
  evaluate(faces || [], blob);
}

function evaluate(faces, blob) {
  const ownPresent = faces.some((f) => f.detected_user_id === ownUserId);

  if (locked) {
    // auto-unlock only when the logged-in user reappears alone in frame
    if (ownPresent && faces.length === 1) unlock();
    return;
  }

  if (ownPresent) {
    mismatchCount = 0;
    return;
  }
  if (faces.length === 0) return; // no one in frame at all — inconclusive

  mismatchCount++;
  if (mismatchCount >= MISMATCH_THRESHOLD) lock(blob);
}

function lock(blob) {
  locked = true;
  mismatchCount = 0;
  if (overlayEl) overlayEl.classList.remove("setup-hidden");
  if (statusEl) statusEl.textContent = "Watching for your face to resume…";
  reportLockEvent(blob);
}

function unlock() {
  locked = false;
  mismatchCount = 0;
  if (overlayEl) overlayEl.classList.add("setup-hidden");
}

async function reportLockEvent(blob) {
  try {
    const fd = new FormData();
    if (blob) fd.append("image", blob, "frame.jpg");
    await fetch("/api/face/lock-event", { method: "POST", body: fd });
  } catch {
    /* best-effort — the lock itself doesn't depend on this succeeding */
  }
}

if (loginBtn) {
  loginBtn.addEventListener("click", () => {
    window.location.href = "/login";
  });
}

document.addEventListener("visibilitychange", updateCaptureState);

socket.on("vigil_mode_changed", ({ mode }) => {
  vigilMode = mode;
  updateCaptureState();
});

fetch("/api/status")
  .then((r) => r.json())
  .then((d) => {
    ownUserId = d.user_id || null;
    faceEnrolled = !!d.face_enrolled;
    return fetch("/api/vigil-mode");
  })
  .then((r) => r.json())
  .then(({ mode }) => {
    vigilMode = mode || "auto";
    updateCaptureState();
  })
  .catch(() => {});
