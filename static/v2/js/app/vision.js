/* ===========================================================
   VISION SETTINGS PANEL — cameras, presence, face enrollment
   =========================================================== */
import { $, socket } from "./core.js";
import { subscribePush } from "./pwa.js";

const visionSettingsEl = $("vision-settings");
const visionBtn = $("vision-btn");
const visionClose = $("vision-settings-close");
const visionCameraList = $("vision-camera-list");
const visionPresenceList = $("vision-presence-list");
const visionAddForm = $("vision-add-camera-form");
const visionEnrollBtn = $("vision-enroll-btn");
const visionEnrollClear = $("vision-enroll-clear-btn");
const visionFaceFile = $("vision-face-file");
const visionEnrollStatus = $("vision-enroll-status");
const visionSentryButtons = document.querySelectorAll(".vision-sentry-btn");
const visionEnablePushBtn = $("vision-enable-push-btn");
const visionPushStatus = $("vision-push-status");
const visionSecurityEvents = $("vision-security-events");

async function loadCameras() {
  if (!visionCameraList) return;
  try {
    const r = await fetch("/api/cameras");
    const { cameras } = await r.json();
    if (!cameras.length) {
      visionCameraList.innerHTML = "<em>No cameras configured.</em>";
      return;
    }
    visionCameraList.innerHTML = cameras
      .map(
        (c) =>
          `<div class="vision-cam-row">
        <span>${c.name} <small>(${c.source_type}:${c.source}${c.room ? " · " + c.room : ""})</small></span>
        <span class="vision-cam-badges">
          ${c.enabled ? "" : '<span class="vision-badge">OFF</span>'}
          ${c.privacy ? '<span class="vision-badge vision-badge-priv">PRIVATE</span>' : ""}
        </span>
        <button class="vision-cam-del" data-id="${c.id}">✕</button>
      </div>`,
      )
      .join("");
    visionCameraList.querySelectorAll(".vision-cam-del").forEach((btn) => {
      btn.addEventListener("click", async () => {
        await fetch(`/api/cameras/${btn.dataset.id}`, { method: "DELETE" });
        loadCameras();
      });
    });
  } catch {
    visionCameraList.innerHTML = "<em>Could not load cameras.</em>";
  }
}

async function loadPresence() {
  if (!visionPresenceList) return;
  try {
    const r = await fetch("/api/presence");
    const { members } = await r.json();
    if (!members || !members.length) {
      visionPresenceList.innerHTML = "<em>No one detected home.</em>";
      return;
    }
    visionPresenceList.innerHTML = members
      .map((m) => {
        const where = m.room ? ` &mdash; ${m.room}` : "";
        const activity =
          m.activity && m.activity !== "home"
            ? ` <small>(${m.activity})</small>`
            : "";
        return `<div class="vision-presence-row"><span class="vision-presence-dot"></span><span>${m.name}${where}${activity}</span></div>`;
      })
      .join("");
  } catch {
    visionPresenceList.innerHTML = "<em>Could not load presence.</em>";
  }
}

function setSentryModeUI(mode) {
  visionSentryButtons.forEach((btn) => {
    btn.classList.toggle("active", btn.dataset.mode === mode);
  });
}

async function loadSentryMode() {
  if (!visionSentryButtons.length) return;
  try {
    const r = await fetch("/api/sentry-mode");
    const { mode } = await r.json();
    setSentryModeUI(mode);
  } catch {
    /* leave buttons in their last-known state */
  }
}

visionSentryButtons.forEach((btn) => {
  btn.addEventListener("click", async () => {
    const mode = btn.dataset.mode;
    setSentryModeUI(mode);
    try {
      await fetch("/api/sentry-mode", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ mode }),
      });
    } catch {
      loadSentryMode();
    }
  });
});

const SECURITY_EVENT_LABELS = {
  unknown_person: "UNKNOWN PERSON",
  motion: "MOTION",
};

async function loadSecurityEvents() {
  if (!visionSecurityEvents) return;
  try {
    const r = await fetch("/api/security-events?hours=24");
    const { events } = await r.json();
    if (!events || !events.length) {
      visionSecurityEvents.innerHTML = "<em>No events in the last 24h.</em>";
      return;
    }
    visionSecurityEvents.innerHTML = events
      .map((e) => {
        const label =
          SECURITY_EVENT_LABELS[e.event_type] || e.event_type.toUpperCase();
        const when = new Date(e.detected_at).toLocaleString();
        const thumb = e.has_snapshot
          ? `<img class="vision-event-thumb" src="/api/security-events/${e.id}/snapshot" loading="lazy" />`
          : `<div class="vision-event-thumb"></div>`;
        return `<div class="vision-event-row">
          ${thumb}
          <div class="vision-event-info">
            <span class="vision-event-type">${label}</span>
            <span class="vision-event-meta">${when}${e.room ? " · " + e.room : ""}</span>
          </div>
        </div>`;
      })
      .join("");
  } catch {
    visionSecurityEvents.innerHTML = "<em>Could not load events.</em>";
  }
}

if (visionEnablePushBtn) {
  visionEnablePushBtn.addEventListener("click", async () => {
    if (visionPushStatus) visionPushStatus.textContent = "Requesting…";
    const result = await subscribePush();
    if (visionPushStatus)
      visionPushStatus.textContent = result.ok
        ? "Push notifications enabled."
        : result.error;
  });
}

if (visionBtn) {
  visionBtn.addEventListener("click", () => {
    if (visionSettingsEl) {
      visionSettingsEl.classList.remove("setup-hidden");
      loadPresence();
      loadCameras();
      loadSentryMode();
      loadSecurityEvents();
    }
  });
}
if (visionClose)
  visionClose.addEventListener(
    "click",
    () => visionSettingsEl && visionSettingsEl.classList.add("setup-hidden"),
  );

if (visionAddForm) {
  visionAddForm.addEventListener("submit", async (e) => {
    e.preventDefault();
    const body = {
      name: $("vision-cam-name").value.trim(),
      source_type: $("vision-cam-source-type").value,
      source: $("vision-cam-source").value.trim(),
      room: $("vision-cam-room").value.trim(),
    };
    if (!body.name || !body.source) return;
    await fetch("/api/cameras", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    $("vision-cam-name").value = "";
    $("vision-cam-source").value = "";
    $("vision-cam-room").value = "";
    loadCameras();
  });
}

let _faceEmbeddings = [];
if (visionEnrollBtn) {
  visionEnrollBtn.addEventListener(
    "click",
    () => visionFaceFile && visionFaceFile.click(),
  );
}
if (visionFaceFile) {
  visionFaceFile.addEventListener("change", async () => {
    const file = visionFaceFile.files[0];
    if (!file) return;
    if (visionEnrollStatus) visionEnrollStatus.textContent = "Processing…";
    const fd = new FormData();
    fd.append("image", file);
    const r = await fetch("/api/face/enroll-sample", {
      method: "POST",
      body: fd,
    });
    const data = await r.json();
    if (!data.ok) {
      if (visionEnrollStatus)
        visionEnrollStatus.textContent = "Error: " + data.error;
      return;
    }
    _faceEmbeddings.push(data.embedding);
    const r2 = await fetch("/api/face/enroll-finish", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ embeddings: _faceEmbeddings }),
    });
    const d2 = await r2.json();
    if (visionEnrollStatus)
      visionEnrollStatus.textContent = d2.ok
        ? `Face enrolled (${_faceEmbeddings.length} sample${_faceEmbeddings.length > 1 ? "s" : ""}).`
        : "Enroll failed.";
    visionFaceFile.value = "";
  });
}
if (visionEnrollClear) {
  visionEnrollClear.addEventListener("click", async () => {
    await fetch("/api/face/enrollment", { method: "DELETE" });
    _faceEmbeddings = [];
    if (visionEnrollStatus)
      visionEnrollStatus.textContent = "Face data cleared.";
  });
}

socket.on("presence_update", ({ name, is_home, room }) => {
  const action = is_home ? "arrived" : "left";
  const where = room ? ` (${room})` : "";
  console.log(`[presence] ${name} ${action}${where}`);
  loadPresence();
});

socket.on("sentry_mode_changed", ({ mode }) => {
  setSentryModeUI(mode);
});

socket.on("security_alert", () => {
  if (
    visionSettingsEl &&
    !visionSettingsEl.classList.contains("setup-hidden")
  ) {
    loadSecurityEvents();
  }
});
