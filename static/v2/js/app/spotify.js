/* ===========================================================
   SPOTIFY
   =========================================================== */
import { $ } from "./core.js";

const spotifyBtn = $("spotify-btn");
const spotifySettingsEl = $("spotify-settings");
const spotifySettingsClose = $("spotify-settings-close");
const spotifyDisconnectBtn = $("spotify-disconnect-btn");
const spotifyDot = $("spotify-dot");
const spotifyTextEl = $("spotify-text");
const spotifyMsg = $("spotify-msg");
const spotifyRedirectUri = $("spotify-redirect-uri");
export const spotifyAuthLink = $("spotify-auth-link");

if (spotifyRedirectUri) {
  spotifyRedirectUri.textContent =
    window.location.origin + "/auth/spotify/callback";
}

export function setSpotifyStatus(connected) {
  if (spotifyDot)
    spotifyDot.className = connected ? "connected" : "disconnected";
  if (spotifyTextEl)
    spotifyTextEl.textContent = connected ? "CONNECTED" : "NOT CONNECTED";
  if (spotifyBtn) spotifyBtn.classList.toggle("spotify-live", connected);
}

function showSpotifySettings() {
  if (spotifySettingsEl) spotifySettingsEl.classList.remove("setup-hidden");
}

function hideSpotifySettings() {
  if (spotifySettingsEl) spotifySettingsEl.classList.add("setup-hidden");
}

if (spotifyBtn) spotifyBtn.addEventListener("click", showSpotifySettings);
if (spotifySettingsClose)
  spotifySettingsClose.addEventListener("click", hideSpotifySettings);
spotifySettingsEl &&
  spotifySettingsEl.addEventListener("click", (e) => {
    if (e.target === spotifySettingsEl) hideSpotifySettings();
  });

if (spotifyDisconnectBtn) {
  spotifyDisconnectBtn.addEventListener("click", async () => {
    try {
      await fetch("/api/spotify/disconnect", { method: "POST" });
      setSpotifyStatus(false);
      if (spotifyMsg) {
        spotifyMsg.className = "ok";
        spotifyMsg.textContent = "Disconnected from Spotify.";
      }
    } catch {
      if (spotifyMsg) {
        spotifyMsg.className = "err";
        spotifyMsg.textContent = "Could not reach the server.";
      }
    }
  });
}

if (
  new URLSearchParams(window.location.search).get("spotify_connected") === "1"
) {
  history.replaceState({}, "", "/");
  showSpotifySettings();
}
