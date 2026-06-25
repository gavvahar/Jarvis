============================================================
J.A.R.V.I.S.

============================================================

## WHAT THIS IS

A clean, lightweight J.A.R.V.I.S. you talk to. He speaks and listens in
your browser using server-side Whisper transcription, and he thinks using an
AI model of YOUR choice — Claude, ChatGPT, or almost any other.

Multi-user: each person who logs in gets their own AI provider config and
conversation history, stored in PostgreSQL and protected behind your
Authentik identity provider.

Beyond conversation, J.A.R.V.I.S. can control your home, monitor your
garage, receive and triage your phone messages, alert you when someone's at
the door, and transcribe your meetings.

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

The variables you must set:

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

| Variable             | What it is                                                                  |
| -------------------- | --------------------------------------------------------------------------- |
| `OIDC_DISCOVERY_URL` | Override the OIDC discovery URL if it doesn't follow the Authentik pattern  |
| `OIDC_ADMIN_GROUP`   | Authentik group whose members get the admin role (default: `jarvis-admins`) |

**Step 2 — Set up Authentik.** In your Authentik admin panel:

1. Go to **Applications → Providers → Create → OAuth2/OpenID Provider**.
2. Set the redirect URI to `{APP_URL}/auth/callback`.
3. Note the **Client ID** and **Client Secret** — put them in `.env`.
4. Set `OIDC_APP_SLUG` to the application slug shown on the provider detail page.
5. Create an **Application** and assign the provider to it.

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

## HOME ASSISTANT INTEGRATION

Connect J.A.R.V.I.S. to your Home Assistant instance from the settings panel.
You'll need your Home Assistant URL and a Long-Lived Access Token
(Profile → Long-Lived Access Tokens in the HA UI).

Once connected, he can:

- Check the state of any device ("Are the lights on in the kitchen?")
- Control lights, switches, thermostats, locks, and more ("Turn off all the lights")
- Trigger scripts and automations ("Run the bedtime routine")
- Tell you about recent doorbell and motion activity

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

### Using both

If both are connected, J.A.R.V.I.S. prefers the Unofficial API (no virtual
key required for commands) and falls back to the Fleet API if needed.

### What he can do

- "What's my Tesla's battery level?"
- "Lock the car" / "Unlock the car"
- "Turn on the heat" / "Set the temperature to 72"
- "Start charging" / "Stop charging"
- "Open the trunk" / "Open the frunk"
- "Honk the horn" / "Flash the lights"

Commands auto-wake the vehicle — mention to the user it may take up to
30 seconds if the car is sleeping.

## GARAGE DOOR (MYQ / CHAMBERLAIN)

Connect your MyQ Chamberlain smart garage from the settings panel using your
MyQ account email and password.

Once connected: "Is the garage door open?" or "Close the garage door."

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

## MEETING RECORDER

J.A.R.V.I.S. can transcribe your meetings live and generate structured notes
when you're done.

- Click **Record Meeting** (or say "start recording") to begin.
- Audio is transcribed in real time using Whisper.
- Click **End Meeting** when you're done — J.A.R.V.I.S. produces notes with
  a summary, key decisions, action items, and topics discussed.
- Past meeting notes are accessible from the meetings panel.

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

Run the linter:

```bash
make lint
```

Run with hot-reload (no Docker):

```bash
pip install -r requirements.txt
uvicorn app:app --reload --port 5000
```

Note: you'll still need a running Postgres and `.env` set up.

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

Each user's API key and credentials are stored only in your Postgres database
and are sent only to the relevant service. They are never shared with anyone else.

Enjoy, sir.
