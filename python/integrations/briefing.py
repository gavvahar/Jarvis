import asyncio, datetime, re

from fastapi import HTTPException

from db import (
    _db_get_briefing_prefs,
    _db_list_reminders,
    _db_list_users_due_for_briefing,
    _db_load_config,
    _db_mark_briefing_sent,
    _db_ready,
    _db_set_briefing_prefs,
)
from integrations.pim.calendar import _calendar_configured, _calendar_events_between, _format_calendar_event
from integrations.pim.timers import _fetch_news_headlines
from integrations.push import _send_push
from tool_schemas import anthropic_tools_to_openai

_sio = None
_sids_fn = None
_location_context: dict = {}

_VALID_SLOTS = {"morning", "evening"}
_TIME_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")


def init(sio, sids_fn, location_context: dict) -> None:
    global _sio, _sids_fn, _location_context
    _sio = sio
    _sids_fn = sids_fn
    _location_context = location_context


BRIEFING_TOOL_ANTHROPIC = {
    "name": "manage_briefing",
    "description": (
        "Enable, disable, reschedule, check the status of, or immediately deliver the daily briefing — a spoken "
        "summary of current weather, today's remaining calendar events, today's reminders, and top news headlines."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": ["enable", "disable", "set_time", "status", "now"]},
            "slot": {"type": "string", "enum": ["morning", "evening"], "description": "Which briefing to reschedule (required for set_time)."},
            "time": {"type": "string", "description": "24-hour HH:MM time (required for set_time), e.g. '07:30'."},
        },
        "required": ["action"],
    },
}

BRIEFING_TOOL_OPENAI = anthropic_tools_to_openai([BRIEFING_TOOL_ANTHROPIC])[0]

_BRIEFING_TOOL_NAMES = {"manage_briefing"}


def _get_briefing_tools(provider: str) -> list:
    return [BRIEFING_TOOL_ANTHROPIC] if provider == "anthropic" else [BRIEFING_TOOL_OPENAI]


def _weather_line() -> str:
    if not _location_context.get("temp_f"):
        return ""
    city = _location_context.get("city") or ""
    where = f" in {city}" if city else ""
    condition = _location_context.get("condition") or "—"
    return f"It's currently {_location_context['temp_f']}°F and {condition}{where}."


async def _calendar_line(config: dict) -> str:
    if not _calendar_configured(config):
        return ""
    now = datetime.datetime.now().astimezone()
    end_of_day = now.replace(hour=23, minute=59, second=59, microsecond=0)
    try:
        events = await _calendar_events_between(config, now, end_of_day, limit=5)
    except Exception:
        return ""
    if not events:
        return "Nothing left on your calendar today."
    return "Today's calendar: " + "; ".join(_format_calendar_event(e) for e in events) + "."


async def _reminders_line(user_id: str) -> str:
    try:
        reminders = await _db_list_reminders(user_id)
    except Exception:
        return ""
    today = datetime.datetime.now().astimezone().date()
    todays = [r for r in reminders if r["fire_at"].astimezone().date() == today]
    if not todays:
        return ""
    bits = [f"{r['text']} at {r['fire_at'].astimezone().strftime('%I:%M %p').lstrip('0')}" for r in todays]
    return "Reminders today: " + "; ".join(bits) + "."


async def _news_line() -> str:
    try:
        headlines = await _fetch_news_headlines("general", 3)
    except Exception:
        return ""
    if not headlines:
        return ""
    return "In the news: " + "; ".join(headlines) + "."


async def _compose_briefing(user_id: str, config: dict) -> str:
    parts = [p for p in [_weather_line(), await _calendar_line(config), await _reminders_line(user_id), await _news_line()] if p]
    return " ".join(parts) if parts else "Nothing new to report, sir."


async def _get_briefing_prefs(user_id: str) -> dict:
    return await _db_get_briefing_prefs(user_id)


async def _set_briefing_prefs(user_id: str, data: dict) -> dict:
    enabled = bool(data.get("enabled"))
    morning_time = (data.get("morning_time") or "07:00").strip()
    evening_time = (data.get("evening_time") or "18:00").strip()
    if not _TIME_RE.match(morning_time) or not _TIME_RE.match(evening_time):
        raise HTTPException(400, "morning_time/evening_time must be 24-hour HH:MM, e.g. '07:30'")
    await _db_set_briefing_prefs(user_id, enabled, morning_time, evening_time)
    return {"ok": True, "enabled": enabled, "morning_time": morning_time, "evening_time": evening_time}


async def _execute_briefing_tool(user_id: str, args: dict, config: dict) -> str:
    action = (args.get("action") or "").lower()
    try:
        if action == "now":
            return await _compose_briefing(user_id, config)
        prefs = await _db_get_briefing_prefs(user_id)
        if action == "status":
            state = "enabled" if prefs["enabled"] else "disabled"
            return f"Daily briefing is {state}. Morning at {prefs['morning_time']}, evening at {prefs['evening_time']}."
        if action == "enable":
            await _db_set_briefing_prefs(user_id, True, prefs["morning_time"], prefs["evening_time"])
            return f"Daily briefing enabled — morning at {prefs['morning_time']}, evening at {prefs['evening_time']}."
        if action == "disable":
            await _db_set_briefing_prefs(user_id, False, prefs["morning_time"], prefs["evening_time"])
            return "Daily briefing disabled."
        if action == "set_time":
            slot = (args.get("slot") or "").lower()
            time_str = (args.get("time") or "").strip()
            if slot not in _VALID_SLOTS:
                return "Specify slot as 'morning' or 'evening'."
            if not _TIME_RE.match(time_str):
                return "Specify time as 24-hour HH:MM, e.g. 07:30."
            morning = time_str if slot == "morning" else prefs["morning_time"]
            evening = time_str if slot == "evening" else prefs["evening_time"]
            await _db_set_briefing_prefs(user_id, prefs["enabled"], morning, evening)
            return f"{slot.capitalize()} briefing set for {time_str}."
        return f"Unknown action: {action}"
    except Exception as e:
        return f"Error: {e}"


async def _deliver_briefing(user_id: str, slot: str, today: datetime.date) -> None:
    config = await _db_load_config(user_id)
    text = await _compose_briefing(user_id, config)
    greeting = "Good morning, sir." if slot == "morning" else "Good evening, sir."
    speak = f"{greeting} {text}"
    if _sio is not None and _sids_fn is not None:
        for sid in _sids_fn(user_id):
            await _sio.emit("briefing_ready", {"slot": slot, "text": text, "speak": speak}, to=sid)
    await _send_push(user_id, "Morning Briefing" if slot == "morning" else "Evening Briefing", text[:180])
    await _db_mark_briefing_sent(user_id, slot, today)


async def _briefing_loop() -> None:
    while True:
        await asyncio.sleep(60)
        if not _db_ready():
            continue
        try:
            now = datetime.datetime.now().astimezone()
            hhmm = now.strftime("%H:%M")
            today = now.date()
            for slot in _VALID_SLOTS:
                for user_id in await _db_list_users_due_for_briefing(slot, hhmm, today):
                    await _deliver_briefing(user_id, slot, today)
        except Exception as e:
            print(f"[BRIEFING] {e}", flush=True)
