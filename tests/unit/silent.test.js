import { beforeEach, describe, expect, it, vi } from "vitest";

let silentModeState = false;
const setSilentMode = vi.fn((v) => {
  silentModeState = v;
});

vi.mock("../../static/v2/js/app/core.js", () => ({
  $: (id) => document.getElementById(id),
  isSilentMode: () => silentModeState,
  setSilentMode,
}));

function $(id) {
  return document.getElementById(id);
}

describe("silent.js", () => {
  beforeEach(() => {
    vi.resetModules();
    setSilentMode.mockClear();
  });

  it("reflects a persisted silent-mode-on state immediately on load", async () => {
    silentModeState = true;
    document.body.innerHTML = `<button id="silent-btn"></button>`;
    await import("../../static/v2/js/app/silent.js");
    expect($("silent-btn").classList.contains("silent-active")).toBe(true);
  });

  it("reflects a persisted silent-mode-off state immediately on load", async () => {
    silentModeState = false;
    document.body.innerHTML = `<button id="silent-btn"></button>`;
    await import("../../static/v2/js/app/silent.js");
    expect($("silent-btn").classList.contains("silent-active")).toBe(false);
  });

  it("clicking toggles silent mode on and reflects it", async () => {
    silentModeState = false;
    document.body.innerHTML = `<button id="silent-btn"></button>`;
    await import("../../static/v2/js/app/silent.js");

    $("silent-btn").click();

    expect(setSilentMode).toHaveBeenCalledWith(true);
    expect($("silent-btn").classList.contains("silent-active")).toBe(true);
  });

  it("clicking again toggles silent mode back off", async () => {
    silentModeState = true;
    document.body.innerHTML = `<button id="silent-btn"></button>`;
    await import("../../static/v2/js/app/silent.js");

    $("silent-btn").click();

    expect(setSilentMode).toHaveBeenCalledWith(false);
    expect($("silent-btn").classList.contains("silent-active")).toBe(false);
  });

  it("does nothing when the button isn't on the page", async () => {
    document.body.innerHTML = "";
    await expect(import("../../static/v2/js/app/silent.js")).resolves.toBeDefined();
  });
});
