/* ===========================================================
   TESLA SETTINGS MODAL
   =========================================================== */
import { $ } from "./core.js";

const teslaSettingsEl = $("tesla-settings");
const teslaBtn = $("tesla-btn");
const teslaSettingsClose = $("tesla-settings-close");
const teslaUnofficialForm = $("tesla-unofficial-form");
const teslaRefreshTokenInput = $("tesla-refresh-token");
const teslaUnofficialSave = $("tesla-unofficial-save");
const teslaUnofficialDisconnect = $("tesla-unofficial-disconnect");
const teslaUnofficialDot = $("tesla-unofficial-dot");
const teslaUnofficialText = $("tesla-unofficial-text");
const teslaUnofficialMsg = $("tesla-unofficial-msg");
const teslaFleetDot = $("tesla-fleet-dot");
const teslaFleetText = $("tesla-fleet-text");
const teslaFleetMsg = $("tesla-fleet-msg");
const teslaFleetDisconnect = $("tesla-fleet-disconnect");
export const teslaFleetAuthBtn = $("tesla-fleet-auth-btn");

export function setTeslaStatus(method) {
  const hasUnofficial = method === "unofficial" || method === "both";
  const hasFleet = method === "fleet" || method === "both";
  if (teslaUnofficialDot) {
    teslaUnofficialDot.className = hasUnofficial
      ? "connected"
      : "disconnected";
  }
  if (teslaUnofficialText)
    teslaUnofficialText.textContent = hasUnofficial
      ? "CONNECTED"
      : "NOT CONNECTED";
  if (teslaFleetDot) {
    teslaFleetDot.className = hasFleet ? "connected" : "disconnected";
  }
  if (teslaFleetText)
    teslaFleetText.textContent = hasFleet ? "CONNECTED" : "NOT CONNECTED";
  if (teslaBtn) {
    if (hasUnofficial || hasFleet) teslaBtn.classList.add("tesla-live");
    else teslaBtn.classList.remove("tesla-live");
  }
}

function showTeslaSettings() {
  if (teslaSettingsEl) teslaSettingsEl.classList.remove("setup-hidden");
}
function hideTeslaSettings() {
  if (teslaSettingsEl) teslaSettingsEl.classList.add("setup-hidden");
  if (teslaRefreshTokenInput) teslaRefreshTokenInput.value = "";
  if (teslaUnofficialMsg) {
    teslaUnofficialMsg.textContent = "";
    teslaUnofficialMsg.className = "";
  }
  if (teslaFleetMsg) {
    teslaFleetMsg.textContent = "";
    teslaFleetMsg.className = "";
  }
}

if (teslaBtn) teslaBtn.addEventListener("click", showTeslaSettings);
if (teslaSettingsClose)
  teslaSettingsClose.addEventListener("click", hideTeslaSettings);
teslaSettingsEl &&
  teslaSettingsEl.addEventListener("click", (e) => {
    if (e.target === teslaSettingsEl) hideTeslaSettings();
  });

document.querySelectorAll(".tesla-tab").forEach((tab) => {
  tab.addEventListener("click", () => {
    document
      .querySelectorAll(".tesla-tab")
      .forEach((t) => t.classList.remove("tesla-tab-active"));
    document
      .querySelectorAll(".tesla-tab-content")
      .forEach((c) => c.classList.add("tesla-tab-hidden"));
    tab.classList.add("tesla-tab-active");
    const target = $("tesla-tab-" + tab.dataset.ttab);
    if (target) target.classList.remove("tesla-tab-hidden");
  });
});

if (teslaUnofficialForm) {
  teslaUnofficialForm.addEventListener("submit", async (e) => {
    e.preventDefault();
    const refresh_token = (teslaRefreshTokenInput.value || "").trim();
    if (!refresh_token) {
      if (teslaUnofficialMsg) {
        teslaUnofficialMsg.className = "err";
        teslaUnofficialMsg.textContent =
          "Please paste your Tesla refresh token.";
      }
      return;
    }
    if (teslaUnofficialSave) teslaUnofficialSave.disabled = true;
    if (teslaUnofficialMsg) {
      teslaUnofficialMsg.className = "";
      teslaUnofficialMsg.textContent = "Verifying…";
    }
    try {
      const res = await fetch("/api/tesla/save_unofficial", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ refresh_token }),
      });
      const data = await res.json();
      if (data.ok) {
        if (teslaUnofficialMsg) {
          teslaUnofficialMsg.className = "ok";
          teslaUnofficialMsg.textContent = "Connected. Tesla online.";
        }
        setTeslaStatus(data.tesla_method || "unofficial");
        if (teslaRefreshTokenInput) teslaRefreshTokenInput.value = "";
        setTimeout(hideTeslaSettings, 1200);
      } else {
        if (teslaUnofficialMsg) {
          teslaUnofficialMsg.className = "err";
          teslaUnofficialMsg.textContent = data.error || "Could not connect.";
        }
      }
    } catch {
      if (teslaUnofficialMsg) {
        teslaUnofficialMsg.className = "err";
        teslaUnofficialMsg.textContent = "Could not reach the server.";
      }
    } finally {
      if (teslaUnofficialSave) teslaUnofficialSave.disabled = false;
    }
  });
}

if (teslaUnofficialDisconnect) {
  teslaUnofficialDisconnect.addEventListener("click", async () => {
    try {
      const res = await fetch("/api/tesla/disconnect", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ which: "unofficial" }),
      });
      const data = await res.json();
      if (data.ok) {
        setTeslaStatus(data.tesla_method || "");
        if (teslaUnofficialMsg) {
          teslaUnofficialMsg.className = "ok";
          teslaUnofficialMsg.textContent = "Unofficial API disconnected.";
        }
      }
    } catch {
      if (teslaUnofficialMsg) {
        teslaUnofficialMsg.className = "err";
        teslaUnofficialMsg.textContent = "Could not reach the server.";
      }
    }
  });
}

if (teslaFleetDisconnect) {
  teslaFleetDisconnect.addEventListener("click", async () => {
    try {
      const res = await fetch("/api/tesla/disconnect", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ which: "fleet" }),
      });
      const data = await res.json();
      if (data.ok) {
        setTeslaStatus(data.tesla_method || "");
        if (teslaFleetMsg) {
          teslaFleetMsg.className = "ok";
          teslaFleetMsg.textContent = "Fleet API disconnected.";
        }
      }
    } catch {
      if (teslaFleetMsg) {
        teslaFleetMsg.className = "err";
        teslaFleetMsg.textContent = "Could not reach the server.";
      }
    }
  });
}
