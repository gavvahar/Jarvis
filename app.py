"""
app.py — J.A.R.V.I.S. backend (FastAPI + python-socketio).

Multi-user: each user authenticates via Authentik (OIDC) and gets their own
config and conversation history stored in PostgreSQL.

Three providers:
  • anthropic         — Claude, via AsyncAnthropic
  • openai            — GPT models, via AsyncOpenAI
  • openai_compatible — any OpenAI-compatible endpoint (Ollama, OpenRouter, …)
"""

import json, os, re, asyncio, secrets, tempfile, urllib.parse, asyncpg, httpx, datetime, hashlib, base64, pathlib, uuid
import xml.etree.ElementTree as ET


from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException, Request, File, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
import socketio
from dotenv import load_dotenv

from personality import JARVIS_SYSTEM

try:
    import jwt
except ImportError:
    jwt = None  # type: ignore[assignment]

try:
    import librosa as _librosa
    import numpy as _np

    _VOICE_ID_OK = True
except ImportError:
    _VOICE_ID_OK = False

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
SPOTIFY_CLIENT_ID = os.environ.get("SPOTIFY_CLIENT_ID", "")
SPOTIFY_CLIENT_SECRET = os.environ.get("SPOTIFY_CLIENT_SECRET", "")
APPLE_MUSIC_TEAM_ID = os.environ.get("APPLE_MUSIC_TEAM_ID", "")
APPLE_MUSIC_KEY_ID = os.environ.get("APPLE_MUSIC_KEY_ID", "")
APPLE_MUSIC_PRIVATE_KEY = os.environ.get("APPLE_MUSIC_PRIVATE_KEY", "")

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
ALTER TABLE user_configs ADD COLUMN IF NOT EXISTS spotify_refresh_token TEXT NOT NULL DEFAULT '';
ALTER TABLE user_configs ADD COLUMN IF NOT EXISTS spotify_access_token TEXT NOT NULL DEFAULT '';
ALTER TABLE user_configs ADD COLUMN IF NOT EXISTS spotify_token_expiry DOUBLE PRECISION NOT NULL DEFAULT 0;
ALTER TABLE user_configs ADD COLUMN IF NOT EXISTS apple_music_user_token TEXT NOT NULL DEFAULT '';
ALTER TABLE user_configs ADD COLUMN IF NOT EXISTS apple_music_storefront TEXT NOT NULL DEFAULT 'us';
ALTER TABLE user_configs ADD COLUMN IF NOT EXISTS calendar_url TEXT NOT NULL DEFAULT '';
ALTER TABLE user_configs ADD COLUMN IF NOT EXISTS calendar_username TEXT NOT NULL DEFAULT '';
ALTER TABLE user_configs ADD COLUMN IF NOT EXISTS calendar_password TEXT NOT NULL DEFAULT '';
ALTER TABLE user_configs ADD COLUMN IF NOT EXISTS contacts_url TEXT NOT NULL DEFAULT '';
ALTER TABLE user_configs ADD COLUMN IF NOT EXISTS contacts_username TEXT NOT NULL DEFAULT '';
ALTER TABLE user_configs ADD COLUMN IF NOT EXISTS contacts_password TEXT NOT NULL DEFAULT '';
ALTER TABLE user_configs ADD COLUMN IF NOT EXISTS display_name TEXT NOT NULL DEFAULT '';
ALTER TABLE user_configs ADD COLUMN IF NOT EXISTS voice_embedding JSONB;
ALTER TABLE user_configs ADD COLUMN IF NOT EXISTS is_kid_safe BOOLEAN NOT NULL DEFAULT FALSE;

CREATE TABLE IF NOT EXISTS shared_lists (
    id          BIGSERIAL PRIMARY KEY,
    name        TEXT NOT NULL,
    items       JSONB NOT NULL DEFAULT '[]',
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_shared_lists_name ON shared_lists (name);

INSERT INTO shared_lists (name, items) VALUES ('shopping', '[]'), ('todo', '[]') ON CONFLICT (name) DO NOTHING;

CREATE TABLE IF NOT EXISTS timers (
    id          BIGSERIAL PRIMARY KEY,
    user_id     TEXT NOT NULL REFERENCES user_configs(user_id) ON DELETE CASCADE,
    label       TEXT NOT NULL DEFAULT 'Timer',
    fire_at     TIMESTAMPTZ NOT NULL,
    fired       BOOLEAN NOT NULL DEFAULT FALSE,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_timers_user ON timers (user_id, fire_at);

CREATE TABLE IF NOT EXISTS reminders (
    id                  BIGSERIAL PRIMARY KEY,
    user_id             TEXT NOT NULL REFERENCES user_configs(user_id) ON DELETE CASCADE,
    text                TEXT NOT NULL,
    fire_at             TIMESTAMPTZ NOT NULL,
    recurring_minutes   INTEGER,
    active              BOOLEAN NOT NULL DEFAULT TRUE,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_reminders_user ON reminders (user_id, fire_at);

CREATE TABLE IF NOT EXISTS routines (
    id              BIGSERIAL PRIMARY KEY,
    user_id         TEXT NOT NULL REFERENCES user_configs(user_id) ON DELETE CASCADE,
    name            TEXT NOT NULL,
    trigger_phrases JSONB NOT NULL DEFAULT '[]',
    steps           JSONB NOT NULL DEFAULT '[]',
    active          BOOLEAN NOT NULL DEFAULT TRUE,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_routines_user ON routines (user_id);

CREATE TABLE IF NOT EXISTS device_alert_rules (
    id               BIGSERIAL PRIMARY KEY,
    user_id          TEXT NOT NULL REFERENCES user_configs(user_id) ON DELETE CASCADE,
    name             TEXT NOT NULL,
    entity_id        TEXT NOT NULL,
    condition        TEXT NOT NULL,
    value            TEXT NOT NULL DEFAULT '',
    message          TEXT NOT NULL,
    cooldown_minutes INTEGER NOT NULL DEFAULT 30,
    last_fired       TIMESTAMPTZ,
    active           BOOLEAN NOT NULL DEFAULT TRUE,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_device_alerts_user ON device_alert_rules (user_id);

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
            "SELECT role, provider, api_key, model, base_url, ha_url, ha_token, myq_email, myq_password, tesla_method, tesla_refresh_token, tesla_fleet_refresh_token, spotify_refresh_token, spotify_access_token, spotify_token_expiry, apple_music_user_token, apple_music_storefront, calendar_url, calendar_username, calendar_password, contacts_url, contacts_username, contacts_password, display_name, voice_embedding, is_kid_safe FROM user_configs WHERE user_id = $1",
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
            "spotify_refresh_token": "",
            "spotify_access_token": "",
            "spotify_token_expiry": 0.0,
            "apple_music_user_token": "",
            "apple_music_storefront": "us",
            "calendar_url": "",
            "calendar_username": "",
            "calendar_password": "",
            "contacts_url": "",
            "contacts_username": "",
            "contacts_password": "",
            "display_name": "",
            "voice_embedding": None,
            "is_kid_safe": False,
        }
    return dict(row)


async def _db_save_config(user_id: str, config: dict):
    async with _pool().acquire() as conn:
        await conn.execute(
            "INSERT INTO user_configs (user_id, email, role) VALUES ($1, '', 'user') ON CONFLICT (user_id) DO NOTHING",
            user_id,
        )
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


async def _db_save_voice_embedding(user_id: str, embedding: list) -> None:
    async with _pool().acquire() as conn:
        await conn.execute(
            "UPDATE user_configs SET voice_embedding = $2 WHERE user_id = $1",
            user_id,
            json.dumps(embedding),
        )


async def _db_clear_voice_embedding(user_id: str) -> None:
    async with _pool().acquire() as conn:
        await conn.execute("UPDATE user_configs SET voice_embedding = NULL WHERE user_id = $1", user_id)


async def _db_get_all_voice_embeddings() -> dict:
    async with _pool().acquire() as conn:
        rows = await conn.fetch("SELECT user_id, voice_embedding, display_name, is_kid_safe FROM user_configs WHERE voice_embedding IS NOT NULL")
    result = {}
    for row in rows:
        emb = row["voice_embedding"]
        if emb:
            parsed = json.loads(emb) if isinstance(emb, str) else emb
            result[row["user_id"]] = (parsed, row["display_name"] or row["user_id"][:8], row["is_kid_safe"])
    return result


async def _db_set_kid_safe(user_id: str, value: bool) -> None:
    async with _pool().acquire() as conn:
        await conn.execute("UPDATE user_configs SET is_kid_safe = $2 WHERE user_id = $1", user_id, value)


async def _db_set_display_name(user_id: str, name: str) -> None:
    async with _pool().acquire() as conn:
        await conn.execute("UPDATE user_configs SET display_name = $2 WHERE user_id = $1", user_id, name)


async def _db_save_pim_config(
    user_id: str,
    calendar_url: str,
    calendar_username: str,
    calendar_password: str,
    contacts_url: str,
    contacts_username: str,
    contacts_password: str,
) -> None:
    async with _pool().acquire() as conn:
        await conn.execute(
            "INSERT INTO user_configs (user_id, email, role) VALUES ($1, '', 'user') ON CONFLICT (user_id) DO NOTHING",
            user_id,
        )
        await conn.execute(
            """
            UPDATE user_configs
            SET calendar_url=$2, calendar_username=$3, calendar_password=$4,
                contacts_url=$5, contacts_username=$6, contacts_password=$7,
                updated_at=NOW()
            WHERE user_id=$1
            """,
            user_id,
            calendar_url,
            calendar_username,
            calendar_password,
            contacts_url,
            contacts_username,
            contacts_password,
        )


async def _db_get_shared_list(name: str) -> list:
    async with _pool().acquire() as conn:
        row = await conn.fetchrow("SELECT items FROM shared_lists WHERE name = $1", name)
    if row is None:
        await _db_create_shared_list(name)
        return []
    items = row["items"]
    return json.loads(items) if isinstance(items, str) else (items or [])


async def _db_create_shared_list(name: str) -> None:
    async with _pool().acquire() as conn:
        await conn.execute("INSERT INTO shared_lists (name, items) VALUES ($1, '[]') ON CONFLICT (name) DO NOTHING", name)


async def _db_update_shared_list(name: str, items: list) -> None:
    async with _pool().acquire() as conn:
        await conn.execute(
            "INSERT INTO shared_lists (name, items, updated_at) VALUES ($1, $2, NOW()) ON CONFLICT (name) DO UPDATE SET items = $2, updated_at = NOW()",
            name,
            json.dumps(items),
        )


async def _db_get_all_shared_lists() -> dict:
    async with _pool().acquire() as conn:
        rows = await conn.fetch("SELECT name, items FROM shared_lists ORDER BY name")
    result = {}
    for row in rows:
        items = row["items"]
        result[row["name"]] = json.loads(items) if isinstance(items, str) else (items or [])
    return result


async def _db_get_household_members() -> list:
    async with _pool().acquire() as conn:
        rows = await conn.fetch("SELECT user_id, email, display_name, is_kid_safe, voice_embedding IS NOT NULL AS has_voice FROM user_configs ORDER BY email")
    return [dict(r) for r in rows]


async def _db_set_timer(user_id: str, label: str, duration_seconds: int) -> int:
    fire_at = datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None) + datetime.timedelta(seconds=duration_seconds)
    async with _pool().acquire() as conn:
        row = await conn.fetchrow(
            "INSERT INTO timers (user_id, label, fire_at) VALUES ($1, $2, $3) RETURNING id",
            user_id,
            label,
            fire_at,
        )
    return row["id"]


async def _db_list_timers(user_id: str) -> list:
    async with _pool().acquire() as conn:
        rows = await conn.fetch(
            "SELECT id, label, fire_at FROM timers WHERE user_id = $1 AND fired = FALSE AND fire_at > NOW() ORDER BY fire_at",
            user_id,
        )
    return [dict(r) for r in rows]


async def _db_cancel_timer(user_id: str, timer_id: int) -> bool:
    async with _pool().acquire() as conn:
        result = await conn.execute(
            "UPDATE timers SET fired = TRUE WHERE id = $1 AND user_id = $2 AND fired = FALSE",
            timer_id,
            user_id,
        )
    return result.split()[-1] == "1"


async def _db_fire_due_timers() -> list:
    async with _pool().acquire() as conn:
        rows = await conn.fetch("UPDATE timers SET fired = TRUE WHERE fire_at <= NOW() AND fired = FALSE RETURNING user_id, label")
    return [dict(r) for r in rows]


async def _db_set_reminder(user_id: str, text: str, fire_at: datetime.datetime, recurring_minutes: int | None) -> int:
    async with _pool().acquire() as conn:
        row = await conn.fetchrow(
            "INSERT INTO reminders (user_id, text, fire_at, recurring_minutes) VALUES ($1, $2, $3, $4) RETURNING id",
            user_id,
            text,
            fire_at,
            recurring_minutes,
        )
    return row["id"]


async def _db_list_reminders(user_id: str) -> list:
    async with _pool().acquire() as conn:
        rows = await conn.fetch(
            "SELECT id, text, fire_at, recurring_minutes FROM reminders WHERE user_id = $1 AND active = TRUE AND fire_at > NOW() ORDER BY fire_at",
            user_id,
        )
    return [dict(r) for r in rows]


async def _db_cancel_reminder(user_id: str, reminder_id: int) -> bool:
    async with _pool().acquire() as conn:
        result = await conn.execute(
            "UPDATE reminders SET active = FALSE WHERE id = $1 AND user_id = $2",
            reminder_id,
            user_id,
        )
    return result.split()[-1] == "1"


async def _db_fire_due_reminders() -> list:
    async with _pool().acquire() as conn:
        rows = await conn.fetch("SELECT id, user_id, text, recurring_minutes FROM reminders WHERE fire_at <= NOW() AND active = TRUE")
        fired = [dict(r) for r in rows]
        for r in fired:
            if r["recurring_minutes"]:
                next_fire = datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None) + datetime.timedelta(minutes=r["recurring_minutes"])
                await conn.execute("UPDATE reminders SET fire_at = $2 WHERE id = $1", r["id"], next_fire)
            else:
                await conn.execute("UPDATE reminders SET active = FALSE WHERE id = $1", r["id"])
    return fired


# ─── ROUTINES DB ─────────────────────────────────────────────────────────────
async def _db_create_routine(user_id: str, name: str, trigger_phrases: list, steps: list) -> int:
    async with _pool().acquire() as conn:
        row = await conn.fetchrow(
            "INSERT INTO routines (user_id, name, trigger_phrases, steps) VALUES ($1,$2,$3,$4) RETURNING id",
            user_id,
            name,
            json.dumps(trigger_phrases),
            json.dumps(steps),
        )
    return row["id"]


async def _db_list_routines(user_id: str) -> list:
    async with _pool().acquire() as conn:
        rows = await conn.fetch(
            "SELECT id, name, trigger_phrases, steps, active FROM routines WHERE user_id = $1 ORDER BY name",
            user_id,
        )
    result = []
    for row in rows:
        phrases = row["trigger_phrases"]
        steps = row["steps"]
        result.append(
            {
                "id": row["id"],
                "name": row["name"],
                "trigger_phrases": json.loads(phrases) if isinstance(phrases, str) else (phrases or []),
                "steps": json.loads(steps) if isinstance(steps, str) else (steps or []),
                "active": row["active"],
            }
        )
    return result


async def _db_delete_routine(user_id: str, routine_id: int) -> bool:
    async with _pool().acquire() as conn:
        result = await conn.execute("DELETE FROM routines WHERE id = $1 AND user_id = $2", routine_id, user_id)
    return result.split()[-1] == "1"


async def _db_toggle_routine(user_id: str, routine_id: int, active: bool) -> bool:
    async with _pool().acquire() as conn:
        result = await conn.execute("UPDATE routines SET active = $3 WHERE id = $1 AND user_id = $2", routine_id, user_id, active)
    return result.split()[-1] == "1"


# ─── DEVICE ALERTS DB ────────────────────────────────────────────────────────
async def _db_create_device_alert(user_id: str, name: str, entity_id: str, condition: str, value: str, message: str, cooldown_minutes: int) -> int:
    async with _pool().acquire() as conn:
        row = await conn.fetchrow(
            "INSERT INTO device_alert_rules (user_id, name, entity_id, condition, value, message, cooldown_minutes) VALUES ($1,$2,$3,$4,$5,$6,$7) RETURNING id",
            user_id,
            name,
            entity_id,
            condition,
            value,
            message,
            cooldown_minutes,
        )
    return row["id"]


async def _db_list_device_alerts(user_id: str) -> list:
    async with _pool().acquire() as conn:
        rows = await conn.fetch(
            "SELECT id, name, entity_id, condition, value, message, cooldown_minutes, active FROM device_alert_rules WHERE user_id = $1 ORDER BY name",
            user_id,
        )
    return [dict(r) for r in rows]


async def _db_delete_device_alert(user_id: str, alert_id: int) -> bool:
    async with _pool().acquire() as conn:
        result = await conn.execute("DELETE FROM device_alert_rules WHERE id = $1 AND user_id = $2", alert_id, user_id)
    return result.split()[-1] == "1"


async def _db_get_active_device_alerts() -> list:
    async with _pool().acquire() as conn:
        rows = await conn.fetch("SELECT id, user_id, name, entity_id, condition, value, message, cooldown_minutes, last_fired FROM device_alert_rules WHERE active = TRUE")
    return [dict(r) for r in rows]


async def _db_update_alert_last_fired(alert_id: int) -> None:
    async with _pool().acquire() as conn:
        await conn.execute("UPDATE device_alert_rules SET last_fired = NOW() WHERE id = $1", alert_id)


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
_party_tokens: dict[str, str] = {}  # token → user_id

# Dedup map for wake triggers — prevents two devices firing simultaneously
_last_wake_time: dict[str, float] = {}
_WAKE_DEDUP_WINDOW = 2.0  # seconds


def _create_party_token(user_id: str) -> str:
    for t, uid in list(_party_tokens.items()):
        if uid == user_id:
            _party_tokens.pop(t, None)
    token = secrets.token_urlsafe(8)
    _party_tokens[token] = user_id
    return token


def _clear_party_tokens(user_id: str):
    for t, uid in list(_party_tokens.items()):
        if uid == user_id:
            _party_tokens.pop(t, None)


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


# Voice embedding cache: user_id → (embedding, display_name, is_kid_safe)
_voice_cache: dict = {}
_VOICE_THRESHOLD = 0.82


def _cosine_similarity(a: list, b: list) -> float:
    av = _np.array(a, dtype=float)
    bv = _np.array(b, dtype=float)
    denom = _np.linalg.norm(av) * _np.linalg.norm(bv)
    return float(_np.dot(av, bv) / denom) if denom > 0 else 0.0


def _extract_voice_embedding(audio_path: str) -> list | None:
    if not _VOICE_ID_OK:
        return None
    y, _ = _librosa.load(audio_path, sr=16000, mono=True)
    mfcc = _librosa.feature.mfcc(y=y, sr=16000, n_mfcc=40)
    return [*mfcc.mean(axis=1).tolist(), *mfcc.std(axis=1).tolist()]


async def _refresh_voice_cache() -> None:
    rows = await _db_get_all_voice_embeddings()
    _voice_cache.clear()
    _voice_cache.update(rows)


def _identify_speaker_from_embedding(embedding: list) -> tuple:
    """Returns (user_id | None, display_name, is_kid_safe)."""
    if not _voice_cache or not embedding:
        return None, "", False
    best_uid, best_name, best_safe, best_score = None, "", False, 0.0
    for uid, (stored, name, is_safe) in _voice_cache.items():
        score = _cosine_similarity(embedding, stored)
        if score > best_score:
            best_uid, best_name, best_safe, best_score = uid, name, is_safe, score
    if best_score >= _VOICE_THRESHOLD:
        return best_uid, best_name, best_safe
    return None, "guest", False


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
    if cached.get("unofficial_access") and expiry and expiry > datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(minutes=5):
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
    expiry_dt = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(seconds=data.get("expires_in", 28800))
    _tesla_tokens.setdefault(user_id, {})
    _tesla_tokens[user_id].update({"unofficial_access": access_token, "unofficial_expiry": expiry_dt})
    if new_refresh and new_refresh != config.get("tesla_refresh_token"):
        config["tesla_refresh_token"] = new_refresh
        async with _pool().acquire() as conn:
            await conn.execute(
                "UPDATE user_configs SET tesla_refresh_token = $2 WHERE user_id = $1",
                user_id,
                new_refresh,
            )
    return access_token


async def _tesla_fleet_access_token(user_id: str, config: dict) -> str:
    cached = _tesla_tokens.get(user_id, {})
    expiry = cached.get("fleet_expiry")
    if cached.get("fleet_access") and expiry and expiry > datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(minutes=5):
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
    expiry_dt = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(seconds=data.get("expires_in", 28800))
    _tesla_tokens.setdefault(user_id, {})
    _tesla_tokens[user_id].update({"fleet_access": access_token, "fleet_expiry": expiry_dt})
    if new_refresh and new_refresh != config.get("tesla_fleet_refresh_token"):
        config["tesla_fleet_refresh_token"] = new_refresh
        async with _pool().acquire() as conn:
            await conn.execute(
                "UPDATE user_configs SET tesla_fleet_refresh_token = $2 WHERE user_id = $1",
                user_id,
                new_refresh,
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


async def _ha_get_entity_state(config: dict, entity_id: str) -> str | None:
    url = config["ha_url"].rstrip("/") + f"/api/states/{entity_id}"
    try:
        async with httpx.AsyncClient(timeout=5) as c:
            r = await c.get(url, headers=_ha_headers(config))
        if r.status_code == 200:
            return r.json().get("state")
        return None
    except Exception:
        return None


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
        if name in _SPOTIFY_TOOL_NAMES:
            return await _execute_spotify_tool(name, args, user_id, config)
        if name in _AM_TOOL_NAMES:
            return await _execute_apple_music_tool(name, args, user_id)
        return f"Unknown tool: {name}"
    except Exception as e:
        return f"Error: {e}"


# ─── SPOTIFY ──────────────────────────────────────────────────────────────────
_SPOTIFY_AUTH_BASE = "https://accounts.spotify.com"
_SPOTIFY_API_BASE = "https://api.spotify.com/v1"
_SPOTIFY_SCOPES = "user-read-playback-state user-modify-playback-state user-read-currently-playing"

_spotify_auth_pending: dict[str, str] = {}
_spotify_tokens: dict[str, dict] = {}


def _spotify_configured(config: dict) -> bool:
    return bool(config.get("spotify_refresh_token"))


async def _db_save_spotify_tokens(user_id: str, access_token: str, refresh_token: str, expiry: float):
    async with _pool().acquire() as conn:
        await conn.execute(
            "UPDATE user_configs SET spotify_access_token=$2, spotify_refresh_token=$3, spotify_token_expiry=$4 WHERE user_id=$1",
            user_id,
            access_token,
            refresh_token,
            expiry,
        )


async def _spotify_access_token(user_id: str, config: dict) -> str:
    cached = _spotify_tokens.get(user_id, {})
    now = datetime.datetime.now().timestamp()
    if cached.get("access") and cached.get("expiry", 0) > now + 60:
        return cached["access"]

    refresh = config.get("spotify_refresh_token", "")
    if not refresh:
        raise ValueError("Spotify not connected")

    async with httpx.AsyncClient(timeout=15) as c:
        r = await c.post(
            f"{_SPOTIFY_AUTH_BASE}/api/token",
            data={
                "grant_type": "refresh_token",
                "refresh_token": refresh,
                "client_id": SPOTIFY_CLIENT_ID,
                "client_secret": SPOTIFY_CLIENT_SECRET,
            },
        )
        r.raise_for_status()
        data = r.json()

    access = data["access_token"]
    expiry = now + data.get("expires_in", 3600)
    new_refresh = data.get("refresh_token", refresh)

    _spotify_tokens[user_id] = {"access": access, "expiry": expiry}
    config["spotify_access_token"] = access
    config["spotify_refresh_token"] = new_refresh
    config["spotify_token_expiry"] = expiry
    await _db_save_spotify_tokens(user_id, access, new_refresh, expiry)
    return access


async def _spotify_req(method: str, endpoint: str, user_id: str, config: dict, **kwargs):
    token = await _spotify_access_token(user_id, config)
    headers = {"Authorization": f"Bearer {token}"}
    async with httpx.AsyncClient(timeout=15) as c:
        return await getattr(c, method)(f"{_SPOTIFY_API_BASE}{endpoint}", headers=headers, **kwargs)


async def _spotify_start_party(user_id: str, config: dict):
    try:
        await _spotify_req("put", "/me/player/shuffle", user_id, config, params={"state": "true"})
        await _spotify_req("put", "/me/player/play", user_id, config)
    except Exception:
        pass


async def _execute_spotify_tool(name: str, args: dict, user_id: str, config: dict) -> str:
    if name == "spotify_now_playing":
        r = await _spotify_req("get", "/me/player/currently-playing", user_id, config)
        if r.status_code == 204 or not r.text:
            return "Nothing is currently playing."
        d = r.json()
        item = d.get("item") or {}
        track = item.get("name", "Unknown")
        artists = ", ".join(a["name"] for a in item.get("artists", []))
        state = "playing" if d.get("is_playing") else "paused"
        return f"Currently {state}: {track} by {artists}."
    if name == "spotify_play":
        r = await _spotify_req("put", "/me/player/play", user_id, config)
        return "Resumed playback." if r.status_code in (200, 204) else f"Spotify returned {r.status_code}."
    if name == "spotify_pause":
        r = await _spotify_req("put", "/me/player/pause", user_id, config)
        return "Playback paused." if r.status_code in (200, 204) else f"Spotify returned {r.status_code}."
    if name == "spotify_next":
        r = await _spotify_req("post", "/me/player/next", user_id, config)
        return "Skipped to next track." if r.status_code in (200, 204) else f"Spotify returned {r.status_code}."
    if name == "spotify_previous":
        r = await _spotify_req("post", "/me/player/previous", user_id, config)
        return "Back to previous track." if r.status_code in (200, 204) else f"Spotify returned {r.status_code}."
    if name == "spotify_volume":
        vol = max(0, min(100, int(args.get("volume_percent", 50))))
        r = await _spotify_req("put", "/me/player/volume", user_id, config, params={"volume_percent": vol})
        return f"Volume set to {vol}%." if r.status_code in (200, 204) else f"Spotify returned {r.status_code}."
    if name == "spotify_search_and_play":
        query = args.get("query", "")
        search_type = args.get("type", "track")
        r = await _spotify_req("get", "/search", user_id, config, params={"q": query, "type": search_type, "limit": 1})
        r.raise_for_status()
        data = r.json()
        uri = label = None
        if search_type == "track":
            items = data.get("tracks", {}).get("items", [])
            if items:
                uri = items[0]["uri"]
                label = f"{items[0]['name']} by {items[0]['artists'][0]['name']}"
        elif search_type == "playlist":
            items = data.get("playlists", {}).get("items", [])
            if items:
                uri, label = items[0]["uri"], items[0]["name"]
        elif search_type == "artist":
            items = data.get("artists", {}).get("items", [])
            if items:
                uri, label = items[0]["uri"], items[0]["name"]
        elif search_type == "album":
            items = data.get("albums", {}).get("items", [])
            if items:
                uri = items[0]["uri"]
                label = f"{items[0]['name']} by {items[0]['artists'][0]['name']}"
        if not uri:
            return f"Could not find any {search_type} matching '{query}'."
        play_body = {"uris": [uri]} if search_type == "track" else {"context_uri": uri}
        r2 = await _spotify_req("put", "/me/player/play", user_id, config, json=play_body)
        if r2.status_code in (200, 204):
            return f"Now playing {label}."
        return f"Found {label} but playback failed (Spotify returned {r2.status_code})."
    return f"Unknown Spotify tool: {name}"


SPOTIFY_TOOLS_ANTHROPIC = [
    {
        "name": "spotify_now_playing",
        "description": "Get the currently playing track on Spotify.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "spotify_play",
        "description": "Resume or start Spotify playback.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "spotify_pause",
        "description": "Pause Spotify playback.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "spotify_next",
        "description": "Skip to the next track on Spotify.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "spotify_previous",
        "description": "Go back to the previous track on Spotify.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "spotify_volume",
        "description": "Set the Spotify playback volume (0–100).",
        "input_schema": {
            "type": "object",
            "properties": {
                "volume_percent": {"type": "integer", "description": "Volume from 0 to 100."},
            },
            "required": ["volume_percent"],
        },
    },
    {
        "name": "spotify_search_and_play",
        "description": "Search Spotify and play the best matching track, artist, album, or playlist.",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search query (artist, song, playlist name, etc.)"},
                "type": {
                    "type": "string",
                    "enum": ["track", "artist", "album", "playlist"],
                    "description": "What to search for. Default: track.",
                },
            },
            "required": ["query"],
        },
    },
]

SPOTIFY_TOOLS_OPENAI = [
    {
        "type": "function",
        "function": {"name": "spotify_now_playing", "description": "Get the currently playing track on Spotify.", "parameters": {"type": "object", "properties": {}}},
    },
    {"type": "function", "function": {"name": "spotify_play", "description": "Resume or start Spotify playback.", "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {"name": "spotify_pause", "description": "Pause Spotify playback.", "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {"name": "spotify_next", "description": "Skip to the next track on Spotify.", "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {"name": "spotify_previous", "description": "Go back to the previous track on Spotify.", "parameters": {"type": "object", "properties": {}}}},
    {
        "type": "function",
        "function": {
            "name": "spotify_volume",
            "description": "Set the Spotify playback volume (0–100).",
            "parameters": {"type": "object", "properties": {"volume_percent": {"type": "integer", "description": "Volume 0–100."}}, "required": ["volume_percent"]},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "spotify_search_and_play",
            "description": "Search Spotify and play the best matching track, artist, album, or playlist.",
            "parameters": {
                "type": "object",
                "properties": {"query": {"type": "string", "description": "Search query"}, "type": {"type": "string", "enum": ["track", "artist", "album", "playlist"]}},
                "required": ["query"],
            },
        },
    },
]

_SPOTIFY_TOOL_NAMES = {t["name"] for t in SPOTIFY_TOOLS_ANTHROPIC}


def _get_spotify_tools(config: dict, provider: str) -> list:
    if not _spotify_configured(config):
        return []
    return SPOTIFY_TOOLS_ANTHROPIC if provider == "anthropic" else SPOTIFY_TOOLS_OPENAI


# ─── APPLE MUSIC ──────────────────────────────────────────────────────────────
_am_callbacks: dict[str, asyncio.Future] = {}


def _apple_music_server_configured() -> bool:
    return bool(APPLE_MUSIC_TEAM_ID and APPLE_MUSIC_KEY_ID and APPLE_MUSIC_PRIVATE_KEY)


def _apple_music_configured(config: dict) -> bool:
    return _apple_music_server_configured() and bool(config.get("apple_music_user_token"))


def _apple_music_dev_token() -> str:
    if jwt is None:
        raise RuntimeError("PyJWT is required for Apple Music support. Install dependencies from requirements.txt.")
    now = int(datetime.datetime.now().timestamp())
    return jwt.encode(
        {"iss": APPLE_MUSIC_TEAM_ID, "iat": now, "exp": now + 15777000},
        APPLE_MUSIC_PRIVATE_KEY,
        algorithm="ES256",
        headers={"kid": APPLE_MUSIC_KEY_ID},
    )


async def _am_request_callback(sid: str, action: str, extra: dict | None = None, timeout: float = 7.0) -> str:
    cb_id = secrets.token_hex(8)
    fut: asyncio.Future = asyncio.get_event_loop().create_future()
    _am_callbacks[cb_id] = fut
    await sio.emit("apple_music_cmd", {"action": action, "cb": cb_id, **(extra or {})}, to=sid)
    try:
        return await asyncio.wait_for(fut, timeout=timeout)
    except asyncio.TimeoutError:
        return "Request timed out."
    finally:
        _am_callbacks.pop(cb_id, None)


async def _apple_music_start_party(user_id: str):
    sids = [sid for sid, uid in _sid_to_user.items() if uid == user_id]
    if sids:
        await sio.emit("apple_music_cmd", {"action": "party"}, to=sids[0])


async def _execute_apple_music_tool(name: str, args: dict, user_id: str) -> str:
    sids = [sid for sid, uid in _sid_to_user.items() if uid == user_id]
    if not sids:
        return "No active Apple Music session."
    sid = sids[0]

    _simple: dict[str, tuple[str, str]] = {
        "apple_music_play": ("play", "Playback started."),
        "apple_music_pause": ("pause", "Playback paused."),
        "apple_music_next": ("next", "Skipped to next track."),
        "apple_music_previous": ("previous", "Back to previous track."),
    }
    if name in _simple:
        action, msg = _simple[name]
        await sio.emit("apple_music_cmd", {"action": action}, to=sid)
        return msg
    if name == "apple_music_now_playing":
        return await _am_request_callback(sid, "now_playing")
    if name == "apple_music_volume":
        vol = max(0, min(100, int(args.get("volume_percent", 50))))
        await sio.emit("apple_music_cmd", {"action": "volume", "value": vol / 100}, to=sid)
        return f"Volume set to {vol}%."
    if name == "apple_music_search_and_play":
        type_map = {"track": "songs", "artist": "artists", "album": "albums", "playlist": "playlists"}
        am_type = type_map.get(args.get("type", "track"), "songs")
        return await _am_request_callback(sid, "search_and_play", {"query": args.get("query", ""), "type": am_type}, timeout=12.0)
    return f"Unknown Apple Music tool: {name}"


APPLE_MUSIC_TOOLS_ANTHROPIC = [
    {"name": "apple_music_now_playing", "description": "Get the currently playing track on Apple Music.", "input_schema": {"type": "object", "properties": {}}},
    {"name": "apple_music_play", "description": "Resume or start Apple Music playback.", "input_schema": {"type": "object", "properties": {}}},
    {"name": "apple_music_pause", "description": "Pause Apple Music playback.", "input_schema": {"type": "object", "properties": {}}},
    {"name": "apple_music_next", "description": "Skip to the next track on Apple Music.", "input_schema": {"type": "object", "properties": {}}},
    {"name": "apple_music_previous", "description": "Go back to the previous track on Apple Music.", "input_schema": {"type": "object", "properties": {}}},
    {
        "name": "apple_music_volume",
        "description": "Set the Apple Music playback volume (0–100).",
        "input_schema": {
            "type": "object",
            "properties": {"volume_percent": {"type": "integer", "description": "Volume from 0 to 100."}},
            "required": ["volume_percent"],
        },
    },
    {
        "name": "apple_music_search_and_play",
        "description": "Search Apple Music and play the best matching song, artist, album, or playlist.",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search query (artist, song, playlist name, etc.)"},
                "type": {"type": "string", "enum": ["track", "artist", "album", "playlist"], "description": "What to search for. Default: track."},
            },
            "required": ["query"],
        },
    },
]

APPLE_MUSIC_TOOLS_OPENAI = [
    {
        "type": "function",
        "function": {"name": "apple_music_now_playing", "description": "Get the currently playing track on Apple Music.", "parameters": {"type": "object", "properties": {}}},
    },
    {"type": "function", "function": {"name": "apple_music_play", "description": "Resume or start Apple Music playback.", "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {"name": "apple_music_pause", "description": "Pause Apple Music playback.", "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {"name": "apple_music_next", "description": "Skip to the next track on Apple Music.", "parameters": {"type": "object", "properties": {}}}},
    {
        "type": "function",
        "function": {"name": "apple_music_previous", "description": "Go back to the previous track on Apple Music.", "parameters": {"type": "object", "properties": {}}},
    },
    {
        "type": "function",
        "function": {
            "name": "apple_music_volume",
            "description": "Set the Apple Music playback volume (0–100).",
            "parameters": {"type": "object", "properties": {"volume_percent": {"type": "integer", "description": "Volume 0–100."}}, "required": ["volume_percent"]},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "apple_music_search_and_play",
            "description": "Search Apple Music and play the best matching song, artist, album, or playlist.",
            "parameters": {
                "type": "object",
                "properties": {"query": {"type": "string", "description": "Search query"}, "type": {"type": "string", "enum": ["track", "artist", "album", "playlist"]}},
                "required": ["query"],
            },
        },
    },
]

_AM_TOOL_NAMES = {t["name"] for t in APPLE_MUSIC_TOOLS_ANTHROPIC}


def _get_apple_music_tools(config: dict, provider: str) -> list:
    if not _apple_music_configured(config):
        return []
    return APPLE_MUSIC_TOOLS_ANTHROPIC if provider == "anthropic" else APPLE_MUSIC_TOOLS_OPENAI


# ─── SHARED LIST TOOLS ───────────────────────────────────────────────────────
_SHARED_LIST_TOOL_ANTHROPIC = {
    "name": "manage_shared_list",
    "description": "Manage shared household lists such as shopping or todo. Use to add, remove, read, or clear items.",
    "input_schema": {
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": ["add", "remove", "read", "clear"], "description": "Operation to perform"},
            "list_name": {"type": "string", "description": "Name of the list, e.g. shopping or todo"},
            "item": {"type": "string", "description": "Item to add or remove (omit for read/clear)"},
        },
        "required": ["action", "list_name"],
    },
}

_SHARED_LIST_TOOL_OPENAI = {
    "type": "function",
    "function": {
        "name": "manage_shared_list",
        "description": "Manage shared household lists such as shopping or todo.",
        "parameters": {
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": ["add", "remove", "read", "clear"]},
                "list_name": {"type": "string", "description": "Name of the list, e.g. shopping or todo"},
                "item": {"type": "string", "description": "Item to add or remove (omit for read/clear)"},
            },
            "required": ["action", "list_name"],
        },
    },
}


def _get_shared_list_tools(provider: str) -> list:
    return [_SHARED_LIST_TOOL_ANTHROPIC] if provider == "anthropic" else [_SHARED_LIST_TOOL_OPENAI]


async def _execute_shared_list_tool(args: dict) -> str:
    action = (args.get("action") or "").lower()
    list_name = (args.get("list_name") or "shopping").lower().strip()[:50]
    item = (args.get("item") or "").strip()[:200]
    items = await _db_get_shared_list(list_name)
    if action == "read":
        return f"{list_name.title()} list is empty." if not items else f"{list_name.title()}: " + ", ".join(items) + "."
    if action == "add":
        if not item:
            return "No item specified."
        if item.lower() not in [i.lower() for i in items]:
            items.append(item)
            await _db_update_shared_list(list_name, items)
        return f"Added '{item}' to {list_name}. {len(items)} item(s) now."
    if action == "remove":
        if not item:
            return "No item specified."
        new = [i for i in items if i.lower() != item.lower()]
        if len(new) == len(items):
            return f"'{item}' not found in {list_name}."
        await _db_update_shared_list(list_name, new)
        return f"Removed '{item}' from {list_name}."
    if action == "clear":
        await _db_update_shared_list(list_name, [])
        return f"{list_name.title()} list cleared."
    return f"Unknown action: {action}"


# ─── CALENDAR & CONTACTS (CALDAV / CARDDAV) ─────────────────────────────────
_DAV_NS = {
    "D": "DAV:",
    "C": "urn:ietf:params:xml:ns:caldav",
    "A": "urn:ietf:params:xml:ns:carddav",
}


def _calendar_configured(config: dict) -> bool:
    return bool(config.get("calendar_url") and config.get("calendar_username") and config.get("calendar_password"))


def _contacts_configured(config: dict) -> bool:
    return bool(config.get("contacts_url") and config.get("contacts_username") and config.get("contacts_password"))


def _ensure_trailing_slash(url: str) -> str:
    parsed = urllib.parse.urlsplit(url)
    path = parsed.path or "/"
    if not path.endswith("/"):
        path += "/"
    return urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, path, parsed.query, parsed.fragment))


def _dav_join(base: str, href: str) -> str:
    base_url = base if base.endswith("/") else base + "/"
    return urllib.parse.urljoin(base_url, href or "")


def _dav_propfind_body(props: list[tuple[str, str]]) -> bytes:
    root = ET.Element("{DAV:}propfind")
    prop_el = ET.SubElement(root, "{DAV:}prop")
    for ns_uri, name in props:
        ET.SubElement(prop_el, f"{{{ns_uri}}}{name}")
    return ET.tostring(root, encoding="utf-8", xml_declaration=True)


async def _dav_request(
    method: str,
    url: str,
    username: str,
    password: str,
    body: bytes | str | None = None,
    *,
    depth: str | None = None,
    content_type: str | None = "application/xml; charset=utf-8",
    extra_headers: dict | None = None,
):
    headers = {"User-Agent": "Jarvis/1.0"}
    if depth is not None:
        headers["Depth"] = depth
    if body is not None and content_type:
        headers["Content-Type"] = content_type
    if extra_headers:
        headers.update(extra_headers)
    async with httpx.AsyncClient(follow_redirects=True) as client:
        return await client.request(method, url, headers=headers, content=body, auth=(username, password), timeout=15)


def _dav_raise_for_status(response, action: str) -> None:
    if response.status_code in (200, 201, 204, 207):
        return
    if response.status_code in (401, 403):
        raise ValueError(f"{action}: authentication failed.")
    detail = re.sub(r"\s+", " ", response.text or "").strip()[:140]
    if detail:
        raise ValueError(f"{action}: server returned {response.status_code} ({detail}).")
    raise ValueError(f"{action}: server returned {response.status_code}.")


def _dav_multistatus_responses(xml_text: str) -> list:
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as e:
        raise ValueError(f"DAV server returned malformed XML: {e}") from e
    return root.findall("D:response", _DAV_NS)


def _dav_href(response) -> str:
    return (response.findtext("D:href", default="", namespaces=_DAV_NS) or "").strip()


def _dav_response_for_url(responses: list, url: str):
    wanted_path = urllib.parse.urlsplit(url).path.rstrip("/")
    for response in responses:
        href = _dav_href(response)
        if urllib.parse.urlsplit(href).path.rstrip("/") == wanted_path:
            return response
    return responses[0] if responses else None


def _dav_response_prop(response):
    for propstat in response.findall("D:propstat", _DAV_NS):
        status = (propstat.findtext("D:status", default="", namespaces=_DAV_NS) or "").upper()
        if " 200 " in status:
            prop = propstat.find("D:prop", _DAV_NS)
            if prop is not None:
                return prop
    propstat = response.find("D:propstat", _DAV_NS)
    return propstat.find("D:prop", _DAV_NS) if propstat is not None else None


def _dav_resource_types(response) -> set[str]:
    prop = _dav_response_prop(response)
    if prop is None:
        return set()
    resourcetype = prop.find("D:resourcetype", _DAV_NS)
    if resourcetype is None:
        return set()
    return {child.tag.split("}", 1)[-1] for child in list(resourcetype)}


def _dav_display_name(response) -> str:
    prop = _dav_response_prop(response)
    if prop is None:
        return ""
    return (prop.findtext("D:displayname", default="", namespaces=_DAV_NS) or "").strip()


def _dav_prop_href(response, path: str) -> str | None:
    prop = _dav_response_prop(response)
    if prop is None:
        return None
    node = prop.find(path, _DAV_NS)
    if node is None:
        return None
    if node.tag.endswith("href"):
        return (node.text or "").strip() or None
    href = node.findtext("D:href", default="", namespaces=_DAV_NS)
    return href.strip() or None


def _pick_best_dav_collection(collections: list[dict], kind: str) -> dict | None:
    if not collections:
        return None

    def score(item: dict) -> int:
        name = (item.get("display_name") or "").lower()
        url = (item.get("url") or "").lower()
        score = 0
        if "default" in name or "primary" in name:
            score += 4
        if kind == "calendar" and url.endswith("/events/"):
            score += 3
        if kind == "addressbook" and ("contacts" in name or "address" in name):
            score += 3
        if kind == "calendar" and not any(piece in url for piece in ("inbox", "outbox", "notification")):
            score += 2
        if item.get("display_name"):
            score += 1
        return score

    return max(collections, key=score)


async def _resolve_dav_collection(url: str, username: str, password: str, kind: str) -> dict:
    url = (url or "").strip()
    username = (username or "").strip()
    password = (password or "").strip()
    if not url or not username or not password:
        raise ValueError("Server URL, username, and password are all required.")

    direct_props = [
        ("DAV:", "resourcetype"),
        ("DAV:", "displayname"),
        ("DAV:", "current-user-principal"),
    ]
    direct = await _dav_request("PROPFIND", url, username, password, _dav_propfind_body(direct_props), depth="0")
    _dav_raise_for_status(direct, "DAV discovery")
    responses = _dav_multistatus_responses(direct.text)
    current = _dav_response_for_url(responses, url)
    if current and kind in _dav_resource_types(current):
        return {
            "url": _ensure_trailing_slash(url),
            "display_name": _dav_display_name(current),
        }

    principal_href = _dav_prop_href(current, "D:current-user-principal") if current is not None else None
    if not principal_href:
        raise ValueError("Could not discover the current DAV principal from that URL.")
    principal_url = _dav_join(url, principal_href)

    home_ns = "urn:ietf:params:xml:ns:caldav" if kind == "calendar" else "urn:ietf:params:xml:ns:carddav"
    home_prop = "calendar-home-set" if kind == "calendar" else "addressbook-home-set"
    home = await _dav_request("PROPFIND", principal_url, username, password, _dav_propfind_body([(home_ns, home_prop)]), depth="0")
    _dav_raise_for_status(home, "DAV home-set discovery")
    home_responses = _dav_multistatus_responses(home.text)
    principal_response = _dav_response_for_url(home_responses, principal_url)
    home_href = _dav_prop_href(principal_response, f"{'C' if kind == 'calendar' else 'A'}:{home_prop}") if principal_response is not None else None
    if not home_href:
        raise ValueError(f"Could not find a {kind} home for this account.")
    home_url = _dav_join(principal_url, home_href)

    collection = await _dav_request(
        "PROPFIND",
        home_url,
        username,
        password,
        _dav_propfind_body([("DAV:", "resourcetype"), ("DAV:", "displayname")]),
        depth="1",
    )
    _dav_raise_for_status(collection, "DAV collection discovery")
    collections = []
    for response in _dav_multistatus_responses(collection.text):
        if kind not in _dav_resource_types(response):
            continue
        href = _dav_href(response)
        if not href:
            continue
        collections.append(
            {
                "url": _ensure_trailing_slash(_dav_join(home_url, href)),
                "display_name": _dav_display_name(response),
            }
        )

    best = _pick_best_dav_collection(collections, kind)
    if not best:
        raise ValueError(f"No {kind} collection was found for this account.")
    return best


def _unfold_ical_lines(text: str) -> list[str]:
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    unfolded = []
    for line in lines:
        if not line:
            continue
        if line[:1] in (" ", "\t") and unfolded:
            unfolded[-1] += line[1:]
        else:
            unfolded.append(line)
    return unfolded


def _parse_ical_line(line: str) -> tuple[str, dict, str]:
    key, value = line.split(":", 1)
    parts = key.split(";")
    params = {}
    for param in parts[1:]:
        if "=" in param:
            pkey, pvalue = param.split("=", 1)
            params[pkey.upper()] = pvalue
    return parts[0].upper(), params, value


def _unescape_ical_text(value: str) -> str:
    return value.replace("\\n", "\n").replace("\\N", "\n").replace("\\,", ",").replace("\\;", ";").replace("\\\\", "\\")


def _parse_ical_datetime(value: str, params: dict) -> tuple[datetime.datetime, bool]:
    local_tz = datetime.datetime.now().astimezone().tzinfo
    if re.fullmatch(r"\d{8}", value):
        day = datetime.date(int(value[:4]), int(value[4:6]), int(value[6:8]))
        return datetime.datetime.combine(day, datetime.time.min, tzinfo=local_tz), True
    if value.endswith("Z"):
        dt = datetime.datetime.strptime(value, "%Y%m%dT%H%M%SZ").replace(tzinfo=datetime.timezone.utc)
        return dt.astimezone(local_tz), False
    for fmt in ("%Y%m%dT%H%M%S", "%Y%m%dT%H%M"):
        try:
            dt = datetime.datetime.strptime(value, fmt)
            return dt.replace(tzinfo=local_tz), False
        except ValueError:
            continue
    raise ValueError(f"Unsupported iCalendar datetime: {value}")


def _friendly_when(dt: datetime.datetime, *, include_date: bool = True) -> str:
    stamp = dt.astimezone().strftime("%a %b %d, %I:%M %p" if include_date else "%I:%M %p")
    stamp = re.sub(r"(?<=\s)0(\d)", r"\1", stamp)
    return stamp


type _CalendarEvent = dict[str, datetime.datetime | str | bool]
type _ContactCard = dict[str, str | list[str]]


def _format_calendar_event(event: _CalendarEvent) -> str:
    title_value = event.get("title")
    title = title_value if isinstance(title_value, str) and title_value else "Untitled event"
    start_value = event.get("start")
    end_value = event.get("end")
    start = start_value if isinstance(start_value, datetime.datetime) else None
    end = end_value if isinstance(end_value, datetime.datetime) else start
    if not start:
        return title
    if bool(event.get("all_day")):
        when = f"{start.strftime('%a %b %d').replace(' 0', ' ')} (all day)"
    elif end and start.date() == end.date():
        when = f"{_friendly_when(start)}–{_friendly_when(end, include_date=False)}"
    else:
        when = f"{_friendly_when(start)} to {_friendly_when(end)}"
    bits = [f"{title} — {when}"]
    location_value = event.get("location")
    if isinstance(location_value, str) and location_value:
        bits.append(f"@ {location_value}")
    return " ".join(bits)


def _parse_ical_events(calendar_blob: str) -> list[_CalendarEvent]:
    events: list[_CalendarEvent] = []
    current: _CalendarEvent | None = None
    for line in _unfold_ical_lines(calendar_blob):
        upper = line.upper()
        if upper == "BEGIN:VEVENT":
            current = {"title": "", "location": "", "description": "", "all_day": False}
            continue
        if upper == "END:VEVENT":
            if current and current.get("start"):
                if "end" not in current:
                    current["end"] = current["start"]
                events.append(current)
            current = None
            continue
        if current is None or ":" not in line:
            continue
        name, params, value = _parse_ical_line(line)
        if name == "SUMMARY":
            current["title"] = _unescape_ical_text(value).strip()
        elif name == "LOCATION":
            current["location"] = _unescape_ical_text(value).strip()
        elif name == "DESCRIPTION":
            current["description"] = _unescape_ical_text(value).strip()
        elif name == "DTSTART":
            current["start"], current["all_day"] = _parse_ical_datetime(value.strip(), params)
        elif name == "DTEND":
            current["end"], _ = _parse_ical_datetime(value.strip(), params)
    return events


def _parse_calendar_input(value: str):
    raw = (value or "").strip()
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", raw):
        return datetime.date.fromisoformat(raw), True
    try:
        parsed = datetime.datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError as e:
        raise ValueError(f"Invalid datetime: {raw}. Use ISO 8601.") from e
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=datetime.datetime.now().astimezone().tzinfo)
    return parsed, False


def _ical_escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace("\n", "\\n").replace(",", "\\,").replace(";", "\\;")


def _build_calendar_event_ics(title: str, start, end, *, description: str = "", location: str = "", all_day: bool = False) -> str:
    uid = f"{uuid.uuid4().hex}@jarvis"
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//JARVIS//EN",
        "CALSCALE:GREGORIAN",
        "BEGIN:VEVENT",
        f"UID:{uid}",
        f"DTSTAMP:{datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%SZ')}",
    ]
    if all_day:
        start_day = start if isinstance(start, datetime.date) and not isinstance(start, datetime.datetime) else start.date()
        end_day = end if isinstance(end, datetime.date) and not isinstance(end, datetime.datetime) else end.date()
        lines.append(f"DTSTART;VALUE=DATE:{start_day.strftime('%Y%m%d')}")
        lines.append(f"DTEND;VALUE=DATE:{end_day.strftime('%Y%m%d')}")
    else:
        start_utc = start.astimezone(datetime.timezone.utc)
        end_utc = end.astimezone(datetime.timezone.utc)
        lines.append(f"DTSTART:{start_utc.strftime('%Y%m%dT%H%M%SZ')}")
        lines.append(f"DTEND:{end_utc.strftime('%Y%m%dT%H%M%SZ')}")
    lines.append(f"SUMMARY:{_ical_escape(title)}")
    if location:
        lines.append(f"LOCATION:{_ical_escape(location)}")
    if description:
        lines.append(f"DESCRIPTION:{_ical_escape(description)}")
    lines.extend(["END:VEVENT", "END:VCALENDAR", ""])
    return "\r\n".join(lines)


async def _calendar_events_between(config: dict, start: datetime.datetime, end: datetime.datetime, *, limit: int = 10) -> list[_CalendarEvent]:
    start_utc = start.astimezone(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    end_utc = end.astimezone(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    body = f"""<?xml version="1.0" encoding="utf-8"?>
<C:calendar-query xmlns:D="DAV:" xmlns:C="urn:ietf:params:xml:ns:caldav">
  <D:prop>
    <D:getetag />
    <C:calendar-data />
  </D:prop>
  <C:filter>
    <C:comp-filter name="VCALENDAR">
      <C:comp-filter name="VEVENT">
        <C:time-range start="{start_utc}" end="{end_utc}" />
      </C:comp-filter>
    </C:comp-filter>
  </C:filter>
</C:calendar-query>"""
    response = await _dav_request(
        "REPORT",
        config["calendar_url"],
        config["calendar_username"],
        config["calendar_password"],
        body,
        depth="1",
    )
    _dav_raise_for_status(response, "Calendar lookup")
    events: list[_CalendarEvent] = []
    for dav_response in _dav_multistatus_responses(response.text):
        prop = _dav_response_prop(dav_response)
        if prop is None:
            continue
        calendar_data = prop.findtext("C:calendar-data", default="", namespaces=_DAV_NS)
        if not calendar_data:
            continue
        for event in _parse_ical_events(calendar_data):
            event_end_value = event.get("end") or event.get("start")
            event_start_value = event.get("start")
            event_start = event_start_value if isinstance(event_start_value, datetime.datetime) else None
            event_end = event_end_value if isinstance(event_end_value, datetime.datetime) else None
            if not event_start or not event_end:
                continue
            if event_start < end and event_end >= start:
                events.append(event)
    events.sort(key=lambda event: event["start"] if isinstance(event.get("start"), datetime.datetime) else datetime.datetime.max.replace(tzinfo=datetime.timezone.utc))
    return events[:limit]


async def _lookup_contacts(config: dict, query: str, *, preferred_channel: str = "any", limit: int = 5) -> list[_ContactCard]:
    body = """<?xml version="1.0" encoding="utf-8"?>
<A:addressbook-query xmlns:D="DAV:" xmlns:A="urn:ietf:params:xml:ns:carddav">
  <D:prop>
    <D:getetag />
    <A:address-data />
  </D:prop>
</A:addressbook-query>"""
    response = await _dav_request(
        "REPORT",
        config["contacts_url"],
        config["contacts_username"],
        config["contacts_password"],
        body,
        depth="1",
    )
    _dav_raise_for_status(response, "Contacts lookup")
    query_lc = query.lower().strip()
    digits = re.sub(r"\D", "", query)
    matches: list[tuple[int, _ContactCard]] = []
    for dav_response in _dav_multistatus_responses(response.text):
        prop = _dav_response_prop(dav_response)
        if prop is None:
            continue
        address_data = prop.findtext("A:address-data", default="", namespaces=_DAV_NS)
        if not address_data:
            continue
        for contact in _parse_vcards(address_data):
            if preferred_channel == "phone" and not contact["phones"]:
                continue
            if preferred_channel == "email" and not contact["emails"]:
                continue
            score = _score_contact_match(contact, query_lc, digits)
            if score <= 0:
                continue
            matches.append((score, contact))
    matches.sort(key=lambda item: (-item[0], (item[1].get("name") or "").lower()))
    return [contact for _, contact in matches[:limit]]


def _dedupe_preserve_order(values: list[str]) -> list[str]:
    seen = set()
    out = []
    for value in values:
        key = value.lower()
        if not value or key in seen:
            continue
        seen.add(key)
        out.append(value)
    return out


def _parse_vcards(vcard_blob: str) -> list[_ContactCard]:
    cards: list[_ContactCard] = []
    current: _ContactCard | None = None
    for line in _unfold_ical_lines(vcard_blob):
        upper = line.upper()
        if upper == "BEGIN:VCARD":
            current = {"name": "", "nicknames": [], "phones": [], "emails": []}
            continue
        if upper == "END:VCARD":
            name_value = current.get("name") if current else ""
            phones_value = current.get("phones") if current else []
            emails_value = current.get("emails") if current else []
            nicknames_value = current.get("nicknames") if current else []
            if current and (name_value or phones_value or emails_value):
                if isinstance(phones_value, list):
                    current["phones"] = _dedupe_preserve_order(phones_value)
                if isinstance(emails_value, list):
                    current["emails"] = _dedupe_preserve_order(emails_value)
                if isinstance(nicknames_value, list):
                    current["nicknames"] = _dedupe_preserve_order(nicknames_value)
                cards.append(current)
            current = None
            continue
        if current is None or ":" not in line:
            continue
        name, _params, value = _parse_ical_line(line)
        clean = _unescape_ical_text(value).strip()
        if name == "FN":
            current["name"] = clean
        elif name == "NICKNAME":
            nicknames = current.get("nicknames")
            if isinstance(nicknames, list):
                nicknames.extend([part.strip() for part in clean.split(",") if part.strip()])
        elif name == "TEL":
            phones = current.get("phones")
            if isinstance(phones, list):
                phones.append(clean[4:] if clean.lower().startswith("tel:") else clean)
        elif name == "EMAIL":
            emails = current.get("emails")
            if isinstance(emails, list):
                emails.append(clean[7:] if clean.lower().startswith("mailto:") else clean)
    return cards


def _score_contact_match(contact: _ContactCard, query_lc: str, digits: str) -> int:
    if not query_lc and not digits:
        return 0
    name_value = contact.get("name")
    nicknames_value = contact.get("nicknames", [])
    emails_value = contact.get("emails", [])
    phones_value = contact.get("phones", [])
    name = name_value.lower() if isinstance(name_value, str) else ""
    nicknames = [nick.lower() for nick in nicknames_value] if isinstance(nicknames_value, list) else []
    emails = [email.lower() for email in emails_value] if isinstance(emails_value, list) else []
    phones = phones_value if isinstance(phones_value, list) else []
    if query_lc and name == query_lc:
        return 100
    if query_lc and query_lc in nicknames:
        return 95
    if query_lc and name.startswith(query_lc):
        return 85
    if query_lc and any(nick.startswith(query_lc) for nick in nicknames):
        return 80
    if query_lc and query_lc in name:
        return 70
    if query_lc and any(query_lc in nick for nick in nicknames):
        return 65
    if query_lc and any(query_lc in email for email in emails):
        return 60
    if digits and any(digits in re.sub(r"\D", "", phone) for phone in phones):
        return 60
    return 0


def _format_contact(contact: _ContactCard, preferred_channel: str) -> str:
    name_value = contact.get("name")
    emails_value = contact.get("emails", [])
    phones_value = contact.get("phones", [])
    emails = emails_value if isinstance(emails_value, list) else []
    phones = phones_value if isinstance(phones_value, list) else []
    name = name_value if isinstance(name_value, str) and name_value else (emails or phones or ["Unnamed contact"])[0]
    details = []
    if preferred_channel in ("any", "phone") and phones:
        details.append("phone: " + ", ".join(phones[:2]))
    if preferred_channel in ("any", "email") and emails:
        details.append("email: " + ", ".join(emails[:2]))
    return f"{name} — " + "; ".join(details) if details else name


_CALENDAR_TOOL_ANTHROPIC = {
    "name": "manage_calendar",
    "description": "Read upcoming calendar events or create a new event in the user's CalDAV calendar.",
    "input_schema": {
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": ["list", "create"]},
            "start": {"type": "string", "description": "Start date/time in ISO 8601. Optional for list; required for create."},
            "end": {"type": "string", "description": "End date/time in ISO 8601. Optional for list; required for create."},
            "title": {"type": "string", "description": "Event title for create."},
            "location": {"type": "string", "description": "Event location for create."},
            "description": {"type": "string", "description": "Event notes/description for create."},
            "all_day": {"type": "boolean", "description": "Whether this should be created as an all-day event."},
            "limit": {"type": "integer", "description": "How many events to return when listing (default 5, max 10)."},
        },
        "required": ["action"],
    },
}

_CALENDAR_TOOL_OPENAI = {
    "type": "function",
    "function": {
        "name": "manage_calendar",
        "description": "Read upcoming calendar events or create a new event in the user's CalDAV calendar.",
        "parameters": {
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": ["list", "create"]},
                "start": {"type": "string"},
                "end": {"type": "string"},
                "title": {"type": "string"},
                "location": {"type": "string"},
                "description": {"type": "string"},
                "all_day": {"type": "boolean"},
                "limit": {"type": "integer"},
            },
            "required": ["action"],
        },
    },
}

_CONTACT_LOOKUP_TOOL_ANTHROPIC = {
    "name": "lookup_contact",
    "description": "Look up a contact by name, nickname, phone number, or email in the user's CardDAV address book.",
    "input_schema": {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Name, nickname, email, or phone digits to search for."},
            "preferred_channel": {"type": "string", "enum": ["any", "phone", "email"], "description": "Prefer phone numbers, email addresses, or either."},
        },
        "required": ["query"],
    },
}

_CONTACT_LOOKUP_TOOL_OPENAI = {
    "type": "function",
    "function": {
        "name": "lookup_contact",
        "description": "Look up a contact by name, nickname, phone number, or email in the user's CardDAV address book.",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "preferred_channel": {"type": "string", "enum": ["any", "phone", "email"]},
            },
            "required": ["query"],
        },
    },
}


# ─── TIMER / REMINDER / NEWS TOOLS ───────────────────────────────────────────
_TIMER_TOOL_ANTHROPIC = {
    "name": "manage_timer",
    "description": "Set, list, or cancel kitchen/task timers.",
    "input_schema": {
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": ["set", "list", "cancel"]},
            "label": {"type": "string", "description": "Name for the timer, e.g. pasta, laundry"},
            "duration_seconds": {"type": "integer", "description": "Duration in seconds (required for set)"},
            "timer_id": {"type": "integer", "description": "Timer ID to cancel (required for cancel)"},
        },
        "required": ["action"],
    },
}

_TIMER_TOOL_OPENAI = {
    "type": "function",
    "function": {
        "name": "manage_timer",
        "description": "Set, list, or cancel timers.",
        "parameters": {
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": ["set", "list", "cancel"]},
                "label": {"type": "string"},
                "duration_seconds": {"type": "integer"},
                "timer_id": {"type": "integer"},
            },
            "required": ["action"],
        },
    },
}

_REMINDER_TOOL_ANTHROPIC = {
    "name": "manage_reminder",
    "description": "Set, list, or cancel reminders. fire_at must be ISO 8601 (use the current date/time from context to calculate it).",
    "input_schema": {
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": ["set", "list", "cancel"]},
            "text": {"type": "string", "description": "Reminder message"},
            "fire_at": {"type": "string", "description": "ISO 8601 datetime when to fire"},
            "recurring_minutes": {"type": "integer", "description": "Repeat interval in minutes (optional)"},
            "reminder_id": {"type": "integer", "description": "Reminder ID to cancel"},
        },
        "required": ["action"],
    },
}

_REMINDER_TOOL_OPENAI = {
    "type": "function",
    "function": {
        "name": "manage_reminder",
        "description": "Set, list, or cancel reminders.",
        "parameters": {
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": ["set", "list", "cancel"]},
                "text": {"type": "string"},
                "fire_at": {"type": "string"},
                "recurring_minutes": {"type": "integer"},
                "reminder_id": {"type": "integer"},
            },
            "required": ["action"],
        },
    },
}

_NEWS_TOOL_ANTHROPIC = {
    "name": "get_news_headlines",
    "description": "Fetch the latest news headlines by category.",
    "input_schema": {
        "type": "object",
        "properties": {
            "category": {
                "type": "string",
                "enum": ["general", "technology", "science", "health", "business", "sports"],
            },
            "count": {"type": "integer", "description": "Number of headlines (1–10, default 5)"},
        },
        "required": [],
    },
}

_NEWS_TOOL_OPENAI = {
    "type": "function",
    "function": {
        "name": "get_news_headlines",
        "description": "Fetch latest news headlines by category.",
        "parameters": {
            "type": "object",
            "properties": {
                "category": {"type": "string", "enum": ["general", "technology", "science", "health", "business", "sports"]},
                "count": {"type": "integer"},
            },
            "required": [],
        },
    },
}

_NEWS_RSS = {
    "general": "https://feeds.bbci.co.uk/news/rss.xml",
    "technology": "https://feeds.bbci.co.uk/news/technology/rss.xml",
    "science": "https://feeds.bbci.co.uk/news/science_and_environment/rss.xml",
    "health": "https://feeds.bbci.co.uk/news/health/rss.xml",
    "business": "https://feeds.bbci.co.uk/news/business/rss.xml",
    "sports": "https://feeds.bbci.co.uk/news/sport/rss.xml",
}


def _get_parity_tools(provider: str) -> list:
    if provider == "anthropic":
        return [_TIMER_TOOL_ANTHROPIC, _REMINDER_TOOL_ANTHROPIC, _NEWS_TOOL_ANTHROPIC]
    return [_TIMER_TOOL_OPENAI, _REMINDER_TOOL_OPENAI, _NEWS_TOOL_OPENAI]


def _get_phase1_tools(config: dict, provider: str) -> list:
    tools = _get_parity_tools(provider)
    if _calendar_configured(config):
        tools.append(_CALENDAR_TOOL_ANTHROPIC if provider == "anthropic" else _CALENDAR_TOOL_OPENAI)
    if _contacts_configured(config):
        tools.append(_CONTACT_LOOKUP_TOOL_ANTHROPIC if provider == "anthropic" else _CONTACT_LOOKUP_TOOL_OPENAI)
    return tools


def _duration_str(seconds: int) -> str:
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    parts = []
    if h:
        parts.append(f"{h}h")
    if m:
        parts.append(f"{m}m")
    if s or not parts:
        parts.append(f"{s}s")
    return " ".join(parts)


async def _execute_timer_tool(user_id: str, args: dict) -> str:
    action = (args.get("action") or "").lower()
    if action == "set":
        label = (args.get("label") or "Timer").strip()[:100]
        duration = int(args.get("duration_seconds") or 0)
        if duration <= 0:
            return "Please specify a duration greater than zero."
        tid = await _db_set_timer(user_id, label, duration)
        return f"Timer '{label}' set for {_duration_str(duration)}. ID: {tid}."
    if action == "list":
        timers = await _db_list_timers(user_id)
        if not timers:
            return "No active timers."
        lines = []
        for t in timers:
            remaining = int((t["fire_at"].replace(tzinfo=None) - datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)).total_seconds())
            lines.append(f"[{t['id']}] {t['label']} — {_duration_str(max(remaining, 0))} remaining")
        return "\n".join(lines)
    if action == "cancel":
        tid = args.get("timer_id")
        if not tid:
            return "Specify a timer ID to cancel."
        ok = await _db_cancel_timer(user_id, int(tid))
        return "Timer cancelled." if ok else "Timer not found or already fired."
    return f"Unknown action: {action}"


async def _execute_reminder_tool(user_id: str, args: dict) -> str:
    action = (args.get("action") or "").lower()
    if action == "set":
        text = (args.get("text") or "").strip()
        fire_at_str = (args.get("fire_at") or "").strip()
        if not text or not fire_at_str:
            return "Specify both reminder text and fire_at datetime."
        try:
            fire_at = datetime.datetime.fromisoformat(fire_at_str.replace("Z", "+00:00"))
        except ValueError:
            return f"Invalid datetime: {fire_at_str}. Use ISO 8601."
        recurring = args.get("recurring_minutes")
        rid = await _db_set_reminder(user_id, text, fire_at, recurring)
        recur = f", repeating every {recurring} min" if recurring else ""
        return f"Reminder set: '{text}' at {fire_at.strftime('%I:%M %p on %b %d')}{recur}. ID: {rid}."
    if action == "list":
        reminders = await _db_list_reminders(user_id)
        if not reminders:
            return "No upcoming reminders."
        return "\n".join(
            f"[{r['id']}] {r['text']} — {r['fire_at'].strftime('%I:%M %p, %b %d')}" + (f" (every {r['recurring_minutes']}m)" if r["recurring_minutes"] else "") for r in reminders
        )
    if action == "cancel":
        rid = args.get("reminder_id")
        if not rid:
            return "Specify a reminder ID to cancel."
        ok = await _db_cancel_reminder(user_id, int(rid))
        return "Reminder cancelled." if ok else "Reminder not found."
    return f"Unknown action: {action}"


async def _execute_news_tool(args: dict) -> str:
    category = (args.get("category") or "general").lower()
    count = min(max(int(args.get("count") or 5), 1), 10)
    url = _NEWS_RSS.get(category, _NEWS_RSS["general"])
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(url, timeout=10, follow_redirects=True)
            resp.raise_for_status()
        root = ET.fromstring(resp.text)
        headlines = [item.findtext("title", "").strip() for item in root.findall(".//item")[:count]]
        headlines = [h for h in headlines if h]
        if not headlines:
            return "No headlines available right now."
        return f"Top {category} news:\n" + "\n".join(f"• {h}" for h in headlines)
    except Exception as e:
        return f"Could not fetch news: {e}"


async def _execute_calendar_tool(config: dict, args: dict) -> str:
    if not _calendar_configured(config):
        return "Calendar is not configured yet."

    action = (args.get("action") or "").lower()
    local_tz = datetime.datetime.now().astimezone().tzinfo
    if action == "list":
        start_raw = (args.get("start") or "").strip()
        end_raw = (args.get("end") or "").strip()
        limit = min(max(int(args.get("limit") or 5), 1), 10)
        if start_raw:
            parsed_start, is_date_start = _parse_calendar_input(start_raw)
            if is_date_start:
                start = datetime.datetime.combine(parsed_start, datetime.time.min, tzinfo=local_tz)
            else:
                start = parsed_start
        else:
            start = datetime.datetime.now().astimezone()
        if end_raw:
            parsed_end, is_date_end = _parse_calendar_input(end_raw)
            if is_date_end:
                end = datetime.datetime.combine(parsed_end, datetime.time.min, tzinfo=local_tz) + datetime.timedelta(days=1)
            else:
                end = parsed_end
        else:
            end = start + datetime.timedelta(days=7)
        if end <= start:
            return "Calendar end must be after the start time."
        try:
            events = await _calendar_events_between(config, start, end, limit=limit)
        except ValueError as e:
            return f"Could not read the calendar: {e}"
        if not events:
            return "No calendar events found in that time range."
        return "Upcoming events:\n" + "\n".join(f"• {_format_calendar_event(event)}" for event in events)

    if action == "create":
        title = (args.get("title") or "").strip()
        start_raw = (args.get("start") or "").strip()
        end_raw = (args.get("end") or "").strip()
        location = (args.get("location") or "").strip()[:200]
        description = (args.get("description") or "").strip()[:1000]
        if not title or not start_raw or not end_raw:
            return "Calendar create needs title, start, and end."
        try:
            start_value, start_is_date = _parse_calendar_input(start_raw)
            end_value, end_is_date = _parse_calendar_input(end_raw)
        except ValueError as e:
            return str(e)
        all_day = bool(args.get("all_day")) or start_is_date or end_is_date
        if all_day:
            start_day = start_value if isinstance(start_value, datetime.date) and not isinstance(start_value, datetime.datetime) else start_value.date()
            end_day = end_value if isinstance(end_value, datetime.date) and not isinstance(end_value, datetime.datetime) else end_value.date()
            if end_day < start_day:
                return "Calendar end must not be before the start date."
            if end_day == start_day:
                end_day += datetime.timedelta(days=1)
            body = _build_calendar_event_ics(title, start_day, end_day, description=description, location=location, all_day=True)
            human_when = f"{start_day.strftime('%a %b %d').replace(' 0', ' ')} (all day)"
        else:
            if end_value <= start_value:
                return "Calendar end must be after the start time."
            body = _build_calendar_event_ics(title, start_value, end_value, description=description, location=location, all_day=False)
            human_when = _format_calendar_event({"title": title, "start": start_value, "end": end_value, "location": location})
        event_url = _dav_join(config["calendar_url"], f"{uuid.uuid4().hex}.ics")
        try:
            response = await _dav_request(
                "PUT",
                event_url,
                config["calendar_username"],
                config["calendar_password"],
                body,
                content_type="text/calendar; charset=utf-8",
                extra_headers={"If-None-Match": "*"},
            )
            _dav_raise_for_status(response, "Calendar create")
        except ValueError as e:
            return f"Could not create the calendar event: {e}"
        return f"Created calendar event '{title}' for {human_when}."

    return f"Unknown action: {action}"


async def _execute_contact_lookup_tool(config: dict, args: dict) -> str:
    if not _contacts_configured(config):
        return "Contacts are not configured yet."
    query = (args.get("query") or "").strip()
    preferred_channel = (args.get("preferred_channel") or "any").lower()
    if preferred_channel not in {"any", "phone", "email"}:
        preferred_channel = "any"
    if not query:
        return "Provide a name, nickname, phone number, or email to search for."
    try:
        matches = await _lookup_contacts(config, query, preferred_channel=preferred_channel, limit=5)
    except ValueError as e:
        return f"Could not search contacts: {e}"
    if not matches:
        return f"No contacts matched '{query}'."
    return f"Contact matches for '{query}':\n" + "\n".join(f"• {_format_contact(contact, preferred_channel)}" for contact in matches)


# ─── PHASE 5: ROUTINES & DEVICE ALERTS ───────────────────────────────────────
MQTT_BROKER = os.environ.get("MQTT_BROKER", "")
MQTT_PORT = int(os.environ.get("MQTT_PORT", "1883"))
MQTT_USER = os.environ.get("MQTT_USER", "")
MQTT_PASSWORD = os.environ.get("MQTT_PASSWORD", "")
Z2M_BASE_TOPIC = os.environ.get("Z2M_BASE_TOPIC", "zigbee2mqtt")

_ROUTINE_TOOL_ANTHROPIC = {
    "name": "manage_routine",
    "description": (
        "Create, list, delete, or run named routines. A routine is a sequence of steps "
        "(ha_service, speak, delay) triggered by voice phrases. "
        "Steps: ha_service={domain,service,entity_id?,service_data?}, speak={text}, delay={seconds}."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": ["create", "list", "delete", "run"]},
            "name": {"type": "string", "description": "Routine name"},
            "trigger_phrases": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Voice phrases that trigger this routine",
            },
            "steps": {
                "type": "array",
                "description": "Ordered steps to execute",
                "items": {"type": "object"},
            },
            "routine_id": {"type": "integer", "description": "ID to delete"},
        },
        "required": ["action"],
    },
}

_ROUTINE_TOOL_OPENAI = {
    "type": "function",
    "function": {
        "name": "manage_routine",
        "description": "Create, list, delete, or run named routines (ha_service/speak/delay steps).",
        "parameters": {
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": ["create", "list", "delete", "run"]},
                "name": {"type": "string"},
                "trigger_phrases": {"type": "array", "items": {"type": "string"}},
                "steps": {"type": "array", "items": {"type": "object"}},
                "routine_id": {"type": "integer"},
            },
            "required": ["action"],
        },
    },
}

_DEVICE_ALERT_TOOL_ANTHROPIC = {
    "name": "manage_device_alert",
    "description": ("Create, list, or delete proactive device alert rules. When an HA entity's state matches the condition, Jarvis speaks the alert message."),
    "input_schema": {
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": ["create", "list", "delete"]},
            "name": {"type": "string", "description": "Human-readable alert name"},
            "entity_id": {"type": "string", "description": "HA entity to monitor, e.g. sensor.front_door"},
            "condition": {
                "type": "string",
                "enum": ["equals", "not_equals", "greater_than", "less_than"],
                "description": "Comparison operator",
            },
            "value": {"type": "string", "description": "Target state value to compare against"},
            "message": {"type": "string", "description": "What Jarvis should say when the alert fires"},
            "cooldown_minutes": {"type": "integer", "description": "Minutes before re-alerting (default 30)"},
            "alert_id": {"type": "integer", "description": "Alert ID to delete"},
        },
        "required": ["action"],
    },
}

_DEVICE_ALERT_TOOL_OPENAI = {
    "type": "function",
    "function": {
        "name": "manage_device_alert",
        "description": "Create, list, or delete proactive HA device alert rules.",
        "parameters": {
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": ["create", "list", "delete"]},
                "name": {"type": "string"},
                "entity_id": {"type": "string"},
                "condition": {"type": "string", "enum": ["equals", "not_equals", "greater_than", "less_than"]},
                "value": {"type": "string"},
                "message": {"type": "string"},
                "cooldown_minutes": {"type": "integer"},
                "alert_id": {"type": "integer"},
            },
            "required": ["action"],
        },
    },
}

_ZIGBEE_TOOL_ANTHROPIC = {
    "name": "zigbee_control",
    "description": ("Send a command to a Zigbee device via Zigbee2MQTT. Use for devices not in Home Assistant. Payload is merged into the set topic."),
    "input_schema": {
        "type": "object",
        "properties": {
            "device": {"type": "string", "description": "Zigbee2MQTT device friendly name"},
            "payload": {"type": "object", "description": 'Command payload, e.g. {"state": "ON", "brightness": 128}'},
        },
        "required": ["device", "payload"],
    },
}

_ZIGBEE_TOOL_OPENAI = {
    "type": "function",
    "function": {
        "name": "zigbee_control",
        "description": "Send a command to a Zigbee device via Zigbee2MQTT.",
        "parameters": {
            "type": "object",
            "properties": {
                "device": {"type": "string"},
                "payload": {"type": "object"},
            },
            "required": ["device", "payload"],
        },
    },
}


def _get_phase5_tools(config: dict, provider: str) -> list:
    tools = []
    if _ha_configured(config):
        if provider == "anthropic":
            tools += [_ROUTINE_TOOL_ANTHROPIC, _DEVICE_ALERT_TOOL_ANTHROPIC]
        else:
            tools += [_ROUTINE_TOOL_OPENAI, _DEVICE_ALERT_TOOL_OPENAI]
    if MQTT_BROKER:
        tools.append(_ZIGBEE_TOOL_ANTHROPIC if provider == "anthropic" else _ZIGBEE_TOOL_OPENAI)
    return tools


async def _run_routine(user_id: str, config: dict, steps: list) -> None:
    sids = _sids_for_user(user_id)
    for i, step in enumerate(steps):
        step_type = (step.get("type") or "").lower()
        try:
            if step_type == "ha_service" and _ha_configured(config):
                await _ha_call_service(
                    config,
                    step.get("domain", ""),
                    step.get("service", ""),
                    step.get("entity_id"),
                    step.get("service_data"),
                )
            elif step_type == "speak":
                text = (step.get("text") or "").strip()
                if text:
                    for sid in sids:
                        await sio.emit("speak_sentence", {"text": text, "seq": i}, to=sid)
            elif step_type == "delay":
                secs = float(step.get("seconds") or 0)
                if secs > 0:
                    await asyncio.sleep(min(secs, 300))
        except Exception as e:
            print(f"[ROUTINE] Step {i} ({step_type}) error: {e}", flush=True)


async def _execute_routine_tool(user_id: str, args: dict, config: dict) -> str:
    action = (args.get("action") or "").lower()
    if action == "create":
        name = (args.get("name") or "").strip()
        if not name:
            return "Specify a routine name."
        phrases = args.get("trigger_phrases") or []
        steps = args.get("steps") or []
        if not steps:
            return "Specify at least one step."
        rid = await _db_create_routine(user_id, name, phrases, steps)
        phrase_str = ", ".join(f'"{p}"' for p in phrases[:3]) if phrases else "none"
        return f"Routine '{name}' created with {len(steps)} step(s). Trigger phrases: {phrase_str}. ID: {rid}."
    if action == "list":
        routines = await _db_list_routines(user_id)
        if not routines:
            return "No routines configured."
        return "\n".join(
            f"[{r['id']}] {r['name']} ({'active' if r['active'] else 'disabled'}) — {len(r['steps'])} steps, phrases: {', '.join(r['trigger_phrases']) or 'none'}" for r in routines
        )
    if action == "delete":
        rid = args.get("routine_id")
        if not rid:
            return "Specify a routine_id to delete."
        ok = await _db_delete_routine(user_id, int(rid))
        return "Routine deleted." if ok else "Routine not found."
    if action == "run":
        name = (args.get("name") or "").strip()
        routines = await _db_list_routines(user_id)
        routine = next((r for r in routines if r["name"].lower() == name.lower()), None)
        if not routine:
            return f"No routine named '{name}'."
        asyncio.create_task(_run_routine(user_id, config, routine["steps"]))
        return f"Running routine '{name}'."
    return f"Unknown action: {action}"


async def _execute_device_alert_tool(user_id: str, args: dict) -> str:
    action = (args.get("action") or "").lower()
    if action == "create":
        name = (args.get("name") or "").strip()
        entity_id = (args.get("entity_id") or "").strip()
        condition = (args.get("condition") or "equals").strip()
        value = str(args.get("value") or "").strip()
        message = (args.get("message") or "").strip()
        cooldown = int(args.get("cooldown_minutes") or 30)
        if not all([name, entity_id, message]):
            return "Specify name, entity_id, and message."
        aid = await _db_create_device_alert(user_id, name, entity_id, condition, value, message, cooldown)
        return f"Alert '{name}' created (ID: {aid}). Will notify when {entity_id} {condition} '{value}'."
    if action == "list":
        alerts = await _db_list_device_alerts(user_id)
        if not alerts:
            return "No alert rules configured."
        return "\n".join(
            f"[{a['id']}] {a['name']} — {a['entity_id']} {a['condition']} '{a['value']}' ({'active' if a['active'] else 'disabled'}, cooldown {a['cooldown_minutes']}m)"
            for a in alerts
        )
    if action == "delete":
        aid = args.get("alert_id")
        if not aid:
            return "Specify an alert_id to delete."
        ok = await _db_delete_device_alert(user_id, int(aid))
        return "Alert deleted." if ok else "Alert not found."
    return f"Unknown action: {action}"


async def _execute_zigbee_tool(args: dict) -> str:
    if not MQTT_BROKER:
        return "MQTT broker not configured."
    device = (args.get("device") or "").strip()
    payload = args.get("payload") or {}
    if not device:
        return "Specify a device name."
    try:
        import aiomqtt

        topic = f"{Z2M_BASE_TOPIC}/{device}/set"
        async with aiomqtt.Client(
            hostname=MQTT_BROKER,
            port=MQTT_PORT,
            username=MQTT_USER or None,
            password=MQTT_PASSWORD or None,
        ) as client:
            await client.publish(topic, json.dumps(payload))
        return f"Command sent to {device}: {payload}"
    except ImportError:
        return "aiomqtt not installed — Zigbee control unavailable."
    except Exception as e:
        return f"MQTT error: {e}"


def _evaluate_alert_condition(state: str, condition: str, value: str) -> bool:
    if condition == "equals":
        return state.lower() == value.lower()
    if condition == "not_equals":
        return state.lower() != value.lower()
    try:
        sn, vn = float(state), float(value)
        if condition == "greater_than":
            return sn > vn
        if condition == "less_than":
            return sn < vn
    except (ValueError, TypeError):
        pass
    return False


async def _device_alert_loop():
    while True:
        await asyncio.sleep(120)
        if not _db_pool:
            continue
        try:
            alerts = await _db_get_active_device_alerts()
            if not alerts:
                continue
            now_utc = datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)
            for alert in alerts:
                uid = alert["user_id"]
                state = _user_states.get(uid)
                if not state or not _ha_configured(state["config"]):
                    continue
                last_fired = alert.get("last_fired")
                if last_fired:
                    elapsed = now_utc - last_fired.replace(tzinfo=None)
                    if elapsed < datetime.timedelta(minutes=alert["cooldown_minutes"]):
                        continue
                entity_state = await _ha_get_entity_state(state["config"], alert["entity_id"])
                if entity_state is None:
                    continue
                if _evaluate_alert_condition(entity_state, alert["condition"], alert["value"]):
                    await _db_update_alert_last_fired(alert["id"])
                    speak = alert["message"]
                    for sid in _sids_for_user(uid):
                        await sio.emit(
                            "device_alert",
                            {"name": alert["name"], "message": speak, "speak": speak},
                            to=sid,
                        )
        except Exception as e:
            print(f"[ALERT] {e}", flush=True)


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
    print("J.A.R.V.I.S. - online. Open http://localhost:5000", flush=True)
    try:
        await asyncio.to_thread(_get_whisper)
        print("[STT] Whisper model ready.", flush=True)
    except Exception as e:
        print(f"[STT] Whisper model load failed: {e}", flush=True)
    t1 = asyncio.create_task(_telemetry_loop())
    t2 = asyncio.create_task(_weather_loop())
    t3 = asyncio.create_task(_meeting_cleanup_loop())
    t4 = asyncio.create_task(_timer_reminder_loop())
    t5 = asyncio.create_task(_device_alert_loop())
    yield
    t1.cancel()
    t2.cancel()
    t3.cancel()
    t4.cancel()
    t5.cancel()
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
        "calendar_configured": _calendar_configured(config),
        "calendar_url": config.get("calendar_url", ""),
        "calendar_username": config.get("calendar_username", ""),
        "contacts_configured": _contacts_configured(config),
        "contacts_url": config.get("contacts_url", ""),
        "contacts_username": config.get("contacts_username", ""),
        "myq_configured": _myq_configured(config),
        "tesla_configured": _tesla_configured(config),
        "tesla_method": config.get("tesla_method", ""),
        "tesla_fleet_enabled": bool(TESLA_CLIENT_ID),
        "spotify_configured": _spotify_configured(config),
        "spotify_client_enabled": bool(SPOTIFY_CLIENT_ID),
        "apple_music_configured": _apple_music_configured(config),
        "apple_music_server_enabled": _apple_music_server_configured(),
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

        speaker_id, speaker_name, speaker_kid_safe = None, None, False
        if _VOICE_ID_OK and _voice_cache:
            embedding = await asyncio.to_thread(_extract_voice_embedding, tmp)
            if embedding:
                speaker_id, speaker_name, speaker_kid_safe = _identify_speaker_from_embedding(embedding)

        return {
            "text": text,
            "speaker_id": speaker_id,
            "speaker_name": speaker_name,
            "speaker_kid_safe": speaker_kid_safe,
        }
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


@fast_app.post("/api/save_pim")
async def api_save_pim(request: Request):
    user_id = _get_current_user(request)
    if not user_id:
        raise HTTPException(401)

    data = await request.json()
    state = await _get_user_state(user_id)
    config = state["config"]

    calendar_url = (data.get("calendar_url") or "").strip()
    calendar_username = (data.get("calendar_username") or "").strip()
    calendar_password = (data.get("calendar_password") or "").strip()
    contacts_url = (data.get("contacts_url") or "").strip()
    contacts_username = (data.get("contacts_username") or "").strip()
    contacts_password = (data.get("contacts_password") or "").strip()
    clear_calendar = bool(data.get("clear_calendar"))
    clear_contacts = bool(data.get("clear_contacts"))

    calendar_to_save = {
        "url": config.get("calendar_url", ""),
        "username": config.get("calendar_username", ""),
        "password": config.get("calendar_password", ""),
    }
    contacts_to_save = {
        "url": config.get("contacts_url", ""),
        "username": config.get("contacts_username", ""),
        "password": config.get("contacts_password", ""),
    }

    if clear_calendar:
        calendar_to_save = {"url": "", "username": "", "password": ""}
    elif calendar_url or calendar_username:
        if not calendar_url or not calendar_username:
            return {"ok": False, "error": "Calendar needs both a server URL and username."}
        effective_calendar_password = calendar_password or config.get("calendar_password", "")
        if not effective_calendar_password:
            return {"ok": False, "error": "Calendar password is required."}
        try:
            resolved = await _resolve_dav_collection(calendar_url, calendar_username, effective_calendar_password, "calendar")
        except ValueError as e:
            return {"ok": False, "error": f"Calendar: {e}"}
        calendar_to_save = {
            "url": resolved["url"],
            "username": calendar_username,
            "password": effective_calendar_password,
        }

    if clear_contacts:
        contacts_to_save = {"url": "", "username": "", "password": ""}
    elif contacts_url or contacts_username:
        if not contacts_url or not contacts_username:
            return {"ok": False, "error": "Contacts needs both a server URL and username."}
        effective_contacts_password = contacts_password or config.get("contacts_password", "")
        if not effective_contacts_password:
            return {"ok": False, "error": "Contacts password is required."}
        try:
            resolved = await _resolve_dav_collection(contacts_url, contacts_username, effective_contacts_password, "addressbook")
        except ValueError as e:
            return {"ok": False, "error": f"Contacts: {e}"}
        contacts_to_save = {
            "url": resolved["url"],
            "username": contacts_username,
            "password": effective_contacts_password,
        }

    async with _get_user_lock(user_id):
        config["calendar_url"] = calendar_to_save["url"]
        config["calendar_username"] = calendar_to_save["username"]
        config["calendar_password"] = calendar_to_save["password"]
        config["contacts_url"] = contacts_to_save["url"]
        config["contacts_username"] = contacts_to_save["username"]
        config["contacts_password"] = contacts_to_save["password"]
        await _db_save_pim_config(
            user_id,
            config["calendar_url"],
            config["calendar_username"],
            config["calendar_password"],
            config["contacts_url"],
            config["contacts_username"],
            config["contacts_password"],
        )

    return {
        "ok": True,
        "calendar_configured": _calendar_configured(config),
        "calendar_url": config.get("calendar_url", ""),
        "calendar_username": config.get("calendar_username", ""),
        "contacts_configured": _contacts_configured(config),
        "contacts_url": config.get("contacts_url", ""),
        "contacts_username": config.get("contacts_username", ""),
    }


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
    return {
        "token": token,
        "url": f"{APP_URL}/api/messages/ingest",
        "apk_url": f"{APP_URL}/download/jarvis-messages.apk",
    }


@fast_app.post("/api/messages/token/regenerate")
async def api_messages_token_regenerate(request: Request):
    user_id = _get_current_user(request)
    if not user_id:
        raise HTTPException(401)
    token = await _db_regenerate_webhook_token(user_id)
    return {
        "token": token,
        "url": f"{APP_URL}/api/messages/ingest",
        "apk_url": f"{APP_URL}/download/jarvis-messages.apk",
    }


@fast_app.get("/download/jarvis-messages.apk")
async def download_apk():
    apk_path = pathlib.Path("static/downloads/jarvis-messages.apk")
    if not apk_path.exists():
        raise HTTPException(404, detail="APK not yet available. Ask your admin to build it from the android/ folder.")
    return FileResponse(apk_path, media_type="application/vnd.android.package-archive", filename="jarvis-messages.apk")


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


@fast_app.post("/api/wake")
async def api_wake(request: Request):
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        raise HTTPException(401)
    token = auth[7:].strip()
    user_id = await _db_find_user_by_token(token)
    if not user_id:
        raise HTTPException(401)

    data = await request.json()
    device_id = (data.get("device_id") or "unknown").strip()[:100]

    now = __import__("time").time()
    if now - _last_wake_time.get(user_id, 0) < _WAKE_DEDUP_WINDOW:
        return {"status": "ignored"}
    _last_wake_time[user_id] = now

    for sid in _sids_for_user(user_id):
        await sio.emit("wake_trigger", {"device_id": device_id}, to=sid)

    return {"status": "ok"}


@fast_app.post("/api/voice/enroll-sample")
async def api_voice_enroll_sample(request: Request, audio: UploadFile = File(...)):
    """Extract a voice embedding from an uploaded audio sample. Returns embedding (not saved)."""
    if not _get_current_user(request):
        raise HTTPException(401)
    if not _VOICE_ID_OK:
        return {"ok": False, "error": "Voice ID unavailable — install librosa on the server."}
    data = await audio.read()
    tmp = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".webm", delete=False) as f:
            f.write(data)
            tmp = f.name
        embedding = await asyncio.to_thread(_extract_voice_embedding, tmp)
        if embedding is None:
            return {"ok": False, "error": "Could not extract embedding."}
        return {"ok": True, "embedding": embedding}
    except Exception as e:
        return {"ok": False, "error": str(e)}
    finally:
        if tmp:
            try:
                os.unlink(tmp)
            except OSError:
                pass


@fast_app.post("/api/voice/enroll-finish")
async def api_voice_enroll_finish(request: Request):
    """Average provided embeddings and save as the user's voiceprint."""
    user_id = _get_current_user(request)
    if not user_id:
        raise HTTPException(401)
    if not _VOICE_ID_OK:
        raise HTTPException(400, "Voice ID unavailable.")
    data = await request.json()
    embeddings = data.get("embeddings", [])
    if not embeddings or len(embeddings) < 2:
        raise HTTPException(400, "At least 2 samples required.")
    import numpy as _np2

    avg = _np2.mean([_np2.array(e) for e in embeddings], axis=0).tolist()
    await _db_save_voice_embedding(user_id, avg)
    await _refresh_voice_cache()
    return {"ok": True}


@fast_app.delete("/api/voice/enrollment")
async def api_voice_enrollment_delete(request: Request):
    user_id = _get_current_user(request)
    if not user_id:
        raise HTTPException(401)
    await _db_clear_voice_embedding(user_id)
    await _refresh_voice_cache()
    return {"ok": True}


@fast_app.patch("/api/user/profile")
async def api_user_profile(request: Request):
    user_id = _get_current_user(request)
    if not user_id:
        raise HTTPException(401)
    data = await request.json()
    if "display_name" in data:
        name = str(data["display_name"]).strip()[:100]
        await _db_set_display_name(user_id, name)
        if user_id in _user_states:
            _user_states[user_id]["config"]["display_name"] = name
        await _refresh_voice_cache()
    if "is_kid_safe" in data:
        value = bool(data["is_kid_safe"])
        await _db_set_kid_safe(user_id, value)
        if user_id in _user_states:
            _user_states[user_id]["config"]["is_kid_safe"] = value
        await _refresh_voice_cache()
    return {"ok": True}


@fast_app.get("/api/household/members")
async def api_household_members(request: Request):
    user_id = _get_current_user(request)
    if not user_id:
        raise HTTPException(401)
    state = await _get_user_state(user_id)
    if state.get("role") != "admin":
        raise HTTPException(403)
    members = await _db_get_household_members()
    return {"members": members}


@fast_app.get("/api/shared-lists")
async def api_shared_lists(request: Request):
    if not _get_current_user(request):
        raise HTTPException(401)
    return {"lists": await _db_get_all_shared_lists()}


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
                user_id,
                refresh_token,
                new_method,
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

    params = urllib.parse.urlencode(
        {
            "client_id": TESLA_CLIENT_ID,
            "redirect_uri": f"{APP_URL}/auth/tesla/callback",
            "response_type": "code",
            "scope": "openid offline_access vehicle_device_data vehicle_cmds vehicle_charging_cmds",
            "state": state_token,
            "code_challenge": code_challenge,
            "code_challenge_method": "S256",
        }
    )
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
                user_id,
                fleet_refresh,
                new_method,
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


# ─── SPOTIFY OAUTH ────────────────────────────────────────────────────────────
@fast_app.get("/api/spotify/auth")
async def api_spotify_auth(request: Request):
    user_id = _get_current_user(request)
    if not user_id:
        raise HTTPException(401)
    if not SPOTIFY_CLIENT_ID:
        raise HTTPException(503, "Spotify not configured — set SPOTIFY_CLIENT_ID and SPOTIFY_CLIENT_SECRET in .env")

    state_token = secrets.token_urlsafe(32)
    _spotify_auth_pending[state_token] = user_id
    if len(_spotify_auth_pending) > 200:
        for k in list(_spotify_auth_pending.keys())[:100]:
            _spotify_auth_pending.pop(k, None)

    params = urllib.parse.urlencode(
        {
            "client_id": SPOTIFY_CLIENT_ID,
            "response_type": "code",
            "redirect_uri": f"{APP_URL}/auth/spotify/callback",
            "scope": _SPOTIFY_SCOPES,
            "state": state_token,
        }
    )
    return RedirectResponse(f"{_SPOTIFY_AUTH_BASE}/authorize?{params}")


@fast_app.get("/auth/spotify/callback")
async def auth_spotify_callback(request: Request):
    code = request.query_params.get("code")
    state_token = request.query_params.get("state")
    user_id = _spotify_auth_pending.pop(state_token, None) if state_token else None
    if not user_id or not code:
        raise HTTPException(400, "Invalid Spotify OAuth callback — state mismatch or missing code")

    try:
        async with httpx.AsyncClient(timeout=15) as c:
            r = await c.post(
                f"{_SPOTIFY_AUTH_BASE}/api/token",
                data={
                    "grant_type": "authorization_code",
                    "code": code,
                    "redirect_uri": f"{APP_URL}/auth/spotify/callback",
                    "client_id": SPOTIFY_CLIENT_ID,
                    "client_secret": SPOTIFY_CLIENT_SECRET,
                },
            )
            r.raise_for_status()
            tokens = r.json()
    except Exception as e:
        raise HTTPException(502, f"Spotify token exchange failed: {e}") from e

    access = tokens.get("access_token", "")
    refresh = tokens.get("refresh_token", "")
    expiry = datetime.datetime.now().timestamp() + tokens.get("expires_in", 3600)

    state = await _get_user_state(user_id)
    config = state["config"]
    async with _get_user_lock(user_id):
        config["spotify_access_token"] = access
        config["spotify_refresh_token"] = refresh
        config["spotify_token_expiry"] = expiry
        await _db_save_spotify_tokens(user_id, access, refresh, expiry)
    _spotify_tokens[user_id] = {"access": access, "expiry": expiry}

    return RedirectResponse("/?spotify_connected=1", status_code=303)


@fast_app.post("/api/spotify/disconnect")
async def api_spotify_disconnect(request: Request):
    user_id = _get_current_user(request)
    if not user_id:
        raise HTTPException(401)
    state = await _get_user_state(user_id)
    config = state["config"]
    async with _get_user_lock(user_id):
        config["spotify_access_token"] = ""
        config["spotify_refresh_token"] = ""
        config["spotify_token_expiry"] = 0.0
        await _db_save_spotify_tokens(user_id, "", "", 0.0)
    _spotify_tokens.pop(user_id, None)
    return {"ok": True}


# ─── APPLE MUSIC API ──────────────────────────────────────────────────────────
@fast_app.get("/api/apple_music/token")
async def api_apple_music_token(request: Request):
    user_id = _get_current_user(request)
    if not user_id:
        raise HTTPException(401)
    if not _apple_music_server_configured():
        return {"token": None, "enabled": False}
    return {"token": _apple_music_dev_token(), "enabled": True}


@fast_app.post("/api/apple_music/user_token")
async def api_apple_music_user_token(request: Request):
    user_id = _get_current_user(request)
    if not user_id:
        raise HTTPException(401)
    body = await request.json()
    token = (body.get("token") or "").strip()
    storefront = (body.get("storefront") or "us").strip().lower()
    state = await _get_user_state(user_id)
    config = state["config"]
    async with _get_user_lock(user_id):
        config["apple_music_user_token"] = token
        config["apple_music_storefront"] = storefront
        async with _pool().acquire() as conn:
            await conn.execute(
                "UPDATE user_configs SET apple_music_user_token=$2, apple_music_storefront=$3 WHERE user_id=$1",
                user_id,
                token,
                storefront,
            )
    return {"ok": True}


@fast_app.post("/api/apple_music/disconnect")
async def api_apple_music_disconnect(request: Request):
    user_id = _get_current_user(request)
    if not user_id:
        raise HTTPException(401)
    state = await _get_user_state(user_id)
    config = state["config"]
    async with _get_user_lock(user_id):
        config["apple_music_user_token"] = ""
        async with _pool().acquire() as conn:
            await conn.execute("UPDATE user_configs SET apple_music_user_token='' WHERE user_id=$1", user_id)
    return {"ok": True}


@sio.on("apple_music_callback")
async def on_apple_music_callback(sid, data):
    cb_id = (data or {}).get("cb")
    result = (data or {}).get("result", "")
    fut = _am_callbacks.get(cb_id)
    if fut and not fut.done():
        fut.set_result(result)


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
def _build_system_prompt(config: dict, speaker_name: str | None = None, is_kid_safe: bool = False) -> str:
    system = JARVIS_SYSTEM
    now = datetime.datetime.now()
    system += f"\n\nCURRENT DATE AND TIME: {now.strftime('%A, %B %d, %Y, %I:%M %p')}."
    system += (
        "\n\nTIMERS & REMINDERS — use manage_timer to set/list/cancel timers by duration. "
        "Use manage_reminder to set/list/cancel reminders at a specific datetime (ISO 8601). "
        "Calculate fire_at from the current date/time above."
    )
    system += "\n\nNEWS — use get_news_headlines to fetch the latest headlines by category (general, technology, science, health, business, sports)."
    if _calendar_configured(config):
        system += (
            "\n\nCALENDAR — use manage_calendar to read upcoming events or create new events in the user's calendar. "
            "Always calculate ISO 8601 start/end values from the current date/time above before calling the tool."
        )
    if _contacts_configured(config):
        system += (
            "\n\nCONTACTS — use lookup_contact to find phone numbers or email addresses for people in the user's address book. "
            "If the user asks to call or text someone, look up the contact first and provide the right number if direct dialing is unavailable."
        )
    if speaker_name and speaker_name != "guest":
        system += f"\n\nYou are currently speaking with {speaker_name}. Address them by name when it feels natural."
    if is_kid_safe:
        system += (
            "\n\nKID-SAFE MODE — You are speaking with a child. Keep all responses age-appropriate, "
            "use simple and encouraging language, and avoid adult topics, violence, or anything "
            "inappropriate for children under 13."
        )
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
    if _spotify_configured(config):
        system += (
            "\n\nSPOTIFY — you are connected to the user's Spotify account. "
            "Use spotify_now_playing to check what's playing, spotify_play/spotify_pause to control playback, "
            "spotify_next/spotify_previous to skip tracks, spotify_volume to adjust volume (0–100), "
            "and spotify_search_and_play to find and play a specific song, artist, album, or playlist."
        )
    if _apple_music_configured(config):
        system += (
            "\n\nAPPLE MUSIC — you are connected to the user's Apple Music account. "
            "Use apple_music_now_playing to check what's playing, apple_music_play/apple_music_pause to control playback, "
            "apple_music_next/apple_music_previous to skip tracks, apple_music_volume to adjust volume (0–100), "
            "and apple_music_search_and_play to find and play a specific song, artist, album, or playlist."
        )
    system += (
        "\n\nSHARED HOUSEHOLD LISTS — use manage_shared_list to add, remove, read, or clear items on "
        "shared lists (shopping, todo, or any custom name). All household members share the same lists."
    )
    if _ha_configured(config):
        system += (
            "\n\nROUTINES — use manage_routine to create, list, delete, or run named automations. "
            "A routine is a sequence of steps: ha_service (call HA), speak (say something), or delay (wait N seconds). "
            "Trigger phrases let users run routines by voice. "
            "\n\nDEVICE ALERTS — use manage_device_alert to create proactive alerts. "
            "When an HA entity's state matches a condition, Jarvis speaks the alert message. "
            "Useful for: garage left open, temperature thresholds, door/window sensors."
        )
    if MQTT_BROKER:
        system += (
            '\n\nZIGBEE — use zigbee_control to send commands directly to Zigbee devices via MQTT. Payload examples: {"state": "ON"}, {"brightness": 128}, {"color_temp": 300}.'
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
    system = _build_system_prompt(
        config,
        speaker_name=state.get("_speaker_name"),
        is_kid_safe=state.get("_speaker_kid_safe", False),
    )
    ha_tools = (
        _get_ha_tools(config, provider)
        + _get_myq_tools(config, provider)
        + _get_tesla_tools(config, provider)
        + _get_spotify_tools(config, provider)
        + _get_apple_music_tools(config, provider)
        + _get_shared_list_tools(provider)
        + _get_phase1_tools(config, provider)
        + _get_phase5_tools(config, provider)
    )
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
                    uid = state.get("user_id", "")
                    if block.name == "manage_shared_list":
                        result = await _execute_shared_list_tool(dict(block.input))
                    elif block.name == "manage_timer":
                        result = await _execute_timer_tool(uid, dict(block.input))
                    elif block.name == "manage_reminder":
                        result = await _execute_reminder_tool(uid, dict(block.input))
                    elif block.name == "get_news_headlines":
                        result = await _execute_news_tool(dict(block.input))
                    elif block.name == "manage_calendar":
                        result = await _execute_calendar_tool(config, dict(block.input))
                    elif block.name == "lookup_contact":
                        result = await _execute_contact_lookup_tool(config, dict(block.input))
                    elif block.name == "manage_routine":
                        result = await _execute_routine_tool(uid, dict(block.input), config)
                    elif block.name == "manage_device_alert":
                        result = await _execute_device_alert_tool(uid, dict(block.input))
                    elif block.name == "zigbee_control":
                        result = await _execute_zigbee_tool(dict(block.input))
                    else:
                        result = await _execute_ha_tool(config, block.name, dict(block.input), uid)
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
                uid = state.get("user_id", "")
                if acc["name"] == "manage_shared_list":
                    result = await _execute_shared_list_tool(args)
                elif acc["name"] == "manage_timer":
                    result = await _execute_timer_tool(uid, args)
                elif acc["name"] == "manage_reminder":
                    result = await _execute_reminder_tool(uid, args)
                elif acc["name"] == "get_news_headlines":
                    result = await _execute_news_tool(args)
                elif acc["name"] == "manage_calendar":
                    result = await _execute_calendar_tool(config, args)
                elif acc["name"] == "lookup_contact":
                    result = await _execute_contact_lookup_tool(config, args)
                elif acc["name"] == "manage_routine":
                    result = await _execute_routine_tool(uid, args, config)
                elif acc["name"] == "manage_device_alert":
                    result = await _execute_device_alert_tool(uid, args)
                elif acc["name"] == "zigbee_control":
                    result = await _execute_zigbee_tool(args)
                else:
                    result = await _execute_ha_tool(config, acc["name"], args, uid)
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


async def _process_message(sid: str, text: str, speaker_name: str | None = None, speaker_kid_safe: bool = False):
    user_id = _sid_to_user.get(sid)
    if not user_id:
        return

    state = await _get_user_state(user_id)
    state["_speaker_name"] = speaker_name
    state["_speaker_kid_safe"] = speaker_kid_safe

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
    if not text:
        return
    lower = text.lower()
    party_on = any(p in lower for p in ("party mode", "let's party", "party time", "activate party", "start the party"))
    party_off = any(p in lower for p in ("end party", "stop party", "deactivate party", "turn off party", "party off"))
    if party_on or party_off:
        active = party_on
        user_id = _sid_to_user.get(sid)
        state = await _get_user_state(user_id) if user_id else {}
        config = state.get("config", {})
        music_line = ""
        if active and user_id:
            if _spotify_configured(config):
                await _spotify_start_party(user_id, config)
                music_line = " Music is on."
            elif _apple_music_configured(config):
                await _apple_music_start_party(user_id)
                music_line = " Music is on."
        token = _create_party_token(user_id) if active and user_id else None
        if not active and user_id:
            _clear_party_tokens(user_id)
        msg = f"Activating party protocols. Excellent taste, sir.{music_line}" if active else "Returning to standard operations. It was fun while it lasted, sir."
        await sio.emit("status", {"state": "speaking"}, to=sid)
        await sio.emit("party_mode", {"active": active, "token": token}, to=sid)
        await sio.emit("speak_sentence", {"text": msg, "seq": 0}, to=sid)
        await sio.emit("response_done", {"text": msg}, to=sid)
        await sio.emit("status", {"state": "idle"}, to=sid)
        return
    speaker_name: str | None = None
    speaker_kid_safe = False
    speaker_id = (data or {}).get("speaker_id", "")
    if speaker_id and speaker_id != "guest" and _voice_cache:
        entry = _voice_cache.get(speaker_id)
        if entry:
            _, speaker_name, speaker_kid_safe = entry
    elif speaker_id == "guest":
        speaker_name = "guest"
    asyncio.create_task(_process_message(sid, text, speaker_name=speaker_name, speaker_kid_safe=speaker_kid_safe))


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


@sio.on("start_party_music")
async def on_start_party_music(sid, data=None):
    user_id = _sid_to_user.get(sid)
    if not user_id:
        return
    state = await _get_user_state(user_id)
    config = state.get("config", {})
    if _spotify_configured(config):
        await _spotify_start_party(user_id, config)
    elif _apple_music_configured(config):
        await _apple_music_start_party(user_id)
    token = _create_party_token(user_id)
    await sio.emit("party_token", {"token": token}, to=sid)


@sio.on("stop_party_music")
async def on_stop_party_music(sid, data=None):
    user_id = _sid_to_user.get(sid)
    if user_id:
        _clear_party_tokens(user_id)


# ─── PARTY GUEST QUEUE ───────────────────────────────────────────────────────
def _get_party_base_url() -> str:
    base = os.getenv("JARVIS_PUBLIC_URL", "").rstrip("/")
    if base:
        return base
    ip = os.getenv("HOST_IP", "localhost")
    return f"http://{ip}:5000"


@fast_app.get("/api/party-token")
async def get_party_token(request: Request):
    user_id = _get_current_user(request)
    if not user_id:
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    token = _create_party_token(user_id)
    url = f"{_get_party_base_url()}/party/{token}"
    return JSONResponse({"token": token, "url": url})


@fast_app.get("/party/{token}", response_class=HTMLResponse)
async def party_guest_page(token: str, request: Request):
    if token not in _party_tokens:
        return HTMLResponse("<html><body style='background:#08111e;color:#7fe9ff;font-family:monospace;padding:40px'><h2>Party has ended.</h2></body></html>", status_code=404)
    return templates.TemplateResponse(request, "party.html")


@fast_app.get("/party/{token}/now_playing")
async def party_now_playing(token: str):
    user_id = _party_tokens.get(token)
    if not user_id:
        raise HTTPException(404)
    state = await _get_user_state(user_id)
    config = state.get("config", {})
    if _spotify_configured(config):
        try:
            r = await _spotify_req("get", "/me/player/currently-playing", user_id, config)
            if r.status_code == 204 or not r.text:
                return {"title": None, "artist": None}
            d = r.json()
            item = d.get("item") or {}
            return {"title": item.get("name"), "artist": ", ".join(a["name"] for a in item.get("artists", []))}
        except Exception:
            return {"title": None, "artist": None}
    if _apple_music_configured(config):
        sids = [sid for sid, uid in _sid_to_user.items() if uid == user_id]
        if sids:
            try:
                raw = await _am_request_callback(sids[0], "now_playing_data", timeout=4.0)
                return json.loads(raw)
            except Exception:
                pass
    return {"title": None, "artist": None}


@fast_app.get("/party/{token}/search")
async def party_search(token: str, q: str = ""):
    user_id = _party_tokens.get(token)
    if not user_id:
        raise HTTPException(404)
    if not q.strip():
        return {"results": []}
    state = await _get_user_state(user_id)
    config = state.get("config", {})
    if _spotify_configured(config):
        try:
            r = await _spotify_req("get", "/search", user_id, config, params={"q": q, "type": "track", "limit": 5})
            r.raise_for_status()
            items = r.json().get("tracks", {}).get("items", [])
            return {"results": [{"id": t["uri"], "title": t["name"], "artist": ", ".join(a["name"] for a in t.get("artists", []))} for t in items]}
        except Exception:
            return {"results": []}
    if _apple_music_configured(config):
        try:
            storefront = config.get("apple_music_storefront") or "us"
            async with httpx.AsyncClient(timeout=10) as c:
                r = await c.get(
                    f"https://api.music.apple.com/v1/catalog/{storefront}/search",
                    headers={"Authorization": f"Bearer {_apple_music_dev_token()}"},
                    params={"term": q, "types": "songs", "limit": 5},
                )
                r.raise_for_status()
            songs = r.json().get("results", {}).get("songs", {}).get("data", [])
            return {"results": [{"id": s["id"], "title": s["attributes"].get("name", ""), "artist": s["attributes"].get("artistName", "")} for s in songs]}
        except Exception:
            return {"results": []}
    return {"results": []}


@fast_app.post("/party/{token}/add")
async def party_add_to_queue(token: str, request: Request):
    user_id = _party_tokens.get(token)
    if not user_id:
        raise HTTPException(404)
    body = await request.json()
    song_id = (body.get("id") or "").strip()
    if not song_id:
        return {"ok": False, "error": "No song ID provided."}
    state = await _get_user_state(user_id)
    config = state.get("config", {})
    if _spotify_configured(config):
        try:
            r = await _spotify_req("post", "/me/player/queue", user_id, config, params={"uri": song_id})
            return {"ok": r.status_code in (200, 204)}
        except Exception as e:
            return {"ok": False, "error": str(e)}
    if _apple_music_configured(config):
        sids = [sid for sid, uid in _sid_to_user.items() if uid == user_id]
        if not sids:
            return {"ok": False, "error": "Host is not connected."}
        try:
            await _am_request_callback(sids[0], "queue_add", {"id": song_id}, timeout=8.0)
            return {"ok": True}
        except Exception as e:
            return {"ok": False, "error": str(e)}
    return {"ok": False, "error": "No music service connected."}


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


async def _timer_reminder_loop():
    while True:
        await asyncio.sleep(30)
        if not _db_pool:
            continue
        try:
            fired_timers = await _db_fire_due_timers()
            for t in fired_timers:
                speak = f"Your {t['label']} timer is done, sir."
                for sid in _sids_for_user(t["user_id"]):
                    await sio.emit("timer_fired", {"label": t["label"], "speak": speak}, to=sid)

            fired_reminders = await _db_fire_due_reminders()
            for r in fired_reminders:
                speak = f"Reminder, sir: {r['text']}."
                for sid in _sids_for_user(r["user_id"]):
                    await sio.emit("reminder_fired", {"text": r["text"], "speak": speak}, to=sid)
        except Exception as e:
            print(f"[TIMER] {e}", flush=True)


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
