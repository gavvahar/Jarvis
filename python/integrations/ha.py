import httpx

from tool_schemas import anthropic_tools_to_openai

HA_TOOLS_ANTHROPIC = [
    {
        "name": "get_ha_states",
        "description": (
            "Get the current state of Home Assistant devices. "
            "Optionally filter by domain (e.g. 'light', 'switch', 'climate', "
            "'sensor', 'automation', 'script'). Omit domain to get all entities."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "domain": {
                    "type": "string",
                    "description": "Optional domain filter, e.g. 'light' or 'switch'.",
                }
            },
        },
    },
    {
        "name": "call_ha_service",
        "description": ("Call a Home Assistant service to control a device, run a script, or trigger an automation."),
        "input_schema": {
            "type": "object",
            "properties": {
                "domain": {
                    "type": "string",
                    "description": "Service domain, e.g. 'light', 'switch', 'climate', 'automation', 'script'.",
                },
                "service": {
                    "type": "string",
                    "description": "Service name, e.g. 'turn_on', 'turn_off', 'toggle', 'trigger'.",
                },
                "entity_id": {
                    "type": "string",
                    "description": "Entity ID to act on, e.g. 'light.living_room'. Omit for scripts/automations.",
                },
                "service_data": {
                    "type": "object",
                    "description": 'Optional extra data, e.g. {"brightness_pct": 50} for lights.',
                },
            },
            "required": ["domain", "service"],
        },
    },
    {
        "name": "get_doorbell_events",
        "description": (
            "Get recent doorbell and motion events from the front door. "
            "Use this to answer questions about who came to the door, recent motion, "
            "deliveries, or 'any activity while I was out?'"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "hours": {
                    "type": "number",
                    "description": "How many hours back to look (default 24).",
                }
            },
        },
    },
]

HA_TOOLS_OPENAI = anthropic_tools_to_openai(HA_TOOLS_ANTHROPIC)


def _ha_configured(config: dict) -> bool:
    return bool(config.get("ha_url") and config.get("ha_token"))


def _ha_headers(config: dict) -> dict:
    return {
        "Authorization": f"Bearer {config['ha_token']}",
        "Content-Type": "application/json",
    }


def _get_ha_tools(config: dict, provider: str) -> list:
    if not _ha_configured(config):
        return []
    return HA_TOOLS_ANTHROPIC if provider == "anthropic" else HA_TOOLS_OPENAI


async def _validate_ha(url, token):
    try:
        async with httpx.AsyncClient(timeout=5) as c:
            r = await c.get(
                url.rstrip("/") + "/api/",
                headers={"Authorization": f"Bearer {token}"},
            )
        if r.status_code == 200:
            return True, ""
        if r.status_code == 401:
            return False, "Home Assistant token was rejected."
        return False, f"Home Assistant returned HTTP {r.status_code}."
    except Exception as e:
        return False, f"Could not reach Home Assistant: {e}"


async def _ha_get_entity_state(config: dict, entity_id: str) -> str | None:
    url = config["ha_url"].rstrip("/") + f"/api/states/{entity_id}"
    try:
        async with httpx.AsyncClient(timeout=5) as c:
            r = await c.get(url, headers=_ha_headers(config))
        if r.status_code == 200:
            return r.json().get("state")
        return None
    except Exception:
        return None


async def _ha_get_states(config: dict, domain=None):
    url = config["ha_url"].rstrip("/") + "/api/states"
    async with httpx.AsyncClient(timeout=8) as c:
        r = await c.get(url, headers=_ha_headers(config))
    r.raise_for_status()
    states = r.json()
    if domain:
        states = [s for s in states if s["entity_id"].startswith(domain + ".")]
    lines = []
    for s in states[:60]:
        name = s.get("attributes", {}).get("friendly_name", "")
        line = f"{s['entity_id']}: {s['state']}"
        if name:
            line += f" ({name})"
        lines.append(line)
    return "\n".join(lines) if lines else "No entities found."


async def _ha_call_service(config: dict, domain, service, entity_id=None, service_data=None):
    url = config["ha_url"].rstrip("/") + f"/api/services/{domain}/{service}"
    payload = dict(service_data or {})
    if entity_id:
        payload["entity_id"] = entity_id
    async with httpx.AsyncClient(timeout=8) as c:
        r = await c.post(url, headers=_ha_headers(config), json=payload)
    return "Done." if r.status_code in (200, 201) else f"HA returned {r.status_code}: {r.text[:120]}"
