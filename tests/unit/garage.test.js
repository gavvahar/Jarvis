import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../../static/v2/js/app/core.js", () => ({
  $: (id) => document.getElementById(id),
}));

function buildDom() {
  document.body.innerHTML = `
    <div id="garage-settings" class="setup-hidden">
      <button id="garage-btn"></button>
      <button id="garage-cancel"></button>
      <span id="garage-msg"></span>
      <span id="garage-status-dot"></span>
      <span id="garage-status-text"></span>
      <form id="garage-settings-form">
        <input id="myq-email" />
        <input id="myq-password" />
        <button id="garage-save"></button>
      </form>
    </div>
  `;
}

function $(id) {
  return document.getElementById(id);
}

function submit() {
  $("garage-settings-form").dispatchEvent(
    new Event("submit", { bubbles: true, cancelable: true }),
  );
}

function flush() {
  return new Promise((r) => setTimeout(r, 10));
}

let mod;

describe("garage.js", () => {
  beforeEach(async () => {
    vi.resetModules();
    buildDom();
    mod = await import("../../static/v2/js/app/garage.js");
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("setGarageStatus reflects connected state", () => {
    mod.setGarageStatus(true);

    expect($("garage-status-dot").classList.contains("connected")).toBe(true);
    expect($("garage-status-text").textContent).toBe("CONNECTED");
    expect($("garage-btn").classList.contains("garage-live")).toBe(true);
  });

  it("setGarageStatus reflects disconnected state", () => {
    mod.setGarageStatus(true);
    mod.setGarageStatus(false);

    expect($("garage-status-dot").classList.contains("disconnected")).toBe(
      true,
    );
    expect($("garage-status-text").textContent).toBe("NOT CONNECTED");
    expect($("garage-btn").classList.contains("garage-live")).toBe(false);
  });

  it("opening clears the message, closing blanks the password field", () => {
    $("garage-msg").textContent = "stale";
    $("myq-password").value = "secret";

    $("garage-btn").click();
    expect($("garage-settings").classList.contains("setup-hidden")).toBe(false);
    expect($("garage-msg").textContent).toBe("");

    $("garage-cancel").click();
    expect($("garage-settings").classList.contains("setup-hidden")).toBe(true);
    expect($("myq-password").value).toBe("");
  });

  it("closes on backdrop click but not on content click", () => {
    $("garage-settings").classList.remove("setup-hidden");
    $("garage-btn").click();
    expect($("garage-settings").classList.contains("setup-hidden")).toBe(false);
    $("garage-settings").click();
    expect($("garage-settings").classList.contains("setup-hidden")).toBe(true);
  });

  it("rejects an email with no password and no existing password on file", () => {
    $("myq-email").value = "me@example.com";
    submit();
    expect($("garage-msg").className).toBe("err");
    expect($("garage-msg").textContent).toContain("MyQ password");
  });

  it("saves successfully and remembers a password was set", async () => {
    $("myq-email").value = "me@example.com";
    $("myq-password").value = "hunter2";
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        json: async () => ({ ok: true, myq_configured: true }),
      }),
    );

    submit();
    await flush();

    expect($("garage-msg").className).toBe("ok");
    expect($("garage-msg").textContent).toBe("Connected. Garage door online.");
    expect($("myq-password").dataset.hasExisting).toBe("1");
  });

  it("shows the server error on failure", async () => {
    $("myq-email").value = "me@example.com";
    $("myq-password").value = "hunter2";
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        json: async () => ({ ok: false, error: "bad creds" }),
      }),
    );

    submit();
    await flush();

    expect($("garage-msg").textContent).toBe("bad creds");
  });

  it("shows a generic error when the request throws", async () => {
    $("myq-email").value = "me@example.com";
    $("myq-password").value = "hunter2";
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new Error("offline")));

    submit();
    await flush();

    expect($("garage-msg").textContent).toBe("Could not reach the server.");
    expect($("garage-save").disabled).toBe(false);
  });
});
