from tool_schemas import anthropic_tools_to_openai

MYQ_TOOLS_ANTHROPIC = [
    {
        "name": "get_garage_status",
        "description": "Get the current open/closed state of your MyQ Chamberlain smart garage door(s).",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "set_garage_door",
        "description": "Open or close a MyQ Chamberlain smart garage door.",
        "input_schema": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["open", "close"],
                    "description": "Whether to open or close the door.",
                },
                "device": {
                    "type": "string",
                    "description": "Garage door name. Omit if you only have one.",
                },
            },
            "required": ["action"],
        },
    },
]

MYQ_TOOLS_OPENAI = anthropic_tools_to_openai(MYQ_TOOLS_ANTHROPIC)


def _myq_configured(config: dict) -> bool:
    return bool(config.get("myq_email") and config.get("myq_password"))


def _get_myq_tools(config: dict, provider: str) -> list:
    if not _myq_configured(config):
        return []
    return MYQ_TOOLS_ANTHROPIC if provider == "anthropic" else MYQ_TOOLS_OPENAI


async def _myq_get_status(config: dict) -> str:
    try:
        import aiohttp
        import pymyq

        async with aiohttp.ClientSession() as session:
            myq = await pymyq.login(config["myq_email"], config["myq_password"], session)
            if not myq.covers:
                return "No garage doors found in your MyQ account."
            lines = [f"{d.name}: {d.state}" for d in myq.covers.values()]
            return "\n".join(lines)
    except Exception as e:
        return f"Could not reach MyQ: {e}"


async def _myq_set_door(config: dict, device_name: str | None, action: str) -> str:
    try:
        import aiohttp
        import pymyq

        async with aiohttp.ClientSession() as session:
            myq = await pymyq.login(config["myq_email"], config["myq_password"], session)
            if not myq.covers:
                return "No garage doors found in your MyQ account."
            if device_name:
                device = next(
                    (d for d in myq.covers.values() if device_name.lower() in d.name.lower()),
                    None,
                )
                if device is None:
                    names = ", ".join(d.name for d in myq.covers.values())
                    return f"No garage door matching '{device_name}'. Available: {names}."
            else:
                device = next(iter(myq.covers.values()))
            if action == "open":
                await device.open(wait_for_state=None)
            else:
                await device.close(wait_for_state=None)
            return f"{device.name}: {action} command sent."
    except Exception as e:
        return f"Could not reach MyQ: {e}"
