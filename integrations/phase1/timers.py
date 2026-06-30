import datetime
import xml.etree.ElementTree as ET

import httpx

from db import _db_cancel_reminder, _db_cancel_timer, _db_list_reminders, _db_list_timers, _db_set_reminder, _db_set_timer
from integrations.phase1.calendar import _CALENDAR_TOOL_ANTHROPIC, _CALENDAR_TOOL_OPENAI, _calendar_configured
from integrations.phase1.contacts import _CONTACT_LOOKUP_TOOL_ANTHROPIC, _CONTACT_LOOKUP_TOOL_OPENAI, _contacts_configured

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
