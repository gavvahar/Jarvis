"""
app.py — J.A.R.V.I.S. Starter Kit backend (FastAPI + python-socketio).

Multi-user: each user authenticates via Authentik (OIDC) and gets their own
config and conversation history stored in PostgreSQL.

Three providers:
  • anthropic         — Claude, via AsyncAnthropic
  • openai            — GPT models, via AsyncOpenAI
  • openai_compatible — any OpenAI-compatible endpoint (Ollama, OpenRouter, …)
"""

import json, os, re, asyncio, secrets, tempfile, urllib.parse, asyncpg, httpx, datetime, hashlib, base64


from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException, Request, File, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
import socketio
from dotenv import load_dotenv

from personality import JARVIS_SYSTEM

load_dotenv()

# ─── CONSTANTS ────────────────────────────────────────────────────────────────
MAX_HISTORY = 20

DEFAULT_MODELS = {
    "anthropic": "claude-haiku-4-5",
    "openai": "gpt-4o-mini",
    "openai_compatible": "",
}
VALID_PROVIDERS = set(DEFAULT_MODELS.keys())

# ─── ENV ──────────────────────────────────────────────────────────────────────
DATABASE_URL = os.environ.get("DATABASE_URL", "postgresql://jarvis:jarvis@postgres/jarvis")
AUTHENTIK_URL = os.environ.get("AUTHENTIK_URL", "").rstrip("/")
_OIDC_APP_SLUG = os.environ.get("OIDC_APP_SLUG", "").strip()
OIDC_DISCOVERY_URL = os.environ.get("OIDC_DISCOVERY_URL", "") or (
    f"{AUTHENTIK_URL}/application/o/{_OIDC_APP_SLUG}/.well-known/openid-configuration" if AUTHENTIK_URL and _OIDC_APP_SLUG else ""
)
OIDC_CLIENT_ID = os.environ.get("OIDC_CLIENT_ID", "")
OIDC_CLIENT_SECRET = os.environ.get("OIDC_CLIENT_SECRET", "")
APP_URL = os.environ.get("APP_URL", "http://localhost:5000").rstrip("/")
SECRET_KEY = os.environ.get("SECRET_KEY", "change-me")
OIDC_ADMIN_GROUP = os.environ.get("OIDC_ADMIN_GROUP", "jarvis-admins")
TESLA_CLIENT_ID = os.environ.get("TESLA_CLIENT_ID", "")
TESLA_CLIENT_SECRET = os.environ.get("TESLA_CLIENT_SECRET", "")

# ─── DB ───────────────────────────────────────────────────────────────────────
_db_pool: asyncpg.Pool | None = None


def _pool() -> asyncpg.Pool:
    assert _db_pool is not None, "Database pool not initialised"
    return _db_pool


_SCHEMA = """
CREATE TABLE IF NOT EXISTS user_configs (
    user_id     TEXT PRIMARY KEY,
    email       TEXT NOT NULL DEFAULT '',
    role        TEXT NOT NULL DEFAULT 'user',
    provider    TEXT NOT NULL DEFAULT 'anthropic',
    api_key     TEXT NOT NULL DEFAULT '',
    model       TEXT NOT NULL DEFAULT 'claude-haiku-4-5',
    base_url    TEXT NOT NULL DEFAULT '',
    ha_url      TEXT NOT NULL DEFAULT '',
    ha_token    TEXT NOT NULL DEFAULT '',
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

ALTER TABLE user_configs ADD COLUMN IF NOT EXISTS role TEXT NOT NULL DEFAULT 'user';
ALTER TABLE user_configs ADD COLUMN IF NOT EXISTS webhook_token TEXT NOT NULL DEFAULT '';
ALTER TABLE user_configs ADD COLUMN IF NOT EXISTS myq_email TEXT NOT NULL DEFAULT '';
ALTER TABLE user_configs ADD COLUMN IF NOT EXISTS myq_password TEXT NOT NULL DEFAULT '';
ALTER TABLE user_configs ADD COLUMN IF NOT EXISTS tesla_method TEXT NOT NULL DEFAULT '';
ALTER TABLE user_configs ADD COLUMN IF NOT EXISTS tesla_refresh_token TEXT NOT NULL DEFAULT '';
ALTER TABLE user_configs ADD COLUMN IF NOT EXISTS tesla_fleet_refresh_token TEXT NOT NULL DEFAULT '';

CREATE TABLE IF NOT EXISTS phone_messages (
    id          BIGSERIAL PRIMARY KEY,
    user_id     TEXT NOT NULL REFERENCES user_configs(user_id) ON DELETE CASCADE,
    sender      TEXT NOT NULL DEFAULT '',
    body        TEXT NOT NULL DEFAULT '',
    important   BOOLEAN NOT NULL DEFAULT FALSE,
    reason      TEXT NOT NULL DEFAULT '',
    received_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_phone_messages_user ON phone_messages (user_id, received_at DESC);

CREATE TABLE IF NOT EXISTS conversations (
    id          BIGSERIAL PRIMARY KEY,
    user_id     TEXT NOT NULL REFERENCES user_configs(user_id) ON DELETE CASCADE,
    role        TEXT NOT NULL,
    content     TEXT NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_conversations_user ON conversations (user_id, created_at DESC);

CREATE TABLE IF NOT EXISTS meetings (
    id          BIGSERIAL PRIMARY KEY,
    user_id     TEXT NOT NULL REFERENCES user_configs(user_id) ON DELETE CASCADE,
    started_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    ended_at    TIMESTAMPTZ,
    transcript  TEXT NOT NULL DEFAULT '',
    notes       TEXT NOT NULL DEFAULT '',
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_meetings_user ON meetings (user_id, started_at DESC);

CREATE TABLE IF NOT EXISTS doorbell_events (
    id          BIGSERIAL PRIMARY KEY,
    user_id     TEXT NOT NULL REFERENCES user_configs(user_id) ON DELETE CASCADE,
    event_type  TEXT NOT NULL,
    source      TEXT NOT NULL DEFAULT '',
    received_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_doorbell_events_user ON doorbell_events (user_id, received_at DESC);
"""


async def _db_init():
    global _db_pool
    _db_pool = await asyncpg.create_pool(DATABASE_URL, min_size=2, max_size=10)
    async with _pool().acquire() as conn:
        await conn.execute(_SCHEMA)


async def _db_ensure_user(user_id: str, email: str, role: str):
    async with _pool().acquire() as conn:
        await conn.execute(
            """
            INSERT INTO user_configs (user_id, email, role)
            VALUES ($1, $2, $3)
            ON CONFLICT (user_id) DO UPDATE SET email = EXCLUDED.email, role = EXCLUDED.role
            """,
            user_id,
            email,
            role,
        )


async def _db_load_config(user_id: str) -> dict:
    async with _pool().acquire() as conn:
        row = await conn.fetchrow(
            "SELECT role, provider, api_key, model, base_url, ha_url, ha_token, myq_email, myq_password, tesla_method, tesla_refresh_token, tesla_fleet_refresh_token FROM user_configs WHERE user_id = $1",
            user_id,
        )
    if row is None:
        return {
            "role": "user",
            "provider": "anthropic",
            "api_key": "",
            "model": "claude-haiku-4-5",
            "base_url": "",
            "ha_url": "",
            "ha_token": "",
            "myq_email": "",
            "myq_password": "",
            "tesla_method": "",
            "tesla_refresh_token": "",
            "tesla_fleet_refresh_token": "",
        }
    return dict(row)


async def _db_save_config(user_id: str, config: dict):
    async with _pool().acquire() as conn:
        await conn.execute(
            """
            UPDATE user_configs
            SET provider=$2, api_key=$3, model=$4, base_url=$5,
                ha_url=$6, ha_token=$7, myq_email=$8, myq_password=$9,
                updated_at=NOW()
            WHERE user_id=$1
            """,
            user_id,
            config["provider"],
            config["api_key"],
            config["model"],
            config["base_url"],
            config["ha_url"],
            config["ha_token"],
            config.get("myq_email", ""),
            config.get("myq_password", ""),
        )


async def _db_load_conversation(user_id: str) -> list:
    async with _pool().acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT role, content FROM (
                SELECT role, content, created_at
                FROM conversations
                WHERE user_id = $1
                ORDER BY created_at DESC
                LIMIT $2
            ) sub ORDER BY created_at ASC
            """,
            user_id,
            MAX_HISTORY,
        )
    return [{"role": r["role"], "content": json.loads(r["content"])} for r in rows]


async def _db_append_message(user_id: str, role: str, content):
    async with _pool().acquire() as conn:
        await conn.execute(
            "INSERT INTO conversations (user_id, role, content) VALUES ($1, $2, $3)",
            user_id,
            role,
            json.dumps(content),
        )
        await conn.execute(
            """
            DELETE FROM conversations
            WHERE user_id = $1 AND id NOT IN (
                SELECT id FROM conversations
                WHERE user_id = $1
                ORDER BY created_at DESC
                LIMIT $2
            )
            """,
            user_id,
            MAX_HISTORY,
        )


async def _db_clear_conversation(user_id: str):
    async with _pool().acquire() as conn:
        await conn.execute("DELETE FROM conversations WHERE user_id = $1", user_id)


async def _db_get_or_create_webhook_token(user_id: str) -> str:
    async with _pool().acquire() as conn:
        row = await conn.fetchrow("SELECT webhook_token FROM user_configs WHERE user_id = $1", user_id)
    if row and row["webhook_token"]:
        return row["webhook_token"]
    token = secrets.token_hex(32)
    async with _pool().acquire() as conn:
        await conn.execute(
            "UPDATE user_configs SET webhook_token = $2 WHERE user_id = $1",
            user_id,
            token,
        )
    return token


async def _db_regenerate_webhook_token(user_id: str) -> str:
    token = secrets.token_hex(32)
    async with _pool().acquire() as conn:
        await conn.execute(
            "UPDATE user_configs SET webhook_token = $2 WHERE user_id = $1",
            user_id,
            token,
        )
    return token


async def _db_find_user_by_token(token: str) -> str | None:
    async with _pool().acquire() as conn:
        row = await conn.fetchrow(
            "SELECT user_id FROM user_configs WHERE webhook_token = $1 AND webhook_token != ''",
            token,
        )
    return row["user_id"] if row else None


async def _db_store_phone_message(user_id: str, sender: str, body: str, important: bool, reason: str):
    async with _pool().acquire() as conn:
        await conn.execute(
            "INSERT INTO phone_messages (user_id, sender, body, important, reason) VALUES ($1, $2, $3, $4, $5)",
            user_id,
            sender,
            body,
            important,
            reason,
        )


async def _db_create_meeting(user_id: str) -> int:
    async with _pool().acquire() as conn:
        row = await conn.fetchrow("INSERT INTO meetings (user_id) VALUES ($1) RETURNING id", user_id)
    return row["id"]


async def _db_append_transcript_segment(meeting_id: int, segment: str):
    async with _pool().acquire() as conn:
        await conn.execute(
            "UPDATE meetings SET transcript = transcript || $2 WHERE id = $1",
            meeting_id,
            " " + segment,
        )


async def _db_finalize_meeting(meeting_id: int, notes: str):
    async with _pool().acquire() as conn:
        await conn.execute(
            "UPDATE meetings SET ended_at = NOW(), notes = $2 WHERE id = $1",
            meeting_id,
            notes,
        )


async def _db_store_doorbell_event(user_id: str, event_type: str, source: str):
    async with _pool().acquire() as conn:
        await conn.execute(
            "INSERT INTO doorbell_events (user_id, event_type, source) VALUES ($1, $2, $3)",
            user_id,
            event_type,
            source,
        )


async def _db_get_recent_doorbell_events(user_id: str, hours: float = 24) -> list:
    async with _pool().acquire() as conn:
        rows = await conn.fetch(
            "SELECT event_type, source, received_at FROM doorbell_events WHERE user_id = $1 AND received_at > NOW() - $2 ORDER BY received_at DESC LIMIT 50",
            user_id,
            datetime.timedelta(hours=hours),
        )
    return [
        {
            "event_type": r["event_type"],
            "source": r["source"],
            "received_at": r["received_at"].isoformat(),
        }
        for r in rows
    ]


# ─── AUTH ─────────────────────────────────────────────────────────────────────
_signer: URLSafeTimedSerializer | None = None
_oidc_config: dict | None = None


def _get_signer() -> URLSafeTimedSerializer:
    assert _signer is not None, "Session signer not initialised"
    return _signer


def _get_oidc_config() -> dict:
    assert _oidc_config is not None, "OIDC not configured"
    return _oidc_config


async def _fetch_oidc_config():
    global _oidc_config
    if not OIDC_DISCOVERY_URL:
        print("[AUTH] OIDC_DISCOVERY_URL not set — authentication disabled.", flush=True)
        return
    try:
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.get(OIDC_DISCOVERY_URL)
            r.raise_for_status()
            _oidc_config = r.json()
        print("[AUTH] OIDC configuration loaded.", flush=True)
    except Exception as e:
        print(f"[AUTH] Failed to fetch OIDC discovery document: {e}", flush=True)


def _sign_session(user_id: str) -> str:
    return _get_signer().dumps(user_id)


def _verify_session(value: str) -> str | None:
    try:
        return _get_signer().loads(value, max_age=86400 * 30)
    except (BadSignature, SignatureExpired):
        return None


def _get_current_user(request: Request) -> str | None:
    cookie = request.cookies.get("jarvis_session")
    if not cookie:
        return None
    return _verify_session(cookie)


def _get_user_from_environ(environ: dict) -> str | None:
    """Extract and verify the session cookie from a Socket.IO ASGI environ."""
    headers = dict(environ.get("headers", []))
    cookie_str = headers.get(b"cookie", b"").decode()
    for part in cookie_str.split(";"):
        part = part.strip()
        if part.startswith("jarvis_session="):
            return _verify_session(part[len("jarvis_session=") :])
    return None


# ─── PER-USER STATE ───────────────────────────────────────────────────────────
# {user_id: {config, client, provider, conversation}}
_user_states: dict[str, dict] = {}
_user_locks: dict[str, asyncio.Lock] = {}

# {user_id: {meeting_id, segments}}
_active_meetings: dict[str, dict] = {}

# socket sid → user_id
_sid_to_user: dict[str, str] = {}

# {user_id: {unofficial_access, unofficial_expiry, fleet_access, fleet_expiry}}
_tesla_tokens: dict[str, dict] = {}
# {state_token: {user_id, code_verifier}}
_tesla_auth_pending: dict[str, dict] = {}

_location_context: dict = {}

_whisper = None
_whisper_lock = asyncio.Lock()


def _get_whisper():
    global _whisper
    if _whisper is None:
        from faster_whisper import WhisperModel

        _whisper = WhisperModel("tiny.en", device="cpu", compute_type="int8")
    return _whisper


def _get_user_lock(user_id: str) -> asyncio.Lock:
    if user_id not in _user_locks:
        _user_locks[user_id] = asyncio.Lock()
    return _user_locks[user_id]


async def _get_user_state(user_id: str) -> dict:
    if user_id not in _user_states:
        config = await _db_load_config(user_id)
        conversation = await _db_load_conversation(user_id)
        provider = config.get("provider", "anthropic")
        if provider not in VALID_PROVIDERS:
            provider = "anthropic"
        if not config.get("model"):
            config["model"] = DEFAULT_MODELS.get(provider, "")
        client = _build_client(provider, config.get("api_key", ""), config.get("base_url", ""))
        _user_states[user_id] = {
            "config": config,
            "client": client,
            "provider": provider,
            "conversation": conversation,
            "role": config.get("role", "user"),
            "user_id": user_id,
        }
    return _user_states[user_id]


def _user_configured(state: dict) -> bool:
    return state["client"] is not None


async def _require_admin(request: Request) -> str:
    user_id = _get_current_user(request)
    if not user_id:
        raise HTTPException(401)
    state = await _get_user_state(user_id)
    if state.get("role") != "admin":
        raise HTTPException(403, "Admin access required")
    return user_id


# ─── LLM CLIENTS ─────────────────────────────────────────────────────────────
def _build_client(provider, api_key, base_url=""):
    if not api_key and provider != "openai_compatible":
        return None
    try:
        if provider == "anthropic":
            import anthropic

            return anthropic.AsyncAnthropic(api_key=api_key)
        import openai

        kwargs = {"api_key": api_key or "ollama"}
        if provider == "openai_compatible" and base_url:
            kwargs["base_url"] = base_url.strip()
        return openai.AsyncOpenAI(**kwargs)
    except Exception as e:
        print(f"[CLIENT] Failed to build {provider} client: {e}", flush=True)
        return None


def _build_sync_client(provider, api_key, base_url=""):
    if not api_key and provider != "openai_compatible":
        return None
    try:
        if provider == "anthropic":
            import anthropic

            return anthropic.Anthropic(api_key=api_key)
        import openai

        kwargs = {"api_key": api_key or "ollama"}
        if provider == "openai_compatible" and base_url:
            kwargs["base_url"] = base_url.strip()
        return openai.OpenAI(**kwargs)
    except Exception as e:
        print(f"[CLIENT] Failed to build sync {provider} client: {e}", flush=True)
        return None


# ─── HOME ASSISTANT ───────────────────────────────────────────────────────────
HA_TOOLS_ANTHROPIC = [
    {
        "name": "get_ha_states",
        "description": (
            "Get the current state of Home Assistant devices. "
            "Optionally filter by domain (e.g. 'light', 'switch', 'climate', "
            "'sensor', 'automation', 'script'). Omit domain to get all entities."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "domain": {
                    "type": "string",
                    "description": "Optional domain filter, e.g. 'light' or 'switch'.",
                }
            },
        },
    },
    {
        "name": "call_ha_service",
        "description": ("Call a Home Assistant service to control a device, run a script, or trigger an automation."),
        "input_schema": {
            "type": "object",
            "properties": {
                "domain": {
                    "type": "string",
                    "description": "Service domain, e.g. 'light', 'switch', 'climate', 'automation', 'script'.",
                },
                "service": {
                    "type": "string",
                    "description": "Service name, e.g. 'turn_on', 'turn_off', 'toggle', 'trigger'.",
                },
                "entity_id": {
                    "type": "string",
                    "description": "Entity ID to act on, e.g. 'light.living_room'. Omit for scripts/automations.",
                },
                "service_data": {
                    "type": "object",
                    "description": 'Optional extra data, e.g. {"brightness_pct": 50} for lights.',
                },
            },
            "required": ["domain", "service"],
        },
    },
    {
        "name": "get_doorbell_events",
        "description": (
            "Get recent doorbell and motion events from the front door. "
            "Use this to answer questions about who came to the door, recent motion, "
            "deliveries, or 'any activity while I was out?'"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "hours": {
                    "type": "number",
                    "description": "How many hours back to look (default 24).",
                }
            },
        },
    },
]

HA_TOOLS_OPENAI = [
    {
        "type": "function",
        "function": {
            "name": "get_ha_states",
            "description": (
                "Get the current state of Home Assistant devices. "
                "Optionally filter by domain (e.g. 'light', 'switch', 'climate', "
                "'sensor', 'automation', 'script'). Omit domain to get all entities."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "domain": {
                        "type": "string",
                        "description": "Optional domain filter, e.g. 'light' or 'switch'.",
                    }
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "call_ha_service",
            "description": ("Call a Home Assistant service to control a device, run a script, or trigger an automation."),
            "parameters": {
                "type": "object",
                "properties": {
                    "domain": {
                        "type": "string",
                        "description": "Service domain, e.g. 'light', 'switch', 'climate', 'automation', 'script'.",
                    },
                    "service": {
                        "type": "string",
                        "description": "Service name, e.g. 'turn_on', 'turn_off', 'toggle', 'trigger'.",
                    },
                    "entity_id": {
                        "type": "string",
                        "description": "Entity ID to act on, e.g. 'light.living_room'. Omit for scripts/automations.",
                    },
                    "service_data": {
                        "type": "object",
                        "description": 'Optional extra data, e.g. {"brightness_pct": 50} for lights.',
                    },
                },
                "required": ["domain", "service"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_doorbell_events",
            "description": (
                "Get recent doorbell and motion events from the front door. "
                "Use this to answer questions about who came to the door, recent motion, "
                "deliveries, or 'any activity while I was out?'"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "hours": {
                        "type": "number",
                        "description": "How many hours back to look (default 24).",
                    }
                },
            },
        },
    },
]


MYQ_TOOLS_ANTHROPIC = [
    {
        "name": "get_garage_status",
        "description": "Get the current open/closed state of your MyQ Chamberlain smart garage door(s).",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "set_garage_door",
        "description": "Open or close a MyQ Chamberlain smart garage door.",
        "input_schema": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["open", "close"],
                    "description": "Whether to open or close the door.",
                },
                "device": {
                    "type": "string",
                    "description": "Garage door name. Omit if you only have one.",
                },
            },
            "required": ["action"],
        },
    },
]

MYQ_TOOLS_OPENAI = [
    {
        "type": "function",
        "function": {
            "name": "get_garage_status",
            "description": "Get the current open/closed state of your MyQ Chamberlain smart garage door(s).",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "set_garage_door",
            "description": "Open or close a MyQ Chamberlain smart garage door.",
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["open", "close"],
                        "description": "Whether to open or close the door.",
                    },
                    "device": {
                        "type": "string",
                        "description": "Garage door name. Omit if you only have one.",
                    },
                },
                "required": ["action"],
            },
        },
    },
]


def _get_myq_tools(config: dict, provider: str) -> list:
    if not _myq_configured(config):
        return []
    return MYQ_TOOLS_ANTHROPIC if provider == "anthropic" else MYQ_TOOLS_OPENAI


def _ha_configured(config: dict) -> bool:
    return bool(config.get("ha_url") and config.get("ha_token"))


# ─── MYQ / CHAMBERLAIN GARAGE ─────────────────────────────────────────────────
def _myq_configured(config: dict) -> bool:
    return bool(config.get("myq_email") and config.get("myq_password"))


async def _myq_get_status(config: dict) -> str:
    try:
        import aiohttp
        import pymyq

        async with aiohttp.ClientSession() as session:
            myq = await pymyq.login(config["myq_email"], config["myq_password"], session)
            if not myq.covers:
                return "No garage doors found in your MyQ account."
            lines = [f"{d.name}: {d.state}" for d in myq.covers.values()]
            return "\n".join(lines)
    except Exception as e:
        return f"Could not reach MyQ: {e}"


async def _myq_set_door(config: dict, device_name: str | None, action: str) -> str:
    try:
        import aiohttp
        import pymyq

        async with aiohttp.ClientSession() as session:
            myq = await pymyq.login(config["myq_email"], config["myq_password"], session)
            if not myq.covers:
                return "No garage doors found in your MyQ account."
            if device_name:
                device = next(
                    (d for d in myq.covers.values() if device_name.lower() in d.name.lower()),
                    None,
                )
                if device is None:
                    names = ", ".join(d.name for d in myq.covers.values())
                    return f"No garage door matching '{device_name}'. Available: {names}."
            else:
                device = next(iter(myq.covers.values()))
            if action == "open":
                await device.open(wait_for_state=None)
            else:
                await device.close(wait_for_state=None)
            return f"{device.name}: {action} command sent."
    except Exception as e:
        return f"Could not reach MyQ: {e}"


# ─── TESLA ────────────────────────────────────────────────────────────────────
_TESLA_AUTH_BASE = "https://auth.tesla.com/oauth2/v3"
_TESLA_OWNER_BASE = "https://owner-api.teslamotors.com"
_TESLA_FLEET_BASE = "https://fleet-api.prd.na.vn.cloud.tesla.com"


def _tesla_configured(config: dict) -> bool:
    method = config.get("tesla_method", "")
    if not method:
        return False
    if method in ("unofficial", "both") and not config.get("tesla_refresh_token"):
        return False
    if method in ("fleet", "both") and not config.get("tesla_fleet_refresh_token"):
        return False
    return True


async def _tesla_unofficial_access_token(user_id: str, config: dict) -> str:
    cached = _tesla_tokens.get(user_id, {})
    expiry = cached.get("unofficial_expiry")
    if cached.get("unofficial_access") and expiry and expiry > datetime.datetime.utcnow() + datetime.timedelta(minutes=5):
        return cached["unofficial_access"]
    async with httpx.AsyncClient(timeout=15) as c:
        r = await c.post(
            f"{_TESLA_AUTH_BASE}/token",
            json={
                "grant_type": "refresh_token",
                "client_id": "ownerapi",
                "refresh_token": config["tesla_refresh_token"],
                "scope": "openid email offline_access",
            },
        )
        r.raise_for_status()
        data = r.json()
    access_token = data["access_token"]
    new_refresh = data.get("refresh_token")
    expiry_dt = datetime.datetime.utcnow() + datetime.timedelta(seconds=data.get("expires_in", 28800))
    _tesla_tokens.setdefault(user_id, {})
    _tesla_tokens[user_id].update({"unofficial_access": access_token, "unofficial_expiry": expiry_dt})
    if new_refresh and new_refresh != config.get("tesla_refresh_token"):
        config["tesla_refresh_token"] = new_refresh
        async with _pool().acquire() as conn:
            await conn.execute(
                "UPDATE user_configs SET tesla_refresh_token = $2 WHERE user_id = $1",
                user_id, new_refresh,
            )
    return access_token


async def _tesla_fleet_access_token(user_id: str, config: dict) -> str:
    cached = _tesla_tokens.get(user_id, {})
    expiry = cached.get("fleet_expiry")
    if cached.get("fleet_access") and expiry and expiry > datetime.datetime.utcnow() + datetime.timedelta(minutes=5):
        return cached["fleet_access"]
    async with httpx.AsyncClient(timeout=15) as c:
        r = await c.post(
            f"{_TESLA_AUTH_BASE}/token",
            json={
                "grant_type": "refresh_token",
                "client_id": TESLA_CLIENT_ID,
                "client_secret": TESLA_CLIENT_SECRET,
                "refresh_token": config["tesla_fleet_refresh_token"],
            },
        )
        r.raise_for_status()
        data = r.json()
    access_token = data["access_token"]
    new_refresh = data.get("refresh_token")
    expiry_dt = datetime.datetime.utcnow() + datetime.timedelta(seconds=data.get("expires_in", 28800))
    _tesla_tokens.setdefault(user_id, {})
    _tesla_tokens[user_id].update({"fleet_access": access_token, "fleet_expiry": expiry_dt})
    if new_refresh and new_refresh != config.get("tesla_fleet_refresh_token"):
        config["tesla_fleet_refresh_token"] = new_refresh
        async with _pool().acquire() as conn:
            await conn.execute(
                "UPDATE user_configs SET tesla_fleet_refresh_token = $2 WHERE user_id = $1",
                user_id, new_refresh,
            )
    return access_token


async def _tesla_unofficial_vehicles(user_id: str, config: dict) -> list:
    token = await _tesla_unofficial_access_token(user_id, config)
    async with httpx.AsyncClient(timeout=15) as c:
        r = await c.get(
            f"{_TESLA_OWNER_BASE}/api/1/vehicles",
            headers={"Authorization": f"Bearer {token}"},
        )
        r.raise_for_status()
    return r.json().get("response", [])


async def _tesla_unofficial_wake(user_id: str, config: dict, vehicle_id: int, token: str) -> bool:
    for _ in range(10):
        async with httpx.AsyncClient(timeout=15) as c:
            r = await c.post(
                f"{_TESLA_OWNER_BASE}/api/1/vehicles/{vehicle_id}/wake_up",
                headers={"Authorization": f"Bearer {token}"},
            )
        if r.status_code == 200 and r.json().get("response", {}).get("state") == "online":
            return True
        await asyncio.sleep(3)
    return False


async def _tesla_unofficial_cmd(user_id: str, config: dict, vehicle_id: int, command: str, data: dict | None = None) -> dict:
    token = await _tesla_unofficial_access_token(user_id, config)
    await _tesla_unofficial_wake(user_id, config, vehicle_id, token)
    async with httpx.AsyncClient(timeout=30) as c:
        r = await c.post(
            f"{_TESLA_OWNER_BASE}/api/1/vehicles/{vehicle_id}/command/{command}",
            headers={"Authorization": f"Bearer {token}"},
            json=data or {},
        )
        r.raise_for_status()
    return r.json().get("response", {})


async def _tesla_fleet_vehicles(user_id: str, config: dict) -> list:
    token = await _tesla_fleet_access_token(user_id, config)
    async with httpx.AsyncClient(timeout=15) as c:
        r = await c.get(
            f"{_TESLA_FLEET_BASE}/api/1/vehicles",
            headers={"Authorization": f"Bearer {token}"},
        )
        r.raise_for_status()
    return r.json().get("response", [])


async def _tesla_fleet_wake(user_id: str, config: dict, vin: str, token: str) -> bool:
    for _ in range(10):
        async with httpx.AsyncClient(timeout=15) as c:
            r = await c.post(
                f"{_TESLA_FLEET_BASE}/api/1/vehicles/{vin}/wake_up",
                headers={"Authorization": f"Bearer {token}"},
            )
        if r.status_code == 200 and r.json().get("response", {}).get("state") == "online":
            return True
        await asyncio.sleep(3)
    return False


async def _tesla_fleet_cmd(user_id: str, config: dict, vin: str, command: str, data: dict | None = None) -> dict:
    token = await _tesla_fleet_access_token(user_id, config)
    await _tesla_fleet_wake(user_id, config, vin, token)
    async with httpx.AsyncClient(timeout=30) as c:
        r = await c.post(
            f"{_TESLA_FLEET_BASE}/api/1/vehicles/{vin}/command/{command}",
            headers={"Authorization": f"Bearer {token}"},
            json=data or {},
        )
        r.raise_for_status()
    return r.json().get("response", {})


async def _tesla_pick_vehicle(user_id: str, config: dict, name_hint: str | None = None) -> tuple:
    """Returns (method, vehicle_dict). Unofficial is always preferred when available."""
    method = config.get("tesla_method", "unofficial")

    def _match(vehicles):
        if name_hint:
            return next((v for v in vehicles if name_hint.lower() in v.get("display_name", "").lower()), vehicles[0])
        return vehicles[0]

    if method in ("unofficial", "both"):
        try:
            vehicles = await _tesla_unofficial_vehicles(user_id, config)
            if vehicles:
                return "unofficial", _match(vehicles)
        except Exception:
            if method == "unofficial":
                raise

    vehicles = await _tesla_fleet_vehicles(user_id, config)
    if not vehicles:
        raise ValueError("No Tesla vehicle found in your account.")
    return "fleet", _match(vehicles)


def _c_to_f(c) -> float:
    return c * 9 / 5 + 32


async def _execute_tesla_tool(config: dict, name: str, args: dict, user_id: str = "") -> str:
    try:
        name_hint = args.get("vehicle")
        method, vehicle = await _tesla_pick_vehicle(user_id, config, name_hint)
        display = vehicle.get("display_name", "Tesla")

        if method == "unofficial":
            vid = vehicle["id"]
            token = await _tesla_unofficial_access_token(user_id, config)

            if name == "get_vehicle_status":
                if vehicle.get("state") != "online":
                    return f"{display} is {vehicle.get('state', 'asleep')}. Send a command to auto-wake it, or ask me to check again in a moment."
                async with httpx.AsyncClient(timeout=15) as c:
                    r = await c.get(
                        f"{_TESLA_OWNER_BASE}/api/1/vehicles/{vid}/vehicle_data",
                        headers={"Authorization": f"Bearer {token}"},
                    )
                    r.raise_for_status()
                d = r.json().get("response", {})
                ch = d.get("charge_state", {})
                cl = d.get("climate_state", {})
                vs = d.get("vehicle_state", {})
                lines = [
                    f"{display}",
                    f"Battery: {ch.get('battery_level', '?')}% — {round(ch.get('est_battery_range', 0))} mi est. range",
                    f"Charge state: {ch.get('charging_state', 'unknown')}",
                    f"Doors: {'Locked' if vs.get('locked') else 'Unlocked'}",
                ]
                if cl.get("inside_temp") is not None:
                    lines.append(f"Climate: {'On' if cl.get('is_climate_on') else 'Off'} — {_c_to_f(cl['inside_temp']):.0f}°F inside")
                if cl.get("outside_temp") is not None:
                    lines.append(f"Outside temp: {_c_to_f(cl['outside_temp']):.0f}°F")
                if vs.get("odometer"):
                    lines.append(f"Odometer: {vs['odometer']:,.0f} mi")
                return "\n".join(lines)

            if name == "set_climate":
                action = args.get("action", "start")
                if action == "stop":
                    resp = await _tesla_unofficial_cmd(user_id, config, vid, "auto_conditioning_stop")
                else:
                    resp = await _tesla_unofficial_cmd(user_id, config, vid, "auto_conditioning_start")
                    temp_f = args.get("temperature_f")
                    if temp_f is not None:
                        temp_c = (float(temp_f) - 32) * 5 / 9
                        await _tesla_unofficial_cmd(user_id, config, vid, "set_temps", {"driver_temp": temp_c, "passenger_temp": temp_c})
                return f"Climate {'started' if action == 'start' else 'stopped'} on {display}." if resp.get("result") else f"Command failed: {resp.get('reason', 'unknown')}"

            if name == "actuate_trunk":
                which = args.get("which", "rear")
                resp = await _tesla_unofficial_cmd(user_id, config, vid, "actuate_trunk", {"which_trunk": which})
                label = "Rear trunk" if which == "rear" else "Frunk"
                return f"{label} opened on {display}." if resp.get("result") else f"Command failed: {resp.get('reason', 'unknown')}"

            _CMD = {
                "lock_vehicle": ("door_lock", "Doors locked"),
                "unlock_vehicle": ("door_unlock", "Doors unlocked"),
                "start_charging": ("charge_start", "Charging started"),
                "stop_charging": ("charge_stop", "Charging stopped"),
                "honk_horn": ("honk_horn", "Horn honked"),
                "flash_lights": ("flash_lights", "Lights flashed"),
            }
            if name in _CMD:
                cmd, label = _CMD[name]
                resp = await _tesla_unofficial_cmd(user_id, config, vid, cmd)
                return f"{label} on {display}." if resp.get("result") else f"Command failed: {resp.get('reason', 'unknown')}"

        else:  # fleet
            vin = vehicle.get("vin", "")
            token = await _tesla_fleet_access_token(user_id, config)

            if name == "get_vehicle_status":
                if vehicle.get("state") != "online":
                    return f"{display} is {vehicle.get('state', 'asleep')}. Send a command to auto-wake it."
                async with httpx.AsyncClient(timeout=15) as c:
                    r = await c.get(
                        f"{_TESLA_FLEET_BASE}/api/1/vehicles/{vin}/vehicle_data",
                        headers={"Authorization": f"Bearer {token}"},
                    )
                    r.raise_for_status()
                d = r.json().get("response", {})
                ch = d.get("charge_state", {})
                cl = d.get("climate_state", {})
                vs = d.get("vehicle_state", {})
                lines = [
                    f"{display}",
                    f"Battery: {ch.get('battery_level', '?')}% — {round(ch.get('est_battery_range', 0))} mi est. range",
                    f"Charge state: {ch.get('charging_state', 'unknown')}",
                    f"Doors: {'Locked' if vs.get('locked') else 'Unlocked'}",
                ]
                if cl.get("inside_temp") is not None:
                    lines.append(f"Climate: {'On' if cl.get('is_climate_on') else 'Off'} — {_c_to_f(cl['inside_temp']):.0f}°F inside")
                return "\n".join(lines)

            if name == "set_climate":
                action = args.get("action", "start")
                cmd = "auto_conditioning_start" if action == "start" else "auto_conditioning_stop"
                await _tesla_fleet_cmd(user_id, config, vin, cmd)
                temp_f = args.get("temperature_f")
                if action == "start" and temp_f is not None:
                    temp_c = (float(temp_f) - 32) * 5 / 9
                    await _tesla_fleet_cmd(user_id, config, vin, "set_temps", {"driver_temp": temp_c, "passenger_temp": temp_c})
                return f"Climate {'started' if action == 'start' else 'stopped'} on {display}."

            if name == "actuate_trunk":
                which = args.get("which", "rear")
                await _tesla_fleet_cmd(user_id, config, vin, "actuate_trunk", {"which_trunk": which})
                return f"{'Rear trunk' if which == 'rear' else 'Frunk'} command sent to {display}."

            _CMD_FLEET = {
                "lock_vehicle": ("door_lock", "Doors locked"),
                "unlock_vehicle": ("door_unlock", "Doors unlocked"),
                "start_charging": ("charge_start", "Charging started"),
                "stop_charging": ("charge_stop", "Charging stopped"),
                "honk_horn": ("honk_horn", "Horn honked"),
                "flash_lights": ("flash_lights", "Lights flashed"),
            }
            if name in _CMD_FLEET:
                cmd, label = _CMD_FLEET[name]
                await _tesla_fleet_cmd(user_id, config, vin, cmd)
                return f"{label} on {display}."

        return f"Unknown Tesla tool: {name}"
    except Exception as e:
        return f"Tesla error: {e}"


TESLA_TOOLS_ANTHROPIC = [
    {
        "name": "get_vehicle_status",
        "description": "Get the current status of a Tesla vehicle: battery level, estimated range, charge state, locked/unlocked, climate, and odometer.",
        "input_schema": {
            "type": "object",
            "properties": {
                "vehicle": {"type": "string", "description": "Vehicle display name. Omit if you only have one Tesla."},
            },
        },
    },
    {
        "name": "lock_vehicle",
        "description": "Lock all doors on the Tesla.",
        "input_schema": {
            "type": "object",
            "properties": {"vehicle": {"type": "string", "description": "Vehicle name. Omit for a single Tesla."}},
        },
    },
    {
        "name": "unlock_vehicle",
        "description": "Unlock all doors on the Tesla.",
        "input_schema": {
            "type": "object",
            "properties": {"vehicle": {"type": "string", "description": "Vehicle name. Omit for a single Tesla."}},
        },
    },
    {
        "name": "set_climate",
        "description": "Start or stop the Tesla's climate control. Optionally set the temperature.",
        "input_schema": {
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": ["start", "stop"], "description": "Start or stop climate."},
                "temperature_f": {"type": "number", "description": "Target temperature in °F (60–85). Only used when starting."},
                "vehicle": {"type": "string", "description": "Vehicle name. Omit for a single Tesla."},
            },
            "required": ["action"],
        },
    },
    {
        "name": "start_charging",
        "description": "Start charging the Tesla. The car must already be plugged in.",
        "input_schema": {
            "type": "object",
            "properties": {"vehicle": {"type": "string", "description": "Vehicle name. Omit for a single Tesla."}},
        },
    },
    {
        "name": "stop_charging",
        "description": "Stop charging the Tesla.",
        "input_schema": {
            "type": "object",
            "properties": {"vehicle": {"type": "string", "description": "Vehicle name. Omit for a single Tesla."}},
        },
    },
    {
        "name": "honk_horn",
        "description": "Honk the Tesla's horn.",
        "input_schema": {
            "type": "object",
            "properties": {"vehicle": {"type": "string", "description": "Vehicle name. Omit for a single Tesla."}},
        },
    },
    {
        "name": "flash_lights",
        "description": "Flash the Tesla's headlights.",
        "input_schema": {
            "type": "object",
            "properties": {"vehicle": {"type": "string", "description": "Vehicle name. Omit for a single Tesla."}},
        },
    },
    {
        "name": "actuate_trunk",
        "description": "Open the Tesla's rear trunk or front trunk (frunk).",
        "input_schema": {
            "type": "object",
            "properties": {
                "which": {"type": "string", "enum": ["rear", "front"], "description": "'rear' for the main boot, 'front' for the frunk. Default: rear."},
                "vehicle": {"type": "string", "description": "Vehicle name. Omit for a single Tesla."},
            },
        },
    },
]

TESLA_TOOLS_OPENAI = [
    {
        "type": "function",
        "function": {
            "name": t["name"],
            "description": t["description"],
            "parameters": t["input_schema"],
        },
    }
    for t in TESLA_TOOLS_ANTHROPIC
]

_TESLA_TOOL_NAMES = {t["name"] for t in TESLA_TOOLS_ANTHROPIC}


def _get_tesla_tools(config: dict, provider: str) -> list:
    if not _tesla_configured(config):
        return []
    return TESLA_TOOLS_ANTHROPIC if provider == "anthropic" else TESLA_TOOLS_OPENAI


def _ha_headers(config: dict) -> dict:
    return {
        "Authorization": f"Bearer {config['ha_token']}",
        "Content-Type": "application/json",
    }


def _get_ha_tools(config: dict, provider: str) -> list:
    if not _ha_configured(config):
        return []
    return HA_TOOLS_ANTHROPIC if provider == "anthropic" else HA_TOOLS_OPENAI


async def _validate_ha(url, token):
    try:
        async with httpx.AsyncClient(timeout=5) as c:
            r = await c.get(
                url.rstrip("/") + "/api/",
                headers={"Authorization": f"Bearer {token}"},
            )
        if r.status_code == 200:
            return True, ""
        if r.status_code == 401:
            return False, "Home Assistant token was rejected."
        return False, f"Home Assistant returned HTTP {r.status_code}."
    except Exception as e:
        return False, f"Could not reach Home Assistant: {e}"


async def _ha_get_states(config: dict, domain=None):
    url = config["ha_url"].rstrip("/") + "/api/states"
    async with httpx.AsyncClient(timeout=8) as c:
        r = await c.get(url, headers=_ha_headers(config))
    r.raise_for_status()
    states = r.json()
    if domain:
        states = [s for s in states if s["entity_id"].startswith(domain + ".")]
    lines = []
    for s in states[:60]:
        name = s.get("attributes", {}).get("friendly_name", "")
        line = f"{s['entity_id']}: {s['state']}"
        if name:
            line += f" ({name})"
        lines.append(line)
    return "\n".join(lines) if lines else "No entities found."


async def _ha_call_service(config: dict, domain, service, entity_id=None, service_data=None):
    url = config["ha_url"].rstrip("/") + f"/api/services/{domain}/{service}"
    payload = dict(service_data or {})
    if entity_id:
        payload["entity_id"] = entity_id
    async with httpx.AsyncClient(timeout=8) as c:
        r = await c.post(url, headers=_ha_headers(config), json=payload)
    return "Done." if r.status_code in (200, 201) else f"HA returned {r.status_code}: {r.text[:120]}"


async def _execute_ha_tool(config: dict, name, args, user_id: str = ""):
    try:
        if name == "get_ha_states":
            return await _ha_get_states(config, args.get("domain"))
        if name == "call_ha_service":
            return await _ha_call_service(
                config,
                args["domain"],
                args["service"],
                args.get("entity_id"),
                args.get("service_data"),
            )
        if name == "get_doorbell_events":
            if not user_id:
                return "No user context available."
            hours = float(args.get("hours", 24))
            events = await _db_get_recent_doorbell_events(user_id, hours)
            if not events:
                return f"No doorbell events in the past {hours:.0f} hours."
            lines = []
            for e in events:
                line = f"{e['received_at']}: {e['event_type']}"
                if e["source"]:
                    line += f" ({e['source']})"
                lines.append(line)
            return "\n".join(lines)
        if name == "get_garage_status":
            return await _myq_get_status(config)
        if name == "set_garage_door":
            return await _myq_set_door(config, args.get("device"), args.get("action", "close"))
        if name in _TESLA_TOOL_NAMES:
            return await _execute_tesla_tool(config, name, args, user_id)
        return f"Unknown tool: {name}"
    except Exception as e:
        return f"Error: {e}"


# ─── CONFIG VALIDATION ────────────────────────────────────────────────────────
def _openai_create_sync(client, model, messages, stream, max_out=500):
    last = None
    for extra in ({"max_tokens": max_out}, {"max_completion_tokens": max_out}, {}):
        try:
            return client.chat.completions.create(model=model, messages=messages, stream=stream, **extra)
        except Exception as e:
            last = e
            if any(
                x in str(e).lower()
                for x in (
                    "max_tokens",
                    "max_completion_tokens",
                    "unsupported",
                    "temperature",
                )
            ):
                continue
            raise
    assert last is not None
    raise last


def _validate(provider, api_key, model, base_url=""):
    client = _build_sync_client(provider, api_key, base_url)
    if client is None:
        pkg = "anthropic" if provider == "anthropic" else "openai"
        return (
            False,
            f"Could not initialise the client. Is the '{pkg}' package installed?",
        )
    model = model or DEFAULT_MODELS.get(provider, "")
    if not model:
        return False, "Please choose a model."
    try:
        if provider == "anthropic":
            client.messages.create(
                model=model,
                max_tokens=4,
                messages=[{"role": "user", "content": "Reply with: ok"}],
            )
        else:
            _openai_create_sync(
                client,
                model,
                [{"role": "user", "content": "Reply with: ok"}],
                stream=False,
                max_out=4,
            )
        return True, ""
    except Exception as e:
        msg = str(e)
        low = msg.lower()
        if "authentication" in low or "401" in low or ("invalid" in low and "key" in low):
            return False, "That key was rejected. Check it and try again."
        if "404" in low or "not_found" in low or ("model" in low and "exist" in low):
            return False, f"The model '{model}' wasn't found for this key/provider."
        if "credit" in low or "billing" in low or "quota" in low or "insufficient" in low:
            return False, "The key is valid but the account has no available credit."
        if "connection" in low or "could not" in low or "getaddrinfo" in low:
            return (
                False,
                "Couldn't reach the endpoint. Check the base URL / your connection.",
            )
        return False, f"Couldn't connect: {msg[:160]}"


# ─── MEETING NOTES ───────────────────────────────────────────────────────────
async def _generate_meeting_notes(state: dict, transcript: str) -> str:
    provider = state["provider"]
    config = state["config"]
    client = state["client"]
    model = config.get("model") or DEFAULT_MODELS.get(provider, "")
    prompt = (
        "Analyze this meeting transcript and produce structured notes in exactly this format:\n\n"
        "## Summary\n[2-3 sentence summary]\n\n"
        "## Key Decisions\n- [decision]\n\n"
        "## Action Items\n- [owner]: [action]\n\n"
        "## Topics Discussed\n- [topic]\n\n"
        f"Transcript:\n{transcript}"
    )
    if provider == "anthropic":
        msg = await client.messages.create(
            model=model,
            max_tokens=1000,
            messages=[{"role": "user", "content": prompt}],
        )
        return msg.content[0].text
    last = None
    for extra in ({"max_tokens": 1000}, {"max_completion_tokens": 1000}, {}):
        try:
            resp = await client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": prompt}],
                stream=False,
                **extra,
            )
            return resp.choices[0].message.content
        except Exception as e:
            last = e
            if any(x in str(e).lower() for x in ("max_tokens", "max_completion_tokens", "unsupported")):
                continue
            raise
    assert last is not None
    raise last


# ─── SOCKET.IO + FASTAPI ─────────────────────────────────────────────────────
sio = socketio.AsyncServer(async_mode="asgi", cors_allowed_origins="*")


@asynccontextmanager
async def lifespan(application: FastAPI):
    global _signer
    _signer = URLSafeTimedSerializer(SECRET_KEY)
    await _db_init()
    await _fetch_oidc_config()
    print("J.A.R.V.I.S. Starter Kit - online. Open http://localhost:5000", flush=True)
    try:
        await asyncio.to_thread(_get_whisper)
        print("[STT] Whisper model ready.", flush=True)
    except Exception as e:
        print(f"[STT] Whisper model load failed: {e}", flush=True)
    t1 = asyncio.create_task(_telemetry_loop())
    t2 = asyncio.create_task(_weather_loop())
    t3 = asyncio.create_task(_meeting_cleanup_loop())
    yield
    t1.cancel()
    t2.cancel()
    t3.cancel()
    if _db_pool:
        await _db_pool.close()


_SESSION_COOKIE_OPTS = dict(httponly=True, max_age=86400 * 30, samesite="lax")
_NO_REFRESH_PATHS = {"/login", "/auth/callback", "/logout"}

fast_app = FastAPI(lifespan=lifespan)
fast_app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")

app = socketio.ASGIApp(sio, other_asgi_app=fast_app)


@fast_app.middleware("http")
async def _refresh_session(request: Request, call_next):
    """Re-issue the session cookie on every authenticated request so the
    30-day expiry resets from last activity, not from login."""
    response = await call_next(request)
    if request.url.path not in _NO_REFRESH_PATHS and _signer:
        user_id = _get_current_user(request)
        if user_id:
            response.set_cookie("jarvis_session", _sign_session(user_id), **_SESSION_COOKIE_OPTS)
    return response


# ─── AUTH ROUTES ─────────────────────────────────────────────────────────────
@fast_app.get("/login")
async def login(request: Request):
    if not _oidc_config:
        raise HTTPException(503, "OIDC not configured — set OIDC_DISCOVERY_URL in .env")
    state = secrets.token_urlsafe(32)
    params = {
        "client_id": OIDC_CLIENT_ID,
        "response_type": "code",
        "scope": "openid email profile",
        "redirect_uri": f"{APP_URL}/auth/callback",
        "state": state,
    }
    url = _oidc_config["authorization_endpoint"] + "?" + urllib.parse.urlencode(params)
    response = RedirectResponse(url)
    response.set_cookie("oidc_state", state, httponly=True, max_age=300, samesite="lax")
    return response


@fast_app.get("/auth/callback")
async def auth_callback(request: Request):
    code = request.query_params.get("code")
    state = request.query_params.get("state")
    stored_state = request.cookies.get("oidc_state")
    if not code or not state or state != stored_state:
        raise HTTPException(400, "Invalid OAuth2 callback — state mismatch or missing code")

    try:
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.post(
                _get_oidc_config()["token_endpoint"],
                data={
                    "grant_type": "authorization_code",
                    "code": code,
                    "redirect_uri": f"{APP_URL}/auth/callback",
                    "client_id": OIDC_CLIENT_ID,
                    "client_secret": OIDC_CLIENT_SECRET,
                },
            )
            r.raise_for_status()
            tokens = r.json()

            r = await c.get(
                _get_oidc_config()["userinfo_endpoint"],
                headers={"Authorization": f"Bearer {tokens['access_token']}"},
            )
            r.raise_for_status()
            userinfo = r.json()
    except Exception as e:
        raise HTTPException(502, f"OIDC token exchange failed: {e}") from e

    user_id = userinfo["sub"]
    email = userinfo.get("email", "")
    groups = userinfo.get("groups", [])
    role = "admin" if OIDC_ADMIN_GROUP and OIDC_ADMIN_GROUP in groups else "user"
    await _db_ensure_user(user_id, email, role)
    # Invalidate cached state so role is reloaded on next request
    _user_states.pop(user_id, None)

    response = RedirectResponse("/", status_code=303)
    response.set_cookie("jarvis_session", _sign_session(user_id), **_SESSION_COOKIE_OPTS)
    response.delete_cookie("oidc_state")
    return response


@fast_app.get("/logout")
async def logout():
    response = RedirectResponse("/login", status_code=303)
    response.delete_cookie("jarvis_session")
    return response


# ─── HTTP ROUTES ─────────────────────────────────────────────────────────────
@fast_app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    if not _get_current_user(request):
        return RedirectResponse("/login")
    return templates.TemplateResponse(request, "index.html")


@fast_app.get("/api/status")
async def api_status(request: Request):
    user_id = _get_current_user(request)
    if not user_id:
        raise HTTPException(401)
    state = await _get_user_state(user_id)
    config = state["config"]
    return {
        "configured": _user_configured(state),
        "provider": config.get("provider", "anthropic"),
        "model": config.get("model", ""),
        "ha_configured": _ha_configured(config),
        "ha_url": config.get("ha_url", ""),
        "myq_configured": _myq_configured(config),
        "tesla_configured": _tesla_configured(config),
        "tesla_method": config.get("tesla_method", ""),
        "tesla_fleet_enabled": bool(TESLA_CLIENT_ID),
        "role": state.get("role", "user"),
    }


@fast_app.post("/api/transcribe")
async def api_transcribe(request: Request, audio: UploadFile = File(...)):
    if not _get_current_user(request):
        raise HTTPException(401)
    data = await audio.read()
    if not data:
        return {"text": ""}
    tmp = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".webm", delete=False) as f:
            f.write(data)
            tmp = f.name

        def _run():
            m = _get_whisper()
            segs, _ = m.transcribe(
                tmp,
                language="en",
                beam_size=1,
                vad_filter=True,
                no_speech_threshold=0.6,
            )
            return " ".join(s.text for s in segs).strip()

        async with _whisper_lock:
            text = await asyncio.to_thread(_run)
        return {"text": text}
    except Exception as e:
        print(f"[STT] {e}", flush=True)
        return {"text": ""}
    finally:
        if tmp:
            try:
                os.unlink(tmp)
            except OSError:
                pass


@fast_app.post("/api/save_config")
async def api_save_config(request: Request):
    user_id = _get_current_user(request)
    if not user_id:
        raise HTTPException(401)

    data = await request.json()
    provider = (data.get("provider") or "anthropic").strip()
    key = (data.get("key") or "").strip()
    model = (data.get("model") or "").strip()
    base_url = (data.get("base_url") or "").strip()
    ha_url = (data.get("ha_url") or "").strip()
    ha_token = (data.get("ha_token") or "").strip()

    if provider not in VALID_PROVIDERS:
        return {"ok": False, "error": "Unknown provider."}
    if not key and provider != "openai_compatible":
        return {"ok": False, "error": "No API key provided."}
    if provider == "openai_compatible" and not base_url:
        return {"ok": False, "error": "An OpenAI-compatible endpoint needs a base URL."}
    if not model:
        model = DEFAULT_MODELS.get(provider, "")

    ok, err = await asyncio.to_thread(_validate, provider, key, model, base_url)
    if not ok:
        return {"ok": False, "error": err}

    if ha_url and ha_token:
        ha_ok, ha_err = await _validate_ha(ha_url, ha_token)
        if not ha_ok:
            return {"ok": False, "error": f"Home Assistant: {ha_err}"}

    new_config = {
        "provider": provider,
        "api_key": key,
        "model": model,
        "base_url": base_url,
        "ha_url": ha_url,
        "ha_token": ha_token,
    }

    async with _get_user_lock(user_id):
        await _db_save_config(user_id, new_config)
        state = await _get_user_state(user_id)
        state["config"].update(new_config)
        state["client"] = _build_client(provider, key, base_url)
        state["provider"] = provider

    return {"ok": True}


@fast_app.post("/api/save_ha")
async def api_save_ha(request: Request):
    user_id = _get_current_user(request)
    if not user_id:
        raise HTTPException(401)

    data = await request.json()
    ha_url = (data.get("ha_url") or "").strip()
    ha_token = (data.get("ha_token") or "").strip()

    state = await _get_user_state(user_id)
    config = state["config"]
    effective_token = ha_token or config.get("ha_token", "")

    if ha_url and effective_token:
        ha_ok, ha_err = await _validate_ha(ha_url, effective_token)
        if not ha_ok:
            return {"ok": False, "error": ha_err}

    async with _get_user_lock(user_id):
        config["ha_url"] = ha_url
        if ha_token:
            config["ha_token"] = ha_token
        elif not ha_url:
            config["ha_token"] = ""
        await _db_save_config(user_id, config)

    return {"ok": True, "ha_configured": _ha_configured(config)}


@fast_app.post("/api/save_myq")
async def api_save_myq(request: Request):
    user_id = _get_current_user(request)
    if not user_id:
        raise HTTPException(401)

    data = await request.json()
    myq_email = (data.get("myq_email") or "").strip()
    myq_password = (data.get("myq_password") or "").strip()

    if myq_email and myq_password:
        result = await _myq_get_status({"myq_email": myq_email, "myq_password": myq_password})
        if result.startswith("Could not reach MyQ"):
            return {"ok": False, "error": result}

    state = await _get_user_state(user_id)
    config = state["config"]

    async with _get_user_lock(user_id):
        config["myq_email"] = myq_email
        config["myq_password"] = myq_password
        await _db_save_config(user_id, config)

    return {"ok": True, "myq_configured": _myq_configured(config)}


@fast_app.get("/api/meetings")
async def api_meetings(request: Request):
    user_id = _get_current_user(request)
    if not user_id:
        raise HTTPException(401)
    async with _pool().acquire() as conn:
        rows = await conn.fetch(
            "SELECT id, started_at, ended_at, notes FROM meetings WHERE user_id = $1 ORDER BY started_at DESC LIMIT 20",
            user_id,
        )
    return [
        {
            "id": r["id"],
            "started_at": r["started_at"].isoformat() if r["started_at"] else None,
            "ended_at": r["ended_at"].isoformat() if r["ended_at"] else None,
            "notes": r["notes"],
        }
        for r in rows
    ]


@fast_app.get("/api/meetings/{meeting_id}")
async def api_meeting_detail(request: Request, meeting_id: int):
    user_id = _get_current_user(request)
    if not user_id:
        raise HTTPException(401)
    async with _pool().acquire() as conn:
        row = await conn.fetchrow(
            "SELECT id, started_at, ended_at, transcript, notes FROM meetings WHERE id = $1 AND user_id = $2",
            meeting_id,
            user_id,
        )
    if not row:
        raise HTTPException(404)
    return {
        "id": row["id"],
        "started_at": row["started_at"].isoformat() if row["started_at"] else None,
        "ended_at": row["ended_at"].isoformat() if row["ended_at"] else None,
        "transcript": row["transcript"],
        "notes": row["notes"],
    }


# ─── PHONE MESSAGES ──────────────────────────────────────────────────────────
def _sids_for_user(user_id: str) -> list[str]:
    return [sid for sid, uid in _sid_to_user.items() if uid == user_id]


async def _classify_message(state: dict, sender: str, body: str) -> tuple[bool, str]:
    """Return (is_important, reason). Falls back to False on any error."""
    provider = state["provider"]
    config = state["config"]
    client = state["client"]
    model = config.get("model") or DEFAULT_MODELS.get(provider, "")
    prompt = (
        "You filter phone messages for importance. Reply with exactly:\n"
        "  yes: <one-line reason>\n"
        "or:\n"
        "  no\n\n"
        "Flag as important if the message contains: an invitation, event, deadline, "
        "urgent request, meeting request, or time-sensitive ask. "
        "Routine greetings, spam, and casual chitchat are NOT important.\n\n"
        f"Sender: {sender}\n"
        f"Message: {body}"
    )
    try:
        if provider == "anthropic":
            msg = await client.messages.create(
                model=model,
                max_tokens=60,
                messages=[{"role": "user", "content": prompt}],
            )
            reply = msg.content[0].text.strip().lower()
        else:
            last = None
            reply = "no"
            for extra in ({"max_tokens": 60}, {"max_completion_tokens": 60}, {}):
                try:
                    resp = await client.chat.completions.create(
                        model=model,
                        messages=[{"role": "user", "content": prompt}],
                        stream=False,
                        **extra,
                    )
                    reply = resp.choices[0].message.content.strip().lower()
                    break
                except Exception as e:
                    last = e
                    if any(x in str(e).lower() for x in ("max_tokens", "max_completion_tokens", "unsupported")):
                        continue
                    raise
            if last and not reply:
                raise last
        if reply.startswith("yes"):
            reason = reply[3:].lstrip(":").strip()
            return True, reason or "flagged as important"
        return False, ""
    except Exception as e:
        print(f"[MESSAGES] classify error: {e}", flush=True)
        return False, ""


async def _classify_and_notify(user_id: str, sender: str, body: str, state: dict):
    important, reason = await _classify_message(state, sender, body)
    await _db_store_phone_message(user_id, sender, body, important, reason)
    if important:
        for sid in _sids_for_user(user_id):
            await sio.emit(
                "message_alert",
                {"sender": sender, "text": body[:300], "reason": reason},
                to=sid,
            )


@fast_app.get("/api/messages/token")
async def api_messages_token(request: Request):
    user_id = _get_current_user(request)
    if not user_id:
        raise HTTPException(401)
    token = await _db_get_or_create_webhook_token(user_id)
    return {"token": token, "url": f"{APP_URL}/api/messages/ingest"}


@fast_app.post("/api/messages/token/regenerate")
async def api_messages_token_regenerate(request: Request):
    user_id = _get_current_user(request)
    if not user_id:
        raise HTTPException(401)
    token = await _db_regenerate_webhook_token(user_id)
    return {"token": token, "url": f"{APP_URL}/api/messages/ingest"}


@fast_app.post("/api/messages/ingest")
async def api_messages_ingest(request: Request):
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        raise HTTPException(401)
    token = auth[7:].strip()
    user_id = await _db_find_user_by_token(token)
    if not user_id:
        raise HTTPException(401)

    data = await request.json()
    sender = (data.get("sender") or "Unknown").strip()[:200]
    body = (data.get("text") or "").strip()[:2000]
    if not body:
        return {"ok": True}

    state = _user_states.get(user_id)
    if state and _user_configured(state):
        asyncio.create_task(_classify_and_notify(user_id, sender, body, state))
    else:
        await _db_store_phone_message(user_id, sender, body, False, "")

    return {"ok": True}


@fast_app.post("/api/doorbell/event")
async def api_doorbell_event(request: Request):
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        raise HTTPException(401)
    token = auth[7:].strip()
    user_id = await _db_find_user_by_token(token)
    if not user_id:
        raise HTTPException(401)

    data = await request.json()
    event_type = (data.get("event_type") or "motion").strip()[:50]
    source = (data.get("source") or "").strip()[:200]

    await _db_store_doorbell_event(user_id, event_type, source)

    hour = datetime.datetime.now().hour
    quiet = hour >= 23 or hour < 7
    if not (event_type == "motion" and quiet):
        speak_map = {
            "doorbell_press": "Someone is at the front door, sir.",
            "motion": "Motion detected at the front door.",
            "person": "A person has been detected at the front door, sir.",
            "package": "A package has been delivered to the front door, sir.",
        }
        speak_text = speak_map.get(event_type, "Doorbell alert.")
        for sid in _sids_for_user(user_id):
            await sio.emit(
                "doorbell_alert",
                {"event_type": event_type, "source": source, "speak": speak_text},
                to=sid,
            )

    return {"ok": True}


@fast_app.get("/api/doorbell/token")
async def api_doorbell_token(request: Request):
    user_id = _get_current_user(request)
    if not user_id:
        raise HTTPException(401)
    token = await _db_get_or_create_webhook_token(user_id)
    return {"token": token, "url": f"{APP_URL}/api/doorbell/event"}


@fast_app.get("/api/doorbell/events")
async def api_doorbell_events(request: Request):
    user_id = _get_current_user(request)
    if not user_id:
        raise HTTPException(401)
    async with _pool().acquire() as conn:
        rows = await conn.fetch(
            "SELECT id, event_type, source, received_at FROM doorbell_events WHERE user_id = $1 ORDER BY received_at DESC LIMIT 50",
            user_id,
        )
    return [
        {
            "id": r["id"],
            "event_type": r["event_type"],
            "source": r["source"],
            "received_at": r["received_at"].isoformat(),
        }
        for r in rows
    ]


# ─── TESLA ROUTES ────────────────────────────────────────────────────────────
@fast_app.get("/api/tesla/status")
async def api_tesla_status(request: Request):
    user_id = _get_current_user(request)
    if not user_id:
        raise HTTPException(401)
    state = await _get_user_state(user_id)
    config = state["config"]
    return {
        "tesla_configured": _tesla_configured(config),
        "tesla_method": config.get("tesla_method", ""),
        "tesla_fleet_enabled": bool(TESLA_CLIENT_ID),
    }


@fast_app.post("/api/tesla/save_unofficial")
async def api_tesla_save_unofficial(request: Request):
    user_id = _get_current_user(request)
    if not user_id:
        raise HTTPException(401)

    data = await request.json()
    refresh_token = (data.get("refresh_token") or "").strip()
    if not refresh_token:
        return {"ok": False, "error": "No refresh token provided."}

    _tesla_tokens.pop(user_id, None)
    try:
        test_config = {"tesla_refresh_token": refresh_token}
        token = await _tesla_unofficial_access_token(user_id, test_config)
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.get(
                f"{_TESLA_OWNER_BASE}/api/1/vehicles",
                headers={"Authorization": f"Bearer {token}"},
            )
            r.raise_for_status()
    except Exception as e:
        _tesla_tokens.pop(user_id, None)
        return {"ok": False, "error": f"Could not connect to Tesla: {e}"}

    state = await _get_user_state(user_id)
    config = state["config"]
    current_method = config.get("tesla_method", "")
    new_method = "both" if current_method in ("fleet",) and config.get("tesla_fleet_refresh_token") else "unofficial"

    async with _get_user_lock(user_id):
        config["tesla_refresh_token"] = refresh_token
        config["tesla_method"] = new_method
        async with _pool().acquire() as conn:
            await conn.execute(
                "UPDATE user_configs SET tesla_refresh_token = $2, tesla_method = $3 WHERE user_id = $1",
                user_id, refresh_token, new_method,
            )

    return {"ok": True, "tesla_configured": True, "tesla_method": new_method}


@fast_app.get("/api/tesla/fleet/auth")
async def api_tesla_fleet_auth(request: Request):
    user_id = _get_current_user(request)
    if not user_id:
        raise HTTPException(401)
    if not TESLA_CLIENT_ID:
        raise HTTPException(503, "Tesla Fleet API not configured — set TESLA_CLIENT_ID and TESLA_CLIENT_SECRET in .env")

    state_token = secrets.token_urlsafe(32)
    code_verifier = secrets.token_urlsafe(64)
    digest = hashlib.sha256(code_verifier.encode()).digest()
    code_challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()

    _tesla_auth_pending[state_token] = {"user_id": user_id, "code_verifier": code_verifier}
    if len(_tesla_auth_pending) > 200:
        for k in list(_tesla_auth_pending.keys())[:100]:
            _tesla_auth_pending.pop(k, None)

    params = urllib.parse.urlencode({
        "client_id": TESLA_CLIENT_ID,
        "redirect_uri": f"{APP_URL}/auth/tesla/callback",
        "response_type": "code",
        "scope": "openid offline_access vehicle_device_data vehicle_cmds vehicle_charging_cmds",
        "state": state_token,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
    })
    return RedirectResponse(f"{_TESLA_AUTH_BASE}/authorize?{params}")


@fast_app.get("/auth/tesla/callback")
async def auth_tesla_callback(request: Request):
    code = request.query_params.get("code")
    state_token = request.query_params.get("state")
    pending = _tesla_auth_pending.pop(state_token, None) if state_token else None
    if not pending or not code:
        raise HTTPException(400, "Invalid Tesla OAuth callback — state mismatch or missing code")

    user_id = pending["user_id"]
    code_verifier = pending["code_verifier"]

    try:
        async with httpx.AsyncClient(timeout=15) as c:
            r = await c.post(
                f"{_TESLA_AUTH_BASE}/token",
                json={
                    "grant_type": "authorization_code",
                    "client_id": TESLA_CLIENT_ID,
                    "client_secret": TESLA_CLIENT_SECRET,
                    "code": code,
                    "redirect_uri": f"{APP_URL}/auth/tesla/callback",
                    "code_verifier": code_verifier,
                },
            )
            r.raise_for_status()
            tokens = r.json()
    except Exception as e:
        raise HTTPException(502, f"Tesla token exchange failed: {e}") from e

    fleet_refresh = tokens.get("refresh_token", "")
    state = await _get_user_state(user_id)
    config = state["config"]
    current_method = config.get("tesla_method", "")
    new_method = "both" if current_method == "unofficial" and config.get("tesla_refresh_token") else "fleet"

    async with _get_user_lock(user_id):
        config["tesla_fleet_refresh_token"] = fleet_refresh
        config["tesla_method"] = new_method
        async with _pool().acquire() as conn:
            await conn.execute(
                "UPDATE user_configs SET tesla_fleet_refresh_token = $2, tesla_method = $3 WHERE user_id = $1",
                user_id, fleet_refresh, new_method,
            )
    _tesla_tokens.pop(user_id, None)

    return RedirectResponse("/?tesla_connected=1", status_code=303)


@fast_app.post("/api/tesla/disconnect")
async def api_tesla_disconnect(request: Request):
    user_id = _get_current_user(request)
    if not user_id:
        raise HTTPException(401)

    data = await request.json()
    which = data.get("which", "all")

    state = await _get_user_state(user_id)
    config = state["config"]

    async with _get_user_lock(user_id):
        if which in ("unofficial", "all"):
            config["tesla_refresh_token"] = ""
        if which in ("fleet", "all"):
            config["tesla_fleet_refresh_token"] = ""

        has_unofficial = bool(config.get("tesla_refresh_token"))
        has_fleet = bool(config.get("tesla_fleet_refresh_token"))
        if has_unofficial and has_fleet:
            config["tesla_method"] = "both"
        elif has_unofficial:
            config["tesla_method"] = "unofficial"
        elif has_fleet:
            config["tesla_method"] = "fleet"
        else:
            config["tesla_method"] = ""

        async with _pool().acquire() as conn:
            await conn.execute(
                "UPDATE user_configs SET tesla_refresh_token = $2, tesla_fleet_refresh_token = $3, tesla_method = $4 WHERE user_id = $1",
                user_id,
                config["tesla_refresh_token"],
                config["tesla_fleet_refresh_token"],
                config["tesla_method"],
            )
    _tesla_tokens.pop(user_id, None)

    return {"ok": True, "tesla_configured": _tesla_configured(config), "tesla_method": config.get("tesla_method", "")}


@fast_app.get("/api/messages")
async def api_messages(request: Request):
    user_id = _get_current_user(request)
    if not user_id:
        raise HTTPException(401)
    async with _pool().acquire() as conn:
        rows = await conn.fetch(
            "SELECT id, sender, body, important, reason, received_at FROM phone_messages WHERE user_id = $1 ORDER BY received_at DESC LIMIT 50",
            user_id,
        )
    return [
        {
            "id": r["id"],
            "sender": r["sender"],
            "body": r["body"],
            "important": r["important"],
            "reason": r["reason"],
            "received_at": r["received_at"].isoformat(),
        }
        for r in rows
    ]


# ─── LLM STREAMING ───────────────────────────────────────────────────────────
def _build_system_prompt(config: dict) -> str:
    system = JARVIS_SYSTEM
    ctx = _location_context
    if ctx:
        parts = []
        if ctx.get("city"):
            loc = ctx["city"]
            if ctx.get("region"):
                loc += f", {ctx['region']}"
            parts.append(f"location: {loc}")
        if ctx.get("temp_f") is not None:
            parts.append(f"temperature: {ctx['temp_f']}°F")
        if ctx.get("condition"):
            parts.append(f"conditions: {ctx['condition']}")
        if ctx.get("pressure_kpa"):
            parts.append(f"pressure: {ctx['pressure_kpa']} kPa")
        if parts:
            system += "\n\nCURRENT ENVIRONMENT — use naturally when relevant, don't announce it unprompted:\n" + ", ".join(parts) + "."
    if _ha_configured(config):
        system += (
            "\n\nHOME AUTOMATION — you are connected to Home Assistant via tools. "
            "Use get_ha_states to check device states and call_ha_service to control "
            "devices, run scripts, and trigger automations. When given a home control "
            "command, use your tools and then confirm briefly in JARVIS voice."
        )
    if _myq_configured(config):
        system += (
            "\n\nGARAGE DOOR — you are connected to the MyQ Chamberlain smart garage. "
            "Use get_garage_status to check whether the door is open or closed, "
            "and set_garage_door to open or close it on command."
        )
    if _tesla_configured(config):
        system += (
            "\n\nTESLA — you are connected to the user's Tesla vehicle via tools. "
            "Use get_vehicle_status to check battery, range, lock state, and climate. "
            "Use lock_vehicle, unlock_vehicle, set_climate, start_charging, stop_charging, "
            "honk_horn, flash_lights, and actuate_trunk to control the vehicle. "
            "Commands auto-wake the car, which may take up to 30 seconds — mention this if relevant."
        )
    return system


async def _openai_stream_async(client, model, messages, max_out=500, **extra_kwargs):
    last = None
    for extra in ({"max_tokens": max_out}, {"max_completion_tokens": max_out}, {}):
        try:
            return await client.chat.completions.create(model=model, messages=messages, stream=True, **extra, **extra_kwargs)
        except Exception as e:
            last = e
            if any(
                x in str(e).lower()
                for x in (
                    "max_tokens",
                    "max_completion_tokens",
                    "unsupported",
                    "temperature",
                )
            ):
                continue
            raise
    assert last is not None
    raise last


_SENT_RE = re.compile(r'(.+?[.!?…]+["\')\]]?\s)', re.DOTALL)


def _split_sentences(buf):
    out = []
    while True:
        m = _SENT_RE.match(buf)
        if not m:
            break
        out.append(m.group(1).strip())
        buf = buf[m.end() :]
    return out, buf


async def _stream_reply(state: dict, on_text):
    provider = state["provider"]
    config = state["config"]
    client = state["client"]
    model = config.get("model") or DEFAULT_MODELS.get(provider, "")
    system = _build_system_prompt(config)
    ha_tools = _get_ha_tools(config, provider) + _get_myq_tools(config, provider) + _get_tesla_tools(config, provider)
    local_msgs = list(state["conversation"])

    for _ in range(4):
        if provider == "anthropic":
            full = ""
            stream_kwargs = dict(
                model=model,
                max_tokens=500,
                system=[
                    {
                        "type": "text",
                        "text": system,
                        "cache_control": {"type": "ephemeral"},
                    }
                ],
                messages=local_msgs,
            )
            if ha_tools:
                stream_kwargs["tools"] = ha_tools
            async with client.messages.stream(**stream_kwargs) as stream:
                async for delta in stream.text_stream:
                    full += delta
                    await on_text(delta)
                final = await stream.get_final_message()
            if final.stop_reason != "tool_use" or not ha_tools:
                return full
            results = []
            for block in final.content:
                if block.type == "tool_use":
                    result = await _execute_ha_tool(config, block.name, dict(block.input), state.get("user_id", ""))
                    results.append(
                        {
                            "type": "tool_result",
                            "tool_use_id": block.id,
                            "content": result,
                        }
                    )
            local_msgs.append({"role": "assistant", "content": final.content})
            local_msgs.append({"role": "user", "content": results})

        else:
            msgs = [{"role": "system", "content": system}] + local_msgs
            tool_calls_acc = {}
            finish_reason = None
            full = ""
            stream_extra = {"tools": ha_tools} if ha_tools else {}
            stream = await _openai_stream_async(client, model, msgs, **stream_extra)
            async for chunk in stream:
                try:
                    choice = chunk.choices[0]
                except (AttributeError, IndexError):
                    continue
                if choice.finish_reason:
                    finish_reason = choice.finish_reason
                delta = choice.delta
                if delta.content:
                    full += delta.content
                    await on_text(delta.content)
                if getattr(delta, "tool_calls", None):
                    for tc in delta.tool_calls:
                        idx = tc.index
                        if idx not in tool_calls_acc:
                            tool_calls_acc[idx] = {
                                "id": tc.id or "",
                                "name": (tc.function.name or "") if tc.function else "",
                                "args": "",
                            }
                        if tc.function and tc.function.arguments:
                            tool_calls_acc[idx]["args"] += tc.function.arguments
                        if tc.id and not tool_calls_acc[idx]["id"]:
                            tool_calls_acc[idx]["id"] = tc.id
                        if tc.function and tc.function.name and not tool_calls_acc[idx]["name"]:
                            tool_calls_acc[idx]["name"] = tc.function.name
            if finish_reason != "tool_calls" or not ha_tools:
                return full
            tc_list = []
            tool_msgs = []
            for acc in tool_calls_acc.values():
                args = json.loads(acc["args"] or "{}")
                result = await _execute_ha_tool(config, acc["name"], args, state.get("user_id", ""))
                tc_list.append(
                    {
                        "id": acc["id"],
                        "type": "function",
                        "function": {"name": acc["name"], "arguments": acc["args"]},
                    }
                )
                tool_msgs.append({"role": "tool", "tool_call_id": acc["id"], "content": result})
            local_msgs.append({"role": "assistant", "content": None, "tool_calls": tc_list})
            local_msgs.extend(tool_msgs)

    return full


async def _process_message(sid: str, text: str):
    user_id = _sid_to_user.get(sid)
    if not user_id:
        return

    state = await _get_user_state(user_id)

    if not _user_configured(state):
        await sio.emit("need_setup", {}, to=sid)
        await sio.emit("status", {"state": "idle"}, to=sid)
        return

    await sio.emit("status", {"state": "thinking"}, to=sid)

    state["conversation"].append({"role": "user", "content": text})
    await _db_append_message(user_id, "user", text)
    if len(state["conversation"]) > MAX_HISTORY:
        state["conversation"] = state["conversation"][-MAX_HISTORY:]

    seq = 0
    sent_buf = ""
    first = True

    async def on_text(delta):
        nonlocal sent_buf, seq, first
        if first:
            await sio.emit("status", {"state": "speaking"}, to=sid)
            first = False
        sent_buf += delta
        sents, sent_buf = _split_sentences(sent_buf)
        for s in sents:
            if s:
                await sio.emit("speak_sentence", {"text": s, "seq": seq}, to=sid)
                seq += 1

    try:
        full = await _stream_reply(state, on_text)
        if sent_buf.strip():
            await sio.emit("speak_sentence", {"text": sent_buf.strip(), "seq": seq}, to=sid)
        reply = full.strip() or "…"
        state["conversation"].append({"role": "assistant", "content": reply})
        await _db_append_message(user_id, "assistant", reply)
        if len(state["conversation"]) > MAX_HISTORY:
            state["conversation"] = state["conversation"][-MAX_HISTORY:]
        await sio.emit("response_done", {"text": reply}, to=sid)
        await sio.emit("status", {"state": "idle"}, to=sid)

    except Exception as e:
        print(f"[BRAIN] {e}", flush=True)
        low = str(e).lower()
        if "authentication" in low or "401" in low:
            msg = "My key's been refused, sir — best re-enter it."
            await sio.emit("need_setup", {}, to=sid)
        elif "overloaded" in low or "429" in low or "rate" in low or "529" in low:
            msg = "Briefly overloaded, sir — worth trying again in a moment."
        else:
            msg = "Something's gone wrong on my end, sir. Do try that again."
        conv = state["conversation"]
        if conv and conv[-1].get("role") == "user":
            conv.pop()
            await _db_clear_conversation(user_id)
            for msg_entry in conv:
                await _db_append_message(user_id, msg_entry["role"], msg_entry["content"])
        await sio.emit("speak_sentence", {"text": msg, "seq": 0}, to=sid)
        await sio.emit("response_done", {"text": msg}, to=sid)
        await sio.emit("status", {"state": "idle"}, to=sid)


# ─── SOCKET.IO EVENTS ────────────────────────────────────────────────────────
@sio.on("connect")
async def on_connect(sid, environ, auth=None):
    user_id = _get_user_from_environ(environ)
    if not user_id:
        raise ConnectionRefusedError("unauthorized")
    _sid_to_user[sid] = user_id
    state = await _get_user_state(user_id)
    await sio.emit("status", {"state": "idle"}, to=sid)
    await sio.emit(
        "config_state",
        {"configured": _user_configured(state), "role": state.get("role", "user")},
        to=sid,
    )


@sio.on("disconnect")
async def on_disconnect(sid):
    _sid_to_user.pop(sid, None)


@sio.on("user_message")
async def on_user_message(sid, data):
    text = ((data or {}).get("text") or "").strip()
    if text:
        asyncio.create_task(_process_message(sid, text))


@sio.on("start_meeting")
async def on_start_meeting(sid, data=None):
    user_id = _sid_to_user.get(sid)
    if not user_id:
        return
    if user_id in _active_meetings:
        await sio.emit("meeting_error", {"error": "A meeting is already active."}, to=sid)
        return
    meeting_id = await _db_create_meeting(user_id)
    _active_meetings[user_id] = {"meeting_id": meeting_id, "segments": []}
    await sio.emit("meeting_started", {"meeting_id": meeting_id}, to=sid)


@sio.on("meeting_audio_chunk")
async def on_meeting_audio_chunk(sid, data):
    user_id = _sid_to_user.get(sid)
    if not user_id or user_id not in _active_meetings:
        return
    if not data:
        return
    tmp = None
    try:
        audio_bytes = bytes(data) if not isinstance(data, bytes) else data
        with tempfile.NamedTemporaryFile(suffix=".webm", delete=False) as f:
            f.write(audio_bytes)
            tmp = f.name

        def _run_meeting_stt():
            m = _get_whisper()
            segs, _ = m.transcribe(
                tmp,
                language="en",
                beam_size=1,
                vad_filter=True,
                no_speech_threshold=0.6,
            )
            return " ".join(s.text for s in segs).strip()

        async with _whisper_lock:
            text = await asyncio.to_thread(_run_meeting_stt)

        if text:
            meeting = _active_meetings.get(user_id)
            if meeting:
                meeting["segments"].append(text)
                await _db_append_transcript_segment(meeting["meeting_id"], text)
                full = " ".join(meeting["segments"])
                await sio.emit(
                    "meeting_transcript_update",
                    {"segment": text, "full": full},
                    to=sid,
                )
    except Exception as e:
        print(f"[MEETING] chunk error: {e}", flush=True)
    finally:
        if tmp:
            try:
                os.unlink(tmp)
            except OSError:
                pass


@sio.on("end_meeting")
async def on_end_meeting(sid, data=None):
    user_id = _sid_to_user.get(sid)
    if not user_id or user_id not in _active_meetings:
        return

    # Wait for any in-flight chunk transcription to complete before finalizing
    async with _whisper_lock:
        pass

    meeting = _active_meetings.pop(user_id, None)
    if not meeting:
        return

    meeting_id = meeting["meeting_id"]
    transcript = " ".join(meeting["segments"]).strip()

    if not transcript:
        notes = "No speech was detected during this meeting."
        await _db_finalize_meeting(meeting_id, notes)
        await sio.emit(
            "meeting_notes_ready",
            {"meeting_id": meeting_id, "transcript": "", "notes": notes},
            to=sid,
        )
        return

    state = _user_states.get(user_id)
    notes = "Notes unavailable — no LLM configured."
    if state and _user_configured(state):
        try:
            notes = await _generate_meeting_notes(state, transcript)
        except Exception as e:
            print(f"[MEETING] notes generation error: {e}", flush=True)
            notes = f"Transcript captured but notes generation failed: {e}"

    await _db_finalize_meeting(meeting_id, notes)
    await sio.emit(
        "meeting_notes_ready",
        {"meeting_id": meeting_id, "transcript": transcript, "notes": notes},
        to=sid,
    )


@sio.on("reset_chat")
async def on_reset_chat(sid, data=None):
    user_id = _sid_to_user.get(sid)
    if not user_id:
        return
    state = _user_states.get(user_id)
    if state:
        state["conversation"] = []
    await _db_clear_conversation(user_id)


# ─── BACKGROUND TASKS ────────────────────────────────────────────────────────
async def _telemetry_loop():
    try:
        import psutil
        import time
    except Exception:
        print(
            "[TELEMETRY] psutil not installed - HUD panels will show placeholders.",
            flush=True,
        )
        return
    boot = psutil.boot_time()
    last_net = psutil.net_io_counters()
    last_t = asyncio.get_event_loop().time()
    psutil.cpu_percent(interval=None)
    while True:
        await asyncio.sleep(1.5)
        try:
            now = asyncio.get_event_loop().time()
            net = psutil.net_io_counters()
            dt = max(now - last_t, 0.1)
            down = (net.bytes_recv - last_net.bytes_recv) * 8 / 1e6 / dt
            up = (net.bytes_sent - last_net.bytes_sent) * 8 / 1e6 / dt
            pps = int(((net.packets_recv + net.packets_sent) - (last_net.packets_recv + last_net.packets_sent)) / dt)
            last_net, last_t = net, now
            await sio.emit(
                "hud_update",
                {
                    "cpu": round(psutil.cpu_percent(interval=None)),
                    "ram": round(psutil.virtual_memory().percent),
                    "uptime_h": round((time.time() - boot) / 3600, 2),
                    "net_down_mbps": round(max(down, 0), 1),
                    "net_up_mbps": round(max(up, 0), 1),
                    "net_pps": max(pps, 0),
                    "infer_active": False,
                },
            )
        except Exception:
            pass


async def _weather_loop():
    while True:
        try:
            async with httpx.AsyncClient(timeout=8) as client:
                loc_r = await client.get(
                    "http://ip-api.com/json/",
                    headers={"User-Agent": "JARVIS-Starter/1.0"},
                )
                loc = loc_r.json()
                lat, lon = loc.get("lat"), loc.get("lon")
                if lat is not None and lon is not None:
                    wx_r = await client.get(
                        f"https://api.open-meteo.com/v1/forecast?latitude={lat}&longitude={lon}&current=temperature_2m,surface_pressure,weather_code&temperature_unit=fahrenheit",
                        headers={"User-Agent": "JARVIS-Starter/1.0"},
                    )
                    cur = wx_r.json().get("current", {})
                    code = cur.get("weather_code", 0)
                    cond = {
                        0: "Clear",
                        1: "Mainly clear",
                        2: "Partly cloudy",
                        3: "Overcast",
                        45: "Fog",
                        48: "Fog",
                        51: "Drizzle",
                        61: "Rain",
                        63: "Rain",
                        65: "Heavy rain",
                        71: "Snow",
                        73: "Snow",
                        80: "Showers",
                        95: "Thunderstorm",
                    }.get(code, "—")
                    weather_data = {
                        "temp_f": (round(cur["temperature_2m"]) if cur.get("temperature_2m") is not None else None),
                        "pressure_kpa": (round(cur["surface_pressure"] / 10, 1) if cur.get("surface_pressure") else None),
                        "city": loc.get("city", "—"),
                        "region": loc.get("region", ""),
                        "condition": cond,
                    }
                    _location_context.update(weather_data)
                    await sio.emit("weather_update", weather_data)
        except Exception:
            pass
        await asyncio.sleep(600)


async def _meeting_cleanup_loop():
    while True:
        await asyncio.sleep(3600)  # check every hour
        try:
            if _db_pool:
                async with _pool().acquire() as conn:
                    result = await conn.execute("DELETE FROM meetings WHERE created_at < NOW() - INTERVAL '48 hours'")
                if result != "DELETE 0":
                    print(f"[MEETING] Cleanup: {result}", flush=True)
        except Exception as e:
            print(f"[MEETING] Cleanup error: {e}", flush=True)
