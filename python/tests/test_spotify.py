"""Tests for integrations/music/spotify.py — Spotify playback + OAuth."""

import asyncio, datetime, app as jarvis

from unittest.mock import AsyncMock, MagicMock, patch
from fastapi import HTTPException
from app import _spotify_configured
from integrations.music.spotify import _execute_spotify_tool, _get_spotify_tools
from integrations.music import spotify as spotify_mod
from helpers import _async_cm, _mock_async_client, _mock_asyncpg_pool


class TestSpotifyConfigured:
    def test_not_configured_when_token_empty(self):
        assert _spotify_configured({"spotify_refresh_token": ""}) is False

    def test_not_configured_when_key_missing(self):
        assert _spotify_configured({}) is False

    def test_configured_when_token_present(self):
        assert _spotify_configured({"spotify_refresh_token": "rtok"}) is True


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
