/* ===========================================================
   SILENT MODE  — topbar toggle for text-only sessions.
   Mic + TTS live in core.js; this module just owns the button.
   =========================================================== */
import { $, isSilentMode, setSilentMode } from "./core.js";

const btn = $("silent-btn");

function reflect() {
  if (btn) btn.classList.toggle("silent-active", isSilentMode());
}
reflect(); // core.js reads localStorage synchronously, so this already reflects a persisted state

if (btn) {
  btn.addEventListener("click", () => {
    setSilentMode(!isSilentMode());
    reflect();
  });
}
