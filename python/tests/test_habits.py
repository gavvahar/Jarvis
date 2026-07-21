"""Tests for integrations/habits.py — habit learning + nudges."""

import asyncio, datetime, app as jarvis, db as db_mod, integrations.habits as habits_mod

from unittest.mock import AsyncMock, MagicMock, patch
from helpers import _mock_asyncpg_pool


class TestHabitsIntegration:
    def test_get_habits_tools_returns_tool_for_each_provider(self):
        assert {t["name"] for t in habits_mod._get_habits_tools("anthropic")} == {"get_habits"}
        assert {t["function"]["name"] for t in habits_mod._get_habits_tools("openai")} == {"get_habits"}

    def test_bucket_for_weekday(self):
        dt = datetime.datetime(2026, 7, 20, 8, 30, tzinfo=datetime.timezone.utc)
        assert habits_mod._bucket_for(dt) == "weekday"

    def test_bucket_for_weekend(self):
        dt = datetime.datetime(2026, 7, 18, 8, 30, tzinfo=datetime.timezone.utc)
        assert habits_mod._bucket_for(dt) == "weekend"

    def test_minutes_to_clock_am(self):
        assert habits_mod._minutes_to_clock(8 * 60 + 30) == "8:30 AM"

    def test_minutes_to_clock_pm(self):
        assert habits_mod._minutes_to_clock(18 * 60) == "6:00 PM"

    def test_minutes_to_clock_midnight(self):
        assert habits_mod._minutes_to_clock(0) == "12:00 AM"

    def test_minutes_to_clock_noon(self):
        assert habits_mod._minutes_to_clock(12 * 60) == "12:00 PM"

    def _local_dt(self, y, m, d, h, mi):
        return datetime.datetime(y, m, d, h, mi, tzinfo=datetime.datetime.now().astimezone().tzinfo)

    def test_analyze_habit_not_enough_samples(self):
        events = [self._local_dt(2026, 7, 20, 8, 30), self._local_dt(2026, 7, 21, 8, 30)]
        with patch.object(habits_mod, "_db_get_presence_events", new=AsyncMock(return_value=events)):
            result = asyncio.run(habits_mod._analyze_habit("u1", "departed"))
        assert result is None

    def test_analyze_habit_weekday_pattern(self):
        events = [self._local_dt(2026, 7, 20, 8, 30), self._local_dt(2026, 7, 21, 8, 32), self._local_dt(2026, 7, 22, 8, 28)]
        with patch.object(habits_mod, "_db_get_presence_events", new=AsyncMock(return_value=events)):
            result = asyncio.run(habits_mod._analyze_habit("u1", "departed"))
        assert result is not None
        assert "weekday" in result
        assert "weekend" not in result
        assert result["weekday"]["typical_minutes"] == 8 * 60 + 30
        assert result["weekday"]["sample_size"] == 3

    def test_analyze_habit_mixed_weekday_weekend(self):
        events = [
            self._local_dt(2026, 7, 20, 8, 30),
            self._local_dt(2026, 7, 21, 8, 30),
            self._local_dt(2026, 7, 22, 8, 30),
            self._local_dt(2026, 7, 18, 10, 0),
            self._local_dt(2026, 7, 19, 10, 0),
            self._local_dt(2026, 7, 25, 10, 0),
        ]
        with patch.object(habits_mod, "_db_get_presence_events", new=AsyncMock(return_value=events)):
            result = asyncio.run(habits_mod._analyze_habit("u1", "departed"))
        assert set(result) == {"weekday", "weekend"}

    def test_format_habit_line(self):
        habit = {"weekday": {"typical_minutes": 510, "sample_size": 5}}
        line = habits_mod._format_habit_line("departed", habit)
        assert "leave home" in line
        assert "8:30 AM" in line
        assert "weekdays" in line

    def test_execute_habits_tool_specific_event_type(self):
        with patch.object(habits_mod, "_analyze_habit", new=AsyncMock(return_value=None)):
            result = asyncio.run(habits_mod._execute_habits_tool("u1", {"event_type": "departed"}))
        assert "Not enough data" in result
        assert "leave home" in result

    def test_execute_habits_tool_both_when_no_event_type(self):
        with patch.object(habits_mod, "_analyze_habit", new=AsyncMock(return_value=None)):
            result = asyncio.run(habits_mod._execute_habits_tool("u1", {}))
        assert result.count("Not enough data") == 2

    def test_get_habit_prefs_helper(self):
        with (
            patch.object(habits_mod, "_db_get_habit_nudge_prefs", new=AsyncMock(return_value={"enabled": True})),
            patch.object(habits_mod, "_analyze_habit", new=AsyncMock(side_effect=[{"weekday": {"typical_minutes": 500, "sample_size": 3}}, None])),
        ):
            result = asyncio.run(habits_mod._get_habit_prefs("u1"))
        assert result["enabled"] is True
        assert result["departed"] is not None
        assert result["arrived"] is None

    def test_set_habit_prefs_helper(self):
        with patch.object(habits_mod, "_db_set_habit_nudges_enabled", new=AsyncMock()) as mock_set:
            result = asyncio.run(habits_mod._set_habit_prefs("u1", {"enabled": True}))
        mock_set.assert_awaited_once_with("u1", True)
        assert result == {"ok": True, "enabled": True}

    def test_maybe_nudge_no_habit_does_nothing(self):
        with patch.object(habits_mod, "_analyze_habit", new=AsyncMock(return_value=None)):
            asyncio.run(habits_mod._maybe_nudge("u1", datetime.date(2026, 7, 17)))

    def test_maybe_nudge_in_window_emits_and_marks_sent(self):
        now = datetime.datetime.now().astimezone()
        bucket = "weekend" if now.weekday() >= 5 else "weekday"
        now_minutes = now.hour * 60 + now.minute
        habit = {bucket: {"typical_minutes": now_minutes, "sample_size": 5}}
        sio = MagicMock()
        sio.emit = AsyncMock()
        habits_mod.init(sio, lambda uid: ["sid1"])
        with (
            patch.object(habits_mod, "_analyze_habit", new=AsyncMock(return_value=habit)),
            patch.object(habits_mod, "_db_has_presence_event_today", new=AsyncMock(return_value=False)),
            patch.object(habits_mod, "_send_push", new=AsyncMock()) as mock_push,
            patch.object(habits_mod, "_db_mark_habit_nudge_sent", new=AsyncMock()) as mock_mark,
        ):
            asyncio.run(habits_mod._maybe_nudge("u1", now.date()))
        sio.emit.assert_awaited_once()
        assert sio.emit.call_args.args[0] == "habit_nudge"
        mock_push.assert_awaited_once()
        mock_mark.assert_awaited_once_with("u1", now.date())

    def test_maybe_nudge_outside_window_skips(self):
        now = datetime.datetime.now().astimezone()
        bucket = "weekend" if now.weekday() >= 5 else "weekday"
        now_minutes = now.hour * 60 + now.minute
        far_minutes = (now_minutes + 120) % 1440
        habit = {bucket: {"typical_minutes": far_minutes, "sample_size": 5}}
        sio = MagicMock()
        sio.emit = AsyncMock()
        habits_mod.init(sio, lambda uid: ["sid1"])
        with (
            patch.object(habits_mod, "_analyze_habit", new=AsyncMock(return_value=habit)),
            patch.object(habits_mod, "_db_mark_habit_nudge_sent", new=AsyncMock()) as mock_mark,
        ):
            asyncio.run(habits_mod._maybe_nudge("u1", now.date()))
        sio.emit.assert_not_awaited()
        mock_mark.assert_not_awaited()

    def test_maybe_nudge_already_departed_today_skips(self):
        now = datetime.datetime.now().astimezone()
        bucket = "weekend" if now.weekday() >= 5 else "weekday"
        now_minutes = now.hour * 60 + now.minute
        habit = {bucket: {"typical_minutes": now_minutes, "sample_size": 5}}
        sio = MagicMock()
        sio.emit = AsyncMock()
        habits_mod.init(sio, lambda uid: ["sid1"])
        with (
            patch.object(habits_mod, "_analyze_habit", new=AsyncMock(return_value=habit)),
            patch.object(habits_mod, "_db_has_presence_event_today", new=AsyncMock(return_value=True)),
            patch.object(habits_mod, "_db_mark_habit_nudge_sent", new=AsyncMock()) as mock_mark,
        ):
            asyncio.run(habits_mod._maybe_nudge("u1", now.date()))
        sio.emit.assert_not_awaited()
        mock_mark.assert_not_awaited()

    def test_maybe_nudge_wrong_bucket_skips(self):
        now = datetime.datetime.now().astimezone()
        other_bucket = "weekday" if now.weekday() >= 5 else "weekend"
        habit = {other_bucket: {"typical_minutes": 0, "sample_size": 5}}
        sio = MagicMock()
        sio.emit = AsyncMock()
        habits_mod.init(sio, lambda uid: ["sid1"])
        with patch.object(habits_mod, "_analyze_habit", new=AsyncMock(return_value=habit)):
            asyncio.run(habits_mod._maybe_nudge("u1", now.date()))
        sio.emit.assert_not_awaited()

    def _fake_sleep(self, stop_after):
        call_count = 0

        async def fake_sleep(secs):
            nonlocal call_count
            call_count += 1
            if call_count > stop_after:
                raise RuntimeError("stop-loop")

        return fake_sleep

    def test_habit_nudge_loop_calls_maybe_nudge_for_due_users(self):
        with (
            patch("integrations.habits.asyncio.sleep", new=self._fake_sleep(1)),
            patch.object(habits_mod, "_db_ready", return_value=True),
            patch.object(habits_mod, "_db_list_users_for_habit_nudge", new=AsyncMock(return_value=["u1"])),
            patch.object(habits_mod, "_maybe_nudge", new=AsyncMock()) as mock_nudge,
        ):
            try:
                asyncio.run(habits_mod._habit_nudge_loop())
                raise AssertionError("expected loop to stop")
            except RuntimeError:
                pass
        mock_nudge.assert_awaited_once()
        assert mock_nudge.call_args.args[0] == "u1"

    def test_habit_nudge_loop_skips_when_not_ready(self):
        with (
            patch("integrations.habits.asyncio.sleep", new=self._fake_sleep(1)),
            patch.object(habits_mod, "_db_ready", return_value=False),
            patch.object(habits_mod, "_db_list_users_for_habit_nudge", new=AsyncMock()) as mock_list,
        ):
            try:
                asyncio.run(habits_mod._habit_nudge_loop())
                raise AssertionError("expected loop to stop")
            except RuntimeError:
                pass
        mock_list.assert_not_awaited()

    def test_habit_nudge_loop_swallows_exceptions(self):
        with (
            patch("integrations.habits.asyncio.sleep", new=self._fake_sleep(1)),
            patch.object(habits_mod, "_db_ready", return_value=True),
            patch.object(habits_mod, "_db_list_users_for_habit_nudge", new=AsyncMock(side_effect=Exception("db down"))),
        ):
            try:
                asyncio.run(habits_mod._habit_nudge_loop())
                raise AssertionError("expected loop to stop")
            except RuntimeError:
                pass


class TestApiHabits:
    def test_get_habits(self, api_client):
        prefs = {"enabled": True, "departed": None, "arrived": None}
        with patch.object(jarvis, "_get_habit_prefs", new=AsyncMock(return_value=prefs)):
            resp = api_client.get("/api/habits")
        assert resp.json() == prefs

    def test_set_habits(self, api_client):
        with patch.object(jarvis, "_set_habit_prefs", new=AsyncMock(return_value={"ok": True, "enabled": True})) as mock_set:
            resp = api_client.post("/api/habits", json={"enabled": True})
        assert resp.json() == {"ok": True, "enabled": True}
        mock_set.assert_awaited_once_with("local", {"enabled": True})


class TestDbHabits:
    def test_record_presence_event_defaults_to_now(self):
        pool, conn = _mock_asyncpg_pool()
        with patch("db._pool", return_value=pool):
            asyncio.run(db_mod._db_record_presence_event("u1", "arrived"))
        conn.execute.assert_awaited_once()
        assert conn.execute.call_args.args[1:] == ("u1", "arrived", None)

    def test_record_presence_event_explicit_timestamp(self):
        pool, conn = _mock_asyncpg_pool()
        ts = datetime.datetime(2026, 7, 17, 8, 30, tzinfo=datetime.timezone.utc)
        with patch("db._pool", return_value=pool):
            asyncio.run(db_mod._db_record_presence_event("u1", "departed", ts))
        assert conn.execute.call_args.args[1:] == ("u1", "departed", ts)

    def test_get_presence_events(self):
        ts = datetime.datetime(2026, 7, 17, 8, 30, tzinfo=datetime.timezone.utc)
        pool, conn = _mock_asyncpg_pool(fetch=[{"occurred_at": ts}])
        with patch("db._pool", return_value=pool):
            result = asyncio.run(db_mod._db_get_presence_events("u1", "departed"))
        assert result == [ts]

    def test_has_presence_event_today_true(self):
        pool, conn = _mock_asyncpg_pool(fetchval=True)
        with patch("db._pool", return_value=pool):
            result = asyncio.run(db_mod._db_has_presence_event_today("u1", "departed", datetime.date(2026, 7, 17)))
        assert result is True

    def test_has_presence_event_today_false(self):
        pool, conn = _mock_asyncpg_pool(fetchval=False)
        with patch("db._pool", return_value=pool):
            result = asyncio.run(db_mod._db_has_presence_event_today("u1", "departed", datetime.date(2026, 7, 17)))
        assert result is False

    def test_get_habit_nudge_prefs_missing_row(self):
        pool, conn = _mock_asyncpg_pool(fetchrow=None)
        with patch("db._pool", return_value=pool):
            result = asyncio.run(db_mod._db_get_habit_nudge_prefs("u1"))
        assert result == {"enabled": False}

    def test_get_habit_nudge_prefs_stored(self):
        pool, conn = _mock_asyncpg_pool(fetchrow={"habit_nudges_enabled": True})
        with patch("db._pool", return_value=pool):
            result = asyncio.run(db_mod._db_get_habit_nudge_prefs("u1"))
        assert result == {"enabled": True}

    def test_set_habit_nudges_enabled(self):
        pool, conn = _mock_asyncpg_pool()
        with patch("db._pool", return_value=pool):
            asyncio.run(db_mod._db_set_habit_nudges_enabled("u1", True))
        conn.execute.assert_awaited_once_with("UPDATE user_configs SET habit_nudges_enabled=$2 WHERE user_id=$1", "u1", True)

    def test_list_users_for_habit_nudge(self):
        pool, conn = _mock_asyncpg_pool(fetch=[{"user_id": "u1"}])
        with patch("db._pool", return_value=pool):
            result = asyncio.run(db_mod._db_list_users_for_habit_nudge(datetime.date(2026, 7, 17)))
        assert result == ["u1"]

    def test_mark_habit_nudge_sent(self):
        pool, conn = _mock_asyncpg_pool()
        today = datetime.date(2026, 7, 17)
        with patch("db._pool", return_value=pool):
            asyncio.run(db_mod._db_mark_habit_nudge_sent("u1", today))
        conn.execute.assert_awaited_once_with("UPDATE user_configs SET habit_nudge_last_sent=$2 WHERE user_id=$1", "u1", today)
