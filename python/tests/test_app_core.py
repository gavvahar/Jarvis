"""Tests for app.py — core routes and helpers not owned by a single integration."""

import asyncio, app as jarvis

from unittest.mock import AsyncMock, MagicMock, patch
from app import _get_user_lock, _sids_for_user, _split_sentences, _user_configured
from helpers import _mock_async_client, _mock_asyncpg_pool, _seed_user_state


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


class TestUserConfigured:
    def test_with_client(self):
        assert _user_configured({"client": object()}) is True

    def test_with_none_client(self):
        assert _user_configured({"client": None}) is False


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
        with (
            patch.object(jarvis, "_user_has_face_enrollment", return_value=True),
            patch.object(
                jarvis,
                "_db_get_tts_prefs",
                new=AsyncMock(return_value={"rate": 1.0, "pitch": 1.0, "volume": 1.0}),
            ),
        ):
            resp = api_client.get("/api/status")
        assert resp.status_code == 200
        data = resp.json()
        assert data["provider"] == "anthropic"
        assert data["model"] == "claude-haiku-4-5"
        assert data["role"] == "user"
        assert data["user_id"] == "local"
        assert data["face_enrolled"] is True
        assert data["tts_rate"] == 1.0
        assert data["tts_pitch"] == 1.0
        assert data["tts_volume"] == 1.0


class TestTtsPrefs:
    def test_get_returns_db_values(self, api_client):
        _seed_user_state()
        with patch.object(
            jarvis,
            "_db_get_tts_prefs",
            new=AsyncMock(return_value={"rate": 0.8, "pitch": 1.0, "volume": 1.0}),
        ):
            resp = api_client.get("/api/tts-prefs")
        assert resp.status_code == 200
        assert resp.json() == {"rate": 0.8, "pitch": 1.0, "volume": 1.0}

    def test_set_clamps_out_of_range_values(self, api_client):
        _seed_user_state()
        with patch.object(jarvis, "_db_set_tts_prefs", new=AsyncMock()) as mock_set:
            resp = api_client.post(
                "/api/tts-prefs", json={"rate": 99, "pitch": -5, "volume": 3}
            )
        assert resp.status_code == 200
        data = resp.json()
        assert data == {"ok": True, "rate": 2.0, "pitch": 0.5, "volume": 1.0}
        mock_set.assert_awaited_once_with("local", 2.0, 0.5, 1.0)

    def test_set_rejects_non_numeric(self, api_client):
        _seed_user_state()
        resp = api_client.post("/api/tts-prefs", json={"rate": "loud"})
        assert resp.status_code == 400


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
