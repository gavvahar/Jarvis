/* ===========================================================
   APP BRIDGE — J.A.R.V.I.S.
   Entry point: pulls in every feature module for its side
   effects (DOM wiring, socket listeners), then boot.js runs
   last to fetch /api/status and hydrate every panel's state.
   =========================================================== */
import "./core.js";
import "./setup.js";
import "./settings.js";
import "./ha.js";
import "./pim.js";
import "./meeting.js";
import "./messages.js";
import "./doorbell.js";
import "./vision.js";
import "./garage.js";
import "./finance.js";
import "./tesla.js";
import "./spotify.js";
import "./apple_music.js";
import "./party.js";
import "./silent.js";
import "./boot.js";
import "./pwa.js";
