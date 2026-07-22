"""Tests for Doorbell/security webhook events (app.py)."""

import asyncio, datetime, app as jarvis, db as db_mod

from unittest.mock import AsyncMock, MagicMock, patch
from helpers import _mock_asyncpg_pool


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
