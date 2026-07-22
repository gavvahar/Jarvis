"""Tests for Meetings (app.py) + integrations/meeting_prep.py."""

import asyncio, datetime, app as jarvis, db as db_mod, integrations.meeting_prep as meeting_prep_mod

from unittest.mock import AsyncMock, MagicMock, patch
from fastapi import HTTPException
from helpers import _mock_asyncpg_pool


class TestMeetingPrepIntegration:
    def test_get_meeting_prep_tools_empty_when_not_configured(self):
        assert meeting_prep_mod._get_meeting_prep_tools({}, "anthropic") == []

    def test_get_meeting_prep_tools_returns_both_tools_when_configured(self):
        config = {"calendar_url": "u", "calendar_username": "n", "calendar_password": "p"}
        names = {t["name"] for t in meeting_prep_mod._get_meeting_prep_tools(config, "anthropic")}
        assert names == {"manage_meeting_prep", "get_meeting_prep"}
        openai_names = {t["function"]["name"] for t in meeting_prep_mod._get_meeting_prep_tools(config, "openai")}
        assert openai_names == {"manage_meeting_prep", "get_meeting_prep"}

    def test_keywords_from_title_drops_short_words_and_stopwords(self):
        keywords = meeting_prep_mod._keywords_from_title("Weekly Budget Sync with Finance")
        assert "budget" in keywords
        assert "finance" in keywords
        assert "weekly" not in keywords
        assert "with" not in keywords

    def test_extract_summary_pulls_summary_section(self):
        notes = "## Summary\nDiscussed Q3 budget and hiring plan.\n\n## Key Decisions\n- Approved hire"
        assert meeting_prep_mod._extract_summary(notes) == "Discussed Q3 budget and hiring plan."

    def test_extract_summary_falls_back_to_whole_notes(self):
        assert meeting_prep_mod._extract_summary("No speech was detected during this meeting.") == "No speech was detected during this meeting."

    def test_prior_notes_line_empty_without_keywords(self):
        result = asyncio.run(meeting_prep_mod._prior_notes_line("u1", "a"))
        assert result == ""

    def test_prior_notes_line_empty_without_matches(self):
        with patch.object(meeting_prep_mod, "_db_search_past_meetings", new=AsyncMock(return_value=[])):
            result = asyncio.run(meeting_prep_mod._prior_notes_line("u1", "Budget Sync"))
        assert result == ""

    def test_prior_notes_line_formats_matches(self):
        past = [{"started_at": datetime.datetime(2026, 6, 1, tzinfo=datetime.timezone.utc), "notes": "## Summary\nApproved the Q2 budget."}]
        with patch.object(meeting_prep_mod, "_db_search_past_meetings", new=AsyncMock(return_value=past)):
            result = asyncio.run(meeting_prep_mod._prior_notes_line("u1", "Budget Sync"))
        assert "prior related meeting" in result
        assert "Approved the Q2 budget." in result

    def test_compose_meeting_prep_includes_all_parts(self):
        event = {
            "title": "Budget Sync",
            "start": datetime.datetime(2026, 7, 20, 15, 0, tzinfo=datetime.timezone.utc),
            "location": "Room 4",
            "description": "Review Q3 numbers",
            "attendees": ["Jane Doe", "John Smith"],
        }
        with patch.object(meeting_prep_mod, "_prior_notes_line", new=AsyncMock(return_value="")):
            result = asyncio.run(meeting_prep_mod._compose_meeting_prep("u1", event))
        assert "Budget Sync" in result
        assert "Room 4" in result
        assert "Review Q3 numbers" in result
        assert "Jane Doe" in result and "John Smith" in result

    def test_compose_meeting_prep_no_agenda(self):
        event = {"title": "Standup", "start": None, "location": "", "description": "", "attendees": []}
        with patch.object(meeting_prep_mod, "_prior_notes_line", new=AsyncMock(return_value="")):
            result = asyncio.run(meeting_prep_mod._compose_meeting_prep("u1", event))
        assert "No agenda provided" in result

    def test_next_upcoming_event_not_configured_returns_none(self):
        assert asyncio.run(meeting_prep_mod._next_upcoming_event({})) is None

    def test_next_upcoming_event_returns_first_match(self):
        config = {"calendar_url": "u", "calendar_username": "n", "calendar_password": "p"}
        event = {"title": "Standup"}
        with patch.object(meeting_prep_mod, "_calendar_events_between", new=AsyncMock(return_value=[event])):
            result = asyncio.run(meeting_prep_mod._next_upcoming_event(config))
        assert result == event

    def test_next_upcoming_event_swallows_errors(self):
        config = {"calendar_url": "u", "calendar_username": "n", "calendar_password": "p"}
        with patch.object(meeting_prep_mod, "_calendar_events_between", new=AsyncMock(side_effect=Exception("dav down"))):
            result = asyncio.run(meeting_prep_mod._next_upcoming_event(config))
        assert result is None

    def test_execute_get_meeting_prep_not_configured(self):
        result = asyncio.run(meeting_prep_mod._execute_meeting_prep_tool("get_meeting_prep", "u1", {}, {}))
        assert "not configured" in result

    def test_execute_get_meeting_prep_no_upcoming_event(self):
        config = {"calendar_url": "u", "calendar_username": "n", "calendar_password": "p"}
        with patch.object(meeting_prep_mod, "_next_upcoming_event", new=AsyncMock(return_value=None)):
            result = asyncio.run(meeting_prep_mod._execute_meeting_prep_tool("get_meeting_prep", "u1", {}, config))
        assert "No upcoming meetings" in result

    def test_execute_get_meeting_prep_returns_composed_text(self):
        config = {"calendar_url": "u", "calendar_username": "n", "calendar_password": "p"}
        with (
            patch.object(meeting_prep_mod, "_next_upcoming_event", new=AsyncMock(return_value={"title": "Standup"})),
            patch.object(meeting_prep_mod, "_compose_meeting_prep", new=AsyncMock(return_value="Prep text.")),
        ):
            result = asyncio.run(meeting_prep_mod._execute_meeting_prep_tool("get_meeting_prep", "u1", {}, config))
        assert result == "Prep text."

    def test_execute_manage_meeting_prep_status(self):
        prefs = {"enabled": True, "lead_minutes": 15}
        with patch.object(meeting_prep_mod, "_db_get_meeting_prep_prefs", new=AsyncMock(return_value=prefs)):
            result = asyncio.run(meeting_prep_mod._execute_meeting_prep_tool("manage_meeting_prep", "u1", {"action": "status"}, {}))
        assert "enabled" in result and "15" in result

    def test_execute_manage_meeting_prep_enable(self):
        prefs = {"enabled": False, "lead_minutes": 15}
        with (
            patch.object(meeting_prep_mod, "_db_get_meeting_prep_prefs", new=AsyncMock(return_value=prefs)),
            patch.object(meeting_prep_mod, "_db_set_meeting_prep_prefs", new=AsyncMock()) as mock_set,
        ):
            result = asyncio.run(meeting_prep_mod._execute_meeting_prep_tool("manage_meeting_prep", "u1", {"action": "enable"}, {}))
        mock_set.assert_awaited_once_with("u1", True, 15)
        assert "enabled" in result.lower()

    def test_execute_manage_meeting_prep_disable(self):
        prefs = {"enabled": True, "lead_minutes": 15}
        with (
            patch.object(meeting_prep_mod, "_db_get_meeting_prep_prefs", new=AsyncMock(return_value=prefs)),
            patch.object(meeting_prep_mod, "_db_set_meeting_prep_prefs", new=AsyncMock()) as mock_set,
        ):
            result = asyncio.run(meeting_prep_mod._execute_meeting_prep_tool("manage_meeting_prep", "u1", {"action": "disable"}, {}))
        mock_set.assert_awaited_once_with("u1", False, 15)
        assert "disabled" in result.lower()

    def test_execute_manage_meeting_prep_set_lead_time_valid(self):
        prefs = {"enabled": True, "lead_minutes": 15}
        with (
            patch.object(meeting_prep_mod, "_db_get_meeting_prep_prefs", new=AsyncMock(return_value=prefs)),
            patch.object(meeting_prep_mod, "_db_set_meeting_prep_prefs", new=AsyncMock()) as mock_set,
        ):
            result = asyncio.run(meeting_prep_mod._execute_meeting_prep_tool("manage_meeting_prep", "u1", {"action": "set_lead_time", "lead_minutes": 30}, {}))
        mock_set.assert_awaited_once_with("u1", True, 30)
        assert "30" in result

    def test_execute_manage_meeting_prep_set_lead_time_invalid(self):
        prefs = {"enabled": True, "lead_minutes": 15}
        with patch.object(meeting_prep_mod, "_db_get_meeting_prep_prefs", new=AsyncMock(return_value=prefs)):
            result = asyncio.run(meeting_prep_mod._execute_meeting_prep_tool("manage_meeting_prep", "u1", {"action": "set_lead_time"}, {}))
        assert "positive number" in result

    def test_execute_manage_meeting_prep_unknown_action(self):
        prefs = {"enabled": True, "lead_minutes": 15}
        with patch.object(meeting_prep_mod, "_db_get_meeting_prep_prefs", new=AsyncMock(return_value=prefs)):
            result = asyncio.run(meeting_prep_mod._execute_meeting_prep_tool("manage_meeting_prep", "u1", {"action": "bogus"}, {}))
        assert "Unknown action" in result

    def test_get_meeting_prep_prefs_helper(self):
        prefs = {"enabled": True, "lead_minutes": 15}
        with patch.object(meeting_prep_mod, "_db_get_meeting_prep_prefs", new=AsyncMock(return_value=prefs)):
            result = asyncio.run(meeting_prep_mod._get_meeting_prep_prefs("u1"))
        assert result == prefs

    def test_set_meeting_prep_prefs_helper_rejects_bad_lead_minutes(self):
        try:
            asyncio.run(meeting_prep_mod._set_meeting_prep_prefs("u1", {"enabled": True, "lead_minutes": "soon"}))
            raise AssertionError("expected HTTPException")
        except HTTPException as e:
            assert e.status_code == 400

    def test_set_meeting_prep_prefs_helper_clamps_range(self):
        with patch.object(meeting_prep_mod, "_db_set_meeting_prep_prefs", new=AsyncMock()) as mock_set:
            result = asyncio.run(meeting_prep_mod._set_meeting_prep_prefs("u1", {"enabled": True, "lead_minutes": 999}))
        mock_set.assert_awaited_once_with("u1", True, 120)
        assert result == {"ok": True, "enabled": True, "lead_minutes": 120}

    def test_deliver_meeting_prep_emits_pushes_and_marks_sent(self):
        sio = MagicMock()
        sio.emit = AsyncMock()
        meeting_prep_mod.init(sio, lambda uid: ["sid1"])
        event = {"title": "Standup", "uid": "abc123"}
        with (
            patch.object(meeting_prep_mod, "_compose_meeting_prep", new=AsyncMock(return_value="Prep text.")),
            patch.object(meeting_prep_mod, "_send_push", new=AsyncMock()) as mock_push,
            patch.object(meeting_prep_mod, "_db_mark_meeting_prep_sent", new=AsyncMock()) as mock_mark,
        ):
            asyncio.run(meeting_prep_mod._deliver_meeting_prep("u1", event))
        sio.emit.assert_awaited_once()
        assert sio.emit.call_args.args[0] == "meeting_prep_ready"
        assert sio.emit.call_args.args[1]["text"] == "Prep text."
        mock_push.assert_awaited_once()
        mock_mark.assert_awaited_once_with("u1", "abc123")

    def test_check_user_meeting_prep_skips_events_without_uid(self):
        config = {"calendar_url": "u", "calendar_username": "n", "calendar_password": "p"}
        with (
            patch.object(meeting_prep_mod, "_calendar_events_between", new=AsyncMock(return_value=[{"title": "No UID", "uid": ""}])),
            patch.object(meeting_prep_mod, "_deliver_meeting_prep", new=AsyncMock()) as mock_deliver,
        ):
            asyncio.run(meeting_prep_mod._check_user_meeting_prep("u1", config, 15))
        mock_deliver.assert_not_awaited()

    def test_check_user_meeting_prep_skips_already_sent(self):
        config = {"calendar_url": "u", "calendar_username": "n", "calendar_password": "p"}
        event = {"title": "Standup", "uid": "abc123"}
        with (
            patch.object(meeting_prep_mod, "_calendar_events_between", new=AsyncMock(return_value=[event])),
            patch.object(meeting_prep_mod, "_db_meeting_prep_sent_uids", new=AsyncMock(return_value={"abc123"})),
            patch.object(meeting_prep_mod, "_deliver_meeting_prep", new=AsyncMock()) as mock_deliver,
        ):
            asyncio.run(meeting_prep_mod._check_user_meeting_prep("u1", config, 15))
        mock_deliver.assert_not_awaited()

    def test_check_user_meeting_prep_delivers_new_event(self):
        config = {"calendar_url": "u", "calendar_username": "n", "calendar_password": "p"}
        event = {"title": "Standup", "uid": "abc123"}
        with (
            patch.object(meeting_prep_mod, "_calendar_events_between", new=AsyncMock(return_value=[event])),
            patch.object(meeting_prep_mod, "_db_meeting_prep_sent_uids", new=AsyncMock(return_value=set())),
            patch.object(meeting_prep_mod, "_deliver_meeting_prep", new=AsyncMock()) as mock_deliver,
        ):
            asyncio.run(meeting_prep_mod._check_user_meeting_prep("u1", config, 15))
        mock_deliver.assert_awaited_once_with("u1", event)

    def test_check_user_meeting_prep_swallows_calendar_errors(self):
        config = {"calendar_url": "u", "calendar_username": "n", "calendar_password": "p"}
        with patch.object(meeting_prep_mod, "_calendar_events_between", new=AsyncMock(side_effect=Exception("dav down"))):
            asyncio.run(meeting_prep_mod._check_user_meeting_prep("u1", config, 15))

    def _fake_sleep(self, stop_after):
        call_count = 0

        async def fake_sleep(secs):
            nonlocal call_count
            call_count += 1
            if call_count > stop_after:
                raise RuntimeError("stop-loop")

        return fake_sleep

    def test_meeting_prep_loop_checks_configured_users(self):
        config = {"calendar_url": "u", "calendar_username": "n", "calendar_password": "p"}
        prefs = {"enabled": True, "lead_minutes": 15}
        with (
            patch("integrations.meeting_prep.asyncio.sleep", new=self._fake_sleep(1)),
            patch.object(meeting_prep_mod, "_db_ready", return_value=True),
            patch.object(meeting_prep_mod, "_db_list_users_for_meeting_prep", new=AsyncMock(return_value=["u1"])),
            patch.object(meeting_prep_mod, "_db_load_config", new=AsyncMock(return_value=config)),
            patch.object(meeting_prep_mod, "_db_get_meeting_prep_prefs", new=AsyncMock(return_value=prefs)),
            patch.object(meeting_prep_mod, "_check_user_meeting_prep", new=AsyncMock()) as mock_check,
        ):
            try:
                asyncio.run(meeting_prep_mod._meeting_prep_loop())
                raise AssertionError("expected loop to stop")
            except RuntimeError:
                pass
        mock_check.assert_awaited_once_with("u1", config, 15)

    def test_meeting_prep_loop_skips_unconfigured_users(self):
        with (
            patch("integrations.meeting_prep.asyncio.sleep", new=self._fake_sleep(1)),
            patch.object(meeting_prep_mod, "_db_ready", return_value=True),
            patch.object(meeting_prep_mod, "_db_list_users_for_meeting_prep", new=AsyncMock(return_value=["u1"])),
            patch.object(meeting_prep_mod, "_db_load_config", new=AsyncMock(return_value={})),
            patch.object(meeting_prep_mod, "_check_user_meeting_prep", new=AsyncMock()) as mock_check,
        ):
            try:
                asyncio.run(meeting_prep_mod._meeting_prep_loop())
                raise AssertionError("expected loop to stop")
            except RuntimeError:
                pass
        mock_check.assert_not_awaited()

    def test_meeting_prep_loop_skips_when_not_ready(self):
        with (
            patch("integrations.meeting_prep.asyncio.sleep", new=self._fake_sleep(1)),
            patch.object(meeting_prep_mod, "_db_ready", return_value=False),
            patch.object(meeting_prep_mod, "_db_list_users_for_meeting_prep", new=AsyncMock()) as mock_list,
        ):
            try:
                asyncio.run(meeting_prep_mod._meeting_prep_loop())
                raise AssertionError("expected loop to stop")
            except RuntimeError:
                pass
        mock_list.assert_not_awaited()

    def test_meeting_prep_loop_swallows_exceptions(self):
        with (
            patch("integrations.meeting_prep.asyncio.sleep", new=self._fake_sleep(1)),
            patch.object(meeting_prep_mod, "_db_ready", return_value=True),
            patch.object(meeting_prep_mod, "_db_list_users_for_meeting_prep", new=AsyncMock(side_effect=Exception("db down"))),
        ):
            try:
                asyncio.run(meeting_prep_mod._meeting_prep_loop())
                raise AssertionError("expected loop to stop")
            except RuntimeError:
                pass


class TestApiMeetingPrep:
    def test_get_meeting_prep_prefs(self, api_client):
        prefs = {"enabled": True, "lead_minutes": 15}
        with patch.object(jarvis, "_get_meeting_prep_prefs", new=AsyncMock(return_value=prefs)):
            resp = api_client.get("/api/meeting-prep")
        assert resp.json() == prefs

    def test_set_meeting_prep_prefs(self, api_client):
        with patch.object(jarvis, "_set_meeting_prep_prefs", new=AsyncMock(return_value={"ok": True})) as mock_set:
            resp = api_client.post("/api/meeting-prep", json={"enabled": True, "lead_minutes": 20})
        assert resp.json() == {"ok": True}
        mock_set.assert_awaited_once_with("local", {"enabled": True, "lead_minutes": 20})


class TestDbMeetingPrep:
    def test_get_meeting_prep_prefs_missing_row_returns_defaults(self):
        pool, conn = _mock_asyncpg_pool(fetchrow=None)
        with patch("db._pool", return_value=pool):
            result = asyncio.run(db_mod._db_get_meeting_prep_prefs("u1"))
        assert result == {"enabled": False, "lead_minutes": 15}

    def test_get_meeting_prep_prefs_returns_stored_row(self):
        row = {"meeting_prep_enabled": True, "meeting_prep_lead_minutes": 30}
        pool, conn = _mock_asyncpg_pool(fetchrow=row)
        with patch("db._pool", return_value=pool):
            result = asyncio.run(db_mod._db_get_meeting_prep_prefs("u1"))
        assert result == {"enabled": True, "lead_minutes": 30}

    def test_set_meeting_prep_prefs_updates_row(self):
        pool, conn = _mock_asyncpg_pool()
        with patch("db._pool", return_value=pool):
            asyncio.run(db_mod._db_set_meeting_prep_prefs("u1", True, 20))
        conn.execute.assert_awaited_once()
        assert conn.execute.call_args.args[1:] == ("u1", True, 20)

    def test_list_users_for_meeting_prep(self):
        pool, conn = _mock_asyncpg_pool(fetch=[{"user_id": "u1"}])
        with patch("db._pool", return_value=pool):
            result = asyncio.run(db_mod._db_list_users_for_meeting_prep())
        assert result == ["u1"]

    def test_meeting_prep_sent_uids_empty_input(self):
        result = asyncio.run(db_mod._db_meeting_prep_sent_uids("u1", []))
        assert result == set()

    def test_meeting_prep_sent_uids_returns_set(self):
        pool, conn = _mock_asyncpg_pool(fetch=[{"event_uid": "abc"}])
        with patch("db._pool", return_value=pool):
            result = asyncio.run(db_mod._db_meeting_prep_sent_uids("u1", ["abc", "xyz"]))
        assert result == {"abc"}

    def test_mark_meeting_prep_sent(self):
        pool, conn = _mock_asyncpg_pool()
        with patch("db._pool", return_value=pool):
            asyncio.run(db_mod._db_mark_meeting_prep_sent("u1", "abc"))
        conn.execute.assert_awaited_once_with("INSERT INTO meeting_prep_sent (user_id, event_uid) VALUES ($1, $2) ON CONFLICT (user_id, event_uid) DO NOTHING", "u1", "abc")


class TestDbMeetings:
    def test_create_meeting(self):
        pool, conn = _mock_asyncpg_pool(fetchrow={"id": 4})
        with patch("db._pool", return_value=pool):
            mid = asyncio.run(db_mod._db_create_meeting("u1"))
        assert mid == 4

    def test_append_transcript_segment(self):
        pool, conn = _mock_asyncpg_pool()
        with patch("db._pool", return_value=pool):
            asyncio.run(db_mod._db_append_transcript_segment(4, "hello"))
        conn.execute.assert_awaited_once()

    def test_finalize_meeting(self):
        pool, conn = _mock_asyncpg_pool()
        with patch("db._pool", return_value=pool):
            asyncio.run(db_mod._db_finalize_meeting(4, "notes"))
        conn.execute.assert_awaited_once()

    def test_search_past_meetings_empty_keywords(self):
        result = asyncio.run(db_mod._db_search_past_meetings("u1", []))
        assert result == []

    def test_search_past_meetings_returns_rows(self):
        rows = [{"id": 1, "started_at": datetime.datetime(2026, 6, 1), "notes": "## Summary\nDiscussed budget."}]
        pool, conn = _mock_asyncpg_pool(fetch=rows)
        with patch("db._pool", return_value=pool):
            result = asyncio.run(db_mod._db_search_past_meetings("u1", ["budget"], limit=2))
        assert result == rows
        assert conn.fetch.call_args.args[1:] == ("u1", ["%budget%"], 2)


class TestApiMeetings:
    def test_list_meetings(self, api_client):
        pool, conn = _mock_asyncpg_pool(fetch=[{"id": 1, "started_at": datetime.datetime(2026, 7, 1, 9, 0), "ended_at": None, "notes": None}])
        with patch.object(jarvis, "_pool", return_value=pool):
            resp = api_client.get("/api/meetings")
        assert resp.json() == [{"id": 1, "started_at": "2026-07-01T09:00:00", "ended_at": None, "notes": None}]

    def test_meeting_detail_not_found(self, api_client):
        pool, conn = _mock_asyncpg_pool(fetchrow=None)
        with patch.object(jarvis, "_pool", return_value=pool):
            resp = api_client.get("/api/meetings/1")
        assert resp.status_code == 404

    def test_meeting_detail_found(self, api_client):
        row = {"id": 1, "started_at": datetime.datetime(2026, 7, 1, 9, 0), "ended_at": datetime.datetime(2026, 7, 1, 9, 30), "transcript": "hi", "notes": "notes"}
        pool, conn = _mock_asyncpg_pool(fetchrow=row)
        with patch.object(jarvis, "_pool", return_value=pool):
            resp = api_client.get("/api/meetings/1")
        data = resp.json()
        assert data["transcript"] == "hi"
        assert data["started_at"] == "2026-07-01T09:00:00"
