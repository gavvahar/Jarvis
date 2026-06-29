"""
Unit and HTTP-level tests for Jarvis.

Pure-function tests need no fixtures.
Webhook auth tests use the `api_client` fixture from conftest.py which
stubs out the database so no running PostgreSQL is required.
"""

import asyncio
import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import app as jarvis
from app import (
    _build_client,
    _build_system_prompt,
    _calendar_configured,
    _c_to_f,
    _contacts_configured,
    _duration_str,
    _evaluate_alert_condition,
    _execute_calendar_tool,
    _execute_contact_lookup_tool,
    _execute_device_alert_tool,
    _execute_news_tool,
    _execute_reminder_tool,
    _execute_routine_tool,
    _execute_shared_list_tool,
    _execute_spotify_tool,
    _execute_timer_tool,
    _get_phase1_tools,
    _get_myq_tools,
    _get_phase5_tools,
    _get_spotify_tools,
    _get_tesla_tools,
    _get_user_lock,
    _ha_configured,
    _ha_headers,
    _myq_configured,
    _myq_get_status,
    _myq_set_door,
    _parse_ical_events,
    _parse_vcards,
    _pick_best_dav_collection,
    _sids_for_user,
    _split_sentences,
    _spotify_configured,
    _tesla_configured,
    _user_configured,
)

# ── Pure function tests ────────────────────────────────────────────────────────


class TestSplitSentences:
    def test_single_sentence(self):
        sents, rem = _split_sentences("Hello, world. ")
        assert sents == ["Hello, world."]
        assert rem == ""

    def test_multiple_sentences(self):
        sents, rem = _split_sentences("First. Second! Third? ")
        assert sents == ["First.", "Second!", "Third?"]
        assert rem == ""

    def test_incomplete_trailing(self):
        sents, rem = _split_sentences("Done. Still typing")
        assert sents == ["Done."]
        assert rem == "Still typing"

    def test_no_sentence_end(self):
        sents, rem = _split_sentences("No terminator here")
        assert sents == []
        assert rem == "No terminator here"

    def test_empty_string(self):
        sents, rem = _split_sentences("")
        assert sents == []
        assert rem == ""

    def test_ellipsis_terminates(self):
        sents, rem = _split_sentences("Thinking… ")
        assert sents == ["Thinking…"]
        assert rem == ""

    def test_quoted_sentence(self):
        sents, rem = _split_sentences('He said "Hello." ')
        assert len(sents) == 1
        assert rem == ""


class TestHaConfigured:
    def test_both_present(self):
        assert _ha_configured({"ha_url": "http://ha.local", "ha_token": "tok"}) is True

    def test_empty_url(self):
        assert _ha_configured({"ha_url": "", "ha_token": "tok"}) is False

    def test_empty_token(self):
        assert _ha_configured({"ha_url": "http://ha.local", "ha_token": ""}) is False

    def test_both_missing(self):
        assert _ha_configured({}) is False

    def test_none_values(self):
        assert _ha_configured({"ha_url": None, "ha_token": None}) is False


class TestCalendarConfigured:
    def test_all_fields_required(self):
        assert _calendar_configured({"calendar_url": "https://dav.example.com", "calendar_username": "me", "calendar_password": "secret"}) is True
        assert _calendar_configured({"calendar_url": "https://dav.example.com", "calendar_username": "me", "calendar_password": ""}) is False


class TestContactsConfigured:
    def test_all_fields_required(self):
        assert _contacts_configured({"contacts_url": "https://dav.example.com", "contacts_username": "me", "contacts_password": "secret"}) is True
        assert _contacts_configured({"contacts_url": "", "contacts_username": "me", "contacts_password": "secret"}) is False


class TestUserConfigured:
    def test_with_client(self):
        assert _user_configured({"client": object()}) is True

    def test_with_none_client(self):
        assert _user_configured({"client": None}) is False


class TestHaHeaders:
    def test_returns_bearer_token(self):
        headers = _ha_headers({"ha_token": "secret123"})
        assert headers["Authorization"] == "Bearer secret123"
        assert headers["Content-Type"] == "application/json"


class TestMyqConfigured:
    def test_both_present(self):
        assert _myq_configured({"myq_email": "a@b.com", "myq_password": "secret"}) is True

    def test_empty_email(self):
        assert _myq_configured({"myq_email": "", "myq_password": "secret"}) is False

    def test_empty_password(self):
        assert _myq_configured({"myq_email": "a@b.com", "myq_password": ""}) is False

    def test_both_missing(self):
        assert _myq_configured({}) is False

    def test_none_values(self):
        assert _myq_configured({"myq_email": None, "myq_password": None}) is False


class TestGetMyqTools:
    def test_empty_when_not_configured(self):
        assert _get_myq_tools({"myq_email": "", "myq_password": ""}, "anthropic") == []

    def test_anthropic_tools_when_configured(self):
        tools = _get_myq_tools({"myq_email": "a@b.com", "myq_password": "s"}, "anthropic")
        names = [t["name"] for t in tools]
        assert "get_garage_status" in names
        assert "set_garage_door" in names

    def test_openai_tools_when_configured(self):
        tools = _get_myq_tools({"myq_email": "a@b.com", "myq_password": "s"}, "openai")
        names = [t["function"]["name"] for t in tools]
        assert "get_garage_status" in names
        assert "set_garage_door" in names


def _make_myq_session_mock():
    session = MagicMock()
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=None)
    return session


class TestMyqGetStatus:
    def test_returns_device_state(self):
        device = MagicMock()
        device.name = "Main Garage"
        device.state = "closed"
        myq = MagicMock()
        myq.covers = {"s1": device}
        session = _make_myq_session_mock()
        with patch("aiohttp.ClientSession", return_value=session), patch("pymyq.login", new=AsyncMock(return_value=myq)):
            result = asyncio.run(_myq_get_status({"myq_email": "a@b.com", "myq_password": "s"}))
        assert "Main Garage" in result
        assert "closed" in result

    def test_returns_no_devices_message(self):
        myq = MagicMock()
        myq.covers = {}
        session = _make_myq_session_mock()
        with patch("aiohttp.ClientSession", return_value=session), patch("pymyq.login", new=AsyncMock(return_value=myq)):
            result = asyncio.run(_myq_get_status({"myq_email": "a@b.com", "myq_password": "s"}))
        assert "No garage doors found" in result

    def test_handles_exception(self):
        session = _make_myq_session_mock()
        with patch("aiohttp.ClientSession", return_value=session), patch("pymyq.login", new=AsyncMock(side_effect=Exception("auth failed"))):
            result = asyncio.run(_myq_get_status({"myq_email": "a@b.com", "myq_password": "bad"}))
        assert "Could not reach MyQ" in result


class TestMyqSetDoor:
    def test_sends_open_command(self):
        device = MagicMock()
        device.name = "Main Garage"
        device.open = AsyncMock()
        myq = MagicMock()
        myq.covers = {"s1": device}
        session = _make_myq_session_mock()
        with patch("aiohttp.ClientSession", return_value=session), patch("pymyq.login", new=AsyncMock(return_value=myq)):
            result = asyncio.run(_myq_set_door({"myq_email": "a@b.com", "myq_password": "s"}, None, "open"))
        device.open.assert_called_once_with(wait_for_state=None)
        assert "open" in result

    def test_sends_close_command(self):
        device = MagicMock()
        device.name = "Main Garage"
        device.close = AsyncMock()
        myq = MagicMock()
        myq.covers = {"s1": device}
        session = _make_myq_session_mock()
        with patch("aiohttp.ClientSession", return_value=session), patch("pymyq.login", new=AsyncMock(return_value=myq)):
            result = asyncio.run(_myq_set_door({"myq_email": "a@b.com", "myq_password": "s"}, None, "close"))
        device.close.assert_called_once_with(wait_for_state=None)
        assert "close" in result

    def test_no_matching_device_returns_error(self):
        device = MagicMock()
        device.name = "Main Garage"
        myq = MagicMock()
        myq.covers = {"s1": device}
        session = _make_myq_session_mock()
        with patch("aiohttp.ClientSession", return_value=session), patch("pymyq.login", new=AsyncMock(return_value=myq)):
            result = asyncio.run(_myq_set_door({"myq_email": "a@b.com", "myq_password": "s"}, "Side Door", "open"))
        assert "No garage door matching" in result

    def test_handles_exception(self):
        session = _make_myq_session_mock()
        with patch("aiohttp.ClientSession", return_value=session), patch("pymyq.login", new=AsyncMock(side_effect=Exception("network error"))):
            result = asyncio.run(_myq_set_door({"myq_email": "a@b.com", "myq_password": "s"}, None, "close"))
        assert "Could not reach MyQ" in result


class TestBuildSystemPrompt:
    def test_base_prompt_non_empty(self):
        prompt = _build_system_prompt({"ha_url": "", "ha_token": ""})
        assert len(prompt) > 50

    def test_ha_section_added_when_configured(self):
        prompt = _build_system_prompt({"ha_url": "http://ha.local", "ha_token": "tok"})
        assert "HOME AUTOMATION" in prompt

    def test_ha_section_absent_when_not_configured(self):
        prompt = _build_system_prompt({"ha_url": "", "ha_token": ""})
        assert "HOME AUTOMATION" not in prompt

    def test_garage_section_added_when_configured(self):
        prompt = _build_system_prompt({"ha_url": "", "ha_token": "", "myq_email": "a@b.com", "myq_password": "s"})
        assert "GARAGE DOOR" in prompt

    def test_garage_section_absent_when_not_configured(self):
        prompt = _build_system_prompt({"ha_url": "", "ha_token": "", "myq_email": "", "myq_password": ""})
        assert "GARAGE DOOR" not in prompt

    def test_calendar_section_added_when_configured(self):
        prompt = _build_system_prompt({"calendar_url": "https://dav.example.com/cal/", "calendar_username": "me", "calendar_password": "secret"})
        assert "CALENDAR" in prompt

    def test_contacts_section_added_when_configured(self):
        prompt = _build_system_prompt({"contacts_url": "https://dav.example.com/ab/", "contacts_username": "me", "contacts_password": "secret"})
        assert "CONTACTS" in prompt

    def test_tesla_section_added_when_configured(self):
        cfg = {"ha_url": "", "ha_token": "", "tesla_method": "unofficial", "tesla_refresh_token": "tok", "tesla_fleet_refresh_token": ""}
        assert "TESLA" in _build_system_prompt(cfg)

    def test_tesla_section_absent_when_not_configured(self):
        cfg = {"ha_url": "", "ha_token": "", "tesla_method": "", "tesla_refresh_token": "", "tesla_fleet_refresh_token": ""}
        assert "TESLA" not in _build_system_prompt(cfg)

    def test_location_context_included_when_set(self):
        jarvis._location_context.update({"city": "Austin", "region": "TX", "temp_f": 95, "condition": "Clear"})
        try:
            prompt = _build_system_prompt({"ha_url": "", "ha_token": ""})
            assert "Austin" in prompt
            assert "95" in prompt
        finally:
            jarvis._location_context.clear()

    def test_location_context_city_without_region(self):
        jarvis._location_context.update({"city": "London", "temp_f": 60, "condition": "Overcast", "pressure_kpa": 101.3})
        try:
            prompt = _build_system_prompt({"ha_url": "", "ha_token": ""})
            assert "London" in prompt
            assert "101.3" in prompt
        finally:
            jarvis._location_context.clear()

    def test_spotify_section_added_when_configured(self):
        cfg = {"ha_url": "", "ha_token": "", "spotify_refresh_token": "rtok"}
        assert "SPOTIFY" in _build_system_prompt(cfg)

    def test_spotify_section_absent_when_not_configured(self):
        cfg = {"ha_url": "", "ha_token": "", "spotify_refresh_token": ""}
        assert "SPOTIFY" not in _build_system_prompt(cfg)

    def test_all_integrations_configured(self):
        cfg = {
            "ha_url": "http://ha.local",
            "ha_token": "tok",
            "myq_email": "a@b.com",
            "myq_password": "s",
            "tesla_method": "unofficial",
            "tesla_refresh_token": "rtok",
            "tesla_fleet_refresh_token": "",
            "spotify_refresh_token": "sprtok",
            "calendar_url": "https://dav.example.com/cal/",
            "calendar_username": "me",
            "calendar_password": "secret",
            "contacts_url": "https://dav.example.com/ab/",
            "contacts_username": "me",
            "contacts_password": "secret",
        }
        prompt = _build_system_prompt(cfg)
        assert "HOME AUTOMATION" in prompt
        assert "GARAGE DOOR" in prompt
        assert "TESLA" in prompt
        assert "SPOTIFY" in prompt
        assert "CALENDAR" in prompt
        assert "CONTACTS" in prompt


class TestTeslaConfigured:
    def test_not_configured_when_method_empty(self):
        assert _tesla_configured({"tesla_method": "", "tesla_refresh_token": "", "tesla_fleet_refresh_token": ""}) is False

    def test_unofficial_configured_when_token_present(self):
        assert _tesla_configured({"tesla_method": "unofficial", "tesla_refresh_token": "tok", "tesla_fleet_refresh_token": ""}) is True

    def test_unofficial_not_configured_when_token_missing(self):
        assert _tesla_configured({"tesla_method": "unofficial", "tesla_refresh_token": "", "tesla_fleet_refresh_token": ""}) is False

    def test_fleet_configured_when_token_present(self):
        assert _tesla_configured({"tesla_method": "fleet", "tesla_refresh_token": "", "tesla_fleet_refresh_token": "fleet_tok"}) is True

    def test_fleet_not_configured_when_token_missing(self):
        assert _tesla_configured({"tesla_method": "fleet", "tesla_refresh_token": "", "tesla_fleet_refresh_token": ""}) is False

    def test_both_requires_both_tokens(self):
        assert _tesla_configured({"tesla_method": "both", "tesla_refresh_token": "tok", "tesla_fleet_refresh_token": "fleet_tok"}) is True

    def test_both_fails_if_unofficial_token_missing(self):
        assert _tesla_configured({"tesla_method": "both", "tesla_refresh_token": "", "tesla_fleet_refresh_token": "fleet_tok"}) is False

    def test_both_fails_if_fleet_token_missing(self):
        assert _tesla_configured({"tesla_method": "both", "tesla_refresh_token": "tok", "tesla_fleet_refresh_token": ""}) is False


class TestSpotifyConfigured:
    def test_not_configured_when_token_empty(self):
        assert _spotify_configured({"spotify_refresh_token": ""}) is False

    def test_not_configured_when_key_missing(self):
        assert _spotify_configured({}) is False

    def test_configured_when_token_present(self):
        assert _spotify_configured({"spotify_refresh_token": "rtok"}) is True


class TestGetPhase1Tools:
    def test_base_tools_present_without_dav(self):
        names = {tool["name"] for tool in _get_phase1_tools({}, "anthropic")}
        assert {"manage_timer", "manage_reminder", "get_news_headlines"} <= names
        assert "manage_calendar" not in names
        assert "lookup_contact" not in names

    def test_dav_tools_added_when_configured(self):
        cfg = {
            "calendar_url": "https://dav.example.com/cal/",
            "calendar_username": "me",
            "calendar_password": "secret",
            "contacts_url": "https://dav.example.com/ab/",
            "contacts_username": "me",
            "contacts_password": "secret",
        }
        names = {tool["function"]["name"] for tool in _get_phase1_tools(cfg, "openai")}
        assert "manage_calendar" in names
        assert "lookup_contact" in names


class TestGetSpotifyTools:
    def test_empty_when_not_configured(self):
        assert _get_spotify_tools({"spotify_refresh_token": ""}, "anthropic") == []

    def test_anthropic_tools_when_configured(self):
        tools = _get_spotify_tools({"spotify_refresh_token": "rtok"}, "anthropic")
        assert len(tools) > 0
        assert all("name" in t for t in tools)

    def test_openai_tools_when_configured(self):
        tools = _get_spotify_tools({"spotify_refresh_token": "rtok"}, "openai")
        assert len(tools) > 0
        assert all(t["type"] == "function" for t in tools)

    def test_returns_seven_tools(self):
        tools = _get_spotify_tools({"spotify_refresh_token": "rtok"}, "anthropic")
        assert len(tools) == 7

    def test_tool_names_include_search_and_play(self):
        names = {t["name"] for t in _get_spotify_tools({"spotify_refresh_token": "rtok"}, "anthropic")}
        assert "spotify_search_and_play" in names
        assert "spotify_now_playing" in names


class TestExecuteSpotifyTool:
    _cfg = {"spotify_refresh_token": "rtok"}

    def _mock_resp(self, status=204, text="", json_data=None):
        r = MagicMock()
        r.status_code = status
        r.text = text
        if json_data is not None:
            r.json = MagicMock(return_value=json_data)
        return r

    def test_now_playing_nothing(self):
        with patch("app._spotify_req", new=AsyncMock(return_value=self._mock_resp(204, ""))):
            result = asyncio.run(_execute_spotify_tool("spotify_now_playing", {}, "u1", self._cfg))
        assert "Nothing" in result

    def test_now_playing_track(self):
        data = {"is_playing": True, "item": {"name": "Get Lucky", "artists": [{"name": "Daft Punk"}]}}
        with patch("app._spotify_req", new=AsyncMock(return_value=self._mock_resp(200, "x", data))):
            result = asyncio.run(_execute_spotify_tool("spotify_now_playing", {}, "u1", self._cfg))
        assert "Get Lucky" in result
        assert "Daft Punk" in result

    def test_play_success(self):
        with patch("app._spotify_req", new=AsyncMock(return_value=self._mock_resp(204))):
            result = asyncio.run(_execute_spotify_tool("spotify_play", {}, "u1", self._cfg))
        assert "playback" in result.lower()

    def test_pause_success(self):
        with patch("app._spotify_req", new=AsyncMock(return_value=self._mock_resp(204))):
            result = asyncio.run(_execute_spotify_tool("spotify_pause", {}, "u1", self._cfg))
        assert "paused" in result.lower()

    def test_next_success(self):
        with patch("app._spotify_req", new=AsyncMock(return_value=self._mock_resp(204))):
            result = asyncio.run(_execute_spotify_tool("spotify_next", {}, "u1", self._cfg))
        assert "next" in result.lower() or "skipped" in result.lower()

    def test_previous_success(self):
        with patch("app._spotify_req", new=AsyncMock(return_value=self._mock_resp(204))):
            result = asyncio.run(_execute_spotify_tool("spotify_previous", {}, "u1", self._cfg))
        assert "previous" in result.lower() or "back" in result.lower()

    def test_volume_clamped_and_set(self):
        with patch("app._spotify_req", new=AsyncMock(return_value=self._mock_resp(204))):
            result = asyncio.run(_execute_spotify_tool("spotify_volume", {"volume_percent": 70}, "u1", self._cfg))
        assert "70" in result

    def test_volume_clamped_above_100(self):
        with patch("app._spotify_req", new=AsyncMock(return_value=self._mock_resp(204))):
            result = asyncio.run(_execute_spotify_tool("spotify_volume", {"volume_percent": 150}, "u1", self._cfg))
        assert "100" in result

    def test_search_and_play_track_found(self):
        search_data = {"tracks": {"items": [{"uri": "spotify:track:abc", "name": "Around the World", "artists": [{"name": "Daft Punk"}]}]}}
        play_resp = self._mock_resp(204)
        call_count = 0

        async def mock_req(method, _endpoint, *_a, **_kw):
            nonlocal call_count
            call_count += 1
            if method == "get":
                return self._mock_resp(200, "x", search_data)
            return play_resp

        with patch("app._spotify_req", new=mock_req):
            result = asyncio.run(_execute_spotify_tool("spotify_search_and_play", {"query": "Around the World", "type": "track"}, "u1", self._cfg))
        assert "Around the World" in result

    def test_search_and_play_not_found(self):
        search_data = {"tracks": {"items": []}}
        with patch("app._spotify_req", new=AsyncMock(return_value=self._mock_resp(200, "x", search_data))):
            result = asyncio.run(_execute_spotify_tool("spotify_search_and_play", {"query": "xyzzy", "type": "track"}, "u1", self._cfg))
        assert "Could not find" in result

    def test_unknown_tool_returns_error(self):
        result = asyncio.run(_execute_spotify_tool("spotify_nonexistent", {}, "u1", self._cfg))
        assert "Unknown" in result


class TestCToF:
    def test_freezing(self):
        assert _c_to_f(0) == 32.0

    def test_boiling(self):
        assert _c_to_f(100) == 212.0

    def test_body_temp(self):
        assert abs(_c_to_f(37) - 98.6) < 0.1

    def test_crossover(self):
        assert _c_to_f(-40) == -40.0

    def test_negative(self):
        assert _c_to_f(-10) == 14.0


class TestPickBestDavCollection:
    def test_prefers_events_collection_over_inbox(self):
        collections = [
            {"url": "https://dav.example.com/cal/inbox/", "display_name": "Inbox"},
            {"url": "https://dav.example.com/cal/events/", "display_name": "Primary"},
        ]
        best = _pick_best_dav_collection(collections, "calendar")
        assert best is not None
        assert best["url"].endswith("/events/")

    def test_prefers_named_contacts_collection(self):
        collections = [
            {"url": "https://dav.example.com/addressbooks/1/", "display_name": "Archive"},
            {"url": "https://dav.example.com/addressbooks/2/", "display_name": "Contacts"},
        ]
        best = _pick_best_dav_collection(collections, "addressbook")
        assert best is not None
        assert best["display_name"] == "Contacts"


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


class TestParseVcards:
    def test_parses_name_phone_and_email(self):
        blob = """BEGIN:VCARD
VERSION:3.0
FN:Mom
TEL;TYPE=CELL:tel:+15551234567
EMAIL:mailto:mom@example.com
END:VCARD
"""
        cards = _parse_vcards(blob)
        assert cards[0]["name"] == "Mom"
        assert cards[0]["phones"] == ["+15551234567"]
        assert cards[0]["emails"] == ["mom@example.com"]


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
        with patch("app._calendar_events_between", new=AsyncMock(return_value=[event])):
            result = asyncio.run(_execute_calendar_tool(self._cfg, {"action": "list"}))
        assert "Dentist" in result
        assert "Main Street" in result

    def test_create_puts_event(self):
        mock_req = AsyncMock(return_value=self._mock_resp(status=201))
        with patch("app._dav_request", new=mock_req):
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


class TestExecuteContactLookupTool:
    _cfg = {
        "contacts_url": "https://dav.example.com/ab/",
        "contacts_username": "me",
        "contacts_password": "secret",
    }

    def test_formats_contact_matches(self):
        match = {"name": "Mom", "phones": ["+15551234567"], "emails": ["mom@example.com"], "nicknames": []}
        with patch("app._lookup_contacts", new=AsyncMock(return_value=[match])):
            result = asyncio.run(_execute_contact_lookup_tool(self._cfg, {"query": "Mom", "preferred_channel": "phone"}))
        assert "Mom" in result
        assert "+15551234567" in result

    def test_returns_not_found_message(self):
        with patch("app._lookup_contacts", new=AsyncMock(return_value=[])):
            result = asyncio.run(_execute_contact_lookup_tool(self._cfg, {"query": "Nobody"}))
        assert "No contacts matched" in result


class TestGetTeslaTools:
    def test_returns_empty_when_not_configured(self):
        cfg = {"tesla_method": "", "tesla_refresh_token": "", "tesla_fleet_refresh_token": ""}
        assert _get_tesla_tools(cfg, "anthropic") == []

    def test_returns_anthropic_tools_when_configured(self):
        cfg = {"tesla_method": "unofficial", "tesla_refresh_token": "tok", "tesla_fleet_refresh_token": ""}
        tools = _get_tesla_tools(cfg, "anthropic")
        assert len(tools) > 0
        names = [t["name"] for t in tools]
        assert "get_vehicle_status" in names
        assert "lock_vehicle" in names
        assert "set_climate" in names

    def test_returns_openai_tools_when_configured(self):
        cfg = {"tesla_method": "unofficial", "tesla_refresh_token": "tok", "tesla_fleet_refresh_token": ""}
        tools = _get_tesla_tools(cfg, "openai")
        assert len(tools) > 0
        assert tools[0]["type"] == "function"

    def test_returns_nine_tools(self):
        cfg = {"tesla_method": "fleet", "tesla_refresh_token": "", "tesla_fleet_refresh_token": "fleet_tok"}
        tools = _get_tesla_tools(cfg, "anthropic")
        assert len(tools) == 9

    def test_tool_names_include_trunk(self):
        cfg = {"tesla_method": "unofficial", "tesla_refresh_token": "tok", "tesla_fleet_refresh_token": ""}
        names = {t["name"] for t in _get_tesla_tools(cfg, "anthropic")}
        assert "actuate_trunk" in names
        assert "honk_horn" in names
        assert "flash_lights" in names


class TestBuildClient:
    def test_no_key_returns_none_for_anthropic(self):
        assert _build_client("anthropic", "") is None

    def test_no_key_returns_none_for_openai(self):
        assert _build_client("openai", "") is None


class TestGetUserLock:
    def test_returns_same_lock_for_same_user(self):
        lock1 = _get_user_lock("lockuser")
        lock2 = _get_user_lock("lockuser")
        assert lock1 is lock2

    def test_different_users_get_different_locks(self):
        assert _get_user_lock("user_a") is not _get_user_lock("user_b")


class TestSidsForUser:
    def test_finds_matching_sids(self):
        jarvis._sid_to_user["s1"] = "alice"
        jarvis._sid_to_user["s2"] = "bob"
        jarvis._sid_to_user["s3"] = "alice"
        try:
            assert set(_sids_for_user("alice")) == {"s1", "s3"}
        finally:
            jarvis._sid_to_user.pop("s1", None)
            jarvis._sid_to_user.pop("s2", None)
            jarvis._sid_to_user.pop("s3", None)

    def test_returns_empty_for_unknown_user(self):
        assert _sids_for_user("nobody") == []


# ── Webhook auth tests ─────────────────────────────────────────────────────────


class TestMessagesIngest:
    def test_no_auth_header_returns_401(self, api_client):
        resp = api_client.post("/api/messages/ingest", json={"sender": "Alice", "text": "Hi"})
        assert resp.status_code == 401

    def test_wrong_auth_scheme_returns_401(self, api_client):
        resp = api_client.post(
            "/api/messages/ingest",
            headers={"Authorization": "Basic dXNlcjpwYXNz"},
            json={"sender": "Alice", "text": "Hi"},
        )
        assert resp.status_code == 401

    def test_unknown_token_returns_401(self, api_client):
        with patch.object(jarvis, "_db_find_user_by_token", new=AsyncMock(return_value=None)):
            resp = api_client.post(
                "/api/messages/ingest",
                headers={"Authorization": "Bearer notarealtoken"},
                json={"sender": "Alice", "text": "Hi"},
            )
        assert resp.status_code == 401

    def test_valid_token_empty_body_returns_200(self, api_client):
        with (
            patch.object(jarvis, "_db_find_user_by_token", new=AsyncMock(return_value="user1")),
            patch.object(jarvis, "_db_store_phone_message", new=AsyncMock()),
        ):
            resp = api_client.post(
                "/api/messages/ingest",
                headers={"Authorization": "Bearer validtoken"},
                json={"sender": "Alice", "text": ""},
            )
        assert resp.status_code == 200
        assert resp.json() == {"ok": True}

    def test_valid_token_with_message_returns_200(self, api_client):
        with (
            patch.object(jarvis, "_db_find_user_by_token", new=AsyncMock(return_value="user1")),
            patch.object(jarvis, "_db_store_phone_message", new=AsyncMock()),
        ):
            resp = api_client.post(
                "/api/messages/ingest",
                headers={"Authorization": "Bearer validtoken"},
                json={"sender": "Bob", "text": "Are you free Saturday?"},
            )
        assert resp.status_code == 200
        assert resp.json() == {"ok": True}


# ── Phase 5 pure-function tests ───────────────────────────────────────────────


class TestEvaluateAlertCondition:
    def test_equals_match(self):
        assert _evaluate_alert_condition("on", "equals", "on") is True

    def test_equals_case_insensitive(self):
        assert _evaluate_alert_condition("ON", "equals", "on") is True

    def test_equals_no_match(self):
        assert _evaluate_alert_condition("off", "equals", "on") is False

    def test_not_equals_match(self):
        assert _evaluate_alert_condition("off", "not_equals", "on") is True

    def test_not_equals_no_match(self):
        assert _evaluate_alert_condition("on", "not_equals", "on") is False

    def test_greater_than_true(self):
        assert _evaluate_alert_condition("30", "greater_than", "25") is True

    def test_greater_than_false(self):
        assert _evaluate_alert_condition("20", "greater_than", "25") is False

    def test_less_than_true(self):
        assert _evaluate_alert_condition("10", "less_than", "20") is True

    def test_less_than_false(self):
        assert _evaluate_alert_condition("30", "less_than", "20") is False

    def test_numeric_condition_non_numeric_state_returns_false(self):
        assert _evaluate_alert_condition("unavailable", "greater_than", "25") is False

    def test_unknown_condition_returns_false(self):
        assert _evaluate_alert_condition("on", "contains", "on") is False


class TestDurationStr:
    def test_seconds_only(self):
        assert _duration_str(45) == "45s"

    def test_minutes_only(self):
        assert _duration_str(120) == "2m"

    def test_hours_only(self):
        assert _duration_str(3600) == "1h"

    def test_hours_and_minutes(self):
        assert _duration_str(3660) == "1h 1m"

    def test_hours_minutes_seconds(self):
        assert _duration_str(3661) == "1h 1m 1s"

    def test_zero(self):
        assert _duration_str(0) == "0s"

    def test_one_minute_thirty(self):
        assert _duration_str(90) == "1m 30s"


class TestGetPhase5Tools:
    _ha_cfg = {"ha_url": "http://ha.local", "ha_token": "tok"}
    _no_ha = {"ha_url": "", "ha_token": ""}

    def test_empty_when_no_ha_and_no_mqtt(self):
        with patch.object(jarvis, "MQTT_BROKER", ""):
            tools = _get_phase5_tools(self._no_ha, "anthropic")
        assert tools == []

    def test_ha_tools_included_when_configured(self):
        with patch.object(jarvis, "MQTT_BROKER", ""):
            tools = _get_phase5_tools(self._ha_cfg, "anthropic")
        names = {t["name"] for t in tools}
        assert "manage_routine" in names
        assert "manage_device_alert" in names

    def test_openai_format_when_provider_openai(self):
        with patch.object(jarvis, "MQTT_BROKER", ""):
            tools = _get_phase5_tools(self._ha_cfg, "openai")
        assert all(t["type"] == "function" for t in tools)

    def test_zigbee_tool_added_when_mqtt_configured(self):
        with patch.object(jarvis, "MQTT_BROKER", "mqtt.local"):
            tools = _get_phase5_tools(self._no_ha, "anthropic")
        names = {t["name"] for t in tools}
        assert "zigbee_control" in names


class TestExecuteNewsToolMocked:
    def _make_rss(self, titles):
        items = "".join(f"<item><title>{t}</title></item>" for t in titles)
        return f'<?xml version="1.0"?><rss><channel>{items}</channel></rss>'

    def test_returns_headlines(self):
        rss = self._make_rss(["Story One", "Story Two", "Story Three"])
        mock_resp = MagicMock()
        mock_resp.text = rss
        mock_resp.raise_for_status = MagicMock()

        async def mock_get(*a, **kw):
            return mock_resp

        mock_client = MagicMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = mock_get

        with patch("httpx.AsyncClient", return_value=mock_client):
            result = asyncio.run(_execute_news_tool({"category": "general", "count": 2}))
        assert "Story One" in result
        assert "Story Two" in result

    def test_handles_fetch_error(self):
        mock_client = MagicMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(side_effect=Exception("timeout"))

        with patch("httpx.AsyncClient", return_value=mock_client):
            result = asyncio.run(_execute_news_tool({}))
        assert "Could not fetch" in result


class TestExecuteTimerToolMocked:
    def test_set_timer(self):
        with patch.object(jarvis, "_db_set_timer", new=AsyncMock(return_value=42)):
            result = asyncio.run(_execute_timer_tool("u1", {"action": "set", "label": "Pasta", "duration_seconds": 300}))
        assert "Pasta" in result
        assert "42" in result

    def test_set_timer_zero_duration(self):
        result = asyncio.run(_execute_timer_tool("u1", {"action": "set", "duration_seconds": 0}))
        assert "greater than zero" in result

    def test_list_no_timers(self):
        with patch.object(jarvis, "_db_list_timers", new=AsyncMock(return_value=[])):
            result = asyncio.run(_execute_timer_tool("u1", {"action": "list"}))
        assert "No active timers" in result

    def test_list_with_timers(self):
        fire_at = datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None) + datetime.timedelta(minutes=5)
        timers = [{"id": 1, "label": "Laundry", "fire_at": fire_at}]
        with patch.object(jarvis, "_db_list_timers", new=AsyncMock(return_value=timers)):
            result = asyncio.run(_execute_timer_tool("u1", {"action": "list"}))
        assert "Laundry" in result

    def test_cancel_timer(self):
        with patch.object(jarvis, "_db_cancel_timer", new=AsyncMock(return_value=True)):
            result = asyncio.run(_execute_timer_tool("u1", {"action": "cancel", "timer_id": 1}))
        assert "cancelled" in result.lower()

    def test_cancel_no_id(self):
        result = asyncio.run(_execute_timer_tool("u1", {"action": "cancel"}))
        assert "Specify" in result

    def test_unknown_action(self):
        result = asyncio.run(_execute_timer_tool("u1", {"action": "explode"}))
        assert "Unknown" in result


class TestExecuteReminderToolMocked:
    def test_set_reminder(self):
        with patch.object(jarvis, "_db_set_reminder", new=AsyncMock(return_value=7)):
            result = asyncio.run(_execute_reminder_tool("u1", {"action": "set", "text": "Call Mom", "fire_at": "2030-01-01T09:00:00"}))
        assert "Call Mom" in result

    def test_set_reminder_invalid_datetime(self):
        result = asyncio.run(_execute_reminder_tool("u1", {"action": "set", "text": "x", "fire_at": "not-a-date"}))
        assert "Invalid" in result

    def test_set_reminder_missing_fields(self):
        result = asyncio.run(_execute_reminder_tool("u1", {"action": "set"}))
        assert "Specify" in result

    def test_list_no_reminders(self):
        with patch.object(jarvis, "_db_list_reminders", new=AsyncMock(return_value=[])):
            result = asyncio.run(_execute_reminder_tool("u1", {"action": "list"}))
        assert "No upcoming" in result

    def test_cancel_reminder(self):
        with patch.object(jarvis, "_db_cancel_reminder", new=AsyncMock(return_value=True)):
            result = asyncio.run(_execute_reminder_tool("u1", {"action": "cancel", "reminder_id": 3}))
        assert "cancelled" in result.lower()


class TestExecuteSharedListToolMocked:
    def test_read_empty(self):
        with patch.object(jarvis, "_db_get_shared_list", new=AsyncMock(return_value=[])):
            result = asyncio.run(_execute_shared_list_tool({"action": "read", "list_name": "shopping"}))
        assert "empty" in result.lower()

    def test_add_item(self):
        with (
            patch.object(jarvis, "_db_get_shared_list", new=AsyncMock(return_value=[])),
            patch.object(jarvis, "_db_update_shared_list", new=AsyncMock()),
        ):
            result = asyncio.run(_execute_shared_list_tool({"action": "add", "list_name": "shopping", "item": "Milk"}))
        assert "Milk" in result

    def test_remove_item(self):
        with (
            patch.object(jarvis, "_db_get_shared_list", new=AsyncMock(return_value=["Milk", "Eggs"])),
            patch.object(jarvis, "_db_update_shared_list", new=AsyncMock()),
        ):
            result = asyncio.run(_execute_shared_list_tool({"action": "remove", "list_name": "shopping", "item": "Milk"}))
        assert "Removed" in result

    def test_remove_not_found(self):
        with patch.object(jarvis, "_db_get_shared_list", new=AsyncMock(return_value=["Eggs"])):
            result = asyncio.run(_execute_shared_list_tool({"action": "remove", "list_name": "shopping", "item": "Milk"}))
        assert "not found" in result.lower()

    def test_clear_list(self):
        with (
            patch.object(jarvis, "_db_get_shared_list", new=AsyncMock(return_value=["Milk"])),
            patch.object(jarvis, "_db_update_shared_list", new=AsyncMock()),
        ):
            result = asyncio.run(_execute_shared_list_tool({"action": "clear", "list_name": "shopping"}))
        assert "cleared" in result.lower()


class TestExecuteRoutineToolMocked:
    _cfg = {"ha_url": "http://ha.local", "ha_token": "tok"}

    def test_create_routine(self):
        with patch.object(jarvis, "_db_create_routine", new=AsyncMock(return_value=5)):
            result = asyncio.run(
                _execute_routine_tool(
                    "u1",
                    {"action": "create", "name": "Good Morning", "steps": [{"type": "speak", "text": "Good morning!"}]},
                    self._cfg,
                )
            )
        assert "Good Morning" in result
        assert "5" in result

    def test_create_routine_no_name(self):
        result = asyncio.run(_execute_routine_tool("u1", {"action": "create"}, self._cfg))
        assert "name" in result.lower()

    def test_create_routine_no_steps(self):
        result = asyncio.run(_execute_routine_tool("u1", {"action": "create", "name": "Empty"}, self._cfg))
        assert "step" in result.lower()

    def test_list_no_routines(self):
        with patch.object(jarvis, "_db_list_routines", new=AsyncMock(return_value=[])):
            result = asyncio.run(_execute_routine_tool("u1", {"action": "list"}, self._cfg))
        assert "No routines" in result

    def test_delete_routine(self):
        with patch.object(jarvis, "_db_delete_routine", new=AsyncMock(return_value=True)):
            result = asyncio.run(_execute_routine_tool("u1", {"action": "delete", "routine_id": 1}, self._cfg))
        assert "deleted" in result.lower()

    def test_run_routine_not_found(self):
        with patch.object(jarvis, "_db_list_routines", new=AsyncMock(return_value=[])):
            result = asyncio.run(_execute_routine_tool("u1", {"action": "run", "name": "Nonexistent"}, self._cfg))
        assert "No routine" in result


class TestExecuteDeviceAlertToolMocked:
    def test_create_alert(self):
        with patch.object(jarvis, "_db_create_device_alert", new=AsyncMock(return_value=3)):
            result = asyncio.run(
                _execute_device_alert_tool(
                    "u1",
                    {
                        "action": "create",
                        "name": "Garage open",
                        "entity_id": "cover.garage",
                        "condition": "equals",
                        "value": "open",
                        "message": "The garage door is open!",
                    },
                )
            )
        assert "Garage open" in result
        assert "3" in result

    def test_create_alert_missing_fields(self):
        result = asyncio.run(_execute_device_alert_tool("u1", {"action": "create", "name": "x"}))
        assert "Specify" in result

    def test_list_no_alerts(self):
        with patch.object(jarvis, "_db_list_device_alerts", new=AsyncMock(return_value=[])):
            result = asyncio.run(_execute_device_alert_tool("u1", {"action": "list"}))
        assert "No alert" in result

    def test_delete_alert(self):
        with patch.object(jarvis, "_db_delete_device_alert", new=AsyncMock(return_value=True)):
            result = asyncio.run(_execute_device_alert_tool("u1", {"action": "delete", "alert_id": 2}))
        assert "deleted" in result.lower()

    def test_delete_no_id(self):
        result = asyncio.run(_execute_device_alert_tool("u1", {"action": "delete"}))
        assert "Specify" in result

    def test_unknown_action(self):
        result = asyncio.run(_execute_device_alert_tool("u1", {"action": "whatever"}))
        assert "Unknown" in result
