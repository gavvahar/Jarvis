import asyncio, datetime, re

from fastapi import HTTPException

from db import (
    _db_get_meeting_prep_prefs,
    _db_list_users_for_meeting_prep,
    _db_load_config,
    _db_mark_meeting_prep_sent,
    _db_meeting_prep_sent_uids,
    _db_ready,
    _db_search_past_meetings,
    _db_set_meeting_prep_prefs,
)
from integrations.pim.calendar import _calendar_configured, _calendar_events_between, _friendly_when
from integrations.push import _send_push
from tool_schemas import anthropic_tools_to_openai

_sio = None
_sids_fn = None

_POLL_INTERVAL_SECONDS = 60
_LOOKUP_WINDOW_HOURS = 24
_STOPWORDS = {"the", "and", "for", "with", "about", "from", "this", "that", "meeting", "call", "sync", "weekly", "daily", "monthly"}


def init(sio, sids_fn) -> None:
    global _sio, _sids_fn
    _sio = sio
    _sids_fn = sids_fn


# ─── Tool schema ────────────────────────────────────────────────────────────

_MANAGE_TOOL_ANTHROPIC = {
    "name": "manage_meeting_prep",
    "description": (
        "Enable, disable, reschedule, or check the status of proactive meeting prep — a heads-up with the agenda, "
        "attendees, and notes from prior related meetings, sent shortly before each calendar event."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": ["enable", "disable", "set_lead_time", "status"]},
            "lead_minutes": {"type": "integer", "description": "Minutes before a meeting to send the prep (required for set_lead_time)."},
        },
        "required": ["action"],
    },
}

_MANAGE_TOOL_OPENAI = anthropic_tools_to_openai([_MANAGE_TOOL_ANTHROPIC])[0]

_GET_TOOL_ANTHROPIC = {
    "name": "get_meeting_prep",
    "description": "Get a prep summary — agenda, attendees, and notes from prior related meetings — for the next upcoming calendar event.",
    "input_schema": {"type": "object", "properties": {}, "required": []},
}

_GET_TOOL_OPENAI = anthropic_tools_to_openai([_GET_TOOL_ANTHROPIC])[0]

_MEETING_PREP_TOOL_NAMES = {"manage_meeting_prep", "get_meeting_prep"}


def _get_meeting_prep_tools(config: dict, provider: str) -> list:
    if not _calendar_configured(config):
        return []
    if provider == "anthropic":
        return [_MANAGE_TOOL_ANTHROPIC, _GET_TOOL_ANTHROPIC]
    return [_MANAGE_TOOL_OPENAI, _GET_TOOL_OPENAI]


# ─── Composition ──────────────────────────────────────────────────────────────


def _keywords_from_title(title: str) -> list[str]:
    words = re.findall(r"[A-Za-z0-9']+", title.lower())
    seen = []
    for w in words:
        if len(w) > 3 and w not in _STOPWORDS and w not in seen:
            seen.append(w)
    return seen[:5]


def _extract_summary(notes: str) -> str:
    match = re.search(r"##\s*Summary\s*\n+(.+?)(?:\n##|\Z)", notes, re.DOTALL)
    text = match.group(1) if match else notes
    return " ".join(text.split())[:200]


async def _prior_notes_line(user_id: str, title: str) -> str:
    keywords = _keywords_from_title(title)
    if not keywords:
        return ""
    past = await _db_search_past_meetings(user_id, keywords, limit=2)
    if not past:
        return ""
    bits = []
    for m in past:
        when = m["started_at"].astimezone().strftime("%b %d").replace(" 0", " ")
        summary = _extract_summary(m["notes"])
        bits.append(f"{when}: {summary}")
    return "From a prior related meeting — " + "; ".join(bits)


async def _compose_meeting_prep(user_id: str, event: dict) -> str:
    title = event.get("title") or "Untitled event"
    start = event.get("start")
    when = _friendly_when(start) if isinstance(start, datetime.datetime) else ""
    bits = [f"Your next meeting is '{title}'" + (f" at {when}" if when else "") + "."]
    location = (event.get("location") or "").strip()
    if location:
        bits.append(f"Location: {location}.")
    description = (event.get("description") or "").strip()
    bits.append(f"Agenda: {description}." if description else "No agenda provided.")
    attendees = event.get("attendees") or []
    if attendees:
        bits.append("Attendees: " + ", ".join(attendees) + ".")
    prior = await _prior_notes_line(user_id, title)
    if prior:
        bits.append(prior)
    return " ".join(bits)


async def _next_upcoming_event(config: dict) -> dict | None:
    if not _calendar_configured(config):
        return None
    now = datetime.datetime.now().astimezone()
    end = now + datetime.timedelta(hours=_LOOKUP_WINDOW_HOURS)
    try:
        events = await _calendar_events_between(config, now, end, limit=1)
    except Exception:
        return None
    return events[0] if events else None


# ─── Execution ──────────────────────────────────────────────────────────────


async def _execute_meeting_prep_tool(name: str, user_id: str, args: dict, config: dict) -> str:
    try:
        if name == "get_meeting_prep":
            if not _calendar_configured(config):
                return "Calendar is not configured yet."
            event = await _next_upcoming_event(config)
            if not event:
                return f"No upcoming meetings found in the next {_LOOKUP_WINDOW_HOURS} hours."
            return await _compose_meeting_prep(user_id, event)

        action = (args.get("action") or "").lower()
        prefs = await _db_get_meeting_prep_prefs(user_id)
        if action == "status":
            state = "enabled" if prefs["enabled"] else "disabled"
            return f"Meeting prep is {state}, {prefs['lead_minutes']} minutes before each meeting."
        if action == "enable":
            await _db_set_meeting_prep_prefs(user_id, True, prefs["lead_minutes"])
            return f"Meeting prep enabled — you'll get a heads-up {prefs['lead_minutes']} minutes before each meeting."
        if action == "disable":
            await _db_set_meeting_prep_prefs(user_id, False, prefs["lead_minutes"])
            return "Meeting prep disabled."
        if action == "set_lead_time":
            lead = args.get("lead_minutes")
            if not lead or int(lead) <= 0:
                return "Specify lead_minutes as a positive number of minutes."
            lead = min(max(int(lead), 1), 120)
            await _db_set_meeting_prep_prefs(user_id, prefs["enabled"], lead)
            return f"Meeting prep will now arrive {lead} minutes before each meeting."
        return f"Unknown action: {action}"
    except Exception as e:
        return f"Error: {e}"


async def _get_meeting_prep_prefs(user_id: str) -> dict:
    return await _db_get_meeting_prep_prefs(user_id)


async def _set_meeting_prep_prefs(user_id: str, data: dict) -> dict:
    enabled = bool(data.get("enabled"))
    raw_lead = data.get("lead_minutes")
    try:
        lead_minutes = int(raw_lead) if raw_lead is not None else 15
    except (TypeError, ValueError) as e:
        raise HTTPException(400, "lead_minutes must be a number") from e
    lead_minutes = min(max(lead_minutes, 1), 120)
    await _db_set_meeting_prep_prefs(user_id, enabled, lead_minutes)
    return {"ok": True, "enabled": enabled, "lead_minutes": lead_minutes}


# ─── Background polling ─────────────────────────────────────────────────────


async def _deliver_meeting_prep(user_id: str, event: dict) -> None:
    text = await _compose_meeting_prep(user_id, event)
    speak = f"Heads up, sir — {text}"
    if _sio is not None and _sids_fn is not None:
        for sid in _sids_fn(user_id):
            await _sio.emit("meeting_prep_ready", {"text": text, "speak": speak}, to=sid)
    await _send_push(user_id, "Meeting prep", text[:180])
    await _db_mark_meeting_prep_sent(user_id, event["uid"])


async def _check_user_meeting_prep(user_id: str, config: dict, lead_minutes: int) -> None:
    now = datetime.datetime.now().astimezone()
    window_end = now + datetime.timedelta(minutes=lead_minutes)
    try:
        events = await _calendar_events_between(config, now, window_end, limit=10)
    except Exception:
        return
    events_with_uid = [e for e in events if e.get("uid")]
    if not events_with_uid:
        return
    already = await _db_meeting_prep_sent_uids(user_id, [e["uid"] for e in events_with_uid])
    for event in events_with_uid:
        if event["uid"] in already:
            continue
        await _deliver_meeting_prep(user_id, event)


async def _meeting_prep_loop() -> None:
    while True:
        await asyncio.sleep(_POLL_INTERVAL_SECONDS)
        if not _db_ready():
            continue
        try:
            for user_id in await _db_list_users_for_meeting_prep():
                config = await _db_load_config(user_id)
                if not _calendar_configured(config):
                    continue
                prefs = await _db_get_meeting_prep_prefs(user_id)
                await _check_user_meeting_prep(user_id, config, prefs["lead_minutes"])
        except Exception as e:
            print(f"[MEETING_PREP] {e}", flush=True)
