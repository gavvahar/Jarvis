"""Tests for integrations/automation.py — routines, device alerts, Zigbee."""

import asyncio, db as db_mod, integrations.automation as automation_mod

from unittest.mock import AsyncMock, MagicMock, patch
from integrations.automation import _evaluate_alert_condition, _execute_device_alert_tool, _execute_routine_tool, _get_automation_tools
from helpers import _mock_async_client, _mock_asyncpg_pool


class TestEvaluateAlertCondition:
    def test_equals_match(self):
        assert _evaluate_alert_condition("on", "equals", "on") is True

    def test_equals_case_insensitive(self):
        assert _evaluate_alert_condition("ON", "equals", "on") is True

    def test_equals_no_match(self):
        assert _evaluate_alert_condition("off", "equals", "on") is False

    def test_not_equals_match(self):
        assert _evaluate_alert_condition("off", "not_equals", "on") is True

    def test_not_equals_no_match(self):
        assert _evaluate_alert_condition("on", "not_equals", "on") is False

    def test_greater_than_true(self):
        assert _evaluate_alert_condition("30", "greater_than", "25") is True

    def test_greater_than_false(self):
        assert _evaluate_alert_condition("20", "greater_than", "25") is False

    def test_less_than_true(self):
        assert _evaluate_alert_condition("10", "less_than", "20") is True

    def test_less_than_false(self):
        assert _evaluate_alert_condition("30", "less_than", "20") is False

    def test_numeric_condition_non_numeric_state_returns_false(self):
        assert _evaluate_alert_condition("unavailable", "greater_than", "25") is False

    def test_unknown_condition_returns_false(self):
        assert _evaluate_alert_condition("on", "contains", "on") is False


class TestGetPhase5Tools:
    _ha_cfg = {"ha_url": "http://ha.local", "ha_token": "tok"}
    _no_ha = {"ha_url": "", "ha_token": ""}

    def test_empty_when_no_ha_and_no_mqtt(self):
        with patch("integrations.automation.MQTT_BROKER", ""):
            tools = _get_automation_tools(self._no_ha, "anthropic")
        assert tools == []

    def test_ha_tools_included_when_configured(self):
        with patch("integrations.automation.MQTT_BROKER", ""):
            tools = _get_automation_tools(self._ha_cfg, "anthropic")
        names = {t["name"] for t in tools}
        assert "manage_routine" in names
        assert "manage_device_alert" in names

    def test_openai_format_when_provider_openai(self):
        with patch("integrations.automation.MQTT_BROKER", ""):
            tools = _get_automation_tools(self._ha_cfg, "openai")
        assert all(t["type"] == "function" for t in tools)

    def test_zigbee_tool_added_when_mqtt_configured(self):
        with patch("integrations.automation.MQTT_BROKER", "mqtt.local"):
            tools = _get_automation_tools(self._no_ha, "anthropic")
        names = {t["name"] for t in tools}
        assert "zigbee_control" in names


class TestExecuteRoutineToolMocked:
    _cfg = {"ha_url": "http://ha.local", "ha_token": "tok"}

    def test_create_routine(self):
        with patch("integrations.automation._db_create_routine", new=AsyncMock(return_value=5)):
            result = asyncio.run(
                _execute_routine_tool(
                    "u1",
                    {"action": "create", "name": "Good Morning", "steps": [{"type": "speak", "text": "Good morning!"}]},
                    self._cfg,
                )
            )
        assert "Good Morning" in result
        assert "5" in result

    def test_create_routine_no_name(self):
        result = asyncio.run(_execute_routine_tool("u1", {"action": "create"}, self._cfg))
        assert "name" in result.lower()

    def test_create_routine_no_steps(self):
        result = asyncio.run(_execute_routine_tool("u1", {"action": "create", "name": "Empty"}, self._cfg))
        assert "step" in result.lower()

    def test_list_no_routines(self):
        with patch("integrations.automation._db_list_routines", new=AsyncMock(return_value=[])):
            result = asyncio.run(_execute_routine_tool("u1", {"action": "list"}, self._cfg))
        assert "No routines" in result

    def test_delete_routine(self):
        with patch("integrations.automation._db_delete_routine", new=AsyncMock(return_value=True)):
            result = asyncio.run(_execute_routine_tool("u1", {"action": "delete", "routine_id": 1}, self._cfg))
        assert "deleted" in result.lower()

    def test_run_routine_not_found(self):
        with patch("integrations.automation._db_list_routines", new=AsyncMock(return_value=[])):
            result = asyncio.run(_execute_routine_tool("u1", {"action": "run", "name": "Nonexistent"}, self._cfg))
        assert "No routine" in result

    def test_list_formats_routines(self):
        routines = [{"id": 1, "name": "Good Morning", "active": True, "steps": [{"type": "speak"}], "trigger_phrases": ["good morning"]}]
        with patch("integrations.automation._db_list_routines", new=AsyncMock(return_value=routines)):
            result = asyncio.run(_execute_routine_tool("u1", {"action": "list"}, self._cfg))
        assert "Good Morning" in result
        assert "good morning" in result

    def test_delete_routine_no_id(self):
        result = asyncio.run(_execute_routine_tool("u1", {"action": "delete"}, self._cfg))
        assert "Specify" in result

    def test_run_routine_found_schedules_task(self):
        routines = [{"id": 1, "name": "Good Night", "active": True, "steps": [{"type": "speak", "text": "Night"}], "trigger_phrases": []}]
        with patch("integrations.automation._db_list_routines", new=AsyncMock(return_value=routines)):
            result = asyncio.run(_execute_routine_tool("u1", {"action": "run", "name": "good night"}, self._cfg))
        assert "Running routine 'good night'" in result


class TestExecuteDeviceAlertToolMocked:
    def test_create_alert(self):
        with patch("integrations.automation._db_create_device_alert", new=AsyncMock(return_value=3)):
            result = asyncio.run(
                _execute_device_alert_tool(
                    "u1",
                    {
                        "action": "create",
                        "name": "Garage open",
                        "entity_id": "cover.garage",
                        "condition": "equals",
                        "value": "open",
                        "message": "The garage door is open!",
                    },
                )
            )
        assert "Garage open" in result
        assert "3" in result

    def test_create_alert_missing_fields(self):
        result = asyncio.run(_execute_device_alert_tool("u1", {"action": "create", "name": "x"}))
        assert "Specify" in result

    def test_list_no_alerts(self):
        with patch("integrations.automation._db_list_device_alerts", new=AsyncMock(return_value=[])):
            result = asyncio.run(_execute_device_alert_tool("u1", {"action": "list"}))
        assert "No alert" in result

    def test_delete_alert(self):
        with patch("integrations.automation._db_delete_device_alert", new=AsyncMock(return_value=True)):
            result = asyncio.run(_execute_device_alert_tool("u1", {"action": "delete", "alert_id": 2}))
        assert "deleted" in result.lower()

    def test_delete_no_id(self):
        result = asyncio.run(_execute_device_alert_tool("u1", {"action": "delete"}))
        assert "Specify" in result

    def test_unknown_action(self):
        result = asyncio.run(_execute_device_alert_tool("u1", {"action": "whatever"}))
        assert "Unknown" in result


class TestRunRoutine:
    def test_noop_when_not_initialized(self):
        with patch.object(automation_mod, "_sids_fn", None):
            asyncio.run(automation_mod._run_routine("u1", {}, [{"type": "speak", "text": "hi"}]))

    def test_executes_ha_service_speak_and_delay_steps(self):
        sio = MagicMock()
        sio.emit = AsyncMock()
        automation_mod.init(sio, lambda uid: ["sid1"], {})
        cfg = {"ha_url": "http://ha.local", "ha_token": "tok"}
        mock_call_service = AsyncMock()
        steps = [
            {"type": "ha_service", "domain": "light", "service": "turn_off"},
            {"type": "speak", "text": "Goodnight"},
            {"type": "delay", "seconds": 1},
        ]
        with (
            patch("integrations.automation._ha_call_service", new=mock_call_service),
            patch("integrations.automation.asyncio.sleep", new=AsyncMock()) as mock_sleep,
        ):
            asyncio.run(automation_mod._run_routine("u1", cfg, steps))
        mock_call_service.assert_awaited_once()
        sio.emit.assert_awaited_once_with("speak_sentence", {"text": "Goodnight", "seq": 1}, to="sid1")
        mock_sleep.assert_awaited_once_with(1)

    def test_step_exception_is_caught_and_logged(self):
        sio = MagicMock()
        sio.emit = AsyncMock()
        automation_mod.init(sio, lambda uid: ["sid1"], {})
        cfg = {"ha_url": "http://ha.local", "ha_token": "tok"}
        with patch("integrations.automation._ha_call_service", new=AsyncMock(side_effect=Exception("boom"))):
            asyncio.run(automation_mod._run_routine("u1", cfg, [{"type": "ha_service", "domain": "light", "service": "turn_on"}]))


class TestExecuteZigbeeTool:
    def test_not_configured(self):
        with patch("integrations.automation.MQTT_BROKER", ""):
            result = asyncio.run(automation_mod._execute_zigbee_tool({"device": "lamp", "payload": {"state": "ON"}}))
        assert "not configured" in result

    def test_missing_device(self):
        with patch("integrations.automation.MQTT_BROKER", "mqtt.local"):
            result = asyncio.run(automation_mod._execute_zigbee_tool({"payload": {}}))
        assert "Specify a device" in result

    def test_import_error_when_aiomqtt_missing(self):
        with (
            patch("integrations.automation.MQTT_BROKER", "mqtt.local"),
            patch.dict("sys.modules", {"aiomqtt": None}),
        ):
            result = asyncio.run(automation_mod._execute_zigbee_tool({"device": "lamp", "payload": {"state": "ON"}}))
        assert "aiomqtt not installed" in result

    def test_publishes_command_successfully(self):
        mock_client = _mock_async_client(publish=AsyncMock())
        mock_aiomqtt = MagicMock()
        mock_aiomqtt.Client = MagicMock(return_value=mock_client)
        with (
            patch("integrations.automation.MQTT_BROKER", "mqtt.local"),
            patch.dict("sys.modules", {"aiomqtt": mock_aiomqtt}),
        ):
            result = asyncio.run(automation_mod._execute_zigbee_tool({"device": "lamp", "payload": {"state": "ON"}}))
        assert "Command sent to lamp" in result
        mock_client.publish.assert_awaited_once()

    def test_mqtt_error_wrapped(self):
        mock_aiomqtt = MagicMock()
        mock_aiomqtt.Client = MagicMock(side_effect=Exception("connection refused"))
        with (
            patch("integrations.automation.MQTT_BROKER", "mqtt.local"),
            patch.dict("sys.modules", {"aiomqtt": mock_aiomqtt}),
        ):
            result = asyncio.run(automation_mod._execute_zigbee_tool({"device": "lamp", "payload": {"state": "ON"}}))
        assert "MQTT error" in result


class TestDeviceAlertLoop:
    def test_skips_when_db_not_ready(self):
        call_count = 0

        async def fake_sleep(secs):
            nonlocal call_count
            call_count += 1
            if call_count > 1:
                raise RuntimeError("stop-loop")

        with (
            patch("integrations.automation.asyncio.sleep", new=fake_sleep),
            patch("integrations.automation._db_ready", return_value=False),
        ):
            try:
                asyncio.run(automation_mod._device_alert_loop())
                raise AssertionError("expected loop to stop")
            except RuntimeError:
                pass

    def test_fires_alert_when_condition_met(self):
        call_count = 0

        async def fake_sleep(secs):
            nonlocal call_count
            call_count += 1
            if call_count > 1:
                raise RuntimeError("stop-loop")

        alert = {
            "id": 1,
            "user_id": "u1",
            "entity_id": "sensor.temp",
            "condition": "greater_than",
            "value": "75",
            "message": "It's hot",
            "name": "Heat alert",
            "cooldown_minutes": 30,
            "last_fired": None,
        }
        sio = MagicMock()
        sio.emit = AsyncMock()
        user_states = {"u1": {"config": {"ha_url": "http://ha.local", "ha_token": "tok"}}}
        automation_mod.init(sio, lambda uid: ["sid1"], user_states)

        with (
            patch("integrations.automation.asyncio.sleep", new=fake_sleep),
            patch("integrations.automation._db_ready", return_value=True),
            patch("integrations.automation._db_get_active_device_alerts", new=AsyncMock(return_value=[alert])),
            patch("integrations.automation._ha_get_entity_state", new=AsyncMock(return_value="80")),
            patch("integrations.automation._db_update_alert_last_fired", new=AsyncMock()) as mock_update,
        ):
            try:
                asyncio.run(automation_mod._device_alert_loop())
                raise AssertionError("expected loop to stop")
            except RuntimeError:
                pass
        sio.emit.assert_awaited_once()
        mock_update.assert_awaited_once_with(1)

    def test_skips_alert_when_user_state_missing(self):
        call_count = 0

        async def fake_sleep(secs):
            nonlocal call_count
            call_count += 1
            if call_count > 1:
                raise RuntimeError("stop-loop")

        alert = {
            "id": 2,
            "user_id": "unknown-user",
            "entity_id": "sensor.temp",
            "condition": "equals",
            "value": "on",
            "message": "x",
            "name": "y",
            "cooldown_minutes": 30,
            "last_fired": None,
        }
        automation_mod.init(MagicMock(), lambda uid: [], {})

        with (
            patch("integrations.automation.asyncio.sleep", new=fake_sleep),
            patch("integrations.automation._db_ready", return_value=True),
            patch("integrations.automation._db_get_active_device_alerts", new=AsyncMock(return_value=[alert])),
        ):
            try:
                asyncio.run(automation_mod._device_alert_loop())
                raise AssertionError("expected loop to stop")
            except RuntimeError:
                pass


class TestDbRoutines:
    def test_create_routine(self):
        pool, conn = _mock_asyncpg_pool(fetchrow={"id": 3})
        with patch("db._pool", return_value=pool):
            rid = asyncio.run(db_mod._db_create_routine("u1", "Good Morning", ["good morning"], [{"type": "speak"}]))
        assert rid == 3

    def test_list_routines(self):
        import json

        rows = [{"id": 1, "name": "Good Morning", "trigger_phrases": json.dumps(["hi"]), "steps": json.dumps([{"type": "speak"}]), "active": True}]
        pool, conn = _mock_asyncpg_pool(fetch=rows)
        with patch("db._pool", return_value=pool):
            result = asyncio.run(db_mod._db_list_routines("u1"))
        assert result[0]["trigger_phrases"] == ["hi"]
        assert result[0]["steps"] == [{"type": "speak"}]

    def test_delete_routine(self):
        pool, conn = _mock_asyncpg_pool(execute="DELETE 1")
        with patch("db._pool", return_value=pool):
            assert asyncio.run(db_mod._db_delete_routine("u1", 1)) is True

    def test_toggle_routine(self):
        pool, conn = _mock_asyncpg_pool(execute="UPDATE 1")
        with patch("db._pool", return_value=pool):
            assert asyncio.run(db_mod._db_toggle_routine("u1", 1, False)) is True


class TestDbDeviceAlertsCrud:
    def test_create_device_alert(self):
        pool, conn = _mock_asyncpg_pool(fetchrow={"id": 9})
        with patch("db._pool", return_value=pool):
            aid = asyncio.run(db_mod._db_create_device_alert("u1", "Heat", "sensor.temp", "greater_than", "75", "hot", 30))
        assert aid == 9

    def test_list_device_alerts(self):
        rows = [{"id": 1, "name": "Heat", "entity_id": "sensor.temp", "condition": "greater_than", "value": "75", "message": "hot", "cooldown_minutes": 30, "active": True}]
        pool, conn = _mock_asyncpg_pool(fetch=rows)
        with patch("db._pool", return_value=pool):
            result = asyncio.run(db_mod._db_list_device_alerts("u1"))
        assert result == rows

    def test_delete_device_alert(self):
        pool, conn = _mock_asyncpg_pool(execute="DELETE 1")
        with patch("db._pool", return_value=pool):
            assert asyncio.run(db_mod._db_delete_device_alert("u1", 1)) is True

    def test_get_active_device_alerts(self):
        rows = [{"id": 1, "user_id": "u1"}]
        pool, conn = _mock_asyncpg_pool(fetch=rows)
        with patch("db._pool", return_value=pool):
            result = asyncio.run(db_mod._db_get_active_device_alerts())
        assert result == rows

    def test_update_alert_last_fired(self):
        pool, conn = _mock_asyncpg_pool()
        with patch("db._pool", return_value=pool):
            asyncio.run(db_mod._db_update_alert_last_fired(1))
        conn.execute.assert_awaited_once()
