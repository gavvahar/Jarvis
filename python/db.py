import json, secrets, datetime, pathlib, asyncpg
from config import DATABASE_URL, MAX_HISTORY, VISION_AWAY_TIMEOUT
from db_helpers import db_exec_affected

_db_pool: asyncpg.Pool | None = None


def _pool() -> asyncpg.Pool:
    assert _db_pool is not None, "Database pool not initialised"
    return _db_pool


_SCHEMA = (pathlib.Path(__file__).parent / "schema.sql").read_text()


async def _db_init():
    global _db_pool
    _db_pool = await asyncpg.create_pool(DATABASE_URL, min_size=2, max_size=10)
    async with _pool().acquire() as conn:
        await conn.execute(_SCHEMA)


def _db_ready() -> bool:
    return _db_pool is not None


async def _db_close():
    global _db_pool
    if _db_pool:
        await _db_pool.close()
        _db_pool = None


# ─── USER CONFIG ──────────────────────────────────────────────────────────────
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
            "SELECT role, provider, api_key, model, base_url, ha_url, ha_token, myq_email, myq_password, tesla_method, tesla_refresh_token, tesla_fleet_refresh_token, spotify_refresh_token, spotify_access_token, spotify_token_expiry, apple_music_user_token, apple_music_storefront, calendar_url, calendar_username, calendar_password, contacts_url, contacts_username, contacts_password, email_host, email_username, email_password, display_name, voice_embedding, is_kid_safe FROM user_configs WHERE user_id = $1",
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
            "email_host": "",
            "email_username": "",
            "email_password": "",
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


async def _db_save_email_config(user_id: str, email_host: str, email_username: str, email_password: str) -> None:
    async with _pool().acquire() as conn:
        await conn.execute(
            "INSERT INTO user_configs (user_id, email, role) VALUES ($1, '', 'user') ON CONFLICT (user_id) DO NOTHING",
            user_id,
        )
        await conn.execute(
            "UPDATE user_configs SET email_host=$2, email_username=$3, email_password=$4, updated_at=NOW() WHERE user_id=$1",
            user_id,
            email_host,
            email_username,
            email_password,
        )


async def _db_get_household_members() -> list:
    async with _pool().acquire() as conn:
        rows = await conn.fetch("SELECT user_id, email, display_name, is_kid_safe, voice_embedding IS NOT NULL AS has_voice FROM user_configs ORDER BY email")
    return [dict(r) for r in rows]


# ─── WEBHOOK TOKENS ───────────────────────────────────────────────────────────
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


# ─── CONVERSATIONS ────────────────────────────────────────────────────────────
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


# ─── VOICE EMBEDDINGS ─────────────────────────────────────────────────────────
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


# ─── SHARED LISTS ─────────────────────────────────────────────────────────────
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


# ─── TIMERS ───────────────────────────────────────────────────────────────────
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
    return await db_exec_affected(_pool(), "UPDATE timers SET fired = TRUE WHERE id = $1 AND user_id = $2 AND fired = FALSE", timer_id, user_id)


async def _db_fire_due_timers() -> list:
    async with _pool().acquire() as conn:
        rows = await conn.fetch("UPDATE timers SET fired = TRUE WHERE fire_at <= NOW() AND fired = FALSE RETURNING user_id, label")
    return [dict(r) for r in rows]


# ─── REMINDERS ────────────────────────────────────────────────────────────────
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
    return await db_exec_affected(_pool(), "UPDATE reminders SET active = FALSE WHERE id = $1 AND user_id = $2", reminder_id, user_id)


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


# ─── DAILY BRIEFING ───────────────────────────────────────────────────────────
async def _db_get_briefing_prefs(user_id: str) -> dict:
    async with _pool().acquire() as conn:
        row = await conn.fetchrow(
            "SELECT briefing_enabled, briefing_morning_time, briefing_evening_time FROM user_configs WHERE user_id = $1",
            user_id,
        )
    if row is None:
        return {"enabled": False, "morning_time": "07:00", "evening_time": "18:00"}
    return {"enabled": row["briefing_enabled"], "morning_time": row["briefing_morning_time"], "evening_time": row["briefing_evening_time"]}


async def _db_set_briefing_prefs(user_id: str, enabled: bool, morning_time: str, evening_time: str) -> None:
    async with _pool().acquire() as conn:
        await conn.execute(
            "UPDATE user_configs SET briefing_enabled=$2, briefing_morning_time=$3, briefing_evening_time=$4 WHERE user_id=$1",
            user_id,
            enabled,
            morning_time,
            evening_time,
        )


async def _db_list_users_due_for_briefing(slot: str, hhmm: str, today: datetime.date) -> list:
    time_col = "briefing_morning_time" if slot == "morning" else "briefing_evening_time"
    sent_col = "briefing_last_morning_sent" if slot == "morning" else "briefing_last_evening_sent"
    # time_col/sent_col are chosen from the fixed "morning"/"evening" branch above, not user input
    async with _pool().acquire() as conn:
        rows = await conn.fetch(
            f"SELECT user_id FROM user_configs WHERE briefing_enabled = TRUE AND {time_col} = $1 AND ({sent_col} IS NULL OR {sent_col} != $2)",
            hhmm,
            today,
        )
    return [r["user_id"] for r in rows]


async def _db_mark_briefing_sent(user_id: str, slot: str, today: datetime.date) -> None:
    sent_col = "briefing_last_morning_sent" if slot == "morning" else "briefing_last_evening_sent"
    async with _pool().acquire() as conn:
        await conn.execute(f"UPDATE user_configs SET {sent_col}=$2 WHERE user_id=$1", user_id, today)


# ─── ROUTINES ─────────────────────────────────────────────────────────────────
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
    return await db_exec_affected(_pool(), "DELETE FROM routines WHERE id = $1 AND user_id = $2", routine_id, user_id)


async def _db_toggle_routine(user_id: str, routine_id: int, active: bool) -> bool:
    return await db_exec_affected(_pool(), "UPDATE routines SET active = $3 WHERE id = $1 AND user_id = $2", routine_id, user_id, active)


# ─── DEVICE ALERTS ────────────────────────────────────────────────────────────
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
    return await db_exec_affected(_pool(), "DELETE FROM device_alert_rules WHERE id = $1 AND user_id = $2", alert_id, user_id)


async def _db_get_active_device_alerts() -> list:
    async with _pool().acquire() as conn:
        rows = await conn.fetch("SELECT id, user_id, name, entity_id, condition, value, message, cooldown_minutes, last_fired FROM device_alert_rules WHERE active = TRUE")
    return [dict(r) for r in rows]


async def _db_update_alert_last_fired(alert_id: int) -> None:
    async with _pool().acquire() as conn:
        await conn.execute("UPDATE device_alert_rules SET last_fired = NOW() WHERE id = $1", alert_id)


# ─── PHONE MESSAGES ───────────────────────────────────────────────────────────
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


# ─── MEETINGS ─────────────────────────────────────────────────────────────────
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


async def _db_search_past_meetings(user_id: str, keywords: list[str], limit: int = 3) -> list:
    if not keywords:
        return []
    async with _pool().acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT id, started_at, notes FROM meetings
            WHERE user_id = $1 AND notes != '' AND notes ILIKE ANY($2::text[])
            ORDER BY started_at DESC
            LIMIT $3
            """,
            user_id,
            [f"%{kw}%" for kw in keywords],
            limit,
        )
    return [dict(r) for r in rows]


# ─── MEETING PREP ─────────────────────────────────────────────────────────────
async def _db_get_meeting_prep_prefs(user_id: str) -> dict:
    async with _pool().acquire() as conn:
        row = await conn.fetchrow(
            "SELECT meeting_prep_enabled, meeting_prep_lead_minutes FROM user_configs WHERE user_id = $1",
            user_id,
        )
    if row is None:
        return {"enabled": False, "lead_minutes": 15}
    return {"enabled": row["meeting_prep_enabled"], "lead_minutes": row["meeting_prep_lead_minutes"]}


async def _db_set_meeting_prep_prefs(user_id: str, enabled: bool, lead_minutes: int) -> None:
    async with _pool().acquire() as conn:
        await conn.execute(
            "UPDATE user_configs SET meeting_prep_enabled=$2, meeting_prep_lead_minutes=$3 WHERE user_id=$1",
            user_id,
            enabled,
            lead_minutes,
        )


async def _db_list_users_for_meeting_prep() -> list:
    async with _pool().acquire() as conn:
        rows = await conn.fetch("SELECT user_id FROM user_configs WHERE meeting_prep_enabled = TRUE")
    return [r["user_id"] for r in rows]


# ─── TTS CLARITY (Phase 10 accessibility) ──────────────────────────────────────
async def _db_get_tts_prefs(user_id: str) -> dict:
    async with _pool().acquire() as conn:
        row = await conn.fetchrow(
            "SELECT tts_rate, tts_pitch, tts_volume FROM user_configs WHERE user_id = $1",
            user_id,
        )
    if row is None:
        return {"rate": 1.0, "pitch": 1.0, "volume": 1.0}
    return {"rate": row["tts_rate"], "pitch": row["tts_pitch"], "volume": row["tts_volume"]}


async def _db_set_tts_prefs(user_id: str, rate: float, pitch: float, volume: float) -> None:
    async with _pool().acquire() as conn:
        await conn.execute(
            "UPDATE user_configs SET tts_rate=$2, tts_pitch=$3, tts_volume=$4 WHERE user_id=$1",
            user_id,
            rate,
            pitch,
            volume,
        )


async def _db_meeting_prep_sent_uids(user_id: str, event_uids: list[str]) -> set[str]:
    if not event_uids:
        return set()
    async with _pool().acquire() as conn:
        rows = await conn.fetch(
            "SELECT event_uid FROM meeting_prep_sent WHERE user_id = $1 AND event_uid = ANY($2::text[])",
            user_id,
            event_uids,
        )
    return {r["event_uid"] for r in rows}


async def _db_mark_meeting_prep_sent(user_id: str, event_uid: str) -> None:
    async with _pool().acquire() as conn:
        await conn.execute(
            "INSERT INTO meeting_prep_sent (user_id, event_uid) VALUES ($1, $2) ON CONFLICT (user_id, event_uid) DO NOTHING",
            user_id,
            event_uid,
        )


# ─── DOORBELL ─────────────────────────────────────────────────────────────────
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


# ─── CAMERAS ──────────────────────────────────────────────────────────────────
async def _db_add_camera(user_id: str, name: str, room: str, source_type: str, source: str) -> int:
    async with _pool().acquire() as conn:
        return await conn.fetchval(
            "INSERT INTO cameras (user_id, name, room, source_type, source) VALUES ($1,$2,$3,$4,$5) RETURNING id",
            user_id,
            name,
            room,
            source_type,
            source,
        )


async def _db_list_cameras(user_id: str) -> list:
    async with _pool().acquire() as conn:
        rows = await conn.fetch(
            "SELECT id, name, room, source_type, source, enabled, privacy FROM cameras WHERE user_id=$1 ORDER BY created_at",
            user_id,
        )
    return [dict(r) for r in rows]


async def _db_delete_camera(user_id: str, camera_id: int) -> bool:
    async with _pool().acquire() as conn:
        r = await conn.execute("DELETE FROM cameras WHERE id=$1 AND user_id=$2", camera_id, user_id)
    return r.split()[-1] != "0"


async def _db_update_camera(user_id: str, camera_id: int, **kwargs) -> bool:
    allowed = {"enabled", "privacy", "name", "room"}
    updates = {k: v for k, v in kwargs.items() if k in allowed}
    if not updates:
        return False
    cols = ", ".join(f"{k}=${i + 3}" for i, k in enumerate(updates))
    async with _pool().acquire() as conn:
        r = await conn.execute(
            f"UPDATE cameras SET {cols} WHERE id=$1 AND user_id=$2",
            camera_id,
            user_id,
            *updates.values(),
        )
    return r.split()[-1] != "0"


async def _db_record_detection(user_id: str, camera_id: int, detected_user_id: str | None, confidence: float, room: str):
    async with _pool().acquire() as conn:
        await conn.execute(
            "INSERT INTO person_detections (user_id, camera_id, detected_user_id, confidence, room) VALUES ($1,$2,$3,$4,$5)",
            user_id,
            camera_id,
            detected_user_id,
            confidence,
            room,
        )


async def _db_record_security_event(user_id: str, camera_id: int | None, event_type: str, room: str, snapshot: bytes | None = None):
    async with _pool().acquire() as conn:
        await conn.execute(
            "INSERT INTO security_events (user_id, camera_id, event_type, room, snapshot) VALUES ($1,$2,$3,$4,$5)",
            user_id,
            camera_id,
            event_type,
            room,
            snapshot,
        )


async def _db_get_recent_security_events(user_id: str, hours: float = 24) -> list:
    async with _pool().acquire() as conn:
        rows = await conn.fetch(
            "SELECT id, event_type, room, detected_at, (snapshot IS NOT NULL) AS has_snapshot "
            "FROM security_events WHERE user_id=$1 AND detected_at > NOW()-$2 ORDER BY detected_at DESC LIMIT 50",
            user_id,
            datetime.timedelta(hours=hours),
        )
    return [{"id": r["id"], "event_type": r["event_type"], "room": r["room"], "detected_at": r["detected_at"].isoformat(), "has_snapshot": r["has_snapshot"]} for r in rows]


async def _db_get_security_event_snapshot(user_id: str, event_id: int) -> bytes | None:
    async with _pool().acquire() as conn:
        return await conn.fetchval("SELECT snapshot FROM security_events WHERE id=$1 AND user_id=$2", event_id, user_id)


async def _db_get_vigil_mode() -> str:
    async with _pool().acquire() as conn:
        mode = await conn.fetchval("SELECT mode FROM vigil_state WHERE id=1")
    return mode or "auto"


async def _db_set_vigil_mode(mode: str, updated_by: str) -> None:
    async with _pool().acquire() as conn:
        await conn.execute(
            "UPDATE vigil_state SET mode=$1, updated_by=$2, updated_at=NOW() WHERE id=1",
            mode,
            updated_by,
        )


async def _db_add_push_subscription(user_id: str, endpoint: str, p256dh: str, auth: str) -> None:
    async with _pool().acquire() as conn:
        await conn.execute(
            "INSERT INTO push_subscriptions (user_id, endpoint, p256dh, auth) VALUES ($1,$2,$3,$4) "
            "ON CONFLICT (user_id, endpoint) DO UPDATE SET p256dh=EXCLUDED.p256dh, auth=EXCLUDED.auth",
            user_id,
            endpoint,
            p256dh,
            auth,
        )


async def _db_remove_push_subscription(user_id: str, endpoint: str) -> None:
    async with _pool().acquire() as conn:
        await conn.execute("DELETE FROM push_subscriptions WHERE user_id=$1 AND endpoint=$2", user_id, endpoint)


async def _db_get_push_subscriptions(user_id: str) -> list:
    async with _pool().acquire() as conn:
        rows = await conn.fetch("SELECT endpoint, p256dh, auth FROM push_subscriptions WHERE user_id=$1", user_id)
    return [{"endpoint": r["endpoint"], "p256dh": r["p256dh"], "auth": r["auth"]} for r in rows]


# ─── FACE / PRESENCE ──────────────────────────────────────────────────────────
def _infer_activity(room: str, hour: int) -> str:
    r = (room or "").lower()
    if any(w in r for w in ("bedroom", "bed room", "master")):
        return "sleeping" if (hour >= 22 or hour < 7) else "resting"
    if "kitchen" in r:
        return "cooking"
    if any(w in r for w in ("gym", "exercise", "workout", "fitness")):
        return "exercising"
    if any(w in r for w in ("office", "study", "desk")):
        return "working"
    if any(w in r for w in ("bathroom", "bath", "restroom", "toilet")):
        return "unavailable"
    return "home"


async def _db_get_who_is_home() -> list:
    cutoff = datetime.timedelta(seconds=VISION_AWAY_TIMEOUT)
    hour = datetime.datetime.now().hour
    async with _pool().acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT u.user_id, u.display_name, u.is_home, u.last_seen_at,
                   (SELECT room FROM person_detections d WHERE d.detected_user_id=u.user_id
                    ORDER BY detected_at DESC LIMIT 1) AS room
            FROM user_configs u
            WHERE u.is_home = TRUE AND u.last_seen_at > NOW()-$1
            """,
            cutoff,
        )
    return [
        {
            "user_id": r["user_id"],
            "name": r["display_name"] or r["user_id"],
            "last_seen_at": r["last_seen_at"].isoformat() if r["last_seen_at"] else None,
            "room": r["room"] or "",
            "activity": _infer_activity(r["room"] or "", hour),
        }
        for r in rows
    ]


async def _db_get_all_face_embeddings() -> dict:
    async with _pool().acquire() as conn:
        rows = await conn.fetch("SELECT user_id, display_name, face_embedding FROM user_configs WHERE face_embedding IS NOT NULL")
    return {r["user_id"]: (r["face_embedding"], r["display_name"] or r["user_id"]) for r in rows}


async def _db_save_face_embedding(user_id: str, embedding: list):
    async with _pool().acquire() as conn:
        await conn.execute(
            "UPDATE user_configs SET face_embedding=$2 WHERE user_id=$1",
            user_id,
            json.dumps(embedding),
        )


async def _db_clear_face_embedding(user_id: str):
    async with _pool().acquire() as conn:
        await conn.execute("UPDATE user_configs SET face_embedding=NULL WHERE user_id=$1", user_id)


async def _db_update_presence(user_id: str, is_home: bool):
    async with _pool().acquire() as conn:
        await conn.execute(
            "UPDATE user_configs SET is_home=$2, last_seen_at=NOW() WHERE user_id=$1",
            user_id,
            is_home,
        )


# ─── HABITS (PRESENCE EVENTS) ─────────────────────────────────────────────────
async def _db_record_presence_event(user_id: str, event_type: str, occurred_at: datetime.datetime | None = None) -> None:
    async with _pool().acquire() as conn:
        await conn.execute(
            "INSERT INTO presence_events (user_id, event_type, occurred_at) VALUES ($1, $2, COALESCE($3, NOW()))",
            user_id,
            event_type,
            occurred_at,
        )


async def _db_get_presence_events(user_id: str, event_type: str, since_days: int = 60, limit: int = 200) -> list:
    async with _pool().acquire() as conn:
        rows = await conn.fetch(
            "SELECT occurred_at FROM presence_events WHERE user_id=$1 AND event_type=$2 AND occurred_at > NOW() - ($3 || ' days')::interval ORDER BY occurred_at DESC LIMIT $4",
            user_id,
            event_type,
            str(since_days),
            limit,
        )
    return [r["occurred_at"] for r in rows]


async def _db_has_presence_event_today(user_id: str, event_type: str, today: datetime.date) -> bool:
    async with _pool().acquire() as conn:
        return bool(await conn.fetchval("SELECT EXISTS(SELECT 1 FROM presence_events WHERE user_id=$1 AND event_type=$2 AND occurred_at::date=$3)", user_id, event_type, today))


async def _db_get_habit_nudge_prefs(user_id: str) -> dict:
    async with _pool().acquire() as conn:
        row = await conn.fetchrow("SELECT habit_nudges_enabled FROM user_configs WHERE user_id = $1", user_id)
    return {"enabled": bool(row["habit_nudges_enabled"])} if row else {"enabled": False}


async def _db_set_habit_nudges_enabled(user_id: str, enabled: bool) -> None:
    async with _pool().acquire() as conn:
        await conn.execute("UPDATE user_configs SET habit_nudges_enabled=$2 WHERE user_id=$1", user_id, enabled)


async def _db_list_users_for_habit_nudge(today: datetime.date) -> list:
    async with _pool().acquire() as conn:
        rows = await conn.fetch(
            "SELECT user_id FROM user_configs WHERE habit_nudges_enabled = TRUE AND (habit_nudge_last_sent IS NULL OR habit_nudge_last_sent != $1)",
            today,
        )
    return [r["user_id"] for r in rows]


async def _db_mark_habit_nudge_sent(user_id: str, today: datetime.date) -> None:
    async with _pool().acquire() as conn:
        await conn.execute("UPDATE user_configs SET habit_nudge_last_sent=$2 WHERE user_id=$1", user_id, today)


# ─── TRAVEL ALERTS ────────────────────────────────────────────────────────────
async def _db_add_travel_trip(user_id: str, airline: str, flight_number: str, flight_date: datetime.date) -> int:
    async with _pool().acquire() as conn:
        return await conn.fetchval(
            "INSERT INTO travel_trips (user_id, airline, flight_number, flight_date) VALUES ($1,$2,$3,$4) RETURNING id",
            user_id,
            airline,
            flight_number,
            flight_date,
        )


async def _db_list_travel_trips(user_id: str) -> list:
    async with _pool().acquire() as conn:
        rows = await conn.fetch(
            "SELECT id, airline, flight_number, flight_date, status, gate, terminal, departure_time, active "
            "FROM travel_trips WHERE user_id = $1 ORDER BY flight_date DESC, id DESC",
            user_id,
        )
    return [dict(r) for r in rows]


async def _db_delete_travel_trip(user_id: str, trip_id: int) -> bool:
    return await db_exec_affected(_pool(), "DELETE FROM travel_trips WHERE id = $1 AND user_id = $2", trip_id, user_id)


async def _db_get_travel_trip(user_id: str, trip_id: int) -> dict | None:
    async with _pool().acquire() as conn:
        row = await conn.fetchrow(
            "SELECT id, airline, flight_number, flight_date, status, gate, terminal, departure_time, active FROM travel_trips WHERE id = $1 AND user_id = $2",
            trip_id,
            user_id,
        )
    return dict(row) if row else None


async def _db_get_active_travel_trips() -> list:
    async with _pool().acquire() as conn:
        rows = await conn.fetch("SELECT id, user_id, airline, flight_number, flight_date, status, gate, terminal, departure_time FROM travel_trips WHERE active = TRUE")
    return [dict(r) for r in rows]


async def _db_update_travel_trip(trip_id: int, status: str, gate: str, terminal: str, departure_time: datetime.datetime | None) -> None:
    async with _pool().acquire() as conn:
        await conn.execute(
            "UPDATE travel_trips SET status=$2, gate=$3, terminal=$4, departure_time=$5, last_checked_at=NOW() WHERE id=$1",
            trip_id,
            status,
            gate,
            terminal,
            departure_time,
        )


async def _db_deactivate_travel_trip(trip_id: int) -> None:
    async with _pool().acquire() as conn:
        await conn.execute("UPDATE travel_trips SET active = FALSE WHERE id = $1", trip_id)


# ─── EMAIL TRIAGE ──────────────────────────────────────────────────────────────
async def _db_get_email_triage_prefs(user_id: str) -> dict:
    async with _pool().acquire() as conn:
        row = await conn.fetchrow("SELECT email_triage_enabled FROM user_configs WHERE user_id = $1", user_id)
    return {"enabled": bool(row["email_triage_enabled"])} if row else {"enabled": False}


async def _db_set_email_triage_enabled(user_id: str, enabled: bool) -> None:
    async with _pool().acquire() as conn:
        await conn.execute("UPDATE user_configs SET email_triage_enabled=$2 WHERE user_id=$1", user_id, enabled)


async def _db_list_users_for_email_triage() -> list:
    async with _pool().acquire() as conn:
        rows = await conn.fetch("SELECT user_id FROM user_configs WHERE email_triage_enabled = TRUE")
    return [r["user_id"] for r in rows]


async def _db_uids_already_classified(user_id: str, uids: list[str]) -> set[str]:
    if not uids:
        return set()
    async with _pool().acquire() as conn:
        rows = await conn.fetch("SELECT uid FROM email_triage WHERE user_id = $1 AND uid = ANY($2::text[])", user_id, uids)
    return {r["uid"] for r in rows}


async def _db_insert_email_triage(user_id: str, uid: str, sender: str, subject: str, summary: str, important: bool) -> None:
    async with _pool().acquire() as conn:
        await conn.execute(
            "INSERT INTO email_triage (user_id, uid, sender, subject, summary, important) VALUES ($1,$2,$3,$4,$5,$6) ON CONFLICT (user_id, uid) DO NOTHING",
            user_id,
            uid,
            sender,
            subject,
            summary,
            important,
        )


async def _db_list_email_triage(user_id: str, limit: int = 20) -> list:
    async with _pool().acquire() as conn:
        rows = await conn.fetch(
            "SELECT id, sender, subject, summary, important, classified_at FROM email_triage WHERE user_id = $1 ORDER BY classified_at DESC LIMIT $2",
            user_id,
            limit,
        )
    return [dict(r) for r in rows]


# ─── PACKAGE TRACKING ──────────────────────────────────────────────────────────
async def _db_get_package_tracking_prefs(user_id: str) -> dict:
    async with _pool().acquire() as conn:
        row = await conn.fetchrow("SELECT package_tracking_enabled FROM user_configs WHERE user_id = $1", user_id)
    return {"enabled": bool(row["package_tracking_enabled"])} if row else {"enabled": False}


async def _db_set_package_tracking_enabled(user_id: str, enabled: bool) -> None:
    async with _pool().acquire() as conn:
        await conn.execute("UPDATE user_configs SET package_tracking_enabled=$2 WHERE user_id=$1", user_id, enabled)


async def _db_list_users_for_package_tracking() -> list:
    async with _pool().acquire() as conn:
        rows = await conn.fetch("SELECT user_id FROM user_configs WHERE package_tracking_enabled = TRUE")
    return [r["user_id"] for r in rows]


async def _db_uids_already_tracked(user_id: str, uids: list[str]) -> set[str]:
    if not uids:
        return set()
    async with _pool().acquire() as conn:
        rows = await conn.fetch("SELECT uid FROM package_events WHERE user_id = $1 AND uid = ANY($2::text[])", user_id, uids)
    return {r["uid"] for r in rows}


async def _db_insert_package_event(user_id: str, uid: str, carrier: str, status: str, tracking_number: str) -> None:
    async with _pool().acquire() as conn:
        await conn.execute(
            "INSERT INTO package_events (user_id, uid, carrier, status, tracking_number) VALUES ($1,$2,$3,$4,$5) ON CONFLICT (user_id, uid) DO NOTHING",
            user_id,
            uid,
            carrier,
            status,
            tracking_number,
        )


async def _db_list_package_events(user_id: str, limit: int = 20) -> list:
    async with _pool().acquire() as conn:
        rows = await conn.fetch(
            "SELECT id, carrier, status, tracking_number, detected_at FROM package_events WHERE user_id = $1 ORDER BY detected_at DESC LIMIT $2",
            user_id,
            limit,
        )
    return [dict(r) for r in rows]
