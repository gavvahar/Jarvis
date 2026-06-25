"""
Unit and HTTP-level tests for Jarvis.

Pure-function tests need no fixtures.
Webhook auth tests use the `api_client` fixture from conftest.py which
stubs out the database so no running PostgreSQL is required.
"""

from unittest.mock import AsyncMock, patch
import app as jarvis
from app import (
    _build_client,
    _build_system_prompt,
    _get_user_lock,
    _ha_configured,
    _ha_headers,
    _sids_for_user,
    _split_sentences,
    _user_configured,
)

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


class TestHaHeaders:
    def test_returns_bearer_token(self):
        headers = _ha_headers({"ha_token": "secret123"})
        assert headers["Authorization"] == "Bearer secret123"
        assert headers["Content-Type"] == "application/json"


class TestBuildSystemPrompt:
    def test_base_prompt_non_empty(self):
        prompt = _build_system_prompt({"ha_url": "", "ha_token": ""})
        assert len(prompt) > 50

    def test_ha_section_added_when_configured(self):
        prompt = _build_system_prompt({"ha_url": "http://ha.local", "ha_token": "tok"})
        assert "HOME AUTOMATION" in prompt

    def test_ha_section_absent_when_not_configured(self):
        prompt = _build_system_prompt({"ha_url": "", "ha_token": ""})
        assert "HOME AUTOMATION" not in prompt

    def test_location_context_included_when_set(self):
        jarvis._location_context.update({"city": "Austin", "region": "TX", "temp_f": 95, "condition": "Clear"})
        try:
            prompt = _build_system_prompt({"ha_url": "", "ha_token": ""})
            assert "Austin" in prompt
            assert "95" in prompt
        finally:
            jarvis._location_context.clear()


class TestBuildClient:
    def test_no_key_returns_none_for_anthropic(self):
        assert _build_client("anthropic", "") is None

    def test_no_key_returns_none_for_openai(self):
        assert _build_client("openai", "") is None


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


# ── Webhook auth tests ─────────────────────────────────────────────────────────


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
