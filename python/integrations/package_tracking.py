import asyncio, re

from db import (
    _db_get_package_tracking_prefs,
    _db_insert_package_event,
    _db_list_package_events,
    _db_list_users_for_package_tracking,
    _db_load_config,
    _db_ready,
    _db_set_package_tracking_enabled,
    _db_uids_already_tracked,
)
from integrations.pim.mail import _email_configured, _imap_fetch_body, _imap_fetch_unread
from integrations.push import _send_push
from tool_schemas import anthropic_tools_to_openai

_sio = None
_sids_fn = None

_POLL_INTERVAL_SECONDS = 300
_FETCH_LIMIT = 20
_LIST_LIMIT = 20

# Sender-domain match only (not subject keywords) — keeps v1 tight and avoids false
# positives from ordinary promotional email that happens to mention "order"/"package".
_CARRIER_SENDER_PATTERNS = {
    "UPS": re.compile(r"@ups\.com", re.I),
    "FedEx": re.compile(r"@fedex\.com", re.I),
    "USPS": re.compile(r"@usps\.com", re.I),
    "Amazon": re.compile(r"@(?:[a-z0-9.-]+\.)?amazon\.[a-z.]+", re.I),
}

_STATUS_PATTERNS = [
    ("delivered", re.compile(r"\b(?:has been delivered|was delivered|delivered on|package delivered)\b", re.I)),
    ("out_for_delivery", re.compile(r"\bout for delivery\b", re.I)),
    ("shipped", re.compile(r"\b(?:has shipped|shipment has shipped|order has shipped|on (?:its|the) way)\b", re.I)),
]

_STATUS_LABELS = {
    "delivered": "delivered",
    "out_for_delivery": "out for delivery",
    "shipped": "shipped",
    "update": "updated",
}

# Best-effort per-carrier formats — tracking numbers vary enough (especially USPS) that
# this won't catch every case; a miss just means tracking_number comes back empty.
_TRACKING_NUMBER_PATTERNS = {
    "UPS": re.compile(r"\b1Z[0-9A-Z]{16}\b"),
    "FedEx": re.compile(r"\b\d{12}(?:\d{3})?\b"),
    "USPS": re.compile(r"\b(?:94|93|92|82|420\d{5}9)\d{15,20}\b"),
}


def init(sio, sids_fn) -> None:
    global _sio, _sids_fn
    _sio = sio
    _sids_fn = sids_fn


def _detect_carrier(sender: str) -> str | None:
    for carrier, pattern in _CARRIER_SENDER_PATTERNS.items():
        if pattern.search(sender):
            return carrier
    return None


def _detect_status(text: str) -> str:
    for status, pattern in _STATUS_PATTERNS:
        if pattern.search(text):
            return status
    return "update"


def _extract_tracking_number(carrier: str, text: str) -> str:
    pattern = _TRACKING_NUMBER_PATTERNS.get(carrier)
    if not pattern:
        return ""
    match = pattern.search(text)
    return match.group(0) if match else ""


# ─── Tool schema ────────────────────────────────────────────────────────────

_PACKAGE_TOOL_ANTHROPIC = {
    "name": "get_package_updates",
    "description": "Get recent package delivery updates detected from shipping emails (UPS, FedEx, USPS, Amazon).",
    "input_schema": {
        "type": "object",
        "properties": {
            "limit": {"type": "integer", "description": "Max updates to return (default 10, max 20)."},
        },
        "required": [],
    },
}

_PACKAGE_TOOL_OPENAI = anthropic_tools_to_openai([_PACKAGE_TOOL_ANTHROPIC])[0]

_PACKAGE_TOOL_NAMES = {"get_package_updates"}


def _get_package_tools(config: dict, provider: str) -> list:
    if not _email_configured(config):
        return []
    return [_PACKAGE_TOOL_ANTHROPIC] if provider == "anthropic" else [_PACKAGE_TOOL_OPENAI]


# ─── Execution ──────────────────────────────────────────────────────────────


async def _execute_package_tool(user_id: str, args: dict) -> str:
    limit = min(max(int(args.get("limit") or 10), 1), _LIST_LIMIT)
    rows = await _db_list_package_events(user_id, limit=limit)
    if not rows:
        return "No package updates."
    lines = []
    for r in rows:
        line = f"{r['carrier']}: {_STATUS_LABELS.get(r['status'], r['status'])}"
        if r["tracking_number"]:
            line += f" (tracking {r['tracking_number']})"
        lines.append(line)
    return "\n".join(lines)


async def _get_package_tracking_prefs(user_id: str) -> dict:
    prefs = await _db_get_package_tracking_prefs(user_id)
    events = await _db_list_package_events(user_id, limit=_LIST_LIMIT)
    return {"enabled": prefs["enabled"], "events": events}


async def _set_package_tracking_prefs(user_id: str, data: dict) -> dict:
    enabled = bool(data.get("enabled"))
    await _db_set_package_tracking_enabled(user_id, enabled)
    return {"ok": True, "enabled": enabled}


# ─── Background polling ─────────────────────────────────────────────────────


async def _alert_package_update(user_id: str, carrier: str, status: str, tracking_number: str) -> None:
    label = _STATUS_LABELS.get(status, status)
    speak = f"Package update: your {carrier} package is {label}."
    if _sio is not None and _sids_fn is not None:
        payload = {"carrier": carrier, "status": status, "tracking_number": tracking_number, "speak": speak}
        for sid in _sids_fn(user_id):
            await _sio.emit("package_alert", payload, to=sid)
    await _send_push(user_id, "Package update", speak)


async def _scan_for_package_updates(user_id: str, config: dict) -> None:
    try:
        messages = await _imap_fetch_unread(config, _FETCH_LIMIT)
    except Exception as e:
        print(f"[PACKAGE_TRACKING] Fetch failed for {user_id}: {e}", flush=True)
        return
    candidates = [m for m in messages if _detect_carrier(m.get("from", ""))]
    if not candidates:
        return
    already = await _db_uids_already_tracked(user_id, [m["uid"] for m in candidates])
    for message in candidates:
        if message["uid"] in already:
            continue
        carrier = _detect_carrier(message.get("from", ""))
        try:
            body = await _imap_fetch_body(config, message["uid"])
        except Exception as e:
            print(f"[PACKAGE_TRACKING] Body fetch failed for {user_id}: {e}", flush=True)
            body = ""
        haystack = f"{message.get('subject', '')}\n{body}"
        status = _detect_status(haystack)
        tracking_number = _extract_tracking_number(carrier, haystack)
        await _db_insert_package_event(user_id, message["uid"], carrier, status, tracking_number)
        if status in ("delivered", "out_for_delivery"):
            await _alert_package_update(user_id, carrier, status, tracking_number)


async def _package_tracking_loop() -> None:
    while True:
        await asyncio.sleep(_POLL_INTERVAL_SECONDS)
        if not _db_ready():
            continue
        try:
            for user_id in await _db_list_users_for_package_tracking():
                config = await _db_load_config(user_id)
                if not _email_configured(config):
                    continue
                await _scan_for_package_updates(user_id, config)
        except Exception as e:
            print(f"[PACKAGE_TRACKING] {e}", flush=True)
