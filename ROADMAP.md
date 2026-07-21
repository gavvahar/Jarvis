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
| 7        | Phase 5 — Deeper Smart Home                    | Complete    |
| 8        | Phase 6 — Proactive & Ambient Intelligence     | In Progress |
| 9        | Phase 8 — Developer & Extensibility Platform   | On Hold     |
| 10       | Phase 9 — Financial Intelligence               | In Progress |
| 11       | Phase 10 — Computer Vision & Spatial Awareness | Complete    |
| 12       | Phase 11 — Accessibility & Hearing Assistance  | Planned     |
| 13       | Phase 12 — Mental Wellness & Social Assistance | Planned     |
| 14       | Phase 3 — Mobile PWA                           | In Progress |

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

Replace native iOS/Android apps with a Progressive Web App to avoid app store copyright issues. (Note: the notification-listener app in `android/` is a separate, narrow SMS-forwarding tool for the phone-message-triage feature — not the mobile client this phase builds.)

- [x] **Responsive layout pass 1** — phone/tablet breakpoints in `responsive.css`; device-tailored orb particle count + FPS cap
- [x] **PWA manifest & service worker** — `static/manifest.json` + `static/sw.js`, served at `/manifest.json` and `/sw.js` (root scope, not `/static/`) via dedicated FastAPI routes; registered from `pwa.js`. Cache-first for `/static/v2/` assets. Installability still requires HTTPS in production (service workers won't register over plain HTTP except on localhost)
- [ ] **Mobile UI pass 2** — audit remaining panels (settings tabs, telemetry panels, party mode, meeting panel) for touch targets and viewport overflow; add a mobile-viewport project to `playwright.config.js` so regressions are caught in CI
- [ ] **Tap-to-talk mic flow** — background mic access is OS-restricted, so mobile uses an explicit mic button (`MediaRecorder`) instead of the always-on wake daemon; iOS Safari and Android Chrome differ on audio formats/permission prompts and need separate testing
- [ ] **Push notifications** — Web Push (VAPID) + service worker push handler, wired into existing alert sources (doorbell, reminders/timers, device alerts) via a `pywebpush` layer server-side. The shared infrastructure (`push_subscriptions` table, `python/integrations/push.py`'s `_send_push`, VAPID env vars, `sw.js` `push`/`notificationclick` listeners) already shipped as part of Phase 10's Vigil Mode and is only wired into `security_alert` today — remaining work is just adding the other 5 emit sites (`message_alert`, `doorbell_alert`, `timer_fired`, `reminder_fired`, `device_alert`) to the same fan-out
- [ ] **Offline mode** — service worker caches the app shell; IndexedDB queue holds outbound commands issued while offline and replays them on reconnect, with a visible "reconnecting" state

**Planned build order** (safest first, each item its own commit):

1. **UI pass 2** — pure CSS (`responsive.css`) + Playwright config; no runtime risk, doesn't touch `sw.js`/backend. Adds `.settings-tab-btn`/`.msg-tab`/`.doorbell-tab` touch-target sizing, collapses the absolutely-positioned telemetry `.panel` parallax elements to a static stacked/hidden layout below 768px/480px, audits `party.html`'s own CSS. Playwright gets a `projects` array (`devices["iPhone 13"]`, `devices["Pixel 5"]`, plus the existing desktop project) so every current spec re-runs on mobile viewports for free, plus a new `tests/browser/mobile.spec.js` for touch-target and overflow assertions.
2. **Tap-to-talk** — client-only JS, no protocol changes; reuses the existing `/api/transcribe` endpoint `core.js` already calls. New `#ptt-btn` in the topbar (shown via CSS media query, same convention as the rest of `responsive.css`), press-and-hold via `pointerdown`/`pointerup`, coexists with (doesn't replace) the always-on `_vadLoop`. Mime-negotiation logic (currently duplicated between `core.js` and `meeting.js`) gets extracted into a shared `static/v2/js/app/media_utils.js` helper; add `audio/mp4` to the candidate list for iOS Safari.
3. **Push notifications** — additive only. The `push_subscriptions` table, `python/integrations/push.py` module (`_send_push`, no class, fans out via `pywebpush.webpush()` off the event loop via `asyncio.to_thread`), VAPID env vars (`VAPID_PUBLIC_KEY`/`VAPID_PRIVATE_KEY`/`VAPID_SUBJECT`, exposed to the frontend via `/api/status`), and the `push`/`notificationclick` listeners in `sw.js` already exist (built for Vigil Mode, currently wired only into `security_alert`). Remaining work: add the other 5 existing alert emit-sites (`message_alert`, `doorbell_alert`, `timer_fired`, `reminder_fired`, `device_alert`) to the same `_send_push` fan-out — push sent unconditionally alongside each socket emit (not gated on "no live sid") since mobile OSes can silently suspend a socket long before the server sees it drop.
4. **Offline mode** — last and riskiest, since it's the only item that changes the existing cache-first `fetch` logic in `sw.js`. Along the way, fixes a latent bug: `caches.match(event.request, { ignoreSearch: true })` ignores the `?v=` cache-busting query string, so once an asset is cached by pathname, bumping `?v=2` → `?v=3` doesn't actually invalidate it — switching to `CACHE_NAME`-based versioning (bumped alongside `?v=N`) fixes this and is required before app-shell pre-caching can be trusted. Offline queue scope is deliberately narrow: only typed/spoken-then-transcribed chat text sent via the `user_message` socket event gets queued in IndexedDB (mic capture itself needs a live Whisper round-trip and can't be queued). Dedup via a client-generated `client_msg_id` + a socket ack from `on_user_message`, with a small in-memory server-side dict as a second line of defense against double-sends on flaky reconnects.

---

## Phase 4 — Smart Speaker & Local Hardware

Deploy Jarvis on dedicated always-on hardware around the home.

- [x] **Raspberry Pi image** — `make pi-setup` (or `sudo bash scripts/setup-pi.sh`) installs daemon + systemd service on any Pi; runs Whisper + wake word
- [x] **Speaker array support** — `AUDIO_DEVICE` env var in `wake_daemon.py` selects mic by name or index; `python3 -c "import sounddevice; print(sounddevice.query_devices())"` to list devices
- [x] **Multi-room audio** — Snapcast JSON-RPC integration (`integrations/phase4/snapcast.py`); LLM can control per-room volume, mute, and stream routing; add Snapcast via `compose.yml` comment block
- [x] **Room presence** — `ROOM` env var in daemon sends room with each wake event; `integrations/phase4/presence.py` tracks device→room and routes replies to the right socket session; room injected into LLM system prompt
- [x] **LED ring feedback** — NeoPixel/WS2812 LED ring driver in `wake_daemon.py`; set `LED_TYPE=neopixel`, `LED_PIN`, `LED_COUNT`, `LED_BRIGHTNESS`; flashes blue on wake detection
- [x] **Offline-first mode** — Ollama service added to `compose.yml` as `--profile offline`; `docker compose --profile offline up -d` starts Ollama alongside Jarvis; set provider=openai_compatible, base_url=`http://ollama:11434/v1`

---

## Phase 5 — Deeper Smart Home

Extend beyond Home Assistant to cover all major smart home ecosystems.

- [x] **Matter/Thread support** — not built directly; Home Assistant has a native Matter integration, so Matter devices added to HA are already controllable via `call_ha_service`/`get_ha_states`
- [x] **Zigbee direct** — zigbee2mqtt integration via MQTT (`zigbee_control` tool), for devices deliberately kept outside Home Assistant
- [x] **Z-Wave** — not built directly; Home Assistant has a native Z-Wave JS integration
- [x] **Apple HomeKit** — not built directly; Home Assistant has a native HomeKit Controller integration
- [x] **Lutron, Ecobee, Nest** — not built directly; Home Assistant has native integrations for all three
- [x] **Routine engine** — `manage_routine` tool; trigger phrases + multi-step execution stored in DB
- [x] **Proactive alerts** — `manage_device_alert` tool; condition-based rules with cooldown stored in DB

---

## Phase 6 — Proactive & Ambient Intelligence

Move from reactive (answer questions) to proactive (anticipate needs).

- [x] **Daily briefing** — scheduled morning/evening summaries (weather, calendar, reminders, news). Opt-in per user (`briefing_enabled`, off by default) with configurable `briefing_morning_time`/`briefing_evening_time` (24h `HH:MM`, server-local time — same convention `_vision_loop`'s night detection and calendar event display already use) via a **DAILY BRIEFING** section in the PIM settings panel or voice (`manage_briefing`: `enable`/`disable`/`set_time`/`status`/`now`). `python/integrations/briefing.py`'s `_briefing_loop()` polls every 60s, composes weather (`_location_context`, already populated by the existing weather loop) + today's remaining calendar events (reuses `_calendar_events_between`) + today's reminders (`_db_list_reminders`) + top 3 general headlines (`_fetch_news_headlines`, factored out of the existing `get_news_headlines` tool), and delivers via the same `speak`-field socket pattern as `timer_fired`/`reminder_fired` (new `briefing_ready` event) plus a push notification through the existing `_send_push` fan-out.
- [ ] **Context awareness** — time of day, location, recent activity shape responses and suggestions
- [x] **Habit learning** — detect patterns ("you usually leave at 8:30") and surface them. Built on top of existing camera presence detection (requires Vigil Mode cameras + face enrollment; no data without them): `python/integrations/vision.py`'s `_vision_loop` now records `arrived`/`departed` transitions to a new `presence_events` table (departure timestamp backdated to `last_seen_at`, not "now", since the away-timeout detection lags the actual departure by up to `VISION_AWAY_TIMEOUT`). `python/integrations/habits.py`'s `_analyze_habit()` buckets each user's last 60 days of events into weekday/weekend, takes the median time-of-day per bucket (min 3 samples required, else "not enough data yet"), and surfaces it via the `get_habits` voice tool, a **HABIT LEARNING** summary in the VISION settings panel, and an opt-in (`habit_nudges_enabled`, off by default) proactive nudge — `_habit_nudge_loop()` polls every 5 min and speaks/pushes a heads-up (`habit_nudge` socket event, same pattern as `timer_fired`) once per day when the current time enters a 10-minute window around the user's usual "departed" time and they haven't left yet today. Scoped to leave/arrive-home patterns only (the roadmap's example); other behavioral signals (routine usage, timer patterns, etc.) are a natural extension but out of scope for this pass.
- [x] **Email triage** — classify and summarize unread email; flag urgent items. See steps 1-3 of the build order below.
- [ ] **Meeting prep** — pull agenda, attendees, and prior notes before calendar events
- [x] **Package tracking** — parse shipping emails, announce deliveries. See step 4 of the build order below.

**Email triage / package tracking build order** (each its own commit; package tracking and the later triage steps all sit on top of step 1, so nothing downstream can start until it lands):

1. [x] **Email connection layer (foundational)** — a generic **IMAP** connection (not a Gmail-specific OAuth app), matching the existing CalDAV/CardDAV pattern (`python/integrations/pim/dav.py` + the PIM settings panel): works with Gmail, iCloud, Fastmail, etc. via an app-specific password, no per-provider OAuth app to register. `python/integrations/pim/mail.py`'s `_imap_fetch_unread()` (stdlib `imaplib`, read-only `SELECT INBOX` + `SEARCH UNSEEN`, one `BODY.PEEK[HEADER.FIELDS (FROM SUBJECT DATE)]` fetch per message so nothing is marked read) runs off the event loop via `asyncio.to_thread` since `imaplib` is blocking. New `email_host`/`email_username`/`email_password` columns on `user_configs`, an **EMAIL** section in the PIM settings panel (`POST /api/save_email`, which does a live `_test_email_connection()` login + returns the current unread count before saving, mirroring how `/api/save_pim` calls `_resolve_dav_collection` before persisting calendar/contacts creds), and a bare `list_unread_email` voice tool that just lists sender/subject/date — no classification yet. This step alone proves connectivity end-to-end before any LLM logic is layered on.
2. [x] **Email triage — classify & summarize** — `python/integrations/email_triage.py`'s `_email_triage_loop()` (same shape as `_briefing_loop()`, polls every 5 minutes) fetches unread mail via step 1's `_imap_fetch_unread()`, skips UIDs already in the new `email_triage` table (`_db_uids_already_classified`, keyed on `(user_id, uid)`), and runs each new message through a one-shot, non-streaming LLM call (built directly via `llm_client.build_llm_client()` rather than importing `llm.py`, to avoid a circular import) asking for strict JSON — `{"summary": ..., "important": ...}` — with a hardcoded fallback (subject line, `important=False`) if the model call or JSON parse fails. Surfaced via a `get_email_summary` voice tool (gated on email being connected, same as `list_unread_email`) and an **EMAIL TRIAGE** list in the PIM settings panel (sender, one-line summary, an `URGENT` badge when flagged). Opt-in per user (`email_triage_enabled`, off by default, `GET`/`POST /api/email-triage`), same convention as `briefing_enabled`/`habit_nudges_enabled`. No proactive alert yet — that's step 3.
3. [x] **Email triage — urgent alerts** — layered on step 2: `_triage_new_messages()` calls `_alert_urgent_email()` whenever a newly-classified message comes back `important=True`, which fires an `email_alert` socket event (`from`/`subject`/`summary`/`speak`) to every active session for that user plus a push notification through the existing `_send_push` fan-out — same delivery pattern `travel_alert`/`habit_nudge` already use. Client-side, `email_alert` is handled in `doorbell.js` identically to those (speaks the message, echoes it into the chat log). No new infra beyond `email_triage.py` gaining an `init(sio, sids_fn)` (it didn't need one for step 2, since nothing was pushed out yet).
4. [x] **Package tracking** — also layered on step 1's IMAP connection, independent of steps 2-3: `python/integrations/package_tracking.py`'s `_package_tracking_loop()` (same 5-minute cadence) fetches unread mail, keeps only messages from a known carrier sender domain (UPS/FedEx/USPS/Amazon — sender-domain match only, not subject keywords, to avoid false positives from ordinary promotional mail), fetches the message body for those candidates only (`_imap_fetch_body()`, new in `mail.py`, walks a multipart message for the `text/plain` part, falling back to tag-stripped `text/html`), and regexes the subject+body for a status (`delivered`/`out_for_delivery`/`shipped`, defaulting to `update`) and a best-effort tracking number. Every match is cached in a new `package_events` table (dedup on `(user_id, uid)`, same pattern as `email_triage`) and surfaced via a `get_package_updates` voice tool and a **PACKAGE TRACKING** list in the settings panel; only `delivered`/`out_for_delivery` fire a `package_alert` socket event + push, through the same fan-out as every other proactive alert in this phase. Opt-in (`package_tracking_enabled`, off by default). Fixed a latent bug while adding `_imap_fetch_body()`: step 1/2's IMAP calls used plain sequence numbers (`conn.search`/`conn.fetch`) instead of true IMAP UIDs (`conn.uid("search", ...)`/`conn.uid("fetch", ...)`) as the dedup key — sequence numbers shift whenever another message is expunged from the mailbox, so they weren't actually stable across polls; both `mail.py` helpers now use UID commands. Carrier-specific tracking-status APIs (vs. just parsing the email text) remain a possible follow-up, not required for v1.

- [x] **Travel alerts** — flight status, gate changes, and cancellations for flights tracked by airline code + flight number (manual/voice entry, not email parsing — the roadmap's original "via email parsing" plan would have depended on the still-unbuilt "Email triage" item above, so this scopes to the flight-status half only). `python/integrations/travel.py`'s `manage_travel_alert` voice tool (and a **TRAVEL ALERTS** section in the PIM settings panel) lets a user track a flight (`travel_trips` table); `_travel_alert_loop()` polls the [AeroDataBox](https://rapidapi.com/aedbx-aedbx/api/aerodatabox) API (`AERODATABOX_KEY`) every `TRAVEL_POLL_INTERVAL` (default 15m, only for trips departing within a day to stay inside AeroDataBox's free-tier budget) and delivers a `travel_alert` socket event + push notification through the existing `_send_push` fan-out whenever status, gate, or terminal changes; the trip auto-stops tracking once a flight lands, is cancelled, or diverts.

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
- [x] **Vigil Mode** — explicit AUTO/ARMED/DISARMED control (`vigil_state` table + `GET`/`POST /api/vigil-mode` + `set_vigil_mode`/`get_vigil_mode` voice tools in `python/integrations/vigil.py`) layered on top of the existing presence-based away/night detection in `_vision_loop` (`python/integrations/vision.py`); motion-only detection via OpenCV frame differencing (`VISION_MOTION_THRESHOLD`) so a camera with no matched face still fires an event; snapshots now populate the `security_events.snapshot` column at trigger time and are servable via `GET /api/security-events/{id}/snapshot`; a minimal Web Push layer (`python/integrations/push.py`, `push_subscriptions` table, `VAPID_*` env vars, `sw.js` `push`/`notificationclick` listeners) delivers `motion`/`unknown_person` alerts even when the app isn't open. Arm/disarm/status also controllable by voice ("arm Vigil Mode") and from the new VIGIL MODE section of the VISION settings panel, which also shows a snapshot-thumbnail event log. Video clips (vs. still snapshots) remain out of scope — would need a continuous RTSP recording buffer, a much larger effort. The push layer here is deliberately scoped to security alerts only; wiring the other 5 alert types (doorbell, timer, reminder, device, message) into the same `_send_push` fan-out is still tracked under Phase 4's "Push notifications" item.
- [x] **Privacy controls** — per-camera opt-in, all inference runs locally (no video leaves the network), configurable retention window
- [x] **Device-camera lock (Vigil Mode add-on)** — while Vigil Mode is ARMED, use the _device's own webcam_ (the laptop/desktop JARVIS is open on, via client-side `getUserMedia` — not a network camera) to detect who's in front of the screen; if a face is matched that isn't the logged-in user's _and_ the logged-in user's own face isn't visible in the same frame for 3 consecutive checks (~15s), lock the JARVIS session in that browser tab. Scoped as an in-app lock only — no browser exposes a real OS-level lock-screen API to a webpage, so this can't trigger an actual Windows/Linux/macOS lock; it blanks/hides the JARVIS UI (`templates/partials/vigil_lock_overlay.html` + `static/v2/css/vigil_lock.css`) and auto-unlocks the moment the logged-in user's face reappears alone, with an "unlock with account login" fallback that re-runs the OIDC session check (this app has no local password store, so that's the equivalent escape hatch) so a bad camera angle can never permanently lock someone out. Being pure client-side web APIs (no OS-specific code), it works identically cross-platform (Linux, Windows, macOS, mobile browsers) — though it's most useful on laptops/desktops where the tab stays open unattended, since phones already have their own lock screen. Built as five layers in one module (`static/v2/js/app/vigil_lock.js`):
  - [x] **Presence-check endpoint** — `POST /api/face/check-presence` in `python/app.py` → `_check_presence()` in `python/integrations/vision.py`, a thin wrapper around the already-existing `_identify_faces_in_image` and the household `_face_cache` populated by `_refresh_face_cache` — accepts an uploaded frame, returns every matched face in it (`[{detected_user_id, name, confidence}]`), same shape `_vision_loop` already produces per camera. No new face-matching logic needed, just exposes the existing per-frame multi-face match as a route.
  - [x] **Client capture loop** — requests `getUserMedia({video: true})` into a hidden `<video>`, only starts capturing when Vigil Mode is `armed` (listens for the existing `vigil_mode_changed` socket event) _and_ the tab is visible (Page Visibility API) _and_ the logged-in user has an enrolled face (new `face_enrolled`/`user_id` fields on `/api/status`). Captures a frame to a `<canvas>` every ~5s and POSTs it to the presence-check endpoint.
  - [x] **Consecutive-mismatch state machine** — lock condition is: the logged-in user's `user_id` is _absent_ from the matched faces _and_ at least one _other_ matched (or unmatched/`unknown`) face _is_ present, for 3 consecutive checks — a single bad frame (bad lighting, brief look-away, empty frame) doesn't trigger it or reset it; the counter only resets to 0 on a check where the logged-in user is visible again.
  - [x] **Lock screen UI** — full-viewport overlay blanking the rest of the app, "J.A.R.V.I.S. LOCKED" state, continues running the same capture loop underneath and auto-unlocks the instant a check sees the logged-in user alone in frame.
  - [x] **Snapshot on lock event** — `POST /api/face/lock-event` → `_record_device_lock()` reuses the `security_events` table (`event_type: "device_lock"`) so a lock event shows up in the same RECENT EVENTS list Vigil Mode already has, with the triggering snapshot attached via the existing `_db_record_security_event(..., snapshot=...)` path.

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

## Known Issues

- [x] **Settings panel closes entirely when switching tabs** — root cause: browser runs the microtask checkpoint between capture and bubble phases, so the `MutationObserver` in `settings.js` fired between them and saw "no pane open" before the new pane was shown. Fixed by wrapping the auto-close check in `setTimeout(0)` so it defers to after all event listeners complete; all `?v=` cache strings bumped to `?v=2`.

---

## GitHub Actions & CI/CD

Automated workflows to keep the repo healthy and branches in sync.

- [x] **Auto-merge staging → main** — nightly cron merges staging into main if clean
- [x] **Cascade merge on push** — when `staging` or `main` receives a push, automatically attempt to merge it into every other open branch; on conflict, open a detailed issue describing the conflicting files and assign it to whoever made the last commit on that branch
- [x] **Auto-deploy on push to `main`** — `deploy-main.yml` runs on `[self-hosted, homelab]`; pulls latest, restarts the stack with `docker compose up -d --build`, and health-checks `/login` before reporting success
- [x] **Playwright browser checks in `testing-smoke.yml`** — the smoke test currently only curls `/login` and `/` for non-5xx status; add a headless Playwright pass (with a seeded test account/session) that logs in and clicks through core UI (Settings panel tabs, chat send) so a broken button/JS bundle fails CI, not just a broken route
- [x] **Self-hosted GitHub Actions runner** — home server registered as a self-hosted runner (`homelab` label); `docker-build`, `compose-validate`, and `testing-smoke` run on it for persistent Docker layer cache and real-stack testing, without eating GitHub's free-tier minutes. `android-build` and `actionlint` stay on `ubuntu-latest` for clean environments. Docs: [Self-hosted runners](https://docs.github.com/en/actions/concepts/runners/self-hosted-runners)

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
