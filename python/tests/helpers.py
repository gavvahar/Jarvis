"""Shared mock-builder helpers for Jarvis tests."""

from unittest.mock import AsyncMock, MagicMock
import app as jarvis


def _async_cm(return_value=None):
    """Mock usable as `async with X() as y:` where y is return_value."""
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=return_value)
    cm.__aexit__ = AsyncMock(return_value=False)
    return cm


def _mock_async_client(**method_returns):
    """Mock usable as `async with httpx.AsyncClient(...) as c:`.
    Pass e.g. get=resp, or get=AsyncMock(side_effect=...) for custom behavior.
    """
    client = _async_cm()
    client.__aenter__ = AsyncMock(return_value=client)
    for method, value in method_returns.items():
        setattr(client, method, value if isinstance(value, AsyncMock) else AsyncMock(return_value=value))
    return client


def _mock_asyncpg_pool(*, fetchrow=None, fetch=None, fetchval=None, execute=None):
    """Mock asyncpg pool; pool.acquire() yields a conn with configurable
    fetchrow/fetch/fetchval/execute return values (fetch defaults to []).
    """
    conn = MagicMock()
    conn.fetchrow = AsyncMock(return_value=fetchrow)
    conn.fetch = AsyncMock(return_value=fetch if fetch is not None else [])
    conn.fetchval = AsyncMock(return_value=fetchval)
    conn.execute = AsyncMock(return_value=execute)
    pool = MagicMock()
    pool.acquire = MagicMock(return_value=_async_cm(conn))
    return pool, conn


def _seed_user_state(config=None, role="user", client=None):
    """Pre-populate app.py's `_user_states["local"]` cache so route handlers
    that call `_get_user_state` skip the real DB-loading path entirely.
    `_get_current_user` always resolves to "local" under the `api_client`
    fixture, since `_oidc_config` is never set in tests.
    """
    state = {
        "config": config if config is not None else {},
        "client": client if client is not None else MagicMock(),
        "provider": "anthropic",
        "conversation": [],
        "role": role,
        "user_id": "local",
    }
    jarvis._user_states["local"] = state
    return state
