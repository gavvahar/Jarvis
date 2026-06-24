"""
Unit and HTTP-level tests for Jarvis.

Pure-function tests need no fixtures.
Webhook auth tests use the `api_client` fixture from conftest.py which
stubs out the database so no running PostgreSQL is required.
"""

from unittest.mock import AsyncMock, patch

import app as jarvis
from app import _ha_configured, _split_sentences, _user_configured

# ── Pure function tests ────────────────────────────────────────────────────────


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


class TestHaConfigured:
    def test_both_present(self):
        assert _ha_configured({"ha_url": "http://ha.local", "ha_token": "tok"}) is True

    def test_empty_url(self):
        assert _ha_configured({"ha_url": "", "ha_token": "tok"}) is False

    def test_empty_token(self):
        assert _ha_configured({"ha_url": "http://ha.local", "ha_token": ""}) is False

    def test_both_missing(self):
        assert _ha_configured({}) is False

    def test_none_values(self):
        assert _ha_configured({"ha_url": None, "ha_token": None}) is False


class TestUserConfigured:
    def test_with_client(self):
        assert _user_configured({"client": object()}) is True

    def test_with_none_client(self):
        assert _user_configured({"client": None}) is False


# ── Webhook auth tests ─────────────────────────────────────────────────────────


class TestMessagesIngest:
    def test_no_auth_header_returns_401(self, api_client):
        resp = api_client.post(
            "/api/messages/ingest", json={"sender": "Alice", "text": "Hi"}
        )
        assert resp.status_code == 401

    def test_wrong_auth_scheme_returns_401(self, api_client):
        resp = api_client.post(
            "/api/messages/ingest",
            headers={"Authorization": "Basic dXNlcjpwYXNz"},
            json={"sender": "Alice", "text": "Hi"},
        )
        assert resp.status_code == 401

    def test_unknown_token_returns_401(self, api_client):
        with patch.object(
            jarvis, "_db_find_user_by_token", new=AsyncMock(return_value=None)
        ):
            resp = api_client.post(
                "/api/messages/ingest",
                headers={"Authorization": "Bearer notarealtoken"},
                json={"sender": "Alice", "text": "Hi"},
            )
        assert resp.status_code == 401

    def test_valid_token_empty_body_returns_200(self, api_client):
        with (
            patch.object(
                jarvis, "_db_find_user_by_token", new=AsyncMock(return_value="user1")
            ),
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
            patch.object(
                jarvis, "_db_find_user_by_token", new=AsyncMock(return_value="user1")
            ),
            patch.object(jarvis, "_db_store_phone_message", new=AsyncMock()),
        ):
            resp = api_client.post(
                "/api/messages/ingest",
                headers={"Authorization": "Bearer validtoken"},
                json={"sender": "Bob", "text": "Are you free Saturday?"},
            )
        assert resp.status_code == 200
        assert resp.json() == {"ok": True}
