import asyncio, datetime, statistics

from db import (
    _db_get_habit_nudge_prefs,
    _db_get_presence_events,
    _db_has_presence_event_today,
    _db_list_users_for_habit_nudge,
    _db_mark_habit_nudge_sent,
    _db_ready,
    _db_set_habit_nudges_enabled,
)
from integrations.push import _send_push
from tool_schemas import anthropic_tools_to_openai

_sio = None
_sids_fn = None

_MIN_SAMPLES = 3
_NUDGE_WINDOW_MINUTES = 10

_EVENT_LABELS = {"departed": "leave home", "arrived": "arrive home"}


def init(sio, sids_fn) -> None:
    global _sio, _sids_fn
    _sio = sio
    _sids_fn = sids_fn


HABITS_TOOL_ANTHROPIC = {
    "name": "get_habits",
    "description": "Get detected patterns in when the user typically leaves home or arrives home, learned from camera presence history over the last 60 days.",
    "input_schema": {
        "type": "object",
        "properties": {
            "event_type": {"type": "string", "enum": ["departed", "arrived"], "description": "Which pattern to look up; omit to get both."},
        },
        "required": [],
    },
}

HABITS_TOOL_OPENAI = anthropic_tools_to_openai([HABITS_TOOL_ANTHROPIC])[0]

_HABITS_TOOL_NAMES = {"get_habits"}


def _get_habits_tools(provider: str) -> list:
    return [HABITS_TOOL_ANTHROPIC] if provider == "anthropic" else [HABITS_TOOL_OPENAI]


def _bucket_for(dt: datetime.datetime) -> str:
    return "weekend" if dt.astimezone().weekday() >= 5 else "weekday"


def _minutes_since_midnight(dt: datetime.datetime) -> int:
    local = dt.astimezone()
    return local.hour * 60 + local.minute


def _minutes_to_clock(minutes: float) -> str:
    total = int(round(minutes)) % 1440
    hour, minute = divmod(total, 60)
    period = "AM" if hour < 12 else "PM"
    hour12 = hour % 12 or 12
    return f"{hour12}:{minute:02d} {period}"


async def _analyze_habit(user_id: str, event_type: str) -> dict | None:
    events = await _db_get_presence_events(user_id, event_type)
    if len(events) < _MIN_SAMPLES:
        return None
    buckets: dict[str, list[int]] = {"weekday": [], "weekend": []}
    for dt in events:
        buckets[_bucket_for(dt)].append(_minutes_since_midnight(dt))
    result = {bucket: {"typical_minutes": statistics.median(mins), "sample_size": len(mins)} for bucket, mins in buckets.items() if len(mins) >= _MIN_SAMPLES}
    return result or None


def _format_habit_line(event_type: str, habit: dict) -> str:
    verb = _EVENT_LABELS.get(event_type, event_type)
    bits = [f"around {_minutes_to_clock(habit[bucket]['typical_minutes'])} on {bucket}s" for bucket in ("weekday", "weekend") if bucket in habit]
    if not bits:
        return ""
    return f"You usually {verb} " + " and ".join(bits) + "."


async def _execute_habits_tool(user_id: str, args: dict) -> str:
    event_type = (args.get("event_type") or "").lower()
    types = [event_type] if event_type in _EVENT_LABELS else list(_EVENT_LABELS)
    lines = []
    for et in types:
        habit = await _analyze_habit(user_id, et)
        lines.append(_format_habit_line(et, habit) if habit else f"Not enough data yet to detect a pattern for when you {_EVENT_LABELS[et]}.")
    return "\n".join(lines)


async def _get_habit_prefs(user_id: str) -> dict:
    prefs = await _db_get_habit_nudge_prefs(user_id)
    return {
        "enabled": prefs["enabled"],
        "departed": await _analyze_habit(user_id, "departed"),
        "arrived": await _analyze_habit(user_id, "arrived"),
    }


async def _set_habit_prefs(user_id: str, data: dict) -> dict:
    enabled = bool(data.get("enabled"))
    await _db_set_habit_nudges_enabled(user_id, enabled)
    return {"ok": True, "enabled": enabled}


async def _maybe_nudge(user_id: str, today: datetime.date) -> None:
    habit = await _analyze_habit(user_id, "departed")
    if habit is None:
        return
    now = datetime.datetime.now().astimezone()
    bucket = "weekend" if now.weekday() >= 5 else "weekday"
    if bucket not in habit:
        return
    target_minutes = habit[bucket]["typical_minutes"]
    now_minutes = now.hour * 60 + now.minute
    if not (target_minutes <= now_minutes < target_minutes + _NUDGE_WINDOW_MINUTES):
        return
    if await _db_has_presence_event_today(user_id, "departed", today):
        return
    speak = f"You usually leave around {_minutes_to_clock(target_minutes)}, sir — just a heads up."
    if _sio is not None and _sids_fn is not None:
        for sid in _sids_fn(user_id):
            await _sio.emit("habit_nudge", {"event_type": "departed", "speak": speak}, to=sid)
    await _send_push(user_id, "Time to head out?", speak)
    await _db_mark_habit_nudge_sent(user_id, today)


async def _habit_nudge_loop() -> None:
    while True:
        await asyncio.sleep(300)
        if not _db_ready():
            continue
        try:
            today = datetime.datetime.now().astimezone().date()
            for user_id in await _db_list_users_for_habit_nudge(today):
                await _maybe_nudge(user_id, today)
        except Exception as e:
            print(f"[HABITS] {e}", flush=True)
