"""Tests for integrations/music/apple_music.py — Apple Music playback + MusicKit auth."""

import asyncio, app as jarvis

from unittest.mock import AsyncMock, MagicMock, patch
from integrations.music import apple_music as apple_music_mod
from integrations.music.apple_music import (
    _am_callbacks,
    _apple_music_configured,
    _apple_music_server_configured,
    _execute_apple_music_tool,
    _get_apple_music_tools,
    _require_runtime,
    _resolve_apple_music_callback,
)
from helpers import _async_cm, _mock_asyncpg_pool


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
