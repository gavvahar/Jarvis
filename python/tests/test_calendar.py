"""Tests for integrations/pim/calendar.py — CalDAV calendar."""

import asyncio, datetime

from unittest.mock import AsyncMock, MagicMock, patch
from app import _calendar_configured
from integrations.pim import calendar as calendar_mod
from integrations.pim.calendar import _execute_calendar_tool, _parse_ical_events


class TestCalendarConfigured:
    def test_all_fields_required(self):
        assert _calendar_configured({"calendar_url": "https://dav.example.com", "calendar_username": "me", "calendar_password": "secret"}) is True
        assert _calendar_configured({"calendar_url": "https://dav.example.com", "calendar_username": "me", "calendar_password": ""}) is False


class TestParseIcalEvents:
    def test_parses_timed_event(self):
        blob = """BEGIN:VCALENDAR
BEGIN:VEVENT
SUMMARY:Dentist
DTSTART:20260701T150000Z
DTEND:20260701T160000Z
LOCATION:Main Street
END:VEVENT
END:VCALENDAR
"""
        events = _parse_ical_events(blob)
        assert events[0]["title"] == "Dentist"
        assert events[0]["location"] == "Main Street"
        assert events[0]["all_day"] is False

    def test_parses_all_day_event(self):
        blob = """BEGIN:VCALENDAR
BEGIN:VEVENT
SUMMARY:Holiday
DTSTART;VALUE=DATE:20260704
DTEND;VALUE=DATE:20260705
END:VEVENT
END:VCALENDAR
"""
        events = _parse_ical_events(blob)
        assert events[0]["title"] == "Holiday"
        assert events[0]["all_day"] is True

    def test_parses_uid_and_attendees(self):
        blob = """BEGIN:VCALENDAR
BEGIN:VEVENT
UID:abc123@example.com
SUMMARY:Planning Sync
DTSTART:20260701T150000Z
DTEND:20260701T160000Z
ATTENDEE;CN="Jane Doe":mailto:jane@example.com
ATTENDEE:mailto:noname@example.com
END:VEVENT
END:VCALENDAR
"""
        events = _parse_ical_events(blob)
        assert events[0]["uid"] == "abc123@example.com"
        assert events[0]["attendees"] == ["Jane Doe", "noname@example.com"]

    def test_missing_uid_and_attendees_default_empty(self):
        blob = """BEGIN:VCALENDAR
BEGIN:VEVENT
SUMMARY:No Metadata
DTSTART:20260701T150000Z
DTEND:20260701T160000Z
END:VEVENT
END:VCALENDAR
"""
        events = _parse_ical_events(blob)
        assert events[0]["uid"] == ""
        assert events[0]["attendees"] == []


class TestExecuteCalendarTool:
    _cfg = {
        "calendar_url": "https://dav.example.com/cal/",
        "calendar_username": "me",
        "calendar_password": "secret",
    }

    def _mock_resp(self, status=207, text=""):
        resp = MagicMock()
        resp.status_code = status
        resp.text = text
        return resp

    def test_list_formats_events(self):
        event = {
            "title": "Dentist",
            "start": datetime.datetime(2026, 7, 1, 15, 0, tzinfo=datetime.timezone.utc),
            "end": datetime.datetime(2026, 7, 1, 16, 0, tzinfo=datetime.timezone.utc),
            "location": "Main Street",
            "all_day": False,
        }
        with patch("integrations.pim.calendar._calendar_events_between", new=AsyncMock(return_value=[event])):
            result = asyncio.run(_execute_calendar_tool(self._cfg, {"action": "list"}))
        assert "Dentist" in result
        assert "Main Street" in result

    def test_create_puts_event(self):
        mock_req = AsyncMock(return_value=self._mock_resp(status=201))
        with patch("integrations.pim.calendar._dav_request", new=mock_req):
            result = asyncio.run(
                _execute_calendar_tool(
                    self._cfg,
                    {
                        "action": "create",
                        "title": "Dinner",
                        "start": "2026-07-01T18:00:00+00:00",
                        "end": "2026-07-01T19:30:00+00:00",
                        "location": "Kitchen",
                    },
                )
            )
        assert "Created calendar event" in result
        assert mock_req.await_args is not None
        assert mock_req.await_args.args[0] == "PUT"

    def test_create_rejects_backwards_time(self):
        result = asyncio.run(
            _execute_calendar_tool(
                self._cfg,
                {
                    "action": "create",
                    "title": "Impossible",
                    "start": "2026-07-01T19:30:00+00:00",
                    "end": "2026-07-01T18:00:00+00:00",
                },
            )
        )
        assert "after the start time" in result

    def test_not_configured(self):
        result = asyncio.run(_execute_calendar_tool({}, {"action": "list"}))
        assert "not configured" in result

    def test_list_with_date_only_range(self):
        with patch("integrations.pim.calendar._calendar_events_between", new=AsyncMock(return_value=[])):
            result = asyncio.run(_execute_calendar_tool(self._cfg, {"action": "list", "start": "2026-07-01", "end": "2026-07-02"}))
        assert "No calendar events found" in result

    def test_list_lookup_error_surfaced(self):
        with patch("integrations.pim.calendar._calendar_events_between", new=AsyncMock(side_effect=ValueError("auth failed"))):
            result = asyncio.run(_execute_calendar_tool(self._cfg, {"action": "list"}))
        assert "Could not read the calendar" in result

    def test_create_invalid_date_string(self):
        result = asyncio.run(
            _execute_calendar_tool(
                self._cfg,
                {"action": "create", "title": "Bad Date", "start": "not-a-date", "end": "2026-07-01T18:00:00+00:00"},
            )
        )
        assert "Invalid datetime" in result

    def test_create_missing_fields(self):
        result = asyncio.run(_execute_calendar_tool(self._cfg, {"action": "create", "title": "No Times"}))
        assert "needs title, start, and end" in result

    def test_create_all_day_event(self):
        mock_req = AsyncMock(return_value=self._mock_resp(status=201))
        with patch("integrations.pim.calendar._dav_request", new=mock_req):
            result = asyncio.run(
                _execute_calendar_tool(
                    self._cfg,
                    {"action": "create", "title": "Holiday", "start": "2026-07-04", "end": "2026-07-04", "all_day": True},
                )
            )
        assert "Created calendar event 'Holiday'" in result
        assert "all day" in result

    def test_create_all_day_backwards_rejected(self):
        result = asyncio.run(
            _execute_calendar_tool(
                self._cfg,
                {"action": "create", "title": "Bad", "start": "2026-07-05", "end": "2026-07-01", "all_day": True},
            )
        )
        assert "must not be before the start date" in result

    def test_create_dav_failure_surfaced(self):
        with (
            patch("integrations.pim.calendar._dav_request", new=AsyncMock(return_value=self._mock_resp(status=500))),
            patch("integrations.pim.calendar._dav_raise_for_status", side_effect=ValueError("server returned 500")),
        ):
            result = asyncio.run(
                _execute_calendar_tool(
                    self._cfg,
                    {
                        "action": "create",
                        "title": "Dinner",
                        "start": "2026-07-01T18:00:00+00:00",
                        "end": "2026-07-01T19:30:00+00:00",
                    },
                )
            )
        assert "Could not create the calendar event" in result

    def test_unknown_action(self):
        result = asyncio.run(_execute_calendar_tool(self._cfg, {"action": "bogus"}))
        assert "Unknown action" in result


class TestFormatCalendarEvent:
    def test_no_start_returns_title_only(self):
        assert calendar_mod._format_calendar_event({"title": "Untitled Meeting"}) == "Untitled Meeting"

    def test_all_day_event(self):
        event = {"title": "Holiday", "start": datetime.datetime(2026, 7, 4, tzinfo=datetime.timezone.utc), "all_day": True}
        result = calendar_mod._format_calendar_event(event)
        assert "all day" in result
        assert "Holiday" in result

    def test_multi_day_event(self):
        event = {
            "title": "Conference",
            "start": datetime.datetime(2026, 7, 1, 9, 0, tzinfo=datetime.timezone.utc),
            "end": datetime.datetime(2026, 7, 3, 17, 0, tzinfo=datetime.timezone.utc),
        }
        result = calendar_mod._format_calendar_event(event)
        assert "to" in result

    def test_includes_location(self):
        event = {
            "title": "Dentist",
            "start": datetime.datetime(2026, 7, 1, 9, 0, tzinfo=datetime.timezone.utc),
            "end": datetime.datetime(2026, 7, 1, 10, 0, tzinfo=datetime.timezone.utc),
            "location": "Main Street",
        }
        result = calendar_mod._format_calendar_event(event)
        assert "@ Main Street" in result


class TestParseCalendarInput:
    def test_date_only_string(self):
        value, is_date = calendar_mod._parse_calendar_input("2026-07-04")
        assert value == datetime.date(2026, 7, 4)
        assert is_date is True

    def test_iso_datetime_with_z(self):
        value, is_date = calendar_mod._parse_calendar_input("2026-07-04T12:00:00Z")
        assert is_date is False
        assert value.year == 2026

    def test_naive_datetime_gets_local_tz(self):
        value, is_date = calendar_mod._parse_calendar_input("2026-07-04T12:00:00")
        assert value.tzinfo is not None

    def test_invalid_string_raises(self):
        try:
            calendar_mod._parse_calendar_input("not-a-date")
            raise AssertionError("expected ValueError")
        except ValueError as e:
            assert "Invalid datetime" in str(e)


class TestBuildCalendarEventIcs:
    def test_all_day_event_uses_date_value(self):
        ics = calendar_mod._build_calendar_event_ics("Holiday", datetime.date(2026, 7, 4), datetime.date(2026, 7, 5), all_day=True)
        assert "DTSTART;VALUE=DATE:20260704" in ics
        assert "DTEND;VALUE=DATE:20260705" in ics

    def test_timed_event_uses_utc_stamps(self):
        start = datetime.datetime(2026, 7, 1, 18, 0, tzinfo=datetime.timezone.utc)
        end = datetime.datetime(2026, 7, 1, 19, 0, tzinfo=datetime.timezone.utc)
        ics = calendar_mod._build_calendar_event_ics("Dinner", start, end)
        assert "DTSTART:20260701T180000Z" in ics
        assert "DTEND:20260701T190000Z" in ics

    def test_includes_location_and_description(self):
        start = datetime.datetime(2026, 7, 1, 18, 0, tzinfo=datetime.timezone.utc)
        end = datetime.datetime(2026, 7, 1, 19, 0, tzinfo=datetime.timezone.utc)
        ics = calendar_mod._build_calendar_event_ics("Dinner", start, end, description="Bring wine", location="Kitchen")
        assert "LOCATION:Kitchen" in ics
        assert "DESCRIPTION:Bring wine" in ics


class TestParseIcalEventsExtra:
    def test_description_captured(self):
        blob = "BEGIN:VEVENT\r\nSUMMARY:Meeting\r\nDESCRIPTION:Discuss roadmap\r\nDTSTART:20260701T090000\r\nEND:VEVENT\r\n"
        events = _parse_ical_events(blob)
        assert events[0]["description"] == "Discuss roadmap"

    def test_missing_dtend_defaults_to_start(self):
        blob = "BEGIN:VEVENT\r\nSUMMARY:Quick chat\r\nDTSTART:20260701T090000\r\nEND:VEVENT\r\n"
        events = _parse_ical_events(blob)
        assert events[0]["end"] == events[0]["start"]


class TestCalendarEventsBetween:
    _cfg = {"calendar_url": "https://dav.example.com/cal/", "calendar_username": "me", "calendar_password": "secret"}

    _EVENTS_XML = (
        '<?xml version="1.0" encoding="utf-8"?>'
        '<D:multistatus xmlns:D="DAV:" xmlns:C="urn:ietf:params:xml:ns:caldav">'
        "<D:response><D:href>/cal/dentist.ics</D:href><D:propstat><D:prop>"
        "<C:calendar-data>BEGIN:VCALENDAR&#10;BEGIN:VEVENT&#10;SUMMARY:Dentist&#10;"
        "DTSTART:20260701T150000Z&#10;DTEND:20260701T160000Z&#10;END:VEVENT&#10;END:VCALENDAR&#10;</C:calendar-data>"
        "</D:prop><D:status>HTTP/1.1 200 OK</D:status></D:propstat></D:response>"
        "</D:multistatus>"
    )

    def test_returns_events_in_range(self):
        resp = MagicMock(status_code=207, text=self._EVENTS_XML)
        with patch("integrations.pim.calendar._dav_request", new=AsyncMock(return_value=resp)):
            events = asyncio.run(
                calendar_mod._calendar_events_between(
                    self._cfg,
                    datetime.datetime(2026, 7, 1, tzinfo=datetime.timezone.utc),
                    datetime.datetime(2026, 7, 2, tzinfo=datetime.timezone.utc),
                )
            )
        assert len(events) == 1
        assert events[0]["title"] == "Dentist"

    def test_excludes_events_outside_range(self):
        resp = MagicMock(status_code=207, text=self._EVENTS_XML)
        with patch("integrations.pim.calendar._dav_request", new=AsyncMock(return_value=resp)):
            events = asyncio.run(
                calendar_mod._calendar_events_between(
                    self._cfg,
                    datetime.datetime(2026, 8, 1, tzinfo=datetime.timezone.utc),
                    datetime.datetime(2026, 8, 2, tzinfo=datetime.timezone.utc),
                )
            )
        assert events == []
