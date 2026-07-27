/* ===========================================================
   CORE — J.A.R.V.I.S. runtime
   Socket.IO link to the tiny Claude proxy backend, voice out
   (speechSynthesis), voice in (VAD + MediaRecorder → Whisper),
   mode switching (standby <-> awake), and command routing.
   Every feature module imports from here for $, socket, wake/
   sleep/speak, and the setup-modal show/hide.

   It feeds the same window globals the visual modules read:
     window.__speech      {speaking,listening,level,bass,mid,high}  (sphere.js, hud.js)
     window.__recognition  status string                            (hud.js)
     window.__setMode()/__getMode()  (standby.js)
     window.__chat / window.__sendMessage  (chat.js)
   =========================================================== */

export const $ = (id) => document.getElementById(id);

window.__speech = {
  speaking: false,
  listening: false,
  level: 0,
  bass: 0,
  mid: 0,
  high: 0,
};
window.__recognition = "CONNECTING…";
document.body.classList.add("stats-open"); // show the HUD panels on the awake screen

// ===================================================================
//  SOCKET.IO
// ===================================================================
export const socket = io();
let _configured = false;

export function setConfigured(v) {
  _configured = !!v;
}

// ===================================================================
//  MODES  (standby <-> awake) — boots DORMANT, like the real JARVIS
// ===================================================================
let _standby = true;
export function isStandby() {
  return _standby;
}
export function applyMode() {
  if (_standby) {
    window.__setMode("standby", true);
    window.__recognition = _configured
      ? "STANDBY — SAY “JARVIS”"
      : "AWAITING SETUP";
  } else {
    window.__setMode("awake", true);
    window.__recognition =
      _vizState === "idle" ? "LISTENING…" : RECOG[_vizState] || "LISTENING…";
  }
}
export function wake() {
  if (!_standby) return;
  _standby = false;
  applyMode();
}
export function sleep() {
  if (_standby) return;
  _standby = true;
  applyMode();
}

const RECOG = {
  idle: "LISTENING…",
  listening: "LISTENING",
  thinking: "PROCESSING",
  speaking: "RESPONDING",
};
let _vizState = "idle";

// ===================================================================
//  TEXT-TO-SPEECH  —  Windows voices via the browser
// ===================================================================
const synth = window.speechSynthesis;
let _voice = null;
function scoreVoice(v) {
  const n = (v.name || "").toLowerCase();
  let s = 0;
  if (v.lang && v.lang.toLowerCase().startsWith("en")) s += 4;
  if (v.lang && v.lang.toLowerCase() === "en-gb") s += 3; // JARVIS is British
  if (/(david|george|ryan|guy|james|thomas|daniel)/.test(n)) s += 3; // male voices
  if (n.includes("microsoft")) s += 1;
  return s;
}

export function pickBestVoice(voices) {
  if (!voices.length) return null;
  return (
    voices.slice().sort((a, b) => scoreVoice(b) - scoreVoice(a))[0] || voices[0]
  );
}

function pickVoice() {
  if (!synth) return;
  const voices = synth.getVoices();
  if (!voices.length) return;
  _voice = pickBestVoice(voices);
}
if (synth) {
  pickVoice();
  synth.onvoiceschanged = pickVoice;
}

// Per-user TTS clarity (Phase 10 accessibility, opt-in) — defaults match
// today's behavior; boot.js overrides these from /api/status right after
// load, tts_settings panel (accessibility.js) updates them live on save.
let _ttsRate = 1.0,
  _ttsPitch = 1.0,
  _ttsVolume = 1.0;
export function setTtsPrefs({ rate, pitch, volume } = {}) {
  if (typeof rate === "number") _ttsRate = rate;
  if (typeof pitch === "number") _ttsPitch = pitch;
  if (typeof volume === "number") _ttsVolume = volume;
}

// sentence queue → spoken one at a time, with a synthetic orb envelope while speaking
let _ttsQ = [],
  _ttsActive = false;
export function speak(text) {
  if (!text) return;
  if (_silent) return; // Silent Mode: text only, never speak
  if (!synth) return; // no speech synthesis → silent (chat still shows text)
  _ttsQ.push(text);
  if (!_ttsActive) _ttsNext();
}
function _ttsNext() {
  if (_ttsQ.length === 0) {
    _ttsActive = false;
    _speaking = false;
    if (!_standby) window.__recognition = "LISTENING…";
    _vizState = "idle";
    return;
  }
  _ttsActive = true;
  _speaking = true;
  const text = _ttsQ.shift();
  const u = new SpeechSynthesisUtterance(text);
  if (_voice) u.voice = _voice;
  u.rate = _ttsRate;
  u.pitch = _ttsPitch;
  u.volume = _ttsVolume;
  u.onboundary = () => {
    _wordPunch = 1;
  }; // a little orb kick per word
  u.onend = () => {
    _ttsNext();
  };
  u.onerror = () => {
    _ttsNext();
  };
  try {
    synth.speak(u);
  } catch (e) {
    _ttsNext();
  }
}
export function stopSpeaking() {
  _ttsQ = [];
  try {
    synth && synth.cancel();
  } catch (e) {}
  _ttsActive = false;
  _speaking = false;
}

// ---- synthetic voice envelope so the orb + waveform react while speaking ----
// speechSynthesis can't be tapped by the Web Audio analyser, so we fabricate a
// lively-but-smooth level (plus a per-word punch) — visually equivalent.
let _speaking = false,
  _wordPunch = 0,
  _env = 0,
  _t = 0;
// Speech-recognition state is declared up here as well, so driveViz()'s first
// synchronous call (below) doesn't hit the temporal dead zone on _listening.
let _listening = false,
  _thinking = false,
  _micOk = false;
function driveViz() {
  requestAnimationFrame(driveViz);
  _t += 0.08;
  if (_speaking) {
    const osc = (Math.sin(_t * 3.1) * 0.5 + 0.5) * 0.45 + 0.25; // base wobble
    const target = Math.min(1, osc + _wordPunch * 0.5);
    _env += (target - _env) * (target > _env ? 0.5 : 0.12);
    _wordPunch *= 0.82;
  } else {
    _env *= 0.9;
  }
  window.__speech = {
    speaking: _speaking,
    listening: _listening && !_speaking,
    level: _env,
    bass: _env * 0.9,
    mid: _env * 0.7,
    high: _env * 0.5,
  };
}
driveViz();

// ===================================================================
//  SPEECH RECOGNITION  — local Whisper via VAD + MediaRecorder
// ===================================================================
let _micStream = null,
  _vadCtx = null,
  _vadRafId = null,
  _vadRec = null;

export function startRecognition() {
  if (_silent) {
    window.__recognition = "SILENT — TEXT ONLY";
    return;
  }
  if (_micStream) return; // already running
  if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
    window.__recognition = "MIC NOT SUPPORTED";
    return;
  }
  navigator.mediaDevices
    .getUserMedia({ audio: true, video: false })
    .then((stream) => {
      _micOk = true;
      _micStream = stream;
      window.__recognition = "LISTENING…";
      _vadLoop(stream);
    })
    .catch(() => {
      _micOk = false;
      window.__recognition = "MIC BLOCKED — TYPE BELOW";
      if (window.__chat)
        window.__chat.addMsg(
          "Microphone's blocked, sir — you can type to me below.",
          "in",
        );
    });
}

// stops and fully releases the mic (used by Silent Mode) — safe to call anytime
export function stopRecognition() {
  if (_vadRafId) cancelAnimationFrame(_vadRafId);
  _vadRafId = null;
  if (_vadRec && _vadRec.state === "recording") {
    _vadRec.ondataavailable = null;
    _vadRec.onstop = null;
    try {
      _vadRec.stop();
    } catch (e) {}
  }
  _vadRec = null;
  if (_micStream) {
    _micStream.getTracks().forEach((t) => t.stop());
    _micStream = null;
  }
  if (_vadCtx) {
    try {
      _vadCtx.close();
    } catch (e) {}
    _vadCtx = null;
  }
  _micOk = false;
}

function _vadLoop(stream) {
  const ctx = new (window.AudioContext || window.webkitAudioContext)();
  _vadCtx = ctx;
  const source = ctx.createMediaStreamSource(stream);
  const analyser = ctx.createAnalyser();
  analyser.fftSize = 512;
  source.connect(analyser);
  const buf = new Uint8Array(analyser.frequencyBinCount);

  const THRESHOLD = 30; // 0–255 amplitude; adjust if too sensitive
  const SILENCE_MS = 800; // ms of quiet before we cut the recording
  const MIN_MS = 300; // ignore clips shorter than this (noise bursts)

  const mime =
    ["audio/webm;codecs=opus", "audio/webm", "audio/ogg"].find((t) =>
      MediaRecorder.isTypeSupported(t),
    ) || "";

  let rec = null,
    chunks = [],
    recStart = 0,
    lastLoud = 0;

  function tick() {
    _vadRafId = requestAnimationFrame(tick);
    analyser.getByteFrequencyData(buf);
    const avg = buf.reduce((s, v) => s + v, 0) / buf.length;
    const now = Date.now();
    const loud = avg > THRESHOLD && !_speaking && !_thinking;
    if (loud) lastLoud = now;
    const silentFor = now - lastLoud;

    if (loud && !rec) {
      chunks = [];
      recStart = now;
      rec = new MediaRecorder(stream, mime ? { mimeType: mime } : {});
      _vadRec = rec;
      rec.ondataavailable = (e) => e.data.size > 0 && chunks.push(e.data);
      rec.onstop = () => {
        const r = rec;
        rec = null;
        _vadRec = null;
        if (Date.now() - recStart >= MIN_MS && chunks.length)
          _transcribe(new Blob(chunks, { type: r.mimeType }));
      };
      rec.start(100);
    }

    if (rec && rec.state === "recording" && silentFor > SILENCE_MS) {
      rec.stop();
    }
  }

  tick();
}

// ===================================================================
//  SILENT MODE  — text in, text out: mic fully released, TTS muted
// ===================================================================
let _silent = false;
try {
  _silent = localStorage.getItem("jarvis_silent_mode") === "1";
} catch (e) {}

export function isSilentMode() {
  return _silent;
}
export function setSilentMode(v) {
  v = !!v;
  _silent = v;
  try {
    localStorage.setItem("jarvis_silent_mode", v ? "1" : "0");
  } catch (e) {}
  if (v) {
    stopSpeaking();
    stopRecognition();
    if (_standby) wake();
    window.__recognition = "SILENT — TEXT ONLY";
    if (window.__chat && window.__chat.setOpen) window.__chat.setOpen(true);
  } else {
    startRecognition();
    if (!_standby) window.__recognition = "LISTENING…";
  }
}

let _lastSpeakerId = null;

async function _transcribe(blob) {
  const fd = new FormData();
  fd.append("audio", blob, "speech.webm");
  try {
    const r = await fetch("/api/transcribe", { method: "POST", body: fd });
    const { text, speaker_id, speaker_name } = await r.json();
    if (speaker_id) _lastSpeakerId = speaker_id;
    const t = (text || "").trim();
    if (t && t.split(/\s+/).length >= 2) {
      console.log("[STT]", t, speaker_name ? `(${speaker_name})` : "");
      handleHeard(t);
    } else if (t) {
      console.log("[STT] ignored (too short):", t);
    }
  } catch (e) {
    console.warn("[STT] error:", e);
  }
}

function handleHeard(text) {
  const lower = text.toLowerCase();
  if (_standby) {
    if (lower.includes("jarvis")) {
      wake();
      // strip the wake word; if a command follows, run it, else just acknowledge
      const cmd = text.replace(/.*?jarvis[,.\s!?]*/i, "").trim();
      if (cmd.length > 2) sendCommand(cmd);
      else {
        const acks = [
          "Yes, sir?",
          "Sir?",
          "Go ahead.",
          "At your service.",
          "Right here, sir.",
          "You rang, sir?",
        ];
        const a = acks[Math.floor(Math.random() * acks.length)];
        if (window.__chat) window.__chat.addMsg(a, "in");
        _vizState = "speaking";
        window.__recognition = "RESPONDING";
        speak(a);
      }
    }
    return;
  }
  // awake: standby phrases put him to sleep
  if (
    /\b(standby|go to sleep|sleep mode|that'?s all|goodnight)\b/.test(lower)
  ) {
    if (window.__chat) window.__chat.addMsg("Entering standby, sir.", "in");
    speak("Entering standby, sir.");
    setTimeout(sleep, 900);
    return;
  }
  sendCommand(text);
}

// ===================================================================
//  SENDING TO CLAUDE
// ===================================================================
function sendCommand(text) {
  if (!_configured) {
    showSetup();
    return;
  }
  if (window.__chat) window.__chat.addMsg(text, "out"); // show the heard/typed command
  window.__justTyped = { text, t: Date.now() };
  socket.emit("user_message", { text, speaker_id: _lastSpeakerId });
}
// chat.js hands typed text here (it already renders the 'out' bubble itself)
window.__sendMessage = (text) => {
  if (!_configured) {
    showSetup();
    return;
  }
  if (_standby) wake();
  socket.emit("user_message", { text, speaker_id: _lastSpeakerId });
};

// ===================================================================
//  INCOMING — Claude's reply, accumulated into one chat bubble per turn
// ===================================================================
let _turnEl = null,
  _turnText = "";
function renderTurn() {
  if (!window.__chat) return;
  if (!_turnEl) {
    window.__chat.setTyping(false);
    _turnEl = window.__chat.addMsg(_turnText, "in");
  } else window.__chat.updateMsg(_turnEl, _turnText);
}
function endTurn() {
  _turnEl = null;
  _turnText = "";
}

socket.on("status", ({ state }) => {
  _vizState = state;
  _thinking = state === "thinking";
  _listening = state === "idle" && !_standby;
  if (state === "thinking") {
    endTurn();
    if (window.__chat) window.__chat.setTyping(true);
  }
  if (!_standby) {
    window.__recognition =
      _silent && state === "idle"
        ? "SILENT — TEXT ONLY"
        : RECOG[state] || state.toUpperCase();
  }
});

socket.on("speak_sentence", ({ text }) => {
  const t = (text || "").trim();
  if (!t) return;
  _turnText = _turnText ? _turnText + " " + t : t;
  renderTurn();
  if (!_standby) {
    _vizState = "speaking";
    window.__recognition = "RESPONDING";
  }
  speak(t);
});

socket.on("response_done", ({ text }) => {
  if (text && text.length >= _turnText.length) {
    _turnText = text;
    renderTurn();
  }
  if (window.__chat) window.__chat.setTyping(false);
});

socket.on("need_setup", () => {
  _configured = false;
  showSetup();
});
socket.on("config_state", ({ configured }) => {
  _configured = !!configured;
  if (_configured) hideSetup();
  else showSetup();
});

// ---- live telemetry / weather (read by hud.js) ----
socket.on("hud_update", (d) => {
  window.__telemetry = d;
});
socket.on("weather_update", (d) => {
  window.__weather = d;
});

socket.on("connect", () => {
  const ts = $("top-status");
  if (ts) {
    ts.textContent = "ONLINE";
    ts.style.color = "var(--cyan-bright)";
  }
});
socket.on("disconnect", () => {
  const ts = $("top-status");
  if (ts) {
    ts.textContent = "OFFLINE";
    ts.style.color = "var(--amber)";
  }
  window.__recognition = "OFFLINE";
});

// ===================================================================
//  PUSH-TO-TALK  (SPACE) — also a manual wake
// ===================================================================
const isTyping = (t) =>
  t &&
  (t.tagName === "INPUT" || t.tagName === "TEXTAREA" || t.isContentEditable);
document.addEventListener("keydown", (e) => {
  if (e.key === " " && !isTyping(e.target) && !e.repeat) {
    e.preventDefault();
    if (_standby) wake();
  }
});

// ===================================================================
//  FIRST-RUN SETUP MODAL — show/hide (form logic lives in setup.js)
// ===================================================================
const setupEl = $("setup"),
  keyInput = $("setup-key");

export function showSetup() {
  if (setupEl) setupEl.classList.remove("setup-hidden");
  setTimeout(() => keyInput && keyInput.focus(), 200);
}
export function hideSetup() {
  if (setupEl) setupEl.classList.add("setup-hidden");
}
