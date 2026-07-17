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


# ─── FINANCE / PLAID ──────────────────────────────────────────────────────────
async def _db_add_plaid_item(user_id: str, item_id: str, access_token: str, institution_id: str, institution_name: str) -> int:
    async with _pool().acquire() as conn:
        return await conn.fetchval(
            "INSERT INTO plaid_items (user_id, item_id, access_token, institution_id, institution_name) VALUES ($1,$2,$3,$4,$5) RETURNING id",
            user_id,
            item_id,
            access_token,
            institution_id,
            institution_name,
        )


async def _db_list_plaid_items(user_id: str) -> list:
    async with _pool().acquire() as conn:
        rows = await conn.fetch(
            "SELECT id, item_id, institution_name, status, created_at FROM plaid_items WHERE user_id=$1 ORDER BY created_at",
            user_id,
        )
    return [dict(r) for r in rows]


async def _db_list_all_plaid_items() -> list:
    async with _pool().acquire() as conn:
        rows = await conn.fetch("SELECT id, user_id, item_id, access_token, cursor, status FROM plaid_items ORDER BY id")
    return [dict(r) for r in rows]


async def _db_get_plaid_item(user_id: str, item_pk: int) -> dict | None:
    async with _pool().acquire() as conn:
        row = await conn.fetchrow(
            "SELECT id, item_id, access_token, institution_name, cursor, status FROM plaid_items WHERE id=$1 AND user_id=$2",
            item_pk,
            user_id,
        )
    return dict(row) if row else None


async def _db_delete_plaid_item(user_id: str, item_pk: int) -> bool:
    async with _pool().acquire() as conn:
        r = await conn.execute("DELETE FROM plaid_items WHERE id=$1 AND user_id=$2", item_pk, user_id)
    return r.split()[-1] != "0"


async def _db_update_plaid_cursor(item_pk: int, cursor: str) -> None:
    async with _pool().acquire() as conn:
        await conn.execute("UPDATE plaid_items SET cursor=$2, updated_at=NOW() WHERE id=$1", item_pk, cursor)


async def _db_mark_plaid_item_status(item_pk: int, status: str) -> None:
    async with _pool().acquire() as conn:
        await conn.execute("UPDATE plaid_items SET status=$2, updated_at=NOW() WHERE id=$1", item_pk, status)


async def _db_upsert_plaid_accounts(user_id: str, item_pk: int, accounts: list) -> None:
    async with _pool().acquire() as conn:
        for a in accounts:
            await conn.execute(
                """
                INSERT INTO plaid_accounts (user_id, item_id, account_id, name, official_name, mask, type, subtype, balance_current, balance_available, balance_limit, iso_currency, updated_at)
                VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,NOW())
                ON CONFLICT (account_id) DO UPDATE SET
                    name=EXCLUDED.name, official_name=EXCLUDED.official_name, mask=EXCLUDED.mask,
                    type=EXCLUDED.type, subtype=EXCLUDED.subtype,
                    balance_current=EXCLUDED.balance_current, balance_available=EXCLUDED.balance_available,
                    balance_limit=EXCLUDED.balance_limit, iso_currency=EXCLUDED.iso_currency, updated_at=NOW()
                """,
                user_id,
                item_pk,
                a["account_id"],
                a["name"],
                a["official_name"],
                a["mask"],
                a["type"],
                a["subtype"],
                a["balance_current"],
                a["balance_available"],
                a["balance_limit"],
                a["iso_currency"],
            )


async def _db_list_plaid_accounts(user_id: str, account_hint: str | None = None) -> list:
    async with _pool().acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT id, item_id, account_id, name, official_name, mask, type, subtype,
                   balance_current, balance_available, balance_limit, iso_currency
            FROM plaid_accounts
            WHERE user_id=$1 AND ($2::text IS NULL OR name ILIKE '%'||$2||'%' OR mask = $2)
            ORDER BY name
            """,
            user_id,
            account_hint,
        )
    return [dict(r) for r in rows]


async def _db_upsert_plaid_transactions(user_id: str, upserts: list, removed_ids: list) -> None:
    async with _pool().acquire() as conn:
        for t in upserts:
            await conn.execute(
                """
                INSERT INTO plaid_transactions (user_id, account_id, transaction_id, amount, iso_currency, date, merchant_name, name, category, personal_finance_category, pending)
                VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11)
                ON CONFLICT (transaction_id) DO UPDATE SET
                    amount=EXCLUDED.amount, iso_currency=EXCLUDED.iso_currency, date=EXCLUDED.date,
                    merchant_name=EXCLUDED.merchant_name, name=EXCLUDED.name, category=EXCLUDED.category,
                    personal_finance_category=EXCLUDED.personal_finance_category, pending=EXCLUDED.pending
                """,
                user_id,
                t["account_id"],
                t["transaction_id"],
                t["amount"],
                t["iso_currency"],
                t["date"],
                t["merchant_name"],
                t["name"],
                t["category"],
                t["personal_finance_category"],
                t["pending"],
            )
        if removed_ids:
            await conn.execute("DELETE FROM plaid_transactions WHERE transaction_id = ANY($1::text[])", removed_ids)


async def _db_get_recent_transactions(user_id: str, account_hint: str | None = None, days: float = 7, limit: int = 10) -> list:
    async with _pool().acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT t.id, t.transaction_id, t.amount, t.date, t.merchant_name, t.name,
                   t.category, t.personal_finance_category, t.category_override, t.pending
            FROM plaid_transactions t
            JOIN plaid_accounts pa ON pa.account_id = t.account_id
            WHERE t.user_id=$1 AND t.date > (CURRENT_DATE - $2::int)
              AND ($3::text IS NULL OR pa.name ILIKE '%'||$3||'%' OR pa.mask = $3)
            ORDER BY t.date DESC, t.id DESC
            LIMIT $4
            """,
            user_id,
            int(days),
            account_hint,
            int(limit),
        )
    return [dict(r) for r in rows]


async def _db_get_spending_by_category(user_id: str, account_hint: str | None = None, days: float = 30) -> list:
    async with _pool().acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT COALESCE(NULLIF(t.category_override, ''), NULLIF(t.category, ''), 'Uncategorized') AS category,
                   SUM(t.amount) AS total
            FROM plaid_transactions t
            JOIN plaid_accounts pa ON pa.account_id = t.account_id
            WHERE t.user_id=$1 AND t.date > (CURRENT_DATE - $2::int) AND t.amount > 0
              AND ($3::text IS NULL OR pa.name ILIKE '%'||$3||'%' OR pa.mask = $3)
            GROUP BY category
            ORDER BY total DESC
            """,
            user_id,
            int(days),
            account_hint,
        )
    return [{"category": r["category"], "total": r["total"]} for r in rows]


async def _db_find_transaction_by_merchant(user_id: str, merchant: str) -> dict | None:
    async with _pool().acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT id, transaction_id, amount, date, merchant_name, name
            FROM plaid_transactions
            WHERE user_id=$1 AND (merchant_name ILIKE '%'||$2||'%' OR name ILIKE '%'||$2||'%')
            ORDER BY date DESC, id DESC
            LIMIT 1
            """,
            user_id,
            merchant,
        )
    return dict(row) if row else None


async def _db_set_transaction_category_override(user_id: str, transaction_pk: int, category: str) -> bool:
    async with _pool().acquire() as conn:
        r = await conn.execute(
            "UPDATE plaid_transactions SET category_override=$3 WHERE id=$1 AND user_id=$2",
            transaction_pk,
            user_id,
            category,
        )
    return r.split()[-1] != "0"
