============================================================

J.A.R.V.I.S.

============================================================

## WHAT THIS IS

A self-hosted AI assistant you talk to — a drop-in replacement for Siri, Google
Assistant, and Alexa. He speaks and listens in your browser using server-side
Whisper transcription, and he thinks using an AI model of **your** choice —
Claude, ChatGPT, or almost any other.

Multi-user household: every person in the house logs in with their own Authentik
account, gets their own AI config and conversation history, and can optionally
enroll a voice so J.A.R.V.I.S. knows who is speaking without being asked.

Beyond conversation, J.A.R.V.I.S. can:

- Control your entire smart home via Home Assistant
- Set timers and reminders you hear across the house
- Read upcoming calendar events, create new ones, and look up contacts
- Manage shared household shopping and to-do lists
- Run named routines (a sequence of smart-home actions)
- Alert you proactively when a device changes state
- Control Zigbee devices directly via Zigbee2MQTT
- Control multi-room audio via Snapcast
- Monitor your garage (MyQ/Chamberlain)
- Check and control your Tesla by voice
- Play music through Spotify or Apple Music
- Receive and triage your phone messages
- Alert you when someone is at the door
- Recognize who's home via camera-based face detection, and flag security events
- Track balances, transactions, and spending by category from your linked bank accounts
- Transcribe meetings and generate structured notes
- Read the latest news headlines by category

See [ROADMAP.md](ROADMAP.md) for what's shipped, in progress, and planned next
(mobile PWA push notifications, offline mode, and a tap-to-talk mic flow are
next up).

## WHICH AI CAN HE USE?

Each user picks their own on the setup screen after first login:

- **Anthropic (Claude)** — Haiku (cheapest), Sonnet (most in-character), Opus (most capable).
- **OpenAI (ChatGPT)** — GPT-4o mini, GPT-4o, GPT-4.1, etc.
- **Other (OpenAI-compatible)** — any service via its base URL: OpenRouter
  (Claude, Gemini, Llama and hundreds more from one key), Groq, Together,
  Ollama, LM Studio, etc.

## WHAT YOU NEED

- Docker and Docker Compose
- An [Authentik](https://goauthentik.io) instance (self-hosted or cloud)
- An API key from whichever AI provider you want to use

## SETUP

**Step 1 — Configure environment.** Copy `.env.example` to `.env` and fill it in:

```bash
cp .env.example .env
```

Required variables:

| Variable             | What it is                                                           |
| -------------------- | -------------------------------------------------------------------- |
| `SECRET_KEY`         | Any long random string — signs session cookies                       |
| `POSTGRES_PASSWORD`  | Password for the Postgres database                                   |
| `DATABASE_URL`       | Full Postgres connection string (default matches compose.yml)        |
| `AUTHENTIK_URL`      | Base URL of your Authentik instance, e.g. `https://auth.example.com` |
| `OIDC_APP_SLUG`      | The slug of your Authentik application, e.g. `jarvis`                |
| `OIDC_CLIENT_ID`     | Client ID from your Authentik OAuth2 provider                        |
| `OIDC_CLIENT_SECRET` | Client secret from your Authentik OAuth2 provider                    |
| `APP_URL`            | Public URL of this app, e.g. `https://jarvis.example.com`            |

Optional:

| Variable                  | What it is                                                                                          |
| ------------------------- | --------------------------------------------------------------------------------------------------- |
| `OIDC_DISCOVERY_URL`      | Override the OIDC discovery URL if it doesn't follow the Authentik pattern                          |
| `OIDC_ADMIN_GROUP`        | Authentik group whose members get the admin role (default: `jarvis-admins`)                         |
| `MQTT_BROKER`             | Hostname/IP of your Zigbee2MQTT MQTT broker (enables Zigbee control)                                |
| `MQTT_PORT`               | MQTT broker port (default: `1883`)                                                                  |
| `MQTT_USER`               | MQTT username (optional)                                                                            |
| `MQTT_PASSWORD`           | MQTT password (optional)                                                                            |
| `Z2M_BASE_TOPIC`          | Zigbee2MQTT base topic (default: `zigbee2mqtt`)                                                     |
| `SNAPCAST_URL`            | Snapcast server JSON-RPC URL, e.g. `http://192.168.1.100:1780`                                      |
| `PLAID_CLIENT_ID`         | Plaid client ID (enables the FINANCE panel)                                                         |
| `PLAID_SECRET`            | Plaid secret matching `PLAID_ENV`                                                                   |
| `PLAID_ENV`               | `sandbox` (default, no real bank needed) or `production`                                            |
| `FINANCE_POLL_INTERVAL`   | Seconds between background transaction syncs (default: `14400`, 4h)                                 |
| `VISION_POLL_INTERVAL`    | Seconds between camera presence checks (default: `30`)                                              |
| `VISION_AWAY_TIMEOUT`     | Seconds without a detection before someone is marked away (default: `1800`)                         |
| `VISION_FACE_THRESHOLD`   | Face-match distance threshold, lower = stricter (default: `0.4`)                                    |
| `VISION_MOTION_THRESHOLD` | Mean-pixel-diff threshold for Vigil Mode motion detection, lower = more sensitive (default: `15.0`) |
| `VAPID_PUBLIC_KEY`        | Web Push public key (enables push notifications for Vigil Mode alerts)                              |
| `VAPID_PRIVATE_KEY`       | Web Push private key matching `VAPID_PUBLIC_KEY`                                                    |
| `VAPID_SUBJECT`           | Contact URI for Web Push, e.g. `mailto:you@example.com` (default: `mailto:admin@example.com`)       |

Generate a VAPID key pair once with `vapid --gen` (installed alongside
`pywebpush`), then copy `applicationServerKey`/`privateKey` into
`VAPID_PUBLIC_KEY`/`VAPID_PRIVATE_KEY`.

**Step 2 — Set up Authentik.** In your Authentik admin panel:

1. Go to **Applications → Providers → Create → OAuth2/OpenID Provider**.
2. Set the redirect URI to `{APP_URL}/auth/callback`.
3. Note the **Client ID** and **Client Secret** — put them in `.env`.
4. Set `OIDC_APP_SLUG` to the application slug shown on the provider detail page.
5. Create an **Application** and assign the provider to it.
6. Create a group named `jarvis-admins` (or whatever you set `OIDC_ADMIN_GROUP` to)
   and add your own account to it. Admins can see all household members.

**Step 3 — Start.** Run:

```bash
docker compose up -d
```

This starts J.A.R.V.I.S. on port 5000 and Postgres on port 5432 (internal only).
Postgres data is persisted in the `postgres_data` Docker volume.

**Step 4 — First login.** Open `http://localhost:5000` (or your `APP_URL`) in your browser.
You'll be redirected to Authentik to log in. Once authenticated, you land on the setup
screen where you pick a provider, enter your API key, and click **CONNECT**.
J.A.R.V.I.S. verifies the key and saves your config — from then on, logging
in takes you straight to the chat.

## HOW TO TALK TO HIM

- He starts in STANDBY (the dim lock screen).
- Say **"JARVIS"** to wake him. (Or press **SPACEBAR**.)
- Then just talk — ask him anything.
- Say **"standby"** or **"go to sleep"** to send him back to the lock screen.
- Prefer typing? Press **C** to open the chat panel.

Your voice is transcribed server-side by a Whisper model — no browser speech
API required. Chrome, Firefox, and Edge all work for audio input.

## ALWAYS-ON WAKE WORD

J.A.R.V.I.S. ships a standalone daemon (`wake_daemon.py`) that listens on a
microphone 24/7 using [openWakeWord](https://github.com/dscripka/openWakeWord)
and pings the server the moment it hears "Hey Jarvis". The server then wakes
every connected browser session simultaneously.

### Install the daemon

```bash
pip install -r requirements/daemon/requirements.txt
```

### Configure

Create `/etc/jarvis-wake.env`:

```env
JARVIS_URL=https://jarvis.example.com
WAKE_TOKEN=<your-webhook-token-from-settings>
DEVICE_ID=living-room         # any name for this microphone
WAKE_MODEL=hey_jarvis         # openWakeWord model name
WAKE_THRESHOLD=0.5            # detection confidence threshold
WAKE_COOLDOWN=3.0             # seconds between triggers
```

Get your webhook token from the J.A.R.V.I.S. settings panel under **Messages**.

### Run as a systemd service

```bash
sudo cp systemd/jarvis-wake.service /etc/systemd/system/jarvis-wake@.service
sudo systemctl enable --now jarvis-wake@$USER
```

You can install the daemon on as many devices as you like (Raspberry Pi, old
laptop, smart speaker). If two devices hear the wake word at the same time,
a 2-second deduplication window prevents double-triggers.

## HOUSEHOLD & MULTI-USER

Every person in the house has their own Authentik account. Each user gets their
own conversation history and AI provider config.

### Voice enrollment (optional)

Voice enrollment lets J.A.R.V.I.S. identify who is speaking from a brief audio
sample, so he can address you by name and apply per-person settings (like
kid-safe mode) automatically.

1. Open the settings panel and go to **Voice Enrollment**.
2. Record several short samples when prompted.
3. Click **Save Voiceprint**.

To clear your voiceprint, click **Remove Voiceprint** in the same panel.

### Kid-safe mode

Any household member can be marked as kid-safe by an admin (or by themselves in
their profile). When J.A.R.V.I.S. identifies that person by voice, he
automatically switches to age-appropriate language for the entire session.

### Shared household lists

All household members share the same shopping list, to-do list, and any custom
lists you create:

- "Add milk to the shopping list."
- "What's on the shopping list?"
- "Remove eggs from shopping."
- "Clear the to-do list."

Lists sync instantly across all sessions.

### Profile settings

Each user can set a display name (used when J.A.R.V.I.S. addresses them by
voice) and toggle kid-safe mode from their profile.

## TIMERS & REMINDERS

J.A.R.V.I.S. tracks timers and reminders in the database and fires them even if
the browser tab is reloaded — as long as one session is open.

- "Set a 10-minute pasta timer."
- "Remind me to take my medication at 8 PM."
- "What timers do I have running?"
- "Cancel the pasta timer."
- "Remind me every 30 minutes to drink water." _(recurring reminder)_

When a timer or reminder fires, J.A.R.V.I.S. wakes from standby and speaks the
alert on every connected session for that user.

## CALENDAR & CONTACTS

Open the **AGENDA** button in the top bar to connect a CalDAV calendar and a
CardDAV address book. This works with iCloud, Google, Fastmail, Nextcloud, and
other DAV providers. If your provider requires an app-specific password, use
that instead of your normal login password.

Once connected, J.A.R.V.I.S. can:

- Read your upcoming events
- Create new calendar events
- Look up phone numbers and email addresses from your contacts

Examples:

- "What's on my calendar tomorrow?"
- "Add dinner with Sam on Friday at 7 PM."
- "What's Mom's number?"
- "Look up John's email."

## NEWS

J.A.R.V.I.S. pulls live headlines from the BBC RSS feeds — no API key required:

- "What's in the news today?"
- "Give me the top tech headlines."
- "Any health news?"

Categories: general, technology, science, health, business, sports.

## DAILY BRIEFING

A spoken morning and/or evening summary — current weather, today's remaining
calendar events, today's reminders, and top news headlines — delivered
automatically at times you choose. Off by default; enable it under **DAILY
BRIEFING** in the calendar/contacts (**AGENDA**) settings panel, or by voice:

- "Turn on my daily briefing."
- "Set my morning briefing for 6:30."
- "What's my briefing status?"
- "Give me my briefing now." _(delivers it immediately, any time)_
- "Turn off my daily briefing."

When a scheduled briefing fires, J.A.R.V.I.S. wakes from standby and speaks it
on every connected session for that user, and sends a push notification if
you've enabled push (see **VISION & PRESENCE** below) — so it reaches you even
if the app isn't open.

## HOME ASSISTANT

Connect J.A.R.V.I.S. to your Home Assistant instance from the settings panel.
You'll need your Home Assistant URL and a Long-Lived Access Token
(Profile → Long-Lived Access Tokens in the HA UI).

Once connected, he can:

- Check the state of any device ("Are the lights on in the kitchen?")
- Control lights, switches, thermostats, locks, and more ("Turn off all the lights")
- Trigger scripts and automations ("Run the bedtime routine")
- Tell you about recent doorbell and motion activity

## ROUTINES

A routine is a named sequence of steps that runs on demand or via a voice phrase.

- "Create a Good Night routine that turns off all the lights, locks the front
  door, and says 'Sleep well'."
- "Run the Good Night routine."
- "What routines do I have?"
- "Delete the Good Night routine."

Step types available in a routine:

| Type         | What it does                                     |
| ------------ | ------------------------------------------------ |
| `ha_service` | Calls a Home Assistant service                   |
| `speak`      | J.A.R.V.I.S. says something aloud                |
| `delay`      | Waits N seconds before the next step (max 5 min) |

Routines are stored per-user in the database and survive restarts.

## DEVICE ALERTS

Set up proactive alerts that fire when a Home Assistant entity changes state:

- "Alert me if the garage door stays open."
- "Notify me when the front door sensor opens."
- "Tell me if the temperature drops below 60."

J.A.R.V.I.S. checks all active alerts every 2 minutes. When a condition is met,
he wakes from standby and speaks the configured message. A cooldown period
(default 30 minutes) prevents repeated alerts for the same condition.

Supported conditions: `equals`, `not_equals`, `greater_than`, `less_than`.

## ZIGBEE (ZIGBEE2MQTT)

If you run Zigbee2MQTT, J.A.R.V.I.S. can send commands directly to Zigbee
devices without going through Home Assistant. Set `MQTT_BROKER` in `.env` to
enable this.

- "Turn on the bedroom strip light."
- "Set the kitchen bulb to 50% brightness."

The `zigbee_control` tool sends a JSON payload to
`{Z2M_BASE_TOPIC}/{device_name}/set`.

## MULTI-ROOM AUDIO (SNAPCAST)

If you run a [Snapcast](https://github.com/badaix/snapcast) server, set
`SNAPCAST_URL` in `.env` to control it by room:

- "What's playing in the living room?"
- "Set the kitchen volume to 40."
- "Mute the bedroom."
- "Switch the office to the TV stream."

If you tell J.A.R.V.I.S. which room you're in (via the `ROOM` env var on the
wake daemon), room-specific commands default to that room.

## GARAGE DOOR (MYQ / CHAMBERLAIN)

Connect your MyQ Chamberlain smart garage from the settings panel using your
MyQ account email and password.

Once connected: "Is the garage door open?" or "Close the garage door."

## TESLA

J.A.R.V.I.S. can check and control your Tesla vehicles by voice. Two API
options are available — choose either or both.

### Option A — Unofficial API (recommended for most users)

No developer account needed. Get a Tesla refresh token from one of these apps:

- **Auth App for Tesla** (iOS / Android) — easiest
- **tesla-auth** CLI — run it once, copy the refresh token it prints

Then open the TESLA settings panel in J.A.R.V.I.S., paste the token, and
click **CONNECT**.

### Option B — Fleet API (official)

Requires a registered Tesla developer app. The admin must set these in `.env`:

| Variable              | What it is                             |
| --------------------- | -------------------------------------- |
| `TESLA_CLIENT_ID`     | Client ID from developer.tesla.com     |
| `TESLA_CLIENT_SECRET` | Client secret from developer.tesla.com |

Once set, users click **CONNECT WITH TESLA** in the Fleet API tab and complete
Tesla's OAuth flow. Note: vehicle commands via the Fleet API additionally
require a virtual key to be paired with the car via the Tesla mobile app.

### What he can do

- "What's my Tesla's battery level?"
- "Lock the car" / "Unlock the car"
- "Turn on the heat" / "Set the temperature to 72"
- "Start charging" / "Stop charging"
- "Open the trunk" / "Open the frunk"
- "Honk the horn" / "Flash the lights"

Commands auto-wake the vehicle — this may take up to 30 seconds if the car
is sleeping.

## SPOTIFY

Connect Spotify from the settings panel via OAuth. Once connected:

- "What's playing?"
- "Play some jazz."
- "Skip this track."
- "Turn the volume up to 80."
- "Pause the music."

## APPLE MUSIC

Connect Apple Music by providing your MusicKit user token in the settings panel.
Once connected, all the same playback controls work as with Spotify.

## FINANCE (PLAID)

Connect your bank and credit card accounts from the **FINANCE** settings
panel. The admin needs `PLAID_CLIENT_ID` and `PLAID_SECRET` set in `.env`
first — Plaid provides these free for their `sandbox` environment, no real
bank required to try it out. Set `PLAID_ENV=production` (with production
credentials) to link real accounts.

In sandbox mode, use Plaid's test institution ("Platypus Bank") with username
`user_good` and password `pass_good`.

Once linked:

- "What's my checking account balance?"
- "Show me my recent transactions."
- "How much did I spend on dining out this month?"
- "Recategorize that Amazon purchase as a business expense."

Balances and transactions sync automatically in the background (every 4 hours
by default — see `FINANCE_POLL_INTERVAL`). This is read-only: J.A.R.V.I.S.
cannot move money or make payments.

## PHONE MESSAGES

J.A.R.V.I.S. can receive your text messages and alert you to the important ones.

**Setup:** In the settings panel, go to **Messages** and copy your webhook token
and ingest URL. Configure your phone to forward messages to that URL via an
automation app (e.g. Tasker on Android, Shortcuts on iOS, or Konnected).

Requests must include the header `Authorization: Bearer <your-token>` and a
JSON body with `sender` and `text` fields.

J.A.R.V.I.S. uses your AI model to classify each message. If it contains an
invitation, event, deadline, or urgent request, he'll alert you in real time
with a spoken announcement. Non-important messages are stored silently.

## DOORBELL ALERTS

J.A.R.V.I.S. can announce doorbell rings, motion, and package deliveries
from your front door camera.

**Setup:** In the settings panel, go to **Doorbell** and copy your webhook URL.
Point your doorbell or NVR (Frigate, HomeKit, Unifi Protect, etc.) at that
URL using the same Bearer token as phone messages.

Send a POST with JSON body:

```json
{ "event_type": "doorbell_press", "source": "front_door" }
```

Supported event types: `doorbell_press`, `motion`, `person`, `package`.
Motion alerts are suppressed between 11 PM and 7 AM.

## VISION & PRESENCE

J.A.R.V.I.S. can watch cameras to know who's home and flag security events,
using local face recognition — no cloud vision API involved.

**Setup:** In the **VISION** settings panel:

1. Add a camera — either a Home Assistant camera entity (`camera.front_door`)
   or a direct RTSP stream URL.
2. Upload a clear photo of your face under **Face Enrollment** so
   J.A.R.V.I.S. can recognize you (one sample is enough; more improves
   accuracy).

Once set up:

- "Who's home?"
- "Any security events today?"
- "Add a camera for the backyard."
- "Turn on privacy mode for the office camera." _(pauses detection for that camera)_
- "Arm Vigil Mode." / "Disarm Vigil Mode." / "Put Vigil Mode back on auto."

Cameras are polled every 30 seconds by default (`VISION_POLL_INTERVAL`); a
household member is marked away after no detection for 30 minutes
(`VISION_AWAY_TIMEOUT`). Unrecognized faces and motion (frame-difference
detection, threshold `VISION_MOTION_THRESHOLD`) are logged as security events
with a snapshot whenever Vigil Mode is heightened — which happens
automatically while everyone's away or it's night (**AUTO**, the default),
always (**ARMED**), or never (**DISARMED**). Toggle it by voice or in the
VISION settings panel. If you've enabled push notifications there, alerts
reach you even when the app isn't open.

## MEETING RECORDER

J.A.R.V.I.S. can transcribe your meetings live and generate structured notes
when you're done.

- Click **Record Meeting** (or say "start recording") to begin.
- Audio is transcribed in real time using Whisper.
- Click **End Meeting** when you're done — J.A.R.V.I.S. produces notes with
  a summary, key decisions, action items, and topics discussed.

Meetings are kept for 48 hours and then automatically deleted.

## LIVE HUD

The interface shows a live heads-up display with:

- CPU and RAM usage of the host server
- Network throughput (Mbps in/out) and packets per second
- Server uptime
- Current weather and temperature (fetched automatically by IP location)

## CHANGING HIS VOICE

J.A.R.V.I.S. uses your browser's built-in Web Speech synthesis. To get a
British male voice (closest to the films), install the English (UK) voice pack
in your operating system's speech settings. He'll automatically prefer it if
available.

## DEVELOPMENT

Run the full lint, format, and test suite:

```bash
make lint
```

This runs ruff, ty, prettier, and pytest (coverage threshold: 50%).

Run with hot-reload (no Docker):

```bash
pip install -r requirements/local/requirements.txt
uvicorn app:app --reload --port 5000 --app-dir python
```

Note: you'll still need a running Postgres and `.env` set up.

The frontend (`templates/`, `static/v2/`) is split into one file per feature
panel — see [CONTRIBUTING.md](CONTRIBUTING.md#frontend-structure) for the
layout before adding a new one.

## TROUBLESHOOTING

- **Redirected to login but Authentik shows an error** — check that the redirect
  URI in Authentik exactly matches `{APP_URL}/auth/callback`.
- **"OIDC not configured"** — `OIDC_APP_SLUG` (or `OIDC_DISCOVERY_URL`) is
  missing or wrong in `.env`.
- **Postgres connection refused** — the `jarvis` container starts only after
  Postgres passes its health check; give it a few seconds on first boot.
- **The mic doesn't work** — allow microphone access in your browser. Unlike
  the browser Speech API, Whisper works in Firefox, Chrome, and Edge.
- **"Key was rejected"** — double-check the API key and that the account has credit.
- **He talks over himself / hears himself** — use headphones, or lower speaker volume.
- **Home Assistant: token was rejected** — the token needs at least read access;
  for control you need to allow services in HA's `configuration.yaml`.
- **MyQ: Could not reach MyQ** — verify your email/password are correct for the
  MyQ mobile app. The API can be rate-limited if you log in too frequently.
- **Wake daemon: no trigger** — check `WAKE_TOKEN` matches the token in settings,
  and that `JARVIS_URL` is reachable from the daemon host.
- **Voice not recognized** — re-enroll with a few more samples in a quieter
  environment, or lower the threshold in `_VOICE_THRESHOLD` in `app.py`.
- **MQTT / Zigbee errors** — confirm `MQTT_BROKER` is reachable and the device
  friendly name in Zigbee2MQTT matches what you say.
- **FINANCE panel doesn't appear / link fails** — `PLAID_CLIENT_ID` and
  `PLAID_SECRET` must be set in `.env` by the admin before any user can link
  an account; restart the container after changing them.
- **VISION: no detections** — check the camera source is reachable (HA entity
  exists and is not unavailable, or the RTSP URL works in VLC), and that
  you've uploaded a face photo under Face Enrollment.

Each user's API key and credentials are stored only in your Postgres database
and are sent only to the relevant service. They are never shared with anyone else.

Enjoy, sir.
