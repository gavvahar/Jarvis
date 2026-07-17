from fastapi import HTTPException

from db import _db_get_vigil_mode, _db_set_vigil_mode
from tool_schemas import anthropic_tools_to_openai

_VALID_MODES = {"auto", "armed", "disarmed"}

_broadcast_fn = None


def init(broadcast_fn):
    global _broadcast_fn
    _broadcast_fn = broadcast_fn


VIGIL_TOOLS_ANTHROPIC = [
    {
        "name": "set_vigil_mode",
        "description": (
            "Arm, disarm, or set camera security monitoring back to automatic. 'armed' means always-heightened "
            "monitoring (motion and unrecognized faces trigger alerts) regardless of presence or time of day. "
            "'disarmed' turns off security alerts entirely. 'auto' (the default) heightens monitoring automatically "
            "when everyone is away or it's nighttime."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"mode": {"type": "string", "enum": ["armed", "disarmed", "auto"]}},
            "required": ["mode"],
        },
    },
    {
        "name": "get_vigil_mode",
        "description": "Get the current camera security Vigil Mode (armed, disarmed, or auto).",
        "input_schema": {"type": "object", "properties": {}},
    },
]

VIGIL_TOOLS_OPENAI = anthropic_tools_to_openai(VIGIL_TOOLS_ANTHROPIC)

_VIGIL_TOOL_NAMES = {t["name"] for t in VIGIL_TOOLS_ANTHROPIC}


def _get_vigil_tools(provider: str) -> list:
    return VIGIL_TOOLS_ANTHROPIC if provider == "anthropic" else VIGIL_TOOLS_OPENAI


async def _set_vigil_mode(mode: str, updated_by: str) -> dict:
    mode = (mode or "").strip().lower()
    if mode not in _VALID_MODES:
        raise HTTPException(400, f"mode must be one of {sorted(_VALID_MODES)}")
    await _db_set_vigil_mode(mode, updated_by)
    if _broadcast_fn is not None:
        await _broadcast_fn("vigil_mode_changed", {"mode": mode, "updated_by": updated_by})
    return {"ok": True, "mode": mode}


async def _get_vigil_mode() -> dict:
    return {"mode": await _db_get_vigil_mode()}


_VIGIL_MODE_SPEAK = {
    "armed": "Vigil Mode armed, sir. I'll flag any motion or unrecognized face.",
    "disarmed": "Vigil Mode disarmed. Security alerts are off.",
    "auto": "Vigil Mode set to automatic. I'll watch closely when everyone's away or it's night.",
}


async def _execute_vigil_tool(name: str, args: dict, user_id: str = "") -> str:
    try:
        if name == "set_vigil_mode":
            mode = (args.get("mode") or "").strip().lower()
            if mode not in _VALID_MODES:
                return f"mode must be one of {sorted(_VALID_MODES)}."
            await _set_vigil_mode(mode, user_id)
            return _VIGIL_MODE_SPEAK[mode]
        if name == "get_vigil_mode":
            mode = await _db_get_vigil_mode()
            return f"Vigil Mode is currently: {mode}."
        return f"Unknown vigil tool: {name}"
    except Exception as e:
        return f"Error: {e}"
