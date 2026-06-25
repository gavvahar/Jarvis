"""
Unit and HTTP-level tests for Jarvis.

Pure-function tests need no fixtures.
Webhook auth tests use the `api_client` fixture from conftest.py which
stubs out the database so no running PostgreSQL is required.
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import app as jarvis
from app import (
    _build_client,
    _build_system_prompt,
    _c_to_f,
    _get_myq_tools,
    _get_tesla_tools,
    _get_user_lock,
    _ha_configured,
    _ha_headers,
    _myq_configured,
    _myq_get_status,
    _myq_set_door,
    _sids_for_user,
    _split_sentences,
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

    def test_unknown_method_not_configured(self):
        assert _tesla_configured({"tesla_method": "unknown", "tesla_refresh_token": "tok", "tesla_fleet_refresh_token": "fleet"}) is False


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
