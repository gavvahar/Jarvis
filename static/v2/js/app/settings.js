/* ===========================================================
   SETTINGS PANEL — single entry point with a tab per integration
   (HA, Agenda, Messages, Doorbell, Vision, Garage, Tesla, Finance,
   Spotify, Apple Music).

   Each tab button keeps the id + click handler its own module
   already wires up (e.g. ha.js still owns showHaSettings/
   hideHaSettings) — this file only adds the behavior needed to
   host them inside one shell: showing exactly one pane at a time,
   and collapsing the shell once every pane is closed again.
   =========================================================== */
import { $ } from "./core.js";

const settingsPanel = $("settings-panel");
const settingsBtn = $("settings-btn");
const settingsClose = $("settings-panel-close");
const settingsTabs = $("settings-tabs");

function setupHiddenPane(id) {
  return {
    id,
    isOpen: (el) => !el.classList.contains("setup-hidden"),
    hide: (el) => el.classList.add("setup-hidden"),
  };
}
function openClassPane(id, openClass) {
  return {
    id,
    isOpen: (el) => el.classList.contains(openClass),
    hide: (el) => el.classList.remove(openClass),
  };
}

const PANES = [
  setupHiddenPane("ha-settings"),
  setupHiddenPane("pim-settings"),
  openClassPane("msg-settings", "msg-settings-open"),
  openClassPane("doorbell-settings", "doorbell-settings-open"),
  setupHiddenPane("vision-settings"),
  setupHiddenPane("garage-settings"),
  setupHiddenPane("tesla-settings"),
  setupHiddenPane("finance-settings"),
  setupHiddenPane("spotify-settings"),
  setupHiddenPane("apple-music-settings"),
];

function anyPaneOpen() {
  return PANES.some(({ id, isOpen }) => {
    const el = $(id);
    return el && isOpen(el);
  });
}

// Capture phase on the tab bar fires before the tab button's own
// (bubble-phase) click handler defined in ha.js/vision.js/etc., so by
// the time that handler opens its pane, every other pane is already
// closed — regardless of which module's script happened to load first.
if (settingsTabs) {
  settingsTabs.addEventListener(
    "click",
    (e) => {
      const btn = e.target.closest("button[data-panel]");
      if (!btn) return;
      PANES.forEach(({ id, hide }) => {
        if (id === btn.dataset.panel) return;
        const el = $(id);
        if (el) hide(el);
      });
      settingsTabs
        .querySelectorAll("button[data-panel]")
        .forEach((b) => b.classList.toggle("active", b === btn));
    },
    true,
  );
}

// Each tab's own close/cancel/save-success logic only knows how to hide
// itself, not this shared shell. Watch for that and collapse the shell
// too once nothing is left open.
//
// The check is deferred to a setTimeout(0) macrotask so it runs AFTER
// both the capture-phase and bubble-phase event listeners have fired.
// Without the defer, the browser's microtask checkpoint fires the
// observer between the two phases — at that moment the old pane is
// already hidden but the new pane hasn't been shown yet, so
// anyPaneOpen() incorrectly returns false and collapses the shell
// mid-switch.
const paneObserver = new MutationObserver(() => {
  setTimeout(() => {
    if (
      settingsPanel &&
      !settingsPanel.classList.contains("setup-hidden") &&
      !anyPaneOpen()
    ) {
      settingsPanel.classList.add("setup-hidden");
    }
  }, 0);
});
PANES.forEach(({ id }) => {
  const el = $(id);
  if (el)
    paneObserver.observe(el, { attributes: true, attributeFilter: ["class"] });
});

if (settingsBtn) {
  settingsBtn.addEventListener("click", () => {
    if (!settingsPanel) return;
    settingsPanel.classList.remove("setup-hidden");
    if (!anyPaneOpen()) {
      const firstTab =
        settingsTabs && settingsTabs.querySelector("button[data-panel]");
      if (firstTab) firstTab.click();
    }
  });
}
if (settingsClose) {
  settingsClose.addEventListener("click", () => {
    if (settingsPanel) settingsPanel.classList.add("setup-hidden");
  });
}
if (settingsPanel) {
  settingsPanel.addEventListener("click", (e) => {
    if (e.target === settingsPanel) settingsPanel.classList.add("setup-hidden");
  });
}
