"""
Unit and HTTP-level tests for Jarvis.

Pure-function tests need no fixtures.
Webhook auth tests use the `api_client` fixture from conftest.py which
stubs out the database so no running PostgreSQL is required.
"""

import asyncio, datetime, app as jarvis, auth, db as db_mod, integrations.automation as automation_mod, integrations.finance as finance_mod, integrations.vision as vision_mod
from unittest.mock import AsyncMock, MagicMock, patch
from fastapi import HTTPException
from app import (
    _build_client,
    _calendar_configured,
    _contacts_configured,
    _get_user_lock,
    _ha_configured,
    _myq_configured,
    _myq_get_status,
    _sids_for_user,
    _split_sentences,
    _spotify_configured,
    _tesla_configured,
    _user_configured,
)
from integrations.ha import _ha_call_service, _ha_get_entity_state, _ha_get_states, _ha_headers, _validate_ha
from integrations.music.spotify import _execute_spotify_tool, _get_spotify_tools
from integrations.music import apple_music as apple_music_mod
from integrations.music import spotify as spotify_mod
from integrations.multiroom import presence as presence_mod
from integrations.pim import calendar as calendar_mod
from integrations.pim.contacts import _dedupe_preserve_order, _format_contact, _lookup_contacts, _score_contact_match
from integrations.music.apple_music import (
    _am_callbacks,
    _apple_music_configured,
    _apple_music_server_configured,
    _execute_apple_music_tool,
    _get_apple_music_tools,
    _require_runtime,
    _resolve_apple_music_callback,
)
from integrations.myq import _get_myq_tools, _myq_set_door
from integrations.pim.calendar import _execute_calendar_tool, _parse_ical_events
from integrations.pim.contacts import _execute_contact_lookup_tool, _parse_vcards
from integrations.pim.dav import (
    _dav_display_name,
    _dav_href,
    _dav_multistatus_responses,
    _dav_prop_href,
    _dav_propfind_body,
    _dav_raise_for_status,
    _dav_resource_types,
    _dav_response_for_url,
    _dav_response_prop,
    _dav_join,
    _ensure_trailing_slash,
    _pick_best_dav_collection,
    _resolve_dav_collection,
)
from integrations.pim.timers import _duration_str, _execute_news_tool, _execute_reminder_tool, _execute_timer_tool, _get_pim_tools
from integrations.automation import _evaluate_alert_condition, _execute_device_alert_tool, _execute_routine_tool, _get_automation_tools
from integrations.shared_lists import _execute_shared_list_tool
from integrations.multiroom.snapcast import _execute_snapcast_tool, _get_snapcast_tools, _snapcast_get_status
from integrations.tesla import _execute_tesla_tool, _get_tesla_tools, _tesla_base_url, _tesla_pick_vehicle
import integrations.tesla as tesla_mod
from llm import _build_system_prompt
from integrations.tesla import _c_to_f
from integrations.finance import _execute_finance_tool, _finance_configured, _get_finance_tools, _normalize_account, _normalize_transaction

# ── Shared async-mock helpers ────────────────────────────────────────────────
#
# `async with X() as y:` shows up constantly in this codebase (httpx clients,
# db pool.acquire(), user locks). These build the MagicMock plumbing once so
# individual tests can stay one-liners.


def _async_cm(return_value=None):
    """Mock usable as `async with X() as y:` where y is return_value."""
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=return_value)
    cm.__aexit__ = AsyncMock(return_value=False)
    return cm


def _mock_async_client(**method_returns):
    """Mock usable as `async with httpx.AsyncClient(...) as c:`.
    Pass e.g. get=resp, or get=AsyncMock(side_effect=...) for custom behavior.
    """
    client = _async_cm()
    client.__aenter__ = AsyncMock(return_value=client)
    for method, value in method_returns.items():
        setattr(client, method, value if isinstance(value, AsyncMock) else AsyncMock(return_value=value))
    return client


def _mock_asyncpg_pool(*, fetchrow=None, fetch=None, fetchval=None, execute=None):
    """Mock asyncpg pool; pool.acquire() yields a conn with configurable
    fetchrow/fetch/fetchval/execute return values (fetch defaults to []).
    """
    conn = MagicMock()
    conn.fetchrow = AsyncMock(return_value=fetchrow)
    conn.fetch = AsyncMock(return_value=fetch if fetch is not None else [])
    conn.fetchval = AsyncMock(return_value=fetchval)
    conn.execute = AsyncMock(return_value=execute)
    pool = MagicMock()
    pool.acquire = MagicMock(return_value=_async_cm(conn))
    return pool, conn


def _seed_user_state(config=None, role="user", client=None):
    """Pre-populate app.py's `_user_states["local"]` cache so route handlers
    that call `_get_user_state` skip the real DB-loading path entirely.
    `_get_current_user` always resolves to "local" under the `api_client`
    fixture, since `_oidc_config` is never set in tests.
    """
    state = {
        "config": config if config is not None else {},
        "client": client if client is not None else MagicMock(),
        "provider": "anthropic",
        "conversation": [],
        "role": role,
        "user_id": "local",
    }
    jarvis._user_states["local"] = state
    return state


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
        names = {tool["name"] for tool in _get_pim_tools({}, "anthropic")}
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
        names = {tool["function"]["name"] for tool in _get_pim_tools(cfg, "openai")}
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
        with patch("integrations.music.spotify._spotify_req", new=AsyncMock(return_value=self._mock_resp(204, ""))):
            result = asyncio.run(_execute_spotify_tool("spotify_now_playing", {}, "u1", self._cfg))
        assert "Nothing" in result

    def test_now_playing_track(self):
        data = {"is_playing": True, "item": {"name": "Get Lucky", "artists": [{"name": "Daft Punk"}]}}
        with patch("integrations.music.spotify._spotify_req", new=AsyncMock(return_value=self._mock_resp(200, "x", data))):
            result = asyncio.run(_execute_spotify_tool("spotify_now_playing", {}, "u1", self._cfg))
        assert "Get Lucky" in result
        assert "Daft Punk" in result

    def test_play_success(self):
        with patch("integrations.music.spotify._spotify_req", new=AsyncMock(return_value=self._mock_resp(204))):
            result = asyncio.run(_execute_spotify_tool("spotify_play", {}, "u1", self._cfg))
        assert "playback" in result.lower()

    def test_pause_success(self):
        with patch("integrations.music.spotify._spotify_req", new=AsyncMock(return_value=self._mock_resp(204))):
            result = asyncio.run(_execute_spotify_tool("spotify_pause", {}, "u1", self._cfg))
        assert "paused" in result.lower()

    def test_next_success(self):
        with patch("integrations.music.spotify._spotify_req", new=AsyncMock(return_value=self._mock_resp(204))):
            result = asyncio.run(_execute_spotify_tool("spotify_next", {}, "u1", self._cfg))
        assert "next" in result.lower() or "skipped" in result.lower()

    def test_previous_success(self):
        with patch("integrations.music.spotify._spotify_req", new=AsyncMock(return_value=self._mock_resp(204))):
            result = asyncio.run(_execute_spotify_tool("spotify_previous", {}, "u1", self._cfg))
        assert "previous" in result.lower() or "back" in result.lower()

    def test_volume_clamped_and_set(self):
        with patch("integrations.music.spotify._spotify_req", new=AsyncMock(return_value=self._mock_resp(204))):
            result = asyncio.run(_execute_spotify_tool("spotify_volume", {"volume_percent": 70}, "u1", self._cfg))
        assert "70" in result

    def test_volume_clamped_above_100(self):
        with patch("integrations.music.spotify._spotify_req", new=AsyncMock(return_value=self._mock_resp(204))):
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

        with patch("integrations.music.spotify._spotify_req", new=mock_req):
            result = asyncio.run(_execute_spotify_tool("spotify_search_and_play", {"query": "Around the World", "type": "track"}, "u1", self._cfg))
        assert "Around the World" in result

    def test_search_and_play_not_found(self):
        search_data = {"tracks": {"items": []}}
        with patch("integrations.music.spotify._spotify_req", new=AsyncMock(return_value=self._mock_resp(200, "x", search_data))):
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


class TestExecuteContactLookupTool:
    _cfg = {
        "contacts_url": "https://dav.example.com/ab/",
        "contacts_username": "me",
        "contacts_password": "secret",
    }

    def test_formats_contact_matches(self):
        match = {"name": "Mom", "phones": ["+15551234567"], "emails": ["mom@example.com"], "nicknames": []}
        with patch("integrations.pim.contacts._lookup_contacts", new=AsyncMock(return_value=[match])):
            result = asyncio.run(_execute_contact_lookup_tool(self._cfg, {"query": "Mom", "preferred_channel": "phone"}))
        assert "Mom" in result
        assert "+15551234567" in result

    def test_returns_not_found_message(self):
        with patch("integrations.pim.contacts._lookup_contacts", new=AsyncMock(return_value=[])):
            result = asyncio.run(_execute_contact_lookup_tool(self._cfg, {"query": "Nobody"}))
        assert "No contacts matched" in result

    def test_not_configured(self):
        result = asyncio.run(_execute_contact_lookup_tool({}, {"query": "Mom"}))
        assert "not configured" in result

    def test_empty_query(self):
        result = asyncio.run(_execute_contact_lookup_tool(self._cfg, {"query": ""}))
        assert "Provide a name" in result

    def test_invalid_channel_defaults_to_any(self):
        with patch("integrations.pim.contacts._lookup_contacts", new=AsyncMock(return_value=[])):
            result = asyncio.run(_execute_contact_lookup_tool(self._cfg, {"query": "Mom", "preferred_channel": "bogus"}))
        assert "No contacts matched" in result

    def test_lookup_error_surfaced(self):
        with patch("integrations.pim.contacts._lookup_contacts", new=AsyncMock(side_effect=ValueError("auth failed"))):
            result = asyncio.run(_execute_contact_lookup_tool(self._cfg, {"query": "Mom"}))
        assert "Could not search contacts" in result


class TestScoreContactMatch:
    def test_exact_name_match_scores_highest(self):
        contact = {"name": "Mom", "nicknames": [], "emails": [], "phones": []}
        assert _score_contact_match(contact, "mom", "") == 100

    def test_exact_nickname_match(self):
        contact = {"name": "Robert Smith", "nicknames": ["Bob"], "emails": [], "phones": []}
        assert _score_contact_match(contact, "bob", "") == 95

    def test_name_starts_with_query(self):
        contact = {"name": "Robert Smith", "nicknames": [], "emails": [], "phones": []}
        assert _score_contact_match(contact, "rob", "") == 85

    def test_nickname_starts_with_query(self):
        contact = {"name": "Robert", "nicknames": ["Bobby"], "emails": [], "phones": []}
        assert _score_contact_match(contact, "bob", "") == 80

    def test_name_contains_query(self):
        contact = {"name": "Robert Smith", "nicknames": [], "emails": [], "phones": []}
        assert _score_contact_match(contact, "ert sm", "") == 70

    def test_nickname_contains_query(self):
        contact = {"name": "Robert", "nicknames": ["Bobcat"], "emails": [], "phones": []}
        assert _score_contact_match(contact, "obc", "") == 65

    def test_email_contains_query(self):
        contact = {"name": "Robert", "nicknames": [], "emails": ["robert@example.com"], "phones": []}
        assert _score_contact_match(contact, "example", "") == 60

    def test_phone_digits_match(self):
        contact = {"name": "Robert", "nicknames": [], "emails": [], "phones": ["+1 (555) 123-4567"]}
        assert _score_contact_match(contact, "", "5551234567") == 60

    def test_no_match_returns_zero(self):
        contact = {"name": "Robert", "nicknames": [], "emails": [], "phones": []}
        assert _score_contact_match(contact, "zzz", "") == 0

    def test_empty_query_returns_zero(self):
        contact = {"name": "Robert", "nicknames": [], "emails": [], "phones": []}
        assert _score_contact_match(contact, "", "") == 0


class TestFormatContact:
    def test_name_with_phone_and_email(self):
        contact = {"name": "Mom", "phones": ["555-1234"], "emails": ["mom@example.com"]}
        result = _format_contact(contact, "any")
        assert result.startswith("Mom — ")
        assert "phone: 555-1234" in result
        assert "email: mom@example.com" in result

    def test_preferred_channel_phone_only(self):
        contact = {"name": "Mom", "phones": ["555-1234"], "emails": ["mom@example.com"]}
        result = _format_contact(contact, "phone")
        assert "phone:" in result
        assert "email:" not in result

    def test_unnamed_contact_falls_back_to_email(self):
        contact = {"name": "", "phones": [], "emails": ["a@b.com"]}
        assert _format_contact(contact, "any") == "a@b.com — email: a@b.com"

    def test_no_name_no_contact_info_falls_back_to_placeholder(self):
        contact = {"name": "", "phones": [], "emails": []}
        assert _format_contact(contact, "any") == "Unnamed contact"

    def test_name_only_no_details(self):
        contact = {"name": "Ghost", "phones": [], "emails": []}
        assert _format_contact(contact, "any") == "Ghost"


class TestDedupePreserveOrder:
    def test_removes_case_insensitive_duplicates_preserving_order(self):
        assert _dedupe_preserve_order(["A", "b", "a", "B", "c"]) == ["A", "b", "c"]

    def test_skips_empty_strings(self):
        assert _dedupe_preserve_order(["", "x", ""]) == ["x"]


class TestLookupContacts:
    _cfg = {"contacts_url": "https://dav.example.com/ab/", "contacts_username": "me", "contacts_password": "secret"}

    _VCARD_MULTISTATUS = (
        '<?xml version="1.0" encoding="utf-8"?>'
        '<D:multistatus xmlns:D="DAV:" xmlns:A="urn:ietf:params:xml:ns:carddav">'
        "<D:response><D:href>/ab/mom.vcf</D:href><D:propstat><D:prop>"
        "<A:address-data>BEGIN:VCARD&#10;VERSION:3.0&#10;FN:Mom&#10;TEL:+15551234567&#10;END:VCARD&#10;</A:address-data>"
        "</D:prop><D:status>HTTP/1.1 200 OK</D:status></D:propstat></D:response>"
        "</D:multistatus>"
    )

    def test_finds_and_scores_matches(self):
        resp = MagicMock(status_code=207, text=self._VCARD_MULTISTATUS)
        with patch("integrations.pim.contacts._dav_request", new=AsyncMock(return_value=resp)):
            matches = asyncio.run(_lookup_contacts(self._cfg, "Mom"))
        assert len(matches) == 1
        assert matches[0]["name"] == "Mom"

    def test_preferred_channel_phone_filters_email_only_contacts(self):
        resp = MagicMock(status_code=207, text=self._VCARD_MULTISTATUS)
        with patch("integrations.pim.contacts._dav_request", new=AsyncMock(return_value=resp)):
            matches = asyncio.run(_lookup_contacts(self._cfg, "Mom", preferred_channel="email"))
        assert matches == []

    def test_no_matches_for_unrelated_query(self):
        resp = MagicMock(status_code=207, text=self._VCARD_MULTISTATUS)
        with patch("integrations.pim.contacts._dav_request", new=AsyncMock(return_value=resp)):
            matches = asyncio.run(_lookup_contacts(self._cfg, "Zzyzx"))
        assert matches == []


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

    def test_configured_user_schedules_classification(self, api_client):
        jarvis._user_states["msguser"] = {
            "config": {"provider": "anthropic", "api_key": "k"},
            "client": MagicMock(),
            "provider": "anthropic",
            "conversation": [],
            "role": "user",
            "user_id": "msguser",
        }
        with (
            patch.object(jarvis, "_db_find_user_by_token", new=AsyncMock(return_value="msguser")),
            patch.object(jarvis, "_classify_and_notify", new=AsyncMock()) as mock_classify,
        ):
            resp = api_client.post(
                "/api/messages/ingest",
                headers={"Authorization": "Bearer validtoken"},
                json={"sender": "Bob", "text": "Are you free Saturday?"},
            )
        assert resp.status_code == 200
        mock_classify.assert_called_once()
        jarvis._user_states.pop("msguser", None)


class TestClassifyMessage:
    def _state(self, provider="anthropic", client=None):
        return {"provider": provider, "config": {"model": "m"}, "client": client or MagicMock()}

    def test_anthropic_flags_important(self):
        client = MagicMock()
        reply_msg = MagicMock()
        reply_msg.content = [MagicMock(text="yes: dinner invite")]
        client.messages.create = AsyncMock(return_value=reply_msg)
        important, reason = asyncio.run(jarvis._classify_message(self._state(client=client), "Bob", "Dinner Saturday?"))
        assert important is True
        assert reason == "dinner invite"

    def test_anthropic_not_important(self):
        client = MagicMock()
        reply_msg = MagicMock()
        reply_msg.content = [MagicMock(text="no")]
        client.messages.create = AsyncMock(return_value=reply_msg)
        important, reason = asyncio.run(jarvis._classify_message(self._state(client=client), "Bob", "lol ok"))
        assert important is False
        assert reason == ""

    def test_openai_flags_important(self):
        client = MagicMock()
        resp = MagicMock()
        resp.choices = [MagicMock(message=MagicMock(content="yes: urgent deadline"))]
        client.chat.completions.create = AsyncMock(return_value=resp)
        important, reason = asyncio.run(jarvis._classify_message(self._state(provider="openai", client=client), "Bob", "Need this by 5pm"))
        assert important is True
        assert reason == "urgent deadline"

    def test_exception_returns_not_important(self):
        client = MagicMock()
        client.messages.create = AsyncMock(side_effect=Exception("API down"))
        important, reason = asyncio.run(jarvis._classify_message(self._state(client=client), "Bob", "hi"))
        assert important is False
        assert reason == ""


class TestClassifyAndNotify:
    def test_notifies_when_important(self):
        with (
            patch.object(jarvis, "_classify_message", new=AsyncMock(return_value=(True, "urgent"))),
            patch.object(jarvis, "_db_store_phone_message", new=AsyncMock()) as mock_store,
            patch.object(jarvis, "_sids_for_user", return_value=["sid1"]),
            patch.object(jarvis, "sio") as mock_sio,
        ):
            mock_sio.emit = AsyncMock()
            asyncio.run(jarvis._classify_and_notify("u1", "Bob", "hi", {}))
        mock_store.assert_awaited_once_with("u1", "Bob", "hi", True, "urgent")
        mock_sio.emit.assert_awaited_once()

    def test_no_notify_when_not_important(self):
        with (
            patch.object(jarvis, "_classify_message", new=AsyncMock(return_value=(False, ""))),
            patch.object(jarvis, "_db_store_phone_message", new=AsyncMock()),
            patch.object(jarvis, "sio") as mock_sio,
        ):
            mock_sio.emit = AsyncMock()
            asyncio.run(jarvis._classify_and_notify("u1", "Bob", "hi", {}))
        mock_sio.emit.assert_not_awaited()


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
        with patch("integrations.automation.MQTT_BROKER", ""):
            tools = _get_automation_tools(self._no_ha, "anthropic")
        assert tools == []

    def test_ha_tools_included_when_configured(self):
        with patch("integrations.automation.MQTT_BROKER", ""):
            tools = _get_automation_tools(self._ha_cfg, "anthropic")
        names = {t["name"] for t in tools}
        assert "manage_routine" in names
        assert "manage_device_alert" in names

    def test_openai_format_when_provider_openai(self):
        with patch("integrations.automation.MQTT_BROKER", ""):
            tools = _get_automation_tools(self._ha_cfg, "openai")
        assert all(t["type"] == "function" for t in tools)

    def test_zigbee_tool_added_when_mqtt_configured(self):
        with patch("integrations.automation.MQTT_BROKER", "mqtt.local"):
            tools = _get_automation_tools(self._no_ha, "anthropic")
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

        with patch("httpx.AsyncClient", return_value=_mock_async_client(get=mock_resp)):
            result = asyncio.run(_execute_news_tool({"category": "general", "count": 2}))
        assert "Story One" in result
        assert "Story Two" in result

    def test_handles_fetch_error(self):
        with patch("httpx.AsyncClient", return_value=_mock_async_client(get=AsyncMock(side_effect=Exception("timeout")))):
            result = asyncio.run(_execute_news_tool({}))
        assert "Could not fetch" in result


class TestExecuteTimerToolMocked:
    def test_set_timer(self):
        with patch("integrations.pim.timers._db_set_timer", new=AsyncMock(return_value=42)):
            result = asyncio.run(_execute_timer_tool("u1", {"action": "set", "label": "Pasta", "duration_seconds": 300}))
        assert "Pasta" in result
        assert "42" in result

    def test_set_timer_zero_duration(self):
        result = asyncio.run(_execute_timer_tool("u1", {"action": "set", "duration_seconds": 0}))
        assert "greater than zero" in result

    def test_list_no_timers(self):
        with patch("integrations.pim.timers._db_list_timers", new=AsyncMock(return_value=[])):
            result = asyncio.run(_execute_timer_tool("u1", {"action": "list"}))
        assert "No active timers" in result

    def test_list_with_timers(self):
        fire_at = datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None) + datetime.timedelta(minutes=5)
        timers = [{"id": 1, "label": "Laundry", "fire_at": fire_at}]
        with patch("integrations.pim.timers._db_list_timers", new=AsyncMock(return_value=timers)):
            result = asyncio.run(_execute_timer_tool("u1", {"action": "list"}))
        assert "Laundry" in result

    def test_cancel_timer(self):
        with patch("integrations.pim.timers._db_cancel_timer", new=AsyncMock(return_value=True)):
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
        with patch("integrations.pim.timers._db_set_reminder", new=AsyncMock(return_value=7)):
            result = asyncio.run(_execute_reminder_tool("u1", {"action": "set", "text": "Call Mom", "fire_at": "2030-01-01T09:00:00"}))
        assert "Call Mom" in result

    def test_set_reminder_invalid_datetime(self):
        result = asyncio.run(_execute_reminder_tool("u1", {"action": "set", "text": "x", "fire_at": "not-a-date"}))
        assert "Invalid" in result

    def test_set_reminder_missing_fields(self):
        result = asyncio.run(_execute_reminder_tool("u1", {"action": "set"}))
        assert "Specify" in result

    def test_list_no_reminders(self):
        with patch("integrations.pim.timers._db_list_reminders", new=AsyncMock(return_value=[])):
            result = asyncio.run(_execute_reminder_tool("u1", {"action": "list"}))
        assert "No upcoming" in result

    def test_cancel_reminder(self):
        with patch("integrations.pim.timers._db_cancel_reminder", new=AsyncMock(return_value=True)):
            result = asyncio.run(_execute_reminder_tool("u1", {"action": "cancel", "reminder_id": 3}))
        assert "cancelled" in result.lower()


class TestExecuteSharedListToolMocked:
    def test_read_empty(self):
        with patch("integrations.shared_lists._db_get_shared_list", new=AsyncMock(return_value=[])):
            result = asyncio.run(_execute_shared_list_tool({"action": "read", "list_name": "shopping"}))
        assert "empty" in result.lower()

    def test_add_item(self):
        with (
            patch("integrations.shared_lists._db_get_shared_list", new=AsyncMock(return_value=[])),
            patch("integrations.shared_lists._db_update_shared_list", new=AsyncMock()),
        ):
            result = asyncio.run(_execute_shared_list_tool({"action": "add", "list_name": "shopping", "item": "Milk"}))
        assert "Milk" in result

    def test_remove_item(self):
        with (
            patch("integrations.shared_lists._db_get_shared_list", new=AsyncMock(return_value=["Milk", "Eggs"])),
            patch("integrations.shared_lists._db_update_shared_list", new=AsyncMock()),
        ):
            result = asyncio.run(_execute_shared_list_tool({"action": "remove", "list_name": "shopping", "item": "Milk"}))
        assert "Removed" in result

    def test_remove_not_found(self):
        with patch("integrations.shared_lists._db_get_shared_list", new=AsyncMock(return_value=["Eggs"])):
            result = asyncio.run(_execute_shared_list_tool({"action": "remove", "list_name": "shopping", "item": "Milk"}))
        assert "not found" in result.lower()

    def test_clear_list(self):
        with (
            patch("integrations.shared_lists._db_get_shared_list", new=AsyncMock(return_value=["Milk"])),
            patch("integrations.shared_lists._db_update_shared_list", new=AsyncMock()),
        ):
            result = asyncio.run(_execute_shared_list_tool({"action": "clear", "list_name": "shopping"}))
        assert "cleared" in result.lower()


class TestExecuteRoutineToolMocked:
    _cfg = {"ha_url": "http://ha.local", "ha_token": "tok"}

    def test_create_routine(self):
        with patch("integrations.automation._db_create_routine", new=AsyncMock(return_value=5)):
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
        with patch("integrations.automation._db_list_routines", new=AsyncMock(return_value=[])):
            result = asyncio.run(_execute_routine_tool("u1", {"action": "list"}, self._cfg))
        assert "No routines" in result

    def test_delete_routine(self):
        with patch("integrations.automation._db_delete_routine", new=AsyncMock(return_value=True)):
            result = asyncio.run(_execute_routine_tool("u1", {"action": "delete", "routine_id": 1}, self._cfg))
        assert "deleted" in result.lower()

    def test_run_routine_not_found(self):
        with patch("integrations.automation._db_list_routines", new=AsyncMock(return_value=[])):
            result = asyncio.run(_execute_routine_tool("u1", {"action": "run", "name": "Nonexistent"}, self._cfg))
        assert "No routine" in result

    def test_list_formats_routines(self):
        routines = [{"id": 1, "name": "Good Morning", "active": True, "steps": [{"type": "speak"}], "trigger_phrases": ["good morning"]}]
        with patch("integrations.automation._db_list_routines", new=AsyncMock(return_value=routines)):
            result = asyncio.run(_execute_routine_tool("u1", {"action": "list"}, self._cfg))
        assert "Good Morning" in result
        assert "good morning" in result

    def test_delete_routine_no_id(self):
        result = asyncio.run(_execute_routine_tool("u1", {"action": "delete"}, self._cfg))
        assert "Specify" in result

    def test_run_routine_found_schedules_task(self):
        routines = [{"id": 1, "name": "Good Night", "active": True, "steps": [{"type": "speak", "text": "Night"}], "trigger_phrases": []}]
        with patch("integrations.automation._db_list_routines", new=AsyncMock(return_value=routines)):
            result = asyncio.run(_execute_routine_tool("u1", {"action": "run", "name": "good night"}, self._cfg))
        assert "Running routine 'good night'" in result


class TestExecuteDeviceAlertToolMocked:
    def test_create_alert(self):
        with patch("integrations.automation._db_create_device_alert", new=AsyncMock(return_value=3)):
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
        with patch("integrations.automation._db_list_device_alerts", new=AsyncMock(return_value=[])):
            result = asyncio.run(_execute_device_alert_tool("u1", {"action": "list"}))
        assert "No alert" in result

    def test_delete_alert(self):
        with patch("integrations.automation._db_delete_device_alert", new=AsyncMock(return_value=True)):
            result = asyncio.run(_execute_device_alert_tool("u1", {"action": "delete", "alert_id": 2}))
        assert "deleted" in result.lower()

    def test_delete_no_id(self):
        result = asyncio.run(_execute_device_alert_tool("u1", {"action": "delete"}))
        assert "Specify" in result

    def test_unknown_action(self):
        result = asyncio.run(_execute_device_alert_tool("u1", {"action": "whatever"}))
        assert "Unknown" in result


class TestFinanceConfigured:
    def test_no_items_returns_false(self):
        with patch("integrations.finance._db_list_plaid_items", new=AsyncMock(return_value=[])):
            assert asyncio.run(_finance_configured("user1")) is False

    def test_with_items_returns_true(self):
        with patch("integrations.finance._db_list_plaid_items", new=AsyncMock(return_value=[{"id": 1}])):
            assert asyncio.run(_finance_configured("user1")) is True


class TestGetFinanceTools:
    def test_empty_when_not_configured(self):
        with patch("integrations.finance._finance_configured", new=AsyncMock(return_value=False)):
            assert asyncio.run(_get_finance_tools("user1", "anthropic")) == []

    def test_anthropic_tools_when_configured(self):
        with patch("integrations.finance._finance_configured", new=AsyncMock(return_value=True)):
            tools = asyncio.run(_get_finance_tools("user1", "anthropic"))
            names = [t["name"] for t in tools]
            assert "get_account_balances" in names
            assert "get_recent_transactions" in names
            assert "get_spending_by_category" in names
            assert "set_transaction_category" in names

    def test_openai_tools_when_configured(self):
        with patch("integrations.finance._finance_configured", new=AsyncMock(return_value=True)):
            tools = asyncio.run(_get_finance_tools("user1", "openai"))
            names = [t["function"]["name"] for t in tools]
            assert "get_account_balances" in names


class TestExecuteFinanceTool:
    def test_no_user_id_returns_error(self):
        result = asyncio.run(_execute_finance_tool("get_account_balances", {}, ""))
        assert "No user context" in result

    def test_unknown_tool_name(self):
        result = asyncio.run(_execute_finance_tool("bogus_tool", {}, "user1"))
        assert "Unknown finance tool" in result

    def test_get_account_balances_formats_output(self):
        fake_accounts = [{"name": "Checking", "mask": "1234", "balance_current": 100.5}]
        with patch("integrations.finance._db_list_plaid_accounts", new=AsyncMock(return_value=fake_accounts)):
            result = asyncio.run(_execute_finance_tool("get_account_balances", {}, "user1"))
        assert "Checking" in result and "100.50" in result

    def test_get_account_balances_no_accounts(self):
        with patch("integrations.finance._db_list_plaid_accounts", new=AsyncMock(return_value=[])):
            result = asyncio.run(_execute_finance_tool("get_account_balances", {}, "user1"))
        assert "No linked bank accounts" in result

    def test_get_recent_transactions_formats_output(self):
        fake_txns = [
            {
                "date": datetime.date(2026, 6, 28),
                "merchant_name": "Trader Joe's",
                "name": "TJ PURCHASE",
                "category": "FOOD_AND_DRINK",
                "personal_finance_category": "FOOD_AND_DRINK_GROCERIES",
                "category_override": None,
                "amount": 42.10,
                "pending": False,
            }
        ]
        with patch("integrations.finance._db_get_recent_transactions", new=AsyncMock(return_value=fake_txns)):
            result = asyncio.run(_execute_finance_tool("get_recent_transactions", {}, "user1"))
        assert "Trader Joe's" in result and "42.10" in result

    def test_get_spending_by_category_formats_output(self):
        fake_rows = [{"category": "Groceries", "total": 340.12}]
        with patch("integrations.finance._db_get_spending_by_category", new=AsyncMock(return_value=fake_rows)):
            result = asyncio.run(_execute_finance_tool("get_spending_by_category", {}, "user1"))
        assert "Groceries" in result and "340.12" in result

    def test_set_transaction_category_no_match(self):
        with patch("integrations.finance._db_find_transaction_by_merchant", new=AsyncMock(return_value=None)):
            result = asyncio.run(_execute_finance_tool("set_transaction_category", {"merchant": "Nowhere", "category": "Misc"}, "user1"))
        assert "No transaction found" in result

    def test_set_transaction_category_updates(self):
        fake_txn = {"id": 5, "amount": 12.5, "date": datetime.date(2026, 6, 20), "merchant_name": "Cafe", "name": "CAFE PURCHASE"}
        with (
            patch("integrations.finance._db_find_transaction_by_merchant", new=AsyncMock(return_value=fake_txn)),
            patch("integrations.finance._db_set_transaction_category_override", new=AsyncMock(return_value=True)) as mock_set,
        ):
            result = asyncio.run(_execute_finance_tool("set_transaction_category", {"merchant": "Cafe", "category": "Dining"}, "user1"))
        mock_set.assert_awaited_once_with("user1", 5, "Dining")
        assert "Cafe" in result and "Dining" in result

    def test_missing_merchant_or_category(self):
        result = asyncio.run(_execute_finance_tool("set_transaction_category", {"merchant": "", "category": "Dining"}, "user1"))
        assert "required" in result


class TestNormalizeTransaction:
    def test_uses_personal_finance_category(self):
        t = {
            "account_id": "a1",
            "transaction_id": "t1",
            "amount": 10.0,
            "iso_currency_code": "USD",
            "date": "2026-06-28",
            "merchant_name": "Store",
            "name": "STORE PURCHASE",
            "personal_finance_category": {"primary": "SHOPS", "detailed": "SHOPS_GENERAL"},
            "category": ["Shops"],
            "pending": False,
        }
        result = _normalize_transaction(t)
        assert result["category"] == "SHOPS"
        assert result["personal_finance_category"] == "SHOPS_GENERAL"
        assert result["date"] == datetime.date(2026, 6, 28)

    def test_falls_back_to_legacy_category(self):
        t = {
            "account_id": "a1",
            "transaction_id": "t2",
            "amount": 5.0,
            "date": "2026-06-01",
            "name": "MISC",
            "category": ["Legacy Category"],
        }
        result = _normalize_transaction(t)
        assert result["category"] == "Legacy Category"
        assert result["personal_finance_category"] == ""


class TestNormalizeAccount:
    def test_extracts_balances(self):
        a = {
            "account_id": "acc1",
            "name": "Checking",
            "mask": "1234",
            "type": "depository",
            "subtype": "checking",
            "balances": {"current": 100.0, "available": 90.0, "limit": None, "iso_currency_code": "USD"},
        }
        result = _normalize_account(a)
        assert result["balance_current"] == 100.0
        assert result["balance_available"] == 90.0
        assert result["iso_currency"] == "USD"


# ── auth.py ─────────────────────────────────────────────────────────────────


class TestAuthSession:
    def test_sign_and_verify_roundtrip(self):
        auth.init_signer("test-secret-key")
        token = auth._sign_session("user-123")
        assert auth._verify_session(token) == "user-123"

    def test_verify_rejects_tampered_token(self):
        auth.init_signer("test-secret-key")
        token = auth._sign_session("user-123")
        assert auth._verify_session(token + "x") is None

    def test_verify_rejects_garbage(self):
        auth.init_signer("test-secret-key")
        assert auth._verify_session("not-a-real-token") is None

    def test_get_current_user_no_oidc_returns_local(self):
        with patch.object(auth, "_oidc_config", None):
            request = MagicMock()
            request.cookies = {}
            assert auth._get_current_user(request) == "local"

    def test_get_current_user_with_oidc_no_cookie(self):
        with patch.object(auth, "_oidc_config", {"issuer": "x"}):
            request = MagicMock()
            request.cookies = {}
            assert auth._get_current_user(request) is None

    def test_get_current_user_with_oidc_valid_cookie(self):
        auth.init_signer("test-secret-key")
        token = auth._sign_session("user-456")
        with patch.object(auth, "_oidc_config", {"issuer": "x"}):
            request = MagicMock()
            request.cookies = {"jarvis_session": token}
            assert auth._get_current_user(request) == "user-456"

    def test_get_user_from_environ_no_oidc_returns_local(self):
        with patch.object(auth, "_oidc_config", None):
            assert auth._get_user_from_environ({}) == "local"

    def test_get_user_from_environ_missing_cookie_header(self):
        with patch.object(auth, "_oidc_config", {"issuer": "x"}):
            assert auth._get_user_from_environ({}) is None

    def test_get_user_from_environ_parses_cookie(self):
        auth.init_signer("test-secret-key")
        token = auth._sign_session("user-789")
        environ = {"headers": [(b"cookie", f"other=1; jarvis_session={token}".encode())]}
        with patch.object(auth, "_oidc_config", {"issuer": "x"}):
            assert auth._get_user_from_environ(environ) == "user-789"

    def test_fetch_oidc_config_noop_when_no_discovery_url(self):
        with patch("auth.OIDC_DISCOVERY_URL", ""):
            asyncio.run(auth._fetch_oidc_config())

    def test_fetch_oidc_config_success(self):
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json = MagicMock(return_value={"issuer": "https://auth.example.com"})
        with (
            patch("auth.OIDC_DISCOVERY_URL", "https://auth.example.com/.well-known/openid-configuration"),
            patch("httpx.AsyncClient", return_value=_mock_async_client(get=mock_resp)),
        ):
            asyncio.run(auth._fetch_oidc_config())
        assert auth._oidc_config == {"issuer": "https://auth.example.com"}
        auth._oidc_config = None

    def test_fetch_oidc_config_handles_failure(self):
        with (
            patch("auth.OIDC_DISCOVERY_URL", "https://auth.example.com/.well-known/openid-configuration"),
            patch("httpx.AsyncClient", return_value=_mock_async_client(get=AsyncMock(side_effect=Exception("timeout")))),
        ):
            asyncio.run(auth._fetch_oidc_config())  # should not raise


# ── integrations/ha.py ───────────────────────────────────────────────────────


class TestHaIntegration:
    def test_validate_ha_success(self):
        resp = MagicMock(status_code=200)
        with patch("httpx.AsyncClient", return_value=_mock_async_client(get=resp)):
            ok, msg = asyncio.run(_validate_ha("http://ha.local", "tok"))
        assert ok is True
        assert msg == ""

    def test_validate_ha_rejected_token(self):
        resp = MagicMock(status_code=401)
        with patch("httpx.AsyncClient", return_value=_mock_async_client(get=resp)):
            ok, msg = asyncio.run(_validate_ha("http://ha.local", "bad"))
        assert ok is False
        assert "rejected" in msg

    def test_validate_ha_other_status(self):
        resp = MagicMock(status_code=500)
        with patch("httpx.AsyncClient", return_value=_mock_async_client(get=resp)):
            ok, msg = asyncio.run(_validate_ha("http://ha.local", "tok"))
        assert ok is False
        assert "500" in msg

    def test_validate_ha_connection_error(self):
        with patch("httpx.AsyncClient", return_value=_mock_async_client(get=AsyncMock(side_effect=Exception("refused")))):
            ok, msg = asyncio.run(_validate_ha("http://ha.local", "tok"))
        assert ok is False
        assert "Could not reach" in msg

    def test_get_entity_state_found(self):
        resp = MagicMock(status_code=200)
        resp.json = MagicMock(return_value={"state": "on"})
        cfg = {"ha_url": "http://ha.local", "ha_token": "tok"}
        with patch("httpx.AsyncClient", return_value=_mock_async_client(get=resp)):
            state = asyncio.run(_ha_get_entity_state(cfg, "light.kitchen"))
        assert state == "on"

    def test_get_entity_state_not_found(self):
        resp = MagicMock(status_code=404)
        cfg = {"ha_url": "http://ha.local", "ha_token": "tok"}
        with patch("httpx.AsyncClient", return_value=_mock_async_client(get=resp)):
            state = asyncio.run(_ha_get_entity_state(cfg, "light.kitchen"))
        assert state is None

    def test_get_entity_state_exception_returns_none(self):
        cfg = {"ha_url": "http://ha.local", "ha_token": "tok"}
        with patch("httpx.AsyncClient", return_value=_mock_async_client(get=AsyncMock(side_effect=Exception("boom")))):
            state = asyncio.run(_ha_get_entity_state(cfg, "light.kitchen"))
        assert state is None

    def test_get_states_filters_by_domain_and_formats(self):
        resp = MagicMock(status_code=200)
        resp.raise_for_status = MagicMock()
        resp.json = MagicMock(
            return_value=[
                {"entity_id": "light.kitchen", "state": "on", "attributes": {"friendly_name": "Kitchen Light"}},
                {"entity_id": "switch.fan", "state": "off", "attributes": {}},
            ]
        )
        cfg = {"ha_url": "http://ha.local", "ha_token": "tok"}
        with patch("httpx.AsyncClient", return_value=_mock_async_client(get=resp)):
            result = asyncio.run(_ha_get_states(cfg, domain="light"))
        assert "light.kitchen: on (Kitchen Light)" in result
        assert "switch.fan" not in result

    def test_get_states_no_entities(self):
        resp = MagicMock(status_code=200)
        resp.raise_for_status = MagicMock()
        resp.json = MagicMock(return_value=[])
        cfg = {"ha_url": "http://ha.local", "ha_token": "tok"}
        with patch("httpx.AsyncClient", return_value=_mock_async_client(get=resp)):
            result = asyncio.run(_ha_get_states(cfg))
        assert result == "No entities found."

    def test_call_service_success(self):
        resp = MagicMock(status_code=200)
        cfg = {"ha_url": "http://ha.local", "ha_token": "tok"}
        with patch("httpx.AsyncClient", return_value=_mock_async_client(post=resp)):
            result = asyncio.run(_ha_call_service(cfg, "light", "turn_on", "light.kitchen", {"brightness_pct": 50}))
        assert result == "Done."

    def test_call_service_failure(self):
        resp = MagicMock(status_code=400, text="bad request")
        cfg = {"ha_url": "http://ha.local", "ha_token": "tok"}
        with patch("httpx.AsyncClient", return_value=_mock_async_client(post=resp)):
            result = asyncio.run(_ha_call_service(cfg, "light", "turn_on"))
        assert "400" in result


# ── integrations/multiroom/snapcast.py ───────────────────────────────────────


class TestSnapcastTool:
    def test_not_configured_no_tools(self):
        with patch("integrations.multiroom.snapcast.SNAPCAST_URL", ""):
            assert _get_snapcast_tools("anthropic") == []

    def test_configured_returns_tools(self):
        with patch("integrations.multiroom.snapcast.SNAPCAST_URL", "http://snap.local:1780"):
            tools = _get_snapcast_tools("anthropic")
        names = {t["name"] for t in tools}
        assert names == {"snapcast_status", "snapcast_set_volume", "snapcast_mute", "snapcast_set_stream"}

    def test_openai_format(self):
        with patch("integrations.multiroom.snapcast.SNAPCAST_URL", "http://snap.local:1780"):
            tools = _get_snapcast_tools("openai")
        assert all(t["type"] == "function" for t in tools)

    def _resp(self, result_json):
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json = MagicMock(return_value=result_json)
        return mock_resp

    def test_status_formats_groups_and_clients(self):
        server_status = {
            "result": {
                "server": {
                    "streams": [{"id": "stream1", "status": {"stream": {"meta": {"TITLE": "Radio"}}}}],
                    "groups": [
                        {
                            "id": "group1",
                            "stream_id": "stream1",
                            "muted": False,
                            "clients": [
                                {
                                    "id": "client1",
                                    "host": {"name": "Kitchen"},
                                    "config": {"volume": {"percent": 60, "muted": False}},
                                    "connected": True,
                                },
                            ],
                        }
                    ],
                }
            }
        }
        with patch("httpx.AsyncClient", return_value=_mock_async_client(post=self._resp(server_status))):
            result = asyncio.run(_execute_snapcast_tool("snapcast_status", {}))
        assert "Radio" in result
        assert "Kitchen" in result
        assert "vol=60%" in result

    def test_status_no_groups(self):
        with patch("httpx.AsyncClient", return_value=_mock_async_client(post=self._resp({"result": {"server": {}}}))):
            result = asyncio.run(_execute_snapcast_tool("snapcast_status", {}))
        assert "No Snapcast groups" in result

    def test_set_volume(self):
        with patch("httpx.AsyncClient", return_value=_mock_async_client(post=self._resp({"result": {}}))):
            result = asyncio.run(_execute_snapcast_tool("snapcast_set_volume", {"client_id": "client1", "volume": 75}))
        assert "75%" in result

    def test_mute_preserves_volume(self):
        status_result = {"result": {"server": {"groups": [{"clients": [{"id": "client1", "config": {"volume": {"percent": 42}}}]}]}}}
        with patch("httpx.AsyncClient", return_value=_mock_async_client(post=self._resp(status_result))):
            result = asyncio.run(_execute_snapcast_tool("snapcast_mute", {"client_id": "client1", "muted": True}))
        assert "Muted 'client1'" in result

    def test_set_stream(self):
        with patch("httpx.AsyncClient", return_value=_mock_async_client(post=self._resp({"result": {}}))):
            result = asyncio.run(_execute_snapcast_tool("snapcast_set_stream", {"group_id": "g1", "stream_id": "s2"}))
        assert "g1" in result and "s2" in result

    def test_unknown_tool(self):
        result = asyncio.run(_execute_snapcast_tool("snapcast_bogus", {}))
        assert "Unknown Snapcast tool" in result

    def test_rpc_error_wrapped(self):
        with patch("httpx.AsyncClient", return_value=_mock_async_client(post=self._resp({"error": {"message": "boom"}}))):
            result = asyncio.run(_execute_snapcast_tool("snapcast_status", {}))
        assert "Snapcast error: boom" in result

    def test_get_status_direct(self):
        with patch("httpx.AsyncClient", return_value=_mock_async_client(post=self._resp({"result": {"server": {}}}))):
            result = asyncio.run(_snapcast_get_status())
        assert "No Snapcast groups" in result


# ── integrations/music/apple_music.py ────────────────────────────────────────


class TestAppleMusicTool:
    def test_server_not_configured(self):
        with patch("integrations.music.apple_music.APPLE_MUSIC_TEAM_ID", ""):
            assert _apple_music_server_configured() is False

    def test_server_configured(self):
        with (
            patch("integrations.music.apple_music.APPLE_MUSIC_TEAM_ID", "team"),
            patch("integrations.music.apple_music.APPLE_MUSIC_KEY_ID", "key"),
            patch("integrations.music.apple_music.APPLE_MUSIC_PRIVATE_KEY", "pk"),
        ):
            assert _apple_music_server_configured() is True

    def test_user_configured_requires_token(self):
        with (
            patch("integrations.music.apple_music.APPLE_MUSIC_TEAM_ID", "team"),
            patch("integrations.music.apple_music.APPLE_MUSIC_KEY_ID", "key"),
            patch("integrations.music.apple_music.APPLE_MUSIC_PRIVATE_KEY", "pk"),
        ):
            assert _apple_music_configured({}) is False
            assert _apple_music_configured({"apple_music_user_token": "tok"}) is True

    def test_get_tools_gated(self):
        with patch("integrations.music.apple_music.APPLE_MUSIC_TEAM_ID", ""):
            assert _get_apple_music_tools({}, "anthropic") == []

    def test_get_tools_returns_when_configured(self):
        with (
            patch("integrations.music.apple_music.APPLE_MUSIC_TEAM_ID", "team"),
            patch("integrations.music.apple_music.APPLE_MUSIC_KEY_ID", "key"),
            patch("integrations.music.apple_music.APPLE_MUSIC_PRIVATE_KEY", "pk"),
        ):
            tools = _get_apple_music_tools({"apple_music_user_token": "tok"}, "openai")
        assert all(t["type"] == "function" for t in tools)

    def test_require_runtime_raises_before_init(self):
        with patch.object(apple_music_mod, "_sio", None), patch.object(apple_music_mod, "_sid_to_user", None):
            try:
                _require_runtime()
                raise AssertionError("expected RuntimeError")
            except RuntimeError:
                pass

    def _init_am(self, user_id="u1", sid="sid1"):
        sio = MagicMock()
        sio.emit = AsyncMock()
        apple_music_mod.init(sio, {sid: user_id})
        return sio

    def test_no_active_session(self):
        apple_music_mod.init(MagicMock(), {})
        result = asyncio.run(_execute_apple_music_tool("apple_music_play", {}, "u1"))
        assert "No active Apple Music session" in result

    def test_simple_actions_emit_and_return_message(self):
        sio = self._init_am()
        result = asyncio.run(_execute_apple_music_tool("apple_music_pause", {}, "u1"))
        assert "paused" in result.lower()
        sio.emit.assert_awaited_once_with("apple_music_cmd", {"action": "pause"}, to="sid1")

    def test_volume_clamped(self):
        sio = self._init_am()
        result = asyncio.run(_execute_apple_music_tool("apple_music_volume", {"volume_percent": 150}, "u1"))
        assert "100%" in result
        sio.emit.assert_awaited_once_with("apple_music_cmd", {"action": "volume", "value": 1.0}, to="sid1")

    def test_unknown_tool(self):
        self._init_am()
        result = asyncio.run(_execute_apple_music_tool("apple_music_bogus", {}, "u1"))
        assert "Unknown Apple Music tool" in result

    def test_now_playing_resolves_via_callback(self):
        sio = self._init_am()

        async def fake_emit(event, data, to):
            _resolve_apple_music_callback({"cb": data["cb"], "result": "Song XYZ"})

        sio.emit = fake_emit
        result = asyncio.run(_execute_apple_music_tool("apple_music_now_playing", {}, "u1"))
        assert result == "Song XYZ"
        assert _am_callbacks == {}

    def test_search_and_play(self):
        sio = self._init_am()

        async def fake_emit(event, data, to):
            _resolve_apple_music_callback({"cb": data["cb"], "result": "Playing Track"})

        sio.emit = fake_emit
        result = asyncio.run(_execute_apple_music_tool("apple_music_search_and_play", {"query": "Yesterday", "type": "track"}, "u1"))
        assert result == "Playing Track"

    def test_resolve_callback_noop_when_missing(self):
        _resolve_apple_music_callback({"cb": "does-not-exist", "result": "x"})

    def test_start_party_emits_to_first_session(self):
        sio = self._init_am()
        asyncio.run(apple_music_mod._apple_music_start_party("u1"))
        sio.emit.assert_awaited_once_with("apple_music_cmd", {"action": "party"}, to="sid1")

    def test_request_callback_times_out(self):
        self._init_am()
        with patch("integrations.music.apple_music.asyncio.wait_for", new=AsyncMock(side_effect=TimeoutError)):
            result = asyncio.run(apple_music_mod._am_request_callback("sid1", "now_playing"))
        assert result == "Request timed out."


class TestAppleMusicDevToken:
    def test_raises_when_jwt_not_installed(self):
        with patch.object(apple_music_mod, "jwt", None):
            try:
                apple_music_mod._apple_music_dev_token()
                raise AssertionError("expected RuntimeError")
            except RuntimeError as e:
                assert "PyJWT" in str(e)

    def test_encodes_token_with_es256(self):
        fake_jwt = MagicMock()
        fake_jwt.encode = MagicMock(return_value="fake.jwt.token")
        with (
            patch.object(apple_music_mod, "jwt", fake_jwt),
            patch("integrations.music.apple_music.APPLE_MUSIC_TEAM_ID", "team"),
            patch("integrations.music.apple_music.APPLE_MUSIC_KEY_ID", "key"),
            patch("integrations.music.apple_music.APPLE_MUSIC_PRIVATE_KEY", "pk"),
        ):
            token = apple_music_mod._apple_music_dev_token()
        assert token == "fake.jwt.token"
        assert fake_jwt.encode.call_args.kwargs["algorithm"] == "ES256"


class TestAppleMusicUserToken:
    def test_save_user_token(self):
        pool, conn = _mock_asyncpg_pool()
        config = {}
        state = {"config": config}

        async def get_user_state(uid):
            return state

        lock_cm = _async_cm()

        def get_user_lock(uid):
            return lock_cm

        with patch("integrations.music.apple_music._pool", return_value=pool):
            asyncio.run(apple_music_mod._save_apple_music_user_token("u1", "tok", "us", get_user_state, get_user_lock))
        assert config["apple_music_user_token"] == "tok"
        assert config["apple_music_storefront"] == "us"
        conn.execute.assert_awaited_once()

    def test_disconnect_user_token(self):
        pool, conn = _mock_asyncpg_pool()
        config = {"apple_music_user_token": "tok"}
        state = {"config": config}

        async def get_user_state(uid):
            return state

        lock_cm = _async_cm()

        def get_user_lock(uid):
            return lock_cm

        with patch("integrations.music.apple_music._pool", return_value=pool):
            asyncio.run(apple_music_mod._disconnect_apple_music_user_token("u1", get_user_state, get_user_lock))
        assert config["apple_music_user_token"] == ""
        conn.execute.assert_awaited_once()


# ── integrations/pim/dav.py ───────────────────────────────────────────────────


class TestDavHelpers:
    _MULTISTATUS_XML = """<?xml version="1.0" encoding="utf-8"?>
<D:multistatus xmlns:D="DAV:" xmlns:C="urn:ietf:params:xml:ns:caldav">
  <D:response>
    <D:href>/dav/calendars/user/personal/</D:href>
    <D:propstat>
      <D:prop>
        <D:resourcetype><D:collection/><C:calendar/></D:resourcetype>
        <D:displayname>Personal</D:displayname>
      </D:prop>
      <D:status>HTTP/1.1 200 OK</D:status>
    </D:propstat>
  </D:response>
  <D:response>
    <D:href>/dav/calendars/user/inbox/</D:href>
    <D:propstat>
      <D:prop>
        <D:resourcetype><D:collection/></D:resourcetype>
      </D:prop>
      <D:status>HTTP/1.1 200 OK</D:status>
    </D:propstat>
  </D:response>
</D:multistatus>"""

    _PRINCIPAL_XML = """<?xml version="1.0" encoding="utf-8"?>
<D:multistatus xmlns:D="DAV:">
  <D:response>
    <D:href>/dav/</D:href>
    <D:propstat>
      <D:prop>
        <D:current-user-principal><D:href>/dav/principals/user/</D:href></D:current-user-principal>
      </D:prop>
      <D:status>HTTP/1.1 200 OK</D:status>
    </D:propstat>
  </D:response>
</D:multistatus>"""

    def test_ensure_trailing_slash_adds_slash(self):
        assert _ensure_trailing_slash("https://example.com/dav") == "https://example.com/dav/"

    def test_ensure_trailing_slash_noop_when_present(self):
        assert _ensure_trailing_slash("https://example.com/dav/") == "https://example.com/dav/"

    def test_dav_join_relative(self):
        assert _dav_join("https://example.com/dav", "calendars/personal/") == "https://example.com/dav/calendars/personal/"

    def test_dav_join_absolute_path_replaces_base_path(self):
        assert _dav_join("https://example.com/dav/", "/other/path/") == "https://example.com/other/path/"

    def test_propfind_body_contains_requested_props(self):
        body = _dav_propfind_body([("DAV:", "resourcetype"), ("DAV:", "displayname")])
        assert body.startswith(b"<?xml")
        assert b"resourcetype" in body
        assert b"displayname" in body

    def test_raise_for_status_ok_codes_noop(self):
        for code in (200, 201, 204, 207):
            _dav_raise_for_status(MagicMock(status_code=code), "test")

    def test_raise_for_status_auth_failure(self):
        try:
            _dav_raise_for_status(MagicMock(status_code=401, text=""), "DAV discovery")
            raise AssertionError("expected ValueError")
        except ValueError as e:
            assert "authentication failed" in str(e)

    def test_raise_for_status_other_error_includes_detail(self):
        try:
            _dav_raise_for_status(MagicMock(status_code=500, text="Internal Server Error"), "DAV discovery")
            raise AssertionError("expected ValueError")
        except ValueError as e:
            assert "500" in str(e) and "Internal Server Error" in str(e)

    def test_raise_for_status_no_detail(self):
        try:
            _dav_raise_for_status(MagicMock(status_code=500, text=""), "DAV discovery")
            raise AssertionError("expected ValueError")
        except ValueError as e:
            assert str(e) == "DAV discovery: server returned 500."

    def test_multistatus_responses_parses(self):
        responses = _dav_multistatus_responses(self._MULTISTATUS_XML)
        assert len(responses) == 2

    def test_multistatus_responses_malformed_raises(self):
        try:
            _dav_multistatus_responses("<not><valid>xml")
            raise AssertionError("expected ValueError")
        except ValueError as e:
            assert "malformed XML" in str(e)

    def test_href_and_resource_types_and_display_name(self):
        responses = _dav_multistatus_responses(self._MULTISTATUS_XML)
        first, second = responses
        assert _dav_href(first) == "/dav/calendars/user/personal/"
        assert _dav_resource_types(first) == {"collection", "calendar"}
        assert _dav_display_name(first) == "Personal"
        assert _dav_resource_types(second) == {"collection"}
        assert _dav_display_name(second) == ""

    def test_response_for_url_matches_by_path(self):
        responses = _dav_multistatus_responses(self._MULTISTATUS_XML)
        match = _dav_response_for_url(responses, "https://example.com/dav/calendars/user/inbox/")
        assert _dav_href(match) == "/dav/calendars/user/inbox/"

    def test_response_for_url_falls_back_to_first(self):
        responses = _dav_multistatus_responses(self._MULTISTATUS_XML)
        match = _dav_response_for_url(responses, "https://example.com/nonexistent/")
        assert match is responses[0]

    def test_response_prop_selects_200_status(self):
        responses = _dav_multistatus_responses(self._MULTISTATUS_XML)
        assert _dav_response_prop(responses[0]) is not None

    def test_prop_href_extracts_nested_href(self):
        responses = _dav_multistatus_responses(self._PRINCIPAL_XML)
        href = _dav_prop_href(responses[0], "D:current-user-principal")
        assert href == "/dav/principals/user/"

    def test_prop_href_missing_returns_none(self):
        responses = _dav_multistatus_responses(self._MULTISTATUS_XML)
        assert _dav_prop_href(responses[1], "D:current-user-principal") is None


class TestResolveDavCollection:
    def _resp(self, xml):
        return MagicMock(status_code=200, text=xml)

    def test_missing_credentials_raises(self):
        try:
            asyncio.run(_resolve_dav_collection("", "user", "pw", "calendar"))
            raise AssertionError("expected ValueError")
        except ValueError as e:
            assert "required" in str(e)

    def test_direct_url_is_already_the_collection(self):
        xml = """<?xml version="1.0" encoding="utf-8"?>
<D:multistatus xmlns:D="DAV:" xmlns:C="urn:ietf:params:xml:ns:caldav">
  <D:response>
    <D:href>/cal/personal/</D:href>
    <D:propstat>
      <D:prop>
        <D:resourcetype><D:collection/><C:calendar/></D:resourcetype>
        <D:displayname>Personal</D:displayname>
      </D:prop>
      <D:status>HTTP/1.1 200 OK</D:status>
    </D:propstat>
  </D:response>
</D:multistatus>"""
        mock_req = AsyncMock(return_value=self._resp(xml))
        with patch("integrations.pim.dav._dav_request", new=mock_req):
            result = asyncio.run(_resolve_dav_collection("https://dav.example.com/cal/personal/", "user", "pw", "calendar"))
        assert result == {"url": "https://dav.example.com/cal/personal/", "display_name": "Personal"}
        mock_req.assert_awaited_once()

    def _principal_xml(self, with_principal=True):
        principal_block = "<D:current-user-principal><D:href>/principals/users/me/</D:href></D:current-user-principal>" if with_principal else ""
        return f"""<?xml version="1.0" encoding="utf-8"?>
<D:multistatus xmlns:D="DAV:">
  <D:response>
    <D:href>/</D:href>
    <D:propstat>
      <D:prop>
        <D:resourcetype><D:collection/></D:resourcetype>
        {principal_block}
      </D:prop>
      <D:status>HTTP/1.1 200 OK</D:status>
    </D:propstat>
  </D:response>
</D:multistatus>"""

    def _home_set_xml(self, with_home=True):
        home_block = "<C:calendar-home-set><D:href>/cal/</D:href></C:calendar-home-set>" if with_home else ""
        return f"""<?xml version="1.0" encoding="utf-8"?>
<D:multistatus xmlns:D="DAV:" xmlns:C="urn:ietf:params:xml:ns:caldav">
  <D:response>
    <D:href>/principals/users/me/</D:href>
    <D:propstat>
      <D:prop>
        {home_block}
      </D:prop>
      <D:status>HTTP/1.1 200 OK</D:status>
    </D:propstat>
  </D:response>
</D:multistatus>"""

    _COLLECTIONS_XML = """<?xml version="1.0" encoding="utf-8"?>
<D:multistatus xmlns:D="DAV:" xmlns:C="urn:ietf:params:xml:ns:caldav">
  <D:response>
    <D:href>/cal/inbox/</D:href>
    <D:propstat><D:prop><D:resourcetype><D:collection/></D:resourcetype></D:prop><D:status>HTTP/1.1 200 OK</D:status></D:propstat>
  </D:response>
  <D:response>
    <D:href>/cal/personal/</D:href>
    <D:propstat><D:prop><D:resourcetype><D:collection/><C:calendar/></D:resourcetype><D:displayname>Personal</D:displayname></D:prop><D:status>HTTP/1.1 200 OK</D:status></D:propstat>
  </D:response>
</D:multistatus>"""

    _COLLECTIONS_XML_NO_MATCH = """<?xml version="1.0" encoding="utf-8"?>
<D:multistatus xmlns:D="DAV:">
  <D:response>
    <D:href>/cal/inbox/</D:href>
    <D:propstat><D:prop><D:resourcetype><D:collection/></D:resourcetype></D:prop><D:status>HTTP/1.1 200 OK</D:status></D:propstat>
  </D:response>
</D:multistatus>"""

    def test_full_discovery_chain(self):
        responses = [self._resp(self._principal_xml()), self._resp(self._home_set_xml()), self._resp(self._COLLECTIONS_XML)]
        with patch("integrations.pim.dav._dav_request", new=AsyncMock(side_effect=responses)):
            result = asyncio.run(_resolve_dav_collection("https://dav.example.com/", "user", "pw", "calendar"))
        assert result == {"url": "https://dav.example.com/cal/personal/", "display_name": "Personal"}

    def test_missing_principal_href_raises(self):
        responses = [self._resp(self._principal_xml(with_principal=False))]
        with patch("integrations.pim.dav._dav_request", new=AsyncMock(side_effect=responses)):
            try:
                asyncio.run(_resolve_dav_collection("https://dav.example.com/", "user", "pw", "calendar"))
                raise AssertionError("expected ValueError")
            except ValueError as e:
                assert "principal" in str(e)

    def test_missing_home_href_raises(self):
        responses = [self._resp(self._principal_xml()), self._resp(self._home_set_xml(with_home=False))]
        with patch("integrations.pim.dav._dav_request", new=AsyncMock(side_effect=responses)):
            try:
                asyncio.run(_resolve_dav_collection("https://dav.example.com/", "user", "pw", "calendar"))
                raise AssertionError("expected ValueError")
            except ValueError as e:
                assert "calendar home" in str(e)

    def test_no_matching_collection_raises(self):
        responses = [self._resp(self._principal_xml()), self._resp(self._home_set_xml()), self._resp(self._COLLECTIONS_XML_NO_MATCH)]
        with patch("integrations.pim.dav._dav_request", new=AsyncMock(side_effect=responses)):
            try:
                asyncio.run(_resolve_dav_collection("https://dav.example.com/", "user", "pw", "calendar"))
                raise AssertionError("expected ValueError")
            except ValueError as e:
                assert "No calendar collection" in str(e)


# ── integrations/tesla.py ─────────────────────────────────────────────────────


class TestTeslaBaseUrl:
    def test_unofficial(self):
        assert _tesla_base_url("unofficial") == "https://owner-api.teslamotors.com"

    def test_fleet(self):
        assert "fleet-api" in _tesla_base_url("fleet")


class TestExecuteTeslaTool:
    _cfg = {"tesla_method": "unofficial", "tesla_refresh_token": "rt"}

    def test_pick_vehicle_error_surfaced(self):
        with patch("integrations.tesla._tesla_pick_vehicle", new=AsyncMock(side_effect=ValueError("No Tesla vehicle found in your account."))):
            result = asyncio.run(_execute_tesla_tool(self._cfg, "get_vehicle_status", {}, "u1"))
        assert "Tesla error: No Tesla vehicle found" in result

    def test_status_asleep(self):
        vehicle = {"id": 1, "display_name": "Model 3", "state": "asleep"}
        with (
            patch("integrations.tesla._tesla_pick_vehicle", new=AsyncMock(return_value=("unofficial", vehicle))),
            patch("integrations.tesla._tesla_access_token", new=AsyncMock(return_value="tok")),
        ):
            result = asyncio.run(_execute_tesla_tool(self._cfg, "get_vehicle_status", {}, "u1"))
        assert "asleep" in result

    def test_status_online(self):
        vehicle = {"id": 1, "display_name": "Model 3", "state": "online"}
        vehicle_data = {
            "response": {
                "charge_state": {"battery_level": 80, "est_battery_range": 250, "charging_state": "Disconnected"},
                "climate_state": {"inside_temp": 22, "is_climate_on": True, "outside_temp": 10},
                "vehicle_state": {"locked": True, "odometer": 12345},
            }
        }
        resp = MagicMock(status_code=200)
        resp.raise_for_status = MagicMock()
        resp.json = MagicMock(return_value=vehicle_data)
        with (
            patch("integrations.tesla._tesla_pick_vehicle", new=AsyncMock(return_value=("unofficial", vehicle))),
            patch("integrations.tesla._tesla_access_token", new=AsyncMock(return_value="tok")),
            patch("httpx.AsyncClient", return_value=_mock_async_client(get=resp)),
        ):
            result = asyncio.run(_execute_tesla_tool(self._cfg, "get_vehicle_status", {}, "u1"))
        assert "Battery: 80%" in result
        assert "Locked" in result
        assert "72°F inside" in result

    def test_lock_vehicle(self):
        vehicle = {"id": 1, "display_name": "Model 3", "state": "online"}
        with (
            patch("integrations.tesla._tesla_pick_vehicle", new=AsyncMock(return_value=("unofficial", vehicle))),
            patch("integrations.tesla._tesla_access_token", new=AsyncMock(return_value="tok")),
            patch("integrations.tesla._tesla_cmd", new=AsyncMock(return_value={"result": True})),
        ):
            result = asyncio.run(_execute_tesla_tool(self._cfg, "lock_vehicle", {}, "u1"))
        assert result == "Doors locked on Model 3."

    def test_command_failure_message(self):
        vehicle = {"id": 1, "display_name": "Model 3", "state": "online"}
        with (
            patch("integrations.tesla._tesla_pick_vehicle", new=AsyncMock(return_value=("unofficial", vehicle))),
            patch("integrations.tesla._tesla_access_token", new=AsyncMock(return_value="tok")),
            patch("integrations.tesla._tesla_cmd", new=AsyncMock(return_value={"result": False, "reason": "vehicle_unavailable"})),
        ):
            result = asyncio.run(_execute_tesla_tool(self._cfg, "unlock_vehicle", {}, "u1"))
        assert "Command failed: vehicle_unavailable" in result

    def test_set_climate_start_with_temperature(self):
        vehicle = {"id": 1, "display_name": "Model 3", "state": "online"}
        mock_cmd = AsyncMock(return_value={"result": True})
        with (
            patch("integrations.tesla._tesla_pick_vehicle", new=AsyncMock(return_value=("unofficial", vehicle))),
            patch("integrations.tesla._tesla_access_token", new=AsyncMock(return_value="tok")),
            patch("integrations.tesla._tesla_cmd", new=mock_cmd),
        ):
            result = asyncio.run(_execute_tesla_tool(self._cfg, "set_climate", {"action": "start", "temperature_f": 72}, "u1"))
        assert "Climate started" in result
        assert mock_cmd.await_count == 2

    def test_actuate_trunk_frunk(self):
        vehicle = {"id": 1, "display_name": "Model 3", "state": "online"}
        with (
            patch("integrations.tesla._tesla_pick_vehicle", new=AsyncMock(return_value=("unofficial", vehicle))),
            patch("integrations.tesla._tesla_access_token", new=AsyncMock(return_value="tok")),
            patch("integrations.tesla._tesla_cmd", new=AsyncMock(return_value={"result": True})),
        ):
            result = asyncio.run(_execute_tesla_tool(self._cfg, "actuate_trunk", {"which": "front"}, "u1"))
        assert result == "Frunk opened on Model 3."

    def test_unknown_tool(self):
        vehicle = {"id": 1, "display_name": "Model 3", "state": "online"}
        with (
            patch("integrations.tesla._tesla_pick_vehicle", new=AsyncMock(return_value=("unofficial", vehicle))),
            patch("integrations.tesla._tesla_access_token", new=AsyncMock(return_value="tok")),
        ):
            result = asyncio.run(_execute_tesla_tool(self._cfg, "bogus_tool", {}, "u1"))
        assert "Unknown Tesla tool" in result

    def test_fleet_lock_vehicle(self):
        vehicle = {"vin": "5YJ123", "display_name": "Model Y", "state": "online"}
        with (
            patch("integrations.tesla._tesla_pick_vehicle", new=AsyncMock(return_value=("fleet", vehicle))),
            patch("integrations.tesla._tesla_access_token", new=AsyncMock(return_value="tok")),
            patch("integrations.tesla._tesla_cmd", new=AsyncMock(return_value={"result": True})),
        ):
            result = asyncio.run(_execute_tesla_tool({"tesla_method": "fleet", "tesla_fleet_refresh_token": "ft"}, "lock_vehicle", {}, "u1"))
        assert result == "Doors locked on Model Y."

    def test_set_climate_stop_unofficial(self):
        vehicle = {"id": 1, "display_name": "Model 3", "state": "online"}
        with (
            patch("integrations.tesla._tesla_pick_vehicle", new=AsyncMock(return_value=("unofficial", vehicle))),
            patch("integrations.tesla._tesla_access_token", new=AsyncMock(return_value="tok")),
            patch("integrations.tesla._tesla_cmd", new=AsyncMock(return_value={"result": True})),
        ):
            result = asyncio.run(_execute_tesla_tool(self._cfg, "set_climate", {"action": "stop"}, "u1"))
        assert result == "Climate stopped on Model 3."

    def test_fleet_get_vehicle_status_online(self):
        vehicle = {"vin": "5YJ123", "display_name": "Model Y", "state": "online"}
        vehicle_data = {
            "response": {
                "charge_state": {"battery_level": 70, "est_battery_range": 200, "charging_state": "Charging"},
                "climate_state": {"inside_temp": 20, "is_climate_on": True},
                "vehicle_state": {"locked": False},
            }
        }
        resp = MagicMock(status_code=200)
        resp.raise_for_status = MagicMock()
        resp.json = MagicMock(return_value=vehicle_data)
        with (
            patch("integrations.tesla._tesla_pick_vehicle", new=AsyncMock(return_value=("fleet", vehicle))),
            patch("integrations.tesla._tesla_access_token", new=AsyncMock(return_value="tok")),
            patch("httpx.AsyncClient", return_value=_mock_async_client(get=resp)),
        ):
            result = asyncio.run(_execute_tesla_tool({"tesla_method": "fleet", "tesla_fleet_refresh_token": "ft"}, "get_vehicle_status", {}, "u1"))
        assert "Battery: 70%" in result
        assert "Unlocked" in result
        assert "68°F inside" in result

    def test_fleet_get_vehicle_status_asleep(self):
        vehicle = {"vin": "5YJ123", "display_name": "Model Y", "state": "asleep"}
        with (
            patch("integrations.tesla._tesla_pick_vehicle", new=AsyncMock(return_value=("fleet", vehicle))),
            patch("integrations.tesla._tesla_access_token", new=AsyncMock(return_value="tok")),
        ):
            result = asyncio.run(_execute_tesla_tool({"tesla_method": "fleet", "tesla_fleet_refresh_token": "ft"}, "get_vehicle_status", {}, "u1"))
        assert "asleep" in result

    def test_fleet_set_climate_with_temperature(self):
        vehicle = {"vin": "5YJ123", "display_name": "Model Y", "state": "online"}
        mock_cmd = AsyncMock(return_value={"result": True})
        with (
            patch("integrations.tesla._tesla_pick_vehicle", new=AsyncMock(return_value=("fleet", vehicle))),
            patch("integrations.tesla._tesla_access_token", new=AsyncMock(return_value="tok")),
            patch("integrations.tesla._tesla_cmd", new=mock_cmd),
        ):
            result = asyncio.run(_execute_tesla_tool({"tesla_method": "fleet", "tesla_fleet_refresh_token": "ft"}, "set_climate", {"action": "start", "temperature_f": 70}, "u1"))
        assert result == "Climate started on Model Y."
        assert mock_cmd.await_count == 2

    def test_fleet_actuate_trunk(self):
        vehicle = {"vin": "5YJ123", "display_name": "Model Y", "state": "online"}
        with (
            patch("integrations.tesla._tesla_pick_vehicle", new=AsyncMock(return_value=("fleet", vehicle))),
            patch("integrations.tesla._tesla_access_token", new=AsyncMock(return_value="tok")),
            patch("integrations.tesla._tesla_cmd", new=AsyncMock(return_value={"result": True})),
        ):
            result = asyncio.run(_execute_tesla_tool({"tesla_method": "fleet", "tesla_fleet_refresh_token": "ft"}, "actuate_trunk", {"which": "rear"}, "u1"))
        assert result == "Rear trunk command sent to Model Y."


class TestTeslaLowLevel:
    def test_access_token_uses_cache_when_valid(self):
        future_expiry = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=1)
        with patch.object(tesla_mod, "_tesla_tokens", {"u1": {"unofficial_access": "cached-tok", "unofficial_expiry": future_expiry}}):
            token = asyncio.run(tesla_mod._tesla_access_token("unofficial", "u1", {}))
        assert token == "cached-tok"

    def test_access_token_refreshes_unofficial(self):
        pool, conn = _mock_asyncpg_pool()
        cfg = {"tesla_refresh_token": "old-rt"}
        with (
            patch.object(tesla_mod, "_tesla_tokens", {}),
            patch("integrations.tesla._pool", return_value=pool),
            patch(
                "integrations.tesla.refresh_oauth_token",
                new=AsyncMock(return_value={"access_token": "new-tok", "refresh_token": "new-rt", "expires_in": 28800}),
            ),
        ):
            token = asyncio.run(tesla_mod._tesla_access_token("unofficial", "u1", cfg))
        assert token == "new-tok"
        assert cfg["tesla_refresh_token"] == "new-rt"
        conn.execute.assert_awaited_once()

    def test_access_token_refreshes_fleet(self):
        pool, conn = _mock_asyncpg_pool()
        cfg = {"tesla_fleet_refresh_token": "old-rt"}
        with (
            patch.object(tesla_mod, "_tesla_tokens", {}),
            patch("integrations.tesla._pool", return_value=pool),
            patch(
                "integrations.tesla.refresh_oauth_token",
                new=AsyncMock(return_value={"access_token": "new-tok", "refresh_token": "new-rt", "expires_in": 28800}),
            ),
        ):
            token = asyncio.run(tesla_mod._tesla_access_token("fleet", "u1", cfg))
        assert token == "new-tok"
        assert cfg["tesla_fleet_refresh_token"] == "new-rt"

    def test_vehicles_fetches_from_api(self):
        resp = MagicMock(status_code=200)
        resp.raise_for_status = MagicMock()
        resp.json = MagicMock(return_value={"response": [{"id": 1, "display_name": "Model 3"}]})
        with (
            patch("integrations.tesla._tesla_access_token", new=AsyncMock(return_value="tok")),
            patch("httpx.AsyncClient", return_value=_mock_async_client(get=resp)),
        ):
            vehicles = asyncio.run(tesla_mod._tesla_vehicles("unofficial", "u1", {}))
        assert vehicles == [{"id": 1, "display_name": "Model 3"}]

    def test_wake_returns_true_when_online(self):
        resp = MagicMock(status_code=200)
        resp.json = MagicMock(return_value={"response": {"state": "online"}})
        with patch("httpx.AsyncClient", return_value=_mock_async_client(post=resp)):
            result = asyncio.run(tesla_mod._tesla_wake("unofficial", 1, "tok"))
        assert result is True

    def test_wake_returns_false_after_retries_exhausted(self):
        resp = MagicMock(status_code=200)
        resp.json = MagicMock(return_value={"response": {"state": "asleep"}})
        with (
            patch("httpx.AsyncClient", return_value=_mock_async_client(post=resp)),
            patch("integrations.tesla.asyncio.sleep", new=AsyncMock()),
        ):
            result = asyncio.run(tesla_mod._tesla_wake("unofficial", 1, "tok"))
        assert result is False

    def test_cmd_wakes_and_sends_command(self):
        resp = MagicMock(status_code=200)
        resp.raise_for_status = MagicMock()
        resp.json = MagicMock(return_value={"response": {"result": True}})
        with (
            patch("integrations.tesla._tesla_access_token", new=AsyncMock(return_value="tok")),
            patch("integrations.tesla._tesla_wake", new=AsyncMock(return_value=True)),
            patch("httpx.AsyncClient", return_value=_mock_async_client(post=resp)),
        ):
            result = asyncio.run(tesla_mod._tesla_cmd("unofficial", "u1", {}, 1, "door_lock"))
        assert result == {"result": True}


class TestTeslaPickVehicle:
    def test_prefers_unofficial_when_available(self):
        vehicles = [{"id": 1, "display_name": "Model 3"}]
        with patch("integrations.tesla._tesla_vehicles", new=AsyncMock(return_value=vehicles)):
            method, vehicle = asyncio.run(_tesla_pick_vehicle("u1", {"tesla_method": "unofficial"}))
        assert method == "unofficial"
        assert vehicle["display_name"] == "Model 3"

    def test_falls_back_to_fleet_when_both_and_unofficial_fails(self):
        vehicles_fleet = [{"vin": "v1", "display_name": "Model Y"}]

        async def fake_vehicles(method, user_id, config):
            if method == "unofficial":
                raise Exception("unofficial down")
            return vehicles_fleet

        with patch("integrations.tesla._tesla_vehicles", new=fake_vehicles):
            method, vehicle = asyncio.run(_tesla_pick_vehicle("u1", {"tesla_method": "both"}))
        assert method == "fleet"
        assert vehicle["vin"] == "v1"

    def test_unofficial_error_propagates_when_method_is_unofficial(self):
        with patch("integrations.tesla._tesla_vehicles", new=AsyncMock(side_effect=Exception("unofficial down"))):
            try:
                asyncio.run(_tesla_pick_vehicle("u1", {"tesla_method": "unofficial"}))
                raise AssertionError("expected Exception")
            except Exception as e:
                assert str(e) == "unofficial down"

    def test_no_vehicles_raises(self):
        with patch("integrations.tesla._tesla_vehicles", new=AsyncMock(return_value=[])):
            try:
                asyncio.run(_tesla_pick_vehicle("u1", {"tesla_method": "fleet"}))
                raise AssertionError("expected ValueError")
            except ValueError as e:
                assert "No Tesla vehicle found" in str(e)

    def test_matches_by_name_hint(self):
        vehicles = [{"id": 1, "display_name": "Model 3"}, {"id": 2, "display_name": "Model Y"}]
        with patch("integrations.tesla._tesla_vehicles", new=AsyncMock(return_value=vehicles)):
            method, vehicle = asyncio.run(_tesla_pick_vehicle("u1", {"tesla_method": "unofficial"}, name_hint="Y"))
        assert vehicle["display_name"] == "Model Y"


# ── integrations/multiroom/presence.py ────────────────────────────────────────


class TestPresenceRegistry:
    def test_register_device_room(self):
        presence_mod.register_device_room("dev1", "kitchen")
        assert presence_mod._device_room["dev1"] == "kitchen"

    def test_register_device_room_empty_room_noop(self):
        presence_mod._device_room.pop("dev2", None)
        presence_mod.register_device_room("dev2", "")
        assert "dev2" not in presence_mod._device_room

    def test_update_user_room_uses_explicit_room(self):
        presence_mod.update_user_room("u1", "dev1", "bedroom")
        assert presence_mod.get_user_room("u1") == "bedroom"

    def test_update_user_room_falls_back_to_device_room(self):
        presence_mod.register_device_room("dev3", "office")
        presence_mod.update_user_room("u2", "dev3", "")
        assert presence_mod.get_user_room("u2") == "office"

    def test_update_user_room_noop_when_no_room_found(self):
        presence_mod._user_last_room.pop("u3", None)
        presence_mod.update_user_room("u3", "unknown-device", "")
        assert presence_mod.get_user_room("u3") == ""

    def test_register_and_deregister_sid_room(self):
        presence_mod.register_sid_room("sid1", "kitchen")
        assert presence_mod._sid_room["sid1"] == "kitchen"
        presence_mod.register_sid_room("sid1", "")
        assert "sid1" not in presence_mod._sid_room

    def test_deregister_sid(self):
        presence_mod.register_sid_room("sid2", "office")
        presence_mod.deregister_sid("sid2")
        assert "sid2" not in presence_mod._sid_room

    def test_get_user_room_default_empty(self):
        assert presence_mod.get_user_room("never-seen-user") == ""

    def test_get_sids_for_user_in_room_scopes_by_room(self):
        presence_mod.update_user_room("u4", "devX", "kitchen")
        presence_mod.register_sid_room("sidA", "kitchen")
        presence_mod.register_sid_room("sidB", "bedroom")
        result = presence_mod.get_sids_for_user_in_room("u4", lambda uid: ["sidA", "sidB"])
        assert result == ["sidA"]

    def test_get_sids_for_user_in_room_falls_back_to_all_when_no_room_match(self):
        presence_mod.update_user_room("u5", "devY", "garage")
        result = presence_mod.get_sids_for_user_in_room("u5", lambda uid: ["sidC", "sidD"])
        assert result == ["sidC", "sidD"]

    def test_get_sids_for_user_in_room_returns_all_when_no_known_room(self):
        presence_mod._user_last_room.pop("brand-new-user", None)
        result = presence_mod.get_sids_for_user_in_room("brand-new-user", lambda uid: ["sidE"])
        assert result == ["sidE"]


# ── integrations/music/spotify.py ─────────────────────────────────────────────


class TestSpotifyAccessToken:
    def test_raises_when_not_connected(self):
        with patch.object(spotify_mod, "_spotify_tokens", {}):
            try:
                asyncio.run(spotify_mod._spotify_access_token("u1", {}))
                raise AssertionError("expected ValueError")
            except ValueError as e:
                assert "not connected" in str(e).lower()

    def test_uses_cached_token_when_valid(self):
        future_expiry = datetime.datetime.now().timestamp() + 3600
        with patch.object(spotify_mod, "_spotify_tokens", {"u1": {"access": "cached-tok", "expiry": future_expiry}}):
            token = asyncio.run(spotify_mod._spotify_access_token("u1", {"spotify_refresh_token": "rt"}))
        assert token == "cached-tok"

    def test_refreshes_when_expired(self):
        pool, conn = _mock_asyncpg_pool()
        cfg = {"spotify_refresh_token": "old-rt"}
        with (
            patch.object(spotify_mod, "_spotify_tokens", {}),
            patch("integrations.music.spotify._pool", return_value=pool),
            patch(
                "integrations.music.spotify.refresh_oauth_token",
                new=AsyncMock(return_value={"access_token": "new-tok", "refresh_token": "new-rt", "expires_in": 3600}),
            ),
        ):
            token = asyncio.run(spotify_mod._spotify_access_token("u1", cfg))
        assert token == "new-tok"
        assert cfg["spotify_refresh_token"] == "new-rt"
        conn.execute.assert_awaited_once()


class TestSpotifyReq:
    def test_calls_correct_endpoint_with_token(self):
        resp = MagicMock(status_code=200)
        mock_client = _mock_async_client(get=resp)
        future_expiry = datetime.datetime.now().timestamp() + 3600
        with (
            patch.object(spotify_mod, "_spotify_tokens", {"u1": {"access": "tok", "expiry": future_expiry}}),
            patch("httpx.AsyncClient", return_value=mock_client),
        ):
            result = asyncio.run(spotify_mod._spotify_req("get", "/me/player", "u1", {"spotify_refresh_token": "rt"}))
        assert result is resp
        mock_client.get.assert_awaited_once()


class TestSpotifyStartParty:
    def test_calls_shuffle_and_play(self):
        mock_req = AsyncMock(return_value=MagicMock(status_code=204))
        with patch.object(spotify_mod, "_spotify_req", new=mock_req):
            asyncio.run(spotify_mod._spotify_start_party("u1", {}))
        assert mock_req.await_count == 2

    def test_swallows_exceptions(self):
        mock_req = AsyncMock(side_effect=Exception("boom"))
        with patch.object(spotify_mod, "_spotify_req", new=mock_req):
            asyncio.run(spotify_mod._spotify_start_party("u1", {}))


class TestSpotifyAuthUrl:
    def test_raises_when_not_configured(self):
        with patch("integrations.music.spotify.SPOTIFY_CLIENT_ID", ""):
            try:
                spotify_mod._spotify_auth_url("u1")
                raise AssertionError("expected HTTPException")
            except HTTPException as e:
                assert e.status_code == 503

    def test_returns_url_with_state(self):
        with (
            patch("integrations.music.spotify.SPOTIFY_CLIENT_ID", "cid"),
            patch("integrations.music.spotify.APP_URL", "https://jarvis.example.com"),
        ):
            url = spotify_mod._spotify_auth_url("u1")
        assert url.startswith("https://accounts.spotify.com/authorize?")
        assert "client_id=cid" in url


class TestSpotifyFinishAuth:
    def test_invalid_state_raises(self):
        try:
            asyncio.run(spotify_mod._spotify_finish_auth(None, "code", AsyncMock(), MagicMock()))
            raise AssertionError("expected HTTPException")
        except HTTPException as e:
            assert e.status_code == 400

    def test_success_saves_tokens(self):
        spotify_mod._spotify_auth_pending["state123"] = "u1"
        resp = MagicMock()
        resp.raise_for_status = MagicMock()
        resp.json = MagicMock(return_value={"access_token": "at", "refresh_token": "rt", "expires_in": 3600})

        config = {}
        state = {"config": config}

        async def get_user_state(uid):
            return state

        lock_cm = _async_cm()

        def get_user_lock(uid):
            return lock_cm

        pool, conn = _mock_asyncpg_pool()
        with (
            patch("httpx.AsyncClient", return_value=_mock_async_client(post=resp)),
            patch("integrations.music.spotify._pool", return_value=pool),
        ):
            result_uid = asyncio.run(spotify_mod._spotify_finish_auth("state123", "authcode", get_user_state, get_user_lock))
        assert result_uid == "u1"
        assert config["spotify_access_token"] == "at"
        conn.execute.assert_awaited_once()

    def test_token_exchange_failure_raises_502(self):
        spotify_mod._spotify_auth_pending["state456"] = "u1"
        with patch("httpx.AsyncClient", return_value=_mock_async_client(post=AsyncMock(side_effect=Exception("network error")))):
            try:
                asyncio.run(spotify_mod._spotify_finish_auth("state456", "authcode", AsyncMock(), MagicMock()))
                raise AssertionError("expected HTTPException")
            except HTTPException as e:
                assert e.status_code == 502


class TestSpotifyDisconnect:
    def test_clears_tokens(self):
        config = {"spotify_access_token": "at", "spotify_refresh_token": "rt", "spotify_token_expiry": 123.0}
        state = {"config": config}

        async def get_user_state(uid):
            return state

        lock_cm = _async_cm()

        def get_user_lock(uid):
            return lock_cm

        pool, conn = _mock_asyncpg_pool()
        spotify_mod._spotify_tokens["u1"] = {"access": "at", "expiry": 123.0}
        with patch("integrations.music.spotify._pool", return_value=pool):
            asyncio.run(spotify_mod._spotify_disconnect("u1", get_user_state, get_user_lock))
        assert config["spotify_access_token"] == ""
        assert config["spotify_refresh_token"] == ""
        assert "u1" not in spotify_mod._spotify_tokens


class TestExecuteSpotifyToolSearchVariants:
    _cfg = {"spotify_refresh_token": "rtok"}

    def _mock_resp(self, status=204, text="", json_data=None):
        r = MagicMock()
        r.status_code = status
        r.text = text
        if json_data is not None:
            r.json = MagicMock(return_value=json_data)
        return r

    def test_search_and_play_playlist(self):
        search_data = {"playlists": {"items": [{"uri": "spotify:playlist:xyz", "name": "Chill Vibes"}]}}

        async def mock_req(method, _endpoint, *_a, **_kw):
            return self._mock_resp(200, "x", search_data) if method == "get" else self._mock_resp(204)

        with patch("integrations.music.spotify._spotify_req", new=mock_req):
            result = asyncio.run(_execute_spotify_tool("spotify_search_and_play", {"query": "chill", "type": "playlist"}, "u1", self._cfg))
        assert "Chill Vibes" in result

    def test_search_and_play_artist(self):
        search_data = {"artists": {"items": [{"uri": "spotify:artist:xyz", "name": "Daft Punk"}]}}

        async def mock_req(method, _endpoint, *_a, **_kw):
            return self._mock_resp(200, "x", search_data) if method == "get" else self._mock_resp(204)

        with patch("integrations.music.spotify._spotify_req", new=mock_req):
            result = asyncio.run(_execute_spotify_tool("spotify_search_and_play", {"query": "daft punk", "type": "artist"}, "u1", self._cfg))
        assert "Daft Punk" in result

    def test_search_and_play_album(self):
        search_data = {"albums": {"items": [{"uri": "spotify:album:xyz", "name": "Discovery", "artists": [{"name": "Daft Punk"}]}]}}

        async def mock_req(method, _endpoint, *_a, **_kw):
            return self._mock_resp(200, "x", search_data) if method == "get" else self._mock_resp(204)

        with patch("integrations.music.spotify._spotify_req", new=mock_req):
            result = asyncio.run(_execute_spotify_tool("spotify_search_and_play", {"query": "discovery", "type": "album"}, "u1", self._cfg))
        assert "Discovery" in result

    def test_search_and_play_found_but_playback_fails(self):
        search_data = {"tracks": {"items": [{"uri": "spotify:track:abc", "name": "Track", "artists": [{"name": "Artist"}]}]}}

        async def mock_req(method, _endpoint, *_a, **_kw):
            return self._mock_resp(200, "x", search_data) if method == "get" else self._mock_resp(500)

        with patch("integrations.music.spotify._spotify_req", new=mock_req):
            result = asyncio.run(_execute_spotify_tool("spotify_search_and_play", {"query": "track", "type": "track"}, "u1", self._cfg))
        assert "playback failed" in result


# ── integrations/finance.py ───────────────────────────────────────────────────


class TestParseDate:
    def test_parses_iso_string(self):
        assert finance_mod._parse_date("2026-07-01") == datetime.date(2026, 7, 1)

    def test_passes_through_date_object(self):
        d = datetime.date(2026, 7, 1)
        assert finance_mod._parse_date(d) is d


class TestPlaidLinkToken:
    def test_create_link_token(self):
        fake_client = MagicMock()
        fake_response = MagicMock()
        fake_response.to_dict = MagicMock(return_value={"link_token": "link-abc"})
        fake_client.link_token_create = MagicMock(return_value=fake_response)
        with patch("integrations.finance._plaid_client", return_value=fake_client):
            token = asyncio.run(finance_mod._plaid_create_link_token("u1"))
        assert token == "link-abc"


class TestPlaidSyncTransactions:
    def test_syncs_transactions_and_accounts(self):
        fake_client = MagicMock()
        sync_resp = MagicMock()
        sync_resp.to_dict = MagicMock(
            return_value={
                "added": [{"account_id": "a1", "transaction_id": "t1", "amount": 10.0, "date": "2026-07-01", "name": "Coffee"}],
                "modified": [],
                "removed": [],
                "next_cursor": "cursor2",
                "has_more": False,
            }
        )
        fake_client.transactions_sync = MagicMock(return_value=sync_resp)
        accounts_resp = MagicMock()
        accounts_resp.to_dict = MagicMock(return_value={"accounts": [{"account_id": "a1", "name": "Checking"}]})
        fake_client.accounts_get = MagicMock(return_value=accounts_resp)

        with (
            patch("integrations.finance._plaid_client", return_value=fake_client),
            patch("integrations.finance._db_upsert_plaid_transactions", new=AsyncMock()) as mock_upsert_txn,
            patch("integrations.finance._db_update_plaid_cursor", new=AsyncMock()) as mock_cursor,
            patch("integrations.finance._db_upsert_plaid_accounts", new=AsyncMock()) as mock_upsert_acct,
        ):
            asyncio.run(finance_mod._plaid_sync_transactions("u1", 1, "access-tok", ""))
        mock_upsert_txn.assert_awaited_once()
        mock_cursor.assert_awaited_once_with(1, "cursor2")
        mock_upsert_acct.assert_awaited_once()


class TestPlaidExchangePublicToken:
    def test_exchanges_and_syncs(self):
        fake_client = MagicMock()
        exchange_resp = MagicMock()
        exchange_resp.to_dict = MagicMock(return_value={"access_token": "at", "item_id": "item1"})
        fake_client.item_public_token_exchange = MagicMock(return_value=exchange_resp)

        with (
            patch("integrations.finance._plaid_client", return_value=fake_client),
            patch("integrations.finance._db_add_plaid_item", new=AsyncMock(return_value=5)),
            patch("integrations.finance._plaid_sync_transactions", new=AsyncMock()) as mock_sync,
        ):
            result = asyncio.run(finance_mod._plaid_exchange_public_token("u1", "public-tok", "ins_1", "Chase"))
        assert result == {"item_id": "item1", "institution_name": "Chase"}
        mock_sync.assert_awaited_once_with("u1", 5, "at", "")


class TestPlaidRemoveItem:
    def test_removes_item(self):
        fake_client = MagicMock()
        fake_client.item_remove = MagicMock(return_value=MagicMock())
        with patch("integrations.finance._plaid_client", return_value=fake_client):
            asyncio.run(finance_mod._plaid_remove_item("access-tok"))
        fake_client.item_remove.assert_called_once()

    def test_swallows_exceptions(self):
        fake_client = MagicMock()
        fake_client.item_remove = MagicMock(side_effect=Exception("network error"))
        with patch("integrations.finance._plaid_client", return_value=fake_client):
            asyncio.run(finance_mod._plaid_remove_item("access-tok"))


class TestExecuteFinanceToolEdgeCases:
    def test_no_transactions_found(self):
        with patch("integrations.finance._db_get_recent_transactions", new=AsyncMock(return_value=[])):
            result = asyncio.run(_execute_finance_tool("get_recent_transactions", {}, "u1"))
        assert "No transactions found" in result

    def test_no_spending_found(self):
        with patch("integrations.finance._db_get_spending_by_category", new=AsyncMock(return_value=[])):
            result = asyncio.run(_execute_finance_tool("get_spending_by_category", {}, "u1"))
        assert "No spending found" in result

    def test_generic_exception_wrapped(self):
        with patch("integrations.finance._db_list_plaid_accounts", new=AsyncMock(side_effect=Exception("db down"))):
            result = asyncio.run(_execute_finance_tool("get_account_balances", {}, "u1"))
        assert "Finance error: db down" in result


class TestFinanceLoop:
    def test_skips_when_not_ready(self):
        call_count = 0

        async def fake_sleep(secs):
            nonlocal call_count
            call_count += 1
            if call_count > 2:
                raise RuntimeError("stop-loop")

        with (
            patch("integrations.finance.asyncio.sleep", new=fake_sleep),
            patch("integrations.finance._db_ready", return_value=False),
        ):
            try:
                asyncio.run(finance_mod._finance_loop())
                raise AssertionError("expected loop to stop")
            except RuntimeError as e:
                assert str(e) == "stop-loop"

    def test_syncs_items_and_marks_status(self):
        call_count = 0

        async def fake_sleep(secs):
            nonlocal call_count
            call_count += 1
            if call_count > 2:
                raise RuntimeError("stop-loop")

        items = [
            {"user_id": "u1", "id": 1, "access_token": "tok1", "cursor": "", "status": "pending"},
            {"user_id": "u2", "id": 2, "access_token": "tok2", "cursor": "", "status": "active"},
        ]

        async def fake_sync(user_id, item_pk, access_token, cursor):
            if item_pk == 2:
                raise Exception("ITEM_LOGIN_REQUIRED: relink needed")

        mark_calls = []

        async def fake_mark(item_pk, status):
            mark_calls.append((item_pk, status))

        with (
            patch("integrations.finance.asyncio.sleep", new=fake_sleep),
            patch("integrations.finance._db_ready", return_value=True),
            patch("integrations.finance.PLAID_CLIENT_ID", "cid"),
            patch("integrations.finance.PLAID_SECRET", "secret"),
            patch("integrations.finance._db_list_all_plaid_items", new=AsyncMock(return_value=items)),
            patch("integrations.finance._plaid_sync_transactions", new=fake_sync),
            patch("integrations.finance._db_mark_plaid_item_status", new=fake_mark),
        ):
            try:
                asyncio.run(finance_mod._finance_loop())
                raise AssertionError("expected loop to stop")
            except RuntimeError:
                pass
        assert (1, "active") in mark_calls
        assert (2, "login_required") in mark_calls


# ── integrations/automation.py ────────────────────────────────────────────────


class TestRunRoutine:
    def test_noop_when_not_initialized(self):
        with patch.object(automation_mod, "_sids_fn", None):
            asyncio.run(automation_mod._run_routine("u1", {}, [{"type": "speak", "text": "hi"}]))

    def test_executes_ha_service_speak_and_delay_steps(self):
        sio = MagicMock()
        sio.emit = AsyncMock()
        automation_mod.init(sio, lambda uid: ["sid1"], {})
        cfg = {"ha_url": "http://ha.local", "ha_token": "tok"}
        mock_call_service = AsyncMock()
        steps = [
            {"type": "ha_service", "domain": "light", "service": "turn_off"},
            {"type": "speak", "text": "Goodnight"},
            {"type": "delay", "seconds": 1},
        ]
        with (
            patch("integrations.automation._ha_call_service", new=mock_call_service),
            patch("integrations.automation.asyncio.sleep", new=AsyncMock()) as mock_sleep,
        ):
            asyncio.run(automation_mod._run_routine("u1", cfg, steps))
        mock_call_service.assert_awaited_once()
        sio.emit.assert_awaited_once_with("speak_sentence", {"text": "Goodnight", "seq": 1}, to="sid1")
        mock_sleep.assert_awaited_once_with(1)

    def test_step_exception_is_caught_and_logged(self):
        sio = MagicMock()
        sio.emit = AsyncMock()
        automation_mod.init(sio, lambda uid: ["sid1"], {})
        cfg = {"ha_url": "http://ha.local", "ha_token": "tok"}
        with patch("integrations.automation._ha_call_service", new=AsyncMock(side_effect=Exception("boom"))):
            asyncio.run(automation_mod._run_routine("u1", cfg, [{"type": "ha_service", "domain": "light", "service": "turn_on"}]))


class TestExecuteZigbeeTool:
    def test_not_configured(self):
        with patch("integrations.automation.MQTT_BROKER", ""):
            result = asyncio.run(automation_mod._execute_zigbee_tool({"device": "lamp", "payload": {"state": "ON"}}))
        assert "not configured" in result

    def test_missing_device(self):
        with patch("integrations.automation.MQTT_BROKER", "mqtt.local"):
            result = asyncio.run(automation_mod._execute_zigbee_tool({"payload": {}}))
        assert "Specify a device" in result

    def test_import_error_when_aiomqtt_missing(self):
        with (
            patch("integrations.automation.MQTT_BROKER", "mqtt.local"),
            patch.dict("sys.modules", {"aiomqtt": None}),
        ):
            result = asyncio.run(automation_mod._execute_zigbee_tool({"device": "lamp", "payload": {"state": "ON"}}))
        assert "aiomqtt not installed" in result

    def test_publishes_command_successfully(self):
        mock_client = _mock_async_client(publish=AsyncMock())
        mock_aiomqtt = MagicMock()
        mock_aiomqtt.Client = MagicMock(return_value=mock_client)
        with (
            patch("integrations.automation.MQTT_BROKER", "mqtt.local"),
            patch.dict("sys.modules", {"aiomqtt": mock_aiomqtt}),
        ):
            result = asyncio.run(automation_mod._execute_zigbee_tool({"device": "lamp", "payload": {"state": "ON"}}))
        assert "Command sent to lamp" in result
        mock_client.publish.assert_awaited_once()

    def test_mqtt_error_wrapped(self):
        mock_aiomqtt = MagicMock()
        mock_aiomqtt.Client = MagicMock(side_effect=Exception("connection refused"))
        with (
            patch("integrations.automation.MQTT_BROKER", "mqtt.local"),
            patch.dict("sys.modules", {"aiomqtt": mock_aiomqtt}),
        ):
            result = asyncio.run(automation_mod._execute_zigbee_tool({"device": "lamp", "payload": {"state": "ON"}}))
        assert "MQTT error" in result


class TestDeviceAlertLoop:
    def test_skips_when_db_not_ready(self):
        call_count = 0

        async def fake_sleep(secs):
            nonlocal call_count
            call_count += 1
            if call_count > 1:
                raise RuntimeError("stop-loop")

        with (
            patch("integrations.automation.asyncio.sleep", new=fake_sleep),
            patch("integrations.automation._db_ready", return_value=False),
        ):
            try:
                asyncio.run(automation_mod._device_alert_loop())
                raise AssertionError("expected loop to stop")
            except RuntimeError:
                pass

    def test_fires_alert_when_condition_met(self):
        call_count = 0

        async def fake_sleep(secs):
            nonlocal call_count
            call_count += 1
            if call_count > 1:
                raise RuntimeError("stop-loop")

        alert = {
            "id": 1,
            "user_id": "u1",
            "entity_id": "sensor.temp",
            "condition": "greater_than",
            "value": "75",
            "message": "It's hot",
            "name": "Heat alert",
            "cooldown_minutes": 30,
            "last_fired": None,
        }
        sio = MagicMock()
        sio.emit = AsyncMock()
        user_states = {"u1": {"config": {"ha_url": "http://ha.local", "ha_token": "tok"}}}
        automation_mod.init(sio, lambda uid: ["sid1"], user_states)

        with (
            patch("integrations.automation.asyncio.sleep", new=fake_sleep),
            patch("integrations.automation._db_ready", return_value=True),
            patch("integrations.automation._db_get_active_device_alerts", new=AsyncMock(return_value=[alert])),
            patch("integrations.automation._ha_get_entity_state", new=AsyncMock(return_value="80")),
            patch("integrations.automation._db_update_alert_last_fired", new=AsyncMock()) as mock_update,
        ):
            try:
                asyncio.run(automation_mod._device_alert_loop())
                raise AssertionError("expected loop to stop")
            except RuntimeError:
                pass
        sio.emit.assert_awaited_once()
        mock_update.assert_awaited_once_with(1)

    def test_skips_alert_when_user_state_missing(self):
        call_count = 0

        async def fake_sleep(secs):
            nonlocal call_count
            call_count += 1
            if call_count > 1:
                raise RuntimeError("stop-loop")

        alert = {
            "id": 2,
            "user_id": "unknown-user",
            "entity_id": "sensor.temp",
            "condition": "equals",
            "value": "on",
            "message": "x",
            "name": "y",
            "cooldown_minutes": 30,
            "last_fired": None,
        }
        automation_mod.init(MagicMock(), lambda uid: [], {})

        with (
            patch("integrations.automation.asyncio.sleep", new=fake_sleep),
            patch("integrations.automation._db_ready", return_value=True),
            patch("integrations.automation._db_get_active_device_alerts", new=AsyncMock(return_value=[alert])),
        ):
            try:
                asyncio.run(automation_mod._device_alert_loop())
                raise AssertionError("expected loop to stop")
            except RuntimeError:
                pass


# ── db.py ──────────────────────────────────────────────────────────────────


class TestDbInitCloseReady:
    def test_ready_false_when_no_pool(self):
        with patch.object(db_mod, "_db_pool", None):
            assert db_mod._db_ready() is False

    def test_ready_true_when_pool_set(self):
        with patch.object(db_mod, "_db_pool", MagicMock()):
            assert db_mod._db_ready() is True

    def test_close_noop_when_no_pool(self):
        with patch.object(db_mod, "_db_pool", None):
            asyncio.run(db_mod._db_close())

    def test_close_closes_and_clears_pool(self):
        pool = MagicMock()
        pool.close = AsyncMock()
        with patch.object(db_mod, "_db_pool", pool):
            asyncio.run(db_mod._db_close())
            assert db_mod._db_pool is None
        pool.close.assert_awaited_once()

    def test_init_creates_pool_and_runs_schema(self):
        pool, conn = _mock_asyncpg_pool()
        with (
            patch("db.asyncpg.create_pool", new=AsyncMock(return_value=pool)),
            patch.object(db_mod, "_db_pool", None),
        ):
            asyncio.run(db_mod._db_init())
            assert db_mod._db_pool is pool
        conn.execute.assert_awaited_once_with(db_mod._SCHEMA)


class TestDbUserConfig:
    def test_ensure_user(self):
        pool, conn = _mock_asyncpg_pool()
        with patch("db._pool", return_value=pool):
            asyncio.run(db_mod._db_ensure_user("u1", "a@b.com", "admin"))
        conn.execute.assert_awaited_once_with(conn.execute.call_args.args[0], "u1", "a@b.com", "admin")

    def test_load_config_found(self):
        row = {"role": "user", "provider": "anthropic", "api_key": "k", "model": "claude-haiku-4-5"}
        pool, conn = _mock_asyncpg_pool(fetchrow=row)
        with patch("db._pool", return_value=pool):
            result = asyncio.run(db_mod._db_load_config("u1"))
        assert result == row

    def test_load_config_defaults_when_missing(self):
        pool, conn = _mock_asyncpg_pool(fetchrow=None)
        with patch("db._pool", return_value=pool):
            result = asyncio.run(db_mod._db_load_config("u1"))
        assert result["role"] == "user"
        assert result["provider"] == "anthropic"
        assert result["apple_music_storefront"] == "us"
        assert result["is_kid_safe"] is False

    def test_save_config(self):
        pool, conn = _mock_asyncpg_pool()
        cfg = {"provider": "anthropic", "api_key": "k", "model": "m", "base_url": "", "ha_url": "", "ha_token": ""}
        with patch("db._pool", return_value=pool):
            asyncio.run(db_mod._db_save_config("u1", cfg))
        assert conn.execute.await_count == 2

    def test_set_kid_safe(self):
        pool, conn = _mock_asyncpg_pool()
        with patch("db._pool", return_value=pool):
            asyncio.run(db_mod._db_set_kid_safe("u1", True))
        conn.execute.assert_awaited_once()

    def test_set_display_name(self):
        pool, conn = _mock_asyncpg_pool()
        with patch("db._pool", return_value=pool):
            asyncio.run(db_mod._db_set_display_name("u1", "Alice"))
        conn.execute.assert_awaited_once()

    def test_save_pim_config(self):
        pool, conn = _mock_asyncpg_pool()
        with patch("db._pool", return_value=pool):
            asyncio.run(db_mod._db_save_pim_config("u1", "url", "user", "pw", "curl", "cuser", "cpw"))
        assert conn.execute.await_count == 2

    def test_get_household_members(self):
        rows = [{"user_id": "u1", "email": "a@b.com", "display_name": "", "is_kid_safe": False, "has_voice": True}]
        pool, conn = _mock_asyncpg_pool(fetch=rows)
        with patch("db._pool", return_value=pool):
            result = asyncio.run(db_mod._db_get_household_members())
        assert result == rows


class TestDbWebhookTokens:
    def test_get_or_create_returns_existing(self):
        pool, conn = _mock_asyncpg_pool(fetchrow={"webhook_token": "tok123"})
        with patch("db._pool", return_value=pool):
            token = asyncio.run(db_mod._db_get_or_create_webhook_token("u1"))
        assert token == "tok123"

    def test_get_or_create_generates_new(self):
        pool, conn = _mock_asyncpg_pool(fetchrow={"webhook_token": ""})
        with patch("db._pool", return_value=pool):
            token = asyncio.run(db_mod._db_get_or_create_webhook_token("u1"))
        assert len(token) == 64
        conn.execute.assert_awaited_once()

    def test_regenerate_token(self):
        pool, conn = _mock_asyncpg_pool()
        with patch("db._pool", return_value=pool):
            token = asyncio.run(db_mod._db_regenerate_webhook_token("u1"))
        assert len(token) == 64
        conn.execute.assert_awaited_once()

    def test_find_user_by_token_found(self):
        pool, conn = _mock_asyncpg_pool(fetchrow={"user_id": "u1"})
        with patch("db._pool", return_value=pool):
            result = asyncio.run(db_mod._db_find_user_by_token("tok"))
        assert result == "u1"

    def test_find_user_by_token_not_found(self):
        pool, conn = _mock_asyncpg_pool(fetchrow=None)
        with patch("db._pool", return_value=pool):
            result = asyncio.run(db_mod._db_find_user_by_token("bogus"))
        assert result is None


class TestDbConversations:
    def test_load_conversation_parses_json(self):
        import json

        rows = [{"role": "user", "content": json.dumps("hello")}]
        pool, conn = _mock_asyncpg_pool(fetch=rows)
        with patch("db._pool", return_value=pool):
            result = asyncio.run(db_mod._db_load_conversation("u1"))
        assert result == [{"role": "user", "content": "hello"}]

    def test_append_message(self):
        pool, conn = _mock_asyncpg_pool()
        with patch("db._pool", return_value=pool):
            asyncio.run(db_mod._db_append_message("u1", "user", "hi"))
        assert conn.execute.await_count == 2

    def test_clear_conversation(self):
        pool, conn = _mock_asyncpg_pool()
        with patch("db._pool", return_value=pool):
            asyncio.run(db_mod._db_clear_conversation("u1"))
        conn.execute.assert_awaited_once()


class TestDbVoiceEmbeddings:
    def test_save_voice_embedding(self):
        pool, conn = _mock_asyncpg_pool()
        with patch("db._pool", return_value=pool):
            asyncio.run(db_mod._db_save_voice_embedding("u1", [0.1, 0.2]))
        conn.execute.assert_awaited_once()

    def test_clear_voice_embedding(self):
        pool, conn = _mock_asyncpg_pool()
        with patch("db._pool", return_value=pool):
            asyncio.run(db_mod._db_clear_voice_embedding("u1"))
        conn.execute.assert_awaited_once()

    def test_get_all_voice_embeddings_skips_empty(self):
        import json

        rows = [
            {"user_id": "u1", "voice_embedding": json.dumps([1, 2, 3]), "display_name": "Alice", "is_kid_safe": False},
            {"user_id": "u2", "voice_embedding": None, "display_name": "", "is_kid_safe": False},
        ]
        pool, conn = _mock_asyncpg_pool(fetch=rows)
        with patch("db._pool", return_value=pool):
            result = asyncio.run(db_mod._db_get_all_voice_embeddings())
        assert list(result.keys()) == ["u1"]
        assert result["u1"] == ([1, 2, 3], "Alice", False)


class TestDbSharedLists:
    def test_get_shared_list_found(self):
        import json

        pool, conn = _mock_asyncpg_pool(fetchrow={"items": json.dumps(["milk"])})
        with patch("db._pool", return_value=pool):
            result = asyncio.run(db_mod._db_get_shared_list("shopping"))
        assert result == ["milk"]

    def test_get_shared_list_creates_when_missing(self):
        pool, conn = _mock_asyncpg_pool(fetchrow=None)
        with patch("db._pool", return_value=pool):
            result = asyncio.run(db_mod._db_get_shared_list("shopping"))
        assert result == []
        conn.execute.assert_awaited_once()

    def test_create_shared_list(self):
        pool, conn = _mock_asyncpg_pool()
        with patch("db._pool", return_value=pool):
            asyncio.run(db_mod._db_create_shared_list("shopping"))
        conn.execute.assert_awaited_once()

    def test_update_shared_list(self):
        pool, conn = _mock_asyncpg_pool()
        with patch("db._pool", return_value=pool):
            asyncio.run(db_mod._db_update_shared_list("shopping", ["milk", "eggs"]))
        conn.execute.assert_awaited_once()

    def test_get_all_shared_lists(self):
        import json

        rows = [{"name": "shopping", "items": json.dumps(["milk"])}, {"name": "todo", "items": None}]
        pool, conn = _mock_asyncpg_pool(fetch=rows)
        with patch("db._pool", return_value=pool):
            result = asyncio.run(db_mod._db_get_all_shared_lists())
        assert result == {"shopping": ["milk"], "todo": []}


class TestDbTimers:
    def test_set_timer(self):
        pool, conn = _mock_asyncpg_pool(fetchrow={"id": 5})
        with patch("db._pool", return_value=pool):
            tid = asyncio.run(db_mod._db_set_timer("u1", "pasta", 600))
        assert tid == 5

    def test_list_timers(self):
        rows = [{"id": 1, "label": "pasta", "fire_at": datetime.datetime.now()}]
        pool, conn = _mock_asyncpg_pool(fetch=rows)
        with patch("db._pool", return_value=pool):
            result = asyncio.run(db_mod._db_list_timers("u1"))
        assert result == rows

    def test_cancel_timer_true(self):
        pool, conn = _mock_asyncpg_pool(execute="UPDATE 1")
        with patch("db._pool", return_value=pool):
            assert asyncio.run(db_mod._db_cancel_timer("u1", 1)) is True

    def test_cancel_timer_false(self):
        pool, conn = _mock_asyncpg_pool(execute="UPDATE 0")
        with patch("db._pool", return_value=pool):
            assert asyncio.run(db_mod._db_cancel_timer("u1", 1)) is False

    def test_fire_due_timers(self):
        rows = [{"user_id": "u1", "label": "pasta"}]
        pool, conn = _mock_asyncpg_pool(fetch=rows)
        with patch("db._pool", return_value=pool):
            result = asyncio.run(db_mod._db_fire_due_timers())
        assert result == rows


class TestDbReminders:
    def test_set_reminder(self):
        pool, conn = _mock_asyncpg_pool(fetchrow={"id": 7})
        with patch("db._pool", return_value=pool):
            rid = asyncio.run(db_mod._db_set_reminder("u1", "drink water", datetime.datetime.now(), None))
        assert rid == 7

    def test_list_reminders(self):
        rows = [{"id": 1, "text": "drink water", "fire_at": datetime.datetime.now(), "recurring_minutes": None}]
        pool, conn = _mock_asyncpg_pool(fetch=rows)
        with patch("db._pool", return_value=pool):
            result = asyncio.run(db_mod._db_list_reminders("u1"))
        assert result == rows

    def test_cancel_reminder_true(self):
        pool, conn = _mock_asyncpg_pool(execute="UPDATE 1")
        with patch("db._pool", return_value=pool):
            assert asyncio.run(db_mod._db_cancel_reminder("u1", 1)) is True

    def test_fire_due_reminders_recurring(self):
        rows = [{"id": 1, "user_id": "u1", "text": "drink water", "recurring_minutes": 30}]
        pool, conn = _mock_asyncpg_pool(fetch=rows)
        with patch("db._pool", return_value=pool):
            result = asyncio.run(db_mod._db_fire_due_reminders())
        assert result == rows
        assert conn.execute.await_count == 1

    def test_fire_due_reminders_one_time(self):
        rows = [{"id": 2, "user_id": "u1", "text": "one-off", "recurring_minutes": None}]
        pool, conn = _mock_asyncpg_pool(fetch=rows)
        with patch("db._pool", return_value=pool):
            result = asyncio.run(db_mod._db_fire_due_reminders())
        assert result == rows
        assert conn.execute.await_count == 1


class TestDbRoutines:
    def test_create_routine(self):
        pool, conn = _mock_asyncpg_pool(fetchrow={"id": 3})
        with patch("db._pool", return_value=pool):
            rid = asyncio.run(db_mod._db_create_routine("u1", "Good Morning", ["good morning"], [{"type": "speak"}]))
        assert rid == 3

    def test_list_routines(self):
        import json

        rows = [{"id": 1, "name": "Good Morning", "trigger_phrases": json.dumps(["hi"]), "steps": json.dumps([{"type": "speak"}]), "active": True}]
        pool, conn = _mock_asyncpg_pool(fetch=rows)
        with patch("db._pool", return_value=pool):
            result = asyncio.run(db_mod._db_list_routines("u1"))
        assert result[0]["trigger_phrases"] == ["hi"]
        assert result[0]["steps"] == [{"type": "speak"}]

    def test_delete_routine(self):
        pool, conn = _mock_asyncpg_pool(execute="DELETE 1")
        with patch("db._pool", return_value=pool):
            assert asyncio.run(db_mod._db_delete_routine("u1", 1)) is True

    def test_toggle_routine(self):
        pool, conn = _mock_asyncpg_pool(execute="UPDATE 1")
        with patch("db._pool", return_value=pool):
            assert asyncio.run(db_mod._db_toggle_routine("u1", 1, False)) is True


class TestDbDeviceAlertsCrud:
    def test_create_device_alert(self):
        pool, conn = _mock_asyncpg_pool(fetchrow={"id": 9})
        with patch("db._pool", return_value=pool):
            aid = asyncio.run(db_mod._db_create_device_alert("u1", "Heat", "sensor.temp", "greater_than", "75", "hot", 30))
        assert aid == 9

    def test_list_device_alerts(self):
        rows = [{"id": 1, "name": "Heat", "entity_id": "sensor.temp", "condition": "greater_than", "value": "75", "message": "hot", "cooldown_minutes": 30, "active": True}]
        pool, conn = _mock_asyncpg_pool(fetch=rows)
        with patch("db._pool", return_value=pool):
            result = asyncio.run(db_mod._db_list_device_alerts("u1"))
        assert result == rows

    def test_delete_device_alert(self):
        pool, conn = _mock_asyncpg_pool(execute="DELETE 1")
        with patch("db._pool", return_value=pool):
            assert asyncio.run(db_mod._db_delete_device_alert("u1", 1)) is True

    def test_get_active_device_alerts(self):
        rows = [{"id": 1, "user_id": "u1"}]
        pool, conn = _mock_asyncpg_pool(fetch=rows)
        with patch("db._pool", return_value=pool):
            result = asyncio.run(db_mod._db_get_active_device_alerts())
        assert result == rows

    def test_update_alert_last_fired(self):
        pool, conn = _mock_asyncpg_pool()
        with patch("db._pool", return_value=pool):
            asyncio.run(db_mod._db_update_alert_last_fired(1))
        conn.execute.assert_awaited_once()


class TestDbPhoneMessages:
    def test_store_phone_message(self):
        pool, conn = _mock_asyncpg_pool()
        with patch("db._pool", return_value=pool):
            asyncio.run(db_mod._db_store_phone_message("u1", "555-1234", "hi", False, ""))
        conn.execute.assert_awaited_once()


class TestDbMeetings:
    def test_create_meeting(self):
        pool, conn = _mock_asyncpg_pool(fetchrow={"id": 4})
        with patch("db._pool", return_value=pool):
            mid = asyncio.run(db_mod._db_create_meeting("u1"))
        assert mid == 4

    def test_append_transcript_segment(self):
        pool, conn = _mock_asyncpg_pool()
        with patch("db._pool", return_value=pool):
            asyncio.run(db_mod._db_append_transcript_segment(4, "hello"))
        conn.execute.assert_awaited_once()

    def test_finalize_meeting(self):
        pool, conn = _mock_asyncpg_pool()
        with patch("db._pool", return_value=pool):
            asyncio.run(db_mod._db_finalize_meeting(4, "notes"))
        conn.execute.assert_awaited_once()


class TestDbDoorbell:
    def test_store_doorbell_event(self):
        pool, conn = _mock_asyncpg_pool()
        with patch("db._pool", return_value=pool):
            asyncio.run(db_mod._db_store_doorbell_event("u1", "motion", "front_door"))
        conn.execute.assert_awaited_once()

    def test_get_recent_doorbell_events(self):
        rows = [{"event_type": "motion", "source": "front_door", "received_at": datetime.datetime(2026, 7, 1, 12, 0)}]
        pool, conn = _mock_asyncpg_pool(fetch=rows)
        with patch("db._pool", return_value=pool):
            result = asyncio.run(db_mod._db_get_recent_doorbell_events("u1"))
        assert result[0]["received_at"] == "2026-07-01T12:00:00"


class TestDbCameras:
    def test_add_camera(self):
        pool, conn = _mock_asyncpg_pool(fetchval=11)
        with patch("db._pool", return_value=pool):
            cid = asyncio.run(db_mod._db_add_camera("u1", "Front Door", "Entry", "rtsp", "rtsp://x"))
        assert cid == 11

    def test_list_cameras(self):
        rows = [{"id": 1, "name": "Front Door", "room": "Entry", "source_type": "rtsp", "source": "rtsp://x", "enabled": True, "privacy": False}]
        pool, conn = _mock_asyncpg_pool(fetch=rows)
        with patch("db._pool", return_value=pool):
            result = asyncio.run(db_mod._db_list_cameras("u1"))
        assert result == rows

    def test_delete_camera_true(self):
        pool, conn = _mock_asyncpg_pool(execute="DELETE 1")
        with patch("db._pool", return_value=pool):
            assert asyncio.run(db_mod._db_delete_camera("u1", 1)) is True

    def test_delete_camera_false(self):
        pool, conn = _mock_asyncpg_pool(execute="DELETE 0")
        with patch("db._pool", return_value=pool):
            assert asyncio.run(db_mod._db_delete_camera("u1", 1)) is False

    def test_update_camera_with_valid_fields(self):
        pool, conn = _mock_asyncpg_pool(execute="UPDATE 1")
        with patch("db._pool", return_value=pool):
            assert asyncio.run(db_mod._db_update_camera("u1", 1, enabled=False, bogus="ignored")) is True

    def test_update_camera_no_valid_fields(self):
        pool, conn = _mock_asyncpg_pool()
        with patch("db._pool", return_value=pool):
            assert asyncio.run(db_mod._db_update_camera("u1", 1, bogus="ignored")) is False
        conn.execute.assert_not_awaited()

    def test_record_detection(self):
        pool, conn = _mock_asyncpg_pool()
        with patch("db._pool", return_value=pool):
            asyncio.run(db_mod._db_record_detection("u1", 1, "u2", 0.9, "Kitchen"))
        conn.execute.assert_awaited_once()

    def test_record_security_event(self):
        pool, conn = _mock_asyncpg_pool()
        with patch("db._pool", return_value=pool):
            asyncio.run(db_mod._db_record_security_event("u1", 1, "unknown_person", "Kitchen"))
        conn.execute.assert_awaited_once()

    def test_get_recent_security_events(self):
        rows = [{"event_type": "unknown_person", "room": "Kitchen", "detected_at": datetime.datetime(2026, 7, 1, 8, 0)}]
        pool, conn = _mock_asyncpg_pool(fetch=rows)
        with patch("db._pool", return_value=pool):
            result = asyncio.run(db_mod._db_get_recent_security_events("u1"))
        assert result[0]["detected_at"] == "2026-07-01T08:00:00"


class TestInferActivity:
    def test_bedroom_night(self):
        assert db_mod._infer_activity("Master Bedroom", 23) == "sleeping"

    def test_bedroom_day(self):
        assert db_mod._infer_activity("Bedroom", 14) == "resting"

    def test_kitchen(self):
        assert db_mod._infer_activity("Kitchen", 12) == "cooking"

    def test_gym(self):
        assert db_mod._infer_activity("Home Gym", 9) == "exercising"

    def test_office(self):
        assert db_mod._infer_activity("Office", 10) == "working"

    def test_bathroom(self):
        assert db_mod._infer_activity("Bathroom", 8) == "unavailable"

    def test_default_room(self):
        assert db_mod._infer_activity("Living Room", 15) == "home"


class TestDbFacePresence:
    def test_get_who_is_home(self):
        rows = [{"user_id": "u1", "display_name": "Alice", "is_home": True, "last_seen_at": datetime.datetime(2026, 7, 1, 9, 0), "room": "Kitchen"}]
        pool, conn = _mock_asyncpg_pool(fetch=rows)
        with patch("db._pool", return_value=pool):
            result = asyncio.run(db_mod._db_get_who_is_home())
        assert result[0]["name"] == "Alice"
        assert result[0]["activity"] == "cooking"

    def test_get_who_is_home_no_room_or_last_seen(self):
        rows = [{"user_id": "u1", "display_name": "", "is_home": True, "last_seen_at": None, "room": None}]
        pool, conn = _mock_asyncpg_pool(fetch=rows)
        with patch("db._pool", return_value=pool):
            result = asyncio.run(db_mod._db_get_who_is_home())
        assert result[0]["name"] == "u1"
        assert result[0]["last_seen_at"] is None
        assert result[0]["room"] == ""

    def test_get_all_face_embeddings(self):
        rows = [{"user_id": "u1", "display_name": "Alice", "face_embedding": [0.1, 0.2]}]
        pool, conn = _mock_asyncpg_pool(fetch=rows)
        with patch("db._pool", return_value=pool):
            result = asyncio.run(db_mod._db_get_all_face_embeddings())
        assert result == {"u1": ([0.1, 0.2], "Alice")}

    def test_save_face_embedding(self):
        pool, conn = _mock_asyncpg_pool()
        with patch("db._pool", return_value=pool):
            asyncio.run(db_mod._db_save_face_embedding("u1", [0.1, 0.2]))
        conn.execute.assert_awaited_once()

    def test_clear_face_embedding(self):
        pool, conn = _mock_asyncpg_pool()
        with patch("db._pool", return_value=pool):
            asyncio.run(db_mod._db_clear_face_embedding("u1"))
        conn.execute.assert_awaited_once()

    def test_update_presence(self):
        pool, conn = _mock_asyncpg_pool()
        with patch("db._pool", return_value=pool):
            asyncio.run(db_mod._db_update_presence("u1", True))
        conn.execute.assert_awaited_once()


class TestDbPlaid:
    def test_add_plaid_item(self):
        pool, conn = _mock_asyncpg_pool(fetchval=21)
        with patch("db._pool", return_value=pool):
            pk = asyncio.run(db_mod._db_add_plaid_item("u1", "item1", "access-tok", "ins_1", "Chase"))
        assert pk == 21

    def test_list_plaid_items(self):
        rows = [{"id": 1, "item_id": "item1", "institution_name": "Chase", "status": "active", "created_at": datetime.datetime.now()}]
        pool, conn = _mock_asyncpg_pool(fetch=rows)
        with patch("db._pool", return_value=pool):
            result = asyncio.run(db_mod._db_list_plaid_items("u1"))
        assert result == rows

    def test_list_all_plaid_items(self):
        rows = [{"id": 1, "user_id": "u1", "item_id": "item1", "access_token": "at", "cursor": "", "status": "active"}]
        pool, conn = _mock_asyncpg_pool(fetch=rows)
        with patch("db._pool", return_value=pool):
            result = asyncio.run(db_mod._db_list_all_plaid_items())
        assert result == rows

    def test_get_plaid_item_found(self):
        row = {"id": 1, "item_id": "item1", "access_token": "at", "institution_name": "Chase", "cursor": "", "status": "active"}
        pool, conn = _mock_asyncpg_pool(fetchrow=row)
        with patch("db._pool", return_value=pool):
            result = asyncio.run(db_mod._db_get_plaid_item("u1", 1))
        assert result == row

    def test_get_plaid_item_not_found(self):
        pool, conn = _mock_asyncpg_pool(fetchrow=None)
        with patch("db._pool", return_value=pool):
            result = asyncio.run(db_mod._db_get_plaid_item("u1", 1))
        assert result is None

    def test_delete_plaid_item_true(self):
        pool, conn = _mock_asyncpg_pool(execute="DELETE 1")
        with patch("db._pool", return_value=pool):
            assert asyncio.run(db_mod._db_delete_plaid_item("u1", 1)) is True

    def test_update_plaid_cursor(self):
        pool, conn = _mock_asyncpg_pool()
        with patch("db._pool", return_value=pool):
            asyncio.run(db_mod._db_update_plaid_cursor(1, "cursor2"))
        conn.execute.assert_awaited_once()

    def test_mark_plaid_item_status(self):
        pool, conn = _mock_asyncpg_pool()
        with patch("db._pool", return_value=pool):
            asyncio.run(db_mod._db_mark_plaid_item_status(1, "error"))
        conn.execute.assert_awaited_once()

    def test_upsert_plaid_accounts(self):
        pool, conn = _mock_asyncpg_pool()
        accounts = [
            {
                "account_id": "a1",
                "name": "Checking",
                "official_name": "",
                "mask": "1234",
                "type": "depository",
                "subtype": "checking",
                "balance_current": 100.0,
                "balance_available": 90.0,
                "balance_limit": None,
                "iso_currency": "USD",
            }
        ]
        with patch("db._pool", return_value=pool):
            asyncio.run(db_mod._db_upsert_plaid_accounts("u1", 1, accounts))
        conn.execute.assert_awaited_once()

    def test_list_plaid_accounts(self):
        rows = [
            {
                "id": 1,
                "item_id": 1,
                "account_id": "a1",
                "name": "Checking",
                "official_name": "",
                "mask": "1234",
                "type": "depository",
                "subtype": "checking",
                "balance_current": 100.0,
                "balance_available": 90.0,
                "balance_limit": None,
                "iso_currency": "USD",
            }
        ]
        pool, conn = _mock_asyncpg_pool(fetch=rows)
        with patch("db._pool", return_value=pool):
            result = asyncio.run(db_mod._db_list_plaid_accounts("u1"))
        assert result == rows

    def test_upsert_plaid_transactions_with_removals(self):
        pool, conn = _mock_asyncpg_pool()
        upserts = [
            {
                "account_id": "a1",
                "transaction_id": "t1",
                "amount": 10.0,
                "iso_currency": "USD",
                "date": datetime.date(2026, 7, 1),
                "merchant_name": "Coffee Shop",
                "name": "COFFEE",
                "category": "Food",
                "personal_finance_category": "FOOD_AND_DRINK",
                "pending": False,
            }
        ]
        with patch("db._pool", return_value=pool):
            asyncio.run(db_mod._db_upsert_plaid_transactions("u1", upserts, ["t2"]))
        assert conn.execute.await_count == 2

    def test_get_recent_transactions(self):
        rows = [
            {
                "id": 1,
                "transaction_id": "t1",
                "amount": 10.0,
                "date": datetime.date(2026, 7, 1),
                "merchant_name": "Coffee Shop",
                "name": "COFFEE",
                "category": "Food",
                "personal_finance_category": "",
                "category_override": None,
                "pending": False,
            }
        ]
        pool, conn = _mock_asyncpg_pool(fetch=rows)
        with patch("db._pool", return_value=pool):
            result = asyncio.run(db_mod._db_get_recent_transactions("u1"))
        assert result == rows

    def test_get_spending_by_category(self):
        rows = [{"category": "Food", "total": 42.5}]
        pool, conn = _mock_asyncpg_pool(fetch=rows)
        with patch("db._pool", return_value=pool):
            result = asyncio.run(db_mod._db_get_spending_by_category("u1"))
        assert result == [{"category": "Food", "total": 42.5}]

    def test_find_transaction_by_merchant_found(self):
        row = {"id": 1, "transaction_id": "t1", "amount": 10.0, "date": datetime.date(2026, 7, 1), "merchant_name": "Coffee Shop", "name": "COFFEE"}
        pool, conn = _mock_asyncpg_pool(fetchrow=row)
        with patch("db._pool", return_value=pool):
            result = asyncio.run(db_mod._db_find_transaction_by_merchant("u1", "Coffee"))
        assert result == row

    def test_find_transaction_by_merchant_not_found(self):
        pool, conn = _mock_asyncpg_pool(fetchrow=None)
        with patch("db._pool", return_value=pool):
            result = asyncio.run(db_mod._db_find_transaction_by_merchant("u1", "Nowhere"))
        assert result is None

    def test_set_transaction_category_override_true(self):
        pool, conn = _mock_asyncpg_pool(execute="UPDATE 1")
        with patch("db._pool", return_value=pool):
            assert asyncio.run(db_mod._db_set_transaction_category_override("u1", 1, "Business")) is True


# ── app.py routes ──────────────────────────────────────────────────────────────
# `api_client` (module-scoped) always resolves the caller as user_id "local"
# since `_oidc_config` is never set in tests (see conftest.py). Routes that
# call `_get_user_state` need `_seed_user_state()` first or they'll try a real
# DB load against the fixture's bare MagicMock pool and fail.


class TestAuthRoutes:
    def test_login_redirects_to_root_when_no_oidc(self, api_client):
        with patch.object(auth, "_oidc_config", None):
            resp = api_client.get("/login", follow_redirects=False)
        assert resp.status_code == 302
        assert resp.headers["location"] == "/"

    def test_login_redirects_to_oidc_when_configured(self, api_client):
        with patch.object(auth, "_oidc_config", {"authorization_endpoint": "https://auth.example.com/authorize"}):
            resp = api_client.get("/login", follow_redirects=False)
        assert resp.status_code == 307 or resp.status_code == 302
        assert resp.headers["location"].startswith("https://auth.example.com/authorize?")
        assert "oidc_state" in resp.cookies

    def test_auth_callback_missing_code_returns_400(self, api_client):
        resp = api_client.get("/auth/callback")
        assert resp.status_code == 400

    def test_auth_callback_state_mismatch_returns_400(self, api_client):
        resp = api_client.get("/auth/callback?code=abc&state=x", cookies={"oidc_state": "y"})
        assert resp.status_code == 400

    def test_auth_callback_success(self, api_client):
        token_resp = MagicMock(status_code=200)
        token_resp.raise_for_status = MagicMock()
        token_resp.json = MagicMock(return_value={"access_token": "at"})
        userinfo_resp = MagicMock(status_code=200)
        userinfo_resp.raise_for_status = MagicMock()
        userinfo_resp.json = MagicMock(return_value={"sub": "user-42", "email": "a@b.com", "groups": []})
        mock_client = _mock_async_client(post=token_resp, get=userinfo_resp)
        with (
            patch.object(auth, "_oidc_config", {"token_endpoint": "https://auth.example.com/token", "userinfo_endpoint": "https://auth.example.com/userinfo"}),
            patch("httpx.AsyncClient", return_value=mock_client),
            patch.object(jarvis, "_db_ensure_user", new=AsyncMock()) as mock_ensure,
        ):
            resp = api_client.get("/auth/callback?code=abc&state=x", cookies={"oidc_state": "x"}, follow_redirects=False)
        assert resp.status_code == 303
        assert resp.headers["location"] == "/"
        mock_ensure.assert_awaited_once_with("user-42", "a@b.com", "user")

    def test_auth_callback_token_exchange_failure_returns_502(self, api_client):
        mock_client = _mock_async_client(post=AsyncMock(side_effect=Exception("network down")))
        with (
            patch.object(auth, "_oidc_config", {"token_endpoint": "https://auth.example.com/token", "userinfo_endpoint": "https://auth.example.com/userinfo"}),
            patch("httpx.AsyncClient", return_value=mock_client),
        ):
            resp = api_client.get("/auth/callback?code=abc&state=x", cookies={"oidc_state": "x"})
        assert resp.status_code == 502

    def test_logout_redirects_to_login(self, api_client):
        resp = api_client.get("/logout", follow_redirects=False)
        assert resp.status_code == 303
        assert resp.headers["location"] == "/login"


class TestIndexRoute:
    def test_redirects_to_login_when_not_authenticated(self, api_client):
        with patch.object(jarvis, "_get_current_user", return_value=None):
            resp = api_client.get("/", follow_redirects=False)
        assert resp.status_code == 307
        assert resp.headers["location"] == "/login"

    def test_renders_when_authenticated(self, api_client):
        resp = api_client.get("/")
        assert resp.status_code == 200
        assert "text/html" in resp.headers["content-type"]


class TestPWARoutes:
    def test_manifest_served_at_root(self, api_client):
        resp = api_client.get("/manifest.json")
        assert resp.status_code == 200
        assert resp.headers["content-type"] == "application/manifest+json"
        data = resp.json()
        assert data["name"] == "J.A.R.V.I.S."
        assert data["start_url"] == "/"
        assert len(data["icons"]) >= 2

    def test_service_worker_served_at_root_scope(self, api_client):
        # Must be served from "/" (not "/static/") so its default scope
        # covers the whole origin.
        resp = api_client.get("/sw.js")
        assert resp.status_code == 200
        assert "javascript" in resp.headers["content-type"]


class TestApiStatus:
    def test_returns_status_fields(self, api_client):
        _seed_user_state(config={"provider": "anthropic", "model": "claude-haiku-4-5"})
        with patch.object(jarvis, "_finance_configured", new=AsyncMock(return_value=False)):
            resp = api_client.get("/api/status")
        assert resp.status_code == 200
        data = resp.json()
        assert data["provider"] == "anthropic"
        assert data["model"] == "claude-haiku-4-5"
        assert data["role"] == "user"


class TestApiSaveConfig:
    def test_unknown_provider_rejected(self, api_client):
        resp = api_client.post("/api/save_config", json={"provider": "bogus", "key": "k"})
        assert resp.json() == {"ok": False, "error": "Unknown provider."}

    def test_missing_key_rejected(self, api_client):
        resp = api_client.post("/api/save_config", json={"provider": "anthropic", "key": ""})
        assert resp.json()["ok"] is False
        assert "API key" in resp.json()["error"]

    def test_openai_compatible_missing_base_url_rejected(self, api_client):
        resp = api_client.post("/api/save_config", json={"provider": "openai_compatible", "key": "k", "base_url": ""})
        assert resp.json()["ok"] is False
        assert "base URL" in resp.json()["error"]

    def test_validate_failure_surfaced(self, api_client):
        with patch.object(jarvis, "_validate", return_value=(False, "Key was rejected")):
            resp = api_client.post("/api/save_config", json={"provider": "anthropic", "key": "bad-key"})
        assert resp.json() == {"ok": False, "error": "Key was rejected"}

    def test_success_saves_config(self, api_client):
        _seed_user_state()
        with (
            patch.object(jarvis, "_validate", return_value=(True, "")),
            patch.object(jarvis, "_db_save_config", new=AsyncMock()) as mock_save,
        ):
            resp = api_client.post("/api/save_config", json={"provider": "anthropic", "key": "k", "model": "claude-haiku-4-5"})
        assert resp.json() == {"ok": True}
        mock_save.assert_awaited_once()

    def test_ha_validation_failure_surfaced(self, api_client):
        _seed_user_state()
        with (
            patch.object(jarvis, "_validate", return_value=(True, "")),
            patch.object(jarvis, "_validate_ha", new=AsyncMock(return_value=(False, "token rejected"))),
        ):
            resp = api_client.post(
                "/api/save_config",
                json={"provider": "anthropic", "key": "k", "ha_url": "http://ha.local", "ha_token": "tok"},
            )
        assert resp.json() == {"ok": False, "error": "Home Assistant: token rejected"}


class TestApiCamerasAndVision:
    def test_list_cameras(self, api_client):
        with patch.object(jarvis, "_list_cameras", new=AsyncMock(return_value=[{"id": 1, "name": "Front Door"}])):
            resp = api_client.get("/api/cameras")
        assert resp.json() == [{"id": 1, "name": "Front Door"}]

    def test_add_camera(self, api_client):
        with patch.object(jarvis, "_add_camera", new=AsyncMock(return_value={"ok": True, "id": 1})):
            resp = api_client.post("/api/cameras", json={"name": "Front Door", "source_type": "rtsp", "source": "rtsp://x"})
        assert resp.json() == {"ok": True, "id": 1}

    def test_delete_camera(self, api_client):
        with patch.object(jarvis, "_delete_camera", new=AsyncMock(return_value={"ok": True})):
            resp = api_client.delete("/api/cameras/1")
        assert resp.json() == {"ok": True}

    def test_update_camera(self, api_client):
        with patch.object(jarvis, "_update_camera", new=AsyncMock(return_value={"ok": True})):
            resp = api_client.patch("/api/cameras/1", json={"enabled": False})
        assert resp.json() == {"ok": True}

    def test_presence(self, api_client):
        with patch.object(jarvis, "_get_presence_members", new=AsyncMock(return_value=[{"user_id": "u1", "is_home": True}])):
            resp = api_client.get("/api/presence")
        assert resp.json() == [{"user_id": "u1", "is_home": True}]

    def test_security_events(self, api_client):
        with patch.object(jarvis, "_get_security_events", new=AsyncMock(return_value=[])) as mock_events:
            resp = api_client.get("/api/security-events?hours=12")
        assert resp.json() == []
        mock_events.assert_awaited_once_with("local", 12.0)

    def test_face_enroll_sample(self, api_client):
        with patch.object(jarvis, "_face_enroll_sample", new=AsyncMock(return_value={"ok": True, "embedding": [0.1]})):
            resp = api_client.post("/api/face/enroll-sample", files={"image": ("face.jpg", b"fake-bytes", "image/jpeg")})
        assert resp.json() == {"ok": True, "embedding": [0.1]}

    def test_face_enroll_finish(self, api_client):
        with patch.object(jarvis, "_face_enroll_finish", new=AsyncMock(return_value={"ok": True})) as mock_finish:
            resp = api_client.post("/api/face/enroll-finish", json={"embeddings": [[0.1, 0.2]]})
        assert resp.json() == {"ok": True}
        mock_finish.assert_awaited_once_with("local", [[0.1, 0.2]])

    def test_face_enroll_delete(self, api_client):
        with patch.object(jarvis, "_face_enroll_delete", new=AsyncMock(return_value={"ok": True})):
            resp = api_client.delete("/api/face/enrollment")
        assert resp.json() == {"ok": True}


class TestApiSaveHa:
    def test_saves_without_validation_when_no_token_change(self, api_client):
        _seed_user_state(config={"ha_url": "", "ha_token": ""})
        with patch.object(jarvis, "_db_save_config", new=AsyncMock()):
            resp = api_client.post("/api/save_ha", json={"ha_url": "", "ha_token": ""})
        assert resp.json() == {"ok": True, "ha_configured": False}

    def test_validation_failure_surfaced(self, api_client):
        _seed_user_state(config={})
        with patch.object(jarvis, "_validate_ha", new=AsyncMock(return_value=(False, "bad token"))):
            resp = api_client.post("/api/save_ha", json={"ha_url": "http://ha.local", "ha_token": "tok"})
        assert resp.json() == {"ok": False, "error": "bad token"}

    def test_success(self, api_client):
        _seed_user_state(config={})
        with (
            patch.object(jarvis, "_validate_ha", new=AsyncMock(return_value=(True, ""))),
            patch.object(jarvis, "_db_save_config", new=AsyncMock()),
        ):
            resp = api_client.post("/api/save_ha", json={"ha_url": "http://ha.local", "ha_token": "tok"})
        assert resp.json() == {"ok": True, "ha_configured": True}


class TestApiSavePim:
    def test_clears_calendar_and_contacts(self, api_client):
        _seed_user_state(config={"calendar_url": "old", "contacts_url": "old"})
        with patch.object(jarvis, "_db_save_pim_config", new=AsyncMock()):
            resp = api_client.post("/api/save_pim", json={"clear_calendar": True, "clear_contacts": True})
        data = resp.json()
        assert data["ok"] is True
        assert data["calendar_url"] == ""
        assert data["contacts_url"] == ""

    def test_calendar_requires_username(self, api_client):
        _seed_user_state(config={})
        resp = api_client.post("/api/save_pim", json={"calendar_url": "https://dav.example.com/", "calendar_username": ""})
        assert resp.json()["ok"] is False
        assert "username" in resp.json()["error"]

    def test_calendar_resolve_failure_surfaced(self, api_client):
        _seed_user_state(config={})
        with patch.object(jarvis, "_resolve_dav_collection", new=AsyncMock(side_effect=ValueError("auth failed"))):
            resp = api_client.post(
                "/api/save_pim",
                json={"calendar_url": "https://dav.example.com/", "calendar_username": "me", "calendar_password": "pw"},
            )
        assert resp.json() == {"ok": False, "error": "Calendar: auth failed"}

    def test_success(self, api_client):
        _seed_user_state(config={})
        resolved = {"url": "https://dav.example.com/cal/", "display_name": "Personal"}
        with (
            patch.object(jarvis, "_resolve_dav_collection", new=AsyncMock(return_value=resolved)),
            patch.object(jarvis, "_db_save_pim_config", new=AsyncMock()),
        ):
            resp = api_client.post(
                "/api/save_pim",
                json={"calendar_url": "https://dav.example.com/", "calendar_username": "me", "calendar_password": "pw"},
            )
        data = resp.json()
        assert data["ok"] is True
        assert data["calendar_url"] == "https://dav.example.com/cal/"

    def test_contacts_requires_username(self, api_client):
        _seed_user_state(config={})
        resp = api_client.post("/api/save_pim", json={"contacts_url": "https://dav.example.com/", "contacts_username": ""})
        assert resp.json()["ok"] is False
        assert "username" in resp.json()["error"]

    def test_contacts_success(self, api_client):
        _seed_user_state(config={})
        resolved = {"url": "https://dav.example.com/ab/", "display_name": "Contacts"}
        with (
            patch.object(jarvis, "_resolve_dav_collection", new=AsyncMock(return_value=resolved)),
            patch.object(jarvis, "_db_save_pim_config", new=AsyncMock()),
        ):
            resp = api_client.post(
                "/api/save_pim",
                json={"contacts_url": "https://dav.example.com/", "contacts_username": "me", "contacts_password": "pw"},
            )
        data = resp.json()
        assert data["ok"] is True
        assert data["contacts_url"] == "https://dav.example.com/ab/"


class TestApiSaveMyq:
    def test_rejects_unreachable_myq(self, api_client):
        with patch.object(jarvis, "_myq_get_status", new=AsyncMock(return_value="Could not reach MyQ servers.")):
            resp = api_client.post("/api/save_myq", json={"myq_email": "a@b.com", "myq_password": "pw"})
        assert resp.json()["ok"] is False

    def test_success(self, api_client):
        _seed_user_state(config={})
        with (
            patch.object(jarvis, "_myq_get_status", new=AsyncMock(return_value="Garage: closed")),
            patch.object(jarvis, "_db_save_config", new=AsyncMock()),
        ):
            resp = api_client.post("/api/save_myq", json={"myq_email": "a@b.com", "myq_password": "pw"})
        assert resp.json() == {"ok": True, "myq_configured": True}

    def test_clears_credentials(self, api_client):
        _seed_user_state(config={})
        with patch.object(jarvis, "_db_save_config", new=AsyncMock()):
            resp = api_client.post("/api/save_myq", json={"myq_email": "", "myq_password": ""})
        assert resp.json() == {"ok": True, "myq_configured": False}


class TestApiFinanceRoutes:
    def test_link_token_requires_plaid_configured(self, api_client):
        with patch.object(jarvis, "PLAID_CLIENT_ID", ""):
            resp = api_client.post("/api/finance/link_token")
        assert resp.status_code == 503

    def test_link_token_success(self, api_client):
        with (
            patch.object(jarvis, "PLAID_CLIENT_ID", "cid"),
            patch.object(jarvis, "PLAID_SECRET", "secret"),
            patch.object(jarvis, "_plaid_create_link_token", new=AsyncMock(return_value="link-abc")),
        ):
            resp = api_client.post("/api/finance/link_token")
        assert resp.json() == {"link_token": "link-abc"}

    def test_exchange_token_missing_public_token(self, api_client):
        resp = api_client.post("/api/finance/exchange_token", json={})
        assert resp.json() == {"ok": False, "error": "Missing public_token."}

    def test_exchange_token_success(self, api_client):
        with patch.object(jarvis, "_plaid_exchange_public_token", new=AsyncMock(return_value={"item_id": "item1", "institution_name": "Chase"})):
            resp = api_client.post("/api/finance/exchange_token", json={"public_token": "pub-tok"})
        assert resp.json() == {"ok": True, "item_id": "item1", "institution_name": "Chase"}

    def test_exchange_token_failure_surfaced(self, api_client):
        with patch.object(jarvis, "_plaid_exchange_public_token", new=AsyncMock(side_effect=Exception("bad token"))):
            resp = api_client.post("/api/finance/exchange_token", json={"public_token": "pub-tok"})
        assert resp.json()["ok"] is False

    def test_connections(self, api_client):
        with (
            patch.object(jarvis, "_db_list_plaid_items", new=AsyncMock(return_value=[{"id": 1}])),
            patch.object(jarvis, "_db_list_plaid_accounts", new=AsyncMock(return_value=[{"id": 1, "name": "Checking"}])),
        ):
            resp = api_client.get("/api/finance/connections")
        assert resp.json() == {"connections": [{"id": 1}], "accounts": [{"id": 1, "name": "Checking"}]}

    def test_disconnect_not_found(self, api_client):
        with patch.object(jarvis, "_db_get_plaid_item", new=AsyncMock(return_value=None)):
            resp = api_client.delete("/api/finance/connections/1")
        assert resp.status_code == 404

    def test_disconnect_success(self, api_client):
        with (
            patch.object(jarvis, "_db_get_plaid_item", new=AsyncMock(return_value={"access_token": "at"})),
            patch.object(jarvis, "_plaid_remove_item", new=AsyncMock()),
            patch.object(jarvis, "_db_delete_plaid_item", new=AsyncMock(return_value=True)),
        ):
            resp = api_client.delete("/api/finance/connections/1")
        assert resp.json() == {"ok": True}

    def test_override_category_missing(self, api_client):
        resp = api_client.patch("/api/finance/transactions/1", json={"category": ""})
        assert resp.json() == {"ok": False, "error": "category is required"}

    def test_override_category_not_found(self, api_client):
        with patch.object(jarvis, "_db_set_transaction_category_override", new=AsyncMock(return_value=False)):
            resp = api_client.patch("/api/finance/transactions/1", json={"category": "Business"})
        assert resp.status_code == 404

    def test_override_category_success(self, api_client):
        with patch.object(jarvis, "_db_set_transaction_category_override", new=AsyncMock(return_value=True)):
            resp = api_client.patch("/api/finance/transactions/1", json={"category": "Business"})
        assert resp.json() == {"ok": True}


class TestApiMeetings:
    def test_list_meetings(self, api_client):
        pool, conn = _mock_asyncpg_pool(fetch=[{"id": 1, "started_at": datetime.datetime(2026, 7, 1, 9, 0), "ended_at": None, "notes": None}])
        with patch.object(jarvis, "_pool", return_value=pool):
            resp = api_client.get("/api/meetings")
        assert resp.json() == [{"id": 1, "started_at": "2026-07-01T09:00:00", "ended_at": None, "notes": None}]

    def test_meeting_detail_not_found(self, api_client):
        pool, conn = _mock_asyncpg_pool(fetchrow=None)
        with patch.object(jarvis, "_pool", return_value=pool):
            resp = api_client.get("/api/meetings/1")
        assert resp.status_code == 404

    def test_meeting_detail_found(self, api_client):
        row = {"id": 1, "started_at": datetime.datetime(2026, 7, 1, 9, 0), "ended_at": datetime.datetime(2026, 7, 1, 9, 30), "transcript": "hi", "notes": "notes"}
        pool, conn = _mock_asyncpg_pool(fetchrow=row)
        with patch.object(jarvis, "_pool", return_value=pool):
            resp = api_client.get("/api/meetings/1")
        data = resp.json()
        assert data["transcript"] == "hi"
        assert data["started_at"] == "2026-07-01T09:00:00"


class TestApiMessagesRoutes:
    def test_messages_token(self, api_client):
        with patch.object(jarvis, "_db_get_or_create_webhook_token", new=AsyncMock(return_value="tok123")):
            resp = api_client.get("/api/messages/token")
        data = resp.json()
        assert data["token"] == "tok123"
        assert data["url"].endswith("/api/messages/ingest")

    def test_messages_token_regenerate(self, api_client):
        with patch.object(jarvis, "_db_regenerate_webhook_token", new=AsyncMock(return_value="newtok")):
            resp = api_client.post("/api/messages/token/regenerate")
        assert resp.json()["token"] == "newtok"

    def test_download_apk_not_available(self, api_client):
        resp = api_client.get("/download/jarvis-messages.apk")
        assert resp.status_code == 404

    def test_messages_list(self, api_client):
        rows = [{"id": 1, "sender": "Alice", "body": "hi", "important": False, "reason": "", "received_at": datetime.datetime(2026, 7, 1, 9, 0)}]
        pool, conn = _mock_asyncpg_pool(fetch=rows)
        with patch.object(jarvis, "_pool", return_value=pool):
            resp = api_client.get("/api/messages")
        assert resp.json()[0]["sender"] == "Alice"


class TestApiDoorbellRoutes:
    def test_event_requires_bearer(self, api_client):
        resp = api_client.post("/api/doorbell/event", json={"event_type": "motion"})
        assert resp.status_code == 401

    def test_event_stored_and_broadcast(self, api_client):
        with (
            patch.object(jarvis, "_db_find_user_by_token", new=AsyncMock(return_value="user1")),
            patch.object(jarvis, "_db_store_doorbell_event", new=AsyncMock()) as mock_store,
            patch.object(jarvis, "_sids_for_user", return_value=["sid1"]),
            patch.object(jarvis, "sio") as mock_sio,
        ):
            mock_sio.emit = AsyncMock()
            resp = api_client.post(
                "/api/doorbell/event",
                headers={"Authorization": "Bearer validtoken"},
                json={"event_type": "doorbell_press", "source": "front_door"},
            )
            mock_sio.emit.assert_awaited_once()
        assert resp.json() == {"ok": True}
        mock_store.assert_awaited_once_with("user1", "doorbell_press", "front_door")

    def test_motion_suppressed_late_at_night(self, api_client):
        fake_now = datetime.datetime(2026, 7, 1, 23, 30)
        with (
            patch.object(jarvis, "_db_find_user_by_token", new=AsyncMock(return_value="user1")),
            patch.object(jarvis, "_db_store_doorbell_event", new=AsyncMock()),
            patch.object(jarvis.datetime, "datetime", MagicMock(now=MagicMock(return_value=fake_now))),
            patch.object(jarvis, "sio") as mock_sio,
        ):
            mock_sio.emit = AsyncMock()
            resp = api_client.post(
                "/api/doorbell/event",
                headers={"Authorization": "Bearer validtoken"},
                json={"event_type": "motion", "source": "front_door"},
            )
            mock_sio.emit.assert_not_awaited()
        assert resp.json() == {"ok": True}

    def test_token(self, api_client):
        with patch.object(jarvis, "_db_get_or_create_webhook_token", new=AsyncMock(return_value="tok123")):
            resp = api_client.get("/api/doorbell/token")
        assert resp.json()["token"] == "tok123"

    def test_events(self, api_client):
        rows = [{"id": 1, "event_type": "motion", "source": "front_door", "received_at": datetime.datetime(2026, 7, 1, 9, 0)}]
        pool, conn = _mock_asyncpg_pool(fetch=rows)
        with patch.object(jarvis, "_pool", return_value=pool):
            resp = api_client.get("/api/doorbell/events")
        assert resp.json()[0]["event_type"] == "motion"


class TestApiWake:
    def test_wake_requires_bearer(self, api_client):
        resp = api_client.post("/api/wake", json={"device_id": "living-room"})
        assert resp.status_code == 401

    def test_wake_broadcasts(self, api_client):
        jarvis._last_wake_time.pop("wakeuser", None)
        with (
            patch.object(jarvis, "_db_find_user_by_token", new=AsyncMock(return_value="wakeuser")),
            patch.object(jarvis, "_sids_for_user", return_value=["sid1"]),
            patch.object(jarvis, "sio") as mock_sio,
        ):
            mock_sio.emit = AsyncMock()
            resp = api_client.post(
                "/api/wake",
                headers={"Authorization": "Bearer validtoken"},
                json={"device_id": "living-room", "room": "Living Room"},
            )
            mock_sio.emit.assert_awaited_once()
        assert resp.json() == {"status": "ok"}

    def test_wake_deduplicates_rapid_repeats(self, api_client):
        jarvis._last_wake_time.pop("wakeuser", None)
        with patch.object(jarvis, "_db_find_user_by_token", new=AsyncMock(return_value="wakeuser")):
            api_client.post("/api/wake", headers={"Authorization": "Bearer validtoken"}, json={"device_id": "living-room"})
            resp = api_client.post("/api/wake", headers={"Authorization": "Bearer validtoken"}, json={"device_id": "living-room"})
        assert resp.json() == {"status": "ignored"}


class TestApiVoiceEnrollment:
    def test_enroll_sample_voice_id_unavailable(self, api_client):
        with patch.object(jarvis, "_VOICE_ID_OK", False):
            resp = api_client.post("/api/voice/enroll-sample", files={"audio": ("sample.webm", b"fake-audio", "audio/webm")})
        assert resp.json()["ok"] is False

    def test_enroll_sample_extraction_fails(self, api_client):
        with (
            patch.object(jarvis, "_VOICE_ID_OK", True),
            patch.object(jarvis, "_extract_voice_embedding", return_value=None),
        ):
            resp = api_client.post("/api/voice/enroll-sample", files={"audio": ("sample.webm", b"fake-audio", "audio/webm")})
        assert resp.json() == {"ok": False, "error": "Could not extract embedding."}

    def test_enroll_sample_success(self, api_client):
        with (
            patch.object(jarvis, "_VOICE_ID_OK", True),
            patch.object(jarvis, "_extract_voice_embedding", return_value=[0.1, 0.2]),
        ):
            resp = api_client.post("/api/voice/enroll-sample", files={"audio": ("sample.webm", b"fake-audio", "audio/webm")})
        assert resp.json() == {"ok": True, "embedding": [0.1, 0.2]}

    def test_enroll_finish_voice_id_unavailable(self, api_client):
        with patch.object(jarvis, "_VOICE_ID_OK", False):
            resp = api_client.post("/api/voice/enroll-finish", json={"embeddings": [[0.1], [0.2]]})
        assert resp.status_code == 400

    def test_enroll_finish_needs_two_samples(self, api_client):
        with patch.object(jarvis, "_VOICE_ID_OK", True):
            resp = api_client.post("/api/voice/enroll-finish", json={"embeddings": [[0.1]]})
        assert resp.status_code == 400

    def test_enroll_finish_success(self, api_client):
        with (
            patch.object(jarvis, "_VOICE_ID_OK", True),
            patch.object(jarvis, "_db_save_voice_embedding", new=AsyncMock()) as mock_save,
            patch.object(jarvis, "_refresh_voice_cache", new=AsyncMock()),
        ):
            resp = api_client.post("/api/voice/enroll-finish", json={"embeddings": [[0.1, 0.2], [0.3, 0.4]]})
        assert resp.json() == {"ok": True}
        mock_save.assert_awaited_once()

    def test_enrollment_delete(self, api_client):
        with (
            patch.object(jarvis, "_db_clear_voice_embedding", new=AsyncMock()) as mock_clear,
            patch.object(jarvis, "_refresh_voice_cache", new=AsyncMock()),
        ):
            resp = api_client.delete("/api/voice/enrollment")
        assert resp.json() == {"ok": True}
        mock_clear.assert_awaited_once()


class TestApiUserProfile:
    def test_updates_display_name(self, api_client):
        _seed_user_state(config={})
        with (
            patch.object(jarvis, "_db_set_display_name", new=AsyncMock()),
            patch.object(jarvis, "_refresh_voice_cache", new=AsyncMock()),
        ):
            resp = api_client.patch("/api/user/profile", json={"display_name": "Alice"})
        assert resp.json() == {"ok": True}
        assert jarvis._user_states["local"]["config"]["display_name"] == "Alice"

    def test_updates_kid_safe(self, api_client):
        _seed_user_state(config={})
        with (
            patch.object(jarvis, "_db_set_kid_safe", new=AsyncMock()),
            patch.object(jarvis, "_refresh_voice_cache", new=AsyncMock()),
        ):
            resp = api_client.patch("/api/user/profile", json={"is_kid_safe": True})
        assert resp.json() == {"ok": True}
        assert jarvis._user_states["local"]["config"]["is_kid_safe"] is True


class TestApiHouseholdMembers:
    def test_requires_admin(self, api_client):
        _seed_user_state(role="user")
        resp = api_client.get("/api/household/members")
        assert resp.status_code == 403

    def test_admin_success(self, api_client):
        _seed_user_state(role="admin")
        with patch.object(jarvis, "_db_get_household_members", new=AsyncMock(return_value=[{"user_id": "u1"}])):
            resp = api_client.get("/api/household/members")
        assert resp.json() == {"members": [{"user_id": "u1"}]}


class TestApiSharedListsRoute:
    def test_shared_lists(self, api_client):
        with patch.object(jarvis, "_db_get_all_shared_lists", new=AsyncMock(return_value={"shopping": ["milk"]})):
            resp = api_client.get("/api/shared-lists")
        assert resp.json() == {"lists": {"shopping": ["milk"]}}


class TestApiSnapcastStatusRoute:
    def test_not_configured(self, api_client):
        with patch.object(jarvis._snapcast_mod, "_snapcast_configured", return_value=False):
            resp = api_client.get("/api/snapcast/status")
        assert resp.status_code == 503

    def test_configured(self, api_client):
        with (
            patch.object(jarvis._snapcast_mod, "_snapcast_configured", return_value=True),
            patch.object(jarvis._snapcast_mod, "_snapcast_get_status", new=AsyncMock(return_value="Group 'g1' -> stream 's1'")),
        ):
            resp = api_client.get("/api/snapcast/status")
        assert resp.json() == {"status": "Group 'g1' -> stream 's1'"}


class TestApiTeslaRoutes:
    def test_status(self, api_client):
        _seed_user_state(config={"tesla_method": "unofficial", "tesla_refresh_token": "rt"})
        resp = api_client.get("/api/tesla/status")
        assert resp.json()["tesla_configured"] is True

    def test_save_unofficial_missing_token(self, api_client):
        resp = api_client.post("/api/tesla/save_unofficial", json={"refresh_token": ""})
        assert resp.json() == {"ok": False, "error": "No refresh token provided."}

    def test_save_unofficial_success(self, api_client):
        _seed_user_state(config={})
        resp_vehicles = MagicMock(status_code=200)
        resp_vehicles.raise_for_status = MagicMock()
        pool, conn = _mock_asyncpg_pool()
        with (
            patch.object(jarvis, "_tesla_access_token", new=AsyncMock(return_value="tok")),
            patch("httpx.AsyncClient", return_value=_mock_async_client(get=resp_vehicles)),
            patch.object(jarvis, "_pool", return_value=pool),
        ):
            resp = api_client.post("/api/tesla/save_unofficial", json={"refresh_token": "rt"})
        assert resp.json()["ok"] is True

    def test_save_unofficial_connect_failure(self, api_client):
        _seed_user_state(config={})
        with patch.object(jarvis, "_tesla_access_token", new=AsyncMock(side_effect=Exception("bad token"))):
            resp = api_client.post("/api/tesla/save_unofficial", json={"refresh_token": "rt"})
        assert resp.json()["ok"] is False

    def test_fleet_auth_not_configured(self, api_client):
        with patch.object(jarvis, "TESLA_CLIENT_ID", ""):
            resp = api_client.get("/api/tesla/fleet/auth")
        assert resp.status_code == 503

    def test_fleet_auth_redirect(self, api_client):
        with patch.object(jarvis, "TESLA_CLIENT_ID", "cid"):
            resp = api_client.get("/api/tesla/fleet/auth", follow_redirects=False)
        assert resp.status_code in (302, 307)
        assert resp.headers["location"].startswith(jarvis._tesla_mod._TESLA_AUTH_BASE)

    def test_callback_invalid_state(self, api_client):
        resp = api_client.get("/auth/tesla/callback?code=abc&state=bogus")
        assert resp.status_code == 400

    def test_callback_success(self, api_client):
        _seed_user_state(config={})
        jarvis._tesla_mod._tesla_auth_pending["state1"] = {"user_id": "local", "code_verifier": "verifier"}
        token_resp = MagicMock(status_code=200)
        token_resp.raise_for_status = MagicMock()
        token_resp.json = MagicMock(return_value={"refresh_token": "fleet-rt"})
        pool, conn = _mock_asyncpg_pool()
        with (
            patch("httpx.AsyncClient", return_value=_mock_async_client(post=token_resp)),
            patch.object(jarvis, "_pool", return_value=pool),
        ):
            resp = api_client.get("/auth/tesla/callback?code=abc&state=state1", follow_redirects=False)
        assert resp.status_code == 303
        assert resp.headers["location"] == "/?tesla_connected=1"

    def test_disconnect(self, api_client):
        _seed_user_state(config={"tesla_refresh_token": "rt", "tesla_fleet_refresh_token": "ft"})
        pool, conn = _mock_asyncpg_pool()
        with patch.object(jarvis, "_pool", return_value=pool):
            resp = api_client.post("/api/tesla/disconnect", json={"which": "all"})
        assert resp.json()["ok"] is True
        assert resp.json()["tesla_configured"] is False


class TestApiSpotifyRoutes:
    def test_auth_redirect(self, api_client):
        with patch.object(jarvis, "_spotify_auth_url", return_value="https://accounts.spotify.com/authorize?x=1"):
            resp = api_client.get("/api/spotify/auth", follow_redirects=False)
        assert resp.headers["location"] == "https://accounts.spotify.com/authorize?x=1"

    def test_callback_redirects(self, api_client):
        with patch.object(jarvis, "_spotify_finish_auth", new=AsyncMock(return_value="local")):
            resp = api_client.get("/auth/spotify/callback?state=x&code=y", follow_redirects=False)
        assert resp.status_code == 303
        assert resp.headers["location"] == "/?spotify_connected=1"

    def test_disconnect(self, api_client):
        with patch.object(jarvis, "_spotify_disconnect", new=AsyncMock()) as mock_disconnect:
            resp = api_client.post("/api/spotify/disconnect")
        assert resp.json() == {"ok": True}
        mock_disconnect.assert_awaited_once()


class TestApiAppleMusicRoutes:
    def test_token_not_configured(self, api_client):
        with patch.object(jarvis, "_apple_music_server_configured", return_value=False):
            resp = api_client.get("/api/apple_music/token")
        assert resp.json() == {"token": None, "enabled": False}

    def test_token_configured(self, api_client):
        with (
            patch.object(jarvis, "_apple_music_server_configured", return_value=True),
            patch.object(jarvis, "_apple_music_dev_token", return_value="fake.jwt.token"),
        ):
            resp = api_client.get("/api/apple_music/token")
        assert resp.json() == {"token": "fake.jwt.token", "enabled": True}

    def test_user_token_saved(self, api_client):
        with patch.object(jarvis, "_save_apple_music_user_token", new=AsyncMock()) as mock_save:
            resp = api_client.post("/api/apple_music/user_token", json={"token": "usertok", "storefront": "us"})
        assert resp.json() == {"ok": True}
        mock_save.assert_awaited_once()

    def test_disconnect(self, api_client):
        with patch.object(jarvis, "_disconnect_apple_music_user_token", new=AsyncMock()) as mock_disc:
            resp = api_client.post("/api/apple_music/disconnect")
        assert resp.json() == {"ok": True}
        mock_disc.assert_awaited_once()


class TestBackgroundLoops:
    def test_telemetry_loop_emits_hud_update(self):
        call_count = 0

        async def fake_sleep(secs):
            nonlocal call_count
            call_count += 1
            if call_count > 1:
                raise RuntimeError("stop-loop")

        with patch("app.asyncio.sleep", new=fake_sleep), patch.object(jarvis, "sio") as mock_sio:
            mock_sio.emit = AsyncMock()
            try:
                asyncio.run(jarvis._telemetry_loop())
                raise AssertionError("expected loop to stop")
            except RuntimeError:
                pass
        mock_sio.emit.assert_awaited_once()
        assert mock_sio.emit.call_args.args[0] == "hud_update"

    def test_weather_loop_swallows_errors(self):
        call_count = 0

        async def fake_sleep(secs):
            nonlocal call_count
            call_count += 1
            if call_count > 0:
                raise RuntimeError("stop-loop")

        with (
            patch("app.asyncio.sleep", new=fake_sleep),
            patch("httpx.AsyncClient", return_value=_mock_async_client(get=AsyncMock(side_effect=Exception("network down")))),
        ):
            try:
                asyncio.run(jarvis._weather_loop())
                raise AssertionError("expected loop to stop")
            except RuntimeError:
                pass

    def test_weather_loop_updates_location_context(self):
        call_count = 0

        async def fake_sleep(secs):
            nonlocal call_count
            call_count += 1
            if call_count > 0:
                raise RuntimeError("stop-loop")

        loc_resp = MagicMock(status_code=200)
        loc_resp.json = MagicMock(return_value={"lat": 40.0, "lon": -75.0, "city": "Philadelphia", "region": "PA"})
        wx_resp = MagicMock(status_code=200)
        wx_resp.json = MagicMock(return_value={"current": {"temperature_2m": 72.0, "surface_pressure": 1013.0, "weather_code": 1}})

        async def fake_get(url, **kwargs):
            return loc_resp if "ip-api.com" in url else wx_resp

        mock_client = _mock_async_client()
        mock_client.get = fake_get
        with (
            patch("app.asyncio.sleep", new=fake_sleep),
            patch("httpx.AsyncClient", return_value=mock_client),
            patch.object(jarvis, "sio") as mock_sio,
        ):
            mock_sio.emit = AsyncMock()
            try:
                asyncio.run(jarvis._weather_loop())
                raise AssertionError("expected loop to stop")
            except RuntimeError:
                pass
        assert jarvis._location_context["city"] == "Philadelphia"
        assert jarvis._location_context["condition"] == "Mainly clear"
        mock_sio.emit.assert_awaited_once()

    def test_timer_reminder_loop_skips_when_db_not_ready(self):
        call_count = 0

        async def fake_sleep(secs):
            nonlocal call_count
            call_count += 1
            if call_count > 1:
                raise RuntimeError("stop-loop")

        with (
            patch("app.asyncio.sleep", new=fake_sleep),
            patch.object(jarvis, "_db_ready", return_value=False),
        ):
            try:
                asyncio.run(jarvis._timer_reminder_loop())
                raise AssertionError("expected loop to stop")
            except RuntimeError:
                pass

    def test_timer_reminder_loop_fires_timers_and_reminders(self):
        call_count = 0

        async def fake_sleep(secs):
            nonlocal call_count
            call_count += 1
            if call_count > 1:
                raise RuntimeError("stop-loop")

        with (
            patch("app.asyncio.sleep", new=fake_sleep),
            patch.object(jarvis, "_db_ready", return_value=True),
            patch.object(jarvis, "_db_fire_due_timers", new=AsyncMock(return_value=[{"user_id": "u1", "label": "pasta"}])),
            patch.object(jarvis, "_db_fire_due_reminders", new=AsyncMock(return_value=[{"user_id": "u1", "text": "drink water"}])),
            patch.object(jarvis, "_sids_for_user", return_value=["sid1"]),
            patch.object(jarvis, "sio") as mock_sio,
        ):
            mock_sio.emit = AsyncMock()
            try:
                asyncio.run(jarvis._timer_reminder_loop())
                raise AssertionError("expected loop to stop")
            except RuntimeError:
                pass
        assert mock_sio.emit.await_count == 2

    def test_meeting_cleanup_loop_skips_when_db_not_ready(self):
        call_count = 0

        async def fake_sleep(secs):
            nonlocal call_count
            call_count += 1
            if call_count > 1:
                raise RuntimeError("stop-loop")

        with (
            patch("app.asyncio.sleep", new=fake_sleep),
            patch.object(jarvis, "_db_ready", return_value=False),
        ):
            try:
                asyncio.run(jarvis._meeting_cleanup_loop())
                raise AssertionError("expected loop to stop")
            except RuntimeError:
                pass

    def test_meeting_cleanup_loop_deletes_old_meetings(self):
        call_count = 0

        async def fake_sleep(secs):
            nonlocal call_count
            call_count += 1
            if call_count > 1:
                raise RuntimeError("stop-loop")

        pool, conn = _mock_asyncpg_pool(execute="DELETE 2")
        with (
            patch("app.asyncio.sleep", new=fake_sleep),
            patch.object(jarvis, "_db_ready", return_value=True),
            patch.object(jarvis, "_pool", return_value=pool),
        ):
            try:
                asyncio.run(jarvis._meeting_cleanup_loop())
                raise AssertionError("expected loop to stop")
            except RuntimeError:
                pass
        conn.execute.assert_awaited_once()


# ── integrations/vision.py ────────────────────────────────────────────────────


class TestVisionRuntime:
    def test_require_runtime_raises_before_init(self):
        with patch.object(vision_mod, "_sio", None), patch.object(vision_mod, "_sids_fn", None):
            try:
                vision_mod._require_runtime()
                raise AssertionError("expected RuntimeError")
            except RuntimeError:
                pass

    def test_require_runtime_returns_after_init(self):
        sio = MagicMock()
        sids_fn = MagicMock()
        vision_mod.init(sio, sids_fn)
        assert vision_mod._require_runtime() == (sio, sids_fn)


class TestVisionAvailable:
    def test_reflects_module_flag(self):
        with patch.object(vision_mod, "_VISION_OK", True):
            assert vision_mod._vision_available() is True
        with patch.object(vision_mod, "_VISION_OK", False):
            assert vision_mod._vision_available() is False


class TestGetPresenceCache:
    def test_returns_module_cache(self):
        with patch.object(vision_mod, "_presence_cache", [{"user_id": "u1"}]):
            assert vision_mod._get_presence_cache() == [{"user_id": "u1"}]


class TestGetPresencePromptContext:
    def test_empty_when_vision_unavailable(self):
        with patch.object(vision_mod, "_VISION_OK", False):
            assert vision_mod._get_presence_prompt_context() == ""

    def test_empty_when_cache_empty(self):
        with (
            patch.object(vision_mod, "_VISION_OK", True),
            patch.object(vision_mod, "_presence_cache", []),
        ):
            assert vision_mod._get_presence_prompt_context() == ""

    def test_formats_members_with_room_and_activity(self):
        cache = [{"name": "Alice", "room": "Kitchen", "activity": "cooking"}]
        with (
            patch.object(vision_mod, "_VISION_OK", True),
            patch.object(vision_mod, "_presence_cache", cache),
        ):
            text = vision_mod._get_presence_prompt_context()
        assert "Alice" in text
        assert "Kitchen" in text
        assert "cooking" in text

    def test_sleeping_suffix_singular(self):
        cache = [{"name": "Alice", "room": "Bedroom", "activity": "sleeping"}]
        with (
            patch.object(vision_mod, "_VISION_OK", True),
            patch.object(vision_mod, "_presence_cache", cache),
        ):
            text = vision_mod._get_presence_prompt_context()
        assert "Alice appears to be sleeping" in text

    def test_sleeping_suffix_plural(self):
        cache = [
            {"name": "Alice", "room": "Bedroom", "activity": "sleeping"},
            {"name": "Bob", "room": "Bedroom", "activity": "sleeping"},
        ]
        with (
            patch.object(vision_mod, "_VISION_OK", True),
            patch.object(vision_mod, "_presence_cache", cache),
        ):
            text = vision_mod._get_presence_prompt_context()
        assert "Alice, Bob appear to be sleeping" in text

    def test_home_activity_omitted_from_line(self):
        cache = [{"name": "Alice", "room": "", "activity": "home"}]
        with (
            patch.object(vision_mod, "_VISION_OK", True),
            patch.object(vision_mod, "_presence_cache", cache),
        ):
            text = vision_mod._get_presence_prompt_context()
        assert "(home)" not in text


class TestGetVisionTools:
    def test_empty_when_unavailable(self):
        with patch.object(vision_mod, "_VISION_OK", False):
            assert vision_mod._get_vision_tools("anthropic") == []

    def test_returns_anthropic_tools(self):
        with patch.object(vision_mod, "_VISION_OK", True):
            tools = vision_mod._get_vision_tools("anthropic")
        names = {t["name"] for t in tools}
        assert names == {"get_who_is_home", "get_security_events", "manage_camera"}

    def test_returns_openai_format(self):
        with patch.object(vision_mod, "_VISION_OK", True):
            tools = vision_mod._get_vision_tools("openai")
        assert all(t["type"] == "function" for t in tools)


class TestGetFaceApp:
    def test_lazily_constructs_and_caches(self):
        fake_fa = MagicMock()
        fake_cls = MagicMock(return_value=fake_fa)
        with (
            patch.object(vision_mod, "_VISION_OK", True),
            patch.object(vision_mod, "_face_app_instance", None),
            patch.object(vision_mod, "_FaceAnalysis", fake_cls),
        ):
            first = vision_mod._get_face_app()
            second = vision_mod._get_face_app()
        assert first is fake_fa
        assert second is fake_fa
        fake_cls.assert_called_once_with(name="buffalo_sc", providers=["CPUExecutionProvider"])
        fake_fa.prepare.assert_called_once_with(ctx_id=0, det_size=(320, 320))

    def test_returns_none_when_unavailable(self):
        with (
            patch.object(vision_mod, "_VISION_OK", False),
            patch.object(vision_mod, "_face_app_instance", None),
        ):
            assert vision_mod._get_face_app() is None


class TestExtractFaceEmbedding:
    def test_returns_none_when_unavailable(self):
        with patch.object(vision_mod, "_VISION_OK", False):
            assert vision_mod._extract_face_embedding(b"data") is None

    def test_returns_none_when_face_app_unavailable(self):
        with (
            patch.object(vision_mod, "_VISION_OK", True),
            patch.object(vision_mod, "_get_face_app", return_value=None),
        ):
            assert vision_mod._extract_face_embedding(b"data") is None

    def test_returns_none_when_image_decode_fails(self):
        fake_fa = MagicMock()
        with (
            patch.object(vision_mod, "_VISION_OK", True),
            patch.object(vision_mod, "_get_face_app", return_value=fake_fa),
            patch.object(vision_mod, "_np_v"),
            patch.object(vision_mod, "_cv2") as mock_cv2,
        ):
            mock_cv2.imdecode.return_value = None
            assert vision_mod._extract_face_embedding(b"data") is None

    def test_returns_none_when_no_faces_found(self):
        fake_fa = MagicMock()
        fake_fa.get.return_value = []
        with (
            patch.object(vision_mod, "_VISION_OK", True),
            patch.object(vision_mod, "_get_face_app", return_value=fake_fa),
            patch.object(vision_mod, "_np_v"),
            patch.object(vision_mod, "_cv2") as mock_cv2,
        ):
            mock_cv2.imdecode.return_value = MagicMock()
            assert vision_mod._extract_face_embedding(b"data") is None

    def test_returns_embedding_on_success(self):
        fake_face = MagicMock()
        fake_face.normed_embedding.tolist.return_value = [0.1, 0.2]
        fake_fa = MagicMock()
        fake_fa.get.return_value = [fake_face]
        with (
            patch.object(vision_mod, "_VISION_OK", True),
            patch.object(vision_mod, "_get_face_app", return_value=fake_fa),
            patch.object(vision_mod, "_np_v"),
            patch.object(vision_mod, "_cv2") as mock_cv2,
        ):
            mock_cv2.imdecode.return_value = MagicMock()
            result = vision_mod._extract_face_embedding(b"data")
        assert result == [0.1, 0.2]


class TestIdentifyFacesInImage:
    def test_returns_empty_when_unavailable(self):
        with patch.object(vision_mod, "_VISION_OK", False):
            assert vision_mod._identify_faces_in_image(b"data") == []

    def test_returns_empty_when_face_cache_empty(self):
        with (
            patch.object(vision_mod, "_VISION_OK", True),
            patch.object(vision_mod, "_face_cache", {}),
        ):
            assert vision_mod._identify_faces_in_image(b"data") == []

    def test_returns_empty_when_face_app_unavailable(self):
        with (
            patch.object(vision_mod, "_VISION_OK", True),
            patch.object(vision_mod, "_face_cache", {"u1": ([0.1], "Alice")}),
            patch.object(vision_mod, "_get_face_app", return_value=None),
        ):
            assert vision_mod._identify_faces_in_image(b"data") == []

    def test_returns_empty_when_image_decode_fails(self):
        fake_fa = MagicMock()
        with (
            patch.object(vision_mod, "_VISION_OK", True),
            patch.object(vision_mod, "_face_cache", {"u1": ([0.1], "Alice")}),
            patch.object(vision_mod, "_get_face_app", return_value=fake_fa),
            patch.object(vision_mod, "_np_v"),
            patch.object(vision_mod, "_cv2") as mock_cv2,
        ):
            mock_cv2.imdecode.return_value = None
            assert vision_mod._identify_faces_in_image(b"data") == []

    def test_identifies_known_face(self):
        fake_face = MagicMock()
        fake_face.normed_embedding.tolist.return_value = [0.1, 0.2]
        fake_fa = MagicMock()
        fake_fa.get.return_value = [fake_face]
        with (
            patch.object(vision_mod, "_VISION_OK", True),
            patch.object(vision_mod, "_face_cache", {"u1": ([0.1, 0.2], "Alice")}),
            patch.object(vision_mod, "_get_face_app", return_value=fake_fa),
            patch.object(vision_mod, "_np_v"),
            patch.object(vision_mod, "_cv2") as mock_cv2,
            patch.object(vision_mod, "best_match", return_value=("u1", 1.0, ("Alice",))),
        ):
            mock_cv2.imdecode.return_value = MagicMock()
            results = vision_mod._identify_faces_in_image(b"data")
        assert results == [{"detected_user_id": "u1", "name": "Alice", "confidence": 1.0}]

    def test_unknown_face_when_no_match_within_threshold(self):
        fake_face = MagicMock()
        fake_face.normed_embedding.tolist.return_value = [0.1, 0.2]
        fake_fa = MagicMock()
        fake_fa.get.return_value = [fake_face]
        with (
            patch.object(vision_mod, "_VISION_OK", True),
            patch.object(vision_mod, "_face_cache", {"u1": ([0.9, 0.9], "Alice")}),
            patch.object(vision_mod, "_get_face_app", return_value=fake_fa),
            patch.object(vision_mod, "_np_v"),
            patch.object(vision_mod, "_cv2") as mock_cv2,
            patch.object(vision_mod, "best_match", return_value=("u1", 0.0, ("Alice",))),
        ):
            mock_cv2.imdecode.return_value = MagicMock()
            results = vision_mod._identify_faces_in_image(b"data")
        assert results == [{"detected_user_id": None, "name": "unknown", "confidence": 0.0}]


class TestRefreshFaceCache:
    def test_replaces_cache_contents(self):
        with (
            patch.object(vision_mod, "_face_cache", {"stale": ([0.0], "Old")}),
            patch.object(vision_mod, "_db_get_all_face_embeddings", new=AsyncMock(return_value={"u1": ([0.1], "Alice")})),
        ):
            asyncio.run(vision_mod._refresh_face_cache())
            assert vision_mod._face_cache == {"u1": ([0.1], "Alice")}


class TestGetHaCameraSnapshot:
    def test_success(self):
        resp = MagicMock(status_code=200, content=b"jpeg-bytes")
        with patch("httpx.AsyncClient", return_value=_mock_async_client(get=resp)):
            result = asyncio.run(vision_mod._get_ha_camera_snapshot("http://ha.local", "tok", "camera.front_door"))
        assert result == b"jpeg-bytes"

    def test_non_200_returns_none(self):
        resp = MagicMock(status_code=404, content=b"")
        with patch("httpx.AsyncClient", return_value=_mock_async_client(get=resp)):
            result = asyncio.run(vision_mod._get_ha_camera_snapshot("http://ha.local", "tok", "camera.front_door"))
        assert result is None

    def test_exception_returns_none(self):
        with patch("httpx.AsyncClient", return_value=_mock_async_client(get=AsyncMock(side_effect=Exception("timeout")))):
            result = asyncio.run(vision_mod._get_ha_camera_snapshot("http://ha.local", "tok", "camera.front_door"))
        assert result is None


class TestCaptureRtspFrame:
    def test_returns_none_when_unavailable(self):
        with patch.object(vision_mod, "_VISION_OK", False):
            assert vision_mod._capture_rtsp_frame("rtsp://x") is None

    def test_returns_none_when_read_fails(self):
        fake_cap = MagicMock()
        fake_cap.read.return_value = (False, None)
        with (
            patch.object(vision_mod, "_VISION_OK", True),
            patch.object(vision_mod, "_cv2") as mock_cv2,
        ):
            mock_cv2.VideoCapture.return_value = fake_cap
            assert vision_mod._capture_rtsp_frame("rtsp://x") is None
        fake_cap.release.assert_called_once()

    def test_returns_jpeg_bytes_on_success(self):
        fake_cap = MagicMock()
        fake_cap.read.return_value = (True, MagicMock())
        fake_buf = MagicMock()
        fake_buf.tobytes.return_value = b"jpeg-bytes"
        with (
            patch.object(vision_mod, "_VISION_OK", True),
            patch.object(vision_mod, "_cv2") as mock_cv2,
        ):
            mock_cv2.VideoCapture.return_value = fake_cap
            mock_cv2.imencode.return_value = (True, fake_buf)
            result = vision_mod._capture_rtsp_frame("rtsp://x")
        assert result == b"jpeg-bytes"
        fake_cap.release.assert_called_once()


class TestExecuteVisionTool:
    def test_who_is_home_unavailable(self):
        with patch.object(vision_mod, "_VISION_OK", False):
            result = asyncio.run(vision_mod._execute_vision_tool("get_who_is_home", {}))
        assert "not available" in result

    def test_who_is_home_no_members(self):
        with (
            patch.object(vision_mod, "_VISION_OK", True),
            patch.object(vision_mod, "_db_get_who_is_home", new=AsyncMock(return_value=[])),
        ):
            result = asyncio.run(vision_mod._execute_vision_tool("get_who_is_home", {}))
        assert "No one detected" in result

    def test_who_is_home_formats_members(self):
        members = [{"name": "Alice", "activity": "cooking", "room": "Kitchen", "last_seen_at": "2026-07-01T09:00:00"}]
        with (
            patch.object(vision_mod, "_VISION_OK", True),
            patch.object(vision_mod, "_db_get_who_is_home", new=AsyncMock(return_value=members)),
        ):
            result = asyncio.run(vision_mod._execute_vision_tool("get_who_is_home", {}))
        assert "Alice — cooking in Kitchen" in result

    def test_security_events_no_user(self):
        result = asyncio.run(vision_mod._execute_vision_tool("get_security_events", {}))
        assert "No user context" in result

    def test_security_events_none_found(self):
        with patch.object(vision_mod, "_db_get_recent_security_events", new=AsyncMock(return_value=[])):
            result = asyncio.run(vision_mod._execute_vision_tool("get_security_events", {}, "u1"))
        assert "No security events" in result

    def test_security_events_formats_results(self):
        events = [{"detected_at": "2026-07-01T09:00:00", "event_type": "unknown_person", "room": "Front Door"}]
        with patch.object(vision_mod, "_db_get_recent_security_events", new=AsyncMock(return_value=events)):
            result = asyncio.run(vision_mod._execute_vision_tool("get_security_events", {"hours": 12}, "u1"))
        assert "unknown_person (Front Door)" in result

    def test_manage_camera_no_user(self):
        result = asyncio.run(vision_mod._execute_vision_tool("manage_camera", {"action": "list"}))
        assert "No user context" in result

    def test_manage_camera_list_empty(self):
        with patch.object(vision_mod, "_db_list_cameras", new=AsyncMock(return_value=[])):
            result = asyncio.run(vision_mod._execute_vision_tool("manage_camera", {"action": "list"}, "u1"))
        assert "No cameras configured" in result

    def test_manage_camera_list_formats(self):
        cams = [{"id": 1, "name": "Front Door", "source_type": "rtsp", "source": "rtsp://x", "room": "Entry", "enabled": True, "privacy": False}]
        with patch.object(vision_mod, "_db_list_cameras", new=AsyncMock(return_value=cams)):
            result = asyncio.run(vision_mod._execute_vision_tool("manage_camera", {"action": "list"}, "u1"))
        assert "Front Door" in result

    def test_manage_camera_add_missing_fields(self):
        result = asyncio.run(vision_mod._execute_vision_tool("manage_camera", {"action": "add", "name": ""}, "u1"))
        assert "Provide 'name' and 'source'" in result

    def test_manage_camera_add_success(self):
        with patch.object(vision_mod, "_db_add_camera", new=AsyncMock(return_value=5)):
            result = asyncio.run(vision_mod._execute_vision_tool("manage_camera", {"action": "add", "name": "Front Door", "source": "rtsp://x"}, "u1"))
        assert "id=5" in result

    def test_manage_camera_action_missing_camera_id(self):
        result = asyncio.run(vision_mod._execute_vision_tool("manage_camera", {"action": "remove"}, "u1"))
        assert "Provide 'camera_id'" in result

    def test_manage_camera_remove_success(self):
        with patch.object(vision_mod, "_db_delete_camera", new=AsyncMock(return_value=True)):
            result = asyncio.run(vision_mod._execute_vision_tool("manage_camera", {"action": "remove", "camera_id": 1}, "u1"))
        assert result == "Camera removed."

    def test_manage_camera_remove_not_found(self):
        with patch.object(vision_mod, "_db_delete_camera", new=AsyncMock(return_value=False)):
            result = asyncio.run(vision_mod._execute_vision_tool("manage_camera", {"action": "remove", "camera_id": 1}, "u1"))
        assert result == "Camera not found."

    def test_manage_camera_enable_flag(self):
        with patch.object(vision_mod, "_db_update_camera", new=AsyncMock(return_value=True)) as mock_update:
            result = asyncio.run(vision_mod._execute_vision_tool("manage_camera", {"action": "enable", "camera_id": 1}, "u1"))
        assert result == "Camera updated."
        mock_update.assert_awaited_once_with("u1", 1, enabled=True)

    def test_manage_camera_privacy_on_flag(self):
        with patch.object(vision_mod, "_db_update_camera", new=AsyncMock(return_value=True)) as mock_update:
            asyncio.run(vision_mod._execute_vision_tool("manage_camera", {"action": "privacy_on", "camera_id": 1}, "u1"))
        mock_update.assert_awaited_once_with("u1", 1, privacy=True)

    def test_manage_camera_unknown_action(self):
        result = asyncio.run(vision_mod._execute_vision_tool("manage_camera", {"action": "bogus", "camera_id": 1}, "u1"))
        assert "Unknown action" in result

    def test_unknown_tool(self):
        result = asyncio.run(vision_mod._execute_vision_tool("bogus_tool", {}, "u1"))
        assert "Unknown vision tool" in result

    def test_exception_wrapped(self):
        with patch.object(vision_mod, "_db_get_who_is_home", new=AsyncMock(side_effect=Exception("db down"))):
            result = asyncio.run(vision_mod._execute_vision_tool("get_who_is_home", {}))
        assert result == "Error: db down"


class TestVisionAppHelpers:
    def test_list_cameras(self):
        with patch.object(vision_mod, "_db_list_cameras", new=AsyncMock(return_value=[{"id": 1}])):
            result = asyncio.run(vision_mod._list_cameras("u1"))
        assert result == {"cameras": [{"id": 1}]}

    def test_add_camera_missing_fields_raises(self):
        try:
            asyncio.run(vision_mod._add_camera("u1", {"name": ""}))
            raise AssertionError("expected HTTPException")
        except HTTPException as e:
            assert e.status_code == 400

    def test_add_camera_invalid_source_type_raises(self):
        try:
            asyncio.run(vision_mod._add_camera("u1", {"name": "Front", "source": "x", "source_type": "bogus"}))
            raise AssertionError("expected HTTPException")
        except HTTPException as e:
            assert e.status_code == 400

    def test_add_camera_success(self):
        with patch.object(vision_mod, "_db_add_camera", new=AsyncMock(return_value=5)):
            result = asyncio.run(vision_mod._add_camera("u1", {"name": "Front", "source": "rtsp://x", "source_type": "rtsp"}))
        assert result == {"ok": True, "id": 5}

    def test_delete_camera_not_found_raises(self):
        with patch.object(vision_mod, "_db_delete_camera", new=AsyncMock(return_value=False)):
            try:
                asyncio.run(vision_mod._delete_camera(1, "u1"))
                raise AssertionError("expected HTTPException")
            except HTTPException as e:
                assert e.status_code == 404

    def test_delete_camera_success(self):
        with patch.object(vision_mod, "_db_delete_camera", new=AsyncMock(return_value=True)):
            result = asyncio.run(vision_mod._delete_camera(1, "u1"))
        assert result == {"ok": True}

    def test_update_camera_no_valid_fields_raises(self):
        try:
            asyncio.run(vision_mod._update_camera(1, {"bogus": "x"}, "u1"))
            raise AssertionError("expected HTTPException")
        except HTTPException as e:
            assert e.status_code == 400

    def test_update_camera_not_found_raises(self):
        with patch.object(vision_mod, "_db_update_camera", new=AsyncMock(return_value=False)):
            try:
                asyncio.run(vision_mod._update_camera(1, {"enabled": False}, "u1"))
                raise AssertionError("expected HTTPException")
            except HTTPException as e:
                assert e.status_code == 404

    def test_update_camera_success(self):
        with patch.object(vision_mod, "_db_update_camera", new=AsyncMock(return_value=True)):
            result = asyncio.run(vision_mod._update_camera(1, {"enabled": False}, "u1"))
        assert result == {"ok": True}

    def test_get_presence_members(self):
        with patch.object(vision_mod, "_db_get_who_is_home", new=AsyncMock(return_value=[{"user_id": "u1"}])):
            result = asyncio.run(vision_mod._get_presence_members())
        assert result == {"members": [{"user_id": "u1"}]}

    def test_get_security_events_helper(self):
        with patch.object(vision_mod, "_db_get_recent_security_events", new=AsyncMock(return_value=[])):
            result = asyncio.run(vision_mod._get_security_events("u1", 24.0))
        assert result == {"events": []}

    def test_face_enroll_sample_unavailable(self):
        with patch.object(vision_mod, "_VISION_OK", False):
            result = asyncio.run(vision_mod._face_enroll_sample(b"data"))
        assert result["ok"] is False

    def test_face_enroll_sample_no_face_detected(self):
        with (
            patch.object(vision_mod, "_VISION_OK", True),
            patch.object(vision_mod, "_extract_face_embedding", return_value=None),
        ):
            result = asyncio.run(vision_mod._face_enroll_sample(b"data"))
        assert result == {"ok": False, "error": "No face detected in image."}

    def test_face_enroll_sample_success(self):
        with (
            patch.object(vision_mod, "_VISION_OK", True),
            patch.object(vision_mod, "_extract_face_embedding", return_value=[0.1, 0.2]),
        ):
            result = asyncio.run(vision_mod._face_enroll_sample(b"data"))
        assert result == {"ok": True, "embedding": [0.1, 0.2]}

    def test_face_enroll_finish_unavailable_raises(self):
        with patch.object(vision_mod, "_VISION_OK", False):
            try:
                asyncio.run(vision_mod._face_enroll_finish("u1", [[0.1]]))
                raise AssertionError("expected HTTPException")
            except HTTPException as e:
                assert e.status_code == 400

    def test_face_enroll_finish_no_samples_raises(self):
        with patch.object(vision_mod, "_VISION_OK", True):
            try:
                asyncio.run(vision_mod._face_enroll_finish("u1", []))
                raise AssertionError("expected HTTPException")
            except HTTPException as e:
                assert e.status_code == 400

    def test_face_enroll_finish_success(self):
        with (
            patch.object(vision_mod, "_VISION_OK", True),
            patch.object(vision_mod, "_db_save_face_embedding", new=AsyncMock()) as mock_save,
            patch.object(vision_mod, "_refresh_face_cache", new=AsyncMock()),
        ):
            result = asyncio.run(vision_mod._face_enroll_finish("u1", [[0.1, 0.2]]))
        assert result == {"ok": True}
        mock_save.assert_awaited_once()

    def test_face_enroll_delete(self):
        with (
            patch.object(vision_mod, "_db_clear_face_embedding", new=AsyncMock()) as mock_clear,
            patch.object(vision_mod, "_refresh_face_cache", new=AsyncMock()),
        ):
            result = asyncio.run(vision_mod._face_enroll_delete("u1"))
        assert result == {"ok": True}
        mock_clear.assert_awaited_once()


class TestVisionLoop:
    def _fake_sleep(self, stop_after):
        call_count = 0

        async def fake_sleep(secs):
            nonlocal call_count
            call_count += 1
            if call_count > stop_after:
                raise RuntimeError("stop-loop")

        return fake_sleep

    def test_known_person_arrives_home(self):
        cam_rows = [
            {"id": 1, "user_id": "u1", "name": "Front Door", "room": "Entry", "source_type": "ha", "source": "camera.front_door", "ha_url": "http://ha.local", "ha_token": "tok"}
        ]
        pool, conn = _mock_asyncpg_pool()
        conn.fetch = AsyncMock(side_effect=[cam_rows, []])
        conn.fetchrow = AsyncMock(return_value={"is_home": False})
        sio = MagicMock()
        sio.emit = AsyncMock()
        vision_mod.init(sio, lambda uid: ["sid1"])
        with (
            patch("integrations.vision.asyncio.sleep", new=self._fake_sleep(2)),
            patch.object(vision_mod, "_db_ready", return_value=True),
            patch.object(vision_mod, "_VISION_OK", True),
            patch.object(vision_mod, "_pool", return_value=pool),
            patch.object(vision_mod, "_db_get_all_face_embeddings", new=AsyncMock(return_value={})),
            patch.object(vision_mod, "_get_ha_camera_snapshot", new=AsyncMock(return_value=b"jpeg")),
            patch.object(vision_mod, "_identify_faces_in_image", return_value=[{"detected_user_id": "u2", "name": "Bob", "confidence": 0.9}]),
            patch.object(vision_mod, "_db_record_detection", new=AsyncMock()),
            patch.object(vision_mod, "_db_update_presence", new=AsyncMock()) as mock_presence,
            patch.object(vision_mod, "_db_get_who_is_home", new=AsyncMock(return_value=[])),
        ):
            try:
                asyncio.run(vision_mod._vision_loop())
                raise AssertionError("expected loop to stop")
            except RuntimeError:
                pass
        mock_presence.assert_awaited_once_with("u2", True)
        sio.emit.assert_awaited_once()
        assert sio.emit.call_args.args[0] == "presence_update"

    def test_unknown_person_triggers_alert_when_away(self):
        cam_rows = [{"id": 1, "user_id": "u1", "name": "Front Door", "room": "Entry", "source_type": "rtsp", "source": "rtsp://x", "ha_url": "", "ha_token": ""}]
        pool, conn = _mock_asyncpg_pool()
        conn.fetch = AsyncMock(side_effect=[cam_rows, []])
        conn.fetchval = AsyncMock(return_value=0)
        sio = MagicMock()
        sio.emit = AsyncMock()
        vision_mod.init(sio, lambda uid: ["sid1"])
        with (
            patch("integrations.vision.asyncio.sleep", new=self._fake_sleep(2)),
            patch.object(vision_mod, "_db_ready", return_value=True),
            patch.object(vision_mod, "_VISION_OK", True),
            patch.object(vision_mod, "_pool", return_value=pool),
            patch.object(vision_mod, "_db_get_all_face_embeddings", new=AsyncMock(return_value={})),
            patch.object(vision_mod, "_capture_rtsp_frame", return_value=b"jpeg"),
            patch.object(vision_mod, "_identify_faces_in_image", return_value=[{"detected_user_id": None, "name": "unknown", "confidence": 0.0}]),
            patch.object(vision_mod, "_db_record_detection", new=AsyncMock()),
            patch.object(vision_mod, "_db_record_security_event", new=AsyncMock()) as mock_security,
            patch.object(vision_mod, "_db_get_who_is_home", new=AsyncMock(return_value=[])),
        ):
            try:
                asyncio.run(vision_mod._vision_loop())
                raise AssertionError("expected loop to stop")
            except RuntimeError:
                pass
        mock_security.assert_awaited_once_with("u1", 1, "unknown_person", "Entry")
        sio.emit.assert_awaited_once()
        assert sio.emit.call_args.args[0] == "security_alert"

    def test_no_snapshot_and_no_detections_are_skipped(self):
        cam_rows = [
            {"id": 1, "user_id": "u1", "name": "Cam1", "room": "Entry", "source_type": "ha", "source": "camera.front", "ha_url": "http://ha.local", "ha_token": "tok"},
            {"id": 2, "user_id": "u1", "name": "Cam2", "room": "Entry", "source_type": "ha", "source": "camera.back", "ha_url": "http://ha.local", "ha_token": "tok"},
        ]
        pool, conn = _mock_asyncpg_pool()
        conn.fetch = AsyncMock(side_effect=[cam_rows, []])
        sio = MagicMock()
        sio.emit = AsyncMock()
        vision_mod.init(sio, lambda uid: ["sid1"])
        with (
            patch("integrations.vision.asyncio.sleep", new=self._fake_sleep(2)),
            patch.object(vision_mod, "_db_ready", return_value=True),
            patch.object(vision_mod, "_VISION_OK", True),
            patch.object(vision_mod, "_pool", return_value=pool),
            patch.object(vision_mod, "_db_get_all_face_embeddings", new=AsyncMock(return_value={})),
            patch.object(vision_mod, "_get_ha_camera_snapshot", new=AsyncMock(side_effect=[None, b"jpeg"])),
            patch.object(vision_mod, "_identify_faces_in_image", return_value=[]),
            patch.object(vision_mod, "_db_get_who_is_home", new=AsyncMock(return_value=[])),
        ):
            try:
                asyncio.run(vision_mod._vision_loop())
                raise AssertionError("expected loop to stop")
            except RuntimeError:
                pass
        sio.emit.assert_not_awaited()

    def test_marks_stale_users_away(self):
        pool, conn = _mock_asyncpg_pool()
        conn.fetch = AsyncMock(side_effect=[[], [{"user_id": "u3"}]])
        sio = MagicMock()
        sio.emit = AsyncMock()
        vision_mod.init(sio, lambda uid: ["sid1"])
        with (
            patch("integrations.vision.asyncio.sleep", new=self._fake_sleep(2)),
            patch.object(vision_mod, "_db_ready", return_value=True),
            patch.object(vision_mod, "_VISION_OK", True),
            patch.object(vision_mod, "_pool", return_value=pool),
            patch.object(vision_mod, "_db_get_all_face_embeddings", new=AsyncMock(return_value={})),
            patch.object(vision_mod, "_db_update_presence", new=AsyncMock()) as mock_presence,
            patch.object(vision_mod, "_db_get_who_is_home", new=AsyncMock(return_value=[])),
        ):
            try:
                asyncio.run(vision_mod._vision_loop())
                raise AssertionError("expected loop to stop")
            except RuntimeError:
                pass
        mock_presence.assert_awaited_once_with("u3", False)
        sio.emit.assert_awaited_once()
        assert sio.emit.call_args.args[0] == "presence_update"

    def test_skips_when_not_ready(self):
        with (
            patch("integrations.vision.asyncio.sleep", new=self._fake_sleep(2)),
            patch.object(vision_mod, "_db_ready", return_value=False),
        ):
            vision_mod.init(MagicMock(), lambda uid: [])
            try:
                asyncio.run(vision_mod._vision_loop())
                raise AssertionError("expected loop to stop")
            except RuntimeError:
                pass

    def test_swallows_exceptions(self):
        pool, conn = _mock_asyncpg_pool()
        with (
            patch("integrations.vision.asyncio.sleep", new=self._fake_sleep(2)),
            patch.object(vision_mod, "_db_ready", return_value=True),
            patch.object(vision_mod, "_VISION_OK", True),
            patch.object(vision_mod, "_db_get_all_face_embeddings", new=AsyncMock(side_effect=Exception("db down"))),
        ):
            vision_mod.init(MagicMock(), lambda uid: [])
            try:
                asyncio.run(vision_mod._vision_loop())
                raise AssertionError("expected loop to stop")
            except RuntimeError:
                pass
