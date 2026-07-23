/* ===========================================================
   ACCESSIBILITY & HEARING ASSISTANCE (Phase 10)
   =========================================================== */
import { $, socket, setTtsPrefs } from "./core.js";

// Wake word visual confirmation: doorbell.js's own wake_trigger handler
// only speaks/wakes when starting from standby, so a repeat wake word
// while already awake gets no feedback today. This fires the existing
// orb energy-surge (standby.js's #mode-flash) on every wake_trigger,
// standby or not, so it's always visible that Jarvis heard you.
socket.on("wake_trigger", () => {
  if (window.__triggerWakeFlash) window.__triggerWakeFlash();
});

// ---- visual TTS: caption bar mirrors every spoken reply on screen ----
// core.js already renders reply text into window.__chat, but that's the
// Chat Tab drawer — hidden by default, opened only with [C]. A reply
// spoken during normal voice use had no on-screen text unless that
// drawer happened to be open. #caption-bar (film_overlays.html) is
// always-visible instead, driven by the same speak_sentence/response_done
// events core.js listens to.
const captionBar = $("caption-bar");
const captionText = $("caption-text");
let _captionBuf = "";
let _captionHideTimer = null;

function showCaption(text) {
  if (!captionBar || !captionText) return;
  captionText.textContent = text;
  captionBar.classList.remove("caption-hidden");
  if (_captionHideTimer) clearTimeout(_captionHideTimer);
}

function scheduleCaptionHide() {
  if (!captionBar) return;
  if (_captionHideTimer) clearTimeout(_captionHideTimer);
  // grace period after speech ends so the last line stays readable
  _captionHideTimer = setTimeout(() => {
    captionBar.classList.add("caption-hidden");
    _captionBuf = "";
  }, 2500);
}

socket.on("speak_sentence", ({ text }) => {
  const t = (text || "").trim();
  if (!t) return;
  _captionBuf = _captionBuf ? _captionBuf + " " + t : t;
  showCaption(_captionBuf);
});

socket.on("response_done", ({ text }) => {
  if (text && text.length >= _captionBuf.length) {
    _captionBuf = text;
    showCaption(_captionBuf);
  }
  scheduleCaptionHide();
});

// ---- TTS clarity settings panel ----
const accessibilityBtn = $("accessibility-btn");
const accessibilitySettingsEl = $("accessibility-settings");
const accessibilityCloseBtn = $("accessibility-settings-close");
const ttsPrefsForm = $("tts-prefs-form");
const ttsRateInput = $("tts-rate-input");
const ttsPitchInput = $("tts-pitch-input");
const ttsVolumeInput = $("tts-volume-input");
const ttsRateVal = $("tts-rate-val");
const ttsPitchVal = $("tts-pitch-val");
const ttsVolumeVal = $("tts-volume-val");
const ttsPrefsPresetBtn = $("tts-prefs-preset");
const ttsPrefsSaveBtn = $("tts-prefs-save");
const ttsPrefsMsg = $("tts-prefs-msg");

function syncTtsLabels() {
  if (ttsRateVal && ttsRateInput) ttsRateVal.textContent = ttsRateInput.value;
  if (ttsPitchVal && ttsPitchInput)
    ttsPitchVal.textContent = ttsPitchInput.value;
  if (ttsVolumeVal && ttsVolumeInput)
    ttsVolumeVal.textContent = ttsVolumeInput.value;
}

async function loadTtsPrefs() {
  if (!ttsRateInput) return;
  try {
    const r = await fetch("/api/tts-prefs");
    const { rate, pitch, volume } = await r.json();
    if (rate != null) ttsRateInput.value = rate;
    if (pitch != null) ttsPitchInput.value = pitch;
    if (volume != null) ttsVolumeInput.value = volume;
    syncTtsLabels();
  } catch {
    /* leave defaults */
  }
}

if (accessibilityBtn && accessibilitySettingsEl) {
  accessibilityBtn.addEventListener("click", () => {
    accessibilitySettingsEl.classList.remove("setup-hidden");
    loadTtsPrefs();
  });
}
if (accessibilityCloseBtn && accessibilitySettingsEl) {
  accessibilityCloseBtn.addEventListener("click", () => {
    accessibilitySettingsEl.classList.add("setup-hidden");
  });
}
[ttsRateInput, ttsPitchInput, ttsVolumeInput].forEach((el) => {
  if (el) el.addEventListener("input", syncTtsLabels);
});
if (ttsPrefsPresetBtn) {
  ttsPrefsPresetBtn.addEventListener("click", () => {
    // Roadmap's hearing-impaired default: slower speech. Volume is already
    // at the Web Speech API's max (1.0), so there's no "louder" to give it.
    if (ttsRateInput) ttsRateInput.value = "0.8";
    if (ttsPitchInput) ttsPitchInput.value = "1.0";
    if (ttsVolumeInput) ttsVolumeInput.value = "1.0";
    syncTtsLabels();
  });
}
if (ttsPrefsForm) {
  ttsPrefsForm.addEventListener("submit", async (e) => {
    e.preventDefault();
    ttsPrefsSaveBtn.disabled = true;
    ttsPrefsMsg.className = "";
    ttsPrefsMsg.textContent = "Saving…";
    const rate = parseFloat(ttsRateInput.value) || 1.0;
    const pitch = parseFloat(ttsPitchInput.value) || 1.0;
    const volume = parseFloat(ttsVolumeInput.value);
    try {
      const res = await fetch("/api/tts-prefs", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          rate,
          pitch,
          volume: Number.isFinite(volume) ? volume : 1.0,
        }),
      });
      const data = await res.json();
      if (data.ok) {
        setTtsPrefs(data);
        ttsPrefsMsg.className = "ok";
        ttsPrefsMsg.textContent = "Saved. Applies to your next reply.";
      } else {
        ttsPrefsMsg.className = "err";
        ttsPrefsMsg.textContent = data.error || "Could not save settings.";
      }
    } catch {
      ttsPrefsMsg.className = "err";
      ttsPrefsMsg.textContent = "Could not reach the server.";
    } finally {
      ttsPrefsSaveBtn.disabled = false;
    }
  });
}
