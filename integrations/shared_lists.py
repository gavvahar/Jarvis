from db import _db_get_shared_list, _db_update_shared_list

_SHARED_LIST_TOOL_ANTHROPIC = {
    "name": "manage_shared_list",
    "description": "Manage shared household lists such as shopping or todo. Use to add, remove, read, or clear items.",
    "input_schema": {
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": ["add", "remove", "read", "clear"], "description": "Operation to perform"},
            "list_name": {"type": "string", "description": "Name of the list, e.g. shopping or todo"},
            "item": {"type": "string", "description": "Item to add or remove (omit for read/clear)"},
        },
        "required": ["action", "list_name"],
    },
}

_SHARED_LIST_TOOL_OPENAI = {
    "type": "function",
    "function": {
        "name": "manage_shared_list",
        "description": "Manage shared household lists such as shopping or todo.",
        "parameters": {
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": ["add", "remove", "read", "clear"]},
                "list_name": {"type": "string", "description": "Name of the list, e.g. shopping or todo"},
                "item": {"type": "string", "description": "Item to add or remove (omit for read/clear)"},
            },
            "required": ["action", "list_name"],
        },
    },
}


def _get_shared_list_tools(provider: str) -> list:
    return [_SHARED_LIST_TOOL_ANTHROPIC] if provider == "anthropic" else [_SHARED_LIST_TOOL_OPENAI]


async def _execute_shared_list_tool(args: dict) -> str:
    action = (args.get("action") or "").lower()
    list_name = (args.get("list_name") or "shopping").lower().strip()[:50]
    item = (args.get("item") or "").strip()[:200]
    items = await _db_get_shared_list(list_name)
    if action == "read":
        return f"{list_name.title()} list is empty." if not items else f"{list_name.title()}: " + ", ".join(items) + "."
    if action == "add":
        if not item:
            return "No item specified."
        if item.lower() not in [i.lower() for i in items]:
            items.append(item)
            await _db_update_shared_list(list_name, items)
        return f"Added '{item}' to {list_name}. {len(items)} item(s) now."
    if action == "remove":
        if not item:
            return "No item specified."
        new = [i for i in items if i.lower() != item.lower()]
        if len(new) == len(items):
            return f"'{item}' not found in {list_name}."
        await _db_update_shared_list(list_name, new)
        return f"Removed '{item}' from {list_name}."
    if action == "clear":
        await _db_update_shared_list(list_name, [])
        return f"{list_name.title()} list cleared."
    return f"Unknown action: {action}"
