# J.A.R.V.I.S. Roadmap

**Goal:** Replace Siri, Google Assistant, and Alexa as a fully self-hosted, privacy-first AI assistant — always listening, deeply integrated, and extensible across all devices and platforms.

---

## Phase 1 — Foundation & Parity (Current → Baseline)

Get Jarvis to feature parity with Siri/Google Assistant/Alexa on core everyday tasks.

- [ ] **Timers & alarms** — set, list, cancel; spoken countdown alerts
- [ ] **Reminders** — one-time and recurring; stored in DB, surfaced via voice
- [ ] **Calendar integration** — read/create events (Google Calendar, Apple Calendar via CalDAV)
- [ ] **Contacts lookup** — "call Mom", "text John" via Google/iCloud contacts
- [ ] **Music & media control** — Spotify, Apple Music, YouTube Music (play, pause, skip, volume)
- [ ] **Shopping & to-do lists** — add items, read back, mark done (local + Todoist/OmniFocus sync)
- [ ] **Unit conversion & calculations** — handled natively by the LLM, no tool needed
- [ ] **News & weather briefings** — morning digest on demand or on schedule

---

## Phase 2 — Always-On Wake Word

Move from browser/spacebar activation to always-listening hardware-grade detection.

- [ ] **Local wake word engine** — integrate openWakeWord or Porcupine; custom "Hey Jarvis" model
- [ ] **Microphone daemon** — lightweight background process (Linux/Mac/Windows/Pi)
- [ ] **Low-power standby mode** — wake word runs on CPU only; full model activates on trigger
- [ ] **Multi-room wake word** — simultaneous detection across devices, first responder wins
- [ ] **False-positive suppression** — noise gating, confidence threshold tuning

---

## Phase 3 — Native Mobile Apps

Full assistant apps for iOS and Android, not just the SMS listener.

### Android

- [ ] **Background assistant service** — always listening, foreground notification
- [ ] **Voice overlay** — floating orb activatable from any app (like Assistant)
- [ ] **On-device wake word** — openWakeWord running locally on device
- [ ] **Notification reading** — read aloud incoming notifications on request or automatically
- [ ] **Deep OS integration** — open apps, control volume/brightness, set alarms via Android APIs
- [ ] **Offline fallback** — local Whisper + small LLM when no server reachable

### iOS

- [ ] **Siri Shortcut integration** — trigger Jarvis via Siri ("Hey Siri, ask Jarvis…")
- [ ] **Live Activities widget** — show Jarvis listening state on Dynamic Island / Lock Screen
- [ ] **Background audio session** — compliant always-on listening via iOS Audio Session APIs
- [ ] **CallKit integration** — intercept and handle "call contact" requests
- [ ] **App Intents** — expose Jarvis actions to Shortcuts, Focus filters, and Spotlight

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
- [ ] **Zigbee direct** — zigbee2mqtt integration for direct device control without HA
- [ ] **Z-Wave** — Z-Wave JS integration
- [ ] **Apple HomeKit** — read/write HomeKit accessories via HAP-python
- [ ] **Lutron, Ecobee, Nest** — direct cloud integrations for lighting and climate
- [ ] **Routine engine** — "good morning" / "leaving home" / "goodnight" multi-step automations
- [ ] **Proactive alerts** — Jarvis speaks up: "Your laundry has been in the washer for 45 minutes"

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

- [ ] **Voice identification** — speaker diarization to distinguish household members
- [ ] **Per-user profiles** — each voice gets their own calendar, reminders, music preferences
- [ ] **Kid-safe mode** — content filtering and parental controls per voice profile
- [ ] **Guest mode** — limited access for visitors without login
- [ ] **Shared lists** — shopping and to-do lists shared across household members

---

## Phase 8 — Developer & Extensibility Platform

Make Jarvis a platform others can build on, like Alexa Skills or Google Actions.

- [ ] **Plugin system** — drop a Python file into `/plugins`; auto-discovered as AI tools
- [ ] **Webhook triggers** — external services can push events Jarvis acts on
- [ ] **REST API** — public API for sending commands and reading state (for automations)
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
