"""Tests for integrations/email_triage.py — email classification + urgent alerts."""

import asyncio, integrations.email_triage as email_triage_mod

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from integrations.email_triage import _classify_email, _execute_email_triage_tool, _get_email_triage_tools


class TestClassifyEmail:
    _msg = {"from": "billing@example.com", "subject": "Invoice #42"}

    def test_no_client_falls_back_to_subject(self):
        with patch("integrations.email_triage.build_llm_client", return_value=None):
            result = asyncio.run(_classify_email({"provider": "anthropic", "api_key": ""}, self._msg))
        assert result == {"summary": "Invoice #42", "important": False}

    def test_parses_anthropic_json_response(self):
        fake_client = MagicMock()
        fake_client.messages.create = AsyncMock(return_value=SimpleNamespace(content=[SimpleNamespace(text='{"summary": "Bill due Friday", "important": true}')]))
        with patch("integrations.email_triage.build_llm_client", return_value=fake_client):
            result = asyncio.run(_classify_email({"provider": "anthropic", "api_key": "x", "model": "claude-haiku-4-5"}, self._msg))
        assert result == {"summary": "Bill due Friday", "important": True}

    def test_parses_openai_json_response(self):
        fake_client = MagicMock()
        fake_client.chat.completions.create = AsyncMock(
            return_value=SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content='{"summary": "Weekly digest", "important": false}'))])
        )
        with patch("integrations.email_triage.build_llm_client", return_value=fake_client):
            result = asyncio.run(_classify_email({"provider": "openai", "api_key": "x", "model": "gpt-4o-mini"}, self._msg))
        assert result == {"summary": "Weekly digest", "important": False}

    def test_strips_markdown_code_fence(self):
        fake_client = MagicMock()
        fake_client.messages.create = AsyncMock(return_value=SimpleNamespace(content=[SimpleNamespace(text='```json\n{"summary": "ok", "important": false}\n```')]))
        with patch("integrations.email_triage.build_llm_client", return_value=fake_client):
            result = asyncio.run(_classify_email({"provider": "anthropic", "api_key": "x"}, self._msg))
        assert result == {"summary": "ok", "important": False}

    def test_malformed_json_falls_back(self):
        fake_client = MagicMock()
        fake_client.messages.create = AsyncMock(return_value=SimpleNamespace(content=[SimpleNamespace(text="not json")]))
        with patch("integrations.email_triage.build_llm_client", return_value=fake_client):
            result = asyncio.run(_classify_email({"provider": "anthropic", "api_key": "x"}, self._msg))
        assert result == {"summary": "Invoice #42", "important": False}


class TestExecuteEmailTriageTool:
    def test_no_messages(self):
        with patch("integrations.email_triage._db_list_email_triage", new=AsyncMock(return_value=[])):
            result = asyncio.run(_execute_email_triage_tool("u1", {}))
        assert "No triaged email" in result

    def test_formats_messages_with_urgent_marker(self):
        rows = [
            {"sender": "Mom", "subject": "Hi", "summary": "Says hi", "important": False},
            {"sender": "Bank", "subject": "Alert", "summary": "Suspicious login", "important": True},
        ]
        with patch("integrations.email_triage._db_list_email_triage", new=AsyncMock(return_value=rows)):
            result = asyncio.run(_execute_email_triage_tool("u1", {}))
        assert "Mom — Says hi" in result
        assert "⚠ Bank — Suspicious login" in result

    def test_important_only_filter(self):
        rows = [{"sender": "Mom", "subject": "Hi", "summary": "Says hi", "important": False}]
        with patch("integrations.email_triage._db_list_email_triage", new=AsyncMock(return_value=rows)):
            result = asyncio.run(_execute_email_triage_tool("u1", {"important_only": True}))
        assert "No urgent email" in result


class TestGetEmailTriageTools:
    def test_not_configured_returns_empty(self):
        assert _get_email_triage_tools({}, "anthropic") == []

    def test_configured_returns_tool(self):
        config = {"email_host": "imap.example.com", "email_username": "me", "email_password": "secret"}
        tools = _get_email_triage_tools(config, "anthropic")
        assert len(tools) == 1
        assert tools[0]["name"] == "get_email_summary"


class TestAlertUrgentEmail:
    def test_emits_socket_and_push(self):
        sio = MagicMock()
        sio.emit = AsyncMock()
        email_triage_mod.init(sio, lambda uid: ["sid1"])
        with patch.object(email_triage_mod, "_send_push", new=AsyncMock()) as mock_push:
            asyncio.run(
                email_triage_mod._alert_urgent_email(
                    "u1",
                    {"from": "Bank", "subject": "Alert"},
                    {"summary": "Suspicious login", "important": True},
                )
            )
        sio.emit.assert_awaited_once()
        assert sio.emit.call_args.args[0] == "email_alert"
        assert sio.emit.call_args.args[1]["summary"] == "Suspicious login"
        mock_push.assert_awaited_once()


class TestTriageNewMessages:
    def test_skips_already_classified(self):
        messages = [{"uid": "1", "from": "a", "subject": "s"}]
        with (
            patch.object(email_triage_mod, "_imap_fetch_unread", new=AsyncMock(return_value=messages)),
            patch.object(email_triage_mod, "_db_uids_already_classified", new=AsyncMock(return_value={"1"})),
            patch.object(email_triage_mod, "_classify_email", new=AsyncMock()) as mock_classify,
        ):
            asyncio.run(email_triage_mod._triage_new_messages("u1", {}))
        mock_classify.assert_not_awaited()

    def test_classifies_new_message_and_alerts_when_important(self):
        messages = [{"uid": "1", "from": "Bank", "subject": "Alert"}]
        with (
            patch.object(email_triage_mod, "_imap_fetch_unread", new=AsyncMock(return_value=messages)),
            patch.object(email_triage_mod, "_db_uids_already_classified", new=AsyncMock(return_value=set())),
            patch.object(email_triage_mod, "_classify_email", new=AsyncMock(return_value={"summary": "Suspicious login", "important": True})),
            patch.object(email_triage_mod, "_db_insert_email_triage", new=AsyncMock()) as mock_insert,
            patch.object(email_triage_mod, "_alert_urgent_email", new=AsyncMock()) as mock_alert,
        ):
            asyncio.run(email_triage_mod._triage_new_messages("u1", {}))
        mock_insert.assert_awaited_once_with("u1", "1", "Bank", "Alert", "Suspicious login", True)
        mock_alert.assert_awaited_once()

    def test_no_alert_when_not_important(self):
        messages = [{"uid": "1", "from": "Newsletter", "subject": "Digest"}]
        with (
            patch.object(email_triage_mod, "_imap_fetch_unread", new=AsyncMock(return_value=messages)),
            patch.object(email_triage_mod, "_db_uids_already_classified", new=AsyncMock(return_value=set())),
            patch.object(email_triage_mod, "_classify_email", new=AsyncMock(return_value={"summary": "Weekly digest", "important": False})),
            patch.object(email_triage_mod, "_db_insert_email_triage", new=AsyncMock()),
            patch.object(email_triage_mod, "_alert_urgent_email", new=AsyncMock()) as mock_alert,
        ):
            asyncio.run(email_triage_mod._triage_new_messages("u1", {}))
        mock_alert.assert_not_awaited()

    def test_fetch_failure_is_swallowed(self):
        with patch.object(email_triage_mod, "_imap_fetch_unread", new=AsyncMock(side_effect=ValueError("boom"))):
            asyncio.run(email_triage_mod._triage_new_messages("u1", {}))
