/* ===========================================================
   HA SETTINGS MODAL
   =========================================================== */
import { $ } from "./core.js";

const haSettingsEl = $("ha-settings");
const haSettingsBtn = $("ha-settings-btn");
const haSettingsForm = $("ha-settings-form");
const haUrlInput = $("ha-url");
export const haTokenInput = $("ha-token");
const haSaveBtn = $("ha-save");
const haCancelBtn = $("ha-cancel");
const haMsg = $("ha-msg");
const haStatusDot = $("ha-status-dot");
const haStatusText = $("ha-status-text");

export function setHaStatus(configured, url) {
  if (configured) {
    (haStatusDot &&
      haStatusDot.classList.replace("disconnected", "connected")) ||
      (haStatusDot && haStatusDot.classList.add("connected"));
    if (haStatusText) haStatusText.textContent = "CONNECTED";
    haSettingsBtn && haSettingsBtn.classList.add("ha-live");
  } else {
    haStatusDot && haStatusDot.classList.remove("connected");
    haStatusDot && haStatusDot.classList.add("disconnected");
    if (haStatusText) haStatusText.textContent = "NOT CONNECTED";
    haSettingsBtn && haSettingsBtn.classList.remove("ha-live");
  }
  if (haUrlInput && url) haUrlInput.value = url;
}

function showHaSettings() {
  if (haSettingsEl) haSettingsEl.classList.remove("setup-hidden");
  if (haMsg) {
    haMsg.textContent = "";
    haMsg.className = "";
  }
  setTimeout(() => haUrlInput && haUrlInput.focus(), 150);
}
function hideHaSettings() {
  if (haSettingsEl) haSettingsEl.classList.add("setup-hidden");
  if (haTokenInput) haTokenInput.value = "";
}

if (haSettingsBtn) haSettingsBtn.addEventListener("click", showHaSettings);
if (haCancelBtn) haCancelBtn.addEventListener("click", hideHaSettings);
haSettingsEl &&
  haSettingsEl.addEventListener("click", (e) => {
    if (e.target === haSettingsEl) hideHaSettings();
  });

if (haSettingsForm) {
  haSettingsForm.addEventListener("submit", async (e) => {
    e.preventDefault();
    const ha_url = (haUrlInput.value || "").trim();
    const ha_token = (haTokenInput.value || "").trim();
    if (ha_url && !ha_token && !haTokenInput.dataset.hasExisting) {
      haMsg.className = "err";
      haMsg.textContent = "Please provide a Long-Lived Access Token.";
      return;
    }
    haSaveBtn.disabled = true;
    haMsg.className = "";
    haMsg.textContent = "Verifying…";
    try {
      const res = await fetch("/api/save_ha", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ ha_url, ha_token }),
      });
      const data = await res.json();
      if (data.ok) {
        haMsg.className = "ok";
        haMsg.textContent = data.ha_configured
          ? "Connected. Home automation online."
          : "Home automation disconnected.";
        setHaStatus(data.ha_configured, ha_url);
        haTokenInput.dataset.hasExisting = data.ha_configured ? "1" : "";
        setTimeout(hideHaSettings, 1200);
      } else {
        haMsg.className = "err";
        haMsg.textContent = data.error || "Could not save settings.";
      }
    } catch {
      haMsg.className = "err";
      haMsg.textContent = "Could not reach the server.";
    } finally {
      haSaveBtn.disabled = false;
    }
  });
}
