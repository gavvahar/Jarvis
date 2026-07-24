import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

function makeSocketMock() {
  const handlers = {};
  return {
    on: vi.fn((event, cb) => {
      handlers[event] = cb;
    }),
    emit: vi.fn(),
    trigger: (event, payload) => handlers[event]?.(payload),
  };
}

let socketMock;

vi.mock("../../static/v2/js/app/core.js", () => ({
  $: (id) => document.getElementById(id),
  get socket() {
    return socketMock;
  },
}));

function buildDom() {
  document.body.innerHTML = `
    <div id="apple-music-settings" class="setup-hidden">
      <button id="apple-music-btn"></button>
      <button id="apple-music-settings-close"></button>
      <button id="apple-music-connect-btn"></button>
      <button id="apple-music-disconnect-btn"></button>
      <span id="apple-music-dot"></span>
      <span id="apple-music-text"></span>
      <span id="apple-music-msg"></span>
    </div>
  `;
}

function $(id) {
  return document.getElementById(id);
}

function flush() {
  return new Promise((r) => setTimeout(r, 10));
}

function fetchJson(body) {
  return { json: async () => body };
}

// Fully wires MusicKit + connect flow so _musicKit is populated, returning
// the fake instance for assertions on subsequent socket command tests.
async function connectMusicKit() {
  const musicKit = {
    authorize: vi.fn().mockResolvedValue("user-token"),
    unauthorize: vi.fn().mockResolvedValue(),
    storefrontId: "us",
    play: vi.fn().mockResolvedValue(),
    pause: vi.fn().mockResolvedValue(),
    skipToNextItem: vi.fn().mockResolvedValue(),
    skipToPreviousItem: vi.fn().mockResolvedValue(),
    volume: 0,
    shuffleMode: null,
    playbackState: null,
    queue: { currentItem: null, append: vi.fn().mockResolvedValue() },
    setQueue: vi.fn().mockResolvedValue(),
    api: { music: vi.fn() },
  };
  vi.stubGlobal("MusicKit", {
    configure: vi.fn().mockResolvedValue(musicKit),
    PlayerShuffleMode: { songs: "songs" },
    PlaybackStates: { playing: "playing" },
  });
  vi.stubGlobal(
    "fetch",
    vi.fn().mockImplementation((url) => {
      if (url === "/api/apple_music/token")
        return Promise.resolve(fetchJson({ token: "dev-token" }));
      return Promise.resolve(fetchJson({ ok: true }));
    }),
  );

  $("apple-music-connect-btn").click();
  await flush();
  return musicKit;
}

let mod;

describe("apple_music.js", () => {
  beforeEach(async () => {
    vi.resetModules();
    socketMock = makeSocketMock();
    buildDom();
    mod = await import("../../static/v2/js/app/apple_music.js");
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  describe("setAppleMusicStatus", () => {
    it("reflects connected state", () => {
      mod.setAppleMusicStatus(true);
      expect($("apple-music-dot").className).toBe("connected");
      expect($("apple-music-text").textContent).toBe("CONNECTED");
      expect($("apple-music-btn").classList.contains("spotify-live")).toBe(
        true,
      );
    });

    it("reflects disconnected state", () => {
      mod.setAppleMusicStatus(false);
      expect($("apple-music-dot").className).toBe("disconnected");
      expect($("apple-music-btn").classList.contains("spotify-live")).toBe(
        false,
      );
    });
  });

  it("opens and closes the settings panel, including backdrop click", () => {
    $("apple-music-btn").click();
    expect($("apple-music-settings").classList.contains("setup-hidden")).toBe(
      false,
    );

    $("apple-music-settings-close").click();
    expect($("apple-music-settings").classList.contains("setup-hidden")).toBe(
      true,
    );

    $("apple-music-btn").click();
    $("apple-music-settings").click();
    expect($("apple-music-settings").classList.contains("setup-hidden")).toBe(
      true,
    );
  });

  describe("connect flow", () => {
    it("reports not-configured when window.MusicKit is unavailable", async () => {
      $("apple-music-connect-btn").click();
      await flush();
      expect($("apple-music-msg").className).toBe("err");
      expect($("apple-music-msg").textContent).toBe(
        "Apple Music not configured on server.",
      );
    });

    it("reports not-configured when the server has no developer token", async () => {
      vi.stubGlobal("MusicKit", { configure: vi.fn() });
      vi.stubGlobal("fetch", vi.fn().mockResolvedValue(fetchJson({})));

      $("apple-music-connect-btn").click();
      await flush();

      expect($("apple-music-msg").textContent).toBe(
        "Apple Music not configured on server.",
      );
      expect(MusicKit.configure).not.toHaveBeenCalled();
    });

    it("connects successfully and posts the user token", async () => {
      const musicKit = await connectMusicKit();

      expect(musicKit.authorize).toHaveBeenCalled();
      expect($("apple-music-msg").className).toBe("ok");
      expect($("apple-music-msg").textContent).toBe(
        "Connected to Apple Music.",
      );
      expect($("apple-music-dot").className).toBe("connected");
      expect(fetch).toHaveBeenCalledWith(
        "/api/apple_music/user_token",
        expect.objectContaining({
          body: JSON.stringify({ token: "user-token", storefront: "us" }),
        }),
      );
    });

    it("reports an authorization failure", async () => {
      vi.stubGlobal("MusicKit", {
        configure: vi
          .fn()
          .mockResolvedValue({
            authorize: vi.fn().mockRejectedValue(new Error("denied")),
          }),
      });
      vi.stubGlobal(
        "fetch",
        vi.fn().mockResolvedValue(fetchJson({ token: "dev-token" })),
      );

      $("apple-music-connect-btn").click();
      await flush();

      expect($("apple-music-msg").className).toBe("err");
      expect($("apple-music-msg").textContent).toBe("Authorization failed.");
    });
  });

  describe("disconnect", () => {
    it("disconnects successfully", async () => {
      vi.stubGlobal("fetch", vi.fn().mockResolvedValue({}));
      $("apple-music-disconnect-btn").click();
      await flush();
      expect($("apple-music-msg").textContent).toBe(
        "Disconnected from Apple Music.",
      );
      expect($("apple-music-dot").className).toBe("disconnected");
    });

    it("shows an error when disconnect fails", async () => {
      vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new Error("offline")));
      $("apple-music-disconnect-btn").click();
      await flush();
      expect($("apple-music-msg").textContent).toBe(
        "Could not reach the server.",
      );
    });
  });

  describe("apple_music_cmd socket handler", () => {
    it("does nothing when MusicKit was never connected", async () => {
      socketMock.trigger("apple_music_cmd", { action: "play", cb: "cb1" });
      await flush();
      expect(socketMock.emit).not.toHaveBeenCalledWith(
        "apple_music_callback",
        expect.anything(),
      );
    });

    it.each([
      ["play", {}, (mk) => expect(mk.play).toHaveBeenCalled()],
      ["pause", {}, (mk) => expect(mk.pause).toHaveBeenCalled()],
      ["next", {}, (mk) => expect(mk.skipToNextItem).toHaveBeenCalled()],
      [
        "previous",
        {},
        (mk) => expect(mk.skipToPreviousItem).toHaveBeenCalled(),
      ],
      ["volume", { value: 1.5 }, (mk) => expect(mk.volume).toBe(1)],
    ])("handles the %s action", async (action, extra, assertFn) => {
      const musicKit = await connectMusicKit();
      socketMock.trigger("apple_music_cmd", { action, cb: "cb1", ...extra });
      await flush();

      assertFn(musicKit);
      expect(socketMock.emit).toHaveBeenCalledWith("apple_music_callback", {
        cb: "cb1",
        result: "ok",
      });
    });

    it("handles party mode (shuffle + play)", async () => {
      const musicKit = await connectMusicKit();
      socketMock.trigger("apple_music_cmd", { action: "party" });
      await flush();
      expect(musicKit.shuffleMode).toBe("songs");
      expect(musicKit.play).toHaveBeenCalled();
    });

    it("reports what's currently playing", async () => {
      const musicKit = await connectMusicKit();
      musicKit.queue.currentItem = {
        attributes: { name: "Song A", artistName: "Artist A" },
      };
      musicKit.playbackState = "playing";

      socketMock.trigger("apple_music_cmd", {
        action: "now_playing",
        cb: "cb1",
      });
      await flush();

      expect(socketMock.emit).toHaveBeenCalledWith("apple_music_callback", {
        cb: "cb1",
        result: "Currently playing: Song A by Artist A.",
      });
    });

    it("reports nothing playing", async () => {
      await connectMusicKit();
      socketMock.trigger("apple_music_cmd", {
        action: "now_playing",
        cb: "cb1",
      });
      await flush();
      expect(socketMock.emit).toHaveBeenCalledWith("apple_music_callback", {
        cb: "cb1",
        result: "Nothing is currently playing.",
      });
    });

    it("reports now-playing data as JSON", async () => {
      const musicKit = await connectMusicKit();
      musicKit.queue.currentItem = {
        attributes: { name: "Song A", artistName: "Artist A" },
      };

      socketMock.trigger("apple_music_cmd", {
        action: "now_playing_data",
        cb: "cb1",
      });
      await flush();

      expect(socketMock.emit).toHaveBeenCalledWith("apple_music_callback", {
        cb: "cb1",
        result: JSON.stringify({ title: "Song A", artist: "Artist A" }),
      });
    });

    it("adds a song to the queue, swallowing queue errors", async () => {
      const musicKit = await connectMusicKit();
      musicKit.queue.append.mockRejectedValueOnce(new Error("boom"));

      socketMock.trigger("apple_music_cmd", {
        action: "queue_add",
        id: "song1",
        cb: "cb1",
      });
      await flush();

      expect(musicKit.queue.append).toHaveBeenCalledWith({ song: "song1" });
      expect(socketMock.emit).toHaveBeenCalledWith("apple_music_callback", {
        cb: "cb1",
        result: "ok",
      });
    });

    it("searches and plays a matching song", async () => {
      const musicKit = await connectMusicKit();
      musicKit.api.music.mockResolvedValue({
        data: {
          results: {
            songs: {
              data: [
                {
                  id: "s1",
                  attributes: { name: "Song A", artistName: "Artist A" },
                },
              ],
            },
          },
        },
      });

      socketMock.trigger("apple_music_cmd", {
        action: "search_and_play",
        query: "song a",
        cb: "cb1",
      });
      await flush();

      expect(musicKit.setQueue).toHaveBeenCalledWith({ song: "s1" });
      expect(musicKit.play).toHaveBeenCalled();
      expect(socketMock.emit).toHaveBeenCalledWith("apple_music_callback", {
        cb: "cb1",
        result: "Now playing Song A by Artist A.",
      });
    });

    it("searches an album by type", async () => {
      const musicKit = await connectMusicKit();
      musicKit.api.music.mockResolvedValue({
        data: {
          results: {
            albums: { data: [{ id: "a1", attributes: { name: "Album A" } }] },
          },
        },
      });

      socketMock.trigger("apple_music_cmd", {
        action: "search_and_play",
        query: "album a",
        type: "albums",
        cb: "cb1",
      });
      await flush();

      expect(musicKit.setQueue).toHaveBeenCalledWith({ album: "a1" });
    });

    it("reports no results found", async () => {
      const musicKit = await connectMusicKit();
      musicKit.api.music.mockResolvedValue({ data: { results: {} } });

      socketMock.trigger("apple_music_cmd", {
        action: "search_and_play",
        query: "nothing",
        cb: "cb1",
      });
      await flush();

      expect(socketMock.emit).toHaveBeenCalledWith("apple_music_callback", {
        cb: "cb1",
        result: 'Could not find anything matching "nothing".',
      });
    });

    it("reports a playback error and does not crash", async () => {
      const musicKit = await connectMusicKit();
      musicKit.play.mockRejectedValueOnce(new Error("device unavailable"));

      socketMock.trigger("apple_music_cmd", { action: "play", cb: "cb1" });
      await flush();

      expect(socketMock.emit).toHaveBeenCalledWith("apple_music_callback", {
        cb: "cb1",
        result: "Playback error: device unavailable",
      });
    });

    it("does not emit a callback when none was requested", async () => {
      await connectMusicKit();
      socketMock.emit.mockClear();

      socketMock.trigger("apple_music_cmd", { action: "play" });
      await flush();

      expect(socketMock.emit).not.toHaveBeenCalledWith(
        "apple_music_callback",
        expect.anything(),
      );
    });
  });
});
