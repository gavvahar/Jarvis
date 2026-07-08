"""
Snapcast multi-room audio integration.

Snapcast is a self-hosted synchronous multi-room audio server.
Jarvis can control per-room volume, mute, and stream routing via the
Snapcast JSON-RPC HTTP API (default port 1780).

Config: SNAPCAST_URL=http://192.168.1.100:1780
"""

import httpx
from config import SNAPCAST_URL
from tool_schemas import anthropic_tools_to_openai

_SNAPCAST_TOOL_NAMES = {"snapcast_status", "snapcast_set_volume", "snapcast_mute", "snapcast_set_stream"}

_rpc_id = 0


async def _snapcast_rpc(method: str, params: dict | None = None) -> dict:
    global _rpc_id
    _rpc_id += 1
    async with httpx.AsyncClient(timeout=5) as c:
        r = await c.post(
            f"{SNAPCAST_URL}/jsonrpc",
            json={"id": _rpc_id, "jsonrpc": "2.0", "method": method, "params": params or {}},
        )
        r.raise_for_status()
        data = r.json()
        if "error" in data:
            raise ValueError(data["error"]["message"])
        return data.get("result", {})


async def _snapcast_get_status() -> str:
    result = await _snapcast_rpc("Server.GetStatus")
    server = result.get("server", {})
    streams = {s["id"]: s.get("status", {}).get("stream", {}).get("meta", {}).get("TITLE", s["id"]) for s in server.get("streams", [])}
    lines = []
    for g in server.get("groups", []):
        stream_name = streams.get(g.get("stream_id", ""), g.get("stream_id", "unknown"))
        lines.append(f"Group '{g['id']}' → stream '{stream_name}' (muted={g.get('muted', False)})")
        for client in g.get("clients", []):
            host = client.get("host", {}).get("name", client["id"])
            vol = client.get("config", {}).get("volume", {})
            lines.append(f"  • {host} (id={client['id']}) vol={vol.get('percent', 0)}% muted={vol.get('muted', False)} connected={client.get('connected', False)}")
    return "\n".join(lines) if lines else "No Snapcast groups or clients found."


def _snapcast_configured() -> bool:
    return bool(SNAPCAST_URL)


_SNAPCAST_TOOLS_ANTHROPIC = [
    {
        "name": "snapcast_status",
        "description": "Get all Snapcast audio groups, clients, volumes, and streams.",
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "snapcast_set_volume",
        "description": "Set the playback volume (0–100) for a Snapcast client by its ID.",
        "input_schema": {
            "type": "object",
            "properties": {
                "client_id": {"type": "string", "description": "Client ID from snapcast_status"},
                "volume": {"type": "integer", "description": "Volume level 0–100"},
            },
            "required": ["client_id", "volume"],
        },
    },
    {
        "name": "snapcast_mute",
        "description": "Mute or unmute a Snapcast client without changing its volume level.",
        "input_schema": {
            "type": "object",
            "properties": {
                "client_id": {"type": "string", "description": "Client ID from snapcast_status"},
                "muted": {"type": "boolean", "description": "true to mute, false to unmute"},
            },
            "required": ["client_id", "muted"],
        },
    },
    {
        "name": "snapcast_set_stream",
        "description": "Change which audio stream a Snapcast group plays.",
        "input_schema": {
            "type": "object",
            "properties": {
                "group_id": {"type": "string", "description": "Group ID from snapcast_status"},
                "stream_id": {"type": "string", "description": "Stream ID to switch to"},
            },
            "required": ["group_id", "stream_id"],
        },
    },
]

_SNAPCAST_TOOLS_OPENAI = anthropic_tools_to_openai(_SNAPCAST_TOOLS_ANTHROPIC)


def _get_snapcast_tools(provider: str) -> list:
    if not _snapcast_configured():
        return []
    return _SNAPCAST_TOOLS_ANTHROPIC if provider == "anthropic" else _SNAPCAST_TOOLS_OPENAI


async def _execute_snapcast_tool(name: str, args: dict) -> str:
    try:
        if name == "snapcast_status":
            return await _snapcast_get_status()

        if name == "snapcast_set_volume":
            await _snapcast_rpc("Client.SetVolume", {"id": args["client_id"], "volume": {"percent": int(args["volume"]), "muted": False}})
            return f"Volume for '{args['client_id']}' set to {args['volume']}%."

        if name == "snapcast_mute":
            # Fetch current volume so mute doesn't reset the level
            result = await _snapcast_rpc("Server.GetStatus")
            clients = [c for g in result.get("server", {}).get("groups", []) for c in g.get("clients", [])]
            match = next((c for c in clients if c["id"] == args["client_id"]), None)
            current_pct = match["config"]["volume"]["percent"] if match else 100
            await _snapcast_rpc("Client.SetVolume", {"id": args["client_id"], "volume": {"percent": current_pct, "muted": bool(args["muted"])}})
            return ("Muted" if args["muted"] else "Unmuted") + f" '{args['client_id']}'."

        if name == "snapcast_set_stream":
            await _snapcast_rpc("Group.SetStream", {"id": args["group_id"], "stream_id": args["stream_id"]})
            return f"Group '{args['group_id']}' now playing stream '{args['stream_id']}'."

        return f"Unknown Snapcast tool: {name}"
    except Exception as e:
        return f"Snapcast error: {e}"
