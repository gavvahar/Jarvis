/* ===========================================================
   PHONE MESSAGES — alert toast + webhook settings panel
   =========================================================== */
import { $, socket } from "./core.js";

function showMessageToast(sender, text, reason) {
  const existing = document.getElementById("msg-alert-toast");
  if (existing) existing.remove();
  const toast = document.createElement("div");
  toast.id = "msg-alert-toast";
  toast.innerHTML =
    '<div class="msg-toast-label">MESSAGE</div>' +
    '<div class="msg-toast-sender">' +
    sender.replace(/</g, "&lt;") +
    "</div>" +
    '<div class="msg-toast-text">' +
    text.replace(/</g, "&lt;").slice(0, 120) +
    "</div>" +
    '<div class="msg-toast-reason">' +
    reason.replace(/</g, "&lt;") +
    "</div>";
  toast.addEventListener("click", () => toast.remove());
  document.body.appendChild(toast);
  setTimeout(() => toast && toast.remove(), 12000);
}

socket.on("message_alert", ({ sender, text, reason }) => {
  showMessageToast(sender, text, reason);
});

const msgSettingsPanel = $("msg-settings");
const msgSettingsBtn = $("msg-settings-btn");
const msgSettingsClose = $("msg-settings-close");
const msgWebhookUrl = $("msg-webhook-url");
const msgWebhookToken = $("msg-webhook-token");
const msgCopyUrl = $("msg-copy-url");
const msgCopyToken = $("msg-copy-token");
const msgRegenToken = $("msg-regen-token");
const msgApkUrl = $("msg-apk-url");
const msgCopyApk = $("msg-copy-apk");

function openMsgSettings() {
  if (!msgSettingsPanel) return;
  msgSettingsPanel.classList.add("msg-settings-open");
  fetch("/api/messages/token")
    .then((r) => r.json())
    .then((d) => {
      if (msgWebhookUrl) msgWebhookUrl.value = d.url || "";
      if (msgWebhookToken) msgWebhookToken.value = d.token || "";
      if (msgApkUrl) msgApkUrl.value = d.apk_url || "";
    })
    .catch(() => {});
}

if (msgSettingsBtn) msgSettingsBtn.addEventListener("click", openMsgSettings);
if (msgSettingsClose)
  msgSettingsClose.addEventListener("click", () => {
    if (msgSettingsPanel)
      msgSettingsPanel.classList.remove("msg-settings-open");
  });
if (msgSettingsPanel)
  msgSettingsPanel.addEventListener("click", (e) => {
    if (e.target === msgSettingsPanel)
      msgSettingsPanel.classList.remove("msg-settings-open");
  });

if (msgCopyUrl)
  msgCopyUrl.addEventListener("click", () => {
    if (msgWebhookUrl)
      navigator.clipboard.writeText(msgWebhookUrl.value).catch(() => {});
  });
if (msgCopyToken)
  msgCopyToken.addEventListener("click", () => {
    if (msgWebhookToken)
      navigator.clipboard.writeText(msgWebhookToken.value).catch(() => {});
  });
if (msgCopyApk)
  msgCopyApk.addEventListener("click", () => {
    if (msgApkUrl)
      navigator.clipboard.writeText(msgApkUrl.value).catch(() => {});
  });
if (msgRegenToken)
  msgRegenToken.addEventListener("click", () => {
    fetch("/api/messages/token/regenerate", { method: "POST" })
      .then((r) => r.json())
      .then((d) => {
        if (msgWebhookToken) msgWebhookToken.value = d.token || "";
      })
      .catch(() => {});
  });

// Android sub-tabs (Jarvis App vs Macrodroid)
document.querySelectorAll(".msg-android-tab-bar .msg-tab").forEach((btn) => {
  btn.addEventListener("click", () => {
    document
      .querySelectorAll(".msg-android-tab-bar .msg-tab")
      .forEach((b) => b.classList.remove("msg-tab-active"));
    btn.classList.add("msg-tab-active");
    const target = btn.dataset.atab;
    document.querySelectorAll(".msg-android-content").forEach((el) => {
      el.classList.toggle("msg-tab-hidden", el.id !== `msg-atab-${target}`);
    });
  });
});

// Top-level Android/iOS tabs
document.querySelectorAll(".msg-tab").forEach((tab) => {
  tab.addEventListener("click", () => {
    document
      .querySelectorAll(".msg-tab")
      .forEach((t) => t.classList.remove("msg-tab-active"));
    document
      .querySelectorAll(".msg-tab-content")
      .forEach((c) => c.classList.add("msg-tab-hidden"));
    tab.classList.add("msg-tab-active");
    const target = document.getElementById("msg-tab-" + tab.dataset.tab);
    if (target) target.classList.remove("msg-tab-hidden");
  });
});
