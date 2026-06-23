============================================================
J.A.R.V.I.S. — STARTER KIT
Your own Iron Man-style AI, running on your server
============================================================

## WHAT THIS IS

A clean, lightweight J.A.R.V.I.S. you talk to. He speaks and listens in
your browser using your computer's built-in voices, and he thinks using an
AI model of YOUR choice — Claude, ChatGPT, or almost any other.

Multi-user: each person who logs in gets their own AI provider config and
conversation history, stored in PostgreSQL and protected behind your
Authentik identity provider.

This is the Starter Kit: it's built for CONVERSATION. He has the full
J.A.R.V.I.S. personality, the holographic interface, the standby screen,
voice in and voice out.

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

### 1. Configure environment

Copy `.env.example` to `.env` and fill it in:

```bash
cp .env.example .env
```

The variables you must set:

| Variable | What it is |
| --- | --- |
| `SECRET_KEY` | Any long random string — signs session cookies |
| `POSTGRES_PASSWORD` | Password for the Postgres database |
| `DATABASE_URL` | Full Postgres connection string (default matches compose.yml) |
| `AUTHENTIK_URL` | Base URL of your Authentik instance, e.g. `https://auth.example.com` |
| `OIDC_DISCOVERY_URL` | Authentik OIDC discovery URL (see below) |
| `OIDC_CLIENT_ID` | Client ID from your Authentik OAuth2 provider |
| `OIDC_CLIENT_SECRET` | Client secret from your Authentik OAuth2 provider |
| `APP_URL` | Public URL of this app, e.g. `https://jarvis.example.com` |

### 2. Set up Authentik

In your Authentik admin panel:

1. Go to **Applications → Providers → Create → OAuth2/OpenID Provider**.
2. Set the redirect URI to `{APP_URL}/auth/callback`.
3. Note the **Client ID** and **Client Secret** — put them in `.env`.
4. The discovery URL is shown on the provider detail page. It follows this pattern:

   ```
   https://auth.example.com/application/o/<your-app-slug>/.well-known/openid-configuration
   ```

5. Create an **Application** and assign the provider to it.

### 3. Start

```bash
docker compose up -d
```

This starts J.A.R.V.I.S. on port 5000 and Postgres on port 5432 (internal only).
Postgres data is persisted in the `postgres_data` Docker volume.

### 4. First login

Open `http://localhost:5000` (or your `APP_URL`) in your browser. You'll be
redirected to Authentik to log in. Once authenticated, you land on the setup
screen where you pick a provider, enter your API key, and click **CONNECT**.
J.A.R.V.I.S. verifies the key and saves your config — from then on, logging
in takes you straight to the chat.

## HOW TO TALK TO HIM

- He starts in STANDBY (the dim lock screen).
- Say **"JARVIS"** to wake him. (Or press **SPACEBAR**.)
- Then just talk — ask him anything.
- Say **"standby"** or **"go to sleep"** to send him back to the lock screen.
- Prefer typing? Press **C** to open the chat panel.

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
- **"OIDC not configured"** — `OIDC_DISCOVERY_URL` is missing or wrong in `.env`.
- **Postgres connection refused** — the `jarvis` container starts only after
  Postgres passes its health check; give it a few seconds on first boot.
- **The mic doesn't work** — use Chrome or Edge and allow microphone access.
  Firefox doesn't support browser voice input — you can still type with **C**.
- **"Key was rejected"** — double-check the API key and that the account has credit.
- **He talks over himself / hears himself** — use headphones, or lower speaker volume.

Each user's API key is stored only in your Postgres database and is sent only
to the AI provider they chose. It is never shared with anyone else.

Enjoy, sir.
