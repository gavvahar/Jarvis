"""Tests for integrations/shared_lists.py — household shopping/todo lists."""

import asyncio, app as jarvis, db as db_mod

from unittest.mock import AsyncMock, patch
from integrations.shared_lists import _execute_shared_list_tool
from helpers import _mock_asyncpg_pool


class TestExecuteSharedListToolMocked:
    def test_read_empty(self):
        with patch("integrations.shared_lists._db_get_shared_list", new=AsyncMock(return_value=[])):
            result = asyncio.run(_execute_shared_list_tool({"action": "read", "list_name": "shopping"}))
        assert "empty" in result.lower()

    def test_add_item(self):
        with (
            patch("integrations.shared_lists._db_get_shared_list", new=AsyncMock(return_value=[])),
            patch("integrations.shared_lists._db_update_shared_list", new=AsyncMock()),
        ):
            result = asyncio.run(_execute_shared_list_tool({"action": "add", "list_name": "shopping", "item": "Milk"}))
        assert "Milk" in result

    def test_remove_item(self):
        with (
            patch("integrations.shared_lists._db_get_shared_list", new=AsyncMock(return_value=["Milk", "Eggs"])),
            patch("integrations.shared_lists._db_update_shared_list", new=AsyncMock()),
        ):
            result = asyncio.run(_execute_shared_list_tool({"action": "remove", "list_name": "shopping", "item": "Milk"}))
        assert "Removed" in result

    def test_remove_not_found(self):
        with patch("integrations.shared_lists._db_get_shared_list", new=AsyncMock(return_value=["Eggs"])):
            result = asyncio.run(_execute_shared_list_tool({"action": "remove", "list_name": "shopping", "item": "Milk"}))
        assert "not found" in result.lower()

    def test_clear_list(self):
        with (
            patch("integrations.shared_lists._db_get_shared_list", new=AsyncMock(return_value=["Milk"])),
            patch("integrations.shared_lists._db_update_shared_list", new=AsyncMock()),
        ):
            result = asyncio.run(_execute_shared_list_tool({"action": "clear", "list_name": "shopping"}))
        assert "cleared" in result.lower()


class TestDbSharedLists:
    def test_get_shared_list_found(self):
        import json

        pool, conn = _mock_asyncpg_pool(fetchrow={"items": json.dumps(["milk"])})
        with patch("db._pool", return_value=pool):
            result = asyncio.run(db_mod._db_get_shared_list("shopping"))
        assert result == ["milk"]

    def test_get_shared_list_creates_when_missing(self):
        pool, conn = _mock_asyncpg_pool(fetchrow=None)
        with patch("db._pool", return_value=pool):
            result = asyncio.run(db_mod._db_get_shared_list("shopping"))
        assert result == []
        conn.execute.assert_awaited_once()

    def test_create_shared_list(self):
        pool, conn = _mock_asyncpg_pool()
        with patch("db._pool", return_value=pool):
            asyncio.run(db_mod._db_create_shared_list("shopping"))
        conn.execute.assert_awaited_once()

    def test_update_shared_list(self):
        pool, conn = _mock_asyncpg_pool()
        with patch("db._pool", return_value=pool):
            asyncio.run(db_mod._db_update_shared_list("shopping", ["milk", "eggs"]))
        conn.execute.assert_awaited_once()

    def test_get_all_shared_lists(self):
        import json

        rows = [{"name": "shopping", "items": json.dumps(["milk"])}, {"name": "todo", "items": None}]
        pool, conn = _mock_asyncpg_pool(fetch=rows)
        with patch("db._pool", return_value=pool):
            result = asyncio.run(db_mod._db_get_all_shared_lists())
        assert result == {"shopping": ["milk"], "todo": []}


class TestApiSharedListsRoute:
    def test_shared_lists(self, api_client):
        with patch.object(jarvis, "_db_get_all_shared_lists", new=AsyncMock(return_value={"shopping": ["milk"]})):
            resp = api_client.get("/api/shared-lists")
        assert resp.json() == {"lists": {"shopping": ["milk"]}}
