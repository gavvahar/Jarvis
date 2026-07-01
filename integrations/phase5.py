import asyncio, datetime, json

from config import MQTT_BROKER, MQTT_PASSWORD, MQTT_PORT, MQTT_USER, Z2M_BASE_TOPIC
from db import (
    _db_create_device_alert,
    _db_create_routine,
    _db_delete_device_alert,
    _db_delete_routine,
    _db_get_active_device_alerts,
    _db_list_device_alerts,
    _db_list_routines,
    _db_ready,
    _db_update_alert_last_fired,
)
from integrations.ha import _ha_call_service, _ha_configured, _ha_get_entity_state

_sio = None
_sids_fn = None
_user_states_ref: dict = {}


def init(sio, sids_fn, user_states):
    global _sio, _sids_fn, _user_states_ref
    _sio = sio
    _sids_fn = sids_fn
    _user_states_ref = user_states


# ── Tool schemas ───────────────────────────────────────────────────────────────

_ROUTINE_TOOL_ANTHROPIC = {
    "name": "manage_routine",
    "description": (
        "Create, list, delete, or run named routines. A routine is a sequence of steps "
        "(ha_service, speak, delay) triggered by voice phrases. "
        "Steps: ha_service={domain,service,entity_id?,service_data?}, speak={text}, delay={seconds}."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": ["create", "list", "delete", "run"]},
            "name": {"type": "string", "description": "Routine name"},
            "trigger_phrases": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Voice phrases that trigger this routine",
            },
            "steps": {
                "type": "array",
                "description": "Ordered steps to execute",
                "items": {"type": "object"},
            },
            "routine_id": {"type": "integer", "description": "ID to delete"},
        },
        "required": ["action"],
    },
}

_ROUTINE_TOOL_OPENAI = {
    "type": "function",
    "function": {
        "name": "manage_routine",
        "description": "Create, list, delete, or run named routines (ha_service/speak/delay steps).",
        "parameters": {
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": ["create", "list", "delete", "run"]},
                "name": {"type": "string"},
                "trigger_phrases": {"type": "array", "items": {"type": "string"}},
                "steps": {"type": "array", "items": {"type": "object"}},
                "routine_id": {"type": "integer"},
            },
            "required": ["action"],
        },
    },
}

_DEVICE_ALERT_TOOL_ANTHROPIC = {
    "name": "manage_device_alert",
    "description": ("Create, list, or delete proactive device alert rules. When an HA entity's state matches the condition, Jarvis speaks the alert message."),
    "input_schema": {
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": ["create", "list", "delete"]},
            "name": {"type": "string", "description": "Human-readable alert name"},
            "entity_id": {"type": "string", "description": "HA entity to monitor, e.g. sensor.front_door"},
            "condition": {
                "type": "string",
                "enum": ["equals", "not_equals", "greater_than", "less_than"],
                "description": "Comparison operator",
            },
            "value": {"type": "string", "description": "Target state value to compare against"},
            "message": {"type": "string", "description": "What Jarvis should say when the alert fires"},
            "cooldown_minutes": {"type": "integer", "description": "Minutes before re-alerting (default 30)"},
            "alert_id": {"type": "integer", "description": "Alert ID to delete"},
        },
        "required": ["action"],
    },
}

_DEVICE_ALERT_TOOL_OPENAI = {
    "type": "function",
    "function": {
        "name": "manage_device_alert",
        "description": "Create, list, or delete proactive HA device alert rules.",
        "parameters": {
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": ["create", "list", "delete"]},
                "name": {"type": "string"},
                "entity_id": {"type": "string"},
                "condition": {"type": "string", "enum": ["equals", "not_equals", "greater_than", "less_than"]},
                "value": {"type": "string"},
                "message": {"type": "string"},
                "cooldown_minutes": {"type": "integer"},
                "alert_id": {"type": "integer"},
            },
            "required": ["action"],
        },
    },
}

_ZIGBEE_TOOL_ANTHROPIC = {
    "name": "zigbee_control",
    "description": ("Send a command to a Zigbee device via Zigbee2MQTT. Use for devices not in Home Assistant. Payload is merged into the set topic."),
    "input_schema": {
        "type": "object",
        "properties": {
            "device": {"type": "string", "description": "Zigbee2MQTT device friendly name"},
            "payload": {"type": "object", "description": 'Command payload, e.g. {"state": "ON", "brightness": 128}'},
        },
        "required": ["device", "payload"],
    },
}

_ZIGBEE_TOOL_OPENAI = {
    "type": "function",
    "function": {
        "name": "zigbee_control",
        "description": "Send a command to a Zigbee device via Zigbee2MQTT.",
        "parameters": {
            "type": "object",
            "properties": {
                "device": {"type": "string"},
                "payload": {"type": "object"},
            },
            "required": ["device", "payload"],
        },
    },
}


# ── Tool getters ───────────────────────────────────────────────────────────────


def _get_phase5_tools(config: dict, provider: str) -> list:
    tools = []
    if _ha_configured(config):
        if provider == "anthropic":
            tools += [_ROUTINE_TOOL_ANTHROPIC, _DEVICE_ALERT_TOOL_ANTHROPIC]
        else:
            tools += [_ROUTINE_TOOL_OPENAI, _DEVICE_ALERT_TOOL_OPENAI]
    if MQTT_BROKER:
        tools.append(_ZIGBEE_TOOL_ANTHROPIC if provider == "anthropic" else _ZIGBEE_TOOL_OPENAI)
    return tools


# ── Execution ──────────────────────────────────────────────────────────────────


async def _run_routine(user_id: str, config: dict, steps: list) -> None:
    if not _sids_fn:
        return
    sids = _sids_fn(user_id)
    for i, step in enumerate(steps):
        step_type = (step.get("type") or "").lower()
        try:
            if step_type == "ha_service" and _ha_configured(config):
                await _ha_call_service(
                    config,
                    step.get("domain", ""),
                    step.get("service", ""),
                    step.get("entity_id"),
                    step.get("service_data"),
                )
            elif step_type == "speak":
                text = (step.get("text") or "").strip()
                if text and _sio:
                    for sid in sids:
                        await _sio.emit("speak_sentence", {"text": text, "seq": i}, to=sid)
            elif step_type == "delay":
                secs = float(step.get("seconds") or 0)
                if secs > 0:
                    await asyncio.sleep(min(secs, 300))
        except Exception as e:
            print(f"[ROUTINE] Step {i} ({step_type}) error: {e}", flush=True)


async def _execute_routine_tool(user_id: str, args: dict, config: dict) -> str:
    action = (args.get("action") or "").lower()
    if action == "create":
        name = (args.get("name") or "").strip()
        if not name:
            return "Specify a routine name."
        phrases = args.get("trigger_phrases") or []
        steps = args.get("steps") or []
        if not steps:
            return "Specify at least one step."
        rid = await _db_create_routine(user_id, name, phrases, steps)
        phrase_str = ", ".join(f'"{p}"' for p in phrases[:3]) if phrases else "none"
        return f"Routine '{name}' created with {len(steps)} step(s). Trigger phrases: {phrase_str}. ID: {rid}."
    if action == "list":
        routines = await _db_list_routines(user_id)
        if not routines:
            return "No routines configured."
        return "\n".join(
            f"[{r['id']}] {r['name']} ({'active' if r['active'] else 'disabled'}) — {len(r['steps'])} steps, phrases: {', '.join(r['trigger_phrases']) or 'none'}" for r in routines
        )
    if action == "delete":
        rid = args.get("routine_id")
        if not rid:
            return "Specify a routine_id to delete."
        ok = await _db_delete_routine(user_id, int(rid))
        return "Routine deleted." if ok else "Routine not found."
    if action == "run":
        name = (args.get("name") or "").strip()
        routines = await _db_list_routines(user_id)
        routine = next((r for r in routines if r["name"].lower() == name.lower()), None)
        if not routine:
            return f"No routine named '{name}'."
        asyncio.create_task(_run_routine(user_id, config, routine["steps"]))
        return f"Running routine '{name}'."
    return f"Unknown action: {action}"


async def _execute_device_alert_tool(user_id: str, args: dict) -> str:
    action = (args.get("action") or "").lower()
    if action == "create":
        name = (args.get("name") or "").strip()
        entity_id = (args.get("entity_id") or "").strip()
        condition = (args.get("condition") or "equals").strip()
        value = str(args.get("value") or "").strip()
        message = (args.get("message") or "").strip()
        cooldown = int(args.get("cooldown_minutes") or 30)
        if not all([name, entity_id, message]):
            return "Specify name, entity_id, and message."
        aid = await _db_create_device_alert(user_id, name, entity_id, condition, value, message, cooldown)
        return f"Alert '{name}' created (ID: {aid}). Will notify when {entity_id} {condition} '{value}'."
    if action == "list":
        alerts = await _db_list_device_alerts(user_id)
        if not alerts:
            return "No alert rules configured."
        return "\n".join(
            f"[{a['id']}] {a['name']} — {a['entity_id']} {a['condition']} '{a['value']}' ({'active' if a['active'] else 'disabled'}, cooldown {a['cooldown_minutes']}m)"
            for a in alerts
        )
    if action == "delete":
        aid = args.get("alert_id")
        if not aid:
            return "Specify an alert_id to delete."
        ok = await _db_delete_device_alert(user_id, int(aid))
        return "Alert deleted." if ok else "Alert not found."
    return f"Unknown action: {action}"


async def _execute_zigbee_tool(args: dict) -> str:
    if not MQTT_BROKER:
        return "MQTT broker not configured."
    device = (args.get("device") or "").strip()
    payload = args.get("payload") or {}
    if not device:
        return "Specify a device name."
    try:
        import aiomqtt

        topic = f"{Z2M_BASE_TOPIC}/{device}/set"
        async with aiomqtt.Client(
            hostname=MQTT_BROKER,
            port=MQTT_PORT,
            username=MQTT_USER or None,
            password=MQTT_PASSWORD or None,
        ) as client:
            await client.publish(topic, json.dumps(payload))
        return f"Command sent to {device}: {payload}"
    except ImportError:
        return "aiomqtt not installed — Zigbee control unavailable."
    except Exception as e:
        return f"MQTT error: {e}"


def _evaluate_alert_condition(state: str, condition: str, value: str) -> bool:
    if condition == "equals":
        return state.lower() == value.lower()
    if condition == "not_equals":
        return state.lower() != value.lower()
    try:
        sn, vn = float(state), float(value)
        if condition == "greater_than":
            return sn > vn
        if condition == "less_than":
            return sn < vn
    except (ValueError, TypeError):
        pass
    return False


async def _device_alert_loop():
    while True:
        await asyncio.sleep(120)
        if not _db_ready():
            continue
        try:
            alerts = await _db_get_active_device_alerts()
            if not alerts:
                continue
            now_utc = datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)
            for alert in alerts:
                uid = alert["user_id"]
                state = _user_states_ref.get(uid)
                if not state or not _ha_configured(state["config"]):
                    continue
                last_fired = alert.get("last_fired")
                if last_fired:
                    elapsed = now_utc - last_fired.replace(tzinfo=None)
                    if elapsed < datetime.timedelta(minutes=alert["cooldown_minutes"]):
                        continue
                entity_state = await _ha_get_entity_state(state["config"], alert["entity_id"])
                if entity_state is None:
                    continue
                if _evaluate_alert_condition(entity_state, alert["condition"], alert["value"]):
                    await _db_update_alert_last_fired(alert["id"])
                    speak = alert["message"]
                    if _sids_fn is not None:
                        for sid in _sids_fn(uid):
                            if _sio is not None:
                                await _sio.emit(
                                    "device_alert",
                                    {"name": alert["name"], "message": speak, "speak": speak},
                                    to=sid,
                                )
        except Exception as e:
            print(f"[ALERT] {e}", flush=True)
