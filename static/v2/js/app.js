/* ===========================================================
   APP BRIDGE — J.A.R.V.I.S. Starter Kit
   The integration layer for the stripped-down build:
     • Socket.IO link to the tiny Claude proxy backend
     • VOICE OUT  : window.speechSynthesis (Windows voices)
     • VOICE IN   : webkitSpeechRecognition (no GPU, no libs)
     • Wake word  : say "JARVIS" to wake; "standby" to sleep
     • First-run  : captures the user's Claude API key

   It feeds the same window globals the visual modules read:
     window.__speech      {speaking,listening,level,bass,mid,high}  (sphere.js, hud.js)
     window.__recognition  status string                            (hud.js)
     window.__setMode()/__getMode()  (standby.js)
     window.__chat / window.__sendMessage  (chat.js)
   =========================================================== */
(function () {
  "use strict";

  const $ = (id) => document.getElementById(id);

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
  const socket = io();
  let _configured = false;

  // ===================================================================
  //  MODES  (standby <-> awake) — boots DORMANT, like the real JARVIS
  // ===================================================================
  let _standby = true;
  function applyMode() {
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
  function wake() {
    if (!_standby) return;
    _standby = false;
    applyMode();
  }
  function sleep() {
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
  function pickVoice() {
    if (!synth) return;
    const voices = synth.getVoices();
    if (!voices.length) return;
    const score = (v) => {
      const n = (v.name || "").toLowerCase();
      let s = 0;
      if (v.lang && v.lang.toLowerCase().startsWith("en")) s += 4;
      if (v.lang && v.lang.toLowerCase() === "en-gb") s += 3; // JARVIS is British
      if (/(david|george|ryan|guy|james|thomas|daniel)/.test(n)) s += 3; // male voices
      if (n.includes("microsoft")) s += 1;
      return s;
    };
    _voice = voices.slice().sort((a, b) => score(b) - score(a))[0] || voices[0];
  }
  if (synth) {
    pickVoice();
    synth.onvoiceschanged = pickVoice;
  }

  // sentence queue → spoken one at a time, with a synthetic orb envelope while speaking
  let _ttsQ = [],
    _ttsActive = false;
  function speak(text) {
    if (!text) return;
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
    u.rate = 1.0;
    u.pitch = 1.0;
    u.volume = 1.0;
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
  function stopSpeaking() {
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
  function startRecognition() {
    if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
      window.__recognition = "MIC NOT SUPPORTED";
      return;
    }
    navigator.mediaDevices
      .getUserMedia({ audio: true, video: false })
      .then((stream) => {
        _micOk = true;
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

  function _vadLoop(stream) {
    const ctx = new (window.AudioContext || window.webkitAudioContext)();
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
      requestAnimationFrame(tick);
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
        rec.ondataavailable = (e) => e.data.size > 0 && chunks.push(e.data);
        rec.onstop = () => {
          const r = rec;
          rec = null;
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

  async function _transcribe(blob) {
    const fd = new FormData();
    fd.append("audio", blob, "speech.webm");
    try {
      const r = await fetch("/api/transcribe", { method: "POST", body: fd });
      const { text } = await r.json();
      const t = (text || "").trim();
      if (t && t.split(/\s+/).length >= 2) {
        console.log("[STT]", t);
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
    socket.emit("user_message", { text });
  }
  // chat.js hands typed text here (it already renders the 'out' bubble itself)
  window.__sendMessage = (text) => {
    if (!_configured) {
      showSetup();
      return;
    }
    if (_standby) wake();
    socket.emit("user_message", { text });
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
    if (!_standby) window.__recognition = RECOG[state] || state.toUpperCase();
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
  //  FIRST-RUN SETUP  (provider + model + API key)
  // ===================================================================
  const setupEl = $("setup"),
    keyInput = $("setup-key"),
    setupForm = $("setup-form"),
    setupMsg = $("setup-msg"),
    setupGo = $("setup-go"),
    provSel = $("setup-provider"),
    modelSel = $("setup-model"),
    modelCustom = $("setup-model-custom"),
    baseUrl = $("setup-baseurl"),
    helpLink = $("setup-help");

  // Curated model options per provider. "" value = the "Other (type below)" choice.
  const MODELS = {
    anthropic: [
      { v: "claude-haiku-4-5", t: "Claude Haiku 4.5 — fast & affordable" },
      { v: "claude-sonnet-4-6", t: "Claude Sonnet 4.6 — most in-character" },
      { v: "claude-opus-4-8", t: "Claude Opus 4.8 — most capable" },
      { v: "", t: "Other (type below)…" },
    ],
    openai: [
      { v: "gpt-4o-mini", t: "GPT-4o mini — fast & affordable" },
      { v: "gpt-4o", t: "GPT-4o — capable" },
      { v: "gpt-4.1-mini", t: "GPT-4.1 mini" },
      { v: "gpt-4.1", t: "GPT-4.1 — most capable" },
      { v: "", t: "Other (type below)…" },
    ],
    openai_compatible: [{ v: "", t: "Type the model name below…" }],
  };
  const HELP = {
    anthropic: {
      url: "https://console.anthropic.com/settings/keys",
      txt: "Get an Anthropic key →",
      ph: "sk-ant-...",
    },
    openai: {
      url: "https://platform.openai.com/api-keys",
      txt: "Get an OpenAI key →",
      ph: "sk-...",
    },
    openai_compatible: {
      url: "https://openrouter.ai/keys",
      txt: "e.g. get an OpenRouter key →",
      ph: "your API key",
    },
  };

  function refreshProviderUI() {
    const p = provSel.value;
    // model dropdown
    modelSel.innerHTML = "";
    (MODELS[p] || []).forEach((m) => {
      const o = document.createElement("option");
      o.value = m.v;
      o.textContent = m.t;
      modelSel.appendChild(o);
    });
    // help link + key placeholder
    const h = HELP[p] || HELP.anthropic;
    if (helpLink) {
      helpLink.href = h.url;
      helpLink.textContent = h.txt;
    }
    if (keyInput) keyInput.placeholder = h.ph;
    // base URL only for the compatible provider
    baseUrl.style.display = p === "openai_compatible" ? "block" : "none";
    refreshModelUI();
  }
  function refreshModelUI() {
    // show the free-text model box when "Other" (empty value) is selected
    const custom = modelSel.value === "";
    modelCustom.style.display = custom ? "block" : "none";
  }
  if (provSel) {
    provSel.addEventListener("change", refreshProviderUI);
    refreshProviderUI();
  }
  if (modelSel) modelSel.addEventListener("change", refreshModelUI);

  function showSetup() {
    if (setupEl) setupEl.classList.remove("setup-hidden");
    setTimeout(() => keyInput && keyInput.focus(), 200);
  }
  function hideSetup() {
    if (setupEl) setupEl.classList.add("setup-hidden");
  }

  if (setupForm) {
    setupForm.addEventListener("submit", async (e) => {
      e.preventDefault();
      const provider = provSel.value;
      const key = (keyInput.value || "").trim();
      const model = modelSel.value || (modelCustom.value || "").trim();
      const base_url = (baseUrl.value || "").trim();
      if (!key && provider !== "openai_compatible") {
        setupMsg.className = "err";
        setupMsg.textContent = "Please paste your API key.";
        return;
      }
      if (!model) {
        setupMsg.className = "err";
        setupMsg.textContent = "Please choose or type a model.";
        return;
      }
      if (provider === "openai_compatible" && !base_url) {
        setupMsg.className = "err";
        setupMsg.textContent = "This provider needs a base URL.";
        return;
      }
      setupGo.disabled = true;
      setupMsg.className = "";
      setupMsg.textContent = "Verifying…";
      try {
        const ha_url = ($("setup-ha-url").value || "").trim();
        const ha_token = ($("setup-ha-token").value || "").trim();
        const res = await fetch("/api/save_config", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            provider,
            key,
            model,
            base_url,
            ha_url,
            ha_token,
          }),
        });
        const data = await res.json();
        if (data.ok) {
          _configured = true;
          setupMsg.className = "ok";
          setupMsg.textContent = "Connected. Welcome aboard, sir.";
          keyInput.value = "";
          setTimeout(() => {
            hideSetup();
          }, 1100);
        } else {
          setupMsg.className = "err";
          setupMsg.textContent = data.error || "That was rejected.";
        }
      } catch (err) {
        setupMsg.className = "err";
        setupMsg.textContent = "Could not reach the server. Is it running?";
      } finally {
        setupGo.disabled = false;
      }
    });
  }

  // ===================================================================
  //  HA SETTINGS MODAL
  // ===================================================================
  const haSettingsEl = $("ha-settings");
  const haSettingsBtn = $("ha-settings-btn");
  const haSettingsForm = $("ha-settings-form");
  const haUrlInput = $("ha-url");
  const haTokenInput = $("ha-token");
  const haSaveBtn = $("ha-save");
  const haCancelBtn = $("ha-cancel");
  const haMsg = $("ha-msg");
  const haStatusDot = $("ha-status-dot");
  const haStatusText = $("ha-status-text");

  function setHaStatus(configured, url) {
    if (configured) {
      (haStatusDot &&
        haStatusDot.classList.replace("disconnected", "connected")) ||
        (haStatusDot && haStatusDot.classList.add("connected"));
      if (haStatusText) haStatusText.textContent = "CONNECTED";
      haSettingsBtn && haSettingsBtn.classList.add("ha-live");
    } else {
      haStatusDot && haStatusDot.classList.remove("connected");
      haStatusDot && haStatusDot.classList.add("disconnected");
      if (haStatusText) haStatusText.textContent = "NOT CONNECTED";
      haSettingsBtn && haSettingsBtn.classList.remove("ha-live");
    }
    if (haUrlInput && url) haUrlInput.value = url;
  }

  function showHaSettings() {
    if (haSettingsEl) haSettingsEl.classList.remove("setup-hidden");
    if (haMsg) {
      haMsg.textContent = "";
      haMsg.className = "";
    }
    setTimeout(() => haUrlInput && haUrlInput.focus(), 150);
  }
  function hideHaSettings() {
    if (haSettingsEl) haSettingsEl.classList.add("setup-hidden");
    if (haTokenInput) haTokenInput.value = "";
  }

  if (haSettingsBtn) haSettingsBtn.addEventListener("click", showHaSettings);
  if (haCancelBtn) haCancelBtn.addEventListener("click", hideHaSettings);
  haSettingsEl &&
    haSettingsEl.addEventListener("click", (e) => {
      if (e.target === haSettingsEl) hideHaSettings();
    });

  if (haSettingsForm) {
    haSettingsForm.addEventListener("submit", async (e) => {
      e.preventDefault();
      const ha_url = (haUrlInput.value || "").trim();
      const ha_token = (haTokenInput.value || "").trim();
      if (ha_url && !ha_token && !haTokenInput.dataset.hasExisting) {
        haMsg.className = "err";
        haMsg.textContent = "Please provide a Long-Lived Access Token.";
        return;
      }
      haSaveBtn.disabled = true;
      haMsg.className = "";
      haMsg.textContent = "Verifying…";
      try {
        const res = await fetch("/api/save_ha", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ ha_url, ha_token }),
        });
        const data = await res.json();
        if (data.ok) {
          haMsg.className = "ok";
          haMsg.textContent = data.ha_configured
            ? "Connected. Home automation online."
            : "Home automation disconnected.";
          setHaStatus(data.ha_configured, ha_url);
          haTokenInput.dataset.hasExisting = data.ha_configured ? "1" : "";
          setTimeout(hideHaSettings, 1200);
        } else {
          haMsg.className = "err";
          haMsg.textContent = data.error || "Could not save settings.";
        }
      } catch {
        haMsg.className = "err";
        haMsg.textContent = "Could not reach the server.";
      } finally {
        haSaveBtn.disabled = false;
      }
    });
  }

  // ===================================================================
  //  MEETING RECORDING
  // ===================================================================
  const meetingBtn = $("meeting-btn");
  const meetingPanel = $("meeting-panel");
  const meetingLog = $("meeting-log");
  const meetingTimerEl = $("meeting-timer");
  const meetingEndBtn = $("meeting-end-btn");
  const meetingPanelClose = $("meeting-panel-close");
  const meetingNotesModal = $("meeting-notes-modal");
  const meetingNotesCard = $("meeting-notes-card");
  const meetingNotesContent = $("meeting-notes-content");
  const meetingNotesDate = $("meeting-notes-date");
  const meetingNotesCopy = $("meeting-notes-copy");
  const meetingNotesExport = $("meeting-notes-export");
  const meetingNotesTranscriptBtn = $("meeting-notes-transcript-btn");
  const meetingNotesClose = $("meeting-notes-close");
  const meetingNotesMsg = $("meeting-notes-msg");
  const meetingTranscriptWrap = $("meeting-transcript-wrap");
  const meetingTranscriptContent = $("meeting-transcript-content");
  const meetingStatusLine = $("meeting-status-line");

  let _meetingActive = false;
  let _meetingStreams = [];
  let _meetingAudioCtx = null;
  let _meetingTimerInterval = null;
  let _meetingStartTime = null;
  let _meetingLoopPromise = null;
  let _stopCurrentChunk = null;
  let _meetingLastNotes = "";
  let _meetingLastTranscript = "";
  let _meetingPanelMinimised = false;

  function _meetingLog(text, cls) {
    if (!meetingLog) return;
    const el = document.createElement("div");
    el.className = cls || "meeting-seg";
    el.textContent = text;
    meetingLog.appendChild(el);
    meetingLog.scrollTop = meetingLog.scrollHeight;
  }

  function _updateMeetingTimer() {
    if (!meetingTimerEl || !_meetingStartTime) return;
    const s = Math.floor((Date.now() - _meetingStartTime) / 1000);
    const mm = String(Math.floor(s / 60)).padStart(2, "0");
    const ss = String(s % 60).padStart(2, "0");
    meetingTimerEl.textContent = mm + ":" + ss;
  }

  function _setMeetingStatus(text) {
    if (meetingStatusLine) meetingStatusLine.textContent = text;
  }

  async function startMeeting() {
    if (_meetingActive) return;

    let micStream;
    try {
      micStream = await navigator.mediaDevices.getUserMedia({
        audio: true,
        video: false,
      });
    } catch (e) {
      if (window.__chat)
        window.__chat.addMsg(
          "Microphone access is required to record meetings, sir.",
          "in",
        );
      return;
    }

    // Request system audio via screen share; video required by most browsers
    // — we stop the video track immediately after
    let sysStream = null;
    try {
      sysStream = await navigator.mediaDevices.getDisplayMedia({
        video: { width: 1, height: 1 },
        audio: true,
      });
      sysStream.getVideoTracks().forEach((t) => t.stop());
      if (sysStream.getAudioTracks().length === 0) sysStream = null;
    } catch (e) {
      // User declined or browser doesn't support — mic-only fallback
      sysStream = null;
    }

    // Mix mic + system audio into a single stream
    const ctx = new (window.AudioContext || window.webkitAudioContext)();
    const dest = ctx.createMediaStreamDestination();
    ctx.createMediaStreamSource(micStream).connect(dest);
    if (sysStream) ctx.createMediaStreamSource(sysStream).connect(dest);

    _meetingActive = true;
    _meetingStreams = [micStream, sysStream].filter(Boolean);
    _meetingAudioCtx = ctx;
    _meetingStartTime = Date.now();
    _meetingTimerInterval = setInterval(_updateMeetingTimer, 1000);

    socket.emit("start_meeting");

    // Show panel
    if (meetingPanel) meetingPanel.classList.remove("meeting-hidden");
    if (meetingLog) meetingLog.innerHTML = "";
    const src = sysStream ? "mic + system audio" : "mic only";
    _setMeetingStatus("Recording (" + src + ")…");
    if (meetingBtn) meetingBtn.classList.add("meeting-live");

    const mime =
      ["audio/webm;codecs=opus", "audio/webm", "audio/ogg"].find((t) =>
        MediaRecorder.isTypeSupported(t),
      ) || "";

    _meetingLoopPromise = _runMeetingChunks(dest.stream, mime);
  }

  async function _runMeetingChunks(stream, mimeType) {
    const CHUNK_MS = 30000;
    while (_meetingActive) {
      await new Promise((resolve) => {
        const rec = new MediaRecorder(stream, mimeType ? { mimeType } : {});
        const chunks = [];
        rec.ondataavailable = (e) => {
          if (e.data.size > 0) chunks.push(e.data);
        };
        rec.onstop = async () => {
          if (chunks.length) {
            const blob = new Blob(chunks, { type: rec.mimeType });
            const buf = await blob.arrayBuffer();
            socket.emit("meeting_audio_chunk", buf);
          }
          resolve();
        };
        rec.start();
        _stopCurrentChunk = () => {
          if (rec.state === "recording") rec.stop();
        };
        setTimeout(_stopCurrentChunk, CHUNK_MS);
      });
    }
  }

  async function endMeeting() {
    if (!_meetingActive) return;
    _meetingActive = false;
    clearInterval(_meetingTimerInterval);

    // Stop the current recorder so the last chunk is sent
    if (_stopCurrentChunk) {
      _stopCurrentChunk();
      _stopCurrentChunk = null;
    }
    // Wait for the chunk loop to exit (last onstop resolves the loop)
    if (_meetingLoopPromise) await _meetingLoopPromise;

    socket.emit("end_meeting");

    // Update UI to "generating notes" state
    _setMeetingStatus("Generating notes…");
    if (meetingBtn) meetingBtn.classList.remove("meeting-live");
    if (meetingEndBtn) meetingEndBtn.disabled = true;

    // Cleanup audio
    _meetingStreams.forEach((s) => s.getTracks().forEach((t) => t.stop()));
    _meetingStreams = [];
    if (_meetingAudioCtx) {
      _meetingAudioCtx.close();
      _meetingAudioCtx = null;
    }
  }

  // Socket.IO meeting events
  socket.on("meeting_started", () => {
    _setMeetingStatus("Listening…");
  });

  socket.on("meeting_transcript_update", ({ segment }) => {
    if (meetingStatusLine) meetingStatusLine.style.display = "none";
    _meetingLog(segment);
  });

  socket.on("meeting_notes_ready", ({ notes, transcript }) => {
    _meetingLastNotes = notes || "";
    _meetingLastTranscript = transcript || "";

    // Hide the meeting panel
    if (meetingPanel) meetingPanel.classList.add("meeting-hidden");
    if (meetingEndBtn) meetingEndBtn.disabled = false;

    // Show notes modal
    if (meetingNotesDate) {
      meetingNotesDate.textContent = new Date().toLocaleString();
    }
    if (meetingNotesContent) {
      meetingNotesContent.textContent = notes;
    }
    if (meetingTranscriptContent) {
      meetingTranscriptContent.textContent = transcript;
    }
    if (meetingTranscriptWrap) meetingTranscriptWrap.style.display = "none";
    if (meetingNotesTranscriptBtn)
      meetingNotesTranscriptBtn.textContent = "SHOW TRANSCRIPT";
    if (meetingNotesMsg) {
      meetingNotesMsg.textContent = "";
      meetingNotesMsg.className = "";
    }
    if (meetingNotesModal) meetingNotesModal.classList.remove("setup-hidden");
  });

  socket.on("meeting_error", ({ error }) => {
    if (window.__chat) window.__chat.addMsg("Meeting: " + error, "in");
  });

  function showMessageToast(sender, text, reason) {
    const existing = document.getElementById("msg-alert-toast");
    if (existing) existing.remove();
    const toast = document.createElement("div");
    toast.id = "msg-alert-toast";
    toast.innerHTML =
      '<div class="msg-toast-label">MESSAGE</div>' +
      '<div class="msg-toast-sender">' +
      sender.replace(/</g, "&lt;") +
      "</div>" +
      '<div class="msg-toast-text">' +
      text.replace(/</g, "&lt;").slice(0, 120) +
      "</div>" +
      '<div class="msg-toast-reason">' +
      reason.replace(/</g, "&lt;") +
      "</div>";
    toast.addEventListener("click", () => toast.remove());
    document.body.appendChild(toast);
    setTimeout(() => toast && toast.remove(), 12000);
  }

  socket.on("message_alert", ({ sender, text, reason }) => {
    showMessageToast(sender, text, reason);
  });

  // ===================================================================
  //  DOORBELL ALERTS
  // ===================================================================
  const DOORBELL_LABELS = {
    doorbell_press: "DOORBELL",
    motion: "MOTION DETECTED",
    person: "PERSON DETECTED",
    package: "PACKAGE DELIVERED",
  };

  function showDoorbellToast(event_type, speak_text) {
    const existing = document.getElementById("doorbell-toast");
    if (existing) existing.remove();
    const toast = document.createElement("div");
    toast.id = "doorbell-toast";
    const label = DOORBELL_LABELS[event_type] || "SECURITY ALERT";
    toast.innerHTML =
      '<div class="doorbell-toast-label">' +
      label +
      "</div>" +
      '<div class="doorbell-toast-text">' +
      speak_text.replace(/</g, "&lt;") +
      "</div>";
    toast.addEventListener("click", () => toast.remove());
    document.body.appendChild(toast);
    setTimeout(() => toast && toast.remove(), 10000);
  }

  socket.on("doorbell_alert", ({ event_type, speak: speakText }) => {
    const msg = speakText || "Doorbell alert.";
    showDoorbellToast(event_type, msg);
    if (!_standby) speak(msg);
    const btn = $("doorbell-btn");
    if (btn) {
      btn.classList.add("doorbell-active");
      setTimeout(() => btn.classList.remove("doorbell-active"), 8000);
    }
  });

  // Meeting button wires
  if (meetingBtn) {
    meetingBtn.addEventListener("click", () => {
      if (_meetingActive) endMeeting();
      else startMeeting();
    });
  }
  if (meetingEndBtn) {
    meetingEndBtn.addEventListener("click", endMeeting);
  }
  if (meetingPanelClose) {
    meetingPanelClose.addEventListener("click", () => {
      // Minimise (hide log, keep header)
      _meetingPanelMinimised = !_meetingPanelMinimised;
      if (meetingLog)
        meetingLog.style.display = _meetingPanelMinimised ? "none" : "";
    });
  }

  // Notes modal buttons
  if (meetingNotesCopy) {
    meetingNotesCopy.addEventListener("click", () => {
      navigator.clipboard
        .writeText(_meetingLastNotes)
        .then(() => {
          if (meetingNotesMsg) {
            meetingNotesMsg.className = "ok";
            meetingNotesMsg.textContent = "Copied to clipboard.";
            setTimeout(() => {
              meetingNotesMsg.textContent = "";
            }, 2000);
          }
        })
        .catch(() => {
          if (meetingNotesMsg) {
            meetingNotesMsg.className = "err";
            meetingNotesMsg.textContent = "Clipboard unavailable.";
          }
        });
    });
  }
  if (meetingNotesExport) {
    meetingNotesExport.addEventListener("click", () => {
      const date = meetingNotesDate
        ? meetingNotesDate.textContent
        : new Date().toLocaleString();
      const slug = new Date().toISOString().slice(0, 16).replace("T", "-");
      const md =
        `# Meeting Notes\n**Date:** ${date}\n\n` +
        _meetingLastNotes +
        (_meetingLastTranscript
          ? `\n\n---\n## Transcript\n\n${_meetingLastTranscript}`
          : "");
      const a = document.createElement("a");
      a.href = URL.createObjectURL(new Blob([md], { type: "text/markdown" }));
      a.download = `meeting-${slug}.md`;
      a.click();
      URL.revokeObjectURL(a.href);
    });
  }
  if (meetingNotesTranscriptBtn) {
    meetingNotesTranscriptBtn.addEventListener("click", () => {
      if (!meetingTranscriptWrap) return;
      const show = meetingTranscriptWrap.style.display === "none";
      meetingTranscriptWrap.style.display = show ? "" : "none";
      meetingNotesTranscriptBtn.textContent = show
        ? "HIDE TRANSCRIPT"
        : "SHOW TRANSCRIPT";
    });
  }
  if (meetingNotesClose) {
    meetingNotesClose.addEventListener("click", () => {
      if (meetingNotesModal) meetingNotesModal.classList.add("setup-hidden");
    });
  }

  // ===================================================================
  //  PHONE MESSAGES SETTINGS
  // ===================================================================
  const msgSettingsPanel = $("msg-settings");
  const msgSettingsBtn = $("msg-settings-btn");
  const msgSettingsClose = $("msg-settings-close");
  const msgWebhookUrl = $("msg-webhook-url");
  const msgWebhookToken = $("msg-webhook-token");
  const msgCopyUrl = $("msg-copy-url");
  const msgCopyToken = $("msg-copy-token");
  const msgRegenToken = $("msg-regen-token");

  function openMsgSettings() {
    if (!msgSettingsPanel) return;
    msgSettingsPanel.classList.add("msg-settings-open");
    fetch("/api/messages/token")
      .then((r) => r.json())
      .then((d) => {
        if (msgWebhookUrl) msgWebhookUrl.value = d.url || "";
        if (msgWebhookToken) msgWebhookToken.value = d.token || "";
      })
      .catch(() => {});
  }

  if (msgSettingsBtn) msgSettingsBtn.addEventListener("click", openMsgSettings);
  if (msgSettingsClose)
    msgSettingsClose.addEventListener("click", () => {
      if (msgSettingsPanel)
        msgSettingsPanel.classList.remove("msg-settings-open");
    });
  if (msgSettingsPanel)
    msgSettingsPanel.addEventListener("click", (e) => {
      if (e.target === msgSettingsPanel)
        msgSettingsPanel.classList.remove("msg-settings-open");
    });

  if (msgCopyUrl)
    msgCopyUrl.addEventListener("click", () => {
      if (msgWebhookUrl)
        navigator.clipboard.writeText(msgWebhookUrl.value).catch(() => {});
    });
  if (msgCopyToken)
    msgCopyToken.addEventListener("click", () => {
      if (msgWebhookToken)
        navigator.clipboard.writeText(msgWebhookToken.value).catch(() => {});
    });
  if (msgRegenToken)
    msgRegenToken.addEventListener("click", () => {
      fetch("/api/messages/token/regenerate", { method: "POST" })
        .then((r) => r.json())
        .then((d) => {
          if (msgWebhookToken) msgWebhookToken.value = d.token || "";
        })
        .catch(() => {});
    });

  // ===================================================================
  //  DOORBELL SETTINGS PANEL
  // ===================================================================
  const doorbellSettingsEl = $("doorbell-settings");
  const doorbellBtn = $("doorbell-btn");
  const doorbellSettingsClose = $("doorbell-settings-close");
  const doorbellWebhookUrl = $("doorbell-webhook-url");
  const doorbellWebhookToken = $("doorbell-webhook-token");
  const doorbellCopyUrl = $("doorbell-copy-url");
  const doorbellCopyToken = $("doorbell-copy-token");

  function buildDoorbellYaml(eventType, webhookUrl, token) {
    const entityHints = {
      doorbell_press: "event.YOUR_DOORBELL   # e.g. event.front_door_doorbell",
      motion:
        "binary_sensor.YOUR_MOTION  # e.g. binary_sensor.front_door_motion",
      person:
        "binary_sensor.YOUR_PERSON  # e.g. binary_sensor.front_door_person",
      package:
        "binary_sensor.YOUR_PACKAGE # e.g. binary_sensor.front_door_package",
    };
    const triggerPlatform =
      eventType === "doorbell_press"
        ? "  - platform: state\n    entity_id: " + entityHints[eventType]
        : "  - platform: state\n    entity_id: " +
          entityHints[eventType] +
          '\n    to: "on"';
    return (
      "# Add to configuration.yaml:\n" +
      "rest_command:\n" +
      "  jarvis_doorbell_event:\n" +
      '    url: "' +
      webhookUrl +
      '"\n' +
      "    method: POST\n" +
      "    headers:\n" +
      '      Authorization: "Bearer ' +
      token +
      '"\n' +
      '    payload: \'{"event_type": "' +
      eventType +
      "\"}'\n" +
      '    content_type: "application/json"\n\n' +
      "# Automation:\n" +
      'alias: "Jarvis — ' +
      eventType.replace("_", " ").toUpperCase() +
      '"\n' +
      "trigger:\n" +
      triggerPlatform +
      "\n" +
      "action:\n" +
      "  - action: rest_command.jarvis_doorbell_event"
    );
  }

  function openDoorbellSettings() {
    if (!doorbellSettingsEl) return;
    doorbellSettingsEl.classList.add("doorbell-settings-open");
    fetch("/api/doorbell/token")
      .then((r) => r.json())
      .then((d) => {
        const url = d.url || "";
        const token = d.token || "";
        if (doorbellWebhookUrl) doorbellWebhookUrl.value = url;
        if (doorbellWebhookToken) doorbellWebhookToken.value = token;
        ["press", "motion", "person", "package"].forEach((type) => {
          const el = $("yaml-" + type);
          if (el)
            el.textContent = buildDoorbellYaml(
              type === "press" ? "doorbell_press" : type,
              url,
              token,
            );
        });
      })
      .catch(() => {});
  }

  function closeDoorbellSettings() {
    if (doorbellSettingsEl)
      doorbellSettingsEl.classList.remove("doorbell-settings-open");
  }

  if (doorbellBtn) doorbellBtn.addEventListener("click", openDoorbellSettings);
  if (doorbellSettingsClose)
    doorbellSettingsClose.addEventListener("click", closeDoorbellSettings);
  if (doorbellSettingsEl)
    doorbellSettingsEl.addEventListener("click", (e) => {
      if (e.target === doorbellSettingsEl) closeDoorbellSettings();
    });

  if (doorbellCopyUrl)
    doorbellCopyUrl.addEventListener("click", () => {
      if (doorbellWebhookUrl)
        navigator.clipboard.writeText(doorbellWebhookUrl.value).catch(() => {});
    });
  if (doorbellCopyToken)
    doorbellCopyToken.addEventListener("click", () => {
      if (doorbellWebhookToken)
        navigator.clipboard
          .writeText(doorbellWebhookToken.value)
          .catch(() => {});
    });

  document.querySelectorAll(".doorbell-tab").forEach((tab) => {
    tab.addEventListener("click", () => {
      document
        .querySelectorAll(".doorbell-tab")
        .forEach((t) => t.classList.remove("doorbell-tab-active"));
      document
        .querySelectorAll(".doorbell-tab-content")
        .forEach((c) => c.classList.add("doorbell-tab-hidden"));
      tab.classList.add("doorbell-tab-active");
      const target = document.getElementById(
        "doorbell-tab-" + tab.dataset.dtab,
      );
      if (target) target.classList.remove("doorbell-tab-hidden");
    });
  });

  document.querySelectorAll(".msg-tab").forEach((tab) => {
    tab.addEventListener("click", () => {
      document
        .querySelectorAll(".msg-tab")
        .forEach((t) => t.classList.remove("msg-tab-active"));
      document
        .querySelectorAll(".msg-tab-content")
        .forEach((c) => c.classList.add("msg-tab-hidden"));
      tab.classList.add("msg-tab-active");
      const target = document.getElementById("msg-tab-" + tab.dataset.tab);
      if (target) target.classList.remove("msg-tab-hidden");
    });
  });

  // On load, ask the backend whether we're already configured.
  fetch("/api/status")
    .then((r) => r.json())
    .then((d) => {
      _configured = !!d.configured;
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
      if (d.ha_configured && haTokenInput)
        haTokenInput.dataset.hasExisting = "1";
      if (_configured) hideSetup();
      else showSetup();
      applyMode();
      startRecognition();
    })
    .catch(() => {
      showSetup();
      applyMode();
    });
})();
