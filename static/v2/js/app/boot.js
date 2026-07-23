/* ===========================================================
   BOOT — ask the backend whether we're already configured, then
   hydrate every settings panel's connected/disconnected state.
   =========================================================== */
import {
  $,
  setConfigured,
  showSetup,
  hideSetup,
  applyMode,
  startRecognition,
  isSilentMode,
  setSilentMode,
  setTtsPrefs,
} from "./core.js";
import { setHaStatus, haTokenInput } from "./ha.js";
import {
  setCalendarStatus,
  setContactsStatus,
  setEmailStatus,
  calendarPasswordInput,
  contactsPasswordInput,
  emailPasswordInput,
} from "./pim.js";
import { setGarageStatus, myqPasswordInput } from "./garage.js";
import { setTeslaStatus, teslaFleetAuthBtn } from "./tesla.js";
import { setSpotifyStatus, spotifyAuthLink } from "./spotify.js";
import {
  setAppleMusicStatus,
  initMusicKit,
  appleMusicConnectBtn,
} from "./apple_music.js";

fetch("/api/status")
  .then((r) => r.json())
  .then((d) => {
    setConfigured(!!d.configured);
    setTtsPrefs({ rate: d.tts_rate, pitch: d.tts_pitch, volume: d.tts_volume });
    const ml = $("mod-link");
    if (ml && d.provider)
      ml.textContent =
        {
          anthropic: "CLAUDE",
          openai: "OPENAI",
          openai_compatible: "CUSTOM",
        }[d.provider] || "LLM";
    if (d.ha_url) {
      const haUrlEl = $("setup-ha-url");
      if (haUrlEl) haUrlEl.value = d.ha_url;
    }
    setHaStatus(!!d.ha_configured, d.ha_url || "");
    if (d.ha_configured && haTokenInput) haTokenInput.dataset.hasExisting = "1";
    setCalendarStatus(
      !!d.calendar_configured,
      d.calendar_url || "",
      d.calendar_username || "",
    );
    setContactsStatus(
      !!d.contacts_configured,
      d.contacts_url || "",
      d.contacts_username || "",
    );
    if (d.calendar_configured && calendarPasswordInput)
      calendarPasswordInput.dataset.hasExisting = "1";
    if (d.contacts_configured && contactsPasswordInput)
      contactsPasswordInput.dataset.hasExisting = "1";
    setEmailStatus(
      !!d.email_configured,
      d.email_host || "",
      d.email_username || "",
    );
    if (d.email_configured && emailPasswordInput)
      emailPasswordInput.dataset.hasExisting = "1";
    setGarageStatus(!!d.myq_configured);
    if (d.myq_configured && myqPasswordInput)
      myqPasswordInput.dataset.hasExisting = "1";
    setTeslaStatus(d.tesla_method || "");
    if (!d.tesla_fleet_enabled && teslaFleetAuthBtn) {
      teslaFleetAuthBtn.style.opacity = "0.4";
      teslaFleetAuthBtn.style.pointerEvents = "none";
      teslaFleetAuthBtn.title = "TESLA_CLIENT_ID not configured in .env";
    }
    setSpotifyStatus(!!d.spotify_configured);
    if (!d.spotify_client_enabled && spotifyAuthLink) {
      spotifyAuthLink.style.opacity = "0.4";
      spotifyAuthLink.style.pointerEvents = "none";
      spotifyAuthLink.title = "SPOTIFY_CLIENT_ID not configured in .env";
    }
    setAppleMusicStatus(!!d.apple_music_configured);
    if (!d.apple_music_server_enabled && appleMusicConnectBtn) {
      appleMusicConnectBtn.style.opacity = "0.4";
      appleMusicConnectBtn.style.pointerEvents = "none";
      appleMusicConnectBtn.title = "APPLE_MUSIC_* keys not configured in .env";
    }
    if (d.apple_music_server_enabled) initMusicKit();
    if (
      new URLSearchParams(window.location.search).get("spotify_connected") ===
      "1"
    ) {
      setSpotifyStatus(true);
    }
    if (d.configured) hideSetup();
    else showSetup();
    applyMode();
    startRecognition();
    // reapply Silent Mode's wake + chat-open side effects if it was left on
    if (isSilentMode()) setSilentMode(true);
  })
  .catch(() => {
    showSetup();
    applyMode();
  });
