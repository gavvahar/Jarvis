/* ===========================================================
   APPLE MUSIC
   =========================================================== */
import { $, socket } from "./core.js";

const appleMusicBtn = $("apple-music-btn");
const appleMusicSettingsEl = $("apple-music-settings");
const appleMusicSettingsClose = $("apple-music-settings-close");
export const appleMusicConnectBtn = $("apple-music-connect-btn");
const appleMusicDisconnectBtn = $("apple-music-disconnect-btn");
const appleMusicDot = $("apple-music-dot");
const appleMusicTextEl = $("apple-music-text");
const appleMusicMsg = $("apple-music-msg");

let _musicKit = null;

export function setAppleMusicStatus(connected) {
  if (appleMusicDot)
    appleMusicDot.className = connected ? "connected" : "disconnected";
  if (appleMusicTextEl)
    appleMusicTextEl.textContent = connected ? "CONNECTED" : "NOT CONNECTED";
  if (appleMusicBtn)
    appleMusicBtn.classList.toggle("spotify-live", connected);
}

function showAppleMusicSettings() {
  if (appleMusicSettingsEl)
    appleMusicSettingsEl.classList.remove("setup-hidden");
}

function hideAppleMusicSettings() {
  if (appleMusicSettingsEl)
    appleMusicSettingsEl.classList.add("setup-hidden");
}

export async function initMusicKit() {
  if (_musicKit || !window.MusicKit) return;
  try {
    const resp = await fetch("/api/apple_music/token");
    const data = await resp.json();
    if (!data.token) return;
    _musicKit = await MusicKit.configure({
      developerToken: data.token,
      app: { name: "Jarvis", build: "1.0" },
    });
  } catch (e) {
    console.warn("[MusicKit] init failed:", e);
  }
}

document.addEventListener("musickitloaded", initMusicKit);

if (appleMusicBtn)
  appleMusicBtn.addEventListener("click", showAppleMusicSettings);
if (appleMusicSettingsClose)
  appleMusicSettingsClose.addEventListener("click", hideAppleMusicSettings);
appleMusicSettingsEl &&
  appleMusicSettingsEl.addEventListener("click", (e) => {
    if (e.target === appleMusicSettingsEl) hideAppleMusicSettings();
  });

if (appleMusicConnectBtn) {
  appleMusicConnectBtn.addEventListener("click", async () => {
    await initMusicKit();
    if (!_musicKit) {
      if (appleMusicMsg) {
        appleMusicMsg.className = "err";
        appleMusicMsg.textContent = "Apple Music not configured on server.";
      }
      return;
    }
    try {
      const userToken = await _musicKit.authorize();
      await fetch("/api/apple_music/user_token", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          token: userToken,
          storefront: _musicKit.storefrontId || "us",
        }),
      });
      setAppleMusicStatus(true);
      if (appleMusicMsg) {
        appleMusicMsg.className = "ok";
        appleMusicMsg.textContent = "Connected to Apple Music.";
      }
    } catch (e) {
      if (appleMusicMsg) {
        appleMusicMsg.className = "err";
        appleMusicMsg.textContent = "Authorization failed.";
      }
    }
  });
}

if (appleMusicDisconnectBtn) {
  appleMusicDisconnectBtn.addEventListener("click", async () => {
    try {
      if (_musicKit) await _musicKit.unauthorize().catch(() => {});
      await fetch("/api/apple_music/disconnect", { method: "POST" });
      setAppleMusicStatus(false);
      if (appleMusicMsg) {
        appleMusicMsg.className = "ok";
        appleMusicMsg.textContent = "Disconnected from Apple Music.";
      }
    } catch {
      if (appleMusicMsg) {
        appleMusicMsg.className = "err";
        appleMusicMsg.textContent = "Could not reach the server.";
      }
    }
  });
}

socket.on("apple_music_cmd", async (data) => {
  if (!_musicKit) {
    await initMusicKit();
  }
  if (!_musicKit) return;
  const { action, cb, value, query, type } = data;
  let result = "ok";
  try {
    if (action === "play") await _musicKit.play();
    else if (action === "pause") await _musicKit.pause();
    else if (action === "next") await _musicKit.skipToNextItem();
    else if (action === "previous") await _musicKit.skipToPreviousItem();
    else if (action === "volume")
      _musicKit.volume = Math.max(0, Math.min(1, value));
    else if (action === "party") {
      _musicKit.shuffleMode = MusicKit.PlayerShuffleMode.songs;
      await _musicKit.play();
    } else if (action === "now_playing") {
      const item = _musicKit.queue?.currentItem;
      const playing =
        _musicKit.playbackState === MusicKit.PlaybackStates.playing;
      result = item
        ? `Currently ${playing ? "playing" : "paused"}: ${item.attributes?.name} by ${item.attributes?.artistName}.`
        : "Nothing is currently playing.";
    } else if (action === "now_playing_data") {
      const item = _musicKit.queue?.currentItem;
      result = JSON.stringify(
        item
          ? {
              title: item.attributes?.name || "",
              artist: item.attributes?.artistName || "",
            }
          : { title: null, artist: null },
      );
    } else if (action === "queue_add") {
      try {
        await _musicKit.queue.append({ song: data.id });
      } catch (_) {}
      result = "ok";
    } else if (action === "search_and_play") {
      const sf = _musicKit.storefrontId || "us";
      const resp = await _musicKit.api.music(`/v1/catalog/${sf}/search`, {
        term: query,
        types: type || "songs",
        limit: "1",
      });
      const results = resp.data?.results;
      const key = type || "songs";
      const items = results?.[key]?.data;
      if (items?.length) {
        const id = items[0].id;
        const name = items[0].attributes?.name;
        const artist =
          items[0].attributes?.artistName ||
          items[0].attributes?.curatorName ||
          "";
        if (key === "songs") await _musicKit.setQueue({ song: id });
        else if (key === "albums") await _musicKit.setQueue({ album: id });
        else if (key === "playlists")
          await _musicKit.setQueue({ playlist: id });
        else if (key === "artists") await _musicKit.setQueue({ artist: id });
        await _musicKit.play();
        result = `Now playing ${name}${artist ? " by " + artist : ""}.`;
      } else {
        result = `Could not find anything matching "${query}".`;
      }
    }
  } catch (e) {
    result = `Playback error: ${e.message || e}`;
    console.error("[AppleMusic]", e);
  }
  if (cb) socket.emit("apple_music_callback", { cb, result });
});
