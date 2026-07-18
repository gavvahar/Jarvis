import asyncio, json

from config import DEFAULT_MODELS, VALID_PROVIDERS
from db import (
    _db_get_email_triage_prefs,
    _db_insert_email_triage,
    _db_list_email_triage,
    _db_list_users_for_email_triage,
    _db_load_config,
    _db_ready,
    _db_set_email_triage_enabled,
    _db_uids_already_classified,
)
from integrations.pim.mail import _email_configured, _imap_fetch_unread
from integrations.push import _send_push
from llm_client import build_llm_client
from tool_schemas import anthropic_tools_to_openai

_sio = None
_sids_fn = None

_POLL_INTERVAL_SECONDS = 300
_FETCH_LIMIT = 20
_LIST_LIMIT = 20


def init(sio, sids_fn) -> None:
    global _sio, _sids_fn
    _sio = sio
    _sids_fn = sids_fn

_CLASSIFY_INSTRUCTIONS = (
    "Classify this email. Respond with strict JSON only, no other text: "
    '{"summary": "<one short sentence, under 20 words>", "important": true or false}. '
    "Mark important=true only for things needing timely attention (bills, deadlines, "
    "security alerts, personal messages needing a reply) — not newsletters, marketing, "
    "or automated notices."
)


def _email_triage_configured(config: dict) -> bool:
    return _email_configured(config)


async def _classify_email(config: dict, message: dict) -> dict:
    fallback = {"summary": (message.get("subject") or "(no subject)")[:140], "important": False}
    provider = config.get("provider", "anthropic")
    if provider not in VALID_PROVIDERS:
        provider = "anthropic"
    client = build_llm_client(provider, config.get("api_key", ""), config.get("base_url", ""), is_async=True)
    if client is None:
        return fallback
    model = config.get("model") or DEFAULT_MODELS.get(provider, "")
    prompt = f"{_CLASSIFY_INSTRUCTIONS}\n\nFrom: {message.get('from', '')}\nSubject: {message.get('subject', '')}\n"
    try:
        if provider == "anthropic":
            msg = await client.messages.create(model=model, max_tokens=150, messages=[{"role": "user", "content": prompt}])
            raw = msg.content[0].text
        else:
            resp = await client.chat.completions.create(model=model, messages=[{"role": "user", "content": prompt}], stream=False)
            raw = resp.choices[0].message.content
        raw = (raw or "").strip()
        if raw.startswith("```"):
            raw = raw.strip("`")
            if raw.lower().startswith("json"):
                raw = raw[4:]
            raw = raw.strip()
        parsed = json.loads(raw)
        summary = str(parsed.get("summary") or fallback["summary"])[:200]
        important = bool(parsed.get("important"))
        return {"summary": summary, "important": important}
    except Exception as e:
        print(f"[EMAIL_TRIAGE] Classification failed: {e}", flush=True)
        return fallback


# ─── Tool schema ────────────────────────────────────────────────────────────

_EMAIL_TRIAGE_TOOL_ANTHROPIC = {
    "name": "get_email_summary",
    "description": "Get one-line summaries of recently triaged unread email, optionally filtered to just the important/urgent ones.",
    "input_schema": {
        "type": "object",
        "properties": {
            "important_only": {"type": "boolean", "description": "If true, only return messages flagged important."},
            "limit": {"type": "integer", "description": "Max messages to return (default 10, max 20)."},
        },
        "required": [],
    },
}

_EMAIL_TRIAGE_TOOL_OPENAI = anthropic_tools_to_openai([_EMAIL_TRIAGE_TOOL_ANTHROPIC])[0]

_EMAIL_TRIAGE_TOOL_NAMES = {"get_email_summary"}


def _get_email_triage_tools(config: dict, provider: str) -> list:
    if not _email_triage_configured(config):
        return []
    return [_EMAIL_TRIAGE_TOOL_ANTHROPIC] if provider == "anthropic" else [_EMAIL_TRIAGE_TOOL_OPENAI]


# ─── Execution ──────────────────────────────────────────────────────────────


async def _execute_email_triage_tool(user_id: str, args: dict) -> str:
    limit = min(max(int(args.get("limit") or 10), 1), _LIST_LIMIT)
    rows = await _db_list_email_triage(user_id, limit=limit)
    if args.get("important_only"):
        rows = [r for r in rows if r["important"]]
    if not rows:
        return "No urgent email." if args.get("important_only") else "No triaged email yet."
    return "\n".join(f"{'⚠ ' if r['important'] else ''}{r['sender']} — {r['summary']}" for r in rows)


async def _get_email_triage_prefs(user_id: str) -> dict:
    prefs = await _db_get_email_triage_prefs(user_id)
    messages = await _db_list_email_triage(user_id, limit=_LIST_LIMIT)
    return {"enabled": prefs["enabled"], "messages": messages}


async def _set_email_triage_prefs(user_id: str, data: dict) -> dict:
    enabled = bool(data.get("enabled"))
    await _db_set_email_triage_enabled(user_id, enabled)
    return {"ok": True, "enabled": enabled}


# ─── Background polling ─────────────────────────────────────────────────────


async def _alert_urgent_email(user_id: str, message: dict, result: dict) -> None:
    sender = message.get("from") or "someone"
    speak = f"Urgent email from {sender}: {result['summary']}"
    if _sio is not None and _sids_fn is not None:
        payload = {
            "from": sender,
            "subject": message.get("subject", ""),
            "summary": result["summary"],
            "speak": speak,
        }
        for sid in _sids_fn(user_id):
            await _sio.emit("email_alert", payload, to=sid)
    await _send_push(user_id, "Urgent email", result["summary"][:180])


async def _triage_new_messages(user_id: str, config: dict) -> None:
    try:
        messages = await _imap_fetch_unread(config, _FETCH_LIMIT)
    except Exception as e:
        print(f"[EMAIL_TRIAGE] Fetch failed for {user_id}: {e}", flush=True)
        return
    if not messages:
        return
    already = await _db_uids_already_classified(user_id, [m["uid"] for m in messages])
    for message in messages:
        if message["uid"] in already:
            continue
        result = await _classify_email(config, message)
        await _db_insert_email_triage(user_id, message["uid"], message.get("from", ""), message.get("subject", ""), result["summary"], result["important"])
        if result["important"]:
            await _alert_urgent_email(user_id, message, result)


async def _email_triage_loop() -> None:
    while True:
        await asyncio.sleep(_POLL_INTERVAL_SECONDS)
        if not _db_ready():
            continue
        try:
            for user_id in await _db_list_users_for_email_triage():
                config = await _db_load_config(user_id)
                if not _email_triage_configured(config):
                    continue
                await _triage_new_messages(user_id, config)
        except Exception as e:
            print(f"[EMAIL_TRIAGE] {e}", flush=True)
