"""Tests for integrations/myq.py — MyQ garage door."""

import asyncio, app as jarvis

from unittest.mock import AsyncMock, MagicMock, patch
from app import _myq_configured, _myq_get_status
from integrations.myq import _get_myq_tools, _myq_set_door
from helpers import _seed_user_state


def _make_myq_session_mock():
    session = MagicMock()
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=None)
    return session


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
