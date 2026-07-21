"""Tests for integrations/pim/timers.py — timers, reminders, news."""

import asyncio, datetime, db as db_mod

from unittest.mock import AsyncMock, MagicMock, patch
from integrations.pim.timers import _duration_str, _execute_news_tool, _execute_reminder_tool, _execute_timer_tool, _get_pim_tools
from helpers import _mock_async_client, _mock_asyncpg_pool


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
