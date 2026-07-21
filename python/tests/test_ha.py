"""Tests for integrations/ha.py + Home Assistant config/route tests."""

import asyncio, app as jarvis

from unittest.mock import AsyncMock, MagicMock, patch
from app import _ha_configured
from integrations.ha import _ha_call_service, _ha_get_entity_state, _ha_get_states, _ha_headers, _validate_ha
from helpers import _mock_async_client, _seed_user_state


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


class TestHaHeaders:
    def test_returns_bearer_token(self):
        headers = _ha_headers({"ha_token": "secret123"})
        assert headers["Authorization"] == "Bearer secret123"
        assert headers["Content-Type"] == "application/json"


class TestHaIntegration:
    def test_validate_ha_success(self):
        resp = MagicMock(status_code=200)
        with patch("httpx.AsyncClient", return_value=_mock_async_client(get=resp)):
            ok, msg = asyncio.run(_validate_ha("http://ha.local", "tok"))
        assert ok is True
        assert msg == ""

    def test_validate_ha_rejected_token(self):
        resp = MagicMock(status_code=401)
        with patch("httpx.AsyncClient", return_value=_mock_async_client(get=resp)):
            ok, msg = asyncio.run(_validate_ha("http://ha.local", "bad"))
        assert ok is False
        assert "rejected" in msg

    def test_validate_ha_other_status(self):
        resp = MagicMock(status_code=500)
        with patch("httpx.AsyncClient", return_value=_mock_async_client(get=resp)):
            ok, msg = asyncio.run(_validate_ha("http://ha.local", "tok"))
        assert ok is False
        assert "500" in msg

    def test_validate_ha_connection_error(self):
        with patch("httpx.AsyncClient", return_value=_mock_async_client(get=AsyncMock(side_effect=Exception("refused")))):
            ok, msg = asyncio.run(_validate_ha("http://ha.local", "tok"))
        assert ok is False
        assert "Could not reach" in msg

    def test_get_entity_state_found(self):
        resp = MagicMock(status_code=200)
        resp.json = MagicMock(return_value={"state": "on"})
        cfg = {"ha_url": "http://ha.local", "ha_token": "tok"}
        with patch("httpx.AsyncClient", return_value=_mock_async_client(get=resp)):
            state = asyncio.run(_ha_get_entity_state(cfg, "light.kitchen"))
        assert state == "on"

    def test_get_entity_state_not_found(self):
        resp = MagicMock(status_code=404)
        cfg = {"ha_url": "http://ha.local", "ha_token": "tok"}
        with patch("httpx.AsyncClient", return_value=_mock_async_client(get=resp)):
            state = asyncio.run(_ha_get_entity_state(cfg, "light.kitchen"))
        assert state is None

    def test_get_entity_state_exception_returns_none(self):
        cfg = {"ha_url": "http://ha.local", "ha_token": "tok"}
        with patch("httpx.AsyncClient", return_value=_mock_async_client(get=AsyncMock(side_effect=Exception("boom")))):
            state = asyncio.run(_ha_get_entity_state(cfg, "light.kitchen"))
        assert state is None

    def test_get_states_filters_by_domain_and_formats(self):
        resp = MagicMock(status_code=200)
        resp.raise_for_status = MagicMock()
        resp.json = MagicMock(
            return_value=[
                {"entity_id": "light.kitchen", "state": "on", "attributes": {"friendly_name": "Kitchen Light"}},
                {"entity_id": "switch.fan", "state": "off", "attributes": {}},
            ]
        )
        cfg = {"ha_url": "http://ha.local", "ha_token": "tok"}
        with patch("httpx.AsyncClient", return_value=_mock_async_client(get=resp)):
            result = asyncio.run(_ha_get_states(cfg, domain="light"))
        assert "light.kitchen: on (Kitchen Light)" in result
        assert "switch.fan" not in result

    def test_get_states_no_entities(self):
        resp = MagicMock(status_code=200)
        resp.raise_for_status = MagicMock()
        resp.json = MagicMock(return_value=[])
        cfg = {"ha_url": "http://ha.local", "ha_token": "tok"}
        with patch("httpx.AsyncClient", return_value=_mock_async_client(get=resp)):
            result = asyncio.run(_ha_get_states(cfg))
        assert result == "No entities found."

    def test_call_service_success(self):
        resp = MagicMock(status_code=200)
        cfg = {"ha_url": "http://ha.local", "ha_token": "tok"}
        with patch("httpx.AsyncClient", return_value=_mock_async_client(post=resp)):
            result = asyncio.run(_ha_call_service(cfg, "light", "turn_on", "light.kitchen", {"brightness_pct": 50}))
        assert result == "Done."

    def test_call_service_failure(self):
        resp = MagicMock(status_code=400, text="bad request")
        cfg = {"ha_url": "http://ha.local", "ha_token": "tok"}
        with patch("httpx.AsyncClient", return_value=_mock_async_client(post=resp)):
            result = asyncio.run(_ha_call_service(cfg, "light", "turn_on"))
        assert "400" in result


class TestApiSaveHa:
    def test_saves_without_validation_when_no_token_change(self, api_client):
        _seed_user_state(config={"ha_url": "", "ha_token": ""})
        with patch.object(jarvis, "_db_save_config", new=AsyncMock()):
            resp = api_client.post("/api/save_ha", json={"ha_url": "", "ha_token": ""})
        assert resp.json() == {"ok": True, "ha_configured": False}

    def test_validation_failure_surfaced(self, api_client):
        _seed_user_state(config={})
        with patch.object(jarvis, "_validate_ha", new=AsyncMock(return_value=(False, "bad token"))):
            resp = api_client.post("/api/save_ha", json={"ha_url": "http://ha.local", "ha_token": "tok"})
        assert resp.json() == {"ok": False, "error": "bad token"}

    def test_success(self, api_client):
        _seed_user_state(config={})
        with (
            patch.object(jarvis, "_validate_ha", new=AsyncMock(return_value=(True, ""))),
            patch.object(jarvis, "_db_save_config", new=AsyncMock()),
        ):
            resp = api_client.post("/api/save_ha", json={"ha_url": "http://ha.local", "ha_token": "tok"})
        assert resp.json() == {"ok": True, "ha_configured": True}
