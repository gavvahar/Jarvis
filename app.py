"""
app.py — J.A.R.V.I.S. Starter Kit backend (FastAPI + python-socketio).

Multi-user: each user authenticates via Authentik (OIDC) and gets their own
config and conversation history stored in PostgreSQL.

Three providers:
  • anthropic         — Claude, via AsyncAnthropic
  • openai            — GPT models, via AsyncOpenAI
  • openai_compatible — any OpenAI-compatible endpoint (Ollama, OpenRouter, …)
"""

import json, os, re, asyncio, secrets, tempfile, urllib.parse, asyncpg, httpx
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
DATABASE_URL = os.environ.get(
    "DATABASE_URL", "postgresql://jarvis:jarvis@postgres/jarvis"
)
AUTHENTIK_URL = os.environ.get("AUTHENTIK_URL", "").rstrip("/")
_OIDC_APP_SLUG = os.environ.get("OIDC_APP_SLUG", "").strip()
OIDC_DISCOVERY_URL = os.environ.get("OIDC_DISCOVERY_URL", "") or (
    f"{AUTHENTIK_URL}/application/o/{_OIDC_APP_SLUG}/.well-known/openid-configuration"
    if AUTHENTIK_URL and _OIDC_APP_SLUG
    else ""
)
OIDC_CLIENT_ID = os.environ.get("OIDC_CLIENT_ID", "")
OIDC_CLIENT_SECRET = os.environ.get("OIDC_CLIENT_SECRET", "")
APP_URL = os.environ.get("APP_URL", "http://localhost:5000").rstrip("/")
SECRET_KEY = os.environ.get("SECRET_KEY", "change-me")
OIDC_ADMIN_GROUP = os.environ.get("OIDC_ADMIN_GROUP", "jarvis-admins")

# ─── DB ───────────────────────────────────────────────────────────────────────
_db_pool: asyncpg.Pool | None = None

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
"""


async def _db_init():
    global _db_pool
    _db_pool = await asyncpg.create_pool(DATABASE_URL, min_size=2, max_size=10)
    async with _db_pool.acquire() as conn:
        await conn.execute(_SCHEMA)


async def _db_ensure_user(user_id: str, email: str, role: str):
    async with _db_pool.acquire() as conn:
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
    async with _db_pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT role, provider, api_key, model, base_url, ha_url, ha_token "
            "FROM user_configs WHERE user_id = $1",
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
        }
    return dict(row)


async def _db_save_config(user_id: str, config: dict):
    async with _db_pool.acquire() as conn:
        await conn.execute(
            """
            UPDATE user_configs
            SET provider=$2, api_key=$3, model=$4, base_url=$5,
                ha_url=$6, ha_token=$7, updated_at=NOW()
            WHERE user_id=$1
            """,
            user_id,
            config["provider"],
            config["api_key"],
            config["model"],
            config["base_url"],
            config["ha_url"],
            config["ha_token"],
        )


async def _db_load_conversation(user_id: str) -> list:
    async with _db_pool.acquire() as conn:
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
    async with _db_pool.acquire() as conn:
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
    async with _db_pool.acquire() as conn:
        await conn.execute("DELETE FROM conversations WHERE user_id = $1", user_id)


async def _db_create_meeting(user_id: str) -> int:
    async with _db_pool.acquire() as conn:
        row = await conn.fetchrow(
            "INSERT INTO meetings (user_id) VALUES ($1) RETURNING id", user_id
        )
    return row["id"]


async def _db_append_transcript_segment(meeting_id: int, segment: str):
    async with _db_pool.acquire() as conn:
        await conn.execute(
            "UPDATE meetings SET transcript = transcript || $2 WHERE id = $1",
            meeting_id,
            " " + segment,
        )


async def _db_finalize_meeting(meeting_id: int, notes: str):
    async with _db_pool.acquire() as conn:
        await conn.execute(
            "UPDATE meetings SET ended_at = NOW(), notes = $2 WHERE id = $1",
            meeting_id,
            notes,
        )


# ─── AUTH ─────────────────────────────────────────────────────────────────────
_signer: URLSafeTimedSerializer | None = None
_oidc_config: dict | None = None


async def _fetch_oidc_config():
    global _oidc_config
    if not OIDC_DISCOVERY_URL:
        print(
            "[AUTH] OIDC_DISCOVERY_URL not set — authentication disabled.", flush=True
        )
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
    return _signer.dumps(user_id)


def _verify_session(value: str) -> str | None:
    try:
        return _signer.loads(value, max_age=86400 * 30)
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
        client = _build_client(
            provider, config.get("api_key", ""), config.get("base_url", "")
        )
        _user_states[user_id] = {
            "config": config,
            "client": client,
            "provider": provider,
            "conversation": conversation,
            "role": config.get("role", "user"),
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
        "description": (
            "Call a Home Assistant service to control a device, run a script, "
            "or trigger an automation."
        ),
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
            "description": (
                "Call a Home Assistant service to control a device, run a script, "
                "or trigger an automation."
            ),
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
]


def _ha_configured(config: dict) -> bool:
    return bool(config.get("ha_url") and config.get("ha_token"))


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


async def _ha_call_service(
    config: dict, domain, service, entity_id=None, service_data=None
):
    url = config["ha_url"].rstrip("/") + f"/api/services/{domain}/{service}"
    payload = dict(service_data or {})
    if entity_id:
        payload["entity_id"] = entity_id
    async with httpx.AsyncClient(timeout=8) as c:
        r = await c.post(url, headers=_ha_headers(config), json=payload)
    return (
        "Done."
        if r.status_code in (200, 201)
        else f"HA returned {r.status_code}: {r.text[:120]}"
    )


async def _execute_ha_tool(config: dict, name, args):
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
        return f"Unknown tool: {name}"
    except Exception as e:
        return f"Error: {e}"


# ─── CONFIG VALIDATION ────────────────────────────────────────────────────────
def _openai_create_sync(client, model, messages, stream, max_out=500):
    last = None
    for extra in ({"max_tokens": max_out}, {"max_completion_tokens": max_out}, {}):
        try:
            return client.chat.completions.create(
                model=model, messages=messages, stream=stream, **extra
            )
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
        if (
            "authentication" in low
            or "401" in low
            or ("invalid" in low and "key" in low)
        ):
            return False, "That key was rejected. Check it and try again."
        if "404" in low or "not_found" in low or ("model" in low and "exist" in low):
            return False, f"The model '{model}' wasn't found for this key/provider."
        if (
            "credit" in low
            or "billing" in low
            or "quota" in low
            or "insufficient" in low
        ):
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
            if any(
                x in str(e).lower()
                for x in ("max_tokens", "max_completion_tokens", "unsupported")
            ):
                continue
            raise
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
            response.set_cookie(
                "jarvis_session", _sign_session(user_id), **_SESSION_COOKIE_OPTS
            )
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
        raise HTTPException(
            400, "Invalid OAuth2 callback — state mismatch or missing code"
        )

    try:
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.post(
                _oidc_config["token_endpoint"],
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
                _oidc_config["userinfo_endpoint"],
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
    response.set_cookie(
        "jarvis_session", _sign_session(user_id), **_SESSION_COOKIE_OPTS
    )
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


@fast_app.get("/api/meetings")
async def api_meetings(request: Request):
    user_id = _get_current_user(request)
    if not user_id:
        raise HTTPException(401)
    async with _db_pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT id, started_at, ended_at, notes FROM meetings "
            "WHERE user_id = $1 ORDER BY started_at DESC LIMIT 20",
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
    async with _db_pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT id, started_at, ended_at, transcript, notes FROM meetings "
            "WHERE id = $1 AND user_id = $2",
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
            system += (
                "\n\nCURRENT ENVIRONMENT — use naturally when relevant, don't announce it unprompted:\n"
                + ", ".join(parts)
                + "."
            )
    if _ha_configured(config):
        system += (
            "\n\nHOME AUTOMATION — you are connected to Home Assistant via tools. "
            "Use get_ha_states to check device states and call_ha_service to control "
            "devices, run scripts, and trigger automations. When given a home control "
            "command, use your tools and then confirm briefly in JARVIS voice."
        )
    return system


async def _openai_stream_async(client, model, messages, max_out=500, **extra_kwargs):
    last = None
    for extra in ({"max_tokens": max_out}, {"max_completion_tokens": max_out}, {}):
        try:
            return await client.chat.completions.create(
                model=model, messages=messages, stream=True, **extra, **extra_kwargs
            )
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
    ha_tools = _get_ha_tools(config, provider)
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
                    result = await _execute_ha_tool(
                        config, block.name, dict(block.input)
                    )
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
                        if (
                            tc.function
                            and tc.function.name
                            and not tool_calls_acc[idx]["name"]
                        ):
                            tool_calls_acc[idx]["name"] = tc.function.name
            if finish_reason != "tool_calls" or not ha_tools:
                return full
            tc_list = []
            tool_msgs = []
            for acc in tool_calls_acc.values():
                args = json.loads(acc["args"] or "{}")
                result = await _execute_ha_tool(config, acc["name"], args)
                tc_list.append(
                    {
                        "id": acc["id"],
                        "type": "function",
                        "function": {"name": acc["name"], "arguments": acc["args"]},
                    }
                )
                tool_msgs.append(
                    {"role": "tool", "tool_call_id": acc["id"], "content": result}
                )
            local_msgs.append(
                {"role": "assistant", "content": None, "tool_calls": tc_list}
            )
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
            await sio.emit(
                "speak_sentence", {"text": sent_buf.strip(), "seq": seq}, to=sid
            )
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
                await _db_append_message(
                    user_id, msg_entry["role"], msg_entry["content"]
                )
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
        await sio.emit(
            "meeting_error", {"error": "A meeting is already active."}, to=sid
        )
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
            pps = int(
                (
                    (net.packets_recv + net.packets_sent)
                    - (last_net.packets_recv + last_net.packets_sent)
                )
                / dt
            )
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
                        f"https://api.open-meteo.com/v1/forecast"
                        f"?latitude={lat}&longitude={lon}"
                        f"&current=temperature_2m,surface_pressure,weather_code"
                        f"&temperature_unit=fahrenheit",
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
                        "temp_f": (
                            round(cur["temperature_2m"])
                            if cur.get("temperature_2m") is not None
                            else None
                        ),
                        "pressure_kpa": (
                            round(cur["surface_pressure"] / 10, 1)
                            if cur.get("surface_pressure")
                            else None
                        ),
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
                async with _db_pool.acquire() as conn:
                    result = await conn.execute(
                        "DELETE FROM meetings WHERE created_at < NOW() - INTERVAL '48 hours'"
                    )
                if result != "DELETE 0":
                    print(f"[MEETING] Cleanup: {result}", flush=True)
        except Exception as e:
            print(f"[MEETING] Cleanup error: {e}", flush=True)
