import datetime
import re
import uuid

from integrations.pim.dav import (
    _DAV_NS,
    _dav_join,
    _dav_multistatus_responses,
    _dav_raise_for_status,
    _dav_request,
    _dav_response_prop,
)
from tool_schemas import anthropic_tools_to_openai

type _CalendarEvent = dict[str, datetime.datetime | str | bool]


def _calendar_configured(config: dict) -> bool:
    return bool(config.get("calendar_url") and config.get("calendar_username") and config.get("calendar_password"))


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


# ─── Tool schema ────────────────────────────────────────────────────────────

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

_CALENDAR_TOOL_OPENAI = anthropic_tools_to_openai([_CALENDAR_TOOL_ANTHROPIC])[0]


# ─── Execution ──────────────────────────────────────────────────────────────


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
