"""Tests for integrations/push.py — Web Push notifications."""

import asyncio, db as db_mod, integrations.push as push_mod

from unittest.mock import AsyncMock, patch
from helpers import _mock_asyncpg_pool


class TestPushIntegration:
    def test_send_push_noop_when_not_configured(self):
        with (
            patch.object(push_mod, "_PUSH_OK", False),
            patch.object(push_mod, "_db_get_push_subscriptions", new=AsyncMock()) as mock_subs,
        ):
            asyncio.run(push_mod._send_push("u1", "Title", "Body"))
        mock_subs.assert_not_awaited()

    def test_send_push_noop_when_no_subscriptions(self):
        with (
            patch.object(push_mod, "_PUSH_OK", True),
            patch.object(push_mod, "VAPID_PUBLIC_KEY", "pub"),
            patch.object(push_mod, "VAPID_PRIVATE_KEY", "priv"),
            patch.object(push_mod, "_db_get_push_subscriptions", new=AsyncMock(return_value=[])),
            patch.object(push_mod, "_send_one") as mock_send_one,
        ):
            asyncio.run(push_mod._send_push("u1", "Title", "Body"))
        mock_send_one.assert_not_called()

    def test_send_push_prunes_expired_subscription(self):
        subs = [{"endpoint": "https://push.example/1", "p256dh": "key", "auth": "secret"}]
        with (
            patch.object(push_mod, "_PUSH_OK", True),
            patch.object(push_mod, "VAPID_PUBLIC_KEY", "pub"),
            patch.object(push_mod, "VAPID_PRIVATE_KEY", "priv"),
            patch.object(push_mod, "_db_get_push_subscriptions", new=AsyncMock(return_value=subs)),
            patch.object(push_mod, "_send_one", return_value=410),
            patch.object(push_mod, "_db_remove_push_subscription", new=AsyncMock()) as mock_remove,
        ):
            asyncio.run(push_mod._send_push("u1", "Title", "Body"))
        mock_remove.assert_awaited_once_with("u1", "https://push.example/1")

    def test_send_push_keeps_subscription_on_success(self):
        subs = [{"endpoint": "https://push.example/1", "p256dh": "key", "auth": "secret"}]
        with (
            patch.object(push_mod, "_PUSH_OK", True),
            patch.object(push_mod, "VAPID_PUBLIC_KEY", "pub"),
            patch.object(push_mod, "VAPID_PRIVATE_KEY", "priv"),
            patch.object(push_mod, "_db_get_push_subscriptions", new=AsyncMock(return_value=subs)),
            patch.object(push_mod, "_send_one", return_value=None),
            patch.object(push_mod, "_db_remove_push_subscription", new=AsyncMock()) as mock_remove,
        ):
            asyncio.run(push_mod._send_push("u1", "Title", "Body"))
        mock_remove.assert_not_awaited()


class TestDbPushSubscriptions:
    def test_add_and_list_push_subscription(self):
        pool, conn = _mock_asyncpg_pool(fetch=[{"endpoint": "https://push.example/1", "p256dh": "key", "auth": "secret"}])
        with patch("db._pool", return_value=pool):
            asyncio.run(db_mod._db_add_push_subscription("u1", "https://push.example/1", "key", "secret"))
            result = asyncio.run(db_mod._db_get_push_subscriptions("u1"))
        conn.execute.assert_awaited_once()
        assert result == [{"endpoint": "https://push.example/1", "p256dh": "key", "auth": "secret"}]

    def test_remove_push_subscription(self):
        pool, conn = _mock_asyncpg_pool()
        with patch("db._pool", return_value=pool):
            asyncio.run(db_mod._db_remove_push_subscription("u1", "https://push.example/1"))
        conn.execute.assert_awaited_once_with("DELETE FROM push_subscriptions WHERE user_id=$1 AND endpoint=$2", "u1", "https://push.example/1")
