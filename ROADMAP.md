# J.A.R.V.I.S. Roadmap

**Goal:** Replace Siri, Google Assistant, and Alexa as a fully self-hosted, privacy-first AI assistant — always listening, deeply integrated, and extensible across all devices and platforms.

## Build Order

| Priority | Phase                                          | Status      |
| -------- | ---------------------------------------------- | ----------- |
| 1        | Phase 2 — Always-On Wake Word                  | Complete    |
| 2        | Phase 7 — Multi-User & Household               | Complete    |
| 3        | Phase 1 — Foundation & Parity                  | Complete    |
| 4        | GitHub Actions & CI/CD                         | Complete    |
| 5        | app.py Modularisation                          | Complete    |
| 6        | Phase 4 — Smart Speaker & Local Hardware       | Complete    |
| 7        | Phase 5 — Deeper Smart Home                    | In Progress |
| 8        | Phase 6 — Proactive & Ambient Intelligence     | Planned     |
| 9        | Phase 8 — Developer & Extensibility Platform   | In Progress |
| 10       | Phase 9 — Financial Intelligence               | In Progress |
| 11       | Phase 10 — Computer Vision & Spatial Awareness | Complete    |
| 12       | Phase 11 — Accessibility & Hearing Assistance  | Planned     |
| 13       | Phase 12 — Mental Wellness & Social Assistance | Planned     |
| 14       | Phase 3 — Mobile PWA                           | Last        |

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

## Phase 2 — Always-On Wake Word

Move from browser/spacebar activation to always-listening hardware-grade detection.

- [x] **Local wake word engine** — openWakeWord with custom "hey_jarvis" model; threshold tunable via env
- [x] **Replace openwakeword with direct onnxruntime** — openwakeword pulls in tflite-runtime which blocks Python 3.14; call onnxruntime directly with the same HuggingFace ONNX models (onnxruntime + huggingface-hub already installed)
- [x] **Microphone daemon** — `wake_daemon.py` runs as a systemd service (`jarvis-wake.service`)
- [x] **Low-power standby mode** — noise gate skips inference on silence; CPU-only via systemd idle priority
- [x] **Multi-room wake word** — simultaneous detection across devices, first responder wins
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

- [x] **Raspberry Pi image** — `make pi-setup` (or `sudo bash scripts/setup-pi.sh`) installs daemon + systemd service on any Pi; runs Whisper + wake word
- [x] **Speaker array support** — `AUDIO_DEVICE` env var in `wake_daemon.py` selects mic by name or index; `python3 -c "import sounddevice; print(sounddevice.query_devices())"` to list devices
- [x] **Multi-room audio** — Snapcast JSON-RPC integration (`integrations/phase4/snapcast.py`); LLM can control per-room volume, mute, and stream routing; add Snapcast via `compose.yml` comment block
- [x] **Room presence** — `ROOM` env var in daemon sends room with each wake event; `integrations/phase4/presence.py` tracks device→room and routes replies to the right socket session; room injected into LLM system prompt
- [x] **LED ring feedback** — NeoPixel/WS2812 LED ring driver in `wake_daemon.py`; set `LED_TYPE=neopixel`, `LED_PIN`, `LED_COUNT`, `LED_BRIGHTNESS`; flashes blue on wake detection
- [x] **Offline-first mode** — Ollama service added to `compose.yml` as `--profile offline`; `docker compose --profile offline up -d` starts Ollama alongside Jarvis; set provider=openai_compatible, base_url=http://ollama:11434/v1

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

## Phase 7 — Multi-User & Household

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

## Phase 12 — Mental Wellness & Social Assistance

Reduce social friction for introverts and provide grounding, calm, and pattern awareness for anxiety.

- [ ] **Calm mode** — on request or detected distress, switch to a slower, quieter, softer Jarvis voice and suppress non-essential notifications
- [ ] **Breathing & grounding exercises** — guided box breathing, 4-7-8, and 5-4-3-2-1 sensory grounding by voice; hands-free, no screen required
- [ ] **Worry dump** — "Jarvis, I need to vent" opens a low-pressure voice journal; Jarvis acknowledges without judgment and stores the entry privately
- [ ] **Overthinking interrupt** — detect rumination loops in conversation and gently offer a reframe, a distraction, or a grounding exercise
- [ ] **Mood check-ins** — optional daily voice check-in ("how are you feeling?"); track mood over time and surface patterns (time of day, day of week, recent events)
- [ ] **Anxiety pattern detection** — identify recurring triggers from journal and mood data; surface insights privately ("you tend to feel anxious on Sunday evenings")
- [ ] **Call screening & voicemail** — intercept unknown calls, transcribe voicemails to text, and suggest a text reply instead of calling back
- [ ] **Social reply drafting** — "help me respond to this" — draft replies to messages, emails, or invitations in your voice so you don't have to start from scratch
- [ ] **Social energy tracker** — log social commitments; warn when the week is overloaded and suggest blocking recovery time
- [ ] **Polite decline generator** — given an event or request, draft a kind, non-awkward way to say no
- [ ] **Therapist mode** — dedicated conversational mode that uses active listening, reflective questioning, and CBT-influenced techniques; Jarvis listens without rushing to fix, tracks session history for continuity, and escalates to real emergency resources if crisis language is detected

---

## Phase 11 — Accessibility & Hearing Assistance

Compensate for single-sided hearing loss with visual alerts, real-time captions, and a more forgiving voice UX.

- [ ] **Sound event detection** — continuously monitor mic for non-speech sounds (doorbell, smoke alarm, phone ring, knocking, baby cry); flash smart lights and push a phone notification so nothing is missed
- [ ] **Visual TTS output** — display Jarvis's spoken response as text on screen simultaneously; never lose a reply because it was too quiet or came from the wrong direction
- [ ] **Wake word visual confirmation** — flash a light or show an on-screen indicator when the wake word fires, so it's clear Jarvis heard you
- [ ] **Conversation transcription** — on demand, use Whisper (already installed) to caption live in-person conversation and display it on screen or phone
- [ ] **Media & TV captions** — capture room audio via mic and display rolling captions for TV or media playing nearby; no HDMI tap required
- [ ] **Call transcription** — transcribe phone and video calls in real time; surface as scrollable text alongside the conversation
- [ ] **Adjustable TTS clarity** — per-user controls for Jarvis voice speed, volume, and EQ; default to slower and louder for the hearing-impaired profile

---

## Phase 10 — Computer Vision & Spatial Awareness

Give Jarvis eyes — know who is home, where they are, what they're doing, and flag anything unusual.

- [x] **Camera ingestion** — pull RTSP/ONVIF streams from IP cameras and USB webcams; integrate with Home Assistant camera entities
- [x] **Room presence detection** — identify which room each person is in; feed into response routing so audio plays from the nearest device (extends Phase 4 room presence)
- [x] **Person identification** — recognize household members by face; tie detections to existing user profiles for personalized responses without voice input
- [x] **Activity recognition** — classify what someone is doing (cooking, sleeping, exercising, watching TV) and use it to shape Jarvis behavior (e.g. don't interrupt during sleep)
- [x] **Security alerts** — detect unfamiliar faces, motion during night/away mode, or unexpected presence; push notification + optional camera snapshot
- [x] **Away mode** — automatically detect when the house is empty and arm alerts; disarm when a known face returns
- [x] **Privacy controls** — per-camera opt-in, all inference runs locally (no video leaves the network), configurable retention window

---

## Phase 9 — Financial Intelligence

Give Jarvis full visibility and control over money — balances, spending, budgets, goals, and payments.

- [x] **Account aggregation** — Plaid Link (sandbox); unified account view across linked banks in `plaid_items`/`plaid_accounts`
- [x] **Balance & transaction lookup** — "what's my balance?", "show my recent transactions" answered by voice via `get_account_balances`/`get_recent_transactions`
- [x] **Spending categorization** — reuses Plaid's `personal_finance_category`; override via voice (`set_transaction_category`) or `PATCH /api/finance/transactions/{id}`
- [ ] **Budget tracking** — set monthly budgets by category; alert when approaching or over limit
- [ ] **Bill & subscription detection** — surface recurring charges automatically; alert before due dates
- [ ] **Savings goals** — "save $5k for vacation by December"; track progress and surface weekly
- [ ] **Net worth dashboard** — aggregate all accounts (checking, savings, credit, investments) into a single number
- [ ] **Spending alerts** — flag large, unusual, or out-of-category transactions in real time via webhook
- [ ] **Transfer & payment initiation** — initiate bank transfers via Plaid Transfer API or direct bank APIs; confirm by voice before executing
- [ ] **Financial briefing** — daily/weekly money summary: net cash flow, top spending categories, upcoming bills, goal progress

---

## GitHub Actions & CI/CD

Automated workflows to keep the repo healthy and branches in sync.

- [x] **Auto-merge staging → main** — nightly cron merges staging into main if clean
- [x] **Cascade merge on push** — when `staging` or `main` receives a push, automatically attempt to merge it into every other open branch; on conflict, open a detailed issue describing the conflicting files and assign it to whoever made the last commit on that branch

---

## app.py Modularisation

Split the monolithic `app.py` (~5,900 lines) into focused modules so each integration and layer can be found, edited, and tested in isolation.

- [x] **`config.py`** — all ENV vars and constants; no local imports
- [x] **`db.py`** — DB pool, `_pool()`, schema loading, and all `_db_*` helper functions
- [x] **`auth.py`** — OIDC discovery, session signing/verification, `_get_current_user`, `_require_admin`
- [x] **`integrations/ha.py`** — Home Assistant tool schemas, `_ha_call_service`, `_ha_get_states`, `_execute_ha_tool`
- [x] **`integrations/myq.py`** — MyQ/Chamberlain tool schemas and execution
- [x] **`integrations/tesla.py`** — Tesla tool schemas, token management, and execution
- [x] **`integrations/music/spotify.py`** — Spotify tool schemas, OAuth helpers, and execution
- [x] **`integrations/music/apple_music.py`** — Apple Music tool schemas and execution
- [x] **`integrations/vision.py`** — face recognition, camera snapshots, `_vision_loop`, vision tool schemas
- [x] **`integrations/phase1.py`** — timers, reminders, news, calendar, contacts tool schemas and execution
- [x] **`integrations/phase5.py`** — routines, device alerts, Zigbee tool schemas and execution
- [x] **`integrations/shared_lists.py`** — shared list tool schemas and execution
- [x] **`llm.py`** — LLM client builders, `_stream_reply`, `_build_system_prompt`
- [x] **`app.py`** — FastAPI app, lifespan, Socket.IO handlers, and HTTP routes only (glue layer)

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
