/* ===========================================================
   DOORBELL & SECURITY ALERTS + DOORBELL SETTINGS PANEL
   =========================================================== */
import { $, socket, speak, wake, isStandby } from "./core.js";

const DOORBELL_LABELS = {
  doorbell_press: "DOORBELL",
  motion: "MOTION DETECTED",
  person: "PERSON DETECTED",
  package: "PACKAGE DELIVERED",
};

function showDoorbellToast(event_type, speak_text) {
  const existing = document.getElementById("doorbell-toast");
  if (existing) existing.remove();
  const toast = document.createElement("div");
  toast.id = "doorbell-toast";
  const label = DOORBELL_LABELS[event_type] || "SECURITY ALERT";
  toast.innerHTML =
    '<div class="doorbell-toast-label">' +
    label +
    "</div>" +
    '<div class="doorbell-toast-text">' +
    speak_text.replace(/</g, "&lt;") +
    "</div>";
  toast.addEventListener("click", () => toast.remove());
  document.body.appendChild(toast);
  setTimeout(() => toast && toast.remove(), 10000);
}

socket.on("timer_fired", ({ label, speak: speakText }) => {
  const msg = speakText || `Your ${label} timer is done.`;
  if (window.__chat) window.__chat.addMsg(msg, "in");
  if (isStandby()) wake();
  speak(msg);
});

socket.on("reminder_fired", ({ text, speak: speakText }) => {
  const msg = speakText || `Reminder: ${text}.`;
  if (window.__chat) window.__chat.addMsg(msg, "in");
  if (isStandby()) wake();
  speak(msg);
});

socket.on("device_alert", ({ name, message, speak: speakText }) => {
  const msg = speakText || message || "Device alert.";
  if (window.__chat) window.__chat.addMsg(msg, "in");
  if (isStandby()) wake();
  speak(msg);
});

socket.on("briefing_ready", ({ text, speak: speakText }) => {
  const msg = speakText || text || "Here's your briefing.";
  if (window.__chat) window.__chat.addMsg(msg, "in");
  if (isStandby()) wake();
  speak(msg);
});

socket.on("habit_nudge", ({ speak: speakText }) => {
  const msg = speakText || "Just a heads up, sir.";
  if (window.__chat) window.__chat.addMsg(msg, "in");
  if (isStandby()) wake();
  speak(msg);
});

socket.on("travel_alert", ({ speak: speakText }) => {
  const msg = speakText || "Flight status update.";
  if (window.__chat) window.__chat.addMsg(msg, "in");
  if (isStandby()) wake();
  speak(msg);
});

socket.on("email_alert", ({ speak: speakText }) => {
  const msg = speakText || "You have an urgent email.";
  if (window.__chat) window.__chat.addMsg(msg, "in");
  if (isStandby()) wake();
  speak(msg);
});

socket.on("wake_trigger", ({ device_id }) => {
  if (isStandby()) {
    wake();
    const acks = [
      "Yes, sir?",
      "Sir?",
      "Go ahead.",
      "At your service.",
      "Right here, sir.",
      "You rang, sir?",
    ];
    const a = acks[Math.floor(Math.random() * acks.length)];
    if (window.__chat) window.__chat.addMsg(a, "in");
    speak(a);
  }
});

socket.on(
  "security_alert",
  ({ event_type, camera, room, speak: speakText }) => {
    const msg = speakText || "Security alert.";
    const existing = document.getElementById("security-alert-toast");
    if (existing) existing.remove();
    const toast = document.createElement("div");
    toast.id = "security-alert-toast";
    toast.className = "doorbell-toast";
    toast.style.borderColor = "#ef4444";
    const loc = room ? ` — ${room}` : camera ? ` — ${camera}` : "";
    toast.innerHTML =
      '<div class="doorbell-toast-label" style="color:#ef4444">SECURITY ALERT' +
      loc.toUpperCase() +
      "</div>" +
      '<div class="doorbell-toast-text">' +
      msg.replace(/</g, "&lt;") +
      "</div>";
    toast.addEventListener("click", () => toast.remove());
    document.body.appendChild(toast);
    setTimeout(() => toast && toast.remove(), 12000);
    if (!isStandby()) speak(msg);
    const btn = $("vision-btn");
    if (btn) {
      btn.classList.add("doorbell-active");
      setTimeout(() => btn.classList.remove("doorbell-active"), 8000);
    }
  },
);

socket.on("doorbell_alert", ({ event_type, speak: speakText }) => {
  const msg = speakText || "Doorbell alert.";
  showDoorbellToast(event_type, msg);
  if (!isStandby()) speak(msg);
  const btn = $("doorbell-btn");
  if (btn) {
    btn.classList.add("doorbell-active");
    setTimeout(() => btn.classList.remove("doorbell-active"), 8000);
  }
});

// ─── DOORBELL PANEL ───────────────────────────────────────────────────────
const doorbellSettingsEl = $("doorbell-settings");
const doorbellBtn = $("doorbell-btn");
const doorbellSettingsClose = $("doorbell-settings-close");
const doorbellWebhookUrl = $("doorbell-webhook-url");
const doorbellWebhookToken = $("doorbell-webhook-token");
const doorbellCopyUrl = $("doorbell-copy-url");
const doorbellCopyToken = $("doorbell-copy-token");

function buildDoorbellYaml(eventType, webhookUrl, token) {
  const entityHints = {
    doorbell_press: "event.YOUR_DOORBELL   # e.g. event.front_door_doorbell",
    motion: "binary_sensor.YOUR_MOTION  # e.g. binary_sensor.front_door_motion",
    person: "binary_sensor.YOUR_PERSON  # e.g. binary_sensor.front_door_person",
    package:
      "binary_sensor.YOUR_PACKAGE # e.g. binary_sensor.front_door_package",
  };
  const triggerPlatform =
    eventType === "doorbell_press"
      ? "  - platform: state\n    entity_id: " + entityHints[eventType]
      : "  - platform: state\n    entity_id: " +
        entityHints[eventType] +
        '\n    to: "on"';
  return (
    "# Add to configuration.yaml:\n" +
    "rest_command:\n" +
    "  jarvis_doorbell_event:\n" +
    '    url: "' +
    webhookUrl +
    '"\n' +
    "    method: POST\n" +
    "    headers:\n" +
    '      Authorization: "Bearer ' +
    token +
    '"\n' +
    '    payload: \'{"event_type": "' +
    eventType +
    "\"}'\n" +
    '    content_type: "application/json"\n\n' +
    "# Automation:\n" +
    'alias: "Jarvis — ' +
    eventType.replace("_", " ").toUpperCase() +
    '"\n' +
    "trigger:\n" +
    triggerPlatform +
    "\n" +
    "action:\n" +
    "  - action: rest_command.jarvis_doorbell_event"
  );
}

function openDoorbellSettings() {
  if (!doorbellSettingsEl) return;
  doorbellSettingsEl.classList.add("doorbell-settings-open");
  fetch("/api/doorbell/token")
    .then((r) => r.json())
    .then((d) => {
      const url = d.url || "";
      const token = d.token || "";
      if (doorbellWebhookUrl) doorbellWebhookUrl.value = url;
      if (doorbellWebhookToken) doorbellWebhookToken.value = token;
      ["press", "motion", "person", "package"].forEach((type) => {
        const el = $("yaml-" + type);
        if (el)
          el.textContent = buildDoorbellYaml(
            type === "press" ? "doorbell_press" : type,
            url,
            token,
          );
      });
    })
    .catch(() => {});
}

function closeDoorbellSettings() {
  if (doorbellSettingsEl)
    doorbellSettingsEl.classList.remove("doorbell-settings-open");
}

if (doorbellBtn) doorbellBtn.addEventListener("click", openDoorbellSettings);
if (doorbellSettingsClose)
  doorbellSettingsClose.addEventListener("click", closeDoorbellSettings);
if (doorbellSettingsEl)
  doorbellSettingsEl.addEventListener("click", (e) => {
    if (e.target === doorbellSettingsEl) closeDoorbellSettings();
  });

if (doorbellCopyUrl)
  doorbellCopyUrl.addEventListener("click", () => {
    if (doorbellWebhookUrl)
      navigator.clipboard.writeText(doorbellWebhookUrl.value).catch(() => {});
  });
if (doorbellCopyToken)
  doorbellCopyToken.addEventListener("click", () => {
    if (doorbellWebhookToken)
      navigator.clipboard.writeText(doorbellWebhookToken.value).catch(() => {});
  });

document.querySelectorAll(".doorbell-tab").forEach((tab) => {
  tab.addEventListener("click", () => {
    document
      .querySelectorAll(".doorbell-tab")
      .forEach((t) => t.classList.remove("doorbell-tab-active"));
    document
      .querySelectorAll(".doorbell-tab-content")
      .forEach((c) => c.classList.add("doorbell-tab-hidden"));
    tab.classList.add("doorbell-tab-active");
    const target = document.getElementById("doorbell-tab-" + tab.dataset.dtab);
    if (target) target.classList.remove("doorbell-tab-hidden");
  });
});
