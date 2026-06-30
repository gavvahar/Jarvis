# J.A.R.V.I.S. Roadmap

**Goal:** Replace Siri, Google Assistant, and Alexa as a fully self-hosted, privacy-first AI assistant — always listening, deeply integrated, and extensible across all devices and platforms.

## Build Order

| Priority | Phase                                        | Status      |
| -------- | -------------------------------------------- | ----------- |
| 1        | Phase 2 — Always-On Wake Word                | In Progress |
| 2        | Phase 7 — Multi-User & Household             | Complete    |
| 3        | Phase 1 — Foundation & Parity                | Complete    |
| 4        | Phase 4 — Smart Speaker & Local Hardware     | Planned     |
| 5        | Phase 5 — Deeper Smart Home                  | In Progress |
| 6        | Phase 6 — Proactive & Ambient Intelligence   | Planned     |
| 7        | Phase 8 — Developer & Extensibility Platform | In Progress |
| 8        | Phase 3 — Mobile PWA                         | Last        |

---

## Phase 1 — Foundation & Parity

Get Jarvis to feature parity with Siri/Google Assistant/Alexa on core everyday tasks.

- [x] **Timers & alarms** — set, list, cancel; spoken countdown alerts
- [x] **Reminders** — one-time and recurring; stored in DB, surfaced via voice
- [x] **Calendar integration** — read/create events (Google Calendar, Apple Calendar via CalDAV)
- [x] **Contacts lookup** — "call Mom", "text John" via Google/iCloud contacts
- [x] **Music & media control** — Spotify, Apple Music, YouTube Music (play, pause, skip, volume)
- [x] **Shopping & to-do lists** — add items, read back, mark done (local + Todoist/OmniFocus sync)
- [x] **Unit conversion & calculations** — handled natively by the LLM, no tool needed
- [x] **News & weather briefings** — news via RSS (`get_news_headlines` tool); weather injected as live context

---

## Phase 2 — Always-On Wake Word ← Starting Here

Move from browser/spacebar activation to always-listening hardware-grade detection.

- [x] **Local wake word engine** — openWakeWord with custom "hey_jarvis" model; threshold tunable via env
- [ ] **Replace openwakeword with direct onnxruntime** — openwakeword pulls in tflite-runtime which blocks Python 3.14; call onnxruntime directly with the same HuggingFace ONNX models (onnxruntime + huggingface-hub already installed)
- [x] **Microphone daemon** — `wake_daemon.py` runs as a systemd service (`jarvis-wake.service`)
- [x] **Low-power standby mode** — noise gate skips inference on silence; CPU-only via systemd idle priority
- [ ] **Multi-room wake word** — simultaneous detection across devices, first responder wins
- [x] **False-positive suppression** — noise gate (RMS), confidence threshold, and cooldown all implemented

---

## Phase 3 — Mobile PWA (Last)

Replace native iOS/Android apps with a Progressive Web App to avoid app store copyright issues.

- [ ] **PWA manifest & service worker** — installable from browser, works offline
- [ ] **Mobile-optimized UI** — touch-friendly orb, fullscreen mode
- [ ] **Mobile microphone access** — wake via tap since background mic is OS-restricted
- [ ] **Push notifications** — alerts, reminders, doorbell events delivered to home screen
- [ ] **Offline mode** — cached UI + queue commands for when server is unreachable

---

## Phase 4 — Smart Speaker & Local Hardware

Deploy Jarvis on dedicated always-on hardware around the home.

- [ ] **Raspberry Pi image** — single-command flash; runs Whisper + wake word + full Jarvis stack
- [ ] **Speaker array support** — USB audio, ReSpeaker HAT, matrix voice
- [ ] **Multi-room audio** — Snapcast integration for synchronized playback across rooms
- [ ] **Room presence** — use Bluetooth/UWB beacons or Home Assistant presence to route responses to nearest device
- [ ] **LED ring feedback** — NeoPixel / WS2812 ring shows listening, thinking, speaking states
- [ ] **Offline-first mode** — full local stack: Whisper large + local LLM (Ollama) + no cloud required

---

## Phase 5 — Deeper Smart Home

Extend beyond Home Assistant to cover all major smart home ecosystems.

- [ ] **Matter/Thread support** — native Matter controller alongside Home Assistant
- [x] **Zigbee direct** — zigbee2mqtt integration via MQTT (`zigbee_control` tool)
- [ ] **Z-Wave** — Z-Wave JS integration
- [ ] **Apple HomeKit** — read/write HomeKit accessories via HAP-python
- [ ] **Lutron, Ecobee, Nest** — direct cloud integrations for lighting and climate
- [x] **Routine engine** — `manage_routine` tool; trigger phrases + multi-step execution stored in DB
- [x] **Proactive alerts** — `manage_device_alert` tool; condition-based rules with cooldown stored in DB

---

## Phase 6 — Proactive & Ambient Intelligence

Move from reactive (answer questions) to proactive (anticipate needs).

- [ ] **Daily briefing** — scheduled morning/evening summaries (weather, calendar, reminders, news)
- [ ] **Context awareness** — time of day, location, recent activity shape responses and suggestions
- [ ] **Habit learning** — detect patterns ("you usually leave at 8:30") and surface them
- [ ] **Email triage** — classify and summarize unread email; flag urgent items
- [ ] **Meeting prep** — pull agenda, attendees, and prior notes before calendar events
- [ ] **Package tracking** — parse shipping emails, announce deliveries
- [ ] **Travel alerts** — flight status, gate changes, delays via email parsing

---

## Phase 7 — Multi-User & Household ← Up Next After Phase 2

Scale from single-user to full household with voice recognition.

- [x] **Voice identification** — MFCC embeddings + cosine similarity; stored in `user_configs.voice_embedding`
- [x] **Per-user profiles** — per-user DB rows, conversation history, music tokens, reminders, timers
- [x] **Kid-safe mode** — `is_kid_safe` flag per user; age-appropriate system prompt injected automatically
- [x] **Guest mode** — unrecognized speakers identified as "guest" with limited feature access
- [x] **Shared lists** — `shared_lists` DB table; shopping + todo pre-created, accessible to all household members

---

## Phase 8 — Developer & Extensibility Platform

Make Jarvis a platform others can build on, like Alexa Skills or Google Actions.

- [ ] **Plugin system** — drop a Python file into `/plugins`; auto-discovered as AI tools
- [x] **Webhook triggers** — external services can push events Jarvis acts on
- [x] **REST API** — public API for sending commands and reading state (for automations)
- [ ] **MCP server** — expose Jarvis as a Model Context Protocol server for Claude Desktop etc.
- [ ] **IFTTT / Zapier / Make connectors** — no-code integration layer
- [ ] **CLI client** — `jarvis "turn off the lights"` from terminal

---

## Non-Goals (for now)

- Replacing a smartphone OS (we augment, not replace)
- Building proprietary hardware (use commodity Pi/mic hardware)
- Competing on cloud infrastructure (self-hosted is the value proposition)

---

## Guiding Principles

1. **Privacy first** — all processing stays local by default; cloud is opt-in
2. **No subscription** — runs on hardware you own with models you choose
3. **Extensible** — every integration is a tool the AI can call, not hardcoded logic
4. **Personality** — Jarvis is witty, brief, and deferential — not a corporate FAQ bot
5. **Open** — MIT licensed, community contributions welcome
