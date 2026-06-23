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
      if (!key) {
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
