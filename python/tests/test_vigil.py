"""Tests for integrations/vigil.py — Vigil Mode arm/disarm."""

import asyncio, db as db_mod, integrations.vigil as vigil_mod

from unittest.mock import AsyncMock, patch
from fastapi import HTTPException
from helpers import _mock_asyncpg_pool


class TestVigilIntegration:
    def test_get_vigil_tools_returns_both_tools(self):
        names = {t["name"] for t in vigil_mod._get_vigil_tools("anthropic")}
        assert names == {"set_vigil_mode", "get_vigil_mode"}

    def test_set_vigil_mode_rejects_invalid_mode(self):
        try:
            asyncio.run(vigil_mod._set_vigil_mode("bogus", "u1"))
            raise AssertionError("expected HTTPException")
        except HTTPException as e:
            assert e.status_code == 400

    def test_set_vigil_mode_updates_and_broadcasts(self):
        mock_broadcast = AsyncMock()
        vigil_mod.init(mock_broadcast)
        with patch.object(vigil_mod, "_db_set_vigil_mode", new=AsyncMock()) as mock_set:
            result = asyncio.run(vigil_mod._set_vigil_mode("ARMED", "u1"))
        assert result == {"ok": True, "mode": "armed"}
        mock_set.assert_awaited_once_with("armed", "u1")
        mock_broadcast.assert_awaited_once_with("vigil_mode_changed", {"mode": "armed", "updated_by": "u1"})

    def test_execute_vigil_tool_set_mode_returns_speak_text(self):
        vigil_mod.init(AsyncMock())
        with patch.object(vigil_mod, "_db_set_vigil_mode", new=AsyncMock()):
            result = asyncio.run(vigil_mod._execute_vigil_tool("set_vigil_mode", {"mode": "disarmed"}, "u1"))
        assert "disarmed" in result.lower()

    def test_execute_vigil_tool_set_mode_invalid(self):
        result = asyncio.run(vigil_mod._execute_vigil_tool("set_vigil_mode", {"mode": "nope"}, "u1"))
        assert "mode must be one of" in result

    def test_execute_vigil_tool_get_mode(self):
        with patch.object(vigil_mod, "_db_get_vigil_mode", new=AsyncMock(return_value="auto")):
            result = asyncio.run(vigil_mod._execute_vigil_tool("get_vigil_mode", {}, "u1"))
        assert "auto" in result

    def test_execute_vigil_tool_unknown_name(self):
        result = asyncio.run(vigil_mod._execute_vigil_tool("nonsense", {}, "u1"))
        assert "Unknown vigil tool" in result


class TestDbVigilMode:
    def test_get_vigil_mode_defaults_to_auto(self):
        pool, conn = _mock_asyncpg_pool(fetchval=None)
        with patch("db._pool", return_value=pool):
            result = asyncio.run(db_mod._db_get_vigil_mode())
        assert result == "auto"

    def test_get_vigil_mode_returns_stored_value(self):
        pool, conn = _mock_asyncpg_pool(fetchval="armed")
        with patch("db._pool", return_value=pool):
            result = asyncio.run(db_mod._db_get_vigil_mode())
        assert result == "armed"

    def test_set_vigil_mode_updates_row(self):
        pool, conn = _mock_asyncpg_pool()
        with patch("db._pool", return_value=pool):
            asyncio.run(db_mod._db_set_vigil_mode("disarmed", "u1"))
        conn.execute.assert_awaited_once()
        assert conn.execute.call_args.args[1] == "disarmed"
        assert conn.execute.call_args.args[2] == "u1"
