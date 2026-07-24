import { beforeEach, describe, expect, it, vi } from "vitest";

// settings.js only needs $ from core.js, and it's cheap to give it the real
// DOM lookup rather than a stub — this file's value is in exercising real
// class-list/MutationObserver behavior against jsdom.
//
// Caveat (see settings-panel-tabs-bug.md): the historical "tab switch closes
// the whole panel" bug depended on a real-browser microtask checkpoint firing
// between two synchronous listeners on the same event dispatch, which jsdom
// doesn't reproduce — confirmed by temporarily reverting the setTimeout(0)
// fix here and finding these tests still passed. So this suite verifies the
// panel's intended design (capture-phase exclusivity, auto-collapse only
// when nothing's open) but can't by itself pin down that specific fix.
vi.mock("../../static/v2/js/app/core.js", () => ({
  $: (id) => document.getElementById(id),
}));

const PANE_IDS = [
  "ha-settings",
  "pim-settings",
  "msg-settings",
  "doorbell-settings",
  "vision-settings",
  "garage-settings",
  "tesla-settings",
  "spotify-settings",
  "apple-music-settings",
  "accessibility-settings",
];
const OPEN_CLASS = {
  "msg-settings": "msg-settings-open",
  "doorbell-settings": "doorbell-settings-open",
};

function buildDom() {
  const tabs = PANE_IDS.map(
    (id) => `<button data-panel="${id}">${id}</button>`,
  ).join("");
  const panes = PANE_IDS.map(
    (id) =>
      `<div id="${id}" class="${OPEN_CLASS[id] ? "" : "setup-hidden"}"></div>`,
  ).join("");
  document.body.innerHTML = `
    <div id="settings-panel" class="setup-hidden">
      <button id="settings-btn"></button>
      <button id="settings-panel-close"></button>
      <div id="settings-tabs">${tabs}</div>
    </div>
    ${panes}
  `;
}

function openPane(id) {
  const el = document.getElementById(id);
  if (OPEN_CLASS[id]) el.classList.add(OPEN_CLASS[id]);
  else el.classList.remove("setup-hidden");
}

function closePane(id) {
  const el = document.getElementById(id);
  if (OPEN_CLASS[id]) el.classList.remove(OPEN_CLASS[id]);
  else el.classList.add("setup-hidden");
}

function isPaneOpen(id) {
  const el = document.getElementById(id);
  return OPEN_CLASS[id]
    ? el.classList.contains(OPEN_CLASS[id])
    : !el.classList.contains("setup-hidden");
}

function clickTab(id) {
  document.querySelector(`button[data-panel="${id}"]`).click();
}

// MutationObserver callbacks are microtasks and settings.js itself defers
// its check another tick with setTimeout(0) — a real macrotask wait flushes
// both.
function flush() {
  return new Promise((r) => setTimeout(r, 10));
}

describe("settings panel shell", () => {
  beforeEach(async () => {
    vi.resetModules();
    buildDom();
    await import("../../static/v2/js/app/settings.js");
  });

  it("hides every other pane when a tab is clicked (capture-phase exclusivity)", () => {
    openPane("ha-settings");
    openPane("msg-settings");

    clickTab("vision-settings");

    expect(isPaneOpen("ha-settings")).toBe(false);
    expect(isPaneOpen("msg-settings")).toBe(false);
  });

  it("marks only the clicked tab button as active", () => {
    clickTab("garage-settings");
    clickTab("tesla-settings");

    const active = [...document.querySelectorAll("button[data-panel]")].filter(
      (b) => b.classList.contains("active"),
    );
    expect(active).toHaveLength(1);
    expect(active[0].dataset.panel).toBe("tesla-settings");
  });

  it("auto-collapses the shell once the last open pane closes", async () => {
    document.getElementById("settings-panel").classList.remove("setup-hidden");
    openPane("ha-settings");

    closePane("ha-settings");
    await flush();

    expect(
      document
        .getElementById("settings-panel")
        .classList.contains("setup-hidden"),
    ).toBe(true);
  });

  it("does not collapse the shell while another pane is still open (regression: tab switch used to close the whole panel)", async () => {
    document.getElementById("settings-panel").classList.remove("setup-hidden");
    openPane("ha-settings");
    openPane("vision-settings");

    closePane("ha-settings"); // simulates ha.js's own close handler mid-switch
    await flush();

    expect(
      document
        .getElementById("settings-panel")
        .classList.contains("setup-hidden"),
    ).toBe(false);
  });

  it("opening the panel with nothing open auto-clicks the first tab", () => {
    document.getElementById("settings-btn").click();

    expect(
      document
        .getElementById("settings-panel")
        .classList.contains("setup-hidden"),
    ).toBe(false);
    expect(
      document
        .querySelector('button[data-panel="ha-settings"]')
        .classList.contains("active"),
    ).toBe(true);
  });

  it("opening the panel with a pane already open does not steal focus to the first tab", () => {
    openPane("spotify-settings");

    document.getElementById("settings-btn").click();

    const active = [...document.querySelectorAll("button[data-panel]")].filter(
      (b) => b.classList.contains("active"),
    );
    expect(active).toHaveLength(0);
  });

  it("closes the panel via the close button", () => {
    const panel = document.getElementById("settings-panel");
    panel.classList.remove("setup-hidden");

    document.getElementById("settings-panel-close").click();

    expect(panel.classList.contains("setup-hidden")).toBe(true);
  });

  it("closes the panel on backdrop click but not on content click", () => {
    const panel = document.getElementById("settings-panel");
    panel.classList.remove("setup-hidden");

    document.getElementById("settings-btn").click(); // click on content, inside the panel
    expect(panel.classList.contains("setup-hidden")).toBe(false);

    panel.click(); // click on the backdrop itself (target === panel)
    expect(panel.classList.contains("setup-hidden")).toBe(true);
  });
});
