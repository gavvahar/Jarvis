/* ===========================================================
   FIRST-RUN SETUP  (provider + model + API key)
   =========================================================== */
import { $, setConfigured, hideSetup } from "./core.js";

const setupEl = $("setup"),
  keyInput = $("setup-key"),
  setupForm = $("setup-form"),
  setupMsg = $("setup-msg"),
  setupGo = $("setup-go"),
  provSel = $("setup-provider"),
  modelSel = $("setup-model"),
  modelCustom = $("setup-model-custom"),
  baseUrl = $("setup-baseurl"),
  helpLink = $("setup-help");

// Curated model options per provider. "" value = the "Other (type below)" choice.
const MODELS = {
  anthropic: [
    { v: "claude-haiku-4-5", t: "Claude Haiku 4.5 — fast & affordable" },
    { v: "claude-sonnet-4-6", t: "Claude Sonnet 4.6 — most in-character" },
    { v: "claude-opus-4-8", t: "Claude Opus 4.8 — most capable" },
    { v: "", t: "Other (type below)…" },
  ],
  openai: [
    { v: "gpt-4o-mini", t: "GPT-4o mini — fast & affordable" },
    { v: "gpt-4o", t: "GPT-4o — capable" },
    { v: "gpt-4.1-mini", t: "GPT-4.1 mini" },
    { v: "gpt-4.1", t: "GPT-4.1 — most capable" },
    { v: "", t: "Other (type below)…" },
  ],
  openai_compatible: [{ v: "", t: "Type the model name below…" }],
};
const HELP = {
  anthropic: {
    url: "https://console.anthropic.com/settings/keys",
    txt: "Get an Anthropic key →",
    ph: "sk-ant-...",
  },
  openai: {
    url: "https://platform.openai.com/api-keys",
    txt: "Get an OpenAI key →",
    ph: "sk-...",
  },
  openai_compatible: {
    url: "https://openrouter.ai/keys",
    txt: "e.g. get an OpenRouter key →",
    ph: "your API key",
  },
};

function refreshProviderUI() {
  const p = provSel.value;
  // model dropdown
  modelSel.innerHTML = "";
  (MODELS[p] || []).forEach((m) => {
    const o = document.createElement("option");
    o.value = m.v;
    o.textContent = m.t;
    modelSel.appendChild(o);
  });
  // help link + key placeholder
  const h = HELP[p] || HELP.anthropic;
  if (helpLink) {
    helpLink.href = h.url;
    helpLink.textContent = h.txt;
  }
  if (keyInput) keyInput.placeholder = h.ph;
  // base URL only for the compatible provider
  baseUrl.style.display = p === "openai_compatible" ? "block" : "none";
  refreshModelUI();
}
function refreshModelUI() {
  // show the free-text model box when "Other" (empty value) is selected
  const custom = modelSel.value === "";
  modelCustom.style.display = custom ? "block" : "none";
}
if (provSel) {
  provSel.addEventListener("change", refreshProviderUI);
  refreshProviderUI();
}
if (modelSel) modelSel.addEventListener("change", refreshModelUI);

if (setupForm) {
  setupForm.addEventListener("submit", async (e) => {
    e.preventDefault();
    const provider = provSel.value;
    const key = (keyInput.value || "").trim();
    const model = modelSel.value || (modelCustom.value || "").trim();
    const base_url = (baseUrl.value || "").trim();
    if (!key && provider !== "openai_compatible") {
      setupMsg.className = "err";
      setupMsg.textContent = "Please paste your API key.";
      return;
    }
    if (!model) {
      setupMsg.className = "err";
      setupMsg.textContent = "Please choose or type a model.";
      return;
    }
    if (provider === "openai_compatible" && !base_url) {
      setupMsg.className = "err";
      setupMsg.textContent = "This provider needs a base URL.";
      return;
    }
    setupGo.disabled = true;
    setupMsg.className = "";
    setupMsg.textContent = "Verifying…";
    try {
      const ha_url = ($("setup-ha-url").value || "").trim();
      const ha_token = ($("setup-ha-token").value || "").trim();
      const res = await fetch("/api/save_config", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          provider,
          key,
          model,
          base_url,
          ha_url,
          ha_token,
        }),
      });
      const data = await res.json();
      if (data.ok) {
        setConfigured(true);
        setupMsg.className = "ok";
        setupMsg.textContent = "Connected. Welcome aboard, sir.";
        keyInput.value = "";
        setTimeout(() => {
          hideSetup();
        }, 1100);
      } else {
        setupMsg.className = "err";
        setupMsg.textContent = data.error || "That was rejected.";
      }
    } catch (err) {
      setupMsg.className = "err";
      setupMsg.textContent = "Could not reach the server. Is it running?";
    } finally {
      setupGo.disabled = false;
    }
  });
}
