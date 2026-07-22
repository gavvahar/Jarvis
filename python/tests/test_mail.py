"""Tests for integrations/pim/mail.py — IMAP email connection."""

import asyncio, email as email_pkg, integrations.pim.mail as mail_mod

from unittest.mock import AsyncMock, patch
from integrations.pim.mail import _decode_header_value, _email_configured, _execute_email_tool


class TestEmailConfigured:
    def test_all_fields_required(self):
        assert _email_configured({"email_host": "imap.example.com", "email_username": "me", "email_password": "secret"}) is True
        assert _email_configured({"email_host": "imap.example.com", "email_username": "me", "email_password": ""}) is False


class TestDecodeHeaderValue:
    def test_plain_ascii(self):
        assert _decode_header_value("Hello there") == "Hello there"

    def test_empty(self):
        assert _decode_header_value(None) == ""
        assert _decode_header_value("") == ""

    def test_encoded_word(self):
        assert _decode_header_value("=?utf-8?q?Caf=C3=A9?=") == "Café"


class TestExtractPlainText:
    def test_plain_text_message(self):
        msg = email_pkg.message_from_string("Subject: Hi\n\nHello world")
        assert mail_mod._extract_plain_text(msg).strip() == "Hello world"

    def test_multipart_prefers_plain_text(self):
        raw = 'Content-Type: multipart/alternative; boundary="b"\n\n--b\nContent-Type: text/plain\n\nPlain body\n--b\nContent-Type: text/html\n\n<p>HTML body</p>\n--b--\n'
        msg = email_pkg.message_from_string(raw)
        assert "Plain body" in mail_mod._extract_plain_text(msg)

    def test_multipart_falls_back_to_stripped_html(self):
        raw = 'Content-Type: multipart/alternative; boundary="b"\n\n--b\nContent-Type: text/html\n\n<p>Hello <b>there</b></p>\n--b--\n'
        msg = email_pkg.message_from_string(raw)
        text = mail_mod._extract_plain_text(msg)
        assert "Hello" in text and "there" in text
        assert "<p>" not in text


class TestExecuteEmailTool:
    _cfg = {
        "email_host": "imap.example.com",
        "email_username": "me@example.com",
        "email_password": "secret",
    }

    def test_not_configured(self):
        result = asyncio.run(_execute_email_tool({}, {}))
        assert "not configured" in result

    def test_no_unread(self):
        with patch("integrations.pim.mail._imap_fetch_unread", new=AsyncMock(return_value=[])):
            result = asyncio.run(_execute_email_tool(self._cfg, {}))
        assert "No unread email" in result

    def test_formats_messages(self):
        messages = [{"uid": "1", "from": "Mom <mom@example.com>", "subject": "Dinner?", "date": "Mon, 1 Jan 2026"}]
        with patch("integrations.pim.mail._imap_fetch_unread", new=AsyncMock(return_value=messages)):
            result = asyncio.run(_execute_email_tool(self._cfg, {}))
        assert "Mom <mom@example.com>" in result
        assert "Dinner?" in result

    def test_clamps_limit(self):
        fetch = AsyncMock(return_value=[])
        with patch("integrations.pim.mail._imap_fetch_unread", new=fetch):
            asyncio.run(_execute_email_tool(self._cfg, {"limit": 999}))
        fetch.assert_awaited_once_with(self._cfg, 25)

    def test_fetch_error_surfaced(self):
        with patch("integrations.pim.mail._imap_fetch_unread", new=AsyncMock(side_effect=ValueError("login failed"))):
            result = asyncio.run(_execute_email_tool(self._cfg, {}))
        assert "Could not read email" in result
