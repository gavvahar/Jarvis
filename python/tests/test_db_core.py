"""Tests for db.py — core user config, webhook tokens, conversations, voice embeddings."""

import asyncio, db as db_mod

from unittest.mock import AsyncMock, MagicMock, patch
from helpers import _mock_asyncpg_pool


class TestDbInitCloseReady:
    def test_ready_false_when_no_pool(self):
        with patch.object(db_mod, "_db_pool", None):
            assert db_mod._db_ready() is False

    def test_ready_true_when_pool_set(self):
        with patch.object(db_mod, "_db_pool", MagicMock()):
            assert db_mod._db_ready() is True

    def test_close_noop_when_no_pool(self):
        with patch.object(db_mod, "_db_pool", None):
            asyncio.run(db_mod._db_close())

    def test_close_closes_and_clears_pool(self):
        pool = MagicMock()
        pool.close = AsyncMock()
        with patch.object(db_mod, "_db_pool", pool):
            asyncio.run(db_mod._db_close())
            assert db_mod._db_pool is None
        pool.close.assert_awaited_once()

    def test_init_creates_pool_and_runs_schema(self):
        pool, conn = _mock_asyncpg_pool()
        with (
            patch("db.asyncpg.create_pool", new=AsyncMock(return_value=pool)),
            patch.object(db_mod, "_db_pool", None),
        ):
            asyncio.run(db_mod._db_init())
            assert db_mod._db_pool is pool
        conn.execute.assert_awaited_once_with(db_mod._SCHEMA)


class TestDbUserConfig:
    def test_ensure_user(self):
        pool, conn = _mock_asyncpg_pool()
        with patch("db._pool", return_value=pool):
            asyncio.run(db_mod._db_ensure_user("u1", "a@b.com", "admin"))
        conn.execute.assert_awaited_once_with(conn.execute.call_args.args[0], "u1", "a@b.com", "admin")

    def test_load_config_found(self):
        row = {"role": "user", "provider": "anthropic", "api_key": "k", "model": "claude-haiku-4-5"}
        pool, conn = _mock_asyncpg_pool(fetchrow=row)
        with patch("db._pool", return_value=pool):
            result = asyncio.run(db_mod._db_load_config("u1"))
        assert result == row

    def test_load_config_defaults_when_missing(self):
        pool, conn = _mock_asyncpg_pool(fetchrow=None)
        with patch("db._pool", return_value=pool):
            result = asyncio.run(db_mod._db_load_config("u1"))
        assert result["role"] == "user"
        assert result["provider"] == "anthropic"
        assert result["apple_music_storefront"] == "us"
        assert result["is_kid_safe"] is False

    def test_save_config(self):
        pool, conn = _mock_asyncpg_pool()
        cfg = {"provider": "anthropic", "api_key": "k", "model": "m", "base_url": "", "ha_url": "", "ha_token": ""}
        with patch("db._pool", return_value=pool):
            asyncio.run(db_mod._db_save_config("u1", cfg))
        assert conn.execute.await_count == 2

    def test_set_kid_safe(self):
        pool, conn = _mock_asyncpg_pool()
        with patch("db._pool", return_value=pool):
            asyncio.run(db_mod._db_set_kid_safe("u1", True))
        conn.execute.assert_awaited_once()

    def test_set_display_name(self):
        pool, conn = _mock_asyncpg_pool()
        with patch("db._pool", return_value=pool):
            asyncio.run(db_mod._db_set_display_name("u1", "Alice"))
        conn.execute.assert_awaited_once()

    def test_save_pim_config(self):
        pool, conn = _mock_asyncpg_pool()
        with patch("db._pool", return_value=pool):
            asyncio.run(db_mod._db_save_pim_config("u1", "url", "user", "pw", "curl", "cuser", "cpw"))
        assert conn.execute.await_count == 2

    def test_get_household_members(self):
        rows = [{"user_id": "u1", "email": "a@b.com", "display_name": "", "is_kid_safe": False, "has_voice": True}]
        pool, conn = _mock_asyncpg_pool(fetch=rows)
        with patch("db._pool", return_value=pool):
            result = asyncio.run(db_mod._db_get_household_members())
        assert result == rows


class TestDbWebhookTokens:
    def test_get_or_create_returns_existing(self):
        pool, conn = _mock_asyncpg_pool(fetchrow={"webhook_token": "tok123"})
        with patch("db._pool", return_value=pool):
            token = asyncio.run(db_mod._db_get_or_create_webhook_token("u1"))
        assert token == "tok123"

    def test_get_or_create_generates_new(self):
        pool, conn = _mock_asyncpg_pool(fetchrow={"webhook_token": ""})
        with patch("db._pool", return_value=pool):
            token = asyncio.run(db_mod._db_get_or_create_webhook_token("u1"))
        assert len(token) == 64
        conn.execute.assert_awaited_once()

    def test_regenerate_token(self):
        pool, conn = _mock_asyncpg_pool()
        with patch("db._pool", return_value=pool):
            token = asyncio.run(db_mod._db_regenerate_webhook_token("u1"))
        assert len(token) == 64
        conn.execute.assert_awaited_once()

    def test_find_user_by_token_found(self):
        pool, conn = _mock_asyncpg_pool(fetchrow={"user_id": "u1"})
        with patch("db._pool", return_value=pool):
            result = asyncio.run(db_mod._db_find_user_by_token("tok"))
        assert result == "u1"

    def test_find_user_by_token_not_found(self):
        pool, conn = _mock_asyncpg_pool(fetchrow=None)
        with patch("db._pool", return_value=pool):
            result = asyncio.run(db_mod._db_find_user_by_token("bogus"))
        assert result is None


class TestDbConversations:
    def test_load_conversation_parses_json(self):
        import json

        rows = [{"role": "user", "content": json.dumps("hello")}]
        pool, conn = _mock_asyncpg_pool(fetch=rows)
        with patch("db._pool", return_value=pool):
            result = asyncio.run(db_mod._db_load_conversation("u1"))
        assert result == [{"role": "user", "content": "hello"}]

    def test_append_message(self):
        pool, conn = _mock_asyncpg_pool()
        with patch("db._pool", return_value=pool):
            asyncio.run(db_mod._db_append_message("u1", "user", "hi"))
        assert conn.execute.await_count == 2

    def test_clear_conversation(self):
        pool, conn = _mock_asyncpg_pool()
        with patch("db._pool", return_value=pool):
            asyncio.run(db_mod._db_clear_conversation("u1"))
        conn.execute.assert_awaited_once()


class TestDbVoiceEmbeddings:
    def test_save_voice_embedding(self):
        pool, conn = _mock_asyncpg_pool()
        with patch("db._pool", return_value=pool):
            asyncio.run(db_mod._db_save_voice_embedding("u1", [0.1, 0.2]))
        conn.execute.assert_awaited_once()

    def test_clear_voice_embedding(self):
        pool, conn = _mock_asyncpg_pool()
        with patch("db._pool", return_value=pool):
            asyncio.run(db_mod._db_clear_voice_embedding("u1"))
        conn.execute.assert_awaited_once()

    def test_get_all_voice_embeddings_skips_empty(self):
        import json

        rows = [
            {"user_id": "u1", "voice_embedding": json.dumps([1, 2, 3]), "display_name": "Alice", "is_kid_safe": False},
            {"user_id": "u2", "voice_embedding": None, "display_name": "", "is_kid_safe": False},
        ]
        pool, conn = _mock_asyncpg_pool(fetch=rows)
        with patch("db._pool", return_value=pool):
            result = asyncio.run(db_mod._db_get_all_voice_embeddings())
        assert list(result.keys()) == ["u1"]
        assert result["u1"] == ([1, 2, 3], "Alice", False)
