/* ===========================================================
   GARAGE SETTINGS MODAL (MYQ)
   =========================================================== */
import { $ } from "./core.js";

const garageSettingsEl = $("garage-settings");
const garageBtn = $("garage-btn");
const garageSettingsForm = $("garage-settings-form");
const myqEmailInput = $("myq-email");
export const myqPasswordInput = $("myq-password");
const garageSaveBtn = $("garage-save");
const garageCancelBtn = $("garage-cancel");
const garageMsg = $("garage-msg");
const garageStatusDot = $("garage-status-dot");
const garageStatusText = $("garage-status-text");

export function setGarageStatus(configured) {
  if (configured) {
    garageStatusDot && garageStatusDot.classList.add("connected");
    garageStatusDot && garageStatusDot.classList.remove("disconnected");
    if (garageStatusText) garageStatusText.textContent = "CONNECTED";
    garageBtn && garageBtn.classList.add("garage-live");
  } else {
    garageStatusDot && garageStatusDot.classList.remove("connected");
    garageStatusDot && garageStatusDot.classList.add("disconnected");
    if (garageStatusText) garageStatusText.textContent = "NOT CONNECTED";
    garageBtn && garageBtn.classList.remove("garage-live");
  }
}

function showGarageSettings() {
  if (garageSettingsEl) garageSettingsEl.classList.remove("setup-hidden");
  if (garageMsg) {
    garageMsg.textContent = "";
    garageMsg.className = "";
  }
  setTimeout(() => myqEmailInput && myqEmailInput.focus(), 150);
}
function hideGarageSettings() {
  if (garageSettingsEl) garageSettingsEl.classList.add("setup-hidden");
  if (myqPasswordInput) myqPasswordInput.value = "";
}

if (garageBtn) garageBtn.addEventListener("click", showGarageSettings);
if (garageCancelBtn)
  garageCancelBtn.addEventListener("click", hideGarageSettings);
garageSettingsEl &&
  garageSettingsEl.addEventListener("click", (e) => {
    if (e.target === garageSettingsEl) hideGarageSettings();
  });

if (garageSettingsForm) {
  garageSettingsForm.addEventListener("submit", async (e) => {
    e.preventDefault();
    const myq_email = (myqEmailInput.value || "").trim();
    const myq_password = (myqPasswordInput.value || "").trim();
    if (myq_email && !myq_password && !myqPasswordInput.dataset.hasExisting) {
      garageMsg.className = "err";
      garageMsg.textContent = "Please provide your MyQ password.";
      return;
    }
    garageSaveBtn.disabled = true;
    garageMsg.className = "";
    garageMsg.textContent = "Verifying…";
    try {
      const res = await fetch("/api/save_myq", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ myq_email, myq_password }),
      });
      const data = await res.json();
      if (data.ok) {
        garageMsg.className = "ok";
        garageMsg.textContent = data.myq_configured
          ? "Connected. Garage door online."
          : "MyQ disconnected.";
        setGarageStatus(data.myq_configured);
        myqPasswordInput.dataset.hasExisting = data.myq_configured ? "1" : "";
        setTimeout(hideGarageSettings, 1200);
      } else {
        garageMsg.className = "err";
        garageMsg.textContent = data.error || "Could not save settings.";
      }
    } catch {
      garageMsg.className = "err";
      garageMsg.textContent = "Could not reach the server.";
    } finally {
      garageSaveBtn.disabled = false;
    }
  });
}
