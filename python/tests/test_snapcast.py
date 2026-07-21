"""Tests for integrations/multiroom/snapcast.py — multi-room audio."""

import asyncio, app as jarvis

from unittest.mock import AsyncMock, MagicMock, patch
from integrations.multiroom.snapcast import _execute_snapcast_tool, _get_snapcast_tools, _snapcast_get_status
from helpers import _mock_async_client


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
