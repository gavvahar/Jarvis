import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../../static/v2/js/app/core.js", () => ({
  $: (id) => document.getElementById(id),
}));

function buildDom() {
  document.body.innerHTML = `
    <div id="tesla-settings" class="setup-hidden">
      <button id="tesla-btn"></button>
      <button id="tesla-settings-close"></button>
      <span id="tesla-unofficial-dot"></span>
      <span id="tesla-unofficial-text"></span>
      <span id="tesla-unofficial-msg"></span>
      <span id="tesla-fleet-dot"></span>
      <span id="tesla-fleet-text"></span>
      <span id="tesla-fleet-msg"></span>
      <button id="tesla-fleet-auth-btn"></button>
      <button id="tesla-fleet-disconnect"></button>
      <button id="tesla-unofficial-disconnect"></button>
      <form id="tesla-unofficial-form">
        <input id="tesla-refresh-token" />
        <button id="tesla-unofficial-save"></button>
      </form>
      <button class="tesla-tab" data-ttab="unofficial"></button>
      <button class="tesla-tab" data-ttab="fleet"></button>
      <div id="tesla-tab-unofficial" class="tesla-tab-hidden"></div>
      <div id="tesla-tab-fleet" class="tesla-tab-hidden"></div>
    </div>
  `;
}

function $(id) {
  return document.getElementById(id);
}

function flush() {
  return new Promise((r) => setTimeout(r, 10));
}

let mod;

describe("tesla.js", () => {
  beforeEach(async () => {
    vi.resetModules();
    buildDom();
    mod = await import("../../static/v2/js/app/tesla.js");
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  describe("setTeslaStatus", () => {
    it("reflects unofficial-only connection", () => {
      mod.setTeslaStatus("unofficial");
      expect($("tesla-unofficial-dot").className).toBe("connected");
      expect($("tesla-fleet-dot").className).toBe("disconnected");
      expect($("tesla-btn").classList.contains("tesla-live")).toBe(true);
    });

    it("reflects fleet-only connection", () => {
      mod.setTeslaStatus("fleet");
      expect($("tesla-fleet-text").textContent).toBe("CONNECTED");
      expect($("tesla-unofficial-text").textContent).toBe("NOT CONNECTED");
    });

    it("reflects both connected", () => {
      mod.setTeslaStatus("both");
      expect($("tesla-unofficial-dot").className).toBe("connected");
      expect($("tesla-fleet-dot").className).toBe("connected");
    });

    it("reflects neither connected and dims the topbar button", () => {
      mod.setTeslaStatus("both");
      mod.setTeslaStatus("");
      expect($("tesla-unofficial-dot").className).toBe("disconnected");
      expect($("tesla-fleet-dot").className).toBe("disconnected");
      expect($("tesla-btn").classList.contains("tesla-live")).toBe(false);
    });
  });

  it("opening shows the panel; closing resets token field and messages", () => {
    $("tesla-refresh-token").value = "secret";
    $("tesla-unofficial-msg").textContent = "stale";
    $("tesla-fleet-msg").textContent = "stale";

    $("tesla-btn").click();
    expect($("tesla-settings").classList.contains("setup-hidden")).toBe(false);

    $("tesla-settings-close").click();
    expect($("tesla-settings").classList.contains("setup-hidden")).toBe(true);
    expect($("tesla-refresh-token").value).toBe("");
    expect($("tesla-unofficial-msg").textContent).toBe("");
    expect($("tesla-fleet-msg").textContent).toBe("");
  });

  it("closes on backdrop click but not on content click", () => {
    $("tesla-settings").classList.remove("setup-hidden");
    $("tesla-btn").click();
    expect($("tesla-settings").classList.contains("setup-hidden")).toBe(false);
    $("tesla-settings").click();
    expect($("tesla-settings").classList.contains("setup-hidden")).toBe(true);
  });

  it("switches tabs, activating the clicked tab and revealing its content", () => {
    const [unofficialTab, fleetTab] = document.querySelectorAll(".tesla-tab");

    fleetTab.click();
    expect(fleetTab.classList.contains("tesla-tab-active")).toBe(true);
    expect($("tesla-tab-fleet").classList.contains("tesla-tab-hidden")).toBe(
      false,
    );
    expect(
      $("tesla-tab-unofficial").classList.contains("tesla-tab-hidden"),
    ).toBe(true);

    unofficialTab.click();
    expect(unofficialTab.classList.contains("tesla-tab-active")).toBe(true);
    expect(
      $("tesla-tab-unofficial").classList.contains("tesla-tab-hidden"),
    ).toBe(false);
  });

  describe("unofficial token form", () => {
    it("rejects an empty token", () => {
      $("tesla-unofficial-form").dispatchEvent(
        new Event("submit", { bubbles: true, cancelable: true }),
      );
      expect($("tesla-unofficial-msg").className).toBe("err");
      expect($("tesla-unofficial-msg").textContent).toContain("refresh token");
    });

    it("saves successfully and updates status", async () => {
      $("tesla-refresh-token").value = "tok123";
      vi.stubGlobal(
        "fetch",
        vi.fn().mockResolvedValue({
          json: async () => ({ ok: true, tesla_method: "unofficial" }),
        }),
      );

      $("tesla-unofficial-form").dispatchEvent(
        new Event("submit", { bubbles: true, cancelable: true }),
      );
      await flush();

      expect($("tesla-unofficial-msg").className).toBe("ok");
      expect($("tesla-unofficial-dot").className).toBe("connected");
      expect($("tesla-refresh-token").value).toBe("");
    });

    it("shows the server error on failure", async () => {
      $("tesla-refresh-token").value = "tok123";
      vi.stubGlobal(
        "fetch",
        vi.fn().mockResolvedValue({
          json: async () => ({ ok: false, error: "invalid token" }),
        }),
      );

      $("tesla-unofficial-form").dispatchEvent(
        new Event("submit", { bubbles: true, cancelable: true }),
      );
      await flush();

      expect($("tesla-unofficial-msg").textContent).toBe("invalid token");
    });

    it("shows a generic error when the request throws", async () => {
      $("tesla-refresh-token").value = "tok123";
      vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new Error("offline")));

      $("tesla-unofficial-form").dispatchEvent(
        new Event("submit", { bubbles: true, cancelable: true }),
      );
      await flush();

      expect($("tesla-unofficial-msg").textContent).toBe(
        "Could not reach the server.",
      );
      expect($("tesla-unofficial-save").disabled).toBe(false);
    });
  });

  describe("disconnect buttons", () => {
    it("disconnects the unofficial API", async () => {
      vi.stubGlobal(
        "fetch",
        vi.fn().mockResolvedValue({
          json: async () => ({ ok: true, tesla_method: "fleet" }),
        }),
      );

      $("tesla-unofficial-disconnect").click();
      await flush();

      expect($("tesla-unofficial-msg").textContent).toBe(
        "Unofficial API disconnected.",
      );
      expect($("tesla-fleet-dot").className).toBe("connected");
    });

    it("disconnects the fleet API", async () => {
      vi.stubGlobal(
        "fetch",
        vi.fn().mockResolvedValue({
          json: async () => ({ ok: true, tesla_method: "" }),
        }),
      );

      $("tesla-fleet-disconnect").click();
      await flush();

      expect($("tesla-fleet-msg").textContent).toBe("Fleet API disconnected.");
    });

    it("shows an error when disconnect fails", async () => {
      vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new Error("offline")));

      $("tesla-fleet-disconnect").click();
      await flush();

      expect($("tesla-fleet-msg").textContent).toBe(
        "Could not reach the server.",
      );
    });
  });
});
