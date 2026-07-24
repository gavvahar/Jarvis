import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../../static/v2/js/app/core.js", () => ({
  $: (id) => document.getElementById(id),
}));

function buildDom() {
  document.body.innerHTML = `
    <div id="ha-settings" class="setup-hidden">
      <button id="ha-settings-btn"></button>
      <button id="ha-cancel"></button>
      <span id="ha-msg"></span>
      <span id="ha-status-dot"></span>
      <span id="ha-status-text"></span>
      <form id="ha-settings-form">
        <input id="ha-url" />
        <input id="ha-token" />
        <button id="ha-save"></button>
      </form>
    </div>
  `;
}

function $(id) {
  return document.getElementById(id);
}

function submit() {
  $("ha-settings-form").dispatchEvent(
    new Event("submit", { bubbles: true, cancelable: true }),
  );
}

function flush() {
  return new Promise((r) => setTimeout(r, 10));
}

let mod;

describe("ha.js", () => {
  beforeEach(async () => {
    vi.resetModules();
    buildDom();
    mod = await import("../../static/v2/js/app/ha.js");
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("setHaStatus reflects connected state and populates the URL", () => {
    mod.setHaStatus(true, "https://ha.example.com");

    expect($("ha-status-dot").classList.contains("connected")).toBe(true);
    expect($("ha-status-text").textContent).toBe("CONNECTED");
    expect($("ha-settings-btn").classList.contains("ha-live")).toBe(true);
    expect($("ha-url").value).toBe("https://ha.example.com");
  });

  it("setHaStatus reflects disconnected state", () => {
    mod.setHaStatus(true, "https://ha.example.com");
    mod.setHaStatus(false);

    expect($("ha-status-dot").classList.contains("disconnected")).toBe(true);
    expect($("ha-status-text").textContent).toBe("NOT CONNECTED");
    expect($("ha-settings-btn").classList.contains("ha-live")).toBe(false);
  });

  it("opening clears the message, closing blanks the token field", () => {
    $("ha-msg").textContent = "stale";
    $("ha-msg").className = "err";
    $("ha-token").value = "secret-token";

    $("ha-settings-btn").click();
    expect($("ha-settings").classList.contains("setup-hidden")).toBe(false);
    expect($("ha-msg").textContent).toBe("");

    $("ha-cancel").click();
    expect($("ha-settings").classList.contains("setup-hidden")).toBe(true);
    expect($("ha-token").value).toBe("");
  });

  it("closes on backdrop click but not on content click", () => {
    $("ha-settings").classList.remove("setup-hidden");
    $("ha-settings-btn").click();
    expect($("ha-settings").classList.contains("setup-hidden")).toBe(false);
    $("ha-settings").click();
    expect($("ha-settings").classList.contains("setup-hidden")).toBe(true);
  });

  it("rejects a URL with no token and no existing token on file", () => {
    $("ha-url").value = "https://ha.example.com";
    submit();
    expect($("ha-msg").className).toBe("err");
    expect($("ha-msg").textContent).toContain("Long-Lived Access Token");
  });

  it("saves successfully and remembers a token was set", async () => {
    $("ha-url").value = "https://ha.example.com";
    $("ha-token").value = "tok123";
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        json: async () => ({ ok: true, ha_configured: true }),
      }),
    );

    submit();
    await flush();

    expect($("ha-msg").className).toBe("ok");
    expect($("ha-msg").textContent).toBe("Connected. Home automation online.");
    expect($("ha-token").dataset.hasExisting).toBe("1");
  });

  it("reports disconnection when the server clears the config", async () => {
    $("ha-url").value = "";
    $("ha-token").value = "";
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        json: async () => ({ ok: true, ha_configured: false }),
      }),
    );

    submit();
    await flush();

    expect($("ha-msg").textContent).toBe("Home automation disconnected.");
  });

  it("shows the server error on failure", async () => {
    $("ha-url").value = "https://ha.example.com";
    $("ha-token").value = "tok123";
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        json: async () => ({ ok: false, error: "bad token" }),
      }),
    );

    submit();
    await flush();

    expect($("ha-msg").className).toBe("err");
    expect($("ha-msg").textContent).toBe("bad token");
  });

  it("shows a generic error when the request throws", async () => {
    $("ha-url").value = "https://ha.example.com";
    $("ha-token").value = "tok123";
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new Error("offline")));

    submit();
    await flush();

    expect($("ha-msg").textContent).toBe("Could not reach the server.");
    expect($("ha-save").disabled).toBe(false);
  });
});
