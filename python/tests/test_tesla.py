"""Tests for integrations/tesla.py — Tesla vehicle control."""

import asyncio, datetime, app as jarvis, integrations.tesla as tesla_mod

from unittest.mock import AsyncMock, MagicMock, patch
from app import _tesla_configured
from integrations.tesla import _execute_tesla_tool, _get_tesla_tools, _tesla_base_url, _tesla_pick_vehicle, _c_to_f
from helpers import _mock_async_client, _mock_asyncpg_pool, _seed_user_state


class TestTeslaConfigured:
    def test_not_configured_when_method_empty(self):
        assert _tesla_configured({"tesla_method": "", "tesla_refresh_token": "", "tesla_fleet_refresh_token": ""}) is False

    def test_unofficial_configured_when_token_present(self):
        assert _tesla_configured({"tesla_method": "unofficial", "tesla_refresh_token": "tok", "tesla_fleet_refresh_token": ""}) is True

    def test_unofficial_not_configured_when_token_missing(self):
        assert _tesla_configured({"tesla_method": "unofficial", "tesla_refresh_token": "", "tesla_fleet_refresh_token": ""}) is False

    def test_fleet_configured_when_token_present(self):
        assert _tesla_configured({"tesla_method": "fleet", "tesla_refresh_token": "", "tesla_fleet_refresh_token": "fleet_tok"}) is True

    def test_fleet_not_configured_when_token_missing(self):
        assert _tesla_configured({"tesla_method": "fleet", "tesla_refresh_token": "", "tesla_fleet_refresh_token": ""}) is False

    def test_both_requires_both_tokens(self):
        assert _tesla_configured({"tesla_method": "both", "tesla_refresh_token": "tok", "tesla_fleet_refresh_token": "fleet_tok"}) is True

    def test_both_fails_if_unofficial_token_missing(self):
        assert _tesla_configured({"tesla_method": "both", "tesla_refresh_token": "", "tesla_fleet_refresh_token": "fleet_tok"}) is False

    def test_both_fails_if_fleet_token_missing(self):
        assert _tesla_configured({"tesla_method": "both", "tesla_refresh_token": "tok", "tesla_fleet_refresh_token": ""}) is False


class TestCToF:
    def test_freezing(self):
        assert _c_to_f(0) == 32.0

    def test_boiling(self):
        assert _c_to_f(100) == 212.0

    def test_body_temp(self):
        assert abs(_c_to_f(37) - 98.6) < 0.1

    def test_crossover(self):
        assert _c_to_f(-40) == -40.0

    def test_negative(self):
        assert _c_to_f(-10) == 14.0


class TestGetTeslaTools:
    def test_returns_empty_when_not_configured(self):
        cfg = {"tesla_method": "", "tesla_refresh_token": "", "tesla_fleet_refresh_token": ""}
        assert _get_tesla_tools(cfg, "anthropic") == []

    def test_returns_anthropic_tools_when_configured(self):
        cfg = {"tesla_method": "unofficial", "tesla_refresh_token": "tok", "tesla_fleet_refresh_token": ""}
        tools = _get_tesla_tools(cfg, "anthropic")
        assert len(tools) > 0
        names = [t["name"] for t in tools]
        assert "get_vehicle_status" in names
        assert "lock_vehicle" in names
        assert "set_climate" in names

    def test_returns_openai_tools_when_configured(self):
        cfg = {"tesla_method": "unofficial", "tesla_refresh_token": "tok", "tesla_fleet_refresh_token": ""}
        tools = _get_tesla_tools(cfg, "openai")
        assert len(tools) > 0
        assert tools[0]["type"] == "function"

    def test_returns_nine_tools(self):
        cfg = {"tesla_method": "fleet", "tesla_refresh_token": "", "tesla_fleet_refresh_token": "fleet_tok"}
        tools = _get_tesla_tools(cfg, "anthropic")
        assert len(tools) == 9

    def test_tool_names_include_trunk(self):
        cfg = {"tesla_method": "unofficial", "tesla_refresh_token": "tok", "tesla_fleet_refresh_token": ""}
        names = {t["name"] for t in _get_tesla_tools(cfg, "anthropic")}
        assert "actuate_trunk" in names
        assert "honk_horn" in names
        assert "flash_lights" in names


class TestTeslaBaseUrl:
    def test_unofficial(self):
        assert _tesla_base_url("unofficial") == "https://owner-api.teslamotors.com"

    def test_fleet(self):
        assert "fleet-api" in _tesla_base_url("fleet")


class TestExecuteTeslaTool:
    _cfg = {"tesla_method": "unofficial", "tesla_refresh_token": "rt"}

    def test_pick_vehicle_error_surfaced(self):
        with patch("integrations.tesla._tesla_pick_vehicle", new=AsyncMock(side_effect=ValueError("No Tesla vehicle found in your account."))):
            result = asyncio.run(_execute_tesla_tool(self._cfg, "get_vehicle_status", {}, "u1"))
        assert "Tesla error: No Tesla vehicle found" in result

    def test_status_asleep(self):
        vehicle = {"id": 1, "display_name": "Model 3", "state": "asleep"}
        with (
            patch("integrations.tesla._tesla_pick_vehicle", new=AsyncMock(return_value=("unofficial", vehicle))),
            patch("integrations.tesla._tesla_access_token", new=AsyncMock(return_value="tok")),
        ):
            result = asyncio.run(_execute_tesla_tool(self._cfg, "get_vehicle_status", {}, "u1"))
        assert "asleep" in result

    def test_status_online(self):
        vehicle = {"id": 1, "display_name": "Model 3", "state": "online"}
        vehicle_data = {
            "response": {
                "charge_state": {"battery_level": 80, "est_battery_range": 250, "charging_state": "Disconnected"},
                "climate_state": {"inside_temp": 22, "is_climate_on": True, "outside_temp": 10},
                "vehicle_state": {"locked": True, "odometer": 12345},
            }
        }
        resp = MagicMock(status_code=200)
        resp.raise_for_status = MagicMock()
        resp.json = MagicMock(return_value=vehicle_data)
        with (
            patch("integrations.tesla._tesla_pick_vehicle", new=AsyncMock(return_value=("unofficial", vehicle))),
            patch("integrations.tesla._tesla_access_token", new=AsyncMock(return_value="tok")),
            patch("httpx.AsyncClient", return_value=_mock_async_client(get=resp)),
        ):
            result = asyncio.run(_execute_tesla_tool(self._cfg, "get_vehicle_status", {}, "u1"))
        assert "Battery: 80%" in result
        assert "Locked" in result
        assert "72°F inside" in result

    def test_lock_vehicle(self):
        vehicle = {"id": 1, "display_name": "Model 3", "state": "online"}
        with (
            patch("integrations.tesla._tesla_pick_vehicle", new=AsyncMock(return_value=("unofficial", vehicle))),
            patch("integrations.tesla._tesla_access_token", new=AsyncMock(return_value="tok")),
            patch("integrations.tesla._tesla_cmd", new=AsyncMock(return_value={"result": True})),
        ):
            result = asyncio.run(_execute_tesla_tool(self._cfg, "lock_vehicle", {}, "u1"))
        assert result == "Doors locked on Model 3."

    def test_command_failure_message(self):
        vehicle = {"id": 1, "display_name": "Model 3", "state": "online"}
        with (
            patch("integrations.tesla._tesla_pick_vehicle", new=AsyncMock(return_value=("unofficial", vehicle))),
            patch("integrations.tesla._tesla_access_token", new=AsyncMock(return_value="tok")),
            patch("integrations.tesla._tesla_cmd", new=AsyncMock(return_value={"result": False, "reason": "vehicle_unavailable"})),
        ):
            result = asyncio.run(_execute_tesla_tool(self._cfg, "unlock_vehicle", {}, "u1"))
        assert "Command failed: vehicle_unavailable" in result

    def test_set_climate_start_with_temperature(self):
        vehicle = {"id": 1, "display_name": "Model 3", "state": "online"}
        mock_cmd = AsyncMock(return_value={"result": True})
        with (
            patch("integrations.tesla._tesla_pick_vehicle", new=AsyncMock(return_value=("unofficial", vehicle))),
            patch("integrations.tesla._tesla_access_token", new=AsyncMock(return_value="tok")),
            patch("integrations.tesla._tesla_cmd", new=mock_cmd),
        ):
            result = asyncio.run(_execute_tesla_tool(self._cfg, "set_climate", {"action": "start", "temperature_f": 72}, "u1"))
        assert "Climate started" in result
        assert mock_cmd.await_count == 2

    def test_actuate_trunk_frunk(self):
        vehicle = {"id": 1, "display_name": "Model 3", "state": "online"}
        with (
            patch("integrations.tesla._tesla_pick_vehicle", new=AsyncMock(return_value=("unofficial", vehicle))),
            patch("integrations.tesla._tesla_access_token", new=AsyncMock(return_value="tok")),
            patch("integrations.tesla._tesla_cmd", new=AsyncMock(return_value={"result": True})),
        ):
            result = asyncio.run(_execute_tesla_tool(self._cfg, "actuate_trunk", {"which": "front"}, "u1"))
        assert result == "Frunk opened on Model 3."

    def test_unknown_tool(self):
        vehicle = {"id": 1, "display_name": "Model 3", "state": "online"}
        with (
            patch("integrations.tesla._tesla_pick_vehicle", new=AsyncMock(return_value=("unofficial", vehicle))),
            patch("integrations.tesla._tesla_access_token", new=AsyncMock(return_value="tok")),
        ):
            result = asyncio.run(_execute_tesla_tool(self._cfg, "bogus_tool", {}, "u1"))
        assert "Unknown Tesla tool" in result

    def test_fleet_lock_vehicle(self):
        vehicle = {"vin": "5YJ123", "display_name": "Model Y", "state": "online"}
        with (
            patch("integrations.tesla._tesla_pick_vehicle", new=AsyncMock(return_value=("fleet", vehicle))),
            patch("integrations.tesla._tesla_access_token", new=AsyncMock(return_value="tok")),
            patch("integrations.tesla._tesla_cmd", new=AsyncMock(return_value={"result": True})),
        ):
            result = asyncio.run(_execute_tesla_tool({"tesla_method": "fleet", "tesla_fleet_refresh_token": "ft"}, "lock_vehicle", {}, "u1"))
        assert result == "Doors locked on Model Y."

    def test_set_climate_stop_unofficial(self):
        vehicle = {"id": 1, "display_name": "Model 3", "state": "online"}
        with (
            patch("integrations.tesla._tesla_pick_vehicle", new=AsyncMock(return_value=("unofficial", vehicle))),
            patch("integrations.tesla._tesla_access_token", new=AsyncMock(return_value="tok")),
            patch("integrations.tesla._tesla_cmd", new=AsyncMock(return_value={"result": True})),
        ):
            result = asyncio.run(_execute_tesla_tool(self._cfg, "set_climate", {"action": "stop"}, "u1"))
        assert result == "Climate stopped on Model 3."

    def test_fleet_get_vehicle_status_online(self):
        vehicle = {"vin": "5YJ123", "display_name": "Model Y", "state": "online"}
        vehicle_data = {
            "response": {
                "charge_state": {"battery_level": 70, "est_battery_range": 200, "charging_state": "Charging"},
                "climate_state": {"inside_temp": 20, "is_climate_on": True},
                "vehicle_state": {"locked": False},
            }
        }
        resp = MagicMock(status_code=200)
        resp.raise_for_status = MagicMock()
        resp.json = MagicMock(return_value=vehicle_data)
        with (
            patch("integrations.tesla._tesla_pick_vehicle", new=AsyncMock(return_value=("fleet", vehicle))),
            patch("integrations.tesla._tesla_access_token", new=AsyncMock(return_value="tok")),
            patch("httpx.AsyncClient", return_value=_mock_async_client(get=resp)),
        ):
            result = asyncio.run(_execute_tesla_tool({"tesla_method": "fleet", "tesla_fleet_refresh_token": "ft"}, "get_vehicle_status", {}, "u1"))
        assert "Battery: 70%" in result
        assert "Unlocked" in result
        assert "68°F inside" in result

    def test_fleet_get_vehicle_status_asleep(self):
        vehicle = {"vin": "5YJ123", "display_name": "Model Y", "state": "asleep"}
        with (
            patch("integrations.tesla._tesla_pick_vehicle", new=AsyncMock(return_value=("fleet", vehicle))),
            patch("integrations.tesla._tesla_access_token", new=AsyncMock(return_value="tok")),
        ):
            result = asyncio.run(_execute_tesla_tool({"tesla_method": "fleet", "tesla_fleet_refresh_token": "ft"}, "get_vehicle_status", {}, "u1"))
        assert "asleep" in result

    def test_fleet_set_climate_with_temperature(self):
        vehicle = {"vin": "5YJ123", "display_name": "Model Y", "state": "online"}
        mock_cmd = AsyncMock(return_value={"result": True})
        with (
            patch("integrations.tesla._tesla_pick_vehicle", new=AsyncMock(return_value=("fleet", vehicle))),
            patch("integrations.tesla._tesla_access_token", new=AsyncMock(return_value="tok")),
            patch("integrations.tesla._tesla_cmd", new=mock_cmd),
        ):
            result = asyncio.run(_execute_tesla_tool({"tesla_method": "fleet", "tesla_fleet_refresh_token": "ft"}, "set_climate", {"action": "start", "temperature_f": 70}, "u1"))
        assert result == "Climate started on Model Y."
        assert mock_cmd.await_count == 2

    def test_fleet_actuate_trunk(self):
        vehicle = {"vin": "5YJ123", "display_name": "Model Y", "state": "online"}
        with (
            patch("integrations.tesla._tesla_pick_vehicle", new=AsyncMock(return_value=("fleet", vehicle))),
            patch("integrations.tesla._tesla_access_token", new=AsyncMock(return_value="tok")),
            patch("integrations.tesla._tesla_cmd", new=AsyncMock(return_value={"result": True})),
        ):
            result = asyncio.run(_execute_tesla_tool({"tesla_method": "fleet", "tesla_fleet_refresh_token": "ft"}, "actuate_trunk", {"which": "rear"}, "u1"))
        assert result == "Rear trunk command sent to Model Y."


class TestTeslaLowLevel:
    def test_access_token_uses_cache_when_valid(self):
        future_expiry = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=1)
        with patch.object(tesla_mod, "_tesla_tokens", {"u1": {"unofficial_access": "cached-tok", "unofficial_expiry": future_expiry}}):
            token = asyncio.run(tesla_mod._tesla_access_token("unofficial", "u1", {}))
        assert token == "cached-tok"

    def test_access_token_refreshes_unofficial(self):
        pool, conn = _mock_asyncpg_pool()
        cfg = {"tesla_refresh_token": "old-rt"}
        with (
            patch.object(tesla_mod, "_tesla_tokens", {}),
            patch("integrations.tesla._pool", return_value=pool),
            patch(
                "integrations.tesla.refresh_oauth_token",
                new=AsyncMock(return_value={"access_token": "new-tok", "refresh_token": "new-rt", "expires_in": 28800}),
            ),
        ):
            token = asyncio.run(tesla_mod._tesla_access_token("unofficial", "u1", cfg))
        assert token == "new-tok"
        assert cfg["tesla_refresh_token"] == "new-rt"
        conn.execute.assert_awaited_once()

    def test_access_token_refreshes_fleet(self):
        pool, conn = _mock_asyncpg_pool()
        cfg = {"tesla_fleet_refresh_token": "old-rt"}
        with (
            patch.object(tesla_mod, "_tesla_tokens", {}),
            patch("integrations.tesla._pool", return_value=pool),
            patch(
                "integrations.tesla.refresh_oauth_token",
                new=AsyncMock(return_value={"access_token": "new-tok", "refresh_token": "new-rt", "expires_in": 28800}),
            ),
        ):
            token = asyncio.run(tesla_mod._tesla_access_token("fleet", "u1", cfg))
        assert token == "new-tok"
        assert cfg["tesla_fleet_refresh_token"] == "new-rt"

    def test_vehicles_fetches_from_api(self):
        resp = MagicMock(status_code=200)
        resp.raise_for_status = MagicMock()
        resp.json = MagicMock(return_value={"response": [{"id": 1, "display_name": "Model 3"}]})
        with (
            patch("integrations.tesla._tesla_access_token", new=AsyncMock(return_value="tok")),
            patch("httpx.AsyncClient", return_value=_mock_async_client(get=resp)),
        ):
            vehicles = asyncio.run(tesla_mod._tesla_vehicles("unofficial", "u1", {}))
        assert vehicles == [{"id": 1, "display_name": "Model 3"}]

    def test_wake_returns_true_when_online(self):
        resp = MagicMock(status_code=200)
        resp.json = MagicMock(return_value={"response": {"state": "online"}})
        with patch("httpx.AsyncClient", return_value=_mock_async_client(post=resp)):
            result = asyncio.run(tesla_mod._tesla_wake("unofficial", 1, "tok"))
        assert result is True

    def test_wake_returns_false_after_retries_exhausted(self):
        resp = MagicMock(status_code=200)
        resp.json = MagicMock(return_value={"response": {"state": "asleep"}})
        with (
            patch("httpx.AsyncClient", return_value=_mock_async_client(post=resp)),
            patch("integrations.tesla.asyncio.sleep", new=AsyncMock()),
        ):
            result = asyncio.run(tesla_mod._tesla_wake("unofficial", 1, "tok"))
        assert result is False

    def test_cmd_wakes_and_sends_command(self):
        resp = MagicMock(status_code=200)
        resp.raise_for_status = MagicMock()
        resp.json = MagicMock(return_value={"response": {"result": True}})
        with (
            patch("integrations.tesla._tesla_access_token", new=AsyncMock(return_value="tok")),
            patch("integrations.tesla._tesla_wake", new=AsyncMock(return_value=True)),
            patch("httpx.AsyncClient", return_value=_mock_async_client(post=resp)),
        ):
            result = asyncio.run(tesla_mod._tesla_cmd("unofficial", "u1", {}, 1, "door_lock"))
        assert result == {"result": True}


class TestTeslaPickVehicle:
    def test_prefers_unofficial_when_available(self):
        vehicles = [{"id": 1, "display_name": "Model 3"}]
        with patch("integrations.tesla._tesla_vehicles", new=AsyncMock(return_value=vehicles)):
            method, vehicle = asyncio.run(_tesla_pick_vehicle("u1", {"tesla_method": "unofficial"}))
        assert method == "unofficial"
        assert vehicle["display_name"] == "Model 3"

    def test_falls_back_to_fleet_when_both_and_unofficial_fails(self):
        vehicles_fleet = [{"vin": "v1", "display_name": "Model Y"}]

        async def fake_vehicles(method, user_id, config):
            if method == "unofficial":
                raise Exception("unofficial down")
            return vehicles_fleet

        with patch("integrations.tesla._tesla_vehicles", new=fake_vehicles):
            method, vehicle = asyncio.run(_tesla_pick_vehicle("u1", {"tesla_method": "both"}))
        assert method == "fleet"
        assert vehicle["vin"] == "v1"

    def test_unofficial_error_propagates_when_method_is_unofficial(self):
        with patch("integrations.tesla._tesla_vehicles", new=AsyncMock(side_effect=Exception("unofficial down"))):
            try:
                asyncio.run(_tesla_pick_vehicle("u1", {"tesla_method": "unofficial"}))
                raise AssertionError("expected Exception")
            except Exception as e:
                assert str(e) == "unofficial down"

    def test_no_vehicles_raises(self):
        with patch("integrations.tesla._tesla_vehicles", new=AsyncMock(return_value=[])):
            try:
                asyncio.run(_tesla_pick_vehicle("u1", {"tesla_method": "fleet"}))
                raise AssertionError("expected ValueError")
            except ValueError as e:
                assert "No Tesla vehicle found" in str(e)

    def test_matches_by_name_hint(self):
        vehicles = [{"id": 1, "display_name": "Model 3"}, {"id": 2, "display_name": "Model Y"}]
        with patch("integrations.tesla._tesla_vehicles", new=AsyncMock(return_value=vehicles)):
            method, vehicle = asyncio.run(_tesla_pick_vehicle("u1", {"tesla_method": "unofficial"}, name_hint="Y"))
        assert vehicle["display_name"] == "Model Y"


class TestApiTeslaRoutes:
    def test_status(self, api_client):
        _seed_user_state(config={"tesla_method": "unofficial", "tesla_refresh_token": "rt"})
        resp = api_client.get("/api/tesla/status")
        assert resp.json()["tesla_configured"] is True

    def test_save_unofficial_missing_token(self, api_client):
        resp = api_client.post("/api/tesla/save_unofficial", json={"refresh_token": ""})
        assert resp.json() == {"ok": False, "error": "No refresh token provided."}

    def test_save_unofficial_success(self, api_client):
        _seed_user_state(config={})
        resp_vehicles = MagicMock(status_code=200)
        resp_vehicles.raise_for_status = MagicMock()
        pool, conn = _mock_asyncpg_pool()
        with (
            patch.object(jarvis, "_tesla_access_token", new=AsyncMock(return_value="tok")),
            patch("httpx.AsyncClient", return_value=_mock_async_client(get=resp_vehicles)),
            patch.object(jarvis, "_pool", return_value=pool),
        ):
            resp = api_client.post("/api/tesla/save_unofficial", json={"refresh_token": "rt"})
        assert resp.json()["ok"] is True

    def test_save_unofficial_connect_failure(self, api_client):
        _seed_user_state(config={})
        with patch.object(jarvis, "_tesla_access_token", new=AsyncMock(side_effect=Exception("bad token"))):
            resp = api_client.post("/api/tesla/save_unofficial", json={"refresh_token": "rt"})
        assert resp.json()["ok"] is False

    def test_fleet_auth_not_configured(self, api_client):
        with patch.object(jarvis, "TESLA_CLIENT_ID", ""):
            resp = api_client.get("/api/tesla/fleet/auth")
        assert resp.status_code == 503

    def test_fleet_auth_redirect(self, api_client):
        with patch.object(jarvis, "TESLA_CLIENT_ID", "cid"):
            resp = api_client.get("/api/tesla/fleet/auth", follow_redirects=False)
        assert resp.status_code in (302, 307)
        assert resp.headers["location"].startswith(jarvis._tesla_mod._TESLA_AUTH_BASE)

    def test_callback_invalid_state(self, api_client):
        resp = api_client.get("/auth/tesla/callback?code=abc&state=bogus")
        assert resp.status_code == 400

    def test_callback_success(self, api_client):
        _seed_user_state(config={})
        jarvis._tesla_mod._tesla_auth_pending["state1"] = {"user_id": "local", "code_verifier": "verifier"}
        token_resp = MagicMock(status_code=200)
        token_resp.raise_for_status = MagicMock()
        token_resp.json = MagicMock(return_value={"refresh_token": "fleet-rt"})
        pool, conn = _mock_asyncpg_pool()
        with (
            patch("httpx.AsyncClient", return_value=_mock_async_client(post=token_resp)),
            patch.object(jarvis, "_pool", return_value=pool),
        ):
            resp = api_client.get("/auth/tesla/callback?code=abc&state=state1", follow_redirects=False)
        assert resp.status_code == 303
        assert resp.headers["location"] == "/?tesla_connected=1"

    def test_disconnect(self, api_client):
        _seed_user_state(config={"tesla_refresh_token": "rt", "tesla_fleet_refresh_token": "ft"})
        pool, conn = _mock_asyncpg_pool()
        with patch.object(jarvis, "_pool", return_value=pool):
            resp = api_client.post("/api/tesla/disconnect", json={"which": "all"})
        assert resp.json()["ok"] is True
        assert resp.json()["tesla_configured"] is False
