"""Tests for integrations/package_tracking.py — shipping email parsing."""

import asyncio, integrations.package_tracking as package_tracking_mod

from unittest.mock import AsyncMock, MagicMock, patch
from integrations.package_tracking import _detect_carrier, _detect_status, _execute_package_tool, _extract_tracking_number, _get_package_tools


class TestDetectCarrier:
    def test_ups(self):
        assert _detect_carrier("UPS Quantum View <auto-notify@ups.com>") == "UPS"

    def test_fedex(self):
        assert _detect_carrier("FedEx <tracking@fedex.com>") == "FedEx"

    def test_usps(self):
        assert _detect_carrier("USPS <informeddelivery@usps.com>") == "USPS"

    def test_amazon(self):
        assert _detect_carrier("Amazon.com <shipment-tracking@amazon.com>") == "Amazon"

    def test_no_match(self):
        assert _detect_carrier("Newsletter <news@example.com>") is None


class TestDetectStatus:
    def test_delivered(self):
        assert _detect_status("Your package was delivered at 2pm") == "delivered"

    def test_out_for_delivery(self):
        assert _detect_status("Your package is out for delivery") == "out_for_delivery"

    def test_shipped(self):
        assert _detect_status("Good news, your order has shipped!") == "shipped"

    def test_default_update(self):
        assert _detect_status("Your delivery date has changed") == "update"


class TestExtractTrackingNumber:
    def test_ups_number(self):
        assert _extract_tracking_number("UPS", "Tracking: 1Z999AA10123456784") == "1Z999AA10123456784"

    def test_fedex_number(self):
        assert _extract_tracking_number("FedEx", "Tracking number 123456789012") == "123456789012"

    def test_no_match_returns_empty(self):
        assert _extract_tracking_number("UPS", "no tracking info here") == ""

    def test_unknown_carrier_returns_empty(self):
        assert _extract_tracking_number("Amazon", "Tracking: 1Z999AA10123456784") == ""


class TestGetPackageTools:
    def test_not_configured_returns_empty(self):
        assert _get_package_tools({}, "anthropic") == []

    def test_configured_returns_tool(self):
        config = {"email_host": "imap.example.com", "email_username": "me", "email_password": "secret"}
        tools = _get_package_tools(config, "anthropic")
        assert len(tools) == 1
        assert tools[0]["name"] == "get_package_updates"


class TestExecutePackageTool:
    def test_no_events(self):
        with patch.object(package_tracking_mod, "_db_list_package_events", new=AsyncMock(return_value=[])):
            result = asyncio.run(_execute_package_tool("u1", {}))
        assert "No package updates" in result

    def test_formats_with_tracking_number(self):
        rows = [{"carrier": "UPS", "status": "delivered", "tracking_number": "1Z999AA10123456784"}]
        with patch.object(package_tracking_mod, "_db_list_package_events", new=AsyncMock(return_value=rows)):
            result = asyncio.run(_execute_package_tool("u1", {}))
        assert "UPS: delivered" in result
        assert "1Z999AA10123456784" in result

    def test_formats_without_tracking_number(self):
        rows = [{"carrier": "Amazon", "status": "out_for_delivery", "tracking_number": ""}]
        with patch.object(package_tracking_mod, "_db_list_package_events", new=AsyncMock(return_value=rows)):
            result = asyncio.run(_execute_package_tool("u1", {}))
        assert result == "Amazon: out for delivery"


class TestAlertPackageUpdate:
    def test_emits_socket_and_push(self):
        sio = MagicMock()
        sio.emit = AsyncMock()
        package_tracking_mod.init(sio, lambda uid: ["sid1"])
        with patch.object(package_tracking_mod, "_send_push", new=AsyncMock()) as mock_push:
            asyncio.run(package_tracking_mod._alert_package_update("u1", "UPS", "delivered", "1Z999AA10123456784"))
        sio.emit.assert_awaited_once()
        assert sio.emit.call_args.args[0] == "package_alert"
        mock_push.assert_awaited_once()


class TestScanForPackageUpdates:
    def test_ignores_non_carrier_messages(self):
        messages = [{"uid": "1", "from": "news@example.com", "subject": "Weekly digest"}]
        with (
            patch.object(package_tracking_mod, "_imap_fetch_unread", new=AsyncMock(return_value=messages)),
            patch.object(package_tracking_mod, "_imap_fetch_body", new=AsyncMock()) as mock_body,
        ):
            asyncio.run(package_tracking_mod._scan_for_package_updates("u1", {}))
        mock_body.assert_not_awaited()

    def test_skips_already_tracked(self):
        messages = [{"uid": "1", "from": "auto-notify@ups.com", "subject": "Your package has shipped"}]
        with (
            patch.object(package_tracking_mod, "_imap_fetch_unread", new=AsyncMock(return_value=messages)),
            patch.object(package_tracking_mod, "_db_uids_already_tracked", new=AsyncMock(return_value={"1"})),
            patch.object(package_tracking_mod, "_imap_fetch_body", new=AsyncMock()) as mock_body,
        ):
            asyncio.run(package_tracking_mod._scan_for_package_updates("u1", {}))
        mock_body.assert_not_awaited()

    def test_alerts_on_delivered(self):
        messages = [{"uid": "1", "from": "auto-notify@ups.com", "subject": "Your package was delivered"}]
        with (
            patch.object(package_tracking_mod, "_imap_fetch_unread", new=AsyncMock(return_value=messages)),
            patch.object(package_tracking_mod, "_db_uids_already_tracked", new=AsyncMock(return_value=set())),
            patch.object(package_tracking_mod, "_imap_fetch_body", new=AsyncMock(return_value="")),
            patch.object(package_tracking_mod, "_db_insert_package_event", new=AsyncMock()) as mock_insert,
            patch.object(package_tracking_mod, "_alert_package_update", new=AsyncMock()) as mock_alert,
        ):
            asyncio.run(package_tracking_mod._scan_for_package_updates("u1", {}))
        mock_insert.assert_awaited_once_with("u1", "1", "UPS", "delivered", "")
        mock_alert.assert_awaited_once()

    def test_no_alert_for_shipped(self):
        messages = [{"uid": "1", "from": "auto-notify@ups.com", "subject": "Your package has shipped"}]
        with (
            patch.object(package_tracking_mod, "_imap_fetch_unread", new=AsyncMock(return_value=messages)),
            patch.object(package_tracking_mod, "_db_uids_already_tracked", new=AsyncMock(return_value=set())),
            patch.object(package_tracking_mod, "_imap_fetch_body", new=AsyncMock(return_value="")),
            patch.object(package_tracking_mod, "_db_insert_package_event", new=AsyncMock()),
            patch.object(package_tracking_mod, "_alert_package_update", new=AsyncMock()) as mock_alert,
        ):
            asyncio.run(package_tracking_mod._scan_for_package_updates("u1", {}))
        mock_alert.assert_not_awaited()

    def test_fetch_failure_is_swallowed(self):
        with patch.object(package_tracking_mod, "_imap_fetch_unread", new=AsyncMock(side_effect=ValueError("boom"))):
            asyncio.run(package_tracking_mod._scan_for_package_updates("u1", {}))
