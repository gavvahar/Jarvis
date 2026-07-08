import asyncio, itertools, json

import aiohttp

from config import MATTER_SERVER_URL
from tool_schemas import anthropic_tools_to_openai

# Matter/ZCL cluster and attribute IDs (decimal) used by the wire protocol
# path format "{endpoint}/{cluster_id}/{attribute_id}", per python-matter-server.
_ONOFF_CLUSTER = 6
_ONOFF_ATTR = 0
_LEVEL_CONTROL_CLUSTER = 8
_CURRENT_LEVEL_ATTR = 0
_BASIC_INFO_CLUSTER = 40
_NODE_LABEL_ATTR = 5
_PRODUCT_NAME_ATTR = 3

_msg_id_counter = itertools.count(1)

_MATTER_TOOL_ANTHROPIC = {
    "name": "matter_control",
    "description": (
        "Control Matter/Thread smart home devices via a self-hosted python-matter-server instance, "
        "for devices not exposed through Home Assistant. Call with action='list_nodes' first to "
        "discover node_ids and names."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["list_nodes", "get_state", "on", "off", "toggle", "set_level"],
            },
            "node_id": {"type": "integer", "description": "Matter node ID, from list_nodes"},
            "endpoint_id": {"type": "integer", "description": "Endpoint on the node (default 1)"},
            "level_percent": {"type": "integer", "description": "Brightness/level 0-100, for set_level"},
        },
        "required": ["action"],
    },
}

_MATTER_TOOL_OPENAI = anthropic_tools_to_openai([_MATTER_TOOL_ANTHROPIC])[0]


def _matter_configured() -> bool:
    return bool(MATTER_SERVER_URL)


def _get_matter_tools(provider: str) -> list:
    if not _matter_configured():
        return []
    return [_MATTER_TOOL_ANTHROPIC] if provider == "anthropic" else [_MATTER_TOOL_OPENAI]


async def _matter_ws_command(command: str, args: dict):
    message_id = str(next(_msg_id_counter))
    async with asyncio.timeout(10):
        async with aiohttp.ClientSession() as session:
            async with session.ws_connect(MATTER_SERVER_URL) as ws:
                await ws.receive()  # initial server info greeting
                await ws.send_str(json.dumps({"message_id": message_id, "command": command, "args": args}))
                async for msg in ws:
                    if msg.type != aiohttp.WSMsgType.TEXT:
                        continue
                    data = json.loads(msg.data)
                    if data.get("message_id") != message_id:
                        continue
                    if "error_code" in data:
                        raise RuntimeError(data.get("details") or f"Matter server error {data['error_code']}")
                    return data.get("result")
    raise RuntimeError("No response from Matter server.")


async def _execute_matter_tool(args: dict) -> str:
    if not _matter_configured():
        return "Matter server not configured."
    action = (args.get("action") or "").lower()
    endpoint_id = int(args.get("endpoint_id") or 1)
    try:
        if action == "list_nodes":
            nodes = await _matter_ws_command("get_nodes", {})
            if not nodes:
                return "No Matter nodes commissioned."
            lines = []
            for n in nodes:
                attrs = n.get("attributes", {})
                label = attrs.get(f"0/{_BASIC_INFO_CLUSTER}/{_NODE_LABEL_ATTR}") or attrs.get(f"0/{_BASIC_INFO_CLUSTER}/{_PRODUCT_NAME_ATTR}") or f"node {n['node_id']}"
                status = "online" if n.get("available") else "offline"
                lines.append(f"[{n['node_id']}] {label} ({status})")
            return "\n".join(lines)

        node_id = args.get("node_id")
        if not node_id:
            return "Specify a node_id (use action='list_nodes' to find one)."
        node_id = int(node_id)

        if action == "get_state":
            onoff_path = f"{endpoint_id}/{_ONOFF_CLUSTER}/{_ONOFF_ATTR}"
            level_path = f"{endpoint_id}/{_LEVEL_CONTROL_CLUSTER}/{_CURRENT_LEVEL_ATTR}"
            result = await _matter_ws_command("read_attribute", {"node_id": node_id, "attribute_path": [onoff_path, level_path]})
            on = result.get(onoff_path)
            level = result.get(level_path)
            parts = []
            if on is not None:
                parts.append("on" if on else "off")
            if level is not None:
                parts.append(f"level {round(level / 254 * 100)}%")
            return f"Node {node_id}: {', '.join(parts)}" if parts else f"Node {node_id} has no on/off or level attributes."

        if action in ("on", "off", "toggle"):
            await _matter_ws_command(
                "device_command",
                {
                    "node_id": node_id,
                    "endpoint_id": endpoint_id,
                    "cluster_id": _ONOFF_CLUSTER,
                    "command_name": action.capitalize(),
                    "payload": {},
                },
            )
            return f"Node {node_id} toggled." if action == "toggle" else f"Node {node_id} turned {action}."

        if action == "set_level":
            pct = args.get("level_percent")
            if pct is None:
                return "Specify level_percent (0-100)."
            level = max(0, min(254, round(float(pct) / 100 * 254)))
            await _matter_ws_command(
                "device_command",
                {
                    "node_id": node_id,
                    "endpoint_id": endpoint_id,
                    "cluster_id": _LEVEL_CONTROL_CLUSTER,
                    "command_name": "MoveToLevelWithOnOff",
                    "payload": {"level": level, "transitionTime": 0, "optionsMask": 0, "optionsOverride": 0},
                },
            )
            return f"Node {node_id} set to {pct}%."

        return f"Unknown action: {action}"
    except TimeoutError:
        return "Matter server did not respond in time."
    except Exception as e:
        return f"Matter error: {e}"
