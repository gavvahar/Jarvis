"""Tests for Phone message triage (app.py webhook + classification)."""

import asyncio, datetime, app as jarvis, db as db_mod

from unittest.mock import AsyncMock, MagicMock, patch
from helpers import _mock_asyncpg_pool


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


class TestDbPhoneMessages:
    def test_store_phone_message(self):
        pool, conn = _mock_asyncpg_pool()
        with patch("db._pool", return_value=pool):
            asyncio.run(db_mod._db_store_phone_message("u1", "555-1234", "hi", False, ""))
        conn.execute.assert_awaited_once()
