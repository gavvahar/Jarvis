import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../../static/v2/js/app/core.js", () => ({
  $: (id) => document.getElementById(id),
}));

function buildDom() {
  document.body.innerHTML = `
    <div id="spotify-settings" class="setup-hidden">
      <button id="spotify-btn"></button>
      <button id="spotify-settings-close"></button>
      <button id="spotify-disconnect-btn"></button>
      <span id="spotify-dot"></span>
      <span id="spotify-text"></span>
      <span id="spotify-msg"></span>
      <span id="spotify-redirect-uri"></span>
      <a id="spotify-auth-link"></a>
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

describe("spotify.js", () => {
  beforeEach(async () => {
    vi.resetModules();
    buildDom();
    mod = await import("../../static/v2/js/app/spotify.js");
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("populates the redirect URI from the current origin", () => {
    expect($("spotify-redirect-uri").textContent).toBe(
      window.location.origin + "/auth/spotify/callback",
    );
  });

  it("setSpotifyStatus reflects connected state", () => {
    mod.setSpotifyStatus(true);
    expect($("spotify-dot").className).toBe("connected");
    expect($("spotify-text").textContent).toBe("CONNECTED");
    expect($("spotify-btn").classList.contains("spotify-live")).toBe(true);
  });

  it("setSpotifyStatus reflects disconnected state", () => {
    mod.setSpotifyStatus(true);
    mod.setSpotifyStatus(false);
    expect($("spotify-dot").className).toBe("disconnected");
    expect($("spotify-btn").classList.contains("spotify-live")).toBe(false);
  });

  it("opens and closes the settings panel", () => {
    $("spotify-btn").click();
    expect($("spotify-settings").classList.contains("setup-hidden")).toBe(false);
    $("spotify-settings-close").click();
    expect($("spotify-settings").classList.contains("setup-hidden")).toBe(true);
  });

  it("closes on backdrop click but not on content click", () => {
    $("spotify-settings").classList.remove("setup-hidden");
    $("spotify-btn").click();
    expect($("spotify-settings").classList.contains("setup-hidden")).toBe(false);
    $("spotify-settings").click();
    expect($("spotify-settings").classList.contains("setup-hidden")).toBe(true);
  });

  it("disconnects successfully", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({}));

    $("spotify-disconnect-btn").click();
    await flush();

    expect($("spotify-msg").className).toBe("ok");
    expect($("spotify-msg").textContent).toBe("Disconnected from Spotify.");
    expect($("spotify-dot").className).toBe("disconnected");
  });

  it("shows an error when disconnect fails", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new Error("offline")));

    $("spotify-disconnect-btn").click();
    await flush();

    expect($("spotify-msg").className).toBe("err");
    expect($("spotify-msg").textContent).toBe("Could not reach the server.");
  });

  it("auto-opens settings and cleans the URL when redirected back from Spotify OAuth", async () => {
    window.history.pushState({}, "", "/?spotify_connected=1");
    vi.resetModules();
    buildDom();
    await import("../../static/v2/js/app/spotify.js");

    expect($("spotify-settings").classList.contains("setup-hidden")).toBe(false);
    expect(window.location.search).toBe("");

    window.history.pushState({}, "", "/");
  });
});
