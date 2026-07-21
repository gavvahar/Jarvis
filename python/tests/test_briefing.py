"""Tests for integrations/briefing.py — daily briefing."""

import asyncio, datetime, app as jarvis, db as db_mod, integrations.briefing as briefing_mod

from unittest.mock import AsyncMock, MagicMock, patch
from fastapi import HTTPException
from helpers import _mock_asyncpg_pool


class TestBriefingIntegration:
    def test_get_briefing_tools_returns_tool_for_each_provider(self):
        assert {t["name"] for t in briefing_mod._get_briefing_tools("anthropic")} == {"manage_briefing"}
        assert {t["function"]["name"] for t in briefing_mod._get_briefing_tools("openai")} == {"manage_briefing"}

    def test_weather_line_empty_without_temp(self):
        with patch.object(briefing_mod, "_location_context", {}):
            assert briefing_mod._weather_line() == ""

    def test_weather_line_formats_available_data(self):
        ctx = {"temp_f": 72, "condition": "Clear", "city": "Springfield"}
        with patch.object(briefing_mod, "_location_context", ctx):
            line = briefing_mod._weather_line()
        assert "72" in line
        assert "Clear" in line
        assert "Springfield" in line

    def test_calendar_line_not_configured_returns_empty(self):
        result = asyncio.run(briefing_mod._calendar_line({}))
        assert result == ""

    def test_calendar_line_no_events(self):
        config = {"calendar_url": "u", "calendar_username": "n", "calendar_password": "p"}
        with patch.object(briefing_mod, "_calendar_events_between", new=AsyncMock(return_value=[])):
            result = asyncio.run(briefing_mod._calendar_line(config))
        assert "Nothing left" in result

    def test_calendar_line_swallows_errors(self):
        config = {"calendar_url": "u", "calendar_username": "n", "calendar_password": "p"}
        with patch.object(briefing_mod, "_calendar_events_between", new=AsyncMock(side_effect=Exception("dav down"))):
            result = asyncio.run(briefing_mod._calendar_line(config))
        assert result == ""

    def test_reminders_line_filters_to_today(self):
        now = datetime.datetime.now().astimezone()
        today_reminder = {"text": "Take out trash", "fire_at": now.replace(hour=20, minute=0)}
        tomorrow_reminder = {"text": "Dentist", "fire_at": now + datetime.timedelta(days=1)}
        with patch.object(briefing_mod, "_db_list_reminders", new=AsyncMock(return_value=[today_reminder, tomorrow_reminder])):
            result = asyncio.run(briefing_mod._reminders_line("u1"))
        assert "Take out trash" in result
        assert "Dentist" not in result

    def test_reminders_line_empty_when_none_today(self):
        with patch.object(briefing_mod, "_db_list_reminders", new=AsyncMock(return_value=[])):
            result = asyncio.run(briefing_mod._reminders_line("u1"))
        assert result == ""

    def test_news_line_formats_headlines(self):
        with patch.object(briefing_mod, "_fetch_news_headlines", new=AsyncMock(return_value=["Story One", "Story Two"])):
            result = asyncio.run(briefing_mod._news_line())
        assert "Story One" in result and "Story Two" in result

    def test_news_line_swallows_errors(self):
        with patch.object(briefing_mod, "_fetch_news_headlines", new=AsyncMock(side_effect=Exception("rss down"))):
            result = asyncio.run(briefing_mod._news_line())
        assert result == ""

    def test_compose_briefing_joins_available_parts(self):
        with (
            patch.object(briefing_mod, "_weather_line", return_value="Weather bit."),
            patch.object(briefing_mod, "_calendar_line", new=AsyncMock(return_value="Calendar bit.")),
            patch.object(briefing_mod, "_reminders_line", new=AsyncMock(return_value="")),
            patch.object(briefing_mod, "_news_line", new=AsyncMock(return_value="News bit.")),
        ):
            result = asyncio.run(briefing_mod._compose_briefing("u1", {}))
        assert result == "Weather bit. Calendar bit. News bit."

    def test_compose_briefing_fallback_when_all_empty(self):
        with (
            patch.object(briefing_mod, "_weather_line", return_value=""),
            patch.object(briefing_mod, "_calendar_line", new=AsyncMock(return_value="")),
            patch.object(briefing_mod, "_reminders_line", new=AsyncMock(return_value="")),
            patch.object(briefing_mod, "_news_line", new=AsyncMock(return_value="")),
        ):
            result = asyncio.run(briefing_mod._compose_briefing("u1", {}))
        assert result == "Nothing new to report, sir."

    def test_execute_briefing_tool_now_returns_composed_text(self):
        with patch.object(briefing_mod, "_compose_briefing", new=AsyncMock(return_value="Here's your day.")):
            result = asyncio.run(briefing_mod._execute_briefing_tool("u1", {"action": "now"}, {}))
        assert result == "Here's your day."

    def test_execute_briefing_tool_status(self):
        prefs = {"enabled": True, "morning_time": "07:00", "evening_time": "18:00"}
        with patch.object(briefing_mod, "_db_get_briefing_prefs", new=AsyncMock(return_value=prefs)):
            result = asyncio.run(briefing_mod._execute_briefing_tool("u1", {"action": "status"}, {}))
        assert "enabled" in result
        assert "07:00" in result and "18:00" in result

    def test_execute_briefing_tool_enable(self):
        prefs = {"enabled": False, "morning_time": "07:00", "evening_time": "18:00"}
        with (
            patch.object(briefing_mod, "_db_get_briefing_prefs", new=AsyncMock(return_value=prefs)),
            patch.object(briefing_mod, "_db_set_briefing_prefs", new=AsyncMock()) as mock_set,
        ):
            result = asyncio.run(briefing_mod._execute_briefing_tool("u1", {"action": "enable"}, {}))
        mock_set.assert_awaited_once_with("u1", True, "07:00", "18:00")
        assert "enabled" in result.lower()

    def test_execute_briefing_tool_disable(self):
        prefs = {"enabled": True, "morning_time": "07:00", "evening_time": "18:00"}
        with (
            patch.object(briefing_mod, "_db_get_briefing_prefs", new=AsyncMock(return_value=prefs)),
            patch.object(briefing_mod, "_db_set_briefing_prefs", new=AsyncMock()) as mock_set,
        ):
            result = asyncio.run(briefing_mod._execute_briefing_tool("u1", {"action": "disable"}, {}))
        mock_set.assert_awaited_once_with("u1", False, "07:00", "18:00")
        assert "disabled" in result.lower()

    def test_execute_briefing_tool_set_time_valid(self):
        prefs = {"enabled": True, "morning_time": "07:00", "evening_time": "18:00"}
        with (
            patch.object(briefing_mod, "_db_get_briefing_prefs", new=AsyncMock(return_value=prefs)),
            patch.object(briefing_mod, "_db_set_briefing_prefs", new=AsyncMock()) as mock_set,
        ):
            result = asyncio.run(briefing_mod._execute_briefing_tool("u1", {"action": "set_time", "slot": "morning", "time": "06:15"}, {}))
        mock_set.assert_awaited_once_with("u1", True, "06:15", "18:00")
        assert "06:15" in result

    def test_execute_briefing_tool_set_time_invalid_slot(self):
        prefs = {"enabled": True, "morning_time": "07:00", "evening_time": "18:00"}
        with patch.object(briefing_mod, "_db_get_briefing_prefs", new=AsyncMock(return_value=prefs)):
            result = asyncio.run(briefing_mod._execute_briefing_tool("u1", {"action": "set_time", "slot": "noon", "time": "12:00"}, {}))
        assert "morning" in result.lower() and "evening" in result.lower()

    def test_execute_briefing_tool_set_time_invalid_time(self):
        prefs = {"enabled": True, "morning_time": "07:00", "evening_time": "18:00"}
        with patch.object(briefing_mod, "_db_get_briefing_prefs", new=AsyncMock(return_value=prefs)):
            result = asyncio.run(briefing_mod._execute_briefing_tool("u1", {"action": "set_time", "slot": "morning", "time": "not-a-time"}, {}))
        assert "HH:MM" in result

    def test_execute_briefing_tool_unknown_action(self):
        prefs = {"enabled": True, "morning_time": "07:00", "evening_time": "18:00"}
        with patch.object(briefing_mod, "_db_get_briefing_prefs", new=AsyncMock(return_value=prefs)):
            result = asyncio.run(briefing_mod._execute_briefing_tool("u1", {"action": "bogus"}, {}))
        assert "Unknown action" in result

    def test_get_briefing_prefs_helper(self):
        prefs = {"enabled": True, "morning_time": "07:00", "evening_time": "18:00"}
        with patch.object(briefing_mod, "_db_get_briefing_prefs", new=AsyncMock(return_value=prefs)):
            result = asyncio.run(briefing_mod._get_briefing_prefs("u1"))
        assert result == prefs

    def test_set_briefing_prefs_helper_rejects_bad_time(self):
        try:
            asyncio.run(briefing_mod._set_briefing_prefs("u1", {"enabled": True, "morning_time": "7am", "evening_time": "18:00"}))
            raise AssertionError("expected HTTPException")
        except HTTPException as e:
            assert e.status_code == 400

    def test_set_briefing_prefs_helper_success(self):
        with patch.object(briefing_mod, "_db_set_briefing_prefs", new=AsyncMock()) as mock_set:
            result = asyncio.run(briefing_mod._set_briefing_prefs("u1", {"enabled": True, "morning_time": "06:45", "evening_time": "19:30"}))
        mock_set.assert_awaited_once_with("u1", True, "06:45", "19:30")
        assert result == {"ok": True, "enabled": True, "morning_time": "06:45", "evening_time": "19:30"}

    def test_deliver_briefing_emits_pushes_and_marks_sent(self):
        sio = MagicMock()
        sio.emit = AsyncMock()
        briefing_mod.init(sio, lambda uid: ["sid1"], {})
        today = datetime.date(2026, 7, 17)
        with (
            patch.object(briefing_mod, "_db_load_config", new=AsyncMock(return_value={})),
            patch.object(briefing_mod, "_compose_briefing", new=AsyncMock(return_value="Your summary.")),
            patch.object(briefing_mod, "_send_push", new=AsyncMock()) as mock_push,
            patch.object(briefing_mod, "_db_mark_briefing_sent", new=AsyncMock()) as mock_mark,
        ):
            asyncio.run(briefing_mod._deliver_briefing("u1", "morning", today))
        sio.emit.assert_awaited_once()
        assert sio.emit.call_args.args[0] == "briefing_ready"
        assert sio.emit.call_args.args[1]["text"] == "Your summary."
        mock_push.assert_awaited_once()
        mock_mark.assert_awaited_once_with("u1", "morning", today)

    def _fake_sleep(self, stop_after):
        call_count = 0

        async def fake_sleep(secs):
            nonlocal call_count
            call_count += 1
            if call_count > stop_after:
                raise RuntimeError("stop-loop")

        return fake_sleep

    def test_briefing_loop_delivers_due_users(self):
        with (
            patch("integrations.briefing.asyncio.sleep", new=self._fake_sleep(1)),
            patch.object(briefing_mod, "_db_ready", return_value=True),
            patch.object(briefing_mod, "_db_list_users_due_for_briefing", new=AsyncMock(side_effect=lambda slot, hhmm, today: ["u1"] if slot == "morning" else [])),
            patch.object(briefing_mod, "_deliver_briefing", new=AsyncMock()) as mock_deliver,
        ):
            try:
                asyncio.run(briefing_mod._briefing_loop())
                raise AssertionError("expected loop to stop")
            except RuntimeError:
                pass
        mock_deliver.assert_awaited_once()
        assert mock_deliver.call_args.args[0] == "u1"
        assert mock_deliver.call_args.args[1] == "morning"

    def test_briefing_loop_skips_when_not_ready(self):
        with (
            patch("integrations.briefing.asyncio.sleep", new=self._fake_sleep(1)),
            patch.object(briefing_mod, "_db_ready", return_value=False),
            patch.object(briefing_mod, "_db_list_users_due_for_briefing", new=AsyncMock()) as mock_list,
        ):
            try:
                asyncio.run(briefing_mod._briefing_loop())
                raise AssertionError("expected loop to stop")
            except RuntimeError:
                pass
        mock_list.assert_not_awaited()

    def test_briefing_loop_swallows_exceptions(self):
        with (
            patch("integrations.briefing.asyncio.sleep", new=self._fake_sleep(1)),
            patch.object(briefing_mod, "_db_ready", return_value=True),
            patch.object(briefing_mod, "_db_list_users_due_for_briefing", new=AsyncMock(side_effect=Exception("db down"))),
        ):
            try:
                asyncio.run(briefing_mod._briefing_loop())
                raise AssertionError("expected loop to stop")
            except RuntimeError:
                pass


class TestApiBriefing:
    def test_get_briefing_prefs(self, api_client):
        prefs = {"enabled": True, "morning_time": "07:00", "evening_time": "18:00"}
        with patch.object(jarvis, "_get_briefing_prefs", new=AsyncMock(return_value=prefs)):
            resp = api_client.get("/api/briefing")
        assert resp.json() == prefs

    def test_set_briefing_prefs(self, api_client):
        with patch.object(jarvis, "_set_briefing_prefs", new=AsyncMock(return_value={"ok": True})) as mock_set:
            resp = api_client.post("/api/briefing", json={"enabled": True, "morning_time": "06:00", "evening_time": "20:00"})
        assert resp.json() == {"ok": True}
        mock_set.assert_awaited_once_with("local", {"enabled": True, "morning_time": "06:00", "evening_time": "20:00"})


class TestDbBriefing:
    def test_get_briefing_prefs_missing_row_returns_defaults(self):
        pool, conn = _mock_asyncpg_pool(fetchrow=None)
        with patch("db._pool", return_value=pool):
            result = asyncio.run(db_mod._db_get_briefing_prefs("u1"))
        assert result == {"enabled": False, "morning_time": "07:00", "evening_time": "18:00"}

    def test_get_briefing_prefs_returns_stored_row(self):
        row = {"briefing_enabled": True, "briefing_morning_time": "06:30", "briefing_evening_time": "19:00"}
        pool, conn = _mock_asyncpg_pool(fetchrow=row)
        with patch("db._pool", return_value=pool):
            result = asyncio.run(db_mod._db_get_briefing_prefs("u1"))
        assert result == {"enabled": True, "morning_time": "06:30", "evening_time": "19:00"}

    def test_set_briefing_prefs_updates_row(self):
        pool, conn = _mock_asyncpg_pool()
        with patch("db._pool", return_value=pool):
            asyncio.run(db_mod._db_set_briefing_prefs("u1", True, "06:00", "20:00"))
        conn.execute.assert_awaited_once()
        assert conn.execute.call_args.args[1:] == ("u1", True, "06:00", "20:00")

    def test_list_users_due_for_briefing_morning(self):
        pool, conn = _mock_asyncpg_pool(fetch=[{"user_id": "u1"}, {"user_id": "u2"}])
        with patch("db._pool", return_value=pool):
            result = asyncio.run(db_mod._db_list_users_due_for_briefing("morning", "07:00", datetime.date(2026, 7, 17)))
        assert result == ["u1", "u2"]
        assert "briefing_morning_time" in conn.fetch.call_args.args[0]
        assert conn.fetch.call_args.args[1] == "07:00"

    def test_list_users_due_for_briefing_evening(self):
        pool, conn = _mock_asyncpg_pool(fetch=[])
        with patch("db._pool", return_value=pool):
            result = asyncio.run(db_mod._db_list_users_due_for_briefing("evening", "18:00", datetime.date(2026, 7, 17)))
        assert result == []
        assert "briefing_evening_time" in conn.fetch.call_args.args[0]

    def test_mark_briefing_sent(self):
        pool, conn = _mock_asyncpg_pool()
        today = datetime.date(2026, 7, 17)
        with patch("db._pool", return_value=pool):
            asyncio.run(db_mod._db_mark_briefing_sent("u1", "evening", today))
        conn.execute.assert_awaited_once()
        assert "briefing_last_evening_sent" in conn.execute.call_args.args[0]
        assert conn.execute.call_args.args[1:] == ("u1", today)
