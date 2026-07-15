from fastapi import HTTPException

from db import _db_get_sentry_mode, _db_set_sentry_mode
from tool_schemas import anthropic_tools_to_openai

_VALID_MODES = {"auto", "armed", "disarmed"}

_broadcast_fn = None


def init(broadcast_fn):
    global _broadcast_fn
    _broadcast_fn = broadcast_fn


SENTRY_TOOLS_ANTHROPIC = [
    {
        "name": "set_sentry_mode",
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
        "name": "get_sentry_mode",
        "description": "Get the current camera security Sentry Mode (armed, disarmed, or auto).",
        "input_schema": {"type": "object", "properties": {}},
    },
]

SENTRY_TOOLS_OPENAI = anthropic_tools_to_openai(SENTRY_TOOLS_ANTHROPIC)

_SENTRY_TOOL_NAMES = {t["name"] for t in SENTRY_TOOLS_ANTHROPIC}


def _get_sentry_tools(provider: str) -> list:
    return SENTRY_TOOLS_ANTHROPIC if provider == "anthropic" else SENTRY_TOOLS_OPENAI


async def _set_sentry_mode(mode: str, updated_by: str) -> dict:
    mode = (mode or "").strip().lower()
    if mode not in _VALID_MODES:
        raise HTTPException(400, f"mode must be one of {sorted(_VALID_MODES)}")
    await _db_set_sentry_mode(mode, updated_by)
    if _broadcast_fn is not None:
        await _broadcast_fn("sentry_mode_changed", {"mode": mode, "updated_by": updated_by})
    return {"ok": True, "mode": mode}


async def _get_sentry_mode() -> dict:
    return {"mode": await _db_get_sentry_mode()}


_SENTRY_MODE_SPEAK = {
    "armed": "Sentry Mode armed, sir. I'll flag any motion or unrecognized face.",
    "disarmed": "Sentry Mode disarmed. Security alerts are off.",
    "auto": "Sentry Mode set to automatic. I'll watch closely when everyone's away or it's night.",
}


async def _execute_sentry_tool(name: str, args: dict, user_id: str = "") -> str:
    try:
        if name == "set_sentry_mode":
            mode = (args.get("mode") or "").strip().lower()
            if mode not in _VALID_MODES:
                return f"mode must be one of {sorted(_VALID_MODES)}."
            await _set_sentry_mode(mode, user_id)
            return _SENTRY_MODE_SPEAK[mode]
        if name == "get_sentry_mode":
            mode = await _db_get_sentry_mode()
            return f"Sentry Mode is currently: {mode}."
        return f"Unknown sentry tool: {name}"
    except Exception as e:
        return f"Error: {e}"
